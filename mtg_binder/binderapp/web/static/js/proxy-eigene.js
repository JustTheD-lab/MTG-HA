// Proxys: eigene Kartenbilder (Wunsch des Nutzers, 11.11.2026). Bibliothek im Add-on mit Vorgabe je Kartenname:
// Ein Bild ersetzt eine Karte der Liste (über den Kartennamen) oder kommt als Zusatzkarte dazu. Hochladen oder
// Adresse einfügen → Entwurf mit Vorschau (automatisch eingepasst, Zuschnitt wählbar) → Speichern.
//
// Daten: /api/proxy/eigene (Liste), POST /api/proxy/eigene?name= (Bild), POST /api/proxy/eigene/url,
// PATCH|DELETE /api/proxy/eigene/{id}, POST|DELETE /api/proxy/eigene/{id}/hinten[/url].
// Aufrufe nur über holen und senden; Adressen relativ.

import { holen, senden, esc, adresse } from './gemeinsam.js';
import { bestaetigen } from './dialog.js';

const ZUSCHNITTE = {
  auto: 'Automatisch',
  rand: 'Beschnittrand entfernen (1/8 Zoll)',
  mitte: 'Mittig auf 63 × 88 mm zuschneiden',
  strecken: 'Ganzes Bild (wird gestreckt)',
};
const ERGEBNIS = {
  rand: 'Beschnittrand entfernt', mitte: 'mittig zugeschnitten', passt: 'passte schon', strecken: 'gestreckt',
};
const SEITEN = { vorne: 'Vorderseite', hinten: 'Zweite Seite' };
const ANZAHL_MAX = 99;

// Home Assistant reicht über Ingress nur Anfragen bis 16 MB weiter. Größere Bilder rechnet der Browser vorher
// herunter (1488 Pixel Breite: doppelt 300 ppi); kleine gehen unverändert hoch.
const UNVERAENDERT = 12 * 1048576;
const BREITE = 1488;

function alsBlob(leinwand, typ, guete) {
  return new Promise((ok) => leinwand.toBlob(ok, typ, guete));
}

/** Großes Bild im Browser verkleinern ({datei, typ} für `senden`); auch für die Rückseiten. */
export async function verkleinern(datei) {
  if (datei.size <= UNVERAENDERT) return { datei, typ: datei.type };
  const bild = await createImageBitmap(datei);
  try {
    const faktor = Math.min(1, BREITE / Math.min(bild.width, bild.height));
    const leinwand = document.createElement('canvas');
    leinwand.width = Math.round(bild.width * faktor);
    leinwand.height = Math.round(bild.height * faktor);
    const ctx = leinwand.getContext('2d');
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(bild, 0, 0, leinwand.width, leinwand.height);
    let blob = datei.type === 'image/png' ? await alsBlob(leinwand, 'image/png') : null;
    if (blob && blob.size <= UNVERAENDERT) return { datei: blob, typ: 'image/png' };
    // JPEG kennt keine Transparenz: durchsichtige Ecken werden schwarz statt zufällig.
    ctx.globalCompositeOperation = 'destination-over';
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, leinwand.width, leinwand.height);
    blob = await alsBlob(leinwand, 'image/jpeg', 0.93);
    if (!blob) throw new Error('Umrechnen fehlgeschlagen');
    return { datei: blob, typ: 'image/jpeg' };
  } finally {
    bild.close?.();
  }
}

const bildAdresse = (e, seite = 'vorne', klein = true) => adresse(`api/proxy/eigene/${encodeURIComponent(e.id)}/bild`
  + `?seite=${seite}${klein ? '&klein=1' : ''}&v=${encodeURIComponent(e[seite]?.stand ?? '')}`);

function fehlerSatz(f) {
  return f?.message || 'Das hat nicht geklappt. Bitte noch einmal versuchen.';
}

function ergebnisText(s) {
  if (!s) return '';
  return `${ERGEBNIS[s.zuschnitt] ?? s.zuschnitt}${s.gedreht ? ', hochkant gedreht' : ''} · ${s.breite} × ${s.hoehe} px`
    + ` (Original ${s.original?.[0]} × ${s.original?.[1]})`;
}

// ---------------------------------------------------------------- Für die Vorschau und den Auftrag

/** Fertige Bilder zum Kartennamen einer Zeile (Schlüssel vom Server, Vorgabe zuerst); kennt auch Bilder, die
    nach dem Prüfen gespeichert wurden. */
