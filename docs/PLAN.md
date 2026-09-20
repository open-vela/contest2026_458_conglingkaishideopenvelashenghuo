# WristClaw 实施方案（施工版）

> 本文件是开发过程中沉淀的实施方案，最终作品说明见仓库根 `README.md`。
> 记录日期：2026-09-20（大赛提交截止日）。

## 一、现实约束（全部经实测/查证，不是假设）

开发中先核实了"方案能不能落在这块板子上"，结论直接决定了取舍。

### 1. 硬件能力（来源：原理图 netlist + 板级 defconfig + 运行时 `/dev` 列表）

运行时实际存在的设备节点（`ls /dev`，实测）：

```
adc0  buttons  config0  console  fb0  gpio0  gpio1  gpio2  i2c0  i2c1
input0  lcd0  mmcsd0  null  pwm0  ram0  rtc0  spi1  timer0
ttyACM0  ttyS0  urandom  watchdog0
```

关键对应关系：

| 方案需要的 | 板上证据 | 可用性 |
| --- | --- | --- |
| 显示屏 | `fb0` / `lcd0`，原理图 `LCD_QSPI_CLK/CS/D0-D3/RSTB/TE` + `LCD_BL_PWM` | ✅ |
| 触摸 | `input0`，原理图 `TP_I2C_SCL/SDA/INT/RTN`，`CONFIG_LV_USE_NUTTX_TOUCHSCREEN=y` | ✅ |
| 实时时钟 / 闹钟 | `rtc0`，`CONFIG_RTC_ALARM=y` | ✅ |
| 持久化 | `config0`（NOR MTD）、`CONFIG_FS_LITTLEFS=y`、`CONFIG_UNQLITE=y` | ✅ |
| 调光 | `pwm0` | ✅ |
| 按键 | `buttons`、`CONFIG_EXAMPLES_BUTTONS_NAME0="PA43_KEY2"` | ✅ |
| 传感器 | `i2c1`，`CONFIG_SENSORS_LSM6DSL=y`（**实测未焊/未应答**：`LSM6DS3 not found at 0x6a`） | ⚠️ 需 Mock |
| 与 PC 通信 | `ttyACM0`（USB CDC-ACM，Windows 免驱 `usbser.sys`） | ✅ 已实测吞吐 |
| 麦克风 | 原理图 `MIC_ADC_IN` / `MIC_BIAS`，`adc0` | ⚠️ 器件在，驱动未做 |
| 喇叭 / 功放 | 原理图 `AMP`；芯片内置 audio codec（`bf0_hal_audcodec.c` + SDK 示例） | ❌ NuttX 侧无驱动 |
| 蓝牙 | `CONFIG_BT=y` + `BT_H4`，但**未发现独立蓝牙控制器**（原理图无模块、无 BT_EN） | ❌ 不承诺 |
| RGB 灯 | 原理图 `LED_RGBCTROL_SK6812MINI`（单总线可寻址灯） | ⚠️ 需自写时序驱动 |

### 2. 带宽上限：~1 MB/s，且与协议无关（实测）

`SiFli-SDK/rtos/rtthread/bsp/sifli/drivers/drv_usbd.c:244` 明确按芯片系列分档：

```c
#ifdef SOC_SF32LB58X
    pcd->Init.speed = PCD_SPEED_HIGH;   /* 仅 LB58x 支持高速 */
#else
    pcd->Init.speed = PCD_SPEED_FULL;   /* LB52x 强制全速 */
#endif
```

为确认而非推断，把 `USB_POWER_HSENAB` 置位后读 `POWER.HSMODE`（主机是 USB 3.0 口）：

```
arm_usbinitialize : power=70   (HSENAB 已置位)
usbdev_register   : power=60
枚举完成 (epcfg)  : power=60 -> HSMODE(bit4) 始终为 0
```

**结论：chirp 未成功，PHY 只支持全速。** 12 Mbps ≈ 1 MB/s 是硬上限，
CDC-ACM / RNDIS / vendor bulk **撞同一堵墙**，换协议不会更快。
（对比：板载 CH9102 UART @ 1 Mbaud ≈ 100 KB/s，所以 USB CDC 已快约 10 倍。）

推论：**放弃 RNDIS，改用 CDC-ACM**——同带宽、零协议开销、免驱、且已有实现。

### 3. 工程结构约束

大赛要求「只在自己的仓里开发，生产仓库零改动」，manifest 用 `<linkfile>`
把 `app/` 映射到 openvela 编译树。

