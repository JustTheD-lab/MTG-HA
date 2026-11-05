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

## Neue Karten von Scryfall (täglich)

Das Add-on gleicht seinen Kartenkatalog einmal täglich selbst mit Scryfall ab, auch wenn der PC aus ist. Es holt
**nur Neues**: Scryfall listet alle Karten in einer Änderungsliste (das dauert wegen der Grenzen von Scryfall
einige Minuten im Hintergrund); vollständig geladen werden nur Karten, die der Katalog noch nicht kennt, in allen
Sprachen. Junge Drucke ohne Cardmarket-Produkt fragt das Add-on bis zu 120 Tage lang täglich nach, denn Scryfall
trägt das Produkt bei neuen Sets oft erst später nach; danach bekommen die Karten ihren Preis.
Ohne Katalog im Add-on gibt es keinen Abgleich (den ersten schickt die Erkennung am PC). Fehler, etwa ohne
Internet, stehen im Protokoll; der nächste Versuch folgt nach einer Stunde.

Nur das Add-on fragt Scryfall. Neue und geänderte Karten merkt es sich in einer Liste. Die Erkennung am PC holt
sie stündlich über das Heimnetz ab (Port 8099, mit Schlüssel), wenn keine Fotos warten, und trägt sie in ihren
eigenen Katalog ein. Danach lädt sie die Bilder neuer Karten direkt vom Bildserver von Scryfall und hängt sie an
den Bildindex an. Neue Karten werden so auch auf Fotos erkannt. Bilder aus der Vorschauzeit eines Sets nimmt sie
nicht, sondern erst den fertigen Scan; dafür fragt das Add-on junge Drucke ohne fertigen Scan täglich nach.
Beim ersten Abgleich nach dem Update kommen alle Drucke der letzten 120 Tage einmal in die Liste. Ist das Add-on
nicht erreichbar, versucht es die Erkennung eine Stunde später wieder.

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
Neue Karten bekommen ihren Preis etwa eine halbe Minute nach der Erkennung aus der Datei des Tages.
Der Verlauf bleibt Tag für Tag in der Datenbank. Erreicht er etwa 200 MB, fallen die ältesten Tage weg; die letzten
40 Tage bleiben immer.
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
5. Läuft die Erkennung am PC, holt sie die Scan-Fotos ab (nach den Binderseiten) und legt die erkannten Karten in
   die Kiste **„Scan-Eingang“**. Gleiche sichere Drucke werden zu einem Posten mit Anzahl zusammengefasst,
   unsichere bleiben einzeln und stehen in der Prüfansicht. Ist der PC aus, warten die Fotos im Scan-Eingang.
   Der Name erscheint meist schon nach ein bis zwei Sekunden.

**Foil und Sets je Durchgang:** Am Scan-Bildschirm stellst du vor dem Start oder während des Scans ein:
- **„Alle Karten Foil“:** Jede danach erkannte Karte ist Foil.
- **„Sets wählen“:** Nach Name oder Code suchen, ein Set antippen. Die App hakt die zugehörigen Codes vor (Hauptset,
  Commander, Bonus-Reihen, Special Guests vom selben Tag), du kannst weitere dazunehmen oder abwählen. Die Karte
  wird wie immer erkannt; nur der Druck wird unter diesen Sets gesucht. Gibt es die Karte in keinem davon, kommt
  sie als „unsicher“ zur Prüfung.

Ein neuer Durchgang beginnt wieder ohne Foil und ohne Set-Auswahl.

## Sortierregeln

Unter **„Sortierregeln“** (Regal, Kisten-Boden oder Scan-Eingang) legst du fest, in welche Kiste eine Karte
gehört, zum Beispiel „Preis (Ø 7 Tage) ab 5 € → Spezial“ oder „Farbe genau Rot → Rot“.

- Die Regeln werden **von oben nach unten** geprüft, die erste passende bestimmt die Kiste. Alle Bedingungen einer
  Regel müssen zutreffen. Passt keine, bleibt die Karte im Scan-Eingang.
