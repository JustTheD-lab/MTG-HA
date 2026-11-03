"""Werte für Home Assistant: Zähler und Werte aus der Datenbank, nur lesend.

Zwei Gruppen mit eigenem Takt (Entscheidung des Nutzers, 02.11.2026):
  status()  jede Minute: PC verbunden, Warteschlange, Scan-Eingang, Karten zur Prüfung, letzter Scan, letzter Preisabruf
  werte()   stündlich: Gesamtwert mit Veränderung (Euro und Prozent), Kartenzahlen, Binder und Kisten, Top 5

Nichts wird neu berechnet oder gespeichert: Es sind dieselben Abfragen wie in der App (Regal, Sammlung,
Prüfansicht, preise.werte). Die Preisart ist eine Einstellung (preise.werte.PREISARTEN), heute Ø 7 Tage.
"""
from __future__ import annotations

from binderapp.db import erkennung
from binderapp.db.datenbank import version
from binderapp.preise import werte as preise

NUTZER = 1
TOP = 5

SQL_FOTOS_WARTEND = "SELECT COUNT(*) FROM foto WHERE nutzer_id = ? AND status IN ('neu', 'in_verarbeitung')"
SQL_SCAN_WARTEND = ("SELECT COUNT(*) FROM scan_foto f JOIN scan_durchgang d ON d.id = f.durchgang_id"
                    " WHERE d.nutzer_id = ? AND f.status IN ('neu', 'in_verarbeitung')")
SQL_LETZTER_SCAN = ("SELECT MAX(f.aufgenommen) FROM scan_foto f JOIN scan_durchgang d ON d.id = f.durchgang_id"
                    " WHERE d.nutzer_id = ?")
# Wie die Warteschlange der Prüfansicht (web/pruefen.py): ungeprüfte unsichere Karten und unsichere leere Taschen
SQL_ZUR_PRUEFUNG = """
SELECT (SELECT COUNT(*) FROM karte k WHERE k.nutzer_id = :n AND k.geprueft = 0
          AND k.status IN ('unsicher', 'name_sicher'))
     + (SELECT COUNT(*) FROM tasche t JOIN seite s ON s.id = t.seite_id JOIN binder b ON b.id = s.binder_id
         WHERE b.nutzer_id = :n AND t.inhalt = 'unsicher'
           AND NOT EXISTS (SELECT 1 FROM karte k WHERE k.tasche_id = t.id))
"""
SQL_SCAN_ZUR_PRUEFUNG = """
SELECT COUNT(*) FROM karte k JOIN kiste ki ON ki.id = k.kiste_id
WHERE k.nutzer_id = ? AND ki.extern_name = '@scan-eingang' AND k.geprueft = 0 AND k.status IN ('unsicher', 'name_sicher')
"""
# Kartenzahlen wie Regal (web/server.SQL_REGAL), Kistenregal und Gesamtsammlung (web/sammlung.SQL_KOPF)
SQL_BINDER = """
SELECT b.id, b.name, COALESCE(SUM(k.anzahl), 0) AS karten
FROM binder b LEFT JOIN seite s ON s.binder_id = b.id LEFT JOIN tasche t ON t.seite_id = s.id
LEFT JOIN karte k ON k.tasche_id = t.id
WHERE b.nutzer_id = ? GROUP BY b.id ORDER BY b.regalplatz, b.id
"""
SQL_KISTEN = """
SELECT ki.id, ki.name, COALESCE(SUM(k.anzahl), 0) AS karten
FROM kiste ki LEFT JOIN karte k ON k.kiste_id = ki.id
WHERE ki.nutzer_id = ? GROUP BY ki.id ORDER BY ki.regalplatz, ki.name
"""
SQL_SAMMLUNG = """
SELECT COUNT(*) AS posten, COALESCE(SUM(anzahl), 0) AS karten FROM karte
WHERE nutzer_id = ? AND (kiste_id IS NOT NULL OR tasche_id IS NOT NULL {deck})
"""
# ab Version 8: Decks als Ort (Karten, die im Deck liegen); Kosten der Fehlt-Liste aus dem Abgleich
SQL_DECKS = """
SELECT de.id, de.name, COALESCE(SUM(k.anzahl), 0) AS karten
FROM deck de LEFT JOIN karte k ON k.deck_id = de.id
WHERE de.nutzer_id = ? GROUP BY de.id ORDER BY de.regalplatz, de.id
"""


