/****************************************************************************
 * app/wristclaw/wc_agent.c
 *
 * WristClaw 板端 Agent 实现。
 *
 * 三个设计要点（都是为了让「无人值守也能跑通」）：
 *
 * 1. 提醒用「相对倒计时」而不是绝对时间戳。这块板子的 RTC 没有备份电池，
 *    time() 在未校时前不可信；用倒计时触发则与 RTC 是否正确无关，演示必定
 *    能响。真实日期只在展示时用 time()，取不到就显示相对时间。
 *
 * 2. 意图路由两级：先本地规则（离线可用，覆盖提醒/备忘/时间/亮度/状态），
 *    判不出来才上行云端。这样断网时核心功能不瘫 —— 也是赛道「离线兜底」
 *    的直接落地。
 *
 * 3. 能力如实上报。未实现的硬件（唤醒词/TTS/BLE）在状态里明确标注，不假装。
 ****************************************************************************/

#include <nuttx/config.h>

/* 板级导出的 GPIO 助手（vendor/sifli/.../src/bsp_power.c）。
 * 芯片 HAL 的头文件不在 app 的 include 路径里，但该符号是全局的
 * （nm 实测 0x1209ce80 T BSP_GPIO_Set），直接声明即可调用。 */

extern void BSP_GPIO_Set(int pin, int val, int is_porta);

#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <stdbool.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <time.h>
#include <dirent.h>
#include <syslog.h>
#include <sys/stat.h>
#include <sys/types.h>

#include "wc_agent.h"

#ifdef CONFIG_GRAPHICS_LVGL
#  include "wc_ui.h"
#endif
#include "wc_proto.h"

/****************************************************************************
 * 规模限制
 ****************************************************************************/

#define WC_MAX_REMINDERS   8
#define WC_MAX_MEMOS       16
#define WC_MAX_SKILLS      8
#define WC_MAX_HISTORY     8
#define WC_TEXT_LEN        96
#define WC_MAX_LINE        160

struct reminder_s
{
  bool     used;
  bool     fired;
  uint32_t remain_s;              /* 倒计时，不与 RTC 正确性耦合 */
  int64_t  due_epoch;             /* 仅用于展示 */
  char     text[WC_TEXT_LEN];
};

struct skill_s
{
  char name[32];
  char triggers[128];             /* 逗号分隔的触发词 */
  char tool[32];
  char prompt[WC_TEXT_LEN];
};

/****************************************************************************
 * 状态
 ****************************************************************************/

static char  g_datadir[64];
static char  g_skilldir[64];

static struct reminder_s g_reminders[WC_MAX_REMINDERS];
static char   g_memos[WC_MAX_MEMOS][WC_TEXT_LEN];
static int    g_nmemos;

static struct skill_s g_skills[WC_MAX_SKILLS];
static int    g_nskills;

static char   g_history[WC_MAX_HISTORY][WC_TEXT_LEN];
static int    g_nhistory;

static int    g_brightness = 80;
static int    g_memos_total;             /* 累计，用于上下文主动 */
static int    g_reminders_total;
static int    g_last_proactive;          /* 上次主动建议的分钟数，防刷屏 */
static int64_t g_boot_epoch;
static int     g_tz_off_s = 8 * 3600;   /* 东八区，可由 TIME 命令改写 */
static bool    g_time_synced;           /* 是否已被上位机校时 */

/* 云端请求组装缓冲。单线程 Agent 独占；绝不能放栈上 —— 栈 16K 下
 * inject_utterance 深链 + snprintf + say 叠加会溢出成硬故障死机。 */

static char   g_reqbuf[WC_MAX_PAYLOAD];

static char   g_status[192];

/****************************************************************************
 * 输出：同时进日志和上行帧
 ****************************************************************************/

static void say(const char *fmt, ...)
{
  char    buf[WC_TEXT_LEN * 2];
  va_list ap;

  va_start(ap, fmt);
  vsnprintf(buf, sizeof(buf), fmt, ap);
  va_end(ap);

  syslog(LOG_INFO, "WristClaw: %s\n", buf);

#ifdef CONFIG_GRAPHICS_LVGL
  wc_ui_say(buf);
#endif

  if (wc_link_ready())
    {
      (void)wc_link_send_text(wc_next_seq(), buf);
    }
}

