# Werte der MTG-Binder-App in Home Assistant

Ab Version 0.7.0 schickt das Add-on seine Werte per **MQTT mit Discovery** an Home Assistant. Die Sensoren
entstehen von selbst unter dem Gerät **„MTG“** (Einstellungen → Geräte & Dienste → MQTT). Es gibt nichts in
`configuration.yaml` einzutragen; die YAML-Beispiele unten sind nur für Dashboard und Benachrichtigungen.

## Voraussetzungen

1. Das **Mosquitto-Broker-Add-on** läuft und die **MQTT-Integration** ist eingerichtet (bei Zigbee2MQTT meist schon so).
2. Add-on „MTG Binder“ auf 0.7.0 aktualisieren. In seiner Konfiguration ist „Werte an Home Assistant (MQTT)“
   eingeschaltet (Standard).
3. Add-on neu starten. Im Protokoll des Add-ons steht dann „Home Assistant: mit MQTT-Broker … verbunden“.

Benutzer und Passwort für den Broker holt das Add-on selbst beim Supervisor (`services: mqtt:want`). Es öffnet
dafür keinen Port und liest die Datenbank nur. Ist das Add-on gestoppt, zeigen alle Sensoren „nicht verfügbar“
(letzter Wille auf `mtg_binder/verfuegbar`), nie eine falsche 0.

## Sensoren

Die Entitäts-IDs entstehen aus „MTG“ und dem Namen. Wer eine ID in Home Assistant umbenennt, passt die Beispiele an.

| Entität | Inhalt | Takt |
|---|---|---|
| `binary_sensor.mtg_pc_verbunden` | Erkennung am PC aktiv (Attribute: zuletzt, Zustand) | 1 min |
| `sensor.mtg_warteschlange` | Fotos, die auf die Erkennung warten (Binder und Scan) | 1 min |
| `sensor.mtg_scan_wartend` | davon Scan-Fotos | 1 min |
| `sensor.mtg_karten_zur_prufung` | Einträge in der Prüfansicht | 1 min |
| `sensor.mtg_scan_zur_prufung` | davon im Scan-Eingang | 1 min |
| `sensor.mtg_letzter_scan` | Zeitpunkt des letzten Scan-Fotos | 1 min |
| `sensor.mtg_letzter_preisabruf` | Zeitpunkt des letzten Preisabrufs | 1 min |
| `sensor.mtg_secret_lair_kommend` | Kommende Secret-Lair-Drops | 1 min |
| `sensor.mtg_secret_lair_neuester_drop` | Zuletzt gefundener Drop; wechselt bei jedem neuen Drop | 1 min |
| `sensor.mtg_gesamtwert` | Wert der Sammlung in € (Attribute: Preisart, Preistag, Karten ohne Preis, Liste der Binder und Kisten) | 1 h |
| `sensor.mtg_veranderung_vortag` (`_3_tage`, `_30_tage`) | Veränderung in € | 1 h |
| `sensor.mtg_veranderung_vortag_prozent` (`_3_tage_prozent`, `_30_tage_prozent`) | Veränderung in % | 1 h |
| `sensor.mtg_karten` | Karten in Bindern und Kisten (Attribut: Posten) | 1 h |
| `sensor.mtg_top_5_teuerste_karten` | Preis der teuersten Karte, Liste der fünf teuersten im Attribut `karten` | 1 h |
| `sensor.mtg_top_5_gewinner` / `sensor.mtg_top_5_verlierer` | größte Steigerung / größter Verlust in € zum Vortag, Liste im Attribut `karten` | 1 h |
| `sensor.mtg_binder_<name>_wert`, `sensor.mtg_binder_<name>_karten` | je Binder | 1 h |
| `sensor.mtg_kiste_<name>_wert`, `sensor.mtg_kiste_<name>_karten` | je Kiste | 1 h |
| `sensor.mtg_deck_<name>_wert`, `sensor.mtg_deck_<name>_karten` | je Deck: Wert und Zahl der Karten, die im Deck liegen; Attribut `fehlt_kosten` (Kosten der Fehlt-Liste) | 1 h |

- Werte und Top-Listen nutzen die Preisart **Ø 7 Tage** von Cardmarket, Foil-Karten den Foil-Preis.
- Nach jedem neuen Preistag kommen die Werte sofort, nicht erst zur vollen Stunde.
- Die **Veränderung** rechnet mit dem heutigen Bestand und den Preisen des früheren Tages. Neu hinzugekommene Karten
  verfälschen sie also nicht.
- Gibt es keinen passenden früheren Tag, steht der Sensor auf „unbekannt“. Der Verlauf entsteht erst mit den
  täglichen Abrufen.
- Neue Binder, Kisten und Decks bekommen ihre Sensoren von selbst, gelöschte verschwinden wieder. Karten in Decks
  zählen zum Gesamtwert und zur Kartenzahl der Sammlung (ab Add-on 0.10.0).

## Dashboard-Karte

Dashboard bearbeiten → Karte hinzufügen → „Manuell“ und Folgendes einfügen:

