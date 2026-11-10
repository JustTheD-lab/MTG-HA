"""Sets und ihre Vollständigkeit (Wunsch des Nutzers, 01.11.2026), nur lesend.

    GET /api/sets           alle Sets, aus denen die Sammlung (Binder und Kisten) Karten hat: Drucke und Namen
                            vorhanden/gesamt, Karten, Wert
    GET /api/set/{code}     ein Set: alle Drucke des Katalogs (Sammlernummer, Name, Seltenheit, Merkmale) mit
                            der Anzahl in der Sammlung, dazu je Name, ob er in diesem Set vorhanden ist
    GET /api/set/{code}/fehlt   fehlende Namen des Sets mit Preis und Summe, dazu Text zum Kopieren (Paket 6,
                            10.11.2026); nur auf Abruf, damit die Übersicht nicht langsamer wird

Zählweise:
- **Drucke:** jede Sammlernummer des Sets im Katalog (also auch Borderless, Showcase, Extended Art …). Vorhanden ist
  eine Nummer, wenn eine Karte der Sammlung sie hat, gleich in welcher Sprache und ob Foil.
- **Namen:** jede Karte (Oracle-ID) des Sets. Vorhanden ist ein Name, wenn irgendein Druck dieses Sets mit diesem
  Namen in der Sammlung ist.
- **Fehlend** (Antworten des Nutzers, 10.11.2026): jeder Name des Sets, der aus genau diesem Set nicht da ist (wie
  die Namenszählung, Varianten nicht extra). Preis je Name ist der günstigste Druck dieses Namens in genau diesem Set
  in der gewählten Preisart, nicht Foil. Die Preise kommen aus der Preisdatei `<daten>/preise/price_guide_1.json`
  (die Tabelle `preis` kennt nur Produkte der Sammlung); ohne Preisdatei aus `preis` vom letzten Abruf.
  Text zum Kopieren: eine Zeile „1 Name (SET)“ je fehlendem Namen.
Gesamtzahlen kommen aus dem Katalog; ohne Katalog gibt es die Ansicht nicht (`verfuegbar: false`). Alle Quellen
(Scan, ManaBox, von Hand) zählen mit, Karten ohne Ort nicht.
"""
from __future__ import annotations

import re
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from starlette.responses import JSONResponse
from starlette.routing import Route

from binderapp.preise import abruf
from binderapp.web import kisten as k_
from binderapp.web.helfer import Fehler, fehler

SET_MUSTER = re.compile(r"[a-z0-9]{2,8}")

# Besitz je Set aus der Sammlung: Set, Nummer und Oracle-ID aus den Kartendaten, sonst von der Karte
SQL_BESITZ = f"""
SELECT COALESCE(kd.set_code, LOWER(k.set_code)) AS code, COALESCE(kd.nummer, k.nummer) AS nummer,
       COALESCE(kd.oracle_id, k.oracle_id) AS oracle_id, SUM(k.anzahl) AS anzahl, MIN(k.id) AS karte_id,
       MIN(k.kiste_id) AS kiste_id, COALESCE(SUM(k.anzahl * {{preis}}), 0) AS wert
FROM karte k LEFT JOIN kartendaten kd ON kd.scryfall_id = k.scryfall_id {{preis_join}}
WHERE k.nutzer_id = ? AND {k_.ORT_DA} AND COALESCE(kd.set_code, k.set_code) IS NOT NULL {{nur_set}}
GROUP BY 1, 2, 3
"""


def _besitz(con, nutzer: int, code: str | None = None) -> list:
    preise = k_.version(con) >= k_.PREIS_AB_VERSION
    preis = k_.preis_sql(k_.PREIS, k_.werte.gewaehlt(con, nutzer)) if preise else "0"
    sql = SQL_BESITZ.format(preis=preis, preis_join=k_.PREIS_JOIN if preise else "",
                            nur_set="AND COALESCE(kd.set_code, LOWER(k.set_code)) = ?" if code else "")
    return con.execute(sql, (nutzer, code) if code else (nutzer,)).fetchall()