function passende(z, zeile) {
  const k = zeile.schluessel ?? '';
  if (!k) return [];
  return (z.daten.eigene ?? []).filter((e) => !e.entwurf && e.schluessel === k)
    .sort((a, b) => Number(b.vorgabe) - Number(a.vorgabe));
}

/** Gewähltes Bild einer Zeile: Wahl im Auftrag, sonst die Vorgabe der Bibliothek, sonst '' (Scryfall). */
export function wahlFuer(z, zeile) {
  const k = zeile.schluessel ?? '';
  const bilder = passende(z, zeile);
  if (Object.hasOwn(z.eigeneWahl, k) && (z.eigeneWahl[k] === '' || bilder.some((e) => e.id === z.eigeneWahl[k]))) {
    return z.eigeneWahl[k];
  }
  return bilder.find((e) => e.vorgabe)?.id ?? '';
}

/** Auswahl „Scryfall oder eigenes Bild“ für eine Zeile der Vorschau (nur, wenn es Bilder zum Namen gibt). */
export function wahlHtml(z, zeile) {
  const bilder = passende(z, zeile);
  if (!bilder.length) return '';
  const gewaehlt = wahlFuer(z, zeile);
  const optionen = ['<option value="">Scryfall-Bild</option>'].concat(bilder.map((e) => `<option value="${esc(e.id)}"`
    + `${e.id === gewaehlt ? ' selected' : ''}>Eigenes Bild: ${esc(e.name)}${e.vorgabe ? ' (automatisch)' : ''}</option>`));
  return `<select class="feld proxy-eigen-wahl" data-eigen-zeile="${esc(zeile.schluessel)}"`
    + ` aria-label="Bild für ${esc(zeile.name)}">${optionen.join('')}</select>`;
}

/** Anfrage-Teil für POST /api/proxy/auftrag: Wahl je Zeile (nur Abweichungen) und Zusatzkarten. */
export function auftragDaten(z) {
  const zusatz = Object.entries(z.zusatz).filter(([, n]) => n > 0).map(([id, anzahl]) => ({ id, anzahl }));
  // genau die Wahl, die die Vorschau zeigt: je Zeile mit Bildern das gewählte Bild oder null (Scryfall)
  const eigene = {};
  for (const r of z.vorschau?.zeilen ?? []) {
    if (passende(z, r).length) eigene[r.schluessel] = wahlFuer(z, r) || null;
  }
  return { eigene, zusatz };
}

export const zusatzSumme = (z) => Object.values(z.zusatz).reduce((s, n) => s + n, 0);

// ---------------------------------------------------------------- Bibliothek

function entwurfHtml(e, liste) {
  const andereVorgabe = liste.some((x) => x.id !== e.id && x.vorgabe && x.schluessel && x.schluessel === e.schluessel);
  const seite = (s) => {
    const daten = e[s];
    const wahl = Object.entries(ZUSCHNITTE).map(([w, t]) => `<option value="${w}"${(daten?.gewuenscht ?? 'auto') === w
      ? ' selected' : ''}>${esc(t)}</option>`).join('');
    if (!daten) {
      return `<div class="proxy-eigen-seite"><p class="etikett">${SEITEN[s]}</p>`
        + '<p class="proxy-hinweis">Nur für doppelseitige Karten: Bild der zweiten Seite.</p>'
        + bildEingabe(`proxy-eigen-hinten-${e.id}`, 'Zweite Seite hochladen', `data-hinten="${esc(e.id)}"`)
        + `<div class="proxy-zeile"><input class="feld proxy-eigen-url" type="url" inputmode="url" placeholder="https://…"`
        + ` aria-label="Adresse der zweiten Seite" data-hinten-url-feld="${esc(e.id)}" autocomplete="off" spellcheck="false">`
        + `<button type="button" class="knopf" data-hinten-url="${esc(e.id)}">Laden</button></div></div>`;
    }
    return `<div class="proxy-eigen-seite"><p class="etikett">${SEITEN[s]}</p>`
      + `<a href="${esc(bildAdresse(e, s, false))}" target="_blank" rel="noopener" class="proxy-eigen-gross">`
      + `<img src="${esc(bildAdresse(e, s))}" alt="Vorschau ${esc(SEITEN[s])} ${esc(e.name)}" decoding="async"></a>`
      + `<p class="proxy-hinweis" role="status">${esc(ergebnisText(daten))}</p>`
      + `<label class="etikett feld-etikett" for="proxy-zs-${s}-${e.id}">Zuschnitt</label>`
      + `<select id="proxy-zs-${s}-${e.id}" class="feld" data-zuschnitt="${s}" data-id="${esc(e.id)}">${wahl}</select>`
      + (s === 'hinten' ? `<button type="button" class="knopf proxy-weg" data-hinten-weg="${esc(e.id)}">Zweite Seite entfernen</button>` : '')
      + '</div>';
  };
  return `<div class="proxy-eigen-entwurf" data-entwurf="${esc(e.id)}">`
    + `<h3 class="proxy-unter">Neues Bild prüfen</h3>`
    + `<div class="proxy-eigen-seiten">${seite('vorne')}${seite('hinten')}</div>`
    + '<div class="proxy-felder">'
    + `<div><label class="etikett feld-etikett" for="proxy-eigen-name-${e.id}">Name in der Bibliothek</label>`
    + `<input id="proxy-eigen-name-${e.id}" class="feld" type="text" maxlength="80" value="${esc(e.name)}" autocomplete="off"></div>`
    + `<div><label class="etikett feld-etikett" for="proxy-eigen-karte-${e.id}">Kartenname (leer: nur Zusatzkarte)</label>`
    + `<input id="proxy-eigen-karte-${e.id}" class="feld" type="text" maxlength="200" value="${esc(e.karte ?? '')}"`
    + ' autocomplete="off" spellcheck="false" placeholder="z. B. Sol Ring"></div></div>'
    + `<label class="proxy-haken"><input type="checkbox" id="proxy-eigen-vorgabe-${e.id}"${andereVorgabe ? '' : ' checked'}>`
    + '<span>Automatisch für diese Karte verwenden (statt Scryfall, je Auftrag abwählbar)</span></label>'
    + '<div class="proxy-zeile">'
    + `<button type="button" class="knopf knopf-haupt" data-speichern="${esc(e.id)}">Speichern</button>`
    + `<button type="button" class="knopf" data-verwerfen="${esc(e.id)}">Verwerfen</button></div></div>`;
}

