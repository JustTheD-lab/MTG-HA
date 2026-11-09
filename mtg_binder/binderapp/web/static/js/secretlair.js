// Secret-Lair-Drops (nur im Add-on). Zeigt Zahlen zum Vergleich, keine Kaufempfehlung.
//
//   #/secretlair      Liste: kommende Drops, dann die der letzten 12 Monate
//   #/secretlair/5    ein Drop: Karten mit Vergleichswerten, Kosten, Name, Verkaufstag, Wunsch
//
// Verkaufsbeginn = Tag des Drops + Uhrzeit für alle Drops (Einstellung, Standard 17:00). Den Tag kann der Nutzer
// je Drop ändern; Drops mit Wunsch meldet das Add-on an Home Assistant (Sensor „Secret Lair nächster Wunsch“).
//
// Vergleichswert ist der teuerste Nonfoil-Reprint je Karte (Wunsch des Nutzers). Daten: web/secretlair.py.

import { holen, senden, esc, gehe, zurueck, euro, scryfallBild } from './gemeinsam.js';
import { dialog, bestaetigen } from './dialog.js';
import { angemeldet } from './anmeldung.js';

const ZURUECK = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5"'
  + ' stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M15 5l-7 7 7 7"/></svg>';
const QUELLEN = {
  hand: 'von Hand', mtgjson: 'MTGJSON', scryfall: 'Scryfall, Karten nach Nummer gruppiert, kann unvollständig sein',
};

let wurzelMerk = null;
let gezeigt = undefined;   // Nummer des Drops oder null für die Liste

const geld = (w) => (w === null || w === undefined ? '–' : euro(w));

function datum(tag) {
  if (!tag) return 'Verkaufstag unbekannt';
  return new Date(`${tag}T00:00:00`).toLocaleDateString('de-DE', { day: 'numeric', month: 'long', year: 'numeric' });
}

/** „12. November 2026, 17:00 Uhr“ aus dem Verkaufsbeginn des Servers (mit Zeitzone); ohne Beginn nur der Tag. */
function beginnText(d) {
  if (!d.beginn) return datum(d.verkauf);
  const [tag, uhr] = d.beginn.split('T');
  return `${datum(tag)}, ${uhr.slice(0, 5)} Uhr`;
}

function zeitText(z) {
  return z ? new Date(z).toLocaleString('de-DE', { dateStyle: 'medium', timeStyle: 'short' }) : 'noch nie';
}

function knopf(text, klasse = 'knopf') {
  return Object.assign(document.createElement('button'), { type: 'button', className: klasse, textContent: text });
}

function kopf(titel, unter, ziel) {
  const header = document.createElement('header');
  header.className = 'sets-kopfzeile';
  header.innerHTML = `<button type="button" class="knopf knopf-sym sets-zurueck" aria-label="Zurück">${ZURUECK}</button>`
    + `<div class="sets-titelblock"><h1 class="sets-titel">${esc(titel)}</h1>`
    + `<p class="etikett sets-zahl">${esc(unter)}</p></div>`;
  header.querySelector('.sets-zurueck').addEventListener('click', () => zurueck(ziel));
  return header;
}

function werteHtml(d) {
  const reihen = [
    ['Teuerste Reprints', geld(d.teuer)], ['Günstigste Reprints', geld(d.guenstig)],
    ['Dieser Drop heute', geld(d.wert)], ['Deine Kosten', geld(d.kosten)],
    ['Teuerste Reprints ÷ Kosten', d.faktor === null ? '–' : `${d.faktor.toLocaleString('de-DE')} ×`],
  ];
  return `<dl class="sl-werte">${reihen.map(([t, w]) => `<div><dt>${esc(t)}</dt><dd>${esc(w)}</dd></div>`).join('')}</dl>`;
}

const WUNSCH = '<span class="sl-wunsch"><span aria-hidden="true">★</span> Wunsch</span>';

function eintragHtml(d) {
  const ohne = d.ohne_vergleich ? ` · ${d.ohne_vergleich} ohne Vergleich` : '';
  const tag = d.kommend ? beginnText(d) : datum(d.verkauf);
  return `<li><a class="sl-drop${d.wunsch ? ' sl-drop-wunsch' : ''}" href="#/secretlair/${d.id}"><span class="sl-kopf">`
    + `<span class="sl-name">${esc(d.name)}</span>${d.wunsch ? WUNSCH : ''}`
    + `<span class="sl-datum">${esc(tag)} · ${d.karten} Karten${esc(ohne)}</span></span>${werteHtml(d)}</a></li>`;
}

