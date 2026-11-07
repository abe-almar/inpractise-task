# How the agents were set up and used

The brief asks to use agents as one would at work and to show what they got wrong. This is the setup.

## Three roles

| role | tool | model | what it did |
|---|---|---|---|
| lead / implementer | Claude Code (Claude Fable 5.1) in this repository | claude-fable-5-1 | read the brief, probed EDGAR, made and logged the decisions, wrote the job, ran it, wrote these documents; delegated the test suite to a sub-agent with a precise brief |
| independent auditor | Hermes agent, a dedicated profile `takehome` | gpt-6.1-sol (OpenAI Codex), reasoning max, with its own sub-agents at xhigh | recomputed 183 filings of four companies through EDGAR's Atom feeds and confirmed five periods on index pages (verdict MATCH); separately, with no persona or memory injected, played the "fresh agent" that must add five companies from the handover kit alone: it loaded them with their predecessor CIKs, stopped on a Disney filing the kit could not correct, and proposed nine kit changes, all adopted |
| adversarial reviewer | Hermes agent, a dedicated profile `takehomereviewer` | gpt-6.1-sol, report-only | reviewed the job, the morning check and the kit against the brief (pass 1: FAIL, nine major findings, seven fixed in code, two stated as limitations), then the delta; first line of every report is a verdict |

The two Hermes profiles were created for this task only (cloned from generic coder/reviewer profiles, then given a
task-specific persona and a clean memory). Their personas are reproduced in `docs/agent-personas.md`; their reports
are in `docs/review-evidence/` and `docs/agent-runs/`.

## Rules the agents worked under

- Evidence first: a claim about a filing, a date or an SEC rule comes with the query or request that shows it.
- EDGAR fair access: declared user agent with a contact address, gzip, five requests per second, no crawling.
- Decisions go in `docs/decisions.md` with alternatives and the check behind them, before the code.
- Review passes are delta passes after the first: the reviewer gets the previous findings and the changed files,
  not the whole repository again.
- No agent output was trusted on its own: labels were cross-checked against the SEC's XBRL labels, counts against
  independent queries, the test suite against hand-computed expectations.

## Session logs

Claude Code session transcripts (`.jsonl`) and the Hermes session exports are in `agent-logs/`, redacted only of
unrelated material from the same machine. They show the probes, the mistakes and the corrections in order.
