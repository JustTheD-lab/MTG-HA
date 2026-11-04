"""Binder und Seiten verwalten: Nummern vom System, lückenlos und aufsteigend; Leerseiten.

Alle Funktionen schreiben über `einfuegen`, `aendern` und `loeschen` (Historie) und committen
nicht selbst. Der Aufrufer fasst einen Vorgang in eine Transaktion (`datenbank.schreiben`),
damit ein Umnummerieren ganz oder gar nicht geschieht.

Umnummerieren: Die Nummer ist je Binder eindeutig (UNIQUE) und mindestens 1 (CHECK). SQLite
prüft die Eindeutigkeit je Zeile, deshalb wird Seite für Seite verschoben: beim Platzschaffen von
hinten nach vorn, beim Schließen einer Lücke von vorn nach hinten. So ist das Ziel jedes Schritts
frei, und jede geänderte Seite bekommt ihre eigene Zeile in der Historie (alt und neu).
"""
from __future__ import annotations

import re
import unicodedata

from binderapp.db.datenbank import aendern, einfuegen, loeschen

QUELLE = "nutzer"
RASTER = ("2x2", "3x3")
FARBEN = ("schwarz", "durchsichtig", "gelb", "blau", "gruen", "rot", "braun")
EIGENE_FARBE = re.compile(r"#[0-9a-f]{6}")   # aus dem Farbrad, klein geschrieben gespeichert
NAME_LAENGE = 60
LEERSEITEN_HOECHSTENS = 50


class Fehler(ValueError):
    """Verständliche Meldung für die Oberfläche; `status` ist der passende HTTP-Status."""

    def __init__(self, text: str, status: int = 400):
        super().__init__(text)
        self.status = status


def raster_masse(raster: str) -> tuple[int, int]:
    zeilen, spalten = (int(x) for x in raster.split("x"))
    return zeilen, spalten


def _name_pruefen(name) -> str:
    name = unicodedata.normalize("NFC", str(name or "")).strip()
    if not name:
        raise Fehler("Bitte einen Namen für den Binder eingeben.")
    if len(name) > NAME_LAENGE:
        raise Fehler(f"Der Name ist zu lang (höchstens {NAME_LAENGE} Zeichen).")
    if any(unicodedata.category(z).startswith("C") for z in name):
        raise Fehler("Der Name enthält unzulässige Zeichen.")
    return name


def _farbe_pruefen(farbe) -> str:
    """Eine der festen Farben oder eine eigene Farbe aus dem Farbrad als #rrggbb (Wunsch des Nutzers, 04.11.2026)."""
    farbe = str(farbe or "").strip().lower().replace("ü", "ue")
    if farbe not in FARBEN and not EIGENE_FARBE.fullmatch(farbe):
        raise Fehler("Unbekannte Farbe. Möglich: " + ", ".join(f.replace("ue", "ü") for f in FARBEN)
                     + " oder eine eigene Farbe wie #3a6f9c.")
    return farbe


def _name_frei(con, nutzer_id: int, name: str, ausser: int | None = None) -> None:
    zeile = con.execute("SELECT id FROM binder WHERE nutzer_id = ? AND name = ? AND id IS NOT ?",
                        (nutzer_id, name, ausser)).fetchone()
    if zeile is not None:
        raise Fehler(f"Einen Binder „{name}“ gibt es schon.", 409)


def binder_holen(con, nutzer_id: int, binder_id: int):
    b = con.execute("SELECT * FROM binder WHERE id = ? AND nutzer_id = ?", (binder_id, nutzer_id)).fetchone()
    if b is None:
        raise Fehler("Binder nicht gefunden.", 404)
    return b


def binder_anlegen(con, nutzer_id: int, name, raster, farbe) -> int:
    """Neuer Binder ganz rechts im Regal. Raster 2x2 oder 3x3."""
    name = _name_pruefen(name)
    if raster not in RASTER:
        raise Fehler("Raster bitte 2x2 oder 3x3 wählen.")
    farbe = _farbe_pruefen(farbe)
    _name_frei(con, nutzer_id, name)
    platz = con.execute("SELECT COALESCE(MAX(regalplatz), 0) + 1 FROM binder WHERE nutzer_id = ?",
                        (nutzer_id,)).fetchone()[0]
    return einfuegen(con, nutzer_id, QUELLE, "binder", {
        "nutzer_id": nutzer_id, "name": name, "farbe": farbe, "raster": raster, "regalplatz": platz,
    })


