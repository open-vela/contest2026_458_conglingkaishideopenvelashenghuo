# 操作手册（评委/演示复现）

> 本文是"照着做就能复现"的完整流程。所有命令的路径以本仓为根。

## 0. 硬件与线缆

| 线 | Windows 口 | 用途 |
|---|---|---|
| 调试串口 (CH9102) | **COM8** | NSH 控制台 + RTS 硬复位（RTS 接 VCC 负载开关） |
| 片内 USB (CDC-ACM) | **COM10**（编号以实际为准） | 帧协议数据链路（`VID_38F4&PID_A4A7`） |

> **两条线都要插。** 板子整板由 USB 供电。

## 1. 编译与烧录

```bash
# WSL 内
ninja -C cmake_out/sf32lb52_devkit_lcd nuttx.bin

# Windows 侧（sftool）
sftool -c SF32LB52 -p COM8 -b 1000000 --before default_reset \
       --after soft_reset \
       write_flash cmake_out/sf32lb52_devkit_lcd/nuttx.bin@0x12010000
```

若 sftool 报 `Failed to connect to the chip`：SoC 错过了 RTS 复位后约 2s 的
监听窗口 —— 再烧一次，或按板上的物理 Reset 键。

## 2. 启动板端 Agent

串口工具连 **COM8**：1 000 000 8N1，**关闭流控、RTS 必须 deassert**
（picocom 用 `--lower-rts --lower-dtr`；SSCOM 请勿勾选 RTS/DTR）。

```
nsh> wristclaw &
WristClaw: booting (build ...)
WristClaw: agent ready (memos=0 skills=0)
```

屏幕出现表盘页：时间 / 日期 / 状态行（提醒数、备忘数、Skill 数、亮度）。
**触摸左右滑动**切换：表盘 → 对话 → 提醒。

## 3. 启动 PC 网关

```bash
cd host
python app.py                    # 单程序 GUI（推荐入口）
python bridge.py --mock          # 键盘输入模拟语音（离线可用）
python wake.py --mock            # 全局语音唤醒（说「你好 openvela」）
```

`bridge.py` 会自动找到 COM10 并**保持持有**；板端随后打印
`WristClaw: link up on /dev/ttyACM0`。

> ⚠️ **操作规则（重要）**：COM10 同一时间只允许一个主机程序持有。
> 一旦 `bridge.py` 连上，就不要再开 SSCOM 去连 COM10；
> 控制台日志走 COM8。若中途换程序，请先关掉上一个。

## 4. 演示脚本（3 分钟）

1. **唤醒**：对 PC 麦克风说“你好 openvela”（`wake.py` 或 GUI 勾选「语音唤醒」）→ 提示音；
2. **本地指令（断网可用）**："提醒我 1 分钟后喝水" → 板端立即回
   "好的，60 秒后提醒你…"，屏幕上提醒列表出现该条目；
3. **主动推送**：1 分钟后板端**主动**推送"【提醒】喝水"，屏幕弹卡片、
   上位机打印；
4. **云端理解**：说"帮我安排一下明天上午的会议准备" → 板端上行云端 →
   云端（MiMo 或 mock）回复、结构化动作回板执行；
5. **Skill 扩展**：在 `bridge.py` 控制台输入
   `SKILL weather|天气,下雨|cloud|查询天气并只回一句话`
   → 板端回"SKILL 已安装并生效"，再输入 `SKILLS` 可列出；
6. **手表 UI**：滑动查看对话卡片与提醒列表。

## 5. 常见问题（都是实测踩过的）

| 现象 | 原因 / 处理 |
|---|---|
| 板端打印 `open /dev/ttyACM0 failed: 107` | 主机还没打开 COM10（DTR 未置位）。先启动 `bridge.py`/`wake.py`，板端会自动绑定 |
| 主机 `Cannot configure port ... PermissionError(31/121)` | Windows 端口状态卡住：断开所有占用 COM10 的程序，重新插拔板子的 CDC 线，或运行 `tests/usb_recover.py` |
| COM10 打不开但设备管理器有 `VID_0000&PID_0002 描述符请求失败` | 主机侧端口处于失败态：`usb_recover.py` 会重启该节点；仍不行则重启该设备所在的 **USB 控制器**（`PCI\VEN_8086&DEV_A12F...`）；再不行**物理拔插** |
| 板子的 CDC（`VID_38F4`）在设备管理器里**完全消失**（只剩 COM8） | 设备侧 USB 未挂上，软件层无法恢复：**物理拔插 CDC 那根线**（等 5 秒再插回） |
| 主机端 `open COM10` 一直不返回 / 报 `121 信号灯超时` | 板端正在灌数据（app 已跑），主机配置请求被饿死 → 保证**先开主机端口再启动板端 app** |
| `ps` 里有多个 `wristclaw` | 重复启动：多实例争抢 ttyACM0、同时发心跳会撑爆主机 RX（表现为链路抖动）；冷复位后可清空（本 NSH 无 `kill` 命令） |
| 串口被占用（`拒绝访问`）但找不到串口助手 | 可能是被杀掉外层 shell 后**存活的测试脚本**（`python.exe`）；任务管理器 → 详细信息 → 结束 `python.exe` |
| 屏幕中文显示不全 | 内置 CJK 位图字体为常用字子集，生僻字留空；替换自生成全量字体可解决 |
| 板子整机无响应（控制台也静默） | 拉低 COM8 的 RTS ≥ 0.5s 做**冷复位**（等价断电重上电）；RTS 接的是整板供电负载开关，这是最可靠的一级恢复手段。软件侧可运行 `tests/usb_recover.py` 恢复 CDC 端口坏态 |

