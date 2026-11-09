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
TABELLEN = {"binder", "seite", "tasche", "kiste", "karte", "foto", "nutzer", "scan_durchgang", "scan_foto",
            "sortierregel", "sortierfach", "deck", "deck_karte", "deck_entnahme"}
VERSION = 15         # Stand von schema.sql (PRAGMA user_version); 4: Preise, 5: Bulk-Scan, 6: Sortierregeln (01.11.2026),
                     # 7: Decks (02.11.2026), 8: Deckbau (03.11.2026), 9: Foil und Sets je Scan-Durchgang (04.11.2026),
                     # 10: Messwerte je Scan-Foto (05.11.2026), 11: Kaufpreis (05.11.2026),
                     # 12: Secret-Lair-Drops (05.11.2026), 13: Reprints der Drops mit Druck (06.11.2026),
                     # 14: Ausschnitt je Scan-Foto (06.11.2026), 15: Verkaufstag und Wunsch je Secret-Lair-Drop (09.11.2026)
MINDEST = 2          # ältester Stand, mit dem Server und Verarbeiter noch laufen (ohne Kisten)
WARTEZEIT = 10       # Sekunden Wartezeit, wenn ein anderer Prozess gerade schreibt
ANZAHL_MAX = 999     # so viele Exemplare höchstens in einem Posten

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

# Tabellen, die erst später dazukamen (kartendaten ab 3, scan_durchgang ab 5); fehlt eine, legt schema.sql sie
# vollständig an, statt Spalten anzuhängen
NEUE_TABELLEN = {"kartendaten", "scan_durchgang", "scan_foto", "deck_karte", "secret_karte", "secret_drop"}

# Spalten, die Version 4 anhängt (Preise); die Tabellen preis und preisabruf legt schema.sql an
SPALTEN_V4 = {
    "kartendaten": [
        ("cardmarket_id", "INTEGER"),
    ],
}

# Spalten, die Version 6 anhängt (Sortierregeln); die Tabellen sortierregel und sortierfach legt schema.sql an
SPALTEN_V6 = {
    "karte": [
        ("sortieren", "TEXT CHECK (sortieren IS NULL OR sortieren = 'offen')"),
        ("vorgemerkt_binder_id", "INTEGER REFERENCES binder(id) ON DELETE SET NULL"),
    ],
    "kartendaten": [
        ("besonderheiten", "TEXT"),
        ("promo", "INTEGER"),
        ("set_art", "TEXT"),
    ],
    "scan_durchgang": [
        ("einsortieren", "INTEGER NOT NULL DEFAULT 1 CHECK (einsortieren IN (0, 1))"),
    ],
}
# Spalten, die Version 8 anhängt (Deckbau): Ort „Deck“ und Herkunft je Karte; die Tabellen deck_produkt und
# deck_entnahme legt schema.sql an. deck_karte gibt es erst ab Version 7 (sonst legt schema.sql sie ganz an).
SPALTEN_V8 = {
    "karte": [
        ("deck_id", "INTEGER REFERENCES deck(id)"),
        ("herkunft_tasche_id", "INTEGER REFERENCES tasche(id) ON DELETE SET NULL"),
        ("herkunft_kiste_id", "INTEGER REFERENCES kiste(id) ON DELETE SET NULL"),
    ],
    "deck_karte": [
        ("produkte", "INTEGER"),
    ],
}
# Spalten, die Version 9 anhängt (Wunsch des Nutzers, 04.11.2026): Einstellungen je Scan-Durchgang
SPALTEN_V9 = {
    "scan_durchgang": [
        ("foil", "INTEGER NOT NULL DEFAULT 0 CHECK (foil IN (0, 1))"),
        ("sets", "TEXT"),
    ],
}
# Spalten, die Version 11 anhängt (Antwort des Nutzers, 05.11.2026): Kaufpreis aus ManaBox
SPALTEN_V11 = {
    "karte": [
        ("kaufpreis", "REAL"),
        ("kaufpreis_waehrung", "TEXT"),
    ],
}
# Spalten, die Version 13 anhängt (Wunsch des Nutzers, 06.11.2026): Bilder der Reprints in der Secret-Lair-Ansicht
SPALTEN_V13 = {
    "secret_karte": [
        ("teuer_id", "TEXT"),
        ("guenstig_id", "TEXT"),
        ("guenstig_nummer", "TEXT"),
    ],
}
# Spalte, die Version 14 anhängt (Wunsch des Nutzers, 06.11.2026): Ausschnitt, nach dem eine Scan-Karte bestimmt wurde
SPALTEN_V14 = {
    "scan_foto": [
        ("ausschnitt", "TEXT"),
    ],
}
# Spalten, die Version 15 anhängt (Wunsch des Nutzers, 09.11.2026): Verkaufstag vom Nutzer und Kaufwunsch je Drop
SPALTEN_V15 = {
    "secret_drop": [
        ("datum_eigen", "INTEGER NOT NULL DEFAULT 0 CHECK (datum_eigen IN (0, 1))"),
        ("wunsch", "INTEGER NOT NULL DEFAULT 0 CHECK (wunsch IN (0, 1))"),
    ],
}
ALLE_SPALTEN = (SPALTEN_V2, SPALTEN_V3, SPALTEN_V4, SPALTEN_V6, SPALTEN_V8, SPALTEN_V9, SPALTEN_V11, SPALTEN_V13,
                SPALTEN_V14, SPALTEN_V15)

