"""Routen „Liste abziehen“ (Paket M, 28.10.2026); Logik in db/abzug.py.

    POST /api/abzug/vorschau?name=DATEI.csv          Rumpf: die Liste (höchstens 5 MB). Schreibt nichts. Antwort:
                                                     Summen, Positionen mit Entnahme je Ort und Status, flache
                                                     Entnahmeliste nach Ort, frühere Abzüge derselben Datei
                                                     („frueher“) und die Entnahmeliste als CSV-Text („csv“)
    POST /api/abzug/ausfuehren?name=…&karten=N[&abgewaehlt=ID,ID]
                                                     Rumpf: dieselbe Liste. Sicherung, dann ein Bedienschritt;
                                                     `karten` ist die bestätigte Kartenzahl (sonst 409)
    GET  /api/abzuege                                Abzugsprotokoll (Datum, Datei, Prüfsumme, Posten, Karten,
                                                     zurückgenommen)
    GET  /api/abzug/{schritt}/entnahme[?format=csv]  Entnahmeliste eines ausgeführten Abzugs
Zurücknehmen: POST /api/schritt/{schritt}/rueckgaengig (kistenschreiben.py). Vorschau und Ausführen nur
angemeldet (Upload), Lesen offen wie der Rest der App.
"""
from __future__ import annotations

from contextlib import closing

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from binderapp.db import abzug, schritte
from binderapp.db.manabox_import import ImportFehler
from binderapp.web.helfer import Fehler
from binderapp.web.kisten import ZAHL, _ab_version_3, _lesen
from binderapp.web.kistenschreiben import datei_lesen, dateiname

__all__ = ["routen"]

ABGEWAEHLT_MAX = 2000


def _abgewaehlt(request) -> set[int]:
    text = request.query_params.get("abgewaehlt", "")
    werte = [w for w in text.split(",") if w.strip()]
    if len(werte) > ABGEWAEHLT_MAX:
        raise Fehler(f"Höchstens {ABGEWAEHLT_MAX} abgewählte Karten.")
    if not all(ZAHL.fullmatch(w.strip()) for w in werte):
        raise Fehler("„abgewaehlt“ muss eine Liste von Karten-IDs sein.")
    return {int(w) for w in werte}


def routen(h) -> list[Route]:

    def _plan(con, daten: bytes, name: str) -> dict:
        try:
            return abzug.plan(con, h.nutzer, daten, name)
        except ImportFehler as e:
            raise Fehler(str(e))

    @h.schreibend
    async def vorschau(request):
        name = dateiname(request)
        daten = await datei_lesen(request, abzug.MAX_BYTES)

        def arbeit():
            with closing(h.db()) as con:
                con.execute("BEGIN")
                _ab_version_3(con, 409)
                v = _plan(con, daten, name)
            v["csv"] = abzug.entnahme_csv(v["entnahme"])
            return v
        return JSONResponse(await run_in_threadpool(arbeit))

    @h.schreibend
    async def ausfuehren(request):
        name = dateiname(request)
        erwartet = request.query_params.get("karten", "")
        if not ZAHL.fullmatch(erwartet):
            raise Fehler("Bitte die bestätigte Kartenzahl angeben („karten“).")
        abgewaehlt = _abgewaehlt(request)
        pruefsumme = request.query_params.get("pruefsumme") or None
        if pruefsumme is not None and not schritte.MUSTER.fullmatch(pruefsumme):
            raise Fehler("Ungültige Prüfsumme.")
        daten = await datei_lesen(request, abzug.MAX_BYTES)

        def arbeit():
            with closing(h.db()) as con:
                _ab_version_3(con, 409)
            sicherung = h.sichern()

            def schreiben(con):
                v = _plan(con, daten, name)
                return abzug.ausfuehren(con, h.nutzer, v, abgewaehlt, int(erwartet), sicherung.name, pruefsumme)
            return h.schreibvorgang(schreiben)
        return JSONResponse(await run_in_threadpool(arbeit))

    @_lesen
    def liste(request):
        with closing(h.db()) as con:
            _ab_version_3(con, 404)
            return JSONResponse({"abzuege": abzug.abzuege(con, h.nutzer)})

    @_lesen
    def entnahme(request):
        schritt = schritte.kennung(request.path_params["schritt"])
        als_csv = request.query_params.get("format") == "csv"
        with closing(h.db()) as con:
            _ab_version_3(con, 404)
            d = abzug.entnahme_nach_abzug(con, h.nutzer, schritt)
        if als_csv:
            return Response(abzug.entnahme_csv(d["entnahme"]), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="entnahme-{schritt[:8]}.csv"'})
        return JSONResponse(d)

    return [
        Route("/abzug/vorschau", vorschau, methods=["POST"]),
        Route("/abzug/ausfuehren", ausfuehren, methods=["POST"]),
        Route("/abzuege", liste),
        Route("/abzug/{schritt}/entnahme", entnahme),
    ]
