/****************************************************************************
 * app/wristclaw/wristclaw_main.c
 *
 * 腕灵犀 WristClaw —— 端云协同的腕上 AI 伙伴（板端 Agent）
 *
 * 职责划分（对应赛道加分项「端云/端端协作」）：
 *   端侧（本文件）：唤醒/输入编排、意图路由的本地兜底、工具执行、
 *                   主动任务、记忆持久化、Skill 加载、UI 呈现。
 *   云侧（PC 上位机 host/）：意图理解、自然语言→结构化、摘要生成，
 *                   后端接 Xiaomi MiMo，失败时回落本地 Mock。
 *
 * 设计取舍见 ../docs/PLAN.md，其中三条最要紧：
 *   1. 对 USB 口永不无限阻塞（主机停读时阻塞写会把设备写死）。
 *   2. 意图路由两级兜底：本地规则先判，判不了才上行云端 —— 断网可用。
 *   3. 未实现的硬件能力（唤醒词/TTS/BLE）不假装，如实上报状态。
 ****************************************************************************/

#include <nuttx/config.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <time.h>
#include <syslog.h>
#include <sys/stat.h>
#include <sys/types.h>

#include <nuttx/clock.h>

#include "wc_proto.h"
#include "wc_agent.h"
#ifdef CONFIG_GRAPHICS_LVGL
#  include "wc_ui.h"
#endif

/****************************************************************************
 * 配置
 ****************************************************************************/

#define WC_LINK_PATH      "/dev/ttyACM0"
#define WC_DATA_DIR       "/data/wristclaw"
#define WC_SKILL_DIR      "/data/agent/skills"
#define WC_POLL_MS        20
#define WC_LINK_RETRY_MS  2000


/****************************************************************************
 * 全局状态
 ****************************************************************************/

static bool     g_link_up;
static uint32_t g_last_link_try;
static uint32_t g_last_heartbeat;
static uint32_t g_boot_tick;

static uint32_t now_ms(void)
{
  return (uint32_t)(clock_systime_ticks() * 1000 / TICK_PER_SEC);
}

/* 把一行文本作为 TEXT 帧发给上位机（同时打印到控制台便于调试） */

static void wc_say(const char *fmt, ...)
{
  char    buf[256];
  va_list ap;

  va_start(ap, fmt);
  vsnprintf(buf, sizeof(buf), fmt, ap);
  va_end(ap);

  syslog(LOG_INFO, "WristClaw: %s\n", buf);

#ifdef CONFIG_GRAPHICS_LVGL
  wc_ui_say(buf);
#endif

  if (g_link_up)
    {
      (void)wc_link_send_text(wc_next_seq(), buf);
    }
}

/****************************************************************************
 * 收到上位机帧
 ****************************************************************************/

static void on_frame(const struct wc_frame_s *f, void *arg)
{
  char line[WC_MAX_PAYLOAD + 1];

  switch (f->type)
    {
      case WC_T_CLOUD_RESP:
        {
          /* 上位机回包使用极简行协议，避免板端引入 JSON 依赖：
           *   SAY <text>            显示/播报
           *   REMIND <hh:mm> <text> 建提醒
           *   MEMO <text>           存备忘
           *   BRIGHT <n>            调亮度
           */

          memcpy(line, f->payload, f->len);
          line[f->len] = '\0';
          wc_agent_handle_cloud_reply(line);
        }
        break;

      case WC_T_DEVICE_CMD:
        {
          uint8_t cmd  = f->len > 0 ? f->payload[0] : 0;
          uint8_t argv = f->len > 1 ? f->payload[1] : 0;
          wc_agent_handle_device_cmd(cmd, argv);
        }
        break;

      case WC_T_AUDIO_CMD:
      case WC_T_AUDIO_DATA:
        /* 音频通路未在本期实现（见 docs/PLAN.md 的取舍表），
         * 明确回一条状态而不是静默丢弃，便于上位机感知。
         */

        wc_say("AUDIO: 本期未实现音频通路（无 NuttX audio 驱动）");
        break;

      case WC_T_HEARTBEAT:
        if (g_link_up)
          {
            (void)wc_link_send(WC_T_HEARTBEAT, f->seq, NULL, 0);
          }
        break;

      case WC_T_TEXT:
        {
          /* 上位机下发文本。两种语义：
           *   "USER <text>"  -- 模拟语音输入，走意图路由（本地优先，云端兜底）
           *   其他           -- 行协议命令（SAY/REMIND/...，见 wc_agent.c）
           * 板端的 say() 输出也用 TEXT 帧回传上位机，因此上位机不得把
           * 收到的 TEXT 再当输入喂回云端，否则会形成回声循环。
           */

          memcpy(line, f->payload, f->len);
          line[f->len] = '\0';

          if (strncmp(line, "USER ", 5) == 0)
            {
#ifdef CONFIG_GRAPHICS_LVGL
              wc_ui_user(line + 5);        /* 用户话术 → 右侧气泡 */
#endif
              wc_agent_inject_utterance(line + 5);
            }
          else
            {
              wc_agent_handle_cloud_reply(line);
            }
        }
        break;

      default:
        break;
    }
}

