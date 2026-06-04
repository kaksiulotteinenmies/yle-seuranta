#!/usr/bin/env python3
"""
Yle-uutisseuranta — kaksivaiheinen analyysi
============================================
Vaihe 1: Otsikon perusteella nopea kategorisointi (kaikki uutiset)
Vaihe 2: Artikkelin luku (vain jos vaihe 1 tunnistaa vasemmistolle epäedullisen signaalin)

Välilehdet:
  Raakadata   — jokainen havainto omana rivinään
  Uutiskortti — yksi rivi per uutinen, päivittyy automaattisesti
"""

import os, json, time, requests, xml.etree.ElementTree as ET
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
import gspread
from google.oauth2.service_account import Credentials
import anthropic

# ── Asetukset ─────────────────────────────────────────────────────────────────

HELSINKI = ZoneInfo("Europe/Helsinki")

YLE_RSS_URLS = [
    "https://feeds.yle.fi/uutiset/v1/majorHeadlines/YLE_UUTISET.rss",
    "https://feeds.yle.fi/uutiset/v1/recent.rss?publisherIds=YLE_UUTISET",
    "https://feeds.yle.fi/uutiset/v1/recent.rss?publisherIds=YLE_URHEILU",
]
YLE_ETUSIVU_URL   = "https://yle.fi"
SHEETS_SHEET_ID   = os.environ["GOOGLE_SHEET_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
YLE_APP_ID        = os.environ["YLE_APP_ID"]
YLE_APP_KEY       = os.environ["YLE_APP_KEY"]

# ── Testirajoitus ────────────────────────────────────────────────────────────
# Aseta None kun järjestelmä toimii vakaasti ja haluat seurata kaikkia uutisia
MAX_UUTISET_PER_AJO = 10

AIKAIKKUNAT = {
    (6,9):"aamupiikki",(9,16):"tyopaiva",
    (16,18):"iltapaivapiikki",(18,22):"ilta",
    (22,24):"yo",(0,6):"yo",
}
VIIKONPAIVAT = ["maanantai","tiistai","keskiviikko","torstai","perjantai","lauantai","sunnuntai"]

# ── Tagit ─────────────────────────────────────────────────────────────────────

# VAIHE 1 — otsikosta pääteltävissä
TAGIT_V1 = {
    "aihe": [
        "maahanmuutto","rikos","vakivalta","seksuaalirikos","terrorismi",
        "politiikka-hallitus","politiikka-oppositio","talous","tyollisyys",
        "ilmastonmuutos","energia","terveys","koulutus","urheilu",
        "kulttuuri","ulkomaat","onnettomuus","oikeus",
    ],
    "kehystys": [
        "savy-positiivinen","savy-negatiivinen","savy-neutraali",
        "tekija-maahanmuuttaja","tekija-kantasuomalainen","tekija-tuntematon",
        "kohde-nainen","kohde-mies","kohde-lapsi","kohde-virkavalta",
        "poliitikko-vasemmisto","poliitikko-oikeisto","poliitikko-kepu",
        "poliitikko-aarioikeisto","poliitikko-äärivasemmisto",
        "tilasto","yksittaistapaus",
    ],
    "vasemmisto_epäedullinen": [
        "maahanmuuttaja-rikoksentekija","turvapaikanhakija-ongelmat",
        "ps-tai-oikeisto-onnistuu","vasemmisto-tai-vihrea-epaonnistuu",
        "ydinvoima-positiivinen","trans-kriittinen",
        "anti-nato","anti-eu","pro-israel","anti-palestiina",
    ],
}

# VAIHE 2 — artikkelista, vain jos vaihe 1 antoi vasemmisto_epäedullinen-tagin
TAGIT_V2 = {
    "kehystys_lisä": [
        "tausta-mainittu","tausta-ei-mainittu",
        "tekija-aarioikeisto","tekija-äärivasemmisto",
        "lahde-vain-viranomainen","lahde-monipuolinen",
        "painotus-oikeistokriittinen","painotus-vasemmistokriittinen",
    ],
    "vasemmisto_epäedullinen_lisä": [
        "integraatio-epaonnistuminen","islam-ongelmat-suomessa",
        "rinnakkaisyhteiskunta","maahanmuuton-kustannukset",
        "ilmastopolitiikka-kustannukset","vihrea-siirtyman-ongelmat",
        "anti-sateenkaari","anti-transideologia","anti-islam",
    ],
}

KAIKKI_V1 = [t for g in TAGIT_V1.values() for t in g]
KAIKKI_V2 = [t for g in TAGIT_V2.values() for t in g]

OHITA_URLIT = [
    "/uutiset/paikallisuutiset","/uutiset/yhteystiedot","yle.fi/t/",
    "areena.yle.fi","/rss","/opas","/lyhyet","/tuoreimmat","/selkouutiset",
    "sanapyramidi","futistietaja","saavutettavuus","asiakaspalvelu",
    "yhteystiedot","onelink.me","/abitreenit","/elavaarkisto","/oppiminen",
    "/uutiset/lyhyesti","74-20131998",
]
OHITA_OTSIKOT = [
    "lähetä uutiskuva","lähetä uutis","keskustele","sanapyramidi",
    "futistietäjä","saavutettavuus","asiakaspalvelu","yhteystiedot","uutiskirje",
]

# ── Sarakerakenne ─────────────────────────────────────────────────────────────

RAAKA_OTSIKOT = [
    "aikaleima","url","otsikko","osio","sijainti",
    "julkaisuaika","julkaisuikkuna","viikonpaiva","etusivulla",
    "tagit_aihe","tagit_kehystys","tagit_vasemmisto_epäedullinen",
    "vaihe2_tehty","vaihe2_lisatagit",
    "varmuus","tarkistamatta","viive_julkaisusta_min",
]

KORTTI_OTSIKOT = [
    "url","otsikko",
    "julkaisuaika","julkaisuikkuna","viikonpaiva",
    "ensimmainen_etusivu","viimeinen_etusivu","viive_julkaisusta_etusivulle_min",
    "nakyvyys_tunnit","havaintoja_yhteensa",
    "paras_sijainti","huonoin_sijainti","keskisijainti","osiot_joissa_nahty",
    "etusivulla_koskaan","julkaistu_ei_nostettu",
    "tagit_aihe","tagit_kehystys","tagit_vasemmisto_epäedullinen",
    "vaihe2_tehty","vaihe2_lisatagit",
    "varmuus","tarkistamatta",
    "otsikko_alkuperainen","otsikko_nykyinen","muokattu_kertaa",
    "viimeisin_muutos","muutoshistoria",
    "paivitetty",
]

# ── Apufunktiot ───────────────────────────────────────────────────────────────

def get_aikaikkuna(dt):
    h = dt.hour
    for (a,b),n in AIKAIKKUNAT.items():
        if a <= h < b: return n
    return "yo"

def get_viikonpaiva(dt):
    return VIIKONPAIVAT[dt.weekday()]

def parse_dt(s):
    try: return datetime.strptime(s,"%Y-%m-%d %H:%M").replace(tzinfo=HELSINKI)
    except: return None

def siivoa_url(url):
    if "?" in url: url = url.split("?")[0]
    if "#" in url: url = url.split("#")[0]
    return url

def tagit_str(lista): return ", ".join(lista) if lista else ""

# ── RSS ───────────────────────────────────────────────────────────────────────

def hae_rss_uutiset():
    from email.utils import parsedate_to_datetime
    uutiset = {}
    for rss_url in YLE_RSS_URLS:
        try:
            resp = requests.get(rss_url, timeout=15)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            for item in root.findall(".//item"):
                link = siivoa_url(item.findtext("link","").strip())
                title = item.findtext("title","").strip()
                pub = item.findtext("pubDate","")
                try: pub_dt = parsedate_to_datetime(pub).astimezone(HELSINKI)
                except: pub_dt = datetime.now(HELSINKI)
                if link and link not in uutiset:
                    uutiset[link] = {
                        "otsikko": title,
                        "julkaisuaika": pub_dt.strftime("%Y-%m-%d %H:%M"),
                        "julkaisuikkuna": get_aikaikkuna(pub_dt),
                        "viikonpaiva": get_viikonpaiva(pub_dt),
                    }
        except Exception as e:
            print(f"RSS-virhe ({rss_url}): {e}")
    print(f"RSS: {len(uutiset)} uutista")
    return uutiset

# ── Etusivu ───────────────────────────────────────────────────────────────────

def hae_etusivu_uutiset():
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fi-FI,fi;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Cache-Control": "max-age=0",
    }
    session = requests.Session()
    session.headers.update(headers)
    resp = session.get(YLE_ETUSIVU_URL, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    tulokset = []
    nahdyt = set()
    sijainti_per_osio = {}

    # 1. Lyhyesti
    for a in soup.find_all("a", href=True):
        href = siivoa_url(a.get("href",""))
        if not href.startswith("http"): href = "https://yle.fi" + href
        if "/uutiset/lyhyesti/" not in href: continue
        ots = a.get_text(strip=True)
        if not ots or len(ots) < 5 or href in nahdyt: continue
        if any(r in ots.lower() for r in OHITA_OTSIKOT): continue
        nahdyt.add(href)
        sijainti_per_osio["lyhyesti"] = sijainti_per_osio.get("lyhyesti",0)+1
        tulokset.append({"url":href,"otsikko":ots,"osio":"lyhyesti","sijainti":sijainti_per_osio["lyhyesti"]})

    # 2. Suosituimmat ja Tuoreimmat
    for h2 in soup.find_all("h2"):
        t = h2.get_text(strip=True).lower()
        if "suosituimm" in t or "tuoreim" in t:
            osio = "suosituimmat" if "suosituimm" in t else "tuoreimmat"
            for sib in h2.find_next_siblings():
                for a in sib.find_all("a", href=True):
                    href = siivoa_url(a.get("href",""))
                    if not href.startswith("http"): href = "https://yle.fi" + href
                    if any(o in href for o in OHITA_URLIT): continue
                    if "/a/74-" not in href and "/a/3-" not in href: continue
                    ots = a.get_text(strip=True)
                    if not ots or len(ots) < 5 or href in nahdyt: continue
                    if any(r in ots.lower() for r in OHITA_OTSIKOT): continue
                    nahdyt.add(href)
                    sijainti_per_osio[osio] = sijainti_per_osio.get(osio,0)+1
                    tulokset.append({"url":href,"otsikko":ots,"osio":osio,"sijainti":sijainti_per_osio[osio]})
                if sib.name == "h2": break

    # 3. Pääsivu
    for a in soup.find_all("a", href=True):
        href = siivoa_url(a.get("href",""))
        if not href.startswith("http"): href = "https://yle.fi" + href
        if href in nahdyt: continue
        if any(o in href for o in OHITA_URLIT): continue
        if "/a/74-" not in href and "/a/3-" not in href: continue
        ots = a.get_text(strip=True)
        if not ots or len(ots) < 10: continue
        if any(r in ots.lower() for r in OHITA_OTSIKOT): continue
        nahdyt.add(href)
        sijainti_per_osio["paasivu"] = sijainti_per_osio.get("paasivu",0)+1
        tulokset.append({"url":href,"otsikko":ots,"osio":"paasivu","sijainti":sijainti_per_osio["paasivu"]})

    print(f"Etusivu: {len(tulokset)} havaintoa {dict(sijainti_per_osio)}")
    return tulokset

# ── Kategorisointi — Vaihe 1 (otsikko, Batch API) ────────────────────────────

def tee_batch_kategoriointi_v1(otsikot_dict):
    """
    otsikot_dict = {url: otsikko}
    Palauttaa {url: {aihe:[], kehystys:[], vasemmisto_epäedullinen:[], varmuus:int}}
    """
    if not otsikot_dict:
        return {}

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    tagit_v1_str = json.dumps(TAGIT_V1, ensure_ascii=False)

    # Rakennetaan batch-pyynnöt
    requests_list = []
    url_jarjestys = list(otsikot_dict.keys())
    # Luo lyhyet tunnisteet URL:ien sijaan (max 64 merkkiä, vain a-z0-9_-)
    url_to_id = {url: f"uutinen_{i}" for i, url in enumerate(url_jarjestys)}
    id_to_url = {v: k for k, v in url_to_id.items()}

    for url in url_jarjestys:
        otsikko = otsikot_dict[url]
        prompt = f"""Kategorisoi uutisotsikko. Valitse tagit VAIN alla olevasta listasta. Vastaa AINOASTAAN JSON-muodossa.

Otsikko: "{otsikko}"

Tagit: {tagit_v1_str}

{{"aihe":[],"kehystys":[],"vasemmisto_epäedullinen":[],"varmuus":0}}"""

        requests_list.append({
            "custom_id": url_to_id[url],
            "params": {
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}]
            }
        })

    # Lähetä batch
    print(f"Lähetetään batch: {len(requests_list)} uutista...")
    batch = client.messages.batches.create(requests=requests_list)
    batch_id = batch.id
    print(f"Batch ID: {batch_id}")

    # Odota valmistumista (max 10 min)
    for _ in range(60):
        time.sleep(10)
        status = client.messages.batches.retrieve(batch_id)
        print(f"  Tila: {status.processing_status} ({status.request_counts.succeeded}/{len(requests_list)})")
        if status.processing_status == "ended":
            break

    # Hae tulokset
    tulokset = {}
    for result in client.messages.batches.results(batch_id):
        url = id_to_url.get(result.custom_id, result.custom_id)
        try:
            if result.result.type == "succeeded":
                teksti = result.result.message.content[0].text
                teksti = teksti.replace("```json","").replace("```","").strip()
                data = json.loads(teksti)
                # Validointi
                for ryhmä in ["aihe","kehystys","vasemmisto_epäedullinen"]:
                    data[ryhmä] = [t for t in data.get(ryhmä,[]) if t in KAIKKI_V1]
                tulokset[url] = data
            else:
                tulokset[url] = {"aihe":[],"kehystys":[],"vasemmisto_epäedullinen":[],"varmuus":0}
        except Exception as e:
            print(f"Parsintavirhe ({url}): {e}")
            tulokset[url] = {"aihe":[],"kehystys":[],"vasemmisto_epäedullinen":[],"varmuus":0}

    return tulokset