async function einfuegen() {
  const form = document.createElement('form');
  form.id = 'sl-einfuegen';
  form.noValidate = true;
  form.innerHTML = '<label class="etikett feld-etikett" for="sl-name">Name des Drops</label>'
    + '<input id="sl-name" class="feld" type="text" maxlength="80" autocomplete="off">'
    + '<label class="etikett feld-etikett" for="sl-tag">Verkaufstag (wenn bekannt)</label>'
    + '<input id="sl-tag" class="feld" type="date">'
    + '<label class="etikett feld-etikett" for="sl-text">Karten aus der Ankündigung, je Zeile eine</label>'
    + '<textarea id="sl-text" class="feld" rows="8" spellcheck="false"></textarea>'
    + '<p class="sl-hinweis">Zum Beispiel „2x Plains“ oder „Eerie Interlude as "Beam Us Up"“.</p>'
    + '<p class="meldung" role="alert" aria-live="assertive"></p>';
  const d = dialog({ titel: 'Drop einfügen', klasse: 'dialog-klein', inhalt: form });
  const abbrechen = knopf('Abbrechen');
  abbrechen.addEventListener('click', () => d.schliessen());
  const los = Object.assign(knopf('Einfügen', 'knopf knopf-haupt'), { type: 'submit' });
  los.setAttribute('form', form.id);
  d.fuss.append(abbrechen, los);
  const meldung = form.querySelector('.meldung');
  let geprueft = '';
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const daten = { name: form.querySelector('#sl-name').value, verkauf: form.querySelector('#sl-tag').value,
                    text: form.querySelector('#sl-text').value };
    los.disabled = true;
    try {
      // Unbekannte Namen zeigt die App einmal an; ein zweiter Tipp fügt trotzdem ein
      if (geprueft !== daten.text) {
        const p = await senden('/api/secretlair', { daten: { ...daten, probe: true } });
        if (p.unbekannt.length) {
          geprueft = daten.text;
          meldung.textContent = `Nicht im Katalog: ${p.unbekannt.join(', ')}. Noch einmal tippen fügt den Drop trotzdem ein.`;
          los.disabled = false;
          return;
        }
      }
      d.schliessen(await senden('/api/secretlair', { daten }));
    } catch (f) {
      meldung.textContent = f.message;
      los.disabled = false;
    }
  });
  form.querySelector('#sl-name').focus();
  return d.fertig;
}

async function kostenFragen(titel, text, wert) {
  const form = document.createElement('form');
  form.id = 'sl-kosten';
  form.noValidate = true;
  form.innerHTML = `<p class="dialog-text">${esc(text)}</p>`
    + '<label class="etikett feld-etikett" for="sl-euro">Euro</label>'
    + `<input id="sl-euro" class="feld" type="number" min="0" max="10000" step="0.01" inputmode="decimal" value="${wert ?? ''}">`;
  const d = dialog({ titel, klasse: 'dialog-klein', inhalt: form });
  const abbrechen = knopf('Abbrechen');
  abbrechen.addEventListener('click', () => d.schliessen());
  const los = Object.assign(knopf('Speichern', 'knopf knopf-haupt'), { type: 'submit' });
  los.setAttribute('form', form.id);
  d.fuss.append(abbrechen, los);
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const roh = form.querySelector('#sl-euro').value;
    d.schliessen({ kosten: roh === '' ? null : Number(roh) });
  });
  form.querySelector('#sl-euro').focus();
  return d.fertig;
}

