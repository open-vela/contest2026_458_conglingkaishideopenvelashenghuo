/****************************************************************************
 * app/wristclaw/wc_ui.h
 *
 * 腕灵犀手表 UI（LVGL 9.2，390x450 AMOLED + FT6146 触摸）
 *
 * 与 Agent 同任务运行（主循环调 wc_ui_tick），共享状态无需加锁。
 * 三页 tileview，触摸左右滑动：
 *   页0 表盘：模拟表针 + 数字时间 + 日期 + 状态栏
 *   页1 对话：聊天气泡（用户右 / Agent 左）
 *   页2 提醒：卡片式提醒列表 + 备忘
 ****************************************************************************/

#ifndef __APP_WRISTCLAW_WC_UI_H
#define __APP_WRISTCLAW_WC_UI_H

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <nuttx/config.h>
#include <stdbool.h>
#include <stddef.h>

#ifdef CONFIG_GRAPHICS_LVGL

/****************************************************************************
 * Public Types
 ****************************************************************************/

struct wc_ui_info_s
{
  bool link;                        /* 上位机链路状态 */
  int  nrem;                        /* 待触发提醒数 */
  int  nmemo;                       /* 备忘条数 */
  int  nskill;                      /* 已加载 Skill 数 */
  int  bright;                      /* 亮度百分比 */
};

/****************************************************************************
 * Public Function Prototypes
 ****************************************************************************/

/* 初始化 LVGL 与显示/触摸；失败（如无 fb）时内部降级，后续调用空操作 */

void wc_ui_init(void);

/* 主循环调用：驱动 LVGL 定时器 */

void wc_ui_tick(void);

/* Agent 输出一行（say 的去向之一，同任务调用安全）→ 左侧气泡 */

void wc_ui_say(const char *line);

/* 用户输入一行（模拟语音）→ 右侧气泡 */

void wc_ui_user(const char *line);

/* 周期性喂状态快照（1Hz） */

void wc_ui_status(const struct wc_ui_info_s *info);

/* 提醒 / 备忘列表（行格式 "剩余秒s 文本"） */

void wc_ui_reminders(char items[][48], int n);
void wc_ui_memos(char items[][48], int n);

#endif /* CONFIG_GRAPHICS_LVGL */
#endif /* __APP_WRISTCLAW_WC_UI_H */
