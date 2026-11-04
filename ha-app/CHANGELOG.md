# Muutokset

## 0.4.44

- Hintarajojen (Halpa/Kallis hinta, Hintaraja 1 ja 2) selitys ja yksikkö kertovat nyt, mitä hintaa verrataan: pörssihinta ilman siirtoa, veroja ja marginaalia, ja ALV mukana vain jos "Lisää ALV sähkönhintaan" on päällä (yksikössä lukee "ALV 25,5 % mukana" tai "ilman ALV:ta"). Laskenta ei muuttunut: rajat olivat jo ALV:llisia ALV-kytkimen ollessa päällä, joten vanhoja arvoja ei tarvitse muuttaa. Laitesääntöjen "Hinta enintään" -selitys täsmennetty samoin.

## 0.4.43

- Historian Tulos-tekstit ovat nyt kokonaan suomeksi (esim. "tavoite vanheni", "Kammari 1000W patteri: säätö käsin"), laitteista näkyy nimi eikä entiteetin tunnus, ja "muuttaja"-toisto on poistettu. Vanhat tapahtumat käännetään näytettäessä. Mökin integraatio 0.7.3: päivitä install.sh:lla, jotta uudet syyt tulevat suomeksi.

## 0.4.42

- Korjattu: Käsiohjauksen aikana mökin Tavoite ja historian Voimassa oleva tavoite -viiva näyttävät nyt käsin asetetun lämpötilan eivätkä "–" tai vanhaa arvoa. Mökin integraatio 0.7.2: päivitä install.sh:lla, jotta historia tallentaa arvon.

## 0.4.41

- ALPSTUGA-anturien kosteus, lämpötila, CO₂ ja PM2,5 näkyvät nyt myös Laitteet-korteilla ja historiassa, ei vain huonekortilla.

## 0.4.40

- Laitekorttien englanninkieliset nimet ja tilat näkyvät suomeksi (esim. "Kammari 1000W lämpötila", "Ilmanlaatu", "Hyvä", "Latausnopeus").

## 0.4.39

- Korjattu: puhelimella "Käsiohjaus päättyy klo ..." -teksti ei enää leikkaudu; Palauta automaatio -nappi on tekstin alla. Pitkät laitenimet rivittyvät.

## 0.4.38

- Korjattu: varoitus "ei ehdi lämmetä ajoissa" tuli turhaan noin puoli tuntia jokaisen mökin esilämmityksen alussa.
- Vierailua voi muuttaa myös mökillä ollessa, ja päättyneen vierailun voi aloittaa uudelleen paikan päällä. Vierailun aikana muutokset tallentuvat itsestään.
- Mökin energia-arvio ei enää liioittele turvalämmön pitoa.
- Kosteusvahdin vuorokausi säilyy uudelleenkäynnistyksen ja lyhyen katkon yli. Tuuletusmuistutus ei toistu lyhyen yhteyskatkon jälkeen.
- Mökin huonekortit päivittyvät heti, kun vierailu alkaa tai muuttuu. Kortissa on yksi Tavoite-rivi; hinta ja mökin kuittaus ovat sen vihjeessä.
- Mökin yleiskuva näyttää kylmimmän huoneen ja vierailun. Sivun vaihto alkaa ylhäältä.
- Historia: aika-akselilla tasatunnit, yksi selite ryhmää kohden ja puhelimeen sopiva energiataulukko. Päivä-näkymässä ei ole energiataulukkoa.
- Asetukset: muutettu asetus näyttää "Tallennettu" omalla rivillään, ja desimaalit kirjoitetaan pilkulla. Pörssihinta on molemmilla sivuilla ohjausasetusten jälkeen.
- Mökin Vähimmäislämpötila-asetus poistui: Perustaso on myös optimoidun lämmityksen alaraja.
- Hae hinnat -nappi näkyy vain, kun hinnat ovat myöhässä. Opittujen nopeuksien Aloita alusta on Lisää-kohdan takana.
- Testi-ilmoitus kertoo, mihin puhelimiin se lähti, ja miksi, jos se ei lähtenyt. Mökin asetuksissa näkyy, onko mökin oma hälytys valmiina.
- Seinänäkymä: kosteus- ja CO₂-varoitus ilmoitusten rajoilla, halvin tunti kuten yleiskuvassa, oma koko näytön asennus ja "Ei yhteyttä" myös ensimmäisellä latauksella.
- Hinnat ovat kaikkialla snt/kWh, ja varalämpö on kaikkialla turvalämpö.
- Mökin integraatio 0.7.1: mökin oma hälytys yrittää uudelleen, kunnes jokin puhelin saa sen, ja kertoo, mitä mökki tekee. Päivitä ajamalla install.sh mökin Home Assistantissa.

