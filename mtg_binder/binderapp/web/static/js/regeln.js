// Sortierregeln (#/regeln): Wohin eine neu erkannte Karte einsortiert wird.
//
// Die Regeln gelten von oben nach unten, die erste passende bestimmt die Kiste. Passt keine, bleibt die Karte im
// Scan-Eingang. Die Seite zeigt die Regeln als Karten „Wenn … → Kiste“ (ein-/ausschalten, Reihenfolge, Bearbeiten,
// Löschen) und eine Probe: eine Karte der Sammlung wählen und sehen, welche Regel für sie greift und warum.
// Bedingungen lassen sich kopieren (alle an der Regelkarte und im Editor, einzeln an jeder Bedingung) und in jedem
// Editor einfügen, auch in den Fächern einer Kiste (Wunsch des Nutzers, 08.11.2026).
//
// Weitere Ausgänge für andere Ansichten:
//   bedingungenEditor(container, beschreibung, bedingungen, { leerText }) → { werte(), hinzufuegen() }
//       Editor für eine Liste von Bedingungen (Regeln und Fächer), Werte im Format der Schnittstelle.
//   faecherBearbeiten(kisteId, kisteName) → Promise<boolean>   Dialog „Fächer“ einer Kiste (true = gespeichert)
//
// Schreiben nur angemeldet (im Add-on ist man immer angemeldet). Fehler der Schnittstelle erscheinen als Satz.

import { holen, senden, esc, zurueck } from './gemeinsam.js';
import { knopf as anmeldeKnopf, angemeldet } from './anmeldung.js';
import { dialog, bestaetigen } from './dialog.js';

const sym = (d, g = 18) => `<svg viewBox="0 0 24 24" width="${g}" height="${g}" fill="none" stroke="currentColor"`
  + ` stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${d}</svg>`;
const SYM = {
  zurueck: sym('<path d="M15 5l-7 7 7 7"/>', 20),
  stift: sym('<path d="M5 19l1-4L16.5 4.5a2 2 0 0 1 3 3L9 18z"/>'),
  muell: sym('<path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12"/>'),
  plus: sym('<path d="M12 5v14M5 12h14"/>'),
  kreuz: sym('<path d="M6 6l12 12M18 6L6 18"/>'),
  hoch: sym('<path d="M12 19V6M6 12l6-6 6 6"/>'),
  runter: sym('<path d="M12 5v13M6 12l6 6 6-6"/>'),
  kopieren: sym('<rect x="8" y="8" width="11" height="11" rx="2"/><path d="M5 15V6a1 1 0 0 1 1-1h9"/>'),
  einfuegen: sym('<rect x="5" y="5" width="14" height="15" rx="2"/><path d="M9 5V3.5h6V5M9 11h6M9 15h4"/>'),
};

let Z = null;          // Zustand der gerade gezeigten Seite
let zaehler = 0;       // für eindeutige Namen von Auswahlgruppen

// ---------------------------------------------------------------- Ablage für Bedingungen
//
// „Bedingungen kopieren“ legt die Bedingungen einer Regel oder eines Fachs hier ab, „Bedingungen einfügen“ hängt sie in
// jedem Editor an (Regeln, Fächer jeder Kiste). Die Ablage lebt nur bis zum Neuladen der Seite; sie schreibt nichts.

let ablage = null;     // { quelle, bedingungen } oder null

const anzahlText = (n) => `${n} ${n === 1 ? 'Bedingung' : 'Bedingungen'}`;

function ablageSetzen(bedingungen, quelle) {
  ablage = bedingungen.length ? { quelle, bedingungen: JSON.parse(JSON.stringify(bedingungen)) } : null;
  window.dispatchEvent(new CustomEvent('binder:ablage'));
}

function el(tag, klasse, html) {
  const e = document.createElement(tag);
  if (klasse) e.className = klasse;
  if (html !== undefined) e.innerHTML = html;
  return e;
}

function knopfEl(klasse, html, label) {
  const b = el('button', klasse, html);
  b.type = 'button';
  if (label) b.setAttribute('aria-label', label);
  return b;
}

const an = (knopf) => knopf.getAttribute('aria-pressed') === 'true';

/** Umschaltknopf in Pillenform (aria-pressed). */
function pille(text, gedrueckt, vorsatz = '') {
  const p = knopfEl('knopf regeln-pille', `${vorsatz}<span>${esc(text)}</span>`);
  p.setAttribute('aria-pressed', String(Boolean(gedrueckt)));
  return p;
}

function umschalten(knopf) {
  knopf.setAttribute('aria-pressed', String(!an(knopf)));
}

// ---------------------------------------------------------------- Editor für Bedingungen

function zahlFeld(titel, wert) {
  const l = el('label', 'regeln-zahl');
  l.append(el('span', 'regeln-zahl-titel', esc(titel)));
  const i = el('input', 'feld');
  i.type = 'number';
  i.step = 'any';
  i.min = '0';
  i.inputMode = 'decimal';
  i.placeholder = 'egal';
  i.value = wert ?? '';
  l.append(i);
  return [l, i];
}

const ohneLeer = (i) => (i.value.trim() === '' ? null : i.value.trim());

