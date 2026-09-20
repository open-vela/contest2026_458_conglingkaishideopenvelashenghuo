/****************************************************************************
 * app/wristclaw/wc_ui.c
 *
 * 腕灵犀手表 UI（LVGL 9.2，390x450 AMOLED）
 *
 * 三页 tileview（触摸左右滑动）：
 *   页0 表盘：lv_meter 模拟表针（时/分/秒）+ 数字时间 + 日期 + 状态栏
 *   页1 对话：聊天气泡（用户右侧 / Agent 左侧，自动滚到最新）
 *   页2 提醒：卡片式提醒列表 + 备忘列表
 *
 * 中文用自生成的 GB2312 全量位图字体（wc_font_16.h，6763 汉字 + ASCII，
 * 约 930KB，--no-compress 导出、XIP 直读不占 RAM）。
 * 与 Agent 同任务：wc_ui_tick() 由主循环调，所有 LVGL 操作都在同一线程。
 ****************************************************************************/

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <nuttx/config.h>

#include <stdio.h>
#include <string.h>
#include <math.h>
#include <time.h>

#include <lvgl/lvgl.h>

#include "wc_ui.h"
#include "wc_agent.h"

/* 中文位图字体：由 tests/gen_font.js（lv_font_conv + 黑体）
 * 生成，覆盖 ASCII + GB2312 全量汉字。用 --no-compress 导出，位图为
 * 线性数据，SF32LB52 直接从 NOR 上 XIP 读取，不占 RAM。
 * 作为头文件包含（而不是独立编译单元），这样新增字体不需要改
 * CMakeLists 触发全量重配。 */

#include "wc_font_16.h"

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#define UI_BUBBLES      6          /* 对话页保留的气泡数 */
#define UI_BUBBLE_W     250

/* 配色（AMOLED 深色） */

#define C_BG            0x000000
#define C_PANEL         0x141A1C
#define C_ACCENT        0x26D0CE
#define C_TEXT          0xECECEC
#define C_DIM           0x8A8A8A
#define C_USER_BG       0x1E5F5C

/****************************************************************************
 * Private Data
 ****************************************************************************/

static bool g_ui_ok;
static struct wc_ui_info_s g_info;

/* 状态栏 */
static lv_obj_t *g_lbl_link;
static lv_obj_t *g_dots[3];

/* 表盘页 */
static lv_obj_t *g_scale;
static lv_obj_t *g_needle_h, *g_needle_m, *g_needle_s;
static int       g_hh, g_mm, g_ss;      /* 当前时间（供自绘表针） */
static lv_obj_t *g_lbl_time;
static lv_obj_t *g_lbl_date;
static lv_obj_t *g_lbl_chip;

/* 对话页 */
static lv_obj_t *g_chat;
static lv_obj_t *g_bubbles[UI_BUBBLES];
static int       g_nbubbles;

/* 提醒页 */
static lv_obj_t *g_list_rem;
static lv_obj_t *g_lbl_memo;

/* tileview 与页指针（用于指示点高亮） */

static lv_obj_t *g_tv;
static lv_obj_t *g_tiles[3];

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static const lv_font_t *cjk(void)
{
  return &wc_font_16;
}

static const lv_font_t *big(void)
{
#ifdef CONFIG_LV_FONT_MONTSERRAT_28
  return &lv_font_montserrat_28;
#else
  return LV_FONT_DEFAULT;
#endif
}

static const lv_font_t *mid(void)
{
#ifdef CONFIG_LV_FONT_MONTSERRAT_20
  return &lv_font_montserrat_20;
#else
  return LV_FONT_DEFAULT;
#endif
}

/* 说明：LVGL 的日志由 NuttX 移植层接管（lv_nuttx_entry.c 里的
 * syslog_print 会在 lv_nuttx_init 时覆盖任何先前注册的回调），
 * 应用侧注册过滤器无效，因此这里不再注册。中文缺字告警已通过
 * 完整的 CJK 位图字体从根上消除。 */

