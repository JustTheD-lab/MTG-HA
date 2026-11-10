// Decks: Ansicht eines Decks (#/deck/ID) und der Dialog „Neues Deck“ (vom Regal aufgerufen).
//
// Ein Deck kommt aus Archidekt (Link) oder aus eingefügtem Text (Export aus Moxfield oder Archidekt). Die Ansicht
// gleicht es mit der Sammlung ab: Commander, Fehlt, Vorhanden, Proxy, Ausgelassen (Basics). Orte der vorhandenen
// Karten sind Links in Binder und Kisten. Alle Aufrufe laufen über `holen` und `senden`; Texte mit `esc`.
// Schreibende Knöpfe (Proxy, Neu laden, Umbenennen, Löschen) nur mit Schreibrecht.

import { holen, senden, esc, gehe, zurueck, bildUrl, scryfallBild, bewegungReduziert, euro, vorliebe } from './gemeinsam.js';
import { angemeldet } from './anmeldung.js';
import { dialog, dialogOffen, bestaetigen } from './dialog.js';

// Meldung mit Rückgängig, die die nächste Deckansicht zeigt (z. B. nach „Fertig“ in der Entnahme)
let wartendeMeldung = null;
export function meldungMerken(text, schritt = null) { wartendeMeldung = { text, schritt }; }

/** Meldungszeile mit Text, optional „Rückgängig“ (POST api/schritt/{id}/rueckgaengig) und Schließen. */
export function toastZeigen(box, text, { schritt = null, art = 'ok', nachRueck = null } = {}) {
  box.hidden = false;
  box.className = `deck-toast${art === 'fehler' ? ' deck-toast--fehler' : ''}`;
  box.innerHTML = '<span class="deck-toast-text"></span>'
    + (schritt ? '<button type="button" class="knopf deck-toast-rueck">Rückgängig</button>' : '')
    + '<button type="button" class="knopf knopf-sym deck-toast-zu" aria-label="Meldung schließen">×</button>';
  box.querySelector('.deck-toast-text').textContent = text;
  box.querySelector('.deck-toast-zu').addEventListener('click', () => { box.hidden = true; });
  const rueck = box.querySelector('.deck-toast-rueck');
  rueck?.addEventListener('click', async () => {
    rueck.disabled = true;
    try {
      await senden(`/api/schritt/${encodeURIComponent(schritt)}/rueckgaengig`);
    } catch (f) {
      toastZeigen(box, f.message, { art: 'fehler' });
      return;
    }
    if (nachRueck) await nachRueck();
    toastZeigen(box, 'Rückgängig gemacht.');
  });
}

const sym = (d, g = 18) => `<svg viewBox="0 0 24 24" width="${g}" height="${g}" fill="none" stroke="currentColor"`
  + ` stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${d}</svg>`;
export const ZURUECK = sym('<path d="M15 5l-7 7 7 7"/>', 20);
const KOPIE = sym('<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 012-2h9"/>');
const NEU = sym('<path d="M20 12a8 8 0 11-2.4-5.7"/><path d="M20 4v5h-5"/>');

const zahl = (n) => Number(n ?? 0).toLocaleString('de-DE');
const HINWEIS_CARDMARKET = 'Für Cardmarket: Wants → Aus Text hinzufügen.';
const GRUPPEN = [
  { schluessel: 'commander', titel: 'Commander' },
  { schluessel: 'fehlt', titel: 'Fehlt' },
  { schluessel: 'vorhanden', titel: 'Vorhanden' },
  { schluessel: 'proxy', titel: 'Proxy' },
  { schluessel: 'ausgelassen', titel: 'Ausgelassen (Basics)' },
];

let Z = null;                    // Zustand der gezeigten Ansicht

export function el(tag, klasse, html) {
  const e = document.createElement(tag);
  if (klasse) e.className = klasse;
  if (html !== undefined) e.innerHTML = html;
  return e;
}

export function knopfIn(fuss, text, klasse, beiKlick) {
  const k = Object.assign(document.createElement('button'), { type: 'button', className: `knopf ${klasse}`.trim(), textContent: text });
  k.addEventListener('click', beiKlick);
  fuss.append(k);
  return k;
}

/** Bild einer Karte: Vorschau des Servers, dann Scryfall direkt, dann eine schlichte Fläche. */
export function bildHtml(k, breite = 200) {
  if (!k.scryfall_id) return '<span class="deck-bild deck-bild--leer" aria-hidden="true"></span>';
  // `vorschau: false` meldet der Server, wenn es kein Vorschaubild gibt: dann gleich Scryfall (keine 404-Anfrage)
  const direkt = k.vorschau === false && scryfallBild(k.scryfall_id);
  return '<span class="deck-bild" aria-hidden="true"><img alt="" width="63" height="88" loading="lazy" decoding="async"'
    + ` src="${esc(direkt || bildUrl('scryfall', k.scryfall_id, breite))}" data-sid="${esc(k.scryfall_id)}"`
    + `${direkt ? ' data-cdn="1"' : ''}></span>`;
}

/** Fehlendes Bild: erst direkt von Scryfall, dann Fläche (Fehlerereignisse sprudeln nicht, daher Erfassungsphase). */
export function bilderAbsichern(wurzel) {
  wurzel.addEventListener('error', (e) => {
    const bild = e.target;
    if (bild.tagName !== 'IMG') return;
    const sid = bild.dataset.sid;
    const cdn = sid ? scryfallBild(sid) : null;
    if (cdn && !bild.dataset.cdn) {
      bild.dataset.cdn = '1';
      bild.removeAttribute('srcset');
      bild.src = cdn;
    } else {
      bild.closest('.deck-bild')?.classList.add('deck-bild--leer');
      bild.remove();
    }
  }, true);
}

