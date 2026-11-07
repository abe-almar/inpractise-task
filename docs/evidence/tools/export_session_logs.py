"""Copy the Claude Code session transcripts of this project into agent-logs/, redacted.

The brief asks for the agent session logs. The transcripts were produced on a machine that also serves other
projects, and Claude Code injects per-machine memory and reminders into the conversation as <system-reminder>
blocks. Those blocks are removed; everything the agent itself read, wrote, ran or said is kept. A final scan refuses
to write a file that still contains a word from the blocklist, so nothing unrelated leaks by accident.

    .venv/bin/python docs/evidence/tools/export_session_logs.py <session.jsonl> [...] --out agent-logs/claude-code
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REMINDER = re.compile(r"<system-reminder[^>]*>.*?</system-reminder>", re.S)
BLOCKLIST_FILE = Path(__file__).with_name("export_blocklist.txt")  # one lowercase word per line, not committed


def scrub(value):
    if isinstance(value, str):
        return REMINDER.sub("[system reminder removed]", value)
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    return value


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    blocklist = [w.strip().lower() for w in BLOCKLIST_FILE.read_text().splitlines() if w.strip()] if BLOCKLIST_FILE.is_file() else []
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name in args.files:
        src = Path(name)
        kept = dropped = 0
        lines = []
        for raw in src.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                dropped += 1
                continue
            entry = scrub(entry)
            lines.append(json.dumps(entry, ensure_ascii=False))
            kept += 1
        text = "\n".join(lines) + "\n"
        lowered = text.lower()
        hits = sorted({w for w in blocklist if re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", lowered)})
        if hits:
            print(f"{src.name}: NOT written, blocklisted words still present: {', '.join(hits)}", file=sys.stderr)
            return 1
        (out / src.name).write_text(text, encoding="utf-8")
        print(f"{src.name}: {kept} entries kept, {dropped} unparsable lines dropped, {len(text)//1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
