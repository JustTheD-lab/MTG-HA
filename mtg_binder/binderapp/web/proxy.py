"""Proxy-Druck mit Silhouette Card Maker (proxy/auftrag.py). Lesen frei, Schreiben nur angemeldet. Nur mit `ha=True`
und gesetztem `BINDER_PROXY` (das Add-on setzt es, am PC gibt es den Bereich nicht).

    GET    /api/proxy                         Stand, laufender Auftrag, Rückseiten, Ablage erreichbar?
    POST   /api/proxy/stand/suchen            sofort nach neuem Stand suchen (Knopf)
    POST   /api/proxy/stand/zurueck           vorherigen Stand verwenden
    POST   /api/proxy/pruefen                 {"text", "format"?, "sets_ignorieren"?}  Vorschau, schreibt nichts
    POST   /api/proxy/auftrag                 {"text", "format", "name", "rueckseite", "sets_ignorieren", "extra_art",
                                               "versatz" (Standard true: Kalibrierung einrechnen),
                                               "druckwahl": "liste" | "guenstig" | "erster"}
    GET    /api/proxy/auftrag/{id}            Zustand und Fortschritt
    POST   /api/proxy/auftrag/{id}/weiter     {"weiter": true|false}  PDF ohne die fehlenden Karten oder abbrechen
    GET    /api/proxy/decks                   Decks der App für „Aus Deck übernehmen“ (id, name)
    GET    /api/proxy/deck/{id}?gruppen=      Proxy-Liste eines Decks; Gruppen fehlt,proxy,vorhanden,basics
                                              (ohne Angabe: fehlt,proxy), Druck aus der Deckliste, Format mtga
    POST   /api/proxy/archidekt               {"link", "basics"} Proxy-Liste aus einem Archidekt-Deck (Hauptdeck
                                              mit Commander, Druck aus Archidekt, Basics nur auf Wunsch; Format mtga)
    GET    /api/proxy/kalibrierung            Druckversatz der Rückseiten {x, y, winkel, geaendert, vorher}
    POST   /api/proxy/kalibrierung            {"x", "y", "winkel"} setzen oder {"ablesen": {"x", "y"}} dazuzählen
    GET    /api/proxy/kalibrierung/bogen      Kalibrierbogen A4 mit dem gespeicherten Versatz (PDF)
    GET    /api/proxy/pdfs                    die letzten 50 PDFs
    GET    /api/proxy/pdf/{datei}             Download
    GET    /api/proxy/rueckseiten             Rückseiten (mit dem neutralen Beispiel)
    POST   /api/proxy/rueckseiten?name=       Bild als Rumpf (PNG oder JPEG, höchstens 40 MB)
    GET    /api/proxy/rueckseite/{id}/bild    das Bild
    DELETE /api/proxy/rueckseite/{id}         eine hochgeladene Rückseite entfernen
    GET    /api/proxy/eigene                  eigene Kartenbilder (proxy/eigene.py)
    POST   /api/proxy/eigene?name=            Bild als Rumpf (PNG, JPEG, WebP) → Entwurf, automatisch eingepasst
    POST   /api/proxy/eigene/url              {"url", "name"?} Bild von einer öffentlichen Adresse → Entwurf
    POST   /api/proxy/eigene/{id}/hinten      Bild als Rumpf: eigene zweite Seite
    POST   /api/proxy/eigene/{id}/hinten/url  {"url"} zweite Seite von einer Adresse
    PATCH  /api/proxy/eigene/{id}             {"name", "karte", "vorgabe", "zuschnitt": {seite: art}, "fertig": true}
    DELETE /api/proxy/eigene/{id}[/hinten]    Bild bzw. nur die zweite Seite entfernen
    GET    /api/proxy/eigene/{id}/bild?seite=vorne|hinten&klein=1
    Aufträge nehmen dazu "eigene": {Schlüssel: id oder null für Scryfall} und "zusatz": [{"id", "anzahl"}].
"""
from __future__ import annotations

import threading
from contextlib import closing
from functools import wraps
from pathlib import Path

from binderapp.db.datenbank import version
from binderapp.decks import abgleich, lesen

from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from binderapp.proxy import auftrag as pa
from binderapp.proxy import eigene
from binderapp.proxy import staende as st
from binderapp.proxy.laeufer import ZUSCHNITTE
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
        except (pa.Proxyfehler, eigene.Eigenfehler) as f:
            return fehler(str(f), f.status)
        except st.Standfehler as f:
            return fehler(str(f), 409)
        except Fehler as f:
            return fehler(str(f), f.status)
    return huelle


