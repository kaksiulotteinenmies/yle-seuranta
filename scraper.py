#!/usr/bin/env python3
"""
Yle-uutisseuranta: Etusivun skrapperi + Claude-kategorisointi + Google Sheets
Ajastettu GitHub Actionsilla kerran tunnissa.

Välilehdet:
  1. Raakadata     — jokainen havainto omana rivinään (koskematon arkisto)
  2. Uutiskortti   — yksi rivi per uutinen, päivittyy automaattisesti
"""

import os
import json
import time
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from bs4 import BeautifulSoup
import gspread
from google.oauth2.service_account import Credentials
import anthropic

# ── Asetukset ────────────────────────────────────────────────────────────────

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

AIKAIKKUNAT = {
    (6,  9):  "aamupiikki",
    (9,  16): "tyopaiva",
    (16, 18): "iltapaivapiikki",
    (18, 22): "ilta",
    (22, 24): "yo",
    (0,  6):  "yo",
}
VIIKONPAIVAT = ["maanantai","tiistai","keskiviikko","torstai",
                "perjantai","lauantai","sunnuntai"]

# ── Tagit ────────────────────────────────────────────────────────────────────
# Kaikki mahdolliset tagit ryhmittäin. Claude valitsee näistä — ei keksi uusia.

TAGIT = {
    "aihe": [
        "maahanmuutto", "rikos", "vakivalta", "seksuaalirikos", "terrorismi",
        "politiikka-hallitus", "politiikka-oppositio", "talous", "tyollisyys",
        "ilmastonmuutos", "energia", "terveys", "koulutus",
        "urheilu", "kulttuuri", "ulkomaat", "onnettomuus", "oikeus",
    ],
    "tekija_kohde": [
        "tekija-maahanmuuttaja", "tekija-kantasuomalainen", "tekija-tuntematon",
        "tekija-aarioikeisto", "tekija-äärivasemmisto",
        "kohde-nainen", "kohde-mies", "kohde-lapsi", "kohde-virkavalta",
        "poliitikko-vasemmisto", "poliitikko-oikeisto", "poliitikko-kepu",
        "poliitikko-aarioikeisto", "poliitikko-äärivasemmisto",
    ],
    "kehystys": [
        "savy-positiivinen", "savy-negatiivinen", "savy-neutraali",
        "tausta-mainittu", "tausta-ei-mainittu",
        "lahde-vain-viranomainen", "lahde-monipuolinen",
        "tilasto", "yksittaistapaus",
        "molemmat-osapuolet-mainittu",
        "painotus-oikeistokriittinen", "painotus-vasemmistokriittinen",
        "painotus-tasapuolinen",
    ],
    "sensitiivisyys": [
        "sensitiivinen-maahanmuutto",
        "sensitiivinen-rikos-ja-etnisyys",
        "sensitiivinen-sukupuoli",
        "sensitiivinen-ilmasto",
        "sensitiivinen-kristinusko",
        "sensitiivinen-islam",
        "sensitiivinen-poliisi-ja-valta",
    ],
    "kanta": [
        # Geopolitiikka
        "pro-palestiina", "anti-palestiina",
        "pro-israel", "anti-israel",
        "pro-trump", "anti-trump",
        "pro-nato", "anti-nato",
        "pro-eu", "anti-eu",
        # Yhteiskunta
        "pro-maahanmuutto", "anti-maahanmuutto",
        "pro-sateenkaari", "anti-sateenkaari",
        "pro-transideologia", "anti-transideologia",
        "pro-feminismi", "anti-feminismi",
        # Ympäristö & energia
        "pro-ydinvoima", "anti-ydinvoima",
        "pro-luonnonsuojelu", "anti-luonnonsuojelu",
        # Uskonto (eriteltynä)
        "pro-kristinusko", "anti-kristinusko",
        "pro-islam", "anti-islam",
    ],
}

# Taskulista kaikkia tageista validointia varten
KAIKKI_TAGIT = [t for ryhmä in TAGIT.values() for t in ryhmä]

# ── Sarakeratkenne ────────────────────────────────────────────────────────────

RAAKA_OTSIKOT = [
    "aikaleima","url","otsikko","osio","sijainti",
    "julkaisuaika","julkaisuikkuna","viikonpaiva","etusivulla",
    # Tagit omina sarakkeinaan (helppo suodatus Sheetsissä)
    "tagit_aihe","tagit_tekija_kohde","tagit_kehystys","tagit_sensitiivisyys","tagit_kanta",
    "varmuus","tarkistamatta","viive_julkaisusta_min",
]

