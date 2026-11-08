#!/usr/bin/env python3
"""Earnings Analyst: a company's reported numbers, quarter by quarter, from SEC filings.

    python3 earnings.py AAPL                  # last 8 quarters + TTM + latest earnings release link
    python3 earnings.py NVDA --quarters 12
    python3 earnings.py MSFT GOOGL META       # several, writes trader/metrics/<T>.json each
    python3 earnings.py AAPL --facts companyfacts.json   # offline, from a saved file

Data: SEC XBRL company facts (what the company itself reported in its 10-Q and
10-K), plus the latest 8-K with Item 2.02 (the earnings press release).
Q4 is not filed on its own: it is derived as full year minus Q1-Q3 (marked ~;
for EPS this is approximate because share counts change).
Analyst expectations and the next report date are NOT in SEC data: look them
up separately (see SKILL.md).
"""
import argparse
import datetime as dt
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_HERE, "_lib"), os.path.join(_HERE, "..", "_lib")]
import tadawal as T  # noqa: E402

CONCEPTS = {
    "revenue": (["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
                 "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet"], "USD"),
    "cost_of_revenue": (["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"], "USD"),
    "gross_profit": (["GrossProfit"], "USD"),
    "operating_income": (["OperatingIncomeLoss"], "USD"),
    "net_income": (["NetIncomeLoss", "ProfitLoss"], "USD"),
    "eps_diluted": (["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"], "USD/shares"),
    "operating_cash_flow": (["NetCashProvidedByUsedInOperatingActivities"], "USD"),
}
FORMS = ("10-Q", "10-K", "10-Q/A", "10-K/A", "20-F", "40-F")


def _d(s):
    return dt.date.fromisoformat(s)


def facts_series(facts, tags, unit):
    """{(start, end): fact} for duration facts; latest filing wins; earlier tags win per period."""
    out = {}
    gaap = facts.get("facts", {}).get("us-gaap", {})
    for tag in tags:
        best = {}
        for f in gaap.get(tag, {}).get("units", {}).get(unit, []):
            if "start" not in f or f.get("form", "") not in FORMS:
                continue
            k = (f["start"], f["end"])
            if k not in best or f.get("filed", "") > best[k].get("filed", ""):
                best[k] = f
        for k, f in best.items():
            out.setdefault(k, {"val": f["val"], "fy": f.get("fy"), "fp": f.get("fp"), "form": f.get("form"),
                               "filed": f.get("filed"), "tag": tag})
    return out


def quarterize(series):
    """Quarters (80-100 days) and years (350-380 days); Q4 derived as FY minus the 3 quarters inside it."""
    q, y = {}, {}
    for (s, e), v in series.items():
        days = (_d(e) - _d(s)).days
        if 80 <= days <= 100:
            q[e] = dict(v, start=s, end=e, derived=False)
        elif 350 <= days <= 380:
            y[e] = dict(v, start=s, end=e)
    for e, v in y.items():
        if e in q:
            continue
        inside = sorted([x for x in q.values() if _d(x["start"]) >= _d(v["start"]) - dt.timedelta(days=7)
                         and _d(x["end"]) < _d(e) - dt.timedelta(days=30)], key=lambda x: x["end"])
        if len(inside) == 3:
            q[e] = {"val": v["val"] - sum(x["val"] for x in inside), "fy": v["fy"], "fp": "Q4", "form": v["form"],
                    "filed": v["filed"], "start": (_d(inside[-1]["end"]) + dt.timedelta(days=1)).isoformat(),
                    "end": e, "derived": True, "tag": v["tag"]}
    return q, y


def year_ago(q, end):
    target = _d(end) - dt.timedelta(days=364)
    best = None
    for e in q:
        gap = abs((_d(e) - target).days)
        if gap <= 20 and (best is None or gap < best[0]):
            best = (gap, e)
    return q[best[1]] if best else None


def pct(a, b):
    if a is None or b in (None, 0):
        return None
    return round((a - b) / abs(b) * 100, 1)


