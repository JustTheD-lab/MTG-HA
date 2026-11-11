# MTG Binder

![Unterstützt amd64][amd64-shield] ![Oberfläche: Deutsch][sprache-shield]

Deine Magic-Sammlung als Bücherregal in Home Assistant: Binderseiten fotografieren, alle Karten eines Fotos auf
einmal erkennen und digital durchblättern.

![Bücherregal mit Bindern und Kisten][regal]

## Über die App

- **Binder per Foto:** Bücherregal, Lesetisch zum Durchblättern und Prüfansicht für unsichere Treffer
- **Kisten und Gesamtsammlung:** Karteikasten, Raster oder Liste, Suche, Sets mit Vollständigkeit
- **Bulk-Scan** mit dem Handy über einem Kartenauswurf, mit **Sortierregeln** und automatischem Einsortieren
- **Preise** täglich von Cardmarket, Dashboard, Kaufpreise mit Gewinn und Verlust
- **Decks** mit Fehlt-Liste, Deckbau, Statistik und **Kaufliste mit Preisalarm**
- **Secret Lair** mit Reprint-Werten und eigenen Käufen
- **Proxys drucken** als PDF für den Schneideplotter
- **Sensoren per MQTT**, tägliche Sicherungen, jede Änderung mit Rückgängig

## Was du brauchst

- Home Assistant auf einem Rechner mit `amd64`
- einen Windows-PC mit NVIDIA-Grafikkarte im selben Netzwerk: Dort läuft die Erkennung der Karten. Den Installer
  lädst du nach dem Start in der App unter **„PC verbinden“** herunter.
- empfohlen: ein Netzlaufwerk für Fotos und Vorschaubilder (Option „Speicherort“)

Die Einrichtung Schritt für Schritt steht im Reiter **Dokumentation**, alle Funktionen mit Bildern auf
[GitHub][repo].

MTG Binder is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed by Wizards.
Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC.

[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg
[sprache-shield]: https://img.shields.io/badge/Oberfl%C3%A4che-Deutsch-informational.svg
[regal]: https://raw.githubusercontent.com/JustTheD-lab/MTG-HA/main/bilder/regal.png
[repo]: https://github.com/JustTheD-lab/MTG-HA
