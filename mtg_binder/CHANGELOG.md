# Änderungen

## 0.14.2
- Preisverlauf begrenzt: Die Preise bleiben Tag für Tag gespeichert. Erreichen sie etwa 200 MB, löscht das Add-on
  beim täglichen Abruf die ältesten Tage; die letzten 40 Tage bleiben immer.

## 0.14.1
- Binder bearbeiten: Taschen mit Rückseite (und unklare Taschen ohne Karte) lassen sich jetzt bearbeiten.
  Antippen legt eine Karte hinein, das Werkzeug „Löschen“ markiert die Tasche als leer, beim Verschieben sind
  sie ein Ziel. Alles mit Rückgängig.

## 0.14.0
- Bulk-Scan: je Durchgang „Alle Karten Foil“ und „Sets wählen“. Foil-Karten werden wie gewohnt zu Posten
  zusammengefasst und nach Foil-Preis bewertet.
- Sets wählen: Suche nach Name oder Code; zu einem Set schlägt die App die zugehörigen Codes vor (Hauptset,
  Commander, Bonus-Reihen und Special Guests vom selben Tag, z. B. bei Reality Fracture FRA, FRC und SPG).
- Die Karte erkennt die App wie bisher; nur der Druck wird unter den gewählten Sets gesucht. Liegt eine Karte in
  keinem davon, kommt sie als „unsicher“ zur Prüfung. Ein neuer Durchgang beginnt ohne Foil und ohne Auswahl.
- Datenbank Version 9 (zwei neue Spalten je Durchgang; die Sicherung entsteht beim Update wie immer).

## 0.13.0
- Farbrad für Binder und Kisten: Neben den festen Farben lässt sich jede Farbe wählen (Farbkreis, Helligkeit,
  Farbcode). Gilt beim Anlegen eines Binders, unter „Binder bearbeiten“, bei „Neue Kiste“ und bei „Kiste
  bearbeiten“ (früher „Umbenennen“, jetzt mit Farbe).
- Farbige Kisten: Die ganze Kiste bekommt die Farbe, das Etikett bleibt Messing. Messing, Silber und Kupfer sind
  weiter dunkle Kisten mit Metalletikett. Schrift auf hellen Farben wird automatisch dunkel.

## 0.12.0
- Nur noch das Add-on fragt Scryfall: Es merkt sich neue und geänderte Karten, die Erkennung am PC holt sie über
  das Heimnetz ab (Port 8099, mit Schlüssel) und fragt Scryfall nicht mehr selbst. Die Bilder neuer Karten lädt
  sie weiter direkt vom Bildserver von Scryfall.
- Junge Drucke ohne fertigen Scan fragt das Add-on täglich nach, damit der PC das Bild bekommt, sobald es fertig
  ist. Beim ersten Abgleich nach dem Update kommen alle Drucke der letzten 120 Tage einmal in die Liste.

## 0.11.0
- Neue Karten von Scryfall: Das Add-on gleicht seinen Kartenkatalog täglich selbst mit Scryfall ab und holt nur,
  was neu ist (alle Sprachen). Junge Drucke ohne Cardmarket-Produkt werden bis zu 120 Tage lang nachgefragt,
  danach haben sie Preise. Nichts wird gelöscht.
- Die Erkennung am PC gleicht ihren Katalog ebenso ab, wenn keine Fotos warten, und nimmt neue Karten in das
  Namensverzeichnis und den Bildindex auf (nur fertige Scans, keine Vorschaubilder).

## 0.10.0
- Deckbau: Ein Deck ist jetzt ein eigener Ort wie eine Kiste.
  - „Deck bauen“ zeigt eine Entnahmeliste nach Ort (Kisten nach Fach, Binder nach Seite und Platz). Die App schlägt
    das Exemplar vor (genauer Druck zuerst, dann Kisten vor Bindern), ein anderes lässt sich wählen.
  - Abhaken merkt nur vor (bleibt gespeichert, auch beim Gerätewechsel); „Fertig“ legt alle abgehakten Karten in
    einem Schritt ins Deck, mit Rückgängig. Tauchen fehlende Karten später auf, bietet das Deck einen Nachtrag an.
  - „Zerlegen“ legt die Karten an ihren alten Platz zurück (sonst Herkunftskiste, Sortierregel oder eine Kiste
    nach Wahl). Ein Deck mit Karten lässt sich erst nach dem Zerlegen löschen.
  - „Deck zur Sammlung hinzufügen“ (z. B. Precon): legt alle Karten der Liste neu im Deck an (Basics ja, Proxys
    nein).