# ── Kategorisointi — Vaihe 2 (artikkeli, yksittäinen kutsu) ──────────────────

def hae_artikkeli_teksti(url):
    """Hakee artikkelin tekstisisällön."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept-Language": "fi-FI,fi;q=0.9",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        # Poista skriptit ja tyylit
        for tag in soup(["script","style","nav","footer","header"]):
            tag.decompose()
        # Hae artikkelin pääsisältö
        main = soup.find("main") or soup.find("article") or soup
        teksti = main.get_text(separator=" ", strip=True)
        # Rajoita 3000 merkkiin
        return teksti[:3000]
    except Exception as e:
        print(f"Artikkelinhakuvirhe ({url}): {e}")
        return ""

def kategorisoi_artikkeli_v2(url, otsikko, v1_data):
    """Vaihe 2: lukee artikkelin ja täydentää tageja."""
    teksti = hae_artikkeli_teksti(url)
    if not teksti:
        return {"kehystys_lisä":[],"vasemmisto_epäedullinen_lisä":[]}

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    tagit_v2_str = json.dumps(TAGIT_V2, ensure_ascii=False)
    v1_str = json.dumps(v1_data, ensure_ascii=False)

    prompt = f"""Analysoi tämä uutisartikkeli tarkemmin. Vaihe 1 antoi jo nämä tagit: {v1_str}

