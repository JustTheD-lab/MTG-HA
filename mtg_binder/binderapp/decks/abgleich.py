"""Decks in der Datenbank: anlegen, neu laden (mit Vorschau), Abgleich mit der Sammlung, Fehlt-Liste.

Abgleich (berechnet, nicht gespeichert):
- Gezählt werden Karten in Bindern und Kisten, die sicher erkannt oder geprüft sind; ungeprüfte Karten mit Status
  „unsicher“ zählen nicht (wie beim Abzug). Im Modus „name“ zählt jeder Druck der Karte, im Modus „druck“ nur
  gleiches Set und gleiche Nummer (die Sprache ist egal). Ohne Katalog-Treffer wird nach dem Namen abgeglichen.
- Jede Karte der Sammlung wird höchstens so oft vergeben, wie sie da ist; der genaue Druck kommt zuerst dran.
- Basics fallen mit „Basics auslassen“ heraus, Proxys zählen als vorhanden (nicht in der Fehlt-Liste).
Schreiben mit Historie (Quelle „nutzer“) in der Transaktion des Aufrufers.
"""
from __future__ import annotations

from collections import defaultdict

from binderapp.db.datenbank import aendern, einfuegen, loeschen

QUELLE = "nutzer"
ORTE_HOECHSTENS = 6
FELDER = ("scryfall_id", "oracle_id", "name", "set_code", "nummer", "druck_angegeben", "anzahl", "foil", "commander",
          "basic")

SQL_SAMMLUNG = """
SELECT k.id, k.name, COALESCE(kd.oracle_id, k.oracle_id) AS oracle_id, k.set_code, k.nummer, k.anzahl, k.foil,
       k.kiste_id, ki.name AS kiste_name, b.id AS binder_id, b.name AS binder_name, se.nummer AS seite,
       t.zeile, t.spalte
FROM karte k
LEFT JOIN kartendaten kd ON kd.scryfall_id = k.scryfall_id
LEFT JOIN kiste ki ON ki.id = k.kiste_id
LEFT JOIN tasche t ON t.id = k.tasche_id
LEFT JOIN seite se ON se.id = t.seite_id
LEFT JOIN binder b ON b.id = se.binder_id
WHERE k.nutzer_id = :n AND (k.kiste_id IS NOT NULL OR k.tasche_id IS NOT NULL)
  AND (k.status != 'unsicher' OR k.geprueft = 1)
  AND (COALESCE(kd.oracle_id, k.oracle_id) IN (SELECT oracle_id FROM deck_karte WHERE deck_id = :d)
       OR lower(k.name) IN (SELECT lower(name) FROM deck_karte WHERE deck_id = :d AND oracle_id IS NULL))
ORDER BY b.regalplatz, b.id, se.nummer, t.zeile, t.spalte, ki.regalplatz, ki.id, k.id
"""


def _schluessel(z: dict) -> tuple:
    """Gleiche Deckzeile beim Neu laden: Druck (oder Name), Foil, Commander."""
    druck = (z["set_code"] or "", z["nummer"] or "") if z["druck_angegeben"] else ("", "")
    return (z["oracle_id"] or z["name"].lower(), *druck, bool(z["foil"]), bool(z["commander"]))


def _zeile_felder(z: dict) -> dict:
    return {f: (int(bool(z.get(f))) if f in ("druck_angegeben", "foil", "commander", "basic") else z.get(f))
            for f in FELDER}


def deck_holen(con, nutzer: int, deck_id: int):
    d = con.execute("SELECT * FROM deck WHERE id = ? AND nutzer_id = ?", (deck_id, nutzer)).fetchone()
    if d is None:
        raise LookupError("Deck nicht gefunden.")
    return d


def anlegen(con, nutzer: int, kopf: dict, zeilen: list[dict], quelle: str) -> int:
    platz = con.execute("SELECT COALESCE(MAX(regalplatz), -1) + 1 FROM deck WHERE nutzer_id = ?", (nutzer,)).fetchone()[0]
    deck_id = einfuegen(con, nutzer, QUELLE, "deck", {
        "nutzer_id": nutzer, "name": kopf["name"], "quelle": quelle, "extern_id": kopf.get("extern_id") or None,
        "extern_stand": kopf.get("extern_stand"), "regalplatz": platz})
    for z in zeilen:
        einfuegen(con, nutzer, QUELLE, "deck_karte", {"deck_id": deck_id, **_zeile_felder(z)})
    return deck_id


def unterschiede(con, deck_id: int, zeilen: list[dict]) -> dict:
    """Vorschau für „Neu laden“: dazu, weg, Anzahl geändert (nach _schluessel)."""
    alt = {_schluessel(dict(z)): dict(z) for z in con.execute("SELECT * FROM deck_karte WHERE deck_id = ?", (deck_id,))}
    neu = {_schluessel(_zeile_felder(z)): _zeile_felder(z) for z in zeilen}
    dazu = [{"name": z["name"], "anzahl": z["anzahl"]} for s, z in neu.items() if s not in alt]
    weg = [{"name": z["name"], "anzahl": z["anzahl"]} for s, z in alt.items() if s not in neu]
    anders = [{"name": z["name"], "vorher": alt[s]["anzahl"], "nachher": z["anzahl"]}
              for s, z in neu.items() if s in alt and alt[s]["anzahl"] != z["anzahl"]]
    return {"dazu": dazu, "weg": weg, "anders": anders, "gleich": not (dazu or weg or anders)}


