// Kistenansicht (Bulkkisten): Karteikasten, Raster und Liste mit serverseitigem Sortieren, Gruppieren,
// Filtern und nachladendem Blättern.
//
//   #/kiste/5             Kiste 5
//   #/kiste/5/karte/12    dazu Karte 12 groß (die Kartenebene öffnet app.js; hier wird nur navigiert)
//   #/kiste/5/fach/2      Fach 2 herausgezogen: nur seine Karten („ohne“: Karten ohne Fach), dazu /karte/12
//                         Eine Kiste mit Fächern zeigt zuerst sich selbst mit Trennkarten (Fachkasten); der
//                         Umschalter „Alle Karten“ führt zur Gesamtansicht (gemerkt je Kiste).
//   #/sammlung            Gesamtsammlung (Binder und Kisten): dieselbe Ansicht mit `parameter.sammlung`,
//                         Kopf aus /api/sammlung, Karten aus /api/sammlung/karten, dazu Ort, Quelle, Doppel
//   #/sammlung/karte/12   dazu Karte 12 groß
//
// Schreiben (nur angemeldet, alles über senden() mit Rückgängig über /api/schritt/{id}/rueckgaengig):
// Liste importieren (Probelauf, dann Importieren), Karte hinzufügen (Katalog nötig), Auswahlmodus mit
// „Verschieben nach …“, in der Sammlung zusätzlich „Liste abziehen“ (abzug.js).
//
// Technik: Die Karten kommen in Seiten von 60 vom Server (`/api/kiste/{id}/karten`). Geladen ist immer
// ein zusammenhängender Bereich [start, ende) der sortierten Ergebnisse. Ein IntersectionObserver lädt
// unten (und nach einem Sprung in eine Gruppe auch oben) nach, sobald das Ende naht. Bei jeder
// Änderung von Sortierung, Gruppierung oder Filter werden laufende Anfragen abgebrochen (AbortController)
// und späte Antworten verworfen (Zählung `gen`). Es werden nur transform und opacity animiert.

import { esc, bildUrl, gehe, zurueck, senden, fehlerText, SchnittstellenFehler, vorliebe, bewegungReduziert,
  adresse as serverAdresse, scryfallBild, euro, preisStreifen, preisText, warten } from './gemeinsam.js';
import { knopf as anmeldeKnopf, angemeldet, sitzung } from './anmeldung.js';
import { dialog, bestaetigen, dialogOffen } from './dialog.js';
import { druckWaehlen } from './pruefen.js';
import { KISTENFARBEN, farbwahl, kistenAussehen } from './farbrad.js';

const SEITE = 60;
const ZUSTAENDE = [
  { wert: 'mint', text: 'Neuwertig (Mint)' }, { wert: 'near_mint', text: 'Fast neuwertig (Near Mint)' },
  { wert: 'excellent', text: 'Sehr gut (Excellent)' }, { wert: 'good', text: 'Gut (Good)' },
  { wert: 'light_played', text: 'Leicht gespielt (Light Played)' }, { wert: 'played', text: 'Gespielt (Played)' },
  { wert: 'poor', text: 'Stark gespielt (Poor)' },
];
const HANDY = '(max-width: 699px)';
const ANSICHTEN = ['kasten', 'raster', 'liste'];

const SPRACHEN = {
  de: 'Deutsch', en: 'Englisch', fr: 'Französisch', it: 'Italienisch', es: 'Spanisch', pt: 'Portugiesisch',
  ja: 'Japanisch', ko: 'Koreanisch', ru: 'Russisch', zhs: 'Chinesisch (vereinfacht)',
  zht: 'Chinesisch (traditionell)', ph: 'Phyrexianisch', he: 'Hebräisch', la: 'Lateinisch',
  grc: 'Altgriechisch', ar: 'Arabisch', sa: 'Sanskrit', qya: 'Quenya',
};
const sprachName = (code) => SPRACHEN[code] ?? (code ? String(code).toUpperCase() : 'ohne Angabe');
const MANA_NAMEN = { W: 'Weiß', U: 'Blau', B: 'Schwarz', R: 'Rot', G: 'Grün', C: 'farblos', S: 'Schnee',
  E: 'Energie', X: 'X', Y: 'Y', Z: 'Z' };

const zahl = (n) => Number(n ?? 0).toLocaleString('de-DE');
const zahlText = (n) => String(n).replace('.', ',');

// ---------- Symbole (schlichte Striche, currentColor) ----------

const sym = (d, g = 18) => `<svg viewBox="0 0 24 24" width="${g}" height="${g}" fill="none" stroke="currentColor"`
  + ` stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${d}</svg>`;
const SYM = {
  zurueck: sym('<path d="M15 5l-7 7 7 7"/>'),
  stift: sym('<path d="M5 19l1-4L16.5 4.5a2 2 0 0 1 3 3L9 18z"/>'),
  muell: sym('<path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12"/>'),
  kreuz: sym('<path d="M6 6l12 12M18 6L6 18"/>', 14),
  haken: sym('<path d="M4 12.5l5 5L20 6.5"/>', 12),
  filter: sym('<path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/>'),
  balken: sym('<path d="M6 20V10M12 20V4M18 20v-7"/>'),
  pfeil: sym('<path d="M12 19V5M6 11l6-6 6 6"/>'),
  hoch: sym('<path d="M12 19V6M6 12l6-6 6 6"/>'),
  kasten: sym('<path d="M5 8l7-4 7 4-7 4z"/><path d="M5 12l7 4 7-4M5 16l7 4 7-4"/>'),
  raster: sym('<rect x="4" y="4" width="6.5" height="6.5" rx="1"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1"/>'
    + '<rect x="4" y="13.5" width="6.5" height="6.5" rx="1"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1"/>'),
  liste: sym('<path d="M4 7h16M4 12h16M4 17h16"/>'),
  plus: sym('<path d="M12 5v14M5 12h14"/>'),
  hochladen: sym('<path d="M12 16V5M7 10l5-5 5 5M5 19h14"/>'),
  wahl: sym('<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8 12.5l3 3 5-6"/>'),
  abzug: sym('<path d="M7 4h10v16H7zM10 9h4M10 13h4"/>'),
  weiter: sym('<path d="M4 12h14M13 7l5 5-5 5"/>'),
  stern: sym('<path d="M12 4l2.4 5 5.4.7-4 3.8 1 5.4L12 16.2 7.2 18.9l1-5.4-4-3.8 5.4-.7z"/>'),
  faecher: sym('<rect x="4" y="4" width="16" height="6" rx="1.5"/><rect x="4" y="14" width="16" height="6" rx="1.5"/>'),
  kopie: sym('<rect x="9" y="9" width="11" height="11" rx="1.5"/><path d="M15 9V5.5A1.5 1.5 0 0 0 13.5 4h-8A1.5 1.5 0 0 0 4 5.5v8A1.5 1.5 0 0 0 5.5 15H9"/>'),
};
const ANSICHT_TEXT = { kasten: 'Karteikasten', raster: 'Raster', liste: 'Liste' };

// ---------- Zustand ----------

let optionenKarte = null;       // Antwort von /api/kisten/optionen (einmal pro Seitenaufruf)
let S = null;                   // Zustand der gerade gezeigten Kistenansicht

function el(tag, klasse, html) {
  const e = document.createElement(tag);
  if (klasse) e.className = klasse;
  if (html !== undefined) e.innerHTML = html;
  return e;
}

/** Holt JSON; wirft SchnittstellenFehler mit deutschem Satz (aus {"fehler": …}) oder AbortError. */
async function anfordern(pfad, signal) {
  let antwort;
  try {
    antwort = await fetch(serverAdresse(pfad), { headers: { Accept: 'application/json' }, signal });
  } catch (f) {
    if (f?.name === 'AbortError') throw f;
    throw new SchnittstellenFehler(fehlerText(0), 0);
  }
  let inhalt = null;
  try { inhalt = await antwort.json(); } catch { /* keine JSON-Antwort */ }
  if (signal?.aborted) throw new DOMException('abgebrochen', 'AbortError');
  if (!antwort.ok) throw new SchnittstellenFehler(inhalt?.fehler || fehlerText(antwort.status), antwort.status, inhalt);
  if (inhalt === null) throw new SchnittstellenFehler(fehlerText(500), antwort.status);
  return inhalt;
}

async function optionenHolen() {
  if (!optionenKarte) optionenKarte = await anfordern('/api/kisten/optionen');
  return optionenKarte;
}

// ---------- Filter ----------

const leererFilter = () => ({ farbe: [], mw_von: null, mw_bis: null, seltenheit: [], typ: [], sprache: [],
  foil: null, set: [], anzahl_min: null, q: '', ort: [], quelle: [], doppel: null });
const QUELLEN = ['scan', 'manabox', 'manuell'];

function filterLesen(roh, optionen) {
  const f = leererFilter();
  let o = null;
  try { o = roh ? JSON.parse(roh) : null; } catch { o = null; }
  if (!o || typeof o !== 'object') return f;
  const liste = (name, erlaubt) => (Array.isArray(o[name])
    ? o[name].filter((w) => typeof w === 'string' && (!erlaubt || erlaubt.includes(w))).slice(0, 40) : []);
  f.farbe = liste('farbe', optionen.farben.map((x) => x.wert));
  f.seltenheit = liste('seltenheit', optionen.seltenheiten.map((x) => x.wert));
  f.typ = liste('typ', optionen.typen.map((x) => x.wert));
  f.sprache = liste('sprache').filter((w) => /^[a-z]{2,3}$/.test(w));
  f.set = liste('set').filter((w) => /^[a-z0-9]{2,8}$/.test(w));
  const ganz = (w, von, bis) => (Number.isInteger(w) && w >= von && w <= bis ? w : null);
  f.mw_von = ganz(o.mw_von, 0, 20);
  f.mw_bis = ganz(o.mw_bis, 0, 20);
  f.foil = o.foil === 0 || o.foil === 1 ? o.foil : null;
  f.anzahl_min = ganz(o.anzahl_min, 1, 999);
  f.q = typeof o.q === 'string' ? o.q.slice(0, 60) : '';
  f.ort = liste('ort').filter((w) => /^[kbd][0-9]{1,9}$/.test(w));       // nur in der Sammlung wirksam
  f.quelle = liste('quelle', QUELLEN);
  f.doppel = o.doppel === 1 ? 1 : null;
  return f;
}

/** Abfrage-Parameter der Filter; Ort, Quelle und Doppel gibt es nur in der Gesamtsammlung. */
function filterParameter(f, sammlung = false) {
  const p = new URLSearchParams();
  const listen = ['farbe', 'seltenheit', 'typ', 'sprache', 'set', ...(sammlung ? ['ort', 'quelle'] : [])];
  for (const name of listen) if (f[name].length) p.set(name, f[name].join(','));
  if (sammlung && f.doppel === 1) p.set('doppel', '1');
  if (f.mw_von !== null) p.set('mw_von', f.mw_von);
  if (f.mw_bis !== null) p.set('mw_bis', f.mw_bis);
  if (f.foil !== null) p.set('foil', f.foil);
  if (f.anzahl_min !== null) p.set('anzahl_min', f.anzahl_min);
  if (f.q.trim()) p.set('q', f.q.trim());
  return p;
}

const ortName = (o) => `${o.art === 'kiste' ? 'Kiste' : o.art === 'deck' ? 'Deck' : 'Binder'} ${o.name}`;

/** Aktive Filter als Liste für die Pillen oben: { name, wert, text }. */
function aktiveFilter(s) {
  const f = s.filter;
  const o = s.optionen;
  const text = (liste, wert) => liste.find((x) => x.wert === wert)?.text ?? wert;
  const ergebnis = [];
  for (const w of f.farbe) ergebnis.push({ name: 'farbe', wert: w, text: `Farbe ${text(o.farben, w)}` });
  if (f.mw_von !== null || f.mw_bis !== null) {
    const t = f.mw_von !== null && f.mw_bis !== null
      ? (f.mw_von === f.mw_bis ? `Manawert ${f.mw_von}` : `Manawert ${f.mw_von} bis ${f.mw_bis}`)
      : (f.mw_von !== null ? `Manawert ab ${f.mw_von}` : `Manawert bis ${f.mw_bis}`);
    ergebnis.push({ name: 'manawert', wert: '', text: t });
  }
  for (const w of f.seltenheit) ergebnis.push({ name: 'seltenheit', wert: w, text: text(o.seltenheiten, w) });
  for (const w of f.typ) ergebnis.push({ name: 'typ', wert: w, text: text(o.typen, w) });
  for (const w of f.sprache) ergebnis.push({ name: 'sprache', wert: w, text: sprachName(w) });
  if (f.foil !== null) ergebnis.push({ name: 'foil', wert: String(f.foil), text: f.foil ? 'Nur Foil' : 'Kein Foil' });
  for (const w of f.set) ergebnis.push({ name: 'set', wert: w, text: `Set ${w.toUpperCase()}` });
  if (s.sammlung) {
    for (const w of f.ort) {
      const ort = s.kopf.orte.find((x) => x.wert === w);
      ergebnis.push({ name: 'ort', wert: w, text: ort ? ortName(ort) : `Ort ${w}` });
    }
    for (const w of f.quelle) ergebnis.push({ name: 'quelle', wert: w, text: `Quelle ${text(s.kopf.quellen, w)}` });
    if (f.doppel === 1) ergebnis.push({ name: 'doppel', wert: '1', text: 'Mögliche Doppel' });
  }
  if (f.anzahl_min !== null) ergebnis.push({ name: 'anzahl_min', wert: '', text: `Anzahl ab ${f.anzahl_min}` });
  if (f.q.trim()) ergebnis.push({ name: 'q', wert: '', text: `Name „${f.q.trim()}“` });
  return ergebnis;
}

// ---------- Manakosten als lesbarer Text ----------

const manaCache = new Map();

function manaSymbol(s) {
  if (/^\d+$/.test(s)) return { kurz: s, lang: s };
  const teile = s.split('/');
  const phyrexisch = teile.includes('P');
  const farben = teile.filter((t) => t !== 'P');
  const lang = farben.map((t) => (/^\d+$/.test(t) ? t : (MANA_NAMEN[t] ?? t))).join('/');
  return { kurz: s, lang: phyrexisch ? `${lang} (oder 2 Leben)` : lang };
}

/** `{2}{G}` wird zu { kurz: "2 G", lang: "2 + Grün" }; mehrseitige Karten mit „ // “. */
function manaText(kosten) {
  if (!kosten) return null;
  let t = manaCache.get(kosten);
  if (t) return t;
  const seiten = String(kosten).split(' // ').map((seite) => [...seite.matchAll(/\{([^}]+)\}/g)].map((m) => manaSymbol(m[1])));
  t = {
    kurz: seiten.map((sy) => sy.map((x) => x.kurz).join(' ')).filter(Boolean).join(' // '),
    lang: seiten.map((sy) => sy.map((x) => x.lang).join(' + ')).filter(Boolean).join(' // '),
  };
  if (!t.kurz) t = null;
  manaCache.set(kosten, t);
  return t;
}

// ---------- Darstellung einer Karte ----------

/** Abzeichen für unsichere Erkennung (siehe Vorgaben). */
function abzeichen(k) {
  if (!k.markierung) return '';
  const unsicher = k.markierung === 'unsicher';
  const text = unsicher
    ? (k.geprueft ? 'Erkennung unsicher – von dir geprüft' : 'Erkennung unsicher')
    : (k.geprueft ? 'Druck unsicher – von dir geprüft' : 'Name sicher, Druck offen');
  const inhalt = k.geprueft ? SYM.haken : (unsicher ? '?' : 'i');
  return `<span class="kiste-ab ${unsicher ? 'kiste-ab-unsicher' : 'kiste-ab-druck'}${k.geprueft ? ' kiste-ab-geprueft' : ''}"`
    + ` role="img" title="${text}" aria-label="${text}">${inhalt}</span>`;
}

function setText(k) {
  if (!k.set) return '';
  return k.nummer ? `${k.set.toUpperCase()} ${k.nummer}` : k.set.toUpperCase();
}