function bildEingabe(id, text, attribute) {
  return `<div class="proxy-zeile"><label class="knopf proxy-datei" for="${id}"><span>${esc(text)}</span></label>`
    + `<input id="${id}" class="nur-vorleser" type="file" accept="image/png,image/jpeg,image/webp" ${attribute}></div>`;
}

function eintragHtml(z, e) {
  const n = z.zusatz[e.id] ?? 0;
  return `<li class="proxy-eigen-eintrag" data-eintrag="${esc(e.id)}">`
    + `<a href="${esc(bildAdresse(e, 'vorne', false))}" target="_blank" rel="noopener" class="proxy-eigen-bild">`
    + `<img src="${esc(bildAdresse(e))}" alt="" loading="lazy" decoding="async"></a>`
    + '<div class="proxy-eigen-text">'
    + `<strong class="proxy-kachel-name">${esc(e.name)}</strong>`
    + `<span class="proxy-hinweis">${e.karte ? `Karte: ${esc(e.karte)}` : 'Ohne Kartennamen (nur Zusatzkarte)'}`
    + `${e.hinten ? ' · mit zweiter Seite' : ''}</span>`
    + (e.vorgabe ? '<span class="proxy-eigen-marke">Automatisch statt Scryfall</span>' : '')
    + '<div class="proxy-zeile proxy-eigen-knoepfe">'
    + `<label class="proxy-eigen-anzahl"><span>Zusätzlich drucken</span><input class="feld proxy-zahl" type="number"`
    + ` inputmode="numeric" min="0" max="${ANZAHL_MAX}" step="1" value="${n}" data-zusatz="${esc(e.id)}"`
    + ` aria-label="Anzahl zusätzlich: ${esc(e.name)}"></label>`
    + (e.karte ? `<button type="button" class="knopf" data-vorgabe="${esc(e.id)}" data-an="${e.vorgabe ? '0' : '1'}">`
      + `${e.vorgabe ? 'Nicht mehr automatisch' : 'Automatisch verwenden'}</button>` : '')
    + `<button type="button" class="knopf proxy-weg" data-eigen-weg="${esc(e.id)}"`
    + ` aria-label="Bild ${esc(e.name)} entfernen">Entfernen</button></div></div></li>`;
}

