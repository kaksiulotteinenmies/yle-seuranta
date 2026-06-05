#!/usr/bin/env python3
"""
Yle-uutisseuranta — kaksivaiheinen analyysi
============================================
Vaihe 1: Otsikon perusteella nopea kategorisointi (kaikki uutiset, Batch API)
Vaihe 2: Artikkelin luku (vain jos vaihe 1 tunnistaa vasemmistolle epäedullisen signaalin)

Välilehdet:
  Raakadata    — jokainen havainto omana rivinään
  Uutiskortti  — yksi rivi per uutinen, päivittyy automaattisesti
  Tilastot     — automaattiset yhteenvetotilastot
"""

import os, json, time, requests, xml.etree.ElementTree as ET, re
from datetime import datetime, timezone, timedelta
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

# Testirajoitus — vaihda None:ksi kun järjestelmä toimii vakaasti
MAX_UUTISET_PER_AJO = 10

# Live-uutinen: jos julkaistu yli näin monta päivää sitten
LIVEUUTINEN_RAJA_PV = 7

AIKAIKKUNAT = {
    (6,9):"aamupiikki",(9,16):"tyopaiva",
    (16,18):"iltapaivapiikki",(18,22):"ilta",
    (22,24):"yo",(0,6):"yo",
}
VIIKONPAIVAT = ["maanantai","tiistai","keskiviikko","torstai","perjantai","lauantai","sunnuntai"]

# ── Tagit ─────────────────────────────────────────────────────────────────────

TAGIT_V1 = {
    "aihe": [
        "maahanmuutto","rikos","vakivalta","seksuaalirikos","terrorismi",
        "politiikka-hallitus","politiikka-oppositio","talous","tyollisyys",
        "ilmastonmuutos","energia","terveys","koulutus","urheilu",
        "kulttuuri","ulkomaat","onnettomuus","oikeus",
        "aarioikeisto-liike","äärivasemmisto-liike",
    ],
    "kehystys": [
        "savy-positiivinen","savy-negatiivinen","savy-neutraali",
        "tekija-maahanmuuttaja","tekija-kantasuomalainen","tekija-tuntematon","tekija-alaikainen",
        "kohde-nainen","kohde-mies","kohde-lapsi","kohde-virkavalta",
        "poliitikko-vasemmisto","poliitikko-oikeisto","poliitikko-kepu",
        "aarioikeisto","äärivasemmisto",
        "tilasto","yksittaistapaus",
    ],
    "poliittinen_signaali": [
        # Vasemmistolle epäedulliset
        "maahanmuuttaja-rikoksentekija","turvapaikanhakija-ongelmat",
        "ps-tai-oikeisto-onnistuu","vasemmisto-tai-vihrea-epaonnistuu",
        "ydinvoima-positiivinen","trans-kriittinen",
        "anti-nato","anti-eu","pro-israel","anti-palestiina",
        # Oikeistolle epäedulliset
        "maahanmuutto-positiivinen","oikeisto-tai-ps-epaonnistuu",
        "vasemmisto-tai-vihrea-onnistuu","ydinvoima-kriittinen",
        "ilmastotoimet-positiivinen","trans-myonteinen",
        "pro-nato","pro-eu","anti-israel","pro-palestiina",
    ],
}

TAGIT_V2 = {
    "kehystys_lisä": [
        "tausta-mainittu","tausta-ei-mainittu",
        "tekija-aarioikeisto","tekija-äärivasemmisto",
        "lahde-vain-viranomainen","lahde-monipuolinen",
        "painotus-oikeistokriittinen","painotus-vasemmistokriittinen",
    ],
    "poliittinen_signaali_lisä": [
        "integraatio-epaonnistuminen","islam-ongelmat-suomessa",
        "rinnakkaisyhteiskunta","maahanmuuton-kustannukset",
        "ilmastopolitiikka-kustannukset","vihrea-siirtyman-ongelmat",
        "anti-sateenkaari","anti-transideologia","anti-islam",
    ],
}

KAIKKI_V1 = [t for g in TAGIT_V1.values() for t in g]
KAIKKI_V2 = [t for g in TAGIT_V2.values() for t in g]

# ── Aihehenkilöt — tekstihaku otsikosta ──────────────────────────────────────

TEEMAT = {
    "geopolitiikka": [
        "nato","israel","palestiina","gaza","trump","biden",
        "demokratit","republikaanit","ukraina","venäjä","putin","kiina","eu","yhdysvallat",
    ],
    "maat": [
        "suomi","ruotsi","norja","tanska","saksa","ranska","britannia",
        "viro","latvia","liettua","puola","unkari","italia","espanja",
        "turkki","iran","irak","syyria","afganistan","somalia",
        "intia","japani","etelä-korea","australia","kanada","brasilia",
    ],
    "maahanmuutto": [
        "turvapaikanhakija","pakolainen","maahanmuuttaja","käännytys","oleskelulupa",
    ],
    "puolueet_poliitikot": [
        "kokoomus","sdp","perussuomalaiset","keskusta","vihreät",
        "vasemmistoliitto","rkp","kd","kristillisdemokraatit",
        "demarit","demari","sosialidemokraatit",
        "persut","kokkarit","kepulaiset","vasemmisto",
    ],
    "identiteetti": [
        "seta","trans","transsukupuoli","hlbtq","pride",
    ],
}

