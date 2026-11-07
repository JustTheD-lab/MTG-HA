"""Proxy-Druck mit Silhouette Card Maker (proxy/auftrag.py). Lesen frei, Schreiben nur angemeldet. Nur mit `ha=True`
und gesetztem `BINDER_PROXY` (das Add-on setzt es, am PC gibt es den Bereich nicht).

    GET    /api/proxy                         Stand, laufender Auftrag, Rückseiten, Ablage erreichbar?
    POST   /api/proxy/stand/suchen            sofort nach neuem Stand suchen (Knopf)
    POST   /api/proxy/stand/zurueck           vorherigen Stand verwenden
    POST   /api/proxy/pruefen                 {"text", "format"?, "sets_ignorieren"?}  Vorschau, schreibt nichts
    POST   /api/proxy/auftrag                 {"text", "format", "name", "rueckseite", "sets_ignorieren", "extra_art"}
    GET    /api/proxy/auftrag/{id}            Zustand und Fortschritt
    POST   /api/proxy/auftrag/{id}/weiter     {"weiter": true|false}  PDF ohne die fehlenden Karten oder abbrechen
    GET    /api/proxy/decks                   Decks der App für „Aus Deck übernehmen“ (id, name)
    GET    /api/proxy/deck/{id}?gruppen=      Proxy-Liste eines Decks; Gruppen fehlt,proxy,vorhanden,basics
                                              (ohne Angabe: fehlt,proxy), Druck aus der Deckliste, Format mtga
    GET    /api/proxy/pdfs                    die letzten 50 PDFs
    GET    /api/proxy/pdf/{datei}             Download
    GET    /api/proxy/rueckseiten             Rückseiten (mit dem neutralen Beispiel)
    POST   /api/proxy/rueckseiten?name=       Bild als Rumpf (PNG oder JPEG, höchstens 10 MB)
    GET    /api/proxy/rueckseite/{id}/bild    das Bild
    DELETE /api/proxy/rueckseite/{id}         eine hochgeladene Rückseite entfernen
"""
from __future__ import annotations

import threading
from contextlib import closing
from functools import wraps
from pathlib import Path

from binderapp.db.datenbank import version
from binderapp.decks import abgleich

from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from binderapp.proxy import auftrag as pa
from binderapp.proxy import staende as st
from binderapp.web.helfer import Fehler, fehler
from binderapp.web.kistenschreiben import datei_lesen, dateiname

NUR_ADDON = "Den Proxy-Druck gibt es nur im Home-Assistant-Add-on."


def _dienst() -> pa.Dienst:
    d = pa.dienst()
    if d is None:
        raise Fehler(NUR_ADDON, 404)
    return d


def _fehler_umwandeln(handler):
    @wraps(handler)
    async def huelle(request):
        try:
            return await handler(request)
        except pa.Proxyfehler as f:
            return fehler(str(f), f.status)
        except st.Standfehler as f:
            return fehler(str(f), 409)
        except Fehler as f:
            return fehler(str(f), f.status)
    return huelle