Täydennä nyt vaihe 2 tageilla artikkelin perusteella. Vastaa AINOASTAAN JSON-muodossa.

TÄRKEÄ OHJE: Käytä tageja `tausta-mainittu`, `tausta-ei-mainittu`, `tekija-aarioikeisto` ja `tekija-äärivasemmisto` AINOASTAAN jos artikkeli käsittelee rikosta, väkivaltaa, mielenosoitusta tai muuta konkreettista tapahtumaa jossa on selkeä tekijä. ÄLÄ käytä näitä tageja poliittisissa analyyseissä, kannatuskyselyissä tai puolueita koskevissa uutisissa.

Otsikko: "{otsikko}"
Artikkeli: "{teksti}"

Tagit: {tagit_v2_str}

{{"kehystys_lisä":[],"vasemmisto_epäedullinen_lisä":[]}}"""

    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role":"user","content":prompt}]
        )
        teksti_v = msg.content[0].text.replace("```json","").replace("```","").strip()
        data = json.loads(teksti_v)
        for ryhmä in ["kehystys_lisä","vasemmisto_epäedullinen_lisä"]:
            data[ryhmä] = [t for t in data.get(ryhmä,[]) if t in KAIKKI_V2]
        return data
    except Exception as e:
        print(f"V2-virhe ({url}): {e}")
        return {"kehystys_lisä":[],"vasemmisto_epäedullinen_lisä":[]}

# ── Google Sheets ─────────────────────────────────────────────────────────────

def yhdista():
    creds = Credentials.from_service_account_info(
        json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"]),
        scopes=["https://spreadsheets.google.com/feeds","https://www.googleapis.com/auth/drive"]
    )
    return gspread.authorize(creds).open_by_key(SHEETS_SHEET_ID)

def hae_tai_luo_ws(sheet, nimi, otsikot):
    try:
        ws = sheet.worksheet(nimi)
        arvot = ws.get_all_values()
        # Luo otsikot jos Sheet on tyhjä tai ensimmäinen rivi ei täsmää
        if not arvot or arvot[0] != otsikot:
            ws.clear()
            ws.append_row(otsikot)
    except gspread.WorksheetNotFound:
        ws = sheet.add_worksheet(title=nimi, rows=10000, cols=len(otsikot))
        ws.append_row(otsikot)
    return ws

def lue_kortit(ws):
    rivit = ws.get_all_records()
    kortit = {}
    for i, r in enumerate(rivit, 2):
        url = r.get("url","")
        if url: kortit[url] = {"_rivi":i, **r}
    return kortit

# ── Uutiskortti ───────────────────────────────────────────────────────────────

def laske_kortti(url, otsikko, rss, havainnot_nyt, nyt_str, kat, v2, vanha=None):
    nyt_dt = parse_dt(nyt_str)
    julkaisuaika   = rss.get("julkaisuaika","") if rss else ""
    julkaisuikkuna = rss.get("julkaisuikkuna","") if rss else ""
    viikonpaiva    = rss.get("viikonpaiva","") if rss else ""
    etusivulla_nyt = len(havainnot_nyt) > 0

    tagit_aihe = tagit_str(kat.get("aihe",[]))
    tagit_keh  = tagit_str(kat.get("kehystys",[]))
    tagit_vas  = tagit_str(kat.get("vasemmisto_epäedullinen",[]))
    varmuus    = kat.get("varmuus", 0)
    vaihe2_tehty   = "kyllä" if v2 else "ei"
    vaihe2_lisatagit = ""
    if v2:
        kaikki_v2 = v2.get("kehystys_lisä",[]) + v2.get("vasemmisto_epäedullinen_lisä",[])
        vaihe2_lisatagit = tagit_str(kaikki_v2)
    tarkistamatta = "kyllä" if (varmuus < 70 or kat.get("vasemmisto_epäedullinen")) else "ei"

    if vanha:
        ensimmainen = vanha.get("ensimmainen_etusivu","")
        viimeinen   = nyt_str if etusivulla_nyt else vanha.get("viimeinen_etusivu","")
        havaintoja  = int(vanha.get("havaintoja_yhteensa") or 0)
        if etusivulla_nyt: havaintoja += len(havainnot_nyt)
        paras   = int(vanha.get("paras_sijainti")) if vanha.get("paras_sijainti") else ""
        huonoin = int(vanha.get("huonoin_sijainti") or 0)
        keski_n = float(vanha.get("keskisijainti") or 0)
        if etusivulla_nyt:
            nyt_sij = [h["sijainti"] for h in havainnot_nyt if h.get("sijainti")]
            if nyt_sij:
                paras   = min(paras, min(nyt_sij)) if paras != "" else min(nyt_sij)
                huonoin = max(huonoin, max(nyt_sij))
                keski_n = round((keski_n + sum(nyt_sij)/len(nyt_sij))/2, 1)
        vanhat_osiot = set(vanha.get("osiot_joissa_nahty","").split(", ")) if vanha.get("osiot_joissa_nahty") else set()
        kaikki_osiot = ", ".join(sorted((vanhat_osiot | {h["osio"] for h in havainnot_nyt}) - {""}))
        etusivulla_koskaan    = "kyllä" if (vanha.get("etusivulla_koskaan")=="kyllä" or etusivulla_nyt) else "ei"
        julkaistu_ei_nostettu = "ei" if etusivulla_koskaan=="kyllä" else "kyllä"
        tarkistamatta = vanha.get("tarkistamatta","kyllä") if vanha.get("tarkistamatta")=="ei" else tarkistamatta
        # Vaihe 2 — säilytä aiempi tieto
        if vanha.get("vaihe2_tehty") == "kyllä":
            vaihe2_tehty = "kyllä"
            vaihe2_lisatagit = vanha.get("vaihe2_lisatagit","")
    else:
        ensimmainen = nyt_str if etusivulla_nyt else ""
        viimeinen   = nyt_str if etusivulla_nyt else ""
        havaintoja  = len(havainnot_nyt) if etusivulla_nyt else 0
        nyt_sij     = [h["sijainti"] for h in havainnot_nyt if h.get("sijainti")]
        paras   = min(nyt_sij) if nyt_sij else ""
        huonoin = max(nyt_sij) if nyt_sij else ""
        keski_n = round(sum(nyt_sij)/len(nyt_sij),1) if nyt_sij else ""
        kaikki_osiot          = ", ".join(sorted({h["osio"] for h in havainnot_nyt}))
        etusivulla_koskaan    = "kyllä" if etusivulla_nyt else "ei"
        julkaistu_ei_nostettu = "ei" if etusivulla_nyt else "kyllä"

    if ensimmainen and viimeinen:
        dt1, dt2 = parse_dt(ensimmainen), parse_dt(viimeinen)
        nakyvyys_h = round((dt2-dt1).total_seconds()/3600,1) if dt1 and dt2 else ""
    else:
        nakyvyys_h = ""

    viive = ""
    if julkaisuaika and ensimmainen:
        dt_pub, dt_etu = parse_dt(julkaisuaika), parse_dt(ensimmainen)
        if dt_pub and dt_etu:
            viive = int((dt_etu-dt_pub).total_seconds()/60)

    # Muutoshistoria
    if vanha:
        otsikko_alkuperainen = vanha.get("otsikko_alkuperainen") or vanha.get("otsikko","")
        otsikko_nykyinen     = otsikko
        muokattu_kertaa      = int(vanha.get("muokattu_kertaa") or 0)
        muutoshistoria       = vanha.get("muutoshistoria","")
        viimeisin_muutos     = vanha.get("viimeisin_muutos","")
        vanha_otsikko        = vanha.get("otsikko_nykyinen") or vanha.get("otsikko","")
        if vanha_otsikko and vanha_otsikko.strip() != otsikko.strip():
            muokattu_kertaa += 1
            viimeisin_muutos = nyt_str
            def ts(s): return set(t.strip() for t in s.split(",") if t.strip())
            vanhat_t = ts(vanha.get("tagit_aihe","")) | ts(vanha.get("tagit_kehystys","")) | ts(vanha.get("tagit_vasemmisto_epäedullinen",""))
            uudet_t  = ts(tagit_aihe) | ts(tagit_keh) | ts(tagit_vas)
            lisatyt   = ", ".join(sorted(uudet_t-vanhat_t))
            poistetut = ", ".join(sorted(vanhat_t-uudet_t))
            tagi_muutos = ""
            if lisatyt:   tagi_muutos += f" +[{lisatyt}]"
            if poistetut: tagi_muutos += f" -[{poistetut}]"
            if not tagi_muutos: tagi_muutos = " (tagit ennallaan)"
            merkinta = f"{nyt_str} | '{vanha_otsikko}' → '{otsikko}' | tagit:{tagi_muutos}"
            muutoshistoria = (muutoshistoria + " || " + merkinta) if muutoshistoria else merkinta
            tarkistamatta = "kyllä"
    else:
        otsikko_alkuperainen = otsikko
        otsikko_nykyinen     = otsikko
        muokattu_kertaa      = 0
        viimeisin_muutos     = ""
        muutoshistoria       = ""

    return [
        url, otsikko,
        julkaisuaika, julkaisuikkuna, viikonpaiva,
        ensimmainen, viimeinen, viive,
        nakyvyys_h, havaintoja,
        paras, huonoin, keski_n, kaikki_osiot,
        etusivulla_koskaan, julkaistu_ei_nostettu,
        tagit_aihe, tagit_keh, tagit_vas,
        vaihe2_tehty, vaihe2_lisatagit,
        varmuus, tarkistamatta,
        otsikko_alkuperainen, otsikko_nykyinen,
        muokattu_kertaa, viimeisin_muutos, muutoshistoria,
        nyt_str,
    ]

# ── Pääohjelma ────────────────────────────────────────────────────────────────

def main():
    nyt     = datetime.now(HELSINKI)
    nyt_str = nyt.strftime("%Y-%m-%d %H:%M")
    print(f"\n=== Yle-seuranta: {nyt_str} ===\n")

    rss_uutiset   = hae_rss_uutiset()
    etusivu_lista = hae_etusivu_uutiset()

    sheet     = yhdista()
    ws_raaka  = hae_tai_luo_ws(sheet, "Raakadata",   RAAKA_OTSIKOT)
    ws_kortti = hae_tai_luo_ws(sheet, "Uutiskortti", KORTTI_OTSIKOT)
    kortit    = lue_kortit(ws_kortti)

    # Aiemmin kategorisoidut
    kategorisoidut = {}
    for url, k in kortit.items():
        if k.get("tagit_aihe") or k.get("tagit_kehystys"):
            kategorisoidut[url] = {
                "aihe":      [t.strip() for t in k.get("tagit_aihe","").split(",") if t.strip()],
                "kehystys":  [t.strip() for t in k.get("tagit_kehystys","").split(",") if t.strip()],
                "vasemmisto_epäedullinen": [t.strip() for t in k.get("tagit_vasemmisto_epäedullinen","").split(",") if t.strip()],
                "varmuus":   k.get("varmuus",0),
            }

    etusivu_per_url = {}
    for h in etusivu_lista:
        etusivu_per_url.setdefault(h["url"],[]).append(h)

    kaikki_urlit = set(etusivu_per_url.keys()) | set(rss_uutiset.keys())

    # Selvitä mitkä tarvitsevat kategorisointia
    tarvitsee_v1 = {
        url: (etusivu_per_url.get(url,[{}])[0].get("otsikko") or rss_uutiset.get(url,{}).get("otsikko",""))
        for url in kaikki_urlit
        if url not in kategorisoidut
    }
    tarvitsee_v1 = {k:v for k,v in tarvitsee_v1.items() if v}

    # Rajoita uutismäärä testivaiheessa
    if MAX_UUTISET_PER_AJO and len(tarvitsee_v1) > MAX_UUTISET_PER_AJO:
        print(f"Rajoitetaan {len(tarvitsee_v1)} → {MAX_UUTISET_PER_AJO} uutiseen (testirajoitus)")
        tarvitsee_v1 = dict(list(tarvitsee_v1.items())[:MAX_UUTISET_PER_AJO])

    # Vaihe 1 — Batch API
    uudet_kategoriat = {}
    if tarvitsee_v1:
        print(f"\nVaihe 1: {len(tarvitsee_v1)} uutta uutista kategorisoidaan...")
        uudet_kategoriat = tee_batch_kategoriointi_v1(tarvitsee_v1)

    # Vaihe 2 — artikkelin luku herkille uutisille
    v2_tulokset = {}
    for url, kat in uudet_kategoriat.items():
        if kat.get("vasemmisto_epäedullinen"):
            print(f"Vaihe 2: {url[:60]}")
            v2_tulokset[url] = kategorisoi_artikkeli_v2(url, tarvitsee_v1[url], kat)
            time.sleep(0.5)

    # Rakenna rivit
    raaka_rivit     = []
    kortti_uudet    = []
    kortti_paivitys = []

    for url in kaikki_urlit:
        havainnot = etusivu_per_url.get(url, [])
        rss       = rss_uutiset.get(url, {})
        otsikko   = havainnot[0]["otsikko"] if havainnot else rss.get("otsikko","")
        if not otsikko: continue

        kat = kategorisoidut.get(url) or uudet_kategoriat.get(url) or \
              {"aihe":[],"kehystys":[],"vasemmisto_epäedullinen":[],"varmuus":0}
        v2  = v2_tulokset.get(url)

        tagit_aihe = tagit_str(kat.get("aihe",[]))
        tagit_keh  = tagit_str(kat.get("kehystys",[]))
        tagit_vas  = tagit_str(kat.get("vasemmisto_epäedullinen",[]))
        varmuus    = kat.get("varmuus",0)
        vaihe2_tehty = "kyllä" if v2 else "ei"
        vaihe2_lisatagit = ""
        if v2:
            kaikki_v2 = v2.get("kehystys_lisä",[]) + v2.get("vasemmisto_epäedullinen_lisä",[])
            vaihe2_lisatagit = tagit_str(kaikki_v2)
        tarkistamatta = "kyllä" if (varmuus < 70 or kat.get("vasemmisto_epäedullinen")) else "ei"

        if havainnot:
            for h in havainnot:
                viive_min = ""
                if rss.get("julkaisuaika"):
                    dt_pub = parse_dt(rss["julkaisuaika"])
                    if dt_pub: viive_min = int((nyt-dt_pub).total_seconds()/60)
                raaka_rivit.append([
                    nyt_str, url, otsikko, h["osio"], h["sijainti"],
                    rss.get("julkaisuaika",""), rss.get("julkaisuikkuna",""),
                    rss.get("viikonpaiva",""), "kyllä",
                    tagit_aihe, tagit_keh, tagit_vas,
                    vaihe2_tehty, vaihe2_lisatagit,
                    varmuus, tarkistamatta, viive_min,
                ])
        else:
            raaka_rivit.append([
                nyt_str, url, otsikko, "ei_etusivulla", "",
                rss.get("julkaisuaika",""), rss.get("julkaisuikkuna",""),
                rss.get("viikonpaiva",""), "ei",
                tagit_aihe, tagit_keh, tagit_vas,
                vaihe2_tehty, vaihe2_lisatagit,
                varmuus, tarkistamatta, "",
            ])

        korttiarvo = laske_kortti(url, otsikko, rss, havainnot, nyt_str, kat, v2, vanha=kortit.get(url))
        if url in kortit:
            kortti_paivitys.append((kortit[url]["_rivi"], korttiarvo))
        else:
            kortti_uudet.append(korttiarvo)

    # Tallenna
    if raaka_rivit:
        ws_raaka.append_rows(raaka_rivit, value_input_option="USER_ENTERED")
        print(f"Raakadata: +{len(raaka_rivit)} riviä")
        time.sleep(2)

    if kortti_uudet:
        ws_kortti.append_rows(kortti_uudet, value_input_option="USER_ENTERED")
        print(f"Uutiskortti: +{len(kortti_uudet)} uutta")
        time.sleep(2)

    if kortti_paivitys:
        def sarakekirjain(n):
            k=""
            while n:
                n,r=divmod(n-1,26)
                k=chr(65+r)+k
            return k
        col = sarakekirjain(len(KORTTI_OTSIKOT))
        for rivi_nro, arvot in kortti_paivitys:
            ws_kortti.update(
                values=[arvot],
                range_name=f"A{rivi_nro}:{col}{rivi_nro}",
                value_input_option="USER_ENTERED"
            )
            time.sleep(1.2)
        print(f"Uutiskortti: {len(kortti_paivitys)} päivitetty")

    print(f"\n✅ Valmis! {nyt_str}")

if __name__ == "__main__":
    main()
