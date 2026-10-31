"""Kartendaten (Manawert, Farbe, Typ, Seltenheit, Set) aus Scryfall-Objekten und aus dem Katalog.

Reines Python (Standardbibliothek). Die Tabelle `kartendaten` der App-Datenbank ist abgeleitet:
sie hat keine Änderungshistorie und lässt sich jederzeit neu füllen. Sie legt der Hauptagent im
Schema (Version 3) an; `KARTENDATEN_SCHEMA` hier ist dieselbe Definition und dient den Tests.

Festlegungen (Vorgabe Paket B)
- Manawert ist Scryfall `cmc` (bei mehrseitigen Karten der Wert der Karte, nicht einer Seite).
- Mehrseitige Karten: Manakosten, Farben und Typzeile kommen von der Vorderseite
  (`card_faces[0]`), wenn diese das Feld hat, sonst von der Karte selbst. Folgen: Bei
  Verwandlungs-, Modal- und Umkehrkarten zählt die Vorderseite; bei Split-, Abenteuer-, Flip-
  und Prepare-Karten tragen die Seiten keine Farben, dort gilt `colors` der Karte
  (bei Split-Karten die Vereinigung beider Hälften, wie Scryfall sie führt).
- Farben als Zeichenfolge in der Reihenfolge W U B R G, farblos ist die leere Zeichenfolge.
- Farbgruppe: Land → `L` (die Typen der Vorderseite, also die Typzeile vor dem Gedankenstrich,
  enthalten „Land"); sonst keine Farbe → `C`; mehrere Farben → `M`; sonst die eine Farbe.
- Haupttyp nach Vorrang Land > Kreatur > Planeswalker > Schlacht > Spontanzauber (Instant)
  > Hexerei (Sorcery) > Artefakt > Verzauberung > `sonstige` (Tribal/Kindred allein, Token-
  und Sondertypen, Kunstkarten mit „Card").
- Seltenheit wie Scryfall: common, uncommon, rare, mythic, dazu `special` (Sonderkarten und
  Promos) und `bonus` (Bonuskarten besonderer Sets).
- Sammlernummer als Text; `nummer_zahl` sind die führenden Ziffern (123a → 123, ★ → NULL).
- `set_erschienen` ist das früheste `released_at` aller Drucke des Sets (Katalog), nicht das
  Datum des einzelnen Drucks.

Rückfall, wenn die Scryfall-ID nicht im Katalog steht (`gefunden_ueber`):
1. `id`: die ID steht in `druck` (Standardfall).
2. `sprachdruck`: die ID steht in der Zuordnung fremdsprachiger Drucke (aus `all_cards`);
   die Spielwerte kommen vom Druck desselben Sets mit derselben Nummer.
3. `set_nummer`: Set-Kürzel und Sammlernummer der Karte finden einen Druck.
4. `oracle`: nur die Oracle-ID findet Drucke; Spielwerte sind über alle Drucke gleich, Set und
   Nummer bleiben die der Karte, die Seltenheit gilt nur, wenn der Druck im selben Set liegt.
"""

from __future__ import annotations

import re
import sqlite3

FARBFOLGE = "WUBRG"
FARBGRUPPEN = ("W", "U", "B", "R", "G", "M", "C", "L")
HAUPTTYPEN = (
    "kreatur", "planeswalker", "spontanzauber", "hexerei",
    "artefakt", "verzauberung", "schlacht", "land", "sonstige",
)
WEGE = ("id", "sprachdruck", "set_nummer", "oracle")

# Spalten der Tabelle `kartendaten` ohne `geaendert` (das setzt die Datenbank selbst).
SPALTEN = (
    "scryfall_id", "oracle_id", "name", "set_code", "set_name", "set_erschienen", "nummer",
    "nummer_zahl", "sprache", "manakosten", "manawert", "farben", "farbidentitaet",
    "farbgruppe", "typzeile", "haupttyp", "seltenheit", "bild", "gefunden_ueber", "cardmarket_id",
)