- Deckpreise: Wert der Deckliste, Kosten der Fehlt-Liste, Wert im Deck mit Veränderung zum Vortag, Preis je Karte
  (sortierbar). Steht nur der Name in der Liste, zählt der günstigste Druck. Der tägliche Preisabruf speichert
  dafür auch die Karten der Decklisten.
- Karten in Decks zählen zur Sammlung; in anderen Decks stehen sie als „steckt in Deck X“. Gesamtsammlung und
  Kartenansicht kennen den Ort „Deck“. Home Assistant: je Deck Sensoren für Wert und Karten.
- Die Datenbank wird beim Start auf Version 8 gebracht, vorher legt das Add-on eine Sicherung an.

## 0.9.1
- Archidekt-Import korrigiert: Es zählt nur die erste Kategorie einer Karte (wie bei Archidekt). Karten mit einer
  zusätzlichen Kategorie „Maybeboard“ und Karten in eigenen Kategorien wie „Tokens“ fehlten bisher. Schon
  importierte Decks mit „Neu laden“ ergänzen.

## 0.9.0
- Decks: neuer Regalboden „Decks“.
  - Import per Archidekt-Link (öffentliche oder ungelistete Decks) oder als eingefügter Export-Text (Moxfield,
    Archidekt). Nur das Hauptdeck mit Commander.
  - Abgleich mit der Sammlung: vorhanden, wo die Karte liegt (Binder, Seite, Platz oder Kiste), was fehlt. Der
    Abgleich zählt wahlweise den Namen oder nur den genauen Druck.
  - Schalter „Basic Lands auslassen“; einzelne Karten als Proxy markieren.
  - Fehlt-Liste zum Kopieren für die Cardmarket-Wunschliste.
  - „Neu laden“ holt Änderungen aus Archidekt, vorher mit Vorschau.
- Die Datenbank wird beim Start auf Version 7 gebracht, vorher legt das Add-on eine Sicherung an.

## 0.8.0
- Binder bearbeiten: Knopf „Bearbeiten“ im Lesetisch mit den Werkzeugen Foil, Verschieben und Löschen.
  - Verschieben geht in eine andere Tasche (auch auf einer anderen Seite), in einen anderen Binder (nächste freie
    Tasche) oder in eine Kiste. Auf einer besetzten Tasche tauschen die Karten die Plätze.
  - Löschen entfernt die Karte ganz aus der Sammlung.
  - Ein Tipp auf eine leere Tasche fügt eine Karte aus dem Katalog hinzu.
  - Jede Aktion lässt sich rückgängig machen.
- Foil-Modus: Ein Tipp schaltet ein Exemplar um, ein Stapel teilt sich dabei. Dazu „Ganze Seite Foil“.
  - Den Foil-Modus gibt es auch in Kiste und Gesamtsammlung.
  - Foil-Karten zeigen einen Regenbogen-Schimmer und einen Stern.

## 0.7.0
- Werte an Home Assistant per MQTT mit Discovery (Gerät „MTG“): Gesamtwert mit Veränderung zum Vortag, nach 3 und
  30 Tagen (Euro und Prozent), Kartenzahlen, je Binder und Kiste ein Wert- und ein Kartensensor, Top 5 teuerste
  Karten, Top 5 Gewinner und Verlierer, Warteschlange, Karten zur Prüfung, PC verbunden, letzter Scan und letzter
  Preisabruf.
- Braucht das Mosquitto-Add-on; den Zugang holt das Add-on selbst beim Supervisor. Abschaltbar in der Konfiguration
  („Werte an Home Assistant (MQTT)“). Ist das Add-on aus, zeigen die Sensoren „nicht verfügbar“.
- Nur lesend, kein neuer Port, keine neue Abhängigkeit.

## 0.6.3
- Sortierung „Preis (Ø 7 Tage)“ in der Gesamtsammlung und in jeder Kiste (Foil-Karten mit Foil-Preis, ohne Preis
  zuletzt).
- Neue Ansicht „Sets“ (Regal unter „Sammlung“ oder Kopf der Gesamtsammlung): je Set die Vollständigkeit nach
  Drucken (jede Sammlernummer, auch Borderless und Showcase) und nach Namen, mit Karten und Wert. Im Set alle
  Drucke oder Namen, gefiltert nach vorhanden oder fehlend. Braucht den Katalog im Add-on.

