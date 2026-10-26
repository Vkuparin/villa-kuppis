# Käyttöliittymän alustava suunnitelma

Käyttöliittymän kieli on suomi. Sovellusta käytetään selaimella tietokoneella ja puhelimella; puhelimeen sen voi asentaa kotinäytölle (PWA). Erillistä Android-sovellusta ei ole. Kodin ja mökin laitteet tulevat kummankin kohteen Home Assistantin kautta, mutta käyttäjän ei tarvitse tietää siitä.

## Näkymät

- **Yleiskuva:** kohteet `Koti` ja `Mökki`, huoneet, sisä- ja ulkolämpötilat, yhteyden tila, tämän päivän sähkön hinta ja seuraava suunniteltu muutos.
- **Kohteen yhteys:** `Yhteydessä`, `Yhteys poikki — paikallinen ylläpitolämmitys käytössä`, `Tiedot vanhentuneet` ja viimeisimmän onnistuneen tiedon aika.
- **Laite:** mitattu lämpötila, tavoitelämpötila, toimintatila, laitteen tukemat säädöt ja viimeisin vahvistettu muutos.
- **Kuvaajat:** lämpötilat, ilmanlaatu (ALPSTUGA), tavoitelämpötilat ja hinnat samalla aika-akselilla, huoneittain ja kohteittain. Painikkeet `←`, `Nyt` ja `→` siirtävät ikkunaa jakson puolikkaan verran, ja ikkuna voi ulottua 48 tuntia eteenpäin: tulevat hinnat ja FMI:n sääennuste (`Sääennuste`) näkyvät varjostetulla alueella, ja `nyt`-viiva erottaa ne. Hiiren vienti tai kosketus näyttää tooltipissä kyseisen hetken arvot ja hinnan.
- **Yläpalkki:** kytkin `Automaatio` kytkee koko hinta- ja lämpötilaohjatun automaation päälle tai pois. Pois päältä ei lähetetä pumpuille komentoja, mutta pumput jäävät siihen tilaan, jossa ne ovat.
- **Pörssisähkö:** päivän ja huomisen hinnat 15 minuutin jaksoissa (haetaan päivittäin klo 14.25 ja painikkeella `Hae hinnat`) sekä suunnitelma: mikä tavoitelämpötila on voimassa kussakin jaksossa ja miksi.
- **Hintasäännöt:** kiinteät hintarajat, esimerkiksi `Kun pörssihinta on alle 5 c/kWh → 12 °C`, tai `Hintaohjaus: Optimoitu` (vähimmäislämpötila ja suurin varaava lämpötila). Asetukset koko kohteelle tai huoneittain. Hinta on pörssihinta ilman marginaaleja; ALV lisätään asetuksella `Lisää ALV sähkönhintaan` (25,5 %), ja silloin hintarajat annetaan ALV:llisina. Kotona säännöt koskevat kesällä jäähdytystä.
- **Mökki:** `Vierailu` kysyy saapumisajan ja tavoitelämpötilan, ja sovellus lämmittää halvimmilla jaksoilla ajoissa. `Lopeta vierailu` palauttaa ylläpitolämmityksen.
- **Koti:** painike `Poissa kotoa` vaihtaa kodin poissaoloasetukseen (oletuksena jäähdytys vasta yli 28 °C:ssa). `Yötila`: valituissa huoneissa hiljaisin puhallus yöaikaan.
- **Tapahtumat:** kuka tai mikä muutti asetusta, milloin ja onnistuiko muutos. Kapealla näytöllä jokainen tapahtuma on oma korttinsa.
- **Asetukset:** laitteet, kohteet, käyttäjät, yhteydet ja kaikki ohjauksen asetukset: hintarajat, ALV, lämpötilat, esijäähdytys, käynnin ohjaus, poissaolo- ja yöasetukset. Jokaisella on oletusarvo, jota voi muuttaa; muutos tallentuu, kun kontrolli vapautetaan. Päällä/pois-valinnat ovat liukukytkimiä. Nimen napautus (tai hiiren vienti) näyttää selityksen siitä, mitä asetus tekee, ja kontrollin tooltip näyttää oletusarvon. Vain valitun ohjaustavan (jäähdytys tai lämmitys) asetukset näytetään.
- **Käyttöliittymä (asetuksissa):** `Tumma teema` -liukukytkin ja aksenttiväri (sininen, vihreä, purppura, pinkki, oranssi). Valinnat tallentuvat laitekohtaisesti selaimeen; oletuksena teema seuraa laitteen asetusta.

## Sanasto

