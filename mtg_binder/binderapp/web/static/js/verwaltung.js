// Add-on: Verwaltung. Zeigt den Stand der Sicherungen, spielt eine Sicherung zurück und führt zu „Datenbank
// löschen“ (Neubeginn).
// Entscheidungen des Nutzers (29.10.2026): endgültig, doppelte Bestätigung und ein eingetipptes Wort; die App zeigt
// vorher, was gelöscht wird, und fragt nur bei den Original-Fotos nach.
//
//   await oeffnen()   Dialog mit dem Stand; nach dem Löschen lädt die App neu

import { holen, senden, esc } from './gemeinsam.js';
import { dialog, bestaetigen } from './dialog.js';

const zahl = (n) => Number(n || 0).toLocaleString('de-DE');
const zeit = (z) => (z ? new Date(z).toLocaleString('de-DE', { dateStyle: 'medium', timeStyle: 'short' }) : 'noch keine');

function knopf(text, klasse = 'knopf') {
  return Object.assign(document.createElement('button'), { type: 'button', className: klasse, textContent: text });
}

function sicherungHtml(s) {
  const zeilen = [
    `<dt>Täglich</dt><dd>${esc(zeit(s.letzte))}${s.anzahl ? ` (${esc(zahl(s.anzahl))} im Add-on)` : ''}</dd>`,
    `<dt>Netzlaufwerk</dt><dd>${esc(zeit(s.netz_letzte))}</dd>`,
  ];
  const fehler = [s.fehler, s.netz_fehler].filter(Boolean)
    .map((f) => `<p class="meldung" role="alert">${esc(f)}</p>`).join('');
  return `<dl class="pc-angaben">${zeilen.join('')}</dl>${fehler}`;
}

function zahlenHtml(z) {
  const posten = [
    [z.binder, 'Binder'], [z.kisten, 'Kisten'], [z.decks, 'Decks'], [z.karten, 'Karten'], [z.fotos, 'Binderfotos'],
    [z.scan_fotos, 'Scan-Fotos'], [z.regeln, 'Sortierregeln'], [z.historie, 'Einträge in der Historie'],
  ];
  return `<dl class="pc-angaben">${posten.map(([n, name]) => `<dt>${esc(name)}</dt><dd>${esc(zahl(n))}</dd>`).join('')}</dl>`;
}

/** Gibt true zurück, wenn gelöscht wurde. */
async function loeschen(v) {
  const form = document.createElement('form');
  form.id = 'datenbank-loeschen-form';
  form.noValidate = true;
  form.innerHTML = '<p class="dialog-text">Das wird gelöscht, auch alle Kisten, Fächer und Sortierregeln:</p>'
    + zahlenHtml(v.zahlen)
    + '<p class="dialog-text pc-hinweis">Der Kartenkatalog, die Verbindung zum PC und die Einstellungen bleiben. '
    + 'Eine Sicherung der Datenbank kommt vorher in den Ordner <code>archiv</code> auf dem Netzlaufwerk.</p>'
    + '<fieldset class="verwaltung-wahl"><legend class="etikett">Original-Fotos</legend>'
    + '<label><input type="radio" name="fotos" value="archiv" checked> In den Archivordner verschieben</label>'
    + '<label><input type="radio" name="fotos" value="loeschen"> Endgültig löschen</label></fieldset>'
    + `<label class="etikett feld-etikett" for="datenbank-wort">Zum Bestätigen ${esc(v.wort)} eintippen</label>`
    + '<input id="datenbank-wort" class="feld" type="text" maxlength="20" autocomplete="off" autocapitalize="characters"'
    + ' spellcheck="false">'
    + '<p class="meldung" role="alert" aria-live="assertive"></p>';
  const d = dialog({ titel: 'Datenbank löschen', klasse: 'dialog-klein', inhalt: form });
  const feld = form.querySelector('#datenbank-wort');
  const text = form.querySelector('.meldung');
  const abbrechen = knopf('Abbrechen');
  abbrechen.addEventListener('click', () => d.schliessen(false));
  const weiter = Object.assign(knopf('Löschen', 'knopf knopf-gefahr'), { type: 'submit', disabled: true });
  weiter.setAttribute('form', form.id);
  d.fuss.append(abbrechen, weiter);
  feld.addEventListener('input', () => { weiter.disabled = feld.value.trim() !== v.wort; });
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (feld.value.trim() !== v.wort) return;
    const fotos = form.elements.fotos.value;
    const ja = await bestaetigen({
      titel: 'Wirklich alles löschen?',
      text: `${zahl(v.zahlen.karten)} Karten und alle Binder, Kisten und Decks werden endgültig gelöscht. Die Original-Fotos werden ${
        fotos === 'archiv' ? 'ins Archiv verschoben' : 'endgültig gelöscht'}.`,
      ja: 'Endgültig löschen', gefahr: true,
    });
    if (!ja) return;
    weiter.disabled = true;
    abbrechen.disabled = true;
    text.textContent = 'Wird gelöscht …';
    try {
      const r = await senden('/api/datenbank/loeschen', { daten: { wort: feld.value.trim(), fotos } });
      d.schliessen(r);
    } catch (f) {
      text.textContent = f.message;
      weiter.disabled = false;
      abbrechen.disabled = false;
    }
  });
  feld.focus();
  return d.fertig;
}

