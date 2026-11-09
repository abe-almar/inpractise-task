# An analyst's question

"Which of these companies have filed an annual report late, or had to correct one, in the last 10 years?"

**Can the table answer it?** Partly, and it knows which part. It holds every annual report, amendment and late-filing
notice since 1994 with period end and filing date, so one query lists the candidates. It cannot say whether a
candidate was late (that needs the filer's category at the time; EDGAR gives only today's) or whether an amendment
corrected anything (that needs its explanatory note). Both were checked by hand; evidence in `docs/evidence/`.

**Late.** Under today's 60-day deadline the table flags nine reports since 2016-11-07. Their cover pages
(`late-filers-cover-check-2026-11-07.md`) put AMC and Plug at 75 days and Lucid's SPAC and Carvana's first 10-K at 90
that year: eight were on time. One was late: **Plug Power's fiscal 2020 10-K**, due 2021-03-01, extended to
2021-03-16 by an NT 10-K, filed 2021-05-14, **59 days late**, with restated 2018 and 2019 statements inside.

**Corrected.** Thirty-three amendments; `amendments-purpose.md` quotes each explanatory note.

| what the amendment did | count | companies |
|---|---|---|
| added Part III (directors, pay), nothing corrected | 11 | AMC, Plug, Tesla |
| added Iran Threat Reduction Act disclosures, years later | 14 | Toyota, fiscal 2014 to 2023 |
| added an affiliate's financial statements (Rule 3-09) | 2 | Ford 2016, 2017 |
| **restated the financial statements** | 1 | **Lucid** (as Churchill Capital IV), fiscal 2020, warrant accounting |
| corrected something other than the numbers | 4 | **Shell** 2023, 2024 (auditor's reports reissued); **GameStop** fiscal 2023 (Item 5 omission); **Plug** fiscal 2020 (controls disclosure) |
| rewrote one item after SEC comments | 1 | Alibaba fiscal 2023, Item 16I |

**Answer.** Late: Plug Power (fiscal 2020). Restated: Lucid's predecessor (fiscal 2020) and Plug Power (2018-2019,
inside the late 10-K, so the table shows it only as "late"). Corrected something else: Shell (2023, 2024), GameStop
(fiscal 2023), Plug (2020 controls). Confidence: high; each case rests on the filing itself and the deadline rule.
Caveats: federal holidays are not in the deadline arithmetic, and a correction made inside a later original report,
with neither amendment nor notice, is invisible here.

**What I would add.** The filer category at filing time (cover-page checkbox, or the inline-XBRL
`dei:EntityFilerCategory` tag from 2019), so "late" becomes pure SQL; each amendment's first paragraph after
"Explanatory Note", stored with a class, one request each, once; and the same scan on original reports for a
restatement flag, to catch the Plug 2020 pattern.
