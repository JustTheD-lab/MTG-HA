"""Schlanker Server für Bücherregal und Binder (läuft auch auf dem Raspberry Pi 3).

Liefert eine JSON-Schnittstelle aus der SQLite-Datenbank, die Oberfläche als statische Dateien
und die vom PC vorbereiteten Bilder. Keine Bildverarbeitung, keine fremden Server. Hochgeladene
Fotos werden nur unverändert abgelegt; erkannt werden sie vom Verarbeiter auf dem PC
(`python -m binderapp.verarbeiten`), der in dieselbe Datenbank schreibt.

Lesen (offen wie bisher):
  GET    /api/regal                     Binder im Regal mit Kennzahlen
  GET    /api/binder/{id}               Binder mit allen Seiten (Art, Fotostatus), Taschen und Karten
  GET    /api/binder/{id}/verlauf       Änderungen an Binder und Seiten (neueste zuerst)
  GET    /api/karte/{id}                eine Karte mit Lage und Änderungshistorie
  GET    /api/suche?q=TEXT              Karten über alle Binder (Name enthält TEXT)
  GET    /api/sitzung                   angemeldet? Schreiben eingerichtet? Größenlimit
  GET    /api/verarbeiter               Lebenszeichen des Verarbeiters und Länge der Warteschlange
  GET    /bilder/{art}/{schluessel}_{breite}.webp   art = foto | scryfall, breite = 200 | 400 | 672

Anmelden:
  POST   /api/anmelden                  {"passwort": "…"} → Sitzungs-Cookie
  POST   /api/abmelden

Schreiben (nur angemeldet):
  POST   /api/binder                    {"name", "raster": "2x2"|"3x3", "farbe"} → neuer Binder
  PATCH  /api/binder/{id}               {"name"?, "farbe"?}
  POST   /api/binder/{id}/leerseiten    {"nach": k|null, "anzahl": n} → Leerseiten nach Seite k (null = hinten)
  POST   /api/binder/{id}/fotos?nach=k|ende&name=DATEINAME[&trotzdem=1]   Inhalt = das Foto (JPEG/HEIC)
  POST   /api/seite/{id}/foto?name=DATEINAME[&trotzdem=1]                 Foto für eine Leerseite nachtragen
  DELETE /api/seite/{id}                Leerseite entfernen (Nummern rücken nach)
  POST   /api/foto/{id}/erneut          Foto mit Fehler erneut in die Warteschlange

Bulkkisten (kisten.py, ab Version 3): GET /api/kisten, /api/kisten/optionen, /api/kiste/{id},
/api/kiste/{id}/karten (Sortieren, Gruppieren, Filtern, Blättern per SQL); angemeldet POST /api/kisten,
PATCH und DELETE /api/kiste/{id}.
Prüfansicht (pruefen.py, ab Version 3): GET /api/pruefen/…; angemeldet bestätigen, Druck ändern,
Anzahl/Foil/Sprache, Keine Karte, entfernen, Rückgängig je Bedienschritt.

Fehler kommen als {"fehler": "verständlicher Satz"} mit passendem Status.
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route, Router
from starlette.staticfiles import StaticFiles

from binderapp.db import seiten
from binderapp.db.datenbank import MINDEST, aendern, einfuegen, schreiben, verbinden, version
from binderapp.db.seiten import Fehler
from binderapp.preise import werte
from binderapp.web import (abzug, bearbeiten, decks, hochladen, kisten, kistenschreiben, pc, pruefen, regeln, sammlung,
                           scan, sets)
from binderapp.web.anmeldung import COOKIE, DAUER, Anmeldung
from binderapp.web.helfer import Helfer
from binderapp.web.speicher import MELDUNG as MELDUNG_SPEICHER
from binderapp.web.speicher import Speicher

STATISCH = Path(__file__).with_name("static")
PROJEKT = Path(__file__).resolve().parents[3]
STANDARD_DATEN = PROJEKT / "data" / "app"
STANDARD_KONFIG = PROJEKT / "data" / "konfig.toml"
STANDARD_KATALOG = PROJEKT / "data" / "katalog.sqlite"  # nur am PC; fehlt er, sind Druckwahl und Namenssuche aus
NUTZER = 1  # bis zur Benutzerverwaltung gibt es nur einen Nutzer
LEBENSZEICHEN = 30  # Sekunden: so alt darf das letzte Lebenszeichen des Verarbeiters höchstens sein
FARBNAMEN = {"gruen": "grün"}
HA_SUPERVISOR = ("172.30.32.2",)  # Absender aller Ingress-Anfragen im Add-on
PC_PORT = int(os.environ.get("BINDER_PC_PORT", "8099"))  # PC-Schnittstelle des Add-ons (web/pc.py)

log = logging.getLogger("binderapp.web")

# Unter Windows liest mimetypes die Registry; feste Typen, damit Module und Schriften überall stimmen.
for typ, endung in (("text/javascript", ".js"), ("font/woff2", ".woff2"), ("image/webp", ".webp")):
    mimetypes.add_type(typ, endung)

# Kennzahlen je Binder: Seiten (mit Leerseiten), Karten (mit Anzahl), offene Fälle (unsicher und
# ungeprüft), Fotos in Arbeit und mit Fehler
SQL_REGAL = """
SELECT b.id, b.name, b.farbe, b.raster, b.regalplatz,
       (SELECT COUNT(*) FROM seite s WHERE s.binder_id = b.id) AS seiten,
       COALESCE(SUM(k.anzahl), 0) AS karten,
       COALESCE(SUM(k.status <> 'sicher' AND k.geprueft = 0), 0) AS offen,
       COALESCE(SUM(k.status <> 'sicher'), 0) AS unsicher_erkannt,
       (SELECT COUNT(*) FROM seite s JOIN foto f ON f.id = s.foto_id
         WHERE s.binder_id = b.id AND f.status IN ('neu', 'in_verarbeitung')) AS in_arbeit,
       (SELECT COUNT(*) FROM seite s JOIN foto f ON f.id = s.foto_id
         WHERE s.binder_id = b.id AND f.status = 'fehler') AS fehler
