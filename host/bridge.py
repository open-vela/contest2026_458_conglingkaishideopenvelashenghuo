#!/usr/bin/env python3
"""WristClaw PC 桥接 — 板端 <-> PC <-> 云端 (MiMo / Mock)

数据面：SerialTransport 收发帧。板端 Agent 的 CLOUD_REQ 转给 CloudRelay，
云端结果翻译成板端行协议命令（SAY / REMIND / MEMO / BRIGHT）回板；
板端本地工具（提醒/备忘/亮度/状态）不经过云端，断链可用。

板端行协议（见 app/wristclaw/wc_agent.c wc_agent_handle_cloud_reply）：
  SAY <文本>          显示/播报
  REMIND <秒> <文本>  定时提醒
  MEMO <文本>         备忘
  BRIGHT <0-100>      亮度
  STATUS / SKILLS     状态查询

用法：
  python bridge.py                     # 自动找板 (VID 38F4 PID A4A7)
  python bridge.py --port COM10
  python bridge.py --mock              # 无 API Key，规则 Mock 演示
  python bridge.py --api-key SK-... --api-base https://xxx/v1 --model MiMo
"""
import argparse
import asyncio
import io
import logging
import re
import signal
import sys
import time
import wave
from datetime import datetime, timedelta

from wristclaw.protocol import FrameType, build_frame, build_text_frame
from wristclaw.transport import SerialTransport
from wristclaw.cloud_relay import CloudConfig, CloudRelay

log = logging.getLogger("bridge")


def _seconds_from(t: str) -> int:
    """把自然语言时间折算成秒。支持 N秒/N分钟/N小时/H小时M分钟后/HH:MM/纯数字。"""
    t = (t or "").strip()
    if not t:
        return 60
    m = re.fullmatch(r"(\d+)\s*秒", t)
    if m:
        return int(m.group(1))
    m = re.fullmatch(r"(\d+)\s*分钟", t)
    if m:
        return int(m.group(1)) * 60
    m = re.fullmatch(r"(\d+)\s*小时(?:\s*(\d+)\s*分钟?)?", t)
    if m:
        return int(m.group(1)) * 3600 + (int(m.group(2) or 0) * 60)
    m = re.search(r"(\d{1,2})[点:：时](\d{1,2})?分?", t)
    if m:
        now = datetime.now()
        tgt = now.replace(hour=int(m.group(1)) % 24,
                          minute=int(m.group(2) or 0),
                          second=0, microsecond=0)
        if tgt <= now:
            tgt += timedelta(days=1)
        return int((tgt - now).total_seconds())
    if t.isdigit():
        return int(t)
    return 60


