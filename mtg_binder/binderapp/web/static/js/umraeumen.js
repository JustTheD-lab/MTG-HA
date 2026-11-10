// Umräumliste (Paket 5, 10.11.2026): Karten, die nach den heutigen Sortierregeln in eine andere Kiste gehören.
// Abhaken wie im Deckbau (der Haken wird sofort im Add-on gespeichert); „Fertig“ verschiebt alle abgehakten Karten
// digital als ein Schritt. Die echten Karten räumst du von Hand um. Ausgenommen: Scan-Eingang und „Zurückgestellt“;
// Binder und Decks bleiben.
//
//   oeffnen({ kisteId, kisteName, schreiben })  → Promise mit dem Ergebnis von „Fertig“ ({schritt, karten, …})
//                                                 oder undefined (geschlossen ohne Fertig)
//
// Ohne `kisteId` (Gesamtsammlung) gibt es nur „Alle Kisten“. Ohne `schreiben` (nicht angemeldet) nur lesen.

import { esc, senden, vorliebe } from './gemeinsam.js';
import { dialog, bestaetigen } from './dialog.js';

const zahl = (n) => Number(n ?? 0).toLocaleString('de-DE');
const karten = (n) => `${zahl(n)} ${n === 1 ? 'Karte' : 'Karten'}`;

function el(tag, klasse = '', html = '') {
  const e = document.createElement(tag);
  if (klasse) e.className = klasse;
  if (html) e.innerHTML = html;
  return e;
}

function zeileHtml(e) {
  const druck = [e.set_code?.toUpperCase(), e.nummer, e.sprache && e.sprache !== 'en' ? e.sprache.toUpperCase() : '']
    .filter(Boolean).join(' · ');
  const ziel = `${esc(e.nach ?? '?')}${e.fach ? ` <span class="um-fach">· Fach ${esc(e.fach)}</span>` : ''}`;
  return `<li class="um-zeile${e.abgehakt ? ' um-zeile--ab' : ''}">`
    + `<label class="um-label"><input type="checkbox" class="um-haken" data-id="${e.karte_id}"${e.abgehakt ? ' checked' : ''}>`
    + `<span class="um-karte"><span class="um-name">${e.anzahl > 1 ? `<span class="um-anzahl">${zahl(e.anzahl)}×</span> ` : ''}`
    + `${esc(e.name ?? '?')}${e.foil ? '<span class="um-foil">Foil</span>' : ''}</span>`
    + `<span class="um-druck">${esc(druck)}</span></span>`
    + `<span class="um-ziel"><span class="um-wohin"><span class="um-pfeil" aria-hidden="true">→</span> ${ziel}</span>`
    + `<small class="um-regel">Regel „${esc(e.regel ?? '')}“</small></span></label></li>`;
}