FROM binder b
LEFT JOIN seite s ON s.binder_id = b.id
LEFT JOIN tasche t ON t.seite_id = s.id
LEFT JOIN karte k ON k.tasche_id = t.id
WHERE b.nutzer_id = ?
GROUP BY b.id
ORDER BY b.regalplatz, b.id
"""

SQL_TASCHEN = """
SELECT s.id AS seite_id, s.nummer AS seite, s.art, f.id AS foto_id, f.datei AS foto, f.status AS foto_status,
       f.fehler AS foto_fehler, f.dateiname,
       t.id AS tasche_id, t.zeile, t.spalte, t.inhalt, t.ausschnitt,
       k.id AS karte_id, k.name, k.set_code, k.nummer, k.sprache, k.foil, k.anzahl,
       k.status, k.geprueft, k.bild
FROM seite s
LEFT JOIN foto f ON f.id = s.foto_id
LEFT JOIN tasche t ON t.seite_id = s.id
LEFT JOIN karte k ON k.tasche_id = t.id
WHERE s.binder_id = ?
ORDER BY s.nummer, t.zeile, t.spalte, k.id
"""

SQL_KARTE = """
SELECT k.*, t.zeile, t.spalte, t.inhalt, t.ausschnitt, s.nummer AS seite,
       b.id AS binder_id, b.name AS binder_name, b.raster
FROM karte k
LEFT JOIN tasche t ON t.id = k.tasche_id
LEFT JOIN seite s ON s.id = t.seite_id
LEFT JOIN binder b ON b.id = s.binder_id
WHERE k.id = ? AND k.nutzer_id = ?
"""

# Ab Version 3: dazu Kiste und Kartendaten (Manawert, Farbe, Typ, Seltenheit, Set-Name)
SQL_KARTE_V3 = """
SELECT k.*, t.zeile, t.spalte, t.inhalt, t.ausschnitt, s.nummer AS seite,
       b.id AS binder_id, b.name AS binder_name, b.raster, ki.name AS kiste_name,
       d.set_name, d.set_erschienen, d.manakosten, d.manawert, d.farben, d.farbgruppe, d.typzeile,
       d.haupttyp, d.seltenheit
FROM karte k
LEFT JOIN tasche t ON t.id = k.tasche_id
LEFT JOIN seite s ON s.id = t.seite_id
LEFT JOIN binder b ON b.id = s.binder_id
LEFT JOIN kiste ki ON ki.id = k.kiste_id
LEFT JOIN kartendaten d ON d.scryfall_id = k.scryfall_id
WHERE k.id = ? AND k.nutzer_id = ?
"""
# ab Version 8 zusätzlich der Ort „Deck“
SQL_KARTE_V8 = SQL_KARTE_V3.replace("ki.name AS kiste_name,", "ki.name AS kiste_name, de.name AS deck_name,").replace(
    "LEFT JOIN kartendaten d", "LEFT JOIN deck de ON de.id = k.deck_id\nLEFT JOIN kartendaten d")

SQL_SUCHE = """
SELECT k.id, k.name, k.set_code, k.nummer, k.sprache, k.status, k.geprueft, k.bild,
       t.ausschnitt, t.zeile, t.spalte, s.nummer AS seite, b.id AS binder_id, b.name AS binder_name
FROM karte k
JOIN tasche t ON t.id = k.tasche_id
JOIN seite s ON s.id = t.seite_id
JOIN binder b ON b.id = s.binder_id
WHERE k.nutzer_id = ? AND k.name LIKE ? ESCAPE '\\'
ORDER BY k.name, b.regalplatz, s.nummer
LIMIT 50
"""

# Verlauf eines Binders: der Binder selbst und alle seine Seiten, auch schon entfernte Leerseiten
SQL_VERLAUF = """
SELECT h.zeitpunkt, h.tabelle, h.aktion, h.quelle, h.alt, h.neu, s.nummer AS nummer_jetzt
FROM historie h
LEFT JOIN seite s ON h.tabelle = 'seite' AND s.id = h.datensatz
WHERE (h.tabelle = 'binder' AND h.datensatz = :b)
   OR (h.tabelle = 'seite' AND h.datensatz IN (
         SELECT id FROM seite WHERE binder_id = :b
         UNION SELECT datensatz FROM historie
               WHERE tabelle = 'seite' AND aktion = 'geloescht' AND json_extract(alt, '$.binder_id') = :b))