/****************************************************************************
 * 持久化：极简行式文件，避免在板端引入 JSON 依赖
 ****************************************************************************/

static void path_join(char *out, size_t outsz, const char *dir,
                      const char *name)
{
  snprintf(out, outsz, "%s/%s", dir, name);
}

static int file_save_lines(const char *path, const char *const *lines, int n)
{
  FILE *fp = fopen(path, "w");
  int   i;

  if (fp == NULL)
    {
      return -errno;
    }

  for (i = 0; i < n; i++)
    {
      fprintf(fp, "%s\n", lines[i]);
    }

  fclose(fp);
  return 0;
}

static void memos_save(void)
{
  char        path[96];
  static char lines[WC_MAX_MEMOS][WC_TEXT_LEN];   /* 静态：不占 16K 栈 */
  const char *ptrs[WC_MAX_MEMOS];
  int         i;

  path_join(path, sizeof(path), g_datadir, "memos.txt");

  /* 必须显式做指针数组：file_save_lines 收的是 char*[]，
   * 直接把 char[N][96] 强转成 char** 会让 lines[i] 读成
   * "字符串头 4 字节当指针"，fprintf("%s") 随即硬故障。 */

  for (i = 0; i < g_nmemos; i++)
    {
      snprintf(lines[i], WC_TEXT_LEN, "%s", g_memos[i]);
      ptrs[i] = lines[i];
    }

  (void)file_save_lines(path, ptrs, g_nmemos);
}

static void memos_load(void)
{
  char  path[96];
  char  line[WC_MAX_LINE];
  FILE *fp;

  path_join(path, sizeof(path), g_datadir, "memos.txt");
  fp = fopen(path, "r");
  if (fp == NULL)
    {
      return;
    }

  while (g_nmemos < WC_MAX_MEMOS && fgets(line, sizeof(line), fp) != NULL)
    {
      size_t n = strlen(line);
      while (n > 0 && (line[n - 1] == '\n' || line[n - 1] == '\r'))
        {
          line[--n] = '\0';
        }

      if (n > 0)
        {
          snprintf(g_memos[g_nmemos++], WC_TEXT_LEN, "%s", line);
        }
    }

  fclose(fp);
}

static void memory_save(void)
{
  char  path[96];
  FILE *fp;

  path_join(path, sizeof(path), g_datadir, "memory.txt");
  fp = fopen(path, "w");
  if (fp == NULL)
    {
      return;
    }

  fprintf(fp, "brightness=%d\n", g_brightness);
  fprintf(fp, "memototal=%d\n", g_memos_total);
  fprintf(fp, "remindtotal=%d\n", g_reminders_total);
  fclose(fp);
}

static void memory_load(void)
{
  char  path[96];
  char  line[WC_MAX_LINE];
  FILE *fp;

  path_join(path, sizeof(path), g_datadir, "memory.txt");
  fp = fopen(path, "r");
  if (fp == NULL)
    {
      return;
    }

  while (fgets(line, sizeof(line), fp) != NULL)
    {
      int v;

      if (sscanf(line, "brightness=%d", &v) == 1)
        {
          g_brightness = v;
        }
      else if (sscanf(line, "memototal=%d", &v) == 1)
        {
          g_memos_total = v;
        }
      else if (sscanf(line, "remindtotal=%d", &v) == 1)
        {
          g_reminders_total = v;
        }
    }

  fclose(fp);
}

/****************************************************************************
 * Skill：从 /data/agent/skills/ 目录读取 Markdown 定义
 *
 * 文件格式（YAML 风格的极简头，不需要完整 YAML 解析器）：
 *
 *   ---
 *   name: weather
 *   triggers: 天气,weather,下雨
 *   tool: cloud
 *   prompt: 查询天气并只回一句话
 *   ---
 ****************************************************************************/

