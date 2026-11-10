// Dashboard: die ganze Sammlung auf einer Seite (Wunsch des Nutzers, 06.11.2026). Nur lesend, bis auf die Wahl der
// Preisart (PUT /api/preisart, Paket „Preisart wählbar“, 10.11.2026): Sie gilt für alle Werte der App und Home Assistant.
//
//   #/dashboard            Wert mit Verlauf, Kennzahlen, teuerste Karten, Gewinner und Verlierer, Verteilungen,
//                          Binder und Kisten, Zuwachs, Scans, fast vollständige Sets, kommende Secret-Lair-Drops
//   #/dashboard/karte/12   dazu Karte 12 groß
//
// Daten: /api/dashboard (web/dashboard.py), dazu /api/sets für die Sets. Graphen ohne Bibliothek: Der Verlauf ist ein
// SVG in echter Pixelbreite, Balken und Säulen sind HTML. Alle Verteilungen sind einfarbig (Messing) und tragen ihren
// Namen als Text, die Farbe sagt also nie allein, was gemeint ist; die Punkte bei den Magic-Farben sind nur Beiwerk.

import { holen, senden, fehlerText, esc, gehe, zurueck, euro, bildUrl, scryfallBild, adresse, vorliebe, ausgenommenText } from './gemeinsam.js';
import { dialogOffen } from './dialog.js';
import { laden as sitzungLaden } from './anmeldung.js';

const sym = (d, g = 18) => `<svg viewBox="0 0 24 24" width="${g}" height="${g}" fill="none" stroke="currentColor"`
  + ` stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${d}</svg>`;
const ZURUECK = sym('<path d="M15 5l-7 7 7 7"/>', 20);
const HOCH = sym('<path d="M12 19V5M6 11l6-6 6 6"/>', 14);
const RUNTER = sym('<path d="M12 5v14M6 13l6 6 6-6"/>', 14);
const WEITER = sym('<path d="M9 5l7 7-7 7"/>', 16);

const FARBEN = [['W', 'Weiß'], ['U', 'Blau'], ['B', 'Schwarz'], ['R', 'Rot'], ['G', 'Grün'], ['M', 'Mehrfarbig'],
  ['C', 'Farblos'], ['L', 'Länder']];
const SELTENHEITEN = [['mythic', 'Mythisch selten'], ['rare', 'Selten'], ['uncommon', 'Ungewöhnlich'],
  ['common', 'Gewöhnlich'], ['special', 'Besonders'], ['bonus', 'Bonus']];
const TYPEN = { kreatur: 'Kreaturen', planeswalker: 'Planeswalker', spontanzauber: 'Spontanzauber', hexerei: 'Hexereien',
  artefakt: 'Artefakte', verzauberung: 'Verzauberungen', schlacht: 'Schlachten', land: 'Länder', sonstige: 'Sonstige' };
const SPRACHEN = { de: 'Deutsch', en: 'Englisch', ja: 'Japanisch', fr: 'Französisch', it: 'Italienisch', es: 'Spanisch',
  pt: 'Portugiesisch', ru: 'Russisch', ko: 'Koreanisch', zhs: 'Chinesisch (vereinfacht)', zht: 'Chinesisch (traditionell)' };
const ERKENNUNG = [['geprueft', 'Von dir geprüft'], ['sicher', 'Sicher erkannt'], ['name_sicher', 'Name sicher, Druck offen'],
  ['unsicher', 'Unsicher']];
const ZEITRAEUME = [{ wert: '30', text: '30 Tage' }, { wert: '90', text: '90 Tage' }, { wert: 'alle', text: 'Alles' }];
const ABSTAENDE = [{ wert: 'vortag', text: 'Seit gestern' }, { wert: 'tage3', text: '3 Tage' }, { wert: 'tage7', text: 'Woche' },
  { wert: 'tage30', text: 'Monat' }];
const SORTIERUNGEN = [{ wert: 'euro', text: 'Euro' }, { wert: 'prozent', text: 'Prozent' }];
const SICHTEN = [{ wert: 'karten', text: 'Karten' }, { wert: 'euro', text: 'Wert' }];
const ORTE_MAX = 8;
const SPRACHEN_MAX = 6;

const zahl = (n) => Number(n ?? 0).toLocaleString('de-DE');
const karten = (n) => `${zahl(n)} ${Number(n) === 1 ? 'Karte' : 'Karten'}`;
const betrag = (w) => euro(w, { rund: Math.abs(w ?? 0) >= 1000 });
const mitVorzeichen = (w) => `${w > 0 ? '+' : ''}${euro(w)}`;
const prozentText = (p) => `${p > 0 ? '+' : ''}${Number(p).toLocaleString('de-DE', { maximumFractionDigits: 1 })} %`;
const datum = (tag, art = { day: 'numeric', month: 'long', year: 'numeric' }) => new Date(`${tag}T12:00:00`)
  .toLocaleDateString('de-DE', art);
const kurzDatum = (tag) => datum(tag, { day: 'numeric', month: 'numeric' });
const monat = (m) => new Date(`${m}-15T12:00:00`).toLocaleDateString('de-DE', { month: 'short', year: '2-digit' });

let Z = null;   // Zustand der gezeigten Ansicht

// ---------------------------------------------------------------- Bausteine

function el(tag, klasse, html) {
  const e = document.createElement(tag);
  if (klasse) e.className = klasse;
  if (html !== undefined) e.innerHTML = html;
  return e;
}

