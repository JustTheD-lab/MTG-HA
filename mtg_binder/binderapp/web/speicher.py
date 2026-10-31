"""Speicherorte des Servers und ihre Erreichbarkeit (Add-on: Fotos und Vorschaubilder auf dem Netzlaufwerk).

Die Datenbank liegt immer lokal (SQLite nie auf einem Netzlaufwerk). Fotos und Vorschaubilder können auf
einem Netzlaufwerk liegen, das Home Assistant unter /share oder /media einbindet. Fällt es aus, bleibt der
Einhängepunkt oft als leerer Ordner auf der lokalen Platte stehen; Schreiben dorthin würde den knappen
Speicher des Mini-PCs füllen. Mit `geraet_pruefen` gilt ein Ordner deshalb nur als erreichbar, wenn er
existiert und auf einem anderen Gerät liegt als die Datenbank (anderes `st_dev`, also wirklich eingehängt).

    s = Speicher(bilder=…, fotos=…, lokal=…, geraet_pruefen=True)
    s.stand()        {"bilder": bool, "fotos": bool, "netzlaufwerk": bool, "meldung": str | None}
    s.fotos_ok()     darf hochgeladen werden?
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

GUELTIG = 5.0  # Sekunden: so lange gilt ein Ergebnis (stat auf einem Netzlaufwerk kann dauern)
MELDUNG = ("Das Netzlaufwerk ist nicht erreichbar. Blättern, Suchen und Prüfen gehen weiter, aber ohne Bilder; "
           "Hochladen und Erkennung sind gesperrt, bis es wieder da ist.")


class Speicher:
    def __init__(self, bilder: Path, fotos: Path, lokal: Path, geraet_pruefen: bool = False):
        self.bilder = Path(bilder)
        self.fotos = Path(fotos)
        self.lokal = Path(lokal)          # Ordner der Datenbank (Vergleich des Geräts)
        self.geraet_pruefen = geraet_pruefen
        self._sperre = threading.Lock()
        self._zeit = 0.0
        self._stand: dict | None = None

    def _erreichbar(self, ordner: Path) -> bool:
        if not self.geraet_pruefen:  # PC und lokale Kopien: Ordner entstehen bei Bedarf, nichts sperren
            return True
        try:
            lokal = os.stat(self.lokal).st_dev
            if not ordner.is_dir():
                # Unterordner (fotos/, bilder/) auf einem frisch eingehängten Laufwerk anlegen, nie lokal
                if not ordner.parent.is_dir() or os.stat(ordner.parent).st_dev == lokal:
                    return False
                ordner.mkdir(exist_ok=True)
            return os.stat(ordner).st_dev != lokal
        except OSError:
            return False

    def stand(self) -> dict:
        """Zwischengespeicherter Stand. Prüft höchstens ein Thread zugleich; hängt das Netzlaufwerk, bekommen alle
        anderen sofort den letzten Stand (oder „nicht erreichbar“) statt zu warten."""
        if self._stand is not None and time.monotonic() - self._zeit <= GUELTIG:
            return dict(self._stand)
        if not self._sperre.acquire(blocking=False):
            return dict(self._stand) if self._stand is not None else {
                "bilder": False, "fotos": False, "netzlaufwerk": self.geraet_pruefen, "meldung": MELDUNG}
        try:
            bilder, fotos = self._erreichbar(self.bilder), self._erreichbar(self.fotos)
            self._stand = {"bilder": bilder, "fotos": fotos, "netzlaufwerk": self.geraet_pruefen,
                           "meldung": None if bilder and fotos else MELDUNG}
            self._zeit = time.monotonic()
            return dict(self._stand)
        finally:
            self._sperre.release()

    def fotos_ok(self) -> bool:
        return self.stand()["fotos"]

    def bilder_ok(self) -> bool:
        return self.stand()["bilder"]
