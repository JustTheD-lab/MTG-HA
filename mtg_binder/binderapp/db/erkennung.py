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

from binderapp.db.datenbank import aendern, einfuegen, version
from binderapp.db.importieren import Tasche, karte_felder, schreibe_taschen
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
    """Fotos (auch Scan-Fotos), die noch „in_verarbeitung“ stehen, zurück auf „neu“ (Absturz, Abbruch, Beenden)."""
    haengend = con.execute("SELECT id FROM foto WHERE status = 'in_verarbeitung' AND nutzer_id = ?",
                           (nutzer,)).fetchall()
    for f in haengend:
        aendern(con, nutzer, QUELLE, "foto", f["id"], {"status": "neu"})
    scan = []
    if version(con) >= 5:
        scan = con.execute("SELECT f.id FROM scan_foto f JOIN scan_durchgang d ON d.id = f.durchgang_id"
                           " WHERE f.status = 'in_verarbeitung' AND d.nutzer_id = ?", (nutzer,)).fetchall()
        for f in scan:
            aendern(con, nutzer, QUELLE, "scan_foto", f["id"], {"status": "neu"})
    return len(haengend) + len(scan)


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


# ---------------------------------------------------------------- Bulk-Scan (ab Version 5, Teilrunde B)
#
# Ein Scan-Foto zeigt genau eine Karte (Card Slinger). Erkannt wird mit derselben Erkennung wie bei Binderseiten;
# das Ergebnis landet in der festen Kiste „Scan-Eingang“ (Entscheidung des Nutzers, 31.10.2026). Gleicher Druck,
# gleiche Sprache, nicht Foil und „sicher“ wird zu einem ungeprüften Posten mit Anzahl zusammengefasst; alles andere
# bleibt ein eigener Posten, damit die Prüfansicht jede unsichere Karte einzeln zeigt.

SCAN_KISTE = "Scan-Eingang"
SCAN_MARKE = "@scan-eingang"   # kiste.extern_name: findet die Kiste auch nach dem Umbenennen

SQL_SCAN_NEUE = (
    "SELECT f.id AS scan_id, f.datei, f.durchgang_id, f.nummer, f.sha256"
    " FROM scan_foto f JOIN scan_durchgang d ON d.id = f.durchgang_id"
    " WHERE f.status = 'neu' AND d.nutzer_id = ? ORDER BY f.id LIMIT ?")


def scan_holen(con, nutzer: int, hoechstens: int) -> list[dict]:
    """Bis zu `hoechstens` neue Scan-Fotos in Aufnahmereihenfolge; sie stehen danach auf „in_verarbeitung“."""
    if version(con) < 5:
        return []
    zeilen = con.execute(SQL_SCAN_NEUE, (nutzer, hoechstens)).fetchall()
    for z in zeilen:
        aendern(con, nutzer, QUELLE, "scan_foto", z["scan_id"], {"status": "in_verarbeitung", "fehler": None})
    return [dict(z) for z in zeilen]


def scan_kiste(con, nutzer: int) -> int:
    """Die Kiste „Scan-Eingang“: über die Marke, sonst eine gleichnamige ohne Herkunft übernehmen, sonst neu anlegen.
    Eine gleichnamige Kiste aus einem ManaBox-Import (extern_name gesetzt) bleibt unberührt, damit ihr Import
    wiederholbar bleibt; dann heißt die neue Kiste „Scan-Eingang 2“ usw."""
    z = con.execute("SELECT id FROM kiste WHERE nutzer_id = ? AND extern_name = ?", (nutzer, SCAN_MARKE)).fetchone()
    if z is not None:
        return z["id"]
    z = con.execute("SELECT id FROM kiste WHERE nutzer_id = ? AND name = ? AND extern_name IS NULL",
                    (nutzer, SCAN_KISTE)).fetchone()
    if z is not None:
        aendern(con, nutzer, QUELLE, "kiste", z["id"], {"extern_name": SCAN_MARKE})
        return z["id"]
    vergeben = {r["name"] for r in con.execute("SELECT name FROM kiste WHERE nutzer_id = ?", (nutzer,))}
    name, n = SCAN_KISTE, 1
    while name in vergeben:
        n += 1
        name = f"{SCAN_KISTE} {n}"
    platz = con.execute("SELECT COALESCE(MAX(regalplatz), 0) + 1 FROM kiste WHERE nutzer_id = ?", (nutzer,)).fetchone()[0]
    return einfuegen(con, nutzer, QUELLE, "kiste", {"nutzer_id": nutzer, "name": name, "regalplatz": platz,
                                                    "extern_name": SCAN_MARKE})


