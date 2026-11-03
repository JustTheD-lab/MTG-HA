"""Decklisten einlesen: Archidekt (öffentliche Schnittstelle, nur lesen) und Export-Text (Moxfield, Archidekt, Arena).

Ergebnis ist eine Liste von Zeilen {anzahl, name, set_code, nummer, scryfall_id, foil, commander}; `aufloesen` ergänzt
sie aus dem Katalog (oracle_id, englischer Name, Druck, basic). Nur das Hauptdeck mit Commander (Entscheidung des
Nutzers, 02.11.2026): Sideboard, Maybeboard und andere Kategorien, die bei Archidekt nicht zum Deck zählen, fallen weg.

Archidekt: `card.uid` ist die Scryfall-ID (geprüft am 02.11.2026). Die Schnittstelle ist nicht offiziell dokumentiert;
abgefragt wird nur `https://archidekt.com/api/decks/<Ziffern>/`, nie eine Adresse aus der Eingabe.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from binderapp.katalog.suche import normalisiere

ARCHIDEKT = "https://archidekt.com/api/decks/{id}/"
ARCHIDEKT_HOECHSTENS = 8 * 1024 * 1024
TEXT_HOECHSTENS = 200_000
ZEILEN_HOECHSTENS = 1500
ZEILE_LAENGE = 300      # längere Zeilen sind keine Kartenzeilen (und bremsen die regulären Ausdrücke)
ANZAHL_HOECHSTENS = 999
GESAMTZEIT = 30.0       # Sekunden für den ganzen Abruf bei Archidekt
RE_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# Abschnitte eines Export-Texts, die nicht zum Hauptdeck gehören
NICHT_IM_DECK = {"sideboard", "maybeboard", "considering", "tokens", "token", "about"}
# Kategorien (Archidekt): Es zählt nur die erste Kategorie einer Karte, weitere sind Etiketten. Eigene Kategorien wie
# „Tokens“ gehören zum Deck, solange Archidekt sie mitzählt (`includedInDeck`); Sideboard und Maybeboard nie.
KATEGORIE_NICHT_IM_DECK = {"sideboard", "maybeboard", "considering"}
RE_ARCHIDEKT = re.compile(r"^(?:https?://)?(?:www\.)?archidekt\.com/(?:api/)?decks/(\d{1,12})(?:[/?#].*)?$", re.I)
RE_ABSCHNITT = re.compile(r"^(?://\s*)?(commanders?|companion|deck|mainboard|main|sideboard|maybeboard|considering|"
                          r"tokens?|about)\s*:?\s*(?:\(\d+\))?$", re.I)
RE_ANZAHL = re.compile(r"^(\d{1,4})\s*[xX]?\s+(.+)$")
RE_DRUCK = re.compile(r"\s*\(([A-Za-z0-9]{2,6})\)\s*([A-Za-z0-9★\-]{1,10})?\s*$")


class DeckFehler(ValueError):
    def __init__(self, text: str, status: int = 400):
        super().__init__(text)
        self.status = status


# ---------------------------------------------------------------- Archidekt


def archidekt_id(eingabe) -> str:
    """Deck-ID aus einem Archidekt-Link (oder nur die Ziffern)."""
    text = str(eingabe or "").strip()
    if text.isdigit() and len(text) <= 12:
        return text
    m = RE_ARCHIDEKT.match(text)
    if not m:
        raise DeckFehler("Das ist kein Archidekt-Link (erwartet: https://archidekt.com/decks/12345/…).")
    return m.group(1)


class _KeineWeiterleitung(urllib.request.HTTPRedirectHandler):
    """Weiterleitungen werden nicht verfolgt: abgefragt wird nur archidekt.com, nie ein anderer Rechner."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Weiterleitung nicht erlaubt", headers, fp)


_OEFFNER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _KeineWeiterleitung())


def archidekt_holen(deck_id: str, zeitlimit: float = 20.0) -> dict:
    """Lädt ein öffentliches oder ungelistetes Deck von Archidekt (eine Anfrage nach außen)."""
    if not deck_id.isdigit():
        raise DeckFehler("Ungültige Deck-ID.")
    anfrage = urllib.request.Request(ARCHIDEKT.format(id=deck_id),
                                     headers={"Accept": "application/json", "User-Agent": "MTG-Binder (privat)"})
    ende = time.monotonic() + GESAMTZEIT
    try:
        with _OEFFNER.open(anfrage, timeout=zeitlimit) as antwort:
            teile, n = [], 0
            while n <= ARCHIDEKT_HOECHSTENS:
                if time.monotonic() > ende:
                    raise DeckFehler("Archidekt antwortet zu langsam. Bitte später noch einmal versuchen.", 502)
                stueck = antwort.read(65536)
                if not stueck:
                    break
                teile.append(stueck)
                n += len(stueck)
            daten = b"".join(teile)
    except urllib.error.HTTPError as f:
        if f.code in (401, 403, 404):
            raise DeckFehler("Archidekt kennt dieses Deck nicht oder es ist privat. Bitte in Archidekt auf "
                             "„öffentlich“ oder „ungelistet“ stellen.", 404)
        raise DeckFehler(f"Archidekt antwortet mit Fehler {f.code}. Bitte später noch einmal versuchen.", 502)
    except (OSError, ValueError) as f:
        raise DeckFehler(f"Archidekt ist nicht erreichbar ({f}).", 502)
    if len(daten) > ARCHIDEKT_HOECHSTENS:
        raise DeckFehler("Die Antwort von Archidekt ist zu groß.", 502)
    try:
        return json.loads(daten)
    except (ValueError, RecursionError):
        raise DeckFehler("Archidekt hat keine lesbare Deckliste geliefert.", 502)


