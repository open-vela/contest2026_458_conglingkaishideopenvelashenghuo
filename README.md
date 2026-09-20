# 腕灵犀 WristClaw —— 端云协同的腕上 AI 伙伴

> 2026 首届 openvela AI 硬件开发者大赛 · 队伍 `contest2026_458_conglingkaishideopenvelashenghuo` · 选题：AI 硬件产品创新（主）+ 手表应用创新

**一句话**：把「智能体」放进一块 openvela 手表开发板 —— 断网也能执行高频指令，联网时由云端大模型补上开放域理解；喊一句「你好 openvela」就能说话，设备还会主动提醒你。

| 关键指标 | 实测结果 |
| --- | --- |
| 端到端自动化用例 | **7/7 通过**（提醒 / 备忘 / 状态 / 云端上行 / 云端回复 / Skill 热安装 / 心跳） |
| 协议互操作 | C ↔ Python 双向逐字节一致（含坏帧重同步） |
| 唤醒词 | 「**你好，openvela**」（赛道统一要求，离线 Vosk 本地识别） |
| openvela 能力落地 | **图形**（LVGL 9.2 三页手表 UI，触摸滑动）、设备节点（CDC-ACM / RTC / PWM 背光 / NSH） |
| 中文显示 | 自生成 GB2312 全量位图字体（6763 汉字 + 全量 ASCII，约 930KB，XIP 直读不占 RAM） |

---

## 一、作品简介

腕灵犀是一套**端云协同**的腕上 AI 伙伴，两端一体：

- **板端（openvela / NuttX，本仓 `app/wristclaw/`）**：常驻 Agent 单线程承载
  「链路收发 → 三级意图路由 → 工具执行 → 主动引擎 → LVGL UI」。
  高频指令（提醒 / 备忘 / 时间 / 亮度 / 状态）在端侧闭环执行，**断网照样能用**。
- **网关端（PC，本仓 `host/`）**：全局语音唤醒（离线 Vosk 常驻监听
  「你好 openvela」）→ 录音 → ASR → 经自研帧协议下发；同时负责接云端大模型
  （按大赛 MiMo 的 OpenAI 兼容形式封装，无 Key 时降级为本地 Mock）。

**核心设计取向**：端侧负责"确定性地执行"，云端负责"听懂与生成"。
主动能力（到点推送、久坐提醒、待办积压建议）放在端侧常驻 Agent，
让设备从"问答工具"变成"会主动关心人的伙伴"；且**只基于真实运行状态触发**，
不做虚假传感器数据演示。

---

## 二、选题方向

**AI 硬件产品创新（主）+ 手表应用创新。**

选题理由：

1. **可穿戴是「端云协同」最有说服力的场景**：手表算力与功耗预算极小、交互时间极短，
   但又最依赖「随手就能用」。这正好逼出一条清晰的职责切分——端侧只做确定性的执行，
   云端负责听懂与生成。选题本身就是对这条技术路线的验证。
2. **落地 openvela 的核心能力**：本作品主要落地 **图形**（LVGL 9.2 三页手表 UI、
   390×450 AMOLED + 电容触摸）能力，并大量使用 openvela 的设备与服务
   （CDC-ACM 虚拟串口、RTC、PWM 背光、NSH 控制台）；AI 能力通过端云协同实现。
3. **可量化、可复现**：手表形态的功能边界清晰（提醒 / 备忘 / 时间 / 亮度 / 状态），
   能做端到端自动化验收，也能让评审照着 `tests/` 一步步复跑。
4. **形态可迁移**：同一套端侧 Agent + 帧协议可平移到耳机盒、桌面伴侣等
   低算力、弱网场景，商业延展性明确。

---

## 三、目录结构

