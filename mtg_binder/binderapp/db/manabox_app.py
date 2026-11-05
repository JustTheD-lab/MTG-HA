"""ManaBox-Import in der App: der Nutzer entscheidet je ManaBox-Binder, Liste und Deck, was wohin geht.

Entscheidungen des Nutzers (29.10. und 05.11.2026): Claude führt keinen Import aus, die App fragt nach. Listen und
Decks aus ManaBox sind doppelt erfasst, darum ist „Überspringen“ dort vorgewählt. Kaufpreise kommen mit.

    lesen(daten, name)                         CSV → Posten (Sammlungsexport oder Listen-Export)
    uebersicht(con, nid, posten)               Einträge mit Vorschlag, Kisten zur Auswahl
    importieren(con, nid, daten, name, plan, kat, probelauf=True)   Bericht als dict

Plan: je Eintrag {"name", "typ", "aktion", "kiste_id"?, "kiste_name"?} mit diesen Aktionen:

    neu             neue Kiste (`kiste_name`); gibt es schon eine Kiste zu diesem ManaBox-Binder, kommt alles dorthin
    kiste           vorhandene Kiste (`kiste_id`)
    regeln          nach den Sortierregeln; was keine Regel trifft, bleibt im Scan-Eingang
    ueberspringen   nichts

Wie bei `manabox_import`: Die Datei wird nur gelesen, der Schlüssel eines Postens ist `extern_id`, Vorhandenes bleibt
in seiner Kiste, im Export fehlende Posten werden nur gemeldet, vom Nutzer Geändertes und Gelöschtes bleibt, und mit
Scan-Karten wird nichts zusammengezählt. Ein Probelauf rechnet dasselbe und rollt zurück. Der ganze Import ist ein
Schritt und lässt sich mit `schritte.zuruecknehmen` zurücknehmen, solange die Karten unverändert sind.
"""
from __future__ import annotations

import csv
import io
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path

from binderapp.db import datenbank, erkennung, schritte
from binderapp.db import manabox_import as mi
from binderapp.db.listen_import import KISTENNAME_MAX, LISTE_ANZEIGE, MAX_BYTES
from binderapp.db.manabox_import import Ergebnis, ImportFehler, KistenZahlen, Posten
from binderapp.sortieren import einsortieren

AKTIONEN = ("neu", "kiste", "regeln", "ueberspringen")
TYP_LISTE = "liste"    # Listen-Export ohne Binderspalten: ein Eintrag mit dem Namen der Datei
EINLESEN = ("binder", TYP_LISTE)   # diese Typen sind vorgewählt, Listen und Decks aus ManaBox nicht


class _Zurueck(Exception):
    """Zum Zurückrollen im Probelauf."""


def lesen(daten: bytes, name: str) -> list[Posten]:
    if len(daten) > MAX_BYTES:
        raise ImportFehler(f"Die Datei ist zu groß (höchstens {MAX_BYTES // (1024 * 1024)} MB).")
    try:
        text = daten.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ImportFehler(f"{name}: keine UTF-8-Datei ({e.reason})") from None
    art = mi.format_erkennen(next(csv.reader(io.StringIO(text)), []))
    if art is None:
        raise ImportFehler(f"{name} ist kein ManaBox-Export.")
    spalten = mi.PFLICHTSPALTEN if art == "sammlung" else mi.LISTEN_SPALTEN
    posten, _ = mi._lese(io.StringIO(text, newline=""), name, spalten)
    if not posten:
        raise ImportFehler(f"{name} enthält keine Karten.")
    if art == "liste":
        stamm = Path(name).stem
        posten = [replace(p, binder=stamm, typ=TYP_LISTE) for p in posten]
    return posten


def _posten_von(con, nid: int, binder: str) -> tuple[int, int | None]:
    """(schon importierte Posten dieses ManaBox-Binders, Kiste mit den meisten davon)."""
    praefix = binder + "|"
    zeilen = con.execute(
        "SELECT kiste_id, COUNT(*) AS n FROM karte WHERE nutzer_id = ? AND quelle = ? AND substr(extern_id, 1, ?) = ?"
        " GROUP BY kiste_id ORDER BY n DESC", (nid, mi.QUELLE, len(praefix), praefix)).fetchall()
    kiste = next((z["kiste_id"] for z in zeilen if z["kiste_id"] is not None), None)
    return sum(z["n"] for z in zeilen), kiste


