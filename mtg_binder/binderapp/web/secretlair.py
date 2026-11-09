"""Secret-Lair-Drops im Add-on (secretlair/drops.py). Lesen frei, Schreiben nur angemeldet. Nur mit `ha=True`.

    GET    /api/secretlair                 alle Drops mit Summen, Standardkosten, Stand des Abgleichs
    GET    /api/secretlair/{id}            ein Drop mit Karten und Verlauf
    POST   /api/secretlair                 {"name", "verkauf"?, "text", "probe"?}  Drop von Hand, mit `probe` nur prüfen
    PATCH  /api/secretlair/{id}            {"name"?, "kosten"?, "kosten_foil"?, "verkauf"?, "wunsch"?}  Kosten: Euro oder
                                           null für den Standard; Verkaufstag JJJJ-MM-TT oder null (wieder automatisch)
    DELETE /api/secretlair/{id}            nur Drops von Hand, nur ohne Käufe
    POST   /api/secretlair/kosten          {"kosten"} oder {"kosten_foil"}  Standardkosten je Drop in Euro, null = keine
    POST   /api/secretlair/schnaeppchen    {"prozent"}  Schwelle für „Schnäppchen“ (Standard 80)
    POST   /api/secretlair/{id}/kauf       {"foil", "anzahl", "preis", "datum"?}  eigener Kauf (Preis je Stück in Euro)
    PATCH  /api/secretlair/kauf/{id}       dieselben Felder, einzeln
    DELETE /api/secretlair/kauf/{id}
                                           Käufe sind Nutzerdaten mit Historie; die Antwort nennt den `schritt`,
                                           Rückgängig über POST /api/schritt/{schritt}/rueckgaengig
    POST   /api/secretlair/uhrzeit         {"uhrzeit": "HH:MM"}  Verkaufsbeginn aller Drops (Standard 17:00)
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
ANZAHL_MAX = 999
PROZENT = (1, 100)
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


def _tag(wert) -> str | None:
    if wert is None:
        return None
    try:
        if isinstance(wert, str) and DATUM.fullmatch(wert):
            date.fromisoformat(wert)
            return wert
    except ValueError:
        pass
    raise Fehler("Der Verkaufstag ist ungültig.")


def _kauf(d: dict, alles: bool) -> dict:
    """Felder eines Kaufs aus der Anfrage; `alles` verlangt Ausführung, Anzahl und Preis."""
    if set(d) - set(drops.KAUF_FELDER) or not d:
        raise Fehler("Erlaubt sind „foil“, „anzahl“, „preis“ und „datum“.")
    if alles and not {"foil", "anzahl", "preis"} <= set(d):
        raise Fehler("Bitte Ausführung, Anzahl und Kaufpreis je Stück angeben.")
    aus = {}
    if "foil" in d:
        if not isinstance(d["foil"], bool):
            raise Fehler("„foil“ muss ja oder nein sein.")
        aus["foil"] = int(d["foil"])
    if "anzahl" in d:
        if isinstance(d["anzahl"], bool) or not isinstance(d["anzahl"], int) or not 1 <= d["anzahl"] <= ANZAHL_MAX:
            raise Fehler(f"Die Anzahl muss zwischen 1 und {ANZAHL_MAX} liegen.")
        aus["anzahl"] = d["anzahl"]
    if "preis" in d:
        if d["preis"] is None:
            raise Fehler("Bitte den Kaufpreis je Stück angeben.")
        aus["preis"] = round(_kosten(d["preis"]), 2)
    if "datum" in d:
        try:
            aus["datum"] = _tag(d["datum"] or None)
        except Fehler:
            raise Fehler("Das Kaufdatum ist ungültig.")
    elif alles:
        aus["datum"] = None
    return aus


def routen(h) -> list[Route]:
    preisdatei = Path(h.datenbank).parent / "preise" / "price_guide_1.json"

    def _id(request) -> int:
        return request.path_params["id"]

    def uebersicht(request):
        with closing(h.db()) as con:
            liste = drops.liste(con)
            return JSONResponse({"drops": liste, "kosten": drops.standard_kosten(con),
                                 "kosten_foil": drops.standard_kosten(con, foil=True),
                                 "schnaeppchen": drops.schwelle(con), "besitz": drops.besitz_gesamt(liste),
                                 "uhrzeit": drops.uhrzeit(con),
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
        verkauf = _tag(d.get("verkauf") or None)
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
        if set(d) - {"name", "kosten", "kosten_foil", "verkauf", "wunsch"} or not d:
            raise Fehler("Erlaubt sind „name“, „kosten“, „kosten_foil“, „verkauf“ und „wunsch“.")
        name = _name(d["name"]) if "name" in d else None
        kosten = _kosten(d.get("kosten"))
        kosten_foil = _kosten(d.get("kosten_foil"))
        verkauf = _tag(d.get("verkauf"))
        if "wunsch" in d and not isinstance(d["wunsch"], bool):
            raise Fehler("„wunsch“ muss ja oder nein sein.")

        def arbeit(con, kat=None):
            if con.execute("SELECT 1 FROM secret_drop WHERE id = ?", (drop,)).fetchone() is None:
                raise Fehler("Diesen Drop gibt es nicht.", 404)
            if name is not None:
                con.execute("UPDATE secret_drop SET name = ?, umbenannt = 1 WHERE id = ?", (name, drop))
            if "kosten" in d:
                con.execute("UPDATE secret_drop SET kosten = ? WHERE id = ?", (kosten, drop))
            if "kosten_foil" in d:
                con.execute("UPDATE secret_drop SET kosten_foil = ? WHERE id = ?", (kosten_foil, drop))
            if "wunsch" in d:
                con.execute("UPDATE secret_drop SET wunsch = ? WHERE id = ?", (int(d["wunsch"]), drop))
            if "verkauf" in d:
                drops.tag_setzen(con, drop, verkauf, kat)
            return {"id": drop}

        def mit_katalog():
            # Nur „wieder automatisch“ braucht den Katalog: Der Tag kommt dann aus den Drucken des Drops
            kat = h.katalog() if "verkauf" in d and verkauf is None else None
            if kat is None:
                return h.schreibvorgang(arbeit)
            with closing(kat):
                return h.schreibvorgang(lambda con: arbeit(con, kat))
        return JSONResponse(await run_in_threadpool(mit_katalog))

    @h.schreibend
    async def loeschen(request):
        drop = _id(request)

        def arbeit(con):
            z = con.execute("SELECT quelle FROM secret_drop WHERE id = ?", (drop,)).fetchone()
            if z is None:
                raise Fehler("Diesen Drop gibt es nicht.", 404)
            if z["quelle"] != "hand":
                raise Fehler("Nur Drops von Hand lassen sich löschen, die anderen kommen beim Abgleich wieder.", 409)
            if con.execute("SELECT 1 FROM secret_kauf WHERE drop_id = ? LIMIT 1", (drop,)).fetchone():
                raise Fehler("Zu diesem Drop gibt es Käufe. Bitte zuerst die Käufe löschen.", 409)
            con.execute("DELETE FROM secret_drop WHERE id = ?", (drop,))
            return {"id": drop}
        return JSONResponse(await run_in_threadpool(h.schreibvorgang, arbeit))

    @h.schreibend
    async def kosten_setzen(request):
        d = await h.json_daten(request)
        if len(d) != 1 or not set(d) <= {"kosten", "kosten_foil"}:
            raise Fehler("Bitte genau „kosten“ oder „kosten_foil“ angeben.")
        name, = d
        kosten = _kosten(d[name])
        await run_in_threadpool(h.schreibvorgang, lambda con: drops.wert_setzen(con, name, kosten))
        return JSONResponse({name: kosten})

    @h.schreibend
    async def schwelle_setzen(request):
        d = await h.json_daten(request)
        prozent = d.get("prozent")
        if isinstance(prozent, bool) or not isinstance(prozent, int) or not PROZENT[0] <= prozent <= PROZENT[1]:
            raise Fehler(f"Die Schwelle muss eine ganze Zahl zwischen {PROZENT[0]} und {PROZENT[1]} Prozent sein.")
        await run_in_threadpool(h.schreibvorgang, lambda con: drops.wert_setzen(con, "schnaeppchen", prozent))
        return JSONResponse({"schnaeppchen": prozent})

    @h.schreibend
    async def kauf_anlegen(request):
        felder = _kauf(await h.json_daten(request), alles=True)
        drop = _id(request)

        def arbeit(con):
            if con.execute("SELECT 1 FROM secret_drop WHERE id = ?", (drop,)).fetchone() is None:
                raise Fehler("Diesen Drop gibt es nicht.", 404)
            kauf, schritt = drops.kauf_anlegen(con, h.nutzer, drop, felder)
            return {"id": kauf, "schritt": schritt}
        return JSONResponse(await run_in_threadpool(h.schreibvorgang, arbeit))

    def _kauf_da(con, kauf: int) -> None:
        if con.execute("SELECT 1 FROM secret_kauf WHERE id = ? AND nutzer_id = ?", (kauf, h.nutzer)).fetchone() is None:
            raise Fehler("Diesen Kauf gibt es nicht.", 404)

    @h.schreibend
    async def kauf_aendern(request):
        felder = _kauf(await h.json_daten(request), alles=False)
        kauf = _id(request)

        def arbeit(con):
            _kauf_da(con, kauf)
            return {"id": kauf, "schritt": drops.kauf_aendern(con, h.nutzer, kauf, felder)}
        return JSONResponse(await run_in_threadpool(h.schreibvorgang, arbeit))

    @h.schreibend
    async def kauf_loeschen(request):
        kauf = _id(request)

        def arbeit(con):
            _kauf_da(con, kauf)
            return {"id": kauf, "schritt": drops.kauf_loeschen(con, h.nutzer, kauf)}
        return JSONResponse(await run_in_threadpool(h.schreibvorgang, arbeit))

    @h.schreibend
    async def uhrzeit_setzen(request):
        d = await h.json_daten(request)
        uhr = d.get("uhrzeit")
        if not isinstance(uhr, str) or not drops.UHR.fullmatch(uhr):
            raise Fehler("Die Uhrzeit braucht die Form HH:MM, zum Beispiel 17:00.")
        await run_in_threadpool(h.schreibvorgang, lambda con: drops.wert_setzen(con, "uhrzeit", uhr))
        return JSONResponse({"uhrzeit": uhr})

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
        Route("/secretlair/uhrzeit", uhrzeit_setzen, methods=["POST"]),
        Route("/secretlair/schnaeppchen", schwelle_setzen, methods=["POST"]),
        Route("/secretlair/kauf/{id:int}", kauf_aendern, methods=["PATCH"]),
        Route("/secretlair/kauf/{id:int}", kauf_loeschen, methods=["DELETE"]),
        Route("/secretlair/{id:int}/kauf", kauf_anlegen, methods=["POST"]),
        Route("/secretlair/aktualisieren", aktualisieren, methods=["POST"]),
        Route("/secretlair/{id:int}", einer),
        Route("/secretlair/{id:int}", aendern, methods=["PATCH"]),
        Route("/secretlair/{id:int}", loeschen, methods=["DELETE"]),
    ]
