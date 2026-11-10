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

# Preisarten: Spalten der Tabelle `preis` (normal, Foil) und Namen für die Oberfläche. Welche gilt, wählt der Nutzer
# (Spalte `nutzer.preisart` ab Version 21, Paket „Preisart wählbar“, 10.11.2026); Standard Ø 7 Tage. Nur Werte aus
# dieser Tabelle kommen ins SQL. Trend, Ø 1 und Ø 30 Tage gibt es erst ab Version 21 (Verlauf ab dem Speichern).
PREISARTEN = {"low": ("low", "low_foil"), "trend": ("trend", "trend_foil"), "avg1": ("avg1", "avg1_foil"),
              "avg7": ("avg7", "avg7_foil"), "avg30": ("avg30", "avg30_foil")}
NAMEN = {"low": "Low", "trend": "Trend", "avg1": "Ø 1 Tag", "avg7": "Ø 7 Tage", "avg30": "Ø 30 Tage"}
PREISART = "avg7"
NEU_AB_VERSION = 21
ALT = ("low", "avg7")   # Arten, die es schon vor Version 21 gab


def arten(con) -> tuple[str, ...]:
    """Die Preisarten, die diese Datenbank speichert."""
    return tuple(PREISARTEN) if version(con) >= NEU_AB_VERSION else ALT


def gewaehlt(con, nutzer: int | None = None) -> str:
    """Die Preisart des Nutzers (ohne Nutzer: des ersten, solange es keine Benutzerverwaltung gibt)."""
    if version(con) < NEU_AB_VERSION:
        return PREISART
    if nutzer is None:
        z = con.execute("SELECT preisart FROM nutzer ORDER BY id LIMIT 1").fetchone()
    else:
        z = con.execute("SELECT preisart FROM nutzer WHERE id = ?", (nutzer,)).fetchone()
    return z[0] if z and z[0] in PREISARTEN else PREISART


def aufloesen(con, preisart: str | None, nutzer: int | None = None) -> str:
    art = preisart or gewaehlt(con, nutzer)
    if art not in PREISARTEN:
        raise ValueError(f"unbekannte Preisart {art!r}")
    return art


def waehlen(con, nutzer: int, preisart: str) -> bool:
    """Setzt die Preisart des Nutzers (in der Transaktion des Aufrufers, mit Historie). True, wenn sich etwas ändert."""
    from binderapp.db.datenbank import aendern   # erst hier: datenbank importiert nichts aus preise
    if version(con) < NEU_AB_VERSION:
        raise ValueError("Die Preisart lässt sich erst ab Datenbankversion 21 wählen.")
    if preisart not in PREISARTEN:
        raise ValueError(f"unbekannte Preisart {preisart!r}")
    return bool(aendern(con, nutzer, "nutzer", "nutzer", nutzer, {"preisart": preisart}))


def auswahl(con, nutzer: int | None = None) -> dict:
    """Für die Oberfläche: gewählte Art und alle wählbaren mit Namen."""
    return {"preisart": gewaehlt(con, nutzer), "name": NAMEN[gewaehlt(con, nutzer)],
            "arten": [{"wert": a, "text": NAMEN[a]} for a in arten(con)]}


def preis_ausdruck(preisart: str | None = None, tabelle: str = "p", karte: str = "k") -> str:
    """SQL-Ausdruck für den Preis einer Karte `k` in der Preisart (Foil-Karten mit Foil-Preis)."""
    normal, foil = PREISARTEN[preisart]
    return f"(CASE WHEN {karte}.foil THEN {tabelle}.{foil} ELSE {tabelle}.{normal} END)"

SQL_PREIS_KARTE = """
SELECT k.id, {spalten}
FROM karte k
JOIN kartendaten d ON d.scryfall_id = k.scryfall_id
JOIN preis p ON p.cardmarket_id = d.cardmarket_id AND p.tag = ?
WHERE k.id IN ({platz})
"""