KORTTI_OTSIKOT = [
    "url","otsikko",
    "julkaisuaika","julkaisuikkuna","viikonpaiva",
    "ensimmainen_etusivu","viimeinen_etusivu","viive_julkaisusta_etusivulle_min",
    "nakyvyys_tunnit","havaintoja_yhteensa",
    "paras_sijainti","huonoin_sijainti","keskisijainti",
    "osiot_joissa_nahty",
    "etusivulla_koskaan","julkaistu_ei_nostettu",
    # Tagit
    "tagit_aihe","tagit_tekija_kohde","tagit_kehystys","tagit_sensitiivisyys","tagit_kanta",
    "varmuus","tarkistamatta",
    # Muutoshistoria
    "otsikko_alkuperainen",
    "otsikko_nykyinen",
    "muokattu_kertaa",
    "viimeisin_muutos",
    "muutoshistoria",
    "paivitetty",
]

# ── Apufunktiot ───────────────────────────────────────────────────────────────

def get_aikaikkuna(dt):
    h = dt.hour
    for (a, b), nimi in AIKAIKKUNAT.items():
        if a <= h < b:
            return nimi
    return "yo"

def get_viikonpaiva(dt):
    return VIIKONPAIVAT[dt.weekday()]

def parse_dt(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    except Exception:
        return None

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
                link  = item.findtext("link","").strip()
                title = item.findtext("title","").strip()
                pub   = item.findtext("pubDate","")
                try:
                    pub_dt = parsedate_to_datetime(pub).astimezone(timezone.utc)
                except Exception:
                    pub_dt = datetime.now(timezone.utc)
                if link and link not in uutiset:
                    uutiset[link] = {
                        "otsikko":        title,
                        "julkaisuaika":   pub_dt.strftime("%Y-%m-%d %H:%M"),
                        "julkaisuikkuna": get_aikaikkuna(pub_dt),
                        "viikonpaiva":    get_viikonpaiva(pub_dt),
                    }
        except Exception as e:
            print(f"RSS-virhe ({rss_url}): {e}")
    print(f"RSS: {len(uutiset)} uutista yhteensä")
    return uutiset

# ── Etusivu ───────────────────────────────────────────────────────────────────

OHITA_URLIT = [
    "/uutiset/paikallisuutiset", "/uutiset/yhteystiedot",
    "yle.fi/t/", "areena.yle.fi", "/rss", "/opas",
    "/lyhyet", "/tuoreimmat", "/selkouutiset",
    "sanapyramidi", "futistietaja", "saavutettavuus",
    "asiakaspalvelu", "yhteystiedot", "onelink.me",
    "/abitreenit", "/elavaarkisto", "/oppiminen",
    "/uutiset/lyhyesti",  # lyhyesti haetaan erikseen
    "74-20131998",  # sanapyramidi id
]

def tunnista_osio(href):
    """Tunnistaa uutisen osion URL-muodon perusteella."""
    if "/uutiset/lyhyesti/" in href:
        return "lyhyesti"
    if "/a/74-" in href or "/a/3-" in href:
        return "paasivu"
    if "/urheilu/" in href:
        return "paasivu"
    if "/kulttuuri/" in href:
        return "paasivu"
    return "paasivu"

def hae_etusivu_uutiset():
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "fi-FI,fi;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
    }
    session = requests.Session()
    session.headers.update(headers)
    resp = session.get(YLE_ETUSIVU_URL, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    tulokset = []
    nahdyt_urlit = set()
    sijainti_per_osio = {}

    # ── 1. Lyhyesti-osio (oma URL-muoto) ──
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if not href.startswith("http"):
            href = "https://yle.fi" + href
        if "/uutiset/lyhyesti/" not in href:
            continue
        ots = a.get_text(strip=True)
        if not ots or len(ots) < 5 or href in nahdyt_urlit:
            continue
        nahdyt_urlit.add(href)
        sijainti_per_osio["lyhyesti"] = sijainti_per_osio.get("lyhyesti", 0) + 1
        tulokset.append({"url": href, "otsikko": ots,
                         "osio": "lyhyesti",
                         "sijainti": sijainti_per_osio["lyhyesti"]})

    # ── 2. Suosituimmat ja Tuoreimmat (otsikon perusteella) ──
    for h2 in soup.find_all("h2"):
        teksti = h2.get_text(strip=True).lower()
        if "suosituimm" in teksti or "tuoreim" in teksti:
            osio = "suosituimmat" if "suosituimm" in teksti else "tuoreimmat"
            # Etsi seuraavat linkit tämän otsikon jälkeen
            for sibling in h2.find_next_siblings():
                for a in sibling.find_all("a", href=True):
                    href = a.get("href", "")
                    if not href.startswith("http"):
                        href = "https://yle.fi" + href
                    if any(o in href for o in OHITA_URLIT):
                        continue
                    if "/a/74-" not in href and "/a/3-" not in href:
                        continue
                    ots = a.get_text(strip=True)
                    if not ots or len(ots) < 5 or href in nahdyt_urlit:
                        continue
                    nahdyt_urlit.add(href)
                    sijainti_per_osio[osio] = sijainti_per_osio.get(osio, 0) + 1
                    tulokset.append({"url": href, "otsikko": ots,
                                     "osio": osio,
                                     "sijainti": sijainti_per_osio[osio]})
                # Lopeta kun törmätään seuraavaan h2:een
                if sibling.name == "h2":
                    break

    # ── 3. Pääsivu: kaikki muut /a/74- linkit ──
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if not href.startswith("http"):
            href = "https://yle.fi" + href
        if href in nahdyt_urlit:
            continue
        if any(o in href for o in OHITA_URLIT):
            continue
        if "/a/74-" not in href and "/a/3-" not in href:
            continue
        ots = a.get_text(strip=True)
        if not ots or len(ots) < 10:
            continue
        nahdyt_urlit.add(href)
        sijainti_per_osio["paasivu"] = sijainti_per_osio.get("paasivu", 0) + 1
        tulokset.append({"url": href, "otsikko": ots,
                         "osio": "paasivu",
                         "sijainti": sijainti_per_osio["paasivu"]})

    print(f"Etusivu: {len(tulokset)} havaintoa ({dict(sijainti_per_osio)})")
    return tulokset

# ── Kategorisointi tagijärjestelmällä ─────────────────────────────────────────

def kategorisoi(otsikko):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    tagit_str = json.dumps(TAGIT, ensure_ascii=False, indent=2)

    prompt = f"""Olet uutisten kategorisointijärjestelmä. Tehtäväsi on merkitä suomalainen uutisotsikko tageilla.

SÄÄNTÖJÄ:
- Valitse tagit AINOASTAAN alla olevasta listasta. Älä keksi uusia tageja.
- Voit valita useita tageja samasta ryhmästä.
- Jos et ole varma, jätä valitsematta — on parempi merkitä liian vähän kuin väärin.
- Arvioi varmuutesi kokonaisuutena (0-100).

TAGIT:
{tagit_str}

OTSIKKO: "{otsikko}"

Vastaa AINOASTAAN JSON-muodossa, ei muuta tekstiä:
{{
  "aihe":          ["tagi1", "tagi2"],
  "tekija_kohde":  ["tagi1"],
  "kehystys":      ["tagi1", "tagi2"],
  "sensitiivisyys":["tagi1"],
  "kanta":         ["tagi1", "tagi2"],
  "varmuus":       85
}}"""

    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role":"user","content":prompt}]
        )
        teksti = msg.content[0].text.strip().replace("```json","").replace("```","").strip()
        data = json.loads(teksti)

        # Validointi: poistetaan tagit jotka eivät ole sallitussa listassa
        for ryhmä in ["aihe","tekija_kohde","kehystys","sensitiivisyys","kanta"]:
            data[ryhmä] = [t for t in data.get(ryhmä,[]) if t in KAIKKI_TAGIT]

        return data

    except Exception as e:
        print(f"Kategorisointivirhe ({otsikko[:40]}): {e}")
        return {"aihe":[],"tekija_kohde":[],"kehystys":[],"sensitiivisyys":[],"varmuus":0}

