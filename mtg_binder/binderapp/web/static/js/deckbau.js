// Deck bauen (Entnahme, #/deck/ID/bauen) und Deck zerlegen (#/deck/ID/zerlegen[?karten=1,2]).
//
// Entnahme: Die Karten, die ins Deck wandern, stehen nach Ort sortiert (Binder, Kisten mit Fächern). Ein Tipp hakt eine Karte
// ab; der Haken wird sofort im Add-on gespeichert und überlebt ein Neuladen. „Fertig“ legt alle abgehakten Karten ins Deck.
// Zerlegen: Karten zurück an ihren Platz (Herkunft oder Sortierregel), ohne Ziel in eine gewählte Kiste.
// Alle Aufrufe über `holen` und `senden`; Texte mit `esc`; Rückgängig über die Deckansicht (deck.js).

import { holen, senden, esc, gehe, zurueck } from './gemeinsam.js';
import { dialog, dialogOffen, bestaetigen } from './dialog.js';
import { el, knopfIn, bildHtml, bilderAbsichern, linksAnschliessen, ZURUECK, meldungMerken, toastZeigen } from './deck.js';

const zahl = (n) => Number(n ?? 0).toLocaleString('de-DE');
const HAKEN = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.2"'
  + ' stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';

let B = null;                   // Zustand der gezeigten Ansicht

export function aufraeumenBau() {
  if (!B) return;
  document.removeEventListener('keydown', B.taste);
  B = null;
}

function kopf(wurzel, titel, name, deckId) {
  const bereich = el('section', 'deck bau');
  bereich.innerHTML = '<header class="deck-kopfzeile">'
    + `<a class="knopf knopf-sym deck-zurueck" href="#/deck/${deckId}" aria-label="Zurück zum Deck">${ZURUECK}</a>`
    + `<div class="deck-titelblock"><h1 class="deck-titel">${esc(titel)}</h1><p class="etikett deck-quelle-zeile">${esc(name ?? '')}</p></div></header>`
    + '<div class="deck-toast" role="status" hidden></div>'
    + '<div class="bau-inhalt"><p class="deck-hinweis">Wird geladen …</p></div>';
  wurzel.replaceChildren(bereich);
  linksAnschliessen(bereich);
  bilderAbsichern(bereich);
  return bereich;
}

function tasteEsc(b, deckId) {
  b.taste = (e) => {
    if (e.key !== 'Escape' || dialogOffen() || B !== b) return;
    gehe(`#/deck/${deckId}`, { ersetzen: true });
  };
  document.addEventListener('keydown', b.taste);
}

// ---------------------------------------------------------------- Entnahme

function eintragHtml(e, i) {
  const druck = `${String(e.set_code ?? '').toUpperCase()}${e.nummer ? ` · ${e.nummer}` : ''}`;
  const zusatz = [e.sprache ? String(e.sprache).toUpperCase() : '', e.genau === false ? 'anderer Druck' : ''].filter(Boolean).join(' · ');
  return `<li class="bau-eintrag${e.abgehakt ? ' bau-eintrag--ab' : ''}" data-i="${i}">`
    + `<button type="button" class="bau-karte" aria-pressed="${e.abgehakt ? 'true' : 'false'}"`
    + ` aria-label="${esc(`${e.anzahl}× ${e.name}, ${e.ort?.text ?? ''}`)}">`
    + `${bildHtml(e)}<span class="bau-text">`
    + `<span class="deck-zeile-kopf"><span class="deck-anzahl">${zahl(e.anzahl)}×</span><span class="deck-name">${esc(e.name)}</span>`
    + `${e.foil ? '<span class="deck-foil">Foil</span>' : ''}</span>`
    + `<span class="deck-druck">${esc(druck)}${zusatz ? ` · ${esc(zusatz)}` : ''}</span>`
    + `<span class="bau-platz">${esc(e.ort?.text ?? '')}</span></span>`
    + `<span class="bau-haken" aria-hidden="true">${HAKEN}</span></button>`
    + (e.alternativen > 0 ? `<button type="button" class="knopf bau-anders">anderes Exemplar</button>` : '')
    + '</li>';
}