def etsi_teemat(otsikko):
    """Etsii otsikosta teemat tekstihaulla."""
    otsikko_lower = otsikko.lower()
    loydetyt = []
    for _, sanat in TEEMAT.items():
        for sana in sanat:
            if sana in otsikko_lower and sana not in loydetyt:
                loydetyt.append(sana)
    return ", ".join(loydetyt)

# ── Muut vakiot ───────────────────────────────────────────────────────────────

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
    "tagit_aihe","tagit_kehystys","tagit_poliittinen_signaali",
    "tagit_teemat","tagit_henkilot",
    "vaihe2_tehty","vaihe2_lisatagit",
    "mahdollinen_liveuutinen","viimeisin_paivitys","paivitysviive_pv",
    "varmuus","tarkistamatta","etusivu_haettu","viive_julkaisusta_min",
]

KORTTI_OTSIKOT = [
    "url","otsikko",
    "julkaisuaika","julkaisuikkuna","viikonpaiva",
    "ensimmainen_etusivu","viimeinen_etusivu","viive_julkaisusta_etusivulle_min",
    "nakyvyys_tunnit","havaintoja_yhteensa",
    "paras_sijainti","huonoin_sijainti","keskisijainti","osiot_joissa_nahty",
    "etusivulla_koskaan","julkaistu_ei_nostettu",
    "tagit_aihe","tagit_kehystys","tagit_poliittinen_signaali",
    "tagit_teemat","tagit_henkilot",
    "vaihe2_tehty","vaihe2_lisatagit",
    "mahdollinen_liveuutinen","viimeisin_paivitys","paivitysviive_pv",
    "varmuus","tarkistamatta","etusivu_haettu",
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

# ── Live-uutinen tunnistus ────────────────────────────────────────────────────

def tarkista_liveuutinen(url, julkaisuaika_str, nyt):
    """
    Tarkistaa onko uutinen mahdollinen live-uutinen.
    Hakee modified_time meta-tagin jos julkaistu yli LIVEUUTINEN_RAJA_PV päivää sitten.
    Palauttaa (mahdollinen_liveuutinen, viimeisin_paivitys, paivitysviive_pv)
    """
    if not julkaisuaika_str:
        return "ei", "", ""

    pub_dt = parse_dt(julkaisuaika_str)
    if not pub_dt:
        return "ei", "", ""

    viive_pv = (nyt - pub_dt).days
    if viive_pv < LIVEUUTINEN_RAJA_PV:
        return "ei", "", ""

    # Haetaan modified_time artikkelin sivulta
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept-Language": "fi-FI,fi;q=0.9",
        }
        resp = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        meta = soup.find("meta", {"property": "article:modified_time"})
        if meta and meta.get("content"):
            mod_str = meta["content"]
            # Muunna ISO-formaatista
            mod_dt = datetime.fromisoformat(mod_str).astimezone(HELSINKI)
            mod_str_clean = mod_dt.strftime("%Y-%m-%d %H:%M")
            return "kyllä", mod_str_clean, viive_pv
    except Exception as e:
        print(f"Live-tarkistusvirhe ({url}): {e}")

    return "ei", "", ""

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
    # Kokeillaan eri User-Agentteja jos yksi blokataan
    user_agents = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    ]
    resp = None
    for ua in user_agents:
        try:
            headers = {
                "User-Agent": ua,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "fi-FI,fi;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Cache-Control": "max-age=0",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
            }
            session = requests.Session()
            session.headers.update(headers)
            resp = session.get(YLE_ETUSIVU_URL, timeout=15)
            if resp.status_code == 200:
                print(f"Etusivu OK (UA: {ua[:40]}...)")
                break
            print(f"Status {resp.status_code} UA:lla {ua[:40]}...")
        except Exception as e:
            print(f"Virhe UA:lla {ua[:40]}: {e}")
    if not resp or resp.status_code != 200:
        print("VAROITUS: Etusivu blokattu — jatketaan pelkällä RSS-datalla")
        return [], False  # tyhjä lista + etusivu_haettu=False
    soup = BeautifulSoup(resp.text, "html.parser")

    tulokset = []
    nahdyt = set()
    sijainti_per_osio = {}

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
    return tulokset, True  # lista + etusivu_haettu=True

# ── Kategorisointi Vaihe 1 (Batch API) ───────────────────────────────────────

