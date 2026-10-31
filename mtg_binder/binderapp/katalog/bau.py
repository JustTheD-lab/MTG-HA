"""Baut den Katalog `data/katalog.sqlite` aus den lokalen Scryfall-Daten (nur PC, nicht Raspberry Pi).

Aufruf (im Projektordner, mit PYTHONPATH=src):
    python -m binderapp.katalog.bau [--quelle default_cards.json] [--alle all_cards.json]
                                    [--ziel data/katalog.sqlite] [--bilder data/scryfall/bilder/normal]
                                    [--namenssprachen de,it,fr,es,pt] [--bericht bau.json]

Quellen werden nur gelesen (keine Downloads). Beide Scryfall-Dateien enthalten ein Objekt je Zeile;
sie werden zeilenweise gelesen, nie als Ganzes geladen. Die Dateiendung `.gz` wird erkannt.

Tabellen des Katalogs
- `druck`: ein Druck je Zeile aus `default_cards` (Kartendaten wie in `kartendaten.zeile`, dazu
  `erschienen` = Datum des Drucks, `layout`, `bild_vorhanden` = lokale Bilddatei da; bei
  mehrseitigen Karten zählt `<id>_0.jpg`, siehe `binderapp.katalog.bilddatei`; `cardmarket_id` =
  Produkt bei Cardmarket für die Preise, gilt auch für die Sprachdrucke desselben Sets und derselben Nummer).
- `satz`: Set-Kürzel, Name, frühestes Erscheinungsdatum, Zahl der Drucke.
- `sprachdruck`: schlanke Zuordnung aller Drucke aus `all_cards`, die nicht in `default_cards`
  stehen (vor allem fremdsprachige Drucke): ID → Oracle-ID, Set, Nummer, Sprache, gedruckter Name.
- `orakel` und `namen`: Suchtabellen (englische und gedruckte Namen, normalisiert, siehe `suche`).
- `meta`: Version, Bauzeitpunkt und Zählungen.

Der Katalog entsteht atomar: erst in `<ziel>.tmp`, dann wird die Datei umbenannt.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

from binderapp.katalog import PROJEKT, STANDARD_BILDER, STANDARD_KATALOG, VERSION
from binderapp.katalog.kartendaten import zeile
from binderapp.katalog.suche import normalisiere

STANDARD_QUELLE = PROJEKT / "data" / "scryfall" / "default_cards.json"
STANDARD_ALLE = PROJEKT / "data" / "scryfall" / "all_cards.json"

# Sprachen, deren gedruckte Namen in die Namenssuche kommen (lateinische Schrift).
NAMENSSPRACHEN = ("de", "it", "fr", "es", "pt")
# Layouts, die keine spielbaren Karten sind: in der Namenssuche weiter hinten.
NEBENKARTEN = frozenset({"token", "double_faced_token", "art_series", "emblem"})

SCHEMA = """
CREATE TABLE meta (
    schluessel TEXT PRIMARY KEY,
    wert       TEXT
);
CREATE TABLE druck (
    scryfall_id     TEXT PRIMARY KEY,
    oracle_id       TEXT,
    name            TEXT NOT NULL,              -- englischer Name
    set_code        TEXT NOT NULL,
    set_name        TEXT,
    set_erschienen  TEXT,                       -- frühestes released_at des Sets
    erschienen      TEXT,                       -- released_at des Drucks
    nummer          TEXT NOT NULL,
    nummer_zahl     INTEGER,
    sprache         TEXT NOT NULL,
    manakosten      TEXT,
    manawert        REAL NOT NULL,
    farben          TEXT NOT NULL,
    farbidentitaet  TEXT NOT NULL,
    farbgruppe      TEXT NOT NULL,
    typzeile        TEXT,
    haupttyp        TEXT NOT NULL,
    seltenheit      TEXT,
    layout          TEXT,
    bild_vorhanden  INTEGER NOT NULL,           -- 1, wenn data/scryfall/bilder/normal/<id>.jpg da ist
    cardmarket_id   INTEGER                     -- Produkt bei Cardmarket (Preise; ab Katalog-Version 2)
);
CREATE TABLE satz (
    set_code    TEXT PRIMARY KEY,
    name        TEXT,
    erschienen  TEXT,
    drucke      INTEGER NOT NULL
);
CREATE TABLE sprachdruck (
    scryfall_id    TEXT PRIMARY KEY,
    oracle_id      TEXT,
    set_code       TEXT NOT NULL,
    nummer         TEXT NOT NULL,
    sprache        TEXT NOT NULL,
    name_gedruckt  TEXT
) WITHOUT ROWID;
CREATE TABLE orakel (
    oracle_id  TEXT PRIMARY KEY,
    name       TEXT NOT NULL,                   -- englischer Name
    rang       INTEGER NOT NULL                 -- 0 = Karte, 1 = Spielmarke, Emblem, Kunstkarte
);
CREATE TABLE namen (
    oracle_id  TEXT NOT NULL,
    name       TEXT NOT NULL,                   -- englisch oder gedruckt (eine Seite oder beide)
    sprache    TEXT NOT NULL,
    norm       TEXT NOT NULL                    -- normalisierte Suchform
);
"""

INDIZES = """
CREATE INDEX i_druck_set_nummer ON druck(set_code, nummer);
CREATE INDEX i_druck_oracle     ON druck(oracle_id);
CREATE INDEX i_druck_name       ON druck(name COLLATE NOCASE);
CREATE INDEX i_namen_norm       ON namen(norm);
CREATE INDEX i_namen_oracle     ON namen(oracle_id);
"""

SPALTEN_DRUCK = (
    "scryfall_id", "oracle_id", "name", "set_code", "set_name", "set_erschienen", "erschienen", "nummer",
    "nummer_zahl", "sprache", "manakosten", "manawert", "farben", "farbidentitaet", "farbgruppe",
    "typzeile", "haupttyp", "seltenheit", "layout", "bild_vorhanden", "cardmarket_id",
)


# --------------------------------------------------------------- Lesen


def objekte(pfad: Path):
    """Liefert die Scryfall-Objekte einer Datei mit einem Objekt je Zeile (JSON-Liste oder JSON Lines)."""
    offen = gzip.open if str(pfad).endswith(".gz") else open
    with offen(pfad, "rt", encoding="utf-8") as datei:
        for zeile_ in datei:
            text = zeile_.strip()
            if text.startswith("["):
                text = text[1:]
            if text.endswith("]"):
                text = text[:-1]
            text = text.rstrip(",").strip()
            if text:
                yield json.loads(text)


def bild_ids(ordner: Path | None) -> set[str]:
    """Scryfall-IDs, zu denen eine lokale Bilddatei existiert.

    Einseitige Karten liegen als `<id>.jpg`, mehrseitige als `<id>_0.jpg` (Vorderseite) und
    `<id>_1.jpg` (Rückseite); die Vorderseite zählt als Bild des Drucks.
    """
    if ordner is None or not Path(ordner).is_dir():
        return set()
    ids = set()
    with os.scandir(ordner) as eintraege:
        for e in eintraege:
            if not e.name.endswith(".jpg"):
                continue
            name = e.name[:-4]
            if name.endswith("_0"):
                ids.add(name[:-2])
            elif not name.endswith("_1"):
                ids.add(name)
    return ids


def oracle_von(objekt: dict) -> str | None:
    seiten = objekt.get("card_faces") or []
    return objekt.get("oracle_id") or (seiten[0].get("oracle_id") if seiten else None)


def gedruckter_name(objekt: dict) -> str | None:
    """Gedruckter Name: bei mehrseitigen Karten die Namen der Seiten mit „ // “ verbunden."""
    if objekt.get("printed_name"):
        return objekt["printed_name"]
    seiten = [s.get("printed_name") for s in objekt.get("card_faces") or []]
    if seiten and all(seiten):
        return " // ".join(seiten)
    return None


