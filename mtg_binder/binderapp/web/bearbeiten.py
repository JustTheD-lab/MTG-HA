"""Binder bearbeiten und Foil-Modus (Runde vom 02.11.2026), alle nur angemeldet.

    POST /api/karte/{id}/foil   {"foil": bool}
         Schaltet **ein Exemplar** um (Entscheidung des Nutzers): Ein Stapel teilt sich, das umgeschaltete
         Exemplar kommt zu einem passenden Posten am selben Ort (gleicher Druck, Sprache, Zustand, Quelle, Status,
         Prüfung, ohne ManaBox-Schlüssel) oder wird ein eigener Posten. `neu_laden`: Posten sind entstanden oder
         verschwunden, die Ansicht sollte neu laden.
    POST /api/seite/{id}/foil   {"foil": bool}      alle Karten der Seite (ganze Stapel), ein Schritt
    POST /api/tasche/{id}/verschieben  {"ziel": {"seite_id", "zeile", "spalte"}} oder {"ziel": {"binder_id"}}
         Karten der Tasche an eine andere Stelle (auch in einen anderen Binder; ohne Stelle: nächste freie
         Tasche). Ist das Ziel besetzt, wird getauscht. Inhalt und Foto-Ausschnitt wandern mit den Karten.
    POST /api/seite/{id}/karte  {"zeile", "spalte", "scryfall_id", "foil"?, "sprache"?}
         Eine Karte aus dem Katalog in eine Tasche ohne Karte legen (leer, Rückseite oder unklar; Quelle „manuell“,
         Status „sicher“, geprüft).
    POST /api/tasche/{id}/leeren
         Rückseite oder unklare Tasche ohne Karte als leer markieren (04.11.2026).
    POST /api/karten/zurueckstellen  {"karten": [IDs]} oder {"auswahl": …} wie beim Verschieben
         Sichere oder geprüfte Karten in die Kiste „Zurückgestellt“; unsichere bleiben (`uebersprungen`).
    GET  /api/zurueckgestellt   Karten, die sich in eine Tasche legen lassen
    POST /api/seite/{id}/zurueckgestellt  {"zeile", "spalte", "karte_id"}
         Ein Exemplar aus „Zurückgestellt“ in eine Tasche ohne Karte legen (ein Stapel teilt sich, 05.11.2026).
    POST /api/seite/{id}/loeschen  {"nummer": n}
         Ganze Seite löschen (Wunsch des Nutzers, 07.11.2026): Karten der Seite aus der Sammlung entfernen, die Seite
         wird eine Leerseite an derselben Stelle (Nummern bleiben), das Originalfoto bleibt liegen. Ein Schritt.

Löschen läuft über DELETE /api/karte/{id} (pruefen.py), in eine Kiste über POST /api/karten/verschieben
(kistenschreiben.py). Jeder Schritt lässt sich mit POST /api/schritt/{schritt}/rueckgaengig zurücknehmen.
"""
from __future__ import annotations

from contextlib import closing

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from binderapp.db import erkennung, schritte
from binderapp.db.datenbank import version
from binderapp.db.seiten import raster_masse
from binderapp.katalog import kartendaten
from binderapp.preise import werte, zeitplan
from binderapp.sortieren.einsortieren import sortierbar
from binderapp.web.helfer import Fehler, fehler
from binderapp.web.kisten import _ab_version_3
from binderapp.web.kistenschreiben import (ANZAHL_MAX, _ganzzahl, auswahl_ids, auswahl_lesen, druck_nachschlagen,
                                           verschieben as karten_verschieben)
from binderapp.web.pruefen import SPRACHEN, _kennung

__all__ = ["routen", "foil_umschalten", "seite_foil", "tasche_verschieben", "karte_einlegen", "tasche_leeren",
           "zurueckstellen", "zurueckgestellte", "zurueckgestellte_einlegen", "seite_loeschen"]

GROSS = 2 ** 62
NICHT_KOPIEREN = ("id", "angelegt", "geaendert")
ZURUECK_LISTE_MAX = 2000
SEITE_GELOESCHT = "Die Seite dieses Fotos wurde gelöscht."


def _foil(wert) -> bool:
    if not isinstance(wert, bool):
        raise Fehler("„foil“ muss „true“ oder „false“ sein.")
    return wert


