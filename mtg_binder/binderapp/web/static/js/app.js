// Einstieg und Wegweiser (Router) über den Hash der Adresse.
//
//   #/                         Bücherregal
//   #/binder/3                 Binder 3 auf dem Lesetisch, Seite 1
//   #/binder/3/5               Binder 3, Seite 5 (am PC die Doppelseite, die Seite 5 enthält)
//   #/binder/3/5/karte/12      dazu Karte 12 groß
//   #/kiste/4                  Bulkkiste 4 (Karteikasten, Raster oder Liste)
//   #/kiste/4/karte/12         dazu Karte 12 groß
//   #/sammlung                 Gesamtsammlung (Binder und Kisten): dieselbe Ansicht wie die Kiste (kiste.js)
//                              mit `sammlung: true` und `kiste: null`
//   #/sammlung/karte/12        dazu Karte 12 groß
//   #/scan                     Bulk-Scan mit der Handykamera (scan.js)
//   #/scan/eingang             Scan-Eingang: Durchgänge und ihre Fotos (eingang.js)
//   #/pruefen                  Prüfansicht: unsichere Karten nacheinander
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
import { laden as sitzungLaden } from './anmeldung.js';
import { holen } from './gemeinsam.js';

const ANSICHTEN = { regal, lesetisch, kiste, pruefen, scan, eingang };
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
    return { ansicht: 'kiste', binder: null, kiste: zahl(teile[1]), sammlung: false, seite: null,
             karte: teile[2] === 'karte' ? zahl(teile[3]) : null };
  }
  if (teile[0] === 'sammlung') {
    return { ansicht: 'kiste', binder: null, kiste: null, sammlung: true, seite: null,
             karte: teile[1] === 'karte' ? zahl(teile[2]) : null };
  }
  if (teile[0] === 'scan') {   // #/scan Kamera, #/scan/eingang Scan-Eingang
    return { ansicht: teile[1] === 'eingang' ? 'eingang' : 'scan', binder: null, kiste: null, sammlung: false,
             seite: null, karte: null };
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
async function speicherPruefen() {
  try {
    const s = await holen('/api/speicher');
    if (!s.meldung) speicherAusgeblendet = false;
    speicherHinweis.querySelector('.speicher-text').textContent = s.meldung ?? '';
    speicherHinweis.hidden = !s.meldung || speicherAusgeblendet;
  } catch { /* Server nicht erreichbar: das zeigen die Ansichten selbst */ }
}
speicherPruefen();
setInterval(speicherPruefen, 60000);
