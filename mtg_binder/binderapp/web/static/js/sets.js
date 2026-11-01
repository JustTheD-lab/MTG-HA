// Sets und ihre Vollständigkeit.
//
//   #/sets         Übersicht: alle Sets, aus denen die Sammlung Karten hat (Suche, Sortierung, zwei Balken je Set)
//   #/set/ecl      ein Set: alle Drucke bzw. Namen des Katalogs, vorhanden oder fehlend (Umschalter und Filter)
//
// „Drucke“ zählt jede Sammlernummer (auch Borderless, Showcase …), „Namen“ jede Karte des Sets einmal.
// Beide Ansichten kommen aus diesem Modul (app.js trägt es als `sets` und `set` ein). Nur lesend; die Daten holt
// /api/sets bzw. /api/set/{code}. Ohne Katalog gibt es einen Hinweis statt der Liste.

import { holen, esc, gehe, zurueck, euro, vorliebe, bewegungReduziert } from './gemeinsam.js';
import { dialogOffen } from './dialog.js';

const sym = (d, g = 18) => `<svg viewBox="0 0 24 24" width="${g}" height="${g}" fill="none" stroke="currentColor"`
  + ` stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${d}</svg>`;
const ZURUECK = sym('<path d="M15 5l-7 7 7 7"/>', 20);
const HAKEN = sym('<path d="M4 12.5l5 5L20 6.5"/>', 14);
const LUPE = sym('<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>', 18);

const KEIN_KATALOG = 'Die Set-Ansicht braucht den Katalog. Die Erkennung am PC schickt ihn beim Start.';

const SORTIERUNGEN = [
  { wert: 'neueste', text: 'Neueste' },
  { wert: 'vollstaendig', text: 'Vollständigkeit' },
  { wert: 'name', text: 'Name' },
  { wert: 'karten', text: 'Karten' },
];

const SELTENHEIT = { common: 'Gewöhnlich', uncommon: 'Ungewöhnlich', rare: 'Selten', mythic: 'Mythisch selten',
  special: 'Besonders', bonus: 'Bonus' };
const FOIL_SONDER = new Set(['etched', 'textured', 'galaxy', 'surge', 'serialized']);
// gleiche Texte wie die Sortierregeln (sortieren/regeln.py, BESONDERHEITSNAMEN)
const BESONDERHEIT = { borderless: 'Borderless', showcase: 'Showcase', extendedart: 'Extended Art',
  retro: 'Retro-Rahmen', fullart: 'Full Art', etched: 'Etched', textured: 'Textured', galaxy: 'Galaxy Foil',
  surge: 'Surge Foil', serialized: 'Nummeriert' };

const FILTER = [
  { wert: 'alle', text: 'Alle' }, { wert: 'vorhanden', text: 'Vorhanden' }, { wert: 'fehlt', text: 'Fehlt' },
];
const ANSICHTEN = [{ wert: 'drucke', text: 'Drucke' }, { wert: 'namen', text: 'Namen' }];

const zahl = (n) => Number(n ?? 0).toLocaleString('de-DE');

let Z = null;                    // Zustand der gezeigten Ansicht
let wurzelMerk = null;           // Bühne, für aktualisieren()
const scrollMerk = new Map();    // 'sets' oder 'set:code' -> Bildlaufposition beim Verlassen
let suchMerk = '';               // Suchtext der Übersicht, bleibt beim Zurückkommen aus einem Set

// ---------------------------------------------------------------- Hilfen

function el(tag, klasse, html) {
  const e = document.createElement(tag);
  if (klasse) e.className = klasse;
  if (html !== undefined) e.innerHTML = html;
  return e;
}

/** Prozent für die Anzeige: nie „100 %“, solange etwas fehlt, und nie „0 %“, solange etwas da ist. */
function prozent(da, gesamt) {
  if (!gesamt) return 0;
  if (da >= gesamt) return 100;
  return Math.min(99, Math.max(da > 0 ? 1 : 0, Math.round((da / gesamt) * 100)));
}

