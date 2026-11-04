"""Schnittstelle der Bulkkisten (Paket E): Kisten auflisten, Karten einer Kiste serverseitig sortieren,
gruppieren, filtern und blättern, Kisten anlegen, umbenennen und leere löschen.

`routen(h)` bekommt das `Helfer`-Objekt des Servers (siehe helfer.py) und liefert Starlette-Routen,
die unter /api eingehängt werden. Raspi-tauglich: nur Standardbibliothek und lesende SQL (außer den
drei Schreibrouten), kein Katalog. Kartendaten (Manawert, Farbe, Typ, Seltenheit, Set) kommen aus der
Tabelle `kartendaten` der App-Datenbank (ab Version 3).

Lesen:
  GET    /api/kisten                    Kisten mit Posten und Karten ({"verfuegbar": false}, wenn Version < 3)
  GET    /api/kisten/optionen           feste Listen: Sortierungen, Gruppierungen, Farben, Seltenheiten, Typen
  GET    /api/kiste/{id}                Kopf einer Kiste und vorhandene Werte für die Filterpillen
  GET    /api/kiste/{id}/karten?sort=&richtung=&gruppe=&ab=&anzahl=&<filter>   Karten, sortiert und gefiltert

Schreiben (nur angemeldet, Quelle „nutzer“, mit Historie):
  POST   /api/kisten                    {"name", "etikettfarbe"?} → 201 {"id"}
  PATCH  /api/kiste/{id}                {"name"?, "etikettfarbe"?, "regalplatz"?}
  DELETE /api/kiste/{id}                nur eine leere Kiste
  POST   /api/kiste/{id}/loeschen       Kiste mit allen Karten, {name: genauer Name der Kiste}

Sicherheit: Sortier-, Gruppen- und Richtungswerte gelangen nur über feste Tabellen in den SQL-Text
(Wert aus der Anfrage → Schlüssel einer Python-Tabelle → fertiger SQL-Ausdruck). Alle Filterwerte sind
`?`-Parameter. Unbekannte Parameter, Werte und Zahlen werden mit einem deutschen Satz abgelehnt (400).
"""
from __future__ import annotations

import math
import re
import unicodedata
from contextlib import closing
from typing import NamedTuple

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from binderapp.db.datenbank import einfuegen, aendern, loeschen, version
from binderapp.db.seiten import EIGENE_FARBE
from binderapp.preise import werte
from binderapp.sortieren import einsortieren
from binderapp.web.helfer import Fehler, fehler

__all__ = ["routen"]

QUELLE = "nutzer"
NAME_LAENGE = 60
SEITE_STANDARD = 60
SEITE_MAX = 120
LISTE_MAX = 40            # so viele Werte höchstens je Mehrfachauswahl
KISTEN_AB_VERSION = 3
GROSSE_ID = 2 ** 31       # größere Kisten-IDs gibt es nicht (verhindert Überlauf in SQLite)

# ---------------------------------------------------------------------------------------------------
# Feste Listen (deutsche Texte). Die Reihenfolge ist die Reihenfolge der Sortierung und der Gruppen.

FARBEN = (("W", "Weiß"), ("U", "Blau"), ("B", "Schwarz"), ("R", "Rot"), ("G", "Grün"),
          ("M", "Mehrfarbig"), ("C", "Farblos"), ("L", "Land"))
SELTENHEITEN = (("common", "Gewöhnlich"), ("uncommon", "Ungewöhnlich"), ("rare", "Selten"),
                ("mythic", "Mythisch selten"), ("special", "Besonders"), ("bonus", "Bonus"))
TYPEN = (("kreatur", "Kreatur"), ("planeswalker", "Planeswalker"), ("spontanzauber", "Spontanzauber"),
         ("hexerei", "Hexerei"), ("artefakt", "Artefakt"), ("verzauberung", "Verzauberung"),
         ("schlacht", "Schlacht"), ("land", "Land"), ("sonstige", "Sonstige"))
ETIKETTFARBEN = (("messing", "Messing"), ("silber", "Silber"), ("kupfer", "Kupfer"),
                 ("gruen", "Grün"), ("blau", "Blau"), ("rot", "Rot"))
SPRACHEN = {"de": "Deutsch", "en": "Englisch", "es": "Spanisch", "fr": "Französisch", "it": "Italienisch",
            "pt": "Portugiesisch", "ja": "Japanisch", "ko": "Koreanisch", "ru": "Russisch",
            "zhs": "Chinesisch (vereinfacht)", "zht": "Chinesisch (traditionell)", "ph": "Phyrexianisch",
            "he": "Hebräisch", "la": "Lateinisch", "grc": "Altgriechisch", "ar": "Arabisch",
            "sa": "Sanskrit", "qya": "Quenya"}
MARKIERUNG = {"unsicher": "unsicher", "name_sicher": "druck_offen"}  # wie server.markierung


def _rang(spalte: str, werte, sonst: int | None = None) -> str:
    """SQL-Ausdruck, der einen Wert auf seinen Platz in der festen Liste abbildet."""
    faelle = " ".join(f"WHEN '{wert}' THEN {i}" for i, (wert, _) in enumerate(werte))
    return f"(CASE {spalte} {faelle} ELSE {len(werte) if sonst is None else sonst} END)"


# ---------------------------------------------------------------------------------------------------
# SQL-Bausteine. Nur Konstanten; kein Text aus der Anfrage.

KD_FEHLT = "(kd.scryfall_id IS NULL)"
SATZ = "COALESCE(kd.set_code, LOWER(k.set_code))"
SATZ_FEHLT = f"({SATZ} IS NULL)"
LAND = "(COALESCE(kd.haupttyp, '') = 'land')"
# Zahlanteil der Sammlernummer; Nummern ohne führende Ziffer (★ …) stehen hinten
NUMMER_ZAHL = "(CASE WHEN k.nummer GLOB '[0-9]*' THEN CAST(k.nummer AS INTEGER) ELSE 1000000000 END)"
FARB_RANG = _rang("kd.farbgruppe", FARBEN)
SELTENHEIT_RANG = _rang("kd.seltenheit", SELTENHEITEN)
TYP_RANG = _rang("kd.haupttyp", TYPEN, sonst=len(TYPEN) - 1)
MANA_STUFE = "MIN(CAST(kd.manawert AS INTEGER), 7)"
# Preis (Durchschnitt 7 Tage, Foil-Karten mit Foil-Preis) des neuesten Tages; `pr` gibt es nur bei dieser
# Sortierung (PREIS_JOIN). Karten ohne Preis stehen in beiden Richtungen zuletzt.
PREIS = "(CASE WHEN k.foil THEN pr.avg7_foil ELSE pr.avg7 END)"
PREIS_JOIN = (" LEFT JOIN preis pr ON pr.cardmarket_id = kd.cardmarket_id"
              " AND pr.tag = (SELECT MAX(tag) FROM preisabruf)")
