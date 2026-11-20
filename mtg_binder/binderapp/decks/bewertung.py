"""Commander-Bewertung eines Decks: Legalität und Mindest-Bracket.

Reine Funktionen ohne Datenbank und ohne Ein-/Ausgabe. Die Eingaben (Deckzeilen,
Regeln je Oracle-Id, Kombos) liefert der Aufrufer.
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Bracket-Regeln als Daten: eine Änderung der Regeln ist eine Zeile.
# ---------------------------------------------------------------------------
BRACKET_REGELN: dict[str, Any] = {
    # Game Changer: (von, bis, Bracket); bis=None heißt „und mehr“
    "game_changer": [(1, 3, 3), (4, None, 4)],
    "landzerstoerung": 4,
    # Extrazüge: genau 1 nur Hinweis, ab dieser Zahl Bracket
    "extrazug_ab": 2,
    "extrazug_bracket": 3,
    # Kombos
    "kombo_frueh_tempo": 4,
    "kombo_frueh_bracket": 4,
    "kombo_zwei_karten_bracket": 3,
    "kombo_sonder_bracket": 4,  # Extrazug, Landzerstörung oder alle Gegner
}

# Bracket -> angezeigte Spanne (1/2 und 4/5 unterscheidet die Absicht)
BRACKET_SPANNEN: dict[int, list[int]] = {1: [1, 2], 2: [2, 2], 3: [3, 3], 4: [4, 5], 5: [4, 5]}

KENNZEICHEN_ANZEIGE = ("game_changer", "extrazug", "landzerstoerung")


def _befund(art: str, text: str, zeilen: list[int] | None = None) -> dict:
    return {"art": art, "text": text, "zeilen": list(zeilen or [])}


def _namen(name: str) -> set[str]:
    """Kleingeschriebener Name und erste Seite von „X // Y“."""
    voll = (name or "").strip().lower()
    return {voll, voll.split(" // ")[0].strip()}


def _merkmal_wert(merkmale: list[str], praefix: str) -> list[str]:
    return [m[len(praefix):] for m in merkmale if m.startswith(praefix)]


def _partner_mit(merkmale: list[str], andere: dict) -> bool:
    """Verweist ein „partner_mit:<Name>“ auf die andere Karte?"""
    ziele = {z.strip().lower() for z in _merkmal_wert(merkmale, "partner_mit:")}
    return bool(ziele & _namen(andere["name"]))


def _paar_gueltig(a: dict, b: dict) -> bool:
    """Prüft, ob zwei Commander-Zeilen (beide mit Regel) ein gültiges Paar sind."""
    ma, mb = a["_regel"].get("merkmale", []), b["_regel"].get("merkmale", [])
    art = (
        ("partner" in ma and "partner" in mb)
        or (_partner_mit(ma, b) and _partner_mit(mb, a))
        or bool(set(_merkmal_wert(ma, "partner_gruppe:")) & set(_merkmal_wert(mb, "partner_gruppe:")))
        or ("freunde" in ma and "freunde" in mb)
        or ("background_waehlen" in ma and "background" in mb)
        or ("background_waehlen" in mb and "background" in ma)
        or ("doktor_begleiter" in ma and "doktor" in mb)
        or ("doktor_begleiter" in mb and "doktor" in ma)
    )
    if not art:
        return False
    # Jeder muss Commander sein können, außer der Background
    return all("kann_commander" in m or "background" in m for m in (ma, mb))


def _hoechstens(merkmale: list[str]) -> int | None:
    for w in _merkmal_wert(merkmale, "hoechstens:"):
        try:
            return int(w)
        except ValueError:
            continue
    return None


