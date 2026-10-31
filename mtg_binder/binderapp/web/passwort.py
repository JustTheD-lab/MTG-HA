"""Passwort für das Schreiben (Hochladen, Binder anlegen, Leerseiten) festlegen oder ändern.

Aufruf im Projektordner, in einem eigenen Terminal (die Eingabe bleibt unsichtbar):
    $env:PYTHONPATH = "src"; .venv\\Scripts\\python.exe -m binderapp.web.passwort [--konfig data/konfig.toml]

Gespeichert wird nur ein Hash (PBKDF2-SHA256) in data/konfig.toml, nie das Passwort selbst.
Jede Änderung erzeugt ein neues Geheimnis für die Cookies: bisherige Anmeldungen gelten dann nicht mehr.
Läuft der Server schon, liest er die Datei beim nächsten Zugriff neu ein.
"""
from __future__ import annotations

import argparse
import getpass
import os
import secrets
import sys
import tomllib
from pathlib import Path

from binderapp.web.anmeldung import MAX_FOTO_MB, hash_erzeugen, konfig_text

STANDARD = Path(__file__).resolve().parents[3] / "data" / "konfig.toml"
MINDESTLAENGE = 8


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    teil = argparse.ArgumentParser(description="Passwort für das Schreiben festlegen (nur Hash wird gespeichert).")
    teil.add_argument("--konfig", type=Path, default=STANDARD)
    args = teil.parse_args(argv)

    max_mb = MAX_FOTO_MB
    if args.konfig.exists():
        with args.konfig.open("rb") as f:
            max_mb = int(tomllib.load(f).get("schreiben", {}).get("max_foto_mb", MAX_FOTO_MB))
        print(f"Vorhandene Konfiguration wird ersetzt: {args.konfig}")

    erstes = getpass.getpass(f"Neues Passwort (mindestens {MINDESTLAENGE} Zeichen): ")
    if len(erstes) < MINDESTLAENGE:
        print(f"Abbruch: Das Passwort ist kürzer als {MINDESTLAENGE} Zeichen.", file=sys.stderr)
        return 1
    if getpass.getpass("Passwort wiederholen: ") != erstes:
        print("Abbruch: Die beiden Eingaben sind verschieden.", file=sys.stderr)
        return 1

    text = konfig_text(hash_erzeugen(erstes), secrets.token_hex(32), max_mb)
    args.konfig.parent.mkdir(parents=True, exist_ok=True)
    teil_datei = args.konfig.with_name(args.konfig.name + ".neu")
    teil_datei.write_text(text, encoding="utf-8")
    os.replace(teil_datei, args.konfig)
    print(f"Gespeichert (nur der Hash): {args.konfig}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
