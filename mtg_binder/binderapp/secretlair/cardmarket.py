"""Secret-Lair-Drops als Cardmarket-Produkte: Preis des versiegelten Drops und Link (Wunsch des Nutzers, 09.11.2026).

Quelle ist der Produktkatalog von Cardmarket (`products_nonsingles_1.json`, etwa 1 MB, frei auf demselben Speicher wie
die Preisdatei). Cardmarket führt einen Drop meist zweimal, als „… Secret Lair x Star Trek: Lower Decks“ und als
„… Lower Decks Set“; die App zeigt beide (Antwort des Nutzers). Zugeordnet wird über das Namensende:

- Der Produktname (ohne Endung „Set“) endet auf den Namen des Drops (nur Buchstaben und Ziffern verglichen), sonst
  auf dessen letzten Teil und enthält den ersten (Superdrop im Namen, siehe `passende`).
- Das Produkt kam höchstens `TAGE` vor oder nach dem Verkaufstag zu Cardmarket.
- Je Fassung genau ein Produkt, sonst keins (nie raten). Bundles, Boxen und „Foil Edition“ zählen nicht.

Preise (Antwort des Nutzers): „ab“ und Trend, dazu Foil; „Ø 7 Tage“ gibt es für versiegelte Produkte nicht. Sie kommen
aus der Preisdatei des Tages (`preise_schreiben`). Link: `https://www.cardmarket.com/de/Magic/Products?idProduct=…`,
wie Scryfall verlinkt (keine geratenen Adressen).
"""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import date

from binderapp.preise import abruf
from binderapp.secretlair import wiki

URL = "https://downloads.s3.cardmarket.com/productCatalog/productList/products_nonsingles_1.json"
ZEIT = 120
GROESSE_MAX = 30_000_000
TAGE = 90             # Neuverkäufe („Chaos Vault“) kommen bis etwa 80 Tage nach dem Verkaufstag zu Cardmarket
NAME_MIN = 5          # kürzere Drop-Namen (verglichen) ordnet die App nicht zu
TEIL_MIN = 8          # letzter Namensteil beim zweiten Vergleich
NAME_MAX = 200
AUSGESCHLOSSEN = re.compile(r"\b(Bundle|Box|Foil Edition)$", re.IGNORECASE)
SET_ENDUNG = re.compile(r"\s+Set$")


def lesen(roh: bytes) -> list[dict]:
    """Secret-Lair-Produkte aus dem Katalog: [{"id", "name", "seit"}]."""
    aus = []
    for p in json.loads(roh).get("products") or []:
        if not isinstance(p, dict):
            continue
        name, pid, seit = p.get("name"), p.get("idProduct"), str(p.get("dateAdded") or "")[:10]
        if (isinstance(name, str) and "secret lair" in name.casefold() and isinstance(pid, int)
                and not isinstance(pid, bool) and pid > 0):
            aus.append({"id": pid, "name": name[:NAME_MAX], "seit": seit if re.fullmatch(r"\d{4}-\d{2}-\d{2}", seit) else None})
    if not aus:
        raise ValueError("Cardmarket-Katalog ohne Secret-Lair-Produkte")
    return aus


def laden(oeffnen=urllib.request.urlopen) -> list[dict]:
    anfrage = urllib.request.Request(URL, headers={"User-Agent": "MTG-Binder (privat)"})
    with oeffnen(anfrage, timeout=ZEIT) as antwort:
        roh = antwort.read(GROESSE_MAX + 1)
    if len(roh) > GROESSE_MAX:
        raise ValueError("Cardmarket-Katalog zu groß")
    return lesen(roh)


def als_text(produkte: list[dict]) -> str:
    return json.dumps(produkte, ensure_ascii=False, separators=(",", ":"))


def aus_text(text: str | None) -> list[dict]:
    try:
        d = json.loads(text) if text else []
        return [{"id": int(p["id"]), "name": str(p["name"])[:NAME_MAX], "seit": p.get("seit")} for p in d]
    except (ValueError, TypeError, KeyError):
        return []


def _abstand(seit: str | None, verkauf: str | None) -> int:
    if not seit or not verkauf:
        return 0
    return abs((date.fromisoformat(seit) - date.fromisoformat(verkauf)).days)


