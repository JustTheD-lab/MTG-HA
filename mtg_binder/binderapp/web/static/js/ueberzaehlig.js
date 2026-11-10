// Überzählige Karten (Paket 4, 10.11.2026): was je Druck über einem Limit liegt, mit Cardmarket-Wert. Nur lesend;
// der Dialog wird aus der Gesamtsammlung geöffnet (kiste.js). Verschoben wird hier nichts.
//
//   oeffnen()  → Promise (erfüllt beim Schließen)
//
// Zählweise (sortieren/ueberzaehlig.py): je Druck, Foil getrennt, Sprache nicht; Binder, Kisten und Decks zählen.
// Standardländer nur auf Wunsch. Text zum Kopieren („2 Name (SET) 123 *F*“) und CSV zum Herunterladen.

import { adresse, esc, euro, holen, vorliebe } from './gemeinsam.js';
import { dialog } from './dialog.js';

const zahl = (n) => Number(n ?? 0).toLocaleString('de-DE');
const ORTART = { kiste: 'Kiste', deck: 'Deck', binder: 'Binder' };

function el(tag, klasse = '', html = '') {
  const e = document.createElement(tag);
  if (klasse) e.className = klasse;
  if (html) e.innerHTML = html;
  return e;
}

function limitAusVorliebe() {
  const n = Number(vorliebe('ueberzaehlig.limit'));
  return Number.isInteger(n) && n >= 1 && n <= 999 ? n : 4;
}

export function oeffnen() {
  const z = { limit: limitAusVorliebe(), laender: vorliebe('ueberzaehlig.laender') === '1', d: null, nr: 0 };
  const wurzel = el('div', 'ueber', `
    <div class="ueber-wahl">
      <label class="ueber-limit"><span>Limit je Druck</span>
        <input class="feld ueber-limit-feld" type="number" min="1" max="999" step="1" inputmode="numeric"></label>
      <label class="ueber-laender"><input type="checkbox" class="ueber-laender-feld"> Standardländer mitzählen</label>
    </div>
    <p class="ueber-erkl">Gezählt wird je Druck, Foil getrennt, Sprache nicht; Binder, Kisten und Decks zählen mit.
      Überzählig ist, was über dem Limit liegt. Hier wird nichts verschoben.</p>
    <p class="meldung ueber-meldung" role="alert"></p>
    <div class="ueber-summe" aria-live="polite"></div>
    <div class="ueber-knoepfe">
      <button type="button" class="knopf ueber-kopieren">Liste kopieren</button>
      <a class="knopf ueber-csv" download>Als CSV</a>
      <span class="ueber-status" aria-live="polite"></span>
    </div>
    <ul class="ueber-liste"></ul>`);
  const d = dialog({ titel: 'Überzählige Karten', klasse: 'ueber-dialog', voll: true, inhalt: wurzel });
  const feld = wurzel.querySelector('.ueber-limit-feld');
  const laender = wurzel.querySelector('.ueber-laender-feld');
  const meldung = wurzel.querySelector('.ueber-meldung');
  const summe = wurzel.querySelector('.ueber-summe');
  const liste = wurzel.querySelector('.ueber-liste');
  const status = wurzel.querySelector('.ueber-status');
  const csv = wurzel.querySelector('.ueber-csv');
  const kopieren = wurzel.querySelector('.ueber-kopieren');
  feld.value = String(z.limit);
  laender.checked = z.laender;
  const zu = el('button', 'knopf', 'Schließen');
  zu.type = 'button';
  zu.addEventListener('click', () => d.schliessen(undefined));
  d.fuss.append(zu);

  const abfrage = () => `limit=${z.limit}${z.laender ? '&laender=1' : ''}`;

  async function laden() {
    const nr = ++z.nr;
    meldung.textContent = '';
    status.textContent = '';
    csv.href = adresse(`/api/sammlung/ueberzaehlig?${abfrage()}&format=csv`);
    csv.setAttribute('download', `ueberzaehlige-${z.limit}.csv`);
    summe.textContent = 'Lädt …';
    let daten;
    try {
      daten = await holen(`/api/sammlung/ueberzaehlig?${abfrage()}`);
    } catch (f) {
      if (nr === z.nr) { summe.textContent = ''; meldung.textContent = 'Die Liste ließ sich nicht laden.'; }
      return;
    }
    if (nr !== z.nr) return;
    z.d = daten;
    const ohne = daten.ohne_preis ? ` · ${zahl(daten.ohne_preis)} ohne Preis` : '';
    summe.innerHTML = daten.drucke
      ? `<strong>${zahl(daten.anzahl)} Karten</strong> in ${zahl(daten.drucke)} Drucken · `
        + `<strong>${esc(euro(daten.wert))}</strong> <span class="ueber-art">(${esc(daten.preisart_name)}`
        + `${daten.tag ? `, Stand ${esc(daten.tag)}` : ''})</span>${esc(ohne)}`
      : `Nichts liegt über dem Limit von ${zahl(daten.limit)}.`;
    kopieren.disabled = !daten.drucke;
    csv.toggleAttribute('aria-disabled', !daten.drucke);
    liste.replaceChildren(...daten.karten.map((e) => {
      const li = el('li', 'ueber-zeile');
      const orte = e.orte.map((o) => `${ORTART[o.art] ?? ''} ${o.name} (${zahl(o.anzahl)})`).join(', ');
      li.innerHTML = `<span class="ueber-anzahl">${zahl(e.ueber)}×</span>`
        + `<span class="ueber-karte"><span class="ueber-name">${esc(e.name)}${e.foil ? ' <span class="ueber-foil">Foil</span>' : ''}</span>`
        + `<span class="ueber-druck">${esc(e.set)} ${esc(e.nummer ?? '')} · ${zahl(e.gesamt)} da · ${esc(orte)}</span></span>`
        + `<span class="ueber-wert">${esc(euro(e.wert))}<small>${e.preis == null ? 'ohne Preis' : `je ${esc(euro(e.preis))}`}</small></span>`;
      return li;
    }));
  }

  feld.addEventListener('change', () => {
    const n = Number(feld.value);
    if (!Number.isInteger(n) || n < 1 || n > 999) {
      meldung.textContent = 'Limit: eine ganze Zahl von 1 bis 999.';
      return;
    }
    z.limit = n;
    vorliebe('ueberzaehlig.limit', String(n));
    laden();
  });
  laender.addEventListener('change', () => {
    z.laender = laender.checked;
    vorliebe('ueberzaehlig.laender', z.laender ? '1' : '0');
    laden();
  });
  kopieren.addEventListener('click', async () => {
    if (!z.d?.text) return;
    try {
      await navigator.clipboard.writeText(z.d.text);
      status.textContent = 'Kopiert.';
    } catch {
      status.textContent = 'Kopieren ging nicht; bitte die CSV nehmen.';
    }
  });
  laden();
  return d.fertig;
}
