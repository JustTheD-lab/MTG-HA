"""Tägliche Sicherung der Datenbank im Add-on, mit Kopie auf das Netzlaufwerk.

Die übrigen Sicherungen (`/data/sicherung`) entstehen nur bei Versionswechsel, Listen-Import und Abzug, und bei
vielen Updates reichen die jüngsten 10 nur wenige Tage zurück. Die täglichen liegen darum in einem eigenen Ordner,
den diese Zählung nicht berührt. Die Kopie auf dem Netzlaufwerk übersteht auch einen Ausfall des Mini-PCs.

    /data/sicherung/taeglich/binder-<Zeit>.sqlite    die jüngsten 7
    /data/sicherung/taeglich/stand.json              letzter Lauf, Fehler (zeigt die Verwaltung)
    <speicherort>/sicherung/binder-<Zeit>.sqlite     die jüngsten 30

Jede Sicherung wird geprüft, bevor sie eine ältere verdrängt. SQLite läuft auf dem Netzlaufwerk nie: Dorthin geht
nur eine fertige Datei.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from binderapp.db import datenbank

BEHALTEN_LOKAL = 7
BEHALTEN_NETZ = 30
ALT = 20 * 3600     # ab diesem Alter ist die nächste Sicherung fällig
TAKT = 3600         # Sekunden zwischen zwei Blicken
START = 60          # erster Blick kurz nach dem Start, nicht mitten darin
STAND = "stand.json"

log = logging.getLogger("binderapp.sicherung")


def _jetzt() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stand_lesen(ordner: Path) -> dict:
    try:
        return json.loads((Path(ordner) / STAND).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _stand_schreiben(ordner: Path, stand: dict) -> None:
    teil = Path(ordner) / (STAND + ".teil")
    teil.write_text(json.dumps(stand, ensure_ascii=False), encoding="utf-8")
    os.replace(teil, Path(ordner) / STAND)


def faellig(ordner: Path, jetzt: datetime | None = None) -> bool:
    letzte = stand_lesen(ordner).get("letzte")
    if not letzte:
        return True
    zeit = datetime.strptime(letzte, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return ((jetzt or datetime.now(timezone.utc)) - zeit).total_seconds() >= ALT


def pruefen(datei: Path) -> None:
    """Wirft ValueError, wenn die Datei keine heile Datenbank ist."""
    try:
        with closing(sqlite3.connect(f"file:{Path(datei).as_posix()}?mode=ro", uri=True)) as con:
            ergebnis = con.execute("PRAGMA quick_check").fetchone()[0]
    except sqlite3.DatabaseError as f:
        raise ValueError(f"Sicherung nicht lesbar: {f}") from f
    if ergebnis != "ok":
        raise ValueError(f"Sicherung beschädigt: {ergebnis}")


def kopieren(quelle: Path, ziel: Path) -> None:
    """Kopiert über eine Teildatei: Bricht das Netzlaufwerk weg, liegt dort nie eine halbe Sicherung."""
    teil = ziel.with_name(ziel.name + ".teil")
    try:
        shutil.copyfile(quelle, teil)
        if teil.stat().st_size != quelle.stat().st_size:
            raise OSError("Die Kopie ist unvollständig.")
        os.replace(teil, ziel)
    finally:
        teil.unlink(missing_ok=True)


def netz_erreichbar(netz: Path, lokal: Path) -> bool:
    """Wirklich eingehängt? Ein leerer Einhängepunkt liegt auf der lokalen Platte (siehe web/speicher.py)."""
    try:
        return netz.parent.is_dir() and os.stat(netz.parent).st_dev != os.stat(lokal).st_dev
    except OSError:
        return False


def einmal(db: Path, ordner: Path, netz: Path | None = None, *, geraet_pruefen: bool = True) -> dict:
    """Eine Sicherung samt Prüfung und Aufräumen. Ein Fehler am Netzlaufwerk steht im Stand, die lokale Sicherung
    gilt trotzdem. Fehlt sie selbst, bleibt `letzte` stehen und der nächste Blick versucht es wieder."""
    ordner = Path(ordner)
    ordner.mkdir(parents=True, exist_ok=True)
    stand = stand_lesen(ordner)
    stand.update(versucht=_jetzt(), fehler=None)
    try:
        ziel = datenbank.sichern(db, ordner)
        try:
            pruefen(ziel)
        except ValueError:
            ziel.unlink(missing_ok=True)
            raise
        datenbank.aeltere_entfernen(ordner, BEHALTEN_LOKAL)
        stand.update(letzte=_jetzt(), datei=ziel.name, groesse=ziel.stat().st_size)
    except (OSError, sqlite3.Error, ValueError) as f:
        stand["fehler"] = str(f)
        log.error("Tägliche Sicherung fehlgeschlagen: %s", f)
        _stand_schreiben(ordner, stand)
        return stand
    if netz is not None:
        netz = Path(netz)
        try:
            if geraet_pruefen and not netz_erreichbar(netz, ordner):
                raise OSError("Das Netzlaufwerk ist nicht erreichbar.")
            netz.mkdir(exist_ok=True)
            kopieren(ziel, netz / ziel.name)
            datenbank.aeltere_entfernen(netz, BEHALTEN_NETZ)
            stand.update(netz_letzte=_jetzt(), netz_fehler=None)
        except OSError as f:
            stand["netz_fehler"] = str(f)
            log.warning("Kopie auf das Netzlaufwerk fehlgeschlagen: %s", f)
    _stand_schreiben(ordner, stand)
    log.info("Tägliche Sicherung: %s", ziel.name)
    return stand


class Tagessicherung(threading.Thread):
    """Schaut stündlich nach und sichert, sobald die letzte Sicherung älter als 20 Stunden ist."""

    def __init__(self, db: Path, ordner: Path, netz: Path | None = None):
        super().__init__(name="Tagessicherung", daemon=True)
        self.db, self.ordner, self.netz = Path(db), Path(ordner), netz
        self.halt = threading.Event()

    def run(self) -> None:
        warten = START
        while not self.halt.wait(warten):
            warten = TAKT
            try:
                if faellig(self.ordner):
                    einmal(self.db, self.ordner, self.netz)
            except Exception:   # der Thread darf nie still sterben, sonst gäbe es keine Sicherung mehr
                log.exception("Fehler in der täglichen Sicherung")
