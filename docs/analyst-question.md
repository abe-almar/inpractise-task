# An analyst's question

"Which of these companies have filed an annual report late, or had to correct one, in the last 10 years?"

**Can the table answer it?** Partly, and it knows which part. It holds every annual report, amendment and
late-filing notice (NT 10-K, NT 20-F) since 1994 with period end and filing date, so one query lists the candidates
for both halves. It cannot say by itself whether a candidate was late (that needs the filer's category at the
time; EDGAR gives only today's) or whether an amendment corrected anything (that needs the amendment's explanatory
note). For this answer the candidates were checked against those two sources; evidence in `docs/evidence/`.

**Late (annual reports filed since 2016-11-07).** Under today's 60-day deadline the table flags nine reports.
Their cover pages (`late-filers-cover-check-2026-11-07.md`) say that AMC and Plug were "accelerated filers"
(75 days) in those years and that Lucid's SPAC and Carvana's first 10-K were "non-accelerated" (90 days): eight
were on time. One was late: **Plug Power's fiscal 2020 10-K**, due 2021-03-01, extended to 2021-03-16 by its NT
10-K of 2021-03-02, filed 2021-05-14, **59 days late**, with restated 2018 and 2019 statements inside. The other
fifteen companies filed every annual report within 60 days (domestic) or 120 days (20-F; Alibaba's longest is 118).
Confidence: high; each case rests on the filer's own notice, the deadline rule and the cover page. Caveat: federal
holidays are not in the deadline arithmetic (weekends are).

**Corrected.** Thirty-three amended annual reports in the window; `amendments-purpose.md` quotes each explanatory
note.

| what the amendment did | count | companies |
|---|---|---|
| added Part III (directors, pay), nothing corrected | 11 | AMC 3, Plug 3, Tesla 5 |
| added Iran Threat Reduction Act disclosures, years later | 14 | Toyota, fiscal 2014 to 2023 |
| added an affiliate's financial statements (Rule 3-09) | 2 | Ford 2016, 2017 |
| **restated the financial statements** | 1 | **Lucid** (as Churchill Capital IV), fiscal 2020, SPAC warrant accounting |
| corrected something other than the numbers | 4 | **Shell** 2023 and 2024: auditor EY withdrew its reports over an independence breach and reissued them; **GameStop** fiscal 2023: an omission in Item 5 "due to an editorial error"; **Plug** fiscal 2020: controls disclosure corrected to cover 2018 and 2019 |
| rewrote one item after SEC comments | 1 | Alibaba fiscal 2023, Item 16I |

**Answer.** Late: Plug Power (fiscal 2020). Corrected the financial statements: Lucid's predecessor (fiscal 2020)
and Plug Power (2018-2019, inside the late 2020 10-K, which is not an amendment and shows in the table only as
"late"). Corrected something else: Shell (2023, 2024), GameStop (fiscal 2023), Plug (2020 controls). High
confidence on each, because each was read from the filing. What this method cannot see is a correction made inside
a later original report with neither an amendment nor a notice.

**What is missing, and what I would add.** (1) The filer category at filing time, from the cover page's checked box
or the inline-XBRL `dei:EntityFilerCategory` tag (2019 onwards), so "late" becomes pure SQL. (2) The first paragraph
after "Explanatory Note" of every amendment, stored with a class (Part III, exhibit, restatement, auditor report,
other): one request per amendment, once. (3) The same scan on original reports for a restatement flag, to catch
the Plug 2020 pattern.