- Bedingungen: Farbe, Farbidentität, Manawert, Typ, Typzeile, Seltenheit, Set, Art des Sets (z. B. Secret Lair),
  Special Print (Borderless, Showcase, Extended Art, Retro-Rahmen, Full Art, Foil-Sonderarten), Promo, Foil,
  Sprache, Name und Preis. Jede Bedingung lässt sich verneinen.
- **Fächer** einer Kiste (Knopf „Fächer“ in der Kiste), zum Beispiel „Länder“, „6+“ und „1–5“: Die Kistenansicht
  gruppiert danach („Gruppieren: Fach“), der Scan-Eingang zeigt das Fach zu jeder Karte.
- Beim Bulk-Scan räumt das Add-on jede **sicher** erkannte Karte sofort in ihre Kiste („Automatisch einsortieren“
  am Scan-Bildschirm, je Durchgang abschaltbar). Unsichere Karten warten, bis du sie in der Prüfansicht bestätigst.
  Fehlt ein Wert, den eine Regel braucht (meist der Preis einer neuen Karte), wartet die Karte, bis er da ist; sie
  landet also nie geraten in einer Kiste. Gibt es endgültig keinen Preis (kein Produkt bei Cardmarket oder kein
  Eintrag in der Preisdatei des Tages), zählt die Karte als 0 €. Jedes Einräumen lässt sich zurücknehmen.
- Für die Special-Print-Merkmale braucht das Add-on den Katalog Version 3. Die Erkennung am PC schickt ihn beim
  nächsten Start selbst (oder „Katalog senden“ im Symbol neben der Uhr).

## Decks

- Im Regal unter „Decks“ → „Neues Deck“. Archidekt: den Link des Decks einfügen (das Deck muss öffentlich oder
  ungelistet sein; das Add-on fragt Archidekt einmal ab). Moxfield und andere: „Export → Text“ kopieren und einfügen.
- Die Deckansicht zeigt je Karte, ob sie da ist und wo sie liegt; fehlende Karten lassen sich als Text für die
  Cardmarket-Wunschliste kopieren. Basic Lands auslassen und Proxys markieren geht jederzeit.
- Braucht den Kartenkatalog im Add-on (schickt die Erkennung am PC).
- Deck bauen: „Deck bauen“ zeigt, welche Karte wo liegt; abhaken und am Ende „Fertig“. Die Karten liegen danach im
  Deck (eigener Ort). „Zerlegen“ legt sie an den alten Platz zurück. Für ein gekauftes fertiges Deck gibt es
  „Deck zur Sammlung hinzufügen“.
- Preise je Deck: Wert der Liste, Kosten der Fehlt-Liste und Wert im Deck (Cardmarket Ø 7 Tage, bei Karten ohne
  angegebenen Druck der günstigste Druck).

## Werte in Home Assistant (MQTT)

- Mit laufendem **Mosquitto-Broker** und eingerichteter MQTT-Integration erscheinen die Sensoren von selbst unter
  dem Gerät **„MTG“**, z. B. `sensor.mtg_gesamtwert`, `sensor.mtg_karten_zur_prufung` oder
  `binary_sensor.mtg_pc_verbunden`. Je Binder und Kiste gibt es einen Wert- und einen Kartensensor.
- Den Zugang zum Broker holt das Add-on selbst beim Supervisor. Es liest die Datenbank nur und öffnet keinen Port.
- Status, Warteschlange und Prüfung kommen jede Minute. Werte und die Top-5-Listen kommen stündlich und sofort
  nach einem neuen Preistag.
- Ist das Add-on aus, zeigen die Sensoren „nicht verfügbar“.
- Abschalten: Konfiguration → „Werte an Home Assistant (MQTT)“.
- Liste der Sensoren und Beispiele für Dashboard-Karte und Benachrichtigungen: `HOME-ASSISTANT.md` im Ordner
  `mtg_binder` des Add-on-Repositorys.

## Wo liegt was?

