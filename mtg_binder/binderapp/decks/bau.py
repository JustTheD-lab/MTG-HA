"""Deckbau (ab Version 8): Entnahme nach Lagerort, „Fertig“, Zerlegen und „Deck zur Sammlung hinzufügen“.

Entscheidungen des Nutzers (02./03.11.2026):
- Die Entnahme ist eine Liste nach Ort (Kisten nach Fach, Binder nach Seite und Platz). Die App schlägt das Exemplar
  vor (genauer Druck zuerst, dann Kisten vor Bindern), je Zeile lässt sich ein anderes wählen.
- Abhaken merkt nur vor (Tabelle deck_entnahme im Add-on, übersteht Gerätewechsel). Erst „Fertig“ verschiebt alle
  abgehakten Karten in einem Bedienschritt ins Deck (Rückgängig über POST /api/schritt/{schritt}/rueckgaengig);
  ein Stapel teilt sich, eine Binder-Tasche wird „leer“, die Herkunft wird gespeichert.
- Zerlegen legt jede Karte an ihren alten Platz zurück; ist die Tasche belegt oder fehlt die Herkunft, gilt die
  Herkunftskiste, sonst die Sortierregeln, sonst eine Kiste, die der Nutzer wählt.
- „Deck zur Sammlung hinzufügen“ legt alle Karten der Liste neu im Deck an (Basics ja, Proxys nein).
Alles in der Transaktion des Aufrufers, Karten und Taschen mit Historie (db/schritte.py).
"""
from __future__ import annotations

from binderapp.db import schritte
from binderapp.db.seiten import Fehler
from binderapp.decks import abgleich as ab
from binderapp.katalog import kartendaten
from binderapp.sortieren import einsortieren

NICHT_KOPIEREN = ("id", "angelegt", "geaendert")


def _deck(con, nutzer: int, deck_id: int):
    try:
        return ab.deck_holen(con, nutzer, deck_id)
    except LookupError as f:
        raise Fehler(str(f), 404)


def _zeile(con, deck_id: int, zeile_id: int):
    z = con.execute("SELECT * FROM deck_karte WHERE id = ? AND deck_id = ?", (zeile_id, deck_id)).fetchone()
    if z is None:
        raise Fehler("Diese Karte gehört nicht zum Deck.", 404)
    return dict(z)


def _ort_text(o: dict) -> str:
    if o["art"] == "kiste":
        return f"Kiste {o['kiste']}"
    if o["art"] == "deck":
        return f"Deck {o['deck']}"
    return f"Binder {o['binder']} · Seite {o['seite']} · Reihe {o['zeile']}, Platz {o['spalte']}"


# ---------------------------------------------------------------- Entnahme


