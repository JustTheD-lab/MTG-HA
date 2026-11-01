// Lesetisch: der aufgeschlagene Binder auf dem Schreibtisch (Ansicht #/binder/B/S).
//
// PC (istBreit): Doppelseite wie im echten Binder: [Innenseite des Einbands | Seite 1], [2 | 3], [4 | 5] …
//   Die Adresse #/binder/B/S zeigt die Doppelseite, die Seite S enthält.
// Handy: eine Seite, Wischen nach links/rechts, Knöpfe als Alternative.
// Blättern mit CSS-3D; animiert werden nur transform und opacity. Alle Taschen entstehen im
// echten Raster (zeilen x spalten); jede Karte ist ein Knopf, der die Kartenansicht öffnet.
//
// „Index" heißt: Nummer der Doppelseite (PC, ab 0) bzw. Nummer der Seite (Handy, ab 1).
//
// Leerseiten (leeres Taschenraster mit Etikett) und Fotoseiten ohne Taschen (Status: wartet, wird
// erkannt, Fehler) werden wie jede Seite gezeigt. Solange etwas wartet, fragt der Lesetisch alle
// 4 Sekunden nach (und sofort bei 'binder:geaendert'). Die Seitenverwaltung (Leerseiten, Fotos,
// Name und Farbe, Verlauf) gibt es nur angemeldet. Die Ansicht merkt sich die Seiten-ID und bleibt
// beim Umnummerieren auf ihrer Seite.
//
// Exporte für den Wegweiser (app.js): zeigen, aktualisieren, verlassen.

import { holen, senden, geaendert, bildUrl, bildSrcset, esc, gehe, zurueck, bewegungReduziert, istBreit, vorliebe, warten,
  euro, preisStreifen, preisText } from './gemeinsam.js';
import { dialog, bestaetigen, dialogOffen } from './dialog.js';
import { angemeldet, knopf as anmeldeKnopf } from './anmeldung.js';

const KARTE = 63 / 88;        // Seitenverhältnis einer Karte
const INNEN = 0.035;          // Rand der Seite, im Verhältnis zur Seitenbreite
const LUECKE = 0.03;          // Abstand zwischen den Taschen
const RAND = 0.04;           // Einbandrand rund um die Seiten (PC)
const RAND_RINGE = 0.075;     // Einbandrand mit den Ringen (Handy, links)
const RAND_EINZEL = 0.04;     // Einbandrand (Handy, sonst)
const DAUER_DOPPEL = 600;     // Umblättern am PC in ms
const DAUER_EINZEL = 400;     // Umblättern am Handy in ms
const KURVE = 'cubic-bezier(0.45, 0.05, 0.3, 1)';
const SCHWELLE = 50;          // Wischen ab so vielen Pixeln
const TAKT = 4000;            // Abfrage in ms, solange Fotos auf die Erkennung warten
const WARTET = new Set(['neu', 'in_verarbeitung']);
const OHNE_TASCHEN = new Set(['neu', 'in_verarbeitung', 'fehler']);
const FARBEN = [['schwarz', 'schwarz'], ['durchsichtig', 'durchsichtig'], ['gelb', 'gelb'], ['blau', 'blau'],
  ['gruen', 'grün'], ['rot', 'rot'], ['braun', 'braun']];

/** Schlichte Strich-Symbole (Inline-SVG, currentColor). */
const SYM = (pfad, breite = 1.5) => `<svg class="tisch-sym" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false"`
  + ` fill="none" stroke="currentColor" stroke-width="${breite}" stroke-linecap="round" stroke-linejoin="round"><path d="${pfad}"/></svg>`;
const SYM_ZURUECK = SYM('M15 5l-7 7 7 7');
const SYM_WEITER = SYM('M9 5l7 7-7 7');
const SYM_HAKEN = SYM('M5 12.5l4.5 4.5L19 7.5', 2.6);
const SYM_SEITEN = SYM('M8 3h9a2 2 0 0 1 2 2v11M5 7h9a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2z');
const SYM_MINUS = SYM('M6 12h12');
const SYM_PLUS = SYM('M12 6v12M6 12h12');

/** Zustand des gerade angezeigten Lesetisches, sonst null. */
let z = null;

const el = (tag, klasse, html) => {
  const e = document.createElement(tag);
  if (klasse) e.className = klasse;
  if (html !== undefined) e.innerHTML = html;
  return e;
};

// ---------- Rechnen mit Seiten und Indizes ----------

const minIndex = () => (z.breit ? 0 : 1);
const maxIndex = () => (z.breit ? Math.floor(z.N / 2) : z.N);
const indexVon = (seite) => (z.breit ? Math.floor(seite / 2) : seite);
const seiteVon = (index) => (z.breit ? Math.max(1, 2 * index) : index);
const linksVon = (index) => 2 * index;       // 0 = Innenseite des vorderen Einbands
const rechtsVon = (index) => 2 * index + 1;  // N + 1 = Innenseite des hinteren Einbands
const seitenVon = (index) => (z.breit ? [linksVon(index), rechtsVon(index)] : [index])
  .filter((n) => n >= 1 && n <= z.N);
const karteOffen = () => /\/karte\/\d+/.test(location.hash);

// ---------- Einstieg ----------

export async function zeigen(wurzel, p, vorher) {
  abbauen();
  const tisch = el('div', 'tisch', '<header class="tisch-leiste"></header>'
    + '<div class="tisch-auflage"><div class="tisch-buehne"></div></div>');
  tisch.setAttribute('aria-live', 'off');  // #buehne meldet sonst jeden Seitenwechsel vor
  tisch.dataset.farbe = 'schwarz';
  wurzel.replaceChildren(tisch);
  const zustand = z = {
    tisch, leiste: tisch.firstElementChild, buehne: tisch.querySelector('.tisch-buehne'),
    id: p.binder, seite: p.seite, abbau: [], lauf: null, vor: new Map(), timer: 0, gewischt: -1e9,
    geht: false, sizes: '120px', breit: false, N: 0, taschen: new Map(), index: 1,
    art: vorliebe('bildart') === 'foto' ? 'foto' : 'scryfall',
    info: new Map(), sigs: new Map(), ladeNr: 0, angewandt: 0, wartend: null, abfrage: 0,
    beruehrt: 0, folgeId: null, dlg: null, binder: null, anmeldeKnopf: anmeldeKnopf(), seitenKnopf: null,
  };
  zustand.leiste.append(zurueckKnopf());
  const einblenden = vorher?.ansicht === 'regal' && !bewegungReduziert();
  if (einblenden) tisch.classList.add('tisch-hinein');  // Tisch blendet sich ein, solange der Binder lädt

  let binder;
  try {
    binder = await holen(`/api/binder/${p.binder}`);
  } catch (fehler) {
    if (z !== zustand) return;
    const nicht = /: 404$/.test(String(fehler.message));
    zustand.leiste.firstElementChild.addEventListener('click', zurRegal);
    zustand.buehne.innerHTML = `<p class="hinweis">${nicht
      ? 'Dieser Binder liegt nicht im Regal.' : 'Der Binder ließ sich nicht laden. Läuft der Server?'}</p>`;
    if (!nicht) console.error(fehler);
    return;
  }
  if (z !== zustand) return;  // inzwischen woanders hin

  datenSetzen(binder);
  tisch.dataset.farbe = binder.farbe || 'schwarz';
  zustand.angewandt = 0;
  zustand.seite = Math.min(Math.max(p.seite || 1, 1), Math.max(zustand.N, 1));
  // Kartenlinks bleiben stabil: Liegt die Karte inzwischen auf einer anderen Seite, dorthin springen.
  const kartenSeite = p.karte ? seiteDerKarte(p.karte) : null;
  if (kartenSeite) zustand.seite = kartenSeite;
  if (zustand.N && zustand.seite !== p.seite) adresseSchreiben(p.karte);

  ereignisse(zustand);
  aufbauen();
  layouten();
  abfrageSteuern();
  if (!zustand.N) return;
  fuellen();
  vorladen(1);
  zustand.timer = setTimeout(() => z && vorladen(2), 1500);
  if (einblenden) await oeffnen();
}

/** Gleiche Ansicht, andere Adresse: andere Seite → dorthin blättern; nur Karte geändert → nichts. */
export async function aktualisieren(p) {
  if (!z || !z.N) return;
  z.geht = false;
  let s = Math.min(Math.max(p.seite || 1, 1), z.N);
  const kartenSeite = p.karte ? seiteDerKarte(p.karte) : null;
  if (kartenSeite) s = kartenSeite;
  if (s === z.seite) {
    if (kartenSeite && kartenSeite !== p.seite) adresseSchreiben(p.karte);
    return;
  }
  z.seite = s;
  if (kartenSeite && kartenSeite !== p.seite) adresseSchreiben(p.karte);
  await blaettern(indexVon(s), false);
}