async function ergebnisZeigen(r) {
  const inhalt = document.createElement('div');
  inhalt.innerHTML = '<p class="dialog-text">Die Datenbank ist leer. Du kannst neu beginnen.</p>'
    + `<p class="dialog-text pc-hinweis">Die Sicherung liegt unter <code>${esc(r.archiv)}</code>${
      r.fotos === 'archiv' ? ', die Original-Fotos auch' : ''}.</p>`
    + r.warnungen.map((w) => `<p class="meldung" role="alert">${esc(w)}</p>`).join('');
  const d = dialog({ titel: 'Datenbank gelöscht', klasse: 'dialog-klein', inhalt });
  const zu = knopf('Zum Regal', 'knopf knopf-haupt');
  zu.addEventListener('click', () => d.schliessen());
  d.fuss.append(zu);
  await d.fertig;
  location.hash = '#/';
  location.reload();
}

// ---------------------------------------------------------------- Sicherung zurückspielen
// Antworten des Nutzers (09.11.2026): alle Sicherungen zur Wahl, Bestätigung mit eingetipptem Wort, der Preisverlauf
// bleibt. Vorher sichert das Add-on den jetzigen Stand, das Zurückspielen lässt sich also selbst zurücknehmen.

const groesse = (b) => `${(Number(b || 0) / 1048576).toLocaleString('de-DE', { maximumFractionDigits: 1 })} MB`;

/** Liste der Sicherungen; gibt die gewählte zurück oder nichts. */
async function sicherungWaehlen() {
  const r = await holen('/api/sicherungen');
  const inhalt = document.createElement('div');
  const zeile = (s) => `<li><button type="button" class="verwaltung-sicherung" data-id="${esc(s.id)}">`
    + `<span class="verwaltung-sicherung-zeit">${esc(zeit(s.zeit))}</span>`
    + `<span class="verwaltung-sicherung-art">${esc(s.herkunft_text)} · ${esc(groesse(s.groesse))}</span></button></li>`;
  inhalt.innerHTML = (r.hindernis ? `<p class="meldung" role="alert">${esc(r.hindernis)}</p>` : '')
    + (r.sicherungen.length
      ? '<p class="dialog-text">Wähle den Stand, zu dem du zurückwillst. Die App zeigt dir danach, was darin steckt.</p>'
        + `<ul class="verwaltung-sicherungen">${r.sicherungen.map(zeile).join('')}</ul>`
      : '<p class="dialog-text">Es gibt noch keine Sicherung. Die erste entsteht innerhalb eines Tages von selbst.</p>')
    + (r.netzlaufwerk ? '' : '<p class="dialog-text pc-hinweis">Das Netzlaufwerk ist nicht erreichbar: Die Sicherungen dort fehlen in der Liste.</p>')
    + '<p class="meldung" role="alert" aria-live="assertive"></p>';
  const d = dialog({ titel: 'Sicherung zurückspielen', klasse: 'dialog-klein', inhalt });
  const zu = knopf('Abbrechen');
  zu.addEventListener('click', () => d.schliessen());
  d.fuss.append(zu);
  const text = inhalt.querySelector('.meldung:last-child');
  inhalt.addEventListener('click', async (e) => {
    const k = e.target.closest('[data-id]');
    if (!k || k.disabled) return;
    const alle = inhalt.querySelectorAll('[data-id]');
    alle.forEach((x) => { x.disabled = true; });
    text.textContent = 'Sicherung wird geprüft …';
    try {
      d.schliessen(await senden('/api/sicherung/vorschau', { daten: { id: k.dataset.id } }));
    } catch (f) {
      text.textContent = f.message;
      alle.forEach((x) => { x.disabled = false; });
    }
  });
  return d.fertig;
}

