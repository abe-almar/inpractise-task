# Hermes lead run: audit-1-independent-route

- Generated: 2026-11-07 17:52 CET
- Profile: takehome (gpt-6.1-sol, reasoning max; subagents gpt-6.1-sol, reasoning xhigh)
- Brief: /Users/homerolapida/.hermes/briefs/takehome/audit-1-independent-route.md

VERDICT: MATCH

183 distinct accessions matched. No missing accessions, form mismatches or filing-date mismatches. All 62 annual-report filing dates, including amendments, matched.

Counts below include amendments and use the requested filing-date cutoffs.

AAPL — CIK 320193

  Form      Feed  Table  Only in feed  Only in table  Date mismatches
  10-K        18     18  none          none                         0
  10-K/A       1      1  none          none                         0
  10-Q        54     54  none          none                         0
  10-Q/A       1      1  none          none                         0
  TOTAL       74     74  none          none                         0

  Annual filing dates compared: 19.

GME — CIK 1326380

  Form      Feed  Table  Only in feed  Only in table  Date mismatches
  10-K        18     18  none          none                         0
  10-K/A       2      2  none          none                         0
  10-Q        53     53  none          none                         0
  10-Q/A       4      4  none          none                         0
  TOTAL       77     77  none          none                         0

  Annual filing dates compared: 20.

SHEL — CIK 1306965

  Form      Feed  Table  Only in feed  Only in table  Date mismatches
  20-F        18     18  none          none                         0
  20-F/A       2      2  none          none                         0
  TOTAL       20     20  none          none                         0

  Annual filing dates compared: 20.

XOM — union of CIKs 2115436 and 34088

  Form      Feed  Table  Only in feed  Only in table  Date mismatches
  10-K         3      3  none          none                         0
  10-K/A       0      0  none          none                         0
  10-Q         9      9  none          none                         0
  10-Q/A       0      0  none          none                         0
  TOTAL       12     12  none          none                         0

  Annual filing dates compared: 3.
  The old CIK's feed contributed 11 accessions; the new CIK's contributed 2. One shared accession was counted once. The database selection included both primary CIKs and also_filed_by_ciks.

Index-page confirmations

Rule applied independently: fiscal year is the calendar year in which the fiscal year ends; quarters count from the previous fiscal year end. An amendment retains the period it amends, not its filing year.

1. AAPL — 0001193125-09-214859 — 10-K
   https://www.sec.gov/Archives/edgar/data/320193/000119312509214859/0001193125-09-214859-index.htm
   Period of Report: 2009-09-26
   Table report_date: 2009-09-26
   Table label: 2009 FY. Confirmed: fiscal year ended in calendar 2009.

2. GME — 0001628280-25-014731 — 10-K
   https://www.sec.gov/Archives/edgar/data/1326380/000162828025014731/0001628280-25-014731-index.htm
   Period of Report: 2025-02-01
   Table report_date: 2025-02-01
   Table label: 2025 FY. Confirmed under the ending-calendar-year convention.

3. SHEL — 0001628280-26-017024 — 20-F
   https://www.sec.gov/Archives/edgar/data/1306965/000162828026017024/0001628280-26-017024-index.htm
   Period of Report: 2025-12-31
   Table report_date: 2025-12-31
   Table label: 2025 FY. Confirmed.

4. XOM — 0000034088-26-000093 — 10-Q
   https://www.sec.gov/Archives/edgar/data/2115436/000003408826000093/0000034088-26-000093-index.htm
   Period of Report: 2026-06-30
   Table report_date: 2026-06-30
   Table label: 2026 Q2. Confirmed: the second quarter after the December 2025 year end.
   The table stores this once under CIK 2115436, with 34088 in also_filed_by_ciks.

5. AAPL amendment — 0001193125-10-012091 — 10-K/A
   https://www.sec.gov/Archives/edgar/data/320193/000119312510012091/0001193125-10-012091-index.htm
   Period of Report: 2009-09-26
   Table report_date: 2009-09-26
   Table label: 2009 FY. Confirmed despite its 2010-01-25 filing date.

Exact SEC requests

18 connection/request attempts: 17 HTTP 200 responses and one TLS failure before an HTTP response. All GETs used:

  User-Agent: sec-filings take-home audit abe.alcarazm@gmail.com
  Accept-Encoding: gzip, deflate

Requests were sequential. The automated sequence waited at least 0.4 seconds after each response before starting the next request. No calls were made to data.sec.gov.

Attempts 1, 2 and 3 — same exact URL:
  https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000320193&type=10-K&dateb=&owner=include&count=100&output=atom
  1: Python urllib; TLS certificate-validation failure.
  2: curl; HTTP 200, initial raw-feed inspection.
  3: curl; HTTP 200, parsed for the accession/date comparison.

4 — HTTP 200:
  https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000320193&type=10-Q&dateb=&owner=include&count=100&output=atom

5 — HTTP 200:
  https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001326380&type=10-K&dateb=&owner=include&count=100&output=atom

6 — HTTP 200:
  https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001326380&type=10-Q&dateb=&owner=include&count=100&output=atom

7 — HTTP 200:
  https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001306965&type=20-F&dateb=&owner=include&count=100&output=atom

8 — HTTP 200:
  https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0002115436&type=10-K&dateb=&owner=include&count=100&output=atom

9 — HTTP 200:
  https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0002115436&type=10-Q&dateb=&owner=include&count=100&output=atom

10 — HTTP 200:
  https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000034088&type=10-K&dateb=&owner=include&count=100&output=atom

11 — HTTP 200:
  https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000034088&type=10-Q&dateb=&owner=include&count=100&output=atom

12 — HTTP 200:
  https://www.sec.gov/Archives/edgar/data/320193/000119312509214859/0001193125-09-214859-index.htm

13 — HTTP 200:
  https://www.sec.gov/Archives/edgar/data/1326380/000162828025014731/0001628280-25-014731-index.htm

14 — HTTP 200:
  https://www.sec.gov/Archives/edgar/data/1306965/000162828026017024/0001628280-26-017024-index.htm

15 — HTTP 200:
  https://www.sec.gov/Archives/edgar/data/2115436/000003408826000093/0000034088-26-000093-index.htm

16 — HTTP 200:
  https://www.sec.gov/Archives/edgar/data/320193/000119312510012091/0001193125-10-012091-index.htm

17 — HTTP 200, zero entries:
  https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000320193&type=10-Q&dateb=&owner=include&count=100&output=atom&start=100

18 — HTTP 200, zero entries:
  https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000034088&type=10-Q&dateb=&owner=include&count=100&output=atom&start=100

Blockers and read-only status

The initial Python certificate-chain error was resolved by using curl with certificate verification enabled. No unresolved blockers.

Database access used SELECTs only through docker compose exec -T db psql. I made no file edits or database writes. During the audit, git status gained an additional modification to docs/what-i-didnt-trust.md; I inspected the diff and left it untouched.