function tafel(titel, inhalt, { klasse = '', unter = '' } = {}) {
  return `<section class="dash-tafel ${klasse}"><h2 class="dash-titel">${esc(titel)}</h2>`
    + (unter ? `<p class="dash-unter">${esc(unter)}</p>` : '') + inhalt + '</section>';
}

function pillen(liste, aktuell, name, beschriftung) {
  return `<div class="dash-pillen" role="group" aria-label="${esc(beschriftung)}">`
    + liste.map((x) => `<button type="button" class="knopf dash-pille" data-${name}="${x.wert}"`
      + ` aria-pressed="${x.wert === aktuell}">${esc(x.text)}</button>`).join('') + '</div>';
}

function pillenSetzen(gruppe, knopf) {
  for (const b of gruppe.children) b.setAttribute('aria-pressed', String(b === knopf));
}

/** Veränderung mit Pfeil, Vorzeichen und Betrag: Die Richtung steht nie nur in der Farbe. */
function delta(euroWert, prozent = null) {
  if (euroWert == null) return '<span class="dash-delta">–</span>';
  const art = euroWert > 0 ? ' dash-plus' : euroWert < 0 ? ' dash-minus' : '';
  const pfeil = euroWert > 0 ? HOCH : euroWert < 0 ? RUNTER : '';
  return `<span class="dash-delta${art}">${pfeil}${esc(mitVorzeichen(euroWert))}`
    + (prozent != null ? ` <span class="dash-delta-prozent">(${esc(prozentText(prozent))})</span>` : '') + '</span>';
}

function bildHtml(k, breite, klasse = '') {
  const daten = `data-sid="${esc(k.scryfall_id ?? '')}" data-name="${esc(k.name)}"`;
  const quelle = k.bild ? bildUrl('scryfall', k.bild, breite) : scryfallBild(k.scryfall_id);
  if (!quelle) return `<span class="dash-flaeche ${klasse}">${esc(k.name)}</span>`;
  return `<img class="dash-bild ${klasse}" src="${quelle}" width="63" height="88" alt="" loading="lazy" decoding="async" ${daten}>`;
}

function zahlenTabelle(kopf, zeilen) {
  return '<details class="dash-zahlen"><summary>Zahlen anzeigen</summary><div class="dash-tabelle-rand"><table>'
    + `<thead><tr>${kopf.map((k) => `<th scope="col">${esc(k)}</th>`).join('')}</tr></thead>`
    + `<tbody>${zeilen.map((z) => `<tr>${z.map((w) => `<td>${esc(w)}</td>`).join('')}</tr>`).join('')}</tbody>`
    + '</table></div></details>';
}

// ---------------------------------------------------------------- Verlauf (SVG)

/** Glatte Achsenwerte um min und max. */
function achse(min, max, schritte = 4) {
  if (min === max) { const r = Math.max(Math.abs(min) * 0.01, 1); min -= r; max += r; }
  const roh = (max - min) / schritte;
  const basis = 10 ** Math.floor(Math.log10(roh));
  const schritt = [1, 2, 2.5, 5, 10].map((f) => f * basis).find((s) => s >= roh);
  const unten = Math.floor(min / schritt) * schritt;
  const oben = Math.ceil(max / schritt) * schritt;
  const werte = [];
  for (let w = unten; w <= oben + schritt / 2; w += schritt) werte.push(w);
  return { unten, oben, werte, schritt };
}

function verlaufPunkte() {
  const alle = Z.daten.verlauf;
  if (Z.zeitraum === 'alle' || alle.length === 0) return alle;
  const grenze = new Date(`${alle[alle.length - 1].tag}T12:00:00`);
  grenze.setDate(grenze.getDate() - Number(Z.zeitraum));
  const ab = grenze.toISOString().slice(0, 10);
  return alle.filter((p) => p.tag >= ab);
}

