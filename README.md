<div align="center">

<img src="mtg_binder/logo.png" alt="MTG Binder" width="250">

# MTG Binder für Home Assistant

**Deine Magic-Sammlung als Bücherregal in Home Assistant:<br>
Binderseiten fotografieren, alle Karten eines Fotos auf einmal erkennen und digital durchblättern.**

[![Version][version-shield]][changelog]
![Unterstützt amd64][amd64-shield]
![Oberfläche: Deutsch][sprache-shield]
[![Lizenz: MIT][lizenz-shield]](LICENSE)

[![Repository zu deinem Home Assistant hinzufügen][repo-knopf]][repo-hinzufuegen]

</div>

> **English summary:** A Home Assistant app (add-on) for *Magic: The Gathering* collectors. Photograph your binder
> pages, let a Windows PC with an NVIDIA GPU recognise every card on the photo, and browse the result as a digital
> bookshelf inside Home Assistant, with Cardmarket prices, bulk boxes, sorting rules, decks, Secret Lair tracking and
> proxy printing. The user interface and documentation are **German only**.

![Bücherregal mit Bindern und Kisten](bilder/regal.png)

## Über die App

MTG Binder ist eine App (früher „Add-on“) für Home Assistant. Sie erscheint im Seitenmenü, läuft komplett im eigenen
Netzwerk und ist für das Smartphone gebaut, funktioniert aber genauso am PC.

Die Arbeit teilen sich zwei Teile:

| Teil | Wo | Aufgabe |
|---|---|---|
| **App** (dieses Repository) | Home Assistant | Server, Oberfläche, Datenbank, Preise, Sicherungen, Sensoren |
| **PC-Erkennung** (Installer im [Release][releases]) | Windows-PC mit NVIDIA-Grafikkarte | Karten auf den Fotos finden und erkennen |

Hochgeladene Fotos warten in der App, bis der PC sie abholt. Ist der PC aus, kannst du trotzdem blättern, suchen,
sortieren und Karten von Hand hinzufügen.

## Funktionen

### Binder per Foto

- **Ganze Seiten auf einmal:** Binderseiten mit dem Handy fotografieren und hochladen; die Erkennung findet jede
  Tasche und bestimmt Name, Druck und Sprache jeder Karte (im eigenen Abnahmetest: Name 100 %, Druck 99,1 %).
- **Bücherregal:** Jeder Binder steht als Buchrücken im Regal, mit Farbe, Kartenzahl und Wert.
- **Lesetisch:** Binder aufschlagen und durchblättern, am PC als Doppelseite, am Handy per Wischen. Umschalter
  zwischen Scryfall-Bild und deinem eigenen Foto.
- **Prüfansicht:** Unsichere Treffer werden nie stillschweigend übernommen, sondern warten auf dein Urteil
  (bestätigen, Druck ändern, keine Karte).
- **Bearbeiten:** Karten umstecken und tauschen, Karten in leere Taschen legen, Leerseiten einfügen, Seiten
  löschen, Foil markieren.

### Kisten und Gesamtsammlung

- **Bulkkisten** als Karteikasten, Raster oder Liste; sortieren, gruppieren und filtern auch bei mehreren
  tausend Karten, dazu Fächer und Trennkarten.
- **Gesamtsammlung** über alle Binder, Kisten und Decks mit Suche. Jede Karte ist ein echtes Exemplar an genau
  einem Ort.
- **Sets:** Vollständigkeit je Set nach Drucken und Namen, fehlende Karten mit Preis als kopierbare Liste.
- **Überzählige Karten** über einem wählbaren Limit als Text und CSV, zum Beispiel als Verkaufsliste.
- **Import:** ManaBox-CSV mit Probelauf und Rückgängig, dazu Kartenlisten direkt in eine Kiste.

### Bulk-Scan

- **Karten stapelweise scannen:** Handy über einen Kartenauswurf (zum Beispiel den Card Slinger) hängen; die App
  löst selbst aus, sobald eine Karte still liegt, und zeigt den Namen meist nach ein bis zwei Sekunden.
- **Foil und Sets je Durchgang** vorgeben, damit der richtige Druck gewählt wird.
- **Scan-Eingang:** Sichere Karten werden zu Posten zusammengefasst, unsichere landen in der Prüfansicht.

### Sortierregeln

- **Regeln wie „Preis ab 5 € → Spezial“ oder „Farbe Rot → Rot“:** Bedingungen für Farbe, Farbidentität, Manawert,
  Typ, Seltenheit, Set, Sonderdrucke, Foil, Sprache, Name, Preis und Limit je Karte.
- **Automatisch einsortieren** nach dem Scan, mit Zielkiste und Fach an jeder Karte; jedes Einräumen lässt sich
  zurücknehmen.
- **Umräumen:** Nach geänderten Regeln zeigt die App, welche Karten in eine andere Kiste gehören.

### Preise und Dashboard

