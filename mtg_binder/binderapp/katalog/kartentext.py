"""Englischer Kartentext (Oracle) je Karte im Katalog: für die Kartenansicht und die Scryfall-Suche in der Sammlung
(Wunsch des Nutzers, 20.11.2026: Regeltext, Stärke und Widerstand bzw. Loyalität, bei mehrseitigen Karten jede Seite).

Die Tabelle `kartentext` (eine Zeile je `oracle_id`) ist wie `regel` eine Ergänzung des Katalogs ohne neue
Katalogversion. Gefüllt wird sie

- am PC beim Bauen (`bau.py`, aus den Scryfall-Daten),
- beim täglichen Abgleich für neue und aufgefrischte Drucke (`abgleich.schreiben`; an den PC über die Neuheiten mit
  dem Schlüssel `binder_text`, weil die gekürzten Karten keinen Regeltext enthalten),
- im Add-on einmal durch `nachholen`: alle Karten ohne Text über `/cards/collection` (nach Oracle-ID). Danach steht
  in `meta` der Schlüssel `kartentext_nachgeholt`, und nur noch der Abgleich schreibt neue Texte.

Nur Standardbibliothek (Python 3.11).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from binderapp.katalog.bau import oracle_von

log = logging.getLogger("binderapp.katalog")

TABELLE = """CREATE TABLE IF NOT EXISTS kartentext (
    oracle_id          TEXT PRIMARY KEY,
    name               TEXT NOT NULL,     -- englischer Name (mehrseitig: „A // B“)
    seiten             TEXT NOT NULL,     -- JSON: [{name, manakosten, typzeile, text, staerke, widerstand, loyalitaet,
                                          --          verteidigung}], eine Seite je Kartenseite
    text               TEXT NOT NULL,     -- Regeltext aller Seiten ohne Erinnerungstext (Suche o:)
    volltext           TEXT NOT NULL,     -- Regeltext aller Seiten mit Erinnerungstext (Suche fo:)
    typen              TEXT NOT NULL,     -- Typzeilen aller Seiten (Suche t:)
    schluesselwoerter  TEXT NOT NULL,     -- „|flying|trample|“ klein (Suche kw:)
    staerke            TEXT,              -- erste Seite mit Wert (Suche pow:, tou:, loy:)
    widerstand         TEXT,
    loyalitaet         TEXT
)"""

FELDER = (("name", "name"), ("manakosten", "mana_cost"), ("typzeile", "type_line"), ("text", "oracle_text"),
          ("staerke", "power"), ("widerstand", "toughness"), ("loyalitaet", "loyalty"), ("verteidigung", "defense"))
LAENGE = 4000          # so lang höchstens ein Feld (der längste Oracle-Text hat gut 1.000 Zeichen)
SEITEN_MAX = 4
BLOCK = 2000
RE_ERINNERUNG = re.compile(r"\s*\([^()]*\)")


def _text(wert) -> str | None:
    return str(wert)[:LAENGE] if isinstance(wert, (str, int, float)) and str(wert) != "" else None


def seiten_aus_karte(karte: dict) -> list[dict]:
    """Seiten einer vollständigen Scryfall-Karte (ohne `card_faces`: die Karte selbst als eine Seite)."""
    roh = [s for s in karte.get("card_faces") or [] if isinstance(s, dict)] or [karte]
    seiten = []
    for s in roh[:SEITEN_MAX]:
        seite = {ziel: _text(s.get(quelle)) for ziel, quelle in FELDER}
        if seite["name"]:
            seiten.append(seite)
    return seiten


def zeile_aus_seiten(oracle: str, name: str, seiten: list[dict], schluesselwoerter: list[str],
                     typen: str | None = None) -> dict:
    texte = [s["text"] for s in seiten if s.get("text")]
    wert = lambda feld: next((s[feld] for s in seiten if s.get(feld)), None)   # noqa: E731
    return {
        "oracle_id": oracle, "name": name[:LAENGE], "seiten": seiten,
        "text": "\n".join(RE_ERINNERUNG.sub("", t).strip() for t in texte),
        "volltext": "\n".join(texte),
        "typen": (typen or " // ".join(s["typzeile"] for s in seiten if s.get("typzeile")))[:LAENGE],
        "schluesselwoerter": "|" + "".join(f"{w.lower()}|" for w in schluesselwoerter),
        "staerke": wert("staerke"), "widerstand": wert("widerstand"), "loyalitaet": wert("loyalitaet"),
    }


def aus_karte(karte: dict) -> dict | None:
    """Textzeile aus einer vollständigen Scryfall-Karte oder einer gekürzten mit `binder_text`."""
    oracle = oracle_von(karte)
    if not isinstance(oracle, str) or not oracle:
        return None
    name = karte.get("name")
    if not isinstance(name, str) or not name:
        return None
    fertig = karte.get("binder_text")
    if fertig is not None:
        if not isinstance(fertig, dict) or not isinstance(fertig.get("seiten"), list):
            return None
        seiten = []
        for s in fertig["seiten"][:SEITEN_MAX]:
            if isinstance(s, dict):
                seite = {ziel: _text(s.get(ziel)) for ziel, _ in FELDER}
                if seite["name"]:
                    seiten.append(seite)
        worte = [str(w)[:60] for w in fertig.get("schluesselwoerter") or [] if isinstance(w, str)][:40]
        return zeile_aus_seiten(oracle, name, seiten, worte) if seiten else None
    if "oracle_text" not in karte and not any(isinstance(s, dict) and "oracle_text" in s
                                              for s in karte.get("card_faces") or []):
        return None   # gekürzte Karte ohne Text: nichts überschreiben
    seiten = seiten_aus_karte(karte)
    if not seiten:
        return None
    worte = [str(w)[:60] for w in karte.get("keywords") or [] if isinstance(w, str)][:40]
    return zeile_aus_seiten(oracle, name, seiten, worte, _text(karte.get("type_line")))


def fuer_neuheit(karte: dict) -> dict | None:
    """Das, was die gekürzte Karte für den PC unter `binder_text` mitnimmt."""
    z = aus_karte(karte)
    if z is None:
        return None
    return {"seiten": z["seiten"],
            "schluesselwoerter": [w for w in z["schluesselwoerter"].split("|") if w]}


# ----------------------------------------------------------------- Katalog


def hat_tabelle(kat) -> bool:
    return kat.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'kartentext'").fetchone() is not None


def schreiben(kat, zeilen: list[dict]) -> int:
    """Schreibt Textzeilen (in der Transaktion des Aufrufers)."""
    kat.execute(TABELLE)
    kat.executemany(
        "INSERT OR REPLACE INTO kartentext (oracle_id, name, seiten, text, volltext, typen, schluesselwoerter, "
        "staerke, widerstand, loyalitaet) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(z["oracle_id"], z["name"], json.dumps(z["seiten"], ensure_ascii=False), z["text"], z["volltext"],
          z["typen"], z["schluesselwoerter"], z["staerke"], z["widerstand"], z["loyalitaet"]) for z in zeilen])
    return len(zeilen)


def lesen(kat, oracle_id: str | None) -> list[dict] | None:
    """Seiten einer Karte für die Kartenansicht; None ohne Tabelle oder ohne Zeile."""
    if not oracle_id or kat is None or not hat_tabelle(kat):
        return None
    z = kat.execute("SELECT seiten FROM kartentext WHERE oracle_id = ?", (oracle_id,)).fetchone()
    if z is None:
        return None
    try:
        seiten = json.loads(z[0])
    except ValueError:
        return None
    return [s for s in seiten if isinstance(s, dict)] if isinstance(seiten, list) else None


def fehlende(kat) -> list[str]:
    """Oracle-IDs ohne Textzeile (echte Karten zuerst, dann Spielmarken und Embleme)."""
    if not hat_tabelle(kat):
        return [z[0] for z in kat.execute("SELECT oracle_id FROM orakel ORDER BY rang")]
    return [z[0] for z in kat.execute(
        "SELECT o.oracle_id FROM orakel o LEFT JOIN kartentext t USING (oracle_id) "
        "WHERE t.oracle_id IS NULL ORDER BY o.rang")]


def offen(pfad) -> bool:
    """Add-on: Die Texte wurden in diesem Katalog noch nie nachgeholt (Katalog vom PC ohne Tabelle, nach dem Update)."""
    import sqlite3
    from contextlib import closing
    from pathlib import Path
    if not Path(pfad).is_file():
        return False
    try:
        with closing(sqlite3.connect(f"{Path(pfad).resolve().as_uri()}?mode=ro", uri=True)) as kat:
            z = kat.execute("SELECT wert FROM meta WHERE schluessel = 'kartentext_nachgeholt'").fetchone()
    except sqlite3.Error:
        return False
    return not z


def nachholen(pfad, scryfall=None, transaktion=None, halt=lambda: False) -> dict:
    """Add-on: Texte aller Karten ohne Zeile von Scryfall holen (blockweise, jeder Block eine Transaktion). Wirft
    `AbgleichFehler`, wenn Scryfall nicht erreichbar ist (bis dahin Geschriebenes bleibt, der nächste Lauf macht
    weiter)."""
    import sqlite3
    from contextlib import closing
    from pathlib import Path

    from binderapp.katalog.abgleich import Scryfall, _transaktion
    from binderapp.katalog.regeln import _karten_nach_orakel
    transaktion = transaktion or _transaktion
    scryfall = scryfall or Scryfall()
    pfad = Path(pfad)
    transaktion(pfad, lambda kat: kat.execute(TABELLE))
    with closing(sqlite3.connect(f"{pfad.resolve().as_uri()}?mode=ro", uri=True)) as kat:
        liste = fehlende(kat)
    zahl = {"texte_neu": 0, "offen": len(liste)}
    for anfang in range(0, len(liste), BLOCK):
        if halt():
            return zahl
        karten = _karten_nach_orakel(scryfall, liste[anfang:anfang + BLOCK])
        zeilen = [z for z in (aus_karte(k) for k in karten) if z]
        zahl["texte_neu"] += transaktion(pfad, lambda kat, z=zeilen: schreiben(kat, z))

    def fertig(kat):
        kat.executemany("INSERT OR REPLACE INTO meta (schluessel, wert) VALUES (?, ?)", [
            ("kartentext_nachgeholt", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")),
            ("zeilen_kartentext", str(kat.execute("SELECT COUNT(*) FROM kartentext").fetchone()[0]))])
    transaktion(pfad, fertig)
    return zahl