/** Fortschrittsbalken mit Beschriftung. `art` färbt ihn (drucke = Messing, namen = Grün). */
function balkenHtml(titel, da, gesamt, art) {
  const p = prozent(da, gesamt);
  const text = gesamt ? `${zahl(da)}/${zahl(gesamt)} · ${p} %` : '–';
  const voll = gesamt > 0 && da >= gesamt;
  return `<span class="sets-balken-zeile sets-art-${art}">`
    + `<span class="sets-balken-text"><span class="sets-balken-titel">${esc(titel)}</span>`
    + `<span class="sets-balken-zahlen">${esc(text)}</span></span>`
    + `<span class="sets-balken" role="progressbar" aria-label="${esc(titel)}" aria-valuemin="0" aria-valuemax="100"`
    + ` aria-valuenow="${p}" aria-valuetext="${zahl(da)} von ${zahl(gesamt)}, ${p} Prozent">`
    + `<span class="sets-balken-fuell${voll ? ' sets-voll' : ''}" style="--p:${p}"></span></span></span>`;
}

function jahr(erschienen) {
  return /^\d{4}/.test(erschienen ?? '') ? erschienen.slice(0, 4) : '';
}

function datumText(erschienen) {
  const d = new Date(`${erschienen}T00:00:00`);
  if (!erschienen || Number.isNaN(d.getTime())) return '';
  return d.toLocaleDateString('de-DE', { day: 'numeric', month: 'long', year: 'numeric' });
}

function hinweis(text) {
  return `<p class="sets-hinweis" role="status">${esc(text)}</p>`;
}

function scrollMerken() {
  if (!Z) return;
  scrollMerk.set(Z.schluessel, window.scrollY);
}

function scrollHerstellen(schluessel) {
  const y = scrollMerk.get(schluessel);
  if (y == null) return;
  window.scrollTo({ top: y, behavior: 'instant' });
}

