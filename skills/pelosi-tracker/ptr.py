#!/usr/bin/env python3
"""Pelosi Tracker: stock trades that members of the US House must disclose (STOCK Act).

    python3 ptr.py index --member pelosi            # her Periodic Transaction Reports this year
    python3 ptr.py index --year 2025 --member pelosi
    python3 ptr.py new                              # filings posted since the last run (any member)
    python3 ptr.py new --member pelosi --member crenshaw --fetch
    python3 ptr.py fetch 20033725                   # download one PTR and turn it into rows
    python3 ptr.py parse report.txt                 # parse text you already have

Source: the House Clerk's public disclosure index
(disclosures-clerk.house.gov/public_disc/financial-pdfs/<YEAR>FD.zip) and the
PTR PDFs (/public_disc/ptr-pdfs/<YEAR>/<DocID>.pdf). PDFs are turned into text
with `pdftotext` (poppler) when installed. Hand-written/scanned PTRs have no
text layer: the link is shown so you can read them yourself.

Disclosures arrive up to 45 days after the trade, and amounts are filed as
ranges ($1,001-$15,000, ...). This is public information, not a live feed.
Senate members file at efdsearch.senate.gov (see SKILL.md).
"""
import argparse
import datetime as dt
import io
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_HERE, "_lib"), os.path.join(_HERE, "..", "_lib")]
import tadawal as T  # noqa: E402

BASE = "https://disclosures-clerk.house.gov/public_disc"
OWNERS = {"SP": "spouse", "JT": "joint", "DC": "dependent child", "": "self"}
TX = {"P": "buy", "S": "sell", "S (partial)": "sell (partial)", "E": "exchange"}
AMOUNTS = [(1001, 15000), (15001, 50000), (50001, 100000), (100001, 250000), (250001, 500000),
           (500001, 1000000), (1000001, 5000000), (5000001, 25000000), (25000001, 50000000)]


def index_path(year):
    return T.tpath("cache", "house", "%sFD.zip" % year)


def load_index(year, max_age=3600, offline=False):
    p = index_path(year)
    if not offline and (not os.path.exists(p) or time.time() - os.path.getmtime(p) > max_age):
        data = T.http_get("%s/financial-pdfs/%sFD.zip" % (BASE, year), timeout=60)
        with open(p, "wb") as f:
            f.write(data)
    if not os.path.exists(p):
        T.die("no index for %s (offline and not cached)" % year)
    with open(p, "rb") as f:
        return parse_index_zip(f.read())


def parse_index_zip(raw):
    z = zipfile.ZipFile(io.BytesIO(raw))
    name = next(n for n in z.namelist() if n.lower().endswith(".xml"))
    root = ET.fromstring(z.read(name))
    rows = []
    for m in root.iter("Member"):
        g = lambda k: (m.findtext(k) or "").strip()  # noqa: E731
        rows.append({"doc_id": g("DocID"), "type": g("FilingType"), "last": g("Last"), "first": g("First"),
                     "prefix": g("Prefix"), "suffix": g("Suffix"), "district": g("StateDst"), "year": g("Year"),
                     "filed": _date(g("FilingDate"))})
    return rows