# Preise aus der Preisdatei je Set, gemerkt bis zur nächsten Datei (die ganze Datei lesen dauert am PC rund 0,3 s)
_PREISE_MERK: dict[tuple, tuple[str, dict]] = {}
_PREISE_SPERRE = threading.Lock()
PREISE_MERK_MAX = 8


def _preise_datei(datei: Path, code: str, ids: set[int]) -> tuple[str, dict[int, tuple]]:
    """(Erstellungszeitpunkt, {Produkt: Zeile wie abruf.einlesen}) für die Produkte eines Sets."""
    schluessel = (str(datei), datei.stat().st_mtime_ns, code)
    with _PREISE_SPERRE:
        if schluessel in _PREISE_MERK:
            return _PREISE_MERK[schluessel]
    erstellt, _, zeilen = abruf.einlesen(datei.read_bytes(), ids)
    ergebnis = (erstellt, {z[0]: z for z in zeilen})
    with _PREISE_SPERRE:
        while len(_PREISE_MERK) >= PREISE_MERK_MAX:
            _PREISE_MERK.pop(next(iter(_PREISE_MERK)))
        _PREISE_MERK[schluessel] = ergebnis
    return ergebnis


def _preise_tabelle(con, spalte: str, ids: set[int]) -> tuple[str | None, dict[int, float]]:
    """Ohne Preisdatei: Preise vom letzten Abruf aus `preis` (nur Produkte der Sammlung)."""
    if k_.version(con) < k_.PREIS_AB_VERSION or not ids:
        return None, {}
    try:
        tag = con.execute("SELECT MAX(tag) FROM preisabruf").fetchone()[0]
        if tag is None:
            return None, {}
        werte, liste = {}, sorted(ids)
        for anfang in range(0, len(liste), 400):
            teil = liste[anfang:anfang + 400]
            platz = ", ".join("?" for _ in teil)
            for z in con.execute(f"SELECT cardmarket_id, {spalte} FROM preis WHERE tag = ? AND cardmarket_id IN"
                                 f" ({platz})", (tag, *teil)):
                if z[1]:
                    werte[z[0]] = z[1]
        return tag, werte
    except sqlite3.OperationalError:
        return None, {}


def fehlend(drucke: list[dict], besitz_oracle: set, code: str, preise: dict[int, float]) -> dict:
    """Fehlende Namen eines Sets mit günstigstem Druck, Summe und Text (rein, für Tests).

    `drucke`: Drucke des Sets aus dem Katalog (`oracle_id`, `name`, `nummer`, `scryfall_id`, `cardmarket_id`),
    `besitz_oracle`: Oracle-IDs, die aus diesem Set in der Sammlung sind, `preise`: {Produkt: Preis}.
    Ohne Preis zeigt die Zeile den Druck mit der kleinsten Nummer."""
    je_name: dict[str, dict] = {}
    for d in drucke:
        if d["oracle_id"] in besitz_oracle:
            continue
        n = je_name.setdefault(d["oracle_id"], {"name": d["name"], "oracle_id": d["oracle_id"], "preis": None,
                                                "nummer": d["nummer"], "scryfall_id": d["scryfall_id"],
                                                "drucke": set()})
        n["drucke"].add(d["nummer"])
        preis = preise.get(d.get("cardmarket_id"))
        reihe = _nummer_sortieren(d["nummer"])
        if preis and (n["preis"] is None or (preis, reihe) < (n["preis"], _nummer_sortieren(n["nummer"]))):
            n.update(preis=preis, nummer=d["nummer"], scryfall_id=d["scryfall_id"])
        elif n["preis"] is None and reihe < _nummer_sortieren(n["nummer"]):
            n.update(nummer=d["nummer"], scryfall_id=d["scryfall_id"])
    liste = sorted(je_name.values(), key=lambda x: (x["name"].lower(), x["name"]))
    for n in liste:
        n["drucke"] = len(n["drucke"])
        n["preis"] = round(n["preis"], 2) if n["preis"] else None
    return {"fehlend": liste, "anzahl": len(liste),
            "summe": round(sum(n["preis"] or 0 for n in liste), 2),
            "ohne_preis": sum(n["preis"] is None for n in liste),
            "text": "".join(f"1 {n['name']} ({code.upper()})\n" for n in liste)}


