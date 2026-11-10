"""Kaufliste mit Preisalarm (Paket 7, ab Datenbankversion 23; Rechnung in decks/kaufliste.py).

    GET    /api/kaufliste                          Decks mit Schalter, Karten mit Preis und Wunschpreis, Summen, Text
    POST   /api/kaufliste/deck/{id}  {"an": bool}   Deck mit seinen fehlenden Karten in die Liste oder heraus
    POST   /api/kaufliste/karte      {"oracle_id" | "scryfall_id", "foil"?, "anzahl"?, "wunschpreis"?}   eigener Eintrag
    PATCH  /api/kaufliste/karte/{id} {"anzahl"?, "wunschpreis"?}
    DELETE /api/kaufliste/karte/{id}
    PUT    /api/kaufliste/wunsch     {"schluessel", "foil", "wunschpreis": Zahl | null}   Wunschpreis jeder Karte der Liste

Schreiben nur angemeldet; jede Änderung ist ein Schritt (Antwort `schritt`), Rückgängig über
POST /api/schritt/{schritt}/rueckgaengig (kistenschreiben.py).
"""
from __future__ import annotations

import re
from contextlib import closing

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from binderapp.decks import kaufliste
from binderapp.preise import zeitplan
from binderapp.web.helfer import Fehler, fehler

__all__ = ["routen"]

KENNUNG = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
SCHLUESSEL = re.compile(r"(d:|o:)[0-9a-f-]{36}|n:.{1,200}", re.S)
GROSS = 2 ** 62


def _id(request) -> int:
    wert = request.path_params["id"]
    if not 1 <= wert < GROSS:
        raise Fehler("Nicht gefunden.", 404)
    return wert


def _felder(d: dict, erlaubt: set) -> None:
    if set(d) - erlaubt:
        raise Fehler(f"Unbekannte Felder: {', '.join(sorted(set(d) - erlaubt))}.")


def _kennung(wert, feld: str) -> str | None:
    if wert is None:
        return None
    if not isinstance(wert, str) or not KENNUNG.fullmatch(wert):
        raise Fehler(f"„{feld}“ ist keine gültige Kennung.")
    return wert


def routen(h) -> list[Route]:

    def _schreiben(arbeit):
        return run_in_threadpool(h.schreibvorgang, arbeit)

    def mit_katalog(arbeit):
        def innen(con):
            kat = h.katalog()
            try:
                return arbeit(con, kat)
            finally:
                if kat is not None:
                    kat.close()
        return innen

    async def liste(request):
        def lesen():
            with closing(h.db()) as con:
                return kaufliste.liste(con, h.nutzer)
        try:
            a = await run_in_threadpool(lesen)
        except Fehler as f:
            return fehler(str(f), f.status)
        ordner = h.bilder / "scryfall"   # ohne Vorschaubild lädt die Oberfläche das Bild von Scryfall
        for p in a["karten"]:
            p["vorschau"] = bool(p["scryfall_id"]) and (ordner / f"{p['scryfall_id']}_200.webp").is_file()
        return JSONResponse(a)

    @h.schreibend
    async def deck_setzen(request):
        deck_id = _id(request)
        d = await h.json_daten(request)
        _felder(d, {"an"})
        if not isinstance(d.get("an"), bool):
            raise Fehler("„an“ muss „true“ oder „false“ sein.")
        schritt = await _schreiben(lambda con: kaufliste.deck_setzen(con, h.nutzer, deck_id, d["an"]))
        return JSONResponse({"deck_id": deck_id, "an": d["an"], "schritt": schritt})

    @h.schreibend
    async def karte_anlegen(request):
        d = await h.json_daten(request)
        _felder(d, {"oracle_id", "scryfall_id", "foil", "anzahl", "wunschpreis"})
        oracle_id, scryfall_id = _kennung(d.get("oracle_id"), "oracle_id"), _kennung(d.get("scryfall_id"), "scryfall_id")
        foil = d.get("foil", False)
        if not isinstance(foil, bool):
            raise Fehler("„foil“ muss „true“ oder „false“ sein.")
        anzahl = kaufliste.anzahl_pruefen(d.get("anzahl", 1))
        wunsch = kaufliste.preis_pruefen(d.get("wunschpreis"))
        kauf, schritt = await _schreiben(mit_katalog(lambda con, kat: kaufliste.eintrag_anlegen(
            con, kat, h.nutzer, oracle_id, scryfall_id, foil, anzahl, wunsch)))
        zeitplan.anstossen()   # Add-on: Preise der neuen Produkte bald nachtragen
        return JSONResponse({"id": kauf, "schritt": schritt}, status_code=201)

    @h.schreibend
    async def karte_aendern(request):
        kauf = _id(request)
        d = await h.json_daten(request)
        _felder(d, {"anzahl", "wunschpreis"})
        if not d:
            raise Fehler("Bitte „anzahl“ oder „wunschpreis“ angeben.")
        felder = {}
        if "anzahl" in d:
            felder["anzahl"] = kaufliste.anzahl_pruefen(d["anzahl"], mindestens=0)
        if "wunschpreis" in d:
            felder["wunschpreis"] = kaufliste.preis_pruefen(d["wunschpreis"])
        schritt = await _schreiben(lambda con: kaufliste.eintrag_aendern(con, h.nutzer, kauf, felder))
        return JSONResponse({"id": kauf, "schritt": schritt})

    @h.schreibend
    async def karte_loeschen(request):
        kauf = _id(request)
        schritt = await _schreiben(lambda con: kaufliste.eintrag_loeschen(con, h.nutzer, kauf))
        return JSONResponse({"id": kauf, "schritt": schritt})

    @h.schreibend
    async def wunsch(request):
        d = await h.json_daten(request)
        _felder(d, {"schluessel", "foil", "wunschpreis"})
        schl = d.get("schluessel")
        if not isinstance(schl, str) or not SCHLUESSEL.fullmatch(schl):
            raise Fehler("„schluessel“ fehlt oder ist ungültig.")
        if not isinstance(d.get("foil"), bool) or "wunschpreis" not in d:
            raise Fehler("Bitte „foil“ (true/false) und „wunschpreis“ (Zahl oder null) angeben.")
        preis = kaufliste.preis_pruefen(d["wunschpreis"])
        schritt = await _schreiben(mit_katalog(lambda con, kat: kaufliste.wunsch_setzen(
            con, kat, h.nutzer, schl, d["foil"], preis)))
        return JSONResponse({"schluessel": schl, "wunschpreis": preis, "schritt": schritt})

    return [
        Route("/kaufliste", liste),
        Route("/kaufliste/deck/{id:int}", deck_setzen, methods=["POST"]),
        Route("/kaufliste/karte", karte_anlegen, methods=["POST"]),
        Route("/kaufliste/karte/{id:int}", karte_aendern, methods=["PATCH"]),
        Route("/kaufliste/karte/{id:int}", karte_loeschen, methods=["DELETE"]),
        Route("/kaufliste/wunsch", wunsch, methods=["PUT"]),
    ]
