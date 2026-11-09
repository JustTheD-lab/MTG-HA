"""Secret-Lair-Drops finden und bewerten (Wunsch des Nutzers, 05.11.2026). Die App zeigt Zahlen, keine Kaufempfehlung.

Cardmarket: Preis des versiegelten Drops und Link über `secretlair/cardmarket.py` (Produktkatalog, gemerkt in
`secret_wert.cm_produkte`), Preise beim Bewerten aus derselben Preisdatei.

Quellen und was sie taugen (gemessen am Star-Trek-Drop, vier Tage vor dem Verkauf; MTG Wiki am 09.11.2026):
- **MTG Wiki (`secretlair/wiki.py`, zuerst):** Drop-Name und Verkaufstag je Sammlernummer, neue Superdrops etwa zwei
  Wochen vorher, dazu welche Bonuskarte zu welchem Drop gehört. Ist das Wiki nicht erreichbar, gilt der zuletzt
  gemerkte Stand (`secret_wert.wiki`).
- **Katalog (Scryfall):** kennt viele Karten früh, oft mit Cardmarket-Produkt, aber keinen Namen des Drops. `erschienen`
  ist der Verkaufstag. Karten ohne Drop werden nach Tag und fortlaufender Sammlernummer gruppiert; das ist nur eine
  Näherung, nebeneinanderliegende Drops können verschmelzen.
- **MTGJSON (`SLD.json`, Abschnitt `decks`, dazu `subsets` je Karte):** nennt Drops und ihre Karten, kommt aber später. Sein `releaseDate` ist
  nicht der Verkaufstag (Corn Maze: 14.12. statt 12.10.2026), darum gilt immer das Datum aus dem Katalog.
- **Bonuskarten** (Nummern 501–999 und ab 7000, auch Promos): Sie hängen als Bonus am Drop, den Wiki oder MTGJSON
  nennen, und zählen nicht zum Wert; ohne bekannten Drop erscheinen sie nicht (Antwort des Nutzers, 09.11.2026).
- **Fertige Decks** (ab Version 19, Hinweis des Nutzers, 09.11.2026): Commander-Decks und andere Decks, deren Typ in
  MTGJSON nicht „Secret Lair Drop“ ist, enthalten neben den Secret-Lair-Karten Reprints aus anderen Sets (meist The
  List, `plst`) und haben je Karte eine feste Ausführung (`isFoil`, meist nur die Secret-Lair-Karten Foil). Die ganze
  Liste zählt zum Wert (`secret_karte.deck` = 1 für die Reprints, `foil` = Ausführung im Produkt); die Scryfall-IDs der
  fremden Karten stehen nur in den Deckdateien von MTGJSON (`decks/<Datei>.json`), gemerkt in `secret_wert.deck_ids`.
- **Von Hand:** Der Nutzer fügt die Kartenliste aus der Ankündigung von Wizards ein. Sobald der Katalog die Drucke
  kennt, verbindet der Abgleich sie, und sie verschwinden aus den automatischen Gruppen.

Bewertung je Karte aus der Preisdatei des Tages (Ø 7 Tage, sonst Low), ab Version 18 für Nicht-Foil und Foil getrennt:
- `teuer`: teuerster anderer Druck derselben Karte (Wunsch des Nutzers), `guenstig`: günstigster; Foil-Werte in den
  Spalten `*_foil` aus den Foil-Preisen (ein Druck ohne Foil-Preis zählt dort nicht).
- Basic Lands zählen nur gegen andere Secret Lairs: Sammlerdrucke wie Guru-Länder ergäben keinen sinnvollen Vergleich.
- Sets der Art `memorabilia` (zum Beispiel Weltmeisterschaftsdecks) zählen nicht, das sind keine spielbaren Drucke.

    einmal(datenbank, katalog, preisdatei)      Abgleich und Bewertung, gibt den Stand zurück
    bewerten_lauf(datenbank, katalog, preisdatei)   nur die Bewertung (nach neuen Preisen oder einem eingefügten Drop)
    Secretabgleich                              Thread im Add-on: täglich

Verkaufsbeginn (Wunsch des Nutzers, 09.11.2026): Tag des Drops plus eine Uhrzeit für alle Drops (Einstellung,
Standard 17:00 Ortszeit). Den Tag kann der Nutzer je Drop ändern (`datum_eigen`), der Abgleich lässt ihn dann.
Drops mit `wunsch` möchte der Nutzer kaufen; Home Assistant bekommt den Beginn des nächsten als Zeitstempel.

Ab Version 18 (Wunsch des Nutzers, 09.11.2026): alle Drops seit 2019; der tägliche Preisverlauf nur für Drops der
letzten 12 Monate und eigene (`abruf.sammlung_ids`). Schnäppchen: der ganze Drop bei Cardmarket mit ab-Preis höchstens
`schnaeppchen` Prozent (Standard 80) der Summe seiner Drucke einzeln, gleiche Ausführung, nur wenn alle Drucke ohne
Bonuskarten einen Preis haben. Eigene Secret Lairs (`secret_kauf`, Nutzerdaten mit Historie): je Kauf Ausführung,
Anzahl, Preis je Stück, Kaufdatum; Wert heute = ganzer Drop bei Cardmarket (Trend, sonst ab), sonst Summe der Drucke.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import urllib.request
import uuid
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from binderapp.db import schritte
from binderapp.db.datenbank import schreiben, verbinden, version
from binderapp.katalog.suche import normalisiere
from binderapp.preise import abruf
from binderapp.secretlair import cardmarket, wiki

MTGJSON = "https://mtgjson.com/api/v5/SLD.json"
MTGJSON_DECKLISTE = "https://mtgjson.com/api/v5/DeckList.json"
MTGJSON_DECK = "https://mtgjson.com/api/v5/decks/{datei}.json"
DROP_TYP = "Secret Lair Drop"   # alle anderen Decktypen (Commander Deck, Dandân Deck) sind fertige Decks
DATEI = re.compile(r"[A-Za-z0-9_\-]{1,120}")
BEGINN = "2019-01-01"  # Antwort des Nutzers (09.11.2026): alle Drops seit 2019 (vorher 12 Monate)
VERLAUF_TAGE = 365     # täglicher Preisverlauf nur für Drops der letzten 12 Monate (und eigene)
SCHNAEPPCHEN = 80      # Standard: ganzer Drop höchstens so viel Prozent der Drucke einzeln
QUELLE_ABGLEICH = "secretlair"   # Historie: Käufe, die der Abgleich an einen anderen Drop hängt
ALT = 20 * 3600        # ab diesem Alter wird neu abgeglichen
# Fassung des Abgleichs: erhöhen, wenn er neue Quellen oder Regeln bekommt. Ist die gespeicherte älter, gleicht der
# Thread nach dem Start sofort ab, statt bis zu 20 Stunden zu warten (Befund 09.11.2026: nach dem Update auf 0.35.0
# hatte kein Drop ein Cardmarket-Produkt, weil der Katalog erst beim nächsten Abgleich geladen worden wäre).
# 2: MTG Wiki, Bonuskarten (0.34.0); 3: Cardmarket-Produkte (0.35.0)
FASSUNG = 5
TAKT = 3600
ZEIT = 120             # Sekunden für den Download
NAME_MAX = 80
ZEILEN_MAX = 100
SET = "sld"
ZEILE = re.compile(r"^(?:[-•*]\s*)?(?:(\d{1,2})\s*[x×]\s+)?(.+)$", re.IGNORECASE)
UHRZEIT = "17:00"       # Standard für den Verkaufsbeginn (Wunsch des Nutzers)
UHR = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")
TOLERANZ_TAGE = 30      # eingefügter Drop: Drucke bis so viele Tage vor seinem Verkaufstag gehören noch dazu
ZUSATZ = re.compile(r"\s+(?:as|als)\s+[\"“„'].*$|\s*[(\[].*$", re.IGNORECASE)   # „Eerie Interlude as "Beam Us Up"“

log = logging.getLogger("binderapp.secretlair")


def _jetzt() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def wert_lesen(con, name: str) -> str | None:
    z = con.execute("SELECT wert FROM secret_wert WHERE name = ?", (name,)).fetchone()
    return z[0] if z else None


def wert_setzen(con, name: str, wert) -> None:
    con.execute("INSERT INTO secret_wert (name, wert) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET wert = excluded.wert",
                (name, None if wert is None else str(wert)))


# ---------------------------------------------------------------- Verkaufsbeginn

def _sonntag_letzter(jahr: int, monat: int) -> date:
    tag = date(jahr, monat, 31)
    return tag - timedelta(days=(tag.weekday() + 1) % 7)


def _mitteleuropa(naiv: datetime) -> datetime:
    """Rückfall ohne Zeitzonendaten: deutsche Zeit (MEZ, MESZ von Ende März bis Ende Oktober)."""
    sommer = _sonntag_letzter(naiv.year, 3) <= naiv.date() < _sonntag_letzter(naiv.year, 10)
    return naiv.replace(tzinfo=timezone(timedelta(hours=2 if sommer else 1)))


def zone():
    """Zeitzone des Add-ons (Home Assistant setzt `TZ`), sonst Europe/Berlin; None ohne Zeitzonendaten."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(os.environ.get("TZ") or "Europe/Berlin")
    except Exception:   # keine Zeitzonendaten im Abbild oder unbekannter Name
        return None