/** Interne Links über die App öffnen (neuer Verlaufseintrag), Strg/Cmd-Klick bleibt dem Browser. */
function linksAnschliessen(wurzel) {
  wurzel.addEventListener('click', (e) => {
    const a = e.target.closest('a[href^="#/"]');
    if (!a || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return;
    e.preventDefault();
    gehe(a.getAttribute('href'));
  });
}

function tastaturAnschliessen(z, ersatz) {
  z.taste = (e) => {
    if (e.key !== 'Escape' || dialogOffen() || Z !== z) return;
    const feld = e.target?.matches?.('input[type="search"]') ? e.target : null;
    if (feld && feld.value) {   // erst Esc leert die Suche, danach geht es zurück
      feld.value = '';
      feld.dispatchEvent(new Event('input'));
      return;
    }
    zurueck(ersatz);
  };
  document.addEventListener('keydown', z.taste);
}

function pillen(gruppe, liste, aktuell, name, beschriftung) {
  gruppe.setAttribute('role', 'group');
  gruppe.setAttribute('aria-label', beschriftung);
  gruppe.innerHTML = liste.map((x) => `<button type="button" class="knopf sets-pille" data-${name}="${x.wert}"`
    + ` aria-pressed="${x.wert === aktuell}">${esc(x.text)}</button>`).join('');
}

// ---------------------------------------------------------------- Übersicht

function setHtml(s) {
  const titel = s.name || s.code.toUpperCase();
  const balken = s.im_katalog
    ? balkenHtml('Drucke', s.drucke_da, s.drucke_gesamt, 'drucke') + balkenHtml('Namen', s.namen_da, s.namen_gesamt, 'namen')
    : '<span class="sets-nokat">Nicht im Katalog</span>';
  const wert = s.wert != null ? `<span class="wert">${esc(euro(s.wert, { rund: s.wert >= 100 }))}</span>` : '';
  return `<li class="sets-eintrag"><a class="sets-set" href="#/set/${esc(s.code)}" data-code="${esc(s.code)}">`
    + `<span class="sets-set-kopf"><span class="sets-code">${esc(s.code.toUpperCase())}</span>`
    + `<span class="sets-set-name">${esc(titel)}</span><span class="sets-jahr">${esc(jahr(s.erschienen))}</span></span>`
    + balken
    + `<span class="sets-set-fuss"><span class="sets-karten">${zahl(s.karten)} ${s.karten === 1 ? 'Karte' : 'Karten'}</span>${wert}</span>`
    + '</a></li>';
}

const verhaeltnis = (s) => (s.im_katalog && s.drucke_gesamt ? s.drucke_da / s.drucke_gesamt : -1);

function sortiert(sets, wert) {
  const neu = (a, b) => (b.erschienen ?? '').localeCompare(a.erschienen ?? '') || b.code.localeCompare(a.code);
  const kopie = [...sets];
  if (wert === 'vollstaendig') kopie.sort((a, b) => verhaeltnis(b) - verhaeltnis(a) || b.drucke_da - a.drucke_da || neu(a, b));
  else if (wert === 'name') kopie.sort((a, b) => (a.name || a.code).localeCompare(b.name || b.code, 'de') || a.code.localeCompare(b.code));
  else if (wert === 'karten') kopie.sort((a, b) => b.karten - a.karten || neu(a, b));
  else kopie.sort(neu);
  return kopie;
}

function uebersichtZeichnen() {
  const z = Z;
  const q = z.suche.value.trim().toLowerCase();
  suchMerk = z.suche.value;
  const treffer = q
    ? z.sets.filter((s) => s.code.toLowerCase().includes(q) || (s.name ?? '').toLowerCase().includes(q))
    : z.sets;
  const liste = sortiert(treffer, z.sort);
  z.liste.innerHTML = liste.map(setHtml).join('');
  z.leer.hidden = liste.length > 0;
  z.zahl.textContent = q ? `${zahl(liste.length)} von ${zahl(z.sets.length)} Sets` : `${zahl(z.sets.length)} Sets`;
  z.status.textContent = q ? `${zahl(liste.length)} Treffer` : '';
}

async function uebersichtZeigen(wurzel, vorher) {
  const bereich = el('section', 'sets');
  bereich.setAttribute('aria-label', 'Sets');
  bereich.innerHTML = '<header class="sets-kopfzeile">'
    + `<button type="button" class="knopf knopf-sym sets-zurueck" aria-label="Zurück ins Regal">${ZURUECK}</button>`
    + '<div class="sets-titelblock"><h1 class="sets-titel">Sets</h1><p class="etikett sets-zahl" aria-live="polite"></p></div></header>'
    + '<div class="sets-inhalt"></div>';
  wurzel.replaceChildren(bereich);
  bereich.querySelector('.sets-zurueck').addEventListener('click', () => zurueck('#/'));
  linksAnschliessen(bereich);
  const inhalt = bereich.querySelector('.sets-inhalt');
  inhalt.innerHTML = hinweis('Sets werden geladen …');

  let daten;
  try {
    daten = await holen('/api/sets');
  } catch {
    if (!wurzel.contains(bereich)) return;
    inhalt.innerHTML = hinweis('Die Sets ließen sich nicht laden. Läuft der Server?');
    return;
  }
  if (!wurzel.contains(bereich)) return;
  if (!daten.verfuegbar) { inhalt.innerHTML = hinweis(KEIN_KATALOG); return; }

  const gespeichert = vorliebe('sets.sort');
  const sort = SORTIERUNGEN.some((x) => x.wert === gespeichert) ? gespeichert : 'neueste';
  inhalt.innerHTML = '<div class="sets-werkzeug">'
    + '<div class="sets-suchfeld">' + LUPE + '<input id="sets-suche" class="feld sets-suche" type="search" autocomplete="off"'
    + ' autocapitalize="off" spellcheck="false" enterkeyhint="search" placeholder="Set oder Kürzel suchen"'
    + ' aria-label="Set nach Name oder Kürzel suchen" maxlength="60"></div>'
    + '<div class="sets-pillen sets-sortierung"></div></div>'
    + '<p class="nur-vorleser" role="status" aria-live="polite"></p>'
    + '<p class="sets-hinweis sets-leer" hidden>Kein Set gefunden.</p>'
    + '<ul class="sets-liste" aria-label="Sets"></ul>';
  const z = {
    art: 'sets', schluessel: 'sets', sets: daten.sets, sort, bereich, wurzel,
    suche: inhalt.querySelector('.sets-suche'), liste: inhalt.querySelector('.sets-liste'),
    leer: inhalt.querySelector('.sets-leer'), zahl: bereich.querySelector('.sets-zahl'),
    status: inhalt.querySelector('[role="status"]'), uhr: 0, taste: null,
  };
  Z = z;
  pillen(inhalt.querySelector('.sets-sortierung'), SORTIERUNGEN, sort, 'sort', 'Sortierung');
  inhalt.querySelector('.sets-sortierung').addEventListener('click', (e) => {
    const b = e.target.closest('[data-sort]');
    if (!b || Z !== z) return;
    z.sort = b.dataset.sort;
    vorliebe('sets.sort', z.sort);
    for (const x of b.parentElement.children) x.setAttribute('aria-pressed', String(x === b));
    uebersichtZeichnen();
  });
  const zurueckAusSet = vorher?.ansicht === 'set';
  if (zurueckAusSet && suchMerk) z.suche.value = suchMerk; else suchMerk = '';
  z.suche.addEventListener('input', () => {
    clearTimeout(z.uhr);
    z.uhr = setTimeout(() => { if (Z === z) uebersichtZeichnen(); }, 120);
  });
  z.suche.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); z.suche.blur(); } });
  tastaturAnschliessen(z, '#/');
  uebersichtZeichnen();

  if (zurueckAusSet) {
    scrollHerstellen('sets');
    const zuletzt = vorher.set ? z.liste.querySelector(`a[data-code="${CSS.escape(vorher.set)}"]`) : null;
    zuletzt?.focus({ preventScroll: true });
  }
}

