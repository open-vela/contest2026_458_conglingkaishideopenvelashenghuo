"""
云端中继 — 板端 ↔ PC ↔ MiMo API

支持两种工作模式:
1. USB 模式: 板端通过 USB CDC 直连 PC，PC 脚本做桥接
2. 手机模式: 板端 BLE → 手机 App → HTTPS（未来）

云端 API 抽象层，可切换不同模型后端。
"""

import asyncio
import json
import logging
import time
from typing import Optional, AsyncIterator
from dataclasses import dataclass, field

from openai import AsyncOpenAI

logger = logging.getLogger("wristclaw.cloud")

# ---- 配置 ----

@dataclass
class CloudConfig:
    api_key: str = ""
    api_base: str = "https://api.openai.com/v1"  # 兼容接口，替换为 MiMo 地址
    model: str = "MiMo"  # 大赛指定模型
    tts_model: str = "MiMo-tts"
    asr_model: str = "MiMo-asr"
    mock: bool = False  # 无 API Key 的规则 Mock 模式（离线演示）
    max_tokens: int = 512
    temperature: float = 0.7
    # 系统提示词 — 定义 AI 伙伴人设
    system_prompt: str = (
        "你是「腕灵犀」，一个运行在智能手表上的 AI 语音伙伴。"
        "你性格友好、简洁、高效。由于手表屏幕小，你的回复要简短（1-3句话），"
        "除非用户要求详细解释。你可以：\n"
        "1. 回答问题、闲聊\n"
        "2. 设置提醒（用户说'提醒我X点做Y'，你会返回结构化的提醒指令）\n"
        "3. 记录语音备忘\n"
        "4. 控制设备（亮度、闹钟等）\n"
        "5. 播报天气、日程等信息\n"
        "当需要执行设备操作时，返回 JSON 指令格式。"
    )


# ---- 提醒指令结构化 ----

@dataclass
class ReminderAction:
    event: str
    time: str  # ISO 格式或自然语言时间
    action_type: str = "reminder"

    def to_dict(self):
        return {"action": self.action_type, "event": self.event, "time": self.time}


@dataclass
class DeviceAction:
    command: str  # set_brightness, set_alarm, start_timer, etc.
    params: dict = field(default_factory=dict)
    action_type: str = "device"

    def to_dict(self):
        return {"action": self.action_type, "command": self.command, **self.params}


# ---- 云端中继核心 ----

