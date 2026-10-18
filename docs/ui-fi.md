# Käyttöliittymän alustava suunnitelma

Käyttöliittymän kieli on suomi. Tietokoneella käytetään selainta. Android-sovellus näyttää saman verkkosovelluksen ja lisää mökin toiminnot, jotka tarvitsevat Google Homea.

## Näkymät

- **Yleiskuva:** kohteet `Koti` ja `Mökki`, huoneet, viimeisimmät lämpötilat, yhteyden tila ja tämän päivän sähkön hinta.
- **Laite:** mitattu lämpötila, tavoitelämpötila, toimintatila, laitteen tukemat säädöt ja viimeisin vahvistettu muutos.
- **Kuvaajat:** lämpötilat, ilmanlaatu (ALPSTUGA), tavoitelämpötilat ja hinnat samalla aika-akselilla, huoneittain ja kohteittain.
- **Pörssisähkö:** päivän ja huomisen hinnat 15 minuutin jaksoissa sekä suunnitelma: mikä tavoitelämpötila on voimassa kussakin jaksossa ja miksi.
- **Hintasäännöt:** kiinteät hintarajat, esimerkiksi `Kun pörssihinta on alle 5 c/kWh → 12 °C`, tai kytkin `Älykäs ylläpitolämmitys` (vähimmäislämpötila, suurin varaava lämpötila, halpa ja kallis hinta). Asetukset koko kohteelle tai huoneittain. Hinta on pelkkä pörssihinta ilman veroja ja marginaaleja. Kotona säännöt koskevat kesällä jäähdytystä.
- **Mökki:** `Vierailu` kysyy saapumisajan ja tavoitelämpötilan, ja sovellus lämmittää halvimmilla jaksoilla ajoissa. `Lopeta vierailu` palauttaa ylläpitolämmityksen. `Lähetä suunnitelma uudelleen`, jos päivän siirto Google Homeen epäonnistui.
- **Tapahtumat:** kuka tai mikä muutti asetusta, milloin ja onnistuiko muutos.
- **Ilmoitukset (Android):** päivän siirto epäonnistui, laite ei ole yhteydessä tai vierailun tavoite ei toteudu ajoissa.
- **Asetukset:** laitteet, kohteet, käyttäjät, yhteydet ja hinnanohjauksen rajat.

## Sanasto

| Merkitys | Käyttöliittymän teksti |
| --- | --- |
| Current temperature | Mitattu lämpötila |
| Setpoint | Tavoitelämpötila |
| Heat / cool / dry / fan | Lämmitys / Jäähdytys / Kuivaus / Puhallus |
| Off | Pois päältä |
| Available / disconnected | Yhteydessä / Ei yhteyttä |
| Sending / confirmed / failed | Lähetetään… / Vahvistettu / Muutos epäonnistui |
| Stale reading | Vanhentunut mittaus |
| Last observed | Päivitetty 5 min sitten |
| Spot-price control | Pörssisähköohjaus |
| Cheap / expensive period | Halpa jakso / Kallis jakso |
| Price rule | Hintasääntö |
| Base temperature | Perustaso |
| Away / visit | Poissa / Vierailu |
| Start / end visit | Vierailu / Lopeta vierailu |
| Arrival time | Saapumisaika |
| Smart maintenance heating | Älykäs ylläpitolämmitys |
| Minimum / maximum storage temperature | Vähimmäislämpötila / Suurin varaava lämpötila |
| Low / high price | Halpa hinta / Kallis hinta |
| Re-send plan | Lähetä suunnitelma uudelleen |
| Graphs | Kuvaajat |
| Precooling | Esijäähdytys |

Laitteen ilmoittama tila ja pyydetty muutos näytetään erikseen, kunnes laite vahvistaa muutoksen. Kun pörssisähköohjaus muuttaa asetusta, tapahtumissa näkyy syy, esimerkiksi `Pörssisähköohjaus: halpa jakso`.

Esimerkki: `Mökki · Olohuone — 6,5 °C · Tavoite 12 °C · Hintasääntö: alle 5 c/kWh`. Yhteyskatkossa: `Viimeisin mittaus 6,5 °C, tänään klo 14.32. Ei yhteyttä.`

Käytetään fi-FI-muotoilua, Celsius-asteita, 24 tunnin kelloa ja hinnoissa yksikköä c/kWh. Aikavyöhyke on Europe/Helsinki. Säätimissä on näppäimistötuki, näkyvä kohdistus ja riittävät kosketusalueet. Väri ei yksin välitä tilaa. Sovelluksen käyttö ei edellytä Matter- tai verkkotekniikan tuntemista.
