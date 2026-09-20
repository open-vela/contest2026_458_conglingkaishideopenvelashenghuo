# WristClaw Host — PC 网关

板端 Agent（`app/wristclaw/`）的 PC 侧搭档：**语音唤醒 / ASR / 云端理解 / TTS**
都在这里，板端只负责"执行 + 呈现"。两者通过 USB CDC-ACM 的帧协议对话。

## 组件

| 文件 | 作用 |
|---|---|
| `wake.py` | **全局语音唤醒**（Vosk 离线常驻监听「你好 openvela」）→ 录音 → ASR → 发板端 |
| `bridge.py` | 串口桥 <-> 云端；`--mock` 无需 Key；连接时自动把 `skills/*.md` 推到板端 |
| `wristclaw/protocol.py` | 帧协议（与 `wc_proto.c` 逐字节兼容）+ CRC16-CCITT |
| `wristclaw/cloud_relay.py` | OpenAI 兼容接口（MiMo），内置规则 mock 与离线兜底 |
| `wristclaw/transport.py` | pyserial 传输 + 常驻收帧 |
| `skills/*.md` | 自定义 Skill（触发词 / 工具 / 提示词），启动时热安装到板端 |

## 安装

```bash
pip install pyserial sounddevice numpy vosk openai
# 全局唤醒需离线中文模型（约 40MB）
mkdir -p models && cd models
curl -LO https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip
unzip vosk-model-small-cn-0.22.zip
```

## 用法

```bash
# 1) 键盘联调（无麦克风/无 Key 也能跑）
python bridge.py --mock

# 2) 全局语音唤醒（离线，无需 Key）
python wake.py --mock

# 3) 接真实云端（MiMo / 任意 OpenAI 兼容端点）
python wake.py --api-key sk-xxx --api-base https://api.example.com/v1 --model mimo
```

`wake.py` 交互：说 **“你好 openvela”** → 提示音 → 说指令（8 秒内）→
自动 ASR → 板端执行 → 回复在屏幕与终端同步显示。
唤醒词按大赛要求统一为「你好，openvela / Hello，openvela」，
并做音近容错匹配（openvela / open vela / 维拉 / 欧朋维拉 / 欧本维拉），
容忍 Vosk 中文模型对英文词的常见误识别。

## 操作规则（实测，务必遵守）

1. **COM10 同时只允许一个主机程序持有**。`wake.py` / `bridge.py` / 串口助手
   三者互斥；控制台日志请走 **COM8**（调试串口），数据面走 **COM10**。
2. **先起主机程序，再起板端 app**：板端 `open("/dev/ttyACM0")` 只在主机
   打开 COM10（DTR 置位）后才成功，否则报 `failed: 107`（ENOTCONN）——
   这不是故障，等主机连上即可。
3. **不要重复启动板端 app**：多个实例会同时持有端口、同时发心跳，
   把主机 RX 撑爆（表现为链路抖动）。`ps` 里只应有一个 `wristclaw`。
4. 串口助手若要看 COM8，请关闭流控并让 **RTS 保持 deassert**
   （RTS 接的是 VCC 负载开关，拉 RTS = 给整板断电）。

## 故障排查

| 现象 | 处理 |
|---|---|
| 板端 `open /dev/ttyACM0 failed: 107` | 正常等待态：主机程序未连 COM10 |
| 主机开 COM10 报 `PermissionError(31/121)` 或卡住不返回 | 设备侧 CDC 卡死：重新烧录固件 + `.workbuddy/usb_recover.py`；实在不行物理拔插 |
| 设备管理器出现 `VID_0000&PID_0002 描述符请求失败` | 主机端口处于失败态，`usb_recover.py` 会重启该节点 |
| 唤醒词不响应 | 确认麦克风（`python -c "import sounddevice;print(sounddevice.query_devices())"`）；`--verbose` 看识别中间结果 |
| ASR 结果为空 | mock 模式不支持 ASR（需真实 API Key）；确认 `models/` 模型已解压 |

---

## 单程序 GUI（推荐入口）

```bash
python app.py          # 或双击「腕灵犀上位机.bat」
```

一个窗口完成演示所需的一切：

1. **自动发现并连接**板端 CDC 串口（VID `38F4`）
2. 连接后**立即校时**（行协议 `TIME <epoch> <时区分钟>`，之后每 60 秒重发）
   并**自动推送** `skills/*.md` 到板端热安装
3. 文本框输入 = 模拟语音，走板端完整意图路由（本地优先 / 云端兜底）
4. 「**语音唤醒**」勾选框：离线 Vosk 常驻监听「**你好 openvela**」→ 提示音 →
   录音 4 秒 → ASR → 下发板端
5. 帧日志：板端 SAY / 提醒推送 / 状态上报全部实时显示
6. 云端可选：不填 API Key 走本地 Mock（**演示不会开天窗**），填了就跑真云端

> 依赖：Tkinter（系统 Python 自带）+ `pyserial`；语音唤醒另需 `vosk` /
> `numpy` / `sounddevice`，以及离线模型
> `models/vosk-model-small-cn-0.22`（下载见下）。
> **注意**：WorkBuddy 的托管 Python venv 不含 Tkinter，故 `.bat` 用系统
> Python 建 `.venv` 并安装依赖。

### 离线语音模型

```bash
mkdir -p models && cd models
curl -L -o cn22.zip https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip
python -c "import zipfile; zipfile.ZipFile('cn22.zip').extractall('.')"
```

### 演示顺序（重要）

**先点「连接」，再启动板端 app**（`nsh> wristclaw &`）—— 主机必须先持有
COM10（DTR 置位），板端 `open()` 才能成功；顺序反了会两边都卡住。
