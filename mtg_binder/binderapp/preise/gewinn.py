"""Gewinn und Verlust aus Kaufpreisen (Paket 8 der Arbeitsliste, 10.11.2026; ab Datenbankversion 11).

Kaufpreis je Karte (`karte.kaufpreis`, Stückpreis wie die ManaBox-Spalte „Purchase price“) gegen den heutigen
Wert in der gewählten Preisart (Foil-Karten mit Foil-Preis), je Karte, Kiste, Binder, Deck und gesamt.

Antworten des Nutzers (10.11.2026): Kaufpreis in der App eintragbar (einzeln und für eine Auswahl, jede Änderung
ein Schritt mit Rückgängig); **nur Euro zählt**. Wie bei den eigenen Secret Lairs (`secretlair.drops._summe`)
zählen Gewinn und Prozent nur über Karten mit beiden Angaben. Sichtbar ausgenommen und gezählt werden:
  - `ohne_kaufpreis`: Karten ohne Kaufpreis,
  - `fremdwaehrung`: Karten mit Kaufpreis in einer anderen Währung,
  - `ohne_preis`: Karten mit Kaufpreis in Euro, aber ohne heutigen Preis.
Ein Kaufpreis ohne Währung gilt als Euro (so war es schon im Dashboard; der Import aus ManaBox setzt die
Währung immer, die App schreibt „EUR“). Ein heutiger Preis 0 heißt in der Preisdatei „keine Daten“ und zählt als
„ohne Preis“ (wie in der Kaufliste), sonst erschiene jede solche Karte als Totalverlust.
"""
from __future__ import annotations

from binderapp.db import schritte
from binderapp.db.datenbank import version
from binderapp.db.seiten import Fehler
from binderapp.preise import werte

AB_VERSION = 11
WAEHRUNG = "EUR"
PREIS_MAX = 100000.0
ORT_DA = "(k.kiste_id IS NOT NULL OR k.tasche_id IS NOT NULL OR k.deck_id IS NOT NULL)"

EURO = f"(COALESCE(UPPER(k.kaufpreis_waehrung), '{WAEHRUNG}') = '{WAEHRUNG}')"

# {preis} ist der Preisausdruck der Preisart (nur aus werte.PREISARTEN); alle Werte als Parameter.
SQL_BILANZ = """
SELECT {gruppe} AS gruppe,
       SUM(CASE WHEN k.kaufpreis IS NOT NULL AND {euro} AND {heute} IS NOT NULL THEN k.anzahl * k.kaufpreis END) AS kauf,
       SUM(CASE WHEN k.kaufpreis IS NOT NULL AND {euro} AND {heute} IS NOT NULL THEN k.anzahl * {heute} END) AS wert,
       SUM(CASE WHEN k.kaufpreis IS NOT NULL AND {euro} AND {heute} IS NOT NULL THEN k.anzahl ELSE 0 END) AS karten,
       SUM(CASE WHEN k.kaufpreis IS NULL THEN k.anzahl ELSE 0 END) AS ohne_kaufpreis,
       SUM(CASE WHEN k.kaufpreis IS NOT NULL AND NOT {euro} THEN k.anzahl ELSE 0 END) AS fremdwaehrung,
       SUM(CASE WHEN k.kaufpreis IS NOT NULL AND {euro} AND {heute} IS NULL THEN k.anzahl ELSE 0 END) AS ohne_preis,
       SUM(k.anzahl) AS gesamt
FROM karte k
{verbindung}
LEFT JOIN kartendaten d ON d.scryfall_id = k.scryfall_id
LEFT JOIN preis p ON p.cardmarket_id = d.cardmarket_id AND p.tag = ?
WHERE k.nutzer_id = ? {bedingung}
GROUP BY 1
"""

ARTEN = {
    "binder": ("s.binder_id", "JOIN tasche t ON t.id = k.tasche_id JOIN seite s ON s.id = t.seite_id", ""),
    "kiste": ("k.kiste_id", "", "AND k.kiste_id IS NOT NULL"),
    "deck": ("k.deck_id", "", "AND k.deck_id IS NOT NULL"),
    "gesamt": ("1", "", f"AND {ORT_DA}"),
}


def aktiv(con) -> bool:
    return version(con) >= AB_VERSION


def _rund(x):
    return None if x is None else round(x, 2)


def _eintrag(z) -> dict:
    """Bilanz einer Gruppe. `kauf`, `wert`, `gewinn`, `prozent` nur über Karten, die zählen (sonst None)."""
    karten = z["karten"] or 0
    kauf = z["kauf"] if karten else None
    wert = z["wert"] if karten else None
    gewinn = None if kauf is None else round(wert - kauf, 2)
    return {
        "kauf": _rund(kauf), "wert": _rund(wert), "gewinn": gewinn,
        "prozent": round(gewinn / kauf * 100, 1) if gewinn is not None and kauf else None,
        "karten": karten,
        "ohne_kaufpreis": z["ohne_kaufpreis"] or 0, "fremdwaehrung": z["fremdwaehrung"] or 0,
        "ohne_preis": z["ohne_preis"] or 0, "gesamt": z["gesamt"] or 0,
        # Karten mit irgendeinem Kaufpreis: ohne solche Karten zeigt die Oberfläche nichts an
        "mit_kaufpreis": (z["gesamt"] or 0) - (z["ohne_kaufpreis"] or 0),
    }