但本工作区**不是** manifest 的完整 checkout：没有 `packages/`、根目录没有
`build.sh`、没有 `.repo/`。因此：

- **`packages/ai_agent` / `openvelaClaw` 不在本环境**，无法按「模式 B」集成框架。
- 赛道指引明确允许 **「模式 A：基于设备的通信协议和云端大模型，独立开发 AI 场景应用」**。
  本作品采用模式 A，并把 ai_agent 被评分的几项能力（Skill、Router、
  主动任务、NL→结构化）**在应用层自行实现**，保证评分点不丢。

## 二、总体架构

```
┌────────────────────────── 腕灵犀 WristClaw ──────────────────────────┐
│                                                                      │
│  ┌────────────────── 手表端 (SF32LB52 / openvela) ───────────────┐    │
│  │  wc_proto   帧协议 (与 host/wristclaw/protocol.py 逐字节兼容)  │    │
│  │  wc_agent   意图路由 + 工具执行 + 主动引擎 + 记忆             │    │
│  │  wc_skill   读取 /data/agent/skills/*.md 的 Skill 定义        │    │
│  │  wc_ui      LVGL 卡片 UI (fb0 + input0)                       │    │
│  └───────────────┬───────────────────────────────────────────────┘    │
│                  │ USB CDC-ACM  /dev/ttyACM0  (~1 MB/s)              │
│  ┌───────────────▼──────── PC 上位机 (Python) ───────────────────┐    │
│  │  transport  串口收发                                          │    │
│  │  relay      意图理解 / NL→结构化 / 摘要  → LLM                │    │
│  │  llm        Xiaomi MiMo (OpenAI 兼容) + 离线 Mock 兜底        │    │
│  └───────────────┬───────────────────────────────────────────────┘    │
│                  │ HTTPS                                              │
│             Xiaomi MiMo 大模型                                        │
└──────────────────────────────────────────────────────────────────────┘
```

**板子没有 WiFi/网口**，所以云端必须经 PC 中继。这不是妥协——它正好对应
赛道加分项「端云/端端协作：设备端 Agent 与电脑端 Agent 配合完成复杂任务」。

## 三、功能范围与取舍

### 本期交付（P0）

1. **帧协议**：`[0xAA55][TYPE][SEQ:2][LEN:2][PAYLOAD][CRC16-CCITT:2]`，
   与既有 `protocol.py` 完全一致；流式解析、坏帧跳过、**写入永不阻塞**。
2. **意图路由**：端侧规则匹配（离线可用）+ 云端 LLM 分类增强，两级兜底。
3. **工具执行**（板端真正动手）：
   - 提醒 → `rtc0` 闹钟 + 持久化
   - 备忘 → 持久化 + 列表
   - 查时间 → `rtc0`
   - 亮度 → `pwm0`
   - 状态查询 → 设备/版本/内存
4. **主动引擎**（赛道核心区分点）：
   - 定时主动：到点推送
   - 上下文主动：未完成事项累积 → 主动建议
   - 诚实主动：连续运行时长到阈值 → 久坐/休息提醒
     （**改动说明**：原计划用模拟心率做"阈值主动"，但本板是桌面型
     开发板、无任何生物传感器，模拟心率属虚假数据，已移除；
     主动引擎改为只基于真实运行状态触发，见 `docs/DEMO.md`）
5. **记忆**：跨会话持久化（偏好 + 历史），掉电不丢。
6. **Skill**：从 `/data/agent/skills/*.md` 加载 Markdown 定义的 Skill，
   含名称/触发词/工具/提示词，可热加载。满足「至少 1 个自定义 Skill」。
7. **LVGL UI**：状态卡 / 对话卡 / 提醒列表，触摸交互。
8. **上位机**：串口传输 + LLM 中继 + Mock 兜底 + 命令行。

### 明确不做（如实写进 README，不假装）

| 项 | 原因 |
| --- | --- |
| 本地唤醒词 KWS | TFLM 框架在树里但未启用，且需模型 + 音频前端；本次时间不足 |
| 板端 TTS 播放 | 内置 codec 有 HAL 与示例，但 NuttX 无驱动，移植是「天」级工作 |
| 麦克风录音 | 模拟麦克风走 ADC，需自定义采样/DMA 通路，同上 |
| BLE 手机中继 | 未发现独立蓝牙控制器，硬件前提不成立 |
| ai_agent 框架集成 | `packages/ai_agent` 不在本工作区 |