/** Eingaben je Art des Feldes. Jede Funktion füllt `k` und gibt die Lesefunktion zurück (Werte ohne `feld`, `nicht`). */
const BAUER = {
  farbe(k, feld, spec, b, w) {
    const farbart = el('select', 'feld regeln-farbart');
    farbart.setAttribute('aria-label', `${spec.name}: Art`);
    farbart.innerHTML = Object.entries(b.farbarten).map(([wert, text]) => `<option value="${esc(wert)}">${esc(text)}</option>`).join('');
    farbart.value = w.art && b.farbarten[w.art] ? w.art : 'genau';
    const pillen = el('div', 'regeln-pillen');
    pillen.setAttribute('role', 'group');
    pillen.setAttribute('aria-label', `${spec.name}: Farben`);
    const gewaehlt = String(w.farben ?? '').toUpperCase();
    for (const [code, name] of Object.entries(b.farben)) {
      const p = pille(name, gewaehlt.includes(code), `<span class="regeln-farbpunkt regeln-farbe-${esc(code)}" aria-hidden="true"></span>`);
      p.dataset.farbe = code;
      p.addEventListener('click', () => umschalten(p));
      pillen.append(p);
    }
    const zeigen = () => { pillen.hidden = !['genau', 'enthaelt'].includes(farbart.value); };
    farbart.addEventListener('change', zeigen);
    zeigen();
    k.append(farbart, pillen);
    return () => {
      const o = { art: farbart.value };
      if (['genau', 'enthaelt'].includes(o.art)) {
        o.farben = [...pillen.querySelectorAll('[data-farbe]')].filter(an).map((p) => p.dataset.farbe).join('');
      }
      return o;
    };
  },

  bereich(k, feld, spec, b, w) {
    const [lv, von] = zahlFeld('von', w.von);
    const [lb, bis] = zahlFeld('bis', w.bis);
    const reihe = el('div', 'regeln-zahlen');
    reihe.append(lv, lb);
    k.append(reihe);
    return () => ({ von: ohneLeer(von), bis: ohneLeer(bis) });
  },

  auswahl(k, feld, spec, b, w) {
    const werte = new Set(w.werte ?? []);
    const pillen = el('div', 'regeln-pillen');
    pillen.setAttribute('role', 'group');
    pillen.setAttribute('aria-label', spec.name);
    const einzeln = Object.entries(spec.werte).map(([wert, text]) => {
      const p = pille(text, werte.has(wert));
      p.dataset.wert = wert;
      pillen.append(p);
      return p;
    });
    let sammel = null;
    const sonder = feld === 'besonderheit' ? (b.foil_sonderarten ?? []).filter((x) => x in spec.werte) : [];
    const abgleichen = () => {
      for (const p of einzeln) p.setAttribute('aria-pressed', String(werte.has(p.dataset.wert)));
      if (sammel) sammel.setAttribute('aria-pressed', String(sonder.every((x) => werte.has(x))));
    };
    for (const p of einzeln) {
      p.addEventListener('click', () => {
        if (werte.has(p.dataset.wert)) werte.delete(p.dataset.wert); else werte.add(p.dataset.wert);
        abgleichen();
      });
    }
    if (sonder.length) {
      sammel = pille('Foil-Sonderarten', false);
      sammel.classList.add('regeln-sammel');
      sammel.title = `Wählt auf einmal: ${sonder.map((x) => spec.werte[x]).join(', ')}`;
      sammel.addEventListener('click', () => {
        const alle = sonder.every((x) => werte.has(x));
        for (const x of sonder) { if (alle) werte.delete(x); else werte.add(x); }
        abgleichen();
      });
      k.append(sammel, el('p', 'regeln-hinweis regeln-sammel-hinweis', `Foil-Sonderarten: ${esc(sonder.map((x) => spec.werte[x]).join(', '))}.`));
    }
    k.append(pillen);
    abgleichen();
    return () => ({ werte: Object.keys(spec.werte).filter((x) => werte.has(x)) });
  },

  liste(k, feld, spec, b, w) {
    const i = el('input', 'feld');
    i.type = 'text';
    i.autocomplete = 'off';
    i.autocapitalize = 'off';
    i.spellcheck = false;
    i.placeholder = 'Kürzel, getrennt durch Komma, z. B. sld, plst';
    i.setAttribute('aria-label', `${spec.name}: Kürzel`);
    i.value = (w.werte ?? []).join(', ');
    k.append(i);
    return () => ({ werte: i.value.split(/[\s,;]+/).map((x) => x.trim().toLowerCase()).filter(Boolean) });
  },

  text(k, feld, spec, b, w) {
    const i = el('input', 'feld');
    i.type = 'text';
    i.maxLength = 60;
    i.autocomplete = 'off';
    i.spellcheck = false;
    i.placeholder = 'Text (ohne Groß- und Kleinschreibung)';
    i.setAttribute('aria-label', spec.name);
    i.value = w.text ?? '';
    k.append(i);
    return () => ({ text: i.value });
  },

  ja(k, feld, spec) {
    k.append(el('p', 'regeln-hinweis', `Trifft auf Karten mit „${esc(spec.name)}“ zu; mit „nicht“ auf alle ohne.`));
    return () => ({});
  },

  preis(k, feld, spec, b, w) {
    const wert = el('select', 'feld regeln-farbart');
    wert.setAttribute('aria-label', 'Preis: Art');
    wert.innerHTML = Object.entries(b.preiswerte).map(([x, text]) => `<option value="${esc(x)}">${esc(text)}</option>`).join('');
    // neue Bedingungen folgen der Preisart aus dem Dashboard; gespeicherte behalten ihre Art
    wert.value = w.wert && b.preiswerte[w.wert] ? w.wert : (b.preiswerte.einstellung ? 'einstellung' : 'avg7');
    const [la, ab] = zahlFeld('ab €', w.ab);
    const [lu, unter] = zahlFeld('unter €', w.unter);
    const reihe = el('div', 'regeln-zahlen');
    reihe.append(la, lu);
    k.append(wert, reihe);
    return () => ({ wert: wert.value, ab: ohneLeer(ab), unter: ohneLeer(unter) });
  },
};

