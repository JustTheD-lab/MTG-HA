"""Sortierregeln und Fächer lesen und schreiben, Karten aus dem Scan-Eingang einsortieren, Ziel und Fach anzeigen.

Ablauf beim Bulk-Scan (Entscheidung des Nutzers, 01.11.2026: sofort nach der Erkennung verschieben):
- `scan_ergebnis_schreiben` markiert die Karte mit `sortieren = 'offen'`, wenn der Durchgang einsortieren soll.
- Nach den Kartendaten ruft der Server `nach_erkennung` auf, nach dem Preisnachtrag `offene_einsortieren`.
- Eine offene Karte im Scan-Eingang wird nur einsortiert, wenn sie „sicher“ oder geprüft ist. Die erste passende
  aktive Regel bestimmt die Kiste. Ist eine Regel davor unbekannt (Preis oder Kartendaten fehlen), bleibt die Karte
  offen und wartet; passt keine Regel, bleibt sie im Scan-Eingang und ist nicht mehr offen.
- Das Verschieben ist ein eigener Bedienschritt (Quelle „sortierregel“, `db/schritte.py`) und lässt sich exakt
  zurücknehmen. Gleiche sichere, ungeprüfte Drucke werden in der Zielkiste zu einem Posten zusammengefasst.
- Nach Bestätigen oder Druck ändern in der Prüfansicht gehört das Verschieben zum Schritt des Nutzers (ohne
  Zusammenfassen), damit sein Rückgängig alles zurücknimmt.
"""
from __future__ import annotations

import json
import logging

from binderapp.db import schritte
from binderapp.db.datenbank import aendern, einfuegen, loeschen, version
from binderapp.db.erkennung import SCAN_MARKE
from binderapp.db.seiten import Fehler
from binderapp.preise.werte import neuester_tag
from binderapp.sortieren import regeln as r

AB_VERSION = 6
QUELLE = schritte.QUELLE_REGEL
QUELLE_NUTZER = "nutzer"
HOECHSTENS_REGELN = 100
HOECHSTENS_FAECHER = 30
HOECHSTENS_WERTE_GESAMT = 5000   # Parameter aller Regeln zusammen (SQLite-Grenze 32766, dazu bis 400 Karten-IDs)
NAME_LAENGE = 60

log = logging.getLogger("binderapp.sortieren")

GRUND_PRUEFEN = "Erst prüfen"
GRUND_KEINE = "Keine Regel passt"


def aktiv(con) -> bool:
    return version(con) >= AB_VERSION


def scan_kiste_id(con, nutzer: int) -> int | None:
    z = con.execute("SELECT id FROM kiste WHERE nutzer_id = ? AND extern_name = ?", (nutzer, SCAN_MARKE)).fetchone()
    return z[0] if z else None


# ---------------------------------------------------------------- Lesen

def _regel_dict(z, kisten: dict) -> dict:
    try:
        bedingungen, gueltig = r.laden(z["bedingungen"]), True
    except (Fehler, ValueError):
        bedingungen, gueltig = [], False
    return {"id": z["id"], "platz": z["platz"], "name": z["name"], "aktiv": bool(z["aktiv"]),
            "bedingungen": bedingungen, "texte": [r.text(b) for b in bedingungen], "gueltig": gueltig,
            "kiste_id": z["kiste_id"], "binder_id": z["binder_id"],
            "ziel": kisten.get(z["kiste_id"]) if z["kiste_id"] else None}


def _kistennamen(con, nutzer: int) -> dict:
    return {z[0]: z[1] for z in con.execute("SELECT id, name FROM kiste WHERE nutzer_id = ?", (nutzer,))}


def regeln_lesen(con, nutzer: int) -> list[dict]:
    if not aktiv(con):
        return []
    kisten = _kistennamen(con, nutzer)
    return [_regel_dict(z, kisten) for z in con.execute(
        "SELECT * FROM sortierregel WHERE nutzer_id = ? ORDER BY platz, id", (nutzer,))]


def faecher_lesen(con, nutzer: int, kiste_id: int) -> list[dict]:
    if not aktiv(con):
        return []
    aus = []
    for z in con.execute("SELECT * FROM sortierfach WHERE nutzer_id = ? AND kiste_id = ? ORDER BY platz, id",
                         (nutzer, kiste_id)):
        try:
            bedingungen = r.laden(z["bedingungen"])
        except (Fehler, ValueError):
            bedingungen = None
        aus.append({"id": z["id"], "name": z["name"], "bedingungen": bedingungen or [],
                    "texte": [r.text(b) for b in bedingungen or []], "gueltig": bedingungen is not None})
    return aus


