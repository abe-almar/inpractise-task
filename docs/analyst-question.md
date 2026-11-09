# An analyst's question

“Which of these companies have filed an annual report late, or had to correct one, in the last 10 years?”

**Scope:** the original twenty-company list; 195 original annual reports and 33 annual amendments filed
**2016-11-07 through 2026-11-07**, including amendments to older reports. The table finds candidates; filing covers
and explanatory notes establish historical deadlines and amendment purposes.

**Late: Plug Power, fiscal 2020.** Its large-accelerated-filer deadline was 2021-03-01; the
[NT 10-K](https://www.sec.gov/Archives/edgar/data/1093691/000110465921030324/0001104659-21-030324-index.htm)
was filed 2021-03-02. The [annual report](https://www.sec.gov/Archives/edgar/data/1093691/000155837021007147/plug-20201231x10k.htm)
arrived 2021-05-14: **59 days beyond the 15-day NT window** ending
2021-03-16, or 74 days after the original deadline. Applying today's category flagged nine candidates, but their
covers cleared eight: AMC 2016/2020 and Plug 2016–2019 were accelerated filers (75 days); Carvana 2017 and
Lucid's predecessor 2020 were non-accelerated (90 days). The remaining domestic originals met the shortest
60-day deadline; the five foreign issuers met the [four-calendar-month 20-F deadline](https://www.sec.gov/files/form20-f.pdf).

**Amendments: six corrections or revisions; 27 additions.** Reading all 33 explanatory notes gave:

| purpose | count | companies and source examples |
|---|---:|---|
| Added Part III (directors, pay) | 11 | AMC, Plug, [Tesla](https://www.sec.gov/Archives/edgar/data/1318605/000156459021022604/tsla-10ka_20201231.htm) |
| Supplemented Iran-related disclosures | 14 | [Toyota](https://www.sec.gov/Archives/edgar/data/1094517/000119312524024814/d743563d20fa.htm), reports covering years within 2014–2023 |
| Added an affiliate's financial statements (Rule 3-09) | 2 | [Ford](https://www.sec.gov/Archives/edgar/data/37996/000003799618000025/f1231201710-ka.htm), 2016/2017 |
| Restated financial statements | 1 | [Lucid's predecessor, Churchill Capital IV](https://www.sec.gov/Archives/edgar/data/1811210/000110465921067004/tm2116246d1_10ka.htm), 2020: warrant accounting |
| Corrected disclosures or audit reports | 4 | Shell [2023](https://www.sec.gov/Archives/edgar/data/1306965/000130696525000025/shel-20231231.htm)/[2024](https://www.sec.gov/Archives/edgar/data/1306965/000130696525000027/shel-20241231.htm): reissued audit opinions; [GameStop](https://www.sec.gov/Archives/edgar/data/1326380/000132638024000016/gme-20240203.htm): Item 5 omission; [Plug 2020](https://www.sec.gov/Archives/edgar/data/1093691/000155837022003577/plug-20201231x10ka.htm): MD&A/controls |
| Revised disclosure after SEC comments | 1 | [Alibaba 2023](https://www.sec.gov/Archives/edgar/data/1577552/000119312524044480/d791119d20fa.htm): Item 16I |

Plug's linked explanatory note also confirms that its original 2020 10-K restated 2018–2019 financial statements.
GameStop's amendment covers the year ended 2024-02-03: its “fiscal 2023”, our `2024 FY`.

**Confidence:** high in the confirmed cases; not an exhaustive restatement search. Later originals can contain
corrections without an amendment or late notice. The deadline code also omits federal holidays. To automate the
answer, add historical filer categories, holiday-aware deadlines, evidenced amendment classifications, and
original-report/8-K non-reliance disclosures.

Reproduce the candidate inventory with `DATABASE_URL=... .venv/bin/python scripts/analyst_question.py` from the
repository root. The [query](../scripts/analyst_question.py) fixes the company list and dates above; the final
classifications require reading the linked filings.
