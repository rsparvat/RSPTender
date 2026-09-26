"""Send favourite-tender push notifications after each data refresh.

Runs in GitHub Actions right after scraper.py. It opens the website locally in
headless Chromium so the district / organisation names are worked out by the
very same code the website uses, then compares every subscriber's favourites
with the fresh data and sends Web Push messages:

  * new tenders in the subscriber's favourite districts / organisations
  * favourite tenders that close within the next 3 hours
  * one morning summary: favourite tenders ending today / tomorrow

Needs the secrets VAPID_PRIVATE_KEY and PUSH_TOKEN (the job skips quietly
without them). Subscriptions are kept in the Google Apps Script backend.
"""
import datetime
import hashlib
import json
import os
import sys
import threading
import urllib.parse
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(ROOT, "data", "push_state.json")
PUSH_API = os.environ.get("PUSH_API", "").strip()
PUSH_TOKEN = os.environ.get("PUSH_TOKEN", "").strip()
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
VAPID_SUB = "mailto:rsparvat@gmail.com"
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
SOON_MS = 3 * 3600 * 1000
MAX_NEW_PER_RUN = 400  # more than this means the cache was rebuilt, not real new tenders

TEXT = {
    "en": {
        "new1": "⭐ New tender – {org}",
        "newN": "⭐ {n} new tenders in your favourites",
        "soon1": "⏰ Closing soon – {org}",
        "soonN": "⏰ {n} favourite tenders close within 3 hours",
        "end": "Submission end: {end}",
        "tap": "Tap to see the list",
        "digest": "⭐ Favourites: {today} end today, {tomorrow} end tomorrow",
    },
    "hi": {
        "new1": "⭐ नया टेंडर – {org}",
        "newN": "⭐ आपके पसंदीदा में {n} नए टेंडर",
        "soon1": "⏰ जल्द बंद – {org}",
        "soonN": "⏰ पसंदीदा के {n} टेंडर 3 घंटे में बंद होंगे",
        "end": "अंतिम तिथि: {end}",
        "tap": "लिस्ट देखने के लिए टैप करें",
        "digest": "⭐ पसंदीदा: आज {today} और कल {tomorrow} टेंडर की अंतिम तिथि",
    },
}


def log(*a):
    print("[push]", *a, flush=True)


def api(params, timeout=40):
    url = PUSH_API + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


def tender_keys():
    """Load the site locally and read each open tender's district / organisation."""
    from playwright.sync_api import sync_playwright

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *a, **k):
            pass

    handler = partial(Quiet, directory=ROOT)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            exe = os.environ.get("CHROMIUM_PATH")
            browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
            ctx = browser.new_context(service_workers="block", timezone_id="Asia/Kolkata")
            ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith(f"http://127.0.0.1:{port}/") else r.abort())
            ctx.add_init_script("localStorage.setItem('rsp-customer',JSON.stringify({name:'bot',mobile:'0000000000'}))")
            page = ctx.new_page()
            page.goto(f"http://127.0.0.1:{port}/index.html", wait_until="load")
            page.wait_for_function("typeof all!=='undefined'&&all.length>0&&Object.keys(mpPlaceDistrict).length>0", timeout=90000)
            keys = page.evaluate(
                """all.filter(t=>statusOf(t)!=='Closed').map(t=>{let e=d(t.bid_end);return {
                    id:t.tender_id,src:t.source==='COAL'?'COAL':'MP',
                    area:t.source==='COAL'?coalArea(t):mpDistrictFrom(t),org:orgName(t),unit:t.org_unit||'',
                    end:t.bid_end||'',endMs:e?e.getTime():0,isNew:!!t.is_new,work:String(workDesc(t)||'').slice(0,120)}})"""
            )
            browser.close()
            return keys
    finally:
        server.shutdown()


def matches(t, favs):
    f = (favs or {}).get(t["src"]) or {}
    return (t["area"] and t["area"] in (f.get("area") or [])) or (t["org"] and t["org"] in (f.get("org") or [])) or (
        t["unit"] and t["unit"] in (f.get("unit") or [])
    )


def send(sub, payload):
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info=sub,
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": VAPID_SUB},
            ttl=6 * 3600,
        )
        return "ok"
    except WebPushException as exc:
        code = getattr(getattr(exc, "response", None), "status_code", 0)
        return "gone" if code in (404, 410) else f"error {code}"
    except Exception as exc:  # network trouble etc.
        return f"error {type(exc).__name__}"


