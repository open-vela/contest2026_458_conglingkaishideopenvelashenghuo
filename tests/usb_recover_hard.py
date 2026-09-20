#!/usr/bin/env python3
"""Last-resort recovery for the board's CDC port.

Ladder (each step is checked before moving on):
  1. restart the failed / stale device node
  2. restart the PCI USB controller that hosts the board

Restarting the controller also drops COM8 (the CH9102 console), so the script
runs `pnputil /scan-devices` at the end and reports both ports.

Do NOT restart the root hubs -- that drops the console and it does not come
back on its own.
"""
import os
import re
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))     # <repo>/tests
_REPO = os.path.dirname(_HERE)                         # <repo>
sys.path.insert(0, os.path.join(_REPO, "host"))


def sh(cmd):
    p = subprocess.run(cmd, shell=True, capture_output=True)
    raw = p.stdout + p.stderr
    for enc in ("utf-16le", "utf-8", "gbk"):
        try:
            return raw.decode(enc).replace("\r", "")
        except UnicodeDecodeError:
            pass
    return ""


def ports():
    from serial.tools import list_ports
    cdc = [p.device for p in list_ports.comports()
           if "38F4" in (p.hwid or "").upper()]
    con = [p.device for p in list_ports.comports()
           if "1A86" in (p.hwid or "").upper()]
    return cdc, con


def try_open(dev):
    import serial
    try:
        s = serial.Serial(dev, 1000000, timeout=0.2, write_timeout=2.0,
                          rtscts=False, dsrdtr=False)
        s.rts = False
        s.dtr = True
        s.close()
        return True
    except Exception as e:
        print("     %s: %s" % (dev, str(e)[:55]))
        return False


def main():
    print("== 0. 当前状态 ==")
    cdc, con = ports()
    print("  CDC:", cdc, "| 控制台:", con)

    print("== 1. 重启设备节点 ==")
    out = sh('pnputil /restart-device "USB\\VID_38F4&PID_A4A7\\0"')
    print(" ", out.strip().splitlines()[-1][:70] if out.strip() else "?")
    time.sleep(3)
    cdc, _ = ports()
    for d in cdc:
        if try_open(d):
            print("[OK] 端口可用:", d)
            return 0

    print("== 2. 重启 PCI USB 控制器 ==")
    txt = sh("pnputil /enum-devices /class USB")
    insts = re.findall(r"实例 ID:\s*(PCI\\\S+)", txt)
    for inst in insts:
        out = sh('pnputil /restart-device "%s"' % inst)
        if "已成功重启" not in out:
            continue
        print("  重启成功:", inst[:52])
        for _ in range(6):
            time.sleep(2)
            cdc, _ = ports()
            for d in cdc:
                if try_open(d):
                    print("[OK] 端口可用:", d)
                    sh("pnputil /scan-devices")
                    time.sleep(3)
                    cdc, con = ports()
                    print("  CDC:", cdc, "| 控制台:", con)
                    return 0

    sh("pnputil /scan-devices")
    time.sleep(3)
    cdc, con = ports()
    print("[FAIL] 仍不可用 -> CDC:", cdc, "| 控制台:", con)
    print("       需要物理拔插板子的 CDC 线（10 秒）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
