"""Läufer für Silhouette Card Maker (SCM): läuft im venv eines Stands, nie im Server.

    python -I laeufer.py erkennen   --code STAND --liste LISTE.txt --ergebnis E.json
    python -I laeufer.py laden      --code STAND --liste LISTE.txt --format F --optionen JSON --vorne D --doppelt D
                                    [--cache ORDNER --cache-tage 30] --fortschritt P.json --ergebnis E.json
    python -I laeufer.py pdf        --code STAND --vorne D --hinten D --doppelt D --ausgabe X.pdf [--versatz JSON]
                                    --ergebnis E.json
    python -I laeufer.py kalibrierung --code STAND --arbeit ORDNER --ausgabe X.pdf [--versatz JSON] --ergebnis E.json
    python -I laeufer.py selbsttest --code STAND --arbeit ORDNER --ergebnis E.json

Eingebunden, nicht abgeschrieben: Formate liest `plugins.mtg.deck_formats.parse_deck`, Bilder holt
`plugins.mtg.scryfall.get_handle_card`, das PDF baut `create_pdf.py` über seine Kommandozeile. Dieses Skript
zeichnet nur auf, welche Zeile was ergeben hat, legt einen Bilder-Cache unter `requests` und schreibt das
Ergebnis als JSON. Es braucht die Standardbibliothek und die Pakete des Stands (Pillow nur im Selbsttest).
`-I`: weder die Umgebung noch der Ordner dieses Skripts kommen in den Suchpfad, nur der Stand (`--code`).
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import inspect
import io
import json
import os
import re
import runpy
import sys
import time
import traceback
from pathlib import Path

# Formate, die reiner Text sind und beim Lesen nichts aus dem Netz holen (url, cubecobra_csv, mpcfill_xml und
# scryfall_json laden schon beim Lesen oder rufen `handle_card` anders auf).
TEXTFORMATE = ("moxfield", "archidekt", "mtga", "mtgo", "deckstats", "simple")
BILD_MAX = 20 * 1024 * 1024
FEHLER_MAX = 300


class StandPasstNicht(RuntimeError):
    """Die Schnittstelle des Stands passt nicht mehr (Grund für den Rückfall auf den vorherigen Stand)."""


class Eingabefehler(ValueError):
    """Fehler in der Liste oder den Dateien des Nutzers (kein Grund für einen Rückfall)."""


# Fehlerarten, die auf einen kaputten oder veränderten Stand deuten, nicht auf Liste, Bilder oder Netz
STANDFEHLER = (ImportError, AttributeError, NameError, SyntaxError, TypeError, StandPasstNicht)


def _stand_laden(code: Path) -> None:
    sys.path.insert(0, str(code))
    os.chdir(code)   # wie beim Aufruf im Repository: relative Pfade des Stands gelten dort


def _schreiben(pfad: Path, daten: dict) -> None:
    teil = pfad.with_name(pfad.name + ".teil")
    teil.write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")
    os.replace(teil, pfad)


def _liste(pfad: Path) -> str:
    return pfad.read_text(encoding="utf-8")


def _format_wert(deck_formats, name: str):
    return deck_formats.DeckFormat(name)


def lesen(text: str, format_name: str) -> dict:
    """Liest die Liste mit dem Parser des Stands, ohne Netz: `handle_card` zeichnet nur auf."""
    from plugins.mtg import deck_formats
    karten = []

    def aufzeichnen(*args, **kwargs):
        werte = list(args) + [kwargs.get(k) for k in ("index", "name", "card_set", "card_collector_number",
                                                     "quantity") if k in kwargs]
        index, name, satz, nummer, anzahl = (werte + [None] * 5)[:5]
        karten.append({"index": index, "name": name or "", "set": satz or "", "nummer": str(nummer or ""),
                       "anzahl": int(anzahl or 1)})

    ausgabe = io.StringIO()
    with contextlib.redirect_stdout(ausgabe):
        deck_formats.parse_deck(text, _format_wert(deck_formats, format_name), aufzeichnen, "", "")
    zeilen = ausgabe.getvalue().splitlines()
    uebersprungen = [z[len('Skipping: "'):-1] for z in zeilen if z.startswith('Skipping: "')]
    return {"karten": karten, "uebersprungen": uebersprungen}


def erkennen(a) -> dict:
    _stand_laden(a.code)
    text = _liste(a.liste)
    formate = {}
    for name in TEXTFORMATE:
        try:
            formate[name] = lesen(text, name)
        except Exception as f:   # ein Parser, der an dieser Liste scheitert, zählt einfach nicht
            formate[name] = {"fehler": str(f)[:FEHLER_MAX]}
    return {"formate": formate}


# --- Bilder-Cache unter requests ------------------------------------------------------------------------------

def _ist_bild(url: str, params) -> bool:
    from urllib.parse import parse_qs, urlparse
    teile = urlparse(url)
    host = (teile.hostname or "").lower()
    if host.endswith(".scryfall.io"):
        return True
    if host != "api.scryfall.com":
        return False
    abfrage = parse_qs(teile.query)
    if isinstance(params, dict):
        abfrage.update({k: [str(v)] for k, v in params.items()})
    return abfrage.get("format") == ["image"]


def _schluessel(url: str, params) -> str:
    from urllib.parse import urlencode
    voll = url + ("?" + urlencode(sorted(params.items())) if isinstance(params, dict) and params else "")
    return hashlib.sha256(voll.encode("utf-8")).hexdigest()


def cache_einbauen(ordner: Path, tage: float, zaehler: dict) -> None:
    """Liefert Scryfall-Bilder aus `ordner`, solange sie jünger als `tage` sind; neue landen dort."""
    import requests
    from requests.models import Response
    ordner.mkdir(parents=True, exist_ok=True)
    original = requests.Session.request

    def mit_cache(self, method, url, *args, **kwargs):
        params = kwargs.get("params")
        if str(method).upper() != "GET" or not _ist_bild(str(url), params):
            return original(self, method, url, *args, **kwargs)
        datei = ordner / (_schluessel(str(url), params) + ".png")
        try:
            if time.time() - datei.stat().st_mtime < tage * 86400:
                antwort = Response()
                antwort.status_code, antwort.url, antwort.encoding = 200, str(url), None
                antwort._content = datei.read_bytes()
                antwort.headers["Content-Type"] = "image/png"
                zaehler["treffer"] += 1
                return antwort
        except OSError:
            pass
        antwort = original(self, method, url, *args, **kwargs)
        inhalt = antwort.content if antwort.status_code == 200 else b""
        if inhalt and len(inhalt) <= BILD_MAX and antwort.headers.get("Content-Type", "").startswith("image/"):
            teil = datei.with_name(datei.name + f".{os.getpid()}.teil")
            try:
                teil.write_bytes(inhalt)
                os.replace(teil, datei)
                zaehler["neu"] += 1
            except OSError:
                teil.unlink(missing_ok=True)
        return antwort

    requests.Session.request = mit_cache


def _handle_card(scryfall, optionen: dict, vorne: Path, doppelt: Path):
    """`get_handle_card` mit Schlüsselwörtern, wie `fetch.py` sie füllt; nur bekannte Namen."""
    werte = {
        "ignore_set_and_collector_number": bool(optionen.get("sets_ignorieren")),
        "prefer_older_sets": False, "prefer_sets": (), "ignore_sets": [],
        "prefer_showcase": False, "prefer_extra_art": bool(optionen.get("extra_art")),
        "prefer_ub": False, "ignore_ub": False, "prefer_langs": None, "tokens": False,
        "front_img_dir": str(vorne), "double_sided_dir": str(doppelt),
    }
    parameter = inspect.signature(scryfall.get_handle_card).parameters
    fehlt = [n for n, p in parameter.items() if n not in werte and p.default is inspect.Parameter.empty]
    if fehlt:
        raise StandPasstNicht("Der Stand erwartet unbekannte Angaben: " + ", ".join(fehlt))
    return scryfall.get_handle_card(**{n: v for n, v in werte.items() if n in parameter})


def laden(a) -> dict:
    _stand_laden(a.code)
    from plugins.mtg import deck_formats, scryfall
    optionen = json.loads(a.optionen)
    zaehler = {"treffer": 0, "neu": 0}
    if a.cache:
        cache_einbauen(a.cache, a.cache_tage, zaehler)
    a.vorne.mkdir(parents=True, exist_ok=True)
    a.doppelt.mkdir(parents=True, exist_ok=True)
    echt = _handle_card(scryfall, optionen, a.vorne, a.doppelt)
    text = _liste(a.liste)
    gelesen = lesen(text, a.format)["karten"]
    gesamt = len(gelesen)
    exemplare = sum(k["anzahl"] for k in gelesen)
    if a.hoechstens and exemplare > a.hoechstens:
        raise Eingabefehler(f"Die Liste hat {exemplare} Karten; höchstens {a.hoechstens} je PDF.")
    zeilen = []

    def aufzeichnen(*args, **kwargs):
        eintrag = {"index": args[0] if args else kwargs.get("index"), "name": args[1] if len(args) > 1 else ""}
        try:
            echt(*args, **kwargs)
            eintrag["ok"] = True
        except Exception as f:
            eintrag["ok"], eintrag["fehler"] = False, (str(f) or type(f).__name__)[:FEHLER_MAX]
            raise
        finally:
            zeilen.append(eintrag)
            _schreiben(a.fortschritt, {"fertig": len(zeilen), "gesamt": gesamt})
        return None

    ausgabe = io.StringIO()
    with contextlib.redirect_stdout(ausgabe):
        deck_formats.parse_deck(text, _format_wert(deck_formats, a.format), aufzeichnen, str(a.vorne), str(a.doppelt))
    return {"zeilen": zeilen, "cache": zaehler,
            "vorne": len(list(a.vorne.iterdir())), "doppelt": len(list(a.doppelt.iterdir()))}


# --- PDF ------------------------------------------------------------------------------------------------------

PDF_EINSTELLUNG = ["--paper_size", "a4", "--card_size", "standard", "--registration", "3"]
PAPIER = "a4"   # Kalibrierbogen passend zu PDF_EINSTELLUNG


def _skript(code: Path, name: str, argumente: list[str]) -> str:
    """Ein Skript des Stands wie über die Kommandozeile; gibt seine Ausgabe zurück."""
    alt = sys.argv
    sys.argv = [name, *argumente]
    ausgabe = io.StringIO()
    try:
        with contextlib.redirect_stdout(ausgabe):
            runpy.run_path(str(code / name), run_name="__main__")
    except SystemExit as e:   # click beendet mit sys.exit
        if e.code not in (0, None):
            raise RuntimeError(f"{name} endete mit {e.code}")
    finally:
        sys.argv = alt
    return ausgabe.getvalue()


def versatz_lesen(text: str | None) -> dict | None:
    """Druckversatz der Rückseiten in Pixeln bei 300 ppi (wie SCM), None ohne oder bei (0, 0, 0)."""
    if not text:
        return None
    v = json.loads(text)
    werte = {"x": int(v.get("x", 0)), "y": int(v.get("y", 0)), "winkel": float(v.get("winkel", 0.0))}
    return None if werte == {"x": 0, "y": 0, "winkel": 0.0} else werte


def _versatz_ablegen(ordner: Path, versatz: dict) -> None:
    """SCM liest den gespeicherten Versatz aus `data/offset_data.json` im aktuellen Ordner
    (`utilities.load_saved_offset`); der Ordner gehört dem Auftrag, nie dem Stand."""
    (ordner / "data").mkdir(parents=True, exist_ok=True)
    (ordner / "data" / "offset_data.json").write_text(json.dumps(
        {"x_offset": versatz["x"], "y_offset": versatz["y"], "angle_offset": versatz["winkel"]}), encoding="utf-8")
    os.chdir(ordner)


def pdf_bauen(code: Path, vorne: Path, hinten: Path, doppelt: Path, ausgabe: Path, versatz: dict | None = None) -> None:
    """`create_pdf.py` wie von Hand, nur mit festen Ordnern: A4, standard, 3-Punkt-Passermarken; mit Versatz
    zusätzlich `--load_offset` (verschiebt jede zweite Seite, also die Rückseiten)."""
    argumente = ["--front_dir_path", str(vorne), "--back_dir_path", str(hinten), "--double_sided_dir_path", str(doppelt),
                 "--output_path", str(ausgabe), *PDF_EINSTELLUNG]
    if versatz:
        _versatz_ablegen(ausgabe.parent / "versatz", versatz)
        argumente.append("--load_offset")
    ausgabe_text = _skript(code, "create_pdf.py", argumente)
    if not ausgabe.is_file():
        raise RuntimeError("create_pdf.py hat kein PDF geschrieben.")
    if versatz and "Loaded x offset" not in ausgabe_text:
        raise StandPasstNicht("Der Stand hat den Druckversatz nicht übernommen.")


def pdf_lesen(pfad: Path) -> dict:
    """Seitenzahl und Seitengröße (Punkt) eines von Pillow geschriebenen PDFs, ohne PDF-Bibliothek."""
    daten = pfad.read_bytes()
    seiten = len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", daten))
    box = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", daten)
    breite, hoehe = (float(box.group(1)), float(box.group(2))) if box else (0.0, 0.0)
    return {"seiten": seiten, "breite_pt": round(breite, 1), "hoehe_pt": round(hoehe, 1), "bytes": len(daten)}


def pdf(a) -> dict:
    _stand_laden(a.code)
    versatz = versatz_lesen(a.versatz)
    pdf_bauen(a.code, a.vorne, a.hinten, a.doppelt, a.ausgabe, versatz)
    return {**pdf_lesen(a.ausgabe), "versatz": versatz}


# --- Kalibrierbogen -------------------------------------------------------------------------------------------

def kalibrierung(a) -> dict:
    """Kalibrierbogen des Stands (`generate_calibration.py`) für A4; mit Versatz über `offset_pdf.py` verschoben,
    damit der abgelesene Wert zum gespeicherten dazuzählt (Anleitung von SCM, „Large offset“)."""
    _stand_laden(a.code)
    versatz = versatz_lesen(a.versatz)
    a.arbeit.mkdir(parents=True, exist_ok=True)
    (a.arbeit / "calibration").mkdir(exist_ok=True)
    os.chdir(a.arbeit)   # das Skript schreibt nach ./calibration
    _skript(a.code, "generate_calibration.py", [])
    bogen = a.arbeit / "calibration" / f"{PAPIER}-calibration.pdf"
    if not bogen.is_file():
        raise StandPasstNicht(f"generate_calibration.py hat keinen Bogen für {PAPIER} geschrieben.")
    if versatz:
        try:
            import pypdfium2  # noqa: F401  (braucht offset_pdf.py)
        except ImportError:   # kein Standfehler: ein älter eingerichteter Stand, der nächste Abgleich holt es nach
            raise RuntimeError("Diesem Stand fehlt pypdfium2 für einen Bogen mit Versatz. Bitte „Nach neuem Stand "
                               "suchen“ drücken, dann wird er neu eingerichtet.") from None
        _skript(a.code, "offset_pdf.py", ["--pdf_path", str(bogen), "--output_pdf_path", str(a.ausgabe),
                                          "-x", str(versatz["x"]), "-y", str(versatz["y"]), "-a", str(versatz["winkel"])])
        if not a.ausgabe.is_file():
            raise RuntimeError("offset_pdf.py hat kein PDF geschrieben.")
    else:
        a.ausgabe.write_bytes(bogen.read_bytes())
    return {**pdf_lesen(a.ausgabe), "versatz": versatz}


# --- Selbsttest (ohne Netz) -----------------------------------------------------------------------------------

BEISPIELE = {
    "moxfield": ("1 Brainstorm (MKC) 96\n2 Counterspell (CMM) 81\n", [("Brainstorm", "MKC", "96", 1)]),
    "mtgo": ("1 Brainstorm\n2 Counterspell\n", [("Brainstorm", "", "", 1)]),
    "mtga": ("1 Brainstorm (MKC) 96\n2 Counterspell (CMM) 81\n", [("Brainstorm", "MKC", "96", 1)]),
    "simple": ("Brainstorm\nCounterspell\n", [("Brainstorm", "", "", 1)]),
}
TEST_KARTEN = 10   # 9 einseitige und 1 doppelseitige: mehr als ein Bogen


def selbsttest(a) -> dict:
    _stand_laden(a.code)
    from PIL import Image, ImageDraw
    from plugins.mtg import scryfall
    funde = []
    for name, (text, erwartet) in BEISPIELE.items():
        karten = lesen(text, name)["karten"]
        erste = [(k["name"], k["set"].upper(), k["nummer"], k["anzahl"]) for k in karten[:1]]
        if len(karten) != 2 or erste != erwartet:
            funde.append(f"Format {name}: {erste} statt {erwartet}")
    try:
        _handle_card(scryfall, {}, a.arbeit / "v", a.arbeit / "d")
    except StandPasstNicht as f:
        funde.append(str(f))
    vorne, hinten, doppelt = a.arbeit / "vorne", a.arbeit / "hinten", a.arbeit / "doppelt"
    for ordner in (vorne, hinten, doppelt):
        ordner.mkdir(parents=True, exist_ok=True)

    def bild(pfad: Path, text: str, farbe) -> None:
        b = Image.new("RGB", (745, 1040), farbe)
        ImageDraw.Draw(b).text((60, 60), text, fill=(0, 0, 0))
        b.save(pfad)

    for i in range(1, TEST_KARTEN + 1):
        bild(vorne / f"{i:02d}test.png", f"Testkarte {i}", (200, 200 - i * 10, 160))
    bild(doppelt / f"{TEST_KARTEN:02d}test.png", "Zweite Seite", (160, 200, 220))
    bild(hinten / "rueckseite.png", "Testrückseite", (90, 90, 90))
    ausgabe = a.arbeit / "test.pdf"
    pdf_bauen(a.code, vorne, hinten, doppelt, ausgabe)
    werte = pdf_lesen(ausgabe)
    # Kalibrierung: Druckversatz im PDF und Bogen mit Versatz (offset_pdf.py braucht pypdfium2)
    mit_versatz = a.arbeit / "mit-versatz" / "test.pdf"
    mit_versatz.parent.mkdir()
    pdf_bauen(a.code, vorne, hinten, doppelt, mit_versatz, {"x": 3, "y": -2, "winkel": 0.2})
    if pdf_lesen(mit_versatz)["seiten"] != werte["seiten"]:
        funde.append("Mit Druckversatz hat das PDF eine andere Seitenzahl")
    try:
        bogen = kalibrierung(argparse.Namespace(code=a.code, arbeit=a.arbeit / "kalibrierung",
                                                ausgabe=a.arbeit / "kalibrierung.pdf",
                                                versatz=json.dumps({"x": 20, "y": -5, "winkel": 0})))
        if bogen["seiten"] != 2:
            funde.append(f"Kalibrierbogen mit {bogen['seiten']} statt 2 Seiten")
    except Exception as f:
        funde.append(f"Kalibrierbogen: {str(f)[:FEHLER_MAX]}")
    a4 = (abs(werte["breite_pt"] - 595.3) < 3 and abs(werte["hoehe_pt"] - 841.9) < 3) or \
         (abs(werte["breite_pt"] - 841.9) < 3 and abs(werte["hoehe_pt"] - 595.3) < 3)
    if not a4:
        funde.append(f"Seitengröße {werte['breite_pt']} × {werte['hoehe_pt']} pt ist nicht A4")
    if werte["seiten"] < 2 or werte["seiten"] % 2:
        funde.append(f"{werte['seiten']} Seiten (erwartet: Vorder- und Rückseiten im Wechsel)")
    return {"funde": funde, "pdf": werte, "python": list(sys.version_info[:3])}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("befehl", choices=("erkennen", "laden", "pdf", "kalibrierung", "selbsttest"))
    ap.add_argument("--versatz", default=None)
    ap.add_argument("--code", type=Path, required=True)
    ap.add_argument("--ergebnis", type=Path, required=True)
    ap.add_argument("--liste", type=Path)
    ap.add_argument("--format", choices=TEXTFORMATE)
    ap.add_argument("--optionen", default="{}")
    ap.add_argument("--vorne", type=Path)
    ap.add_argument("--hinten", type=Path)
    ap.add_argument("--doppelt", type=Path)
    ap.add_argument("--ausgabe", type=Path)
    ap.add_argument("--arbeit", type=Path)
    ap.add_argument("--cache", type=Path)
    ap.add_argument("--cache-tage", dest="cache_tage", type=float, default=30)
    ap.add_argument("--fortschritt", type=Path)
    ap.add_argument("--hoechstens", type=int, default=0)
    a = ap.parse_args(argv)
    for name in ("code", "ergebnis", "liste", "vorne", "hinten", "doppelt", "ausgabe", "arbeit", "cache",
                 "fortschritt"):
        if getattr(a, name) is not None:
            setattr(a, name, getattr(a, name).resolve())
    befehl = {"erkennen": erkennen, "laden": laden, "pdf": pdf, "kalibrierung": kalibrierung,
              "selbsttest": selbsttest}[a.befehl]
    try:
        ergebnis = {"ok": True, **befehl(a)}
    except Exception as f:
        ergebnis = {"ok": False, "fehler": (str(f) or type(f).__name__)[:FEHLER_MAX],
                    "standfehler": isinstance(f, STANDFEHLER), "verlauf": traceback.format_exc()[-2000:]}
    _schreiben(a.ergebnis, ergebnis)
    return 0 if ergebnis["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