def routen(h) -> list[Route]:

    def _ablage_ok() -> bool:
        return h.bilder_ok()

    def _bereit() -> pa.Dienst:
        d = _dienst()
        d.ablage_ok = _ablage_ok
        return d

    @_fehler_umwandeln
    async def uebersicht(request):
        d = _bereit()
        a = d.auftrag
        return JSONResponse({"stand": d.staende.anzeige(), "auftrag": a.anzeige() if a else None,
                             "ablage": _ablage_ok(), "katalog": h.katalog_pfad is not None
                             and Path(h.katalog_pfad).exists(), "rueckseiten": d.rueckseiten(),
                             "formate": [{"name": n, "titel": t} for n, t in pa.FORMATNAMEN.items()],
                             "hinweis": "Nur für Casual und Playtesting. Proxys bleiben als Proxy erkennbar."})

    @h.schreibend
    @_fehler_umwandeln
    async def suchen(request):
        d = _bereit()
        if d.abgleich.is_alive():
            d.abgleich.anstossen()
        else:   # ohne Thread (Tests am PC): einmal im Hintergrund suchen
            threading.Thread(target=d.staende.suchen, kwargs={"erzwingen": True}, daemon=True).start()
        return JSONResponse({**d.staende.anzeige(), "sucht": True})

    @h.schreibend
    @_fehler_umwandeln
    async def zurueck(request):
        d = _bereit()
        if not await run_in_threadpool(d.staende.zurueck, "Vom Nutzer zurückgestellt"):
            raise Fehler("Es gibt keinen vorherigen Stand.", 409)
        return JSONResponse(d.staende.anzeige())

    def _pruefen(d: pa.Dienst, daten: dict) -> dict:
        kat = h.katalog()
        try:
            return d.pruefen(daten.get("text"), daten.get("format") or None, bool(daten.get("sets_ignorieren")), kat)
        finally:
            if kat is not None:
                kat.close()

    @h.schreibend
    @_fehler_umwandeln
    async def pruefen(request):
        d = _bereit()
        daten = await h.json_daten(request)
        return JSONResponse(await run_in_threadpool(_pruefen, d, daten))

    @h.schreibend
    @_fehler_umwandeln
    async def neu(request):
        d = _bereit()
        daten = await h.json_daten(request)
        a = await run_in_threadpool(d.neu, daten.get("text"), daten.get("format"), daten.get("name"),
                                    daten.get("rueckseite"), daten)
        return JSONResponse(a, status_code=202)

    @_fehler_umwandeln
    async def auftrag(request):
        return JSONResponse(_bereit().holen(request.path_params["id"]))

    @h.schreibend
    @_fehler_umwandeln
    async def weiter(request):
        daten = await h.json_daten(request)
        if not isinstance(daten.get("weiter"), bool):
            raise Fehler("Ungültige Anfrage.")
        return JSONResponse(_bereit().entscheiden(request.path_params["id"], daten["weiter"]))

    def _decks() -> list[dict]:
        with closing(h.db()) as con:
            if version(con) < 8:
                return []
            return [{"id": z["id"], "name": z["name"]} for z in con.execute(
                "SELECT id, name FROM deck WHERE nutzer_id = ? ORDER BY name COLLATE NOCASE, id", (h.nutzer,))]

    @_fehler_umwandeln
    async def decks(request):
        return JSONResponse({"decks": await run_in_threadpool(_decks), "standard": list(pa.DECK_STANDARD),
                             "gruppen": list(pa.DECK_GRUPPEN)})

    def _deck(deck_id: int, gruppen) -> dict:
        with closing(h.db()) as con:
            if version(con) < 8:
                raise Fehler("Decks gibt es erst mit einer neueren Datenbank.", 409)
            try:
                d = abgleich.deck_holen(con, h.nutzer, deck_id)
            except LookupError as f:
                raise Fehler(str(f), 404)
            return pa.deck_liste(abgleich.abgleichen(con, h.nutzer, d), gruppen)

    @_fehler_umwandeln
    async def deck(request):
        wert = request.query_params.get("gruppen")
        gruppen = pa.DECK_STANDARD if wert is None else [g for g in wert.split(",") if g in pa.DECK_GRUPPEN]
        return JSONResponse(await run_in_threadpool(_deck, request.path_params["id"], gruppen))

    @_fehler_umwandeln
    async def pdfs(request):
        return JSONResponse({"pdfs": await run_in_threadpool(_bereit().pdfs), "ablage": _ablage_ok()})

    @_fehler_umwandeln
    async def pdf(request):
        pfad = await run_in_threadpool(_bereit().pdf_pfad, request.path_params["datei"])
        return FileResponse(pfad, media_type="application/pdf", filename=pfad.name,
                            headers={"Cache-Control": "no-store"})

    @_fehler_umwandeln
    async def rueckseiten(request):
        return JSONResponse({"rueckseiten": _bereit().rueckseiten()})

    @h.schreibend
    @_fehler_umwandeln
    async def rueckseite_neu(request):
        d = _bereit()
        daten = await datei_lesen(request, pa.RUECKSEITE_MAX)
        name = dateiname(request).rsplit(".", 1)[0]
        return JSONResponse(await run_in_threadpool(d.rueckseite_neu, daten, name), status_code=201)

    @_fehler_umwandeln
    async def rueckseite_bild(request):
        pfad = _bereit().rueckseite_bild(request.path_params["id"])
        return FileResponse(pfad, media_type="image/png" if pfad.suffix == ".png" else "image/jpeg",
                            headers={"Cache-Control": "private, max-age=86400"})

    @h.schreibend
    @_fehler_umwandeln
    async def rueckseite_weg(request):
        d = _bereit()
        await run_in_threadpool(d.rueckseite_entfernen, request.path_params["id"])
        return JSONResponse({"ok": True})

    return [
        Route("/proxy", uebersicht),
        Route("/proxy/stand/suchen", suchen, methods=["POST"]),
        Route("/proxy/stand/zurueck", zurueck, methods=["POST"]),
        Route("/proxy/pruefen", pruefen, methods=["POST"]),
        Route("/proxy/auftrag", neu, methods=["POST"]),
        Route("/proxy/auftrag/{id}", auftrag),
        Route("/proxy/auftrag/{id}/weiter", weiter, methods=["POST"]),
        Route("/proxy/decks", decks),
        Route("/proxy/deck/{id:int}", deck),
        Route("/proxy/pdfs", pdfs),
        Route("/proxy/pdf/{datei}", pdf),
        Route("/proxy/rueckseiten", rueckseiten),
        Route("/proxy/rueckseiten", rueckseite_neu, methods=["POST"]),
        Route("/proxy/rueckseite/{id}/bild", rueckseite_bild),
        Route("/proxy/rueckseite/{id}", rueckseite_weg, methods=["DELETE"]),
    ]