# ---------------------------------------------------------------- Schreiben (mit Historie, Quelle „nutzer“)

def _name(wert, was: str) -> str:
    if not isinstance(wert, str) or not wert.strip() or len(wert.strip()) > NAME_LAENGE:
        raise Fehler(f"{was}: Name mit 1 bis {NAME_LAENGE} Zeichen.")
    return wert.strip()


def _kiste_pruefen(con, nutzer: int, kiste_id) -> int:
    if not isinstance(kiste_id, int) or isinstance(kiste_id, bool):
        raise Fehler("Bitte eine Zielkiste wählen.")
    z = con.execute("SELECT extern_name FROM kiste WHERE id = ? AND nutzer_id = ?", (kiste_id, nutzer)).fetchone()
    if z is None:
        raise Fehler("Diese Kiste gibt es nicht.", 404)
    if z[0] == SCAN_MARKE:
        raise Fehler("Der Scan-Eingang kann kein Ziel einer Regel sein.")
    return kiste_id


def _regel_felder(con, nutzer: int, d: dict) -> dict:
    if not isinstance(d, dict):
        raise Fehler("Ungültige Regel.")
    if d.get("binder_id") is not None:
        raise Fehler("Binder als Ziel kommen mit der nächsten Teilrunde.")
    felder = {"name": _name(d.get("name"), "Regel"),
              "aktiv": 0 if d.get("aktiv") is False else 1,
              "bedingungen": json.dumps(r.bedingungen_pruefen(d.get("bedingungen")), ensure_ascii=False),
              "kiste_id": _kiste_pruefen(con, nutzer, d.get("kiste_id"))}
    return felder


def _werte_zaehlen(bedingungen_json: str) -> int:
    return sum(len(r.bedingung_sql(b)[1]) for b in json.loads(bedingungen_json))


def _grenze_pruefen(con, nutzer: int, neu: str, ausser: int | None = None) -> None:
    """Alle Regeln zusammen werden in einer Abfrage ausgewertet; SQLite erlaubt nur begrenzt viele Parameter."""
    summe = _werte_zaehlen(neu)
    for z in con.execute("SELECT id, bedingungen FROM sortierregel WHERE nutzer_id = ?", (nutzer,)):
        if z[0] != ausser:
            try:
                summe += _werte_zaehlen(z[1])
            except (Fehler, ValueError, KeyError):
                pass
    if summe > HOECHSTENS_WERTE_GESAMT:
        raise Fehler(f"Zu viele Werte in allen Regeln zusammen (höchstens {HOECHSTENS_WERTE_GESAMT}).")


def regel_anlegen(con, nutzer: int, d: dict) -> int:
    if con.execute("SELECT COUNT(*) FROM sortierregel WHERE nutzer_id = ?", (nutzer,)).fetchone()[0] >= HOECHSTENS_REGELN:
        raise Fehler(f"Höchstens {HOECHSTENS_REGELN} Regeln.")
    felder = _regel_felder(con, nutzer, d)
    _grenze_pruefen(con, nutzer, felder["bedingungen"])
    platz = con.execute("SELECT COALESCE(MAX(platz), 0) + 1 FROM sortierregel WHERE nutzer_id = ?", (nutzer,)).fetchone()[0]
    return einfuegen(con, nutzer, QUELLE_NUTZER, "sortierregel", {"nutzer_id": nutzer, "platz": platz, **felder})


def _regel_holen(con, nutzer: int, regel_id: int):
    z = con.execute("SELECT * FROM sortierregel WHERE id = ? AND nutzer_id = ?", (regel_id, nutzer)).fetchone()
    if z is None:
        raise Fehler("Diese Regel gibt es nicht.", 404)
    return z


def regel_aendern(con, nutzer: int, regel_id: int, d: dict) -> None:
    _regel_holen(con, nutzer, regel_id)
    felder = _regel_felder(con, nutzer, d)
    _grenze_pruefen(con, nutzer, felder["bedingungen"], ausser=regel_id)
    aendern(con, nutzer, QUELLE_NUTZER, "sortierregel", regel_id, felder)


