"""Eigene Bilder für den Proxy-Druck (Wunsch des Nutzers, 11.11.2026).

    <wurzel>/eigene/liste.json          Einträge (id, name, karte, vorgabe, entwurf, je Seite Maße und Zuschnitt)
    <wurzel>/eigene/<id>-<seite>.png    eingepasstes Bild (Seite vorne oder hinten), in den HA-Sicherungen
    <wurzel>/eigene/<id>-<seite>-klein.jpg  Vorschau (300 px breit)
    <wurzel>/eigene/<id>-<seite>-roh.*  hochgeladenes Original, nur solange der Eintrag ein Entwurf ist

Antworten des Nutzers: Ein eigenes Bild ersetzt eine Karte der Liste (über den Kartennamen) oder kommt als
Zusatzkarte dazu; Bibliothek mit Vorgabe (ein Bild je Kartenname wird automatisch genommen, je Auftrag abwählbar);
automatisch einpassen mit Vorschau vor dem Speichern; Hochladen, Adresse (URL) und eigene zweite Seite.
Der Server verarbeitet keine Bilder: Das Einpassen macht der Läufer im venv des Stands (`einpassen`).
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from binderapp.proxy.laeufer import ZUSCHNITTE, schluessel

EIGENE_MAX = 300
BILD_MAX = 40 * 1024 * 1024
NAME_MAX = 80
ENTWURF_TAGE = 1
SEITEN = ("vorne", "hinten")
ID_MUSTER = re.compile(r"^[0-9a-f]{12}$")
URL_ZEIT = 30
KENNUNG = "MTG-Binder-Addon (Proxy-Druck)"     # ohne persönliche Daten
ANZAHL_MAX = 99


class Eigenfehler(ValueError):
    def __init__(self, text: str, status: int = 400):
        super().__init__(text)
        self.status = status


def bildart(daten: bytes) -> str | None:
    """PNG, JPEG oder WebP am Dateikopf; alles andere lehnt der Server ab, bevor der Läufer es öffnet."""
    if daten[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if daten[:3] == b"\xff\xd8\xff":
        return "jpg"
    if daten[:4] == b"RIFF" and daten[8:12] == b"WEBP":
        return "webp"
    return None


def text(wert, grenze: int = NAME_MAX) -> str:
    return "".join(z for z in str(wert or "") if z.isprintable()).strip()[:grenze]


# --- Adresse (URL) ---------------------------------------------------------------------------------------------

def _adresse_pruefen(url: str) -> None:
    """Nur http(s) zu öffentlichen Adressen: kein Zugriff auf Home Assistant, Supervisor oder das Heimnetz."""
    teile = urllib.parse.urlsplit(url)
    if teile.scheme not in ("http", "https") or not teile.hostname:
        raise Eigenfehler("Bitte eine Adresse mit http:// oder https:// einfügen.")
    try:
        infos = socket.getaddrinfo(teile.hostname, teile.port or (443 if teile.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        raise Eigenfehler("Diese Adresse ist nicht erreichbar (Name unbekannt).") from None
    for info in infos:
        if not ipaddress.ip_address(info[4][0].split("%")[0]).is_global:
            raise Eigenfehler("Adressen im eigenen Netz sind nicht erlaubt.")


class _Weiterleitung(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _adresse_pruefen(newurl)   # jede Weiterleitung noch einmal prüfen
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def url_holen(url, grenze: int = BILD_MAX, oeffnen=None) -> tuple[bytes, str]:
    """Bild von einer Adresse laden; gibt (Daten, Dateiname aus der Adresse) zurück."""
    url = text(url, 2000)
    _adresse_pruefen(url)
    oeffnen = oeffnen or urllib.request.build_opener(_Weiterleitung).open
    anfrage = urllib.request.Request(url, headers={"User-Agent": KENNUNG, "Accept": "image/*"})
    try:
        with oeffnen(anfrage, timeout=URL_ZEIT) as antwort:
            daten = antwort.read(grenze + 1)
    except Eigenfehler:
        raise
    except urllib.error.HTTPError as f:
        raise Eigenfehler(f"Die Adresse antwortet mit Fehler {f.code}.") from None
    except (urllib.error.URLError, OSError, ValueError) as f:
        raise Eigenfehler(f"Die Adresse ist nicht erreichbar ({type(f).__name__}).") from None
    if len(daten) > grenze:
        raise Eigenfehler(f"Das Bild ist zu groß (höchstens {grenze // 2 ** 20} MB).", 413)
    name = urllib.parse.unquote(Path(urllib.parse.urlsplit(url).path).name)
    return daten, name


# --- Bibliothek -----------------------------------------------------------------------------------------------

class Bibliothek:
    """Eigene Bilder: Einträge in `liste.json`, Bilder daneben. `einpassen(eingabe, ausgabe, klein, zuschnitt)`
    ruft den Läufer und gibt sein Ergebnis zurück (oder wirft)."""

    def __init__(self, ordner: Path, einpassen):
        self.ordner = Path(ordner)
        self._einpassen = einpassen
        self._sperre = threading.RLock()

    # Liste
    def _lesen(self) -> list[dict]:
        try:
            return json.loads((self.ordner / "liste.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _speichern(self, liste: list[dict]) -> None:
        self.ordner.mkdir(parents=True, exist_ok=True)
        teil = self.ordner / "liste.teil"
        teil.write_text(json.dumps(liste, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(teil, self.ordner / "liste.json")

    @staticmethod
    def _anzeige(e: dict) -> dict:
        return {**e, "schluessel": schluessel(e.get("karte") or "")}

    def liste(self) -> list[dict]:
        return [self._anzeige(e) for e in self._lesen()]

    def _eintrag(self, eid: str, liste: list[dict] | None = None) -> dict:
        if not ID_MUSTER.match(str(eid or "")):
            raise Eigenfehler("Bild nicht gefunden.", 404)
        for e in self._lesen() if liste is None else liste:
            if e["id"] == eid:
                return e
        raise Eigenfehler("Bild nicht gefunden.", 404)

    def _datei(self, eid: str, seite: str, art: str = "png") -> Path:
        return self.ordner / f"{eid}-{seite}.{art}" if art != "klein" else self.ordner / f"{eid}-{seite}-klein.jpg"

    def _roh(self, eid: str, seite: str) -> Path | None:
        treffer = sorted(self.ordner.glob(f"{eid}-{seite}-roh.*"))
        return treffer[0] if treffer else None

    def pfad(self, eid: str, seite: str, klein: bool) -> Path:
        e = self._eintrag(eid)
        if seite not in SEITEN or not e.get(seite):
            raise Eigenfehler("Bild nicht gefunden.", 404)
        p = self._datei(eid, seite, "klein" if klein else "png")
        if not p.is_file():
            raise Eigenfehler("Bild nicht gefunden.", 404)
        return p

    # Einpassen
    def _bild_ablegen(self, eid: str, seite: str, daten: bytes, zuschnitt: str = "auto") -> dict:
        if len(daten) > BILD_MAX:
            raise Eigenfehler(f"Das Bild ist zu groß (höchstens {BILD_MAX // 2 ** 20} MB).", 413)
        art = bildart(daten)
        if art is None:
            raise Eigenfehler("Bitte ein PNG-, JPEG- oder WebP-Bild wählen.")
        self.ordner.mkdir(parents=True, exist_ok=True)
        for alt in self.ordner.glob(f"{eid}-{seite}-roh.*"):
            alt.unlink(missing_ok=True)
        roh = self.ordner / f"{eid}-{seite}-roh.{art}"
        teil = roh.with_name(roh.name + ".teil")
        teil.write_bytes(daten)
        os.replace(teil, roh)
        return self._einpassen_von(eid, seite, roh, zuschnitt)

    def _einpassen_von(self, eid: str, seite: str, roh: Path, zuschnitt: str) -> dict:
        if zuschnitt not in ZUSCHNITTE:
            raise Eigenfehler("Unbekannter Zuschnitt.")
        ausgabe, klein = self._datei(eid, seite), self._datei(eid, seite, "klein")
        r = self._einpassen(roh, ausgabe.with_name(ausgabe.name + ".teil.png"),
                            klein.with_name(klein.name + ".teil.jpg"), zuschnitt)
        os.replace(ausgabe.with_name(ausgabe.name + ".teil.png"), ausgabe)
        os.replace(klein.with_name(klein.name + ".teil.jpg"), klein)
        return {"breite": r["breite"], "hoehe": r["hoehe"], "zuschnitt": r["zuschnitt"], "gewuenscht": r["gewuenscht"],
                "gedreht": r["gedreht"], "original": r["original"], "stand": time.strftime("%Y%m%d%H%M%S")}

    def _dateien_weg(self, eid: str, seite: str | None = None) -> None:
        muster = f"{eid}-{seite}*" if seite else f"{eid}-*"
        for p in self.ordner.glob(muster):
            p.unlink(missing_ok=True)

    # Ändern
    def neu(self, daten: bytes, name: str, quelle: str) -> dict:
        with self._sperre:
            if len(self._lesen()) >= EIGENE_MAX:
                raise Eigenfehler(f"Es gibt schon {EIGENE_MAX} eigene Bilder.", 409)
        eid = uuid.uuid4().hex[:12]
        try:
            vorne = self._bild_ablegen(eid, "vorne", daten)
        except Exception:
            self._dateien_weg(eid)
            raise
        name = text(name) or "Eigenes Bild"
        eintrag = {"id": eid, "name": name, "karte": name, "vorgabe": False, "entwurf": True, "quelle": quelle,
                   "hochgeladen": time.strftime("%Y-%m-%dT%H:%M:%S"), "vorne": vorne, "hinten": None}
        with self._sperre:
            self._speichern(self._lesen() + [eintrag])
        return self._anzeige(eintrag)

    def seite_neu(self, eid: str, daten: bytes) -> dict:
        """Eigene zweite Seite (hinten) für einen Eintrag; ohne Entwurf gilt der automatische Zuschnitt."""
        self._eintrag(eid)
        hinten = self._bild_ablegen(eid, "hinten", daten)
        with self._sperre:
            liste = self._lesen()
            e = self._eintrag(eid, liste)
            e["hinten"] = hinten
            if not e.get("entwurf"):
                self._dateien_weg_roh(eid)
            self._speichern(liste)
        return self._anzeige(e)

    def _dateien_weg_roh(self, eid: str) -> None:
        for p in self.ordner.glob(f"{eid}-*-roh.*"):
            p.unlink(missing_ok=True)

    def aendern(self, eid: str, daten: dict) -> dict:
        e = self._eintrag(eid)
        zuschnitt = daten.get("zuschnitt")
        if zuschnitt is not None:   # nur im Entwurf: aus dem Original neu einpassen
            if not isinstance(zuschnitt, dict) or not e.get("entwurf"):
                raise Eigenfehler("Den Zuschnitt gibt es nur vor dem Speichern.", 409)
            neu = {}
            for seite, art in zuschnitt.items():
                roh = self._roh(eid, seite) if seite in SEITEN else None
                if roh is None:
                    raise Eigenfehler("Das Original dieses Bildes fehlt.", 409)
                neu[seite] = self._einpassen_von(eid, seite, roh, str(art))
        else:
            neu = {}
        with self._sperre:
            liste = self._lesen()
            e = self._eintrag(eid, liste)
            e.update(neu)
            if "name" in daten:
                e["name"] = text(daten["name"]) or e["name"]
            if "karte" in daten:
                e["karte"] = text(daten["karte"], 200)
            if daten.get("fertig") is True:
                e["entwurf"] = False
                self._dateien_weg_roh(eid)
                if "vorgabe" not in daten:   # erstes Bild zu einem Kartennamen wird Vorgabe
                    k = schluessel(e["karte"])
                    daten = {**daten, "vorgabe": bool(k) and not any(
                        x["vorgabe"] and x["id"] != eid and schluessel(x.get("karte") or "") == k for x in liste)}
            if "vorgabe" in daten:
                e["vorgabe"] = daten["vorgabe"] is True and bool(schluessel(e["karte"]))
            if e["vorgabe"]:   # höchstens eine Vorgabe je Kartenname
                k = schluessel(e["karte"])
                for x in liste:
                    if x["id"] != eid and schluessel(x.get("karte") or "") == k:
                        x["vorgabe"] = False
            self._speichern(liste)
        return self._anzeige(e)

    def entfernen(self, eid: str, seite: str | None = None) -> dict | None:
        with self._sperre:
            liste = self._lesen()
            e = self._eintrag(eid, liste)
            if seite == "hinten":
                e["hinten"] = None
                self._speichern(liste)
                self._dateien_weg(eid, "hinten")
                return self._anzeige(e)
            self._speichern([x for x in liste if x["id"] != eid])
            self._dateien_weg(eid)
        return None

    def aufraeumen(self) -> None:
        """Entwürfe älter als einen Tag und Dateien ohne Eintrag entfernen."""
        if not self.ordner.is_dir():
            return
        with self._sperre:
            grenze = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - ENTWURF_TAGE * 86400))
            liste = self._lesen()
            bleibt = [e for e in liste if not (e.get("entwurf") and e.get("hochgeladen", "") < grenze)]
            if len(bleibt) != len(liste):
                self._speichern(bleibt)
            ids = {e["id"] for e in bleibt}
            for p in self.ordner.iterdir():
                if p.name not in ("liste.json",) and p.name[:12] not in ids:
                    p.unlink(missing_ok=True)

    # Aufträge
    def zuordnung(self, wahl) -> dict:
        """{Schlüssel: {id, vorne, hinten}} für den Läufer: Vorgaben, darüber die Wahl des Auftrags
        ({Schlüssel: id oder None für Scryfall})."""
        liste = [e for e in self._lesen() if not e.get("entwurf")]
        nach_id = {e["id"]: e for e in liste}
        ergebnis = {}
        for e in liste:
            k = schluessel(e.get("karte") or "")
            if k and e.get("vorgabe"):
                ergebnis[k] = e
        if wahl is not None and not isinstance(wahl, dict):
            raise Eigenfehler("Ungültige Wahl der eigenen Bilder.")
        for k, eid in (wahl or {}).items():
            k = schluessel(k)
            if eid is None or eid == "":
                ergebnis.pop(k, None)
            elif eid in nach_id:
                ergebnis[k] = nach_id[eid]
            else:
                raise Eigenfehler("Ein gewähltes eigenes Bild gibt es nicht mehr.", 409)
        return {k: self._fuer_laeufer(e) for k, e in ergebnis.items() if k}

    def _fuer_laeufer(self, e: dict) -> dict:
        return {"id": e["id"], "vorne": str(self._datei(e["id"], "vorne")),
                "hinten": str(self._datei(e["id"], "hinten")) if e.get("hinten") else None}

    def zusatz(self, auswahl) -> list[tuple[dict, int]]:
        """Zusatzkarten [{id, anzahl}] → [(Läufer-Eintrag, Anzahl)], nur fertige Einträge."""
        if auswahl in (None, []):
            return []
        if not isinstance(auswahl, list):
            raise Eigenfehler("Ungültige Zusatzkarten.")
        nach_id = {e["id"]: e for e in self._lesen() if not e.get("entwurf")}
        ergebnis = []
        for z in auswahl:
            if not isinstance(z, dict) or z.get("id") not in nach_id:
                raise Eigenfehler("Eine Zusatzkarte gibt es nicht mehr.", 409)
            anzahl = z.get("anzahl")
            if isinstance(anzahl, bool) or not isinstance(anzahl, int) or not 0 <= anzahl <= ANZAHL_MAX:
                raise Eigenfehler(f"Anzahl je Zusatzkarte: 0 bis {ANZAHL_MAX}.")
            if anzahl:
                ergebnis.append((self._fuer_laeufer(nach_id[z["id"]]), anzahl))
        return ergebnis

    def kandidaten(self) -> dict[str, list[str]]:
        """Je Schlüssel die fertigen Bilder (Vorgabe zuerst) für die Vorschau."""
        ergebnis: dict[str, list[str]] = {}
        for e in sorted(self._lesen(), key=lambda e: not e.get("vorgabe")):
            k = schluessel(e.get("karte") or "")
            if k and not e.get("entwurf"):
                ergebnis.setdefault(k, []).append(e["id"])
        return ergebnis
