# Änderungen

## 0.6.0
- Sortierregeln: frei einstellbare Regeln („Wenn … → Kiste“) mit Farbe, Manawert, Typ, Seltenheit, Set, Art des
  Sets, Special Print, Promo, Foil, Sprache, Name und Preis; Reihenfolge = Vorrang. Probe für eine Karte.
- Fächer je Kiste und Gruppierung „Fach“ in der Kistenansicht.
- Bulk-Scan: sicher erkannte Karten wandern sofort in ihre Kiste (je Durchgang abschaltbar), unsichere nach dem
  Prüfen; fehlt ein Wert (z. B. der Preis), wartet die Karte im Scan-Eingang. Ziel oder Wartegrund im Scan-Eingang.
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
