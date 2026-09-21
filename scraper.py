import json,re,time,os
from datetime import datetime,timezone
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

PORTALS={
 'MP':('MP Tenders','https://mptenders.gov.in','https://mptenders.gov.in/nicgep/app?page=FrontEndTendersByOrganisation&service=page'),
 'COAL':('Coal India Tenders','https://coalindiatenders.nic.in','https://coalindiatenders.nic.in/nicgep/app?page=FrontEndTendersByOrganisation&service=page')
}
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36'
REQUEST_TIMEOUT=(8,18)
PORTAL_BUDGET=105
MAX_ORGS={'MP':14,'COAL':14}
MAX_DETAILS_PER_ORG=35

def clean(x): return re.sub(r'\s+',' ',str(x or '')).strip()
def money(x):
    m=re.search(r'[\d,]+(?:\.\d+)?',str(x or ''))
    return float(m.group(0).replace(',','')) if m else None

def exact(soup,*labels):
    wanted={clean(x).rstrip(':').lower() for x in labels}
    for cell in soup.find_all(['td','th']):
        if clean(cell.get_text(' ',strip=True)).rstrip(':').lower() not in wanted: continue
        sib=cell.find_next_sibling(['td','th'])
        while sib:
            v=clean(sib.get_text(' ',strip=True))
            if v and v not in (':','-'): return v
            sib=sib.find_next_sibling(['td','th'])
    return ''

def get(session,url,home):
    last=None
    for attempt in range(2):
        try:
            r=session.get(url,timeout=REQUEST_TIMEOUT,allow_redirects=True); r.raise_for_status()
            low=r.text.lower()
            if 'session has timed out' in low or 'unauthorised access' in low:
                session.get(home,timeout=REQUEST_TIMEOUT)
                r=session.get(url,timeout=REQUEST_TIMEOUT,allow_redirects=True); r.raise_for_status()
            return r
        except Exception as e:
            last=e
            if attempt==0: time.sleep(1)
    raise last

def org_links(session,base,url,home,source):
    soup=BeautifulSoup(get(session,url,home).text,'html.parser'); out=[]; seen=set()
    for tr in soup.find_all('tr'):
        a=tr.find('a',href=True)
        if not a: continue
        h=a['href']; hl=h.lower()
        if not any(t in hl for t in ('frontendtendersbyorganisation','tendersbyorganisation','service=direct')): continue
        vals=[clean(c.get_text(' ',strip=True)) for c in tr.find_all('td') if clean(c.get_text(' ',strip=True))]
        org=vals[1] if len(vals)>=2 else (vals[0] if vals else '')
        u=urljoin(base,h)
        if org and u not in seen and not re.fullmatch(r'[\d\s./-]+',org): seen.add(u); out.append((org,u))
    out.sort(key=lambda x:(0 if (source=='COAL' and 'northern coalfields limited' in x[0].lower()) or (source=='MP' and 'singrauli' in x[0].lower()) else 1,x[0].lower()))
    return out

def tender_links(session,base,url,home):
    soup=BeautifulSoup(get(session,url,home).text,'html.parser'); out=[]; seen=set()
    for a in soup.find_all('a',href=True):
        h=a['href']; hl=h.lower()
        if any(t in hl for t in ('frontendviewtender','viewtender','frontendtenderdetails')):
            u=urljoin(base,h)
            if u not in seen:
                seen.add(u); tr=a.find_parent('tr'); out.append((u,clean(tr.get_text(' ',strip=True) if tr else a.get_text(' ',strip=True))))
    return out