def _seite_holen(con, nutzer: int, seite_id: int):
    s = con.execute("SELECT s.id, s.binder_id, b.name AS binder_name, b.raster FROM seite s"
                    " JOIN binder b ON b.id = s.binder_id WHERE s.id = ? AND b.nutzer_id = ?",
                    (seite_id, nutzer)).fetchone()
    if s is None:
        raise Fehler("Seite nicht gefunden.", 404)
    return s


def _position(seite, zeile, spalte) -> tuple[int, int]:
    zeilen, spalten = raster_masse(seite["raster"])
    return _ganzzahl(zeile, "zeile", 1, zeilen), _ganzzahl(spalte, "spalte", 1, spalten)


def _tasche_an(con, seite_id: int, zeile: int, spalte: int):
    return con.execute("SELECT * FROM tasche WHERE seite_id = ? AND zeile = ? AND spalte = ?",
                       (seite_id, zeile, spalte)).fetchone()


def _karten_in(con, tasche_id: int) -> list[int]:
    return [z[0] for z in con.execute("SELECT id FROM karte WHERE tasche_id = ? ORDER BY id", (tasche_id,))]


# ---------------------------------------------------------------- Foil


def foil_umschalten(con, nutzer: int, karte_id: int, foil: bool) -> dict:
    k = con.execute("SELECT * FROM karte WHERE id = ? AND nutzer_id = ?", (karte_id, nutzer)).fetchone()
    if k is None:
        raise Fehler("Karte nicht gefunden.", 404)
    if bool(k["foil"]) == foil:
        return {"schritt": None, "karte_id": karte_id, "foil": foil, "neu_laden": False,
                "preis": werte.preise_fuer_karten(con, [karte_id]).get(karte_id)}
    schritt = schritte.neuer_schritt()
    ort = "tasche_id" if k["tasche_id"] is not None else "kiste_id" if k["kiste_id"] is not None else None
    partner = None
    if ort is not None:   # Spaltennamen aus fester Auswahl, Werte als Parameter
        # Marken der Sortierregeln (ab Version 6) müssen gleich sein, sonst gingen sie beim Zusammenfassen verloren
        marken = [s for s in ("sortieren", "vorgemerkt_binder_id") if s in k.keys()]
        partner = con.execute(
            f"SELECT id, anzahl FROM karte WHERE nutzer_id = ? AND id != ? AND {ort} = ? AND scryfall_id IS ?"
            " AND sprache IS ? AND zustand IS ? AND quelle = ? AND status = ? AND geprueft = ? AND foil = ?"
            " AND extern_id IS NULL" + "".join(f" AND {s} IS ?" for s in marken) + " ORDER BY id LIMIT 1",
            (nutzer, karte_id, k[ort], k["scryfall_id"], k["sprache"], k["zustand"], k["quelle"], k["status"],
             k["geprueft"], int(foil), *(k[s] for s in marken))).fetchone()
        if partner is not None and partner["anzahl"] >= ANZAHL_MAX:
            partner = None
    # Ein Scan-Foto zeigt auf seine Karte (ON DELETE SET NULL, ohne Historie): solche Karten nie wegfassen
    hat_foto = version(con) >= 5 and con.execute("SELECT 1 FROM scan_foto WHERE karte_id = ? LIMIT 1",
                                                 (karte_id,)).fetchone() is not None
    if k["anzahl"] == 1 and (partner is None or k["extern_id"] is not None or hat_foto):
        schritte.aendern_genau(con, nutzer, "karte", karte_id, {"foil": int(foil)}, schritt=schritt)
        ergebnis, neu_laden = karte_id, False
    else:
        if k["anzahl"] == 1:
            schritte.loeschen(con, nutzer, "karte", karte_id, schritt=schritt)
        else:
            schritte.aendern_genau(con, nutzer, "karte", karte_id, {"anzahl": k["anzahl"] - 1}, schritt=schritt)
        if partner is not None:
            schritte.aendern_genau(con, nutzer, "karte", partner["id"], {"anzahl": partner["anzahl"] + 1},
                                   schritt=schritt)
            ergebnis = partner["id"]
        else:
            felder = {s: k[s] for s in k.keys() if s not in NICHT_KOPIEREN}
            felder.update({"anzahl": 1, "foil": int(foil), "extern_id": None})
            ergebnis = schritte.einfuegen(con, nutzer, "karte", felder, schritt=schritt)
        neu_laden = True
    return {"schritt": schritt, "karte_id": ergebnis, "foil": foil, "neu_laden": neu_laden,
            "preis": werte.preise_fuer_karten(con, [ergebnis]).get(ergebnis)}