/** Linkklicks über die App (neuer Verlaufseintrag); Strg/Cmd-Klick bleibt dem Browser. */
export function linksAnschliessen(wurzel) {
  wurzel.addEventListener('click', (e) => {
    const a = e.target.closest('a[href^="#/"]');
    if (!a || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return;
    e.preventDefault();
    gehe(a.getAttribute('href'));
  });
}

// ---------------------------------------------------------------- Vorschau-Berichte

function liste(titel, eintraege) {
  if (!eintraege?.length) return '';
  return `<div class="deck-bericht-teil"><p class="etikett">${esc(titel)} · ${zahl(eintraege.length)}</p>`
    + `<ul class="deck-bericht-liste">${eintraege.map((t) => `<li>${esc(t)}</li>`).join('')}</ul></div>`;
}

/** Bericht von POST api/decks/vorschau. */
function berichtHtml(b) {
  const commander = b.commander?.length ? b.commander.join(', ') : 'keiner erkannt';
  return `<div class="deck-bericht"><p class="deck-bericht-name">${esc(b.name)}</p>`
    + '<dl class="deck-bericht-zahlen">'
    + `<div><dt class="etikett">Karten</dt><dd>${zahl(b.karten)}</dd></div>`
    + `<div><dt class="etikett">Commander</dt><dd>${esc(commander)}</dd></div>`
    + `<div><dt class="etikett">Basic Lands</dt><dd>${zahl(b.basics)}</dd></div></dl>`
    + liste('Unbekannte Karten', b.unbekannt) + liste('Unlesbare Zeilen', b.unlesbar) + '</div>';
}

/** Bericht von „Neu laden“: dazu, weg, anders. */
function unterschiedeHtml(u) {
  if (u.gleich) return '<p class="dialog-text">Die Liste ist unverändert. Es gibt nichts zu übernehmen.</p>';
  return '<div class="deck-bericht">'
    + liste('Dazu', (u.dazu ?? []).map((x) => `${x.anzahl} × ${x.name}`))
    + liste('Weg', (u.weg ?? []).map((x) => `${x.anzahl} × ${x.name}`))
    + liste('Anzahl geändert', (u.anders ?? []).map((x) => `${x.name}: ${x.vorher} → ${x.nachher}`))
    + liste('Unbekannte Karten', u.unbekannt) + liste('Unlesbare Zeilen', u.unlesbar) + '</div>';
}

// ---------------------------------------------------------------- Neues Deck (Dialog)

/** Dialog „Neues Deck“. Gibt die ID des angelegten Decks zurück, sonst `undefined`. */
export async function neuesDeckFragen() {
  const form = el('form');
  form.noValidate = true;
  form.autocomplete = 'off';
  form.innerHTML = '<div class="deck-wege" role="group" aria-label="Weg wählen">'
    + '<button type="button" class="knopf deck-weg" data-weg="archidekt" aria-pressed="true">Archidekt-Link</button>'
    + '<button type="button" class="knopf deck-weg" data-weg="text" aria-pressed="false">Text einfügen</button></div>'
    + '<div class="deck-weg-archidekt"><label class="etikett feld-etikett" for="deck-neu-link">Link zum Deck</label>'
    + '<input id="deck-neu-link" class="feld" type="url" inputmode="url" autocomplete="off" spellcheck="false"'
    + ' placeholder="https://archidekt.com/decks/…" aria-describedby="deck-neu-meldung">'
    + '<p class="deck-hinweis">Das Deck muss bei Archidekt öffentlich sein.</p></div>'
    + '<div class="deck-weg-text" hidden><label class="etikett feld-etikett" for="deck-neu-name">Name</label>'
    + '<input id="deck-neu-name" class="feld" type="text" maxlength="120" autocomplete="off" spellcheck="false">'
    + '<label class="etikett feld-etikett" for="deck-neu-text">Deckliste</label>'
    + '<textarea id="deck-neu-text" class="feld deck-textfeld" rows="9" spellcheck="false" autocomplete="off"'
    + ' placeholder="1 Sol Ring&#10;1 Arcane Signet&#10;10 Forest"></textarea>'
    + '<p class="deck-hinweis">Aus Moxfield: Export → Text. Aus Archidekt: Export → Text. Eine Karte je Zeile, Anzahl vorn.</p></div>'
    + '<div class="deck-vorschau" aria-live="polite"></div>'
    + '<p class="meldung" id="deck-neu-meldung" role="alert" aria-live="assertive"></p>';

  const d = dialog({ titel: 'Neues Deck', klasse: 'deck-dialog', inhalt: form, voll: true });
  const meldung = form.querySelector('.meldung');
  const vorschauZone = form.querySelector('.deck-vorschau');
  let weg = 'archidekt';
  let geprueft = null;       // Rumpf, zu dem die gezeigte Vorschau gehört

  const rumpf = () => {
    if (weg === 'archidekt') return { archidekt: form.querySelector('#deck-neu-link').value.trim() };
    return { text: form.querySelector('#deck-neu-text').value, name: form.querySelector('#deck-neu-name').value.trim() };
  };
  const eingabeGeaendert = () => {
    geprueft = null;
    vorschauZone.replaceChildren();
    anlegen.hidden = true;
    meldung.textContent = '';
  };
  form.addEventListener('input', eingabeGeaendert);
  form.querySelector('.deck-wege').addEventListener('click', (e) => {
    const b = e.target.closest('[data-weg]');
    if (!b) return;
    weg = b.dataset.weg;
    for (const x of b.parentElement.children) x.setAttribute('aria-pressed', String(x === b));
    form.querySelector('.deck-weg-archidekt').hidden = weg !== 'archidekt';
    form.querySelector('.deck-weg-text').hidden = weg !== 'text';
    eingabeGeaendert();
  });

  const abbrechen = knopfIn(d.fuss, 'Abbrechen', '', () => d.schliessen(undefined));
  const vorschau = knopfIn(d.fuss, 'Vorschau', '', () => pruefen());
  const anlegen = knopfIn(d.fuss, 'Anlegen', 'knopf-haupt', () => los());
  anlegen.hidden = true;
  abbrechen.setAttribute('aria-label', 'Abbrechen');

  const eingaben = () => {
    const r = rumpf();
    if (weg === 'archidekt' && !r.archidekt) { meldung.textContent = 'Bitte den Link zum Deck einfügen.'; return null; }
    if (weg === 'text' && !r.name) { meldung.textContent = 'Bitte dem Deck einen Namen geben.'; return null; }
    if (weg === 'text' && !r.text.trim()) { meldung.textContent = 'Bitte die Deckliste einfügen.'; return null; }
    return r;
  };
  async function pruefen() {
    meldung.textContent = '';
    const r = eingaben();
    if (!r) return;
    vorschau.disabled = true;
    vorschauZone.innerHTML = '<p class="deck-hinweis">Wird gelesen …</p>';
    try {
      const b = await senden('/api/decks/vorschau', { daten: r });
      vorschauZone.innerHTML = berichtHtml(b);
      geprueft = JSON.stringify(r);
      anlegen.hidden = false;
      vorschauZone.scrollIntoView({ block: 'nearest', behavior: bewegungReduziert() ? 'auto' : 'smooth' });
    } catch (f) {
      vorschauZone.replaceChildren();
      meldung.textContent = f.message;
    } finally {
      vorschau.disabled = false;
    }
  }
  async function los() {
    const r = eingaben();
    if (!r || geprueft !== JSON.stringify(r)) { await pruefen(); return; }
    anlegen.disabled = true;
    meldung.textContent = '';
    try {
      const a = await senden('/api/decks', { daten: r });
      d.schliessen(a?.id);
    } catch (f) {
      meldung.textContent = f.message;
    } finally {
      anlegen.disabled = false;
    }
  }
  form.addEventListener('submit', (e) => { e.preventDefault(); });
  form.querySelector('#deck-neu-link').focus();
  return d.fertig;
}

// ---------------------------------------------------------------- Ansicht

function gruppeVon(k) {
  if (k.commander) return 'commander';
  if (k.ausgelassen) return 'ausgelassen';
  if (k.proxy) return 'proxy';
  if (k.fehlt > 0) return 'fehlt';
  return 'vorhanden';
}

function statusVon(k) {
  if (k.ausgelassen) return { text: 'ausgelassen', art: 'aus' };
  if (k.proxy) return { text: 'Proxy', art: 'proxy' };
  const teile = [];
  if (k.im_deck > 0) teile.push(`${zahl(k.im_deck)} im Deck`);
  if (k.verfuegbar > 0) teile.push(`${zahl(k.verfuegbar)} verfügbar`);
  if (k.fehlt > 0) teile.push(`fehlt ${zahl(k.fehlt)}`);
  if (k.fehlt <= 0 && !k.genau && k.druck_angegeben) teile.push('anderer Druck');
  return { text: teile.join(' · ') || 'vorhanden', art: k.fehlt > 0 ? 'fehlt' : 'da' };
}

/** „steckt in Deck X“ (zählt nicht als frei). */
function andereDecksHtml(k) {
  if (!k.andere_decks?.length) return '';
  return `<p class="deck-andere">${k.andere_decks.map((a) => `steckt in <a href="#/deck/${Number(a.deck_id)}">Deck ${esc(a.deck)}</a>`
    + `${a.anzahl > 1 ? ` (${zahl(a.anzahl)}×)` : ''}`).join(' · ')}</p>`;
}

function preisHtml(k) {
  if (k.preis == null || k.ausgelassen || k.proxy) return '';
  const summe = k.anzahl > 1 ? ` <span class="deck-preis-summe">· ${esc(euro(k.preis * k.anzahl))}</span>` : '';
  return `<span class="deck-preis">${esc(euro(k.preis))}${summe}</span>`;
}

function veraenderungText(v) {
  if (!v || (v.euro == null && v.prozent == null)) return '';
  const w = v.euro ?? v.prozent;
  const art = w > 0 ? 'auf' : w < 0 ? 'ab' : 'gleich';
  const vz = w > 0 ? '+' : w < 0 ? '−' : '±';
  const teile = [];
  if (v.euro != null) teile.push(`${vz}${euro(Math.abs(v.euro))}`);
  if (v.prozent != null) teile.push(`${vz}${Math.abs(v.prozent).toLocaleString('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} %`);
  return `<span class="preis-${art} deck-veraenderung" title="zum Vortag">${esc(teile.join(' · '))}</span>`;
}

function ortHtml(o) {
  if (o.art === 'deck') {
    if (Z && o.deck_id === Z.id) return '';
    return `<li><a class="deck-ort" href="#/deck/${Number(o.deck_id)}">${esc(`Deck ${o.deck}`)}</a>`
      + `<span class="deck-ort-zusatz">${esc(o.anzahl > 1 ? ` · ${zahl(o.anzahl)}×` : '')}</span></li>`;
  }
  const mal = o.anzahl > 1 ? ` · ${zahl(o.anzahl)}×` : '';
  const foil = o.foil ? ' · Foil' : '';
  const anders = o.genau === false ? ' · anderer Druck' : '';
  if (o.art === 'binder') {
    const text = `Binder ${o.binder}, S. ${o.seite}, Reihe ${o.zeile}, Platz ${o.spalte}`;
    return `<li><a class="deck-ort" href="#/binder/${Number(o.binder_id)}/${Number(o.seite)}">${esc(text)}</a>`
      + `<span class="deck-ort-zusatz">${esc(mal + foil + anders)}</span></li>`;
  }
  return `<li><a class="deck-ort" href="#/kiste/${Number(o.kiste_id)}">${esc(`Kiste ${o.kiste}`)}</a>`
    + `<span class="deck-ort-zusatz">${esc(mal + foil + anders)}</span></li>`;
}

function zeileHtml(k, schreiben) {
  const st = statusVon(k);
  const druck = k.druck_angegeben && k.set_code
    ? `<span class="deck-druck">${esc(String(k.set_code).toUpperCase())}${k.nummer ? ` · ${esc(k.nummer)}` : ''}</span>` : '';
  const orteHtml = (k.orte ?? []).map(ortHtml).join('');
  const orte = orteHtml ? `<ul class="deck-orte" aria-label="Orte in der Sammlung">${orteHtml}</ul>` : '';
  const proxy = schreiben && !k.ausgelassen
    ? `<button type="button" class="knopf deck-proxy" data-zeile="${Number(k.id)}" data-proxy="${k.proxy ? 1 : 0}"`
      + ` aria-pressed="${k.proxy ? 'true' : 'false'}" aria-label="Proxy für ${esc(k.name)}">Proxy</button>` : '';
  return `<li class="deck-zeile deck-zeile--${st.art}">${bildHtml(k)}`
    + '<div class="deck-zeile-text">'
    + `<p class="deck-zeile-kopf"><span class="deck-anzahl">${zahl(k.anzahl)}×</span><span class="deck-name">${esc(k.name)}</span>`
    + `${k.foil ? '<span class="deck-foil" title="Foil">Foil</span>' : ''}</p>`
    + `<p class="deck-zeile-unter">${druck}<span class="deck-status deck-status--${st.art}">${esc(st.text)}</span>${preisHtml(k)}</p>`
    + andereDecksHtml(k) + orte + '</div>' + proxy + '</li>';
}

function balkenHtml(s) {
  const gesamt = (s.im_deck ?? 0) + (s.verfuegbar ?? 0) + (s.fehlt ?? 0) + (s.proxy ?? 0) || s.karten || 1;
  const teil = (n) => `${((n ?? 0) / gesamt * 100).toFixed(2)}%`;
  return `<div class="deck-balken" role="img" aria-label="${zahl(s.im_deck)} im Deck, ${zahl(s.verfuegbar)} verfügbar, ${zahl(s.fehlt)} fehlen, ${zahl(s.proxy)} Proxy">`
    + `<span class="deck-balken-deck" style="width:${teil(s.im_deck)}"></span>`
    + `<span class="deck-balken-da" style="width:${teil(s.verfuegbar)}"></span>`
    + `<span class="deck-balken-proxy" style="width:${teil(s.proxy)}"></span>`
    + `<span class="deck-balken-fehlt" style="width:${teil(s.fehlt)}"></span></div>`
    + `<p class="deck-zahlen"><span class="deck-zahl-da">${zahl(s.vorhanden)} von ${zahl(gesamt)} vorhanden</span>`
    + `<span>${zahl(s.im_deck)} im Deck</span><span>${zahl(s.verfuegbar)} verfügbar</span>`
    + `<span>${zahl(s.fehlt)} fehlen</span><span>${zahl(s.proxy)} Proxy</span>`
    + (s.ausgelassen ? `<span>${zahl(s.ausgelassen)} ausgelassen</span>` : '') + '</p>';
}

/** Drei Werte im Kopf: Deckliste, Im Deck (mit Veränderung zum Vortag), Fehlt-Liste. */
function wertHtml(w) {
  if (!w || (w.liste == null && w.fehlt == null && w.im_deck == null)) return '';
  const feld = (titel, wert, extra = '') => `<div class="deck-wert"><dt class="etikett">${titel}</dt>`
    + `<dd>${esc(euro(wert))}${extra}</dd></div>`;
  return '<dl class="deck-werte" aria-label="Werte">' + feld('Deckliste', w.liste)
    + feld('Im Deck', w.im_deck, veraenderungText(w.veraenderung) ? ` ${veraenderungText(w.veraenderung)}` : '')
    + feld('Fehlt-Liste', w.fehlt) + '</dl>'
    + (w.ohne_preis ? `<p class="deck-hinweis deck-ohne-preis">${zahl(w.ohne_preis)} Karten ohne Preis</p>` : '');
}

const SORTIERUNGEN = [['gruppe', 'Gruppe'], ['name', 'Name'], ['preis', 'Preis']];

// ---------------------------------------------------------------- Statistik (Paket 10)

const STAT_FARBEN = { W: 'Weiß', U: 'Blau', B: 'Schwarz', R: 'Rot', G: 'Grün', C: 'Farblos' };
const STAT_TYPEN = { kreatur: 'Kreaturen', planeswalker: 'Planeswalker', spontanzauber: 'Spontanzauber',
  hexerei: 'Hexereien', artefakt: 'Artefakte', verzauberung: 'Verzauberungen', schlacht: 'Schlachten',
  land: 'Länder', sonstige: 'Sonstige' };
const dezimal = (n) => Number(n).toLocaleString('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Einfarbige Balken mit Namen, Zahl und Anteil als Text (wie im Dashboard). `zeilen`: {text, karten}. */
function statBalken(zeilen, bezug) {
  const groesster = Math.max(...zeilen.map((z) => z.karten), 1);
  return '<ul class="deck-stat-balken">' + zeilen.map((z) => {
    const anteil = bezug > 0 ? Math.round((z.karten / bezug) * 100) : 0;
    return `<li><span class="deck-stat-kopf"><span class="deck-stat-name">${esc(z.text)}</span>`
      + `<span class="deck-stat-wert">${zahl(z.karten)}<span class="deck-stat-anteil">${anteil > 0 ? `${anteil} %` : '&lt; 1 %'}</span></span></span>`
      + `<span class="deck-stat-spur" aria-hidden="true"><span style="width:${(z.karten / groesster) * 100}%"></span></span></li>`;
  }).join('') + '</ul>';
}

/** Manakurve als Säulen von einer Grundlinie, Zahl über jeder Säule. */
function statKurve(kurve) {
  const groesste = Math.max(...kurve.map((k) => k.karten), 1);
  const text = kurve.map((k) => `Manawert ${k.text}: ${zahl(k.karten)}`).join(', ');
  return `<ul class="deck-stat-saeulen" role="img" aria-label="Manakurve ohne Länder. ${esc(text)}">`
    + kurve.map((k) => `<li><span class="deck-stat-raum"><span class="deck-stat-kappe">${k.karten ? zahl(k.karten) : ''}</span>`
      + `<span class="deck-stat-saeule" style="height:${(k.karten / groesste) * 100}%"></span></span>`
      + `<span class="deck-stat-x">${esc(k.text)}</span></li>`).join('') + '</ul>';
}

function statistikHtml(s) {
  if (!s || !s.karten) return '<p class="deck-hinweis">Keine Karten in der Deckliste.</p>';
  const tafel = (titel, inhalt, unter = '') => `<div class="deck-stat-tafel"><h3 class="deck-stat-titel etikett">${titel}</h3>`
    + inhalt + (unter ? `<p class="deck-stat-unter">${unter}</p>` : '') + '</div>';
  const kopf = '<p class="deck-stat-zahlen">'
    + `<span><strong>${s.durchschnitt == null ? '–' : esc(dezimal(s.durchschnitt))}</strong> Ø Manawert ohne Länder</span>`
    + `<span>${zahl(s.nichtlaender)} ohne Länder</span><span>${zahl(s.laender)} Länder</span></p>`
    + (s.ohne_daten ? `<p class="deck-hinweis">${zahl(s.ohne_daten)} ${s.ohne_daten === 1 ? 'Karte' : 'Karten'} ohne Kartendaten zählen nicht: `
      + `${esc(s.ohne_daten_namen.join(', '))}${s.ohne_daten_namen.length < s.ohne_daten ? ' …' : ''}</p>` : '');
  const farben = s.farben.filter((f) => f.karten > 0).map((f) => ({ text: STAT_FARBEN[f.farbe] ?? f.farbe, karten: f.karten }));
  const typen = s.typen.filter((t) => t.karten > 0).map((t) => ({ text: STAT_TYPEN[t.typ] ?? t.typ, karten: t.karten }));
  return kopf + '<div class="deck-stat-tafeln">'
    + tafel('Manakurve', statKurve(s.kurve), 'Ohne Länder; doppelseitige Karten mit dem Manawert der Vorderseite.')
    + tafel('Farben', farben.length ? statBalken(farben, s.nichtlaender) : '<p class="deck-hinweis">Nur Länder.</p>',
      `Ohne Länder; mehrfarbige Karten zählen bei jeder ihrer Farben (${zahl(s.mehrfarbig)} mehrfarbig).`)
    + tafel('Kartentypen', statBalken(typen, s.mit_daten))
    + '</div>';
}

function sortiert(karten, art) {
  const kopie = [...karten];
  if (art === 'name') kopie.sort((a, b) => a.name.localeCompare(b.name, 'de'));
  else kopie.sort((a, b) => (b.preis ?? -1) - (a.preis ?? -1) || a.name.localeCompare(b.name, 'de'));
  return kopie;
}

function ueberzaehligHtml(d) {
  const u = d.ueberzaehlig ?? [];
  if (!u.length) return '';
  const ids = u.map((x) => Number(x.karte_id)).join(',');
  return '<section class="deck-gruppe deck-gruppe--ueberzaehlig" aria-label="Überzählig">'
    + `<h2 class="deck-gruppe-titel etikett">Überzählig · ${zahl(u.reduce((s, x) => s + (x.anzahl ?? 0), 0))}</h2>`
    + '<p class="deck-hinweis">Diese Karten liegen im Deck, werden aber von keiner Zeile der Liste gebraucht.</p>'
    + `<ul class="deck-zeilen">${u.map((x) => `<li class="deck-zeile deck-zeile--aus">${bildHtml(x)}<div class="deck-zeile-text">`
      + `<p class="deck-zeile-kopf"><span class="deck-anzahl">${zahl(x.anzahl)}×</span><span class="deck-name">${esc(x.name)}</span>`
      + `${x.foil ? '<span class="deck-foil">Foil</span>' : ''}</p>`
      + `<p class="deck-zeile-unter"><span class="deck-druck">${esc(String(x.set_code ?? '').toUpperCase())}${x.nummer ? ` · ${esc(x.nummer)}` : ''}</span></p>`
      + '</div></li>').join('')}</ul>`
    + (angemeldet() ? `<p><a class="knopf deck-zuruecklegen" href="#/deck/${Number(Z.id)}/zerlegen?karten=${ids}">Zurücklegen</a></p>` : '')
    + '</section>';
}

function quelleHtml(d) {
  if (d.quelle === 'archidekt' && /^\d+$/.test(String(d.extern_id ?? ''))) {
    return `<a class="deck-quelle" href="https://archidekt.com/decks/${esc(d.extern_id)}" target="_blank" rel="noopener noreferrer">Archidekt</a>`;
  }
  return '<span class="deck-quelle">Text</span>';
}

function zeichnen() {
  const z = Z;
  const d = z.daten;
  const schreiben = angemeldet();
  z.titel.textContent = d.name;
  z.quelle.innerHTML = `Quelle: ${quelleHtml(d)}`;
  z.kopfZahlen.innerHTML = wertHtml(d.wert) + balkenHtml(d.summe ?? {});
  z.statistik.hidden = !d.statistik;   // ältere Antwort ohne Statistik
  if (d.statistik) z.statistikInhalt.innerHTML = statistikHtml(d.statistik);
  const sm = d.summe ?? {};
  z.bauen.hidden = !schreiben || !(sm.verfuegbar > 0);
  z.bauen.querySelector('span').textContent = d.gebaut ? `Nachtrag: ${zahl(sm.verfuegbar)} verfügbar` : 'Deck bauen';
  z.bauen.setAttribute('href', `#/deck/${z.id}/bauen`);
  z.zurSammlung.hidden = !schreiben || Boolean(d.gebaut);
  z.zerlegen.hidden = !schreiben || !d.gebaut;
  z.zerlegen.setAttribute('href', `#/deck/${z.id}/zerlegen`);
  for (const b of z.sortierung.querySelectorAll('[data-sort]')) b.setAttribute('aria-pressed', String(b.dataset.sort === z.sort));
  z.basics.checked = Boolean(d.basics_auslassen);
  // Paket 7: Schalter „in Kaufliste“ (ab Datenbankversion 23; ältere Antworten ohne Feld blenden ihn aus)
  z.kaufSchalter.hidden = d.kaufliste == null;
  z.kauf.checked = Boolean(d.kaufliste);
  z.kauf.disabled = !schreiben;
  for (const b of z.abgleich.children) b.setAttribute('aria-pressed', String(b.dataset.abgleich === d.abgleich));
  z.umbenennen.hidden = !schreiben;
  z.loeschen.hidden = !schreiben;
  z.neuLaden.hidden = !schreiben;

  const gruppen = new Map(GRUPPEN.map((g) => [g.schluessel, []]));
  for (const k of d.karten ?? []) gruppen.get(gruppeVon(k)).push(k);
  if (z.sort !== 'gruppe') {
    z.liste.innerHTML = `<section class="deck-gruppe" aria-label="Alle Karten"><ul class="deck-zeilen">`
      + `${sortiert(d.karten ?? [], z.sort).map((k) => zeileHtml(k, schreiben)).join('')}</ul></section>` + ueberzaehligHtml(d);
    return;
  }
  z.liste.innerHTML = GRUPPEN.filter((g) => gruppen.get(g.schluessel).length).map((g) => {
    const zeilen = gruppen.get(g.schluessel);
    const summe = zeilen.reduce((s, k) => s + (k.anzahl ?? 0), 0);
    return `<section class="deck-gruppe deck-gruppe--${g.schluessel}" aria-label="${esc(g.titel)}">`
      + `<h2 class="deck-gruppe-titel etikett">${esc(g.titel)} · ${zahl(summe)}</h2>`
      + `<ul class="deck-zeilen">${zeilen.map((k) => zeileHtml(k, schreiben)).join('')}</ul></section>`;
  }).join('') + ueberzaehligHtml(d) || '<p class="deck-hinweis">Dieses Deck hat keine Karten.</p>';
}

async function zurSammlung(z) {
  const ja = await bestaetigen({ titel: 'Deck zur Sammlung hinzufügen?',
    text: 'Alle Karten der Liste kommen neu in die Sammlung, Ort ist das Deck (Basics ja, Proxys nein).', ja: 'Hinzufügen' });
  if (!ja || Z !== z) return;
  z.zurSammlung.disabled = true;
  try {
    const a = await senden(`/api/deck/${z.id}/zur-sammlung`, { daten: {} });
    await laden(z, { still: true });
    toastZeigen(z.toast, `${zahl(a.karten)} Karten (${zahl(a.posten)} Posten) liegen jetzt im Deck.`,
      { schritt: a.schritt, nachRueck: () => laden(z, { still: true }) });
  } catch (f) {
    toastZeigen(z.toast, f.message, { art: 'fehler' });
  } finally {
    z.zurSammlung.disabled = false;
  }
}

async function laden(z, { still = false } = {}) {
  try {
    z.daten = await holen(`/api/deck/${z.id}`);
  } catch (f) {
    if (Z !== z) return;
    z.meldung.textContent = /: 404$/.test(String(f.message)) ? 'Dieses Deck gibt es nicht (mehr).'
      : 'Das Deck ließ sich nicht laden. Läuft der Server?';
    return;
  }
  if (Z !== z) return;
  z.meldung.textContent = '';
  const y = window.scrollY;
  zeichnen();
  if (still) window.scrollTo({ top: y, behavior: 'instant' });
}

async function aendern(z, daten) {
  z.meldung.textContent = '';
  try {
    await senden(`/api/deck/${z.id}`, { methode: 'PATCH', daten });
  } catch (f) {
    z.meldung.textContent = f.message;
  }
  await laden(z, { still: true });
}

async function fehltKopieren(z) {
  const text = z.daten.fehlt_text ?? '';
  if (!text.trim()) { z.meldung.textContent = ''; z.status.textContent = 'Es fehlt nichts.'; return; }
  try {
    await navigator.clipboard.writeText(text);
    z.status.textContent = `Fehlt-Liste kopiert. ${HINWEIS_CARDMARKET}`;
    return;
  } catch { /* kein Zugriff auf die Zwischenablage: Textfeld zum Markieren */ }
  const feld = el('textarea', 'feld deck-textfeld');
  feld.readOnly = true;
  feld.rows = 10;
  feld.value = text;
  feld.setAttribute('aria-label', 'Fehlt-Liste');
  const inhalt = el('div');
  inhalt.append(feld, el('p', 'deck-hinweis', esc(`Markieren und kopieren. ${HINWEIS_CARDMARKET}`)));
  const dlg = dialog({ titel: 'Fehlt-Liste', klasse: 'deck-dialog', inhalt });
  knopfIn(dlg.fuss, 'Schließen', 'knopf-haupt', () => dlg.schliessen());
  feld.focus();
  feld.select();
}

async function umbenennen(z) {
  const form = el('form');
  form.noValidate = true;
  form.innerHTML = '<label class="etikett feld-etikett" for="deck-umbenennen">Name</label>'
    + `<input id="deck-umbenennen" class="feld" type="text" maxlength="120" autocomplete="off" value="${esc(z.daten.name)}">`
    + '<p class="meldung" role="alert"></p>';
  const dlg = dialog({ titel: 'Deck umbenennen', klasse: 'dialog-klein', inhalt: form });
  const meldung = form.querySelector('.meldung');
  knopfIn(dlg.fuss, 'Abbrechen', '', () => dlg.schliessen());
  const ok = knopfIn(dlg.fuss, 'Speichern', 'knopf-haupt', () => form.requestSubmit());
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = form.querySelector('input').value.trim();
    if (!name) { meldung.textContent = 'Bitte einen Namen eingeben.'; return; }
    ok.disabled = true;
    try {
      await senden(`/api/deck/${z.id}`, { methode: 'PATCH', daten: { name } });
      dlg.schliessen(true);
    } catch (f) {
      meldung.textContent = f.message;
    } finally {
      ok.disabled = false;
    }
  });
  form.querySelector('input').focus();
  if (await dlg.fertig && Z === z) await laden(z, { still: true });
}

