"""
音频桥接 — PC 端音频采集与播放

通过 USB CDC 从板端接收录音数据，发送 TTS 音频到板端。
支持 PCM 和 Opus 格式。
"""

import asyncio
import struct
import logging
import time
from typing import Optional
from enum import IntEnum

logger = logging.getLogger("wristclaw.audio")

# 音频参数
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_CHANNELS = 1
DEFAULT_SAMPLE_WIDTH = 2  # 16-bit


class AudioFormat(IntEnum):
    PCM = 0x00
    OPUS = 0x10


class AudioBridge:
    """
    音频桥接器

    负责:
    1. 从板端接收录音帧 → 拼接为完整音频
    2. 将 TTS 音频分帧 → 发送到板端播放
    3. 本地音频录制（PC 麦克风 → 发送到板端）
    4. 本地音频播放（板端音频 → PC 扬声器）
    """

    def __init__(self, transport):
        """
        Args:
            transport: SerialTransport 实例
        """
        self._transport = transport
        self._recording = False
        self._playback_buffer: bytearray = bytearray()
        self._recording_buffer: bytearray = bytearray()
        self._on_audio_complete: Optional[callable] = None
        self._frame_size = 640  # 20ms @ 16kHz 16bit mono = 640 bytes

        # PyAudio（可选，PC 端播放）
        self._pyaudio = None
        self._audio_stream = None

    def init_pyaudio(self):
        """初始化 PC 端音频播放（可选）"""
        try:
            import pyaudio
            self._pyaudio = pyaudio.PyAudio()
            self._audio_stream = self._pyaudio.open(
                format=pyaudio.paInt16,
                channels=DEFAULT_CHANNELS,
                rate=DEFAULT_SAMPLE_RATE,
                output=True,
                frames_per_buffer=self._frame_size // 2,
            )
            logger.info("PyAudio initialized for local playback")
        except ImportError:
            logger.warning("PyAudio not installed, local playback disabled")
        except Exception as e:
            logger.warning(f"PyAudio init failed: {e}")

    def close(self):
        if self._audio_stream:
            self._audio_stream.close()
        if self._pyaudio:
            self._pyaudio.terminate()

    # ---- 录音：板端 → PC ----

    async def start_recording(self):
        """通知板端开始录音"""
        from .protocol import FrameType, AudioCmd
        self._recording = True
        self._recording_buffer.clear()
        # 发送 AUDIO_CMD: START_RECORD
        await self._transport.send(
            struct.pack('>BB', FrameType.AUDIO_CMD, AudioCmd.START_RECORD)
        )
        logger.info("Recording started")

    async def stop_recording(self) -> bytes:
        """通知板端停止录音，返回录制的音频数据"""
        from .protocol import FrameType, AudioCmd
        self._recording = False
        await self._transport.send(
            struct.pack('>BB', FrameType.AUDIO_CMD, AudioCmd.STOP_RECORD)
        )
        logger.info(f"Recording stopped, {len(self._recording_buffer)} bytes captured")
        return bytes(self._recording_buffer)

    def handle_audio_frame(self, data: bytes):
        """处理来自板端的音频数据帧"""
        if self._recording:
            self._recording_buffer.extend(data)

        # 如果正在播放，推送到 PyAudio
        if self._audio_stream and not self._recording:
            try:
                self._audio_stream.write(data)
            except Exception as e:
                logger.error(f"Audio playback error: {e}")

    def save_recording_wav(self, audio_data: bytes, filepath: str):
        """将 PCM 音频保存为 WAV 文件"""
        import wave
        with wave.open(filepath, 'wb') as wf:
            wf.setnchannels(DEFAULT_CHANNELS)
            wf.setsampwidth(DEFAULT_SAMPLE_WIDTH)
            wf.setframerate(DEFAULT_SAMPLE_RATE)
            wf.writeframes(audio_data)
        logger.info(f"Recording saved to {filepath}")

    # ---- 播放：PC → 板端 ----

    async def send_tts_to_board(self, pcm_data: bytes):
        """
        将 PCM 音频数据分帧发送到板端播放。

        每帧大小: 640 bytes (20ms @ 16kHz 16bit mono)
        """
        from .protocol import FrameType, AudioCmd

        # 先发送播放命令
        await self._transport.send(
            struct.pack('>BB', FrameType.AUDIO_CMD, AudioCmd.START_PLAY)
        )

        # 分帧发送
        offset = 0
        while offset < len(pcm_data):
            chunk = pcm_data[offset:offset + self._frame_size]
            await self._transport.send(
                struct.pack('>BB', FrameType.AUDIO_DATA, 0) + chunk
            )
            offset += self._frame_size
            # 控制发送速率，避免溢出
            await asyncio.sleep(0.018)  # 略小于 20ms

        # 发送停止命令
        await self._transport.send(
            struct.pack('>BB', FrameType.AUDIO_CMD, AudioCmd.STOP_PLAY)
        )
        logger.info(f"TTS sent to board: {len(pcm_data)} bytes")

    async def record_from_pc_mic(self, duration_sec: float = 5.0) -> Optional[bytes]:
        """
        从 PC 麦克风录制音频。

        返回 PCM 数据，用于发送到板端或云端 ASR。
        """
        try:
            import pyaudio
            pa = pyaudio.PyAudio()
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=DEFAULT_CHANNELS,
                rate=DEFAULT_SAMPLE_RATE,
                input=True,
                frames_per_buffer=self._frame_size // 2,
            )

            frames = []
            num_chunks = int(duration_sec * DEFAULT_SAMPLE_RATE / (self._frame_size // 2))
            for _ in range(num_chunks):
                data = stream.read(self._frame_size // 2)
                frames.append(data)

            stream.stop_stream()
            stream.close()
            pa.terminate()

            return b''.join(frames)
        except Exception as e:
            logger.error(f"PC mic recording failed: {e}")
            return None

    # ---- 音频处理工具 ----

    @staticmethod
    def pcm_to_wav(pcm_data: bytes, sample_rate: int = 16000,
                   channels: int = 1, sample_width: int = 2) -> bytes:
        """PCM → WAV"""
        import wave
        import io
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(sample_width)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_data)
        return buf.getvalue()

    @staticmethod
    def wav_to_pcm(wav_data: bytes) -> tuple[bytes, dict]:
        """WAV → PCM + 元数据"""
        import wave
        import io
        with wave.open(io.BytesIO(wav_data), 'rb') as wf:
            pcm = wf.readframes(wf.getnframes())
            meta = {
                "channels": wf.getnchannels(),
                "sample_width": wf.getsampwidth(),
                "sample_rate": wf.getframerate(),
            }
        return pcm, meta
