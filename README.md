# Yle-uutisseuranta — Käyttöohje ja dokumentaatio

## Mitä järjestelmä tekee

Botti skannaa Yle.fi:n etusivua kerran tunnissa ja tallentaa kaiken dataan.
Tavoitteena on selvittää dataan perustuen, katoavatko tietyt uutiset
etusivulta nopeammin kuin muut — erityisesti ne jotka ovat poliittisesti
epäedullisia vasemmistolle.

---

## Kaksivaiheinen analyysimalli

### Vaihe 1 — Otsikon perusteella (kaikki uutiset)
Kaikki uutiset kategorisoidaan otsikon perusteella Batch API:lla.
Batch API on 50% halvempi kuin yksittäiset kutsut.
Kategorisoidaan kolme tagiryhmää (ks. alla).

### Vaihe 2 — Artikkelin perusteella (vain herkkä sisältö)
Jos vaihe 1 tunnistaa "vasemmistolle epäedullinen" -tagin,
botti hakee ja lukee koko artikkelin ja täydentää tageja tarkemmilla
tiedoilla: tekijän tausta mainittu/ei, lähteen monipuolisuus, painotus jne.

---

## Tagit

### Ryhmä 1: Aihe (vaihe 1, otsikosta)
Mistä uutinen kertoo:
maahanmuutto, rikos, vakivalta, seksuaalirikos, terrorismi,
politiikka-hallitus, politiikka-oppositio, talous, tyollisyys,
ilmastonmuutos, energia, terveys, koulutus, urheilu, kulttuuri,
ulkomaat, onnettomuus, oikeus

### Ryhmä 2: Kehystys (vaihe 1, otsikosta)
Miten uutinen on kirjoitettu — vain otsikosta selvästi pääteltävissä:
savy-positiivinen, savy-negatiivinen, savy-neutraali,
tekija-maahanmuuttaja, tekija-kantasuomalainen, tekija-tuntematon,
kohde-nainen, kohde-mies, kohde-lapsi, kohde-virkavalta,
poliitikko-vasemmisto, poliitikko-oikeisto, poliitikko-kepu,
poliitikko-aarioikeisto, poliitikko-äärivasemmisto,
tilasto, yksittaistapaus

### Ryhmä 3: Vasemmistolle epäedullinen (vaihe 1, otsikosta)
Uutiset jotka ovat poliittisesti epäedullisia vasemmistolle:
maahanmuuttaja-rikoksentekija, turvapaikanhakija-ongelmat,
ps-tai-oikeisto-onnistuu, vasemmisto-tai-vihrea-epaonnistuu,
ydinvoima-positiivinen, trans-kriittinen,
anti-nato, anti-eu, pro-israel, anti-palestiina

### Vaihe 2 lisätagit (artikkelista, vain herkille uutisille)
Kehystyksen täydennys:
tausta-mainittu, tausta-ei-mainittu, tekija-aarioikeisto,
tekija-äärivasemmisto, lahde-vain-viranomainen, lahde-monipuolinen,
painotus-oikeistokriittinen, painotus-vasemmistokriittinen

Vasemmistolle epäedullinen täydennys:
integraatio-epaonnistuminen, islam-ongelmat-suomessa,
rinnakkaisyhteiskunta, maahanmuuton-kustannukset,
ilmastopolitiikka-kustannukset, vihrea-siirtyman-ongelmat,
anti-sateenkaari, anti-transideologia, anti-islam

---

## Google Sheets rakenne

### Välilehti 1: Raakadata
Jokainen skannaus omana rivinään — koskematon arkisto.
Yksi uutinen voi esiintyä useita kertoja (eri skannauksista).

Tärkeimmät sarakkeet:
- aikaleima — milloin skannaus tehtiin
- url — artikkelin pysyvä osoite
- osio — paasivu / lyhyesti / suosituimmat / tuoreimmat / ei_etusivulla
- etusivulla — kyllä / ei
- tagit_vasemmisto_epäedullinen — projektin ydinsarake
- vaihe2_tehty — onko artikkeli luettu tarkemmin

