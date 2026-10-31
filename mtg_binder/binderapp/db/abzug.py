"""Liste von der Sammlung abziehen (Paket M, 28.10.2026). Verändert nur Karten dieser App, nie ManaBox.

Ablauf
- Die Liste (ManaBox-Listen-Export, Parser `manabox_import.lese_liste`) wird nur gelesen. Gewünschte Anzahlen
  werden je (Scryfall-ID, Foil, Sprache) summiert; der Zustand ist nur ein Hinweis.
- `vorschau` schreibt nichts: je Position gewünschte Anzahl, vorhanden, vorgeschlagene Entnahme je Ort und
  Status (voll, teilweise, fehlt, pruefen). Orte nach der Antwort des Nutzers: **Kisten vor Bindern**
  (Kisten nach Regalplatz, Binder nach Regalplatz, Seite, Zeile, Spalte).
- Ungeprüfte Karten mit Status „unsicher“ werden nie automatisch zugeordnet; „name_sicher“ (Name gesichert,
  Druck offen) zählt dagegen mit (Entscheidung des Nutzers, 29.10.2026). Unsichere erscheinen, sondern als „zuerst prüfen“ gezeigt. Gleicher Name bei anderem Druck, anderer
  Sprache oder anderem Foil ist nur ein Hinweis.
- `ausfuehren` rechnet die Vorschau neu, lässt abgewählte Karten weg, prüft die bestätigte Kartenzahl und
  schreibt in **einer** Transaktion als **ein** Bedienschritt: Anzahl verringern, bei 0 die Karte entfernen
  (volle Zeile in der Historie), leere Taschen werden „leer“. Dazu eine Historienzeile `tabelle = 'abzug'`
  mit Dateiname, Prüfsumme, Posten, Karten und Sicherung (das Abzugsprotokoll; keine Schemaänderung nötig).
- Rückgängig über `schritte.zuruecknehmen` (exakt, Tabellen hashgleich).
Nur Standardbibliothek, Python 3.11 kompatibel; läuft im Server.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import defaultdict

from binderapp.db import schritte
from binderapp.db.datenbank import _protokoll
from binderapp.db.manabox_import import ImportFehler, lese_liste
from binderapp.db.seiten import Fehler

MAX_BYTES = 5 * 1024 * 1024
UNGESICHERT = ("unsicher",)   # ohne Prüfung nie automatisch abziehen; name_sicher zählt mit (Nutzer, 29.10.2026)
AEHNLICH_MAX = 8
STATUS_TEXT = {"voll": "voll vorhanden", "teilweise": "teilweise vorhanden", "fehlt": "nicht vorhanden",
               "pruefen": "zuerst prüfen"}
CSV_SPALTEN = ("ort", "kiste_oder_binder", "seite", "zeile", "spalte", "name", "set", "nummer", "sprache", "foil",
               "menge", "anzahl_vorher", "quelle")

SQL_KANDIDATEN = """
SELECT k.id, k.name, k.set_code, k.nummer, k.sprache, k.foil, k.anzahl, k.status, k.geprueft, k.quelle,
       k.kiste_id, k.tasche_id, ki.name AS kiste_name, ki.regalplatz AS kiste_platz,
       b.id AS binder_id, b.name AS binder_name, b.regalplatz AS binder_platz, se.nummer AS seite, t.zeile, t.spalte
FROM karte k LEFT JOIN kiste ki ON ki.id = k.kiste_id LEFT JOIN tasche t ON t.id = k.tasche_id
LEFT JOIN seite se ON se.id = t.seite_id LEFT JOIN binder b ON b.id = se.binder_id
WHERE k.scryfall_id = ? AND +k.nutzer_id = ? AND k.foil = ? AND k.sprache IS ?
  AND (k.kiste_id IS NOT NULL OR k.tasche_id IS NOT NULL)
"""
SQL_AEHNLICH = """
SELECT k.id, k.name, k.set_code, k.nummer, k.sprache, k.foil, k.anzahl, k.status, k.quelle,
       ki.name AS kiste_name, b.name AS binder_name, se.nummer AS seite, t.zeile, t.spalte
FROM karte k LEFT JOIN kiste ki ON ki.id = k.kiste_id LEFT JOIN tasche t ON t.id = k.tasche_id
LEFT JOIN seite se ON se.id = t.seite_id LEFT JOIN binder b ON b.id = se.binder_id
WHERE k.name = ? COLLATE NOCASE AND +k.nutzer_id = ? AND (k.kiste_id IS NOT NULL OR k.tasche_id IS NOT NULL)
  AND NOT (k.scryfall_id IS ? AND k.foil = ? AND k.sprache IS ?)