```text
contest2026_458_conglingkaishideopenvelashenghuo/
├── app/wristclaw/            # ★ 板端 openvela 应用（纯 C，四模块 + 自生成字体）
│   ├── wristclaw_main.c      #   主循环：链路 tick / 收帧 / 主动引擎 / UI 驱动
│   ├── wc_agent.c            #   意图路由 / 工具 / 主动引擎 / 记忆 / Skill 加载
│   ├── wc_proto.c(.h)        #   帧协议 + 有界写入（忙丢帧、保链路）
│   ├── wc_ui.c(.h)           #   LVGL 手表 UI（表盘 / 对话 / 提醒三页）
│   └── wc_font_16.h          #   自生成 CJK 位图字体（生成脚本见 tests/）
├── board/contest_board/      # 板级适配说明 + 实机使用的 defconfig（逐字复制）
├── host/                     # ★ PC 网关：单一程序 GUI + 命令行桥 + 协议实现
│   ├── app.py                #   GUI 上位机（推荐入口）
│   ├── 腕灵犀上位机.bat       #   双击启动
│   ├── bridge.py  wake.py    #   命令行桥 / 离线唤醒
│   └── wristclaw/            #   protocol.py（与板端逐字节一致的帧协议）…
├── tests/                    # 复现与验收脚本（协议互操作、端到端、校时、烧录校验）
├── tools/                    # 交付工具链（报告/海报渲染、日志导出）
│   ├── export_logs.py        #   从本机真实 transcript 导出 AI Coding 日志
│   ├── docx2html.py          #   报告 docx → HTML → PDF（无 Word 环境下的排版链）
│   ├── fix_report.py         #   报告事实性勘误
│   ├── poster.html           #   作品海报源文件
│   └── render_poster.py      #   海报 → PNG / JPG / PDF
├── docs/                     # 方案、架构、演示手册、构建复现
│   ├── PLAN.md  ARCHITECTURE.md  DEMO.md  BUILD.md
│   └── submission/           # ★ 提交材料：技术报告 docx/pdf、海报 pdf/jpg
├── logs/                     # AI Coding 日志（工具真实会话导出，请勿手改）
└── README.md  LICENSE        # 作品说明 / Apache-2.0
```

> 组委会模板的 manifest 会把 `app/wristclaw` 软链到编译树的
> `packages/demos/contest2026_458_wristclaw`。本仓同时给出**已验证的**
> 构建方式（见 `docs/BUILD.md`），两条路径都能编译。

---

## 四、运行方式

### 1. 拉取工程

```bash
repo init -u https://github.com/open-vela/contest2026_458_conglingkaishideopenvelashenghuo \
  -b dev-ai-contest-2026 -m contest2026_458_conglingkaishideopenvelashenghuo.xml
repo sync -c -j8
```

### 2. 构建固件

目标板是 **SF32LB52-DevKit-LCD**（openvela 的 SiFli vendor 层已提供板级支持）。

```bash
# 应用放进编译树（与我们实机验证一致的路径）
cp -r contest2026_458_*/app/wristclaw apps/examples/wristclaw

# 启用应用（defconfig 加一行）
echo "CONFIG_LVX_USE_DEMO_CONTEST2026_458_WRISTCLAW=y" >> \
     vendor/sifli/boards/sf32lb52/sf32lb52_devkit_lcd/configs/nsh/defconfig

cd nuttx && cmake -B cmake_out/sf32lb52_devkit_lcd \
     -DBOARD_CONFIG=../vendor/sifli/boards/sf32lb52/sf32lb52_devkit_lcd/configs/nsh
ninja -C cmake_out/sf32lb52_devkit_lcd nuttx.bin
```

> 完整、可复制的构建/烧录步骤（含 CMake 缓存陷阱、Kconfig 汇总不重生成、
> USB 端点方向硬约束等实测坑）见 **[docs/BUILD.md](docs/BUILD.md)**。

### 3. 烧录

```bash
sftool -c SF32LB52 -p COM8 -b 1000000 --before default_reset --after soft_reset \
       write_flash nuttx/cmake_out/sf32lb52_devkit_lcd/nuttx.bin@0x12010000
```

