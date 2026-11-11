# MTG Binder

![Unterstützt amd64][amd64-shield] ![Oberfläche: Deutsch][sprache-shield]

Deine Magic-Sammlung als Bücherregal in Home Assistant. Du fotografierst deine Binderseiten, die App erkennt jede
Karte, und du blätterst digital durch deine Binder.

![Bücherregal mit Bindern und Kisten][regal]

## Über die App

Deine Binder stehen als Buchrücken in einem digitalen Regal, und du kannst sie aufschlagen und durchblättern wie die
echten. Für deinen Bulk gibt es Kisten, die du mit dem Handy über einem Kartenauswurf in einem Rutsch einscannst und
nach deinen eigenen Regeln sortieren lässt.

Die App holt jeden Tag die Preise von Cardmarket und zeigt dir im Dashboard, was deine Sammlung wert ist und wie sie
sich entwickelt. Sie kennt außerdem deine Decks, sagt dir, welche Karten dir noch fehlen, und zeigt dir, wenn eine
davon unter deinen Wunschpreis fällt. Dazu kommen eine Übersicht aller Secret Lairs, der Druck von Proxys als PDF und
Sensoren für Home Assistant.

## Was du brauchst

Neben Home Assistant auf einem Rechner mit `amd64` brauchst du einen Windows-PC mit NVIDIA-Grafikkarte im selben
Netzwerk, denn dort läuft die Erkennung der Karten. Den Installer dafür lädst du nach dem Start in der App unter
**„PC verbinden“** herunter. Für Fotos und Vorschaubilder empfiehlt sich ein Netzlaufwerk, das du in der Option
„Speicherort“ einträgst.

Wie du alles Schritt für Schritt einrichtest, steht im Reiter **Dokumentation**. Alle Funktionen mit Bildern findest
du auf [GitHub][repo].

MTG Binder is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed by Wizards.
Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC.

[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg
[sprache-shield]: https://img.shields.io/badge/Oberfl%C3%A4che-Deutsch-informational.svg
[regal]: https://raw.githubusercontent.com/JustTheD-lab/MTG-HA/main/bilder/regal.png
[repo]: https://github.com/JustTheD-lab/MTG-HA