/** Satz für Vorleser und title. */
function beschriftung(k) {
  const o = S.optionenText;
  const teile = [`${k.anzahl}× ${k.name}`];
  const st = setText(k);
  if (st) teile.push(st);
  if (k.manawert != null) teile.push(`Manawert ${zahlText(k.manawert)}`);
  if (k.haupttyp && o.typ[k.haupttyp]) teile.push(o.typ[k.haupttyp]);
  if (k.seltenheit && o.seltenheit[k.seltenheit]) teile.push(o.seltenheit[k.seltenheit]);
  if (k.sprache) teile.push(sprachName(k.sprache));
  if (k.foil) teile.push('Foil');
  if (k.markierung === 'unsicher') teile.push(k.geprueft ? 'Erkennung unsicher, von dir geprüft' : 'Erkennung unsicher');
  else if (k.markierung) teile.push(k.geprueft ? 'Druck unsicher, von dir geprüft' : 'Name sicher, Druck offen');
  if (k.ort?.text) teile.push(k.ort.text);
  if (k.fach) teile.push(`Fach ${k.fach}`);
  if (k.doppel) teile.push('mögliches Doppel');
  if (k.preis) teile.push(preisText(k.preis));
  return teile.join(', ');
}

/** Foil-Markierung: kleiner Stern neben dem Namen (Liste, Karteikasten). */
const fStern = (k) => (k.foil ? `<span class="kiste-fstern" role="img" aria-label="Foil" title="Foil">${SYM.stern}</span>` : '');

const adresse = (k) => `${S.basis}/karte/${k.id}`;
const gewaehlt = (k) => (S.gewaehlt.has(k.id) ? ' kiste-gewaehlt' : '');
const ortZeile = (k) => (S.sammlung ? k.ort?.text ?? '' : '');

/** Fach der Karte als kleines Schild; nach Fach gruppiert sagt das schon die Trennkarte. */
const fachSichtbar = (k) => Boolean(k.fach) && S.gruppe !== 'fach' && S.fach === null;
const fachHtml = (k) => (fachSichtbar(k) ? `<span class="kiste-fach" title="Fach ${esc(k.fach)}">${esc(k.fach)}</span>` : '');

/** Kleines Kennzeichen „mögliches Doppel“ (nur in der Gesamtsammlung). */
function doppelHtml(k) {
  return k.doppel ? '<span class="kiste-doppel" role="img" aria-label="mögliches Doppel"'
    + ' title="Mögliches Doppel: Derselbe Druck liegt auch in einem Binder bzw. in einer Kiste">Doppel</span>' : '';
}

function bildBreite() {
  return window.matchMedia('(min-width: 700px)').matches ? 400 : 200;
}

function flaecheHtml(k) {
  const st = setText(k);
  return `<span class="kiste-flaeche"><span class="kiste-flaeche-name">${esc(k.name)}</span>`
    + (st ? `<span class="kiste-flaeche-set">${esc(st)}</span>` : '') + '</span>';
}

function listeHtml(k, i) {
  const o = S.optionenText;
  const mana = manaText(k.manakosten);
  const typ = k.haupttyp ? (o.typ[k.haupttyp] ?? '') : '';
  const selt = k.seltenheit ? (o.seltenheit[k.seltenheit] ?? '') : '';
  const ort = ortZeile(k);
  return `<li><a class="kiste-zeile${gewaehlt(k)}" href="${adresse(k)}" data-karte="${k.id}" data-i="${i}"`
    + ` aria-label="${esc(beschriftung(k))}">`
    + `<span class="z-anz${k.anzahl > 1 ? ' z-mehr' : ''}">×${k.anzahl}</span>`
    + `<span class="z-name"><span class="z-text">${esc(k.name)}</span>${fStern(k)}${abzeichen(k)}${doppelHtml(k)}${fachHtml(k)}</span>`
    + `<span class="z-mw"${mana ? ` title="${esc(mana.lang)}"` : ''}>${k.manawert == null ? '–' : esc(zahlText(k.manawert))}</span>`
    + '<span class="z-meta">'
    + `<span class="z-set">${esc(setText(k)) || '–'}</span>`
    + `<span class="z-typ">${esc(typ)}</span>`
    + `<span class="z-selt">${esc(selt)}</span>`
    + `<span class="z-spr" title="${esc(sprachName(k.sprache))}">${esc((k.sprache ?? '').toUpperCase())}</span>`
    + `<span class="z-foil">${k.foil ? 'Foil' : ''}</span>`
    + `<span class="z-preis">${k.preis?.avg7 != null ? `Ø ${esc(euro(k.preis.avg7))}` : ''}</span>`
    + '</span>'
    + (S.sammlung ? `<span class="z-ort" title="${esc(ort)}">${esc(ort) || '–'}</span>` : '')
    + '</a></li>';
}

function rasterHtml(k, i) {
  const bild = k.bild
    ? `<img class="kiste-bild" src="${bildUrl('scryfall', k.bild, bildBreite())}" width="63" height="88" alt=""`
      + ' loading="lazy" decoding="async">'
    : flaecheHtml(k);
  const ort = ortZeile(k);
  return `<li><a class="kiste-kachel${k.bild ? '' : ' kiste-fehlt'}${gewaehlt(k)}" href="${adresse(k)}" data-karte="${k.id}" data-i="${i}"`
    + ` data-name="${esc(k.name)}" data-set="${esc(setText(k))}" aria-label="${esc(beschriftung(k))}">`
    + bild + abzeichen(k)
    + (k.anzahl > 1 ? `<span class="kachel-anz">×${k.anzahl}</span>` : '')
    + (k.foil ? `<span class="kachel-foil" role="img" aria-label="Foil" title="Foil">${SYM.stern}</span>`
      + '<span class="kiste-schimmer" aria-hidden="true"></span>' : '')
    + preisStreifen(k.preis)
    + '</a>'
    + (S.sammlung ? `<span class="kachel-unter" title="${esc(ort)}">${esc(ort) || '–'}${doppelHtml(k)}</span>` : '')
    + (fachSichtbar(k) ? `<span class="kachel-unter" title="Fach ${esc(k.fach)}">${esc(k.fach)}</span>` : '')
    + '</li>';
}

function kastenHtml(k, i) {
  const mana = manaText(k.manakosten);
  const st = setText(k);
  const ort = ortZeile(k);
  return `<li><a class="kiste-kante${gewaehlt(k)}" href="${adresse(k)}" data-karte="${k.id}" data-i="${i}"`
    + ` data-f="${esc(k.farbgruppe ?? '')}" data-b="${esc(k.bild ?? '')}" aria-label="${esc(beschriftung(k))}">`
    + (S.sammlung
      ? `<span class="ks-name"><span class="ks-text"><span class="z-text">${esc(k.name)}</span>`
        + `<span class="ks-ort">${esc(ort) || '–'}</span></span>${fStern(k)}${abzeichen(k)}${doppelHtml(k)}</span>`
      : `<span class="ks-name"><span class="z-text">${esc(k.name)}</span>${fStern(k)}${abzeichen(k)}${fachHtml(k)}</span>`)
    + (mana ? `<span class="ks-mana" title="${esc(mana.lang)}"><span class="ks-k">${esc(mana.kurz)}</span><span class="ks-l">${esc(mana.lang)}</span></span>` : '')
    + (k.anzahl > 1 ? `<span class="ks-anz">×${k.anzahl}</span>` : '')
    + (k.foil ? '<span class="ks-foil">Foil</span>' : '')
    + `<span class="ks-set">${esc(st)}</span></a></li>`;
}

function trennerHtml(g, i) {
  const teile = String(g.text).split(' · ');
  const kurz = teile[0];
  const lang = teile.slice(1).join(' · ');
  return `<li class="kiste-trenn" data-i="${i}" data-gruppe="${esc(g.schluessel)}" role="heading" aria-level="2"`
    + ` aria-label="${esc(g.text)}, ${zahl(g.posten)} Posten"`
    + ` title="${esc(g.text)}: ${zahl(g.posten)} Posten, ${zahl(g.karten)} Karten">`
    + `<span class="tr-reiter"><span class="tr-kurz">${esc(kurz)}</span><span class="tr-zahl">${zahl(g.posten)}</span></span>`
    + (lang ? `<span class="tr-lang">${esc(lang)}</span>` : '') + '</li>';
}

const HTML_JE_ANSICHT = { kasten: kastenHtml, raster: rasterHtml, liste: listeHtml };

/** HTML für die Karten ab Index `ab` (mit Trennkarten an den Gruppenanfängen). */
function teilHtml(karten, ab) {
  const fn = HTML_JE_ANSICHT[S.ansicht];
  const teile = new Array(karten.length);
  for (let j = 0; j < karten.length; j += 1) {
    const g = S.gruppeBei.get(ab + j);
    teile[j] = (g ? trennerHtml(g, ab + j) : '') + fn(karten[j], ab + j);
  }
  return teile.join('');
}

// ---------- Aufbau ----------

function pille(filter, wert, text, an, zusatz = '') {
  return `<button type="button" class="knopf kiste-pille" data-filter="${filter}" data-wert="${esc(wert)}"`
    + ` aria-pressed="${an}">${esc(text)}${zusatz}</button>`;
}

function mwOptionen(max, leer) {
  let h = `<option value="">${leer}</option>`;
  for (let n = 0; n <= Math.max(max, 1); n += 1) h += `<option value="${n}">${n}</option>`;
  return h;
}

function geruestHtml(s) {
  const o = s.optionen;
  const w = s.kopf.werte;
  const sortierung = o.sortierungen.map((x) => `<option value="${x.wert}">${esc(x.text)}</option>`).join('');
  const gruppierung = '<option value="">Ohne Gruppierung</option>'
    + s.gruppierungen.map((x) => `<option value="${x.wert}">${esc(x.text)}</option>`).join('');
  const ortPillen = s.sammlung ? s.kopf.orte.map((x) => pille('ort', x.wert,
    `${ortName(x)} · ${zahl(x.karten)}`, false)).join('') : '';
  const quellenPillen = s.sammlung ? s.kopf.quellen.map((x) => pille('quelle', x.wert,
    `${x.text} · ${zahl(x.karten)}`, false)).join('') : '';
  const ansichten = ANSICHTEN.map((a) => `<button type="button" class="knopf kiste-segment" data-ansicht="${a}"`
    + ` aria-pressed="false">${SYM[a]}<span>${ANSICHT_TEXT[a]}</span></button>`).join('');
  const sprachen = w.sprachen.map((x) => pille('sprache', x.code, `${sprachName(x.code)} · ${zahl(x.posten)}`, false)).join('');
  const sets = '<option value="">Set hinzufügen …</option>' + w.sets.map((x) => `<option value="${esc(x.code)}">`
    + `${esc(x.code.toUpperCase())}${x.name ? ` · ${esc(x.name)}` : ''} (${zahl(x.posten)})</option>`).join('');
  return `
    <header class="kiste-kopf">
      <div class="kiste-kopfzeile">
        <button type="button" class="knopf kiste-zurueck" aria-label="Zurück ins Regal">${SYM.zurueck}<span class="kiste-zurueck-text">Zurück ins Regal</span></button>
        <div class="kiste-aktionen"></div>
      </div>
      <h1 class="kiste-titel"></h1>
      <p class="kiste-zahlen etikett" aria-live="polite"></p>
      <div class="kiste-sammlung-info" hidden></div>
      <div class="kiste-admin" role="group" aria-label="Bearbeiten" hidden></div>
      <p class="meldung kiste-meldung" role="alert"></p>
    </header>
    <div class="kiste-toast" role="status" aria-live="polite" hidden></div>
    <div class="kiste-fachwechsel" hidden>
      <div class="kiste-segmente" role="group" aria-label="Kiste mit Fächern oder alle Karten zeigen">
        <button type="button" class="knopf kiste-segment" data-fachmodus="faecher" aria-pressed="false">${SYM.faecher}<span>Fächer</span></button>
        <button type="button" class="knopf kiste-segment" data-fachmodus="alle" aria-pressed="false">${SYM.raster}<span>Alle Karten</span></button>
      </div>
    </div>
    <div class="kiste-fachkopf" hidden>
      <span class="fachkopf-reiter"></span>
      <div class="fachkopf-zeile">
        <span class="fachkopf-regel"></span>
        <span class="fachkopf-zahl"></span>
        <button type="button" class="knopf fachkopf-zu">${SYM.zurueck}<span>In die Kiste zurück</span></button>
      </div>
    </div>
    <div class="kiste-bedienung" role="toolbar" aria-label="Ansicht, Sortierung, Gruppierung und Filter">
      <div class="kiste-segmente" role="group" aria-label="Ansicht">${ansichten}</div>
      <div class="kiste-felder">
        <div class="kiste-feld kiste-feld-sort">
          <label class="etikett" for="kiste-sort">Sortieren</label>
          <div class="kiste-feldzeile">
            <select id="kiste-sort" class="feld">${sortierung}</select>
            <button type="button" class="knopf knopf-sym kiste-richtung" aria-label="Aufsteigend">${SYM.pfeil}</button>
          </div>
        </div>
        <div class="kiste-feld kiste-feld-gruppe">
          <label class="etikett" for="kiste-gruppe">Gruppieren</label>
          <select id="kiste-gruppe" class="feld">${gruppierung}</select>
        </div>
        <div class="kiste-feld kiste-feld-sprung" hidden>
          <label class="etikett" for="kiste-sprung">Springen zu</label>
          <select id="kiste-sprung" class="feld"></select>
        </div>
      </div>
      <div class="kiste-suchzeile">
        <input type="search" class="feld kiste-suche" placeholder="Im Namen suchen" aria-label="Im Namen suchen"
          autocomplete="off" autocapitalize="off" spellcheck="false" enterkeyhint="search" maxlength="60">
        <button type="button" class="knopf kiste-filterknopf" aria-expanded="false" aria-controls="kiste-filter">${SYM.filter}<span>Filter</span><span class="kiste-filterzahl" hidden></span></button>
        <button type="button" class="knopf kiste-statknopf" aria-expanded="false" aria-controls="kiste-statistik">${SYM.balken}<span class="kiste-statknopf-text">Statistik</span></button>
      </div>
    </div>
    <div class="kiste-aktiv" aria-label="Aktive Filter"></div>
    <section class="kiste-filterfeld" id="kiste-filter" aria-label="Filter" hidden>
      <fieldset class="kiste-gruppe"><legend class="etikett">Farbe</legend>
        <div class="kiste-pillen">${o.farben.map((x) => pille('farbe', x.wert, x.text, false)).join('')}</div></fieldset>
      <fieldset class="kiste-gruppe"><legend class="etikett">Manawert</legend>
        <div class="kiste-mw">
          <label>von <select class="feld" data-mw="mw_von" aria-label="Manawert von">${mwOptionen(w.manawert_max, 'egal')}</select></label>
          <label>bis <select class="feld" data-mw="mw_bis" aria-label="Manawert bis">${mwOptionen(w.manawert_max, 'egal')}</select></label>
        </div></fieldset>
      <fieldset class="kiste-gruppe"><legend class="etikett">Seltenheit</legend>
        <div class="kiste-pillen">${o.seltenheiten.map((x) => pille('seltenheit', x.wert, x.text, false)).join('')}</div></fieldset>
      <fieldset class="kiste-gruppe"><legend class="etikett">Typ</legend>
        <div class="kiste-pillen">${o.typen.map((x) => pille('typ', x.wert, x.text, false)).join('')}</div></fieldset>
      ${sprachen ? `<fieldset class="kiste-gruppe"><legend class="etikett">Sprache</legend><div class="kiste-pillen">${sprachen}</div></fieldset>` : ''}
      ${w.foils ? `<fieldset class="kiste-gruppe"><legend class="etikett">Foil</legend><div class="kiste-pillen">
        ${pille('foil', '1', 'Nur Foil', false)}${pille('foil', '0', 'Kein Foil', false)}</div></fieldset>` : ''}
      ${w.sets.length ? `<fieldset class="kiste-gruppe"><legend class="etikett">Set</legend>
        <select class="feld kiste-setwahl" aria-label="Set hinzufügen">${sets}</select></fieldset>` : ''}
      <fieldset class="kiste-gruppe"><legend class="etikett">Anzahl mindestens</legend>
        <input type="number" class="feld kiste-anzahl" min="1" max="999" inputmode="numeric" placeholder="egal"
          aria-label="Anzahl mindestens"></fieldset>
      ${s.sammlung ? `<fieldset class="kiste-gruppe kiste-gruppe-breit"><legend class="etikett">Ort</legend>
        <div class="kiste-pillen">${ortPillen}</div></fieldset>
      <fieldset class="kiste-gruppe"><legend class="etikett">Quelle</legend>
        <div class="kiste-pillen">${quellenPillen}</div></fieldset>
      <fieldset class="kiste-gruppe"><legend class="etikett">Doppel</legend>
        <div class="kiste-pillen">${pille('doppel', '1', 'Nur mögliche Doppel', false)}</div></fieldset>` : ''}
      <div class="kiste-filterfuss"><button type="button" class="knopf kiste-alle-loeschen">Alle Filter löschen</button></div>
    </section>
    <section class="kiste-statistik" id="kiste-statistik" aria-label="Statistik dieser Kiste" hidden></section>
    <div class="kiste-huelle" aria-live="off">
      <div class="fachkasten-platz" hidden></div>
      <div class="kiste-listenkopf" aria-hidden="true"><span>Anz.</span><span>Name</span>${s.sammlung ? '<span>Ort</span>' : ''}<span>Set</span><span class="lk-mw">MW</span>
        <span class="lk-typ">Typ</span><span class="lk-selt">Seltenheit</span><span>Spr.</span><span>Foil</span><span class="lk-preis">Ø 7 T.</span></div>
      <div class="kiste-oben" hidden></div>
      <ul class="kiste-liste" aria-label="Karten der Kiste"></ul>
      <div class="kiste-hub" aria-hidden="true"><div class="kiste-hub-bild"></div><div class="kiste-hub-kante"></div></div>
      <div class="kiste-unten" aria-live="polite"></div>
    </div>
    <nav class="kiste-sprung" aria-label="Sprungleiste" hidden></nav>
    <div class="kiste-wahlleiste" role="region" aria-label="Auswahl" hidden>
      <div class="kiste-wahl-kopf">
        <span class="kiste-wahl-zahl" role="status" aria-live="polite"></span>
        <button type="button" class="knopf kiste-wahl-fertig">Fertig</button>
      </div>
      <div class="kiste-wahl-knoepfe">
        <button type="button" class="knopf" data-wahl="seite">Alle auf dieser Seite</button>
        <button type="button" class="knopf" data-wahl="gefiltert">Alle gefilterten</button>
        <button type="button" class="knopf" data-wahl="keine">Keine</button>
      </div>
      <button type="button" class="knopf knopf-haupt kiste-verschieben" disabled>${SYM.weiter}<span>Verschieben nach …</span></button>
      <button type="button" class="knopf kiste-zurueckstellen" hidden disabled>Für Binder zurückstellen</button>
    </div>
    <button type="button" class="knopf knopf-sym kiste-hoch" aria-label="Nach oben" hidden>${SYM.hoch}</button>`;
}

