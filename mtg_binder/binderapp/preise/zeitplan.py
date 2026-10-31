"""Täglicher Preisabruf im Add-on (Entscheidung des Nutzers: das Add-on holt die Preise selbst, unabhängig vom PC).

Ein Hintergrund-Thread schaut stündlich nach:
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
    return "nachtragen" if (fehlt or offen) and datei.is_file() else None


def einmal(datenbank: Path, ordner: Path, katalog: Path | None) -> dict | None:
    datei = Path(ordner) / "price_guide_1.json"
    with closing(verbinden(datenbank, nur_lesen=True)) as con:
        aufgabe = was_tun(con, datei)
        neuester = con.execute("SELECT MAX(tag) FROM preisabruf").fetchone()[0] if aufgabe == "nachtragen" else None
    if aufgabe is None:
        return None
    if aufgabe == "nachtragen":
        kopf = datei.read_bytes()[:2000]
        m = re.search(rb'"createdAt"\s*:\s*"(\d{4}-\d{2}-\d{2})', kopf)
        if not m or m.group(1).decode() != neuester:
            return None   # die gespeicherte Datei gehört zu einem anderen Tag: auf den nächsten Abruf warten
        ergebnis = abruf.abrufen(datenbank, ordner, katalog, datei=datei)
    else:
        ergebnis = abruf.abrufen(datenbank, ordner, katalog)
    ergebnis["aufgabe"] = aufgabe
    log.info("Preise %s (%s): %s Produkte der Sammlung", ergebnis["tag"], aufgabe, ergebnis["gespeichert"])
    return ergebnis


class Preisabruf(threading.Thread):
    def __init__(self, datenbank: Path, ordner: Path, katalog: Path | None):
        super().__init__(daemon=True, name="preisabruf")
        self.datenbank, self.ordner, self.katalog = Path(datenbank), Path(ordner), katalog
        self.halt = threading.Event()

    def run(self) -> None:
        while not self.halt.is_set():
            try:
                einmal(self.datenbank, self.ordner, self.katalog)
            except Exception as fehler:   # nie den Server stören; nächster Versuch beim nächsten Blick
                log.warning("Preisabruf fehlgeschlagen: %s", fehler)
            self.halt.wait(TAKT)
