#!/usr/bin/env python3
"""USB 端口恢复（无人值守）。

场景 A: 端口不存在 -> scan / 重启失败节点 / 重启根集线器
场景 B: 端口存在但打不开 (错误 31) -> 对 A4A7 节点做 restart-device,
        不行再重启根集线器

用法: python usb_recover.py [--secs N]   (默认每步等 16s)
"""
import re
import subprocess
import sys
import time

WAIT = 16


def sh(cmd):
    p = subprocess.run(cmd, shell=True, capture_output=True)
    raw = p.stdout + p.stderr
    for enc in ("utf-16le", "utf-8", "gbk"):
        try:
            return raw.decode(enc).replace("\r", "")
        except Exception:
            pass
    return raw.decode("latin-1", "ignore")


def ports_38f4():
    from serial.tools import list_ports
    return [p.device for p in list_ports.comports()
            if "38F4" in (p.hwid or "").upper()]


def wait_port(secs=WAIT):
    for _ in range(max(1, int(secs / 2))):
        time.sleep(2)
        got = ports_38f4()
        if got:
            return got[0]
    return None


def blocks(txt):
    return [b for b in re.split(r"\n\s*\n", txt) if b.strip()]


def restart_inst(inst, why):
    out = sh('pnputil /restart-device "%s"' % inst)
    ok = "已成功重启" in out or "successfully" in out.lower()
    print("  [%s] restart %s (%s)" % ("ok" if ok else "??", inst, why))
    return ok


def find_inst(pred, source):
    for b in blocks(source):
        m = re.search(r"实例 ID:\s*(\S+)", b)
        if m and pred(b, m.group(1)):
            return m.group(1)
    return None


def main():
    # ---- 场景 B: 端口在, 但打不开 -> 重启 A4A7 节点本身 ----
    port = ports_38f4()
    if port:
        print("[B] port %s exists but (likely) unopenable" % port[0])
        enum = sh("pnputil /enum-devices")
        inst = find_inst(lambda b, i: "VID_38F4&PID_A4A7" in i, enum)
        if inst:
            restart_inst(inst, "A4A7 devnode")
            p = wait_port()
            if p:
                print("[OK] port:", p)
                return 0

    # ---- 场景 A: 端口不在 ----
    print("[A] no port, scanning")
    sh("pnputil /scan-devices")
    p = wait_port(8)
    if p:
        print("[OK] port:", p)
        return 0

    # 1) 重启描述符失败的节点
    bad = sh("pnputil /enum-devices /problem")
    inst = find_inst(lambda b, i: "VID_0000&PID_0002" in i, bad)
    if inst:
        if restart_inst(inst, "failed-attach node"):
            p = wait_port()
            if p:
                print("[OK] port:", p)
                return 0

    # 2) 禁止重启根集线器！COM8 (CH9102 调试串口) 挂在同一个 Hub 上，
    #    Hub 重启会连调试串口一起断掉且不一定自动恢复。
    #    改用: 让 Windows 重新扫描 (对错过 attach 的事件有效)
    for _ in range(3):
        sh("pnputil /scan-devices")
        p = wait_port(8)
        if p:
            print("[OK] port:", p)
            return 0

    print("[FAIL] no CDC port after all recovery steps")
    return 1


if __name__ == "__main__":
    sys.exit(main())