/** Schreibt die Seite des Lesetisches (und eine offene Karte) ohne Verlaufseintrag in die Adresse. */
function adresseSchreiben(karte = null) {
  const k = karte ?? /\/karte\/(\d+)$/.exec(location.hash)?.[1] ?? null;
  gehe(`#/binder/${z.id}/${z.seite}${k ? `/karte/${k}` : ''}`, { ersetzen: true });
}

/** Nummer der Seite, auf der die Karte liegt (oder null). */
function seiteDerKarte(kartenId) {
  for (const [n, felder] of z.taschen) {
    for (const t of felder.values()) {
      if (t.karte?.id === kartenId || t.karten?.some((k) => k.id === kartenId)) return n;
    }
  }
  return null;
}

// ---------- Daten, Aktualisieren, Abfrage ----------

function datenSetzen(binder) {
  z.binder = binder;
  z.N = 0;
  z.taschen = new Map();
  z.info = new Map();
  for (const s of binder.seiten) {
    z.N = Math.max(z.N, s.nummer);
    z.taschen.set(s.nummer, new Map(s.taschen.map((t) => [`${t.zeile}-${t.spalte}`, t])));
    z.info.set(s.nummer, s);
  }
  sigsBerechnen();
}

/** Je Seite ein Fingerabdruck ihrer Daten: nur Seiten, deren Daten sich ändern, werden neu gezeichnet. */
function sigsBerechnen() {
  const aktiv = z.binder.verarbeiter?.aktiv ?? null;
  z.sigs = new Map();
  for (const s of z.binder.seiten) {
    z.sigs.set(s.nummer, JSON.stringify([s.nummer, s.id, s.art, s.status, s.fehler, s.dateiname,
      WARTET.has(s.status) ? aktiv : null, s.status === 'fehler' ? angemeldet() : null, s.taschen]));
  }
}
const sigVon = (n) => (n < 1 || n > z.N ? 'vorsatz' : z.sigs.get(n));
const wartet = () => z.binder.seiten.some((s) => WARTET.has(s.status));

/** Holt den Binder neu und übernimmt ihn, sobald nichts mehr blättert oder gewischt wird. */
async function laden(sofort = false) {
  const zustand = z;
  if (!zustand || zustand.geloescht) return false;
  const nr = ++zustand.ladeNr;
  let binder;
  try {
    binder = await holen(`/api/binder/${zustand.id}`);
  } catch {
    return false;
  }
  if (z !== zustand || nr < zustand.angewandt) return false;
  zustand.angewandt = nr;
  zustand.wartend = binder;
  if (sofort) zustand.lauf?.beenden();
  anwendenFalls();
  return true;
}

/** Liegt gerade ein Finger auf der Seite? (Sicherheitsgrenze, falls das Loslassen nie ankommt: 5 s) */
const beruehrt = () => z.beruehrt > 0 && performance.now() - z.beruehrt < 5000;

function anwendenFalls() {
  if (!z || !z.wartend || z.lauf || beruehrt()) return;
  const binder = z.wartend;
  z.wartend = null;
  anwenden(binder);
}

/** Titel mit Gesamtwert des Binders (Durchschnitt 7 Tage, Wunsch des Nutzers). */
function titelSetzen(titel, binder) {
  titel.replaceChildren(Object.assign(document.createElement('span'), { className: 'tisch-name', textContent: binder.name }));
  if (binder.wert?.wert == null) return;
  const w = Object.assign(document.createElement('span'), { className: 'tisch-wert wert' });
  w.textContent = euro(binder.wert.wert);
  w.title = 'Wert: Summe aus Anzahl × Durchschnittspreis der letzten 7 Tage (Cardmarket)'
    + (binder.wert.ohne_preis ? `; ${binder.wert.ohne_preis} Karten ohne Preis` : '');
  titel.append(' ', w);
}

function anwenden(binder) {
  const warLeer = !z.N;
  const verfolgt = z.folgeId ?? z.info.get(z.seite)?.id ?? null;
  z.folgeId = null;
  datenSetzen(binder);
  z.tisch.dataset.farbe = binder.farbe || 'schwarz';
  const titel = z.leiste.querySelector('.tisch-titel');
  if (titel) titelSetzen(titel, binder);
  // Die Ansicht bleibt auf ihrer Seite (Seiten-ID), auch wenn davor Seiten dazukommen oder wegfallen.
  let neu = z.seite;
  if (verfolgt != null) neu = binder.seiten.find((s) => s.id === verfolgt)?.nummer ?? neu;
  neu = Math.min(Math.max(neu, 1), Math.max(z.N, 1));
  const verschoben = neu !== z.seite;
  z.seite = neu;
  if (warLeer !== !z.N) {  // erste Seiten sind da (oder alle weg): Ansicht neu aufbauen
    aufbauen();
    layouten();
    if (z.N) { fuellen(); vorladen(1); }
  } else if (z.N) {
    z.index = Math.min(Math.max(indexVon(z.seite), minIndex()), maxIndex());
    erneuern();
    leisteAktualisieren();
    vorladen(1);
  }
  if (verschoben && z.N) adresseSchreiben();
  z.dlgAktualisieren?.();
  abfrageSteuern();
}

/** Zeichnet die sichtbaren Seiten neu, deren Daten sich geändert haben. */
function erneuern() {
  if (!z.binderEl) return;
  const plaetze = z.breit
    ? [['links', linksVon(z.index), z.fl.links], ['rechts', rechtsVon(z.index), z.fl.rechts]]
    : [['einzel', z.index, z.fl.einzel]];
  for (const [name, n, flaeche] of plaetze) {
    if (z.kn?.[name]?._sig === sigVon(n)) continue;
    const seite = seiteBauen(n);
    flaeche.replaceChildren(seite);
    z.kn[name] = seite;
  }
}

function abfrageSteuern() {
  if (!z) return;
  if (wartet()) {
    if (!z.abfrage) z.abfrage = setTimeout(abfrageTakt, TAKT);
  } else if (z.abfrage) {
    clearTimeout(z.abfrage);
    z.abfrage = 0;
  }
}

async function abfrageTakt() {
  const zustand = z;
  if (!zustand) return;
  zustand.abfrage = 0;
  if (!document.hidden) await laden();
  if (z === zustand) abfrageSteuern();
}

/** Sitzung hat sich geändert: Schreibknöpfe zeigen bzw. verbergen. */
function sitzungGeaendert() {
  if (!z) return;
  if (z.seitenKnopf) z.seitenKnopf.hidden = !angemeldet();
  if (!angemeldet()) z.dlg?.schliessen();
  if (!z.N) return leereAnsicht();
  sigsBerechnen();
  if (!z.lauf && !beruehrt()) erneuern();
}

/** Abschied: Ins Regal geht der Binder zu und gleitet hinaus, sonst wird nur aufgeräumt. */
export async function verlassen(naechste) {
  const zustand = z;
  if (zustand && zustand.N && naechste?.ansicht === 'regal' && !bewegungReduziert()) {
    zustand.geht = true;
    zustand.lauf?.beenden();
    await schliessen(zustand);
  }
  if (z === zustand) abbauen();
}

function abbauen() {
  if (!z) return;
  z.lauf?.beenden();
  clearTimeout(z.timer);
  clearTimeout(z.abfrage);
  z.dlg?.schliessen();
  z.abbau.forEach((f) => f());
  z.vor.clear();
  z = null;
}

function zurRegal() {
  if (!z || z.geht) return;
  z.geht = true;
  setTimeout(() => { if (z) z.geht = false; }, 2000);  // falls „Zurück" nicht ins Regal führt
  zurueck('#/');
}

// ---------- Aufbau ----------

function zurueckKnopf() {
  const knopf = el('button', 'knopf tisch-zurueck',
    SYM_ZURUECK + '<span class="tisch-zurueck-text">Zurück ins Regal</span>');
  knopf.type = 'button';
  knopf.setAttribute('aria-label', 'Zurück ins Regal');
  return knopf;
}

function leisteBauen() {
  const zurueckKnopfAlt = z.leiste.querySelector('.tisch-zurueck') ?? zurueckKnopf();
  const art = (name, text) => `<button type="button" class="knopf" data-art="${name}"`
    + ` aria-pressed="${z.art === name}">${text}</button>`;
  const rest = el('div', 'tisch-leiste-rest', `
    <h1 class="tisch-titel">${esc(z.binder.name)}</h1>
    <div class="tisch-aktionen">
      <div class="tisch-bildart" role="group" aria-label="Bildart der Karten">
        ${art('scryfall', 'Scryfall')}${art('foto', 'Mein Foto')}
      </div>
      <button type="button" class="knopf tisch-seiten" aria-label="Seiten verwalten" title="Seiten verwalten"
        ${angemeldet() ? '' : 'hidden'}>${SYM_SEITEN}<span class="tisch-seiten-text">Seiten</span></button>
    </div>`);
  z.seitenKnopf = rest.querySelector('.tisch-seiten');
  titelSetzen(rest.querySelector('.tisch-titel'), z.binder);
  rest.querySelector('.tisch-aktionen').append(z.anmeldeKnopf);
  const nav = el('nav', 'tisch-nav', `
    <button type="button" class="knopf tisch-pfeil" data-blatt="-1" aria-label="Eine Seite zurück">${SYM_ZURUECK}</button>
    <p class="tisch-seitenzahl" role="status"></p>
    <button type="button" class="knopf tisch-pfeil" data-blatt="1" aria-label="Eine Seite weiter">${SYM_WEITER}</button>`);
  nav.setAttribute('aria-label', 'Blättern');
  z.leiste.replaceChildren(zurueckKnopfAlt, ...rest.children);
  z.nav?.remove();
  z.nav = nav;
  if (z.breit) z.leiste.append(nav);
  else z.tisch.append(nav);
}

