"""Price Guide von Cardmarket laden und die Preise der Sammlung für den Tag speichern.

    $env:PYTHONPATH = "src"; .venv\\Scripts\\python.exe -m binderapp.preise.abruf --daten work/X/app [--datei …]

Die Datei (etwa 25 MB, rund 130.000 Produkte, ein JSON-Objekt) wird nicht als Ganzes in Python-Objekte
verwandelt: Die flachen Einträge `{"idProduct": …}` werden einzeln gefunden und nur die der Sammlung gelesen.
So bleibt der Speicherbedarf im Add-on klein. Der Tag ist der Erstellungstag der Datei (`createdAt`); ein
zweiter Abruf am selben Tag ersetzt die Werte des Tages.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
import urllib.request
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from binderapp.db.datenbank import schreiben, verbinden, version

URL = "https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_1.json"
ZEIT = 120
EINTRAG = re.compile(rb"\{[^{}]*\}")
ID = re.compile(rb'"idProduct"\s*:\s*(\d+)')
ERSTELLT = re.compile(rb'"createdAt"\s*:\s*"([^"]{10,40})"')

log = logging.getLogger("binderapp.preise")


class AbrufFehler(RuntimeError):
    pass


def herunterladen(ziel: Path, url: str = URL, oeffnen=urllib.request.urlopen) -> Path:
    """Lädt die Datei nach `ziel` (erst als Teildatei, dann umbenannt)."""
    ziel.parent.mkdir(parents=True, exist_ok=True)
    teil = ziel.with_name(ziel.name + ".teil")
    anfrage = urllib.request.Request(url, headers={"User-Agent": "MTG-Binder (privat)"})
    try:
        with oeffnen(anfrage, timeout=ZEIT) as antwort, teil.open("wb") as f:
            shutil.copyfileobj(antwort, f, 1 << 20)
    except OSError as fehler:
        teil.unlink(missing_ok=True)
        raise AbrufFehler(f"Preise nicht geladen: {fehler}") from None
    teil.replace(ziel)
    return ziel


def sammlung_ids(con) -> set[int]:
    return {z[0] for z in con.execute("SELECT DISTINCT cardmarket_id FROM kartendaten WHERE cardmarket_id IS NOT NULL")}


def _zahl(wert):
    return float(wert) if isinstance(wert, (int, float)) and not isinstance(wert, bool) and wert >= 0 else None


def einlesen(roh: bytes, ids: set[int]) -> tuple[str, int, list[tuple]]:
    """(Erstellungszeitpunkt, Zahl der Produkte, Zeilen für die Tabelle preis ohne Tag) aus dem Inhalt der Datei."""
    m = ERSTELLT.search(roh[:2000])
    if m is None:
        raise AbrufFehler("Die Preisdatei hat kein Erstellungsdatum (createdAt).")
    erstellt = m.group(1).decode("ascii", "replace")
    produkte, zeilen = 0, []
    for e in EINTRAG.finditer(roh):
        kennung = ID.search(e.group())
        if kennung is None:
            continue
        produkte += 1
        pid = int(kennung.group(1))
        if pid not in ids:
            continue
        d = json.loads(e.group())
        zeilen.append((pid, _zahl(d.get("low")), _zahl(d.get("avg7")), _zahl(d.get("low-foil")),
                       _zahl(d.get("avg7-foil"))))
    if produkte < 1000:
        raise AbrufFehler(f"Die Preisdatei sieht unvollständig aus ({produkte} Produkte).")
    return erstellt, produkte, zeilen


def speichern(con, erstellt: str, produkte: int, zeilen: list[tuple]) -> dict:
    """Schreibt die Preise des Tages (in der Transaktion des Aufrufers)."""
    tag = erstellt[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", tag):
        raise AbrufFehler(f"Unbekanntes Datum in der Preisdatei: {erstellt}")
    con.executemany("INSERT OR REPLACE INTO preis (cardmarket_id, tag, low, avg7, low_foil, avg7_foil)"
                    " VALUES (?, ?, ?, ?, ?, ?)", [(z[0], tag, *z[1:]) for z in zeilen])
    con.execute("INSERT OR REPLACE INTO preisabruf (tag, erstellt, abgerufen, produkte, gespeichert)"
                " VALUES (?, ?, ?, ?, ?)",
                (tag, erstellt, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), produkte, len(zeilen)))
    return {"tag": tag, "erstellt": erstellt, "produkte": produkte, "gespeichert": len(zeilen)}


def cardmarket_nachtragen(con, katalog: Path | None) -> int:
    """Kartendaten ohne Cardmarket-ID aus dem Katalog ergänzen (älterer Katalog oder Daten von vor Version 4)."""
    if katalog is None or not Path(katalog).is_file():
        return 0
    from binderapp.katalog import kartendaten
    offen = [z[0] for z in con.execute("SELECT scryfall_id FROM kartendaten WHERE cardmarket_id IS NULL")]
    if not offen:
        return 0
    n = 0
    with closing(verbinden(katalog, nur_lesen=True)) as kat:
        if "cardmarket_id" not in {z[1] for z in kat.execute("PRAGMA table_info(druck)")}:
            return 0   # Katalog von vor Version 2: erst neu bauen und schicken
        for sid in offen:
            d = kartendaten.nachschlagen(kat, sid)
            if d and d.get("cardmarket_id"):
                con.execute("UPDATE kartendaten SET cardmarket_id = ? WHERE scryfall_id = ?", (d["cardmarket_id"], sid))
                n += 1
    return n


def abrufen(datenbank: Path, ordner: Path, katalog: Path | None = None, datei: Path | None = None,
            oeffnen=urllib.request.urlopen) -> dict:
    """Ganzer Ablauf: Cardmarket-IDs ergänzen, Datei laden (oder `datei` nehmen), Preise des Tages speichern."""
    with closing(verbinden(datenbank)) as con:
        if version(con) < 4:
            raise AbrufFehler("Die Datenbank kennt noch keine Preise (Version 4 nötig).")
        with schreiben(con):
            ergaenzt = cardmarket_nachtragen(con, katalog)
        ids = sammlung_ids(con)
    quelle = datei or herunterladen(Path(ordner) / "price_guide_1.json", oeffnen=oeffnen)
    erstellt, produkte, zeilen = einlesen(Path(quelle).read_bytes(), ids)
    with closing(verbinden(datenbank)) as con, schreiben(con):
        ergebnis = speichern(con, erstellt, produkte, zeilen)
    ergebnis["cardmarket_ergaenzt"] = ergaenzt
    ergebnis["sammlung_produkte"] = len(ids)
    return ergebnis


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--daten", type=Path, required=True, help="Ordner mit binder.sqlite (nie data/app ohne Sicherung)")
    ap.add_argument("--katalog", type=Path, help="Katalog für fehlende Cardmarket-IDs (Standard: data/katalog.sqlite)")
    ap.add_argument("--datei", type=Path, help="schon geladene Preisdatei statt Download")
    a = ap.parse_args(argv)
    from binderapp.katalog import STANDARD_KATALOG
    try:
        e = abrufen(a.daten / "binder.sqlite", a.daten / "preise", a.katalog or STANDARD_KATALOG, a.datei)
    except AbrufFehler as f:
        log.error("%s", f)
        return 1
    log.info("Preise vom %s: %s von %s Produkten gespeichert (Sammlung %s Produkte, %s Cardmarket-IDs ergänzt)",
             e["tag"], e["gespeichert"], e["produkte"], e["sammlung_produkte"], e["cardmarket_ergaenzt"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
