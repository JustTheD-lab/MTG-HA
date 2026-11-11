"""Kaufliste mit Preisalarm (Paket 7 der Arbeitsliste, ab Datenbankversion 23).

Antworten des Nutzers (10.11.2026): Die Kaufliste enthält die fehlenden Karten gewählter Decks (Schalter „in
Kaufliste“ je Deck) und eigene Einträge, mit Summe. Je Karte ein Wunschpreis, als Vorschlag 90 % des heutigen Preises
in der gewählten Preisart. Home Assistant bekommt einen Sensor für alle Treffer (Zahl der Karten unter Wunschpreis,
Liste als Attribut). Keine Kaufempfehlung, nur Zahlen.

Daten: `kauf_deck` (Decks in der Liste), `kauf_karte` (eigene Einträge und Wunschpreise), beide mit Historie; jede
Änderung ist ein Schritt mit Rückgängig (`schritte`). `kauf_produkt` (Cardmarket-Produkte je Eintrag) ist wie
`deck_produkt` aus dem Katalog abgeleitet und ohne Historie; der tägliche Preisabruf speichert auch diese Produkte.

Preise: fehlende Deckkarten über die Produkte ihrer Deckzeile (`deck_produkt`, bei „nur Name“ der günstigste Druck),
eigene Einträge über ihre Produkte (`kauf_produkt`), jeweils am neuesten Preistag; Foil-Einträge mit Foil-Preis. Ein
Preis 0 (in der Preisdatei „keine Daten“) zählt nicht.
Steht dieselbe Karte mehrfach in der Liste (zwei Decks, Deck und eigener Eintrag), zählt die Summe der Anzahlen und
der kleinste bekannte Preis.

Entscheidungen von Code (vom Nutzer zu bestätigen, 10.11.2026): Jedes Deck rechnet seine Fehlt-Liste für sich (wie die
Deckansicht); fehlt eine Karte zwei Decks, steht sie zweimal in der Anzahl. „Unter Wunschpreis“ heißt Preis ≤
Wunschpreis. Der Sensor zählt Karten (Einträge), nicht Exemplare.
"""
from __future__ import annotations

from binderapp.db import schritte
from binderapp.db.datenbank import version
from binderapp.decks import abgleich
from binderapp.preise import werte as preise
from binderapp.db.seiten import Fehler

AB_VERSION = 23
VORSCHLAG = 0.9          # Wunschpreis-Vorschlag: 90 % des heutigen Preises (Antwort des Nutzers)
ANZAHL_MAX = 999
PREIS_MAX = 1_000_000.0
NAME_MAX = 200
HA_HOECHSTENS = 50       # so viele Treffer stehen höchstens im Attribut des Sensors (Größe der Zustandsdaten)


def ab_version(con) -> None:
    if version(con) < AB_VERSION:
        raise Fehler("Die Kaufliste braucht die Datenbankversion 23. Bitte die App neu starten.", 409)


def schluessel(scryfall_id: str | None, oracle_id: str | None, name: str, genau: bool) -> str:
    """Schlüssel einer Karte der Liste: genauer Druck, sonst Name über die Oracle-ID, sonst der Name selbst."""
    if genau and scryfall_id:
        return "d:" + scryfall_id
    if oracle_id:
        return "o:" + oracle_id
    return "n:" + name.strip().lower()


# ------------------------------------------------------------------ Lesen


def decks_in_liste(con, nutzer: int) -> set[int]:
    return {z[0] for z in con.execute("SELECT deck_id FROM kauf_deck WHERE nutzer_id = ?", (nutzer,))}