/** Kleiner Dialog mit einem Feld; gibt den Wert zurück, `null` für „wieder automatisch“, undefined bei Abbruch. */
function feldFragen({ titel, text, typ, wert, etikett, automatisch = null }) {
  const form = document.createElement('form');
  form.id = 'sl-feld';
  form.noValidate = true;
  form.innerHTML = `<p class="dialog-text">${esc(text)}</p>`
    + `<label class="etikett feld-etikett" for="sl-wert">${esc(etikett)}</label>`
    + `<input id="sl-wert" class="feld" type="${typ}" required>`
    + '<p class="meldung" role="alert" aria-live="assertive"></p>';
  const feld = form.querySelector('#sl-wert');
  feld.value = wert ?? '';
  const d = dialog({ titel, klasse: 'dialog-klein', inhalt: form });
  const abbrechen = knopf('Abbrechen');
  abbrechen.addEventListener('click', () => d.schliessen());
  d.fuss.append(abbrechen);
  if (automatisch) {
    const auto = knopf(automatisch);
    auto.addEventListener('click', () => d.schliessen(null));
    d.fuss.append(auto);
  }
  const los = Object.assign(knopf('Speichern', 'knopf knopf-haupt'), { type: 'submit' });
  los.setAttribute('form', form.id);
  d.fuss.append(los);
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    if (!feld.value) { form.querySelector('.meldung').textContent = 'Bitte einen Wert eintragen.'; return; }
    d.schliessen(feld.value);
  });
  feld.focus();
  return d.fertig;
}

async function listeZeigen(wurzel) {
  const r = await holen('/api/secretlair');
  const bereich = document.createElement('div');
  bereich.className = 'sets';
  const kommend = r.drops.filter((d) => d.kommend);
  // Einzelne Bonuskarten ohne Drop gehen in der Liste nur unter; bei kommenden Drops bleiben sie sichtbar
  const einzeln = (d) => d.quelle === 'scryfall' && d.karten <= 2;
  const verkauft = r.drops.filter((d) => !d.kommend && !einzeln(d));
  const ausgeblendet = r.drops.filter((d) => !d.kommend && einzeln(d)).length;
  bereich.append(kopf('Secret Lair', `${kommend.length} kommend · ${verkauft.length} seit einem Jahr`, '#/sets'));

  const werkzeug = document.createElement('div');
  werkzeug.className = 'sl-werkzeug';
  if (angemeldet()) {
    const neu = knopf('Drop einfügen');
    neu.addEventListener('click', async () => {
      const e = await einfuegen();
      if (e) gehe(`#/secretlair/${e.id}`);
    });
    const kosten = knopf(`Kosten je Drop: ${geld(r.kosten)}`);
    kosten.addEventListener('click', async () => {
      const e = await kostenFragen('Kosten je Drop', 'Was dich ein Drop ohne Foil kostet, mit Versand. Leer heißt: keine Kosten rechnen.', r.kosten);
      if (e) { await senden('/api/secretlair/kosten', { daten: e }); await neuZeigen(); }
    });
    const uhr = knopf(`Verkauf ab ${r.uhrzeit} Uhr`);
    uhr.addEventListener('click', async () => {
      const neu = await feldFragen({
        titel: 'Verkaufsbeginn', etikett: 'Uhrzeit', typ: 'time', wert: r.uhrzeit,
        text: 'Um diese Uhrzeit beginnt der Verkauf an jedem Verkaufstag. Sie gilt für alle Drops und für die Erinnerung in Home Assistant.',
      });
      if (neu) { await senden('/api/secretlair/uhrzeit', { daten: { uhrzeit: neu } }); await neuZeigen(); }
    });
    const auf = knopf('Jetzt aktualisieren');
    auf.addEventListener('click', async () => {
      auf.disabled = true;
      auf.textContent = 'Aktualisiert …';
      try { await senden('/api/secretlair/aktualisieren'); await neuZeigen(); } catch (f) {
        auf.textContent = f.message;
      }
    });
    werkzeug.append(neu, kosten, uhr, auf);
  }
  bereich.append(werkzeug);

  const hinweise = document.createElement('div');
  hinweise.innerHTML = `<p class="sl-hinweis">Zuletzt abgeglichen: ${esc(zeitText(r.stand.abgleich))}. `
    + 'Die Werte sind Summen aus Cardmarket (Ø 7 Tage, ohne Foil), keine Kaufempfehlung.'
    + `${ausgeblendet ? ` ${ausgeblendet} einzelne Karten ohne Drop sind ausgeblendet.` : ''}</p>`
    + (r.stand.fehler ? `<p class="meldung" role="alert">${esc(r.stand.fehler)}</p>` : '')
    + (r.katalog ? '' : '<p class="meldung" role="alert">Dafür braucht das Add-on den Kartenkatalog. Bitte einmal die Erkennung am PC starten.</p>');
  bereich.append(hinweise);

  for (const [titel, drops] of [['Kommend', [...kommend].reverse()], ['Bereits im Verkauf', verkauft]]) {
    if (!drops.length) continue;
    const h = document.createElement('h2');
    h.className = 'etikett sl-abschnitt';
    h.textContent = titel;
    const ul = document.createElement('ul');
    ul.className = 'sl-liste';
    ul.innerHTML = drops.map(eintragHtml).join('');
    bereich.append(h, ul);
  }
  wurzel.replaceChildren(bereich);
}

