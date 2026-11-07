# Filer category at filing time, read from the cover pages (2026-11-07)

Why: the table only knows each filer's category as EDGAR reports it today ("Large accelerated filer" for all fifteen
domestic companies). Computing lateness with today's 60-day deadline flagged nine annual reports in the last ten
years. The deadline depends on the category checked on the cover page of the report itself, so the first 600 KB of
each candidate's primary document was fetched and the checked box (or the inline-XBRL `dei:EntityFilerCategory`
value) read. Script output kept verbatim below; "☒" / "x" is the checked box.

| ticker | fiscal year | filed | days after year end | category on the cover | deadline that applied | late? |
|---|---|---|---|---|---|---|
| AMC | 2016 | 2017-03-10 | 69 | Accelerated filer ☒ | 75 days (2017-03-16) | no |
| AMC | 2020 | 2021-03-12 | 71 | Accelerated filer ☒ | 75 days (2021-03-16) | no |
| CVNA | 2017 | 2018-03-06 | 65 | Non-accelerated filer ý, Emerging growth company ý (read by hand, note 1) | 90 days (2018-04-02, 31 March is a Saturday) | no |
| LCID | 2020 | 2021-03-16 | 75 | Non-accelerated filer x, Emerging growth company x | 90 days (2021-03-31) | no |
| PLUG | 2016 | 2017-03-10 | 69 | Accelerated filer ☒ | 75 days (2017-03-16) | no |
| PLUG | 2017 | 2018-03-12 | 71 | Accelerated filer ☒ | 75 days (2018-03-16) | no |
| PLUG | 2018 | 2019-03-13 | 72 | Accelerated filer ☒ | 75 days (2019-03-18, 16th is a Saturday) | no |
| PLUG | 2019 | 2020-03-10 | 70 | Accelerated Filer ☒ (read by hand, note 2) | 75 days (2020-03-16) | no |
| PLUG | 2020 | 2021-05-14 | 134 | Large accelerated filer ☒, NT 10-K filed 2021-03-02 | 60 + 15 days (2021-03-16) | **yes, 59 days** |

Other annual reports in the window were filed inside today's deadline for their class, so they are inside any
historical deadline as well (a filer's deadline never gets shorter than 60 days; a 20-F's is 120 days and the longest
20-F lag in the window is BABA's 118 days).

Notes: (1) the 2018 cover page uses "ý" for a checked box, which the script's pattern missed; read directly from the
saved page: "Large accelerated filer q Accelerated filer q Non-accelerated filer ý ... Emerging growth company ý".
(2) The cover writes "Accelerated Filer" with a capital F, which the case-sensitive pattern missed; read directly:
"Large accelerated filer ☐ Accelerated Filer ☒ Non-accelerated filer ☐".

Raw script output (ticker, fiscal year, filing date, inline-XBRL category, checkboxes found):

```
AMC FY2016 filed 2017-03-10 | iXBRL EntityFilerCategory: None | checkboxes: [('Large accelerated filer', '☐'), ('Accelerated filer', '☒'), ('Non-accelerated filer', '☐'), ('Smaller reporting company', '☐')]
AMC FY2017 filed 2018-03-01 | iXBRL EntityFilerCategory: None | checkboxes: [('Large accelerated filer', '☒'), ('Accelerated filer', '☐'), ('Non-accelerated filer', '☐'), ('Smaller reporting company', '☐')]
AMC FY2018 filed 2019-03-01 | iXBRL EntityFilerCategory: None | checkboxes: [('Large accelerated filer', '☒'), ('Accelerated filer', '☐'), ('Non-accelerated filer', '☐'), ('Smaller reporting company', '☐')]
AMC FY2019 filed 2020-02-28 | iXBRL EntityFilerCategory: None | checkboxes: [('Large accelerated filer', '☐'), ('Accelerated filer', '☒'), ('Non-accelerated filer', '☐'), ('Smaller reporting company', '☐')]
AMC FY2020 filed 2021-03-12 | iXBRL EntityFilerCategory: None | checkboxes: [('Large accelerated filer', '☐'), ('Accelerated filer', '☒'), ('Non-accelerated filer', '☐'), ('Smaller reporting company', '☐')]
CVNA FY2017 filed 2018-03-06 | iXBRL EntityFilerCategory: None | checkboxes: none matched
CVNA FY2018 filed 2019-02-27 | iXBRL EntityFilerCategory: Large Accelerated Filer | checkboxes: none matched
CVNA FY2019 filed 2020-02-26 | iXBRL EntityFilerCategory: Large accelerated filer | checkboxes: [('Large accelerated filer', '☒'), ...]
CVNA FY2020 filed 2021-02-25 | iXBRL EntityFilerCategory: Large accelerated filer | checkboxes: [('Large accelerated filer', '☒'), ...]
LCID FY2020 filed 2021-03-16 | iXBRL EntityFilerCategory: None | checkboxes: [('Large accelerated filer', '¨'), ('Accelerated filer', '¨'), ('Non-accelerated filer', 'x'), ('Emerging growth company', 'x')]
PLUG FY2016 filed 2017-03-10 | iXBRL EntityFilerCategory: None | checkboxes: [('Large accelerated filer', '☐'), ('Accelerated filer', '☒'), ('Non-accelerated filer', '☐'), ('Smaller reporting company', '☐')]
PLUG FY2017 filed 2018-03-12 | iXBRL EntityFilerCategory: None | checkboxes: [('Large accelerated filer', '☐'), ('Accelerated filer', '☒'), ...]
PLUG FY2018 filed 2019-03-13 | iXBRL EntityFilerCategory: None | checkboxes: [('Large accelerated filer', '☐'), ('Accelerated filer', '☒'), ...]
PLUG FY2019 filed 2020-03-10 | iXBRL EntityFilerCategory: None | checkboxes: [('Large accelerated filer', '☐'), ('Non-accelerated filer', '☐'), ('Smaller reporting company', '☐'), ('Emerging growth company', '☐')]
PLUG FY2020 filed 2021-05-14 | iXBRL EntityFilerCategory: None | checkboxes: [('Large accelerated filer', '☒'), ('Non-accelerated filer', '☐'), ('Smaller reporting company', '☐'), ('Emerging growth company', '☐')]
```