NAME_NOCASE = "k.name COLLATE NOCASE"


def _buchstabe_sql() -> str:
    """Anfangsbuchstabe des Namens: A–Z, Akzente und Umlaute wie der Grundbuchstabe, sonst „#“."""
    u = "UPPER(SUBSTR(k.name, 1, 1))"
    je: dict[str, list[str]] = {}
    for code in range(0xC0, 0x250):
        zeichen = chr(code)
        grund = unicodedata.normalize("NFD", zeichen)[0]
        if zeichen.isalpha() and grund.isascii() and grund.isalpha():
            je.setdefault(grund.upper(), []).append(zeichen)
    for zeichen, grund in (("Æ", "A"), ("æ", "A"), ("Œ", "O"), ("œ", "O"), ("Ø", "O"), ("ø", "O")):
        je.setdefault(grund, []).append(zeichen)
    teile = [f"WHEN {u} BETWEEN 'A' AND 'Z' THEN {u}"]
    for grund in sorted(je):
        liste = ", ".join(f"'{z}'" for z in je[grund])
        teile.append(f"WHEN {u} IN ({liste}) THEN '{grund}'")
    return "(CASE " + " ".join(teile) + " ELSE '#' END)"


BUCHSTABE = _buchstabe_sql()


class Sortierung(NamedTuple):
    text: str
    richtung: str                 # Standardrichtung: "auf" oder "ab"
    fest: tuple[str, ...]         # Schlüssel, die bei jeder Richtung gleich bleiben (zum Beispiel „ohne Kartendaten zuletzt“)
    schluessel: tuple[str, ...]   # Schlüssel, die mit der Richtung umkehren


SORTIERUNGEN = {
    "name": Sortierung("Name", "auf", (), (NAME_NOCASE,)),
    "set_datum": Sortierung("Set (Erscheinungsdatum)", "auf", (KD_FEHLT,),
                            ("COALESCE(kd.set_erschienen, '9999-99-99')", "COALESCE(kd.set_code, '')",
                             NUMMER_ZAHL, "COALESCE(k.nummer, '')")),
    "set_code": Sortierung("Set-Kürzel", "auf", (SATZ_FEHLT,), (SATZ, NUMMER_ZAHL, "COALESCE(k.nummer, '')")),
    "manawert": Sortierung("Manawert", "auf", (KD_FEHLT, LAND), ("kd.manawert",)),
    "farbe": Sortierung("Farbe", "auf", (KD_FEHLT,), (FARB_RANG,)),
    "seltenheit": Sortierung("Seltenheit", "auf", (KD_FEHLT,), (SELTENHEIT_RANG,)),
    "typ": Sortierung("Typ", "auf", (KD_FEHLT,), (TYP_RANG,)),
    "sprache": Sortierung("Sprache", "auf", ("(k.sprache IS NULL)",), ("COALESCE(k.sprache, '')",)),
    "nummer": Sortierung("Sammlernummer", "auf", (), (NUMMER_ZAHL, "COALESCE(k.nummer, '')")),
    "anzahl": Sortierung("Anzahl", "ab", (), ("k.anzahl",)),
    "neu": Sortierung("Zuletzt hinzugefügt", "ab", (), ("k.angelegt", "k.id")),
    "foil": Sortierung("Foil", "ab", (), ("k.foil",)),
    "preis": Sortierung("Preis (Ø 7 Tage)", "ab", (f"({PREIS} IS NULL)",), (PREIS,)),
}
PREIS_AB_VERSION = 4
RICHTUNGEN = {"auf": "ASC", "ab": "DESC"}


class Gruppierung(NamedTuple):
    text: str
    rang: tuple[str, ...]   # Reihenfolge der Gruppen (aufsteigend); der Schlüssel kommt zuletzt dazu
    schluessel: str         # SQL-Ausdruck des Gruppenschlüssels; leer = ohne Kartendaten
    extra: str              # SQL-Ausdruck für Zusatztext (Set-Name) oder NULL


def _mit_kartendaten(ausdruck: str) -> str:
    return f"(CASE WHEN {KD_FEHLT} THEN '' ELSE {ausdruck} END)"


GRUPPIERUNGEN = {
    "set": Gruppierung("Set", (KD_FEHLT, "COALESCE(kd.set_erschienen, '9999-99-99')"),
                       _mit_kartendaten("COALESCE(kd.set_code, '?')"), "kd.set_name"),
    "farbe": Gruppierung("Farbe", (KD_FEHLT, FARB_RANG), _mit_kartendaten("kd.farbgruppe"), "NULL"),
    "manawert": Gruppierung(
        "Manawert", (KD_FEHLT, LAND, MANA_STUFE),
        _mit_kartendaten(f"CASE WHEN {LAND} THEN 'land' WHEN kd.manawert >= 7 THEN '7+' "
                         f"ELSE CAST(CAST(kd.manawert AS INTEGER) AS TEXT) END"), "NULL"),
    "seltenheit": Gruppierung("Seltenheit", (KD_FEHLT, SELTENHEIT_RANG),
                              _mit_kartendaten("COALESCE(kd.seltenheit, 'sonstige')"), "NULL"),
    "typ": Gruppierung("Typ", (KD_FEHLT, TYP_RANG), _mit_kartendaten("kd.haupttyp"), "NULL"),
    "sprache": Gruppierung("Sprache", ("(k.sprache IS NULL)",), "COALESCE(k.sprache, '')", "NULL"),
    "buchstabe": Gruppierung("Anfangsbuchstabe", (f"({BUCHSTABE} = '#')",), BUCHSTABE, "NULL"),
    # Fächer der Kiste (Sortierregeln, ab Version 6): das Fach je Karte berechnet `fach_vorbereiten` vorab in die
    # temporäre Tabelle fachwahl; Karten ohne passendes Fach stehen zuletzt.
    "fach": Gruppierung("Fach", (f"COALESCE(fw.platz, {10 ** 6})",), f"COALESCE(fw.platz, {10 ** 6})", "fw.name"),
}
FACH_JOIN = " LEFT JOIN temp.fachwahl fw ON fw.karte_id = k.id"