/** Bild eines Drucks mit Preis darunter. Ein Tipp vergrößert es. Die Bilder kommen direkt von Scryfall. */
function figurHtml(titel, id, name, wert, druck, leer) {
  const url = scryfallBild(id);
  const bild = url
    ? `<button type="button" class="sl-bild" data-bild="${esc(url)}" data-name="${esc(name)}" aria-label="${esc(titel)} von ${esc(name)} groß anzeigen">`
      + `<img src="${esc(url)}" alt="" loading="lazy" decoding="async"></button>`
    : `<span class="sl-bild sl-bild-leer">${esc(leer)}</span>`;
  return `<figure class="sl-figur">${bild}<figcaption><span class="sl-druck">${esc(titel)}</span>`
    + `<strong>${esc(geld(wert))}</strong><span class="sl-druck">${esc(druck)}</span></figcaption></figure>`;
}

function kartenHtml(d) {
  const druck = (set, nummer) => (set ? `${String(set).toUpperCase()} ${nummer ?? ''}`.trim() : '');
  const karte = (k) => `<li class="sl-karte"><h2 class="sl-kartenname">${k.anzahl > 1 ? `${k.anzahl}× ` : ''}${esc(k.name)}</h2>`
    + (k.nur_sld ? '<p class="sl-druck">Basic Land: nur gegen andere Secret Lairs</p>' : '')
    + '<div class="sl-figuren">'
    + figurHtml('Secret Lair', k.scryfall_id, k.name, k.preis, k.nummer ? `SLD ${k.nummer}` : '', 'Noch kein Bild')
    + figurHtml('Teuerster Reprint', k.teuer_id, k.name, k.teuer, druck(k.teuer_set, k.teuer_nummer), 'Kein Reprint')
    + figurHtml('Günstigster Reprint', k.guenstig_id, k.name, k.guenstig, druck(k.guenstig_set, k.guenstig_nummer), 'Kein Reprint')
    + '</div></li>';
  return `<ul class="sl-karten">${d.liste.map(karte).join('')}</ul>`;
}

function bildZeigen(knopf) {
  const bild = Object.assign(document.createElement('img'), { src: knopf.dataset.bild, alt: knopf.dataset.name, className: 'sl-gross' });
  dialog({ titel: knopf.dataset.name, klasse: 'dialog-klein', inhalt: bild });
}

