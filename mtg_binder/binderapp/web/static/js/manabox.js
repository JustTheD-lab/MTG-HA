// Add-on: ManaBox-Import. Du wählst je ManaBox-Binder, Liste und Deck, was wohin geht; ein Probelauf zeigt das Ergebnis,
// erst danach wird importiert. Die Datei selbst bleibt unverändert und reist bei jedem Schritt mit (web/manabox.py).
//
//   await oeffnen(datei)   Dialog zu einer gewählten CSV-Datei; nach einem Import lädt die App neu

import { senden, esc } from './gemeinsam.js';
import { dialog } from './dialog.js';

const zahl = (n) => Number(n || 0).toLocaleString('de-DE');
const TYPEN = { binder: 'Binder', list: 'Liste', liste: 'Liste', deck: 'Deck' };
const AKTIONEN = [
  ['neu', 'Neue Kiste'], ['kiste', 'Vorhandene Kiste'], ['regeln', 'Nach Sortierregeln'], ['ueberspringen', 'Überspringen'],
];

function knopf(text, klasse = 'knopf') {
  return Object.assign(document.createElement('button'), { type: 'button', className: klasse, textContent: text });
}

function kartenText(e) {
  const set = e.set ? ` (${String(e.set).toUpperCase()}${e.nummer ? ` ${e.nummer}` : ''})` : '';
  return `${e.name}${set}${e.sprache ? `, ${String(e.sprache).toUpperCase()}` : ''}${e.foil ? ', Foil' : ''}`;
}

function liste(titel, eintraege, gesamt, zeile) {
  if (!gesamt) return `<p class="bericht-zeile">${esc(titel)}: <strong>0</strong></p>`;
  return `<details class="bericht-liste"><summary>${esc(titel)}: <strong>${zahl(gesamt)}</strong></summary><ul>`
    + eintraege.map((e) => `<li>${esc(zeile(e))}</li>`).join('')
    + (gesamt > eintraege.length ? `<li class="bericht-mehr">… und ${zahl(gesamt - eintraege.length)} weitere</li>` : '')
    + '</ul></details>';
}

function zeileHtml(e, i, u) {
  const optionen = AKTIONEN.filter(([wert]) => (wert !== 'regeln' || u.regeln) && (wert !== 'kiste' || u.kisten.length))
    .map(([wert, text]) => `<option value="${wert}"${wert === e.aktion ? ' selected' : ''}>${esc(text)}</option>`).join('');
  const kisten = u.kisten.map((k) => `<option value="${k.id}"${k.id === e.kiste_id ? ' selected' : ''}>${esc(k.name)}</option>`).join('');
  const angaben = [TYPEN[e.typ] || e.typ, `${zahl(e.karten)} Karten`];
  if (e.bekannt) angaben.push(`${zahl(e.bekannt)} Posten schon importiert`);
  return `<li class="manabox-zeile" data-i="${i}"><div class="manabox-name"><strong>${esc(e.name.trim())}</strong>`
    + `<span>${esc(angaben.join(' · '))}</span></div><div class="manabox-wahl">`
    + `<select class="feld manabox-aktion" aria-label="Was mit ${esc(e.name.trim())} geschieht">${optionen}</select>`
    + `<input class="feld manabox-kistenname" type="text" maxlength="80" value="${esc(e.kiste_name)}" aria-label="Name der neuen Kiste">`
    + `<select class="feld manabox-kiste" aria-label="Ziel-Kiste">${kisten}</select></div></li>`;
}

function felderZeigen(zeile) {
  const aktion = zeile.querySelector('.manabox-aktion').value;
  zeile.querySelector('.manabox-kistenname').hidden = aktion !== 'neu';
  zeile.querySelector('.manabox-kiste').hidden = aktion !== 'kiste';
  zeile.classList.toggle('manabox-aus', aktion === 'ueberspringen');
}

function planLesen(wurzel, u) {
  return [...wurzel.querySelectorAll('.manabox-zeile')].map((zeile) => {
    const e = u.eintraege[Number(zeile.dataset.i)];
    return {
      name: e.name, typ: e.typ, aktion: zeile.querySelector('.manabox-aktion').value,
      kiste_name: zeile.querySelector('.manabox-kistenname').value,
      kiste_id: Number(zeile.querySelector('.manabox-kiste').value) || null,
    };
  });
}

function berichtHtml(b, fertig) {
  const werte = [
    ['Karten', b.karten], ['Posten', b.posten], ['Neu', b.neu], ['Geändert', b.geaendert], ['Unverändert', b.unveraendert],
    ['Mit Kaufpreis', b.kaufpreise], ['Von dir gelöscht (bleibt weg)', b.nutzer_geloescht], ['Von dir geändert (bleibt)', b.geschuetzt],
  ];
  const r = b.regeln;
  return `<p class="dialog-text bericht-kopf">${fertig
    ? `<strong>Import abgeschlossen.</strong> Vorher wurde gesichert${b.sicherung ? ` (${esc(b.sicherung)})` : ''}.`
    : '<strong>Probelauf:</strong> Es wurde noch nichts geändert.'}</p>`
    + `<dl class="bericht-werte">${werte.map(([t, n]) => `<div><dt>${esc(t)}</dt><dd>${zahl(n)}</dd></div>`).join('')}</dl>`
    + `<p class="bericht-zeile">Kartendaten: <strong>${zahl(b.mit_kartendaten)}</strong> von ${zahl(b.posten)} Posten</p>`
    + liste('Ziele', b.eintraege, b.eintraege.length,
      (e) => `${e.name.trim()} → ${e.ziel}${e.kiste_neu ? ' (neu)' : ''}: ${zahl(e.karten)} Karten, ${zahl(e.neu)} neu`)
    + (r ? liste('Nach Sortierregeln', r.kisten, r.kisten.length, (k) => `${k.name}: ${zahl(k.karten)} Karten`)
      + `<p class="bericht-zeile">Keine Regel passt, bleibt im Scan-Eingang: <strong>${zahl(r.keine)}</strong> Karten · `
      + `wartet auf den Preis: <strong>${zahl(r.wartet)}</strong> Karten</p>` : '')
    + liste('Zeilen ohne Scryfall-ID', b.ohne_scryfall, b.ohne_scryfall_anzahl, (e) => `Zeile ${e.zeile}: ${e.name}`)
    + liste('Nicht mehr im Export (bleiben, wo sie sind)', b.fehlend, b.fehlend_anzahl, kartenText)
    + liste('Mögliche Doppel mit gescannten Karten (nichts zusammengezählt)', b.doppel, b.doppel_anzahl,
      (e) => `${kartenText(e)}: ${e.kiste} und Binder ${e.binder}, Seite ${e.seite}`);
}