def plan(con, nutzer: int, deck_id: int) -> dict:
    """Entnahmeliste: je Zeile, die noch Karten braucht (ohne Proxys und ausgelassene Basics), die vorgeschlagenen
    oder gewählten Exemplare, gruppiert nach Ort. Gespeicherte Wahl und Haken haben Vorrang."""
    deck = _deck(con, nutzer, deck_id)
    a = ab.abgleichen(con, nutzer, deck)
    im_deck, frei, _, alle = ab.karten_lesen(con, nutzer, deck_id)
    rest = {kid: k["anzahl"] for kid, k in alle.items() if k["deck_id"] is None}
    druck_modus = deck["abgleich"] == "druck"
    gemerkt: dict[int, list] = {}
    for g in con.execute("SELECT * FROM deck_entnahme WHERE deck_id = ? ORDER BY id", (deck_id,)):
        gemerkt.setdefault(g["deck_karte_id"], []).append(dict(g))
    eintraege, offen = [], []
    for z in a["karten"]:
        brauche = 0 if (z["ausgelassen"] or z["proxy"]) else z["anzahl"] - z["im_deck"]
        if brauche <= 0:
            continue
        kandidaten = ab.passend(ab._kandidaten(frei, z), z, druck_modus)
        erlaubt = {k["id"] for k in kandidaten}
        reihenfolge = [alle[g["karte_id"]] for g in gemerkt.get(z["id"], []) if g["karte_id"] in erlaubt]
        reihenfolge += [k for k in kandidaten if k not in reihenfolge]
        haken = {g["karte_id"]: g for g in gemerkt.get(z["id"], [])}
        for k in reihenfolge:
            nimm = min(brauche, rest.get(k["id"], 0))
            g = haken.get(k["id"])
            if g is not None:
                nimm = min(nimm, g["anzahl"]) if g["abgehakt"] else nimm
            if nimm <= 0:
                continue
            rest[k["id"]] -= nimm
            brauche -= nimm
            o = ab.ort(k)
            eintraege.append({
                "deck_karte_id": z["id"], "karte_id": k["id"], "anzahl": nimm, "name": k["name"],
                "set_code": k["set_code"], "nummer": k["nummer"], "foil": bool(k["foil"]), "sprache": k["sprache"],
                "scryfall_id": k["scryfall_id"], "genau": ab.genau(k, z), "ort": {**o, "text": _ort_text(o)},
                "abgehakt": bool(g and g["abgehakt"]), "alternativen": max(0, len(kandidaten) - 1),
                "_sort": ab.frei_reihenfolge(k)})
            if brauche == 0:
                break
        if brauche:
            offen.append({"deck_karte_id": z["id"], "name": z["name"], "fehlt": brauche,
                          "andere_decks": z["andere_decks"]})
    return {"deck_id": deck_id, "name": deck["name"], "gruppen": _gruppieren(con, nutzer, eintraege),
            "offen": offen, "gesamt": sum(e["anzahl"] for e in eintraege),
            "abgehakt": sum(e["anzahl"] for e in eintraege if e["abgehakt"])}


def _gruppieren(con, nutzer: int, eintraege: list[dict]) -> list[dict]:
    """Gruppen nach Ort: Kisten (nach Fach, dann Name), dann Binder (nach Seite, Reihe, Platz)."""
    faecher: dict[int, dict] = {}
    for kiste_id in {e["ort"]["kiste_id"] for e in eintraege if e["ort"]["art"] == "kiste"}:
        ids = [e["karte_id"] for e in eintraege if e["ort"].get("kiste_id") == kiste_id]
        reihe = [f["name"] for f in einsortieren.faecher_lesen(con, nutzer, kiste_id) if f["gueltig"]]
        faecher.update({kid: {"fach": name, "fach_platz": reihe.index(name) if name in reihe else len(reihe)}
                        for kid, name in einsortieren.faecher_von(con, nutzer, kiste_id, ids).items()})
    gruppen: dict[tuple, dict] = {}
    for e in sorted(eintraege, key=lambda e: e.pop("_sort")):
        o = e["ort"]
        e.update(faecher.get(e["karte_id"], {"fach": None, "fach_platz": None}) if o["art"] == "kiste" else {})
        schluessel = ("kiste", o["kiste_id"]) if o["art"] == "kiste" else ("binder", o["binder_id"])
        g = gruppen.setdefault(schluessel, {
            "art": o["art"], "id": schluessel[1], "name": o.get("kiste") or o.get("binder"),
            "text": f"Kiste {o['kiste']}" if o["art"] == "kiste" else f"Binder {o['binder']}", "eintraege": []})
        g["eintraege"].append(e)
    for g in gruppen.values():
        if g["art"] == "kiste":
            g["eintraege"].sort(key=lambda e: (e["fach_platz"] is None, e["fach_platz"] or 0, e["name"].lower()))
    return list(gruppen.values())


def _frei_pruefen(con, nutzer: int, karte_id: int):
    k = con.execute("SELECT * FROM karte WHERE id = ? AND nutzer_id = ?", (karte_id, nutzer)).fetchone()
    if k is None:
        raise Fehler("Karte nicht gefunden.", 404)
    if k["deck_id"] is not None or (k["kiste_id"] is None and k["tasche_id"] is None):
        raise Fehler("Diese Karte liegt nicht frei in einem Binder oder einer Kiste.", 409)
    return k


