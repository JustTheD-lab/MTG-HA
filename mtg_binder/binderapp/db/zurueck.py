"""Eine Sicherung in die laufende Datenbank zurückspielen (Verwaltung im Add-on, Wunsch des Nutzers, 09.11.2026).

Wie beim Leeren bleibt die Datei bestehen: Der Inhalt wird in einer einzigen Transaktion ersetzt. So bleiben alle
anderen Verbindungen gültig (Preisabruf, Werte an Home Assistant, PC-Schnittstelle), und ein Fehler lässt alles, wie
es war. Die Sicherung selbst wird nie angefasst: Gearbeitet wird mit einer lokalen Kopie, die vorher geprüft und auf
den Stand des heutigen Schemas gebracht wird (SQLite läuft auf dem Netzlaufwerk nie).

Antworten des Nutzers (09.11.2026): Der Preisverlauf bleibt, wie er heute ist (`AKTUELL`; die Tage seit der Sicherung
gingen sonst verloren). Alles andere kommt aus der Sicherung, auch die Secret-Lair-Tabellen: Ihre Marktdaten holt der
nächste Abgleich neu, die eigenen Käufe und Einstellungen hängen an den Nummern der Drops. Der Nutzer und das
Lebenszeichen des PCs bleiben wie beim Leeren.

    vorbereiten(sicherung, arbeit)        Lokale, geprüfte, migrierte Kopie; gibt ihre ursprüngliche Version zurück
    zahlen(arbeit, nutzer)                Was in der Sicherung steckt
    uebernehmen(db, arbeit, nutzer, …)    Ersetzt den Inhalt (eine Transaktion), zählt den Bildstand hoch
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from binderapp.db import datenbank, leeren, sicherung

AKTUELL = frozenset({"preis", "preisabruf"})   # bleibt, wie es heute ist
BLEIBT = leeren.BEHALTEN | AKTUELL
QUELLE = "sicherung"                            # Historie: Zeile über das Zurückspielen


class Unbrauchbar(ValueError):
    """Die Sicherung lässt sich nicht zurückspielen (beschädigt, zu neu, passt nicht zu dieser Datenbank)."""


def vorbereiten(quelle: Path, arbeit: Path) -> int:
    """Kopiert die Sicherung nach `arbeit` (lokal), prüft sie und bringt sie auf das heutige Schema."""
    arbeit = Path(arbeit)
    arbeit.parent.mkdir(parents=True, exist_ok=True)
    for rest in (arbeit, arbeit.with_name(arbeit.name + "-wal"), arbeit.with_name(arbeit.name + "-shm")):
        rest.unlink(missing_ok=True)
    try:
        sicherung.kopieren(Path(quelle), arbeit)
        sicherung.pruefen(arbeit)
    except OSError as f:
        raise Unbrauchbar(f"Die Sicherung ließ sich nicht lesen: {f}") from f
    except ValueError as f:
        raise Unbrauchbar(str(f)) from f
    try:
        with closing(sqlite3.connect(arbeit)) as con:
            vorher = con.execute("PRAGMA user_version").fetchone()[0] or 1
            hat_karten = con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'karte'").fetchone()
        if not hat_karten:
            raise Unbrauchbar("Die Datei ist keine Datenbank dieser App.")
        if vorher > datenbank.VERSION:
            raise Unbrauchbar("Die Sicherung stammt von einer neueren Version der App. Bitte zuerst das Add-on aktualisieren.")
        with closing(datenbank.anlegen(arbeit)):   # ältere Sicherung auf den heutigen Stand bringen
            pass
    except sqlite3.Error as f:
        raise Unbrauchbar(f"Die Sicherung ließ sich nicht auf den heutigen Stand bringen: {f}") from f
    return vorher


def zahlen(arbeit: Path, nutzer: int) -> dict:
    with closing(datenbank.verbinden(arbeit, nur_lesen=True)) as con:
        return leeren.zahlen(con, nutzer)


def _spalten(con, schema: str, tabelle: str) -> list[str]:
    return [z[1] for z in con.execute(f'PRAGMA {schema}.table_info("{tabelle}")')]


def uebernehmen(db: Path, arbeit: Path, nutzer: int, *, hinweis: dict | None = None, vorher_pruefen=None) -> dict:
    """Ersetzt den Inhalt der Datenbank durch den der vorbereiteten Kopie. `vorher_pruefen(con)` läuft in der
    Transaktion und darf abbrechen (zum Beispiel, wenn der PC inzwischen an einem Foto rechnet). Gibt die Zahlen
    nach dem Zurückspielen zurück."""
    con = datenbank.verbinden(db)
    try:
        con.execute("ATTACH DATABASE ? AS alt", (str(Path(arbeit)),))   # nur außerhalb einer Transaktion möglich
        try:
            with datenbank.schreiben(con):
                if vorher_pruefen is not None:
                    vorher_pruefen(con)
                con.execute("PRAGMA defer_foreign_keys = ON")
                # Namen stammen aus sqlite_master und table_info, nie aus einer Anfrage
                in_alt = {z[0] for z in con.execute("SELECT name FROM alt.sqlite_master WHERE type = 'table'")}
                tabellen = [t for t in leeren.tabellen(con) if t not in AKTUELL]
                for t in tabellen:
                    con.execute(f'DELETE FROM main."{t}"')
                for t in tabellen:
                    if t not in in_alt:
                        continue
                    alt = set(_spalten(con, "alt", t))
                    spalten = ", ".join(f'"{s}"' for s in _spalten(con, "main", t) if s in alt)
                    con.execute(f'INSERT INTO main."{t}" ({spalten}) SELECT {spalten} FROM alt."{t}"')
                if con.execute("SELECT 1 FROM main.sqlite_master WHERE name = 'sqlite_sequence'").fetchone():
                    marken = ", ".join("?" for _ in BLEIBT)
                    con.execute(f"DELETE FROM main.sqlite_sequence WHERE name NOT IN ({marken})", tuple(BLEIBT))
                    if "sqlite_sequence" in in_alt:
                        con.execute("INSERT INTO main.sqlite_sequence (name, seq) SELECT name, seq FROM alt.sqlite_sequence"
                                    f" WHERE name NOT IN ({marken})", tuple(BLEIBT))
                verletzt = con.execute("PRAGMA main.foreign_key_check").fetchall()
                if verletzt:
                    raise Unbrauchbar("Die Sicherung passt nicht zu dieser Datenbank "
                                      f"({len(verletzt)} Verweise gehen ins Leere, zuerst in „{verletzt[0][0]}“).")
                # Die Nummern der Sicherung tragen andere Bilder als die von eben: Adressen der Vorschaubilder erneuern
                con.execute(f"PRAGMA application_id = {leeren.bildstand(con) + 1:d}")
                con.execute("INSERT INTO historie (nutzer_id, tabelle, datensatz, aktion, quelle, neu)"
                            " VALUES (?, 'sicherung', 0, 'geaendert', ?, ?)",
                            (nutzer, QUELLE, json.dumps(hinweis or {}, ensure_ascii=False)))
                ergebnis = leeren.zahlen(con, nutzer)
        finally:
            if not con.in_transaction:
                con.execute("DETACH DATABASE alt")
    finally:
        con.close()
    return ergebnis
