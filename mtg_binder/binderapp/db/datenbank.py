"""Zugriff auf die SQLite-Datenbank der Binder-App.

Schreibende Zugriffe laufen über `einfuegen`, `aendern` und `loeschen`, damit jede Änderung
in der Tabelle `historie` landet. Läuft auch auf dem Raspberry Pi (nur Standardbibliothek).

Server und Verarbeiter schreiben in dieselbe Datei: WAL-Modus, kurze Transaktionen
(`schreiben`) und eine Wartezeit bei Sperre (`WARTEZEIT`).
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")
TABELLEN = {"binder", "seite", "tasche", "kiste", "karte", "foto", "nutzer"}
VERSION = 4          # Stand von schema.sql (PRAGMA user_version); 4: Preise (31.10.2026)
MINDEST = 2          # ältester Stand, mit dem Server und Verarbeiter noch laufen (ohne Kisten)
WARTEZEIT = 10       # Sekunden Wartezeit, wenn ein anderer Prozess gerade schreibt

# Spalten, die Version 2 an bestehende Tabellen anhängt (Reihenfolge wie in schema.sql)
SPALTEN_V2 = {
    "foto": [
        ("quelle", "TEXT NOT NULL DEFAULT 'import' CHECK (quelle IN ('import', 'upload'))"),
        ("dateiname", "TEXT"),
        ("groesse", "INTEGER"),
        ("format", "TEXT"),
        ("hochgeladen", "TEXT"),
    ],
    "seite": [
        ("art", "TEXT NOT NULL DEFAULT 'foto' CHECK (art IN ('foto', 'leer'))"),
    ],
}

# Spalten, die Version 3 anhängt (Bulkkisten, ManaBox-Import); neue Tabellen legt schema.sql an
SPALTEN_V3 = {
    "karte": [
        ("kiste_id", "INTEGER REFERENCES kiste(id) CHECK (kiste_id IS NULL OR tasche_id IS NULL)"),
        ("quelle", "TEXT NOT NULL DEFAULT 'scan' CHECK (quelle IN ('scan', 'manabox', 'manuell'))"),
        ("extern_id", "TEXT"),
        ("zustand", "TEXT"),
    ],
    "historie": [
        ("schritt", "TEXT"),
        ("rueckgaengig_von", "TEXT"),
    ],
}

NEUE_TABELLEN = {"kartendaten"}   # gibt es erst ab Version 3; fehlt sie, legt schema.sql sie vollständig an

# Spalten, die Version 4 anhängt (Preise); die Tabellen preis und preisabruf legt schema.sql an
SPALTEN_V4 = {
    "kartendaten": [
        ("cardmarket_id", "INTEGER"),
    ],
}


def verbinden(pfad: str | Path, nur_lesen: bool = False) -> sqlite3.Connection:
    """Öffnet die Datenbank; `nur_lesen` für reine Abfragen (keine Schreibsperren)."""
    pfad = Path(pfad)
    if nur_lesen:
        con = sqlite3.connect(f"{pfad.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False,
                              timeout=WARTEZEIT)
    else:
        con = sqlite3.connect(pfad, timeout=WARTEZEIT, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def version(con) -> int:
    """Stand des Schemas. Version 1 (26.10.2026) setzte noch keine user_version, daher 0 → 1."""
    return con.execute("PRAGMA user_version").fetchone()[0] or 1


def _spalten(con, tabelle: str) -> list[str]:
    return [z[1] for z in con.execute(f"PRAGMA table_info({tabelle})")]


def fehlende_spalten(con) -> list[str]:
    """Spalten, die `migrieren` anhängen würde (leer, wenn die Datenbank aktuell ist)."""
    fehlend = []
    for spalten_je in (SPALTEN_V2, SPALTEN_V3, SPALTEN_V4):
        for tabelle, spalten in spalten_je.items():
            vorhanden = _spalten(con, tabelle)
            if not vorhanden and tabelle in NEUE_TABELLEN:
                continue   # die Tabelle legt schema.sql gleich mit allen Spalten an
            fehlend += [f"{tabelle}.{name}" for name, _ in spalten if name not in vorhanden]
    return fehlend


def migrieren(con) -> list[str]:
    """Bringt eine Datenbank der Version 1, 2 oder 3 auf Version 4. Wiederholbar: Vorhandenes bleibt, wie es ist.

    Gibt die ausgeführten Schritte zurück (leer, wenn nichts zu tun war). Daten werden nicht
    verändert; neue Spalten bekommen ihren Standardwert (Fotos: Quelle „import", Seiten: Art „foto",
    Karten: Quelle „scan", ohne Kiste).
    """
    schritte = []
    with schreiben(con):
        for spalten_je in (SPALTEN_V2, SPALTEN_V3, SPALTEN_V4):
            for tabelle, spalten in spalten_je.items():
                vorhanden = _spalten(con, tabelle)
                if not vorhanden and tabelle in NEUE_TABELLEN:
                    continue   # Tabelle fehlt noch (z. B. kartendaten bei Version 2): schema.sql legt sie ganz an
                for name, art in spalten:
                    if name not in vorhanden:
                        con.execute(f"ALTER TABLE {tabelle} ADD COLUMN {name} {art}")
                        schritte.append(f"{tabelle}.{name}")
        if version(con) < VERSION:
            schritte.append(f"Version {version(con)} -> {VERSION}")
    # Tabellen und Indizes, die fehlen, legt schema.sql an (IF NOT EXISTS); setzt die Version.
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    return schritte


def wal(con) -> None:
    """Schaltet den WAL-Modus ein (bleibt in der Datei gespeichert)."""
    con.execute("PRAGMA journal_mode = WAL")


def anlegen(pfad: str | Path) -> sqlite3.Connection:
    """Legt die Datenbank mit Schema an (idempotent) und gibt die Verbindung zurück.

    Eine vorhandene Datenbank der Version 1 wird dabei migriert, nie nur umetikettiert.
    """
    Path(pfad).parent.mkdir(parents=True, exist_ok=True)
    con = verbinden(pfad)
    if _spalten(con, "foto"):
        migrieren(con)
    else:
        con.executescript(SCHEMA.read_text(encoding="utf-8"))
    wal(con)
    return con


def leer_anlegen(pfad: str | Path, nutzer: str = "Daniel") -> None:
    """Neue, leere Datenbank der aktuellen Version mit dem einen Nutzer (ID 1), z. B. beim ersten Start des
    Add-ons. Verweigert, wenn die Datei schon existiert."""
    pfad = Path(pfad)
    if pfad.exists():
        raise FileExistsError(f"{pfad} existiert schon.")
    con = anlegen(pfad)
    try:
        with schreiben(con):
            nid = con.execute("INSERT INTO nutzer (name) VALUES (?)", (nutzer,)).lastrowid
            _protokoll(con, nid, "nutzer", "nutzer", nid, "angelegt", None, {"name": nutzer})
    finally:
        con.close()


def sichern(pfad: str | Path, ordner: str | Path) -> Path:
    """Kopiert die Datenbank konsistent (SQLite-Sicherung) nach `ordner`; überschreibt nie etwas."""
    ordner = Path(ordner)
    ordner.mkdir(parents=True, exist_ok=True)
    stamm = f"{Path(pfad).stem}-{datetime.now():%Y%m%d-%H%M%S}"
    ziel = ordner / f"{stamm}.sqlite"
    n = 1
    while ziel.exists():
        n += 1
        ziel = ordner / f"{stamm}-{n}.sqlite"
    quelle = sqlite3.connect(Path(pfad), timeout=WARTEZEIT)  # liest nur; schreibbar, damit keine WAL-Reste bleiben
    kopie = sqlite3.connect(ziel)
    try:
        quelle.backup(kopie)
    finally:
        kopie.close()
        quelle.close()
    return ziel


def aeltere_entfernen(ordner: str | Path, behalten: int) -> list[Path]:
    """Löscht in `ordner` alle Sicherungen (*.sqlite) außer den `behalten` jüngsten (nach Änderungszeit).
    Nur für das Add-on (Entscheidung des Nutzers: die letzten 10); am PC bleibt jede Sicherung."""
    if behalten < 1:
        raise ValueError("Mindestens eine Sicherung behalten.")
    dateien = sorted(Path(ordner).glob("*.sqlite"), key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    weg = dateien[behalten:]
    for datei in weg:
        datei.unlink(missing_ok=True)
    return weg


@contextmanager
def schreiben(con):
    """Eine kurze Schreibtransaktion: sperrt sofort (BEGIN IMMEDIATE), am Ende COMMIT oder ROLLBACK."""
    if con.in_transaction:
        raise RuntimeError("Transaktion läuft schon")
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        con.rollback()
        raise
    con.commit()


def _protokoll(con, nutzer_id, quelle, tabelle, datensatz, aktion, alt, neu, schritt=None, rueckgaengig_von=None):
    werte = (nutzer_id, tabelle, datensatz, aktion, quelle,
             json.dumps(alt, ensure_ascii=False) if alt is not None else None,
             json.dumps(neu, ensure_ascii=False) if neu is not None else None)
    if schritt is None and rueckgaengig_von is None:  # so läuft es auch mit Datenbanken der Version 2
        con.execute("INSERT INTO historie (nutzer_id, tabelle, datensatz, aktion, quelle, alt, neu)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)", werte)
    else:
        con.execute("INSERT INTO historie (nutzer_id, tabelle, datensatz, aktion, quelle, alt, neu, schritt,"
                    " rueckgaengig_von) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (*werte, schritt, rueckgaengig_von))


def einfuegen(con, nutzer_id: int, quelle: str, tabelle: str, felder: dict, *,
              schritt: str | None = None, rueckgaengig_von: str | None = None) -> int:
    """Legt einen Datensatz an und protokolliert ihn; gibt die neue ID zurück.
    `schritt`: Kennung des Bedienschritts (ab Version 3, für Rückgängig), `rueckgaengig_von`: zurückgenommener Schritt."""
    assert tabelle in TABELLEN, tabelle
    spalten = ", ".join(felder)
    platz = ", ".join("?" for _ in felder)
    cur = con.execute(f"INSERT INTO {tabelle} ({spalten}) VALUES ({platz})", tuple(felder.values()))
    _protokoll(con, nutzer_id, quelle, tabelle, cur.lastrowid, "angelegt", None, felder, schritt, rueckgaengig_von)
    return cur.lastrowid


def aendern(con, nutzer_id: int, quelle: str, tabelle: str, datensatz: int, felder: dict, *,
            schritt: str | None = None, rueckgaengig_von: str | None = None) -> dict:
    """Ändert Felder eines Datensatzes; protokolliert nur tatsächlich geänderte Werte."""
    assert tabelle in TABELLEN, tabelle
    zeile = con.execute(f"SELECT * FROM {tabelle} WHERE id = ?", (datensatz,)).fetchone()
    if zeile is None:
        raise KeyError(f"{tabelle} {datensatz} fehlt")
    alt = {k: zeile[k] for k in felder if zeile[k] != felder[k]}
    if not alt:
        return {}
    neu = {k: felder[k] for k in alt}
    setzen = ", ".join(f"{k} = ?" for k in neu)
    if "geaendert" in zeile.keys():
        setzen += ", geaendert = strftime('%Y-%m-%dT%H:%M:%S', 'now')"
    con.execute(f"UPDATE {tabelle} SET {setzen} WHERE id = ?", (*neu.values(), datensatz))
    _protokoll(con, nutzer_id, quelle, tabelle, datensatz, "geaendert", alt, neu, schritt, rueckgaengig_von)
    return neu


def loeschen(con, nutzer_id: int, quelle: str, tabelle: str, datensatz: int, *,
             schritt: str | None = None, rueckgaengig_von: str | None = None) -> dict:
    """Löscht einen Datensatz; die Historie behält alle Felder als „alt"."""
    assert tabelle in TABELLEN, tabelle
    zeile = con.execute(f"SELECT * FROM {tabelle} WHERE id = ?", (datensatz,)).fetchone()
    if zeile is None:
        raise KeyError(f"{tabelle} {datensatz} fehlt")
    alt = dict(zeile)
    con.execute(f"DELETE FROM {tabelle} WHERE id = ?", (datensatz,))
    _protokoll(con, nutzer_id, quelle, tabelle, datensatz, "geloescht", alt, None, schritt, rueckgaengig_von)
    return alt