function aufbauen() {
  z.lauf?.beenden();
  z.breit = istBreit();
  z.tisch.classList.toggle('tisch-breit', z.breit);
  z.tisch.classList.toggle('tisch-einzel', !z.breit);
  z.index = Math.min(Math.max(indexVon(z.seite), minIndex()), maxIndex());
  leisteBauen();
  if (!z.N) {
    z.binderEl = null;
    z.nav.hidden = true;
    z.leiste.querySelector('.tisch-bildart').hidden = true;
    leereAnsicht();
    return;
  }
  const b = el('div', 'tisch-binder');
  b.style.setProperty('--zeilen', z.binder.zeilen);
  b.style.setProperty('--spalten', z.binder.spalten);
  const ringe = '<i class="tisch-ring"></i>'.repeat(3);
  b.innerHTML = z.breit ? `
    <div class="tisch-aufgeschlagen">
      <div class="tisch-halbe tisch-halbe-l"><div class="tisch-flaeche tisch-flaeche-l"></div></div>
      <div class="tisch-halbe tisch-halbe-r"><div class="tisch-flaeche tisch-flaeche-r"></div></div>
      <span class="tisch-wurf tisch-wurf-l"></span><span class="tisch-wurf tisch-wurf-r"></span>
      <div class="tisch-ruecken" aria-hidden="true">${ringe}</div>
      <button type="button" class="tisch-ecke tisch-ecke-l" data-blatt="-1" tabindex="-1" aria-hidden="true"
        title="Zurückblättern"><span class="tisch-ecke-knopf">${SYM_ZURUECK}</span></button>
      <button type="button" class="tisch-ecke tisch-ecke-r" data-blatt="1" tabindex="-1" aria-hidden="true"
        title="Weiterblättern"><span class="tisch-ecke-knopf">${SYM_WEITER}</span></button>
    </div>` : `
    <div class="tisch-aufgeschlagen">
      <div class="tisch-halbe tisch-halbe-einzel"><div class="tisch-flaeche tisch-flaeche-einzel"></div></div>
      <div class="tisch-ruecken" aria-hidden="true">${ringe}</div>
    </div>`;
  z.binderEl = b;
  z.auf = b.firstElementChild;
  z.ruecken = b.querySelector('.tisch-ruecken');
  z.fl = {
    links: b.querySelector('.tisch-flaeche-l'), rechts: b.querySelector('.tisch-flaeche-r'),
    einzel: b.querySelector('.tisch-flaeche-einzel'),
  };
  z.wurf = { l: b.querySelector('.tisch-wurf-l'), r: b.querySelector('.tisch-wurf-r') };
  z.buehne.replaceChildren(b);
  z.buehne.style.touchAction = z.breit ? '' : 'pan-y';
}

/** Binder ohne Seiten: Hinweis, angemeldet dazu der Knopf zum Hochladen. */
function leereAnsicht() {
  if (!z || z.N) return;
  const wurzel = el('div', 'tisch-leer', '<p class="hinweis">Dieser Binder hat noch keine Seiten.</p>');
  if (angemeldet()) {
    wurzel.append(el('button', 'knopf knopf-haupt', 'Fotos hinzufügen'));
    wurzel.lastElementChild.type = 'button';
    wurzel.lastElementChild.dataset.fotos = '1';
  }
  z.buehne.replaceChildren(wurzel);
}

/** Größe der Seiten aus dem verfügbaren Platz: die ganze Doppelseite bzw. Seite passt immer. */
function layouten() {
  if (!z.binderEl) return;
  const cs = getComputedStyle(z.buehne);
  const vb = z.buehne.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  const vh = z.buehne.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
  if (vb <= 0 || vh <= 0) return;
  const { zeilen, spalten } = z.binder;
  const tb = (1 - 2 * INNEN - (spalten - 1) * LUECKE) / spalten;  // Taschenbreite je Seitenbreite
  const k = 2 * INNEN + zeilen * (tb / KARTE) + (zeilen - 1) * LUECKE;  // Seitenhöhe je Seitenbreite
  const rand = z.breit ? RAND : RAND_EINZEL;
  const gesamtB = z.breit ? 2 + 2 * RAND : 1 + RAND_RINGE + RAND_EINZEL;
  const sb = Math.max(60, Math.floor(Math.min(vb / gesamtB, vh / (k + 2 * rand))));
  z.sb = sb;
  const stil = z.binderEl.style;
  stil.setProperty('--seite-b', `${sb}px`);
  stil.setProperty('--seite-h', `${sb * k}px`);
  stil.setProperty('--tasche-b', `${sb * tb}px`);
  stil.setProperty('--rand', `${sb * rand}px`);
  stil.setProperty('--rand-links', `${sb * (z.breit ? RAND : RAND_RINGE)}px`);
  stil.setProperty('--binder-b', `${sb * gesamtB}px`);
  stil.setProperty('--binder-h', `${sb * (k + 2 * rand)}px`);
  z.sizes = `${Math.ceil(sb * tb)}px`;
  z.binderEl.querySelectorAll('img[sizes]').forEach((img) => { img.sizes = z.sizes; });
}

/** Zeigt die Seiten des aktuellen Index. */
function fuellen() {
  if (!z.binderEl) return;
  if (z.breit) {
    const n = { links: seiteBauen(linksVon(z.index)), rechts: seiteBauen(rechtsVon(z.index)) };
    z.fl.links.replaceChildren(n.links);
    z.fl.rechts.replaceChildren(n.rechts);
    z.kn = n;
  } else {
    const n = { einzel: seiteBauen(z.index) };
    z.fl.einzel.replaceChildren(n.einzel);
    z.kn = n;
  }
  leisteAktualisieren();
}

function leisteAktualisieren() {
  const ersteSeite = z.breit ? z.index <= 0 : z.index <= 1;
  const letzte = z.index >= maxIndex();
  const nav = z.nav;
  nav.querySelector('[data-blatt="-1"]').disabled = ersteSeite;
  nav.querySelector('[data-blatt="1"]').disabled = letzte;
  if (z.breit && z.binderEl) {
    z.binderEl.querySelector('.tisch-ecke-l').hidden = ersteSeite;
    z.binderEl.querySelector('.tisch-ecke-r').hidden = letzte;
  }
  const sichtbar = seitenVon(z.index);
  // Ohne Kartenbilder (nur Leerseiten oder Seiten ohne Erkennung) entfällt der Umschalter der Bildart.
  const bildart = z.leiste.querySelector('.tisch-bildart');
  if (bildart) bildart.hidden = sichtbar.every((n) => ohneBilder(n));
  z.nav.querySelector('.tisch-seitenzahl').textContent = !sichtbar.length ? '' : sichtbar.length > 1
    ? `Seiten ${sichtbar[0]}–${sichtbar[1]} von ${z.N}` : `Seite ${sichtbar[0]} von ${z.N}`;
}

// ---------- Seiten und Taschen ----------

/** Seite ohne Kartenbilder: Leerseite oder Foto, dessen Erkennung noch aussteht bzw. fehlgeschlagen ist. */
function ohneBilder(n) {
  const s = z.info.get(n);
  return !s || s.art === 'leer' || (OHNE_TASCHEN.has(s.status) && !z.taschen.get(n)?.size);
}

/** Text der Tafel über einer Fotoseite ohne Taschen. */
function tafelText(s) {
  if (s.status === 'fehler') return `Fehler: ${s.fehler || 'Die Erkennung hat nicht geklappt.'}`;
  if (z.binder.verarbeiter && z.binder.verarbeiter.aktiv === false) return null;  // eigener Hinweis mit Befehl
  return s.status === 'in_verarbeitung' ? 'Wird erkannt …' : 'Wartet auf die Erkennung';
}

