"""Daily tender PDFs for the WhatsApp / Telegram groups.

Every morning (GitHub Actions) this makes four PDFs in the same layout as the
"TENDER LIST BY RAMASHANKAR PARVAT (RSP)" lists:

  data/pdf/Singrauli_MP_Aaj.pdf    MP Tenders, Singrauli district, last date today
  data/pdf/Singrauli_MP_Sabhi.pdf  MP Tenders, Singrauli district, all open tenders
  data/pdf/NCL_Aaj.pdf             Coal India - NCL, last date today
  data/pdf/NCL_Sabhi.pdf           Coal India - NCL, all open tenders

  -> https://tenders.rsparvat.com/data/pdf/<name>.pdf

The website is loaded locally in headless Chromium, so districts / organisation
names come from exactly the same code as tenders.rsparvat.com.
When the secret TELEGRAM_BOT_TOKEN is set, the PDFs are also posted to
Telegram (@TenderAlert by default, or the comma separated TELEGRAM_CHAT_IDS).
"""
import base64
import datetime
import html
import json
import os
import sys
import threading
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(ROOT, "data", "pdf")
SITE = "https://tenders.rsparvat.com"
WA_GROUP = "https://chat.whatsapp.com/E2P8hltnVFS4Qz9BzOSYaW"
WA_CHANNEL = "https://whatsapp.com/channel/0029Va6CjFn90x2yklf3rd3a"
TG_CHANNEL = "https://t.me/TenderAlert"
PDF_TOOL = "https://rsparvat.com/rsp-pdf-sign-compress/"
BUY_DSC = "https://secure.certificate.digital/web/dsc/referral/?bp=TE2WF5ODCDo="
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
ALL_PDF_START = datetime.date(2026, 10, 22)  # "Sabhi" PDFs are posted on this day and every 4th day after
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_IDS = [c.strip() for c in (os.environ.get("TELEGRAM_CHAT_IDS", "").strip() or "@TenderAlert").split(",") if c.strip()]

MP_COLS = [  # (label, key, width, align)
    ("S.N.", "sn", 3, "c"), ("District / City", "area", 6.5, "c"), ("Organisation", "org", 7, "c"),
    ("Tender ID", "id", 7, "c"), ("Name of Work (English)", "work", 19, "l"), ("Name of Work (Hindi)", "hi", 17, "l"),
    ("NIT Ref. No.", "nit", 8, "l"), ("PAC / Estimate / Tender Value", "pac", 7.5, "r"),
    ("Tender Fees with Portal Fees", "totalfee", 6.5, "r"), ("EMD Fee", "emd", 6, "r"),
    ("Total Payable Amount (Before Submission)", "payable", 7, "r"), ("Bid Submission End Date", "end", 7.5, "c"),
]
NCL_COLS = [
    ("S.N.", "sn", 3, "c"), ("Project", "area", 7, "c"), ("Tender ID", "id", 8, "c"),
    ("Name of Work (English)", "work", 24, "l"), ("Name of Work (Hindi)", "hi", 22, "l"), ("NIT Ref. No.", "nit", 10, "l"),
    ("PAC / Estimate / Tender Value", "pac", 9, "r"), ("EMD Fee", "emd", 7, "r"), ("Bid Submission End Date", "end", 9, "c"),
]


def log(*a):
    print("[daily-pdf]", *a, flush=True)


