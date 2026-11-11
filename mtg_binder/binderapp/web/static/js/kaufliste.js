// Kaufliste mit Preisalarm (#/kaufliste, Paket 7): fehlende Karten gewählter Decks und eigene Einträge, mit Summe,
// Wunschpreis je Karte (Vorschlag 90 % des heutigen Preises) und Treffern „unter Wunschpreis“ (auch als Sensor in
// Home Assistant). Keine Kaufempfehlung, nur Zahlen. Jede Änderung ist ein Schritt mit Rückgängig.
// Schnittstelle: web/kaufliste.py. Bausteine (Zeilen, Schalter, Meldung) aus deck.js und deck.css.

import { holen, senden, esc, zurueck, euro, vorliebe } from './gemeinsam.js';
import { angemeldet } from './anmeldung.js';
import { dialogOffen } from './dialog.js';
import { el, bildHtml, bilderAbsichern, linksAnschliessen, toastZeigen, ZURUECK } from './deck.js';

const zahl = (n) => Number(n ?? 0).toLocaleString('de-DE');
const HINWEIS_CARDMARKET = 'Für Cardmarket: Wants → Aus Text hinzufügen.';
let Z = null;

// Link je Karte auf die Produktseite des günstigsten Drucks (Produktnummer wie bei Secret Lair, keine geratenen
// Adressen), mit den Angebotsfiltern von Cardmarket als Adressparameter (Vorlieben im Browser). Kein Preisabruf
// von der Website: Cardmarket steht hinter einer Bot-Prüfung, die Preise kommen weiter aus der täglichen Preisdatei.
const CARDMARKET = 'https://www.cardmarket.com/de/Magic/Products';
const CM_FILTER = {
  land: { name: 'Verkäuferland', param: 'sellerCountry', werte: [['', 'Alle'], ['7', 'Deutschland'], ['1', 'Österreich'],
    ['4', 'Schweiz'], ['23', 'Niederlande'], ['2', 'Belgien'], ['20', 'Luxemburg'], ['12', 'Frankreich'], ['17', 'Italien'],
    ['10', 'Spanien'], ['8', 'Dänemark'], ['25', 'Polen'], ['6', 'Tschechien']] },
  sprache: { name: 'Kartensprache', param: 'language', werte: [['', 'Alle'], ['1', 'Englisch'], ['3', 'Deutsch'],
    ['1,3', 'Englisch oder Deutsch'], ['2', 'Französisch'], ['5', 'Italienisch'], ['4', 'Spanisch'], ['7', 'Japanisch']] },
  zustand: { name: 'Mindestzustand', param: 'minCondition', werte: [['', 'Alle'], ['1', 'Mint'], ['2', 'Near Mint'],
    ['3', 'Excellent'], ['4', 'Good'], ['5', 'Light Played'], ['6', 'Played']] },
};

function cmFilter() {
  const f = {};
  for (const [k, d] of Object.entries(CM_FILTER)) {
    const w = vorliebe(`kaufliste.cm.${k}`) ?? '';
    f[k] = d.werte.some(([v]) => v === w) ? w : '';
  }
  return f;
}

function cmAdresse(p) {
  const q = new URLSearchParams({ idProduct: String(p.cardmarket_id) });
  const f = cmFilter();
  for (const [k, d] of Object.entries(CM_FILTER)) if (f[k]) q.set(d.param, f[k]);
  q.set('isFoil', p.foil ? 'Y' : 'N');
  return `${CARDMARKET}?${q.toString().replaceAll('%2C', ',')}`;
}

function cmLinkHtml(p) {
  if (p.cardmarket_id == null) return '';
  return `<a class="knopf kauf-cm-link" href="${esc(cmAdresse(p))}" target="_blank" rel="noopener noreferrer">Cardmarket`
    + `<span aria-hidden="true"> ↗</span><span class="nur-vorleser"> für ${esc(p.name)} (öffnet Cardmarket)</span></a>`;
}