function tafelBauen(s) {
  const tafel = el('div', `tisch-tafel tisch-tafel-${s.status}`);
  const text = tafelText(s);
  if (s.status === 'fehler') {
    tafel.append(el('p', 'tisch-tafel-text', esc(text)));
    tafel.firstElementChild.title = text;
    if (angemeldet()) {
      const knopf = el('button', 'knopf', 'Erneut versuchen');
      knopf.type = 'button';
      knopf.dataset.erneut = s.foto_id;
      tafel.append(knopf, el('p', 'meldung'));
      tafel.lastElementChild.setAttribute('role', 'alert');
    }
  } else if (text === null) {
    tafel.classList.add('tisch-tafel-still');
    tafel.append(el('p', 'tisch-tafel-text',
      'Erkennung nicht aktiv – am PC <code>Starten.bat</code> doppelklicken'));
  } else {
    tafel.append(el('p', 'tisch-tafel-text',
      `${s.status === 'in_verarbeitung' ? '<i class="tisch-puls" aria-hidden="true"></i>' : ''}${esc(text)}`));
  }
  if (s.dateiname) tafel.append(el('p', 'etikett tisch-tafel-name', esc(s.dateiname)));
  return tafel;
}

function platzBauen(zeile, spalte) {
  const tasche = el('div', 'tisch-tasche tisch-tasche-platz', '<span class="tisch-platzkarte"></span>');
  tasche.style.gridRow = zeile;
  tasche.style.gridColumn = spalte;
  return tasche;
}

function seiteBauen(n, eifrig = false) {
  const seite = el('div', 'tisch-seite');
  seite._sig = sigVon(n);
  if (n < 1 || n > z.N) {  // Innenseite des Einbands
    seite.classList.add('tisch-vorsatz');
    seite.setAttribute('aria-hidden', 'true');
    return seite;
  }
  seite.dataset.seite = n;
  seite.setAttribute('role', 'group');
  const info = z.info.get(n);
  const taschen = z.taschen.get(n);
  const leer = info?.art === 'leer';
  const status = !leer && info && OHNE_TASCHEN.has(info.status) && !taschen?.size ? info.status : null;
  let beschriftung = `Seite ${n}`;
  if (leer) beschriftung += ', Leerseite';
  if (status) beschriftung += `, ${status === 'fehler' ? 'Fehler bei der Erkennung' : 'Erkennung steht aus'}`;
  seite.setAttribute('aria-label', beschriftung);
  if (status) seite.dataset.status = status;
  for (let zeile = 1; zeile <= z.binder.zeilen; zeile++) {
    for (let spalte = 1; spalte <= z.binder.spalten; spalte++) {
      seite.append(status ? platzBauen(zeile, spalte)
        : tascheBauen(taschen?.get(`${zeile}-${spalte}`), n, zeile, spalte, eifrig));
    }
  }
  if (leer) seite.append(el('span', 'etikett tisch-leerseite', 'Leerseite'));
  if (status) seite.append(tafelBauen(info));
  return seite;
}

function zeichenText(k) {
  if (k.markierung === 'unsicher') return k.geprueft ? 'Erkennung unsicher – von dir geprüft' : 'Erkennung unsicher';
  if (k.markierung === 'druck_offen') return k.geprueft ? 'Druck unsicher – von dir geprüft' : 'Name sicher, Druck offen';
  return '';
}

/** Kleines rundes Abzeichen an der oberen rechten Kartenecke (art: 'unsicher' oder 'druck_offen'). */
function abzeichen(art, geprueft) {
  const zeichen = geprueft ? SYM_HAKEN : art === 'druck_offen' ? 'i' : '?';
  const e = el('span', `tisch-abz tisch-abz-${art}${geprueft ? ' tisch-abz-geprueft' : ''}`, zeichen);
  e.setAttribute('aria-hidden', 'true');
  return e;
}
function tascheBauen(t, seite, zeile, spalte, eifrig) {
  const k = t?.karte ?? null;
  const typ = k ? 'karte' : t?.inhalt === 'rueckseite' ? 'rueckseite' : t?.inhalt === 'leer' || !t ? 'leer' : 'unsicher';
  const tasche = el('div', `tisch-tasche tisch-tasche-${typ}`);
  tasche.style.gridRow = zeile;
  tasche.style.gridColumn = spalte;
  if (typ === 'leer') {
    tasche.setAttribute('role', 'img');
    tasche.setAttribute('aria-label', 'Leere Tasche');
    return tasche;
  }
  if (typ === 'karte') {
    const knopf = el('button', 'tisch-karte');
    knopf.type = 'button';
    knopf.dataset.karte = k.id;
    knopf.dataset.seite = seite;
    const zeichen = zeichenText(k);
    const ort = [k.set ? String(k.set).toUpperCase() : '', k.nummer ?? ''].filter(Boolean).join(' ');
    const menge = k.anzahl > 1 ? `${k.anzahl} Stück` : '';
    knopf.setAttribute('aria-label', [k.name, ort, menge, zeichen].filter(Boolean).join(', '));
    knopf.title = zeichen ? `${k.name} – ${zeichen}` : k.name;
    if (k.preis) knopf.setAttribute('aria-label', `${knopf.getAttribute('aria-label')}, ${preisText(k.preis)}`);
    knopf.append(bildBauen(t, k, eifrig));
    knopf.insertAdjacentHTML('beforeend', preisStreifen(k.preis));   // unteres Sechzehntel der Karte
    tasche.append(knopf);
    if (k.markierung === 'unsicher' || k.markierung === 'druck_offen') tasche.append(abzeichen(k.markierung, k.geprueft));
    if (k.anzahl > 1) tasche.append(el('span', 'tisch-anzahl', `×${k.anzahl}`));
    return tasche;
  }
  tasche.setAttribute('role', 'img');
  tasche.setAttribute('aria-label', typ === 'rueckseite' ? 'Kartenrückseite' : 'Tasche unklar, Erkennung unsicher');
  const flaeche = el('div', 'tisch-foto');
  flaeche.append(bildBauen(t, null, eifrig));
  tasche.append(flaeche);
  if (typ === 'rueckseite') tasche.append(el('span', 'tisch-etikett', 'Rückseite'));
  else tasche.append(abzeichen('unsicher', false));
  return tasche;
}

// ---------- Bilder ----------

/** Bildquellen in der Reihenfolge der Vorliebe, ohne fehlende: [[art, schluessel], …] */
function quellen(t, k) {
  const foto = t?.ausschnitt ? ['foto', t.ausschnitt] : null;
  const scryfall = k?.bild ? ['scryfall', k.bild] : null;
  return (z.art === 'foto' ? [foto, scryfall] : [scryfall, foto]).filter(Boolean);
}

function bildBauen(t, k, eifrig) {
  const img = new Image();
  img.alt = '';
  img.decoding = 'async';
  img.loading = eifrig ? 'eager' : 'lazy';
  img.draggable = false;
  img._t = t;
  img._k = k;
  img.addEventListener('error', () => quelleSetzen(img, (img._i ?? 0) + 1));
  quelleSetzen(img, 0);
  return img;
}

function quelleSetzen(img, i) {
  const q = quellen(img._t, img._k)[i];
  if (!q) {  // weder Scryfall-Bild noch eigenes Foto: schlichte Kartenfläche mit Namen
    const k = img._k;
    img.replaceWith(el('div', 'tisch-ersatz', k
      ? `<span class="tisch-ersatz-name">${esc(k.name)}</span><span>${esc([k.set?.toUpperCase(), k.nummer].filter(Boolean).join(' '))}</span>`
      : ''));
    return;
  }
  img._i = i;
  img.sizes = z.sizes;
  img.srcset = bildSrcset(q[0], q[1]);
  img.src = bildUrl(q[0], q[1], 400);
}