function verlaufZeichnen() {
  const box = Z.bereich.querySelector('.dash-verlauf');
  if (!box) return;
  const punkte = verlaufPunkte();
  const breite = box.clientWidth;
  Z.verlaufBreite = breite;
  if (punkte.length < 2) {
    box.innerHTML = '<p class="dash-leer">Der Verlauf entsteht mit den täglichen Preisabrufen. Dafür braucht es mindestens zwei Tage.</p>';
    return;
  }
  const hoehe = breite < 560 ? 200 : 260;
  const rand = { l: 62, r: 12, o: 10, u: 26 };
  const zeit = punkte.map((p) => new Date(`${p.tag}T12:00:00`).getTime());
  const werte = punkte.map((p) => p.wert);
  const a = achse(Math.min(...werte), Math.max(...werte));
  const x = (t) => rand.l + ((t - zeit[0]) / (zeit[zeit.length - 1] - zeit[0])) * (breite - rand.l - rand.r);
  const y = (w) => rand.o + (1 - (w - a.unten) / (a.oben - a.unten)) * (hoehe - rand.o - rand.u);
  const ticks = a.werte.map((w) => `<line class="dash-gitter" x1="${rand.l}" x2="${breite - rand.r}" y1="${y(w)}" y2="${y(w)}"/>`
    + `<text class="dash-achse" x="${rand.l - 8}" y="${y(w) + 4}" text-anchor="end">${esc(euro(w, { rund: a.schritt >= 1 }))}</text>`).join('');
  const mitte = Math.floor((punkte.length - 1) / 2);
  const tage = breite < 400 ? [0, punkte.length - 1] : [0, mitte, punkte.length - 1];
  const zeitachse = tage.map((i, n) => `<text class="dash-achse" x="${x(zeit[i])}" y="${hoehe - 6}"`
    + ` text-anchor="${n === 0 ? 'start' : n === tage.length - 1 ? 'end' : 'middle'}">${esc(kurzDatum(punkte[i].tag))}</text>`).join('');
  const pfad = punkte.map((p, i) => `${i ? 'L' : 'M'}${x(zeit[i]).toFixed(1)} ${y(p.wert).toFixed(1)}`).join(' ');
  const letzter = punkte[punkte.length - 1];
  const veraendert = letzter.wert - punkte[0].wert;
  const text = `Wert von ${euro(punkte[0].wert)} am ${datum(punkte[0].tag)} auf ${euro(letzter.wert)} am ${datum(letzter.tag)},`
    + ` ${mitVorzeichen(veraendert)}. Pfeiltasten gehen die Tage durch.`;
  box.innerHTML = `<svg class="dash-linie" width="${breite}" height="${hoehe}" viewBox="0 0 ${breite} ${hoehe}"`
    + ` role="img" tabindex="0" aria-label="${esc(text)}">${ticks}${zeitachse}`
    + `<path class="dash-pfad" d="${pfad}"/>`
    + `<circle class="dash-ende" cx="${x(zeit[zeit.length - 1])}" cy="${y(letzter.wert)}" r="4"/>`
    + `<line class="dash-fadenkreuz" y1="${rand.o}" y2="${hoehe - rand.u}" hidden/>`
    + '<circle class="dash-marke" r="4" hidden/></svg>';

  const svg = box.firstElementChild;
  const faden = svg.querySelector('.dash-fadenkreuz');
  const marke = svg.querySelector('.dash-marke');
  let index = punkte.length - 1;
  const zeigen = (i) => {
    index = i;
    const p = punkte[i];
    const px = x(zeit[i]);
    faden.setAttribute('x1', px); faden.setAttribute('x2', px);
    marke.setAttribute('cx', px); marke.setAttribute('cy', y(p.wert));
    faden.removeAttribute('hidden'); marke.removeAttribute('hidden');
    const vorher = i > 0 ? p.wert - punkte[i - 1].wert : null;
    const r = svg.getBoundingClientRect();
    tipZeigen(`<strong>${esc(euro(p.wert))}</strong><span>${esc(datum(p.tag))}</span>`
      + (vorher != null ? `<span>${esc(mitVorzeichen(vorher))} zum Preistag davor</span>` : ''), r.left + px, r.top + y(p.wert));
  };
  const weg = () => { faden.setAttribute('hidden', ''); marke.setAttribute('hidden', ''); tipWeg(); };
  svg.addEventListener('pointermove', (e) => {
    const px = e.clientX - svg.getBoundingClientRect().left;
    let beste = 0;
    zeit.forEach((t, i) => { if (Math.abs(x(t) - px) < Math.abs(x(zeit[beste]) - px)) beste = i; });
    zeigen(beste);
  });
  svg.addEventListener('pointerleave', weg);
  svg.addEventListener('blur', weg);
  svg.addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    zeigen(Math.min(punkte.length - 1, Math.max(0, index + (e.key === 'ArrowLeft' ? -1 : 1))));
  });
}

// ---------------------------------------------------------------- Hinweisfeld

function tipZeigen(html, links, oben) {
  const t = Z.tip;
  t.innerHTML = html;
  t.hidden = false;
  const b = t.offsetWidth;
  t.style.left = `${Math.max(8, Math.min(window.innerWidth - b - 8, links - b / 2))}px`;
  t.style.top = `${Math.max(8, oben - t.offsetHeight - 12)}px`;
}

function tipWeg() { if (Z?.tip) Z.tip.hidden = true; }

// ---------------------------------------------------------------- Balken und Säulen

/** Einfarbige Balken mit Namen und Wert als Text. `zeilen`: {text, karten, euro, punkt?, ziel?, art?}. */
function balken(zeilen, sicht) {
  const wert = (z) => Number(z[sicht] ?? 0);
  const summe = zeilen.reduce((s, z) => s + wert(z), 0);
  // „weitere“ ist eine Summe vieler Orte: Sie gibt nicht den Maßstab vor und bleibt zurückhaltend
  const groesster = Math.max(...zeilen.filter((z) => !z.rest).map(wert), 0);
  const zeile = (z) => {
    const w = wert(z);
    const anteil = summe > 0 ? Math.round((w / summe) * 100) : 0;
    const name = `${z.punkt ? `<span class="dash-punkt dash-punkt-${z.punkt}" aria-hidden="true"></span>` : ''}`
      + `<span class="dash-balken-name">${esc(z.text)}</span>${z.art ? `<span class="dash-art">${esc(z.art)}</span>` : ''}`;
    const inhalt = `<span class="dash-balken-kopf">${name}<span class="dash-balken-wert">`
      + `${esc(sicht === 'euro' ? betrag(w) : zahl(w))}<span class="dash-anteil">${anteil > 0 ? `${anteil} %` : '&lt; 1 %'}</span></span></span>`
      + `<span class="dash-spur"><span class="dash-fuellung${z.rest ? ' dash-rest' : ''}"`
      + ` style="width:${groesster > 0 ? Math.min(100, (w / groesster) * 100) : 0}%"></span></span>`;
    return `<li>${z.ziel ? `<a class="dash-zeile" href="${z.ziel}">${inhalt}</a>` : `<div class="dash-zeile">${inhalt}</div>`}</li>`;
  };
  return `<ul class="dash-balken">${zeilen.filter((z) => wert(z) > 0).map(zeile).join('')}</ul>`;
}