def uebersicht(con, nid: int, posten: list[Posten]) -> dict:
    kisten = con.execute("SELECT id, name, extern_name FROM kiste WHERE nutzer_id = ? ORDER BY regalplatz, name",
                         (nid,)).fetchall()
    nach_extern = {z["extern_name"]: z["id"] for z in kisten if z["extern_name"]}
    nach_name = {z["name"]: z["id"] for z in kisten}
    eintraege = []
    for info in mi.uebersicht(posten):
        name = info.name.strip()[:KISTENNAME_MAX]
        bekannt, ort = _posten_von(con, nid, info.name)
        ziel = ort or nach_extern.get(info.name) or nach_name.get(name)
        if info.typ not in EINLESEN:
            aktion = "ueberspringen"
        else:
            aktion = "kiste" if ziel else "neu"
        eintraege.append({"name": info.name, "typ": info.typ, "posten": info.posten, "karten": info.karten,
                          "bekannt": bekannt, "aktion": aktion, "kiste_id": ziel, "kiste_name": name})
    return {
        "eintraege": eintraege,
        "posten": len(posten),
        "karten": sum(p.anzahl for p in posten),
        "kisten": [{"id": z["id"], "name": z["name"]} for z in kisten],
        "regeln": len(einsortieren.regeln_lesen(con, nid)),
    }


def _plan_lesen(plan, posten: list[Posten]) -> dict[tuple[str, str], dict]:
    """Prüft den Plan gegen die Datei; gibt die gewählten Einträge je (Binder, Typ) zurück."""
    if not isinstance(plan, list):
        raise ImportFehler("Der Plan ist ungültig.")
    vorhanden = {i.schluessel for i in mi.uebersicht(posten)}
    gewaehlt: dict[tuple[str, str], dict] = {}
    gesehen = set()
    for e in plan:
        if not isinstance(e, dict) or e.get("aktion") not in AKTIONEN:
            raise ImportFehler("Der Plan ist ungültig.")
        schluessel = (e.get("name"), e.get("typ"))
        if schluessel not in vorhanden or schluessel in gesehen:
            raise ImportFehler("Der Plan passt nicht zur Datei. Bitte die Datei neu wählen.")
        gesehen.add(schluessel)
        if e["aktion"] != "ueberspringen":
            gewaehlt[schluessel] = e
    if gesehen != vorhanden:
        raise ImportFehler("Der Plan passt nicht zur Datei. Bitte die Datei neu wählen.")
    if not gewaehlt:
        raise ImportFehler("Es ist nichts zum Importieren gewählt.")
    # Der Schlüssel eines Postens kennt nur den Namen des Binders, nicht den Typ
    doppelt = sorted(n for n, c in Counter(n for n, _ in gewaehlt).items() if c > 1)
    if doppelt:
        raise ImportFehler("Diese Namen kommen als Binder und Liste vor, nur einer lässt sich importieren: "
                           + ", ".join(doppelt))
    return gewaehlt


def _kiste_ziel(con, nid: int, info, e: dict, schritt: str, neue: dict[str, str]) -> KistenZahlen:
    """Die Kiste eines Eintrags. Eine neue Kiste legt der Import mit an, sie geht beim Rückgängig mit weg."""
    if e["aktion"] == "regeln":
        return KistenZahlen(name="nach Sortierregeln", extern_name=info.name, id=erkennung.scan_kiste(con, nid))
    if e["aktion"] == "kiste":
        z = con.execute("SELECT id, name FROM kiste WHERE id = ? AND nutzer_id = ?", (e.get("kiste_id"), nid)).fetchone()
        if z is None:
            raise ImportFehler(f"„{info.name.strip()}“: Die gewählte Kiste gibt es nicht mehr.")
        return KistenZahlen(name=z["name"], extern_name=info.name, id=z["id"])
    z = con.execute("SELECT id, name FROM kiste WHERE nutzer_id = ? AND extern_name = ?", (nid, info.name)).fetchone()
    if z is not None:
        return KistenZahlen(name=z["name"], extern_name=info.name, id=z["id"])
    name = e.get("kiste_name")
    name = name.strip() if isinstance(name, str) else ""
    if not name or len(name) > KISTENNAME_MAX:
        raise ImportFehler(f"„{info.name.strip()}“: Der Kistenname muss 1 bis {KISTENNAME_MAX} Zeichen haben.")
    if name in neue or con.execute("SELECT 1 FROM kiste WHERE nutzer_id = ? AND name = ?", (nid, name)).fetchone():
        raise ImportFehler(f"Eine Kiste „{name}“ gibt es schon. Bitte einen anderen Namen oder „Vorhandene Kiste“ wählen.")
    neue[name] = info.name
    platz = con.execute("SELECT COALESCE(MAX(regalplatz), 0) + 1 FROM kiste WHERE nutzer_id = ?", (nid,)).fetchone()[0]
    kid = datenbank.einfuegen(con, nid, mi.QUELLE, "kiste", {"nutzer_id": nid, "name": name, "regalplatz": platz,
                                                             "extern_name": info.name}, schritt=schritt)
    return KistenZahlen(name=name, extern_name=info.name, id=kid, angelegt=True)


