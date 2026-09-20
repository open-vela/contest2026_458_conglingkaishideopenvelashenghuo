/****************************************************************************
 * app/wristclaw/wc_proto.c
 *
 * 帧协议 + 有界写入链路实现。协议语义见 wc_proto.h 与 protocol.py。
 ****************************************************************************/

#ifdef __NuttX__
#  include <nuttx/config.h>
#endif

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <syslog.h>
#include <sys/types.h>

#include "wc_proto.h"

/****************************************************************************
 * CRC16-CCITT —— 必须与 protocol.py 的 _crc16_ccitt() 完全一致
 ****************************************************************************/

uint16_t wc_crc16(const uint8_t *data, size_t len)
{
  uint16_t crc = 0xFFFF;
  size_t   i;
  int      b;

  for (i = 0; i < len; i++)
    {
      crc ^= (uint16_t)((uint16_t)data[i] << 8);
      for (b = 0; b < 8; b++)
        {
          if (crc & 0x8000)
            {
              crc = (uint16_t)((crc << 1) ^ 0x1021);
            }
          else
            {
              crc = (uint16_t)(crc << 1);
            }
        }
    }

  return crc;
}

/****************************************************************************
 * 组帧
 ****************************************************************************/

int wc_build(uint8_t *out, size_t outsz, uint8_t type, uint16_t seq,
             const uint8_t *payload, uint16_t len)
{
  size_t total = WC_HEADER_SIZE + (size_t)len + WC_CRC_SIZE;
  uint16_t crc;

  if (len > WC_MAX_PAYLOAD || outsz < total)
    {
      return -1;
    }

  out[0] = WC_HEAD0;
  out[1] = WC_HEAD1;
  out[2] = type;
  out[3] = (uint8_t)(seq >> 8);
  out[4] = (uint8_t)(seq & 0xff);
  out[5] = (uint8_t)(len >> 8);
  out[6] = (uint8_t)(len & 0xff);

  if (len > 0 && payload != NULL)
    {
      memcpy(out + WC_HEADER_SIZE, payload, len);
    }

  crc = wc_crc16(out, WC_HEADER_SIZE + (size_t)len);
  out[WC_HEADER_SIZE + len]      = (uint8_t)(crc >> 8);
  out[WC_HEADER_SIZE + len + 1]  = (uint8_t)(crc & 0xff);

  return (int)total;
}

/****************************************************************************
 * 流式解析
 ****************************************************************************/

void wc_parser_init(struct wc_parser_s *p)
{
  p->len = 0;
}

static void parser_drop(struct wc_parser_s *p, size_t n)
{
  if (n >= p->len)
    {
      p->len = 0;
      return;
    }

  memmove(p->buf, p->buf + n, p->len - n);
  p->len -= n;
}

int wc_parser_next(struct wc_parser_s *p, struct wc_frame_s *out)
{
  size_t   need;
  uint16_t len;
  uint16_t crc_rx;
  uint16_t crc_calc;

  while (p->len >= WC_HEADER_SIZE + WC_CRC_SIZE)
    {
      /* 重新同步到帧头 */

      if (p->buf[0] != WC_HEAD0 || p->buf[1] != WC_HEAD1)
        {
          parser_drop(p, 1);
          continue;
        }

      len = (uint16_t)(((uint16_t)p->buf[5] << 8) | p->buf[6]);
      if (len > WC_MAX_PAYLOAD)
        {
          parser_drop(p, 1);
          continue;
        }

      need = WC_HEADER_SIZE + (size_t)len + WC_CRC_SIZE;
      if (p->len < need)
        {
          return 0;                 /* 等更多数据 */
        }

      crc_rx   = (uint16_t)(((uint16_t)p->buf[WC_HEADER_SIZE + len] << 8) |
                            p->buf[WC_HEADER_SIZE + len + 1]);
      crc_calc = wc_crc16(p->buf, WC_HEADER_SIZE + (size_t)len);

      if (crc_rx != crc_calc)
        {
          parser_drop(p, 1);        /* 坏帧：滑动一字节重新找头 */
          continue;
        }

      out->type = p->buf[2];
      out->seq  = (uint16_t)(((uint16_t)p->buf[3] << 8) | p->buf[4]);
      out->len  = len;
      if (len > 0)
        {
          memcpy(out->payload, p->buf + WC_HEADER_SIZE, len);
        }

      parser_drop(p, need);
      return 1;
    }

  return 0;
}

