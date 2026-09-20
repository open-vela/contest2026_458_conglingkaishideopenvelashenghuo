#!/usr/bin/env python3
"""腕灵犀 · 上位机（单程序 GUI）

一个窗口搞定演示需要的一切：
  * 自动发现板端 CDC 串口（VID 38F4）并连接 / 断开
  * 连接后立刻校时（板端无 RTC 备份电池，不校时表盘只能显示 --:--）
    并推送 skills/*.md 到板端热安装
  * 文本输入 = 语音替代，走板端完整意图路由（本地优先、云端兜底）
  * 「语音唤醒」开关：离线 Vosk 常驻监听「你好 openvela」→ 提示音 →
    录音 → ASR → 下发板端（不需要 VPN / API Key）
  * 云端可选：填了 API Key 就用真云端，否则用本地 Mock 应答

依赖：pyserial（必需）；vosk / numpy / sounddevice（仅语音唤醒需要）
运行：双击同目录的「腕灵犀上位机.bat」，或
      .venv\\Scripts\\python.exe app.py
"""

import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from wristclaw.protocol import FrameType, build_frame, build_text_frame, FrameParser  # noqa: E402

SKILL_DIR = os.path.join(HERE, "skills")
VID_TAG = "38F4"
BAUD = 1000000

BG = "#12181c"
FG = "#e6f0f2"
ACCENT = "#3fd0c9"
DIM = "#8b9aa0"
WARN = "#ffb454"


def find_port():
    from serial.tools import list_ports
    for p in list_ports.comports():
        if VID_TAG in (p.hwid or "").upper():
            return p.device
    return None