def seite_foil(con, nutzer: int, seite_id: int, foil: bool) -> dict:
    _seite_holen(con, nutzer, seite_id)
    schritt = schritte.neuer_schritt()
    karten = con.execute("SELECT k.id FROM karte k JOIN tasche t ON t.id = k.tasche_id"
                         " WHERE t.seite_id = ? AND k.nutzer_id = ? AND k.foil != ? ORDER BY k.id",
                         (seite_id, nutzer, int(foil))).fetchall()
    for z in karten:
        schritte.aendern_genau(con, nutzer, "karte", z["id"], {"foil": int(foil)}, schritt=schritt)
    return {"schritt": schritt if karten else None, "geaendert": len(karten), "foil": foil}


# ---------------------------------------------------------------- Verschieben und Tauschen


def freie_tasche(con, nutzer: int, binder_id: int, ausser: int | None = None) -> tuple[int, int, int]:
    """(seite_id, zeile, spalte) der ersten freien Tasche des Binders in Blätterreihenfolge. Frei ist eine Stelle
    ohne Taschen-Eintrag oder eine leere Tasche ohne Karte (unsichere Taschen und Rückseiten nicht)."""
    b = con.execute("SELECT id, name, raster FROM binder WHERE id = ? AND nutzer_id = ?", (binder_id, nutzer)).fetchone()
    if b is None:
        raise Fehler("Binder nicht gefunden.", 404)
    zeilen, spalten = raster_masse(b["raster"])
    taschen = {(z["seite_id"], z["zeile"], z["spalte"]): z for z in con.execute(
        "SELECT t.id, t.seite_id, t.zeile, t.spalte, t.inhalt,"
        " EXISTS (SELECT 1 FROM karte k WHERE k.tasche_id = t.id) AS belegt"
        " FROM tasche t JOIN seite s ON s.id = t.seite_id WHERE s.binder_id = ?", (binder_id,))}
    for (seite_id,) in con.execute("SELECT id FROM seite WHERE binder_id = ? ORDER BY nummer", (binder_id,)):
        for z in range(1, zeilen + 1):
            for s in range(1, spalten + 1):
                t = taschen.get((seite_id, z, s))
                if t is None or (not t["belegt"] and t["inhalt"] == "leer" and t["id"] != ausser):
                    return seite_id, z, s
    raise Fehler(f"Im Binder „{b['name']}“ ist keine Tasche frei. Bitte zuerst eine Leerseite anlegen.", 409)


