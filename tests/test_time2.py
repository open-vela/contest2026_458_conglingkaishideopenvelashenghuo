#!/usr/bin/env python3
"""Cold-start + time-sync verification.

Order matters on this board:
  1. host opens COM10 and keeps it open (DTR asserted)
  2. wristclaw is started from the NSH console (no autostart in the firmware)
  3. board reports "link up" and pushes its greeting
  4. host sends TIME -> board answers "时间已同步：HH:MM"

Both ports are used: COM8 = CH9102 debug console, COM10 = on-chip CDC (data).
"""
import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))     # <repo>/tests
_REPO = os.path.dirname(_HERE)                         # <repo>
sys.path.insert(0, os.path.join(_REPO, "host"))
from serial.tools import list_ports                            # noqa: E402
from wristclaw.protocol import FrameType, build_frame, FrameParser  # noqa: E402

HERE = _HERE
CONSOLE = os.environ.get("WC_CONSOLE_PORT", "COM8")


def find_port():
    for p in list_ports.comports():
        if "38F4" in (p.hwid or "").upper():
            return p.device
    return None


def recover():
    subprocess.run([sys.executable, HERE + "/usb_recover.py"],
                   capture_output=True)


def open_data_port():
    import serial
    for attempt in range(1, 5):
        port = find_port()
        if port:
            for _ in range(4):
                try:
                    s = serial.Serial(port, 1000000, timeout=0.1,
                                      write_timeout=2.0, rtscts=False,
                                      dsrdtr=False)
                    s.rts = False
                    s.dtr = True
                    print("  主机已持有 %s" % port, flush=True)
                    return s
                except Exception as e:
                    print("  open 失败: %s" % str(e)[:55], flush=True)
                    time.sleep(1.5)
        else:
            print("  端口不在，恢复中…", flush=True)
        recover()
        time.sleep(3)
    return None


def read_frames(usb, fp, sec):
    out = []
    t0 = time.time()
    while time.time() - t0 < sec:
        try:
            d = usb.read(8192)
        except Exception as e:
            print("  读失败: %s" % e, flush=True)
            break
        for f in fp.feed(d):
            out.append(f)
    return out


def main():
    con = None
    usb = open_data_port()
    if usb is None:
        print("FAIL: 数据口打不开", flush=True)
        return 1

    print("--- 启动板端 app（主机已先持有端口）---", flush=True)
    con = __import__("serial").Serial(CONSOLE, 1000000, timeout=0.3,
                                      rtscts=False, dsrdtr=False)
    con.rts = False
    con.dtr = False
    time.sleep(0.3)
    con.reset_input_buffer()
    con.write(b"ps\r\n")
    time.sleep(1.2)
    running = "wristclaw" in con.read(8000).decode("latin-1", "replace")
    if not running:
        con.write(b"wristclaw &\r\n")
        print("  已启动 wristclaw", flush=True)

    fp = FrameParser()
    print("--- 等待链路建立 ---", flush=True)
    seen = []
    for f in read_frames(usb, fp, 8):
        if f.type == FrameType.TEXT:
            s = f.payload.decode("utf-8", "replace")
            seen.append(s)
            print("  |", s[:80], flush=True)

    off = -(time.altzone if time.daylight and time.localtime().tm_isdst
            else time.timezone)
    usb.write(build_frame(FrameType.CLOUD_RESP, 900,
                          ("TIME %d %d" % (int(time.time()), off // 60)).encode()))
    print(">> 下发 TIME（本机 %s，时区 %+d 分钟）"
          % (time.strftime("%H:%M:%S"), off // 60), flush=True)

    ok = False
    for f in read_frames(usb, fp, 3):
        if f.type == FrameType.TEXT:
            s = f.payload.decode("utf-8", "replace")
            print("  <<", s[:85], flush=True)
            if "时间已同步" in s:
                ok = True

    usb.write(build_frame(FrameType.TEXT, 901, "USER 现在几点".encode()))
    print(">> 问：现在几点", flush=True)
    for f in read_frames(usb, fp, 4):
        if f.type == FrameType.TEXT:
            s = f.payload.decode("utf-8", "replace")
            print("  <<", s[:95], flush=True)
            if "现在是" in s:
                ok = True

    # 顺带把控制台里 UI 的启动日志带回来（好看时间是否已同步）
    tail = con.read(8000).decode("latin-1", "replace")
    for ln in tail.splitlines():
        if "WristClaw" in ln or "link up" in ln:
            print("  C|", ln.strip()[:100], flush=True)

    usb.close()
    con.close()
    print("RESULT:", "PASS" if ok else "FAIL", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
