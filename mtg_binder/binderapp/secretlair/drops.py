"""Secret-Lair-Drops finden und bewerten (Wunsch des Nutzers, 05.11.2026). Die App zeigt Zahlen, keine Kaufempfehlung.

Quellen und was sie taugen (gemessen am Star-Trek-Drop, vier Tage vor dem Verkauf):
- **Katalog (Scryfall):** kennt viele Karten früh, oft mit Cardmarket-Produkt, aber keinen Namen des Drops. `erschienen`
  ist der Verkaufstag. Karten ohne Drop werden nach Tag und fortlaufender Sammlernummer gruppiert; das ist nur eine
  Näherung, nebeneinanderliegende Drops können verschmelzen.
- **MTGJSON (`SLD.json`, Abschnitt `decks`):** nennt Drops und ihre Karten, kommt aber später. Sein `releaseDate` ist
  nicht der Verkaufstag (Corn Maze: 14.12. statt 12.10.2026), darum gilt immer das Datum aus dem Katalog.
- **Von Hand:** Der Nutzer fügt die Kartenliste aus der Ankündigung von Wizards ein. Sobald der Katalog die Drucke
  kennt, verbindet der Abgleich sie, und sie verschwinden aus den automatischen Gruppen.

Bewertung je Karte aus der Preisdatei des Tages (Ø 7 Tage, sonst Low, nur Nonfoil):
- `teuer`: teuerster anderer Druck derselben Karte (Wunsch des Nutzers), `guenstig`: günstigster.
- Basic Lands zählen nur gegen andere Secret Lairs: Sammlerdrucke wie Guru-Länder ergäben keinen sinnvollen Vergleich.
- Sets der Art `memorabilia` (zum Beispiel Weltmeisterschaftsdecks) zählen nicht, das sind keine spielbaren Drucke.

    einmal(datenbank, katalog, preisdatei)      Abgleich und Bewertung, gibt den Stand zurück
    bewerten_lauf(datenbank, katalog, preisdatei)   nur die Bewertung (nach neuen Preisen oder einem eingefügten Drop)
    Secretabgleich                              Thread im Add-on: täglich

Verkaufsbeginn (Wunsch des Nutzers, 09.11.2026): Tag des Drops plus eine Uhrzeit für alle Drops (Einstellung,
Standard 17:00 Ortszeit). Den Tag kann der Nutzer je Drop ändern (`datum_eigen`), der Abgleich lässt ihn dann.
Drops mit `wunsch` möchte der Nutzer kaufen; Home Assistant bekommt den Beginn des nächsten als Zeitstempel.
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

from binderapp.db.datenbank import schreiben, verbinden
from binderapp.katalog.suche import normalisiere
from binderapp.preise import abruf

MTGJSON = "https://mtgjson.com/api/v5/SLD.json"
FENSTER_TAGE = 365     # Antwort des Nutzers: Drops der letzten 12 Monate
ALT = 20 * 3600        # ab diesem Alter wird neu abgeglichen
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
    """Drops aus `SLD.json`: [{"name", "karten": [(Scryfall-ID, Anzahl)]}]. Foil-Ausgaben sind derselbe Drop."""
    daten = json.loads(roh)["data"]
    ids = {k["uuid"]: k.get("identifiers", {}).get("scryfallId") for k in daten.get("cards", [])}
    drops, gesehen = [], set()
    for d in daten.get("decks", []):
        name = d.get("name") or ""
        if not name or name in gesehen or name.endswith("Foil Edition"):
            continue
        gesehen.add(name)
        karten = [(ids[e["uuid"]], int(e.get("count") or 1)) for e in d.get("mainBoard", []) if ids.get(e.get("uuid"))]
        if karten:
            drops.append({"name": name, "karten": karten})
    return drops


def mtgjson_laden(oeffnen=urllib.request.urlopen) -> list[dict]:
    anfrage = urllib.request.Request(MTGJSON, headers={"User-Agent": "MTG-Binder (privat)"})
    with oeffnen(anfrage, timeout=ZEIT) as antwort:
        return mtgjson_lesen(antwort.read())


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
                    "karten": [(d["scryfall_id"], 1) for d in g]})
    return aus


def _gruppen_schreiben(con, gruppen: list[dict], drucke: dict, behalten: set[int]) -> None:
    """Gleicht die automatischen Drops ab. Ein Drop behält Name vom Nutzer und Kosten, auch wenn er seinen Schlüssel
    wechselt, zum Beispiel wenn MTGJSON den Namen nachliefert."""
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
        ids = {sid for sid, _ in g["karten"]}
        gemeinsam = [(len(ids & d["karten"]), i) for i, d in frei.items()]
        n, i = max(gemeinsam, default=(0, None))
        if n and n * 2 >= min(len(ids), len(frei[i]["karten"])):
            zuordnung[platz] = i
            del frei[i]
    for i in frei:
        con.execute("DELETE FROM secret_drop WHERE id = ?", (i,))
    for platz, g in enumerate(gruppen):
        verkauf = min(drucke[sid]["erschienen"] for sid, _ in g["karten"])
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
        for sid, anzahl in g["karten"]:
            d = drucke[sid]
            con.execute("INSERT INTO secret_karte (drop_id, name, oracle_id, scryfall_id, nummer, cardmarket_id, anzahl)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (drop, d["name"], d["oracle_id"], sid, d["nummer"], d["cardmarket_id"], anzahl))


def abgleichen(con, kat, decks: list[dict] | None, heute: date) -> None:
    """Trägt die Drops der letzten 12 Monate und die kommenden ein (in der Transaktion des Aufrufers). `decks` ist
    None, wenn MTGJSON gerade nicht erreichbar war: Dann bleiben die Drops aus MTGJSON, wie sie sind."""
    grenze = (heute - timedelta(days=FENSTER_TAGE)).isoformat()
    drucke = {z["scryfall_id"]: dict(z) for z in kat.execute(
        "SELECT scryfall_id, oracle_id, name, nummer, nummer_zahl, erschienen, cardmarket_id FROM druck"
        " WHERE set_code = ? AND erschienen >= ?", (SET, grenze))}
    nach_oracle: dict[str, list[dict]] = {}
    for d in drucke.values():
        nach_oracle.setdefault(d["oracle_id"], []).append(d)
    belegt = _hand_verbinden(con, drucke, nach_oracle)
    behalten: set[int] = set()
    gruppen: list[dict] = []
    if decks is None:
        for z in con.execute("SELECT k.drop_id, k.scryfall_id FROM secret_karte k JOIN secret_drop d ON d.id = k.drop_id"
                             " WHERE d.quelle = 'mtgjson'"):
            behalten.add(z["drop_id"])
            belegt.add(z["scryfall_id"])
    for deck in decks or []:
        karten = [(sid, n) for sid, n in deck["karten"] if sid in drucke and sid not in belegt]
        if karten:
            belegt.update(sid for sid, _ in karten)
            gruppen.append({"schluessel": f"mtgjson:{deck['name']}", "quelle": "mtgjson", "name": deck["name"][:NAME_MAX],
                            "karten": karten})
    gruppen += _laeufe([d for sid, d in drucke.items() if sid not in belegt])
    gruppen.sort(key=lambda g: (min(drucke[sid]["erschienen"] for sid, _ in g["karten"]), g["schluessel"]))
    _gruppen_schreiben(con, gruppen, drucke, behalten)
    con.execute("DELETE FROM secret_drop WHERE quelle <> 'hand' AND verkauf < ?", (grenze,))


# ---------------------------------------------------------------- Bewertung

def _preis(z: tuple) -> float | None:
    """Nonfoil-Wert einer Zeile aus `abruf.einlesen`: (Produkt, low, avg7, low_foil, avg7_foil)."""
    wert = z[2] if z[2] else z[1]
    return wert if wert else None


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
    preise = {z[0]: _preis(z) for z in abruf.einlesen(roh, ids)[2]}
    for k in karten:
        drucke = andere.get(k["oracle_id"], [])
        nur_sld = any("Basic" in (d["typzeile"] or "") for d in drucke)
        werte = [(preise[d["cardmarket_id"]], d) for d in drucke
                 if d["scryfall_id"] != k["scryfall_id"] and preise.get(d["cardmarket_id"])
                 and (not nur_sld or d["set_code"] == SET)]
        teuer = max(werte, key=lambda w: w[0], default=None)
        guenstig = min(werte, key=lambda w: w[0], default=None)
        con.execute("UPDATE secret_karte SET preis = ?, teuer = ?, teuer_set = ?, teuer_nummer = ?, teuer_id = ?,"
                    " guenstig = ?, guenstig_set = ?, guenstig_nummer = ?, guenstig_id = ?, nur_sld = ? WHERE id = ?",
                    (preise.get(k["cardmarket_id"]), teuer and teuer[0], teuer and teuer[1]["set_code"],
                     teuer and teuer[1]["nummer"], teuer and teuer[1]["scryfall_id"], guenstig and guenstig[0],
                     guenstig and guenstig[1]["set_code"], guenstig and guenstig[1]["nummer"],
                     guenstig and guenstig[1]["scryfall_id"], int(nur_sld), k["id"]))
    wert_setzen(con, "bewertet", _jetzt())
    return len(karten)


def bewerten_lauf(datenbank: Path, katalog: Path, preisdatei: Path) -> int:
    """Nur bewerten. Ohne Preisdatei (Add-on frisch installiert) bleibt alles ohne Werte, bis sie da ist."""
    if not Path(preisdatei).is_file():
        return 0
    roh = Path(preisdatei).read_bytes()
    with closing(verbinden(katalog, nur_lesen=True)) as kat, closing(verbinden(datenbank)) as con, schreiben(con):
        return bewerten(con, kat, roh)


def einmal(datenbank: Path, katalog: Path, preisdatei: Path, oeffnen=urllib.request.urlopen, heute: date | None = None) -> dict:
    """Abgleich und Bewertung. Ist MTGJSON nicht erreichbar, läuft der Rest trotzdem, der Fehler steht im Stand."""
    fehler = None
    try:
        decks = mtgjson_laden(oeffnen)
    except (OSError, ValueError, KeyError) as f:
        decks, fehler = None, f"MTGJSON nicht erreichbar: {f}"
        log.warning("%s", fehler)
    with closing(verbinden(katalog, nur_lesen=True)) as kat, closing(verbinden(datenbank)) as con, schreiben(con):
        abgleichen(con, kat, decks, heute or date.today())
        wert_setzen(con, "abgleich", _jetzt())
        wert_setzen(con, "fehler", fehler)
    karten = bewerten_lauf(datenbank, katalog, preisdatei)
    return {"karten": karten, "fehler": fehler}


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
SELECT d.id, d.name, d.quelle, d.verkauf, d.datum_eigen, d.wunsch, d.kosten, COUNT(k.id) AS karten, SUM(k.anzahl) AS exemplare,
       SUM(k.anzahl * k.teuer) AS teuer, SUM(k.anzahl * k.guenstig) AS guenstig, SUM(k.anzahl * k.preis) AS wert,
       SUM(k.preis IS NOT NULL) AS mit_preis, SUM(k.teuer IS NULL) AS ohne_vergleich
FROM secret_drop d LEFT JOIN secret_karte k ON k.drop_id = d.id {wo} GROUP BY d.id
ORDER BY d.verkauf IS NULL DESC, d.verkauf DESC, d.id DESC
"""