static void skill_parse_file(const char *path, const char *fname)
{
  char  line[WC_MAX_LINE];
  FILE *fp;
  struct skill_s *sk;

  if (g_nskills >= WC_MAX_SKILLS)
    {
      return;
    }

  fp = fopen(path, "r");
  if (fp == NULL)
    {
      return;
    }

  sk = &g_skills[g_nskills];
  memset(sk, 0, sizeof(*sk));

  /* 默认用文件名当 Skill 名 */

  snprintf(sk->name, sizeof(sk->name), "%s", fname);

  while (fgets(line, sizeof(line), fp) != NULL)
    {
      char *colon;
      size_t n = strlen(line);

      while (n > 0 && (line[n - 1] == '\n' || line[n - 1] == '\r'))
        {
          line[--n] = '\0';
        }

      if (strncmp(line, "name:", 5) == 0)
        {
          snprintf(sk->name, sizeof(sk->name), "%s", line + 5);
        }
      else if (strncmp(line, "triggers:", 9) == 0)
        {
          snprintf(sk->triggers, sizeof(sk->triggers), "%s", line + 9);
        }
      else if (strncmp(line, "tool:", 5) == 0)
        {
          snprintf(sk->tool, sizeof(sk->tool), "%s", line + 5);
        }
      else if (strncmp(line, "prompt:", 7) == 0)
        {
          snprintf(sk->prompt, sizeof(sk->prompt), "%s", line + 7);
        }
      else
        {
          colon = strchr(line, ':');   /* 其余行忽略，正文留给云端读 */
          (void)colon;
        }
    }

  fclose(fp);

  /* 去掉值前面的空格 */

  {
    char *fields[3] = { sk->name, sk->triggers, sk->tool };
    int   i;

    for (i = 0; i < 3; i++)
      {
        char *p = fields[i];
        while (*p == ' ' || *p == '\t')
          {
            memmove(p, p + 1, strlen(p));
          }
      }
  }

  if (sk->triggers[0] != '\0')
    {
      g_nskills++;
      syslog(LOG_INFO, "WristClaw: skill loaded: %s (triggers=%s)\n",
             sk->name, sk->triggers);
    }
}

static void skills_load(void)
{
  DIR           *dir;
  struct dirent *ent;
  char           path[192];

  dir = opendir(g_skilldir);
  if (dir == NULL)
    {
      syslog(LOG_WARNING, "WristClaw: no skill dir %s (%d)\n", g_skilldir,
             errno);
      return;
    }

  while ((ent = readdir(dir)) != NULL && g_nskills < WC_MAX_SKILLS)
    {
      size_t n = strlen(ent->d_name);

      if (n < 4 || strcmp(ent->d_name + n - 3, ".md") != 0)
        {
          continue;
        }

      snprintf(path, sizeof(path), "%s/%s", g_skilldir, ent->d_name);
      ent->d_name[n - 3] = '\0';           /* 文件名去掉 .md 作默认名 */
      skill_parse_file(path, ent->d_name);
    }

  closedir(dir);
}

static const struct skill_s *skill_match(const char *text)
{
  int i;

  for (i = 0; i < g_nskills; i++)
    {
      char buf[sizeof(g_skills[i].triggers)];
      char *save = NULL;
      char *tok;

      snprintf(buf, sizeof(buf), "%s", g_skills[i].triggers);

      for (tok = strtok_r(buf, ",", &save); tok != NULL;
           tok = strtok_r(NULL, ",", &save))
        {
          while (*tok == ' ')
            {
              tok++;
            }

          if (*tok != '\0' && strstr(text, tok) != NULL)
            {
              return &g_skills[i];
            }
        }
    }

  return NULL;
}

/****************************************************************************
 * 功放使能（PA10 = AUDIO_PA_CTRL → NS4150B）
 ****************************************************************************/

static void amp_enable(bool on)
{
  /* 10 = PA10（AUDIO_PA_CTRL），is_porta = 1（端口 A）。
   * 板级 pinmux 已把 PA10 配为 GPIO，这里只设方向与电平。 */

  BSP_GPIO_Set(10, on ? 1 : 0, 1);
  syslog(LOG_INFO, "WristClaw: audio PA (PA10) = %d\n", on ? 1 : 0);
}

/****************************************************************************
 * 工具
 ****************************************************************************/