def main():
    if not (PUSH_API and PUSH_TOKEN and VAPID_PRIVATE_KEY):
        log("push secrets not set - skipping")
        return 0
    try:
        rows = api({"push": "list", "token": PUSH_TOKEN}).get("rows") or []
    except Exception as exc:
        log("could not load subscriptions:", type(exc).__name__)
        return 0
    if not rows:
        log("no subscribers")
        return 0
    keys = tender_keys()
    now = datetime.datetime.now(IST)
    now_ms = int(now.timestamp() * 1000)
    day = now.strftime("%Y-%m-%d")
    tomorrow = (now + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    day_of = lambda ms: datetime.datetime.fromtimestamp(ms / 1000, IST).strftime("%Y-%m-%d") if ms else ""
    new_total = sum(1 for t in keys if t["isNew"])
    allow_new = new_total <= MAX_NEW_PER_RUN
    log(f"{len(keys)} open tenders, {new_total} new, {len(rows)} subscribers")

    try:
        state = json.load(open(STATE_FILE, encoding="utf-8"))
    except Exception:
        state = {}
    if state.get("day") != day:
        state = {"day": day, "digest": state.get("digest", {}), "soon": {}, "new": {}}
    stats = {"sent": 0, "gone": 0, "errors": 0}
    gone = []
    for row in rows:
        try:
            sub = json.loads(row.get("sub") or "{}")
            favs = json.loads(row.get("favs") or "{}")
        except Exception:
            continue
        endpoint = sub.get("endpoint")
        if not endpoint:
            continue
        h = hashlib.sha1(endpoint.encode()).hexdigest()[:14]
        tx = TEXT["hi" if row.get("lang") == "hi" else "en"]
        mine = [t for t in keys if matches(t, favs)]
        if not mine:
            continue
        msgs = []
        sent_new = set(state["new"].get(h, []))
        fresh = [t for t in mine if allow_new and t["isNew"] and t["id"] not in sent_new]
        if len(fresh) == 1:
            t = fresh[0]
            msgs.append({"title": tx["new1"].format(org=t["org"]), "body": "\n".join(x for x in [t["area"], t["work"], tx["end"].format(end=t["end"])] if x),
                         "url": "./?tender=" + urllib.parse.quote(t["id"]), "tag": "rsp-fav-new"})
        elif fresh:
            names = ", ".join(list(dict.fromkeys(t["org"] for t in fresh))[:3])
            msgs.append({"title": tx["newN"].format(n=len(fresh)), "body": names + "\n" + tx["tap"], "url": "./?view=favnew", "tag": "rsp-fav-new"})
        state["new"][h] = sorted(sent_new | {t["id"] for t in fresh})

        sent_soon = set(state["soon"].get(h, []))
        soon = [t for t in mine if 0 < t["endMs"] - now_ms <= SOON_MS and t["id"] not in sent_soon]
        if len(soon) == 1:
            t = soon[0]
            msgs.append({"title": tx["soon1"].format(org=t["org"]), "body": "\n".join(x for x in [t["work"], tx["end"].format(end=t["end"])] if x),
                         "url": "./?tender=" + urllib.parse.quote(t["id"]), "tag": "rsp-fav-soon"})
        elif soon:
            names = ", ".join(list(dict.fromkeys(t["org"] for t in soon))[:3])
            msgs.append({"title": tx["soonN"].format(n=len(soon)), "body": names + "\n" + tx["tap"], "url": "./?view=favtoday", "tag": "rsp-fav-soon"})
        state["soon"][h] = sorted(sent_soon | {t["id"] for t in soon})

        if now.hour >= 7 and state["digest"].get(h) != day:
            n_today = sum(1 for t in mine if t["endMs"] > now_ms and day_of(t["endMs"]) == day)
            n_tom = sum(1 for t in mine if day_of(t["endMs"]) == tomorrow)
            state["digest"][h] = day
            if n_today or n_tom:
                msgs.append({"title": tx["digest"].format(today=n_today, tomorrow=n_tom), "body": tx["tap"],
                             "url": "./?view=" + ("favtoday" if n_today else "favtomorrow"), "tag": "rsp-fav-digest"})

        for m in msgs[:3]:
            result = send(sub, m)
            if result == "ok":
                stats["sent"] += 1
            elif result == "gone":
                stats["gone"] += 1
                gone.append(endpoint)
                break
            else:
                stats["errors"] += 1
    keep = {r_h for r_h in state["digest"] if state["digest"][r_h] >= (now - datetime.timedelta(days=3)).strftime("%Y-%m-%d")}
    state["digest"] = {k: v for k, v in state["digest"].items() if k in keep}
    for endpoint in gone:
        try:
            api({"push": "del", "token": PUSH_TOKEN, "endpoint": endpoint})
        except Exception:
            pass
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, separators=(",", ":"))
    log(f"sent {stats['sent']}, expired {stats['gone']}, errors {stats['errors']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # never break the data refresh
        log("failed:", type(exc).__name__, exc)
        sys.exit(0)