ORDER BY h.id DESC
LIMIT 200
"""

SQL_DOPPELT = """
SELECT f.id, s.nummer AS seite, b.id AS binder_id, b.name AS binder_name
FROM foto f
LEFT JOIN seite s ON s.foto_id = f.id
LEFT JOIN binder b ON b.id = s.binder_id
WHERE f.nutzer_id = ? AND f.sha256 = ?
  AND (s.id IS NOT NULL OR f.quelle <> 'upload')   -- Fotos eines gelöschten Binders dürfen neu hochgeladen werden
ORDER BY f.id
LIMIT 1
"""


def markierung(status: str | None) -> str | None:
    """Zeichen für die Oberfläche: unsichere Erkennung bleibt sichtbar, auch nach Prüfung."""
    return {"unsicher": "unsicher", "name_sicher": "druck_offen"}.get(status)


def _karte_kurz(z) -> dict:
    return {
        "id": z["karte_id"], "name": z["name"], "set": z["set_code"], "nummer": z["nummer"],
        "sprache": z["sprache"], "foil": bool(z["foil"]), "anzahl": z["anzahl"],
        "status": z["status"], "geprueft": bool(z["geprueft"]), "bild": z["bild"],
        "markierung": markierung(z["status"]),
    }


def farbname(farbe) -> str:
    return FARBNAMEN.get(farbe, farbe or "")


def verlauf_text(h) -> str:
    """Ein Satz je Historienzeile von Binder oder Seite."""
    alt = json.loads(h["alt"]) if h["alt"] else {}
    neu = json.loads(h["neu"]) if h["neu"] else {}
    importiert = h["quelle"] == "zuordnung"
    if h["tabelle"] == "binder":
        if h["aktion"] == "angelegt":
            woher = " aus dem Import" if importiert else ""
            return f"Binder „{neu.get('name')}“{woher} angelegt (Raster {neu.get('raster')}, {farbname(neu.get('farbe'))})"
        teile = []
        if "name" in neu:
            teile.append(f"Name „{alt.get('name')}“ → „{neu['name']}“")
        if "farbe" in neu:
            teile.append(f"Farbe {farbname(alt.get('farbe'))} → {farbname(neu['farbe'])}")
        return "Binder geändert: " + (", ".join(teile) or "ohne sichtbare Änderung")
    if h["aktion"] == "angelegt":
        if neu.get("art") == "leer":
            return f"Leerseite als Seite {neu.get('nummer')} eingefügt"
        return f"Seite {neu.get('nummer')} " + ("aus dem Import angelegt" if importiert else "mit Foto angelegt")
    if h["aktion"] == "geloescht":
        return (f"Leerseite {alt.get('nummer')} entfernt" if alt.get("art") == "leer"
                else f"Seite {alt.get('nummer')} entfernt")
    if "nummer" in neu:
        return f"Seite {alt.get('nummer')} wird Seite {neu['nummer']}"
    if neu.get("art") == "foto":
        return f"Leerseite {h['nummer_jetzt'] or ''} durch ein Foto ersetzt".replace("  ", " ")
    return "Seite geändert"


def dateiname_kurz(name: str) -> str | None:
    """Ursprünglicher Dateiname nur zur Anzeige: ohne Pfad, ohne Steuerzeichen, höchstens 120 Zeichen."""
    name = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(z for z in name if z.isprintable()).strip()
    return name[:120] or None


def fehler(text: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"fehler": text}, status_code=status)


def verarbeiter_status(con) -> dict:
    z = con.execute("SELECT zeitpunkt, info, (julianday('now') - julianday(zeitpunkt)) * 86400 AS alter_s"
                    " FROM dienst WHERE name = 'verarbeiter'").fetchone()
    warte = con.execute("SELECT COUNT(*) FROM foto WHERE nutzer_id = ? AND status IN ('neu', 'in_verarbeitung')",
                        (NUTZER,)).fetchone()[0]
    info = json.loads(z["info"]) if z and z["info"] else {}
    return {
        "aktiv": bool(z is not None and z["alter_s"] < LEBENSZEICHEN and info.get("zustand") != "beendet"),
        "zuletzt": z["zeitpunkt"] + "Z" if z else None,
        "zustand": info.get("zustand"),
        "warteschlange": warte,
    }


def erstellen(daten: Path = STANDARD_DATEN, fotos: Path | None = None, konfig: Path | None = None,
              katalog: Path | None = STANDARD_KATALOG, *, bilder: Path | None = None, ha: bool = False,
              ha_erlaubt: tuple[str, ...] = HA_SUPERVISOR, sicherungen: Path | None = None,
              sicherungen_behalten: int | None = None, speicher_pruefen: bool = False) -> Starlette:
    """Baut die App. `daten`: Ordner mit binder.sqlite (und bilder/, falls `bilder` fehlt); `fotos`: Ablage der
    Originale (Standard: Nachbarordner photos/, also data/photos); `konfig`: Passwort-Datei (Standard
    data/konfig.toml); `katalog`: Scryfall-Katalog für die Prüfansicht (None oder fehlende Datei: ohne Druckwahl).

    Add-on (`ha=True`): Anfragen nur vom Supervisor (`ha_erlaubt`, Ingress); Home Assistant hat angemeldet und
    zeigt die App nur Admins, darum Schreiben ohne eigenes Passwort. `bilder` und `fotos` liegen dann auf dem
    Netzlaufwerk (`speicher_pruefen`: erreichbar nur, wenn wirklich eingehängt), Sicherungen in `sicherungen`,
    davon bleiben die jüngsten `sicherungen_behalten`."""
    daten = Path(daten)
    datenbank = daten / "binder.sqlite"
    fotos = Path(fotos) if fotos else daten.parent / "photos"
    bilder = Path(bilder) if bilder else daten / "bilder"
    speicher = Speicher(bilder, fotos, daten, geraet_pruefen=speicher_pruefen)
    anmeldung = Anmeldung(Path(konfig) if konfig else STANDARD_KONFIG)
    # Version 2 läuft weiter (Binder, Upload, Leerseiten); Kisten und Kartendaten erst ab Version 3.
    if datenbank.exists():
        with closing(verbinden(datenbank, nur_lesen=True)) as con:
            if version(con) < MINDEST:
                raise RuntimeError(f"{datenbank} ist Version {version(con)}. "
                                   "Bitte zuerst: python -m binderapp.db.migrieren")
    if speicher.fotos_ok():
        hochladen.teile_aufraeumen(fotos / "upload")
        hochladen.teile_aufraeumen(fotos / "scan")
    if speicher.bilder_ok():
        hochladen.teile_aufraeumen(bilder / "scan")

    def db():
        if not datenbank.exists():
            raise FileNotFoundError(f"Datenbank fehlt: {datenbank}")
        return verbinden(datenbank, nur_lesen=True)

    def schreibvorgang(arbeit):
        """Führt `arbeit(con)` in einer kurzen Schreibtransaktion aus (im Thread, blockiert nichts)."""
        with closing(verbinden(datenbank)) as con, schreiben(con):
            return arbeit(con)

    # ---------------------------------------------------------------- Lesen

    def regal(request):
        with closing(db()) as con:
            zeilen = con.execute(SQL_REGAL, (NUTZER,)).fetchall()
            wert = werte.werte(con, NUTZER, "binder")   # Durchschnitt 7 Tage, ab Version 4
            preisstand = werte.stand(con)
        return JSONResponse({"binder": [{**dict(z), "wert": wert.get(z["id"], {}).get("wert")} for z in zeilen],
                             "preise": preisstand})

    def binder(request):
        binder_id = request.path_params["id"]
        with closing(db()) as con:
            b = con.execute("SELECT id, name, farbe, raster FROM binder WHERE id = ? AND nutzer_id = ?",
                            (binder_id, NUTZER)).fetchone()
            if b is None:
                return fehler("Binder nicht gefunden", 404)
            zeilen = con.execute(SQL_TASCHEN, (binder_id,)).fetchall()
            status = verarbeiter_status(con)
            preise = werte.preise_fuer_karten(con, [z["karte_id"] for z in zeilen if z["karte_id"] is not None])
            wert = werte.werte(con, NUTZER, "binder").get(int(binder_id))
        zeilen_n, spalten_n = seiten.raster_masse(b["raster"])
        seiten_je: dict[int, dict] = {}
        taschen: dict[int, dict] = {}
        for z in zeilen:
            seite = seiten_je.get(z["seite"])
            if seite is None:
                seite = seiten_je[z["seite"]] = {
                    "id": z["seite_id"], "nummer": z["seite"], "art": z["art"], "foto": z["foto"],
                    "foto_id": z["foto_id"], "status": z["foto_status"], "fehler": z["foto_fehler"],
                    "dateiname": z["dateiname"], "taschen": [],
                }
            if z["tasche_id"] is None:
                continue
            tasche = taschen.get(z["tasche_id"])
            if tasche is None:
                tasche = {"id": z["tasche_id"], "zeile": z["zeile"], "spalte": z["spalte"], "inhalt": z["inhalt"],
                          "ausschnitt": z["ausschnitt"], "karten": []}
                taschen[z["tasche_id"]] = tasche
                seite["taschen"].append(tasche)
            if z["karte_id"] is not None:
                tasche["karten"].append({**_karte_kurz(z), "preis": preise.get(z["karte_id"])})
        for tasche in taschen.values():  # meist genau eine Karte je Tasche
            tasche["karte"] = tasche["karten"][0] if tasche["karten"] else None
        return JSONResponse({**dict(b), "zeilen": zeilen_n, "spalten": spalten_n,
                             "seiten": list(seiten_je.values()), "verarbeiter": status, "wert": wert})

    def verlauf(request):
        binder_id = request.path_params["id"]
        with closing(db()) as con:
            if con.execute("SELECT 1 FROM binder WHERE id = ? AND nutzer_id = ?", (binder_id, NUTZER)).fetchone() is None:
                return fehler("Binder nicht gefunden", 404)
            zeilen = con.execute(SQL_VERLAUF, {"b": binder_id}).fetchall()
        return JSONResponse({"verlauf": [
            {"zeitpunkt": h["zeitpunkt"] + "Z", "quelle": h["quelle"], "text": verlauf_text(h)} for h in zeilen]})

    def karte(request):
        with closing(db()) as con:
            sql = SQL_KARTE_V8 if version(con) >= 8 else SQL_KARTE_V3 if version(con) >= 3 else SQL_KARTE
            k = con.execute(sql, (request.path_params["id"], NUTZER)).fetchone()
            if k is None:
                return fehler("Karte nicht gefunden", 404)
            # Ab Version 3 nur die Historie ab der jüngsten Anlage (eine neu vergebene ID erbt nichts)
            ab = ("AND id >= COALESCE((SELECT MAX(id) FROM historie WHERE tabelle = 'karte' AND datensatz = :k"
                  " AND aktion = 'angelegt' AND rueckgaengig_von IS NULL), 0)") if version(con) >= 3 else ""
            historie = con.execute(
                "SELECT zeitpunkt, aktion, quelle, alt, neu FROM historie"
                f" WHERE tabelle = 'karte' AND datensatz = :k {ab} ORDER BY id", {"k": k["id"]}).fetchall()
            preise = werte.verlauf_karte(con, k["id"])   # Low, Durchschnitt 7 Tage, Veränderungen (ab Version 4)
        antwort = dict(k)
        antwort["foil"] = bool(antwort["foil"])
        antwort["geprueft"] = bool(antwort["geprueft"])
        antwort["set"] = antwort.pop("set_code")
        antwort["markierung"] = markierung(k["status"])
        antwort["preise"] = preise
        antwort["historie"] = [
            {**dict(h), "alt": json.loads(h["alt"]) if h["alt"] else None,
             "neu": json.loads(h["neu"]) if h["neu"] else None}
            for h in historie
        ]
        return JSONResponse(antwort)

    def suche(request):
        text = request.query_params.get("q", "").strip()
        if len(text) < 2:
            return JSONResponse({"treffer": []})
        muster = "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with closing(db()) as con:
            zeilen = con.execute(SQL_SUCHE, (NUTZER, muster)).fetchall()
        treffer = [{**dict(z), "set": z["set_code"], "geprueft": bool(z["geprueft"]),
                    "markierung": markierung(z["status"])} for z in zeilen]
        for t in treffer:
            del t["set_code"]
        return JSONResponse({"treffer": treffer})

    def sitzung(request):
        return JSONResponse({
            "angemeldet": ha or anmeldung.gueltig(request.cookies.get(COOKIE)),
            "eingerichtet": ha or anmeldung.eingerichtet(),
            "ha": ha,  # im Add-on meldet Home Assistant an: kein Knopf „Anmelden“
            "max_foto_mb": anmeldung.max_foto_bytes() // (1024 * 1024),
        })

    def verarbeiter(request):
        with closing(db()) as con:
            return JSONResponse(verarbeiter_status(con))

    def speicherstand(request):
        return JSONResponse(speicher.stand())

    # Add-on: Verbindung zum PC (Schlüssel für die PC-Schnittstelle, web/pc.py)
    def pc_verbindung(request):
        return JSONResponse({"eingerichtet": pc.schluessel_eingerichtet(daten), "port": PC_PORT})

    # ---------------------------------------------------------------- Anmelden

    async def anmelden(request):
        if ha:
            return fehler("Im Home-Assistant-Add-on meldet Home Assistant an.", 409)
        try:
            passwort = (await request.json()).get("passwort", "")
        except (ValueError, AttributeError):
            return fehler("Ungültige Anfrage.")
        adresse = request.client.host if request.client else "?"
        ok, meldung = await run_in_threadpool(anmeldung.anmelden, passwort, adresse)
        if not ok:
            await asyncio.sleep(0.4)  # bremst Rateversuche zusätzlich
            return fehler(meldung, 429 if "Fehlversuche" in meldung else 403 if "eingerichtet" in meldung else 401)
        antwort = JSONResponse({"angemeldet": True})
        antwort.set_cookie(COOKIE, anmeldung.cookie_erzeugen(), max_age=DAUER, httponly=True, samesite="lax", path="/")
        return antwort

    async def abmelden(request):
        antwort = JSONResponse({"angemeldet": False})
        antwort.delete_cookie(COOKIE, path="/", httponly=True, samesite="lax")
        return antwort

    def schreibend(handler):
        """Nur mit Passwort-Konfiguration, gültigem Cookie und ohne fremde Herkunft (Origin).
        Im Add-on prüft die Wache vor der App die Herkunft (nur Supervisor); Home Assistant hat angemeldet."""
        async def huelle(request):
            if ha:
                try:
                    return await handler(request)
                except Fehler as f:
                    return fehler(str(f), f.status)
            if not anmeldung.eingerichtet():
                return fehler("Schreiben ist nicht eingerichtet: Bitte am PC zuerst ein Passwort festlegen "
                              "(python -m binderapp.web.passwort).", 403)
            herkunft = request.headers.get("origin")
            if herkunft and urlsplit(herkunft).netloc != request.headers.get("host"):
                return fehler("Anfrage von einer fremden Seite abgelehnt.", 403)
            if not anmeldung.gueltig(request.cookies.get(COOKIE)):
                return fehler("Bitte zuerst anmelden.", 401)
            try:
                return await handler(request)
            except Fehler as f:
                return fehler(str(f), f.status)
        return huelle

    async def json_daten(request) -> dict:
        try:
            daten_ = await request.json()
        except ValueError:
            raise Fehler("Ungültige Anfrage.")
        if not isinstance(daten_, dict):
            raise Fehler("Ungültige Anfrage.")
        return daten_

    # ---------------------------------------------------------------- Schreiben

    @schreibend
    async def pc_schluessel(request):
        schluessel = await run_in_threadpool(pc.schluessel_erzeugen, daten)
        return JSONResponse({"schluessel": schluessel, "port": PC_PORT}, status_code=201)

    @schreibend
    async def binder_neu(request):
        d = await json_daten(request)
        bid = await run_in_threadpool(schreibvorgang, lambda con: seiten.binder_anlegen(
            con, NUTZER, d.get("name"), d.get("raster"), d.get("farbe")))
        return JSONResponse({"id": bid}, status_code=201)

    @schreibend
    async def binder_aendern(request):
        d = await json_daten(request)
        geaendert = await run_in_threadpool(schreibvorgang, lambda con: seiten.binder_aendern(
            con, NUTZER, request.path_params["id"], name=d.get("name"), farbe=d.get("farbe")))
        return JSONResponse({"geaendert": geaendert})

    @schreibend
    async def binder_loeschen(request):
        """Binder mit Seiten und Karten löschen; die Oberfläche fragt zweimal nach (Name eintippen)."""
        d = await json_daten(request)
        bid = request.path_params["id"]

        def arbeit(con):
            b = seiten.binder_holen(con, NUTZER, bid)
            if d.get("name") != b["name"]:
                raise Fehler("Zum Löschen bitte den Namen des Binders genau eintippen.")
            return seiten.binder_loeschen(con, NUTZER, bid)
        return JSONResponse({"entfernt": await run_in_threadpool(schreibvorgang, arbeit)})

    @schreibend
    async def leerseiten(request):
        d = await json_daten(request)
        nach, anzahl = d.get("nach"), d.get("anzahl", 1)
        if (nach is not None and not isinstance(nach, int)) or not isinstance(anzahl, int):
            raise Fehler("Ungültige Angabe für Stelle oder Anzahl.")
        neu = await run_in_threadpool(schreibvorgang, lambda con: seiten.leerseiten_einfuegen(
            con, NUTZER, request.path_params["id"], nach, anzahl))
        return JSONResponse({"seiten": neu}, status_code=201)

    @schreibend
    async def seite_entfernen(request):
        weg = await run_in_threadpool(schreibvorgang, lambda con: seiten.leerseite_entfernen(
            con, NUTZER, request.path_params["id"]))
        return JSONResponse({"entfernt": weg})

    @schreibend
    async def erneut(request):
        def arbeit(con):
            f = con.execute("SELECT status FROM foto WHERE id = ? AND nutzer_id = ?",
                            (request.path_params["id"], NUTZER)).fetchone()
            if f is None:
                raise Fehler("Foto nicht gefunden.", 404)
            if f["status"] != "fehler":
                raise Fehler("Dieses Foto wartet schon auf die Erkennung oder ist erkannt.", 409)
            if con.execute("SELECT 1 FROM seite WHERE foto_id = ?", (request.path_params["id"],)).fetchone() is None:
                raise Fehler(seiten.BINDER_GELOESCHT, 409)
            aendern(con, NUTZER, "nutzer", "foto", request.path_params["id"], {"status": "neu", "fehler": None})
        await run_in_threadpool(schreibvorgang, arbeit)
        return JSONResponse({"status": "neu"})

    def foto_uebernehmen(empfang, binder_id, nach, seite_id, name, trotzdem):
        """Prüft auf Doppel, legt Foto und Seite an und benennt die Teildatei um (eine Transaktion)."""
        with closing(verbinden(datenbank)) as con:
            doppelt = con.execute(SQL_DOPPELT, (NUTZER, empfang.sha256)).fetchone()
            if doppelt is not None and not trotzdem:
                ort = (f"Binder „{doppelt['binder_name']}“, Seite {doppelt['seite']}" if doppelt["binder_name"]
                       else "ohne Seite")
                return JSONResponse({"fehler": f"Dieses Foto ist schon hochgeladen: {ort}.",
                                     "doppelt": {k: doppelt[k] for k in ("binder_id", "binder_name", "seite")}},
                                    status_code=409)
            # Eine Transaktion: Foto, Seite (mit Umnummerieren) und Umbenennen gelingen zusammen oder gar nicht.
            # Umbenannt wird vor dem COMMIT; scheitert das COMMIT, wird die Datei wieder entfernt.
            umbenannt = None
            con.execute("BEGIN IMMEDIATE")
            try:
                fid = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM foto").fetchone()[0]
                endung = "jpg" if empfang.format == "jpeg" else "heic"
                datei = f"{fid:05d}_{empfang.sha256[:12]}.{endung}"
                einfuegen(con, NUTZER, "nutzer", "foto", {
                    "id": fid, "nutzer_id": NUTZER, "datei": f"upload/{binder_id}/{datei}",
                    "sha256": empfang.sha256, "status": "neu", "quelle": "upload", "dateiname": name,
                    "groesse": empfang.groesse, "format": empfang.format,
                    "hochgeladen": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
                })
                if seite_id is None:
                    seite = seiten.fotoseite_anlegen(con, NUTZER, binder_id, nach, fid)
                else:
                    seite = seiten.foto_nachtragen(con, NUTZER, seite_id, fid)
                ziel = fotos / "upload" / str(binder_id) / datei
                os.replace(empfang.teil, ziel)
                umbenannt = ziel
                con.commit()
            except BaseException:
                con.rollback()
                if umbenannt is not None:
                    umbenannt.unlink(missing_ok=True)
                raise
            return JSONResponse({"foto_id": fid, "seite": {"id": seite["id"], "nummer": seite["nummer"]},
                                 "status": "neu", "format": empfang.format, "groesse": empfang.groesse},
                                status_code=201)

    async def foto_empfangen(request, binder_id: int, nach, seite_id=None):
        if not await run_in_threadpool(speicher.fotos_ok):
            return fehler(MELDUNG_SPEICHER, 503)
        trotzdem = request.query_params.get("trotzdem") == "1"
        name = dateiname_kurz(request.query_params.get("name", ""))
        ordner = fotos / "upload" / str(binder_id)
        try:
            empfang = await hochladen.empfangen(request, ordner, anmeldung.max_foto_bytes())
        except hochladen.UploadFehler as f:
            return fehler(str(f), f.status)
        except ClientDisconnect:
            log.info("Hochladen abgebrochen (Binder %s), Teildatei gelöscht", binder_id)
            return Response(status_code=400)
        try:
            return await run_in_threadpool(foto_uebernehmen, empfang, binder_id, nach, seite_id, name, trotzdem)
        finally:
            empfang.teil.unlink(missing_ok=True)

    def binder_pruefen(binder_id):
        with closing(db()) as con:
            seiten.binder_holen(con, NUTZER, binder_id)

    @schreibend
    async def foto_neu(request):
        binder_id = request.path_params["id"]
        wert = request.query_params.get("nach", "ende")
        if wert == "ende":
            nach = None
        elif wert.isascii() and wert.isdigit() and len(wert) <= 6:
            nach = int(wert)
        else:
            raise Fehler("Ungültige Stelle für die neue Seite.")
        await run_in_threadpool(binder_pruefen, binder_id)
        return await foto_empfangen(request, binder_id, nach)

    @schreibend
    async def foto_nachtragen(request):
        seite_id = request.path_params["id"]

        def binder_der_seite():
            with closing(db()) as con:
                z = con.execute("SELECT s.binder_id FROM seite s JOIN binder b ON b.id = s.binder_id"
                                " WHERE s.id = ? AND b.nutzer_id = ?", (seite_id, NUTZER)).fetchone()
            if z is None:
                raise Fehler("Seite nicht gefunden.", 404)
            return z["binder_id"]

        binder_id = await run_in_threadpool(binder_der_seite)
        return await foto_empfangen(request, binder_id, None, seite_id)

    # Routen-Module (Prüfansicht, Bulkkisten) bekommen Datenbank, Anmeldung und Katalog über den Helfer.
    h = Helfer(datenbank, NUTZER, schreibend=schreibend, katalog_pfad=katalog, bilder_ordner=bilder,
               sicherung_ordner=sicherungen, sicherungen_behalten=sicherungen_behalten, fotos_ordner=fotos,
               speicher=speicher, max_foto_bytes=anmeldung.max_foto_bytes())

    api = Router(routes=[
        Route("/regal", regal),
        Route("/binder", binder_neu, methods=["POST"]),
        Route("/binder/{id:int}", binder),
        Route("/binder/{id:int}", binder_aendern, methods=["PATCH"]),
        Route("/binder/{id:int}/loeschen", binder_loeschen, methods=["POST"]),
        Route("/binder/{id:int}/verlauf", verlauf),
        Route("/binder/{id:int}/leerseiten", leerseiten, methods=["POST"]),
        Route("/binder/{id:int}/fotos", foto_neu, methods=["POST"]),
        Route("/seite/{id:int}", seite_entfernen, methods=["DELETE"]),
        Route("/seite/{id:int}/foto", foto_nachtragen, methods=["POST"]),
        Route("/foto/{id:int}/erneut", erneut, methods=["POST"]),
        Route("/karte/{id:int}", karte),
        Route("/suche", suche),
        Route("/sitzung", sitzung),
        Route("/verarbeiter", verarbeiter),
        Route("/speicher", speicherstand),
        *([Route("/pc/verbindung", pc_verbindung), Route("/pc/schluessel", pc_schluessel, methods=["POST"])]
          if ha else []),
        Route("/anmelden", anmelden, methods=["POST"]),
        Route("/abmelden", abmelden, methods=["POST"]),
        *pruefen.routen(h),
        *kisten.routen(h),
        *kistenschreiben.routen(h),
        *bearbeiten.routen(h),
        *decks.routen(h),
        *sammlung.routen(h),
        *abzug.routen(h),
        *scan.routen(h),
        *regeln.routen(h),
        *sets.routen(h),
    ])
    async def zu_gross(request, ausnahme):
        """Riesige IDs in der Adresse (SQLite kennt nur 64 Bit) sind schlicht nicht vorhanden, kein Serverfehler."""
        return fehler("Nicht gefunden.", 404)

    app = Starlette(exception_handlers={OverflowError: zu_gross}, routes=[
        Mount("/api", app=GZipMiddleware(api, minimum_size=500)),
        Mount("/bilder", app=StaticFiles(directory=bilder, check_dir=False)),
        Mount("/", app=GZipMiddleware(StaticFiles(directory=STATISCH, html=True), minimum_size=500)),
    ])
    app = Zwischenspeicher(app)
    return Wache(app, ha_erlaubt) if ha else app


class Wache:
    """Add-on: lässt nur Anfragen vom Supervisor durch (Ingress). Home Assistant hat den Nutzer angemeldet und
    zeigt das Seitenmenü nur Admins; direkt über den Port des Add-ons kommt niemand an die Oberfläche."""

    def __init__(self, app, erlaubt: tuple[str, ...]):
        self.app = app
        self.erlaubt = frozenset(erlaubt)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and (scope.get("client") or ("",))[0] not in self.erlaubt:
            antwort = JSONResponse({"fehler": "Nur über Home Assistant erreichbar."}, status_code=403)
            return await antwort(scope, receive, send)
        return await self.app(scope, receive, send)


class Zwischenspeicher:
    """Setzt Cache-Control: Bilder ändern sich nie (eine Woche), alles andere mit Rückfrage (ETag)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        wert = b"public, max-age=604800" if scope["path"].startswith("/bilder/") else b"no-cache"

        async def senden(nachricht):
            if nachricht["type"] == "http.response.start":
                nachricht["headers"] = [*nachricht.get("headers", []), (b"cache-control", wert)]
            await send(nachricht)

        await self.app(scope, receive, senden)