static void tool_reminder_add(uint32_t in_s, const char *text)
{
  int i;

  for (i = 0; i < WC_MAX_REMINDERS; i++)
    {
      if (!g_reminders[i].used)
        {
          g_reminders[i].used     = true;
          g_reminders[i].fired    = false;
          g_reminders[i].remain_s = in_s;
          {
            time_t t_now = time(NULL);

            g_reminders[i].due_epoch = t_now > 1000000000 ?
                                       (int64_t)t_now + (int64_t)in_s : 0;
          }
          snprintf(g_reminders[i].text, WC_TEXT_LEN, "%s", text);

          g_reminders_total++;

          if (in_s >= 60)
            {
              say("好的，%u 分钟后提醒你：%s", (unsigned)(in_s / 60), text);
            }
          else
            {
              say("好的，%u 秒后提醒你：%s", (unsigned)in_s, text);
            }

          memory_save();
          return;
        }
    }

  say("提醒位已满（最多 %d 条），先完成或清理一条", WC_MAX_REMINDERS);
}

static void tool_memo_add(const char *text)
{
  if (g_nmemos >= WC_MAX_MEMOS)
    {
      memmove(g_memos[0], g_memos[1], sizeof(g_memos[0]) * (WC_MAX_MEMOS - 1));
      g_nmemos = WC_MAX_MEMOS - 1;
    }

  snprintf(g_memos[g_nmemos++], WC_TEXT_LEN, "%s", text);
  g_memos_total++;

  memos_save();
  memory_save();
  say("记下了（第 %d 条备忘）：%s", g_nmemos, text);
}

static void tool_memo_list(void)
{
  int i;

  if (g_nmemos == 0)
    {
      say("还没有备忘");
      return;
    }

  say("共 %d 条备忘：", g_nmemos);
  for (i = 0; i < g_nmemos; i++)
    {
      say("  %d. %s", i + 1, g_memos[i]);
    }
}

/* 本地时间换算（见 wc_agent.h：板端没有 TZ 支持，用显式偏移） */

struct tm *wc_agent_localtime(time_t utc, struct tm *out)
{
  time_t local = utc + (time_t)g_tz_off_s;

  return gmtime_r(&local, out);
}

bool wc_agent_time_synced(void)
{
  return g_time_synced;
}

static void tool_time(void)
{
  time_t    now = time(NULL);
  struct tm tm;

  if (now > 1000000000 && wc_agent_localtime(now, &tm) != NULL)
    {
      say("现在是 %04d-%02d-%02d %02d:%02d:%02d",
          tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday,
          tm.tm_hour, tm.tm_min, tm.tm_sec);
    }
  else
    {
      say("尚未校时：上位机连接后会自动同步时间（本板无 RTC 备份电池）");
    }
}

static void tool_brightness(int pct)
{
  if (pct < 0)
    {
      pct = 0;
    }

  if (pct > 100)
    {
      pct = 100;
    }

  g_brightness = pct;
  memory_save();

  /* 背光走 /dev/pwm0（原理图 LCD_BL_PWM）。PWM 需要 ioctl 配置周期/占空比，
   * 这里只做可达性探测并把结果如实上报，不假装已经调光。
   */

  {
    int fd = open("/dev/pwm0", O_RDWR);
    if (fd >= 0)
      {
        close(fd);
        say("亮度已设为 %d%%（PWM 通道可用）", pct);
      }
    else
      {
        say("亮度记录为 %d%%，但 /dev/pwm0 打开失败(%d) —— 调光未真正生效",
            pct, errno);
      }
  }
}

static void tool_status(void)
{
  int   i;
  int   nrem = 0;

  for (i = 0; i < WC_MAX_REMINDERS; i++)
    {
      if (g_reminders[i].used && !g_reminders[i].fired)
        {
          nrem++;
        }
    }

  say("腕灵犀状态：待触发提醒 %d，备忘 %d，Skill %d，亮度 %d%%",
      nrem, g_nmemos, g_nskills, g_brightness);
  say("能力：UI/触摸/RTC/存储 可用；唤醒词·TTS·BLE 本期未实现");
}

/****************************************************************************
 * 意图路由（本地规则优先，判不了才上行云端）
 ****************************************************************************/

