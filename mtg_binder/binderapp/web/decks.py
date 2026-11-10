"""Decks: Import aus Archidekt (Link) oder Export-Text, Abgleich, Preise, Fehlt-Liste, Deckbau (ab Version 8).

    GET   /api/decks                              alle Decks mit Summen und Werten (Regalboden „Decks“)
    POST  /api/decks/vorschau  {"archidekt": Link} oder {"text", "name"}   Probelauf, schreibt nichts
    POST  /api/decks           dasselbe, legt das Deck an (201)
    GET   /api/deck/{id}                          Zeilen mit Abgleich und Preisen, Summen, Werte, Fehlt-Liste
    GET   /api/deck/{id}/fehlt                    Fehlt-Liste als Textdatei
    PATCH /api/deck/{id}       {"name"?, "abgleich"?: "name"|"druck", "basics_auslassen"?: bool}
    POST  /api/deck/{id}/zeile/{zeile}/proxy  {"proxy": bool}
    POST  /api/deck/{id}/neu-laden  {"ausfuehren"?: bool, "text"? (nur Text-Decks)}   ohne „ausfuehren“ nur Vorschau
    POST  /api/deck/{id}/loeschen   {"name"}      zweite Bestätigung: Name eintippen; nur ohne Karten im Deck
  Deckbau (decks.bau):
    GET   /api/deck/{id}/entnahme                 Entnahmeliste nach Ort, Haken, offene Karten
    POST  /api/deck/{id}/entnahme/haken  {"zeile", "karte", "anzahl", "abgehakt"}
    GET   /api/deck/{id}/entnahme/alternativen?zeile=   freie Exemplare einer Zeile
    POST  /api/deck/{id}/entnahme/wahl   {"zeile", "karte"}   anderes Exemplar
    POST  /api/deck/{id}/entnahme/fertig          abgehakte Karten ins Deck (ein Schritt, Rückgängig)
    GET   /api/deck/{id}/zerlegen?karten=1,2      wohin jede Karte zurückgeht (ohne „karten“: alle)
    POST  /api/deck/{id}/zerlegen  {"karten"?: [IDs], "ersatz_kiste"?: ID}   zurücklegen (ein Schritt)
    POST  /api/deck/{id}/zur-sammlung             alle Karten der Liste neu im Deck anlegen (ein Schritt)

Schreiben nur angemeldet, mit Historie. Archidekt wird nur auf Wunsch abgefragt (Import, Neu laden).
Rückgängig der Schritte: POST /api/schritt/{schritt}/rueckgaengig (kistenschreiben.py).
"""
from __future__ import annotations

import re
from contextlib import closing

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from binderapp.db.datenbank import aendern, loeschen, version
from binderapp.decks import abgleich, bau, kaufliste, lesen
from binderapp.preise import zeitplan
from binderapp.web.helfer import Fehler, fehler

__all__ = ["routen"]

NAME_LAENGE = 120
GROSS = 2 ** 62
KEIN_KATALOG = "Für Decks braucht die App den Kartenkatalog (die Erkennung am PC schickt ihn beim Start)."


def _ab_version_7(con) -> None:
    """Decks brauchen ab dem Deckbau (03.11.2026) die Datenbankversion 8 (Ort „Deck“)."""
    if version(con) < 8:
        raise Fehler("Decks brauchen die Datenbankversion 8. Bitte die App neu starten.", 409)


def _id(wert, feld: str) -> int:
    if isinstance(wert, bool) or not isinstance(wert, int) or not 1 <= wert < GROSS:
        raise Fehler(f"„{feld}“ muss eine ganze Zahl sein.")
    return wert


def _name(wert) -> str:
    if not isinstance(wert, str) or not wert.strip() or len(wert.strip()) > NAME_LAENGE:
        raise Fehler(f"Bitte einen Namen mit 1 bis {NAME_LAENGE} Zeichen angeben.")
    return wert.strip()