void wc_parser_push(struct wc_parser_s *p, const uint8_t *data, size_t n)
{
  /* 缓冲有界：宁可丢旧数据，也不无限增长 */

  if (n >= sizeof(p->buf))
    {
      data += (n - sizeof(p->buf));
      n = sizeof(p->buf);
      p->len = 0;
    }

  if (p->len + n > sizeof(p->buf))
    {
      parser_drop(p, p->len + n - sizeof(p->buf));
    }

  memcpy(p->buf + p->len, data, n);
  p->len += n;
}

/****************************************************************************
 * 链路
 *
 * 关键设计：**永不无限阻塞**。上位机可能随时停止读取（收工、崩溃、或被
 * 强制重枚举），此时对 USB 口的阻塞写会占死调用者；若紧接着发生总线复位，
 * 整个设备会挂起（实测连控制台都会静默，只能重新烧录恢复）。
 * 因此：非阻塞 fd + 有界重试，超限即丢弃该帧。
 ****************************************************************************/

static int      g_fd = -1;
static uint16_t g_seq = 1;
static struct wc_parser_s g_parser;
static struct wc_frame_s  g_frame_scratch;

int wc_link_open(const char *path)
{
  if (g_fd >= 0)
    {
      wc_link_close();
    }

  g_fd = open(path, O_RDWR | O_NONBLOCK);
  if (g_fd < 0)
    {
      syslog(LOG_WARNING, "WristClaw: open %s failed: %d\n",
             path, errno);
      return -errno;
    }

  wc_parser_init(&g_parser);
  return 0;
}

void wc_link_close(void)
{
  if (g_fd >= 0)
    {
      close(g_fd);
      g_fd = -1;
    }
}

bool wc_link_ready(void)
{
  return g_fd >= 0;
}

uint16_t wc_next_seq(void)
{
  uint16_t s = g_seq++;
  if (g_seq == 0)
    {
      g_seq = 1;
    }

  return s;
}

static int link_write_all(const uint8_t *buf, size_t len)
{
  size_t off  = 0;
  int    spin = 0;

  while (off < len && spin < 12)
    {
      ssize_t n = write(g_fd, buf + off, len - off);

      if (n > 0)
        {
          off += (size_t)n;
          spin = 0;
          continue;
        }

      if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
        {
          struct pollfd pfd;

          pfd.fd      = g_fd;
          pfd.events  = POLLOUT;
          pfd.revents = 0;

          /* 最多等 20ms，然后重试；累计 spin 次仍不行就放弃该帧。
           * 返回 -2 表示"忙丢帧"而非链路错误 —— 主机暂时没读是常态，
           * 不应因此拆除链路（否则 board 会在主机打开/关闭窗口期
           * 里疯狂拆绑重绑）。 */

          (void)poll(&pfd, 1, 20);
          spin++;
          continue;
        }

      return -1;                    /* 真错误（如 ENODEV） */
    }

  return off == len ? (int)len : -2;
}

int wc_link_send(uint8_t type, uint16_t seq, const uint8_t *payload,
                 uint16_t len)
{
  uint8_t frame[WC_MAX_FRAME];
  int     n;

  if (g_fd < 0)
    {
      return -1;
    }

  n = wc_build(frame, sizeof(frame), type, seq, payload, len);
  if (n < 0)
    {
      return -2;
    }

  return link_write_all(frame, (size_t)n);
}

int wc_link_send_text(uint16_t seq, const char *text)
{
  return wc_link_send(WC_T_TEXT, seq, (const uint8_t *)text,
                      (uint16_t)strlen(text));
}

void wc_link_poll(wc_frame_cb_t cb, void *arg)
{
  uint8_t chunk[256];
  ssize_t n;
  int     budget = 16;

  if (g_fd < 0)
    {
      return;
    }

  /* 一次最多读 4KB，避免长时间占用主循环 */

  while (budget-- > 0)
    {
      n = read(g_fd, chunk, sizeof(chunk));
      if (n <= 0)
        {
          break;
        }

      wc_parser_push(&g_parser, chunk, (size_t)n);

      while (wc_parser_next(&g_parser, &g_frame_scratch))
        {
          if (cb)
            {
              cb(&g_frame_scratch, arg);
            }
        }
    }
}
