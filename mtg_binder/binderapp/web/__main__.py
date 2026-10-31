"""Startet den Server: python -m binderapp.web [--host 0.0.0.0] [--port 8000] [--daten data/app]
[--fotos data/photos] [--konfig data/konfig.toml] [--katalog data/katalog.sqlite|keiner]

Zweite Instanz auf einer Kopie (Tests, Abnahme am iPhone), siehe eval/kopie_anlegen.py:
    python -m binderapp.web --port 8001 --daten work/kopie/app
"""
import argparse
import os

import uvicorn

teil = argparse.ArgumentParser(description="Bücherregal und Binder im Heimnetz bereitstellen")
teil.add_argument("--host", default="0.0.0.0", help="0.0.0.0 = im Heimnetz erreichbar")
teil.add_argument("--port", type=int, default=8000)
teil.add_argument("--daten", help="Ordner mit binder.sqlite und bilder/ (Standard: data/app)")
teil.add_argument("--fotos", help="Ablage der Originale (Standard: Ordner photos/ neben --daten)")
teil.add_argument("--konfig", help="Passwort-Datei (Standard: data/konfig.toml)")
teil.add_argument("--katalog", help="Scryfall-Katalog für die Prüfansicht (Standard: data/katalog.sqlite; "
                                    "„keiner“ = ohne, wie auf dem Raspi)")
args = teil.parse_args()
if args.katalog:
    os.environ["BINDER_KATALOG"] = args.katalog
if args.daten:
    os.environ["BINDER_DATEN"] = args.daten
if args.fotos:
    os.environ["BINDER_FOTOS"] = args.fotos
if args.konfig:
    os.environ["BINDER_KONFIG"] = args.konfig

# Ein Prozess, ohne Neustart-Überwachung: so bleibt der Speicherbedarf auf dem Raspi klein.
uvicorn.run("binderapp.web.server:app", host=args.host, port=args.port, workers=1,
            log_level="warning", access_log=False)