# Gesamtsammlung (Paket L): Bereich „alles“ statt einer Kiste; dazu Ort und Quelle. Die Tabellen ki (Kiste),
# t, se, b (Tasche, Seite, Binder) gibt es nur in dieser Abfrage (VON_SAMMLUNG).
QUELLEN = (("scan", "Scan"), ("manabox", "ManaBox"), ("manuell", "Von Hand"))
# Orte einer Karte: Binder-Tasche, Kiste oder (ab Version 8) Deck
ORT_DA = "(k.kiste_id IS NOT NULL OR k.tasche_id IS NOT NULL OR k.deck_id IS NOT NULL)"
ORT_SCHLUESSEL = ("(CASE WHEN k.kiste_id IS NOT NULL THEN 'k' || k.kiste_id WHEN k.deck_id IS NOT NULL "
                  "THEN 'd' || k.deck_id ELSE 'b' || b.id END)")
ORT_RANG = "(CASE WHEN k.kiste_id IS NOT NULL THEN 1 WHEN k.deck_id IS NOT NULL THEN 2 ELSE 0 END)"
SAMMLUNG_GRUPPIERUNGEN = {
    **GRUPPIERUNGEN,
    "ort": Gruppierung("Ort", (ORT_RANG, "COALESCE(ki.regalplatz, b.regalplatz, de.regalplatz)"),
                       ORT_SCHLUESSEL, "COALESCE(ki.name, b.name, de.name)"),
    "quelle": Gruppierung("Quelle", (_rang("k.quelle", QUELLEN),), "k.quelle", "NULL"),
}
ALLE_GRUPPIERUNGEN = SAMMLUNG_GRUPPIERUNGEN   # zum Nachschlagen nach der Prüfung in anfrage_lesen
# Mögliche Doppel: gleicher Druck, gleiche Sprache und gleiches Foil in einer Kiste und in einem Binder.
# Das „+“ sperrt den Nutzerindex, sonst durchsucht SQLite alle Karten des Nutzers (5,7 s statt Millisekunden).
DOPPEL = ("EXISTS (SELECT 1 FROM karte d WHERE d.scryfall_id = k.scryfall_id AND +d.nutzer_id = k.nutzer_id "
          "AND d.id <> k.id AND d.sprache IS k.sprache AND d.foil = k.foil AND "
          "((k.tasche_id IS NOT NULL AND d.kiste_id IS NOT NULL) OR (k.kiste_id IS NOT NULL AND d.tasche_id IS NOT NULL)))")


def gruppentext(gruppe: str, schluessel: str, extra) -> str:
    """Beschriftung einer Gruppe (Reiter, Trennkarte)."""
    if gruppe == "buchstabe":
        return schluessel
    if gruppe == "ort":
        return f"{ {'k': 'Kiste', 'd': 'Deck'}.get(schluessel[:1], 'Binder')} {extra}"
    if gruppe == "quelle":
        return dict(QUELLEN).get(schluessel, schluessel)
    if gruppe == "sprache":
        return SPRACHEN.get(schluessel, schluessel.upper()) if schluessel else "Ohne Angabe"
    if gruppe == "fach":
        return extra or "Ohne Fach"
    if schluessel == "":
        return "Ohne Kartendaten"
    if gruppe == "set":
        if schluessel == "?":
            return "Ohne Set"
        return f"{schluessel.upper()} · {extra}" if extra else schluessel.upper()
    if gruppe == "farbe":
        return dict(FARBEN).get(schluessel, schluessel)
    if gruppe == "manawert":
        return "Länder" if schluessel == "land" else schluessel
    if gruppe == "seltenheit":
        return dict(SELTENHEITEN).get(schluessel, "Sonstige")
    return dict(TYPEN).get(schluessel, "Sonstige")


# ---------------------------------------------------------------------------------------------------
# Anfrage lesen und prüfen

SKALARE = ("sort", "richtung", "gruppe", "ab", "anzahl", "foil", "mw_von", "mw_bis", "anzahl_min", "q")
LISTEN = ("farbe", "seltenheit", "typ", "sprache", "set")
SAMMLUNG_SKALARE = ("doppel",)
SAMMLUNG_LISTEN = ("ort", "quelle")
ORT_MUSTER = re.compile(r"[kbd][0-9]{1,9}")
ZAHL = re.compile(r"[0-9]{1,9}")
SPRACHE_MUSTER = re.compile(r"[a-z]{2,3}")
SET_MUSTER = re.compile(r"[a-z0-9]{2,8}")


def _auswahl(werte) -> str:
    return ", ".join(werte)


def _skalar(roh: dict, name: str):
    wert = roh.get(name)
    if not wert:
        return None
    if len(wert) > 1:
        raise Fehler(f"Der Parameter „{name}“ darf nur einmal vorkommen.")
    return wert[0] if wert[0] != "" else None


def _zahl(roh: dict, name: str, von: int, bis: int, standard: int | None = None) -> int | None:
    wert = _skalar(roh, name)
    if wert is None:
        return standard
    if not ZAHL.fullmatch(wert) or not von <= int(wert) <= bis:
        raise Fehler(f"„{name}“ muss eine ganze Zahl von {von} bis {bis} sein.")
    return int(wert)


def _liste(roh: dict, name: str, erlaubt=None, muster=None, gross: bool = False, hinweis: str = "") -> list[str]:
    """Mehrfachauswahl als kommagetrennte Werte (auch mehrfach angegeben); höchstens LISTE_MAX Werte."""
    ergebnis: list[str] = []
    for teil in roh.get(name, []):
        for wert in teil.split(","):
            wert = wert.strip()
            if not wert:
                continue
            if gross:
                wert = wert.upper()
            if (erlaubt is not None and wert not in erlaubt) or (muster is not None and not muster.fullmatch(wert)):
                raise Fehler(f"Unbekannter Wert bei „{name}“. {hinweis}".strip())
            if wert not in ergebnis:
                ergebnis.append(wert)
    if len(ergebnis) > LISTE_MAX:
        raise Fehler(f"Bei „{name}“ sind höchstens {LISTE_MAX} Werte möglich.")
    return ergebnis


class Anfrage(NamedTuple):
    sort: str
    richtung: str
    gruppe: str | None
    ab: int
    anzahl: int
    where: tuple[str, ...]
    args: tuple
    kiste: int | None = 0   # Bereich: eine Kiste oder None für die ganze Sammlung (Binder und Kisten)