# Genau die Definition aus der Vorgabe (der Hauptagent legt sie im Schema Version 3 an).
KARTENDATEN_SCHEMA = """
CREATE TABLE IF NOT EXISTS kartendaten (
    scryfall_id     TEXT PRIMARY KEY,           -- wie karte.scryfall_id (auch Sprachdrucke)
    oracle_id       TEXT,
    name            TEXT NOT NULL,              -- englischer Name laut Scryfall
    set_code        TEXT,                       -- klein, wie Scryfall
    set_name        TEXT,
    set_erschienen  TEXT,                       -- Erscheinungsdatum des Sets, JJJJ-MM-TT
    nummer          TEXT,                       -- Sammlernummer als Text
    nummer_zahl     INTEGER,                    -- Zahlanteil zum Sortieren (123a → 123, ★ → NULL)
    sprache         TEXT,                       -- Sprache des Katalogdrucks, aus dem die Daten stammen
    manakosten      TEXT,                       -- Vorderseite
    manawert        REAL NOT NULL DEFAULT 0,    -- Scryfall cmc
    farben          TEXT NOT NULL DEFAULT '',   -- Vorderseite, Reihenfolge WUBRG, '' = farblos
    farbidentitaet  TEXT NOT NULL DEFAULT '',   -- Reihenfolge WUBRG
    farbgruppe      TEXT NOT NULL CHECK (farbgruppe IN ('W','U','B','R','G','M','C','L')),
    typzeile        TEXT,                       -- Vorderseite
    haupttyp        TEXT NOT NULL CHECK (haupttyp IN ('kreatur','planeswalker','spontanzauber','hexerei',
                        'artefakt','verzauberung','schlacht','land','sonstige')),
    seltenheit      TEXT,                       -- common, uncommon, rare, mythic, special, bonus
    bild            TEXT,                       -- Scryfall-ID eines Drucks mit lokalem Bild (für Vorschaubilder)
    gefunden_ueber  TEXT NOT NULL CHECK (gefunden_ueber IN ('id','sprachdruck','set_nummer','oracle')),
    geaendert       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    cardmarket_id   INTEGER
);
"""

_TYPWORT = re.compile(r"[A-Za-z]+")
_ZIFFERN = re.compile(r"\d+")

# Reihenfolge = Vorrang. Der erste Treffer unter den Typen der Vorderseite gewinnt.
_TYPEN_VORRANG = (
    ("Land", "land"),
    ("Creature", "kreatur"),
    ("Planeswalker", "planeswalker"),
    ("Battle", "schlacht"),
    ("Instant", "spontanzauber"),
    ("Sorcery", "hexerei"),
    ("Artifact", "artefakt"),
    ("Enchantment", "verzauberung"),
)


# --------------------------------------------------------------- Scryfall-Objekt → Zeile


def farben_zeichenfolge(farben) -> str:
    """Scryfall-Farbliste (z. B. ["G", "W"]) → Zeichenfolge in der Reihenfolge W U B R G."""
    gesetzt = {str(f).upper() for f in (farben or [])}
    return "".join(f for f in FARBFOLGE if f in gesetzt)


def nummer_zahl(nummer: str | None) -> int | None:
    """Führende Ziffern der Sammlernummer (123a → 123); ohne führende Ziffern None (★)."""
    if not nummer:
        return None
    treffer = _ZIFFERN.match(str(nummer))
    return int(treffer.group()) if treffer else None


def haupttyp(typzeile: str | None) -> str:
    """Haupttyp (deutsch, klein) aus der Typzeile der Vorderseite, nach Vorrang."""
    if not typzeile:
        return "sonstige"
    typen = set(_TYPWORT.findall(typzeile.split("—")[0]))
    for wort, name in _TYPEN_VORRANG:
        if wort in typen:
            return name
    return "sonstige"


def farbgruppe(farben: str, typzeile: str | None) -> str:
    """W, U, B, R, G, M (mehrfarbig), C (farblos) oder L (Land)."""
    if haupttyp(typzeile) == "land":
        return "L"
    if not farben:
        return "C"
    return "M" if len(farben) > 1 else farben


def _vorderseite(objekt: dict) -> dict:
    seiten = objekt.get("card_faces")
    return seiten[0] if seiten else {}


def _feld(objekt: dict, vorne: dict, schluessel: str):
    """Wert der Vorderseite, wenn sie das Feld hat, sonst der Wert der Karte."""
    wert = vorne.get(schluessel)
    return wert if wert is not None else objekt.get(schluessel)