def tagit_stringiksi(lista):
    """Muuntaa tagilistan pilkkueroteluksi stringiksi Sheetsiä varten."""
    return ", ".join(lista) if lista else ""

# ── Google Sheets ─────────────────────────────────────────────────────────────

def yhdista():
    creds = Credentials.from_service_account_info(
        json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"]),
        scopes=["https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive"]
    )
    return gspread.authorize(creds).open_by_key(SHEETS_SHEET_ID)

def hae_tai_luo_ws(sheet, nimi, otsikot):
    try:
        ws = sheet.worksheet(nimi)
        if not ws.get_all_values():
            ws.append_row(otsikot)
    except gspread.WorksheetNotFound:
        ws = sheet.add_worksheet(title=nimi, rows=10000, cols=len(otsikot))
        ws.append_row(otsikot)
    return ws

def lue_kortit(ws_kortti):
    rivit = ws_kortti.get_all_records()
    kortit = {}
    for i, r in enumerate(rivit, 2):
        url = r.get("url","")
        if url:
            kortit[url] = {"_rivi": i, **r}
    return kortit

# ── Uutiskortti-laskenta ──────────────────────────────────────────────────────

def laske_kortti(url, otsikko, rss, havainnot_nyt, nyt_str, kat, vanha=None):
    nyt_dt = parse_dt(nyt_str)
    julkaisuaika   = rss.get("julkaisuaika","") if rss else ""
    julkaisuikkuna = rss.get("julkaisuikkuna","") if rss else ""
    viikonpaiva    = rss.get("viikonpaiva","") if rss else ""
    etusivulla_nyt = len(havainnot_nyt) > 0

    tagit_aihe     = tagit_stringiksi(kat.get("aihe",[]))
    tagit_tk       = tagit_stringiksi(kat.get("tekija_kohde",[]))
    tagit_kehystys = tagit_stringiksi(kat.get("kehystys",[]))
    tagit_sens     = tagit_stringiksi(kat.get("sensitiivisyys",[]))
    tagit_kanta    = tagit_stringiksi(kat.get("kanta",[]))
    varmuus        = kat.get("varmuus", 0)
    # Merkitään tarkistettavaksi jos varmuus alle 70, sensitiivinen tai kanta-tagi
    tarkistamatta  = "kyllä" if (varmuus < 70 or bool(kat.get("sensitiivisyys")) or bool(kat.get("kanta"))) else "ei"

    if vanha:
        ensimmainen = vanha.get("ensimmainen_etusivu","")
        viimeinen   = nyt_str if etusivulla_nyt else vanha.get("viimeinen_etusivu","")
        havaintoja  = int(vanha.get("havaintoja_yhteensa") or 0)
        if etusivulla_nyt:
            havaintoja += len(havainnot_nyt)

        paras   = int(vanha.get("paras_sijainti") or 9999)
        huonoin = int(vanha.get("huonoin_sijainti") or 0)
        keski_n = float(vanha.get("keskisijainti") or 0)

        if etusivulla_nyt:
            nyt_sij = [h["sijainti"] for h in havainnot_nyt if h.get("sijainti")]
            if nyt_sij:
                paras   = min(paras, min(nyt_sij))
                huonoin = max(huonoin, max(nyt_sij))
                keski_n = round((keski_n + sum(nyt_sij)/len(nyt_sij)) / 2, 1)

        vanhat_osiot = set(vanha.get("osiot_joissa_nahty","").split(", ")) if vanha.get("osiot_joissa_nahty") else set()
        uudet_osiot  = {h["osio"] for h in havainnot_nyt}
        kaikki_osiot = ", ".join(sorted((vanhat_osiot | uudet_osiot) - {""}))

        etusivulla_koskaan    = "kyllä" if (vanha.get("etusivulla_koskaan") == "kyllä" or etusivulla_nyt) else "ei"
        julkaistu_ei_nostettu = "ei" if etusivulla_koskaan == "kyllä" else "kyllä"
        # Säilytä manuaalisesti tarkistettu status
        tarkistamatta = vanha.get("tarkistamatta","kyllä") if vanha.get("tarkistamatta") == "ei" else tarkistamatta

    else:
        ensimmainen = nyt_str if etusivulla_nyt else ""
        viimeinen   = nyt_str if etusivulla_nyt else ""
        havaintoja  = len(havainnot_nyt) if etusivulla_nyt else 0
        nyt_sij     = [h["sijainti"] for h in havainnot_nyt if h.get("sijainti")]
        paras   = min(nyt_sij) if nyt_sij else ""
        huonoin = max(nyt_sij) if nyt_sij else ""
        keski_n = round(sum(nyt_sij)/len(nyt_sij), 1) if nyt_sij else ""
        kaikki_osiot          = ", ".join(sorted({h["osio"] for h in havainnot_nyt}))
        etusivulla_koskaan    = "kyllä" if etusivulla_nyt else "ei"
        julkaistu_ei_nostettu = "ei" if etusivulla_nyt else "kyllä"

    if ensimmainen and viimeinen:
        dt1, dt2 = parse_dt(ensimmainen), parse_dt(viimeinen)
        nakyvyys_h = round((dt2 - dt1).total_seconds() / 3600, 1) if dt1 and dt2 else ""
    else:
        nakyvyys_h = ""

    viive = ""
    if julkaisuaika and ensimmainen:
        dt_pub, dt_etu = parse_dt(julkaisuaika), parse_dt(ensimmainen)
        if dt_pub and dt_etu:
            viive = int((dt_etu - dt_pub).total_seconds() / 60)

    # ── Muutoshistoria ──
    if vanha:
        otsikko_alkuperainen = vanha.get("otsikko_alkuperainen") or vanha.get("otsikko","")
        otsikko_nykyinen     = otsikko
        muokattu_kertaa      = int(vanha.get("muokattu_kertaa") or 0)
        muutoshistoria       = vanha.get("muutoshistoria","")
        viimeisin_muutos     = vanha.get("viimeisin_muutos","")

        vanha_otsikko = vanha.get("otsikko_nykyinen") or vanha.get("otsikko","")
        if vanha_otsikko and vanha_otsikko.strip() != otsikko.strip():
            muokattu_kertaa += 1
            viimeisin_muutos = nyt_str

            # Vertaa tageja — mitä muuttui
            def tagit_set(s):
                return set(t.strip() for t in s.split(",") if t.strip())

            vanhat = (tagit_set(vanha.get("tagit_aihe","")) |
                      tagit_set(vanha.get("tagit_tekija_kohde","")) |
                      tagit_set(vanha.get("tagit_kehystys","")) |
                      tagit_set(vanha.get("tagit_sensitiivisyys","")) |
                      tagit_set(vanha.get("tagit_kanta","")))
            uudet  = (tagit_set(tagit_aihe) | tagit_set(tagit_tk) |
                      tagit_set(tagit_kehystys) | tagit_set(tagit_sens) |
                      tagit_set(tagit_kanta))

            lisatyt   = uudet - vanhat
            poistetut = vanhat - uudet

            tagi_muutos = ""
            if lisatyt:
                tagi_muutos += f" +[{', '.join(sorted(lisatyt))}]"
            if poistetut:
                tagi_muutos += f" -[{', '.join(sorted(poistetut))}]"
            if not tagi_muutos:
                tagi_muutos = " (tagit ennallaan)"

            merkinta = (f"{nyt_str} | "
                        f"'{vanha_otsikko}' → '{otsikko}' |"
                        f" tagit:{tagi_muutos}")

            muutoshistoria = (muutoshistoria + " || " + merkinta
                              if muutoshistoria else merkinta)
            # Uudelleenkategorisointi jos otsikko muuttui merkittävästi
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
        paras, huonoin, keski_n,
        kaikki_osiot,
        etusivulla_koskaan, julkaistu_ei_nostettu,
        tagit_aihe, tagit_tk, tagit_kehystys, tagit_sens, tagit_kanta,
        varmuus, tarkistamatta,
        otsikko_alkuperainen, otsikko_nykyinen,
        muokattu_kertaa, viimeisin_muutos, muutoshistoria,
        nyt_str,
    ]

