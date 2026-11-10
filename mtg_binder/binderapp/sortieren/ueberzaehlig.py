"""Überzählige Karten (Paket 4, 10.11.2026): was je Druck über einem Limit liegt, mit Cardmarket-Wert. Nur lesend.

Zählweise (Antworten des Nutzers, 10.11.2026): je Druck (scryfall_id), Foil getrennt, Sprache nicht; alle Orte zählen
(Binder, Kisten, Decks, auch der Scan-Eingang) und alle Quellen, denn jede Karte ist ein echtes Exemplar. Karten ohne
Ort oder ohne Druck zählen nicht. Überzählig = Exemplare des Drucks − Limit. Der Wert ist Überzählige × Preis der
gewählten Preisart (Foil-Karten mit Foil-Preis) vom neuesten Preistag. Standardländer bleiben ohne Wunsch draußen.

Dieselbe Zählung steckt in der Bedingung „Über dem Limit“ der Sortierregeln (`regeln.LIMIT_VORHER`); dort wird sie
je Karte in Reihenfolge ausgewertet, hier je Druck zusammengefasst.
"""
from __future__ import annotations

import csv
import io

from binderapp.db.seiten import Fehler
from binderapp.preise import werte
from binderapp.sortieren.regeln import LIMIT_MAX

LIMIT_STANDARD = 4
ORT_DA = "(k.kiste_id IS NOT NULL OR k.tasche_id IS NOT NULL OR k.deck_id IS NOT NULL)"
STANDARDLAND = "(instr(COALESCE(kd.typzeile, ''), 'Basic') = 1)"

SQL_GRUPPEN = """
SELECT k.scryfall_id, k.foil, SUM(k.anzahl) AS gesamt, MIN(k.name) AS name,
       MIN(COALESCE(kd.set_code, lower(k.set_code))) AS set_code, MIN(k.nummer) AS nummer, MIN(k.id) AS karte_id,
       MAX({preis}) AS preis
FROM karte k
LEFT JOIN kartendaten kd ON kd.scryfall_id = k.scryfall_id
LEFT JOIN preis p ON p.cardmarket_id = kd.cardmarket_id AND p.tag = ?
WHERE k.nutzer_id = ? AND k.scryfall_id IS NOT NULL AND {ort} {land}
GROUP BY k.scryfall_id, k.foil
HAVING SUM(k.anzahl) > ?
"""

SQL_ORTE = """
SELECT k.scryfall_id, k.foil,
       CASE WHEN k.kiste_id IS NOT NULL THEN 'kiste' WHEN k.deck_id IS NOT NULL THEN 'deck' ELSE 'binder' END AS art,
       COALESCE(ki.name, de.name, b.name) AS ort, SUM(k.anzahl) AS anzahl
FROM karte k
LEFT JOIN kiste ki ON ki.id = k.kiste_id
LEFT JOIN deck de ON de.id = k.deck_id
LEFT JOIN tasche t ON t.id = k.tasche_id LEFT JOIN seite se ON se.id = t.seite_id
LEFT JOIN binder b ON b.id = se.binder_id
WHERE k.nutzer_id = ? AND {ort} AND k.scryfall_id IN ({platz})
GROUP BY k.scryfall_id, k.foil, art, ort
ORDER BY art, ort
"""

CSV_SPALTEN = ("Anzahl", "Name", "Set", "Nummer", "Foil", "Exemplare", "Preis", "Wert", "Orte")


def limit_lesen(roh) -> int:
    if roh is None or roh == "":
        return LIMIT_STANDARD
    try:
        n = int(str(roh))
    except ValueError:
        raise Fehler(f"Limit: eine ganze Zahl von 1 bis {LIMIT_MAX}.")
    if not 1 <= n <= LIMIT_MAX:
        raise Fehler(f"Limit: eine ganze Zahl von 1 bis {LIMIT_MAX}.")
    return n


def liste(con, nutzer: int, limit: int = LIMIT_STANDARD, *, mit_laendern: bool = False,
          preisart: str | None = None) -> dict:
    """{"limit", "preisart", "preisart_name", "tag", "karten": [...], "drucke", "anzahl", "wert", "ohne_preis"}.
    Jede Zeile: scryfall_id, karte_id, name, set, nummer, foil, gesamt, ueber, preis, wert, orte [{art, name, anzahl}].
    Sortiert nach Wert, dann Name."""
    preisart = werte.aufloesen(con, preisart, nutzer)
    tag = werte.neuester_tag(con)
    sql = SQL_GRUPPEN.format(preis=werte.preis_ausdruck(preisart), ort=ORT_DA,
                             land="" if mit_laendern else f"AND NOT {STANDARDLAND}")
    zeilen = con.execute(sql, (tag, nutzer, limit)).fetchall()
    karten = []
    for z in zeilen:
        ueber = z["gesamt"] - limit
        preis = None if z["preis"] is None else round(z["preis"], 2)
        karten.append({"scryfall_id": z["scryfall_id"], "karte_id": z["karte_id"], "name": z["name"],
                       "set": (z["set_code"] or "").upper(), "nummer": z["nummer"], "foil": bool(z["foil"]),
                       "gesamt": z["gesamt"], "ueber": ueber, "preis": preis,
                       "wert": None if preis is None else round(ueber * preis, 2), "orte": []})
    nach_druck = {(e["scryfall_id"], e["foil"]): e for e in karten}
    ids = sorted({e["scryfall_id"] for e in karten})
    for anfang in range(0, len(ids), 400):
        teil = ids[anfang:anfang + 400]
        sql = SQL_ORTE.format(ort=ORT_DA, platz=", ".join("?" for _ in teil))
        for o in con.execute(sql, (nutzer, *teil)):
            e = nach_druck.get((o["scryfall_id"], bool(o["foil"])))
            if e is not None:
                e["orte"].append({"art": o["art"], "name": o["ort"], "anzahl": o["anzahl"]})
    karten.sort(key=lambda e: (-(e["wert"] or 0), e["name"].lower(), e["set"], e["foil"]))
    return {"limit": limit, "preisart": preisart, "preisart_name": werte.NAMEN[preisart], "tag": tag,
            "mit_laendern": mit_laendern, "karten": karten, "drucke": len(karten),
            "anzahl": sum(e["ueber"] for e in karten),
            "wert": round(sum(e["wert"] or 0 for e in karten), 2),
            "ohne_preis": sum(e["ueber"] for e in karten if e["preis"] is None)}


def _druck(e: dict) -> str:
    return f"{e['name']} ({e['set']}) {e['nummer'] or ''}".rstrip() + (" *F*" if e["foil"] else "")


def als_text(d: dict) -> str:
    """Eine Zeile je Druck: „2 Name (SET) 123 *F*“ (Anzahl = Überzählige)."""
    return "\n".join(f"{e['ueber']} {_druck(e)}" for e in d["karten"])


def _komma(x) -> str:
    return "" if x is None else f"{x:.2f}".replace(".", ",")


def als_csv(d: dict) -> str:
    """CSV mit Semikolon und Dezimalkomma (UTF-8 mit BOM für Tabellenprogramme)."""
    f = io.StringIO()
    schreiber = csv.writer(f, delimiter=";")
    schreiber.writerow(CSV_SPALTEN)
    for e in d["karten"]:
        orte = ", ".join(f"{o['name']} ({o['anzahl']})" for o in e["orte"])
        schreiber.writerow((e["ueber"], e["name"], e["set"], e["nummer"] or "", "foil" if e["foil"] else "normal",
                            e["gesamt"], _komma(e["preis"]), _komma(e["wert"]), orte))
    return "﻿" + f.getvalue()
