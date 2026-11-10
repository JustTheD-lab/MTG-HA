"""Decks in der Datenbank: anlegen, neu laden (mit Vorschau), Abgleich mit der Sammlung, Preise, Fehlt-Liste.

Abgleich (berechnet, nicht gespeichert; ab Version 8 mit dem Ort „Deck“):
- Gezählt werden Karten, die sicher erkannt oder geprüft sind; ungeprüfte Karten mit Status „unsicher“ zählen nicht
  (wie beim Abzug). Im Modus „name“ zählt jeder Druck der Karte, im Modus „druck“ nur gleiches Set und gleiche
  Nummer (die Sprache ist egal). Ohne Katalog-Treffer wird nach dem Namen abgeglichen.
- Je Zeile zuerst die Karten, die schon **im Deck** liegen, dann **frei verfügbare** in Bindern und Kisten (genauer
  Druck zuerst, dann Kisten vor Bindern, Entscheidung des Nutzers). Karten in **anderen Decks** sind nicht frei, sie
  stehen nur als Hinweis „steckt in Deck X“ da. Jede Karte wird höchstens so oft vergeben, wie sie da ist.
- Basics fallen mit „Basics auslassen“ heraus, Proxys zählen als vorhanden (nicht in der Fehlt-Liste).
- Preise (Preisart wie überall, preise.werte): je Zeile der günstigste ihrer Cardmarket-Produkte (deck_produkt).
Schreiben mit Historie (Quelle „nutzer“) in der Transaktion des Aufrufers.
"""
from __future__ import annotations

from collections import defaultdict

from binderapp.db.datenbank import aendern, einfuegen, loeschen
from binderapp.decks import statistik
from binderapp.preise import werte as preise

QUELLE = "nutzer"
ORTE_HOECHSTENS = 6
FELDER = ("scryfall_id", "oracle_id", "name", "set_code", "nummer", "druck_angegeben", "anzahl", "foil", "commander",
          "basic")

SQL_KARTEN = """
SELECT k.id, k.name, COALESCE(kd.oracle_id, k.oracle_id) AS oracle_id, k.set_code, k.nummer, k.anzahl, k.foil,
       k.scryfall_id, k.sprache, k.kiste_id, ki.name AS kiste_name, ki.regalplatz AS kiste_platz,
       b.id AS binder_id, b.name AS binder_name, b.regalplatz AS binder_platz, se.nummer AS seite,
       t.zeile, t.spalte, k.tasche_id, k.deck_id, de.name AS deck_name
FROM karte k
LEFT JOIN kartendaten kd ON kd.scryfall_id = k.scryfall_id
LEFT JOIN kiste ki ON ki.id = k.kiste_id
LEFT JOIN tasche t ON t.id = k.tasche_id
LEFT JOIN seite se ON se.id = t.seite_id
LEFT JOIN binder b ON b.id = se.binder_id
LEFT JOIN deck de ON de.id = k.deck_id
WHERE k.nutzer_id = :n AND (k.kiste_id IS NOT NULL OR k.tasche_id IS NOT NULL OR k.deck_id IS NOT NULL)
  AND (k.status != 'unsicher' OR k.geprueft = 1)
  AND (COALESCE(kd.oracle_id, k.oracle_id) IN (SELECT oracle_id FROM deck_karte WHERE deck_id = :d)
       OR lower(k.name) IN (SELECT lower(name) FROM deck_karte WHERE deck_id = :d AND oracle_id IS NULL)
       OR k.deck_id = :d)
"""


def _schluessel(z: dict) -> tuple:
    """Gleiche Deckzeile beim Neu laden: Druck (oder Name), Foil, Commander."""
    druck = (z["set_code"] or "", z["nummer"] or "") if z["druck_angegeben"] else ("", "")
    return (z["oracle_id"] or z["name"].lower(), *druck, bool(z["foil"]), bool(z["commander"]))


def _zeile_felder(z: dict) -> dict:
    return {f: (int(bool(z.get(f))) if f in ("druck_angegeben", "foil", "commander", "basic") else z.get(f))
            for f in FELDER}


