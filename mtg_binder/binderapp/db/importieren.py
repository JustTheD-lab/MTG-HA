"""Füllt data/app/binder.sqlite aus Binder-Zuordnung, Erkennung und Prüfurteilen.

Aufruf im Projektordner (PowerShell):
    $env:PYTHONPATH = "src"; .venv\\Scripts\\python.exe -m binderapp.db.importieren [--neu]

Ohne --neu bricht der Import ab, wenn die Datenbank schon existiert. Mit --neu wird die alte
Datei vorher in binder.sqlite.bak umbenannt. Enthält sie Daten, die der Import nicht wiederherstellen
kann (hochgeladene Fotos, Leerseiten, Änderungen in der Oberfläche), verweigert --neu; nur mit
--neu --erzwingen geht es weiter, und dann erst nach einer Sicherung in data/sicherung/.
Geschrieben wird nur über binderapp.db.datenbank (anlegen, einfuegen, aendern), damit jede
Änderung in der Tabelle historie landet.

Die Quellen werden nur gelesen:
- Entwicklungsset: Urteile aus dem Vergleich mit eval/soll_karten_entwicklung.csv.
- Abnahmeset: Urteile aus der Prüfliste eval/pruefung_abnahme.csv (nur lesen, nie messen).
- Scryfall: data/scryfall/all_cards.jsonl.gz, einmal gestreamt, nur die gesuchten Karten gesammelt.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from binderapp.db import datenbank

PROJEKT = Path(__file__).resolve().parents[3]
DATEN = PROJEKT / "data"
DB_PFAD = DATEN / "app" / "binder.sqlite"
ZUORDNUNG = DATEN / "binder_zuordnung.csv"
FOTOS = DATEN / "photos"
SCRYFALL = DATEN / "scryfall" / "all_cards.jsonl.gz"

ERKENNUNG = "c804be1"
NUTZER = "Daniel"
URTEILE = ("stimmt", "druck_falsch", "falsch", "keine_karte")
KOPF = re.compile(r'\{"object":"card","id":"([0-9a-f-]{36})"')

QUELLEN = {
    "entwicklung": {
        "erkennung": PROJEKT / "work" / "zf-final" / "ergebnis.csv",
        "taschen": PROJEKT / "work" / "ausschnitte" / "ausschnitte.csv",
        "wahrheit": PROJEKT / "eval" / "soll_karten_entwicklung.csv",
    },
    "abnahme": {
        "erkennung": PROJEKT / "work" / "abnahme" / "zf" / "ergebnis.csv",
        "taschen": PROJEKT / "work" / "abnahme" / "ausschnitte" / "ausschnitte.csv",
        "urteile": PROJEKT / "eval" / "pruefung_abnahme.csv",
        "taschenzahlen": PROJEKT / "eval" / "soll_abnahme.csv",
    },
}

# Erwartete Prüfwerte laut Auftrag. Eine Abweichung ergibt Exitcode 1.
ERWARTET = {
    "binder": 3,
    "seite": 25,
    "tasche": 190,
    "tasche_karte": 166,
    "tasche_rueckseite": 15,
    "tasche_leer": 9,
    "tasche_unsicher": 0,
    "karte": 166,
    "karte_entwicklung": 57,
    "karte_abnahme": 109,
    "status_sicher": 147,
    "status_name_sicher": 13,
    "status_unsicher": 6,
    "geprueft": 166,
    "karte_ohne_historie": 0,
    "korrekturen": 2,
    "historie": 600,
    "taschenzahlen_abweichung": 0,
}


@dataclass
class Urteil:
    art: str                   # stimmt, druck_falsch, falsch oder keine_karte
    richtig: dict[str, str]    # richtig_name/_set/_nummer/_sprache; leer = Wert behalten
    oracle: str | None = None  # Wahrheit aus dem Entwicklungsset, sonst None


@dataclass
class Tasche:
    satz: str                  # Ausschnitt ohne .png, z. B. IMG_5451_z1s1
    foto: str                  # z. B. IMG_5451
    menge: str                 # entwicklung oder abnahme
    zeile: int
    spalte: int
    erk: dict[str, str]        # Zeile aus der Erkennung
    aus_status: str            # Taschenstatus aus dem Ausschnitt-Lauf
    urteil: Urteil | None = None
    ersatz: str | None = None  # Tascheninhalt bei keine_karte: rueckseite, leer oder unsicher


# ---------------------------------------------------------------- Lesen


def lese_csv(pfad: Path) -> list[dict[str, str]]:
    """Liest eine CSV-Datei (UTF-8, auch mit BOM) als Liste von Zeilen."""
    with pfad.open(encoding="utf-8-sig", newline="") as f:
        return [{k: (v or "").strip() for k, v in z.items()} for z in csv.DictReader(f)]


def satzname(datei: str) -> str:
    """Dateiname eines Ausschnitts ohne .png, z. B. IMG_5451_z1s1."""
    return datei.removesuffix(".png")


def lese_erkennung(pfad: Path) -> dict[str, dict[str, str]]:
    ergebnis: dict[str, dict[str, str]] = {}
    for z in lese_csv(pfad):
        satz = satzname(z["datei"])
        if satz in ergebnis:
            raise ValueError(f"{pfad.name}: Tasche doppelt: {satz}")
        ergebnis[satz] = z
    return ergebnis


def urteile_abnahme(pfad: Path, erk: dict[str, dict[str, str]]) -> dict[str, Urteil]:
    """Prüfurteile aus der Prüfliste. Erkennungsfelder müssen mit dem Ergebnis übereinstimmen."""
    urteile: dict[str, Urteil] = {}
    for z in lese_csv(pfad):
        satz = z["datei"]
        e = erk.get(satz)
        if e is None:
            raise ValueError(f"Prüfliste: Tasche ohne Erkennung: {satz}")
        for feld in ("status", "name", "set", "nummer", "sprache", "scryfall_id"):
            if e[feld] != z[feld]:
                raise ValueError(f"{satz}: Feld {feld} weicht ab (Erkennung {e[feld]!r}, Prüfliste {z[feld]!r})")
        if abs(float(e["konfidenz"]) - float(z["konfidenz"])) > 1e-6:
            raise ValueError(f"{satz}: Konfidenz weicht ab")
        if z["urteil"] not in URTEILE:
            raise ValueError(f"{satz}: unbekanntes Urteil {z['urteil']!r}")
        richtig = {k: z[f"richtig_{k}"] for k in ("name", "set", "nummer", "sprache")}
        if z["urteil"] in ("stimmt", "keine_karte") and any(richtig.values()):
            raise ValueError(f"{satz}: Urteil {z['urteil']} mit Korrekturfeldern")
        urteile[satz] = Urteil(z["urteil"], richtig)
    if set(urteile) != set(erk):
        raise ValueError("Prüfliste und Erkennung haben unterschiedliche Taschen")
    return urteile


def urteile_entwicklung(pfad: Path, erk: dict[str, dict[str, str]]) -> dict[str, Urteil]:
    """Urteile des Entwicklungssets aus dem Vergleich mit der Wahrheit."""
    wahrheit = {z["datei"]: z for z in lese_csv(pfad)}
    if set(wahrheit) != set(erk):
        raise ValueError("Wahrheit und Erkennung haben unterschiedliche Taschen")
    urteile: dict[str, Urteil] = {}
    for satz, e in erk.items():
        w = wahrheit[satz]
        if w["inhalt"] == "keine_karte":
            urteile[satz] = Urteil("keine_karte", {})
            continue
        if w["inhalt"] != "karte":
            raise ValueError(f"{satz}: unbekannter Inhalt in der Wahrheit: {w['inhalt']!r}")
        gleich = (w["oracle_id"], w["set"], w["nummer"], w["sprache"]) == (
            e["oracle_id"], e["set"], e["nummer"], e["sprache"])
        if gleich:
            urteile[satz] = Urteil("stimmt", {}, w["oracle_id"])
        else:
            art = "druck_falsch" if w["oracle_id"] == e["oracle_id"] else "falsch"
            richtig = {"name": "", "set": w["set"], "nummer": w["nummer"], "sprache": w["sprache"]}
            urteile[satz] = Urteil(art, richtig, w["oracle_id"])
    return urteile


def ersatz_keine_karte(t: Tasche, taschenzahlen: dict[str, dict[str, str]]) -> str:
    """Was steht in einer Tasche, die keine Karte ist? Entwicklungsset: Rückseite. Abnahmeset: nach Taschenzahlen."""
    if t.menge == "entwicklung":
        if t.erk["status"] != "rueckseite":
            raise ValueError(f"{t.satz}: keine_karte, aber die Erkennung ist keine Rückseite")
        return "rueckseite"
    z = taschenzahlen[t.foto + ".JPEG"]
    rueck, leer = int(z["rueckseiten"]), int(z["leer"])
    if rueck > 0 and leer == 0:
        return "rueckseite"
    if leer > 0 and rueck == 0:
        return "leer"
    return "unsicher"


def lade_menge(menge: str, q: dict[str, Path], taschenzahlen: dict[str, dict[str, str]]) -> list[Tasche]:
    erk = lese_erkennung(q["erkennung"])
    aus = {satzname(z["datei"]): z for z in lese_csv(q["taschen"])}
    if set(aus) != set(erk):
        raise ValueError(f"{menge}: Taschen-Ausschnitte und Erkennung haben unterschiedliche Taschen")
    for satz, e in erk.items():
        a = aus[satz]
        if (a["foto"], a["zeile"], a["spalte"]) != (e["foto"], e["zeile"], e["spalte"]):
            raise ValueError(f"{satz}: Position in Ausschnitten und Erkennung weicht ab")
    if "wahrheit" in q:
        urteile = urteile_entwicklung(q["wahrheit"], erk)
    else:
        urteile = urteile_abnahme(q["urteile"], erk)
    taschen = []
    for satz, e in erk.items():
        t = Tasche(
            satz=satz, foto=e["foto"], menge=menge, zeile=int(e["zeile"]), spalte=int(e["spalte"]),
            erk=e, aus_status=aus[satz]["status"], urteil=urteile.get(satz),
        )
        if t.urteil is not None and t.urteil.art == "keine_karte":
            t.ersatz = ersatz_keine_karte(t, taschenzahlen)
        taschen.append(t)
    return taschen


def ziel(t: Tasche) -> tuple[str, bool]:
    """Endzustand der Tasche und ob dafür eine Karte angelegt wird."""
    rueck = t.erk["status"] == "rueckseite"
    if t.urteil is None:  # kein Urteil: Erkennung unverändert, geprüft bleibt 0
        if rueck:
            return "rueckseite", False
        return ("unsicher" if t.aus_status == "unsicher" else "karte"), True
    art = t.urteil.art
    if art == "stimmt":  # Ausnahme: Erkennung Rückseite -> Tasche Rückseite, keine Karte
        return ("rueckseite", False) if rueck else ("karte", True)
    if art in ("druck_falsch", "falsch"):
        if rueck:
            raise ValueError(f"{t.satz}: {art}, aber die Erkennung ist Rückseite")
        return "karte", True
    return t.ersatz or "unsicher", False  # keine_karte: keine Karte anlegen


def angefragter_druck(t: Tasche) -> tuple[str, str, str]:
    """Set, Nummer, Sprache des korrigierten Drucks; leere richtig_* behalten den Erkennungswert."""
    return (
        (t.urteil.richtig.get("set") or t.erk["set"]).lower(),
        t.urteil.richtig.get("nummer") or t.erk["nummer"],
        t.urteil.richtig.get("sprache") or t.erk["sprache"],
    )


# ---------------------------------------------------------------- Scryfall


def scryfall_lesen(ids: set[str], schluessel: set[tuple[str, str, str]]):
    """Streamt all_cards einmal und sammelt nur gesuchte IDs und Schlüssel (Set, Nummer, Sprache)."""
    marken = [f'"set":"{s}"' for s in {s for s, _, _ in schluessel}]
    info: dict[str, dict] = {}
    treffer: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    with gzip.open(SCRYFALL, "rt", encoding="utf-8") as f:
        for zeile in f:
            z = zeile.strip().rstrip(",")
            if not z.startswith("{"):
                continue
            kopf = KOPF.match(z)
            if kopf and kopf.group(1) not in ids and not any(m in z for m in marken):
                continue
            obj = json.loads(z)
            karte_id = obj["id"]
            schl = (str(obj.get("set", "")).lower(), str(obj.get("collector_number", "")), str(obj.get("lang", "")))
            if karte_id in ids or schl in schluessel:
                info.setdefault(karte_id, {
                    "oracle_id": obj.get("oracle_id"), "name": obj.get("name"),
                    "set": schl[0], "nummer": schl[1], "sprache": schl[2],
                })
            if schl in schluessel:
                treffer[schl].append(karte_id)
    return info, treffer


def nachschlagen(treffer, set_code: str, nummer: str, sprache: str) -> tuple[str, str]:
    """Scryfall-ID eines Drucks; ohne Treffer in der Sprache Rückfall auf en."""
    for lang in dict.fromkeys((sprache, "en")):
        ids = list(dict.fromkeys(treffer.get((set_code.lower(), nummer, lang), [])))
        if len(ids) > 1:
            raise ValueError(f"Scryfall: {set_code} {nummer} ({lang}) ist mehrdeutig: {ids}")
        if ids:
            return ids[0], lang
    raise ValueError(f"Scryfall: kein Druck für {set_code} {nummer} ({sprache} oder en)")


def korrigiere(t: Tasche, treffer, info) -> dict:
    """Korrekturfelder für eine Karte mit druck_falsch oder falsch, aus Scryfall nachgeschlagen."""
    set_code, nummer, sprache = angefragter_druck(t)
    sid, gefunden = nachschlagen(treffer, set_code, nummer, sprache)
    sc = info[sid]
    erk_oracle = t.erk["oracle_id"]
    if t.urteil.art == "druck_falsch" and sc["oracle_id"] != erk_oracle:
        raise ValueError(f"{t.satz}: druck_falsch, aber die Oracle-ID weicht ab")
    if t.urteil.art == "falsch" and sc["oracle_id"] == erk_oracle:
        raise ValueError(f"{t.satz}: falsch, aber die Oracle-ID ist gleich")
    if t.urteil.oracle and sc["oracle_id"] != t.urteil.oracle:
        raise ValueError(f"{t.satz}: Oracle-ID weicht von der Wahrheit ab")
    if t.urteil.richtig.get("name"):
        name = t.urteil.richtig["name"]
    elif t.urteil.art == "druck_falsch":
        name = t.erk["name"]  # gleiche Karte, anderer Druck: Name in der Datenbank bleibt englisch
    elif gefunden == "en":
        name = sc["name"]
    else:
        raise ValueError(f"{t.satz}: neuer Kartenname nur aus einem englischen Druck ableitbar")
    felder = {
        "scryfall_id": sid, "oracle_id": sc["oracle_id"], "name": name,
        "set_code": sc["set"], "nummer": sc["nummer"], "sprache": sc["sprache"],
        "bild": sid, "geprueft": 1,
    }
    return {
        "felder": felder,
        "vorher": (t.erk["set"], t.erk["nummer"], t.erk["sprache"], t.erk["name"]),
        "nachher": (sc["set"], sc["nummer"], sc["sprache"], name),
        "scryfall_name": sc["name"],
        "rueckfall": gefunden != sprache,
        "art": t.urteil.art,
        "menge": t.menge,
    }


# ---------------------------------------------------------------- Datenbank


def sha256(pfad: Path) -> str:
    h = hashlib.sha256()
    with pfad.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def finde_foto(name: str) -> Path:
    treffer = sorted(FOTOS.rglob(name + ".JPEG"))
    if len(treffer) != 1:
        raise ValueError(f"Foto {name}.JPEG: {len(treffer)} Treffer unter {FOTOS}")
    return treffer[0]


def karte_felder(t: Tasche, nid: int, tid: int) -> dict:
    e = t.erk
    return {
        "nutzer_id": nid,
        "tasche_id": tid,
        "scryfall_id": e["scryfall_id"],
        "oracle_id": e["oracle_id"],
        "name": e["name"],
        "set_code": e["set"],
        "nummer": e["nummer"],
        "sprache": e["sprache"],
        "foil": 0,
        "anzahl": 1,
        "status": e["status"],
        "konfidenz": float(e["konfidenz"]) if e["konfidenz"] else None,
        "gruende": e["gruende"] or None,
        "geprueft": 0,
        "bild": e["scryfall_id"],
    }


def _nutzer_anlegen(con) -> int:
    """Legt den Nutzer an. Er braucht seine eigene ID für die Historie, deshalb direkt und mit Protokoll."""
    nid = con.execute("INSERT INTO nutzer (name) VALUES (?)", (NUTZER,)).lastrowid
    datenbank._protokoll(con, nid, "nutzer", "nutzer", nid, "angelegt", None, {"name": NUTZER})
    return nid


def schreibe_taschen(con, nid: int, sid: int, taschen: list[Tasche], korrekturen: dict) -> None:
    """Taschen und Karten einer Seite aus der Erkennung (und, falls vorhanden, den Prüfurteilen).

    Wird auch vom Verarbeiter für hochgeladene Fotos benutzt (ohne Urteile, ohne Korrekturen).
    """
    for t in sorted(taschen, key=lambda t: (t.zeile, t.spalte)):
        inhalt, mit_karte = ziel(t)
        quelle = "pruefliste" if t.urteil is not None else "erkennung"
        tid = datenbank.einfuegen(con, nid, "erkennung", "tasche", {
            "seite_id": sid, "zeile": t.zeile, "spalte": t.spalte,
            "inhalt": "karte", "ausschnitt": t.satz,
        })
        if mit_karte:
            kid = datenbank.einfuegen(con, nid, "erkennung", "karte", karte_felder(t, nid, tid))
            if t.urteil is not None:
                if t.satz in korrekturen:
                    felder = dict(korrekturen[t.satz]["felder"])
                else:
                    felder = {"geprueft": 1}
                datenbank.aendern(con, nid, "pruefliste", "karte", kid, felder)
        if inhalt != "karte":
            datenbank.aendern(con, nid, quelle, "tasche", tid, {"inhalt": inhalt})


def schreibe(con, zuo, taschen_je_foto, fotos, korrekturen) -> int:
    nid = _nutzer_anlegen(con)
    binder_ids: dict[str, int] = {}
    for z in zuo:
        if z["binder"] not in binder_ids:
            binder_ids[z["binder"]] = datenbank.einfuegen(con, nid, "zuordnung", "binder", {
                "nutzer_id": nid, "name": z["binder"], "farbe": z["farbe"], "raster": z["raster"],
                "regalplatz": len(binder_ids) + 1,
            })
        pfad = fotos[z["foto"]]
        fid = datenbank.einfuegen(con, nid, "zuordnung", "foto", {
            "nutzer_id": nid, "datei": pfad.relative_to(FOTOS).as_posix(), "sha256": sha256(pfad),
            "status": "erkannt", "erkennung": ERKENNUNG,
        })
        sid = datenbank.einfuegen(con, nid, "zuordnung", "seite", {
            "binder_id": binder_ids[z["binder"]], "nummer": z["seite"], "foto_id": fid,
        })
        schreibe_taschen(con, nid, sid, taschen_je_foto[z["foto"]], korrekturen)
    return nid


def geschlossen(pfad: Path) -> bool:
    """Hält sonst niemand die Datenbank offen? Nach einem Checkpoint löscht die letzte Verbindung
    beim Schließen die WAL-Datei; bleibt sie liegen, ist noch ein anderer Prozess verbunden."""
    con = datenbank.verbinden(pfad)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()
    return not pfad.with_name(pfad.name + "-wal").exists()


def nutzerdaten(pfad: Path) -> list[str]:
    """Was in einer vorhandenen Datenbank nicht aus dem Import stammt (Upload, Leerseiten, Änderungen
    in der Oberfläche). Leer heißt: --neu verliert nichts, was sich nicht neu importieren ließe."""
    con = datenbank.verbinden(pfad)
    try:
        def spalte(tabelle, name):
            return name in [z[1] for z in con.execute(f"PRAGMA table_info({tabelle})")]

        funde = []
        if spalte("foto", "quelle"):
            n = con.execute("SELECT COUNT(*) FROM foto WHERE quelle = 'upload'").fetchone()[0]
            if n:
                funde.append(f"{n} hochgeladene Fotos")
        if spalte("seite", "art"):
            n = con.execute("SELECT COUNT(*) FROM seite WHERE art = 'leer'").fetchone()[0]
            if n:
                funde.append(f"{n} Leerseiten")
        n = con.execute("SELECT COUNT(*) FROM historie WHERE quelle = 'nutzer' AND tabelle <> 'nutzer'").fetchone()[0]
        if n:
            funde.append(f"{n} Änderungen in der Oberfläche (Historie, Quelle „nutzer“)")
        if spalte("karte", "quelle"):  # ab Version 3: Kisten und Karten aus dem ManaBox-Import
            n = con.execute("SELECT COUNT(*) FROM kiste").fetchone()[0]
            if n:
                funde.append(f"{n} Kisten")
            n = con.execute("SELECT COUNT(*) FROM karte WHERE quelle <> 'scan'").fetchone()[0]
            if n:
                funde.append(f"{n} Karten aus ManaBox-Import oder von Hand")
        return funde
    finally:
        con.close()


def pruefe(con, korrekturen, taschenzahlen) -> tuple[list[tuple[str, int, int]], list[str]]:
    """Zählt die Datenbank nach und vergleicht mit ERWARTET. Gibt Zeilen und Abweichungen zurück."""

    def wert(sql: str, *args) -> int:
        return con.execute(sql, args).fetchone()[0]

    karte_von = (
        "SELECT COUNT(*) FROM karte k JOIN tasche t ON t.id = k.tasche_id "
        "JOIN seite s ON s.id = t.seite_id JOIN foto f ON f.id = s.foto_id WHERE f.datei LIKE ?"
    )
    ist = {
        "binder": wert("SELECT COUNT(*) FROM binder"),
        "seite": wert("SELECT COUNT(*) FROM seite"),
        "tasche": wert("SELECT COUNT(*) FROM tasche"),
        "tasche_karte": wert("SELECT COUNT(*) FROM tasche WHERE inhalt = 'karte'"),
        "tasche_rueckseite": wert("SELECT COUNT(*) FROM tasche WHERE inhalt = 'rueckseite'"),
        "tasche_leer": wert("SELECT COUNT(*) FROM tasche WHERE inhalt = 'leer'"),
        "tasche_unsicher": wert("SELECT COUNT(*) FROM tasche WHERE inhalt = 'unsicher'"),
        "karte": wert("SELECT COUNT(*) FROM karte"),
        "karte_entwicklung": wert(karte_von, "iphone-%"),
        "karte_abnahme": wert(karte_von, "abnahme/%"),
        "status_sicher": wert("SELECT COUNT(*) FROM karte WHERE status = 'sicher'"),
        "status_name_sicher": wert("SELECT COUNT(*) FROM karte WHERE status = 'name_sicher'"),
        "status_unsicher": wert("SELECT COUNT(*) FROM karte WHERE status = 'unsicher'"),
        "geprueft": wert("SELECT COUNT(*) FROM karte WHERE geprueft = 1"),
        "karte_ohne_historie": wert(
            "SELECT COUNT(*) FROM karte k WHERE NOT EXISTS (SELECT 1 FROM historie h "
            "WHERE h.tabelle = 'karte' AND h.datensatz = k.id)"),
        "korrekturen": len(korrekturen),
        "historie": wert("SELECT COUNT(*) FROM historie"),
    }

    # Abgleich der Abnahme-Taschen mit den Taschenzahlen je Foto (Karten, leer, Rückseiten)
    zeilen = con.execute(
        "SELECT f.datei AS datei, t.inhalt AS inhalt, "
        "(SELECT COUNT(*) FROM karte k WHERE k.tasche_id = t.id) AS kn "
        "FROM tasche t JOIN seite s ON s.id = t.seite_id JOIN foto f ON f.id = s.foto_id "
        "WHERE f.datei LIKE 'abnahme/%'"
    ).fetchall()
    pro: dict[str, Counter] = defaultdict(Counter)
    for r in zeilen:
        foto = r["datei"].split("/")[-1]
        pro[foto]["karten"] += r["kn"]
        pro[foto][r["inhalt"]] += 1
    abweichungen = []
    for foto, z in taschenzahlen.items():
        soll = (int(z["karten"]), int(z["leer"]), int(z["rueckseiten"]))
        ist_foto = (pro[foto]["karten"], pro[foto]["leer"], pro[foto]["rueckseite"])
        if soll != ist_foto:
            abweichungen.append(f"{foto}: Karten/leer/Rückseite Soll {soll}, Ist {ist_foto}")
    ist["taschenzahlen_abweichung"] = len(abweichungen)

    zeilen_bericht = [(k, ist[k], ERWARTET[k]) for k in ERWARTET]
    return zeilen_bericht, abweichungen


# ---------------------------------------------------------------- Ablauf


def lade_welt():
    """Liest alle Quellen, plant jede Tasche und löst die Scryfall-Korrekturen auf."""
    zuo = lese_csv(ZUORDNUNG)
    for z in zuo:
        z["seite"] = int(z["seite"])
    if len({z["foto"] for z in zuo}) != len(zuo):
        raise ValueError("binder_zuordnung.csv: Foto mehrfach")

    taschenzahlen = {z["foto"]: z for z in lese_csv(QUELLEN["abnahme"]["taschenzahlen"])}
    alle: list[Tasche] = []
    for menge, q in QUELLEN.items():
        alle.extend(lade_menge(menge, q, taschenzahlen))

    taschen_je_foto: dict[str, list[Tasche]] = defaultdict(list)
    for t in alle:
        taschen_je_foto[t.foto].append(t)
    zuo_fotos = {z["foto"] for z in zuo}
    if zuo_fotos != set(taschen_je_foto):
        raise ValueError(f"Zuordnung und Erkennung haben unterschiedliche Fotos: {zuo_fotos ^ set(taschen_je_foto)}")
    for z in zuo:
        muster = {"3x3": 9, "2x2": 4}[z["raster"]]
        if len(taschen_je_foto[z["foto"]]) != muster:
            raise ValueError(f"{z['foto']}: {len(taschen_je_foto[z['foto']])} Taschen, Raster {z['raster']} erwartet {muster}")

    fotos = {z["foto"]: finde_foto(z["foto"]) for z in zuo}

    planung = [(t, *ziel(t)) for t in alle]
    karten = [(t, mit) for t, _, mit in planung if mit]
    korrektur_taschen = [t for t in alle if t.urteil is not None and t.urteil.art in ("druck_falsch", "falsch")]

    ids = {t.erk["scryfall_id"] for t, _ in karten if t.erk["scryfall_id"]}
    schluessel: set[tuple[str, str, str]] = set()
    for t in korrektur_taschen:
        s, n, spr = angefragter_druck(t)
        schluessel.add((s, n, spr))
        schluessel.add((s, n, "en"))
    print(f"Scryfall wird gelesen (IDs: {len(ids)}, Korrektur-Schlüssel: {len(schluessel)}) ...", flush=True)
    info, treffer = scryfall_lesen(ids, schluessel)

    # Erkennungs-IDs müssen in Scryfall stehen und zu Oracle, Set und Nummer passen (hart).
    # Die Sprache wird nur gemeldet: Scryfall hat für manche Drucke keine Sprachvariante,
    # die Prüfwerte bleiben unverändert und die Frage geht an den Nutzer.
    korrigiert = {t.satz for t in korrektur_taschen}
    probleme = []
    hinweise = []
    for t, _ in karten:
        if t.satz in korrigiert:
            continue
        sid = t.erk["scryfall_id"]
        sc = info.get(sid)
        if sc is None:
            probleme.append(f"{t.satz}: Scryfall-ID {sid} nicht gefunden")
        elif (sc["oracle_id"], sc["set"], sc["nummer"]) != (t.erk["oracle_id"], t.erk["set"].lower(), t.erk["nummer"]):
            probleme.append(f"{t.satz}: Scryfall-ID passt nicht zu Oracle/Set/Nummer der Erkennung")
        elif sc["sprache"] != t.erk["sprache"]:
            hinweise.append(f"{t.satz} ({t.erk['name']}, {t.erk['set']} {t.erk['nummer']}): "
                            f"Sprache laut Erkennung {t.erk['sprache']!r}, Scryfall-Druck der Scryfall-ID {sc['sprache']!r}")
    if probleme:
        raise ValueError("Scryfall-Abgleich fehlgeschlagen:\n  " + "\n  ".join(probleme))

    korrekturen = {}
    for t in korrektur_taschen:
        korrekturen[t.satz] = korrigiere(t, treffer, info)

    return zuo, taschen_je_foto, fotos, korrekturen, taschenzahlen, alle, planung, hinweise


def bericht(alle, planung, korrekturen, taschenzahlen, ergebnis, abweichungen, hinweise) -> bool:
    ersatz = Counter(t.ersatz for t, _, _ in planung if t.urteil is not None and t.urteil.art == "keine_karte")
    ohne_urteil = sum(1 for t in alle if t.urteil is None)
    print("\nErsatzinhalt keine_karte-Taschen:", dict(ersatz))
    print("Taschen ohne Urteil:", ohne_urteil)
    angenommen = [f for f, z in taschenzahlen.items() if "angenommen" in z.get("bemerkung", "")]
    if angenommen:
        print("Hinweis: Leer-Zahl laut soll_abnahme.csv nur angenommen (nicht vom Nutzer genannt):", ", ".join(sorted(angenommen)))
    print(f"\nHinweise Sprache, Werte unverändert übernommen ({len(hinweise)}):")
    for h in hinweise:
        print("  " + h)
    print(f"\nKorrekturen ({len(korrekturen)}):")
    for satz, k in korrekturen.items():
        print(f"  {satz} ({k['menge']}, {k['art']}): {k['vorher'][0]} {k['vorher'][1]} {k['vorher'][2]} -> "
              f"{k['nachher'][0]} {k['nachher'][1]} {k['nachher'][2]}; Name {k['nachher'][3]!r}"
              f" (Scryfall: {k['scryfall_name']!r})" + (" [Rückfall auf en]" if k["rueckfall"] else ""))

    print("\nPrüfung gegen Erwartung:")
    print(f"  {'Prüfpunkt':<28}{'ist':>6}{'erwartet':>10}  ok")
    fehler = 0
    for name, ist, soll in ergebnis:
        ok = ist == soll
        fehler += 0 if ok else 1
        print(f"  {name:<28}{ist:>6}{soll:>10}  {'ja' if ok else 'NEIN'}")
    for a in abweichungen:
        print("  Abweichung Taschenzahlen:", a)
    return fehler == 0


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Füllt data/app/binder.sqlite aus Zuordnung, Erkennung und Prüfurteilen.")
    parser.add_argument("--neu", action="store_true",
                        help="Datenbank neu anlegen; die alte Datei wird vorher in binder.sqlite.bak umbenannt.")
    parser.add_argument("--erzwingen", action="store_true",
                        help="Mit --neu auch dann neu anlegen, wenn die Datenbank Upload-, Leerseiten- oder "
                             "Nutzerdaten enthält. Vorher wird sie nach data/sicherung/ gesichert.")
    args = parser.parse_args(argv)

    if DB_PFAD.exists() and not args.neu:
        print(f"Abbruch: {DB_PFAD} existiert schon. Mit --neu neu anlegen.", file=sys.stderr)
        return 1

    if DB_PFAD.exists():
        if not geschlossen(DB_PFAD):
            print(f"Abbruch: {DB_PFAD} ist noch geöffnet (laufen Server oder Verarbeiter?). "
                  "Erst beide beenden.", file=sys.stderr)
            return 1
        funde = nutzerdaten(DB_PFAD)
        if funde and not args.erzwingen:
            print(f"Abbruch: {DB_PFAD} enthält Daten, die der Import nicht wiederherstellen kann: "
                  + "; ".join(funde) + ". Nur mit --neu --erzwingen (mit Sicherung) neu anlegen.", file=sys.stderr)
            return 1
        if funde:
            print("Sicherung vor dem Überschreiben:", datenbank.sichern(DB_PFAD, DATEN / "sicherung"))

    zuo, taschen_je_foto, fotos, korrekturen, taschenzahlen, alle, planung, hinweise = lade_welt()

    backup = None
    if DB_PFAD.exists():
        backup = DB_PFAD.with_name(DB_PFAD.name + ".bak")
        if backup.exists():
            backup = DB_PFAD.with_name(f"{DB_PFAD.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
        DB_PFAD.rename(backup)
        print(f"Alte Datenbank umbenannt nach: {backup}")

    con = None
    try:
        con = datenbank.anlegen(DB_PFAD)
        schreibe(con, zuo, taschen_je_foto, fotos, korrekturen)
        con.commit()
        ergebnis, abweichungen = pruefe(con, korrekturen, taschenzahlen)
        con.close()
        con = None
    except Exception:
        if con is not None:
            con.rollback()
            con.close()
        DB_PFAD.unlink(missing_ok=True)
        if backup is not None:
            backup.rename(DB_PFAD)
        raise

    print(f"Datenbank geschrieben: {DB_PFAD}")
    ok = bericht(alle, planung, korrekturen, taschenzahlen, ergebnis, abweichungen, hinweise)
    print("\nErgebnis:", "alle Prüfpunkte stimmen" if ok else "Abweichungen gefunden")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