/** Baut den Editor in `container`. `bedingungen` im Format der Schnittstelle (leer = keine).
    `quelle()` nennt beim Kopieren, woher die Bedingungen stammen (z. B. „Fach „Länder““). */
export function bedingungenEditor(container, beschreibung, bedingungen = [],
  { leerText = 'Keine Bedingung: Das passt immer.', quelle = () => 'diesem Editor' } = {}) {
  const felder = beschreibung.felder;
  const liste = el('div', 'regeln-bed-liste');
  const leer = el('p', 'regeln-hinweis regeln-bed-leer', esc(leerText));
  const knoepfe = el('div', 'regeln-bed-knoepfe');
  const neu = knopfEl('knopf regeln-bed-neu', `${SYM.plus}<span>Bedingung hinzufügen</span>`);
  const kopieren = knopfEl('knopf regeln-bed-kopieren', `${SYM.kopieren}<span>Alle kopieren</span>`);
  kopieren.title = 'Alle Bedingungen kopieren, um sie in eine andere Regel oder ein Fach einzufügen';
  const einfuegen = knopfEl('knopf regeln-bed-einfuegen', `${SYM.einfuegen}<span>Einfügen</span>`);
  const status = el('p', 'regeln-hinweis regeln-bed-status');
  status.setAttribute('role', 'status');
  knoepfe.append(neu, kopieren, einfuegen);
  container.append(liste, leer, knoepfe, status);
  const zeilen = [];
  const zeileWert = (z) => ({ feld: z.wahl.value, ...z.lesen(), ...(an(z.nicht) ? { nicht: true } : {}) });

  const ablageZeigen = () => {
    einfuegen.hidden = !ablage;
    if (!ablage) return;
    const was = ablage.bedingungen.length === 1 ? `Bedingung „${felder[ablage.bedingungen[0].feld]?.name ?? '?'}“`
      : anzahlText(ablage.bedingungen.length);
    einfuegen.setAttribute('aria-label', `${was} aus ${ablage.quelle} einfügen`);
    einfuegen.title = `${was} aus ${ablage.quelle} einfügen`;
  };
  // Der Editor hört auf neue Ablagen (anderes Fach im selben Dialog). Ist er nicht mehr im Dokument, meldet er sich ab.
  const aufAblage = () => {
    if (!container.isConnected) { window.removeEventListener('binder:ablage', aufAblage); return; }
    ablageZeigen();
  };
  window.addEventListener('binder:ablage', aufAblage);

  const nummerieren = () => {
    zeilen.forEach((z, i) => {
      z.wahl.setAttribute('aria-label', `Feld der Bedingung ${i + 1}`);
      z.nicht.setAttribute('aria-label', `Bedingung ${i + 1} verneinen`);
      z.kopie.setAttribute('aria-label', `Bedingung ${i + 1} kopieren`);
      z.weg.setAttribute('aria-label', `Bedingung ${i + 1} entfernen`);
    });
    leer.hidden = zeilen.length > 0;
    kopieren.disabled = zeilen.length === 0;
  };

  function hinzufuegen(start = null) {
    const startFeld = start && felder[start.feld] ? start.feld : Object.keys(felder)[0];
    const wurzel = el('div', 'regeln-bed');
    const kopf = el('div', 'regeln-bed-kopf');
    const wahl = el('select', 'feld regeln-bed-feld');
    wahl.innerHTML = Object.entries(felder).map(([f, s]) => `<option value="${esc(f)}">${esc(s.name)}</option>`).join('');
    wahl.value = startFeld;
    const nicht = pille('nicht', Boolean(start?.nicht));
    nicht.classList.add('regeln-nicht');
    nicht.title = 'Bedingung umkehren: Die Karte darf nicht so sein';
    nicht.addEventListener('click', () => umschalten(nicht));
    const kopie = knopfEl('knopf knopf-sym regeln-bed-kopie', SYM.kopieren);
    kopie.title = 'Nur diese Bedingung kopieren';
    const weg = knopfEl('knopf knopf-sym regeln-bed-weg', SYM.kreuz);
    kopf.append(wahl, nicht, kopie, weg);
    const koerper = el('div', 'regeln-bed-koerper');
    wurzel.append(kopf, koerper);
    const z = { wurzel, wahl, nicht, kopie, weg, lesen: () => ({}) };
    const bauen = (feld, wert) => {
      koerper.replaceChildren();
      z.lesen = BAUER[felder[feld].art](koerper, feld, felder[feld], beschreibung, wert ?? {});
    };
    wahl.addEventListener('change', () => bauen(wahl.value, null));
    kopie.addEventListener('click', () => {
      ablageSetzen([zeileWert(z)], quelle());
      status.textContent = `Bedingung „${felder[wahl.value].name}“ kopiert. Einfügen geht in jeder Regel und in den Fächern jeder Kiste.`;
    });
    weg.addEventListener('click', () => {
      zeilen.splice(zeilen.indexOf(z), 1);
      wurzel.remove();
      nummerieren();
      (zeilen[zeilen.length - 1]?.wahl ?? neu).focus();
    });
    bauen(startFeld, start);
    zeilen.push(z);
    liste.append(wurzel);
    nummerieren();
    return z;
  }

  const werte = () => zeilen.map(zeileWert);

  neu.addEventListener('click', () => { status.textContent = ''; hinzufuegen().wahl.focus(); });
  kopieren.addEventListener('click', () => {
    const w = werte();
    if (!w.length) return;
    ablageSetzen(w, quelle());
    status.textContent = `${anzahlText(w.length)} kopiert. Einfügen geht in jeder Regel und in den Fächern jeder Kiste.`;
  });
  einfuegen.addEventListener('click', () => {
    if (!ablage) return;
    const passend = ablage.bedingungen.filter((b) => felder[b.feld]);
    const erste = passend.map((b) => hinzufuegen(b))[0];
    const fehlt = ablage.bedingungen.length - passend.length;
    status.textContent = `${anzahlText(passend.length)} eingefügt.`
      + (fehlt ? ` ${anzahlText(fehlt)} mit unbekanntem Feld ${fehlt === 1 ? 'ließ' : 'ließen'} sich nicht einfügen.` : '');
    erste?.wahl.focus();
  });
  for (const b of bedingungen) { if (felder[b.feld]) hinzufuegen(b); }
  nummerieren();
  ablageZeigen();

  return { hinzufuegen, werte };
}