def uhrzeit(con) -> str:
    wert = wert_lesen(con, "uhrzeit")
    return wert if wert and UHR.fullmatch(wert) else UHRZEIT


def beginn(verkauf: str | None, uhr: str, tz=None) -> datetime | None:
    """Verkaufsbeginn als Zeitpunkt mit Zeitzone; None ohne Tag."""
    if not verkauf:
        return None
    h, m = UHR.fullmatch(uhr).groups()
    naiv = datetime.combine(date.fromisoformat(verkauf), time(int(h), int(m)))
    return naiv.replace(tzinfo=tz) if tz is not None else _mitteleuropa(naiv)


# ---------------------------------------------------------------- MTGJSON

def mtgjson_lesen(roh: bytes) -> list[dict]:
    """Drops aus `SLD.json`: [{"name", "karten": [(Scryfall-ID, Anzahl)]}]. Foil-Ausgaben sind derselbe Drop. Karten
    ohne Deck kommen über ihr erstes `subsets` dazu (MTGJSON nennt dort den Drop je Karte, oft vor dem Deck).

    Fertige Decks (Typ nicht „Secret Lair Drop“) bekommen dazu `foil` ({Scryfall-ID: Foil im Deck}) und `fremd`
    [(MTGJSON-UUID, Anzahl, Foil)] für Karten aus anderen Sets; `aufloesen` macht daraus `deck` [(ID, Anzahl, Foil)]."""
    daten = json.loads(roh)["data"]
    ids = {k["uuid"]: k.get("identifiers", {}).get("scryfallId") for k in daten.get("cards", [])}
    drops, nach_name = [], {}
    for d in daten.get("decks", []):
        name = d.get("name") or ""
        if not name or name in nach_name or name.endswith("Foil Edition"):
            continue
        eintraege = [e for e in (d.get("commander") or []) + (d.get("mainBoard") or []) if isinstance(e, dict)]
        karten = [(ids[e["uuid"]], int(e.get("count") or 1)) for e in eintraege if ids.get(e.get("uuid"))]
        if karten:
            nach_name[name] = {"name": name, "karten": karten}
            if (d.get("type") or DROP_TYP) != DROP_TYP:
                nach_name[name].update(
                    foil={ids[e["uuid"]]: bool(e.get("isFoil")) for e in eintraege if ids.get(e.get("uuid"))},
                    fremd=[(e["uuid"], int(e.get("count") or 1), bool(e.get("isFoil"))) for e in eintraege
                           if e.get("uuid") and e["uuid"] not in ids])
            drops.append(nach_name[name])
    im_deck = {sid for d in drops for sid, _ in d["karten"]}
    for k in daten.get("cards", []):
        sid, teile = ids.get(k.get("uuid")), k.get("subsets") or []
        if not sid or sid in im_deck or not teile or not isinstance(teile[0], str) or not teile[0].strip():
            continue
        name = teile[0].strip()
        if name not in nach_name:
            nach_name[name] = {"name": name, "karten": []}
            drops.append(nach_name[name])
        nach_name[name]["karten"].append((sid, 1))
        im_deck.add(sid)
    return drops


def _holen(adresse: str, oeffnen) -> bytes:
    anfrage = urllib.request.Request(adresse, headers={"User-Agent": "MTG-Binder (privat)"})
    with oeffnen(anfrage, timeout=ZEIT) as antwort:
        return antwort.read()


def mtgjson_laden(oeffnen=urllib.request.urlopen) -> list[dict]:
    return mtgjson_lesen(_holen(MTGJSON, oeffnen))


def deckdatei_lesen(roh: bytes) -> dict[str, str]:
    """{MTGJSON-UUID: Scryfall-ID} aller Karten einer Deckdatei (`decks/<Datei>.json`)."""
    daten = json.loads(roh)["data"]
    return {e["uuid"]: e["identifiers"]["scryfallId"] for teil in ("commander", "mainBoard")
            for e in daten.get(teil) or [] if e.get("uuid") and e.get("identifiers", {}).get("scryfallId")}


def aufloesen(decks: list[dict], bekannt: dict[str, str], oeffnen=urllib.request.urlopen) -> tuple[dict, list[str]]:
    """Setzt `deck` [(Scryfall-ID, Anzahl, Foil)] je fertigem Deck. Unbekannte UUIDs holt es aus der Deckdatei bei
    MTGJSON (je Deck einmal, danach gemerkt). Gibt (alle bekannten UUIDs, Fehler) zurück; ein Deck mit fehlenden
    Karten bleibt unvollständig, und der Fehler steht im Stand (nie stillschweigend)."""
    bekannt, fehler, liste = dict(bekannt), [], None
    for d in decks:
        if "fremd" not in d:
            continue
        if any(u not in bekannt for u, _, _ in d["fremd"]):
            try:
                if liste is None:
                    liste = json.loads(_holen(MTGJSON_DECKLISTE, oeffnen))["data"]
                datei = next((e["fileName"] for e in liste if e.get("code") == "SLD" and e.get("name") == d["name"]), None)
                if datei is None or not DATEI.fullmatch(datei):
                    raise ValueError("keine Deckdatei")
                bekannt.update(deckdatei_lesen(_holen(MTGJSON_DECK.format(datei=datei), oeffnen)))
            except (OSError, ValueError, KeyError, TypeError) as f:
                fehler.append(f"Deckliste „{d['name']}“ nicht vollständig: {f}")
        d["deck"] = [(bekannt[u], n, foil) for u, n, foil in d["fremd"] if u in bekannt]
        if len(d["deck"]) < len(d["fremd"]) and not any(d["name"] in f for f in fehler):
            fehler.append(f"Deckliste „{d['name']}“: {len(d['fremd']) - len(d['deck'])} Karten unbekannt")
    return bekannt, fehler


def _fest_merken(decks: list[dict]) -> str:
    return json.dumps([{"name": d["name"], "karten": d["karten"], "foil": d["foil"], "deck": d.get("deck", [])}
                       for d in decks if "foil" in d], ensure_ascii=False)


# ---------------------------------------------------------------- Abgleich

