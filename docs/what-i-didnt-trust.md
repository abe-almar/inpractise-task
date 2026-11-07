# What I didn't trust

The agents were a Claude Code session doing the work, a sub-agent writing tests, and two Hermes profiles auditing
and reviewing. Where they (and I, reading them) were wrong, how it came out, and what it was checked against.

**Exxon's two CIKs.** The probe note said both had filed a Q2 2026 10-Q. The first backfill printed one filing as
"seen again" for the old CIK: the same accession number sits on both pages. Checked against the two submissions
JSON files: one combined filing by two registrants. It became an `also_filed_by_ciks` column, not a second row.

**Four companies with two "original" annual reports for one year.** The `labels_unique` check failed on the first
backfill (GE 2015, AT&T 2010, Novo Nordisk 2012, SAP 2004). Cause: for 20 filings, mostly 1994-2015, EDGAR's "period
of report" is the filing date itself. Checked against the documents: GE's 10-K filed 2015-02-27 says "for the fiscal
year ended December 31, 2014" on its cover. Fix: when the period of report is within 14 days of the filing date and
off the calendar, label from the last period end before the filing date. The duplicates disappeared and the SEC's
labels agree with the result.

**Two bugs visible only by reading a passing check's output.** The overdue check printed Microsoft's next quarter end
as 30 December (month arithmetic lost the month end) and gave Shell and SAP a 60-day deadline because EDGAR calls them
"Large accelerated filer", though a 20-F has four months. Both fixed, the second by letting the annual form decide.

**The SEC's own fiscal labels disagreed on 106 of 1,017 filings.** 101 are one year apart with the same period:
GameStop in every year, Walmart until March 2014, Toyota until June 2017, issuers naming the year by its start, two
of them switching convention over time. Four are SEC errors, checked on the cover pages (AT&T's "quarterly period
ended September 30" tagged Q2, twice; GE's 10-K tagged Q4; a GE Q1 2020 10-Q tagged 2019 Q3). Decision: one rule,
the SEC label stored beside it, the four errors recorded as accepted findings with what was read.

**"Late filers" from today's filer category were mostly wrong.** Nine annual reports came out late under a large
accelerated filer's 60 days. The nine cover pages say AMC and Plug were "accelerated" (75 days) in those years and
Lucid's SPAC and Carvana's first 10-K "non-accelerated" (90 days). One was late: Plug's fiscal 2020 10-K, 59 days.

**A keyword classifier called Tesla's Part III amendments "restatements"**, from clawback boilerplate. Replaced by
quoting each explanatory note: of 33 amendments in ten years, one restated the financial statements.

**The test sub-agent** found that the recent page alone cannot answer "Apple 2009" (the 2009 10-K is on an older
page) and merged that page into its fixture; it also pinned the Exxon row as one accession with two CIKs. Its 211
tests pass; it changed nothing under `src/`.

**Data decisions, briefly** (entries in `docs/decisions.md`): annual = 10-K family and 20-F, quarterly = 10-Q
family, 6-K excluded, so the five foreign filers have no quarterly rows on purpose; a company is a set of CIKs, not
a ticker; fiscal year = the calendar year in which the year ends, one rule for everyone; nothing is deleted,
vanished filings are flagged; every fact EDGAR lacks, and every finding a person accepted, lives in
`overrides.toml` with its evidence.
