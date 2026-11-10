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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from binderapp.db.datenbank import schreiben, verbinden, version

URL = "https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_1.json"
ZEIT = 120
EINTRAG = re.compile(rb"\{[^{}]*\}")
ID = re.compile(rb'"idProduct"\s*:\s*(\d+)')
# Spalten der Tabelle `preis` in der Reihenfolge der eingelesenen Zeilen, dazu die Schlüssel der Preisdatei.
# Die ersten vier gibt es seit Version 4, die übrigen seit Version 21 (Preisart wählbar).
SPALTEN = ("low", "avg7", "low_foil", "avg7_foil", "trend", "avg1", "avg30", "trend_foil", "avg1_foil", "avg30_foil")
SCHLUESSEL = tuple(s.replace("_foil", "-foil") for s in SPALTEN)
ERSTELLT = re.compile(rb'"createdAt"\s*:\s*"([^"]{10,40})"')

# Aufräumen (Entscheidung des Nutzers, 05.11.2026): Alles bleibt täglich, bis die Preise die Grenze erreichen; dann
# fallen die ältesten Tage weg. Die Größe wird aus der Zeilenzahl geschätzt (dbstat fehlt in manchem SQLite).
GRENZE_MB = 200
BYTES_JE_ZEILE = 133   # gemessen am 10.11.2026 (Version 21, zehn Preisspalten; vorher 95 mit vier): Tabelle preis mit Primärschlüssel
SCHUTZ_TAGE = 40       # nie löschen: Veränderung nach 30 Tagen (Toleranz 3) und verlauf_karte (40 Tage) brauchen sie

log =logging.getLogger("binderapp.preise")


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
    """Produkte, deren Preise gespeichert werden: die Sammlung, (ab Version 8) die Decklisten, auch fehlende Karten
    (bei Zeilen ohne Druck alle Drucke der Karte, für den günstigsten Preis), und (ab Version 12) die Secret-Lair-Drops
    der letzten 12 Monate, damit ab dem Verkaufsstart ein echter Verlauf entsteht. Ab Version 18 kennt die App alle
    Drops seit 2019; in den Verlauf kommen weiter nur die der letzten 12 Monate, kommende und (immer) eigene. Ab Version
    23 auch die Produkte der Kaufliste."""
    ids = {z[0] for z in con.execute("SELECT DISTINCT cardmarket_id FROM kartendaten WHERE cardmarket_id IS NOT NULL")}
    if version(con) >= 8:
        ids |= {z[0] for z in con.execute("SELECT DISTINCT cardmarket_id FROM deck_produkt")}
    if version(con) >= 12:
        grenze = (date.today() - timedelta(days=365)).isoformat()
        eigene = " OR d.id IN (SELECT drop_id FROM secret_kauf)" if version(con) >= 18 else ""
        stern = ", k.foil_cardmarket_id" if version(con) >= 20 else ""
        for z in con.execute(f"SELECT k.cardmarket_id{stern} FROM secret_karte k JOIN secret_drop d ON d.id = k.drop_id"
                             f" WHERE d.verkauf IS NULL OR d.verkauf >= ?{eigene}", (grenze,)):
            ids |= {i for i in z if i is not None}
    if version(con) >= 23:   # Kaufliste (Paket 7): eigene Einträge und Wunschpreise
        ids |= {z[0] for z in con.execute("SELECT DISTINCT cardmarket_id FROM kauf_produkt")}
    return ids


def kauf_produkte_nachtragen(con, katalog: Path | None) -> int:
    """Einträge der Kaufliste ohne Cardmarket-Produkte (Katalog fehlte beim Anlegen) aus dem Katalog ergänzen."""
    if katalog is None or not Path(katalog).is_file() or version(con) < 23:
        return 0
    from binderapp.decks import kaufliste
    with closing(verbinden(katalog, nur_lesen=True)) as kat:
        return kaufliste.produkte_nachtragen(con, kat)


def deck_produkte_nachtragen(con, katalog: Path | None) -> int:
    """Deckzeilen ohne gesuchte Cardmarket-Produkte (vor Version 8 importiert) aus dem Katalog ergänzen."""
    if katalog is None or not Path(katalog).is_file() or version(con) < 8:
        return 0
    from binderapp.decks import abgleich, lesen
    offen = [dict(z) for z in con.execute("SELECT * FROM deck_karte WHERE produkte IS NULL")]
    if not offen:
        return 0
    with closing(verbinden(katalog, nur_lesen=True)) as kat:
        for z in offen:
            abgleich._produkte_schreiben(con, z["id"], lesen.produkte_suchen(kat, z))
    return len(offen)


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
        # (Produkt, low, avg7, low_foil, avg7_foil, dann ab Version 21 die übrigen Arten in SPALTEN_NEU)
        zeilen.append((pid, *(_zahl(d.get(schluessel)) for schluessel in SCHLUESSEL)))
    if produkte < 1000:
        raise AbrufFehler(f"Die Preisdatei sieht unvollständig aus ({produkte} Produkte).")
    return erstellt, produkte, zeilen


