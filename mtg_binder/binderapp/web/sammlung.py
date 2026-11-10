"""Gesamtsammlung (Paket L, 28.10.2026): alle Karten aus Kisten und gescannten Bindern, nur lesend.

    GET /api/sammlung            Kopf: Posten und Karten gesamt, je Ort (Binder, Kisten) und je Quelle, mögliche
                                 Doppel, Karten ohne Ort, Werte für die Filterpillen (wie /api/kiste/{id}) und
                                 die Orte (Kisten und Binder) für Filter und Gruppen
    GET /api/sammlung/karten?…   wie /api/kiste/{id}/karten, zusätzlich ort=k12,b3 · quelle=scan,manabox,manuell
                                 · doppel=1 und die Gruppen „ort“ und „quelle“; jede Karte mit Ort, Quelle, Doppel
    GET /api/sammlung/ueberzaehlig?limit=4&laender=1&format=csv|text
                                 Überzählige je Druck über dem Limit mit Wert (Paket 4, sortieren/ueberzaehlig.py)

Zählweise (Antwort des Nutzers, 28.10.2026): Binder und Kisten zusammen, die Quellen getrennt ausgewiesen,
mögliche Doppel (gleicher Druck, Sprache und Foil in einer Kiste und in einem Binder) nur gekennzeichnet,
nichts verrechnet. Dieselben Abfragen wie die Kistenansicht (kisten.py, Bereich None = alles).
"""
from __future__ import annotations

import math
from contextlib import closing

from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from binderapp.db.seiten import Fehler
from binderapp.preise import werte
from binderapp.sortieren import ueberzaehlig as ue
from binderapp.web import kisten as k_

__all__ = ["routen"]

SQL_KOPF = f"""
SELECT COUNT(*) AS posten, COALESCE(SUM(k.anzahl), 0) AS karten,
       COALESCE(SUM(k.tasche_id IS NOT NULL), 0) AS binder_posten,
       COALESCE(SUM(CASE WHEN k.tasche_id IS NOT NULL THEN k.anzahl ELSE 0 END), 0) AS binder_karten,
       COALESCE(SUM(k.kiste_id IS NOT NULL), 0) AS kisten_posten,
       COALESCE(SUM(CASE WHEN k.kiste_id IS NOT NULL THEN k.anzahl ELSE 0 END), 0) AS kisten_karten,
       COALESCE(SUM(k.deck_id IS NOT NULL), 0) AS deck_posten,
       COALESCE(SUM(CASE WHEN k.deck_id IS NOT NULL THEN k.anzahl ELSE 0 END), 0) AS deck_karten,
       MAX(kd.manawert) AS manawert_max, COALESCE(MAX(k.foil), 0) AS foils,
       COALESCE(SUM(kd.scryfall_id IS NULL), 0) AS ohne_kartendaten
{k_.VON} WHERE k.nutzer_id = ? AND {k_.ORT_DA}
"""
SQL_QUELLEN = f"""
SELECT k.quelle AS wert, COUNT(*) AS posten, COALESCE(SUM(k.anzahl), 0) AS karten
FROM karte k WHERE k.nutzer_id = ? AND {k_.ORT_DA} GROUP BY k.quelle
"""
SQL_DOPPEL = f"""
SELECT COUNT(*) AS posten, COALESCE(SUM(k.anzahl), 0) AS karten,
       COALESCE(SUM(k.kiste_id IS NOT NULL), 0) AS in_kisten, COALESCE(SUM(k.tasche_id IS NOT NULL), 0) AS in_bindern
FROM karte k WHERE k.nutzer_id = ? AND {k_.ORT_DA} AND {k_.DOPPEL}
"""
SQL_OHNE_ORT = """
SELECT COUNT(*) AS posten, COALESCE(SUM(anzahl), 0) AS karten FROM karte
WHERE nutzer_id = ? AND kiste_id IS NULL AND tasche_id IS NULL AND deck_id IS NULL
"""
SQL_SAETZE = f"""
SELECT {k_.SATZ} AS code, MIN(kd.set_name) AS name, MIN(kd.set_erschienen) AS erschienen, COUNT(*) AS posten
{k_.VON} WHERE k.nutzer_id = ? AND {k_.ORT_DA} AND {k_.SATZ} IS NOT NULL
GROUP BY code ORDER BY COALESCE(MIN(kd.set_erschienen), '9999-99-99'), code
"""
SQL_SPRACHEN = f"""
SELECT k.sprache AS code, COUNT(*) AS posten FROM karte k
WHERE k.nutzer_id = ? AND {k_.ORT_DA} AND k.sprache IS NOT NULL
GROUP BY k.sprache ORDER BY posten DESC, code
"""
# Orte in der Reihenfolge des Regals: zuerst die Binder, dann die Kisten (wie die Gruppe „Ort“)
SQL_ORTE = """
SELECT 'b' || b.id AS wert, 'binder' AS art, b.id AS id, b.name AS name, COUNT(k.id) AS posten,
       COALESCE(SUM(k.anzahl), 0) AS karten, 0 AS r, b.regalplatz AS platz
FROM binder b JOIN seite se ON se.binder_id = b.id JOIN tasche t ON t.seite_id = se.id
JOIN karte k ON k.tasche_id = t.id AND k.nutzer_id = b.nutzer_id
WHERE b.nutzer_id = ? GROUP BY b.id
UNION ALL
SELECT 'k' || ki.id, 'kiste', ki.id, ki.name, COUNT(k.id), COALESCE(SUM(k.anzahl), 0), 1, ki.regalplatz
FROM kiste ki JOIN karte k ON k.kiste_id = ki.id AND k.nutzer_id = ki.nutzer_id
WHERE ki.nutzer_id = ? GROUP BY ki.id
UNION ALL
SELECT 'd' || de.id, 'deck', de.id, de.name, COUNT(k.id), COALESCE(SUM(k.anzahl), 0), 2, de.regalplatz
FROM deck de JOIN karte k ON k.deck_id = de.id AND k.nutzer_id = de.nutzer_id
WHERE de.nutzer_id = ? GROUP BY de.id
ORDER BY r, platz, id
"""