def anfrage_lesen(query_params, kiste_id: int | None, nutzer: int) -> Anfrage:
    """Prüft alle Parameter und baut die WHERE-Bedingungen (nur mit `?`-Platzhaltern).
    `kiste_id` None: Bereich ganze Sammlung, dann sind auch Ort, Quelle, Doppel und deren Gruppen erlaubt."""
    sammlung = kiste_id is None
    skalare = (*SKALARE, *SAMMLUNG_SKALARE) if sammlung else SKALARE
    listen = (*LISTEN, *SAMMLUNG_LISTEN) if sammlung else LISTEN
    gruppierungen = SAMMLUNG_GRUPPIERUNGEN if sammlung else GRUPPIERUNGEN
    roh: dict[str, list[str]] = {}
    for name, wert in query_params.multi_items():
        if name not in skalare and name not in listen:
            raise Fehler("Unbekannter Parameter. Erlaubt: " + _auswahl(sorted((*skalare, *listen))) + ".")
        roh.setdefault(name, []).append(wert)

    sort = _skalar(roh, "sort") or "name"
    if sort not in SORTIERUNGEN:
        raise Fehler("Unbekannte Sortierung. Möglich: " + _auswahl(SORTIERUNGEN) + ".")
    richtung = _skalar(roh, "richtung") or SORTIERUNGEN[sort].richtung
    if richtung not in RICHTUNGEN:
        raise Fehler("Unbekannte Richtung. Möglich: " + _auswahl(RICHTUNGEN) + ".")
    gruppe = _skalar(roh, "gruppe")
    if gruppe is not None and (gruppe not in gruppierungen or (sammlung and gruppe == "fach")):
        raise Fehler("Unbekannte Gruppierung. Möglich: " + _auswahl(g for g in gruppierungen
                                                                    if not (sammlung and g == "fach")) + ".")
    ab = _zahl(roh, "ab", 0, 10 ** 9 - 1, 0)
    anzahl = _zahl(roh, "anzahl", 1, SEITE_MAX, SEITE_STANDARD)

    # die beiden ersten Bedingungen setzt `_wo` je nach Abfrage
    if sammlung:
        where = ["k.nutzer_id = ?", ORT_DA]
        args: list = [nutzer]
        orte = _liste(roh, "ort", muster=ORT_MUSTER,
                      hinweis="Erwartet werden Orte wie k12 (Kiste), b3 (Binder) oder d5 (Deck).")
        kisten_ids = [int(o[1:]) for o in orte if o[0] == "k"]
        binder_ids = [int(o[1:]) for o in orte if o[0] == "b"]
        deck_ids = [int(o[1:]) for o in orte if o[0] == "d"]
        teile = []
        if kisten_ids:
            teile.append(f"k.kiste_id IN ({','.join('?' * len(kisten_ids))})")
        if binder_ids:
            teile.append(f"b.id IN ({','.join('?' * len(binder_ids))})")
        if deck_ids:
            teile.append(f"k.deck_id IN ({','.join('?' * len(deck_ids))})")
        if teile:
            where.append("(" + " OR ".join(teile) + ")")
            args += kisten_ids + binder_ids + deck_ids
        quellen = _liste(roh, "quelle", erlaubt={q for q, _ in QUELLEN},
                         hinweis="Möglich: " + _auswahl(q for q, _ in QUELLEN) + ".")
        if quellen:
            where.append(f"k.quelle IN ({','.join('?' * len(quellen))})")
            args += quellen
        doppel = _skalar(roh, "doppel")
        if doppel is not None:
            if doppel not in ("0", "1"):
                raise Fehler("„doppel“ muss 0 oder 1 sein.")
            where.append(DOPPEL if doppel == "1" else f"NOT {DOPPEL}")
    else:
        where = ["k.nutzer_id = ?", "k.kiste_id = ?"]
        args = [nutzer, kiste_id]

    farben = _liste(roh, "farbe", erlaubt={f for f, _ in FARBEN}, gross=True,
                    hinweis="Möglich: " + _auswahl(f for f, _ in FARBEN) + ".")
    if farben:
        where.append(f"kd.farbgruppe IN ({','.join('?' * len(farben))})")
        args += farben
    mw_von = _zahl(roh, "mw_von", 0, 20)
    mw_bis = _zahl(roh, "mw_bis", 0, 20)
    if mw_von is not None and mw_bis is not None and mw_von > mw_bis:
        raise Fehler("„mw_von“ darf nicht größer sein als „mw_bis“.")
    if mw_von is not None:
        where.append("kd.manawert >= ?")
        args.append(mw_von)
    if mw_bis is not None:
        where.append("kd.manawert <= ?")
        args.append(mw_bis)
    seltenheiten = _liste(roh, "seltenheit", erlaubt={s for s, _ in SELTENHEITEN},
                          hinweis="Möglich: " + _auswahl(s for s, _ in SELTENHEITEN) + ".")
    if seltenheiten:
        where.append(f"kd.seltenheit IN ({','.join('?' * len(seltenheiten))})")
        args += seltenheiten
    typen = _liste(roh, "typ", erlaubt={t for t, _ in TYPEN}, hinweis="Möglich: " + _auswahl(t for t, _ in TYPEN) + ".")
    if typen:
        where.append(f"kd.haupttyp IN ({','.join('?' * len(typen))})")
        args += typen
    sprachen = _liste(roh, "sprache", muster=SPRACHE_MUSTER, hinweis="Erwartet werden Sprachkürzel wie de oder en.")
    if sprachen:
        where.append(f"k.sprache IN ({','.join('?' * len(sprachen))})")
        args += sprachen
    foil = _skalar(roh, "foil")
    if foil is not None:
        if foil not in ("0", "1"):
            raise Fehler("„foil“ muss 0 oder 1 sein.")
        where.append("k.foil = ?")
        args.append(int(foil))
    saetze = _liste(roh, "set", muster=SET_MUSTER, hinweis="Erwartet werden Set-Kürzel wie stx.")
    if saetze:
        where.append(f"{SATZ} IN ({','.join('?' * len(saetze))})")
        args += saetze
    anzahl_min = _zahl(roh, "anzahl_min", 1, 999)
    if anzahl_min is not None:
        where.append("k.anzahl >= ?")
        args.append(anzahl_min)
    text = (_skalar(roh, "q") or "").strip()
    if len(text) > NAME_LAENGE:
        raise Fehler(f"Der Suchtext ist zu lang (höchstens {NAME_LAENGE} Zeichen).")
    if text:
        muster = "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(k.name LIKE ? ESCAPE '\\' OR kd.name LIKE ? ESCAPE '\\')")
        args += [muster, muster]
    return Anfrage(sort, richtung, gruppe, ab, anzahl, tuple(where), tuple(args), kiste_id)


# ---------------------------------------------------------------------------------------------------
# SQL