def _regeln_anwenden(con, nid: int, ids: list[int], schritt: str) -> dict:
    """Neue Posten aus dem Eingang einsortieren. Fehlt ein Wert (meist der Preis), wartet die Karte wie beim Scan."""
    stand = {"kisten": [], "keine": 0, "wartet": 0}
    if not ids:
        return stand
    karten = {z["id"]: z["anzahl"] for z in con.execute(
        "SELECT id, anzahl FROM karte WHERE nutzer_id = ? AND kiste_id = ?", (nid, erkennung.scan_kiste(con, nid)))}
    ergebnis = einsortieren.auswerten(con, nid, ids)
    ziele: dict[int, dict] = {}
    for kid in ids:
        e = ergebnis.get(kid, {"regel": None, "fehlt": ["Kartendaten"]})
        if e["fehlt"]:
            schritte.aendern_genau(con, nid, "karte", kid, {"sortieren": "offen"}, schritt=schritt, quelle=mi.QUELLE)
            stand["wartet"] += karten[kid]
        elif e["regel"] is None:
            stand["keine"] += karten[kid]
        else:
            regel = e["regel"]
            schritte.aendern_genau(con, nid, "karte", kid, {"kiste_id": regel["kiste_id"]}, schritt=schritt,
                                   quelle=mi.QUELLE)
            ziel = ziele.setdefault(regel["kiste_id"], {"id": regel["kiste_id"], "name": regel["ziel"], "karten": 0})
            ziel["karten"] += karten[kid]
    stand["kisten"] = sorted(ziele.values(), key=lambda z: -z["karten"])
    return stand


def _fehlende(con, nid: int, importiert: list[Posten], binder: set[str]) -> list[dict]:
    """Posten der gewählten ManaBox-Binder, die im Export nicht mehr stehen. Sie bleiben, wo sie sind."""
    schluessel = {p.extern_id for p in importiert}
    aus = []
    for z in con.execute("SELECT id, extern_id, name, set_code, nummer, sprache, foil, anzahl FROM karte"
                         " WHERE nutzer_id = ? AND quelle = ? AND extern_id IS NOT NULL ORDER BY id", (nid, mi.QUELLE)):
        if z["extern_id"] not in schluessel and z["extern_id"].rsplit("|", 4)[0] in binder:
            aus.append(dict(z))
    return aus