def _hand_verbinden(con, drucke: dict, nach_oracle: dict) -> set[str]:
    """Verbindet eingefügte Karten mit den Drucken des Katalogs und gibt alle so belegten Drucke zurück."""
    belegt: set[str] = set()
    zeilen = con.execute(f"SELECT k.id, k.oracle_id, k.scryfall_id, date(d.verkauf, '-{TOLERANZ_TAGE} days') AS ab"
                         " FROM secret_karte k"
                         " JOIN secret_drop d ON d.id = k.drop_id WHERE d.quelle = 'hand' ORDER BY k.id").fetchall()
    belegt.update(k["scryfall_id"] for k in zeilen if k["scryfall_id"])
    for k in zeilen:
        sid = k["scryfall_id"]
        if sid is None and k["oracle_id"]:
            frei = [d for d in nach_oracle.get(k["oracle_id"], ()) if d["scryfall_id"] not in belegt
                    and (k["ab"] is None or d["erschienen"] >= k["ab"])]
            if frei:
                d = max(frei, key=lambda x: (x["erschienen"], x["nummer_zahl"] or 0))
                sid = d["scryfall_id"]
                con.execute("UPDATE secret_karte SET scryfall_id = ?, nummer = ?, cardmarket_id = ? WHERE id = ?",
                            (sid, d["nummer"], d["cardmarket_id"], k["id"]))
                belegt.add(sid)
        elif sid in drucke:   # Cardmarket trägt das Produkt oft später nach
            con.execute("UPDATE secret_karte SET cardmarket_id = ? WHERE id = ?", (drucke[sid]["cardmarket_id"], k["id"]))
        if sid in drucke:     # „2x Plains“: Die Drucke desselben Tages gehören mit zum eingefügten Drop
            belegt.update(d["scryfall_id"] for d in nach_oracle.get(k["oracle_id"], ())
                          if d["erschienen"] == drucke[sid]["erschienen"])
    return belegt


def _laeufe(rest: list[dict]) -> list[dict]:
    """Drucke ohne Drop nach Tag und fortlaufender Nummer. Nur eine Näherung, siehe oben."""
    gruppen: list[list[dict]] = []
    for d in sorted(rest, key=lambda x: (x["erschienen"], x["nummer_zahl"] or 0, x["nummer"])):
        letzte = gruppen[-1][-1] if gruppen else None
        if (letzte is not None and letzte["erschienen"] == d["erschienen"]
                and (d["nummer_zahl"] or 0) - (letzte["nummer_zahl"] or 0) <= 1):
            gruppen[-1].append(d)
        else:
            gruppen.append([d])
    aus = []
    for g in gruppen:
        tag = date.fromisoformat(g[0]["erschienen"]).strftime("%d.%m.%Y")
        von, bis = g[0]["nummer"], g[-1]["nummer"]
        aus.append({"schluessel": f"scryfall:{g[0]['erschienen']}:{von}", "quelle": "scryfall",
                    "name": f"Secret Lair vom {tag} (Nr. {von}{'–' + bis if bis != von else ''})",
                    "karten": [(d["scryfall_id"], 1, 0) for d in g]})
    return aus


def nummer(druck: dict) -> int | None:
    """Sammlernummer als Zahl, nur wenn sie ganz aus Ziffern besteht („IFIYW-6“ ist nicht Nummer 6)."""
    n = str(druck.get("nummer") or "")
    return int(n) if n.isdigit() else None


def ist_bonus(n: int | None) -> bool:
    """Bonuskarten und Promos haben eigene Nummernbereiche (MTG Wiki: 501–899, 900–924, ab 7001)."""
    return n is not None and (501 <= n <= 999 or n >= 7000)


def _start(g: dict, drucke: dict) -> str:
    tage = [drucke[sid]["erschienen"] for sid, _, bonus in g["karten"] if not bonus] or \
        [drucke[sid]["erschienen"] for sid, _, _ in g["karten"]]
    return g.get("tag") or min(tage)


def _mit_kaeufen(con) -> set[int]:
    if version(con) < 18:
        return set()
    return {z[0] for z in con.execute("SELECT DISTINCT drop_id FROM secret_kauf")}


def kaeufe_umhaengen(con, von: int, nach: int) -> int:
    """Hängt alle Käufe eines Drops an einen anderen (in der Transaktion des Aufrufers), mit Historie."""
    schritt = schritte.neuer_schritt()
    zeilen = con.execute("SELECT id, nutzer_id FROM secret_kauf WHERE drop_id = ?", (von,)).fetchall()
    for z in zeilen:
        schritte.aendern_genau(con, z["nutzer_id"], "secret_kauf", z["id"], {"drop_id": nach}, schritt=schritt,
                               quelle=QUELLE_ABGLEICH)
    return len(zeilen)


def _decks_anhaengen(gruppen: list[dict], fest: list[dict], drucke: dict, kat) -> None:
    """Hängt an jede Gruppe, die die meisten Secret-Lair-Karten eines fertigen Decks enthält, die übrigen Karten der
    Deckliste und die Ausführung je Karte. Die Gruppe kann aus dem Wiki kommen und anders heißen als das Deck."""
    for d in fest:
        sl = {sid for sid, _ in d["karten"]}
        n, g = max(((len(sl & {sid for sid, *_ in g["karten"]}), i) for i, g in enumerate(gruppen)), default=(0, None))
        if not n:
            continue
        g = gruppen[g]
        g.setdefault("foil", {}).update(d["foil"])
        g.setdefault("anzahl", {}).update({sid: n for sid, n in d["karten"]})   # das Wiki kennt keine Anzahl
        # Secret-Lair-Karten des Decks, die das Wiki einem anderen Drop zuordnet, gehören hier zur Deckliste
        in_gruppe = {sid for sid, *_ in g["karten"]}
        weitere = [(sid, n, d["foil"].get(sid, False)) for sid, n in d["karten"] if sid not in in_gruppe and sid in drucke]
        for sid, anzahl, foil in weitere + d.get("deck", []):
            if sid not in drucke:
                z = kat.execute("SELECT scryfall_id, oracle_id, name, nummer, nummer_zahl, erschienen, cardmarket_id"
                                " FROM druck WHERE scryfall_id = ?", (sid,)).fetchone()
                if z is None:
                    continue
                drucke[sid] = dict(z)
            g.setdefault("deck", []).append((sid, anzahl, int(foil)))