def _zeit(text: str | None) -> str | None:
    """Zeitpunkt aus der Datenbank (UTC ohne Zone) im Format für Home Assistant (device_class timestamp)."""
    return f"{text}+00:00" if text else None


def status(con, nutzer: int = NUTZER) -> dict:
    z = erkennung.letztes_lebenszeichen(con)
    pc_verbunden = bool(z is not None and z[1] < erkennung.FRISCH and z[0].get("zustand") != "beendet")
    fotos = con.execute(SQL_FOTOS_WARTEND, (nutzer,)).fetchone()[0]
    scan = letzter_scan = scan_pruefen = None
    if version(con) >= 5:
        scan = con.execute(SQL_SCAN_WARTEND, (nutzer,)).fetchone()[0]
        letzter_scan = con.execute(SQL_LETZTER_SCAN, (nutzer,)).fetchone()[0]
    if version(con) >= 3:
        pruefen = con.execute(SQL_ZUR_PRUEFUNG, {"n": nutzer}).fetchone()[0]
        scan_pruefen = con.execute(SQL_SCAN_ZUR_PRUEFUNG, (nutzer,)).fetchone()[0]
    else:
        pruefen = None
    preisstand = preise.stand(con)
    return {
        "pc_verbunden": "ON" if pc_verbunden else "OFF",
        "pc_zuletzt": _zeit(con.execute("SELECT zeitpunkt FROM dienst WHERE name = ?",
                                        (erkennung.DIENST,)).fetchone()[0]) if z is not None else None,
        "pc_zustand": z[0].get("zustand") if z is not None else None,
        "fotos_wartend": fotos,
        "scan_wartend": scan,
        "warteschlange": fotos + (scan or 0),
        "zur_pruefung": pruefen,
        "scan_zur_pruefung": scan_pruefen,
        "letzter_scan": _zeit(letzter_scan),
        "letzter_preisabruf": (preisstand["abgerufen"].removesuffix("Z") + "+00:00") if preisstand.get("abgerufen")
        else None,
    }


def werte(con, nutzer: int = NUTZER, preisart: str = preise.PREISART) -> dict:
    gesamt = preise.gesamtwert(con, nutzer, preisart)
    sammlung = con.execute(SQL_SAMMLUNG.format(deck="OR deck_id IS NOT NULL" if version(con) >= 8 else ""),
                           (nutzer,)).fetchone()
    wert_binder = preise.werte(con, nutzer, "binder", preisart)
    binder = [{"id": z["id"], "name": z["name"], "karten": z["karten"],
               "wert": wert_binder.get(z["id"], {}).get("wert") if gesamt else None}
              for z in con.execute(SQL_BINDER, (nutzer,))]
    kisten = []
    if version(con) >= 3:
        wert_kisten = preise.werte(con, nutzer, "kiste", preisart)
        kisten = [{"id": z["id"], "name": z["name"], "karten": z["karten"],
                   "wert": wert_kisten.get(z["id"], {}).get("wert") if gesamt else None}
                  for z in con.execute(SQL_KISTEN, (nutzer,))]
    decks = []
    if version(con) >= 8:
        from binderapp.decks import abgleich
        wert_decks = preise.werte(con, nutzer, "deck", preisart)
        for z in con.execute(SQL_DECKS, (nutzer,)).fetchall():
            fehlt = abgleich.abgleichen(con, nutzer, abgleich.deck_holen(con, nutzer, z["id"]), preisart)["wert"]
            decks.append({"id": z["id"], "name": z["name"], "karten": z["karten"],
                          "wert": wert_decks.get(z["id"], {}).get("wert", 0.0) if gesamt else None,
                          "fehlt_kosten": fehlt["fehlt"] if fehlt else None})
    veraenderung = preise.veraenderung_gesamt(con, nutzer, preisart)
    aenderung = preise.groesste_aenderung(con, nutzer, TOP, preisart)
    return {
        "preisart": preisart,
        "preistag": gesamt["tag"] if gesamt else None,
        "gesamtwert": gesamt["wert"] if gesamt else None,
        "ohne_preis": gesamt["ohne_preis"] if gesamt else None,
        "karten": sammlung["karten"],
        "posten": sammlung["posten"],
        "veraenderung": veraenderung,
        "binder": binder,
        "kisten": kisten,
        "decks": decks,
        "teuerste": preise.teuerste(con, nutzer, TOP, preisart),
        "gewinner": aenderung["gewinner"],
        "verlierer": aenderung["verlierer"],
    }