```yaml
type: vertical-stack
cards:
  - type: entities
    title: MTG Sammlung
    entities:
      - entity: sensor.mtg_gesamtwert
        name: Gesamtwert
      - entity: sensor.mtg_veranderung_vortag
        name: Seit gestern
      - entity: sensor.mtg_veranderung_vortag_prozent
        name: Seit gestern (%)
      - entity: sensor.mtg_veranderung_3_tage
        name: Seit 3 Tagen
      - entity: sensor.mtg_veranderung_30_tage
        name: Seit 30 Tagen
      - entity: sensor.mtg_karten
        name: Karten
  - type: history-graph
    title: Wert der Sammlung
    hours_to_show: 720
    entities:
      - sensor.mtg_gesamtwert
  - type: entities
    title: Scan und Erkennung
    entities:
      - entity: binary_sensor.mtg_pc_verbunden
        name: PC verbunden
      - entity: sensor.mtg_warteschlange
        name: Warteschlange
      - entity: sensor.mtg_karten_zur_prufung
        name: Zur Prüfung
      - entity: sensor.mtg_scan_zur_prufung
        name: Davon Scan-Eingang
      - entity: sensor.mtg_letzter_scan
        name: Letzter Scan
        format: relative
      - entity: sensor.mtg_letzter_preisabruf
        name: Letzter Preisabruf
        format: relative
  - type: markdown
    title: Top 5
    content: |
      {% set listen = [('Teuerste Karten', 'sensor.mtg_top_5_teuerste_karten', 'preis'),
                       ('Gewinner seit gestern', 'sensor.mtg_top_5_gewinner', 'euro'),
                       ('Verlierer seit gestern', 'sensor.mtg_top_5_verlierer', 'euro')] -%}
      {% for titel, entitaet, feld in listen -%}
      **{{ titel }}**
      {% for k in state_attr(entitaet, 'karten') or [] -%}
      {{ loop.index }}. {{ k.name }} ({{ k.set }} {{ k.nummer }}{{ ', Foil' if k.foil }}): {{ '%.2f' | format(k[feld]) | replace('.', ',') }} €{{ ' (' ~ k.anzahl ~ '×)' if k.anzahl > 1 }}
      {% else -%}
      noch keine Daten
      {% endfor %}
      {% endfor %}
  - type: markdown
    title: Binder und Kisten
    content: |
      | | Karten | Wert |
      |---|---:|---:|
      {% for art, liste in [('Binder', state_attr('sensor.mtg_gesamtwert', 'binder') or []),
                            ('Kiste', state_attr('sensor.mtg_gesamtwert', 'kisten') or [])] -%}
      {% for e in liste -%}
      | {{ art }} {{ e.name }} | {{ e.karten }} | {{ ('%.2f' | format(e.wert) | replace('.', ',')) ~ ' €' if e.wert is not none else '–' }} |
      {% endfor -%}
      {% endfor %}
```

## Benachrichtigungen

In `automations.yaml` (oder über „Automation erstellen → In YAML bearbeiten“). `notify.mobile_app_DEIN_GERAET`
durch den eigenen Dienst ersetzen (Entwicklerwerkzeuge → Aktionen → „notify.mobile_app…“).

```yaml
- alias: "MTG: Karten warten auf Prüfung"
  description: Meldet einmal, wenn seit 30 Minuten Karten in der Prüfansicht liegen.
  triggers:
    - trigger: numeric_state
      entity_id: sensor.mtg_karten_zur_prufung
      above: 0
      for: "00:30:00"
  actions:
    - action: notify.mobile_app_DEIN_GERAET
      data:
        title: MTG Binder
        message: "{{ states('sensor.mtg_karten_zur_prufung') }} Karten warten auf Prüfung."
  mode: single

- alias: "MTG: Fotos warten, aber der PC ist aus"
  description: Fotos liegen seit 10 Minuten in der Warteschlange und die Erkennung am PC läuft nicht.
  triggers:
    - trigger: numeric_state
      entity_id: sensor.mtg_warteschlange
      above: 0
      for: "00:10:00"
    - trigger: state
      entity_id: binary_sensor.mtg_pc_verbunden
      to: "off"
      for: "00:10:00"
  conditions:
    - condition: numeric_state
      entity_id: sensor.mtg_warteschlange
      above: 0
    - condition: state
      entity_id: binary_sensor.mtg_pc_verbunden
      state: "off"
  actions:
    - action: notify.mobile_app_DEIN_GERAET
      data:
        title: MTG Binder
        message: "{{ states('sensor.mtg_warteschlange') }} Fotos warten auf die Erkennung, der PC ist nicht verbunden."
  mode: single
```

## Fehlersuche

- **Keine Sensoren:** Läuft Mosquitto? Steht im Protokoll des Add-ons eine Meldung „Home Assistant (MQTT): …“?
  Das Add-on versucht es jede Minute neu.
- **MQTT Explorer** oder „MQTT → Einstellungen → Auf Thema hören“ mit `mtg_binder/#` zeigt die gesendeten Werte.
- **Abschalten:** in der Konfiguration des Add-ons „Werte an Home Assistant (MQTT)“ aus und neu starten.
  Die Sensoren bleiben dann „nicht verfügbar“. Entfernen geht unter Geräte & Dienste → MQTT → Gerät „MTG“ → Löschen.
