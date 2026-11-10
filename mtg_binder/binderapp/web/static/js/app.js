// Einstieg und Wegweiser (Router) über den Hash der Adresse.
//
//   #/                         Bücherregal
//   #/binder/3                 Binder 3 auf dem Lesetisch, Seite 1
//   #/binder/3/5               Binder 3, Seite 5 (am PC die Doppelseite, die Seite 5 enthält)
//   #/binder/3/5/karte/12      dazu Karte 12 groß
//   #/kiste/4                  Bulkkiste 4 (Karteikasten, Raster oder Liste)
//   #/kiste/4/karte/12         dazu Karte 12 groß
//   #/kiste/4/fach/2           Fach 2 der Kiste herausgezogen („ohne“: Karten ohne Fach), dazu /karte/12
//   #/sammlung                 Gesamtsammlung (Binder und Kisten): dieselbe Ansicht wie die Kiste (kiste.js)
//                              mit `sammlung: true` und `kiste: null`
//   #/sammlung/karte/12        dazu Karte 12 groß
//   #/scan                     Bulk-Scan mit der Handykamera (scan.js)
//   #/scan/eingang             Scan-Eingang: Durchgänge und ihre Fotos (eingang.js)
//   #/regeln                   Sortierregeln: wohin neue Karten einsortiert werden (regeln.js)
//   #/sets                     Sets: Übersicht mit Vollständigkeit der Sammlung (sets.js)
//   #/set/ecl                  ein Set: Drucke und Namen, vorhanden oder fehlend (sets.js; Code nur [a-z0-9]{2,8})
//   #/secretlair               Secret-Lair-Drops mit Vergleichswerten (secretlair.js, nur im Add-on)
//   #/secretlair/5             ein Drop mit seinen Karten
//   #/dashboard                Dashboard: Werte, Graphen und Bilder der ganzen Sammlung (dashboard.js)
//   #/dashboard/karte/12       dazu Karte 12 groß
//   #/proxy                    Proxys: Deckliste prüfen, druckfertiges PDF erzeugen (proxy.js, nur im Add-on)
//   #/proxy/deck/7             dazu die Liste aus Deck 7 übernehmen (fehlende und als Proxy markierte Karten)
//   #/kaufliste                Kaufliste mit Wunschpreisen (kaufliste.js, Paket 7)
//   #/deck/7                   Deck 7: Abgleich mit der Sammlung, Fehlt-Liste (deck.js)
//   #/deck/7/bauen             Entnahme für Deck 7 (Karten aus Binder und Kisten holen, deckbau.js)
//   #/deck/7/zerlegen[?karten=1,2]   Deck zerlegen: Karten zurücklegen (deckbau.js)
//   #/pruefen                 Prüfansicht: unsichere Karten nacheinander
//   #/pruefen/karte/12         dazu Karte 12 groß
//
// Jede Ansicht (regal.js, lesetisch.js, kiste.js, pruefen.js) exportiert:
//   zeigen(wurzel, parameter, vorher)  baut die Ansicht in `wurzel` auf; `vorher` = Parameter der
//                                      vorigen Ansicht oder null (für Übergänge, z. B. Binder gleitet zurück)
//   aktualisieren(parameter)           optional: gleiche Ansicht, andere Parameter (Blättern)
//   verlassen(naechste) -> Promise     optional: Abschiedsanimation, bevor die nächste Ansicht kommt
// Die Kartenansicht (karte.js) ist eine Ebene darüber: oeffnen(kartenId), schliessen() -> Promise.

import * as regal from './regal.js';
import * as lesetisch from './lesetisch.js';
import * as kiste from './kiste.js';
import * as pruefen from './pruefen.js';
import * as karte from './karte.js';
import * as scan from './scan.js';
import * as eingang from './eingang.js';
import * as regeln from './regeln.js';
import * as sets from './sets.js';
import * as secretlair from './secretlair.js';
import * as deck from './deck.js';
import * as dashboard from './dashboard.js';
import * as proxy from './proxy.js';
import * as kaufliste from './kaufliste.js';
import { laden as sitzungLaden } from './anmeldung.js';
import { holen } from './gemeinsam.js';

