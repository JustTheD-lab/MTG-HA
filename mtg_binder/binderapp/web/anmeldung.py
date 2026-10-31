"""Anmeldung für die schreibenden Routen: ein Passwort, ein Sitzungs-Cookie.

- Das Passwort steht nie im Klartext in einer Datei. `data/konfig.toml` (nicht in Git) enthält nur
  einen PBKDF2-Hash und ein zufälliges Geheimnis für die Cookies; beides setzt der Nutzer selbst mit
  `python -m binderapp.web.passwort`. Ohne diese Datei bleiben alle schreibenden Routen gesperrt.
- Cookie `binder_sitzung` = Ablauf.Zufall.Signatur (HMAC-SHA256 mit dem Geheimnis), HttpOnly,
  SameSite=Lax, 30 Tage. Ein neues Passwort erzeugt ein neues Geheimnis; alte Cookies gelten dann nicht mehr.
- Vergleiche in konstanter Zeit (`hmac.compare_digest`). Nach 5 Fehlversuchen innerhalb von
  15 Minuten ist die Anmeldung von dieser Adresse aus 5 Minuten gesperrt.
- Restrisiko: Im Heimnetz läuft alles über HTTP, Passwort und Cookie gehen also unverschlüsselt über
  das Netz. HTTPS kommt mit der Einrichtung des Raspberry Pi.

Nur Standardbibliothek (läuft auf dem Raspberry Pi 3).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import tomllib
from pathlib import Path

ITERATIONEN = 200_000          # PBKDF2: auf dem Raspberry Pi 3 etwa eine Sekunde je Anmeldung
COOKIE = "binder_sitzung"
DAUER = 30 * 24 * 3600         # Sekunden, die eine Anmeldung gilt
FEHLVERSUCHE = 5
FENSTER = 15 * 60
SPERRE = 5 * 60
MAX_FOTO_MB = 60


def hash_erzeugen(passwort: str, iterationen: int = ITERATIONEN) -> str:
    salz = secrets.token_bytes(16)
    wert = hashlib.pbkdf2_hmac("sha256", passwort.encode("utf-8"), salz, iterationen)
    return f"pbkdf2_sha256${iterationen}${salz.hex()}${wert.hex()}"


def passwort_pruefen(passwort: str, gespeichert: str) -> bool:
    try:
        art, iterationen, salz, wert = gespeichert.split("$")
        if art != "pbkdf2_sha256":
            return False
        probe = hashlib.pbkdf2_hmac("sha256", passwort.encode("utf-8"), bytes.fromhex(salz), int(iterationen))
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(probe.hex(), wert)


def konfig_text(passwort_hash: str, geheimnis: str, max_foto_mb: int = MAX_FOTO_MB) -> str:
    return (
        "# Konfiguration der Binder-App. Liegt in data/ und damit nicht in Git.\n"
        "# Passwort ändern: python -m binderapp.web.passwort (danach gelten alte Anmeldungen nicht mehr).\n"
        "[schreiben]\n"
        f'passwort_hash = "{passwort_hash}"\n'
        f'geheimnis = "{geheimnis}"\n'
        f"max_foto_mb = {int(max_foto_mb)}\n"
    )


class Anmeldung:
    """Liest die Konfiguration (neu, sobald sich die Datei ändert) und verwaltet Cookies und Sperren."""

    def __init__(self, konfig: Path):
        self.pfad = Path(konfig)
        self._stand = None
        self._werte: dict = {}
        self._fehler: dict[str, list[float]] = {}
        self._gesperrt: dict[str, float] = {}

    def werte(self) -> dict:
        try:
            stand = self.pfad.stat().st_mtime_ns
        except FileNotFoundError:
            self._stand, self._werte = None, {}
            return {}
        if stand != self._stand:
            with self.pfad.open("rb") as f:
                self._werte = tomllib.load(f).get("schreiben", {})
            self._stand = stand
        return self._werte

    def eingerichtet(self) -> bool:
        w = self.werte()
        return bool(w.get("passwort_hash")) and len(str(w.get("geheimnis", ""))) >= 32

    def max_foto_bytes(self) -> int:
        return int(self.werte().get("max_foto_mb", MAX_FOTO_MB)) * 1024 * 1024

    def _signatur(self, text: str) -> str:
        geheimnis = str(self.werte()["geheimnis"]).encode("utf-8")
        return hmac.new(geheimnis, text.encode("ascii"), hashlib.sha256).hexdigest()

    def cookie_erzeugen(self) -> str:
        text = f"{int(time.time()) + DAUER}.{secrets.token_hex(8)}"
        return f"{text}.{self._signatur(text)}"

    def gueltig(self, wert: str | None) -> bool:
        if not wert or not self.eingerichtet():
            return False
        teile = wert.split(".")
        if len(teile) != 3 or not teile[0].isdigit():
            return False
        text = f"{teile[0]}.{teile[1]}"
        try:
            richtig = hmac.compare_digest(self._signatur(text), teile[2])
        except (TypeError, ValueError):
            return False
        return richtig and int(teile[0]) > time.time()

    def gesperrt_bis(self, adresse: str) -> float:
        bis = self._gesperrt.get(adresse, 0)
        return bis if bis > time.time() else 0

    def anmelden(self, passwort: str, adresse: str) -> tuple[bool, str]:
        """Prüft das Passwort. Gibt (ok, Meldung) zurück; bei Erfolg ist die Meldung leer."""
        if not self.eingerichtet():
            return False, "Schreiben ist nicht eingerichtet: Bitte am PC zuerst ein Passwort festlegen."
        jetzt = time.time()
        if self.gesperrt_bis(adresse):
            minuten = max(1, round((self._gesperrt[adresse] - jetzt) / 60))
            return False, f"Zu viele Fehlversuche. Bitte in {minuten} Minuten erneut versuchen."
        if passwort_pruefen(str(passwort or ""), str(self.werte()["passwort_hash"])):
            self._fehler.pop(adresse, None)
            return True, ""
        versuche = [t for t in self._fehler.get(adresse, []) if t > jetzt - FENSTER] + [jetzt]
        self._fehler[adresse] = versuche
        if len(versuche) >= FEHLVERSUCHE:
            self._gesperrt[adresse] = jetzt + SPERRE
            self._fehler.pop(adresse, None)
            return False, "Zu viele Fehlversuche. Bitte in 5 Minuten erneut versuchen."
        return False, "Das Passwort stimmt nicht."