# Wert = Summe aus Anzahl × Preis der Preisart (Foil-Preis für Foil-Karten); Karten ohne Preis zählen nicht mit.
SQL_WERT = """
SELECT {gruppe} AS gruppe,
       SUM(k.anzahl * {preis}) AS wert,
       SUM(CASE WHEN {preis} IS NULL THEN k.anzahl ELSE 0 END) AS ohne_preis
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


def preise_fuer_karten(con, karten_ids: list[int], preisart: str | None = None) -> dict[int, dict]:
    """{karte_id: {"low": …, "avg7": …, (ab Version 21) "trend", "avg1", "avg30", "art": gewählte Art,
    "wert": Preis der gewählten Art}} des neuesten Tages (nur Karten mit Preis)."""
    tag = neuester_tag(con)
    ids = [int(i) for i in karten_ids if i is not None]
    if tag is None or not ids:
        return {}
    art, namen = aufloesen(con, preisart), arten(con)
    spalten = ", ".join(preis_ausdruck(a) for a in namen)
    ergebnis = {}
    for anfang in range(0, len(ids), 500):
        teil = ids[anfang:anfang + 500]
        sql = SQL_PREIS_KARTE.format(spalten=spalten, platz=", ".join("?" for _ in teil))
        for z in con.execute(sql, (tag, *teil)):
            preise = dict(zip(namen, z[1:]))
            if any(v is not None for v in preise.values()):
                ergebnis[z[0]] = {**preise, "art": art, "wert": preise[art]}
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


def verlauf_karte(con, karte_id: int, preisart: str | None = None) -> dict | None:
    """Preise einer Karte in allen gespeicherten Arten mit Veränderung (Kartenansicht); `art` ist die gewählte."""
    tag = neuester_tag(con)
    if tag is None:
        return None
    art, namen = aufloesen(con, preisart), arten(con)
    k = con.execute("SELECT k.foil, d.cardmarket_id FROM karte k LEFT JOIN kartendaten d ON d.scryfall_id = k.scryfall_id"
                    " WHERE k.id = ?", (karte_id,)).fetchone()
    if k is None or k[1] is None:
        return {"tag": tag, "cardmarket_id": None, "art": art}
    foil = bool(k[0])
    spalten = ", ".join(PREISARTEN[a][1 if foil else 0] for a in namen)
    zeilen = {z[0]: z[1:] for z in con.execute(
        f"SELECT tag, {spalten} FROM preis WHERE cardmarket_id = ? AND tag <= ? AND tag >= ?",
        (k[1], tag, (date.fromisoformat(tag) - timedelta(days=40)).isoformat()))}
    jetzt = zeilen.get(tag)
    ergebnis = {"tag": tag, "cardmarket_id": k[1], "foil": foil, "art": art, "arten": list(namen),
                **{a: (jetzt[i] if jetzt else None) for i, a in enumerate(namen)},
                "veraenderung": {a: {} for a in namen}}
    ergebnis["wert"] = ergebnis[art]
    frueher_tage = sorted(t for t in zeilen if t != tag)
    for name, (tage, toleranz) in ABSTAENDE.items():
        t = _naechster_tag(frueher_tage, date.fromisoformat(tag) - timedelta(days=tage), toleranz)
        alt = zeilen.get(t) if t else None
        for i, feld in enumerate(namen):
            ergebnis["veraenderung"][feld][name] = _prozent(jetzt[i] if jetzt else None, alt[i] if alt else None)
    return ergebnis


def werte(con, nutzer: int, art: str, preisart: str | None = None) -> dict[int, dict]:
    """Wert (Preisart, ohne Angabe die gewählte) je Binder (`art="binder"`) oder je Kiste (`art="kiste"`):
    {id: {"wert": Euro, "ohne_preis": Zahl der Karten ohne Preis}}."""
    preisart = aufloesen(con, preisart, nutzer)
    tag = neuester_tag(con)
    if tag is None:
        return {}
    preis = preis_ausdruck(preisart)
    if art == "binder":
        sql = SQL_WERT.format(gruppe="s.binder_id", preis=preis, verbindung="JOIN tasche t ON t.id = k.tasche_id "
                              "JOIN seite s ON s.id = t.seite_id", bedingung="")
    elif art == "kiste":
        sql = SQL_WERT.format(gruppe="k.kiste_id", preis=preis, verbindung="", bedingung="AND k.kiste_id IS NOT NULL")
    elif art == "deck":   # ab Version 8: Karten, die im Deck liegen
        sql = SQL_WERT.format(gruppe="k.deck_id", preis=preis, verbindung="", bedingung="AND k.deck_id IS NOT NULL")
    else:
        raise ValueError(art)
    return {z[0]: {"wert": round(z[1] or 0, 2), "ohne_preis": z[2]} for z in con.execute(sql, (tag, nutzer))}


def fach_werte(con, nutzer: int, kiste_id: int, preisart: str | None = None) -> dict[int, dict]:
    """Wert je Fach einer Kiste aus der temporären Tabelle fachwahl (`web.kisten.fach_vorbereiten`):
    {Platz des Fachs oder -1 für Karten ohne Fach: {"wert": Euro, "ohne_preis": Karten ohne Preis}}."""
    preisart = aufloesen(con, preisart, nutzer)
    tag = neuester_tag(con)
    if tag is None:
        return {}
    sql = SQL_WERT.format(gruppe="COALESCE(fw.platz, -1)", preis=preis_ausdruck(preisart),
                          verbindung="LEFT JOIN temp.fachwahl fw ON fw.karte_id = k.id", bedingung="AND k.kiste_id = ?")
    return {z[0]: {"wert": round(z[1] or 0, 2), "ohne_preis": z[2]} for z in con.execute(sql, (tag, nutzer, kiste_id))}


def gesamtwert(con, nutzer: int, preisart: str | None = None) -> dict | None:
    preisart = aufloesen(con, preisart, nutzer)
    tag = neuester_tag(con)
    if tag is None:
        return None
    sql = SQL_WERT.format(gruppe="1", preis=preis_ausdruck(preisart), verbindung="", bedingung="")
    z = con.execute(sql, (tag, nutzer)).fetchone()
    return {"wert": round((z[1] if z else 0) or 0, 2), "ohne_preis": z[2] if z else 0, "tag": tag}


# Veränderung am heutigen Bestand: dieselben Karten mit dem Preis des neuesten und des früheren Tages; nur Karten,
# die an beiden Tagen einen Preis haben (neue Karten verfälschen die Steigerung nicht).
SQL_VERAENDERUNG = """
SELECT SUM(k.anzahl * {jetzt}) AS jetzt, SUM(k.anzahl * {frueher}) AS frueher
FROM karte k
JOIN kartendaten d ON d.scryfall_id = k.scryfall_id
JOIN preis p ON p.cardmarket_id = d.cardmarket_id AND p.tag = ?
JOIN preis q ON q.cardmarket_id = d.cardmarket_id AND q.tag = ?
WHERE k.nutzer_id = ? AND {jetzt} IS NOT NULL AND {frueher} IS NOT NULL {bedingung}
"""


def vergleichstage(con) -> dict[str, str | None]:
    """{"vortag"|"tage3"|"tage30": Tag des Vergleichs oder None} zum neuesten Tag (Toleranzen wie ABSTAENDE)."""
    tag = neuester_tag(con)
    if tag is None:
        return {name: None for name in ABSTAENDE}
    tage = [z[0] for z in con.execute("SELECT tag FROM preisabruf WHERE tag < ? ORDER BY tag", (tag,))]
    return {name: _naechster_tag(tage, date.fromisoformat(tag) - timedelta(days=abstand), toleranz)
            for name, (abstand, toleranz) in ABSTAENDE.items()}


def veraenderung_gesamt(con, nutzer: int, preisart: str | None = None, deck_id: int | None = None) -> dict[str, dict]:
    """{"vortag"|"tage3"|"tage30": {"euro": …, "prozent": …, "tag": …}}; ohne passenden früheren Tag None-Werte.
    Mit `deck_id` nur die Karten, die in diesem Deck liegen (ab Version 8)."""
    preisart = aufloesen(con, preisart, nutzer)
    tag = neuester_tag(con)
    ergebnis = {}
    bedingung, extra = ("AND k.deck_id = ?", (deck_id,)) if deck_id is not None else ("", ())
    for name, frueher_tag in vergleichstage(con).items():
        euro = prozent = None
        if frueher_tag is not None:
            sql = SQL_VERAENDERUNG.format(jetzt=preis_ausdruck(preisart, "p"), frueher=preis_ausdruck(preisart, "q"),
                                          bedingung=bedingung)
            z = con.execute(sql, (tag, frueher_tag, nutzer, *extra)).fetchone()
            if z is not None and z[0] is not None and z[1] is not None:
                euro = round(z[0] - z[1], 2)
                prozent = _prozent(z[0], z[1])
        ergebnis[name] = {"euro": euro, "prozent": prozent, "tag": frueher_tag}
    return ergebnis


# Drucke zusammengefasst (gleicher Druck und Foil an mehreren Orten zählt einmal, mit Summe der Anzahl)
SQL_TEUERSTE = """
SELECT MIN(k.name) AS name, k.set_code, k.nummer, k.foil, SUM(k.anzahl) AS anzahl, {preis} AS preis,
       MIN(k.id) AS karte_id, k.scryfall_id, MIN(COALESCE(k.bild, d.bild)) AS bild
