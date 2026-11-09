"""Welche PC-Erkennung zu diesem Add-on gehört (Wunsch des Nutzers, 09.11.2026). Nur Standardbibliothek.

Die Dateien der PC-Erkennung (Code-Zip, Erkennungs-exe, Setup-exe) liegen als GitHub-Release `v<Version>` im
öffentlichen Add-on-Repository; das Add-on selbst bleibt klein. `pc_version.json` neben diesem Modul schreibt
`python -m binderapp.pc.paket` beim Bauen: Version und SHA-256 je Datei. Die exe am PC lädt die Dateien von GitHub und
prüft sie gegen diese Hashes, die sie über die geschützte PC-Schnittstelle bekommt (`GET /pc/version`).

    lesen()          ganzer Inhalt (für die exe) oder None, wenn das Add-on ohne PC-Paket gebaut wurde
    oeffentlich()    Version, Download-Adresse der Setup-exe und Größe (für die Seite in der App)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DATEI = Path(__file__).with_name("pc_version.json")
REPO = "JustTheD-lab/MTG-HA"
SETUP = "MTG-Binder-Setup.exe"
VERSION = re.compile(r"\d+\.\d+\.\d+")
NAME = re.compile(r"[A-Za-z0-9._-]{1,80}")
HASH = re.compile(r"[0-9a-f]{64}")


def adresse(version: str, datei: str) -> str:
    """Download-Adresse einer Release-Datei (nur feste Bestandteile, nie aus einer Anfrage)."""
    return f"https://github.com/{REPO}/releases/download/v{version}/{datei}"


def lesen(pfad: Path | None = None) -> dict | None:
    """Prüft den Inhalt streng; was nicht passt, gilt als nicht vorhanden."""
    pfad = pfad or DATEI
    try:
        roh = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(roh, dict) or not isinstance(roh.get("version"), str) or not VERSION.fullmatch(roh["version"]):
        return None
    dateien = {}
    for name, d in (roh.get("dateien") or {}).items():
        if not NAME.fullmatch(name) or not isinstance(d, dict) or not HASH.fullmatch(str(d.get("sha256", ""))) \
                or not isinstance(d.get("groesse"), int):
            return None
        dateien[name] = {"sha256": d["sha256"], "groesse": d["groesse"], "adresse": adresse(roh["version"], name)}
    if SETUP not in dateien:
        return None
    return {"version": roh["version"], "dateien": dateien, "anforderungen": roh.get("anforderungen") or {}}


def oeffentlich(pfad: Path | None = None) -> dict | None:
    v = lesen(pfad)
    if v is None:
        return None
    s = v["dateien"][SETUP]
    return {"version": v["version"], "setup": s["adresse"], "mb": round(s["groesse"] / 1024 / 1024, 1)}
