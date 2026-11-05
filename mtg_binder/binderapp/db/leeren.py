"""Datenbank leeren für den Neubeginn (Funktion „Datenbank löschen“ im Add-on).

Die Datei bleibt bestehen, nur ihr Inhalt verschwindet in einer einzigen Transaktion. So bleiben alle anderen
Verbindungen gültig (Preisabruf, Werte an Home Assistant, PC-Schnittstelle), und ein Fehler lässt alles, wie es war.

Erhalten bleiben nur der Nutzer und das Lebenszeichen des PCs. Neue Tabellen zählen automatisch zu den gelöschten.
Weil die Nummern danach wieder bei 1 beginnen, zählt `bildstand` hoch: Der Browser hält Vorschaubilder eine Woche
vor, und ein neues Bild unter einer alten Adresse würde sonst das alte zeigen.

    zahlen(con, nutzer)      Was gelöscht würde
    hindernis(con, nutzer)   Satz, wenn gerade nicht gelöscht werden darf, sonst None
    leeren(con)              Löscht alles (in einer Schreibtransaktion aufrufen)
    verdichten(pfad)         Gibt den Platz der Datei frei (danach, außerhalb der Transaktion)
    bildstand(con)           Wie oft schon geleert wurde (`PRAGMA application_id`, keine Schemaänderung)
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from binderapp.db import erkennung
from binderapp.db.datenbank import WARTEZEIT

BEHALTEN = frozenset({"nutzer", "dienst"})


def zahlen(con, nutzer: int) -> dict:
    def eins(sql: str, *werte) -> int:
        return con.execute(sql, werte).fetchone()[0]

    return {
        "binder": eins("SELECT COUNT(*) FROM binder WHERE nutzer_id = ?", nutzer),
        "kisten": eins("SELECT COUNT(*) FROM kiste WHERE nutzer_id = ?", nutzer),
        "decks": eins("SELECT COUNT(*) FROM deck WHERE nutzer_id = ?", nutzer),
        "karten": eins("SELECT COALESCE(SUM(anzahl), 0) FROM karte WHERE nutzer_id = ?", nutzer),
        "fotos": eins("SELECT COUNT(*) FROM foto WHERE nutzer_id = ?", nutzer),
        "scan_fotos": eins("SELECT COUNT(*) FROM scan_foto"),
        "regeln": eins("SELECT COUNT(*) FROM sortierregel WHERE nutzer_id = ?", nutzer),
        "historie": eins("SELECT COUNT(*) FROM historie WHERE nutzer_id = ?", nutzer),
    }


def hindernis(con, nutzer: int) -> str | None:
    """Solange der PC an einem Foto rechnet, wartet er mit einem Ergebnis für eine Nummer, die es danach nicht mehr
    gibt oder die schon ein neues Foto trägt. Ein abgestürzter PC sperrt nichts: Sein Lebenszeichen ist dann alt."""
    z = erkennung.letztes_lebenszeichen(con)
    if z is None or z[1] >= erkennung.FRISCH or z[0].get("zustand") == "beendet":
        return None
    offen = con.execute("SELECT COUNT(*) FROM foto WHERE nutzer_id = ? AND status = 'in_verarbeitung'",
                        (nutzer,)).fetchone()[0]
    offen += con.execute("SELECT COUNT(*) FROM scan_foto WHERE status = 'in_verarbeitung'").fetchone()[0]
    if offen:
        return "Die Erkennung am PC arbeitet gerade. Bitte warte, bis sie fertig ist, oder beende sie."
    return None


def tabellen(con) -> list[str]:
    zeilen = con.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
    return sorted(z[0] for z in zeilen if z[0] not in BEHALTEN)


def bildstand(con) -> int:
    return con.execute("PRAGMA application_id").fetchone()[0]


def leeren(con) -> None:
    """Löscht alle Inhalte. Prüft die Fremdschlüssel erst am Ende, die Reihenfolge der Tabellen spielt keine Rolle."""
    con.execute("PRAGMA defer_foreign_keys = ON")
    for tabelle in tabellen(con):   # Namen stammen aus sqlite_master, nie aus einer Anfrage
        con.execute(f'DELETE FROM "{tabelle}"')
    if con.execute("SELECT 1 FROM sqlite_master WHERE name = 'sqlite_sequence'").fetchone():
        con.execute("DELETE FROM sqlite_sequence")
    # Ganzzahl aus der eigenen Abfrage: PRAGMA nimmt keine Parameter
    con.execute(f"PRAGMA application_id = {bildstand(con) + 1:d}")


def verdichten(pfad: str | Path) -> bool:
    """Gibt den Platz frei. Liest gerade jemand, klappt es nicht; das schadet nichts, die Datei bleibt nur größer."""
    try:
        with closing(sqlite3.connect(Path(pfad), timeout=WARTEZEIT)) as con:
            con.execute("VACUUM")
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return True
    except sqlite3.OperationalError:
        return False
