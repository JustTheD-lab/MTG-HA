"""Bedienschritte mit exaktem Rückgängig (Karte hinzufügen, Verschieben, Abzug; Runde 28.10.2026).

Ein Bedienschritt ist eine Kennung `schritt` in allen seinen Historienzeilen (Quelle „nutzer“), wie in der
Prüfansicht. Anders als dort hält `aendern_genau` auch den alten Wert von `geaendert` in der Historie fest,
und `zuruecknehmen` stellt die Zeilen damit **exakt** wieder her (Tabellen hashgleich, nur die Historie
wächst). Läuft in der Transaktion des Aufrufers; jede Abweichung vom erwarteten Stand ist ein Konflikt (409)
und lässt über die Ausnahme alles unverändert.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid

from binderapp.db.datenbank import _protokoll
from binderapp.db.seiten import Fehler

QUELLE = "nutzer"
# Schritte, die die Sortierregeln von selbst ausführen (binderapp.sortieren), lassen sich genauso zurücknehmen.
QUELLE_REGEL = "sortierregel"
QUELLEN = (QUELLE, QUELLE_REGEL)
TABELLEN = ("karte", "tasche", "kiste", "scan_foto")
KONFLIKT = "Das lässt sich nicht mehr zurücknehmen: Die Karten wurden inzwischen geändert."
MUSTER = re.compile(r"^[0-9a-f]{32}$")
JETZT = "SELECT strftime('%Y-%m-%dT%H:%M:%S', 'now')"


def neuer_schritt() -> str:
    return uuid.uuid4().hex


def summe(con, nutzer: int) -> tuple[int, int]:
    """(Posten, Karten) über alle Binder und Kisten des Nutzers (Summenprüfung bei Verschieben und Abzug)."""
    z = con.execute("SELECT COUNT(*), COALESCE(SUM(anzahl), 0) FROM karte WHERE nutzer_id = ? "
                    "AND (kiste_id IS NOT NULL OR tasche_id IS NOT NULL)", (nutzer,)).fetchone()
    return z[0], z[1]


def kennung(text) -> str:
    """Schritt-Kennung aus einer Anfrage (nur 32 Hex-Zeichen)."""
    if not isinstance(text, str) or not MUSTER.match(text):
        raise Fehler("Ungültige Schritt-Kennung.")
    return text


def _zeile(con, tabelle: str, datensatz: int):
    assert tabelle in TABELLEN, tabelle
    return con.execute(f"SELECT * FROM {tabelle} WHERE id = ?", (datensatz,)).fetchone()


def aendern_genau(con, nutzer: int, tabelle: str, datensatz: int, felder: dict, *, schritt: str,
                  quelle: str = QUELLE) -> dict:
    """Wie `datenbank.aendern`, aber mit altem `geaendert` in der Historie (für exaktes Rückgängig)."""
    zeile = _zeile(con, tabelle, datensatz)
    if zeile is None:
        raise KeyError(f"{tabelle} {datensatz} fehlt")
    alt = {k: zeile[k] for k in felder if zeile[k] != felder[k]}
    if not alt:
        return {}
    neu = {k: felder[k] for k in alt}
    if "geaendert" in zeile.keys():
        alt["geaendert"] = zeile["geaendert"]
        neu["geaendert"] = con.execute(JETZT).fetchone()[0]
    con.execute(f"UPDATE {tabelle} SET {', '.join(f'{k} = ?' for k in neu)} WHERE id = ?", (*neu.values(), datensatz))
    _protokoll(con, nutzer, quelle, tabelle, datensatz, "geaendert", alt, neu, schritt)
    return neu


def einfuegen(con, nutzer: int, tabelle: str, felder: dict, *, schritt: str, rueckgaengig_von: str | None = None) -> int:
    assert tabelle in TABELLEN, tabelle
    cur = con.execute(f"INSERT INTO {tabelle} ({', '.join(felder)}) VALUES ({', '.join('?' for _ in felder)})",
                      tuple(felder.values()))
    _protokoll(con, nutzer, QUELLE, tabelle, cur.lastrowid, "angelegt", None, felder, schritt, rueckgaengig_von)
    return cur.lastrowid


def loeschen(con, nutzer: int, tabelle: str, datensatz: int, *, schritt: str,
             rueckgaengig_von: str | None = None, quelle: str = QUELLE) -> dict:
    zeile = _zeile(con, tabelle, datensatz)
    if zeile is None:
        raise KeyError(f"{tabelle} {datensatz} fehlt")
    alt = dict(zeile)
    con.execute(f"DELETE FROM {tabelle} WHERE id = ?", (datensatz,))
    _protokoll(con, nutzer, quelle, tabelle, datensatz, "geloescht", alt, None, schritt, rueckgaengig_von)
    return alt


def zeilen(con, nutzer: int, schritt: str) -> list:
    return con.execute("SELECT * FROM historie WHERE schritt = ? AND nutzer_id = ? AND quelle IN (?, ?) ORDER BY id",
                       (schritt, nutzer, *QUELLEN)).fetchall()


def zurueckgenommen(con, schritt: str) -> bool:
    return con.execute("SELECT 1 FROM historie WHERE rueckgaengig_von = ? LIMIT 1", (schritt,)).fetchone() is not None


def zuruecknehmen(con, nutzer: int, schritt: str) -> tuple[str, list]:
    """Nimmt einen Schritt exakt zurück; gibt (neue Kennung, Zeilen des alten Schritts) zurück."""
    alte = zeilen(con, nutzer, schritt)
    if not alte:
        raise Fehler("Diesen Schritt gibt es nicht.", 404)
    if any(z["rueckgaengig_von"] for z in alte):
        raise Fehler("Ein Rückgängig lässt sich nicht noch einmal zurücknehmen.", 409)
    if zurueckgenommen(con, schritt):
        raise Fehler("Dieser Schritt ist schon zurückgenommen.", 409)
    neuer = neuer_schritt()
    for z in reversed(alte):
        tabelle = z["tabelle"]
        if tabelle == "abzug":   # Protokollzeile eines Abzugs (db/abzug.py), kein Datensatz: bleibt stehen
            continue
        if tabelle not in TABELLEN:
            raise Fehler(KONFLIKT, 409)
        alt = json.loads(z["alt"]) if z["alt"] else None
        neu = json.loads(z["neu"]) if z["neu"] else None
        aktuell = _zeile(con, tabelle, z["datensatz"])
        if z["aktion"] == "geaendert":
            if aktuell is None or not alt or not neu or set(neu) - set(aktuell.keys()) or set(alt) - set(aktuell.keys()):
                raise Fehler(KONFLIKT, 409)
            if any(aktuell[k] != v for k, v in neu.items()):
                raise Fehler(KONFLIKT, 409)
            con.execute(f"UPDATE {tabelle} SET {', '.join(f'{k} = ?' for k in alt)} WHERE id = ?",
                        (*alt.values(), z["datensatz"]))
            _protokoll(con, nutzer, QUELLE, tabelle, z["datensatz"], "geaendert", neu, alt, neuer, schritt)
        elif z["aktion"] == "angelegt":
            if aktuell is None or not neu or any(aktuell[k] != v for k, v in neu.items() if k in aktuell.keys()):
                raise Fehler(KONFLIKT, 409)
            loeschen(con, nutzer, tabelle, z["datensatz"], schritt=neuer, rueckgaengig_von=schritt)
        elif z["aktion"] == "geloescht":
            if aktuell is not None or not alt:
                raise Fehler(KONFLIKT, 409)
            try:
                einfuegen(con, nutzer, tabelle, alt, schritt=neuer, rueckgaengig_von=schritt)
            except sqlite3.IntegrityError:  # Tasche oder Kiste gibt es nicht mehr
                raise Fehler(KONFLIKT, 409)
        else:
            raise Fehler(KONFLIKT, 409)
    return neuer, alte
