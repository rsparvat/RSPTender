import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


VERSION = "2.6.1 AREA_PARSER_FIX / web-parity"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "Chrome/124 Safari/537.36"
)

PORTALS = {
    "MP": {
        "name": "MP Tenders",
        "base": "https://mptenders.gov.in",
        "home": "https://mptenders.gov.in/nicgep/app",
        "org": "https://mptenders.gov.in/nicgep/app?page=FrontEndTendersByOrganisation&service=page",
    },
    "COAL": {
        "name": "Coal India Tenders",
        "base": "https://coalindiatenders.nic.in",
        "home": "https://coalindiatenders.nic.in/nicgep/app",
        "org": "https://coalindiatenders.nic.in/nicgep/app?page=FrontEndTendersByOrganisation&service=page",
    },
}

CONNECT_TIMEOUT = int(os.environ.get("CONNECT_TIMEOUT", "10"))
READ_TIMEOUT = int(os.environ.get("READ_TIMEOUT", "60"))
ORG_INDEX_READ_TIMEOUT = int(os.environ.get("ORG_INDEX_READ_TIMEOUT", "90"))
REQUEST_RETRIES = int(os.environ.get("REQUEST_RETRIES", "4"))
DETAIL_REFRESH_HOURS = int(os.environ.get("DETAIL_REFRESH_HOURS", "6"))
MAX_DETAIL_FETCH_PER_PORTAL = int(os.environ.get("MAX_DETAIL_FETCH_PER_PORTAL", "900"))
PORTAL_BUDGET_SECONDS = int(os.environ.get("PORTAL_BUDGET_SECONDS", "720"))
MAX_TRANSLATIONS_PER_RUN = int(os.environ.get("MAX_TRANSLATIONS_PER_RUN", "120"))
TRANSLATION_BUDGET_SECONDS = int(os.environ.get("TRANSLATION_BUDGET_SECONDS", "90"))
DATA_PATH = os.environ.get("TENDER_DATA_PATH", "data/tenders.json")
MP_PLACES_PATH = os.path.join(os.path.dirname(__file__), "data", "mp_places.json")
NCL_NAME = "Northern Coalfields Limited"
_MP_PLACE_INDEX = None

MP_DISTRICTS = [
    "Agar Malwa", "Alirajpur", "Anuppur", "Ashoknagar", "Balaghat", "Barwani",
    "Betul", "Bhind", "Bhopal", "Burhanpur", "Chhatarpur", "Chhindwara",
    "Damoh", "Datia", "Dewas", "Dhar", "Dindori", "Guna", "Gwalior", "Harda",
    "Indore", "Jabalpur", "Jhabua", "Katni", "Khandwa", "Khargone", "Maihar",
    "Mandla", "Mandsaur", "Mauganj", "Morena", "Narmadapuram", "Narsinghpur",
    "Neemuch", "Niwari", "Pandhurna", "Panna", "Raisen", "Rajgarh", "Ratlam",
    "Rewa", "Sagar", "Satna", "Sehore", "Seoni", "Shahdol", "Shajapur",
    "Sheopur", "Shivpuri", "Sidhi", "Singrauli", "Tikamgarh", "Ujjain",
    "Umaria", "Vidisha",
]

