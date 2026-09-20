# contest_board —— 板级适配说明

## 结论先说

本作品跑在 **SF32LB52-DevKit-LCD** 上，这块板子的板级支持由 **openvela 的 SiFli
vendor 层**提供：

```
vendor/sifli/boards/sf32lb52/sf32lb52_devkit_lcd/
```

因此**本目录不重复提供驱动代码**，只固化「我们实机构建时用的那份配置」与
板级差异说明，便于评审复现。

## 本目录内容

| 文件 | 说明 |
| --- | --- |
| `configs/nsh/defconfig` | **实机使用的 defconfig，逐字复制自** `vendor/sifli/boards/sf32lb52/sf32lb52_devkit_lcd/configs/nsh/defconfig`（顶部有来源说明）。评审对照它即可知道我们开了哪些配置。 |
| `CMakeLists.txt` / `Kconfig` / `src/board_boot.c` | 组委会模板附带的板级骨架样例（占位，非本作品使用路径），保留以备参考。 |

## 相对同 SoC 的黄山派（sf32lb52-lchspi-ulp）的差异

| 功能 | 本板 | -ULP（黄山派） |
| --- | --- | --- |
| 触摸 I2C SCL | PA30 | PA37 |
| 触摸 IRQ | PA31 | PA41 |
| KEY2 | PA11 | PA43 |
| LCD VADD_EN | PA37 | PA01 |
| PA01 | **背光 PWM**（GPTIM1_CH4） | VADD_EN |
| 音频 PA EN | PA10 | PA42 |
| UART2 调试 log | PA20 / PA27 | PA27 被 TF 卡 detect 占用 |

其余：UART1 控制台 PA18/PA19；触摸 I2C SDA PA33、INT/RST PA31/PA09；
QSPI LCD CS/CLK/TE = PA03/PA04/PA02，D0–D3 = PA05–PA08；LCD reset PA00；
KEY1 PA34；RGB LED PA32。

> 22-pin LCD FPC 遵循思澈 SF-DevKit-LCM-Adapter 规范（同时引出 QSPI 与 8080 MCU
> 走线）。当前 nsh defconfig **只用 QSPI**。

## 本作品对 defconfig 的改动（都在上面那份文件里）

1. `CONFIG_LVX_USE_DEMO_CONTEST2026_458_WRISTCLAW=y` —— 编入板端 Agent 应用。
2. `CONFIG_LV_FONT_MONTSERRAT_16/20/28=y` —— 表盘大号数字、图标 fallback。
3. `CONFIG_LV_FONT_SIMSUN_16_CJK=y` —— 保留（实际中文由 `wc_font_16.h`
   自生成全量字体提供，见 `docs/DEMO.md` 第十一节）。
4. CDC-ACM 端点显式绑定：`EPINTIN=5`、`EPBULKIN=1`、`EPBULKOUT=2`
   （本芯片端点方向硬固定，详见 `docs/BUILD.md` 第四节第 5 条）。
5. `VID/PID = 0x38F4 / 0xA4A7`，上位机据此自动找口。

## 是否完成全新硬件平台适配？

**否**（复用官方板级支持）。本作品的适配工作集中在
**应用层 + 板级配置取舍**，以及把 openvela 图形能力在可穿戴形态下落地的验证。
若要做真正的端侧音频（唤醒词/TTS），需要在
`vendor/sifli/boards/.../src/` 新增 NuttX audio 下层驱动（PDM 麦 + codec 放音 +
DMA 环形缓冲）并注册 `/dev/audio`，这是赛期首要迭代项。