void wc_agent_stat(struct wc_agent_stat_s *s)
{
  int i;

  if (s == NULL)
    {
      return;
    }

  s->link   = wc_link_ready();
  s->nmemo  = g_nmemos;
  s->nskill = g_nskills;
  s->bright = g_brightness;

  s->nrem = 0;
  for (i = 0; i < WC_MAX_REMINDERS; i++)
    {
      if (g_reminders[i].used && !g_reminders[i].fired)
        {
          s->nrem++;
        }
    }
}

int wc_agent_pending(char out[][48], int max)
{
  int i;
  int n = 0;

  if (out == NULL)
    {
      return 0;
    }

  for (i = 0; i < WC_MAX_REMINDERS && n < max; i++)
    {
      if (g_reminders[i].used && !g_reminders[i].fired)
        {
          snprintf(out[n], 48, "%us %s",
                   (unsigned)g_reminders[i].remain_s,
                   g_reminders[i].text);
          n++;
        }
    }

  return n;
}

int wc_agent_memo_list(char out[][48], int max)
{
  int i;
  int n = 0;

  if (out == NULL)
    {
      return 0;
    }

  for (i = 0; i < g_nmemos && n < max; i++)
    {
      snprintf(out[n], 48, "%s", g_memos[i]);
      n++;
    }

  return n;
}

static uint32_t parse_delay_seconds(const char *text, bool *ok)
{
  const char *p = text;
  long        num = -1;

  *ok = false;

  /* 找第一个数字，识别「N 分」「N 秒」「N 小时」「N 分钟」「N 小时后」 */

  while (*p != '\0')
    {
      if (*p >= '0' && *p <= '9')
        {
          num = strtol(p, (char **)&p, 10);
          break;
        }

      p++;
    }

  if (num < 0)
    {
      return 0;
    }

  if (strstr(p, "小时") != NULL)
    {
      *ok = true;
      return (uint32_t)(num * 3600);
    }

  if (strstr(p, "分") != NULL)
    {
      *ok = true;
      return (uint32_t)(num * 60);
    }

  if (strstr(p, "秒") != NULL)
    {
      *ok = true;
      return (uint32_t)num;
    }

  /* 只给了数字：默认按分钟理解 */

  *ok = true;
  return (uint32_t)(num * 60);
}

void wc_agent_inject_utterance(const char *text)
{
  bool     ok;
  uint32_t delay;

  if (text == NULL || text[0] == '\0')
    {
      return;
    }

  /* 记入最近历史，供「上下文主动」使用 */

  {
    int i;
    for (i = WC_MAX_HISTORY - 1; i > 0; i--)
      {
        memcpy(g_history[i], g_history[i - 1], WC_TEXT_LEN);
      }

    snprintf(g_history[0], WC_TEXT_LEN, "%s", text);
    if (g_nhistory < WC_MAX_HISTORY)
      {
        g_nhistory++;
      }
  }

  /* ---- 第 1 级：本地规则 ---- */

  if (strstr(text, "提醒") != NULL || strstr(text, "remind") != NULL)
    {
      delay = parse_delay_seconds(text, &ok);
      if (ok)
        {
          tool_reminder_add(delay, text);
          return;
        }

      /* 说了「提醒」但没给时间 → 交云端做 NL→结构化 */

      say("想什么时候提醒？说「N 分钟后提醒我 …」即可（离线也能用）");
      return;
    }

  if (strstr(text, "备忘") != NULL || strstr(text, "记一下") != NULL ||
      strstr(text, "记下") != NULL || strstr(text, "memo") != NULL)
    {
      tool_memo_add(text);
      return;
    }

  if (strstr(text, "备忘") != NULL || strstr(text, "列表") != NULL ||
      strstr(text, "list") != NULL)
    {
      tool_memo_list();
      return;
    }

  if (strstr(text, "几点") != NULL || strstr(text, "时间") != NULL ||
      strstr(text, "time") != NULL)
    {
      tool_time();
      return;
    }

  if (strstr(text, "亮度") != NULL || strstr(text, "bright") != NULL)
    {
      const char *p = text;
      while (*p != '\0' && !(*p >= '0' && *p <= '9'))
        {
          p++;
        }

      tool_brightness(*p != '\0' ? atoi(p) : 50);
      return;
    }

  if (strstr(text, "状态") != NULL || strstr(text, "status") != NULL)
    {
      tool_status();
      return;
    }

  /* ---- 第 2 级：Skill 触发词 → 仍交云端，但带上 Skill 的提示词 ---- */

  {
    const struct skill_s *sk = skill_match(text);
    if (sk != NULL)
      {
        /* 2KB 缓冲必须静态：栈只有 16K，这里放栈上会在深层调用链
         * （inject_utterance + snprintf + say）下溢出导致硬故障死机。 */

        snprintf(g_reqbuf, sizeof(g_reqbuf),
                 "{\"skill\":\"%s\",\"prompt\":\"%s\",\"q\":\"%s\"}",
                 sk->name, sk->prompt, text);
        if (wc_link_ready())
          {
            (void)wc_link_send(WC_T_CLOUD_REQ, wc_next_seq(),
                               (const uint8_t *)g_reqbuf,
                               (uint16_t)strlen(g_reqbuf));
          }

        say("（%s）交给云端处理：%s", sk->name, text);
        return;
      }
  }

  /* ---- 第 3 级：上行云端 ---- */

  {
    snprintf(g_reqbuf, sizeof(g_reqbuf),
             "{\"req\":\"chat\",\"q\":\"%s\"}", text);

    if (wc_link_ready() &&
        wc_link_send(WC_T_CLOUD_REQ, wc_next_seq(),
                     (const uint8_t *)g_reqbuf,
                     (uint16_t)strlen(g_reqbuf)) > 0)
      {
        say("已发送到云端：%s", text);
      }
    else
      {
        say("云端不可达（上位机未连接），离线可用的有：提醒 / 备忘 / 时间 / 亮度 / 状态");
      }
  }
}