def _preise(con, nutzer: int, preisart: str | None, art: str, deck_id: int | None = None) -> dict[int, tuple]:
    """Günstigster Preis je Kaufliste-Eintrag (`art` = "kauf") oder Deckzeile (`art` = "deck") am neuesten Preistag,
    Foil-Zeilen mit Foil-Preis, als (Preis, Cardmarket-Produkt dieses Preises). Ein Preis 0 heißt in der Preisdatei
    „keine Daten“ (z. B. Trend ohne Verkäufe) und zählt nicht, sonst wäre 0,00 € der günstigste Druck und jeder
    Wunschpreis erreicht. Zeilen ohne Preis (oder ohne Preistag, `preisart` None) bekommen (None, kleinste
    Produktnummer), damit der Link zu Cardmarket trotzdem geht."""
    if art == "kauf":
        tabelle, produkt, bedingung, wert = "kauf_karte", "kauf_produkt kp ON kp.kauf_karte_id", "z.nutzer_id = ?", nutzer
    else:
        tabelle, produkt, bedingung, wert = "deck_karte", "deck_produkt kp ON kp.deck_karte_id", "z.deck_id = ?", deck_id
    erg = {z[0]: (None, z[1]) for z in con.execute(
        f"SELECT z.id, MIN(kp.cardmarket_id) FROM {tabelle} z JOIN {produkt} = z.id WHERE {bedingung} GROUP BY z.id",
        (wert,))}
    tag = preise.neuester_tag(con)
    if tag is None or preisart is None:
        return erg
    ausdruck = preise.preis_ausdruck(preisart, karte="z")
    # SQLite: neben MIN() gibt eine nackte Spalte den Wert aus der Zeile mit dem kleinsten Preis
    sql = (f"SELECT z.id, MIN(NULLIF({ausdruck}, 0)), kp.cardmarket_id FROM {tabelle} z JOIN {produkt} = z.id"
           f" JOIN preis p ON p.cardmarket_id = kp.cardmarket_id AND p.tag = ? WHERE {bedingung} GROUP BY z.id")
    for z in con.execute(sql, (tag, wert)):
        if z[1] is not None:
            erg[z[0]] = (round(z[1], 2), z[2])
    return erg


def _guenstiger(p: dict, preis: float | None, produkt: int | None) -> None:
    """Übernimmt Preis und Produkt, wenn der Preis kleiner ist (ein Produkt ohne Preis nur, solange keins da ist)."""
    if preis is not None and (p["preis"] is None or preis < p["preis"]):
        p["preis"], p["cardmarket_id"] = preis, produkt
    elif p["cardmarket_id"] is None:
        p["cardmarket_id"] = produkt


def liste(con, nutzer: int, preisart: str | None = None) -> dict:
    """Kaufliste mit Preisen, Wunschpreisen, Treffern und Summen (ohne Preisart die Einstellung des Nutzers)."""
    ab_version(con)
    preisart = preise.aufloesen(con, preisart, nutzer)
    hat_preise = preise.neuester_tag(con) is not None
    an = decks_in_liste(con, nutzer)
    pos: dict[tuple[str, int], dict] = {}

    def eintrag(s: str, foil: int, basis: dict) -> dict:
        return pos.setdefault((s, foil), {
            "schluessel": s, "foil": bool(foil), "name": basis["name"], "oracle_id": basis.get("oracle_id"),
            "scryfall_id": basis.get("scryfall_id"), "set_code": basis.get("set_code"), "nummer": basis.get("nummer"),
            "genau": s.startswith("d:"), "anzahl": 0, "eigen": 0, "eintrag_id": None, "decks": [], "preis": None,
            "cardmarket_id": None, "wunschpreis": None})

    decks = []
    for d in con.execute("SELECT * FROM deck WHERE nutzer_id = ? ORDER BY regalplatz, id", (nutzer,)).fetchall():
        info = {"id": d["id"], "name": d["name"], "an": d["id"] in an, "fehlt": None, "fehlt_wert": None}
        decks.append(info)
        if not info["an"]:
            continue
        a = abgleich.abgleichen(con, nutzer, d, preisart)
        zeilen_preise = _preise(con, nutzer, preisart if hat_preise else None, "deck", d["id"])
        info["fehlt"], info["fehlt_wert"] = a["summe"]["fehlt"], 0.0 if hat_preise else None
        druck_modus = d["abgleich"] == "druck"
        for z in a["karten"]:
            if not z["fehlt"]:
                continue
            s = schluessel(z["scryfall_id"], z["oracle_id"], z["name"], druck_modus and bool(z["druck_angegeben"]))
            p = eintrag(s, int(bool(z["foil"])), z)
            p["anzahl"] += z["fehlt"]
            preis, produkt = zeilen_preise.get(z["id"], (None, None))
            _guenstiger(p, preis, produkt)
            if preis is not None:
                info["fehlt_wert"] = round(info["fehlt_wert"] + z["fehlt"] * preis, 2)
            for e in p["decks"]:
                if e["id"] == d["id"]:
                    e["anzahl"] += z["fehlt"]
                    break
            else:
                p["decks"].append({"id": d["id"], "name": d["name"], "anzahl": z["fehlt"]})

    eigene_preise = _preise(con, nutzer, preisart if hat_preise else None, "kauf")
    for k in con.execute("SELECT * FROM kauf_karte WHERE nutzer_id = ? ORDER BY id", (nutzer,)).fetchall():
        schl = (k["schluessel"], k["foil"])
        if k["anzahl"] == 0 and schl not in pos:
            continue   # nur ein gemerkter Wunschpreis einer Deckkarte, die gerade nicht fehlt
        p = eintrag(k["schluessel"], k["foil"], dict(k))
        p["anzahl"] += k["anzahl"]
        p["eigen"] = k["anzahl"]
        p["eintrag_id"] = k["id"]
        p["wunschpreis"] = k["wunschpreis"]
        _guenstiger(p, *eigene_preise.get(k["id"], (None, None)))

    zeilen = sorted(pos.values(), key=lambda p: (p["name"].lower(), p["foil"], p["set_code"] or "", p["nummer"] or ""))
    summe = {"positionen": len(zeilen), "karten": 0, "wert": 0.0, "ohne_preis": 0, "treffer": 0,
             "treffer_karten": 0, "treffer_wert": 0.0}
    for p in zeilen:
        if not hat_preise:
            p["preis"] = None
        p["vorschlag"] = round(p["preis"] * VORSCHLAG, 2) if p["preis"] is not None else None
        p["wert"] = round(p["anzahl"] * p["preis"], 2) if p["preis"] is not None else None
        p["unter_wunsch"] = p["preis"] is not None and p["wunschpreis"] is not None and p["preis"] <= p["wunschpreis"]
        summe["karten"] += p["anzahl"]
        if p["preis"] is None:
            summe["ohne_preis"] += p["anzahl"]
        else:
            summe["wert"] += p["wert"]
        if p["unter_wunsch"]:
            summe["treffer"] += 1
            summe["treffer_karten"] += p["anzahl"]
            summe["treffer_wert"] += p["wert"]
    summe["wert"] = round(summe["wert"], 2) if hat_preise else None
    summe["treffer_wert"] = round(summe["treffer_wert"], 2)
    return {"preisart": preisart, "preisart_name": preise.NAMEN[preisart], "preistag": preise.neuester_tag(con),
            "decks": decks, "karten": zeilen, "summe": summe, "text": text(zeilen)}