# Karten aus dem Bulk-Scan liegen in keiner Tasche: Ihr Ausschnitt steht am Scan-Foto. Mehrere Fotos eines Postens
# zeigen denselben Druck, dann genügt das neueste.
SQL_AUSSCHNITT = ("COALESCE(t.ausschnitt, (SELECT f.ausschnitt FROM scan_foto f WHERE f.karte_id = k.id"
                  " AND f.ausschnitt IS NOT NULL ORDER BY f.id DESC LIMIT 1)) AS ausschnitt")


def mit_scan_ausschnitt(con, sql: str) -> str:
    """Ersetzt in `sql` das erste `t.ausschnitt` durch `SQL_AUSSCHNITT` (Datenbanken vor Version 14 kennen die Spalte
    noch nicht und bleiben bei der Tasche)."""
    return sql.replace("t.ausschnitt", SQL_AUSSCHNITT, 1) if version(con) >= 14 else sql


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
    for spalten_je in ALLE_SPALTEN:
        for tabelle, spalten in spalten_je.items():
            vorhanden = _spalten(con, tabelle)
            if not vorhanden and tabelle in NEUE_TABELLEN:
                continue   # die Tabelle legt schema.sql gleich mit allen Spalten an
            fehlend += [f"{tabelle}.{name}" for name, _ in spalten if name not in vorhanden]
    return fehlend


def migrieren(con) -> list[str]:
    """Bringt eine Datenbank der Version 1 bis 14 auf Version 15. Wiederholbar: Vorhandenes bleibt, wie es ist.

    Gibt die ausgeführten Schritte zurück (leer, wenn nichts zu tun war). Daten werden nicht
    verändert; neue Spalten bekommen ihren Standardwert (Fotos: Quelle „import", Seiten: Art „foto",
    Karten: Quelle „scan", ohne Kiste).
    """
    schritte = []
    with schreiben(con):
        for spalten_je in ALLE_SPALTEN:
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


def leer_anlegen(pfad: str | Path, nutzer: str = "Sammler") -> None:
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


def scan_fotos_von(con, karte_id: int) -> list[int]:
    """Scan-Fotos einer Karte. Wer die Karte löscht, löst sie vorher mit Historie: Sonst leert ON DELETE SET NULL die
    Verbindung unbemerkt, und ein Rückgängig stellt sie nicht wieder her."""
    if version(con) < 5:
        return []
    return [z[0] for z in con.execute("SELECT id FROM scan_foto WHERE karte_id = ?", (karte_id,)).fetchall()]


def loeschen(con, nutzer_id: int, quelle: str, tabelle: str, datensatz: int, *,
             schritt: str | None = None, rueckgaengig_von: str | None = None) -> dict:
    """Löscht einen Datensatz; die Historie behält alle Felder als „alt"."""
    assert tabelle in TABELLEN, tabelle
    zeile = con.execute(f"SELECT * FROM {tabelle} WHERE id = ?", (datensatz,)).fetchone()
    if zeile is None:
        raise KeyError(f"{tabelle} {datensatz} fehlt")
    alt = dict(zeile)
    if tabelle == "karte":
        for foto in scan_fotos_von(con, datensatz):
            aendern(con, nutzer_id, quelle, "scan_foto", foto, {"karte_id": None}, schritt=schritt,
                    rueckgaengig_von=rueckgaengig_von)
    con.execute(f"DELETE FROM {tabelle} WHERE id = ?", (datensatz,))
    _protokoll(con, nutzer_id, quelle, tabelle, datensatz, "geloescht", alt, None, schritt, rueckgaengig_von)
    return alt