def binder_aendern(con, nutzer_id: int, binder_id: int, name=None, farbe=None) -> dict:
    """Name und Farbe ändern (Raster bleibt, weil Taschen daran hängen)."""
    binder_holen(con, nutzer_id, binder_id)
    felder = {}
    if name is not None:
        felder["name"] = _name_pruefen(name)
        _name_frei(con, nutzer_id, felder["name"], ausser=binder_id)
    if farbe is not None:
        felder["farbe"] = _farbe_pruefen(farbe)
    return aendern(con, nutzer_id, QUELLE, "binder", binder_id, felder) if felder else {}


def seitenzahl(con, binder_id: int) -> int:
    return con.execute("SELECT COUNT(*) FROM seite WHERE binder_id = ?", (binder_id,)).fetchone()[0]


def platz_schaffen(con, nutzer_id: int, binder_id: int, nach: int | None, anzahl: int,
                   quelle: str = QUELLE) -> int:
    """Macht Platz für `anzahl` neue Seiten nach Seite `nach` (None = hinten, 0 = vor Seite 1).

    Alle Seiten ab `nach + 1` rücken um `anzahl` nach hinten. Gibt die erste freie Nummer zurück.
    """
    n = seitenzahl(con, binder_id)
    if nach is None:
        nach = n
    if not 0 <= nach <= n:
        raise Fehler(f"Seite {nach} gibt es in diesem Binder nicht (er hat {n} Seiten).")
    if anzahl < 1:
        raise Fehler("Es muss mindestens eine Seite sein.")
    spaeter = con.execute("SELECT id, nummer FROM seite WHERE binder_id = ? AND nummer > ? ORDER BY nummer DESC",
                          (binder_id, nach)).fetchall()
    for s in spaeter:
        aendern(con, nutzer_id, quelle, "seite", s["id"], {"nummer": s["nummer"] + anzahl})
    return nach + 1


def _leere_taschen(con, nutzer_id: int, seite_id: int, raster: str, quelle: str) -> None:
    zeilen, spalten = raster_masse(raster)
    for z in range(1, zeilen + 1):
        for s in range(1, spalten + 1):
            einfuegen(con, nutzer_id, quelle, "tasche",
                      {"seite_id": seite_id, "zeile": z, "spalte": s, "inhalt": "leer"})


def leerseiten_einfuegen(con, nutzer_id: int, binder_id: int, nach: int | None, anzahl: int = 1) -> list[dict]:
    """Fügt `anzahl` Leerseiten nach Seite `nach` ein (None = hinten). Taschen nach dem Raster, alle leer."""
    b = binder_holen(con, nutzer_id, binder_id)
    if not 1 <= anzahl <= LEERSEITEN_HOECHSTENS:
        raise Fehler(f"Bitte 1 bis {LEERSEITEN_HOECHSTENS} Leerseiten auf einmal.")
    erste = platz_schaffen(con, nutzer_id, binder_id, nach, anzahl)
    neu = []
    for i in range(anzahl):
        sid = einfuegen(con, nutzer_id, QUELLE, "seite",
                        {"binder_id": binder_id, "nummer": erste + i, "art": "leer"})
        _leere_taschen(con, nutzer_id, sid, b["raster"], QUELLE)
        neu.append({"id": sid, "nummer": erste + i})
    return neu


def fotoseite_anlegen(con, nutzer_id: int, binder_id: int, nach: int | None, foto_id: int,
                      quelle: str = QUELLE) -> dict:
    """Neue Seite mit Foto nach Seite `nach` (None = hinten). Taschen schreibt später der Verarbeiter."""
    binder_holen(con, nutzer_id, binder_id)
    nummer = platz_schaffen(con, nutzer_id, binder_id, nach, 1, quelle)
    sid = einfuegen(con, nutzer_id, quelle, "seite",
                    {"binder_id": binder_id, "nummer": nummer, "foto_id": foto_id, "art": "foto"})
    return {"id": sid, "nummer": nummer}


def _leerseite(con, nutzer_id: int, seite_id: int):
    s = con.execute("SELECT s.*, b.raster FROM seite s JOIN binder b ON b.id = s.binder_id"
                    " WHERE s.id = ? AND b.nutzer_id = ?", (seite_id, nutzer_id)).fetchone()
    if s is None:
        raise Fehler("Seite nicht gefunden.", 404)
    karten = con.execute("SELECT COUNT(*) FROM karte k JOIN tasche t ON t.id = k.tasche_id WHERE t.seite_id = ?",
                         (seite_id,)).fetchone()[0]
    if s["art"] != "leer" or s["foto_id"] is not None or karten:
        raise Fehler("Nur Leerseiten ohne Foto und ohne Karten lassen sich entfernen oder ersetzen.", 409)
    return s