FROM karte k
JOIN kartendaten d ON d.scryfall_id = k.scryfall_id
JOIN preis p ON p.cardmarket_id = d.cardmarket_id AND p.tag = ?
WHERE k.nutzer_id = ? AND {preis} IS NOT NULL
GROUP BY k.scryfall_id, k.foil
ORDER BY preis DESC, name LIMIT ?
"""
SQL_AENDERUNG_KARTEN = """
SELECT MIN(k.name) AS name, k.set_code, k.nummer, k.foil, SUM(k.anzahl) AS anzahl,
       {jetzt} AS preis, {frueher} AS preis_vorher, SUM(k.anzahl) * ({jetzt} - {frueher}) AS euro,
       MIN(k.id) AS karte_id, k.scryfall_id, MIN(COALESCE(k.bild, d.bild)) AS bild
FROM karte k
JOIN kartendaten d ON d.scryfall_id = k.scryfall_id
JOIN preis p ON p.cardmarket_id = d.cardmarket_id AND p.tag = ?
JOIN preis q ON q.cardmarket_id = d.cardmarket_id AND q.tag = ?
WHERE k.nutzer_id = ? AND {jetzt} IS NOT NULL AND {frueher} IS NOT NULL
GROUP BY k.scryfall_id, k.foil
HAVING euro {vergleich} 0
ORDER BY euro {richtung}, name LIMIT ?
"""


def _karte_eintrag(z, mit_bild: bool = False) -> dict:
    eintrag = {"name": z["name"], "set": (z["set_code"] or "").upper(), "nummer": z["nummer"], "foil": bool(z["foil"]),
               "anzahl": z["anzahl"], "preis": round(z["preis"], 2)}
    if mit_bild:   # für die Oberfläche; an Home Assistant gehen Bildschlüssel nicht
        eintrag.update(karte_id=z["karte_id"], scryfall_id=z["scryfall_id"], bild=z["bild"])
    return eintrag


def teuerste(con, nutzer: int, anzahl: int = 5, preisart: str | None = None, mit_bild: bool = False) -> list[dict]:
    """Die `anzahl` Drucke mit dem höchsten Einzelpreis (neuester Tag)."""
    preisart = aufloesen(con, preisart, nutzer)
    tag = neuester_tag(con)
    if tag is None:
        return []
    sql = SQL_TEUERSTE.format(preis=preis_ausdruck(preisart))
    return [_karte_eintrag(z, mit_bild) for z in con.execute(sql, (tag, nutzer, anzahl))]


def groesste_aenderung(con, nutzer: int, anzahl: int = 5, preisart: str | None = None, abstand: str = "vortag",
                       mit_bild: bool = False) -> dict[str, list[dict]]:
    """Gewinner und Verlierer in Euro zum früheren Tag `abstand` (Schlüssel aus ABSTAENDE, Anzahl × Preisänderung):
    {"gewinner": […], "verlierer": […]}."""
    preisart = aufloesen(con, preisart, nutzer)
    tag, frueher_tag = neuester_tag(con), vergleichstage(con)[abstand]
    if tag is None or frueher_tag is None:
        return {"gewinner": [], "verlierer": []}
    ergebnis = {}
    for liste, vergleich, richtung in (("gewinner", ">", "DESC"), ("verlierer", "<", "ASC")):
        sql = SQL_AENDERUNG_KARTEN.format(jetzt=preis_ausdruck(preisart, "p"), frueher=preis_ausdruck(preisart, "q"),
                                          vergleich=vergleich, richtung=richtung)
        ergebnis[liste] = [{**_karte_eintrag(z, mit_bild), "preis_vorher": round(z["preis_vorher"], 2),
                            "euro": round(z["euro"], 2), "prozent": _prozent(z["preis"], z["preis_vorher"])}
                           for z in con.execute(sql, (tag, frueher_tag, nutzer, anzahl))]
    return ergebnis