function entnahmeZeichnen(b) {
  const d = b.daten;
  const alle = b.eintraege;
  const gesamt = alle.reduce((s, e) => s + (e.anzahl ?? 0), 0);
  const ab = alle.filter((e) => e.abgehakt).reduce((s, e) => s + (e.anzahl ?? 0), 0);
  const offen = d.offen ?? [];
  let i = 0;
  const gruppen = (d.gruppen ?? []).map((g) => {
    let fach;
    const teile = [];
    for (const e of g.eintraege) {
      const f = g.art === 'kiste' ? (e.fach ?? '') : '';
      if (g.art === 'kiste' && f !== fach) {
        fach = f;
        teile.push(f ? `</ul><h3 class="bau-fach etikett">${esc(f)}</h3><ul class="deck-zeilen bau-zeilen">` : '</ul><ul class="deck-zeilen bau-zeilen">');
      }
      teile.push(eintragHtml(e, i++));
    }
    const gAb = g.eintraege.filter((e) => e.abgehakt).length;
    return `<section class="deck-gruppe bau-gruppe" aria-label="${esc(g.text ?? g.name)}">`
      + `<h2 class="deck-gruppe-titel etikett">${esc(g.text ?? g.name)} · ${gAb}/${g.eintraege.length}</h2>`
      + `<ul class="deck-zeilen bau-zeilen">${teile.join('')}</ul></section>`;
  }).join('');
  const offenHtml = offen.length
    ? '<section class="deck-gruppe deck-gruppe--fehlt" aria-label="Offen">'
      + `<h2 class="deck-gruppe-titel etikett">Offen · ${zahl(offen.length)}</h2>`
      + '<p class="deck-hinweis">Für diese Karten gibt es kein freies Exemplar.</p><ul class="deck-zeilen">'
      + offen.map((o) => `<li class="deck-zeile deck-zeile--fehlt"><div class="deck-zeile-text"><p class="deck-zeile-kopf">`
        + `<span class="deck-anzahl">${zahl(o.fehlt)}×</span><span class="deck-name">${esc(o.name)}</span></p>`
        + (o.andere_decks?.length ? `<p class="deck-andere">${o.andere_decks.map((a) => `steckt in <a href="#/deck/${Number(a.deck_id)}">Deck ${esc(a.deck)}</a>`).join(' · ')}</p>` : '')
        + '</div></li>').join('') + '</ul></section>'
    : '';
  const y = window.scrollY;
  b.inhalt.innerHTML = `<div class="bau-stand"><p class="bau-zaehler"><strong>noch ${zahl(gesamt - ab)}</strong> von ${zahl(gesamt)}</p>`
    + `<div class="deck-balken" role="img" aria-label="${zahl(ab)} von ${zahl(gesamt)} abgehakt"><span class="deck-balken-deck" style="width:${gesamt ? (ab / gesamt * 100).toFixed(1) : 0}%"></span></div></div>`
    + (gesamt === 0 && !offen.length ? '<p class="deck-hinweis">Es gibt nichts zu holen.</p>' : '') + gruppen + offenHtml;
  window.scrollTo({ top: y, behavior: 'instant' });
  b.fertig.disabled = ab === 0;
  b.fertigText.textContent = ab ? `Fertig · ${zahl(ab)} Karten` : 'Fertig';
  b.abgehakt = ab;
}

async function entnahmeLaden(b) {
  try {
    b.daten = await holen(`/api/deck/${b.id}/entnahme`);
  } catch (f) {
    if (B === b) b.inhalt.innerHTML = '<p class="deck-hinweis">Die Entnahme ließ sich nicht laden.</p>';
    return;
  }
  if (B !== b) return;
  b.eintraege = (b.daten.gruppen ?? []).flatMap((g) => g.eintraege);
  entnahmeZeichnen(b);
}

async function anderesWaehlen(b, e) {
  let a;
  try {
    a = await holen(`/api/deck/${b.id}/entnahme/alternativen?zeile=${Number(e.deck_karte_id)}`);
  } catch {
    toastZeigen(b.toast, 'Die anderen Exemplare ließen sich nicht laden.', { art: 'fehler' });
    return;
  }
  const inhalt = el('div', 'bau-wahl');
  const liste = a.alternativen ?? [];
  inhalt.innerHTML = liste.length
    ? `<ul class="bau-wahl-liste">${liste.map((x) => `<li><button type="button" class="bau-wahl-eintrag" data-karte="${Number(x.karte_id)}">`
      + `${bildHtml(x)}<span class="bau-text"><span class="deck-zeile-kopf"><span class="deck-anzahl">${zahl(x.anzahl)}×</span>`
      + `<span class="deck-name">${esc(x.name)}</span>${x.foil ? '<span class="deck-foil">Foil</span>' : ''}</span>`
      + `<span class="deck-druck">${esc(String(x.set_code ?? '').toUpperCase())}${x.nummer ? ` · ${esc(x.nummer)}` : ''}${x.sprache ? ` · ${esc(String(x.sprache).toUpperCase())}` : ''}</span>`
      + `<span class="bau-platz">${esc(x.ort?.text ?? '')}</span></span></button></li>`).join('')}</ul>`
    : '<p class="dialog-text">Es gibt kein anderes Exemplar.</p>';
  const dlg = dialog({ titel: `Exemplar wählen: ${e.name}`, klasse: 'deck-dialog', inhalt, voll: true });
  bilderAbsichern(inhalt);
  knopfIn(dlg.fuss, 'Abbrechen', '', () => dlg.schliessen());
  inhalt.addEventListener('click', async (ev) => {
    const w = ev.target.closest('[data-karte]');
    if (!w) return;
    w.disabled = true;
    try {
      await senden(`/api/deck/${b.id}/entnahme/wahl`, { daten: { zeile: e.deck_karte_id, karte: Number(w.dataset.karte) } });
      dlg.schliessen(true);
    } catch (f) {
      w.disabled = false;
      toastZeigen(b.toast, f.message, { art: 'fehler' });
      dlg.schliessen();
    }
  });
  if (await dlg.fertig && B === b) await entnahmeLaden(b);
}