ORDER BY k.kiste_id IS NULL, k.id LIMIT ?
"""


def _sortierschluessel(k) -> tuple:
    """Kisten vor Bindern; Kisten nach Regalplatz, Binder nach Regalplatz, Seite, Zeile, Spalte."""
    if k["kiste_id"] is not None:
        return (0, k["kiste_platz"] or 0, k["kiste_name"] or "", 0, 0, 0, (k["name"] or "").lower(), k["id"])
    return (1, k["binder_platz"] or 0, k["binder_name"] or "", k["seite"] or 0, k["zeile"] or 0, k["spalte"] or 0,
            (k["name"] or "").lower(), k["id"])


def _ort(k) -> dict:
    if k["kiste_id"] is not None:
        return {"art": "kiste", "id": k["kiste_id"], "name": k["kiste_name"], "text": f"Kiste {k['kiste_name']}"}
    return {"art": "binder", "id": k["binder_id"], "name": k["binder_name"], "seite": k["seite"], "zeile": k["zeile"],
            "spalte": k["spalte"],
            "text": f"Binder {k['binder_name']} · Seite {k['seite']} · Zeile {k['zeile']}, Spalte {k['spalte']}"}


def _wuensche(posten) -> tuple[list[dict], list[dict]]:
    """Summiert die Liste je (Scryfall-ID, Foil, Sprache); Zeilen ohne Scryfall-ID gesondert."""
    je: dict[tuple, dict] = {}
    ohne: list[dict] = []
    for p in posten:
        if not p.scryfall_id:
            ohne.append({"zeile": p.zeile, "name": p.name, "anzahl": p.anzahl})
            continue
        schluessel = (p.scryfall_id.lower(), p.ist_foil, p.sprache.lower() or None)
        w = je.setdefault(schluessel, {
            "scryfall_id": schluessel[0], "foil": schluessel[1], "sprache": schluessel[2], "name": p.name,
            "set": p.set_code.lower() or None, "nummer": p.nummer or None, "anzahl": 0, "zeilen": [], "zustaende": []})
        w["anzahl"] += p.anzahl
        w["zeilen"].append(p.zeile)
        if p.zustand and p.zustand not in w["zustaende"]:
            w["zustaende"].append(p.zustand)
    return list(je.values()), ohne


def _entnahme(k, menge: int) -> dict:
    return {"karte_id": k["id"], "menge": menge, "anzahl_vorher": k["anzahl"], "name": k["name"],
            "set": k["set_code"], "nummer": k["nummer"], "sprache": k["sprache"], "foil": bool(k["foil"]),
            "quelle": k["quelle"], "ort": _ort(k), "_sort": _sortierschluessel(k)}


def frueher(con, nutzer: int, sha256: str) -> list[dict]:
    """Frühere Abzüge derselben Datei (gleiche Prüfsumme)."""
    return [a for a in abzuege(con, nutzer) if a["sha256"] == sha256]


def plan(con, nutzer: int, daten: bytes, dateiname: str) -> dict:
    """Die Vorschau (schreibt nichts)."""
    if len(daten) > MAX_BYTES:
        raise ImportFehler(f"Die Datei ist zu groß (höchstens {MAX_BYTES // (1024 * 1024)} MB).")
    posten = lese_liste(daten, dateiname)
    wuensche, ohne = _wuensche(posten)
    positionen: list[dict] = []
    entnahme: list[dict] = []
    zaehler: dict[str, int] = defaultdict(int)
    for w in wuensche:
        kandidaten = sorted(con.execute(SQL_KANDIDATEN, (w["scryfall_id"], nutzer, w["foil"], w["sprache"])),
                            key=_sortierschluessel)
        frei = [k for k in kandidaten if not (k["status"] in UNGESICHERT and not k["geprueft"])]
        offen = [k for k in kandidaten if k["status"] in UNGESICHERT and not k["geprueft"]]
        rest = w["anzahl"]
        eigene: list[dict] = []
        for k in frei:
            menge = min(rest, k["anzahl"])
            if menge <= 0:
                break
            eigene.append(_entnahme(k, menge))
            rest -= menge
        vorhanden = sum(k["anzahl"] for k in frei)
        if rest == 0:
            status = "voll"
        elif offen:
            status = "pruefen"
        elif eigene:
            status = "teilweise"
        else:
            status = "fehlt"
        zaehler[status] += 1
        aehnlich = []
        if status != "voll":
            aehnlich = [{"karte_id": a["id"], "name": a["name"], "set": a["set_code"], "nummer": a["nummer"],
                         "sprache": a["sprache"], "foil": bool(a["foil"]), "anzahl": a["anzahl"], "status": a["status"],
                         "ort": (f"Kiste {a['kiste_name']}" if a["kiste_name"] is not None else
                                 f"Binder {a['binder_name']} · Seite {a['seite']} · Zeile {a['zeile']}, Spalte {a['spalte']}")}
                        for a in con.execute(SQL_AEHNLICH, (w["name"], nutzer, w["scryfall_id"], w["foil"],
                                                            w["sprache"], AEHNLICH_MAX))]
        positionen.append({
            **{s: w[s] for s in ("scryfall_id", "name", "set", "nummer", "sprache", "anzahl", "zeilen", "zustaende")},
            "foil": bool(w["foil"]), "vorhanden": vorhanden, "entnahme_karten": w["anzahl"] - rest, "fehlt": rest,
            "status": status, "status_text": STATUS_TEXT[status], "entnahmen": [dict(e, _sort=None) for e in eigene],
            "zuerst_pruefen": [{"karte_id": k["id"], "status": k["status"], "anzahl": k["anzahl"], "ort": _ort(k)}
                               for k in offen],
            "aehnlich": aehnlich,
        })
        entnahme += eigene
    entnahme.sort(key=lambda e: e["_sort"])
    for e in entnahme:
        del e["_sort"]
    for p in positionen:
        for e in p["entnahmen"]:
            del e["_sort"]
    sha256 = hashlib.sha256(daten).hexdigest()
    return {
        "datei": dateiname, "sha256": sha256, "pruefsumme": pruefsumme(entnahme),
        "summen": {"zeilen": len(posten), "posten": len(wuensche), "karten": sum(w["anzahl"] for w in wuensche),
                   "voll": zaehler["voll"], "teilweise": zaehler["teilweise"], "fehlt": zaehler["fehlt"],
                   "pruefen": zaehler["pruefen"], "entnahme_posten": len(entnahme),
                   "entnahme_karten": sum(e["menge"] for e in entnahme), "ohne_scryfall": len(ohne)},
        "positionen": positionen,
        "entnahme": entnahme,
        "ohne_scryfall": ohne,
        "frueher": frueher(con, nutzer, sha256),
    }


def pruefsumme(entnahme: list[dict]) -> str:
    """Kennung des Entnahmeplans (welche Karte, wie viele, bei welcher Anzahl): Ausführen nur, wenn sie zur
    gezeigten Vorschau passt (Befund des Prüfers vom 28.10.2026)."""
    text = ";".join(f"{e['karte_id']}:{e['menge']}:{e['anzahl_vorher']}" for e in sorted(entnahme, key=lambda e: e["karte_id"]))
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def ausfuehren(con, nutzer: int, vorschau: dict, abgewaehlt: set[int], erwartet: int, sicherung: str | None,
               erwartete_pruefsumme: str | None = None) -> dict:
    """Führt die (neu berechnete) Vorschau aus, in der Transaktion des Aufrufers; ein Bedienschritt."""
    if erwartete_pruefsumme is not None and erwartete_pruefsumme != vorschau["pruefsumme"]:
        raise Fehler("Der Stand hat sich seit der Vorschau geändert. Bitte die Vorschau neu laden.", 409)
    entnahme = [e for e in vorschau["entnahme"] if e["karte_id"] not in abgewaehlt]
    karten = sum(e["menge"] for e in entnahme)
    if karten != erwartet:
        raise Fehler(f"Der Stand hat sich geändert: Jetzt wären es {karten} statt {erwartet} Karten. "
                     "Bitte die Vorschau neu laden.", 409)
    if not entnahme:
        raise Fehler("Es ist nichts zum Abziehen ausgewählt.")
    summe = schritte.summe   # Summenprüfung wie beim Verschieben
    vorher = summe(con, nutzer)
    schritt = schritte.neuer_schritt()
    taschen: set[int] = set()
    entfernt = 0
    for e in entnahme:
        k = con.execute("SELECT id, anzahl, tasche_id FROM karte WHERE id = ? AND nutzer_id = ?",
                        (e["karte_id"], nutzer)).fetchone()
        if k is None or k["anzahl"] != e["anzahl_vorher"]:
            raise Fehler("Eine Karte wurde inzwischen geändert. Bitte die Vorschau neu laden.", 409)
        if e["menge"] < k["anzahl"]:
            schritte.aendern_genau(con, nutzer, "karte", k["id"], {"anzahl": k["anzahl"] - e["menge"]}, schritt=schritt)
        else:
            schritte.loeschen(con, nutzer, "karte", k["id"], schritt=schritt)
            entfernt += 1
            if k["tasche_id"] is not None:
                taschen.add(k["tasche_id"])
    leer = 0
    for tid in sorted(taschen):
        if con.execute("SELECT 1 FROM karte WHERE tasche_id = ? LIMIT 1", (tid,)).fetchone() is None:
            leer += bool(schritte.aendern_genau(con, nutzer, "tasche", tid, {"inhalt": "leer"}, schritt=schritt))
    nachher = summe(con, nutzer)
    if nachher[1] != vorher[1] - karten:
        raise Fehler("Summenprüfung fehlgeschlagen, nichts abgezogen.", 500)
    meta = {"datei": vorschau["datei"], "sha256": vorschau["sha256"], "posten": len(entnahme), "karten": karten,
            "listenposten": vorschau["summen"]["posten"], "listenkarten": vorschau["summen"]["karten"],
            "entfernt": entfernt, "taschen_leer": leer, "sicherung": sicherung}
    _protokoll(con, nutzer, schritte.QUELLE, "abzug", 0, "angelegt", None, meta, schritt)
    return {"schritt": schritt, **meta, "summe": {"posten": nachher[0], "karten": nachher[1]}}


def abzuege(con, nutzer: int) -> list[dict]:
    """Abzugsprotokoll, neueste zuerst."""
    zeilen = con.execute(
        "SELECT h.zeitpunkt, h.schritt, h.neu, EXISTS (SELECT 1 FROM historie r WHERE r.rueckgaengig_von = h.schritt) "
        "AS zurueck FROM historie h WHERE h.tabelle = 'abzug' AND h.nutzer_id = ? AND h.quelle = ? ORDER BY h.id DESC",
        (nutzer, schritte.QUELLE)).fetchall()
    liste = []
    for z in zeilen:
        neu = json.loads(z["neu"] or "{}")
        liste.append({"zeitpunkt": z["zeitpunkt"], "schritt": z["schritt"], "datei": neu.get("datei"),
                      "sha256": neu.get("sha256"), "posten": neu.get("posten"), "karten": neu.get("karten"),
                      "zurueckgenommen": bool(z["zurueck"])})
    return liste


def entnahme_nach_abzug(con, nutzer: int, schritt: str) -> dict:
    """Entnahmeliste eines ausgeführten Abzugs aus seiner Historie (Ort aus den alten Werten der Karte)."""
    zeilen = schritte.zeilen(con, nutzer, schritt)
    meta = next((json.loads(z["neu"]) for z in zeilen if z["tabelle"] == "abzug"), None)
    if meta is None:
        raise Fehler("Diesen Abzug gibt es nicht.", 404)
    liste = []
    for z in zeilen:
        if z["tabelle"] != "karte":
            continue
        alt = json.loads(z["alt"] or "{}")
        if z["aktion"] == "geloescht":
            karte, menge = alt, alt["anzahl"]
        else:
            karte = dict(con.execute("SELECT * FROM karte WHERE id = ?", (z["datensatz"],)).fetchone() or {})
            karte.update({k: v for k, v in alt.items() if k != "geaendert"})
            menge = alt["anzahl"] - json.loads(z["neu"])["anzahl"]
        ort = con.execute(
            "SELECT ki.name AS kiste_name, ki.regalplatz AS kiste_platz, b.id AS binder_id, b.name AS binder_name, "
            "b.regalplatz AS binder_platz, se.nummer AS seite, t.zeile, t.spalte FROM (SELECT ? AS kid, ? AS tid) x "
            "LEFT JOIN kiste ki ON ki.id = x.kid LEFT JOIN tasche t ON t.id = x.tid "
            "LEFT JOIN seite se ON se.id = t.seite_id LEFT JOIN binder b ON b.id = se.binder_id",
            (karte.get("kiste_id"), karte.get("tasche_id"))).fetchone()
        k = {**dict(ort), "id": z["datensatz"], "kiste_id": karte.get("kiste_id"), "name": karte.get("name"),
             "set_code": karte.get("set_code"), "nummer": karte.get("nummer"), "sprache": karte.get("sprache"),
             "foil": karte.get("foil"), "anzahl": karte.get("anzahl"), "quelle": karte.get("quelle")}
        liste.append(_entnahme(k, menge))
    liste.sort(key=lambda e: e["_sort"])
    for e in liste:
        del e["_sort"]
    return {"schritt": schritt, **meta, "zurueckgenommen": schritte.zurueckgenommen(con, schritt), "entnahme": liste}


def entnahme_csv(entnahme: list[dict]) -> str:
    """Entnahmeliste als CSV (UTF-8 mit BOM für Tabellenprogramme), nach Ort sortiert."""
    f = io.StringIO()
    schreiber = csv.writer(f)
    schreiber.writerow(CSV_SPALTEN)
    for e in entnahme:
        o = e["ort"]
        schreiber.writerow((o["art"], o["name"], o.get("seite") or "", o.get("zeile") or "", o.get("spalte") or "",
                            e["name"], (e["set"] or "").upper(), e["nummer"] or "", e["sprache"] or "",
                            "foil" if e["foil"] else "normal", e["menge"], e["anzahl_vorher"], e["quelle"]))
    return "﻿" + f.getvalue()
