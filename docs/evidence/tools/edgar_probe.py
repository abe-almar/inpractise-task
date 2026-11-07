"""Day-one probe (2026-11-07): map tickers to CIKs and count the report forms in every page of each company's
EDGAR history. Produced the table in docs/evidence/edgar-probe-2026-11-07.md. Read-only, declared user agent, one
request every 0.15 s.

    SEC_USER_AGENT="you you@example.com" .venv/bin/python docs/evidence/tools/edgar_probe.py <out-dir> AAPL,MSFT,...
"""
import os
import json, sys, time, urllib.request, collections, pathlib
out = pathlib.Path(sys.argv[1]); tickers = sys.argv[2].split(",")
UA = os.environ.get("SEC_USER_AGENT", "sec-filings take-home abe.alcarazm@gmail.com")
def get(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            import gzip; data = gzip.decompress(data)
    dest.write_bytes(data); return data
tk = json.loads(get("https://www.sec.gov/files/company_tickers.json", out/"company_tickers.json"))
by_ticker = {}
for row in tk.values():
    by_ticker.setdefault(row["ticker"].upper(), []).append(row)
WANT = ("10-K","10-K/A","10-KT","10-Q","10-Q/A","10-QT","20-F","20-F/A","40-F","6-K","NT 10-K","NT 10-Q","NT 20-F")
for t in tickers:
    rows = by_ticker.get(t.upper())
    if not rows: print(f"{t}: NOT IN company_tickers.json"); continue
    cik = rows[0]["cik_str"]; time.sleep(0.15)
    d = json.loads(get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", out/"subs"/f"CIK{cik:010d}.json"))
    rec = d["filings"]["recent"]; c = collections.Counter(rec["form"])
    pages = d["filings"].get("files", [])
    # older pages
    for p in pages:
        time.sleep(0.15)
        pd_ = json.loads(get("https://data.sec.gov/submissions/"+p["name"], out/"subs"/p["name"]))
        c.update(pd_["form"])
    sub = {f: c[f] for f in WANT if c.get(f)}
    first = min(rec["filingDate"]) if not pages else min(p["filingFrom"] for p in pages)
    print(f"{t:5} CIK {cik:>10} {d['name'][:34]:34} fye={d.get('fiscalYearEnd')} type={d.get('entityType')} exch={d.get('exchanges')} tickers={d.get('tickers')} former={len(d.get('formerNames',[]))} pages={len(pages)} recent={len(rec['form'])} from={first} forms={sub}")
