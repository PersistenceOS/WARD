"""Analyze the Complete-FABLE.5-traces-2M parquet corpus.

Reads the mirrored Claude trace parquet files and classifies each row into
one of three shapes observed in the corpus:

  - "events": raw Claude Code session events (dict with `sessionId`),
    e.g. 1EYE4ALL/Fable-5-traces, armand0e/claude-fable-5-claude-code.
  - "conversation": multi-turn message lists (dict with `messages`),
    e.g. Roman1111111/claude-sonnet-*, greghavens/fable-5-*,
    Glint-Research/Fable-5-traces, Swarm-AI-Research, TeichAI/*.
  - "qa": single-turn prompt/response or completion/cot rows
    (dict with prompt+response or completion/cot/output_type),
    e.g. TheFusionCube/Fable-5-CoT-Traces, PawanKrd/claude-fable-5-code.
  - "other": anything that fits none of the above.

Language/tool markers are scanned on every row regardless of shape, so the
counts are corpus-wide. For the greghavens source the explicit `lang`,
`domain`, `verifier`, and `task` fields are collected separately because they
are the cleanest signal for "how many rows could seed a benchmark task".

Usage:
    python fable_analysis.py --data-dir <path-to-data> [--sample 2000]
"""

import argparse
import collections
import json
import re
import sys
from pathlib import Path

import pyarrow.parquet as pq

# ---------------------------------------------------------------- heuristics

TOOL_USE_RE = re.compile(r"toolu_[a-zA-Z0-9_]+|\"tool_use\"|\"tool_result\"|ToolCall|Bash\(|Write\(|Edit\(")
LANG_MARKERS = [
    ("python", re.compile(r"```(?:py|python)\b|\.py\b|def \w+\(|import \w+")),
    ("typescript", re.compile(r"```(?:ts|typescript)\b|\.tsx?\b|interface \w+|export (?:const|function|class)")),
    ("javascript", re.compile(r"```(?:js|javascript)\b|\.js\b|require\(|module\.exports|=>")),
    ("go", re.compile(r"```go\b|\.go\b|func \w+\(|package main")),
    ("rust", re.compile(r"```rust\b|\.rs\b|fn \w+\(|use \w+::")),
    ("java", re.compile(r"```java\b|\.java\b|public class|public static")),
    ("c/c++", re.compile(r"```(?:c|cpp|c\+\+|h)\b|\.(?:c|cpp|h|hpp)\b|#include <")),
    ("csharp", re.compile(r"```(?:csharp|cs)\b|\.cs\b|using System|namespace \w+")),
    ("html/css", re.compile(r"```(?:html|css)\b|<div|</?html|<style")),
    ("sql", re.compile(r"```sql\b|\.sql\b|CREATE TABLE|SELECT \* FROM|INSERT INTO")),
    # require a strong signal so bare `ensures`/`requires` words don't fire
    # on every code trace
    ("dafny", re.compile(r"```dafny\b|\.dfy\b|datatype \w+ = |method \w+.*returns")),
    ("lean", re.compile(r"```lean\b|\.lean\b|theorem \w+|example \w+ :")),
]

def classify_row(d: dict) -> str:
    """Return 'events' | 'conversation' | 'qa' | 'other'."""
    if "sessionId" in d:
        return "events"
    if isinstance(d.get("messages"), list):
        return "conversation"
    if "completion" in d or "cot" in d or "output_type" in d:
        return "qa"
    if "prompt" in d or "response" in d:
        return "qa"
    return "other"


def scan_blob(blob: str, lang_counter: collections.Counter, stats: dict) -> None:
    """Corpus-wide markers, shape-agnostic."""
    if TOOL_USE_RE.search(blob):
        stats["tool_use_rows"] += 1
    for lang, pat in LANG_MARKERS:
        if pat.search(blob):
            lang_counter[lang] += 1