class Bridge:
    def __init__(self, transport: SerialTransport, relay: CloudRelay,
                 voice=False):
        self.tr = transport
        self.relay = relay
        self.voice = voice
        self._time_task = None
        self.tr.set_on_frame(self._on_frame)

    async def start(self):
        await self.relay.initialize()
        ok = await self.relay.health_check()
        log.info("cloud health: %s", "OK" if ok else "DOWN (mock fallback)")
        await self._install_skills()

        # 板端没有 RTC 备份电池，time() 上电即不可信：链路建立后必须由
        # 上位机把时间给它，否则表盘只能显示 --:--（不敢显示 1970 年）。
        await self._sync_time()
        self._time_task = asyncio.create_task(self._time_loop())

    @staticmethod
    def _local_tz_minutes() -> int:
        """本机相对 UTC 的分钟偏移（东八区 = +480）。"""
        off = -(time.altzone if time.daylight and time.localtime().tm_isdst
                else time.timezone)
        return int(off // 60)

    async def _sync_time(self):
        """行协议 TIME <epoch 秒> <时区分钟>：板端据此设系统时间。"""
        cmd = "TIME %d %d" % (int(time.time()), self._local_tz_minutes())
        await self.tr.send(build_frame(FrameType.CLOUD_RESP,
                                       self.tr.next_seq(),
                                       cmd.encode("utf-8")))
        log.info(">> TIME      %s", cmd)

    async def _time_loop(self):
        """每 60 秒重发校时，抵消板端晶振漂移。"""
        while True:
            await asyncio.sleep(60)
            try:
                await self._sync_time()
            except asyncio.CancelledError:
                return
            except Exception:
                log.debug("time resync failed", exc_info=True)

    async def _install_skills(self):
        """把 host/skills/*.md 推给板端热安装（满足「至少 1 个自定义 Skill」）。

        板端行协议：SKILL name|triggers|tool|prompt
        """
        import glob
        import os
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")
        n = 0
        for path in sorted(glob.glob(os.path.join(d, "*.md"))):
            fields = {}
            try:
                with open(path, encoding="utf-8") as fh:
                    for ln in fh:
                        for k in ("name", "triggers", "tool", "prompt"):
                            if ln.startswith(k + ":"):
                                fields[k] = ln.split(":", 1)[1].strip()
            except OSError:
                continue
            if "name" not in fields or "triggers" not in fields:
                continue
            cmd = "SKILL %s|%s|%s|%s" % (
                fields["name"], fields["triggers"],
                fields.get("tool", "cloud"), fields.get("prompt", ""))
            await self.tr.send(build_frame(
                FrameType.CLOUD_RESP, self.tr.next_seq(),
                cmd.encode("utf-8")))
            log.info(">> SKILL     %s", fields["name"])
            n += 1
            await asyncio.sleep(0.4)
        if n:
            log.info("已推送 %d 个 Skill 到板端", n)

    async def _reply(self, seq: int, result: dict):
        """把云端结果翻译成板端行协议（可拆多帧，板端逐条执行）。"""
        cmds = []
        text = str(result.get("text", "")).strip()
        if text:
            cmds.append("SAY " + text)
        for a in result.get("actions", []) or []:
            kind = a.get("action", "")
            if kind == "reminder":
                secs = _seconds_from(str(a.get("time", "")))
                cmds.append("REMIND %d %s" % (secs, a.get("event", "(提醒)")))
            elif kind in ("memo", "note"):
                cmds.append("MEMO " + str(a.get("content", a.get("text", ""))))
            elif kind in ("brightness", "device"):
                v = a.get("value", a.get("brightness", 50))
                try:
                    cmds.append("BRIGHT %d" % max(0, min(100, int(v))))
                except (TypeError, ValueError):
                    pass
        if not cmds:
            cmds.append("SAY （云端没有可执行的结果）")
        for c in cmds:
            await self.tr.send(build_frame(
                FrameType.CLOUD_RESP, seq, c.encode("utf-8")))
            log.info(">> CLOUD_RESP %r", c)

    async def _on_frame(self, f):
        t, s, p = f.type, f.seq, f.payload
        try:
            if t == FrameType.CLOUD_REQ:
                text = p.decode("utf-8", "replace")
                log.info("<< CLOUD_REQ  seq=%d %r", s, text)
                await self._reply(s, await self.relay.chat(text))
            elif t == FrameType.TEXT:
                text = p.decode("utf-8", "replace")
                log.info("<< TEXT       seq=%d %r", s, text)
                await self._reply(s, await self.relay.chat(text))
            elif t == FrameType.HEARTBEAT:
                await self.tr.send(build_frame(FrameType.ACK, s))
            elif t == FrameType.DEVICE_STATUS:
                log.info("<< STATUS     seq=%d %s", s,
                         p.decode("utf-8", "replace"))
            elif t == FrameType.WAKE_EVENT:
                log.info("<< WAKE        seq=%d", s)
            elif t == FrameType.AUDIO_DATA:
                log.info("<< AUDIO      seq=%d %d bytes", s, len(p))
                text = await self.relay.transcribe_audio(p)
                await self._reply(s, await self.relay.chat(text))
            elif t == FrameType.ACK:
                pass
            else:
                log.debug("<< type=0x%02x seq=%d len=%d", t, s, len(p))
        except Exception:
            log.exception("handler failed for type=0x%02x seq=%d", t, s)

    async def console(self):
        """控制台输入 = 语音替代：文本发板端 Agent 路由（本地优先，云端兜底）。

        --voice 模式下输入非空行 = 录音指令：
            直接回车       退出
            数字 N         说 N 秒话（默认 4 秒），录完自动 ASR -> 板端
        """
        loop = asyncio.get_event_loop()
        print("输入文本模拟语音（直接回车退出）；--voice 模式输入数字=录音秒数")
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            line = line.strip()
            if not line:
                break

            if self.voice and line.isdigit():
                secs = min(15, max(1, int(line)))
                text = await loop.run_in_executor(
                    None, self._record_and_transcribe, secs)
                if not text:
                    log.warning("ASR 无结果（mock 模式不支持语音，改用文本）")
                    continue
                log.info("ASR -> %r", text)
                line = text

            # "USER " 前缀 = 模拟语音输入，板端走意图路由（本地优先，云端兜底）
            await self.tr.send(build_text_frame(self.tr.next_seq(),
                                                "USER " + line))
            log.info(">> USER      %r", line)

    # ---- 语音采集（--voice）----

    voice = False

    def _record_and_transcribe(self, secs: int) -> str:
        """录 secs 秒 16k 单声道 -> WAV bytes -> 云端 ASR -> 文本"""
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError:
            log.error("需要 pip install sounddevice numpy")
            return ""

        rate = 16000
        log.info(">> 录音 %ds（请说话）...", secs)
        audio = sd.rec(int(secs * rate), samplerate=rate,
                       channels=1, dtype="int16")
        sd.wait()
        log.info("   录音结束")

        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(audio.tobytes())

        loop = asyncio.get_event_loop()
        return loop.run_until_complete(
            self.relay.transcribe_audio(buf.getvalue(), "wav"))


async def amain():
    ap = argparse.ArgumentParser(description="WristClaw PC bridge")
    ap.add_argument("--port")
    ap.add_argument("--baud", type=int, default=1000000)
    ap.add_argument("--mock", action="store_true",
                    help="无 API Key 的规则 Mock 模式")
    ap.add_argument("--voice", action="store_true",
                    help="开启麦克风语音输入（需 API Key 做 ASR）")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--api-base", default="")
    ap.add_argument("--model", default="")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S")

    cfg = CloudConfig()
    cfg.mock = args.mock
    if not args.mock:
        cfg.api_key = args.api_key
        if args.api_base:
            cfg.api_base = args.api_base
        if args.model:
            cfg.model = args.model

    tr = SerialTransport(port=args.port, baudrate=args.baud)
    relay = CloudRelay(cfg)
    b = Bridge(tr, relay, voice=args.voice)
    ok = await tr.connect(port=args.port)
    if not ok:
        log.error("未找到板端串口 (VID_38F4 PID_A4A7)，用 --port 指定")
        return 1
    await b.start()

    stop = asyncio.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    con = asyncio.create_task(b.console())
    await stop.wait()
    con.cancel()
    if b._time_task is not None:
        b._time_task.cancel()
    await tr.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()) or 0)