/* 一句话中文标签（自动换行） */

static lv_obj_t *mk_label(lv_obj_t *parent, const char *text,
                          const lv_font_t *font, lv_color_t color,
                          int width)
{
  lv_obj_t *l = lv_label_create(parent);

  lv_obj_set_width(l, width);
  lv_label_set_long_mode(l, LV_LABEL_LONG_WRAP);
  lv_obj_set_style_text_font(l, font, 0);
  lv_obj_set_style_text_color(l, color, 0);
  lv_label_set_text(l, text);
  return l;
}

/* 加气泡：is_user=true 靠右（用户），false 靠左（Agent） */

static void add_bubble(const char *text, bool is_user)
{
  lv_obj_t *b;
  lv_obj_t *l;
  int       i;
  int       y = 6;

  if (!g_ui_ok || g_chat == NULL)
    {
      return;
    }

  if (g_nbubbles > 0)
    {
      lv_obj_t *prev = g_bubbles[g_nbubbles - 1];

      lv_obj_update_layout(prev);
      y = lv_obj_get_y(prev) + lv_obj_get_height(prev) + 8;
    }

  /* 满了删最老的 */

  if (g_nbubbles == UI_BUBBLES)
    {
      lv_obj_del(g_bubbles[0]);
      for (i = 1; i < UI_BUBBLES; i++)
        {
          g_bubbles[i - 1] = g_bubbles[i];
        }
      g_nbubbles--;
    }

  b = lv_obj_create(g_chat);
  lv_obj_set_width(b, LV_SIZE_CONTENT);
  lv_obj_set_style_max_width(b, UI_BUBBLE_W, 0);
  lv_obj_set_height(b, LV_SIZE_CONTENT);
  lv_obj_set_style_radius(b, 14, 0);
  lv_obj_set_style_pad_all(b, 10, 0);
  lv_obj_set_style_border_width(b, 0, 0);
  lv_obj_set_style_bg_color(b, lv_color_hex(is_user ? C_USER_BG : C_PANEL), 0);
  lv_obj_set_style_bg_opa(b, LV_OPA_COVER, 0);
  lv_obj_clear_flag(b, LV_OBJ_FLAG_SCROLLABLE);

  l = lv_label_create(b);
  lv_obj_set_style_text_font(l, cjk(), 0);
  lv_obj_set_style_text_color(l, lv_color_hex(C_TEXT), 0);
  lv_label_set_long_mode(l, LV_LABEL_LONG_WRAP);
  lv_label_set_text(l, text);
  lv_obj_set_width(l, UI_BUBBLE_W - 30);

  lv_obj_align(b, is_user ? LV_ALIGN_TOP_RIGHT : LV_ALIGN_TOP_LEFT,
               is_user ? -4 : 4, y);

  g_bubbles[g_nbubbles++] = b;

  lv_obj_scroll_to_view(b, LV_ANIM_OFF);
}

/* 表针更新：官方 API lv_scale_set_line_needle_value（见 LVGL 官方示例
 * lv_example_scale_6「A round scale with multiple needles, resembling a clock」） */

static void clock_set_time(void)
{
  if (g_scale == NULL)
    {
      return;
    }

  lv_scale_set_line_needle_value(g_scale, g_needle_h, 62,
                                 (g_hh % 12) * 5 + g_mm / 12);
  lv_scale_set_line_needle_value(g_scale, g_needle_m, 92, g_mm);
  lv_scale_set_line_needle_value(g_scale, g_needle_s, 104, g_ss);
}

/* 1Hz 刷新：表针 + 数字时间 + 日期 + 页脚状态 */

