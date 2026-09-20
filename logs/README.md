# logs/ — AI Coding 日志

本目录存放**本机 AI 工具真实会话**的导出记录，与作品代码一并提交。

## 目录结构

```text
logs/
└── yilives/                                  # GitHub 用户名，一人一目录
    ├── manifest.json                         # 会话清单（会话数 / 事件数 / 时间跨度）
    └── <date>/                               # 会话开始日期 YYYY-MM-DD
        └── <tool>__<sid>.jsonl               # 一个会话一个文件
```

## 本次提交的会话

| 会话 | 时间跨度 | 事件数 |
| --- | --- | --- |
| `4708967e-15a2-4030-aaaa-9281c51db74e` | 2026-09-17 → 09-20 | 3961 |
| `ab4c7d38-6ef5-40e4-8e5d-3b99241ebc76` | 2026-09-20 | 214 |

## 一行一个事件

字段遵循[《AI Coding 日志归集与提交手册》](https://github.com/open-vela/docs/blob/dev-ai-contest-2026/zh-cn/contest_2026/ai_coding_log_guide.md)
给出的示例格式：

```json
{"schema_version":"1.0","session_id":"...","team_id":"...","github_login":"yilives",
 "tool":"mimocode","seq":1,"ts":"2026-09-20T14:56:17.207Z","role":"assistant",
 "text":"…","thinking":"…","model":"Deepseek-V4.1-Flash"}
{"schema_version":"1.0","session_id":"...","tool":"mimocode","seq":2,
 "ts":"2026-09-20T14:56:17.237Z","role":"tool","tool_name":"Read",
 "tool_call_id":"call_00_…","input":{"file_path":"…"},"output":{"text":"…"},
 "files_touched":["…"]}
```

- `role`：`user` / `assistant` / `tool`
- `seq`：**会话内**从 0 连续递增，无断档
- `ts`：该事件的**真实本机时间**（UTC，毫秒精度）
- `model`：实际使用的模型名
- `thinking`：模型的思考过程

## 导出方式（可复现）

日志由本仓 `tools/export_logs.py` 从 AI 工具的本机 session transcript
（`<home>/.workbuddy/projects/<project-slug>/*.jsonl`）忠实导出：

```bash
python tools/export_logs.py --dry-run   # 只统计，不写盘
python tools/export_logs.py             # 导出到 logs/
```

导出器**只做搬运，不改写**：

- 时间戳、角色、模型名、工具名与入参/出参全部取自原始记录；
- `seq` 按真实时间顺序重排后连续编号，因此可用于一致性校验；
- 超长字段（编译日志、串口 dump 等）按上限截断，并在原地追加
  `...[truncated N chars]` 显式标注 —— 截断是标注过的，不是改写；
- 导出前自动遮蔽疑似密钥（`sk-…` / `Authorization: Bearer …` / `api_key=`）。

> 本目录**不含**任何人工编写或事后拼接的事件。若需撤回某次对话，
> 在 `git commit` 前删掉对应 `.jsonl` 并在 `manifest.json` 中移除该条即可。