def _aus_umgebung(name: str) -> Path | None:
    wert = os.environ.get(name)
    return Path(wert) if wert else None


def _katalog_aus_umgebung() -> Path | None:
    wert = os.environ.get("BINDER_KATALOG")
    if wert is None:
        return STANDARD_KATALOG
    return None if wert.strip().lower() in ("", "keiner", "ohne") else Path(wert)


def _ja(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "ja", "true")


def _behalten() -> int | None:
    wert = os.environ.get("BINDER_SICHERUNGEN_BEHALTEN", "").strip()
    return int(wert) if wert.isdigit() and int(wert) > 0 else None


# Add-on (addon/run.sh): BINDER_HA=1, BINDER_BILDER und BINDER_FOTOS auf dem Netzlaufwerk,
# BINDER_SPEICHER_PRUEFEN=1, BINDER_SICHERUNG=/data/sicherung, BINDER_SICHERUNGEN_BEHALTEN=10.
app = erstellen(Path(os.environ.get("BINDER_DATEN", STANDARD_DATEN)),
                fotos=_aus_umgebung("BINDER_FOTOS"), konfig=_aus_umgebung("BINDER_KONFIG"),
                katalog=_katalog_aus_umgebung(), bilder=_aus_umgebung("BINDER_BILDER"), ha=_ja("BINDER_HA"),
                sicherungen=_aus_umgebung("BINDER_SICHERUNG"), sicherungen_behalten=_behalten(),
                speicher_pruefen=_ja("BINDER_SPEICHER_PRUEFEN"))
