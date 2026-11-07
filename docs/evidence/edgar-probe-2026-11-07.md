# EDGAR probe of the 20 tickers (2026-11-07)

Source: `https://www.sec.gov/files/company_tickers.json` (ticker to CIK) and `https://data.sec.gov/submissions/CIK##########.json` plus every older page listed under `filings.files`. Counts are forms of the whole filing history as served on 2026-11-07. Script: the probe kept in the session scratchpad; it will be rewritten as a repository tool.

| ticker | CIK | name (EDGAR) | entity type | FYE | pages | first filing | 10-K | 10-K405 | 10-K/A | 10-K405/A | 10-KT | 10-Q | 10-Q/A | 10-QT | 20-F | 20-F/A | 40-F | 6-K | NT 10-K | NT 10-Q | NT 20-F |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AAPL | 320193 | Apple Inc. | operating | 0926 | 1 | 1994-01-26 | 31 | 2 | 2 | 0 | 0 | 98 | 2 | 0 | 0 | 0 | 0 | 0 | 1 | 1 | 0 |
| MSFT | 789019 | MICROSOFT CORP | operating | 0630 | 2 | 1994-02-14 | 33 | 0 | 0 | 0 | 0 | 99 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| JPM,JPM-PC,VYLD,AMJB,JPM-PD,JPM-PJ,JPM-PK,JPM-PL,JPM-PM | 19617 | JPMORGAN CHASE & CO | operating | 1231 | 71 | 1994-01-20 | 27 | 6 | 10 | 0 | 0 | 99 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| XOM | 2115436 | ExxonMobil Holdings Corp | operating | 1231 | 0 | 2026-07-01 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| WMT | 104169 | Walmart Inc. | operating | 0131 | 2 | 1994-02-14 | 32 | 0 | 3 | 0 | 0 | 95 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| KO | 21344 | COCA COLA CO | operating | 1231 | 2 | 1994-01-27 | 30 | 3 | 1 | 0 | 0 | 99 | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| GE | 40545 | GENERAL ELECTRIC CO | operating | 1231 | 2 | 1994-01-28 | 26 | 7 | 2 | 0 | 0 | 99 | 11 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| F,F-PB,F-PC,F-PD | 37996 | FORD MOTOR CO | operating | 1231 | 2 | 1994-01-20 | 30 | 3 | 5 | 0 | 0 | 99 | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 |
| T,TBB,T-PA,T-PC | 732717 | AT&T INC. | operating | 1231 | 4 | 1994-02-14 | 29 | 4 | 8 | 5 | 0 | 99 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| TSLA | 1318605 | Tesla, Inc. | operating | 1231 | 1 | 2005-02-17 | 16 | 0 | 6 | 0 | 0 | 50 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| PLUG | 1093691 | PLUG POWER INC | operating | 1231 | 1 | 1999-08-27 | 26 | 1 | 8 | 0 | 0 | 81 | 1 | 0 | 0 | 0 | 0 | 0 | 2 | 1 | 0 |
| GME | 1326380 | GameStop Corp. | operating | 0130 | 1 | 2005-05-23 | 21 | 0 | 2 | 0 | 0 | 63 | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| AMC | 1411579 | AMC ENTERTAINMENT HOLDINGS, INC. | operating | 1231 | 1 | 2007-09-12 | 13 | 0 | 3 | 0 | 0 | 39 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| CVNA | 1690820 | CARVANA CO. | operating | 1231 | 1 | 2016-12-22 | 9 | 0 | 0 | 0 | 0 | 30 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| LCID | 1811210 | Lucid Group, Inc. | operating | 1231 | 0 | 2020-05-27 | 6 | 0 | 1 | 0 | 0 | 19 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 |
| SAP,SAPGF | 1000184 | SAP SE | other | 1231 | 0 | 1996-08-08 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 28 | 2 | 0 | 369 | 0 | 0 | 0 |
| SHEL,RYDAF | 1306965 | Shell plc | other | 1231 | 1 | 2004-10-28 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 21 | 2 | 0 | 1415 | 0 | 0 | 0 |
| BABA,BABAF,BBAAY | 1577552 | Alibaba Group Holding Ltd | other | None | 0 | 2014-05-06 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 12 | 1 | 0 | 354 | 0 | 0 | 0 |
| NVO,NONOF | 353278 | NOVO NORDISK A S | other | 1231 | 1 | 2001-01-18 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 25 | 4 | 0 | 1796 | 0 | 0 | 0 |
| TM,TOYOF | 1094517 | TOYOTA MOTOR CORP/ | other | 0331 | 1 | 2002-02-27 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 25 | 18 | 0 | 632 | 0 | 0 | 0 |
| (none) | 34088 | EXXON MOBIL CORP | operating | 1231 | 2 | 1994-03-04 | 7 | 0 | 0 | 0 | 0 | 20 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## Observations

- `XOM` maps to ExxonMobil Holdings Corp (CIK 2115436), created by a holding-company reorganisation on 2026-07-01 (form 8-K12B). Its history is under Exxon Mobil Corp (CIK 34088), which no longer carries a ticker. The Q2 2026 10-Q (accession 0000034088-26-000093, filed 2026-08-03) is one combined filing listed under both CIKs, not two filings; the first backfill proved it when the same accession number came back from both pages (loaded once, then "seen again").
- SAP, SHEL, BABA, NVO and TM are foreign private issuers: annual report = 20-F, no 10-Q at all; quarterly results, when published, travel as 6-K (furnished, not filed), mixed with hundreds of other 6-Ks.
- BABA's `fiscalYearEnd` is null in the submissions JSON (Alibaba's year ends 31 March).
- LCID's CIK (1811210) belonged to Churchill Capital Corp IV, a SPAC, until the July 2021 merger; its first 10-K (fiscal 2020) is the SPAC's.
- `10-K405` (26 filings, 1990s to 2002) is a 10-K variant and must count as an annual report; `10-K405/A` likewise.
- Amendments: 51 `10-K/A`, 39 `10-Q/A`, 27 `20-F/A` (18 of them Toyota). Late-filing notices: 4 `NT 10-K` (AAPL 2006, TSLA, PLUG x2), 4 `NT 10-Q`.
- JPM has 71 older pages (26,470 filings); the daily job must only read the `recent` page and leave the full history to the backfill.
- The XBRL companyfacts API (`/api/xbrl/companyfacts/CIK##########.json`) carries the SEC's own `fy` / `fp` label per accession for XBRL-era filings (all 45 AAPL 10-K/10-Q in the recent page, no conflicts), usable as an independent cross-check of the fiscal labelling rule; it does not exist for pre-2009 filings.
- EDGAR fair access (2026-11-07): max 10 requests/second, declared User-Agent with contact, gzip accepted, filings submitted after 17:30 ET are disseminated the next business day, post-acceptance corrections and deletions happen.
