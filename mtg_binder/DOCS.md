# MTG Binder

Bücherregal, Lesetisch, Kisten und Prüfansicht deiner Magic-Binder, direkt im Seitenmenü von Home Assistant
(auch unterwegs über Nabu Casa). Sichtbar nur für Admins; ein eigenes Passwort gibt es im Add-on nicht.

Die Erkennung der Karten läuft weiter auf dem PC mit Grafikkarte. Hochgeladene Fotos warten im Add-on, bis der
PC sie über Port 8099 abholt (nur im Heimnetz, nur mit Schlüssel).

## Erkennung am PC verbinden

1. In der App im Regal auf **„PC verbinden“** tippen und **„Schlüssel erzeugen“**. Der Schlüssel erscheint nur
   einmal; geht er verloren, erzeugst du einfach einen neuen (der alte gilt dann nicht mehr).
2. Am PC `MTG-Binder-Erkennung.exe` im Projektordner starten. Beim ersten Start fragt sie nach
   **Adresse** (`http://<IP von Home Assistant>:8099`) und **Schlüssel**.
3. Das Symbol neben der Uhr zeigt den Stand: grau = aus, grün = wartet, messing = erkennt, rot = Fehler.
   Rechtsklick: Jetzt abholen, Pause, Betriebsart (im Hintergrund nachsehen oder abarbeiten und beenden),
   Katalog senden, Verbindung testen, Einstellungen, Protokoll, Mit Windows starten, Beenden.
4. Beim Start schickt die Erkennung den Kartenkatalog (etwa 126 MB), wenn das Add-on keinen oder einen älteren
   hat. Damit funktionieren „Karte hinzufügen“ und „Druck ändern“ auch, wenn der PC aus ist.

## Einbau (über das Add-on-Repository)

1. Am PC das Repository füllen: `python -m binderapp.addon.paket --repo <Ordner des Repositorys>`, dann in
   GitHub Desktop committen und pushen.
2. In Home Assistant: Einstellungen → Add-ons → Add-on-Store → Menü oben rechts → „Repositories“ → Adresse des
   Repositorys eintragen. „MTG Binder“ erscheint im Store → Installieren (baut das Abbild, dauert ein paar Minuten).
3. Reiter „Konfiguration“: **Speicherort** auf einen Ordner des Netzlaufwerks setzen, z. B.
   `/share/netzlaufwerk/mtg-binder`. Das Netzlaufwerk bindest du vorher unter Einstellungen → System →
   Speicher → „Netzwerkspeicher hinzufügen“ ein (Verwendung „Freigabe“ oder „Medien“).
4. Starten und „In der Seitenleiste anzeigen“ einschalten.

Ohne Repository geht es weiter als lokales Add-on: `python -m binderapp.addon.paket` und den Ordner
`work/addon/mtg_binder` per Samba nach `addons/` kopieren.

## Preise

Das Add-on lädt einmal täglich den Price Guide von Cardmarket (etwa 25 MB) und speichert für die Karten deiner
Sammlung **Low** und **Durchschnitt 7 Tage** (bei Foil-Karten die Foil-Preise). Jede Karte zeigt beides im unteren
Rand („ab … · Ø …“), Binder und Kisten ihren Gesamtwert (Durchschnitt 7 Tage). Die große Kartenansicht zeigt die
Veränderung gegen Vortag, 3 und 30 Tage – erst, wenn genug Tage gesammelt sind, vorher „–“.
Damit die Karten ihrem Cardmarket-Produkt zugeordnet werden, braucht das Add-on den Katalog ab Version 2: Den
schickt die Erkennung am PC beim nächsten Start automatisch.

## Bulk-Scan mit dem Card Slinger

1. Handy über dem Card Slinger befestigen, im Regal auf **„Scannen“** tippen, dann **„Scan starten“**.
   Die Kamera geht im Browser nur über HTTPS: Home Assistant am Handy über die **Nabu-Casa-Adresse** öffnen
   (über `http://<IP>:8123` bleibt die Kamera gesperrt).
2. Den Rahmen mit dem Regler so einstellen, dass er die Karte in der Scanzone umschließt. Zu Beginn muss die
   Scanzone leer sein: Das erste ruhige Bild merkt sich das Add-on als „leer“.
3. Karten einwerfen. Das Handy schickt laufend ein kleines Graustufenbild der Scanzone; das Add-on erkennt
   Bewegung, wartet, bis die Karte still liegt, und löst dann genau ein Foto aus (Klick-Ton, Zähler). Eine Karte
   sollte etwa eine Sekunde liegen, bevor die nächste kommt.
4. „Letztes verwerfen“ nimmt ein falsches Foto zurück, „Jetzt auslösen“ erzwingt eines. „Fertig“ beendet den
   Durchgang. Die Fotos liegen unverändert unter `fotos/scan/<Durchgang>/` und erscheinen im **Scan-Eingang**.
   Gelöscht wird dort nichts, verworfene Fotos lassen sich zurückholen.

## Wo liegt was?

| Was | Wo |
|---|---|
| Datenbank | im Add-on (`/data/binder.sqlite`), in den Backups von Home Assistant |
| Kartenkatalog, Schlüssel (nur als Hash) | im Add-on (`/data/katalog.sqlite`, `/data/pc_schluessel`) |
| Sicherungen | im Add-on (`/data/sicherung`), die jüngsten 10 bleiben; vor jedem Update automatisch |
| Fotos und Vorschaubilder | auf dem Netzlaufwerk unter dem Speicherort (`fotos/`, `bilder/`), **nicht** in den Backups |
| Scan-Fotos | auf dem Netzlaufwerk unter `fotos/scan/`, Vorschauen unter `bilder/scan/` |

Ist das Netzlaufwerk nicht erreichbar, kannst du weiter blättern, suchen und prüfen, nur ohne Bilder.
Hochladen ist dann gesperrt, oben steht ein Hinweis.

## Testkopie einspielen

Eine Datenbank vom PC legst du als `einspielen/binder.sqlite` in den Speicherort und startest das Add-on neu.
Die vorhandene Datenbank wird vorher gesichert, die eingespielte Datei danach umbenannt
(`binder-eingespielt-<Datum>.sqlite`). Die Bilder dazu kopierst du nach `bilder/`, die Fotos nach `fotos/`.

## Update

Repository neu füllen (`--repo …`) und pushen. Home Assistant zeigt „Update verfügbar“, sobald die Version in
`config.yaml` gestiegen ist (sofort über „Nach Updates suchen“ im Store-Menü), mit den Änderungen aus
`CHANGELOG.md`. Beim ersten Start einer neuen Version wird die Datenbank gesichert.