def zeile(objekt: dict, set_erschienen: str | None = None) -> dict:
    """Scryfall-Objekt → Kartendaten (ohne `bild`, `gefunden_ueber`, `geaendert`).

    `set_erschienen` ist das Datum des Sets; ohne Angabe gilt `released_at` des Drucks.
    """
    vorne = _vorderseite(objekt)
    typzeile = _feld(objekt, vorne, "type_line")
    farben = farben_zeichenfolge(_feld(objekt, vorne, "colors"))
    nummer = objekt.get("collector_number")
    nummer = None if nummer is None else str(nummer)
    cmc = objekt.get("cmc")
    if cmc is None:
        cmc = vorne.get("cmc")
    identitaet = objekt.get("color_identity")
    if identitaet is None:
        identitaet = vorne.get("color_identity")
    return {
        "scryfall_id": objekt["id"],
        "oracle_id": objekt.get("oracle_id") or vorne.get("oracle_id"),
        "name": objekt["name"],
        "set_code": str(objekt["set"]).lower() if objekt.get("set") else None,
        "set_name": objekt.get("set_name"),
        "set_erschienen": set_erschienen or objekt.get("released_at"),
        "nummer": nummer,
        "nummer_zahl": nummer_zahl(nummer),
        "sprache": objekt.get("lang"),
        "manakosten": _feld(objekt, vorne, "mana_cost"),
        "manawert": float(cmc) if cmc is not None else 0.0,
        "farben": farben,
        "farbidentitaet": farben_zeichenfolge(identitaet),
        "farbgruppe": farbgruppe(farben, typzeile),
        "typzeile": typzeile,
        "haupttyp": haupttyp(typzeile),
        "seltenheit": objekt.get("rarity"),
    }


# --------------------------------------------------------------- Katalog-Abfragen


def _alle(verbindung: sqlite3.Connection, sql: str, parameter: tuple = ()) -> list[dict]:
    """Führt eine Abfrage aus und gibt die Zeilen als Wörterbücher zurück (unabhängig von row_factory)."""
    cur = verbindung.execute(sql, parameter)
    spalten = [s[0] for s in cur.description]
    return [dict(zip(spalten, r)) for r in cur.fetchall()]


def _eine(verbindung: sqlite3.Connection, sql: str, parameter: tuple = ()) -> dict | None:
    zeilen = _alle(verbindung, sql + " LIMIT 1", parameter)
    return zeilen[0] if zeilen else None


def _druck_nach_set_nummer(kat, set_code: str, nummer: str) -> dict | None:
    return _eine(
        kat,
        "SELECT * FROM druck WHERE set_code = ? AND nummer = ? "
        "ORDER BY (sprache = 'en') DESC, bild_vorhanden DESC, scryfall_id",
        (set_code, nummer),
    )


def _druck_nach_oracle(kat, oracle_id: str, set_code: str | None) -> dict | None:
    return _eine(
        kat,
        "SELECT * FROM druck WHERE oracle_id = ? "
        "ORDER BY (set_code = ?) DESC, (sprache = 'en') DESC, bild_vorhanden DESC, erschienen DESC, scryfall_id",
        (oracle_id, set_code or ""),
    )


def _bild(kat, druck: dict, set_code: str | None, nummer: str | None) -> str | None:
    """Scryfall-ID eines Drucks mit lokalem Bild: der Druck selbst, sonst ein Druck derselben Karte."""
    if druck["bild_vorhanden"]:
        return druck["scryfall_id"]
    if not druck.get("oracle_id"):
        return None
    ersatz = _eine(
        kat,
        "SELECT scryfall_id FROM druck WHERE oracle_id = ? AND bild_vorhanden = 1 "
        "ORDER BY (set_code = ?) DESC, (nummer = ?) DESC, (sprache = 'en') DESC, erschienen DESC, scryfall_id",
        (druck["oracle_id"], set_code or druck["set_code"], nummer or druck["nummer"]),
    )
    return ersatz["scryfall_id"] if ersatz else None


def _satz(kat, set_code: str | None) -> dict | None:
    if not set_code:
        return None
    return _eine(kat, "SELECT name, erschienen FROM satz WHERE set_code = ?", (set_code,))