def ersetzen(con, nutzer: int, deck_id: int, kopf: dict, zeilen: list[dict]) -> dict:
    """Übernimmt eine neu geladene Liste: Zeilen ändern, anlegen, löschen; Proxy-Marken bleiben erhalten."""
    alt = {_schluessel(dict(z)): dict(z) for z in con.execute("SELECT * FROM deck_karte WHERE deck_id = ?", (deck_id,))}
    neu = {_schluessel(_zeile_felder(z)): _zeile_felder(z) for z in zeilen}
    vorschau = unterschiede(con, deck_id, zeilen)
    for s, z in alt.items():
        if s not in neu:
            loeschen(con, nutzer, QUELLE, "deck_karte", z["id"])
    for s, z in neu.items():
        if s in alt:
            felder = {f: w for f, w in z.items() if alt[s][f] != w}
            if felder:
                aendern(con, nutzer, QUELLE, "deck_karte", alt[s]["id"], felder)
        else:
            einfuegen(con, nutzer, QUELLE, "deck_karte", {"deck_id": deck_id, **z})
    felder = {"geladen": con.execute("SELECT strftime('%Y-%m-%dT%H:%M:%S', 'now')").fetchone()[0]}
    if kopf.get("extern_stand"):
        felder["extern_stand"] = kopf["extern_stand"]
    aendern(con, nutzer, QUELLE, "deck", deck_id, felder)
    return vorschau


# ---------------------------------------------------------------- Abgleich


def _ort(k) -> dict:
    if k["kiste_id"] is not None:
        return {"art": "kiste", "kiste_id": k["kiste_id"], "kiste": k["kiste_name"], "karte_id": k["id"]}
    return {"art": "binder", "binder_id": k["binder_id"], "binder": k["binder_name"], "seite": k["seite"],
            "zeile": k["zeile"], "spalte": k["spalte"], "karte_id": k["id"]}


def abgleichen(con, nutzer: int, deck) -> dict:
    """Deck mit Zeilen und Abgleich: je Zeile vorhanden, fehlt, genauer Druck, Orte."""
    zeilen = [dict(z) for z in con.execute(
        "SELECT * FROM deck_karte WHERE deck_id = ? ORDER BY commander DESC, basic, name COLLATE NOCASE, id",
        (deck["id"],))]
    rest: dict[int, int] = {}
    nach_schluessel: dict[str, list] = defaultdict(list)
    for k in con.execute(SQL_SAMMLUNG, {"n": nutzer, "d": deck["id"]}):
        rest[k["id"]] = k["anzahl"]
        nach_schluessel[k["oracle_id"] or ""].append(k)
        nach_schluessel["#" + k["name"].lower()].append(k)
    druck_modus = deck["abgleich"] == "druck"
    summe = {"karten": 0, "vorhanden": 0, "fehlt": 0, "proxy": 0, "ausgelassen": 0}
    for z in zeilen:
        z["ausgelassen"] = bool(z["basic"] and deck["basics_auslassen"])
        z["orte"], z["vorhanden"], z["genau"] = [], 0, False
        if z["ausgelassen"]:
            summe["ausgelassen"] += z["anzahl"]
            z["fehlt"] = 0
            continue
        summe["karten"] += z["anzahl"]
        kandidaten = nach_schluessel[z["oracle_id"]] if z["oracle_id"] else nach_schluessel["#" + z["name"].lower()]

        def genau(k, z=z):
            return bool(z["druck_angegeben"] and z["set_code"] and (k["set_code"] or "").lower() == z["set_code"].lower()
                        and k["nummer"] == z["nummer"])
        passend = [k for k in kandidaten if genau(k)] + ([] if druck_modus else [k for k in kandidaten if not genau(k)])
        if druck_modus and not z["druck_angegeben"]:
            passend = list(kandidaten)   # ohne Druck in der Liste zählt auch im Druck-Modus der Name
        brauche = z["anzahl"]
        for k in passend:
            if brauche == 0:
                break
            nimm = min(brauche, rest.get(k["id"], 0))
            if nimm <= 0:
                continue
            rest[k["id"]] -= nimm
            brauche -= nimm
            z["vorhanden"] += nimm
            z["genau"] = z["genau"] or genau(k)
            if len(z["orte"]) < ORTE_HOECHSTENS:
                z["orte"].append({**_ort(k), "anzahl": nimm, "genau": genau(k), "foil": bool(k["foil"])})
        if z["proxy"]:
            summe["proxy"] += brauche
            brauche = 0
        z["fehlt"] = brauche
        summe["vorhanden"] += z["anzahl"] - brauche
        summe["fehlt"] += brauche
    return {**dict(deck), "karten": zeilen, "summe": summe}


def fehlt_text(abgleich: dict) -> str:
    """Fehlt-Liste für Cardmarket („Wants“ → aus Text hinzufügen): eine Zeile „1x Name“ je Karte."""
    je_name: dict[str, int] = {}
    for z in abgleich["karten"]:
        if z["fehlt"]:
            je_name[z["name"]] = je_name.get(z["name"], 0) + z["fehlt"]
    return "".join(f"{n}x {name}\n" for name, n in je_name.items())


def uebersicht(con, nutzer: int) -> list[dict]:
    """Alle Decks mit Summen (für den Regalboden)."""
    ergebnis = []
    for d in con.execute("SELECT * FROM deck WHERE nutzer_id = ? ORDER BY regalplatz, id", (nutzer,)).fetchall():
        a = abgleichen(con, nutzer, d)
        commander = next((z for z in a["karten"] if z["commander"]), None)
        ergebnis.append({k: d[k] for k in ("id", "name", "quelle", "extern_id", "abgleich", "basics_auslassen",
                                           "geladen")}
                        | {"summe": a["summe"], "commander": commander["name"] if commander else None,
                           "bild": commander["scryfall_id"] if commander else None})
    return ergebnis
