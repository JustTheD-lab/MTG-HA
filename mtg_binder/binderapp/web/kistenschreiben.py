"""Schreibende Routen der Kisten (Runde „Kisten fertigstellen“, 28.10.2026), alle nur angemeldet.

    POST /api/kiste/{id}/liste?name=DATEI.csv[&ausfuehren=1]
         Rumpf: die CSV-Datei selbst (höchstens 5 MB). Ohne `ausfuehren` ein Probelauf (schreibt nichts),
         mit `ausfuehren=1` Import in einer Transaktion nach einer Sicherung. Antwort: Bericht
         (listen_import.bericht). Der Dateiname dient nur der Anzeige, nie als Pfad.
    POST /api/kiste/{id}/karte  {"scryfall_id", "anzahl"?, "foil"?, "sprache"?, "zustand"?}
         Karte hinzufügen (nur mit Katalog, sonst 409): Quelle „manuell“, Status „sicher“, geprüft. Gibt es in
         der Kiste schon einen Posten von Hand mit gleichem Druck, Foil, Sprache und Zustand, wächst seine Anzahl.
    POST /api/karten/verschieben  {"ziel": Kisten-ID, "karten": [IDs]} oder {"ziel", "auswahl": {"kiste": ID
         oder null (Sammlung), "abfrage": "Filter wie bei …/karten"}}: alle Karten in die Ziel-Kiste, aus einem
         Binder heraus wird die Tasche „leer“. Die Quelle bleibt. Summenprüfung: Karten über alle Binder und
         Kisten vorher = nachher (sonst Abbruch).
    POST /api/schritt/{kennung}/rueckgaengig   nimmt einen Bedienschritt exakt zurück (db/schritte.py).

Jeder Bedienschritt hat eine Kennung `schritt` (Quelle „nutzer“) und lässt sich als Ganzes zurücknehmen.
Eingebunden über `routen(h)` wie kisten.py und pruefen.py (siehe helfer.py).
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import QueryParams
from starlette.responses import JSONResponse
from starlette.routing import Route

from binderapp.db import datenbank, listen_import, schritte
from binderapp.db.manabox_import import ImportFehler
from binderapp.katalog import kartendaten
from binderapp.preise import zeitplan
from binderapp.web.helfer import Fehler
from binderapp.web.kisten import GROSSE_ID, _ab_version_3, _kiste_holen, _kiste_id, anfrage_lesen, sql_ids
from binderapp.web.pruefen import KEIN_KATALOG, SPRACHEN, _kennung

__all__ = ["routen", "datei_lesen", "dateiname", "summe"]

DATEINAME_MAX = 120
ANZAHL_MAX = 999
AUSWAHL_MAX = 20000      # so viele Karten höchstens in einem Schritt (die ganze Sammlung hat rund 9.600)
ABFRAGE_MAX = 2000       # Länge des Filtertexts
ZUSTAENDE = ("mint", "near_mint", "excellent", "good", "light_played", "played", "poor")  # wie ManaBox


summe = schritte.summe


def druck_nachschlagen(h, scryfall_id: str) -> dict:
    """Kartendaten eines Drucks aus dem Katalog (Karte hinzufügen in Kiste und Binder); ohne Katalog 409."""
    kat = h.katalog()
    if kat is None:
        raise Fehler(KEIN_KATALOG.replace("Druck ändern", "Karte hinzufügen"), 409)
    with closing(kat):
        gefunden = kartendaten.nachschlagen(kat, scryfall_id)
    if gefunden is None:
        raise Fehler("Diesen Druck gibt es im Katalog nicht.", 404)
    return gefunden


def _ganzzahl(wert, name: str, von: int, bis: int) -> int:
    if isinstance(wert, bool) or not isinstance(wert, int) or not von <= wert <= bis:
        raise Fehler(f"„{name}“ muss eine ganze Zahl von {von} bis {bis} sein.")
    return wert


def verschieben(con, nutzer: int, ids: list[int], ziel: int) -> dict:
    """Verschiebt Karten in die Kiste `ziel` als ein Bedienschritt (in der Transaktion des Aufrufers)."""
    _kiste_holen(con, nutzer, ziel)
    vorher = summe(con, nutzer)
    schritt = schritte.neuer_schritt()
    # ab Version 8: eine Karte aus einem Deck verlässt das Deck (ein Ort je Karte), die Herkunft gilt nicht mehr
    deck_felder = ({"deck_id": None, "herkunft_tasche_id": None, "herkunft_kiste_id": None}
                   if datenbank.version(con) >= 8 else {})
    verschoben = karten = schon_dort = 0
    taschen: set[int] = set()
    fehlend = 0
    for kid in ids:
        k = con.execute("SELECT id, kiste_id, tasche_id, anzahl FROM karte WHERE id = ? AND nutzer_id = ?",
                        (kid, nutzer)).fetchone()
        if k is None:
            fehlend += 1
            continue
        if k["kiste_id"] == ziel:
            schon_dort += 1
            continue
        if k["tasche_id"] is not None:
            taschen.add(k["tasche_id"])
        schritte.aendern_genau(con, nutzer, "karte", kid, {"tasche_id": None, "kiste_id": ziel, **deck_felder},
                               schritt=schritt)
        verschoben += 1
        karten += k["anzahl"]
    if fehlend:
        raise Fehler(f"{fehlend} der gewählten Karten gibt es nicht (mehr). Bitte die Ansicht neu laden.", 404)
    leer = 0
    for tid in sorted(taschen):
        if con.execute("SELECT 1 FROM karte WHERE tasche_id = ? LIMIT 1", (tid,)).fetchone() is None:
            leer += bool(schritte.aendern_genau(con, nutzer, "tasche", tid, {"inhalt": "leer"}, schritt=schritt))
    nachher = summe(con, nutzer)
    if nachher != vorher:   # darf nie vorkommen; die Ausnahme rollt alles zurück
        raise Fehler("Summenprüfung fehlgeschlagen, nichts verschoben.", 500)
    return {"schritt": schritt if verschoben else None, "verschoben": verschoben, "karten": karten,
            "schon_dort": schon_dort, "taschen_leer": leer, "summe": {"posten": nachher[0], "karten": nachher[1]}}


async def datei_lesen(request, grenze: int) -> bytes:
    """Rumpf der Anfrage als Bytes, höchstens `grenze` (sonst 413), ohne alles vorher in den Speicher zu laden."""
    laenge = request.headers.get("content-length", "")
    if laenge.isdigit() and int(laenge) > grenze:
        raise Fehler(f"Die Datei ist zu groß (höchstens {grenze // (1024 * 1024)} MB).", 413)
    teile, n = [], 0
    async for stueck in request.stream():
        n += len(stueck)
        if n > grenze:
            raise Fehler(f"Die Datei ist zu groß (höchstens {grenze // (1024 * 1024)} MB).", 413)
        teile.append(stueck)
    if not n:
        raise Fehler("Keine Datei erhalten.")
    return b"".join(teile)


def dateiname(request) -> str:
    """Anzeigename der Datei aus `?name=` (nur Text für Bericht und Protokoll, nie ein Pfad)."""
    name = Path((request.query_params.get("name") or "liste.csv").replace("\\", "/")).name
    name = "".join(z for z in name if z.isprintable())[:DATEINAME_MAX]
    return name or "liste.csv"


def _ja(request, schluessel: str) -> bool:
    return request.query_params.get(schluessel, "") in ("1", "ja", "true")


def routen(h) -> list[Route]:

    @h.schreibend
    async def liste_importieren(request):
        kiste_id = _kiste_id(request)
        ausfuehren = _ja(request, "ausfuehren")
        name = dateiname(request)
        daten = await datei_lesen(request, listen_import.MAX_BYTES)

        def arbeit():
            with closing(h.db()) as con:
                _ab_version_3(con, 409)
                _kiste_holen(con, h.nutzer, kiste_id)
            sicherung = None
            if ausfuehren:
                sicherung = h.sichern()
            kat = h.katalog()
            try:
                with closing(datenbank.verbinden(h.datenbank)) as con:
                    b = listen_import.importieren(con, h.nutzer, daten, name, kiste_id=kiste_id,
                                                  probelauf=not ausfuehren, kat=kat)
            except ImportFehler as e:
                raise Fehler(str(e))
            finally:
                if kat is not None:
                    kat.close()
            b["sicherung"] = sicherung.name if sicherung else None
            return b
        return JSONResponse(await run_in_threadpool(arbeit))

    @h.schreibend
    async def karte_hinzufuegen(request):
        kiste_id = _kiste_id(request)
        d = await h.json_daten(request)
        unbekannt = set(d) - {"scryfall_id", "anzahl", "foil", "sprache", "zustand"}
        if unbekannt:
            raise Fehler("Unbekannte Angabe. Erlaubt: scryfall_id, anzahl, foil, sprache, zustand.")
        scryfall_id = _kennung(d.get("scryfall_id"), "Scryfall-ID")
        anzahl = _ganzzahl(d.get("anzahl", 1), "anzahl", 1, ANZAHL_MAX)
        foil = d.get("foil", False)
        if not isinstance(foil, bool):
            raise Fehler("Foil muss „true“ oder „false“ sein.")
        sprache = d.get("sprache")
        if sprache is not None and (not isinstance(sprache, str) or sprache not in SPRACHEN):
            raise Fehler("Unbekannte Sprache. Möglich: " + ", ".join(SPRACHEN) + ".")
        zustand = d.get("zustand", "near_mint")
        if zustand is not None and zustand not in ZUSTAENDE:
            raise Fehler("Unbekannter Zustand. Möglich: " + ", ".join(ZUSTAENDE) + ".")

        daten = await run_in_threadpool(druck_nachschlagen, h, scryfall_id)

        def arbeit(con):
            _ab_version_3(con, 409)
            _kiste_holen(con, h.nutzer, kiste_id)
            schritt = schritte.neuer_schritt()
            spr = sprache or daten.get("sprache") or "en"
            da = con.execute(
                "SELECT id, anzahl FROM karte WHERE nutzer_id = ? AND kiste_id = ? AND quelle = 'manuell' "
                "AND scryfall_id = ? AND foil = ? AND sprache IS ? AND zustand IS ? ORDER BY id LIMIT 1",
                (h.nutzer, kiste_id, daten["scryfall_id"], int(foil), spr, zustand)).fetchone()
            if da is not None:
                if da["anzahl"] + anzahl > ANZAHL_MAX:
                    raise Fehler(f"Mehr als {ANZAHL_MAX} gleiche Karten in einem Posten gehen nicht.")
                schritte.aendern_genau(con, h.nutzer, "karte", da["id"], {"anzahl": da["anzahl"] + anzahl},
                                       schritt=schritt)
                karte_id, neu = da["id"], False
            else:
                karte_id = schritte.einfuegen(con, h.nutzer, "karte", {
                    "nutzer_id": h.nutzer, "tasche_id": None, "kiste_id": kiste_id, "quelle": "manuell",
                    "scryfall_id": daten["scryfall_id"], "oracle_id": daten["oracle_id"], "name": daten["name"],
                    "set_code": daten["set_code"], "nummer": daten["nummer"], "sprache": spr, "foil": int(foil),
                    "anzahl": anzahl, "status": "sicher", "geprueft": 1, "bild": daten["bild"], "zustand": zustand,
                }, schritt=schritt)
                neu = True
            # Kartendaten sind abgeleitet (Katalog), ohne Historie; vorhandene bleiben unverändert.
            kartendaten.schreiben(con, daten, art="behalten")
            return {"id": karte_id, "neu": neu, "schritt": schritt, "name": daten["name"],
                    "anzahl": anzahl, "kiste_id": kiste_id}
        antwort = await run_in_threadpool(h.schreibvorgang, arbeit)
        zeitplan.anstossen()   # Add-on: Preis des neuen Drucks bald nachtragen
        return JSONResponse(antwort, status_code=201)

    @h.schreibend
    async def karten_verschieben(request):
        d = await h.json_daten(request)
        unbekannt = set(d) - {"ziel", "karten", "auswahl"}
        if unbekannt or ("karten" in d) == ("auswahl" in d):
            raise Fehler("Bitte „ziel“ und entweder „karten“ (Liste von IDs) oder „auswahl“ angeben.")
        ziel = _ganzzahl(d.get("ziel"), "ziel", 1, GROSSE_ID - 1)
        ids: list[int] | None = None
        auswahl = None
        if "karten" in d:
            liste = d["karten"]
            if not isinstance(liste, list) or not liste or len(liste) > AUSWAHL_MAX:
                raise Fehler(f"„karten“ muss eine Liste mit 1 bis {AUSWAHL_MAX} IDs sein.")
            ids = list(dict.fromkeys(_ganzzahl(i, "karten", 1, 2 ** 62) for i in liste))
        else:
            auswahl = d["auswahl"]
            if not isinstance(auswahl, dict) or set(auswahl) - {"kiste", "abfrage"}:
                raise Fehler("„auswahl“ braucht „kiste“ (ID oder null für die Sammlung) und „abfrage“.")
            bereich = auswahl.get("kiste")
            if bereich is not None:
                bereich = _ganzzahl(bereich, "kiste", 1, GROSSE_ID - 1)
            abfrage = auswahl.get("abfrage") or ""
            if not isinstance(abfrage, str) or len(abfrage) > ABFRAGE_MAX:
                raise Fehler("„abfrage“ ist ungültig.")
            # dieselbe Prüfung wie beim Blättern; Sortierung, Seite und Gruppe ändern die Auswahl nicht
            a = anfrage_lesen(QueryParams(abfrage), bereich, h.nutzer)

        def arbeit(con):
            _ab_version_3(con, 409)
            nonlocal ids
            if ids is None:
                if a.kiste is not None:
                    _kiste_holen(con, h.nutzer, a.kiste)
                ids = [z[0] for z in con.execute(sql_ids(a), a.args)]
                if not ids:
                    raise Fehler("Die Auswahl ist leer.")
                if len(ids) > AUSWAHL_MAX:
                    raise Fehler(f"Höchstens {AUSWAHL_MAX} Karten in einem Schritt.")
            return verschieben(con, h.nutzer, ids, ziel)
        return JSONResponse(await run_in_threadpool(h.schreibvorgang, arbeit))

    @h.schreibend
    async def schritt_zuruecknehmen(request):
        alter = schritte.kennung(request.path_params["kennung"])

        def arbeit(con):
            _ab_version_3(con, 409)
            neuer, alte = schritte.zuruecknehmen(con, h.nutzer, alter)
            return {"schritt": neuer, "zurueckgenommen": alter, "zeilen": len(alte)}
        return JSONResponse(await run_in_threadpool(h.schreibvorgang, arbeit))

    return [
        Route("/kiste/{id:int}/liste", liste_importieren, methods=["POST"]),
        Route("/kiste/{id:int}/karte", karte_hinzufuegen, methods=["POST"]),
        Route("/karten/verschieben", karten_verschieben, methods=["POST"]),
        Route("/schritt/{kennung}/rueckgaengig", schritt_zuruecknehmen, methods=["POST"]),
    ]
