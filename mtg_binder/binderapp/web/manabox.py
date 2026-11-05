"""ManaBox-Import im Add-on (db/manabox_app.py), alle Routen nur angemeldet, Antwort als JSON.

    POST /api/manabox/uebersicht  {"name", "csv"}           Einträge mit Vorschlag. Schreibt nichts.
    POST /api/manabox/pruefen     {"name", "csv", "plan"}   Probelauf, wird zurückgerollt. Schreibt nichts.
    POST /api/manabox/importieren {"name", "csv", "plan"}   Sicherung, dann Import als ein Schritt.

Die Datei reist bei jedem Aufruf mit, so hält der Server keinen Zustand zwischen den Schritten. Der Name dient nur der
Anzeige und, beim Listen-Export, als Name des Eintrags, nie als Pfad.
Ohne Katalog (409) gibt es keinen Import: Die Sortierregeln und die Kistenansicht brauchen die Kartendaten.
"""
from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from binderapp.db import datenbank, listen_import, manabox_app
from binderapp.db.manabox_import import ImportFehler
from binderapp.preise import zeitplan
from binderapp.web.helfer import Fehler
from binderapp.web.kistenschreiben import DATEINAME_MAX, datei_lesen

ANFRAGE_MAX = listen_import.MAX_BYTES * 2    # die CSV steckt als Text im JSON, Sonderzeichen werden länger
KEIN_KATALOG = "Für den Import braucht das Add-on den Kartenkatalog. Bitte einmal die Erkennung am PC starten."


async def _anfrage(request) -> tuple[bytes, str, object]:
    roh = await datei_lesen(request, ANFRAGE_MAX)
    try:
        d = json.loads(roh)
    except ValueError:
        raise Fehler("Ungültige Anfrage.")
    if not isinstance(d, dict) or not isinstance(d.get("csv"), str):
        raise Fehler("Ungültige Anfrage.")
    name = Path(str(d.get("name") or "manabox.csv").replace("\\", "/")).name
    name = "".join(z for z in name if z.isprintable())[:DATEINAME_MAX] or "manabox.csv"
    return d["csv"].encode("utf-8"), name, d.get("plan")


def routen(h) -> list[Route]:

    def uebersicht_lesen(daten: bytes, name: str) -> dict:
        try:
            posten = manabox_app.lesen(daten, name)
            with closing(h.db()) as con:
                return {"datei": name, **manabox_app.uebersicht(con, h.nutzer, posten)}
        except ImportFehler as e:
            raise Fehler(str(e))

    def lauf(daten: bytes, name: str, plan, probelauf: bool) -> dict:
        kat = h.katalog()
        if kat is None:
            raise Fehler(KEIN_KATALOG, 409)
        try:
            with closing(kat):
                # Vor dem Sichern prüfen: Eine falsche Datei soll keine Sicherung verdrängen
                manabox_app._plan_lesen(plan, manabox_app.lesen(daten, name))
                sicherung = None if probelauf else h.sichern()
                with closing(datenbank.verbinden(h.datenbank)) as con:
                    b = manabox_app.importieren(con, h.nutzer, daten, name, plan, kat, probelauf=probelauf)
        except ImportFehler as e:
            raise Fehler(str(e))
        b["sicherung"] = sicherung.name if sicherung else None
        return b

    @h.schreibend
    async def uebersicht(request):
        daten, name, _ = await _anfrage(request)
        return JSONResponse(await run_in_threadpool(uebersicht_lesen, daten, name))

    @h.schreibend
    async def pruefen(request):
        daten, name, plan = await _anfrage(request)
        return JSONResponse(await run_in_threadpool(lauf, daten, name, plan, True))

    @h.schreibend
    async def importieren(request):
        daten, name, plan = await _anfrage(request)
        antwort = await run_in_threadpool(lauf, daten, name, plan, False)
        zeitplan.anstossen()   # Add-on: Preise der neuen Drucke bald nachtragen
        return JSONResponse(antwort)

    return [
        Route("/manabox/uebersicht", uebersicht, methods=["POST"]),
        Route("/manabox/pruefen", pruefen, methods=["POST"]),
        Route("/manabox/importieren", importieren, methods=["POST"]),
    ]