| Was | Wo |
|---|---|
| Datenbank | im Add-on (`/data/binder.sqlite`), in den Backups von Home Assistant |
| Kartenkatalog, Schlüssel (nur als Hash) | im Add-on (`/data/katalog.sqlite`, `/data/pc_schluessel`) |
| Sicherungen | im Add-on (`/data/sicherung`), die jüngsten 10 bleiben; vor jedem Update automatisch |
| Tägliche Sicherungen | im Add-on (`/data/sicherung/taeglich`, die jüngsten 7) und auf dem Netzlaufwerk (`sicherung/`, die jüngsten 30) |
| Fotos und Vorschaubilder | auf dem Netzlaufwerk unter dem Speicherort (`fotos/`, `bilder/`), **nicht** in den Backups |
| Scan-Fotos | auf dem Netzlaufwerk unter `fotos/scan/`, Vorschauen unter `bilder/scan/` |
| Archiv aus „Datenbank löschen“ | auf dem Netzlaufwerk unter `archiv/<Zeit>/` |

Ist das Netzlaufwerk nicht erreichbar, kannst du weiter blättern, suchen und prüfen, nur ohne Bilder.
Hochladen ist dann gesperrt, oben steht ein Hinweis.

## Verwaltung

Der Knopf **„Verwaltung“** im Regal zeigt, wann zuletzt gesichert wurde, und meldet Fehler dabei.

**ManaBox-Import:** In ManaBox die Sammlung (oder eine Liste) als CSV exportieren, unter „Verwaltung“ → „Export wählen“
einlesen und je Eintrag das Ziel wählen:

- **Neue Kiste** oder **Vorhandene Kiste:** Die Karten kommen in diese Kiste.
- **Nach Sortierregeln:** Die Regeln verteilen die Karten. Was keine Regel trifft, bleibt im Scan-Eingang. Fehlt ein
  Wert, meist der Preis, wartet die Karte, bis er da ist.
- **Überspringen:** Für Listen und Decks vorgewählt, weil sie die Karten der Binder doppelt enthalten.

Der Probelauf zeigt das Ergebnis, ohne etwas zu schreiben. Ein späterer Export aktualisiert vorhandene Karten in ihrer
Kiste. Karten, die im Export fehlen, bleiben liegen und werden nur gemeldet; was du in der App geändert oder gelöscht
hast, bleibt so. „Rückgängig“ im Bericht nimmt den ganzen Import zurück, solange die Karten unverändert sind. Mit
gescannten Karten wird nichts zusammengezählt, mögliche Doppel stehen im Bericht. Für den Import braucht das Add-on den
Kartenkatalog.

**Sicherung zurückholen:** Eine Datei aus `sicherung/` (oder `archiv/<Zeit>/binder.sqlite`) als
`einspielen/binder.sqlite` in den Speicherort kopieren und das Add-on neu starten, siehe „Testkopie einspielen“.

**Datenbank löschen** ist für den Neubeginn gedacht und nicht rückgängig zu machen:

- Gelöscht werden Binder, Kisten, Fächer, Sortierregeln, Decks, Karten, Preise, die Historie und alle Vorschaubilder.
- Die Original-Fotos kommen wahlweise nach `archiv/<Zeit>/fotos/` oder werden endgültig gelöscht.
- Die Datenbank wird vorher immer nach `archiv/<Zeit>/binder.sqlite` gesichert. Dieser Ordner wird nie automatisch
  gelöscht.
- Kartenkatalog, Schlüssel des PCs und die Einstellungen des Add-ons bleiben.
- Gesperrt, solange die Erkennung am PC gerade rechnet oder das Netzlaufwerk nicht erreichbar ist.

## Testkopie einspielen

Eine Datenbank vom PC legst du als `einspielen/binder.sqlite` in den Speicherort und startest das Add-on neu.
Die vorhandene Datenbank wird vorher gesichert, die eingespielte Datei danach umbenannt
(`binder-eingespielt-<Datum>.sqlite`). Die Bilder dazu kopierst du nach `bilder/`, die Fotos nach `fotos/`.

## Update

Repository neu füllen (`--repo …`) und pushen. Home Assistant zeigt „Update verfügbar“, sobald die Version in
`config.yaml` gestiegen ist (sofort über „Nach Updates suchen“ im Store-Menü), mit den Änderungen aus
`CHANGELOG.md`. Beim ersten Start einer neuen Version wird die Datenbank gesichert.