def analyze(facts, ticker, n=8):
    data = {k: quarterize(facts_series(facts, tags, unit)) for k, (tags, unit) in CONCEPTS.items()}
    rq = data["revenue"][0] or data["net_income"][0]
    ends = sorted(rq)[-n:]
    rows = []
    for e in ends:
        base = rq[e]
        row = {"end": e, "label": "FY%s %s" % (base.get("fy") or _d(e).year, base.get("fp") or "")}
        for k in CONCEPTS:
            qv = data[k][0].get(e)
            row[k] = qv["val"] if qv else None
            if qv and qv.get("derived"):
                row.setdefault("derived", []).append(k)
        if row["gross_profit"] is None and row["revenue"] is not None and row["cost_of_revenue"] is not None:
            row["gross_profit"] = row["revenue"] - row["cost_of_revenue"]
        rev = row["revenue"]
        for k, name in (("gross_profit", "gross_margin"), ("operating_income", "operating_margin"),
                        ("net_income", "net_margin")):
            row[name] = round(row[k] / rev * 100, 1) if rev and row[k] is not None else None
        for k in ("revenue", "net_income", "eps_diluted", "operating_income"):
            prev = year_ago(data[k][0], e)
            row[k + "_yoy"] = pct(row[k], prev["val"] if prev else None)
        rows.append(row)
    for i in range(1, len(rows)):
        rows[i]["revenue_qoq"] = pct(rows[i]["revenue"], rows[i - 1]["revenue"])
    ttm = {}
    if len(rows) >= 4:
        for k in ("revenue", "net_income", "eps_diluted", "operating_cash_flow"):
            vals = [r[k] for r in rows[-4:]]
            ttm[k] = round(sum(vals), 2) if all(v is not None for v in vals) else None
        if len(rows) >= 8:
            pv = [r["revenue"] for r in rows[-8:-4]]
            if all(v is not None for v in pv) and ttm.get("revenue"):
                ttm["revenue_yoy"] = pct(ttm["revenue"], sum(pv))
    annual = [{"end": e, "fy": v.get("fy"), "revenue": v["val"],
               "net_income": (data["net_income"][1].get(e) or {}).get("val")}
              for e, v in sorted(data["revenue"][1].items())[-3:]]
    flags = []
    if rows:
        last = rows[-1]
        if len(rows) >= 2 and last.get("revenue_yoy") is not None and rows[-2].get("revenue_yoy") is not None:
            a, b = rows[-2]["revenue_yoy"], last["revenue_yoy"]
            if b < a - 5:
                flags.append("revenue growth slowing (%.1f%% -> %.1f%% YoY)" % (a, b))
            elif b > a + 5:
                flags.append("revenue growth accelerating (%.1f%% -> %.1f%% YoY)" % (a, b))
        if len(rows) >= 5 and last.get("net_margin") is not None and rows[-5].get("net_margin") is not None:
            d = last["net_margin"] - rows[-5]["net_margin"]
            if abs(d) >= 2:
                flags.append("net margin %s %.1f pts vs a year ago" % ("up" if d > 0 else "down", abs(d)))
        if last.get("net_income") is not None and last["net_income"] < 0:
            flags.append("loss-making quarter")
        ocf, ni = last.get("operating_cash_flow"), last.get("net_income")
        if ocf is not None and ni and ni > 0 and ocf < 0.5 * ni:
            flags.append("operating cash flow well below net income (check earnings quality)")
    return {"ticker": ticker.upper(), "company": facts.get("entityName"), "cik": facts.get("cik"),
            "quarters": rows, "ttm": ttm, "annual": annual, "flags": flags,
            "latest_period": rows[-1]["end"] if rows else None,
            "latest_filed": rq[ends[-1]]["filed"] if ends else None}