// ---------------------------------------------------------------- Seite „Sortierregeln“

function meldung(text) {
  if (Z) Z.meldung.textContent = text || '';
}

function textKette(texte) {
  return texte.length
    ? texte.map((t) => `<span class="regeln-chip">${esc(t)}</span>`).join('')
    : '<span class="regeln-chip regeln-chip-immer">immer</span>';
}

function regelHtml(r, i, gesamt, schreiben) {
  const ziel = r.ziel
    ? `<span class="regeln-zielname">${esc(r.ziel)}</span>`
    : '<span class="regeln-zielname regeln-fehlt">Zielkiste fehlt</span>';
  const marken = (!r.gueltig ? '<span class="regeln-marke regeln-marke-warn">Ungültig</span>' : '')
    + (!r.aktiv ? '<span class="regeln-marke">aus</span>' : '');
  const note = !r.gueltig
    ? '<p class="regeln-warnung" role="note">Diese Regel lässt sich nicht mehr lesen und wird übersprungen. Bearbeite sie neu oder lösche sie.</p>'
    : (!r.ziel ? '<p class="regeln-warnung" role="note">Die Zielkiste gibt es nicht mehr. Wähle beim Bearbeiten eine andere.</p>' : '');
  const name = esc(r.name);
  const aktionen = schreiben
    ? `<div class="regeln-aktionen">`
      + `<button type="button" class="regeln-schalter" role="switch" aria-checked="${r.aktiv}" data-aktion="schalter"`
      + ` aria-label="Regel „${name}“ ist ${r.aktiv ? 'an' : 'aus'}"><span class="regeln-schalter-spur" aria-hidden="true"></span>`
      + `<span class="regeln-schalter-text">${r.aktiv ? 'An' : 'Aus'}</span></button>`
      + '<span class="regeln-aktionen-rechts">'
      + `<button type="button" class="knopf knopf-sym" data-aktion="hoch" aria-label="Regel „${name}“ nach oben"${i === 0 ? ' disabled' : ''}>${SYM.hoch}</button>`
      + `<button type="button" class="knopf knopf-sym" data-aktion="runter" aria-label="Regel „${name}“ nach unten"${i === gesamt - 1 ? ' disabled' : ''}>${SYM.runter}</button>`
      + `<button type="button" class="knopf knopf-sym" data-aktion="kopieren" aria-label="Bedingungen der Regel „${name}“ kopieren"`
      + ` title="Bedingungen kopieren"${r.gueltig && r.bedingungen.length ? '' : ' disabled'}>${SYM.kopieren}</button>`
      + `<button type="button" class="knopf knopf-sym" data-aktion="bearbeiten" aria-label="Regel „${name}“ bearbeiten">${SYM.stift}</button>`
      + `<button type="button" class="knopf knopf-sym" data-aktion="loeschen" aria-label="Regel „${name}“ löschen">${SYM.muell}</button>`
      + '</span></div>'
    : '';
  return `<li class="regeln-regel" data-id="${r.id}" data-aktiv="${r.aktiv}" data-gueltig="${r.gueltig}">`
    + `<div class="regeln-regel-kopf"><span class="regeln-nr" aria-hidden="true">${i + 1}</span>`
    + `<h3 class="regeln-name">${name}</h3>${marken}</div>`
    + `<div class="regeln-zeile"><span class="etikett">Wenn</span><span class="regeln-ketten">${r.gueltig ? textKette(r.texte) : '<span class="regeln-chip">nicht lesbar</span>'}</span></div>`
    + `<div class="regeln-zeile"><span class="etikett">Dann</span><span class="regeln-ziel"><span aria-hidden="true">→</span> Kiste ${ziel}</span></div>`
    + note + aktionen + '</li>';
}

function listeZeichnen(fokus = null) {
  const { daten, liste, leer } = Z;
  const schreiben = angemeldet();
  const regeln = daten.regeln;
  liste.innerHTML = regeln.map((r, i) => regelHtml(r, i, regeln.length, schreiben)).join('');
  leer.hidden = regeln.length > 0;
  leer.textContent = daten.kisten.length
    ? 'Noch keine Regeln. Ohne Regeln bleiben alle erkannten Karten im Scan-Eingang.'
    : 'Noch keine Regeln. Lege zuerst eine Kiste an (im Regal unter „Kisten“), dann kannst du Regeln dafür schreiben.';
  Z.neueRegel.hidden = !schreiben;
  if (fokus) {
    const zeile = liste.querySelector(`[data-id="${fokus.id}"]`);
    const gegenstueck = { hoch: 'runter', runter: 'hoch' }[fokus.aktion];   // am Rand springt der Fokus zur anderen Richtung
    const ziel = [fokus.aktion, gegenstueck, 'bearbeiten']
      .map((a) => zeile?.querySelector(`[data-aktion="${a}"]:not(:disabled)`)).find(Boolean);
    ziel?.focus();
  }
}