## 0.6.2
- Kisten lassen sich jetzt auch mit Karten löschen („Löschen“ in der Kiste, Name eintippen und noch einmal
  bestätigen). Karten und Fächer werden mit Historie gelöscht; eine Kiste, in die eine Sortierregel einsortiert,
  bleibt stehen. Der Scan-Eingang entsteht beim nächsten Scan neu.

## 0.6.1
- Fotos hochladen: Mehrere Fotos auf einmal kommen nach dem Dateinamen sortiert in die Liste (IMG_2 vor IMG_10);
  vorher bestimmten Windows-Dateidialog und Ziehen die Reihenfolge.
- „Binder löschen“ unter „Seiten verwalten“ (Name eintippen und noch einmal bestätigen): löscht Binder, Seiten und
  Karten mit Historie. Die Originalfotos bleiben liegen und lassen sich ohne Doppel-Hinweis neu hochladen.

## 0.6.0
- Sortierregeln: frei einstellbare Regeln („Wenn … → Kiste“) mit Farbe, Manawert, Typ, Seltenheit, Set, Art des
  Sets, Special Print, Promo, Foil, Sprache, Name und Preis; Reihenfolge = Vorrang. Probe für eine Karte.
- Fächer je Kiste und Gruppierung „Fach“ in der Kistenansicht.
- Bulk-Scan: sicher erkannte Karten wandern sofort in ihre Kiste (je Durchgang abschaltbar), unsichere nach dem
  Prüfen; fehlt ein Wert (z. B. der Preis), wartet die Karte im Scan-Eingang, ohne Preis bei Cardmarket zählt sie
  als 0 €. Ziel oder Wartegrund im Scan-Eingang.
- Datenbank Version 6 (vorher automatische Sicherung), Katalog Version 3 (Special-Print-Merkmale).

## 0.5.2
- Preise neuer Karten (Erkennung, Bulk-Scan, Druck ändern, Karte hinzufügen) erscheinen nach etwa einer halben
  Minute statt erst beim nächsten stündlichen Blick.

## 0.5.1
- Bulk-Scan: Eine gleichnamige Kiste aus einem ManaBox-Import wird nicht mehr zum Scan-Eingang (dann
  „Scan-Eingang 2“). Link im Scan-Eingang auch für Karten, die inzwischen nicht mehr in einer Kiste liegen.

## 0.5.0
- Bulk-Scan: Läuft die Erkennung am PC, holt sie auch die Scan-Fotos ab und erkennt je Foto eine Karte. Erkannte
  Karten kommen in die Kiste „Scan-Eingang“ (gleiche sichere Drucke als ein Posten mit Anzahl, unsichere einzeln
  zum Prüfen). Der Scan-Eingang und die Scan-Seite zeigen die erkannten Namen.

## 0.4.1
- Der PC kann Scan-Fotos als Kopie laden (Entwicklungsset für die Erkennung der Einzelfotos), ohne ihren Stand
  im Add-on zu ändern.

## 0.4.0
- Bulk-Scan mit dem Card Slinger: „Scannen“ im Regal filmt die Scanzone mit dem Handy, das Add-on entscheidet,
  wann ein Foto entsteht (eine Karte je Wurf), und legt die Fotos in den Scan-Eingang auf dem Netzlaufwerk.
  Verwerfen und Zurückholen im Scan-Eingang. Die Erkennung der Einzelfotos am PC folgt.
- Datenbank Version 5 (vorher automatische Sicherung).

## 0.3.0
- Preise von Cardmarket: Low und Durchschnitt 7 Tage auf jeder Karte, Gesamtwert je Binder, Kiste und Sammlung,
  Veränderung gegen Vortag, 3 und 30 Tage in der Kartenansicht. Das Add-on lädt die Preise täglich selbst.
- Datenbank Version 4 (vorher automatische Sicherung).

## 0.2.0
- Erkennung am PC über Port 8099 mit Schlüssel („PC verbinden“ im Regal), Kartenkatalog im Add-on.
- Bild neuer Karten direkt von Scryfall, bis der PC die Vorschau liefert.

## 0.1.0
- Erste Fassung: Oberfläche über Ingress, Fotos und Bilder auf dem Netzlaufwerk, Testkopie einspielen.