### Välilehti 2: Uutiskortti
Yksi rivi per uutinen — päivittyy automaattisesti.

Tärkeimmät sarakkeet:
- ensimmainen_etusivu / viimeinen_etusivu — milloin näkyi
- nakyvyys_tunnit — kuinka kauan etusivulla yhteensä
- paras_sijainti — korkein sijainti (pienin numero = ylimpänä)
- osiot_joissa_nahty — missä kaikissa osioissa nähty
- etusivulla_koskaan — kyllä / ei
- julkaistu_ei_nostettu — julkaistu mutta ei koskaan etusivulle
- vaihe2_lisatagit — artikkelin perusteella saadut lisätagit
- tarkistamatta — kyllä jos kannattaa tarkistaa manuaalisesti
- otsikko_alkuperainen — ensimmäinen otsikko (ei muutu)
- muutoshistoria — kaikki otsikkomuutokset aikaleimoineen

---

## Aikaikkunat

- aamupiikki: 06–09 (korkein lukijakunta)
- tyopaiva: 09–16
- iltapaivapiikki: 16–18
- ilta: 18–22 (hypoteesi: herkkiä uutisia julkaistaan tähän aikaan)
- yo: 22–06

---

## Hypoteesit joita testataan

1. Katoavatko "vasemmistolle epäedulliset" uutiset etusivulta
   nopeammin kuin muut vastaavan kategorian uutiset?

2. Julkaistaanko herkkiä uutisia systemaattisesti illalla
   jotta ne katoavat uutisvirrasta ennen aamuruuhkaa?

3. Onko uutisia jotka on julkaistu mutta ei koskaan nostettu etusivulle?
   (sarake: julkaistu_ei_nostettu = kyllä)

4. Muutetaanko uutisten otsikoita jälkikäteen tavalla joka
   pehmentää sisältöä? (sarake: muutoshistoria)

---

## Hyödyllisiä suodatuksia Sheetsissä

- Suodata julkaistu_ei_nostettu = "kyllä"
  → näet kaikki julkaistut mutta piilotetut uutiset

- Suodata tagit_vasemmisto_epäedullinen ei tyhjä
  → vertaa nakyvyys_tunnit muihin uutisiin

- Suodata julkaisuikkuna = "ilta" + etusivulla_koskaan = "ei"
  → testaa ilta-hypoteesi

- Suodata muokattu_kertaa > 0
  → uutiset joiden otsikko on vaihtunut

---

## Asennusohjeet

### Tarvittavat tilit ja avaimet
1. GitHub-tili (ilmainen) — github.com
2. Google-tili — sheets.google.com
3. Anthropic API-avain — console.anthropic.com (~1-2€/kk)
4. Ylen API-avain (ilmainen) — tunnus.yle.fi/api-avaimet

### GitHub Secrets
Lisää repositoryn Settings → Secrets → Actions:
- ANTHROPIC_API_KEY
- YLE_APP_ID
- YLE_APP_KEY
- GOOGLE_SHEET_ID
- GOOGLE_CREDENTIALS_JSON

### Google Service Account
1. Luo projekti: console.cloud.google.com
2. Ota käyttöön: Google Sheets API ja Google Drive API
3. Luo Service Account → lataa JSON-avain
4. Jaa Google Sheet service accountin sähköpostille (Editor)

### Tiedostot GitHubissa
- scraper.py — pääskripti
- requirements.txt — Python-kirjastot
- .github/workflows/seuranta.yml — ajastus (joka tunti)

### Testaus
Actions-välilehti → Run workflow → seuraa lokia

---

## Kustannukset

Batch API + lyhyt prompt:
- Päivä: ~2-5 senttiä
- Kuukausi: ~1-2€
- $5 saldo kestää useita kuukausia
