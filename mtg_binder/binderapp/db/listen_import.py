"""Listen-Import: ein ManaBox-Listen-Export (CSV ohne „Binder Name“ und „Binder Type“) wiederholbar in eine
gewählte Kiste (Paket H, 28.10.2026).

Aufruf (bei beendetem Server und Verarbeiter), zuerst als Probelauf:
    $env:PYTHONPATH = "src"; .venv\\Scripts\\python.exe -m binderapp.db.manabox_import LISTE.csv
        --kiste "Gelber Binder (Test)" [--kiste-anlegen] [--probelauf] [--db …] [--katalog …]
In der Oberfläche: Kistenansicht → „Liste importieren“ (nur angemeldet, Ziel ist die geöffnete Kiste),
Route POST /api/kiste/{id}/liste in web/kistenschreiben.py.

Regeln
- Die CSV wird nur gelesen (Parser `manabox_import.lese_liste`, auch für den Abzug in Paket M).
- Schlüssel je Ziel-Kiste: ``liste:<Kisten-ID>|ManaBox ID|Foil|Sprache|Zustand`` in ``karte.extern_id``,
  Quelle ``manabox``. Gleicher Schlüssel: nur abweichende Felder werden geändert (mit Historie), vom Nutzer
  geänderte Felder und vom Nutzer gelöschte Posten fasst der Import nicht an (wie der Sammlungsimport).
- Im Export fehlende Posten werden nie gelöscht, nur gemeldet. Mögliche Doppel (gleiche Scryfall-ID,
  Sprache und Foil in einer anderen Kiste oder als Scan-Karte in einem Binder) werden nur aufgelistet.
- Alles in einer Transaktion; ein Fehler oder der Probelauf lässt die Datenbank unverändert.
Nur Standardbibliothek, Python 3.11 kompatibel; läuft auch im Server (ohne Bildverarbeitung).
"""
from __future__ import annotations

import hashlib
import time
from collections import Counter, defaultdict
from dataclasses import replace

from binderapp.db import datenbank
from binderapp.db import manabox_import as mi
from binderapp.db.manabox_import import ImportFehler, KistenZahlen, Ergebnis, Posten

MAX_BYTES = 5 * 1024 * 1024   # größte Listendatei (der ganze Sammlungsexport hat rund 2 MB)
LISTE_ANZEIGE = 200           # so viele Einträge je Liste höchstens im Bericht der Oberfläche
KISTENNAME_MAX = 80


def praefix(kiste_id: int) -> str:
    return f"liste:{int(kiste_id)}"


class _Zurueck(Exception):
    """Zum Zurückrollen im Probelauf."""


def kiste_finden(con, nid: int, *, kiste_id: int | None = None, name: str | None = None,
                 anlegen: bool = False) -> tuple[int, str, bool]:
    """(ID, Name, neu angelegt) der Ziel-Kiste. Anlegen nur mit `anlegen` (in der Transaktion des Aufrufers)."""
    if kiste_id is not None:
        z = con.execute("SELECT id, name FROM kiste WHERE id = ? AND nutzer_id = ?", (kiste_id, nid)).fetchone()
        if z is None:
            raise ImportFehler("Die Kiste gibt es nicht.")
        return z["id"], z["name"], False
    name = (name or "").strip()
    if not name or len(name) > KISTENNAME_MAX:
        raise ImportFehler(f"Der Kistenname muss 1 bis {KISTENNAME_MAX} Zeichen haben.")
    z = con.execute("SELECT id, name FROM kiste WHERE nutzer_id = ? AND name = ?", (nid, name)).fetchone()
    if z is not None:
        return z["id"], z["name"], False
    if not anlegen:
        raise ImportFehler(f"Eine Kiste {name!r} gibt es nicht (mit --kiste-anlegen neu anlegen).")
    platz = con.execute("SELECT COALESCE(MAX(regalplatz), 0) + 1 FROM kiste WHERE nutzer_id = ?", (nid,)).fetchone()[0]
    kid = datenbank.einfuegen(con, nid, mi.QUELLE, "kiste", {"nutzer_id": nid, "name": name, "regalplatz": platz})
    return kid, name, True