async function nachladen(fokus = null) {
  const z = Z;
  const daten = await holen('/api/regeln');
  if (Z !== z) return;
  z.daten = daten;
  listeZeichnen(fokus);
  if (z.probeKarte) probeLaden();
}

async function aktivSchalten(r) {
  try {
    await senden(`/api/regel/${r.id}`, { methode: 'PUT',
      daten: { name: r.name, aktiv: !r.aktiv, bedingungen: r.bedingungen, kiste_id: r.kiste_id } });
    meldung('');
    await nachladen({ id: r.id, aktion: 'schalter' });
  } catch (f) {
    meldung(f.message);
  }
}

async function verschieben(r, richtung) {
  const ids = Z.daten.regeln.map((x) => x.id);
  const von = ids.indexOf(r.id);
  const nach = von + richtung;
  if (von < 0 || nach < 0 || nach >= ids.length) return;
  [ids[von], ids[nach]] = [ids[nach], ids[von]];
  try {
    const antwort = await senden('/api/regeln/reihenfolge', { daten: { ids } });
    meldung('');
    if (!Z) return;
    Z.daten.regeln = antwort.regeln;
    listeZeichnen({ id: r.id, aktion: richtung < 0 ? 'hoch' : 'runter' });
    if (Z.probeKarte) probeLaden();
  } catch (f) {
    meldung(f.message);
  }
}

async function loeschen(r) {
  const ja = await bestaetigen({ titel: 'Regel löschen', text: `Die Regel „${r.name}“ wird gelöscht. Bereits einsortierte Karten bleiben, wo sie sind.`,
    ja: 'Löschen', gefahr: true });
  if (!ja || !Z) return;
  try {
    await senden(`/api/regel/${r.id}`, { methode: 'DELETE' });
    meldung('');
    await nachladen();
    Z?.neueRegel.focus();
  } catch (f) {
    meldung(f.message);
  }
}

/** Dialog für eine neue (`regel` = null) oder bestehende Regel. Gibt true zurück, wenn gespeichert wurde. */
async function regelDialog(regel) {
  const { kisten, beschreibung } = Z.daten;
  const form = el('form');
  form.id = 'regel-form';
  form.noValidate = true;
  form.autocomplete = 'off';
  form.innerHTML = '<label class="etikett feld-etikett" for="regel-name">Name</label>'
    + '<input id="regel-name" class="feld" type="text" maxlength="60" autocomplete="off" enterkeyhint="done"'
    + ' placeholder="z. B. Rot" spellcheck="false">'
    + '<label class="etikett feld-etikett" for="regel-kiste">Zielkiste</label>'
    + (kisten.length
      ? '<select id="regel-kiste" class="feld"></select>'
      : '<p class="regeln-hinweis" id="regel-kiste-hinweis">Erst eine Kiste anlegen: im Regal unter „Kisten“ mit „Neue Kiste“.</p>')
    + '<label class="regeln-aktivzeile"><input type="checkbox" id="regel-aktiv"><span>Regel ist an</span></label>'
    + '<h3 class="etikett feld-etikett">Bedingungen</h3>'
    + '<p class="regeln-hinweis">Alle Bedingungen müssen zusammen zutreffen. Eine Regel ohne Bedingung passt auf jede Karte.</p>'
    + '<div class="regeln-bed-platz"></div>'
    + '<p class="meldung regeln-dialogmeldung" role="alert" aria-live="assertive"></p>';
  const name = form.querySelector('#regel-name');
  const kiste = form.querySelector('#regel-kiste');
  const aktivBox = form.querySelector('#regel-aktiv');
  const text = form.querySelector('.regeln-dialogmeldung');
  name.value = regel?.name ?? '';
  aktivBox.checked = regel ? regel.aktiv : true;
  if (kiste) {
    kiste.innerHTML = kisten.map((k) => `<option value="${k.id}">${esc(k.name)}</option>`).join('');
    if (regel?.kiste_id && kisten.some((k) => k.id === regel.kiste_id)) kiste.value = String(regel.kiste_id);
  }
  const editor = bedingungenEditor(form.querySelector('.regeln-bed-platz'), beschreibung, regel?.gueltig ? regel.bedingungen : [],
    { leerText: 'Keine Bedingung: Die Regel passt auf jede Karte.',
      quelle: () => (name.value.trim() ? `Regel „${name.value.trim()}“` : 'einer neuen Regel') });

  const d = dialog({ titel: regel ? 'Regel bearbeiten' : 'Neue Regel', klasse: 'regeln-dialog', inhalt: form, voll: true });
  const abbrechen = knopfEl('knopf', 'Abbrechen');
  abbrechen.addEventListener('click', () => d.schliessen(false));
  const speichern = el('button', 'knopf knopf-haupt', 'Speichern');
  speichern.type = 'submit';
  speichern.setAttribute('form', form.id);
  speichern.disabled = !kiste;
  d.fuss.append(abbrechen, speichern);

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!kiste) return;
    speichern.disabled = true;
    text.textContent = '';
    try {
      const daten = { name: name.value.trim(), aktiv: aktivBox.checked, bedingungen: editor.werte(), kiste_id: Number(kiste.value) };
      if (regel) await senden(`/api/regel/${regel.id}`, { methode: 'PUT', daten });
      else await senden('/api/regeln', { daten });
      d.schliessen(true);
    } catch (f) {
      text.textContent = f.message;
      speichern.disabled = false;
    }
  });
  name.focus();
  return (await d.fertig) === true;
}