def _namensvarianten(name: str):
    """Der ganze Name und bei „A // B“ jede Seite einzeln."""
    yield name
    if " // " in name:
        for teil in name.split(" // "):
            if teil and teil != name:
                yield teil


# --------------------------------------------------------------- Bauen


def bauen(
    quelle: Path,
    ziel: Path,
    alle: Path | None = None,
    bilder: Path | None = None,
    namenssprachen: tuple[str, ...] = NAMENSSPRACHEN,
    meldung=print,
) -> dict:
    """Baut den Katalog und gibt die Kennzahlen zurück (Zeilen, Bauzeit, Größe)."""
    quelle, ziel = Path(quelle), Path(ziel)
    start = time.perf_counter()
    ziel.parent.mkdir(parents=True, exist_ok=True)
    temp = ziel.with_name(ziel.name + ".tmp")
    for rest in (temp, temp.with_name(temp.name + "-journal")):
        if rest.exists():
            rest.unlink()

    mit_bild = bild_ids(bilder)
    meldung(f"Bilddateien gefunden: {len(mit_bild)}")

    zeilen: list[tuple] = []
    saetze: dict[str, list] = {}  # set_code -> [name, frühestes Datum, Zahl]
    orakel: dict[str, list] = {}  # oracle_id -> [name, rang]
    namen: dict[tuple[str, str, str], None] = {}  # (oracle_id, name, sprache)
    in_default: set[str] = set()
    sprachen = set(namenssprachen)

    def name_merken(oracle: str | None, name: str | None, sprache: str) -> None:
        if not oracle or not name:
            return
        for variante in _namensvarianten(name):
            if normalisiere(variante):
                namen[(oracle, variante, sprache)] = None

    meldung(f"Lese {quelle.name} …")
    for objekt in objekte(quelle):
        z = zeile(objekt)
        in_default.add(z["scryfall_id"])
        set_code = z["set_code"] or ""
        satz = saetze.get(set_code)
        erschienen = objekt.get("released_at")
        if satz is None:
            saetze[set_code] = [z["set_name"], erschienen, 1]
        else:
            satz[2] += 1
            if erschienen and (satz[1] is None or erschienen < satz[1]):
                satz[1] = erschienen
        zeilen.append((
            z["scryfall_id"], z["oracle_id"], z["name"], set_code, z["set_name"], None, erschienen,
            z["nummer"] or "", z["nummer_zahl"], z["sprache"] or "", z["manakosten"], z["manawert"],
            z["farben"], z["farbidentitaet"], z["farbgruppe"], z["typzeile"], z["haupttyp"],
            z["seltenheit"], objekt.get("layout"), 1 if z["scryfall_id"] in mit_bild else 0,
            objekt.get("cardmarket_id"),
        ))
        oracle = z["oracle_id"]
        if oracle:
            rang = 1 if objekt.get("layout") in NEBENKARTEN else 0
            alt = orakel.get(oracle)
            if alt is None:
                orakel[oracle] = [z["name"], rang]
            elif rang < alt[1]:
                alt[1] = rang
            name_merken(oracle, z["name"], "en")
            if z["sprache"] in sprachen:
                name_merken(oracle, gedruckter_name(objekt), z["sprache"])
    gelesen_default = len(zeilen)
    meldung(f"  {gelesen_default} Drucke, {len(saetze)} Sets")

    sprachdrucke: list[tuple] = []
    gelesen_alle = 0
    if alle is not None and Path(alle).is_file():
        meldung(f"Lese {Path(alle).name} …")
        for objekt in objekte(Path(alle)):
            gelesen_alle += 1
            if objekt["id"] in in_default:
                continue
            oracle = oracle_von(objekt)
            sprache = objekt.get("lang") or ""
            gedruckt = gedruckter_name(objekt)
            sprachdrucke.append((
                objekt["id"], oracle, str(objekt.get("set", "")).lower(),
                str(objekt.get("collector_number", "")), sprache, gedruckt or objekt.get("name"),
            ))
            if sprache in sprachen:
                name_merken(oracle, gedruckt, sprache)
        meldung(f"  {gelesen_alle} Einträge, davon {len(sprachdrucke)} Zuordnungen (nicht in default_cards)")
    elif alle is not None:
        meldung(f"Hinweis: {alle} fehlt, keine Sprachdruck-Zuordnung und keine gedruckten Namen daraus.")

    # Nur Namen von Karten, die es als Druck gibt (Oracle-ID im Orakel); sonst keine Anzeige möglich.
    namenszeilen = [
        (oracle, name, sprache, normalisiere(name))
        for (oracle, name, sprache) in namen
        if oracle in orakel
    ]

    con = sqlite3.connect(temp)
    try:
        con.execute("PRAGMA journal_mode = OFF")
        con.execute("PRAGMA synchronous = OFF")
        con.executescript(SCHEMA)
        con.executemany(
            "INSERT INTO satz (set_code, name, erschienen, drucke) VALUES (?, ?, ?, ?)",
            [(k, v[0], v[1], v[2]) for k, v in saetze.items()],
        )
        zeilen = [
            z[:5] + (saetze[z[3]][1],) + z[6:]  # set_erschienen = frühestes Datum des Sets
            for z in zeilen
        ]
        con.executemany(
            f"INSERT INTO druck ({', '.join(SPALTEN_DRUCK)}) VALUES ({', '.join('?' for _ in SPALTEN_DRUCK)})",
            zeilen,
        )
        con.executemany("INSERT INTO sprachdruck VALUES (?, ?, ?, ?, ?, ?)", sprachdrucke)
        con.executemany(
            "INSERT INTO orakel (oracle_id, name, rang) VALUES (?, ?, ?)",
            [(k, v[0], v[1]) for k, v in orakel.items()],
        )
        con.executemany("INSERT INTO namen (oracle_id, name, sprache, norm) VALUES (?, ?, ?, ?)", namenszeilen)
        con.executescript(INDIZES)
        anzahl = {
            tabelle: con.execute(f"SELECT COUNT(*) FROM {tabelle}").fetchone()[0]
            for tabelle in ("druck", "satz", "sprachdruck", "orakel", "namen")
        }
        mit_bild_zeilen = con.execute("SELECT COUNT(*) FROM druck WHERE bild_vorhanden = 1").fetchone()[0]
        meta = {
            "version": str(VERSION),
            "gebaut": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "quelle": quelle.name,
            "alle": Path(alle).name if alle else "",
            "gelesen_default_cards": str(gelesen_default),
            "gelesen_all_cards": str(gelesen_alle),
            "namenssprachen": ",".join(namenssprachen),
            **{f"zeilen_{k}": str(v) for k, v in anzahl.items()},
            "drucke_mit_bild": str(mit_bild_zeilen),
        }
        con.executemany("INSERT INTO meta VALUES (?, ?)", list(meta.items()))
        con.commit()
    except BaseException:
        con.close()
        for rest in (temp, temp.with_name(temp.name + "-journal")):
            if rest.exists():
                rest.unlink()
        raise
    con.close()
    os.replace(temp, ziel)  # atomar; scheitert unter Windows, solange ein Prozess den Katalog geöffnet hat

    sekunden = time.perf_counter() - start
    return {
        "gelesen_default_cards": gelesen_default,
        "gelesen_all_cards": gelesen_alle,
        "zeilen": anzahl,
        "drucke_mit_bild": mit_bild_zeilen,
        "sekunden": round(sekunden, 1),
        "groesse_mb": round(ziel.stat().st_size / 1e6, 1),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Baut den Katalog aus den lokalen Scryfall-Daten.")
    parser.add_argument("--quelle", type=Path, default=STANDARD_QUELLE, help="default_cards.json (ein Objekt je Zeile)")
    parser.add_argument("--alle", type=Path, default=STANDARD_ALLE, help="all_cards.json (Sprachdrucke und gedruckte Namen)")
    parser.add_argument("--ohne-alle", action="store_true", help="all_cards nicht lesen")
    parser.add_argument("--bilder", type=Path, default=STANDARD_BILDER, help="Ordner mit <scryfall_id>.jpg")
    parser.add_argument("--ziel", type=Path, default=STANDARD_KATALOG)
    parser.add_argument("--namenssprachen", default=",".join(NAMENSSPRACHEN), help="Kürzel, getrennt durch Komma")
    parser.add_argument("--bericht", type=Path, help="Kennzahlen zusätzlich als JSON schreiben")
    args = parser.parse_args(argv)

    if not args.quelle.is_file():
        print(f"Fehler: Quelle fehlt: {args.quelle}", file=sys.stderr)
        return 1
    sprachen = tuple(s.strip() for s in args.namenssprachen.split(",") if s.strip())
    kennzahlen = bauen(
        args.quelle, args.ziel, None if args.ohne_alle else args.alle, args.bilder, sprachen,
    )
    print(json.dumps(kennzahlen, ensure_ascii=False, indent=2))
    if args.bericht:
        args.bericht.parent.mkdir(parents=True, exist_ok=True)
        args.bericht.write_text(json.dumps(kennzahlen, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Katalog: {args.ziel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