def _date(s):
    try:
        return dt.datetime.strptime(s, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return s


def ptrs(rows, members=None):
    out = [r for r in rows if r["type"] == "P"]
    if members:
        ms = [m.lower() for m in members]
        out = [r for r in out if any(m in (r["last"] + " " + r["first"]).lower() for m in ms)]
    out.sort(key=lambda r: (r["filed"], r["doc_id"]), reverse=True)
    return out


def pdf_url(r):
    return "%s/ptr-pdfs/%s/%s.pdf" % (BASE, r.get("year") or r["filed"][:4], r["doc_id"])


def pdf_text(path):
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-layout", path, "-"], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.decode("utf-8", "replace") if out.returncode == 0 else None


TX_LINE = re.compile(
    r"(?P<pre>.*?)\s(?P<tx>P|S \(partial\)|S|E)\s+(?P<tdate>\d{2}/\d{2}/\d{4})\s+(?P<ndate>\d{2}/\d{2}/\d{4})"
    r"\s*(?P<amt>(?:\$[\d,]+\s*-\s*(?:\$[\d,]+)?|Over \$[\d,]+|\$[\d,]+))?\s*(?P<rest>.*)$")


def parse_ptr_text(text, filed=None):
    """Turn pdftotext -layout output of an electronic House PTR into rows."""
    text = text.replace("\x00", "").replace(" ", " ")
    lines = [ln.rstrip() for ln in text.splitlines()]
    head = {}
    for ln in lines[:40]:
        m = re.search(r"Name:\s*(.+)", ln)
        if m and "name" not in head:
            head["name"] = m.group(1).strip()
        m = re.search(r"State/District:\s*(\S+)", ln)
        if m:
            head["district"] = m.group(1)
        m = re.search(r"Filing ID #?\s*(\d+)", ln)
        if m:
            head["doc_id"] = m.group(1)
    blocks, cur = [], None
    for ln in lines:
        m = TX_LINE.match(ln)
        if m and re.search(r"[A-Za-z]", m.group("pre") or ""):
            if cur:
                blocks.append(cur)
            cur = {"m": m, "more": []}
        elif cur is not None:
            if re.match(r"^\s*(\* For the complete list|Initial Public Offerings|Certification|I CERTIFY|Digitally Signed|Asset class details)", ln, re.I):
                blocks.append(cur)
                cur = None
            else:
                cur["more"].append(ln)
    if cur:
        blocks.append(cur)
    rows = []
    for b in blocks:
        m = b["m"]
        pre = m.group("pre").strip()
        owner = ""
        om = re.match(r"^(SP|JT|DC)\s+(.*)$", pre)
        if om:
            owner, pre = om.group(1), om.group(2)
        asset_parts = [pre]
        amt = (m.group("amt") or "").strip()
        desc, status, sub = [], None, None
        for ln in b["more"]:
            s = ln.strip()
            if not s:
                continue
            if re.match(r"^F\s*S\s*:", s):
                status = s.split(":", 1)[1].strip()
                continue
            if re.match(r"^S\s*O\s*:", s):
                sub = s.split(":", 1)[1].strip()
                continue
            if re.match(r"^D\s*:", s):
                desc.append(s.split(":", 1)[1].strip())
                continue
            if desc:
                desc.append(s)
                continue
            am = re.search(r"(\$[\d,]+)\s*$", s)
            if amt.endswith("-") and am:
                amt = amt + " " + am.group(1)
                s = s[:am.start()].strip()
            if s and not re.match(r"^(ID|Owner|Type|Date|Notification|Amount|Cap\.|Gains)", s):
                asset_parts.append(s)
        asset = " ".join(" ".join(asset_parts).split())
        tk = re.search(r"\(([A-Z][A-Z0-9.\-]{0,9})\)", asset)
        at = re.search(r"\[([A-Z]{2})\]", asset)
        name = re.sub(r"\s*\(([A-Z][A-Z0-9.\-]{0,9})\)\s*", " ", asset)
        name = re.sub(r"\s*\[[A-Z]{2}\]\s*", " ", name).strip()
        d = " ".join(desc)
        opt = {}
        om2 = re.search(r"([\d,]+)\s+(call|put)\s+options?", d, re.I)
        if om2:
            opt["contracts"] = int(om2.group(1).replace(",", ""))
            opt["right"] = om2.group(2).lower()
        sm = re.search(r"strike price of \$([\d,.]+)", d, re.I)
        if sm:
            opt["strike"] = float(sm.group(1).replace(",", "").rstrip("."))
        em = re.search(r"expiration date of (\d{1,2}/\d{1,2}/\d{2,4})", d, re.I)
        if em:
            opt["expiry"] = em.group(1)
        tdate = _date(m.group("tdate"))
        lo, hi = amount_range(amt)
        row = {"owner": OWNERS.get(owner, owner), "asset": name, "ticker": tk.group(1) if tk else None,
               "asset_type": at.group(1) if at else None, "tx": TX.get(m.group("tx"), m.group("tx")),
               "trade_date": tdate, "notified": _date(m.group("ndate")), "amount": amt, "amount_low": lo,
               "amount_high": hi, "description": d or None, "status": status, "subholding": sub}
        if opt:
            row["option"] = opt
        if filed:
            try:
                row["days_to_disclose"] = (dt.date.fromisoformat(filed) - dt.date.fromisoformat(tdate)).days
            except ValueError:
                pass
        rows.append(row)
    return {"filer": head, "rows": rows}


def amount_range(s):
    nums = [int(x.replace(",", "")) for x in re.findall(r"\$([\d,]+)", s or "")]
    if not nums:
        return None, None
    if s.lower().startswith("over"):
        return nums[0], None
    if len(nums) == 1:
        lo = nums[0]
        for a, b in AMOUNTS:
            if a == lo:
                return a, b
        return lo, None
    return nums[0], nums[1]


def fetch_ptr(r):
    p = T.tpath("cache", "house", "ptr", "%s.pdf" % r["doc_id"])
    if not os.path.exists(p):
        data = T.http_get(pdf_url(r), timeout=60)
        with open(p, "wb") as f:
            f.write(data)
    txt = pdf_text(p)
    if txt is None:
        return {"filer": {}, "rows": [], "note": "pdftotext not installed: install poppler (Windows: "
                "`winget install poppler` or conda), or open %s" % pdf_url(r)}
    if len(txt.strip()) < 50 or not re.search(r"\d{2}/\d{2}/\d{4}", txt):
        return {"filer": {}, "rows": [], "note": "scanned / hand-written filing with no text: open %s" % pdf_url(r)}
    out = parse_ptr_text(txt, r.get("filed"))
    if not out["rows"]:
        out["note"] = "no rows recognised: open %s" % pdf_url(r)
    return out


def watched():
    wl = set(s.upper() for s in T.load_json(T.tpath("watchlist.json"), {"symbols": []})["symbols"])
    try:
        wl |= {p["symbol"].upper() for p in T.account_snapshot()["positions"]}
    except SystemExit:
        pass
    return wl


def render_filing(r, parsed, mine):
    url = pdf_url(r) if (r.get("year") or r.get("filed")) else ""
    L = [" ".join(("%s  %s %s (%s)  DocID %s  %s" % (r["filed"], r["first"], r["last"], r["district"], r["doc_id"], url)).split())]
    for x in parsed.get("rows", []):
        o = x.get("option")
        opt = " | %s %s %s strike %s exp %s" % (o.get("contracts", ""), o.get("right", ""), "options", o.get("strike"),
                                                o.get("expiry")) if o else ""
        flag = "  ** YOU HOLD/WATCH %s **" % x["ticker"] if x["ticker"] and x["ticker"] in mine else ""
        L.append("   %-14s %-6s %-40.40s %s  traded %s  %s  (%s)%s%s" % (
            x["tx"], x["ticker"] or "-", x["asset"], x["asset_type"] or "", x["trade_date"], x["amount"], x["owner"],
            opt, flag))
        if x.get("days_to_disclose") is not None and x["days_to_disclose"] > 45:
            L.append("      filed %d days after the trade (late: the STOCK Act allows 45)" % x["days_to_disclose"])
    if parsed.get("note"):
        L.append("   note: " + parsed["note"])
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ix = sub.add_parser("index")
    ix.add_argument("--year", type=int, default=T.ny_now().year)
    ix.add_argument("--member", action="append")
    ix.add_argument("--limit", type=int, default=25)
    ix.add_argument("--fetch", action="store_true", help="also download and parse each PTR")
    nw = sub.add_parser("new")
    nw.add_argument("--member", action="append")
    nw.add_argument("--fetch", action="store_true")
    nw.add_argument("--limit", type=int, default=20)
    fe = sub.add_parser("fetch")
    fe.add_argument("doc_ids", nargs="+")
    fe.add_argument("--year", type=int, default=T.ny_now().year)
    pa = sub.add_parser("parse")
    pa.add_argument("file")
    a = ap.parse_args(argv)
    mine = watched()

    if a.cmd == "parse":
        if a.file.lower().endswith(".pdf"):
            txt = pdf_text(a.file)
        else:
            with open(a.file, encoding="utf-8", errors="replace") as f:
                txt = f.read()
        out = parse_ptr_text(txt or "")
        r = {"filed": "", "first": out["filer"].get("name", ""), "last": "", "district": out["filer"].get("district", ""),
             "doc_id": out["filer"].get("doc_id", "?"), "year": ""}
        T.emit(dict(out, text=render_filing(r, out, mine)), a.json)
        return 0

    if a.cmd == "fetch":
        rows = {r["doc_id"]: r for r in load_index(a.year)}
        L, res = [], []
        for d in a.doc_ids:
            r = rows.get(d) or {"doc_id": d, "filed": "", "first": "", "last": "", "district": "", "year": str(a.year)}
            parsed = fetch_ptr(r)
            res.append({"filing": r, **parsed})
            L.append(render_filing(r, parsed, mine))
            time.sleep(0.5)
        T.emit({"filings": res, "text": "\n\n".join(L)}, a.json)
        return 0

    year = getattr(a, "year", None) or T.ny_now().year
    rows = load_index(year)
    if a.cmd == "new" and T.ny_now().month == 1 and T.ny_now().day <= 15:
        try:
            rows += load_index(year - 1)
        except SystemExit:
            pass
    sel = ptrs(rows, a.member)
    seen_p = T.tpath("pelosi_seen.json")
    seen = T.load_json(seen_p, None)
    if a.cmd == "new":
        first_run = seen is None
        seen = seen or {"doc_ids": []}
        known = set(seen["doc_ids"])
        fresh = [r for r in sel if r["doc_id"] not in known]
        seen["doc_ids"] = sorted(known | {r["doc_id"] for r in ptrs(rows)})
        seen["last_check"] = T.ny_now().isoformat(timespec="seconds")
        T.save_json(seen_p, seen)
        show = fresh[:a.limit]
        head = ("first run: showing the latest %d PTRs and remembering the rest" % len(show)) if first_run else \
            "%d new PTR filing(s) since the last check" % len(fresh)
    else:
        show = sel[:a.limit]
        head = "%d PTR filings in %s%s (showing %d)" % (len(sel), year, " for " + ", ".join(a.member) if a.member else "", len(show))
    L, res = ["PELOSI TRACKER  " + head], []
    for r in show:
        parsed = fetch_ptr(r) if a.fetch else {}
        res.append({"filing": r, **parsed})
        L.append(render_filing(r, parsed, mine) if a.fetch else "%s  %s %s (%s)  DocID %s  %s" % (
            r["filed"], r["first"], r["last"], r["district"], r["doc_id"], pdf_url(r)))
        if a.fetch:
            time.sleep(0.5)
    if not a.fetch and show:
        L.append("details: ptr.py fetch %s" % " ".join(r["doc_id"] for r in show[:5]))
    T.emit({"filings": res, "text": "\n".join(L)}, a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