class CloudRelay:
    def __init__(self, config: Optional[CloudConfig] = None):
        self.config = config or CloudConfig()
        self._client: Optional[AsyncOpenAI] = None
        self._conversation_history: list[dict] = []
        self._max_history = 20  # 保留最近 N 轮对话

    async def initialize(self):
        if self.config.mock or not self.config.api_key:
            logger.info("Cloud relay in MOCK mode (no API key)")
            return
        self._client = AsyncOpenAI(
            api_key=self.config.api_key,
            base_url=self.config.api_base,
        )
        logger.info(f"Cloud relay initialized: model={self.config.model}")

    def _trim_history(self):
        if len(self._conversation_history) > self._max_history * 2:
            # 保留系统提示 + 最近的对话
            self._conversation_history = [
                self._conversation_history[0]
            ] + self._conversation_history[-(self._max_history * 2):]

    async def chat(self, user_text: str) -> dict:
        """
        发送用户文本到云端，返回回复。

        返回格式:
        {
            "text": "AI 回复文本",
            "actions": [{"action": "reminder", ...}, ...],  // 可选的设备操作
            "latency_ms": 1234
        }
        """
        if not self._client:
            await self.initialize()

        if self.config.mock or not self.config.api_key:
            return self._mock_chat(user_text)

        self._conversation_history.append({"role": "user", "content": user_text})
        self._trim_history()

        t0 = time.time()
        try:
            response = await self._client.chat.completions.create(
                model=self.config.model,
                messages=[
                    {"role": "system", "content": self.config.system_prompt},
                    *self._conversation_history,
                ],
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
            )
            reply_text = response.choices[0].message.content.strip()
            self._conversation_history.append({"role": "assistant", "content": reply_text})
            latency_ms = int((time.time() - t0) * 1000)

            # 尝试解析结构化操作
            actions = self._extract_actions(reply_text)

            return {
                "text": reply_text,
                "actions": actions,
                "latency_ms": latency_ms,
                "tokens_used": response.usage.total_tokens if response.usage else 0,
            }
        except Exception as e:
            logger.error(f"Cloud API error: {e}")
            latency_ms = int((time.time() - t0) * 1000)
            return {
                "text": "抱歉，网络连接出现问题，请稍后再试。",
                "actions": [],
                "latency_ms": latency_ms,
                "error": str(e),
            }

    async def transcribe_audio(self, audio_data: bytes, format: str = "wav") -> str:
        """
        音频转文字 (ASR)。

        调用云端 ASR 接口，返回转写文本。
        如果云端不支持，返回空字符串。
        """
        if not self._client:
            await self.initialize()

        if self.config.mock or not self.config.api_key:
            logger.info("mock mode: ASR unavailable, %d bytes dropped",
                        len(audio_data))
            return ""

        try:
            # OpenAI 兼容的 Whisper 接口
            import io
            audio_file = io.BytesIO(audio_data)
            audio_file.name = f"audio.{format}"

            response = await self._client.audio.transcriptions.create(
                model=self.config.asr_model,
                file=audio_file,
                language="zh",
            )
            return response.text
        except Exception as e:
            logger.warning(f"ASR failed (falling back to local): {e}")
            return ""

    async def synthesize_speech(self, text: str) -> Optional[bytes]:
        """
        文字转语音 (TTS)。

        返回音频数据 (PCM/WAV)。
        如果云端不支持，返回 None（板端播放提示音）。
        """
        if not self._client:
            await self.initialize()

        try:
            response = await self._client.audio.speech.create(
                model=self.config.tts_model,
                voice="alloy",
                input=text,
                response_format="pcm",  # 板端直接播放 PCM
            )
            audio_data = await response.content.read()
            return audio_data
        except Exception as e:
            logger.warning(f"TTS failed: {e}")
            return None

    def _mock_chat(self, user_text: str) -> dict:
        """离线 Mock：规则应答，保证无 API Key 也能完整演示端云闭环。"""
        t0 = time.time()
        text = user_text.strip()
        low = text.lower()
        actions: list[dict] = []

        if any(k in text for k in ("提醒", "闹钟", "定时")):
            actions.append({"action": "reminder", "event": text, "time": text})
            reply = "好的，已设置提醒。"
        elif any(k in text for k in ("备忘", "记一下", "记住")):
            actions.append({"action": "memo", "content": text})
            reply = "已记入备忘录。"
        elif any(k in text for k in ("亮度", "调亮", "调暗")):
            value = 80 if ("亮" in text and "调亮" in text) else (
                30 if "暗" in text else 50)
            actions.append({"action": "brightness", "value": value})
            reply = "已调整亮度到 %d%%。" % value
        elif any(k in low for k in ("hello", "hi", "你好", "在吗")):
            reply = "你好！我是腕灵犀，当前处于 Mock 模式（未配置 API Key）。"
        elif "状态" in text or "status" in low:
            reply = "设备在线，链路正常。"
        else:
            reply = "（Mock）收到：%s。配置 --api-key 后可接入大模型。" % text

        return {
            "text": reply,
            "actions": actions,
            "latency_ms": int((time.time() - t0) * 1000),
            "mock": True,
        }

    def _extract_actions(self, text: str) -> list[dict]:
        """从 AI 回复中提取结构化操作指令"""
        actions = []
        # 检查是否包含 JSON 格式的操作指令
        try:
            # AI 可能在回复末尾附加 JSON
            if "```json" in text:
                json_str = text.split("```json")[1].split("```")[0].strip()
                data = json.loads(json_str)
                if isinstance(data, list):
                    actions = [self._parse_action(a) for a in data]
                elif isinstance(data, dict):
                    actions = [self._parse_action(data)]
        except (json.JSONDecodeError, IndexError):
            pass

        # 关键词匹配兜底
        if not actions:
            actions = self._keyword_match_actions(text)

        return [a for a in actions if a is not None]

    def _parse_action(self, data: dict) -> Optional[dict]:
        action_type = data.get("action", "")
        if action_type == "reminder":
            return ReminderAction(
                event=data.get("event", ""),
                time=data.get("time", ""),
            ).to_dict()
        elif action_type == "device":
            return DeviceAction(
                command=data.get("command", ""),
                params={k: v for k, v in data.items() if k not in ("action", "command")},
            ).to_dict()
        return None

    def _keyword_match_actions(self, text: str) -> list[dict]:
        """简单关键词匹配，作为结构化解析的兜底"""
        actions = []
        # 闹钟/提醒关键词
        reminder_keywords = ["提醒", "闹钟", "定时", "提醒我"]
        for kw in reminder_keywords:
            if kw in text:
                # 尝试提取时间和事件
                actions.append({"action": "reminder", "event": text, "time": "parsed_later"})
                break
        return actions

    def clear_history(self):
        self._conversation_history.clear()

    async def health_check(self) -> bool:
        """检查云端 API 是否可用"""
        try:
            response = await self.chat("ping")
            return "error" not in response
        except Exception:
            return False
