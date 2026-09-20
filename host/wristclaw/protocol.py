"""
板端 ↔ PC 帧协议

帧格式:
  [HEAD(2)] [TYPE(1)] [SEQ(2)] [LEN(2)] [PAYLOAD(N)] [CRC16(2)]

HEAD  = 0xAA 0x55
TYPE  = 帧类型 (见下方常量)
SEQ   = 序号 (用于 ACK/重传)
LEN   = payload 长度
CRC16 = CCITT 校验

帧类型:
  0x01 TEXT        文本数据 (对话文字、日志)
  0x02 AUDIO_DATA  音频帧 (Opus/PCM)
  0x03 AUDIO_CMD   音频控制 (开始/停止录音, 播放)
  0x04 CLOUD_REQ   云端请求 (板端→PC)
  0x05 CLOUD_RESP  云端响应 (PC→板端)
  0x06 DEVICE_CMD  设备命令 (重启、OTA、状态查询)
  0x07 DEVICE_STATUS 设备状态上报
  0x08 WAKE_EVENT  唤醒事件
  0x09 ACK         确认帧
  0x0A HEARTBEAT   心跳
  0x11 SENSOR_DATA 传感器数据（本板无生物传感器，本期未使用）
  0x12 REMINDER    提醒事件（板端主动推送）
"""

import struct
from enum import IntEnum
from dataclasses import dataclass
from typing import Optional

HEAD = b'\xAA\x55'
HEAD_SIZE = 2
HEADER_SIZE = 7  # HEAD(2) + TYPE(1) + SEQ(2) + LEN(2)
CRC_SIZE = 2
MIN_FRAME_SIZE = HEADER_SIZE + CRC_SIZE


class FrameType(IntEnum):
    TEXT = 0x01
    AUDIO_DATA = 0x02
    AUDIO_CMD = 0x03
    CLOUD_REQ = 0x04
    CLOUD_RESP = 0x05
    DEVICE_CMD = 0x06
    DEVICE_STATUS = 0x07
    WAKE_EVENT = 0x08
    ACK = 0x09
    HEARTBEAT = 0x0A
    SENSOR_DATA = 0x11
    REMINDER = 0x12


class AudioCmd(IntEnum):
    START_RECORD = 0x01
    STOP_RECORD = 0x02
    START_PLAY = 0x03
    STOP_PLAY = 0x04
    SAMPLE_RATE_16K = 0x10
    SAMPLE_RATE_8K = 0x11
    CODEC_OPUS = 0x20
    CODEC_PCM = 0x21


class DeviceCmd(IntEnum):
    REBOOT = 0x01
    QUERY_STATUS = 0x02
    QUERY_VERSION = 0x03
    ENTER_OTA = 0x10
    SET_VOLUME = 0x20
    SET_BRIGHTNESS = 0x21


@dataclass
class Frame:
    type: int
    seq: int
    payload: bytes

    @property
    def type_name(self) -> str:
        try:
            return FrameType(self.type).name
        except ValueError:
            return f"0x{self.type:02X}"


def _crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return crc


def build_frame(frame_type: int, seq: int, payload: bytes = b'') -> bytes:
    header = HEAD + struct.pack('>BHH', frame_type, seq, len(payload))
    crc = _crc16_ccitt(header + payload)
    return header + payload + struct.pack('>H', crc)


class FrameParser:
    """流式帧解析器，处理分包/粘包"""

    def __init__(self):
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[Frame]:
        self._buf.extend(data)
        frames = []
        while True:
            frame, consumed = self._try_parse()
            if frame is None:
                break
            frames.append(frame)
            del self._buf[:consumed]
        return frames

    def _try_parse(self) -> tuple[Optional[Frame], int]:
        buf = self._buf
        if len(buf) < MIN_FRAME_SIZE:
            return None, 0

        # 找帧头
        while len(buf) >= MIN_FRAME_SIZE:
            if buf[0] == 0xAA and buf[1] == 0x55:
                break
            # 跳过乱码
            del buf[0]

        if len(buf) < MIN_FRAME_SIZE:
            return None, 0

        frame_type = buf[2]
        seq = struct.unpack('>H', buf[3:5])[0]
        payload_len = struct.unpack('>H', buf[5:7])[0]
        total_size = HEADER_SIZE + payload_len + CRC_SIZE

        if len(buf) < total_size:
            return None, 0

        payload = bytes(buf[7:7 + payload_len])
        recv_crc = struct.unpack('>H', buf[7 + payload_len:9 + payload_len])[0]
        calc_crc = _crc16_ccitt(bytes(buf[:7 + payload_len]))

        if recv_crc != calc_crc:
            del buf[0]
            return None, 0

        return Frame(type=frame_type, seq=seq, payload=payload), total_size

    def reset(self):
        self._buf.clear()


def build_text_frame(seq: int, text: str) -> bytes:
    return build_frame(FrameType.TEXT, seq, text.encode('utf-8'))


def build_audio_frame(seq: int, audio_data: bytes) -> bytes:
    return build_frame(FrameType.AUDIO_DATA, seq, audio_data)


def build_audio_cmd_frame(seq: int, cmd: int) -> bytes:
    return build_frame(FrameType.AUDIO_CMD, seq, bytes([cmd]))


def build_cloud_req_frame(seq: int, json_bytes: bytes) -> bytes:
    return build_frame(FrameType.CLOUD_REQ, seq, json_bytes)


def build_ack_frame(seq: int) -> bytes:
    return build_frame(FrameType.ACK, seq)


def build_device_cmd_frame(seq: int, cmd: int, arg: int = 0) -> bytes:
    return build_frame(FrameType.DEVICE_CMD, seq, bytes([cmd, arg]))