## 0.4.37

- Mökin oma hälytys: Villa Kuppis kertoo mökin Home Assistantille viiden minuutin välein olevansa käynnissä. Jos viestejä ei tule tuntiin (kotipalvelin tai yhteys on poikki), mökin Home Assistant lähettää itse ilmoituksen "Villa Kuppis ei vastaa" ja ilmoittaa alle 3 °C:n huoneesta. Vaatii mökin integraation 0.7.0 (aja install.sh mökin Home Assistantissa) ja että puhelimen Home Assistant -sovellus on yhdistetty myös mökin Home Assistantiin.

## 0.4.36

- Historian energiataulukko näyttää kulutuksen ja kustannuksen. Lämpöpumppujen kilowattitunnit tulevat MELCloud Home -integraation kuukausilaskureista tunneittain. Mökin huoneiden luvut ovat arvio: patterin nimessä oleva teho kertaa aika, jonka patteri on lämmittänyt. Kustannus lasketaan pörssihinnasta, ALV:stä ja Siirto, vero ja marginaali -asetuksesta. Taulukon lopussa on yhteissumma ja keskihinta.
- Mökkivierailulle voi antaa lähtöajan, jolloin vierailu päättyy itsestään ja huoneet palaavat hintaohjaukseen.
- Vierailukortti näyttää lämmityksen aikana, kuinka nopeasti huone lämpenee ja milloin se on arviolta tavoitteessa. Jos huone ei ehdi tavoitteeseen saapumiseen mennessä, rivi muuttuu oranssiksi.
- Uudet ilmoitukset, kukin omalla kytkimellään: mökki on lämmin, mökki ei ehdi lämmetä ajoissa, ja muistutus vierailusta, joka on ollut päällä kolme vuorokautta ilman lähtöaikaa.
- Uusi asetus Koti Poissa-tilaan vierailun ajaksi (oletuksena pois): kodin Poissa-tila kytkeytyy päälle saapumisaikana ja pois, kun vierailu päättyy.
- Kosteusvahti: ilmoitus, kun mökin huoneen ilmankosteus on ollut vuorokauden yli kosteusrajan (oletus 80 %). Valinnainen Lämmitä kosteaa huonetta -asetus pitää kostean huoneen 2 °C perustasoa lämpimämpänä, kunnes kosteus laskee.
- Vierailun aikana tulee tuuletusmuistutus, kun huoneen hiilidioksidi on ollut 10 minuuttia yli 1 200 ppm.
- Seinänäkymä tabletille: iso, vain luettava näkymä kodista, mökistä ja seuraavien 24 tunnin hinnoista. Avaa se kohdasta Asetukset > Käyttöliittymä > Seinänäkymä tabletille.

## 0.4.35

- Uusi asetus Siirto, vero ja marginaali (c/kWh) Pörssihinta-ryhmässä, erikseen kodille ja mökille: mitä maksat pörssihinnan päälle jokaisesta kilowattitunnista. Optimoitu ohjaus ja energiakustannukset käyttävät kokonaishintaa. Hintakäyrä ja hintarajat pysyvät pörssihinnassa.
- Mökin hintarajojen oletukset lämmittävät varastoon vain negatiivisella pörssihinnalla (alle 0 snt: 8 °C, alle −2 snt: 12 °C). Itse asetetut rajat pysyvät ennallaan.
- Ilmoitukset-asetusten lopussa on Lähetä testi-ilmoitus -painike.
- Jos automaatio käynnistää lämpöpumpun ja se lähtee muuhun kuin jäähdytystilaan (tilaa on vaihdettu pumpun ollessa pois), automaatio sammuttaa sen heti.
- Huoneen asetuksissa ei enää näytetä asetuksia, jotka koskevat koko kohdetta.

## 0.4.34

