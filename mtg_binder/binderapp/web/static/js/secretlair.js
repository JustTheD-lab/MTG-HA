// Secret-Lair-Drops (nur im Add-on). Zeigt Zahlen zum Vergleich, keine Kaufempfehlung.
//
//   #/secretlair      Liste: kommende Drops, dann alle seit 2019 nach Jahr, mit Filtern
//   #/secretlair/5    ein Drop: Karten mit Vergleichswerten, Kosten, Name, Verkaufstag, Wunsch, eigene Käufe
//
// Verkaufsbeginn = Tag des Drops + Uhrzeit für alle Drops (Einstellung, Standard 17:00). Den Tag kann der Nutzer
// je Drop ändern; Drops mit Wunsch meldet das Add-on an Home Assistant (Sensor „Secret Lair nächster Wunsch“).
//
// Vergleichswert ist der teuerste Reprint je Karte (Wunsch des Nutzers). Der Umschalter Nicht-Foil/Foil oben (gemerkt
// je Gerät) wählt die Ausführung aller Zahlen; der Server liefert beide (`d` und `d.foil`). „Schnäppchen“: der ganze
// Drop bei Cardmarket ab höchstens N % der Drucke einzeln (Schwelle einstellbar). Eigene Käufe sind Nutzerdaten mit
// Rückgängig über /api/schritt/{id}/rueckgaengig. Daten: web/secretlair.py.

import { holen, senden, esc, gehe, zurueck, euro, scryfallBild, vorliebe } from './gemeinsam.js';
import { dialog, bestaetigen } from './dialog.js';
import { angemeldet } from './anmeldung.js';
import { toastZeigen } from './deck.js';

const ZURUECK = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5"'
  + ' stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M15 5l-7 7 7 7"/></svg>';
const QUELLEN = {
  hand: 'von Hand', wiki: 'MTG Wiki', mtgjson: 'MTGJSON', scryfall: 'Scryfall, Karten nach Nummer gruppiert, kann unvollständig sein',
};

let wurzelMerk = null;
let gezeigt = undefined;   // Nummer des Drops oder null für die Liste
// Filter der Liste: leben bis zum Neuladen, damit „Zurück“ von einem Drop dieselbe Auswahl zeigt
const filter = { suche: '', jahr: '', status: '', wunsch: false, schnaeppchen: false, besitz: false };

const geld = (w) => (w === null || w === undefined ? '–' : euro(w));

/** Gewählte Ausführung (gemerkt je Gerät). */
const istFoil = () => vorliebe('secretlair.foil') === '1';
/** Zahlen der gewählten Ausführung: oben im Drop Nicht-Foil, in `d.foil` dasselbe für Foil. */
const zahlen = (d) => (istFoil() ? d.foil : d);
const ausfuehrungText = (foil) => (foil ? 'Foil' : 'Nicht-Foil');

/** „+12,30 €“ / „−4,00 €“ mit Prozent; Klasse für Farbe und Pfeil-Text für Vorleser. */
function gewinnHtml(gewinn, prozent) {
  if (gewinn === null || gewinn === undefined) return '–';
  const art = gewinn > 0 ? 'sl-plus' : gewinn < 0 ? 'sl-minus' : '';
  const zeichen = gewinn > 0 ? '+' : gewinn < 0 ? '−' : '±';
  const p = prozent === null || prozent === undefined ? ''
    : ` (${zeichen}${Math.abs(prozent).toLocaleString('de-DE', { maximumFractionDigits: 1 })} %)`;
  return `<span class="${art}">${zeichen}${esc(euro(Math.abs(gewinn)))}${esc(p)}</span>`;
}

/** Marke „Schnäppchen −23 %“ der gewählten Ausführung, sonst leer. */
function schnaeppchenHtml(d) {
  const s = zahlen(d).schnaeppchen;
  return s ? `<span class="sl-marke sl-schnaeppchen">Schnäppchen −${s.abschlag} %</span>` : '';
}

/** Umschalter Nicht-Foil/Foil; ein Wechsel merkt sich die Wahl und zeichnet neu. */
function umschalter() {
  const box = document.createElement('div');
  box.className = 'sl-segmente';
  box.setAttribute('role', 'group');
  box.setAttribute('aria-label', 'Ausführung');
  for (const foil of [false, true]) {
    const b = knopf(ausfuehrungText(foil), 'knopf sl-segment');
    b.setAttribute('aria-pressed', String(istFoil() === foil));
    b.addEventListener('click', async () => {
      if (istFoil() === foil) return;
      vorliebe('secretlair.foil', foil ? '1' : '0');
      await neuZeigen();
    });
    box.append(b);
  }
  return box;
}

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

