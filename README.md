# Yle-uutisseuranta

Automaattinen järjestelmä joka seuraa Yle.fi:n etusivua ympäri vuorokauden ja analysoi, mitkä uutiset nostetaan esille, kuinka kauan ne pysyvät näkyvissä, ja mitä uutisia julkaistaan mutta ei koskaan nosteta etusivulle.

---

## Miten se toimii

Botti herää kerran tunnissa (GitHub Actions), skannaa Ylen etusivun, kategorisoi uutiset tekoälyllä ja tallentaa kaiken Google Sheetsiin. Data kertyy automaattisesti.

### Kaksivaiheinen analyysi

**Vaihe 1 — otsikon perusteella (kaikki uutiset)**
Claude analysoi jokaisen uutisen otsikon ja merkitsee sille tagit kolmesta ryhmästä: aihe, kehystys ja poliittinen signaali. Käyttää Batch API:a — 50% halvempi kuin yksittäiset kutsut.

**Vaihe 2 — artikkelin perusteella (vain herkkä sisältö)**
Jos vaihe 1 tunnistaa poliittisesti herkän uutisen, botti lukee koko artikkelin ja täydentää analyysiä tarkemmilla tiedoilla: onko tekijän tausta mainittu, ovatko lähteet monipuolisia, mihin suuntaan painotus kallistuu.

---

## Tagit

### Aihe — mistä uutinen kertoo
`maahanmuutto` `rikos` `vakivalta` `seksuaalirikos` `terrorismi` `politiikka-hallitus` `politiikka-oppositio` `talous` `tyollisyys` `ilmastonmuutos` `energia` `terveys` `koulutus` `urheilu` `kulttuuri` `ulkomaat` `onnettomuus` `oikeus`

### Kehystys — miten uutinen on kirjoitettu
`savy-positiivinen` `savy-negatiivinen` `savy-neutraali` `tekija-maahanmuuttaja` `tekija-kantasuomalainen` `tekija-tuntematon` `kohde-nainen` `kohde-mies` `kohde-lapsi` `kohde-virkavalta` `poliitikko-vasemmisto` `poliitikko-oikeisto` `poliitikko-kepu` `poliitikko-aarioikeisto` `poliitikko-äärivasemmisto` `tilasto` `yksittaistapaus`

### Vasemmistolle epäedullinen — projektin ydinkysymys
`maahanmuuttaja-rikoksentekija` `turvapaikanhakija-ongelmat` `ps-tai-oikeisto-onnistuu` `vasemmisto-tai-vihrea-epaonnistuu` `ydinvoima-positiivinen` `trans-kriittinen` `anti-nato` `anti-eu` `pro-israel` `anti-palestiina`

### Aihehenkilöt (tekstihaku otsikosta — ei AI:ta)
Poimitaan suoraan otsikosta, 100% luotettava:

**Geopolitiikka:** `nato` `israel` `palestiina` `gaza` `trump` `biden` `demokratit` `republikaanit` `ukraina` `venäjä` `putin` `kiina` `eu` `yhdysvallat`

**Maahanmuutto:** `turvapaikanhakija` `pakolainen` `maahanmuuttaja` `käännytys` `oleskelulupa`

**Puolueet ja poliitikot:** `kokoomus` `sdp` `perussuomalaiset` `keskusta` `vihreät` `vasemmistoliitto` `rkp` `kd` `orpo` `lindtman` `purra` `haavisto`

**Identiteetti:** `seta` `trans` `transsukupuoli` `hlbtq` `pride`

### Vaihe 2 lisätagit — artikkelista
`tausta-mainittu` `tausta-ei-mainittu` `tekija-aarioikeisto` `tekija-äärivasemmisto` `lahde-vain-viranomainen` `lahde-monipuolinen` `painotus-oikeistokriittinen` `painotus-vasemmistokriittinen` `integraatio-epaonnistuminen` `islam-ongelmat-suomessa` `rinnakkaisyhteiskunta` `maahanmuuton-kustannukset` `ilmastopolitiikka-kustannukset` `vihrea-siirtyman-ongelmat` `anti-sateenkaari` `anti-transideologia` `anti-islam`

---

## Google Sheets

### Raakadata-välilehti
Jokainen skannaus omana rivinään — koskematon arkisto. Sama uutinen voi esiintyä useita kertoja eri skannauksista.

### Uutiskortti-välilehti
Yksi rivi per uutinen. Päivittyy automaattisesti joka skannauksen yhteydessä.

