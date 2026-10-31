"""Prüfansicht (Paket F): unsichere Erkennungen bestätigen und korrigieren, alles mit Historie und Rückgängig.

Routen für `server.py` (`routen(h)` liefert Starlette-Routen, die unter /api eingehängt werden; der
Helfer `h` kommt aus `binderapp.web.helfer`). Läuft auch auf dem Raspberry Pi: nur lesende SQL und
SQLite; nur „Druck ändern“ und die Namenssuche brauchen den Katalog (`h.katalog()`, nur am PC).

Lesen (offen wie die übrigen Leseroutten, nur für den Nutzer `h.nutzer`):
  GET    /api/pruefen/warteschlange        {"faelle": […], "gesamt": n, "katalog": bool}
  GET    /api/pruefen/karte/{id}           ein Fall, ausgehend von einer Karte
  GET    /api/pruefen/tasche/{id}          ein Fall, ausgehend von einer Tasche
  GET    /api/pruefen/namen?q=TEXT         Namenssuche im Katalog (ab 2 Zeichen) oder {"verfuegbar": false}
  GET    /api/pruefen/drucke?oracle_id=ID  Drucke einer Karte (mit „vorschau“) oder {"verfuegbar": false}
  GET    /api/pruefen/bild/{scryfall_id}   lokales Scryfall-Bild unverändert (nur mit Katalog am PC)

Schreiben (nur angemeldet, über `h.schreibend`); jeder Bedienschritt trägt eine neue Kennung
`schritt` in allen seinen Historienzeilen (Quelle „nutzer“); die Antwort ist der aktualisierte Fall
oder {"entfernt": true, …}, jeweils mit `schritt` (null, wenn nichts zu ändern war):
  POST   /api/karte/{id}/bestaetigen       geprueft = 1
  POST   /api/karte/{id}/druck             {scryfall_id, sprache?, foil?}  (nur mit Katalog, sonst 409)
  PATCH  /api/karte/{id}                   {anzahl?, foil?, sprache?}
  DELETE /api/karte/{id}                   Karte entfernen (die Tasche wird „leer“)
  POST   /api/tasche/{id}/inhalt           {inhalt: "leer"|"rueckseite"}  („Keine Karte“)
  POST   /api/karte/{id}/rueckgaengig      nimmt den neuesten Nutzer-Schritt zurück, der die Karte betrifft
  POST   /api/tasche/{id}/rueckgaengig     dasselbe für eine Tasche (und die Karten darin)

Ein Fall ist ein Wörterbuch mit `art` (karte|tasche), `karte_id`, `tasche_id`, `ort`, `status`,
`karte` (Felder der Karte oder null), `tasche` (id, inhalt, zeile, spalte oder null), `ausschnitt`
(eigenes Foto), `bild` (Scryfall-Bildschlüssel), `gruende` (Rohtext der Erkennung), `kartendaten`
(Zeile aus der Tabelle gleichen Namens oder null), `rueckgaengig` (Text des letzten zurücknehmbaren
Schritts oder null) und `katalog` (Druckwahl möglich).

Rückgängig: Es zählen nur Schritte mit Quelle „nutzer“ und Kennung (also nie Erkennung, Import oder
Prüfliste). Ein Rückgängig ist selbst kein zurücknehmbarer Schritt; mehrfaches Rückgängig geht
Schritt für Schritt weiter zurück. Hat sich ein betroffenes Feld seitdem anders geändert, scheitert
der ganze Vorgang mit 409, ohne Teiländerung (eine Transaktion). Die Zeit `geaendert` einer Karte
wird dabei neu gesetzt; sie gehört nicht zum wiederhergestellten Zustand.

Alle Werte laufen als SQL-Parameter; erlaubte Werte stehen in festen Listen; Eingaben prüfen ergibt
400 mit einem deutschen Satz, fremde oder fehlende Datensätze 404.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from binderapp import katalog as katalog_paket
from binderapp.db.datenbank import aendern, einfuegen, loeschen, version
from binderapp.katalog import kartendaten, suche
from binderapp.web.helfer import Fehler, fehler

QUELLE = "nutzer"
MIN_VERSION = 3          # Schema mit historie.schritt und kartendaten
SPRACHEN = ("en", "de", "fr", "it", "es", "pt", "ja", "ko", "ru", "zhs", "zht", "la", "he", "ar", "grc", "ph")
INHALTE = ("leer", "rueckseite")
ANZAHL_MAX = 999
WARTESCHLANGE_MAX = 2000
NAMEN_MAX = 20
SUCHTEXT_MAX = 80
GROESSTE_ID = 2 ** 62     # größere Nummern gibt es nicht (SQLite-Ganzzahl), also 404 statt Überlauf
KONFLIKT = "Die Karte wurde inzwischen geändert."
KEIN_KATALOG = "Druck ändern geht nur am PC mit Katalog."
UUID_MUSTER = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
FELDNAMEN = {"anzahl": "Anzahl", "foil": "Foil", "sprache": "Sprache"}

# Fall ausgehend von einer Karte: Lage (Tasche, Seite, Binder oder Kiste) in einer Abfrage
SQL_KARTE = """
SELECT k.*, t.inhalt, t.zeile, t.spalte, t.ausschnitt, s.id AS seite_id, s.nummer AS seite,
       b.id AS binder_id, b.name AS binder_name, ki.name AS kiste_name
