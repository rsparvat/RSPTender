#!/usr/bin/env python3
"""Build plain HTML tender lists (no JavaScript) from data/tenders.json.

Search engines and AI tools (Google, ChatGPT, Gemini...) usually do not run
JavaScript, so the live app shows them an empty table. These pages give them
the same tender data as simple HTML.  Run before the GitHub Pages upload.

Output:
  list/index.html            overview + closing today + Singrauli + NCL + links
  list/mp/<district>.html    MP Tenders, one page per district
  list/coal/<company>.html   Coal India, one page per company
  sitemap.xml, robots.txt, llms.txt
"""
import datetime as dt
import html
import json
import os
import re
import sys
from collections import defaultdict

SITE = "https://tenders.rsparvat.com/"
MAIN = "https://rsparvat.com/"
TG = "https://t.me/+R8A46oAs9fpmODA9"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "list")


def esc(s):
    return html.escape(str(s or ""), quote=True)


def slug(s):
    s = re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")
    return s or "other"


def parse(iso):
    try:
        d = dt.datetime.fromisoformat(iso)
        return d if d.tzinfo else d.replace(tzinfo=IST)
    except Exception:
        return None


def fdate(iso, time=True):
    d = parse(iso)
    if not d:
        return ""
    d = d.astimezone(IST)
    return d.strftime("%d/%m/%Y %I:%M %p") if time else d.strftime("%d/%m/%Y")