def _ergebnis(kat, druck: dict, weg: str, scryfall_id: str | None, set_code: str | None, nummer: str | None) -> dict:
    """Baut die Ergebniszeile aus dem gefundenen Katalogdruck."""
    if weg == "oracle":
        # Set und Nummer bleiben die der Karte; Set-Daten aus dem Satz der Karte.
        satz = _satz(kat, set_code)
        ziel_set = set_code
        ziel_nummer = nummer
        set_name = satz["name"] if satz else None
        set_erschienen = satz["erschienen"] if satz else None
        # Die Seltenheit gehört zum Druck; sie gilt nur, wenn der Druck im Set der Karte liegt.
        seltenheit = druck["seltenheit"] if set_code and druck["set_code"] == set_code else None
    else:
        ziel_set = druck["set_code"]
        ziel_nummer = druck["nummer"]
        set_name = druck["set_name"]
        set_erschienen = druck["set_erschienen"]
        seltenheit = druck["seltenheit"]
    return {
        "scryfall_id": scryfall_id or druck["scryfall_id"],
        "oracle_id": druck["oracle_id"],
        "name": druck["name"],
        "set_code": ziel_set,
        "set_name": set_name,
        "set_erschienen": set_erschienen,
        "nummer": ziel_nummer,
        "nummer_zahl": nummer_zahl(ziel_nummer),
        "sprache": druck["sprache"],
        "manakosten": druck["manakosten"],
        "manawert": druck["manawert"],
        "farben": druck["farben"],
        "farbidentitaet": druck["farbidentitaet"],
        "farbgruppe": druck["farbgruppe"],
        "typzeile": druck["typzeile"],
        "haupttyp": druck["haupttyp"],
        "seltenheit": seltenheit,
        "bild": _bild(kat, druck, ziel_set, ziel_nummer),
        "gefunden_ueber": weg,
        # Produkt bei Cardmarket (Preise): gleicher Druck oder Sprachdruck desselben Sets und derselben Nummer;
        # über die Oracle-ID gefunden wäre es ein anderer Druck, daher dort keins. Ältere Kataloge: None.
        "cardmarket_id": druck.get("cardmarket_id") if weg != "oracle" else None,
    }


def nachschlagen(
    kat: sqlite3.Connection,
    scryfall_id: str | None,
    set_code: str | None = None,
    nummer: str | None = None,
    oracle_id: str | None = None,
) -> dict | None:
    """Kartendaten zu einer Karte; Rückfallkette ID → Sprachdruck → (Set, Nummer) → Oracle-ID.

    Gibt ein Wörterbuch mit den Spalten von `kartendaten` (ohne `geaendert`) zurück, `gefunden_ueber`
    nennt den Weg. Ohne Treffer kommt `None`. `scryfall_id` im Ergebnis ist die angefragte ID
    (auch bei Sprachdrucken), ohne Angabe die des gefundenen Katalogdrucks.
    """
    set_code = set_code.strip().lower() if set_code else None
    nummer = str(nummer).strip() if nummer not in (None, "") else None

    if scryfall_id:
        druck = _eine(kat, "SELECT * FROM druck WHERE scryfall_id = ?", (scryfall_id,))
        if druck:
            return _ergebnis(kat, druck, "id", scryfall_id, set_code, nummer)
        zuordnung = _eine(kat, "SELECT * FROM sprachdruck WHERE scryfall_id = ?", (scryfall_id,))
        if zuordnung:
            druck = _druck_nach_set_nummer(kat, zuordnung["set_code"], zuordnung["nummer"])
            if druck:
                return _ergebnis(kat, druck, "sprachdruck", scryfall_id, set_code, nummer)
            # Der Druck desselben Sets fehlt: die Zuordnung liefert wenigstens Set, Nummer und Oracle-ID.
            set_code = set_code or zuordnung["set_code"]
            nummer = nummer or zuordnung["nummer"]
            oracle_id = oracle_id or zuordnung["oracle_id"]

    if set_code and nummer:
        druck = _druck_nach_set_nummer(kat, set_code, nummer)
        if druck:
            return _ergebnis(kat, druck, "set_nummer", scryfall_id, set_code, nummer)

    if oracle_id:
        druck = _druck_nach_oracle(kat, oracle_id, set_code)
        if druck:
            return _ergebnis(kat, druck, "oracle", scryfall_id, set_code, nummer)
    return None


# --------------------------------------------------------------- App-Datenbank füllen

def spalten_in(con) -> tuple[str, ...]:
    """Die Spalten aus SPALTEN, die die Tabelle `kartendaten` dieser Datenbank hat. Eine Datenbank der
    Version 3 (vor den Preisen) kennt `cardmarket_id` noch nicht; Schreiben soll dort trotzdem gehen."""
    vorhanden = {z[1] for z in con.execute("PRAGMA table_info(kartendaten)")}
    return tuple(s for s in SPALTEN if s in vorhanden)


def einfuegen_sql(spalten: tuple[str, ...], art: str = "aktualisieren") -> str:
    """INSERT für `kartendaten`: art = "aktualisieren" (vorhandene Zeile überschreiben) oder "behalten"."""
    sql = f"INSERT INTO kartendaten ({', '.join(spalten)}) VALUES ({', '.join('?' for _ in spalten)}) "
    if art == "behalten":
        return sql + "ON CONFLICT(scryfall_id) DO NOTHING"
    return (sql + "ON CONFLICT(scryfall_id) DO UPDATE SET "
            + ", ".join(f"{s} = excluded.{s}" for s in spalten if s != "scryfall_id")
            + ", geaendert = strftime('%Y-%m-%dT%H:%M:%S', 'now')")