def text(zeilen: list[dict]) -> str:
    """Liste für Cardmarket („Wants“ → aus Text hinzufügen) im Format der Fehlt-Liste der Decks: „1x Name“."""
    je_name: dict[str, int] = {}
    for p in zeilen:
        je_name[p["name"]] = je_name.get(p["name"], 0) + p["anzahl"]
    return "".join(f"{n}x {name}\n" for name, n in je_name.items() if n)


def fuer_home_assistant(con, nutzer: int, preisart: str | None = None) -> dict | None:
    """Treffer für den Sensor: Zahl der Karten unter Wunschpreis, Liste (größte Ersparnis zuerst) als Attribut."""
    if version(con) < AB_VERSION:
        return None
    a = liste(con, nutzer, preisart)
    treffer = [p for p in a["karten"] if p["unter_wunsch"]]
    treffer.sort(key=lambda p: (p["preis"] - p["wunschpreis"], p["name"].lower()))
    return {"treffer": len(treffer), "karten": sum(p["anzahl"] for p in treffer),
            "preisart": a["preisart"], "preistag": a["preistag"], "gekuerzt": len(treffer) > HA_HOECHSTENS,
            "liste": [{"name": p["name"], "set": (p["set_code"] or "").upper() or None, "nummer": p["nummer"],
                       "foil": p["foil"], "anzahl": p["anzahl"], "preis": p["preis"], "wunschpreis": p["wunschpreis"]}
                      for p in treffer[:HA_HOECHSTENS]]}


def stand(con, nutzer: int) -> tuple | None:
    """Kennzeichen der Nutzerdaten der Kaufliste: ändert es sich, sendet Home Assistant die Werte gleich neu."""
    if version(con) < AB_VERSION:
        return None
    d = con.execute("SELECT COUNT(*), TOTAL(deck_id) FROM kauf_deck WHERE nutzer_id = ?", (nutzer,)).fetchone()
    k = con.execute("SELECT COUNT(*), TOTAL(id), TOTAL(anzahl), TOTAL(wunschpreis), MAX(geaendert) FROM kauf_karte"
                    " WHERE nutzer_id = ?", (nutzer,)).fetchone()
    return (*tuple(d), *tuple(k))


# ------------------------------------------------------------------ Prüfen der Eingaben