def _bool(wert, feld: str) -> bool:
    if not isinstance(wert, bool):
        raise Fehler(f"„{feld}“ muss „true“ oder „false“ sein.")
    return wert


def lesend(funktion):
    """Lese-Route im Thread; `Fehler` wird zur Antwort mit Text."""
    async def huelle(request):
        try:
            return await run_in_threadpool(funktion, request)
        except Fehler as f:
            return fehler(str(f), f.status)
    huelle.__name__ = funktion.__name__
    return huelle


def routen(h) -> list[Route]:

    def liste_lesen(d: dict, archidekt_id: str | None = None) -> tuple[dict, list, list, list, str]:
        """(Kopf, Zeilen, unbekannte Namen, unlesbare Zeilen, Quelle) – im Thread (Netz und Katalog)."""
        try:
            if archidekt_id is not None or "archidekt" in d:
                kennung = archidekt_id or lesen.archidekt_id(d.get("archidekt"))
                kopf, zeilen = lesen.archidekt_zeilen(lesen.archidekt_holen(kennung))
                kopf["extern_id"] = kennung   # die angefragte ID, nicht die aus der Antwort
                unlesbar, quelle = [], "archidekt"
            elif "text" in d:
                zeilen, unlesbar = lesen.text_zeilen(d.get("text"))
                kopf, quelle = {"name": _name(d["name"]) if "name" in d else "Deck"}, "text"
            else:
                raise Fehler("Bitte „archidekt“ (Link) oder „text“ angeben.")
        except lesen.DeckFehler as f:
            raise Fehler(str(f), f.status)
        kat = h.katalog()
        if kat is None:
            raise Fehler(KEIN_KATALOG, 409)
        with closing(kat):
            zeilen, unbekannt = lesen.aufloesen(kat, zeilen)
        if not zeilen:
            raise Fehler("In der Liste wurden keine Karten erkannt.")
        return kopf, zeilen, unbekannt, unlesbar, quelle

    def _pruefen(d: dict, erlaubt: set) -> None:
        if not isinstance(d, dict) or set(d) - erlaubt:
            raise Fehler("Unbekannte Angabe. Erlaubt: " + ", ".join(sorted(erlaubt)) + ".")

    def _bericht(kopf, zeilen, unbekannt, unlesbar, quelle) -> dict:
        return {"name": kopf["name"], "quelle": quelle, "zeilen": len(zeilen),
                "karten": sum(z["anzahl"] for z in zeilen),
                "commander": [z["name"] for z in zeilen if z["commander"]],
                "basics": sum(z["anzahl"] for z in zeilen if z.get("basic")),
                "unbekannt": unbekannt[:50], "unlesbar": unlesbar[:50]}

    @lesend
    def decks(request):
        with closing(h.db()) as con:
            if version(con) < 8:
                return JSONResponse({"verfuegbar": False, "decks": []})
            return JSONResponse({"verfuegbar": True, "decks": abgleich.uebersicht(con, h.nutzer)})

    @h.schreibend
    async def vorschau(request):
        d = await h.json_daten(request)
        _pruefen(d, {"archidekt", "text", "name"})
        return JSONResponse(_bericht(*await run_in_threadpool(liste_lesen, d)))

    @h.schreibend
    async def anlegen(request):
        d = await h.json_daten(request)
        _pruefen(d, {"archidekt", "text", "name"})
        if "text" in d and "name" not in d:
            raise Fehler("Bitte dem Deck einen Namen geben.")
        kopf, zeilen, unbekannt, unlesbar, quelle = await run_in_threadpool(liste_lesen, d)

        def arbeit(con):
            _ab_version_7(con)
            return abgleich.anlegen(con, h.nutzer, kopf, zeilen, quelle)
        deck_id = await run_in_threadpool(h.schreibvorgang, arbeit)
        zeitplan.anstossen()   # Add-on: Preise der neuen Produkte bald nachtragen
        return JSONResponse({"id": deck_id, **_bericht(kopf, zeilen, unbekannt, unlesbar, quelle)}, status_code=201)

    def _deck_id(request) -> int:
        return int(request.path_params["id"])

    def _lesen(con, deck_id: int):
        _ab_version_7(con)
        try:
            return abgleich.deck_holen(con, h.nutzer, deck_id)
        except LookupError as f:
            raise Fehler(str(f), 404)

    @lesend
    def deck(request):
        with closing(h.db()) as con:
            a = abgleich.abgleichen(con, h.nutzer, _lesen(con, _deck_id(request)))
            # Paket 7: Schalter „in Kaufliste“ (ab Version 23; vorher None, die Oberfläche blendet ihn aus)
            a["kaufliste"] = (a["id"] in kaufliste.decks_in_liste(con, h.nutzer)
                              if version(con) >= kaufliste.AB_VERSION else None)
        ordner = h.bilder / "scryfall"   # ohne Vorschaubild lädt die Oberfläche gleich das Bild von Scryfall
        for z in a["karten"]:
            z["vorschau"] = bool(z["scryfall_id"]) and (ordner / f"{z['scryfall_id']}_200.webp").is_file()
        return JSONResponse({**a, "fehlt_text": abgleich.fehlt_text(a)})

    @lesend
    def fehlt(request):
        with closing(h.db()) as con:
            d = _lesen(con, _deck_id(request))
            text = abgleich.fehlt_text(abgleich.abgleichen(con, h.nutzer, d))
        return PlainTextResponse(text, headers={"Content-Disposition": 'attachment; filename="fehlt.txt"'})

    @h.schreibend
    async def aendern_route(request):
        deck_id = _deck_id(request)
        d = await h.json_daten(request)
        _pruefen(d, {"name", "abgleich", "basics_auslassen"})
        felder = {}
        if "name" in d:
            felder["name"] = _name(d["name"])
        if "abgleich" in d:
            if d["abgleich"] not in ("name", "druck"):
                raise Fehler("„abgleich“ ist „name“ oder „druck“.")
            felder["abgleich"] = d["abgleich"]
        if "basics_auslassen" in d:
            felder["basics_auslassen"] = int(_bool(d["basics_auslassen"], "basics_auslassen"))

        def arbeit(con):
            _lesen(con, deck_id)
            aendern(con, h.nutzer, abgleich.QUELLE, "deck", deck_id, felder)
            return {"id": deck_id, **felder}
        return JSONResponse(await run_in_threadpool(h.schreibvorgang, arbeit))

    @h.schreibend
    async def proxy(request):
        deck_id, zeile_id = _deck_id(request), int(request.path_params["zeile"])
        d = await h.json_daten(request)
        _pruefen(d, {"proxy"})
        wert = _bool(d.get("proxy"), "proxy")

        def arbeit(con):
            _lesen(con, deck_id)
            if con.execute("SELECT 1 FROM deck_karte WHERE id = ? AND deck_id = ?", (zeile_id, deck_id)).fetchone() is None:
                raise Fehler("Diese Karte gehört nicht zum Deck.", 404)
            aendern(con, h.nutzer, abgleich.QUELLE, "deck_karte", zeile_id, {"proxy": int(wert)})
            return {"id": zeile_id, "proxy": wert}
        return JSONResponse(await run_in_threadpool(h.schreibvorgang, arbeit))

    @h.schreibend
    async def neu_laden(request):
        deck_id = _deck_id(request)
        d = await h.json_daten(request)
        _pruefen(d, {"ausfuehren", "text"})
        ausfuehren = _bool(d.get("ausfuehren", False), "ausfuehren")
        with closing(h.db()) as con:
            alt = _lesen(con, deck_id)
        if alt["quelle"] == "archidekt":
            if "text" in d:
                raise Fehler("Dieses Deck kommt von Archidekt und wird von dort neu geladen.")
            kopf, zeilen, unbekannt, unlesbar, _ = await run_in_threadpool(liste_lesen, {}, alt["extern_id"])
        else:
            if "text" not in d:
                raise Fehler("Bitte die neue Deckliste als „text“ einfügen.")
            kopf, zeilen, unbekannt, unlesbar, _ = await run_in_threadpool(liste_lesen, {"text": d["text"]})

        def arbeit(con):
            _lesen(con, deck_id)
            if ausfuehren:
                return abgleich.ersetzen(con, h.nutzer, deck_id, kopf, zeilen)
            return abgleich.unterschiede(con, deck_id, zeilen)
        if ausfuehren:
            ergebnis = await run_in_threadpool(h.schreibvorgang, arbeit)
            zeitplan.anstossen()
        else:
            with closing(h.db()) as con:
                ergebnis = arbeit(con)
        return JSONResponse({**ergebnis, "ausgefuehrt": ausfuehren, "unbekannt": unbekannt[:50],
                             "unlesbar": unlesbar[:50]})

    @h.schreibend
    async def loeschen_route(request):
        deck_id = _deck_id(request)
        d = await h.json_daten(request)
        _pruefen(d, {"name"})

        def arbeit(con):
            deck_ = _lesen(con, deck_id)
            if not isinstance(d.get("name"), str) or d["name"].strip() != deck_["name"]:
                raise Fehler("Der eingetippte Name passt nicht zum Deck.")
            if con.execute("SELECT 1 FROM karte WHERE deck_id = ? LIMIT 1", (deck_id,)).fetchone() is not None:
                raise Fehler("Im Deck liegen noch Karten. Bitte das Deck zuerst zerlegen.", 409)
            for z in con.execute("SELECT id FROM deck_karte WHERE deck_id = ?", (deck_id,)).fetchall():
                loeschen(con, h.nutzer, abgleich.QUELLE, "deck_karte", z["id"])
            loeschen(con, h.nutzer, abgleich.QUELLE, "deck", deck_id)
            return {"geloescht": deck_id}
        return JSONResponse(await run_in_threadpool(h.schreibvorgang, arbeit))

    # ------------------------------------------------------------ Deckbau

    def _schreiben(arbeit):
        def mit_pruefung(con):
            _ab_version_7(con)
            return arbeit(con)
        return run_in_threadpool(h.schreibvorgang, mit_pruefung)

    def _bild_hinweis(eintraege) -> None:
        ordner = h.bilder / "scryfall"
        for e in eintraege:
            e["vorschau"] = bool(e.get("scryfall_id")) and (ordner / f"{e['scryfall_id']}_200.webp").is_file()

    @lesend
    def entnahme(request):
        with closing(h.db()) as con:
            _ab_version_7(con)
            p = bau.plan(con, h.nutzer, _deck_id(request))
        for g in p["gruppen"]:
            _bild_hinweis(g["eintraege"])
        return JSONResponse(p)

    @h.schreibend
    async def entnahme_haken(request):
        deck_id = _deck_id(request)
        d = await h.json_daten(request)
        _pruefen(d, {"zeile", "karte", "anzahl", "abgehakt"})
        zeile, karte, anzahl = _id(d.get("zeile"), "zeile"), _id(d.get("karte"), "karte"), _id(d.get("anzahl"), "anzahl")
        abgehakt = _bool(d.get("abgehakt"), "abgehakt")
        return JSONResponse(await _schreiben(lambda con: bau.haken(con, h.nutzer, deck_id, zeile, karte, anzahl,
                                                                   abgehakt)))

    @lesend
    def entnahme_alternativen(request):
        roh = request.query_params.get("zeile", "")
        if not re.fullmatch(r"[0-9]{1,12}", roh):
            raise Fehler("Bitte „zeile“ angeben.")
        with closing(h.db()) as con:
            _ab_version_7(con)
            liste = bau.alternativen(con, h.nutzer, _deck_id(request), int(roh))
        _bild_hinweis(liste)
        return JSONResponse({"alternativen": liste})

    @h.schreibend
    async def entnahme_wahl(request):
        deck_id = _deck_id(request)
        d = await h.json_daten(request)
        _pruefen(d, {"zeile", "karte"})
        zeile, karte = _id(d.get("zeile"), "zeile"), _id(d.get("karte"), "karte")
        return JSONResponse(await _schreiben(lambda con: bau.waehlen(con, h.nutzer, deck_id, zeile, karte)))

    @h.schreibend
    async def entnahme_fertig(request):
        deck_id = _deck_id(request)
        return JSONResponse(await _schreiben(lambda con: bau.fertig(con, h.nutzer, deck_id)))

    def _karten_ids(roh) -> list[int] | None:
        if roh is None:
            return None
        if not isinstance(roh, list) or not roh or len(roh) > 2000:
            raise Fehler("„karten“ muss eine Liste von Karten-IDs sein.")
        return [_id(i, "karten") for i in roh]

    @lesend
    def zerlegen_plan(request):
        roh = request.query_params.get("karten")
        ids = None
        if roh:
            if len(roh) > 20000 or not re.fullmatch(r"[0-9]{1,12}(,[0-9]{1,12})*", roh):
                raise Fehler("„karten“: Karten-IDs mit Komma getrennt.")
            ids = [int(t) for t in roh.split(",")]
        with closing(h.db()) as con:
            _ab_version_7(con)
            p = bau.zerlegen_plan(con, h.nutzer, _deck_id(request), ids)
        _bild_hinweis(p["karten"])
        return JSONResponse(p)

    @h.schreibend
    async def zerlegen(request):
        deck_id = _deck_id(request)
        d = await h.json_daten(request)
        _pruefen(d, {"karten", "ersatz_kiste"})
        ids = _karten_ids(d.get("karten"))
        ersatz = _id(d["ersatz_kiste"], "ersatz_kiste") if d.get("ersatz_kiste") is not None else None
        return JSONResponse(await _schreiben(lambda con: bau.zerlegen(con, h.nutzer, deck_id, ids, ersatz)))

    @h.schreibend
    async def zur_sammlung(request):
        deck_id = _deck_id(request)

        def arbeit(con):
            kat = h.katalog()
            try:
                return bau.zur_sammlung(con, kat, h.nutzer, deck_id)
            finally:
                if kat is not None:
                    kat.close()
        antwort = await _schreiben(arbeit)
        zeitplan.anstossen()   # Add-on: Preise der neuen Karten bald nachtragen
        return JSONResponse(antwort, status_code=201)

    return [
        Route("/deck/{id:int}/entnahme", entnahme),
        Route("/deck/{id:int}/entnahme/haken", entnahme_haken, methods=["POST"]),
        Route("/deck/{id:int}/entnahme/alternativen", entnahme_alternativen),
        Route("/deck/{id:int}/entnahme/wahl", entnahme_wahl, methods=["POST"]),
        Route("/deck/{id:int}/entnahme/fertig", entnahme_fertig, methods=["POST"]),
        Route("/deck/{id:int}/zerlegen", zerlegen_plan),
        Route("/deck/{id:int}/zerlegen", zerlegen, methods=["POST"]),
        Route("/deck/{id:int}/zur-sammlung", zur_sammlung, methods=["POST"]),
        Route("/decks", decks),
        Route("/decks", anlegen, methods=["POST"]),
        Route("/decks/vorschau", vorschau, methods=["POST"]),
        Route("/deck/{id:int}", deck),
        Route("/deck/{id:int}", aendern_route, methods=["PATCH"]),
        Route("/deck/{id:int}/fehlt", fehlt),
        Route("/deck/{id:int}/zeile/{zeile:int}/proxy", proxy, methods=["POST"]),
        Route("/deck/{id:int}/neu-laden", neu_laden, methods=["POST"]),
        Route("/deck/{id:int}/loeschen", loeschen_route, methods=["POST"]),
    ]