// `sets` (Übersicht) und `set` (ein Set) sind dasselbe Modul; es unterscheidet sie am Parameter `set`.
const ANSICHTEN = { regal, lesetisch, kiste, pruefen, scan, eingang, regeln, sets, set: sets, deck, secretlair, dashboard, proxy, kaufliste };
const buehne = document.getElementById('buehne');
let aktuell = null;  // { name, parameter }
let laeuft = Promise.resolve();

export function lesen(hash) {
  const teile = hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  const zahl = (wert) => (/^\d+$/.test(wert ?? '') ? Number(wert) : null);
  if (teile[0] === 'binder' && zahl(teile[1]) !== null) {
    return {
      ansicht: 'lesetisch',
      binder: zahl(teile[1]),
      kiste: null,
      sammlung: false,
      seite: zahl(teile[2]) ?? 1,
      karte: teile[3] === 'karte' ? zahl(teile[4]) : null,
    };
  }
  if (teile[0] === 'kiste' && zahl(teile[1]) !== null) {
    const fach = teile[2] === 'fach' && /^(\d{1,3}|ohne)$/.test(teile[3] ?? '') ? teile[3] : null;
    const rest = fach === null ? 2 : 4;
    return { ansicht: 'kiste', binder: null, kiste: zahl(teile[1]), sammlung: false, seite: null, fach,
             karte: teile[rest] === 'karte' ? zahl(teile[rest + 1]) : null };
  }
  if (teile[0] === 'sammlung') {
    return { ansicht: 'kiste', binder: null, kiste: null, sammlung: true, seite: null,
             karte: teile[1] === 'karte' ? zahl(teile[2]) : null };
  }
  if (teile[0] === 'scan') {   // #/scan Kamera, #/scan/eingang Scan-Eingang
    return { ansicht: teile[1] === 'eingang' ? 'eingang' : 'scan', binder: null, kiste: null, sammlung: false,
             seite: null, karte: null };
  }
  if (teile[0] === 'regeln') {
    return { ansicht: 'regeln', binder: null, kiste: null, sammlung: false, seite: null, karte: null };
  }
  if (teile[0] === 'sets') {
    return { ansicht: 'sets', binder: null, kiste: null, sammlung: false, seite: null, karte: null, set: null };
  }
  if (teile[0] === 'set' && /^[a-z0-9]{2,8}$/.test(teile[1] ?? '')) {
    return { ansicht: 'set', binder: null, kiste: null, sammlung: false, seite: null, karte: null, set: teile[1] };
  }
  if (teile[0] === 'secretlair') {
    return { ansicht: 'secretlair', binder: null, kiste: null, sammlung: false, seite: null, karte: null,
             drop: zahl(teile[1]) };
  }
  if (teile[0] === 'dashboard') {
    return { ansicht: 'dashboard', binder: null, kiste: null, sammlung: false, seite: null,
             karte: teile[1] === 'karte' ? zahl(teile[2]) : null };
  }
  if (teile[0] === 'kaufliste') {
    return { ansicht: 'kaufliste', binder: null, kiste: null, sammlung: false, seite: null, karte: null };
  }
  if (teile[0] === 'proxy') {
    return { ansicht: 'proxy', binder: null, kiste: null, sammlung: false, seite: null, karte: null,
             deck: teile[1] === 'deck' ? zahl(teile[2]) : null };
  }
  if (teile[0] === 'deck' && zahl(teile[1]) !== null) {
    // #/deck/7/bauen (Entnahme) und #/deck/7/zerlegen[?karten=1,2] (Zerlegen)
    const modus = teile[2] === 'bauen' || teile[2]?.split('?')[0] === 'zerlegen' ? teile[2].split('?')[0] : null;
    const abfrage = new URLSearchParams(hash.split('?')[1] ?? '');
    const ids = (abfrage.get('karten') ?? '').split(',').filter((x) => /^\d+$/.test(x)).map(Number);
    return { ansicht: 'deck', binder: null, kiste: null, sammlung: false, seite: null, karte: null, deck: zahl(teile[1]),
             modus, karten: modus === 'zerlegen' && ids.length ? ids : null };
  }
  if (teile[0] === 'pruefen') {
    return { ansicht: 'pruefen', binder: null, kiste: null, sammlung: false, seite: null,
             karte: teile[1] === 'karte' ? zahl(teile[2]) : null };
  }
  return { ansicht: 'regal', binder: null, kiste: null, sammlung: false, seite: null, karte: null };
}