- Mökin huone ei enää pääse jäätymään pois kytketyn patterin tai käsiohjauksen takia: kun huone on alle turvalämmön (5 °C) tai sen lämpötilaa ei saada, pois kytketyt ja käsin pois käännetyt patterit pitävät turvalämmön. Vaatii mökin integraation 0.6.0 (aja install.sh mökin Home Assistantissa).
- Mökin huoneen kortissa on Palauta automaatio -painike, kun huone on käsiohjauksessa.
- Huoneen oppiminen laskee lämmitykseksi vain ajan, jolloin patterit oikeasti lämmittävät, ja alkaa alusta, kun huoneen anturi tai patteri vaihtuu. Opittujen nopeuksien kortissa on Aloita oppiminen alusta -painike: käytä sitä, kun patterit on siirretty mökille.
- Opitun mallin testi vaatii, että huone on testijaksolla lämmennyt pattereilla ja jäähtynyt ilman niitä. Pelkkä tavoitteen pitäminen ei enää riitä hyväksyntään.
- Automaation kytkeminen pois vapauttaa mökin huoneet myös Villa Kuppisin uudelleenkäynnistyksen jälkeen.
- Optimoitu lämmitys ei laske huonetta perustason alle. Mökkikäynnin esilämmitys käyttää opittua mallia vain Optimoitu-tilassa ja varaa lämpenemiseen lisäaikaa.
- Uusi ilmoitus, kun mökin lämpötila-anturilta ei ole saatu lukemaa tuntiin tai patteri ei ole vastannut 30 minuuttiin. Kylmähälytys ei käytä vanhentunutta lukemaa.

## 0.4.33

- Mökin patterit pitävät oman historiansa, kun huoneeseen tulee uusi patteri tai patterien järjestys Home Assistantissa muuttuu. Aiemmin patterit numeroitiin uudelleen ja asetushistoria saattoi siirtyä väärän patterin nimelle.

## 0.4.32

- Kodin huoneet nimetään Home Assistantin alueiden mukaan: lämpöpumpun huone on sen alueen nimi (esim. Makuuhuone-pumppu näkyy nyt Iso huone -huoneena). Kun alueen nimeää uudelleen tai pumpun siirtää toiselle alueelle Home Assistantissa, nimi vaihtuu heti. Huoneiden historia ja asetukset säilyvät.

## 0.4.31

- ALV-asetus on nyt yhteinen kodille ja mökille, joten molempien sivujen hintakäyrät näyttävät samat hinnat. Kodin aiempi asetus (ALV päällä) koskee nyt myös mökkiä, ja mökin halpa/kallis-rajoja verrataan ALV:llisiin hintoihin.

## 0.4.30

- Mökin huoneet ovat nyt Mökki ja Kammari, samoin kuin mökin Home Assistantin alueet (ennen Iso huone ja Pieni huone). Huoneiden historia säilyy.

## 0.4.29

- Mökin huoneelle voi valita toisen lämpöanturin: napauta anturia Mökki-sivun Laitteet-osiossa ja valitse huone kohdasta Mökin huoneen lämpötilana. Huoneen kortilla Palauta oletus palauttaa alkuperäisen anturin.
- Mökin huoneeseen voi liittää Home Assistantin alueen: alueen patterit liittyvät huoneeseen ja lämmitetään sen mukana, kun laite siirretään alueelle Home Assistantissa. Vaatii mökin integraation version 0.5.0 (aja install.sh mökin Home Assistantissa) ja huoneelle `area:`-rivin mökin asetuksiin.

## 0.4.28

- Lämpöpumpun huonelämpötilaksi voi valita erillisen lämpöanturin: napauta anturia Laitteet-osiossa ja valitse pumppu kohdasta Pumpun huonelämpötilana. Pumpun ohjaus ja suunnitelma käyttävät anturin lukemaa aina kun anturi on tavoitettavissa, muuten pumpun omaa.
- Kun uusi anturi tai laite tulee Laitteet-osioon, sen historia täydennetään Home Assistantin tilastoista enintään 30 päivän ajalta tuntikeskiarvoina.

## 0.4.27