def archidekt_zeilen(daten: dict) -> tuple[dict, list[dict]]:
    """(Kopf, Zeilen) aus der Antwort von Archidekt. Kopf: name, extern_id, extern_stand."""
    try:
        return _archidekt_zeilen(daten)
    except DeckFehler:
        raise
    except (TypeError, ValueError, AttributeError) as f:   # fremde Antwort: nie als 500 durchreichen
        raise DeckFehler(f"Archidekt hat keine lesbare Deckliste geliefert ({type(f).__name__}).", 502)


def _archidekt_zeilen(daten: dict) -> tuple[dict, list[dict]]:
    if not isinstance(daten, dict) or not isinstance(daten.get("cards"), list):
        raise DeckFehler("Archidekt hat keine lesbare Deckliste geliefert.", 502)
    if len(daten["cards"]) > ZEILEN_HOECHSTENS:
        raise DeckFehler(f"Mehr als {ZEILEN_HOECHSTENS} Zeilen gehen nicht.")
    kategorien = {k.get("name"): k for k in daten.get("categories") or [] if isinstance(k, dict)}
    zeilen = []
    for e in daten["cards"]:
        if not isinstance(e, dict) or e.get("deletedAt"):
            continue
        namen = [c for c in e.get("categories") or [] if isinstance(c, str)]
        erste = namen[0] if namen else ""
        if erste.lower() in KATEGORIE_NICHT_IM_DECK or kategorien.get(erste, {}).get("includedInDeck") is False:
            continue
        karte = e.get("card") or {}
        orakel = karte.get("oracleCard") or {}
        name = orakel.get("name") or karte.get("displayName")
        menge = e.get("quantity")
        if not name or isinstance(menge, bool) or not isinstance(menge, int) or not 1 <= menge <= ANZAHL_HOECHSTENS:
            continue
        uid_ = karte.get("uid")
        zeilen.append({
            "anzahl": menge, "name": str(name)[:ZEILE_LAENGE],
            "scryfall_id": uid_ if isinstance(uid_, str) and RE_UUID.match(uid_) else None,
            "set_code": ((karte.get("edition") or {}).get("editioncode") or None),
            "nummer": karte.get("collectorNumber") or None,
            "foil": str(e.get("modifier") or "").lower() in ("foil", "etched"),
            "commander": erste == "Commander" or bool(kategorien.get(erste, {}).get("isPremier")),
            "basic": "Basic" in (orakel.get("superTypes") or []),
        })
    kopf = {"name": str(daten.get("name") or "Archidekt-Deck")[:120], "extern_id": str(daten.get("id") or ""),
            "extern_stand": daten.get("updatedAt")}
    return kopf, zusammenfassen(zeilen)


# ---------------------------------------------------------------- Text


def text_zeilen(text: str) -> tuple[list[dict], list[str]]:
    """Zeilen aus einem Export-Text; gibt (Zeilen, nicht lesbare Zeilen) zurück.

    Erkannt werden z. B. „1 Sol Ring (C21) 263 *F*“ (Moxfield), „1x Sol Ring (c21) 263 [Ramp]“ (Archidekt),
    „1 Sol Ring“ und Abschnitte wie „Commander“, „Deck“, „Sideboard:“ (Sideboard und Maybeboard fallen weg).
    """
    if not isinstance(text, str) or not text.strip():
        raise DeckFehler("Die Deckliste ist leer.")
    if len(text) > TEXT_HOECHSTENS:
        raise DeckFehler("Die Deckliste ist zu lang.")
    zeilen, unlesbar = [], []
    abschnitt = "deck"
    for roh in text.splitlines():
        z = roh.strip()
        if len(z) > ZEILE_LAENGE:
            unlesbar.append(z[:120] + " …")
            continue
        if not z or z.startswith("#") or (z.startswith("//") and not RE_ABSCHNITT.match(z)):
            continue
        m = RE_ABSCHNITT.match(z)
        if m:
            wort = m.group(1).lower()
            abschnitt = "commander" if wort.startswith("commander") else wort if wort in NICHT_IM_DECK else "deck"
            continue
        if abschnitt in NICHT_IM_DECK:
            continue
        m = RE_ANZAHL.match(z)
        anzahl, rest = (int(m.group(1)), m.group(2)) if m else (1, z)
        commander = abschnitt == "commander"
        kategorien = re.findall(r"\[([^\]]*)\]", rest)
        if kategorien:   # Archidekt-Export: „[Draw,Maybeboard]“, „[Maybeboard{noDeck}{noPrice}]“; nur die erste zählt
            erste = re.split(r"[,{]", kategorien[0])[0].strip().lower()
            if erste in KATEGORIE_NICHT_IM_DECK or "{nodeck}" in kategorien[0].lower().replace(" ", ""):
                continue
            commander = commander or erste == "commander"
        rest = re.sub(r"\[[^\]]*\]|\^[^^]*\^", " ", rest)
        foil = bool(re.search(r"\*[FE]\*|\(foil\)|\(etched\)", rest, re.I))
        rest = re.sub(r"\*[A-Z]\*|\((?:foil|etched)\)", " ", rest, flags=re.I).strip()
        set_code = nummer = None
        d = RE_DRUCK.search(rest)
        if d:
            set_code, nummer = d.group(1).lower(), d.group(2)
            rest = rest[:d.start()].strip()
        name = rest.strip()
        if not name or anzahl < 1 or anzahl > ANZAHL_HOECHSTENS:
            unlesbar.append(z[:120])
            continue
        zeilen.append({"anzahl": anzahl, "name": name, "scryfall_id": None, "set_code": set_code, "nummer": nummer,
                       "foil": foil, "commander": commander, "basic": False})
    if len(zeilen) > ZEILEN_HOECHSTENS:
        raise DeckFehler(f"Mehr als {ZEILEN_HOECHSTENS} Zeilen gehen nicht.")
    return zusammenfassen(zeilen), unlesbar


