"""Bedingungen der Sortierregeln: Prüfen, Beschreiben und Übersetzen in SQL.

Eine Bedingung ist ein Wörterbuch mit `feld` aus FELDER, den Werten ihrer Art und optional `nicht` (verneint):

    {"feld": "farbe", "art": "genau", "farben": "R"}            Farbe genau Rot (auch farbidentitaet)
    {"feld": "farbe", "art": "enthaelt", "farben": "RG"}        enthält Rot und Grün
    {"feld": "farbe", "art": "mehrfarbig" | "einfarbig" | "farblos"}
    {"feld": "manawert", "von": 0, "bis": 5}                    von und bis einschließlich, eins darf fehlen
    {"feld": "haupttyp", "werte": ["land"]}                     eine der Auswahlen (auch seltenheit, set_art,
                                                                besonderheit, sprache)
    {"feld": "set", "werte": ["sld", "plst"]}                   Set-Kürzel
    {"feld": "typzeile", "text": "Legendary"}                   enthält (auch name), ohne Groß-/Kleinschreibung
    {"feld": "foil"}                                            ja (auch promo); mit "nicht": true = nein
    {"feld": "preis", "wert": "avg7" | "low" | … | "einstellung", "ab": 5, "unter": null}
                                                                Euro, Foil-Karten mit Foil-Preis; Arten wie
                                                                preise.werte.PREISARTEN, „einstellung“ = gewählte
    {"feld": "limit", "ueber": 4}                               über dem Limit: Exemplare desselben Drucks (Foil
                                                                getrennt, Sprache nicht) vor dieser Karte ≥ 4

Alle Bedingungen einer Regel müssen zutreffen (UND). Die Auswertung kennt drei Werte: zutreffend, nicht zutreffend
und unbekannt (ein Wert fehlt, zum Beispiel der Preis oder die Kartendaten). SQL liefert dafür 1, 0 und NULL; eine
Regel ist nicht zutreffend, sobald eine Bedingung 0 ist, sonst unbekannt, sobald eine NULL ist.

Die Ausdrücke beziehen sich auf `k` (karte), `d` (kartendaten, LEFT JOIN), `p` (preis des neuesten Tages,
LEFT JOIN) und `pa` (preisabruf des neuesten Tages, LEFT JOIN). Feldnamen und Operatoren stammen nur aus den festen Listen hier, Werte gehen als Parameter ein.
"""
from __future__ import annotations

import json
import re

from binderapp.db.erkennung import SCAN_MARKE
from binderapp.db.seiten import Fehler
from binderapp.katalog.kartendaten import BESONDERHEITEN, FARBFOLGE, FOIL_SONDERARTEN, HAUPTTYPEN
from binderapp.preise import werte as werte_preise

HOECHSTENS_BEDINGUNGEN = 12
HOECHSTENS_WERTE = 60
TEXT_LAENGE = 60

FARBARTEN = ("genau", "enthaelt", "mehrfarbig", "einfarbig", "farblos")
FARBNAMEN = {"W": "Weiß", "U": "Blau", "B": "Schwarz", "R": "Rot", "G": "Grün"}
TYPNAMEN = {"kreatur": "Kreatur", "planeswalker": "Planeswalker", "spontanzauber": "Spontanzauber",
            "hexerei": "Hexerei", "artefakt": "Artefakt", "verzauberung": "Verzauberung", "schlacht": "Schlacht",
            "land": "Land", "sonstige": "Sonstige"}
SELTENHEITEN = {"common": "Common", "uncommon": "Uncommon", "rare": "Rare", "mythic": "Mythic",
                "special": "Special", "bonus": "Bonus"}
BESONDERHEITSNAMEN = {"borderless": "Borderless", "showcase": "Showcase", "extendedart": "Extended Art",
                      "retro": "Retro-Rahmen", "fullart": "Full Art", "etched": "Etched", "textured": "Textured",
                      "galaxy": "Galaxy Foil", "surge": "Surge Foil", "serialized": "Nummeriert"}