- Säännöt Laitteet-osiossa: laite päälle aikavälillä (kellonaika tai auringonnousu/-lasku siirrolla, esimerkiksi jouluvalot auringonlaskusta klo 23:een), laite päälle sähkön halvimpina 15 minuutin jaksoina valitulla ikkunalla, tai toiminnot kun jotain tapahtuu (hetki, anturin raja, laite päälle tai pois, joku tulee kotiin tai viimeinen lähtee) ja valinnaiset ehdot aikavälistä ja hintarajasta.
- Säännöt odottavat, kun kohteen Automaatio on pois päältä. Aikaväli- ja halvimpien tuntien säännöt muuttavat laitetta vain muutoshetkellä, joten käsin tehty muutos pysyy seuraavaan muutokseen asti. Sääntöjen toiminnot näkyvät Tapahtumissa.

## 0.4.26

- Uusi Laitteet-osio Koti- ja Mökki-sivuilla: kaikki Home Assistantissa johonkin alueeseen (huoneeseen) lisätyt laitteet näkyvät huoneittain ilman sovelluspäivitystä. Uudet laitteet saavat Uusi-merkin.
- Valot, pistorasiat ja kytkimet kytketään liukukytkimellä; himmennettävillä valoilla on kirkkaus ja värilämpötila, verhoilla ja tuulettimilla omat säätimet, tilanteilla ja skripteillä Käynnistä-painike. Huoneen kaikki valot saa pois yhdellä painalluksella.
- Laitetta napauttamalla näkyy sen historia (päivä, viikko, kuukausi, vuosi), nimen voi vaihtaa ja laitteen piilottaa. Järjestä-kytkimellä huoneita ja laitteita voi siirtää ja piilotettuja palauttaa.
- Home Assistantin automaatiot näkyvät listana, ja ne voi kytkeä päälle ja pois.
- Anturien arvot ja laitteiden päälle/pois-tila tallentuvat historiaan; muutokset näkyvät Tapahtumissa.

## 0.4.25

- Mökin Automaatio-kytkin toimii: pois päältä mökille ei lähetetä tavoitteita, ja patterit siirtyvät turvalämpöön noin minuutissa. Sivulla näkyy siitä ilmoitus.
- Jokaisen mökin patterin voi kytkeä pois käytöstä ja takaisin omalla kytkimellään. Pois kytketty patteri pidetään pois päältä, mutta turvalämpö käyttää sitä silti jäätymisen estoon. Muutos näkyy Tapahtumissa. Vaatii mökin integraation version 0.4.0 (aja install.sh mökin Home Assistantissa).

## 0.4.24

- Uusi ulkoasu: sivupalkki (puhelimessa alapalkki), tervehdys ja yleiskatsaus, jossa sähkön hinta nyt ja pylväinä tänään ja huomenna, ulko- ja sisälämpötila sekä pumput.
- Pumppujen ja mökin huoneiden lämpötila näkyy mittarina, jossa piste on tavoite.
- Historiassa sähkön hinta on pylväinä, ajanjakso valitaan painikkeista ja käyriä voi piilottaa selitteestä.
- Päivän suunnitelma näkyy aikajanana, opitut nopeudet tiivistettyinä lukuina.
- Asetukset ja Tapahtumat voi pienentää; valinta muistetaan selaimessa.

## 0.4.23

- Sovellus toimii myös osoitteessa /villa-kuppis (uusi asetus public_path), myös ilman loppukauttaviivaa.

## 0.4.22

- Kodilla ja Mökillä on omat sivunsa ja kummallakin oma Automaatio-kytkin. Mökin kytkin seuraa aluksi kodin kytkintä.
- Otsikko on nyt Villa Kuppis.
- Sammutetun pumpun tavoitelämpötilan voi taas asettaa: tila haetaan pumpun historiasta, koska Home Assistant ei kerro sammutetun pumpun tilaa.
- Mökin lukemat eivät enää näy vanhentuneina, kun arvo pysyy samana. Vaatii mökin integraation version 0.3.1.

## 0.4.21

- Mökin huoneet saavat tavoitteen mukana koko tunnetun suunnitelman (myös mökkikäynnin lämmityksen). Jos yhteys kotiin katkeaa, mökki noudattaa suunnitelmaa loppuun asti ja vasta sitten siirtyy 5 °C:n varalämpöön. Vaatii mökin integraation version 0.3.0.

## 0.4.20

- Mökkikäynnin lämmitys suunnitellaan opituille huoneille niin, että huone on lämmin juuri saapuessa ja halvemmalla kuin 12 tunnin säännöllä. Muut huoneet käyttävät edelleen 12 tunnin sääntöä.