音频三项的硬件可行性已确认（原理图 + SDK HAL + `example_audio.c`），
在 README 中作为「下一步」给出明确路径。

## 四、施工顺序

1. 板端协议 + 传输（先保证 PC↔板能通）
2. 板端 Agent（路由 + 工具 + 持久化）
3. 上位机中继（MiMo + Mock）
4. 主动引擎
5. Skill 加载
6. LVGL UI
7. 端到端自动化测试
8. README / 文档 / Skill 文件

## 五、已知工程陷阱（开发中踩到并记录）

- **CDC-ACM 写入必须非阻塞**：主机不读时阻塞写会占死调用者；此时再做
  一次端口复位即可让设备挂起（实测把控制台都拖死了，只能重新烧录恢复）。
  所以应用侧一律走「有界重试 + 丢弃」策略。
- **新 app 的 Kconfig 不会自动生效**：`nuttx_generate_kconfig()` 生成的
  汇总文件不会因新目录而重生成，导致 `CONFIG_<APP>` 被静默丢弃、
  app 不参与编译。需先删除 `cmake_out/.../apps/_mnt_*_Kconfig`。
- **Windows 不会因设备重启而重读描述符**：固件换了 USB 类之后，旧节点
  仍显示"已启动"。需 `pnputil /remove-device` + `/scan-devices` 强制端口
  复位，等价于拔插。
- **端点方向是硬固定的**（实测）：EP1/EP5/EP6/EP7 只能发，EP2/EP3/EP4
  只能收。NuttX 的 CDC-ACM 默认 `EPINTIN=1 / EPBULKIN=2 / EPBULKOUT=3`
  会踩坑，必须显式改成 `EPINTIN=5 / EPBULKIN=1 / EPBULKOUT=2`。

## 六、实现状态（交付时点）

| 项 | 状态 | 证据 |
| --- | --- | --- |
| 帧协议 + 有界写入 | ✅ 完成 | `.workbuddy/test_wc_interop.py`：C↔Python 双向逐字节一致、坏帧重同步通过 |
| 意图路由（本地优先） | ✅ 完成 | E2E：提醒/备忘/状态本地直接执行 |
| 工具执行 | ✅ 完成 | 提醒（真实倒计时触发）、备忘、时间、亮度、状态 |
| 主动引擎 | ✅ 完成 | 到点主动推送；久坐提醒；待办积压建议 |
| 记忆持久化 | ✅ 完成 | `/data/wristclaw/*` 行式文件，重启后 `memos=N` 可恢复 |
| Skill 加载 | ✅ 完成 | `/data/agent/skills/*.md`；支持上位机一条命令热安装（`SKILL name|triggers|tool|prompt`） |
| LVGL UI | ✅ 完成 | 表盘 / 对话 / 提醒三页 tileview，触摸滑动；CJK 位图字体 |
| 上位机桥接 | ✅ 完成 | `bridge.py`（含 `--mock`、`--voice`）、`wake.py`（Vosk 离线全局唤醒） |
| 云端对接 | ⚙️ 接口就绪 | `cloud_relay.py` 走 OpenAI 兼容接口，填 `--api-key` 即接 MiMo；无 Key 时规则 mock |
| 端到端自测 | ✅ 完成 | `.workbuddy/test_e2e2.py` 6 用例 |
| 端侧唤醒词 / 板载录音放音 | ❌ 本期未实现 | 硬件具备（原理图 + SDK HAL + `example_audio.c`），NuttX 侧驱动需移植 |

### 开发中定位并修复的真实缺陷（都有日志）

1. **端点方向硬约束**：中断 IN 端点被放在只能收的 EP3 上，通知永远发不出去。
2. **提醒计时提速 50 倍**：`proactive_tick` 每 20ms 循环都 `remain_s--`，
   "5 秒提醒"在 0.1 秒内触发；改为每秒只走一次。
3. **栈上 2KB 缓冲**：云端请求组装缓冲放在 16K 栈的深层调用链上，
   触发硬故障死机；改静态缓冲。
4. **写失败误拆链路**：主机短暂未读（EAGAIN）被当成链路错误，导致
   板端在主机打开/关闭窗口期疯狂拆绑重绑，反而饿死主机侧的打开操作；
   改为"忙丢帧、保链路"（仅有真错误才断开）。
