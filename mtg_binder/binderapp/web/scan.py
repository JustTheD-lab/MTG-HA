"""Bulk-Scan mit dem Card Slinger (Teilrunde A, 31.10.2026): Livestream vom Handy, Auslösen im Add-on, Puffer.

Ablauf am Handy (js/scan.js):
1. `POST /api/scan/durchgang` beginnt einen Durchgang (oder setzt den offenen fort).
2. Laufend `POST /api/scan/durchgang/{id}/bild?b=…&h=…&t=…`: kleines Graustufenbild der Scanzone als roher
   Inhalt (ein Byte je Pixel), `t` = Zeit des Handys in Millisekunden. Antwort: Entscheidung des Auslösers
   (`binderapp.scan.ausloeser`), bei `ausloesen` nimmt das Handy ein Standbild in voller Auflösung.
3. `POST /api/scan/durchgang/{id}/foto`: das Standbild (JPEG), unverändert nach fotos/scan/<Durchgang>/.
4. `POST /api/scan/foto/{id}/vorschau`: verkleinertes Bild vom Handy (das Add-on rechnet keine Bilder um).

Ohne erreichbares Netzlaufwerk ist Scannen gesperrt. Fotos werden nie gelöscht: „verwerfen“ setzt nur den Status.
Der Zustand des Auslösers liegt im Speicher des Servers (ein Prozess); nach einem Neustart beginnt er mit einem
neuen Grundbild.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import time
from contextlib import closing

from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from binderapp.db import erkennung
from binderapp.db.datenbank import aendern, einfuegen
from binderapp.katalog import setfamilie
from binderapp.scan.ausloeser import GROESSTE_SEITE, Ausloeser
from binderapp.sortieren import einsortieren
from binderapp.web import hochladen, regeln
from binderapp.web.helfer import Fehler, Helfer, fehler
from binderapp.web.speicher import MELDUNG as MELDUNG_SPEICHER

MAX_VORSCHAU = 512 * 1024
MAX_BILD = GROESSTE_SEITE * GROESSTE_SEITE
QUELLE = "scan"
STAND_WARTEN = 20     # Sekunden, die /scan/durchgang/{id}/stand höchstens offen bleibt
STAND_TAKT = 0.25     # Sekunden zwischen zwei Blicken in die Datenbank während des Wartens
ZEITEN_HOECHSTENS = 50
AKTIV = 30            # so viele Sekunden nach dem letzten Bild gilt die Scan-Seite als aktiv
_letztes_bild = 0.0   # time.monotonic() des letzten Bildes; gilt für den einen Server-Prozess


def scan_aktiv() -> bool:
    """Schickt die Scan-Seite gerade Bilder? Der PC lädt dann schon die Modelle (siehe `/pc/warten`)."""
    return _letztes_bild > 0 and time.monotonic() - _letztes_bild < AKTIV


def _ms(wert) -> int | None:
    """Messwert des Handys: ganze Millisekunden von 0 bis 24 Stunden, sonst nichts."""
    try:
        z = int(float(wert))
    except (TypeError, ValueError, OverflowError):
        return None
    return z if 0 <= z <= 86_400_000 else None


def _zahl(wert: str | None, name: str, ganz: bool = True):
    try:
        z = int(wert) if ganz else float(wert)
    except (TypeError, ValueError):
        raise Fehler(f"Ungültige Angabe: {name}.")
    if not ganz and not math.isfinite(z):
        raise Fehler(f"Ungültige Angabe: {name}.")
    return z


async def _klein_lesen(request, hoechstens: int) -> bytes:
    angekuendigt = request.headers.get("content-length")
    if angekuendigt and angekuendigt.isdigit() and int(angekuendigt) > hoechstens:
        raise Fehler("Bild zu groß.", 413)
    daten = bytearray()
    async for block in request.stream():
        daten += block
        if len(daten) > hoechstens:
            raise Fehler("Bild zu groß.", 413)
    return bytes(daten)


def _lesend(handler):
    """Lesende Routen (laufen im Thread): `Fehler` als verständliche Antwort statt Serverfehler."""
    def huelle(request):
        try:
            return handler(request)
        except Fehler as f:
            return fehler(str(f), f.status)
    return huelle


def _foto_json(z) -> dict:
    return {"id": z["id"], "nummer": z["nummer"], "status": z["status"], "vorschau": z["vorschau"],
            "aufgenommen": z["aufgenommen"] + "Z", "fehler": z["fehler"],
            "grund": (json.loads(z["ausloeser"]) or {}).get("grund") if z["ausloeser"] else None,
            # erkannte Karte (Teilrunde B): Name, Erkennungsstatus, Kiste für den Sprung in die Kartenansicht
            "karte": {"id": z["karte_id"], "name": z["karte_name"], "status": z["karte_status"],
                      "kiste_id": z["kiste_id"], "geprueft": bool(z["geprueft"])} if z["karte_name"] else None}


SQL_DURCHGAENGE = """
SELECT d.id, d.begonnen, d.beendet,
       COUNT(f.id) AS fotos,
       SUM(f.status = 'neu') AS neu, SUM(f.status = 'in_verarbeitung') AS in_verarbeitung,
       SUM(f.status = 'erkannt') AS erkannt, SUM(f.status = 'fehler') AS fehler,
       SUM(f.status = 'verworfen') AS verworfen,
       (SELECT vorschau FROM scan_foto v WHERE v.durchgang_id = d.id AND v.status != 'verworfen'
          AND v.vorschau IS NOT NULL ORDER BY v.nummer DESC LIMIT 1) AS letzte_vorschau