function cmFilterHtml() {
  const f = cmFilter();
  const aktiv = Object.values(f).filter(Boolean).length;
  return `<details class="kauf-cm"><summary class="etikett">Cardmarket-Filter${aktiv ? ` (${aktiv} aktiv)` : ''}</summary>`
    + '<div class="kauf-formular-zeile">'
    + Object.entries(CM_FILTER).map(([k, d]) => `<label><span class="etikett">${esc(d.name)}</span>`
      + `<select class="feld kauf-cm-wahl" data-filter-art="${k}">`
      + d.werte.map(([v, t]) => `<option value="${esc(v)}"${v === f[k] ? ' selected' : ''}>${esc(t)}</option>`).join('')
      + '</select></label>').join('')
    + '</div><p class="deck-hinweis">Gilt für die Knöpfe „Cardmarket“ je Karte; Foil-Einträge suchen nur Foil.</p></details>';
}

/** „1,5“ oder „1.50“ → 1.5; leer → null; sonst undefined (ungültig). */
function preisLesen(text) {
  const t = String(text ?? '').trim().replace(/\s*€$/, '').replace(',', '.');
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) && n >= 0 && /^\d+(\.\d{0,2})?$/.test(t) ? n : undefined;
}
const preisFeld = (w) => (w == null ? '' : Number(w).toFixed(2).replace('.', ','));

function kopfHtml(a) {
  const s = a.summe;
  const teile = [`<span class="deck-zahl-da">${esc(zahl(s.karten))} Karten</span>`,
    `<span>Summe ${esc(euro(s.wert))}</span>`];
  if (s.ohne_preis) teile.push(`<span>${esc(zahl(s.ohne_preis))} ohne Preis</span>`);
  return `<p class="deck-zahlen">${teile.join('')}</p>`
    + `<p class="deck-zahlen kauf-treffer-zeile${s.treffer ? ' kauf-treffer-zeile--da' : ''}">`
    + `<span>Unter Wunschpreis: ${esc(zahl(s.treffer))} ${s.treffer === 1 ? 'Karte' : 'Karten'}`
    + `${s.treffer ? ` (${esc(zahl(s.treffer_karten))} Stück, ${esc(euro(s.treffer_wert))})` : ''}</span></p>`;
}

function deckHtml(d, schreiben) {
  const fehlt = d.an && d.fehlt != null ? `<span class="etikett">fehlt ${esc(zahl(d.fehlt))} · ${esc(euro(d.fehlt_wert))}</span>` : '';
  return `<li class="kauf-deck"><label class="deck-schalter"><input type="checkbox" role="switch" data-deck="${Number(d.id)}"`
    + `${d.an ? ' checked' : ''}${schreiben ? '' : ' disabled'}><span class="deck-schalter-spur" aria-hidden="true"></span>`
    + `<span>${esc(d.name)}</span></label>${fehlt}</li>`;
}

function zeileHtml(p, i, schreiben) {
  const druck = p.genau ? `${esc((p.set_code ?? '').toUpperCase())} ${esc(p.nummer ?? '')}` : 'beliebiger Druck';
  const herkunft = [...p.decks.map((d) => `<a href="#/deck/${Number(d.id)}">${esc(d.name)}</a> ${esc(zahl(d.anzahl))}`),
    ...(p.eigen ? [`eigener Eintrag ${esc(zahl(p.eigen))}`] : [])].join(' · ');
  const wunschFeld = schreiben
    ? `<label class="kauf-wunsch"><span class="etikett">Wunschpreis</span><input class="feld kauf-wunsch-feld" inputmode="decimal"`
      + ` autocomplete="off" data-i="${i}" value="${esc(preisFeld(p.wunschpreis))}"`
      + ` placeholder="${esc(p.vorschlag != null ? preisFeld(p.vorschlag) : '€')}" aria-label="Wunschpreis für ${esc(p.name)} in Euro"></label>`
      + (p.vorschlag != null ? `<button type="button" class="knopf kauf-vorschlag" data-i="${i}">90 % = ${esc(euro(p.vorschlag))}</button>` : '')
    : `<span>Wunschpreis ${esc(euro(p.wunschpreis))}</span>`;
  const eigen = schreiben && p.eintrag_id && p.eigen
    ? `<label class="kauf-anzahl"><span class="etikett">Anzahl</span><input class="feld kauf-anzahl-feld" type="number" min="1" max="999"`
      + ` data-i="${i}" value="${Number(p.eigen)}" aria-label="Anzahl eigener Eintrag ${esc(p.name)}"></label>`
      + `<button type="button" class="knopf kauf-entfernen" data-i="${i}">Entfernen</button>`
    : '';
  return `<li class="deck-zeile kauf-zeile${p.unter_wunsch ? ' kauf-zeile--treffer' : ''}">${bildHtml(p)}`
    + '<div class="deck-zeile-text">'
    + `<p class="deck-zeile-kopf"><span class="deck-anzahl">${esc(zahl(p.anzahl))}×</span>`
    + `<span class="deck-name">${esc(p.name)}</span>${p.foil ? '<span class="deck-foil" title="Foil">Foil</span>' : ''}</p>`
    + `<p class="deck-zeile-unter etikett"><span>${druck}</span><span>${herkunft}</span></p>`
    + `<p class="deck-zeile-unter"><span>Preis ${esc(euro(p.preis))}${p.anzahl > 1 && p.wert != null ? ` · zusammen ${esc(euro(p.wert))}` : ''}</span>`
    + (p.unter_wunsch ? '<span class="kauf-marke">unter Wunschpreis</span>' : '') + '</p>'
    + `<div class="kauf-felder">${wunschFeld}${eigen}${cmLinkHtml(p)}</div></div></li>`;
}