VON = "FROM karte k LEFT JOIN kartendaten kd ON kd.scryfall_id = k.scryfall_id"
VON_SAMMLUNG = (f"{VON} LEFT JOIN kiste ki ON ki.id = k.kiste_id LEFT JOIN tasche t ON t.id = k.tasche_id "
                "LEFT JOIN seite se ON se.id = t.seite_id LEFT JOIN binder b ON b.id = se.binder_id "
                "LEFT JOIN deck de ON de.id = k.deck_id")

SPALTEN = f"""k.id, k.name, {SATZ} AS satz, kd.set_name, k.nummer, k.sprache, k.foil, k.anzahl,
       COALESCE(k.bild, kd.bild) AS bild, kd.manakosten, kd.manawert, kd.farben, kd.farbgruppe,
       kd.typzeile, kd.haupttyp, kd.seltenheit, k.status, k.geprueft"""
SPALTEN_SAMMLUNG = f"{SPALTEN}, k.quelle, k.kiste_id"
# Ort je Karte, nur für die Karten einer Seite (seite_lesen)
SQL_ORT = ("SELECT k.id, k.kiste_id, ki.name AS kiste_name, b.id AS binder_id, b.name AS binder_name, "
           "se.nummer AS seite, t.zeile, t.spalte, k.deck_id, de.name AS deck_name FROM karte k "
           "LEFT JOIN kiste ki ON ki.id = k.kiste_id "
           "LEFT JOIN tasche t ON t.id = k.tasche_id LEFT JOIN seite se ON se.id = t.seite_id "
           "LEFT JOIN binder b ON b.id = se.binder_id LEFT JOIN deck de ON de.id = k.deck_id WHERE k.id IN ({platz})")


def von(a: Anfrage) -> str:
    """Die Tabellen der Orte (ki, t, se, b) nur, wenn Gruppe oder Filter „Ort“ sie brauchen (schneller)."""
    preis = PREIS_JOIN if a.sort == "preis" else ""
    if a.kiste is None and (a.gruppe == "ort" or any("b.id IN" in w for w in a.where)):
        return VON_SAMMLUNG + preis
    return VON + preis + (FACH_JOIN if a.gruppe == "fach" else "")


def fach_vorbereiten(con, nutzer: int, kiste_id: int) -> None:
    """Fach je Karte der Kiste in die temporäre Tabelle fachwahl (nur diese Verbindung; geht auch nur lesend)."""
    con.execute("CREATE TEMP TABLE IF NOT EXISTS fachwahl (karte_id INTEGER PRIMARY KEY, platz INTEGER, name TEXT)")
    con.execute("DELETE FROM temp.fachwahl")
    faecher = [f["name"] for f in einsortieren.faecher_lesen(con, nutzer, kiste_id) if f["gueltig"]]
    if not faecher:
        return
    ids = [z[0] for z in con.execute("SELECT id FROM karte WHERE nutzer_id = ? AND kiste_id = ?", (nutzer, kiste_id))]
    zeilen = [(kid, faecher.index(name), name)
              for kid, name in einsortieren.faecher_von(con, nutzer, kiste_id, ids).items() if name is not None]
    con.executemany("INSERT INTO temp.fachwahl VALUES (?, ?, ?)", zeilen)


def _wo(a: Anfrage, nach_index: bool = False) -> str:
    """WHERE-Text. Ohne eigenen Kistenindex wählt SQLite je nach Bedingung einen Index; das „+“ vor einer
    Spalte sperrt ihn für diese Bedingung. Zum Sortieren ist der Durchlauf in Tabellenreihenfolge (Index über
    den Nutzer) schneller als in Namensreihenfolge (Kistenindex); nur die Sortierung nach Name nutzt den
    Kistenindex, weil er dort die Sortierung ersetzt (Messung: work/kisten-api-bauer/messung.md)."""
    if a.kiste is None:
        erste = ("+k.nutzer_id = ?", ORT_DA) if nach_index else ("k.nutzer_id = ?", ORT_DA)
    else:
        erste = ("+k.nutzer_id = ?", "k.kiste_id = ?") if nach_index else ("k.nutzer_id = ?", "+k.kiste_id = ?")
    return " AND ".join((*erste, *a.where[2:]))


def sql_ids(a: Anfrage) -> str:
    """Alle Karten-IDs der gefilterten Auswahl (Mehrfachauswahl „alle gefilterten“), ohne Seiten. Die Gruppe ändert
    die Auswahl nicht (und „fach“ hätte hier keine Tabelle fachwahl)."""
    a = a._replace(gruppe=None)
    return f"SELECT k.id {von(a)} WHERE {_wo(a)} ORDER BY k.id"


def sql_karten(a: Anfrage) -> str:
    """Seite der Karten einer Kiste; Reihenfolge: Gruppe, gewählte Sortierung, Name ohne Groß/Klein, ID."""
    s = SORTIERUNGEN[a.sort]
    teile: list[str] = []
    gruppen_spalten = ""
    if a.gruppe:
        g = ALLE_GRUPPIERUNGEN[a.gruppe]
        teile += [*g.rang, g.schluessel]
        gruppen_spalten = f", {g.schluessel} AS gk, {g.extra} AS ge"
    teile += s.fest
    richtung = RICHTUNGEN[a.richtung]
    teile += [f"{k} {richtung}" for k in s.schluessel]
    # Ohne Name und ID als letzte Schlüssel wäre die Reihenfolge nicht eindeutig. Steht der Name schon
    # vorn (Sortierung „name“), entfällt der doppelte Schlüssel, damit SQLite den Index nutzen kann.
    if NAME_NOCASE not in s.schluessel:
        teile.append(NAME_NOCASE)
    if "k.id" not in s.schluessel:
        teile.append("k.id")
    spalten = SPALTEN_SAMMLUNG if a.kiste is None else SPALTEN
    return (f"SELECT {spalten}{gruppen_spalten} {von(a)} WHERE {_wo(a, nach_index=a.sort == 'name' and not a.gruppe)} "
            f"ORDER BY {', '.join(teile)} LIMIT ? OFFSET ?")


def sql_gesamt(a: Anfrage) -> str:
    return f"SELECT COUNT(*) AS posten, COALESCE(SUM(k.anzahl), 0) AS karten {von(a)} WHERE {_wo(a)}"


