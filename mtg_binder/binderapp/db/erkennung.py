"""Warteschlange der Erkennung in der Datenbank: Fotos holen, Ergebnis schreiben, Fehler, Lebenszeichen.

Gemeinsam für den Verarbeiter am PC (`verarbeiten.warteschlange.LokaleWarteschlange`, direkt auf der Datei)
und die PC-Schnittstelle des Add-ons (`web.pc`, der PC schickt sein Ergebnis über das Heimnetz). Nur
Standardbibliothek und der Importcode, damit es im Add-on läuft. Alle Schreibzugriffe mit Historie (Quelle
„erkennung“) in der Transaktion des Aufrufers.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict

from binderapp.db.datenbank import aendern, einfuegen
from binderapp.db.importieren import Tasche, schreibe_taschen
from binderapp.db.seiten import raster_masse

# Stand der Erkennung, mit dem hochgeladene Fotos jetzt erkannt werden (abgenommen am 29.10.2026, Sprachkorrektur).
# Der erste Import (importieren.ERKENNUNG = c804be1) behält seinen eigenen Stand.
ERKENNUNG = "527d563"
QUELLE = "erkennung"
DIENST = "verarbeiter"
FRISCH = 30  # Sekunden: so jung muss ein Lebenszeichen sein, damit ein anderer Verarbeiter als aktiv gilt

SQL_NEUE = (
    "SELECT f.id AS foto_id, f.datei, f.format, s.id AS seite_id, s.nummer, b.id AS binder_id, b.raster"
    " FROM foto f JOIN seite s ON s.foto_id = f.id JOIN binder b ON b.id = s.binder_id"
    " WHERE f.status = 'neu' AND f.nutzer_id = ? ORDER BY b.id, s.nummer LIMIT ?")


def lebenszeichen(con, info: dict) -> None:
    con.execute("INSERT INTO dienst (name, zeitpunkt, info) VALUES (?, strftime('%Y-%m-%dT%H:%M:%S', 'now'), ?)"
                " ON CONFLICT(name) DO UPDATE SET zeitpunkt = excluded.zeitpunkt, info = excluded.info",
                (DIENST, json.dumps(info, ensure_ascii=False)))


def letztes_lebenszeichen(con) -> tuple[dict, float] | None:
    """(Info, Alter in Sekunden) des letzten Lebenszeichens oder None."""
    z = con.execute("SELECT info, (julianday('now') - julianday(zeitpunkt)) * 86400 AS alter_s"
                    " FROM dienst WHERE name = ?", (DIENST,)).fetchone()
    return None if z is None else (json.loads(z["info"] or "{}"), z["alter_s"])


def zuruecksetzen(con, nutzer: int) -> int:
    """Fotos, die noch „in_verarbeitung“ stehen, zurück auf „neu“ (Absturz, Abbruch, Beenden)."""
    haengend = con.execute("SELECT id FROM foto WHERE status = 'in_verarbeitung' AND nutzer_id = ?",
                           (nutzer,)).fetchall()
    for f in haengend:
        aendern(con, nutzer, QUELLE, "foto", f["id"], {"status": "neu"})
    return len(haengend)


def holen(con, nutzer: int, hoechstens: int) -> list[dict]:
    """Bis zu `hoechstens` neue Fotos in Seitenreihenfolge; sie stehen danach auf „in_verarbeitung“."""
    zeilen = con.execute(SQL_NEUE, (nutzer, hoechstens)).fetchall()
    for z in zeilen:
        aendern(con, nutzer, QUELLE, "foto", z["foto_id"], {"status": "in_verarbeitung", "fehler": None})
    return [dict(z) for z in zeilen]


def ergebnis_schreiben(con, nutzer: int, foto_id: int, seite_id: int, raster: str, nummer, taschen: list[Tasche]) -> dict:
    """Schreibt Taschen und Karten der Seite (Importcode) und setzt das Foto auf „erkannt“.

    Positionen des Rasters, für die die Erkennung keinen Ausschnitt liefert (Tasche außerhalb des Fotos),
    werden „unsicher“ ohne Karte: lieber prüfen als eine Karte stillschweigend verlieren.
    """
    zeilen_n, spalten_n = raster_masse(raster)
    f = con.execute("SELECT f.status, s.id AS seite_id FROM foto f JOIN seite s ON s.foto_id = f.id"
                    " WHERE f.id = ? AND f.nutzer_id = ?", (foto_id, nutzer)).fetchone()
    if f is None or f["status"] != "in_verarbeitung" or f["seite_id"] != seite_id:
        raise RuntimeError(f"Foto {foto_id} gehört nicht mehr zu dieser Seite oder wurde zurückgesetzt.")
    if con.execute("SELECT COUNT(*) FROM tasche WHERE seite_id = ?", (seite_id,)).fetchone()[0]:
        raise RuntimeError(f"Seite {nummer} hat schon Taschen.")
    if any(t.zeile > zeilen_n or t.spalte > spalten_n or t.zeile < 1 or t.spalte < 1 for t in taschen):
        raise RuntimeError(f"Taschen außerhalb des Rasters {raster}.")
    schreibe_taschen(con, nutzer, seite_id, taschen, {})
    belegt = {(t.zeile, t.spalte) for t in taschen}
    fehlend = [(z, s) for z in range(1, zeilen_n + 1) for s in range(1, spalten_n + 1) if (z, s) not in belegt]
    for z, s in fehlend:
        einfuegen(con, nutzer, QUELLE, "tasche", {"seite_id": seite_id, "zeile": z, "spalte": s, "inhalt": "unsicher"})
    aendern(con, nutzer, QUELLE, "foto", foto_id, {"status": "erkannt", "fehler": None, "erkennung": ERKENNUNG})
    karten = con.execute("SELECT COUNT(*) FROM karte k JOIN tasche t ON t.id = k.tasche_id WHERE t.seite_id = ?",
                         (seite_id,)).fetchone()[0]
    return {"taschen": len(taschen) + len(fehlend), "ohne_ausschnitt": len(fehlend), "karten": karten}


def fehlgeschlagen(con, nutzer: int, foto_id: int, grund: str) -> None:
    aendern(con, nutzer, QUELLE, "foto", foto_id, {"status": "fehler", "fehler": str(grund)[:500]})


# ---------------------------------------------------------------- Übertragung (PC ↔ Add-on)

TASCHE_FELDER = ("satz", "foto", "menge", "zeile", "spalte", "erk", "aus_status")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}")


def tasche_als_dict(t: Tasche) -> dict:
    return {k: getattr(t, k) for k in TASCHE_FELDER}


def tasche_aus_dict(d: dict) -> Tasche:
    """Prüft eine übertragene Tasche streng (Typen, Längen); wirft ValueError."""
    if not isinstance(d, dict) or set(d) != set(TASCHE_FELDER):
        raise ValueError("Tasche: falsche Felder.")
    if not all(isinstance(d[k], str) and len(d[k]) <= 120 for k in ("satz", "foto", "menge", "aus_status")):
        raise ValueError("Tasche: ungültige Angaben.")
    if not NAME.fullmatch(d["satz"]):   # wird Dateiname der Vorschaubilder
        raise ValueError("Tasche: ungültiger Name des Ausschnitts.")
    if not all(isinstance(d[k], int) and not isinstance(d[k], bool) and 1 <= d[k] <= 10 for k in ("zeile", "spalte")):
        raise ValueError("Tasche: ungültige Lage.")
    erk = d["erk"]
    if not isinstance(erk, dict) or len(erk) > 60 or not all(
            isinstance(k, str) and isinstance(v, str) and len(k) <= 60 and len(v) <= 2000 for k, v in erk.items()):
        raise ValueError("Tasche: ungültige Erkennung.")
    return Tasche(**{k: d[k] for k in TASCHE_FELDER})


def scryfall_ids(taschen: list[Tasche]) -> list[str]:
    """Drucke, die das Ergebnis vorschlägt (für Kartendaten und Vorschaubilder)."""
    ids = defaultdict(int)
    for t in taschen:
        sid = (t.erk.get("scryfall_id") or "").lower()
        if sid and t.erk.get("status") != "rueckseite":
            ids[sid] += 1
    return sorted(ids)
