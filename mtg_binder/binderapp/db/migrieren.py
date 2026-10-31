"""Bringt die Datenbank auf den Stand von schema.sql (Version 2: Upload und Leerseiten;
Version 3: Bulkkisten, ManaBox-Import, Kartendaten).

Aufruf im Projektordner (PowerShell), bei beendetem Server und Verarbeiter:
    $env:PYTHONPATH = "src"; .venv\\Scripts\\python.exe -m binderapp.db.migrieren [--db data/app/binder.sqlite]

Ablauf: Sicherung der Datei nach data/sicherung/ (überschreibt nie etwas), dann Migration in einer
Transaktion, danach WAL-Modus. Wiederholbar: Ist die Datenbank schon aktuell, ändert sich
nichts (auch keine neue Sicherung). Zum Beleg zählt das Programm jede Tabelle vorher und nachher.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from binderapp.db import datenbank

STANDARD = Path(__file__).resolve().parents[3] / "data" / "app" / "binder.sqlite"
ZAEHLEN = ("nutzer", "foto", "binder", "seite", "tasche", "karte", "historie")


def zaehlen(con) -> dict[str, int]:
    return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ZAEHLEN}


def kartendaten_fuellen(pfad: Path, katalog: Path | None = None) -> dict | None:
    """Füllt die abgeleitete Tabelle `kartendaten` aus dem Katalog (nur fehlende Drucke, ändert keine Karte).
    Gibt die Zählungen zurück oder None, wenn kein Katalog da ist (zum Beispiel auf dem Raspberry Pi)."""
    from binderapp.katalog import kartendaten, oeffnen
    kat = oeffnen(katalog)
    if kat is None:
        return None
    con = datenbank.verbinden(pfad)
    try:
        with datenbank.schreiben(con):
            return kartendaten.uebernehmen(con, kat)
    finally:
        con.close()
        kat.close()


def migriere_datei(pfad: Path, sicherung: Path | None = None) -> dict:
    """Sichert und migriert `pfad`. Gibt Version vorher/nachher, Schritte, Sicherung und Zählungen zurück."""
    pfad = Path(pfad)
    if not pfad.exists():
        raise FileNotFoundError(f"Datenbank fehlt: {pfad}")
    sicherung = Path(sicherung) if sicherung else pfad.parent.parent / "sicherung"
    con = datenbank.verbinden(pfad)
    try:
        vorher_version = datenbank.version(con)
        if vorher_version > datenbank.VERSION:  # nie eine neuere Datenbank still zurückstufen
            raise RuntimeError(f"{pfad} ist Version {vorher_version}, dieses Programm kennt nur {datenbank.VERSION}.")
        vorher = zaehlen(con)
        ergebnis = {"version_vorher": vorher_version, "sicherung": None, "schritte": [], "vorher": vorher}
        if vorher_version < datenbank.VERSION or datenbank.fehlende_spalten(con):
            ergebnis["sicherung"] = str(datenbank.sichern(pfad, sicherung))
        ergebnis["schritte"] = datenbank.migrieren(con)
        datenbank.wal(con)
        ergebnis["version_nachher"] = datenbank.version(con)
        ergebnis["nachher"] = zaehlen(con)
        if ergebnis["nachher"] != vorher:
            raise RuntimeError(f"Zählung nach der Migration weicht ab: {vorher} -> {ergebnis['nachher']}")
        return ergebnis
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    teil = argparse.ArgumentParser(description=f"Datenbank sichern und auf Version {datenbank.VERSION} bringen.")
    teil.add_argument("--db", type=Path, default=STANDARD)
    teil.add_argument("--sicherung", type=Path, help="Ordner der Sicherung (Standard: data/sicherung)")
    teil.add_argument("--katalog", type=Path, help="Scryfall-Katalog für die Kartendaten (Standard: data/katalog.sqlite)")
    teil.add_argument("--ohne-kartendaten", action="store_true", help="Kartendaten nicht aus dem Katalog füllen")
    args = teil.parse_args(argv)
    from binderapp.db.importieren import geschlossen
    if args.db.exists() and not geschlossen(args.db):
        print(f"Abbruch: {args.db} ist noch geöffnet (laufen Server oder Verarbeiter?). Erst Beenden.bat.",
              file=sys.stderr)
        return 1
    e = migriere_datei(args.db, args.sicherung)
    print(f"Datenbank: {args.db}")
    print(f"Version: {e['version_vorher']} -> {e['version_nachher']}")
    print("Sicherung:", e["sicherung"] or "nicht nötig (schon aktuell)")
    print("Schritte:", ", ".join(e["schritte"]) or "keine")
    print("Zeilen je Tabelle (unverändert):", ", ".join(f"{k} {v}" for k, v in e["nachher"].items()))
    if not args.ohne_kartendaten:
        k = kartendaten_fuellen(args.db, args.katalog)
        if k is None:
            print("Kartendaten: kein Katalog gefunden (python -m binderapp.katalog.bau), übersprungen")
        else:
            gefunden = sum(w["karten"] for w in k["gefunden"].values())
            fehlt = sum(w["karten"] for w in k["nicht_gefunden"].values())
            print(f"Kartendaten: {k['geschrieben']} Drucke neu, Karten gefunden {gefunden}, schon vorhanden "
                  f"{k['vorhanden']['karten']}, nicht gefunden {fehlt}, ohne Scryfall-ID {k['ohne_scryfall_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
