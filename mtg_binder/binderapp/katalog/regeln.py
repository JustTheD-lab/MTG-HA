"""Regeldaten je Karte im Katalog: Legalität in allen Formaten, Game Changer, Merkmale für den Deckbau und die
Scryfall-Tags für Extrazüge und Massen-Landzerstörung (Commander-Bewertung, Auftrag des Nutzers, 20.11.2026).

Die Tabelle `regel` (eine Zeile je `oracle_id`) ist eine Ergänzung des Katalogs ohne neue Katalogversion: Fehlt sie
oder ist sie unvollständig, legt das Add-on sie an und füllt sie selbst von Scryfall (`auffrischen`). So bricht
kein älterer Katalog, und der PC muss keinen neuen schicken. Gefüllt wird sie

- am PC beim Bauen (`bau.py`, aus den Scryfall-Daten),
- beim täglichen Abgleich für neue und aufgefrischte Drucke (`abgleich.schreiben`; an den PC über die Neuheiten
  mit dem Schlüssel `binder_regel`, weil die gekürzten Karten keine Legalität und keinen Regeltext enthalten),
- im Add-on durch `auffrischen`: fehlende Karten über `/cards/collection` (nach Oracle-ID), danach täglich die
  Suchen nach Banns je Format, Game Changern und den beiden Tags. Jede Suche ersetzt die ganze Menge; eine leere
  Antwort bei Game Changern oder Tags gilt als Fehler (Tag umbenannt) und ändert nichts.

Wechsel von legal zu nicht legal (Rotation) kommen nur mit neu geholten Karten oder einem neuen Katalog vom PC
(Antwort des Nutzers, 20.11.2026). Nur Standardbibliothek (Python 3.11).
"""
from __future__ import annotations

import json
import logging
import re
import urllib.parse
from datetime import datetime, timezone

from binderapp.katalog.bau import oracle_von

log = logging.getLogger("binderapp.katalog")

# Reihenfolge der Formate im Text `legal` (ein Buchstabe je Format); neue Formate von Scryfall hinten anfügen
FORMATE = ("standard", "future", "historic", "timeless", "gladiator", "pioneer", "modern", "legacy", "pauper",
           "vintage", "penny", "commander", "oathbreaker", "standardbrawl", "brawl", "competitivebrawl", "alchemy",
           "paupercommander", "duel", "oldschool", "premodern", "predh")
KURZ = {"legal": "l", "not_legal": "n", "banned": "b", "restricted": "r"}
LANG = {v: k for k, v in KURZ.items()}
FORMAT_NAMEN = {
    "standard": "Standard", "future": "Future Standard", "historic": "Historic", "timeless": "Timeless",
    "gladiator": "Gladiator", "pioneer": "Pioneer", "modern": "Modern", "legacy": "Legacy", "pauper": "Pauper",
    "vintage": "Vintage", "penny": "Penny Dreadful", "commander": "Commander", "oathbreaker": "Oathbreaker",
    "standardbrawl": "Standard Brawl", "brawl": "Brawl", "competitivebrawl": "Competitive Brawl",
    "alchemy": "Alchemy", "paupercommander": "Pauper Commander", "duel": "Duel Commander", "oldschool": "Old School",
    "premodern": "Premodern", "predh": "PreDH"}

TABELLE = """CREATE TABLE IF NOT EXISTS regel (
    oracle_id       TEXT PRIMARY KEY,
    legal           TEXT NOT NULL,                -- ein Buchstabe je Format in der Reihenfolge von FORMATE (l, n, b, r)
    game_changer    INTEGER NOT NULL DEFAULT 0,
    extrazug        INTEGER NOT NULL DEFAULT 0,   -- Scryfall-Tag extra-turn
    landzerstoerung INTEGER NOT NULL DEFAULT 0,   -- Scryfall-Tag mass-land-denial
    merkmale        TEXT NOT NULL DEFAULT '[]'    -- JSON-Liste (kann_commander, partner, beliebig, hoechstens:7 …)
)"""

# Suchen der täglichen Auffrischung (Scryfall-Tags geprüft am 20.11.2026: 111 und 59 Karten)
TAG_LANDZERSTOERUNG = "otag:mass-land-denial"
TAG_EXTRAZUG = "otag:extra-turn"
SUCHE_GAME_CHANGER = "is:gamechanger"
ORAKEL_JE_ANFRAGE = 75
BLOCK = 2000
HOECHSTENS_SUCHSEITEN = 40