def load_rows():
    from playwright.sync_api import sync_playwright

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *a, **k):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=ROOT))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            exe = os.environ.get("CHROMIUM_PATH")
            browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
            ctx = browser.new_context(service_workers="block", timezone_id="Asia/Kolkata")
            ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith(f"http://127.0.0.1:{port}/") else r.abort())
            ctx.add_init_script("localStorage.setItem('rsp-customer',JSON.stringify({favOnly:true,city:''}))")
            page = ctx.new_page()
            page.goto(f"http://127.0.0.1:{port}/index.html", wait_until="load")
            page.wait_for_function("typeof all!=='undefined'&&all.length>0&&Object.keys(mpPlaceDistrict).length>0", timeout=90000)
            rows = page.evaluate(
                """(()=>{const num=v=>{v=n(v);return Number.isFinite(v)?v:null};
                return all.filter(t=>statusOf(t)!=='Closed'&&(t.source==='COAL'?isNCL(t):mpDistrictFrom(t)==='Singrauli')).map(t=>({
                  src:t.source,id:t.tender_id,area:t.source==='COAL'?coalLabel(t):areaOf(t),org:orgName(t),
                  work:String(t.work_en||''),hi:String(t.work_hi||''),nit:String(t.nit_ref||''),
                  pac:num(t.pac),fee:num(t.tender_fee),totalfee:(num(t.tender_fee)??null)===null&&num(t.processing_fee)===null?null:(num(t.tender_fee)||0)+(num(t.processing_fee)||0),
                  emd:num(t.emd),payable:num(payable(t)),end:t.bid_end||'',endMs:(d(t.bid_end)||new Date(0)).getTime(),today:closesToday(t)}))})()"""
            )
            generated = page.evaluate("payload.generated_at||''")
            browser.close()
            return rows, generated
    finally:
        server.shutdown()


def money(v):
    if v is None:
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    if v != v or v <= 0:
        return "—"
    s = f"{int(round(v)):d}"
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return "₹" + ",".join(parts + [tail]) if parts else "₹" + tail


def cell(r, key, i):
    if key == "sn":
        return str(i)
    if key in ("pac", "fee", "totalfee", "emd", "payable"):
        return money(r.get(key))
    return r.get(key) or "—"


LOGO = None


def build_html(rows, cols, heading, title, empty_text, upd):
    global LOGO
    if LOGO is None:
        LOGO = base64.b64encode(open(os.path.join(ROOT, "assets", "rsp-icon-192.png"), "rb").read()).decode()
    e = html.escape
    total = sum(c[2] for c in cols)
    head = "".join(f'<th style="width:{c[2] / total * 100:.2f}%">{e(c[0])}</th>' for c in cols)
    body = []
    for i, r in enumerate(rows, 1):
        tds = []
        for label, key, _w, align in cols:
            v = e(cell(r, key, i))
            if key == "id":
                v = f'<a href="{SITE}/?tender={e(r["id"])}">{v.replace("_", "_<wbr>")}</a>'
            tds.append(f'<td class="{align}{" end" if key == "end" else ""}">{v}</td>')
        body.append("<tr>" + "".join(tds) + "</tr>")
    table = (f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>" if rows
             else f'<p class="none">{e(empty_text)}</p>')
    return f"""<!doctype html><html lang="hi"><head><meta charset="utf-8"><style>
body{{font-family:'Noto Sans','Noto Sans Devanagari',Arial,sans-serif;color:#111;font-size:8.3px;margin:0}}
.top{{text-align:center;margin-bottom:6px}}.top img{{width:26px;height:26px}}
.top h1{{margin:0;font-size:19px;letter-spacing:.3px}}.tag{{font-size:10px;color:#333}}
.hd{{font-size:11px;font-weight:700;margin-top:2px}}.ct{{font-size:8.5px;color:#222}}
.warn{{color:#c00000;font-weight:700;font-size:9.5px;margin-top:3px}}
.ttl{{display:inline-block;margin-top:5px;padding:3px 12px;border-radius:4px;background:#17365d;color:#fff;font-size:11px;font-weight:700}}
table{{width:100%;border-collapse:collapse;table-layout:fixed}}
th{{background:#fff;border:1px solid #333;font-size:8.6px;font-weight:700;padding:4px 3px;text-align:center;vertical-align:middle}}
td{{border:1px solid #444;padding:3px;vertical-align:middle;word-wrap:break-word;overflow-wrap:anywhere}}
tr{{page-break-inside:avoid}}thead{{display:table-header-group}}
.c{{text-align:center}}.l{{text-align:left}}.r{{text-align:right;white-space:nowrap}}.end{{font-weight:700}}
a{{color:#111;text-decoration:none}}.none{{text-align:center;font-size:14px;margin:40px 0;color:#17365d}}
.join{{text-align:center;font-size:8.5px;margin-top:6px;color:#333}}.join a{{color:#0b57d0}}
</style></head><body>
<div class="top"><img src="data:image/png;base64,{LOGO}"><h1>TENDER LIST BY RAMASHANKAR PARVAT (RSP)</h1>
<div class="tag">Reliable, Secure &amp; Perfect</div><div class="hd">{e(heading)}</div>
<div class="ct">Contact: Ramashankar Parvat | Mob/WhatsApp: 9752318084 | Email: rsparvat@gmail.com | Website: rsparvat.com</div>
<div class="warn">इस सूची को अंतिम या पूर्णतः सही न मानें। संबंधित आधिकारिक पोर्टल से मिलान अवश्य करें।</div>
<div class="ttl">{e(title)}</div></div>
{table}
<div class="join">डेटा अपडेट: {e(upd)} · पूरी लाइव लिस्ट: <a href="{SITE}">tenders.rsparvat.com</a> · रोज़ लिस्ट पाने के लिए जुड़ें:
<a href="{WA_GROUP}">WhatsApp ग्रुप</a> · <a href="{WA_CHANNEL}">WhatsApp चैनल</a> · <a href="{TG_CHANNEL}">Telegram @TenderAlert</a><br>🖊️ <b>RSP PDF Sign Compress</b> – टेंडर दस्तावेज़ों पर DSC से साइन, PDF कम्प्रेस व मर्ज करें (30 दिन मुफ़्त): <a href="{PDF_TOOL}">rsparvat.com/rsp-pdf-sign-compress</a><br>🔐 <b>DSC खरीदें / Buy DSC</b> – 5 मिनट में अप्रूवल: <a href="{BUY_DSC}">यहाँ क्लिक करें / Click here</a></div>
</body></html>"""