def sql_gruppen(a: Anfrage) -> str:
    """Gruppen in Anzeigereihenfolge mit Posten und Karten; `ab` ergibt sich aus der Summe der Posten davor."""
    g = ALLE_GRUPPIERUNGEN[a.gruppe]
    raenge = [*g.rang, g.schluessel]
    spalten = ", ".join(f"{r} AS r{i}" for i, r in enumerate(raenge))
    ordnung = ", ".join(f"r{i}" for i in range(len(raenge)))
    return (f"SELECT {spalten}, {g.schluessel} AS gk, MIN({g.extra}) AS ge, COUNT(*) AS posten, "
            f"COALESCE(SUM(k.anzahl), 0) AS karten {von(a)} WHERE {_wo(a)} "
            f"GROUP BY {ordnung} ORDER BY {ordnung}")


def seite_lesen(con, a: Anfrage) -> dict:
    """Eine Seite Karten mit Gesamtzahl und Gruppen (Kistenansicht und Gesamtsammlung)."""
    if a.sort == "preis" and version(con) < PREIS_AB_VERSION:
        raise Fehler("Preise gibt es erst ab Datenbankversion 4.", 409)
    gesamt = con.execute(sql_gesamt(a), a.args).fetchone()
    zeilen = con.execute(sql_karten(a), (*a.args, a.anzahl, a.ab)).fetchall()
    gruppen = None
    if a.gruppe:
        gruppen = []
        davor = 0
        for g in con.execute(sql_gruppen(a), a.args):
            gruppen.append({"schluessel": g["gk"], "text": gruppentext(a.gruppe, g["gk"], g["ge"]),
                            "posten": g["posten"], "karten": g["karten"], "ab": davor})
            davor += g["posten"]
    ende = a.ab + len(zeilen)
    karten = [karte_dict(z, a.gruppe) for z in zeilen]
    preise = werte.preise_fuer_karten(con, [k["id"] for k in karten])   # ab Version 4, sonst leer
    for k in karten:
        k["preis"] = preise.get(k["id"])
    if a.kiste is None and karten:
        # Kennzeichen „mögliches Doppel“ nur für die Karten dieser Seite (im Sortierlauf wäre es für alle teuer)
        ids = [k["id"] for k in karten]
        platz = ",".join("?" * len(ids))
        doppelt = {z[0] for z in con.execute(f"SELECT k.id FROM karte k WHERE k.id IN ({platz}) AND {DOPPEL}", ids)}
        orte = {z["id"]: z for z in con.execute(SQL_ORT.format(platz=platz), ids)}
        for k in karten:
            k["doppel"] = k["id"] in doppelt
            z = orte[k["id"]]
            if z["kiste_id"] is not None:
                k["ort"] = {"art": "kiste", "id": z["kiste_id"], "name": z["kiste_name"],
                            "text": f"Kiste {z['kiste_name']}"}
            elif z["deck_id"] is not None:
                k["ort"] = {"art": "deck", "id": z["deck_id"], "name": z["deck_name"], "text": f"Deck {z['deck_name']}"}
            elif z["binder_id"] is not None:
                k["ort"] = {"art": "binder", "id": z["binder_id"], "name": z["binder_name"], "seite": z["seite"],
                            "zeile": z["zeile"], "spalte": z["spalte"],
                            "text": f"Binder {z['binder_name']} · Seite {z['seite']} · {z['zeile']}/{z['spalte']}"}
    return {
        "gesamt": {"posten": gesamt["posten"], "karten": gesamt["karten"]},
        "ab": a.ab, "anzahl": a.anzahl,
        "weiter": ende if zeilen and ende < gesamt["posten"] else None,
        "karten": karten,
        "gruppen": gruppen,
    }


def _zahlwert(wert):
    if wert is None:
        return None
    return int(wert) if float(wert).is_integer() else wert


def karte_dict(z, gruppe: str | None) -> dict:
    karte = {
        "id": z["id"], "name": z["name"], "set": z["satz"], "set_name": z["set_name"], "nummer": z["nummer"],
        "sprache": z["sprache"], "foil": bool(z["foil"]), "anzahl": z["anzahl"], "bild": z["bild"],
        "manakosten": z["manakosten"], "manawert": _zahlwert(z["manawert"]), "farben": z["farben"],
        "farbgruppe": z["farbgruppe"], "typzeile": z["typzeile"], "haupttyp": z["haupttyp"],
        "seltenheit": z["seltenheit"], "status": z["status"], "geprueft": bool(z["geprueft"]),
        "markierung": MARKIERUNG.get(z["status"]), "gruppe": None,
    }
    if gruppe:
        karte["gruppe"] = {"schluessel": z["gk"], "text": gruppentext(gruppe, z["gk"], z["ge"])}
    if "quelle" in z.keys():   # Gesamtsammlung: Quelle; Ort und „mögliches Doppel“ setzt seite_lesen
        karte["quelle"] = z["quelle"]
        karte["doppel"] = False
        karte["ort"] = None
    return karte


# ---------------------------------------------------------------------------------------------------
# Namen und Farben der Kisten

def _name_pruefen(name) -> str:
    if not isinstance(name, str):
        raise Fehler("Bitte einen Namen für die Kiste eingeben.")
    name = unicodedata.normalize("NFC", name).strip()
    if not name:
        raise Fehler("Bitte einen Namen für die Kiste eingeben.")
    if len(name) > NAME_LAENGE:
        raise Fehler(f"Der Name ist zu lang (höchstens {NAME_LAENGE} Zeichen).")
    if any(unicodedata.category(z).startswith("C") for z in name):
        raise Fehler("Der Name enthält unzulässige Zeichen.")
    return name


def _etikettfarbe_pruefen(farbe) -> str:
    """Farbe der Kiste: Messing, Silber, Kupfer (dunkle Kiste mit Metalletikett), eine feste Farbe oder eine eigene
    aus dem Farbrad als #rrggbb (dann ist die ganze Kiste farbig; Wunsch des Nutzers, 04.11.2026)."""
    wert = str(farbe).strip().lower().replace("ü", "ue") if isinstance(farbe, str) else ""
    if wert not in {f for f, _ in ETIKETTFARBEN} and not EIGENE_FARBE.fullmatch(wert):
        raise Fehler("Unbekannte Farbe. Möglich: " + _auswahl(f for f, _ in ETIKETTFARBEN)
                     + " oder eine eigene Farbe wie #3a6f9c.")
    return wert


def _name_frei(con, nutzer: int, name: str, ausser: int | None = None) -> None:
    if con.execute("SELECT id FROM kiste WHERE nutzer_id = ? AND name = ? COLLATE NOCASE AND id IS NOT ?",
                   (nutzer, name, ausser)).fetchone() is not None:
        raise Fehler(f"Eine Kiste „{name}“ gibt es schon.", 409)