ORG_SHORT_OVERRIDES = {
    "Northern Coalfields Limited": "NCL",
    "South Eastern Coalfields Limited": "SECL",
    "Central Coalfields Limited": "CCL",
    "Eastern Coalfields Limited": "ECL",
    "Mahanadi Coalfields Limited": "MCL",
    "Western Coalfields Limited": "WCL",
    "Bharat Coking Coal Limited": "BCCL",
    "Coal India Limited": "CIL",
    "Central Mine Planning and Design Institute Limited": "CMPDI",
    "Directorate Urban Administration and Development": "UADD",
    "Directorate of Urban Administration and Development": "UADD",
    "Directorate of Health Services": "DHS",
    "Directorate of Public Instruction": "DPI",
    "Public Works Department": "PWD",
    "PWD- Roads and Bridges": "PWD",
    "PWD-PIU": "PWD PIU",
    "Public Health Engineering- O/o Engineer In Chief": "PHED",
    "Public Health Engineering Department": "PHED",
    "Rural Engineering Service": "RES",
    "Madhya Pradesh Building Development Corporation Limited": "MPBDC",
    "Madhya Pradesh Building Development Corporation": "MPBDC",
    "Madhya Pradesh Power Generating Company Limited": "MPPGCL",
    "Madhya Pradesh Poorv Kshetra Vidyut Vitaran Company Limited": "MPPKVVCL",
    "Madhya Pradesh Madhya Kshetra Vidyut Vitaran Company Limited": "MPMKVVCL",
    "Madhya Pradesh Paschim Kshetra Vidyut Vitaran Company Limited": "MPPKVVCL",
}


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def norm(value):
    value = str(value or "").casefold()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def money_num(value):
    text = str(value or "")
    if not text or re.search(r"\b(?:nil|n/?a|not applicable)\b", text, re.I):
        return None
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def parse_dt(value):
    text = clean(value)
    if not text:
        return None
    formats = (
        "%d-%b-%Y %I:%M %p",
        "%d-%b-%Y %H:%M",
        "%d/%m/%Y %I:%M %p",
        "%d/%m/%Y %H:%M",
        "%d-%m-%Y %I:%M %p",
        "%d-%m-%Y %H:%M",
        "%Y-%m-%dT%H:%M:%S",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def iso_dt(value):
    parsed = parse_dt(value)
    return parsed.isoformat() if parsed else ""


def organisation_short(name):
    name = clean(name)
    if not name:
        return ""
    if name in ORG_SHORT_OVERRIDES:
        return ORG_SHORT_OVERRIDES[name]
    low = name.casefold()
    rules = [
        (("municipal corporation", "singrauli"), "MCS"),
        (("nagar parishad", "bargawan"), "NP Bargawan"),
        (("nagar parishad", "sarai"), "NP Sarai"),
        (("rural engineering service",), "RES"),
        (("public health engineering",), "PHED"),
        (("pwd", "piu"), "PWD PIU"),
        (("public works department",), "PWD"),
        (("urban administration",), "UADD"),
        (("building development corporation",), "MPBDC"),
    ]
    for needles, label in rules:
        if all(n in low for n in needles):
            return label
    if len(name) <= 30:
        return name
    return " ".join(name.split()[:4]) + ("..." if len(name.split()) > 4 else "")


def coal_project_label(organisation, project):
    project = clean(project)
    if not project:
        return ""
    project = re.sub(r"\s+Project\s*$", "", project, flags=re.I).strip()
    project = re.sub(r"^NCL[-\s]*", "", project, flags=re.I).strip()
    return clean(f"{organisation_short(organisation) or 'CIL'} {project}")


def district_from_hints(value):
    text = " " + clean(value) + " "
    aliases = {
        "Singrauli": ["singrauli", "waidhan", "baidhan", "singroli", "486886", "486889"],
        "Anuppur": ["anuppur", "chachai", "amarkantak"],
        "Betul": ["betul", "sarni", "stps"],
        "Shahdol": ["shahdol", "bansagar"],
        "Umaria": ["umaria", "birsinghpur", "sgtps"],
        "Rewa": ["rewa", "tons hydel"],
        "Shivpuri": ["shivpuri", "pohari"],
        "Ujjain": ["ujjain", "umc"],
        "Sidhi": ["sidhi"],
        "Satna": ["satna"],
        "Jabalpur": ["jabalpur"],
        "Bhopal": ["bhopal"],
        "Indore": ["indore"],
        "Gwalior": ["gwalior"],
        "Sagar": ["sagar"],
    }
    for district in sorted(MP_DISTRICTS, key=len, reverse=True):
        if re.search(r"\b" + re.escape(district) + r"\b", text, re.I):
            return district
    low = text.casefold()
    for district, terms in aliases.items():
        if any(term in low for term in terms):
            return district
    for place, district in mp_place_index().items():
        if re.search(r"(?<![a-z0-9])" + re.escape(place) + r"(?![a-z0-9])", low):
            return district
    return ""


def mp_place_index():
    global _MP_PLACE_INDEX
    if _MP_PLACE_INDEX is not None:
        return _MP_PLACE_INDEX
    claims = {}
    try:
        with open(MP_PLACES_PATH, encoding="utf-8") as handle:
            districts = json.load(handle).get("districts", {})
        for district, places in districts.items():
            for place in places:
                key = re.sub(r"[^a-z0-9]+", " ", str(place).casefold()).strip()
                if len(key) >= 4:
                    claims.setdefault(key, set()).add(district)
    except Exception:
        claims = {}
    _MP_PLACE_INDEX = {
        place: next(iter(districts))
        for place, districts in sorted(claims.items(), key=lambda item: len(item[0]), reverse=True)
        if len(districts) == 1
    }
    return _MP_PLACE_INDEX


def resolve_mp_district_city(row):
    district = district_from_hints(clean(row.get("district")))
    location = clean(row.get("location"))
    pincode = clean(row.get("pincode"))
    if not district:
        authority_text = " ".join(clean(row.get(k)) for k in ("location", "organisation", "org_unit", "listing_row_hint"))
        district = district_from_hints(authority_text)
    if not district and pincode.startswith(("48688", "48689")):
        district = "Singrauli"
    if not district:
        district = district_from_hints(" ".join(clean(row.get(k)) for k in ("nit_ref", "work_en")))
    if district == "Singrauli" and (not location or location.casefold() == "singrauli"):
        if pincode in {"486886", "486889"} or any(
            x in norm(" ".join([row.get("work_en", ""), row.get("nit_ref", ""), row.get("org_unit", "")]))
            for x in ("waidhan", "baidhan")
        ):
            location = "Waidhan"
    if district and location and norm(location) != norm(district) and len(location) <= 40:
        return f"{district} / {location}"
    return district or location


def status_for(row, now=None):
    now = now or datetime.now()
    bid_end = parse_dt(row.get("bid_end"))
    if not bid_end:
        return "Unknown"
    if bid_end < now:
        return "Closed"
    if (bid_end - now).total_seconds() <= 3 * 86400:
        return "Closing <=3 Days"
    return "Active"


def label_value_map(soup):
    values = {}
    for tr in soup.find_all("tr"):
        cells = [clean(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
        cells = [c for c in cells if c]
        for i in range(0, len(cells) - 1, 2):
            key, value = cells[i], cells[i + 1]
            if len(key) <= 90 and key not in values:
                values[key] = value
    return values


def exact_value(soup, *labels):
    wanted = {clean(x).rstrip(":").casefold() for x in labels}
    for cell in soup.find_all(["td", "th"]):
        key = clean(cell.get_text(" ", strip=True)).rstrip(":").casefold()
        if key not in wanted:
            continue
        sib = cell.find_next_sibling(["td", "th"])
        while sib is not None:
            value = clean(sib.get_text(" ", strip=True))
            if value and value not in (":", "-"):
                return value
            sib = sib.find_next_sibling(["td", "th"])
        nxt = cell.find_next(["td", "th"])
        while nxt is not None and nxt is not cell:
            value = clean(nxt.get_text(" ", strip=True))
            if value and value not in (":", "-"):
                return value
            nxt = nxt.find_next(["td", "th"])
    return ""


def all_portal_fields(soup):
    fields = {}
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["td", "th"], recursive=False) or tr.find_all(["td", "th"])
        for i, cell in enumerate(cells[:-1]):
            key = clean(cell.get_text(" ", strip=True)).rstrip(":")
            if not key or len(key) > 120 or re.fullmatch(r"[\d\s,./:\-APMapm]+", key):
                continue
            value = ""
            for sib in cells[i + 1 :]:
                value = clean(sib.get_text(" ", strip=True))
                if value and value not in (":", "-"):
                    break
            if not value or value == key:
                continue
            if key in fields and fields[key] != value:
                current = fields[key]
                fields[key] = current + [value] if isinstance(current, list) else [current, value]
            else:
                fields[key] = value
    return fields


def extract_corrigenda(soup, base_url):
    heading = soup.find(
        string=lambda s: isinstance(s, str)
        and re.search(r"^\s*Latest\s+Corrigendum\s+List\s*$", s, re.I)
    )
    tables = []
    if heading is not None:
        for table in heading.parent.find_all_next("table", limit=8):
            text = " ".join(table.stripped_strings)
            if re.search(r"Corrigendum\s+Title", text, re.I) and re.search(
                r"Corrigendum\s+Type", text, re.I
            ):
                tables.append(table)
                break
    if not tables:
        for table in soup.find_all("table"):
            text = " ".join(table.stripped_strings)
            if re.search(r"Corrigendum\s+Title", text, re.I) and re.search(
                r"Corrigendum\s+Type", text, re.I
            ):
                tables.append(table)
                break
    if not tables:
        return []
    corr = []
    for tr in tables[0].find_all("tr"):
        cells = tr.find_all("td")
        if not cells:
            continue
        vals = [clean(c.get_text(" ", strip=True)) for c in cells]
        joined = " ".join(vals)
        if not joined or "corrigendum title" in joined.casefold():
            continue
        title = vals[1] if len(vals) >= 3 else (vals[0] if vals else "")
        ctype = vals[2] if len(vals) >= 3 else (vals[1] if len(vals) >= 2 else "")
        if not title and not ctype:
            continue
        a = tr.find("a", href=True)
        link = urljoin(base_url, a["href"]) if a else ""
        link_text = (title + " " + link).casefold()
        if "tendernotice_1" in link_text or link.split("?", 1)[0].casefold().endswith(".zip"):
            link = ""
        corr.append({"title": title or "Corrigendum", "type": ctype, "url": link})
    return corr


class PortalClient:
    def __init__(self, source):
        self.source = source
        self.profile = PORTALS[source]
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"})

    def get(self, url, read_timeout=None):
        last = None
        read_timeout = read_timeout or READ_TIMEOUT
        for attempt in range(1, REQUEST_RETRIES + 1):
            try:
                res = self.session.get(
                    url,
                    timeout=(CONNECT_TIMEOUT, read_timeout),
                    allow_redirects=True,
                )
                res.raise_for_status()
                body = res.text.casefold()
                stale = (
                    "your session has timed out" in body
                    or "unauthorised access" in body
                    or "unauthorized access" in body
                    or "unauthorisedaccesspage" in res.url.casefold()
                )
                if stale:
                    try:
                        self.session.get(
                            self.profile["home"],
                            timeout=(CONNECT_TIMEOUT, min(30, read_timeout)),
                            allow_redirects=True,
                        )
                    except Exception:
                        pass
                    time.sleep(0.7)
                    res = self.session.get(
                        url,
                        timeout=(CONNECT_TIMEOUT, read_timeout),
                        allow_redirects=True,
                    )
                    res.raise_for_status()
                return res
            except Exception as exc:
                last = exc
                if attempt < REQUEST_RETRIES:
                    time.sleep((2, 5, 10, 15)[min(attempt - 1, 3)])
                    try:
                        self.session.get(
                            self.profile["home"],
                            timeout=(CONNECT_TIMEOUT, 30),
                            allow_redirects=True,
                        )
                    except Exception:
                        pass
        raise last or RuntimeError("Portal request failed")

    def soup(self, url, read_timeout=None):
        return BeautifulSoup(self.get(url, read_timeout=read_timeout).text, "html.parser")

    def organisation_links(self):
        soup = self.soup(self.profile["org"], read_timeout=ORG_INDEX_READ_TIMEOUT)
        out = []
        for tr in soup.find_all("tr"):
            a = tr.find("a", href=True)
            if not a:
                continue
            href = a["href"]
            if not any(
                token in href.casefold()
                for token in ("frontendtendersbyorganisation", "tendersbyorganisation", "service=direct")
            ):
                continue
            values = [clean(c.get_text(" ", strip=True)) for c in tr.find_all("td")]
            values = [v for v in values if v]
            org = values[1] if len(values) >= 2 else (values[0] if values else "")
            if org and not re.fullmatch(r"[\d\s./-]+", org):
                out.append((org, urljoin(self.profile["base"], href)))
        seen, uniq = set(), []
        for item in out:
            if item[1] not in seen:
                seen.add(item[1])
                uniq.append(item)
        uniq.sort(
            key=lambda x: (
                0
                if self.source == "COAL" and NCL_NAME.casefold() in x[0].casefold()
                else 0
                if self.source == "MP" and "singrauli" in x[0].casefold()
                else 1,
                x[0].casefold(),
            )
        )
        return uniq

    def tender_links_from_org(self, org, url):
        soup = self.soup(url)
        out = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if any(
                token in href.casefold()
                for token in ("frontendviewtender", "viewtender", "frontendtenderdetails")
            ):
                tr = a.find_parent("tr")
                hint = clean(tr.get_text(" ", strip=True) if tr else a.get_text(" ", strip=True))
                out.append((urljoin(self.profile["base"], href), hint))
        seen, uniq = set(), []
        for item in out:
            if item[0] not in seen:
                seen.add(item[0])
                uniq.append(item)
        return uniq

    def parse_detail(self, org, url, row_hint=""):
        soup = self.soup(url)
        flat = clean(" ".join(soup.stripped_strings))
        pairs = label_value_map(soup)

        def pv(*labels):
            for label in labels:
                for key, value in pairs.items():
                    if key.casefold() == label.casefold() or label.casefold() in key.casefold():
                        return clean(value)
            return ""

        tender_id = exact_value(soup, "Tender ID") or pv("Tender ID")
        if not tender_id:
            match = re.search(r"\b20\d{2}_[A-Za-z0-9]+_\d+_\d+\b", flat)
            tender_id = match.group(0) if match else ""
        if not tender_id:
            return None

        nit_ref = exact_value(soup, "Tender Reference Number", "Tender Ref. No.", "Tender Ref.No")
        if not nit_ref:
            nit_ref = pv("Tender Reference Number")
        if not nit_ref:
            match = re.search(
                r"Tender Reference Number\s*[:\-]?\s*(.*?)\s+Tender ID\s+20\d{2}_[A-Za-z0-9]+_\d+_\d+",
                flat,
                re.I,
            )
            nit_ref = match.group(1).strip(" :-") if match else ""

        work = (
            exact_value(soup, "Work Description", "Tender Title", "Title")
            or pv("Work Description", "Tender Title", "Title")
        )
        location = exact_value(soup, "Location") or pv("Location")
        pincode = exact_value(soup, "Pincode", "Pin Code") or pv("Pincode", "Pin Code")
        bid_place = exact_value(soup, "Bid Opening Place") or pv("Bid Opening Place")
        published = (
            exact_value(soup, "Published Date", "Publish Date", "Publication Date")
            or pv("Published Date", "Publish Date", "Publication Date")
        )
        bid_end = (
            exact_value(soup, "Bid Submission End Date", "Bid Submission Closing Date", "Submission End Date")
            or pv("Bid Submission End Date", "Bid Submission Closing Date", "Submission End Date")
        )
        if parse_dt(published) is None:
            published = ""
        if not bid_end:
            match = re.search(
                r"Bid Submission End Date\s+(\d{1,2}-[A-Za-z]{3}-\d{4}\s+\d{1,2}:\d{2}\s+[AP]M)",
                flat,
                re.I,
            )
            bid_end = match.group(1) if match else ""

        tender_fee = money_num(
            exact_value(soup, "Tender Fee in ₹", "Tender Fee", "Document Fee", "Tender Document Fee")
            or pv("Tender Fee in ₹", "Tender Fee", "Document Fee", "Tender Document Fee")
        )
        processing_fee = money_num(
            exact_value(soup, "Processing Fee in ₹", "Processing Fee", "Portal Fee")
            or pv("Processing Fee in ₹", "Processing Fee", "Portal Fee")
        )
        emd = money_num(
            exact_value(soup, "EMD Amount in ₹", "EMD Amount", "EMD Fee", "Earnest Money Deposit")
            or pv("EMD Amount in ₹", "EMD Amount", "EMD Fee", "Earnest Money Deposit")
        )
        pac = money_num(
            exact_value(
                soup,
                "Tender Value in ₹",
                "Tender Value",
                "Estimated Cost in ₹",
                "Estimated Cost",
                "Estimated Value in ₹",
                "Estimated Value",
                "Estimated Tender Value",
            )
            or pv(
                "Tender Value in ₹",
                "Tender Value",
                "Estimated Cost in ₹",
                "Estimated Cost",
                "Estimated Value in ₹",
                "Estimated Value",
                "Estimated Tender Value",
            )
        )
        if pac is None:
            match = re.search(
                r"(?:Tender Value|Estimated Cost|Estimated Value|Estimated Tender Value)"
                r"(?:\s+in\s*₹)?\s*[:\-]?\s*₹?\s*([\d,]+(?:\.\d+)?)",
                flat,
                re.I,
            )
            pac = money_num(match.group(1)) if match else None
        total_fee = None
        match = re.search(r"Total Fee in\s*₹?\s*\*?\s*-\s*([\d,]+(?:\.\d+)?)", flat, re.I)
        if match:
            total_fee = money_num(match.group(1))
        if total_fee is None and (tender_fee is not None or processing_fee is not None):
            total_fee = (tender_fee or 0) + (processing_fee or 0)

        org_chain = exact_value(soup, "Organisation Chain", "Organization Chain") or pv(
            "Organisation Chain", "Organization Chain"
        )
        chain = [x.strip() for x in (org_chain or org).split("||") if x.strip()]
        organisation = chain[0] if chain else org
        org_unit = " > ".join(chain[1:]) if len(chain) > 1 else ""
        if not org_unit:
            org_unit = pv("Organisation Unit", "Organization Unit", "Office Name", "Department Name", "Division")
        project = ""
        if self.source == "COAL":
            if len(chain) >= 2:
                project = chain[1]
            if not project:
                match = re.search(
                    r"Northern Coalfields Limited\s*\|\|\s*([^|]+?)(?:\|\||Tender Reference|Tender ID)",
                    flat,
                    re.I,
                )
                project = match.group(1).strip() if match else ""
            if not project and location:
                project = location

        district = ""
        if self.source == "MP":
            district = district_from_hints(
                " ".join([bid_place, location, org, org_unit, work, nit_ref, row_hint, flat[-2500:]])
            )
        corr = extract_corrigenda(soup, self.profile["base"])
        corr_latest = ""
        corr_url = ""
        if corr:
            corr_latest = corr[0]["title"] + (f" ({corr[0]['type']})" if corr[0].get("type") else "")
            corr_url = corr[0].get("url", "")

        row = {
            "tender_id": tender_id,
            "source": self.source,
            "source_name": self.profile["name"],
            "project": project,
            "district": district,
            "district_city": "",
            "location": location or bid_place,
            "pincode": pincode,
            "organisation": organisation,
            "organisation_short": organisation_short(organisation),
            "org_unit": org_unit,
            "work_en": work,
            "work_hi": "",
            "nit_ref": nit_ref,
            "pac": pac,
            "tender_fee": tender_fee,
            "processing_fee": processing_fee,
            "total_fee": total_fee,
            "emd": emd,
            "total_payable": (total_fee or 0) + (emd or 0) if total_fee is not None or emd is not None else None,
            "published_date": published,
            "published_iso": iso_dt(published),
            "bid_end": bid_end,
            "bid_end_iso": iso_dt(bid_end),
            "detail_url": url,
            "detail_fetched": datetime.now(timezone.utc).isoformat(),
            "corrigendum_count": len(corr),
            "corrigendum_latest": corr_latest,
            "corrigendum_url": corr_url,
            "corrigenda": corr,
            "portal_fields": all_portal_fields(soup),
            "listing_row_hint": row_hint,
        }
        row["district_city"] = (
            coal_project_label(row["organisation"], row["project"])
            if self.source == "COAL"
            else resolve_mp_district_city(row)
        )
        row["status"] = status_for(row)
        return row


def load_old():
    try:
        with open(DATA_PATH, encoding="utf-8") as handle:
            payload = json.load(handle)
        tenders = payload.get("tenders", [])
        if isinstance(tenders, list):
            return tenders
    except Exception:
        pass
    return []


def should_refresh(old_row):
    if not old_row:
        return True
    if not old_row.get("detail_fetched"):
        return True
    try:
        fetched = datetime.fromisoformat(str(old_row["detail_fetched"]).replace("Z", "+00:00"))
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
    except Exception:
        return True
    bid_end = parse_dt(old_row.get("bid_end"))
    urgent = bid_end and 0 < (bid_end - datetime.now()).total_seconds() <= 7 * 86400
    return urgent or (datetime.now(timezone.utc) - fetched).total_seconds() >= DETAIL_REFRESH_HOURS * 3600


def merge_keep_good(old_row, new_row):
    if not old_row:
        return new_row
    merged = dict(old_row)
    for key, value in new_row.items():
        if value in (None, "", []):
            continue
        merged[key] = value
    if new_row.get("work_en") and normalize_translation_key(new_row.get("work_en")) != normalize_translation_key(old_row.get("work_en")):
        merged["work_hi"] = ""
    if old_row.get("bid_end") and new_row.get("bid_end"):
        old_dt = parse_dt(old_row.get("bid_end"))
        new_dt = parse_dt(new_row.get("bid_end"))
        if old_dt and new_dt and new_dt < old_dt:
            merged["bid_end"] = old_row["bid_end"]
            merged["bid_end_iso"] = old_row.get("bid_end_iso", "")
            merged["pending_backward_bid_end"] = new_row.get("bid_end")
    merged["status"] = status_for(merged)
    merged["district_city"] = (
        coal_project_label(merged.get("organisation"), merged.get("project"))
        if merged.get("source") == "COAL"
        else resolve_mp_district_city(merged)
    )
    return merged


def normalize_translation_key(text):
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def valid_hindi_text(text, source=""):
    text = str(text or "").strip()
    if not text or not re.search(r"[\u0900-\u097f]", text):
        return ""
    if source and normalize_translation_key(text) == normalize_translation_key(source):
        return ""
    text = re.sub(r"(?:वैधान|वैधन|वाइधान|वायधान)", "वैढ़न", text)
    return re.sub(r"(?:बैधान|बैधन)", "बैढ़न", text)


def translate_hindi(text):
    for attempt in range(2):
        try:
            response = requests.get(
                "https://translate.googleapis.com/translate_a/single",
                params={"client": "gtx", "sl": "en", "tl": "hi", "dt": "t", "q": text},
                headers={"User-Agent": UA},
                timeout=(CONNECT_TIMEOUT, 12),
            )
            if response.status_code == 429:
                return "", "rate_limited"
            response.raise_for_status()
            data = response.json()
            translated = "".join(
                str(part[0]) for part in (data[0] if data and isinstance(data, list) else [])
                if isinstance(part, list) and part and part[0]
            ).strip()
            translated = valid_hindi_text(translated, text)
            if translated:
                return translated, ""
        except Exception as exc:
            if attempt:
                return "", str(exc)[:160]
            time.sleep(0.8)
    return "", "translation_failed"


def add_hindi_translations(rows, old_rows):
    cache = {}
    for row in [*old_rows, *rows]:
        key = normalize_translation_key(row.get("work_en"))
        hi = valid_hindi_text(row.get("work_hi"), row.get("work_en"))
        if key and hi:
            cache[key] = hi
    for row in rows:
        key = normalize_translation_key(row.get("work_en"))
        if key in cache:
            row["work_hi"] = cache[key]

    pending = {}
    for row in rows:
        text = str(row.get("work_en") or "").strip()
        if text and not valid_hindi_text(row.get("work_hi"), text):
            pending.setdefault(normalize_translation_key(text), {"text": text, "rows": []})["rows"].append(row)
    ordered = sorted(
        pending.values(),
        key=lambda item: min(priority(r.get("source", ""), r.get("organisation", ""), f"{r.get('district_city', '')} {r.get('location', '')}", r) for r in item["rows"]),
    )[:MAX_TRANSLATIONS_PER_RUN]
    started = time.monotonic()
    translated = 0
    attempted = 0
    rate_limited = False
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="RSPHindi") as pool:
        futures = {pool.submit(translate_hindi, item["text"]): item for item in ordered}
        for future in as_completed(futures):
            if time.monotonic() - started > TRANSLATION_BUDGET_SECONDS:
                break
            item = futures[future]
            attempted += 1
            hi, error = future.result()
            if error == "rate_limited":
                rate_limited = True
            if hi:
                for row in item["rows"]:
                    row["work_hi"] = hi
                translated += len(item["rows"])
        for future in futures:
            future.cancel()
    remaining = sum(1 for row in rows if row.get("work_en") and not valid_hindi_text(row.get("work_hi"), row.get("work_en")))
    return {"translated_this_run": translated, "attempted": attempted, "remaining": remaining, "rate_limited": rate_limited}


def priority(source, org, hint, old):
    text = f"{org} {hint}".casefold()
    if source == "COAL" and NCL_NAME.casefold() in text:
        return (0, 0)
    if source == "MP" and any(x in text for x in ("singrauli", "waidhan", "baidhan", "486886")):
        return (0, 1)
    if old:
        bid_end = parse_dt(old.get("bid_end"))
        if bid_end and bid_end > datetime.now():
            return (1, (bid_end - datetime.now()).total_seconds())
        return (2, 10**12)
    return (0, 2)


def scan_portal(source, old_by_id):
    client = PortalClient(source)
    start = time.monotonic()
    status = {
        "source": source,
        "source_name": PORTALS[source]["name"],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "complete": False,
        "organisations_total": 0,
        "organisations_scanned": 0,
        "links_found": 0,
        "details_fetched": 0,
        "kept_from_cache": 0,
        "errors": [],
    }
    found = {}
    try:
        orgs = client.organisation_links()
        status["organisations_total"] = len(orgs)
    except Exception as exc:
        status["errors"].append(f"organisation index failed: {exc}")
        return [], status

    links = []
    for org, org_url in orgs:
        if time.monotonic() - start > PORTAL_BUDGET_SECONDS:
            status["errors"].append("portal time budget reached while reading organisation list")
            break
        try:
            status["organisations_scanned"] += 1
            for detail_url, hint in client.tender_links_from_org(org, org_url):
                links.append((org, detail_url, hint))
        except Exception as exc:
            status["errors"].append(f"{org}: {exc}")

    seen = set()
    uniq = []
    for org, detail_url, hint in links:
        if detail_url not in seen:
            seen.add(detail_url)
            tid_match = re.search(r"\b20\d{2}_[A-Za-z0-9]+_\d+_\d+\b", hint)
            old = old_by_id.get(tid_match.group(0)) if tid_match else None
            uniq.append((org, detail_url, hint, old))
    status["links_found"] = len(uniq)
    uniq.sort(key=lambda item: priority(source, item[0], item[2], item[3]))

    for org, detail_url, hint, old in uniq:
        if time.monotonic() - start > PORTAL_BUDGET_SECONDS:
            status["errors"].append("portal time budget reached while reading tender details")
            break
        if status["details_fetched"] >= MAX_DETAIL_FETCH_PER_PORTAL:
            continue
        tid_hint_match = re.search(r"\b20\d{2}_[A-Za-z0-9]+_\d+_\d+\b", hint)
        tid_hint = tid_hint_match.group(0) if tid_hint_match else ""
        old = old_by_id.get(tid_hint) if tid_hint else old
        if old and not should_refresh(old):
            found[old["tender_id"]] = merge_keep_good(old, {})
            status["kept_from_cache"] += 1
            continue
        try:
            row = client.parse_detail(org, detail_url, hint)
            status["details_fetched"] += 1
            if row:
                found[row["tender_id"]] = merge_keep_good(old_by_id.get(row["tender_id"]), row)
        except Exception as exc:
            status["errors"].append(f"detail failed {detail_url}: {exc}")
            if old and old.get("tender_id"):
                found[old["tender_id"]] = merge_keep_good(old, {})

    for old in old_by_id.values():
        if old.get("source") == source and old.get("tender_id") not in found:
            if status["errors"] or status["details_fetched"] >= MAX_DETAIL_FETCH_PER_PORTAL:
                found[old["tender_id"]] = merge_keep_good(old, {})
                status["kept_from_cache"] += 1

    status["complete"] = (
        not status["errors"]
        and status["organisations_scanned"] == status["organisations_total"]
        and status["details_fetched"] < MAX_DETAIL_FETCH_PER_PORTAL
    )
    status["finished_at"] = datetime.now(timezone.utc).isoformat()
    return list(found.values()), status


def enrich_counts(rows, old_by_id):
    today = datetime.now().date()
    for row in rows:
        row["status"] = status_for(row)
        row["is_active"] = row["status"] != "Closed"
        pub_dt = parse_dt(row.get("published_date"))
        row["is_today"] = bool(pub_dt and pub_dt.date() == today)
        bid_end = parse_dt(row.get("bid_end"))
        row["is_closing_soon"] = bool(bid_end and bid_end > datetime.now() and (bid_end - datetime.now()).total_seconds() <= 3 * 86400)
        old = old_by_id.get(row.get("tender_id", ""))
        tracked = ("bid_end", "pac", "tender_fee", "processing_fee", "total_fee", "emd", "work_en", "location", "corrigendum_count")
        row["is_new"] = old is None
        row["is_changed"] = bool(old and any(str(old.get(k, "")) != str(row.get(k, "")) for k in tracked))
        row["district_city"] = (
            coal_project_label(row.get("organisation"), row.get("project"))
            if row.get("source") == "COAL"
            else resolve_mp_district_city(row)
        )
    counts = {}
    for source in PORTALS:
        subset = [r for r in rows if r.get("source") == source]
        counts[source] = {
            "total": len(subset),
            "active": sum(1 for r in subset if r.get("is_active")),
            "today": sum(1 for r in subset if r.get("is_today")),
            "closing_soon": sum(1 for r in subset if r.get("is_closing_soon")),
            "new_changed": sum(1 for r in subset if r.get("is_new") or r.get("is_changed")),
            "corrigendum": sum(1 for r in subset if int(r.get("corrigendum_count") or 0) > 0),
        }
    return counts


def main():
    os.makedirs(os.path.dirname(DATA_PATH) or ".", exist_ok=True)
    old_rows = load_old()
    old_by_id = {row.get("tender_id"): row for row in old_rows if row.get("tender_id")}
    rows = []
    scan_status = {}
    for source in ("MP", "COAL"):
        portal_rows, status = scan_portal(source, old_by_id)
        rows.extend(portal_rows)
        scan_status[source] = status

    deduped = {}
    for row in rows:
        if row.get("tender_id"):
            deduped[row["tender_id"]] = row
    rows = sorted(
        deduped.values(),
        key=lambda row: (
            row.get("source", ""),
            parse_dt(row.get("bid_end")) or datetime.max,
            row.get("organisation", ""),
        ),
    )
    translation_status = add_hindi_translations(rows, old_rows)
    counts = enrich_counts(rows, old_by_id)
    errors = [f"{src}: {err}" for src, st in scan_status.items() for err in st.get("errors", [])]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_version": VERSION,
        "count": len(rows),
        "counts": counts,
        "scan_status": scan_status,
        "translation_status": translation_status,
        "errors": errors,
        "portal_limits": {
            "max_detail_fetch_per_portal": MAX_DETAIL_FETCH_PER_PORTAL,
            "portal_budget_seconds": PORTAL_BUDGET_SECONDS,
            "detail_refresh_hours": DETAIL_REFRESH_HOURS,
            "note": "Organisation scan is full. Detail fetching is bounded for GitHub Actions; cache is preserved and scan_status reports partial runs.",
        },
        "tenders": rows,
    }
    tmp = DATA_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, DATA_PATH)
    print(f"saved {len(rows)} tenders")
    for source, status in scan_status.items():
        print(
            f"{source}: orgs {status['organisations_scanned']}/{status['organisations_total']}, "
            f"links {status['links_found']}, fetched {status['details_fetched']}, complete={status['complete']}"
        )
        for err in status.get("errors", [])[:8]:
            print(f"  warning: {err}")


if __name__ == "__main__":
    main()
