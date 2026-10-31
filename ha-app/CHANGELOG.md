# Muutokset

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
