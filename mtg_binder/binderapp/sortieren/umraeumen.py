"""Vorhandene Kisten nach geänderten Regeln umsortieren (Paket 5, 10.11.2026).

Antworten des Nutzers (10.11.2026):
- Nach einer Regeländerung zeigt die App eine Umräumliste: welche Karten heute in eine andere Kiste gehören. Wahl:
  eine Kiste oder alle Kisten.
- Abhaken wie im Deckbau (Haken bis „Fertig“, Tabelle `umraeum_haken`, übersteht Gerätewechsel). „Fertig“ verschiebt
  die abgehakten Karten digital als ein Schritt mit Rückgängig; die echten Karten bewegt der Nutzer von Hand.
- Ausgenommen sind die Kisten „Zurückgestellt“ und der Scan-Eingang. Karten in Bindern und Decks bleiben.

Rechnung wie beim Einsortieren (`einsortieren.auswerten`): erste passende aktive Regel. Die Limit-Bedingung (Paket 4)
zählt die Exemplare vor der Karte in fester Reihenfolge (außerhalb des Eingangs nach Kennung); ein Umzug ändert diese
Reihenfolge nicht, darum bleibt das Ergebnis nach „Fertig“ stabil.

Vorsichtige Annahmen von Claude (vom Nutzer zu bestätigen):
- Nur „sichere“ oder geprüfte Karten kommen in die Liste (wie beim Einsortieren); unsichere werden nur gezählt.
- Passt keine Regel mehr, bleibt die Karte, wo sie ist (sie geht nicht zurück in den Scan-Eingang).
- Ist ein Wert unbekannt (Preis oder Kartendaten fehlen), bleibt die Karte und wird als „wartet“ gezählt.
- Fächer werden berechnet, nicht gespeichert; die Liste nennt das Zielfach, erkennt aber keinen reinen Fachwechsel
  innerhalb derselben Kiste.
- Beim Umzug wird nichts zusammengefasst (jeder Posten bleibt ein Posten).
"""
from __future__ import annotations

from binderapp.db import schritte
from binderapp.db.erkennung import SCAN_MARKE, ZURUECK_MARKE
from binderapp.db.seiten import Fehler
from binderapp.sortieren import einsortieren as es

QUELLE = es.QUELLE_NUTZER
AUSGENOMMEN = (SCAN_MARKE, ZURUECK_MARKE)
LISTE_MAX = 5000   # so viele Posten zeigt die Liste höchstens (Rest nur gezählt)


def _kisten(con, nutzer: int) -> tuple[dict[int, str], set[int]]:
    """({id: Name} aller Kisten, {IDs der ausgenommenen Kisten})."""
    namen, aus = {}, set()
    for z in con.execute("SELECT id, name, extern_name FROM kiste WHERE nutzer_id = ?", (nutzer,)):
        namen[z["id"]] = z["name"]
        if z["extern_name"] in AUSGENOMMEN:
            aus.add(z["id"])
    return namen, aus


def _kiste_pruefen(con, nutzer: int, kiste_id: int | None, ausgenommen: set[int]) -> None:
    if kiste_id is None:
        return
    z = con.execute("SELECT 1 FROM kiste WHERE id = ? AND nutzer_id = ?", (kiste_id, nutzer)).fetchone()
    if z is None:
        raise Fehler("Diese Kiste gibt es nicht.", 404)
    if kiste_id in ausgenommen:
        raise Fehler("Der Scan-Eingang und „Zurückgestellt“ werden nicht umgeräumt.")


def _rechnen(con, nutzer: int, kiste_id: int | None = None, karten_ids: list[int] | None = None):
    """(Übersicht, [(Karte, Regel)]) für alle Karten in Kisten (ohne die ausgenommenen) oder nur in `kiste_id`."""
    namen, ausgenommen = _kisten(con, nutzer)
    _kiste_pruefen(con, nutzer, kiste_id, ausgenommen)
    uebersicht = {"posten": 0, "karten": 0, "wartet": 0, "pruefen": 0, "gesamt": 0}
    sql = "SELECT * FROM karte WHERE nutzer_id = ? AND kiste_id IS NOT NULL"
    p: list = [nutzer]
    if ausgenommen:
        sql += f" AND kiste_id NOT IN ({', '.join('?' for _ in ausgenommen)})"
        p += sorted(ausgenommen)
    if kiste_id is not None:
        sql += " AND kiste_id = ?"
        p.append(kiste_id)
    if karten_ids is not None:
        if not karten_ids:
            return uebersicht, [], namen
        sql += f" AND id IN ({', '.join('?' for _ in karten_ids)})"
        p += karten_ids
    karten = con.execute(sql + " ORDER BY id", p).fetchall()
    sortierbare = [k for k in karten if es.sortierbar(k)]
    ergebnis = es.auswerten(con, nutzer, [k["id"] for k in sortierbare]) if sortierbare else {}
    wege = []
    for k in karten:
        uebersicht["gesamt"] += k["anzahl"]
        if not es.sortierbar(k):
            uebersicht["pruefen"] += k["anzahl"]
            continue
        e = ergebnis.get(k["id"], {"regel": None, "fehlt": ["Kartendaten"]})
        if e["fehlt"]:
            uebersicht["wartet"] += k["anzahl"]
            continue
        regel = e["regel"]
        if regel is None or regel["kiste_id"] == k["kiste_id"] or regel["kiste_id"] in ausgenommen:
            continue   # bleibt: passt, keine Regel mehr oder Ziel ist ausgenommen
        wege.append((k, regel))
        uebersicht["posten"] += 1
        uebersicht["karten"] += k["anzahl"]
    return uebersicht, wege, namen