## 0.4.19

- Mökin lämmitykselle uusi asetus Hintaohjaus: Kiinteät rajat tai Optimoitu (oletus). Optimoitu varaa lämpöä halvoilla tunneilla vähimmäislämpötilan ja suurimman varaavan lämpötilan välissä.
- Optimointi alkaa huonekohtaisesti, kun huoneen nopeudet on opittu ja testipäivä on hyväksytty; siihen asti käytetään kiinteitä rajoja.

## 0.4.18

- Mökin huoneet oppivat lämpenemis- ja jäähtymisnopeutensa lukemista; ne näkyvät kodin pumppujen kanssa kohdassa Huoneiden opitut nopeudet. Tämä on ensimmäinen vaihe mökin älykkäästä ylläpitolämmityksestä.

## 0.4.17

- Mökin lämmitysasetusten selitykset kertovat, mitä asetukset nyt tekevät.
- Älykäs ylläpitolämmitys on piilotettu, kunnes se on valmis.

## 0.4.16

- Jos kaikki pumput ovat 10 minuuttia poissa käytöstä mutta kodin Home Assistant vastaa, MELCloud Home -integraatio ladataan uudelleen, jolloin se kirjautuu uudelleen (enintään kerran tunnissa). Kytkin asetuksissa.

## 0.4.15

- Puhelinilmoitukset kodin Home Assistantin kautta: mökin huone on kylmä, mökki ei vastaa 30 minuuttiin tai kodin pumppu ei toimi 30 minuuttiin. Jokaisella ilmoituksella on oma kytkin asetuksissa.

## 0.4.14

- Pumppujen tila luetaan Home Assistantista 10 sekunnin välein minuutin sijaan.
- Sivu päivittyy heti, kun se avataan uudelleen, eikä päivitä piilossa ollessaan.

## 0.4.13

- Jos pumppu ei ole käytettävissä, kortti näyttää merkinnän Ei käytettävissä, tuntemattomat arvot viivana eikä ohjaimia voi käyttää.
- Pumpun ilmoittama laitevirhe näkyy kortissa.

## 0.4.12

- Varmuuskopio kattaa nyt myös laiteliitokset ja mökkikäynnit.
- Vanhat varmuuskopiot poistetaan: tallessa pysyvät 14 viimeistä päivää ja kuukauden ensimmäinen puolen vuoden ajalta.

## 0.4.11

- Mökin laitteet sidotaan automaattisesti mökin Home Assistantin ohjaimen tiedoista, joten oikeisiin laitteisiin vaihtaminen ei vaadi muutoksia Villa Kuppisiin.

## 0.4.10

- Erillinen tavoitelämpötila-arvo poistettu, koska lista näyttää sen.
- Jos tavoitelämpötilan (tai tilan, puhalluksen) muutos epäonnistuu, lista palaa edelliseen arvoon.

## 0.4.9

- Tavoitelämpötilan − ja + -painikkeet poistettu, lämpötila valitaan listasta.

## 0.4.8

- Sovelluksen versio näkyy yläpalkissa.
- Päivityksillä on nyt muutosloki.

## 0.4.7

- Pois päältä olevaa pumppua ei enää käynnistetä tilan vaihdolla.

## 0.4.6

- Tila- ja puhallinlistat aukeavat napautuksella eivätkä lähetä mitään tai sulkeudu päivityksessä.

## 0.4.5

- Pumppukomennon tulos ja kesto kirjataan lokiin.

## 0.4.4

- Puhaltimien nimet vastaavat MELCloud Home -integraatiota; tilakohtaiset lämpötila-alueet säilyvät.

## 0.4.3

- Kodin viilennys yksinkertaistui: yksi ohjain per pumppu ja käsin tehty muutos keskeyttää automaation hetkeksi.
- Asetukset yhdistettiin; vanhat asetukset siirtyvät automaattisesti.

## 0.4.2

- Pumppujen vertailutarkistus Home Assistantin kanssa (vain luku).
- Pois päältä olevan pumpun viimeisin tila muistetaan.

## 0.4.1

- Mökin varalämpötila on 5 °C.
- Valintalistat pysyvät auki päivityksen aikana.

## 0.4.0

- Villa Kuppis toimii Home Assistant -sovelluksena.
