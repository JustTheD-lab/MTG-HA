"""Wiederholbarer Import der ManaBox-Sammlung (CSV) in Bulkkisten.

Aufruf im Projektordner (PowerShell), bei beendetem Server und Verarbeiter:
    $env:PYTHONPATH = "src"; .venv\\Scripts\\python.exe -m binderapp.db.manabox_import DATEI.csv
        [--db data/app/binder.sqlite] [--katalog data/katalog.sqlite] [--uebersicht]
        [--zuordnung DATEI] [--probelauf] [--bericht-ordner work/import-bauer] [--nutzer NAME]

Ablauf
- Die CSV wird nur gelesen (UTF-8, mit oder ohne BOM) und nie verändert.
- ``--uebersicht`` zeigt je ManaBox-Binder Name, Typ, Posten und Karten und schreibt eine
  Zuordnungsvorlage (``manabox_binder, typ, posten, karten, aktion, kiste_name``) in den
  Berichtsordner. Aktion ``kiste`` oder ``ignorieren``. Ohne ``--zuordnung`` gilt die
  Vorbelegung: Typ ``binder`` wird eine Kiste mit dem Namen des Binders (ohne Leerzeichen am
  Rand), ``list`` und ``deck`` werden ignoriert.
- Der Import sichert die Datenbank (``../sicherung`` neben dem Ordner der Datenbank), bricht ab,
  wenn sie älter als Version 3 oder noch von Server oder Verarbeiter geöffnet ist, und schreibt
  alles in **einer** Transaktion über ``datenbank.einfuegen`` und ``aendern`` (mit Historie,
  Quelle ``manabox``). ``--probelauf`` rechnet alles durch und rollt zurück (ohne Sicherung).

Regeln
- Eine Kiste je ManaBox-Binder; ``kiste.extern_name`` behält den Originalnamen, damit ein
  Umbenennen in der App den Wiederimport nicht stört.
- Eine Zeile der CSV ist ein Posten. Schlüssel (``karte.extern_id``):
  ``Binder|ManaBox ID|Foil|Language|Condition``. Die ManaBox ID allein ist nicht eindeutig.
- Vorhandene Posten werden nur in den Feldern geändert, in denen die CSV abweicht. Felder, die der
  Nutzer in der App geändert hat (Historie, Quelle ``nutzer``), und die Kiste eines Postens
  fasst der Import nicht an. Vom Nutzer gelöschte Posten legt er nicht neu an.
- Im Export fehlende Posten werden nie gelöscht, nur im Bericht aufgelistet.
- Karten der Quelle ``manabox`` werden nie mit Scan-Karten zusammengezählt; der Import ändert
  keine Scan-Karte, keine Tasche und keinen Binder. Mögliche Doppel (gleiche Karte als Scan-Karte
  in einer Tasche und in einer Kiste) stehen nur im Bericht.
- Der Kaufpreis wird übernommen (ab Version 11). Misprint, Altered, Signed und Proxy werden nicht übernommen.

Der Import ist ein PC-Werkzeug (der Katalog liegt nur am PC); auf dem Raspberry Pi läuft er nicht.
Nur Standardbibliothek, Python 3.11 kompatibel.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from binderapp.db import datenbank
from binderapp.db.importieren import geschlossen
from binderapp.katalog import STANDARD_KATALOG, kartendaten, oeffnen

PROJEKT = Path(__file__).resolve().parents[3]
STANDARD_DB = PROJEKT / "data" / "app" / "binder.sqlite"
AUSGABE = PROJEKT / "work" / "import-bauer"

QUELLE = "manabox"        # karte.quelle und Quelle der Historie
NUTZER_QUELLE = "nutzer"  # Quelle der Historie bei Änderungen in der App
KISTEN_TYP = "binder"     # nur dieser ManaBox-Typ wird standardmäßig eine Kiste
AKTIONEN = ("kiste", "ignorieren")
FOIL_WERTE = ("foil", "etched")  # alles andere („normal“) ist kein Foil
MINDEST_VERSION = 3

PFLICHTSPALTEN = (
    "Binder Name", "Binder Type", "Name", "Set code", "Collector number", "Foil", "Quantity",
    "ManaBox ID", "Scryfall ID", "Condition", "Language",
)
BINDER_SPALTEN = ("Binder Name", "Binder Type")
LISTEN_SPALTEN = tuple(s for s in PFLICHTSPALTEN if s not in BINDER_SPALTEN)  # Listen-Export (listen_import.py)
VORLAGE_SPALTEN = ("manabox_binder", "typ", "posten", "karten", "aktion", "kiste_name")
DOPPEL_SPALTEN = (
    "art", "kiste", "name", "set_code", "nummer", "sprache", "foil", "anzahl_kiste", "scryfall_id_kiste",
    "scryfall_id_scan", "anzahl_scan", "status_scan", "binder", "seite", "zeile", "spalte",
)
# Felder, die der Import aus der CSV (immer) und aus dem Katalog (nur mit Katalog) setzt.
CSV_FELDER = ("name", "set_code", "nummer", "scryfall_id", "anzahl")
KATALOG_FELDER = ("oracle_id", "bild")


class ImportFehler(Exception):
    """Abbruch mit verständlicher Meldung; die Datenbank bleibt unverändert."""


class _Zurueck(Exception):
    """Zum Zurückrollen im Probelauf."""


# ---------------------------------------------------------------- Lesen der CSV


@dataclass(frozen=True)
class Posten:
    """Eine Zeile der ManaBox-CSV."""

    zeile: int           # Zeilennummer in der Datei (Kopfzeile = 1)
    binder: str          # Originalname, auch mit Leerzeichen am Ende
    typ: str
    name: str
    set_code: str
    nummer: str
    foil: str            # wie in der CSV (normal, foil)
    anzahl: int
    manabox_id: str
    scryfall_id: str
    zustand: str
    sprache: str         # wie in der CSV (de, en, ja)
    hinzugefuegt: str    # Spalte „Added“, ISO-Zeit mit Z
    angaben: tuple[str, ...] = ()  # besondere Angaben (Bericht): preis, misprint, altered, signed, proxy
    kaufpreis: float | None = None   # Spalte „Purchase price“; leer oder unlesbar: None
    waehrung: str | None = None      # Spalte „Purchase price currency“

    @property
    def extern_id(self) -> str:
        return "|".join((self.binder, self.manabox_id, self.foil, self.sprache, self.zustand))

    @property
    def ist_foil(self) -> int:
        return 1 if self.foil.lower() in FOIL_WERTE else 0


@dataclass
class BinderInfo:
    """Ein ManaBox-Binder (Name und Typ) mit Zählung."""

    name: str
    typ: str
    posten: int = 0
    karten: int = 0

    @property
    def schluessel(self) -> tuple[str, str]:
        return (self.name, self.typ)


@dataclass(frozen=True)
class Eintrag:
    """Zuordnung eines ManaBox-Binders."""

    aktion: str       # kiste oder ignorieren
    kiste_name: str   # Name der Kiste (bei „ignorieren“ ohne Bedeutung)


def format_erkennen(spalten) -> str | None:
    """„sammlung“ (mit Binderspalten), „liste“ (Listen-Export ohne Binderspalten) oder None (keine ManaBox-CSV)."""
    spalten = set(spalten or [])
    if all(s in spalten for s in PFLICHTSPALTEN):
        return "sammlung"
    if all(s in spalten for s in LISTEN_SPALTEN) and not spalten & set(BINDER_SPALTEN):
        return "liste"
    return None


def _lese(f, name: str, pflicht: tuple[str, ...]) -> tuple[list[Posten], list[str]]:
    """Liest Zeilen aus einem Textstrom; gibt (Posten, Spaltennamen) zurück."""
    posten: list[Posten] = []
    leser = csv.DictReader(f)
    fehlend = [s for s in pflicht if s not in (leser.fieldnames or [])]
    if fehlend:
        hinweis = ""
        if pflicht == PFLICHTSPALTEN and format_erkennen(leser.fieldnames) == "liste":
            hinweis = " (das ist ein Listen-Export: mit --kiste NAME in eine Kiste importieren)"
        raise ImportFehler(f"{name}: Spalten fehlen: {', '.join(fehlend)}{hinweis}")
    for nr, z in enumerate(leser, start=2):
        def wert(spalte: str) -> str:
            return (z.get(spalte) or "").strip()

        try:
            anzahl = int(wert("Quantity"))
        except ValueError:
            raise ImportFehler(f"{name}, Zeile {nr}: Quantity ist keine Zahl: {wert('Quantity')!r}") from None
        if not 1 <= anzahl <= 99999:
            raise ImportFehler(f"{name}, Zeile {nr}: Quantity muss zwischen 1 und 99.999 liegen")
        if not wert("Name"):
            raise ImportFehler(f"{name}, Zeile {nr}: Name fehlt")
        angaben = tuple(
            n for n, da in (
                ("preis", bool(wert("Purchase price"))),
                ("misprint", wert("Misprint").lower() == "true"),
                ("altered", wert("Altered").lower() == "true"),
                ("signed", wert("Signed").lower() == "true"),
                ("proxy", wert("Proxy").lower() == "true"),
            ) if da
        )
        posten.append(Posten(
            zeile=nr, binder=z.get("Binder Name") or "", typ=wert("Binder Type"), name=wert("Name"),
            set_code=wert("Set code"), nummer=wert("Collector number"), foil=wert("Foil"), anzahl=anzahl,
            manabox_id=wert("ManaBox ID"), scryfall_id=wert("Scryfall ID"), zustand=wert("Condition"),
            sprache=wert("Language"), hinzugefuegt=wert("Added"), angaben=angaben,
            kaufpreis=_preis(wert("Purchase price")), waehrung=wert("Purchase price currency") or None,
        ))
    return posten, list(leser.fieldnames or [])


def _preis(text: str) -> float | None:
    """Kaufpreis aus der CSV. Ein unlesbarer Wert ist kein Grund, den Import abzubrechen."""
    try:
        wert = float(text)
    except ValueError:
        return None
    return wert if 0 <= wert < 1_000_000 else None


def lese_export(pfad: str | Path) -> list[Posten]:
    """Liest die ManaBox-CSV (nur lesend). Wirft ImportFehler bei fehlenden Spalten oder unlesbaren Zeilen."""
    pfad = Path(pfad)
    try:
        with pfad.open("r", encoding="utf-8-sig", newline="") as f:
            return _lese(f, pfad.name, PFLICHTSPALTEN)[0]
    except UnicodeDecodeError as e:
        raise ImportFehler(f"{pfad.name}: keine UTF-8-Datei ({e.reason})") from None


def lese_liste(daten: bytes, name: str) -> list[Posten]:
    """Liest einen ManaBox-Listen-Export (ohne Binderspalten) aus den Bytes der Datei (nur lesend).
    Ein Sammlungsexport oder eine andere CSV wird mit einem verständlichen Satz abgelehnt."""
    try:
        text = daten.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ImportFehler(f"{name}: keine UTF-8-Datei ({e.reason})") from None
    kopf = next(csv.reader(io.StringIO(text)), [])
    art = format_erkennen(kopf)
    if art == "sammlung":
        raise ImportFehler(f"{name} ist ein Sammlungsexport mit Binderspalten, keine Liste. Den Sammlungsexport "
                           "liest python -m binderapp.db.manabox_import ohne --kiste.")
    posten, _ = _lese(io.StringIO(text, newline=""), name, LISTEN_SPALTEN)
    if not posten:
        raise ImportFehler(f"{name}: Die Liste enthält keine Zeilen.")
    return posten


def angelegt_aus(text: str) -> str | None:
    """Spalte „Added“ (2025-08-26T12:33:09.155Z) → Zeitpunkt wie in der Datenbank (UTC, ohne Bruchteil)."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        zeit = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if zeit.tzinfo is not None:
        zeit = zeit.astimezone(timezone.utc).replace(tzinfo=None)
    return zeit.strftime("%Y-%m-%dT%H:%M:%S")


