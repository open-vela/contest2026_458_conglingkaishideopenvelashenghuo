#!/usr/bin/env python3
"""WristClaw 全局语音唤醒（PC 网关侧，离线 Vosk 常驻监听）

体验与 "Hi Siri" 一致：PC 麦克风 7x24 监听，说出唤醒词即进入对话。
命中唤醒词 -> 提示音 -> 录一句话 -> 离线 ASR -> "USER <text>" 发板端
-> 板端 Agent 路由（本地工具直接执行，复杂意图上行云端）。

唤醒词：**你好，openvela**（大赛统一唤醒词，音近容错匹配）：
  你好 openvela / Hello openvela / open vela / 维拉 / 欧朋维拉 ...

用法：
  python wake.py --mock                 # 离线全链路（无 API Key）
  python wake.py --api-key SK-...       # 真实 MiMo（ASR/TTS/对话）
"""
import argparse
import asyncio
import io
import json
import logging
import os
import queue
import sys
import time
import wave

from wristclaw.protocol import FrameType, build_text_frame
from wristclaw.transport import SerialTransport
from wristclaw.cloud_relay import CloudConfig, CloudRelay

log = logging.getLogger("wake")

WAKE_WORDS = ("openvela", "open vela", "openvela", "维拉", "欧朋维拉", "欧本维拉")
# 大赛强制唤醒词：你好，openvela / Hello，openvela
# Vosk 中文模型对英文词常识别为音近字，这里做音近容错匹配：
#   命中「openvela / open vela / 维拉 / 欧朋维拉」等任一形式即视为唤醒
_HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.environ.get(
    "WC_VOSK_MODEL",
    os.path.join(_HERE, "models", "vosk-model-small-cn-0.22"))
RATE = 16000


def is_wake(partial: str) -> bool:
    """唤醒词判断：赛道统一词「你好，openvela」的音近容错匹配"""
    p = partial.lower().replace(" ", "")
    if not p:
        return False
    for w in ("openvela", "openvel", "openvel", "维拉", "维啦", "欧朋", "欧本"):
        if w in p:
            return True
    return False


def beep(freq=880, ms=150):
    """唤醒提示音"""
    try:
        import numpy as np
        import sounddevice as sd
        t = np.linspace(0, ms / 1000, int(RATE * ms / 1000), False)
        tone = (np.sin(freq * 2 * np.pi * t) * 12000).astype("int16")
        sd.play(tone, RATE)
        sd.wait()
    except Exception:
        pass


def record(secs=5):
    import numpy as np
    import sounddevice as sd
    log.info(">> 请说指令（%ds）...", secs)
    audio = sd.rec(int(secs * RATE), samplerate=RATE, channels=1,
                   dtype="int16")
    sd.wait()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(audio.tobytes())
    return buf.getvalue()


async def amain():
    ap = argparse.ArgumentParser(description="WristClaw voice wake")
    ap.add_argument("--port")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--api-base", default="")
    ap.add_argument("--model", default="")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-5s %(message)s",
                        datefmt="%H:%M:%S")

    # ---- Vosk 离线模型 ----
    from vosk import Model, KaldiRecognizer
    model = Model(MODEL_DIR)
    log.info("Vosk 模型加载完成（离线）")

    # ---- 串口 + 云端 ----
    cfg = CloudConfig()
    cfg.mock = args.mock
    if not args.mock:
        cfg.api_key = args.api_key
        if args.api_base:
            cfg.api_base = args.api_base
        if args.model:
            cfg.model = args.model

    tr = SerialTransport(port=args.port, baudrate=1000000)
    relay = CloudRelay(cfg)
    await relay.initialize()
    if not await tr.connect(port=args.port):
        log.error("未找到板端串口")
        return 1
    log.info("板端已连接：%s", tr.port_name)

    # 后台收帧（SAY 日志）
    async def rx():
        while True:
            await asyncio.sleep(0.2)

    asyncio.ensure_future(rx())

    # ---- 麦克风常驻监听 ----
    import numpy as np
    import sounddevice as sd

    audio_q = queue.Queue()

    def audio_cb(indata, frames, t, status):
        audio_q.put(bytes(indata))

    rec = KaldiRecognizer(model, RATE, json.dumps(["你好 openvela", "openvela", "你好"], ensure_ascii=False))
    stream = sd.RawInputStream(samplerate=RATE, blocksize=8000,
                               dtype="int16", channels=1, callback=audio_cb)
    stream.start()
    log.info("=== 全局唤醒已就绪，请说「你好 openvela」===")
    log.info("（唤醒后说指令；Ctrl+C 退出）")

    listening = False
    listen_start = 0.0
    cmd_buf = b""

    try:
        while True:
            data = audio_q.get()
            if rec.AcceptWaveform(data):
                res = json.loads(rec.Result())
                text = res.get("text", "")
                if listening and text:
                    # 一句指令识别完成
                    log.info("指令: %r", text)
                    listening = False
                    if text:
                        await tr.send(build_text_frame(
                            tr.next_seq(), "USER " + text))
                elif not listening and text:
                    pass
            else:
                partial = json.loads(rec.PartialResult()).get("partial", "")

                if not listening:
                    if is_wake(partial):
                        listening = True
                        listen_start = time.time()
                        log.info(">>> 唤醒命中（%s）", partial)
                        beep()
                        # 新开一个识别器专听指令
                        rec = KaldiRecognizer(model, RATE)
                    continue

                # 聆听指令中：静默 2.5s 或 8s 超时则收句
                if partial:
                    cmd_buf = partial.encode("utf-8")
                # 简化：partial 稳定 2s 或超 8s 就收
                if time.time() - listen_start > 8:
                    text = partial.strip()
                    listening = False
                    rec = KaldiRecognizer(
                        model, RATE,
                        json.dumps(["你好 openvela", "openvela", "你好"],
                                   ensure_ascii=False))
                    if text:
                        log.info("指令: %r", text)
                        await tr.send(build_text_frame(
                            tr.next_seq(), "USER " + text))
                    else:
                        log.info("未听到指令")
    except KeyboardInterrupt:
        pass
    finally:
        stream.stop()
        stream.close()
        await tr.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()) or 0)