function zeichnen() {
  const z = Z;
  const a = z.daten;
  const schreiben = angemeldet();
  z.stand.textContent = `Preise: ${a.preisart_name}${a.preistag ? ` · Stand ${a.preistag.split('-').reverse().join('.')}` : ' · noch keine Preise'}`;
  z.kopf.innerHTML = kopfHtml(a);
  z.hinzu.hidden = !schreiben;
  if (!schreiben) z.formular.hidden = true;
  z.decks.innerHTML = a.decks.length ? a.decks.map((d) => deckHtml(d, schreiben)).join('')
    : '<li class="deck-hinweis">Noch keine Decks. Decks legst du im Regal an.</li>';
  for (const b of z.filter.querySelectorAll('[data-filter]')) b.setAttribute('aria-pressed', String(b.dataset.filter === z.ansicht));
  const karten = z.ansicht === 'treffer' ? a.karten.filter((p) => p.unter_wunsch) : a.karten;
  z.liste.innerHTML = karten.length
    ? `<ul class="deck-zeilen">${karten.map((p) => zeileHtml(p, a.karten.indexOf(p), schreiben)).join('')}</ul>`
    : `<p class="deck-hinweis">${z.ansicht === 'treffer' ? 'Keine Karte liegt unter ihrem Wunschpreis.'
      : 'Die Kaufliste ist leer. Schalte oben ein Deck ein oder füge eine Karte hinzu.'}</p>`;
}

async function laden(z, { still = false } = {}) {
  try {
    z.daten = await holen('/api/kaufliste');
  } catch (f) {
    if (Z !== z) return;
    z.meldung.textContent = /: 409$/.test(String(f.message)) ? 'Die Kaufliste braucht eine neuere Datenbank. Bitte die App neu starten.'
      : 'Die Kaufliste ließ sich nicht laden. Läuft der Server?';
    z.liste.replaceChildren();
    return;
  }
  if (Z !== z) return;
  z.meldung.textContent = '';
  const y = window.scrollY;
  zeichnen();
  if (still) window.scrollTo({ top: y, behavior: 'instant' });
}

/** Schreibt, lädt neu und zeigt die Meldung mit Rückgängig. */
async function schreiben(z, pfad, optionen, text) {
  z.meldung.textContent = '';
  let a;
  try {
    a = await senden(pfad, optionen);
  } catch (f) {
    toastZeigen(z.toast, f.message, { art: 'fehler' });
    await laden(z, { still: true });
    return false;
  }
  await laden(z, { still: true });
  if (a?.schritt) toastZeigen(z.toast, text, { schritt: a.schritt, nachRueck: () => laden(z, { still: true }) });
  return true;
}

function wunschSetzen(z, p, wert) {
  if (wert === (p.wunschpreis ?? null)) return;
  const text = wert == null ? `Wunschpreis für ${p.name} entfernt.` : `Wunschpreis für ${p.name}: ${euro(wert)}.`;
  return schreiben(z, '/api/kaufliste/wunsch',
    { methode: 'PUT', daten: { schluessel: p.schluessel, foil: p.foil, wunschpreis: wert } }, text);
}