def uebersicht(posten: list[Posten]) -> list[BinderInfo]:
    """Je ManaBox-Binder (Name und Typ) Posten und Karten, in der Reihenfolge des ersten Auftretens."""
    infos: dict[tuple[str, str], BinderInfo] = {}
    for p in posten:
        info = infos.setdefault((p.binder, p.typ), BinderInfo(p.binder, p.typ))
        info.posten += 1
        info.karten += p.anzahl
    return list(infos.values())


# ---------------------------------------------------------------- Zuordnung


def vorbelegung(info: BinderInfo) -> Eintrag:
    return Eintrag("kiste" if info.typ == KISTEN_TYP else "ignorieren", info.name.strip())


def lese_zuordnung(pfad: str | Path | None, infos: list[BinderInfo]) -> tuple[dict[tuple[str, str], Eintrag], list[str]]:
    """Zuordnung je (Binder, Typ). Ohne Datei gilt die Vorbelegung. Gibt (Zuordnung, Hinweise) zurück."""
    zuordnung = {info.schluessel: vorbelegung(info) for info in infos}
    hinweise: list[str] = []
    if pfad is None:
        return zuordnung, hinweise
    pfad = Path(pfad)
    try:
        with pfad.open("r", encoding="utf-8-sig", newline="") as f:
            leser = csv.DictReader(f)
            fehlend = [s for s in ("manabox_binder", "typ", "aktion") if s not in (leser.fieldnames or [])]
            if fehlend:
                raise ImportFehler(f"{pfad.name}: Spalten fehlen: {', '.join(fehlend)}")
            zeilen = list(leser)
    except UnicodeDecodeError as e:
        raise ImportFehler(f"{pfad.name}: keine UTF-8-Datei ({e.reason})") from None
    gesehen: set[tuple[str, str]] = set()
    for nr, z in enumerate(zeilen, start=2):
        name = z.get("manabox_binder") or ""
        typ = (z.get("typ") or "").strip()
        aktion = (z.get("aktion") or "").strip().lower()
        if aktion not in AKTIONEN:
            raise ImportFehler(f"{pfad.name}, Zeile {nr}: Aktion {aktion!r} unbekannt (erlaubt: {', '.join(AKTIONEN)})")
        treffer = [i for i in infos if i.name == name and i.typ == typ]
        if not treffer:  # Tabellenprogramme schneiden Leerzeichen am Rand gern ab
            treffer = [i for i in infos if i.name.strip() == name.strip() and i.typ == typ]
        if not treffer:
            hinweise.append(f"Zuordnung, Zeile {nr}: ManaBox-Binder {name!r} ({typ}) steht nicht im Export, übergangen")
            continue
        for info in treffer:
            kiste_name = (z.get("kiste_name") or "").strip() or info.name.strip()
            zuordnung[info.schluessel] = Eintrag(aktion, kiste_name)
            gesehen.add(info.schluessel)
    for info in infos:
        if info.schluessel not in gesehen:
            hinweise.append(f"Zuordnung: {info.name!r} ({info.typ}) fehlt in der Datei, es gilt die Vorbelegung "
                            f"({zuordnung[info.schluessel].aktion})")
    return zuordnung, hinweise


