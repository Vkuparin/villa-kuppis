# Käyttöliittymän alustava suunnitelma

Käyttöliittymän kieli on suomi. Sama verkkosovellus toimii tietokoneen selaimessa ja Android-puhelimen kotinäytölle asennettuna.

## Näkymät

- **Yleiskuva:** kohteet `Koti` ja `Mökki`, huoneet, viimeisimmät lämpötilat, yhteyden tila ja tämän päivän sähkön hinta.
- **Laite:** mitattu lämpötila, tavoitelämpötila, toimintatila, laitteen tukemat säädöt ja viimeisin vahvistettu muutos.
- **Pörssisähkö:** päivän ja huomisen hinnat 15 minuutin jaksoissa sekä suunnitelma: mikä tavoitelämpötila on voimassa kussakin jaksossa.
- **Hintasäännöt:** käyttäjä asettaa itse hintarajat ja lämpötilat, esimerkiksi `Kun pörssihinta on alle 5 c/kWh → 12 °C`. Hinta on pelkkä pörssihinta ilman veroja ja marginaaleja. Perustaso on mökillä 5 °C. Kotona säännöt koskevat kesällä jäähdytystä.
- **Mökki:** painike `Aloita vierailulämmitys` nostaa lämmittimet 22 asteeseen ja keskeyttää hintasäännöt. `Lopeta vierailu` palauttaa ne.
- **Tapahtumat:** kuka tai mikä muutti asetusta, milloin ja onnistuiko muutos.
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
| Start / end visit heating | Aloita vierailulämmitys / Lopeta vierailu |
| Precooling | Esijäähdytys |

Laitteen ilmoittama tila ja pyydetty muutos näytetään erikseen, kunnes laite vahvistaa muutoksen. Kun pörssisähköohjaus muuttaa asetusta, tapahtumissa näkyy syy, esimerkiksi `Pörssisähköohjaus: halpa jakso`.

Esimerkki: `Mökki · Olohuone — 6,5 °C · Tavoite 12 °C · Hintasääntö: alle 5 c/kWh`. Yhteyskatkossa: `Viimeisin mittaus 6,5 °C, tänään klo 14.32. Ei yhteyttä.`

Käytetään fi-FI-muotoilua, Celsius-asteita, 24 tunnin kelloa ja hinnoissa yksikköä c/kWh. Aikavyöhyke on Europe/Helsinki. Säätimissä on näppäimistötuki, näkyvä kohdistus ja riittävät kosketusalueet. Väri ei yksin välitä tilaa. Sovelluksen käyttö ei edellytä Matter- tai verkkotekniikan tuntemista.