// ---------------------------------------------------------------- Detail

function druckZeile(d) {
  const da = d.anzahl > 0;
  const abzeichen = d.besonderheiten.map((b) => `<span class="sets-abz${FOIL_SONDER.has(b) ? ' sets-abz-foil' : ''}">`
    + `${esc(BESONDERHEIT[b] ?? b)}</span>`).join('');
  const selt = SELTENHEIT[d.seltenheit] ?? (d.seltenheit ? d.seltenheit[0].toUpperCase() + d.seltenheit.slice(1) : '');
  const inhalt = `<span class="sets-nr">${esc(d.nummer)}</span>`
    + `<span class="sets-haupt"><span class="sets-druck-name">${esc(d.name)}</span>`
    + `<span class="sets-unter"><span class="sets-selt sets-selt-${esc(d.seltenheit)}">${esc(selt)}</span>${abzeichen}</span></span>`
    + (da
      ? `<span class="sets-anz">${HAKEN}<span><span class="nur-vorleser">Anzahl: </span>×${zahl(d.anzahl)}</span></span>`
      : '<span class="sets-anz sets-anz-fehlt">fehlt</span>');
  if (!da || d.karte_id == null) {
    return `<li class="sets-zeile ${da ? 'sets-da' : 'sets-fehlt'}"><div class="sets-zeile-innen">${inhalt}</div></li>`;
  }
  const ziel = d.kiste_id != null ? `#/kiste/${d.kiste_id}/karte/${d.karte_id}` : `#/sammlung/karte/${d.karte_id}`;
  return `<li class="sets-zeile sets-da"><a class="sets-zeile-innen sets-link" href="${ziel}"`
    + ` aria-label="${esc(`${d.nummer} ${d.name}, ${zahl(d.anzahl)} vorhanden, Karte öffnen`)}">${inhalt}</a></li>`;
}