def regel_loeschen(con, nutzer: int, regel_id: int) -> None:
    _regel_holen(con, nutzer, regel_id)
    loeschen(con, nutzer, QUELLE_NUTZER, "sortierregel", regel_id)


def reihenfolge(con, nutzer: int, ids) -> None:
    """Neue Reihenfolge: `ids` muss genau alle Regeln des Nutzers enthalten."""
    vorhanden = [z[0] for z in con.execute("SELECT id FROM sortierregel WHERE nutzer_id = ?", (nutzer,))]
    if (not isinstance(ids, list) or any(not isinstance(i, int) or isinstance(i, bool) for i in ids)
            or sorted(ids) != sorted(vorhanden) or len(set(ids)) != len(ids)):
        raise Fehler("Die Reihenfolge passt nicht zu den Regeln (Seite neu laden).", 409)
    for platz, regel_id in enumerate(ids, 1):
        aendern(con, nutzer, QUELLE_NUTZER, "sortierregel", regel_id, {"platz": platz})


def faecher_setzen(con, nutzer: int, kiste_id: int, liste) -> None:
    """Ersetzt die Fächer einer Kiste durch `liste` ([{name, bedingungen}], Reihenfolge = Vorrang)."""
    if con.execute("SELECT 1 FROM kiste WHERE id = ? AND nutzer_id = ?", (kiste_id, nutzer)).fetchone() is None:
        raise Fehler("Diese Kiste gibt es nicht.", 404)
    if not isinstance(liste, list) or len(liste) > HOECHSTENS_FAECHER:
        raise Fehler(f"Höchstens {HOECHSTENS_FAECHER} Fächer.")
    neu = []
    for f in liste:
        if not isinstance(f, dict):
            raise Fehler("Ungültiges Fach.")
        neu.append((_name(f.get("name"), "Fach"),
                    json.dumps(r.bedingungen_pruefen(f.get("bedingungen")), ensure_ascii=False)))
    if len({n for n, _ in neu}) != len(neu):
        raise Fehler("Zwei Fächer heißen gleich.")
    alt = con.execute("SELECT id, name, bedingungen FROM sortierfach WHERE nutzer_id = ? AND kiste_id = ? "
                      "ORDER BY platz, id", (nutzer, kiste_id)).fetchall()
    # Gleiche Zeilen bleiben (keine Historie ohne Änderung); sonst ändern, überzählige löschen, fehlende anlegen
    for platz, (name, bedingungen) in enumerate(neu, 1):
        if platz <= len(alt):
            aendern(con, nutzer, QUELLE_NUTZER, "sortierfach", alt[platz - 1]["id"],
                    {"name": name, "bedingungen": bedingungen, "platz": platz})
        else:
            einfuegen(con, nutzer, QUELLE_NUTZER, "sortierfach", {"nutzer_id": nutzer, "kiste_id": kiste_id,
                                                                  "platz": platz, "name": name, "bedingungen": bedingungen})
    for z in alt[len(neu):]:
        loeschen(con, nutzer, QUELLE_NUTZER, "sortierfach", z["id"])


def kiste_benutzt(con, nutzer: int, kiste_id: int) -> list[str]:
    """Namen der Regeln, die in die Kiste sortieren (eine solche Kiste lässt sich nicht löschen)."""
    if not aktiv(con):
        return []
    return [z[0] for z in con.execute("SELECT name FROM sortierregel WHERE nutzer_id = ? AND kiste_id = ? ORDER BY platz",
                                      (nutzer, kiste_id))]


def faecher_entfernen(con, nutzer: int, kiste_id: int) -> None:
    """Vor dem Löschen einer Kiste: ihre Fächer mit Historie entfernen."""
    if aktiv(con):
        for z in con.execute("SELECT id FROM sortierfach WHERE nutzer_id = ? AND kiste_id = ?", (nutzer, kiste_id)).fetchall():
            loeschen(con, nutzer, QUELLE_NUTZER, "sortierfach", z[0])


# ---------------------------------------------------------------- Auswerten

_VON = ("FROM karte k LEFT JOIN kartendaten d ON d.scryfall_id = k.scryfall_id "
        "LEFT JOIN preis p ON p.cardmarket_id = d.cardmarket_id AND p.tag = ? "
        "LEFT JOIN preisabruf pa ON pa.tag = ?")