def routen(h) -> list[Route]:

    def _ablage_ok() -> bool:
        return h.bilder_ok()

    def _preisdatei() -> Path:
        # wie Secret Lair: der tägliche Abruf legt den ganzen Price Guide neben die Datenbank
        return Path(h.datenbank).parent / "preise" / "price_guide_1.json"

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
                             "kalibrierung": d.kalibrierung(), "eigene": d.eigene.liste(),
                             "zuschnitte": list(ZUSCHNITTE),
                             "guenstig_moeglich": h.katalog_pfad is not None and Path(h.katalog_pfad).exists()
                             and _preisdatei().is_file(),
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
                                    daten.get("rueckseite"), daten, h.katalog_pfad, _preisdatei())
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

    def _archidekt(link, basics: bool) -> dict:
        try:
            kopf, zeilen = lesen.archidekt_zeilen(lesen.archidekt_holen(lesen.archidekt_id(link)))
            kat = h.katalog()
            if kat is not None:   # Druck aus dem Katalog bestätigen; ohne Katalog gilt Set und Nummer von Archidekt
                try:
                    zeilen, _ = lesen.aufloesen(kat, zeilen)
                finally:
                    kat.close()
        except lesen.DeckFehler as f:
            raise Fehler(str(f), f.status)
        if not zeilen:
            raise Fehler("Das Archidekt-Deck hat keine Karten im Hauptdeck.")
        return pa.archidekt_liste(kopf, zeilen, basics)

    @h.schreibend
    @_fehler_umwandeln
    async def archidekt(request):
        daten = await h.json_daten(request)
        return JSONResponse(await run_in_threadpool(_archidekt, daten.get("link"), daten.get("basics") is True))

    @_fehler_umwandeln
    async def kalibrierung(request):
        return JSONResponse(_bereit().kalibrierung())

    @h.schreibend
    @_fehler_umwandeln
    async def kalibrierung_setzen(request):
        daten = await h.json_daten(request)
        d = _bereit()
        if "ablesen" in daten:
            feld = daten["ablesen"] if isinstance(daten["ablesen"], dict) else {}
            return JSONResponse(d.kalibrierung_ablesen(feld.get("x"), feld.get("y")))
        return JSONResponse(d.kalibrierung_setzen(daten.get("x"), daten.get("y"), daten.get("winkel", 0)))

    @_fehler_umwandeln
    async def kalibrierbogen(request):
        pfad = await run_in_threadpool(_bereit().kalibrierbogen)
        return FileResponse(pfad, media_type="application/pdf", filename="kalibrierbogen-a4.pdf",
                            headers={"Cache-Control": "no-store"})

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

    # Eigene Kartenbilder
    @_fehler_umwandeln
    async def eigene_liste(request):
        return JSONResponse({"eigene": _bereit().eigene.liste(), "zuschnitte": list(ZUSCHNITTE)})

    @h.schreibend
    @_fehler_umwandeln
    async def eigen_neu(request):
        b = _bereit().eigene
        daten = await datei_lesen(request, eigene.BILD_MAX)
        name = dateiname(request).rsplit(".", 1)[0]
        return JSONResponse(await run_in_threadpool(b.neu, daten, name, "datei"), status_code=201)

    @h.schreibend
    @_fehler_umwandeln
    async def eigen_url(request):
        b = _bereit().eigene
        daten = await h.json_daten(request)
        bild, name = await run_in_threadpool(eigene.url_holen, daten.get("url"))
        name = eigene.text(daten.get("name")) or name.rsplit(".", 1)[0]
        return JSONResponse(await run_in_threadpool(b.neu, bild, name, "url"), status_code=201)

    @h.schreibend
    @_fehler_umwandeln
    async def eigen_hinten(request):
        b = _bereit().eigene
        daten = await datei_lesen(request, eigene.BILD_MAX)
        return JSONResponse(await run_in_threadpool(b.seite_neu, request.path_params["id"], daten), status_code=201)

    @h.schreibend
    @_fehler_umwandeln
    async def eigen_hinten_url(request):
        b = _bereit().eigene
        daten = await h.json_daten(request)
        bild, _ = await run_in_threadpool(eigene.url_holen, daten.get("url"))
        return JSONResponse(await run_in_threadpool(b.seite_neu, request.path_params["id"], bild), status_code=201)

    @h.schreibend
    @_fehler_umwandeln
    async def eigen_aendern(request):
        b = _bereit().eigene
        daten = await h.json_daten(request)
        return JSONResponse(await run_in_threadpool(b.aendern, request.path_params["id"], daten))

    @h.schreibend
    @_fehler_umwandeln
    async def eigen_weg(request):
        b = _bereit().eigene
        await run_in_threadpool(b.entfernen, request.path_params["id"])
        return JSONResponse({"ok": True})

    @h.schreibend
    @_fehler_umwandeln
    async def eigen_hinten_weg(request):
        b = _bereit().eigene
        return JSONResponse(await run_in_threadpool(b.entfernen, request.path_params["id"], "hinten"))

    @_fehler_umwandeln
    async def eigen_bild(request):
        klein = request.query_params.get("klein") == "1"
        pfad = _bereit().eigene.pfad(request.path_params["id"], request.query_params.get("seite") or "vorne", klein)
        return FileResponse(pfad, media_type="image/jpeg" if klein else "image/png",
                            headers={"Cache-Control": "private, max-age=86400"})

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
        Route("/proxy/archidekt", archidekt, methods=["POST"]),
        Route("/proxy/kalibrierung", kalibrierung),
        Route("/proxy/kalibrierung", kalibrierung_setzen, methods=["POST"]),
        Route("/proxy/kalibrierung/bogen", kalibrierbogen),
        Route("/proxy/pdfs", pdfs),
        Route("/proxy/pdf/{datei}", pdf),
        Route("/proxy/rueckseiten", rueckseiten),
        Route("/proxy/rueckseiten", rueckseite_neu, methods=["POST"]),
        Route("/proxy/rueckseite/{id}/bild", rueckseite_bild),
        Route("/proxy/rueckseite/{id}", rueckseite_weg, methods=["DELETE"]),
        Route("/proxy/eigene", eigene_liste),
        Route("/proxy/eigene", eigen_neu, methods=["POST"]),
        Route("/proxy/eigene/url", eigen_url, methods=["POST"]),
        Route("/proxy/eigene/{id}", eigen_aendern, methods=["PATCH"]),
        Route("/proxy/eigene/{id}", eigen_weg, methods=["DELETE"]),
        Route("/proxy/eigene/{id}/hinten", eigen_hinten, methods=["POST"]),
        Route("/proxy/eigene/{id}/hinten", eigen_hinten_weg, methods=["DELETE"]),
        Route("/proxy/eigene/{id}/hinten/url", eigen_hinten_url, methods=["POST"]),
        Route("/proxy/eigene/{id}/bild", eigen_bild),
    ]