/** Vergleich „in der Sicherung“ gegen „heute“, dann das Wort. Gibt das Ergebnis des Servers zurück oder nichts. */
async function zurueckspielen(v) {
  const posten = [['binder', 'Binder'], ['kisten', 'Kisten'], ['decks', 'Decks'], ['karten', 'Karten'],
    ['fotos', 'Binderfotos'], ['scan_fotos', 'Scan-Fotos'], ['regeln', 'Sortierregeln']];
  const form = document.createElement('form');
  form.id = 'sicherung-zurueck-form';
  form.noValidate = true;
  form.innerHTML = `<p class="dialog-text">Sicherung vom <strong>${esc(zeit(v.zeit))}</strong> (${esc(v.herkunft_text)}).</p>`
    + '<table class="verwaltung-vergleich"><thead><tr><th scope="col"></th><th scope="col">In der Sicherung</th>'
    + '<th scope="col">Heute</th></tr></thead><tbody>'
    + posten.map(([k, name]) => `<tr><th scope="row">${esc(name)}</th><td>${esc(zahl(v.zahlen[k]))}</td>`
      + `<td>${esc(zahl(v.heute[k]))}</td></tr>`).join('')
    + '</tbody></table>'
    + '<p class="dialog-text pc-hinweis">Die Sammlung ist danach wieder so wie in der Sicherung; alles, was seitdem '
    + 'dazukam oder geändert wurde, ist dann weg. Der Preisverlauf bleibt. Der jetzige Stand wird vorher gesichert und '
    + 'steht danach in dieser Liste als „vor einem Zurückspielen“.</p>'
    + '<p class="dialog-text pc-hinweis">Fotos und Vorschaubilder liegen nicht in der Sicherung: Bilder, die seitdem '
    + 'gelöscht wurden, kommen nicht zurück.</p>'
    + (v.hindernis ? `<p class="meldung" role="alert">${esc(v.hindernis)}</p>` : '')
    + `<label class="etikett feld-etikett" for="sicherung-wort">Zum Bestätigen ${esc(v.wort)} eintippen</label>`
    + '<input id="sicherung-wort" class="feld" type="text" maxlength="20" autocomplete="off" autocapitalize="characters"'
    + ' spellcheck="false">'
    + '<p class="meldung" role="alert" aria-live="assertive"></p>';
  const d = dialog({ titel: 'Sicherung zurückspielen', klasse: 'dialog-klein', inhalt: form });
  const feld = form.querySelector('#sicherung-wort');
  const text = form.querySelector('.meldung:last-child');
  const abbrechen = knopf('Abbrechen');
  abbrechen.addEventListener('click', () => d.schliessen());
  const weiter = Object.assign(knopf('Zurückspielen', 'knopf knopf-gefahr'), { type: 'submit', disabled: true });
  weiter.setAttribute('form', form.id);
  d.fuss.append(abbrechen, weiter);
  const passt = () => feld.value.trim() === v.wort && !v.hindernis;
  feld.addEventListener('input', () => { weiter.disabled = !passt(); });
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!passt()) return;
    weiter.disabled = true;
    abbrechen.disabled = true;
    text.textContent = 'Wird zurückgespielt …';
    try {
      d.schliessen(await senden('/api/sicherung/zurueckspielen', { daten: { id: v.id, wort: feld.value.trim() } }));
    } catch (f) {
      text.textContent = f.message;
      weiter.disabled = !passt();
      abbrechen.disabled = false;
    }
  });
  feld.focus();
  return d.fertig;
}