async function haken(b, e) {
  const neu = !e.abgehakt;
  e.abgehakt = neu;
  entnahmeZeichnen(b);
  try {
    await senden(`/api/deck/${b.id}/entnahme/haken`,
      { daten: { zeile: e.deck_karte_id, karte: e.karte_id, anzahl: e.anzahl, abgehakt: neu } });
  } catch (f) {
    e.abgehakt = !neu;
    if (B === b) { entnahmeZeichnen(b); toastZeigen(b.toast, f.message, { art: 'fehler' }); }
  }
}

async function fertigMachen(b) {
  if (!b.abgehakt) return;
  const ja = await bestaetigen({ titel: 'Fertig?', text: `${zahl(b.abgehakt)} Karten wandern ins Deck.`, ja: 'Ins Deck legen' });
  if (!ja || B !== b) return;
  b.fertig.disabled = true;
  try {
    const a = await senden(`/api/deck/${b.id}/entnahme/fertig`, { daten: {} });
    const weg = a.uebersprungen ? ` ${zahl(a.uebersprungen)} abgehakte Karten lagen nicht mehr frei und wurden übersprungen.` : '';
    meldungMerken(a.karten ? `${zahl(a.karten)} Karten liegen jetzt im Deck.${weg}` : `Keine Karte verschoben.${weg}`,
      a.schritt);
    gehe(`#/deck/${b.id}`, { ersetzen: true });
  } catch (f) {
    b.fertig.disabled = false;
    toastZeigen(b.toast, f.message, { art: 'fehler' });
  }
}

export async function bauenZeigen(wurzel, parameter) {
  aufraeumenBau();
  const bereich = kopf(wurzel, 'Deck bauen', '', parameter.deck);
  const b = { id: parameter.deck, bereich, inhalt: bereich.querySelector('.bau-inhalt'), toast: bereich.querySelector('.deck-toast'),
    daten: null, eintraege: [], abgehakt: 0, taste: null };
  B = b;
  const fuss = el('div', 'bau-fuss');
  b.fertig = Object.assign(el('button', 'knopf knopf-haupt bau-fertig'), { type: 'button', disabled: true });
  b.fertigText = el('span', '', 'Fertig');
  b.fertig.append(b.fertigText);
  fuss.append(b.fertig);
  bereich.append(fuss);
  b.fertig.addEventListener('click', () => fertigMachen(b));
  b.inhalt.addEventListener('click', (ev) => {
    const li = ev.target.closest('.bau-eintrag');
    if (!li) return;
    const e = b.eintraege[Number(li.dataset.i)];
    if (!e) return;
    if (ev.target.closest('.bau-anders')) anderesWaehlen(b, e);
    else if (ev.target.closest('.bau-karte')) haken(b, e);
  });
  tasteEsc(b, b.id);
  await entnahmeLaden(b);
  if (B === b && b.daten) bereich.querySelector('.deck-quelle-zeile').textContent = b.daten.name ?? '';
}

// ---------------------------------------------------------------- Zerlegen