// ---------- Anzeige-Hilfen ----------

function titelSetzen() {
  S.wurzel.querySelector('.kiste-titel').textContent = S.kopf.name;
  S.wurzel.setAttribute('aria-label', S.sammlung ? 'Gesamtsammlung' : `Kiste ${S.kopf.name}`);
}

function zahlenZeichnen() {
  const k = S.kopf;
  let t = `${zahl(k.karten)} ${k.karten === 1 ? 'Karte' : 'Karten'} · ${zahl(k.posten)} Posten`;
  if (S.sammlung) {
    t += ` · davon in Bindern ${zahl(k.binder.karten)} · davon in Kisten ${zahl(k.kisten.karten)}`;
    if (k.decks && k.decks.karten > 0) t += ` · davon in Decks ${zahl(k.decks.karten)}`;
  }
  const gefiltert = aktiveFilter(S).length > 0 || S.fach !== null;
  if (gefiltert && S.gesamt) t += S.sammlung ? ` · angezeigt: ${zahl(S.gesamt.posten)} Posten` : ` · davon ${zahl(S.gesamt.posten)} angezeigt`;
  const z = S.wurzel.querySelector('.kiste-zahlen');
  z.textContent = t;
  if (k.wert?.wert != null) {   // Gesamtwert (Durchschnitt 7 Tage), Wunsch des Nutzers
    const w = Object.assign(document.createElement('span'), { className: 'wert' });
    w.textContent = ` · Wert ${euro(k.wert.wert)}`;
    w.title = 'Summe aus Anzahl × Durchschnittspreis der letzten 7 Tage (Cardmarket)'
      + (k.wert.ohne_preis ? `; ${zahl(k.wert.ohne_preis)} Karten ohne Preis` : '');
    z.append(w);
  }
  z.title = gefiltert && S.gesamt ? `${zahl(S.gesamt.posten)} Posten mit ${zahl(S.gesamt.karten)} Karten passen zu den Filtern` : '';
  S.wurzel.querySelector('.fachkopf-zahl').textContent = S.fach !== null && S.gesamt ? karten(S.gesamt.karten) : '';
}

/** Gesamtsammlung: Quellen getrennt, mögliche Doppel (Tipp filtert), Karten ohne Ort. */
function sammlungInfoZeichnen() {
  const s = S;
  const box = s.wurzel.querySelector('.kiste-sammlung-info');
  if (!s.sammlung) { box.hidden = true; return; }
  const k = s.kopf;
  box.hidden = false;
  const quellen = k.quellen.map((q) => `<span class="si-teil"><span class="si-wert">${zahl(q.karten)}</span> ${esc(q.text)}</span>`).join('');
  const doppel = k.doppel.posten > 0
    ? `<button type="button" class="knopf kiste-pille si-doppel" data-filter="doppel" data-wert="1" aria-pressed="false"`
      + ` title="Gleicher Druck, gleiche Sprache und gleiches Foil liegen in einer Kiste und in einem Binder: ${zahl(k.doppel.in_kisten)} Posten in Kisten, ${zahl(k.doppel.in_bindern)} in Bindern">`
      + `mögliche Doppel: ${zahl(k.doppel.posten)}</button>`
    : '<span class="si-teil">keine möglichen Doppel</span>';
  const ohne = k.ohne_ort.karten > 0
    ? `<span class="si-teil si-warn" title="Karten, die weder in einer Kiste noch in einem Binder oder Deck liegen"><span class="si-wert">${zahl(k.ohne_ort.karten)}</span> Karten ohne Ort (nicht mitgezählt)</span>` : '';
  box.innerHTML = `<span class="etikett si-titel">Quellen</span>${quellen}<span class="si-trenn" aria-hidden="true"></span>${doppel}${ohne}`;
}

function meldung(text) {
  S.wurzel.querySelector('.kiste-meldung').textContent = text || '';
}