static void ui_timer_cb(lv_timer_t *timer)
{
  struct tm tm_now;
  time_t    now;
  char      buf[64];
  static const char *wk[] = { "日", "一", "二", "三", "四", "五", "六" };

  now = time(NULL);

  if (!wc_agent_time_synced())
    {
      /* 未校时：本板无 RTC 备份电池，time() 不可信，宁可不显示也不要
       * 摆一个 1970 年的假时间。上位机连接后会自动下发 TIME 校时。 */

      g_hh = 0;
      g_mm = 0;
      g_ss = 0;
      clock_set_time();
      lv_label_set_text(g_lbl_time, "--:--");
      lv_label_set_text(g_lbl_date, "等待上位机校时");
    }
  else
    {
      wc_agent_localtime(now, &tm_now);

      g_hh = tm_now.tm_hour;
      g_mm = tm_now.tm_min;
      g_ss = tm_now.tm_sec;

      clock_set_time();

      snprintf(buf, sizeof(buf), "%02d:%02d", tm_now.tm_hour, tm_now.tm_min);
      lv_label_set_text(g_lbl_time, buf);

      snprintf(buf, sizeof(buf), "%d月%d日 星期%s",
               tm_now.tm_mon + 1, tm_now.tm_mday, wk[tm_now.tm_wday % 7]);
      lv_label_set_text(g_lbl_date, buf);
    }

  snprintf(buf, sizeof(buf), "提醒 %d   备忘 %d   Skill %d",
           g_info.nrem, g_info.nmemo, g_info.nskill);
  lv_label_set_text(g_lbl_chip, buf);
}

/* 翻页 → 更新指示点 */