def haken(con, nutzer: int, deck_id: int, zeile_id: int, karte_id: int, anzahl: int, abgehakt: bool) -> dict:
    """Haken setzen oder entfernen (Zwischenstand, ohne Historie). Entfernen geht immer, auch wenn die Karte
    inzwischen woanders liegt; setzen nur für ein freies Exemplar, das zur Zeile passt."""
    _deck(con, nutzer, deck_id)
    _zeile(con, deck_id, zeile_id)
    if not abgehakt:
        con.execute("DELETE FROM deck_entnahme WHERE deck_karte_id = ? AND karte_id = ?", (zeile_id, karte_id))
        return {"deck_karte_id": zeile_id, "karte_id": karte_id, "anzahl": anzahl, "abgehakt": False}
    if karte_id not in {a["karte_id"] for a in alternativen(con, nutzer, deck_id, zeile_id)}:
        raise Fehler("Dieses Exemplar passt nicht zur Karte oder liegt nicht frei.", 409)
    k = _frei_pruefen(con, nutzer, karte_id)
    if anzahl < 1 or anzahl > k["anzahl"]:
        raise Fehler(f"Von dieser Karte gibt es dort nur {k['anzahl']}.")
    con.execute("INSERT INTO deck_entnahme (deck_id, deck_karte_id, karte_id, anzahl, abgehakt) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(deck_karte_id, karte_id) DO UPDATE SET anzahl = excluded.anzahl,"
                " abgehakt = excluded.abgehakt", (deck_id, zeile_id, karte_id, anzahl, int(abgehakt)))
    return {"deck_karte_id": zeile_id, "karte_id": karte_id, "anzahl": anzahl, "abgehakt": abgehakt}


def alternativen(con, nutzer: int, deck_id: int, zeile_id: int) -> list[dict]:
    """Alle freien Exemplare, die zu einer Zeile passen (für „anderes Exemplar wählen“)."""
    deck = _deck(con, nutzer, deck_id)
    z = _zeile(con, deck_id, zeile_id)
    _, frei, _, _ = ab.karten_lesen(con, nutzer, deck_id)
    aus = []
    for k in ab.passend(ab._kandidaten(frei, z), z, deck["abgleich"] == "druck"):
        o = ab.ort(k)
        aus.append({"karte_id": k["id"], "name": k["name"], "set_code": k["set_code"], "nummer": k["nummer"],
                    "foil": bool(k["foil"]), "sprache": k["sprache"], "anzahl": k["anzahl"],
                    "scryfall_id": k["scryfall_id"], "genau": ab.genau(k, z), "ort": {**o, "text": _ort_text(o)}})
    return aus


def waehlen(con, nutzer: int, deck_id: int, zeile_id: int, karte_id: int) -> dict:
    """Ein anderes Exemplar für eine Zeile: ersetzt die nicht abgehakten Vorschläge dieser Zeile."""
    _deck(con, nutzer, deck_id)
    _zeile(con, deck_id, zeile_id)
    if karte_id not in {a["karte_id"] for a in alternativen(con, nutzer, deck_id, zeile_id)}:
        raise Fehler("Dieses Exemplar passt nicht zur Karte oder liegt nicht frei.", 409)
    k = _frei_pruefen(con, nutzer, karte_id)
    con.execute("DELETE FROM deck_entnahme WHERE deck_karte_id = ? AND abgehakt = 0", (zeile_id,))
    con.execute("INSERT INTO deck_entnahme (deck_id, deck_karte_id, karte_id, anzahl, abgehakt) VALUES (?, ?, ?, ?, 0)"
                " ON CONFLICT(deck_karte_id, karte_id) DO NOTHING", (deck_id, zeile_id, karte_id, k["anzahl"]))
    return {"deck_karte_id": zeile_id, "karte_id": karte_id}


