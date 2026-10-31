// Liste abziehen (Paket M): Karten, die laut einer ManaBox-Liste (CSV) die Sammlung verlassen haben, aus der
// App herausnehmen. Der Dialog wird aus der Gesamtsammlung geöffnet (kiste.js, nur angemeldet).
//
//   oeffnen()  → Promise<boolean>; wahr, wenn sich etwas an den Karten geändert hat (Abzug oder Rückgängig)
//
// Ablauf: Datei wählen → Vorschau (schreibt nichts) → Häkchen entfernen, wo die Karte bleiben soll → bestätigen →
// Abzug (ein Bedienschritt mit Sicherung) → Meldung mit „Rückgängig“. Dazu die Entnahmeliste nach Ort zum
// Drucken oder als CSV und das Abzugsprotokoll. Alle Aufrufe laufen über senden() aus gemeinsam.js; Fehler
// erscheinen als Satz des Servers. Die Entnahmeliste liegt nur beim Drucken als eigener Block im Dokument
// (`.abzug-druck`, siehe css/abzug.css), auf dem Bildschirm ist er unsichtbar.

import { esc, senden, adresse } from './gemeinsam.js';
import { dialog, bestaetigen } from './dialog.js';

const zahl = (n) => Number(n ?? 0).toLocaleString('de-DE');
const mehr = (n, eins, viele) => `${zahl(n)} ${Number(n) === 1 ? eins : viele}`;
const enc = encodeURIComponent;

function el(tag, klasse = '', html = '') {
  const e = document.createElement(tag);
  if (klasse) e.className = klasse;
  if (html) e.innerHTML = html;
  return e;
}

function zeit(iso) {
  // Zeitpunkte der Datenbank sind UTC, aber ohne „Z“ gespeichert: als UTC lesen, als Ortszeit anzeigen
  const d = new Date(typeof iso === 'string' && /^\d{4}-\d\d-\d\dT[\d:.]+$/.test(iso) ? `${iso}Z` : iso);
  return Number.isNaN(d.getTime()) ? String(iso ?? '') : d.toLocaleString('de-DE', { dateStyle: 'medium', timeStyle: 'short' });
}

const STATUS = { voll: 'Voll vorhanden', teilweise: 'Teilweise vorhanden', fehlt: 'Nicht vorhanden', pruefen: 'Zuerst prüfen' };
const QUELLE = { scan: 'Scan', manabox: 'ManaBox', manuell: 'Von Hand' };
const UNSICHER = { unsicher: 'Erkennung unsicher', name_sicher: 'Name sicher, Druck offen' };

const kartenText = (e) => {
  const set = e.set ? `${String(e.set).toUpperCase()}${e.nummer ? ` ${e.nummer}` : ''}` : '';
  return [set, e.sprache ? String(e.sprache).toUpperCase() : '', e.foil ? 'Foil' : ''].filter(Boolean).join(' · ');
};
const ortName = (o) => `${o.art === 'kiste' ? 'Kiste' : 'Binder'} ${o.name}`;
const platz = (o) => (o.art === 'binder' ? `Seite ${o.seite} · Zeile ${o.zeile}, Spalte ${o.spalte}` : '');

// ---------- CSV und Druck ----------

const CSV_KOPF = ['ort', 'kiste_oder_binder', 'seite', 'zeile', 'spalte', 'name', 'set', 'nummer', 'sprache', 'foil',
  'menge', 'anzahl_vorher', 'quelle'];

/** Entnahmeliste als CSV (UTF-8 mit BOM, Spalten wie auf dem Server). */
function csvText(entnahme) {
  const feld = (w) => {
    const t = String(w ?? '');
    return /[",\r\n]/.test(t) ? `"${t.replaceAll('"', '""')}"` : t;
  };
  const zeilen = entnahme.map((e) => [e.ort.art, e.ort.name, e.ort.seite || '', e.ort.zeile || '', e.ort.spalte || '',
    e.name, (e.set || '').toUpperCase(), e.nummer || '', e.sprache || '', e.foil ? 'foil' : 'normal', e.menge,
    e.anzahl_vorher, e.quelle]);
  return `﻿${[CSV_KOPF, ...zeilen].map((z) => z.map(feld).join(',')).join('\r\n')}\r\n`;
}

function csvSpeichern(text, name = 'entnahme.csv') {
  const a = el('a');
  a.href = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }));
  a.download = name;
  a.hidden = true;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

