"""„Einräumen zurücknehmen“ im Scan-Eingang (Paket 2 der Arbeitsliste, 10.11.2026).

Jeder automatische Einsortier-Lauf (`einsortieren.einsortieren` ohne Schritt des Nutzers, also nach der Erkennung oder
nach dem Preisabruf) steht als Protokollzeile in der Historie (`einsortieren.LAUF_TABELLE`). Die App zeigt die letzten
zehn Läufe mit Zeit und Kartenzahl; jeder lässt sich einzeln zurücknehmen.

Antworten des Nutzers (10.11.2026): Karten, die seit dem Einräumen von Hand bewegt oder geändert wurden, bleiben, wo sie
sind; der Rest kommt zurück in den Scan-Eingang, und die App nennt die Karten, die geblieben sind (Hinweis statt Abbruch).

Zurückgenommen wird je Karte des Laufs, in umgekehrter Reihenfolge, gemessen an der Zielkarte (bei zusammengefassten
Posten der Posten in der Zielkiste):
- Zielkarte fehlt, liegt nicht mehr in der Zielkiste oder hat seit dem Lauf eine Änderung, die weder von einem
  automatischen Lauf noch vom Zurücknehmen eines Laufs stammt: Die Karte bleibt (Hinweis).
- Zielkarte hat genau die Anzahl der Karte: Sie geht als Ganzes zurück in den Scan-Eingang.
- Zielkarte hat mehr (zusammengefasst): Die Anzahl wird getrennt. Die eingeräumte Karte entsteht wieder im Scan-Eingang
  (bei einem zusammengefassten Posten mit ihren alten Feldern) und bekommt ihre Scan-Fotos zurück.
Alles ist ein Schritt des Nutzers mit `rueckgaengig_von` = Kennung des Laufs; die Summe der Karten bleibt gleich.
"""
from __future__ import annotations

import json

from binderapp.db import schritte
from binderapp.db.datenbank import _protokoll
from binderapp.db.seiten import Fehler
from binderapp.sortieren import einsortieren

ANZAHL = 10
TABELLE = einsortieren.LAUF_TABELLE
GRUND_FEHLT = "nicht mehr vorhanden"
GRUND_HAND = "seit dem Einräumen von Hand bewegt oder geändert"

# Änderungen an einer Karte nach dem Lauf, die nicht von einem automatischen Lauf und nicht vom Zurücknehmen eines
# Laufs stammen, gelten als „von Hand“ (auch „Jetzt einsortieren“, Prüfen, Verschieben, Abzug, Deckbau).
SQL_VON_HAND = """
SELECT 1 FROM historie h
WHERE h.tabelle = 'karte' AND h.datensatz = ? AND h.id > ? AND h.quelle <> ?
  AND NOT (h.rueckgaengig_von IS NOT NULL AND EXISTS (
        SELECT 1 FROM historie l WHERE l.tabelle = ? AND l.schritt = h.rueckgaengig_von))
LIMIT 1
"""


def _zurueck_zeile(con, kennung: str):
    return con.execute("SELECT neu FROM historie WHERE tabelle = ? AND rueckgaengig_von = ? ORDER BY id LIMIT 1",
                       (TABELLE, kennung)).fetchone()


def laeufe(con, nutzer: int, anzahl: int = ANZAHL) -> list[dict]:
    """Die letzten automatischen Läufe, neueste zuerst."""
    if not einsortieren.aktiv(con):
        return []
    namen = einsortieren._kistennamen(con, nutzer)
    zeilen = con.execute(
        "SELECT zeitpunkt, schritt, neu FROM historie WHERE tabelle = ? AND nutzer_id = ? AND aktion = 'angelegt'"
        " AND quelle = ? ORDER BY id DESC LIMIT ?", (TABELLE, nutzer, einsortieren.QUELLE, int(anzahl))).fetchall()
    liste = []
    for z in zeilen:
        meta = json.loads(z["neu"] or "{}")
        kisten: dict[int, int] = {}
        for e in meta.get("karten_liste", []):
            kisten[e["z"]] = kisten.get(e["z"], 0) + e["n"]
        zurueck = _zurueck_zeile(con, z["schritt"])
        erg = json.loads(zurueck["neu"] or "{}") if zurueck else None
        liste.append({
            "lauf": z["schritt"], "zeitpunkt": z["zeitpunkt"], "posten": meta.get("posten", 0),
            "karten": meta.get("karten", 0),
            "kisten": [{"kiste_id": k, "kiste": namen.get(k, "(gelöschte Kiste)"), "karten": n} for k, n in kisten.items()],
            "zurueckgenommen": erg is not None,
            "ergebnis": {"zurueck": erg.get("zurueck", 0), "geblieben": erg.get("geblieben", [])} if erg else None,
        })
    return liste


def _von_hand(con, karte_id: int, ab: int) -> bool:
    return con.execute(SQL_VON_HAND, (karte_id, ab, einsortieren.QUELLE, TABELLE)).fetchone() is not None