def collect_session_id(d: dict) -> str | None:
    for k in ("sessionId", "session_id", "source_trajectory_id"):
        v = d.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description="FABLE 5 trace corpus analysis")
    ap.add_argument("--data-dir", required=True, help="path to the data/ directory with train-*.parquet")
    ap.add_argument("--sample", type=int, default=0, help="only analyze the first N rows per file (0 = all)")
    ap.add_argument("--out", default=None, help="write the JSON report here (default: stdout)")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    files = sorted(data_dir.glob("train-*.parquet"))
    if not files:
        print("no parquet files found", file=sys.stderr)
        sys.exit(1)

    per_source = collections.Counter()
    per_source_kind = collections.defaultdict(collections.Counter)
    row_kinds = collections.Counter()
    lang_counter = collections.Counter()
    sessions = set()
    session_msgs = collections.Counter()
    # pseudo-sessions for id-less conversation rows (row_hash = one row per session)
    idless_convo_rows = 0
    stats = {
        "tool_use_rows": 0,
        "events": collections.Counter(),
        "convo_n_messages": [],
        "explicit_lang": collections.Counter(),
        "explicit_domain": collections.Counter(),
        "explicit_verifier": collections.Counter(),
        "explicit_tasks": collections.Counter(),
        "explicit_tools": collections.Counter(),
    }

    total = 0
    parse_fail = 0
    for f in files:
        table = pq.read_table(f, columns=["first_source_dataset", "row_json", "row_hash"])
        if args.sample:
            table = table.slice(0, args.sample)
        sources = table.column("first_source_dataset").to_pylist()
        blobs = table.column("row_json").to_pylist()
        hashes = table.column("row_hash").to_pylist()
        for src, blob, row_hash in zip(sources, blobs, hashes):
            total += 1
            per_source[src] += 1
            try:
                d = json.loads(blob)
            except Exception:
                parse_fail += 1
                continue
            if not isinstance(d, dict):
                row_kinds["other"] += 1
                per_source_kind[src]["other"] += 1
                scan_blob(blob, lang_counter, stats)
                continue

            kind = classify_row(d)
            row_kinds[kind] += 1
            per_source_kind[src][kind] += 1

            if kind == "events":
                stats["events"][str(d.get("type", ""))] += 1
                sid = collect_session_id(d)
                if sid:
                    sessions.add(sid)
                    session_msgs[sid] += 1
            elif kind == "conversation":
                msgs = d.get("messages")
                if isinstance(msgs, list):
                    stats["convo_n_messages"].append(len(msgs))
                sid = collect_session_id(d)
                if sid:
                    sessions.add(sid)
                    session_msgs[sid] += 1
                else:
                    # id-less conversation row: treat the row itself as a session
                    idless_convo_rows += 1
                # explicit structured fields (greghavens source)
                for field, counter in (
                    ("lang", stats["explicit_lang"]),
                    ("domain", stats["explicit_domain"]),
                    ("verifier", stats["explicit_verifier"]),
                    ("task", stats["explicit_tasks"]),
                ):
                    v = d.get(field)
                    if isinstance(v, str) and v.strip():
                        counter[v.strip()[:80]] += 1
                tools = d.get("tools_used")
                if not isinstance(tools, list):
                    tools = d.get("tools")  # greghavens 2nd shape uses `tools`
                if isinstance(tools, list):
                    for t in tools:
                        if isinstance(t, str):
                            stats["explicit_tools"][t] += 1
            elif kind == "qa":
                sid = collect_session_id(d)
                if sid:
                    sessions.add(sid)
                    session_msgs[sid] += 1

            scan_blob(blob, lang_counter, stats)

    def pct(q, n):
        return round(100.0 * q / n, 1) if n else 0.0

    convo_n = stats["convo_n_messages"]
    # sessions: explicit ids + id-less conversation rows (each such row is one session)
    total_sessions = len(sessions) + idless_convo_rows
    report = {
        "rows_total": total,
        "rows_parse_failed": parse_fail,
        "row_kinds": dict(row_kinds),
        "row_kind_pct": {k: pct(v, total) for k, v in row_kinds.items()},
        "files_analyzed": [f.name for f in files],
        "sessions": {
            "with_explicit_id": len(sessions),
            "idless_conversation_rows": idless_convo_rows,
            "total_estimated": total_sessions,
        },
        "row_stats": {
            "tool_use_rows": stats["tool_use_rows"],
            "tool_use_pct": pct(stats["tool_use_rows"], total),
            "event_types": dict(stats["events"].most_common(10)),
            "events_per_session_median": _median(list(session_msgs.values())),
            "conversation_rows": len(convo_n),
            "conversation_msg_median": _median(convo_n),
        },
        "explicit_fields": {
            "lang": dict(stats["explicit_lang"].most_common(20)),
            "domain": dict(stats["explicit_domain"].most_common(20)),
            "verifier": dict(stats["explicit_verifier"].most_common(20)),
            "rows_with_task": sum(stats["explicit_tasks"].values()),
            "task_shapes": dict(stats["explicit_tasks"].most_common(15)),
            "tools_used": dict(stats["explicit_tools"].most_common(20)),
        },
        "language_hits": dict(lang_counter.most_common(20)),
        "per_source_rows": dict(per_source.most_common()),
        "per_source_kinds": {k: dict(v) for k, v in sorted(per_source_kind.items())},
    }

    out = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        print(f"report written to {args.out}")
    else:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(out)


def _median(vals: list[int]) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    m = len(s) // 2
    if len(s) % 2:
        return float(s[m])
    return (s[m - 1] + s[m]) / 2.0


if __name__ == "__main__":
    main()