/** Säulen von einer Grundlinie. `zeilen`: {text, wert, tip, kappe?}; `text` leer lässt die Beschriftung aus. */
function saeulen(zeilen, beschriftung) {
  const groesste = Math.max(...zeilen.map((z) => z.wert), 1);
  return `<ul class="dash-saeulen" aria-label="${esc(beschriftung)}">` + zeilen.map((z) => `<li data-tip="${esc(z.tip)}">`
    + `<span class="dash-saeule-raum">${z.kappe != null ? `<span class="dash-kappe">${esc(z.kappe)}</span>` : ''}`
    + `<span class="dash-saeule" style="height:${(z.wert / groesste) * 100}%"></span></span>`
    + `<span class="dash-saeule-text">${esc(z.text)}</span></li>`).join('') + '</ul>';
}

function geordnet(liste, reihenfolge) {
  const nach = new Map(liste.map((z) => [z.wert, z]));
  return reihenfolge.filter(([w]) => nach.has(w)).map(([w, text]) => ({ ...nach.get(w), text }));
}

function nachWert(liste, sicht) {
  return [...liste].sort((a, b) => (b[sicht] ?? 0) - (a[sicht] ?? 0));
}

function mitUebrigen(liste, sicht, wieviele) {
  const reihe = nachWert(liste, sicht);
  if (reihe.length <= wieviele) return reihe;
  const rest = reihe.slice(wieviele);
  return [...reihe.slice(0, wieviele), {
    text: `${zahl(rest.length)} weitere`,
    rest: true,
    karten: rest.reduce((s, z) => s + (z.karten ?? 0), 0),
    euro: rest.reduce((s, z) => s + (z.euro ?? 0), 0),
  }];
}

// ---------------------------------------------------------------- Abschnitte

function kopfHtml(d) {
  const v = d.veraenderung;
  const zeile = (titel, x) => `<div class="dash-vergleich"><span class="etikett">${esc(titel)}</span>${delta(x?.euro, x?.prozent)}</div>`;
  const p = d.preisarten ?? { preisart: 'avg7', name: 'Ø 7 Tage', arten: [] };
  const wahl = p.arten.length > 1
    ? `<label class="dash-preisart-wahl">Preisart <select class="feld dash-preisart" aria-describedby="dash-preisart-hinweis">`
      + p.arten.map((a) => `<option value="${esc(a.wert)}"${a.wert === p.preisart ? ' selected' : ''}>${esc(a.text)}</option>`).join('')
      + '</select></label><span class="dash-preisart-hinweis" id="dash-preisart-hinweis">gilt für alle Werte der App und für Home Assistant</span>'
      + '<span class="dash-preisart-fehler" role="alert"></span>'
    : '';
  const stand = d.tag ? `Cardmarket, ${p.name}, Preise vom ${datum(d.tag)}` : 'Noch keine Preise abgerufen';
  return '<section class="dash-tafel dash-kopf"><div class="dash-held"><div>'
    + '<p class="etikett">Wert der Sammlung</p>'
    + `<p class="dash-gesamt">${esc(d.wert == null ? '–' : euro(d.wert, { rund: d.wert >= 1000 }))}</p>`
    + `<p class="dash-unter">${esc(stand)}</p>${wahl ? `<p class="dash-unter dash-preisart-zeile">${wahl}</p>` : ''}</div>`
    + `<div class="dash-vergleiche">${zeile('Seit gestern', v.vortag)}${zeile('3 Tage', v.tage3)}${zeile('30 Tage', v.tage30)}</div></div>`
    + '<div class="dash-verlauf-kopf"><div><h2 class="dash-titel">Preisentwicklung</h2>'
    + '<p class="dash-unter">Die Karten von heute zu den Preisen des jeweiligen Tages. Neue Karten verändern die Kurve nicht.</p></div>'
    + `${pillen(ZEITRAEUME, Z.zeitraum, 'zeitraum', 'Zeitraum')}</div>`
    + '<div class="dash-verlauf"></div>'
    + zahlenTabelle(['Tag', 'Wert'], [...d.verlauf].reverse().slice(0, 30).map((p) => [datum(p.tag), euro(p.wert)]))
    + '</section>';
}

function kachel(titel, wert, unter, { ziel, inhalt } = {}) {
  const innen = `<span class="etikett">${esc(titel)}</span>`
    + `<span class="dash-kachel-wert">${inhalt ?? esc(wert)}</span><span class="dash-kachel-unter">${esc(unter)}</span>`;
  return ziel ? `<a class="dash-kachel dash-kachel-link" href="${ziel}">${innen}${WEITER}</a>` : `<div class="dash-kachel">${innen}</div>`;
}