def speichern(con, erstellt: str, produkte: int, zeilen: list[tuple], nachtragen: bool = False) -> dict:
    """Schreibt die Preise des Tages (in der Transaktion des Aufrufers). Beim Nachtragen aus der schon geladenen
    Datei bleibt der Zeitpunkt des Abrufs stehen, sonst würde nie wieder neu geladen (Befund 03.11.2026)."""
    tag = erstellt[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", tag):
        raise AbrufFehler(f"Unbekanntes Datum in der Preisdatei: {erstellt}")
    spalten = SPALTEN if version(con) >= 21 else SPALTEN[:4]
    con.executemany(f"INSERT OR REPLACE INTO preis (cardmarket_id, tag, {', '.join(spalten)})"
                    f" VALUES (?, ?, {', '.join('?' for _ in spalten)})",
                    [(z[0], tag, *z[1:len(spalten) + 1]) for z in zeilen])
    con.execute("INSERT INTO preisabruf (tag, erstellt, abgerufen, produkte, gespeichert) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(tag) DO UPDATE SET erstellt = excluded.erstellt, produkte = excluded.produkte,"
                " gespeichert = excluded.gespeichert,"
                " abgerufen = CASE WHEN ? THEN preisabruf.abgerufen ELSE excluded.abgerufen END",
                (tag, erstellt, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), produkte, len(zeilen),
                 int(nachtragen)))
    return {"tag": tag, "erstellt": erstellt, "produkte": produkte, "gespeichert": len(zeilen)}


def aufraeumen(con, grenze_mb: float = GRENZE_MB, schutz_tage: int = SCHUTZ_TAGE) -> list[str]:
    """Löscht ab der Grenze die ältesten Preistage (in der Transaktion des Aufrufers), nie die letzten `schutz_tage`
    Tage. Gibt die gelöschten Tage zurück. Freie Seiten nutzt SQLite weiter, die Datei wächst dann nicht mehr."""
    grenze = int(grenze_mb * 1_000_000 / BYTES_JE_ZEILE)
    zeilen = con.execute("SELECT COUNT(*) FROM preis").fetchone()[0]
    if zeilen <= grenze:
        return []
    neuester = con.execute("SELECT MAX(tag) FROM preisabruf").fetchone()[0]
    schutz = (date.fromisoformat(neuester) - timedelta(days=schutz_tage)).isoformat()
    geloescht = []
    for tag, anzahl in con.execute("SELECT tag, COUNT(*) FROM preis WHERE tag < ? GROUP BY tag ORDER BY tag",
                                   (schutz,)).fetchall():
        if zeilen <= grenze:
            break
        con.execute("DELETE FROM preis WHERE tag = ?", (tag,))
        con.execute("DELETE FROM preisabruf WHERE tag = ?", (tag,))
        zeilen -= anzahl
        geloescht.append(tag)
    return geloescht


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
            oeffnen=urllib.request.urlopen, nachtragen: bool = False) -> dict:
    """Ganzer Ablauf: Cardmarket-IDs ergänzen, Datei laden (oder `datei` nehmen), Preise des Tages speichern.
    `nachtragen`: nur neue Produkte aus der schon geladenen Datei (Zeitpunkt des Abrufs bleibt)."""
    with closing(verbinden(datenbank)) as con:
        if version(con) < 4:
            raise AbrufFehler("Die Datenbank kennt noch keine Preise (Version 4 nötig).")
        with schreiben(con):
            ergaenzt = cardmarket_nachtragen(con, katalog)
            deck_produkte_nachtragen(con, katalog)
            kauf_produkte_nachtragen(con, katalog)
        ids = sammlung_ids(con)
    quelle = datei or herunterladen(Path(ordner) / "price_guide_1.json", oeffnen=oeffnen)
    erstellt, produkte, zeilen = einlesen(Path(quelle).read_bytes(), ids)
    with closing(verbinden(datenbank)) as con, schreiben(con):
        ergebnis = speichern(con, erstellt, produkte, zeilen, nachtragen=nachtragen)
        ergebnis["geloeschte_tage"] = [] if nachtragen else aufraeumen(con)
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