// ---------------------------------------------------------------- Karte hinzufügen

function formularHtml() {
  return '<div class="kauf-formular-zeile"><label class="kauf-suche"><span class="etikett">Karte</span>'
    + '<input class="feld kauf-suche-feld" type="search" autocomplete="off" placeholder="Name eingeben" aria-label="Kartenname suchen"></label></div>'
    + '<ul class="kauf-treffer" role="listbox" aria-label="Gefundene Karten"></ul>'
    + '<div class="kauf-formular-zeile kauf-gewaehlt" hidden><p class="kauf-gewaehlt-name"></p>'
    + '<label><span class="etikett">Druck</span><select class="feld kauf-druck"></select></label>'
    + '<label class="kauf-foil-wahl"><input type="checkbox" class="kauf-foil-feld"> Foil</label>'
    + '<label><span class="etikett">Anzahl</span><input class="feld kauf-neu-anzahl" type="number" min="1" max="999" value="1"></label>'
    + '<label><span class="etikett">Wunschpreis (optional)</span><input class="feld kauf-neu-wunsch" inputmode="decimal" placeholder="€"></label>'
    + '<button type="button" class="knopf knopf-haupt kauf-neu-los">Hinzufügen</button></div>';
}

function formularEinrichten(z) {
  const f = z.formular;
  f.innerHTML = formularHtml();
  const feld = f.querySelector('.kauf-suche-feld');
  const treffer = f.querySelector('.kauf-treffer');
  const gewaehlt = f.querySelector('.kauf-gewaehlt');
  const druck = f.querySelector('.kauf-druck');
  let uhr = 0;
  let wahl = null;
  feld.addEventListener('input', () => {
    clearTimeout(uhr);
    uhr = setTimeout(async () => {
      const text = feld.value.trim();
      if (text.length < 2) { treffer.replaceChildren(); return; }
      try {
        const a = await holen(`/api/pruefen/namen?q=${encodeURIComponent(text)}`);
        if (!a.verfuegbar) { treffer.innerHTML = '<li class="deck-hinweis">Ohne Kartenkatalog gibt es keine Suche.</li>'; return; }
        treffer.innerHTML = a.treffer.slice(0, 8).map((t) => `<li><button type="button" class="knopf kauf-treffer-knopf"`
          + ` data-oracle="${esc(t.oracle_id)}" data-name="${esc(t.name)}">${esc(t.name)}</button></li>`).join('')
          || '<li class="deck-hinweis">Nichts gefunden.</li>';
      } catch { treffer.innerHTML = '<li class="deck-hinweis">Die Suche ging nicht.</li>'; }
    }, 200);
  });
  treffer.addEventListener('click', async (e) => {
    const b = e.target.closest('.kauf-treffer-knopf');
    if (!b) return;
    wahl = { oracle_id: b.dataset.oracle, name: b.dataset.name };
    treffer.replaceChildren();
    feld.value = wahl.name;
    gewaehlt.hidden = false;
    f.querySelector('.kauf-gewaehlt-name').textContent = wahl.name;
    druck.innerHTML = '<option value="">beliebiger Druck (günstigster)</option>';
    try {
      const a = await holen(`/api/pruefen/drucke?oracle_id=${encodeURIComponent(wahl.oracle_id)}`);
      const gesehen = new Set();
      for (const d of a.drucke ?? []) {
        const s = `${d.set_code}|${d.nummer}`;
        if (gesehen.has(s) || d.sprache !== 'en') continue;
        gesehen.add(s);
        druck.append(Object.assign(document.createElement('option'), { value: d.scryfall_id,
          textContent: `${(d.set_code ?? '').toUpperCase()} ${d.nummer} · ${d.set_name ?? ''}` }));
      }
    } catch { /* nur „beliebiger Druck“ */ }
    druck.focus();
  });
  f.querySelector('.kauf-neu-los').addEventListener('click', async () => {
    if (!wahl) return;
    const anzahl = Number(f.querySelector('.kauf-neu-anzahl').value);
    const wunsch = preisLesen(f.querySelector('.kauf-neu-wunsch').value);
    if (!Number.isInteger(anzahl) || anzahl < 1 || anzahl > 999) { z.meldung.textContent = 'Die Anzahl muss von 1 bis 999 gehen.'; return; }
    if (wunsch === undefined) { z.meldung.textContent = 'Der Wunschpreis ist keine gültige Zahl (z. B. 1,50).'; return; }
    const daten = { anzahl, foil: f.querySelector('.kauf-foil-feld').checked, wunschpreis: wunsch,
      ...(druck.value ? { scryfall_id: druck.value } : { oracle_id: wahl.oracle_id }) };
    const ok = await schreiben(z, '/api/kaufliste/karte', { daten }, `${anzahl}× ${wahl.name} in der Kaufliste.`);
    if (ok) { formularEinrichten(z); z.formular.hidden = true; z.hinzu.setAttribute('aria-expanded', 'false'); }
  });
}