function obenSync() {
  const s = S;
  s.wurzel.querySelectorAll('.kiste-segment[data-ansicht]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.ansicht === s.ansicht)));
  s.wurzel.querySelector('#kiste-sort').value = s.sort;
  s.wurzel.querySelector('#kiste-gruppe').value = s.gruppe;
  const r = s.wurzel.querySelector('.kiste-richtung');
  r.dataset.richtung = s.richtung;
  r.setAttribute('aria-label', s.richtung === 'auf' ? 'Aufsteigend – tippen für absteigend' : 'Absteigend – tippen für aufsteigend');
  r.title = s.richtung === 'auf' ? 'Aufsteigend' : 'Absteigend';
}

function filterSync() {
  const s = S;
  const f = s.filter;
  s.wurzel.querySelectorAll('.kiste-pille').forEach((b) => {
    const name = b.dataset.filter;
    const an = name === 'foil' || name === 'doppel' ? String(f[name]) === b.dataset.wert : f[name].includes(b.dataset.wert);
    b.setAttribute('aria-pressed', String(an));
  });
  s.wurzel.querySelectorAll('[data-mw]').forEach((sel) => { sel.value = f[sel.dataset.mw] === null ? '' : String(f[sel.dataset.mw]); });
  const anzahl = s.wurzel.querySelector('.kiste-anzahl');
  if (document.activeElement !== anzahl) anzahl.value = f.anzahl_min === null ? '' : String(f.anzahl_min);
  const suche = s.wurzel.querySelector('.kiste-suche');
  if (document.activeElement !== suche) suche.value = f.q;
  const setwahl = s.wurzel.querySelector('.kiste-setwahl');
  if (setwahl) {
    setwahl.value = '';
    for (const opt of setwahl.options) opt.disabled = Boolean(opt.value) && f.set.includes(opt.value);
  }
  // aktive Filter oben
  const aktiv = aktiveFilter(s);
  const box = s.wurzel.querySelector('.kiste-aktiv');
  box.hidden = aktiv.length === 0;
  box.innerHTML = aktiv.map((a) => `<button type="button" class="knopf kiste-chip" data-loeschen="${a.name}"`
    + ` data-wert="${esc(a.wert)}" aria-label="Filter entfernen: ${esc(a.text)}">${esc(a.text)}${SYM.kreuz}</button>`).join('')
    + (aktiv.length > 1 ? '<button type="button" class="knopf kiste-chip kiste-chip-alle" data-loeschen="alle">Alle Filter löschen</button>' : '');
  const zahlEl = s.wurzel.querySelector('.kiste-filterzahl');
  zahlEl.hidden = aktiv.length === 0;
  zahlEl.textContent = String(aktiv.length);
  s.wurzel.querySelector('.kiste-alle-loeschen').disabled = aktiv.length === 0;
}

function speichern() {
  const s = S;
  vorliebe(`kiste.${s.schluessel}.ansicht`, s.ansicht);
  vorliebe(`kiste.${s.schluessel}.sort`, s.sort);
  vorliebe(`kiste.${s.schluessel}.richtung`, s.richtung);
  vorliebe(`kiste.${s.schluessel}.gruppe`, s.gruppe);
  vorliebe(`kiste.${s.schluessel}.filter`, JSON.stringify(s.filter));
}

// ---------- Laden ----------

const kartenPfad = (s) => (s.sammlung ? '/api/sammlung/karten' : `/api/kiste/${s.id}/karten`);

/** Filter samt herausgezogenem Fach: für Karten, Statistik und „Alle gefilterten“. */
function kartenFilter(s) {
  const p = filterParameter(s.filter, s.sammlung);
  if (s.fach !== null) p.set('fach', s.fach);
  return p;
}

function abfrage(s, ab, anzahl) {
  const p = kartenFilter(s);
  p.set('sort', s.sort);
  p.set('richtung', s.richtung);
  if (s.gruppe) p.set('gruppe', s.gruppe);
  p.set('ab', ab);
  p.set('anzahl', anzahl);
  return `${kartenPfad(s)}?${p}`;
}

async function seiteHolen(s, ab, anzahl) {
  const steuer = new AbortController();
  s.anfragen.add(steuer);
  try {
    return await anfordern(abfrage(s, ab, anzahl), steuer.signal);
  } finally {
    s.anfragen.delete(steuer);
  }
}

function alleAbbrechen(s) {
  for (const a of s.anfragen) a.abort();
  s.anfragen.clear();
}

function gruppenSetzen(gruppen) {
  const s = S;
  s.gruppen = gruppen && gruppen.length ? gruppen : null;
  s.gruppeBei = new Map((gruppen ?? []).map((g) => [g.ab, g]));
  const schluessel = s.gruppen ? s.gruppen.map((g) => `${g.schluessel}:${g.ab}`).join('|') : '';
  if (schluessel === s.gruppenSchluessel) return;
  s.gruppenSchluessel = schluessel;
  sprungZeichnen();
}

const ende = (s) => s.start + s.karten.length;

function untenZeichnen() {
  const s = S;
  const box = s.wurzel.querySelector('.kiste-unten');
  box.classList.remove('kiste-fehler');
  if (s.neuFehler) {
    box.classList.add('kiste-fehler');
    box.innerHTML = `<p class="meldung" role="alert">${esc(s.neuFehler)}</p>`
      + '<button type="button" class="knopf kiste-nochmal">Noch einmal versuchen</button>';
  } else if (s.neuLaedt && !s.karten.length) {
    box.innerHTML = '<span class="kiste-status">Karten werden geladen …</span>';
  } else if (s.fehlerNach.unten || s.fehlerNach.oben) {
    box.classList.add('kiste-fehler');
    box.innerHTML = `<p class="meldung" role="alert">${esc(s.fehlerNach.unten || s.fehlerNach.oben)}</p>`
      + '<button type="button" class="knopf kiste-nochmal">Noch einmal versuchen</button>';
  } else if (s.gesamt && s.gesamt.posten === 0) {
    const gefiltert = aktiveFilter(s).length > 0;
    const leer = s.fach !== null ? 'In diesem Fach liegt keine Karte.' : 'Diese Kiste ist noch leer.';
    box.innerHTML = `<p class="kiste-leer">${gefiltert ? 'Keine Karte passt zu diesen Filtern.' : leer}</p>`
      + (gefiltert ? '<button type="button" class="knopf kiste-chip-alle" data-loeschen="alle">Alle Filter löschen</button>' : '');
  } else if (s.laed.unten || s.laed.oben) {
    box.innerHTML = '<span class="kiste-status">Weitere Karten werden geladen …</span>';
  } else if (s.gesamt && ende(s) >= s.gesamt.posten && s.karten.length) {
    box.innerHTML = `<span class="kiste-status kiste-ende">${s.fach !== null ? 'Ende des Fachs' : 'Ende der Kiste'}</span>`;
  } else {
    box.innerHTML = '';
  }
  const oben = s.wurzel.querySelector('.kiste-oben');
  oben.hidden = s.start <= 0 || !s.karten.length;
}

function beobachten() {
  const s = S;
  if (!s || !s.io) return;
  for (const knoten of [s.wurzel.querySelector('.kiste-oben'), s.wurzel.querySelector('.kiste-unten')]) {
    s.io.unobserve(knoten);
    if (!knoten.hidden) s.io.observe(knoten);
  }
}

function allesZeichnen() {
  const s = S;
  const liste = s.wurzel.querySelector('.kiste-liste');
  const huelle = s.wurzel.querySelector('.kiste-huelle');
  huelle.dataset.ansicht = s.ansicht;
  liste.className = `kiste-liste kiste-liste-${s.ansicht}${s.alleGefiltert ? ' kiste-alle' : ''}`;
  liste.innerHTML = teilHtml(s.karten, s.start);
  senken();
  s.wurzel.classList.toggle('kiste-mit-sprung', Boolean(s.gruppen && s.gruppen.length > 1));
  untenZeichnen();
}

/** Lädt die erste Seite ab `ab` neu (Sortierung, Filter, Gruppierung, Sprung); frühere Anfragen verfallen. */
async function neuLaden({ ab = 0, ziel = null, anker = null } = {}) {
  const s = S;
  if (!s) return;
  if (kistenBlick(s)) { await fachkastenLaden(); return; }
  const gen = ++s.gen;
  alleAbbrechen(s);
  s.laed = { oben: false, unten: false };
  s.fehlerNach = { oben: null, unten: null };
  s.neuFehler = null;
  s.neuLaedt = true;
  const huelle = s.wurzel.querySelector('.kiste-huelle');
  huelle.classList.add('kiste-laedt');
  huelle.setAttribute('aria-busy', 'true');
  if (!s.karten.length) untenZeichnen();
  let antwort;
  try {
    antwort = await seiteHolen(s, ab, SEITE);
  } catch (f) {
    if (f?.name === 'AbortError' || S !== s || gen !== s.gen) return;
    s.neuLaedt = false;
    s.neuFehler = f.message;
    huelle.classList.remove('kiste-laedt');
    huelle.removeAttribute('aria-busy');
    untenZeichnen();
    return;
  }
  if (S !== s || gen !== s.gen) return;
  s.karten = antwort.karten;
  s.start = antwort.ab;
  s.gesamt = antwort.gesamt;
  s.neuLaedt = false;
  gruppenSetzen(antwort.gruppen);
  zahlenZeichnen();
  allesZeichnen();
  if (s.wahlModus) wahlAnzeigen();
  huelle.classList.remove('kiste-laedt');
  huelle.removeAttribute('aria-busy');
  if (ziel !== null) {
    zuIndex(ziel);
  } else if (anker !== null) {
    zuIndex(anker, true);
  } else {
    const oben = huelle.getBoundingClientRect().top;
    if (oben < 0) window.scrollTo(0, Math.max(0, oben + window.scrollY - 8));
  }
  beobachten();
  if (s.statistikOffen) statistikLaden();
}

/** Nächste (unten) oder vorige Seite (oben) nachladen, solange nichts dergleichen läuft. */
async function nachladen(richtung) {
  const s = S;
  if (!s || s.neuLaedt || s.laed[richtung] || s.fehlerNach[richtung] || !s.gesamt) return;
  let ab;
  let anzahl;
  if (richtung === 'unten') {
    if (ende(s) >= s.gesamt.posten) return;
    ab = ende(s);
    anzahl = SEITE;
  } else {
    if (s.start <= 0) return;
    ab = Math.max(0, s.start - SEITE);
    anzahl = s.start - ab;
  }
  const gen = s.gen;
  s.laed[richtung] = true;
  untenZeichnen();
  try {
    const a = await seiteHolen(s, ab, anzahl);
    if (S !== s || gen !== s.gen) return;
    const passt = a.ab === ab && (richtung === 'unten' ? ab === ende(s) : a.karten.length === anzahl);
    if (!passt || a.gesamt.posten !== s.gesamt.posten) {  // die Kiste hat sich zwischenzeitlich geändert
      neuLaden({ anker: richtung === 'unten' ? s.start : null });
      return;
    }
    s.gesamt = a.gesamt;
    if (a.gruppen) gruppenSetzen(a.gruppen);
    const liste = s.wurzel.querySelector('.kiste-liste');
    if (richtung === 'unten') {
      const html = teilHtml(a.karten, ab);
      for (const k of a.karten) s.karten.push(k);
      liste.insertAdjacentHTML('beforeend', html);
    } else {
      const html = teilHtml(a.karten, ab);
      const hoeheVorher = document.documentElement.scrollHeight;
      const y = window.scrollY;
      s.karten = a.karten.concat(s.karten);
      s.start = ab;
      liste.insertAdjacentHTML('afterbegin', html);
      window.scrollTo(0, y + (document.documentElement.scrollHeight - hoeheVorher));
      senken();
    }
  } catch (f) {
    if (f?.name === 'AbortError' || S !== s || gen !== s.gen) return;
    s.fehlerNach[richtung] = f.message;
  } finally {
    if (S === s && gen === s.gen) {
      s.laed[richtung] = false;
      untenZeichnen();
      beobachten();
    }
  }
}

// ---------- Fächer: die Kiste mit ihren Trennkarten ----------

/** Zeigt die Ansicht gerade die Kiste selbst (Trennkarten statt Karten)? */
const kistenBlick = (s) => s.hatFaecher && s.fachModus === 'faecher' && s.fach === null;

const fachPfad = (s, fach) => `#/kiste/${s.id}${fach === null ? '' : `/fach/${fach}`}`;

/** Fach zu einem Wert aus der Adresse ({ name, texte }), null wenn es dieses Fach nicht gibt. */
function fachLesen(s, wert) {
  if (wert === 'ohne') return { name: 'Ohne Fach', texte: [] };
  return s.kopf.faecher.filter((f) => f.gueltig)[Number(wert)] ?? null;
}

function fachReiheHtml(f, i, n) {
  const ohne = f.fach === 'ohne';
  const name = ohne ? 'Ohne Fach' : f.name;
  const bilder = f.bilder.map((b, j) => `<img class="fach-bild" style="--j:${j}" src="${bildUrl('scryfall', b, 200)}"`
    + ` srcset="${bildUrl('scryfall', b, 200)} 200w, ${bildUrl('scryfall', b, 400)} 400w" sizes="120px"`
    + ' width="63" height="88" alt="" loading="lazy" decoding="async">').join('');
  const regel = ohne ? 'Kein Fach passt' : (f.texte.join(' · ') || 'Alle übrigen Karten');
  const zahlen = f.posten === 0 ? 'leer'
    : karten(f.karten) + (f.wert != null ? ` · ${euro(f.wert)}` : '');
  const klassen = `fach${f.posten === 0 ? ' fach-leer' : ''}${ohne ? ' fach-ohne' : ''}`;
  return `<li class="${klassen}" style="--i:${i};--reiter:${i % 3};--rueck:${Math.min(22, (n - 1 - i) * 6)}px">`
    + `<button type="button" class="fach-knopf" data-fach="${esc(f.fach)}" aria-label="Fach ${esc(name)} herausziehen: ${esc(zahlen)}">`
    + `<span class="fach-blick" aria-hidden="true">${bilder}</span>`
    + `<span class="fach-trenner"><span class="fach-reiter">${esc(name)}</span>`
    + `<span class="fach-zeile"><span class="fach-regel">${esc(regel)}</span><span class="fach-zahl">${esc(zahlen)}</span></span>`
    + '</span></button></li>';
}

function fachkastenZeichnen() {
  const s = S;
  const u = s.fachkasten;
  const liste = u.ohne.posten > 0 ? [...u.faecher, u.ohne] : u.faecher;
  const aussehen = kistenAussehen(s.kopf.etikettfarbe);
  s.wurzel.querySelector('.fachkasten-platz').innerHTML = `<div class="fachkasten ${aussehen.klasse}" style="${aussehen.stil}">`
    + `<ol class="fachkasten-reihen" aria-label="Fächer der Kiste">${liste.map((f, i) => fachReiheHtml(f, i, liste.length)).join('')}</ol>`
    + `<div class="fachkasten-front" aria-hidden="true"><span class="regal-kiste-etikett"><span class="regal-kiste-name">${esc(s.kopf.name)}</span></span></div>`
    + '</div>';
}

async function fachkastenLaden() {
  const s = S;
  const gen = ++s.gen;
  alleAbbrechen(s);
  // Eine abgebrochene Kartenanfrage räumt nicht selbst auf: Sonst bliebe die Kiste gedimmt und ließe sich nicht antippen
  s.neuLaedt = false;
  const huelle = s.wurzel.querySelector('.kiste-huelle');
  huelle.classList.remove('kiste-laedt');
  huelle.removeAttribute('aria-busy');
  const steuer = new AbortController();
  s.anfragen.add(steuer);
  if (s.fachkasten) fachkastenZeichnen();   // sofort der letzte Stand, die Zahlen kommen gleich nach
  try {
    const u = await anfordern(`/api/kiste/${s.id}/fachkasten`, steuer.signal);
    if (S !== s || gen !== s.gen) return;
    s.fachkasten = u;
    fachkastenZeichnen();
  } catch (f) {
    if (f?.name === 'AbortError' || S !== s || gen !== s.gen) return;
    s.wurzel.querySelector('.fachkasten-platz').innerHTML = `<p class="meldung" role="alert">${esc(f.message)}</p>`
      + '<button type="button" class="knopf kiste-nochmal">Noch einmal versuchen</button>';
  } finally {
    s.anfragen.delete(steuer);
  }
}

/** Stimmt Umschalter, Kopf des Fachs und den Weg zurück auf den Zustand ab. */
function fachAnsichtSync() {
  const s = S;
  const w = s.wurzel;
  const blick = kistenBlick(s);
  w.classList.toggle('kiste-fachblick', blick);
  w.querySelector('.fachkasten-platz').hidden = !blick;
  w.querySelector('.kiste-fachwechsel').hidden = !s.hatFaecher;
  w.querySelectorAll('[data-fachmodus]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.fachmodus === s.fachModus)));
  const kopf = w.querySelector('.kiste-fachkopf');
  const fach = s.fach === null ? null : fachLesen(s, s.fach);
  kopf.hidden = !fach;
  if (fach) {
    kopf.className = `kiste-fachkopf ${kistenAussehen(s.kopf.etikettfarbe).klasse}`;
    kopf.querySelector('.fachkopf-reiter').textContent = fach.name;
    kopf.querySelector('.fachkopf-regel').textContent = s.fach === 'ohne' ? 'Kein Fach passt' : (fach.texte.join(' · ') || 'Alle übrigen Karten');
  }
  const text = s.fach === null ? 'Zurück ins Regal' : 'Zurück in die Kiste';
  const zurueckKnopf = w.querySelector('.kiste-zurueck');
  zurueckKnopf.setAttribute('aria-label', text);
  zurueckKnopf.querySelector('.kiste-zurueck-text').textContent = text;
}

function kartenLeeren(s) {
  s.karten = [];
  s.start = 0;
  s.gesamt = null;
  gruppenSetzen(null);
  s.wurzel.querySelector('.kiste-liste').innerHTML = '';
}

/** Ein Fach herausziehen: Es hebt sich aus der Kiste, dann folgt die Adresse (damit „Zurück“ es wieder einsteckt). */
async function fachZiehen(knopf) {
  const s = S;
  if (s.fachZieht) return;
  s.fachZieht = true;
  if (!bewegungReduziert()) {
    knopf.closest('.fachkasten').classList.add('fachkasten-zieht');
    knopf.parentElement.classList.add('fach-gezogen');
    await warten(220);
  }
  s.fachZieht = false;
  if (S === s) gehe(fachPfad(s, knopf.dataset.fach));
}

/** Zeigt ein Fach (`neu`) oder wieder die Kiste (null); der Wegweiser ruft das über `aktualisieren`. */
function fachAnzeigen(neu) {
  const s = S;
  if (s.wahlModus) wahlUmschalten(false);
  if (s.foilModus) foilModusUmschalten(false);
  const zurueckY = s.fachY;
  if (neu !== null) { s.fachY = window.scrollY; s.fachModus = 'faecher'; }
  s.fach = neu;
  s.basis = fachPfad(s, neu);
  kartenLeeren(s);
  fachAnsichtSync();
  zahlenZeichnen();
  neuLaden();
  window.scrollTo(0, neu === null && kistenBlick(s) ? zurueckY : 0);
}

/** Umschalter „Fächer“ und „Alle Karten“. `merken: false`: nur für diesen Besuch (Auswählen und Foil brauchen Karten). */
function fachModusWechseln(neu, { merken = true } = {}) {
  const s = S;
  if (!s.hatFaecher) return;
  const gleich = neu === s.fachModus;
  s.fachModus = neu;
  if (merken) vorliebe(`kiste.${s.schluessel}.fachmodus`, neu);
  if (s.fach !== null) { zurueck(fachPfad(s, null)); return; }   // aktualisieren zeichnet dann neu
  if (gleich) return;
  kartenLeeren(s);
  fachAnsichtSync();
  zahlenZeichnen();
  neuLaden();
}

// ---------- Springen und Scrollen ----------

/** Scrollt zum Eintrag mit Index `i` (bei Trennkarten zur Trennkarte). `still`: nur wenn schon sichtbar nahe. */
function zuIndex(i, nurAnker = false) {
  const s = S;
  const liste = s.wurzel.querySelector('.kiste-liste');
  const ziel = liste.querySelector(`[data-i="${i}"]`);
  if (!ziel) return;
  const y = ziel.getBoundingClientRect().top + window.scrollY - (nurAnker ? 80 : 12);
  window.scrollTo(0, Math.max(0, y));
}

async function springe(ab) {
  const s = S;
  if (!s || !s.gesamt || ab >= s.gesamt.posten) return;
  if (!s.neuLaedt && ab >= s.start && ab < ende(s)) {
    zuIndex(ab);
    return;
  }
  await neuLaden({ ab, ziel: ab });
}

function sprungZeichnen() {
  const s = S;
  const nav = s.wurzel.querySelector('.kiste-sprung');
  const feld = s.wurzel.querySelector('.kiste-feld-sprung');
  const wahl = s.wurzel.querySelector('#kiste-sprung');
  const gruppen = s.gruppen;
  const sichtbar = Boolean(gruppen && gruppen.length > 1);
  nav.hidden = !sichtbar;
  feld.hidden = !sichtbar;
  s.wurzel.classList.toggle('kiste-mit-sprung', sichtbar);
  if (!sichtbar) { nav.innerHTML = ''; wahl.innerHTML = ''; return; }
  wahl.innerHTML = '<option value="">Gruppe wählen …</option>' + gruppen.map((g, n) => `<option value="${n}">`
    + `${esc(g.text)} · ${zahl(g.posten)}</option>`).join('');
  // Leiste: bei vielen Gruppen gleichmäßig ausgewählt (höchstens 26 Einträge)
  const grenze = 26;
  const gewaehlt = gruppen.length <= grenze ? gruppen.map((g, n) => n)
    : Array.from({ length: grenze }, (_, n) => Math.round((n * (gruppen.length - 1)) / (grenze - 1)));
  s.leiste = gewaehlt.map((n) => gruppen[n]);
  nav.innerHTML = s.leiste.map((g, n) => {
    const kurz = String(g.text).split(' · ')[0];
    // Orte heißen „Kiste X“ bzw. „Binder X“: auf der Leiste genügt der Anfang des Namens
    const etikett = s.gruppe === 'ort' ? kurz.replace(/^(Kiste|Binder) /, '').slice(0, 3)
      : kurz.length > 5 ? kurz.slice(0, 3) : kurz;
    return `<button type="button" class="kiste-sprung-knopf" data-n="${n}" tabindex="-1" aria-label="Springe zu ${esc(g.text)}"`
      + ` title="${esc(g.text)} · ${zahl(g.posten)}">${esc(etikett)}</button>`;
  }).join('');
}

// ---------- Karteikasten: gehobene Karte ----------

const HUB_BILD = 168;

function heben(a) {
  const s = S;
  if (!a || s.gehoben === a || s.ansicht !== 'kasten' || s.wahlModus) return;
  const hub = s.wurzel.querySelector('.kiste-hub');
  const li = a.parentElement;
  s.gehoben = a;
  s.wurzel.querySelector('.kiste-hub-kante').innerHTML = a.innerHTML;
  const bildFlaeche = s.wurzel.querySelector('.kiste-hub-bild');
  bildFlaeche.classList.remove('kiste-fehlt');
  const bild = a.dataset.b;
  if (bild) {
    bildFlaeche.innerHTML = `<img class="kiste-bild" src="${bildUrl('scryfall', bild, 400)}" width="63" height="88" alt=""`
      + ' decoding="async">';
  } else {
    bildFlaeche.innerHTML = '';
    bildFlaeche.classList.add('kiste-fehlt');
  }
  // Ist oben im Bild kein Platz für das Bild, ragt die Karte stattdessen nach unten (Bild unter der Kante).
  const runter = li.getBoundingClientRect().top < HUB_BILD + 8;
  hub.classList.toggle('kiste-hub-runter', runter);
  hub.style.transform = `translate3d(0, ${li.offsetTop - (runter ? 0 : HUB_BILD)}px, 0)`;
  hub.classList.add('kiste-hub-an');
  hub.dataset.hubKarte = a.dataset.karte;
}

function senken() {
  const s = S;
  s.gehoben = null;
  clearTimeout(s.hebeUhr);
  const hub = s.wurzel.querySelector('.kiste-hub');
  if (hub) hub.classList.remove('kiste-hub-an');
}

// ---------- Aktionen ----------

function filterAendern(aenderung) {
  const s = S;
  aenderung(s.filter);
  if (s.wahlModus) wahlLeeren();   // die Auswahl gehörte zum alten Filter
  filterSync();
  zahlenZeichnen();
  speichern();
  neuLaden();
}

function filterKlick(knopf) {
  const name = knopf.dataset.filter;
  const wert = knopf.dataset.wert;
  filterAendern((f) => {
    if (name === 'foil') {
      f.foil = f.foil === Number(wert) ? null : Number(wert);
    } else if (name === 'doppel') {
      f.doppel = f.doppel === 1 ? null : 1;
    } else {
      const i = f[name].indexOf(wert);
      if (i >= 0) f[name].splice(i, 1);
      else f[name].push(wert);
    }
  });
}

function filterLoeschen(name, wert) {
  filterAendern((f) => {
    if (name === 'alle') Object.assign(f, leererFilter());
    else if (name === 'manawert') { f.mw_von = null; f.mw_bis = null; }
    else if (name === 'foil') f.foil = null;
    else if (name === 'doppel') f.doppel = null;
    else if (name === 'anzahl_min') f.anzahl_min = null;
    else if (name === 'q') f.q = '';
    else f[name] = f[name].filter((w) => w !== wert);
  });
}

function ansichtWechseln(neu) {
  const s = S;
  if (neu === s.ansicht || !ANSICHTEN.includes(neu)) return;
  const anker = ankerIndex();
  s.ansicht = neu;
  speichern();
  obenSync();
  allesZeichnen();
  if (anker !== null) zuIndex(anker, true);
  beobachten();
}

/** Index des Eintrags, der gerade oben im Bild steht (um beim Umschalten der Ansicht dort zu bleiben). */
function ankerIndex() {
  const huelle = S.wurzel.querySelector('.kiste-huelle');
  const r = huelle.getBoundingClientRect();
  if (r.top > 100 || !S.karten.length) return null;
  const x = Math.min(innerWidth - 40, Math.max(10, r.left + r.width / 2));
  const e = document.elementFromPoint(x, 90)?.closest('[data-i]');
  return e ? Number(e.dataset.i) : null;
}

function sortierungWechseln(wert) {
  const s = S;
  const o = s.optionen.sortierungen.find((x) => x.wert === wert);
  if (!o) return;
  s.sort = wert;
  s.richtung = o.richtung;
  speichern();
  obenSync();
  neuLaden();
}

function richtungWechseln() {
  S.richtung = S.richtung === 'auf' ? 'ab' : 'auf';
  speichern();
  obenSync();
  neuLaden();
}

function gruppeWechseln(wert) {
  S.gruppe = wert;
  if (!wert) gruppenSetzen(null);
  speichern();
  neuLaden();
}

// ---------- Statistik (nur auf Wunsch, drei kleine Abfragen) ----------

const FARB_TINT = { W: '#d9d2bd', U: '#5b86c4', B: '#8a7a99', R: '#c4574a', G: '#5f9a6b', M: '#d6a440', C: '#a8a39b', L: '#9b7b57' };

async function statistikLaden() {
  const s = S;
  const box = s.wurzel.querySelector('.kiste-statistik');
  s.statAbbruch?.abort();
  const steuer = new AbortController();
  s.statAbbruch = steuer;
  box.setAttribute('aria-busy', 'true');
  const holen = (gruppe) => {
    const p = kartenFilter(s);
    p.set('gruppe', gruppe);
    p.set('anzahl', '1');
    p.set('sort', 'name');
    return anfordern(`${kartenPfad(s)}?${p}`, steuer.signal);
  };
  try {
    const [mana, farbe, selt] = await Promise.all([holen('manawert'), holen('farbe'), holen('seltenheit')]);
    if (S !== s || steuer.signal.aborted) return;
    box.removeAttribute('aria-busy');
    box.innerHTML = '<div class="stat-block stat-mana"><h2 class="etikett">Manakurve</h2>'
      + saeulen(mana.gruppen) + '</div>'
      + '<div class="stat-block"><h2 class="etikett">Farben</h2>' + balken(farbe.gruppen, true) + '</div>'
      + '<div class="stat-block"><h2 class="etikett">Seltenheit</h2>' + balken(selt.gruppen, false) + '</div>';
  } catch (f) {
    if (f?.name === 'AbortError' || S !== s) return;
    box.removeAttribute('aria-busy');
    box.innerHTML = `<p class="meldung" role="alert">${esc(f.message)}</p>`;
  }
}

function saeulen(gruppen) {
  if (!gruppen || !gruppen.length) return '<p class="kiste-leer">Keine Daten.</p>';
  const max = Math.max(...gruppen.map((g) => g.karten), 1);
  return '<div class="stat-saeulen" role="list">' + gruppen.map((g) => `<div class="stat-saeule" role="listitem"`
    + ` aria-label="Manawert ${esc(g.text)}: ${zahl(g.karten)} Karten" title="${zahl(g.karten)} Karten">`
    + `<span class="stat-spur"><span class="stat-stapel" style="--h:${(g.karten / max).toFixed(3)}"><span class="stat-wert">${zahl(g.karten)}</span><i></i></span></span>`
    + `<span class="stat-name">${esc(g.text === 'Länder' ? 'Land' : g.text)}</span></div>`).join('') + '</div>';
}

function balken(gruppen, farbig) {
  if (!gruppen || !gruppen.length) return '<p class="kiste-leer">Keine Daten.</p>';
  const max = Math.max(...gruppen.map((g) => g.karten), 1);
  const summe = gruppen.reduce((a, g) => a + g.karten, 0) || 1;
  return '<div class="stat-balken" role="list">' + gruppen.map((g) => `<div class="stat-balken-zeile" role="listitem"`
    + ` aria-label="${esc(g.text)}: ${zahl(g.karten)} Karten">`
    + `<span class="stat-name">${esc(g.text)}</span>`
    + `<span class="stat-spur"><i style="--w:${(g.karten / max).toFixed(3)};${farbig && FARB_TINT[g.schluessel] ? `--tint:${FARB_TINT[g.schluessel]}` : ''}"></i></span>`
    + `<span class="stat-wert">${zahl(g.karten)} · ${Math.round((g.karten / summe) * 100)} %</span></div>`).join('') + '</div>';
}

// ---------- Umbenennen und Löschen ----------

/** Schreibende Knöpfe, nur angemeldet. In der Kiste: Auswählen, Liste importieren, Karte hinzufügen, Umbenennen,
    Löschen (wenn leer). In der Sammlung: Auswählen und Liste abziehen. */
function adminZeichnen() {
  const s = S;
  const box = s.wurzel.querySelector('.kiste-admin');
  box.replaceChildren();
  box.hidden = !angemeldet();
  if (!angemeldet()) {
    if (s.wahlModus) wahlUmschalten(false);
    if (s.foilModus) foilModusUmschalten(false);
    return;
  }
  const neu = (klasse, symbol, text, label, fn) => {
    const b = el('button', `knopf ${klasse}`, `${symbol}<span class="kiste-aktion-text">${text}</span>`);
    b.type = 'button';
    if (label) b.setAttribute('aria-label', label);
    b.addEventListener('click', fn);
    box.append(b);
    return b;
  };
  const wahl = neu('kiste-wahlknopf', SYM.wahl, 'Auswählen', 'Karten auswählen und verschieben', () => wahlUmschalten());
  wahl.setAttribute('aria-pressed', String(s.wahlModus));
  const foil = neu('kiste-foilknopf', SYM.stern, 'Foil', 'Foil-Modus: Karten antippen, um Foil umzuschalten',
    () => foilModusUmschalten());
  foil.setAttribute('aria-pressed', String(Boolean(s.foilModus)));
  if (s.sammlung) {
    neu('kiste-abzugknopf', SYM.abzug, 'Liste abziehen', 'Liste von der Sammlung abziehen', abziehenOeffnen);
    return;
  }
  if (s.kopf.scan_eingang) {
    neu('kiste-einsortierknopf', SYM.weiter, 'Jetzt einsortieren', 'Karten des Scan-Eingangs nach den Sortierregeln einsortieren',
      jetztEinsortieren);
  }
  neu('kiste-importknopf', SYM.hochladen, 'Liste importieren', 'Kartenliste (CSV) in diese Kiste importieren', listeImportieren);
  const plus = neu('kiste-plusknopf', SYM.plus, 'Karte hinzufügen', 'Karte zu dieser Kiste hinzufügen', karteHinzufuegen);
  const hinweis = el('span', 'kiste-katalog-hinweis');
  hinweis.id = 'kiste-katalog-hinweis';
  hinweis.hidden = true;
  box.append(hinweis);
  katalogDa().then((da) => {
    if (S !== s || da !== false) return;
    plus.disabled = true;
    plus.setAttribute('aria-describedby', hinweis.id);
    hinweis.textContent = 'Nur am PC mit Katalog';
    hinweis.hidden = false;
  });
  neu('kiste-faecherknopf', SYM.faecher, 'Fächer', 'Fächer dieser Kiste bearbeiten', faecherOeffnen);
  neu('kiste-umbenennen', SYM.stift, 'Bearbeiten', 'Name und Farbe der Kiste ändern', umbenennen);
  neu('kiste-kopierknopf', SYM.kopie, 'Kopieren', 'Neue Kiste mit Farbe und Fächern dieser Kiste anlegen', kopieren);
  neu('kiste-loeschen', SYM.muell, 'Löschen', s.kopf.posten === 0 ? 'Leere Kiste löschen' : 'Kiste mit allen Karten löschen',
    loeschen);
}

let katalogStand = null;   // Promise: true, false (kein Katalog) oder null (unbekannt)
let katalogJa = false;     // schon bekannt: Es gibt einen Katalog (dann liefert /api/pruefen/bild/… lokale Bilder)

/** Gibt es am Server einen Katalog? `namen` liefert ohne ihn { verfuegbar: false }. */
function katalogDa() {
  if (!katalogStand) {
    katalogStand = senden('/api/pruefen/namen?q=xx', { methode: 'GET' })
      .then((a) => { katalogJa = a.verfuegbar !== false; return katalogJa; })
      .catch(() => { katalogStand = null; return null; });
  }
  return katalogStand;
}

/** Fächer der Kiste bearbeiten (Dialog in regeln.js, erst bei Bedarf geladen); danach die Kiste neu aufbauen. */
async function faecherOeffnen() {
  const s = S;
  let geaendert = false;
  try {
    const { faecherBearbeiten } = await import('./regeln.js');
    geaendert = await faecherBearbeiten(s.id, s.kopf.name);
  } catch (f) {
    if (S === s) meldung(f.message);
    return;
  }
  if (geaendert && S === s) {
    meldung('');
    await neuAufbauen();
    hinweisZeigen('Fächer gespeichert.');
  }
}

/** Name und Farbe der Kiste ändern (Farbe: Metalletikett, feste Farbe oder Farbrad). */
async function umbenennen() {
  const s = S;
  const form = el('form');
  form.id = 'kiste-umbenennen-form';
  form.noValidate = true;
  form.innerHTML = '<label class="etikett feld-etikett" for="kiste-name-feld">Name</label>'
    + `<input id="kiste-name-feld" class="feld" type="text" maxlength="60" value="${esc(s.kopf.name)}" autocomplete="off"`
    + ' enterkeyhint="done" spellcheck="false"><p class="meldung" role="alert" aria-live="assertive"></p>';
  const alteFarbe = s.kopf.etikettfarbe || 'messing';
  const wahl = farbwahl({ farben: KISTENFARBEN, wert: alteFarbe });
  form.querySelector('.meldung').before(wahl);
  const d = dialog({ titel: 'Kiste bearbeiten', klasse: 'dialog-klein', inhalt: form });
  const feld = form.querySelector('input');
  const text = form.querySelector('.meldung');
  const abbrechen = el('button', 'knopf', 'Abbrechen');
  abbrechen.type = 'button';
  const speichernKnopf = el('button', 'knopf knopf-haupt', 'Speichern');
  speichernKnopf.type = 'submit';
  speichernKnopf.setAttribute('form', form.id);
  abbrechen.addEventListener('click', () => d.schliessen(false));
  d.fuss.append(abbrechen, speichernKnopf);
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = feld.value.trim();
    if (!name) { text.textContent = 'Bitte einen Namen für die Kiste eingeben.'; feld.focus(); return; }
    const daten = {};
    if (name !== s.kopf.name) daten.name = name;
    if (wahl.wert !== alteFarbe) daten.etikettfarbe = wahl.wert;
    if (!Object.keys(daten).length) { d.schliessen(false); return; }
    speichernKnopf.disabled = true;
    text.textContent = '';
    try {
      await senden(`/api/kiste/${s.id}`, { methode: 'PATCH', daten });
      if (S === s) {
        s.kopf.name = name;
        if (daten.etikettfarbe) s.kopf.etikettfarbe = daten.etikettfarbe;
        titelSetzen();
      }
      d.schliessen(true);
    } catch (f) {
      text.textContent = f.message;
      feld.focus();
    } finally {
      speichernKnopf.disabled = false;
    }
  });
  feld.focus();
  feld.select();
  await d.fertig;
}