def _werte(con, karten_ids: list[int], listen: list[list[dict]]) -> dict[int, list[list]]:
    """{karte_id: [[Wert je Bedingung] je Liste]} mit 1, 0 oder None (unbekannt)."""
    ausdruecke, p = [], []
    for bedingungen in listen:
        for b in bedingungen:
            s, q = r.bedingung_sql(b)
            ausdruecke.append(s)
            p += q
    tag = neuester_tag(con)
    aus: dict[int, list[list]] = {}
    for anfang in range(0, len(karten_ids), 400):
        teil = karten_ids[anfang:anfang + 400]
        spalten = ", ".join(["k.id"] + ausdruecke)
        sql = f"SELECT {spalten} {_VON} WHERE k.id IN ({', '.join('?' for _ in teil)})"
        for z in con.execute(sql, (*p, tag, tag, *teil)):
            werte, i = [], 1
            for bedingungen in listen:
                werte.append([None if z[i + j] is None else int(bool(z[i + j])) for j in range(len(bedingungen))])
                i += len(bedingungen)
            aus[z[0]] = werte
    return aus


def _erste(listen: list[list[dict]], werte: list[list]) -> tuple[int | None, list[str]]:
    """Index der ersten zutreffenden Liste. Steht davor eine unbekannte, kommt (None, fehlende Werte)."""
    for i, (bedingungen, w) in enumerate(zip(listen, werte)):
        ergebnis = r.und(w)
        if ergebnis == 1:
            return i, []
        if ergebnis is None:
            fehlt = sorted({r.FEHLT.get(b["feld"], "Kartendaten") for b, x in zip(bedingungen, w) if x is None})
            return None, fehlt
    return None, []


def _aktive_regeln(con, nutzer: int) -> list[dict]:
    return [g for g in regeln_lesen(con, nutzer) if g["aktiv"] and g["gueltig"] and g["kiste_id"]]


def auswerten(con, nutzer: int, karten_ids: list[int], regeln_liste: list[dict] | None = None) -> dict[int, dict]:
    """{karte_id: {"regel": Regel oder None, "fehlt": [...]}} für die Karten (ohne Rücksicht auf ihren Status)."""
    regeln_liste = _aktive_regeln(con, nutzer) if regeln_liste is None else regeln_liste
    listen = [g["bedingungen"] for g in regeln_liste]
    aus = {}
    for kid, werte in _werte(con, list(karten_ids), listen).items():
        i, fehlt = _erste(listen, werte)
        aus[kid] = {"regel": regeln_liste[i] if i is not None else None, "fehlt": fehlt}
    return aus


def faecher_von(con, nutzer: int, kiste_id: int, karten_ids: list[int]) -> dict[int, str | None]:
    """Fach je Karte in einer Kiste (None: kein Fach passt, Fächer fehlen oder ein Wert ist unbekannt)."""
    faecher = [f for f in faecher_lesen(con, nutzer, kiste_id) if f["gueltig"]]
    if not faecher or not karten_ids:
        return {k: None for k in karten_ids}
    listen = [f["bedingungen"] for f in faecher]
    aus = {}
    for kid, werte in _werte(con, list(karten_ids), listen).items():
        i, _ = _erste(listen, werte)
        aus[kid] = faecher[i]["name"] if i is not None else None
    return aus


# ---------------------------------------------------------------- Einsortieren

def _sortierbar(k) -> bool:
    return k["status"] == "sicher" or bool(k["geprueft"])


