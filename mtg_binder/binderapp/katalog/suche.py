"""Namens- und Drucksuche im Katalog (für die Prüfansicht, nur PC).

`namen` findet Karten über englische und gedruckte (zum Beispiel deutsche) Namen: erst Treffer
am Namensanfang, dann Treffer im Namensteil, Groß- und Kleinschreibung, Akzente und
Satzzeichen spielen keine Rolle. `drucke` listet alle Drucke einer Karte, neueste zuerst.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata

_ERSATZ = str.maketrans({"æ": "ae", "œ": "oe", "ß": "ss", "ø": "o", "đ": "d", "ł": "l", "ı": "i"})
_APOSTROPHE = re.compile(r"[’'`´ʼ]")
_NICHT_WORT = re.compile(r"[\W_]+")


def normalisiere(text: str | None) -> str:
    """Suchform eines Namens: klein, ohne Akzente und Apostrophe, Satzzeichen als Leerzeichen.

    Beispiele: „Jetmir’s Second“ → „jetmirs second“, „Mäuse“ → „mause“, „Æther“ → „aether“.
    """
    if not text:
        return ""
    zerlegt = unicodedata.normalize("NFKD", text.casefold())
    ohne_akzente = "".join(z for z in zerlegt if not unicodedata.combining(z))
    ersetzt = _APOSTROPHE.sub("", ohne_akzente.translate(_ERSATZ))
    return _NICHT_WORT.sub(" ", ersetzt).strip()


def namen(kat: sqlite3.Connection, text: str, grenze: int = 20) -> list[dict]:
    """Karten zum Suchtext: Namensanfang vor Namensteil, je Karte (Oracle-ID) ein Treffer.

    Jeder Treffer: `oracle_id`, `name` (englischer Name), `treffer` (der gefundene Name, bei
    Treffern über einen gedruckten Namen dieser), `sprache` (des gefundenen Namens), `art`
    (`anfang` oder `teil`). Innerhalb einer Art kommen echte Karten vor Spielmarken und Kunstkarten,
    dann der genaue Treffer, dann kürzere Namen. Bei einem einzelnen Buchstaben gilt nur die
    alphabetische Reihenfolge echter Karten (sonst zu langsam, über 150 ms).
    """
    suche = normalisiere(text)
    if not suche or grenze <= 0:
        return []
    ergebnis: list[dict] = []
    gesehen: set[str] = set()
    # Eine Karte hat höchstens etwa zehn Namenszeilen (Englisch, Sprachen, Seiten).
    holen = grenze * 12

    def aufnehmen(zeilen, art: str) -> None:
        for oracle_id, englisch, treffer, sprache in zeilen:
            if oracle_id in gesehen:
                continue
            gesehen.add(oracle_id)
            ergebnis.append(
                {"oracle_id": oracle_id, "name": englisch, "treffer": treffer, "sprache": sprache, "art": art}
            )
            if len(ergebnis) >= grenze:
                return

    if len(suche) == 1:
        # Ein einzelner Buchstabe trifft Zehntausende Namen; die Rangfolge nach Länge wäre zu langsam.
        # Hier gilt die Reihenfolge des Index (alphabetisch), nur echte Karten.
        anfang = kat.execute(
            "SELECT n.oracle_id, o.name, n.name, n.sprache FROM namen n JOIN orakel o USING (oracle_id) "
            "WHERE n.norm >= ? AND n.norm < ? AND o.rang = 0 ORDER BY n.norm, n.oracle_id LIMIT ?",
            (suche, suche + "￿", holen),
        )
    else:
        anfang = kat.execute(
            "SELECT n.oracle_id, o.name, n.name, n.sprache FROM namen n JOIN orakel o USING (oracle_id) "
            "WHERE n.norm >= ? AND n.norm < ? "
            "ORDER BY o.rang, (n.norm = ?) DESC, length(n.norm), (n.sprache = 'en') DESC, n.norm, n.oracle_id "
            "LIMIT ?",
            (suche, suche + "￿", suche, holen),
        )
    aufnehmen(anfang, "anfang")
    if len(ergebnis) < grenze:
        aufnehmen(
            kat.execute(
                "SELECT n.oracle_id, o.name, n.name, n.sprache FROM namen n JOIN orakel o USING (oracle_id) "
                "WHERE instr(n.norm, ?) > 1 "
                # Treffer am Wortanfang vor Treffern mitten im Wort.
                "ORDER BY o.rang, (instr(' ' || n.norm, ' ' || ?) > 0) DESC, length(n.norm), "
                "(n.sprache = 'en') DESC, n.norm, n.oracle_id LIMIT ?",
                (suche, suche, holen),
            ),
            "teil",
        )
    return ergebnis


def drucke(kat: sqlite3.Connection, oracle_id: str) -> list[dict]:
    """Alle Drucke einer Karte (Oracle-ID), neueste zuerst.

    Jeder Druck: `scryfall_id`, `set_code`, `set_name`, `set_erschienen` (Datum des Sets),
    `erschienen` (Datum des Drucks), `nummer`, `sprache`, `seltenheit`, `bild_vorhanden` (bool).
    Sortierung: Erscheinungsdatum des Drucks absteigend, dann Set, dann Nummer.
    """
    cur = kat.execute(
        "SELECT scryfall_id, set_code, set_name, set_erschienen, erschienen, nummer, sprache, "
        "seltenheit, bild_vorhanden FROM druck WHERE oracle_id = ? "
        "ORDER BY erschienen DESC, set_code, nummer_zahl, nummer, scryfall_id",
        (oracle_id,),
    )
    spalten = [s[0] for s in cur.description]
    liste = []
    for zeile in cur.fetchall():
        eintrag = dict(zip(spalten, zeile))
        eintrag["bild_vorhanden"] = bool(eintrag["bild_vorhanden"])
        liste.append(eintrag)
    return liste