def _produkte_schreiben(con, zeile_id: int, produkte) -> None:
    """Cardmarket-Produkte einer Zeile (abgeleitet aus dem Katalog, ohne Historie)."""
    if produkte is None:
        return
    con.execute("DELETE FROM deck_produkt WHERE deck_karte_id = ?", (zeile_id,))
    con.executemany("INSERT OR IGNORE INTO deck_produkt (deck_karte_id, cardmarket_id) VALUES (?, ?)",
                    [(zeile_id, int(p)) for p in produkte])
    con.execute("UPDATE deck_karte SET produkte = ? WHERE id = ?", (len(produkte), zeile_id))


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
        zeile_id = einfuegen(con, nutzer, QUELLE, "deck_karte", {"deck_id": deck_id, **_zeile_felder(z)})
        _produkte_schreiben(con, zeile_id, z.get("produkte"))
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
    """Übernimmt eine neu geladene Liste: Zeilen ändern, anlegen, löschen; Proxy-Marken bleiben erhalten. Karten,
    die schon im Deck liegen, bleiben dort (der Abgleich zeigt sie danach gegebenenfalls als überzählig)."""
    alt = {_schluessel(dict(z)): dict(z) for z in con.execute("SELECT * FROM deck_karte WHERE deck_id = ?", (deck_id,))}
    neu = {_schluessel(_zeile_felder(z)): (_zeile_felder(z), z.get("produkte")) for z in zeilen}
    vorschau = unterschiede(con, deck_id, zeilen)
    for s, z in alt.items():
        if s not in neu:
            loeschen(con, nutzer, QUELLE, "deck_karte", z["id"])
    for s, (z, produkte) in neu.items():
        if s in alt:
            felder = {f: w for f, w in z.items() if alt[s][f] != w}
            if felder:
                aendern(con, nutzer, QUELLE, "deck_karte", alt[s]["id"], felder)
            _produkte_schreiben(con, alt[s]["id"], produkte)
        else:
            zeile_id = einfuegen(con, nutzer, QUELLE, "deck_karte", {"deck_id": deck_id, **z})
            _produkte_schreiben(con, zeile_id, produkte)
    felder = {"geladen": con.execute("SELECT strftime('%Y-%m-%dT%H:%M:%S', 'now')").fetchone()[0]}
    if kopf.get("extern_stand"):
        felder["extern_stand"] = kopf["extern_stand"]
    aendern(con, nutzer, QUELLE, "deck", deck_id, felder)
    return vorschau


# ---------------------------------------------------------------- Abgleich


def ort(k) -> dict:
    if k["deck_id"] is not None:
        return {"art": "deck", "deck_id": k["deck_id"], "deck": k["deck_name"], "karte_id": k["id"]}
    if k["kiste_id"] is not None:
        return {"art": "kiste", "kiste_id": k["kiste_id"], "kiste": k["kiste_name"], "karte_id": k["id"]}
    return {"art": "binder", "binder_id": k["binder_id"], "binder": k["binder_name"], "seite": k["seite"],
            "zeile": k["zeile"], "spalte": k["spalte"], "karte_id": k["id"]}


def genau(k, z) -> bool:
    """Liegt der genaue Druck der Deckzeile vor (Set und Nummer; die Sprache ist egal)?"""
    return bool(z["druck_angegeben"] and z["set_code"] and (k["set_code"] or "").lower() == z["set_code"].lower()
                and k["nummer"] == z["nummer"])


def frei_reihenfolge(k) -> tuple:
    """Vorschlag beim Entnehmen: Kisten vor Bindern (wie der Abzug), dann Regalplatz und Platz."""
    if k["kiste_id"] is not None:
        return (0, k["kiste_platz"] or 0, k["kiste_id"], 0, 0, 0, k["id"])
    return (1, k["binder_platz"] or 0, k["binder_id"] or 0, k["seite"] or 0, k["zeile"] or 0, k["spalte"] or 0, k["id"])


