"""Deck-Statistik (Paket 10): Manakurve, Farben, Kartentypen und Durchschnittsmanawert einer Deckliste.

Antworten des Nutzers (10.11.2026): Manakurve, Farbverteilung, Kartentypen, Durchschnittsmanawert ohne Länder;
Doppelseitige Karten zählen mit dem Manawert der Vorderseite, Länder stehen nicht in der Manakurve.

Festlegungen (berechnet, nicht gespeichert, nur lesend):
- Grundlage ist die **Deckliste** (alle Zeilen von `deck_karte` mit ihrer Anzahl, Commander eingeschlossen), nicht die
  Karten der Sammlung. Basics zählen immer mit, auch bei „Basics auslassen“ (das betrifft nur den Abgleich).
- Kartendaten je Zeile: `kartendaten` nach Scryfall-ID, sonst nach Oracle-ID (Manawert, Farben und Typ hängen nicht
  am Druck), sonst der Katalog (`kartendaten.nachschlagen`). Zeilen ohne Daten zählen sichtbar nicht mit.
- Manawert ist der von `kartendaten` (Scryfall `cmc`): bei Verwandlungs- und Modalkarten der Wert der Vorderseite,
  Farben und Haupttyp ebenfalls von der Vorderseite (siehe `katalog/kartendaten.py`). Ein Land auf der Vorderseite
  macht die Karte zum Land.
- Manakurve: Nicht-Länder in Stufen 0 bis 6 und „7+“ (Bruchteile wie 0,5 abgerundet).
- Durchschnittsmanawert: über alle Nicht-Länder mit Daten (Karten mit Manawert 0 zählen mit).
- Farben: Nicht-Länder je Farbe (W U B R G); eine mehrfarbige Karte zählt bei jeder ihrer Farben, dazu „farblos“ und
  die Zahl der mehrfarbigen Karten. Länder sind nicht dabei (sonst überwöge „farblos“).
- Kartentypen: alle Karten nach Haupttyp (Vorrang wie im Katalog: Land > Kreatur > Planeswalker > …).
"""
from __future__ import annotations

from binderapp.katalog import kartendaten as kd

STUFEN = 8                     # 0 … 6 und „7+“
FARBEN = ("W", "U", "B", "R", "G")
TYPEN = ("kreatur", "planeswalker", "spontanzauber", "hexerei", "artefakt", "verzauberung", "schlacht", "land",
         "sonstige")
FELDER = ("manawert", "farben", "haupttyp")
OHNE_DATEN_NAMEN = 10


def _platzhalter(n: int) -> str:
    return ",".join("?" * n)


def daten_je_zeile(con, zeilen: list[dict], katalog=None) -> dict[int, dict]:
    """{deck_karte_id: {manawert, farben, haupttyp}} für jede Zeile, zu der Daten bekannt sind.

    `katalog` ist eine Funktion, die eine Verbindung zum Katalog liefert (oder None); sie wird nur aufgerufen, wenn
    nach `kartendaten` noch Zeilen ohne Daten übrig sind.
    """
    ids = sorted({z["scryfall_id"] for z in zeilen if z.get("scryfall_id")})
    oracles = sorted({z["oracle_id"] for z in zeilen if z.get("oracle_id")})
    nach_id, nach_oracle = {}, {}
    spalten = ", ".join(FELDER)
    if ids:
        for r in con.execute(f"SELECT scryfall_id, {spalten} FROM kartendaten WHERE scryfall_id IN "
                             f"({_platzhalter(len(ids))})", ids):
            nach_id[r[0]] = dict(zip(FELDER, r[1:]))
    if oracles:
        for r in con.execute(f"SELECT oracle_id, {spalten} FROM kartendaten WHERE oracle_id IN "
                             f"({_platzhalter(len(oracles))}) ORDER BY scryfall_id", oracles):
            nach_oracle.setdefault(r[0], dict(zip(FELDER, r[1:])))
    ergebnis = {}
    fehlen = []
    for z in zeilen:
        d = nach_id.get(z.get("scryfall_id")) or nach_oracle.get(z.get("oracle_id"))
        if d:
            ergebnis[z["id"]] = d
        else:
            fehlen.append(z)
    if fehlen and katalog is not None:
        kat = katalog()
        if kat is not None:
            try:
                for z in fehlen:
                    d = kd.nachschlagen(kat, z.get("scryfall_id"), z.get("set_code"), z.get("nummer"),
                                        z.get("oracle_id"))
                    if d:
                        ergebnis[z["id"]] = {f: d[f] for f in FELDER}
            finally:
                kat.close()
    return ergebnis


def berechnen(zeilen: list[dict], daten: dict[int, dict]) -> dict:
    """Statistik aus Deckzeilen ({id, name, anzahl}) und ihren Kartendaten (`daten_je_zeile`)."""
    kurve = [0] * STUFEN
    farben = {f: 0 for f in FARBEN}
    farblos = mehrfarbig = 0
    typen = {t: 0 for t in TYPEN}
    karten = laender = nichtlaender = 0
    summe_mw = 0.0
    ohne = 0
    ohne_namen: list[str] = []
    for z in zeilen:
        n = int(z["anzahl"])
        karten += n
        d = daten.get(z["id"])
        if not d:
            ohne += n
            if z["name"] not in ohne_namen and len(ohne_namen) < OHNE_DATEN_NAMEN:
                ohne_namen.append(z["name"])
            continue
        typ = d["haupttyp"] if d["haupttyp"] in typen else "sonstige"
        typen[typ] += n
        if typ == "land":
            laender += n
            continue
        nichtlaender += n
        mw = float(d["manawert"] or 0)
        summe_mw += mw * n
        kurve[min(int(mw), STUFEN - 1)] += n
        eigene = [f for f in FARBEN if f in (d["farben"] or "")]
        for f in eigene:
            farben[f] += n
        if not eigene:
            farblos += n
        elif len(eigene) > 1:
            mehrfarbig += n
    return {
        "karten": karten,
        "mit_daten": karten - ohne,
        "ohne_daten": ohne,
        "ohne_daten_namen": ohne_namen,
        "laender": laender,
        "nichtlaender": nichtlaender,
        "durchschnitt": round(summe_mw / nichtlaender, 2) if nichtlaender else None,
        "kurve": [{"manawert": i, "text": f"{i}+" if i == STUFEN - 1 else str(i), "karten": kurve[i]}
                  for i in range(STUFEN)],
        "farben": [{"farbe": f, "karten": farben[f]} for f in FARBEN] + [{"farbe": "C", "karten": farblos}],
        "mehrfarbig": mehrfarbig,
        "typen": [{"typ": t, "karten": typen[t]} for t in TYPEN],
    }


def statistik(con, zeilen: list[dict], katalog=None) -> dict:
    """Statistik einer Deckliste (Zeilen wie in `abgleich.abgleichen`)."""
    return berechnen(zeilen, daten_je_zeile(con, zeilen, katalog))


def baubar(deck, summe: dict) -> dict:
    """Wie viel des Decks aus der Sammlung baubar ist, nach der Einstellung des Decks (Name oder Druck).

    `aus_sammlung` = schon im Deck + frei verfügbar (ohne Proxys, ohne ausgelassene Basics); Prozent abgerundet,
    damit 100 % nur dasteht, wenn wirklich nichts fehlt.
    """
    karten = summe.get("karten", 0)
    da = summe.get("vorhanden", 0)
    return {"karten": karten, "aus_sammlung": da, "proxy": summe.get("proxy", 0), "fehlt": summe.get("fehlt", 0),
            "prozent": (100 * da // karten) if karten else None, "abgleich": deck["abgleich"]}