def schreibe_vorlage(pfad: Path, infos: list[BinderInfo], zuordnung: dict[tuple[str, str], Eintrag]) -> Path:
    """Schreibt die Zuordnungsvorlage (UTF-8 mit BOM, damit Tabellenprogramme die Umlaute lesen)."""
    pfad = _freier_pfad(pfad)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    with pfad.open("w", encoding="utf-8-sig", newline="") as f:
        schreiber = csv.writer(f)
        schreiber.writerow(VORLAGE_SPALTEN)
        for info in infos:
            e = zuordnung[info.schluessel]
            schreiber.writerow([info.name, info.typ, info.posten, info.karten, e.aktion, e.kiste_name])
    return pfad


def _freier_pfad(pfad: Path) -> Path:
    """Gibt `pfad` zurück oder, falls vorhanden, eine Variante mit Zähler (überschreibt nie etwas)."""
    ziel, n = pfad, 1
    while ziel.exists():
        n += 1
        ziel = pfad.with_name(f"{pfad.stem}-{n}{pfad.suffix}")
    return ziel


# ---------------------------------------------------------------- Ergebnis


@dataclass
class KistenZahlen:
    name: str                  # Name der Kiste in der Datenbank
    extern_name: str
    id: int = 0
    angelegt: bool = False
    posten: int = 0            # Zeilen der CSV
    karten: int = 0            # Summe Quantity
    neu: int = 0
    geaendert: int = 0
    unveraendert: int = 0
    nutzer_geloescht: int = 0  # nicht neu angelegt, weil der Nutzer den Posten gelöscht hat
    fehlend: int = 0           # in der Datenbank, aber nicht mehr in der CSV
    doppel_id: int = 0         # Posten mit gleicher Scryfall-ID, Sprache und Foil als Scan-Karte
    doppel_set: int = 0        # Posten mit gleichem Set, gleicher Nummer, Sprache und Foil (andere ID)


@dataclass
class Ergebnis:
    kisten: list[KistenZahlen] = field(default_factory=list)
    fehlende: list[dict] = field(default_factory=list)
    ohne_scryfall: list[Posten] = field(default_factory=list)
    nutzer_geloescht: list[Posten] = field(default_factory=list)
    geschuetzt: list[dict] = field(default_factory=list)   # vom Nutzer geänderte Felder, nicht überschrieben
    geaenderte_felder: Counter = field(default_factory=Counter)
    kartendaten: dict | None = None
    anteil: dict = field(default_factory=dict)
    doppel: list[dict] = field(default_factory=list)
    historie_neu: int = 0
    mit_katalog: bool = False


# ---------------------------------------------------------------- Import


def _nutzer(con, name: str | None) -> int:
    zeilen = con.execute("SELECT id, name FROM nutzer ORDER BY id").fetchall()
    if name:
        for z in zeilen:
            if z["name"] == name:
                return z["id"]
        raise ImportFehler(f"Nutzer {name!r} gibt es in der Datenbank nicht")
    if len(zeilen) == 1:
        return zeilen[0]["id"]
    if not zeilen:
        raise ImportFehler("Die Datenbank hat keinen Nutzer (Erst-Import fehlt?)")
    raise ImportFehler("Mehrere Nutzer in der Datenbank: --nutzer NAME angeben")


def _auswahl(infos: list[BinderInfo], zuordnung: dict[tuple[str, str], Eintrag]) -> list[tuple[BinderInfo, Eintrag]]:
    """Die ManaBox-Binder, die Kisten werden, in der Reihenfolge der CSV."""
    auswahl = [(i, zuordnung[i.schluessel]) for i in infos if zuordnung[i.schluessel].aktion == "kiste"]
    namen = Counter(i.name for i, _ in auswahl)
    doppelt = sorted(n for n, c in namen.items() if c > 1)
    if doppelt:
        raise ImportFehler("ManaBox-Binder mit mehreren Typen sind alle als Kiste gewählt (nur einer darf es sein): "
                           + ", ".join(repr(n) for n in doppelt))
    return auswahl