export function zeichnen(z) {
  const wurzel = z.teil.eigene;
  if (!wurzel) return;
  const liste = z.daten.eigene ?? [];
  const entwuerfe = liste.filter((e) => e.entwurf);
  const fertig = liste.filter((e) => !e.entwurf);
  const summe = zusatzSumme(z);
  const offen = wurzel.querySelector('details.proxy-eigen-alle')?.open;
  wurzel.innerHTML = '<h2 class="proxy-titel">Eigene Bilder</h2>'
    + '<p class="proxy-hinweis">Eigene Kartenbilder statt Scryfall, zum Beispiel Alter-Art oder Tokens. Mit Kartennamen'
    + ' ersetzt ein Bild diese Karte in der Liste; jedes Bild lässt sich außerdem zusätzlich drucken.</p>'
    + bildEingabe('proxy-eigen-datei', 'Bild hochladen', 'data-neu')
    + '<div class="proxy-zeile"><input id="proxy-eigen-url" class="feld proxy-eigen-url" type="url" inputmode="url"'
    + ' placeholder="Adresse eines Bildes (https://…)" aria-label="Adresse eines Bildes" autocomplete="off"'
    + ' autocapitalize="off" spellcheck="false"><button type="button" class="knopf" data-url-laden>Laden</button></div>'
    + '<p class="proxy-hinweis">PNG, JPEG oder WebP, höchstens 40 MB. Das Bild wird auf 63 × 88 mm eingepasst;'
    + ' ein Beschnittrand von 1/8 Zoll (z. B. MPC) wird erkannt und entfernt.</p>'
    + `<p class="proxy-hinweis" role="status" data-eigen-status>${esc(z.eigenStatus || '')}</p>`
    + '<p class="meldung" role="alert" data-eigen-fehler></p>'
    + entwuerfe.map((e) => entwurfHtml(e, liste)).join('')
    + (fertig.length
      ? `<details class="proxy-mehr proxy-eigen-alle"${offen || summe ? ' open' : ''}><summary>Bibliothek (${fertig.length}`
        + `${summe ? `, ${summe} zusätzlich im nächsten PDF` : ''})</summary>`
        + `<ul class="proxy-eigen-liste">${fertig.map((e) => eintragHtml(z, e)).join('')}</ul></details>`
      : '');
}

// ---------------------------------------------------------------- Aktionen

function status(z, text) {
  z.eigenStatus = text;
  const s = z.teil.eigene?.querySelector('[data-eigen-status]');
  if (s) s.textContent = text;
}

function fehler(z, text) {
  const f = z.teil.eigene?.querySelector('[data-eigen-fehler]');
  if (f) f.textContent = text || '';
}

async function neuLaden(z, aktuell) {
  try {
    const antwort = await holen('/api/proxy/eigene');
    if (!aktuell()) return;
    z.daten.eigene = antwort.eigene ?? [];
  } catch { /* alte Liste bleibt */ }
  for (const id of Object.keys(z.zusatz)) if (!z.daten.eigene.some((e) => e.id === id && !e.entwurf)) delete z.zusatz[id];
  if (aktuell()) z.geaendert();
}

/** Ein Aufruf mit Statuszeile und Fehlermeldung; danach Liste neu laden. */
async function aktion(z, aktuell, text, ausfuehren) {
  fehler(z, '');
  status(z, text);
  try {
    await ausfuehren();
    if (!aktuell()) return;
    status(z, '');
  } catch (f) {
    if (!aktuell()) return;
    status(z, '');
    await neuLaden(z, aktuell);
    fehler(z, fehlerSatz(f));
    return;
  }
  await neuLaden(z, aktuell);
}

async function dateiPruefen(datei) {
  if (!/^image\/(png|jpeg|webp)$/.test(datei.type)) throw new Error('Bitte ein PNG-, JPEG- oder WebP-Bild wählen.');
  if (datei.size > 40 * 1048576) throw new Error('Das Bild ist zu groß, höchstens 40 MB.');
  try {
    return await verkleinern(datei);
  } catch {
    throw new Error('Das Bild ließ sich nicht verkleinern. Bitte ein kleineres Bild wählen (höchstens 12 MB).');
  }
}