def scan_ergebnis_schreiben(con, nutzer: int, scan_id: int, tasche: Tasche | None) -> dict:
    """Legt die Karte des Scan-Fotos in der Kiste „Scan-Eingang“ an (oder zählt einen gleichen Posten hoch) und
    setzt das Foto auf „erkannt“. Ohne Karte (keine gefunden, Rückseite): Foto „fehler“ mit Grund."""
    v6 = version(con) >= 6
    f = con.execute("SELECT f.status" + (", d.einsortieren" if v6 else "") + " FROM scan_foto f"
                    " JOIN scan_durchgang d ON d.id = f.durchgang_id"
                    " WHERE f.id = ? AND d.nutzer_id = ?", (scan_id, nutzer)).fetchone()
    if f is None or f["status"] != "in_verarbeitung":
        raise RuntimeError(f"Scan-Foto {scan_id} ist nicht in Arbeit.")
    # ab Version 6: die Karte wartet aufs Einsortieren nach den Sortierregeln (binderapp.sortieren)
    offen = {"sortieren": "offen"} if v6 and f["einsortieren"] else {}
    e = tasche.erk if tasche is not None else {}
    if tasche is None or e.get("status") == "rueckseite" or not e.get("name"):
        grund = ("Auf dem Foto liegt eine Kartenrückseite." if e.get("status") == "rueckseite"
                 else "Auf dem Foto wurde keine Karte gefunden.")
        aendern(con, nutzer, QUELLE, "scan_foto", scan_id, {"status": "fehler", "fehler": grund, "erkennung": ERKENNUNG})
        return {"karte_id": None, "fehler": grund}
    kiste = scan_kiste(con, nutzer)
    kid, zusammen = None, False
    if e.get("status") == "sicher" and e.get("scryfall_id"):
        z = con.execute(
            "SELECT id, anzahl FROM karte WHERE nutzer_id = ? AND kiste_id = ? AND quelle = 'scan' AND scryfall_id = ?"
            " AND COALESCE(sprache, '') = ? AND foil = 0 AND status = 'sicher' AND geprueft = 0 ORDER BY id LIMIT 1",
            (nutzer, kiste, e["scryfall_id"], e.get("sprache") or "")).fetchone()
        if z is not None:
            aendern(con, nutzer, QUELLE, "karte", z["id"], {"anzahl": z["anzahl"] + 1, **offen})
            kid, zusammen = z["id"], True
    if kid is None:
        felder = karte_felder(tasche, nutzer, None)
        felder.pop("tasche_id")
        kid = einfuegen(con, nutzer, QUELLE, "karte", {**felder, "kiste_id": kiste, "quelle": "scan", **offen})
    aendern(con, nutzer, QUELLE, "scan_foto", scan_id,
            {"status": "erkannt", "karte_id": kid, "fehler": None, "erkennung": ERKENNUNG})
    return {"karte_id": kid, "name": e["name"], "status": e["status"], "zusammengefasst": zusammen, "kiste_id": kiste}


def scan_fehlgeschlagen(con, nutzer: int, scan_id: int, grund: str) -> None:
    f = con.execute("SELECT f.status FROM scan_foto f JOIN scan_durchgang d ON d.id = f.durchgang_id"
                    " WHERE f.id = ? AND d.nutzer_id = ?", (scan_id, nutzer)).fetchone()
    if f is None or f["status"] != "in_verarbeitung":
        raise RuntimeError(f"Scan-Foto {scan_id} ist nicht in Arbeit.")
    aendern(con, nutzer, QUELLE, "scan_foto", scan_id, {"status": "fehler", "fehler": str(grund)[:500]})


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