/** Neue, leere Kiste mit Farbe und Fächern dieser Kiste. Karten gibt es nur einmal, darum bleiben sie hier liegen. */
async function kopieren() {
  const s = S;
  const faecher = s.kopf.faecher?.length ?? 0;
  const form = el('form');
  form.id = 'kiste-kopieren-form';
  form.noValidate = true;
  form.innerHTML = '<label class="etikett feld-etikett" for="kiste-kopie-name">Name der neuen Kiste</label>'
    + `<input id="kiste-kopie-name" class="feld" type="text" maxlength="60" value="${esc(`${s.kopf.name.slice(0, 52)} (Kopie)`)}"`
    + ' autocomplete="off" enterkeyhint="done" spellcheck="false">'
    + '<p class="dialog-text">'
    + (faecher ? `Übernommen werden die Farbe und ${faecher === 1 ? '1 Fach' : `${esc(zahl(faecher))} Fächer`}.`
      : 'Übernommen wird die Farbe.')
    + ' Die neue Kiste ist leer, die Karten bleiben hier.</p>'
    + '<p class="meldung" role="alert" aria-live="assertive"></p>';
  const d = dialog({ titel: 'Kiste kopieren', klasse: 'dialog-klein', inhalt: form });
  const feld = form.querySelector('input');
  const text = form.querySelector('.meldung');
  const abbrechen = el('button', 'knopf', 'Abbrechen');
  abbrechen.type = 'button';
  abbrechen.addEventListener('click', () => d.schliessen(null));
  const los = el('button', 'knopf knopf-haupt', 'Kopieren');
  los.type = 'submit';
  los.setAttribute('form', form.id);
  d.fuss.append(abbrechen, los);
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = feld.value.trim();
    if (!name) { text.textContent = 'Bitte einen Namen für die Kiste eingeben.'; feld.focus(); return; }
    los.disabled = true;
    text.textContent = '';
    try {
      const antwort = await senden('/api/kisten', { daten: { name, vorlage: s.id } });
      d.schliessen(antwort.id);
    } catch (f) {
      text.textContent = f.message;   // zum Beispiel: Eine Kiste „X“ gibt es schon.
      feld.focus();
    } finally {
      los.disabled = false;
    }
  });
  feld.focus();
  feld.select();
  const neueId = await d.fertig;
  if (neueId && S === s) gehe(`#/kiste/${neueId}`);
}

async function loeschen() {
  const s = S;
  if (s.kopf.posten === 0) {
    const ja = await bestaetigen({ titel: 'Kiste löschen', text: `Die leere Kiste „${s.kopf.name}“ wird gelöscht. Das lässt sich nicht rückgängig machen.`,
      ja: 'Löschen', gefahr: true });
    if (!ja || S !== s) return;
    try {
      await senden(`/api/kiste/${s.id}`, { methode: 'DELETE' });
      gehe('#/', { ersetzen: true });
    } catch (f) {
      if (S === s) meldung(f.message);
    }
    return;
  }
  // Volle Kiste: erst den Namen eintippen, dann noch einmal bestätigen
  const name = s.kopf.name;
  const form = el('form');
  form.id = 'kiste-loeschen-form';
  form.noValidate = true;
  form.innerHTML = `<p class="dialog-text">„${esc(name)}“ enthält ${esc(zahl(s.kopf.karten))} Karten in`
    + ` ${esc(zahl(s.kopf.posten))} Posten. Sie werden mit der Kiste gelöscht. Das lässt sich nicht rückgängig machen.</p>`
    + '<label class="etikett feld-etikett" for="kiste-loeschname">Zum Bestätigen den Namen der Kiste eintippen</label>'
    + '<input id="kiste-loeschname" class="feld" type="text" maxlength="60" autocomplete="off" spellcheck="false">'
    + '<p class="meldung" role="alert" aria-live="assertive"></p>';
  const d = dialog({ titel: 'Kiste mit Karten löschen', klasse: 'dialog-klein', inhalt: form });
  const feld = form.querySelector('input');
  const text = form.querySelector('.meldung');
  const abbrechen = el('button', 'knopf', 'Abbrechen');
  abbrechen.type = 'button';
  abbrechen.addEventListener('click', () => d.schliessen(false));
  const weiter = el('button', 'knopf knopf-gefahr', 'Löschen');
  weiter.type = 'submit';
  weiter.disabled = true;
  weiter.setAttribute('form', form.id);
  d.fuss.append(abbrechen, weiter);
  feld.addEventListener('input', () => { weiter.disabled = feld.value.trim() !== name; });
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (feld.value.trim() !== name) return;
    const ja = await bestaetigen({ titel: 'Kiste wirklich löschen?',
      text: `„${name}“ und ${zahl(s.kopf.karten)} Karten werden endgültig gelöscht.`, ja: 'Endgültig löschen', gefahr: true });
    if (!ja || S !== s) return;
    weiter.disabled = true;
    try {
      await senden(`/api/kiste/${s.id}/loeschen`, { daten: { name } });
      d.schliessen(true);
      gehe('#/', { ersetzen: true });
    } catch (f) {
      text.textContent = f.message;
      weiter.disabled = false;
    }
  });
  feld.focus();
  await d.fertig;
}