def _gruppen_schreiben(con, gruppen: list[dict], drucke: dict, behalten: set[int]) -> None:
    """Gleicht die automatischen Drops ab. Ein Drop behält Name vom Nutzer und Kosten, auch wenn er seinen Schlüssel
    wechselt, zum Beispiel wenn MTGJSON den Namen nachliefert.

    Käufe gehen nie verloren (Antwort des Nutzers, 09.11.2026): Fällt ein Drop mit Käufen weg, wandern sie zum neuen
    Drop mit den meisten gleichen Drucken; teilt keiner einen Druck mit ihm, bleibt der alte Drop stehen."""
    alt = {}
    for z in con.execute("SELECT id, schluessel, umbenannt FROM secret_drop WHERE quelle <> 'hand'"):
        if z["id"] not in behalten:
            alt[z["id"]] = {**dict(z), "karten": {k[0] for k in con.execute(
                "SELECT scryfall_id FROM secret_karte WHERE drop_id = ?", (z["id"],))}}
    zuordnung: dict[int, int] = {}   # Gruppe (Platz) -> vorhandener Drop
    frei = dict(alt)
    nach_schluessel = {d["schluessel"]: i for i, d in alt.items()}
    for platz, g in enumerate(gruppen):
        if g["schluessel"] in nach_schluessel:
            zuordnung[platz] = nach_schluessel[g["schluessel"]]
            frei.pop(zuordnung[platz], None)
    for platz, g in enumerate(gruppen):
        if platz in zuordnung:
            continue
        ids = {sid for sid, *_ in g["karten"]}
        gemeinsam = [(len(ids & d["karten"]), i) for i, d in frei.items()]
        n, i = max(gemeinsam, default=(0, None))
        if n and n * 2 >= min(len(ids), len(frei[i]["karten"])):
            zuordnung[platz] = i
            del frei[i]
    gekauft = _mit_kaeufen(con)
    for i in frei:
        if i not in gekauft:
            con.execute("DELETE FROM secret_drop WHERE id = ?", (i,))
    neu_karten: dict[int, set[str]] = {}
    for platz, g in enumerate(gruppen):
        verkauf = _start(g, drucke)
        drop = zuordnung.get(platz)
        if drop is None:
            drop = con.execute("INSERT INTO secret_drop (schluessel, name, quelle, verkauf) VALUES (?, ?, ?, ?)",
                               (g["schluessel"], g["name"], g["quelle"], verkauf)).lastrowid
        else:
            con.execute("UPDATE secret_drop SET schluessel = ?, quelle = ?,"
                        " verkauf = CASE WHEN datum_eigen THEN verkauf ELSE ? END,"
                        " name = CASE WHEN umbenannt THEN name ELSE ? END WHERE id = ?",
                        (g["schluessel"], g["quelle"], verkauf, g["name"], drop))
            con.execute("DELETE FROM secret_karte WHERE drop_id = ?", (drop,))
        neu_karten[drop] = {sid for sid, *_ in g["karten"]}
        foil = g.get("foil", {})
        # Fertiges Deck: Karten der Gruppe, die nicht in der Deckliste stehen (Tokens, Zweitfassungen), zählen wie
        # Bonuskarten nicht zum Wert
        n_deck = g.get("anzahl", {})
        zeilen = [(sid, n_deck.get(sid, anzahl), int(bonus or (bool(foil) and sid not in foil)), 0,
                   None if sid not in foil else int(foil[sid])) for sid, anzahl, bonus in g["karten"]]
        zeilen += [(sid, anzahl, 0, 1, f) for sid, anzahl, f in g.get("deck", [])]
        for sid, anzahl, bonus, deck, f in zeilen:
            d = drucke[sid]
            con.execute("INSERT INTO secret_karte (drop_id, name, oracle_id, scryfall_id, nummer, cardmarket_id, anzahl,"
                        " bonus, deck, foil) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (drop, d["name"], d["oracle_id"], sid, d["nummer"], d["cardmarket_id"], anzahl, bonus, deck, f))
    for i in frei:
        if i not in gekauft:
            continue
        n, ziel = max(((len(frei[i]["karten"] & ids), d) for d, ids in neu_karten.items()), default=(0, None))
        if n:
            kaeufe_umhaengen(con, i, ziel)
            con.execute("DELETE FROM secret_drop WHERE id = ?", (i,))
        else:
            log.warning("Secret Lair: Drop %s hat Käufe, aber keinen Nachfolger; er bleibt stehen.", i)


def abgleichen(con, kat, decks: list[dict] | None, heute: date, wiki_daten: dict | None = None) -> None:
    """Trägt alle Drops seit 2019 und die kommenden ein (in der Transaktion des Aufrufers). `decks` ist
    None, wenn MTGJSON gerade nicht erreichbar war: Dann bleiben die Drops aus MTGJSON, wie sie sind. Ohne
    `wiki_daten` gilt der zuletzt gemerkte Stand des Wikis.

    Reihenfolge je Karte: eingefügter Drop, MTG Wiki, MTGJSON, sonst Gruppe nach Tag und Nummer (Scryfall).
    Bonuskarten hängen am Drop, den Wiki oder MTGJSON nennen, sonst fallen sie weg."""
    grenze = BEGINN
    drucke = {z["scryfall_id"]: dict(z) for z in kat.execute(
        "SELECT scryfall_id, oracle_id, name, nummer, nummer_zahl, erschienen, cardmarket_id FROM druck"
        " WHERE set_code = ? AND erschienen >= ?", (SET, grenze))}
    nach_oracle: dict[str, list[dict]] = {}
    for d in drucke.values():
        nach_oracle.setdefault(d["oracle_id"], []).append(d)
    belegt = _hand_verbinden(con, drucke, nach_oracle)
    behalten: set[int] = set()
    if decks is None:
        for z in con.execute("SELECT k.drop_id, k.scryfall_id FROM secret_karte k JOIN secret_drop d ON d.id = k.drop_id"
                             " WHERE d.quelle = 'mtgjson'"):
            behalten.add(z["drop_id"])
            belegt.add(z["scryfall_id"])
    if wiki_daten is None:
        wiki_daten = wiki.aus_text(wert_lesen(con, "wiki"))
    aus_mtgjson: dict[str, tuple[str, int]] = {}
    for deck in decks or []:
        for sid, n in deck["karten"]:
            aus_mtgjson.setdefault(sid, (deck["name"][:NAME_MAX], n))

    gruppen: dict[str, dict] = {}   # verglichener Name → Gruppe; gleiche Namen aus Wiki und MTGJSON sind ein Drop

    def gruppe(quelle: str, name: str, tag: str | None = None) -> dict:
        g = gruppen.get(wiki.norm(name))
        if g is None:
            g = gruppen[wiki.norm(name)] = {"schluessel": f"{quelle}:{name}", "quelle": quelle, "name": name[:NAME_MAX],
                                            "karten": [], "tag": tag}
        elif quelle == "wiki" and g["quelle"] != "wiki":   # das Wiki gewinnt, auch wenn MTGJSON die Gruppe anlegte
            g.update(schluessel=f"wiki:{name}", quelle="wiki", name=name[:NAME_MAX], tag=tag or g["tag"])
        elif tag and not g["tag"]:
            g["tag"] = tag
        return g

    bonus, rest = [], []
    for sid, d in sorted(drucke.items(), key=lambda x: (x[1]["nummer_zahl"] or 0, x[1]["nummer"])):
        if sid in belegt:
            continue
        n = nummer(d)
        if ist_bonus(n):
            bonus.append(d)
        elif n in wiki_daten["nummern"]:
            name, tag = wiki_daten["nummern"][n]
            gruppe("wiki", name, tag)["karten"].append((sid, 1, 0))
        elif sid in aus_mtgjson:
            name, anzahl = aus_mtgjson[sid]
            gruppe("mtgjson", name)["karten"].append((sid, anzahl, 0))
        else:
            rest.append(d)
    for d in bonus:   # Antwort des Nutzers: zum Drop, sonst nicht zeigen
        name = wiki_daten["bonus"].get(nummer(d)) or aus_mtgjson.get(d["scryfall_id"], (None,))[0]
        g = gruppen.get(wiki.norm(name)) if name else None
        if g is not None and g["karten"]:
            g["karten"].append((d["scryfall_id"], 1, 1))
    liste_ = [g for g in gruppen.values() if any(not b for _, _, b in g["karten"])] + _laeufe(rest)
    liste_.sort(key=lambda g: (_start(g, drucke), g["schluessel"]))
    # Fertige Decks: aus MTGJSON, sonst (MTGJSON gerade nicht erreichbar) der gemerkte Stand
    fest = [d for d in decks if "foil" in d] if decks is not None else json.loads(wert_lesen(con, "decks_fest") or "[]")
    _decks_anhaengen(liste_, fest, drucke, kat)
    _gruppen_schreiben(con, liste_, drucke, behalten)
    # Cardmarket-Produkte der Drops aus dem gemerkten Katalog (`einmal` merkt ihn vorher frisch)
    cardmarket.zuordnen(con, cardmarket.aus_text(wert_lesen(con, "cm_produkte")))
    gekauft = _mit_kaeufen(con)   # Drops mit Käufen bleiben immer
    for z in con.execute("SELECT id FROM secret_drop WHERE quelle <> 'hand' AND verkauf < ?", (grenze,)).fetchall():
        if z[0] not in gekauft:
            con.execute("DELETE FROM secret_drop WHERE id = ?", (z[0],))


# ---------------------------------------------------------------- Bewertung

def _preis(z: tuple, foil: bool = False) -> float | None:
    """Wert einer Zeile aus `abruf.einlesen`: (Produkt, low, avg7, low_foil, avg7_foil); Ø 7 Tage, sonst ab."""
    wert = (z[4] or z[3]) if foil else (z[2] or z[1])
    return wert if wert else None


def _vergleich(drucke, preise: dict, karte, nur_sld: bool):
    """(teuerster, günstigster) anderer Druck als (Preis, Druck) oder None."""
    werte = [(preise[d["cardmarket_id"]], d) for d in drucke
             if d["scryfall_id"] != karte["scryfall_id"] and preise.get(d["cardmarket_id"])
             and (not nur_sld or d["set_code"] == SET)]
    return max(werte, key=lambda w: w[0], default=None), min(werte, key=lambda w: w[0], default=None)


def _felder(teuer, guenstig) -> tuple:
    return (teuer and teuer[0], teuer and teuer[1]["set_code"], teuer and teuer[1]["nummer"],
            teuer and teuer[1]["scryfall_id"], guenstig and guenstig[0], guenstig and guenstig[1]["set_code"],
            guenstig and guenstig[1]["nummer"], guenstig and guenstig[1]["scryfall_id"])


def bewerten(con, kat, roh: bytes) -> int:
    """Schreibt die Vergleichswerte aller Drop-Karten (in der Transaktion des Aufrufers)."""
    karten = con.execute("SELECT id, oracle_id, scryfall_id, cardmarket_id FROM secret_karte").fetchall()
    andere: dict[str, list] = {}
    for oracle in {k["oracle_id"] for k in karten if k["oracle_id"]}:
        andere[oracle] = kat.execute(
            "SELECT scryfall_id, cardmarket_id, set_code, nummer, typzeile FROM druck WHERE oracle_id = ?"
            " AND cardmarket_id IS NOT NULL AND COALESCE(set_art, '') <> 'memorabilia'", (oracle,)).fetchall()
    ids = {d["cardmarket_id"] for liste in andere.values() for d in liste}
    ids |= {k["cardmarket_id"] for k in karten if k["cardmarket_id"]}
    zeilen = abruf.einlesen(roh, ids)[2]
    preise = {z[0]: _preis(z) for z in zeilen}
    preise_foil = {z[0]: _preis(z, foil=True) for z in zeilen}
    foil = version(con) >= 18
    for k in karten:
        drucke = andere.get(k["oracle_id"], [])
        nur_sld = any("Basic" in (d["typzeile"] or "") for d in drucke)   # gilt auch für Foil
        con.execute("UPDATE secret_karte SET preis = ?, teuer = ?, teuer_set = ?, teuer_nummer = ?, teuer_id = ?,"
                    " guenstig = ?, guenstig_set = ?, guenstig_nummer = ?, guenstig_id = ?, nur_sld = ? WHERE id = ?",
                    (preise.get(k["cardmarket_id"]), *_felder(*_vergleich(drucke, preise, k, nur_sld)), int(nur_sld),
                     k["id"]))
        if foil:
            con.execute("UPDATE secret_karte SET preis_foil = ?, teuer_foil = ?, teuer_foil_set = ?,"
                        " teuer_foil_nummer = ?, teuer_foil_id = ?, guenstig_foil = ?, guenstig_foil_set = ?,"
                        " guenstig_foil_nummer = ?, guenstig_foil_id = ? WHERE id = ?",
                        (preise_foil.get(k["cardmarket_id"]), *_felder(*_vergleich(drucke, preise_foil, k, nur_sld)),
                         k["id"]))
    cardmarket.preise_schreiben(con, roh)
    wert_setzen(con, "bewertet", _jetzt())
    return len(karten)


def bewerten_lauf(datenbank: Path, katalog: Path, preisdatei: Path) -> int:
    """Nur bewerten. Ohne Preisdatei (Add-on frisch installiert) bleibt alles ohne Werte, bis sie da ist."""
    if not Path(preisdatei).is_file():
        return 0
    roh = Path(preisdatei).read_bytes()
    with closing(verbinden(katalog, nur_lesen=True)) as kat, closing(verbinden(datenbank)) as con, schreiben(con):
        return bewerten(con, kat, roh)


def einmal(datenbank: Path, katalog: Path, preisdatei: Path, oeffnen=urllib.request.urlopen, heute: date | None = None,
           wiki_laden=None, cardmarket_laden=None) -> dict:
    """Abgleich und Bewertung. Ist MTGJSON oder MTG Wiki nicht erreichbar, läuft der Rest trotzdem (beim Wiki mit dem
    gemerkten Stand), der Fehler steht im Stand. `wiki_laden` (Tests): liefert die Daten des Wikis."""
    fehler = []
    try:
        decks = mtgjson_laden(oeffnen)
    except (OSError, ValueError, KeyError) as f:
        decks = None
        fehler.append(f"MTGJSON nicht erreichbar: {f}")
    try:
        wiki_daten = (wiki_laden or (lambda: wiki.laden(oeffnen)))()
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as f:
        wiki_daten = None
        fehler.append(f"MTG Wiki nicht erreichbar: {f}")
    try:
        produkte = (cardmarket_laden or (lambda: cardmarket.laden(oeffnen)))()
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as f:
        produkte = None
        fehler.append(f"Cardmarket-Katalog nicht erreichbar: {f}")
    bekannt = None
    if decks is not None and any("fremd" in d for d in decks):
        with closing(verbinden(datenbank, nur_lesen=True)) as con:
            gemerkt = json.loads(wert_lesen(con, "deck_ids") or "{}")
        bekannt, deckfehler = aufloesen(decks, gemerkt, oeffnen)
        fehler += deckfehler
    for f in fehler:
        log.warning("%s", f)
    with closing(verbinden(katalog, nur_lesen=True)) as kat, closing(verbinden(datenbank)) as con, schreiben(con):
        if bekannt is not None:
            wert_setzen(con, "deck_ids", json.dumps(bekannt))
        if decks is not None:
            wert_setzen(con, "decks_fest", _fest_merken(decks))
        if wiki_daten is not None:
            wert_setzen(con, "wiki", wiki.als_text(wiki_daten))
            wert_setzen(con, "wiki_stand", _jetzt())
        if produkte is not None:
            wert_setzen(con, "cm_produkte", cardmarket.als_text(produkte))
        abgleichen(con, kat, decks, heute or date.today(), wiki_daten)
        wert_setzen(con, "abgleich", _jetzt())
        wert_setzen(con, "fassung", FASSUNG)
        wert_setzen(con, "fehler", " · ".join(fehler) or None)
    karten = bewerten_lauf(datenbank, katalog, preisdatei)
    return {"karten": karten, "fehler": " · ".join(fehler) or None}


# ---------------------------------------------------------------- Einfügen

def text_lesen(text: str) -> list[tuple[int, str]]:
    """Kartenliste aus dem Text einer Ankündigung: je Zeile eine Karte, wahlweise mit „2x“ und einem Zusatz wie
    „as "Beam Us Up"“. Gibt (Anzahl, Name) zurück."""
    zeilen = []
    for roh in text.splitlines():
        m = ZEILE.match(roh.strip())
        if not roh.strip() or m is None:
            continue
        name = ZUSATZ.sub("", m.group(2)).strip()
        if name:
            zeilen.append((int(m.group(1) or 1), name))
    return zeilen


def karten_finden(kat, zeilen: list[tuple[int, str]]) -> list[dict]:
    """Sucht jede Zeile im Katalog. `oracle_id` bleibt None, wenn der Name unbekannt ist."""
    aus = []
    for anzahl, name in zeilen:
        z = kat.execute("SELECT n.oracle_id, o.name FROM namen n JOIN orakel o ON o.oracle_id = n.oracle_id"
                        " WHERE n.norm = ? ORDER BY (n.sprache = 'en') DESC LIMIT 1", (normalisiere(name),)).fetchone()
        aus.append({"anzahl": anzahl, "name": z["name"] if z else name, "oracle_id": z["oracle_id"] if z else None})
    return aus


def einfuegen(con, name: str, verkauf: str | None, karten: list[dict]) -> int:
    """Legt einen Drop von Hand an (in der Transaktion des Aufrufers)."""
    drop = con.execute("INSERT INTO secret_drop (schluessel, name, quelle, verkauf, umbenannt) VALUES (?, ?, 'hand', ?, 1)",
                       (f"hand:{uuid.uuid4().hex}", name, verkauf)).lastrowid
    for k in karten:
        con.execute("INSERT INTO secret_karte (drop_id, name, oracle_id, anzahl) VALUES (?, ?, ?, ?)",
                    (drop, k["name"], k["oracle_id"], k["anzahl"]))
    return drop


# ---------------------------------------------------------------- Ändern

def tag_aus_katalog(con, kat, drop_id: int) -> str | None:
    """Verkaufstag eines Drops nach dem Katalog: erster Erscheinungstag seiner Drucke."""
    ids = [z[0] for z in con.execute("SELECT scryfall_id FROM secret_karte WHERE drop_id = ? AND scryfall_id IS NOT NULL",
                                     (drop_id,))]
    tage = [z[0] for i in range(0, len(ids), 500) for z in kat.execute(
        f"SELECT erschienen FROM druck WHERE scryfall_id IN ({','.join('?' * len(ids[i:i + 500]))})", ids[i:i + 500])]
    return min(tage, default=None)


def tag_setzen(con, drop_id: int, verkauf: str | None, kat=None) -> str | None:
    """Tag vom Nutzer (in der Transaktion des Aufrufers). `None` gibt den Tag wieder dem Abgleich: bei
    automatischen Drops der Tag aus dem Katalog (ohne Katalog bleibt der alte bis zum nächsten Abgleich), bei
    eingefügten heißt es „unbekannt“."""
    if verkauf is not None:
        con.execute("UPDATE secret_drop SET verkauf = ?, datum_eigen = 1 WHERE id = ?", (verkauf, drop_id))
        return verkauf
    z = con.execute("SELECT quelle, verkauf FROM secret_drop WHERE id = ?", (drop_id,)).fetchone()
    if z["quelle"] == "hand":
        neu = None
    else:
        neu = (tag_aus_katalog(con, kat, drop_id) if kat is not None else None) or z["verkauf"]
    con.execute("UPDATE secret_drop SET verkauf = ?, datum_eigen = 0 WHERE id = ?", (neu, drop_id))
    return neu


# ---------------------------------------------------------------- Lesen

SQL_DROPS = """
SELECT d.id, d.name, d.quelle, d.verkauf, d.datum_eigen, d.wunsch, d.kosten, {kosten_foil} AS kosten_foil,
       COALESCE(SUM(k.id IS NOT NULL AND k.bonus = 0), 0) AS karten, SUM(CASE WHEN k.bonus = 0 THEN k.anzahl END) AS exemplare,
       SUM(CASE WHEN k.bonus = 0 AND {deck} = 0 THEN k.anzahl * k.teuer END) AS teuer,
       SUM(CASE WHEN k.bonus = 0 AND {deck} = 0 THEN k.anzahl * k.guenstig END) AS guenstig,
       SUM(CASE WHEN k.bonus = 0 THEN k.anzahl * {preis_nf} END) AS wert,
       COALESCE(SUM(k.bonus = 0 AND {preis_nf} IS NOT NULL), 0) AS mit_preis,
       COALESCE(SUM(k.bonus = 0 AND {deck} = 0 AND k.teuer IS NULL), 0) AS ohne_vergleich, COALESCE(SUM(k.bonus), 0) AS bonus,
       COALESCE(SUM(k.bonus = 0 AND {deck} = 1), 0) AS deckkarten, COALESCE(MAX({kfoil} IS NOT NULL), 0) AS fest,
       {foil}
FROM secret_drop d LEFT JOIN secret_karte k ON k.drop_id = d.id {wo} GROUP BY d.id
ORDER BY d.verkauf IS NULL DESC, d.verkauf DESC, d.id DESC
"""
# Dieselben Summen für Foil (ab Version 18)
SQL_FOIL = """SUM(CASE WHEN k.bonus = 0 AND {deck} = 0 THEN k.anzahl * k.teuer_foil END) AS teuer_foil,
       SUM(CASE WHEN k.bonus = 0 AND {deck} = 0 THEN k.anzahl * k.guenstig_foil END) AS guenstig_foil,
       SUM(CASE WHEN k.bonus = 0 THEN k.anzahl * {preis_f} END) AS wert_foil,
       COALESCE(SUM(k.bonus = 0 AND {preis_f} IS NOT NULL), 0) AS mit_preis_foil,
       COALESCE(SUM(k.bonus = 0 AND {deck} = 0 AND k.teuer_foil IS NULL), 0) AS ohne_vergleich_foil"""
# Preis einer Karte in der gewählten Ausführung; Karten fertiger Decks (ab Version 19) immer in ihrer Ausführung im Deck
PREIS_IN = "(CASE WHEN COALESCE({kfoil}, {modus}) = 1 THEN k.preis_foil ELSE k.preis END)"
SQL_OHNE_FOIL = ("NULL AS teuer_foil, NULL AS guenstig_foil, NULL AS wert_foil, 0 AS mit_preis_foil,"
                 " COALESCE(SUM(k.bonus = 0), 0) AS ohne_vergleich_foil")
NUR_EIGENE = "WHERE d.id IN (SELECT drop_id FROM secret_kauf)"


def _sql(con, wo: str = "") -> str:
    neu, decks = version(con) >= 18, version(con) >= 19
    teile = {"deck": "k.deck" if decks else "0", "kfoil": "k.foil" if decks else "NULL"}
    teile.update(preis_nf=PREIS_IN.format(modus=0, **teile) if neu else "k.preis",
                 preis_f=PREIS_IN.format(modus=1, **teile))
    return SQL_DROPS.format(wo=wo, foil=(SQL_FOIL if neu else SQL_OHNE_FOIL).format(**teile),
                            kosten_foil="d.kosten_foil" if neu else "NULL", **teile)


def _rund(wert) -> float | None:
    return None if wert is None else round(wert, 2)


def _jetzt_zeit(jetzt: datetime | None) -> datetime:
    return jetzt if jetzt is not None else datetime.now(timezone.utc)


def schnaeppchen(produkte: list[dict], summe: float | None, vollstaendig: bool, foil: bool,
                 schwelle: float) -> dict | None:
    """Der ganze Drop bei Cardmarket mit ab-Preis höchstens `schwelle` Prozent der Summe seiner Drucke einzeln
    (gleiche Ausführung). Nur wenn alle Drucke ohne Bonuskarten einen Preis haben, sonst nie (Antwort des Nutzers).
    Von beiden Fassungen (mit und ohne „Set“) zählt die mit dem größten Abschlag."""
    if not vollstaendig or not summe:
        return None
    beste = None
    for p in produkte:
        ab = p["low_foil"] if foil else p["low"]
        if ab and ab <= summe * schwelle / 100:
            abschlag = round((1 - ab / summe) * 100)
            if beste is None or ab < beste["ab"]:
                beste = {"abschlag": abschlag, "ab": ab, "summe": _rund(summe), "cardmarket_id": p["cardmarket_id"],
                         "name": p["name"]}
    return beste


def _ausfuehrung(z, foil: bool, standard: float | None, produkte: list[dict], schwelle: float) -> dict:
    """Zahlen einer Ausführung (Nicht-Foil oder Foil) eines Drops. Ein fertiges Deck gibt es nur in einer Ausführung:
    Seine Summe ist in beiden gleich, das Schnäppchen vergleicht den Preis des Produkts (ab, sonst ab Foil)."""
    s = "_foil" if foil else ""
    eigen = z["kosten_foil"] if foil else z["kosten"]
    kosten = eigen if eigen is not None else standard
    teuer = _rund(z["teuer" + s])
    return {"teuer": teuer, "guenstig": _rund(z["guenstig" + s]), "wert": _rund(z["wert" + s]),
            "mit_preis": z["mit_preis" + s], "ohne_vergleich": z["ohne_vergleich" + s], "kosten_eigen": eigen,
            "kosten": kosten, "faktor": round(teuer / kosten, 2) if kosten and teuer is not None else None,
            "schnaeppchen": schnaeppchen(_fest_produkte(produkte) if z["fest"] else produkte, z["wert" + s],
                                         z["karten"] > 0 and z["mit_preis" + s] == z["karten"],
                                         foil and not z["fest"], schwelle)}


def _fest_produkte(produkte: list[dict]) -> list[dict]:
    """Ein fertiges Deck hat bei Cardmarket einen Preis, egal ob „low“ oder „low-foil“ belegt ist."""
    return [{**p, "low": p["low"] or p["low_foil"], "trend": p["trend"] or p["trend_foil"]} for p in produkte]


def stueckwert(a: dict, produkte: list[dict], karten: int, foil: bool) -> tuple[float | None, str | None]:
    """Heutiger Wert eines gekauften Drops in der gekauften Ausführung und die Grundlage dafür (Antwort des
    Nutzers): der ganze Drop bei Cardmarket (Trend, sonst ab; zuerst das Produkt ohne „Set“), sonst die Summe der
    Drucke einzeln (Ø 7 Tage, sonst ab)."""
    for p in sorted(produkte, key=lambda p: p["set_fassung"]):
        trend, ab = (p["trend_foil"], p["low_foil"]) if foil else (p["trend"], p["low"])
        if trend or ab:
            return (trend or ab), f"ganzer Drop bei Cardmarket, {'Trend' if trend else 'ab'}"
    if a["wert"] is not None:
        teil = "" if a["mit_preis"] == karten else f", nur {a['mit_preis']} von {karten} Karten mit Preis"
        return a["wert"], f"Drucke einzeln, Ø 7 Tage, sonst ab{teil}"
    return None, None


def _summe(kaeufe: list[dict]) -> dict:
    """Kaufpreis, Wert, Gewinn/Verlust über Käufe. Gewinn und Prozent nur über Käufe mit heutigem Wert."""
    mit = [k for k in kaeufe if k["wert"] is not None]
    kauf_mit = sum(k["kaufpreis"] for k in mit)
    wert = sum(k["wert"] for k in mit) if mit else None
    gewinn = None if wert is None else _rund(wert - kauf_mit)
    return {"exemplare": sum(k["anzahl"] for k in kaeufe), "kaufpreis": _rund(sum(k["kaufpreis"] for k in kaeufe)),
            "wert": _rund(wert), "gewinn": gewinn,
            "prozent": round(gewinn / kauf_mit * 100, 1) if gewinn is not None and kauf_mit else None,
            "ohne_wert": len(kaeufe) - len(mit)}


def _kaeufe_lesen(con, drop_id: int | None = None) -> dict[int, list[dict]]:
    if version(con) < 18:
        return {}
    wo, werte = ("WHERE drop_id = ?", (drop_id,)) if drop_id is not None else ("", ())
    aus: dict[int, list[dict]] = {}
    for z in con.execute(f"SELECT id, drop_id, foil, anzahl, preis, datum FROM secret_kauf {wo}"
                         " ORDER BY datum IS NULL, datum, id", werte):
        aus.setdefault(z["drop_id"], []).append({k: z[k] for k in z.keys() if k != "drop_id"})
    return aus


def _besitz(d: dict, kaeufe: list[dict]) -> dict | None:
    if not kaeufe:
        return None
    liste_ = []
    for k in kaeufe:
        foil = bool(k["foil"])
        if d["fest"]:   # fertiges Deck: eine Ausführung, Produktpreis aus „ab“ oder „ab Foil“
            stueck, grundlage = stueckwert(d, _fest_produkte(d["produkte"]), d["karten"], False)
        else:
            stueck, grundlage = stueckwert(d["foil"] if foil else d, d["produkte"], d["karten"], foil)
        wert = None if stueck is None else stueck * k["anzahl"]
        kaufpreis = k["preis"] * k["anzahl"]
        liste_.append({**k, "foil": foil, "stueckwert": _rund(stueck), "grundlage": grundlage,
                       "kaufpreis": _rund(kaufpreis), "wert": _rund(wert),
                       "gewinn": None if wert is None else _rund(wert - kaufpreis),
                       "prozent": round((wert - kaufpreis) / kaufpreis * 100, 1) if wert is not None and kaufpreis else None})
    return {**_summe(liste_), "kaeufe": liste_}


def _drop(z, standard: float | None, standard_foil: float | None, uhr: str, jetzt: datetime, tz,
          produkte: list[dict], schwelle: float, kaeufe: list[dict]) -> dict:
    d = {k: z[k] for k in ("id", "name", "quelle", "verkauf", "karten", "exemplare", "bonus", "deckkarten")}
    d["fest"] = bool(z["fest"])
    d.update(_ausfuehrung(z, False, standard, produkte, schwelle))
    d["foil"] = _ausfuehrung(z, True, standard_foil, produkte, schwelle)
    start = beginn(d["verkauf"], uhr, tz)
    d.update(kommend=start is None or start > jetzt, beginn=start.isoformat() if start else None,
             wunsch=bool(z["wunsch"]), datum_eigen=bool(z["datum_eigen"]), produkte=produkte)
    d["besitz"] = _besitz(d, kaeufe)
    return d


def standard_kosten(con, foil: bool = False) -> float | None:
    wert = wert_lesen(con, "kosten_foil" if foil else "kosten")
    return float(wert) if wert else None


def schwelle(con) -> float:
    """Schwelle für „Schnäppchen“ in Prozent (Einstellung, Standard 80)."""
    wert = wert_lesen(con, "schnaeppchen")
    return float(wert) if wert else float(SCHNAEPPCHEN)


def _produkte(con) -> dict[int, list[dict]]:
    return cardmarket.lesen_je_drop(con) if version(con) >= 17 else {}


def liste(con, jetzt: datetime | None = None, nur_eigene: bool = False) -> list[dict]:
    standard, standard_foil, uhr, tz = standard_kosten(con), standard_kosten(con, True), uhrzeit(con), zone()
    produkte, kaeufe, grenze = _produkte(con), _kaeufe_lesen(con), schwelle(con)
    if nur_eigene and version(con) < 18:
        return []
    return [_drop(z, standard, standard_foil, uhr, _jetzt_zeit(jetzt), tz, produkte.get(z["id"], []), grenze,
                  kaeufe.get(z["id"], []))
            for z in con.execute(_sql(con, NUR_EIGENE if nur_eigene else ""))]


def besitz_gesamt(drops_: list[dict]) -> dict | None:
    """Summe aller eigenen Drops (oben in der Ansicht und im Dashboard)."""
    eigene = [d for d in drops_ if d.get("besitz")]
    if not eigene:
        return None
    return {**_summe([k for d in eigene for k in d["besitz"]["kaeufe"]]), "drops": len(eigene)}


def einzeln(con, drop_id: int, jetzt: datetime | None = None, katalog_set=None) -> dict | None:
    """Ein Drop mit Karten. `katalog_set(ids)` (optional) nennt {Scryfall-ID: Set-Code} für die Reprints eines Decks."""
    z = con.execute(_sql(con, "WHERE d.id = ?"), (drop_id,)).fetchone()
    if z is None or z["id"] is None:
        return None
    d = _drop(z, standard_kosten(con), standard_kosten(con, True), uhrzeit(con), _jetzt_zeit(jetzt), zone(),
              _produkte(con).get(drop_id, []), schwelle(con), _kaeufe_lesen(con, drop_id).get(drop_id, []))
    d["schwelle"] = schwelle(con)
    foil = ("preis_foil, teuer_foil, teuer_foil_set, teuer_foil_nummer, teuer_foil_id, guenstig_foil, guenstig_foil_set,"
            " guenstig_foil_nummer, guenstig_foil_id, ") if version(con) >= 18 else ""
    decks = version(con) >= 19
    deck = "deck, foil AS im_deck_foil, " if decks else "0 AS deck, NULL AS im_deck_foil, "
    d["liste"] = [dict(k) for k in con.execute(
        "SELECT name, nummer, anzahl, preis, teuer, teuer_set, teuer_nummer, teuer_id, guenstig, guenstig_set,"
        f" guenstig_nummer, guenstig_id, {foil}{deck}nur_sld, scryfall_id, bonus"
        " FROM secret_karte WHERE drop_id = ? ORDER BY bonus, deck, teuer IS NULL, teuer DESC, name", (drop_id,))]
    if decks:   # Set und Nummer der Reprints der Deckliste, für die Anzeige
        ids = [k["scryfall_id"] for k in d["liste"] if k["deck"]]
        if ids and katalog_set is not None:
            sets = katalog_set(ids)
            for k in d["liste"]:
                if k["deck"]:
                    k["set"] = sets.get(k["scryfall_id"])
    # Verlauf nur aus echten Abrufen, je Ausführung; fertige Decks in der Ausführung je Karte
    k_foil = "k.foil" if decks else "NULL"
    for schluessel, modus in (("verlauf", 0), ("verlauf_foil", 1)):
        ausdruck = (f"(CASE WHEN COALESCE({k_foil}, {modus}) = 1 THEN COALESCE(p.avg7_foil, p.low_foil)"
                    " ELSE COALESCE(p.avg7, p.low) END)")
        verlauf = [(t, _rund(w)) for t, w in con.execute(
            f"SELECT p.tag, SUM(k.anzahl * {ausdruck}) FROM preis p JOIN secret_karte k"
            " ON k.cardmarket_id = p.cardmarket_id WHERE k.drop_id = ? AND k.bonus = 0 GROUP BY p.tag ORDER BY p.tag",
            (drop_id,)) if w]
        d[schluessel] = {"erster": verlauf[0], "letzter": verlauf[-1], "tage": len(verlauf)} if verlauf else None
    return d


# ---------------------------------------------------------------- Eigene Secret Lairs

KAUF_FELDER = ("foil", "anzahl", "preis", "datum")


def kauf_anlegen(con, nutzer: int, drop_id: int, felder: dict) -> tuple[int, str]:
    """Neuer Kauf (in der Transaktion des Aufrufers); gibt (ID, Schritt) für Rückgängig zurück."""
    schritt = schritte.neuer_schritt()
    kauf = schritte.einfuegen(con, nutzer, "secret_kauf", {"nutzer_id": nutzer, "drop_id": drop_id,
                                                          **{k: felder[k] for k in KAUF_FELDER}}, schritt=schritt)
    return kauf, schritt


def kauf_aendern(con, nutzer: int, kauf: int, felder: dict) -> str:
    schritt = schritte.neuer_schritt()
    schritte.aendern_genau(con, nutzer, "secret_kauf", kauf, {k: felder[k] for k in KAUF_FELDER if k in felder},
                           schritt=schritt)
    return schritt


def kauf_loeschen(con, nutzer: int, kauf: int) -> str:
    schritt = schritte.neuer_schritt()
    schritte.loeschen(con, nutzer, "secret_kauf", kauf, schritt=schritt)
    return schritt


def stand(con) -> dict:
    return {"abgleich": wert_lesen(con, "abgleich"), "bewertet": wert_lesen(con, "bewertet"),
            "fehler": wert_lesen(con, "fehler"), "wiki": wert_lesen(con, "wiki_stand")}


def fuer_home_assistant(con, jetzt: datetime | None = None, wuensche: bool = True) -> dict:
    """Kommende Drops, der zuletzt aufgenommene (sein Name wechselt, sobald die App einen neuen findet) und die
    kommenden Wünsche: `secret_wunsch_naechster` ist der Verkaufsbeginn des nächsten (Zeitstempel für eine
    Automation). Datenbanken vor Version 15 kennen keine Wünsche (`wuensche=False`)."""
    jetzt, uhr, tz = _jetzt_zeit(jetzt), uhrzeit(con), zone()
    spalte = "wunsch" if wuensche else "0"
    kommend, liste_ = 0, []
    for z in con.execute(f"SELECT name, verkauf, {spalte} AS wunsch FROM secret_drop ORDER BY verkauf, id"):
        start = beginn(z["verkauf"], uhr, tz)
        if start is None or start > jetzt:
            kommend += 1
            if z["wunsch"] and start is not None:
                liste_.append({"name": z["name"], "beginn": start.isoformat()})
    # Neuester nach Verkaufstag (unbekannter Tag zuerst), bei gleichem Tag der zuletzt aufgenommene: Seit Version 18
    # nimmt der Abgleich auch alte Drops neu auf, die höchste ID allein wäre dann ein Drop von 2019
    z = con.execute("SELECT name FROM secret_drop ORDER BY verkauf IS NULL DESC, verkauf DESC, id DESC LIMIT 1").fetchone()
    return {"secret_kommend": kommend, "secret_neuester": z[0] if z else None,
            "secret_wuensche": len(liste_), "secret_wunsch_naechster": liste_[0]["beginn"] if liste_ else None,
            "secret_wunsch_name": liste_[0]["name"] if liste_ else None, "secret_wuensche_liste": liste_}


class Secretabgleich(threading.Thread):
    """Add-on: schaut stündlich nach. Nach 20 Stunden gleicht er neu ab, nach einer neuen Preisdatei bewertet er neu."""

    def __init__(self, datenbank: Path, katalog: Path, preisdatei: Path):
        super().__init__(name="Secretabgleich", daemon=True)
        self.datenbank, self.katalog, self.preisdatei = Path(datenbank), Path(katalog), Path(preisdatei)
        self.halt = threading.Event()

    def _zeit(self, name: str) -> datetime | None:
        with closing(verbinden(self.datenbank, nur_lesen=True)) as con:
            wert = wert_lesen(con, name)
        return datetime.strptime(wert, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if wert else None

    def blick(self) -> None:
        if not self.katalog.is_file():
            return
        jetzt = datetime.now(timezone.utc)
        abgleich = self._zeit("abgleich")
        with closing(verbinden(self.datenbank, nur_lesen=True)) as con:
            fassung = wert_lesen(con, "fassung")
        if abgleich is None or (jetzt - abgleich).total_seconds() >= ALT or fassung != str(FASSUNG):
            einmal(self.datenbank, self.katalog, self.preisdatei)
            return
        bewertet = self._zeit("bewertet")
        if self.preisdatei.is_file():
            datei = datetime.fromtimestamp(self.preisdatei.stat().st_mtime, timezone.utc)
            with closing(verbinden(self.datenbank, nur_lesen=True)) as con:   # bewertet vor Version 13: Drucke fehlen
                ohne_druck = con.execute("SELECT 1 FROM secret_karte WHERE teuer IS NOT NULL AND teuer_id IS NULL"
                                         " LIMIT 1").fetchone()
            if bewertet is None or datei > bewertet or ohne_druck:
                bewerten_lauf(self.datenbank, self.katalog, self.preisdatei)

    def run(self) -> None:
        self.halt.wait(120)   # erst Serverstart und Preisabruf abwarten
        while not self.halt.is_set():
            try:
                self.blick()
            except Exception:   # der Thread darf nie still sterben
                log.exception("Fehler im Secret-Lair-Abgleich")
            self.halt.wait(TAKT)