/** Gruppen je Ort in der Reihenfolge der Entnahmeliste (der Server sortiert schon: Kisten vor Bindern). */
function nachOrt(entnahme) {
  const gruppen = new Map();
  for (const e of entnahme) {
    const schluessel = `${e.ort.art}:${e.ort.id}`;
    if (!gruppen.has(schluessel)) gruppen.set(schluessel, { ort: e.ort, zeilen: [], karten: 0 });
    const g = gruppen.get(schluessel);
    g.zeilen.push(e);
    g.karten += e.menge;
  }
  return [...gruppen.values()];
}

function tabellenHtml(entnahme) {
  if (!entnahme.length) return '<p class="abzug-leer">Nichts zu entnehmen.</p>';
  return nachOrt(entnahme).map((g) => `<section class="abzug-ortgruppe"><h3 class="abzug-ortkopf">${esc(ortName(g.ort))}`
    + ` <span class="abzug-ortzahl">${esc(mehr(g.karten, 'Karte', 'Karten'))}</span></h3>`
    + '<table class="abzug-tabelle"><thead><tr><th class="abzug-kasten"><span class="nur-vorleser">Erledigt</span></th>'
    + '<th class="abzug-r">Menge</th><th>Karte</th><th>Druck</th><th>Platz</th><th class="abzug-r">Vorher</th></tr></thead><tbody>'
    + g.zeilen.map((e) => `<tr><td class="abzug-kasten"><span class="abzug-feld" aria-hidden="true"></span></td>`
      + `<td class="abzug-r"><strong>${e.menge}×</strong></td><td>${esc(e.name)}</td><td>${esc(kartenText(e))}</td>`
      + `<td>${esc(platz(e.ort))}</td><td class="abzug-r">${e.anzahl_vorher}</td></tr>`).join('')
    + '</tbody></table></section>').join('');
}

/** Druckbare Entnahmeliste: eigener Block im Dokument, den nur `@media print` zeigt. */
function drucken(entnahme, titel, untertitel) {
  document.querySelector('.abzug-druck')?.remove();
  const karten = entnahme.reduce((s, e) => s + e.menge, 0);
  const block = el('div', 'abzug-druck', `<h1>${esc(titel)}</h1><p>${esc(untertitel)} · ${esc(mehr(karten, 'Karte', 'Karten'))}`
    + ` aus ${esc(mehr(entnahme.length, 'Posten', 'Posten'))}</p>${tabellenHtml(entnahme)}`);
  document.body.append(block);
  document.body.classList.add('abzug-druckt');
  window.print();
}

function druckAufraeumen() {
  document.querySelector('.abzug-druck')?.remove();
  document.body.classList.remove('abzug-druckt');
}

// ---------- Dialog ----------

