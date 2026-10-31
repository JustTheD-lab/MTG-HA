"""Gemeinsame Helfer für Routen-Module (Bulkkisten `kisten.py`, Prüfansicht `pruefen.py`).

server.py baut beim Start ein `Helfer`-Objekt und gibt es jedem Modul: `modul.routen(h)` liefert
eine Liste von Starlette-Routen, die unter /api eingehängt werden. So brauchen die Module keinen
eigenen Zugriff auf Anmeldung und Datenbankpfad, und Tests können einen Helfer ohne Server bauen.

    h.db()                       nur lesende Verbindung (mit `closing` benutzen)
    h.schreibvorgang(arbeit)     arbeit(con) in einer kurzen Schreibtransaktion (im Thread aufrufen)
    h.schreibend(handler)        Hülle für schreibende async-Routen: Passwort eingerichtet, angemeldet,
                                 gleiche Herkunft; wandelt `Fehler` in {"fehler": …} um
    await h.json_daten(request)  JSON-Objekt der Anfrage oder Fehler 400
    h.version()                  Schema-Version der Datenbank (Kisten und Kartendaten ab 3)
    h.katalog()                  nur lesende Verbindung zum Scryfall-Katalog oder None (Raspi: immer None)
    h.nutzer                     Nutzer-ID (bis zur Benutzerverwaltung 1)
    h.bilder, h.sicherungen      Ordner der Vorschaubilder und der Sicherungen (im Add-on getrennt gesetzt)
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from starlette.responses import JSONResponse

from binderapp.db.datenbank import aeltere_entfernen, schreiben, verbinden, version
from binderapp.db.datenbank import sichern as datenbank_sichern
from binderapp.db.seiten import Fehler

__all__ = ["Fehler", "Helfer", "fehler"]


def fehler(text: str, status: int = 400) -> JSONResponse:
    """Fehlerantwort mit einem verständlichen deutschen Satz."""
    return JSONResponse({"fehler": text}, status_code=status)


async def _json_daten(request) -> dict:
    try:
        daten = await request.json()
    except ValueError:
        raise Fehler("Ungültige Anfrage.")
    if not isinstance(daten, dict):
        raise Fehler("Ungültige Anfrage.")
    return daten


def _ohne_anmeldung(handler):
    """Für Tests ohne Server: wandelt nur `Fehler` um, prüft keine Anmeldung."""
    async def huelle(request):
        try:
            return await handler(request)
        except Fehler as f:
            return fehler(str(f), f.status)
    return huelle


@dataclass
class Helfer:
    datenbank: Path
    nutzer: int = 1
    schreibend: Callable = _ohne_anmeldung
    katalog_pfad: Path | None = None
    json_daten: Callable = field(default=staticmethod(_json_daten))
    bilder_ordner: Path | None = None    # Vorschaubilder (Standard: bilder/ neben der Datenbank)
    sicherung_ordner: Path | None = None  # Sicherungen (Standard: sicherung/ neben dem Datenordner)

    @property
    def bilder(self) -> Path:
        return Path(self.bilder_ordner) if self.bilder_ordner else Path(self.datenbank).parent / "bilder"

    sicherungen_behalten: int | None = None  # Add-on: nur die jüngsten n Sicherungen behalten (PC: alle)

    @property
    def sicherungen(self) -> Path:
        return Path(self.sicherung_ordner) if self.sicherung_ordner else Path(self.datenbank).parent.parent / "sicherung"

    def sichern(self) -> Path:
        """Sichert die Datenbank (vor Listen-Import und Abzug); im Add-on danach ältere Sicherungen weg."""
        ziel = datenbank_sichern(self.datenbank, self.sicherungen)
        if self.sicherungen_behalten:
            aeltere_entfernen(self.sicherungen, self.sicherungen_behalten)
        return ziel

    def db(self):
        if not Path(self.datenbank).exists():
            raise FileNotFoundError(f"Datenbank fehlt: {self.datenbank}")
        return verbinden(self.datenbank, nur_lesen=True)

    def schreibvorgang(self, arbeit):
        with closing(verbinden(self.datenbank)) as con, schreiben(con):
            return arbeit(con)

    def version(self) -> int:
        with closing(self.db()) as con:
            return version(con)

    def katalog(self):
        """Nur lesende Verbindung zum Katalog oder None, wenn er fehlt (dann sind Druckwahl und Namenssuche aus)."""
        if self.katalog_pfad is None or not Path(self.katalog_pfad).exists():
            return None
        return verbinden(self.katalog_pfad, nur_lesen=True)
