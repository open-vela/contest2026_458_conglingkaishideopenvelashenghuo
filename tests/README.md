# tests/ —— 复现与验收脚本

这些脚本就是开发过程中**实际用来验收**的脚本，评审可直接跑。

| 脚本 | 用途 | 依赖 |
| --- | --- | --- |
| `test_wc_interop.py` | **协议互操作验收**（C↔Python 双向逐字节一致 + 坏帧重同步），**不需要开发板** | python3 + gcc/clang |
| `test_e2e2.py` | 端到端 7 用例验收（自动找回端口 → 启动板端 app → 双通道日志：数据帧 + 控制台） | pyserial |
| `test_time2.py` | 校时链路验收（主机先持 COM10 → 启动 app → 下发 `TIME` → 问"现在几点"） | pyserial |
| `usb_recover.py` | 板端 CDC 端口坏态恢复（重启失败设备节点 → 必要时重启所在 USB 控制器） | pyserial + pnputil |
| `deploy.sh` | 同步 → 编译 → 烧录 → **读回 sha256 校验**（照抄我们实机部署流程） | WSL + sftool + ninja |
| `gen_font_symbols.py` + `gen_font.js` | 生成中文位图字体（GB2312 全量 + ASCII，输出 `app/wristclaw/wc_font_16.h`） | Python 3 / Node + lv_font_conv |

## 运行

```bash
cd tests
python test_wc_interop.py  # 协议互操作（无需板子，需 gcc；Windows 下建议在 WSL 里跑）
python test_e2e2.py        # 端到端（约 1–2 分钟，需开发板）
python test_time2.py       # 校时（需开发板）
python usb_recover.py      # 端口坏了才需要
```

## 最近一次结果

```
$ python3 tests/test_wc_interop.py        # 协议互操作（无需开发板，需 gcc）
[py] wrote 746 bytes (13 garbage/corrupt bytes spliced in)
[C] PASS: 8/8 frames parsed, corrupted frame resynced out
[C] ALL OK (0 failures)
[py] parsed 3 frames from device
[py] PASS: 3/3 device frames verified (type+payload exact)

INTEROP OK
```

```
========== E2E 结果 ==========
[PASS] 本地提醒   | 好的，5 秒后提醒你：提醒我5秒后测试提醒
[PASS] 本地备忘   | 记下了（第 2 条备忘）：记一下 明天带水杯
[PASS] 状态查询   | 腕灵犀状态：待触发提醒 0，备忘 2，Skill 1，亮度 80%
[PASS] 云端上行   | 已发送到云端：给我讲个笑话
[PASS] 云端回复   | Mock：收到「给我讲个笑话」，这是模拟云端回复
[PASS] Skill 安装 | 已加载 1 个 Skill：
[PASS] 心跳应答
E2E PASS (7/7 通过)
```

```
>> 下发 TIME（本机 22:43:18，时区 +480 分钟）
<< 时间已同步：22:43
>> 问：现在几点
<< 现在是 2026-09-20 22:43:21
RESULT: PASS
```

## 注意

- **主机必须先持有 COM10**，板端 `open("/dev/ttyACM0")` 才能成功（详见 `docs/BUILD.md`）。
- 端口坏态下 pyserial 的 `Serial()` 可能**直接阻塞**，脚本外层建议加 `timeout`。
- 控制台上中文按 latin-1 解码会显示乱码，属正常（帧内 UTF-8 数据是正确的）。

## 移植提示

- `test_*.py` / `usb_recover.py` 会自动找口，**无需改动**；串口号可用环境变量覆盖
  （`WC_CONSOLE_PORT=COM8`、`WC_CONSOLE_LOG=<日志路径>`）。
- `deploy.sh` 的机器相关设置全部走环境变量，默认值对应我们的开发机：

  ```bash
  ROOT=/path/to/openvela \
  SFTOOL=/path/to/sftool.exe \
  PORT=COM8 BAUD=1000000 \
  PY=python3 tests/deploy.sh
  ```

- `gen_font.js` 需要 `lv_font_conv`：`npm i -g lv_font_conv`，或设
  `LV_FONT_CONV=/path/to/lv_font_conv.js`；字体与符号表可作参数传入：

  ```bash
  python3 tests/gen_font_symbols.py tests/font_symbols.txt   # ① 生成符号表
  node tests/gen_font.js /path/to/simhei.ttf tests/font_symbols.txt  # ② 生成字体
  ```

  产物直接写回 `app/wristclaw/wc_font_16.h`，**不需要改 CMakeLists.txt**
  （该文件以头文件形式被 `wc_ui.c` include）。
