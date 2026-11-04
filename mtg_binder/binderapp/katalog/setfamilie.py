"""Sets für den Bulk-Scan suchen und ihre „Familie“ vorschlagen (Wunsch des Nutzers, 04.11.2026), nur lesend.

Wählt der Nutzer ein Set, schlägt die App alle Set-Codes vor, die mit ihm erschienen sind. Vorausgewählt ist, was
in Boostern und Decks des Sets vorkommt; nach der Antwort des Nutzers für Reality Fracture: Hauptset (fra),
Commander-Set (frc) und die Special Guests (spg) vom selben Tag. Allgemein:

- vorgewählt: Sets vom selben Erscheinungstag mit einer Set-Art aus `VORWAHL_ARTEN` (Hauptset, Commander, …) und
  „spg“, wenn Special Guests am selben Tag erschienen sind;
- angeboten, aber nicht vorgewählt: die übrigen Sets vom selben Tag (Promos, Tokens, Art Series, …).

Scryfalls Kennzeichen „booster“ hilft hier nicht: Es ist bei neuen Sets oft leer (Stand 11/2026).
"""
from __future__ import annotations

import re

SET_MUSTER = re.compile(r"[a-z0-9]{2,8}")
# „masterpiece“: Bonus-Reihen in Boostern (z. B. Stardates bei Star Trek)
VORWAHL_ARTEN = {"expansion", "core", "draft_innovation", "masters", "commander", "funny", "starter", "masterpiece"}
SPECIAL_GUESTS = "spg"
ART_TEXT = {"expansion": "Hauptset", "core": "Hauptset", "draft_innovation": "Hauptset", "masters": "Masters-Set",
            "masterpiece": "Bonus-Reihe in Boostern",
            "commander": "Commander", "promo": "Promos", "token": "Tokens", "memorabilia": "Art Series und Beigaben",
            "alchemy": "Alchemy (digital)", "funny": "Un-Set", "starter": "Einsteigerset", "box": "Box-Set"}


def _art(kat, code: str) -> str | None:
    """Häufigste Set-Art der Drucke eines Sets (Spalte set_art im Katalog)."""
    z = kat.execute("SELECT set_art, COUNT(*) AS n FROM druck WHERE set_code = ? GROUP BY set_art ORDER BY n DESC "
                    "LIMIT 1", (code,)).fetchone()
    return z[0] if z else None


def suchen(kat, text: str, hoechstens: int = 15) -> list[dict]:
    """Sets nach Code oder Namen (Teilwort, ohne Groß/Klein); genauer Code zuerst, sonst die jüngsten zuerst.
    Ohne Suchtext die jüngsten Sets mit Hauptset-Art."""
    text = (text or "").strip().lower()[:60]
    if text:
        muster = "%" + text.replace("\\", "").replace("%", "").replace("_", "") + "%"
        zeilen = kat.execute(
            "SELECT set_code, name, erschienen, drucke FROM satz WHERE set_code = ? OR lower(name) LIKE ? "
            "ORDER BY set_code = ? DESC, erschienen DESC LIMIT ?", (text, muster, text, hoechstens)).fetchall()
    else:
        zeilen = kat.execute(
            "SELECT s.set_code, s.name, s.erschienen, s.drucke FROM satz s WHERE EXISTS (SELECT 1 FROM druck d "
            "WHERE d.set_code = s.set_code AND d.set_art IN ('expansion', 'core', 'draft_innovation', 'masters')) "
            "ORDER BY s.erschienen DESC LIMIT ?", (hoechstens,)).fetchall()
    return [{"code": z[0], "name": z[1], "erschienen": z[2], "drucke": z[3]} for z in zeilen]


def familie(kat, code: str) -> list[dict] | None:
    """Das Set und alle Sets vom selben Erscheinungstag, mit Vorschlag (`vorwahl`) und Art. None: Set unbekannt."""
    code = (code or "").strip().lower()
    if not SET_MUSTER.fullmatch(code):
        return None
    basis = kat.execute("SELECT set_code, name, erschienen, drucke FROM satz WHERE set_code = ?", (code,)).fetchone()
    if basis is None:
        return None
    aus = []
    zeilen = [basis]
    if basis[2]:
        zeilen += kat.execute("SELECT set_code, name, erschienen, drucke FROM satz WHERE erschienen = ? AND set_code != ?"
                              " ORDER BY name", (basis[2], code)).fetchall()
    for z in zeilen:
        if z[0] == SPECIAL_GUESTS:
            continue
        art = _art(kat, z[0])
        aus.append({"code": z[0], "name": z[1], "drucke": z[3], "art": ART_TEXT.get(art or "", art or ""),
                    "vorwahl": z[0] == code or art in VORWAHL_ARTEN})
    # Special Guests: ein fortlaufendes Set; vorgeschlagen, wenn Drucke am selben Tag erschienen sind
    if basis[2] and code != SPECIAL_GUESTS:
        n = kat.execute("SELECT COUNT(*) FROM druck WHERE set_code = ? AND erschienen = ?",
                        (SPECIAL_GUESTS, basis[2])).fetchone()[0]
        if n:
            name = kat.execute("SELECT name FROM satz WHERE set_code = ?", (SPECIAL_GUESTS,)).fetchone()
            aus.append({"code": SPECIAL_GUESTS, "name": name[0] if name else "Special Guests", "drucke": n,
                        "art": f"{n} Drucke vom selben Tag", "vorwahl": True})
    # gewähltes Set zuerst, dann die Vorschläge, dann der Rest
    return sorted(aus, key=lambda s: (s["code"] != code, not s["vorwahl"]))


def bekannt(kat, codes: list[str]) -> set[str]:
    """Welche der Codes der Katalog kennt."""
    if not codes:
        return set()
    platz = ", ".join("?" for _ in codes)
    return {z[0] for z in kat.execute(f"SELECT set_code FROM satz WHERE set_code IN ({platz})", codes)}