def _ins_deck(con, nutzer: int, k, menge: int, deck_id: int, schritt: str) -> int:
    """Verschiebt `menge` Exemplare einer freien Karte ins Deck (ein Stapel teilt sich); gibt die Karte im Deck an."""
    herkunft = {"herkunft_tasche_id": k["tasche_id"], "herkunft_kiste_id": k["kiste_id"]}
    if menge >= k["anzahl"]:
        schritte.aendern_genau(con, nutzer, "karte", k["id"], {"deck_id": deck_id, "tasche_id": None, "kiste_id": None,
                                                                "sortieren": None, **herkunft}, schritt=schritt)
        if k["tasche_id"] is not None and con.execute("SELECT 1 FROM karte WHERE tasche_id = ? LIMIT 1",
                                                      (k["tasche_id"],)).fetchone() is None:
            schritte.aendern_genau(con, nutzer, "tasche", k["tasche_id"], {"inhalt": "leer"}, schritt=schritt)
        return k["id"]
    schritte.aendern_genau(con, nutzer, "karte", k["id"], {"anzahl": k["anzahl"] - menge}, schritt=schritt)
    felder = {s: k[s] for s in k.keys() if s not in NICHT_KOPIEREN}
    felder.update({"anzahl": menge, "deck_id": deck_id, "tasche_id": None, "kiste_id": None, "extern_id": None,
                   "sortieren": None, **herkunft})
    return schritte.einfuegen(con, nutzer, "karte", felder, schritt=schritt)


def fertig(con, nutzer: int, deck_id: int) -> dict:
    """Alle abgehakten Karten in einem Schritt ins Deck; danach ist der Zwischenstand leer."""
    deck = _deck(con, nutzer, deck_id)
    vorher = schritte.summe(con, nutzer)
    a = ab.abgleichen(con, nutzer, deck)
    brauche = {z["id"]: (0 if (z["ausgelassen"] or z["proxy"]) else z["anzahl"] - z["im_deck"]) for z in a["karten"]}
    schritt = schritte.neuer_schritt()
    karten = posten = uebersprungen = 0
    for g in con.execute("SELECT * FROM deck_entnahme WHERE deck_id = ? AND abgehakt = 1 ORDER BY id",
                         (deck_id,)).fetchall():
        menge = min(g["anzahl"], brauche.get(g["deck_karte_id"], 0))
        if menge <= 0:
            continue
        k = con.execute("SELECT * FROM karte WHERE id = ? AND nutzer_id = ?", (g["karte_id"], nutzer)).fetchone()
        if k is None or k["deck_id"] is not None or (k["kiste_id"] is None and k["tasche_id"] is None):
            uebersprungen += 1   # inzwischen in einem anderen Deck oder weg: Haken verfällt (Befund 03.11.2026)
            continue
        menge = min(menge, k["anzahl"])
        _ins_deck(con, nutzer, k, menge, deck_id, schritt)
        brauche[g["deck_karte_id"]] -= menge
        karten += menge
        posten += 1
    if not karten:
        if uebersprungen:   # nur veraltete Haken: aufräumen, damit sie Fertig nicht dauerhaft sperren
            con.execute("DELETE FROM deck_entnahme WHERE deck_id = ? AND abgehakt = 1", (deck_id,))
            return {"schritt": None, "karten": 0, "posten": 0, "uebersprungen": uebersprungen}
        raise Fehler("Es ist noch keine Karte abgehakt.")
    if schritte.summe(con, nutzer)[1] != vorher[1]:   # darf nie vorkommen; die Ausnahme rollt alles zurück
        raise Fehler("Summenprüfung fehlgeschlagen, nichts verschoben.", 500)
    con.execute("DELETE FROM deck_entnahme WHERE deck_id = ?", (deck_id,))
    return {"schritt": schritt, "karten": karten, "posten": posten, "uebersprungen": uebersprungen}


# ---------------------------------------------------------------- Zerlegen