function kachelnHtml(d) {
  const s = d.status;
  const foil = d.verteilung.foil.find((z) => z.wert === 1);
  const aktiv = s.pc_verbunden === 'ON';
  const zeichen = `<span class="dash-status${aktiv ? ' dash-status-an' : ''}" aria-hidden="true"></span>${aktiv ? 'Aktiv' : 'Aus'}`;
  const teile = [
    kachel('Karten', zahl(d.karten), `${zahl(d.posten)} Posten`, { ziel: '#/sammlung' }),
    d.wert != null && d.karten ? kachel('Ø je Karte', euro(d.wert / d.karten), d.ohne_preis ? `${karten(d.ohne_preis)} ohne Preis` : 'alle mit Preis') : '',
    kachel('Foil', zahl(foil?.karten ?? 0), foil?.euro ? `${betrag(foil.euro)} Wert` : 'keine Foils'),
    kachel('Zur Prüfung', zahl(s.zur_pruefung ?? 0), !s.zur_pruefung ? 'alles geprüft'
      : s.scan_zur_pruefung ? `davon ${zahl(s.scan_zur_pruefung)} im Scan-Eingang` : 'in Bindern und Kisten', { ziel: '#/pruefen' }),
    kachel('Erkennung am PC', '', s.warteschlange ? `${zahl(s.warteschlange)} Fotos warten` : 'Nichts wartet', { inhalt: zeichen }),
  ];
  if (d.kaufpreis) {
    const k = d.kaufpreis;
    // Paket 8: nur Karten mit Kaufpreis in Euro und heutigem Preis zählen; die anderen stehen sichtbar darunter
    const aus = ausgenommenText(k);
    const unter = (k.karten ? `${betrag(k.kauf)} bezahlt für ${karten(k.karten)}` : 'keine Karte zählt')
      + (aus ? ` · zählen nicht: ${aus}` : '');
    teile.push(kachel('Gegen Kaufpreis', '', unter, { inhalt: delta(k.gewinn ?? null, k.prozent ?? null), ziel: '#/sammlung' }));
  }
  if (d.secret_besitz) {
    // Eigene Secret Lairs: Gewinn/Verlust gegen den Kaufpreis (Wert wie in der Secret-Lair-Ansicht)
    const b = d.secret_besitz;
    const ohne = b.ohne_wert ? ` · ${zahl(b.ohne_wert)} ohne Wert` : '';
    teile.push(kachel('Eigene Secret Lairs', '', `${betrag(b.kaufpreis)} bezahlt · ${zahl(b.exemplare)} Stück${ohne}`,
      { inhalt: delta(b.gewinn, b.prozent), ziel: '#/secretlair' }));
  }
  return `<div class="dash-kacheln">${teile.join('')}</div>`;
}

function galerieHtml(d) {
  if (!d.teuerste.length) return '';
  const karte = (k) => `<li><a class="dash-karte" href="#/dashboard/karte/${k.karte_id}" aria-label="${esc(`${k.name}, ${euro(k.preis)}, Karte öffnen`)}">`
    + `<span class="dash-karte-bild">${bildHtml(k, 400)}${k.foil ? '<span class="dash-foil">Foil</span>' : ''}</span>`
    + `<span class="dash-karte-preis">${esc(euro(k.preis))}</span>`
    + `<span class="dash-karte-name">${esc(k.name)}</span>`
    + `<span class="dash-karte-set">${esc(`${k.set} ${k.nummer ?? ''}`)}${k.anzahl > 1 ? ` · ${zahl(k.anzahl)}×` : ''}</span></a></li>`;
  return tafel('Die teuersten Karten', `<ul class="dash-galerie">${d.teuerste.map(karte).join('')}</ul>`,
    { klasse: 'dash-breit', unter: 'Einzelpreis je Druck' });
}

function bewegungListe(titel, liste, leer) {
  const zeile = (k) => `<li><a class="dash-bewegt" href="#/dashboard/karte/${k.karte_id}">`
    + `<span class="dash-bewegt-bild">${bildHtml(k, 200)}</span>`
    + `<span class="dash-bewegt-text"><span class="dash-bewegt-name">${esc(k.name)}</span>`
    + `<span class="dash-karte-set">${esc(`${k.set} ${k.nummer ?? ''}`)}${k.foil ? ' · Foil' : ''}${k.anzahl > 1 ? ` · ${zahl(k.anzahl)}×` : ''}`
    + ` · ${esc(euro(k.preis_vorher))} auf ${esc(euro(k.preis))}</span></span>${delta(k.euro, k.prozent)}</a></li>`;
  return `<div class="dash-bewegung-spalte"><h3 class="etikett">${esc(titel)}</h3>`
    + (liste.length ? `<ul class="dash-bewegung-liste">${liste.map(zeile).join('')}</ul>` : `<p class="dash-leer">${esc(leer)}</p>`)
    + '</div>';
}