// ---------- Meldung mit Rückgängig ----------

/** Meldung oben in der Ansicht. Mit `schritt` gibt es „Rückgängig“ (nimmt den ganzen Bedienschritt zurück). */
function hinweisZeigen(text, { schritt = null, art = 'ok' } = {}) {
  const s = S;
  if (!s) return;
  const box = s.wurzel.querySelector('.kiste-toast');
  box.hidden = false;
  box.className = `kiste-toast${art === 'fehler' ? ' kiste-toast-fehler' : ''}`;
  box.innerHTML = '<span class="toast-text"></span>'
    + (schritt ? '<button type="button" class="knopf toast-rueck">Rückgängig</button>' : '')
    + `<button type="button" class="knopf knopf-sym toast-zu" aria-label="Meldung schließen">${SYM.kreuz}</button>`;
  box.querySelector('.toast-text').textContent = text;
  box.querySelector('.toast-zu').addEventListener('click', () => { box.hidden = true; });
  box.querySelector('.toast-rueck')?.addEventListener('click', (e) => rueckgaengigMachen(schritt, e.currentTarget));
}

async function rueckgaengigMachen(schritt, knopf) {
  const s = S;
  knopf.disabled = true;
  try {
    await senden(`/api/schritt/${encodeURIComponent(schritt)}/rueckgaengig`);
  } catch (f) {
    if (S === s) { knopf.disabled = false; hinweisZeigen(f.message, { art: 'fehler' }); }
    return;
  }
  if (S !== s) return;
  await neuAufbauen({ wahlBehalten: true });
  hinweisZeigen('Rückgängig gemacht.');
}

/** Baut die Ansicht nach einer Änderung neu auf (Zahlen, Werte der Filter, erste Seite); die alte bleibt
    gedimmt stehen, bis die erste Seite da ist. */
async function neuAufbauen({ wahlBehalten = false } = {}) {
  const alt = S;
  if (!alt) return;
  const eltern = alt.wurzel.parentElement;
  const y = window.scrollY;
  const offen = { filter: !alt.wurzel.querySelector('.kiste-filterfeld').hidden, statistik: alt.statistikOffen };
  alt.wurzel.classList.add('kiste-umbau');
  alt.wurzel.inert = true;
  const neu = await aufbauen(eltern, alt.parameter, {
    still: true, wahlModus: wahlBehalten && alt.wahlModus, foilModus: wahlBehalten && alt.foilModus, offen,
    fachModus: alt.hatFaecher ? alt.fachModus : null });   // auch die Wahl, die nur für diesen Besuch gilt
  if (!neu) return;   // Fehler: aufbauen hat die Ansicht schon ersetzt
  await neu.erstes;
  if (S !== neu) return;
  neu.wurzel.hidden = false;
  alt.wurzel.remove();
  window.scrollTo(0, y);
}

// ---------- Auswahlmodus und Verschieben ----------

/** Anzahl der gewählten Posten und Karten (bei „alle gefilterten“ aus der Gesamtzahl des Filters). */
function wahlZahl() {
  const s = S;
  if (s.alleGefiltert) return { posten: s.gesamt?.posten ?? 0, karten: s.gesamt?.karten ?? 0 };
  let karten = 0;
  for (const a of s.gewaehlt.values()) karten += a;
  return { posten: s.gewaehlt.size, karten };
}

function wahlAnzeigen() {
  const s = S;
  const leiste = s.wurzel.querySelector('.kiste-wahlleiste');
  if (!leiste) return;
  const z = wahlZahl();
  leiste.querySelector('.kiste-wahl-zahl').textContent = z.posten
    ? `${zahl(z.karten)} ${z.karten === 1 ? 'Karte' : 'Karten'} gewählt (${zahl(z.posten)} Posten)`
    : 'Nichts gewählt – Karten antippen';
  leiste.querySelector('.kiste-verschieben').disabled = z.posten === 0;
  const zurueck = leiste.querySelector('.kiste-zurueckstellen');
  zurueck.hidden = !s.kopf?.scan_eingang;
  zurueck.disabled = z.posten === 0;
  const alle = leiste.querySelector('[data-wahl="gefiltert"]');
  alle.setAttribute('aria-pressed', String(s.alleGefiltert));
  alle.textContent = s.gesamt ? `Alle gefilterten (${zahl(s.gesamt.posten)})` : 'Alle gefilterten';
  leiste.querySelector('[data-wahl="keine"]').disabled = z.posten === 0;
}

function wahlUmschalten(an = !S.wahlModus) {
  const s = S;
  if (Boolean(an) === s.wahlModus) return;
  if (an && kistenBlick(s)) fachModusWechseln('alle', { merken: false });   // Auswählen braucht Karten
  if (an && s.foilModus) foilModusUmschalten(false);   // immer nur ein Modus
  s.wahlModus = Boolean(an);
  senken();
  s.wurzel.classList.toggle('kiste-wahl', s.wahlModus);
  s.wurzel.querySelector('.kiste-wahlleiste').hidden = !s.wahlModus;
  s.wurzel.querySelector('.kiste-wahlknopf')?.setAttribute('aria-pressed', String(s.wahlModus));
  if (s.wahlModus) wahlAnzeigen(); else wahlLeeren();
}

function wahlLeeren() {
  const s = S;
  s.gewaehlt.clear();
  s.alleGefiltert = false;
  s.wurzel.querySelector('.kiste-liste').classList.remove('kiste-alle');
  s.wurzel.querySelectorAll('.kiste-gewaehlt').forEach((e) => e.classList.remove('kiste-gewaehlt'));
  wahlAnzeigen();
}

/** Eine Karte an- oder abwählen (Tippen im Auswahlmodus). */
function wahlKlick(a) {
  const s = S;
  if (s.alleGefiltert) {  // erst „Keine“, dann einzeln wählen
    hinweisZeigen('Alle gefilterten Karten sind gewählt. Tippe auf „Keine“, um einzeln zu wählen.');
    return;
  }
  const id = Number(a.dataset.karte);
  if (s.gewaehlt.has(id)) {
    s.gewaehlt.delete(id);
    a.classList.remove('kiste-gewaehlt');
  } else {
    const k = s.karten[Number(a.dataset.i) - s.start];
    s.gewaehlt.set(id, k?.anzahl ?? 1);
    a.classList.add('kiste-gewaehlt');
  }
  wahlAnzeigen();
}

function wahlAlleSeite() {
  const s = S;
  if (s.alleGefiltert) wahlLeeren();
  for (const k of s.karten) s.gewaehlt.set(k.id, k.anzahl);
  s.wurzel.querySelectorAll('.kiste-liste a[data-karte]').forEach((a) => a.classList.add('kiste-gewaehlt'));
  wahlAnzeigen();
}

function wahlAlleGefiltert() {
  const s = S;
  if (s.alleGefiltert) { wahlLeeren(); return; }
  wahlLeeren();
  s.alleGefiltert = true;
  s.wurzel.querySelector('.kiste-liste').classList.add('kiste-alle');
  wahlAnzeigen();
}

/** Dialog: Ziel-Kiste wählen. Gibt die Kiste zurück oder undefined. */
function zielWaehlen(kisten, z) {
  const inhalt = el('div', 'kiste-ziele-wrap');
  const was = `${zahl(z.karten)} ${z.karten === 1 ? 'Karte' : 'Karten'} (${zahl(z.posten)} Posten)`;
  inhalt.innerHTML = kisten.length
    ? `<p class="dialog-text">${esc(was)} verschieben nach:</p><ul class="kiste-ziele" role="list">`
      + kisten.map((k) => `<li><button type="button" class="knopf kiste-ziel" data-id="${k.id}">`
        + `<span class="kz-name">${esc(k.name)}</span><span class="kz-zahl etikett">${zahl(k.karten)} Karten</span></button></li>`).join('')
      + '</ul>'
    : '<p class="dialog-text">Es gibt keine andere Kiste. Lege zuerst im Regal eine neue Kiste an.</p>';
  const d = dialog({ titel: 'Verschieben nach …', klasse: 'dialog-klein kiste-dialog', inhalt });
  inhalt.addEventListener('click', (e) => {
    const b = e.target.closest('.kiste-ziel');
    if (b) d.schliessen(kisten.find((k) => k.id === Number(b.dataset.id)));
  });
  const abbrechen = el('button', 'knopf', 'Abbrechen');
  abbrechen.type = 'button';
  abbrechen.addEventListener('click', () => d.schliessen(undefined));
  d.fuss.append(abbrechen);
  inhalt.querySelector('.kiste-ziel')?.focus();
  return d.fertig;
}

async function verschieben() {
  const s = S;
  const z = wahlZahl();
  if (!z.posten) return;
  let kisten;
  try {
    kisten = (await senden('/api/kisten', { methode: 'GET' })).kisten ?? [];
  } catch (f) {
    hinweisZeigen(f.message, { art: 'fehler' });
    return;
  }
  const ziel = await zielWaehlen(kisten.filter((k) => k.id !== s.id), z);
  if (!ziel || S !== s) return;
  const ja = await bestaetigen({
    titel: 'Karten verschieben',
    text: `${zahl(z.karten)} ${z.karten === 1 ? 'Karte' : 'Karten'} (${zahl(z.posten)} Posten) nach „${ziel.name}“ verschieben?`
      + ' Danach kannst du das noch rückgängig machen.',
    ja: 'Verschieben',
  });
  if (!ja || S !== s) return;
  const daten = s.alleGefiltert
    ? { ziel: ziel.id, auswahl: { kiste: s.sammlung ? null : s.id, abfrage: kartenFilter(s).toString() } }
    : { ziel: ziel.id, karten: [...s.gewaehlt.keys()] };
  const knopf = s.wurzel.querySelector('.kiste-verschieben');
  knopf.disabled = true;
  let a;
  try {
    a = await senden('/api/karten/verschieben', { daten });
  } catch (f) {
    if (S === s) { knopf.disabled = false; hinweisZeigen(f.message, { art: 'fehler' }); }
    return;
  }
  if (S !== s) return;
  await neuAufbauen({ wahlBehalten: true });
  if (!a.schritt) {
    hinweisZeigen(`Nichts zu verschieben: Alle gewählten Karten liegen schon in „${ziel.name}“.`);
    return;
  }
  const leer = a.taschen_leer ? `, ${zahl(a.taschen_leer)} ${a.taschen_leer === 1 ? 'Tasche ist' : 'Taschen sind'} jetzt leer` : '';
  const dort = a.schon_dort ? `, ${zahl(a.schon_dort)} lagen schon dort` : '';
  hinweisZeigen(`${zahl(a.karten)} ${a.karten === 1 ? 'Karte' : 'Karten'} (${zahl(a.verschoben)} Posten) nach „${ziel.name}“`
    + ` verschoben${leer}${dort}.`, { schritt: a.schritt });
}

// ---------- Jetzt einsortieren (Scan-Eingang) ----------

const karten = (n) => `${zahl(n)} ${n === 1 ? 'Karte' : 'Karten'}`;

/** Gewählte Karten für einen Binder beiseitelegen (Kiste „Zurückgestellt“). Im Binder liegen sie dann bereit. */
async function zurueckstellen() {
  const s = S;
  if (!wahlZahl().posten) return;
  const daten = s.alleGefiltert
    ? { auswahl: { kiste: s.id, abfrage: kartenFilter(s).toString() } }
    : { karten: [...s.gewaehlt.keys()] };
  const knopf = s.wurzel.querySelector('.kiste-zurueckstellen');
  knopf.disabled = true;
  let a;
  try {
    a = await senden('/api/karten/zurueckstellen', { daten });
  } catch (f) {
    if (S === s) { knopf.disabled = false; hinweisZeigen(f.message, { art: 'fehler' }); }
    return;
  }
  if (S !== s) return;
  await neuAufbauen({ wahlBehalten: true });
  const rest = a.uebersprungen ? ` ${zahl(a.uebersprungen)} unsichere bleiben hier, bitte erst prüfen.` : '';
  hinweisZeigen(`${karten(a.karten)} zurückgestellt. Im Binder unter „Bearbeiten“ eine leere Tasche antippen.${rest}`,
    { schritt: a.schritt });
}

/** Sortierregeln auf den ganzen Scan-Eingang anwenden: erst zeigen, was geschieht, dann als ein Schritt ausführen. */
async function jetztEinsortieren() {
  const s = S;
  let plan;
  try {
    plan = await senden('/api/regeln/einsortieren', { methode: 'GET' });
  } catch (f) {
    if (S === s) hinweisZeigen(f.message, { art: 'fehler' });
    return;
  }
  if (S !== s) return;
  const bleiben = [
    plan.keine ? `${zahl(plan.keine)} ohne passende Regel` : '',
    plan.wartet ? `${zahl(plan.wartet)} ohne Preis oder Kartendaten` : '',
    plan.pruefen ? `${zahl(plan.pruefen)} erst prüfen` : '',
  ].filter(Boolean).join(', ');
  if (!plan.karten) {
    hinweisZeigen(`Nichts einzusortieren${bleiben ? `: ${bleiben}` : ''}.`);
    return;
  }
  const ziele = plan.kisten.map((k) => `${k.kiste} ${zahl(k.karten)}`).join(', ');
  const ja = await bestaetigen({
    titel: 'Jetzt einsortieren',
    text: `${karten(plan.karten)} nach den Sortierregeln einsortieren: ${ziele}.`
      + `${bleiben ? ` Im Scan-Eingang bleiben: ${bleiben}.` : ''} Danach kannst du das noch rückgängig machen.`,
    ja: 'Einsortieren',
  });
  if (!ja || S !== s) return;
  let a;
  try {
    a = await senden('/api/regeln/einsortieren');
  } catch (f) {
    if (S === s) hinweisZeigen(f.message, { art: 'fehler' });
    return;
  }
  if (S !== s) return;
  await neuAufbauen();
  hinweisZeigen(a.schritt ? `${karten(a.karten)} einsortiert.` : 'Nichts einzusortieren.', { schritt: a.schritt });
}

// ---------- Liste importieren ----------

const WERT_REIHEN = [
  ['posten', 'Posten'], ['karten', 'Karten'], ['foil_posten', 'davon Foil (Posten)'], ['neu', 'Neu'],
  ['geaendert', 'Geändert'], ['unveraendert', 'Unverändert'],
  ['nutzer_geloescht', 'Von dir gelöscht (nicht neu angelegt)'], ['geschuetzt', 'Von dir geändert (geschützt)'],
];