def tasche_verschieben(con, nutzer: int, tasche_id: int, ziel: dict) -> dict:
    a = con.execute("SELECT t.*, s.binder_id FROM tasche t JOIN seite s ON s.id = t.seite_id"
                    " JOIN binder b ON b.id = s.binder_id WHERE t.id = ? AND b.nutzer_id = ?",
                    (tasche_id, nutzer)).fetchone()
    if a is None:
        raise Fehler("Tasche nicht gefunden.", 404)
    karten_a = _karten_in(con, tasche_id)
    if not karten_a:
        raise Fehler("In dieser Tasche liegt keine Karte.", 409)
    if set(ziel) == {"binder_id"}:
        seite_id, zeile, spalte = freie_tasche(con, nutzer, _ganzzahl(ziel["binder_id"], "binder_id", 1, GROSS),
                                               ausser=tasche_id)
        seite = _seite_holen(con, nutzer, seite_id)
    elif set(ziel) == {"seite_id", "zeile", "spalte"}:
        seite = _seite_holen(con, nutzer, _ganzzahl(ziel["seite_id"], "seite_id", 1, GROSS))
        zeile, spalte = _position(seite, ziel["zeile"], ziel["spalte"])
    else:
        raise Fehler("„ziel“ braucht entweder „binder_id“ oder „seite_id“, „zeile“ und „spalte“.")
    vorher = schritte.summe(con, nutzer)
    schritt = schritte.neuer_schritt()
    b = _tasche_an(con, seite["id"], zeile, spalte)
    if b is not None and b["id"] == tasche_id:
        raise Fehler("Die Karte liegt schon in dieser Tasche.")
    if b is None:
        neu = schritte.einfuegen(con, nutzer, "tasche", {"seite_id": seite["id"], "zeile": zeile, "spalte": spalte,
                                                         "inhalt": "leer", "ausschnitt": None}, schritt=schritt)
        b = con.execute("SELECT * FROM tasche WHERE id = ?", (neu,)).fetchone()
    karten_b = _karten_in(con, b["id"])
    for kid in karten_a:
        schritte.aendern_genau(con, nutzer, "karte", kid, {"tasche_id": b["id"]}, schritt=schritt)
    for kid in karten_b:
        schritte.aendern_genau(con, nutzer, "karte", kid, {"tasche_id": tasche_id}, schritt=schritt)
    # Inhalt und eigener Foto-Ausschnitt gehören zur Karte, nicht zur Stelle
    if (a["inhalt"], a["ausschnitt"]) != (b["inhalt"], b["ausschnitt"]):
        schritte.aendern_genau(con, nutzer, "tasche", tasche_id, {"inhalt": b["inhalt"], "ausschnitt": b["ausschnitt"]},
                               schritt=schritt)
        schritte.aendern_genau(con, nutzer, "tasche", b["id"], {"inhalt": a["inhalt"], "ausschnitt": a["ausschnitt"]},
                               schritt=schritt)
    if schritte.summe(con, nutzer) != vorher:   # darf nie vorkommen; die Ausnahme rollt alles zurück
        raise Fehler("Summenprüfung fehlgeschlagen, nichts verschoben.", 500)
    return {"schritt": schritt, "von": tasche_id, "nach": b["id"], "getauscht": bool(karten_b),
            "binder_id": seite["binder_id"], "seite_id": seite["id"], "zeile": zeile, "spalte": spalte}


# ---------------------------------------------------------------- Karte hinzufügen


def _freie_stelle(con, seite_id: int, zeile: int, spalte: int):
    """Die Tasche an dieser Stelle (oder None); liegt dort schon eine Karte, ein Fehler."""
    t = _tasche_an(con, seite_id, zeile, spalte)
    if t is not None and _karten_in(con, t["id"]):
        raise Fehler("In dieser Tasche liegt schon eine Karte.", 409)
    return t


def _tasche_fuer_karte(con, nutzer: int, seite_id: int, zeile: int, spalte: int, t, schritt: str) -> int:
    """Stelle für eine neue Karte vorbereiten: Tasche anlegen oder ihren Inhalt auf „karte“ setzen."""
    if t is None:
        return schritte.einfuegen(con, nutzer, "tasche", {"seite_id": seite_id, "zeile": zeile, "spalte": spalte,
                                                          "inhalt": "karte", "ausschnitt": None}, schritt=schritt)
    # Der Foto-Ausschnitt zeigt eine Rückseite, eine leere Tasche oder eine Karte, die nicht mehr hier liegt, also nie
    # die neue Karte (Rückgängig stellt ihn wieder her). Nur eine unklare Tasche zeigt, was jetzt bestimmt wird.
    felder = {"inhalt": "karte", **({} if t["inhalt"] == "unsicher" else {"ausschnitt": None})}
    schritte.aendern_genau(con, nutzer, "tasche", t["id"], felder, schritt=schritt)
    return t["id"]