def anzahl_pruefen(wert, mindestens: int = 1) -> int:
    if isinstance(wert, bool) or not isinstance(wert, int) or not mindestens <= wert <= ANZAHL_MAX:
        raise Fehler(f"Die Anzahl muss eine ganze Zahl von {mindestens} bis {ANZAHL_MAX} sein.")
    return wert


def preis_pruefen(wert) -> float | None:
    if wert is None:
        return None
    if isinstance(wert, bool) or not isinstance(wert, (int, float)) or not 0 <= wert <= PREIS_MAX:
        raise Fehler("Der Wunschpreis muss eine Zahl ab 0 sein (Euro).")
    return round(float(wert), 2)


# ------------------------------------------------------------------ Schreiben (Transaktion des Aufrufers)


def deck_setzen(con, nutzer: int, deck_id: int, an: bool) -> str | None:
    """Schalter „in Kaufliste“ eines Decks; gibt den Schritt zurück (None, wenn sich nichts ändert)."""
    ab_version(con)
    if con.execute("SELECT 1 FROM deck WHERE id = ? AND nutzer_id = ?", (deck_id, nutzer)).fetchone() is None:
        raise Fehler("Dieses Deck gibt es nicht.", 404)
    z = con.execute("SELECT id FROM kauf_deck WHERE deck_id = ?", (deck_id,)).fetchone()
    if bool(z) == an:
        return None
    schritt = schritte.neuer_schritt()
    if an:
        schritte.einfuegen(con, nutzer, "kauf_deck", {"nutzer_id": nutzer, "deck_id": deck_id}, schritt=schritt)
    else:
        schritte.loeschen(con, nutzer, "kauf_deck", z[0], schritt=schritt)
    return schritt


def _produkte_schreiben(con, kat, kauf_id: int, z: dict) -> int:
    """Cardmarket-Produkte eines Eintrags aus dem Katalog (genauer Druck: eins, sonst alle Drucke der Karte)."""
    from binderapp.decks import lesen
    if kat is None:
        return 0
    ids = lesen.produkte_suchen(kat, {"druck_angegeben": z["schluessel"].startswith("d:"),
                                      "scryfall_id": z.get("scryfall_id"), "oracle_id": z.get("oracle_id")})
    con.execute("DELETE FROM kauf_produkt WHERE kauf_karte_id = ?", (kauf_id,))
    con.executemany("INSERT INTO kauf_produkt (kauf_karte_id, cardmarket_id) VALUES (?, ?)",
                    [(kauf_id, i) for i in ids])
    return len(ids)


def produkte_nachtragen(con, kat) -> int:
    """Einträge ohne Produkte (Katalog fehlte beim Anlegen, Eintrag per Rückgängig zurück) aus dem Katalog ergänzen."""
    if kat is None or version(con) < AB_VERSION:
        return 0
    offen = [dict(z) for z in con.execute(
        "SELECT * FROM kauf_karte k WHERE NOT EXISTS (SELECT 1 FROM kauf_produkt p WHERE p.kauf_karte_id = k.id)"
        " AND (k.scryfall_id IS NOT NULL OR k.oracle_id IS NOT NULL)")]
    return sum(_produkte_schreiben(con, kat, z["id"], z) > 0 for z in offen)


def _druck_lesen(kat, scryfall_id: str | None, oracle_id: str | None) -> dict:
    """Name, Oracle-ID, Set und Nummer aus dem Katalog (genauer Druck oder, nur Name, der englische Name)."""
    if kat is None:
        raise Fehler("Für eigene Einträge braucht die App den Kartenkatalog (die Erkennung am PC schickt ihn).", 409)
    if scryfall_id:
        z = kat.execute("SELECT name, oracle_id, set_code, nummer FROM druck WHERE scryfall_id = ? LIMIT 1",
                        (scryfall_id,)).fetchone()
        if z is None:
            raise Fehler("Diesen Druck gibt es im Katalog nicht.", 404)
        return {"name": z["name"], "oracle_id": z["oracle_id"], "scryfall_id": scryfall_id,
                "set_code": z["set_code"], "nummer": z["nummer"]}
    z = kat.execute("SELECT name FROM druck WHERE oracle_id = ? ORDER BY sprache <> 'en' LIMIT 1",
                    (oracle_id,)).fetchone()
    if z is None:
        raise Fehler("Diese Karte gibt es im Katalog nicht.", 404)
    return {"name": z["name"], "oracle_id": oracle_id, "scryfall_id": None, "set_code": None, "nummer": None}