static void tv_event_cb(lv_event_t *e)
{
  lv_obj_t *act = lv_tileview_get_tile_active(g_tv);
  int       i;

  for (i = 0; i < 3; i++)
    {
      if (g_dots[i] != NULL)
        {
          lv_obj_set_style_bg_color(g_dots[i],
                                    lv_color_hex(g_tiles[i] == act ?
                                                 C_ACCENT : 0x404040), 0);
        }
    }
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

void wc_ui_init(void)
{
  lv_nuttx_dsc_t    dsc;
  lv_nuttx_result_t result;
  lv_obj_t         *tile;
  lv_obj_t         *root;
  lv_obj_t         *bar;

  lv_color_t        fg  = lv_color_hex(C_TEXT);
  lv_color_t        dim = lv_color_hex(C_DIM);
  int               i;

  g_ui_ok = false;

  if (lv_is_initialized())
    {
      return;
    }

  lv_init();
  lv_nuttx_dsc_init(&dsc);
  dsc.fb_path    = "/dev/lcd0";
  dsc.input_path = "/dev/input0";
  lv_nuttx_init(&dsc, &result);

  if (result.disp == NULL)
    {
      return;
    }

  lv_obj_set_style_bg_color(lv_screen_active(), lv_color_hex(C_BG), 0);

  /* ---- 根容器：纵向 flex，状态栏固定 34px，tileview 占满剩余 ----
   *
   * 注意：不要写 `lv_obj_set_size(tv, LV_PCT(100), LV_PCT(100) - 34)`。
   * LVGL 的 LV_PCT() 返回的是编码过的特殊坐标值，与整数相减会得到
   * 一个非法尺寸，导致 tileview 布局失效并压住状态栏（表现为顶部
   * 一条黑框、滑动时盖住表盘）。用 flex + flex_grow 表达"占满剩余"。 */

  root = lv_obj_create(lv_screen_active());
  lv_obj_set_size(root, LV_PCT(100), LV_PCT(100));
  lv_obj_set_pos(root, 0, 0);
  lv_obj_set_style_bg_color(root, lv_color_hex(C_BG), 0);
  lv_obj_set_style_bg_opa(root, LV_OPA_COVER, 0);
  lv_obj_set_style_border_width(root, 0, 0);
  lv_obj_set_style_radius(root, 0, 0);
  lv_obj_set_style_pad_all(root, 0, 0);
  lv_obj_set_style_pad_row(root, 0, 0);
  lv_obj_clear_flag(root, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_set_layout(root, LV_LAYOUT_FLEX);
  lv_obj_set_flex_flow(root, LV_FLEX_FLOW_COLUMN);

  /* ---- 顶部状态栏 ---- */

  bar = lv_obj_create(root);
  lv_obj_set_size(bar, LV_PCT(100), 34);
  lv_obj_set_style_bg_color(bar, lv_color_hex(C_BG), 0);
  lv_obj_set_style_border_width(bar, 0, 0);
  lv_obj_set_style_pad_all(bar, 6, 0);
  lv_obj_clear_flag(bar, LV_OBJ_FLAG_SCROLLABLE);

  g_lbl_link = mk_label(bar, LV_SYMBOL_USB " 未连接", cjk(), dim, 140);
  lv_obj_align(g_lbl_link, LV_ALIGN_LEFT_MID, 0, 0);

  for (i = 0; i < 3; i++)
    {
      g_dots[i] = lv_obj_create(bar);
      lv_obj_set_size(g_dots[i], 8, 8);
      lv_obj_set_style_radius(g_dots[i], LV_RADIUS_CIRCLE, 0);
      lv_obj_set_style_border_width(g_dots[i], 0, 0);
      lv_obj_set_style_bg_color(g_dots[i],
                                lv_color_hex(i == 0 ? C_ACCENT : 0x404040), 0);
      lv_obj_align(g_dots[i], LV_ALIGN_RIGHT_MID, -14 * (2 - i), 0);
    }

  /* ---- 三页 tileview ---- */

  g_tv = lv_tileview_create(root);
  lv_obj_set_width(g_tv, LV_PCT(100));
  lv_obj_set_flex_grow(g_tv, 1);
  lv_obj_set_style_bg_color(g_tv, lv_color_hex(C_BG), 0);
  lv_obj_set_style_anim_duration(g_tv, 0, 0);   /* 滑动瞬时切换，避免中间帧卡顿 */
  lv_obj_add_event_cb(g_tv, tv_event_cb, LV_EVENT_VALUE_CHANGED, NULL);

  /* ===== 页0：表盘 ===== */

  g_tiles[0] = tile = lv_tileview_add_tile(g_tv, 0, 0, LV_DIR_HOR);
  lv_obj_set_style_bg_color(tile, lv_color_hex(C_BG), 0);
  lv_obj_set_style_border_width(tile, 0, 0);

  /* 表盘：官方写法（LVGL 示例 lv_example_scale_6 = 像钟表一样多表针） */

  g_scale = lv_scale_create(tile);
  lv_obj_set_size(g_scale, 250, 250);
  lv_obj_align(g_scale, LV_ALIGN_TOP_MID, 0, 2);
  lv_scale_set_mode(g_scale, LV_SCALE_MODE_ROUND_INNER);
  lv_obj_set_style_bg_opa(g_scale, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(g_scale, lv_color_hex(0x0A0E10), 0);
  lv_obj_set_style_radius(g_scale, LV_RADIUS_CIRCLE, 0);
  lv_obj_set_style_clip_corner(g_scale, true, 0);

  lv_scale_set_label_show(g_scale, true);
  lv_scale_set_total_tick_count(g_scale, 61);
  lv_scale_set_major_tick_every(g_scale, 5);

  {
    static const char *hour_ticks[] = { "12", "1", "2", "3", "4", "5",
                                        "6", "7", "8", "9", "10", "11", NULL };
    lv_scale_set_text_src(g_scale, hour_ticks);
  }

  /* 主刻度（每 5 分钟）/ 次刻度 / 外圈 —— 官方样式分段一致 */

  {
    static lv_style_t st_major;
    static lv_style_t st_minor;
    static lv_style_t st_arc;

    lv_style_init(&st_major);
    lv_style_set_text_font(&st_major, mid());
    lv_style_set_text_color(&st_major, lv_color_hex(0xB8C4C8));
    lv_style_set_line_color(&st_major, lv_color_hex(0xD8E4E8));
    lv_style_set_length(&st_major, 10);
    lv_style_set_line_width(&st_major, 2);
    lv_obj_add_style(g_scale, &st_major, LV_PART_INDICATOR);

    lv_style_init(&st_minor);
    lv_style_set_line_color(&st_minor, lv_color_hex(0x3E4A4E));
    lv_style_set_length(&st_minor, 5);
    lv_style_set_line_width(&st_minor, 1);
    lv_obj_add_style(g_scale, &st_minor, LV_PART_ITEMS);

    lv_style_init(&st_arc);
    lv_style_set_arc_color(&st_arc, lv_color_hex(0x1E2A2E));
    lv_style_set_arc_width(&st_arc, 3);
    lv_obj_add_style(g_scale, &st_arc, LV_PART_MAIN);
  }

  lv_scale_set_range(g_scale, 0, 60);
  lv_scale_set_angle_range(g_scale, 360);
  lv_scale_set_rotation(g_scale, 270);

  /* 三根表针：官方 API lv_scale_set_line_needle_value */

  g_needle_h = lv_line_create(g_scale);
  lv_obj_set_style_line_width(g_needle_h, 5, 0);
  lv_obj_set_style_line_rounded(g_needle_h, true, 0);
  lv_obj_set_style_line_color(g_needle_h, lv_color_hex(C_TEXT), 0);

  g_needle_m = lv_line_create(g_scale);
  lv_obj_set_style_line_width(g_needle_m, 3, 0);
  lv_obj_set_style_line_rounded(g_needle_m, true, 0);
  lv_obj_set_style_line_color(g_needle_m, lv_color_hex(C_TEXT), 0);

  g_needle_s = lv_line_create(g_scale);
  lv_obj_set_style_line_width(g_needle_s, 2, 0);
  lv_obj_set_style_line_rounded(g_needle_s, true, 0);
  lv_obj_set_style_line_color(g_needle_s, lv_color_hex(C_ACCENT), 0);

  g_lbl_time = mk_label(tile, "--:--", big(), fg, 220);
  lv_obj_set_style_text_align(g_lbl_time, LV_TEXT_ALIGN_CENTER, 0);
  lv_obj_align(g_lbl_time, LV_ALIGN_TOP_MID, 0, 250);

  g_lbl_date = mk_label(tile, " ", cjk(), dim, 260);
  lv_obj_set_style_text_align(g_lbl_date, LV_TEXT_ALIGN_CENTER, 0);
  lv_obj_align(g_lbl_date, LV_ALIGN_TOP_MID, 0, 292);

  g_lbl_chip = mk_label(tile, "提醒 0   备忘 0   Skill 0", cjk(), dim, 300);
  lv_obj_set_style_text_align(g_lbl_chip, LV_TEXT_ALIGN_CENTER, 0);
  lv_obj_align(g_lbl_chip, LV_ALIGN_BOTTOM_MID, 0, -12);

  /* ===== 页1：对话 ===== */

  g_tiles[1] = tile = lv_tileview_add_tile(g_tv, 1, 0, LV_DIR_HOR);
  lv_obj_set_style_bg_color(tile, lv_color_hex(C_BG), 0);
  lv_obj_set_style_border_width(tile, 0, 0);

  g_chat = lv_obj_create(tile);
  lv_obj_set_size(g_chat, LV_PCT(100), LV_PCT(100));
  lv_obj_set_style_bg_color(g_chat, lv_color_hex(C_BG), 0);
  lv_obj_set_style_border_width(g_chat, 0, 0);
  lv_obj_set_style_pad_all(g_chat, 4, 0);
  lv_obj_set_scroll_dir(g_chat, LV_DIR_VER);

  /* ===== 页2：提醒 / 备忘 ===== */

  g_tiles[2] = tile = lv_tileview_add_tile(g_tv, 2, 0, LV_DIR_HOR);
  lv_obj_set_style_bg_color(tile, lv_color_hex(C_BG), 0);
  lv_obj_set_style_border_width(tile, 0, 0);

  {
    lv_obj_t *t = mk_label(tile, "待触发提醒", cjk(),
                           lv_color_hex(C_ACCENT), 200);
    lv_obj_align(t, LV_ALIGN_TOP_LEFT, 12, 6);
  }

  g_list_rem = lv_list_create(tile);
  lv_obj_set_size(g_list_rem, LV_PCT(94), 200);
  lv_obj_align(g_list_rem, LV_ALIGN_TOP_MID, 0, 34);
  lv_obj_set_style_bg_color(g_list_rem, lv_color_hex(C_BG), 0);
  lv_obj_set_style_border_width(g_list_rem, 0, 0);
  lv_obj_set_style_pad_all(g_list_rem, 0, 0);

  {
    lv_obj_t *t = mk_label(tile, "备忘", cjk(), lv_color_hex(C_ACCENT), 200);
    lv_obj_align(t, LV_ALIGN_TOP_LEFT, 12, 244);
  }

  g_lbl_memo = mk_label(tile, "暂无", cjk(), dim, 340);
  lv_obj_align(g_lbl_memo, LV_ALIGN_TOP_LEFT, 16, 272);

  lv_timer_create(ui_timer_cb, 1000, NULL);
  ui_timer_cb(NULL);

  g_ui_ok = true;
}

void wc_ui_tick(void)
{
  if (g_ui_ok)
    {
      lv_timer_handler();
    }
}

void wc_ui_say(const char *line)
{
  if (!g_ui_ok || line == NULL)
    {
      return;
    }

  add_bubble(line, false);
}

void wc_ui_user(const char *line)
{
  if (!g_ui_ok || line == NULL)
    {
      return;
    }

  add_bubble(line, true);
}

void wc_ui_status(const struct wc_ui_info_s *info)
{
  char buf[48];

  if (!g_ui_ok || info == NULL)
    {
      return;
    }

  g_info = *info;

  if (g_info.link)
    {
      snprintf(buf, sizeof(buf), LV_SYMBOL_USB " 已连接");
      lv_obj_set_style_text_color(g_lbl_link, lv_color_hex(C_ACCENT), 0);
    }
  else
    {
      snprintf(buf, sizeof(buf), LV_SYMBOL_USB " 未连接");
      lv_obj_set_style_text_color(g_lbl_link, lv_color_hex(C_DIM), 0);
    }

  lv_label_set_text(g_lbl_link, buf);
}

void wc_ui_reminders(char items[][48], int n)
{
  int i;

  if (!g_ui_ok)
    {
      return;
    }

  lv_obj_clean(g_list_rem);

  if (n == 0)
    {
      lv_obj_t *b = lv_list_add_btn(g_list_rem, LV_SYMBOL_OK, "暂无待触发提醒");
      lv_obj_set_style_text_font(b, cjk(), 0);
      return;
    }

  for (i = 0; i < n; i++)
    {
      lv_obj_t *b = lv_list_add_btn(g_list_rem, LV_SYMBOL_BELL, items[i]);

      lv_obj_set_style_text_font(b, cjk(), 0);
      lv_obj_set_style_bg_color(b, lv_color_hex(C_PANEL), 0);
      lv_obj_set_style_radius(b, 10, 0);
      lv_obj_set_style_text_color(b, lv_color_hex(C_TEXT), 0);
    }
}

void wc_ui_memos(char items[][48], int n)
{
  int  i;
  char buf[340];
  int  off = 0;

  if (!g_ui_ok)
    {
      return;
    }

  if (n == 0)
    {
      lv_label_set_text(g_lbl_memo, "暂无");
      return;
    }

  buf[0] = '\0';
  for (i = 0; i < n && off < (int)sizeof(buf) - 56; i++)
    {
      off += snprintf(buf + off, sizeof(buf) - off, "· %s\n", items[i]);
    }

  lv_label_set_text(g_lbl_memo, buf);
}