def zusammenfassen(zeilen: list[dict]) -> list[dict]:
    """Gleiche Zeilen (Druck oder Name, Foil, Commander) zu einer mit Summe der Anzahl."""
    ergebnis: dict[tuple, dict] = {}
    for z in zeilen:
        schluessel = (z.get("scryfall_id") or (z.get("set_code"), z.get("nummer"), z["name"].lower()),
                      bool(z.get("druck_angegeben", True)), bool(z["foil"]), bool(z["commander"]))
        if schluessel in ergebnis:
            ergebnis[schluessel]["anzahl"] = min(ANZAHL_HOECHSTENS, ergebnis[schluessel]["anzahl"] + z["anzahl"])
        else:
            ergebnis[schluessel] = dict(z)
    return list(ergebnis.values())


# ---------------------------------------------------------------- Katalog


def _druck(kat, sql: str, werte: tuple) -> dict | None:
    cur = kat.execute(sql + " LIMIT 1", werte)
    z = cur.fetchone()
    return None if z is None else dict(zip([s[0] for s in cur.description], z))


def _oracle_nach_name(kat, name: str) -> str | None:
    for versuch in (name, name.split("//")[0], name.split(" / ")[0]):
        norm = normalisiere(versuch.strip())
        if norm:
            z = kat.execute("SELECT oracle_id FROM namen WHERE norm = ? LIMIT 1", (norm,)).fetchone()
            if z:
                return z[0]
    return None


def aufloesen(kat, zeilen: list[dict]) -> tuple[list[dict], list[str]]:
    """Ergänzt jede Zeile aus dem Katalog; gibt (Zeilen, unbekannte Namen) zurück. Unbekannte Zeilen bleiben mit
    ihrem Namen erhalten (oracle_id leer, Abgleich dann nach Name)."""
    unbekannt = []
    for z in zeilen:
        druck = None
        if z.get("scryfall_id"):
            druck = _druck(kat, "SELECT * FROM druck WHERE scryfall_id = ?", (z["scryfall_id"],))
        if druck is None and z.get("set_code") and z.get("nummer"):
            druck = _druck(kat, "SELECT * FROM druck WHERE set_code = ? AND nummer = ?",
                           (z["set_code"].lower(), z["nummer"]))
        z["druck_angegeben"] = druck is not None or bool(z.get("set_code") and z.get("nummer"))
        if druck is None:
            oracle = _oracle_nach_name(kat, z["name"])
            if oracle:   # Druck zum Anzeigen: der neueste mit Bild
                druck = _druck(kat, "SELECT * FROM druck WHERE oracle_id = ? ORDER BY bild_vorhanden DESC, "
                                    "erschienen DESC", (oracle,))
                if z["druck_angegeben"]:
                    z["druck_angegeben"] = False   # Set und Nummer passten nicht: nur der Name ist sicher
        if druck is None:
            unbekannt.append(z["name"])
            z.update(oracle_id=None, druck_angegeben=bool(z.get("scryfall_id")))
            continue
        z["oracle_id"] = druck["oracle_id"]
        z["name"] = druck["name"]
        z["basic"] = bool(z.get("basic")) or "Basic" in (druck.get("typzeile") or "")
        z["scryfall_id"] = druck["scryfall_id"]
        if z["druck_angegeben"]:
            z["set_code"], z["nummer"] = druck["set_code"], druck["nummer"]
        else:
            z["set_code"] = z["nummer"] = None
    return zusammenfassen(zeilen), unbekannt
