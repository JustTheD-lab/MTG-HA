"""Dashboard (Wunsch des Nutzers, 06.11.2026): alle Kennzahlen der Sammlung auf einer Seite, nur lesend.

    GET /api/dashboard   Kopf (Wert, Veränderung, Zahlen), Verlauf, teuerste Karten, Gewinner und Verlierer je Abstand,
                         Verteilungen (Karten und Euro je Farbe, Seltenheit, Typ, Sprache, Manawert, Preisbereich,
                         Erkennung, Set), Binder, Kisten und Decks, Zuwachs je Monat, Scans je Tag, kommende Drops

Dieselben Abfragen wie Regal, Sammlung und Home Assistant (`ha.daten`, `preise.werte`); neu sind nur die Verteilungen.
Der Verlauf zeigt die **Preisentwicklung des heutigen Bestands**: `preis` kennt nur Produkte, die am jeweiligen Tag zur
Sammlung gehörten, eine einfache Summe je Tag würde also mit jeder neuen Karte springen. Darum läuft er vom heutigen Wert
rückwärts über die Änderungen je Produkt, wie `veraenderung_gesamt` nur aus Preisen, die es wirklich gab.
"""
from __future__ import annotations

from contextlib import closing
from datetime import date, timedelta

from starlette.responses import JSONResponse
from starlette.routing import Route

from binderapp.db.datenbank import version
from binderapp.ha import daten as ha_daten
from binderapp.preise import werte
from binderapp.web import kisten as k_

__all__ = ["routen", "verlauf", "verteilungen"]

TOP_KARTEN = 12
TOP_BEWEGUNG = 5
TOP_SETS = 10
VERLAUF_TAGE = 400     # Preistage im Verlauf
ZUWACHS_MONATE = 24
SCAN_TAGE = 30
DROPS = 4

# Änderung je Preistag und Produkt gegen den vorigen Tag, an dem dieses Produkt einen Preis hatte
SQL_VERLAUF = """
WITH bestand AS (
    SELECT kd.cardmarket_id AS id, k.foil AS foil, SUM(k.anzahl) AS anzahl
    FROM karte k JOIN kartendaten kd ON kd.scryfall_id = k.scryfall_id
    WHERE k.nutzer_id = ? AND kd.cardmarket_id IS NOT NULL GROUP BY 1, 2
), schritt AS (
    SELECT p.tag, p.cardmarket_id AS id, p.{normal} AS normal, p.{foil} AS foil_preis,
           LAG(p.{normal}) OVER fenster AS normal_vorher, LAG(p.{foil}) OVER fenster AS foil_vorher
    FROM preis p
    WHERE p.tag >= ? AND p.cardmarket_id IN (SELECT id FROM bestand)
    WINDOW fenster AS (PARTITION BY p.cardmarket_id ORDER BY p.tag)
)
SELECT s.tag, SUM(b.anzahl * (CASE WHEN b.foil THEN s.foil_preis - s.foil_vorher ELSE s.normal - s.normal_vorher END))
FROM schritt s JOIN bestand b ON b.id = s.id GROUP BY s.tag
"""

# Ausdrücke nur aus dieser Liste kommen ins SQL (Spalten von karte `k` und kartendaten `kd`, Preis `p`)
GRUPPEN = {
    "farbe": ("kd.farbgruppe", ""),
    "seltenheit": ("kd.seltenheit", ""),
    "typ": ("kd.haupttyp", ""),
    "sprache": ("k.sprache", ""),
    "mana": (k_.MANA_STUFE, "AND kd.haupttyp IS NOT NULL AND kd.haupttyp != 'land'"),
    "erkennung": ("CASE WHEN k.geprueft THEN 'geprueft' ELSE k.status END", ""),
    "foil": ("k.foil", ""),
}
# Reihenfolge der Bereiche: von teuer nach günstig, zuletzt ohne Preis
PREISBEREICHE = (("ab50", "ab 50 €", 50), ("ab20", "20 bis 50 €", 20), ("ab5", "5 bis 20 €", 5),
                 ("ab1", "1 bis 5 €", 1), ("unter1", "unter 1 €", 0))