// ---------------------------------------------------------------- Probe

function karteText(t) {
  const teile = [t.set_code && String(t.set_code).toUpperCase(), t.nummer, t.sprache && String(t.sprache).toUpperCase(),
    t.foil ? 'Foil' : null].filter(Boolean).join(' ');
  return teile;
}

function probeHtml(p, karte) {
  let ziel;
  if (p.ziel) {
    ziel = `<p class="regeln-pziel regeln-pziel-ok"><span aria-hidden="true">→</span> Kiste <strong>${esc(p.ziel.kiste)}</strong>`
      + `${p.ziel.fach ? ` · Fach <strong>${esc(p.ziel.fach)}</strong>` : ''}</p>`;
  } else if (p.wartet) {
    ziel = `<p class="regeln-pziel regeln-pziel-wartet">Wartet: ${esc(p.wartet)}. Die Karte wird erst einsortiert, wenn der Wert da ist.</p>`;
  } else {
    ziel = '<p class="regeln-pziel">Keine Regel passt. Die Karte bleibt im Scan-Eingang.</p>';
  }
  const regeln = p.regeln.map((r) => {
    const bed = r.bedingungen.length
      ? `<ul class="regeln-pbed">${r.bedingungen.map((b) => `<li data-e="${esc(b.ergebnis)}"><span class="regeln-pmarke">${esc(b.ergebnis)}</span> ${esc(b.text)}</li>`).join('')}</ul>`
      : '<p class="regeln-hinweis">Keine Bedingung: passt immer.</p>';
    return `<li class="regeln-pregel" data-e="${esc(r.ergebnis)}"><div class="regeln-pregel-kopf"><strong>${esc(r.regel)}</strong>`
      + `${r.aktiv ? '' : ' <span class="regeln-marke">aus</span>'}`
      + `<span class="regeln-pmarke regeln-pmarke-gross">${esc(r.ergebnis)}</span></div>${bed}</li>`;
  }).join('');
  return `<h3 class="regeln-ptitel">Probe für ${esc(karte.name)}</h3>${ziel}`
    + (regeln ? `<ol class="regeln-pregeln" aria-label="Regeln bis zur passenden">${regeln}</ol>`
      : '<p class="regeln-hinweis">Es gibt keine gültige, eingeschaltete Regel.</p>');
}

async function probeLaden() {
  const z = Z;
  const karte = z.probeKarte;
  if (!karte) return;
  z.ergebnis.setAttribute('aria-busy', 'true');
  try {
    const p = await holen(`/api/regeln/probe?karte=${karte.id}`);
    if (Z !== z || z.probeKarte !== karte) return;
    z.ergebnis.innerHTML = probeHtml(p, karte);
  } catch (f) {
    if (Z !== z) return;
    z.ergebnis.innerHTML = `<p class="meldung" role="alert">${esc(f.message)}</p>`;
  } finally {
    z.ergebnis.removeAttribute('aria-busy');
  }
}

async function suchen() {
  const z = Z;
  const q = z.suche.value.trim();
  const lauf = ++z.suchlauf;
  if (q.length < 2) { z.treffer.replaceChildren(); z.trefferStatus.textContent = ''; return; }
  try {
    const a = await holen(`/api/regeln/suche?q=${encodeURIComponent(q)}`);
    if (Z !== z || lauf !== z.suchlauf) return;
    z.trefferListe = a.treffer;
    z.treffer.innerHTML = a.treffer.map((t, i) => `<li><button type="button" class="regeln-treffer-knopf" data-i="${i}">`
      + `<span class="regeln-treffer-name">${esc(t.name)}</span>`
      + `<span class="regeln-treffer-info">${esc(karteText(t))}${t.kiste ? ` · ${esc(t.kiste)}` : ''}</span></button></li>`).join('');
    z.trefferStatus.textContent = a.treffer.length
      ? `${a.treffer.length} ${a.treffer.length === 1 ? 'Treffer' : 'Treffer'}` : `Keine Karte mit „${q}“ gefunden.`;
  } catch (f) {
    if (Z !== z || lauf !== z.suchlauf) return;
    z.trefferStatus.textContent = f.message;
  }
}

// ---------------------------------------------------------------- Ansicht