def _alte_karte(con, e: dict) -> dict | None:
    """Felder der eingeräumten Karte, wenn sie beim Lauf in einen Posten zusammengefasst (gelöscht) wurde."""
    z = con.execute("SELECT alt FROM historie WHERE schritt = ? AND tabelle = 'karte' AND datensatz = ?"
                    " AND aktion = 'geloescht' ORDER BY id LIMIT 1", (e["s"], e["k"])).fetchone()
    return json.loads(z["alt"]) if z and z["alt"] else None


def _eine(con, nutzer: int, e: dict, ab: int, eingang: int, neuer: str, kennung: str) -> str | None:
    """Eine Karte des Laufs zurücknehmen; gibt den Grund zurück, wenn sie bleibt."""
    t = con.execute("SELECT * FROM karte WHERE id = ? AND nutzer_id = ?", (e["t"], nutzer)).fetchone()
    if t is None:
        return GRUND_FEHLT
    if t["kiste_id"] != e["z"] or t["anzahl"] < e["n"] or _von_hand(con, t["id"], ab):
        return GRUND_HAND
    aendern = lambda tabelle, datensatz, felder: schritte.aendern_genau(   # noqa: E731
        con, nutzer, tabelle, datensatz, felder, schritt=neuer, quelle=schritte.QUELLE, rueckgaengig_von=kennung)
    if t["anzahl"] == e["n"]:
        aendern("karte", t["id"], {"kiste_id": eingang, "sortieren": None})
        return None
    # zusammengefasst: Anzahl trennen, die eingeräumte Karte entsteht wieder im Scan-Eingang
    felder = (_alte_karte(con, e) if e["t"] != e["k"] else None) or dict(t)
    felder.update({"kiste_id": eingang, "tasche_id": None, "deck_id": None, "anzahl": e["n"], "sortieren": None,
                   "geaendert": con.execute(schritte.JETZT).fetchone()[0]})
    if con.execute("SELECT 1 FROM karte WHERE id = ?", (felder.get("id"),)).fetchone():
        felder.pop("id")   # Kennung inzwischen neu vergeben (SQLite nimmt nach dem Löschen der höchsten ID dieselbe)
    spalten = {z[1] for z in con.execute("PRAGMA table_info(karte)")}
    felder = {k: v for k, v in felder.items() if k in spalten}
    neu_id = schritte.einfuegen(con, nutzer, "karte", felder, schritt=neuer, rueckgaengig_von=kennung)
    aendern("karte", t["id"], {"anzahl": t["anzahl"] - e["n"]})
    for f in e.get("f", []):
        if con.execute("SELECT 1 FROM scan_foto WHERE id = ? AND karte_id = ?", (f, t["id"])).fetchone():
            aendern("scan_foto", f, {"karte_id": neu_id})
    return None


def zuruecknehmen(con, nutzer: int, kennung: str) -> dict:
    """Einen Lauf zurücknehmen, in der Transaktion des Aufrufers."""
    if not einsortieren.aktiv(con):
        raise Fehler("Sortierregeln gibt es erst ab Datenbankversion 6.", 409)
    zeile = con.execute("SELECT id, neu FROM historie WHERE tabelle = ? AND schritt = ? AND nutzer_id = ?"
                        " AND aktion = 'angelegt' AND quelle = ?",
                        (TABELLE, kennung, nutzer, einsortieren.QUELLE)).fetchone()
    if zeile is None:
        raise Fehler("Diesen Einsortier-Lauf gibt es nicht.", 404)
    if _zurueck_zeile(con, kennung):
        raise Fehler("Dieser Lauf ist schon zurückgenommen.", 409)
    eingang = einsortieren.scan_kiste_id(con, nutzer)
    if eingang is None:
        raise Fehler("Es gibt keinen Scan-Eingang mehr.", 409)
    meta = json.loads(zeile["neu"] or "{}")
    namen = einsortieren._kistennamen(con, nutzer)
    vorher = schritte.summe(con, nutzer)
    neuer = schritte.neuer_schritt()
    zurueck, posten, geblieben = 0, 0, []
    for e in reversed(meta.get("karten_liste", [])):
        grund = _eine(con, nutzer, e, zeile["id"], eingang, neuer, kennung)
        if grund is None:
            zurueck += e["n"]
            posten += 1
        else:
            geblieben.append({"karte_id": e["t"], "name": e["name"], "anzahl": e["n"],
                              "kiste": namen.get(e["z"], "(gelöschte Kiste)"), "grund": grund})
    geblieben.reverse()
    if schritte.summe(con, nutzer)[1] != vorher[1]:
        raise Fehler("Summenprüfung fehlgeschlagen, nichts zurückgenommen.", 500)
    ergebnis = {"zurueck": zurueck, "posten": posten, "geblieben": geblieben}
    _protokoll(con, nutzer, schritte.QUELLE, TABELLE, 0, "geaendert", None, ergebnis, neuer, kennung)
    return {"schritt": neuer, "lauf": kennung, **ergebnis}
