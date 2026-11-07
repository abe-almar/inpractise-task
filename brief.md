# SEC filings take-home (In Practise, data team)

Followed by a 60-minute call.

## The task

Using AI agents as you normally would, build a job that keeps a table of every annual (10-K) and quarterly (10-Q) report these 20 companies have filed with the SEC. For each report, record which fiscal year or quarter it covers, so we can ask "what do we have for Apple's 2009 annual report?" and get one clear answer.

AAPL MSFT JPM XOM WMT KO GE F T TSLA PLUG GME AMC CVNA LCID SAP SHEL BABA NVO TM

Source: EDGAR, free, no key. Start at https://data.sec.gov/submissions/. Fair-access rules: https://www.sec.gov/os/accessing-edgar-data

## Send us five things

1. **The job.** Python, with a local Postgres database. Read the connection from a `DATABASE_URL` environment variable, and include a README with the one command that runs it. It must be safe to run every morning. Our jobs run on Prefect, so shape it as something a scheduler could call, but don't install Prefect.
2. **The morning check.** If tomorrow's 06:00 run is wrong, what tells us? "It didn't crash" is not an answer.
3. **The handover kit.** Instructions and checks that let an AI agent run, extend or fix this job later without you, for example adding five new companies next quarter. Tell the agent what decisions you made and what it must check before it says it's done. We'll test it by handing it to a fresh agent.
4. **What you didn't trust (half a page).** Where was the agent wrong? How did you find out? What did you check it against? Include any decisions you made about the data, and why.
5. **An analyst's question (half a page).** An analyst asks: "Which of these companies have filed an annual report late, or had to correct one, in the last 10 years?" Can your table answer that? If yes, give the answer and say how sure you are. If not, say what's missing and what you'd add.

Please also send your agent session logs, if you're comfortable sharing them (for Claude Code, the `.jsonl` files under `~/.claude/projects/`).

## Ground rules

- Use agents, exactly as you would at work. We're not testing whether you type code; we're testing whether you know what the agent got wrong.
- If the brief leaves something open, make the call yourself and write down why. There's no need to wait for us.

## How to submit

Reply to the email that sent you this brief with a link to a Git repository or a zip file.
