#!/usr/bin/env python3
"""WristClaw E2E v2 — 双通道监控版

通道1: COM10 (CDC-ACM) 帧协议，跑 6 个用例
通道2: COM8 (调试串口) 并行记录板端日志到同一时间线
写失败/超时不中断用例循环; 冻结时控制台日志可定位最后一句。

用法: python test_e2e2.py [--port COM10]
"""
import argparse
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))      # <repo>/tests
REPO = os.path.dirname(HERE)                           # <repo>
sys.path.insert(0, os.path.join(REPO, "host"))
from wristclaw.protocol import build_frame, build_text_frame, FrameParser  # noqa

VID_PID = "38F4:A4A7"
CONSOLE_PORT = os.environ.get("WC_CONSOLE_PORT", "COM8")
CONSOLE_LOG = os.environ.get("WC_CONSOLE_LOG",
                             os.path.join(HERE, "console_e2e.log"))
TL_LOCK = threading.Lock()
T0 = time.time()


def tl(tag, msg):
    with TL_LOCK:
        print("%6.1f [%s] %s" % (time.time() - T0, tag, msg), flush=True)


def console_thread(stop, path=CONSOLE_LOG):
    """COM8 监控线程: 记录板端日志；start_app 事件置位时顺带启动 app"""
    import serial
    log = open(path, "a", encoding="utf-8", errors="replace")
    tl("C", "控制台日志 -> %s" % path)
    started = {"fired": False}
    try:
        s = serial.Serial(CONSOLE_PORT, 1000000, timeout=0.2,
                          rtscts=False, dsrdtr=False)
        s.rts = False
        s.dtr = False
        s.reset_input_buffer()
        while not stop.is_set():
            if start_app_event.is_set() and not started["fired"]:
                started["fired"] = True
                # 先查重：板端可能已有实例在跑，重复启动会让多个实例
                # 争抢 ttyACM0、互相把主机 RX 撑爆
                s.write(b"ps\r\n")
                time.sleep(1.0)
                out = s.read(8000).decode("latin-1", "replace")
                if "wristclaw" in out:
                    tl("C", "ps: wristclaw 已在运行，跳过启动")
                else:
                    s.write(b"wristclaw &\r\n")
                    tl("C", ">> wristclaw & (由 E2E 经控制台启动)")
            d = s.read(4096)
            if d:
                txt = d.decode("latin-1", "replace")
                log.write(txt)
                log.flush()
                clean = (txt.replace("\x1b[0m", "").replace("\x1b[K", "")
                         .strip())
                for ln in clean.splitlines():
                    ln = ln.strip()
                    if ln and not ln.startswith("nsh>"):
                        tl("C", ln[:110])
            else:
                time.sleep(0.05)
    except Exception as e:
        tl("C", "console thread died: %s" % e)
    finally:
        try:
            s.close()
        except Exception:
            pass
        log.close()


start_app_event = threading.Event()


def find_port():
    from serial.tools import list_ports
    for p in list_ports.comports():
        if VID_PID in (p.hwid or "").upper():
            return p.device
    return None


