# What I didn't trust

The agents were a Claude Code session doing the work, a sub-agent writing tests, and two Hermes profiles auditing
and reviewing. Where they (and I, reading them) were wrong, how it came out, and what it was checked against.

**Exxon's two CIKs.** My probe note said both had filed a Q2 2026 10-Q. The backfill printed one filing as "seen
again": the same accession number sits on both pages. Checked against the two submissions files: one combined
filing by two registrants. It became an `also_filed_by_ciks` column, not a second row.

**Four companies with two "original" annual reports for one year.** The `labels_unique` check failed on the first
backfill. Cause: for 19 old filings EDGAR's "period of report" is the filing date itself; GE's 10-K filed
2015-02-27 says "fiscal year ended December 31, 2014" on its cover. Fix: label from the last period end before the
filing date. Two more bugs showed only in a passing check's printout: Microsoft's next quarter end as 30 December,
and Shell held to a 60-day deadline because EDGAR calls it "Large accelerated filer" although a 20-F has four months.

**The SEC's own fiscal labels disagreed on 106 of 1,017 filings.** 101 are issuers naming the year by its start
(GameStop always, Walmart until 2014, Toyota until 2017); four are SEC errors, checked on the cover pages (AT&T's
"quarterly period ended September 30" tagged Q2, twice; GE's 10-K tagged Q4; a Q1 10-Q tagged 2019 Q3). One rule,
the SEC label kept beside it, conventions listed with evidence, the four errors accepted.

**"Late filers" from today's filer category were mostly wrong.** Nine annual reports came out late under a large
accelerated filer's 60 days. The nine cover pages say AMC and Plug were "accelerated" (75 days) in those years,
Lucid's SPAC and Carvana's first 10-K "non-accelerated" (90 days). One was late: Plug's fiscal 2020 10-K, 59 days.

**A keyword classifier called Tesla's Part III amendments "restatements"** (clawback boilerplate). Replaced by
quoting each explanatory note: of 33 amendments in ten years, one restated the financial statements.

**The adversarial reviewer failed the work four times before passing it.** Pass 1 found, among nine findings: the
disappearance alarm fired once and went quiet; an empty EDGAR page counted as fetched; "any one-year difference is
a naming convention" would have excused an off-by-one bug in my own rule; acceptance ran on a truncated list; the
20-F deadline was 120 days, not four months. Passes 2 to 4 each caught a defect in the previous fix: a "last good
run" baseline that first faded the outage alarm, then made recovery impossible, then an initialisation that cleared
an inherited gap. Coverage is now a per-filer watermark, exercised live on an outage
(`docs/evidence/outage-recovery-2026-11-07.log`).

**The fresh-agent test found what the kit could not express.** With no persona, it added five companies, found the
Google and Disney predecessor CIKs on its own, and stopped, correctly, on a Disney 10-Q whose EDGAR period is a year
off: the kit had no per-filing correction. That became `[[filing_corrections]]`, which then fixed an error of my own
(a 1997 Apple amendment inferred as Q3 amends, per its cover, Q2).

**The one command on an empty database.** Six review passes and the fresh agent all followed the README's first step,
`backfill`, so nobody ran the README's one command on nothing. When it became `make run` I tried that: `RESULT: OK`,
557 of 1,800 filings, no Apple 2009. A filer's first run now reads its history, whichever the mode. The review of that
fix found an empty history page counted as read, for good; a page must now hold what EDGAR advertised for it.

**What held up.** An independent audit through a different route (the www.sec.gov Atom feeds) matched all 183
accession numbers, forms and filing dates for Apple and GameStop since 2009, Shell's 20-Fs and both Exxon CIKs, and
confirmed five periods and labels on the filings' index pages.

**Data decisions, briefly** (`docs/decisions.md`): annual = 10-K family and 20-F, quarterly = 10-Q family, 6-K
excluded, so the five foreign filers have no quarterly rows on purpose; a company is a set of CIKs, not a ticker;
one fiscal-year convention for everyone, issuer conventions and corrections listed with evidence; nothing is
deleted, vanished filings stay flagged until a person decides; every fact EDGAR lacks, and every finding a person
accepted, lives in `overrides.toml` with its evidence.