def tee_batch_kategoriointi_v1(otsikot_dict):
    if not otsikot_dict:
        return {}

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    tagit_v1_str = json.dumps(TAGIT_V1, ensure_ascii=False)
    requests_list = []
    url_jarjestys = list(otsikot_dict.keys())
    url_to_id = {url: f"uutinen_{i}" for i, url in enumerate(url_jarjestys)}
    id_to_url = {v: k for k, v in url_to_id.items()}

    for url in url_jarjestys:
        otsikko = otsikot_dict[url]
        prompt = f"""Kategorisoi uutisotsikko. Valitse tagit VAIN alla olevasta listasta. Vastaa AINOASTAAN JSON-muodossa.

TÄRKEÄT OHJEET:
- Käytä `tekija-*` ja `kohde-*` tageja AINOASTAAN rikos-, väkivalta- tai onnettomuusuutisissa joissa on selkeä tekijä tai uhri. ÄLÄ käytä näitä urheilussa, politiikassa tai muissa neutraaleissa uutisissa.
- Käytä `aarioikeisto-liike` tai `äärivasemmisto-liike` tageja kun uutinen käsittelee äärioikeistolaista tai äärivasemmistolaista liikettä tai järjestöä (esim. Sinimusta liike, uusnatsit, anarkistit). Nämä ovat aihetageja, eivät tekijätageja.
- Käytä `politiikka-hallitus` VAIN suomalaisesta hallituksesta, eduskunnasta tai suomalaisista puolueista kertovissa uutisissa. Ulkomaiden hallitukset, valtiot tai poliitikot menevät `ulkomaat`-kategoriaan.
- Käytä `politiikka-oppositio` VAIN suomalaisesta oppositiosta kertovissa uutisissa.
- Käytä `kohde-lapsi` VAIN jos lapsi on rikoksen tai väkivallan uhri. ÄLÄ käytä jos lapsi tai alaikäinen on epäilty tai tekijä — silloin käytä `tekija-alaikainen`.
- Käytä `savy-positiivinen` VAIN jos uutinen kertoo selkeästi hyvistä uutisista, voitoista tai myönteisistä tapahtumista. Mielenterveys-, sairaus-, kuolema- tai menetysuutiset ovat `savy-negatiivinen` tai `savy-neutraali` vaikka henkilö suhtautuisi asiaan rohkeasti.
- Käytä `tekija-tuntematon` VAIN jos tekijää ei ole mainittu eikä vihjattu. Jos otsikossa sanotaan esim. "poliisi epäilee alaikäistä", käytä `tekija-alaikainen` eikä `tekija-tuntematon`.
- Käytä `vasemmisto-tai-vihrea-epaonnistuu`, `ps-tai-oikeisto-onnistuu`, `vasemmisto-tai-vihrea-onnistuu` ja `oikeisto-tai-ps-epaonnistuu` tageja VAIN selkeissä poliittisissa skandaaleissa tai epäonnistumisissa — EI tavallisessa kriittisessä journalismissa tai puolueita analysoivissa uutisissa.
- Tunnista `henkilot`-kenttään kaikki otsikossa mainitut henkilönnimet. Normalisoi nimet perusmuotoon (nominatiivi) — esim. "Häkkisen" → "Häkkinen", "Orpolle" → "Orpo". Lista voi olla tyhjä.

Otsikko: "{otsikko}"

Tagit: {tagit_v1_str}

{{"aihe":[],"kehystys":[],"poliittinen_signaali":[],"henkilot":[],"varmuus":0}}"""

        requests_list.append({
            "custom_id": url_to_id[url],
            "params": {
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}]
            }
        })

    print(f"Lähetetään batch: {len(requests_list)} uutista...")
    batch = client.messages.batches.create(requests=requests_list)
    batch_id = batch.id
    print(f"Batch ID: {batch_id}")

    for _ in range(60):
        time.sleep(10)
        status = client.messages.batches.retrieve(batch_id)
        print(f"  Tila: {status.processing_status} ({status.request_counts.succeeded}/{len(requests_list)})")
        if status.processing_status == "ended":
            break

    tulokset = {}
    for result in client.messages.batches.results(batch_id):
        url = id_to_url.get(result.custom_id, result.custom_id)
        try:
            if result.result.type == "succeeded":
                teksti = result.result.message.content[0].text
                teksti = teksti.replace("```json","").replace("```","").strip()
                data = json.loads(teksti)
                for ryhmä in ["aihe","kehystys","poliittinen_signaali"]:
                    data[ryhmä] = [t for t in data.get(ryhmä,[]) if t in KAIKKI_V1]
                data["henkilot"] = [h.strip() for h in data.get("henkilot",[]) if h.strip()]
                # Normalisoi varmuus: jos välillä 0-1, muunna 0-100
                varmuus_raw = data.get("varmuus", 0)
                if isinstance(varmuus_raw, (int, float)) and varmuus_raw <= 1.0:
                    data["varmuus"] = int(varmuus_raw * 100)
                elif not isinstance(varmuus_raw, (int, float)):
                    data["varmuus"] = 0
                tulokset[url] = data
            else:
                tulokset[url] = {"aihe":[],"kehystys":[],"poliittinen_signaali":[],"varmuus":0}
        except Exception as e:
            print(f"Parsintavirhe ({url}): {e}")
            tulokset[url] = {"aihe":[],"kehystys":[],"poliittinen_signaali":[],"varmuus":0}

    return tulokset

# ── Kategorisointi Vaihe 2 (artikkeli) ───────────────────────────────────────

