"""Proxy-Druck im Add-on: Liste prüfen, Bilder laden, PDF bauen, Rückseiten und fertige PDFs verwalten.

    <wurzel>/                 lokal (/data/proxy): Stände (staende.py), Rückseiten, Arbeitsordner der Aufträge
    <wurzel>/rueckseiten/     hochgeladene Rückseiten (<id>.png|jpg und liste.json), in den HA-Sicherungen
    <wurzel>/eigene/          eigene Kartenbilder (proxy/eigene.py), in den HA-Sicherungen
    <wurzel>/arbeit/<id>/     Bilder eines Auftrags, nach dem Auftrag gelöscht (nicht in den Sicherungen)
    <ablage>/bilder/          Bilder-Cache (Netzlaufwerk), höchstens 2 GB, nach 30 Tagen neu geladen
    <ablage>/pdf/             die letzten 50 PDFs mit <name>.json daneben
    <wurzel>/kalibrierung.json Druckversatz der Rückseiten (x, y, Winkel), in den HA-Sicherungen
    <wurzel>/kalibrierung/    der zuletzt gebaute Kalibrierbogen

Entscheidungen des Nutzers (07.11.2026): Cache auf dem Netzlaufwerk (ohne Netzlaufwerk nur je Auftrag), doppelseitige
Karten immer mit der zweiten Seite hinten (wie im Original), 50 PDFs aufheben. Nichts fällt stillschweigend weg:
Karten, die nicht geladen werden konnten, zeigt der Auftrag, und erst der Nutzer entscheidet, ob das PDF ohne sie
entsteht. Nur Standardbibliothek; Bilder liest und schreibt nur der Läufer im venv des Stands.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import struct
import threading
import time
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from binderapp.db.datenbank import verbinden
from binderapp.proxy import druckwahl, eigene, liste
from binderapp.proxy import staende as st
from binderapp.proxy.laeufer import eigen_ablegen, schluessel

log = logging.getLogger("binderapp.proxy")

TEXT_MAX = 200 * 1024
ZEILEN_MAX = 1000
# Exemplare je Auftrag (25 Bögen). SCM hält alle Seiten im Speicher: 100 Karten brauchen rund 1,1 GB (gemessen
# 07.11.2026), 200 also gut 2 GB. Die Grenze prüft der Läufer, bevor er etwas lädt.
KARTEN_MAX = 200
ENTSCHEIDUNG_ZEIT = 30 * 60      # so lange wartet ein Auftrag mit fehlenden Karten auf „weiter“ oder „abbrechen“
NAME_MAX = 60
PDF_BEHALTEN = 50
CACHE_MAX = 2 * 1024 ** 3
CACHE_TAGE = 30
# Wie beim Foto-Upload: Die Rückseite des Nutzers hat 2944 × 4036 px und 17,9 MB (gemessen 07.11.2026); SCM
# skaliert sie beim PDF selbst auf Kartengröße, das Original bleibt unverändert.
RUECKSEITE_MAX = 40 * 1024 * 1024
RUECKSEITEN_MAX = 20
ERKENNEN_ZEIT = 120
LADEN_ZEIT = 60 * 60
PDF_ZEIT = 30 * 60
EINPASSEN_ZEIT = 2 * 60
BEISPIEL = Path(__file__).with_name("rueckseite-beispiel.png")
BEISPIEL_ID = "beispiel"
FORMATNAMEN = {"moxfield": "Moxfield/Archidekt mit Set und Nummer", "archidekt": "Archidekt", "mtga": "MTG Arena",
               "mtgo": "MTGO (Anzahl und Name)", "deckstats": "Deckstats", "simple": "Nur Namen"}
ID_MUSTER = re.compile(r"^[0-9a-f]{12}$")
# Kalibrierung (Druckversatz der Rückseiten in Pixeln bei 300 ppi wie in SCM, 1 px ≈ 0,085 mm). Entscheidung des
# Nutzers 07.11.2026: eine Kalibrierung, in jedem PDF eingerechnet, je Auftrag abschaltbar.
VERSATZ_MAX = 300          # rund 25 mm
WINKEL_MAX = 5.0
ABLESEN_MAX = 50           # ein Feld des Bogens liegt bei ±9 (waagerecht) und ±14 (senkrecht)
KALIBRIERUNG_ZEIT = 3 * 60
KALIBRIERUNG_LEER = {"x": 0, "y": 0, "winkel": 0.0}
# Druck bevorzugen (Wunsch des Nutzers 07.11.2026): wie in der Liste, günstigster (proxy/druckwahl.py) oder erster
# Druck (prefer_older_sets von SCM)
DRUCKWAHL = ("liste", "guenstig", "erster")
PDF_MUSTER = re.compile(r"^[0-9]{8}-[0-9]{6}-[A-Za-z0-9_\-]{1,60}\.pdf$")


class Proxyfehler(ValueError):
    def __init__(self, text: str, status: int = 400):
        super().__init__(text)
        self.status = status


def text_pruefen(text) -> str:
    if not isinstance(text, str) or not text.strip():
        raise Proxyfehler("Bitte eine Deckliste einfügen.")
    if len(text.encode("utf-8")) > TEXT_MAX:
        raise Proxyfehler("Die Liste ist zu lang (höchstens 200 KB).")
    zeilen = text.replace("\r\n", "\n").replace("\r", "\n")
    if zeilen.count("\n") > ZEILEN_MAX:
        raise Proxyfehler(f"Die Liste hat zu viele Zeilen (höchstens {ZEILEN_MAX}).")
    return zeilen


def deckname(name) -> str:
    """Anzeigename und Teil des Dateinamens: nur Buchstaben, Ziffern, Bindestrich."""
    text = str(name or "").strip()[:NAME_MAX]
    ersetzt = text.translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
                                             "ß": "ss"}))
    sicher = re.sub(r"[^A-Za-z0-9]+", "-", ersetzt).strip("-")[:NAME_MAX]
    return sicher or "Deck"


def grund(fehler) -> str:
    """Verständlicher Grund zu einer Fehlermeldung aus dem Original (englisch, technisch)."""
    text = str(fehler or "")
    if re.search(r"\b(400|404)\b", text):
        return "Bei Scryfall nicht gefunden (Name, Set oder Nummer prüfen)."
    if re.search(r"\b429\b", text):
        return "Scryfall hat zu viele Anfragen gemeldet. Bitte später noch einmal."
    if re.search(r"(?i)timeout|timed out|connection|name resolution", text):
        return "Keine Verbindung zu Scryfall."
    return "Laden gescheitert."


# --- Katalog --------------------------------------------------------------------------------------------------

def _norm(text: str) -> str:
    from binderapp.katalog.suche import normalisiere
    return normalisiere(text)


def katalog_pruefen(kat, karten: list[dict], sets_ignorieren: bool) -> list[dict]:
    """Je Zeile: `ok`, `druck_fehlt` (Name bekannt, Set/Nummer nicht) oder `unbekannt` (Name nicht im Katalog;
    Scryfall versucht es beim Laden trotzdem, z. B. über Flavor-Namen). Ohne Katalog: `ungeprueft`."""
    ergebnis = []
    for k in karten:
        z = dict(k)
        if kat is None:
            z["status"] = "ungeprueft"
        else:
            name = k["name"].split(" // ")[0] if " // " in k["name"] else k["name"]
            bekannt = kat.execute("SELECT 1 FROM namen WHERE norm = ? LIMIT 1", (_norm(name),)).fetchone()
            if not bekannt:
                z["status"] = "unbekannt"
            elif k["set"] and k["nummer"] and not sets_ignorieren:
                druck = kat.execute("SELECT 1 FROM druck WHERE set_code = ? AND nummer = ? LIMIT 1",
                                    (k["set"].lower(), k["nummer"])).fetchone()
                z["status"] = "ok" if druck else "druck_fehlt"
            else:
                z["status"] = "ok"
        ergebnis.append(z)
    return ergebnis


def format_waehlen(formate: dict, kat) -> str | None:
    """Das Format, dessen Zeilen am meisten bekannte Karten ergeben; bei Gleichstand weniger übersprungene Zeilen,
    dann die Reihenfolge des Läufers (mit Set und Nummer vor nur Namen)."""
    beste, wert = None, None
    for rang, (name, f) in enumerate(formate.items()):
        karten = f.get("karten") or []
        if not karten:
            continue
        if kat is not None:
            bekannt = sum(1 for z in katalog_pruefen(kat, karten, True) if z["status"] == "ok")
        else:
            bekannt = sum(1 for k in karten if not re.match(r"^\d+x?\s", k["name"]))
        mit_druck = sum(1 for k in karten if k["set"] and k["nummer"])
        w = (bekannt, -len(f.get("uebersprungen") or []), mit_druck, -rang)
        if wert is None or w > wert:
            beste, wert = name, w
    return beste


# --- Decks der App als Quelle ---------------------------------------------------------------------------------

# Listen, die die App selbst schreibt, im Deckstats-Format (proxy/liste.py)
LISTENFORMAT = liste.FORMAT
liste_zeile = liste.zeile


# Gruppen einer Deckzeile (Entscheidung des Nutzers, 07.11.2026: vorgewählt fehlend und als Proxy markiert)
DECK_GRUPPEN = ("fehlt", "proxy", "vorhanden", "basics")
DECK_STANDARD = ("fehlt", "proxy")


def deck_liste(abgleich: dict, gruppen) -> dict:
    """Proxy-Liste aus dem Abgleich eines Decks (`decks.abgleich.abgleichen`), im Stil von MTG Arena: „N Name (SET) Nr“
    mit dem Druck aus der Deckliste (Entscheidung des Nutzers), ohne Druckangabe nur „N Name“ (dann wählt Scryfall).

    Je Zeile zählen: `fehlt` (nicht in der Sammlung, nicht als Proxy markiert), `proxy` (als Proxy markiert und nicht
    vorhanden), `vorhanden` (in der Sammlung), `basics` (Basic Lands, die das Deck auslässt)."""
    gruppen = set(gruppen) & set(DECK_GRUPPEN)
    zahlen = dict.fromkeys(DECK_GRUPPEN, 0)
    zeilen = []
    for z in abgleich["karten"]:
        if z.get("ausgelassen"):
            je = {"basics": z["anzahl"]}
        else:
            proxy = z["anzahl"] - z["vorhanden"] if z.get("proxy") else 0
            je = {"fehlt": z["fehlt"], "proxy": max(proxy, 0), "vorhanden": z["vorhanden"]}
        for g, n in je.items():
            zahlen[g] += n
        anzahl = sum(n for g, n in je.items() if g in gruppen)
        if anzahl <= 0:
            continue
        if z.get("druck_angegeben") and z.get("set_code") and z.get("nummer"):
            zeilen.append(liste_zeile(anzahl, z["name"], z["set_code"], z["nummer"]))
        else:
            zeilen.append(liste_zeile(anzahl, z["name"]))
    return {"name": abgleich.get("name") or "Deck", "text": "\n".join(zeilen), "format": LISTENFORMAT,
            "gruppen": sorted(gruppen, key=DECK_GRUPPEN.index), "zahlen": zahlen,
            "exemplare": sum(int(zeile.split(" ", 1)[0]) for zeile in zeilen)}


def archidekt_liste(kopf: dict, zeilen: list[dict], basics: bool) -> dict:
    """Proxy-Liste aus einem Archidekt-Deck (`decks.lesen.archidekt_zeilen`, schon ohne Sideboard und Maybeboard):
    Commander zuerst, Druck aus Archidekt, Basic Lands nur mit `basics` (Antworten des Nutzers, 07.11.2026)."""
    reihe = sorted(zeilen, key=lambda z: not z.get("commander"))
    text, exemplare = [], 0
    for z in reihe:
        if z.get("basic") and not basics:
            continue
        text.append(liste_zeile(z["anzahl"], z["name"], z.get("set_code"), z.get("nummer")))
        exemplare += z["anzahl"]
    return {"name": kopf.get("name") or "Archidekt-Deck", "text": "\n".join(text), "format": LISTENFORMAT,
            "exemplare": exemplare, "basics": sum(z["anzahl"] for z in zeilen if z.get("basic"))}


# --- Bilder in Dateiköpfen (ohne Bildverarbeitung) ------------------------------------------------------------

def bildmasse(daten: bytes) -> tuple[str, int, int] | None:
    """Art und Größe eines PNG oder JPEG aus dem Dateikopf, sonst None."""
    if daten[:8] == b"\x89PNG\r\n\x1a\n" and daten[12:16] == b"IHDR":
        b, h = struct.unpack(">II", daten[16:24])
        return "png", b, h
    if daten[:3] == b"\xff\xd8\xff":
        i = 2
        while i + 9 < len(daten):
            if daten[i] != 0xFF:
                return None
            marke = daten[i + 1]
            if marke in (0xD8, 0x01) or 0xD0 <= marke <= 0xD7:
                i += 2
                continue
            laenge = struct.unpack(">H", daten[i + 2:i + 4])[0]
            if marke in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, b = struct.unpack(">HH", daten[i + 5:i + 9])
                return "jpg", b, h
            i += 2 + laenge
    return None


# --- Ablage: Cache und PDFs -----------------------------------------------------------------------------------

def cache_aufraeumen(ordner: Path, grenze: int = CACHE_MAX) -> dict:
    """Älteste Bilder zuerst entfernen, bis der Cache unter `grenze` liegt; Reste abgebrochener Downloads weg."""
    if not ordner.is_dir():
        return {"bytes": 0, "entfernt": 0}
    dateien, entfernt = [], 0
    for p in ordner.iterdir():
        try:
            info = p.stat()
        except OSError:
            continue
        if p.name.endswith(".teil"):
            if time.time() - info.st_mtime > 3600:
                p.unlink(missing_ok=True)
            continue
        if p.suffix == ".png":
            dateien.append((info.st_mtime, info.st_size, p))
    summe = sum(g for _, g, _ in dateien)
    for _, groesse, p in sorted(dateien):
        if summe <= grenze:
            break
        p.unlink(missing_ok=True)
        summe -= groesse
        entfernt += 1
    return {"bytes": summe, "entfernt": entfernt}


def pdfs_aufraeumen(ordner: Path, behalten: int = PDF_BEHALTEN) -> None:
    alle = sorted((p for p in ordner.glob("*.pdf") if PDF_MUSTER.match(p.name)), key=lambda p: p.name, reverse=True)
    for p in alle[behalten:]:
        p.unlink(missing_ok=True)
        p.with_suffix(".json").unlink(missing_ok=True)


# --- Aufträge -------------------------------------------------------------------------------------------------

@dataclass
class Auftrag:
    id: str
    name: str
    text: str
    format: str
    optionen: dict
    rueckseite: str
    versatz: dict | None = None        # Kalibrierung beim Anlegen (None: abgeschaltet oder nicht kalibriert)
    gewaehlt: list | None = None       # günstigster Druck: was je Zeile gewählt wurde (druckwahl.waehlen)
    eigene: dict = field(default_factory=dict)    # eigene Bilder je Schlüssel (Bibliothek.zuordnung)
    zusatz: list = field(default_factory=list)    # Zusatzkarten [(Läufer-Eintrag, Anzahl)]
    eigen_genutzt: int = 0                        # Zeilen der Liste mit eigenem Bild
    _katalog: Path | None = None
    _preisdatei: Path | None = None
    zustand: str = "wartet"          # wartet → laedt → (fehlende) → baut → fertig | abgebrochen | fehler
    fertig: int = 0
    gesamt: int = 0
    fehlende: list = field(default_factory=list)
    meldung: str | None = None
    pdf: str | None = None
    stand: str | None = None
    zeiten: dict = field(default_factory=dict)
    cache: dict = field(default_factory=dict)
    _weiter: threading.Event = field(default_factory=threading.Event, repr=False)
    _abbruch: bool = False

    def anzeige(self) -> dict:
        return {"id": self.id, "name": self.name, "format": self.format, "zustand": self.zustand,
                "fertig": self.fertig, "gesamt": self.gesamt, "fehlende": self.fehlende, "meldung": self.meldung,
                "pdf": self.pdf, "stand": self.stand, "zeiten": self.zeiten, "cache": self.cache, "versatz": self.versatz,
                "druckwahl": self.optionen.get("druckwahl", "liste"), "gewaehlt": self.gewaehlt,
                "eigene": self.eigen_genutzt, "zusatz": sum(n for _, n in self.zusatz)}


class Dienst:
    """Alles zum Proxy-Druck im Add-on; ein Auftrag zur Zeit."""

    def __init__(self, wurzel: Path, ablage: Path, staende: st.Staende | None = None, ablage_ok=lambda: True,
                 laeufer=st.laeufer):
        # absolut: der Läufer arbeitet in einem anderen Ordner (relative Pfade gälten dort)
        self.wurzel, self.ablage = Path(wurzel).resolve(), Path(ablage).absolute()
        self.staende = staende or st.Staende(self.wurzel)
        self.abgleich = st.Proxyabgleich(self.staende)
        self.ablage_ok = ablage_ok
        self._laeufer = laeufer
        self._sperre = threading.Lock()
        self.auftrag: Auftrag | None = None
        self.eigene = eigene.Bibliothek(self.wurzel / "eigene", self._einpassen)

    def starten(self) -> None:
        shutil.rmtree(self.wurzel / "arbeit", ignore_errors=True)   # Reste eines Neustarts mitten im Auftrag
        self.staende.aufraeumen()
        self.eigene.aufraeumen()
        self.abgleich.start()

    # Stand
    def _stand(self):
        s = self.staende.stand()
        if s is None:
            z = self.staende.zustand()
            text = "Silhouette Card Maker wird gerade eingerichtet. Bitte in ein paar Minuten noch einmal versuchen." \
                if self.staende.sucht or not z.get("gesucht") else \
                ("Silhouette Card Maker ist noch nicht eingerichtet. " + (z.get("meldung") or ""))
            raise Proxyfehler(text.strip(), 409)
        return s

    def _lauf(self, befehl: str, argumente: list[str], zeit: float, arbeit: Path, auftrag: Auftrag | None = None):
        """Läufer mit dem aktiven Stand; stürzt er ab, einmal mit dem vorherigen (Entscheidung des Nutzers)."""
        tag, code, py = self._stand()
        if auftrag:
            auftrag.stand = tag
        r = self._laeufer(py, code, befehl, argumente, zeit, arbeit)
        # Rückfall nur, wenn der Stand selbst kaputt ist (kein Ergebnis, Schnittstelle passt nicht), nie bei Fehlern
        # in Liste, Bildern, Netz oder Zeit: dann wäre der vorherige Stand genauso betroffen.
        if r.get("ok") or not (r.get("absturz") or r.get("standfehler")) or not self.staende.zustand().get("vorher"):
            return r
        grund = f"Absturz bei „{befehl}“ mit {tag}: {r.get('fehler')}"
        try:
            if not self.staende.zurueck(grund):
                return r
        except st.Standfehler:
            return r
        log.warning("Proxy-Druck: %s – versuche den vorherigen Stand.", grund)
        tag, code, py = self._stand()
        if auftrag:
            auftrag.stand = tag
        return self._laeufer(py, code, befehl, argumente, zeit, arbeit)

    def _einpassen(self, roh: Path, ausgabe: Path, klein: Path, zuschnitt: str) -> dict:
        """Eigenes Bild vom Läufer einpassen lassen (Pillow im venv des Stands, nie im Server)."""
        arbeit = self._arbeitsordner("einpassen")
        try:
            r = self._lauf("einpassen", ["--eingabe", str(roh), "--ausgabe", str(ausgabe), "--klein", str(klein),
                                         "--zuschnitt", zuschnitt], EINPASSEN_ZEIT, arbeit)
        finally:
            shutil.rmtree(arbeit, ignore_errors=True)
        if not r.get("ok"):
            raise eigene.Eigenfehler(str(r.get("fehler") or "Das Bild ließ sich nicht einpassen."),
                                     500 if r.get("standfehler") or r.get("absturz") else 400)
        return r

    # Prüfen (Vorschau)
    def pruefen(self, text, format_name: str | None, sets_ignorieren: bool, kat) -> dict:
        text = text_pruefen(text)
        if format_name is not None and format_name not in FORMATNAMEN:
            raise Proxyfehler("Unbekanntes Format.")
        arbeit = self._arbeitsordner("pruefen")
        try:
            (arbeit / "liste.txt").write_text(text, encoding="utf-8")
            # ohne Sperre: der aktive Stand wird erst beim übernächsten Wechsel gelöscht, Prüfen wartet nie auf einen
            # laufenden Auftrag
            r = self._lauf("erkennen", ["--liste", str(arbeit / "liste.txt")], ERKENNEN_ZEIT, arbeit)
        finally:
            shutil.rmtree(arbeit, ignore_errors=True)
        if not r.get("ok"):
            raise Proxyfehler("Die Liste ließ sich nicht lesen: " + str(r.get("fehler")), 500)
        formate = r["formate"]
        gewaehlt = format_name or format_waehlen(formate, kat)
        f = formate.get(gewaehlt or "", {})
        karten = katalog_pruefen(kat, f.get("karten") or [], sets_ignorieren)
        kandidaten = self.eigene.kandidaten()
        for k in karten:   # eigene Bilder je Zeile (Vorgabe zuerst); die Wahl trifft der Nutzer im Auftrag
            k["schluessel"] = schluessel(k.get("name") or "")
            k["eigene"] = kandidaten.get(k["schluessel"], [])
        exemplare = sum(k["anzahl"] for k in karten)
        return {"format": gewaehlt, "formate": [{"name": n, "titel": t, "karten": len(formate.get(n, {}).get("karten") or [])}
                                                for n, t in FORMATNAMEN.items()],
                "zeilen": karten, "uebersprungen": f.get("uebersprungen") or [], "exemplare": exemplare,
                "zu_viele": exemplare > KARTEN_MAX, "hoechstens": KARTEN_MAX, "katalog": kat is not None}

    # Auftrag
    def neu(self, text, format_name, name, rueckseite, optionen: dict, katalog: Path | None = None,
            preisdatei: Path | None = None) -> dict:
        zusatz = self.eigene.zusatz(optionen.get("zusatz"))
        zusatz_summe = sum(n for _, n in zusatz)
        if zusatz_summe > KARTEN_MAX:
            raise Proxyfehler(f"Zu viele Zusatzkarten: höchstens {KARTEN_MAX} je PDF.")
        nur_zusatz = zusatz_summe > 0 and not (isinstance(text, str) and text.strip())
        text = "" if nur_zusatz else text_pruefen(text)
        format_name = format_name or ("simple" if nur_zusatz else None)
        zuordnung = self.eigene.zuordnung(optionen.get("eigene"))
        if format_name not in FORMATNAMEN:
            raise Proxyfehler("Unbekanntes Format.")
        druckwahl = optionen.get("druckwahl") or "liste"
        if druckwahl not in DRUCKWAHL:
            raise Proxyfehler("Unbekannte Druckwahl.")
        if druckwahl == "guenstig" and not (katalog and Path(katalog).is_file()):
            raise Proxyfehler("Für den günstigsten Druck fehlt der Katalog.", 409)
        if druckwahl == "guenstig" and not (preisdatei and Path(preisdatei).is_file()):
            raise Proxyfehler("Für den günstigsten Druck fehlen die Preise von Cardmarket (der tägliche Abruf "
                              "ist noch nicht gelaufen).", 409)
        if not self.ablage_ok():
            raise Proxyfehler("Das Netzlaufwerk ist nicht erreichbar: Ohne Ablage für das PDF geht es gerade nicht.", 409)
        self._rueckseite_pfad(rueckseite)
        self._stand()
        with self._sperre:
            if self.auftrag and self.auftrag.zustand in ("wartet", "laedt", "fehlende", "baut"):
                raise Proxyfehler("Es läuft schon ein Proxy-Auftrag. Bitte warten, bis er fertig ist.", 409)
            a = Auftrag(uuid.uuid4().hex[:12], deckname(name), text, format_name,
                        {"sets_ignorieren": bool(optionen.get("sets_ignorieren")),
                         "extra_art": bool(optionen.get("extra_art")), "druckwahl": druckwahl,
                         "erster_druck": druckwahl == "erster"}, rueckseite or BEISPIEL_ID,
                        versatz=self.kalibrierung_werte() if optionen.get("versatz", True) is not False else None,
                        eigene=zuordnung, zusatz=zusatz, _katalog=katalog, _preisdatei=preisdatei)
            self.auftrag = a
        threading.Thread(target=self._ausfuehren, args=(a,), name="Proxyauftrag", daemon=True).start()
        return a.anzeige()

    def holen(self, auftrag_id: str) -> dict:
        a = self.auftrag
        if a is None or a.id != auftrag_id:
            raise Proxyfehler("Auftrag nicht gefunden.", 404)
        return a.anzeige()

    def entscheiden(self, auftrag_id: str, weiter: bool) -> dict:
        a = self.auftrag
        if a is None or a.id != auftrag_id:
            raise Proxyfehler("Auftrag nicht gefunden.", 404)
        if a.zustand != "fehlende":
            raise Proxyfehler("Der Auftrag wartet nicht auf eine Entscheidung.", 409)
        a._abbruch = not weiter
        # sofort sichtbar, damit die Oberfläche weiter abfragt (der Auftrag läuft im eigenen Thread weiter)
        a.zustand = "baut" if weiter else "abgebrochen"
        a._weiter.set()
        return a.anzeige()

    def _arbeitsordner(self, name: str) -> Path:
        o = self.wurzel / "arbeit" / f"{name}-{uuid.uuid4().hex[:8]}"
        o.mkdir(parents=True)
        return o

    def _ausfuehren(self, a: Auftrag) -> None:
        arbeit = self._arbeitsordner(a.id)
        beginn = time.monotonic()
        try:
            self._laden_und_bauen(a, arbeit, beginn)
        except Exception as f:   # der Auftrag meldet jeden Fehler, der Server läuft weiter
            log.exception("Proxy-Druck: Auftrag %s gescheitert", a.id)
            a.zustand, a.meldung = "fehler", str(f)[:400] or type(f).__name__
        finally:
            shutil.rmtree(arbeit, ignore_errors=True)

    def _guenstig_waehlen(self, a: Auftrag, arbeit: Path) -> None:
        """Liste auf den günstigsten Druck je Zeile umschreiben (proxy/druckwahl.py); danach lädt SCM genau diese."""
        a.zustand = "waehlt"
        (arbeit / "liste.txt").write_text(a.text, encoding="utf-8")
        r = self._lauf("erkennen", ["--liste", str(arbeit / "liste.txt")], ERKENNEN_ZEIT, arbeit, a)
        if not r.get("ok"):
            raise RuntimeError("Die Liste ließ sich nicht lesen: " + str(r.get("fehler")))
        karten = (r.get("formate", {}).get(a.format) or {}).get("karten") or []
        if not karten:
            raise RuntimeError("Die Liste enthält im gewählten Format keine Karten.")
        with closing(verbinden(a._katalog, nur_lesen=True)) as kat:
            try:
                neu = druckwahl.waehlen(kat, a._preisdatei, karten, bool(a.optionen.get("sets_ignorieren")))
            except druckwahl.Druckwahlfehler as f:
                raise RuntimeError(f"Günstigster Druck: {f}") from None
        a.text, a.format, a.gewaehlt = neu["text"], neu["format"], neu["gewaehlt"]
        a.optionen = {**a.optionen, "sets_ignorieren": False}   # die Liste nennt jetzt den Druck

    def _laden_und_bauen(self, a: Auftrag, arbeit: Path, beginn: float) -> None:
        if a.text and a.optionen.get("druckwahl") == "guenstig":
            self._guenstig_waehlen(a, arbeit)
            a.zeiten["waehlen_s"] = round(time.monotonic() - beginn, 1)
        start_laden = time.monotonic()
        a.zustand = "laedt"
        (arbeit / "liste.txt").write_text(a.text, encoding="utf-8")
        vorne, doppelt, hinten = arbeit / "vorne", arbeit / "doppelt", arbeit / "hinten"
        hinten.mkdir()
        shutil.copyfile(self._rueckseite_pfad(a.rueckseite), hinten / ("rueckseite" + self._rueckseite_pfad(a.rueckseite).suffix))
        zusatz_summe = sum(n for _, n in a.zusatz)
        zeilen = []
        if a.text:
            cache = self.ablage / "bilder" if self.ablage_ok() else None
            argumente = ["--liste", str(arbeit / "liste.txt"), "--format", a.format, "--optionen", json.dumps(a.optionen),
                         "--vorne", str(vorne), "--doppelt", str(doppelt), "--fortschritt", str(arbeit / "fortschritt.json")]
            if cache is not None:
                argumente += ["--cache", str(cache), "--cache-tage", str(CACHE_TAGE)]
            if a.eigene:
                (arbeit / "eigene.json").write_text(json.dumps(a.eigene, ensure_ascii=False), encoding="utf-8")
                argumente += ["--eigene", str(arbeit / "eigene.json")]
            argumente += ["--hoechstens", str(KARTEN_MAX - zusatz_summe)]
            beobachter = threading.Thread(target=self._fortschritt, args=(a, arbeit / "fortschritt.json"), daemon=True)
            beobachter.start()
            with self.staende.sperre:   # kein Umschalten des Stands, solange er rechnet
                r = self._lauf("laden", argumente, LADEN_ZEIT, arbeit, a)
            a.zeiten["laden_s"] = round(time.monotonic() - start_laden, 1)
            if not r.get("ok"):
                raise RuntimeError("Bilder laden gescheitert: " + str(r.get("fehler")))
            a.cache = r.get("cache") or {}
            if cache is not None:
                cache_aufraeumen(cache)
            zeilen = r.get("zeilen") or []
        else:
            a.zeiten["laden_s"] = 0.0
        # Zusatzkarten aus der Bibliothek (nur Dateien kopieren, keine Bildverarbeitung)
        for i, (eintrag, anzahl) in enumerate(a.zusatz):
            eigen_ablegen(eintrag, vorne, doppelt, f"zz{i:03d}eigen{eintrag['id']}", anzahl)
        a.eigen_genutzt = sum(1 for z in zeilen if z.get("eigen"))
        a.gesamt = a.fertig = len(zeilen) + len(a.zusatz)
        a.fehlende = [{"index": z.get("index"), "name": z.get("name"), "grund": grund(z.get("fehler")),
                       "fehler": z.get("fehler")} for z in zeilen if not z.get("ok")]
        if not any(z.get("ok") for z in zeilen) and not zusatz_summe:
            raise RuntimeError("Keine einzige Karte ließ sich laden.")
        if a.fehlende:
            a.zustand = "fehlende"
            # ohne Sperre warten (Prüfen, Zurück und ein Standwechsel bleiben möglich), höchstens ENTSCHEIDUNG_ZEIT
            if not a._weiter.wait(ENTSCHEIDUNG_ZEIT):
                a.zustand, a.meldung = "abgebrochen", "Ohne Entscheidung nach 30 Minuten abgebrochen."
                return
            if a._abbruch:
                a.zustand, a.meldung = "abgebrochen", "Abgebrochen, es wurde kein PDF erzeugt."
                return
        a.zustand = "baut"
        zwischen = arbeit / "proxy.pdf"
        mitte = time.monotonic()
        with self.staende.sperre:
            r = self._lauf("pdf", ["--vorne", str(vorne), "--hinten", str(hinten), "--doppelt", str(doppelt),
                                   "--ausgabe", str(zwischen)]
                           + (["--versatz", json.dumps(a.versatz)] if a.versatz else []), PDF_ZEIT, arbeit, a)
        a.zeiten["pdf_s"] = round(time.monotonic() - mitte, 1)
        if not r.get("ok"):
            raise RuntimeError("PDF erzeugen gescheitert: " + str(r.get("fehler")))
        ziel = self.ablage / "pdf"
        ziel.mkdir(parents=True, exist_ok=True)
        dateiname = f"{time.strftime('%Y%m%d-%H%M%S')}-{a.name}.pdf"
        teil = ziel / (dateiname + ".teil")
        shutil.copyfile(zwischen, teil)
        os.replace(teil, ziel / dateiname)
        exemplare = sum(1 for _ in vorne.iterdir())
        (ziel / dateiname).with_suffix(".json").write_text(json.dumps({
            "name": a.name, "erstellt": time.strftime("%Y-%m-%dT%H:%M:%S"), "karten": exemplare,
            "doppelseitig": sum(1 for _ in doppelt.iterdir()), "seiten": r.get("seiten"), "bytes": r.get("bytes"),
            "stand": a.stand, "fehlende": [f["name"] for f in a.fehlende], "rueckseite": a.rueckseite,
            "format": a.format, "versatz": a.versatz, "druckwahl": a.optionen.get("druckwahl", "liste"),
            "eigene": a.eigen_genutzt, "zusatz": zusatz_summe},
            ensure_ascii=False), encoding="utf-8")
        pdfs_aufraeumen(ziel)
        a.pdf, a.zustand = dateiname, "fertig"
        a.zeiten["gesamt_s"] = round(time.monotonic() - beginn, 1)
        log.info("Proxy-Druck: %s fertig (%d Karten, %s Seiten, %.0f s).", dateiname, exemplare, r.get("seiten"),
                 a.zeiten["gesamt_s"])

    # Kalibrierung
    @property
    def _kalibrierung_datei(self) -> Path:
        return self.wurzel / "kalibrierung.json"

    def kalibrierung(self) -> dict:
        try:
            d = json.loads(self._kalibrierung_datei.read_text(encoding="utf-8"))
            werte = {"x": int(d["x"]), "y": int(d["y"]), "winkel": float(d["winkel"])}
        except (OSError, ValueError, KeyError, TypeError):
            return {**KALIBRIERUNG_LEER, "geaendert": None, "vorher": None}
        return {**werte, "geaendert": d.get("geaendert"), "vorher": d.get("vorher")}

    def kalibrierung_werte(self) -> dict | None:
        """Werte für den Läufer; None, wenn nichts zu verschieben ist."""
        k = self.kalibrierung()
        werte = {"x": k["x"], "y": k["y"], "winkel": k["winkel"]}
        return None if werte == KALIBRIERUNG_LEER else werte

    @staticmethod
    def _zahl(wert, grenze: float, ganz: bool, name: str):
        if isinstance(wert, bool) or not isinstance(wert, (int, float)) or wert != wert:
            raise Proxyfehler(f"{name}: bitte eine Zahl angeben.")
        if ganz and wert != int(wert):
            raise Proxyfehler(f"{name}: bitte eine ganze Zahl angeben.")
        if abs(wert) > grenze:
            raise Proxyfehler(f"{name}: höchstens ±{grenze:g}.")
        return int(wert) if ganz else round(float(wert), 2)

    def kalibrierung_setzen(self, x, y, winkel) -> dict:
        werte = {"x": self._zahl(x, VERSATZ_MAX, True, "Waagerecht"), "y": self._zahl(y, VERSATZ_MAX, True, "Senkrecht"),
                 "winkel": self._zahl(winkel, WINKEL_MAX, False, "Winkel")}
        with self._sperre:
            alt = self.kalibrierung()
            neu = {**werte, "geaendert": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "vorher": {"x": alt["x"], "y": alt["y"], "winkel": alt["winkel"]}}
            self.wurzel.mkdir(parents=True, exist_ok=True)
            teil = self._kalibrierung_datei.with_suffix(".teil")
            teil.write_text(json.dumps(neu, ensure_ascii=False), encoding="utf-8")
            os.replace(teil, self._kalibrierung_datei)
        return self.kalibrierung()

    def kalibrierung_ablesen(self, x, y) -> dict:
        """Abgelesenes Feld des Bogens (der mit dem gespeicherten Versatz gedruckt wurde) zum Versatz dazuzählen."""
        dx, dy = self._zahl(x, ABLESEN_MAX, True, "Feld waagerecht"), self._zahl(y, ABLESEN_MAX, True, "Feld senkrecht")
        k = self.kalibrierung()
        return self.kalibrierung_setzen(k["x"] + dx, k["y"] + dy, k["winkel"])

    def kalibrierbogen(self) -> Path:
        """Kalibrierbogen A4 mit dem gespeicherten Versatz; der letzte bleibt liegen, solange sich nichts ändert."""
        tag, code, py = self._stand()
        werte = self.kalibrierung_werte()
        schluessel = "{}_{}_{}_{}".format(tag, *(werte or KALIBRIERUNG_LEER).values()).replace(".", "-")
        ordner = self.wurzel / "kalibrierung"
        datei = ordner / f"kalibrierbogen-{schluessel}.pdf"
        if datei.is_file():
            return datei
        if not self.staende.sperre.acquire(timeout=5):
            raise Proxyfehler("Gerade läuft ein Proxy-Auftrag oder ein Standwechsel. Bitte gleich noch einmal.", 409)
        try:
            if datei.is_file():   # ein gleichzeitiger Aufruf hat ihn eben gebaut
                return datei
            arbeit = self._arbeitsordner("kalibrierung")
            try:
                r = self._laeufer(py, code, "kalibrierung",
                                  ["--arbeit", str(arbeit / "bogen"), "--ausgabe", str(arbeit / "bogen.pdf")]
                                  + (["--versatz", json.dumps(werte)] if werte else []), KALIBRIERUNG_ZEIT, arbeit)
                if not r.get("ok"):
                    raise Proxyfehler("Kalibrierbogen gescheitert: " + str(r.get("fehler")), 500)
                shutil.rmtree(ordner, ignore_errors=True)   # nur der neueste Bogen bleibt
                ordner.mkdir(parents=True)
                shutil.copyfile(arbeit / "bogen.pdf", datei)
            finally:
                shutil.rmtree(arbeit, ignore_errors=True)
        finally:
            self.staende.sperre.release()
        return datei

    @staticmethod
    def _fortschritt(a: Auftrag, datei: Path) -> None:
        while a.zustand in ("wartet", "laedt"):
            try:
                d = json.loads(datei.read_text(encoding="utf-8"))
                a.fertig, a.gesamt = int(d["fertig"]), int(d["gesamt"])
            except (OSError, ValueError, KeyError, TypeError):
                pass
            time.sleep(1)

    # PDFs
    def pdfs(self) -> list[dict]:
        ordner = self.ablage / "pdf"
        if not self.ablage_ok() or not ordner.is_dir():
            return []
        liste = []
        for p in sorted(ordner.glob("*.pdf"), key=lambda p: p.name, reverse=True):
            if not PDF_MUSTER.match(p.name):
                continue
            try:
                info = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                info = {}
            liste.append({"datei": p.name, "name": info.get("name") or p.stem[16:], "erstellt": info.get("erstellt"),
                          "karten": info.get("karten"), "seiten": info.get("seiten"), "bytes": p.stat().st_size,
                          "fehlende": info.get("fehlende") or [], "stand": info.get("stand")})
        return liste[:PDF_BEHALTEN]

    def pdf_pfad(self, datei: str) -> Path:
        if not PDF_MUSTER.match(datei or ""):
            raise Proxyfehler("PDF nicht gefunden.", 404)
        p = self.ablage / "pdf" / datei
        if not self.ablage_ok() or not p.is_file():
            raise Proxyfehler("PDF nicht gefunden.", 404)
        return p

    # Rückseiten
    @property
    def _rueckseiten(self) -> Path:
        return self.wurzel / "rueckseiten"

    def _rueckseiten_liste(self) -> list[dict]:
        try:
            return json.loads((self._rueckseiten / "liste.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def rueckseiten(self) -> list[dict]:
        return [{"id": BEISPIEL_ID, "name": "Beispiel (neutral)", "breite": 745, "hoehe": 1040, "beispiel": True},
                *self._rueckseiten_liste()]

    def _rueckseite_pfad(self, rid: str | None) -> Path:
        if not rid or rid == BEISPIEL_ID:
            return BEISPIEL
        for r in self._rueckseiten_liste():
            if r["id"] == rid and ID_MUSTER.match(rid):
                p = self._rueckseiten / f"{rid}.{r['art']}"
                if p.is_file():
                    return p
        raise Proxyfehler("Rückseite nicht gefunden.", 404)

    def rueckseite_bild(self, rid: str) -> Path:
        return self._rueckseite_pfad(rid)

    def rueckseite_neu(self, daten: bytes, name: str) -> dict:
        if len(daten) > RUECKSEITE_MAX:
            raise Proxyfehler(f"Die Rückseite ist zu groß (höchstens {RUECKSEITE_MAX // 2 ** 20} MB).", 413)
        masse = bildmasse(daten)
        if masse is None:
            raise Proxyfehler("Bitte ein PNG- oder JPEG-Bild wählen.")
        art, breite, hoehe = masse
        if not (200 <= breite <= 6000 and 200 <= hoehe <= 8000):
            raise Proxyfehler(f"Das Bild ist {breite} × {hoehe} Pixel groß; erwartet etwa 745 × 1040.")
        with self._sperre:
            liste = self._rueckseiten_liste()
            if len(liste) >= RUECKSEITEN_MAX:
                raise Proxyfehler(f"Es gibt schon {RUECKSEITEN_MAX} Rückseiten.", 409)
            rid = uuid.uuid4().hex[:12]
            self._rueckseiten.mkdir(parents=True, exist_ok=True)
            teil = self._rueckseiten / f"{rid}.teil"
            teil.write_bytes(daten)
            os.replace(teil, self._rueckseiten / f"{rid}.{art}")
            name = "".join(z for z in str(name or "") if z.isprintable()).strip()[:NAME_MAX] or "Rückseite"
            eintrag = {"id": rid, "name": name, "art": art, "breite": breite, "hoehe": hoehe,
                       "hochgeladen": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       "seitenverhaeltnis_ok": abs(breite / hoehe - 63 / 88) < 0.03}
            self._liste_speichern(liste + [eintrag])
        return eintrag

    def rueckseite_entfernen(self, rid: str) -> None:
        if rid == BEISPIEL_ID:
            raise Proxyfehler("Die Beispiel-Rückseite bleibt.", 409)
        with self._sperre:
            liste = self._rueckseiten_liste()
            treffer = [r for r in liste if r["id"] == rid and ID_MUSTER.match(rid)]
            if not treffer:
                raise Proxyfehler("Rückseite nicht gefunden.", 404)
            self._liste_speichern([r for r in liste if r["id"] != rid])
            (self._rueckseiten / f"{rid}.{treffer[0]['art']}").unlink(missing_ok=True)

    def _liste_speichern(self, liste: list[dict]) -> None:
        teil = self._rueckseiten / "liste.teil"
        teil.write_text(json.dumps(liste, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(teil, self._rueckseiten / "liste.json")


_DIENST: Dienst | None = None


def dienst() -> Dienst | None:
    """Der Dienst des Add-ons aus der Umgebung (`BINDER_PROXY`, `BINDER_PROXY_ABLAGE`), sonst None (am PC)."""
    global _DIENST
    if _DIENST is None and os.environ.get("BINDER_PROXY"):
        _DIENST = Dienst(Path(os.environ["BINDER_PROXY"]),
                         Path(os.environ.get("BINDER_PROXY_ABLAGE") or Path(os.environ["BINDER_PROXY"]) / "ablage"))
    return _DIENST