def _nummer_sortieren(nummer: str) -> tuple:
    m = re.match(r"(\d+)(.*)", nummer or "")
    return (0, int(m.group(1)), m.group(2)) if m else (1, 0, nummer or "")


def routen(h) -> list[Route]:

    def lesend(funktion):
        def huelle(request):
            try:
                return JSONResponse(funktion(request))
            except Fehler as f:
                return fehler(str(f), f.status)
        huelle.__name__ = funktion.__name__
        return huelle

    @lesend
    def sets(request):
        with closing(h.db()) as con:
            k_._ab_version_3(con, 404)
            k_._ab_version_8(con)
            besitz = _besitz(con, h.nutzer)
        je_set: dict[str, dict] = {}
        for z in besitz:
            s = je_set.setdefault(z["code"], {"nummern": set(), "oracle": set(), "karten": 0, "wert": 0.0})
            s["nummern"].add(z["nummer"])
            s["oracle"].add(z["oracle_id"])
            s["karten"] += z["anzahl"]
            s["wert"] += z["wert"] or 0
        kat = h.katalog()
        if kat is None:
            return {"verfuegbar": False, "sets": []}
        with closing(kat):
            codes = list(je_set)
            gesamt, drucke = {}, {}
            for anfang in range(0, len(codes), 400):
                teil = codes[anfang:anfang + 400]
                platz = ", ".join("?" for _ in teil)
                for z in kat.execute(f"SELECT set_code, name, erschienen FROM satz WHERE set_code IN ({platz})", teil):
                    gesamt[z["set_code"]] = dict(z)
                for z in kat.execute(f"SELECT set_code, nummer, oracle_id FROM druck WHERE set_code IN ({platz})", teil):
                    d = drucke.setdefault(z["set_code"], {"nummern": set(), "oracle": set()})
                    d["nummern"].add(z["nummer"])
                    d["oracle"].add(z["oracle_id"])
        aus = []
        for code, s in je_set.items():
            d = drucke.get(code, {"nummern": set(), "oracle": set()})
            satz = gesamt.get(code, {})
            aus.append({
                "code": code, "name": satz.get("name"), "erschienen": satz.get("erschienen"),
                "drucke_gesamt": len(d["nummern"]), "drucke_da": len(s["nummern"] & d["nummern"]),
                "namen_gesamt": len(d["oracle"]), "namen_da": len(s["oracle"] & d["oracle"]),
                "karten": s["karten"], "wert": round(s["wert"], 2) if s["wert"] else None,
                "im_katalog": bool(d["nummern"]),
            })
        aus.sort(key=lambda x: (x["erschienen"] or "0000", x["code"]), reverse=True)
        return {"verfuegbar": True, "sets": aus}

    @lesend
    def set_einzeln(request):
        code = request.path_params["code"].lower()
        if not SET_MUSTER.fullmatch(code):
            raise Fehler("Set nicht gefunden.", 404)
        with closing(h.db()) as con:
            k_._ab_version_3(con, 404)
            k_._ab_version_8(con)
            besitz = _besitz(con, h.nutzer, code)
        kat = h.katalog()
        if kat is None:
            return {"verfuegbar": False}
        with closing(kat):
            satz = kat.execute("SELECT name, erschienen FROM satz WHERE set_code = ?", (code,)).fetchone()
            zeilen = kat.execute(
                "SELECT scryfall_id, oracle_id, name, nummer, seltenheit, besonderheiten, sprache, bild_vorhanden"
                " FROM druck WHERE set_code = ? ORDER BY (sprache = 'en') DESC", (code,)).fetchall() \
                if satz else []
        if satz is None:
            raise Fehler("Dieses Set gibt es im Katalog nicht.", 404)
        da = {}
        for z in besitz:
            e = da.setdefault(z["nummer"], {"anzahl": 0, "karte_id": z["karte_id"], "kiste_id": z["kiste_id"]})
            e["anzahl"] += z["anzahl"]
        drucke, gesehen = [], set()
        for z in zeilen:
            if z["nummer"] in gesehen:
                continue
            gesehen.add(z["nummer"])
            e = da.get(z["nummer"])
            drucke.append({"nummer": z["nummer"], "name": z["name"], "seltenheit": z["seltenheit"],
                           "besonderheiten": [b for b in (z["besonderheiten"] or "").split(",") if b],
                           "scryfall_id": z["scryfall_id"], "oracle_id": z["oracle_id"],
                           "anzahl": e["anzahl"] if e else 0, "karte_id": e["karte_id"] if e else None,
                           "kiste_id": e["kiste_id"] if e else None})
        drucke.sort(key=lambda x: _nummer_sortieren(x["nummer"]))
        namen: dict[str, dict] = {}
        for d in drucke:
            n = namen.setdefault(d["oracle_id"], {"name": d["name"], "oracle_id": d["oracle_id"], "anzahl": 0,
                                                  "drucke_da": 0, "drucke_gesamt": 0, "erste_nummer": d["nummer"]})
            n["drucke_gesamt"] += 1
            n["anzahl"] += d["anzahl"]
            n["drucke_da"] += d["anzahl"] > 0
        namen_liste = sorted(namen.values(), key=lambda x: x["name"].lower())
        return {"verfuegbar": True, "code": code, "name": satz["name"], "erschienen": satz["erschienen"],
                "drucke": drucke, "namen": namen_liste,
                "drucke_gesamt": len(drucke), "drucke_da": sum(d["anzahl"] > 0 for d in drucke),
                "namen_gesamt": len(namen_liste), "namen_da": sum(n["anzahl"] > 0 for n in namen_liste),
                "karten": sum(d["anzahl"] for d in drucke)}

    @lesend
    def set_fehlt(request):
        code = request.path_params["code"].lower()
        if not SET_MUSTER.fullmatch(code):
            raise Fehler("Set nicht gefunden.", 404)
        with closing(h.db()) as con:
            k_._ab_version_3(con, 404)
            k_._ab_version_8(con)
            besitz = _besitz(con, h.nutzer, code)
            art = k_.werte.gewaehlt(con, h.nutzer)
            spalte = k_.werte.PREISARTEN[art][0]
            kat = h.katalog()
            if kat is None:
                return {"verfuegbar": False}
            with closing(kat):
                if kat.execute("SELECT 1 FROM satz WHERE set_code = ?", (code,)).fetchone() is None:
                    raise Fehler("Dieses Set gibt es im Katalog nicht.", 404)
                drucke = [dict(z) for z in kat.execute(
                    "SELECT oracle_id, name, nummer, scryfall_id, cardmarket_id FROM druck WHERE set_code = ?"
                    " ORDER BY (sprache = 'en') DESC", (code,))]
            ids = {d["cardmarket_id"] for d in drucke if d["cardmarket_id"]}
            datei = Path(h.datenbank).parent / "preise" / "price_guide_1.json"
            stand, quelle, preise = None, None, {}
            if ids and datei.is_file():
                try:
                    stand, zeilen = _preise_datei(datei, code, ids)
                    platz = 1 + abruf.SPALTEN.index(spalte)
                    preise = {pid: z[platz] for pid, z in zeilen.items() if z[platz]}
                    quelle = "preisdatei"
                except (OSError, abruf.AbrufFehler):
                    stand, preise = None, {}
            if quelle is None:
                stand, preise = _preise_tabelle(con, spalte, ids)
                quelle = "datenbank" if stand else None
        # Namen wie in der Sets-Ansicht: ein Name ist da, wenn eine Karte der Sammlung aus diesem Set ihn hat
        ergebnis = fehlend(drucke, {z["oracle_id"] for z in besitz}, code, preise)
        return {"verfuegbar": True, "code": code, "preisart": art, "preisart_name": k_.werte.NAMEN[art],
                "preisquelle": quelle, "preisstand": stand, **ergebnis}

    return [Route("/sets", sets), Route("/set/{code}", set_einzeln), Route("/set/{code}/fehlt", set_fehlt)]