def _verschieben(con, nutzer: int, k, ziel: int) -> tuple[str, int]:
    """Eigener Schritt (Quelle „sortierregel“): zusammenfassen oder verschieben. Gibt (Schritt, Karte) zurück."""
    schritt = schritte.neuer_schritt()
    if k["status"] == "sicher" and not k["geprueft"] and not k["foil"] and k["scryfall_id"] and k["quelle"] == "scan":
        z = con.execute(
            "SELECT id, anzahl FROM karte WHERE nutzer_id = ? AND kiste_id = ? AND quelle = 'scan' AND scryfall_id = ?"
            " AND COALESCE(sprache, '') = ? AND foil = 0 AND status = 'sicher' AND geprueft = 0 AND id <> ?"
            " ORDER BY id LIMIT 1", (nutzer, ziel, k["scryfall_id"], k["sprache"] or "", k["id"])).fetchone()
        if z is not None:
            schritte.aendern_genau(con, nutzer, "karte", z["id"], {"anzahl": z["anzahl"] + k["anzahl"]},
                                   schritt=schritt, quelle=QUELLE)
            for f in con.execute("SELECT id FROM scan_foto WHERE karte_id = ?", (k["id"],)).fetchall():
                schritte.aendern_genau(con, nutzer, "scan_foto", f[0], {"karte_id": z["id"]}, schritt=schritt,
                                       quelle=QUELLE)
            schritte.loeschen(con, nutzer, "karte", k["id"], schritt=schritt, quelle=QUELLE)
            return schritt, z["id"]
    schritte.aendern_genau(con, nutzer, "karte", k["id"], {"kiste_id": ziel}, schritt=schritt, quelle=QUELLE)
    return schritt, k["id"]


def einsortieren(con, nutzer: int, karten_ids: list[int] | None = None, *, schritt: str | None = None) -> list[dict]:
    """Offene Karten des Nutzers (oder nur `karten_ids`) aus dem Scan-Eingang einsortieren, in der Transaktion des
    Aufrufers. Mit `schritt` (Prüfansicht) gehört alles zum Schritt des Nutzers. Gibt die verschobenen Karten an."""
    if not aktiv(con):
        return []
    eingang = scan_kiste_id(con, nutzer)
    sql = "SELECT * FROM karte WHERE nutzer_id = ? AND sortieren = 'offen'"
    p: list = [nutzer]
    if karten_ids is not None:
        ids = [int(i) for i in karten_ids]
        if not ids:
            return []
        sql += f" AND id IN ({', '.join('?' for _ in ids)})"
        p += ids
    offen = con.execute(sql + " ORDER BY id", p).fetchall()
    kandidaten = []
    for k in offen:
        if eingang is None or k["kiste_id"] != eingang:   # inzwischen woanders: nicht mehr offen
            _offen_beenden(con, nutzer, k["id"], schritt)
        elif _sortierbar(k):
            kandidaten.append(k)
    if not kandidaten:
        return []
    regeln_liste = _aktive_regeln(con, nutzer)
    ergebnis = auswerten(con, nutzer, [k["id"] for k in kandidaten], regeln_liste)
    verschoben = []
    for k in kandidaten:
        e = ergebnis.get(k["id"], {"regel": None, "fehlt": ["Kartendaten"]})
        if e["fehlt"]:
            continue   # wartet (z. B. auf den Preis)
        # Prüfansicht: Eine „sichere“ Karte wäre nach dem Rückgängig sofort wieder einsortierbar; damit das Rückgängig
        # sie nicht von selbst wieder wegräumt, endet ihr Warten außerhalb des Nutzerschritts.
        _offen_beenden(con, nutzer, k["id"], schritt if k["status"] != "sicher" else None)
        regel = e["regel"]
        if regel is None:
            continue   # bleibt im Scan-Eingang
        if schritt is None:
            s, ziel_karte = _verschieben(con, nutzer, k, regel["kiste_id"])
        else:
            aendern(con, nutzer, QUELLE_NUTZER, "karte", k["id"], {"kiste_id": regel["kiste_id"]}, schritt=schritt)
            s, ziel_karte = schritt, k["id"]
        verschoben.append({"karte_id": k["id"], "nach": ziel_karte, "kiste_id": regel["kiste_id"],
                           "kiste": regel["ziel"], "regel": regel["name"], "schritt": s})
    return verschoben


def _offen_beenden(con, nutzer: int, karte_id: int, schritt: str | None) -> None:
    if schritt is None:
        aendern(con, nutzer, QUELLE, "karte", karte_id, {"sortieren": None})
    else:
        aendern(con, nutzer, QUELLE_NUTZER, "karte", karte_id, {"sortieren": None}, schritt=schritt)