export async function oeffnen(datei) {
  const inhalt = document.createElement('div');
  inhalt.className = 'kiste-bericht';
  inhalt.innerHTML = '<p class="dialog-text">Die Datei wird gelesen …</p>';
  const d = dialog({ titel: 'ManaBox-Import', klasse: 'kiste-dialog', voll: true, inhalt });
  const zu = knopf('Abbrechen');
  const zurueck = Object.assign(knopf('Zurück'), { hidden: true });
  const los = Object.assign(knopf('Probelauf', 'knopf knopf-haupt'), { disabled: true });
  const rueckgaengig = Object.assign(knopf('Rückgängig', 'knopf knopf-gefahr'), { hidden: true });
  d.fuss.append(zu, zurueck, rueckgaengig, los);
  let importiert = false;
  zu.addEventListener('click', () => d.schliessen());

  const fehler = (text) => {
    inhalt.querySelector('.meldung')?.remove();
    const p = Object.assign(document.createElement('p'), { className: 'meldung', textContent: text });
    p.setAttribute('role', 'alert');
    inhalt.prepend(p);
    inhalt.scrollTop = 0;
  };

  let csv;
  let u;
  try {
    csv = await datei.text();
    u = await senden('/api/manabox/uebersicht', { daten: { name: datei.name, csv } });
  } catch (f) {
    inhalt.innerHTML = `<p class="meldung" role="alert">${esc(f.message)}</p>`;
    zu.textContent = 'Schließen';
    await d.fertig;
    return;
  }

  const wahl = document.createElement('div');
  wahl.innerHTML = `<p class="dialog-text">${esc(u.datei)}: ${zahl(u.karten)} Karten in ${zahl(u.eintraege.length)} Einträgen. `
    + 'Listen und Decks sind vorgewählt zum Überspringen, weil sie die Karten der Binder doppelt enthalten.</p>'
    + `<ul class="manabox-liste">${u.eintraege.map((e, i) => zeileHtml(e, i, u)).join('')}</ul>`;
  wahl.querySelectorAll('.manabox-zeile').forEach(felderZeigen);
  wahl.addEventListener('change', (e) => {
    if (e.target.matches('.manabox-aktion')) felderZeigen(e.target.closest('.manabox-zeile'));
  });
  const wahlZeigen = () => {
    inhalt.replaceChildren(wahl);
    los.textContent = 'Probelauf';
    los.disabled = false;
    zurueck.hidden = true;
  };
  wahlZeigen();

  let plan = null;
  let bericht = null;
  zurueck.addEventListener('click', () => { bericht = null; wahlZeigen(); });
  los.addEventListener('click', async () => {
    const probe = !bericht;
    if (probe) plan = planLesen(wahl, u);
    los.disabled = true;
    los.textContent = probe ? 'Rechnet …' : 'Importiere …';
    zurueck.disabled = true;
    try {
      const r = await senden(`/api/manabox/${probe ? 'pruefen' : 'importieren'}`, { daten: { name: u.datei, csv, plan } });
      inhalt.innerHTML = berichtHtml(r, !probe);
      inhalt.scrollTop = 0;
      if (probe) {
        bericht = r;
        los.textContent = 'Importieren';
        los.disabled = !(r.neu || r.geaendert);
        zurueck.hidden = false;
      } else {
        importiert = true;
        los.hidden = true;
        zurueck.hidden = true;
        zu.textContent = 'Schließen';
        zu.classList.add('knopf-haupt');
        rueckgaengig.hidden = !r.schritt;
        rueckgaengig.dataset.schritt = r.schritt || '';
      }
    } catch (f) {
      fehler(f.message);
      los.textContent = probe ? 'Probelauf' : 'Importieren';
      los.disabled = false;
    }
    zurueck.disabled = false;
  });
  rueckgaengig.addEventListener('click', async () => {
    rueckgaengig.disabled = true;
    try {
      await senden(`/api/schritt/${rueckgaengig.dataset.schritt}/rueckgaengig`);
      inhalt.innerHTML = '<p class="dialog-text"><strong>Der Import ist zurückgenommen.</strong></p>';
      rueckgaengig.hidden = true;
    } catch (f) {
      fehler(f.message);
      rueckgaengig.disabled = false;
    }
  });

  await d.fertig;
  if (importiert) {
    location.hash = '#/';
    location.reload();
  }
}