def write_pdfs(jobs):
    from playwright.sync_api import sync_playwright

    os.makedirs(OUT_DIR, exist_ok=True)
    with sync_playwright() as p:
        exe = os.environ.get("CHROMIUM_PATH")
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        page = browser.new_page()
        for path, doc in jobs:
            page.set_content(doc, wait_until="load")
            page.pdf(path=path, format="A4", landscape=True, print_background=True, display_header_footer=True,
                     header_template="<span></span>",
                     footer_template='<div style="font-size:8px;width:100%;padding:0 10mm;display:flex;justify-content:space-between;color:#333">'
                                     '<span>rsparvat.com • Mob/WhatsApp: 9752318084</span>'
                                     '<span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span><span></span></div>',
                     margin={"top": "8mm", "bottom": "12mm", "left": "8mm", "right": "8mm"})
        browser.close()


def telegram_send(path, fname, caption):
    if not BOT_TOKEN:
        return
    boundary = "----rspdaily" + datetime.datetime.now().strftime("%H%M%S%f")
    pdf = open(path, "rb").read()
    for chat in CHAT_IDS:
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                 for k, v in (("chat_id", chat), ("caption", caption))]
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{fname}"\r\n'
                     f"Content-Type: application/pdf\r\n\r\n".encode() + pdf + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        req = urllib.request.Request(f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument", data=b"".join(parts),
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                ok = json.loads(res.read().decode()).get("ok")
            log("telegram", chat, fname, "sent" if ok else "not ok")
        except Exception as exc:  # never fail the job because of Telegram
            log("telegram failed:", chat, type(exc).__name__, str(exc)[:120].replace(BOT_TOKEN, "***"))


def main():
    rows, generated = load_rows()
    now = datetime.datetime.now(IST)
    day = now.strftime("%d-%b-%Y")
    stamp = now.strftime("%d-%m-%Y")
    try:
        upd = datetime.datetime.fromisoformat(generated.replace("Z", "+00:00")).astimezone(IST).strftime("%d-%b-%Y %I:%M %p")
    except Exception:
        upd = ""
    rows.sort(key=lambda r: (r["endMs"], r["org"], r["id"]))
    mp = [r for r in rows if r["src"] == "MP"]
    ncl = [r for r in rows if r["src"] == "COAL"]
    mp_today = [r for r in mp if r["today"]]
    ncl_today = [r for r in ncl if r["today"]]
    mp_head = "Organisation: Multiple Organisations | District / City: Singrauli"
    ncl_head = "Company: Northern Coalfields Limited | Project: Multiple Districts / Cities"
    files = [
        ("Singrauli_MP_Aaj.pdf", mp_today, MP_COLS, mp_head, f"आज अंतिम तिथि वाले टेंडर / Last Date Today: {day} · कुल {len(mp_today)}",
         "आज सिंगरौली जिले के किसी MP टेंडर की अंतिम तिथि नहीं है।", f"MP_Tenders_Singrauli_Aaj_{stamp}.pdf"),
        ("Singrauli_MP_Sabhi.pdf", mp, MP_COLS, mp_head, f"सभी चालू टेंडर / All Open Tenders · {day} · कुल {len(mp)}",
         "अभी सिंगरौली जिले का कोई चालू MP टेंडर नहीं है।", f"MP_Tenders_Singrauli_All_{stamp}.pdf"),
        ("NCL_Aaj.pdf", ncl_today, NCL_COLS, ncl_head, f"आज अंतिम तिथि वाले टेंडर / Last Date Today: {day} · कुल {len(ncl_today)}",
         "आज NCL के किसी टेंडर की अंतिम तिथि नहीं है।", f"NCL_Tenders_Aaj_{stamp}.pdf"),
        ("NCL_Sabhi.pdf", ncl, NCL_COLS, ncl_head, f"सभी चालू टेंडर / All Open Tenders · {day} · कुल {len(ncl)}",
         "अभी NCL का कोई चालू टेंडर नहीं है।", f"NCL_Tenders_All_{stamp}.pdf"),
    ]
    write_pdfs([(os.path.join(OUT_DIR, f[0]), build_html(f[1], f[2], f[3], f[4], f[5], upd)) for f in files])
    log(f"Singrauli MP: today {len(mp_today)}, all {len(mp)} | NCL: today {len(ncl_today)}, all {len(ncl)}")

    links = f"\n\nपूरी लाइव लिस्ट: {SITE}\nWhatsApp ग्रुप: {WA_GROUP}\nWhatsApp चैनल: {WA_CHANNEL}\n\n🖊️ टेंडर PDF पर DSC साइन / कम्प्रेस / मर्ज (30 दिन मुफ़्त): {PDF_TOOL}\n🔐 DSC खरीदें (5 मिनट में अप्रूवल): {BUY_DSC}\n– रमाशंकर पर्वत (9752318084)"
    captions = [
        f"📅 आज ({day}) अंतिम तिथि – MP टेंडर, सिंगरौली जिला: {len(mp_today)}",
        f"📋 सभी चालू MP टेंडर – सिंगरौली जिला ({day}): {len(mp)}",
        f"📅 आज ({day}) अंतिम तिथि – NCL टेंडर: {len(ncl_today)}",
        f"📋 सभी चालू NCL टेंडर ({day}): {len(ncl)}",
    ]
    # "Aaj" PDFs go out every day; the "Sabhi" (all open tenders) PDFs every 4th day.
    # All four files are refreshed on the website every day either way.
    all_day = (now.date() - ALL_PDF_START).days % 4 == 0 or os.environ.get("SEND_ALL_PDF") == "1"
    send = [(f, cap) for f, cap in zip(files, captions)
            if f[1] and (f[0].endswith("_Aaj.pdf") or all_day)]
    log("posting:", ", ".join(f[0] for f, _ in send) or "nothing", "| all-tenders day:", all_day)
    for k, (f, cap) in enumerate(send):
        telegram_send(os.path.join(OUT_DIR, f[0]), f[6], cap + (links if k == len(send) - 1 else ""))
    if not BOT_TOKEN:
        log("TELEGRAM_BOT_TOKEN not set - PDFs saved, nothing posted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