def hae_artikkeli_teksti(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept-Language": "fi-FI,fi;q=0.9",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script","style","nav","footer","header"]):
            tag.decompose()
        main = soup.find("main") or soup.find("article") or soup
        teksti = main.get_text(separator=" ", strip=True)
        return teksti[:3000]
    except Exception as e:
        print(f"Artikkelinhakuvirhe ({url}): {e}")
        return ""

def kategorisoi_artikkeli_v2(url, otsikko, v1_data):
    teksti = hae_artikkeli_teksti(url)
    if not teksti:
        return {"kehystys_lisä":[],"poliittinen_signaali_lisä":[]}

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    tagit_v2_str = json.dumps(TAGIT_V2, ensure_ascii=False)
    v1_str = json.dumps(v1_data, ensure_ascii=False)

    prompt = f"""Analysoi tämä uutisartikkeli tarkemmin. Vaihe 1 antoi jo nämä tagit: {v1_str}

Täydennä nyt vaihe 2 tageilla artikkelin perusteella. Vastaa AINOASTAAN JSON-muodossa.

TÄRKEÄ OHJE: Käytä tageja `tausta-mainittu`, `tausta-ei-mainittu`, `tekija-aarioikeisto` ja `tekija-äärivasemmisto` AINOASTAAN jos artikkeli käsittelee rikosta, väkivaltaa, mielenosoitusta tai muuta konkreettista tapahtumaa jossa on selkeä tekijä. ÄLÄ käytä näitä tageja poliittisissa analyyseissä, kannatuskyselyissä tai puolueita koskevissa uutisissa.

Otsikko: "{otsikko}"
Artikkeli: "{teksti}"

Tagit: {tagit_v2_str}

{{"kehystys_lisä":[],"poliittinen_signaali_lisä":[]}}"""

    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role":"user","content":prompt}]
        )
        teksti_v = msg.content[0].text.replace("```json","").replace("```","").strip()
        # Ota vain ensimmäinen JSON-objekti jos on ylimääräistä dataa
        if "{" in teksti_v:
            teksti_v = teksti_v[teksti_v.index("{"):teksti_v.rindex("}")+1]
        data = json.loads(teksti_v)
        for ryhmä in ["kehystys_lisä","poliittinen_signaali_lisä"]:
            data[ryhmä] = [t for t in data.get(ryhmä,[]) if t in KAIKKI_V2]
        return data
    except Exception as e:
        print(f"V2-virhe ({url}): {e}")
        return {"kehystys_lisä":[],"poliittinen_signaali_lisä":[]}

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
        # Tarkista onko otsikkorivi oikein
        arvot = ws.get_all_values()
        if not arvot or arvot[0] != otsikot:
            ws.clear()
            ws.append_row(otsikot)
    except gspread.WorksheetNotFound:
        ws = sheet.add_worksheet(title=nimi, rows=10000, cols=max(len(otsikot), 30))
        ws.append_row(otsikot)
    return ws

def lue_kortit(ws):
    rivit = ws.get_all_records()
    kortit = {}
    for i, r in enumerate(rivit, 2):
        url = r.get("url","")
        if url: kortit[url] = {"_rivi":i, **r}
    return kortit

# ── Tilastovälilehti ──────────────────────────────────────────────────────────