def _ziel(con, nutzer: int, k, regel: dict | None) -> dict | None:
    t = k["herkunft_tasche_id"]
    if t is not None:
        z = con.execute("SELECT t.id, t.zeile, t.spalte, s.nummer AS seite, b.id AS binder_id, b.name AS binder"
                        " FROM tasche t JOIN seite s ON s.id = t.seite_id JOIN binder b ON b.id = s.binder_id"
                        " WHERE t.id = ? AND b.nutzer_id = ?", (t, nutzer)).fetchone()
        if z is not None and con.execute("SELECT 1 FROM karte WHERE tasche_id = ? LIMIT 1", (t,)).fetchone() is None:
            return {"art": "tasche", "tasche_id": z["id"], "binder_id": z["binder_id"], "binder": z["binder"],
                    "seite": z["seite"], "zeile": z["zeile"], "spalte": z["spalte"],
                    "text": f"Binder {z['binder']} · Seite {z['seite']} · Reihe {z['zeile']}, Platz {z['spalte']}"}
    for kiste_id, grund in ((k["herkunft_kiste_id"], "herkunft"), ((regel or {}).get("kiste_id"), "regel")):
        if kiste_id is not None:
            z = con.execute("SELECT id, name FROM kiste WHERE id = ? AND nutzer_id = ?", (kiste_id, nutzer)).fetchone()
            if z is not None:
                return {"art": "kiste", "kiste_id": z["id"], "kiste": z["name"], "grund": grund,
                        "text": f"Kiste {z['name']}"}
    return None


def zerlegen_plan(con, nutzer: int, deck_id: int, karten_ids: list[int] | None = None) -> dict:
    """Wohin jede Karte des Decks zurückgeht (alle oder nur `karten_ids`, z. B. die überzähligen)."""
    _deck(con, nutzer, deck_id)
    karten = [k for k in con.execute("SELECT * FROM karte WHERE deck_id = ? AND nutzer_id = ? ORDER BY id",
                                     (deck_id, nutzer)).fetchall() if karten_ids is None or k["id"] in karten_ids]
    regeln = einsortieren.auswerten(con, nutzer, [k["id"] for k in karten]) if karten else {}
    belegt: set[int] = set()
    eintraege = []
    for k in karten:
        ziel = _ziel(con, nutzer, k, (regeln.get(k["id"]) or {}).get("regel"))
        if ziel and ziel["art"] == "tasche":
            if ziel["tasche_id"] in belegt:   # zwei Karten aus derselben Tasche: die zweite in die Kiste
                ziel = _ziel(con, nutzer, {**dict(k), "herkunft_tasche_id": None},
                             (regeln.get(k["id"]) or {}).get("regel"))
            else:
                belegt.add(ziel["tasche_id"])
        eintraege.append({"karte_id": k["id"], "name": k["name"], "anzahl": k["anzahl"], "foil": bool(k["foil"]),
                          "set_code": k["set_code"], "nummer": k["nummer"], "scryfall_id": k["scryfall_id"],
                          "ziel": ziel})
    return {"deck_id": deck_id, "karten": eintraege, "ohne_ziel": sum(1 for e in eintraege if e["ziel"] is None)}


