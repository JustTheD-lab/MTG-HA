"""Entscheidet im Add-on, wann aus dem Livestream des Handys ein Einzelfoto entsteht (Entscheidung des Nutzers,
31.10.2026: Auslösen im Add-on).

Das Handy schickt laufend ein kleines Graustufenbild der Scanzone (etwa 60 × 84 Pixel, ein Byte je Pixel). Hier
wird nur verglichen, ohne OpenCV, Pillow oder NumPy, damit das Add-on klein bleibt:

1. Abweichung zum vorigen Bild groß → Bewegung (eine Karte fällt herein, merkt sich die stärkste Bewegung).
2. Danach eine Weile ruhig (Zeit und Zahl der Bilder) → die Karte liegt still.
3. Das erste ruhige Bild nach dem Start ist die leere Scanzone (Grundbild, kein Foto).
4. Nach einer Bewegung: ruhiges Bild deutlich anders als bei der letzten Aufnahme → auslösen. Fällt dieselbe
   Karte (z. B. ein Grundland) genau auf ihr Ebenbild, ist der Unterschied klein; dann löst eine starke Bewegung
   („Wurf“) trotzdem aus.

Abweichung: mittlerer Betrag der Pixeldifferenz nach Abzug der mittleren Helligkeit (die Belichtung des iPhones
schwankt). Alle Schwellen auf der Skala 0–255.
"""
from __future__ import annotations

from dataclasses import dataclass, field

KLEINSTE_SEITE, GROESSTE_SEITE = 16, 128


@dataclass(frozen=True)
class Einstellung:
    bewegung: float = 4.0       # Abweichung zum vorigen Bild, ab der sich etwas bewegt
    ruhe_s: float = 0.3         # so lange muss das Bild danach ruhig sein …
    ruhe_bilder: int = 2        # … über mindestens so viele gleiche Bilder
    neu: float = 8.0            # Abweichung zur letzten Aufnahme, ab der eine andere Karte daliegt
    wurf: float = 18.0          # so starke Bewegung: eine Karte ist hereingefallen, auch wenn sie gleich aussieht


@dataclass
class Ergebnis:
    zustand: str                # startet, bewegung, beruhigt, leer, bereit, ausloesen
    abweichung: float = 0.0     # zum vorigen Bild
    zur_aufnahme: float | None = None   # zum Bild der letzten Aufnahme (bzw. zur leeren Scanzone)
    grund: str | None = None    # bei ausloesen: neu, wurf oder hand (Knopf „Jetzt“)
    spitze: float = 0.0         # stärkste Bewegung seit dem letzten ruhigen Zustand

    @property
    def ausloesen(self) -> bool:
        return self.zustand == "ausloesen"

    def als_dict(self) -> dict:
        return {"zustand": self.zustand, "ausloesen": self.ausloesen, "abweichung": round(self.abweichung, 2),
                "zur_aufnahme": None if self.zur_aufnahme is None else round(self.zur_aufnahme, 2),
                "grund": self.grund, "spitze": round(self.spitze, 2)}


def abweichung(a: bytes, b: bytes) -> float:
    """Mittlerer Betrag der Differenz zweier gleich großer Graustufenbilder, Helligkeit ausgeglichen."""
    n = len(a)
    if n == 0 or n != len(b):
        raise ValueError("Bilder ungleich groß")
    versatz = (sum(b) - sum(a)) / n
    return sum(abs(x - y + versatz) for x, y in zip(a, b)) / n


def bild_pruefen(daten: bytes, breite: int, hoehe: int) -> None:
    if not (KLEINSTE_SEITE <= breite <= GROESSTE_SEITE and KLEINSTE_SEITE <= hoehe <= GROESSTE_SEITE):
        raise ValueError(f"Bildgröße {breite} × {hoehe} außerhalb {KLEINSTE_SEITE}–{GROESSTE_SEITE}")
    if len(daten) != breite * hoehe:
        raise ValueError(f"{len(daten)} Bytes passen nicht zu {breite} × {hoehe}")


@dataclass
class Ausloeser:
    """Zustand eines Scan-Durchgangs. `bild(daten, zeit)` je Bild aus dem Livestream; `zeit` in Sekunden."""
    einstellung: Einstellung = field(default_factory=Einstellung)
    groesse: tuple[int, int] | None = None
    vorher: bytes | None = None
    referenz: bytes | None = None       # leere Scanzone bzw. Bild der letzten Aufnahme
    zeit_vorher: float | None = None
    ruhig_seit: float | None = None
    ruhig_n: int = 0
    bewegt: bool = False
    spitze: float = 0.0
    aufnahmen: int = 0

    def zuruecksetzen(self) -> None:
        """Neues Grundbild beim nächsten ruhigen Zustand (z. B. nach Pause oder geänderter Scanzone)."""
        self.vorher = self.referenz = self.zeit_vorher = self.ruhig_seit = None
        self.groesse, self.ruhig_n, self.bewegt, self.spitze = None, 0, False, 0.0

    def bild(self, daten: bytes, zeit: float, breite: int, hoehe: int, *, jetzt: bool = False) -> Ergebnis:
        bild_pruefen(daten, breite, hoehe)
        if self.groesse != (breite, hoehe) or (self.zeit_vorher is not None and zeit < self.zeit_vorher):
            self.zuruecksetzen()            # andere Scanzone oder Uhr des Handys neu: von vorn
            self.groesse = (breite, hoehe)
        e = self.einstellung
        vorher, zeit_vorher = self.vorher, self.zeit_vorher
        self.vorher, self.zeit_vorher = daten, zeit
        if jetzt:                           # Knopf „Jetzt auslösen“: immer, das Bild wird die neue Referenz
            return self._ausloesen(daten, "hand", 0.0 if vorher is None else abweichung(vorher, daten))
        if vorher is None:
            return Ergebnis("startet")
        d = abweichung(vorher, daten)
        if d >= e.bewegung:
            self.ruhig_seit, self.ruhig_n = None, 0
            self.bewegt = True
            self.spitze = max(self.spitze, d)
            return Ergebnis("bewegung", d, spitze=self.spitze)
        if self.ruhig_seit is None:
            # Gleich wie das vorige Bild: still ist es also schon seit dem vorigen (wichtig bei 3 Bildern je Sekunde)
            self.ruhig_seit, self.ruhig_n = zeit_vorher, 1
        self.ruhig_n += 1
        if zeit - self.ruhig_seit < e.ruhe_s or self.ruhig_n < e.ruhe_bilder:
            return Ergebnis("beruhigt", d, spitze=self.spitze)
        if self.referenz is None:           # erstes ruhiges Bild: leere Scanzone
            self.referenz, self.bewegt, self.spitze = daten, False, 0.0
            return Ergebnis("leer", d)
        zur = abweichung(self.referenz, daten)
        if not self.bewegt:
            return Ergebnis("bereit", d, zur)
        spitze, self.bewegt, self.spitze = self.spitze, False, 0.0
        if zur >= e.neu:
            return self._ausloesen(daten, "neu", d, zur, spitze)
        if spitze >= e.wurf:
            return self._ausloesen(daten, "wurf", d, zur, spitze)
        return Ergebnis("bereit", d, zur, spitze=spitze)   # nur gewackelt, nichts Neues

    def _ausloesen(self, daten, grund, d, zur=None, spitze=0.0) -> Ergebnis:
        if zur is None and self.referenz is not None:
            zur = abweichung(self.referenz, daten)
        self.referenz, self.bewegt, self.spitze = daten, False, 0.0
        self.aufnahmen += 1
        return Ergebnis("ausloesen", d, zur, grund, spitze)