- `COM8` = 板载 CH9102 调试串口（控制台 + 烧录）。**RTS 接整板供电开关**，
  拉一下等价断电重上电。
- `COM10` = 片内 USB CDC 数据口（跑帧协议）。

### 4. 运行

```bash
# ① 先启动上位机（关键：主机必须先持有 COM10，板端 open() 才能成功）
cd contest2026_458_*/host && python app.py        # 或双击「腕灵犀上位机.bat」

# ② 再在板端 NSH 控制台启动 Agent
nsh> wristclaw &
```

启动后：上位机点「连接」→ 板端打印 `link up on /dev/ttyACM0` → 自动校时
（表盘从 `--:--` 变成正确时间）→ 自动推送 Skill。然后在输入框打字（或勾选
「语音唤醒」说「你好 openvela」）即可对话。

### 5. 自测

```bash
cd tests
python test_e2e2.py     # 端到端 7 用例（自动开端口、启 app、双通道日志）
python test_time2.py    # 校时链路（主机持口 → 启动 app → TIME → 现在几点）
```

---

## 五、AI Coding 使用说明

本作品从零到可运行的 demo，**约 95% 的代码由 AI 生成**：板端 C（`app/wristclaw/`）、
上位机 Python（`host/`）、全部测试脚本与文档均由 AI 产出；本队负责需求定义、
方案取舍、硬件接线与实机验收。完整对话日志见 `logs/`。

### 1. 各环节如何与 AI 协作

| 环节 | 做法 | 实际效果 |
| --- | --- | --- |
| 需求拆解 | 先让 AI 读原理图、板级 README、SDK 与运行时 `/dev` 列表，**核实硬件事实**再定方案 | 避免了「板子有麦克风就能录音」一类错误前提；实测确认本芯片音频在 NuttX 侧无驱动后，果断把「听/说」移到网关侧 |
| 方案设计 | 用 AI 做方案对比（CDC-ACM vs RNDIS vs WinUSB），并让它在源码里找判定依据 | 从 SiFli SDK `drv_usbd.c` 的分档代码定位到 LB52x 强制全速，再实测 `POWER.HSMODE` 恒为 0 予以确认，**放弃 RNDIS**，省掉一整条弯路 |
| 编码 | 板端四模块 + 上位机全部由 AI 实现，人工 review 关键路径 | 单线程 20ms 主循环、有界写入、静态缓冲等约束由人工把关 |
| 调试 | **AI 主动自建调试闭环**：串口抓取、PnP 状态查询、USB 端口恢复、端到端用例脚本；崩溃用 `addr2line` 解析调用栈 | 把偶发死机定位到具体函数与行号（备忘保存野指针的根因），**共修复 10 个真实缺陷**，每一条都能在 `docs/PLAN.md` 追溯 |
| 文档与交付 | 方案/架构/演示/构建四份文档 + 提交材料同步产出 | 评审可照着 `docs/BUILD.md`、`docs/DEMO.md` 复现 |

### 2. 效率提升的三个具体来源

1. **先查实硬件事实再写代码**——把「猜」变成「读」，避免了在错误前提上写代码。
2. **自建调试闭环**——AI 顺手写出的抓包/恢复/端到端脚本，把每次回归从手工操作变成一条命令。
3. **文档与代码同步产出**——踩过的坑当场写进文档，交付时不需要回头补。

### 3. 工具与日志

- 使用的 AI 工具：WorkBuddy（Claude Code 内核）+ DeepSeek-V4.1-Flash 长上下文会话。
- **日志位置**：`logs/yilives/`，由 `tools/export_logs.py` 从本机 AI 工具的真实会话
  transcript 导出，共 **4175 条事件**（2026-09-17 → 09-20），时间戳/角色/模型名/
  工具调用均取自原始记录，`seq` 会话内连续无断档。格式说明见 `logs/README.md`。
- 导出方式（可复现）：`python tools/export_logs.py`。

---

## 六、功能与实测结果

