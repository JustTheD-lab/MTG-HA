"""Günstigster Druck je Zeile für den Proxy-Druck (Wunsch des Nutzers, 07.11.2026).

Antworten des Nutzers: Preis Ø 7 Tage aus dem Cardmarket Price Guide (wie überall in der App; ohne Ø 7 Tage zählt Low,
wie bei Secret Lair), nur Drucke mit gutem Scan. Kandidaten sind die englischen Papierdrucke der Karte mit
Cardmarket-Produkt aus dem Katalog; ob ihr Scryfall-Bild ein richtiger Scan ist (`image_status = highres_scan`),
fragt `scans_pruefen` live bei Scryfall nach (`/cards/collection`, je Anfrage höchstens 75 Karten), weil der Katalog
das nicht kennt. Zeilen mit Set und Nummer bleiben, außer „Sets ignorieren“ ist an. Das Ergebnis ist eine Liste im
Stil von MTG Arena („N Name (SET) Nr“), die Silhouette Card Maker unverändert lädt.
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

from binderapp.katalog.suche import normalisiere
from binderapp.preise import abruf

SCRYFALL = "https://api.scryfall.com/cards/collection"
SAMMEL_MAX = 75            # Grenze von Scryfall je Anfrage
ABSTAND = 0.1              # Scryfall bittet um 50–100 ms zwischen Anfragen
KANDIDATEN = 8             # so viele der günstigsten Drucke je Karte werden auf ihren Scan geprüft
ANTWORT_MAX = 8 * 1024 * 1024
ZEITLIMIT = 30
GUTER_SCAN = "highres_scan"
# Keine normalen Karten (Gold-Rand-Sammlerdecks, Art Series, Oversized …)
OHNE_SET_ART = ("memorabilia", "minigame", "token")


class Druckwahlfehler(RuntimeError):
    pass


def _oracle(kat, name: str) -> str | None:
    """Oracle-ID zum Namen der Liste (bei „A // B“ zählt die Vorderseite), echte Karten vor Spielmarken."""
    vorne = name.split(" // ")[0].strip()
    zeile = kat.execute(
        "SELECT n.oracle_id FROM namen n LEFT JOIN orakel o ON o.oracle_id = n.oracle_id WHERE n.norm = ?"
        " ORDER BY COALESCE(o.rang, 9), n.sprache <> 'en' LIMIT 1", (normalisiere(vorne),)).fetchone()
    return zeile[0] if zeile else None


def kandidaten(kat, oracle_id: str) -> list[dict]:
    platz = ", ".join("?" * len(OHNE_SET_ART))
    return [dict(z) for z in kat.execute(
        "SELECT scryfall_id, set_code, nummer, cardmarket_id FROM druck WHERE oracle_id = ? AND sprache = 'en'"
        f" AND cardmarket_id IS NOT NULL AND COALESCE(set_art, '') NOT IN ({platz})", (oracle_id, *OHNE_SET_ART))]


def preise_lesen(preisdatei: Path, ids: set[int]) -> dict[int, float]:
    """Nonfoil-Preis je Cardmarket-Produkt: Ø 7 Tage, sonst Low; Produkte ohne Preis fehlen."""
    werte = {}
    for pid, low, avg7, _low_foil, _avg7_foil in abruf.einlesen(Path(preisdatei).read_bytes(), ids)[2]:
        wert = avg7 or low
        if wert:
            werte[pid] = wert
    return werte


def _post(url: str, daten: dict) -> dict:
    anfrage = urllib.request.Request(url, data=json.dumps(daten).encode("utf-8"), method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "MTG-Binder-Addon (Proxy-Druck)"})
    with urllib.request.urlopen(anfrage, timeout=ZEITLIMIT) as antwort:
        roh = antwort.read(ANTWORT_MAX + 1)
    if len(roh) > ANTWORT_MAX:
        raise Druckwahlfehler("Antwort von Scryfall zu groß.")
    return json.loads(roh)