def karte_einlegen(con, nutzer: int, seite_id: int, zeile, spalte, daten: dict, foil: bool,
                   sprache: str | None) -> dict:
    seite = _seite_holen(con, nutzer, seite_id)
    zeile, spalte = _position(seite, zeile, spalte)
    t = _freie_stelle(con, seite_id, zeile, spalte)
    schritt = schritte.neuer_schritt()
    tasche_id = _tasche_fuer_karte(con, nutzer, seite_id, zeile, spalte, t, schritt)
    karte_id = schritte.einfuegen(con, nutzer, "karte", {
        "nutzer_id": nutzer, "tasche_id": tasche_id, "kiste_id": None, "quelle": "manuell",
        "scryfall_id": daten["scryfall_id"], "oracle_id": daten["oracle_id"], "name": daten["name"],
        "set_code": daten["set_code"], "nummer": daten["nummer"], "sprache": sprache or daten.get("sprache") or "en",
        "foil": int(foil), "anzahl": 1, "status": "sicher", "geprueft": 1, "bild": daten["bild"],
        "zustand": "near_mint",
    }, schritt=schritt)
    kartendaten.schreiben(con, daten, art="behalten")   # abgeleitet aus dem Katalog, ohne Historie
    return {"id": karte_id, "schritt": schritt, "tasche_id": tasche_id, "name": daten["name"],
            "binder_id": seite["binder_id"], "seite_id": seite_id, "zeile": zeile, "spalte": spalte}


# ---------------------------------------------------------------- Zurückstellen (Wunsch des Nutzers, 05.11.2026)


def zurueckstellen(con, nutzer: int, ids: list[int]) -> dict:
    """Karten in die Kiste „Zurückgestellt“, ein Schritt. Unsichere bleiben, wo sie sind: Sie gehen wie beim
    Einsortieren erst nach der Prüfung weiter."""
    erlaubt, uebersprungen = [], 0
    for kid in ids:
        k = con.execute("SELECT status, geprueft FROM karte WHERE id = ? AND nutzer_id = ?", (kid, nutzer)).fetchone()
        if k is not None and not sortierbar(k):
            uebersprungen += 1
        else:
            erlaubt.append(kid)   # fehlende Karten meldet `verschieben`
    if not erlaubt:
        raise Fehler("Unsichere Karten bitte zuerst in der Prüfansicht bestätigen.", 409)
    ziel = erkennung.zurueck_kiste(con, nutzer)
    return {**karten_verschieben(con, nutzer, erlaubt, ziel), "uebersprungen": uebersprungen, "kiste_id": ziel}


def zurueckgestellte(con, nutzer: int) -> dict:
    """Karten aus „Zurückgestellt“, die sich in eine Tasche legen lassen (nur sicher oder geprüft). `weitere`: so viele
    Posten passen nicht mehr in die Liste, die Oberfläche sagt das dazu."""
    kiste = erkennung.zurueck_kiste_id(con, nutzer)
    if kiste is None:
        return {"karten": [], "weitere": 0}
    von = "FROM karte WHERE nutzer_id = ? AND kiste_id = ? AND (status = 'sicher' OR geprueft = 1)"
    zeilen = con.execute(f"SELECT id, name, set_code, nummer, sprache, foil, anzahl {von}"
                         " ORDER BY name COLLATE NOCASE, id LIMIT ?", (nutzer, kiste, ZURUECK_LISTE_MAX)).fetchall()
    gesamt = con.execute(f"SELECT COUNT(*) {von}", (nutzer, kiste)).fetchone()[0]
    return {"karten": [{**dict(z), "foil": bool(z["foil"])} for z in zeilen], "weitere": gesamt - len(zeilen)}