ZAHLWOERTER = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
               "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15}
RE_PARTNER_MIT = re.compile(r"^Partner with ([^(]+?)\s*(?:\(|$)")
RE_PARTNER = re.compile(r"^Partner(?:\s*[—-]\s*([^(]+?))?\s*(?:\(|$)")
RE_HOECHSTENS = re.compile(r"A deck can have up to (\w+) cards named", re.IGNORECASE)


# ----------------------------------------------------------------- aus einer Scryfall-Karte


def _texte(karte: dict) -> list[str]:
    texte = [karte.get("oracle_text")] + [s.get("oracle_text") for s in karte.get("card_faces") or []
                                          if isinstance(s, dict)]
    return [t for t in texte if isinstance(t, str) and t]


def _vorderseite_typ(karte: dict) -> str:
    seiten = [s for s in karte.get("card_faces") or [] if isinstance(s, dict)]
    if seiten and seiten[0].get("type_line"):
        return str(seiten[0]["type_line"])
    return str(karte.get("type_line") or "").split(" // ")[0]


def merkmale_aus_karte(karte: dict) -> list[str]:
    """Merkmale für den Commander-Deckbau aus Typzeile und Regeltext (englisch, Oracle)."""
    aus: list[str] = []
    typ = _vorderseite_typ(karte)
    if "Legendary" in typ and "Creature" in typ:
        aus.append("kann_commander")
    if re.search(r"\bBackground\b", typ):
        aus.append("background")
    if "Time Lord Doctor" in typ:
        aus.append("doktor")
    for text in _texte(karte):
        for zeile in text.splitlines():
            z = zeile.strip()
            m = RE_PARTNER_MIT.match(z)
            if m:
                aus.append(f"partner_mit:{m.group(1).strip()}")
            else:
                m = RE_PARTNER.match(z)
                if m:
                    aus.append(f"partner_gruppe:{m.group(1).strip()}" if m.group(1) else "partner")
            if z.startswith("Friends forever"):
                aus.append("freunde")
            if z.startswith("Choose a Background"):
                aus.append("background_waehlen")
            if z.startswith("Doctor's companion"):
                aus.append("doktor_begleiter")
            if "can be your commander" in z:
                aus.append("kann_commander")
            if "A deck can have any number of cards named" in z:
                aus.append("beliebig")
            m = RE_HOECHSTENS.search(z)
            if m:
                wort = m.group(1).lower()
                zahl = int(wort) if wort.isdigit() else ZAHLWOERTER.get(wort)
                if zahl:
                    aus.append(f"hoechstens:{zahl}")
    return list(dict.fromkeys(aus))


def regel_aus_karte(karte: dict) -> dict | None:
    """Regelzeile (ohne Tags) aus einer vollständigen Scryfall-Karte oder einer gekürzten mit `binder_regel`."""
    oracle = oracle_von(karte)
    if not oracle:
        return None
    fertig = karte.get("binder_regel")
    if isinstance(fertig, dict):
        legal = fertig.get("legal")
        merkmale = fertig.get("merkmale")
        if isinstance(legal, str) and re.fullmatch(r"[lnbr]*", legal) and isinstance(merkmale, list):
            return {"oracle_id": oracle, "legal": legal[:len(FORMATE)].ljust(len(FORMATE), "n"),
                    "game_changer": 1 if fertig.get("game_changer") else 0,
                    "merkmale": [str(m)[:200] for m in merkmale if isinstance(m, str)][:20]}
        return None
    leg = karte.get("legalities")
    if not isinstance(leg, dict):
        return None
    return {"oracle_id": oracle, "legal": "".join(KURZ.get(leg.get(f), "n") for f in FORMATE),
            "game_changer": 1 if karte.get("game_changer") else 0, "merkmale": merkmale_aus_karte(karte)}


def fuer_neuheit(karte: dict) -> dict | None:
    """Das, was die gekürzte Karte für den PC unter `binder_regel` mitnimmt."""
    r = regel_aus_karte(karte)
    return None if r is None else {"legal": r["legal"], "game_changer": r["game_changer"], "merkmale": r["merkmale"]}


# ----------------------------------------------------------------- Katalog


def hat_tabelle(kat) -> bool:
    return kat.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'regel'").fetchone() is not None


