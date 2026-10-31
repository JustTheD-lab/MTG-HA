"""Katalog der Scryfall-Drucke (nur PC, nicht Raspberry Pi).

Der Katalog ist eine eigene SQLite-Datei (`data/katalog.sqlite`), gebaut aus den lokalen
Scryfall-Daten (`python -m binderapp.katalog.bau`). Er dient

- dem Befüllen der Tabelle `kartendaten` der App-Datenbank (`kartendaten.uebernehmen`),
- der Namens- und Drucksuche der Prüfansicht (`suche`).

Auf dem Raspberry Pi gibt es keinen Katalog: `oeffnen()` liefert dort `None`, und die
Funktionen, die ihn brauchen, werden abgeschaltet.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

PROJEKT = Path(__file__).resolve().parents[3]
STANDARD_KATALOG = PROJEKT / "data" / "katalog.sqlite"
STANDARD_BILDER = PROJEKT / "data" / "scryfall" / "bilder" / "normal"

# Version des Katalogformats (steht in der Tabelle `meta`). 2: Spalte druck.cardmarket_id (Preise, 31.10.2026).
VERSION = 2


def bilddatei(scryfall_id: str, ordner: str | Path | None = None) -> Path | None:
    """Pfad der lokalen Scryfall-Bilddatei eines Drucks oder `None`.

    Einseitige Karten liegen als `<id>.jpg`, mehrseitige (Verwandlung, Modal, Umkehrkarten,
    Kunstkarten) als `<id>_0.jpg` (Vorderseite) und `<id>_1.jpg` (Rückseite).
    """
    basis = Path(ordner) if ordner is not None else STANDARD_BILDER
    for name in (f"{scryfall_id}.jpg", f"{scryfall_id}_0.jpg"):
        if (basis / name).is_file():
            return basis / name
    return None


def oeffnen(pfad: str | Path | None = None) -> sqlite3.Connection | None:
    """Öffnet den Katalog nur lesend. Fehlt die Datei, kommt `None` zurück."""
    datei = Path(pfad) if pfad is not None else STANDARD_KATALOG
    if not datei.is_file():
        return None
    verbindung = sqlite3.connect(f"{datei.resolve().as_uri()}?mode=ro", uri=True)
    verbindung.row_factory = sqlite3.Row
    return verbindung