| Sarake | Selitys |
|--------|---------|
| `ensimmainen_etusivu` | Milloin uutinen ensin havaittiin etusivulla |
| `viimeinen_etusivu` | Milloin viimeksi havaittiin |
| `nakyvyys_tunnit` | Kuinka kauan etusivulla yhteensä |
| `paras_sijainti` | Korkein sijainti (1 = ylimpänä) |
| `osiot_joissa_nahty` | paasivu / suosituimmat / tuoreimmat / lyhyesti |
| `etusivulla_koskaan` | kyllä / ei |
| `julkaistu_ei_nostettu` | Julkaistu mutta ei koskaan etusivulle |
| `vaihe2_tehty` | Onko artikkeli luettu tarkemmin |
| `vaihe2_lisatagit` | Artikkelista saadut lisätagit |
| `tarkistamatta` | kyllä = kannattaa tarkistaa manuaalisesti |
| `otsikko_alkuperainen` | Ensimmäinen otsikko — ei muutu koskaan |
| `muutoshistoria` | Kaikki otsikkomuutokset aikaleimoineen ja tagimuutoksineen |
| `tagit_aihehenkilot` | Otsikosta poimitut aihehenkilöt ja teemat (tekstihaku) |
| `mahdollinen_liveuutinen` | kyllä = julkaistu yli 7 pv sitten mutta päivitetty äskettäin |
| `viimeisin_paivitys` | Milloin artikkelia viimeksi muokattu |
| `paivitysviive_pv` | Päiviä julkaisusta viimeisimpään päivitykseen |

### Tilastot-välilehti
Päivittyy automaattisesti joka ajon yhteydessä. Sisältää:
- Yleiskatsaus (uutisten määrät, live-uutiset, otsikkomuutokset)
- Näkyvyysajat kategorioittain
- Ilta-hypoteesi: julkaisuikkunajakauma epäedulliset vs. muut
- Suosituimmat vs. pääsivu -ristiriita
- Lista muutetuista otsikoista

---

## Aikaikkunat

| Aikaikkuna | Kellonaika | Huomio |
|------------|------------|--------|
| aamupiikki | 06–09 | Korkein lukijakunta |
| tyopaiva | 09–16 | Tasainen virta |
| iltapaivapiikki | 16–18 | Toinen piikki |
| ilta | 18–22 | Hypoteesi: herkkiä uutisia julkaistaan tähän aikaan |
| yo | 22–06 | Minimaalinen lukijakunta |

---

## Hypoteesit joita testataan

1. **Nopea katoaminen** — Katoavatko vasemmistolle epäedulliset uutiset etusivulta nopeammin kuin muut vastaavan kategorian uutiset?

2. **Ilta-julkaisu** — Julkaistaanko herkkiä uutisia systemaattisesti illalla jotta ne katoavat uutisvirrasta ennen aamuruuhkaa?

3. **Piilottaminen** — Onko uutisia jotka on julkaistu mutta ei koskaan nostettu etusivulle? (`julkaistu_ei_nostettu = kyllä`)

4. **Otsikkomuutokset** — Muutetaanko uutisten otsikoita jälkikäteen tavalla joka pehmentää sisältöä? (`muutoshistoria`)

---

## Hyödyllisiä suodatuksia Sheetsissä

- `julkaistu_ei_nostettu = kyllä` → julkaistut mutta piilotetut uutiset
- `tagit_vasemmisto_epäedullinen` ei tyhjä → vertaa nakyvyys_tunnit muihin
- `julkaisuikkuna = ilta` + `etusivulla_koskaan = ei` → testaa ilta-hypoteesi
- `muokattu_kertaa > 0` → uutiset joiden otsikko on vaihtunut

---

## Asennus

