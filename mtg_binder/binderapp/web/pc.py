"""PC-Schnittstelle des Add-ons: Der PC mit Grafikkarte holt hochgeladene Fotos ab und schickt das Ergebnis zurück.

Läuft im Add-on als eigener Server auf Port 8099 (nur im Heimnetz, nicht über Ingress). Jede Anfrage braucht
den Schlüssel des PCs (`Authorization: Bearer …`); gespeichert ist im Add-on nur dessen SHA-256 (`pc_schluessel`
im Datenordner). Erzeugt wird der Schlüssel in der Oberfläche (`/api/pc/schluessel`, nur Admins über Ingress).

  GET  /pc/stand                      Version, Warteschlange, Katalogstand, Netzlaufwerk
  POST /pc/anmelden    {kennung}      ein Verarbeiter je Add-on; hängende Fotos zurück auf „neu“
  POST /pc/lebenszeichen {kennung, zustand, …}
  POST /pc/beenden     {kennung}      eigene Fotos zurück auf „neu“
  POST /pc/holen?kennung=…&hoechstens=n   neue Fotos (danach „in_verarbeitung“); nur der angemeldete Verarbeiter
  GET  /pc/foto/{id}                  Original eines Fotos in Arbeit (Kopf X-Sha256 zum Prüfen)
  POST /pc/fertig/{id} {seite_id, taschen}   Ergebnis schreiben (db.erkennung), Kartendaten aus dem Katalog
  POST /pc/fehler/{id} {grund}
  PUT  /pc/bild/{art}/{datei}         Vorschaubild (WebP) ablegen, art = foto | scryfall
  GET  /pc/bilder/fehlend             Drucke ohne Vorschaubild (für Karten, die von Hand dazukamen)
  PUT  /pc/katalog  (X-Katalog-Gebaut: …)   Katalog ersetzen (atomar, mit Prüfung)

Ohne erreichbares Netzlaufwerk sind Holen, Fotos und Bilder gesperrt (503).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import re
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from binderapp.db import erkennung
from binderapp.db.datenbank import schreiben, verbinden, version
from binderapp.preise import zeitplan
from binderapp.web.speicher import MELDUNG, Speicher

NUTZER = 1
SCHLUESSEL_DATEI = "pc_schluessel"
BREITEN = (200, 400, 672)
RE_BILD = re.compile(r"(?P<schluessel>[A-Za-z0-9][A-Za-z0-9_.-]{0,119})_(?P<breite>\d{3})\.webp")
RE_SCRYFALL = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
BILD_HOECHSTENS = 3 * 1024 * 1024
JSON_HOECHSTENS = 4 * 1024 * 1024
KATALOG_HOECHSTENS = 600 * 1024 * 1024
HOLEN_HOECHSTENS = 16

log = logging.getLogger("binderapp.pc")


class Fehler(Exception):
    def __init__(self, text: str, status: int = 400):
        super().__init__(text)
        self.status = status


def fehler(text: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"fehler": text}, status_code=status)


# ---------------------------------------------------------------- Schlüssel


def schluessel_hash(schluessel: str) -> str:
    return hashlib.sha256(schluessel.encode("utf-8")).hexdigest()


def schluessel_erzeugen(daten: Path) -> str:
    """Neuer Schlüssel für den PC; ersetzt den alten. Gespeichert wird nur der Hash."""
    schluessel = secrets.token_urlsafe(32)
    ziel = Path(daten) / SCHLUESSEL_DATEI
    teil = ziel.with_name(ziel.name + ".neu")
    teil.write_text(schluessel_hash(schluessel), encoding="ascii")
    os.replace(teil, ziel)
    return schluessel


def schluessel_eingerichtet(daten: Path) -> bool:
    return (Path(daten) / SCHLUESSEL_DATEI).is_file()


def schluessel_gueltig(daten: Path, angabe: str | None) -> bool:
    datei = Path(daten) / SCHLUESSEL_DATEI
    if not angabe or not datei.is_file():
        return False
    return hmac.compare_digest(datei.read_text(encoding="ascii").strip(), schluessel_hash(angabe))


# ---------------------------------------------------------------- Katalog


def katalog_stand(pfad: Path | None) -> str | None:
    """Bauzeitpunkt des Katalogs (meta.gebaut) oder None."""
    if pfad is None or not Path(pfad).is_file():
        return None
    try:
        with closing(sqlite3.connect(f"{Path(pfad).resolve().as_uri()}?mode=ro", uri=True)) as kat:
            z = kat.execute("SELECT wert FROM meta WHERE schluessel = 'gebaut'").fetchone()
    except sqlite3.Error:
        return None
    return z[0] if z else None


def katalog_probe(kat) -> None:
    """Probeabfragen mit dem Code, der den Katalog später benutzt (Kartendaten, Sprachdrucke). Passt das Schema
    nicht, wird der Katalog abgelehnt, statt später jedes Ergebnis scheitern zu lassen."""
    from binderapp.katalog import kartendaten
    kat.row_factory = sqlite3.Row
    try:
        for tabelle in ("druck", "sprachdruck"):
            z = kat.execute(f"SELECT scryfall_id FROM {tabelle} LIMIT 1").fetchone()
            if z is None:
                raise Fehler("Der Katalog ist leer.")
            kartendaten.nachschlagen(kat, z["scryfall_id"])
    except (sqlite3.Error, KeyError, IndexError) as f:
        raise Fehler(f"Der Katalog passt nicht zu dieser Version der App: {f}")


def kartendaten_nachtragen(con, katalog: Path | None) -> None:
    """Kartendaten neuer Drucke aus dem Katalog des Add-ons (in der laufenden Transaktion)."""
    if katalog is None or not Path(katalog).is_file() or version(con) < 3:
        return
    from binderapp.katalog import kartendaten
    with closing(verbinden(katalog, nur_lesen=True)) as kat:
        kartendaten.uebernehmen(con, kat)


# ---------------------------------------------------------------- Server


async def _json(request) -> dict:
    roh = b""
    async for block in request.stream():
        roh += block
        if len(roh) > JSON_HOECHSTENS:
            raise Fehler("Anfrage zu groß.", 413)
    try:
        import json
        daten = json.loads(roh or b"{}")
    except ValueError:
        raise Fehler("Ungültige Anfrage.")
    if not isinstance(daten, dict):
        raise Fehler("Ungültige Anfrage.")
    return daten


async def _datei_empfangen(request, ziel: Path, hoechstens: int, anfang_pruefen=None) -> tuple[int, Path]:
    """Streamt den Inhalt in eine Teildatei neben `ziel` und prüft Größe und Anfang. Gibt (Größe, Teildatei)
    zurück; der Aufrufer ersetzt `ziel` damit atomar (os.replace). Bei Fehlern ist die Teildatei gelöscht.
    Alle Dateizugriffe laufen im Thread: hängt das Netzlaufwerk, bleibt die Oberfläche bedienbar."""
    teil = ziel.with_name(f".{ziel.name}.teil-{secrets.token_hex(6)}")
    groesse, anfang = 0, b""
    f = await run_in_threadpool(teil.open, "xb")
    try:
        try:
            async for block in request.stream():
                groesse += len(block)
                if groesse > hoechstens:
                    raise Fehler("Datei zu groß.", 413)
                if len(anfang) < 16:
                    anfang += block[:16]
                await run_in_threadpool(f.write, block)
        finally:
            await run_in_threadpool(f.close)
        if groesse == 0:
            raise Fehler("Die Datei ist leer.")
        if anfang_pruefen and not anfang_pruefen(anfang):
            raise Fehler("Falscher Dateityp.", 415)
        return groesse, teil
    except BaseException:
        await run_in_threadpool(teil.unlink, True)
        raise


def erstellen(daten: Path, fotos: Path, bilder: Path, katalog: Path | None, speicher: Speicher | None = None) -> Starlette:
    """PC-Schnittstelle. `daten`: Ordner mit binder.sqlite und dem Schlüssel; `katalog`: Pfad, auch wenn die
    Datei noch fehlt (der PC schickt sie)."""
    daten, fotos, bilder = Path(daten), Path(fotos), Path(bilder)
    datenbank = daten / "binder.sqlite"
    speicher = speicher or Speicher(bilder, fotos, daten)
    katalog = Path(katalog) if katalog else daten / "katalog.sqlite"

    def schreibvorgang(arbeit):
        with closing(verbinden(datenbank)) as con, schreiben(con):
            return arbeit(con)

    def geschuetzt(handler, braucht_speicher: str | None = None):
        """`braucht_speicher`: "fotos" oder "bilder" – dieser Ordner muss erreichbar sein (sonst 503)."""
        async def huelle(request):
            kopf = request.headers.get("authorization", "")
            angabe = kopf[7:] if kopf.lower().startswith("bearer ") else None
            if not await run_in_threadpool(schluessel_gueltig, daten, angabe):
                await asyncio.sleep(0.5)   # bremst Rateversuche
                return fehler("Schlüssel fehlt oder ist falsch. Bitte in der App einen neuen erzeugen.", 401)
            if braucht_speicher and not (await run_in_threadpool(speicher.stand))[braucht_speicher]:
                return fehler(MELDUNG, 503)
            try:
                return await handler(request)
            except Fehler as f:
                return fehler(str(f), f.status)
            except (ValueError, KeyError, sqlite3.IntegrityError) as f:
                return fehler(f"Ungültiges Ergebnis: {f}", 400)
            except RuntimeError as f:
                return fehler(str(f), 409)
            except ClientDisconnect:
                return fehler("Übertragung abgebrochen.", 400)
        return huelle

    def _id(request) -> int:
        return int(request.path_params["id"])

    def _kennung(d: dict) -> str:
        k = d.get("kennung")
        if not isinstance(k, str) or not re.fullmatch(r"[A-Za-z0-9-]{8,64}", k):
            raise Fehler("Kennung des Verarbeiters fehlt.")
        return k

    # ------------------------------------------------------------ Routen

    async def stand(request):
        def lesen():
            with closing(verbinden(datenbank, nur_lesen=True)) as con:
                warte = con.execute("SELECT COUNT(*) FROM foto WHERE nutzer_id = ? AND status IN ('neu', 'in_verarbeitung')",
                                    (NUTZER,)).fetchone()[0]
                if version(con) >= 5:
                    warte += con.execute("SELECT COUNT(*) FROM scan_foto f JOIN scan_durchgang d ON d.id = f.durchgang_id"
                                         " WHERE d.nutzer_id = ? AND f.status IN ('neu', 'in_verarbeitung')",
                                         (NUTZER,)).fetchone()[0]
                return {"version": os.environ.get("BINDER_VERSION", "?"), "schema": version(con), "warteschlange": warte,
                        "katalog": katalog_stand(katalog), "speicher": speicher.stand()}
        return JSONResponse(await run_in_threadpool(lesen))

    async def anmelden(request):
        kennung = _kennung(await _json(request))

        def arbeit(con):
            z = erkennung.letztes_lebenszeichen(con)
            if z is not None and z[1] < erkennung.FRISCH and z[0].get("zustand") != "beendet" \
                    and z[0].get("kennung") != kennung:
                raise RuntimeError("Ein anderer Verarbeiter ist schon angemeldet. Nur einer darf laufen.")
            zurueck = erkennung.zuruecksetzen(con, NUTZER)
            erkennung.lebenszeichen(con, {"kennung": kennung, "zustand": "wartet", "ort": "pc"})
            return {"zurueckgesetzt": zurueck}
        return JSONResponse(await run_in_threadpool(schreibvorgang, arbeit))

    async def lebenszeichen(request):
        d = await _json(request)
        kennung = _kennung(d)
        zustand = d.get("zustand")
        if zustand not in ("wartet", "erkennt", "beendet", "pause"):
            raise Fehler("Unbekannter Zustand.")
        info = {"kennung": kennung, "zustand": zustand, "ort": "pc"}
        if isinstance(d.get("fotos"), list) and all(isinstance(x, int) for x in d["fotos"][:50]):
            info["fotos"] = d["fotos"][:50]
        await run_in_threadpool(schreibvorgang, lambda con: erkennung.lebenszeichen(con, info))
        return JSONResponse({"ok": True})

    def _angemeldet(con, kennung: str) -> None:
        """Nur der zuletzt angemeldete Verarbeiter darf holen und beenden (sonst Doppelarbeit)."""
        z = erkennung.letztes_lebenszeichen(con)
        if z is None or z[0].get("kennung") != kennung or z[0].get("zustand") == "beendet":
            raise RuntimeError("Dieser Verarbeiter ist nicht angemeldet. Bitte die Erkennung neu starten.")

    async def beenden(request):
        kennung = _kennung(await _json(request))

        def arbeit(con):
            _angemeldet(con, kennung)
            zurueck = erkennung.zuruecksetzen(con, NUTZER)
            erkennung.lebenszeichen(con, {"kennung": kennung, "zustand": "beendet", "ort": "pc"})
            return {"zurueckgesetzt": zurueck}
        return JSONResponse(await run_in_threadpool(schreibvorgang, arbeit))

    async def holen(request):
        kennung = _kennung({"kennung": request.query_params.get("kennung")})
        wert = request.query_params.get("hoechstens", "8")
        n = int(wert) if wert.isdigit() else 8
        n = max(1, min(n, HOLEN_HOECHSTENS))

        def arbeit(con):
            _angemeldet(con, kennung)
            return erkennung.holen(con, NUTZER, n)
        return JSONResponse({"auftraege": await run_in_threadpool(schreibvorgang, arbeit)})

    async def foto(request):
        foto_id = _id(request)

        def lesen():
            with closing(verbinden(datenbank, nur_lesen=True)) as con:
                f = con.execute("SELECT datei, status, sha256 FROM foto WHERE id = ? AND nutzer_id = ?",
                                (foto_id, NUTZER)).fetchone()
            if f is None or f["status"] != "in_verarbeitung":
                raise Fehler("Dieses Foto ist nicht in Arbeit.", 404)
            pfad = (fotos / f["datei"]).resolve()   # im Thread: das Netzlaufwerk kann hängen
            if fotos.resolve() not in pfad.parents or not pfad.is_file():
                raise Fehler("Das Originalfoto fehlt in der Ablage.", 404)
            return pfad, f["sha256"]
        pfad, sha = await run_in_threadpool(lesen)
        # Prüfsumme des Uploads: der PC erkennt damit einen abgebrochenen Download
        return FileResponse(pfad, media_type="application/octet-stream", headers={"X-Sha256": sha} if sha else None)

    async def fertig(request):
        foto_id = _id(request)
        d = await _json(request)
        seite_id = d.get("seite_id")
        if not isinstance(seite_id, int) or not isinstance(d.get("taschen"), list) or len(d["taschen"]) > 100:
            raise Fehler("Ungültiges Ergebnis.")
        taschen = [erkennung.tasche_aus_dict(t) for t in d["taschen"]]

        def arbeit(con):
            s = con.execute("SELECT s.nummer, b.raster FROM seite s JOIN binder b ON b.id = s.binder_id"
                            " WHERE s.id = ? AND b.nutzer_id = ?", (seite_id, NUTZER)).fetchone()
            if s is None:
                raise RuntimeError("Die Seite gibt es nicht mehr.")
            ergebnis = erkennung.ergebnis_schreiben(con, NUTZER, foto_id, seite_id, s["raster"], s["nummer"], taschen)
            try:   # Kartendaten sind abgeleitet: ein Problem mit dem Katalog darf das Ergebnis nicht kosten
                con.execute("SAVEPOINT kartendaten")
                kartendaten_nachtragen(con, katalog)
                con.execute("RELEASE kartendaten")
            except sqlite3.Error as f:
                con.execute("ROLLBACK TO kartendaten")
                con.execute("RELEASE kartendaten")
                log.warning("Kartendaten nicht nachgetragen (Katalog): %s", f)
            return ergebnis
        antwort = await run_in_threadpool(schreibvorgang, arbeit)
        zeitplan.anstossen()   # Preise der neuen Karten bald nachtragen (nicht erst beim stündlichen Blick)
        return JSONResponse(antwort)

    async def fehlgeschlagen(request):
        foto_id = _id(request)
        grund = (await _json(request)).get("grund")
        if not isinstance(grund, str) or not grund.strip():
            raise Fehler("Grund fehlt.")

        def arbeit(con):
            f = con.execute("SELECT status FROM foto WHERE id = ? AND nutzer_id = ?", (foto_id, NUTZER)).fetchone()
            if f is None or f["status"] != "in_verarbeitung":
                raise RuntimeError("Dieses Foto ist nicht in Arbeit.")
            erkennung.fehlgeschlagen(con, NUTZER, foto_id, grund)
        await run_in_threadpool(schreibvorgang, arbeit)
        return JSONResponse({"ok": True})

    async def bild(request):
        art, datei = request.path_params["art"], request.path_params["datei"]
        m = RE_BILD.fullmatch(datei)
        if art not in ("foto", "scryfall") or m is None or int(m["breite"]) not in BREITEN:
            raise Fehler("Ungültiger Bildname.")
        if art == "scryfall" and not RE_SCRYFALL.fullmatch(m["schluessel"]):
            raise Fehler("Ungültige Scryfall-ID.")
        ordner = bilder / art
        await run_in_threadpool(lambda: ordner.mkdir(parents=True, exist_ok=True))
        groesse, teil = await _datei_empfangen(request, ordner / datei, BILD_HOECHSTENS,
                                               lambda a: a[:4] == b"RIFF" and a[8:12] == b"WEBP")
        await run_in_threadpool(os.replace, teil, ordner / datei)
        return JSONResponse({"ok": True, "groesse": groesse}, status_code=201)

    # Bulk-Scan (Teilrunde B, Schritt 1): Scan-Fotos nur lesend auflisten und laden, z. B. für das Entwicklungsset.
    # Der Status ändert sich dabei nicht; das Abholen zur Erkennung kommt später.
    async def scan_liste(request):
        def lesen():
            with closing(verbinden(datenbank, nur_lesen=True)) as con:
                if version(con) < 5:
                    return []
                return [dict(z) for z in con.execute(
                    "SELECT f.id, f.durchgang_id, f.nummer, f.sha256, f.groesse, f.status, f.aufgenommen"
                    " FROM scan_foto f JOIN scan_durchgang d ON d.id = f.durchgang_id"
                    " WHERE d.nutzer_id = ? AND f.status != 'verworfen' ORDER BY f.durchgang_id, f.nummer",
                    (NUTZER,))]
        return JSONResponse({"fotos": await run_in_threadpool(lesen)})

    async def scan_foto(request):
        foto_id = _id(request)

        def lesen():
            with closing(verbinden(datenbank, nur_lesen=True)) as con:
                f = con.execute("SELECT f.datei, f.sha256 FROM scan_foto f JOIN scan_durchgang d"
                                " ON d.id = f.durchgang_id WHERE f.id = ? AND d.nutzer_id = ?",
                                (foto_id, NUTZER)).fetchone()
            if f is None:
                raise Fehler("Scan-Foto nicht gefunden.", 404)
            pfad = (fotos / f["datei"]).resolve()
            if fotos.resolve() not in pfad.parents or not pfad.is_file():
                raise Fehler("Das Originalfoto fehlt in der Ablage.", 404)
            return pfad, f["sha256"]
        pfad, sha = await run_in_threadpool(lesen)
        return FileResponse(pfad, media_type="application/octet-stream", headers={"X-Sha256": sha})

    async def scan_holen(request):
        kennung = _kennung({"kennung": request.query_params.get("kennung")})
        wert = request.query_params.get("hoechstens", "8")
        n = max(1, min(int(wert) if wert.isdigit() else 8, HOLEN_HOECHSTENS))

        def arbeit(con):
            _angemeldet(con, kennung)
            return erkennung.scan_holen(con, NUTZER, n)
        return JSONResponse({"auftraege": await run_in_threadpool(schreibvorgang, arbeit)})

    async def scan_fertig(request):
        scan_id = _id(request)
        d = await _json(request)
        if "tasche" not in d:
            raise Fehler("Ungültiges Ergebnis.")
        tasche = erkennung.tasche_aus_dict(d["tasche"]) if d["tasche"] is not None else None

        def arbeit(con):
            ergebnis = erkennung.scan_ergebnis_schreiben(con, NUTZER, scan_id, tasche)
            try:   # wie bei Binderseiten: Kartendaten sind abgeleitet und dürfen das Ergebnis nicht kosten
                con.execute("SAVEPOINT kartendaten")
                kartendaten_nachtragen(con, katalog)
                con.execute("RELEASE kartendaten")
            except sqlite3.Error as f:
                con.execute("ROLLBACK TO kartendaten")
                con.execute("RELEASE kartendaten")
                log.warning("Kartendaten nicht nachgetragen (Katalog): %s", f)
            return ergebnis
        antwort = await run_in_threadpool(schreibvorgang, arbeit)
        zeitplan.anstossen()   # Preise der neuen Karten bald nachtragen
        return JSONResponse(antwort)

    async def scan_fehler(request):
        scan_id = _id(request)
        grund = (await _json(request)).get("grund")
        if not isinstance(grund, str) or not grund.strip():
            raise Fehler("Grund fehlt.")
        await run_in_threadpool(schreibvorgang, lambda con: erkennung.scan_fehlgeschlagen(con, NUTZER, scan_id, grund))
        return JSONResponse({"ok": True})

    async def bilder_fehlend(request):
        def lesen():
            with closing(verbinden(datenbank, nur_lesen=True)) as con:
                ids = [z[0] for z in con.execute(
                    "SELECT DISTINCT lower(bild) FROM karte WHERE nutzer_id = ? AND bild IS NOT NULL", (NUTZER,))]
            ordner = bilder / "scryfall"
            return [i for i in ids if RE_SCRYFALL.fullmatch(i or "")
                    and not all((ordner / f"{i}_{b}.webp").is_file() for b in BREITEN[:2])][:500]
        return JSONResponse({"scryfall": await run_in_threadpool(lesen)})

    async def katalog_ersetzen(request):
        gebaut = request.headers.get("x-katalog-gebaut", "")
        groesse, teil = await _datei_empfangen(request, katalog, KATALOG_HOECHSTENS,
                                               lambda a: a.startswith(b"SQLite format 3\x00"))

        def pruefen_und_ersetzen():
            try:
                with closing(sqlite3.connect(f"{teil.resolve().as_uri()}?mode=ro", uri=True)) as k:
                    if k.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise Fehler("Der Katalog ist beschädigt.")
                    tabellen = {z[0] for z in k.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
                    if not {"meta", "druck", "sprachdruck", "namen"} <= tabellen:
                        raise Fehler("Das ist kein Katalog der Binder-App.")
                    katalog_probe(k)
                os.replace(teil, katalog)
            except BaseException:
                teil.unlink(missing_ok=True)
                raise
            with closing(verbinden(datenbank)) as con, schreiben(con):
                kartendaten_nachtragen(con, katalog)   # Lücken aus der Zeit ohne Katalog füllen
            return katalog_stand(katalog)
        stand_neu = await run_in_threadpool(pruefen_und_ersetzen)
        zeitplan.anstossen()   # neuer Katalog kann fehlende Cardmarket-IDs liefern
        return JSONResponse({"katalog": stand_neu, "gemeldet": gebaut[:40], "groesse": groesse}, status_code=201)

    routen = [
        Route("/pc/stand", geschuetzt(stand)),
        Route("/pc/anmelden", geschuetzt(anmelden), methods=["POST"]),
        Route("/pc/lebenszeichen", geschuetzt(lebenszeichen), methods=["POST"]),
        Route("/pc/beenden", geschuetzt(beenden), methods=["POST"]),
        Route("/pc/holen", geschuetzt(holen, braucht_speicher="fotos"), methods=["POST"]),
        Route("/pc/foto/{id:int}", geschuetzt(foto, braucht_speicher="fotos")),
        Route("/pc/fertig/{id:int}", geschuetzt(fertig, braucht_speicher="fotos"), methods=["POST"]),
        Route("/pc/fehler/{id:int}", geschuetzt(fehlgeschlagen), methods=["POST"]),
        Route("/pc/bild/{art}/{datei}", geschuetzt(bild, braucht_speicher="bilder"), methods=["PUT"]),
        Route("/pc/bilder/fehlend", geschuetzt(bilder_fehlend)),
        Route("/pc/katalog", geschuetzt(katalog_ersetzen), methods=["PUT"]),
        Route("/pc/scan/fotos", geschuetzt(scan_liste)),
        Route("/pc/scan/foto/{id:int}", geschuetzt(scan_foto, braucht_speicher="fotos")),
        Route("/pc/scan/holen", geschuetzt(scan_holen, braucht_speicher="fotos"), methods=["POST"]),
        Route("/pc/scan/fertig/{id:int}", geschuetzt(scan_fertig), methods=["POST"]),
        Route("/pc/scan/fehler/{id:int}", geschuetzt(scan_fehler), methods=["POST"]),
    ]

    async def zu_gross(request, ausnahme):
        return fehler("Nicht gefunden.", 404)

    return Starlette(routes=routen, exception_handlers={OverflowError: zu_gross})


def _aus_umgebung() -> Starlette:
    daten = Path(os.environ.get("BINDER_DATEN", "/data"))
    fotos = Path(os.environ.get("BINDER_FOTOS", daten / "medien" / "fotos"))
    bilder = Path(os.environ.get("BINDER_BILDER", daten / "medien" / "bilder"))
    pruefen = os.environ.get("BINDER_SPEICHER_PRUEFEN", "") == "1"
    katalog = os.environ.get("BINDER_KATALOG_PFAD") or str(daten / "katalog.sqlite")
    return erstellen(daten, fotos, bilder, Path(katalog), Speicher(bilder, fotos, daten, geraet_pruefen=pruefen))


def __getattr__(name):
    """`binderapp.web.pc:app` für uvicorn, erst beim Zugriff gebaut (Tests importieren das Modul ohne Umgebung)."""
    if name == "app":
        return _aus_umgebung()
    raise AttributeError(name)