def schreiben(kat, regeln: list[dict]) -> int:
    """Schreibt Regelzeilen (in der Transaktion des Aufrufers); die Tags bleiben, wie sie sind."""
    kat.execute(TABELLE)
    kat.executemany(
        "INSERT INTO regel (oracle_id, legal, game_changer, merkmale) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(oracle_id) DO UPDATE SET legal = excluded.legal, game_changer = excluded.game_changer, "
        "merkmale = excluded.merkmale",
        [(r["oracle_id"], r["legal"], r["game_changer"], json.dumps(r["merkmale"], ensure_ascii=False))
         for r in regeln])
    return len(regeln)


def lesen(kat, oracle_ids) -> dict[str, dict]:
    """Regeln je Oracle-ID: Status in Commander, alle Formate, Game Changer, Tags, Merkmale, dazu Farbidentität und
    Typzeile aus einem Druck. Ohne Tabelle oder unbekannt: fehlt im Ergebnis."""
    ids = [i for i in dict.fromkeys(oracle_ids) if i]
    if not ids or not hat_tabelle(kat):
        return {}
    aus: dict[str, dict] = {}
    for anfang in range(0, len(ids), 500):
        teil = ids[anfang:anfang + 500]
        platz = ", ".join("?" for _ in teil)
        for oracle, legal, gc, extra, land, merkmale in kat.execute(
                f"SELECT oracle_id, legal, game_changer, extrazug, landzerstoerung, merkmale FROM regel "
                f"WHERE oracle_id IN ({platz})", teil):
            try:
                liste = json.loads(merkmale)
            except ValueError:
                liste = []
            formate = {f: LANG.get(legal[i], "not_legal") for i, f in enumerate(FORMATE) if i < len(legal)}
            aus[oracle] = {"commander": formate.get("commander", "not_legal"), "formate": formate,
                           "game_changer": bool(gc), "extrazug": bool(extra), "landzerstoerung": bool(land),
                           "merkmale": liste if isinstance(liste, list) else [],
                           "farbidentitaet": "", "typzeile": ""}
        for oracle, ident, typ in kat.execute(
                f"SELECT oracle_id, MAX(farbidentitaet), MAX(typzeile) FROM druck WHERE oracle_id IN ({platz}) "
                f"GROUP BY oracle_id", teil):
            if oracle in aus:
                aus[oracle]["farbidentitaet"] = ident or ""
                aus[oracle]["typzeile"] = typ or ""
    return aus


def fehlende(kat) -> list[str]:
    """Oracle-IDs echter Karten (Rang 0) ohne Regelzeile."""
    if not hat_tabelle(kat):
        return [z[0] for z in kat.execute("SELECT oracle_id FROM orakel WHERE rang = 0")]
    return [z[0] for z in kat.execute(
        "SELECT o.oracle_id FROM orakel o LEFT JOIN regel r USING (oracle_id) WHERE o.rang = 0 AND r.oracle_id IS NULL")]


# ----------------------------------------------------------------- Add-on: von Scryfall auffrischen


def _suche(scryfall, anfrage: str) -> set[str] | None:
    """Oracle-IDs aller Treffer einer Scryfall-Suche; leere Menge bei „keine Treffer“ (404), None bei zu vielen
    Seiten."""
    from binderapp.katalog.abgleich import API, AbgleichFehler
    url = f"{API}/cards/search?" + urllib.parse.urlencode({"q": anfrage, "unique": "cards"})
    aus: set[str] = set()
    for _ in range(HOECHSTENS_SUCHSEITEN):
        try:
            antwort = scryfall.anfrage(url)
        except AbgleichFehler as f:
            if "mit 404" in str(f):
                return aus
            raise
        for k in antwort.get("data") or []:
            if isinstance(k, dict):
                o = oracle_von(k)
                if isinstance(o, str):
                    aus.add(o)
        if not antwort.get("has_more"):
            return aus
        url = antwort.get("next_page") or ""
        if not url.startswith(API + "/cards/search?"):
            return aus
    return None


def _karten_nach_orakel(scryfall, ids: list[str]) -> list[dict]:
    from binderapp.katalog.abgleich import API
    aus = []
    for anfang in range(0, len(ids), ORAKEL_JE_ANFRAGE):
        teil = ids[anfang:anfang + ORAKEL_JE_ANFRAGE]
        antwort = scryfall.anfrage(f"{API}/cards/collection", {"identifiers": [{"oracle_id": i} for i in teil]})
        aus += [k for k in antwort.get("data") or [] if isinstance(k, dict)]
    return aus


