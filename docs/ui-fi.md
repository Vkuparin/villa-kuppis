# Käyttöliittymän alustava suunnitelma

Käyttöliittymän kieli on suomi. Talouden puhelimet ovat Android-laitteita. Ensimmäinen integraatiokokeilu tehdään Android-sovelluksella, ja työpöytäkäyttöön suunnitellaan selainkäyttöliittymä. Pelkkä asennettava verkkosovellus valitaan vasta, jos mökin laitteiden ohjaus PC-palvelimelta varmistuu. Molemmat käyttöliittymät käyttävät samaa sanastoa ja samoja toimintaperiaatteita.

## Näkymät

- **Yleiskuva:** kohdevalinta `Koti` / `Mökki`, huoneet, viimeisimmät lämpötilat ja yhteyden tila.
- **Laite:** mitattu lämpötila, tavoitelämpötila, toimintatila, laitteen tukemat säädöt ja viimeisin vahvistettu muutos.
- **Ajastukset:** myöhempi vaihe. Erotetaan laitteen omat ajastukset palvelimen ajastuksista ja näytetään ristiriidat.
- **Tapahtumat:** kuka muutti asetusta, milloin ja onnistuiko muutos.
- **Asetukset:** laitteet, kohteet, käyttäjät ja yhteydet. Tekniset tiedot ovat tarvittaessa avattavassa lisänäkymässä.

## Sanasto ja toiminta

| Merkitys | Käyttöliittymän teksti |
| --- | --- |
| Current temperature | Mitattu lämpötila |
| Setpoint | Tavoitelämpötila |
| Heat / cool / dry / fan | Lämmitys / Jäähdytys / Kuivaus / Puhallus |
| Heating off | Lämmitys pois |
| Available / disconnected | Yhteydessä / Ei yhteyttä |
| Sending / confirmed / failed | Lähetetään… / Vahvistettu / Muutos epäonnistui |
| Result unknown | Muutosta ei voitu vahvistaa |
| Stale reading | Vanhentunut mittaus |
| Last observed | Päivitetty 5 min sitten |
| Retry | Yritä uudelleen |

Laitteen ilmoittama tila ja käyttäjän pyytämä muutos näytetään erillisinä, kunnes muutos on vahvistettu. Lämpöpumpun pois kytkeminen ei ole sama asia kuin verkkovirran katkaiseminen. Käyttöliittymä ei saa antaa sellaista vaikutelmaa.

Esimerkki: `Mökki · Olohuone — 18,5 °C · Tavoite 20 °C · Lämmitys`. Yhteyskatkossa: `Viimeisin mittaus 18,5 °C, tänään klo 14.32. Ei yhteyttä.`

Käytetään fi-FI-muotoilua, Celsius-asteita ja 24 tunnin kelloa. Europe/Helsinki on ehdotus ajastusten aikavyöhykkeeksi. Säätimissä on näppäimistötuki, näkyvä kohdistus, riittävät kosketusalueet ja tekstimuotoinen palaute. Väri ei yksin välitä tilaa.

Ensimmäisessä versiossa säädetään yhtä laitetta kerrallaan. Koko mökin lämmityksen poiskytkentä tai muu laaja toiminto vaatii selkeän kohdeluettelon ja vahvistuksen. Yhteydettömässä tilassa voidaan näyttää välimuistissa oleva näkymä, mutta lämmityskomentoja ei jonoteta myöhemmin lähetettäviksi.

Tarvittavat laitekohtaiset ominaisuudet ja mökin ylläpitolämpötila ratkaistaan ennen toteutusta. Sovelluksen käyttö ei edellytä käyttäjältä MQTT-, Matter- tai verkkotekniikan käsitteiden tuntemista.