def zurueckgestellte_einlegen(con, nutzer: int, seite_id: int, zeile, spalte, karte_id: int) -> dict:
    """Legt ein Exemplar aus „Zurückgestellt“ in eine Tasche ohne Karte, ein Schritt mit Rückgängig."""
    seite = _seite_holen(con, nutzer, seite_id)
    zeile, spalte = _position(seite, zeile, spalte)
    t = _freie_stelle(con, seite_id, zeile, spalte)
    k = con.execute("SELECT * FROM karte WHERE id = ? AND nutzer_id = ?", (karte_id, nutzer)).fetchone()
    if k is None or k["kiste_id"] is None or k["kiste_id"] != erkennung.zurueck_kiste_id(con, nutzer):
        raise Fehler("Diese Karte ist nicht mehr zurückgestellt.", 404)
    if not sortierbar(k):
        raise Fehler("Unsichere Karten bitte zuerst in der Prüfansicht bestätigen.", 409)
    vorher = schritte.summe(con, nutzer)[1]   # nur die Karten: Ein geteilter Stapel ergibt einen Posten mehr
    schritt = schritte.neuer_schritt()
    tasche_id = _tasche_fuer_karte(con, nutzer, seite_id, zeile, spalte, t, schritt)
    if k["anzahl"] == 1:
        schritte.aendern_genau(con, nutzer, "karte", karte_id, {"kiste_id": None, "tasche_id": tasche_id}, schritt=schritt)
        ergebnis = karte_id
    else:   # ein Exemplar je Tasche: Der Rest des Stapels bleibt zurückgestellt
        schritte.aendern_genau(con, nutzer, "karte", karte_id, {"anzahl": k["anzahl"] - 1}, schritt=schritt)
        felder = {s: k[s] for s in k.keys() if s not in NICHT_KOPIEREN}
        felder.update({"anzahl": 1, "kiste_id": None, "tasche_id": tasche_id, "extern_id": None})
        if "sortieren" in felder:
            felder["sortieren"] = None
        ergebnis = schritte.einfuegen(con, nutzer, "karte", felder, schritt=schritt)
    if schritte.summe(con, nutzer)[1] != vorher:   # darf nie vorkommen; die Ausnahme rollt alles zurück
        raise Fehler("Summenprüfung fehlgeschlagen, nichts verschoben.", 500)
    return {"id": ergebnis, "schritt": schritt, "tasche_id": tasche_id, "name": k["name"],
            "binder_id": seite["binder_id"], "seite_id": seite_id, "zeile": zeile, "spalte": spalte}


def tasche_leeren(con, nutzer: int, tasche_id: int) -> dict:
    """Rückseite oder unklare Tasche ohne Karte als leer markieren (Wunsch des Nutzers, 04.11.2026). Der Foto-
    Ausschnitt bleibt gespeichert; Rückgängig stellt den alten Inhalt wieder her."""
    t = con.execute("SELECT t.* FROM tasche t JOIN seite s ON s.id = t.seite_id JOIN binder b ON b.id = s.binder_id"
                    " WHERE t.id = ? AND b.nutzer_id = ?", (tasche_id, nutzer)).fetchone()
    if t is None:
        raise Fehler("Tasche nicht gefunden.", 404)
    if _karten_in(con, tasche_id):
        raise Fehler("In dieser Tasche liegt eine Karte. Bitte die Karte löschen oder verschieben.", 409)
    if t["inhalt"] == "leer":
        return {"schritt": None, "tasche_id": tasche_id, "vorher": "leer"}
    schritt = schritte.neuer_schritt()
    schritte.aendern_genau(con, nutzer, "tasche", tasche_id, {"inhalt": "leer"}, schritt=schritt)
    return {"schritt": schritt, "tasche_id": tasche_id, "vorher": t["inhalt"]}


# ---------------------------------------------------------------- Seite löschen (Wunsch des Nutzers, 07.11.2026)