async function neuLaden(z) {
  const archidekt = z.daten.quelle === 'archidekt';
  const form = el('form');
  form.noValidate = true;
  form.innerHTML = (archidekt
    ? '<p class="dialog-text">Das Deck wird jetzt bei Archidekt neu abgefragt.</p>'
    : '<label class="etikett feld-etikett" for="deck-neu-liste">Neue Deckliste</label>'
      + '<textarea id="deck-neu-liste" class="feld deck-textfeld" rows="9" spellcheck="false" autocomplete="off"></textarea>')
    + '<div class="deck-vorschau" aria-live="polite"></div><p class="meldung" role="alert"></p>';
  const dlg = dialog({ titel: 'Deck neu laden', klasse: 'deck-dialog', inhalt: form, voll: true });
  const meldung = form.querySelector('.meldung');
  const zone = form.querySelector('.deck-vorschau');
  let stand = null;          // Rumpf, zu dem die Vorschau gehört
  knopfIn(dlg.fuss, 'Abbrechen', '', () => dlg.schliessen());
  const vorschau = knopfIn(dlg.fuss, archidekt ? 'Vorschau holen' : 'Vorschau', '', () => holenVorschau());
  const uebernehmen = knopfIn(dlg.fuss, 'Übernehmen', 'knopf-haupt', () => ausfuehren());
  uebernehmen.hidden = true;
  const rumpf = () => (archidekt ? {} : { text: form.querySelector('textarea').value });
  form.addEventListener('input', () => { stand = null; zone.replaceChildren(); uebernehmen.hidden = true; meldung.textContent = ''; });
  form.addEventListener('submit', (e) => e.preventDefault());

  async function holenVorschau() {
    meldung.textContent = '';
    const r = rumpf();
    if (!archidekt && !r.text.trim()) { meldung.textContent = 'Bitte die neue Deckliste einfügen.'; return; }
    vorschau.disabled = true;
    zone.innerHTML = '<p class="deck-hinweis">Wird gelesen …</p>';
    try {
      const u = await senden(`/api/deck/${z.id}/neu-laden`, { daten: r });
      zone.innerHTML = unterschiedeHtml(u);
      stand = JSON.stringify(r);
      uebernehmen.hidden = Boolean(u.gleich);
    } catch (f) {
      zone.replaceChildren();
      meldung.textContent = f.message;
    } finally {
      vorschau.disabled = false;
    }
  }
  async function ausfuehren() {
    const r = rumpf();
    if (stand !== JSON.stringify(r)) { await holenVorschau(); return; }
    uebernehmen.disabled = true;
    try {
      await senden(`/api/deck/${z.id}/neu-laden`, { daten: { ...r, ausfuehren: true } });
      dlg.schliessen(true);
    } catch (f) {
      meldung.textContent = f.message;
    } finally {
      uebernehmen.disabled = false;
    }
  }
  if (archidekt) holenVorschau(); else form.querySelector('textarea').focus();
  if (await dlg.fertig && Z === z) await laden(z, { still: true });
}