SET_ARTEN = {"expansion": "Erweiterung", "core": "Grundset", "masters": "Masters", "draft_innovation": "Draft-Set",
             "commander": "Commander", "box": "Secret Lair und Boxen", "promo": "Promo-Set",
             "masterpiece": "Masterpiece", "funny": "Funny (Un-Sets, Playtest)", "memorabilia": "Memorabilia",
             "token": "Spielsteine", "duel_deck": "Duel Decks", "from_the_vault": "From the Vault",
             "premium_deck": "Premium Deck", "spellbook": "Signature Spellbook", "arsenal": "Arsenal",
             "starter": "Einsteigerprodukt", "eternal": "Eternal", "planechase": "Planechase",
             "archenemy": "Archenemy", "treasure_chest": "Treasure Chest", "alchemy": "Alchemy",
             "minigame": "Minispiel", "vanguard": "Vanguard"}
SPRACHEN = {"en": "Englisch", "de": "Deutsch", "fr": "Französisch", "it": "Italienisch", "es": "Spanisch",
            "pt": "Portugiesisch", "ja": "Japanisch", "ko": "Koreanisch", "ru": "Russisch", "zhs": "Chinesisch (verein.)",
            "zht": "Chinesisch (trad.)", "ph": "Phyrexianisch"}
# Preisart je Preisbedingung (Paket „Preisart wählbar“, 10.11.2026): jede Art einzeln oder „wie Einstellung“, dann
# gilt die Preisart des Nutzers (nutzer.preisart, ab Version 21). Bestehende Regeln behalten ihre Art.
EINSTELLUNG = "einstellung"
PREISWERTE = {EINSTELLUNG: "wie Einstellung", **werte_preise.NAMEN}

# Feld → Art und Anzeige. Die Oberfläche bekommt das als Beschreibung (`beschreibung()`).
FELDER = {
    "farbe": {"name": "Farbe", "art": "farbe"},
    "farbidentitaet": {"name": "Farbidentität", "art": "farbe"},
    "manawert": {"name": "Manawert", "art": "bereich"},
    "haupttyp": {"name": "Typ", "art": "auswahl", "werte": TYPNAMEN},
    "typzeile": {"name": "Typzeile enthält", "art": "text"},
    "seltenheit": {"name": "Seltenheit", "art": "auswahl", "werte": SELTENHEITEN},
    "set": {"name": "Set", "art": "liste"},
    "set_art": {"name": "Art des Sets", "art": "auswahl", "werte": SET_ARTEN},
    "besonderheit": {"name": "Special Print", "art": "auswahl", "werte": BESONDERHEITSNAMEN},
    "promo": {"name": "Promo", "art": "ja"},
    "foil": {"name": "Foil", "art": "ja"},
    "sprache": {"name": "Sprache", "art": "auswahl", "werte": SPRACHEN},
    "name": {"name": "Name enthält", "art": "text"},
    "preis": {"name": "Preis", "art": "preis"},
    "limit": {"name": "Über dem Limit", "art": "limit"},
}

# Spalte je Feld (nur aus dieser Liste kommen Spaltennamen ins SQL)
_SPALTE = {
    "farbe": "d.farben", "farbidentitaet": "d.farbidentitaet", "manawert": "d.manawert",
    "haupttyp": "d.haupttyp", "typzeile": "d.typzeile", "seltenheit": "d.seltenheit",
    "set": "lower(k.set_code)", "set_art": "d.set_art", "promo": "d.promo", "foil": "k.foil",
    "sprache": "k.sprache", "name": "k.name",
}
_ROHPREIS = {art: werte_preise.preis_ausdruck(art) for art in werte_preise.PREISARTEN}
# Fehlt der Preis endgültig, zählt er als 0 € (Entscheidung des Nutzers, 01.11.2026): Die Karte hat kein Produkt bei
# Cardmarket, oder der Preisabruf des neuesten Tages lief nach dem Eintragen ihrer Kartendaten und fand keinen
# Preis. Fehlen die Kartendaten oder steht der Preisnachtrag noch aus, bleibt der Preis unbekannt (die Karte wartet).
# `pa` ist der Preisabruf des neuesten Tages (LEFT JOIN).
_PREIS = {wert: (f"(CASE WHEN {roh} IS NOT NULL THEN {roh} WHEN d.scryfall_id IS NULL THEN NULL"
                 " WHEN d.cardmarket_id IS NULL THEN 0 WHEN pa.abgerufen > d.geaendert THEN 0 ELSE NULL END)")
          for wert, roh in _ROHPREIS.items()}
_PREIS[EINSTELLUNG] = ("(CASE (SELECT n.preisart FROM nutzer n WHERE n.id = k.nutzer_id) "
                       + " ".join(f"WHEN '{art}' THEN {sql}" for art, sql in _PREIS.items()) + " END)")