def _sql(art: str, preisart: str) -> str:
    gruppe, verbindung, bedingung = ARTEN[art]
    heute = f"NULLIF({werte.preis_ausdruck(preisart)}, 0)"
    return SQL_BILANZ.format(gruppe=gruppe, verbindung=verbindung, bedingung=bedingung, euro=EURO, heute=heute)


def je(con, nutzer: int, art: str, preisart: str | None = None) -> dict[int, dict]:
    """Bilanz je Binder, Kiste oder Deck (`art`): {id: Eintrag}. Vor Version 11 leer."""
    if art not in ("binder", "kiste", "deck"):
        raise ValueError(art)
    if not aktiv(con):
        return {}
    preisart = werte.aufloesen(con, preisart, nutzer)
    return {z["gruppe"]: _eintrag(z) for z in con.execute(_sql(art, preisart), (werte.neuester_tag(con), nutzer))}


def gesamt(con, nutzer: int, preisart: str | None = None) -> dict | None:
    """Bilanz der ganzen Sammlung (Binder, Kisten, Decks). Vor Version 11 None."""
    if not aktiv(con):
        return None
    preisart = werte.aufloesen(con, preisart, nutzer)
    z = con.execute(_sql("gesamt", preisart), (werte.neuester_tag(con), nutzer)).fetchone()
    if z is None:
        return _eintrag({"kauf": None, "wert": None, "karten": 0, "ohne_kaufpreis": 0, "fremdwaehrung": 0,
                         "ohne_preis": 0, "gesamt": 0})
    return _eintrag(z)


def karte(con, k, preis: float | None) -> dict | None:
    """Bilanz einer Karte aus ihrer Zeile `k` (mit kaufpreis, kaufpreis_waehrung, anzahl) und dem heutigen
    Stückpreis der gewählten Preisart. `zaehlt` False mit `grund`, wenn sie nicht mitzählt."""
    if "kaufpreis" not in k.keys():
        return None
    kauf, waehrung, anzahl = k["kaufpreis"], k["kaufpreis_waehrung"], k["anzahl"] or 1
    eintrag = {"kaufpreis": kauf, "waehrung": waehrung or (WAEHRUNG if kauf is not None else None),
               "anzahl": anzahl, "heute": preis, "zaehlt": False, "grund": None,
               "kauf": None, "wert": None, "gewinn": None, "prozent": None}
    if kauf is None:
        eintrag["grund"] = "ohne_kaufpreis"
    elif (waehrung or WAEHRUNG).upper() != WAEHRUNG:
        eintrag["grund"] = "fremdwaehrung"
    elif not preis:
        eintrag["grund"] = "ohne_preis"
    else:
        gewinn = round((preis - kauf) * anzahl, 2)
        eintrag.update(zaehlt=True, kauf=round(kauf * anzahl, 2), wert=round(preis * anzahl, 2), gewinn=gewinn,
                       prozent=round(gewinn / (kauf * anzahl) * 100, 1) if kauf else None)
    return eintrag


# ------------------------------------------------------------------ Schreiben (Transaktion des Aufrufers)


def preis_pruefen(wert) -> float | None:
    """Kaufpreis aus der Anfrage: Zahl ab 0 (Euro, Stückpreis) oder None zum Entfernen."""
    if wert is None:
        return None
    if isinstance(wert, bool) or not isinstance(wert, (int, float)) or not 0 <= wert <= PREIS_MAX:
        raise Fehler(f"Der Kaufpreis muss eine Zahl von 0 bis {PREIS_MAX:,.0f} sein (Euro je Karte).".replace(",", "."))
    return round(float(wert), 2)


def setzen(con, nutzer: int, ids: list[int], kaufpreis: float | None) -> dict:
    """Kaufpreis (Euro je Karte) für alle Karten `ids` setzen oder mit None entfernen, als ein Schritt.
    Antwort: {schritt (None ohne Änderung), posten, karten, unveraendert}."""
    if not aktiv(con):
        raise Fehler("Kaufpreise gibt es erst ab Datenbankversion 11.", 409)
    felder = ({"kaufpreis": None, "kaufpreis_waehrung": None} if kaufpreis is None
              else {"kaufpreis": kaufpreis, "kaufpreis_waehrung": WAEHRUNG})
    schritt = schritte.neuer_schritt()
    posten = karten = unveraendert = fehlend = 0
    for kid in ids:
        z = con.execute("SELECT anzahl FROM karte WHERE id = ? AND nutzer_id = ?", (kid, nutzer)).fetchone()
        if z is None:
            fehlend += 1
            continue
        if schritte.aendern_genau(con, nutzer, "karte", kid, felder, schritt=schritt):
            posten += 1
            karten += z["anzahl"]
        else:
            unveraendert += 1
    if fehlend:
        raise Fehler(f"{fehlend} der gewählten Karten gibt es nicht (mehr). Bitte die Ansicht neu laden.", 404)
    return {"schritt": schritt if posten else None, "posten": posten, "karten": karten, "unveraendert": unveraendert,
            "kaufpreis": kaufpreis}
