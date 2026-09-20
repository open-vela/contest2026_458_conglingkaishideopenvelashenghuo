/****************************************************************************
 * app/wristclaw/wc_agent.h
 *
 * WristClaw 板端 Agent：意图路由 / 工具执行 / 主动引擎 / 记忆 / Skill
 ****************************************************************************/

#ifndef __APP_WRISTCLAW_WC_AGENT_H
#define __APP_WRISTCLAW_WC_AGENT_H

#include <stdint.h>
#include <stdbool.h>
#include <time.h>

/* 初始化：datadir 存提醒/备忘/记忆，skilldir 放 Markdown Skill */

void wc_agent_init(const char *datadir, const char *skilldir);

/* 周期调用（主循环 ~20ms）。uptime_ms 供主动引擎判超时/计时。 */

void wc_agent_tick(uint32_t uptime_ms);

/* 链路刚建立 / 重新建立时调用：把设备状态与能力上报给上位机 */

void wc_agent_on_link_up(void);

/* 本地时间：本板没有 TZ 环境变量支持（openvela/NuttX 侧未启用），
 * 因此用显式时区偏移把 UTC 秒换算成本地时间。偏移由上位机的
 * TIME 命令下发，默认东八区 (UTC+8)。 */

struct tm *wc_agent_localtime(time_t utc, struct tm *out);

/* 系统时间是否已被上位机校准（未校时 UI 不显示假的 1970 年） */

bool wc_agent_time_synced(void);

/* 处理上位机回包（极简行协议：SAY / REMIND / MEMO / BRIGHT / SKILL） */

void wc_agent_handle_cloud_reply(const char *line);

/* 处理设备命令（数值与 protocol.py 的 DeviceCmd 一致） */

void wc_agent_handle_device_cmd(uint8_t cmd, uint8_t arg);

/* 注入一条用户输入（按键/串口/上位机转来的文本），走完整的意图路由 */

void wc_agent_inject_utterance(const char *text);

/* UI 快照（wristclaw_main 主循环 1Hz 调用） */

struct wc_agent_stat_s
{
  bool link;
  int  nrem;
  int  nmemo;
  int  nskill;
  int  bright;
};

void wc_agent_stat(struct wc_agent_stat_s *s);

/* 待触发提醒列表；out 行格式 "<剩余秒>s 文本"；返回条数（<= max） */

int wc_agent_pending(char out[][48], int max);

/* 备忘列表（供 UI 第 3 页） */

int wc_agent_memo_list(char out[][48], int max);

/* 供 UI 读取的当前状态快照 */

const char *wc_agent_status_line(void);

#endif /* __APP_WRISTCLAW_WC_AGENT_H */