FROM karte k
LEFT JOIN tasche t ON t.id = k.tasche_id
LEFT JOIN seite s ON s.id = t.seite_id
LEFT JOIN binder b ON b.id = s.binder_id
LEFT JOIN kiste ki ON ki.id = k.kiste_id
WHERE k.id = ? AND k.nutzer_id = ?
"""

SQL_TASCHE = """
SELECT t.id AS tasche_id, t.inhalt, t.zeile, t.spalte, t.ausschnitt, s.id AS seite_id, s.nummer AS seite,
       b.id AS binder_id, b.name AS binder_name, NULL AS kiste_id, NULL AS kiste_name
FROM tasche t
JOIN seite s ON s.id = t.seite_id
JOIN binder b ON b.id = s.binder_id
WHERE t.id = ? AND b.nutzer_id = ?
"""

# Warteschlange: Binder nach Regalplatz, Seite, Zeile, Spalte; danach Kisten (Regalplatz, dann nach Name);
# zuletzt lose Karten. Unsichere Taschen ohne Karte stehen an ihrem Platz im Binder.
SQL_WARTESCHLANGE = """
SELECT * FROM (
    SELECT 'karte' AS art, k.id AS karte_id, t.id AS tasche_id, k.status AS status, k.name AS name,
           t.ausschnitt AS ausschnitt, b.id AS binder_id, b.name AS binder_name, b.regalplatz AS binder_platz,
           s.nummer AS seite, t.zeile AS zeile, t.spalte AS spalte,
           ki.id AS kiste_id, ki.name AS kiste_name, ki.regalplatz AS kiste_platz
    FROM karte k
    LEFT JOIN tasche t ON t.id = k.tasche_id
    LEFT JOIN seite s ON s.id = t.seite_id
    LEFT JOIN binder b ON b.id = s.binder_id
    LEFT JOIN kiste ki ON ki.id = k.kiste_id
    WHERE k.nutzer_id = :n AND k.geprueft = 0 AND k.status IN ('unsicher', 'name_sicher')
    UNION ALL
    SELECT 'tasche', NULL, t.id, 'unsicher', NULL, t.ausschnitt, b.id, b.name, b.regalplatz,
           s.nummer, t.zeile, t.spalte, NULL, NULL, NULL
    FROM tasche t
    JOIN seite s ON s.id = t.seite_id
    JOIN binder b ON b.id = s.binder_id
    WHERE b.nutzer_id = :n AND t.inhalt = 'unsicher'
      AND NOT EXISTS (SELECT 1 FROM karte k WHERE k.tasche_id = t.id)
)
ORDER BY CASE WHEN binder_id IS NOT NULL THEN 0 WHEN kiste_id IS NOT NULL THEN 1 ELSE 2 END,
         binder_platz, binder_id, seite, zeile, spalte, kiste_platz, kiste_id, name COLLATE NOCASE, karte_id