/** Gewinner und Verlierer (Paket 9): je Abstand und Sortierung höchstens 10, nur Karten ab dem Mindestpreis. */
function bewegungZeichnen() {
  const r = Z.daten.bewegung;
  const a = r.abstaende[Z.abstand];
  const b = a[Z.sortierung];
  // Beide Listen leer: oft fehlt der gewählten Preisart der Vergleichstag (Trend, Ø 1 und Ø 30 Tage erst ab 0.43.0)
  const beideLeer = !b.gewinner.length && !b.verlierer.length;
  const leer = (richtung) => (!a.tag ? 'Für diesen Zeitraum gibt es noch keinen Preistag zum Vergleich.'
    : beideLeer ? `Keine Karte ab ${euro(r.mindestpreis)} hat an beiden Tagen einen Preis in dieser Preisart und hat sich bewegt.`
      : `In diesem Zeitraum ist keine Karte ab ${euro(r.mindestpreis)} ${richtung}.`);
  const art = Z.sortierung === 'prozent' ? 'Nach Prozent des Einzelpreises' : 'Nach Anzahl mal Preisänderung';
  Z.bereich.querySelector('.dash-bewegung-unter').textContent = `${art} · nur Karten ab ${euro(r.mindestpreis)}`
    + (a.tag ? ` · verglichen mit ${datum(a.tag)}` : '');
  Z.bereich.querySelector('.dash-bewegung').innerHTML = bewegungListe('Gewinner', b.gewinner, leer('gestiegen'))
    + bewegungListe('Verlierer', b.verlierer, leer('gefallen'));
}

function bewegungHtml(d) {
  if (!d.tag) return '';
  return '<section class="dash-tafel dash-breit"><div class="dash-verlauf-kopf"><div><h2 class="dash-titel">Gewinner und Verlierer</h2>'
    + '<p class="dash-unter dash-bewegung-unter"></p></div><div class="dash-bewegung-wahl">'
    + `${pillen(ABSTAENDE, Z.abstand, 'abstand', 'Vergleich')}${pillen(SORTIERUNGEN, Z.sortierung, 'sortierung', 'Sortieren nach')}`
    + '</div></div><div class="dash-bewegung"></div></section>';
}

function orteZeilen(d) {
  const eintrag = (art, pfad) => (o) => ({ text: o.name, art, karten: o.karten, euro: o.wert ?? 0, ziel: `#/${pfad}/${o.id}` });
  return [...d.binder.map(eintrag('Binder', 'binder')), ...d.kisten.map(eintrag('Kiste', 'kiste')), ...d.decks.map(eintrag('Deck', 'deck'))]
    .filter((o) => o.karten > 0);
}

function verteilungZeichnen() {
  const d = Z.daten;
  const v = d.verteilung;
  const sicht = Z.sicht;
  const typen = v.typ.map((z) => ({ ...z, text: TYPEN[z.wert] ?? 'Unbekannt' }));
  const sprachen = v.sprache.map((z) => ({ ...z, text: SPRACHEN[z.wert] ?? (z.wert ? z.wert.toUpperCase() : 'Unbekannt') }));
  const farben = geordnet(v.farbe, FARBEN).map((z) => ({ ...z, punkt: z.wert }));
  const sets = v.sets[sicht].map((z) => ({ ...z, text: z.name || z.wert.toUpperCase(), ziel: `#/set/${encodeURIComponent(z.wert)}` }));
  const mana = Array.from({ length: 8 }, (_, i) => {
    const z = v.mana.find((m) => m.wert === i);
    const w = z?.[sicht] ?? 0;
    const text = sicht === 'euro' ? betrag(w) : zahl(w);
    return { text: i === 7 ? '7+' : String(i), wert: w, kappe: sicht === 'karten' ? zahl(w) : null,
      tip: `Manawert ${i === 7 ? '7 und mehr' : i}: ${text}` };
  });
  Z.bereich.querySelector('.dash-verteilung').innerHTML = [
    tafel('Orte', balken(mitUebrigen(orteZeilen(d), sicht, ORTE_MAX), sicht), { klasse: 'dash-halb' }),
    tafel('Größte Sets', balken(sets, sicht), { klasse: 'dash-halb' }),
    tafel('Farben', balken(farben, sicht), { klasse: 'dash-drittel' }),
    tafel('Seltenheit', balken(geordnet(v.seltenheit, SELTENHEITEN), sicht), { klasse: 'dash-drittel' }),
    tafel('Preisbereiche', balken(v.preisbereiche, sicht), { klasse: 'dash-drittel', unter: 'Preis je Karte' }),
    tafel('Kartentypen', balken(nachWert(typen, sicht), sicht), { klasse: 'dash-drittel' }),
    tafel('Sprachen', balken(mitUebrigen(sprachen, sicht, SPRACHEN_MAX), sicht), { klasse: 'dash-drittel' }),
    tafel('Manakurve', saeulen(mana, 'Karten je Manawert'), { klasse: 'dash-drittel', unter: 'ohne Länder' }),
  ].join('');
}

function verteilungHtml(d) {
  const umschalter = d.tag ? pillen(SICHTEN, Z.sicht, 'sicht', 'Verteilungen zeigen') : '';
  return '<div class="dash-abschnitt"><h2 class="dash-abschnitt-titel">Woraus die Sammlung besteht</h2>'
    + `${umschalter}</div><div class="dash-verteilung dash-raster"></div>`;
}

function erkennungHtml(d) {
  const reihe = geordnet(d.verteilung.erkennung, ERKENNUNG);
  return tafel('Erkennung', balken(reihe, 'karten'), { klasse: 'dash-drittel', unter: 'Stand aller Karten' });
}

function zuwachsHtml(d) {
  if (!d.zuwachs.length) return '';
  const schritt = Math.ceil(d.zuwachs.length / 6);
  const n = d.zuwachs.length;
  const zeilen = d.zuwachs.map((z, i) => ({ text: (n - 1 - i) % schritt === 0 ? monat(z.monat) : '', wert: z.karten,
    tip: `${monat(z.monat)}: ${karten(z.karten)}` }));
  return tafel('Zuwachs je Monat', saeulen(zeilen, 'Neue Karten je Monat')
    + zahlenTabelle(['Monat', 'Karten'], [...d.zuwachs].reverse().map((z) => [monat(z.monat), zahl(z.karten)])),
  { klasse: 'dash-drittel', unter: 'Karten von heute nach Aufnahmetag' });
}