// Cardmarket verlinkt die App über die Produktnummer, wie Scryfall (keine geratenen Adressen)
const CARDMARKET = 'https://www.cardmarket.com/de/Magic/Products?idProduct=';

const abTrend = (low, trend) => [low != null ? `ab ${geld(low)}` : null, trend != null ? `Trend ${geld(trend)}` : null]
  .filter(Boolean).join(' · ');

/** Kurzform für die Liste: das Produkt ohne „Set“, sonst das andere, in der gewählten Ausführung. */
function cardmarketKurz(d) {
  const p = (d.produkte || []).find((x) => !x.set_fassung) || (d.produkte || [])[0];
  if (!p) return '';
  return istFoil() ? abTrend(p.low_foil, p.trend_foil) : abTrend(p.low, p.trend);
}

/** Summe der Secret-Lair-Drucke dieses Drops bei Cardmarket (Ø 7 Tage, sonst ab; ohne Bonuskarten). */
function druckeText(d) {
  const a = zahlen(d);
  if (a.wert === null || a.wert === undefined) return '–';
  return a.mit_preis < d.karten ? `${geld(a.wert)} (${a.mit_preis} von ${d.karten} Karten)` : geld(a.wert);
}

/** Summe einer Sonderausgabe (Raised Foil …), mit „x von y Karten“, wenn Preise fehlen. */
function varianteText(v) {
  if (v.wert === null || v.wert === undefined) return '–';
  return v.mit_preis < v.karten ? `${geld(v.wert)} (${v.mit_preis} von ${v.karten} Karten)` : geld(v.wert);
}

function werteHtml(d) {
  const a = zahlen(d);
  const reihen = [
    ['Teuerste Reprints', geld(a.teuer)], ['Günstigste Reprints', geld(a.guenstig)],
    ['Drucke einzeln bei Cardmarket', druckeText(d)], ['Deine Kosten', geld(a.kosten)],
    ['Teuerste Reprints ÷ Kosten', a.faktor === null ? '–' : `${a.faktor.toLocaleString('de-DE')} ×`],
  ];
  const cm = cardmarketKurz(d);
  if (cm) reihen.push(['Drop bei Cardmarket', cm]);
  for (const v of d.varianten || []) reihen.push([`${v.name}: Drucke einzeln`, varianteText(v)]);
  return `<dl class="sl-werte">${reihen.map(([t, w]) => `<div><dt>${esc(t)}</dt><dd>${esc(w)}</dd></div>`).join('')}</dl>`;
}

const WUNSCH = '<span class="sl-wunsch"><span aria-hidden="true">★</span> Wunsch</span>';

/** Marke „Besitze ich“ mit Gewinn/Verlust, sonst leer. */
function besitzMarke(d) {
  const b = d.besitz;
  if (!b) return '';
  return `<span class="sl-marke sl-besitz">${b.exemplare} im Besitz · ${gewinnHtml(b.gewinn, b.prozent)}</span>`;
}

function eintragHtml(d) {
  const ohneZahl = zahlen(d).ohne_vergleich;
  const ohne = ohneZahl ? ` · ${ohneZahl} ohne Vergleich` : '';
  const tag = d.kommend ? beginnText(d) : datum(d.verkauf);
  return `<li><a class="sl-drop${d.wunsch ? ' sl-drop-wunsch' : ''}" href="#/secretlair/${d.id}"><span class="sl-kopf">`
    + `<span class="sl-name">${esc(d.name)}</span>${d.wunsch ? WUNSCH : ''}${schnaeppchenHtml(d)}${besitzMarke(d)}`
    + `<span class="sl-datum">${esc(tag)} · ${d.fest ? `fertiges Deck, ${d.exemplare} Karten` : `${d.karten} Karten`}${esc(ohne)}</span>`
    + `</span>${werteHtml(d)}</a></li>`;
}

/** Summe aller eigenen Drops oben in der Liste. */
function besitzHtml(b) {
  if (!b) return '';
  const ohne = b.ohne_wert ? ` ${b.ohne_wert} ${b.ohne_wert === 1 ? 'Kauf hat' : 'Käufe haben'} noch keinen Wert und zählen beim Gewinn nicht mit.` : '';
  return '<section class="sl-besitz-summe" aria-label="Eigene Secret Lairs">'
    + `<h2 class="etikett sl-abschnitt">Eigene Secret Lairs (${b.drops} ${b.drops === 1 ? 'Drop' : 'Drops'}, ${b.exemplare} Stück)</h2>`
    + '<dl class="sl-werte">'
    + `<div><dt>Kaufpreis</dt><dd>${esc(geld(b.kaufpreis))}</dd></div>`
    + `<div><dt>Wert heute</dt><dd>${esc(geld(b.wert))}</dd></div>`
    + `<div><dt>Gewinn / Verlust</dt><dd>${gewinnHtml(b.gewinn, b.prozent)}</dd></div></dl>`
    + `<p class="sl-hinweis">Wert je Kauf in der gekauften Ausführung: der ganze Drop bei Cardmarket (Trend, sonst ab), sonst die Drucke einzeln.${esc(ohne)}</p>`
    + '</section>';
}

