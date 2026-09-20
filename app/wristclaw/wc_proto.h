/****************************************************************************
 * app/wristclaw/wc_proto.h
 *
 * WristClaw 板端 ↔ PC 上位机 帧协议（板端实现）
 *
 * 与 host/wristclaw/protocol.py 逐字节兼容：
 *
 *   [HEAD(2)] [TYPE(1)] [SEQ(2,BE)] [LEN(2,BE)] [PAYLOAD(N)] [CRC16(2,BE)]
 *
 *   HEAD  = 0xAA 0x55
 *   CRC16 = CCITT (init 0xFFFF, poly 0x1021)，覆盖 HEAD..PAYLOAD
 *
 * 协议层不负责阻塞：所有发送都经 wc_link_write()，它有界重试后丢弃，
 * 绝不允许在 USB 口上无限等待 —— 主机一旦停读，阻塞写会占死调用者，
 * 而此刻若发生总线复位（例如上位机强制重枚举），设备会整体挂起。
 ****************************************************************************/

#ifndef __APP_WRISTCLAW_WC_PROTO_H
#define __APP_WRISTCLAW_WC_PROTO_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

/* 帧类型（数值必须与 protocol.py 的 FrameType 一致） */

#define WC_T_TEXT          0x01
#define WC_T_AUDIO_DATA    0x02
#define WC_T_AUDIO_CMD     0x03
#define WC_T_CLOUD_REQ     0x04
#define WC_T_CLOUD_RESP    0x05
#define WC_T_DEVICE_CMD    0x06
#define WC_T_DEVICE_STATUS 0x07
#define WC_T_WAKE_EVENT    0x08
#define WC_T_ACK           0x09
#define WC_T_HEARTBEAT     0x0A
#define WC_T_SENSOR_DATA   0x11
#define WC_T_REMINDER      0x12

/* 设备命令（与 DeviceCmd 一致） */

#define WC_CMD_REBOOT        0x01
#define WC_CMD_QUERY_STATUS  0x02
#define WC_CMD_QUERY_VERSION 0x03
#define WC_CMD_SET_VOLUME    0x20
#define WC_CMD_SET_BRIGHTNESS 0x21

#define WC_HEAD0        0xAA
#define WC_HEAD1        0x55
#define WC_HEADER_SIZE  7
#define WC_CRC_SIZE     2

/* 单帧上限：足够放一条 JSON 请求/响应，同时把解析缓冲控制在 4KB 内 */

#define WC_MAX_PAYLOAD  2048
#define WC_MAX_FRAME    (WC_HEADER_SIZE + WC_MAX_PAYLOAD + WC_CRC_SIZE)

struct wc_frame_s
{
  uint8_t  type;
  uint16_t seq;
  uint16_t len;
  uint8_t  payload[WC_MAX_PAYLOAD];
};

struct wc_parser_s
{
  uint8_t buf[WC_MAX_FRAME];
  size_t  len;
};

uint16_t wc_crc16(const uint8_t *data, size_t len);

/* 组装一帧，返回总字节数；outsz 不足返回 -1 */

int wc_build(uint8_t *out, size_t outsz, uint8_t type, uint16_t seq,
             const uint8_t *payload, uint16_t len);

void wc_parser_init(struct wc_parser_s *p);

/* 两段式：先把字节灌进缓冲（内部做重同步与越界丢弃），再循环取帧。
 *
 *   wc_parser_push(&p, data, n);
 *   while (wc_parser_next(&p, &f)) { ... }
 *
 * 坏帧/噪声在 push/next 内部被丢弃并自动重新同步，调用方不需要关心。
 */

void wc_parser_push(struct wc_parser_s *p, const uint8_t *data, size_t n);

/* 取出一帧。返回 1 表示 out 有效，0 表示缓冲里暂时没有完整帧。 */

int wc_parser_next(struct wc_parser_s *p, struct wc_frame_s *out);

/* ---- 链路：串口句柄 + 有界写入 ---- */

int  wc_link_open(const char *path);
void wc_link_close(void);
bool wc_link_ready(void);

/* 发送一帧。内部有界重试，失败即丢弃并返回负值；永不无限阻塞。 */

int  wc_link_send(uint8_t type, uint16_t seq,
                  const uint8_t *payload, uint16_t len);
int  wc_link_send_text(uint16_t seq, const char *text);

/* 非阻塞取流：把可读字节灌进解析器，回调每个完整帧。
 * 由主循环周期性调用。
 */

typedef void (*wc_frame_cb_t)(const struct wc_frame_s *f, void *arg);
void wc_link_poll(wc_frame_cb_t cb, void *arg);

uint16_t wc_next_seq(void);

#endif /* __APP_WRISTCLAW_WC_PROTO_H */
