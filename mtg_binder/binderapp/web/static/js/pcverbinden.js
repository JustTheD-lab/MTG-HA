// Add-on: den PC mit der Erkennung verbinden. Zeigt den Zustand und erzeugt den Schlüssel für die
// PC-Schnittstelle (Port 8099). Der Schlüssel erscheint genau einmal; im Add-on liegt nur sein Hash.
//
//   await oeffnen()   Dialog; erzeugt auf Wunsch einen neuen Schlüssel (der alte gilt dann nicht mehr)

import { holen, senden, esc } from './gemeinsam.js';
import { dialog, bestaetigen } from './dialog.js';

export async function oeffnen() {
  let v = { eingerichtet: false, port: 8099 };
  let verarbeiter = null;
  try {
    [v, verarbeiter] = await Promise.all([holen('/api/pc/verbindung'), holen('/api/verarbeiter').catch(() => null)]);
  } catch { /* Anzeige ohne Zustand */ }
  const host = location.hostname;
  const inhalt = document.createElement('div');
  inhalt.className = 'pc-verbinden';
  const zustandText = verarbeiter?.aktiv ? 'Die Erkennung am PC ist gerade verbunden.'
    : v.eingerichtet ? 'Ein Schlüssel ist eingerichtet, die Erkennung am PC ist aber gerade nicht verbunden.'
      : 'Noch kein PC verbunden.';
  inhalt.innerHTML = `<p class="dialog-text">${esc(zustandText)}</p>`
    + '<p class="dialog-text">In der Erkennung am PC (Symbol neben der Uhr → Einstellungen) trägst du ein:</p>'
    + `<dl class="pc-angaben"><dt>Adresse</dt><dd><code>http://&lt;IP von Home Assistant&gt;:${esc(v.port)}</code></dd>`
    + '<dt>Schlüssel</dt><dd class="pc-schluessel">wird hier einmal angezeigt</dd></dl>'
    + `<p class="dialog-text pc-hinweis">Die IP ist die von Home Assistant im Heimnetz (nicht die Nabu-Casa-Adresse${
      host && !host.endsWith('.ui.nabu.casa') ? `, vermutlich ${esc(host)}` : ''}).</p>`;
  const d = dialog({ titel: 'PC verbinden', klasse: 'dialog-klein', inhalt });
  const zu = Object.assign(document.createElement('button'), { type: 'button', className: 'knopf', textContent: 'Schließen' });
  const los = Object.assign(document.createElement('button'), {
    type: 'button', className: 'knopf knopf-haupt', textContent: v.eingerichtet ? 'Neuen Schlüssel erzeugen' : 'Schlüssel erzeugen',
  });
  zu.addEventListener('click', () => d.schliessen());
  los.addEventListener('click', async () => {
    if (v.eingerichtet && !(await bestaetigen({
      titel: 'Neuen Schlüssel erzeugen?',
      text: 'Der bisherige Schlüssel gilt danach nicht mehr. Du musst den neuen am PC eintragen.',
      ja: 'Neuen erzeugen', gefahr: true,
    }))) return;
    los.disabled = true;
    try {
      const r = await senden('/api/pc/schluessel');
      const feld = inhalt.querySelector('.pc-schluessel');
      feld.innerHTML = `<code class="pc-code">${esc(r.schluessel)}</code>`
        + '<button type="button" class="knopf pc-kopieren">Kopieren</button>'
        + '<span class="pc-einmal">Nur jetzt sichtbar. Geht er verloren, erzeugst du einfach einen neuen.</span>';
      feld.querySelector('.pc-kopieren').addEventListener('click', async (e) => {
        try { await navigator.clipboard.writeText(r.schluessel); e.target.textContent = 'Kopiert'; }
        catch { e.target.textContent = 'Bitte markieren und kopieren'; }
      });
      v.eingerichtet = true;
      los.hidden = true;
    } catch (fehler) {
      inhalt.querySelector('.pc-schluessel').textContent = fehler.message;
      los.disabled = false;
    }
  });
  d.fuss.append(zu, los);
  await d.fertig;
}