> **启动顺序（唯一稳定顺序）**：
> `① 主机打开 COM10 并保持` → `② 板端 nsh> wristclaw &` → 板端自动绑定并打印
> `link up on /dev/ttyACM0`。反过来（板端先跑）会让主机的 open 卡死 250 秒后超时。

## 6. 自动化自测

```bash
python tests/test_wc_interop.py   # 协议互操作（C<->Python，无需板子；需 gcc）
python tests/test_e2e2.py         # 端到端 7 用例（需板子运行）
```

---

## 九、时间同步（演示必需，因为板子没有 RTC 备份电池）

板子断电后 `time()` 就从 0 开始，**没有任何可信时间源**；openvela 侧也没有开
TZ 环境变量支持（`localtime_r` 等于 UTC）。所以时间必须由上位机下发。

**行协议**：`TIME <epoch 秒> <时区分钟>`
- 上位机连接板端后**立即下发**，并每 60 秒重发一次抵消晶振漂移
- 板端收到后 `clock_settime()` 设系统时间，并按下发值设置时区偏移
  （默认东八区 +480，用 `gmtime_r(utc + offset)` 换算）
- **未校时期间表盘显示 `--:--` 和「等待上位机校时」**，不摆 1970 年的假时间

**演示顺序**：先在上位机点「连接」（或跑 `bridge.py`）→ 再启动板端 app
（`wristclaw &`）。0.5 秒内表盘会从 `--:--` 跳到正确时间。

实测（`tests/test_time2.py`）：
```
<< 时间已同步：22:43
问"现在几点" -> << 现在是 2026-09-20 22:43:21   # 与本机一致
```

## 十、音频现状（实测，不是推测）

**结论：本期没有音频通路。** 证据：

| 层 | 实测 |
| --- | --- |
| 固件配置 | `CONFIG_AUDIO` / `CONFIG_I2S` / codec 配置全部不存在 |
| 板级驱动 | `boards/.../src/` 无音频文件，未注册 `/dev/audio` |
| NuttX 胶水层 | 全 `vendor/sifli/` 搜 `audio_lowerhalf_s` / `AUDIO_register` 零命中 |
| HAL 源码 | 有 `bf0_hal_audcodec.c` / `audprc` / `i2s` / `pdm`，但 `chips/sf32lb52/CMakeLists.txt` 里被 `REMOVE_ITEM` **排除在编译之外** |
| 芯片 Kconfig | `BSP_USING_I2S` / `BSP_USING_PDM` 属 RT-Thread 配置，对 NuttX 构建不生效 |

板级硬件确实是齐的（片内 codec + PDM 数字麦 + Class-D 功放，**功放使能 PA10**），
缺的是一个 NuttX audio 下层驱动（PDM 录音 + codec/audprc 放音 + DMA 环形缓冲 +
板级注册 `/dev/audio`）。故此本期「听/说」放在 PC 网关侧完成，端侧负责理解、
执行与显示；端侧音频为赛期首要迭代项。

## 十一、UI 布局注意事项（踩过的坑）

- **不要写 `LV_PCT(100) - 34`**：LVGL 的 `LV_PCT()` 返回编码过的特殊坐标值，
  与整数相减会得到非法尺寸，页签会错位压住状态栏（表现为顶部黑框、滑动时盖住表盘）。
  正确做法是父容器 `LV_LAYOUT_FLEX` + `LV_FLEX_FLOW_COLUMN`，状态栏固定高度，
  内容区 `lv_obj_set_flex_grow(obj, 1)`。
- 中文用自生成位图字体（GB2312 全量 6763 汉字 + ASCII，约 930KB，XIP 直读不占
  RAM），不要用内置的 `lv_font_simsun_16_cjk`（只有 1000 字子集，会出方框）。
  生成脚本：`tests/gen_font_symbols.py` + `tests/gen_font.js`。
- `LV_SYMBOL_*` 图标在私用区，自生成字体里没有，靠 fallback 到 Montserrat 渲染
  （生成脚本已把 `.fallback` 写进字体描述符——注意 lv_font_conv 产出的是
  `const lv_font_t`，**不能**在运行时赋值）。
