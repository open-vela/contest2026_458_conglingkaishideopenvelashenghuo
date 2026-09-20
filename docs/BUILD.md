# 构建与烧录复现

本文记录**我们实机验证过的**构建/烧录流程，以及过程中踩到的坑（都带现象与原因），
便于评审从零复现。

目标板：**SF32LB52-DevKit-LCD**（openvela 的 SiFli vendor 层已提供板级支持：
`vendor/sifli/boards/sf32lb52/sf32lb52_devkit_lcd`）。

---

## 一、编译

```bash
# 1) 应用进编译树（与实机验证一致的位置）
cp -r contest2026_458_conglingkaishideopenvelashenghuo/app/wristclaw apps/examples/wristclaw

# 2) 应用开关（defconfig）
#    CONFIG_LVX_USE_DEMO_CONTEST2026_458_WRISTCLAW=y
#    （本仓 board/contest_board/configs/nsh/defconfig 是实机所用配置的逐字复制）

# 3) 配置 + 构建
cd nuttx
cmake -B cmake_out/sf32lb52_devkit_lcd \
      -DBOARD_CONFIG=../vendor/sifli/boards/sf32lb52/sf32lb52_devkit_lcd/configs/nsh
ninja -C cmake_out/sf32lb52_devkit_lcd nuttx.bin
```

产物：`nuttx/cmake_out/sf32lb52_devkit_lcd/nuttx.bin`（单 XIP 镜像，约 2.4 MB，
含 LVGL 与自生成 CJK 字体）。

**增量编译**（只改 `.c`）：约 **80 秒**。

```bash
ninja -C cmake_out/sf32lb52_devkit_lcd nuttx.bin     # ← 指定目标
```

---

## 二、烧录

```bash
sftool -c SF32LB52 -p COM8 -b 1000000 \
       --before default_reset --after soft_reset \
       write_flash nuttx/cmake_out/sf32lb52_devkit_lcd/nuttx.bin@0x12010000
```

- `COM8`：板载 CH9102 调试串口，同时是控制台与烧录口（1 Mbps）。
- 若上一次烧录失败导致 SoC 停在 ROM bootloader，`--before default_reset` 会超时，
  改用 `--before no_reset` 重试即可。

**烧录后必须做读回校验**（本仓 `tests/deploy.sh` 已自动化）：

```bash
sftool -c SF32LB52 -p COM8 -b 1000000 read_flash "verify.bin@0x12010000:0x4000"
# 与 nuttx.bin 前 16 KB 做 sha256 比对
```

> 为什么必须校验：`sftool` 偶发 panic 中断，**命令返回后镜像可能并没有写进去**，
> 而在板子仍然运行旧固件的情况下，一切"看起来正常"。我们就是因为只看了
> 输出末行、没校验，误判过一次。

---

## 三、运行

```bash
# ① 先起上位机（主机必须先持有 COM10 → DTR 置位，板端 open() 才成功）
cd contest2026_458_*/host && python app.py
# ② 板端 NSH 启动 Agent
nsh> wristclaw &
```

---

## 四、实测踩过的坑（现象 → 原因 → 处理）

### 1. 只改 `.c` 却触发全量重编
`cmake --build` 默认 `all` 目标，会多跑 3 次 14 MB ELF 链接与 3 个巨型 allsyms
（实测 483 秒）。→ **始终指定 `nuttx.bin` 目标**。

### 2. 改了 defconfig 却不生效
`olddefconfig` 会静默沿用旧 `.config`。→ 删掉 `.config` 强制重配（全量约 14–19 分钟）。

### 3. 新建 app 目录后，defconfig 里的 `CONFIG_<APP>` 被丢弃
`nuttx_generate_kconfig()` 为每个 apps 子目录生成 Kconfig 汇总，
**CMake 不会因为新目录而重跑这些 custom command** → 新 app 的 Kconfig 永不 source。
→ 删除 `cmake_out/.../apps/_mnt_*_<dir>_Kconfig` 与 `apps/Kconfig` 强制重生成。

### 4. 改了 `CMakeLists.txt` 的 SRCS 却没编进新文件
配置期生成的依赖不会自动刷新 → 触发重新 configure（接近全量重编）。
本仓的做法：**字体这种大件以头文件形式 `#include` 进 `wc_ui.c`**，
新增字体不必动 `CMakeLists.txt`。

### 5. 端侧 USB 端点方向是硬固定的
中断 IN 端点若配到只能收的端点号上，`TXPKTRDY` 会被静默忽略，包永远发不出去。
本芯片实测：`EP1/5/6/7` 仅发（IN），`EP2/3/4` 仅收（OUT）。
CDC-ACM 必须显式设 `EPINTIN=5`、`EPBULKIN=1`、`EPBULKOUT=2`
（见 defconfig 中的 `CONFIG_CDCACM_EP*`）。

### 6. 板端 `open("/dev/ttyACM0")` 一直返回 ENOTCONN(107)
`cdcacm` 用主机 DTR 判定 connected —— **主机先打开端口，板端才能绑定**。
顺序反了会让主机侧的配置请求被板端数据流饿死（打开卡 250 秒后超时）。

### 7. 一个板子同时跑多个 app 实例
多个实例抢同一 tty，把主机 RX 撑爆，表现为"链路时好时坏"。→ 保持单实例。

### 8. 主机侧 CDC 端口偶发进坏态
现象：`PermissionError(13/31)`、`FileNotFoundError`、甚至 `serial.Serial()`
**直接阻塞不返回**。
→ 处理：`tests/usb_recover.py`（重启失败的设备节点 → 必要时重启所在 USB 控制器，
**不要重启根集线器**，会把调试串口一起打断）；脚本外层一律加 `timeout`。
极少数情况下需要**物理拔插** CDC 线才能恢复。

### 9. 用 `| tail` 会吞掉退出码
`ninja ... | tail -3` 的退出码是 `tail` 的 0，构建失败也会继续往下烧录。
→ 构建/烧录一律重定向到日志文件再判断退出码（见 `tests/deploy.sh`）。