class Link:
    def __init__(self, port):
        import serial
        last = None
        for attempt in range(3):
            try:
                self.ser = serial.Serial(port, 1000000, timeout=0.05,
                                         write_timeout=2.0,
                                         rtscts=False, dsrdtr=False)
                break
            except Exception as e:
                last = e
                tl("U", "open retry %d: %s" % (attempt + 1, e))
                time.sleep(2)
        else:
            raise last
        self.ser.rts = False
        self.ser.dtr = True
        self.parser = FrameParser()
        self.seq = 1
        self.stop = threading.Event()
        self.frames = []
        self.flock = threading.Lock()
        threading.Thread(target=self._rx, daemon=True).start()
        time.sleep(0.3)
        self.ser.reset_input_buffer()

    def _rx(self):
        """常驻收帧：主机必须一直排水，否则板端 TX 缓冲堆满"""
        while not self.stop.is_set():
            try:
                d = self.ser.read(8192)
            except Exception:
                break
            if d:
                fs = self.parser.feed(d)
                if fs:
                    for f in fs:
                        # 板端上行云端请求 → 就地做 mock 云端应答，闭环
                        if f.type == 0x04:            # WC_T_CLOUD_REQ（板端 wc_proto.h）
                            try:
                                req = json.loads(
                                    f.payload.decode("utf-8", "replace"))
                            except Exception:
                                req = {}
                            q = req.get("q", "")
                            if req.get("skill"):
                                rep = "SAY Mock(%s)：%s 已处理" % (
                                    req["skill"], q[:24])
                            else:
                                rep = "SAY Mock：收到「%s」，这是模拟云端回复" % q[:24]
                            try:
                                self.ser.write(build_frame(
                                    0x05, self.next_seq(),
                                    rep.encode("utf-8")))
                                tl("U", "CLOUD_REQ -> 已回 Mock 应答")
                            except Exception as e:
                                tl("U", "cloud reply failed: %s" % e)
                    with self.flock:
                        self.frames += fs
            else:
                time.sleep(0.02)

    def next_seq(self):
        s = self.seq
        self.seq = (self.seq % 60000) + 1
        return s

    def pump(self, seconds):
        time.sleep(seconds)
        with self.flock:
            fs = self.frames[:]
            self.frames.clear()
        return fs

    def send(self, text):
        try:
            self.ser.write(build_text_frame(self.next_seq(), text))
            return True
        except Exception as e:
            tl("U", "WRITE FAILED: %s" % e)
            return False

    def send_raw(self, frame):
        try:
            self.ser.write(frame)
            return True
        except Exception as e:
            tl("U", "WRITE FAILED: %s" % e)
            return False

    def exchange(self, text, expect, wait=3.0):
        says = []
        if not self.send("USER " + text):
            return [], False
        t0 = time.time()
        while time.time() - t0 < wait:
            for f in self.pump(0.3):
                if f.type == 0x01:
                    says.append(f.payload.decode("utf-8", "replace"))
                    tl("U", "SAY: %s" % says[-1][:70])
        return says, any(expect in s for s in says)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--no-console", action="store_true",
                    help="不占用 COM8（由用户自己启动 app 并看日志）")
    args = ap.parse_args()

    port = args.port or find_port()
    if not port:
        tl("E", "FAIL: 找不到板端 CDC 串口")
        return 1
    tl("E", "board on %s" % port)

    stop = threading.Event()
    ct = None
    if not args.no_console:
        ct = threading.Thread(target=console_thread, args=(stop,), daemon=True)
        ct.start()
        time.sleep(1)
    else:
        tl("E", "--no-console: 不占用 COM8，请确认板端 app 已在运行")

    # ★ 顺序至关重要：先打开主机端口（此时板端 app 未跑，USB 无流量，
    #   SET_LINE_CODING 不会被数据流饿死）→ 再启动板端 app。
    #   反过来的话，板端开始灌数据会让主机的 open 卡到 121 超时。
    link = None
    for attempt in range(3):
        try:
            link = Link(port)
            break
        except Exception as e:
            tl("E", "open failed (%s), recover cycle %d" % (e, attempt + 1))
            import subprocess
            subprocess.run([sys.executable,
                            os.path.join(HERE, "usb_recover.py")],
                           capture_output=True)
            time.sleep(2)
    if link is None:
        tl("E", "FAIL: 端口 3 轮恢复后仍打不开")
        stop.set()
        return 1

    tl("E", "host port held; starting app...")
    if not args.no_console:
        start_app_event.set()
    time.sleep(8)

    results = []

    def case(name, fn):
        try:
            says, ok = fn()
        except Exception as e:
            tl("E", "case %s crashed: %s" % (name, e))
            says, ok = [], False
        results.append((name, ok, says))

    # 1-4: 交互用例
    case("本地提醒", lambda: link.exchange("提醒我5秒后测试提醒", "好的", 3.0))
    time.sleep(3)                     # 等提醒触发（主动推送走控制台+TEXT帧）
    link.pump(1.0)                    # 丢弃推送窗口的帧，避免干扰下一用例
    case("本地备忘", lambda: link.exchange("记一下 明天带水杯", "备忘", 3.0))
    case("状态查询", lambda: link.exchange("状态", "提醒", 3.0))
    case("云端上行", lambda: link.exchange("给我讲个笑话", "云端", 4.0))
    case("云端回复", lambda: link.exchange("你好呀", "Mock", 4.0))

    # 5: Skill 安装（行协议，不带 USER 前缀）
    link.send("SKILL demo|演示,测试|cloud|演示技能，回复一句话")
    link.pump(1.0)
    link.send("SKILLS")
    says = [f.payload.decode("utf-8", "replace")
            for f in link.pump(2.0) if f.type == 0x01]
    results.append(("Skill 安装+列举", any("demo" in s for s in says), says))

    # 6: 心跳
    link.send_raw(build_frame(0x0A, link.next_seq()))
    hb = [f for f in link.pump(1.5) if f.type == 0x0A]
    results.append(("心跳应答", len(hb) > 0, []))

    nfail = 0
    tl("E", "========== E2E 结果 ==========")
    for name, ok, says in results:
        tl("E", "[%s] %s" % ("PASS" if ok else "FAIL", name))
        for s in says[-2:]:
            tl("E", "      | %s" % s[:70])
        if not ok:
            nfail += 1
    tl("E", "E2E %s (%d/%d 通过)" % ("PASS" if nfail == 0 else "FAIL",
                                     len(results) - nfail, len(results)))
    stop.set()
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