def doppel(con, nid: int, kiste_id: int) -> list[dict]:
    """Mögliche Doppel der Listen-Posten dieser Kiste: gleiche Scryfall-ID, Sprache und Foil in einer anderen
    Kiste oder in einer Tasche. Nur Auskunft, nichts wird verrechnet."""
    p = praefix(kiste_id) + "|"
    return [dict(z) for z in con.execute(
        "SELECT k.id AS posten, k.name AS name, k.set_code AS set_code, k.nummer AS nummer, k.sprache AS sprache, "
        "k.foil AS foil, k.anzahl AS anzahl, a.id AS andere, a.quelle AS quelle_andere, a.anzahl AS anzahl_andere, "
        "a.status AS status_andere, ki.name AS kiste, b.name AS binder, se.nummer AS seite, t.zeile AS zeile, "
        "t.spalte AS spalte "
        "FROM karte k JOIN karte a ON a.nutzer_id = k.nutzer_id AND a.id <> k.id AND a.scryfall_id = k.scryfall_id "
        "  AND a.sprache IS k.sprache AND a.foil = k.foil "
        "  AND ((a.kiste_id IS NOT NULL AND a.kiste_id <> k.kiste_id) OR a.tasche_id IS NOT NULL) "
        "LEFT JOIN kiste ki ON ki.id = a.kiste_id LEFT JOIN tasche t ON t.id = a.tasche_id "
        "LEFT JOIN seite se ON se.id = t.seite_id LEFT JOIN binder b ON b.id = se.binder_id "
        "WHERE k.nutzer_id = ? AND k.kiste_id = ? AND substr(k.extern_id, 1, ?) = ? "
        "ORDER BY k.name COLLATE NOCASE, k.set_code, k.nummer, ki.regalplatz, b.regalplatz, se.nummer, t.zeile, t.spalte",
        (nid, kiste_id, len(p), p))]


def lauf(con, nid: int, posten: list[Posten], kiste_id: int, kiste_name: str, kat) -> Ergebnis:
    """Der Import in der Transaktion des Aufrufers."""
    erg = Ergebnis(mit_katalog=kat is not None)
    historie_vorher = con.execute("SELECT COUNT(*) FROM historie").fetchone()[0]
    posten = [replace(p, binder=praefix(kiste_id), typ="liste") for p in posten]

    zaehler = Counter(p.extern_id for p in posten)
    doppelt = [s for s, c in zaehler.items() if c > 1]
    if doppelt:
        zeilen: dict[str, list[int]] = defaultdict(list)
        for p in posten:
            if zaehler[p.extern_id] > 1:
                zeilen[p.extern_id].append(p.zeile)
        raise ImportFehler("Schlüssel (ManaBox ID, Foil, Sprache, Zustand) kommt mehrfach vor, ein Zusammenzählen "
                           "findet nicht statt: " + "; ".join(f"Zeilen {z}" for z in list(zeilen.values())[:10]))

    k = KistenZahlen(name=kiste_name, extern_name=praefix(kiste_id), id=kiste_id)
    erg.kisten = [k]
    bestand = {z["extern_id"]: dict(z) for z in con.execute(
        "SELECT id, extern_id, kiste_id FROM karte WHERE nutzer_id = ? AND quelle = ? AND extern_id IS NOT NULL",
        (nid, mi.QUELLE))}
    nutzer_felder, nutzer_geloescht = mi._nutzer_aenderungen(con)
    cache: dict = {}
    for p in posten:
        mi._posten_uebernehmen(con, nid, p, k, kat, cache, bestand, nutzer_felder, nutzer_geloescht, erg)

    schluessel = set(zaehler)
    pf = praefix(kiste_id) + "|"
    for z in con.execute(
        "SELECT id, extern_id, kiste_id, name, set_code, nummer, sprache, foil, anzahl FROM karte "
        "WHERE nutzer_id = ? AND quelle = ? AND substr(extern_id, 1, ?) = ? ORDER BY id",
        (nid, mi.QUELLE, len(pf), pf),
    ):
        if z["extern_id"] not in schluessel:
            k.fehlend += 1
            erg.fehlende.append({**dict(z), "kiste": kiste_name})

    if kat is not None:
        erg.kartendaten = mi.kartendaten.uebernehmen(con, kat)
    z = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(k.anzahl), 0), COALESCE(SUM(d.scryfall_id IS NOT NULL), 0), "
        "COALESCE(SUM(CASE WHEN d.scryfall_id IS NOT NULL THEN k.anzahl ELSE 0 END), 0) "
        "FROM karte k LEFT JOIN kartendaten d ON d.scryfall_id = k.scryfall_id "
        "WHERE k.nutzer_id = ? AND k.quelle = ? AND substr(k.extern_id, 1, ?) = ?",
        (nid, mi.QUELLE, len(pf), pf),
    ).fetchone()
    erg.anteil = {"posten": z[0], "karten": z[1], "posten_mit": z[2], "karten_mit": z[3]}
    erg.doppel = doppel(con, nid, kiste_id)
    erg.historie_neu = con.execute("SELECT COUNT(*) FROM historie").fetchone()[0] - historie_vorher
    return erg