def _ab_version_3(con, status: int) -> None:
    if version(con) < KISTEN_AB_VERSION:
        raise Fehler("Bulkkisten gibt es erst ab Datenbankversion 3. Bitte zuerst: python -m binderapp.db.migrieren",
                     status)


def _ab_version_8(con) -> None:
    """Gesamtsammlung und Sets kennen ab dem Deckbau den Ort „Deck“ (Spalte karte.deck_id, Version 8). Das Add-on
    migriert beim Start selbst; eine ältere Datenbank am PC bekommt eine klare Meldung statt eines Fehlers 500."""
    if version(con) < 8:
        raise Fehler("Die Gesamtsammlung braucht die Datenbankversion 8. Bitte zuerst sichern und migrieren: "
                     "python -m binderapp.db.migrieren", 409)


def kiste_mit_karten_loeschen(con, nutzer: int, kiste_id: int) -> dict:
    """Löscht die Kiste mit ihren Karten und Fächern, alles mit Historie (Wunsch des Nutzers, 01.11.2026). Scan-Fotos
    verlieren den Verweis auf ihre Karte (sichtbar mit Historie), die Fotos selbst bleiben. Eine Kiste, in die eine
    Sortierregel einsortiert, bleibt stehen. Der Scan-Eingang entsteht beim nächsten Scan neu."""
    regeln = einsortieren.kiste_benutzt(con, nutzer, kiste_id)
    if regeln:
        raise Fehler("Diese Kiste ist Ziel der Sortierregel „" + "“, „".join(regeln[:3]) + "“. "
                     "Erst die Regel ändern oder löschen.", 409)
    zahl = {"posten": 0, "karten": 0}
    hat_scan = version(con) >= 5
    for k in con.execute("SELECT id, anzahl FROM karte WHERE nutzer_id = ? AND kiste_id = ? ORDER BY id",
                         (nutzer, kiste_id)).fetchall():
        if hat_scan:
            for f in con.execute("SELECT id FROM scan_foto WHERE karte_id = ?", (k["id"],)).fetchall():
                aendern(con, nutzer, QUELLE, "scan_foto", f["id"], {"karte_id": None})
        loeschen(con, nutzer, QUELLE, "karte", k["id"])
        zahl["posten"] += 1
        zahl["karten"] += k["anzahl"]
    einsortieren.faecher_entfernen(con, nutzer, kiste_id)
    alt = loeschen(con, nutzer, QUELLE, "kiste", kiste_id)
    return {"id": alt["id"], "name": alt["name"], **zahl}


def _kiste_holen(con, nutzer: int, kiste_id: int):
    zeile = con.execute("SELECT * FROM kiste WHERE id = ? AND nutzer_id = ?", (kiste_id, nutzer)).fetchone()
    if zeile is None:
        raise Fehler("Kiste nicht gefunden.", 404)
    return zeile


# ---------------------------------------------------------------------------------------------------

SQL_KISTEN = """
SELECT ki.id, ki.name, ki.etikettfarbe, ki.regalplatz, COUNT(k.id) AS posten, COALESCE(SUM(k.anzahl), 0) AS karten
FROM kiste ki LEFT JOIN karte k ON k.kiste_id = ki.id AND +k.nutzer_id = ki.nutzer_id
WHERE ki.nutzer_id = ?
GROUP BY ki.id
ORDER BY ki.regalplatz, ki.id
"""

SQL_KOPF = f"""
SELECT COUNT(*) AS posten, COALESCE(SUM(k.anzahl), 0) AS karten, MAX(kd.manawert) AS manawert_max,
       COALESCE(MAX(k.foil), 0) AS foils, COALESCE(SUM(kd.scryfall_id IS NULL), 0) AS ohne_kartendaten
{VON} WHERE k.nutzer_id = ? AND +k.kiste_id = ?
"""

SQL_SAETZE = f"""
SELECT {SATZ} AS code, MIN(kd.set_name) AS name, MIN(kd.set_erschienen) AS erschienen, COUNT(*) AS posten
{VON} WHERE k.nutzer_id = ? AND +k.kiste_id = ? AND {SATZ} IS NOT NULL
GROUP BY code ORDER BY COALESCE(MIN(kd.set_erschienen), '9999-99-99'), code
"""

SQL_SPRACHEN = f"""
SELECT k.sprache AS code, COUNT(*) AS posten FROM karte k
WHERE k.nutzer_id = ? AND +k.kiste_id = ? AND k.sprache IS NOT NULL
GROUP BY k.sprache ORDER BY posten DESC, code
"""


def _lesen(handler):
    """Hülle für lesende Routen: `Fehler` werden zu {"fehler": …} mit dem passenden Status."""
    def huelle(request):
        try:
            return handler(request)
        except Fehler as f:
            return fehler(str(f), f.status)
    huelle.__name__ = handler.__name__
    return huelle


def _kiste_id(request) -> int:
    kiste_id = request.path_params["id"]
    if kiste_id >= GROSSE_ID:
        raise Fehler("Kiste nicht gefunden.", 404)
    return kiste_id