- **Cardmarket-Preise täglich**, Preisart wählbar (Low, Trend, Ø 1, 7 oder 30 Tage), bei Foil-Karten der Foil-Preis.
- **Dashboard:** Wert der Sammlung, Preisentwicklung, teuerste Karten, Gewinner und Verlierer, Verteilung nach Orten
  und Sets.
- **Kaufpreise** je Karte eintragen und Gewinn oder Verlust gegen den heutigen Wert sehen.

### Decks und Kaufliste

- **Decks importieren** von Archidekt (Link) oder als Text, zum Beispiel aus dem Export von Moxfield.
- **Was habe ich, was fehlt?** Je Karte Fundort in der Sammlung, Fehlt-Liste zum Kopieren, Anteil „baubar“ im Regal.
- **Deck bauen und zerlegen:** Entnahmeliste zum Abhaken; die Karten wandern ins Deck und später an ihren Platz
  zurück.
- **Statistik:** Manakurve, Farben und Kartentypen.
- **Kaufliste mit Preisalarm:** Wunschpreis je Karte, Markierung beim Unterschreiten, Sprung zum Angebot bei
  Cardmarket mit Filtern für Land, Sprache und Zustand.

### Secret Lair

- Kommende Drops und alle seit 2019 mit Reprint-Werten, Preis der einzelnen Drucke und des ganzen Drops bei
  Cardmarket.
- Eigene Käufe mit Kaufpreis, heutigem Wert und Verlauf. Nur Zahlen, keine Kaufempfehlung.

### Proxys drucken

- Aus einer Deckliste entsteht ein druckfertiges PDF (A4, mit Passermarken für den Schneideplotter), erzeugt mit
  [Silhouette Card Maker][scm] von Alan Cha.
- Eigene Bilder und Rückseiten, Wahl des Drucks, Kalibrierung für Vorder- und Rückseite.
- Proxys sind nur für Casual und Playtesting gedacht.

### Home Assistant

- **Seitenmenü** über Ingress, sichtbar für Admins, auch unterwegs über Nabu Casa.
- **Sensoren per MQTT** (Discovery): Gesamtwert, Kartenzahlen, Karten zur Prüfung, Status der Erkennung, Wert je
  Binder und Kiste, Gewinner und Verlierer, Kaufliste unter Wunschpreis, neue Secret-Lair-Drops. Beispiele für
  Dashboard-Karten und Benachrichtigungen stehen in [HOME-ASSISTANT.md][ha-doku].
- **Sicherungen:** täglich und vor jedem Update, Zurückspielen in der App; die Datenbank steckt außerdem in den
  Backups von Home Assistant.
- **Rückgängig:** Jede Änderung ist ein Schritt in der Historie und lässt sich zurücknehmen.
- **Immer aktuell:** Neue Karten kommen täglich von Scryfall; Updates der PC-Erkennung laufen von selbst.

## Screenshots

| Lesetisch | Kiste |
|---|---|
| ![Aufgeschlagener Binder als Doppelseite](bilder/lesetisch.jpg) | ![Kiste in der Rasteransicht](bilder/kiste.jpg) |

| Dashboard | Deck |
|---|---|
| ![Dashboard mit Wert und Preisentwicklung](bilder/dashboard.jpg) | ![Deckansicht mit Statistik](bilder/deck.png) |

| Sets | Secret Lair |
|---|---|
| ![Vollständigkeit je Set](bilder/sets.png) | ![Secret-Lair-Drops mit Reprint-Werten](bilder/secretlair.png) |

<div align="center">
<img src="bilder/handy-regal.png" alt="Regal am Smartphone" width="260">
&nbsp;&nbsp;
<img src="bilder/handy-lesetisch.jpg" alt="Lesetisch am Smartphone" width="260">
</div>

## Voraussetzungen

- **Home Assistant OS oder Supervised** auf einem Rechner mit `amd64` (zum Beispiel ein Mini-PC). Andere
  Architekturen wie der Raspberry Pi werden derzeit nicht unterstützt.
- **Windows-PC (10 oder 11) mit NVIDIA-Grafikkarte** für die Erkennung, im selben Netzwerk, mit gut 30 GB freiem
  Speicher.
- **Empfohlen:** ein Netzlaufwerk für Fotos und Vorschaubilder, in Home Assistant unter Einstellungen → System →
  Speicher eingebunden.
- **Optional:** Mosquitto-Broker mit MQTT-Integration für die Sensoren; Nabu Casa oder ein anderer HTTPS-Zugang für
  den Bulk-Scan (die Kamera im Browser braucht HTTPS).

## Installation

1. Repository hinzufügen, am einfachsten über den Knopf:

   [![Repository zu deinem Home Assistant hinzufügen][repo-knopf]][repo-hinzufuegen]

   Von Hand: Einstellungen → Apps (früher „Add-ons“) → Store → Menü oben rechts → **Repositories** und diese
   Adresse eintragen:

   ```text
   https://github.com/JustTheD-lab/MTG-HA
   ```

