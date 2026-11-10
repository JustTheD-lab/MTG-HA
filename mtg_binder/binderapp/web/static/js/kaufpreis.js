// Kaufpreis eintragen (Paket 8, Gewinn und Verlust aus Kaufpreisen): ein kleiner Dialog für die Kartenansicht
// (eine Karte) und den Auswahlmodus von Kiste und Sammlung (viele Karten). Gespeichert wird über
// PUT /api/karten/kaufpreis (web/kistenschreiben.py, preise/gewinn.py); jede Änderung ist ein Schritt mit Rückgängig.

import { esc, kaufpreisLesen } from './gemeinsam.js';
import { dialog } from './dialog.js';

/** Fragt den Kaufpreis in Euro je Karte. `was`: z. B. „3 Karten (2 Posten)“, `vorher`: bisheriger Preis.
    Gibt { kaufpreis } zurück (null = entfernen) oder undefined bei Abbruch. */
export function kaufpreisFragen(was, vorher = null) {
  const inhalt = document.createElement('div');
  inhalt.className = 'kaufpreis-dialog';
  inhalt.innerHTML = `<p class="dialog-text">Kaufpreis für ${esc(was)}, in Euro je Karte:</p>
    <label class="feld-etikett etikett" for="kaufpreis-eingabe">Kaufpreis (€ je Karte)</label>
      <input id="kaufpreis-eingabe" class="feld" type="text" inputmode="decimal" autocomplete="off" maxlength="12" placeholder="z. B. 2,50"
        value="${vorher == null ? '' : esc(String(vorher).replace('.', ','))}">
    <p class="dialog-text kaufpreis-hinweis">„Entfernen“ löscht den Kaufpreis. Für Gewinn und Verlust zählt nur Euro.</p>
    <p class="dialog-text kaufpreis-fehler" role="alert" hidden></p>`;
  const d = dialog({ titel: 'Kaufpreis eintragen', klasse: 'dialog-klein', inhalt });
  const feld = inhalt.querySelector('input');
  const fehler = inhalt.querySelector('.kaufpreis-fehler');
  const knopf = (text, klasse, wenn) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = `knopf${klasse ? ` ${klasse}` : ''}`;
    b.textContent = text;
    b.addEventListener('click', wenn);
    d.fuss.append(b);
  };
  const speichern = () => {
    const wert = kaufpreisLesen(feld.value);
    if (wert === null || Number.isNaN(wert)) {
      fehler.textContent = 'Bitte einen Betrag wie 2,50 eingeben (höchstens zwei Nachkommastellen).';
      fehler.hidden = false;
      feld.focus();
      return;
    }
    d.schliessen({ kaufpreis: wert });
  };
  knopf('Abbrechen', '', () => d.schliessen(undefined));
  knopf('Entfernen', '', () => d.schliessen({ kaufpreis: null }));
  knopf('Speichern', 'knopf-haupt', speichern);
  feld.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); speichern(); } });
  feld.focus();
  feld.select();
  return d.fertig;
}