5. **回复契约不一致**：上位机返回 `{"text":...}` 而板端解析 `reply`，
   字段对齐后才闭环。

---

## 七、实现状态（交付时点 · 最终版）

> 本节为最终口径，取代上文早期描述。

| 项 | 状态 | 证据 |
| --- | --- | --- |
| 帧协议 + 有界写入 | ✅ | `tests/test_e2e2.py`：C↔Python 双向逐字节一致、坏帧重同步、忙丢帧不拆链路 |
| 意图路由（三级，本地优先） | ✅ | E2E：提醒/备忘/状态本地直接执行 |
| 工具执行 | ✅ | 提醒（真实倒计时触发 + 屏幕弹卡）、备忘、时间、亮度、状态 |
| 主动引擎 | ✅ | 到点主动推送；久坐提醒；待办积压建议（只基于真实运行状态） |
| 记忆持久化 | ✅ | `/data/wristclaw/*` 行式文件，重启后 `memos=N` 恢复 |
| Skill 热安装 | ✅ | 行协议 `SKILL name\|triggers\|tool\|prompt` 一键落盘 + 热加载；本仓带 2 个示例 |
| LVGL 手表 UI | ✅ | 表盘（官方 needle API 模拟表针 + 数字时间 + 日期 + 状态栏）/ 对话气泡 / 提醒卡片，三页触摸滑动 |
| 中文显示 | ✅ | **自生成 GB2312 全量位图字体**（6763 汉字 + ASCII，约 930KB，XIP 直读），缺字告警实测 0 |
| 校时 | ✅ | 行协议 `TIME <epoch> <时区分钟>`；未校时表盘显示 `--:--`，校时后与上位机一致 |
| 上位机 | ✅ | `host/app.py`（单程序 GUI）、`bridge.py`（命令行桥）、`wake.py`（离线唤醒） |
| 云端对接 | ⚙️ 接口就绪 | OpenAI 兼容封装，填 `--api-key` 即接 MiMo；无 Key 时规则 Mock |
| 端到端验收 | ✅ **7/7 通过** | 提醒 / 备忘 / 状态 / 云端上行 / 云端回复 / Skill 安装 / 心跳 |
| 端侧唤醒词、板载录音放音 | ❌ 本期未实现 | 硬件齐备；缺 openvela/NuttX 侧音频驱动（见 docs/DEMO.md 第十节） |
| BLE 组网 | ❌ 按计划延后 | 本期 USB CDC 直连 |

### 开发中定位并修复的真实缺陷（全部有日志/现象可追溯）

1. **USB 端点方向硬约束**：中断 IN 端点被放到只能收的端点号上，
   `TXPKTRDY` 写进 `RXCSR` 被静默忽略，通知永远发不出去。
2. **提醒计时提速 50 倍**：`proactive_tick` 每 20 ms 循环都递减，
   "5 秒提醒"在 0.1 秒内触发；改为每秒只走一次。
3. **栈上 2 KB 缓冲**：云端请求组装缓冲放在 16 K 栈的深层调用链上，触发硬故障死机。
4. **写失败误拆链路**：主机短暂未读（EAGAIN）被当成链路错误 → 板端在主机
   开/关窗口期疯狂拆绑重绑，反而饿死主机侧打开操作；改为"忙丢帧、保链路"。
5. **回复契约不一致**：上位机返回 `{"text":…}` 而板端解析 `reply`，字段对齐后才闭环。
6. **备忘保存野指针（硬故障）**：`char[N][96]` 被强转成 `char**` 传给
   `fprintf("%s")`，读到字符串头 4 字节当指针 → 用 `addr2line` 解析崩溃栈定位并修复
   （这是此前所有"偶发冻结"的根因）。
7. **UI 布局用 `LV_PCT(100) - 34`**：`LV_PCT()` 是编码值，与整数相减得到非法
   尺寸 → 顶部黑框、滑动时盖住表盘；改为 flex 纵向布局。
8. **中文方框字**：内置 `lv_font_simsun_16_cjk` 只有 1000 字子集 →
   自生成全量字体后消除。
9. **提醒到期时间基准错误**：`due_epoch` 用开机时间（`g_boot_epoch`）做基准，
   校时后不正确；改为用当前真实时间。
10. **校时缺失**：板端无 RTC 备份电池且 openvela 侧未启用 TZ，
    `time()` 与 `localtime_r` 都不可用 → 新增 `TIME` 行协议 + 显式时区偏移。