def seite_loeschen(con, nutzer: int, seite_id: int, nummer: int) -> dict:
    """Löscht eine ganze Seite als einen Schritt mit Rückgängig (Antworten des Nutzers: Karten mitlöschen, an der Stelle
    bleibt eine Leerseite, das Originalfoto bleibt unverändert liegen).

    Die Taschen bleiben als Zeilen erhalten und werden „leer“ (ohne Ausschnitt); fehlende Stellen des Rasters kommen als
    leere Taschen dazu. So zeigt `herkunft_tasche_id` einer Deck-Karte weiter auf eine Tasche (Zerlegen legt sie in die
    leere Tasche zurück). Die Seite verliert ihr Foto: Ein noch nicht erkanntes Foto bekommt den Status „fehler“, damit
    der PC es überspringt; Rückgängig gibt es ihm zurück. Danach lässt sich über „Foto für diese Leerseite“ ein neues Foto
    an dieselbe Stelle legen. `nummer` muss die aktuelle Seitenzahl sein (Schutz gegen eine veraltete Ansicht)."""
    s = con.execute("SELECT s.*, b.raster FROM seite s JOIN binder b ON b.id = s.binder_id"
                    " WHERE s.id = ? AND b.nutzer_id = ?", (seite_id, nutzer)).fetchone()
    if s is None:
        raise Fehler("Seite nicht gefunden.", 404)
    if s["nummer"] != nummer:
        raise Fehler("Die Seitenzahlen haben sich inzwischen geändert. Bitte die Ansicht neu laden.", 409)
    taschen = con.execute("SELECT * FROM tasche WHERE seite_id = ? ORDER BY zeile, spalte", (seite_id,)).fetchall()
    karten = con.execute("SELECT k.id, k.anzahl FROM karte k JOIN tasche t ON t.id = k.tasche_id"
                         " WHERE t.seite_id = ? ORDER BY k.id", (seite_id,)).fetchall()
    if s["art"] == "leer" and s["foto_id"] is None and not karten:
        raise Fehler("Diese Seite ist schon eine Leerseite. Entfernen geht über „Seiten verwalten“.", 409)
    foto = None
    if s["foto_id"] is not None:
        foto = con.execute("SELECT id, status FROM foto WHERE id = ?", (s["foto_id"],)).fetchone()
        if foto is not None and foto["status"] == "in_verarbeitung":
            raise Fehler("Der PC erkennt diese Seite gerade. Bitte warten, bis die Erkennung fertig ist.", 409)
    posten_vorher, karten_vorher = schritte.summe(con, nutzer)
    schritt = schritte.neuer_schritt()
    for k in karten:
        schritte.loeschen(con, nutzer, "karte", k["id"], schritt=schritt)
    for t in taschen:
        schritte.aendern_genau(con, nutzer, "tasche", t["id"], {"inhalt": "leer", "ausschnitt": None}, schritt=schritt)
    vorhanden = {(t["zeile"], t["spalte"]) for t in taschen}
    zeilen, spalten = raster_masse(s["raster"])
    for z in range(1, zeilen + 1):
        for sp in range(1, spalten + 1):
            if (z, sp) not in vorhanden:
                schritte.einfuegen(con, nutzer, "tasche", {"seite_id": seite_id, "zeile": z, "spalte": sp,
                                                           "inhalt": "leer", "ausschnitt": None}, schritt=schritt)
    schritte.aendern_genau(con, nutzer, "seite", seite_id, {"art": "leer", "foto_id": None}, schritt=schritt)
    if foto is not None and foto["status"] == "neu":
        schritte.aendern_genau(con, nutzer, "foto", foto["id"], {"status": "fehler", "fehler": SEITE_GELOESCHT},
                               schritt=schritt)
    entfernt = sum(k["anzahl"] for k in karten)
    if schritte.summe(con, nutzer) != (posten_vorher - len(karten), karten_vorher - entfernt):
        raise Fehler("Summenprüfung fehlgeschlagen, nichts gelöscht.", 500)   # die Ausnahme rollt alles zurück
    return {"schritt": schritt, "seite_id": seite_id, "binder_id": s["binder_id"], "nummer": s["nummer"],
            "posten": len(karten), "karten": entfernt, "foto": s["foto_id"] is not None}


# ---------------------------------------------------------------- Routen