def scans_pruefen(ids: list[str], senden=_post, warten=time.sleep) -> dict[str, str]:
    """{scryfall_id: image_status} über `/cards/collection`."""
    ergebnis = {}
    for beginn in range(0, len(ids), SAMMEL_MAX):
        if beginn:
            warten(ABSTAND)
        try:
            antwort = senden(SCRYFALL, {"identifiers": [{"id": i} for i in ids[beginn:beginn + SAMMEL_MAX]]})
        except (OSError, ValueError) as f:
            raise Druckwahlfehler(f"Scryfall nicht erreichbar ({f}).") from None
        for karte in antwort.get("data") or []:
            if isinstance(karte, dict) and isinstance(karte.get("id"), str):
                ergebnis[karte["id"]] = str(karte.get("image_status") or "")
    return ergebnis


def _zeile(anzahl: int, name: str, satz: str | None, nummer: str | None) -> str:
    name = name.replace("\n", " ").strip()
    return f"{anzahl} {name} ({satz.upper()}) {nummer}" if satz and nummer else f"{anzahl} {name}"


def waehlen(kat, preisdatei: Path, karten: list[dict], sets_ignorieren: bool, scans=scans_pruefen) -> dict:
    """Neue Liste (Text, Format mtga) und je Zeile, was gewählt wurde.

    `karten`: Zeilen des Läufers (`name`, `set`, `nummer`, `anzahl`). Je Zeile im Ergebnis `art`:
    `liste` (Druck aus der Liste behalten), `guenstig` (günstigster Druck mit gutem Scan, `preis` in Euro) oder
    `ohne` (kein passender Druck, Scryfall wählt wie ohne Vorzug; `grund`)."""
    plan = []
    for k in karten:
        eintrag = {"name": k["name"], "anzahl": int(k.get("anzahl") or 1), "set": k.get("set") or "",
                   "nummer": k.get("nummer") or ""}
        if eintrag["set"] and eintrag["nummer"] and not sets_ignorieren:
            eintrag["art"] = "liste"
        else:
            oracle = _oracle(kat, k["name"])
            eintrag["kandidaten"] = kandidaten(kat, oracle) if oracle else []
            if not oracle:
                eintrag.update(art="ohne", grund="Name nicht im Katalog")
        plan.append(eintrag)

    ids = {d["cardmarket_id"] for e in plan for d in e.get("kandidaten", [])}
    preise = preise_lesen(preisdatei, ids) if ids else {}
    zu_pruefen = []
    for e in plan:
        if "kandidaten" not in e or e.get("art"):
            continue
        mit_preis = sorted((d for d in e["kandidaten"] if d["cardmarket_id"] in preise),
                           key=lambda d: (preise[d["cardmarket_id"]], d["set_code"], d["nummer"]))
        e["kandidaten"] = mit_preis[:KANDIDATEN]
        if not e["kandidaten"]:
            e.update(art="ohne", grund="kein Druck mit Cardmarket-Preis")
        zu_pruefen += [d["scryfall_id"] for d in e["kandidaten"]]
    status = scans(sorted(set(zu_pruefen))) if zu_pruefen else {}

    zeilen, gewaehlt = [], []
    for e in plan:
        kandidaten_ = e.pop("kandidaten", [])
        if not e.get("art"):
            gut = next((d for d in kandidaten_ if status.get(d["scryfall_id"]) == GUTER_SCAN), None)
            if gut is None:
                e.update(art="ohne", grund=f"unter den {len(kandidaten_)} günstigsten Drucken keiner mit gutem Scan")
            else:
                e.update(art="guenstig", set=gut["set_code"], nummer=gut["nummer"],
                         preis=round(preise[gut["cardmarket_id"]], 2))
        if e["art"] == "ohne":
            zeilen.append(_zeile(e["anzahl"], e["name"], None, None))
        else:
            zeilen.append(_zeile(e["anzahl"], e["name"], e["set"], e["nummer"]))
        gewaehlt.append(e)
    return {"text": "\n".join(zeilen), "format": "mtga", "gewaehlt": gewaehlt}
