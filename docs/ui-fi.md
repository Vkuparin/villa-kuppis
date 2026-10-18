# Käyttöliittymän alustava suunnitelma

Käyttöliittymän kieli on suomi. Sovellusta käytetään Android-puhelimilla ja tietokoneen selaimella. Molemmat käyttävät samaa sanastoa ja samoja toimintaperiaatteita.

## Näkymät

- **Yleiskuva:** kohteet `Koti` ja `Mökki`, huoneet, viimeisimmät lämpötilat, yhteyden tila ja tämän päivän sähkön hinta.
- **Laite:** mitattu lämpötila, tavoitelämpötila, toimintatila, laitteen tukemat säädöt ja viimeisin vahvistettu muutos.
- **Pörssisähkö:** päivän hinnat 15 minuutin jaksoissa ja suunnitelma: milloin mitäkin tavoitelämpötilaa käytetään ja arvioitu säästö.
- **Mökki:** tila `Poissa` (5–8 °C) tai `Vierailu` (22 °C). Vierailulle annetaan saapumisaika, ja sovellus aloittaa lämmityksen ajoissa.
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
| Away / visit | Poissa / Vierailu |
| Arrival time | Saapumisaika |
| Preheating | Esilämmitys käynnissä |
| Comfort temperature | Mukavuuslämpötila |

Laitteen ilmoittama tila ja pyydetty muutos näytetään erikseen, kunnes laite vahvistaa muutoksen. Kun pörssisähköohjaus muuttaa asetusta, tapahtumissa näkyy syy, esimerkiksi `Pörssisähköohjaus: halpa jakso`.

Esimerkki: `Mökki · Olohuone — 6,5 °C · Tavoite 8 °C · Poissa`. Yhteyskatkossa: `Viimeisin mittaus 6,5 °C, tänään klo 14.32. Ei yhteyttä.`

Käytetään fi-FI-muotoilua, Celsius-asteita, 24 tunnin kelloa ja hinnoissa yksikköä c/kWh. Aikavyöhyke on Europe/Helsinki. Säätimissä on näppäimistötuki, näkyvä kohdistus ja riittävät kosketusalueet. Väri ei yksin välitä tilaa. Sovelluksen käyttö ei edellytä Matter- tai verkkotekniikan tuntemista.