### Vaatimukset
- GitHub-tili — [github.com](https://github.com)
- Google-tili — [sheets.google.com](https://sheets.google.com)
- Anthropic API-avain — [console.anthropic.com](https://console.anthropic.com) (~1–2 €/kk)
- Ylen API-avain (ilmainen) — [tunnus.yle.fi](https://tunnus.yle.fi)

### 1. Google Sheets ja Service Account

1. Luodaan uusi Google Sheet ja kopioidaan sen ID URL:sta
2. Mennään [console.cloud.google.com](https://console.cloud.google.com)
3. Luodaan uusi projekti
4. Otetaan käyttöön: **Google Sheets API** ja **Google Drive API**
5. Luodaan **Service Account** → ladataan JSON-avain
6. Jaetaan Google Sheet service accountin sähköpostille (Editor-oikeus)

### 2. GitHub Secrets

Mennään repositoryn **Settings → Secrets and variables → Actions** ja lisätään:

| Nimi | Arvo |
|------|------|
| `ANTHROPIC_API_KEY` | Claude API -avain (alkaa sk-ant-...) |
| `YLE_APP_ID` | Ylen API app_id |
| `YLE_APP_KEY` | Ylen API app_key |
| `GOOGLE_SHEET_ID` | Sheet ID URL:sta |
| `GOOGLE_CREDENTIALS_JSON` | Koko JSON-tiedoston sisältö |

### 3. Koodi GitHubiin

Ladataan repositoryyn:
- `scraper.py`
- `requirements.txt`
- `.github/workflows/seuranta.yml`

### 4. Testaus

**Actions** → **Yle-uutisseuranta** → **Run workflow**

Onnistuneen ajon jälkeen Google Sheetsiin ilmestyy dataa ja botti pyörii automaattisesti joka tunti.

---

## Kustannukset

Batch API + optimoitu prompt pitää kulut minimissä:

| Ajanjakso | Arvio |
|-----------|-------|
| Päivä | 2–5 senttiä |
| Kuukausi | 1–2 € |
| Vuosi | 12–24 € |

$5 alkusaldo riittää useiksi kuukausiksi normaalikäytössä.

---

## Tulevaisuuden featuret

Nämä ominaisuudet lisätään kun perusjärjestelmä on toiminut vakaasti noin kuukauden ja tiedetään paljonko riskiuutisia oikeasti tunnistetaan päivässä.

### Artikkelin sisältömuutosten seuranta

Tällä hetkellä botti seuraa otsikkomuutoksia mutta ei artikkelin sisältöä. Yle muuttaa joskus artikkelin sisältöä jälkikäteen — esimerkiksi vaihtaa "äärioikeisto" → "oikeisto" tai poistaa arkaluonteisia yksityiskohtia.

**Suunniteltu toteutus:**
1. Vaihe 2 (artikkelin luku) vahvistaa uutisen riskitason
2. Vahvistetuille riskiuutisille lasketaan tiivistetty sisältöhash
3. Joka skannaus tarkistetaan onko hash muuttunut
4. Jos muuttui → artikkeli luetaan uudelleen ja tagit päivitetään
5. Muutoshistoria tallentuu sarakkeeseen `sisalto_muutoshistoria`

**Miksi ei vielä:** Tarvitaan ensin data siitä kuinka monta riskiuutista tunnistetaan päivässä, jotta voidaan arvioida kustannusvaikutus luotettavasti.

### Testirajoituksen poisto

Koodissa on `MAX_UUTISET_PER_AJO = 10` joka rajoittaa kategorisointia testivaiheessa. Kun järjestelmä toimii vakaasti, vaihdetaan arvoksi `None`:

```python
MAX_UUTISET_PER_AJO = None
```

### Parempi osiotunnistus

Tällä hetkellä Ylen uutisten kategoria (Kotimaa, Politiikka, Talous jne.) tunnistetaan vain otsikosta. Ylen sivusto lataa sisällön JavaScriptillä eikä kategoriaa saa suoraan HTML:stä tai RSS:stä. Jos Yle avaa tähän paremman API:n, voidaan aihetagi hakea suoraan Yleltä Claude-kategorisointia käyttämättä — säästäisi tokeneita.

### Symmetrinen poliittinen analyysi

Tällä hetkellä ryhmä 3 tunnistaa vain vasemmistolle epäedulliset uutiset. Jotta voidaan verrata paljonko Yle julkaisee vasemmistoa suosivia vs. oikeistoa suosivia uutisia, tarvitaan symmetrinen vastinpari — ryhmä 4: "Oikeistolle epäedullinen".

Suunnitellut tagit:
`oikeisto-tai-ps-epaonnistuu` `vasemmisto-tai-vihrea-onnistuu` `maahanmuutto-positiivinen` `ilmastotoimet-positiivinen` `trans-myonteinen` `pro-eu` `pro-nato` `ydinvoima-kriittinen` `anti-israel` `pro-palestiina`

Tämän avulla voidaan tehdä aito vertailu:
- Vasemmistoa suosivat vs. oikeistoa suosivat uutiset — lukumäärä ja näkyvyysajat
- Julkaisuikkunavertailu molemmille ryhmille
- Etusivulle pääsyprosentti kategorioittain

Lisätään kun ryhmä 3 on todettu toimivaksi ja luotettavaksi.

### Avainsanalista kategorisointiin

Tällä hetkellä AI kategorisoi uutiset pelkän otsikon perusteella ja tekee virheitä epäsuorilla otsikoilla (esim. "Suomen vanhin kunnanjohtaja" → kulttuuri vaikka pitäisi olla politiikka-hallitus).

Ratkaisu: erillinen avainsanalista jota voi päivittää käsin. Esimerkiksi:
- `kunnanjohtaja, kaupunginjohtaja, valtuusto, kunnanhallitus` → politiikka-hallitus
- `turvapaikka, käännytys, oleskelulupa` → maahanmuutto

Lista toimisi tekstihaulla ennen AI-kategorisointia — luotettava ja helppo ylläpitää.

### Pivot-tilastot

Google Sheetsiin voidaan lisätä automaattiset pivot-taulut jotka laskevat:
- Keskimääräinen näkyvyysaika kategorioittain
- Julkaisuikkunajakauma vasemmistolle epäedullisilla vs. muilla uutisilla
- Etusivulle pääsyprosentti kategorioittain
