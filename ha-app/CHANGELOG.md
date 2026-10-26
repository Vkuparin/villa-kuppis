# Muutokset

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
