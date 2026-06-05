#!/usr/bin/env python3
"""
Rekonstruoi Uutiskortti-välilehti Raakadata-välilehden pohjalta.
Ajetaan manuaalisesti kun Uutiskortti on tyhjennetty tai sekaisin.
"""

import os, json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import gspread
from google.oauth2.service_account import Credentials

HELSINKI = ZoneInfo("Europe/Helsinki")
SHEETS_SHEET_ID = os.environ["GOOGLE_SHEET_ID"]

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

def parse_dt(s):
    try: return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=HELSINKI)
    except: return None

def yhdista():
    creds = Credentials.from_service_account_info(
        json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"]),
        scopes=["https://spreadsheets.google.com/feeds","https://www.googleapis.com/auth/drive"]
    )
    return gspread.authorize(creds).open_by_key(SHEETS_SHEET_ID)

def main():
    print("=== Rekonstruoi Uutiskortti Raakadatasta ===\n")

    sheet = yhdista()
    ws_raaka = sheet.worksheet("Raakadata")

    # Luo tai tyhjennä Uutiskortti
    try:
        ws_kortti = sheet.worksheet("Uutiskortti")
        ws_kortti.clear()
        ws_kortti.append_row(KORTTI_OTSIKOT)
    except gspread.WorksheetNotFound:
        ws_kortti = sheet.add_worksheet(title="Uutiskortti", rows=10000, cols=len(KORTTI_OTSIKOT))
        ws_kortti.append_row(KORTTI_OTSIKOT)

    print("Luetaan Raakadata...")
    raaka = ws_raaka.get_all_records()
    print(f"Raakadatassa {len(raaka)} riviä")

    # Ryhmittele rivit URL:n mukaan
    url_rivit = {}
    for rivi in raaka:
        url = rivi.get("url","")
        if not url: continue
        url_rivit.setdefault(url, []).append(rivi)

    print(f"Uniikkeja uutisia: {len(url_rivit)}")

    kortit = []
    for url, rivit in url_rivit.items():
        # Järjestä aikaleiman mukaan
        rivit_j = sorted(rivit, key=lambda r: r.get("aikaleima",""))

        # Perustiedot ensimmäisestä rivistä
        eka = rivit_j[0]
        otsikko_alkuperainen = eka.get("otsikko","")
        otsikko_nykyinen     = rivit_j[-1].get("otsikko","")
        julkaisuaika   = eka.get("julkaisuaika","")
        julkaisuikkuna = eka.get("julkaisuikkuna","")
        viikonpaiva    = eka.get("viikonpaiva","")
        tagit_aihe     = eka.get("tagit_aihe","")
        tagit_keh      = eka.get("tagit_kehystys","")
        tagit_pol      = eka.get("tagit_poliittinen_signaali","")
        tagit_teemat   = eka.get("tagit_teemat","")
        tagit_henkilot = eka.get("tagit_henkilot","")
        vaihe2_tehty   = eka.get("vaihe2_tehty","ei")
        vaihe2_lisatagit = eka.get("vaihe2_lisatagit","")
        mahdollinen_live = eka.get("mahdollinen_liveuutinen","ei")
        viimeisin_paivitys = eka.get("viimeisin_paivitys","")
        paivitysviive  = eka.get("paivitysviive_pv","")
        varmuus        = eka.get("varmuus",0)
        tarkistamatta  = eka.get("tarkistamatta","kyllä")
        etusivu_haettu = eka.get("etusivu_haettu","kyllä")

        # Etusivu-havainnot
        etusivu_rivit = [r for r in rivit_j if r.get("etusivulla") == "kyllä"]
        etusivulla_koskaan = "kyllä" if etusivu_rivit else "ei"
        julkaistu_ei_nostettu = "ei" if etusivu_rivit else "kyllä"

        ensimmainen_etusivu = ""
        viimeinen_etusivu   = ""
        havaintoja          = len(etusivu_rivit)
        paras_sijainti      = ""
        huonoin_sijainti    = ""
        keski_sijainti      = ""
        osiot               = set()

        if etusivu_rivit:
            ensimmainen_etusivu = etusivu_rivit[0].get("aikaleima","")
            viimeinen_etusivu   = etusivu_rivit[-1].get("aikaleima","")
            sijainteja = [int(r["sijainti"]) for r in etusivu_rivit if r.get("sijainti")]
            if sijainteja:
                paras_sijainti   = min(sijainteja)
                huonoin_sijainti = max(sijainteja)
                keski_sijainti   = round(sum(sijainteja)/len(sijainteja), 1)
            osiot = {r.get("osio","") for r in etusivu_rivit if r.get("osio")}

        # Näkyvyystunnit
        nakyvyys_h = ""
        if ensimmainen_etusivu and viimeinen_etusivu:
            dt1 = parse_dt(ensimmainen_etusivu)
            dt2 = parse_dt(viimeinen_etusivu)
            if dt1 and dt2:
                nakyvyys_h = round((dt2-dt1).total_seconds()/3600, 1)

        # Viive julkaisusta etusivulle
        viive_etusivulle = ""
        if julkaisuaika and ensimmainen_etusivu:
            dt_pub = parse_dt(julkaisuaika)
            dt_etu = parse_dt(ensimmainen_etusivu)
            if dt_pub and dt_etu:
                v = int((dt_etu-dt_pub).total_seconds()/60)
                viive_etusivulle = v if v < 10080 else ""

        # Muutoshistoria
        muokattu_kertaa  = 0
        viimeisin_muutos = ""
        muutoshistoria   = ""
        edellinen_otsikko = otsikko_alkuperainen

        for rivi in rivit_j[1:]:
            nykyinen = rivi.get("otsikko","")
            if nykyinen and nykyinen.strip() != edellinen_otsikko.strip():
                muokattu_kertaa += 1
                viimeisin_muutos = rivi.get("aikaleima","")
                merkinta = f"{viimeisin_muutos} | '{edellinen_otsikko}' → '{nykyinen}'"
                muutoshistoria = (muutoshistoria + " || " + merkinta) if muutoshistoria else merkinta
                edellinen_otsikko = nykyinen

        paivitetty = rivit_j[-1].get("aikaleima","")

        kortit.append([
            url, otsikko_nykyinen,
            julkaisuaika, julkaisuikkuna, viikonpaiva,
            ensimmainen_etusivu, viimeinen_etusivu, viive_etusivulle,
            nakyvyys_h, havaintoja,
            paras_sijainti, huonoin_sijainti, keski_sijainti,
            ", ".join(sorted(osiot - {""})),
            etusivulla_koskaan, julkaistu_ei_nostettu,
            tagit_aihe, tagit_keh, tagit_pol,
            tagit_teemat, tagit_henkilot,
            vaihe2_tehty, vaihe2_lisatagit,
            mahdollinen_live, viimeisin_paivitys, paivitysviive,
            varmuus, tarkistamatta, etusivu_haettu,
            otsikko_alkuperainen, otsikko_nykyinen,
            muokattu_kertaa, viimeisin_muutos, muutoshistoria,
            paivitetty,
        ])

    # Tallenna erissä
    print(f"\nTallennetaan {len(kortit)} uutiskorttia...")
    for i in range(0, len(kortit), 100):
        erä = kortit[i:i+100]
        ws_kortti.append_rows(erä, value_input_option="USER_ENTERED")
        print(f"  {min(i+100, len(kortit))}/{len(kortit)}")

    print(f"\n✅ Valmis! {len(kortit)} uutiskorttia rekonstruoitu.")

if __name__ == "__main__":
    main()