def importieren(con, nid: int, daten: bytes, dateiname: str, *, kiste_id: int | None = None,
                kiste_name: str | None = None, anlegen: bool = False, probelauf: bool = True, kat=None) -> dict:
    """Liest die Liste, importiert in einer Transaktion (Probelauf: zurückgerollt) und gibt den Bericht als dict.
    `con` ist eine schreibbare Verbindung ohne laufende Transaktion."""
    if len(daten) > MAX_BYTES:
        raise ImportFehler(f"Die Datei ist zu groß (höchstens {MAX_BYTES // (1024 * 1024)} MB).")
    posten = mi.lese_liste(daten, dateiname)
    start = time.perf_counter()
    ziel: tuple[int, str, bool] | None = None
    erg: Ergebnis | None = None
    try:
        with datenbank.schreiben(con):
            ziel = kiste_finden(con, nid, kiste_id=kiste_id, name=kiste_name, anlegen=anlegen)
            erg = lauf(con, nid, posten, ziel[0], ziel[1], kat)
            if probelauf:
                raise _Zurueck()
    except _Zurueck:
        pass
    return bericht(erg, posten, dateiname, hashlib.sha256(daten).hexdigest(), ziel, probelauf,
                   time.perf_counter() - start)


def bericht(erg: Ergebnis, posten: list[Posten], dateiname: str, sha256: str, ziel: tuple[int, str, bool],
            probelauf: bool, sekunden: float) -> dict:
    """Bericht als dict (Oberfläche) – `bericht_text` macht daraus Text für die Konsole."""
    k = erg.kisten[0]
    doppel_posten = {d["posten"] for d in erg.doppel}
    gegen_kisten = {d["posten"] for d in erg.doppel if d["kiste"] is not None}
    gegen_binder = {d["posten"] for d in erg.doppel if d["binder"] is not None}
    return {
        "probelauf": probelauf,
        "datei": dateiname,
        "sha256": sha256,
        "kiste": {"id": ziel[0], "name": ziel[1], "neu": ziel[2] and not probelauf, "wird_angelegt": ziel[2]},
        "posten": len(posten),
        "karten": sum(p.anzahl for p in posten),
        "foil_posten": sum(p.ist_foil for p in posten),
        "foil_karten": sum(p.anzahl for p in posten if p.ist_foil),
        "neu": k.neu, "geaendert": k.geaendert, "unveraendert": k.unveraendert,
        "nutzer_geloescht": k.nutzer_geloescht,
        "geschuetzt": len(erg.geschuetzt),
        "geaenderte_felder": dict(erg.geaenderte_felder),
        "kartendaten": erg.anteil if erg.mit_katalog else None,
        "katalog": erg.mit_katalog,
        "ohne_scryfall": [{"zeile": p.zeile, "name": p.name} for p in erg.ohne_scryfall][:LISTE_ANZEIGE],
        "ohne_scryfall_anzahl": len(erg.ohne_scryfall),
        "fehlend": [{"name": d["name"], "set": d["set_code"], "nummer": d["nummer"], "sprache": d["sprache"],
                     "foil": bool(d["foil"]), "anzahl": d["anzahl"]} for d in erg.fehlende][:LISTE_ANZEIGE],
        "fehlend_anzahl": len(erg.fehlende),
        "doppel": {
            "posten": len(doppel_posten), "gegen_kisten": len(gegen_kisten), "gegen_binder": len(gegen_binder),
            "liste": [{
                "name": d["name"], "set": d["set_code"], "nummer": d["nummer"], "sprache": d["sprache"],
                "foil": bool(d["foil"]), "anzahl": d["anzahl"], "quelle": d["quelle_andere"],
                "anzahl_dort": d["anzahl_andere"],
                "ort": (f"Kiste {d['kiste']}" if d["kiste"] is not None else
                        f"Binder {d['binder']} · Seite {d['seite']} · Zeile {d['zeile']}/Spalte {d['spalte']}"),
            } for d in erg.doppel][:LISTE_ANZEIGE],
        },
        "historie_neu": erg.historie_neu,
        "sekunden": round(sekunden, 3),
    }


