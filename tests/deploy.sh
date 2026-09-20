#!/usr/bin/env bash
#
# deploy.sh -- sync the WristClaw app sources into the NuttX tree, do an
# incremental build and flash, then verify the board really holds the image
# that was just built.
#
#   tests/deploy.sh                 # sync + build + flash + verify
#   NO_FLASH=1 tests/deploy.sh      # sync + build only
#
# Machine-specific settings come from environment variables so the script is
# portable; the defaults below match the machine the project was developed on.
#
#   ROOT       openvela working-tree root (contains nuttx/ and apps/)   [/d/sicelcd]
#   REPO       this contest repository root
#   PORT       CH9102 debug/flash serial port                          [COM8]
#   BAUD       debug serial baud rate                                  [1000000]
#   PY         python interpreter used for the read-back check
#   SFTOOL     path to sftool.exe
#   FLASH_ADDR flash offset of the XIP image                           [0x12010000]
#
# Why the verify step exists: sftool occasionally panics mid-flash and the
# on-board image then silently stays at the previous build.  Piping its output
# through `tail` also hides the non-zero exit status, so the build and flash
# steps below keep their exit codes intact and the read-back comparison is the
# only proof that the board runs the new firmware.
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # <repo>/tests
REPO="${REPO:-$(dirname "$HERE")}"                     # <repo>
ROOT="${ROOT:-/d/sicelcd}"                             # openvela tree
PORT="${PORT:-COM8}"
BAUD="${BAUD:-1000000}"
FLASH_ADDR="${FLASH_ADDR:-0x12010000}"
PY="${PY:-python3}"
SFTOOL="${SFTOOL:-$REPO/tests/sftool.exe}"

SRC="$REPO/app/wristclaw"
DST="$ROOT/apps/examples/wristclaw"
BUILD="$ROOT/nuttx/cmake_out/sf32lb52_devkit_lcd"
IMG="$BUILD/nuttx.bin"
LOG="$BUILD/ninja_deploy.log"

echo "== 0. 环境 =="
echo "   REPO=$REPO"
echo "   ROOT=$ROOT"
echo "   PORT=$PORT  BAUD=$BAUD"
if [ ! -d "$ROOT/nuttx" ]; then
  echo "   >>> 找不到 $ROOT/nuttx，请先 repo sync 并设置 ROOT=<openvela 根目录>"
  exit 1
fi

echo "== 1. sync sources =="
mkdir -p "$DST"
cp "$SRC"/*.c "$SRC"/*.h "$DST"/
sed -i 's/\r$//' "$DST"/*.c "$DST"/*.h
ls -la --time-style=+"%H:%M:%S" "$DST" | tail -8

echo "== 2. incremental build =="
set +e
(cd "$ROOT/nuttx" && ninja -C cmake_out/sf32lb52_devkit_lcd nuttx.bin) > "$LOG" 2>&1
RC=$?
set -e
tail -4 "$LOG"
if [ "$RC" -ne 0 ]; then
  echo "BUILD FAILED (rc=$RC) -- 前 40 行错误:"
  grep -nE "error|Error|FAILED" "$LOG" | head -20
  exit 1
fi

if [ "${NO_FLASH:-}" = "1" ]; then
  echo "NO_FLASH=1 -- 只编译不烧录"
  exit 0
fi

echo "== 3. flash =="
if [ ! -x "$SFTOOL" ] && ! command -v sftool >/dev/null 2>&1; then
  echo "   >>> 找不到 sftool（设 SFTOOL=/path/to/sftool）"
  exit 1
fi
SF="${SFTOOL}"; command -v "$SFTOOL" >/dev/null 2>&1 || SF="$SFTOOL"
"$SF" -c SF32LB52 -p "$PORT" -b "$BAUD" \
      --before default_reset --after soft_reset \
      write_flash "$IMG@$FLASH_ADDR"

echo "== 4. 读回校验（板上镜像 == 刚编译的 nuttx.bin）=="
VERIFY="$REPO/tests/verify.bin"
"$SF" -c SF32LB52 -p "$PORT" -b "$BAUD" \
      --before default_reset --after soft_reset \
      read_flash "verify.bin@$FLASH_ADDR:0x4000" > /dev/null 2>&1
IMG="$IMG" VERIFY="$VERIFY" "$PY" - <<'PYEOF'
import hashlib
import os

img = os.environ["IMG"]
verify = os.environ["VERIFY"]
a = open(img, "rb").read(16384)
if not os.path.exists(verify):
    verify = "verify.bin"          # sftool 写在工作目录
b = open(verify, "rb").read()
ha, hb = hashlib.sha256(a).hexdigest(), hashlib.sha256(b).hexdigest()
print("  镜像:", ha)
print("  板上:", hb)
if ha != hb:
    print("  >>> 不一致！板上不是刚编译的固件")
    raise SystemExit(1)
print("  >>> 逐字节一致，烧录确认成功")
PYEOF
