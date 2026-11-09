"""Täglicher Abgleich des Katalogs mit Scryfall: nur Neues holen (Wunsch des Nutzers, 04.11.2026).

Läuft im Add-on (eigener Thread, `Katalogabgleich`) und am PC (im Leerlauf des Verarbeiters, siehe
`binderapp.verarbeiten.scryfall`), jeweils auf dem eigenen Katalog. Nur Standardbibliothek (Python 3.11).

Weg (Scryfall-Schnittstelle, Stand 11/2026):
1. `GET /cards/manifest` listet jede Karte mit ID, Set, Nummer und Sprache, 15.000 je Seite, höchstens
   10 Anfragen je Minute. Ohne `lang` kommen die „default“-Drucke (Englisch oder die einzige Sprache) wie in
   der Tabelle `druck`, mit `lang=xx` alle Drucke einer Sprache (Tabelle `sprachdruck`). Jede ID, die der
   Katalog nicht kennt, ist neu. Die Seiten werden direkt mit dem Katalog verglichen, nie alle IDs im Speicher.
2. `POST /cards/collection` holt zu den neuen IDs die vollständigen Karten (75 je Anfrage, höchstens 2 je
   Sekunde). Dazu kommen junge Drucke (bis `AUFFRISCHEN_TAGE` alt) ohne Cardmarket-Produkt: Das trägt Scryfall
   bei neuen Sets oft erst Tage nach dem Erscheinen nach, und ohne es gibt es keine Preise. Secret-Lair-Drucke
   der letzten `SECRET_LAIR_TAGE` und alle kommenden kommen immer dazu (Verkaufstag der Drops).
3. Geschrieben wird in Blöcken von `BLOCK` Karten, je Block eine Transaktion (druck, sprachdruck, satz, orakel,
   namen); so bleibt der Speicher im Add-on klein, und ein Abbruch verliert nur den laufenden Block (der nächste
   Lauf findet den Rest wieder als unbekannt). Nichts wird gelöscht. Erst am Ende hält `meta.abgleich` den
   Zeitpunkt fest; `meta.gebaut` bleibt (danach richtet sich, ob der PC seinen Katalog ins Add-on schickt).
   Ein Katalog in älterem Format wird nicht abgeglichen (erst am PC neu bauen).

Antwortet Scryfall mit 429 (zu viele Anfragen), wird 35 Sekunden gewartet und es höchstens dreimal neu versucht
(Scryfall sperrt dann 30 Sekunden und verlangt, die Last zu senken). Nach einem gescheiterten Abgleich wartet das
Add-on 6 Stunden. Nur IDs im UUID-Format werden übernommen; Weiterleitungen werden nicht verfolgt.

Neuheiten für den PC (Wunsch des Nutzers, 04.11.2026, Add-on 0.12.0): Nur das Add-on fragt Scryfall. Jede neue
oder geänderte Karte landet zusätzlich (gekürzt, ohne Preise, Regeltexte und Links) in der Tabelle `neuheit` mit
laufender Nummer `lfd`. Die exe holt über `GET /pc/katalog/neu` alles nach ihrer letzten Nummer
(`neuheiten_holen`) und schreibt es mit demselben `schreiben` in ihren Katalog; Namensverzeichnis und Bilder
nimmt sie aus diesen Karten (`neuheit_karten`). Damit die exe auch Karten bekommt, die Scryfall erst später als
fertigen Scan hat, frischt das Add-on junge Drucke ohne fertigen Scan täglich auf. Wird die Tabelle neu angelegt
(erster Lauf, oder der PC hat einen neuen Katalog geschickt), kommen alle Drucke der letzten `AUFFRISCHEN_TAGE`
einmal hinein. Die Kennung `meta.neuheit_kennung` wechselt mit jeder neuen Tabelle; dann beginnt die exe von vorn
(das Schreiben ist wiederholbar).
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from binderapp.katalog.bau import NAMENSSPRACHEN, NEBENKARTEN, SPALTEN_DRUCK, _namensvarianten, gedruckter_name, oracle_von
from binderapp.katalog.kartendaten import zeile
from binderapp.katalog.suche import normalisiere

API = "https://api.scryfall.com"
KOPF = {"User-Agent": "MTG-Binder-Privat/1.0", "Accept": "application/json"}
# Mindestabstand je Schnittstelle in Sekunden (Scryfall: Manifest 10/Minute, Sammlung 2/Sekunde, sonst 10/Sekunde)
ABSTAND = {"/cards/manifest": 7.0, "/cards/collection": 0.6}
ABSTAND_SONST = 0.15
WARTEN_429 = 35
VERSUCHE_429 = 3
ZEITLIMIT = 60
SAMMLUNG_JE_ANFRAGE = 75
AUFFRISCHEN_TAGE = 120
SECRET_LAIR = "sld"
SECRET_LAIR_TAGE = 60   # Secret-Lair-Drucke so lange täglich auffrischen (und alle kommenden)
ALT = 20 * 3600        # ab diesem Alter des letzten Abgleichs wird neu abgeglichen
TAKT = 3600            # Sekunden zwischen zwei Blicken im Add-on
HOECHSTENS_SEITEN = 200   # Schutz gegen eine endlose Seitenkette

log = logging.getLogger("binderapp.katalog")


BLOCK = 2000           # Karten je Schreibblock (Speicher im Add-on klein halten, Fortschritt bleibt bei Abbruch)
HOECHSTENS_ANTWORT = 32 * 1024 * 1024
NACH_FEHLER = 6 * 3600   # nach einem fehlgeschlagenen Abgleich so lange warten (Scryfall nicht stündlich belasten)
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
NEUHEITEN_JE_SEITE = 500   # Karten je Antwort an den PC
# Felder, die weder Katalog noch Erkennung brauchen (hält die Tabelle `neuheit` klein)
WEGLASSEN = frozenset({
    "prices", "purchase_uris", "related_uris", "legalities", "uri", "scryfall_uri", "rulings_uri",
    "prints_search_uri", "set_uri", "set_search_uri", "scryfall_set_uri", "multiverse_ids", "mtgo_id",
    "mtgo_foil_id", "arena_id", "tcgplayer_id", "tcgplayer_etched_id", "artist_ids", "oracle_text", "flavor_text",
    "all_parts", "keywords", "games", "edhrec_rank", "penny_rank", "preview", "printed_text"})


class AbgleichFehler(RuntimeError):
    pass


class _KeineWeiterleitung(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Weiterleitung nicht erlaubt", headers, fp)


OEFFNER = urllib.request.build_opener(_KeineWeiterleitung())


def _ids(eintraege) -> list[str]:
    """Nur gültige Scryfall-IDs (UUID) aus einer Antwort; alles andere fällt weg."""
    return [e["id"] for e in eintraege if isinstance(e, dict) and isinstance(e.get("id"), str) and UUID.match(e["id"])]


class Scryfall:
    """Kleiner Zugang zur Scryfall-Schnittstelle mit festen Abständen je Schnittstelle.

    `oeffnen` ist austauschbar (Tests); `schlafen` ebenso. Weiterleitungen werden nicht verfolgt."""

    def __init__(self, oeffnen=OEFFNER.open, schlafen=time.sleep, uhr=time.monotonic):
        self.oeffnen, self.schlafen, self.uhr = oeffnen, schlafen, uhr
        self.letzte: dict[str, float] = {}
        self.anfragen = 0

    def _warten(self, pfad: str) -> None:
        abstand = ABSTAND.get(pfad, ABSTAND_SONST)
        zuletzt = self.letzte.get(pfad)
        if zuletzt is not None:
            rest = abstand - (self.uhr() - zuletzt)
            if rest > 0:
                self.schlafen(rest)
        self.letzte[pfad] = self.uhr()

    def anfrage(self, url: str, daten: dict | None = None) -> dict:
        if not url.startswith(API + "/"):
            raise AbgleichFehler(f"Unerwartete Adresse: {url[:80]}")
        pfad = url[len(API):].split("?", 1)[0]
        for versuch in range(VERSUCHE_429 + 1):
            self._warten(pfad)
            kopf = dict(KOPF)
            roh = None
            if daten is not None:
                roh = json.dumps(daten).encode("utf-8")
                kopf["Content-Type"] = "application/json"
            self.anfragen += 1
            try:
                with self.oeffnen(urllib.request.Request(url, data=roh, headers=kopf), timeout=ZEITLIMIT) as antwort:
                    inhalt = antwort.read(HOECHSTENS_ANTWORT + 1)
                if len(inhalt) > HOECHSTENS_ANTWORT:
                    raise AbgleichFehler(f"Antwort von Scryfall zu groß ({pfad})")
                return json.loads(inhalt)
            except urllib.error.HTTPError as f:
                if f.code == 429 and versuch < VERSUCHE_429:
                    log.warning("Scryfall: zu viele Anfragen (429), warte %s s", WARTEN_429)
                    self.schlafen(WARTEN_429)
                    continue
                raise AbgleichFehler(f"Scryfall antwortet mit {f.code} ({pfad})") from f
            except (urllib.error.URLError, OSError, ValueError) as f:
                raise AbgleichFehler(f"Scryfall nicht erreichbar ({pfad}): {f}") from f
        raise AbgleichFehler("Scryfall: zu viele Anfragen")

    def manifest(self, sprache: str | None = None):
        """Seiten des Manifests (Listen von Einträgen)."""
        url = f"{API}/cards/manifest" + (f"?lang={sprache}" if sprache else "")
        for _ in range(HOECHSTENS_SEITEN):
            antwort = self.anfrage(url)
            yield antwort.get("data") or []
            if not antwort.get("has_more"):
                return
            url = antwort.get("next_page") or ""
            if not url:
                return
        raise AbgleichFehler("Manifest: zu viele Seiten")

    def karten(self, ids: list[str]) -> list[dict]:
        """Vollständige Karten zu IDs (unbekannte IDs fallen weg)."""
        aus = []
        for anfang in range(0, len(ids), SAMMLUNG_JE_ANFRAGE):
            teil = ids[anfang:anfang + SAMMLUNG_JE_ANFRAGE]
            antwort = self.anfrage(f"{API}/cards/collection", {"identifiers": [{"id": i} for i in teil]})
            aus += [k for k in antwort.get("data") or []
                    if isinstance(k, dict) and isinstance(k.get("id"), str) and UUID.match(k["id"])]
        return aus


# ----------------------------------------------------------------- Katalog lesen


def _unbekannt(kat, ids: list[str]) -> list[str]:
    """IDs, die weder in `druck` noch in `sprachdruck` stehen."""
    bekannt: set[str] = set()
    for anfang in range(0, len(ids), 500):
        teil = ids[anfang:anfang + 500]
        platz = ", ".join("?" for _ in teil)
        for tabelle in ("druck", "sprachdruck"):
            bekannt.update(z[0] for z in kat.execute(f"SELECT scryfall_id FROM {tabelle} WHERE scryfall_id IN ({platz})",
                                                    teil))
    return [i for i in ids if i not in bekannt]


def sprachen(kat) -> list[str]:
    """Sprachen des Katalogs außer Englisch (für die Manifeste je Sprache)."""
    alle = {z[0] for z in kat.execute("SELECT DISTINCT sprache FROM sprachdruck")}
    alle |= {z[0] for z in kat.execute("SELECT DISTINCT sprache FROM druck")}
    return sorted(s for s in alle if s and s != "en" and s.isalnum() and len(s) <= 4)


def letzter_abgleich(pfad: Path) -> str | None:
    """Zeitpunkt des letzten Abgleichs (UTC, ISO) oder None."""
    if not Path(pfad).is_file():
        return None
    try:
        with closing(sqlite3.connect(f"{Path(pfad).resolve().as_uri()}?mode=ro", uri=True)) as kat:
            z = kat.execute("SELECT wert FROM meta WHERE schluessel = 'abgleich'").fetchone()
    except sqlite3.Error:
        return None
    return z[0] if z else None


def saat_offen(pfad: Path) -> bool:
    """Add-on: Die Neuheiten für den PC sind noch nicht angelegt oder nicht fertig gefüllt (dann sofort abgleichen,
    auch wenn der letzte Abgleich jünger als 20 Stunden ist, z. B. direkt nach dem Update auf 0.12.0)."""
    if not Path(pfad).is_file():
        return False
    try:
        with closing(sqlite3.connect(f"{Path(pfad).resolve().as_uri()}?mode=ro", uri=True)) as kat:
            z = kat.execute("SELECT wert FROM meta WHERE schluessel = 'neuheit_saat'").fetchone()
    except sqlite3.Error:
        return False
    return not z or z[0] != "fertig"


def faellig(pfad: Path, jetzt: datetime | None = None) -> bool:
    if not Path(pfad).is_file():
        return False   # ohne Katalog gibt es nichts abzugleichen (den ersten baut der PC)
    zuletzt = letzter_abgleich(pfad)
    if not zuletzt:
        return True
    try:
        alt = datetime.fromisoformat(zuletzt).replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return ((jetzt or datetime.now(timezone.utc)) - alt).total_seconds() > ALT


# ----------------------------------------------------------------- Schreiben


def _druck_zeile(objekt: dict, set_erschienen: str | None, bild: int) -> tuple:
    z = zeile(objekt)
    return (
        z["scryfall_id"], z["oracle_id"], z["name"], z["set_code"] or "", z["set_name"], set_erschienen,
        objekt.get("released_at"), z["nummer"] or "", z["nummer_zahl"], z["sprache"] or "", z["manakosten"],
        z["manawert"], z["farben"], z["farbidentitaet"], z["farbgruppe"], z["typzeile"], z["haupttyp"],
        z["seltenheit"], objekt.get("layout"), bild, objekt.get("cardmarket_id"), z["besonderheiten"], z["promo"],
        z["set_art"],
    )


def _name_merken(kat, oracle: str | None, name: str | None, sprache: str) -> int:
    if not oracle or not name:
        return 0
    neu = 0
    for variante in _namensvarianten(name):
        norm = normalisiere(variante)
        if not norm:
            continue
        if kat.execute("SELECT 1 FROM namen WHERE oracle_id = ? AND name = ? AND sprache = ?",
                       (oracle, variante, sprache)).fetchone() is None:
            kat.execute("INSERT INTO namen (oracle_id, name, sprache, norm) VALUES (?, ?, ?, ?)",
                        (oracle, variante, sprache, norm))
            neu += 1
    return neu


def schreiben(kat, drucke: list[dict], sprachdrucke: list[dict], bilder: Path | None = None) -> dict:
    """Schreibt neue und aufgefrischte Drucke in den Katalog (in der Transaktion des Aufrufers).

    `drucke`: Scryfall-Karten für die Tabelle `druck` (neu oder aufgefrischt), `sprachdrucke`: neue Karten für
    `sprachdruck`. `bilder`: Ordner der lokalen Bilder (PC) für `bild_vorhanden`."""
    from binderapp.katalog import bilddatei
    zahl = {"drucke_neu": 0, "drucke_aufgefrischt": 0, "sprachdrucke_neu": 0, "saetze_neu": 0, "namen_neu": 0}
    sprachen_namen = set(NAMENSSPRACHEN)
    saetze = {z[0]: [z[1], z[2], z[3]] for z in kat.execute("SELECT set_code, name, erschienen, drucke FROM satz")}
    for objekt in drucke:
        set_code = str(objekt.get("set") or "").lower()
        erschienen = objekt.get("released_at")
        alt = kat.execute("SELECT bild_vorhanden FROM druck WHERE scryfall_id = ?", (objekt["id"],)).fetchone()
        satz = saetze.get(set_code)
        if alt is None:
            if satz is None:
                saetze[set_code] = [objekt.get("set_name"), erschienen, 0]
                kat.execute("INSERT INTO satz (set_code, name, erschienen, drucke) VALUES (?, ?, ?, 0)",
                            (set_code, objekt.get("set_name"), erschienen))
                zahl["saetze_neu"] += 1
                satz = saetze[set_code]
            satz[2] += 1
            if erschienen and (satz[1] is None or erschienen < satz[1]):
                satz[1] = erschienen
            kat.execute("UPDATE satz SET erschienen = ?, drucke = ? WHERE set_code = ?", (satz[1], satz[2], set_code))
        bild = 1 if bilder is not None and bilddatei(objekt["id"], bilder) else (alt[0] if alt else 0)
        werte = _druck_zeile(objekt, satz[1] if satz else erschienen, bild)
        kat.execute(f"INSERT OR REPLACE INTO druck ({', '.join(SPALTEN_DRUCK)}) "
                    f"VALUES ({', '.join('?' for _ in SPALTEN_DRUCK)})", werte)
        zahl["drucke_aufgefrischt" if alt else "drucke_neu"] += 1
        oracle = werte[1]
        if oracle:
            rang = 1 if objekt.get("layout") in NEBENKARTEN else 0
            o = kat.execute("SELECT rang FROM orakel WHERE oracle_id = ?", (oracle,)).fetchone()
            if o is None:
                kat.execute("INSERT INTO orakel (oracle_id, name, rang) VALUES (?, ?, ?)", (oracle, werte[2], rang))
            elif rang < o[0]:
                kat.execute("UPDATE orakel SET rang = ? WHERE oracle_id = ?", (rang, oracle))
            zahl["namen_neu"] += _name_merken(kat, oracle, werte[2], "en")
            if werte[9] in sprachen_namen:
                zahl["namen_neu"] += _name_merken(kat, oracle, gedruckter_name(objekt), werte[9])
    for objekt in sprachdrucke:
        oracle = oracle_von(objekt)
        sprache = objekt.get("lang") or ""
        gedruckt = gedruckter_name(objekt)
        cur = kat.execute("INSERT OR IGNORE INTO sprachdruck VALUES (?, ?, ?, ?, ?, ?)", (
            objekt["id"], oracle, str(objekt.get("set", "")).lower(), str(objekt.get("collector_number", "")),
            sprache, gedruckt or objekt.get("name")))
        zahl["sprachdrucke_neu"] += cur.rowcount
        if sprache in sprachen_namen and kat.execute("SELECT 1 FROM orakel WHERE oracle_id = ?", (oracle,)).fetchone():
            zahl["namen_neu"] += _name_merken(kat, oracle, gedruckt, sprache)
    return zahl


def meta_schreiben(kat, zahl: dict) -> None:
    """Zeitpunkt und Ergebnis des Abgleichs, Zeilenzahlen (in der Transaktion des Aufrufers)."""
    meta = {"abgleich": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            "abgleich_ergebnis": json.dumps(zahl, ensure_ascii=False)}
    for tabelle in ("druck", "satz", "sprachdruck", "orakel", "namen"):
        meta[f"zeilen_{tabelle}"] = str(kat.execute(f"SELECT COUNT(*) FROM {tabelle}").fetchone()[0])
    kat.executemany("INSERT OR REPLACE INTO meta (schluessel, wert) VALUES (?, ?)", list(meta.items()))


# ----------------------------------------------------------------- Neuheiten für den PC


NEUHEIT_TABELLE = """CREATE TABLE IF NOT EXISTS neuheit (
    lfd          INTEGER PRIMARY KEY AUTOINCREMENT,   -- wird nie wieder vergeben
    scryfall_id  TEXT NOT NULL UNIQUE,
    art          TEXT NOT NULL CHECK (art IN ('druck', 'sprache')),
    tag          TEXT NOT NULL,                       -- Tag der Aufnahme (UTC)
    karte        TEXT NOT NULL                        -- Scryfall-Karte als JSON, gekürzt
)"""


def hat_neuheit(kat) -> bool:
    return kat.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'neuheit'").fetchone() is not None


def neuheit_anlegen(kat) -> bool:
    """Add-on: legt die Tabelle `neuheit` mit neuer Kennung an (in der Transaktion des Aufrufers). True, wenn sie
    neu ist oder noch keine Kennung hatte (z. B. ein Katalog, den der PC geschickt hat)."""
    neu = not hat_neuheit(kat)
    kat.execute(NEUHEIT_TABELLE)
    if neu or not kat.execute("SELECT 1 FROM meta WHERE schluessel = 'neuheit_kennung'").fetchone():
        kat.executemany("INSERT OR REPLACE INTO meta (schluessel, wert) VALUES (?, ?)",
                        [("neuheit_kennung", secrets.token_hex(8)), ("neuheit_saat", "offen")])
    # die Saat gilt erst als erledigt, wenn ein Abgleich sie ganz geschrieben hat (ein Abbruch wiederholt sie)
    z = kat.execute("SELECT wert FROM meta WHERE schluessel = 'neuheit_saat'").fetchone()
    return bool(z and z[0] == "offen")


def gekuerzt(karte: dict) -> dict:
    """Karte ohne die Felder in `WEGLASSEN` (auch in den Kartenseiten)."""
    aus = {k: v for k, v in karte.items() if k not in WEGLASSEN}
    if isinstance(aus.get("card_faces"), list):
        aus["card_faces"] = [{k: v for k, v in s.items() if k not in WEGLASSEN} if isinstance(s, dict) else s
                             for s in aus["card_faces"]]
    return aus


def neuheiten_merken(kat, karten: list[dict], art: str, tag: str) -> int:
    """Merkt neue oder geänderte Karten mit neuer laufender Nummer (in der Transaktion des Aufrufers); unveränderte
    behalten ihre Nummer, damit der PC sie nicht täglich neu bekommt."""
    zahl = 0
    for k in karten:
        text = json.dumps(gekuerzt(k), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        alt = kat.execute("SELECT art, karte FROM neuheit WHERE scryfall_id = ?", (k["id"],)).fetchone()
        if alt is not None and tuple(alt) == (art, text):
            continue
        kat.execute("DELETE FROM neuheit WHERE scryfall_id = ?", (k["id"],))
        kat.execute("INSERT INTO neuheit (scryfall_id, art, tag, karte) VALUES (?, ?, ?, ?)", (k["id"], art, tag, text))
        zahl += 1
    return zahl


def _ohne_fertigen_scan(kat, grenze: str) -> list[str]:
    """Junge Drucke der Neuheiten, deren Bild noch kein fertiger Scan ist (täglich auffrischen, damit der PC den
    Scan bekommt, sobald Scryfall ihn hat)."""
    if not hat_neuheit(kat):
        return []
    aus = []
    for sid, text in kat.execute("SELECT n.scryfall_id, n.karte FROM neuheit n JOIN druck d USING (scryfall_id) "
                                 "WHERE n.art = 'druck' AND d.erschienen >= ?", (grenze,)):
        try:
            k = json.loads(text)
        except ValueError:
            continue
        if k.get("image_status") != "highres_scan" and not k.get("digital") and (
                k.get("image_uris") or any(isinstance(s, dict) and s.get("image_uris") for s in k.get("card_faces") or [])):
            aus.append(sid)
    return aus


def _saat(kat, grenze: str) -> tuple[list[str], list[str]]:
    """Für eine neue Tabelle `neuheit`: alle Drucke der letzten `AUFFRISCHEN_TAGE` und die Sprachdrucke der Sets
    aus dieser Zeit (einmal; deckt auch einen Abgleich ab, der vor 0.12.0 ohne Neuheiten lief)."""
    drucke = [z[0] for z in kat.execute("SELECT scryfall_id FROM druck WHERE erschienen >= ?", (grenze,))]
    sprach = [z[0] for z in kat.execute("SELECT s.scryfall_id FROM sprachdruck s JOIN satz t USING (set_code) "
                                        "WHERE t.erschienen >= ?", (grenze,))]
    return drucke, sprach


def neuheiten_lesen(pfad: Path, ab: int, kennung: str | None, hoechstens: int = NEUHEITEN_JE_SEITE) -> dict:
    """Antwort an den PC: Neuheiten nach Nummer `ab`. Passt die Kennung nicht (neue Tabelle), beginnt die Liste von
    vorn. Ohne Katalog oder Tabelle: Kennung None und keine Einträge."""
    leer = {"kennung": None, "ab": 0, "eintraege": [], "weiter": False, "abgleich": None}
    if not Path(pfad).is_file():
        return leer
    with closing(sqlite3.connect(f"{Path(pfad).resolve().as_uri()}?mode=ro", uri=True, timeout=30)) as kat:
        if not hat_neuheit(kat):
            return leer
        meta = dict(kat.execute("SELECT schluessel, wert FROM meta WHERE schluessel IN ('neuheit_kennung', 'abgleich')"))
        eigene = meta.get("neuheit_kennung")
        if kennung != eigene:
            ab = 0
        zeilen = kat.execute("SELECT lfd, art, karte FROM neuheit WHERE lfd > ? ORDER BY lfd LIMIT ?",
                             (int(ab), int(hoechstens) + 1)).fetchall()
    weiter = len(zeilen) > hoechstens
    zeilen = zeilen[:hoechstens]
    return {"kennung": eigene, "ab": zeilen[-1][0] if zeilen else int(ab), "weiter": weiter,
            "abgleich": meta.get("abgleich"),
            "eintraege": [{"lfd": z[0], "art": z[1], "karte": json.loads(z[2])} for z in zeilen]}


def neuheit_karten(pfad: Path, ids: list[str]) -> list[dict]:
    """Karten aus der Tabelle `neuheit` des eigenen Katalogs (PC: statt Scryfall zu fragen)."""
    if not ids or not Path(pfad).is_file():
        return []
    aus = []
    with closing(sqlite3.connect(f"{Path(pfad).resolve().as_uri()}?mode=ro", uri=True)) as kat:
        if not hat_neuheit(kat):
            return []
        for anfang in range(0, len(ids), 500):
            teil = ids[anfang:anfang + 500]
            platz = ", ".join("?" for _ in teil)
            for (text,) in kat.execute(f"SELECT karte FROM neuheit WHERE scryfall_id IN ({platz})", teil):
                try:
                    aus.append(json.loads(text))
                except ValueError:
                    continue
    return aus


def _gueltige_eintraege(eintraege) -> tuple[list[dict], list[dict]]:
    drucke, sprach = [], []
    for e in eintraege if isinstance(eintraege, list) else []:
        if not isinstance(e, dict) or not isinstance(e.get("karte"), dict):
            continue
        k = e["karte"]
        if not isinstance(k.get("id"), str) or not UUID.match(k["id"]):
            continue
        if e.get("art") == "druck":
            drucke.append(k)
        elif e.get("art") == "sprache":
            sprach.append(k)
    return drucke, sprach


def neuheiten_holen(pfad: Path, holen, bilder: Path | None = None, halt=lambda: False) -> dict:
    """PC: holt die Neuheiten des Add-ons (`holen(ab, kennung)` → Antwort von `neuheiten_lesen`) seitenweise und
    schreibt sie in den Katalog `pfad`, je Seite eine Transaktion mit der neuen Nummer. Ein Abbruch verliert nichts:
    der nächste Lauf macht ab der letzten geschriebenen Seite weiter."""
    pfad = Path(pfad)
    if not pfad.is_file():
        raise AbgleichFehler("Kein Katalog vorhanden.")
    start = time.monotonic()
    with closing(sqlite3.connect(f"{pfad.resolve().as_uri()}?mode=ro", uri=True)) as kat:
        schema_pruefen(kat)
        meta = dict(kat.execute("SELECT schluessel, wert FROM meta WHERE schluessel IN ('addon_kennung', 'addon_ab')"))
    kennung = meta.get("addon_kennung")
    ab = int(meta["addon_ab"]) if str(meta.get("addon_ab", "")).isdigit() else 0
    zahl = {"drucke_neu": 0, "drucke_aufgefrischt": 0, "sprachdrucke_neu": 0, "saetze_neu": 0, "namen_neu": 0,
            "eintraege": 0, "seiten": 0, "von_vorn": False}
    heute = datetime.now(timezone.utc).date().isoformat()
    for _ in range(100000):
        antwort = holen(ab, kennung)
        neue_kennung = antwort.get("kennung") if isinstance(antwort, dict) else None
        if not isinstance(neue_kennung, str) or not neue_kennung:
            break   # das Add-on hat noch keine Neuheiten (erster Abgleich steht aus)
        if neue_kennung != kennung:
            zahl["von_vorn"] = True
        neues_ab = antwort.get("ab")
        if not isinstance(neues_ab, int) or neues_ab < 0:
            raise AbgleichFehler("Ungültige Antwort des Add-ons (Nummer).")
        drucke, sprach = _gueltige_eintraege(antwort.get("eintraege"))
        abgleich_dort = antwort.get("abgleich") if isinstance(antwort.get("abgleich"), str) else None

        def arbeit(kat, drucke=drucke, sprach=sprach, k=neue_kennung, n=neues_ab, zeit=abgleich_dort):
            kat.execute(NEUHEIT_TABELLE)
            teil = schreiben(kat, drucke, sprach, bilder)
            neuheiten_merken(kat, drucke, "druck", heute)
            neuheiten_merken(kat, sprach, "sprache", heute)
            meta = {"addon_kennung": k, "addon_ab": str(n)}
            if zeit and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", zeit):
                meta["abgleich"] = zeit
            kat.executemany("INSERT OR REPLACE INTO meta (schluessel, wert) VALUES (?, ?)", list(meta.items()))
            return teil

        teil = _transaktion(pfad, arbeit)
        for k, v in teil.items():
            zahl[k] += v
        zahl["eintraege"] += len(drucke) + len(sprach)
        zahl["seiten"] += 1
        stillstand = neue_kennung == kennung and neues_ab <= ab
        kennung, ab = neue_kennung, neues_ab
        if not antwort.get("weiter") or halt() or stillstand:
            break
    zahl["sekunden"] = round(time.monotonic() - start, 1)
    return zahl


def _transaktion(pfad: Path, arbeit):
    with closing(sqlite3.connect(pfad, timeout=60)) as kat:
        try:
            kat.execute("BEGIN IMMEDIATE")
            ergebnis = arbeit(kat)
            kat.execute("COMMIT")
        except BaseException:
            if kat.in_transaction:
                kat.execute("ROLLBACK")
            raise
    return ergebnis


def schema_pruefen(kat) -> None:
    """Der Abgleich schreibt alle Spalten des aktuellen Katalogformats; ein älterer Katalog muss neu gebaut werden."""
    from binderapp.katalog import VERSION
    spalten = {z[1] for z in kat.execute("PRAGMA table_info(druck)")}
    fehlt = [s for s in SPALTEN_DRUCK if s not in spalten]
    v = kat.execute("SELECT wert FROM meta WHERE schluessel = 'version'").fetchone()
    if fehlt or not v or str(v[0]) != str(VERSION):
        raise AbgleichFehler(f"Der Katalog hat ein älteres Format (Version {v[0] if v else '?'}); erst am PC neu bauen "
                             "(python -m binderapp.katalog.bau) und schicken.")


# ----------------------------------------------------------------- Abgleich


def abgleichen(pfad: Path, scryfall: Scryfall | None = None, bilder: Path | None = None,
               heute: date | None = None, mit_sprachen: bool = True, neuheiten: bool = False) -> dict:
    """Gleicht den Katalog `pfad` mit Scryfall ab. Gibt die Zählung zurück.

    `neuheiten` (Add-on): neue und geänderte Karten zusätzlich für den PC in der Tabelle `neuheit` merken.
    Wirft `AbgleichFehler`, wenn Scryfall nicht erreichbar ist; dann bleibt der Katalog unverändert."""
    pfad = Path(pfad)
    if not pfad.is_file():
        raise AbgleichFehler("Kein Katalog vorhanden.")
    scryfall = scryfall or Scryfall()
    start = time.monotonic()
    heute = heute or date.today()
    grenze = (heute - timedelta(days=AUFFRISCHEN_TAGE)).isoformat()
    with closing(sqlite3.connect(f"{pfad.resolve().as_uri()}?mode=ro", uri=True)) as kat:
        schema_pruefen(kat)
    saat = _transaktion(pfad, neuheit_anlegen) if neuheiten else False
    with closing(sqlite3.connect(f"{pfad.resolve().as_uri()}?mode=ro", uri=True)) as kat:
        neu_default: list[str] = []
        for seite in scryfall.manifest():
            neu_default += _unbekannt(kat, _ids(seite))
        neu_default = list(dict.fromkeys(neu_default))
        default_ids = set(neu_default)
        neu_sprache: list[str] = []
        if mit_sprachen:
            for sprache in sprachen(kat):
                for seite in scryfall.manifest(sprache):
                    neu_sprache += [i for i in _unbekannt(kat, _ids(seite)) if i not in default_ids]
        auffrischen = [z[0] for z in kat.execute(
            "SELECT scryfall_id FROM druck WHERE cardmarket_id IS NULL AND erschienen >= ?", (grenze,))]
        # Secret Lair (Antwort des Nutzers, 09.11.2026): junge und kommende Drucke täglich neu holen, damit eine
        # spätere Korrektur von Scryfall (Verkaufstag, Nummer) den Katalog erreicht; etwa 150 Karten, 2–3 Anfragen
        auffrischen += [z[0] for z in kat.execute(
            "SELECT scryfall_id FROM druck WHERE set_code = ? AND erschienen >= ?",
            (SECRET_LAIR, (heute - timedelta(days=SECRET_LAIR_TAGE)).isoformat()))]
        if neuheiten:
            auffrischen += _ohne_fertigen_scan(kat, grenze)
            if saat:
                saat_drucke, saat_sprach = _saat(kat, grenze)
                auffrischen += saat_drucke
                neu_sprache += saat_sprach
    neu_sprache = list(dict.fromkeys(neu_sprache))
    fuer_druck = list(dict.fromkeys(neu_default + auffrischen))
    zahl = {"drucke_neu": 0, "drucke_aufgefrischt": 0, "sprachdrucke_neu": 0, "saetze_neu": 0, "namen_neu": 0}
    if neuheiten:
        zahl["neuheiten"] = 0
    tag = heute.isoformat()

    def arbeit(kat, art, karten):
        teil = schreiben(kat, karten if art == "druck" else [], karten if art == "sprache" else [], bilder)
        if neuheiten:
            teil["neuheiten"] = neuheiten_merken(kat, karten, art, tag)
        return teil

    # in Blöcken holen und schreiben: Drucke zuerst (Sprachdrucke brauchen ihre Oracle-ID für die Namenssuche)
    for art, ids in (("druck", fuer_druck), ("sprache", neu_sprache)):
        for anfang in range(0, len(ids), BLOCK):
            karten = scryfall.karten(ids[anfang:anfang + BLOCK])
            teil = _transaktion(pfad, lambda kat: arbeit(kat, art, karten))
            for k, v in teil.items():
                zahl[k] += v
    zahl["anfragen"] = scryfall.anfragen
    zahl["sekunden"] = round(time.monotonic() - start, 1)

    def abschluss(kat):
        meta_schreiben(kat, zahl)
        if saat:
            kat.execute("INSERT OR REPLACE INTO meta (schluessel, wert) VALUES ('neuheit_saat', 'fertig')")

    _transaktion(pfad, abschluss)
    return zahl


def kurz(zahl: dict) -> str:
    return (f"{zahl.get('drucke_neu', 0)} neue Drucke, {zahl.get('sprachdrucke_neu', 0)} neue Sprachdrucke, "
            f"{zahl.get('drucke_aufgefrischt', 0)} aufgefrischt, {zahl.get('saetze_neu', 0)} neue Sets "
            f"({zahl.get('anfragen', 0)} Anfragen, {zahl.get('sekunden', 0)} s)")


# ----------------------------------------------------------------- Add-on


def nach_abgleich_app(datenbank: Path, katalog: Path) -> None:
    """Im Add-on nach einem Abgleich: fehlende Kartendaten aus dem Katalog nachtragen und den Preisabruf anstoßen
    (Cardmarket-Produkte neuer Drucke, Preise)."""
    from binderapp.db.datenbank import schreiben as db_schreiben
    from binderapp.db.datenbank import verbinden, version
    from binderapp.katalog import kartendaten
    from binderapp.preise import zeitplan
    if Path(datenbank).is_file():
        with closing(verbinden(datenbank)) as con:
            if version(con) >= 3:
                with closing(verbinden(katalog, nur_lesen=True)) as kat, db_schreiben(con):
                    kartendaten.uebernehmen(con, kat)
    zeitplan.anstossen()


class Katalogabgleich(threading.Thread):
    """Add-on: schaut stündlich nach und gleicht ab, wenn der letzte Abgleich älter als 20 Stunden ist (oder der
    PC gerade einen Katalog ohne Abgleich geschickt hat). Fehler landen im Protokoll, der nächste Blick versucht
    es wieder."""

    def __init__(self, katalog: Path, datenbank: Path | None = None):
        super().__init__(daemon=True, name="katalogabgleich")
        self.katalog, self.datenbank = Path(katalog), datenbank
        self.halt = threading.Event()

    def anhalten(self) -> None:
        self.halt.set()

    def einmal(self) -> dict | None:
        if not faellig(self.katalog) and not saat_offen(self.katalog):
            return None
        zahl = abgleichen(self.katalog, neuheiten=True)
        log.info("Scryfall-Abgleich: %s, %s Neuheiten für den PC", kurz(zahl), zahl.get("neuheiten", 0))
        if self.datenbank is not None:
            nach_abgleich_app(self.datenbank, self.katalog)
        return zahl

    def run(self) -> None:
        self.halt.wait(60)   # erst den Serverstart abwarten
        while not self.halt.is_set():
            pause = TAKT
            try:
                self.einmal()
            except Exception as fehler:   # nie den Server stören; nach einem Fehler länger warten
                log.warning("Scryfall-Abgleich fehlgeschlagen (nächster Versuch in %s Stunden): %s",
                            NACH_FEHLER // 3600, fehler)
                pause = NACH_FEHLER
            self.halt.wait(pause)