def legalitaet(zeilen: list[dict], regeln: dict[str, dict]) -> dict:
    """Prüft ein Deck auf Commander-Legalität.

    Ergebnis: legal, Kartenzahl, Fehler, Hinweise und Kennzeichen je Zeile.
    """
    zl = []
    for z in zeilen:
        d = dict(z)
        oid = d.get("oracle_id")
        d["_regel"] = regeln.get(oid) if oid else None
        zl.append(d)

    fehler: list[dict] = []
    hinweise: list[dict] = []
    je_zeile: dict[Any, list[str]] = {}

    def kennz(zid: Any, k: str) -> None:
        lst = je_zeile.setdefault(zid, [])
        if k not in lst:
            lst.append(k)

    gesamt = sum(int(z.get("anzahl") or 0) for z in zl)

    # Zeilen ohne Katalog
    ohne = [z for z in zl if z["_regel"] is None]
    if ohne:
        for z in ohne:
            kennz(z["id"], "ohne_katalog")
        hinweise.append(_befund("ohne_katalog", f"{len(ohne)} Karte(n) ohne Katalogdaten, nicht geprüft",
                                [z["id"] for z in ohne]))

    # Kartenzahl
    if gesamt != 100:
        hinweise.append(_befund("kartenzahl", f"{gesamt} statt 100 Karten"))

    # Commander
    cmd = [z for z in zl if z.get("commander")]
    cmd_ids = [z["id"] for z in cmd]
    cmd_summe = sum(int(z.get("anzahl") or 0) for z in cmd)
    if not cmd:
        fehler.append(_befund("kein_commander", "Kein Commander festgelegt"))
    else:
        grund = None
        if cmd_summe > 2:
            grund = f"{cmd_summe} Commander, höchstens 2 erlaubt"
        elif cmd_summe == 1:
            z = cmd[0]
            if z["_regel"] is not None and "kann_commander" not in z["_regel"].get("merkmale", []):
                grund = f"{z['name']} kann nicht Commander sein"
        elif cmd_summe == 2:
            if len(cmd) == 1:
                grund = f"Zwei Exemplare von {cmd[0]['name']} sind kein gültiges Paar"
            elif all(z["_regel"] is not None for z in cmd) and not _paar_gueltig(cmd[0], cmd[1]):
                grund = f"{cmd[0]['name']} und {cmd[1]['name']} sind kein gültiges Commander-Paar"
        if grund:
            fehler.append(_befund("commander_unzulaessig", grund, cmd_ids))
            for i in cmd_ids:
                kennz(i, "commander_unzulaessig")

    # Gebannt, nicht legal
    for status, art, text in (("banned", "gebannt", "gebannt"),
                              ("not_legal", "nicht_legal", "nicht Commander-legal")):
        ids = [z["id"] for z in zl if z["_regel"] is not None and z["_regel"].get("commander") == status]
        if ids:
            namen = ", ".join(z["name"] for z in zl if z["id"] in ids)
            fehler.append(_befund(art, f"{namen}: {text}", ids))
            for i in ids:
                kennz(i, art)

    # Farbidentität (nur, wenn alle Commander eine Regel haben)
    if cmd and all(z["_regel"] is not None for z in cmd):
        erlaubt: set[str] = set()
        for z in cmd:
            erlaubt |= set(z["_regel"].get("farbidentitaet") or "")
        ids = [z["id"] for z in zl
               if z["_regel"] is not None and not set(z["_regel"].get("farbidentitaet") or "") <= erlaubt]
        if ids:
            namen = ", ".join(z["name"] for z in zl if z["id"] in ids)
            fehler.append(_befund("farbidentitaet", f"Außerhalb der Farbidentität: {namen}", ids))
            for i in ids:
                kennz(i, "farbidentitaet")

    # Singleton (nach Oracle-Id, ohne Katalog nach Name)
    gruppen: dict[str, list[dict]] = {}
    for z in zl:
        schluessel = z["oracle_id"] if z["_regel"] is not None else "name:" + (z.get("name") or "").strip().lower()
        gruppen.setdefault(schluessel, []).append(z)
    for g in gruppen.values():
        summe = sum(int(z.get("anzahl") or 0) for z in g)
        merk: list[str] = []
        for z in g:
            if z["_regel"] is not None:
                merk = z["_regel"].get("merkmale", [])
                break
        if any(z.get("basic") for z in g) or "beliebig" in merk:
            continue
        grenze = _hoechstens(merk)
        if grenze is None:
            grenze = 1
        if summe > grenze:
            ids = [z["id"] for z in g]
            fehler.append(_befund("singleton", f"{g[0]['name']}: {summe}-mal im Deck, erlaubt sind {grenze}", ids))
            for i in ids:
                kennz(i, "singleton")

    # Anzeige-Kennzeichen aus der Regel
    for z in zl:
        r = z["_regel"]
        if r:
            for k in KENNZEICHEN_ANZEIGE:
                if r.get(k):
                    kennz(z["id"], k)

    return {"legal": not fehler, "karten": gesamt, "fehler": fehler, "hinweise": hinweise, "je_zeile": je_zeile}


