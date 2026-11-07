"""Copy the Claude Code session transcripts of this project into agent-logs/, redacted.

The brief asks for the agent session logs. The transcripts were produced on a machine that also serves other
projects, and the harness injects per-machine memory and reminders into the conversation. What is removed, and only
this:

1. Harness bookkeeping entries that are not the conversation (`attachment`, `last-prompt`, `api-request-blob`,
   `file-history-delta`): they duplicate requests and carry the injected per-machine context.
2. `<system-reminder>` blocks inside messages (injected memory and reminders).
3. Tool results whose text contains a word from a private blocklist of unrelated projects and people
   (for example the output of reading this machine's memory files): the whole result is replaced by a marker.
4. Tool calls that edit this machine's memory files: the call's input is replaced by a marker.
5. Any remaining blocklisted word in the agent's own text or commands: replaced by `[redacted]`.

Everything the agent read, wrote, ran and said about this task is kept. The script refuses to write a file that
still contains a blocklisted word.

    .venv/bin/python docs/evidence/tools/export_session_logs.py <session.jsonl> [...] --out agent-logs/claude-code
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

REMINDER = re.compile(r"<system-reminder[^>]*>.*?</system-reminder>", re.S)
DROP_ENTRY_TYPES = {"attachment", "last-prompt", "api-request-blob", "file-history-delta"}
MEMORY_PATH_MARK = "/memory/"  # tool calls touching this machine's memory files are not part of the task
BLOCKLIST_FILE = Path(__file__).with_name("export_blocklist.txt")  # one lowercase word per line, not committed
RESULT_MARK = "[tool result removed: unrelated material from the same machine]"
CALL_MARK = "[tool call removed: edit of this machine's memory files, unrelated to the task]"


def load_blocklist() -> list[re.Pattern]:
    if not BLOCKLIST_FILE.is_file():
        return []
    words = [w.strip().lower() for w in BLOCKLIST_FILE.read_text().splitlines() if w.strip()]
    return [re.compile(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", re.I) for w in words]


def hits(text: str, patterns: list[re.Pattern]) -> bool:
    return any(p.search(text) for p in patterns)


def redact_words(value, patterns, stats):
    if isinstance(value, str):
        out = REMINDER.sub("[system reminder removed]", value)
        for p in patterns:
            out, n = p.subn("[redacted]", out)
            stats["words"] += n
        return out
    if isinstance(value, list):
        return [redact_words(v, patterns, stats) for v in value]
    if isinstance(value, dict):
        return {k: redact_words(v, patterns, stats) for k, v in value.items()}
    return value


def scrub_entry(entry: dict, patterns: list[re.Pattern], stats: Counter) -> dict:
    msg = entry.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("content"), list):
        blocks = []
        for b in msg["content"]:
            if not isinstance(b, dict):
                blocks.append(redact_words(b, patterns, stats))
                continue
            kind = b.get("type")
            raw = REMINDER.sub("", json.dumps(b, ensure_ascii=False))
            if kind == "tool_result" and hits(raw, patterns):
                stats["tool_results_removed"] += 1
                blocks.append({**b, "content": RESULT_MARK})
                continue
            if kind == "tool_use":
                inp = b.get("input") or {}
                path = str(inp.get("file_path", "")) if isinstance(inp, dict) else ""
                if MEMORY_PATH_MARK in path:
                    stats["memory_edits_removed"] += 1
                    blocks.append({**b, "input": {"note": CALL_MARK}})
                    continue
            blocks.append(redact_words(b, patterns, stats))
        entry = {**entry, "message": {**msg, "content": blocks}}
    return redact_words(entry, patterns, stats)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    patterns = load_blocklist()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name in args.files:
        src = Path(name)
        stats: Counter = Counter()
        lines = []
        for raw in src.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                stats["unparsable"] += 1
                continue
            if entry.get("type") in DROP_ENTRY_TYPES:
                stats["bookkeeping_dropped"] += 1
                continue
            lines.append(json.dumps(scrub_entry(entry, patterns, stats), ensure_ascii=False))
            stats["kept"] += 1
        text = "\n".join(lines) + "\n"
        leftovers = [p.pattern for p in patterns if p.search(text)]
        if leftovers:
            print(f"{src.name}: NOT written, blocklisted words still present: {len(leftovers)}", file=sys.stderr)
            return 1
        (out / src.name).write_text(text, encoding="utf-8")
        print(f"{src.name}: {dict(stats)}, {len(text)//1024} KB written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