def eintrag_anlegen(con, kat, nutzer: int, oracle_id: str | None, scryfall_id: str | None, foil: bool,
                    anzahl: int, wunschpreis: float | None) -> tuple[int, str]:
    """Eigener Eintrag (Name oder genauer Druck). Gibt es die Karte schon als Eintrag, wächst dessen Anzahl.
    Gibt (ID, Schritt) zurück."""
    ab_version(con)
    if not (oracle_id or scryfall_id):
        raise Fehler("Bitte „oracle_id“ (nur Name) oder „scryfall_id“ (genauer Druck) angeben.")
    d = _druck_lesen(kat, scryfall_id, oracle_id)
    s = schluessel(d["scryfall_id"], d["oracle_id"], d["name"], bool(scryfall_id))
    schritt = schritte.neuer_schritt()
    alt = con.execute("SELECT * FROM kauf_karte WHERE nutzer_id = ? AND schluessel = ? AND foil = ?",
                      (nutzer, s, int(foil))).fetchone()
    if alt is not None:
        neu = {"anzahl": min(ANZAHL_MAX, alt["anzahl"] + anzahl)}
        if wunschpreis is not None:
            neu["wunschpreis"] = wunschpreis
        schritte.aendern_genau(con, nutzer, "kauf_karte", alt["id"], neu, schritt=schritt)
        return alt["id"], schritt
    felder = {"nutzer_id": nutzer, "schluessel": s, "foil": int(foil), **d, "anzahl": anzahl,
              "wunschpreis": wunschpreis}
    kauf = schritte.einfuegen(con, nutzer, "kauf_karte", felder, schritt=schritt)
    _produkte_schreiben(con, kat, kauf, felder)
    return kauf, schritt


def _eintrag(con, nutzer: int, kauf: int):
    z = con.execute("SELECT * FROM kauf_karte WHERE id = ? AND nutzer_id = ?", (kauf, nutzer)).fetchone()
    if z is None:
        raise Fehler("Diesen Eintrag gibt es nicht.", 404)
    return z


def eintrag_aendern(con, nutzer: int, kauf: int, felder: dict) -> str | None:
    """Anzahl (0 = nur noch Wunschpreis einer Deckkarte) und Wunschpreis eines Eintrags."""
    ab_version(con)
    _eintrag(con, nutzer, kauf)
    schritt = schritte.neuer_schritt()
    return schritt if schritte.aendern_genau(con, nutzer, "kauf_karte", kauf, felder, schritt=schritt) else None


def eintrag_loeschen(con, nutzer: int, kauf: int) -> str:
    ab_version(con)
    _eintrag(con, nutzer, kauf)
    schritt = schritte.neuer_schritt()
    schritte.loeschen(con, nutzer, "kauf_karte", kauf, schritt=schritt)
    return schritt


def wunsch_setzen(con, kat, nutzer: int, schl: str, foil: bool, wunschpreis: float | None) -> str | None:
    """Wunschpreis einer Karte der Liste (auch einer fehlenden Deckkarte ohne eigenen Eintrag). Ohne Eintrag wird
    einer mit Anzahl 0 angelegt; wird dessen Wunschpreis gelöscht, verschwindet er wieder. Gibt den Schritt zurück."""
    ab_version(con)
    alt = con.execute("SELECT * FROM kauf_karte WHERE nutzer_id = ? AND schluessel = ? AND foil = ?",
                      (nutzer, schl, int(foil))).fetchone()
    schritt = schritte.neuer_schritt()
    if alt is not None:
        if wunschpreis is None and alt["anzahl"] == 0:
            schritte.loeschen(con, nutzer, "kauf_karte", alt["id"], schritt=schritt)
            return schritt
        return schritt if schritte.aendern_genau(con, nutzer, "kauf_karte", alt["id"], {"wunschpreis": wunschpreis},
                                                 schritt=schritt) else None
    if wunschpreis is None:
        return None
    p = next((p for p in liste(con, nutzer)["karten"] if p["schluessel"] == schl and p["foil"] == bool(foil)), None)
    if p is None:
        raise Fehler("Diese Karte steht nicht in der Kaufliste.", 404)
    felder = {"nutzer_id": nutzer, "schluessel": schl, "foil": int(foil), "name": p["name"][:NAME_MAX],
              **{k: p[k] for k in ("oracle_id", "scryfall_id", "set_code", "nummer")}, "anzahl": 0,
              "wunschpreis": wunschpreis}
    kauf = schritte.einfuegen(con, nutzer, "kauf_karte", felder, schritt=schritt)
    _produkte_schreiben(con, kat, kauf, felder)
    return schritt