def _gc_bracket(anzahl: int) -> int:
    for von, bis, b in BRACKET_REGELN["game_changer"]:
        if anzahl >= von and (bis is None or anzahl <= bis):
            return b
    return 1


def mindest_bracket(zeilen: list[dict], regeln: dict[str, dict], kombos: list[dict] | None) -> dict:
    """Bestimmt das Mindest-Bracket aus Game Changern, Landzerstörung, Extrazügen und Kombos."""
    gruende: list[dict] = []
    hinweise: list[dict] = []

    def regel(z: dict) -> dict:
        oid = z.get("oracle_id")
        return (regeln.get(oid) if oid else None) or {}

    def grund(art: str, text: str, bracket: int, ids: list[int]) -> None:
        g = _befund(art, text, ids)
        g["bracket"] = bracket
        gruende.append(g)

    # Game Changer
    gc = [z for z in zeilen if regel(z).get("game_changer")]
    n_gc = sum(int(z.get("anzahl") or 0) for z in gc)
    if n_gc:
        grund("game_changer", f"{n_gc} Game Changer", _gc_bracket(n_gc), [z["id"] for z in gc])

    # Massen-Landzerstörung
    lz = [z for z in zeilen if regel(z).get("landzerstoerung")]
    if lz:
        grund("landzerstoerung", "Massen-Landzerstörung: " + ", ".join(z["name"] for z in lz),
              BRACKET_REGELN["landzerstoerung"], [z["id"] for z in lz])

    # Extrazüge
    ez = [z for z in zeilen if regel(z).get("extrazug")]
    n_ez = sum(int(z.get("anzahl") or 0) for z in ez)
    ez_ids = [z["id"] for z in ez]
    if n_ez >= BRACKET_REGELN["extrazug_ab"]:
        grund("extrazug", f"{n_ez} Extrazüge, können hintereinander kommen",
              BRACKET_REGELN["extrazug_bracket"], ez_ids)
    elif n_ez == 1:
        hinweise.append(_befund("extrazug", "1 Extrazug", ez_ids))

    # Kombos
    if kombos is None:
        hinweise.append(_befund("kombos", "Kombos nicht geprüft"))
    else:
        for k in kombos:
            karten = k.get("karten", [])
            gesucht: set[str] = set()
            for n in karten:
                gesucht |= _namen(n)
            ids = [z["id"] for z in zeilen if _namen(z.get("name", "")) & gesucht]
            namen = " + ".join(karten)
            vorher = len(gruende)
            frueh = False
            if k.get("relevant") and k.get("zwei_karten"):
                if int(k.get("tempo") or 0) >= BRACKET_REGELN["kombo_frueh_tempo"]:
                    frueh = True
                    grund("kombo", f"frühe Zwei-Karten-Kombo: {namen}",
                          BRACKET_REGELN["kombo_frueh_bracket"], ids)
                else:
                    grund("kombo", f"Zwei-Karten-Kombo: {namen}",
                          BRACKET_REGELN["kombo_zwei_karten_bracket"], ids)
            if not frueh and (k.get("extrazug") or k.get("landzerstoerung") or k.get("alle_gegner")):
                grund("kombo", f"Kombo mit Extrazug, Landzerstörung oder gegen alle Gegner: {namen}",
                      BRACKET_REGELN["kombo_sonder_bracket"], ids)
            if len(gruende) == vorher and k.get("fast_zwei_karten") \
                    and (k.get("relevant") or k.get("grenzwertig")):
                hinweise.append(_befund("kombo", f"Fast eine Zwei-Karten-Kombo: {namen}", ids))
            if k.get("sperre"):
                hinweise.append(_befund("kombo", f"Kombo mit Sperre: {namen}", ids))

    bracket = max([1] + [g["bracket"] for g in gruende])
    hinweise.append(_befund(
        "absicht",
        "Bracket 1/2 und 4/5 unterscheidet laut offiziellem System die Absicht der Spielrunde, nicht die Kartenliste.",
    ))
    return {"bracket": bracket, "spanne": list(BRACKET_SPANNEN.get(bracket, [bracket, bracket])),
            "gruende": gruende, "hinweise": hinweise}