function namenZeile(n) {
  const da = n.anzahl > 0;
  const druckeText = `${zahl(n.drucke_da)} von ${zahl(n.drucke_gesamt)} ${n.drucke_gesamt === 1 ? 'Druck' : 'Drucken'}`;
  return `<li class="sets-zeile ${da ? 'sets-da' : 'sets-fehlt'}"><div class="sets-zeile-innen sets-namenzeile">`
    + `<span class="sets-haupt"><span class="sets-druck-name">${esc(n.name)}</span>`
    + `<span class="sets-unter"><span class="sets-selt">${esc(druckeText)}</span></span></span>`
    + (da
      ? `<span class="sets-anz">${HAKEN}<span><span class="nur-vorleser">Anzahl: </span>×${zahl(n.anzahl)}</span></span>`
      : '<span class="sets-anz sets-anz-fehlt">fehlt</span>')
    + '</div></li>';
}

function detailZeichnen() {
  const z = Z;
  const drucke = z.ansicht === 'drucke';
  const alle = drucke ? z.daten.drucke : z.daten.namen;
  const gesamt = alle.length;
  const da = alle.filter((x) => x.anzahl > 0).length;
  for (const b of z.filterZeile.children) {
    const wert = b.dataset.filter;
    const n = wert === 'alle' ? gesamt : wert === 'vorhanden' ? da : gesamt - da;
    b.querySelector('.sets-anzahl').textContent = zahl(n);
  }
  const liste = z.filter === 'vorhanden' ? alle.filter((x) => x.anzahl > 0)
    : z.filter === 'fehlt' ? alle.filter((x) => x.anzahl === 0) : alle;
  z.zaehl.textContent = z.filter === 'vorhanden' ? `${zahl(da)} vorhanden`
    : z.filter === 'fehlt' ? `${zahl(gesamt - da)} fehlen`
      : `${zahl(da)} von ${zahl(gesamt)} vorhanden · ${zahl(gesamt - da)} fehlen`;
  z.liste.innerHTML = liste.map(drucke ? druckZeile : namenZeile).join('');
  z.liste.setAttribute('aria-label', drucke ? 'Drucke des Sets' : 'Namen des Sets');
  z.leer.hidden = liste.length > 0;
  z.leer.textContent = gesamt === 0 ? 'Der Katalog kennt zu diesem Set keine Karten.'
    : z.filter === 'fehlt' ? 'Nichts fehlt – dieses Set ist vollständig.' : 'Hier ist nichts zu sehen.';
}