def _rund(wert) -> float | None:
    return None if wert is None else round(wert, 2)


def _jetzt_zeit(jetzt: datetime | None) -> datetime:
    return jetzt if jetzt is not None else datetime.now(timezone.utc)


def _drop(z, standard: float | None, uhr: str, jetzt: datetime, tz) -> dict:
    d = dict(z)
    kosten = d["kosten"] if d["kosten"] is not None else standard
    for f in ("teuer", "guenstig", "wert"):
        d[f] = _rund(d[f])
    start = beginn(d["verkauf"], uhr, tz)
    d.update(kosten_eigen=d["kosten"], kosten=kosten, kommend=start is None or start > jetzt,
             beginn=start.isoformat() if start else None, wunsch=bool(d["wunsch"]), datum_eigen=bool(d["datum_eigen"]),
             faktor=round(d["teuer"] / kosten, 2) if kosten and d["teuer"] is not None else None)
    return d


def standard_kosten(con) -> float | None:
    wert = wert_lesen(con, "kosten")
    return float(wert) if wert else None


def liste(con, jetzt: datetime | None = None) -> list[dict]:
    standard, uhr, tz = standard_kosten(con), uhrzeit(con), zone()
    return [_drop(z, standard, uhr, _jetzt_zeit(jetzt), tz) for z in con.execute(SQL_DROPS.format(wo=""))]