def rupees(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    n = f"{v:.0f}"
    if len(n) > 3:
        head, tail = n[:-3], n[-3:]
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
        n = head + "," + tail
    return "₹" + n


CSS = """*{box-sizing:border-box}body{margin:0;font-family:Arial,"Noto Sans Devanagari",sans-serif;background:#f4f7fb;color:#172033;line-height:1.45}
header{background:#17365d;color:#fff;padding:14px 16px;border-bottom:4px solid #d4a029}header h1{margin:0;font-size:20px}header p{margin:4px 0 0;font-size:13px;color:#dbe7f4}
header a{color:#ffd978}main{max-width:1200px;margin:auto;padding:14px 16px 30px}h2{font-size:18px;color:#17365d;margin:22px 0 8px}
nav.crumb{font-size:13px;margin-bottom:10px}.links{display:flex;flex-wrap:wrap;gap:6px;padding:0;list-style:none}.links a{display:inline-block;background:#fff;border:1px solid #c9d6e6;border-radius:6px;padding:5px 9px;text-decoration:none;color:#17365d;font-size:14px}
.tw{overflow-x:auto;background:#fff;border:1px solid #d5dde8;border-radius:8px}table{border-collapse:collapse;width:100%;font-size:13px}th,td{border-bottom:1px solid #e3e9f1;padding:6px 8px;text-align:left;vertical-align:top}
th{background:#17365d;color:#fff}td.n{white-space:nowrap;text-align:right}td.d{white-space:nowrap}.hi{color:#555;font-size:12px;display:block;margin-top:2px}
.note{background:#fff7e0;border:1px solid #f0c36d;border-radius:8px;padding:9px 12px;font-size:14px}footer{max-width:1200px;margin:auto;padding:16px;font-size:13px;color:#444;border-top:1px solid #d5dde8}footer a{color:#0563c1}"""


def page(title, desc, path, body, updated):
    canon = SITE + path
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><meta name="description" content="{esc(desc)}"><link rel="canonical" href="{canon}">
<link rel="icon" href="{SITE}assets/rsp-icon-192.png"><style>{CSS}</style></head><body>
<header><h1>{esc(title)}</h1><p>By Ramashankar Parvat (RSP) · Updated {esc(updated)} IST · <a href="{SITE}">Open live Tender Monitor app</a> · <a href="{MAIN}">rsparvat.com</a></p></header>
<main>{body}</main>
<footer>Data is collected from official portals (mptenders.gov.in, coalindiatenders.nic.in) and refreshed several times a day. Always verify on the official portal before bidding.<br>
🌐 Website: <a href="{MAIN}">rsparvat.com</a> · Live app: <a href="{SITE}">tenders.rsparvat.com</a> · Telegram: <a href="{TG}">Tender Alert</a> · WhatsApp: 9752318084<br>
Services: DSC (Digital Signature), e-Tender submission, EPF/ESIC registration &amp; returns, MP PWD contractor registration – <a href="{MAIN}">rsparvat.com</a></footer>
</body></html>
"""


def table(rows, show_area=True):
    out = ['<div class="tw"><table><thead><tr><th>#</th><th>Last date</th><th>Name of work</th>'
           + ('<th>District / Area</th>' if show_area else '')
           + '<th>Organisation</th><th>Value (PAC)</th><th>EMD</th><th>Tender ID</th><th>Published</th></tr></thead><tbody>']
    for i, t in enumerate(rows, 1):
        tid = esc(t.get("tender_id"))
        link = t.get("detail_url")
        tid_html = f'<a href="{esc(link)}" rel="nofollow">{tid}</a>' if link else tid
        hi = f'<span class="hi" lang="hi">{esc(t.get("work_hi"))}</span>' if t.get("work_hi") else ""
        corr = f' <b>(Corrigendum {t.get("corrigendum_count")})</b>' if t.get("corrigendum_count") else ""
        area = esc(t.get("district_city") or t.get("district") or t.get("location"))
        org = esc(t.get("organisation"))
        if t.get("org_unit"):
            org += "<br><small>" + esc(t.get("org_unit")) + "</small>"
        out.append(
            f'<tr><td>{i}</td><td class="d">{fdate(t.get("bid_end_iso"))}</td>'
            f'<td>{esc(t.get("work_en"))}{corr}{hi}</td>'
            + (f'<td>{area}</td>' if show_area else '')
            + f'<td>{org}</td><td class="n">{rupees(t.get("pac"))}</td><td class="n">{rupees(t.get("emd"))}</td>'
            f'<td>{tid_html}</td><td class="d">{fdate(t.get("published_iso"), False)}</td></tr>')
    out.append("</tbody></table></div>")
    return "".join(out)


def main():
    with open(os.path.join(ROOT, "data", "tenders.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    now = dt.datetime.now(IST)
    gen = parse(data.get("generated_at") or "") or now
    updated = gen.astimezone(IST).strftime("%d/%m/%Y %I:%M %p")
    live = [t for t in data.get("tenders", []) if (parse(t.get("bid_end_iso") or "") or now) >= now]
    live.sort(key=lambda t: t.get("bid_end_iso") or "9999")

    mp, coal = defaultdict(list), defaultdict(list)
    for t in live:
        if t.get("source") == "MP":
            mp[t.get("district") or t.get("district_city") or "Other"].append(t)
        else:
            coal[t.get("organisation_short") or t.get("organisation") or "Other"].append(t)

    os.makedirs(os.path.join(OUT, "mp"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "coal"), exist_ok=True)
    urls = [SITE, SITE + "list/"]

    def write(rel, text):
        with open(os.path.join(OUT, rel), "w", encoding="utf-8") as fh:
            fh.write(text)

    for name, rows in mp.items():
        rel = f"mp/{slug(name)}.html"
        title = f"{name} District Live Tenders – MP Tenders ({len(rows)})"
        desc = (f"All {len(rows)} live MP e-tenders of {name} district, Madhya Pradesh (PWD, PHED, RES, WRD, "
                f"Nagar Nigam etc.) with value, EMD and last date. Updated {updated}.")
        body = (f'<nav class="crumb"><a href="../">All tender lists</a> › MP Tenders › {esc(name)}</nav>'
                f'<p class="note">{len(rows)} live tenders of <b>{esc(name)}</b> district from mptenders.gov.in, '
                f'sorted by last date of submission.</p>' + table(rows, show_area=False))
        write(rel, page(title, desc, "list/" + rel, body, updated))
        urls.append(SITE + "list/" + rel)

    for name, rows in coal.items():
        rel = f"coal/{slug(name)}.html"
        title = f"{name} Live Tenders – Coal India ({len(rows)})"
        desc = (f"All {len(rows)} live Coal India e-tenders of {name} with area, value, EMD and last date. "
                f"Updated {updated}.")
        body = (f'<nav class="crumb"><a href="../">All tender lists</a> › Coal India › {esc(name)}</nav>'
                f'<p class="note">{len(rows)} live tenders of <b>{esc(name)}</b> from coalindiatenders.nic.in, '
                f'sorted by last date of submission.</p>' + table(rows))
        write(rel, page(title, desc, "list/" + rel, body, updated))
        urls.append(SITE + "list/" + rel)

    today = now.date()
    closing = [t for t in live if (parse(t.get("bid_end_iso")) or now).astimezone(IST).date() == today]
    sing = mp.get("Singrauli", [])
    ncl = coal.get("NCL", [])
    links_mp = "".join(f'<li><a href="mp/{slug(n)}.html">{esc(n)} ({len(r)})</a></li>'
                       for n, r in sorted(mp.items()))
    links_coal = "".join(f'<li><a href="coal/{slug(n)}.html">{esc(n)} ({len(r)})</a></li>'
                         for n, r in sorted(coal.items(), key=lambda x: -len(x[1])))
    body = (f'<p class="note">Live tenders right now: <b>{sum(map(len, mp.values()))}</b> on MP Tenders '
            f'(Madhya Pradesh) and <b>{sum(map(len, coal.values()))}</b> on Coal India. '
            f'Plain list for quick reading; for filters, alerts, PDF and Excel use the '
            f'<a href="{SITE}">live Tender Monitor app</a>.</p>'
            f'<h2>MP Tenders – by district</h2><ul class="links">{links_mp}</ul>'
            f'<h2>Coal India – by company</h2><ul class="links">{links_coal}</ul>'
            f'<h2>Last date today ({today.strftime("%d/%m/%Y")}) – {len(closing)} tenders</h2>'
            + (table(closing) if closing else "<p>No tender closes today.</p>")
            + f'<h2>Singrauli district (MP Tenders) – {len(sing)} live</h2>' + (table(sing) if sing else "<p>None.</p>")
            + f'<h2>NCL (Northern Coalfields, Singrauli) – {len(ncl)} live</h2>' + (table(ncl) if ncl else "<p>None.</p>"))
    write("index.html", page("Live Tender List – MP Tenders & Coal India (district-wise)",
                             f"District-wise list of all live MP e-tenders and Coal India tenders with value, EMD "
                             f"and last date. Updated {updated}. By Ramashankar Parvat, rsparvat.com.",
                             "list/", body, updated))

    lastmod = gen.astimezone(dt.timezone.utc).strftime("%Y-%m-%d")
    with open(os.path.join(ROOT, "sitemap.xml"), "w", encoding="utf-8") as fh:
        fh.write('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        for u in urls:
            fh.write(f"<url><loc>{u}</loc><lastmod>{lastmod}</lastmod><changefreq>hourly</changefreq></url>\n")
        fh.write("</urlset>\n")
    with open(os.path.join(ROOT, "robots.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"User-agent: *\nAllow: /\nDisallow: /share.html\n\nSitemap: {SITE}sitemap.xml\n")
    with open(os.path.join(ROOT, "llms.txt"), "w", encoding="utf-8") as fh:
        fh.write(
            "# RSP Tender Monitor (tenders.rsparvat.com)\n\n"
            "> Live government tenders of Madhya Pradesh (mptenders.gov.in) and Coal India "
            "(coalindiatenders.nic.in), by Ramashankar Parvat (rsparvat.com).\n\n"
            "The main app needs JavaScript. To read the tender data as plain HTML use these pages:\n\n"
            f"- [All lists, closing today, Singrauli and NCL]({SITE}list/)\n"
            f"- MP Tenders, one page per district: {SITE}list/mp/<district>.html "
            f"(e.g. {SITE}list/mp/singrauli.html, {SITE}list/mp/bhopal.html)\n"
            f"- Coal India, one page per company: {SITE}list/coal/<company>.html "
            f"(e.g. {SITE}list/coal/ncl.html)\n\n"
            f"Updated {updated} IST. Website: rsparvat.com, WhatsApp 9752318084.\n")
    print(f"static lists: {len(mp)} MP districts, {len(coal)} coal companies, {len(live)} live tenders, "
          f"{len(closing)} closing today")
    return 0


if __name__ == "__main__":
    sys.exit(main())
