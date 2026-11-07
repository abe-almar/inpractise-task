# What I didn't trust

The agents were a Claude Code session doing the work, a sub-agent writing tests, and two Hermes profiles auditing
and reviewing. Where they (and I, reading them) were wrong, how it came out, and what it was checked against.

**Exxon's two CIKs.** My probe note said both had filed a Q2 2026 10-Q. The first backfill printed one filing as
"seen again" for the old CIK: the same accession number sits on both pages. Checked against the two submissions
JSON files: one combined filing by two registrants. It became an `also_filed_by_ciks` column, not a second row.

**Four companies with two "original" annual reports for one year.** The `labels_unique` check failed on the first
backfill (GE 2015, AT&T 2010, Novo Nordisk 2012, SAP 2004). Cause: for 20 filings, mostly 1994-2015, EDGAR's "period
of report" is the filing date itself. Checked against the documents: GE's 10-K filed 2015-02-27 says "for the fiscal
year ended December 31, 2014" on its cover. Fix: label from the last period end before the filing date. The
duplicates disappeared and the SEC's labels agree with the result.

**Two bugs visible only by reading a passing check's output.** The overdue check printed Microsoft's next quarter end
as 30 December and gave Shell a 60-day deadline because EDGAR calls it "Large accelerated filer", though a 20-F has
four months. Both fixed.

**The SEC's own fiscal labels disagreed on 106 of 1,017 filings.** 101 are one year apart with the same period:
GameStop always, Walmart until March 2014, Toyota until June 2017, issuers naming the year by its start, two of
them switching convention. Four are SEC errors, checked on the cover pages (AT&T's "quarterly period ended
September 30" tagged Q2, twice; GE's 10-K tagged Q4; a GE Q1 2020 10-Q tagged 2019 Q3). One rule, the SEC label
beside it, the four errors accepted with what was read.

**"Late filers" from today's filer category were mostly wrong.** Nine annual reports came out late under a large
accelerated filer's 60 days. The nine cover pages say AMC and Plug were "accelerated" (75 days) in those years and
Lucid's SPAC and Carvana's first 10-K "non-accelerated" (90 days). One was late: Plug's fiscal 2020 10-K, 59 days.

**A keyword classifier called Tesla's Part III amendments "restatements"**, from clawback boilerplate. Replaced by
quoting each explanatory note: of 33 amendments in ten years, one restated the financial statements.

**The adversarial review (Hermes, verdict FAIL, nine major findings) was mostly right.** Real and fixed: the
disappearance alarm fired once and then went quiet, and a crash between load and checks lost it entirely; an empty
EDGAR page counted as a successful fetch; "any one-year difference is a naming convention" would have excused an
off-by-one bug in my own rule for every company; a second registrant's page overwrote the first registrant's
metadata; acceptance was evaluated on a truncated list; the 20-F deadline was 120 days instead of four months; and
two documented features (dated fiscal year ends, a recovery for tickers missing from EDGAR's file) did not exist.
Stated as limitations rather than fixed: federal holidays, and the thirteen-week approximation of 52/53-week
quarter ends. Report and closures: `docs/review-evidence/`.

**The fresh-agent test found what the kit could not express.** With no persona, it added five companies, found the
Google and Disney predecessor CIKs on its own (Disney has two), and stopped, correctly, on a Disney 10-Q whose EDGAR
period is a year off: the kit had no per-filing correction, and accepting the duplicate would have hidden the real
quarter. That became `[[filing_corrections]]`. The same mechanism then fixed an error of my own: a 1997 Apple
amendment whose inferred label said Q3 amends, per its cover, the March quarter (Q2). My first attempt at the test
never started: the persona I had written forbade leaving the main directory, and the agent refused, correctly.

**The test sub-agent** found that the recent page alone cannot answer "Apple 2009" (the 2009 10-K is on an older
page) and merged that page into its fixture. Its tests pass; it changed nothing under `src/`.

**What held up.** An independent audit by the Hermes lead profile, through a different route (the www.sec.gov Atom
feeds), matched all 183 accession numbers, forms and filing dates for Apple and GameStop since 2009, Shell's 20-Fs
and both Exxon CIKs since 2024, and confirmed five periods and labels on the filings' index pages.

**Data decisions, briefly** (entries in `docs/decisions.md`): annual = 10-K family and 20-F, quarterly = 10-Q
family, 6-K excluded, so the five foreign filers have no quarterly rows on purpose; a company is a set of CIKs, not
a ticker; fiscal year = the year of the nominal year end nearest the period end, one rule for everyone, issuer
conventions and corrections listed with evidence; nothing is deleted, vanished filings stay flagged until a person
decides; every fact EDGAR lacks, and every finding a person accepted, lives in `overrides.toml` with its evidence.