function scansHtml(d) {
  const gesamt = d.scans.reduce((s, z) => s + z.fotos, 0);
  if (!gesamt) return '';
  const n = d.scans.length;
  const zeilen = d.scans.map((z, i) => ({ text: (n - 1 - i) % 10 === 0 ? kurzDatum(z.tag) : '', wert: z.fotos,
    tip: `${datum(z.tag)}: ${zahl(z.fotos)} Fotos` }));
  return tafel('Bulk-Scan', saeulen(zeilen, 'Scan-Fotos je Tag')
    + zahlenTabelle(['Tag', 'Fotos'], d.scans.filter((z) => z.fotos).reverse().map((z) => [datum(z.tag), zahl(z.fotos)])),
  { klasse: 'dash-drittel', unter: `${zahl(gesamt)} Fotos in 30 Tagen` });
}

function dropsHtml(d) {
  if (!d.drops.length) return '';
  const drop = (x) => {
    const bild = scryfallBild(x.bild);
    return `<li><a class="dash-bewegt" href="#/secretlair/${x.id}">`
      + `<span class="dash-bewegt-bild">${bild ? `<img class="dash-bild" src="${bild}" width="63" height="88" alt="" loading="lazy" decoding="async">` : ''}</span>`
      + `<span class="dash-bewegt-text"><span class="dash-bewegt-name">${esc(x.name)}</span>`
      + `<span class="dash-karte-set">Verkauf ${esc(datum(x.verkauf))} · ${esc(karten(x.karten))}</span></span>`
      + `<span class="dash-delta">${esc(x.teuer == null ? '–' : betrag(x.teuer))}</span></a></li>`;
  };
  return tafel('Kommende Secret Lairs', `<ul class="dash-bewegung-liste">${d.drops.map(drop).join('')}</ul>`,
    { klasse: 'dash-halb', unter: 'Rechts: die teuersten Reprints zusammen, keine Empfehlung' });
}

async function setsLaden(ziel) {
  const z = Z;
  let antwort;
  try { antwort = await holen('/api/sets'); } catch { return; }
  if (Z !== z || !antwort.verfuegbar) return;
  const fast = antwort.sets.filter((s) => s.im_katalog && s.drucke_gesamt >= 20 && s.drucke_da < s.drucke_gesamt)
    .sort((a, b) => b.drucke_da / b.drucke_gesamt - a.drucke_da / a.drucke_gesamt).slice(0, 5);
  if (!fast.length) return;
  const zeile = (s) => {
    const p = Math.min(99, Math.round((s.drucke_da / s.drucke_gesamt) * 100));
    return `<li><a class="dash-zeile" href="#/set/${esc(s.code)}"><span class="dash-balken-kopf"><span class="dash-balken-name">${esc(s.name || s.code.toUpperCase())}</span>`
      + `<span class="dash-balken-wert">${zahl(s.drucke_gesamt - s.drucke_da)} fehlen<span class="dash-anteil">${p} %</span></span></span>`
      + `<span class="dash-spur"><span class="dash-fuellung" style="width:${p}%"></span></span></a></li>`;
  };
  ziel.insertAdjacentHTML('afterbegin', tafel('Fast vollständige Sets', `<ul class="dash-balken">${fast.map(zeile).join('')}</ul>`,
    { klasse: 'dash-halb', unter: 'Anteil der Drucke, ab 20 Drucken je Set' }));
}

// ---------------------------------------------------------------- Aufbau

function verdrahten(bereich) {
  bereich.addEventListener('click', (e) => {
    const a = e.target.closest('a[href^="#/"]');
    if (a && e.button === 0 && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
      e.preventDefault();
      gehe(a.getAttribute('href'));
      return;
    }
    const b = e.target.closest('[data-zeitraum], [data-abstand], [data-sortierung], [data-sicht]');
    if (b) {
      pillenSetzen(b.parentElement, b);
      if (b.dataset.zeitraum) { Z.zeitraum = b.dataset.zeitraum; vorliebe('dashboard.zeitraum', Z.zeitraum); verlaufZeichnen(); }
      if (b.dataset.abstand) { Z.abstand = b.dataset.abstand; vorliebe('dashboard.abstand', Z.abstand); bewegungZeichnen(); }
      if (b.dataset.sortierung) { Z.sortierung = b.dataset.sortierung; vorliebe('dashboard.sortierung', Z.sortierung); bewegungZeichnen(); }
      if (b.dataset.sicht) { Z.sicht = b.dataset.sicht; vorliebe('dashboard.sicht', Z.sicht); verteilungZeichnen(); }
      return;
    }
    const s = e.target.closest('[data-tip]');
    if (s) { const r = s.getBoundingClientRect(); tipZeigen(esc(s.dataset.tip), r.left + r.width / 2, r.top); }
  });
  bereich.addEventListener('pointerover', (e) => {
    const s = e.target.closest('[data-tip]');
    if (!s) return;
    const r = s.getBoundingClientRect();
    tipZeigen(esc(s.dataset.tip), r.left + r.width / 2, r.top);
  });
  bereich.addEventListener('pointerout', (e) => { if (e.target.closest('[data-tip]')) tipWeg(); });
  // Bilder, die fehlen: wie in der Kiste erst das lokale Scryfall-Bild, dann Scryfall direkt, dann eine schlichte Fläche
  bereich.addEventListener('error', (e) => {
    const bild = e.target;
    if (!(bild instanceof HTMLImageElement)) return;
    const sid = bild.dataset.sid;
    if (sid && !Z?.ha && !bild.dataset.lokal) {
      bild.dataset.lokal = '1';
      bild.src = adresse(`/api/pruefen/bild/${sid}`);
      return;
    }
    if (sid && !bild.dataset.cdn && scryfallBild(sid)) {
      bild.dataset.cdn = '1';
      bild.src = scryfallBild(sid);
      return;
    }
    bild.replaceWith(el('span', 'dash-flaeche', esc(bild.dataset.name ?? '')));
  }, true);
}