| 能力 | 状态 | 实测证据 |
| --- | --- | --- |
| 本地提醒（含到点主动推送 + 屏幕弹卡） | ✅ | 「提醒我 5 秒后测试提醒」→「好的，5 秒后提醒你…」→ 5 秒后主动推送 |
| 本地备忘（持久化，重启可读） | ✅ | 写入 `/data/wristclaw/memos.txt`，重启后 `memos=N` 恢复 |
| 状态 / 时间查询 | ✅ | 「现在是 2026-09-20 22:43:21」与上位机一致 |
| 云端上行 / 云端回复闭环 | ✅ | CLOUD_REQ(0x04) ↔ CLOUD_RESP(0x05) |
| Skill 热安装（≥1 个，硬性要求） | ✅ | `SKILL name\|triggers\|tool\|prompt` → 落盘 + 热加载，本仓带 2 个示例 |
| 心跳与在线判定 | ✅ | 3s 周期 HEARTBEAT(0x0A) ↔ ACK(0x09) |
| 校时（板端无 RTC 备份电池） | ✅ | `TIME <epoch> <时区分钟>`；未校时显示 `--:--` |
| 手表 UI（表盘 / 对话 / 提醒） | ✅ | LVGL 9.2 tileview，触摸左右滑动，CJK 全量字体 |
| 端侧唤醒词 / 板载录音放音 | ❌ 本期未实现 | 硬件齐备，缺 openvela/NuttX 侧音频驱动（见 `docs/DEMO.md` 第十节） |
| BLE 组网 | ❌ 按计划延后 | 本期用 USB CDC 直连 |

---

## 七、已知限制（如实说明）

1. **端侧音频未打通**：`CONFIG_AUDIO` 未开、板级无音频驱动、NuttX 侧无下层
   胶水层，HAL 的 `bf0_hal_audcodec.c` 还被 CMake 排除在编译之外。故「听/说」
   由 PC 网关完成，端侧负责理解、执行与显示。**这是赛期首要迭代项。**
2. **云端大模型需自备 Key**：`host/` 已按 OpenAI 兼容接口封装（`--api-key`），
   无 Key 时自动降级为本地 Mock，保证离线自测与演示可跑。
3. **中文覆盖为 GB2312 全量**（6763 汉字），生僻字/古字仍可能缺字形。
4. **唤醒准确率未做量化评测**（安静环境下听感稳定触发）。
5. 板载 USB 为全速（实测 `POWER.HSMODE` 恒为 0），链路约 1 MB/s 量级。

---

## 八、提交材料对照

大赛模板（`2026 首届 openvela AI 硬件开发者大赛 · 作品提交模板`）第一节列出的清单：

| 序号 | 材料 | 提交要求 | 位置 / 状态 |
| --- | --- | --- | --- |
| 1 | 技术报告（.pdf / .docx） | **必交** | `docs/submission/腕灵犀WristClaw-技术报告.docx` / `.pdf` ✅ |
| 2 | 演示视频（≤5 分钟） | **必交** | ⬜ 待录制（分镜脚本见 §八-1） |
| 3 | 作品展示照片（前/后/侧/俯视） | 可选（涉及硬件实物则需提交） | ⬜ 待拍摄（清单见 §八-2） |
| 4 | 海报（.pdf / .jpg / .pptx） | 可选（入围决赛 / 线下展示） | `docs/submission/WristClaw-海报.*` ✅ |
| 5 | 答辩 PPT（.pptx） | 可选（入围决赛） | ⬜ 入围后制作 |
| — | 项目源码 + AI Coding 日志 | **本仓，评审直接 clone 验证** | `app/`、`host/`、`tests/`、`docs/`、`logs/` ✅ |

提交包命名规则：`<队伍名称>-<作品名称>-<仓库名称>.zip`，本队为

```text
从零开始的openvela生活-腕灵犀WristClaw-contest2026_458_conglingkaishideopenvelashenghuo.zip
```

> 源码与日志**不进压缩包**——评审直接 clone 本仓编译运行验证。