async function loeschen(z) {
  const form = el('form');
  form.noValidate = true;
  form.innerHTML = `<p class="dialog-text">Das Deck „${esc(z.daten.name)}“ wird gelöscht (Karten in Binder und Kisten bleiben unberührt). `
    + 'Zur Bestätigung den Namen des Decks eintippen.</p>'
    + '<label class="etikett feld-etikett" for="deck-loeschen">Name des Decks</label>'
    + '<input id="deck-loeschen" class="feld" type="text" autocomplete="off" spellcheck="false">'
    + '<p class="meldung" role="alert"></p>';
  const dlg = dialog({ titel: 'Deck löschen', klasse: 'dialog-klein', inhalt: form });
  const meldung = form.querySelector('.meldung');
  knopfIn(dlg.fuss, 'Abbrechen', '', () => dlg.schliessen());
  const ja = knopfIn(dlg.fuss, 'Löschen', 'knopf-gefahr', () => form.requestSubmit());
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    ja.disabled = true;
    try {
      await senden(`/api/deck/${z.id}/loeschen`, { daten: { name: form.querySelector('input').value } });
      dlg.schliessen(true);
    } catch (f) {
      meldung.textContent = f.message;
    } finally {
      ja.disabled = false;
    }
  });
  form.querySelector('input').focus();
  if (await dlg.fertig) gehe('#/', { ersetzen: true });
}

