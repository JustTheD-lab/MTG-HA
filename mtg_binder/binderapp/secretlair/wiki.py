"""Namen und Verkaufstage der Secret-Lair-Drops aus MTG Wiki (Entscheidung des Nutzers, 09.11.2026).

Gemessen am 09.11.2026: MTG Wiki nennt Drops mit Sammlernummern und Verkaufstag, neue Superdrops etwa zwei Wochen
vor dem Verkauf (Star Trek am 27.10. für den 09.11.). Scryfall kennt keine Drop-Namen, MTGJSON kommt später und
sein Tag ist nicht der Verkaufstag. Drei Seiten über die MediaWiki-Schnittstelle (nur lesen):

- `Secret Lair/Drop Series`: Tabelle „Drop list“ mit Drop, Sammlernummern („2763 - 2767“) und Tag.
- Superdrop-Seiten der Kategorie `Secret Lair Drop Series` (die zuletzt aufgenommenen): Kartentabelle mit dem
  Drop je Nummer und der Tag in der Infobox („release = November 9, 2026“). Sie füllen nur Nummern, die die
  Haupttabelle noch nicht kennt.
- `Secret Lair/Bonus cards`: Bonuskarte (Nummer) → Drop.

Alles aus dem Wiki ist ungeprüfter Text: Übernommen werden nur Nummern, gültige Tage und Namen (gekürzt, ohne
Wiki-Auszeichnung). Fehlt eine Seite oder hat sie ein anderes Format, liefert sie einfach weniger.

    laden(oeffnen)   → {"nummern": {Nummer: [Drop, Tag | None]}, "bonus": {Nummer: Drop}, "seiten": n}
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import date

API = "https://mtg.wiki/api.php"
KOPF = {"User-Agent": "MTG-Binder (privat)", "Accept": "application/json"}
ZEIT = 60
GROESSE_MAX = 5_000_000   # Bytes je Antwort
SEITEN_MAX = 20           # Superdrop-Seiten je Abgleich (die zuletzt in die Kategorie aufgenommenen)
NAME_MAX = 80
HAUPT = "Secret Lair/Drop Series"
BONUS = "Secret Lair/Bonus cards"
KATEGORIE = "Category:Secret Lair Drop Series"
VORSATZ = re.compile(r"^(?:Secret Lair (?:Drop Series|Drop|Commander Deck)|Secret Lair)\s*:\s*", re.IGNORECASE)
UNBRAUCHBAR = re.compile(r"missing card|bonus cards|promos|unnamed", re.IGNORECASE)
MONATE = {m: i for i, m in enumerate(("January", "February", "March", "April", "May", "June", "July", "August",
                                       "September", "October", "November", "December"), 1)}
NAMENSZEILE = re.compile(r"^\|\s*(?:rowspan=\"?\d+\"?\s*\|)?\s*''(.+?)''\s*$")
NUMMER_KARTE = re.compile(r"SLD\|#=(\d{1,5})\b")


# ---------------------------------------------------------------- Text

def sauber(text: str) -> str:
    """Wiki-Auszeichnung entfernen: Fußnoten, Vorlagen, Links (der angezeigte Teil bleibt), Kursiv."""
    t = re.sub(r"<ref[^>]*/>|<ref.*?</ref>", "", text, flags=re.S)
    t = re.sub(r"\{\{series\|([^}|]*)(?:\|[^}]*)?\}\}", r"\1", t, flags=re.IGNORECASE)
    t = re.sub(r"\[\[[^|\]]*\|([^\]]*)\]\]", r"\1", t)
    t = re.sub(r"\[\[([^\]]*)\]\]", r"\1", t)
    t = re.sub(r"\{\{nowrap\|([^}]*)\}\}", r"\1", t, flags=re.IGNORECASE)
    t = re.sub(r"\{\{[^}]*\}\}", "", t)
    t = re.sub(r'rowspan="?\d+"?\s*\|', "", t)
    t = re.sub(r"<br\s*/?>", " ", t)
    t = re.sub(r"<[^>]+>|''+|&mdash;|&nbsp;", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def dropname(text: str) -> str:
    """Anzeigename: ohne „Secret Lair Drop Series:“ davor, höchstens `NAME_MAX` Zeichen."""
    return VORSATZ.sub("", sauber(text)).strip()[:NAME_MAX]


def norm(name: str) -> str:
    """Zum Vergleichen von Namen aus verschiedenen Quellen: nur Buchstaben und Ziffern, ohne Vorsatz."""
    return re.sub(r"\W+", "", VORSATZ.sub("", name or "").casefold())


def _tag(text: str) -> str | None:
    m = re.search(r"\d{4}-\d{2}-\d{2}", text or "")
    if not m:
        return None
    try:
        date.fromisoformat(m.group(0))
    except ValueError:
        return None
    return m.group(0)


def _zellen(zeile: str) -> list[str]:
    """Zellen einer Tabellenzeile (jede Zelle beginnt mit „|“ am Zeilenanfang)."""
    return re.split(r"\n\|(?![-}])", "\n" + zeile.strip())[1:]


# ---------------------------------------------------------------- Seiten lesen

def haupttabelle(text: str) -> dict[int, list]:
    """Tabelle „Drop list“: {Nummer: [Drop, Tag | None]}. Mehrere Namen in einer Zeile (mit <br> getrennt)
    verteilen sich auf die Nummern, wenn die Zahl passt."""
    anfang = text.find("===Drop list===")
    if anfang < 0:
        return {}
    ende = text.find("\n|}", anfang)
    aus: dict[int, list] = {}
    for zeile in text[anfang:ende if ende > 0 else None].split("\n|-"):
        zellen = _zellen(zeile)
        if len(zellen) < 4:
            continue
        m = re.fullmatch(r"(\d{1,5})\s*[-–]\s*(\d{1,5})|(\d{1,5})", sauber(zellen[2]))
        if not m:
            continue
        von, bis = (int(m.group(1)), int(m.group(2))) if m.group(1) else (int(m.group(3)),) * 2
        if bis < von or bis - von > 200:
            continue
        roh = [dropname(n) for n in re.split(r"<br\s*/?>", zellen[1])]
        roh = [n for n in roh if n]
        if not roh or any(UNBRAUCHBAR.search(n) for n in roh):
            continue
        tag = _tag(sauber(zellen[3]))
        for i, nummer in enumerate(range(von, bis + 1)):
            name = roh[i] if len(roh) == bis - von + 1 else " / ".join(roh)[:NAME_MAX]
            aus[nummer] = [name, tag]
    return aus


def superdrop(text: str) -> tuple[str | None, dict[int, str]]:
    """Superdrop-Seite: (Tag aus der Infobox, {Nummer: Drop}). Kurze Namen der Kartentabelle („Beam Me Up“) werden
    zum vollen Namen der Übersicht („Star Trek: Beam Me Up“), wenn er damit endet."""
    tag = None
    r = re.search(r"\|\s*release\s*=\s*([A-Z][a-z]+) (\d{1,2}), (\d{4})", text)
    if r and r.group(1) in MONATE:
        tag = _tag(f"{r.group(3)}-{MONATE[r.group(1)]:02d}-{int(r.group(2)):02d}")
    voll: list[str] = []
    drop, aus = None, {}
    gesehen_nummer = False
    for zeile in text.splitlines():
        k = NAMENSZEILE.match(zeile)
        if k:
            name = dropname(k.group(1))
            if not gesehen_nummer:
                voll.append(name)
            passend = [v for v in voll if norm(v).endswith(norm(name))]
            drop = passend[0] if passend else name
        for n in NUMMER_KARTE.findall(zeile):
            gesehen_nummer = gesehen_nummer or drop is not None
            if drop and not UNBRAUCHBAR.search(drop):
                aus.setdefault(int(n), drop)
    return tag, aus


def bonuskarten(text: str) -> dict[int, str]:
    """Bonuskarten: {Nummer: Drop}. Eine Drop-Zelle mit rowspan gilt für die folgenden Zeilen mit."""
    aus: dict[int, str] = {}
    weiter, rest = None, 0
    for zeile in text.split("\n|-"):
        nummer = re.search(r"^\|\s*(\d{3,5})\s*$", zeile.strip(), re.M)
        link = re.search(r"\[\[Secret Lair (?:Drop Series|Commander Deck)[^|\]]*\|([^\]]+)\]\]", zeile)
        if link:
            span = re.search(r'rowspan="?(\d+)"?\s*\|\s*\[\[Secret Lair', zeile)
            weiter, rest = dropname(link.group(1)), (int(span.group(1)) - 1 if span else 0)
            name = weiter
        elif rest > 0:
            name, rest = weiter, rest - 1
        else:
            name = None
        if nummer and name:
            aus[int(nummer.group(1))] = name
    return aus


# ---------------------------------------------------------------- Laden

def _holen(oeffnen, **parameter) -> dict:
    parameter.update(format="json", formatversion=2)
    anfrage = urllib.request.Request(f"{API}?{urllib.parse.urlencode(parameter)}", headers=KOPF)
    with oeffnen(anfrage, timeout=ZEIT) as antwort:
        roh = antwort.read(GROESSE_MAX + 1)
    if len(roh) > GROESSE_MAX:
        raise ValueError("Antwort von MTG Wiki zu groß")
    return json.loads(roh)


def _wikitext(oeffnen, seite: str) -> str:
    d = _holen(oeffnen, action="parse", page=seite, prop="wikitext", redirects=1)
    text = (d.get("parse") or {}).get("wikitext")
    if not isinstance(text, str):
        raise ValueError(f"MTG Wiki: Seite „{seite}“ ohne Text")
    return text


def laden(oeffnen=urllib.request.urlopen) -> dict:
    """Haupttabelle, die neuesten Superdrop-Seiten und die Bonuskarten. Wirft OSError oder ValueError, wenn die
    Haupttabelle fehlt; eine fehlende Superdrop- oder Bonusseite liefert nur weniger."""
    nummern = haupttabelle(_wikitext(oeffnen, HAUPT))
    if not nummern:
        raise ValueError("MTG Wiki: Tabelle „Drop list“ nicht gefunden")
    seiten = 1
    mitglieder = (_holen(oeffnen, action="query", list="categorymembers", cmtitle=KATEGORIE, cmsort="timestamp",
                         cmdir="desc", cmlimit=SEITEN_MAX).get("query") or {}).get("categorymembers") or []
    for m in mitglieder[:SEITEN_MAX]:
        titel = m.get("title") if isinstance(m, dict) else None
        if not isinstance(titel, str) or not titel.startswith("Secret Lair"):
            continue
        try:
            tag, karten = superdrop(_wikitext(oeffnen, titel))
        except (OSError, ValueError):
            continue
        seiten += 1
        for nummer, drop in karten.items():
            if nummer not in nummern:
                nummern[nummer] = [drop, tag]
    try:
        bonus = bonuskarten(_wikitext(oeffnen, BONUS))
        seiten += 1
    except (OSError, ValueError):
        bonus = {}
    return {"nummern": nummern, "bonus": bonus, "seiten": seiten}


def als_text(daten: dict) -> str:
    return json.dumps({"nummern": {str(k): v for k, v in daten["nummern"].items()},
                       "bonus": {str(k): v for k, v in daten["bonus"].items()}}, ensure_ascii=False,
                      separators=(",", ":"))


def aus_text(text: str | None) -> dict:
    """Gemerkter Stand (falls das Wiki beim nächsten Abgleich nicht erreichbar ist); kaputt oder leer → leer."""
    try:
        d = json.loads(text) if text else {}
        return {"nummern": {int(k): [str(v[0])[:NAME_MAX], _tag(v[1]) if v[1] else None]
                            for k, v in (d.get("nummern") or {}).items()},
                "bonus": {int(k): str(v)[:NAME_MAX] for k, v in (d.get("bonus") or {}).items()}}
    except (ValueError, TypeError, IndexError, AttributeError):
        return {"nummern": {}, "bonus": {}}