def routen(h) -> list[Route]:

    def _ausfuehren(arbeit):
        def mit_pruefung(con):
            _ab_version_3(con, 409)
            return arbeit(con)
        return run_in_threadpool(h.schreibvorgang, mit_pruefung)

    @h.schreibend
    async def karte_foil(request):
        karte_id = int(request.path_params["id"])
        d = await h.json_daten(request)
        if set(d) != {"foil"}:
            raise Fehler("Bitte nur „foil“ angeben.")
        foil = _foil(d["foil"])
        return JSONResponse(await _ausfuehren(lambda con: foil_umschalten(con, h.nutzer, karte_id, foil)))

    @h.schreibend
    async def ganze_seite_foil(request):
        seite_id = int(request.path_params["id"])
        d = await h.json_daten(request)
        if set(d) != {"foil"}:
            raise Fehler("Bitte nur „foil“ angeben.")
        foil = _foil(d["foil"])
        return JSONResponse(await _ausfuehren(lambda con: seite_foil(con, h.nutzer, seite_id, foil)))

    @h.schreibend
    async def verschieben(request):
        tasche_id = int(request.path_params["id"])
        d = await h.json_daten(request)
        if set(d) != {"ziel"} or not isinstance(d["ziel"], dict):
            raise Fehler("Bitte „ziel“ angeben.")
        return JSONResponse(await _ausfuehren(lambda con: tasche_verschieben(con, h.nutzer, tasche_id, d["ziel"])))

    @h.schreibend
    async def einlegen(request):
        seite_id = int(request.path_params["id"])
        d = await h.json_daten(request)
        if set(d) - {"zeile", "spalte", "scryfall_id", "foil", "sprache"}:
            raise Fehler("Unbekannte Angabe. Erlaubt: zeile, spalte, scryfall_id, foil, sprache.")
        scryfall_id = _kennung(d.get("scryfall_id"), "Scryfall-ID")
        foil = _foil(d.get("foil", False))
        sprache = d.get("sprache")
        if sprache is not None and (not isinstance(sprache, str) or sprache not in SPRACHEN):
            raise Fehler("Unbekannte Sprache. Möglich: " + ", ".join(SPRACHEN) + ".")
        daten = await run_in_threadpool(druck_nachschlagen, h, scryfall_id)
        antwort = await _ausfuehren(lambda con: karte_einlegen(con, h.nutzer, seite_id, d.get("zeile"),
                                                               d.get("spalte"), daten, foil, sprache))
        zeitplan.anstossen()   # Add-on: Preis des neuen Drucks bald nachtragen
        return JSONResponse(antwort, status_code=201)

    @h.schreibend
    async def leeren(request):
        tasche_id = int(request.path_params["id"])
        return JSONResponse(await _ausfuehren(lambda con: tasche_leeren(con, h.nutzer, tasche_id)))

    @h.schreibend
    async def zurueck_stellen(request):
        d = await h.json_daten(request)
        if set(d) - {"karten", "auswahl"} or ("karten" in d) == ("auswahl" in d):
            raise Fehler("Bitte entweder „karten“ (Liste von IDs) oder „auswahl“ angeben.")
        ids, a = auswahl_lesen(d, h.nutzer)
        return JSONResponse(await _ausfuehren(lambda con: zurueckstellen(con, h.nutzer, auswahl_ids(con, h.nutzer, ids, a))))

    def zurueck_liste(request):
        try:
            with closing(h.db()) as con:
                _ab_version_3(con, 404)
                return JSONResponse(zurueckgestellte(con, h.nutzer))
        except Fehler as f:
            return fehler(str(f), f.status)

    @h.schreibend
    async def zurueck_einlegen(request):
        seite_id = int(request.path_params["id"])
        d = await h.json_daten(request)
        if set(d) != {"zeile", "spalte", "karte_id"}:
            raise Fehler("Bitte „zeile“, „spalte“ und „karte_id“ angeben.")
        karte_id = _ganzzahl(d["karte_id"], "karte_id", 1, GROSS)
        antwort = await _ausfuehren(lambda con: zurueckgestellte_einlegen(con, h.nutzer, seite_id, d["zeile"],
                                                                          d["spalte"], karte_id))
        return JSONResponse(antwort, status_code=201)

    @h.schreibend
    async def ganze_seite_loeschen(request):
        seite_id = int(request.path_params["id"])
        d = await h.json_daten(request)
        if set(d) != {"nummer"}:
            raise Fehler("Bitte nur „nummer“ (die aktuelle Seitenzahl) angeben.")
        nummer = _ganzzahl(d["nummer"], "nummer", 1, GROSS)
        return JSONResponse(await _ausfuehren(lambda con: seite_loeschen(con, h.nutzer, seite_id, nummer)))

    return [
        Route("/seite/{id:int}/loeschen", ganze_seite_loeschen, methods=["POST"]),
        Route("/karten/zurueckstellen", zurueck_stellen, methods=["POST"]),
        Route("/zurueckgestellt", zurueck_liste),
        Route("/seite/{id:int}/zurueckgestellt", zurueck_einlegen, methods=["POST"]),
        Route("/tasche/{id:int}/leeren", leeren, methods=["POST"]),
        Route("/karte/{id:int}/foil", karte_foil, methods=["POST"]),
        Route("/seite/{id:int}/foil", ganze_seite_foil, methods=["POST"]),
        Route("/tasche/{id:int}/verschieben", verschieben, methods=["POST"]),
        Route("/seite/{id:int}/karte", einlegen, methods=["POST"]),
    ]