async function dropZeigen(wurzel, id) {
  let d;
  try {
    d = await holen(`/api/secretlair/${id}`);
  } catch {
    wurzel.innerHTML = '<p class="hinweis">Diesen Drop gibt es nicht mehr.</p>';
    return;
  }
  const bereich = document.createElement('div');
  bereich.className = 'sets';
  const eigen = d.datum_eigen && d.quelle !== 'hand' ? ' · Tag von dir' : '';
  bereich.append(kopf(d.name, `${d.kommend ? beginnText(d) : datum(d.verkauf)}${eigen}`, '#/secretlair'));

  const werkzeug = document.createElement('div');
  werkzeug.className = 'sl-werkzeug';
  if (angemeldet()) {
    const name = knopf('Umbenennen');
    name.addEventListener('click', async () => {
      const neu = await nameFragen(d.name);
      if (neu) { await senden(`/api/secretlair/${id}`, { methode: 'PATCH', daten: { name: neu } }); await neuZeigen(); }
    });
    const kosten = knopf('Kosten ändern');
    kosten.addEventListener('click', async () => {
      const e = await kostenFragen('Kosten dieses Drops', 'Leer heißt: die Standardkosten gelten.', d.kosten_eigen);
      if (e) { await senden(`/api/secretlair/${id}`, { methode: 'PATCH', daten: e }); await neuZeigen(); }
    });
    const wunsch = knopf(d.wunsch ? '★ Möchte ich kaufen' : '☆ Möchte ich kaufen');
    wunsch.setAttribute('aria-pressed', String(d.wunsch));
    wunsch.setAttribute('aria-label', 'Möchte ich kaufen');   // der Stern ist nur Schmuck, der Zustand steht in aria-pressed
    wunsch.addEventListener('click', async () => {
      wunsch.disabled = true;
      try {
        await senden(`/api/secretlair/${id}`, { methode: 'PATCH', daten: { wunsch: !d.wunsch } });
        await neuZeigen();
      } finally { wunsch.disabled = false; }
    });
    const tag = knopf('Verkaufstag ändern');
    tag.addEventListener('click', async () => {
      const auto = d.quelle === 'hand' ? 'Unbekannt' : 'Wieder automatisch';
      const neu = await feldFragen({
        titel: 'Verkaufstag', etikett: 'Tag', typ: 'date', wert: d.verkauf,
        text: d.quelle === 'hand' ? 'Der Tag, an dem der Verkauf beginnt.'
          : 'Der Abgleich lässt einen geänderten Tag in Ruhe. „Wieder automatisch“ nimmt den Tag aus Scryfall.',
        automatisch: d.quelle === 'hand' ? (d.verkauf ? auto : null) : (d.datum_eigen ? auto : null),
      });
      if (neu === undefined) return;
      await senden(`/api/secretlair/${id}`, { methode: 'PATCH', daten: { verkauf: neu } });
      await neuZeigen();
    });
    werkzeug.append(wunsch, tag, name, kosten);
    if (d.quelle === 'hand') {
      const weg = knopf('Löschen', 'knopf knopf-gefahr');
      weg.addEventListener('click', async () => {
        if (await bestaetigen({ titel: 'Drop löschen?', text: `„${d.name}“ wird aus der Liste entfernt.`, ja: 'Löschen', gefahr: true })) {
          await senden(`/api/secretlair/${id}`, { methode: 'DELETE' });
          gehe('#/secretlair');
        }
      });
      werkzeug.append(weg);
    }
  }
  const inhalt = document.createElement('div');
  const v = d.verlauf;
  inhalt.innerHTML = werteHtml(d)
    + `<p class="sl-hinweis">Quelle: ${esc(QUELLEN[d.quelle] ?? d.quelle)}.${d.ohne_vergleich ? ` ${d.ohne_vergleich} Karten ohne Vergleichswert.` : ''}</p>`
    + (v && v.tage > 1 ? `<p class="sl-hinweis">Verlauf dieses Drops: ${esc(geld(v.erster[1]))} am ${esc(datum(v.erster[0]))}, `
      + `${esc(geld(v.letzter[1]))} am ${esc(datum(v.letzter[0]))} (${v.tage} Tage).</p>` : '')
    + kartenHtml(d);
  inhalt.addEventListener('click', (e) => {
    const b = e.target.closest('[data-bild]');
    if (b) bildZeigen(b);
  });
  bereich.append(werkzeug, inhalt);
  wurzel.replaceChildren(bereich);
}

async function nameFragen(alt) {
  const form = document.createElement('form');
  form.id = 'sl-umbenennen';
  form.noValidate = true;
  form.innerHTML = '<label class="etikett feld-etikett" for="sl-neu">Name</label>'
    + '<input id="sl-neu" class="feld" type="text" maxlength="80" autocomplete="off">';
  const d = dialog({ titel: 'Drop umbenennen', klasse: 'dialog-klein', inhalt: form });
  const feld = form.querySelector('#sl-neu');
  feld.value = alt;
  const abbrechen = knopf('Abbrechen');
  abbrechen.addEventListener('click', () => d.schliessen());
  const los = Object.assign(knopf('Speichern', 'knopf knopf-haupt'), { type: 'submit' });
  los.setAttribute('form', form.id);
  d.fuss.append(abbrechen, los);
  form.addEventListener('submit', (e) => { e.preventDefault(); d.schliessen(feld.value.trim()); });
  feld.focus();
  return d.fertig;
}

async function neuZeigen() {
  if (!wurzelMerk) return;
  if (gezeigt) await dropZeigen(wurzelMerk, gezeigt);
  else await listeZeigen(wurzelMerk);
}

export async function zeigen(wurzel, parameter) {
  wurzelMerk = wurzel;
  gezeigt = parameter.drop;
  await neuZeigen();
}

export async function aktualisieren(parameter) {
  if (gezeigt === parameter.drop) return;
  gezeigt = parameter.drop;
  await neuZeigen();
}

export function verlassen() {
  wurzelMerk = null;
  return Promise.resolve();
}