const kartenText = (e) => `${e.name}${e.set ? ` (${String(e.set).toUpperCase()}${e.nummer ? ` ${e.nummer}` : ''}` : ' ('}`
  + `${e.sprache ? `, ${String(e.sprache).toUpperCase()}` : ''}${e.foil ? ', Foil' : ''})${e.anzahl ? `, ${e.anzahl}×` : ''}`;

function berichtListe(titel, liste, gesamt, zeile) {
  if (!gesamt) return `<p class="bericht-zeile">${esc(titel)}: <strong>0</strong></p>`;
  return `<details class="bericht-liste"><summary>${esc(titel)}: <strong>${zahl(gesamt)}</strong></summary><ul>`
    + liste.map((e) => `<li>${esc(zeile(e))}</li>`).join('')
    + (gesamt > liste.length ? `<li class="bericht-mehr">… und ${zahl(gesamt - liste.length)} weitere</li>` : '')
    + '</ul></details>';
}

/** Bericht des Listen-Imports (Probelauf oder fertig) als HTML in `box`. */
function berichtZeichnen(box, b, fertig) {
  const kd = b.kartendaten;
  box.innerHTML = `<p class="dialog-text bericht-kopf">${fertig
    ? `<strong>Import abgeschlossen.</strong> Eine Sicherung wurde vorher angelegt${b.sicherung ? ` (${esc(b.sicherung)})` : ''}.`
    : '<strong>Probelauf:</strong> Es wurde noch nichts geändert. Prüfe den Bericht und tippe dann auf „Importieren“.'}</p>`
    + `<p class="bericht-zeile">Datei: ${esc(b.datei)} · Ziel: Kiste „${esc(b.kiste?.name ?? '')}“</p>`
    + '<dl class="bericht-werte">'
    + WERT_REIHEN.map(([schluessel, text]) => `<div><dt>${esc(text)}</dt><dd>${zahl(b[schluessel])}</dd></div>`).join('')
    + '</dl>'
    + `<p class="bericht-zeile">Kartendaten: ${kd ? `${zahl(kd.posten_mit)} von ${zahl(kd.posten)} Posten mit Kartendaten`
      : 'ohne Katalog, es werden keine Kartendaten ergänzt'}</p>`
    + berichtListe('Zeilen ohne Scryfall-ID', b.ohne_scryfall ?? [], b.ohne_scryfall_anzahl,
      (e) => `Zeile ${e.zeile}: ${e.name}`)
    + berichtListe('Im Export nicht mehr enthalten (bleiben in der Kiste)', b.fehlend ?? [], b.fehlend_anzahl, kartenText)
    + (b.doppel
      ? berichtListe(`Mögliche Doppel (${zahl(b.doppel.gegen_kisten)} gegen andere Kisten, ${zahl(b.doppel.gegen_binder)} gegen Binder)`,
        b.doppel.liste ?? [], b.doppel.posten,
        (e) => `${kartenText(e)} liegt auch in ${e.ort}${e.quelle ? ` (${e.quelle})` : ''}`)
      : '');
}

function listeImportieren() {
  const s = S;
  s.wurzel.querySelector('.kiste-dateiwahl')?.remove();
  const eingabe = el('input', 'kiste-dateiwahl');
  eingabe.type = 'file';
  eingabe.accept = '.csv,text/csv';
  eingabe.hidden = true;
  eingabe.setAttribute('aria-label', 'Kartenliste (CSV) wählen');
  eingabe.addEventListener('change', () => {
    const datei = eingabe.files?.[0];
    eingabe.remove();
    if (datei && S === s) importDialog(datei);
  });
  s.wurzel.append(eingabe);
  eingabe.click();
}

async function importDialog(datei) {
  const s = S;
  const inhalt = el('div', 'kiste-bericht', '<p class="dialog-text">Die Datei wird geprüft …</p>');
  const d = dialog({ titel: 'Liste importieren', klasse: 'kiste-dialog', voll: true, inhalt });
  const zu = el('button', 'knopf', 'Abbrechen');
  zu.type = 'button';
  const los = el('button', 'knopf knopf-haupt', 'Importieren');
  los.type = 'button';
  los.disabled = true;
  d.fuss.append(zu, los);
  zu.addEventListener('click', () => d.schliessen(undefined));
  const pfad = `/api/kiste/${s.id}/liste?name=${encodeURIComponent(datei.name)}`;
  let importiert = null;
  d.fertig.then(async () => {
    const r = importiert;   // auch bei Esc: Wurde importiert, muss die Ansicht neu geladen werden
    if (r && S === s) {
      await neuAufbauen();
      hinweisZeigen(`Import aus „${datei.name}“: ${zahl(r.neu)} neu, ${zahl(r.geaendert)} geändert, ${zahl(r.unveraendert)} unverändert.`);
    }
  });
  try {
    const probe = await senden(pfad, { datei });
    berichtZeichnen(inhalt, probe, false);
    los.disabled = probe.posten === 0;
  } catch (f) {
    inhalt.innerHTML = `<p class="meldung" role="alert">${esc(f.message)}</p>`;
    zu.textContent = 'Schließen';
    return;
  }
  los.addEventListener('click', async () => {
    los.disabled = true;
    los.textContent = 'Importiere …';
    try {
      importiert = await senden(`${pfad}&ausfuehren=1`, { datei });
      berichtZeichnen(inhalt, importiert, true);
      los.hidden = true;
      zu.textContent = 'Schließen';
      zu.classList.add('knopf-haupt');
    } catch (f) {
      inhalt.insertAdjacentHTML('afterbegin', `<p class="meldung" role="alert">${esc(f.message)}</p>`);
      los.textContent = 'Noch einmal versuchen';
      los.disabled = false;
    }
    inhalt.scrollTop = 0;
  });
}

// ---------- Foil-Modus ----------

/** Foil-Modus: Tippen auf eine Karte schaltet Foil um (öffnet nie die Kartenansicht). Endet mit der Ansicht. */
function foilModusUmschalten(an = !S.foilModus) {
  const s = S;
  if (!s || Boolean(an) === s.foilModus) return;
  if (an && kistenBlick(s)) fachModusWechseln('alle', { merken: false });   // Foil braucht Karten
  if (an && s.wahlModus) wahlUmschalten(false);   // immer nur ein Modus
  s.foilModus = Boolean(an);
  senken();
  s.wurzel.classList.toggle('kiste-foilmodus', s.foilModus);
  s.wurzel.querySelector('.kiste-foilknopf')?.setAttribute('aria-pressed', String(s.foilModus));
  let leiste = s.wurzel.querySelector('.kiste-foilleiste');
  if (s.foilModus && !leiste) {
    leiste = el('div', 'kiste-foilleiste', '<span class="foil-text" role="status">Foil-Modus: Karten antippen</span>'
      + '<button type="button" class="knopf knopf-haupt foil-fertig">Fertig</button>');
    leiste.setAttribute('role', 'region');
    leiste.setAttribute('aria-label', 'Foil-Modus');
    leiste.querySelector('.foil-fertig').addEventListener('click', () => foilModusUmschalten(false));
    s.wurzel.append(leiste);
  }
  if (leiste) leiste.hidden = !s.foilModus;
}

/** Eine Karte im Foil-Modus antippen: Exemplar umschalten, Liste vor Ort oder (bei geteilten Stapeln) neu laden. */
async function foilKlick(a) {
  const s = S;
  if (s.foilLaeuft) return;
  const index = Number(a.dataset.i);
  const k = s.karten[index - s.start];
  if (!k) return;
  s.foilLaeuft = true;
  a.classList.add('kiste-foil-laedt');
  const oben = a.getBoundingClientRect().top;
  let r;
  try {
    r = await senden(`/api/karte/${k.id}/foil`, { daten: { foil: !k.foil } });
  } catch (f) {
    if (S === s) { s.foilLaeuft = false; a.classList.remove('kiste-foil-laedt'); hinweisZeigen(f.message, { art: 'fehler' }); }
    return;
  }
  if (S !== s) return;
  const text = r.schritt ? `„${k.name}“: ${r.foil ? 'Foil' : 'kein Foil'}.` : `„${k.name}“ war schon ${r.foil ? 'Foil' : 'kein Foil'}.`;
  try {
    if (r.neu_laden) {   // ein Stapel hat sich geteilt oder zusammengelegt: Liste neu laden, Lage auf dem Bildschirm halten
      await neuLaden({ ab: Math.max(0, index - 30) });
      if (S !== s) return;
      const neu = s.wurzel.querySelector(`.kiste-liste [data-i="${index}"]`);
      if (neu) window.scrollTo(0, Math.max(0, neu.getBoundingClientRect().top + window.scrollY - oben));
    } else {
      k.foil = r.foil;
      if (r.preis !== undefined) k.preis = r.preis;
      const li = a.closest('li');
      const vorlage = document.createElement('template');
      vorlage.innerHTML = HTML_JE_ANSICHT[s.ansicht](k, index);
      li.replaceWith(vorlage.content);
    }
  } finally {
    s.foilLaeuft = false;   // auch wenn das Neuladen scheitert, sonst hinge der Foil-Modus
  }
  hinweisZeigen(text, { schritt: r.schritt });
}

// ---------- Karte hinzufügen ----------

async function karteHinzufuegen() {
  const s = S;
  let antwort = null;
  // Sprache: die zuletzt gewählte (Entscheidung des Nutzers, 30.10.2026), beim ersten Mal Deutsch
  const zuletzt = vorliebe('hinzufuegen.sprache');
  const wahl = await druckWaehlen({
    titel: 'Karte hinzufügen', uebernehmen: 'Hinzufügen', sprache: /^[a-z]{2,3}$/.test(zuletzt ?? '') ? zuletzt : 'de',
    zusatz: [
      { name: 'anzahl', etikett: 'Anzahl', art: 'zahl', wert: 1, min: 1, max: 999 },
      { name: 'zustand', etikett: 'Zustand', art: 'auswahl', wert: 'near_mint', optionen: ZUSTAENDE },
    ],
    speichern: async (w) => {
      antwort = await senden(`/api/kiste/${s.id}/karte`, { daten: w });
      if (w.sprache) vorliebe('hinzufuegen.sprache', w.sprache);
      return antwort;
    },
  });
  if (!wahl || S !== s) return;
  await neuAufbauen();
  hinweisZeigen(antwort.neu
    ? `${zahl(antwort.anzahl)}× „${antwort.name}“ hinzugefügt.`
    : `„${antwort.name}“ war schon in dieser Kiste: ${zahl(antwort.anzahl)} dazugezählt.`, { schritt: antwort.schritt });
}

// ---------- Liste abziehen (nur Sammlung, eigene Datei) ----------

async function abziehenOeffnen() {
  const s = S;
  let geaendert = false;
  try {
    const { oeffnen } = await import('./abzug.js');
    geaendert = await oeffnen();
  } catch (f) {
    if (S === s) hinweisZeigen(f.message || 'Die Abzugsansicht ließ sich nicht laden.', { art: 'fehler' });
    return;
  }
  if (geaendert && S === s) await neuAufbauen();
}

// ---------- Ereignisse ----------

function verdrahten(s) {
  const w = s.wurzel;
  const huelle = w.querySelector('.kiste-huelle');
  const liste = w.querySelector('.kiste-liste');
  const sprungKnopfUhr = { uhr: 0, zuletzt: -1 };

  // Bilder, die fehlen: schlichte Kartenfläche mit Namen (das Ereignis „error“ steigt nicht auf, daher Erfassung)
  huelle.addEventListener('error', (e) => {
    const bild = e.target;
    if (!(bild instanceof HTMLImageElement) || !bild.matches('.kiste-bild, .fach-bild')) return;
    // Neu hinzugefügte Karten haben noch kein Vorschaubild: am PC mit Katalog das lokale Scryfall-Bild nehmen,
    // im Add-on (oder wenn auch das fehlt) das Bild direkt von Scryfall, bis der PC die Vorschau nachliefert
    // Nach „Datenbank löschen“ hängt bildPfad den Stand an (?s=…); darum auch mit Anfrageteil (Meldung, 07.11.2026)
    const m = /(?:^|\/)bilder\/scryfall\/([^/?]+)_\d+\.webp(?:\?.*)?$/.exec(bild.getAttribute('src') ?? '');
    if (m) bild.dataset.sid = m[1];
    const sid = bild.dataset.sid;
    if (sid && katalogJa && !sitzung().ha && !bild.dataset.katalog) {
      bild.dataset.katalog = '1';
      bild.removeAttribute('srcset');
      bild.src = serverAdresse(`/api/pruefen/bild/${sid}`);
      return;
    }
    if (sid && !bild.dataset.cdn && scryfallBild(sid)) {
      bild.dataset.cdn = '1';
      bild.removeAttribute('srcset');
      bild.src = scryfallBild(sid);
      return;
    }
    const a = bild.closest('a');
    if (a) {
      const k = { name: a.dataset.name, set: null, nummer: null };
      bild.replaceWith(el('span', 'kiste-flaeche', `<span class="kiste-flaeche-name">${esc(k.name ?? '')}</span>`
        + (a.dataset.set ? `<span class="kiste-flaeche-set">${esc(a.dataset.set)}</span>` : '')));
      a.classList.add('kiste-fehlt');
    } else {
      bild.closest('.kiste-hub-bild')?.classList.add('kiste-fehlt');
      bild.remove();
    }
  }, true);

  huelle.addEventListener('click', (e) => {
    const fachKnopf = e.target.closest('[data-fach]');
    if (fachKnopf) { fachZiehen(fachKnopf); return; }
    const nochmal = e.target.closest('.kiste-nochmal');
    if (nochmal) {
      s.neuFehler = null;
      s.fehlerNach = { oben: null, unten: null };
      if (!s.karten.length) neuLaden(); else { untenZeichnen(); beobachten(); }
      return;
    }
    const alle = e.target.closest('[data-loeschen="alle"]');
    if (alle) { filterLoeschen('alle'); return; }
    if (s.foilModus) {  // Foil-Modus: Tippen schaltet Foil um, öffnet nichts
      const hubKarte = e.target.closest('.kiste-hub')?.dataset.hubKarte;
      const karteA = e.target.closest('a[data-karte]') ?? (hubKarte ? liste.querySelector(`a[data-karte="${hubKarte}"]`) : null);
      if (karteA) { e.preventDefault(); foilKlick(karteA); }
      else if (e.target.closest('.kiste-hub')) e.preventDefault();
      return;
    }
    if (s.wahlModus) {  // Auswahlmodus: Tippen wählt, öffnet nichts
      const gewaehltA = e.target.closest('a[data-karte]');
      if (gewaehltA) { e.preventDefault(); wahlKlick(gewaehltA); }
      return;
    }
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button) return;
    const hub = e.target.closest('.kiste-hub');
    if (hub && hub.dataset.hubKarte) {
      e.preventDefault();
      gehe(`${s.basis}/karte/${hub.dataset.hubKarte}`);
      return;
    }
    const a = e.target.closest('a[data-karte]');
    if (!a) return;
    e.preventDefault();
    // Karteikasten: Mit der Maus liegt die Karte durch das Darüberfahren schon, ein Klick öffnet. Beim Tippen
    // hebt das erste Tippen die Karte, das zweite öffnet sie.
    if (s.ansicht === 'kasten' && s.gehoben !== a && e.pointerType !== 'mouse') { heben(a); return; }
    gehe(`${s.basis}/karte/${a.dataset.karte}`);
  });

  // Hover (nur Maus) und Fokus heben die Karte im Karteikasten
  liste.addEventListener('pointerover', (e) => {
    if (s.ansicht !== 'kasten' || e.pointerType !== 'mouse') return;
    const a = e.target.closest('a[data-karte]');
    if (!a || a === s.gehoben) return;
    clearTimeout(s.hebeUhr);
    s.hebeUhr = setTimeout(() => heben(a), bewegungReduziert() ? 0 : 60);
  });
  liste.addEventListener('focusin', (e) => {
    if (s.ansicht !== 'kasten') return;
    const a = e.target.closest?.('a[data-karte]');
    if (a && a.matches(':focus-visible')) heben(a);  // nur Tastatur: Tippen und Klicken regeln die Klicks selbst
  });
  liste.addEventListener('keydown', (e) => {
    if (e.key === ' ' && s.wahlModus) {   // Leertaste wählt wie ein Tipp
      const a = e.target.closest?.('a[data-karte]');
      if (a) { e.preventDefault(); wahlKlick(a); }
      return;
    }
    const rechts = s.ansicht === 'raster';
    const vor = e.key === (rechts ? 'ArrowRight' : 'ArrowDown');
    const zurueckTaste = e.key === (rechts ? 'ArrowLeft' : 'ArrowUp');
    if (!vor && !zurueckTaste) return;
    const a = e.target.closest?.('a[data-karte]');
    if (!a) return;
    let li = a.parentElement;
    do { li = vor ? li.nextElementSibling : li.previousElementSibling; } while (li && !li.querySelector('a'));
    if (li) { e.preventDefault(); li.querySelector('a').focus(); }
  });

  w.querySelector('.kiste-zurueck').addEventListener('click', () => zurueck(s.fach === null ? '#/' : fachPfad(s, null)));
  w.querySelector('.fachkopf-zu').addEventListener('click', () => zurueck(fachPfad(s, null)));
  w.querySelector('.kiste-fachwechsel').addEventListener('click', (e) => {
    const b = e.target.closest('[data-fachmodus]');
    if (b) fachModusWechseln(b.dataset.fachmodus);
  });

  w.querySelector('.kiste-bedienung .kiste-segmente').addEventListener('click', (e) => {
    const b = e.target.closest('[data-ansicht]');
    if (b) ansichtWechseln(b.dataset.ansicht);
  });
  w.querySelector('#kiste-sort').addEventListener('change', (e) => sortierungWechseln(e.target.value));
  w.querySelector('.kiste-richtung').addEventListener('click', richtungWechseln);
  w.querySelector('#kiste-gruppe').addEventListener('change', (e) => gruppeWechseln(e.target.value));
  w.querySelector('#kiste-sprung').addEventListener('change', (e) => {
    const g = s.gruppen?.[Number(e.target.value)];
    e.target.value = '';
    if (g) springe(g.ab);
  });

  // Textsuche mit 250 ms Verzögerung
  const suche = w.querySelector('.kiste-suche');
  suche.addEventListener('input', () => {
    clearTimeout(s.sucheUhr);
    s.sucheUhr = setTimeout(() => {
      if (suche.value.trim() === s.filter.q.trim()) return;
      filterAendern((f) => { f.q = suche.value; });
    }, 250);
  });
  suche.addEventListener('keydown', (e) => { if (e.key === 'Enter') suche.blur(); });

  // Filterfeld
  const filterfeld = w.querySelector('.kiste-filterfeld');
  const filterknopf = w.querySelector('.kiste-filterknopf');
  filterknopf.addEventListener('click', () => {
    filterfeld.hidden = !filterfeld.hidden;
    filterknopf.setAttribute('aria-expanded', String(!filterfeld.hidden));
  });
  filterfeld.addEventListener('click', (e) => {
    const p = e.target.closest('.kiste-pille');
    if (p) { filterKlick(p); return; }
    if (e.target.closest('.kiste-alle-loeschen')) filterLoeschen('alle');
  });
  filterfeld.addEventListener('change', (e) => {
    const t = e.target;
    if (t.dataset.mw) {
      filterAendern((f) => {
        f[t.dataset.mw] = t.value === '' ? null : Number(t.value);
        if (f.mw_von !== null && f.mw_bis !== null && f.mw_von > f.mw_bis) {
          if (t.dataset.mw === 'mw_von') f.mw_bis = f.mw_von; else f.mw_von = f.mw_bis;
        }
      });
    } else if (t.classList.contains('kiste-setwahl')) {
      const wert = t.value;
      if (wert) filterAendern((f) => { if (!f.set.includes(wert)) f.set.push(wert); });
    }
  });
  const anzahlFeld = w.querySelector('.kiste-anzahl');
  anzahlFeld.addEventListener('input', () => {
    clearTimeout(s.anzahlUhr);
    s.anzahlUhr = setTimeout(() => {
      const n = parseInt(anzahlFeld.value, 10);
      const neu = Number.isInteger(n) && n >= 1 ? Math.min(999, n) : null;
      if (neu === s.filter.anzahl_min) return;
      filterAendern((f) => { f.anzahl_min = neu; });
    }, 250);
  });

  // aktive Filter: ein Tipp löscht
  w.querySelector('.kiste-aktiv').addEventListener('click', (e) => {
    const b = e.target.closest('[data-loeschen]');
    if (b) filterLoeschen(b.dataset.loeschen, b.dataset.wert);
  });

  // Gesamtsammlung: „mögliche Doppel“ im Kopf filtert
  w.querySelector('.kiste-sammlung-info').addEventListener('click', (e) => {
    const b = e.target.closest('[data-filter]');
    if (b) filterKlick(b);
  });

  // Auswahlleiste
  const leiste = w.querySelector('.kiste-wahlleiste');
  leiste.addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.classList.contains('kiste-wahl-fertig')) wahlUmschalten(false);
    else if (b.classList.contains('kiste-verschieben')) verschieben();
    else if (b.classList.contains('kiste-zurueckstellen')) zurueckstellen();
    else if (b.dataset.wahl === 'seite') wahlAlleSeite();
    else if (b.dataset.wahl === 'gefiltert') wahlAlleGefiltert();
    else if (b.dataset.wahl === 'keine') wahlLeeren();
  });

  // Statistik
  const statKnopf = w.querySelector('.kiste-statknopf');
  const statBox = w.querySelector('.kiste-statistik');
  statKnopf.addEventListener('click', () => {
    s.statistikOffen = statBox.hidden;
    statBox.hidden = !s.statistikOffen;
    statKnopf.setAttribute('aria-expanded', String(s.statistikOffen));
    if (s.statistikOffen) statistikLaden();
  });

  // Sprungleiste: Tippen, Wischen und Tastatur
  const nav = w.querySelector('.kiste-sprung');
  const leistenSprung = (e, sofort) => {
    const r = nav.getBoundingClientRect();
    const n = s.leiste?.length ?? 0;
    if (!n || r.height <= 0) return;
    const i = Math.min(n - 1, Math.max(0, Math.floor(((e.clientY - r.top) / r.height) * n)));
    if (i === sprungKnopfUhr.zuletzt && !sofort) return;
    sprungKnopfUhr.zuletzt = i;
    clearTimeout(sprungKnopfUhr.uhr);
    const ab = s.leiste[i].ab;
    if (sofort) springe(ab); else sprungKnopfUhr.uhr = setTimeout(() => springe(ab), 70);
  };
  nav.addEventListener('pointerdown', (e) => {
    nav.setPointerCapture(e.pointerId);
    sprungKnopfUhr.zuletzt = -1;
    leistenSprung(e, true);
  });
  nav.addEventListener('pointermove', (e) => { if (nav.hasPointerCapture(e.pointerId)) leistenSprung(e, false); });
  nav.addEventListener('click', (e) => {
    const b = e.target.closest('.kiste-sprung-knopf');
    if (b && e.detail === 0) springe(s.leiste[Number(b.dataset.n)].ab);
  });

  // Nach oben
  const hoch = w.querySelector('.kiste-hoch');
  hoch.addEventListener('click', () => window.scrollTo({ top: 0, behavior: bewegungReduziert() ? 'auto' : 'smooth' }));
  let geplant = false;
  const beimScrollen = () => {
    if (geplant) return;
    geplant = true;
    requestAnimationFrame(() => {
      geplant = false;
      hoch.hidden = window.scrollY < 900;
    });
  };
  window.addEventListener('scroll', beimScrollen, { passive: true });
  s.aufraeumen.push(() => window.removeEventListener('scroll', beimScrollen));

  // Esc schließt das Filterfeld (die große Karte und Dialoge behandeln Esc selbst)
  const taste = (e) => {
    if (e.key !== 'Escape' || dialogOffen() || /\/karte\/\d+/.test(location.hash)) return;
    if (!filterfeld.hidden) {
      filterfeld.hidden = true;
      filterknopf.setAttribute('aria-expanded', 'false');
      filterknopf.focus();
    } else if (s.wahlModus) {
      wahlUmschalten(false);
    } else if (s.foilModus) {
      foilModusUmschalten(false);
    }
  };
  document.addEventListener('keydown', taste);
  s.aufraeumen.push(() => document.removeEventListener('keydown', taste));

  // Nachladen: Beobachter für Anfang und Ende der Liste
  s.io = new IntersectionObserver((eintraege) => {
    for (const eintrag of eintraege) {
      if (!eintrag.isIntersecting) continue;
      nachladen(eintrag.target.classList.contains('kiste-oben') ? 'oben' : 'unten');
    }
  }, { rootMargin: '1500px 0px 1500px 0px' });
  s.aufraeumen.push(() => s.io.disconnect());

  const beiSitzung = () => adminZeichnen();
  window.addEventListener('binder:sitzung', beiSitzung);
  s.aufraeumen.push(() => window.removeEventListener('binder:sitzung', beiSitzung));
}