function zerlegenZeichnen(b) {
  const d = b.daten;
  const karten = d.karten ?? [];
  if (!karten.length) { b.inhalt.innerHTML = '<p class="deck-hinweis">Im Deck liegen keine Karten.</p>'; return; }
  const gruppen = new Map();
  for (const k of karten) {
    const t = k.ziel ? k.ziel.text : '';
    if (!gruppen.has(t)) gruppen.set(t, []);
    gruppen.get(t).push(k);
  }
  const reihenfolge = [...gruppen.keys()].sort((a, c) => (a === '') - (c === '') || a.localeCompare(c, 'de', { numeric: true }));
  const zeile = (k) => `<li class="deck-zeile">${bildHtml(k)}<div class="deck-zeile-text"><p class="deck-zeile-kopf">`
    + `<span class="deck-anzahl">${zahl(k.anzahl)}×</span><span class="deck-name">${esc(k.name)}</span>`
    + `${k.foil ? '<span class="deck-foil">Foil</span>' : ''}</p>`
    + `<p class="deck-zeile-unter"><span class="deck-druck">${esc(String(k.set_code ?? '').toUpperCase())}${k.nummer ? ` · ${esc(k.nummer)}` : ''}</span>`
    + `${k.ziel?.grund ? `<span class="deck-ort-zusatz">${k.ziel.grund === 'regel' ? 'nach Sortierregel' : 'zurück an die Herkunft'}</span>` : ''}</p></div></li>`;
  const ohne = d.ohne_ziel ?? 0;
  b.inhalt.innerHTML = `<p class="bau-zaehler"><strong>${zahl(karten.reduce((s, k) => s + (k.anzahl ?? 0), 0))} Karten</strong> werden zurückgelegt.</p>`
    + (ohne > 0
      ? '<div class="bau-ersatz"><label class="etikett feld-etikett" for="bau-ersatz">Kiste für Karten ohne Platz</label>'
        + '<select id="bau-ersatz" class="feld"><option value="">– Kiste wählen –</option>'
        + b.kisten.map((x) => `<option value="${Number(x.id)}">${esc(x.name)}</option>`).join('')
        + `</select><p class="deck-hinweis">${zahl(ohne)} Karten haben keinen früheren Platz und keine passende Regel.</p></div>`
      : '')
    + reihenfolge.map((t) => `<section class="deck-gruppe" aria-label="${esc(t || 'ohne Ziel')}">`
      + `<h2 class="deck-gruppe-titel etikett">${esc(t || 'Ohne Ziel')} · ${zahl(gruppen.get(t).reduce((s, k) => s + (k.anzahl ?? 0), 0))}</h2>`
      + `<ul class="deck-zeilen">${gruppen.get(t).map(zeile).join('')}</ul></section>`).join('');
}

export async function zerlegenZeigen(wurzel, parameter) {
  aufraeumenBau();
  const bereich = kopf(wurzel, parameter.karten ? 'Zurücklegen' : 'Deck zerlegen', '', parameter.deck);
  const b = { id: parameter.deck, bereich, inhalt: bereich.querySelector('.bau-inhalt'), toast: bereich.querySelector('.deck-toast'),
    daten: null, kisten: [], taste: null };
  B = b;
  const fuss = el('div', 'bau-fuss');
  const ok = Object.assign(el('button', 'knopf knopf-haupt bau-fertig', 'Zurücklegen'), { type: 'button', disabled: true });
  fuss.append(ok);
  bereich.append(fuss);
  tasteEsc(b, b.id);
  const abfrage = parameter.karten ? `?karten=${parameter.karten.join(',')}` : '';
  try {
    b.daten = await holen(`/api/deck/${b.id}/zerlegen${abfrage}`);
    if ((b.daten.ohne_ziel ?? 0) > 0) b.kisten = (await holen('/api/kisten')).kisten ?? [];
  } catch {
    if (B === b) b.inhalt.innerHTML = '<p class="deck-hinweis">Das ließ sich nicht laden.</p>';
    return;
  }
  if (B !== b) return;
  bereich.querySelector('.deck-quelle-zeile').textContent = b.daten.name ?? '';
  zerlegenZeichnen(b);
  ok.disabled = !(b.daten.karten ?? []).length;
  ok.addEventListener('click', async () => {
    const wahl = b.inhalt.querySelector('#bau-ersatz')?.value;
    if (b.daten.ohne_ziel > 0 && !wahl) {
      toastZeigen(b.toast, 'Bitte eine Kiste für die Karten ohne Platz wählen.', { art: 'fehler' });
      return;
    }
    ok.disabled = true;
    const daten = {};
    if (parameter.karten) daten.karten = parameter.karten;
    if (wahl) daten.ersatz_kiste = Number(wahl);
    try {
      const a = await senden(`/api/deck/${b.id}/zerlegen`, { daten });
      meldungMerken(`${zahl(a.karten)} Karten zurückgelegt.`, a.schritt);
      gehe(`#/deck/${b.id}`, { ersetzen: true });
    } catch (f) {
      ok.disabled = false;
      toastZeigen(b.toast, f.message, { art: 'fehler' });
    }
  });
}