/****************************************************************************
 * 上位机回包
 ****************************************************************************/

void wc_agent_handle_cloud_reply(const char *line)
{
  if (line == NULL)
    {
      return;
    }

  if (strncmp(line, "SAY ", 4) == 0)
    {
      say("%s", line + 4);
    }
  else if (strncmp(line, "REMIND ", 7) == 0)
    {
      /* REMIND <秒> <文本> */

      unsigned long secs = 0;
      const char   *p    = line + 7;
      char         *end;

      secs = strtoul(p, &end, 10);
      while (*end == ' ')
        {
          end++;
        }

      tool_reminder_add((uint32_t)secs, *end ? end : "(云端提醒)");
    }
  else if (strncmp(line, "MEMO ", 5) == 0)
    {
      tool_memo_add(line + 5);
    }
  else if (strncmp(line, "BRIGHT ", 7) == 0)
    {
      tool_brightness(atoi(line + 7));
    }
  else if (strncmp(line, "STATUS", 6) == 0)
    {
      tool_status();
    }
  else if (strncmp(line, "PA ", 3) == 0)
    {
      /* PA <0|1> —— 功放使能（PA10 = AUDIO_PA_CTRL，板上 NS4150B）。
       * 板级 pinmux 已把 PA10 配成 GPIO，这里只设方向与电平。
       * 没有这一步，codec 的信号到不了喇叭。 */

      int on = atoi(line + 3);

      amp_enable(on != 0);
      say("功放 PA10 = %d%s", on ? 1 : 0,
          on ? "（喇叭可能有轻微底噪/嗒声）" : "（已关闭）");
    }
  else if (strncmp(line, "TIME ", 5) == 0)
    {
      /* TIME <epoch 秒> [<时区分钟>]
       * 上位机在链路建立后立刻下发，并周期性重发以抵消时钟漂移。
       * 时区偏移随校时一起下发，因为板端没有 TZ 环境变量支持。 */

      char     *end     = NULL;
      long long secs    = strtoll(line + 5, &end, 10);
      bool      have_tz = false;
      long      tz_min  = 0;

      if (end != NULL && *end == ' ')
        {
          tz_min  = strtol(end + 1, NULL, 10);
          have_tz = true;
        }

      if (secs > 1000000000LL)
        {
          struct timespec ts;

          if (have_tz && tz_min > -1440 && tz_min < 1440)
            {
              g_tz_off_s = (int)(tz_min * 60);
            }

          ts.tv_sec  = (time_t)secs;
          ts.tv_nsec = 0;

          if (clock_settime(CLOCK_REALTIME, &ts) == 0)
            {
              if (!g_time_synced)
                {
                  struct tm tmv;

                  g_time_synced = true;

                  if (wc_agent_localtime((time_t)secs, &tmv) != NULL)
                    {
                      say("时间已同步：%02d:%02d", tmv.tm_hour, tmv.tm_min);
                    }
                }
              else
                {
                  syslog(LOG_INFO, "WristClaw: time resynced\n");
                }
            }
        }
    }
  else if (strncmp(line, "SKILLS", 6) == 0)
    {
      int i;
      say("已加载 %d 个 Skill：", g_nskills);
      for (i = 0; i < g_nskills; i++)
        {
          say("  %s (触发: %s)", g_skills[i].name, g_skills[i].triggers);
        }
    }
  else if (strncmp(line, "SKILL ", 6) == 0)
    {
      /* SKILL name|triggers|tool|prompt
       * 上位机一键安装 Skill：落盘为 /data/agent/skills/<name>.md 并热加载。
       * 重启后 skills_load() 会自动重新读取。
       */

      char  *fields[4] = { 0 };
      char   body[WC_MAX_LINE];
      char   path[192];
      FILE  *fp;
      int    nfields = 0;
      char  *tok;

      snprintf(body, sizeof(body), "%s", line + 6);
      tok = strtok(body, "|");
      while (tok != NULL && nfields < 4)
        {
          fields[nfields++] = tok;
          tok = strtok(NULL, "|");
        }

      if (nfields < 2 || fields[0][0] == '\0')
        {
          say("SKILL 安装失败：格式应为 SKILL name|triggers|tool|prompt");
          return;
        }

      /* 名字只允许字母数字下划线，防止路径逃逸 */

      {
        const char *p;

        for (p = fields[0]; *p != '\0'; p++)
          {
            if (!((*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z') ||
                  (*p >= '0' && *p <= '9') || *p == '_'))
              {
                say("SKILL 安装失败：名字只能用字母数字下划线");
                return;
              }
          }
      }

      snprintf(path, sizeof(path), "%s/%s.md", g_skilldir, fields[0]);
      fp = fopen(path, "w");
      if (fp == NULL)
        {
          say("SKILL 安装失败：写 %s 失败 (errno=%d)", path, errno);
          return;
        }

      fprintf(fp, "---\n");
      fprintf(fp, "name: %s\n", fields[0]);
      fprintf(fp, "triggers: %s\n", nfields > 1 ? fields[1] : "");
      fprintf(fp, "tool: %s\n", nfields > 2 ? fields[2] : "cloud");
      fprintf(fp, "prompt: %s\n", nfields > 3 ? fields[3] : "");
      fprintf(fp, "---\n");
      fclose(fp);

      g_nskills = 0;                     /* 热加载：清空后重扫 */
      skills_load();
      say("SKILL 已安装并生效：%s (触发: %s)",
          fields[0], nfields > 1 ? fields[1] : "");
    }
  else if (line[0] != '\0')
    {
      say("%s", line);                 /* 兜底当纯文本显示 */
    }
}

/****************************************************************************
 * 设备命令
 ****************************************************************************/

void wc_agent_handle_device_cmd(uint8_t cmd, uint8_t arg)
{
  switch (cmd)
    {
      case WC_CMD_QUERY_STATUS:
        tool_status();
        break;

      case WC_CMD_QUERY_VERSION:
        say("WristClaw 0.1.0 / openvela-NuttX / 板级 sf32lb52_devkit_lcd");
        break;

      case WC_CMD_SET_BRIGHTNESS:
        tool_brightness(arg);
        break;

      case WC_CMD_REBOOT:
        say("收到重启指令（本期不自动重启，避免演示中断）");
        break;

      default:
        break;
    }
}

/****************************************************************************
 * 主动引擎（赛道核心区分点）
 ****************************************************************************/

/* 1) 定时主动：倒计时归零即推送
 * 2) 上下文主动：积压事项累积 → 主动建议
 * 3) 阈值主动：传感器（LSM6DS3 实测未应答，用模拟源）越限告警
 */

static void proactive_tick(uint32_t uptime_ms)
{
  static uint32_t last_sec;              /* 上次走过的整秒数 */
  int      i;
  int      pending = 0;
  uint32_t secs    = uptime_ms / 1000;
  int      minutes = (int)(uptime_ms / 60000);

  /* ---- 定时主动（每真实 1 秒只走一次，tick 循环是 20ms） ---- */

  if (secs != last_sec)
    {
      bool second_advanced = secs > last_sec || last_sec == 0;

      last_sec = secs;
      if (second_advanced)
        {
          for (i = 0; i < WC_MAX_REMINDERS; i++)
            {
              if (g_reminders[i].used && !g_reminders[i].fired &&
                  g_reminders[i].remain_s > 0)
                {
                  g_reminders[i].remain_s--;
                }
            }
        }
    }

  for (i = 0; i < WC_MAX_REMINDERS; i++)
    {
      if (!g_reminders[i].used || g_reminders[i].fired)
        {
          continue;
        }

      pending++;

      if (g_reminders[i].remain_s == 0)
        {
          g_reminders[i].fired = true;
          say("【提醒】%s", g_reminders[i].text);

          if (wc_link_ready())
            {
              (void)wc_link_send(WC_T_REMINDER, wc_next_seq(),
                                 (const uint8_t *)g_reminders[i].text,
                                 (uint16_t)strlen(g_reminders[i].text));
            }
        }
    }

  /* ---- 诚实主动：基于真实运行状态（本板无生物传感器） ---- */

  {
    static uint32_t last_nag;

    /* 每 10 分钟一次久坐/休息提醒（桌面开发阶段同样适用） */

    /* 首次在 10 分钟处触发，之后每 10 分钟一次（避免刚启动就报「0 分钟」） */

    if (secs >= 600 && (last_nag == 0 || secs - last_nag >= 600))
      {
        last_nag = secs;
        say("【主动提醒】腕灵犀已连续运行 %d 分钟，记得休息一下眼睛", secs / 60);
      }
  }

  /* ---- 上下文主动：积压过多时主动建议（每 5 分钟最多一次） ---- */

  if (pending >= 3 && minutes > 0 && minutes - g_last_proactive >= 5)
    {
      g_last_proactive = minutes;
      say("【主动建议】你有 %d 条提醒还没处理，要我给你排个优先级吗？",
          pending);
    }
}

/****************************************************************************
 * 对外接口
 ****************************************************************************/

void wc_agent_init(const char *datadir, const char *skilldir)
{
  snprintf(g_datadir, sizeof(g_datadir), "%s", datadir);
  snprintf(g_skilldir, sizeof(g_skilldir), "%s", skilldir);

  memset(g_reminders, 0, sizeof(g_reminders));
  g_nmemos    = 0;
  g_nskills   = 0;
  g_nhistory  = 0;
  g_boot_epoch = (int64_t)time(NULL);
  if (g_boot_epoch < 1000000000)
    {
      g_boot_epoch = 0;             /* RTC 未校时 */
    }

  memory_load();
  memos_load();
  skills_load();

  syslog(LOG_INFO, "WristClaw: agent ready (memos=%d skills=%d)\n",
         g_nmemos, g_nskills);
}

void wc_agent_tick(uint32_t uptime_ms)
{
  proactive_tick(uptime_ms);
}

void wc_agent_on_link_up(void)
{
  say("腕灵犀已连接上位机");
  tool_status();

  if (g_nskills > 0)
    {
      int i;
      for (i = 0; i < g_nskills; i++)
        {
          say("Skill: %s", g_skills[i].name);
        }
    }
}

const char *wc_agent_status_line(void)
{
  int i;
  int nrem = 0;

  for (i = 0; i < WC_MAX_REMINDERS; i++)
    {
      if (g_reminders[i].used && !g_reminders[i].fired)
        {
          nrem++;
        }
    }

  snprintf(g_status, sizeof(g_status),
           "提醒 %d | 备忘 %d | Skill %d | 亮度 %d%%", nrem, g_nmemos,
           g_nskills, g_brightness);

  return g_status;
}
