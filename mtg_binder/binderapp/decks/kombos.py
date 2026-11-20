"""Commander-Bewertung eines Decks: Kombos über Commander Spellbook und das Zusammenführen mit Legalität und
Mindest-Bracket (Auftrag des Nutzers, 20.11.2026).

Antworten des Nutzers (20.11.2026):
- Kombos fragt die App bei Commander Spellbook (`POST /estimate-bracket`). Gesendet werden **nur Kartennamen und
  Anzahl** (Commander getrennt), nichts sonst; im Add-on und in der lokalen Version.
- Gefragt wird automatisch, wenn sich die Liste geändert hat (Anlegen, Neu laden, erster Blick auf ein Deck); die
  Antwort wird je Deckstand (`stand`, Prüfsumme der Liste) in `deck_kombo` gemerkt, ohne Historie (wie Marktdaten).
  Ohne Netz bleibt der letzte Stand mit Datum.

Gefragt wird im Hintergrund (eigener Thread je Deck, höchstens einer gleichzeitig je Deck); nach einem Fehler erst
wieder nach `NEU_VERSUCH` Sekunden. Weiterleitungen werden nicht verfolgt, die Antwort ist begrenzt.
Die Schnittstelle (geprüft am 20.11.2026, ohne Schlüssel): Anfrage `{"commanders": [{"card", "quantity"}],
"main": [...]}`, Antwort `bracketTag` (E, C, O, P, S, R, B) und je Kombo `combo.uses[].card.name`, `relevant`,
`borderlineRelevant`, `definitelyTwoCard`, `arguablyTwoCard`, `speed` (1–6, aus dem nötigen Mana), `extraTurn`,
`massLandDenial`, `lock`, `controlAllOpponents`. Die Umrechnung der Gruppen in Brackets steht im Backend (MIT).
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from binderapp.decks import bewertung

log = logging.getLogger("binderapp.decks")

URL = "https://backend.commanderspellbook.com/estimate-bracket"
KOPF = {"User-Agent": "MTG-Binder-Privat/1.0", "Accept": "application/json", "Content-Type": "application/json"}
ZEITLIMIT = 30
HOECHSTENS_ANTWORT = 8 * 1024 * 1024
HOECHSTENS_KOMBOS = 60
NEU_VERSUCH = 15 * 60
AB_VERSION = 24
# Gruppen von Commander Spellbook und ihr Bracket (Backend, spellbook/models/variant.py)
SPELLBOOK_BRACKET = {"E": 1, "C": 2, "O": 2, "P": 3, "S": 3, "R": 4}
SPELLBOOK_NAME = {"E": "Exhibition", "C": "Core", "O": "Oddball", "P": "Powerful", "S": "Spicy", "R": "Ruthless",
                  "B": "Banned"}


class SpellbookFehler(RuntimeError):
    pass


class _KeineWeiterleitung(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Weiterleitung nicht erlaubt", headers, fp)


OEFFNEN = urllib.request.build_opener(_KeineWeiterleitung()).open


def stand(zeilen: list[dict]) -> str:
    """Prüfsumme der Liste (Name, Anzahl, Commander), unabhängig von der Reihenfolge."""
    teile = sorted((str(z["name"]).strip().lower(), int(z["anzahl"]), bool(z.get("commander"))) for z in zeilen)
    return hashlib.sha256(json.dumps(teile, ensure_ascii=False).encode("utf-8")).hexdigest()[:32]


def anfrage(zeilen: list[dict]) -> dict:
    """Nur Kartennamen und Anzahl, Commander getrennt."""
    je: dict[tuple[str, bool], int] = {}
    for z in zeilen:
        schluessel = (str(z["name"]).strip(), bool(z.get("commander")))
        je[schluessel] = je.get(schluessel, 0) + int(z["anzahl"])
    return {"commanders": [{"card": n, "quantity": a} for (n, c), a in je.items() if c],
            "main": [{"card": n, "quantity": a} for (n, c), a in je.items() if not c]}


def _bool(wert) -> bool:
    return wert is True


def auswerten(antwort: dict) -> dict:
    """Antwort von Commander Spellbook → `{tag, bracket, kombos}` (Kombos im Format von `bewertung`)."""
    if not isinstance(antwort, dict):
        raise SpellbookFehler("Unerwartete Antwort von Commander Spellbook.")
    tag = antwort.get("bracketTag") if antwort.get("bracketTag") in SPELLBOOK_NAME else None
    kombos = []
    for k in antwort.get("combos") or []:
        if not isinstance(k, dict) or not isinstance(k.get("combo"), dict):
            continue
        v = k["combo"]
        karten = [u["card"]["name"] for u in v.get("uses") or []
                  if isinstance(u, dict) and isinstance(u.get("card"), dict) and isinstance(u["card"].get("name"), str)]
        ergebnis = [p["feature"]["name"] for p in v.get("produces") or []
                    if isinstance(p, dict) and isinstance(p.get("feature"), dict)
                    and isinstance(p["feature"].get("name"), str)][:3]
        tempo = k.get("speed") if isinstance(k.get("speed"), int) and not isinstance(k.get("speed"), bool) else 0
        kombos.append({
            "id": str(v.get("id") or "")[:40], "karten": [n[:150] for n in karten][:10],
            "ergebnis": [e[:80] for e in ergebnis],
            "zwei_karten": _bool(k.get("definitelyTwoCard")), "fast_zwei_karten": _bool(k.get("arguablyTwoCard")),
            "relevant": _bool(k.get("relevant")), "grenzwertig": _bool(k.get("borderlineRelevant")),
            "tempo": max(0, min(6, tempo)), "extrazug": _bool(k.get("extraTurn")),
            "landzerstoerung": _bool(k.get("massLandDenial")), "sperre": _bool(k.get("lock")),
            "alle_gegner": _bool(k.get("controlAllOpponents"))})
    # die wichtigsten zuerst: relevante Zwei-Karten-Kombos, schnell vor langsam
    kombos.sort(key=lambda k: (not (k["relevant"] and k["zwei_karten"]), not k["relevant"], -k["tempo"]))
    # Kennzeichen je Karte von Spellbook ergänzen die Scryfall-Tags (z. B. in der lokalen Version ohne Tags)
    karten = {"extrazug": [], "landzerstoerung": []}
    for c in antwort.get("cards") or []:
        if isinstance(c, dict) and isinstance(c.get("card"), dict) and isinstance(c["card"].get("name"), str):
            for feld, schluessel in (("extraTurn", "extrazug"), ("massLandDenial", "landzerstoerung")):
                if c.get(feld) is True and len(karten[schluessel]) < 100:
                    karten[schluessel].append(c["card"]["name"][:150])
    return {"tag": tag, "bracket": SPELLBOOK_BRACKET.get(tag), "name": SPELLBOOK_NAME.get(tag),
            "kombos": kombos[:HOECHSTENS_KOMBOS], "kombos_gesamt": len(kombos), "karten": karten}


def abfragen(zeilen: list[dict], oeffnen=None) -> dict:
    """Fragt Commander Spellbook (wirft `SpellbookFehler`)."""
    oeffnen = oeffnen or OEFFNEN
    roh = json.dumps(anfrage(zeilen)).encode("utf-8")
    try:
        with oeffnen(urllib.request.Request(URL, data=roh, headers=KOPF, method="POST"), timeout=ZEITLIMIT) as a:
            inhalt = a.read(HOECHSTENS_ANTWORT + 1)
    except urllib.error.HTTPError as f:
        raise SpellbookFehler(f"Commander Spellbook antwortet mit {f.code}.") from f
    except (urllib.error.URLError, OSError) as f:
        raise SpellbookFehler(f"Commander Spellbook nicht erreichbar: {f}") from f
    if len(inhalt) > HOECHSTENS_ANTWORT:
        raise SpellbookFehler("Antwort von Commander Spellbook zu groß.")
    try:
        return auswerten(json.loads(inhalt))
    except ValueError as f:
        raise SpellbookFehler("Antwort von Commander Spellbook unlesbar.") from f


# ----------------------------------------------------------------- Zwischenablage je Deck


def _jetzt() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def lesen(con, deck_id: int) -> dict | None:
    z = con.execute("SELECT * FROM deck_kombo WHERE deck_id = ?", (deck_id,)).fetchone()
    return dict(z) if z else None


def merken(con, deck_id: int, stand_: str, ergebnis: dict | None, fehler: str | None) -> None:
    """Ergebnis oder Fehler festhalten (in der Transaktion des Aufrufers); ein Fehler lässt das alte Ergebnis stehen."""
    jetzt = _jetzt()
    if ergebnis is not None:
        con.execute(
            "INSERT INTO deck_kombo (deck_id, stand, geholt, ergebnis, versucht, versucht_stand, fehler) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL) ON CONFLICT(deck_id) DO UPDATE SET stand = excluded.stand, "
            "geholt = excluded.geholt, ergebnis = excluded.ergebnis, versucht = excluded.versucht, "
            "versucht_stand = excluded.versucht_stand, fehler = NULL",
            (deck_id, stand_, jetzt, json.dumps(ergebnis, ensure_ascii=False), jetzt, stand_))
    else:
        con.execute(
            "INSERT INTO deck_kombo (deck_id, versucht, versucht_stand, fehler) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(deck_id) DO UPDATE SET versucht = excluded.versucht, versucht_stand = excluded.versucht_stand, "
            "fehler = excluded.fehler", (deck_id, jetzt, stand_, (fehler or "Fehler")[:300]))


_laufend: set[int] = set()
_sperre = threading.Lock()


def laeuft(deck_id: int) -> bool:
    with _sperre:
        return deck_id in _laufend


def faellig(eintrag: dict | None, stand_: str, jetzt: float | None = None) -> bool:
    """Neu fragen, wenn kein Ergebnis zum Stand da ist und der letzte Fehler zu diesem Stand lange genug her ist."""
    if eintrag and eintrag.get("stand") == stand_ and eintrag.get("ergebnis"):
        return False
    if eintrag and eintrag.get("fehler") and eintrag.get("versucht_stand") == stand_ and eintrag.get("versucht"):
        try:
            alt = datetime.fromisoformat(eintrag["versucht"]).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            return True
        return (jetzt or time.time()) - alt >= NEU_VERSUCH
    return True


def anstossen(schreibvorgang, deck_id: int, zeilen: list[dict], oeffnen=None, warten: bool = False) -> bool:
    """Fragt im Hintergrund und merkt das Ergebnis; False, wenn für das Deck schon eine Abfrage läuft."""
    with _sperre:
        if deck_id in _laufend:
            return False
        _laufend.add(deck_id)
    stand_ = stand(zeilen)
    liste = [{"name": z["name"], "anzahl": z["anzahl"], "commander": z.get("commander")} for z in zeilen]

    def arbeit():
        try:
            try:
                ergebnis, fehler = abfragen(liste, oeffnen), None
            except SpellbookFehler as f:
                ergebnis, fehler = None, str(f)
                log.info("Commander Spellbook (Deck %s): %s", deck_id, f)
            schreibvorgang(lambda con: merken(con, deck_id, stand_, ergebnis, fehler))
        except Exception as f:   # nie den Server stören
            log.warning("Kombos für Deck %s nicht gemerkt: %s", deck_id, f)
        finally:
            with _sperre:
                _laufend.discard(deck_id)

    if warten:
        arbeit()
    else:
        threading.Thread(target=arbeit, daemon=True, name=f"kombos-{deck_id}").start()
    return True


# ----------------------------------------------------------------- Bewertung zusammenführen


def _spellbook_kennzeichen(zeilen: list[dict], regeln_: dict[str, dict], karten) -> None:
    """Extrazug und Massen-Landzerstörung laut Spellbook in die Regeln der passenden Zeilen übernehmen (nur setzen,
    nie löschen; Kopien, der Katalog bleibt unberührt)."""
    if not isinstance(karten, dict):
        return
    for schluessel in ("extrazug", "landzerstoerung"):
        namen = {str(n).lower() for n in karten.get(schluessel) or []}
        for z in zeilen:
            o = z.get("oracle_id")
            name = str(z["name"]).lower()
            if o in regeln_ and (name in namen or name.split(" // ")[0] in namen):
                regeln_[o] = {**regeln_[o], schluessel: True}


def bewerten(con, katalog_oeffnen, deck: dict, zeilen: list[dict], schreibvorgang=None) -> dict:
    """Legalität, Mindest-Bracket, Kombos und eigene Einstufung eines Decks für `GET /api/deck/{id}`.

    `katalog_oeffnen()` liefert eine Verbindung zum Katalog oder None. Mit `schreibvorgang` wird Commander Spellbook
    im Hintergrund gefragt, wenn das Ergebnis zur Liste fehlt."""
    from contextlib import closing

    from binderapp.katalog import regeln as katalog_regeln
    kat = katalog_oeffnen()
    if kat is None:
        regeln_ = {}
    else:
        with closing(kat):
            regeln_ = katalog_regeln.lesen(kat, [z.get("oracle_id") for z in zeilen])
    stand_ = stand(zeilen)
    eintrag = lesen(con, deck["id"])
    kombos, info = None, {"status": "offen", "geholt": None, "fehler": None, "spellbook": None, "liste": [],
                          "gesamt": 0}
    if eintrag and eintrag.get("ergebnis"):
        info["geholt"] = eintrag["geholt"]
    if eintrag and eintrag.get("stand") == stand_ and eintrag.get("ergebnis"):
        try:
            ergebnis = json.loads(eintrag["ergebnis"])
        except ValueError:
            ergebnis = None
        if isinstance(ergebnis, dict):
            kombos = ergebnis.get("kombos") or []
            _spellbook_kennzeichen(zeilen, regeln_, ergebnis.get("karten"))
            info.update(status="aktuell", liste=kombos, gesamt=ergebnis.get("kombos_gesamt", len(kombos)),
                        spellbook={"tag": ergebnis.get("tag"), "bracket": ergebnis.get("bracket"),
                                   "name": ergebnis.get("name")})
    elif eintrag and eintrag.get("ergebnis"):
        info["status"] = "veraltet"   # Ergebnis gehört zu einer älteren Liste
    if info["status"] != "aktuell":
        if eintrag and eintrag.get("fehler") and eintrag.get("versucht_stand") == stand_:
            info["fehler"] = eintrag["fehler"]
        if schreibvorgang is not None and zeilen and faellig(eintrag, stand_):
            anstossen(schreibvorgang, deck["id"], zeilen)
        if laeuft(deck["id"]):
            info["status"] = "laeuft"
        elif info["fehler"] and info["status"] == "offen":
            info["status"] = "fehler"
    formate = {z["id"]: regeln_[z["oracle_id"]]["formate"] for z in zeilen if z.get("oracle_id") in regeln_}
    return {
        "legalitaet": bewertung.legalitaet(zeilen, regeln_),
        "bracket": bewertung.mindest_bracket(zeilen, regeln_, kombos),
        "bracket_eigen": deck.get("bracket_eigen"),
        "kombos": info,
        "katalog": bool(regeln_),
        "formate": formate,
        "format_namen": [[f, katalog_regeln.FORMAT_NAMEN.get(f, f)] for f in katalog_regeln.FORMATE],
    }