// ---------------------------------------------------------------- Ansicht

function aufraeumen() {
  if (!Z) return;
  window.removeEventListener('binder:sitzung', Z.sitzung);
  document.removeEventListener('keydown', Z.taste);
  Z = null;
}

export async function zeigen(wurzel) {
  aufraeumen();
  const bereich = el('section', 'deck kauf');
  bereich.setAttribute('aria-label', 'Kaufliste');
  bereich.innerHTML = '<header class="deck-kopfzeile">'
    + `<button type="button" class="knopf knopf-sym deck-zurueck" aria-label="Zurück ins Regal">${ZURUECK}</button>`
    + '<div class="deck-titelblock"><h1 class="deck-titel">Kaufliste</h1><p class="etikett deck-quelle-zeile kauf-stand"></p></div></header>'
    + '<div class="deck-karte-kopf"><div class="kauf-kopf"></div>'
    + '<div class="deck-knoepfe"><button type="button" class="knopf kauf-kopieren">Liste kopieren</button>'
    + '<button type="button" class="knopf kauf-hinzu" aria-expanded="false" hidden>Karte hinzufügen</button></div>'
    + '<div class="kauf-formular" hidden></div>'
    + '<div class="kauf-cm-bereich"></div>'
    + '<div><h2 class="etikett kauf-titel">Decks in der Kaufliste</h2><ul class="kauf-decks"></ul></div></div>'
    + '<div class="deck-toast" role="status" hidden></div>'
    + '<p class="deck-hinweis deck-meldung" role="alert"></p>'
    + '<p class="nur-vorleser" role="status" aria-live="polite"></p>'
    + '<div class="deck-segmente kauf-filter" role="group" aria-label="Anzeigen"><span class="etikett deck-segmente-titel">Zeigen</span>'
    + '<button type="button" class="knopf deck-pille" data-filter="alle" aria-pressed="true">Alle</button>'
    + '<button type="button" class="knopf deck-pille" data-filter="treffer" aria-pressed="false">Unter Wunschpreis</button></div>'
    + '<div class="deck-liste kauf-liste"><p class="deck-hinweis">Kaufliste wird geladen …</p></div>';
  wurzel.replaceChildren(bereich);
  linksAnschliessen(bereich);
  bilderAbsichern(bereich);
  const q = (s) => bereich.querySelector(s);
  const z = {
    daten: null, stand: q('.kauf-stand'), kopf: q('.kauf-kopf'), hinzu: q('.kauf-hinzu'), formular: q('.kauf-formular'),
    decks: q('.kauf-decks'), filter: q('.kauf-filter'), liste: q('.kauf-liste'), toast: q('.deck-toast'),
    meldung: q('.deck-meldung'), status: q('.nur-vorleser'),
    ansicht: vorliebe('kaufliste.ansicht') === 'treffer' ? 'treffer' : 'alle', sitzung: null, taste: null,
  };
  Z = z;
  formularEinrichten(z);
  const cm = q('.kauf-cm-bereich');
  cm.innerHTML = cmFilterHtml();
  cm.addEventListener('change', (e) => {
    const s = e.target.closest('.kauf-cm-wahl');
    if (!s) return;
    vorliebe(`kaufliste.cm.${s.dataset.filterArt}`, s.value);
    const n = Object.values(cmFilter()).filter(Boolean).length;
    cm.querySelector('summary').textContent = `Cardmarket-Filter${n ? ` (${n} aktiv)` : ''}`;
    if (z.daten) zeichnen();
  });
  q('.deck-zurueck').addEventListener('click', () => zurueck('#/'));
  z.hinzu.addEventListener('click', () => {
    z.formular.hidden = !z.formular.hidden;
    z.hinzu.setAttribute('aria-expanded', String(!z.formular.hidden));
    if (!z.formular.hidden) z.formular.querySelector('.kauf-suche-feld').focus();
  });
  q('.kauf-kopieren').addEventListener('click', async () => {
    const text = z.daten?.text ?? '';
    if (!text.trim()) { z.status.textContent = 'Die Kaufliste ist leer.'; return; }
    try {
      await navigator.clipboard.writeText(text);
      z.status.textContent = `Kaufliste kopiert. ${HINWEIS_CARDMARKET}`;
      toastZeigen(z.toast, `Kaufliste kopiert. ${HINWEIS_CARDMARKET}`);
    } catch {
      toastZeigen(z.toast, 'Die Zwischenablage ist gesperrt.', { art: 'fehler' });
    }
  });
  z.filter.addEventListener('click', (e) => {
    const b = e.target.closest('[data-filter]');
    if (!b) return;
    z.ansicht = b.dataset.filter;
    vorliebe('kaufliste.ansicht', z.ansicht);
    if (z.daten) zeichnen();
  });
  z.decks.addEventListener('change', (e) => {
    const s = e.target.closest('[data-deck]');
    if (!s) return;
    const d = z.daten.decks.find((x) => x.id === Number(s.dataset.deck));
    schreiben(z, `/api/kaufliste/deck/${Number(s.dataset.deck)}`, { daten: { an: s.checked } },
      `${d?.name ?? 'Deck'} ${s.checked ? 'steht jetzt in der Kaufliste' : 'ist aus der Kaufliste genommen'}.`);
  });
  z.liste.addEventListener('click', (e) => {
    const v = e.target.closest('.kauf-vorschlag');
    if (v) { const p = z.daten.karten[Number(v.dataset.i)]; wunschSetzen(z, p, p.vorschlag); return; }
    const weg = e.target.closest('.kauf-entfernen');
    if (weg) {
      const p = z.daten.karten[Number(weg.dataset.i)];
      // Steht die Karte auch über ein Deck in der Liste, bleibt der Eintrag mit Anzahl 0 für den Wunschpreis
      const opt = p.decks.length ? { methode: 'PATCH', daten: { anzahl: 0 } } : { methode: 'DELETE' };
      schreiben(z, `/api/kaufliste/karte/${Number(p.eintrag_id)}`, opt, `Eigener Eintrag ${p.name} entfernt.`);
    }
  });
  z.liste.addEventListener('change', (e) => {
    const w = e.target.closest('.kauf-wunsch-feld');
    if (w) {
      const p = z.daten.karten[Number(w.dataset.i)];
      const wert = preisLesen(w.value);
      if (wert === undefined) { z.meldung.textContent = 'Der Wunschpreis ist keine gültige Zahl (z. B. 1,50).'; return; }
      wunschSetzen(z, p, wert);
      return;
    }
    const n = e.target.closest('.kauf-anzahl-feld');
    if (n) {
      const p = z.daten.karten[Number(n.dataset.i)];
      const anzahl = Number(n.value);
      if (!Number.isInteger(anzahl) || anzahl < 1 || anzahl > 999) { z.meldung.textContent = 'Die Anzahl muss von 1 bis 999 gehen.'; return; }
      schreiben(z, `/api/kaufliste/karte/${Number(p.eintrag_id)}`, { methode: 'PATCH', daten: { anzahl } },
        `${p.name}: eigener Eintrag ${anzahl}×.`);
    }
  });
  z.sitzung = () => { if (Z === z && z.daten) zeichnen(); };
  window.addEventListener('binder:sitzung', z.sitzung);
  z.taste = (e) => { if (e.key === 'Escape' && !dialogOffen() && Z === z && e.target.tagName !== 'INPUT') zurueck('#/'); };
  document.addEventListener('keydown', z.taste);
  await laden(z);
}

export async function verlassen() { aufraeumen(); }