async function detailZeigen(wurzel, code, vorher) {
  const bereich = el('section', 'sets');
  bereich.setAttribute('aria-label', `Set ${code.toUpperCase()}`);
  bereich.innerHTML = '<header class="sets-kopfzeile">'
    + `<button type="button" class="knopf knopf-sym sets-zurueck" aria-label="Zurück zu den Sets">${ZURUECK}</button>`
    + `<div class="sets-titelblock"><p class="etikett sets-code-gross">${esc(code.toUpperCase())}</p>`
    + `<h1 class="sets-titel">${esc(code.toUpperCase())}</h1><p class="sets-datum"></p></div></header>`
    + '<div class="sets-inhalt"></div>';
  wurzel.replaceChildren(bereich);
  bereich.querySelector('.sets-zurueck').addEventListener('click', () => zurueck('#/sets'));
  linksAnschliessen(bereich);
  const inhalt = bereich.querySelector('.sets-inhalt');
  inhalt.innerHTML = hinweis('Set wird geladen …');

  let daten;
  try {
    daten = await holen(`/api/set/${encodeURIComponent(code)}`);
  } catch (f) {
    if (!wurzel.contains(bereich)) return;
    inhalt.innerHTML = hinweis(/: 404$/.test(String(f.message))
      ? 'Dieses Set gibt es im Katalog nicht.' : 'Das Set ließ sich nicht laden. Läuft der Server?');
    return;
  }
  if (!wurzel.contains(bereich)) return;
  if (!daten.verfuegbar) { inhalt.innerHTML = hinweis(KEIN_KATALOG); return; }

  bereich.querySelector('.sets-titel').textContent = daten.name || code.toUpperCase();
  bereich.querySelector('.sets-datum').textContent = datumText(daten.erschienen)
    ? `Erschienen am ${datumText(daten.erschienen)} · ${zahl(daten.karten)} ${daten.karten === 1 ? 'Karte' : 'Karten'} in der Sammlung`
    : `${zahl(daten.karten)} ${daten.karten === 1 ? 'Karte' : 'Karten'} in der Sammlung`;

  const ansichtGesp = vorliebe('sets.ansicht');
  const filterGesp = vorliebe('sets.filter');
  inhalt.innerHTML = '<div class="sets-balken-gross">'
    + balkenHtml('Drucke', daten.drucke_da, daten.drucke_gesamt, 'drucke')
    + balkenHtml('Namen', daten.namen_da, daten.namen_gesamt, 'namen') + '</div>'
    + '<div class="sets-werkzeug"><div class="sets-pillen sets-umschalter"></div>'
    + '<div class="sets-pillen sets-filter"></div></div>'
    + '<p class="sets-zaehlzeile" role="status" aria-live="polite"></p>'
    + '<p class="sets-hinweis sets-leer" hidden></p>'
    + '<ul class="sets-druckliste" aria-label="Drucke des Sets"></ul>';
  const z = {
    art: 'set', schluessel: `set:${code}`, code, daten, bereich, wurzel,
    ansicht: ANSICHTEN.some((x) => x.wert === ansichtGesp) ? ansichtGesp : 'drucke',
    filter: FILTER.some((x) => x.wert === filterGesp) ? filterGesp : 'alle',
    umschalter: inhalt.querySelector('.sets-umschalter'), filterZeile: inhalt.querySelector('.sets-filter'),
    zaehl: inhalt.querySelector('.sets-zaehlzeile'), liste: inhalt.querySelector('.sets-druckliste'),
    leer: inhalt.querySelector('.sets-leer'), taste: null,
  };
  Z = z;
  pillen(z.umschalter, ANSICHTEN, z.ansicht, 'ansicht', 'Ansicht');
  pillen(z.filterZeile, FILTER, z.filter, 'filter', 'Filter');
  for (const b of z.filterZeile.children) b.insertAdjacentHTML('beforeend', ' <span class="sets-anzahl"></span>');
  z.umschalter.addEventListener('click', (e) => {
    const b = e.target.closest('[data-ansicht]');
    if (!b || Z !== z) return;
    z.ansicht = b.dataset.ansicht;
    vorliebe('sets.ansicht', z.ansicht);
    for (const x of z.umschalter.children) x.setAttribute('aria-pressed', String(x === b));
    detailZeichnen();
  });
  z.filterZeile.addEventListener('click', (e) => {
    const b = e.target.closest('[data-filter]');
    if (!b || Z !== z) return;
    z.filter = b.dataset.filter;
    vorliebe('sets.filter', z.filter);
    for (const x of z.filterZeile.children) x.setAttribute('aria-pressed', String(x === b));
    detailZeichnen();
  });
  tastaturAnschliessen(z, '#/sets');
  detailZeichnen();
  if (vorher && vorher.ansicht !== 'sets') scrollHerstellen(z.schluessel);   // zurück von einer Karte
}

// ---------------------------------------------------------------- Einstieg

export async function zeigen(wurzel, parameter, vorher) {
  verlassen();
  wurzelMerk = wurzel;
  if (parameter.set) await detailZeigen(wurzel, parameter.set, vorher);
  else await uebersichtZeigen(wurzel, vorher);
}

/** Gleiche Ansicht, anderer Parameter (z. B. Adresse von Hand geändert): neu aufbauen. */
export async function aktualisieren(parameter) {
  if (!wurzelMerk) return;
  if (parameter.set ? Z?.code === parameter.set : Z?.art === 'sets') return;
  await zeigen(wurzelMerk, parameter, null);
}

export function verlassen() {
  if (!Z) return Promise.resolve();
  scrollMerken();
  clearTimeout(Z.uhr);
  if (Z.taste) document.removeEventListener('keydown', Z.taste);
  Z = null;
  return Promise.resolve();
}
