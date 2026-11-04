"""Täglicher Preisabruf im Add-on (Entscheidung des Nutzers: das Add-on holt die Preise selbst, unabhängig vom PC).

Ein Hintergrund-Thread schaut stündlich nach, nach `anstossen()` (neue Karten) schon nach etwa 30 Sekunden:
- Ist der letzte Abruf älter als 20 Stunden (oder gibt es noch keinen), wird die Datei neu geladen.
- Sonst: Haben neue Karten (Erkennung, „Karte hinzufügen“) Produkte ohne Preis für den neuesten Tag, werden
  sie aus der schon geladenen Datei nachgetragen (ohne neuen Download).
Fehler (kein Internet, Cardmarket nicht erreichbar) landen im Protokoll; beim nächsten Blick wird es neu versucht.
"""
from __future__ import annotations

import logging
import re
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from binderapp.db.datenbank import verbinden, version
from binderapp.preise import abruf

TAKT = 3600            # Sekunden zwischen zwei Blicken
ALT = 20 * 3600        # ab diesem Alter wird neu geladen

log = logging.getLogger("binderapp.preise")


def was_tun(con, datei: Path) -> str | None:
    """"laden", "nachtragen" oder None."""
    if version(con) < 4:
        return None
    z = con.execute("SELECT tag, abgerufen FROM preisabruf ORDER BY abgerufen DESC LIMIT 1").fetchone()
    if z is None:
        return "laden"
    alter = (datetime.now(timezone.utc) - datetime.fromisoformat(z[1]).replace(tzinfo=timezone.utc)).total_seconds()
    if alter > ALT:
        return "laden"
    neuester = con.execute("SELECT MAX(tag) FROM preisabruf").fetchone()[0]
    fehlt = con.execute("SELECT 1 FROM kartendaten d WHERE d.cardmarket_id IS NOT NULL AND NOT EXISTS"
                        " (SELECT 1 FROM preis p WHERE p.cardmarket_id = d.cardmarket_id AND p.tag = ?) LIMIT 1",
                        (neuester,)).fetchone()
    offen = con.execute("SELECT 1 FROM kartendaten WHERE cardmarket_id IS NULL LIMIT 1").fetchone()
    if version(con) >= 8 and not (fehlt or offen):   # Decklisten: neue Zeilen oder Produkte ohne Preis des Tages
        offen = con.execute("SELECT 1 FROM deck_karte WHERE produkte IS NULL LIMIT 1").fetchone()
        # nur Zeilen ganz ohne Preis (bei „nur Name“ haben manche alte Drucke nie einen; das soll nicht stündlich
        # die große Datei lesen lassen)
        fehlt = con.execute("SELECT 1 FROM deck_karte k WHERE k.produkte > 0 AND NOT EXISTS (SELECT 1 FROM deck_produkt"
                            " d JOIN preis p ON p.cardmarket_id = d.cardmarket_id AND p.tag = ? WHERE"
                            " d.deck_karte_id = k.id) LIMIT 1", (neuester,)).fetchone()
    return "nachtragen" if (fehlt or offen) and datei.is_file() else None


_NACHGETRAGEN: dict[str, tuple] = {}   # Datenbank → Stand beim letzten Nachtragen


def _stand(con, tag: str | None, katalog: Path | None = None) -> tuple:
    """Kennzeichen für „schon nachgetragen“: Tag und Zahl der Produkte (Sammlung und Decks), dazu der letzte
    Scryfall-Abgleich des Katalogs (neue Cardmarket-Produkte). Produkte, die in der Preisdatei nie vorkommen,
    lösen so kein stündliches Lesen der großen Datei aus, neue Karten und ein neuer Abgleich aber schon."""
    from binderapp.katalog.abgleich import letzter_abgleich
    teile = [tag, letzter_abgleich(katalog) if katalog else None,
             con.execute("SELECT COUNT(*) FROM kartendaten WHERE cardmarket_id IS NOT NULL").fetchone()[0],
             con.execute("SELECT COUNT(*) FROM kartendaten WHERE cardmarket_id IS NULL").fetchone()[0]]
    if version(con) >= 8:
        teile += [con.execute("SELECT COUNT(*) FROM deck_produkt").fetchone()[0],
                  con.execute("SELECT COUNT(*) FROM deck_karte WHERE produkte IS NULL").fetchone()[0]]
    return tuple(teile)


