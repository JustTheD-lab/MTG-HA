"""Zeilen der Listen, die die App selbst für Silhouette Card Maker (SCM) schreibt: Decks der App, Archidekt-Links und
der günstigste Druck.

Format ist Deckstats von SCM: „N [SET#Nr] Name“ oder „N Name“. Das MTG-Arena-Format von SCM nimmt als Nummer nur
Ziffern (`(\\d+)`): „IMA-177“ (The List) fiel dort ganz durch, „123a“ wurde zu „123“ (gefunden am 07.11.2026 mit einem
Archidekt-Deck). Deckstats nimmt jede Nummer ohne „]“ und erlaubt Zeilen ohne Druck in derselben Liste.
"""
from __future__ import annotations

import re

FORMAT = "deckstats"


def zeile(anzahl: int, name: str, satz: str | None = None, nummer: str | None = None) -> str:
    name = str(name).replace("\n", " ").replace("[", "(").replace("]", ")").strip()
    nummer = str(nummer or "").strip()
    if satz and nummer and re.fullmatch(r"\w+", str(satz)) and re.fullmatch(r"[^\]\s]+", nummer):
        return f"{anzahl} [{str(satz).upper()}#{nummer}] {name}"
    return f"{anzahl} {name}"