def bericht_text(b: dict, grenze: int = 15) -> str:
    z = [f"# Listen-Import{' (Probelauf, nichts geschrieben)' if b['probelauf'] else ''}", ""]
    kiste = b["kiste"]
    marke = " (wird neu angelegt)" if kiste["wird_angelegt"] and b["probelauf"] else (" (neu angelegt)" if kiste["neu"] else "")
    z.append(f"- Datei: {b['datei']} (SHA-256 {b['sha256'][:16]}…)")
    z.append(f"- Ziel-Kiste: {kiste['name']}{marke}")
    z.append(f"- {b['posten']} Posten, {b['karten']} Karten, Foil {b['foil_posten']} Posten / {b['foil_karten']} Karten")
    z.append(f"- neu {b['neu']}, geändert {b['geaendert']}, unverändert {b['unveraendert']}, "
             f"vom Nutzer gelöscht (nicht neu angelegt) {b['nutzer_geloescht']}, vom Nutzer geändert (geschützt) {b['geschuetzt']}")
    if b["kartendaten"] is None:
        z.append("- Kartendaten: kein Katalog (python -m binderapp.katalog.bau)")
    else:
        a = b["kartendaten"]
        z.append(f"- Kartendaten: {a['posten_mit']} von {a['posten']} Posten")
    z.append(f"- Zeilen ohne Scryfall-ID: {b['ohne_scryfall_anzahl']}")
    for p in b["ohne_scryfall"][:grenze]:
        z.append(f"  - Zeile {p['zeile']}: {p['name']}")
    z.append(f"- Im Export fehlend (nicht gelöscht): {b['fehlend_anzahl']}")
    for d in b["fehlend"][:grenze]:
        z.append(f"  - {d['name']} ({(d['set'] or '').upper()} {d['nummer']}, {d['sprache']}, "
                 f"{'Foil' if d['foil'] else 'normal'}, {d['anzahl']}x)")
    dp = b["doppel"]
    z.append(f"- Mögliche Doppel (nur gemeldet): {dp['posten']} Posten, davon gegen andere Kisten {dp['gegen_kisten']}, "
             f"gegen Scan-Binder {dp['gegen_binder']}")
    for d in dp["liste"][:grenze]:
        z.append(f"  - {d['name']} ({(d['set'] or '').upper()} {d['nummer']}, {d['sprache']}, "
                 f"{'Foil' if d['foil'] else 'normal'}) auch in {d['ort']} ({d['quelle']}, {d['anzahl_dort']}x)")
    if len(dp["liste"]) > grenze:
        z.append(f"  - … und {len(dp['liste']) - grenze} weitere")
    z.append(f"- Neue Historienzeilen: {b['historie_neu']}, Laufzeit {b['sekunden']:.2f} s".replace(".", ","))
    return "\n".join(z) + "\n"