def latest_release(cik, ua):
    sub = T.sec_json("https://data.sec.gov/submissions/CIK%010d.json" % cik, ua, "submissions-%d.json" % cik, 6 * 3600)
    r = sub.get("filings", {}).get("recent", {})
    items = r.get("items", [])
    for i, form in enumerate(r.get("form", [])):
        if form.startswith("8-K") and i < len(items) and "2.02" in (items[i] or ""):
            acc = r["accessionNumber"][i]
            base = "https://www.sec.gov/Archives/edgar/data/%d/%s/" % (cik, acc.replace("-", ""))
            ex = None
            try:
                idx = T.sec_json(base + "index.json", ua)
                for it in idx.get("directory", {}).get("item", []):
                    nm = it.get("name", "").lower()
                    if "ex99" in nm.replace("-", "").replace("_", "") and nm.endswith((".htm", ".html", ".txt")):
                        ex = base + it["name"]
                        break
            except Exception:
                pass
            return {"filed": r["filingDate"][i], "accession": acc, "filing": base + r["primaryDocument"][i],
                    "press_release": ex}
    return None


def money(v):
    if v is None:
        return "-"
    a = abs(v)
    s = "%.2fB" % (a / 1e9) if a >= 1e9 else ("%.1fM" % (a / 1e6) if a >= 1e6 else "%.0f" % a)
    return ("-" if v < 0 else "") + s


def sp(v):
    return "-" if v is None else "%+.1f%%" % v


def num(v, f="%.1f"):
    return "-" if v is None else f % v


def render(o):
    L = ["EARNINGS  %s  %s  (CIK %s, latest period %s, filed %s)" % (
        o["ticker"], o.get("company") or "", o.get("cik"), o.get("latest_period"), o.get("latest_filed")),
         "%-11s %-8s %9s %8s %8s %9s %8s %7s %8s %6s %6s %6s" % (
             "period", "end", "revenue", "rev YoY", "rev QoQ", "net inc", "NI YoY", "EPS", "EPS YoY", "GM%", "OM%", "NM%")]
    for r in o["quarters"]:
        der = r.get("derived") or []
        m = lambda k: "~" if k in der else ""  # noqa: E731
        L.append("%-11s %-8s %9s %8s %8s %9s %8s %7s %8s %6s %6s %6s" % (
            r["label"][:11], r["end"][2:], money(r["revenue"]) + m("revenue"), sp(r.get("revenue_yoy")),
            sp(r.get("revenue_qoq")), money(r["net_income"]) + m("net_income"), sp(r.get("net_income_yoy")),
            num(r["eps_diluted"], "%.2f") + m("eps_diluted"), sp(r.get("eps_diluted_yoy")),
            num(r["gross_margin"]), num(r["operating_margin"]), num(r["net_margin"])))
    t = o["ttm"]
    if t:
        L.append("TTM: revenue %s (%s YoY) | net income %s | EPS %s | operating cash flow %s" % (
            money(t.get("revenue")), sp(t.get("revenue_yoy")), money(t.get("net_income")),
            num(t.get("eps_diluted"), "%.2f"), money(t.get("operating_cash_flow"))))
    L += ["flag: " + f for f in o["flags"]]
    rel = o.get("release")
    if rel:
        L.append("latest earnings 8-K: filed %s  %s" % (rel["filed"], rel["filing"]))
        if rel.get("press_release"):
            L.append("press release: %s" % rel["press_release"])
    L.append("~ = derived (full year minus Q1-Q3). Consensus estimates are not in SEC data.")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("--quarters", type=int, default=8)
    ap.add_argument("--facts", help="read company facts from a local JSON file")
    ap.add_argument("--no-release", action="store_true")
    ap.add_argument("--user-agent")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    outs = []
    for tk in a.tickers:
        cik, ua = None, None
        if a.facts:
            facts = T.load_json(a.facts)
        else:
            ua = T.sec_user_agent(a.user_agent)
            cik, _ = T.sec_cik(tk, ua)
            facts = T.sec_json("https://data.sec.gov/api/xbrl/companyfacts/CIK%010d.json" % cik, ua,
                               "companyfacts-%d.json" % cik, 12 * 3600)
        o = analyze(facts, tk, a.quarters)
        if cik and not a.no_release:
            try:
                o["release"] = latest_release(cik, ua)
            except Exception as e:
                o["release_error"] = str(e)
        T.save_json(T.tpath("metrics", "%s.json" % tk.upper()), o)
        o["text"] = render(o)
        outs.append(o)
    if len(outs) == 1:
        T.emit(outs[0], a.json)
    else:
        T.emit({"companies": outs, "text": "\n\n".join(o["text"] for o in outs)}, a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