def schreiben(con, daten: dict, art: str = "aktualisieren") -> None:
    """Eine Zeile Kartendaten (Ergebnis von `nachschlagen`) in der Transaktion des Aufrufers schreiben."""
    spalten = spalten_in(con)
    con.execute(einfuegen_sql(spalten, art), tuple(daten.get(s) for s in spalten))


_EINFUEGEN = einfuegen_sql(SPALTEN)


def _zaehler() -> dict:
    return {"ids": 0, "karten": 0}


def uebernehmen(app_con: sqlite3.Connection, kat: sqlite3.Connection, nur_fehlende: bool = True) -> dict:
    """Füllt `kartendaten` für alle Karten der App-Datenbank, deren `scryfall_id` gesetzt ist.

    - Läuft in der Transaktion des Aufrufers: es wird weder `commit` noch `rollback` aufgerufen.
    - Schreibt nur in `kartendaten`, nie in `karte` (die Tabelle `karte` wird nur gelesen).
    - Je Scryfall-ID eine Zeile; Set und Nummer nimmt sie von der Karte mit der kleinsten ID.
    - `nur_fehlende=True` lässt vorhandene Zeilen unverändert, sonst werden sie aktualisiert.

    Rückgabe (Zählungen je Scryfall-ID und je Karte):
    `ids`, `karten`, `ohne_scryfall_id` (Karten ohne ID, nicht bearbeitet),
    `vorhanden` (übersprungen), `gefunden` (je Weg `id`, `sprachdruck`, `set_nummer`, `oracle`),
    `nicht_gefunden` (je Grund, mit `beispiele`: höchstens fünf Scryfall-IDs),
    `geschrieben` (Zeilen in `kartendaten`).
    """
    if kat is None:
        raise ValueError("Der Katalog fehlt (python -m binderapp.katalog.bau).")
    if not app_con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'kartendaten'"
    ).fetchone():
        raise RuntimeError("Die Tabelle kartendaten fehlt (Schema Version 3).")

    ohne = app_con.execute(
        "SELECT COUNT(*) FROM karte WHERE scryfall_id IS NULL OR scryfall_id = ''"
    ).fetchone()[0]
    bestand = (
        {r[0] for r in app_con.execute("SELECT scryfall_id FROM kartendaten")} if nur_fehlende else set()
    )
    # Bei MIN(id) liefert SQLite die übrigen Spalten derselben Zeile.
    posten = app_con.execute(
        "SELECT scryfall_id, COUNT(*), MIN(id), set_code, nummer, oracle_id FROM karte "
        "WHERE scryfall_id IS NOT NULL AND scryfall_id <> '' GROUP BY scryfall_id ORDER BY scryfall_id"
    ).fetchall()

    ergebnis = {
        "ids": len(posten),
        "karten": sum(p[1] for p in posten),
        "ohne_scryfall_id": ohne,
        "vorhanden": _zaehler(),
        "gefunden": {weg: _zaehler() for weg in WEGE},
        "nicht_gefunden": {},
        "geschrieben": 0,
    }
    zeilen = []
    spalten = spalten_in(app_con)
    for sid, anzahl, _min_id, set_code, nummer, oracle in posten:
        if sid in bestand:
            ergebnis["vorhanden"]["ids"] += 1
            ergebnis["vorhanden"]["karten"] += anzahl
            continue
        daten = nachschlagen(kat, sid, set_code, nummer, oracle)
        if daten is None:
            hat_rueckfall = bool((set_code and nummer) or oracle)
            grund = "id_unbekannt_rueckfall_ohne_treffer" if hat_rueckfall else "id_unbekannt_ohne_rueckfall"
            eintrag = ergebnis["nicht_gefunden"].setdefault(grund, {"ids": 0, "karten": 0, "beispiele": []})
            eintrag["ids"] += 1
            eintrag["karten"] += anzahl
            if len(eintrag["beispiele"]) < 5:
                eintrag["beispiele"].append(sid)
            continue
        zaehler = ergebnis["gefunden"][daten["gefunden_ueber"]]
        zaehler["ids"] += 1
        zaehler["karten"] += anzahl
        zeilen.append(tuple(daten.get(s) for s in spalten))

    app_con.executemany(einfuegen_sql(spalten), zeilen)
    ergebnis["geschrieben"] = len(zeilen)
    return ergebnis