def routen(h) -> list[Route]:
    """Routen der Bulkkisten für `Mount("/api", routes=routen(h))`."""

    # ------------------------------------------------------------------ Lesen

    @_lesen
    def kisten(request):
        with closing(h.db()) as con:
            if version(con) < KISTEN_AB_VERSION:
                return JSONResponse({"verfuegbar": False, "kisten": []})
            zeilen = con.execute(SQL_KISTEN, (h.nutzer,)).fetchall()
            wert = werte.werte(con, h.nutzer, "kiste")
        return JSONResponse({"verfuegbar": True,
                             "kisten": [{**dict(z), "wert": wert.get(z["id"], {}).get("wert")} for z in zeilen]})

    @_lesen
    def optionen(request):
        return JSONResponse({
            "sortierungen": [{"wert": w, "text": s.text, "richtung": s.richtung} for w, s in SORTIERUNGEN.items()],
            "gruppierungen": [{"wert": w, "text": g.text} for w, g in GRUPPIERUNGEN.items()],
            "farben": [{"wert": w, "text": t} for w, t in FARBEN],
            "seltenheiten": [{"wert": w, "text": t} for w, t in SELTENHEITEN],
            "typen": [{"wert": w, "text": t} for w, t in TYPEN],
            "etikettfarben": [{"wert": w, "text": t} for w, t in ETIKETTFARBEN],
        })

    @_lesen
    def kiste(request):
        kiste_id = _kiste_id(request)
        with closing(h.db()) as con:
            con.execute("BEGIN")  # ein Stand für alle Abfragen dieser Antwort
            _ab_version_3(con, 404)
            k = _kiste_holen(con, h.nutzer, kiste_id)
            kopf = con.execute(SQL_KOPF, (h.nutzer, kiste_id)).fetchone()
            saetze = con.execute(SQL_SAETZE, (h.nutzer, kiste_id)).fetchall()
            sprachen = con.execute(SQL_SPRACHEN, (h.nutzer, kiste_id)).fetchall()
            wert = werte.werte(con, h.nutzer, "kiste").get(kiste_id)
            faecher = einsortieren.faecher_lesen(con, h.nutzer, kiste_id)
        maximum = kopf["manawert_max"]
        return JSONResponse({
            "faecher": faecher,   # ab Version 6: Fächer der Kiste (Sortierregeln), sonst leer
            "id": k["id"], "name": k["name"], "etikettfarbe": k["etikettfarbe"], "regalplatz": k["regalplatz"],
            "posten": kopf["posten"], "karten": kopf["karten"],
            "wert": wert,   # {"wert": Euro (Durchschnitt 7 Tage), "ohne_preis": Karten} oder None
            "werte": {
                "sets": [dict(z) for z in saetze],
                "sprachen": [dict(z) for z in sprachen],
                "manawert_max": 0 if maximum is None else min(20, math.ceil(maximum)),
                "foils": bool(kopf["foils"]),
                "ohne_kartendaten": kopf["ohne_kartendaten"],
            },
        })

    @_lesen
    def karten(request):
        kiste_id = _kiste_id(request)
        a = anfrage_lesen(request.query_params, kiste_id, h.nutzer)
        with closing(h.db()) as con:
            con.execute("BEGIN")  # ein Stand für alle Abfragen dieser Antwort
            _ab_version_3(con, 404)
            _kiste_holen(con, h.nutzer, kiste_id)
            if a.gruppe == "fach":
                fach_vorbereiten(con, h.nutzer, kiste_id)
            return JSONResponse(seite_lesen(con, a))

    # ------------------------------------------------------------------ Schreiben

    @h.schreibend
    async def kiste_neu(request):
        d = await h.json_daten(request)
        unbekannt = set(d) - {"name", "etikettfarbe"}
        if unbekannt:
            raise Fehler("Unbekannte Angabe. Erlaubt: name, etikettfarbe.")
        name = _name_pruefen(d.get("name"))
        farbe = _etikettfarbe_pruefen(d["etikettfarbe"]) if d.get("etikettfarbe") is not None else "messing"

        def arbeit(con):
            _ab_version_3(con, 409)
            _name_frei(con, h.nutzer, name)
            platz = con.execute("SELECT COALESCE(MAX(regalplatz), 0) + 1 FROM kiste WHERE nutzer_id = ?",
                                (h.nutzer,)).fetchone()[0]
            return einfuegen(con, h.nutzer, QUELLE, "kiste", {
                "nutzer_id": h.nutzer, "name": name, "etikettfarbe": farbe, "regalplatz": platz})
        kiste_id = await run_in_threadpool(h.schreibvorgang, arbeit)
        return JSONResponse({"id": kiste_id}, status_code=201)

    @h.schreibend
    async def kiste_aendern(request):
        kiste_id = _kiste_id(request)
        d = await h.json_daten(request)
        unbekannt = set(d) - {"name", "etikettfarbe", "regalplatz"}
        if unbekannt:
            raise Fehler("Unbekannte Angabe. Erlaubt: name, etikettfarbe, regalplatz.")
        if not d:
            raise Fehler("Keine Änderung angegeben.")
        felder: dict = {}
        if "name" in d:
            felder["name"] = _name_pruefen(d["name"])
        if "etikettfarbe" in d:
            felder["etikettfarbe"] = _etikettfarbe_pruefen(d["etikettfarbe"])
        if "regalplatz" in d:
            platz = d["regalplatz"]
            if isinstance(platz, bool) or not isinstance(platz, int) or not 0 <= platz <= 100000:
                raise Fehler("Der Regalplatz muss eine ganze Zahl von 0 bis 100000 sein.")
            felder["regalplatz"] = platz

        def arbeit(con):
            _ab_version_3(con, 409)
            _kiste_holen(con, h.nutzer, kiste_id)
            if "name" in felder:
                _name_frei(con, h.nutzer, felder["name"], ausser=kiste_id)
            return aendern(con, h.nutzer, QUELLE, "kiste", kiste_id, felder)
        geaendert = await run_in_threadpool(h.schreibvorgang, arbeit)
        return JSONResponse({"geaendert": geaendert})

    @h.schreibend
    async def kiste_loeschen(request):
        """Leere Kiste löschen (ohne Rückfrage am Server)."""
        kiste_id = _kiste_id(request)

        def arbeit(con):
            _ab_version_3(con, 409)
            _kiste_holen(con, h.nutzer, kiste_id)
            if con.execute("SELECT 1 FROM karte WHERE kiste_id = ? LIMIT 1", (kiste_id,)).fetchone() is not None:
                raise Fehler("Die Kiste ist nicht leer.", 409)
            weg = kiste_mit_karten_loeschen(con, h.nutzer, kiste_id)
            return {"id": weg["id"], "name": weg["name"]}
        weg = await run_in_threadpool(h.schreibvorgang, arbeit)
        return JSONResponse({"entfernt": weg})

    @h.schreibend
    async def kiste_ganz_loeschen(request):
        """Kiste mit allen Karten löschen; die Oberfläche fragt zweimal nach (Name eintippen)."""
        kiste_id = _kiste_id(request)
        d = await h.json_daten(request)

        def arbeit(con):
            _ab_version_3(con, 409)
            k = _kiste_holen(con, h.nutzer, kiste_id)
            if d.get("name") != k["name"]:
                raise Fehler("Zum Löschen bitte den Namen der Kiste genau eintippen.")
            return kiste_mit_karten_loeschen(con, h.nutzer, kiste_id)
        weg = await run_in_threadpool(h.schreibvorgang, arbeit)
        return JSONResponse({"entfernt": weg})

    return [
        Route("/kisten", kisten),
        Route("/kisten", kiste_neu, methods=["POST"]),
        Route("/kisten/optionen", optionen),
        Route("/kiste/{id:int}", kiste),
        Route("/kiste/{id:int}", kiste_aendern, methods=["PATCH"]),
        Route("/kiste/{id:int}", kiste_loeschen, methods=["DELETE"]),
        Route("/kiste/{id:int}/loeschen", kiste_ganz_loeschen, methods=["POST"]),
        Route("/kiste/{id:int}/karten", karten),
    ]
