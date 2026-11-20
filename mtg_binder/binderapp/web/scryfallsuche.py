"""Scryfall-Suche in der Sammlung (Wunsch des Nutzers, 20.11.2026): Das Suchfeld im Regal, in der Gesamtsammlung
und in jeder Kiste versteht die Syntax von Scryfall.

`bedingung(text, katalog)` übersetzt den Suchtext in eine SQL-Bedingung über die Tabellen `k` (karte) und `kd`
(kartendaten) mit `?`-Platzhaltern; kein Text aus der Anfrage landet im SQL.

- Klammern, `or`, implizites „und“, Verneinung mit `-`, Anführungszeichen, `!"Genauer Name"`, reguläre Ausdrücke
  `/…/` bei name, t, o, fo.
- Lokal in der Datenbank: Name, mv/cmc, c, id, m, r, s/e, cn, st, lang, year, date, is:foil/nonfoil/promo/spell/
  permanent und die Besonderheiten (borderless, showcase, extendedart, fullart, etched).
- Lokal über den Katalog (Tabellen `kartentext` und `regel`, Antwort als Menge von Oracle-IDs): t, o, fo, kw, pow,
  tou, loy, f/legal/banned/restricted, is:commander, is:gamechanger. Ohne Katalog geht t über die Typzeile der
  Vorderseite, der Rest über Scryfall.
- Alles andere (otag, art, a, ft, frame, usd …) fragt Scryfall: Nur dieser eine Begriff geht als Suchtext hinaus
  (keine Daten der Sammlung), die Treffer werden mit der Sammlung abgeglichen (Oracle-ID, bei Merkmalen des
  Drucks Set und Nummer). Ergebnisse bleiben 30 Minuten im Speicher, damit das Blättern nicht neu fragt.

Abweichungen von Scryfall: `is:foil` meint das eigene Exemplar; Wörter ohne Schlüssel suchen im Namen (auch im
gedruckten, z. B. deutschen) und zählen einzeln; `order:`, `unique:`, `prefer:`, `display:` und `include:` werden
übergangen (die Ansicht sortiert selbst).
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import urllib.parse
from collections import OrderedDict
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from binderapp.web.helfer import Fehler

LAENGE = 300                  # so lang höchstens der Suchtext
BEGRIFFE_MAX = 30
REGEX_LAENGE = 120
SCRYFALL_SEITEN = 20          # je 175 Treffer, Abstand 0,5 s: höchstens rund 10 Sekunden
ZWISCHEN_SEKUNDEN = 1800
ZWISCHEN_MAX = 64

ORACLE = "COALESCE(kd.oracle_id, k.oracle_id)"
SATZ = "COALESCE(kd.set_code, LOWER(k.set_code))"
KD_DA = "kd.scryfall_id IS NOT NULL"

SCHLUESSEL = {
    "name": "name", "t": "t", "type": "t", "o": "o", "oracle": "o", "fo": "fo", "fulloracle": "fo",
    "kw": "kw", "keyword": "kw", "c": "c", "color": "c", "colour": "c", "id": "id", "identity": "id", "ci": "id",
    "commander": "id", "mv": "mv", "cmc": "mv", "manavalue": "mv", "m": "m", "mana": "m",
    "pow": "pow", "power": "pow", "tou": "tou", "toughness": "tou", "loy": "loy", "loyalty": "loy",
    "r": "r", "rarity": "r", "s": "s", "set": "s", "e": "s", "edition": "s", "cn": "cn", "number": "cn",
    "st": "st", "lang": "lang", "language": "lang", "year": "year", "date": "date",
    "f": "f", "format": "f", "legal": "f", "banned": "banned", "restricted": "restricted",
    "is": "is", "not": "not",
}
UEBERGANGEN = frozenset({"order", "sort", "direction", "dir", "unique", "prefer", "display", "include"})
# Begriffe, die bei Scryfall vom Druck abhängen: dort mit unique=prints suchen und nach Set und Nummer abgleichen
DRUCK_SCHLUESSEL = frozenset({"a", "artist", "ft", "flavor", "wm", "watermark", "frame", "border", "art", "atag",
                              "arttag", "stamp", "new", "usd", "eur", "tix", "game", "prints", "sets",
                              "papersets", "block", "b"})
# is:/not:-Werte, die vom Druck abhängen (die übrigen wie dfc, split, reserved gelten für die Karte)
DRUCK_IS = frozenset({"fullart", "promo", "reprint", "firstprint", "digital", "foil", "nonfoil", "etched", "glossy",
                      "textless", "booster", "hires", "oversized", "showcase", "extendedart", "borderless",
                      "colorshifted", "datestamped", "prerelease", "planeswalkerdeck", "spotlight", "serialized",
                      "boosterfun", "poster", "release", "buyabox", "intropack", "gameday", "league", "unique",
                      "covered", "stamped", "inverted", "retro", "galaxyfoil", "surgefoil", "textured"})
# Schlüssel, die mit einem Begriff für Scryfall zusammen hinausgehen dürfen (gleiche Bedeutung wie lokal; nicht:
# Wörter im Namen, die deutsch sein können, Sprache, das eigene Foil)
MIT_SCRYFALL = frozenset({"t", "o", "fo", "c", "id", "mv", "r", "s", "cn", "st", "f", "banned", "restricted"})

FARBNAMEN = {
    "white": "W", "blue": "U", "black": "B", "red": "R", "green": "G",
    "azorius": "WU", "dimir": "UB", "rakdos": "BR", "gruul": "RG", "selesnya": "GW",
    "orzhov": "WB", "izzet": "UR", "golgari": "BG", "boros": "RW", "simic": "GU",
    "bant": "GWU", "esper": "WUB", "grixis": "UBR", "jund": "BRG", "naya": "RGW",
    "abzan": "WBG", "jeskai": "URW", "sultai": "BGU", "mardu": "RWB", "temur": "GUR",
    "silverquill": "WB", "prismari": "UR", "witherbloom": "BG", "lorehold": "RW", "quandrix": "GU",
}
SELTENHEITEN = {"c": "common", "common": "common", "u": "uncommon", "uncommon": "uncommon", "r": "rare",
                "rare": "rare", "s": "special", "special": "special", "m": "mythic", "mythic": "mythic",
                "b": "bonus", "bonus": "bonus"}
SELTENHEIT_RANG = ("common", "uncommon", "rare", "special", "mythic", "bonus")
SPRACHEN = {"english": "en", "german": "de", "deutsch": "de", "french": "fr", "italian": "it", "spanish": "es",
            "portuguese": "pt", "japanese": "ja", "korean": "ko", "russian": "ru", "chinese": "zhs",
            "simplified": "zhs", "traditional": "zht", "phyrexian": "ph", "latin": "la", "hebrew": "he",
            "arabic": "ar", "greek": "grc", "sanskrit": "sa", "quenya": "qya"}
FORMAT_ALIAS = {"edh": "commander", "cedh": "commander", "pdh": "paupercommander", "duelcommander": "duel",
                "historicbrawl": "brawl", "oldschool": "oldschool", "pennydreadful": "penny"}
BESONDERHEITEN = ("borderless", "showcase", "extendedart", "fullart", "etched")
DAUERHAFT = ("kreatur", "artefakt", "verzauberung", "planeswalker", "land", "schlacht")
OPS = {":": "=", "=": "=", "!=": "<>", "<": "<", ">": ">", "<=": "<=", ">=": ">="}
ZAHL = re.compile(r"-?\d+(?:\.\d+)?")
RE_SCHLUESSEL = re.compile(r"([A-Za-z_]+)(!=|<=|>=|:|=|<|>)")


@dataclass(frozen=True)
class Begriff:
    schluessel: str | None   # None: Wort im Namen
    op: str                  # ":", "=", "!=", "<", ">", "<=", ">=" oder "!" (genauer Name)
    wert: str
    regex: bool = False
    roh: str = ""            # Schlüssel, wie getippt (für Scryfall und Meldungen)

    def text(self) -> str:
        if self.schluessel is None:
            return f'!"{self.wert}"' if self.op == "!" else self.wert
        if self.regex:
            wert = "/" + self.wert.replace("/", "\\/") + "/"
        elif not self.wert or re.search(r'[\s()"]', self.wert):
            wert = '"' + self.wert.replace('"', "") + '"'
        else:
            wert = self.wert
        return f"{self.roh}{self.op}{wert}"


# ---------------------------------------------------------------------------------------------------
# Zerlegen und Parsen


def _bis(text: str, i: int, ende: str, was: str) -> tuple[str, int]:
    """Liest von `i` (hinter dem öffnenden Zeichen) bis zum Zeichen `ende`; `\\` schützt das Folgezeichen."""
    teile = []
    while i < len(text):
        z = text[i]
        if z == "\\" and i + 1 < len(text) and (text[i + 1] == ende or ende == "/"):
            teile.append(text[i:i + 2] if ende == "/" and text[i + 1] != "/" else text[i + 1])
            i += 2
            continue
        if z == ende:
            return "".join(teile), i + 1
        teile.append(z)
        i += 1
    raise Fehler(f"Bei {was} fehlt das schließende Zeichen {ende}.")


def _wort(text: str, i: int) -> tuple[str, int]:
    j = i
    while j < len(text) and not text[j].isspace() and text[j] != ")":
        j += 1
    return text[i:j], j


def zerlegen(text: str) -> list:
    """Suchtext → Liste aus "(", ")", "-", "oder" und Begriffen."""
    text = (text.replace("„", '"').replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'"))
    aus: list = []
    i, n = 0, len(text)
    while i < n:
        z = text[i]
        if z.isspace():
            i += 1
        elif z in "()":
            aus.append(z)
            i += 1
        elif z == "-" and i + 1 < n and not text[i + 1].isspace() and text[i + 1] != ")":
            aus.append("-")
            i += 1
        elif z == "!":
            if i + 1 < n and text[i + 1] == '"':
                wert, i = _bis(text, i + 2, '"', "einem Namen in Anführungszeichen")
            else:
                wert, i = _wort(text, i + 1)
            if wert.strip():
                aus.append(Begriff(None, "!", wert.strip()))
        elif z == '"':
            wert, i = _bis(text, i + 1, '"', "einem Text in Anführungszeichen")
            if wert.strip():
                aus.append(Begriff(None, ":", wert.strip()))
        else:
            m = RE_SCHLUESSEL.match(text, i)
            if m and m.end() < n and not text[m.end()].isspace() and text[m.end()] != ")":
                roh, op, j = m.group(1), m.group(2), m.end()
                regex = False
                if text[j] == '"':
                    wert, i = _bis(text, j + 1, '"', f"„{roh}{op}“")
                elif text[j] == "/" and roh.lower() in ("name", "t", "type", "o", "oracle", "fo", "fulloracle"):
                    wert, i = _bis(text, j + 1, "/", f"„{roh}{op}“")
                    regex = True
                else:
                    wert, i = _wort(text, j)
                aus.append(Begriff(roh.lower(), op, wert, regex, roh.lower()))
            else:
                wort, i = _wort(text, i)
                if wort.lower() == "or":
                    aus.append("oder")
                elif wort.lower() != "and":
                    aus.append(Begriff(None, ":", wort))
    return aus


class _Parser:
    """oder := und ("or" und)* · und := einzel+ · einzel := "-" einzel | "(" oder ")" | Begriff"""

    def __init__(self, teile: list):
        self.teile, self.i = teile, 0

    def _jetzt(self):
        return self.teile[self.i] if self.i < len(self.teile) else None

    def oder(self):
        glieder = [self.und()]
        while self._jetzt() == "oder":
            self.i += 1
            glieder.append(self.und())
        return glieder[0] if len(glieder) == 1 else ("oder", glieder)

    def und(self):
        glieder = []
        while self._jetzt() not in (None, ")", "oder"):
            glieder.append(self.einzel())
        if not glieder:
            raise Fehler("Vor oder nach „or“ und in Klammern muss ein Suchbegriff stehen.")
        return glieder[0] if len(glieder) == 1 else ("und", glieder)

    def einzel(self):
        t = self._jetzt()
        if t == "-":
            self.i += 1
            if self._jetzt() in (None, ")", "oder"):
                raise Fehler("Nach „-“ muss ein Suchbegriff stehen.")
            return ("nicht", self.einzel())
        if t == "(":
            self.i += 1
            innen = self.oder()
            if self._jetzt() != ")":
                raise Fehler("Eine Klammer ist nicht geschlossen.")
            self.i += 1
            return innen
        self.i += 1
        return t


def parsen(text: str):
    teile = zerlegen(text)
    if not teile:
        return None
    p = _Parser(teile)
    baum = p.oder()
    if p.i < len(teile):
        raise Fehler("Eine Klammer „)“ ist zu viel.")
    return baum


def _begriffe(baum) -> list[Begriff]:
    if isinstance(baum, Begriff):
        return [baum]
    if baum[0] == "nicht":
        return _begriffe(baum[1])
    return [b for g in baum[1] for b in _begriffe(g)]


# ---------------------------------------------------------------------------------------------------
# Katalog und Scryfall (Mengen von Oracle-IDs bzw. „set/nummer“)

_sperre = threading.Lock()
_zwischen: OrderedDict = OrderedDict()
_scryfall_sperre = threading.Lock()
_scryfall = None


def _merken(schluessel, laden):
    jetzt = time.monotonic()
    with _sperre:
        treffer = _zwischen.get(schluessel)
        if treffer and jetzt - treffer[0] < ZWISCHEN_SEKUNDEN:
            _zwischen.move_to_end(schluessel)
            return treffer[1]
    wert = laden()
    with _sperre:
        _zwischen[schluessel] = (jetzt, wert)
        while len(_zwischen) > ZWISCHEN_MAX:
            _zwischen.popitem(last=False)
    return wert


def zwischenspeicher_leeren() -> None:
    with _sperre:
        _zwischen.clear()


def _regex(muster: str, wert) -> int:
    if wert is None:
        return 0
    try:
        return 1 if _regex_bauen(muster).search(str(wert)) else 0
    except re.error:
        return 0


_regex_cache: dict[str, re.Pattern] = {}


def _regex_bauen(muster: str) -> re.Pattern:
    r = _regex_cache.get(muster)
    if r is None:
        r = re.compile(muster, re.IGNORECASE)
        if len(_regex_cache) > 200:
            _regex_cache.clear()
        _regex_cache[muster] = r
    return r


class _Kontext:
    def __init__(self, katalog: Path | None, scryfall=None):
        self.katalog = Path(katalog) if katalog is not None and Path(katalog).is_file() else None
        self.scryfall = scryfall
        self._tabellen: set[str] | None = None

    def tabellen(self) -> set[str]:
        if self._tabellen is None:
            self._tabellen = set()
            if self.katalog is not None:
                try:
                    with closing(self._verbinden()) as kat:
                        self._tabellen = {z[0] for z in kat.execute(
                            "SELECT name FROM sqlite_master WHERE type = 'table'")}
                except sqlite3.Error:
                    self._tabellen = set()
        return self._tabellen

    def hat(self, tabelle: str) -> bool:
        return tabelle in self.tabellen()

    def _verbinden(self):
        kat = sqlite3.connect(f"{self.katalog.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False)
        kat.create_function("binder_regex", 2, _regex, deterministic=True)
        return kat

    def menge(self, sql: str, args: tuple) -> str:
        """Oracle-IDs aus dem Katalog als JSON-Liste (für json_each)."""
        stand = self.katalog.stat().st_mtime_ns

        def laden():
            with closing(self._verbinden()) as kat:
                return json.dumps(sorted({z[0] for z in kat.execute(sql, args) if z[0]}))
        return _merken(("katalog", str(self.katalog), stand, sql, args), laden)

    def scryfall_menge(self, b: Begriff, druck: bool, dazu: tuple[str, ...] = ()) -> str:
        """`dazu`: mit „und“ verbundene Begriffe, die Scryfall genauso versteht; sie engen die Treffer ein (lokal
        gelten sie ohnehin), damit z. B. „is:dfc t:creature“ nicht an zu vielen Treffern von „is:dfc“ scheitert."""
        anfrage = " ".join(dict.fromkeys((b.text(), *dazu)))
        return _merken(("scryfall", druck, anfrage), lambda: self._scryfall_fragen(anfrage, druck))

    def _scryfall_fragen(self, anfrage: str, druck: bool) -> str:
        from binderapp.katalog.abgleich import API, AbgleichFehler, Scryfall
        from binderapp.katalog.bau import oracle_von
        global _scryfall
        url = f"{API}/cards/search?" + urllib.parse.urlencode({"q": anfrage, "unique": "prints" if druck else "cards"})
        aus: set[str] = set()
        with _scryfall_sperre:
            scryfall = self.scryfall
            if scryfall is None:
                if _scryfall is None:
                    _scryfall = Scryfall()
                scryfall = _scryfall
            for _ in range(SCRYFALL_SEITEN):
                try:
                    antwort = scryfall.anfrage(url)
                except AbgleichFehler as f:
                    meldung = str(f)
                    if "mit 404" in meldung:
                        return json.dumps(sorted(aus))
                    if "mit 400" in meldung or "mit 422" in meldung:
                        raise Fehler(f"Scryfall versteht den Suchbegriff „{anfrage}“ nicht.")
                    raise Fehler(f"Scryfall ist gerade nicht erreichbar; „{anfrage}“ lässt sich nur dort suchen.", 503)
                for k in antwort.get("data") or []:
                    if not isinstance(k, dict):
                        continue
                    if druck:
                        if k.get("set") and k.get("collector_number"):
                            aus.add(f"{str(k['set']).lower()}/{k['collector_number']}")
                    else:
                        o = oracle_von(k)
                        if isinstance(o, str):
                            aus.add(o)
                if not antwort.get("has_more"):
                    return json.dumps(sorted(aus))
                url = antwort.get("next_page") or ""
                if not url.startswith(API + "/cards/search?"):
                    return json.dumps(sorted(aus))
        raise Fehler(f"„{anfrage}“ hat bei Scryfall zu viele Treffer (über {SCRYFALL_SEITEN * 175}). "
                     "Bitte mit weiteren Begriffen eingrenzen.")


def _in_menge(json_liste: str, druck: bool = False) -> tuple[str, list]:
    links = f"({SATZ} || '/' || COALESCE(k.nummer, ''))" if druck else ORACLE
    return f"{links} IN (SELECT value FROM json_each(?))", [json_liste]


# ---------------------------------------------------------------------------------------------------
# Übersetzen


def _enthaelt(wert: str) -> str:
    return "%" + wert.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _op(b: Begriff, nur_gleich: bool = False) -> str:
    if b.op == "!" or (nur_gleich and b.op not in (":", "=", "!=")):
        raise Fehler(f"„{b.roh}“ kennt nur „:“, „=“ und „!=“.")
    return OPS[b.op]


def _nur_doppelpunkt(b: Begriff) -> None:
    if b.op not in (":", "="):
        raise Fehler(f"„{b.roh}“ kennt nur „:“.")


def _zahl(b: Begriff) -> float:
    if not ZAHL.fullmatch(b.wert):
        raise Fehler(f"Bei „{b.roh}“ wird eine Zahl erwartet.")
    return float(b.wert)


def _farben(b: Begriff, spalte: str, standard: str) -> tuple[str, list]:
    wert = b.wert.lower()
    op = standard if b.op == ":" else b.op
    if b.op == "!":
        raise Fehler(f"„{b.roh}“ kennt „!“ nicht.")
    if ZAHL.fullmatch(wert):
        return f"({KD_DA} AND length({spalte}) {OPS[op]} ?)", [int(float(wert))]
    if wert in ("m", "multicolor", "multicolored"):
        return f"({KD_DA} AND length({spalte}) >= 2)", []
    if wert in ("c", "colorless"):
        return f"({KD_DA} AND {spalte} {'<>' if op == '!=' else '='} '')", []
    menge = FARBNAMEN.get(wert)
    if menge is None:
        if not wert or any(z not in "wubrg" for z in wert):
            raise Fehler(f"„{b.wert}“ ist keine Farbe (w, u, b, r, g, c, m oder Namen wie azorius).")
        menge = wert.upper()
    menge = "".join(dict.fromkeys(menge))
    drin = " AND ".join(f"instr({spalte}, '{z}') > 0" for z in menge)
    draussen = " AND ".join(f"instr({spalte}, '{z}') = 0" for z in "WUBRG" if z not in menge) or "1"
    gleich = f"({drin} AND {draussen})"
    teil = {
        ">=": drin, "<=": draussen, "=": gleich, "!=": f"NOT {gleich}",
        ">": f"({drin} AND length({spalte}) > {len(menge)})",
        "<": f"({draussen} AND length({spalte}) < {len(menge)})",
    }[op]
    return f"({KD_DA} AND {teil})", []


def _mana(b: Begriff) -> tuple[str, list]:
    wert = b.wert.upper()
    symbole = re.findall(r"\{([^}]+)\}", wert)
    if not symbole:
        symbole = re.findall(r"\d+|[WUBRGCXSE]", wert)
        if "".join(symbole) != wert:
            raise Fehler(f"„{b.wert}“ sind keine Manakosten (z. B. 2gg oder {{2}}{{G}}{{G}}).")
    kosten = "".join(f"{{{s}}}" for s in symbole)
    if b.op in ("=", "!="):
        return f"({KD_DA} AND kd.manakosten {OPS[b.op]} ?)", [kosten]
    if b.op not in (":", ">="):
        raise Fehler(f"„{b.roh}“ kennt nur „:“, „=“, „!=“ und „>=“.")
    teile, args = [], []
    for symbol in dict.fromkeys(symbole):
        s = f"{{{symbol}}}"
        teile.append("(length(kd.manakosten) - length(replace(kd.manakosten, ?, ''))) >= ?")
        args += [s, len(s) * symbole.count(symbol)]
    return f"({KD_DA} AND {' AND '.join(teile)})", args


def _katalog_text(b: Begriff, ctx: _Kontext, spalte: str) -> tuple[str, list] | None:
    """t, o, fo und name über die Tabelle `kartentext`; None ohne Tabelle."""
    _nur_doppelpunkt(b)
    if not ctx.hat("kartentext"):
        return None
    if b.regex:
        if len(b.wert) > REGEX_LAENGE:
            raise Fehler(f"Der reguläre Ausdruck ist zu lang (höchstens {REGEX_LAENGE} Zeichen).")
        try:
            re.compile(b.wert)
        except re.error:
            raise Fehler(f"„/{b.wert}/“ ist kein gültiger regulärer Ausdruck.")
        return _in_menge(ctx.menge(f"SELECT oracle_id FROM kartentext WHERE binder_regex(?, {spalte})", (b.wert,)))
    if "~" in b.wert and spalte in ("text", "volltext"):
        vorn = "(CASE WHEN instr(name, ' // ') > 0 THEN substr(name, 1, instr(name, ' // ') - 1) ELSE name END)"
        return _in_menge(ctx.menge(
            f"SELECT oracle_id FROM kartentext WHERE {spalte} LIKE replace(?, '~', {vorn}) ESCAPE '\\'",
            (_enthaelt(b.wert),)))
    return _in_menge(ctx.menge(f"SELECT oracle_id FROM kartentext WHERE {spalte} LIKE ? ESCAPE '\\'",
                               (_enthaelt(b.wert),)))


def _werte_vergleich(b: Begriff, ctx: _Kontext) -> tuple[str, list] | None:
    spalten = {"pow": "staerke", "tou": "widerstand", "loy": "loyalitaet"}
    if not ctx.hat("kartentext"):
        return None
    spalte = spalten[b.schluessel]
    op = OPS.get(b.op)
    if op is None:
        raise Fehler(f"„{b.roh}“ kennt „!“ nicht.")
    andere = spalten.get(SCHLUESSEL.get(b.wert.lower(), ""))
    if andere:
        sql = (f"SELECT oracle_id FROM kartentext WHERE {spalte} IS NOT NULL AND {andere} IS NOT NULL "
               f"AND CAST({spalte} AS REAL) {op} CAST({andere} AS REAL)")
        return _in_menge(ctx.menge(sql, ()))
    if b.wert == "*":
        return _in_menge(ctx.menge(f"SELECT oracle_id FROM kartentext WHERE {spalte} {op} '*'", ()))
    zahl = _zahl(b)
    return _in_menge(ctx.menge(
        f"SELECT oracle_id FROM kartentext WHERE {spalte} IS NOT NULL AND CAST({spalte} AS REAL) {op} ?", (zahl,)))


def _format(b: Begriff, ctx: _Kontext, art: str) -> tuple[str, list] | None:
    from binderapp.katalog.regeln import FORMATE
    _nur_doppelpunkt(b)
    if not ctx.hat("regel"):
        return None
    name = b.wert.lower().replace(" ", "").replace("_", "")
    name = FORMAT_ALIAS.get(name, name)
    if name not in FORMATE:
        raise Fehler(f"Das Format „{b.wert}“ kenne ich nicht. Möglich: {', '.join(FORMATE)}.")
    stelle = FORMATE.index(name) + 1
    zeichen = {"f": ("l", "r"), "banned": ("b",), "restricted": ("r",)}[art]
    platz = ", ".join("?" for _ in zeichen)
    return _in_menge(ctx.menge(f"SELECT oracle_id FROM regel WHERE substr(legal, ?, 1) IN ({platz})",
                               (stelle, *zeichen)))


def _ist(b: Begriff, ctx: _Kontext) -> tuple[str, list] | None:
    """is:… lokal; None, wenn nur Scryfall es weiß."""
    _nur_doppelpunkt(b)
    wert = b.wert.lower()
    if wert == "foil":
        return "k.foil = 1", []
    if wert == "nonfoil":
        return "k.foil = 0", []
    if wert == "promo":
        return f"({KD_DA} AND kd.promo = 1)", []
    if wert == "spell":
        return f"({KD_DA} AND kd.haupttyp <> 'land')", []
    if wert == "permanent":
        return f"({KD_DA} AND kd.haupttyp IN ({', '.join(repr(t) for t in DAUERHAFT)}))", []
    if wert in BESONDERHEITEN:
        return "(',' || COALESCE(kd.besonderheiten, '') || ',') LIKE ?", [f"%,{wert},%"]
    if wert == "commander" and ctx.hat("regel"):
        return _in_menge(ctx.menge("SELECT oracle_id FROM regel WHERE merkmale LIKE ?", ('%"kann_commander"%',)))
    if wert in ("gamechanger", "gc") and ctx.hat("regel"):
        return _in_menge(ctx.menge("SELECT oracle_id FROM regel WHERE game_changer = 1", ()))
    return None


def _lokal(b: Begriff, ctx: _Kontext) -> tuple[str, list] | None:
    """Ein Begriff als SQL; None, wenn nur Scryfall ihn beantworten kann."""
    s = b.schluessel
    if s is None:
        if b.op == "!":
            return ("(k.name = ? COLLATE NOCASE OR kd.name = ? COLLATE NOCASE OR kd.name LIKE ? ESCAPE '\\')",
                    [b.wert, b.wert, b.wert.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + " // %"])
        muster = _enthaelt(b.wert)
        return "(k.name LIKE ? ESCAPE '\\' OR kd.name LIKE ? ESCAPE '\\')", [muster, muster]
    s = SCHLUESSEL.get(s)
    if s is None:
        return None
    if s == "name":
        if b.regex:
            return _katalog_text(b, ctx, "name")
        _nur_doppelpunkt(b)
        muster = _enthaelt(b.wert)
        return "(k.name LIKE ? ESCAPE '\\' OR kd.name LIKE ? ESCAPE '\\')", [muster, muster]
    if s == "t":
        treffer = _katalog_text(b, ctx, "typen")
        if treffer is None and not b.regex:
            return "kd.typzeile LIKE ? ESCAPE '\\'", [_enthaelt(b.wert)]
        return treffer
    if s == "o":
        return _katalog_text(b, ctx, "text")
    if s == "fo":
        return _katalog_text(b, ctx, "volltext")
    if s == "kw":
        _nur_doppelpunkt(b)
        if not ctx.hat("kartentext"):
            return None
        return _in_menge(ctx.menge("SELECT oracle_id FROM kartentext WHERE schluesselwoerter LIKE ? ESCAPE '\\'",
                                   ("%|" + _enthaelt(b.wert.lower())[1:],)))
    if s in ("pow", "tou", "loy"):
        return _werte_vergleich(b, ctx)
    if s == "c":
        return _farben(b, "kd.farben", ">=")
    if s == "id":
        return _farben(b, "kd.farbidentitaet", "<=")
    if s == "mv":
        wert = b.wert.lower()
        if wert in ("even", "odd") and b.op in (":", "="):
            return (f"({KD_DA} AND kd.manawert = CAST(kd.manawert AS INTEGER) "
                    f"AND CAST(kd.manawert AS INTEGER) % 2 = ?)", [0 if wert == "even" else 1])
        return f"({KD_DA} AND kd.manawert {_op(b)} ?)", [_zahl(b)]
    if s == "m":
        return _mana(b)
    if s == "r":
        wert = SELTENHEITEN.get(b.wert.lower())
        if wert is None:
            raise Fehler(f"„{b.wert}“ ist keine Seltenheit (common, uncommon, rare, mythic, special, bonus).")
        if b.op in (":", "=", "!="):
            return f"({KD_DA} AND kd.seltenheit {OPS[b.op]} ?)", [wert]
        rang = "(CASE kd.seltenheit " + " ".join(f"WHEN '{w}' THEN {i}" for i, w in enumerate(SELTENHEIT_RANG)) + " END)"
        return f"({KD_DA} AND {rang} {_op(b)} ?)", [SELTENHEIT_RANG.index(wert)]
    if s == "s":
        return f"{SATZ} {_op(b, nur_gleich=True)} ?", [b.wert.lower()]
    if s == "cn":
        if b.op in ("<", ">", "<=", ">="):
            return f"(k.nummer GLOB '[0-9]*' AND CAST(k.nummer AS INTEGER) {OPS[b.op]} ?)", [int(_zahl(b))]
        return f"COALESCE(k.nummer, '') {_op(b, nur_gleich=True)} ? COLLATE NOCASE", [b.wert]
    if s == "st":
        return f"({KD_DA} AND kd.set_art {_op(b, nur_gleich=True)} ?)", [b.wert.lower()]
    if s == "lang":
        wert = b.wert.lower()
        if wert == "any":
            return "1", []
        return f"COALESCE(k.sprache, '') {_op(b, nur_gleich=True)} ?", [SPRACHEN.get(wert, wert)]
    if s == "year":
        return (f"({KD_DA} AND CAST(substr(kd.set_erschienen, 1, 4) AS INTEGER) {_op(b)} ?)", [int(_zahl(b))])
    if s == "date":
        if not re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", b.wert):
            return None   # z. B. date>=neo (Set statt Datum): Scryfall weiß es
        return f"({KD_DA} AND kd.set_erschienen {_op(b)} ?)", [b.wert]
    if s in ("f", "banned", "restricted"):
        return _format(b, ctx, s)
    if s == "is":
        return _ist(b, ctx)
    if s == "not":
        teil = _ist(b, ctx)
        return None if teil is None else (f"NOT {teil[0]}", teil[1])
    return None


def _fuer_scryfall(b) -> bool:
    return isinstance(b, Begriff) and SCHLUESSEL.get(b.schluessel or "") in MIT_SCRYFALL


def _uebersetzen(baum, ctx: _Kontext, dazu: tuple[str, ...] = ()) -> tuple[str, list]:
    """`dazu`: Texte der mit „und“ verbundenen Begriffe darüber (nicht durch eine Verneinung hindurch)."""
    if isinstance(baum, Begriff):
        if baum.schluessel in UEBERGANGEN:
            return "1", []
        teil = _lokal(baum, ctx)
        if teil is None:   # nur Scryfall weiß es
            if baum.schluessel in ("is", "not"):
                druck = baum.wert.lower() in DRUCK_IS
            else:
                druck = baum.schluessel in DRUCK_SCHLUESSEL
            return _in_menge(ctx.scryfall_menge(baum, druck, dazu), druck)
        return teil
    if baum[0] == "nicht":
        sql, args = _uebersetzen(baum[1], ctx)   # ohne `dazu`: nicht (A und B) ist nicht (nicht A) und B
        return f"NOT ({sql})", args
    if baum[0] == "und":
        dazu = (*dazu, *(g.text() for g in baum[1] if _fuer_scryfall(g)))
    teile = [_uebersetzen(g, ctx, dazu) for g in baum[1]]
    verbinder = " AND " if baum[0] == "und" else " OR "
    return "(" + verbinder.join(f"({sql})" for sql, _ in teile) + ")", [a for _, args in teile for a in args]


def bedingung(text: str, katalog: Path | None = None, scryfall=None) -> tuple[str, list] | None:
    """SQL-Bedingung (über `k` und `kd`) mit Werten zum Suchtext; None bei leerem Text. Fehler als `Fehler` (400,
    Scryfall nicht erreichbar 503)."""
    text = (text or "").strip()
    if not text:
        return None
    if len(text) > LAENGE:
        raise Fehler(f"Der Suchtext ist zu lang (höchstens {LAENGE} Zeichen).")
    baum = parsen(text)
    if baum is None:
        return None
    if len(_begriffe(baum)) > BEGRIFFE_MAX:
        raise Fehler(f"Höchstens {BEGRIFFE_MAX} Suchbegriffe auf einmal.")
    return _uebersetzen(baum, _Kontext(katalog, scryfall))
