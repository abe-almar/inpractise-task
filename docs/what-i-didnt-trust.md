# What I didn't trust

The agents: a Claude Code session doing the work, a sub-agent writing tests, two Hermes profiles auditing and
reviewing. Where they, and I, were wrong, and what it was checked against.

**Exxon's two CIKs.** My probe note said both had filed a Q2 2026 10-Q. The backfill found the same accession number
on both pages: one filing by two registrants, now an `also_filed_by_ciks` column, not a second row.

**Two "original" annual reports for one year, at four companies.** For 19 old filings EDGAR's "period of report" is
the filing date; GE's 10-K filed 2015-02-27 says "fiscal year ended December 31, 2014" on its cover. Fix: label from
the last period end before the filing date.

**The SEC's own labels disagreed on 106 of 1,017 filings.** 101 are issuers naming the year by its start; four are SEC
errors, checked on the cover pages. One rule, the SEC label kept beside it, conventions listed with evidence.

**A keyword classifier called Tesla's Part III amendments "restatements".** Reading each explanatory note instead:
one of 33 amendments in ten years restated anything.

**The adversarial reviewer failed the work four times.** Pass 1: the disappearance alarm fired once and went quiet, an
empty EDGAR page counted as fetched, and a lenient label check would have excused an off-by-one in my own rule. Passes
2 to 4 each caught a defect in the previous fix to the outage baseline, now a per-filer watermark exercised live
(`docs/evidence/outage-recovery-2026-11-07.log`).

**The one command on an empty database.** Every reviewer followed the README's first step, `backfill`; nobody ran
`make run` on nothing. I did: `RESULT: OK`, 557 of 1,800 filings, no Apple 2009. A filer's first run now reads its
history.

**What held up.** An audit through the www.sec.gov Atom feeds matched all 183 accession numbers, forms and dates it
sampled.

**Data decisions** (`docs/decisions.md`): annual = 10-K family and 20-F, quarterly = 10-Q family, 6-K excluded, so the
five foreign filers have no quarterly rows; a company is a set of CIKs, not a ticker; one fiscal-year rule for
everyone, conventions and corrections listed with evidence; nothing is deleted, a vanished filing stays flagged until a
person decides; every fact EDGAR lacks and every accepted finding is in `overrides.toml` with its evidence.