def _haken(con, nutzer: int) -> dict[int, tuple[int, int]]:
    return {z[0]: (z[1], z[2]) for z in con.execute(
        "SELECT karte_id, von_kiste_id, nach_kiste_id FROM umraeum_haken WHERE nutzer_id = ?", (nutzer,))}


def plan(con, nutzer: int, kiste_id: int | None = None) -> dict:
    """Umräumliste für eine Kiste oder (ohne `kiste_id`) alle Kisten, gruppiert nach der Kiste, aus der die Karten
    kommen. Je Posten: Karte, von, nach, Zielfach, Regel, abgehakt."""
    if not es.aktiv(con):
        raise Fehler("Sortierregeln gibt es erst ab Datenbankversion 6.", 404)
    uebersicht, wege, namen = _rechnen(con, nutzer, kiste_id)
    haken = _haken(con, nutzer)
    # Zielfächer je Zielkiste in einem Rutsch
    je_ziel: dict[int, list[int]] = {}
    for k, regel in wege:
        je_ziel.setdefault(regel["kiste_id"], []).append(k["id"])
    faecher: dict[int, str | None] = {}
    for ziel, ids in je_ziel.items():
        faecher.update(es.faecher_von(con, nutzer, ziel, ids))
    gruppen: dict[int, dict] = {}
    abgehakt = {"posten": 0, "karten": 0}
    for k, regel in wege[:LISTE_MAX]:
        hk = haken.get(k["id"]) == (k["kiste_id"], regel["kiste_id"])
        if hk:
            abgehakt["posten"] += 1
            abgehakt["karten"] += k["anzahl"]
        g = gruppen.setdefault(k["kiste_id"], {"kiste_id": k["kiste_id"], "kiste": namen.get(k["kiste_id"]),
                                               "posten": []})
        g["posten"].append({
            "karte_id": k["id"], "name": k["name"], "set_code": k["set_code"], "nummer": k["nummer"],
            "sprache": k["sprache"], "foil": bool(k["foil"]), "anzahl": k["anzahl"], "scryfall_id": k["scryfall_id"],
            "nach_kiste_id": regel["kiste_id"], "nach": regel["ziel"], "fach": faecher.get(k["id"]),
            "regel": regel["name"], "abgehakt": hk})
    liste = sorted(gruppen.values(), key=lambda g: ((g["kiste"] or "").casefold(), g["kiste_id"]))
    for g in liste:
        g["posten"].sort(key=lambda e: ((e["nach"] or "").casefold(), e["fach"] or "", (e["name"] or "").casefold(),
                                        e["karte_id"]))
        g["karten"] = sum(e["anzahl"] for e in g["posten"])
    return {"kiste_id": kiste_id, "kiste": namen.get(kiste_id) if kiste_id else None, **uebersicht,
            "abgehakt": abgehakt, "gekuerzt": len(wege) > LISTE_MAX, "gruppen": liste}


def haken(con, nutzer: int, karte_id: int, abgehakt: bool) -> dict:
    """Einen Posten der Umräumliste abhaken oder den Haken entfernen (merkt nur vor, ohne Historie)."""
    if not abgehakt:
        con.execute("DELETE FROM umraeum_haken WHERE karte_id = ? AND nutzer_id = ?", (karte_id, nutzer))
        return {"karte_id": karte_id, "abgehakt": False}
    _, wege, _ = _rechnen(con, nutzer, karten_ids=[karte_id])
    if not wege:
        raise Fehler("Diese Karte gehört nicht (mehr) in eine andere Kiste. Bitte die Liste neu laden.", 409)
    k, regel = wege[0]
    con.execute("INSERT OR REPLACE INTO umraeum_haken (karte_id, nutzer_id, von_kiste_id, nach_kiste_id)"
                " VALUES (?, ?, ?, ?)", (karte_id, nutzer, k["kiste_id"], regel["kiste_id"]))
    return {"karte_id": karte_id, "abgehakt": True}


def fertig(con, nutzer: int, kiste_id: int | None = None) -> dict:
    """Alle abgehakten Posten (der Kiste oder aller Kisten) digital in ihre Zielkiste verschieben, als ein Schritt
    des Nutzers mit Rückgängig. Haken, die nicht mehr stimmen (Karte bewegt, Regel geändert), verfallen."""
    if not es.aktiv(con):
        raise Fehler("Sortierregeln gibt es erst ab Datenbankversion 6.", 404)
    haken_alle = _haken(con, nutzer)
    _, wege, namen = _rechnen(con, nutzer, kiste_id)
    schritt, posten, karten_n, ziele = None, 0, 0, {}
    for k, regel in wege:
        if haken_alle.get(k["id"]) != (k["kiste_id"], regel["kiste_id"]):
            continue
        schritt = schritt or schritte.neuer_schritt()
        schritte.aendern_genau(con, nutzer, "karte", k["id"], {"kiste_id": regel["kiste_id"]}, schritt=schritt,
                               quelle=QUELLE)
        posten += 1
        karten_n += k["anzahl"]
        z = ziele.setdefault(regel["kiste_id"], {"kiste_id": regel["kiste_id"], "kiste": regel["ziel"], "karten": 0})
        z["karten"] += k["anzahl"]
    # Haken des Bereichs sind jetzt verbraucht (verschoben) oder überholt (Karte bewegt, Regel geändert); die Haken
    # anderer Kisten bleiben
    for kid, (von, _) in haken_alle.items():
        if kiste_id is None or von == kiste_id:
            con.execute("DELETE FROM umraeum_haken WHERE karte_id = ? AND nutzer_id = ?", (kid, nutzer))
    return {"schritt": schritt, "posten": posten, "karten": karten_n, "kisten": list(ziele.values())}