def passend(kandidaten: list, z: dict, druck_modus: bool) -> list:
    """Kandidaten einer Zeile in der Reihenfolge der Vergabe: genauer Druck zuerst; im Druck-Modus nur dieser
    (eine Zeile ohne Druck nimmt jeden)."""
    if druck_modus and z["druck_angegeben"]:
        return [k for k in kandidaten if genau(k, z)]
    return [k for k in kandidaten if genau(k, z)] + [k for k in kandidaten if not genau(k, z)]


def karten_lesen(con, nutzer: int, deck_id: int) -> tuple[dict, dict, dict, dict]:
    """(im Deck, frei, in anderen Decks, alle) je Schlüssel (Oracle-ID bzw. „#name“); freie Karten in der Reihenfolge
    des Vorschlags."""
    im_deck, frei, andere = defaultdict(list), defaultdict(list), defaultdict(list)
    alle = {}
    for k in sorted(con.execute(SQL_KARTEN, {"n": nutzer, "d": deck_id}).fetchall(), key=frei_reihenfolge):
        alle[k["id"]] = k
        ziel = im_deck if k["deck_id"] == deck_id else andere if k["deck_id"] is not None else frei
        for s in (k["oracle_id"] or "", "#" + k["name"].lower()):
            ziel[s].append(k)
    return im_deck, frei, andere, alle


def _kandidaten(pool: dict, z: dict) -> list:
    return pool[z["oracle_id"]] if z["oracle_id"] else pool["#" + z["name"].lower()]


def zeilen_preise(con, deck_id: int, preisart: str | None = None) -> dict[int, float]:
    """{deck_karte_id: Einzelpreis} = günstigstes Produkt der Zeile am neuesten Preistag (Foil-Zeilen: Foil-Preis)."""
    tag = preise.neuester_tag(con)
    if tag is None:
        return {}
    ausdruck = preise.preis_ausdruck(preise.aufloesen(con, preisart))   # nutzt k.foil: hier die Deckzeile
    sql = (f"SELECT k.id, MIN({ausdruck}) FROM deck_karte k JOIN deck_produkt dp ON dp.deck_karte_id = k.id "
           f"JOIN preis p ON p.cardmarket_id = dp.cardmarket_id AND p.tag = ? WHERE k.deck_id = ? GROUP BY k.id")
    return {z[0]: round(z[1], 2) for z in con.execute(sql, (tag, deck_id)) if z[1] is not None}