def tz_minutes():
    off = -(time.altzone if time.daylight and time.localtime().tm_isdst
            else time.timezone)
    return int(off // 60)


class Link:
    """CDC 链路：常驻收帧线程 + 有界写入。"""

    def __init__(self, port, events):
        import serial

        self.ser = serial.Serial(port, BAUD, timeout=0.05, write_timeout=2.0,
                                 rtscts=False, dsrdtr=False)
        self.ser.rts = False
        self.ser.dtr = True          # CDC-ACM 必须 DTR 置位才会收发
        self.parser = FrameParser()
        self.seq = 1
        self.events = events
        self.stop = threading.Event()
        self.last_rx = time.time()
        threading.Thread(target=self._rx, daemon=True).start()

    def next_seq(self):
        s = self.seq
        self.seq = (self.seq % 60000) + 1
        return s

    def _rx(self):
        while not self.stop.is_set():
            try:
                d = self.ser.read(8192)
            except Exception as e:
                self.events.put(("closed", str(e)))
                return
            if not d:
                continue
            self.last_rx = time.time()
            for f in self.parser.feed(d):
                self.events.put(("frame", f))

    def send_line(self, line):
        """行协议（SAY/REMIND/MEMO/SKILL/TIME…）走 CLOUD_RESP 帧。"""
        self.ser.write(build_frame(FrameType.CLOUD_RESP, self.next_seq(),
                                   line.encode("utf-8")))

    def send_user(self, text):
        self.ser.write(build_text_frame(self.next_seq(), "USER " + text))

    def close(self):
        self.stop.set()
        try:
            self.ser.close()
        except Exception:
            pass


class Voice:
    """离线唤醒：Vosk 常驻监听 → 录音 → ASR → 交给回调下发。"""

    def __init__(self, on_text, on_log):
        self.on_text = on_text
        self.on_log = on_log
        self.stop = threading.Event()
        self.thread = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        try:
            import json
            import sounddevice as sd
            from vosk import KaldiRecognizer, Model
            from wake import MODEL_DIR, RATE, is_wake, beep, record
        except Exception as e:
            self.on_log("语音模块不可用: %s" % e)
            return

        if not os.path.isdir(MODEL_DIR):
            self.on_log("缺少离线模型目录: %s" % MODEL_DIR)
            return

        self.on_log("正在加载离线语音模型…")
        model = Model(MODEL_DIR)
        rec = KaldiRecognizer(model, RATE)
        self.on_log("已就绪：说「你好 openvela」唤醒（Ctrl 关闭开关即停）")

        try:
            with sd.RawInputStream(samplerate=RATE, blocksize=4000,
                                   dtype="int16", channels=1) as stream:
                while not self.stop.is_set():
                    data, _ = stream.read(2000)
                    if rec.AcceptWaveform(bytes(data)):
                        partial = json.loads(rec.Result()).get("text", "")
                    else:
                        partial = json.loads(rec.PartialResult()).get("partial", "")
                    if partial and is_wake(partial):
                        self.on_log("检测到唤醒词：%s" % partial)
                        beep()
                        rec.Reset()
                        wav = record(4)
                        text = self._asr(model, wav)
                        if text:
                            self.on_log("识别：%s" % text)
                            self.on_text(text)
                        else:
                            self.on_log("没听清，再说一次")
                        rec = KaldiRecognizer(model, RATE)
        except Exception as e:
            self.on_log("语音线程退出: %s" % e)

    @staticmethod
    def _asr(model, wav_bytes):
        import json
        from vosk import KaldiRecognizer
        from wake import RATE

        rec = KaldiRecognizer(model, RATE)
        rec.AcceptWaveform(wav_bytes)
        return json.loads(rec.FinalResult()).get("text", "").replace(" ", "")

    def stop_now(self):
        self.stop.set()


class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.link = None
        self.voice = Voice(self._on_voice_text, self.log)
        self.api_key = ""
        self._build_ui()
        self.root.after(80, self._pump)

    # ---------------------------------------------------------------- UI
    def _build_ui(self):
        self.root.title("腕灵犀 WristClaw · 上位机")
        self.root.geometry("760x620")
        self.root.configure(bg=BG)

        pad = dict(padx=10, pady=6)

        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x", **pad)

        tk.Label(top, text="串口", bg=BG, fg=DIM).pack(side="left")
        self.lbl_port = tk.Label(top, text="(未连接)", bg=BG, fg=FG)
        self.lbl_port.pack(side="left", padx=6)

        self.btn_conn = ttk.Button(top, text="连接", command=self._toggle_conn)
        self.btn_conn.pack(side="left", padx=6)

        self.var_voice = tk.BooleanVar(value=False)
        tk.Checkbutton(top, text="语音唤醒（你好 openvela）", variable=self.var_voice,
                       command=self._toggle_voice, bg=BG, fg=FG,
                       selectcolor=BG, activebackground=BG,
                       activeforeground=ACCENT).pack(side="left", padx=10)

        self.lbl_state = tk.Label(top, text="● 未连接", bg=BG, fg=DIM)
        self.lbl_state.pack(side="right")

        mid = tk.Frame(self.root, bg=BG)
        mid.pack(fill="both", expand=True, **pad)
        self.txt = scrolledtext.ScrolledText(mid, wrap="word", bg="#0c1114",
                                             fg=FG, insertbackground=FG,
                                             relief="flat", font=("Consolas", 10))
        self.txt.pack(fill="both", expand=True)
        self.txt.configure(state="disabled")

        bot = tk.Frame(self.root, bg=BG)
        bot.pack(fill="x", **pad)
        self.entry = tk.Entry(bot, bg="#0c1114", fg=FG, insertbackground=FG,
                              relief="flat", font=("Consolas", 11))
        self.entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.entry.bind("<Return>", lambda _: self._send_entry())
        ttk.Button(bot, text="发送", command=self._send_entry).pack(side="left", padx=6)
        ttk.Button(bot, text="清空", command=self._clear).pack(side="left")

        self.log("提示：先点「连接」再启动板端 app（板端 open() 需要主机先持有端口）。")
        self.log("文本输入即模拟语音，例如：提醒我 1 分钟后喝水 / 记一下 明天带水杯")

    def log(self, msg, color=None):
        self.txt.configure(state="normal")
        self.txt.insert("end", time.strftime("[%H:%M:%S] ") + msg + "\n")
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def say_bubble(self, who, text):
        self.txt.configure(state="normal")
        self.txt.insert("end", "%s %s\n" % (who, text), ("who",))
        self.txt.tag_config("who", foreground=ACCENT)
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _clear(self):
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")

    # ------------------------------------------------------------ 连接
    def _toggle_conn(self):
        if self.link is not None:
            self.link.close()
            self.link = None
            self.btn_conn.configure(text="连接")
            self.lbl_state.configure(text="● 未连接", fg=DIM)
            self.lbl_port.configure(text="(未连接)")
            self.log("已断开")
            return
        port = find_port()
        if not port:
            messagebox.showwarning("没找到板子", "没发现 VID 38F4 的 CDC 串口。\n"
                                   "请确认板子已上电、CDC 线已插好，然后重试。")
            return
        try:
            self.link = Link(port, self.events)
        except Exception as e:
            messagebox.showerror("打开串口失败", "%s\n\n可以试试：重新插拔板子的 CDC 线，"
                                 "或运行 tests/usb_recover.py" % e)
            return
        self.lbl_port.configure(text=port)
        self.btn_conn.configure(text="断开")
        self.lbl_state.configure(text="● 已连接", fg=ACCENT)
        self.log("已连接 %s（主机持有端口，DTR 已置位）" % port)
        self._sync_time()
        self._push_skills()

    def _sync_time(self):
        if not self.link:
            return
        cmd = "TIME %d %d" % (int(time.time()), tz_minutes())
        self.link.send_line(cmd)
        self.log("下发校时：%s（本机 %s）" % (cmd, time.strftime("%H:%M:%S")))

    def _push_skills(self):
        import glob

        if not self.link:
            return
        paths = sorted(glob.glob(os.path.join(SKILL_DIR, "*.md")))
        n = 0
        for path in paths:
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
            self.link.send_line("SKILL %s|%s|%s|%s" % (
                fields["name"], fields["triggers"],
                fields.get("tool", "cloud"), fields.get("prompt", "")))
            n += 1
            time.sleep(0.35)
        if n:
            self.log("已推送 %d 个 Skill 到板端热安装" % n)

    # ------------------------------------------------------------ 收发
    def _send_entry(self):
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self._send_user(text)

    def _send_user(self, text):
        if not self.link:
            self.log("未连接，先点「连接」")
            return
        try:
            self.link.send_user(text)
            self.say_bubble("我  >", text)
        except Exception as e:
            self.log("发送失败: %s" % e)

    def _on_voice_text(self, text):
        self.root.after(0, lambda: self._send_user(text))

    def _pump(self):
        """主线程每 80ms 处理一次链路事件（Tkinter 只能在主线程操作 UI）。"""
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "frame":
                    self._on_frame(payload)
                elif kind == "closed":
                    self.log("链路断开: %s" % payload)
        except queue.Empty:
            pass
        self.root.after(80, self._pump)

    def _on_frame(self, f):
        t, s, p = f.type, f.seq, f.payload
        try:
            if t == FrameType.TEXT:
                self.say_bubble("灵犀<", p.decode("utf-8", "replace"))
            elif t == FrameType.REMINDER:
                self.log("【提醒】%s" % p.decode("utf-8", "replace"))
            elif t == FrameType.HEARTBEAT:
                if self.link:
                    self.link.ser.write(build_frame(FrameType.ACK, s))
            elif t == FrameType.CLOUD_REQ:
                text = p.decode("utf-8", "replace")
                self.log("板端请求云端：%s" % text)
                self._cloud_reply(s, text)
            elif t == FrameType.DEVICE_STATUS:
                self.log("板端状态：%s" % p.decode("utf-8", "replace"))
            elif t == FrameType.WAKE_EVENT:
                self.log("板端唤醒事件")
        except Exception as e:
            self.log("处理帧出错: %s" % e)

    def _cloud_reply(self, seq, text):
        """有 Key 走真云端，否则本地 Mock 应答（保证演示闭环）。"""
        if not self.api_key:
            import json
            q = text
            try:
                d = json.loads(text)
                q = d.get("q", text)
            except Exception:
                pass
            self.root.after(0, lambda: self.link.send_line(
                "SAY 收到「%s」。（当前为本地 Mock 应答，填 API Key 即接真云端）"
                % q[:24]))
            return

        def work():
            import asyncio
            from wristclaw.cloud_relay import CloudConfig, CloudRelay
            cfg = CloudConfig()
            cfg.api_key = self.api_key
            relay = CloudRelay(cfg)

            async def go():
                await relay.initialize()
                return await relay.chat(text)
            try:
                res = asyncio.run(go())
            except Exception as e:
                self.root.after(0, lambda: self.link.send_line(
                    "SAY 云端调用失败：%s" % str(e)[:40]))
                return
            out = str(res.get("text", "")).strip()
            self.root.after(0, lambda: self.link.send_line("SAY " + out))

        threading.Thread(target=work, daemon=True).start()

    def _toggle_voice(self):
        if self.var_voice.get():
            self.voice.start()
        else:
            self.voice.stop_now()
            self.log("已停止语音唤醒")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
