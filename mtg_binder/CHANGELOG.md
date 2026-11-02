# Änderungen

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
