# Smartschool BE — Home Assistant integratie

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Validate](https://github.com/Maffiow/ha-smartschool-be/actions/workflows/validate.yml/badge.svg)](https://github.com/Maffiow/ha-smartschool-be/actions/workflows/validate.yml)
[![Release](https://img.shields.io/github/v/release/Maffiow/ha-smartschool-be)](https://github.com/Maffiow/ha-smartschool-be/releases)

Een onofficiële Home Assistant custom integratie om toetsresultaten en leerlinginfo op te halen van het Vlaamse [Smartschool](https://www.smartschool.be) schoolplatform.

> Gebaseerd op het werk van [janmeermans/smartschool_ha](https://github.com/janmeermans/smartschool_ha) (MIT). Deze integratie gebruikt een eigen domein (`smartschool_be`) en kan naast andere Smartschool-integraties geïnstalleerd worden.

## Functies

- **Sensor per vak** — automatisch aangemaakt voor elk vak met beschikbare resultaten
- **Volledige scorehistory** als attribuut, inclusief toetsnaam, datum en feedback
- **Gemiddelde score** per vak over het volledige schooljaar
- **Laatste 5 resultaten** (alle vakken) op de overzichtssensor
- **Berichten** — teller van ongelezen berichten, een event bij elk nieuw bericht, en acties om berichten te lezen, beantwoorden, versturen, markeren, archiveren of te verwijderen
- **Agenda en Planner** — kalender met lessen en taken/toetsen uit de klassieke agenda én de nieuwe Planner, plus een sensor met komende taken
- **Info van de kinderen** — een apparaat per kind met naam, klas, titularis en foto
- **Modules aan/uit** — kies in de opties welke onderdelen je gebruikt
- **Diagnostics** — downloadbaar via de integratiepagina, zonder namen, punten of berichtinhoud
- Grafiekklaar voor [ApexCharts Card](https://github.com/RomRider/apexcharts-card) — kleuren per drempelwaarde (rood/geel/groen)
- Automatische herauthenticatie bij verlopen sessie
- Gewijzigd wachtwoord of geboortedatum? Home Assistant toont een melding om opnieuw aan te melden (reauth)
- Grote attributen (`score_history`, `recente_resultaten`) worden niet in de recorder-database opgeslagen

## Vereisten

- Een actief Smartschool-account
- De **geboortedatum** van de leerling (gebruikt voor de verplichte account-verificatiestap van Smartschool)
- Home Assistant 2025.1 of nieuwer

## Installatie

### Via HACS (aanbevolen)

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Maffiow&repository=ha-smartschool-be&category=integration)

1. Ga in Home Assistant naar **HACS**
2. Klik op de drie puntjes rechtsboven → **Aangepaste repositories**
3. Voer `https://github.com/Maffiow/ha-smartschool-be` in en kies type **Integratie**
4. Zoek **Smartschool BE** en klik op **Downloaden**
5. Herstart Home Assistant

Nieuwe versies verschijnen automatisch als update in HACS (en onder **Instellingen → Updates**).

### Handmatig

1. Kopieer de map `custom_components/smartschool_be` naar `<config>/custom_components/` op je Home Assistant installatie
2. Herstart Home Assistant

## Configuratie

1. Ga naar **Instellingen → Apparaten & diensten → Integratie toevoegen**
2. Zoek naar **Smartschool BE**
3. Vul de gegevens in:

| Veld | Beschrijving | Voorbeeld |
|:-----|:-------------|:----------|
| **Smartschool URL** | De basis-URL van de school | `https://mijnschool.smartschool.be` |
| **Gebruikersnaam** | Smartschool-login van de leerling | `jan.peeters` |
| **Wachtwoord** | Bijhorend wachtwoord | |
| **Geboortedatum** | Geboortedatum van de leerling (JJJJ-MM-DD) | `2010-03-15` |

> De geboortedatum is nodig omdat Smartschool bij elke nieuwe sessie een verificatiestap uitvoert. Dit is geen 2FA maar een vaste beveiligingsvraag van het platform.

### Tweestapsverificatie (2FA)

Vraagt je school na het inloggen een 6-cijferige code uit een authenticator-app (LastPass, Google of Microsoft Authenticator, ...)? Typ dan bij het instellen gewoon **de code die op dat moment in je app staat** in het veld *2FA-code*.

Home Assistant bewaart daarna de Smartschool-sessie, ook over herstarts heen, en houdt ze actief. Pas als Smartschool de sessie toch beëindigt, verschijnt de melding **Opnieuw aanmelden**: typ daar opnieuw de huidige code (het wachtwoord mag leeg blijven).

**Volledig automatisch (optioneel):** vul in plaats daarvan de *2FA-sleutel* in, de geheime lettercode achter de QR-code die je bij het instellen van de authenticator kreeg (een `otpauth://`-link mag ook). Dan maakt Home Assistant zelf de codes en vraagt hij nooit meer om een code. Let op: wie toegang heeft tot je Home Assistant of de backups, heeft dan beide factoren.

## Modules kiezen

Via **Instellingen → Apparaten & diensten → Smartschool BE → Configureren** zet je onderdelen aan of uit:

| Optie | Wat je krijgt |
|:------|:--------------|
| Resultaten | Sensor per vak en het laatste resultaat |
| Berichten | Teller van ongelezen berichten, event bij een nieuw bericht, berichtacties |
| Agenda en taken | Lessen en taken uit de klassieke Smartschool-agenda |
| Planner | Lessen, opdrachten en to-do's uit de nieuwe Planner |
| Info van de kinderen | Apparaat per kind met klas, titularis en foto |

Alles staat standaard aan. Zet je een onderdeel uit, dan worden de bijhorende entiteiten verwijderd. Heeft je school een onderdeel uitgeschakeld (bv. geen Resultaten in een lagere school), dan blijft het gewoon leeg; dat geeft geen foutmelding en geen vraag om opnieuw aan te melden. In de diagnostics zie je welke onderdelen je school niet aanbiedt.

## Info van de kinderen

Per kind maakt de integratie een apparaat met:

- `sensor.<naam>_klas` — de klas
- `sensor.<naam>_titularis` — de titularis(sen)
- `sensor.<naam>_info` — naam (met foto), plus attributen zoals klas, titularissen en account-id's

## Beschikbare sensoren

> Alle entiteiten hangen onder het apparaat **Smartschool &lt;gebruikersnaam&gt;** (of onder het apparaat van je kind). De entity-id's hieronder gelden voor nieuwe installaties; bestaande entiteiten behouden hun id.

Na configuratie maakt de integratie automatisch sensoren aan op basis van de gevonden vakken.

### Overzichtssensor

`sensor.smartschool_<gebruikersnaam>_laatste_resultaat`

| Attribuut | Beschrijving |
|:----------|:-------------|
| `vak` | Vaknaam van het laatste resultaat |
| `score` | Score als beschrijving (bv. `9/12`) |
| `score_percentage` | Numerieke score (bv. `75.0`) |
| `datum` | Datum van de toets |
| `beschikbaar_sinds` | Datum waarop het resultaat zichtbaar werd |
| `feedback` | Eventuele feedback van de leerkracht |
| `recente_resultaten` | Lijst van de laatste 5 resultaten (alle vakken) |

### Vaksensoren

`sensor.smartschool_<gebruikersnaam>_<vaknaam>`

De sensorwaarde (state) is de meest recente numerieke score (in %). Heeft de laatste toets geen percentage (bv. `goed`), dan is de state `unknown` en staat de beschrijving in het attribuut `score`.

| Attribuut | Beschrijving |
|:----------|:-------------|
| `toets` | Naam van de meest recente toets |
| `score` | Score als beschrijving |
| `score_percentage` | Numerieke score |
| `datum` | Datum van de toets |
| `beschikbaar_sinds` | Beschikbaarheidsdatum |
| `feedback` | Feedback van de leerkracht |
| `gemiddelde_score` | Gemiddelde van alle numerieke scores dit schooljaar |
| `aantal_toetsen` | Aantal toetsen met numerieke score |
| `score_history` | Volledige lijst van alle toetsen (gesorteerd van oud naar nieuw) |

#### Structuur van `score_history`

```json
[
  {
    "datum": "2025-10-14",
    "toets": "Hoofdstuk 3 — algebraïsche uitdrukkingen",
    "score": 82.5,
    "score_omschrijving": "33/40",
    "feedback": null
  }
]
```

## Berichten

### Sensor

`sensor.smartschool_<gebruikersnaam>_ongelezen_berichten` — aantal ongelezen berichten in de hele inbox, hetzelfde getal als het bolletje in Smartschool (elke 5 minuten bijgewerkt).

| Attribuut | Beschrijving |
|:----------|:-------------|
| `totaal_in_inbox` | Totaal aantal berichten in de inbox |
| `berichten` | De 15 nieuwste berichten: `id`, `van`, `onderwerp`, `datum`, `ongelezen`, `bijlagen`, `vlag` |

### Event bij een nieuw bericht

Bij elk nieuw bericht vuurt de integratie het event `smartschool_be_new_message` met `message_id`, `from`, `subject`, `date`, `attachments`, `account` en `config_entry_id`. Berichten die al in de inbox stonden bij de eerste installatie tellen niet als nieuw.

```yaml
automation:
  - alias: Smartschool bericht naar gsm
    triggers:
      - trigger: event
        event_type: smartschool_be_new_message
    actions:
      - action: smartschool_be.get_message
        data:
          message_id: "{{ trigger.event.data.message_id }}"
        response_variable: bericht
      - action: notify.mobile_app_mijn_gsm
        data:
          title: "Smartschool: {{ trigger.event.data.from }}"
          message: "{{ trigger.event.data.subject }} — {{ bericht.text[:200] }}"
```

### Acties

| Actie | Wat het doet |
|:------|:-------------|
| `smartschool_be.get_message` | Geeft `from`, `subject`, `date`, `to`, `cc`, `text`, `html`, `can_reply` en `attachments` terug. Smartschool markeert het bericht dan als gelezen. |
| `smartschool_be.reply_message` | Beantwoordt een bericht (`message`, optioneel `reply_all` en `subject`), zoals de knop *Beantwoorden*. |
| `smartschool_be.send_message` | Stuurt een nieuw bericht naar één persoon (`recipient` = volledige naam, `subject`, `message`). Vindt Smartschool meerdere personen, dan wordt er niets verstuurd. |
| `smartschool_be.mark_message_unread` | Markeert een bericht als ongelezen. |
| `smartschool_be.set_message_flag` | Zet een vlag: `none`, `green`, `yellow`, `red` of `blue`. |
| `smartschool_be.archive_message` | Verplaatst een bericht naar het archief. |
| `smartschool_be.trash_message` | Verplaatst een bericht naar de prullenbak (herstelbaar in Smartschool). |

Heb je meerdere accounts ingesteld, geef dan `config_entry_id` mee.

```yaml
action: smartschool_be.reply_message
data:
  message_id: 123456
  message: |
    Dag juf,

    Bedankt voor de info, we zijn er bij!
```

> Antwoorden en versturen gebeuren met de gewone tekst; nieuwe regels worden behouden. Bijlagen meesturen kan (nog) niet.
> Bevestigt Smartschool het verzenden niet, dan geeft de actie een fout. Kijk dan eerst bij *Verzonden* voor je opnieuw verstuurt.

## Agenda

- `calendar.smartschool_<gebruikersnaam>_agenda` — lessen met begin- en einduur, lokaal, leerkracht en lesonderwerp, plus taken en toetsen. Komt uit de klassieke agenda en/of de Planner (voor een ouderaccount: de planner van het kind). Werkt met de kalenderkaart en kalender-automatiseringen.
- `sensor.smartschool_<gebruikersnaam>_komende_taken` — aantal komende taken en toetsen, met attributen `volgende_datum`, `aantal_toetsen`, `volgende_toets` en de lijst `taken`.

De agenda wordt elk uur bijgewerkt; de kalenderweergave haalt lessen op voor de periode die je bekijkt.

Kan je school berichten of de agenda niet aanbieden voor jouw account, dan worden enkel die entiteiten *niet beschikbaar*; de resultaten blijven werken.

## Dashboard

De vaksensoren zijn direct bruikbaar met [ApexCharts Card](https://github.com/RomRider/apexcharts-card) (via HACS).

### Voorbeeld: balkgrafiek per vak met kleurdrempels

```yaml
type: custom:apexcharts-card
header:
  title: Wiskunde
  show: true
graph_span: 10months
yaxis:
  - min: 0
    max: 100
    decimals: 0
series:
  - entity: sensor.smartschool_jan_peeters_wiskunde
    type: column
    data_generator: |
      const now = new Date();
      const syYear = now.getMonth() >= 8 ? now.getFullYear() : now.getFullYear() - 1;
      const syStart = new Date(syYear, 8, 1).getTime();
      return entity.attributes.score_history
        .map(h => [new Date(h.datum).getTime(), parseFloat(h.score)])
        .filter(p => !isNaN(p[1]) && p[0] >= syStart);
    color_threshold:
      - value: 0
        color: "#e53935"
      - value: 50
        color: "#fdd835"
      - value: 60
        color: "#43a047"
    show:
      in_header: true
```

- Rood: score onder 50%
- Geel: score 50–59%
- Groen: score 60% of hoger

### Voorbeeld: tabel met laatste 5 resultaten

```yaml
type: markdown
content: |-
  ## Laatste 5 resultaten
  {% set r = state_attr('sensor.smartschool_jan_peeters_laatste_resultaat', 'recente_resultaten') %}
  {% if r and r | length > 0 %}
  {% set ns = namespace(rijen=['| Datum | Vak | Toets | Score |', '|:------|:----|:------|------:|']) %}
  {% for item in r %}
  {% set ns.rijen = ns.rijen + ['| ' + (item.availability_date or item.date or '?')[:10] + ' | ' + (item.course or '?') + ' | ' + (item.name or '?') + ' | **' + (item.score_description or (item.score_value | string) or '?') + '** |'] %}
  {% endfor %}
  {{ ns.rijen | join('\n') }}
  {% else %}
  _Geen recente resultaten beschikbaar._
  {% endif %}
```
<img width="1503" height="1000" alt="image" src="https://github.com/user-attachments/assets/b43306e5-e990-4830-847d-e89a8fb67b56" />

## Opmerkingen

- Resultaten worden elke **15 minuten** opgehaald, berichten elke **5 minuten**, de agenda elk **uur**
- Smartschool vergt een account-verificatie (geboortedatum) bij elke nieuwe sessie; dit is ingebouwd en automatisch
- Per API-aanroep worden maximaal 50 resultaten per pagina opgehaald; alle pagina's worden doorlopen
- Deze integratie is **niet officieel** en heeft geen band met Smartschool/Scala BVBA

## Een nieuwe versie uitbrengen

1. Verhoog `version` in `custom_components/smartschool_be/manifest.json` (bv. `1.2.1`)
2. Commit en push naar `main`
3. Tag en push: `git tag v1.2.1 && git push origin v1.2.1`

De workflow *Release* controleert of de tag overeenkomt met de manifest-versie en maakt de GitHub release aan; HACS biedt die daarna als update aan.

## Licentie

MIT License — zie [LICENSE](LICENSE).