SQL_GRUPPE = f"""
SELECT {{ausdruck}} AS wert, SUM(k.anzahl) AS karten, COUNT(*) AS posten, COALESCE(SUM(k.anzahl * {{preis}}), 0) AS euro
{k_.VON} LEFT JOIN preis p ON p.cardmarket_id = kd.cardmarket_id AND p.tag = ?
WHERE k.nutzer_id = ? AND {k_.ORT_DA} {{bedingung}}
GROUP BY 1
"""
SQL_SETS = f"""
SELECT {k_.SATZ} AS wert, MIN(kd.set_name) AS name, SUM(k.anzahl) AS karten,
       COALESCE(SUM(k.anzahl * {{preis}}), 0) AS euro
{k_.VON} LEFT JOIN preis p ON p.cardmarket_id = kd.cardmarket_id AND p.tag = ?
WHERE k.nutzer_id = ? AND {k_.ORT_DA} AND {k_.SATZ} IS NOT NULL
GROUP BY 1 ORDER BY {{sortierung}} DESC, wert LIMIT ?
"""
SQL_ZUWACHS = f"""
SELECT substr(k.angelegt, 1, 7) AS monat, SUM(k.anzahl) AS karten FROM karte k
WHERE k.nutzer_id = ? AND {k_.ORT_DA} GROUP BY 1 ORDER BY 1 DESC LIMIT ?
"""
SQL_SCANS = """
SELECT substr(f.aufgenommen, 1, 10) AS tag, COUNT(*) AS fotos
FROM scan_foto f JOIN scan_durchgang d ON d.id = f.durchgang_id
WHERE d.nutzer_id = ? AND f.aufgenommen >= ? AND f.status != 'verworfen' GROUP BY 1
"""
# Kaufpreis gilt je Karte (ManaBox-Spalte „Purchase price“); verglichen werden nur Karten mit beiden Angaben in Euro
SQL_KAUFPREIS = f"""
SELECT COALESCE(SUM(k.anzahl * k.kaufpreis), 0) AS kauf, COALESCE(SUM(k.anzahl * {{preis}}), 0) AS wert,
       COALESCE(SUM(k.anzahl), 0) AS karten
{k_.VON} JOIN preis p ON p.cardmarket_id = kd.cardmarket_id AND p.tag = ?
WHERE k.nutzer_id = ? AND {k_.ORT_DA} AND k.kaufpreis IS NOT NULL AND {{preis}} IS NOT NULL
  AND COALESCE(UPPER(k.kaufpreis_waehrung), 'EUR') = 'EUR'
"""
SQL_DROPS = """
SELECT d.id, d.name, d.verkauf, COUNT(k.id) AS karten, SUM(k.anzahl * k.teuer) AS teuer,
       (SELECT k2.scryfall_id FROM secret_karte k2 WHERE k2.drop_id = d.id AND k2.scryfall_id IS NOT NULL
        ORDER BY k2.teuer IS NULL, k2.teuer DESC, k2.id LIMIT 1) AS bild
FROM secret_drop d LEFT JOIN secret_karte k ON k.drop_id = d.id
WHERE d.verkauf > ? GROUP BY d.id ORDER BY d.verkauf, d.id LIMIT ?
"""


def verlauf(con, nutzer: int, preisart: str = werte.PREISART) -> list[dict]:
    """[{"tag", "wert"}] der letzten Preistage, aufsteigend. Der letzte Wert ist der heutige Gesamtwert."""
    gesamt = werte.gesamtwert(con, nutzer, preisart)
    if gesamt is None:
        return []
    tage = [z[0] for z in con.execute("SELECT tag FROM preisabruf ORDER BY tag DESC LIMIT ?", (VERLAUF_TAGE,))]
    normal, foil = werte.PREISARTEN[preisart]
    aenderung = dict(con.execute(SQL_VERLAUF.format(normal=normal, foil=foil), (nutzer, tage[-1])).fetchall())
    punkte, wert = [], gesamt["wert"]
    for tag in tage:   # neuester zuerst: der Wert des Vortags ist der heutige minus die Änderung von heute
        punkte.append({"tag": tag, "wert": round(wert, 2)})
        wert -= aenderung.get(tag) or 0
    return punkte[::-1]


def _gruppe(con, nutzer: int, tag: str | None, preis: str, name: str) -> list[dict]:
    ausdruck, bedingung = GRUPPEN[name]
    sql = SQL_GRUPPE.format(ausdruck=ausdruck, preis=preis, bedingung=bedingung)
    return [{"wert": z["wert"], "karten": z["karten"], "posten": z["posten"], "euro": round(z["euro"], 2)}
            for z in con.execute(sql, (tag, nutzer))]


def _preisbereiche(con, nutzer: int, tag: str | None, preis: str) -> list[dict]:
    stufen = " ".join(f"WHEN {preis} >= {grenze} THEN '{schluessel}'" for schluessel, _, grenze in PREISBEREICHE)
    sql = SQL_GRUPPE.format(ausdruck=f"CASE {stufen} ELSE 'ohne' END", preis=preis, bedingung="")
    gefunden = {z["wert"]: z for z in con.execute(sql, (tag, nutzer))}
    ergebnis = []
    for schluessel, text in [(s, t) for s, t, _ in PREISBEREICHE] + [("ohne", "ohne Preis")]:
        z = gefunden.get(schluessel)
        ergebnis.append({"wert": schluessel, "text": text, "karten": z["karten"] if z else 0,
                         "euro": round(z["euro"], 2) if z else 0})
    return ergebnis


def _sets(con, nutzer: int, tag: str | None, preis: str, sortierung: str) -> list[dict]:
    sql = SQL_SETS.format(preis=preis, sortierung=sortierung)
    return [{"wert": z["wert"], "name": z["name"], "karten": z["karten"], "euro": round(z["euro"], 2)}
            for z in con.execute(sql, (tag, nutzer, TOP_SETS))]