export function oeffnen() {
  const z = {
    datei: null, v: null, abgewaehlt: new Set(), geaendert: false, tab: 'neu', neuZustand: 'start', sicht: 'position',
    filter: 'alle', ergebnis: null, zurueckgenommen: false, laeuft: false,
  };
  const wurzel = el('div', 'abzug', `
    <div class="abzug-tabs" role="group" aria-label="Bereich">
      <button type="button" class="knopf abzug-tab" data-tab="neu" aria-pressed="true">Neue Liste</button>
      <button type="button" class="knopf abzug-tab" data-tab="protokoll" aria-pressed="false">Abzugsprotokoll</button>
    </div>
    <p class="meldung abzug-meldung" role="alert"></p>
    <div class="abzug-haupt"></div>`);
  const d = dialog({ titel: 'Liste abziehen', klasse: 'abzug-dialog', voll: true, inhalt: wurzel });
  const haupt = wurzel.querySelector('.abzug-haupt');
  const meldungEl = wurzel.querySelector('.abzug-meldung');
  const meldung = (text) => { meldungEl.textContent = text || ''; };
  const fuss = (...knoepfe) => d.fuss.replaceChildren(...knoepfe.filter(Boolean));
  const knopf = (text, klasse, fn) => {
    const b = el('button', `knopf ${klasse}`.trim(), text);
    b.type = 'button';
    b.addEventListener('click', fn);
    return b;
  };
  const schliessenKnopf = (text = 'Schließen') => knopf(text, '', () => d.schliessen(undefined));

  const waehlbar = () => (z.v ? z.v.entnahme.filter((e) => !z.abgewaehlt.has(e.karte_id)) : []);
  const kartenZahl = () => waehlbar().reduce((s, e) => s + e.menge, 0);

  function tabSetzen(tab) {
    z.tab = tab;
    wurzel.querySelectorAll('.abzug-tab').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.tab === tab)));
    meldung('');
    if (tab === 'protokoll') zeigeProtokoll();
    else zeigeNeu();
  }

  function zeigeNeu() {
    if (z.neuZustand === 'vorschau' && z.v) zeigeVorschau();
    else if (z.neuZustand === 'fertig' && z.ergebnis) zeigeFertig();
    else zeigeStart();
  }

  // ----- Start: Datei wählen -----
  function zeigeStart() {
    z.neuZustand = 'start';
    haupt.innerHTML = '<p class="dialog-text">Wähle eine Kartenliste im ManaBox-Format (CSV) mit den Karten, die aus der Sammlung'
      + ' herausgenommen werden sollen. Zuerst siehst du nur eine Vorschau, es wird noch nichts geändert.</p>'
      + '<label class="knopf knopf-haupt abzug-wahl-datei"><span>Datei wählen …</span>'
      + '<input type="file" class="abzug-datei" accept=".csv,text/csv" aria-label="Kartenliste (CSV) wählen"></label>';
    fuss(schliessenKnopf());
    haupt.querySelector('.abzug-datei').addEventListener('change', (e) => {
      const f = e.target.files?.[0];
      if (!f) return;
      z.datei = f;
      vorschauLaden();
    });
  }

  async function vorschauLaden() {
    meldung('');
    haupt.innerHTML = '<p class="dialog-text">Die Liste wird mit der Sammlung verglichen …</p>';
    fuss(schliessenKnopf('Abbrechen'));
    try {
      z.v = await senden(`/api/abzug/vorschau?name=${enc(z.datei.name)}`, { datei: z.datei });
    } catch (f) {
      zeigeStart();
      meldung(f.message);
      return;
    }
    z.abgewaehlt.clear();
    z.sicht = 'position';
    z.filter = 'alle';
    z.neuZustand = 'vorschau';
    zeigeVorschau();
  }

  // ----- Vorschau -----
  function positionHtml(p) {
    const eintrag = (e) => `<li class="abzug-zeile"><label class="abzug-wahl"><input type="checkbox" class="abzug-haken"`
      + ` data-id="${e.karte_id}"${z.abgewaehlt.has(e.karte_id) ? '' : ' checked'}`
      + ` aria-label="${esc(`${e.menge}× ${e.name} aus ${e.ort.text} entnehmen`)}">`
      + `<span class="abzug-menge">${e.menge}×</span><span class="abzug-ort">${esc(e.ort.text)}</span>`
      + `<span class="abzug-von">von ${e.anzahl_vorher}${e.quelle ? ` · ${esc(QUELLE[e.quelle] ?? e.quelle)}` : ''}</span></label></li>`;
    const pruefen = p.zuerst_pruefen.length
      ? '<div class="abzug-hinweis abzug-pruefen"><p>Diese Karten sind nicht sicher erkannt und werden nie von selbst abgezogen.'
        + ' Bitte zuerst prüfen:</p><ul>'
        + p.zuerst_pruefen.map((k) => `<li><a class="abzug-link" href="#/pruefen/karte/${k.karte_id}">${k.anzahl}× in `
          + `${esc(k.ort.text)} (${esc(UNSICHER[k.status] ?? k.status)}) – in der Prüfansicht öffnen</a></li>`).join('')
        + '</ul></div>' : '';
    const aehnlich = p.aehnlich.length
      ? '<details class="abzug-aehnlich"><summary>Ähnliche Karten (nur Hinweis, werden nicht abgezogen)</summary><ul>'
        + p.aehnlich.map((a) => `<li>${a.anzahl}× ${esc(a.name)} (${esc(kartenText(a))}) in ${esc(a.ort)}</li>`).join('')
        + '</ul></details>' : '';
    return `<article class="abzug-pos abzug-status-${p.status}" data-status="${p.status}">`
      + `<header class="abzug-poskopf"><span class="abzug-chip">${esc(STATUS[p.status] ?? p.status_text)}</span>`
      + `<h3 class="abzug-name">${esc(p.name)}</h3></header>`
      + `<p class="abzug-meta">${esc(kartenText(p))}${p.zustaende?.length ? ` · Zustand laut Liste: ${esc(p.zustaende.join(', '))}` : ''}</p>`
      + `<p class="abzug-zahlen">gewünscht <strong>${p.anzahl}</strong> · vorhanden <strong>${p.vorhanden}</strong>`
      + ` · zu entnehmen <strong>${p.entnahme_karten}</strong>${p.fehlt ? ` · fehlt <strong>${p.fehlt}</strong>` : ''}</p>`
      + (p.entnahmen.length ? `<ul class="abzug-zeilen">${p.entnahmen.map(eintrag).join('')}</ul>` : '')
      + pruefen + aehnlich + '</article>';
  }

  function summenAktualisieren() {
    const n = kartenZahl();
    const posten = waehlbar().length;
    const sum = haupt.querySelector('.abzug-sum-entnahme');
    if (sum) sum.innerHTML = `<strong>${zahl(n)}</strong> ${n === 1 ? 'Karte' : 'Karten'} aus ${zahl(posten)} ${posten === 1 ? 'Posten' : 'Posten'}`;
    const los = d.fuss.querySelector('.abzug-los');
    if (los) {
      los.disabled = n === 0 || z.laeuft;
      los.textContent = n ? `${zahl(n)} ${n === 1 ? 'Karte' : 'Karten'} abziehen` : 'Nichts zu abziehen';
    }
  }

  function zeigeVorschau() {
    z.neuZustand = 'vorschau';
    const v = z.v;
    const s = v.summen;
    const frueher = v.frueher ?? [];
    const warnung = frueher.length
      ? '<div class="abzug-warnung" role="alert"><strong>Achtung: Diese Datei wurde schon einmal abgezogen.</strong><ul>'
        + frueher.map((f) => `<li>Bereits abgezogen am ${esc(zeit(f.zeitpunkt))} (${esc(f.datei ?? '')}, ${esc(mehr(f.karten, 'Karte', 'Karten'))})`
          + `${f.zurueckgenommen ? ' – inzwischen zurückgenommen' : ''}</li>`).join('')
        + '</ul><p>Wenn du sie noch einmal abziehst, werden die Karten ein zweites Mal herausgenommen.</p></div>' : '';
    const ohne = v.ohne_scryfall?.length
      ? `<details class="abzug-aehnlich"><summary>${esc(mehr(v.ohne_scryfall.length, 'Zeile', 'Zeilen'))} ohne Scryfall-ID (werden nicht abgezogen)</summary><ul>`
        + v.ohne_scryfall.map((o) => `<li>Zeile ${o.zeile}: ${o.anzahl}× ${esc(o.name)}</li>`).join('') + '</ul></details>' : '';
    haupt.innerHTML = `<p class="abzug-datei-name">${esc(v.datei)} · ${esc(mehr(s.zeilen, 'Zeile', 'Zeilen'))}</p>`
      + warnung
      + '<dl class="abzug-summen">'
      + `<div><dt>Gewünscht</dt><dd>${zahl(s.karten)} <small>(${zahl(s.posten)} Posten)</small></dd></div>`
      + `<div class="abzug-s-voll"><dt>Voll vorhanden</dt><dd>${zahl(s.voll)}</dd></div>`
      + `<div class="abzug-s-teilweise"><dt>Teilweise</dt><dd>${zahl(s.teilweise)}</dd></div>`
      + `<div class="abzug-s-fehlt"><dt>Nicht vorhanden</dt><dd>${zahl(s.fehlt)}</dd></div>`
      + `<div class="abzug-s-pruefen"><dt>Zuerst prüfen</dt><dd>${zahl(s.pruefen)}</dd></div>`
      + '<div class="abzug-s-entnahme"><dt>Wird abgezogen</dt><dd class="abzug-sum-entnahme"></dd></div></dl>'
      + ohne
      + '<div class="abzug-sichten" role="group" aria-label="Darstellung">'
      + `<button type="button" class="knopf abzug-sicht" data-sicht="position" aria-pressed="${z.sicht === 'position'}">Nach Karte</button>`
      + `<button type="button" class="knopf abzug-sicht" data-sicht="ort" aria-pressed="${z.sicht === 'ort'}">Entnahmeliste nach Ort</button></div>`
      + '<div class="abzug-inhalt"></div>';
    sichtZeichnen();
    summenAktualisieren();
    fuss(
      knopf('Andere Datei', '', () => { z.neuZustand = 'start'; zeigeStart(); }),
      knopf('Liste drucken', '', () => drucken(waehlbar(), 'Entnahmeliste', `${v.datei}, ${zeit(new Date())}`)),
      knopf('Als CSV', '', () => csvSpeichern(z.abgewaehlt.size ? csvText(waehlbar()) : v.csv, 'entnahme.csv')),
      knopf('', 'knopf-gefahr abzug-los', ausfuehren),
    );
    summenAktualisieren();
  }

  function sichtZeichnen() {
    const inhalt = haupt.querySelector('.abzug-inhalt');
    haupt.querySelectorAll('.abzug-sicht').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.sicht === z.sicht)));
    if (z.sicht === 'ort') {
      inhalt.innerHTML = `<div class="abzug-liste">${tabellenHtml(waehlbar())}</div>`;
      return;
    }
    const s = z.v.summen;
    const filter = [['alle', 'Alle', s.posten], ['voll', 'Voll', s.voll], ['teilweise', 'Teilweise', s.teilweise],
      ['fehlt', 'Fehlt', s.fehlt], ['pruefen', 'Zuerst prüfen', s.pruefen]];
    inhalt.innerHTML = '<div class="abzug-filter" role="group" aria-label="Nach Status filtern">'
      + filter.map(([w, t, n]) => `<button type="button" class="knopf abzug-fil" data-fil="${w}" aria-pressed="${z.filter === w}">`
        + `${esc(t)} · ${zahl(n)}</button>`).join('') + '</div>'
      + `<div class="abzug-positionen" data-filter="${z.filter}">${z.v.positionen.map(positionHtml).join('')}</div>`;
  }

  async function ausfuehren() {
    const n = kartenZahl();
    if (!n || z.laeuft) return;
    const nochmal = (z.v.frueher ?? []).some((f) => !f.zurueckgenommen);
    const ja = await bestaetigen({
      titel: 'Liste abziehen',
      text: `${zahl(n)} ${n === 1 ? 'Karte' : 'Karten'} aus der Sammlung abziehen?`
        + `${nochmal ? ' Achtung: Diese Datei wurde schon einmal abgezogen.' : ''}`
        + ' Vorher wird eine Sicherung angelegt, danach kannst du den Abzug rückgängig machen.',
      ja: 'Abziehen', gefahr: true,
    });
    if (!ja) return;
    z.laeuft = true;
    summenAktualisieren();
    const ids = [...z.abgewaehlt];
    const pfad = `/api/abzug/ausfuehren?name=${enc(z.datei.name)}&karten=${n}&pruefsumme=${enc(z.v.pruefsumme)}${ids.length ? `&abgewaehlt=${ids.join(',')}` : ''}`;
    try {
      z.ergebnis = await senden(pfad, { datei: z.datei });
    } catch (f) {
      z.laeuft = false;
      summenAktualisieren();
      meldung(f.message);
      if (f.status === 409) {
        fuss(schliessenKnopf(), knopf('Vorschau neu laden', 'knopf-haupt', vorschauLaden));
      }
      return;
    }
    z.laeuft = false;
    z.geaendert = true;
    z.zurueckgenommen = false;
    z.neuZustand = 'fertig';
    meldung('');
    zeigeFertig();
  }

  // ----- Ergebnis -----
  function zeigeFertig() {
    const r = z.ergebnis;
    haupt.innerHTML = z.zurueckgenommen
      ? '<p class="abzug-erfolg"><strong>Der Abzug wurde rückgängig gemacht.</strong></p>'
        + `<p class="dialog-text">Alle ${esc(mehr(r.karten, 'Karte', 'Karten'))} stehen wieder in der Sammlung.</p>`
      : `<p class="abzug-erfolg"><strong>${esc(mehr(r.karten, 'Karte', 'Karten'))} aus der Sammlung abgezogen.</strong></p>`
        + '<dl class="abzug-summen">'
        + `<div><dt>Posten</dt><dd>${zahl(r.posten)}</dd></div>`
        + `<div><dt>Davon ganz entfernt</dt><dd>${zahl(r.entfernt)}</dd></div>`
        + `<div><dt>Taschen jetzt leer</dt><dd>${zahl(r.taschen_leer)}</dd></div>`
        + `<div><dt>Sammlung danach</dt><dd>${zahl(r.summe?.karten)} <small>Karten</small></dd></div></dl>`
        + `<p class="dialog-text">Sicherung vorher: ${esc(r.sicherung ?? 'keine')}</p>`;
    const csv = el('a', 'knopf', 'Entnahmeliste (CSV)');
    csv.href = adresse(`/api/abzug/${enc(r.schritt)}/entnahme?format=csv`);
    csv.setAttribute('download', '');
    fuss(
      schliessenKnopf(),
      knopf('Neue Liste wählen', '', () => { z.neuZustand = 'start'; z.ergebnis = null; zeigeStart(); }),
      knopf('Entnahmeliste ansehen', '', () => listeAnsehen(r.schritt, zeigeFertig)),
      csv,
      z.zurueckgenommen ? null : knopf('Rückgängig', 'knopf-haupt', async (e) => {
        const b = e.currentTarget;
        b.disabled = true;
        if (await zuruecknehmen(r.schritt)) { z.zurueckgenommen = true; zeigeFertig(); } else b.disabled = false;
      }),
    );
  }

  async function zuruecknehmen(schritt) {
    try {
      await senden(`/api/schritt/${enc(schritt)}/rueckgaengig`);
      z.geaendert = true;
      meldung('');
      return true;
    } catch (f) {
      meldung(f.message);
      return false;
    }
  }

  // ----- Entnahmeliste eines ausgeführten Abzugs -----
  async function listeAnsehen(schritt, zurueckZu) {
    meldung('');
    let a;
    try {
      a = await senden(`/api/abzug/${enc(schritt)}/entnahme`, { methode: 'GET' });
    } catch (f) {
      meldung(f.message);
      return;
    }
    haupt.innerHTML = `<p class="abzug-datei-name">${esc(a.datei ?? '')} · ${esc(mehr(a.karten, 'Karte', 'Karten'))}`
      + `${a.zurueckgenommen ? ' · zurückgenommen' : ''}</p>`
      + `<div class="abzug-liste">${tabellenHtml(a.entnahme)}</div>`;
    const csv = el('a', 'knopf', 'Als CSV speichern');
    csv.href = adresse(`/api/abzug/${enc(schritt)}/entnahme?format=csv`);
    csv.setAttribute('download', '');
    fuss(knopf('Zurück', '', zurueckZu), knopf('Drucken', '', () => drucken(a.entnahme, 'Entnahmeliste', `${a.datei ?? ''}`)), csv);
  }

  // ----- Protokoll -----
  async function zeigeProtokoll() {
    haupt.innerHTML = '<p class="dialog-text">Das Abzugsprotokoll wird geladen …</p>';
    fuss(schliessenKnopf());
    let liste;
    try {
      liste = (await senden('/api/abzuege', { methode: 'GET' })).abzuege ?? [];
    } catch (f) {
      haupt.innerHTML = '';
      meldung(f.message);
      return;
    }
    if (z.tab !== 'protokoll') return;
    if (!liste.length) { haupt.innerHTML = '<p class="dialog-text">Bisher wurde keine Liste abgezogen.</p>'; return; }
    haupt.innerHTML = '<ul class="abzug-protokoll">' + liste.map((a) => `<li class="abzug-prot">`
      + `<div class="abzug-prot-text"><strong>${esc(zeit(a.zeitpunkt))}</strong> · ${esc(a.datei ?? '')}`
      + `<span class="abzug-prot-meta">${esc(mehr(a.karten, 'Karte', 'Karten'))} aus ${esc(mehr(a.posten, 'Posten', 'Posten'))}`
      + `${a.sha256 ? ` · Prüfsumme ${esc(String(a.sha256).slice(0, 8))}` : ''}</span>`
      + `${a.zurueckgenommen ? '<span class="abzug-chip abzug-chip-grau">zurückgenommen</span>' : ''}</div>`
      + '<div class="abzug-prot-knoepfe">'
      + `<button type="button" class="knopf" data-ansehen="${esc(a.schritt)}">Entnahmeliste ansehen</button>`
      + (a.zurueckgenommen ? '' : `<button type="button" class="knopf" data-rueck="${esc(a.schritt)}">Rückgängig</button>`)
      + '</div></li>').join('') + '</ul>';
  }

  // ----- Ereignisse -----
  wurzel.addEventListener('click', async (e) => {
    const tab = e.target.closest('.abzug-tab');
    if (tab) { tabSetzen(tab.dataset.tab); return; }
    const sicht = e.target.closest('.abzug-sicht');
    if (sicht) { z.sicht = sicht.dataset.sicht; sichtZeichnen(); return; }
    const fil = e.target.closest('.abzug-fil');
    if (fil) {
      z.filter = fil.dataset.fil;
      haupt.querySelectorAll('.abzug-fil').forEach((b) => b.setAttribute('aria-pressed', String(b === fil)));
      haupt.querySelector('.abzug-positionen').dataset.filter = z.filter;
      return;
    }
    const link = e.target.closest('a.abzug-link');
    if (link) {  // zur Prüfansicht: Dialog schließen, dann navigiert der Link (Hash) selbst
      d.schliessen(undefined);
      return;
    }
    const ansehen = e.target.closest('[data-ansehen]');
    if (ansehen) { listeAnsehen(ansehen.dataset.ansehen, zeigeProtokoll); return; }
    const rueck = e.target.closest('[data-rueck]');
    if (rueck) {
      const ja = await bestaetigen({ titel: 'Abzug rückgängig machen', text: 'Alle Karten dieses Abzugs kommen zurück in die Sammlung.',
        ja: 'Rückgängig machen' });
      if (ja && await zuruecknehmen(rueck.dataset.rueck)) zeigeProtokoll();
    }
  });
  wurzel.addEventListener('change', (e) => {
    const haken = e.target.closest('.abzug-haken');
    if (!haken) return;
    const id = Number(haken.dataset.id);
    if (haken.checked) z.abgewaehlt.delete(id); else z.abgewaehlt.add(id);
    summenAktualisieren();
  });

  zeigeStart();
  return d.fertig.then(() => {
    druckAufraeumen();
    return z.geaendert;
  });
}