export function oeffnen({ kisteId = null, kisteName = '', schreiben = false } = {}) {
  const z = { alle: !kisteId || vorliebe('umraeumen.alle') === '1', d: null, nr: 0, laeuft: false };
  const wurzel = el('div', 'um', `
    <div class="um-wahl" role="group" aria-label="Umfang"${kisteId ? '' : ' hidden'}>
      <button type="button" class="knopf um-pille" data-alle="0">Diese Kiste</button>
      <button type="button" class="knopf um-pille" data-alle="1">Alle Kisten</button>
    </div>
    <p class="um-erkl">Nach den heutigen Sortierregeln gehören diese Karten in eine andere Kiste. Räum sie von Hand um
      und hak sie ab; „Fertig“ verschiebt die abgehakten Karten auch in der App (mit Rückgängig). Scan-Eingang,
      „Zurückgestellt“, Binder und Decks bleiben, wie sie sind.</p>
    <p class="meldung um-meldung" role="alert"></p>
    <div class="um-summe" aria-live="polite"></div>
    <div class="um-gruppen"></div>`);
  const d = dialog({ titel: 'Umräumen', klasse: 'um-dialog', voll: true, inhalt: wurzel });
  const meldung = wurzel.querySelector('.um-meldung');
  const summe = wurzel.querySelector('.um-summe');
  const gruppen = wurzel.querySelector('.um-gruppen');
  const fertigKnopf = el('button', 'knopf knopf-haupt um-fertig', 'Fertig');
  fertigKnopf.type = 'button';
  fertigKnopf.hidden = !schreiben;
  const zu = el('button', 'knopf', 'Schließen');
  zu.type = 'button';
  zu.addEventListener('click', () => d.schliessen(undefined));
  d.fuss.append(zu, fertigKnopf);

  const bereich = () => (z.alle || !kisteId ? null : kisteId);

  function pillen() {
    for (const p of wurzel.querySelectorAll('.um-pille')) {
      p.setAttribute('aria-pressed', String((p.dataset.alle === '1') === z.alle));
    }
  }

  function zeichnen() {
    const daten = z.d;
    const ab = daten.abgehakt ?? { posten: 0, karten: 0 };
    const bleibt = [
      daten.wartet ? `${zahl(daten.wartet)} warten auf Preis oder Kartendaten` : '',
      daten.pruefen ? `${zahl(daten.pruefen)} erst prüfen` : '',
    ].filter(Boolean).join(', ');
    const wo = bereich() ? `in „${esc(daten.kiste ?? kisteName)}“` : 'in allen Kisten';
    summe.innerHTML = daten.posten
      ? `<strong>${esc(karten(daten.karten))}</strong> ${wo} gehören woanders hin`
        + `${daten.posten !== daten.karten ? ` (${zahl(daten.posten)} Posten)` : ''}; abgehakt: ${esc(karten(ab.karten))}.`
        + `${bleibt ? ` <span class="um-rest">Nicht bewertet: ${esc(bleibt)}.</span>` : ''}`
        + `${daten.gekuerzt ? ' <span class="um-rest">Die Liste ist gekürzt.</span>' : ''}`
      : `Alles liegt richtig ${wo}.${bleibt ? ` <span class="um-rest">Nicht bewertet: ${esc(bleibt)}.</span>` : ''}`;
    gruppen.innerHTML = daten.gruppen.map((g) => `<section class="um-gruppe">`
      + `<h3 class="um-gruppe-titel">Aus „${esc(g.kiste ?? '?')}“ <span class="um-gruppe-zahl">${esc(karten(g.karten))}</span></h3>`
      + `<ul class="um-liste">${g.posten.map(zeileHtml).join('')}</ul></section>`).join('');
    for (const h of gruppen.querySelectorAll('.um-haken')) h.disabled = !schreiben;
    fertigKnopf.disabled = !ab.posten;
    fertigKnopf.textContent = ab.karten ? `Fertig (${zahl(ab.karten)})` : 'Fertig';
  }

  async function laden() {
    const nr = ++z.nr;
    meldung.textContent = '';
    pillen();
    summe.textContent = 'Lädt …';
    let daten;
    try {
      daten = await senden(`/api/regeln/umraeumen${bereich() ? `?kiste=${bereich()}` : ''}`, { methode: 'GET' });
    } catch (f) {
      if (nr === z.nr) { summe.textContent = ''; meldung.textContent = f.message || 'Die Liste ließ sich nicht laden.'; }
      return;
    }
    if (nr !== z.nr) return;
    z.d = daten;
    zeichnen();
  }

  wurzel.querySelector('.um-wahl').addEventListener('click', (e) => {
    const p = e.target.closest('.um-pille');
    if (!p) return;
    const alle = p.dataset.alle === '1';
    if (alle === z.alle) return;
    z.alle = alle;
    vorliebe('umraeumen.alle', alle ? '1' : '0');
    laden();
  });

  gruppen.addEventListener('change', async (e) => {
    const h = e.target.closest('.um-haken');
    if (!h || !z.d) return;
    const id = Number(h.dataset.id);
    const eintrag = z.d.gruppen.flatMap((g) => g.posten).find((x) => x.karte_id === id);
    if (!eintrag) return;
    const neu = h.checked;
    h.disabled = true;
    try {
      await senden('/api/regeln/umraeumen/haken', { daten: { karte_id: id, abgehakt: neu } });
    } catch (f) {
      h.checked = !neu;
      h.disabled = false;
      meldung.textContent = f.message;
      if (f.status === 409) laden();
      return;
    }
    h.disabled = false;
    eintrag.abgehakt = neu;
    const ab = z.d.abgehakt;
    ab.posten += neu ? 1 : -1;
    ab.karten += (neu ? 1 : -1) * eintrag.anzahl;
    h.closest('.um-zeile')?.classList.toggle('um-zeile--ab', neu);
    zeichnen();
    gruppen.querySelector(`.um-haken[data-id="${id}"]`)?.focus();
  });

  fertigKnopf.addEventListener('click', async () => {
    const ab = z.d?.abgehakt;
    if (!ab?.posten || z.laeuft) return;
    const ja = await bestaetigen({
      titel: 'Fertig?',
      text: `${karten(ab.karten)} wandern in der App in ihre neue Kiste. Danach kannst du das noch rückgängig machen.`,
      ja: 'Verschieben',
    });
    if (!ja) return;
    z.laeuft = true;
    fertigKnopf.disabled = true;
    try {
      const a = await senden('/api/regeln/umraeumen/fertig', { daten: { kiste: bereich() } });
      d.schliessen(a);
    } catch (f) {
      meldung.textContent = f.message;
      z.laeuft = false;
      laden();
    }
  });

  pillen();
  laden();
  zu.focus();
  return d.fertig;
}