async function zurueckErgebnis(r) {
  const inhalt = document.createElement('div');
  inhalt.innerHTML = `<p class="dialog-text">Die Sammlung ist wieder auf dem Stand vom ${esc(zeit(r.sicherung.zeit))}: `
    + `${esc(zahl(r.zahlen.karten))} Karten, ${esc(zahl(r.zahlen.binder))} Binder, ${esc(zahl(r.zahlen.kisten))} Kisten, `
    + `${esc(zahl(r.zahlen.decks))} Decks.</p>`
    + '<p class="dialog-text pc-hinweis">Der Stand von eben liegt als Sicherung „vor einem Zurückspielen“ in der Liste.</p>';
  const d = dialog({ titel: 'Sicherung zurückgespielt', klasse: 'dialog-klein', inhalt });
  const zu = knopf('Zum Regal', 'knopf knopf-haupt');
  zu.addEventListener('click', () => d.schliessen());
  d.fuss.append(zu);
  await d.fertig;
  location.hash = '#/';
  location.reload();
}

export async function oeffnen() {
  const v = await holen('/api/verwaltung');
  const inhalt = document.createElement('div');
  inhalt.innerHTML = `<h3 class="etikett">Sicherungen</h3>${sicherungHtml(v.sicherung)}`
    + '<p class="verwaltung-knopf"><button type="button" class="knopf sicherung-zurueck">Sicherung zurückspielen …</button></p>'
    + '<h3 class="etikett">ManaBox</h3>'
    + '<p class="dialog-text">Sammlung oder Liste aus einem ManaBox-Export einlesen. Du entscheidest je Eintrag, was wohin geht.</p>'
    + '<p class="verwaltung-knopf"><button type="button" class="knopf manabox-start">Export wählen …</button></p>'
    + '<h3 class="etikett">Neubeginn</h3>'
    + '<p class="dialog-text">Löscht die ganze Sammlung und beginnt mit einer leeren Datenbank.</p>'
    + (v.hindernis ? `<p class="meldung" role="alert">${esc(v.hindernis)}</p>` : '');
  const d = dialog({ titel: 'Verwaltung', klasse: 'dialog-klein', inhalt });
  const zu = knopf('Schließen');
  const los = Object.assign(knopf('Datenbank löschen …', 'knopf knopf-gefahr'), { disabled: Boolean(v.hindernis) });
  zu.addEventListener('click', () => d.schliessen());
  // Das Dateifeld muss im offenen Dialog liegen, sonst öffnet Safari die Auswahl nicht
  const eingabe = Object.assign(document.createElement('input'), { type: 'file', accept: '.csv,text/csv', hidden: true });
  eingabe.setAttribute('aria-label', 'ManaBox-Export (CSV) wählen');
  inhalt.append(eingabe);
  inhalt.querySelector('.manabox-start').addEventListener('click', () => eingabe.click());
  inhalt.querySelector('.sicherung-zurueck').addEventListener('click', async () => {
    const wahl = await sicherungWaehlen();
    if (!wahl) return;
    const r = await zurueckspielen(wahl);
    if (!r) return;
    d.schliessen();
    await zurueckErgebnis(r);
  });
  eingabe.addEventListener('change', async () => {
    const datei = eingabe.files?.[0];
    if (!datei) return;
    d.schliessen();
    const { oeffnen: importOeffnen } = await import('./manabox.js');
    await importOeffnen(datei);
  });
  los.addEventListener('click', async () => {
    const r = await loeschen(v);
    if (!r) return;
    d.schliessen();
    await ergebnisZeigen(r);
  });
  d.fuss.append(zu, los);
  await d.fertig;
}
