#!/usr/bin/env python3
"""把本机 AI 工具的真实会话 transcript 导出成大赛要求的 logs/ JSONL。

背景（为什么必须这么干）：
  《AI Coding 日志归集与提交手册》Q3 明确写「validate-log.py 会检测序号断档或
  内容篡改行为。修改日志内容会被视为作弊」。仓库里原先由
  logs/migrate_all_sessions.py 生成的那批日志是**人工编写的事件**（所有事件共用
  同一个 ts、行数与 manifest 不一致），属于手册定义的风险项。
  本脚本改为从工具本机 transcript 忠实导出：时间戳、角色、模型、工具调用全部取自
  真实记录，不做任何内容改写。

字段遵循官方示例 logs/your-github-login/2026-01-01/claude-code__example.jsonl：
  schema_version / session_id / team_id / github_login / tool / seq / ts / role
    role=user      -> text
    role=assistant -> text, thinking, model, tokens_in, tokens_out
    role=tool      -> tool_name, tool_call_id, input, output, files_touched

用法：
    python tools/export_logs.py --dry-run          # 只统计，不写文件
    python tools/export_logs.py                    # 正式导出到 <repo>/logs/
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

TEAM_ID = "contest2026_458_conglingkaishideopenvelashenghuo"
GITHUB_LOGIN = os.environ.get("WC_GITHUB_LOGIN", "yilives")
TOOL = os.environ.get("WC_LOG_TOOL", "mimocode")

# 本机 transcript 目录（WorkBuddy / mimocode 的 projects/<slug>/*.jsonl）
HOME = os.path.expanduser("~")
TRANSCRIPT_DIR = os.environ.get(
    "WC_TRANSCRIPT_DIR",
    os.path.join(HOME, ".workbuddy", "projects", "d-sicelcd"))

# 单字段上限：超长工具输出（编译日志、串口 dump）会截断并显式标注，
# 避免仓库被几十 MB 的日志撑爆。截断是"标注过的"，不是改写。
MAX_TEXT = 6000
MAX_OUTPUT = 3000

REMINDER_RE = re.compile(r"<system-reminder\b.*?</system-reminder>", re.S)
SECRET_RES = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), "sk-***REDACTED***"),
    (re.compile(r"(?i)(authorization\s*:\s*bearer\s+)\S+"), r"\1***REDACTED***"),
    (re.compile(r"(?i)(api[_-]?key\"?\s*[:=]\s*\"?)[A-Za-z0-9_\-]{16,}"),
     r"\1***REDACTED***"),
]


def clip(s, limit):
    if s is None:
        return None
    s = s if isinstance(s, str) else str(s)
    for rx, rep in SECRET_RES:
        s = rx.sub(rep, s)
    if len(s) > limit:
        return s[:limit] + "\n...[truncated %d chars]" % (len(s) - limit)
    return s


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc) \
        .strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (ms % 1000)


def collect_text(content, kinds):
    out = []
    for part in content or []:
        if part.get("type") in kinds and part.get("text"):
            out.append(part["text"])
    return "\n".join(out)


def walk_files(args):
    """从工具入参里捞出它碰过的文件路径（尽力而为）"""
    found = []
    if not isinstance(args, dict):
        return None
    for key in ("file_path", "path", "notebook_path", "file", "target_file"):
        v = args.get(key)
        if isinstance(v, str) and v:
            found.append(v)
    for key in ("target_files", "files", "paths"):
        v = args.get(key)
        if isinstance(v, list):
            found += [x for x in v if isinstance(x, str)]
    return found or None


def load_events(path):
    evs = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                evs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    evs.sort(key=lambda e: e.get("timestamp") or 0)
    return evs


def convert(events, session_id):
    """把原始事件流转成官方 schema 的记录序列（按真实时间顺序）"""
    # 1) 汇总工具调用：callId -> {name, args, output}
    calls = {}
    for e in events:
        t = e.get("type")
        if t == "function_call":
            calls[e.get("callId")] = {
                "name": e.get("name"),
                "args": e.get("arguments"),
                "out": None,
                "ts": e.get("timestamp"),
            }
        elif t == "function_call_result":
            c = calls.get(e.get("callId"))
            o = e.get("output") or {}
            txt = o.get("text") if isinstance(o, dict) else o
            if c is not None:
                c["out"] = txt
            else:
                calls[e.get("callId")] = {"name": e.get("name"), "args": None,
                                          "out": txt, "ts": e.get("timestamp")}

    # 2) 按「模型回合」聚合：reasoning + assistant 文本
    turns = {}          # messageId -> {texts, thinks, model, ts}
    records = []        # (ts, order, record)

    for e in events:
        t = e.get("type")
        pd = e.get("providerData") or {}
        ts = e.get("timestamp")
        if t == "message" and e.get("role") == "user":
            txt = collect_text(e.get("content"), ("input_text", "text"))
            txt = REMINDER_RE.sub("", txt).strip()
            if txt:
                records.append((ts, 0, {
                    "role": "user",
                    "text": clip(txt, MAX_TEXT),
                }))
        elif t == "message" and e.get("role") == "assistant":
            mid = pd.get("messageId") or e.get("id")
            turn = turns.setdefault(mid, {"texts": [], "thinks": [],
                                          "model": None, "ts": ts})
            txt = collect_text(e.get("content"), ("output_text", "text"))
            if txt:
                turn["texts"].append(txt)
            turn["model"] = pd.get("requestModelName") or pd.get("model") \
                or turn["model"]
            turn["ts"] = turn["ts"] or ts
        elif t == "reasoning":
            mid = pd.get("messageId")
            turn = turns.setdefault(mid, {"texts": [], "thinks": [],
                                          "model": None, "ts": ts})
            th = collect_text(e.get("rawContent"), ("reasoning_text",))
            if th:
                turn["thinks"].append(th)
            turn["model"] = pd.get("requestModelName") or pd.get("model") \
                or turn["model"]
            turn["ts"] = turn["ts"] or ts

    # 3) 把回合和工具调用按时间交织
    inter = []
    for mid, turn in turns.items():
        if not (turn["texts"] or turn["thinks"]):
            continue
        rec = {"role": "assistant"}
        if turn["texts"]:
            rec["text"] = clip("\n".join(turn["texts"]), MAX_TEXT)
        if turn["thinks"]:
            rec["thinking"] = clip("\n".join(turn["thinks"]), MAX_TEXT)
        if turn["model"]:
            rec["model"] = turn["model"]
        inter.append((turn["ts"] or 0, 1, rec))

    for cid, c in calls.items():
        if c["out"] is None and not c["args"]:
            continue
        rec = {"role": "tool", "tool_name": c["name"] or "tool",
               "tool_call_id": cid}
        if c["args"]:
            try:
                parsed = json.loads(c["args"])
            except (json.JSONDecodeError, TypeError):
                parsed = clip(c["args"], MAX_OUTPUT)
            rec["input"] = parsed if isinstance(parsed, dict) \
                else {"raw": clip(str(parsed), MAX_OUTPUT)}
        if c["out"] is not None:
            rec["output"] = {"text": clip(c["out"], MAX_OUTPUT)}
        ft = walk_files(rec.get("input"))
        if ft:
            rec["files_touched"] = ft
        inter.append((c["ts"] or 0, 2, rec))

    inter += [(ts, 3, rec) for ts, _o, rec in records]

    inter.sort(key=lambda x: (x[0], x[1]))
    return inter


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_root = args.out or os.path.join(repo, "logs")

    files = sorted(f for f in os.listdir(TRANSCRIPT_DIR)
                   if f.endswith(".jsonl"))
    if not files:
        print("找不到 transcript：%s" % TRANSCRIPT_DIR)
        return 1

    manifest_sessions = []
    total = 0
    for fn in files:
        path = os.path.join(TRANSCRIPT_DIR, fn)
        sid = os.path.splitext(fn)[0].split(".")[0]
        events = load_events(path)
        if not events:
            continue
        inter = convert(events, sid)
        if not inter:
            continue

        first_ms = inter[0][0]
        last_ms = inter[-1][0]
        date = datetime.fromtimestamp(first_ms / 1000).strftime("%Y-%m-%d")

        out_dir = os.path.join(out_root, GITHUB_LOGIN, date)
        out_file = os.path.join(out_dir, "%s__%s.jsonl" % (TOOL, sid))
        rel = os.path.relpath(out_file, repo).replace(os.sep, "/")

        print("%-40s %5d 条  %s -> %s  (%s)"
              % (fn[:40], len(inter), iso(first_ms)[:10], iso(last_ms)[:10],
                 os.path.getsize(path) / 1024 / 1024 and
                 "%.1fMB 原始" % (os.path.getsize(path) / 1024 / 1024)))

        if not args.dry_run:
            os.makedirs(out_dir, exist_ok=True)
            with open(out_file, "w", encoding="utf-8") as fh:
                for i, (_ts, _o, rec) in enumerate(inter):
                    line = {
                        "schema_version": "1.0",
                        "session_id": sid,
                        "team_id": TEAM_ID,
                        "github_login": GITHUB_LOGIN,
                        "tool": TOOL,
                        "seq": i,
                        "ts": iso(inter[i][0]),
                    }
                    line.update(rec)
                    fh.write(json.dumps(line, ensure_ascii=False) + "\n")

        manifest_sessions.append({
            "session_id": sid,
            "tool": TOOL,
            "started_at": iso(first_ms),
            "last_event_at": iso(last_ms),
            "event_count": len(inter),
            "file_path": rel,
            "collection_mode": "cli",
            "health": "ok",
        })
        total += len(inter)

    if args.dry_run:
        print("\n[dry-run] 共 %d 条事件，未写盘" % total)
        return 0

    manifest = {
        "schema_version": "1.0",
        "team_id": TEAM_ID,
        "github_login": GITHUB_LOGIN,
        "generator": TOOL,
        "sessions": manifest_sessions,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    mpath = os.path.join(out_root, GITHUB_LOGIN, "manifest.json")
    os.makedirs(os.path.dirname(mpath), exist_ok=True)
    with open(mpath, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
    print("\n导出 %d 个会话 / %d 条事件" % (len(manifest_sessions), total))
    print("manifest: %s" % mpath)
    return 0


if __name__ == "__main__":
    sys.exit(main())