export async function zeigen(wurzel) {
  verlassen();
  const bereich = el('section', 'regeln');
  bereich.setAttribute('aria-label', 'Sortierregeln');
  bereich.innerHTML = '<header class="regeln-titelzeile">'
    + `<button type="button" class="knopf knopf-sym regeln-zurueck" aria-label="Zurück ins Regal">${SYM.zurueck}</button>`
    + '<h1 class="regeln-titel">Sortierregeln</h1><div class="regeln-anmelden"></div></header>'
    + '<p class="regeln-erklaerung">Von oben nach unten: Die erste passende Regel bestimmt die Kiste. Passt keine, bleibt die Karte im Scan-Eingang.</p>'
    + '<p class="meldung regeln-meldung" role="alert"></p>'
    + '<p class="regeln-hinweis regeln-status" role="status"></p>'
    + '<div class="regeln-inhalt"></div>';
  wurzel.replaceChildren(bereich);
  bereich.querySelector('.regeln-zurueck').addEventListener('click', () => zurueck('#/'));
  bereich.querySelector('.regeln-anmelden').append(anmeldeKnopf());
  const inhalt = bereich.querySelector('.regeln-inhalt');
  const meldungEl = bereich.querySelector('.regeln-meldung');

  let daten;
  try {
    daten = await holen('/api/regeln');
  } catch (f) {
    inhalt.innerHTML = '<p class="regeln-hinweis">Die Sortierregeln ließen sich nicht laden. Läuft der Server?</p>';
    return;
  }
  if (!daten.verfuegbar) {
    inhalt.innerHTML = '<p class="regeln-hinweis">Sortierregeln gibt es erst mit der neuen Version des Add-ons (Datenbank Version 6). '
      + 'Starte das Add-on neu, sobald das Update eingespielt ist.</p>';
    return;
  }

  inhalt.innerHTML = '<div class="regeln-leiste"><h2 class="etikett">Regeln</h2>'
    + `<button type="button" class="knopf knopf-haupt regeln-neu" hidden>${SYM.plus}<span>Neue Regel</span></button></div>`
    + '<p class="regeln-hinweis regeln-leer" hidden></p>'
    + '<ol class="regeln-liste"></ol>'
    + '<section class="regeln-probe" aria-label="Probe"><h2 class="etikett">Probe</h2>'
    + '<p class="regeln-hinweis">Wähle eine Karte deiner Sammlung und sieh, wohin sie einsortiert würde und welche Regeln dabei geprüft werden.</p>'
    + '<label class="etikett feld-etikett" for="regeln-suche">Karte der Sammlung</label>'
    + '<input id="regeln-suche" class="feld" type="search" autocomplete="off" autocapitalize="off" spellcheck="false"'
    + ' enterkeyhint="search" placeholder="Name der Karte (ab 2 Zeichen)" aria-controls="regeln-treffer">'
    + '<p class="nur-vorleser" role="status" aria-live="polite" id="regeln-treffer-status"></p>'
    + '<ul class="regeln-treffer" id="regeln-treffer"></ul>'
    + '<div class="regeln-ergebnis" aria-live="polite"></div></section>';

  const z = {
    daten, bereich, meldung: meldungEl, status: bereich.querySelector('.regeln-status'), liste: inhalt.querySelector('.regeln-liste'), leer: inhalt.querySelector('.regeln-leer'),
    neueRegel: inhalt.querySelector('.regeln-neu'), suche: inhalt.querySelector('#regeln-suche'),
    treffer: inhalt.querySelector('#regeln-treffer'), trefferStatus: inhalt.querySelector('#regeln-treffer-status'),
    ergebnis: inhalt.querySelector('.regeln-ergebnis'), suchlauf: 0, trefferListe: [], probeKarte: null, uhr: 0,
    sitzung: () => { if (Z === z) listeZeichnen(); },
  };
  Z = z;
  window.addEventListener('binder:sitzung', z.sitzung);
  listeZeichnen();

  z.neueRegel.addEventListener('click', async () => {
    if (await regelDialog(null)) { await nachladen(); }
  });

  z.liste.addEventListener('click', async (e) => {
    const knopf = e.target.closest('[data-aktion]');
    if (!knopf || knopf.disabled) return;
    const id = Number(knopf.closest('[data-id]').dataset.id);
    const r = Z.daten.regeln.find((x) => x.id === id);
    if (!r) return;
    const aktion = knopf.dataset.aktion;
    if (aktion === 'schalter') await aktivSchalten(r);
    else if (aktion === 'hoch') await verschieben(r, -1);
    else if (aktion === 'runter') await verschieben(r, 1);
    else if (aktion === 'kopieren') {
      ablageSetzen(r.bedingungen, `Regel „${r.name}“`);
      z.status.textContent = `${anzahlText(r.bedingungen.length)} der Regel „${r.name}“ kopiert. `
        + 'Einfügen geht beim Bearbeiten jeder Regel und in den Fächern jeder Kiste.';
    }
    else if (aktion === 'bearbeiten') { if (await regelDialog(r)) await nachladen({ id: r.id, aktion: 'bearbeiten' }); }
    else if (aktion === 'loeschen') await loeschen(r);
  });

  z.suche.addEventListener('input', () => {
    clearTimeout(z.uhr);
    z.uhr = setTimeout(suchen, 250);
  });
  z.suche.closest('section').addEventListener('submit', (e) => e.preventDefault());
  z.suche.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); clearTimeout(z.uhr); suchen(); }
  });
  z.treffer.addEventListener('click', (e) => {
    const knopf = e.target.closest('[data-i]');
    if (!knopf) return;
    z.probeKarte = z.trefferListe[Number(knopf.dataset.i)];
    z.suche.value = z.probeKarte.name;
    z.treffer.replaceChildren();
    z.trefferStatus.textContent = '';
    probeLaden();
  });
}

export function verlassen() {
  if (!Z) return;
  clearTimeout(Z.uhr);
  window.removeEventListener('binder:sitzung', Z.sitzung);
  Z.suchlauf += 1;
  Z = null;
}

// ---------------------------------------------------------------- Fächer einer Kiste

/** Dialog „Fächer“: Reihenfolge = Vorrang, das erste passende Fach gewinnt. Gibt true zurück, wenn gespeichert wurde.
    Wirft einen Fehler mit deutschem Satz, wenn die Fächer nicht zu holen sind (der Aufrufer zeigt ihn). */
