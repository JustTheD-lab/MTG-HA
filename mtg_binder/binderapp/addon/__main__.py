"""Start des Servers im Home-Assistant-Add-on: python -m binderapp.addon

Liest die Einstellungen des Add-ons (`/data/options.json`), bereitet Datenbank und Speicherorte vor und
startet den Server für Ingress (nur der Supervisor darf anfragen, siehe `server.Wache`).

    /data/binder.sqlite        Datenbank (lokal, in den Backups von Home Assistant; nie auf dem Netzlaufwerk)
    /data/sicherung/           Sicherungen, die jüngsten 10 bleiben
    /data/katalog.sqlite       Kartenkatalog (schickt der PC, web/pc.py)
    /data/pc_schluessel        Hash des Schlüssels für die PC-Schnittstelle (Port 8099)
    /data/preise/              Price Guide von Cardmarket (täglich geladen, preise/zeitplan.py)
    <speicherort>/fotos/       Original-Fotos (Upload)            ┐ Netzlaufwerk, in den Einstellungen
    <speicherort>/bilder/      Vorschaubilder (erzeugt der PC)    ┘ des Add-ons gewählt
    <speicherort>/einspielen/binder.sqlite   wird beim Start übernommen (Testkopie); vorher Sicherung

Ohne Speicherort liegen Fotos und Bilder unter /data/medien (nur zum Ausprobieren: der Mini-PC hat wenig Platz).
Vor jedem Versionswechsel des Add-ons wird die Datenbank gesichert.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

from binderapp.db import datenbank

DATEN = Path(os.environ.get("BINDER_ADDON_DATEN", "/data"))
OPTIONEN = DATEN / "options.json"
PORT = 8098            # Ingress-Port im Container (config.yaml: ingress_port)
PC_PORT = 8099         # PC-Schnittstelle (config.yaml: ports), nur im Heimnetz
BEHALTEN = 10          # Sicherungen (Entscheidung des Nutzers, 30.10.2026)
ERLAUBTE_WURZELN = ("/share/", "/media/")

log = logging.getLogger("binderapp.addon")


class Einstellungsfehler(ValueError):
    pass


def optionen(pfad: Path = OPTIONEN) -> dict:
    try:
        return json.loads(pfad.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def speicherort(opt: dict) -> Path | None:
    """Ordner auf dem Netzlaufwerk aus den Einstellungen oder None (dann lokal unter /data/medien)."""
    wert = str(opt.get("speicherort") or "").strip()
    if not wert:
        return None
    pfad = Path(wert)
    text = pfad.as_posix().rstrip("/") + "/"
    if not text.startswith(ERLAUBTE_WURZELN) or ".." in pfad.parts or text in ERLAUBTE_WURZELN:
        raise Einstellungsfehler(f"Speicherort „{wert}“: bitte einen Ordner unter /share/… oder /media/… angeben "
                                 "(z. B. /share/netzlaufwerk/mtg-binder).")
    return pfad


def einspielen(ort: Path | None, db: Path, sicherungen: Path) -> Path | None:
    """Übernimmt <ort>/einspielen/binder.sqlite als Datenbank (Testkopie). Vorher wird die vorhandene Datenbank
    gesichert; die eingespielte Datei wird umbenannt, damit sie beim nächsten Start nicht erneut übernommen wird."""
    if ort is None:
        return None
    quelle = ort / "einspielen" / "binder.sqlite"
    if not quelle.is_file():
        return None
    # Erst als Datei lokal kopieren (auf dem Netzlaufwerk nur lesen, keine SQLite-Sperren dort), dann prüfen
    neu = db.with_suffix(".neu")
    neu.parent.mkdir(parents=True, exist_ok=True)
    neu.unlink(missing_ok=True)
    shutil.copyfile(quelle, neu)
    try:
        with closing(sqlite3.connect(neu)) as probe:
            if probe.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise Einstellungsfehler(f"{quelle} ist beschädigt und wurde nicht übernommen.")
            probe.execute("PRAGMA journal_mode = DELETE")
    except (sqlite3.DatabaseError, Einstellungsfehler):
        neu.unlink(missing_ok=True)
        raise
    if db.exists():
        ziel = datenbank.sichern(db, sicherungen)
        log.info("Vorhandene Datenbank gesichert: %s", ziel)
    for rest in (db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
        rest.unlink(missing_ok=True)
    os.replace(neu, db)
    erledigt = quelle.with_name(f"binder-eingespielt-{datetime.now():%Y%m%d-%H%M%S}.sqlite")
    os.replace(quelle, erledigt)
    log.info("Datenbank aus %s übernommen (umbenannt in %s)", quelle, erledigt.name)
    return erledigt


def versionswechsel(db: Path, sicherungen: Path, version: str, stand: Path) -> Path | None:
    """Sichert die Datenbank, wenn das Add-on in einer anderen Version startet als zuletzt."""
    vorher = stand.read_text(encoding="utf-8").strip() if stand.exists() else None
    ziel = None
    if db.exists() and vorher != version:
        ziel = datenbank.sichern(db, sicherungen)
        log.info("Neue Version %s (vorher %s): Datenbank gesichert nach %s", version, vorher or "unbekannt", ziel)
    stand.write_text(version, encoding="utf-8")
    return ziel


def vorbereiten(daten: Path = DATEN, version: str | None = None) -> dict:
    """Alles vor dem Serverstart; gibt die Umgebung für den Server zurück."""
    opt = optionen(daten / "options.json")
    ort = speicherort(opt)
    db = daten / "binder.sqlite"
    sicherungen = daten / "sicherung"
    einspielen(ort, db, sicherungen)
    versionswechsel(db, sicherungen, version or os.environ.get("BINDER_VERSION", "unbekannt"), daten / "version.txt")
    if db.exists():
        with closing(datenbank.anlegen(db)):   # bringt eine ältere Datenbank auf den aktuellen Stand
            pass
    else:
        datenbank.leer_anlegen(db)
        log.info("Neue, leere Datenbank angelegt: %s", db)
    if sicherungen.exists():
        datenbank.aeltere_entfernen(sicherungen, BEHALTEN)
    medien = ort if ort else daten / "medien"
    if ort is None:
        (medien / "fotos").mkdir(parents=True, exist_ok=True)
        (medien / "bilder").mkdir(parents=True, exist_ok=True)
        log.warning("Kein Speicherort eingestellt: Fotos und Bilder liegen lokal unter %s.", medien)
    katalog = daten / "katalog.sqlite"   # schickt der PC; fehlt er, sind Kartensuche und Druckwahl aus
    return {
        "BINDER_DATEN": str(daten),
        "BINDER_FOTOS": str(medien / "fotos"),
        "BINDER_BILDER": str(medien / "bilder"),
        "BINDER_KATALOG": str(katalog),
        "BINDER_KATALOG_PFAD": str(katalog),
        "BINDER_PC_PORT": str(PC_PORT),
        "BINDER_KONFIG": str(daten / "konfig.toml"),
        "BINDER_HA": "1",
        "BINDER_SPEICHER_PRUEFEN": "1" if ort else "0",
        "BINDER_SICHERUNG": str(sicherungen),
        "BINDER_SICHERUNGEN_BEHALTEN": str(BEHALTEN),
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    try:
        umgebung = vorbereiten()
    except Einstellungsfehler as f:
        log.error("%s", f)
        return 1
    os.environ.update(umgebung)
    import socket

    import uvicorn   # erst hier: die Umgebung muss stehen, bevor server.py die App baut

    from binderapp.preise.zeitplan import Preisabruf
    from binderapp.web import pc, server
    app = Verteiler(server.app, pc.app, PC_PORT)
    # Preise täglich selbst holen (Entscheidung des Nutzers, 31.10.2026); die Datei bleibt lokal in /data/preise
    Preisabruf(DATEN / "binder.sqlite", DATEN / "preise", DATEN / "katalog.sqlite").start()
    # Werte an Home Assistant per MQTT (Entscheidung des Nutzers, 02.11.2026); abschaltbar in den Einstellungen
    sender = None
    if optionen(DATEN / "options.json").get("mqtt", True) is not False:
        from binderapp.ha.sender import Sender
        sender = Sender(DATEN / "binder.sqlite", DATEN, os.environ.get("BINDER_VERSION", "?"))
        sender.start()
    sockets = []
    for port in (PORT, PC_PORT):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((os.environ.get("BINDER_ADDON_HOST", "0.0.0.0"), port))   # Tests am PC: 127.0.0.1
        sockets.append(s)
    log.info("Starte MTG Binder (Version %s): Oberfläche auf Port %d (Ingress), Erkennung am PC auf Port %d",
             os.environ.get("BINDER_VERSION", "?"), PORT, PC_PORT)
    konfig = uvicorn.Config(app, workers=1, log_level="warning", access_log=False, proxy_headers=False)
    uvicorn.Server(konfig).run(sockets=sockets)
    if sender is not None:
        sender.stoppen()   # „offline“ an Home Assistant, sonst bliebe der letzte Wert stehen
    return 0


class Verteiler:
    """Ein Server, zwei Ports: Port `pc_port` geht an die PC-Schnittstelle (Schlüssel), alles andere an die
    Oberfläche (nur über Ingress, siehe server.Wache)."""

    def __init__(self, oberflaeche, pc_app, pc_port: int):
        self.oberflaeche, self.pc_app, self.pc_port = oberflaeche, pc_app, pc_port

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.oberflaeche(scope, receive, send)
        ziel = self.pc_app if (scope.get("server") or ("", 0))[1] == self.pc_port else self.oberflaeche
        return await ziel(scope, receive, send)


if __name__ == "__main__":
    raise SystemExit(main())