def paivita_tilastot(sheet, ws_kortti):
    """Luo tai päivittää Tilastot-välilehden."""
    try:
        ws_tilastot = sheet.worksheet("Tilastot")
        ws_tilastot.clear()
    except gspread.WorksheetNotFound:
        ws_tilastot = sheet.add_worksheet(title="Tilastot", rows=200, cols=10)

    kortit = ws_kortti.get_all_records()
    if not kortit:
        return

    vas_tagit = {"maahanmuuttaja-rikoksentekija","turvapaikanhakija-ongelmat",
                 "ps-tai-oikeisto-onnistuu","vasemmisto-tai-vihrea-epaonnistuu",
                 "ydinvoima-positiivinen","trans-kriittinen",
                 "anti-nato","anti-eu","pro-israel","anti-palestiina"}
    oik_tagit = {"maahanmuutto-positiivinen","oikeisto-tai-ps-epaonnistuu",
                 "vasemmisto-tai-vihrea-onnistuu","ydinvoima-kriittinen",
                 "ilmastotoimet-positiivinen","trans-myonteinen",
                 "pro-nato","pro-eu","anti-israel","pro-palestiina"}

    nyt = datetime.now(HELSINKI)
    tanaan = nyt.strftime("%Y-%m-%d")
    viikko_sitten = (nyt - timedelta(days=7)).strftime("%Y-%m-%d %H:%M")

    # ── Laskennat ──
    yhteensa = len(kortit)
    tanaan_n = sum(1 for k in kortit if k.get("paivitetty","")[:10] == tanaan)
    viikolla_n = sum(1 for k in kortit if k.get("paivitetty","") >= viikko_sitten)
    ei_etusivulle = sum(1 for k in kortit if k.get("julkaistu_ei_nostettu") == "kyllä")
    otsikko_muutettu = sum(1 for k in kortit if int(k.get("muokattu_kertaa") or 0) > 0)
    liveuutisia = sum(1 for k in kortit if k.get("mahdollinen_liveuutinen") == "kyllä")
    epaedullisia = sum(1 for k in kortit if k.get("tagit_poliittinen_signaali",""))

    # Näkyvyysajat per aihetagi
    aihe_nakyvyys = {}
    aihe_lkm = {}
    for k in kortit:
        if k.get("mahdollinen_liveuutinen") == "kyllä": continue
        nakyvyys = k.get("nakyvyys_tunnit","")
        if not nakyvyys: continue
        try: nakyvyys_f = float(nakyvyys)
        except: continue
        tagit = [t.strip() for t in k.get("tagit_aihe","").split(",") if t.strip()]
        for tagi in tagit:
            aihe_nakyvyys[tagi] = aihe_nakyvyys.get(tagi, 0) + nakyvyys_f
            aihe_lkm[tagi] = aihe_lkm.get(tagi, 0) + 1

    # Julkaisuikkunajakauma — vasemmisto-epäed, oikeisto-epäed, muut
    ikkuna_vas = {}
    ikkuna_oik = {}
    ikkuna_muut = {}
    for k in kortit:
        ikkuna = k.get("julkaisuikkuna","")
        if not ikkuna: continue
        pol = set(t.strip() for t in k.get("tagit_poliittinen_signaali","").split(",") if t.strip())
        on_vas = bool(pol & vas_tagit)
        on_oik = bool(pol & oik_tagit)
        if on_vas:
            ikkuna_vas[ikkuna] = ikkuna_vas.get(ikkuna, 0) + 1
        elif on_oik:
            ikkuna_oik[ikkuna] = ikkuna_oik.get(ikkuna, 0) + 1
        else:
            ikkuna_muut[ikkuna] = ikkuna_muut.get(ikkuna, 0) + 1

    # Suosituimmat vs pääsivu -ristiriita
    ristiriita = [
        k for k in kortit
        if "suosituimmat" in k.get("osiot_joissa_nahty","")
        and "paasivu" not in k.get("osiot_joissa_nahty","")
    ]

    # ── Kirjoita tilastot ──
    rivit = []

    rivit.append([f"Päivitetty: {nyt.strftime('%Y-%m-%d %H:%M')}", ""])
    rivit.append(["", ""])

    vasemmisto_epa = sum(1 for k in kortit if any(
        t.strip() in ["maahanmuuttaja-rikoksentekija","turvapaikanhakija-ongelmat",
                      "ps-tai-oikeisto-onnistuu","vasemmisto-tai-vihrea-epaonnistuu",
                      "ydinvoima-positiivinen","trans-kriittinen",
                      "anti-nato","anti-eu","pro-israel","anti-palestiina"]
        for t in k.get("tagit_poliittinen_signaali","").split(",")
    ))
    oikeisto_epa = sum(1 for k in kortit if any(
        t.strip() in ["maahanmuutto-positiivinen","oikeisto-tai-ps-epaonnistuu",
                      "vasemmisto-tai-vihrea-onnistuu","ydinvoima-kriittinen",
                      "ilmastotoimet-positiivinen","trans-myonteinen",
                      "pro-nato","pro-eu","anti-israel","pro-palestiina"]
        for t in k.get("tagit_poliittinen_signaali","").split(",")
    ))

    rivit.append(["═══ YLEISKATSAUS ═══", ""])
    rivit.append(["Uutisia seurattu yhteensä", yhteensa])
    rivit.append(["Uutisia tänään päivitetty", tanaan_n])
    rivit.append(["Uutisia viimeisen 7 pv aikana", viikolla_n])
    rivit.append(["Julkaistu mutta ei nostettu etusivulle", ei_etusivulle])
    rivit.append(["Otsikkoa muutettu jälkikäteen", otsikko_muutettu])
    rivit.append(["Mahdollisia live-uutisia", liveuutisia])
    ei_etusivu_ajot = sum(1 for k in kortit if k.get("etusivu_haettu") == "ei")
    rivit.append(["Ajoja ilman etusivudataa (403)", ei_etusivu_ajot])
    rivit.append(["Vasemmistolle epäedullisia uutisia", vasemmisto_epa])
    rivit.append(["Oikeistolle epäedullisia uutisia", oikeisto_epa])
    rivit.append(["", ""])

    rivit.append(["═══ NÄKYVYYSAIKA KATEGORIOITTAIN (tuntia, ka) ═══", ""])
    rivit.append(["Kategoria", "Keskiarvo (h)", "Uutisia"])
    for tagi in sorted(aihe_lkm.keys()):
        if aihe_lkm[tagi] > 0:
            ka = round(aihe_nakyvyys[tagi] / aihe_lkm[tagi], 1)
            rivit.append([tagi, ka, aihe_lkm[tagi]])
    rivit.append(["", ""])

    rivit.append(["═══ ILTA-HYPOTEESI: Julkaisuikkuna vs. etusivulle pääsy ═══", ""])
    rivit.append(["Aikaikkuna", "Vasemmistolle epäed.", "Oikeistolle epäed.", "Muut uutiset"])
    for ikkuna in ["aamupiikki","tyopaiva","iltapaivapiikki","ilta","yo"]:
        rivit.append([ikkuna, ikkuna_vas.get(ikkuna,0), ikkuna_oik.get(ikkuna,0), ikkuna_muut.get(ikkuna,0)])
    rivit.append(["", ""])

    rivit.append(["═══ SUOSITUIMMAT vs. PÄÄSIVU (ristiriita) ═══", ""])
    rivit.append(["Uutisia suosituimmissa mutta ei pääsivulla", len(ristiriita)])
    if ristiriita:
        rivit.append(["Otsikko", "Näkyvyys (h)"])
        for k in ristiriita[:10]:
            rivit.append([k.get("otsikko",""), k.get("nakyvyys_tunnit","")])
    rivit.append(["", ""])

    rivit.append(["═══ OTSIKKOMUUTOKSET ═══", ""])
    muutetut = [k for k in kortit if int(k.get("muokattu_kertaa") or 0) > 0]
    if muutetut:
        rivit.append(["Alkuperäinen otsikko", "Nykyinen otsikko", "Muokattu kertaa", "Viimeisin muutos"])
        for k in muutetut[:20]:
            rivit.append([
                k.get("otsikko_alkuperainen",""),
                k.get("otsikko_nykyinen",""),
                k.get("muokattu_kertaa",""),
                k.get("viimeisin_muutos",""),
            ])

    # ── Top henkilöt ──
    henkilot_lkm = {}
    henkilot_nakyvyys = {}
    henkilot_vas = {}
    henkilot_oik = {}

    vas_tagit = {"maahanmuuttaja-rikoksentekija","turvapaikanhakija-ongelmat",
                 "ps-tai-oikeisto-onnistuu","vasemmisto-tai-vihrea-epaonnistuu",
                 "ydinvoima-positiivinen","trans-kriittinen",
                 "anti-nato","anti-eu","pro-israel","anti-palestiina"}
    oik_tagit = {"maahanmuutto-positiivinen","oikeisto-tai-ps-epaonnistuu",
                 "vasemmisto-tai-vihrea-onnistuu","ydinvoima-kriittinen",
                 "ilmastotoimet-positiivinen","trans-myonteinen",
                 "pro-nato","pro-eu","anti-israel","pro-palestiina"}

    for k in kortit:
        henkilo_str = k.get("tagit_henkilot","")
        if not henkilo_str:
            continue
        henkilot = [h.strip() for h in henkilo_str.split(",") if h.strip()]
        nakyvyys = float(k.get("nakyvyys_tunnit") or 0)
        pol_tagit = set(t.strip() for t in k.get("tagit_poliittinen_signaali","").split(",") if t.strip())

        for h in henkilot:
            henkilot_lkm[h] = henkilot_lkm.get(h, 0) + 1
            henkilot_nakyvyys[h] = henkilot_nakyvyys.get(h, 0) + nakyvyys
            if pol_tagit & vas_tagit:
                henkilot_vas[h] = henkilot_vas.get(h, 0) + 1
            if pol_tagit & oik_tagit:
                henkilot_oik[h] = henkilot_oik.get(h, 0) + 1

    top_henkilot = sorted(henkilot_lkm.items(), key=lambda x: x[1], reverse=True)[:20]

    rivit.append(["", ""])
    rivit.append(["═══ TOP 20 ENITEN UUTISOITU HENKILÖ ═══", ""])
    rivit.append(["Henkilö", "Uutisia", "Vasemmisto-epäed.", "Oikeisto-epäed.", "Ka. näkyvyys (h)"])
    for nimi, lkm in top_henkilot:
        ka_nakyvyys = round(henkilot_nakyvyys.get(nimi, 0) / lkm, 1) if lkm > 0 else ""
        rivit.append([
            nimi,
            lkm,
            henkilot_vas.get(nimi, 0),
            henkilot_oik.get(nimi, 0),
            ka_nakyvyys,
        ])

    ws_tilastot.append_rows(rivit, value_input_option="USER_ENTERED")
    print(f"Tilastot päivitetty: {len(rivit)} riviä")

