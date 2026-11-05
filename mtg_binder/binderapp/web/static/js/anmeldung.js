// Anmeldung für das Schreiben: Zustand der Sitzung, Knopf „Anmelden" / „Abmelden", Dialog mit Passwort.
//
// Lesen braucht keine Anmeldung. Schreibende Knöpfe (Hochladen, Leerseiten, Binder anlegen) zeigen die
// Ansichten nur, wenn angemeldet() wahr ist. Ändert sich der Zustand, kommt das Ereignis 'binder:sitzung'
// (detail = { angemeldet, eingerichtet, max_foto_mb }); die Ansichten bauen dann ihre Knöpfe neu.
//
//   await laden()          Zustand vom Server holen (einmal beim Start, danach zwischengespeichert)
//   angemeldet()           true/false
//   sitzung()              { angemeldet, eingerichtet, max_foto_mb }
//   await anmelden()       zeigt den Dialog; true, wenn danach angemeldet
//   knopf()                Pillen-Knopf, der sich selbst aktualisiert (für Kopfleisten)

import { holen, senden, bildStandSetzen } from './gemeinsam.js';
import { dialog } from './dialog.js';

let stand = { angemeldet: false, eingerichtet: false, ha: false, max_foto_mb: 60 };
let anfrage = null;

const SCHLOSS = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5"'
  + ' stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'
  + '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>';

export const sitzung = () => ({ ...stand });
export const angemeldet = () => stand.angemeldet;

function setzen(neu) {
  const vorher = JSON.stringify(stand);
  stand = { ...stand, ...neu };
  if (JSON.stringify(stand) !== vorher) {
    window.dispatchEvent(new CustomEvent('binder:sitzung', { detail: sitzung() }));
  }
}

/** Holt den Zustand vom Server; `neu` erzwingt eine neue Abfrage. Fehler lassen den Zustand, wie er ist. */
export function laden(neu = false) {
  if (!anfrage || neu) {
    anfrage = holen('/api/sitzung').then((s) => {
      bildStandSetzen(s.bildstand);
      setzen(s);
      return sitzung();
    }).catch(() => sitzung());
  }
  return anfrage;
}

// Sitzung abgelaufen (Antwort 401 auf eine schreibende Anfrage): abgemeldet anzeigen.
window.addEventListener('binder:abgelaufen', () => setzen({ angemeldet: false }));

/** Dialog mit Passwortfeld. Gibt true zurück, wenn die Anmeldung geklappt hat. */
export async function anmelden() {
  await laden();
  const form = document.createElement('form');
  form.className = 'anmelde-form';
  form.noValidate = true;
  if (!stand.eingerichtet) {
    form.innerHTML = '<p class="dialog-text">Hochladen und Bearbeiten sind noch nicht eingerichtet. '
      + 'Lege dafür einmal am PC ein Passwort fest:</p>'
      + '<p class="dialog-text"><code>python -m binderapp.web.passwort</code></p>';
    const d = dialog({ titel: 'Anmelden', klasse: 'dialog-klein', inhalt: form });
    const zu = Object.assign(document.createElement('button'), { type: 'button', className: 'knopf', textContent: 'Schließen' });
    zu.addEventListener('click', () => d.schliessen(false));
    d.fuss.append(zu);
    zu.focus();
    await d.fertig;
    return false;
  }
  form.innerHTML = '<p class="dialog-text">Zum Hochladen und Bearbeiten brauchst du das Passwort, das du am PC festgelegt hast.</p>'
    + '<label class="etikett feld-etikett" for="anmelde-passwort">Passwort</label>'
    + '<input id="anmelde-passwort" class="feld" type="password" name="passwort" autocomplete="current-password"'
    + ' autocapitalize="off" spellcheck="false" enterkeyhint="go" required>'
    + '<p class="meldung" role="alert" aria-live="assertive"></p>';
  const d = dialog({ titel: 'Anmelden', klasse: 'dialog-klein', inhalt: form });
  const feld = form.querySelector('input');
  const meldung = form.querySelector('.meldung');
  const abbrechen = Object.assign(document.createElement('button'), { type: 'button', className: 'knopf', textContent: 'Abbrechen' });
  const los = Object.assign(document.createElement('button'), { type: 'submit', className: 'knopf knopf-haupt', textContent: 'Anmelden' });
  los.setAttribute('form', 'anmelde-form');
  form.id = 'anmelde-form';
  abbrechen.addEventListener('click', () => d.schliessen(false));
  d.fuss.append(abbrechen, los);
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!feld.value) { meldung.textContent = 'Bitte das Passwort eingeben.'; feld.focus(); return; }
    los.disabled = true;
    meldung.textContent = '';
    try {
      await senden('/api/anmelden', { daten: { passwort: feld.value } });
      setzen({ angemeldet: true });
      d.schliessen(true);
    } catch (fehler) {
      meldung.textContent = fehler.message;
      feld.select();
    } finally {
      los.disabled = false;
    }
  });
  feld.focus();
  return (await d.fertig) === true;
}

export async function abmelden() {
  try { await senden('/api/abmelden'); } catch { /* abgemeldet ist abgemeldet */ }
  setzen({ angemeldet: false });
}

/** Knopf für Kopfleisten: „Anmelden" (Schloss) bzw. „Abmelden"; aktualisiert sich selbst. */
export function knopf() {
  const k = document.createElement('button');
  k.type = 'button';
  k.className = 'knopf anmelde-knopf';
  const zeichnen = () => {
    const an = stand.angemeldet;
    k.hidden = Boolean(stand.ha);   // im Home-Assistant-Add-on meldet Home Assistant an
    k.innerHTML = `${SCHLOSS}<span class="anmelde-text">${an ? 'Abmelden' : 'Anmelden'}</span>`;
    k.setAttribute('aria-label', an ? 'Abmelden' : 'Anmelden zum Hochladen und Bearbeiten');
    k.dataset.angemeldet = String(an);
  };
  const beiAenderung = () => {
    if (!k.isConnected && k.dataset.gezeigt) { window.removeEventListener('binder:sitzung', beiAenderung); return; }
    zeichnen();
  };
  k.addEventListener('click', () => (stand.angemeldet ? abmelden() : anmelden()));
  window.addEventListener('binder:sitzung', beiAenderung);
  requestAnimationFrame(() => { k.dataset.gezeigt = '1'; });
  zeichnen();
  laden();
  return k;
}