def _lauf(con, nid: int, posten: list[Posten], gewaehlt: dict[tuple[str, str], dict], kat, schritt: str) -> dict:
    erg = Ergebnis(mit_katalog=kat is not None)
    historie_vorher = con.execute("SELECT COUNT(*) FROM historie").fetchone()[0]
    importiert = [p for p in posten if (p.binder, p.typ) in gewaehlt]
    mi.schluessel_pruefen(importiert)

    ziele: dict[tuple[str, str], KistenZahlen] = {}
    neue_namen: dict[str, str] = {}
    infos = {i.schluessel: i for i in mi.uebersicht(importiert)}
    for schluessel, e in gewaehlt.items():
        ziele[schluessel] = _kiste_ziel(con, nid, infos[schluessel], e, schritt, neue_namen)

    bestand = {z["extern_id"]: dict(z) for z in con.execute(
        "SELECT id, extern_id, kiste_id FROM karte WHERE nutzer_id = ? AND quelle = ? AND extern_id IS NOT NULL",
        (nid, mi.QUELLE))}
    nutzer_felder, nutzer_geloescht = mi._nutzer_aenderungen(con)
    cache: dict = {}
    nach_regeln: list[int] = []
    for p in importiert:
        schluessel = (p.binder, p.typ)
        neu = mi._posten_uebernehmen(con, nid, p, ziele[schluessel], kat, cache, bestand, nutzer_felder,
                                     nutzer_geloescht, erg, schritt=schritt)
        if neu is not None and gewaehlt[schluessel]["aktion"] == "regeln":
            nach_regeln.append(neu)
    # Die Regeln brauchen Kartendaten, darum erst danach
    if kat is not None:
        erg.kartendaten = mi.kartendaten.uebernehmen(con, kat)
    regeln = _regeln_anwenden(con, nid, nach_regeln, schritt)

    fehlende = _fehlende(con, nid, importiert, {n for n, _ in gewaehlt})
    kisten_ids = sorted({k.id for k in ziele.values()} | {z["id"] for z in regeln["kisten"]})
    doppel = mi._doppel(con, nid, kisten_ids)
    daten = sum(1 for p in importiert if p.scryfall_id and con.execute(
        "SELECT 1 FROM kartendaten WHERE scryfall_id = ?", (p.scryfall_id,)).fetchone())
    summe = lambda f: sum(getattr(k, f) for k in ziele.values())   # noqa: E731
    return {
        "posten": len(importiert),
        "karten": sum(p.anzahl for p in importiert),
        "neu": summe("neu"), "geaendert": summe("geaendert"), "unveraendert": summe("unveraendert"),
        "nutzer_geloescht": summe("nutzer_geloescht"), "geschuetzt": len(erg.geschuetzt),
        "kaufpreise": sum(1 for p in importiert if p.kaufpreis),
        "mit_kartendaten": daten,
        "eintraege": [{"name": n, "typ": t, "aktion": gewaehlt[(n, t)]["aktion"], "ziel": k.name, "kiste_neu": k.angelegt,
                       "posten": k.posten, "karten": k.karten, "neu": k.neu, "geaendert": k.geaendert,
                       "unveraendert": k.unveraendert} for (n, t), k in ziele.items()],
        "regeln": regeln if any(e["aktion"] == "regeln" for e in gewaehlt.values()) else None,
        "ohne_scryfall": [{"zeile": p.zeile, "name": p.name} for p in erg.ohne_scryfall][:LISTE_ANZEIGE],
        "ohne_scryfall_anzahl": len(erg.ohne_scryfall),
        "fehlend": [{"name": d["name"], "set": d["set_code"], "nummer": d["nummer"], "sprache": d["sprache"],
                     "foil": bool(d["foil"]), "anzahl": d["anzahl"]} for d in fehlende][:LISTE_ANZEIGE],
        "fehlend_anzahl": len(fehlende),
        "doppel": [{"name": d["name"], "set": d["set_code"], "nummer": d["nummer"], "sprache": d["sprache"],
                    "foil": bool(d["foil"]), "kiste": d["kiste"], "binder": d["binder"], "seite": d["seite"]}
                   for d in doppel][:LISTE_ANZEIGE],
        "doppel_anzahl": len({d["kisten_karte"] for d in doppel}),
        "historie_neu": con.execute("SELECT COUNT(*) FROM historie").fetchone()[0] - historie_vorher,
    }


def importieren(con, nid: int, daten: bytes, name: str, plan, kat, *, probelauf: bool = True) -> dict:
    """Liest die Datei und importiert in einer Transaktion (Probelauf: zurückgerollt). `con` ist eine schreibbare
    Verbindung ohne laufende Transaktion. Der Bericht nennt `schritt`, wenn etwas geschrieben wurde."""
    posten = lesen(daten, name)
    gewaehlt = _plan_lesen(plan, posten)
    start = time.perf_counter()
    schritt = schritte.neuer_schritt()
    bericht: dict = {}
    try:
        with datenbank.schreiben(con):
            bericht = _lauf(con, nid, posten, gewaehlt, kat, schritt)
            if probelauf:
                raise _Zurueck()
    except _Zurueck:
        pass
    bericht.update(probelauf=probelauf, datei=name, sekunden=round(time.perf_counter() - start, 2),
                   schritt=None if probelauf or not (bericht["neu"] or bericht["geaendert"]) else schritt)
    return bericht