/** Ereignisse der Tafel „Eigene Bilder“ und der Auswahl je Zeile in der Vorschau (am ganzen Bereich). */
export function verdrahten(z, bereich, aktuell) {
  bereich.addEventListener('click', async (ev) => {
    const k = ev.target.closest('button');
    if (!k || k.disabled || !z.teil.eigene?.contains(k)) return;
    const d = k.dataset;
    if (d.urlLaden !== undefined) {
      const feld = z.teil.eigene.querySelector('#proxy-eigen-url');
      const url = feld.value.trim();
      if (!url) { fehler(z, 'Bitte eine Adresse einfügen.'); return; }
      await aktion(z, aktuell, 'Bild wird geladen und eingepasst …',
        () => senden('/api/proxy/eigene/url', { daten: { url } }));
    } else if (d.hintenUrl) {
      const url = z.teil.eigene.querySelector(`[data-hinten-url-feld="${CSS.escape(d.hintenUrl)}"]`)?.value.trim();
      if (!url) { fehler(z, 'Bitte eine Adresse einfügen.'); return; }
      await aktion(z, aktuell, 'Zweite Seite wird geladen …',
        () => senden(`/api/proxy/eigene/${encodeURIComponent(d.hintenUrl)}/hinten/url`, { daten: { url } }));
    } else if (d.speichern) {
      const id = d.speichern;
      const w = z.teil.eigene;
      const daten = { name: w.querySelector(`#proxy-eigen-name-${id}`).value, karte: w.querySelector(`#proxy-eigen-karte-${id}`).value,
        vorgabe: w.querySelector(`#proxy-eigen-vorgabe-${id}`).checked, fertig: true };
      await aktion(z, aktuell, 'Wird gespeichert …',
        () => senden(`/api/proxy/eigene/${encodeURIComponent(id)}`, { methode: 'PATCH', daten }));
    } else if (d.verwerfen || d.eigenWeg) {
      const id = d.verwerfen || d.eigenWeg;
      const e = (z.daten.eigene ?? []).find((x) => x.id === id);
      if (d.eigenWeg) {
        const ja = await bestaetigen({ titel: 'Bild entfernen?', text: `„${e?.name ?? ''}“ wird aus der Bibliothek gelöscht.`,
          ja: 'Entfernen', gefahr: true });
        if (!ja || !aktuell()) return;
      }
      delete z.zusatz[id];
      for (const [schl, wahl] of Object.entries(z.eigeneWahl)) if (wahl === id) delete z.eigeneWahl[schl];
      await aktion(z, aktuell, '', () => senden(`/api/proxy/eigene/${encodeURIComponent(id)}`, { methode: 'DELETE' }));
    } else if (d.hintenWeg) {
      await aktion(z, aktuell, '', () => senden(`/api/proxy/eigene/${encodeURIComponent(d.hintenWeg)}/hinten`,
        { methode: 'DELETE' }));
    } else if (d.vorgabe) {
      await aktion(z, aktuell, '', () => senden(`/api/proxy/eigene/${encodeURIComponent(d.vorgabe)}`,
        { methode: 'PATCH', daten: { vorgabe: d.an === '1' } }));
    }
  });
  bereich.addEventListener('change', async (ev) => {
    const t = ev.target;
    if (t.dataset.eigenZeile !== undefined) {   // Auswahl je Zeile in der Vorschau
      z.eigeneWahl[t.dataset.eigenZeile] = t.value;
      z.geaendert({ nurVorschau: true });
      return;
    }
    if (!z.teil.eigene?.contains(t)) return;
    if (t.dataset.zusatz) {
      const n = Math.max(0, Math.min(ANZAHL_MAX, Math.round(Number(t.value) || 0)));
      t.value = String(n);
      if (n) z.zusatz[t.dataset.zusatz] = n; else delete z.zusatz[t.dataset.zusatz];
      z.geaendert({ nurAuftrag: true });
    } else if (t.dataset.zuschnitt) {
      const { id } = t.dataset;
      await aktion(z, aktuell, 'Wird neu eingepasst …', () => senden(`/api/proxy/eigene/${encodeURIComponent(id)}`,
        { methode: 'PATCH', daten: { zuschnitt: { [t.dataset.zuschnitt]: t.value } } }));
    } else if (t.type === 'file') {
      const datei = t.files?.[0];
      const hinten = t.dataset.hinten;
      t.value = '';
      if (!datei) return;
      let hoch;
      try {
        status(z, datei.size > UNVERAENDERT ? 'Bild wird verkleinert …' : '');
        hoch = await dateiPruefen(datei);
      } catch (f) {
        if (aktuell()) { status(z, ''); fehler(z, f.message); }
        return;
      }
      if (!aktuell()) return;
      const name = encodeURIComponent(datei.name);
      await aktion(z, aktuell, 'Wird hochgeladen und eingepasst …', () => (hinten
        ? senden(`/api/proxy/eigene/${encodeURIComponent(hinten)}/hinten?name=${name}`, hoch)
        : senden(`/api/proxy/eigene?name=${name}`, hoch)));
    }
  });
  bereich.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && ev.target.id === 'proxy-eigen-url') {
      ev.preventDefault();
      z.teil.eigene.querySelector('[data-url-laden]')?.click();
    }
  });
}