"""

# Neuester noch nicht zurückgenommener Nutzer-Schritt, der ein Ziel betrifft. Der Teil {bedingung} ist
# ein fester Text aus diesem Modul (nie Eingabe); alle Werte sind Parameter.
SQL_LETZTER_SCHRITT = """
SELECT h.schritt FROM historie h
WHERE h.nutzer_id = :n AND h.quelle = 'nutzer' AND h.schritt IS NOT NULL AND h.rueckgaengig_von IS NULL
  AND ({bedingung})
  AND h.schritt NOT IN (SELECT rueckgaengig_von FROM historie WHERE rueckgaengig_von IS NOT NULL)
  AND h.schritt NOT IN (SELECT schritt FROM historie WHERE tabelle = 'abzug' AND schritt IS NOT NULL)
GROUP BY h.schritt
ORDER BY MAX(h.id) DESC
LIMIT 1
"""
# Karten-IDs können nach dem Entfernen der höchsten ID neu vergeben werden (kein AUTOINCREMENT): Es zählt
# nur die Historie ab der jüngsten Anlage, die kein Rückgängig war (Befund des Prüfers vom 28.10.2026).
BEDINGUNG_KARTE = (
    "h.tabelle = 'karte' AND h.datensatz = :ziel AND h.id >= COALESCE((SELECT MAX(g.id) FROM historie g"
    " WHERE g.tabelle = 'karte' AND g.datensatz = :ziel AND g.aktion = 'angelegt'"
    " AND g.rueckgaengig_von IS NULL), 0)"
)
BEDINGUNG_TASCHE = (
    "(h.tabelle = 'tasche' AND h.datensatz = :ziel) OR (h.tabelle = 'karte' AND ("
    "h.datensatz IN (SELECT id FROM karte WHERE tasche_id = :ziel) "
    "OR json_extract(h.alt, '$.tasche_id') = :ziel OR json_extract(h.neu, '$.tasche_id') = :ziel))"
)


# ------------------------------------------------------------------ kleine Helfer


def _neuer_schritt() -> str:
    return uuid.uuid4().hex


def _version_pruefen(con) -> None:
    if version(con) < MIN_VERSION:
        raise Fehler("Die Datenbank muss für die Prüfansicht zuerst auf Version 3 gebracht werden "
                     "(python -m binderapp.db.migrieren).", 409)


def _id(request, wort: str) -> int:
    nummer = int(request.path_params["id"])
    if nummer > GROESSTE_ID:
        raise Fehler(f"{wort} nicht gefunden.", 404)
    return nummer


def _kennung(wert, was: str) -> str:
    """Scryfall-ID oder Oracle-ID: nur die kleingeschriebene UUID-Form (kommt nie als Pfad in Dateizugriffe)."""
    text = wert.strip().lower() if isinstance(wert, str) else ""
    if not UUID_MUSTER.match(text):
        raise Fehler(f"Ungültige {was}.")
    return text


def _markierung(status) -> str | None:
    return {"unsicher": "unsicher", "name_sicher": "druck_offen"}.get(status)


def _json(text):
    return json.loads(text) if text else None


def _ort(z) -> dict:
    if z["binder_id"] is not None:
        return {"art": "binder", "binder_id": z["binder_id"], "binder_name": z["binder_name"],
                "seite": z["seite"], "zeile": z["zeile"], "spalte": z["spalte"]}
    if z["kiste_id"] is not None:
        return {"art": "kiste", "kiste_id": z["kiste_id"], "kiste_name": z["kiste_name"]}
    return {"art": "lose"}


def _karte_dict(k) -> dict:
    return {
        "id": k["id"], "name": k["name"], "set": k["set_code"], "nummer": k["nummer"], "sprache": k["sprache"],
        "foil": bool(k["foil"]), "anzahl": k["anzahl"], "status": k["status"], "konfidenz": k["konfidenz"],
        "geprueft": bool(k["geprueft"]), "scryfall_id": k["scryfall_id"], "oracle_id": k["oracle_id"],
        "bild": k["bild"], "quelle": k["quelle"], "markierung": _markierung(k["status"]),
    }


def _karte_holen(con, nutzer: int, karte_id: int):
    k = con.execute("SELECT * FROM karte WHERE id = ? AND nutzer_id = ?", (karte_id, nutzer)).fetchone()
    if k is None:
        raise Fehler("Karte nicht gefunden.", 404)
    return k


def _tasche_holen(con, nutzer: int, tasche_id: int):
    t = con.execute(SQL_TASCHE, (tasche_id, nutzer)).fetchone()
    if t is None:
        raise Fehler("Tasche nicht gefunden.", 404)
    return t


def _spalten(con, tabelle: str) -> set[str]:
    return {z[1] for z in con.execute(f"PRAGMA table_info({tabelle})")}


# ------------------------------------------------------------------ Rückgängig: Schritte lesen


def _schritt_zeilen(con, nutzer: int, schritt: str) -> list:
    return con.execute("SELECT * FROM historie WHERE schritt = ? AND nutzer_id = ? AND quelle = ? ORDER BY id",
                       (schritt, nutzer, QUELLE)).fetchall()


def _schritt_text(zeilen) -> str:
    """Ein Satz für den Schritt, aus seinen Historienzeilen (für die Rückgängig-Taste)."""
    karten = [(z, _json(z["alt"]) or {}, _json(z["neu"]) or {}) for z in zeilen if z["tabelle"] == "karte"]
    taschen = [(z, _json(z["alt"]) or {}, _json(z["neu"]) or {}) for z in zeilen if z["tabelle"] == "tasche"]
    inhalt = next((n["inhalt"] for _, _, n in taschen if "inhalt" in n), None)
    weg = [a for z, a, _ in karten if z["aktion"] == "geloescht"]
    if weg:
        name = weg[0].get("name") or "Karte"
        if inhalt == "rueckseite":
            return f"Tasche als Rückseite markiert („{name}“ entfernt)"
        if inhalt == "leer":
            return f"„{name}“ entfernt, Tasche leer"
        return f"„{name}“ entfernt"
    if inhalt is not None:
        return {"leer": "Tasche als leer markiert", "rueckseite": "Tasche als Rückseite markiert"}.get(
            inhalt, "Tasche geändert")
    for z, _, n in karten:
        if z["aktion"] == "geaendert":
            if "scryfall_id" in n or "oracle_id" in n:
                return "Druck geändert"
            felder = [FELDNAMEN[f] for f in FELDNAMEN if f in n]
            if felder:
                return ", ".join(felder) + " geändert"
            if "geprueft" in n:
                return "Als geprüft bestätigt"
    return "Letzte Änderung"


def _letzter_schritt(con, nutzer: int, karte_id: int | None = None, tasche_id: int | None = None):
    """(Kennung, Zeilen, Text) des neuesten zurücknehmbaren Nutzer-Schritts oder None."""
    if karte_id is not None:
        bedingung, ziel = BEDINGUNG_KARTE, karte_id
    else:
        bedingung, ziel = BEDINGUNG_TASCHE, tasche_id
    z = con.execute(SQL_LETZTER_SCHRITT.format(bedingung=bedingung), {"n": nutzer, "ziel": ziel}).fetchone()
    if z is None:
        return None
    zeilen = _schritt_zeilen(con, nutzer, z["schritt"])
    return z["schritt"], zeilen, _schritt_text(zeilen)


# ------------------------------------------------------------------ Fall


def fall_holen(con, nutzer: int, *, karte_id: int | None = None, tasche_id: int | None = None,
               katalog: bool = False) -> dict | None:
    """Alles für die Prüfkarte oder None, wenn Karte oder Tasche fehlt oder einem anderen Nutzer gehört."""
    if karte_id is not None:
        z = con.execute(SQL_KARTE, (karte_id, nutzer)).fetchone()
        if z is None:
            return None
        karte, art, tid = z, "karte", z["tasche_id"]
        tasche = None if tid is None else {"id": tid, "inhalt": z["inhalt"], "zeile": z["zeile"], "spalte": z["spalte"]}
        letzter = _letzter_schritt(con, nutzer, karte_id=karte_id)
        status = z["status"]
    else:
        z = con.execute(SQL_TASCHE, (tasche_id, nutzer)).fetchone()
        if z is None:
            return None
        karte = con.execute("SELECT * FROM karte WHERE tasche_id = ? AND nutzer_id = ? ORDER BY id LIMIT 1",
                            (tasche_id, nutzer)).fetchone()
        art, tid = "tasche", tasche_id
        tasche = {"id": tid, "inhalt": z["inhalt"], "zeile": z["zeile"], "spalte": z["spalte"]}
        letzter = _letzter_schritt(con, nutzer, tasche_id=tasche_id)
        status = karte["status"] if karte is not None else z["inhalt"]
    daten = None
    if karte is not None and karte["scryfall_id"]:
        zeile = con.execute("SELECT * FROM kartendaten WHERE scryfall_id = ?", (karte["scryfall_id"],)).fetchone()
        if zeile is not None:
            daten = {s: zeile[s] for s in zeile.keys() if s != "geaendert"}
    return {
        "art": art,
        "karte_id": karte["id"] if karte is not None else None,
        "tasche_id": tid,
        "ort": _ort(z),
        "status": status,
        "karte": _karte_dict(karte) if karte is not None else None,
        "tasche": tasche,
        "ausschnitt": z["ausschnitt"],
        "bild": karte["bild"] if karte is not None else None,
        "gruende": karte["gruende"] if karte is not None else None,
        "kartendaten": daten,
        "rueckgaengig": letzter[2] if letzter else None,
        "katalog": katalog,
    }


def _katalog_da(h) -> bool:
    kat = h.katalog()
    if kat is None:
        return False
    kat.close()
    return True


def _mit_schritt(con, antwort: dict, schritt: str) -> dict:
    """Trägt die Kennung ein, wenn der Schritt Historie geschrieben hat, sonst null (nichts geändert)."""
    n = con.execute("SELECT COUNT(*) FROM historie WHERE schritt = ?", (schritt,)).fetchone()[0]
    antwort["schritt"] = schritt if n else None
    return antwort


def _entfernt(con, nutzer: int, karte_id: int, tasche_id: int | None, schritt: str) -> dict:
    letzter = _letzter_schritt(con, nutzer, karte_id=karte_id)
    antwort = {"entfernt": True, "karte_id": karte_id, "tasche_id": tasche_id,
               "rueckgaengig": letzter[2] if letzter else None}
    return _mit_schritt(con, antwort, schritt)


# ------------------------------------------------------------------ Rückgängig: umkehren


def _zuruecknehmen(con, nutzer: int, alter_schritt: str, zeilen) -> str:
    """Kehrt die Zeilen eines Schritts in umgekehrter Reihenfolge um; gibt die neue Kennung zurück.

    Läuft in der Transaktion des Aufrufers. Jede Abweichung vom erwarteten Zustand ist ein Konflikt
    (409); die Ausnahme macht die Transaktion rückgängig, es bleibt keine Teiländerung.
    """
    neuer = _neuer_schritt()
    for z in reversed(zeilen):
        tabelle = z["tabelle"]
        if tabelle not in ("karte", "tasche"):
            raise Fehler(KONFLIKT, 409)
        spalten = _spalten(con, tabelle)
        alt, neu = _json(z["alt"]), _json(z["neu"])
        aktuell = con.execute(f"SELECT * FROM {tabelle} WHERE id = ?", (z["datensatz"],)).fetchone()
        kennung = dict(schritt=neuer, rueckgaengig_von=alter_schritt)
        if z["aktion"] == "geaendert":
            if aktuell is None or not alt or not neu or not set(alt) <= spalten or not set(neu) <= spalten:
                raise Fehler(KONFLIKT, 409)
            if any(aktuell[k] != v for k, v in neu.items()):
                raise Fehler(KONFLIKT, 409)
            aendern(con, nutzer, QUELLE, tabelle, z["datensatz"], alt, **kennung)
        elif z["aktion"] == "angelegt":
            if aktuell is None or not neu or not set(neu) <= spalten:
                raise Fehler(KONFLIKT, 409)
            if any(aktuell[k] != v for k, v in neu.items()):
                raise Fehler(KONFLIKT, 409)
            loeschen(con, nutzer, QUELLE, tabelle, z["datensatz"], **kennung)
        elif z["aktion"] == "geloescht":
            if aktuell is not None or not alt or not set(alt) <= spalten:
                raise Fehler(KONFLIKT, 409)
            try:
                einfuegen(con, nutzer, QUELLE, tabelle, alt, **kennung)
            except sqlite3.IntegrityError:  # Tasche oder Kiste gibt es nicht mehr
                raise Fehler(KONFLIKT, 409)
        else:
            raise Fehler(KONFLIKT, 409)
    return neuer


# ------------------------------------------------------------------ Routen


def routen(h, bilder: Path | None = None) -> list[Route]:
    """Routen der Prüfansicht. `bilder`: Ordner der lokalen Scryfall-Bilder (Standard `h.bilder_pfad`, falls der
    Helfer so etwas hat, sonst `katalog.STANDARD_BILDER`)."""
    bilder_ordner = Path(bilder or getattr(h, "bilder_pfad", None) or katalog_paket.STANDARD_BILDER)
    vorschau_ordner = h.bilder / "scryfall"

    def fall(con, **kennung):
        return fall_holen(con, h.nutzer, katalog=_katalog_da(h), **kennung)

    # -------------------------------------------------------- Lesen

    def lesend(funktion):
        def huelle(request):
            try:
                with closing(h.db()) as con:
                    _version_pruefen(con)
                    antwort = funktion(con, request)
            except Fehler as f:
                return fehler(str(f), f.status)
            return antwort if hasattr(antwort, "status_code") else JSONResponse(antwort)
        return huelle

    @lesend
    def warteschlange(con, request):
        zeilen = con.execute(SQL_WARTESCHLANGE, {"n": h.nutzer}).fetchall()
        faelle = [{
            "art": z["art"], "karte_id": z["karte_id"], "tasche_id": z["tasche_id"], "status": z["status"],
            "name": z["name"], "ausschnitt": z["ausschnitt"], "ort": _ort(z),
        } for z in zeilen[:WARTESCHLANGE_MAX]]
        return {"faelle": faelle, "gesamt": len(zeilen), "katalog": _katalog_da(h)}

    @lesend
    def fall_karte(con, request):
        antwort = fall(con, karte_id=_id(request, "Karte"))
        if antwort is None:
            raise Fehler("Karte nicht gefunden.", 404)
        return antwort

    @lesend
    def fall_tasche(con, request):
        antwort = fall(con, tasche_id=_id(request, "Tasche"))
        if antwort is None:
            raise Fehler("Tasche nicht gefunden.", 404)
        return antwort

    def namen(request):
        text = request.query_params.get("q", "").strip()
        kat = h.katalog()
        if kat is None:
            return JSONResponse({"verfuegbar": False})
        with closing(kat):
            treffer = suche.namen(kat, text[:SUCHTEXT_MAX], NAMEN_MAX) if len(text) >= 2 else []
        return JSONResponse({"verfuegbar": True, "treffer": treffer})

    def drucke(request):
        kat = h.katalog()
        if kat is None:
            return JSONResponse({"verfuegbar": False})
        with closing(kat):
            try:
                oracle_id = _kennung(request.query_params.get("oracle_id"), "Oracle-ID")
            except Fehler as f:
                return fehler(str(f), f.status)
            liste = suche.drucke(kat, oracle_id)
        for druck in liste:
            druck["vorschau"] = (vorschau_ordner / f"{druck['scryfall_id']}_200.webp").is_file()
        return JSONResponse({"verfuegbar": True, "drucke": liste})

    def bild(request):
        if not _katalog_da(h):
            return fehler("Bild nicht gefunden.", 404)
        try:
            scryfall_id = _kennung(request.path_params["scryfall_id"], "Bildkennung")
        except Fehler as f:
            return fehler(str(f), f.status)
        datei = katalog_paket.bilddatei(scryfall_id, bilder_ordner)
        if datei is None or datei.resolve().parent != bilder_ordner.resolve():
            return fehler("Bild nicht gefunden.", 404)
        return FileResponse(datei, media_type="image/jpeg")

    # -------------------------------------------------------- Schreiben

    async def schreiben(arbeit) -> JSONResponse:
        """`arbeit(con)` in einer Schreibtransaktion (Thread); gibt die Antwort als JSON zurück."""
        def innen(con):
            _version_pruefen(con)
            return arbeit(con)
        return JSONResponse(await run_in_threadpool(h.schreibvorgang, innen))

    async def bestaetigen(request):
        karte_id = _id(request, "Karte")

        def arbeit(con):
            _karte_holen(con, h.nutzer, karte_id)
            schritt = _neuer_schritt()
            aendern(con, h.nutzer, QUELLE, "karte", karte_id, {"geprueft": 1}, schritt=schritt)
            return _mit_schritt(con, fall(con, karte_id=karte_id), schritt)
        return await schreiben(arbeit)

    async def druck(request):
        karte_id = _id(request, "Karte")
        d = await h.json_daten(request)
        scryfall_id = _kennung(d.get("scryfall_id"), "Scryfall-ID")
        sprache = d.get("sprache")
        if sprache is not None and (not isinstance(sprache, str) or sprache not in SPRACHEN):
            raise Fehler("Unbekannte Sprache. Möglich: " + ", ".join(SPRACHEN) + ".")
        foil = d.get("foil")
        if foil is not None and not isinstance(foil, bool):
            raise Fehler("Foil muss „true“ oder „false“ sein.")

        def katalog_lesen():
            kat = h.katalog()
            if kat is None:
                raise Fehler(KEIN_KATALOG, 409)
            with closing(kat):
                gefunden = kartendaten.nachschlagen(kat, scryfall_id)
            if gefunden is None:
                raise Fehler("Diesen Druck gibt es im Katalog nicht.", 404)
            return gefunden
        daten = await run_in_threadpool(katalog_lesen)

        def arbeit(con):
            _karte_holen(con, h.nutzer, karte_id)
            schritt = _neuer_schritt()
            felder = {"scryfall_id": daten["scryfall_id"], "oracle_id": daten["oracle_id"], "name": daten["name"],
                      "set_code": daten["set_code"], "nummer": daten["nummer"], "bild": daten["bild"],
                      "geprueft": 1}
            if sprache is not None:
                felder["sprache"] = sprache
            if foil is not None:
                felder["foil"] = int(foil)
            aendern(con, h.nutzer, QUELLE, "karte", karte_id, felder, schritt=schritt)
            # Kartendaten sind abgeleitet (Katalog) und haben keine Historie.
            kartendaten.schreiben(con, daten)
            return _mit_schritt(con, fall(con, karte_id=karte_id), schritt)
        return await schreiben(arbeit)

    async def karte_aendern(request):
        karte_id = _id(request, "Karte")
        d = await h.json_daten(request)
        if set(d) - set(FELDNAMEN):
            raise Fehler("Die Anfrage enthält eine unbekannte Angabe. Möglich: anzahl, foil, sprache.")
        felder = {}
        if d.get("anzahl") is not None:
            anzahl = d["anzahl"]
            if isinstance(anzahl, bool) or not isinstance(anzahl, int) or not 1 <= anzahl <= ANZAHL_MAX:
                raise Fehler(f"Die Anzahl muss eine ganze Zahl von 1 bis {ANZAHL_MAX} sein.")
            felder["anzahl"] = anzahl
        if d.get("foil") is not None:
            if not isinstance(d["foil"], bool):
                raise Fehler("Foil muss „true“ oder „false“ sein.")
            felder["foil"] = int(d["foil"])
        if d.get("sprache") is not None:
            if not isinstance(d["sprache"], str) or d["sprache"] not in SPRACHEN:
                raise Fehler("Unbekannte Sprache. Möglich: " + ", ".join(SPRACHEN) + ".")
            felder["sprache"] = d["sprache"]
        if not felder:
            raise Fehler("Bitte mindestens eine Änderung angeben (Anzahl, Foil oder Sprache).")

        def arbeit(con):
            _karte_holen(con, h.nutzer, karte_id)
            schritt = _neuer_schritt()
            aendern(con, h.nutzer, QUELLE, "karte", karte_id, felder, schritt=schritt)
            return _mit_schritt(con, fall(con, karte_id=karte_id), schritt)
        return await schreiben(arbeit)

    async def karte_entfernen(request):
        karte_id = _id(request, "Karte")

        def arbeit(con):
            k = _karte_holen(con, h.nutzer, karte_id)
            schritt = _neuer_schritt()
            loeschen(con, h.nutzer, QUELLE, "karte", karte_id, schritt=schritt)
            tasche_id = k["tasche_id"]
            if tasche_id is not None:
                uebrig = con.execute("SELECT COUNT(*) FROM karte WHERE tasche_id = ?", (tasche_id,)).fetchone()[0]
                if uebrig == 0:
                    aendern(con, h.nutzer, QUELLE, "tasche", tasche_id, {"inhalt": "leer"}, schritt=schritt)
            return _entfernt(con, h.nutzer, karte_id, tasche_id, schritt)
        return await schreiben(arbeit)

    async def tasche_inhalt(request):
        tasche_id = _id(request, "Tasche")
        d = await h.json_daten(request)
        inhalt = d.get("inhalt")
        if not isinstance(inhalt, str) or inhalt not in INHALTE:
            raise Fehler("Ungültiger Inhalt. Möglich: leer oder rueckseite.")

        def arbeit(con):
            _tasche_holen(con, h.nutzer, tasche_id)
            schritt = _neuer_schritt()
            for z in con.execute("SELECT id FROM karte WHERE tasche_id = ? AND nutzer_id = ? ORDER BY id",
                                 (tasche_id, h.nutzer)).fetchall():
                loeschen(con, h.nutzer, QUELLE, "karte", z["id"], schritt=schritt)
            aendern(con, h.nutzer, QUELLE, "tasche", tasche_id, {"inhalt": inhalt}, schritt=schritt)
            return _mit_schritt(con, fall(con, tasche_id=tasche_id), schritt)
        return await schreiben(arbeit)

    async def karte_rueckgaengig(request):
        karte_id = _id(request, "Karte")

        def arbeit(con):
            letzter = _letzter_schritt(con, h.nutzer, karte_id=karte_id)
            if letzter is None:
                bekannt = con.execute(
                    "SELECT 1 FROM karte WHERE id = :k AND nutzer_id = :n UNION ALL "
                    "SELECT 1 FROM historie WHERE tabelle = 'karte' AND datensatz = :k AND nutzer_id = :n LIMIT 1",
                    {"k": karte_id, "n": h.nutzer}).fetchone()
                if bekannt is None:
                    raise Fehler("Karte nicht gefunden.", 404)
                raise Fehler("Es gibt nichts zum Zurücknehmen.", 409)
            schritt = _zuruecknehmen(con, h.nutzer, letzter[0], letzter[1])
            antwort = fall(con, karte_id=karte_id)
            if antwort is None:
                return _entfernt(con, h.nutzer, karte_id, None, schritt)
            return _mit_schritt(con, antwort, schritt)
        return await schreiben(arbeit)

    async def tasche_rueckgaengig(request):
        tasche_id = _id(request, "Tasche")

        def arbeit(con):
            _tasche_holen(con, h.nutzer, tasche_id)
            letzter = _letzter_schritt(con, h.nutzer, tasche_id=tasche_id)
            if letzter is None:
                raise Fehler("Es gibt nichts zum Zurücknehmen.", 409)
            schritt = _zuruecknehmen(con, h.nutzer, letzter[0], letzter[1])
            return _mit_schritt(con, fall(con, tasche_id=tasche_id), schritt)
        return await schreiben(arbeit)

    return [
        Route("/pruefen/warteschlange", warteschlange),
        Route("/pruefen/karte/{id:int}", fall_karte),
        Route("/pruefen/tasche/{id:int}", fall_tasche),
        Route("/pruefen/namen", namen),
        Route("/pruefen/drucke", drucke),
        Route("/pruefen/bild/{scryfall_id}", bild),
        Route("/karte/{id:int}/bestaetigen", h.schreibend(bestaetigen), methods=["POST"]),
        Route("/karte/{id:int}/druck", h.schreibend(druck), methods=["POST"]),
        Route("/karte/{id:int}", h.schreibend(karte_aendern), methods=["PATCH"]),
        Route("/karte/{id:int}", h.schreibend(karte_entfernen), methods=["DELETE"]),
        Route("/karte/{id:int}/rueckgaengig", h.schreibend(karte_rueckgaengig), methods=["POST"]),
        Route("/tasche/{id:int}/inhalt", h.schreibend(tasche_inhalt), methods=["POST"]),
        Route("/tasche/{id:int}/rueckgaengig", h.schreibend(tasche_rueckgaengig), methods=["POST"]),
    ]
