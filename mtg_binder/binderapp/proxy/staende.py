"""Stände von Silhouette Card Maker (SCM) im Add-on: suchen, laden, einrichten, testen, umschalten.

Entscheidungen des Nutzers (07.11.2026): nur Releases/Tags des Originals, Suche beim Start, danach alle 24 h und
per Knopf; nach bestandenem Testlauf automatisch umschalten (nie während eines Auftrags); der vorherige Stand
bleibt zum Zurückfallen. Geladen werden nur die Dateien, die Deckliste → PDF braucht (rund 2 MB statt 570 MB).

    <wurzel>/stand.json                 aktiv, vorher, letzte Suche, Meldung, gescheiterte Tags
    <wurzel>/staende/<tag>/code/        Dateien des Tags (mit LICENSE.md des Originals)
    <wurzel>/staende/<tag>/venv/        eigenes venv mit den gepinnten Paketen des Tags
    <wurzel>/staende/<tag>/stand.json   Commit, Zeitpunkte, Ergebnis des Testlaufs

Nur Standardbibliothek; der Läufer (`laeufer.py`) läuft als eigener Prozess im venv des Stands.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

log = logging.getLogger("binderapp.proxy")

REPO = "Alan-Cha/silhouette-card-maker"
API = f"https://api.github.com/repos/{REPO}"
ROH = f"https://raw.githubusercontent.com/{REPO}"
KENNUNG = "MTG-Binder-Addon (Proxy-Druck)"     # ohne persönliche Daten
TAG_MUSTER = re.compile(r"^v?(\d{1,4})\.(\d{1,4})(?:\.(\d{1,4}))?$")
SHA_MUSTER = re.compile(r"^[0-9a-f]{40}$")
PFAD_MUSTER = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-/]{0,200}$")
# Pakete aus requirements.txt, die nur Windows oder Silhouette Studio brauchen (dxf_to_studio3.py, offset_pdf.py,
# generate_dxf.py); alles andere wird installiert, der Testlauf entscheidet.
AUSGENOMMEN = {"pyautogui", "pywinauto", "ezdxf", "pypdfium2", "split-image"}
ANFORDERUNG = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._\-]*)(\[[A-Za-z0-9,._\-]+\])?\s*([<>=!~]=?\s*[A-Za-z0-9.*+!]+"
                         r"(\s*,\s*[<>=!~]=?\s*[A-Za-z0-9.*+!]+)*)?\s*(;[A-Za-z0-9 _.'\"<>=!~()]*)?$")
DATEI_MAX = 8 * 1024 * 1024
GESAMT_MAX = 30 * 1024 * 1024
ANTWORT_MAX = 4 * 1024 * 1024
ZEITLIMIT = 30
PIP_ZEIT = 20 * 60
TEST_ZEIT = 5 * 60
SUCHABSTAND = 24 * 3600
LAEUFER = Path(__file__).with_name("laeufer.py")


class Standfehler(RuntimeError):
    pass


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def version_von(tag: str) -> tuple[int, ...] | None:
    m = TAG_MUSTER.match(tag)
    return tuple(int(t or 0) for t in m.groups()) if m else None


def gewollt(pfad: str) -> bool:
    """Nur was Deckliste → PDF braucht: Python-Dateien im Stammordner, assets/, plugins/, Lizenz, Abhängigkeiten."""
    teile = PurePosixPath(pfad).parts
    if not PFAD_MUSTER.match(pfad) or ".." in teile or any(t.startswith(".") for t in teile):
        return False
    if len(teile) == 1:
        return pfad.endswith(".py") or pfad in ("requirements.txt", "pyproject.toml") or pfad.upper().startswith("LICENSE")
    return teile[0] in ("assets", "plugins")


def anforderungen(text: str) -> list[str]:
    """Zeilen aus requirements.txt ohne die ausgenommenen Pakete. Optionen (`-r`, `--index-url` …) und
    unbekannte Schreibweisen lehnen wir ab, statt sie pip zu geben."""
    zeilen = []
    for roh in text.splitlines():
        zeile = roh.split(" #")[0].strip()
        if not zeile or zeile.startswith("#"):
            continue
        m = ANFORDERUNG.match(zeile)
        if not m or "--" in zeile:
            raise Standfehler(f"Unbekannte Zeile in requirements.txt: {zeile[:80]}")
        if _norm(m.group(1)) not in AUSGENOMMEN:
            zeilen.append(zeile)
    if not zeilen:
        raise Standfehler("requirements.txt nennt keine Pakete.")
    return zeilen


def python_passt(pyproject: str, version: tuple[int, int] = sys.version_info[:2]) -> str | None:
    """None, wenn `requires-python` zu `version` passt, sonst die Anforderung (nur >=, >, <, <=, ==)."""
    try:
        anforderung = tomllib.loads(pyproject).get("project", {}).get("requires-python")
    except tomllib.TOMLDecodeError:
        return None
    if not anforderung:
        return None
    for teil in str(anforderung).split(","):
        m = re.match(r"^\s*(>=|>|<=|<|==)\s*(\d+)\.(\d+)", teil)
        if not m:
            continue
        ziel = (int(m.group(2)), int(m.group(3)))
        ok = {">=": version >= ziel, ">": version > ziel, "<=": version <= ziel, "<": version < ziel,
              "==": version == ziel}[m.group(1)]
        if not ok:
            return str(anforderung)
    return None


# --- Quellen --------------------------------------------------------------------------------------------------

def _holen(url: str, grenze: int = ANTWORT_MAX) -> bytes:
    anfrage = urllib.request.Request(url, headers={"User-Agent": KENNUNG, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(anfrage, timeout=ZEITLIMIT) as antwort:
        daten = antwort.read(grenze + 1)
    if len(daten) > grenze:
        raise Standfehler(f"Antwort zu groß: {url}")
    return daten


class GitHub:
    """Original-Repository auf GitHub (öffentliche API ohne Anmeldung: 60 Anfragen je Stunde reichen weit)."""

    def tags(self, seiten: int = 5) -> list[tuple[str, str]]:
        """Alle Tags (seitenweise, höchstens 500)."""
        alle = []
        for seite in range(1, seiten + 1):
            daten = json.loads(_holen(f"{API}/tags?per_page=100&page={seite}"))
            alle += [(t["name"], t["commit"]["sha"]) for t in daten
                     if isinstance(t, dict) and isinstance(t.get("name"), str) and SHA_MUSTER.match(t["commit"]["sha"])]
            if len(daten) < 100:
                break
        return alle

    def dateien(self, sha: str) -> list[tuple[str, int]]:
        daten = json.loads(_holen(f"{API}/git/trees/{sha}?recursive=1", 16 * 1024 * 1024))
        if daten.get("truncated"):
            raise Standfehler("Dateiliste des Stands unvollständig.")
        return [(e["path"], int(e.get("size") or 0)) for e in daten.get("tree", []) if e.get("type") == "blob"]

    def lesen(self, sha: str, pfad: str) -> bytes:
        return _holen(f"{ROH}/{sha}/{pfad}", DATEI_MAX)


@dataclass
class Ordner:
    """Ein Ordner als Quelle mit genau einem Tag (Tests und Messung am PC, z. B. ein lokaler Klon)."""
    wurzel: Path
    tag: str = "v0.0.0"
    sha: str = "0" * 40

    def tags(self) -> list[tuple[str, str]]:
        return [(self.tag, self.sha)]

    def dateien(self, sha: str) -> list[tuple[str, int]]:
        return [(p.relative_to(self.wurzel).as_posix(), p.stat().st_size)
                for p in self.wurzel.rglob("*") if p.is_file() and ".git" not in p.parts]

    def lesen(self, sha: str, pfad: str) -> bytes:
        return (self.wurzel / pfad).read_bytes()


# --- Einrichten -----------------------------------------------------------------------------------------------

def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


# Nur diese Umgebungsvariablen erreichen pip und den Läufer (fremder Code): kein SUPERVISOR_TOKEN, keine BINDER_*.
UMGEBUNG_ERLAUBT = {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy",
                    "https_proxy", "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
                    # nur Windows (Tests und Messung am PC): ohne sie starten Python und das Netz dort nicht
                    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "USERPROFILE",
                    "APPDATA", "LOCALAPPDATA", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE"}


def ohne_umgebung(heim: Path | None = None) -> dict:
    """Umgebung für Unterprozesse als Whitelist; HOME zeigt auf einen eigenen Ordner, nicht auf /root oder /data."""
    umgebung = {k: v for k, v in os.environ.items() if k in UMGEBUNG_ERLAUBT}
    umgebung["PYTHONIOENCODING"] = "utf-8"
    umgebung["PYTHONNOUSERSITE"] = "1"
    if heim is not None:
        umgebung["HOME"] = str(heim)
    return umgebung


def code_laden(quelle, sha: str, ziel: Path) -> dict:
    dateien = [(p, g) for p, g in quelle.dateien(sha) if gewollt(p)]
    namen = {p for p, _ in dateien}
    for pflicht in ("create_pdf.py", "utilities.py", "requirements.txt", "plugins/mtg/deck_formats.py",
                    "plugins/mtg/scryfall.py"):
        if pflicht not in namen:
            raise Standfehler(f"Im Stand fehlt {pflicht}.")
    if not any(p.upper().startswith("LICENSE") for p in namen):
        raise Standfehler("Im Stand fehlt die Lizenzdatei.")
    if sum(g for _, g in dateien) > GESAMT_MAX:
        raise Standfehler("Stand größer als erwartet.")
    summe = 0
    for pfad, _ in dateien:
        inhalt = quelle.lesen(sha, pfad)
        summe += len(inhalt)
        if summe > GESAMT_MAX:
            raise Standfehler("Stand größer als erwartet.")
        datei = ziel.joinpath(*PurePosixPath(pfad).parts)
        if ziel.resolve() not in datei.resolve().parents:
            raise Standfehler(f"Ungültiger Pfad im Stand: {pfad}")
        datei.parent.mkdir(parents=True, exist_ok=True)
        datei.write_bytes(inhalt)
    return {"dateien": len(dateien), "bytes": summe}


def installieren(code: Path, venv: Path, python: str, protokoll: Path) -> None:
    """Eigenes venv, nur fertige Pakete (kein Übersetzen im Add-on), Ausgabe von pip ins Protokoll."""
    code, venv = code.resolve(), venv.resolve()
    liste = anforderungen((code / "requirements.txt").read_text(encoding="utf-8"))
    datei = venv.parent / "anforderungen.txt"
    datei.write_text("\n".join(liste) + "\n", encoding="utf-8")
    with protokoll.open("w", encoding="utf-8") as aus:
        for befehl in ([python, "-m", "venv", str(venv)],
                       [str(venv_python(venv)), "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check",
                        "--no-input", "--only-binary=:all:", "-r", str(datei)]):
            r = subprocess.run(befehl, stdout=aus, stderr=subprocess.STDOUT, timeout=PIP_ZEIT,
                               env=ohne_umgebung(venv.parent), cwd=venv.parent)
            if r.returncode:
                aus.flush()
                raise Standfehler("Installation der Pakete gescheitert: " + _schluss(protokoll))


def _schluss(protokoll: Path, zeilen: int = 3) -> str:
    try:
        return " | ".join(protokoll.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-zeilen:])[-400:]
    except OSError:
        return "kein Protokoll"


def laeufer(python: Path, code: Path, befehl: str, argumente: list[str], zeit: float, arbeit: Path) -> dict:
    """Startet den Läufer im venv eines Stands und gibt sein Ergebnis zurück (JSON-Datei, nie die Ausgabe)."""
    python, code, arbeit = Path(python).absolute(), Path(code).resolve(), Path(arbeit).resolve()
    ergebnis = arbeit / f"ergebnis-{befehl}.json"
    ergebnis.unlink(missing_ok=True)
    try:
        subprocess.run([str(python), "-I", str(LAEUFER), befehl, "--code", str(code), "--ergebnis", str(ergebnis),
                        *argumente], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=zeit,
                       env=ohne_umgebung(arbeit), cwd=arbeit)
    except subprocess.TimeoutExpired:   # kein Absturz des Stands (eher Netz oder riesige Liste): kein Rückfall
        return {"ok": False, "fehler": f"Zeitüberschreitung nach {int(zeit)} s", "zeitueberschreitung": True}
    try:
        return json.loads(ergebnis.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "fehler": "Der Läufer ist abgestürzt (kein Ergebnis).", "absturz": True}


def testlauf(python: Path, code: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="proxy-test-") as tmp:
        arbeit = Path(tmp)
        r = laeufer(python, code, "selbsttest", ["--arbeit", str(arbeit)], TEST_ZEIT, arbeit)
    if not r.get("ok"):
        return {"ok": False, "funde": [r.get("fehler", "Testlauf gescheitert")]}
    return {"ok": not r["funde"], "funde": r["funde"], "pdf": r.get("pdf"), "python": r.get("python")}


# --- Verwaltung -----------------------------------------------------------------------------------------------

class Staende:
    """Hält den aktiven und den vorherigen Stand. `sperre` teilt sie mit den Aufträgen: Umschalten nur, wenn
    gerade kein Auftrag läuft."""

    def __init__(self, wurzel: Path, quelle=None, python: str = sys.executable,
                 einrichten=None, testen=testlauf):
        self.wurzel = Path(wurzel).resolve()
        self.quelle = quelle or GitHub()
        self.python = python
        self._installieren = einrichten or installieren
        self._testen = testen
        # Auftrag ↔ Umschalten; wiedereintrittsfähig, weil ein Auftrag bei einem Absturz selbst zurückschaltet
        self.sperre = threading.RLock()
        self._suche = threading.Lock()        # nur eine Suche zur Zeit
        self._zustandsperre = threading.Lock()   # stand.json lesen, ändern, schreiben
        self.sucht = False

    # Zustand
    @property
    def _datei(self) -> Path:
        return self.wurzel / "stand.json"

    def zustand(self) -> dict:
        try:
            z = json.loads(self._datei.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            z = {}
        z.setdefault("aktiv", None)
        z.setdefault("vorher", None)
        z.setdefault("fehlgeschlagen", {})
        return z

    def _speichern(self, z: dict) -> None:
        self.wurzel.mkdir(parents=True, exist_ok=True)
        teil = self._datei.with_suffix(f".{threading.get_ident()}.teil")
        teil.write_text(json.dumps(z, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(teil, self._datei)

    def _aendern(self, aenderung) -> dict:
        """Lesen, ändern, schreiben unter einer kurzen Sperre (nie über eine Netzanfrage hinweg)."""
        with self._zustandsperre:
            z = self.zustand()
            aenderung(z)
            self._speichern(z)
            return z

    def ordner(self, tag: str) -> Path:
        if version_von(tag) is None:
            raise Standfehler(f"Ungültiger Stand: {tag}")
        return self.wurzel / "staende" / tag

    def stand(self, tag: str | None = None) -> tuple[str, Path, Path] | None:
        """(Tag, Code, Python) des aktiven oder genannten Stands, None ohne Stand."""
        tag = tag or self.zustand()["aktiv"]
        if not tag:
            return None
        o = self.ordner(tag)
        py = venv_python(o / "venv")
        return (tag, o / "code", py) if py.exists() else None

    def anzeige(self) -> dict:
        z = self.zustand()
        info = {}
        if z["aktiv"]:
            try:
                info = json.loads((self.ordner(z["aktiv"]) / "stand.json").read_text(encoding="utf-8"))
            except (OSError, ValueError, Standfehler):
                info = {}
        return {"aktiv": z["aktiv"], "vorher": z["vorher"], "gesucht": z.get("gesucht"), "meldung": z.get("meldung"),
                "neuester": z.get("neuester"), "sucht": self.sucht, "eingerichtet": info.get("eingerichtet"),
                "commit": (info.get("sha") or "")[:7] or None, "quelle": f"https://github.com/{REPO}"}

    # Suchen und einrichten
    def suchen(self, erzwingen: bool = False) -> dict:
        """Neuester Tag; neu einrichten, wenn er nicht aktiv ist. Ein gescheiterter Tag wird nur auf Knopfdruck
        (`erzwingen`) noch einmal versucht."""
        if not self._suche.acquire(blocking=False):
            return self.anzeige()
        self.sucht = True
        try:
            try:
                tags = [(t, s) for t, s in self.quelle.tags() if version_von(t) is not None]
            except (OSError, ValueError, KeyError, TypeError, Standfehler) as f:
                self._aendern(lambda z: z.update(meldung=f"Suche nach neuem Stand gescheitert: {f}"))
                return self.anzeige()
            jetzt = time.strftime("%Y-%m-%dT%H:%M:%S")
            if not tags:
                self._aendern(lambda z: z.update(gesucht=jetzt, meldung="Im Original-Repository gibt es keinen "
                                                                        "Release-Stand."))
                return self.anzeige()
            tag, sha = max(tags, key=lambda t: version_von(t[0]))
            einrichten = []

            def pruefen(z: dict) -> None:
                z.update(gesucht=jetzt, neuester=tag)
                aktiv = version_von(z["aktiv"]) if z["aktiv"] else None
                if tag == z["aktiv"]:
                    z["meldung"] = None
                elif aktiv is not None and version_von(tag) < aktiv:
                    # nie still auf einen älteren Stand (z. B. wenn ein Tag im Original gelöscht wurde)
                    z["meldung"] = f"Der neueste Release im Original ({tag}) ist älter als der aktive Stand."
                elif tag not in z["fehlgeschlagen"] or erzwingen:
                    einrichten.append(True)

            self._aendern(pruefen)   # Zustand frisch gelesen: ein „Zurück“ während der Netzanfrage bleibt erhalten
            if einrichten:
                self.einrichten(tag, sha)
        finally:
            self.sucht = False
            self._suche.release()
        return self.anzeige()

    def einrichten(self, tag: str, sha: str) -> bool:
        ziel = self.ordner(tag)
        neu = ziel.with_name(tag + ".neu")
        shutil.rmtree(neu, ignore_errors=True)
        neu.mkdir(parents=True)
        log.info("Proxy-Druck: richte Silhouette Card Maker %s ein …", tag)
        beginn = time.monotonic()
        try:
            menge = code_laden(self.quelle, sha, neu / "code")
            bedarf = python_passt((neu / "code" / "pyproject.toml").read_text(encoding="utf-8")) \
                if (neu / "code" / "pyproject.toml").exists() else None
            if bedarf:
                raise Standfehler(f"Stand {tag} braucht Python {bedarf}; das Add-on hat "
                                  f"{sys.version_info[0]}.{sys.version_info[1]}. Bitte das Add-on aktualisieren.")
            self._installieren(neu / "code", neu / "venv", self.python, neu / "installation.log")
            test = self._testen(venv_python(neu / "venv"), neu / "code")
            if test.get("ok"):
                vergleich = self._vergleich(test)
                if vergleich:
                    test = {**test, "ok": False, "funde": [vergleich]}
            if not test.get("ok"):
                raise Standfehler("Testlauf nicht bestanden: " + "; ".join(test.get("funde") or ["unbekannt"]))
        except (OSError, subprocess.SubprocessError, Standfehler, ValueError) as f:
            shutil.rmtree(neu, ignore_errors=True)
            meldung = f"Neuer Stand {tag} nicht übernommen: {str(f)[:400]}"
            self._aendern(lambda z: (z["fehlgeschlagen"].__setitem__(tag, str(f)[:400]), z.update(meldung=meldung)))
            log.warning("Proxy-Druck: %s", meldung)
            return False
        (neu / "stand.json").write_text(json.dumps({
            "tag": tag, "sha": sha, "eingerichtet": time.strftime("%Y-%m-%dT%H:%M:%S"), "test": test,
            "dateien": menge["dateien"], "bytes": menge["bytes"], "sekunden": round(time.monotonic() - beginn)},
            ensure_ascii=False, indent=1), encoding="utf-8")
        with self.sperre:   # wartet, bis ein laufender Auftrag fertig ist
            shutil.rmtree(ziel, ignore_errors=True)
            os.replace(neu, ziel)

            def umschalten(z: dict) -> None:
                if z["aktiv"] != tag:
                    z["vorher"], z["aktiv"] = z["aktiv"], tag
                z["fehlgeschlagen"].pop(tag, None)
                z["meldung"] = None

            self._aendern(umschalten)
        self.aufraeumen()
        log.info("Proxy-Druck: Silhouette Card Maker %s aktiv (%d s).", tag, round(time.monotonic() - beginn))
        return True

    def _vergleich(self, test: dict) -> str | None:
        """Der neue Stand muss die Testbögen wie der laufende bauen (gleiche Seitenzahl und Seitengröße)."""
        z = self.zustand()
        if not z["aktiv"]:
            return None
        try:
            alt = json.loads((self.ordner(z["aktiv"]) / "stand.json").read_text(encoding="utf-8"))["test"]["pdf"]
        except (OSError, ValueError, KeyError, TypeError, Standfehler):
            return None
        neu = test.get("pdf") or {}
        if alt.get("seiten") != neu.get("seiten") or abs((alt.get("breite_pt") or 0) - (neu.get("breite_pt") or 0)) > 1 \
                or abs((alt.get("hoehe_pt") or 0) - (neu.get("hoehe_pt") or 0)) > 1:
            return (f"Testbögen anders als mit {z['aktiv']}: {neu.get('seiten')} statt {alt.get('seiten')} Seiten, "
                    f"{neu.get('breite_pt')} × {neu.get('hoehe_pt')} pt")
        return None

    def aufraeumen(self) -> None:
        """Nur aktiver und vorheriger Stand bleiben, dazu nichts Halbfertiges."""
        z = self.zustand()
        behalten = {z["aktiv"], z["vorher"]} - {None}
        basis = self.wurzel / "staende"
        if not basis.is_dir():
            return
        for o in basis.iterdir():
            if o.name not in behalten and not (self._suche.locked() and o.name.endswith(".neu")):
                shutil.rmtree(o, ignore_errors=True)

    def zurueck(self, grund: str | None = None, warten: float = 5) -> bool:
        """Vorherigen Stand verwenden (Knopf oder Absturz eines Auftrags). Der verlassene Stand gilt als
        gescheitert, damit ihn die tägliche Suche nicht sofort wieder einrichtet. Läuft gerade ein Auftrag (der
        die Sperre hält), wartet der Knopf höchstens `warten` Sekunden und meldet dann `Standfehler`."""
        if not self.sperre.acquire(timeout=warten):
            raise Standfehler("Gerade läuft ein Proxy-Auftrag oder ein Standwechsel. Bitte gleich noch einmal.")
        try:
            erfolg = []

            def tauschen(z: dict) -> None:
                if not z["vorher"] or self.stand(z["vorher"]) is None:
                    return
                verlassen = z["aktiv"]
                z["aktiv"], z["vorher"] = z["vorher"], verlassen
                if verlassen:
                    z["fehlgeschlagen"][verlassen] = grund or "Vom Nutzer zurückgestellt."
                z["meldung"] = (f"Zurück auf {z['aktiv']}" + (f" ({grund})" if grund else "") + ".")
                erfolg.append(True)

            self._aendern(tauschen)
        finally:
            self.sperre.release()
        return bool(erfolg)


class Proxyabgleich(threading.Thread):
    """Thread im Add-on: sucht beim Start und danach alle 24 h; `anstossen()` sucht sofort (Knopf)."""

    def __init__(self, staende: Staende, abstand: float = SUCHABSTAND):
        super().__init__(name="Proxyabgleich", daemon=True)
        self.staende, self.abstand = staende, abstand
        self._jetzt = threading.Event()
        self._erzwingen = False

    def anstossen(self) -> None:
        self._erzwingen = True
        self._jetzt.set()

    def run(self) -> None:
        while True:
            erzwingen, self._erzwingen = self._erzwingen, False
            try:
                self.staende.suchen(erzwingen=erzwingen)
            except Exception:   # der Thread darf nie sterben
                log.exception("Proxy-Druck: Suche nach neuem Stand gescheitert")
            self._jetzt.wait(self.abstand)
            self._jetzt.clear()