async function weiterleiten() {
  const p = lesen(location.hash);
  const modul = ANSICHTEN[p.ansicht];
  const gleich = aktuell && aktuell.name === p.ansicht && aktuell.parameter.binder === p.binder
    && aktuell.parameter.kiste === p.kiste && aktuell.parameter.sammlung === p.sammlung;
  if (!p.karte) await karte.schliessen();
  if (gleich && modul.aktualisieren) {
    await modul.aktualisieren(p);
  } else if (!gleich) {
    const vorher = aktuell ? { ...aktuell.parameter } : null;
    if (aktuell && ANSICHTEN[aktuell.name].verlassen) await ANSICHTEN[aktuell.name].verlassen(p);
    buehne.replaceChildren();
    buehne.dataset.ansicht = p.ansicht;
    try {
      await modul.zeigen(buehne, p, vorher);
    } catch (fehler) {
      console.error(fehler);
      buehne.innerHTML = '<p class="hinweis">Das Regal ließ sich nicht laden. Läuft der Server?</p>';
    }
  }
  aktuell = { name: p.ansicht, parameter: p };
  if (p.karte) await karte.oeffnen(p.karte);
}

// Navigationen nacheinander abarbeiten, damit sich Animationen nicht überlagern.
window.addEventListener('hashchange', () => { laeuft = laeuft.then(weiterleiten, weiterleiten); });
laeuft = laeuft.then(weiterleiten);
sitzungLaden();  // nicht abwarten: die Ansichten zeigen Schreibknöpfe, sobald 'binder:sitzung' kommt

// Add-on: Ist das Netzlaufwerk für Fotos und Bilder weg, steht oben ein Hinweis (Lesen geht weiter).
// Er lässt sich ausblenden (er liegt über der Kopfleiste) und kommt erst nach einem neuen Ausfall wieder.
const speicherHinweis = Object.assign(document.createElement('div'), { className: 'speicher-hinweis', hidden: true });
speicherHinweis.setAttribute('role', 'status');
speicherHinweis.innerHTML = '<p class="speicher-text"></p>'
  + '<button type="button" class="speicher-zu" aria-label="Hinweis ausblenden">×</button>';
document.body.prepend(speicherHinweis);
let speicherAusgeblendet = false;
speicherHinweis.querySelector('.speicher-zu').addEventListener('click', () => {
  speicherAusgeblendet = true;
  speicherHinweis.hidden = true;
});
// Nach einem Update läuft eine offene Seite mit dem alten Code weiter, in der Home-Assistant-App oft lange. Meldet das
// Add-on eine andere Version als beim Laden, bietet die Seite das Neuladen an (Befund 06.11.2026).
const versionHinweis = Object.assign(document.createElement('div'), { className: 'speicher-hinweis', hidden: true });
versionHinweis.setAttribute('role', 'status');
versionHinweis.innerHTML = '<p class="speicher-text">Eine neue Version der App ist da.</p>'
  + '<button type="button" class="knopf version-neu">Neu laden</button>';
document.body.prepend(versionHinweis);
versionHinweis.querySelector('.version-neu').addEventListener('click', () => location.reload());
let geladeneVersion = null;

async function speicherPruefen() {
  try {
    const s = await holen('/api/speicher');
    if (!s.meldung) speicherAusgeblendet = false;
    speicherHinweis.querySelector('.speicher-text').textContent = s.meldung ?? '';
    speicherHinweis.hidden = !s.meldung || speicherAusgeblendet;
    geladeneVersion ??= s.version ?? null;
    versionHinweis.hidden = !s.version || s.version === geladeneVersion;
  } catch { /* Server nicht erreichbar: das zeigen die Ansichten selbst */ }
}
speicherPruefen();
setInterval(speicherPruefen, 60000);
// Zurück in die App: gleich nachsehen, statt bis zu einer Minute zu warten
document.addEventListener('visibilitychange', () => { if (!document.hidden) speicherPruefen(); });
