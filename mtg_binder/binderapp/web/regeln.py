"""Routen der Sortierregeln (Runde 01.11.2026, `binderapp.sortieren`), eingebunden unter /api.

    GET    /api/regeln                    Regeln, mögliche Zielkisten und Beschreibung der Felder
    POST   /api/regeln                    neue Regel {name, aktiv, bedingungen, kiste_id}
    PUT    /api/regel/{id}                Regel ändern (dieselben Angaben)
    DELETE /api/regel/{id}                Regel löschen
    POST   /api/regeln/reihenfolge        {ids: [...]} neue Reihenfolge aller Regeln
    GET    /api/kiste/{id}/faecher        Fächer einer Kiste
    PUT    /api/kiste/{id}/faecher        {faecher: [{name, bedingungen}]} ersetzt die Fächer
    GET    /api/regeln/suche?q=           Karten der Sammlung für die Probe (Name enthält)
    GET    /api/regeln/probe?karte=ID     welche Regel greift für diese Karte und warum
    GET    /api/regeln/einsortieren       was „Jetzt einsortieren“ mit dem Scan-Eingang täte
    POST   /api/regeln/einsortieren       Regeln auf den ganzen Scan-Eingang anwenden (ein Schritt mit Rückgängig)

Schreiben mit Historie (Quelle „nutzer“). Bedingungen nur aus festen Feldern, Werte als Parameter.
"""
from __future__ import annotations

from contextlib import closing

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from binderapp.db.erkennung import SCAN_MARKE
from binderapp.sortieren import einsortieren, regeln
from binderapp.web.helfer import Fehler, fehler

GROSSE_ID = 2 ** 31
SUCHE_MAX = 20
SUCHTEXT_MAX = 60
NICHT_DA = "Sortierregeln gibt es erst ab Datenbankversion 6 (neue Version des Add-ons starten)."


def _version(con, status: int = 409) -> None:
    if not einsortieren.aktiv(con):
        raise Fehler(NICHT_DA, status)


def _id(request, wort: str) -> int:
    wert = request.path_params["id"]
    if wert >= GROSSE_ID:
        raise Fehler(f"{wort} nicht gefunden.", 404)
    return wert


def routen(h) -> list[Route]:

    def lesend(funktion):
        def huelle(request):
            try:
                with closing(h.db()) as con:
                    return JSONResponse(funktion(con, request))
            except Fehler as f:
                return fehler(str(f), f.status)
        huelle.__name__ = funktion.__name__
        return huelle

    async def schreiben(arbeit, status: int = 200) -> JSONResponse:
        def innen(con):
            _version(con)
            return arbeit(con)
        return JSONResponse(await run_in_threadpool(h.schreibvorgang, innen), status_code=status)

    # ------------------------------------------------------------ Lesen

    @lesend
    def liste(con, request):
        if not einsortieren.aktiv(con):
            return {"verfuegbar": False, "regeln": [], "kisten": [], "beschreibung": regeln.beschreibung()}
        kisten = [{"id": z["id"], "name": z["name"]} for z in con.execute(
            "SELECT id, name FROM kiste WHERE nutzer_id = ? AND COALESCE(extern_name, '') <> ? ORDER BY regalplatz, id",
            (h.nutzer, SCAN_MARKE))]
        return {"verfuegbar": True, "regeln": einsortieren.regeln_lesen(con, h.nutzer), "kisten": kisten,
                "beschreibung": regeln.beschreibung()}

    @lesend
    def faecher(con, request):
        _version(con, 404)
        kiste_id = _id(request, "Kiste")
        if con.execute("SELECT 1 FROM kiste WHERE id = ? AND nutzer_id = ?", (kiste_id, h.nutzer)).fetchone() is None:
            raise Fehler("Kiste nicht gefunden.", 404)
        return {"faecher": einsortieren.faecher_lesen(con, h.nutzer, kiste_id)}

    @lesend
    def suche(con, request):
        _version(con, 404)
        text = request.query_params.get("q", "").strip()
        if len(text) > SUCHTEXT_MAX:
            raise Fehler(f"Der Suchtext ist zu lang (höchstens {SUCHTEXT_MAX} Zeichen).")
        if len(text) < 2:
            return {"treffer": []}
        muster = "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        zeilen = con.execute(
            "SELECT k.id, k.name, k.set_code, k.nummer, k.foil, k.sprache, ki.name AS kiste FROM karte k"
            " LEFT JOIN kiste ki ON ki.id = k.kiste_id WHERE k.nutzer_id = ? AND k.name LIKE ? ESCAPE '\\'"
            " ORDER BY k.name COLLATE NOCASE, k.id LIMIT ?", (h.nutzer, muster, SUCHE_MAX)).fetchall()
        return {"treffer": [{**dict(z), "foil": bool(z["foil"])} for z in zeilen]}

    @lesend
    def probe(con, request):
        _version(con, 404)
        wert = request.query_params.get("karte", "")
        if not wert.isdigit() or int(wert) >= 2 ** 62:
            raise Fehler("Bitte eine Karte wählen.")
        return einsortieren.probe(con, h.nutzer, int(wert))

    @lesend
    def eingang_vorschau(con, request):
        _version(con, 404)
        return einsortieren.eingang_vorschau(con, h.nutzer)

    # ------------------------------------------------------------ Schreiben

    @h.schreibend
    async def eingang_einsortieren(request):
        return await schreiben(lambda con: einsortieren.eingang_einsortieren(con, h.nutzer))

    @h.schreibend
    async def anlegen(request):
        d = await h.json_daten(request)
        return await schreiben(lambda con: {"id": einsortieren.regel_anlegen(con, h.nutzer, d)}, 201)

    @h.schreibend
    async def aendern(request):
        regel_id = _id(request, "Regel")
        d = await h.json_daten(request)

        def arbeit(con):
            einsortieren.regel_aendern(con, h.nutzer, regel_id, d)
            return {"id": regel_id}
        return await schreiben(arbeit)

    @h.schreibend
    async def loeschen(request):
        regel_id = _id(request, "Regel")

        def arbeit(con):
            einsortieren.regel_loeschen(con, h.nutzer, regel_id)
            return {"entfernt": regel_id}
        return await schreiben(arbeit)

    @h.schreibend
    async def reihenfolge(request):
        d = await h.json_daten(request)

        def arbeit(con):
            einsortieren.reihenfolge(con, h.nutzer, d.get("ids"))
            return {"regeln": einsortieren.regeln_lesen(con, h.nutzer)}
        return await schreiben(arbeit)

    @h.schreibend
    async def faecher_setzen(request):
        kiste_id = _id(request, "Kiste")
        d = await h.json_daten(request)

        def arbeit(con):
            einsortieren.faecher_setzen(con, h.nutzer, kiste_id, d.get("faecher"))
            return {"faecher": einsortieren.faecher_lesen(con, h.nutzer, kiste_id)}
        return await schreiben(arbeit)

    return [
        Route("/regeln", liste),
        Route("/regeln", anlegen, methods=["POST"]),
        Route("/regeln/reihenfolge", reihenfolge, methods=["POST"]),
        Route("/regeln/suche", suche),
        Route("/regeln/probe", probe),
        Route("/regeln/einsortieren", eingang_vorschau),
        Route("/regeln/einsortieren", eingang_einsortieren, methods=["POST"]),
        Route("/regel/{id:int}", aendern, methods=["PUT"]),
        Route("/regel/{id:int}", loeschen, methods=["DELETE"]),
        Route("/kiste/{id:int}/faecher", faecher),
        Route("/kiste/{id:int}/faecher", faecher_setzen, methods=["PUT"]),
    ]
