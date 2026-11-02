"""Werte per MQTT an Home Assistant (Discovery): läuft im Add-on als eigener Thread neben dem Server.

Entscheidung des Nutzers (02.11.2026): MQTT mit Discovery über das Mosquitto-Add-on. Zugang holt das Add-on beim
Supervisor (`services: mqtt:want` in config.yaml, GET http://supervisor/services/mqtt), der Nutzer trägt nichts ein.

  mtg_binder/verfuegbar       online / offline (letzter Wille; Add-on aus → Sensoren „nicht verfügbar“)
  mtg_binder/status           JSON aus ha.daten.status(), jede Minute
  mtg_binder/werte            JSON aus ha.daten.werte(), stündlich und nach jedem neuen Preistag
  mtg_binder/binder/<id>      {wert, karten} je Binder   ┐ Sensoren entstehen und verschwinden
  mtg_binder/kiste/<id>       {wert, karten} je Kiste    ┘ mit Binder und Kiste
  homeassistant/<art>/mtg_binder/<objekt>/config   Discovery (retain)

Nur lesend: Die Datenbank wird schreibgeschützt geöffnet. Welche Binder und Kisten schon angemeldet sind, steht in
`<daten>/ha_mqtt.json` (zum Abmelden gelöschter Binder auch nach einem Neustart).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from contextlib import closing
from pathlib import Path

from binderapp.db.datenbank import verbinden
from binderapp.ha import daten
from binderapp.ha.mqtt import MqttFehler, Verbindung
from binderapp.preise import werte as preise

PRAEFIX = "mtg_binder"
DISCOVERY = "homeassistant"
VERFUEGBAR = f"{PRAEFIX}/verfuegbar"
THEMA_STATUS = f"{PRAEFIX}/status"
THEMA_WERTE = f"{PRAEFIX}/werte"
TAKT_STATUS = 60       # Sekunden (Entscheidung des Nutzers: Status jede Minute, Werte stündlich)
TAKT_WERTE = 3600
WARTEN_FEHLER = 60     # nach einem Verbindungsfehler
STAND_DATEI = "ha_mqtt.json"
SUPERVISOR = "http://supervisor/services/mqtt"

log = logging.getLogger("binderapp.ha")


def geraet(version: str) -> dict:
    # Gerätename „MTG“: Home Assistant setzt ihn vor den Namen der Entität (sensor.mtg_gesamtwert)
    return {"identifiers": [PRAEFIX], "name": "MTG", "manufacturer": "MTG-Binder-App",
            "model": "Home-Assistant-Add-on", "sw_version": version}


def _wert(feld: str) -> str:
    return "{{ value_json." + feld + " }}"


def _liste(feld: str, *weitere: str) -> str:
    """Attribute aus dem JSON der Werte (Listen der Top 5, Binder und Kisten)."""
    teile = [f"'{feld}': value_json.{feld}"] + [f"'{w}': value_json.{w}" for w in weitere]
    return "{{ {" + ", ".join(teile) + "} | tojson }}"


EURO = {"device_class": "monetary", "unit_of_measurement": "EUR", "state_class": "total"}
ANZAHL = {"state_class": "measurement"}

# (Art, Objekt, Name, Thema, Wert, weitere Angaben) der festen Entitäten
FEST = [
    ("binary_sensor", "pc_verbunden", "PC verbunden", THEMA_STATUS, _wert("pc_verbunden"),
     {"device_class": "connectivity", "json_attributes_topic": THEMA_STATUS,
      "json_attributes_template": "{{ {'zuletzt': value_json.pc_zuletzt, 'zustand': value_json.pc_zustand} | tojson }}"}),
    ("sensor", "warteschlange", "Warteschlange", THEMA_STATUS, _wert("warteschlange"),
     {**ANZAHL, "unit_of_measurement": "Fotos", "icon": "mdi:tray-full", "json_attributes_topic": THEMA_STATUS,
      "json_attributes_template": "{{ {'binderfotos': value_json.fotos_wartend, 'scanfotos': value_json.scan_wartend}"
                                  " | tojson }}"}),
    ("sensor", "scan_wartend", "Scan wartend", THEMA_STATUS, _wert("scan_wartend"),
     {**ANZAHL, "unit_of_measurement": "Fotos", "icon": "mdi:card-search-outline"}),
    ("sensor", "zur_pruefung", "Karten zur Prüfung", THEMA_STATUS, _wert("zur_pruefung"),
     {**ANZAHL, "unit_of_measurement": "Karten", "icon": "mdi:magnify-scan"}),
    ("sensor", "scan_zur_pruefung", "Scan zur Prüfung", THEMA_STATUS, _wert("scan_zur_pruefung"),
     {**ANZAHL, "unit_of_measurement": "Karten", "icon": "mdi:inbox-arrow-down"}),
    ("sensor", "letzter_scan", "Letzter Scan", THEMA_STATUS, _wert("letzter_scan"), {"device_class": "timestamp"}),
    ("sensor", "letzter_preisabruf", "Letzter Preisabruf", THEMA_STATUS, _wert("letzter_preisabruf"),
     {"device_class": "timestamp"}),
    ("sensor", "gesamtwert", "Gesamtwert", THEMA_WERTE, _wert("gesamtwert"),
     {**EURO, "json_attributes_topic": THEMA_WERTE,
      "json_attributes_template": _liste("preisart", "preistag", "ohne_preis", "binder", "kisten")}),
    ("sensor", "karten", "Karten", THEMA_WERTE, _wert("karten"),
     {**ANZAHL, "unit_of_measurement": "Karten", "icon": "mdi:cards", "json_attributes_topic": THEMA_WERTE,
      "json_attributes_template": _liste("posten")}),
    ("sensor", "teuerste", "Top 5 teuerste Karten", THEMA_WERTE,
     "{{ value_json.teuerste[0].preis if value_json.teuerste else None }}",
     {**EURO, "state_class": None, "icon": "mdi:diamond-stone", "json_attributes_topic": THEMA_WERTE,
      "json_attributes_template": "{{ {'karten': value_json.teuerste} | tojson }}"}),
    ("sensor", "gewinner", "Top 5 Gewinner", THEMA_WERTE,
     "{{ value_json.gewinner[0].euro if value_json.gewinner else None }}",
     {**EURO, "state_class": None, "icon": "mdi:trending-up", "json_attributes_topic": THEMA_WERTE,
      "json_attributes_template": "{{ {'karten': value_json.gewinner} | tojson }}"}),
    ("sensor", "verlierer", "Top 5 Verlierer", THEMA_WERTE,
     "{{ value_json.verlierer[0].euro if value_json.verlierer else None }}",
     {**EURO, "state_class": None, "icon": "mdi:trending-down", "json_attributes_topic": THEMA_WERTE,
      "json_attributes_template": "{{ {'karten': value_json.verlierer} | tojson }}"}),
]
for _name, _text in (("vortag", "Vortag"), ("tage3", "3 Tage"), ("tage30", "30 Tage")):
    FEST.append(("sensor", f"veraenderung_{_name}", f"Veränderung {_text}", THEMA_WERTE,
                 _wert(f"veraenderung.{_name}.euro"),
                 {**EURO, "icon": "mdi:swap-vertical", "json_attributes_topic": THEMA_WERTE,
                  "json_attributes_template": "{{ {'vergleichstag': value_json.veraenderung." + _name + ".tag}"
                                              " | tojson }}"}))
    FEST.append(("sensor", f"veraenderung_{_name}_prozent", f"Veränderung {_text} Prozent", THEMA_WERTE,
                 _wert(f"veraenderung.{_name}.prozent"), {**ANZAHL, "unit_of_measurement": "%",
                                                          "icon": "mdi:percent"}))


def konfiguration(art: str, objekt: str, name: str, thema: str, wert: str, weitere: dict, version: str) -> dict:
    k = {"name": name, "unique_id": f"{PRAEFIX}_{objekt}", "state_topic": thema,
         "value_template": wert, "availability_topic": VERFUEGBAR, "device": geraet(version)}
    k.update({s: w for s, w in weitere.items() if w is not None})
    return k


def ort_entitaeten(art_ort: str, eintrag: dict) -> list[tuple]:
    """Zwei Sensoren je Binder oder Kiste: Wert und Karten."""
    thema = f"{PRAEFIX}/{art_ort}/{eintrag['id']}"
    titel = f"{'Binder' if art_ort == 'binder' else 'Kiste'} {eintrag['name']}"
    basis = f"{art_ort}_{eintrag['id']}"
    return [("sensor", f"{basis}_wert", f"{titel} Wert", thema, _wert("wert"), {**EURO}),
            ("sensor", f"{basis}_karten", f"{titel} Karten", thema, _wert("karten"),
             {**ANZAHL, "unit_of_measurement": "Karten", "icon": "mdi:cards"})]


def discovery_thema(art: str, objekt: str) -> str:
    return f"{DISCOVERY}/{art}/{PRAEFIX}/{objekt}/config"


def supervisor_dienst() -> dict:
    """Zugang zum Mosquitto-Add-on vom Supervisor: {host, port, username, password, ssl}."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise MqttFehler("kein SUPERVISOR_TOKEN (läuft nicht als Add-on)")
    anfrage = urllib.request.Request(SUPERVISOR, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(anfrage, timeout=10) as antwort:
            daten_ = json.load(antwort).get("data") or {}
    except (OSError, ValueError) as f:
        raise MqttFehler(f"kein MQTT-Dienst beim Supervisor (läuft das Mosquitto-Add-on?): {f}") from f
    if not daten_.get("host"):
        raise MqttFehler("Der Supervisor meldet keinen MQTT-Broker.")
    if daten_.get("ssl"):
        raise MqttFehler("Der Broker verlangt TLS; das unterstützt das Add-on nicht.")
    return daten_


class Sender(threading.Thread):
    """Schickt Status (jede Minute) und Werte (stündlich, nach neuem Preistag sofort) an den Broker."""

    def __init__(self, db: Path, daten_ordner: Path, version: str, dienst=supervisor_dienst,
                 preisart: str = preise.PREISART, takt_status: float = TAKT_STATUS, takt_werte: float = TAKT_WERTE,
                 warten_fehler: float = WARTEN_FEHLER):
        super().__init__(name="ha-mqtt", daemon=True)
        if preisart not in preise.PREISARTEN:
            raise ValueError(f"unbekannte Preisart {preisart!r}")
        self.db, self.stand_datei, self.version, self.dienst = Path(db), Path(daten_ordner) / STAND_DATEI, version, dienst
        self.preisart, self.takt_status, self.takt_werte, self.warten_fehler = preisart, takt_status, takt_werte, warten_fehler
        self.verbindung: Verbindung | None = None
        self.halt = threading.Event()
        self.letzter_fehler: str | None = None
        self.letzte_werte = float("-inf")   # monotonic() kann klein sein (frisch gestarteter Rechner)
        self.preistag: str | None = None

    # ------------------------------------------------------------ Verbindung

    def _verbinden(self) -> None:
        d = self.dienst()
        self.verbindung = Verbindung(d["host"], int(d.get("port") or 1883), d.get("username"), d.get("password"),
                                     kennung=PRAEFIX, wille=(VERFUEGBAR, "offline"))
        self.verbindung.verbinden()
        self._discovery_fest()
        self.verbindung.senden(VERFUEGBAR, "online")
        self.letzte_werte = float("-inf")   # gleich alles senden
        log.info("Home Assistant: mit MQTT-Broker %s:%s verbunden", d["host"], d.get("port") or 1883)

    def _senden_json(self, thema: str, inhalt) -> None:
        self.verbindung.senden(thema, json.dumps(inhalt, ensure_ascii=False, separators=(",", ":")))

    def _discovery_fest(self) -> None:
        for e in FEST:
            self._senden_json(discovery_thema(e[0], e[1]), konfiguration(*e, version=self.version))

    # ------------------------------------------------------------ Runden

    def runde(self, jetzt: float) -> None:
        """Eine Runde: Status immer, Werte nach Takt oder neuem Preistag."""
        if not self.db.exists():
            self.verbindung.ping()   # Verbindung halten, sonst trennt der Broker nach dem Keepalive
            return
        with closing(verbinden(self.db, nur_lesen=True)) as con:
            status = daten.status(con)
            tag = preise.neuester_tag(con)
            neue_werte = None
            if jetzt - self.letzte_werte >= self.takt_werte or tag != self.preistag:
                neue_werte = daten.werte(con, preisart=self.preisart)
        self._senden_json(THEMA_STATUS, status)
        if neue_werte is not None:
            self._werte_senden(neue_werte)
            self.letzte_werte, self.preistag = jetzt, tag
        self.verbindung.ping()

    def _werte_senden(self, w: dict) -> None:
        self._senden_json(THEMA_WERTE, w)
        aktuell = {}
        for art_ort, liste in (("binder", w["binder"]), ("kiste", w["kisten"])):
            for eintrag in liste:
                for e in ort_entitaeten(art_ort, eintrag):
                    aktuell[e[1]] = e[0]
                    self._senden_json(discovery_thema(e[0], e[1]), konfiguration(*e, version=self.version))
                self._senden_json(f"{PRAEFIX}/{art_ort}/{eintrag['id']}",
                                  {"wert": eintrag["wert"], "karten": eintrag["karten"]})
        # Gelöschte Binder und Kisten abmelden: leere Konfiguration entfernt die Entität in Home Assistant
        for objekt, art in self._stand_lesen().items():
            if objekt not in aktuell:
                self.verbindung.senden(discovery_thema(art, objekt), b"")
                teil = objekt.rsplit("_", 1)[0]          # binder_3_wert → binder_3
                art_ort, oid = teil.split("_", 1)
                self.verbindung.senden(f"{PRAEFIX}/{art_ort}/{oid}", b"")
        self._stand_schreiben(aktuell)

    def _stand_lesen(self) -> dict[str, str]:
        try:
            stand = json.loads(self.stand_datei.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(stand, dict):
            return {}
        return {o: a for o, a in stand.items() if isinstance(a, str) and o.count("_") >= 2
                and o.split("_", 1)[0] in ("binder", "kiste")}

    def _stand_schreiben(self, stand: dict[str, str]) -> None:
        teil = self.stand_datei.with_name(self.stand_datei.name + ".neu")
        teil.write_text(json.dumps(stand, sort_keys=True), encoding="utf-8")
        os.replace(teil, self.stand_datei)

    # ------------------------------------------------------------ Ablauf

    def run(self) -> None:
        while not self.halt.is_set():
            try:
                if self.verbindung is None or not self.verbindung.verbunden:
                    self._verbinden()
                self.runde(time.monotonic())
                if self.letzter_fehler:
                    log.info("Home Assistant: Werte werden wieder gesendet")
                self.letzter_fehler = None
                pause = self.takt_status
            except Exception as f:   # nie den Server stören; Meldung nur bei neuer Ursache
                text = str(f)
                if text != self.letzter_fehler:
                    log.warning("Home Assistant (MQTT): %s; neuer Versuch alle %d s", text, self.warten_fehler)
                self.letzter_fehler = text
                if self.verbindung is not None:
                    self.verbindung.schliessen()
                pause = self.warten_fehler
            self.halt.wait(pause)

    def stoppen(self) -> None:
        """Beim Beenden des Add-ons: „offline“ melden und sauber trennen (erst wenn die Runde fertig ist)."""
        self.halt.set()
        if self.is_alive():
            self.join(timeout=15)
        if self.verbindung is not None and self.verbindung.verbunden:
            try:
                self.verbindung.senden(VERFUEGBAR, "offline")
            except MqttFehler:
                pass
            self.verbindung.trennen()
