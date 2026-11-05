# Wijzigingen

Elke versie hieronder verschijnt als release op GitHub en als update in Home Assistant
(**Instellingen → Updates**, via HACS). De tekst onder een versie is wat je in het
update-venster te zien krijgt.

## [1.10.0]

- **Namen in je eigen taal:** sensor- en kalendernamen volgen de taal van Home Assistant
  (bv. *Unread messages* in het Engels). Bestaande entiteiten houden hun id.
- **Uitleg bij elk veld** bij het instellen en opnieuw aanmelden.
- **Documentatie:** probleemoplossing, bekende beperkingen, hoe vaak gegevens bijwerken en
  verwijderen.
- **Kwaliteit:** testdekking van 79% naar 95%+ (afgedwongen in de controles) en een
  `quality_scale.yaml` die bijhoudt welke regels van de kwaliteitsschaal van Home Assistant
  gehaald zijn.

## [1.9.0]

- **Eigen icoon:** de integratie toont nu het Smartschool-icoon in Home Assistant
  (integratielijst, apparaten, updates). Vereist Home Assistant 2026.3 of nieuwer.
- **Duidelijke release notes:** updates tonen voortaan wat er veranderd is, in plaats van
  enkel een link naar GitHub.

## [1.8.0]

- **Berichten openen in een popup:** de nieuwe actie `smartschool_be.select_message` laadt een
  bericht in de sensor *Geselecteerd bericht* (enkel platte tekst, niet in de recorder).
- Meegeleverd dashboard-script: elementen met `data-ssbe-msg` openen het bericht in een
  popup (`#smartschool-bericht`, bv. een Bubble Card).

## [1.7.1]

- Geen dubbele entiteitsnamen meer, zoals "Smartschool x Smartschool Ongelezen Berichten (x)".

## [1.7.0]

- **Inloggen met de 6-cijferige 2FA-code** uit je authenticator-app (bv. LastPass).
- De Smartschool-sessie wordt bewaard en blijft geldig na een herstart; een nieuwe code is pas
  nodig als Smartschool de sessie beëindigt. Opnieuw aanmelden vraagt dan enkel de code.

## [1.6.1]

- 2FA-sleutels worden aanvaard zoals authenticator-apps dat doen (spaties, onzichtbare
  tekens, `otpauth://`-links) met duidelijkere foutmeldingen.

## [1.6.0]

- Ondersteuning voor scholen met **tweestapsverificatie** (authenticator-app).

## [1.5.1]

- Berichten en agenda worden opgevraagd zoals een browser dat doet; foutmeldingen vermelden
  wat Smartschool teruggaf.

## [1.5.0]

- **Modules aan/uit** in de opties: resultaten, berichten, agenda, planner, info van de kinderen.
- Echte teller van ongelezen berichten (hele inbox), Planner-ondersteuning, klas, titularis en
  foto per kind.
- Een module die je school uitschakelde, vraagt niet langer ten onrechte om opnieuw aan te melden.

## [1.4.0]

- Berichten (lezen, beantwoorden, versturen, markeren, archiveren), event bij een nieuw
  bericht, agendakalender, sensor met komende taken en diagnostics.

## [1.3.0]

- Opnieuw aanmelden (reauth), geen leerlinggegevens meer in de logs, correcte sensorwaarden
  en lijsten buiten de recorder-database.

## [1.2.0]

- Eerste versie als **Smartschool BE**, installeerbaar via HACS.