2. Im Store **„MTG Binder“** öffnen und installieren. Home Assistant baut das Abbild selbst, das dauert ein paar
   Minuten.

   [![MTG Binder in deinem Home Assistant öffnen][app-knopf]][app-oeffnen]

3. Im Reiter **Konfiguration** den Speicherort setzen (siehe unten).
4. Starten und **„In der Seitenleiste anzeigen“** einschalten.
5. In der App im Regal auf **„PC verbinden“** tippen, einen Schlüssel erzeugen und den **Installer herunterladen**
   (`MTG-Binder-Setup.exe`, auch unter [Releases][releases]). Am Windows-PC starten und Adresse
   (`http://<IP von Home Assistant>:8099`) und Schlüssel eintragen. Die erste Einrichtung lädt Kartendaten und
   Kartenbilder von Scryfall und dauert einige Stunden; ein Abbruch setzt beim nächsten Start fort.
6. Im Regal **„Neuer Binder“** wählen und die ersten Fotos hochladen.

Die ausführliche Anleitung steht im Reiter **Dokumentation** der App und in [DOCS.md][doku].

## Konfiguration

| Option | Bedeutung |
|---|---|
| `speicherort` | Ordner für Fotos und Vorschaubilder, am besten auf einem Netzlaufwerk, zum Beispiel `/share/netzlaufwerk/mtg-binder`. Leer lassen nur zum Ausprobieren, dann liegt alles auf dem Rechner von Home Assistant. Die Datenbank bleibt immer in der App. |
| `mqtt` | Schickt die Werte als Sensoren „MTG …“ an Home Assistant (Standard: an). |

Port **8099** ist die Schnittstelle für die PC-Erkennung. Sie ist nur für das Heimnetz gedacht und nur mit dem
Schlüssel aus der App nutzbar; gib den Port nicht ins Internet frei.

## Aktualisieren

Home Assistant zeigt neue Versionen von selbst an, die Änderungen stehen im [Änderungsprotokoll][changelog]. Beim
ersten Start einer neuen Version sichert die App ihre Datenbank. Die PC-Erkennung holt sich die passende Version
danach selbst.

## Gut zu wissen

- Die Oberfläche gibt es nur auf **Deutsch**.
- MTG Binder ist ein privates Hobbyprojekt ohne Gewähr und ohne Anspruch auf Support. Fehler und Wünsche gern als
  [Issue][issues].
- Deine Fotos und deine Sammlung bleiben bei dir. Nach außen fragt die App nur öffentliche Kartendaten ab:
  Scryfall (Karten und Bilder), Cardmarket (Preisdatei), MTGJSON und das MTG Wiki (Secret Lair), Archidekt
  (Decklinks) und GitHub (Silhouette Card Maker, Updates).

## Datenquellen und Dank

- Kartendaten und Kartenbilder: [Scryfall](https://scryfall.com)
- Preise: Price Guide von [Cardmarket](https://www.cardmarket.com)
- Decklisten und Secret-Lair-Daten: [Archidekt](https://archidekt.com), [MTGJSON](https://mtgjson.com) und das MTG Wiki
- Proxy-Druck: [Silhouette Card Maker][scm] von Alan Cha (MIT-Lizenz)

MTG Binder steht in keiner Verbindung zu diesen Diensten und wird von ihnen nicht unterstützt.

## Rechtliches

Der Code dieses Repositorys steht unter der [MIT-Lizenz](LICENSE).

MTG Binder is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed by Wizards.
Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC.

Magic: The Gathering, die Kartennamen und die Kartenbilder sind Eigentum von Wizards of the Coast und der jeweiligen
Künstler. Die Screenshots zeigen Kartenbilder von Scryfall.

[version-shield]: https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2FJustTheD-lab%2FMTG-HA%2Fmain%2Fmtg_binder%2Fconfig.yaml&query=%24.version&label=Version&color=c9a769
[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg
[sprache-shield]: https://img.shields.io/badge/Oberfl%C3%A4che-Deutsch-informational.svg
[lizenz-shield]: https://img.shields.io/badge/Lizenz-MIT-blue.svg
[repo-knopf]: https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg
[repo-hinzufuegen]: https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FJustTheD-lab%2FMTG-HA
[app-knopf]: https://my.home-assistant.io/badges/supervisor_addon.svg
[app-oeffnen]: https://my.home-assistant.io/redirect/supervisor_addon/?addon=afc106f4_mtg_binder&repository_url=https%3A%2F%2Fgithub.com%2FJustTheD-lab%2FMTG-HA
[changelog]: mtg_binder/CHANGELOG.md
[doku]: mtg_binder/DOCS.md
[ha-doku]: mtg_binder/HOME-ASSISTANT.md
[releases]: https://github.com/JustTheD-lab/MTG-HA/releases/latest
[issues]: https://github.com/JustTheD-lab/MTG-HA/issues
[scm]: https://github.com/Alan-Cha/silhouette-card-maker