def _taschen_loeschen(con, nutzer_id: int, seite_id: int) -> None:
    for t in con.execute("SELECT id FROM tasche WHERE seite_id = ? ORDER BY zeile, spalte", (seite_id,)).fetchall():
        loeschen(con, nutzer_id, QUELLE, "tasche", t["id"])


def leerseite_entfernen(con, nutzer_id: int, seite_id: int) -> dict:
    """Entfernt eine Leerseite und schließt die Lücke (spätere Seiten rücken um eins nach vorn)."""
    s = _leerseite(con, nutzer_id, seite_id)
    _taschen_loeschen(con, nutzer_id, seite_id)
    loeschen(con, nutzer_id, QUELLE, "seite", seite_id)
    spaeter = con.execute("SELECT id, nummer FROM seite WHERE binder_id = ? AND nummer > ? ORDER BY nummer",
                          (s["binder_id"], s["nummer"])).fetchall()
    for x in spaeter:
        aendern(con, nutzer_id, QUELLE, "seite", x["id"], {"nummer": x["nummer"] - 1})
    return {"binder_id": s["binder_id"], "nummer": s["nummer"]}


def foto_nachtragen(con, nutzer_id: int, seite_id: int, foto_id: int) -> dict:
    """Ersetzt eine Leerseite durch ein Foto; die Seitennummer bleibt."""
    s = _leerseite(con, nutzer_id, seite_id)
    _taschen_loeschen(con, nutzer_id, seite_id)
    aendern(con, nutzer_id, QUELLE, "seite", seite_id, {"art": "foto", "foto_id": foto_id})
    return {"id": seite_id, "nummer": s["nummer"], "binder_id": s["binder_id"]}


BINDER_GELOESCHT = "Der Binder dieser Seite wurde gelöscht."


def binder_loeschen(con, nutzer_id: int, binder_id: int) -> dict:
    """Löscht einen Binder mit Seiten, Taschen und den Karten darin, alles mit Historie (Wunsch des Nutzers,
    01.11.2026: falsch sortiert hochgeladen, neu hochladen). Die Originalfotos bleiben unverändert liegen; ihre
    Einträge bleiben ebenfalls, noch nicht erkannte bekommen den Status „fehler“, damit die Erkennung sie überspringt."""
    b = binder_holen(con, nutzer_id, binder_id)
    if con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sortierregel'").fetchone() and \
            con.execute("SELECT 1 FROM sortierregel WHERE binder_id = ? LIMIT 1", (binder_id,)).fetchone():
        raise Fehler("Dieser Binder ist Ziel einer Sortierregel. Erst die Regel ändern oder löschen.", 409)
    seiten = con.execute("SELECT id, foto_id FROM seite WHERE binder_id = ? ORDER BY nummer DESC", (binder_id,)).fetchall()
    zahl = {"seiten": len(seiten), "karten": 0, "posten": 0, "fotos": 0}
    for s in seiten:
        for k in con.execute("SELECT k.id, k.anzahl FROM karte k JOIN tasche t ON t.id = k.tasche_id"
                             " WHERE t.seite_id = ? ORDER BY k.id", (s["id"],)).fetchall():
            loeschen(con, nutzer_id, QUELLE, "karte", k["id"])
            zahl["posten"] += 1
            zahl["karten"] += k["anzahl"]
        _taschen_loeschen(con, nutzer_id, s["id"])
        loeschen(con, nutzer_id, QUELLE, "seite", s["id"])
        if s["foto_id"] is not None:
            zahl["fotos"] += 1
            f = con.execute("SELECT status FROM foto WHERE id = ?", (s["foto_id"],)).fetchone()
            if f is not None and f["status"] in ("neu", "in_verarbeitung"):
                aendern(con, nutzer_id, QUELLE, "foto", s["foto_id"], {"status": "fehler", "fehler": BINDER_GELOESCHT})
    loeschen(con, nutzer_id, QUELLE, "binder", binder_id)
    return {"id": binder_id, "name": b["name"], **zahl}


def nummern_lueckenlos(con, binder_id: int) -> bool:
    nummern = [z[0] for z in con.execute("SELECT nummer FROM seite WHERE binder_id = ? ORDER BY nummer", (binder_id,))]
    return nummern == list(range(1, len(nummern) + 1))