def _kisten_bereitstellen(con, nid: int, auswahl: list[tuple[BinderInfo, Eintrag]]) -> list[KistenZahlen]:
    """Findet die Kisten über `extern_name` oder legt sie an. Prüft Namenskonflikte vor dem ersten Schreiben."""
    vorhanden = con.execute("SELECT id, name, extern_name FROM kiste WHERE nutzer_id = ?", (nid,)).fetchall()
    nach_extern = {z["extern_name"]: z for z in vorhanden if z["extern_name"] is not None}
    belegt = {z["name"] for z in vorhanden}
    konflikte: list[str] = []
    neue_namen: dict[str, str] = {}
    for info, eintrag in auswahl:
        if info.name in nach_extern:
            continue
        if not eintrag.kiste_name:
            konflikte.append(f"{info.name!r}: Kistenname ist leer")
        elif eintrag.kiste_name in belegt:
            konflikte.append(f"{info.name!r}: Es gibt schon eine Kiste {eintrag.kiste_name!r} ohne Bezug zu diesem "
                             "ManaBox-Binder (in der Zuordnung einen anderen kiste_name wählen)")
        elif eintrag.kiste_name in neue_namen:
            konflikte.append(f"{info.name!r}: Kistenname {eintrag.kiste_name!r} ist schon für {neue_namen[eintrag.kiste_name]!r} "
                             "vorgesehen (kiste_name in der Zuordnung ändern)")
        else:
            neue_namen[eintrag.kiste_name] = info.name
    if konflikte:
        raise ImportFehler("Kisten lassen sich nicht anlegen:\n  " + "\n  ".join(konflikte))

    platz = con.execute("SELECT COALESCE(MAX(regalplatz), 0) FROM kiste WHERE nutzer_id = ?", (nid,)).fetchone()[0]
    kisten: list[KistenZahlen] = []
    for info, eintrag in auswahl:
        if info.name in nach_extern:
            z = nach_extern[info.name]
            kisten.append(KistenZahlen(name=z["name"], extern_name=info.name, id=z["id"]))
            continue
        platz += 1
        kid = datenbank.einfuegen(con, nid, QUELLE, "kiste", {
            "nutzer_id": nid, "name": eintrag.kiste_name, "regalplatz": platz, "extern_name": info.name,
        })
        kisten.append(KistenZahlen(name=eintrag.kiste_name, extern_name=info.name, id=kid, angelegt=True))
    return kisten


def _nutzer_aenderungen(con) -> tuple[dict[int, set[str]], set[str]]:
    """Felder je Karte, die der Nutzer in der App geändert hat, und die extern_id der vom Nutzer gelöschten Posten."""
    felder: dict[int, set[str]] = defaultdict(set)
    # Rückgängig ist keine Entscheidung gegen den Posten: Ein zurückgenommener Import lässt sich neu einlesen.
    for z in con.execute("SELECT datensatz, neu FROM historie WHERE tabelle = 'karte' AND quelle = ? "
                         "AND aktion = 'geaendert' AND rueckgaengig_von IS NULL", (NUTZER_QUELLE,)):
        try:
            felder[z["datensatz"]].update(json.loads(z["neu"] or "{}"))
        except (ValueError, TypeError):
            continue
    geloescht: set[str] = set()
    for z in con.execute("SELECT alt FROM historie WHERE tabelle = 'karte' AND quelle = ? AND aktion = 'geloescht' "
                         "AND rueckgaengig_von IS NULL", (NUTZER_QUELLE,)):
        try:
            alt = json.loads(z["alt"] or "{}")
        except (ValueError, TypeError):
            continue
        if alt.get("quelle") == QUELLE and alt.get("extern_id"):
            geloescht.add(alt["extern_id"])
    return felder, geloescht


def _doppel(con, nid: int, kisten_ids: list[int]) -> list[dict]:
    """Mögliche Doppel: dieselbe Karte als Scan-Karte (in einer Tasche) und als Posten einer Kiste. Nur Auskunft."""
    if not kisten_ids:
        return []
    platz = ", ".join("?" for _ in kisten_ids)
    zeilen = con.execute(
        "SELECT ki.name AS kiste, k.id AS kisten_karte, k.name AS name, k.set_code AS set_code, k.nummer AS nummer, "
        "k.sprache AS sprache, k.foil AS foil, k.anzahl AS anzahl_kiste, k.scryfall_id AS scryfall_id_kiste, "
        "s.id AS scan_karte, s.scryfall_id AS scryfall_id_scan, s.anzahl AS anzahl_scan, s.status AS status_scan, "
        "b.name AS binder, se.nummer AS seite, t.zeile AS zeile, t.spalte AS spalte "
        "FROM karte k JOIN kiste ki ON ki.id = k.kiste_id "
        "JOIN karte s ON s.nutzer_id = k.nutzer_id AND s.quelle = 'scan' AND s.tasche_id IS NOT NULL "
        "  AND s.sprache IS k.sprache AND s.foil = k.foil "
        "  AND ((k.scryfall_id IS NOT NULL AND s.scryfall_id = k.scryfall_id) "
        "       OR (lower(s.set_code) = lower(k.set_code) AND s.nummer = k.nummer)) "
        "JOIN tasche t ON t.id = s.tasche_id JOIN seite se ON se.id = t.seite_id JOIN binder b ON b.id = se.binder_id "
        f"WHERE k.nutzer_id = ? AND k.quelle = ? AND k.kiste_id IN ({platz}) "
        "ORDER BY ki.regalplatz, ki.name, k.name COLLATE NOCASE, k.set_code, k.nummer, b.name, se.nummer, t.zeile, t.spalte",
        (nid, QUELLE, *kisten_ids),
    ).fetchall()
    ergebnis = []
    for z in zeilen:
        d = dict(z)
        d["art"] = "id" if d["scryfall_id_scan"] == d["scryfall_id_kiste"] else "set_nummer"
        ergebnis.append(d)
    return ergebnis