def verteilungen(con, nutzer: int, preisart: str = werte.PREISART) -> dict:
    tag, preis = werte.neuester_tag(con), werte.preis_ausdruck(preisart)
    ergebnis = {name: _gruppe(con, nutzer, tag, preis, name) for name in GRUPPEN}
    ergebnis["preisbereiche"] = _preisbereiche(con, nutzer, tag, preis)
    # je Sicht die eigenen zehn größten Sets: nach Karten und nach Euro sind es oft andere
    ergebnis["sets"] = {"karten": _sets(con, nutzer, tag, preis, "karten"), "euro": _sets(con, nutzer, tag, preis, "euro")}
    return ergebnis


def _kaufpreis(con, nutzer: int, preisart: str) -> dict | None:
    tag = werte.neuester_tag(con)
    if tag is None or version(con) < 11:
        return None
    z = con.execute(SQL_KAUFPREIS.format(preis=werte.preis_ausdruck(preisart)), (tag, nutzer)).fetchone()
    if not z["karten"]:
        return None
    return {"kauf": round(z["kauf"], 2), "wert": round(z["wert"], 2), "karten": z["karten"]}


def _scans(con, nutzer: int, heute: date) -> list[dict]:
    anfang = heute - timedelta(days=SCAN_TAGE - 1)
    fotos = dict(con.execute(SQL_SCANS, (nutzer, anfang.isoformat())).fetchall())
    tage = [(anfang + timedelta(days=i)).isoformat() for i in range(SCAN_TAGE)]
    return [{"tag": t, "fotos": fotos.get(t, 0)} for t in tage]


def _drops(con, heute: date) -> list[dict]:
    if version(con) < 12:
        return []
    return [{**dict(z), "teuer": None if z["teuer"] is None else round(z["teuer"], 2)}
            for z in con.execute(SQL_DROPS, (heute.isoformat(), DROPS))]


def stand(con) -> tuple:
    """Ändert sich, sobald sich Karten, Kartendaten oder Preise ändern (jede Änderung der Sammlung steht in `historie`)."""
    return tuple(con.execute(
        "SELECT (SELECT MAX(id) FROM historie), (SELECT COUNT(*) FROM historie), (SELECT COUNT(*) FROM preis),"
        " (SELECT MAX(tag) FROM preisabruf), (SELECT COUNT(*) FROM kartendaten),"
        " (SELECT MAX(geaendert) FROM kartendaten)").fetchone())


def _sammlung(con, nutzer: int) -> dict:
    """Der teure Teil: alles, was nur von Karten und Preisen abhängt."""
    art = werte.PREISART
    kopf = ha_daten.werte(con, nutzer, art)
    return {
        "preisart": art,
        "preise": werte.stand(con),
        "wert": kopf["gesamtwert"], "tag": kopf["preistag"], "ohne_preis": kopf["ohne_preis"],
        "karten": kopf["karten"], "posten": kopf["posten"], "veraenderung": kopf["veraenderung"],
        "binder": kopf["binder"], "kisten": kopf["kisten"], "decks": kopf["decks"],
        "kaufpreis": _kaufpreis(con, nutzer, art),
        "verlauf": verlauf(con, nutzer, art),
        "teuerste": werte.teuerste(con, nutzer, TOP_KARTEN, art, mit_bild=True),
        "bewegung": {name: werte.groesste_aenderung(con, nutzer, TOP_BEWEGUNG, art, name, mit_bild=True)
                     for name in werte.ABSTAENDE},
        "verteilung": verteilungen(con, nutzer, art),
        "zuwachs": [dict(z) for z in con.execute(SQL_ZUWACHS, (nutzer, ZUWACHS_MONATE)).fetchall()][::-1],
    }


def _aktuell(con, nutzer: int, heute: date) -> dict:
    """Der günstige Teil, der sich auch ohne Änderung an den Karten bewegt."""
    return {"status": ha_daten.status(con, nutzer), "scans": _scans(con, nutzer, heute), "drops": _drops(con, heute)}


def lesen(con, nutzer: int, heute: date | None = None) -> dict:
    return {**_sammlung(con, nutzer), **_aktuell(con, nutzer, heute or date.today())}


def routen(h) -> list[Route]:
    merk: dict = {}   # letzte Antwort zu `stand`: Der Verlauf kostet auf dem Mini-PC Sekunden, ändert sich aber selten

    @k_._lesen
    def dashboard(request):
        with closing(h.db()) as con:
            con.execute("BEGIN")  # ein Stand für alle Abfragen dieser Antwort
            k_._ab_version_3(con, 404)
            k_._ab_version_8(con)
            jetzt = stand(con)
            if merk.get("stand") != jetzt:
                merk.update(daten=_sammlung(con, h.nutzer), stand=jetzt)
            return JSONResponse({**merk["daten"], **_aktuell(con, h.nutzer, date.today())})

    return [Route("/dashboard", dashboard)]
