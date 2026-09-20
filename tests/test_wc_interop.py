#!/usr/bin/env python3
"""协议互操作验收：PC 侧（host/wristclaw/protocol.py）<-> 板端（app/wristclaw/wc_proto.c）

用 **Python 编码器** 造一段字节流（其中故意混入乱码与一个 CRC 损坏的坏帧），
交给 **C 解析器** 吃；再用 **Python 解析器** 解析 C 构造出来的回复流。
两个方向都逐字节往返一致才算通过。

C 侧是 freestanding 的（wc_proto.c 不依赖 NuttX），直接用宿主机 gcc 编译即可，
不需要开发板。评审只要机器上有 gcc + python3 就能复现。

    python3 tests/test_wc_interop.py
    CC=clang python3 tests/test_wc_interop.py
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))     # <repo>/tests
REPO = os.path.dirname(HERE)                          # <repo>
sys.path.insert(0, os.path.join(REPO, "host"))        # host/wristclaw 包

from wristclaw.protocol import build_frame, FrameParser  # noqa: E402

APP = os.path.join(REPO, "app", "wristclaw")


def build_pc_stream():
    frames = []
    cases = [
        (0x01, 1, b"hello from pc"),
        (0x06, 2, bytes([0x02])),                       # DEVICE_CMD query status
        (0x04, 3, b'{"intent":"reminder","when":"9:00"}'),
        (0x02, 7777, bytes(range(256)) * 2),            # 512-byte audio chunk
        (0x0A, 0xBEEF, b""),                            # heartbeat, empty payload
        (0x05, 900, "云端回复：好的。".encode("utf-8")),
        (0x07, 901, b'{"bat":87,"link":"up"}'),
        (0x12, 902, b'[{"id":1,"when":3600,"what":"stand up"}]'),
    ]
    stream = b""
    for t, s, p in cases:
        stream += build_frame(t, s, p)

    # splice in garbage + a corrupted frame after frame 3
    head = sum(len(build_frame(t, s, p)) for t, s, p in cases[:3])
    good = stream[:head]
    tail = stream[head:]

    corrupt = bytearray(build_frame(0x99, 999, b"evil payload"))
    corrupt[-1] ^= 0xFF                      # break the CRC
    stream = good + b"\xde\xad\xbe\xef\x01" + bytes(corrupt) + b"\x00" + tail
    return stream


def main():
    tmp = tempfile.mkdtemp(prefix="wcproto_")
    pc_file = os.path.join(tmp, "frames_pc.bin")
    dev_file = os.path.join(tmp, "frames_dev.bin")

    stream = build_pc_stream()
    with open(pc_file, "wb") as fh:
        fh.write(stream)
    print("[py] wrote %d bytes (13 garbage/corrupt bytes spliced in)" % len(stream))

    # build the C test with the host compiler (wc_proto.c is freestanding)
    cc = os.environ.get("CC", "gcc")
    r = subprocess.run([cc, "-Wall", "-Wextra", "-O1",
                        "-I", APP,
                        os.path.join(APP, "test_proto_host.c"),
                        os.path.join(APP, "wc_proto.c"),
                        "-o", os.path.join(tmp, "test_proto")],
                       capture_output=True, text=True, errors="replace")
    if r.returncode:
        print(r.stdout)
        print(r.stderr)
        print("[py] FAIL: C test build error")
        print("[py] 提示：需要宿主机 C 编译器（gcc/clang）。Windows 下可在 WSL 里跑本脚本。")
        return 1
    if r.stderr.strip():
        print("[py] C build warnings:", r.stderr.strip()[:500])

    r = subprocess.run([os.path.join(tmp, "test_proto"), pc_file, dev_file],
                       capture_output=True)
    print(r.stdout.decode("utf-8", errors="replace"), end="")
    if r.returncode:
        print(r.stderr.decode("utf-8", errors="replace"))
        print("[py] FAIL: C test exited nonzero")
        return 1

    # parse what C built, with the Python parser
    with open(dev_file, "rb") as fh:
        dev = fh.read()
    got = FrameParser().feed(dev)
    print("[py] parsed %d frames from device" % len(got))
    ok = len(got) == 3
    expects = [(0x07, b"brightness=70 reminders=2 skills=1"),
               (0x09, b""),
               (0x01, "你好，世界".encode("utf-8"))]
    for f, (t, pl) in zip(got, expects):
        if f.type != t or f.payload != pl:
            print("[py] FAIL: got type=%s payload=%r" % (f.type, f.payload))
            ok = False
    if ok:
        print("[py] PASS: 3/3 device frames verified (type+payload exact)")
    print()
    print("INTEROP %s" % ("OK" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