def _posten_uebernehmen(con, nid: int, p: Posten, k: KistenZahlen, kat: sqlite3.Connection | None,
                        cache: dict, bestand: dict[str, dict], nutzer_felder: dict[int, set[str]],
                        nutzer_geloescht: set[str], erg: Ergebnis, *, schritt: str | None = None) -> int | None:
    """Ein Posten der CSV: neu anlegen (in Kiste `k`), Felder angleichen oder als vom Nutzer gelöscht melden.
    Gemeinsam für Sammlungsimport, Listen-Import (listen_import.py) und den Import in der App (manabox_app.py, mit
    `schritt` für Rückgängig). Gibt die ID eines neu angelegten Postens zurück, sonst None."""
    k.posten += 1
    k.karten += p.anzahl
    if not p.scryfall_id:
        erg.ohne_scryfall.append(p)
    daten = None
    if kat is not None and p.scryfall_id:
        schluessel = (p.scryfall_id, p.set_code.lower(), p.nummer)
        if schluessel not in cache:
            cache[schluessel] = kartendaten.nachschlagen(kat, p.scryfall_id, p.set_code or None, p.nummer or None)
        daten = cache[schluessel]
    felder = {
        "name": p.name, "set_code": p.set_code.lower() or None, "nummer": p.nummer or None,
        "scryfall_id": p.scryfall_id or None, "anzahl": p.anzahl,
    }
    if kat is not None:
        felder["oracle_id"] = daten["oracle_id"] if daten else None
        felder["bild"] = ((daten["bild"] if daten else None) or p.scryfall_id) or None
    # eine CSV ohne Preis löscht keinen vorhandenen
    if p.kaufpreis is not None and datenbank.version(con) >= 11:
        felder.update(kaufpreis=p.kaufpreis, kaufpreis_waehrung=p.waehrung)
    alt = bestand.get(p.extern_id)
    if alt is None:
        if p.extern_id in nutzer_geloescht:
            k.nutzer_geloescht += 1
            erg.nutzer_geloescht.append(p)
            return None
        neu = {
            "nutzer_id": nid, "tasche_id": None, "kiste_id": k.id, "quelle": QUELLE, "extern_id": p.extern_id,
            "scryfall_id": felder["scryfall_id"], "oracle_id": felder.get("oracle_id"), "name": p.name,
            "set_code": felder["set_code"], "nummer": felder["nummer"], "sprache": p.sprache.lower() or None,
            "foil": p.ist_foil, "anzahl": p.anzahl, "status": "sicher", "geprueft": 0,
            "bild": felder["bild"] if kat is not None else (p.scryfall_id or None), "zustand": p.zustand or None,
        }
        for f in ("kaufpreis", "kaufpreis_waehrung"):
            if f in felder:
                neu[f] = felder[f]
        angelegt = angelegt_aus(p.hinzugefuegt)
        if angelegt:
            neu["angelegt"] = angelegt
        karte_id = datenbank.einfuegen(con, nid, QUELLE, "karte", neu, schritt=schritt)
        k.neu += 1
        return karte_id
    geschuetzt = {f for f in felder if f in nutzer_felder.get(alt["id"], ())}
    if geschuetzt:
        erg.geschuetzt.append({"karte": alt["id"], "kiste": k.name, "name": p.name, "felder": sorted(geschuetzt)})
    soll = {f: v for f, v in felder.items() if f not in geschuetzt}
    geaendert = datenbank.aendern(con, nid, QUELLE, "karte", alt["id"], soll, schritt=schritt)
    if geaendert:
        k.geaendert += 1
        erg.geaenderte_felder.update(geaendert.keys())
    else:
        k.unveraendert += 1
    return None


def schluessel_pruefen(importiert: list[Posten]) -> None:
    """Doppelte Schlüssel würden Posten stillschweigend verschmelzen: lieber abbrechen."""
    zaehler = Counter(p.extern_id for p in importiert)
    if any(c > 1 for c in zaehler.values()):
        zeilen: dict[str, list[int]] = defaultdict(list)
        for p in importiert:
            if zaehler[p.extern_id] > 1:
                zeilen[p.extern_id].append(p.zeile)
        raise ImportFehler("Schlüssel (Binder, ManaBox ID, Foil, Sprache, Zustand) kommt mehrfach vor, ein "
                           "Zusammenzählen findet nicht statt:\n  "
                           + "\n  ".join(f"{s!r} in den Zeilen {z}" for s, z in list(zeilen.items())[:10]))


def _lauf(con, nid: int, posten: list[Posten], infos: list[BinderInfo], zuordnung: dict[tuple[str, str], Eintrag],
          kat: sqlite3.Connection | None) -> Ergebnis:
    """Der eigentliche Import in der Transaktion des Aufrufers."""
    erg = Ergebnis(mit_katalog=kat is not None)
    historie_vorher = con.execute("SELECT COUNT(*) FROM historie").fetchone()[0]

    auswahl = _auswahl(infos, zuordnung)
    importiert = [p for p in posten if zuordnung[(p.binder, p.typ)].aktion == "kiste"]

    schluessel_pruefen(importiert)
    kisten = _kisten_bereitstellen(con, nid, auswahl)
    erg.kisten = kisten
    kiste_von = {k.extern_name: k for k in kisten}

    bestand = {
        z["extern_id"]: dict(z) for z in con.execute(
            "SELECT id, extern_id, kiste_id FROM karte WHERE nutzer_id = ? AND quelle = ? AND extern_id IS NOT NULL",
            (nid, QUELLE))
    }
    nutzer_felder, nutzer_geloescht = _nutzer_aenderungen(con)

    cache: dict[tuple[str, str, str], dict | None] = {}
    for p in importiert:
        _posten_uebernehmen(con, nid, p, kiste_von[p.binder], kat, cache, bestand, nutzer_felder, nutzer_geloescht, erg)

    # Im Export fehlende Posten: nur melden, nie löschen.
    csv_schluessel = {p.extern_id for p in importiert}
    csv_praefixe = {s.rsplit("|", 3)[0] for s in csv_schluessel}  # Binder|ManaBox ID
    kisten_ids = [k.id for k in kisten]
    kiste_nach_id = {k.id: k for k in kisten}
    if kisten_ids:
        platz = ", ".join("?" for _ in kisten_ids)
        for z in con.execute(
            "SELECT id, extern_id, kiste_id, name, set_code, nummer, sprache, foil, anzahl FROM karte "
            f"WHERE nutzer_id = ? AND quelle = ? AND extern_id IS NOT NULL AND kiste_id IN ({platz}) ORDER BY id",
            (nid, QUELLE, *kisten_ids),
        ):
            if z["extern_id"] not in csv_schluessel:
                kiste_nach_id[z["kiste_id"]].fehlend += 1
                # Gleicher Binder und gleiche ManaBox ID noch im Export, nur Foil, Sprache oder Zustand anders:
                # vermutlich in ManaBox geändert, der alte Posten zählt sonst doppelt (Befund des Prüfers).
                geaendert_vermutlich = z["extern_id"].rsplit("|", 3)[0] in csv_praefixe
                erg.fehlende.append({**dict(z), "kiste": kiste_nach_id[z["kiste_id"]].name,
                                     "vermutlich_geaendert": geaendert_vermutlich})

    # Kartendaten für alle Drucke ohne Eintrag (Manawert, Farbe, Typ, Seltenheit, Set).
    if kat is not None:
        erg.kartendaten = kartendaten.uebernehmen(con, kat)

    if kisten_ids:
        platz = ", ".join("?" for _ in kisten_ids)
        z = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(k.anzahl), 0), COALESCE(SUM(d.scryfall_id IS NOT NULL), 0), "
            "COALESCE(SUM(CASE WHEN d.scryfall_id IS NOT NULL THEN k.anzahl ELSE 0 END), 0) "
            "FROM karte k LEFT JOIN kartendaten d ON d.scryfall_id = k.scryfall_id "
            f"WHERE k.nutzer_id = ? AND k.quelle = ? AND k.kiste_id IN ({platz})",
            (nid, QUELLE, *kisten_ids),
        ).fetchone()
        erg.anteil = {"posten": z[0], "karten": z[1], "posten_mit": z[2], "karten_mit": z[3]}

    erg.doppel = _doppel(con, nid, kisten_ids)
    je_id: dict[int, set[int]] = defaultdict(set)
    je_set: dict[int, set[int]] = defaultdict(set)
    kisten_nach_name = {k.name: k for k in kisten}
    for d in erg.doppel:
        (je_id if d["art"] == "id" else je_set)[kisten_nach_name[d["kiste"]].id].add(d["kisten_karte"])
    for k in kisten:
        k.doppel_id = len(je_id[k.id])
        k.doppel_set = len(je_set[k.id] - je_id[k.id])

    erg.historie_neu = con.execute("SELECT COUNT(*) FROM historie").fetchone()[0] - historie_vorher
    return erg


