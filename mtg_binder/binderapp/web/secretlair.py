"""Secret-Lair-Drops im Add-on (secretlair/drops.py). Lesen frei, Schreiben nur angemeldet. Nur mit `ha=True`.

    GET    /api/secretlair                 alle Drops mit Summen, Standardkosten, Stand des Abgleichs
    GET    /api/secretlair/{id}            ein Drop mit Karten und Verlauf
    POST   /api/secretlair                 {"name", "verkauf"?, "text", "probe"?}  Drop von Hand, mit `probe` nur prüfen
    PATCH  /api/secretlair/{id}            {"name"?, "kosten"?}  Kosten: Euro oder null für den Standard
    DELETE /api/secretlair/{id}            nur Drops von Hand
    POST   /api/secretlair/kosten          {"kosten"}  Standardkosten je Drop in Euro, null = keine
    POST   /api/secretlair/aktualisieren   Abgleich und Bewertung sofort

Die Preisdatei liegt neben der Datenbank unter preise/, wie beim täglichen Abruf.
"""
from __future__ import annotations

import re
from contextlib import closing
from datetime import date
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from binderapp.secretlair import drops
from binderapp.web.helfer import Fehler, fehler

KOSTEN_MAX = 10000
TEXT_MAX = 8000
DATUM = re.compile(r"\d{4}-\d{2}-\d{2}")
KEIN_KATALOG = "Dafür braucht das Add-on den Kartenkatalog. Bitte einmal die Erkennung am PC starten."


def _name(wert) -> str:
    name = wert.strip() if isinstance(wert, str) else ""
    if not name or len(name) > drops.NAME_MAX:
        raise Fehler(f"Der Name muss 1 bis {drops.NAME_MAX} Zeichen haben.")
    return name


def _kosten(wert) -> float | None:
    if wert is None:
        return None
    if isinstance(wert, bool) or not isinstance(wert, (int, float)) or not 0 <= wert <= KOSTEN_MAX:
        raise Fehler(f"Die Kosten müssen zwischen 0 und {KOSTEN_MAX} Euro liegen.")
    return float(wert)


def routen(h) -> list[Route]:
    preisdatei = Path(h.datenbank).parent / "preise" / "price_guide_1.json"

    def _id(request) -> int:
        return request.path_params["id"]

    def uebersicht(request):
        with closing(h.db()) as con:
            return JSONResponse({"drops": drops.liste(con), "kosten": drops.standard_kosten(con),
                                 "stand": drops.stand(con), "katalog": h.katalog_pfad is not None
                                 and Path(h.katalog_pfad).exists()})

    def einer(request):
        with closing(h.db()) as con:
            d = drops.einzeln(con, _id(request))
        return fehler("Diesen Drop gibt es nicht.", 404) if d is None else JSONResponse(d)

    def _einfuegen(d: dict) -> dict:
        kat = h.katalog()
        if kat is None:
            raise Fehler(KEIN_KATALOG, 409)
        text = d.get("text")
        if not isinstance(text, str) or len(text) > TEXT_MAX:
            raise Fehler("Bitte die Kartenliste als Text einfügen.")
        with closing(kat):
            karten = drops.karten_finden(kat, drops.text_lesen(text))
        if not karten or len(karten) > drops.ZEILEN_MAX:
            raise Fehler(f"Die Liste braucht 1 bis {drops.ZEILEN_MAX} Karten.")
        antwort = {"karten": karten, "unbekannt": [k["name"] for k in karten if k["oracle_id"] is None]}
        if d.get("probe"):
            return antwort
        name = _name(d.get("name"))
        verkauf = d.get("verkauf") or None
        if verkauf is not None and not (isinstance(verkauf, str) and DATUM.fullmatch(verkauf)):
            raise Fehler("Der Verkaufstag ist ungültig.")
        drop = h.schreibvorgang(lambda con: drops.einfuegen(con, name, verkauf, karten))
        # Der Abgleich verbindet die Karten mit den Drucken des Katalogs; MTGJSON bleibt dabei außen vor
        with closing(h.katalog()) as kat2:
            h.schreibvorgang(lambda con: drops.abgleichen(con, kat2, None, date.today()))
        drops.bewerten_lauf(h.datenbank, h.katalog_pfad, preisdatei)
        return {**antwort, "id": drop}

    @h.schreibend
    async def einfuegen(request):
        d = await h.json_daten(request)
        return JSONResponse(await run_in_threadpool(_einfuegen, d))

    @h.schreibend
    async def aendern(request):
        d = await h.json_daten(request)
        drop = _id(request)
        if set(d) - {"name", "kosten"} or not d:
            raise Fehler("Erlaubt sind „name“ und „kosten“.")
        name = _name(d["name"]) if "name" in d else None
        kosten = _kosten(d.get("kosten"))

        def arbeit(con):
            if con.execute("SELECT 1 FROM secret_drop WHERE id = ?", (drop,)).fetchone() is None:
                raise Fehler("Diesen Drop gibt es nicht.", 404)
            if name is not None:
                con.execute("UPDATE secret_drop SET name = ?, umbenannt = 1 WHERE id = ?", (name, drop))
            if "kosten" in d:
                con.execute("UPDATE secret_drop SET kosten = ? WHERE id = ?", (kosten, drop))
            return {"id": drop}
        return JSONResponse(await run_in_threadpool(h.schreibvorgang, arbeit))

    @h.schreibend
    async def loeschen(request):
        drop = _id(request)

        def arbeit(con):
            z = con.execute("SELECT quelle FROM secret_drop WHERE id = ?", (drop,)).fetchone()
            if z is None:
                raise Fehler("Diesen Drop gibt es nicht.", 404)
            if z["quelle"] != "hand":
                raise Fehler("Nur Drops von Hand lassen sich löschen, die anderen kommen beim Abgleich wieder.", 409)
            con.execute("DELETE FROM secret_drop WHERE id = ?", (drop,))
            return {"id": drop}
        return JSONResponse(await run_in_threadpool(h.schreibvorgang, arbeit))

    @h.schreibend
    async def kosten_setzen(request):
        d = await h.json_daten(request)
        kosten = _kosten(d.get("kosten"))
        await run_in_threadpool(h.schreibvorgang, lambda con: drops.wert_setzen(con, "kosten", kosten))
        return JSONResponse({"kosten": kosten})

    def _aktualisieren() -> dict:
        if h.katalog_pfad is None or not Path(h.katalog_pfad).exists():
            raise Fehler(KEIN_KATALOG, 409)
        return drops.einmal(h.datenbank, h.katalog_pfad, preisdatei)

    @h.schreibend
    async def aktualisieren(request):
        return JSONResponse(await run_in_threadpool(_aktualisieren))

    return [
        Route("/secretlair", uebersicht),
        Route("/secretlair", einfuegen, methods=["POST"]),
        Route("/secretlair/kosten", kosten_setzen, methods=["POST"]),
        Route("/secretlair/aktualisieren", aktualisieren, methods=["POST"]),
        Route("/secretlair/{id:int}", einer),
        Route("/secretlair/{id:int}", aendern, methods=["PATCH"]),
        Route("/secretlair/{id:int}", loeschen, methods=["DELETE"]),
    ]