_SET = re.compile(r"^[a-z0-9]{2,8}$")

# Limit je Karte (Paket 4, Antworten des Nutzers 10.11.2026): gezählt wird je Druck (scryfall_id), Foil getrennt,
# Sprache nicht; alle Orte zählen (Binder, Kisten, Decks, auch der Scan-Eingang), alle Quellen (echte Exemplare).
# Eine Karte liegt über dem Limit N, wenn vor ihr schon N oder mehr Exemplare liegen. „Vor ihr“ heißt: zuerst alle
# Karten außerhalb des Scan-Eingangs, dann die im Scan-Eingang, jeweils nach Kennung. So bekommen in einem Lauf
# die ersten neuen Exemplare den normalen Platz und erst die darüber das Ziel der Limit-Regel, und eine Karte, die
# schon einsortiert ist, ändert ihren Wert nicht, wenn danach weitere kommen. Ein Posten mit mehreren Exemplaren
# wird nicht geteilt: er liegt nur dann über dem Limit, wenn alle seine Exemplare darüber liegen.
LIMIT_MAX = 999
_EINGANG = "(SELECT ki.id FROM kiste ki WHERE ki.nutzer_id = {a}.nutzer_id AND ki.extern_name = '" + SCAN_MARKE + "')"
_IM_EINGANG = "(COALESCE({a}.kiste_id = " + _EINGANG + ", 0))"
LIMIT_VORHER = (
    "(SELECT COALESCE(SUM(x.anzahl), 0) FROM karte x WHERE x.scryfall_id = k.scryfall_id"
    " AND +x.nutzer_id = k.nutzer_id AND x.foil = k.foil AND x.id <> k.id"
    " AND (x.kiste_id IS NOT NULL OR x.tasche_id IS NOT NULL OR x.deck_id IS NOT NULL)"
    f" AND ({_IM_EINGANG.format(a='x')} < {_IM_EINGANG.format(a='k')}"
    f" OR ({_IM_EINGANG.format(a='x')} = {_IM_EINGANG.format(a='k')} AND x.id < k.id)))")

# Fürs Grund-Feld: welcher Wert fehlt, wenn eine Bedingung unbekannt ist
FEHLT = {"preis": "Preis", "besonderheit": "Special-Print-Merkmale", "promo": "Promo-Angabe",
         "set_art": "Art des Sets", "sprache": "Sprache"}


def beschreibung() -> dict:
    """Felder mit Auswahlen für die Oberfläche."""
    return {
        "felder": {f: {"name": s["name"], "art": s["art"], **({"werte": s["werte"]} if "werte" in s else {})}
                   for f, s in FELDER.items()},
        "farben": FARBNAMEN, "farbarten": {"genau": "genau", "enthaelt": "enthält", "mehrfarbig": "mehrfarbig",
                                            "einfarbig": "einfarbig", "farblos": "farblos"},
        "preiswerte": PREISWERTE, "foil_sonderarten": list(FOIL_SONDERARTEN),
    }


# ---------------------------------------------------------------- Prüfen

def _zahl(wert, name: str, von: float, bis: float) -> float | None:
    if wert is None or wert == "":
        return None
    if isinstance(wert, bool) or not isinstance(wert, (int, float, str)):
        raise Fehler(f"{name}: keine Zahl.")
    try:
        z = float(str(wert).replace(",", "."))
    except ValueError:
        raise Fehler(f"{name}: keine Zahl.")
    if not (von <= z <= bis):
        raise Fehler(f"{name}: nur von {von:g} bis {bis:g}.")
    return z