# ---------------------------------------------------------------- Bericht


def _zahl(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _prozent(teil: int, ganz: int) -> str:
    return "-" if not ganz else f"{100 * teil / ganz:.2f}".replace(".", ",") + " %"


def _kurz(liste: list[str], grenze: int | None) -> list[str]:
    if grenze is None or len(liste) <= grenze:
        return liste
    return liste[:grenze] + [f"... und {len(liste) - grenze} weitere (vollständig im Bericht)"]


def baue_bericht(erg: Ergebnis, *, csv_name: str, posten: list[Posten], infos: list[BinderInfo],
                 zuordnung: dict[tuple[str, str], Eintrag], hinweise: list[str], probelauf: bool,
                 sicherung: Path | None, db: Path, sekunden: float, doppel_datei: Path | None,
                 grenze: int | None) -> str:
    """Bericht als Markdown-Text. `grenze` kürzt lange Listen (Konsole); ohne Grenze ist er vollständig."""
    z: list[str] = []
    z.append("# ManaBox-Import" + (" (Probelauf, nichts geschrieben)" if probelauf else ""))
    z.append("")
    z.append(f"- Export: {csv_name}, {_zahl(len(posten))} Zeilen, {_zahl(sum(p.anzahl for p in posten))} Karten")
    z.append(f"- Datenbank: {db}")
    if probelauf:
        z.append("- Sicherung: übersprungen (Probelauf)")
    else:
        z.append(f"- Sicherung: {sicherung}")
    z.append(f"- Katalog: {'ja' if erg.mit_katalog else 'NEIN (kein Katalog: Kartendaten, oracle_id und Bildschlüssel fehlen)'}")
    z.append(f"- Laufzeit: {f'{sekunden:.1f}'.replace('.', ',')} s, neue Historienzeilen: {_zahl(erg.historie_neu)}")
    for h in hinweise:
        z.append(f"- Hinweis: {h}")

    z.append("")
    z.append("## Kisten")
    z.append("")
    z.append("| Kiste | Posten | Karten | neu | geändert | unverändert | fehlend | Doppel (ID) | Doppel (Set/Nr.) |")
    z.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for k in erg.kisten:
        marke = " (neu angelegt)" if k.angelegt else ""
        z.append(f"| {k.name}{marke} | {_zahl(k.posten)} | {_zahl(k.karten)} | {_zahl(k.neu)} | {_zahl(k.geaendert)} | "
                 f"{_zahl(k.unveraendert)} | {_zahl(k.fehlend)} | {_zahl(k.doppel_id)} | {_zahl(k.doppel_set)} |")
    z.append(f"| **Summe ({len(erg.kisten)} Kisten)** | {_zahl(sum(k.posten for k in erg.kisten))} | "
             f"{_zahl(sum(k.karten for k in erg.kisten))} | {_zahl(sum(k.neu for k in erg.kisten))} | "
             f"{_zahl(sum(k.geaendert for k in erg.kisten))} | {_zahl(sum(k.unveraendert for k in erg.kisten))} | "
             f"{_zahl(len(erg.fehlende))} | {_zahl(sum(k.doppel_id for k in erg.kisten))} | "
             f"{_zahl(sum(k.doppel_set for k in erg.kisten))} |")
    if erg.geaenderte_felder:
        z.append("")
        z.append("Geänderte Felder: " + ", ".join(f"{f} {n}" for f, n in sorted(erg.geaenderte_felder.items())))

    z.append("")
    z.append("## Kartendaten")
    z.append("")
    if not erg.mit_katalog:
        z.append("Kein Katalog gefunden (python -m binderapp.katalog.bau); Kartendaten wurden nicht gefüllt. "
                 "Ein späterer Lauf mit Katalog ergänzt oracle_id und Bildschlüssel.")
    else:
        a = erg.anteil
        z.append(f"- Anteil mit Kartendaten: {_prozent(a['posten_mit'], a['posten'])} der Posten "
                 f"({_zahl(a['posten_mit'])} von {_zahl(a['posten'])}), {_prozent(a['karten_mit'], a['karten'])} der Karten "
                 f"({_zahl(a['karten_mit'])} von {_zahl(a['karten'])}); Ziel mindestens 99 %")
        kd = erg.kartendaten
        if kd:
            wege = ", ".join(f"{w} {v['ids']}" for w, v in kd["gefunden"].items() if v["ids"])
            z.append(f"- Neue Drucke in kartendaten: {_zahl(kd['geschrieben'])} (gefunden über: {wege or 'keine'}); "
                     f"schon vorhanden: {_zahl(kd['vorhanden']['ids'])}")
            for grund, v in kd["nicht_gefunden"].items():
                z.append(f"- Nicht gefunden ({grund}): {v['ids']} Drucke, {v['karten']} Karten, Beispiele: "
                         + ", ".join(v["beispiele"]))
    z.append(f"- Zeilen ohne Scryfall-ID: {len(erg.ohne_scryfall)}")
    for p in _kurz([f"Zeile {p.zeile}: {p.name} ({p.binder.strip()})" for p in erg.ohne_scryfall], grenze):
        z.append(f"  - {p}")

    z.append("")
    z.append("## Fehlende Zeilen (in der Datenbank, nicht mehr im Export; nicht gelöscht)")
    z.append("")
    vermutlich = sum(1 for d in erg.fehlende if d.get("vermutlich_geaendert"))
    z.append(f"{_zahl(len(erg.fehlende))} Posten" + (
        f", davon {_zahl(vermutlich)} vermutlich in ManaBox geändert (gleiche ManaBox ID im selben Binder, aber "
        "anderes Foil, andere Sprache oder anderer Zustand: der alte Posten zählt bis zur Prüfung doppelt)."
        if vermutlich else "."))
    for f in _kurz([f"{d['kiste']}: {d['name']} ({(d['set_code'] or '').upper()} {d['nummer']}, {d['sprache']}, "
                    f"{'Foil' if d['foil'] else 'normal'}, {d['anzahl']}x) [{d['extern_id']}]"
                    + (" – vermutlich geändert" if d.get("vermutlich_geaendert") else "") for d in erg.fehlende], grenze):
        z.append(f"- {f}")

    z.append("")
    z.append("## Vom Nutzer geändert oder gelöscht (vom Import nicht angefasst)")
    z.append("")
    z.append(f"- Posten mit vom Nutzer geänderten Feldern, nicht überschrieben: {len(erg.geschuetzt)}")
    for g in _kurz([f"{g['kiste']}: {g['name']} (Karte {g['karte']}): {', '.join(g['felder'])}" for g in erg.geschuetzt], grenze):
        z.append(f"  - {g}")
    z.append(f"- Vom Nutzer gelöschte Posten, nicht neu angelegt: {len(erg.nutzer_geloescht)}")
    for g in _kurz([f"Zeile {p.zeile}: {p.name} ({p.binder.strip()})" for p in erg.nutzer_geloescht], grenze):
        z.append(f"  - {g}")

    z.append("")
    z.append("## Nicht übernommene Angaben (Zeilen der Kisten)")
    z.append("")
    zahlen = Counter()
    kisten_namen = {k.extern_name for k in erg.kisten}
    gezaehlt = [p for p in posten if p.binder in kisten_namen and zuordnung[(p.binder, p.typ)].aktion == "kiste"]
    for p in gezaehlt:
        zahlen.update(p.angaben)
    z.append(f"Zeilen mit Preis: {_zahl(zahlen['preis'])} · Misprint: {zahlen['misprint']} · Altered: {zahlen['altered']} · "
             f"Signed: {zahlen['signed']} · Proxy: {zahlen['proxy']} (von {_zahl(len(gezaehlt))} Zeilen; Preise kommen in Phase 6)")

    z.append("")
    z.append("## Ignorierte ManaBox-Binder")
    z.append("")
    ignoriert = [i for i in infos if zuordnung[i.schluessel].aktion == "ignorieren"]
    je_typ: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for i in ignoriert:
        je_typ[i.typ][0] += 1
        je_typ[i.typ][1] += i.posten
        je_typ[i.typ][2] += i.karten
    z.append("Typen: " + (", ".join(f"{t} {a} Binder, {_zahl(p)} Posten, {_zahl(k)} Karten" for t, (a, p, k) in sorted(je_typ.items()))
                          or "keine"))
    for line in _kurz([f"{i.name.strip()!r} ({i.typ}): {_zahl(i.posten)} Posten, {_zahl(i.karten)} Karten" for i in ignoriert], grenze):
        z.append(f"- {line}")

    z.append("")
    z.append("## Mögliche Doppel mit den gescannten Bindern (nichts zusammengezählt)")
    z.append("")
    nur_id = sum(k.doppel_id for k in erg.kisten)
    nur_set = sum(k.doppel_set for k in erg.kisten)
    z.append(f"Posten einer Kiste, die als Scan-Karte in einer Tasche liegen: gleiche Scryfall-ID, Sprache und Foil: {nur_id}; "
             f"zusätzlich gleiches Set, gleiche Nummer, Sprache und Foil bei anderer ID: {nur_set}.")
    if doppel_datei is not None:
        z.append(f"Liste: {doppel_datei}")
    return "\n".join(z) + "\n"


def schreibe_doppel(pfad: Path, doppel: list[dict]) -> Path:
    pfad = _freier_pfad(pfad)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    with pfad.open("w", encoding="utf-8-sig", newline="") as f:
        schreiber = csv.DictWriter(f, fieldnames=DOPPEL_SPALTEN, extrasaction="ignore")
        schreiber.writeheader()
        schreiber.writerows(doppel)
    return pfad


def _uebersicht_text(infos: list[BinderInfo], zuordnung: dict[tuple[str, str], Eintrag]) -> str:
    breite = max([len(repr(i.name)) for i in infos] + [10])
    z = [f"{'ManaBox-Binder':<{breite}}  {'Typ':<7}{'Posten':>8}{'Karten':>8}  Aktion"]
    for i in infos:
        e = zuordnung[i.schluessel]
        ziel = f" -> {e.kiste_name}" if e.aktion == "kiste" and e.kiste_name != i.name else ""
        z.append(f"{i.name!r:<{breite}}  {i.typ:<7}{i.posten:>8}{i.karten:>8}  {e.aktion}{ziel}")
    z.append("")
    je_typ: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for i in infos:
        t = je_typ[i.typ]
        t[0] += 1
        t[1] += i.posten
        t[2] += i.karten
    for typ, (a, p, k) in sorted(je_typ.items()):
        z.append(f"Typ {typ}: {a} Binder, {p} Posten, {k} Karten")
    kisten = [(i, zuordnung[i.schluessel]) for i in infos if zuordnung[i.schluessel].aktion == "kiste"]
    z.append(f"Als Kisten vorgesehen: {len(kisten)} Binder, {sum(i.posten for i, _ in kisten)} Posten, "
             f"{sum(i.karten for i, _ in kisten)} Karten")
    return "\n".join(z)


# ---------------------------------------------------------------- Aufruf


def _pruefe_datenbank(db: Path) -> None:
    if not db.is_file():
        raise ImportFehler(f"Datenbank fehlt: {db}")
    con = datenbank.verbinden(db)
    try:
        version = datenbank.version(con)
    finally:
        con.close()
    if version < MINDEST_VERSION:
        raise ImportFehler(f"Die Datenbank hat Version {version}, der Import braucht Version {MINDEST_VERSION} "
                           f"(Kisten). Erst migrieren: python -m binderapp.db.migrieren --db {db}")
    if not geschlossen(db):
        raise ImportFehler(f"{db} ist noch geöffnet (laufen Server oder Verarbeiter?). Erst beide beenden.")


def importieren(csv_pfad: Path, db: Path, katalog: Path | None, zuordnung_pfad: Path | None, probelauf: bool,
                bericht_ordner: Path, nutzer: str | None, zeit: str) -> tuple[int, Ergebnis | None]:
    """Der Import mit Prüfung, Sicherung, Transaktion und Bericht. Gibt (Exitcode, Ergebnis) zurück."""
    posten = lese_export(csv_pfad)
    infos = uebersicht(posten)
    zuordnung, hinweise = lese_zuordnung(zuordnung_pfad, infos)
    _auswahl(infos, zuordnung)  # früh prüfen
    _pruefe_datenbank(db)

    kat = oeffnen(katalog) if katalog is not None else oeffnen()
    if kat is None:
        print("Warnung: Katalog nicht gefunden, der Import läuft ohne Kartendaten (python -m binderapp.katalog.bau).",
              file=sys.stderr)
    sicherung = None
    start = time.perf_counter()
    con = datenbank.verbinden(db)
    try:
        nid = _nutzer(con, nutzer)
        if not probelauf:
            sicherung = datenbank.sichern(db, db.parent.parent / "sicherung")
        erg: Ergebnis | None = None
        try:
            with datenbank.schreiben(con):
                erg = _lauf(con, nid, posten, infos, zuordnung, kat)
                if probelauf:
                    raise _Zurueck()
        except _Zurueck:
            pass
    except sqlite3.Error as e:
        raise ImportFehler(f"Datenbankfehler, es wurde nichts geändert: {e}") from e
    finally:
        con.close()
        if kat is not None:
            kat.close()
    sekunden = time.perf_counter() - start

    doppel_datei = schreibe_doppel(bericht_ordner / f"moegliche-doppel-{zeit}.csv", erg.doppel)
    gemeinsam = dict(csv_name=csv_pfad.name, posten=posten, infos=infos, zuordnung=zuordnung, hinweise=hinweise,
                     probelauf=probelauf, sicherung=sicherung, db=db, sekunden=sekunden, doppel_datei=doppel_datei)
    datei = _freier_pfad(bericht_ordner / f"bericht-{zeit}.md")
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(baue_bericht(erg, grenze=None, **gemeinsam), encoding="utf-8")
    print(baue_bericht(erg, grenze=15, **gemeinsam))
    print(f"Bericht: {datei}")
    return 0, erg


def _liste_importieren(args, zeit: str) -> int:
    """Listen-Export in eine Kiste (listen_import.py): Prüfung, Sicherung, eine Transaktion, Bericht."""
    from binderapp.db import listen_import
    import hashlib

    daten = args.datei.read_bytes()
    vorher = hashlib.sha256(daten).hexdigest()
    listen_import_posten = lese_liste(daten, args.datei.name)  # früh prüfen, bevor etwas gesichert wird
    _pruefe_datenbank(args.db)
    kat = oeffnen(args.katalog) if args.katalog is not None else oeffnen()
    if kat is None:
        print("Warnung: Katalog nicht gefunden, der Import läuft ohne Kartendaten.", file=sys.stderr)
    con = datenbank.verbinden(args.db)
    sicherung = None
    try:
        nid = _nutzer(con, args.nutzer)
        if not args.probelauf:
            sicherung = datenbank.sichern(args.db, args.db.parent.parent / "sicherung")
        b = listen_import.importieren(con, nid, daten, args.datei.name, kiste_name=args.kiste,
                                      anlegen=args.kiste_anlegen, probelauf=args.probelauf, kat=kat)
    except sqlite3.Error as e:
        raise ImportFehler(f"Datenbankfehler, es wurde nichts geändert: {e}") from e
    finally:
        con.close()
        if kat is not None:
            kat.close()
    nachher = hashlib.sha256(args.datei.read_bytes()).hexdigest()
    zusatz = f"- Sicherung: {sicherung or 'übersprungen (Probelauf)'}\n"
    zusatz += f"- CSV unverändert: {'ja' if vorher == nachher else 'NEIN'} ({len(listen_import_posten)} Zeilen gelesen)\n"
    if not args.probelauf and b["neu"]:
        zusatz += "- Vorschaubilder: python -m binderapp.bilder.lokal --daten … (oder der Verarbeiter holt sie nach)\n"
    datei = _freier_pfad(args.bericht_ordner / f"liste-{zeit}.md")
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(listen_import.bericht_text(b, grenze=10 ** 6) + zusatz, encoding="utf-8")
    print(listen_import.bericht_text(b, grenze=15) + zusatz)
    print(f"Bericht: {datei}")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    teil = argparse.ArgumentParser(description="ManaBox-CSV wiederholbar in Kisten importieren (CSV wird nur gelesen).")
    teil.add_argument("datei", type=Path, help="ManaBox-Export (CSV)")
    teil.add_argument("--db", type=Path, default=STANDARD_DB, help="Datenbank (Standard: data/app/binder.sqlite)")
    teil.add_argument("--katalog", type=Path, default=STANDARD_KATALOG, help="Scryfall-Katalog (Standard: data/katalog.sqlite)")
    teil.add_argument("--uebersicht", action="store_true",
                      help="Nur lesen: je ManaBox-Binder Typ, Posten, Karten zeigen und eine Zuordnungsvorlage schreiben")
    teil.add_argument("--zuordnung", type=Path, help="Zuordnung der ManaBox-Binder (CSV, Vorlage aus --uebersicht)")
    teil.add_argument("--probelauf", action="store_true", help="Alles durchrechnen und zurückrollen (keine Sicherung)")
    teil.add_argument("--bericht-ordner", type=Path, default=AUSGABE, help="Ausgabe für Bericht, Doppelliste, Vorlage")
    teil.add_argument("--nutzer", help="Nutzername, falls die Datenbank mehrere hat")
    teil.add_argument("--kiste", help="Listen-Export (ohne Binderspalten) in diese Kiste importieren")
    teil.add_argument("--kiste-anlegen", action="store_true", help="mit --kiste: die Kiste anlegen, falls es sie nicht gibt")
    args = teil.parse_args(argv)
    zeit = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        if not args.datei.is_file():
            raise ImportFehler(f"Datei fehlt: {args.datei}")
        if args.kiste is not None:
            return _liste_importieren(args, zeit)
        if args.uebersicht:
            posten = lese_export(args.datei)
            infos = uebersicht(posten)
            zuordnung, hinweise = lese_zuordnung(args.zuordnung, infos)
            print(f"{args.datei.name}: {len(posten)} Zeilen, {sum(p.anzahl for p in posten)} Karten, {len(infos)} ManaBox-Binder\n")
            print(_uebersicht_text(infos, zuordnung))
            for h in hinweise:
                print("Hinweis:", h)
            vorlage = schreibe_vorlage(args.bericht_ordner / f"zuordnung-vorlage-{zeit}.csv", infos, zuordnung)
            print(f"\nZuordnungsvorlage: {vorlage}")
            print("Aktion je Binder: kiste oder ignorieren; kiste_name ist der Name in der App. "
                  "Danach: ... --zuordnung DATEI importieren.")
            return 0
        code, _ = importieren(args.datei, args.db, args.katalog, args.zuordnung, args.probelauf,
                              args.bericht_ordner, args.nutzer, zeit)
        return code
    except ImportFehler as e:
        print(f"Abbruch: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
