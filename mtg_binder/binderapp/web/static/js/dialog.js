// Gemeinsame Dialoge im Stil „Galerie" auf Basis des nativen <dialog> (Fokus bleibt im Dialog, Esc schließt).
//
//   const d = dialog({ titel: 'Leerseiten', klasse: 'mein-dialog', inhalt: element });
//   d.element  – das <dialog>; d.inhalt – der Bereich für eigene Inhalte; d.fuss – Leiste für Knöpfe
//   d.schliessen(wert) – schließt; d.fertig – Promise mit dem Wert (undefined bei Esc/Schließen)
//   await bestaetigen({ titel, text, ja: 'Entfernen', gefahr: true })  → true/false
//
// Am Handy füllt ein Dialog mit `voll: true` den Bildschirm (z. B. der Upload-Dialog), sonst ist er
// eine ruhige Tafel. Solange ein Dialog offen ist, reagieren die Ansichten nicht auf ihre
// Tastenkürzel (siehe dialogOffen()).

import { esc } from './gemeinsam.js';

const KREUZ = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5"'
  + ' stroke-linecap="round" aria-hidden="true" focusable="false"><path d="M6 6l12 12M18 6L6 18"/></svg>';

/** Ist gerade ein Dialog offen? Dann keine Tastenkürzel der Ansichten (Esc, Pfeile). */
export const dialogOffen = () => Boolean(document.querySelector('dialog.dialog[open]'));

let zaehler = 0;

export function dialog({ titel, klasse = '', inhalt = null, voll = false, schliessbar = true } = {}) {
  const id = `dialog-titel-${++zaehler}`;
  const el = document.createElement('dialog');
  el.className = `dialog${voll ? ' dialog-voll' : ''}${klasse ? ` ${klasse}` : ''}`;
  el.setAttribute('aria-labelledby', id);
  el.innerHTML = `<header class="dialog-kopf"><h2 class="dialog-titel" id="${id}">${esc(titel)}</h2>`
    + (schliessbar ? `<button type="button" class="knopf knopf-sym dialog-zu" aria-label="Schließen">${KREUZ}</button>` : '')
    + '</header><div class="dialog-inhalt"></div><footer class="dialog-fuss"></footer>';
  const bereich = el.querySelector('.dialog-inhalt');
  if (inhalt) bereich.append(inhalt);

  let wert;
  let erledigt;
  const fertig = new Promise((fertigMachen) => { erledigt = fertigMachen; });
  const schliessen = (w) => {
    wert = w;
    if (el.open) el.close();
  };
  el.addEventListener('close', () => {
    el.remove();
    erledigt(wert);
  });
  el.addEventListener('cancel', (e) => {  // Esc
    if (!schliessbar) e.preventDefault();
  });
  el.querySelector('.dialog-zu')?.addEventListener('click', () => schliessen(undefined));
  document.body.append(el);
  el.showModal();
  return { element: el, inhalt: bereich, fuss: el.querySelector('.dialog-fuss'), schliessen, fertig };
}

/** Rückfrage mit zwei Knöpfen. `gefahr` färbt den Bestätigen-Knopf als Warnung (z. B. Entfernen). */
export function bestaetigen({ titel, text, ja = 'OK', nein = 'Abbrechen', gefahr = false }) {
  const absatz = document.createElement('p');
  absatz.className = 'dialog-text';
  absatz.textContent = text;
  const d = dialog({ titel, klasse: 'dialog-klein', inhalt: absatz });
  const neinKnopf = document.createElement('button');
  neinKnopf.type = 'button';
  neinKnopf.className = 'knopf';
  neinKnopf.textContent = nein;
  neinKnopf.addEventListener('click', () => d.schliessen(false));
  const jaKnopf = document.createElement('button');
  jaKnopf.type = 'button';
  jaKnopf.className = `knopf ${gefahr ? 'knopf-gefahr' : 'knopf-haupt'}`;
  jaKnopf.textContent = ja;
  jaKnopf.addEventListener('click', () => d.schliessen(true));
  d.fuss.append(neinKnopf, jaKnopf);
  neinKnopf.focus();
  return d.fertig.then((w) => w === true);
}