def zerlegen(con, nutzer: int, deck_id: int, karten_ids: list[int] | None = None,
             ersatz_kiste: int | None = None) -> dict:
    """Karten zurücklegen (ein Schritt). Karten ohne Ziel gehen in `ersatz_kiste`; fehlt sie, kommt 409."""
    p = zerlegen_plan(con, nutzer, deck_id, karten_ids)
    if not p["karten"]:
        raise Fehler("Im Deck liegen keine passenden Karten.")
    if p["ohne_ziel"]:
        if ersatz_kiste is None:
            raise Fehler(f"{p['ohne_ziel']} Karten haben keinen alten Platz und keine Sortierregel. "
                         "Bitte eine Kiste wählen.", 409)
        if con.execute("SELECT 1 FROM kiste WHERE id = ? AND nutzer_id = ?", (ersatz_kiste, nutzer)).fetchone() is None:
            raise Fehler("Diese Kiste gibt es nicht.", 404)
    vorher = schritte.summe(con, nutzer)
    schritt = schritte.neuer_schritt()
    for e in p["karten"]:
        ziel = e["ziel"] or {"art": "kiste", "kiste_id": ersatz_kiste}
        ort = ({"tasche_id": ziel["tasche_id"], "kiste_id": None} if ziel["art"] == "tasche"
               else {"tasche_id": None, "kiste_id": ziel["kiste_id"]})
        schritte.aendern_genau(con, nutzer, "karte", e["karte_id"], {**ort, "deck_id": None, "herkunft_tasche_id": None,
                                                                      "herkunft_kiste_id": None}, schritt=schritt)
        if ziel["art"] == "tasche":
            schritte.aendern_genau(con, nutzer, "tasche", ziel["tasche_id"], {"inhalt": "karte"}, schritt=schritt)
    if schritte.summe(con, nutzer) != vorher:
        raise Fehler("Summenprüfung fehlgeschlagen, nichts zurückgelegt.", 500)
    return {"schritt": schritt, "karten": sum(e["anzahl"] for e in p["karten"]), "posten": len(p["karten"])}


# ---------------------------------------------------------------- Deck zur Sammlung hinzufügen


def zur_sammlung(con, kat, nutzer: int, deck_id: int) -> dict:
    """Alle Karten der Liste neu im Deck anlegen (z. B. ein gekauftes Precon): Basics ja, Proxys nein (Entscheidung
    des Nutzers, 03.11.2026). Nur für ein Deck ohne Karten. Quelle „manuell“; mit Druck in der Liste „sicher“ und
    geprüft, sonst „Name sicher“ und ungeprüft (Prüfansicht); ein Schritt."""
    _deck(con, nutzer, deck_id)
    if con.execute("SELECT 1 FROM karte WHERE deck_id = ? LIMIT 1", (deck_id,)).fetchone() is not None:
        raise Fehler("In diesem Deck liegen schon Karten. Hinzufügen geht nur bei einem leeren Deck.", 409)
    schritt = schritte.neuer_schritt()
    karten = posten = 0
    for z in con.execute("SELECT * FROM deck_karte WHERE deck_id = ? AND proxy = 0 ORDER BY id", (deck_id,)).fetchall():
        daten = kartendaten.nachschlagen(kat, z["scryfall_id"]) if (kat is not None and z["scryfall_id"]) else None
        gewiss = bool(z["druck_angegeben"] and z["scryfall_id"] and daten)
        schritte.einfuegen(con, nutzer, "karte", {
            "nutzer_id": nutzer, "deck_id": deck_id, "quelle": "manuell",
            "scryfall_id": z["scryfall_id"], "oracle_id": (daten or {}).get("oracle_id") or z["oracle_id"],
            "name": (daten or {}).get("name") or z["name"],
            "set_code": (daten or {}).get("set_code") or z["set_code"],
            "nummer": (daten or {}).get("nummer") or z["nummer"], "sprache": (daten or {}).get("sprache") or "en",
            "foil": z["foil"],
            # Ohne Druck in der Liste ist nur der Name sicher: nie stillschweigend einen Druck als geprüft übernehmen,
            # die Prüfansicht fragt ihn nach (Befund 03.11.2026)
            "anzahl": z["anzahl"], "status": "sicher" if gewiss else "name_sicher", "geprueft": int(gewiss),
            "bild": (daten or {}).get("bild") or z["scryfall_id"], "zustand": "near_mint"}, schritt=schritt)
        if daten:
            kartendaten.schreiben(con, daten, art="behalten")   # abgeleitet aus dem Katalog, ohne Historie
        karten += z["anzahl"]
        posten += 1
    if not posten:
        raise Fehler("Die Liste enthält keine Karten (nur Proxys?).")
    return {"schritt": schritt, "karten": karten, "posten": posten}