def _suche(produkte: list[dict], verkauf: str | None, passt) -> list[tuple[dict, int]]:
    je_fassung: dict[int, list[dict]] = {0: [], 1: []}
    for p in produkte:
        if AUSGESCHLOSSEN.search(p["name"]) or _abstand(p["seit"], verkauf) > TAGE:
            continue
        if passt(wiki.norm(SET_ENDUNG.sub("", p["name"]))):
            je_fassung[1 if SET_ENDUNG.search(p["name"]) else 0].append(p)
    return [(liste[0], f) for f, liste in je_fassung.items() if len(liste) == 1]


def passende(name: str, verkauf: str | None, produkte: list[dict]) -> list[tuple[dict, int]]:
    """Produkte zu einem Drop: [(Produkt, Set-Fassung 0/1)], je Fassung höchstens eins; leer, wenn unsicher.

    Zuerst endet der Produktname auf den ganzen Namen des Drops. Sonst (Cardmarket stellt den Superdrop dazwischen,
    „Secret Lair x Avatar: The Last Airbender Superdrop: A Lot to Learn“) endet er auf den letzten Teil des Namens
    und enthält den ersten („Avatar“)."""
    gesucht = wiki.norm(name)
    if len(gesucht) < NAME_MIN:
        return []
    treffer = _suche(produkte, verkauf, lambda n: n.endswith(gesucht))
    teile = [wiki.norm(t) for t in name.split(":")]
    if not treffer and len(teile) >= 2 and len(teile[-1]) >= TEIL_MIN and len(teile[0]) >= NAME_MIN:
        treffer = _suche(produkte, verkauf, lambda n: n.endswith(teile[-1]) and teile[0] in n[:-len(teile[-1])])
    return treffer


def zuordnen(con, produkte: list[dict]) -> int:
    """Schreibt die Produkte aller Drops neu (in der Transaktion des Aufrufers). Umbenannte Drops aus dem Abgleich
    behalten ihre Produkte, wenn der neue Name keins findet. Gibt die Zahl der Drops mit Produkt zurück."""
    preise = {z["cardmarket_id"]: tuple(z)[1:] for z in con.execute(
        "SELECT cardmarket_id, low, trend, low_foil, trend_foil FROM secret_produkt")}
    mit = 0
    for d in con.execute("SELECT id, name, verkauf, quelle, umbenannt FROM secret_drop").fetchall():
        treffer = passende(d["name"], d["verkauf"], produkte)
        if not treffer and d["umbenannt"] and d["quelle"] != "hand":
            mit += con.execute("SELECT 1 FROM secret_produkt WHERE drop_id = ? LIMIT 1", (d["id"],)).fetchone() is not None
            continue
        con.execute("DELETE FROM secret_produkt WHERE drop_id = ?", (d["id"],))
        for p, fassung in treffer:
            con.execute("INSERT INTO secret_produkt (drop_id, cardmarket_id, name, set_fassung, low, trend, low_foil,"
                        " trend_foil) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (d["id"], p["id"], p["name"], fassung, *preise.get(p["id"], (None,) * 4)))
        mit += bool(treffer)
    return mit


def _zahl(wert):
    return float(wert) if isinstance(wert, (int, float)) and not isinstance(wert, bool) and wert > 0 else None


def preise_schreiben(con, roh: bytes) -> int:
    """Preise der zugeordneten Produkte aus der Preisdatei des Tages (in der Transaktion des Aufrufers)."""
    ids = {z[0] for z in con.execute("SELECT DISTINCT cardmarket_id FROM secret_produkt")}
    if not ids:
        return 0
    gefunden = 0
    for e in abruf.EINTRAG.finditer(roh):
        kennung = abruf.ID.search(e.group())
        if kennung is None or int(kennung.group(1)) not in ids:
            continue
        d = json.loads(e.group())
        gefunden += con.execute("UPDATE secret_produkt SET low = ?, trend = ?, low_foil = ?, trend_foil = ?"
                                " WHERE cardmarket_id = ?",
                                (_zahl(d.get("low")), _zahl(d.get("trend")), _zahl(d.get("low-foil")),
                                 _zahl(d.get("trend-foil")), int(kennung.group(1)))).rowcount
    return gefunden


def lesen_je_drop(con) -> dict[int, list[dict]]:
    aus: dict[int, list[dict]] = {}
    for z in con.execute("SELECT drop_id, cardmarket_id, name, set_fassung, low, trend, low_foil, trend_foil"
                         " FROM secret_produkt ORDER BY drop_id, set_fassung, cardmarket_id"):
        aus.setdefault(z["drop_id"], []).append({k: z[k] for k in z.keys() if k != "drop_id"})
    return aus