/** Jahr des Verkaufstags oder „unbekannt“. */
const jahrVon = (d) => (d.verkauf ? d.verkauf.slice(0, 4) : 'unbekannt');

function passt(d) {
  if (filter.suche && !d.name.toLocaleLowerCase('de-DE').includes(filter.suche.toLocaleLowerCase('de-DE'))) return false;
  if (filter.jahr && jahrVon(d) !== filter.jahr) return false;
  if (filter.status === 'kommend' && !d.kommend) return false;
  if (filter.status === 'verkauft' && d.kommend) return false;
  if (filter.wunsch && !d.wunsch) return false;
  if (filter.schnaeppchen && !zahlen(d).schnaeppchen) return false;
  if (filter.besitz && !d.besitz) return false;
  return true;
}

/** Filterleiste: Suche, Jahr, kommend/verkauft, Wunsch, Schnäppchen, Besitze ich. */
function filterHtml(jahre) {
  const option = (wert, text, aktiv) => `<option value="${esc(wert)}"${aktiv === wert ? ' selected' : ''}>${esc(text)}</option>`;
  const haken = (name, text) => `<label class="sl-haken"><input type="checkbox" data-filter="${name}"`
    + `${filter[name] ? ' checked' : ''}> ${esc(text)}</label>`;
  return '<div class="sl-filter" role="search">'
    + '<label class="sl-feld"><span class="etikett">Suche</span>'
    + `<input class="feld" type="search" data-filter="suche" placeholder="Name des Drops" autocomplete="off" value="${esc(filter.suche)}"></label>`
    + '<label class="sl-feld"><span class="etikett">Jahr</span><select class="feld" data-filter="jahr">'
    + option('', 'Alle Jahre', filter.jahr) + jahre.map((j) => option(j, j === 'unbekannt' ? 'Tag unbekannt' : j, filter.jahr)).join('')
    + '</select></label>'
    + '<label class="sl-feld"><span class="etikett">Verkauf</span><select class="feld" data-filter="status">'
    + option('', 'Alle', filter.status) + option('kommend', 'Kommend', filter.status) + option('verkauft', 'Bereits im Verkauf', filter.status)
    + '</select></label>'
    + `<div class="sl-haken-reihe">${haken('wunsch', 'Wunsch')}${haken('schnaeppchen', 'Schnäppchen')}${haken('besitz', 'Besitze ich')}</div>`
    + '</div>';
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
  // Einzelne Bonuskarten ohne Drop gehen in der Liste nur unter; bei kommenden und eigenen Drops bleiben sie sichtbar
  const einzeln = (d) => d.quelle === 'scryfall' && d.karten <= 2 && !d.besitz;
  const sichtbar = r.drops.filter((d) => d.kommend || !einzeln(d));
  const ausgeblendet = r.drops.length - sichtbar.length;
  const kommendZahl = sichtbar.filter((d) => d.kommend).length;
  bereich.append(kopf('Secret Lair', `${kommendZahl} kommend · ${sichtbar.length - kommendZahl} seit 2019`, '#/sets'));
  bereich.append(umschalter());
  bereich.insertAdjacentHTML('beforeend', besitzHtml(r.besitz));

  const werkzeug = document.createElement('div');
  werkzeug.className = 'sl-werkzeug';
  if (angemeldet()) {
    const neu = knopf('Drop einfügen');
    neu.addEventListener('click', async () => {
      const e = await einfuegen();
      if (e) gehe(`#/secretlair/${e.id}`);
    });
    const foil = istFoil();
    const kosten = knopf(`Kosten je Drop (${ausfuehrungText(foil)}): ${geld(foil ? r.kosten_foil : r.kosten)}`);
    kosten.addEventListener('click', async () => {
      const e = await kostenFragen(`Kosten je Drop (${ausfuehrungText(foil)})`,
        `Was dich ein Drop in ${ausfuehrungText(foil)} kostet, mit Versand. Leer heißt: keine Kosten rechnen.`,
        foil ? r.kosten_foil : r.kosten);
      if (e) {
        await senden('/api/secretlair/kosten', { daten: foil ? { kosten_foil: e.kosten } : e });
        await neuZeigen();
      }
    });
    const schwelle = knopf(`Schnäppchen ab −${100 - r.schnaeppchen} %`);
    schwelle.addEventListener('click', async () => {
      const neu = await feldFragen({
        titel: 'Schnäppchen', etikett: 'Höchstens so viel Prozent der Drucke einzeln', typ: 'number', wert: r.schnaeppchen,
        text: 'Ein Drop gilt als Schnäppchen, wenn er bei Cardmarket ab höchstens so viel Prozent der Summe seiner Drucke '
          + 'einzeln kostet (gleiche Ausführung). Ohne Preis für jede Karte gibt es nie ein Schnäppchen.',
      });
      if (neu) { await senden('/api/secretlair/schnaeppchen', { daten: { prozent: Math.round(Number(neu)) } }); await neuZeigen(); }
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
    werkzeug.append(neu, kosten, schwelle, uhr, auf);
  }
  bereich.append(werkzeug);

  const hinweise = document.createElement('div');
  hinweise.innerHTML = `<p class="sl-hinweis">Zuletzt abgeglichen: ${esc(zeitText(r.stand.abgleich))}. `
    + 'Namen und Verkaufstage kommen aus MTG Wiki, sonst aus MTGJSON und Scryfall. '
    + `Die Werte sind Summen aus Cardmarket (Ø 7 Tage, sonst ab, ${ausfuehrungText(istFoil())}, ohne Bonuskarten), keine Kaufempfehlung. `
    + `Schnäppchen: der ganze Drop ab höchstens ${r.schnaeppchen} % der Drucke einzeln.`
    + `${ausgeblendet ? ` ${ausgeblendet} einzelne Karten ohne Drop sind ausgeblendet.` : ''}</p>`
    + (r.stand.fehler ? `<p class="meldung" role="alert">${esc(r.stand.fehler)}</p>` : '')
    + (r.katalog ? '' : '<p class="meldung" role="alert">Dafür braucht das Add-on den Kartenkatalog. Bitte einmal die Erkennung am PC starten.</p>');
  bereich.append(hinweise);

  const jahre = [...new Set(sichtbar.map(jahrVon))].sort().reverse();
  const leiste = document.createElement('div');
  leiste.innerHTML = filterHtml(jahre);
  const ergebnis = document.createElement('div');
  ergebnis.setAttribute('aria-live', 'polite');
  const zeichnen = () => {
    const treffer = sichtbar.filter(passt);
    // Kommende zuerst (der nächste oben), dann je Jahr, neueste zuerst
    const gruppen = [['Kommend', treffer.filter((d) => d.kommend).reverse()]];
    for (const j of jahre) gruppen.push([j === 'unbekannt' ? 'Tag unbekannt' : j, treffer.filter((d) => !d.kommend && jahrVon(d) === j)]);
    const teile = [`<p class="sl-hinweis sl-treffer">${treffer.length} von ${sichtbar.length} Drops</p>`];
    for (const [titel, drops] of gruppen) {
      if (!drops.length) continue;
      teile.push(`<h2 class="etikett sl-abschnitt">${esc(titel)} <span class="sl-zahl">(${drops.length})</span></h2>`
        + `<ul class="sl-liste">${drops.map(eintragHtml).join('')}</ul>`);
    }
    if (!treffer.length) teile.push('<p class="hinweis">Kein Drop passt zu den Filtern.</p>');
    ergebnis.innerHTML = teile.join('');
  };
  leiste.addEventListener('input', (e) => {
    const feld = e.target.closest('[data-filter]');
    if (!feld) return;
    filter[feld.dataset.filter] = feld.type === 'checkbox' ? feld.checked : feld.value;
    zeichnen();
  });
  zeichnen();
  bereich.append(leiste, ergebnis);
  wurzel.replaceChildren(bereich);
}

/** Der Drop als Cardmarket-Produkt: beide Fassungen (mit und ohne „Set“) mit Preisen und Link. */
function cardmarketHtml(d) {
  const produkte = d.produkte || [];
  // Die Drucke einzeln zusammen, direkt neben dem ganzen Drop (Wunsch des Nutzers)
  const drucke = `<li class="sl-cm"><span class="sl-cm-titel">Die Drucke dieses Drops einzeln</span><dl class="sl-werte">`
    + `<div><dt>Zusammen ${d.fest ? 'wie im Deck' : `in ${ausfuehrungText(istFoil())}`} (Ø 7 Tage, sonst ab)</dt><dd>${esc(druckeText(d))}</dd></div></dl></li>`;
  const s = zahlen(d).schnaeppchen;
  if (!produkte.length) {
    return '<h2 class="etikett sl-abschnitt">Bei Cardmarket</h2>'
      + `<ul class="sl-liste">${drucke}</ul>`
      + '<p class="sl-hinweis">Den ganzen Drop führt Cardmarket noch nicht, '
      + 'oder die App konnte ihn keinem Produkt eindeutig zuordnen.</p>';
  }
  const eintrag = (p) => {
    const reihen = [['Nicht-Foil', abTrend(p.low, p.trend) || '–'], ['Foil', abTrend(p.low_foil, p.trend_foil) || '–']];
    const marke = s && s.cardmarket_id === p.cardmarket_id
      ? ` <span class="sl-marke sl-schnaeppchen">Schnäppchen −${s.abschlag} %</span>` : '';
    return `<li class="sl-cm"><a class="sl-cm-link" href="${CARDMARKET}${encodeURIComponent(p.cardmarket_id)}" target="_blank"`
      + ` rel="noopener noreferrer">${esc(p.name)}<span class="sl-cm-pfeil" aria-hidden="true"> ↗</span>`
      + `<span class="nur-vorleser"> (öffnet Cardmarket)</span></a>${marke}`
      + `<dl class="sl-werte">${reihen.map(([t, w]) => `<div><dt>${esc(t)}</dt><dd>${esc(w)}</dd></div>`).join('')}</dl></li>`;
  };
  return '<h2 class="etikett sl-abschnitt">Bei Cardmarket</h2>'
    + `<ul class="sl-liste">${produkte.map(eintrag).join('')}${drucke}</ul>`
    + '<p class="sl-hinweis">Preise aus der täglichen Preisdatei von Cardmarket: der ganze Drop mit ab und Trend, '
    + `die Drucke einzeln mit Ø 7 Tage (sonst ab), ohne Bonuskarten. Schnäppchen: der ganze Drop ab höchstens `
    + `${esc(String(d.schwelle ?? 80))} % der Drucke einzeln in derselben Ausführung, nur wenn jede Karte einen Preis hat.</p>`;
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
  const f = istFoil() ? '_foil' : '';
  const kein = istFoil() ? 'Kein Foil-Reprint' : 'Kein Reprint';
  // Preis in der Ausführung im Deck, wenn das Produkt sie festlegt (fertige Decks), sonst in der gewählten
  const imDeck = (k) => (k.im_deck_foil === null || k.im_deck_foil === undefined ? k[`preis${f}`]
    : k.im_deck_foil ? k.preis_foil : k.preis);
  const karte = (k) => `<li class="sl-karte"><h2 class="sl-kartenname">${k.anzahl > 1 ? `${k.anzahl}× ` : ''}${esc(k.name)}</h2>`
    + (k.bonus ? `<p class="sl-druck">${d.fest ? 'Nicht in der Deckliste (zum Beispiel Token), zählt nicht zum Wert' : 'Bonuskarte, zählt nicht zum Wert des Drops'}</p>` : '')
    + (k.im_deck_foil !== null && k.im_deck_foil !== undefined && !k.bonus ? `<p class="sl-druck">Im Deck: ${ausfuehrungText(Boolean(k.im_deck_foil))}</p>` : '')
    + (k.nur_sld ? '<p class="sl-druck">Basic Land: nur gegen andere Secret Lairs</p>' : '')
    + (k.foil_id ? `<p class="sl-druck">Foil als eigener Druck SLD ${esc(k.nummer)}★</p>` : '')
    + '<div class="sl-figuren">'
    + figurHtml('Secret Lair', istFoil() && k.foil_id ? k.foil_id : k.scryfall_id, k.name, imDeck(k),
      k.nummer ? `SLD ${k.nummer}${istFoil() && k.foil_id ? '★' : ''}` : '', 'Noch kein Bild')
    + figurHtml('Teuerster Reprint', k[`teuer${f}_id`], k.name, k[`teuer${f}`],
      druck(k[`teuer${f}_set`], k[`teuer${f}_nummer`]), kein)
    + figurHtml('Günstigster Reprint', k[`guenstig${f}_id`], k.name, k[`guenstig${f}`],
      druck(k[`guenstig${f}_set`], k[`guenstig${f}_nummer`]), kein)
    + '</div></li>';
  const eigene = d.liste.filter((k) => !k.deck && !k.variante);
  const deck = d.liste.filter((k) => k.deck);
  let html = `<ul class="sl-karten">${eigene.map(karte).join('')}</ul>`;
  for (const v of d.varianten || []) {
    // Sonderausgabe: eigene Drucke mit besonderem Foil, je Karte Bild und Foil-Preis
    const art = d.liste.filter((k) => k.variante === v.name);
    const preis = (k) => (k.preis_foil ?? k.preis);
    html += `<h2 class="etikett sl-abschnitt">Sonderausgabe ${esc(v.name)} (${art.length})</h2>`
      + `<p class="sl-hinweis">Eigene Drucke dieses Drops mit ${esc(v.name)}. Sie zählen nicht zu den Summen oben; `
      + `zusammen einzeln: ${esc(varianteText(v))}.</p>`
      + `<ul class="sl-karten sl-varianten">${art.map((k) => `<li class="sl-karte"><h2 class="sl-kartenname">${esc(k.name)}</h2>`
        + `<div class="sl-figuren">${figurHtml(v.name, k.scryfall_id, k.name, preis(k), k.nummer ? `SLD ${k.nummer}` : '', 'Noch kein Bild')}</div></li>`).join('')}</ul>`;
  }
  if (deck.length) {
    // Die übrigen Karten eines fertigen Decks (meist The List) als knappe Liste, je Karte in ihrer Ausführung im Deck
    const zeile = (k) => {
      const wert = imDeck(k);
      return `<tr><td>${k.anzahl > 1 ? `${k.anzahl}× ` : ''}${esc(k.name)}</td><td>${esc(k.set ?? (k.nummer ? `Nr. ${k.nummer}` : ''))}</td>`
        + `<td>${ausfuehrungText(Boolean(k.im_deck_foil))}</td><td class="sl-zahl-rechts">${esc(geld(wert === null || wert === undefined ? null : wert * k.anzahl))}</td></tr>`;
    };
    const summe = deck.reduce((n, k) => n + k.anzahl, 0);
    html += `<h2 class="etikett sl-abschnitt">Weitere Karten der Deckliste (${summe})</h2>`
      + '<p class="sl-hinweis">Reprints aus anderen Sets (meist The List) und Secret-Lair-Karten anderer Drops. Sie zählen zur '
      + 'Summe der Drucke einzeln, aber nicht zu den Reprints.</p>'
      + '<div class="sl-tabelle-rahmen"><table class="sl-tabelle"><thead><tr><th scope="col">Karte</th><th scope="col">Druck</th>'
      + '<th scope="col">Ausführung</th><th scope="col" class="sl-zahl-rechts">Preis</th></tr></thead>'
      + `<tbody>${deck.map(zeile).join('')}</tbody></table></div>`;
  }
  return html;
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
  bereich.append(umschalter());
  const marken = schnaeppchenHtml(d);
  if (marken) bereich.insertAdjacentHTML('beforeend', `<p class="sl-marken">${marken}</p>`);

  const werkzeug = document.createElement('div');
  werkzeug.className = 'sl-werkzeug';
  if (angemeldet()) {
    const name = knopf('Umbenennen');
    name.addEventListener('click', async () => {
      const neu = await nameFragen(d.name);
      if (neu) { await senden(`/api/secretlair/${id}`, { methode: 'PATCH', daten: { name: neu } }); await neuZeigen(); }
    });
    const foil = istFoil();
    const kosten = knopf(`Kosten ändern (${ausfuehrungText(foil)})`);
    kosten.addEventListener('click', async () => {
      const e = await kostenFragen(`Kosten dieses Drops (${ausfuehrungText(foil)})`, 'Leer heißt: die Standardkosten gelten.',
        zahlen(d).kosten_eigen);
      if (e) {
        await senden(`/api/secretlair/${id}`, { methode: 'PATCH', daten: foil ? { kosten_foil: e.kosten } : e });
        await neuZeigen();
      }
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
  const v = istFoil() ? d.verlauf_foil : d.verlauf;
  const ohne = zahlen(d).ohne_vergleich;
  inhalt.innerHTML = werteHtml(d)
    + `<p class="sl-hinweis">Quelle: ${esc(QUELLEN[d.quelle] ?? d.quelle)}.${ohne ? ` ${ohne} Karten ohne Vergleichswert.` : ''}</p>`
    + (d.fest ? `<p class="sl-hinweis">Fertiges Deck mit ${d.exemplare} Karten: Die ganze Deckliste zählt, jede Karte in der `
      + 'Ausführung, in der sie im Deck liegt (laut MTGJSON). Der Umschalter Nicht-Foil/Foil wirkt hier nur auf die Reprints '
      + 'der Secret-Lair-Karten und auf deine Kosten.</p>' : '')
    + (v && v.tage > 1 ? `<p class="sl-hinweis">Verlauf dieses Drops (${ausfuehrungText(istFoil())}): ${esc(geld(v.erster[1]))} am ${esc(datum(v.erster[0]))}, `
      + `${esc(geld(v.letzter[1]))} am ${esc(datum(v.letzter[0]))} (${v.tage} Tage).</p>` : '');
  const kaeufe = kaeufeTeil(d, id);
  const rest = document.createElement('div');
  rest.innerHTML = cardmarketHtml(d)
    + '<h2 class="etikett sl-abschnitt">Karten</h2>'
    + kartenHtml(d);
  rest.addEventListener('click', (e) => {
    const b = e.target.closest('[data-bild]');
    if (b) bildZeigen(b);
  });
  bereich.append(werkzeug, inhalt, kaeufe, rest);
  wurzel.replaceChildren(bereich);
}

// ---------------------------------------------------------------- Eigene Käufe

let meldungNachher = null;   // { text, schritt } für die Meldung nach dem Neuzeichnen

const kaufDatum = (tag) => (tag ? new Date(`${tag}T00:00:00`).toLocaleDateString('de-DE') : 'Kaufdatum offen');

/** Abschnitt „Meine Käufe“: Summe, je Kauf Kaufpreis, Wert heute, Gewinn/Verlust; Eintragen, Ändern, Löschen. */
function kaeufeTeil(d, id) {
  const teil = document.createElement('section');
  teil.className = 'sl-kaeufe';
  teil.setAttribute('aria-label', 'Meine Käufe');
  const b = d.besitz;
  let html = '<h2 class="etikett sl-abschnitt">Meine Käufe</h2>';
  if (b) {
    html += '<dl class="sl-werte">'
      + `<div><dt>Kaufpreis (${b.exemplare} Stück)</dt><dd>${esc(geld(b.kaufpreis))}</dd></div>`
      + `<div><dt>Wert heute</dt><dd>${esc(geld(b.wert))}</dd></div>`
      + `<div><dt>Gewinn / Verlust</dt><dd>${gewinnHtml(b.gewinn, b.prozent)}</dd></div></dl>`
      + '<ul class="sl-liste sl-kaufliste">' + b.kaeufe.map((k) => '<li class="sl-kauf">'
        + `<p class="sl-kauf-titel">${k.anzahl}× ${esc(k.variante || ausfuehrungText(k.foil))} · je ${esc(geld(k.preis))} · ${esc(kaufDatum(k.datum))}</p>`
        + '<dl class="sl-werte">'
        + `<div><dt>Kaufpreis</dt><dd>${esc(geld(k.kaufpreis))}</dd></div>`
        + `<div><dt>Wert heute</dt><dd>${esc(geld(k.wert))}</dd></div>`
        + `<div><dt>Gewinn / Verlust</dt><dd>${gewinnHtml(k.gewinn, k.prozent)}</dd></div></dl>`
        + `<p class="sl-druck">${k.grundlage ? `Grundlage: ${esc(k.grundlage)}${k.anzahl > 1 ? `, je Stück ${esc(geld(k.stueckwert))}` : ''}`
          : `Noch kein Preis für ${ausfuehrungText(k.foil)}: kein Wert, zählt beim Gewinn nicht mit.`}</p>`
        + (angemeldet() ? `<div class="sl-werkzeug sl-kauf-werkzeug"><button type="button" class="knopf" data-kauf-aendern="${k.id}">Ändern</button>`
          + `<button type="button" class="knopf knopf-gefahr" data-kauf-loeschen="${k.id}">Löschen</button></div>` : '')
        + '</li>').join('') + '</ul>';
  } else {
    html += '<p class="sl-hinweis">Du hast diesen Drop noch nicht eingetragen.</p>';
  }
  teil.innerHTML = html;
  const box = Object.assign(document.createElement('div'), { hidden: true });
  box.setAttribute('role', 'status');
  if (angemeldet()) {
    const neu = knopf('Kauf eintragen', 'knopf knopf-haupt');
    neu.addEventListener('click', async () => {
      const daten = await kaufFragen(null, d.varianten);
      if (!daten) return;
      await kaufSenden(box, () => senden(`/api/secretlair/${id}/kauf`, { daten }), 'Kauf eingetragen.');
    });
    const reihe = document.createElement('div');
    reihe.className = 'sl-werkzeug';
    reihe.append(neu);
    teil.append(reihe);
    teil.addEventListener('click', async (e) => {
      const aendern = e.target.closest('[data-kauf-aendern]');
      const loeschen = e.target.closest('[data-kauf-loeschen]');
      if (aendern) {
        const k = b.kaeufe.find((x) => String(x.id) === aendern.dataset.kaufAendern);
        const daten = await kaufFragen(k, d.varianten);
        if (daten) await kaufSenden(box, () => senden(`/api/secretlair/kauf/${k.id}`, { methode: 'PATCH', daten }), 'Kauf geändert.');
      } else if (loeschen) {
        const kid = loeschen.dataset.kaufLoeschen;
        if (await bestaetigen({ titel: 'Kauf löschen?', text: 'Der Kauf wird entfernt. Du kannst es gleich danach rückgängig machen.', ja: 'Löschen', gefahr: true })) {
          await kaufSenden(box, () => senden(`/api/secretlair/kauf/${kid}`, { methode: 'DELETE' }), 'Kauf gelöscht.');
        }
      }
    });
  }
  teil.append(box);
  if (meldungNachher) {
    const m = meldungNachher;
    meldungNachher = null;
    toastZeigen(box, m.text, { schritt: m.schritt, nachRueck: async () => { await neuZeigen(); } });
  }
  return teil;
}

async function kaufSenden(box, aufruf, text) {
  let r;
  try {
    r = await aufruf();
  } catch (f) {
    toastZeigen(box, f.message, { art: 'fehler' });
    return;
  }
  meldungNachher = { text, schritt: r.schritt };
  await neuZeigen();
}

/** Dialog für einen Kauf; gibt die Felder zurück oder undefined bei Abbruch. */
function kaufFragen(alt, varianten = []) {
  const form = document.createElement('form');
  form.id = 'sl-kauf';
  form.noValidate = true;
  const foil = alt ? alt.foil : istFoil();
  const gewaehlt = alt?.variante ? `v:${alt.variante}` : (foil ? '1' : '0');
  const wahl = (wert, text) => `<label class="sl-haken"><input type="radio" name="sl-foil" value="${esc(wert)}"`
    + `${gewaehlt === wert ? ' checked' : ''}> ${esc(text)}</label>`;
  form.innerHTML = '<fieldset class="sl-ausfuehrung"><legend class="etikett feld-etikett">Ausführung</legend>'
    + wahl('0', 'Nicht-Foil') + wahl('1', 'Foil') + varianten.map((v) => wahl(`v:${v.name}`, v.name)).join('') + '</fieldset>'
    + '<label class="etikett feld-etikett" for="sl-anzahl">Anzahl</label>'
    + `<input id="sl-anzahl" class="feld" type="number" min="1" max="999" step="1" inputmode="numeric" value="${alt ? alt.anzahl : 1}">`
    + '<label class="etikett feld-etikett" for="sl-preis">Kaufpreis je Stück in Euro</label>'
    + `<input id="sl-preis" class="feld" type="number" min="0" max="10000" step="0.01" inputmode="decimal" value="${alt ? alt.preis : ''}">`
    + '<label class="etikett feld-etikett" for="sl-datum">Kaufdatum (wenn bekannt)</label>'
    + `<input id="sl-datum" class="feld" type="date" value="${esc(alt?.datum ?? '')}">`
    + '<p class="meldung" role="alert" aria-live="assertive"></p>';
  const d = dialog({ titel: alt ? 'Kauf ändern' : 'Kauf eintragen', klasse: 'dialog-klein', inhalt: form });
  const abbrechen = knopf('Abbrechen');
  abbrechen.addEventListener('click', () => d.schliessen());
  const los = Object.assign(knopf('Speichern', 'knopf knopf-haupt'), { type: 'submit' });
  los.setAttribute('form', form.id);
  d.fuss.append(abbrechen, los);
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const anzahl = Number(form.querySelector('#sl-anzahl').value);
    const preisRoh = form.querySelector('#sl-preis').value;
    const meldung = form.querySelector('.meldung');
    if (!Number.isInteger(anzahl) || anzahl < 1 || anzahl > 999) { meldung.textContent = 'Die Anzahl muss zwischen 1 und 999 liegen.'; return; }
    if (preisRoh === '' || Number(preisRoh) < 0) { meldung.textContent = 'Bitte den Kaufpreis je Stück eintragen.'; return; }
    const art = form.querySelector('input[name="sl-foil"]:checked').value;
    d.schliessen({
      foil: art !== '0', variante: art.startsWith('v:') ? art.slice(2) : null, anzahl, preis: Number(preisRoh),
      datum: form.querySelector('#sl-datum').value || null,
    });
  });
  form.querySelector('#sl-preis').focus();
  return d.fertig;
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