### 八-1、演示视频分镜（约 3 分 40 秒）

| 时间 | 画面 | 旁白要点 |
| --- | --- | --- |
| 0:00–0:20 | 作品全景：开发板亮屏、表盘页走针 | 一句话定位：断网也能执行、联网更聪明的腕上 AI 伙伴 |
| 0:20–0:50 | 上位机 GUI「连接」→ 板端控制台 `link up` → 表盘从 `--:--` 跳到正确时间 | 端云建链与校时；强调**先开主机端口再启板端 app** 的稳定顺序 |
| 0:50–1:40 | 说「你好 openvela」→ 提示音 →「提醒我 1 分钟后喝水」 | 语速放慢、字幕打出发音；板端立即回 `好的，60 秒后提醒你…` |
| 1:40–2:10 | **拔掉/停掉上位机**，再说「记一下 明天带水杯」→ 板端照样执行 | 断网自治：本地规则闭环，云端不可达时明确提示离线可用能力 |
| 2:10–2:50 | 1 分钟到点，屏幕**主动**弹卡 + 上位机打印 REMINDER | 主动引擎：到点推送、久坐提醒、待办积压建议，且只基于真实状态触发 |
| 2:50–3:20 | 在 `bridge.py` 输入 `SKILL weather\|天气,下雨\|cloud\|…` → `SKILLS` 列出 | Skill 热安装：Markdown 定义、运行时落盘，**无需重烧固件** |
| 3:20–3:40 | 手指左右滑动三页 UI（表盘 / 对话 / 提醒） | openvela 图形能力：LVGL 9.2、GB2312 全量中文位图字体 |

录制要点：屏幕与上位机同框（画中画）、全程字幕、**不要剪辑掉失败重试片段**（评委会
看真实度）；结尾打出仓库地址。

### 八-2、作品展示照片清单

硬件实物：SF32LB52-DevKit-LCD（390×450 AMOLED + 电容触摸），**两条 USB 线**
（CH9102 调试口 + 片内 CDC）。

| 文件名 | 角度 / 内容 |
| --- | --- |
| `01-前视.jpg` | 正面平视，屏幕点亮停在表盘页 |
| `02-后视.jpg` | 背面，展示丝印与接口 |
| `03-侧视.jpg` | 侧面，展示 USB 双线接法与厚度 |
| `04-俯视.jpg` | 俯视 45°，屏幕内容清晰可读 |
| `05-交互.jpg` | 手指触摸滑动切页的瞬间 |
| `06-提醒弹卡.jpg` | 提醒到点时的屏幕特写（体现主动推送） |
| `07-连线全貌.jpg` | 板子 + PC 上位机同框，体现端云两端一体 |

要求：白底或深色纯色背景、均匀打光、避免屏幕反光；横构图 3:2，长边 ≥ 3000px。

---

## 九、文档索引

| 文档 | 内容 |
| --- | --- |
| [docs/PLAN.md](docs/PLAN.md) | 方案设计、取舍论证、实现状态、开发中定位的真实缺陷 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 端云分工、帧格式与行协议、建链/交互/校时时序、可靠性策略 |
| [docs/DEMO.md](docs/DEMO.md) | 演示脚本、操作步骤、故障 FAQ、音频实测结论 |
| [docs/BUILD.md](docs/BUILD.md) | 构建与烧录复现（含所有踩过的坑） |
| [tests/README.md](tests/README.md) | 验收脚本说明与结果 |
| [host/README.md](host/README.md) | 上位机（GUI / 命令行 / 唤醒）使用说明 |
| [logs/README.md](logs/README.md) | AI Coding 日志格式与导出方式（可复现） |
| [docs/submission/](docs/submission/) | 提交材料：技术报告（docx / pdf）、作品海报（pdf / jpg） |

---

## 十、许可

本项目遵循 **Apache License 2.0**（见 [LICENSE](LICENSE)）。
第三方组件（openvela/NuttX、LVGL、Vosk 模型等）遵循其各自许可。
