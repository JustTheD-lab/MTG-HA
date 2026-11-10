"""Verwaltung im Add-on: Stand der Sicherungen und „Datenbank löschen“ (nur mit `ha=True`, siehe server.py).

    GET  /api/verwaltung           Was gelöscht würde, ob es gerade geht, Stand der Sicherungen. Schreibt nichts.
    POST /api/datenbank/loeschen   {"wort": "LÖSCHEN", "fotos": "archiv" | "loeschen"}
    GET  /api/sicherungen          Alle Sicherungen, die sich zurückspielen lassen (jüngste zuerst). Schreibt nichts.
    POST /api/sicherung/vorschau   {"id"}: prüft die Sicherung und zeigt, was darin steckt und was heute da ist
    POST /api/sicherung/zurueckspielen   {"id", "wort": "ZURÜCK"}

Am PC gibt es diese Routen nicht: Dort liegen unter `h.fotos` die Mess- und Abnahmefotos.

Ablauf (Entscheidungen des Nutzers, 29.10.2026): Vorher kommt eine Sicherung in `<speicherort>/archiv/<Zeit>/`, die
keine Zählung verdrängt. Misslingt sie, geschieht nichts. Danach wird die Datenbank in einer Transaktion geleert,
erst dann folgen die Ordner. Originale wandern ins Archiv oder werden gelöscht, Vorschaubilder immer. Beide Ordner
werden nur umbenannt, das geht auch auf dem Netzlaufwerk sofort; das langsame Löschen läuft im Hintergrund weiter
und wird nach einem Neustart zu Ende geführt (`reste_entfernen`). Katalog und Schlüssel des PCs bleiben.

Zurückspielen (Wunsch und Antworten des Nutzers, 09.11.2026; Einzelheiten in db/zurueck.py): Die Liste zeigt alle
Sicherungen (täglich im Add-on und auf dem Netzlaufwerk, vor Update/Import/Abzug, vor „Datenbank löschen“, vor einem
früheren Zurückspielen). Bestätigt wird mit einem eingetippten Wort. Vorher kommt der jetzige Stand nach
`<sicherungen>/vor-zurueckspielen` (die jüngsten 5), das Zurückspielen lässt sich also selbst zurücknehmen. Der
Preisverlauf bleibt. Fotos und Vorschaubilder liegen nicht in der Sicherung: Was seit damals von der Platte
verschwunden ist, kommt nicht wieder, was dazukam, bleibt liegen.
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from binderapp.db import datenbank, leeren, sicherung, zurueck
from binderapp.web.helfer import Fehler

WORT = "LÖSCHEN"
FOTOS_WAHL = ("archiv", "loeschen")
TAEGLICH = "taeglich"     # Unterordner von h.sicherungen
WEG = ".weg-"             # Kennzeichen umbenannter Ordner, die noch gelöscht werden

WORT_ZURUECK = "ZURÜCK"
VOR_ZURUECK = "vor-zurueckspielen"   # Unterordner von h.sicherungen: Stand direkt vor einem Zurückspielen
VOR_ZURUECK_BEHALTEN = 5
ARBEIT = "arbeit"                    # Unterordner von h.sicherungen: lokale Kopie der gewählten Sicherung
HERKUNFT = {"taeglich": "täglich", "netz": "täglich, Netzlaufwerk", "anlass": "vor Update, Import oder Abzug",
            "zurueck": "vor einem Zurückspielen", "archiv": "vor „Datenbank löschen“"}

log = logging.getLogger("binderapp.verwaltung")
sperre = threading.Lock()            # ein Zurückspielen zur Zeit


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


def _eintrag(herkunft: str, kennung: str, datei: Path) -> dict | None:
    try:
        s = datei.stat()
    except OSError:
        return None
    zeit = datetime.fromtimestamp(s.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": f"{herkunft}:{kennung}", "zeit": zeit, "groesse": s.st_size, "herkunft": herkunft,
            "herkunft_text": HERKUNFT[herkunft], "datei": datei}


def sicherungen_liste(h) -> list[dict]:
    """Alle Sicherungen, jüngste zuerst. Die tägliche Sicherung liegt im Add-on und auf dem Netzlaufwerk: Bei
    gleichem Namen und gleicher Größe zählt die im Add-on. `datei` ist der Pfad und geht nie an die Oberfläche."""
    def dateien(ordner: Path) -> list[Path]:
        try:
            return sorted(p for p in ordner.glob("*.sqlite") if p.is_file())
        except OSError:
            return []

    liste = [_eintrag("taeglich", p.name, p) for p in dateien(h.sicherungen / TAEGLICH)]
    liste += [_eintrag("anlass", p.name, p) for p in dateien(h.sicherungen)]
    liste += [_eintrag("zurueck", p.name, p) for p in dateien(h.sicherungen / VOR_ZURUECK)]
    if h.fotos_ok():
        wurzel = h.fotos.parent
        lokal = {(e["datei"].name, e["groesse"]) for e in liste if e}
        netz = [_eintrag("netz", p.name, p) for p in dateien(wurzel / "sicherung")]
        liste += [e for e in netz if e and (e["datei"].name, e["groesse"]) not in lokal]
        try:
            archive = sorted(p for p in (wurzel / "archiv").iterdir() if p.is_dir())
        except OSError:
            archive = []
        liste += [_eintrag("archiv", a.name, a / "binder.sqlite") for a in archive if (a / "binder.sqlite").is_file()]
    return sorted((e for e in liste if e), key=lambda e: (e["zeit"], e["id"]), reverse=True)


def _sicherung_finden(h, kennung) -> dict:
    """Nur was die Liste gerade nennt: Aus der Anfrage wird nie ein Pfad gebaut."""
    for e in sicherungen_liste(h):
        if e["id"] == kennung:
            return e
    raise Fehler("Diese Sicherung gibt es nicht mehr. Bitte die Liste neu laden.", 404)


def _oeffentlich(e: dict) -> dict:
    return {k: v for k, v in e.items() if k != "datei"}


def _hindernis_zurueck(h) -> str | None:
    with closing(h.db()) as con:
        return leeren.hindernis(con, h.nutzer)


def _arbeit_weg(arbeit: Path) -> None:
    for rest in (arbeit, arbeit.with_name(arbeit.name + "-wal"), arbeit.with_name(arbeit.name + "-shm")):
        try:
            rest.unlink(missing_ok=True)
        except OSError:
            pass


def vorschau(h, kennung) -> dict:
    """Prüft die Sicherung an einer lokalen Kopie und zählt, was darin steckt. Ändert nichts an der Datenbank."""
    e = _sicherung_finden(h, kennung)
    if not sperre.acquire(blocking=False):
        raise Fehler("Gerade wird eine Sicherung zurückgespielt.", 409)
    arbeit = h.sicherungen / ARBEIT / "vorschau.sqlite"
    try:
        version = zurueck.vorbereiten(e["datei"], arbeit)
        drin = zurueck.zahlen(arbeit, h.nutzer)
    except zurueck.Unbrauchbar as f:
        raise Fehler(str(f), 422)
    finally:
        _arbeit_weg(arbeit)
        sperre.release()
    with closing(h.db()) as con:
        heute = leeren.zahlen(con, h.nutzer)
    return {**_oeffentlich(e), "version": version, "zahlen": drin, "heute": heute, "wort": WORT_ZURUECK,
            "hindernis": _hindernis_zurueck(h)}


def zurueckspielen(h, kennung) -> dict:
    e = _sicherung_finden(h, kennung)
    grund = _hindernis_zurueck(h)
    if grund:
        raise Fehler(grund, 409)
    if not sperre.acquire(blocking=False):
        raise Fehler("Gerade wird schon eine Sicherung zurückgespielt.", 409)
    arbeit = h.sicherungen / ARBEIT / "zurueck.sqlite"
    try:
        try:
            zurueck.vorbereiten(e["datei"], arbeit)
        except zurueck.Unbrauchbar as f:
            raise Fehler(str(f), 422)
        try:
            gesichert = datenbank.sichern(h.datenbank, h.sicherungen / VOR_ZURUECK)
            sicherung.pruefen(gesichert)
        except (OSError, ValueError) as f:
            log.error("Sicherung vor dem Zurückspielen fehlgeschlagen: %s", f)
            raise Fehler("Die Sicherung des jetzigen Stands ist fehlgeschlagen. Es wurde nichts geändert.", 503)

        def pruefen(con):   # der PC könnte seit dem Blick oben begonnen haben
            grund = leeren.hindernis(con, h.nutzer)
            if grund:
                raise Fehler(grund, 409)

        try:
            nachher = zurueck.uebernehmen(h.datenbank, arbeit, h.nutzer, vorher_pruefen=pruefen,
                                          hinweis={"sicherung": e["id"], "zeit": e["zeit"], "vorher": gesichert.name})
        except zurueck.Unbrauchbar as f:
            raise Fehler(str(f), 422)
        datenbank.aeltere_entfernen(h.sicherungen / VOR_ZURUECK, VOR_ZURUECK_BEHALTEN)
        leeren.verdichten(h.datenbank)
    finally:
        _arbeit_weg(arbeit)
        sperre.release()
    log.info("Sicherung zurückgespielt: %s (%s); vorheriger Stand: %s", e["id"], e["zeit"], gesichert.name)
    return {"zahlen": nachher, "sicherung": _oeffentlich(e), "gesichert": gesichert.name}


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

    def liste(request):
        return JSONResponse({"sicherungen": [_oeffentlich(e) for e in sicherungen_liste(h)],
                             "netzlaufwerk": h.fotos_ok(), "hindernis": _hindernis_zurueck(h)})

    @h.schreibend
    async def vorschau_route(request):
        daten = await h.json_daten(request)
        return JSONResponse(await run_in_threadpool(vorschau, h, daten.get("id")))

    @h.schreibend
    async def zurueck_route(request):
        daten = await h.json_daten(request)
        if daten.get("wort") != WORT_ZURUECK:
            raise Fehler(f"Zum Bestätigen bitte genau {WORT_ZURUECK} eintippen.")
        return JSONResponse(await run_in_threadpool(zurueckspielen, h, daten.get("id")))

    return [Route("/verwaltung", uebersicht), Route("/datenbank/loeschen", loeschen, methods=["POST"]),
            Route("/sicherungen", liste), Route("/sicherung/vorschau", vorschau_route, methods=["POST"]),
            Route("/sicherung/zurueckspielen", zurueck_route, methods=["POST"])]