function artWaehlen(art) {
  if (!z || art === z.art) return;
  z.art = art;
  vorliebe('bildart', art);
  z.leiste.querySelectorAll('[data-art]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.art === art)));
  z.vor.clear();
  z.binderEl?.querySelectorAll('.tisch-tasche img').forEach((img) => quelleSetzen(img, 0));
  vorladen(1);
}

/** Lädt die Bilder der Nachbarseiten vor (abstand = wie viele Seiten bzw. Doppelseiten entfernt). */
function vorladen(abstand) {
  if (!z.N) return;
  for (const d of [-abstand, abstand]) {
    const i = z.index + d;
    if (i < minIndex() || i > maxIndex()) continue;
    for (const n of seitenVon(i)) {
      for (const t of z.taschen.get(n)?.values() ?? []) {
        const q = quellen(t, t.karte ?? null)[0];
        if (!q) continue;
        const url = bildUrl(q[0], q[1], 400);
        if (z.vor.has(url)) continue;
        const bild = new Image();
        bild.sizes = z.sizes;
        bild.srcset = bildSrcset(q[0], q[1]);
        bild.src = url;
        z.vor.set(url, bild);
      }
    }
  }
  while (z.vor.size > 80) z.vor.delete(z.vor.keys().next().value);
}

/** Wartet kurz, bis die Bilder neuer Seiten entschlüsselt sind, damit beim Blättern nichts aufpoppt. */
function bereit(knoten) {
  const bilder = knoten.flatMap((k) => [...k.querySelectorAll('img')]);
  return Promise.race([Promise.all(bilder.map((b) => b.decode().catch(() => {}))), warten(160)]);
}

// ---------- Blättern ----------

function blaettern(neu, schreiben = true) {
  if (!z || !z.N) return Promise.resolve();
  neu = Math.min(Math.max(neu, minIndex()), maxIndex());
  z.lauf?.beenden();  // laufendes Blättern sofort abschließen
  if (neu === z.index) return Promise.resolve();
  const von = z.index;
  const zustand = z;
  z.index = neu;
  if (schreiben) {
    z.seite = seiteVon(neu);
    gehe(`#/binder/${z.id}/${z.seite}`, { ersetzen: true });
  }
  leisteAktualisieren();
  if (bewegungReduziert()) {
    fuellen();
    vorladen(1);
    return Promise.resolve();
  }
  return (z.breit ? blattDoppel(von, neu) : blattEinzel(von, neu)).then(() => {
    if (z === zustand) vorladen(1);
  });
}

/** Ein laufendes Blättern. `aufraeumen` stellt den Endzustand im DOM her (auch bei Abbruch). */
function laufAnlegen(aufraeumen) {
  const lauf = {
    fertig: false,
    anims: [],
    beenden() {
      if (lauf.fertig) return;
      lauf.fertig = true;
      lauf.anims.forEach((a) => a.cancel());
      aufraeumen();
      if (z && z.lauf === lauf) {
        z.lauf = null;
        queueMicrotask(anwendenFalls);  // neue Daten, die auf das Ende des Blätterns gewartet haben
      }
    },
    async ende() {
      await Promise.all(lauf.anims.map((a) => a.finished)).catch(() => {});
      lauf.beenden();
    },
  };
  z.lauf = lauf;
  return lauf;
}

function blattBauen(einzel) {
  const wurzel = el('div', 'tisch-blatt', einzel
    ? '<div class="tisch-blatt-vorn"><span class="tisch-dunkel"></span></div>'
    : '<div class="tisch-blatt-vorn"><span class="tisch-dunkel"></span></div>'
      + '<div class="tisch-blatt-hinten"><span class="tisch-dunkel"></span></div>');
  const vorn = wurzel.firstElementChild;
  const hinten = einzel ? null : wurzel.lastElementChild;
  return { wurzel, vorn, hinten, dVorn: vorn.lastElementChild, dHinten: hinten?.lastElementChild };
}

const LINEAR = { easing: 'linear', fill: 'forwards' };

/** Schatten der Blattfläche: die zuerst sichtbare Seite dunkelt ab, die zweite hellt wieder auf. */
function blattSchatten(lauf, b, dauer, vor) {
  const [erste, zweite] = vor ? [b.dVorn, b.dHinten] : [b.dHinten, b.dVorn];
  lauf.anims.push(
    erste.animate([{ opacity: 0, offset: 0 }, { opacity: 0.6, offset: 0.5 }, { opacity: 0.6, offset: 1 }], { duration: dauer, ...LINEAR }),
    zweite.animate([{ opacity: 0.6, offset: 0 }, { opacity: 0.6, offset: 0.5 }, { opacity: 0, offset: 1 }], { duration: dauer, ...LINEAR }),
  );
}

/** PC: Das rechte Blatt dreht sich um den Rücken; Vorderseite = rechte Seite, Rückseite = nächste linke Seite. */
async function blattDoppel(von, nach) {
  const vor = nach > von;
  const zustand = z;
  const fl = z.fl;
  const neu = { links: seiteBauen(linksVon(nach), true), rechts: seiteBauen(rechtsVon(nach), true) };
  const b = blattBauen(false);
  const lauf = laufAnlegen(() => {
    fl.links.replaceChildren(neu.links);
    fl.rechts.replaceChildren(neu.rechts);
    zustand.kn = neu;
    b.wurzel.remove();
  });
  await bereit([neu.links, neu.rechts]);
  if (lauf.fertig) return;
  const alt = zustand.kn;
  if (vor) {
    b.vorn.prepend(alt.rechts);
    b.hinten.prepend(neu.links);
    fl.rechts.replaceChildren(neu.rechts);  // darunter liegt schon die neue rechte Seite
  } else {
    b.vorn.prepend(neu.rechts);
    b.hinten.prepend(alt.links);
    fl.links.replaceChildren(neu.links);    // darunter liegt schon die neue linke Seite
  }
  const [start, ende] = vor ? [0, -180] : [-180, 0];
  b.wurzel.style.transform = `rotateY(${start}deg)`;
  b.wurzel.style.willChange = 'transform';
  zustand.auf.insertBefore(b.wurzel, zustand.ruecken);
  const D = DAUER_DOPPEL;
  lauf.anims.push(b.wurzel.animate(
    [{ transform: `rotateY(${start}deg)` }, { transform: `rotateY(${ende}deg)` }],
    { duration: D, easing: KURVE, fill: 'forwards' }));
  blattSchatten(lauf, b, D, vor);
  const [erst, zweit] = vor ? [zustand.wurf.r, zustand.wurf.l] : [zustand.wurf.l, zustand.wurf.r];
  lauf.anims.push(
    erst.animate([{ opacity: 0, offset: 0 }, { opacity: 0.3, offset: 0.35 }, { opacity: 0, offset: 0.62 }, { opacity: 0, offset: 1 }],
      { duration: D, ...LINEAR }),
    zweit.animate([{ opacity: 0, offset: 0 }, { opacity: 0, offset: 0.4 }, { opacity: 0.3, offset: 0.75 }, { opacity: 0, offset: 1 }],
      { duration: D, ...LINEAR }));
  await lauf.ende();
}

/** Handy: Die Seite klappt um ihren linken Rand (die Ringe) weg bzw. wieder zurück. */
async function blattEinzel(von, nach) {
  const vor = nach > von;
  const zustand = z;
  const fl = z.fl;
  const neu = { einzel: seiteBauen(nach, true) };
  const b = blattBauen(true);
  const lauf = laufAnlegen(() => {
    fl.einzel.replaceChildren(neu.einzel);
    zustand.kn = neu;
    b.wurzel.remove();
  });
  await bereit([neu.einzel]);
  if (lauf.fertig) return;
  // Die Seite bleibt beim Wegkippen erst deckend und blendet erst gegen Ende aus (kein Geisterbild).
  const weg = { transform: 'rotateY(-112deg)', opacity: 0 };
  const da = { transform: 'rotateY(0deg)', opacity: 1 };
  const kippen = vor
    ? [{ ...da, offset: 0 }, { opacity: 1, offset: 0.55 }, { ...weg, offset: 1 }]
    : [{ ...weg, offset: 0 }, { opacity: 1, offset: 0.45 }, { ...da, offset: 1 }];
  if (vor) {
    b.vorn.prepend(zustand.kn.einzel);
    fl.einzel.replaceChildren(neu.einzel);
  } else {
    b.vorn.prepend(neu.einzel);
  }
  b.wurzel.style.willChange = 'transform, opacity';
  zustand.auf.insertBefore(b.wurzel, zustand.ruecken);
  const D = DAUER_EINZEL;
  lauf.anims.push(
    b.wurzel.animate(kippen,
      { duration: D, easing: vor ? 'cubic-bezier(0.4, 0, 0.8, 0.6)' : 'cubic-bezier(0.2, 0.4, 0.4, 1)', fill: 'forwards' }),
    b.dVorn.animate(vor ? [{ opacity: 0 }, { opacity: 0.55 }] : [{ opacity: 0.55 }, { opacity: 0 }],
      { duration: D, ...LINEAR }));
  await lauf.ende();
}

// ---------- Wischen: Die Seite folgt dem Finger (Handy) ----------

/** Lage des Blatts: f = 0 liegt auf dem Stapel, f = 1 ist ganz weggeklappt. */
function blattLage(f) {
  return { transform: `rotateY(${(-112 * f).toFixed(2)}deg)`, opacity: f < 0.75 ? 1 : Math.max(0, (1 - f) / 0.25) };
}

function zugStarten(vor) {
  const zustand = z;
  const nach = z.index + (vor ? 1 : -1);
  const zug = { vor, nach, f: vor ? 0 : 1, ziel: false, rand: nach < minIndex() || nach > maxIndex(), bild: 0, dx: 0 };
  if (zug.rand) return zug;  // am Anfang oder Ende: nur ein leichtes Nachgeben
  const alt = zustand.kn;
  const neu = { einzel: seiteBauen(nach, true) };
  const b = blattBauen(true);
  zug.b = b;
  zug.lauf = laufAnlegen(() => {
    const bleibt = zug.ziel ? neu : alt;
    zustand.fl.einzel.replaceChildren(bleibt.einzel);
    zustand.kn = bleibt;
    b.wurzel.style.willChange = '';
    b.wurzel.remove();
  });
  if (vor) {
    b.vorn.prepend(alt.einzel);
    zustand.fl.einzel.replaceChildren(neu.einzel);
  } else {
    b.vorn.prepend(neu.einzel);
  }
  zugZeichnen(zug);
  b.wurzel.style.willChange = 'transform, opacity';
  zustand.auf.insertBefore(b.wurzel, zustand.ruecken);
  return zug;
}

function zugZeichnen(zug) {
  const l = blattLage(zug.f);
  zug.b.wurzel.style.transform = l.transform;
  zug.b.wurzel.style.opacity = l.opacity;
  zug.b.dVorn.style.opacity = 0.55 * zug.f;
}

function zugBewegen(zug, dx) {
  zug.dx = dx;
  if (zug.rand) {
    if (!zug.bild) zug.bild = requestAnimationFrame(() => {
      zug.bild = 0;
      if (z) z.auf.style.transform = `translate3d(${(zug.dx * 0.22).toFixed(1)}px, 0, 0)`;
    });
    return;
  }
  if (zug.lauf.fertig) return;
  const p = Math.min(Math.max((zug.vor ? -dx : dx) / (z.sb * 0.8), 0), 1);
  zug.f = zug.vor ? p : 1 - p;
  if (!zug.bild) zug.bild = requestAnimationFrame(() => { zug.bild = 0; if (!zug.lauf.fertig) zugZeichnen(zug); });
}

/** Loslassen: über der Schwelle blättert die Seite zu Ende, sonst federt sie zurück. */
async function zugLoslassen(zug, dx) {
  const zustand = z;
  cancelAnimationFrame(zug.bild);
  zug.bild = 0;
  if (zug.rand) {
    const alt = zug.dx * 0.22;
    zustand.auf.style.transform = '';
    if (alt) zustand.auf.animate([{ transform: `translate3d(${alt}px, 0, 0)` }, { transform: 'translate3d(0, 0, 0)' }],
      { duration: 220, easing: KURVE });
    return;
  }
  const lauf = zug.lauf;
  if (lauf.fertig) return;
  zugZeichnen(zug);
  const erfolg = Math.abs(dx) >= SCHWELLE && (zug.vor ? dx < 0 : dx > 0);
  zug.ziel = erfolg;
  const ziel = erfolg ? (zug.vor ? 1 : 0) : (zug.vor ? 0 : 1);
  if (erfolg) {
    zustand.index = zug.nach;
    zustand.seite = seiteVon(zug.nach);
    gehe(`#/binder/${zustand.id}/${zustand.seite}`, { ersetzen: true });
    leisteAktualisieren();
  }
  const dauer = Math.max(140, Math.round(DAUER_EINZEL * Math.abs(ziel - zug.f)));
  const opt = { duration: dauer, easing: erfolg ? 'cubic-bezier(0.3, 0.2, 0.4, 1)' : KURVE, fill: 'forwards' };
  const von = blattLage(zug.f);
  const nach = blattLage(ziel);
  lauf.anims.push(
    zug.b.wurzel.animate([von, nach], opt),
    zug.b.dVorn.animate([{ opacity: 0.55 * zug.f }, { opacity: 0.55 * ziel }], { ...opt, easing: 'linear' }));
  await lauf.ende();
  if (erfolg && z === zustand) vorladen(1);
}

// ---------- Öffnen und Schließen des Binders ----------

function deckel() {
  return el('div', 'tisch-deckel', `<span class="tisch-deckel-titel">${esc(z.binder.name)}</span>`);
}

/** Der Binder legt sich auf den Tisch: gleitet von oben herein, der Einband klappt auf. */
async function oeffnen() {
  const zustand = z;
  const b = blattBauen(!zustand.breit);
  b.vorn.prepend(deckel());
  let aufraeumen;
  if (zustand.breit) {
    b.hinten.prepend(zustand.kn.links);  // die linke Seite liegt auf der Rückseite des Einbands
    aufraeumen = () => { zustand.fl.links.replaceChildren(zustand.kn.links); b.wurzel.remove(); };
  } else {
    aufraeumen = () => b.wurzel.remove();
  }
  const lauf = laufAnlegen(() => { aufraeumen(); zustand.tisch.classList.remove('tisch-spielt'); });
  zustand.tisch.classList.add('tisch-spielt');  // Blattecken sind währenddessen verborgen
  b.wurzel.style.willChange = 'transform';
  zustand.auf.insertBefore(b.wurzel, zustand.ruecken);
  const hinein = { duration: 360, easing: 'cubic-bezier(0.2, 0.7, 0.3, 1)', fill: 'both' };
  lauf.anims.push(
    zustand.binderEl.animate([{ transform: 'translate3d(0, -64px, 0)', opacity: 0 }, { transform: 'translate3d(0, 0, 0)', opacity: 1 }], hinein));
  if (zustand.breit) {
    const D = 600;
    const auf = { delay: 300, duration: D, fill: 'both' };
    b.wurzel.style.transform = 'rotateY(0deg)';
    lauf.anims.push(
      zustand.auf.animate([{ transform: 'translate3d(-25%, 0, 0)' }, { transform: 'translate3d(0, 0, 0)' }], { ...auf, easing: KURVE }),
      b.wurzel.animate([{ transform: 'rotateY(0deg)' }, { transform: 'rotateY(-180deg)' }], { ...auf, easing: KURVE }),
      zustand.ruecken.animate(
        [{ opacity: 0, offset: 0 }, { opacity: 0, offset: 0.3 }, { opacity: 1, offset: 0.6 }, { opacity: 1, offset: 1 }],
        { ...auf, easing: 'linear' }),
      zustand.auf.querySelector('.tisch-halbe-l').animate(
        [{ opacity: 0, offset: 0 }, { opacity: 0, offset: 0.55 }, { opacity: 1, offset: 0.72 }, { opacity: 1, offset: 1 }],
        { ...auf, easing: 'linear' }));
    const erst = b.dVorn.animate([{ opacity: 0, offset: 0 }, { opacity: 0.6, offset: 0.5 }, { opacity: 0.6, offset: 1 }], { ...auf, easing: 'linear' });
    const zweit = b.dHinten.animate([{ opacity: 0.6, offset: 0 }, { opacity: 0.6, offset: 0.5 }, { opacity: 0, offset: 1 }], { ...auf, easing: 'linear' });
    lauf.anims.push(erst, zweit);
  } else {
    lauf.anims.push(
      b.wurzel.animate([{ transform: 'rotateY(0deg)', opacity: 1, offset: 0 }, { opacity: 1, offset: 0.6 },
        { transform: 'rotateY(-112deg)', opacity: 0, offset: 1 }],
      { delay: 260, duration: 440, easing: 'cubic-bezier(0.4, 0, 0.7, 0.7)', fill: 'both' }));
  }
  await lauf.ende();
}

/** Der Binder wird ruhig kleiner und blendet aus (kein Einband über der Doppelseite). */
async function schliessen(zustand) {
  if (!zustand.binderEl) return;
  zustand.tisch.classList.add('tisch-spielt');
  const opt = { duration: 300, easing: KURVE, fill: 'both' };
  const anims = [
    zustand.binderEl.animate([{ transform: 'scale(1)' }, { transform: 'scale(0.94)' }], opt),
    zustand.tisch.animate([{ opacity: 1 }, { opacity: 0 }], { ...opt, easing: 'linear' }),
  ];
  // Der Endzustand bleibt stehen (fill), bis der Wegweiser die Ansicht abräumt.
  await Promise.all(anims.map((a) => a.finished)).catch(() => {});
}
// ---------- Eingaben ----------

function ereignisse(zustand) {
  const tisch = zustand.tisch;
  const an = (ziel, name, fn, opt) => {
    ziel.addEventListener(name, fn, opt);
    zustand.abbau.push(() => ziel.removeEventListener(name, fn, opt));
  };

  an(tisch, 'click', (e) => {
    if (!z) return;
    if (e.target.closest('.tisch-zurueck')) return zurRegal();
    const erneut = e.target.closest('[data-erneut]');
    if (erneut) return void erneutVersuchen(erneut);
    if (e.target.closest('[data-fotos]')) return void fotosHinzufuegen();
    if (e.target.closest('.tisch-seiten')) return void verwaltungOeffnen();
    const art = e.target.closest('[data-art]');
    if (art) return artWaehlen(art.dataset.art);
    const blatt = e.target.closest('[data-blatt]');
    if (blatt) return void blaettern(z.index + Number(blatt.dataset.blatt));
    const karte = e.target.closest('[data-karte]');
    // Kein Antippen während des Blätterns oder direkt nach einer Wischgeste.
    if (karte && !z.lauf && performance.now() - z.gewischt > 450) {
      gehe(`#/binder/${z.id}/${karte.dataset.seite}/karte/${karte.dataset.karte}`);
    }
  });

  // Pfeiltasten blättern, Esc geht zurück ins Regal. Im Fang-Durchlauf geprüft, damit eine
  // offene Karte (die selbst auf Esc reagiert) nicht gleichzeitig den Binder verlässt.
  an(window, 'keydown', (e) => {
    if (!z || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    if (dialogOffen()) return;  // Esc und Pfeiltasten gehören dem Dialog
    if (e.target instanceof Element && e.target.closest('input, textarea, select, [contenteditable]')) return;
    if (karteOffen()) return;
    if (e.key === 'Escape') { e.preventDefault(); zurRegal(); return; }
    const schritt = { ArrowRight: 1, PageDown: 1, ArrowLeft: -1, PageUp: -1 }[e.key];
    const rand = { Home: minIndex(), End: maxIndex() }[e.key];
    if (schritt === undefined && rand === undefined) return;
    if (z.geht || (e.repeat && z.lauf)) { e.preventDefault(); return; }
    e.preventDefault();
    blaettern(schritt === undefined ? rand : z.index + schritt);
  }, true);

  // Wischen (nur eine Seite): Die Seite folgt dem Finger; waagrecht vorherrschend, senkrechtes
  // Scrollen bleibt frei (touch-action: pan-y). Mit „Bewegung reduzieren" zählt nur das Ergebnis.
  let start = null;
  let zug = null;
  const buehne = zustand.buehne;
  an(buehne, 'pointerdown', (e) => {
    if (!z || z.breit || !e.isPrimary || (e.pointerType === 'mouse' && e.button !== 0)) return;
    z.beruehrt = performance.now();  // neue Daten warten, bis der Finger wieder oben ist
    z.lauf?.beenden();
    start = { x: e.clientX, y: e.clientY };
  });
  const losgelassen = () => { if (z) { z.beruehrt = 0; anwendenFalls(); } };
  an(buehne, 'pointermove', (e) => {
    if (!start || !z) return;
    const dx = e.clientX - start.x;
    const dy = e.clientY - start.y;
    if (!zug) {
      if (Math.abs(dy) > 10 && Math.abs(dy) > Math.abs(dx)) { start = null; losgelassen(); return; }
      if (bewegungReduziert() || Math.abs(dx) < 8 || Math.abs(dx) < Math.abs(dy) * 1.2) return;
      zug = zugStarten(dx < 0);
    }
    zugBewegen(zug, dx);
  });
  an(buehne, 'pointerup', (e) => {
    if (!start || !z) return;
    const dx = e.clientX - start.x;
    const dy = e.clientY - start.y;
    start = null;
    if (zug) {
      const g = zug;
      zug = null;
      z.gewischt = performance.now();
      zugLoslassen(g, dx);
    } else if (Math.abs(dx) >= SCHWELLE && Math.abs(dx) > Math.abs(dy) * 1.5) {
      z.gewischt = performance.now();
      blaettern(z.index + (dx < 0 ? 1 : -1));
    }
    losgelassen();
  });
  an(buehne, 'pointercancel', () => {
    start = null;
    if (zug && z) { const g = zug; zug = null; zugLoslassen(g, 0); }
    losgelassen();
  });

  // Aktualisieren: sofort bei Änderungen (Upload, Leerseiten, Umbenennen) und wenn sich die Anmeldung ändert.
  an(window, 'binder:geaendert', (e) => {
    if (z === zustand && e.detail?.binderId === zustand.id) laden();
  });
  an(window, 'binder:sitzung', sitzungGeaendert);

  // Größe ändern oder Drehen: neu berechnen, bei Wechsel über die 900-px-Grenze neu aufbauen.
  const anpassen = () => {
    if (!z || z !== zustand) return;
    if (istBreit() !== z.breit) {
      aufbauen();
      layouten();
      if (z.N) { fuellen(); vorladen(1); }
    } else {
      layouten();
    }
  };
  let bild = 0;
  const verzoegert = () => { cancelAnimationFrame(bild); bild = requestAnimationFrame(anpassen); };
  zustand.abbau.push(() => cancelAnimationFrame(bild));
  if (window.ResizeObserver) {
    const beobachter = new ResizeObserver(verzoegert);
    beobachter.observe(zustand.buehne);
    zustand.abbau.push(() => beobachter.disconnect());
  } else {
    an(window, 'resize', verzoegert);
  }
  const mq = window.matchMedia('(min-width: 900px) and (min-aspect-ratio: 1/1)');
  if (mq.addEventListener) an(mq, 'change', anpassen);
}

// ---------- Fotos und Seitenverwaltung (nur angemeldet) ----------

/** Foto erneut in die Warteschlange der Erkennung legen. */
async function erneutVersuchen(knopf) {
  const zustand = z;
  const meldung = knopf.closest('.tisch-tafel')?.querySelector('.meldung');
  knopf.disabled = true;
  try {
    await senden(`/api/foto/${knopf.dataset.erneut}/erneut`);
    await laden(true);
    geaendert(zustand.id);
  } catch (fehler) {
    if (meldung) meldung.textContent = fehler.message;
    knopf.disabled = false;
  }
}

/** Upload-Dialog für diesen Binder (hinten anhängen); danach neu laden. */
async function fotosHinzufuegen(ziel = { nach: null }) {
  const zustand = z;
  if (!zustand) return;
  const hochladen = await import('./hochladen.js');  // erst bei Bedarf laden
  const ergebnis = await hochladen.oeffnen({ binderId: zustand.id, ...ziel });
  if (ergebnis && z === zustand) {
    await laden(true);
    geaendert(zustand.id);
  }
}

/** Seite, auf die sich „Vor/Nach Seite N“ bezieht: am PC die rechte sichtbare Seite, sonst die linke. */
function bezugsSeite() {
  const sichtbar = seitenVon(z.index);
  if (!sichtbar.length) return null;
  return z.breit ? sichtbar[sichtbar.length - 1] : sichtbar[0];
}

const leerSichtbar = () => seitenVon(z.index).map((n) => z.info.get(n)).filter((s) => s?.art === 'leer');

function zeitText(zeitpunkt) {
  const d = new Date(zeitpunkt);
  return Number.isNaN(d.getTime()) ? String(zeitpunkt ?? '')
    : d.toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' });
}

function verwaltungOeffnen() {
  if (!z || z.dlg || !angemeldet()) return;
  const zustand = z;
  const id = zustand.id;
  let anzahl = 1;
  const wurzel = el('div', 'tisch-v', `
    <section class="tisch-v-teil" data-teil="fotos">
      <h3 class="etikett">Fotos</h3>
      <p class="dialog-text">Neue Fotos werden zu Seiten dieses Binders.</p>
      <div class="tisch-v-zeile"><button type="button" class="knopf knopf-haupt" data-v="fotos">Fotos hinzufügen</button></div>
      <p class="meldung" role="alert"></p>
    </section>
    <section class="tisch-v-teil" data-teil="einfuegen">
      <h3 class="etikett">Leerseiten einfügen</h3>
      <label class="etikett feld-etikett" for="tisch-v-stelle">Stelle</label>
      <select id="tisch-v-stelle" class="feld" data-v="stelle"></select>
      <span class="etikett feld-etikett" id="tisch-v-anzahl-name">Anzahl</span>
      <div class="tisch-v-zahl" role="group" aria-labelledby="tisch-v-anzahl-name">
        <button type="button" class="knopf knopf-sym" data-v="weniger" aria-label="Eine weniger">${SYM_MINUS}</button>
        <output class="tisch-v-wert" data-v="anzahl">1</output>
        <button type="button" class="knopf knopf-sym" data-v="mehr" aria-label="Eine mehr">${SYM_PLUS}</button>
      </div>
      <div class="tisch-v-zeile"><button type="button" class="knopf knopf-haupt" data-v="einfuegen">Leerseiten einfügen</button></div>
      <p class="meldung" role="alert"></p>
    </section>
    <section class="tisch-v-teil" data-teil="leer" hidden></section>
    <section class="tisch-v-teil" data-teil="bearbeiten">
      <h3 class="etikett">Binder bearbeiten</h3>
      <label class="etikett feld-etikett" for="tisch-v-name">Name</label>
      <input id="tisch-v-name" class="feld" data-v="name" type="text" maxlength="60" autocomplete="off" spellcheck="false">
      <label class="etikett feld-etikett" for="tisch-v-farbe">Farbe</label>
      <select id="tisch-v-farbe" class="feld" data-v="farbe">${FARBEN.map(([w, t]) => `<option value="${w}">${t}</option>`).join('')}</select>
      <div class="tisch-v-zeile"><button type="button" class="knopf knopf-haupt" data-v="speichern">Speichern</button></div>
      <p class="meldung" role="alert"></p>
    </section>
    <section class="tisch-v-teil" data-teil="loeschen">
      <h3 class="etikett">Binder löschen</h3>
      <p class="dialog-text">Löscht den Binder mit allen Seiten und erkannten Karten. Die Originalfotos bleiben auf dem
        Netzlaufwerk und lassen sich danach neu hochladen. Das lässt sich nicht rückgängig machen.</p>
      <label class="etikett feld-etikett" for="tisch-v-loeschname">Zum Bestätigen den Namen des Binders eintippen</label>
      <input id="tisch-v-loeschname" class="feld" data-v="loeschname" type="text" maxlength="60" autocomplete="off"
        spellcheck="false">
      <div class="tisch-v-zeile"><button type="button" class="knopf knopf-gefahr" data-v="loeschen" disabled>Binder löschen</button></div>
      <p class="meldung" role="alert"></p>
    </section>
    <section class="tisch-v-teil" data-teil="verlauf">
      <h3 class="etikett">Verlauf</h3>
      <ol class="tisch-v-verlauf" aria-label="Verlauf der Änderungen"></ol>
      <p class="meldung" role="alert"></p>
    </section>`);
  const d = dialog({ titel: 'Seiten', klasse: 'tisch-dialog', inhalt: wurzel, voll: true });
  const fertig = el('button', 'knopf', 'Fertig');
  fertig.type = 'button';
  fertig.addEventListener('click', () => d.schliessen());
  d.fuss.append(fertig);
  zustand.dlg = d;

  const teil = (name) => wurzel.querySelector(`[data-teil="${name}"]`);
  const feld = (name) => wurzel.querySelector(`[data-v="${name}"]`);
  const sagen = (name, text = '', gut = false) => {
    const m = teil(name).querySelector('.meldung');
    m.textContent = text;
    m.classList.toggle('tisch-v-gut', gut);
    m.setAttribute('role', gut ? 'status' : 'alert');
  };
  feld('name').value = zustand.binder.name;
  feld('farbe').value = zustand.binder.farbe || 'schwarz';
  // Erste Bestätigung: der genaue Name; zweite: die Rückfrage beim Klick
  feld('loeschname').addEventListener('input', () => {
    feld('loeschen').disabled = feld('loeschname').value.trim() !== zustand.binder.name;
  });

  const verlaufLaden = async () => {
    try {
      const antwort = await holen(`/api/binder/${id}/verlauf`);
      if (z !== zustand || zustand.dlg !== d) return;
      const liste = teil('verlauf').querySelector('.tisch-v-verlauf');
      const eintraege = (antwort.verlauf ?? []).slice(0, 60);
      liste.replaceChildren(...(eintraege.length ? eintraege.map((v) => el('li', 'tisch-v-eintrag',
        `<span class="etikett">${esc(zeitText(v.zeitpunkt))}</span><span>${esc(v.text)}</span>`))
        : [el('li', 'tisch-v-eintrag', '<span class="tisch-v-leer">Noch keine Änderungen.</span>')]));
      sagen('verlauf');
    } catch (fehler) {
      if (z === zustand) sagen('verlauf', 'Der Verlauf ließ sich nicht laden.');
    }
  };

  /** Stelle, Leerseiten-Abschnitt und Titel an den aktuellen Stand anpassen. */
  const auffrischen = () => {
    if (z !== zustand) return;
    const bezug = bezugsSeite();
    const stelle = feld('stelle');
    const alt = stelle.value || 'nach';
    stelle.replaceChildren(...[
      ...(bezug ? [['vor', `Vor Seite ${bezug}`], ['nach', `Nach Seite ${bezug}`]] : []),
      ['ende', 'Am Ende'],
    ].map(([w, t]) => Object.assign(el('option'), { value: w, textContent: t })));
    stelle.value = [...stelle.options].some((o) => o.value === alt) ? alt : (bezug ? 'nach' : 'ende');
    const leere = leerSichtbar();
    const abschnitt = teil('leer');
    abschnitt.hidden = !leere.length;
    abschnitt.replaceChildren(...leere.map((s) => el('div', 'tisch-v-leerseite', `
      <h3 class="etikett">Seite ${s.nummer} ist eine Leerseite</h3>
      <div class="tisch-v-zeile">
        <button type="button" class="knopf" data-v="foto" data-seite-id="${s.id}" data-nummer="${s.nummer}">Foto für diese Leerseite</button>
        <button type="button" class="knopf knopf-gefahr" data-v="entfernen" data-seite-id="${s.id}" data-nummer="${s.nummer}">Diese Leerseite entfernen</button>
      </div>
      <p class="meldung" role="alert"></p>`)));
  };
  zustand.dlgAktualisieren = auffrischen;
  d.fertig.then(() => {
    if (z === zustand) { z.dlg = null; z.dlgAktualisieren = null; }
  });
  auffrischen();
  verlaufLaden();

  /** Führt eine Aktion aus: Knopf sperren, Fehler als Satz im Abschnitt zeigen. */
  const ausfuehren = async (knopf, name, arbeit) => {
    knopf.disabled = true;
    sagen(name);
    try {
      await arbeit();
    } catch (fehler) {
      if (z === zustand && zustand.dlg === d) sagen(name, fehler.message);
    } finally {
      knopf.disabled = false;
    }
  };
  const neuLaden = async () => {
    await laden(true);
    geaendert(id);
    auffrischen();
    await verlaufLaden();
  };

  wurzel.addEventListener('click', (e) => {
    const knopf = e.target.closest('[data-v]');
    if (!knopf || z !== zustand) return;
    const was = knopf.dataset.v;
    if (was === 'weniger' || was === 'mehr') {
      anzahl = Math.min(10, Math.max(1, anzahl + (was === 'mehr' ? 1 : -1)));
      feld('anzahl').textContent = String(anzahl);
      wurzel.querySelector('[data-v="weniger"]').disabled = anzahl <= 1;
      wurzel.querySelector('[data-v="mehr"]').disabled = anzahl >= 10;
    } else if (was === 'fotos') {
      d.schliessen();
      fotosHinzufuegen();
    } else if (was === 'foto') {
      d.schliessen();
      fotosHinzufuegen({ seiteId: Number(knopf.dataset.seiteId), seiteNummer: Number(knopf.dataset.nummer) });
    } else if (was === 'einfuegen') {
      ausfuehren(knopf, 'einfuegen', async () => {
        const art = feld('stelle').value;
        const bezug = bezugsSeite();
        const nach = art === 'vor' ? bezug - 1 : art === 'nach' ? bezug : null;
        const antwort = await senden(`/api/binder/${id}/leerseiten`, { daten: { nach, anzahl } });
        // Vor oder nach der aktuellen Seite: die Ansicht springt auf die erste neue Leerseite.
        if (art !== 'ende' && antwort?.seiten?.length) zustand.folgeId = antwort.seiten[0].id;
        await neuLaden();
        sagen('einfuegen', anzahl === 1 ? 'Leerseite eingefügt.' : `${anzahl} Leerseiten eingefügt.`, true);
      });
    } else if (was === 'entfernen') {
      const seiteId = Number(knopf.dataset.seiteId);
      const nummer = Number(knopf.dataset.nummer);
      const abschnitt = knopf.closest('.tisch-v-leerseite');
      const sagenHier = (text) => { const m = abschnitt?.querySelector('.meldung'); if (m) m.textContent = text; };
      bestaetigen({
        titel: 'Leerseite entfernen?',
        text: `Seite ${nummer} ist leer. Danach rücken die folgenden Seiten um eins nach vorn.`,
        ja: 'Entfernen', gefahr: true,
      }).then(async (ja) => {
        if (!ja || z !== zustand || zustand.dlg !== d) return;
        knopf.disabled = true;
        try {
          await senden(`/api/seite/${seiteId}`, { methode: 'DELETE' });
          await neuLaden();
        } catch (fehler) {
          sagenHier(fehler.message);
          knopf.disabled = false;
        }
      });
    } else if (was === 'loeschen') {
      const name = zustand.binder.name;
      bestaetigen({
        titel: 'Binder wirklich löschen?',
        text: `„${name}“ mit ${zustand.binder.seiten?.length ?? 0} Seiten und allen Karten darin wird gelöscht.`,
        ja: 'Endgültig löschen', gefahr: true,
      }).then(async (ja) => {
        if (!ja || z !== zustand || zustand.dlg !== d) return;
        knopf.disabled = true;
        try {
          await senden(`/api/binder/${id}/loeschen`, { daten: { name } });
          zustand.geloescht = true;   // kein Nachladen mehr (der Binder ist weg)
          d.schliessen();
          gehe('#/');   // erst weg vom Lesetisch, sonst lädt er den gelöschten Binder nach
          window.setTimeout(() => geaendert(id), 300);
        } catch (fehler) {
          sagen('loeschen', fehler.message);
          knopf.disabled = false;
        }
      });
    } else if (was === 'speichern') {
      ausfuehren(knopf, 'bearbeiten', async () => {
        const name = feld('name').value.trim();
        const farbe = feld('farbe').value;
        const daten = {};
        if (name !== zustand.binder.name) daten.name = name;
        if (farbe !== zustand.binder.farbe) daten.farbe = farbe;
        if (!name) throw new Error('Bitte einen Namen eingeben.');
        if (!Object.keys(daten).length) { sagen('bearbeiten', 'Nichts geändert.', true); return; }
        await senden(`/api/binder/${id}`, { methode: 'PATCH', daten });
        await neuLaden();
        feld('name').value = zustand.binder.name;
        sagen('bearbeiten', 'Gespeichert.', true);
      });
    }
  });
  wurzel.querySelector('[data-v="weniger"]').disabled = true;
}