FROM scan_durchgang d LEFT JOIN scan_foto f ON f.durchgang_id = d.id
WHERE d.nutzer_id = ?
GROUP BY d.id ORDER BY d.id DESC
"""


def routen(h: Helfer) -> list[Route]:
    ausloeser: dict[int, Ausloeser] = {}       # je offenem Durchgang
    ausloesungen: dict[int, list[dict]] = {}   # Messwerte, bis das Foto dazu ankommt
    letzte_antwort: dict[int, tuple[int, dict]] = {}   # je Durchgang: Laufnummer und Antwort des letzten Bildes

    def vergessen(did: int) -> None:
        ausloeser.pop(did, None)
        letzte_antwort.pop(did, None)
        # ausloesungen bleiben: Fotos eines beendeten Durchgangs dürfen noch ankommen

    def durchgang_holen(con, did: int, offen: bool = False):
        d = con.execute("SELECT * FROM scan_durchgang WHERE id = ? AND nutzer_id = ?", (did, h.nutzer)).fetchone()
        if d is None:
            raise Fehler("Scan-Durchgang nicht gefunden.", 404)
        if offen and d["beendet"] is not None:
            raise Fehler("Dieser Scan-Durchgang ist schon beendet.", 409)
        return d

    def foto_holen(con, fid: int):
        f = con.execute("SELECT f.* FROM scan_foto f JOIN scan_durchgang d ON d.id = f.durchgang_id"
                        " WHERE f.id = ? AND d.nutzer_id = ?", (fid, h.nutzer)).fetchone()
        if f is None:
            raise Fehler("Scan-Foto nicht gefunden.", 404)
        return f

    def version_pruefen():
        if h.version() < 5:
            raise Fehler("Die Datenbank kennt den Bulk-Scan noch nicht (Version 5 nötig).", 409)

    # ---------------------------------------------------------------- Lesen

    def uebersicht(request):
        version_pruefen()
        with closing(h.db()) as con:
            zeilen = con.execute(SQL_DURCHGAENGE, (h.nutzer,)).fetchall()
        durchgaenge = [{**dict(z), **{k: z[k] or 0 for k in ("neu", "in_verarbeitung", "erkannt", "fehler",
                                                              "verworfen")}} for z in zeilen]
        offen = next((d["id"] for d in durchgaenge if d["beendet"] is None), None)
        return JSONResponse({"durchgaenge": durchgaenge, "offen": offen,
                             "gesamt": {"fotos": sum(d["fotos"] - d["verworfen"] for d in durchgaenge),
                                        "wartend": sum(d["neu"] for d in durchgaenge)},
                             "speicher": h.fotos_ok(),
                             # ab Version 9: Foil und Sets je Durchgang einstellbar
                             "je_durchgang": h.version() >= 9})

    def durchgang(request):
        version_pruefen()
        did = request.path_params["id"]
        with closing(h.db()) as con:
            con.execute("BEGIN")   # ein Stand für alle Abfragen dieser Antwort
            d = durchgang_holen(con, did)
            v6 = einsortieren.aktiv(con)
            fotos = con.execute(
                "SELECT f.*, k.name AS karte_name, k.status AS karte_status, k.kiste_id, k.geprueft"
                + (", k.sortieren" if v6 else "") +
                " FROM scan_foto f LEFT JOIN karte k ON k.id = f.karte_id"
                " WHERE f.durchgang_id = ? ORDER BY f.nummer", (did,)).fetchall()
            # Sortierregeln: Zielkiste und Fach oder Wartegrund je Karte
            karten = {f["karte_id"]: {"id": f["karte_id"], "kiste_id": f["kiste_id"], "sortieren": f["sortieren"],
                                      "status": f["karte_status"], "geprueft": f["geprueft"]}
                      for f in fotos if v6 and f["karte_name"]}
            ziele = einsortieren.stand(con, h.nutzer, list(karten.values()))
        antwort = [_foto_json(f) for f in fotos]
        for f in antwort:
            if f["karte"]:
                f["karte"]["ziel"] = ziele.get(f["karte"]["id"])
        v9 = "foil" in d.keys()
        return JSONResponse({"id": d["id"], "begonnen": d["begonnen"] + "Z",
                             "beendet": d["beendet"] + "Z" if d["beendet"] else None,
                             "einsortieren": bool(d["einsortieren"]) if v6 else None,
                             # ab Version 9: Foil und erlaubte Sets des Durchgangs (None = ältere Datenbank)
                             "foil": bool(d["foil"]) if v9 else None,
                             "sets": list(erkennung.sets_lesen(d["sets"])) if v9 else None,
                             "fotos": antwort})

    async def stand(request):
        """Langes Warten der Scan-Seite: Antwortet, sobald ein Foto nach Nummer `ab` fertig ist, spätestens nach
        `warten` Sekunden. Klein gehalten, weil das Handy über das Internet fragt (Nabu Casa)."""
        did = request.path_params["id"]
        q = request.query_params
        try:
            ab = _zahl(q.get("ab", "0"), "Nummer")
            ende = time.monotonic() + min(max(_zahl(q.get("warten", "0"), "Wartezeit", ganz=False), 0), STAND_WARTEN)

            def lesen():
                with closing(h.db()) as con:
                    con.execute("BEGIN")
                    durchgang_holen(con, did)
                    neu = con.execute("SELECT id, nummer FROM scan_foto WHERE durchgang_id = ? AND nummer > ?"
                                      " AND status IN ('erkannt', 'fehler') ORDER BY nummer", (did, ab)).fetchall()
                    z = con.execute("SELECT COUNT(*) AS erkannt, SUM(k.status != 'sicher' AND k.geprueft = 0) AS pruefen"
                                    " FROM scan_foto f JOIN karte k ON k.id = f.karte_id WHERE f.durchgang_id = ?",
                                    (did,)).fetchone()
                    letzte = con.execute("SELECT k.name FROM scan_foto f JOIN karte k ON k.id = f.karte_id"
                                         " WHERE f.durchgang_id = ? ORDER BY f.nummer DESC LIMIT 1", (did,)).fetchone()
                return {"neu": [dict(x) for x in neu], "erkannt": z["erkannt"], "pruefen": z["pruefen"] or 0,
                        "zuletzt": letzte["name"] if letzte else None}

            while True:
                antwort = await run_in_threadpool(lesen)
                if antwort["neu"] or time.monotonic() >= ende:
                    return JSONResponse(antwort)
                await asyncio.sleep(STAND_TAKT)
        except Fehler as f:
            return fehler(str(f), f.status)

    def original(request):
        with closing(h.db()) as con:
            f = foto_holen(con, request.path_params["id"])
        pfad = h.fotos / f["datei"]
        if not h.fotos_ok() or not pfad.is_file():
            return fehler(MELDUNG_SPEICHER, 503)
        return FileResponse(pfad, media_type="image/jpeg")

    # ---------------------------------------------------------------- Schreiben

    @h.schreibend
    async def beginnen(request):
        version_pruefen()
        d = await h.json_daten(request) if request.headers.get("content-length") not in (None, "0") else {}
        if not await run_in_threadpool(h.fotos_ok):
            return fehler(MELDUNG_SPEICHER, 503)

        def arbeit(con):
            offen = con.execute("SELECT id FROM scan_durchgang WHERE nutzer_id = ? AND beendet IS NULL"
                                " ORDER BY id DESC", (h.nutzer,)).fetchall()
            if offen and not d.get("neu"):
                return offen[0]["id"], False
            for z in offen:   # „Neuer Durchgang“: offene beenden
                aendern(con, h.nutzer, "nutzer", "scan_durchgang", z["id"], {"beendet": _jetzt(con)})
                vergessen(z["id"])
            for alt in [k for k in ausloesungen if k not in {z["id"] for z in offen}]:
                ausloesungen.pop(alt, None)   # Messwerte älterer Durchgänge: deren Fotos sind längst da
            felder = {"nutzer_id": h.nutzer}
            if einsortieren.aktiv(con) and d.get("einsortieren") is False:
                felder["einsortieren"] = 0
            return einfuegen(con, h.nutzer, "nutzer", "scan_durchgang", felder), True

        did, neu = await run_in_threadpool(h.schreibvorgang, arbeit)
        return JSONResponse({"id": did, "neu": neu}, status_code=201 if neu else 200)

    def sets_pruefen(roh) -> list[str]:
        """Liste von Set-Codes: klein, ohne Doppelte, höchstens 30; mit Katalog nur bekannte Sets."""
        if not isinstance(roh, list) or len(roh) > erkennung.SETS_HOECHSTENS:
            raise Fehler(f"Bitte „sets“ als Liste von höchstens {erkennung.SETS_HOECHSTENS} Set-Codes angeben.")
        codes = []
        for s in roh:
            if not isinstance(s, str) or not erkennung.SET_CODE.fullmatch(s.strip().lower()):
                raise Fehler("Ungültiger Set-Code.")
            if s.strip().lower() not in codes:
                codes.append(s.strip().lower())
        kat = h.katalog()
        if kat is not None and codes:
            with closing(kat):
                unbekannt = [c for c in codes if c not in setfamilie.bekannt(kat, codes)]
            if unbekannt:
                raise Fehler("Unbekannte Sets: " + ", ".join(unbekannt) + ".")
        return codes

    @h.schreibend
    async def einstellen(request):
        """Einstellung des Durchgangs (gilt für Karten, die danach erkannt werden): {"einsortieren": bool,
        "foil": bool, "sets": [Set-Codes]} – jede Angabe einzeln möglich; „sets“ leer = alle Sets."""
        did = request.path_params["id"]
        d = await h.json_daten(request)
        if not d or set(d) - {"einsortieren", "foil", "sets"}:
            raise Fehler("Bitte „einsortieren“, „foil“ oder „sets“ angeben.")
        for name in ("einsortieren", "foil"):
            if name in d and not isinstance(d[name], bool):
                raise Fehler(f"Bitte „{name}“ mit true oder false angeben.")
        sets = await run_in_threadpool(sets_pruefen, d["sets"]) if "sets" in d else None

        def arbeit(con):
            if "einsortieren" in d and not einsortieren.aktiv(con):
                raise Fehler(regeln.NICHT_DA, 409)
            if ("foil" in d or "sets" in d) and h.version() < 9:
                raise Fehler("Die Datenbank kennt Foil und Sets je Durchgang noch nicht (Version 9 nötig).", 409)
            durchgang_holen(con, did)
            felder = {}
            if "einsortieren" in d:
                felder["einsortieren"] = int(d["einsortieren"])
            if "foil" in d:
                felder["foil"] = int(d["foil"])
            if sets is not None:
                felder["sets"] = json.dumps(sets) if sets else None
            aendern(con, h.nutzer, "nutzer", "scan_durchgang", did, felder)
            if d.get("einsortieren") is False:   # aus: Karten dieses Durchgangs, die noch warten, bleiben im Eingang
                for z in con.execute("SELECT DISTINCT k.id FROM karte k JOIN scan_foto f ON f.karte_id = k.id"
                                     " WHERE f.durchgang_id = ? AND k.sortieren = 'offen'", (did,)).fetchall():
                    aendern(con, h.nutzer, einsortieren.QUELLE, "karte", z[0], {"sortieren": None})
        await run_in_threadpool(h.schreibvorgang, arbeit)
        return JSONResponse({k: (sets if k == "sets" else d[k]) for k in d})

    def sets_suchen(request):
        kat = h.katalog()
        if kat is None:
            return JSONResponse({"verfuegbar": False, "sets": []})
        with closing(kat):
            return JSONResponse({"verfuegbar": True, "sets": setfamilie.suchen(kat, request.query_params.get("q", ""))})

    def set_familie(request):
        kat = h.katalog()
        if kat is None:
            raise Fehler("Ohne Katalog lassen sich keine Sets wählen.", 409)
        with closing(kat):
            familie = setfamilie.familie(kat, request.path_params["code"])
        if familie is None:
            raise Fehler("Set nicht gefunden.", 404)
        return JSONResponse({"sets": familie})

    @h.schreibend
    async def beenden(request):
        did = request.path_params["id"]

        def arbeit(con):
            d = durchgang_holen(con, did)
            if d["beendet"] is None:
                aendern(con, h.nutzer, "nutzer", "scan_durchgang", did, {"beendet": _jetzt(con)})
        await run_in_threadpool(h.schreibvorgang, arbeit)
        vergessen(did)
        return JSONResponse({"beendet": True})

    @h.schreibend
    async def bild(request):
        global _letztes_bild
        _letztes_bild = time.monotonic()
        did = request.path_params["id"]
        q = request.query_params
        b, hh = _zahl(q.get("b"), "Breite"), _zahl(q.get("h"), "Höhe")
        zeit = _zahl(q.get("t"), "Zeit", ganz=False) / 1000
        # Laufnummer des Bildes: schickt das Handy nach einem Netzfehler dasselbe Bild noch einmal, bekommt es die
        # schon getroffene Entscheidung (sonst ginge eine Auslösung verloren, deren Antwort nie ankam).
        lauf = _zahl(q["n"], "Laufnummer") if "n" in q else None
        daten = await _klein_lesen(request, MAX_BILD)
        a = ausloeser.get(did)
        if a is None:   # erstes Bild (oder nach Neustart): Durchgang prüfen
            def pruefen():
                with closing(h.db()) as con:
                    durchgang_holen(con, did, offen=True)
            await run_in_threadpool(pruefen)
            a = ausloeser.setdefault(did, Ausloeser())
        letzte = letzte_antwort.get(did)
        if lauf is not None and letzte is not None and letzte[0] == lauf:
            return JSONResponse({**letzte[1], "wiederholt": True})
        if q.get("neu") == "1":
            a.zuruecksetzen()
        try:
            e = a.bild(daten, zeit, b, hh, jetzt=q.get("jetzt") == "1")
        except ValueError as f:
            raise Fehler(f"Ungültiges Bild: {f}.")
        antwort = e.als_dict()
        if e.ausloesen:
            antwort["zeit"] = round(time.time(), 3)   # Messpunkt „Auslösen“ (Uhr des Add-ons)
        if lauf is not None:
            letzte_antwort[did] = (lauf, antwort)
        if e.ausloesen:
            liste = ausloesungen.setdefault(did, [])
            liste.append(antwort)
            del liste[:-20]
        return JSONResponse(antwort)

    @h.schreibend
    async def foto(request):
        """Nimmt Fotos auch für einen schon beendeten Durchgang an: Sie wurden ausgelöst, solange er offen war, und
        kommen nur später an (Warteschlange am Handy). Dasselbe Foto zweimal (Wiederholung nach Netzfehler) wird
        nicht doppelt gespeichert."""
        did = request.path_params["id"]
        if not await run_in_threadpool(h.fotos_ok):
            return fehler(MELDUNG_SPEICHER, 503)

        def pruefen():
            with closing(h.db()) as con:
                durchgang_holen(con, did)
        await run_in_threadpool(pruefen)
        ordner = h.fotos / "scan" / str(did)
        try:
            empfang = await hochladen.empfangen(request, ordner, h.max_foto_bytes)
        except hochladen.UploadFehler as f:
            return fehler(str(f), f.status)
        except ClientDisconnect:
            return Response(status_code=400)
        angekommen = time.time()
        offen = ausloesungen.get(did)
        messung = offen[0] if offen else None   # Messwerte der Auslösung zu diesem Foto (in Reihenfolge)
        abgelegt: list = []

        def arbeit(con):
            if empfang.format != "jpeg":
                raise Fehler("Scan-Fotos nur als JPEG.", 415)
            schon = con.execute("SELECT id, nummer FROM scan_foto WHERE durchgang_id = ? AND sha256 = ?",
                                (did, empfang.sha256)).fetchone()
            if schon is not None:
                return schon["id"], schon["nummer"], False
            nummer = con.execute("SELECT COALESCE(MAX(nummer), 0) + 1 FROM scan_foto WHERE durchgang_id = ?",
                                 (did,)).fetchone()[0]
            while (h.fotos / f"scan/{did}/{nummer:05d}.jpg").exists():
                nummer += 1   # verwaiste Datei (z. B. aus einer früheren Datenbank): nie überschreiben, überspringen
            datei = f"scan/{did}/{nummer:05d}.jpg"
            fid = einfuegen(con, h.nutzer, QUELLE, "scan_foto", {
                "durchgang_id": did, "nummer": nummer, "datei": datei, "sha256": empfang.sha256,
                "groesse": empfang.groesse, "status": "neu",
                "ausloeser": json.dumps(messung, ensure_ascii=False) if messung else None})
            erkennung.scan_zeit_setzen(con, fid, {
                "ausgeloest": (messung or {}).get("zeit"), "angekommen": round(angekommen, 3),
                "aufnahme_ms": _ms(request.query_params.get("aufnahme")),
                "warte_ms": _ms(request.query_params.get("wartete"))})
            os.replace(empfang.teil, h.fotos / datei)   # vor dem COMMIT; scheitert das COMMIT, wird sie entfernt
            abgelegt.append(h.fotos / datei)
            return fid, nummer, True

        try:
            fid, nummer, neu = await run_in_threadpool(h.schreibvorgang, arbeit)
        except BaseException:
            for pfad in abgelegt:
                pfad.unlink(missing_ok=True)
            raise
        finally:
            empfang.teil.unlink(missing_ok=True)
        if neu and offen and offen[0] is messung:
            offen.pop(0)
        return JSONResponse({"id": fid, "nummer": nummer, "groesse": empfang.groesse},
                            status_code=201 if neu else 200)

    @h.schreibend
    async def vorschau(request):
        fid = request.path_params["id"]
        if not await run_in_threadpool(h.bilder_ok):
            return fehler(MELDUNG_SPEICHER, 503)

        def holen():
            with closing(h.db()) as con:
                return foto_holen(con, fid)
        f = await run_in_threadpool(holen)
        ordner = h.bilder / "scan" / str(f["durchgang_id"])
        try:
            empfang = await hochladen.empfangen(request, ordner, MAX_VORSCHAU)
        except hochladen.UploadFehler as e:
            return fehler(str(e), e.status)
        except ClientDisconnect:
            return Response(status_code=400)

        def arbeit(con):
            if empfang.format != "jpeg":
                raise Fehler("Vorschau nur als JPEG.", 415)
            schluessel = f"scan/{f['durchgang_id']}/{f['nummer']:05d}.jpg"
            os.replace(empfang.teil, h.bilder / schluessel)
            aendern(con, h.nutzer, QUELLE, "scan_foto", fid, {"vorschau": schluessel})
            return schluessel

        try:
            schluessel = await run_in_threadpool(h.schreibvorgang, arbeit)
        finally:
            empfang.teil.unlink(missing_ok=True)
        return JSONResponse({"vorschau": schluessel}, status_code=201)

    @h.schreibend
    async def zeiten(request):
        """Messwerte des Handys je Foto: {"zeiten": [{"id", "hochladen_ms", "name_ms"}]}. Nur Statistik, deshalb
        werden unbekannte Fotos und unsinnige Werte still übergangen."""
        d = await h.json_daten(request)
        liste = d.get("zeiten")
        if not isinstance(liste, list) or len(liste) > ZEITEN_HOECHSTENS:
            raise Fehler(f"Bitte „zeiten“ als Liste von höchstens {ZEITEN_HOECHSTENS} Einträgen angeben.")

        def arbeit(con):
            for z in liste:
                if not isinstance(z, dict) or not isinstance(z.get("id"), int):
                    continue
                if con.execute("SELECT 1 FROM scan_foto f JOIN scan_durchgang d ON d.id = f.durchgang_id"
                               " WHERE f.id = ? AND d.nutzer_id = ?", (z["id"], h.nutzer)).fetchone():
                    erkennung.scan_zeit_setzen(con, z["id"], {"hochladen_ms": _ms(z.get("hochladen_ms")),
                                                              "name_ms": _ms(z.get("name_ms"))})
        await run_in_threadpool(h.schreibvorgang, arbeit)
        return JSONResponse({"ok": True})

    def status_setzen(von: tuple[str, ...], nach: str, meldung: str):
        @h.schreibend
        async def handler(request):
            fid = request.path_params["id"]

            def arbeit(con):
                f = foto_holen(con, fid)
                if f["status"] == nach:
                    return
                if f["status"] not in von:
                    raise Fehler(meldung, 409)
                aendern(con, h.nutzer, "nutzer", "scan_foto", fid, {"status": nach})
            await run_in_threadpool(h.schreibvorgang, arbeit)
            return JSONResponse({"status": nach})
        return handler

    return [
        Route("/scan", _lesend(uebersicht)),
        Route("/scan/durchgang", beginnen, methods=["POST"]),
        Route("/scan/durchgang/{id:int}", _lesend(durchgang)),
        Route("/scan/durchgang/{id:int}/stand", stand),
        Route("/scan/zeiten", zeiten, methods=["POST"]),
        Route("/scan/durchgang/{id:int}/beenden", beenden, methods=["POST"]),
        Route("/scan/durchgang/{id:int}/einstellung", einstellen, methods=["POST"]),
        Route("/scan/sets", _lesend(sets_suchen)),
        Route("/scan/set/{code}/familie", _lesend(set_familie)),
        Route("/scan/durchgang/{id:int}/bild", bild, methods=["POST"]),
        Route("/scan/durchgang/{id:int}/foto", foto, methods=["POST"]),
        Route("/scan/foto/{id:int}/vorschau", vorschau, methods=["POST"]),
        Route("/scan/foto/{id:int}/original", _lesend(original)),
        Route("/scan/foto/{id:int}/verwerfen", status_setzen(
            ("neu", "fehler"), "verworfen", "Das Foto ist schon in der Erkennung oder erkannt."), methods=["POST"]),
        Route("/scan/foto/{id:int}/zurueckholen", status_setzen(
            ("verworfen",), "neu", "Nur verworfene Fotos lassen sich zurückholen."), methods=["POST"]),
    ]


def _jetzt(con) -> str:
    return con.execute("SELECT strftime('%Y-%m-%dT%H:%M:%S', 'now')").fetchone()[0]