def _menge_setzen(kat, spalte: str, menge: set[str]) -> int:
    """Setzt eine Ja/Nein-Spalte auf genau die Menge (in der Transaktion des Aufrufers); Zahl der Änderungen."""
    if spalte not in ("game_changer", "extrazug", "landzerstoerung"):
        raise ValueError(spalte)
    alt = {z[0] for z in kat.execute(f"SELECT oracle_id FROM regel WHERE {spalte} = 1")}
    an, aus = menge - alt, alt - menge
    kat.executemany(f"UPDATE regel SET {spalte} = 1 WHERE oracle_id = ?", [(o,) for o in an])
    kat.executemany(f"UPDATE regel SET {spalte} = 0 WHERE oracle_id = ?", [(o,) for o in aus])
    return len(an) + len(aus)


def _status_setzen(kat, format_: str, buchstabe: str, menge: set[str]) -> int:
    """Banns (b) oder Einschränkungen (r) eines Formats auf genau die Menge setzen; wer herausfällt, ist legal
    (eine gebannte Karte gehört zum Kartenpool des Formats)."""
    i = FORMATE.index(format_)
    aenderungen = []
    for oracle, legal in kat.execute("SELECT oracle_id, legal FROM regel"):
        if i >= len(legal):
            continue
        jetzt = legal[i]
        soll = buchstabe if oracle in menge else ("l" if jetzt == buchstabe else jetzt)
        if soll != jetzt:
            aenderungen.append((legal[:i] + soll + legal[i + 1:], oracle))
    kat.executemany("UPDATE regel SET legal = ? WHERE oracle_id = ?", aenderungen)
    return len(aenderungen)


def auffrischen(pfad, scryfall=None, transaktion=None) -> dict:
    """Add-on: fehlende Regelzeilen nachholen, dann Banns, Game Changer und Tags auf den Stand von Scryfall bringen.
    Wirft `AbgleichFehler`, wenn Scryfall nicht erreichbar ist (bis dahin Geschriebenes bleibt)."""
    import sqlite3
    from contextlib import closing
    from pathlib import Path

    from binderapp.katalog.abgleich import AbgleichFehler, Scryfall, _transaktion
    transaktion = transaktion or _transaktion
    scryfall = scryfall or Scryfall()
    pfad = Path(pfad)
    transaktion(pfad, lambda kat: kat.execute(TABELLE))
    with closing(sqlite3.connect(f"{pfad.resolve().as_uri()}?mode=ro", uri=True)) as kat:
        offen = fehlende(kat)
    zahl = {"regeln_neu": 0, "aenderungen": 0}
    for anfang in range(0, len(offen), BLOCK):
        karten = _karten_nach_orakel(scryfall, offen[anfang:anfang + BLOCK])
        regeln = [r for r in (regel_aus_karte(k) for k in karten) if r]
        zahl["regeln_neu"] += transaktion(pfad, lambda kat, r=regeln: schreiben(kat, r))

    mengen: dict[tuple[str, str], set[str]] = {}
    for spalte, anfrage in (("game_changer", SUCHE_GAME_CHANGER), ("extrazug", TAG_EXTRAZUG),
                            ("landzerstoerung", TAG_LANDZERSTOERUNG)):
        menge = _suche(scryfall, anfrage)
        if not menge:   # leer oder zu viele Seiten: lieber den alten Stand behalten
            log.warning("Regeln: Suche %s ohne brauchbares Ergebnis, Stand bleibt", anfrage)
            continue
        mengen[("spalte", spalte)] = menge
    for f in FORMATE:
        menge = _suche(scryfall, f"banned:{f}")
        if menge is not None:
            mengen[("b", f)] = menge
    menge = _suche(scryfall, "restricted:vintage")
    if menge is not None:
        mengen[("r", "vintage")] = menge

    def arbeit(kat):
        n = 0
        for (art, name), menge in mengen.items():
            n += _menge_setzen(kat, name, menge) if art == "spalte" else _status_setzen(kat, name, art, menge)
        kat.executemany("INSERT OR REPLACE INTO meta (schluessel, wert) VALUES (?, ?)", [
            ("regel_auffrischen", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")),
            ("zeilen_regel", str(kat.execute("SELECT COUNT(*) FROM regel").fetchone()[0]))])
        return n

    zahl["aenderungen"] = transaktion(pfad, arbeit)
    if not mengen:
        raise AbgleichFehler("Regeln: keine Suche bei Scryfall hat geantwortet.")
    return zahl