// ---------- Öffentliche Schnittstelle ----------

function abraeumen() {
  const s = S;
  if (!s) return;
  S = null;
  if (s.anfragen) alleAbbrechen(s);   // `s` ist nur eine Marke, solange der Kopf noch geladen wird
  s.statAbbruch?.abort();
  clearTimeout(s.sucheUhr);
  clearTimeout(s.anzahlUhr);
  clearTimeout(s.hebeUhr);
  for (const f of s.aufraeumen ?? []) f();
}

function fehlerAnzeigen(wurzel, text) {
  wurzel.innerHTML = '<section class="kiste"><p class="hinweis kiste-fehlertext"></p>'
    + '<p class="kiste-fehlerknopf"><button type="button" class="knopf">Zurück ins Regal</button></p></section>';
  wurzel.querySelector('.kiste-fehlertext').textContent = text;
  wurzel.querySelector('button').addEventListener('click', () => zurueck('#/'));
}

/** Baut die Ansicht auf: eine Kiste (`parameter.kiste`) oder die Gesamtsammlung (`parameter.sammlung`).
    `still`: ohne Einblendung und zunächst verborgen (Neuaufbau nach einer Änderung, siehe neuAufbauen);
    `wahlModus`: Auswahlmodus gleich wieder einschalten. Gibt den Zustand zurück (`erstes` = erste Seite geladen). */
async function aufbauen(wurzel, parameter, { still = false, wahlModus = false, foilModus = false, offen = null,
  fachModus = null } = {}) {
  abraeumen();
  const sammlung = Boolean(parameter.sammlung);
  const id = sammlung ? null : parameter.kiste;
  const marke = {};
  S = marke;
  let kopf;
  let optionen;
  try {
    [kopf, optionen] = await Promise.all([anfordern(sammlung ? '/api/sammlung' : `/api/kiste/${id}`), optionenHolen()]);
  } catch (f) {
    if (S !== marke) return null;   // inzwischen woanders hingegangen: nichts über die neue Ansicht malen
    S = null;
    fehlerAnzeigen(wurzel, f.message);
    return null;
  }
  if (S !== marke) return null;  // inzwischen woanders hingegangen

  const schluessel = sammlung ? 'sammlung' : String(id);
  const gespeichert = (name) => vorliebe(`kiste.${schluessel}.${name}`);
  // „Fach“ gibt es nur in einer Kiste mit Fächern (nie in der Gesamtsammlung)
  const hatFaecher = !sammlung && Array.isArray(kopf.faecher) && kopf.faecher.some((f) => f.gueltig);
  const gruppierungen = (sammlung ? kopf.gruppierungen : optionen.gruppierungen).filter((x) => x.wert !== 'fach' || hatFaecher);
  const sortWert = optionen.sortierungen.some((x) => x.wert === gespeichert('sort')) ? gespeichert('sort') : 'name';
  const standardRichtung = optionen.sortierungen.find((x) => x.wert === sortWert).richtung;
  const s = {
    id, sammlung, schluessel, parameter,
    basis: sammlung ? '#/sammlung' : `#/kiste/${id}`,
    hatFaecher,
    fachModus: hatFaecher ? fachModus ?? (gespeichert('fachmodus') === 'alle' ? 'alle' : 'faecher') : 'alle',
    fach: null, fachkasten: null, fachZieht: false, fachY: 0,
    kopf,
    optionen,
    gruppierungen,
    wahlModus: false, foilModus: false, foilLaeuft: false, gewaehlt: new Map(), alleGefiltert: false, erstes: null,
    optionenText: {
      typ: Object.fromEntries(optionen.typen.map((x) => [x.wert, x.text])),
      seltenheit: Object.fromEntries(optionen.seltenheiten.map((x) => [x.wert, x.text])),
    },
    wurzel: el('section', 'kiste'),
    ansicht: ANSICHTEN.includes(gespeichert('ansicht')) ? gespeichert('ansicht')
      : (window.matchMedia(HANDY).matches ? 'liste' : 'raster'),
    sort: sortWert,
    richtung: ['auf', 'ab'].includes(gespeichert('richtung')) ? gespeichert('richtung') : standardRichtung,
    gruppe: gruppierungen.some((x) => x.wert === gespeichert('gruppe')) ? gespeichert('gruppe') : '',
    filter: filterLesen(gespeichert('filter'), optionen),
    karten: [], start: 0, gesamt: null, gruppen: null, gruppeBei: new Map(), gruppenSchluessel: '', leiste: [],
    gen: 0, anfragen: new Set(), laed: { oben: false, unten: false }, fehlerNach: { oben: null, unten: null },
    neuLaedt: false, neuFehler: null, gehoben: null, statistikOffen: false, aufraeumen: [], io: null,
  };
  S = s;
  // Fach aus der Adresse; gibt es das Fach nicht (mehr), bleibt die Kiste
  const fachWert = hatFaecher ? parameter.fach ?? null : null;
  if (fachWert !== null && fachLesen(s, fachWert)) {
    s.fach = fachWert;
    s.fachModus = 'faecher';
    s.basis = fachPfad(s, fachWert);
  } else if (parameter.fach != null) {
    gehe(fachPfad(s, null), { ersetzen: true });
  }
  s.wurzel.innerHTML = geruestHtml(s);
  s.wurzel.setAttribute('role', 'region');
  s.wurzel.classList.toggle('kiste-sammlung', sammlung);
  katalogDa();   // nicht abwarten: für „Karte hinzufügen“ und Bilder neuer Karten
  if (still) { s.wurzel.classList.add('kiste-still'); s.wurzel.hidden = true; }
  if (sammlung) {   // Einstieg in die Set-Ansicht (sets.js)
    const sets = Object.assign(document.createElement('a'), { className: 'knopf kiste-sets', href: '#/sets' });
    sets.setAttribute('aria-label', 'Sets öffnen: wie vollständig die Sammlung je Set ist');
    sets.textContent = 'Sets';
    sets.addEventListener('click', (e) => {
      if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return;
      e.preventDefault();
      gehe('#/sets');
    });
    s.wurzel.querySelector('.kiste-aktionen').append(sets);
  }
  s.wurzel.querySelector('.kiste-aktionen').append(anmeldeKnopf());
  wurzel.append(s.wurzel);
  titelSetzen();
  zahlenZeichnen();
  sammlungInfoZeichnen();
  obenSync();
  filterSync();
  fachAnsichtSync();
  adminZeichnen();
  verdrahten(s);
  if (wahlModus) wahlUmschalten(true);
  if (foilModus) foilModusUmschalten(true);
  if (offen?.filter) {   // Filterfeld und Statistik bleiben nach einer Änderung so offen, wie sie waren
    s.wurzel.querySelector('.kiste-filterfeld').hidden = false;
    s.wurzel.querySelector('.kiste-filterknopf').setAttribute('aria-expanded', 'true');
  }
  if (offen?.statistik) {
    s.statistikOffen = true;
    s.wurzel.querySelector('.kiste-statistik').hidden = false;
    s.wurzel.querySelector('.kiste-statknopf').setAttribute('aria-expanded', 'true');
  }
  s.erstes = neuLaden();
  return s;
}

export async function zeigen(wurzel, parameter) {
  await aufbauen(wurzel, parameter);
}

/** Gleiche Kiste: Die große Karte kommt oder geht (nichts neu aufbauen), oder ein Fach wird herausgezogen oder eingesteckt. */
export function aktualisieren(parameter) {
  const s = S;
  if (!s?.wurzel || s.sammlung) return;
  s.parameter = parameter;
  const neu = s.hatFaecher ? parameter.fach ?? null : null;
  if (neu !== null && !fachLesen(s, neu)) gehe(fachPfad(s, null), { ersetzen: true });   // das Fach gibt es nicht (mehr)
  else if (neu !== s.fach) fachAnzeigen(neu);
}

export function verlassen() {
  abraeumen();
  return Promise.resolve();
}