def parse_detail(session,source,org,url,home):
    soup=BeautifulSoup(get(session,url,home).text,'html.parser'); flat=clean(' '.join(soup.stripped_strings))
    tid=exact(soup,'Tender ID')
    if not tid:
        m=re.search(r'\b20\d{2}_[A-Za-z0-9]+_\d+_\d+\b',flat); tid=m.group(0) if m else ''
    if not tid:return None
    nit=exact(soup,'Tender Reference Number','Tender Ref. No.','Tender Ref.No'); work=exact(soup,'Work Description','Tender Title','Title')
    loc=exact(soup,'Location') or exact(soup,'Bid Opening Place'); pin=exact(soup,'Pincode','Pin Code')
    pub=exact(soup,'Published Date','Publish Date','Publication Date'); end=exact(soup,'Bid Submission End Date','Bid Submission Closing Date','Submission End Date')
    pac=money(exact(soup,'Tender Value in ₹','Tender Value','Estimated Cost in ₹','Estimated Cost','Estimated Value in ₹','Estimated Value','Estimated Tender Value'))
    tf=money(exact(soup,'Tender Fee in ₹','Tender Fee','Document Fee','Tender Document Fee')); pf=money(exact(soup,'Processing Fee in ₹','Processing Fee','Portal Fee')); emd=money(exact(soup,'EMD Amount in ₹','EMD Amount','EMD Fee','Earnest Money Deposit'))
    chain=exact(soup,'Organisation Chain','Organization Chain'); parts=[x.strip() for x in chain.split('||') if x.strip()]
    project=parts[1] if source=='COAL' and len(parts)>1 else ''; organisation=parts[0] if parts else org; district=''
    for d in ('Singrauli','Rewa','Satna','Sidhi','Shahdol','Anuppur','Umaria','Jabalpur','Bhopal','Indore','Gwalior','Sagar'):
        if re.search(r'\b'+re.escape(d)+r'\b',' '.join((work,loc,chain)),re.I):district=d;break
    corrs=[]
    for a in soup.find_all('a',href=True):
        txt=clean(a.get_text(' ',strip=True)); hl=a['href'].lower()
        if 'corrigendum' in txt.lower() or 'corrigendum' in hl: corrs.append({'title':txt or 'Corrigendum','url':urljoin(PORTALS[source][1],a['href'])})
    total_fee=(tf or 0)+(pf or 0) if tf is not None or pf is not None else None
    return {'tender_id':tid,'source':source,'project':project,'district':district,'location':loc,'pincode':pin,'organisation':organisation,'org_unit':' > '.join(parts[1:]),'work_en':work,'nit_ref':nit,'pac':pac,'tender_fee':tf,'processing_fee':pf,'total_fee':total_fee,'emd':emd,'total_payable':(total_fee or 0)+(emd or 0) if total_fee is not None or emd is not None else None,'published_date':pub,'bid_end':end,'detail_url':url,'corrigendum_count':len(corrs),'corrigendum_latest':corrs[0]['title'] if corrs else '','corrigendum_url':corrs[0]['url'] if corrs else ''}

def scan(source,old):
    started=time.monotonic(); name,base,orgurl=PORTALS[source]; home=base+'/nicgep/app'; s=requests.Session(); s.headers.update({'User-Agent':UA,'Accept-Language':'en-IN,en;q=0.9'})
    found={}; orgs=org_links(s,base,orgurl,home,source)
    if source=='COAL':
        priority=[x for x in orgs if 'northern coalfields limited' in x[0].lower()]; rest=[x for x in orgs if x not in priority]; orgs=priority+rest
    orgs=orgs[:MAX_ORGS[source]]
    for org,u in orgs:
        if time.monotonic()-started>PORTAL_BUDGET: print(source,'budget reached'); break
        try: links=tender_links(s,base,u,home)[:MAX_DETAILS_PER_ORG]
        except Exception as e: print(source,'org failed',org,e); continue
        for du,hint in links:
            if time.monotonic()-started>PORTAL_BUDGET: break
            try:
                t=parse_detail(s,source,org,du,home)
                if t: found[t['tender_id']]=t
            except Exception as e: print(source,'detail failed',du,e)
    for t in old:
        if t.get('source')==source and t.get('tender_id') not in found: found[t['tender_id']]=t
    return list(found.values())

def main():
    os.makedirs('data',exist_ok=True); path='data/tenders.json'
    try: old=json.load(open(path,encoding='utf-8')).get('tenders',[])
    except: old=[]
    rows=[]; errors=[]
    for src in ('MP','COAL'):
        try: rows.extend(scan(src,old))
        except Exception as e: errors.append(f'{src}: {e}'); rows.extend([x for x in old if x.get('source')==src])
    payload={'generated_at':datetime.now(timezone.utc).isoformat(),'base_version':'2.6.1 AREA_PARSER_FIX / web-fast','count':len(rows),'errors':errors,'tenders':rows}
    with open(path,'w',encoding='utf-8') as f: json.dump(payload,f,ensure_ascii=False,separators=(',',':'))
    print('saved',len(rows),'errors',errors)
if __name__=='__main__':main()