export async function zeigen(wurzel) {
  verlassen();
  const bereich = el('section', 'dash');
  bereich.setAttribute('aria-label', 'Dashboard');
  bereich.innerHTML = '<header class="dash-kopfzeile">'
    + `<button type="button" class="knopf knopf-sym dash-zurueck" aria-label="Zurück ins Regal">${ZURUECK}</button>`
    + '<h1 class="dash-seitentitel">Dashboard</h1></header>'
    + '<div class="dash-inhalt"><p class="dash-leer" role="status">Dashboard wird geladen …</p></div>';
  wurzel.replaceChildren(bereich);
  bereich.querySelector('.dash-zurueck').addEventListener('click', () => zurueck('#/'));
  const inhalt = bereich.querySelector('.dash-inhalt');
  const wahl = (name, liste, standard) => {
    const w = vorliebe(`dashboard.${name}`);
    return liste.some((x) => x.wert === w) ? w : standard;
  };
  const z = { bereich, daten: null, ha: false, tip: el('div', 'dash-tip'), verlaufBreite: 0, beobachter: null, taste: null,
    zeitraum: wahl('zeitraum', ZEITRAEUME, '90'), abstand: wahl('abstand', ABSTAENDE, 'tage7'),
    sortierung: wahl('sortierung', SORTIERUNGEN, 'euro'), sicht: wahl('sicht', SICHTEN, 'euro') };
  Z = z;
  z.tip.hidden = true;
  z.tip.setAttribute('role', 'status');
  bereich.append(z.tip);
  verdrahten(bereich);
  bereich.addEventListener('change', async (e) => {
    const auswahl = e.target.closest('.dash-preisart');
    if (!auswahl) return;
    const fehler = bereich.querySelector('.dash-preisart-fehler');
    auswahl.disabled = true;
    try {
      await senden('/api/preisart', { methode: 'PUT', daten: { preisart: auswahl.value } });
      if (Z === z) zeigen(wurzel);   // alle Werte neu, in der neuen Preisart
    } catch (f) {
      auswahl.value = z.daten?.preisarten?.preisart ?? auswahl.value;
      auswahl.disabled = false;
      if (fehler) fehler.textContent = f?.message || fehlerText(f?.status ?? 0);
    }
  });
  z.taste = (e) => {
    if (e.key === 'Escape' && !dialogOffen() && Z === z && !/\/karte\/\d+/.test(location.hash)) zurueck('#/');
  };
  document.addEventListener('keydown', z.taste);

  let daten;
  try {
    [daten, { ha: z.ha }] = await Promise.all([holen('/api/dashboard'), sitzungLaden()]);
  } catch {
    if (Z === z) inhalt.innerHTML = '<p class="dash-leer" role="status">Das Dashboard ließ sich nicht laden. Läuft der Server?</p>';
    return;
  }
  if (Z !== z) return;
  z.daten = daten;
  if (!daten.karten) {
    inhalt.innerHTML = '<p class="dash-leer" role="status">Noch keine Karten in der Sammlung. Sobald ein Binder oder eine Kiste gefüllt ist, steht hier alles Wichtige.</p>';
    return;
  }
  if (!daten.tag) z.sicht = 'karten';
  inhalt.innerHTML = `<div class="dash-raster">${kopfHtml(daten)}</div>${kachelnHtml(daten)}`
    + `<div class="dash-raster">${galerieHtml(daten)}${bewegungHtml(daten)}</div>`
    + verteilungHtml(daten)
    + `<div class="dash-raster dash-betrieb">${erkennungHtml(daten)}${zuwachsHtml(daten)}${scansHtml(daten)}</div>`
    + `<div class="dash-raster dash-ausblick">${dropsHtml(daten)}</div>`;
  verlaufZeichnen();
  if (daten.tag) bewegungZeichnen();
  verteilungZeichnen();
  setsLaden(inhalt.querySelector('.dash-ausblick'));
  z.beobachter = new ResizeObserver(() => {
    const box = bereich.querySelector('.dash-verlauf');
    if (box && Z === z && box.clientWidth !== z.verlaufBreite) verlaufZeichnen();
  });
  z.beobachter.observe(bereich.querySelector('.dash-verlauf'));
}

export function verlassen() {
  if (!Z) return Promise.resolve();
  Z.beobachter?.disconnect();
  if (Z.taste) document.removeEventListener('keydown', Z.taste);
  Z = null;
  return Promise.resolve();
}