# ── Uutiskortti-laskenta ──────────────────────────────────────────────────────

def laske_kortti(url, otsikko, rss, havainnot_nyt, nyt_str, kat, v2,
                 teemat, tagit_henkilot, live_data, etusivu_haettu_str, vanha=None):
    nyt_dt = parse_dt(nyt_str)
    julkaisuaika   = rss.get("julkaisuaika","") if rss else ""
    julkaisuikkuna = rss.get("julkaisuikkuna","") if rss else ""
    viikonpaiva    = rss.get("viikonpaiva","") if rss else ""
    etusivulla_nyt = len(havainnot_nyt) > 0

    tagit_aihe = tagit_str(kat.get("aihe",[]))
    tagit_keh  = tagit_str(kat.get("kehystys",[]))
    tagit_vas  = tagit_str(kat.get("poliittinen_signaali",[]))
    varmuus    = kat.get("varmuus", 0)

    vaihe2_tehty     = "kyllä" if v2 else "ei"
    vaihe2_lisatagit = ""
    if v2:
        kaikki_v2 = v2.get("kehystys_lisä",[]) + v2.get("poliittinen_signaali_lisä",[])
        vaihe2_lisatagit = tagit_str(kaikki_v2)

    mahdollinen_live, viimeisin_paivitys, paivitysviive = live_data

    tarkistamatta = "kyllä" if (varmuus < 70 or kat.get("poliittinen_signaali")) else "ei"

    if vanha:
        ensimmainen = vanha.get("ensimmainen_etusivu","")
        viimeinen   = nyt_str if etusivulla_nyt else vanha.get("viimeinen_etusivu","")
        havaintoja  = int(vanha.get("havaintoja_yhteensa") or 0)
        if etusivulla_nyt: havaintoja += len(havainnot_nyt)
        paras   = int(vanha.get("paras_sijainti")) if vanha.get("paras_sijainti") else ""
        huonoin = int(vanha.get("huonoin_sijainti") or 0)
        # Lasketaan oikea keskiarvo: (vanhat havainnot yhteensä + uudet) / kaikki havainnot
        vanhat_havaintoja = int(vanha.get("havaintoja_yhteensa") or 0)
        vanha_keski = float(vanha.get("keskisijainti") or 0)
        if etusivulla_nyt:
            nyt_sij = [h["sijainti"] for h in havainnot_nyt if h.get("sijainti")]
            if nyt_sij:
                paras   = min(paras, min(nyt_sij)) if paras != "" else min(nyt_sij)
                huonoin = max(huonoin, max(nyt_sij))
                uudet_summa = sum(nyt_sij)
                kaikki_n = vanhat_havaintoja + len(nyt_sij)
                keski_n = round((vanha_keski * vanhat_havaintoja + uudet_summa) / kaikki_n, 1) if kaikki_n > 0 else 0
            else:
                keski_n = vanha_keski
        else:
            keski_n = vanha_keski
        vanhat_osiot = set(vanha.get("osiot_joissa_nahty","").split(", ")) if vanha.get("osiot_joissa_nahty") else set()
        kaikki_osiot = ", ".join(sorted((vanhat_osiot | {h["osio"] for h in havainnot_nyt}) - {""}))
        etusivulla_koskaan    = "kyllä" if (vanha.get("etusivulla_koskaan")=="kyllä" or etusivulla_nyt) else "ei"
        julkaistu_ei_nostettu = "ei" if etusivulla_koskaan=="kyllä" else "kyllä"
        tarkistamatta = vanha.get("tarkistamatta","kyllä") if vanha.get("tarkistamatta")=="ei" else tarkistamatta
        if vanha.get("vaihe2_tehty") == "kyllä":
            vaihe2_tehty     = "kyllä"
            vaihe2_lisatagit = vanha.get("vaihe2_lisatagit","")
        # Säilytä live-tieto jos jo tarkistettu
        if vanha.get("mahdollinen_liveuutinen") == "kyllä":
            mahdollinen_live  = "kyllä"
            viimeisin_paivitys = vanha.get("viimeisin_paivitys", viimeisin_paivitys)
            paivitysviive      = vanha.get("paivitysviive_pv", paivitysviive)
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
            vanhat_t = ts(vanha.get("tagit_aihe","")) | ts(vanha.get("tagit_kehystys","")) | ts(vanha.get("tagit_poliittinen_signaali",""))
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
        teemat, tagit_henkilot,
        vaihe2_tehty, vaihe2_lisatagit,
        mahdollinen_live, viimeisin_paivitys, paivitysviive,
        varmuus, tarkistamatta, etusivu_haettu_str,
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
    etusivu_lista, etusivu_haettu = hae_etusivu_uutiset()
    print(f"Etusivu haettu: {etusivu_haettu}")

    sheet     = yhdista()
    ws_raaka  = hae_tai_luo_ws(sheet, "Raakadata",   RAAKA_OTSIKOT)
    ws_kortti = hae_tai_luo_ws(sheet, "Uutiskortti", KORTTI_OTSIKOT)
    kortit    = lue_kortit(ws_kortti)

    kategorisoidut = {}
    for url, k in kortit.items():
        if k.get("tagit_aihe") or k.get("tagit_kehystys"):
            kategorisoidut[url] = {
                "aihe":      [t.strip() for t in k.get("tagit_aihe","").split(",") if t.strip()],
                "kehystys":  [t.strip() for t in k.get("tagit_kehystys","").split(",") if t.strip()],
                "poliittinen_signaali": [t.strip() for t in k.get("tagit_poliittinen_signaali","").split(",") if t.strip()],
                "henkilot":  [t.strip() for t in k.get("tagit_henkilot","").split(",") if t.strip()],
                "varmuus":   k.get("varmuus",0),
            }

    etusivu_per_url = {}
    for h in etusivu_lista:
        etusivu_per_url.setdefault(h["url"],[]).append(h)

    kaikki_urlit = set(etusivu_per_url.keys()) | set(rss_uutiset.keys())

    tarvitsee_v1 = {
        url: (etusivu_per_url.get(url,[{}])[0].get("otsikko") or rss_uutiset.get(url,{}).get("otsikko",""))
        for url in kaikki_urlit
        if url not in kategorisoidut
    }
    tarvitsee_v1 = {k:v for k,v in tarvitsee_v1.items() if v}

    if MAX_UUTISET_PER_AJO and len(tarvitsee_v1) > MAX_UUTISET_PER_AJO:
        print(f"Rajoitetaan {len(tarvitsee_v1)} → {MAX_UUTISET_PER_AJO} uutiseen (testirajoitus)")
        tarvitsee_v1 = dict(list(tarvitsee_v1.items())[:MAX_UUTISET_PER_AJO])

    uudet_kategoriat = {}
    if tarvitsee_v1:
        print(f"\nVaihe 1: {len(tarvitsee_v1)} uutta uutista kategorisoidaan...")
        uudet_kategoriat = tee_batch_kategoriointi_v1(tarvitsee_v1)

    v2_tulokset = {}
    for url, kat in uudet_kategoriat.items():
        if kat.get("poliittinen_signaali"):
            print(f"Vaihe 2: {url[:60]}")
            v2_tulokset[url] = kategorisoi_artikkeli_v2(url, tarvitsee_v1[url], kat)
            time.sleep(0.5)

    raaka_rivit     = []
    kortti_uudet    = []
    kortti_paivitys = []

    for url in kaikki_urlit:
        havainnot = etusivu_per_url.get(url, [])
        rss       = rss_uutiset.get(url, {})
        otsikko   = havainnot[0]["otsikko"] if havainnot else rss.get("otsikko","")
        if not otsikko: continue

        kat = kategorisoidut.get(url) or uudet_kategoriat.get(url) or \
              {"aihe":[],"kehystys":[],"poliittinen_signaali":[],"varmuus":0}
        v2  = v2_tulokset.get(url)

        tagit_aihe = tagit_str(kat.get("aihe",[]))
        tagit_keh  = tagit_str(kat.get("kehystys",[]))
        tagit_vas  = tagit_str(kat.get("poliittinen_signaali",[]))
        tagit_henkilot = tagit_str(kat.get("henkilot",[]))
        varmuus    = kat.get("varmuus",0)
        teemat = etsi_teemat(otsikko)

        vaihe2_tehty     = "kyllä" if v2 else "ei"
        vaihe2_lisatagit = ""
        if v2:
            kaikki_v2 = v2.get("kehystys_lisä",[]) + v2.get("poliittinen_signaali_lisä",[])
            vaihe2_lisatagit = tagit_str(kaikki_v2)

        tarkistamatta = "kyllä" if (varmuus < 70 or kat.get("poliittinen_signaali")) else "ei"
        etusivu_haettu_str = "kyllä" if etusivu_haettu else "ei"

        # Live-uutinen tarkistus
        julkaisuaika = rss.get("julkaisuaika","")
        live_data = tarkista_liveuutinen(url, julkaisuaika, nyt)

        if havainnot:
            for h in havainnot:
                viive_min = ""
                if julkaisuaika:
                    dt_pub = parse_dt(julkaisuaika)
                    if dt_pub:
                        v = int((nyt-dt_pub).total_seconds()/60)
                        viive_min = v if v < 10080 else ""  # tyhjä jos yli 7 pv
                raaka_rivit.append([
                    nyt_str, url, otsikko, h["osio"], h["sijainti"],
                    julkaisuaika, rss.get("julkaisuikkuna",""),
                    rss.get("viikonpaiva",""), "kyllä",
                    tagit_aihe, tagit_keh, tagit_vas, teemat, tagit_henkilot,
                    vaihe2_tehty, vaihe2_lisatagit,
                    live_data[0], live_data[1], live_data[2],
                    varmuus, tarkistamatta, etusivu_haettu_str, viive_min,
                ])
        else:
            raaka_rivit.append([
                nyt_str, url, otsikko, "ei_etusivulla", "",
                julkaisuaika, rss.get("julkaisuikkuna",""),
                rss.get("viikonpaiva",""), "ei",
                tagit_aihe, tagit_keh, tagit_vas, teemat, tagit_henkilot,
                vaihe2_tehty, vaihe2_lisatagit,
                live_data[0], live_data[1], live_data[2],
                varmuus, tarkistamatta, etusivu_haettu_str, "",
            ])

        korttiarvo = laske_kortti(
            url, otsikko, rss, havainnot, nyt_str, kat, v2,
            teemat, tagit_henkilot, live_data, etusivu_haettu_str, vanha=kortit.get(url)
        )
        if url in kortit:
            kortti_paivitys.append((kortit[url]["_rivi"], korttiarvo))
        else:
            kortti_uudet.append(korttiarvo)

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

    # Päivitä tilastovälilehti
    print("\nPäivitetään tilastot...")
    paivita_tilastot(sheet, ws_kortti)
    time.sleep(2)

    print(f"\n✅ Valmis! {nyt_str}")

if __name__ == "__main__":
    main()