def nach_erkennung(con, nutzer: int) -> list[dict]:
    """Nach dem Schreiben eines Scan-Ergebnisses (und der Kartendaten): offene Karten einsortieren. Ein Fehler hier
    darf das Ergebnis nicht kosten, deshalb in einem Sicherungspunkt."""
    if not aktiv(con):
        return []
    con.execute("SAVEPOINT einsortieren")
    try:
        aus = einsortieren(con, nutzer)
    except Exception as f:   # die Karten bleiben offen im Scan-Eingang; der nächste Lauf versucht es wieder
        con.execute("ROLLBACK TO einsortieren")
        con.execute("RELEASE einsortieren")
        log.warning("Einsortieren fehlgeschlagen: %s", f)
        return []
    con.execute("RELEASE einsortieren")
    return aus


def offene_nutzer(con) -> list[int]:
    if not aktiv(con):
        return []
    return [z[0] for z in con.execute("SELECT DISTINCT nutzer_id FROM karte WHERE sortieren = 'offen'")]


# ---------------------------------------------------------------- Anzeige

def stand(con, nutzer: int, karten: list) -> dict[int, dict]:
    """Ziel oder Wartegrund je Karte für den Scan-Eingang: {karte_id: {"kiste", "kiste_id", "fach"} oder
    {"wartet": Grund}}. `karten` sind Zeilen mit id, kiste_id, sortieren, status, geprueft."""
    if not aktiv(con) or not karten:
        return {}
    eingang = scan_kiste_id(con, nutzer)
    aus: dict[int, dict] = {}
    namen = _kistennamen(con, nutzer)
    woanders: dict[int, list[int]] = {}
    offen = []
    for k in karten:
        if k["kiste_id"] is not None and k["kiste_id"] != eingang:
            woanders.setdefault(k["kiste_id"], []).append(k["id"])
        elif k["sortieren"] == "offen":
            if _sortierbar(k):
                offen.append(k["id"])
            else:
                aus[k["id"]] = {"wartet": GRUND_PRUEFEN}
    for kiste_id, ids in woanders.items():
        for kid, fach in faecher_von(con, nutzer, kiste_id, ids).items():
            aus[kid] = {"kiste_id": kiste_id, "kiste": namen.get(kiste_id), "fach": fach}
    if offen:
        for kid, e in auswerten(con, nutzer, offen).items():
            if e["fehlt"]:
                aus[kid] = {"wartet": " und ".join(e["fehlt"]) + " fehlt"}
            elif e["regel"] is None:
                aus[kid] = {"wartet": GRUND_KEINE}
            else:
                aus[kid] = {"wartet": "Gleich: " + (e["regel"]["ziel"] or "?")}
    return aus


def probe(con, nutzer: int, karte_id: int) -> dict:
    """Welche Regel greift für eine Karte der Sammlung, und warum (jede Bedingung jeder Regel bis zur greifenden)."""
    k = con.execute("SELECT id, kiste_id FROM karte WHERE id = ? AND nutzer_id = ?", (karte_id, nutzer)).fetchone()
    if k is None:
        raise Fehler("Diese Karte gibt es nicht.", 404)
    regeln_liste = [g for g in regeln_lesen(con, nutzer) if g["gueltig"] and g["kiste_id"]]
    listen = [g["bedingungen"] for g in regeln_liste]
    werte = _werte(con, [karte_id], listen).get(karte_id, [[None] * len(x) for x in listen])
    schritte_aus, treffer, wartet = [], None, []
    for g, w in zip(regeln_liste, werte):
        ergebnis = r.und(w) if g["aktiv"] else 0
        schritte_aus.append({"regel": g["name"], "aktiv": g["aktiv"], "ziel": g["ziel"],
                             "ergebnis": {1: "passt", 0: "passt nicht", None: "unbekannt"}[ergebnis],
                             "bedingungen": [{"text": t, "ergebnis": {1: "ja", 0: "nein", None: "unbekannt"}[x]}
                                             for t, x in zip(g["texte"], w)]})
        if g["aktiv"] and ergebnis is None and treffer is None and not wartet:
            wartet = sorted({r.FEHLT.get(b["feld"], "Kartendaten") for b, x in zip(g["bedingungen"], w) if x is None})
        if ergebnis == 1 and not wartet:
            treffer = g
            break
    fach = faecher_von(con, nutzer, treffer["kiste_id"], [karte_id]).get(karte_id) if treffer else None
    return {"karte_id": karte_id, "regeln": schritte_aus,
            "ziel": {"kiste_id": treffer["kiste_id"], "kiste": treffer["ziel"], "fach": fach} if treffer else None,
            "wartet": (" und ".join(wartet) + " fehlt") if wartet else None}