def routen(h) -> list[Route]:

    @k_._lesen
    def kopf(request):
        n = h.nutzer
        with closing(h.db()) as con:
            con.execute("BEGIN")  # ein Stand für alle Abfragen dieser Antwort
            k_._ab_version_3(con, 404)
            k_._ab_version_8(con)
            z = con.execute(SQL_KOPF, (n,)).fetchone()
            quellen = {q["wert"]: dict(q) for q in con.execute(SQL_QUELLEN, (n,))}
            doppel = con.execute(SQL_DOPPEL, (n,)).fetchone()
            ohne = con.execute(SQL_OHNE_ORT, (n,)).fetchone()
            saetze = con.execute(SQL_SAETZE, (n,)).fetchall()
            sprachen = con.execute(SQL_SPRACHEN, (n,)).fetchall()
            orte = con.execute(SQL_ORTE, (n, n, n)).fetchall()
            wert = werte.gesamtwert(con, n)
        maximum = z["manawert_max"]
        return JSONResponse({
            "name": "Sammlung",
            "posten": z["posten"], "karten": z["karten"],
            "wert": wert,   # {"wert": Euro (Durchschnitt 7 Tage), "ohne_preis", "tag"} oder None
            "binder": {"posten": z["binder_posten"], "karten": z["binder_karten"]},
            "kisten": {"posten": z["kisten_posten"], "karten": z["kisten_karten"]},
            "decks": {"posten": z["deck_posten"], "karten": z["deck_karten"]},
            "quellen": [{"wert": w, "text": t, "posten": quellen.get(w, {}).get("posten", 0),
                         "karten": quellen.get(w, {}).get("karten", 0)} for w, t in k_.QUELLEN],
            "doppel": dict(doppel),
            "ohne_ort": dict(ohne),
            "orte": [{"wert": o["wert"], "art": o["art"], "id": o["id"], "name": o["name"], "posten": o["posten"],
                      "karten": o["karten"]} for o in orte],
            "gruppierungen": [{"wert": w, "text": g.text} for w, g in k_.SAMMLUNG_GRUPPIERUNGEN.items()],
            "werte": {
                "sets": [dict(s) for s in saetze],
                "sprachen": [dict(s) for s in sprachen],
                "manawert_max": 0 if maximum is None else min(20, math.ceil(maximum)),
                "foils": bool(z["foils"]),
                "ohne_kartendaten": z["ohne_kartendaten"],
            },
        })

    @k_._lesen
    def karten(request):
        a = k_.anfrage_lesen(request.query_params, None, h.nutzer)
        with closing(h.db()) as con:
            con.execute("BEGIN")  # ein Stand für alle Abfragen dieser Antwort
            k_._ab_version_3(con, 404)
            k_._ab_version_8(con)
            return JSONResponse(k_.seite_lesen(con, a))

    @k_._lesen
    def ueberzaehlige(request):
        q = request.query_params
        limit = ue.limit_lesen(q.get("limit"))
        format_ = q.get("format") or "json"
        if format_ not in ("json", "csv", "text"):
            raise Fehler("Unbekanntes Format.")
        with closing(h.db()) as con:
            con.execute("BEGIN")
            k_._ab_version_3(con, 404)
            k_._ab_version_8(con)
            d = ue.liste(con, h.nutzer, limit, mit_laendern=q.get("laender") == "1")
        if format_ == "csv":
            return Response(ue.als_csv(d), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="ueberzaehlige-{limit}.csv"'})
        if format_ == "text":
            return Response(ue.als_text(d), media_type="text/plain; charset=utf-8")
        return JSONResponse({**d, "text": ue.als_text(d)})

    return [
        Route("/sammlung", kopf),
        Route("/sammlung/ueberzaehlig", ueberzaehlige),
        Route("/sammlung/karten", karten),
    ]