def einmal(datenbank: Path, ordner: Path, katalog: Path | None) -> dict | None:
    datei = Path(ordner) / "price_guide_1.json"
    with closing(verbinden(datenbank, nur_lesen=True)) as con:
        aufgabe = was_tun(con, datei)
        neuester = con.execute("SELECT MAX(tag) FROM preisabruf").fetchone()[0] if aufgabe == "nachtragen" else None
        stand = _stand(con, neuester, katalog) if aufgabe == "nachtragen" else None
    if aufgabe is None:
        return None
    if aufgabe == "nachtragen" and _NACHGETRAGEN.get(str(datenbank)) == stand:
        return None   # mit genau diesem Stand schon nachgetragen
    if aufgabe == "nachtragen":
        kopf = datei.read_bytes()[:2000]
        m = re.search(rb'"createdAt"\s*:\s*"(\d{4}-\d{2}-\d{2})', kopf)
        if not m or m.group(1).decode() != neuester:
            return None   # die gespeicherte Datei gehört zu einem anderen Tag: auf den nächsten Abruf warten
        ergebnis = abruf.abrufen(datenbank, ordner, katalog, datei=datei, nachtragen=True)
        with closing(verbinden(datenbank, nur_lesen=True)) as con:   # Stand nach dem Nachtragen merken
            _NACHGETRAGEN[str(datenbank)] = _stand(con, neuester, katalog)
    else:
        ergebnis = abruf.abrufen(datenbank, ordner, katalog)
    ergebnis["aufgabe"] = aufgabe
    log.info("Preise %s (%s): %s Produkte der Sammlung", ergebnis["tag"], aufgabe, ergebnis["gespeichert"])
    return ergebnis


def offene_einsortieren(datenbank: Path) -> int:
    """Nach dem Preisnachtrag: Karten, die im Scan-Eingang auf einen Preis warten, nach den Sortierregeln
    einsortieren (binderapp.sortieren). Gibt die Zahl der verschobenen Karten zurück."""
    from binderapp.db.datenbank import schreiben
    from binderapp.sortieren import einsortieren
    with closing(verbinden(datenbank)) as con:
        nutzer = einsortieren.offene_nutzer(con)
        if not nutzer:
            return 0
        with schreiben(con):
            verschoben = sum(len(einsortieren.einsortieren(con, n)) for n in nutzer)
    if verschoben:
        log.info("Nach dem Preisnachtrag einsortiert: %s Karten", verschoben)
    return verschoben


BUENDELN = 30          # Sekunden: nach einem Anstoß noch so lange warten, damit ein ganzer Lauf zusammenkommt

_aktiv: "Preisabruf | None" = None


def anstossen() -> None:
    """Neue Karten (Erkennung, Bulk-Scan, Druck ändern, Karte hinzufügen): Preise bald nachtragen statt erst beim
    nächsten stündlichen Blick. Ohne laufenden Preisabruf (PC, Tests) geschieht nichts."""
    if _aktiv is not None:
        _aktiv.anstossen()


class Preisabruf(threading.Thread):
    def __init__(self, datenbank: Path, ordner: Path, katalog: Path | None):
        super().__init__(daemon=True, name="preisabruf")
        self.datenbank, self.ordner, self.katalog = Path(datenbank), Path(ordner), katalog
        self.halt = threading.Event()
        self.stoss = threading.Event()

    def anstossen(self) -> None:
        self.stoss.set()

    def anhalten(self) -> None:
        self.halt.set()
        self.stoss.set()

    def run(self) -> None:
        global _aktiv
        _aktiv = self
        while not self.halt.is_set():
            try:
                einmal(self.datenbank, self.ordner, self.katalog)
            except Exception as fehler:   # nie den Server stören; nächster Versuch beim nächsten Blick
                log.warning("Preisabruf fehlgeschlagen: %s", fehler)
            try:
                offene_einsortieren(self.datenbank)
            except Exception as fehler:
                log.warning("Einsortieren nach dem Preisabruf fehlgeschlagen: %s", fehler)
            if self.stoss.wait(TAKT) and not self.halt.is_set():
                self.halt.wait(BUENDELN)   # weitere neue Karten abwarten, dann einmal nachtragen
            self.stoss.clear()