function aufraeumen() {
  if (!Z) return;
  document.removeEventListener('keydown', Z.taste);
  window.removeEventListener('binder:sitzung', Z.sitzung);
  Z = null;
}

export async function zeigen(wurzel, parameter) {
  aufraeumen();
  if (parameter.modus) {
    const { bauenZeigen, zerlegenZeigen } = await import('./deckbau.js');
    return parameter.modus === 'bauen' ? bauenZeigen(wurzel, parameter) : zerlegenZeigen(wurzel, parameter);
  }
  const bereich = el('section', 'deck');
  bereich.setAttribute('aria-label', 'Deck');
  bereich.innerHTML = '<header class="deck-kopfzeile">'
    + `<button type="button" class="knopf knopf-sym deck-zurueck" aria-label="Zurück ins Regal">${ZURUECK}</button>`
    + '<div class="deck-titelblock"><h1 class="deck-titel"></h1><p class="etikett deck-quelle-zeile"></p></div></header>'
    + '<div class="deck-karte-kopf"><div class="deck-fortschritt"></div>'
    + '<div class="deck-schalter-zeile">'
    + '<label class="deck-schalter"><input type="checkbox" role="switch" class="deck-basics"><span class="deck-schalter-spur" aria-hidden="true"></span>'
    + '<span>Basic Lands auslassen</span></label>'
    + '<label class="deck-schalter deck-kaufliste-schalter" hidden><input type="checkbox" role="switch" class="deck-kaufliste">'
    + '<span class="deck-schalter-spur" aria-hidden="true"></span><span>In Kaufliste</span></label>'
    + '<div class="deck-segmente" role="group" aria-label="Abgleich"><span class="etikett deck-segmente-titel">Abgleich</span>'
    + '<button type="button" class="knopf deck-pille" data-abgleich="name" aria-pressed="false">Name</button>'
    + '<button type="button" class="knopf deck-pille" data-abgleich="druck" aria-pressed="false">Genauer Druck</button></div></div>'
    + '<div class="deck-segmente deck-sortierung" role="group" aria-label="Sortierung"><span class="etikett deck-segmente-titel">Sortierung</span>'
    + SORTIERUNGEN.map(([s, t]) => `<button type="button" class="knopf deck-pille" data-sort="${s}" aria-pressed="false">${t}</button>`).join('')
    + '</div>'
    + '<div class="deck-knoepfe deck-aktionen">'
    + '<a class="knopf knopf-haupt deck-bauen" href="#/deck/0/bauen" hidden><span>Deck bauen</span></a>'
    + '<button type="button" class="knopf deck-zur-sammlung" hidden>Deck zur Sammlung hinzufügen</button>'
    + '<a class="knopf deck-zerlegen" href="#/deck/0/zerlegen" hidden>Zerlegen</a></div>'
    + '<div class="deck-knoepfe">'
    + `<button type="button" class="knopf deck-fehlt-kopieren">${KOPIE}<span>Fehlt-Liste kopieren</span></button>`
    + '<button type="button" class="knopf deck-proxys" hidden>Proxys drucken</button>'
    + `<button type="button" class="knopf deck-neu-laden" hidden>${NEU}<span>Neu laden</span></button>`
    + '<button type="button" class="knopf deck-umbenennen" hidden>Umbenennen</button>'
    + '<button type="button" class="knopf knopf-gefahr deck-loeschen" hidden>Löschen</button></div></div>'
    + '<details class="deck-statistik"><summary class="deck-statistik-titel etikett">Statistik</summary>'
    + '<div class="deck-statistik-inhalt"></div></details>'
    + '<div class="deck-toast" role="status" hidden></div>'
    + '<p class="deck-hinweis deck-meldung" role="alert"></p>'
    + '<p class="nur-vorleser" role="status" aria-live="polite"></p>'
    + '<div class="deck-liste"><p class="deck-hinweis">Deck wird geladen …</p></div>';
  wurzel.replaceChildren(bereich);
  linksAnschliessen(bereich);
  bilderAbsichern(bereich);

  const z = {
    id: Number(parameter.deck), bereich, daten: null,
    titel: bereich.querySelector('.deck-titel'), quelle: bereich.querySelector('.deck-quelle-zeile'),
    kopfZahlen: bereich.querySelector('.deck-fortschritt'), basics: bereich.querySelector('.deck-basics'),
    abgleich: bereich.querySelector('.deck-segmente'), liste: bereich.querySelector('.deck-liste'),
    neuLaden: bereich.querySelector('.deck-neu-laden'), umbenennen: bereich.querySelector('.deck-umbenennen'),
    loeschen: bereich.querySelector('.deck-loeschen'), meldung: bereich.querySelector('.deck-meldung'),
    status: bereich.querySelector('.nur-vorleser'), taste: null, sitzung: null,
    toast: bereich.querySelector('.deck-toast'), bauen: bereich.querySelector('.deck-bauen'),
    zurSammlung: bereich.querySelector('.deck-zur-sammlung'), zerlegen: bereich.querySelector('.deck-zerlegen'),
    sortierung: bereich.querySelector('.deck-sortierung'),
    kauf: bereich.querySelector('.deck-kaufliste'), kaufSchalter: bereich.querySelector('.deck-kaufliste-schalter'),
    statistik: bereich.querySelector('.deck-statistik'),
    statistikInhalt: bereich.querySelector('.deck-statistik-inhalt'),
    sort: SORTIERUNGEN.some(([s]) => s === vorliebe('deck.sort')) ? vorliebe('deck.sort') : 'gruppe',
  };
  Z = z;
  z.statistik.open = vorliebe('deck.statistik') !== 'zu';
  z.statistik.addEventListener('toggle', () => vorliebe('deck.statistik', z.statistik.open ? 'auf' : 'zu'));
  z.zurSammlung.addEventListener('click', () => zurSammlung(z));
  z.sortierung.addEventListener('click', (e) => {
    const b = e.target.closest('[data-sort]');
    if (!b) return;
    z.sort = b.dataset.sort;
    vorliebe('deck.sort', z.sort);
    if (z.daten) zeichnen();
  });
  if (wartendeMeldung) {
    const m = wartendeMeldung;
    wartendeMeldung = null;
    toastZeigen(z.toast, m.text, { schritt: m.schritt, nachRueck: () => laden(z, { still: true }) });
  }
  bereich.querySelector('.deck-zurueck').addEventListener('click', () => zurueck('#/'));
  z.basics.addEventListener('change', () => aendern(z, { basics_auslassen: z.basics.checked }));
  z.kauf.addEventListener('change', async () => {
    const an = z.kauf.checked;
    try {
      const a = await senden(`/api/kaufliste/deck/${z.id}`, { daten: { an } });
      await laden(z, { still: true });
      if (a.schritt) {
        toastZeigen(z.toast, an ? 'Die fehlenden Karten stehen jetzt in der Kaufliste.' : 'Das Deck ist aus der Kaufliste genommen.',
          { schritt: a.schritt, nachRueck: () => laden(z, { still: true }) });
      }
    } catch (f) {
      toastZeigen(z.toast, f.message, { art: 'fehler' });
      await laden(z, { still: true });
    }
  });
  z.abgleich.addEventListener('click', (e) => {
    const b = e.target.closest('[data-abgleich]');
    if (b && b.getAttribute('aria-pressed') !== 'true') aendern(z, { abgleich: b.dataset.abgleich });
  });
  bereich.querySelector('.deck-fehlt-kopieren').addEventListener('click', () => fehltKopieren(z));
  // „Proxys drucken“ (Wunsch des Nutzers, 07.11.2026): nur, wenn es den Bereich gibt (Add-on; am PC liefert er 404)
  const proxys = bereich.querySelector('.deck-proxys');
  proxys.addEventListener('click', () => gehe(`#/proxy/deck/${Number(z.id)}`));
  holen('/api/proxy').then(() => { proxys.hidden = false; }).catch(() => { /* kein Proxy-Bereich */ });
  z.neuLaden.addEventListener('click', () => neuLaden(z));
  z.umbenennen.addEventListener('click', () => umbenennen(z));
  z.loeschen.addEventListener('click', () => loeschen(z));
  z.liste.addEventListener('click', async (e) => {
    const b = e.target.closest('.deck-proxy');
    if (!b) return;
    b.disabled = true;
    z.meldung.textContent = '';
    try {
      await senden(`/api/deck/${z.id}/zeile/${b.dataset.zeile}/proxy`, { daten: { proxy: b.dataset.proxy !== '1' } });
    } catch (f) {
      z.meldung.textContent = f.message;
    }
    await laden(z, { still: true });
  });
  z.sitzung = () => { if (Z === z && z.daten) zeichnen(); };
  window.addEventListener('binder:sitzung', z.sitzung);
  z.taste = (e) => {
    if (e.key !== 'Escape' || dialogOffen() || Z !== z) return;
    zurueck('#/');
  };
  document.addEventListener('keydown', z.taste);

  await laden(z);
  if (Z === z && !z.daten) z.liste.replaceChildren();
}

/** Dieselbe Ansicht „deck“, aber andere Adresse (Deck, Entnahme, Zerlegen): immer neu aufbauen. */
export async function aktualisieren(parameter) {
  const wurzel = document.getElementById('buehne');
  const nochDa = Z && !parameter.modus && Z.id === Number(parameter.deck);
  if (nochDa) return;
  aufraeumen();
  const { aufraeumenBau } = await import('./deckbau.js');
  aufraeumenBau();
  wurzel.replaceChildren();
  await zeigen(wurzel, parameter);
}

export async function verlassen() {
  aufraeumen();
  if (!wartendeMeldung) import('./deckbau.js').then((m) => m.aufraeumenBau());
}