def einzeln(con, drop_id: int, jetzt: datetime | None = None) -> dict | None:
    z = con.execute(SQL_DROPS.format(wo="WHERE d.id = ?"), (drop_id,)).fetchone()
    if z is None or z["id"] is None:
        return None
    d = _drop(z, standard_kosten(con), uhrzeit(con), _jetzt_zeit(jetzt), zone())
    d["liste"] = [dict(k) for k in con.execute(
        "SELECT name, nummer, anzahl, preis, teuer, teuer_set, teuer_nummer, teuer_id, guenstig, guenstig_set,"
        " guenstig_nummer, guenstig_id, nur_sld, scryfall_id"
        " FROM secret_karte WHERE drop_id = ? ORDER BY teuer IS NULL, teuer DESC, name", (drop_id,))]
    # Verlauf nur aus echten Abrufen, mit Foil-Preisen hat er nichts zu tun
    verlauf = [(t, _rund(w)) for t, w in con.execute(
        "SELECT p.tag, SUM(k.anzahl * COALESCE(p.avg7, p.low)) FROM preis p JOIN secret_karte k"
        " ON k.cardmarket_id = p.cardmarket_id WHERE k.drop_id = ? GROUP BY p.tag ORDER BY p.tag", (drop_id,)) if w]
    d["verlauf"] = {"erster": verlauf[0], "letzter": verlauf[-1], "tage": len(verlauf)} if verlauf else None
    return d


def stand(con) -> dict:
    return {"abgleich": wert_lesen(con, "abgleich"), "bewertet": wert_lesen(con, "bewertet"),
            "fehler": wert_lesen(con, "fehler")}


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
    z = con.execute("SELECT name FROM secret_drop ORDER BY id DESC LIMIT 1").fetchone()
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
        if abgleich is None or (jetzt - abgleich).total_seconds() >= ALT:
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