def bedingung_pruefen(roh) -> dict:
    """Eine Bedingung aus der Anfrage prüfen und in die gespeicherte Form bringen (wirft Fehler)."""
    if not isinstance(roh, dict):
        raise Fehler("Eine Bedingung ist kein Objekt.")
    feld = roh.get("feld")
    if feld not in FELDER:
        raise Fehler("Unbekanntes Feld in einer Bedingung.")
    spec = FELDER[feld]
    art = spec["art"]
    b: dict = {"feld": feld}
    if art == "farbe":
        farbart = roh.get("art")
        if farbart not in FARBARTEN:
            raise Fehler(f"{spec['name']}: unbekannte Art.")
        b["art"] = farbart
        if farbart in ("genau", "enthaelt"):
            farben = roh.get("farben")
            if not isinstance(farben, str) or any(c not in FARBFOLGE for c in farben.upper()):
                raise Fehler(f"{spec['name']}: Farben nur aus W, U, B, R, G.")
            farben = "".join(c for c in FARBFOLGE if c in farben.upper())
            if not farben:
                raise Fehler(f"{spec['name']}: mindestens eine Farbe wählen.")
            b["farben"] = farben
    elif art == "bereich":
        von = _zahl(roh.get("von"), spec["name"], 0, 100)
        bis = _zahl(roh.get("bis"), spec["name"], 0, 100)
        if von is None and bis is None:
            raise Fehler(f"{spec['name']}: von oder bis angeben.")
        if von is not None and bis is not None and von > bis:
            raise Fehler(f"{spec['name']}: von ist größer als bis.")
        b["von"], b["bis"] = von, bis
    elif art in ("auswahl", "liste"):
        werte = roh.get("werte")
        if not isinstance(werte, list) or not werte or len(werte) > HOECHSTENS_WERTE:
            raise Fehler(f"{spec['name']}: mindestens einen Wert wählen.")
        sauber = []
        for w in werte:
            if not isinstance(w, str):
                raise Fehler(f"{spec['name']}: ungültiger Wert.")
            w = w.strip().lower()
            if art == "auswahl" and w not in spec["werte"]:
                raise Fehler(f"{spec['name']}: unbekannter Wert „{w[:20]}“.")
            if art == "liste" and not _SET.match(w):
                raise Fehler(f"{spec['name']}: „{w[:20]}“ ist kein Set-Kürzel.")
            if w not in sauber:
                sauber.append(w)
        b["werte"] = sauber
    elif art == "text":
        text = roh.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > TEXT_LAENGE:
            raise Fehler(f"{spec['name']}: Text mit 1 bis {TEXT_LAENGE} Zeichen.")
        b["text"] = text.strip()
    elif art == "preis":
        wert = roh.get("wert", "avg7")
        if wert not in PREISWERTE:
            raise Fehler("Preis: eine Preisart wählen (" + ", ".join(PREISWERTE.values()) + ").")
        ab = _zahl(roh.get("ab"), "Preis ab", 0, 100000)
        unter = _zahl(roh.get("unter"), "Preis unter", 0, 100000)
        if ab is None and unter is None:
            raise Fehler("Preis: ab oder unter angeben.")
        b["wert"], b["ab"], b["unter"] = wert, ab, unter
    elif art == "limit":
        n = _zahl(roh.get("ueber"), "Limit", 1, LIMIT_MAX)
        if n is None or n != int(n):
            raise Fehler(f"Limit: eine ganze Zahl von 1 bis {LIMIT_MAX} angeben.")
        b["ueber"] = int(n)
    if roh.get("nicht"):
        b["nicht"] = True
    return b


def bedingungen_pruefen(roh) -> list[dict]:
    if roh is None:
        return []
    if not isinstance(roh, list) or len(roh) > HOECHSTENS_BEDINGUNGEN:
        raise Fehler(f"Höchstens {HOECHSTENS_BEDINGUNGEN} Bedingungen.")
    return [bedingung_pruefen(b) for b in roh]


def laden(text: str | None) -> list[dict]:
    """Gespeicherte Bedingungen (JSON) lesen; was nicht mehr passt, macht die Regel ungültig (Fehler)."""
    return bedingungen_pruefen(json.loads(text or "[]"))


# ---------------------------------------------------------------- Beschreiben

def _zahltext(z: float) -> str:
    return f"{z:g}".replace(".", ",")


