"""Preise lesen: aktueller Stand je Karte, Veränderung gegen Vortag, 3 und 30 Tage, Werte je Binder und Kiste.

Foil-Karten bekommen die Foil-Preise. Veränderungen nur aus echtem Verlauf (Entscheidung des Nutzers): gibt es
keinen passenden früheren Tag, bleibt der Wert None und die Oberfläche zeigt „–“. Ein früherer Tag passt, wenn
er höchstens um die Toleranz vom Ziel abweicht (das Add-on kann einen Tag aus gewesen sein).
Alles nur ab Datenbankversion 4; vorher liefern die Funktionen leere Ergebnisse.
"""
from __future__ import annotations

from datetime import date, timedelta

from binderapp.db.datenbank import version

ABSTAENDE = {"vortag": (1, 1), "tage3": (3, 1), "tage30": (30, 3)}   # Ziel in Tagen, Toleranz in Tagen

SQL_PREIS_KARTE = """
SELECT k.id,
       CASE WHEN k.foil THEN p.low_foil ELSE p.low END AS low,
       CASE WHEN k.foil THEN p.avg7_foil ELSE p.avg7 END AS avg7
FROM karte k
JOIN kartendaten d ON d.scryfall_id = k.scryfall_id
JOIN preis p ON p.cardmarket_id = d.cardmarket_id AND p.tag = ?
WHERE k.id IN ({platz})
"""

# Wert = Summe aus Anzahl × Durchschnitt 7 Tage (Foil-Preis für Foil-Karten); Karten ohne Preis zählen nicht mit.
SQL_WERT = """
SELECT {gruppe} AS gruppe,
       SUM(k.anzahl * CASE WHEN k.foil THEN p.avg7_foil ELSE p.avg7 END) AS wert,
       SUM(CASE WHEN (CASE WHEN k.foil THEN p.avg7_foil ELSE p.avg7 END) IS NULL THEN k.anzahl ELSE 0 END) AS ohne_preis
FROM karte k
{verbindung}
LEFT JOIN kartendaten d ON d.scryfall_id = k.scryfall_id
LEFT JOIN preis p ON p.cardmarket_id = d.cardmarket_id AND p.tag = ?
WHERE k.nutzer_id = ? {bedingung}
GROUP BY 1
"""


def _hat_preise(con) -> bool:
    return version(con) >= 4


def neuester_tag(con) -> str | None:
    if not _hat_preise(con):
        return None
    z = con.execute("SELECT MAX(tag) FROM preisabruf").fetchone()
    return z[0] if z else None


def stand(con) -> dict:
    """Stand der Preise für die Oberfläche."""
    if not _hat_preise(con):
        return {"tag": None, "tage": 0}
    z = con.execute("SELECT tag, erstellt, abgerufen, gespeichert FROM preisabruf ORDER BY tag DESC LIMIT 1").fetchone()
    tage = con.execute("SELECT COUNT(*) FROM preisabruf").fetchone()[0]
    if z is None:
        return {"tag": None, "tage": 0}
    return {"tag": z[0], "erstellt": z[1], "abgerufen": z[2] + "Z", "produkte": z[3], "tage": tage}


def preise_fuer_karten(con, karten_ids: list[int]) -> dict[int, dict]:
    """{karte_id: {"low": …, "avg7": …}} des neuesten Tages (nur Karten mit Preis)."""
    tag = neuester_tag(con)
    ids = [int(i) for i in karten_ids if i is not None]
    if tag is None or not ids:
        return {}
    ergebnis = {}
    for anfang in range(0, len(ids), 500):
        teil = ids[anfang:anfang + 500]
        for z in con.execute(SQL_PREIS_KARTE.format(platz=", ".join("?" for _ in teil)), (tag, *teil)):
            if z[1] is not None or z[2] is not None:
                ergebnis[z[0]] = {"low": z[1], "avg7": z[2]}
    return ergebnis


def _naechster_tag(tage: list[str], ziel: date, toleranz: int) -> str | None:
    """Der Tag aus `tage`, der `ziel` am nächsten liegt (höchstens `toleranz` Tage daneben)."""
    beste, abstand = None, None
    for t in tage:
        d = abs((date.fromisoformat(t) - ziel).days)
        if d <= toleranz and (abstand is None or d < abstand):
            beste, abstand = t, d
    return beste


def _prozent(jetzt, frueher):
    if jetzt is None or frueher is None or frueher <= 0:
        return None
    return round((jetzt - frueher) / frueher * 100, 1)


def verlauf_karte(con, karte_id: int) -> dict | None:
    """Preise einer Karte mit Veränderung für Low und Durchschnitt 7 Tage (Kartenansicht)."""
    tag = neuester_tag(con)
    if tag is None:
        return None
    k = con.execute("SELECT k.foil, d.cardmarket_id FROM karte k LEFT JOIN kartendaten d ON d.scryfall_id = k.scryfall_id"
                    " WHERE k.id = ?", (karte_id,)).fetchone()
    if k is None or k[1] is None:
        return {"tag": tag, "cardmarket_id": None}
    foil = bool(k[0])
    spalten = ("low_foil", "avg7_foil") if foil else ("low", "avg7")
    zeilen = {z[0]: (z[1], z[2]) for z in con.execute(
        f"SELECT tag, {spalten[0]}, {spalten[1]} FROM preis WHERE cardmarket_id = ? AND tag <= ? AND tag >= ?",
        (k[1], tag, (date.fromisoformat(tag) - timedelta(days=40)).isoformat()))}
    jetzt = zeilen.get(tag)
    ergebnis = {"tag": tag, "cardmarket_id": k[1], "foil": foil,
                "low": jetzt[0] if jetzt else None, "avg7": jetzt[1] if jetzt else None,
                "veraenderung": {"low": {}, "avg7": {}}}
    frueher_tage = sorted(t for t in zeilen if t != tag)
    for name, (tage, toleranz) in ABSTAENDE.items():
        t = _naechster_tag(frueher_tage, date.fromisoformat(tag) - timedelta(days=tage), toleranz)
        alt = zeilen.get(t) if t else None
        for i, feld in enumerate(("low", "avg7")):
            ergebnis["veraenderung"][feld][name] = _prozent(jetzt[i] if jetzt else None, alt[i] if alt else None)
    return ergebnis


def werte(con, nutzer: int, art: str) -> dict[int, dict]:
    """Wert (Durchschnitt 7 Tage) je Binder (`art="binder"`) oder je Kiste (`art="kiste"`):
    {id: {"wert": Euro, "ohne_preis": Zahl der Karten ohne Preis}}."""
    tag = neuester_tag(con)
    if tag is None:
        return {}
    if art == "binder":
        sql = SQL_WERT.format(gruppe="s.binder_id", verbindung="JOIN tasche t ON t.id = k.tasche_id "
                              "JOIN seite s ON s.id = t.seite_id", bedingung="")
    elif art == "kiste":
        sql = SQL_WERT.format(gruppe="k.kiste_id", verbindung="", bedingung="AND k.kiste_id IS NOT NULL")
    else:
        raise ValueError(art)
    return {z[0]: {"wert": round(z[1] or 0, 2), "ohne_preis": z[2]} for z in con.execute(sql, (tag, nutzer))}


def gesamtwert(con, nutzer: int) -> dict | None:
    tag = neuester_tag(con)
    if tag is None:
        return None
    sql = SQL_WERT.format(gruppe="1", verbindung="", bedingung="")
    z = con.execute(sql, (tag, nutzer)).fetchone()
    return {"wert": round((z[1] if z else 0) or 0, 2), "ohne_preis": z[2] if z else 0, "tag": tag}
