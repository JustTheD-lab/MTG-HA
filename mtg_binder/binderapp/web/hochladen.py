"""Fotos empfangen: streamend in eine Teildatei schreiben, prüfen, erst dann übernehmen.

Der Browser schickt das Foto als rohen Inhalt der Anfrage (ein Foto je Anfrage, kein Formular).
Der Server liest es in Blöcken, zählt die Größe, rechnet die SHA-256-Prüfsumme mit und schreibt
in `.teil-<zufall>` im Zielordner. Nichts davon liegt als Ganzes im Speicher. Übernommen wird die
Datei vom Aufrufer per `os.replace` (atomar, gleicher Ordner); bricht die Übertragung ab, wird die
Teildatei gelöscht. Keine Bildverarbeitung: Geprüft wird nur der Dateianfang.
"""
from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

# HEIC/HEIF: ab Byte 4 „ftyp", danach die Marke
HEIF_MARKEN = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1"}
TEIL = ".teil-"
# Bei einer Ablehnung liest der Server den Rest der Anfrage noch ein und verwirft ihn, sonst setzt die
# Verbindung zurück und der Browser sieht nur „keine Verbindung“ statt der Meldung. Höchstens so viel:
VERWERFEN_HOECHSTENS = 256 * 1024 * 1024


class UploadFehler(Exception):
    def __init__(self, text: str, status: int):
        super().__init__(text)
        self.status = status


@dataclass
class Empfang:
    teil: Path        # Teildatei im Zielordner
    groesse: int      # Bytes
    sha256: str
    format: str       # jpeg oder heic


def format_erkennen(anfang: bytes) -> str | None:
    if anfang[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if anfang[4:8] == b"ftyp" and anfang[8:12] in HEIF_MARKEN:
        return "heic"
    return None


def groesse_text(n: int) -> str:
    return f"{n / 1024 / 1024:.0f} MB"


async def verwerfen(request) -> None:
    """Liest den Rest der Anfrage und wirft ihn weg (nichts wird gespeichert)."""
    gelesen = 0
    try:
        async for block in request.stream():
            gelesen += len(block)
            if gelesen > VERWERFEN_HOECHSTENS:
                break
    except Exception:  # Abbruch im Browser o. ä.: dann gibt es niemanden mehr, der die Antwort liest
        pass


async def empfangen(request, ordner: Path, hoechstens: int) -> Empfang:
    """Liest den Inhalt der Anfrage in eine Teildatei. Wirft UploadFehler oder ClientDisconnect;
    in beiden Fällen ist die Teildatei schon gelöscht. Bei UploadFehler ist der Rest der Anfrage
    gelesen und verworfen, damit die Antwort beim Browser ankommt."""
    try:
        return await _empfangen(request, ordner, hoechstens)
    except UploadFehler:
        await verwerfen(request)
        raise


async def _empfangen(request, ordner: Path, hoechstens: int) -> Empfang:
    angekuendigt = request.headers.get("content-length")
    if angekuendigt and angekuendigt.isdigit() and int(angekuendigt) > hoechstens:
        raise UploadFehler(f"Foto zu groß, höchstens {groesse_text(hoechstens)}.", 413)
    ordner.mkdir(parents=True, exist_ok=True)
    teil = ordner / f"{TEIL}{secrets.token_hex(8)}"
    pruefsumme = hashlib.sha256()
    groesse = 0
    anfang = b""
    art = None
    try:
        with teil.open("xb") as f:
            async for block in request.stream():
                if not block:
                    continue
                groesse += len(block)
                if groesse > hoechstens:
                    raise UploadFehler(f"Foto zu groß, höchstens {groesse_text(hoechstens)}.", 413)
                if art is None:
                    anfang += block[:16]
                    if len(anfang) >= 12:
                        art = format_erkennen(anfang)
                        if art is None:
                            raise UploadFehler("Falscher Dateityp: Bitte ein Foto als JPEG oder HEIC wählen.", 415)
                pruefsumme.update(block)
                f.write(block)
        if groesse == 0:
            raise UploadFehler("Die Datei ist leer.", 400)
        if art is None:
            raise UploadFehler("Falscher Dateityp: Bitte ein Foto als JPEG oder HEIC wählen.", 415)
    except BaseException:  # UploadFehler, ClientDisconnect (Abbruch im Browser), Plattenfehler …
        teil.unlink(missing_ok=True)
        raise
    return Empfang(teil, groesse, pruefsumme.hexdigest(), art)


def teile_aufraeumen(wurzel: Path, aelter_als: float = 3600) -> int:
    """Löscht liegengebliebene Teildateien (z. B. nach einem Absturz mitten im Hochladen)."""
    grenze = time.time() - aelter_als
    n = 0
    for p in wurzel.glob(f"*/{TEIL}*"):
        try:
            if p.stat().st_mtime < grenze:
                p.unlink()
                n += 1
        except OSError:
            pass
    return n