/****************************************************************************
 * 链路维护
 ****************************************************************************/

static void link_tick(void)
{
  static int  g_open_fail;
  uint32_t t = now_ms();

  if (!g_link_up)
    {
      int ret;

      if (t - g_last_link_try < WC_LINK_RETRY_MS)
        {
          return;
        }

      g_last_link_try = t;
      ret = wc_link_open(WC_LINK_PATH);
      if (ret == 0)
        {
          g_open_fail = 0;
          g_link_up = true;
          syslog(LOG_INFO, "WristClaw: link up on %s\n", WC_LINK_PATH);
          wc_agent_on_link_up();
        }
      else
        {
          /* ENOTCONN = 主机还没完成枚举/配置（cdcacm disconnected）。
           * 静默重试即可；不要在用户态动 SOFTCONN —— 与驱动的
           * 电源状态机竞争只会更糟。 */

          g_open_fail++;
        }
      return;
    }

      /* 心跳：让上位机知道板子活着。仅在"真错误"时拆链路；
       * -2（忙丢帧，主机暂时没读）不拆 —— 拆了会在主机开/关窗口期
       * 里疯狂重绑，反而饿死主机侧的打开操作。 */

      if (t - g_last_heartbeat >= 3000)
        {
          int ret;

          g_last_heartbeat = t;

          ret = wc_link_send(WC_T_HEARTBEAT, wc_next_seq(), NULL, 0);
          if (ret == -1)
            {
              syslog(LOG_WARNING, "WristClaw: link lost, will reopen\n");
              wc_link_close();
              g_link_up  = false;
              g_last_link_try = t;
            }
        }
}

/****************************************************************************
 * main
 ****************************************************************************/

int main(int argc, char *argv[])
{
  syslog(LOG_INFO, "WristClaw: booting (build %s %s)\n", __DATE__, __TIME__);

  /* 数据目录：持久化提醒/备忘/记忆/Skill。挂载点不可写时降级为内存态。 */

  (void)mkdir("/data", 0777);
  (void)mkdir(WC_DATA_DIR, 0777);
  (void)mkdir("/data/agent", 0777);
  (void)mkdir(WC_SKILL_DIR, 0777);

  wc_agent_init(WC_DATA_DIR, WC_SKILL_DIR);

#ifdef CONFIG_GRAPHICS_LVGL
  /* 屏幕 UI（失败时内部降级为无头，Agent 照常跑） */

  wc_ui_init();
#endif

  g_boot_tick = now_ms();

  /* 主循环：链路 + 主动引擎 + 工作队列 + UI，全部在同一线程，避免锁竞争 */

  for (;;)
    {
      uint32_t      now = now_ms() - g_boot_tick;
      static uint32_t last_ui_feed;

      link_tick();

      if (g_link_up)
        {
          wc_link_poll(on_frame, NULL);
        }

      wc_agent_tick(now);

#ifdef CONFIG_GRAPHICS_LVGL
      /* 1Hz 喂状态快照；每循环驱动 LVGL 定时器 */

      if (now - last_ui_feed >= 1000)
        {
          struct wc_agent_stat_s st;
          struct wc_ui_info_s    ui;
          char                   items[8][48];
          int                    n;

          wc_agent_stat(&st);
          ui.link   = st.link;
          ui.nrem   = st.nrem;
          ui.nmemo  = st.nmemo;
          ui.nskill = st.nskill;
          ui.bright = st.bright;
          wc_ui_status(&ui);

          n = wc_agent_pending(items, 8);
          wc_ui_reminders(items, n);

          n = wc_agent_memo_list(items, 5);
          wc_ui_memos(items, n);

          last_ui_feed = now;
        }

      wc_ui_tick();
#endif

      usleep(WC_POLL_MS * 1000);
    }

  return 0;
}
