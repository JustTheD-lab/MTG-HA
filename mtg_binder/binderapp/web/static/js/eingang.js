// Scan-Eingang (#/scan/eingang): Durchgänge des Bulk-Scans mit ihren Fotos. Vorschauen ansehen, Fotos
// verwerfen oder zurückholen (Fotos werden nie gelöscht), Original groß ansehen.

import { holen, senden, esc, gehe, adresse, bildPfad } from './gemeinsam.js';
import { dialog } from './dialog.js';

const STATUS_TEXT = {
  neu: 'Wartet', in_verarbeitung: 'Wird erkannt', erkannt: 'Erkannt', fehler: 'Fehler', verworfen: 'Verworfen',
};

let zustand = null;

const mehrzahl = (n, e, m) => `${n.toLocaleString('de-DE')} ${n === 1 ? e : m}`;

function zeit(iso) {
  if (!iso) return '–';
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function kopfHtml(d, offen) {
  const teile = [mehrzahl(d.fotos ?? 0, 'Foto', 'Fotos')];
  const warten = (d.neu ?? 0) + (d.in_verarbeitung ?? 0);
  if (warten) teile.push(`${warten} warten auf Erkennung`);
  if (d.erkannt) teile.push(`${d.erkannt} erkannt`);
  if (d.fehler) teile.push(`${d.fehler} mit Fehler`);
  if (d.verworfen) teile.push(`${d.verworfen} verworfen`);
  const vorschau = d.letzte_vorschau
    ? `<img class="eingang-mini" src="${esc(adresse(bildPfad(d.letzte_vorschau)))}" alt="" loading="lazy" decoding="async">`
    : '<span class="eingang-mini eingang-mini--leer" aria-hidden="true"></span>';
  return `<button type="button" class="eingang-kopf" aria-expanded="${offen}" data-durchgang="${d.id}">${vorschau}`
    + '<span class="eingang-text">'
    + `<span class="eingang-zeit">${esc(zeit(d.begonnen))}${d.beendet ? '' : ' <span class="eingang-abzeichen">läuft</span>'}</span>`
    + `<span class="eingang-zahlen">${esc(teile.join(' · '))}</span></span>`
    + '<svg class="eingang-pfeil" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5"'
    + ' stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M6 9l6 6 6-6"/></svg></button>';
}

function fotoHtml(f) {
  const verworfen = f.status === 'verworfen';
  const bild = f.vorschau
    ? `<img src="${esc(adresse(bildPfad(f.vorschau)))}" alt="Foto ${f.nummer}" loading="lazy" decoding="async">`
    : '<span class="eingang-platzhalter">keine Vorschau</span>';
  const titel = f.fehler ? `${STATUS_TEXT[f.status] || f.status}: ${f.fehler}` : (STATUS_TEXT[f.status] || f.status);
  // Erkannte Karte: Name mit Sprung in die Kartenansicht (Kiste „Scan-Eingang“); unsichere sichtbar markiert
  const k = f.karte;
  const name = k
    ? `<a class="eingang-name${k.status !== 'sicher' && !k.geprueft ? ' eingang-name--pruefen' : ''}"`
      + ` href="${k.kiste_id ? `#/kiste/${Number(k.kiste_id)}` : '#/sammlung'}/karte/${Number(k.id)}">${esc(k.name)}`
      + `${k.status !== 'sicher' && !k.geprueft ? ' <span class="eingang-pruefen">prüfen</span>' : ''}</a>`
    : (f.status === 'fehler' && f.fehler ? `<span class="eingang-grund">${esc(f.fehler)}</span>` : '');
  const ziel = k ? zielHtml(k.ziel) : '';
  const aktion = (f.status === 'neu' || f.status === 'fehler' || verworfen)
    ? `<button type="button" class="knopf eingang-aktion" data-foto="${f.id}" data-aktion="${verworfen ? 'zurueckholen' : 'verwerfen'}">`
      + `${verworfen ? 'Zurückholen' : 'Verwerfen'}</button>`
    : '';
  return `<li class="eingang-foto${verworfen ? ' eingang-foto--verworfen' : ''}" data-status="${esc(f.status)}">`
    + `<button type="button" class="eingang-bild" data-original="${f.id}" aria-label="Foto ${f.nummer} groß ansehen">${bild}</button>`
    + `<span class="eingang-nummer">${f.nummer}</span>`
    + `<span class="eingang-status" title="${esc(titel)}">${esc(STATUS_TEXT[f.status] || f.status)}</span>`
    + name + ziel + aktion + '</li>';
}

/** Ziel der Sortierregeln: „→ Rot · 6+“ (eingeräumt) oder dezent „wartet: Preis fehlt“. */
function zielHtml(z) {
  if (!z) return '';
  if (z.wartet) {
    const gleich = /^Gleich: (.+)$/.exec(z.wartet);
    const text = gleich ? `gleich → ${gleich[1]}` : `wartet: ${z.wartet}`;
    return `<span class="eingang-ziel eingang-ziel--wartet" title="Sortierregeln">${esc(text)}</span>`;
  }
  if (z.kiste) {
    return `<span class="eingang-ziel" title="Einsortiert nach den Sortierregeln">→ ${esc(z.kiste)}${z.fach ? ` · ${esc(z.fach)}` : ''}</span>`;
  }
  return '';
}

/** Schalter „Automatisch einsortieren“ im Kopf eines Durchgangs (nur wenn die Datenbank Sortierregeln kennt). */
function sortierenHtml(d) {
  if (typeof d.einsortieren !== 'boolean') return '';
  return '<div class="eingang-sortieren"><label class="eingang-schalter">'
    + `<input type="checkbox" class="eingang-einsortieren" data-durchgang="${d.id}"${d.einsortieren ? ' checked' : ''}>`
    + '<span>Automatisch einsortieren</span></label>'
    + '<a class="eingang-regeln-link" href="#/regeln">Sortierregeln</a></div>';
}

async function fotosLaden(z, id, platz) {
  try {
    const d = await holen(`/api/scan/durchgang/${id}`);
    if (zustand !== z) return;
    const liste = [...(d.fotos || [])].sort((x, y) => x.nummer - y.nummer);
    platz.innerHTML = sortierenHtml(d) + (liste.length
      ? `<ul class="eingang-raster" role="list">${liste.map(fotoHtml).join('')}</ul>`
      : '<p class="eingang-leer">In diesem Durchgang gibt es noch keine Fotos.</p>');
  } catch (f) {
    platz.innerHTML = `<p class="meldung">${esc(f.message)}</p>`;
  }
}

async function zeichnen(z) {
  let antwort;
  try { antwort = await holen('/api/scan'); } catch {
    if (zustand === z) z.liste.innerHTML = '<p class="hinweis">Der Scan-Eingang ist mit dieser Datenbank nicht verfügbar.</p>';
    return;
  }
  if (zustand !== z) return;
  const durchgaenge = [...(antwort.durchgaenge || [])].sort((a, b) => b.id - a.id);
  if (!durchgaenge.length) {
    z.liste.innerHTML = '<p class="eingang-leer">Noch kein Durchgang. Tippe auf „Weiter scannen“, um zu beginnen.</p>';
    return;
  }
  z.liste.innerHTML = durchgaenge.map((d) => {
    const offen = z.offen.has(d.id);
    return `<li class="eingang-durchgang" data-id="${d.id}">${kopfHtml(d, offen)}<div class="eingang-fotos"${offen ? '' : ' hidden'}></div></li>`;
  }).join('');
  await Promise.all(durchgaenge.filter((d) => z.offen.has(d.id)).map((d) =>
    fotosLaden(z, d.id, z.liste.querySelector(`.eingang-durchgang[data-id="${d.id}"] .eingang-fotos`))));
}

function originalZeigen(fotoId) {
  const bild = document.createElement('img');
  bild.className = 'eingang-gross';
  bild.alt = 'Foto in voller Größe';
  bild.src = adresse(`api/scan/foto/${fotoId}/original`);
  const d = dialog({ titel: 'Foto', klasse: 'eingang-dialog', inhalt: bild, voll: true });
  return d.fertig;
}

export async function zeigen(wurzel) {
  verlassen();
  const bereich = document.createElement('section');
  bereich.className = 'eingang';
  bereich.setAttribute('aria-label', 'Scan-Eingang');
  bereich.innerHTML = '<header class="eingang-titelzeile">'
    + '<button type="button" class="knopf knopf-sym eingang-zurueck" aria-label="Zurück zum Regal">'
    + '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"'
    + ' stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M15 5l-7 7 7 7"/></svg></button>'
    + '<h1 class="eingang-titel">Scan-Eingang</h1>'
    + '<button type="button" class="knopf knopf-haupt eingang-weiter">Weiter scannen</button></header>'
    + '<p class="eingang-hinweis">Die Erkennung holt die Fotos ab, sobald der PC läuft. Erkannte Karten liegen in der Kiste „Scan-Eingang“, '
    + 'bis eine Regel sie einsortiert (<a class="eingang-regeln-link" href="#/regeln">Sortierregeln</a>).</p>'
    + '<ul class="eingang-liste" role="list"></ul>';
  wurzel.replaceChildren(bereich);
  const z = { bereich, liste: bereich.querySelector('.eingang-liste'), offen: new Set(), uhr: 0 };
  zustand = z;

  bereich.querySelector('.eingang-zurueck').addEventListener('click', () => gehe('#/'));
  bereich.querySelector('.eingang-weiter').addEventListener('click', () => gehe('#/scan'));

  // Links zu den Sortierregeln über die App (neuer Verlaufseintrag, „Zurück“ kommt hierher zurück)
  bereich.addEventListener('click', (e) => {
    const link = e.target.closest('a.eingang-regeln-link');
    if (!link || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return;
    e.preventDefault();
    gehe('#/regeln');
  });

  // Schalter „Automatisch einsortieren“ eines Durchgangs
  z.liste.addEventListener('change', async (e) => {
    const schalter = e.target.closest('.eingang-einsortieren');
    if (!schalter) return;
    const gewollt = schalter.checked;
    schalter.disabled = true;
    try {
      await senden(`/api/scan/durchgang/${schalter.dataset.durchgang}/einstellung`, { daten: { einsortieren: gewollt } });
    } catch (f) {
      schalter.checked = !gewollt;
      schalter.closest('.eingang-sortieren').insertAdjacentHTML('afterend', `<p class="meldung">${esc(f.message)}</p>`);
    }
    schalter.disabled = false;
  });

  z.liste.addEventListener('click', async (e) => {
    const kopf = e.target.closest('.eingang-kopf');
    if (kopf) {
      const id = Number(kopf.dataset.durchgang);
      const platz = kopf.parentElement.querySelector('.eingang-fotos');
      if (z.offen.has(id)) { z.offen.delete(id); platz.hidden = true; kopf.setAttribute('aria-expanded', 'false'); }
      else {
        z.offen.add(id); platz.hidden = false; kopf.setAttribute('aria-expanded', 'true');
        platz.innerHTML = '<p class="eingang-leer">Lade …</p>';
        await fotosLaden(z, id, platz);
      }
      return;
    }
    const gross = e.target.closest('[data-original]');
    if (gross) { originalZeigen(gross.dataset.original); return; }
    const knopf = e.target.closest('[data-aktion]');
    if (knopf) {
      knopf.disabled = true;
      try {
        await senden(`/api/scan/foto/${knopf.dataset.foto}/${knopf.dataset.aktion}`, {});
      } catch (f) {
        knopf.disabled = false;
        knopf.insertAdjacentHTML('afterend', `<span class="meldung">${esc(f.message)}</span>`);
        return;
      }
      const li = knopf.closest('.eingang-durchgang');
      await zeichnen(z);
      void li;
    }
  });

  await zeichnen(z);
  // Solange etwas wartet, alle paar Sekunden nachsehen (kein WebSocket)
  z.uhr = setInterval(() => { if (!document.hidden && !document.querySelector('dialog.dialog[open]')) zeichnen(z); }, 6000);
}

export async function verlassen() {
  if (zustand) clearInterval(zustand.uhr);
  zustand = null;
}