| Merkitys | Käyttöliittymän teksti |
| --- | --- |
| Current temperature | Mitattu lämpötila |
| Setpoint | Tavoitelämpötila |
| Heat / cool / dry / fan | Lämmitys / Jäähdytys / Kuivaus / Puhallus |
| Off | Pois päältä |
| Available / disconnected | Yhteydessä / Ei yhteyttä |
| Sending / confirmed / failed | Lähetetään… / Vahvistettu / Muutos epäonnistui |
| Stale reading | Tiedot vanhentuneet (näytetään vain, kun tieto on vanhentunut) |
| Automation switch | Automaatio |
| Add VAT to the price | Lisää ALV sähkönhintaan |
| Forecast | Sääennuste |
| Price control: off / fixed thresholds / optimized | Hintaohjaus: Pois / Kiinteät rajat / Optimoitu |
| The automation may switch the pump on and off | Automaatio saa käynnistää ja sammuttaa pumpun |
| Manual pause / until / resume automation | Käsiohjauksen tauko / Käsiohjaus klo 21.30 asti / Palauta automaatio |
| Last observed | Päivitetty 5 min sitten |
| Spot-price control | Pörssisähköohjaus |
| Cheap / expensive period | Halpa jakso / Kallis jakso |
| Price rule | Hintasääntö |
| Base temperature | Perustaso |
| Away / visit | Poissa / Vierailu |
| Start / end visit | Vierailu / Lopeta vierailu |
| Arrival time | Saapumisaika |
| Optimized heating | Hintaohjaus: Optimoitu |
| Minimum / maximum storage temperature | Vähimmäislämpötila / Suurin varaava lämpötila |
| Low / high price | Halpa hinta / Kallis hinta |
| Site connected / link down, local fallback | Yhteydessä / Yhteys poikki — paikallinen ylläpitolämmitys käytössä |
| Fetch prices | Hae hinnat |
| Graphs | Kuvaajat |
| Precooling | Esijäähdytys |
| Maximum temperature | Enimmäislämpötila |
| Away from home | Poissa kotoa |
| Night mode | Yötila |
| Minimum time between power changes | Vähimmäisaika käynnistysten välillä |

Laitteen ilmoittama tila ja pyydetty muutos näytetään erikseen, kunnes laite vahvistaa muutoksen. Kun pörssisähköohjaus muuttaa asetusta, tapahtumissa näkyy syy, esimerkiksi `Pörssisähköohjaus: halpa jakso`.

Esimerkki: `Mökki · Olohuone — 6,5 °C · Tavoite 12 °C · Hintasääntö: alle 5 c/kWh`. Yhteyskatkossa: `Viimeisin mittaus 6,5 °C, tänään klo 14.32. Ei yhteyttä.`

Käytetään fi-FI-muotoilua (selain voi näyttää kellonajan syöttökentässä 12 tunnin muodossa; arvo tallentuu aina muodossa tt:mm), Celsius-asteita, 24 tunnin kelloa ja hinnoissa yksikköä c/kWh. Aikavyöhyke on Europe/Helsinki. Säätimissä on näppäimistötuki, näkyvä kohdistus ja riittävät kosketusalueet. Väri ei yksin välitä tilaa. Sovelluksen käyttö ei edellytä Matter- tai verkkotekniikan tuntemista.

## Asennus puhelimeen ja offline-tila (PWA)

- **Osoite:** sovellus asennetaan vain tailnetin HTTPS-osoitteesta (Tailscale Serve antaa kelvollisen varmenteen, jota PWA vaatii). Pelkkä `http://`-osoite tai IP-osoite ei kelpaa. Puhelimessa pitää olla Tailscale päällä ja kirjautuneena samaan tailnetiin; muita lupia ei tarvita.
- **Android (Chrome):** avaa osoite, valitse valikosta `Asenna sovellus` tai `Lisää aloitusnäytölle`.
- **iPhone/iPad (Safari):** avaa osoite, valitse jakopainikkeesta `Lisää Koti-valikkoon`.
- **Työpuhelin:** työnantajan hallinta voi estää Tailscalen tai VPN-profiilin. Kokeile asennusta ja avaamista ennen kuin luotat siihen; jos Tailscale ei toimi, sovellus avautuu vain selaimessa verkon sisältä tai ei ollenkaan.
- **Mitä tallentuu offline-käyttöön:** vain sovelluksen kuori (sivu, kuvakkeet ja manifesti), jotta sovellus avautuu ilman yhteyttä. Laitteiden tietoja (`/api/…`) ei tallenneta, joten offline-tilassa ei näytetä keksittyjä lukemia. Komentoja ei jonoteta eikä lähetetä myöhemmin: jos yhteyttä ei ole, komento ei lähde.
- **Yläpalkin tilarivi:** `Koti: ok / vanhentunut / virhe / käynnistyy` ja `Mökki: ok / vanhentunut / ei yhteyttä / tunnus hylätty`. `Mökki: turvalämpö` näkyy, kun jokin huone on paikallisella turvalämmöllä. Napautus tai hiiren vienti näyttää selityksen. Jos selain on offline tai päivitys epäonnistui, rivillä lukee `ei tietoa`, arvot himmenevät ja varoitusrivi kertoo, kuinka vanhaa tieto on (`Ei yhteyttä Villa Kuppisiin – näytetään viimeisin tieto`).
- **Odottaa vahvistusta:** pumpun komento näkyy kortilla tilassa `Odottaa vahvistusta`, ja uusi arvo näytetään vasta, kun laite ilmoittaa sen. Mökin tavoitteesta kertoo `Mökki kuitannut`.
- **Päivitys:** palvelutyö (`/sw.js`) tarkistetaan aina verkosta; vanha välimuisti poistetaan, kun välimuistin versio vaihtuu.
