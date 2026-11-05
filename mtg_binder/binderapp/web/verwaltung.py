"""Verwaltung im Add-on: Stand der Sicherungen und „Datenbank löschen“ (nur mit `ha=True`, siehe server.py).

    GET  /api/verwaltung           Was gelöscht würde, ob es gerade geht, Stand der Sicherungen. Schreibt nichts.
    POST /api/datenbank/loeschen   {"wort": "LÖSCHEN", "fotos": "archiv" | "loeschen"}

Am PC gibt es diese Routen nicht: Dort liegen unter `h.fotos` die Mess- und Abnahmefotos.

Ablauf (Entscheidungen des Nutzers, 29.10.2026): Vorher kommt eine Sicherung in `<speicherort>/archiv/<Zeit>/`, die
keine Zählung verdrängt. Misslingt sie, geschieht nichts. Danach wird die Datenbank in einer Transaktion geleert,
erst dann folgen die Ordner. Originale wandern ins Archiv oder werden gelöscht, Vorschaubilder immer. Beide Ordner
werden nur umbenannt, das geht auch auf dem Netzlaufwerk sofort; das langsame Löschen läuft im Hintergrund weiter
und wird nach einem Neustart zu Ende geführt (`reste_entfernen`). Katalog und Schlüssel des PCs bleiben.
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
from contextlib import closing
from datetime import datetime
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from binderapp.db import leeren, sicherung
from binderapp.web.helfer import Fehler

WORT = "LÖSCHEN"
FOTOS_WAHL = ("archiv", "loeschen")
TAEGLICH = "taeglich"     # Unterordner von h.sicherungen
WEG = ".weg-"             # Kennzeichen umbenannter Ordner, die noch gelöscht werden

log = logging.getLogger("binderapp.verwaltung")


def reste_entfernen(wurzel: Path) -> None:
    """Löscht umbenannte Ordner. Nimmt nur Namen wie `bilder.weg-<Zeit>`, nie etwas anderes im Speicherort."""
    try:
        reste = [p for p in Path(wurzel).iterdir() if p.is_dir() and WEG in p.name]
    except OSError:
        return
    for ordner in reste:
        shutil.rmtree(ordner, ignore_errors=True)


def _archiv_anlegen(ordner: Path) -> Path:
    """Neuer Ordner mit der Uhrzeit im Namen; ein vorhandenes Archiv wird nie angefasst."""
    stempel = datetime.now().strftime("%Y%m%d-%H%M%S")
    ziel, n = ordner / stempel, 1
    while True:
        try:
            ziel.mkdir(parents=True)
            return ziel
        except FileExistsError:
            n += 1
            ziel = ordner / f"{stempel}-{n}"


def _beiseite(ordner: Path, stempel: str) -> None:
    """Benennt den Ordner zum Löschen um und legt ihn leer neu an."""
    if ordner.exists():
        os.replace(ordner, ordner.with_name(f"{ordner.name}{WEG}{stempel}"))
    ordner.mkdir(parents=True, exist_ok=True)


def sicherungen_stand(h) -> dict:
    ordner = h.sicherungen / TAEGLICH
    stand = sicherung.stand_lesen(ordner)
    return {
        "letzte": stand.get("letzte"), "fehler": stand.get("fehler"), "netz_letzte": stand.get("netz_letzte"),
        "netz_fehler": stand.get("netz_fehler"), "groesse": stand.get("groesse"),
        "anzahl": len(list(ordner.glob("*.sqlite"))) if ordner.is_dir() else 0,
    }


def ausfuehren(h, fotos_wahl: str) -> dict:
    if not (h.fotos_ok() and h.bilder_ok()):
        raise Fehler("Das Netzlaufwerk ist nicht erreichbar. Ohne es lässt sich nichts aufräumen.", 503)
    with closing(h.db()) as con:
        grund = leeren.hindernis(con, h.nutzer)
        vorher = leeren.zahlen(con, h.nutzer)
    if grund:
        raise Fehler(grund, 409)

    wurzel = h.fotos.parent
    try:
        archiv = _archiv_anlegen(wurzel / "archiv")
        sicherung.kopieren(h.sichern(), archiv / "binder.sqlite")
    except OSError as f:
        log.error("Sicherung vor dem Löschen fehlgeschlagen: %s", f)
        raise Fehler("Die Sicherung ist fehlgeschlagen. Es wurde nichts gelöscht.", 503)

    def arbeit(con):
        grund = leeren.hindernis(con, h.nutzer)   # der PC könnte seit dem Blick oben begonnen haben
        if grund:
            raise Fehler(grund, 409)
        leeren.leeren(con)

    h.schreibvorgang(arbeit)
    leeren.verdichten(h.datenbank)

    # Ab hier ist die Datenbank leer. Ein Fehler an den Ordnern wird gemeldet, ändert daran aber nichts mehr.
    stempel = archiv.name
    warnungen = []
    try:
        if fotos_wahl == "archiv":
            if h.fotos.exists():
                os.replace(h.fotos, archiv / h.fotos.name)
            h.fotos.mkdir(parents=True, exist_ok=True)
        else:
            _beiseite(h.fotos, stempel)
    except OSError as f:
        log.error("Fotos nicht verschoben: %s", f)
        warnungen.append("Die Original-Fotos ließen sich nicht verschieben und liegen noch am alten Platz.")
    try:
        _beiseite(h.bilder, stempel)
    except OSError as f:
        log.error("Vorschaubilder nicht entfernt: %s", f)
        warnungen.append("Die Vorschaubilder ließen sich nicht entfernen und liegen noch am alten Platz.")
    threading.Thread(target=reste_entfernen, args=(wurzel,), name="Aufraeumen", daemon=True).start()
    log.info("Datenbank geleert: %s, Fotos: %s, Archiv: %s", vorher, fotos_wahl, archiv)
    return {"geloescht": vorher, "fotos": fotos_wahl, "archiv": f"archiv/{archiv.name}", "warnungen": warnungen}


def routen(h) -> list[Route]:
    def uebersicht(request):
        with closing(h.db()) as con:
            zahlen = leeren.zahlen(con, h.nutzer)
            grund = leeren.hindernis(con, h.nutzer)
        if grund is None and not (h.fotos_ok() and h.bilder_ok()):
            grund = "Das Netzlaufwerk ist nicht erreichbar. Ohne es lässt sich nichts aufräumen."
        return JSONResponse({"zahlen": zahlen, "hindernis": grund, "wort": WORT, "sicherung": sicherungen_stand(h)})

    @h.schreibend
    async def loeschen(request):
        daten = await h.json_daten(request)
        if daten.get("wort") != WORT:
            raise Fehler(f"Zum Bestätigen bitte genau {WORT} eintippen.")
        if daten.get("fotos") not in FOTOS_WAHL:
            raise Fehler("Bitte wählen, was mit den Original-Fotos geschieht.")
        return JSONResponse(await run_in_threadpool(ausfuehren, h, daten["fotos"]))

    return [Route("/verwaltung", uebersicht), Route("/datenbank/loeschen", loeschen, methods=["POST"])]