def abgleichen(con, nutzer: int, deck, preisart: str | None = None) -> dict:
    """Deck mit Zeilen, Abgleich, Preisen und Summen (ohne Preisart die Einstellung des Nutzers)."""
    preisart = preise.aufloesen(con, preisart, nutzer)
    zeilen = [dict(z) for z in con.execute(
        "SELECT * FROM deck_karte WHERE deck_id = ? ORDER BY commander DESC, basic, name COLLATE NOCASE, id",
        (deck["id"],))]
    im_deck, frei, andere, alle = karten_lesen(con, nutzer, deck["id"])
    rest = {kid: k["anzahl"] for kid, k in alle.items()}
    druck_modus = deck["abgleich"] == "druck"
    einzel = zeilen_preise(con, deck["id"], preisart)
    summe = {"karten": 0, "im_deck": 0, "verfuegbar": 0, "vorhanden": 0, "fehlt": 0, "proxy": 0, "ausgelassen": 0}
    werte = {"liste": 0.0, "fehlt": 0.0, "ohne_preis": 0}
    for z in zeilen:
        z["preis"] = einzel.get(z["id"])
        z["ausgelassen"] = bool(z["basic"] and deck["basics_auslassen"])
        z.update(orte=[], im_deck=0, verfuegbar=0, vorhanden=0, fehlt=0, genau=False, andere_decks=[])
        if z["ausgelassen"]:
            summe["ausgelassen"] += z["anzahl"]
            continue
        summe["karten"] += z["anzahl"]
        brauche = z["anzahl"]
        for art, pool in (("im_deck", im_deck), ("verfuegbar", frei)):
            for k in passend(_kandidaten(pool, z), z, druck_modus):
                nimm = min(brauche, rest.get(k["id"], 0))
                if nimm <= 0:
                    continue
                rest[k["id"]] -= nimm
                brauche -= nimm
                z[art] += nimm
                z["genau"] = z["genau"] or genau(k, z)
                if len(z["orte"]) < ORTE_HOECHSTENS:
                    z["orte"].append({**ort(k), "anzahl": nimm, "genau": genau(k, z), "foil": bool(k["foil"])})
                if brauche == 0:
                    break
        z["vorhanden"] = z["im_deck"] + z["verfuegbar"]
        if brauche and z["proxy"]:
            summe["proxy"] += brauche
            brauche = 0
        z["fehlt"] = brauche
        if brauche:   # Hinweis: steckt in einem anderen Deck (dort nicht frei)
            je_deck: dict[int, dict] = {}
            for k in passend(_kandidaten(andere, z), z, druck_modus):
                d = je_deck.setdefault(k["deck_id"], {"deck_id": k["deck_id"], "deck": k["deck_name"], "anzahl": 0})
                d["anzahl"] += k["anzahl"]
            z["andere_decks"] = list(je_deck.values())
        for f in ("im_deck", "verfuegbar", "vorhanden", "fehlt"):
            summe[f] += z[f]
        if z["preis"] is None:
            werte["ohne_preis"] += z["anzahl"]
        else:
            werte["liste"] += z["anzahl"] * z["preis"]
            werte["fehlt"] += z["fehlt"] * z["preis"]
    # Karten im Deck, die keine Zeile mehr braucht (z. B. nach „Neu laden“): zurücklegen
    ueberzaehlig = []
    for kid, k in alle.items():
        if k["deck_id"] == deck["id"] and rest[kid] > 0:
            ueberzaehlig.append({"karte_id": kid, "name": k["name"], "anzahl": rest[kid], "foil": bool(k["foil"]),
                                 "set_code": k["set_code"], "nummer": k["nummer"], "scryfall_id": k["scryfall_id"]})
    im = preise.werte(con, nutzer, "deck", preisart).get(deck["id"]) if preise.neuester_tag(con) else None
    hat_preise = preise.neuester_tag(con) is not None
    wert = {"liste": round(werte["liste"], 2) if hat_preise else None,
            "fehlt": round(werte["fehlt"], 2) if hat_preise else None,
            "im_deck": (im or {}).get("wert", 0.0) if hat_preise else None,
            "ohne_preis": werte["ohne_preis"], "preisart": preisart,
            "veraenderung": preise.veraenderung_gesamt(con, nutzer, preisart, deck_id=deck["id"])["vortag"]
            if hat_preise else None}
    gebaut = any(k["deck_id"] == deck["id"] for k in alle.values())
    return {**dict(deck), "karten": zeilen, "summe": summe, "wert": wert, "ueberzaehlig": ueberzaehlig,
            "gebaut": gebaut}


def fehlt_text(abgleich: dict) -> str:
    """Fehlt-Liste für Cardmarket („Wants“ → aus Text hinzufügen): eine Zeile „1x Name“ je Karte."""
    je_name: dict[str, int] = {}
    for z in abgleich["karten"]:
        if z["fehlt"]:
            je_name[z["name"]] = je_name.get(z["name"], 0) + z["fehlt"]
    return "".join(f"{n}x {name}\n" for name, n in je_name.items())


def uebersicht(con, nutzer: int) -> list[dict]:
    """Alle Decks mit Summen und Werten (für den Regalboden)."""
    ergebnis = []
    for d in con.execute("SELECT * FROM deck WHERE nutzer_id = ? ORDER BY regalplatz, id", (nutzer,)).fetchall():
        a = abgleichen(con, nutzer, d)
        commander = next((z for z in a["karten"] if z["commander"]), None)
        ergebnis.append({k: d[k] for k in ("id", "name", "quelle", "extern_id", "abgleich", "basics_auslassen",
                                           "geladen")}
                        | {"summe": a["summe"], "wert": a["wert"], "gebaut": a["gebaut"],
                           "baubar": statistik.baubar(d, a["summe"]),   # Paket 10
                           "commander": commander["name"] if commander else None,
                           "bild": commander["scryfall_id"] if commander else None})
    return ergebnis