export async function faecherBearbeiten(kisteId, kisteName) {
  const [stand, antwort] = await Promise.all([holen('/api/regeln'), holen(`/api/kiste/${kisteId}/faecher`)]);
  if (!stand.verfuegbar) throw new Error('Fächer gibt es erst mit der neuen Version des Add-ons (Datenbank Version 6).');
  const beschreibung = stand.beschreibung;

  const wurzel = el('div', 'regeln-faecher-inhalt');
  wurzel.innerHTML = '<p class="regeln-hinweis">Karten, die in diese Kiste einsortiert werden, kommen in das erste Fach von oben, dessen Bedingungen passen. '
    + 'Ein Fach ohne Bedingung nimmt den Rest auf.</p>'
    + '<p class="regeln-hinweis regeln-faecher-leer" hidden>Noch keine Fächer. Ohne Fächer liegen alle Karten lose in der Kiste.</p>'
    + '<ol class="regeln-faecher"></ol>'
    + `<button type="button" class="knopf regeln-fach-neu">${SYM.plus}<span>Fach hinzufügen</span></button>`
    + '<p class="meldung regeln-dialogmeldung" role="alert" aria-live="assertive"></p>';
  const liste = wurzel.querySelector('.regeln-faecher');
  const leer = wurzel.querySelector('.regeln-faecher-leer');
  const text = wurzel.querySelector('.regeln-dialogmeldung');
  const fachId = ++zaehler;
  const faecher = [];

  const nummerieren = () => {
    faecher.forEach((f, i) => {
      f.nr.textContent = String(i + 1);
      f.name.setAttribute('aria-label', `Name von Fach ${i + 1}`);
      f.hoch.disabled = i === 0;
      f.runter.disabled = i === faecher.length - 1;
      f.hoch.setAttribute('aria-label', `Fach ${i + 1} nach oben`);
      f.runter.setAttribute('aria-label', `Fach ${i + 1} nach unten`);
      f.weg.setAttribute('aria-label', `Fach ${i + 1} entfernen`);
    });
    leer.hidden = faecher.length > 0;
  };

  function fachDazu(start = null) {
    const li = el('li', 'regeln-fach');
    const kopf = el('div', 'regeln-fach-kopf');
    const nr = el('span', 'regeln-nr');
    nr.setAttribute('aria-hidden', 'true');
    const name = el('input', 'feld regeln-fach-name');
    name.type = 'text';
    name.maxLength = 60;
    name.autocomplete = 'off';
    name.spellcheck = false;
    name.placeholder = 'Name des Fachs, z. B. Länder';
    name.value = start?.name ?? '';
    name.name = `fach-${fachId}`;
    const hoch = knopfEl('knopf knopf-sym', SYM.hoch);
    const runter = knopfEl('knopf knopf-sym', SYM.runter);
    const weg = knopfEl('knopf knopf-sym', SYM.muell);
    kopf.append(nr, name, hoch, runter, weg);
    const platz = el('div', 'regeln-fach-bed');
    li.append(kopf);
    if (start && start.gueltig === false) {
      li.append(el('p', 'regeln-warnung', 'Die Bedingungen dieses Fachs ließen sich nicht mehr lesen. Setze sie neu.'));
    }
    li.append(platz);
    const editor = bedingungenEditor(platz, beschreibung, start?.bedingungen ?? [],
      { leerText: 'Keine Bedingung: Das Fach nimmt den Rest auf.',
        quelle: () => `Fach „${name.value.trim() || 'ohne Namen'}“ (Kiste „${kisteName}“)` });
    const f = { li, nr, name, hoch, runter, weg, editor };
    hoch.addEventListener('click', () => bewegen(f, -1, 'hoch'));
    runter.addEventListener('click', () => bewegen(f, 1, 'runter'));
    weg.addEventListener('click', () => {
      faecher.splice(faecher.indexOf(f), 1);
      li.remove();
      nummerieren();
      (faecher[faecher.length - 1]?.name ?? wurzel.querySelector('.regeln-fach-neu')).focus();
    });
    faecher.push(f);
    liste.append(li);
    nummerieren();
    return f;
  }

  function bewegen(f, richtung, welche) {
    const von = faecher.indexOf(f);
    const nach = von + richtung;
    if (nach < 0 || nach >= faecher.length) return;
    faecher.splice(von, 1);
    faecher.splice(nach, 0, f);
    liste.replaceChildren(...faecher.map((x) => x.li));
    nummerieren();
    (f[welche].disabled ? f[welche === 'hoch' ? 'runter' : 'hoch'] : f[welche]).focus();
  }

  for (const f of antwort.faecher) fachDazu(f);
  wurzel.querySelector('.regeln-fach-neu').addEventListener('click', () => fachDazu().name.focus());

  const d = dialog({ titel: `Fächer von „${kisteName}“`, klasse: 'regeln-dialog', inhalt: wurzel, voll: true });
  const abbrechen = knopfEl('knopf', 'Abbrechen');
  abbrechen.addEventListener('click', () => d.schliessen(false));
  const speichern = knopfEl('knopf knopf-haupt', 'Speichern');
  d.fuss.append(abbrechen, speichern);
  speichern.addEventListener('click', async () => {
    speichern.disabled = true;
    text.textContent = '';
    try {
      await senden(`/api/kiste/${kisteId}/faecher`, { methode: 'PUT',
        daten: { faecher: faecher.map((f) => ({ name: f.name.value.trim(), bedingungen: f.editor.werte() })) } });
      d.schliessen(true);
    } catch (f) {
      text.textContent = f.message;
      speichern.disabled = false;
    }
  });
  return (await d.fertig) === true;
}