# ── Pääohjelma ────────────────────────────────────────────────────────────────

def main():
    nyt     = datetime.now(timezone.utc)
    nyt_str = nyt.strftime("%Y-%m-%d %H:%M")
    print(f"\n=== Yle-seuranta: {nyt_str} UTC ===\n")

    rss_uutiset   = hae_rss_uutiset()
    etusivu_lista = hae_etusivu_uutiset()

    sheet     = yhdista()
    ws_raaka  = hae_tai_luo_ws(sheet, "Raakadata",   RAAKA_OTSIKOT)
    ws_kortti = hae_tai_luo_ws(sheet, "Uutiskortti", KORTTI_OTSIKOT)

    kortit         = lue_kortit(ws_kortti)
    kategorisoidut = {
        url: {
            "aihe":          [t.strip() for t in k.get("tagit_aihe","").split(",") if t.strip()],
            "tekija_kohde":  [t.strip() for t in k.get("tagit_tekija_kohde","").split(",") if t.strip()],
            "kehystys":      [t.strip() for t in k.get("tagit_kehystys","").split(",") if t.strip()],
            "sensitiivisyys":[t.strip() for t in k.get("tagit_sensitiivisyys","").split(",") if t.strip()],
            "kanta":         [t.strip() for t in k.get("tagit_kanta","").split(",") if t.strip()],
            "varmuus":       k.get("varmuus", 0),
        }
        for url, k in kortit.items()
        if k.get("tagit_aihe") or k.get("tagit_kehystys")
    }

    etusivu_per_url = {}
    for h in etusivu_lista:
        etusivu_per_url.setdefault(h["url"], []).append(h)

    kaikki_urlit = set(etusivu_per_url.keys()) | set(rss_uutiset.keys())

    raaka_rivit     = []
    kortti_uudet    = []
    kortti_paivitys = []

    for url in kaikki_urlit:
        havainnot = etusivu_per_url.get(url, [])
        rss       = rss_uutiset.get(url, {})
        otsikko   = havainnot[0]["otsikko"] if havainnot else rss.get("otsikko","")

        if url in kategorisoidut:
            kat = kategorisoidut[url]
        else:
            print(f"Kategorisoidaan: {otsikko[:60]}")
            kat = kategorisoi(otsikko)
            time.sleep(0.3)

        tagit_aihe     = tagit_stringiksi(kat.get("aihe",[]))
        tagit_tk       = tagit_stringiksi(kat.get("tekija_kohde",[]))
        tagit_kehystys = tagit_stringiksi(kat.get("kehystys",[]))
        tagit_sens     = tagit_stringiksi(kat.get("sensitiivisyys",[]))
        tagit_kanta    = tagit_stringiksi(kat.get("kanta",[]))
        varmuus        = kat.get("varmuus", 0)
        tarkistamatta  = "kyllä" if (varmuus < 70 or bool(kat.get("sensitiivisyys")) or bool(kat.get("kanta"))) else "ei"

        # ── Raakadata ──
        if havainnot:
            for h in havainnot:
                viive_min = ""
                if rss.get("julkaisuaika"):
                    dt_pub = parse_dt(rss["julkaisuaika"])
                    if dt_pub:
                        viive_min = int((nyt - dt_pub).total_seconds() / 60)
                raaka_rivit.append([
                    nyt_str, url, otsikko, h["osio"], h["sijainti"],
                    rss.get("julkaisuaika",""), rss.get("julkaisuikkuna",""),
                    rss.get("viikonpaiva",""), "kyllä",
                    tagit_aihe, tagit_tk, tagit_kehystys, tagit_sens, tagit_kanta,
                    varmuus, tarkistamatta, viive_min,
                ])
        else:
            raaka_rivit.append([
                nyt_str, url, otsikko, "ei_etusivulla", "",
                rss.get("julkaisuaika",""), rss.get("julkaisuikkuna",""),
                rss.get("viikonpaiva",""), "ei",
                tagit_aihe, tagit_tk, tagit_kehystys, tagit_sens, tagit_kanta,
                varmuus, tarkistamatta, "",
            ])

        # ── Uutiskortti ──
        korttiarvo = laske_kortti(
            url, otsikko, rss, havainnot, nyt_str, kat,
            vanha=kortit.get(url)
        )
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
            kirjain = ""
            while n:
                n, r = divmod(n - 1, 26)
                kirjain = chr(65 + r) + kirjain
            return kirjain
        col_letter = sarakekirjain(len(KORTTI_OTSIKOT))
        for rivi_nro, arvot in kortti_paivitys:
            ws_kortti.update(
                values=[arvot],
                range_name=f"A{rivi_nro}:{col_letter}{rivi_nro}",
                value_input_option="USER_ENTERED"
            )
            time.sleep(1.2)  # Google Sheets: max 60 kirjoitusta/min
        print(f"Uutiskortti: {len(kortti_paivitys)} päivitetty")

    print(f"\n✅ Valmis! {nyt_str}")

if __name__ == "__main__":
    main()