def text(b: dict) -> str:
    """Bedingung als kurzer deutscher Text, z. B. „Farbe genau Rot“ oder „Preis (Ø 7 Tage) ab 5 €“."""
    feld, spec = b["feld"], FELDER[b["feld"]]
    art = spec["art"]
    if art == "farbe":
        t = f"{spec['name']} " + {"genau": "genau", "enthaelt": "enthält", "mehrfarbig": "mehrfarbig",
                                  "einfarbig": "einfarbig", "farblos": "farblos"}[b["art"]]
        if b.get("farben"):
            t += " " + "/".join(FARBNAMEN[c] for c in b["farben"])
    elif art == "bereich":
        von, bis = b.get("von"), b.get("bis")
        if von is not None and bis is not None:
            t = f"{spec['name']} {_zahltext(von)}–{_zahltext(bis)}"
        elif von is not None:
            t = f"{spec['name']} ab {_zahltext(von)}"
        else:
            t = f"{spec['name']} bis {_zahltext(bis)}"
    elif art == "auswahl":
        t = f"{spec['name']}: " + ", ".join(spec["werte"].get(w, w) for w in b["werte"])
    elif art == "liste":
        t = f"{spec['name']}: " + ", ".join(w.upper() for w in b["werte"])
    elif art == "text":
        t = f"{spec['name']} „{b['text']}“"
    elif art == "ja":
        t = spec["name"]
    elif art == "limit":
        t = f"Über dem Limit von {b['ueber']} je Druck"
    else:
        teile = []
        if b.get("ab") is not None:
            teile.append(f"ab {_zahltext(b['ab'])} €")
        if b.get("unter") is not None:
            teile.append(f"unter {_zahltext(b['unter'])} €")
        t = f"Preis ({PREISWERTE[b['wert']]}) " + " und ".join(teile)
    return ("nicht: " + t) if b.get("nicht") else t


# ---------------------------------------------------------------- SQL

def bedingung_sql(b: dict) -> tuple[str, list]:
    """Ausdruck mit dem Wert 1, 0 oder NULL (unbekannt) und seine Parameter."""
    feld = b["feld"]
    art = FELDER[feld]["art"]
    if art == "farbe":
        spalte = _SPALTE[feld]
        if b["art"] == "genau":
            sql, p = f"{spalte} = ?", [b["farben"]]
        elif b["art"] == "enthaelt":
            sql, p = " AND ".join(f"instr({spalte}, ?) > 0" for _ in b["farben"]), list(b["farben"])
        elif b["art"] == "mehrfarbig":
            sql, p = f"length({spalte}) > 1", []
        elif b["art"] == "einfarbig":
            sql, p = f"length({spalte}) = 1", []
        else:
            sql, p = f"{spalte} = ''", []
    elif art == "bereich":
        teile, p = [], []
        if b.get("von") is not None:
            teile.append("d.manawert >= ?")
            p.append(b["von"])
        if b.get("bis") is not None:
            teile.append("d.manawert <= ?")
            p.append(b["bis"])
        sql = " AND ".join(teile)
    elif feld == "besonderheit":
        # „Foil-Sonderarten“ wählt die Oberfläche als Gruppe; hier zählt jedes Merkmal einzeln (eins genügt)
        werte = [w for w in BESONDERHEITEN if w in b["werte"]]
        sql = " OR ".join("(',' || d.besonderheiten || ',') LIKE ?" for _ in werte)
        p = [f"%,{w},%" for w in werte]
    elif art in ("auswahl", "liste"):
        sql = f"{_SPALTE[feld]} IN ({', '.join('?' for _ in b['werte'])})"
        p = list(b["werte"])
    elif art == "text":
        sql, p = f"instr(lower({_SPALTE[feld]}), ?) > 0", [b["text"].lower()]
    elif art == "ja":
        sql, p = f"{_SPALTE[feld]} = 1", []
    elif art == "limit":
        # ohne Druck unbekannt (die Karte wartet wie ohne Kartendaten)
        sql, p = f"CASE WHEN k.scryfall_id IS NULL THEN NULL ELSE {LIMIT_VORHER} >= ? END", [b["ueber"]]
    else:
        spalte = _PREIS[b["wert"]]
        teile, p = [], []
        if b.get("ab") is not None:
            teile.append(f"{spalte} >= ?")
            p.append(b["ab"])
        if b.get("unter") is not None:
            teile.append(f"{spalte} < ?")
            p.append(b["unter"])
        sql = " AND ".join(teile)
    sql = f"({sql})"
    if b.get("nicht"):
        sql = f"(NOT {sql})"
    return sql, p


def ausdruck_sql(bedingungen: list[dict]) -> tuple[str, list]:
    """Alle Bedingungen mit UND (dreiwertig wie SQL). Ohne Bedingung trifft die Regel immer zu."""
    if not bedingungen:
        return "(1)", []
    teile, p = [], []
    for b in bedingungen:
        s, q = bedingung_sql(b)
        teile.append(s)
        p += q
    return "(" + " AND ".join(teile) + ")", p


def und(werte: list) -> int | None:
    """UND über Einzelwerte 1/0/None wie in SQL: 0 schlägt alles, sonst None, sonst 1."""
    if any(w == 0 for w in werte):
        return 0
    if any(w is None for w in werte):
        return None
    return 1
