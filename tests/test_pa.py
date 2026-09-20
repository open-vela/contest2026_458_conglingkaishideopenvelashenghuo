#!/usr/bin/env python3
"""Check the audio power-amplifier enable path (PA10 = AUDIO_PA_CTRL).

Board: PA10 drives the NS4150B Class-D amp.  Board pinmux already sets the pin
to GPIO, so the app only sets direction + level through BSP_GPIO_Set().

The firmware answers every line-protocol command with a SAY frame, so a reply
here proves the command reached the agent -- whether the speaker actually
clicks is something only ears (or a scope) can confirm.
"""
import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))     # <repo>/tests
_REPO = os.path.dirname(_HERE)                         # <repo>
sys.path.insert(0, os.path.join(_REPO, "host"))
import serial                                                  # noqa: E402
from serial.tools import list_ports                            # noqa: E402
from wristclaw.protocol import FrameType, build_frame, FrameParser  # noqa: E402

HERE = _HERE
CONSOLE = os.environ.get("WC_CONSOLE_PORT", "COM8")


def open_port():
    for _ in range(4):
        port = next((p.device for p in list_ports.comports()
                     if "38F4" in (p.hwid or "").upper()), None)
        if port:
            for _ in range(3):
                try:
                    s = serial.Serial(port, 1000000, timeout=0.1,
                                      write_timeout=2.0, rtscts=False,
                                      dsrdtr=False)
                    s.rts = False
                    s.dtr = True
                    print("  主机已持有", port, flush=True)
                    return s
                except Exception as e:
                    print("  open 失败:", str(e)[:55], flush=True)
                    time.sleep(1.5)
        subprocess.run([sys.executable, HERE + "/usb_recover.py"],
                       capture_output=True)
        time.sleep(3)
    return None


def main():
    usb = open_port()
    if usb is None:
        print("FAIL: 端口打不开", flush=True)
        return 1

    con = serial.Serial("COM8", 1000000, timeout=0.3, rtscts=False,
                        dsrdtr=False)
    con.rts = False
    con.dtr = False
    time.sleep(0.3)
    con.reset_input_buffer()
    con.write(b"ps\r\n")
    time.sleep(1.2)
    if "wristclaw" not in con.read(8000).decode("latin-1", "replace"):
        con.write(b"wristclaw &\r\n")
        print("  已启动 wristclaw", flush=True)

    fp = FrameParser()

    def drain(sec):
        out = []
        t0 = time.time()
        while time.time() - t0 < sec:
            for f in fp.feed(usb.read(8192)):
                if f.type == FrameType.TEXT:
                    out.append(f.payload.decode("utf-8", "replace"))
        return out

    print("--- 等链路 ---", flush=True)
    for s in drain(6)[-4:]:
        print("   |", s[:80], flush=True)

    for cmd, note in (("PA 1", "功放开（听有没有底噪/嗒声）"),
                      ("PA 0", "功放关"),
                      ("PA 1", "再开一次")):
        usb.write(build_frame(FrameType.CLOUD_RESP, 950, cmd.encode()))
        print(">> %s   (%s)" % (cmd, note), flush=True)
        for s in drain(2):
            if "功放" in s or "PA10" in s:
                print("   <<", s[:90], flush=True)

    usb.close()
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
