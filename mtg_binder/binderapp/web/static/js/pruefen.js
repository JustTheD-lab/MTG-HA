// Prüfansicht: unsichere Erkennungen nacheinander ansehen, bestätigen oder korrigieren.
//
//   #/pruefen             Prüfmodus: ein Fall nach dem anderen, „7 von 14"
//   #/pruefen/karte/12    dazu Karte 12 groß (karte.js)
//
// Exporte für den Wegweiser (app.js): zeigen(wurzel, parameter, vorher), aktualisieren(parameter), verlassen().
// Dazu bearbeiten(kartenId, { titel }): derselbe Satz Aktionen für genau eine Karte als Dialog (aus der
// Kartenansicht, karte.js); gibt zurück, ob etwas geändert wurde.
//
// Schnittstelle (pruefen.py): Warteschlange und Fälle lesen, Aktionen schreiben (nur angemeldet), jede
// Aktion hat eine Kennung `schritt` und lässt sich mit der passenden …/rueckgaengig-Route zurücknehmen.
// Ohne Katalog (`katalog: false`) sind „Druck ändern" und die Namenssuche abgeschaltet. Animiert wird
// nur über CSS (transform, opacity); bei „Bewegung reduzieren" sind die Dauern null.

import { senden, bildUrl, esc, gehe, zurueck, BREITEN, vorliebe, geaendert as binderGeaendert,
  adresse as serverAdresse, scryfallBild } from './gemeinsam.js';
import { dialog, bestaetigen, dialogOffen } from './dialog.js';
import { angemeldet, anmelden, knopf as anmeldeKnopf } from './anmeldung.js';
import { grundText, gruendeListe, SPRACHEN, SELTENHEIT, sprachName } from './karte.js';

const ZEILE = (pfad) => `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5"`
  + ` stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${pfad}</svg>`;
const IKON = {
  zurueck: ZEILE('<path d="M15 5l-7 7 7 7"/>'),
  hoch: ZEILE('<path d="M5 15l7-7 7 7"/>'),
  haken: ZEILE('<path d="M5 12.5l4.5 4.5L19 7.5"/>'),
};

const NEBEN_ENTPRELLEN = 700;      // ms, bis eine geänderte Anzahl gespeichert wird
const SUCH_VERZOEGERUNG = 250;     // ms, Namenssuche
const DRUCKE_ERSTE = 48;           // so viele Drucke zeigt der Dialog zuerst

const STATUS_TEXT = { unsicher: 'Erkennung unsicher', name_sicher: 'Name sicher, Druck offen' };

const el = (tag, klasse = '', html = '') => {
  const e = document.createElement(tag);
  if (klasse) e.className = klasse;
  if (html) e.innerHTML = html;
  return e;
};

/** Anfrage lesend, aber mit dem deutschen Satz des Servers im Fehler (holen() kennt ihn nicht). */
const lesen = (pfad) => senden(pfad, { methode: 'GET' });

// ---------- Texte ----------

function ortText(ort) {
  if (!ort) return '';
  // Geschützte Leerzeichen: „Spalte 2" bricht nicht mitten im Ausdruck um.
  if (ort.art === 'binder') return `${ort.binder_name} · Seite ${ort.seite} · Zeile ${ort.zeile}, Spalte ${ort.spalte}`;
  if (ort.art === 'kiste') return `Kiste ${ort.kiste_name}`;
  return 'Weder in einem Binder noch in einer Kiste';
}

function ortAdresse(fall) {
  const o = fall.ort;
  if (o?.art === 'binder') return `#/binder/${o.binder_id}/${o.seite}` + (fall.karte_id ? `/karte/${fall.karte_id}` : '');
  if (o?.art === 'kiste') return `#/kiste/${o.kiste_id}` + (fall.karte_id ? `/karte/${fall.karte_id}` : '');
  return null;
}

function druckText(fall) {
  const k = fall.karte;
  const d = fall.kartendaten;
  const set = k.set ? String(k.set).toUpperCase() : null;
  const satz = d?.set_name && set ? `${d.set_name} (${set})` : set;
  return [satz, k.nummer && `Nr. ${k.nummer}`, k.sprache && sprachName(k.sprache), k.foil && 'Foil',
    k.anzahl > 1 && `${k.anzahl}×`].filter(Boolean).join(' · ');
}

function statusText(fall) {
  const k = fall.karte;
  if (!k) return fall.tasche?.inhalt === 'unsicher' ? 'Unklar, ob in dieser Tasche eine Karte liegt' : null;
  if (k.geprueft) return k.status === 'sicher' ? 'Von dir geprüft' : `${STATUS_TEXT[k.status] ?? ''} – von dir geprüft`;
  return STATUS_TEXT[k.status] ?? null;
}

const fallPfad = (f) => (f.art === 'karte' ? `/api/pruefen/karte/${f.karte_id}` : `/api/pruefen/tasche/${f.tasche_id}`);
const fallSchluessel = (f) => (f.art === 'karte' ? `k${f.karte_id}` : `t${f.tasche_id}`);

/** Sagt den anderen Ansichten (Lesetisch, Kiste), dass sich an einer Karte etwas geändert hat. */
function meldeAenderung(fall) {
  const o = fall?.ort;
  if (o?.art === 'binder') binderGeaendert(o.binder_id);
  else if (o?.art === 'kiste') window.dispatchEvent(new CustomEvent('kiste:geaendert', { detail: { kisteId: o.kiste_id } }));
}

// ---------- Bilder ----------

function flaeche(titel, nebenText = '') {
  return el('span', 'pruef-flaeche', `<strong>${esc(titel)}</strong>${nebenText ? `<small>${esc(nebenText)}</small>` : ''}`);
}

/** Bild in den Rahmen setzen; fehlt es oder lädt es nicht, bleibt eine schlichte Fläche mit Namen. */
function bildSetzen(rahmen, art, schluessel, name, kiste) {
  const ersatz = () => flaeche(art === 'foto' ? 'Kein Foto' : name, art === 'foto' ? '' : 'Kein Bild vorhanden');
  if (!schluessel) { rahmen.replaceChildren(ersatz()); return; }
  const breiten = kiste && art === 'scryfall' ? BREITEN.filter((b) => b <= 400) : BREITEN;  // Kistenkarten: nur 200 und 400
  const img = new Image();
  img.className = 'pruef-bild';
  img.decoding = 'async';
  img.alt = art === 'foto' ? `Eigenes Foto von ${name}` : `Scryfall-Bild von ${name}`;
  img.sizes = '(min-width: 900px) 330px, 45vw';
  img.srcset = breiten.map((b) => `${bildUrl(art, schluessel, b)} ${b}w`).join(', ');
  img.src = bildUrl(art, schluessel, 400);
  img.addEventListener('error', () => {
    const direkt = art === 'scryfall' && !img.dataset.direkt ? scryfallBild(schluessel) : null;
    if (!direkt) { rahmen.replaceChildren(ersatz()); return; }
    img.dataset.direkt = '1';   // noch kein Vorschaubild: einmal direkt von Scryfall
    img.removeAttribute('srcset');
    img.src = direkt;
  });
  rahmen.replaceChildren(img);
}

function figur(art, fall, opt) {
  const k = fall.karte;
  const name = k?.name ?? 'Tasche';
  const f = el('figure', 'pruef-spalte');
  f.dataset.bild = art;
  const gross = opt.gross && k;
  const rahmen = el('span', 'pruef-rahmen');
  let karte;
  if (gross) {
    karte = el('button', 'pruef-karte');
    karte.type = 'button';
    karte.setAttribute('aria-label', `${name} groß ansehen`);
    if (art === (fall.ausschnitt ? 'foto' : 'scryfall')) karte.dataset.karte = k.id;  // Ausgangspunkt für das Herausheben
    karte.addEventListener('click', () => gehe(`#/pruefen/karte/${k.id}`));
  } else {
    karte = el('div', 'pruef-karte');
  }
  karte.append(rahmen);
  bildSetzen(rahmen, art, art === 'foto' ? fall.ausschnitt : k?.bild, name, fall.ort?.art === 'kiste');
  f.append(karte, el('figcaption', 'etikett pruef-unterschrift', art === 'foto' ? 'Mein Foto' : 'Scryfall-Bild'));
  return f;
}

// ---------- Ein Fall ----------

/** Der ganze Fall als Element. opt: { angemeldet, gross, kompakt, mehr, befehl(name, wert), puffer }. */
function fallDom(fall, opt) {
  const k = fall.karte;
  const a = el('article', `pruef-fall${opt.kompakt ? ' pruef-fall-kompakt' : ''}`);
  a.dataset.art = fall.art;

  const bilder = el('div', 'pruef-bilder');
  const paar = el('div', 'pruef-paar');
  const mitFoto = Boolean(fall.ausschnitt);
  if (mitFoto) paar.append(figur('foto', fall, opt));
  if (k) paar.append(figur('scryfall', fall, opt));
  if (!mitFoto && !k) {
    const leer = el('div', 'pruef-karte');
    const rahmen = el('span', 'pruef-rahmen');
    rahmen.append(flaeche('Kein Foto'));
    leer.append(rahmen);
    paar.append(leer);
  }
  bilder.dataset.ansicht = mitFoto && k ? (opt.kompakt ? 'beide' : (vorliebe('pruefen.bild') || 'beide')) : 'einzeln';
  if (mitFoto && k && !opt.kompakt) {
    const schalter = el('div', 'pruef-schalter', [['beide', 'Nebeneinander'], ['foto', 'Mein Foto'], ['scryfall', 'Scryfall']]
      .map(([wert, text]) => `<button type="button" class="knopf pruef-schalter-teil" data-wert="${wert}" aria-pressed="${bilder.dataset.ansicht === wert}">${text}</button>`)
      .join(''));
    schalter.setAttribute('role', 'group');
    schalter.setAttribute('aria-label', 'Bilder anzeigen');
    schalter.addEventListener('click', (e) => {
      const b = e.target.closest('[data-wert]');
      if (!b) return;
      bilder.dataset.ansicht = b.dataset.wert;
      vorliebe('pruefen.bild', b.dataset.wert);
      schalter.querySelectorAll('[data-wert]').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    });
    bilder.append(schalter);
  }
  bilder.append(paar);

  const tafel = el('section', 'pruef-tafel');
  tafel.append(el('h2', 'pruef-name', esc(k ? k.name : 'Tasche ohne erkannte Karte')));
  if (k) tafel.append(el('p', 'pruef-druck', esc(druckText(fall))));
  const status = statusText(fall);
  if (status) {
    const art = k ? (k.geprueft ? 'geprueft' : k.status) : 'unsicher';
    tafel.append(el('p', `pruef-status pruef-status-${art}`, `<span class="pruef-punkt" aria-hidden="true"></span>${esc(status)}`));
  }
  const adresse = ortAdresse(fall);
  tafel.append(el('p', 'pruef-ort',
    `<span class="etikett">Ort</span><span>${esc(ortText(fall.ort))}</span>`
    + (adresse && opt.gross ? `<a class="pruef-link" href="${esc(adresse)}">${fall.ort.art === 'kiste' ? 'In der Kiste zeigen' : 'Im Binder zeigen'}</a>` : '')));
  if (!k) {
    tafel.append(el('p', 'pruef-frage', 'Ist die Tasche leer, oder liegt eine Karte mit der Rückseite nach oben darin?'));
  }
  const gruende = k ? gruendeListe(fall.gruende) : [];
  if (gruende.length && !k.geprueft) {
    tafel.append(el('div', 'pruef-gruende',
      `<span class="etikett">Warum unsicher?</span><ul>${gruende.map((g) => `<li title="${esc(g)}">${esc(grundText(g))}</li>`).join('')}</ul>`));
  }
  if (k && opt.angemeldet) tafel.append(mehrDom(fall, opt));

  if (opt.gross) {
    bilder.addEventListener('click', (e) => { if (e.target.closest('a')) e.preventDefault(); });
    tafel.querySelectorAll('a.pruef-link').forEach((link) => link.addEventListener('click', (e) => {
      e.preventDefault();
      gehe(link.getAttribute('href'));
    }));
  }
  a.append(bilder, tafel);
  return a;
}

/** „Mehr": Anzahl, Foil, Sprache, Rückgängig und Entfernen (nur angemeldet, nur mit Karte). */
function mehrDom(fall, opt) {
  const k = fall.karte;
  const box = el('div', 'pruef-mehr');
  const zu = el('button', 'knopf pruef-mehr-knopf', 'Mehr');
  zu.type = 'button';
  zu.setAttribute('aria-expanded', String(Boolean(opt.mehr?.())));
  const inhalt = el('div', 'pruef-mehr-inhalt');
  inhalt.id = `pruef-mehr-${k.id}`;
  inhalt.hidden = !opt.mehr?.();
  zu.setAttribute('aria-controls', inhalt.id);
  zu.addEventListener('click', () => {
    inhalt.hidden = !inhalt.hidden;
    zu.setAttribute('aria-expanded', String(!inhalt.hidden));
    opt.mehrMerken?.(!inhalt.hidden);
  });
  const sprachen = Object.entries(SPRACHEN).map(([code, name]) =>
    `<option value="${code}"${code === k.sprache ? ' selected' : ''}>${esc(name)}</option>`).join('');
  inhalt.innerHTML = `
    <div class="pruef-zeile"><span class="etikett" id="pruef-anzahl-etikett">Anzahl</span>
      <div class="pruef-zaehler" role="group" aria-labelledby="pruef-anzahl-etikett">
        <button type="button" class="knopf knopf-sym" data-schritt="-1" aria-label="Eine weniger">−</button>
        <input class="feld pruef-anzahl" type="number" inputmode="numeric" min="1" max="999" step="1" value="${k.anzahl}" aria-label="Anzahl">
        <button type="button" class="knopf knopf-sym" data-schritt="1" aria-label="Eine mehr">+</button>
      </div></div>
    <div class="pruef-zeile"><span class="etikett">Foil</span>
      <button type="button" class="knopf pruef-foil" aria-pressed="${k.foil}">${k.foil ? 'Foil: ja' : 'Foil: nein'}</button></div>
    <div class="pruef-zeile"><label class="etikett" for="pruef-sprache-${k.id}">Sprache</label>
      <select class="feld pruef-sprache" id="pruef-sprache-${k.id}">${sprachen}</select></div>
    <div class="pruef-zeile pruef-zeile-unten">
      ${fall.rueckgaengig ? `<button type="button" class="knopf pruef-zuruecknehmen" title="Letzte Änderung zurücknehmen">Rückgängig: ${esc(fall.rueckgaengig)}</button>` : ''}
      <button type="button" class="knopf knopf-gefahr pruef-entfernen">Karte entfernen</button></div>`;
  const feld = inhalt.querySelector('.pruef-anzahl');
  const anzahlWert = () => Math.min(999, Math.max(1, Math.round(Number(feld.value) || k.anzahl)));
  inhalt.querySelectorAll('[data-schritt]').forEach((b) => b.addEventListener('click', () => {
    feld.value = Math.min(999, Math.max(1, anzahlWert() + Number(b.dataset.schritt)));
    opt.puffer?.plane(Number(feld.value));
  }));
  feld.addEventListener('input', () => { if (feld.value !== '') opt.puffer?.plane(anzahlWert()); });
  feld.addEventListener('change', () => { feld.value = anzahlWert(); opt.puffer?.plane(Number(feld.value)); });
  inhalt.querySelector('.pruef-foil').addEventListener('click', () => opt.befehl('foil', !k.foil));
  inhalt.querySelector('.pruef-sprache').addEventListener('change', (e) => opt.befehl('sprache', e.target.value));
  inhalt.querySelector('.pruef-zuruecknehmen')?.addEventListener('click', () => opt.befehl('zuruecknehmen'));
  inhalt.querySelector('.pruef-entfernen').addEventListener('click', () => opt.befehl('entfernen'));
  box.append(zu, inhalt);
  return box;
}

// ---------- Aktionen (für Prüfmodus und Dialog gleich) ----------

/** Sammelt schnelle Änderungen der Anzahl und speichert den letzten Wert erst nach einer kurzen Pause. */
class Puffer {
  constructor(speichern) {
    this.speichern = speichern;
    this.wartend = null;
  }

  plane(wert) {
    clearTimeout(this.wartend?.uhr);
    this.wartend = { wert, uhr: setTimeout(() => this.leeren(), NEBEN_ENTPRELLEN) };
  }

  async leeren() {
    const w = this.wartend;
    if (!w) return;
    clearTimeout(w.uhr);
    this.wartend = null;
    await this.speichern(w.wert);
  }

  verwerfen() {
    clearTimeout(this.wartend?.uhr);
    this.wartend = null;
  }
}

/** Ergebnis einer Aktion für die Leiste. `schritt` fehlt, wenn nichts zu ändern war. */
function ergebnis(antwort, text, fall, weiter) {
  if (!antwort?.schritt) return { text: 'Keine Änderung nötig', weiter, rueckgaengig: null, gespeichert: false };
  const pfad = fall.art === 'karte' ? `/api/karte/${fall.karte_id}/rueckgaengig` : `/api/tasche/${fall.tasche_id}/rueckgaengig`;
  return { text, weiter, rueckgaengig: pfad, gespeichert: true };
}

const AKTIONEN = {
  async stimmt(f) {
    return ergebnis(await senden(`/api/karte/${f.karte_id}/bestaetigen`), 'Als richtig bestätigt', f, true);
  },
  async druck(f) {
    const antwort = await druckDialog(f);
    return antwort ? ergebnis(antwort, 'Druck geändert', f, true) : null;
  },
  async keine(f) {
    const inhalt = await keineKarteDialog(f);
    if (!inhalt) return null;
    const antwort = await senden(`/api/tasche/${f.tasche_id}/inhalt`, { daten: { inhalt } });
    return ergebnis(antwort, inhalt === 'leer' ? 'Als leere Tasche markiert' : 'Als Rückseite markiert', f, true);
  },
  async anzahl(f, n) {
    return ergebnis(await senden(`/api/karte/${f.karte_id}`, { methode: 'PATCH', daten: { anzahl: n } }), `Anzahl auf ${n} gesetzt`, f, false);
  },
  async foil(f, an) {
    return ergebnis(await senden(`/api/karte/${f.karte_id}`, { methode: 'PATCH', daten: { foil: an } }), an ? 'Foil: ja' : 'Foil: nein', f, false);
  },
  async sprache(f, code) {
    return ergebnis(await senden(`/api/karte/${f.karte_id}`, { methode: 'PATCH', daten: { sprache: code } }),
      `Sprache: ${sprachName(code)}`, f, false);
  },
  async entfernen(f) {
    const ja = await bestaetigen({
      titel: 'Karte entfernen?',
      text: `„${f.karte.name}“ wird aus der Sammlung entfernt. Direkt danach kannst du das noch rückgängig machen.`,
      ja: 'Entfernen', gefahr: true,
    });
    if (!ja) return null;
    return ergebnis(await senden(`/api/karte/${f.karte_id}`, { methode: 'DELETE' }), 'Karte entfernt', f, true);
  },
};

// ---------- Dialoge ----------

/** „Keine Karte": leer oder Rückseite. Gibt 'leer', 'rueckseite' oder undefined zurück. */
function keineKarteDialog(fall) {
  const k = fall.karte;
  const inhalt = el('div', 'pruef-keine', `
    <p class="dialog-text">${k ? `Die erkannte Karte „${esc(k.name)}“ wird dabei entfernt. Du kannst das gleich danach rückgängig machen.`
    : 'Was ist in dieser Tasche?'}</p>
    <div class="pruef-keine-tasten">
      <button type="button" class="knopf pruef-taste" data-inhalt="leer">Leer</button>
      <button type="button" class="knopf pruef-taste" data-inhalt="rueckseite">Rückseite</button>
    </div>
    <p class="pruef-keine-hilfe"><strong>Leer:</strong> keine Karte in der Tasche. <strong>Rückseite:</strong> eine Karte liegt mit der Rückseite nach oben.</p>`);
  const d = dialog({ titel: 'Keine Karte', klasse: 'dialog-klein pruef-keine-dialog', inhalt });
  inhalt.addEventListener('click', (e) => {
    const b = e.target.closest('[data-inhalt]');
    if (b) d.schliessen(b.dataset.inhalt);
  });
  const abbrechen = el('button', 'knopf', 'Abbrechen');
  abbrechen.type = 'button';
  abbrechen.addEventListener('click', () => d.schliessen(undefined));
  d.fuss.append(abbrechen);
  inhalt.querySelector('[data-inhalt]').focus();
  return d.fertig;
}

/** „Druck ändern" in der Prüfansicht: speichert selbst. Gibt die Antwort des Servers zurück oder null bei Abbruch. */
function druckDialog(fall) {
  const k = fall.karte;
  return druckWaehlen({
    name: k.name, oracleId: k.oracle_id, scryfallId: k.scryfall_id, sprache: k.sprache, foil: k.foil,
    speichern: (wahl) => senden(`/api/karte/${k.id}/druck`, { daten: wahl }),
  });
}

/** Namenssuche und Druckwahl als Dialog (Prüfansicht „Druck ändern", Kistenansicht „Karte hinzufügen").
    Optionen: `titel`, `name` (Vorbelegung der Suche), `oracleId` (zeigt gleich dessen Drucke), `scryfallId`
    (aktueller Druck), `sprache`, `foil`, `uebernehmen` (Beschriftung), `zusatz` (weitere Felder, siehe unten) und
    `speichern(wahl)`: Wird es angegeben, ruft der Dialog es beim Übernehmen auf (Fehler erscheinen im Dialog) und
    gibt dessen Ergebnis zurück. Ohne `speichern` kommt die Wahl `{ scryfall_id, sprache, foil, …zusatz }` zurück.
    `zusatz`: Liste von { name, etikett, art: 'zahl'|'auswahl', wert, min, max, optionen: [{ wert, text }] }.
    Bei Abbruch (Esc, Abbrechen) wird `null` zurückgegeben. */
export async function druckWaehlen({
  titel: dialogTitel = 'Druck ändern', name = '', oracleId = null, scryfallId = null, sprache = null, foil: foilStart = false,
  uebernehmen: uebernehmenText = 'Übernehmen', zusatz = [], speichern = null,
} = {}) {
  const k = { name, oracle_id: oracleId, scryfall_id: scryfallId, sprache: sprache ?? 'de', foil: foilStart };
  const inhalt = el('div', 'pruef-druckwahl', `
    <label class="etikett feld-etikett" for="pruef-suche">Kartenname</label>
    <input id="pruef-suche" class="feld" type="search" autocomplete="off" autocapitalize="off" autocorrect="off"
      spellcheck="false" enterkeyhint="search" placeholder="Name der Karte, auch deutsch" value="${esc(k.name)}">
    <ul class="pruef-treffer" role="listbox" aria-label="Gefundene Karten" hidden></ul>
    <p class="meldung" role="alert"></p>
    <h3 class="pruef-drucke-titel"></h3>
    <div class="pruef-drucke" role="listbox" aria-label="Drucke"></div>
    <button type="button" class="knopf pruef-mehr-drucke" hidden></button>`);
  const d = dialog({ titel: dialogTitel, klasse: 'pruef-dialog', voll: true, inhalt });
  const feld = inhalt.querySelector('#pruef-suche');
  const treffer = inhalt.querySelector('.pruef-treffer');
  const meldung = inhalt.querySelector('.meldung');
  const titel = inhalt.querySelector('.pruef-drucke-titel');
  const raster = inhalt.querySelector('.pruef-drucke');
  const mehrKnopf = inhalt.querySelector('.pruef-mehr-drucke');

  const sprachen = Object.entries(SPRACHEN).map(([code, name]) =>
    `<option value="${code}"${code === k.sprache ? ' selected' : ''}>${esc(name)}</option>`).join('');
  const wahl = el('div', 'pruef-wahl', `
    <p class="pruef-wahl-text" aria-live="polite">Wähle einen Druck.</p>
    <label class="pruef-wahl-feld"><span class="etikett">Sprache</span><select class="feld pruef-sprache">${sprachen}</select></label>
    <button type="button" class="knopf pruef-foil" aria-pressed="${k.foil}">${k.foil ? 'Foil: ja' : 'Foil: nein'}</button>`);
  const abbrechen = el('button', 'knopf', 'Abbrechen');
  abbrechen.type = 'button';
  const uebernehmen = el('button', 'knopf knopf-haupt', esc(uebernehmenText));
  uebernehmen.type = 'button';
  uebernehmen.disabled = true;
  // weitere Felder (zum Beispiel Anzahl und Zustand beim Hinzufügen)
  const zusatzFelder = zusatz.map((z) => {
    const feldEl = z.art === 'auswahl'
      ? el('select', 'feld', z.optionen.map((o) => `<option value="${esc(o.wert)}"${o.wert === z.wert ? ' selected' : ''}>${esc(o.text)}</option>`).join(''))
      : el('input', 'feld');
    if (z.art !== 'auswahl') {
      feldEl.type = 'number';
      feldEl.inputMode = 'numeric';
      feldEl.min = String(z.min ?? 1);
      feldEl.max = String(z.max ?? 999);
      feldEl.value = String(z.wert ?? 1);
    }
    const label = el('label', 'pruef-wahl-feld', `<span class="etikett">${esc(z.etikett)}</span>`);
    label.append(feldEl);
    wahl.querySelector('.pruef-foil').before(label);
    return { z, feldEl };
  });
  d.fuss.append(wahl, abbrechen, uebernehmen);
  const foilKnopf = wahl.querySelector('.pruef-foil');
  const sprachFeld = wahl.querySelector('.pruef-sprache');
  const wahlText = wahl.querySelector('.pruef-wahl-text');

  let drucke = [];
  let ausgewaehlt = null;
  let sichtbar = DRUCKE_ERSTE;
  let foil = Boolean(k.foil);
  let zaehler = 0;
  let uhr = null;

  const jahr = (p) => String(p.erschienen || p.set_erschienen || '').slice(0, 4);
  const kachelBild = (p) => (p.vorschau ? bildUrl('scryfall', p.scryfall_id, 200)
    : p.bild_vorhanden ? serverAdresse(`/api/pruefen/bild/${encodeURIComponent(p.scryfall_id)}`) : null);

  function zeichneDrucke() {
    raster.replaceChildren(...drucke.slice(0, sichtbar).map((p) => {
      const b = el('button', 'pruef-kachel');
      b.type = 'button';
      b.setAttribute('role', 'option');
      b.setAttribute('aria-selected', String(ausgewaehlt?.scryfall_id === p.scryfall_id));
      b.dataset.id = p.scryfall_id;
      const url = kachelBild(p);
      const aktuell = p.scryfall_id === k.scryfall_id;
      b.innerHTML = `<span class="pruef-kachel-bild">${url ? '' : flaeche(String(p.set_code).toUpperCase(), 'Kein Bild').outerHTML}</span>
        <span class="pruef-kachel-text"><strong>${esc(p.set_name ?? String(p.set_code).toUpperCase())}</strong>
        <span>${esc(String(p.set_code).toUpperCase())} · Nr. ${esc(p.nummer)}${jahr(p) ? ` · ${esc(jahr(p))}` : ''}</span>
        <span>${esc(SELTENHEIT[p.seltenheit] ?? p.seltenheit ?? '')} · ${esc(sprachName(p.sprache))}</span>
        ${aktuell ? '<span class="pruef-kachel-aktuell">aktuell</span>' : ''}</span>`;
      if (url) {
        const img = new Image();
        img.className = 'pruef-bild';
        img.loading = 'lazy';
        img.decoding = 'async';
        img.alt = '';
        img.src = url;
        img.addEventListener('error', () => b.querySelector('.pruef-kachel-bild').replaceChildren(
          flaeche(String(p.set_code).toUpperCase(), 'Kein Bild')), { once: true });
        b.querySelector('.pruef-kachel-bild').append(img);
      }
      return b;
    }));
    const rest = drucke.length - sichtbar;
    mehrKnopf.hidden = rest <= 0;
    mehrKnopf.textContent = `Weitere Drucke anzeigen (${rest})`;
  }

  function druckeSetzen(name, liste) {
    drucke = liste;
    ausgewaehlt = null;
    uebernehmen.disabled = true;
    wahlText.textContent = 'Wähle einen Druck.';
    titel.textContent = liste.length ? `Drucke von „${name}" (${liste.length})` : `Keine Drucke für „${name}" gefunden`;
    const aktuell = liste.findIndex((p) => p.scryfall_id === k.scryfall_id);
    sichtbar = Math.max(DRUCKE_ERSTE, aktuell + 1);
    zeichneDrucke();
    if (aktuell >= 0) raster.children[aktuell]?.scrollIntoView({ block: 'nearest' });
  }

  async function druckeLaden(oracleId, name) {
    meldung.textContent = '';
    const mein = ++zaehler;
    try {
      const a = await lesen(`/api/pruefen/drucke?oracle_id=${encodeURIComponent(oracleId)}`);
      if (mein !== zaehler) return;
      if (a.verfuegbar === false) { meldung.textContent = 'Nur am PC mit Katalog.'; return; }
      druckeSetzen(name, a.drucke);
    } catch (f) {
      if (mein === zaehler) meldung.textContent = f.message;
    }
  }

  async function suchen(text) {
    const mein = ++zaehler;
    const t = text.trim();
    if (t.length < 2) { treffer.hidden = true; return; }
    try {
      const a = await lesen(`/api/pruefen/namen?q=${encodeURIComponent(t)}`);
      if (mein !== zaehler) return;
      meldung.textContent = '';
      if (a.verfuegbar === false) { meldung.textContent = 'Nur am PC mit Katalog.'; return; }
      treffer.replaceChildren(...a.treffer.map((h) => {
        const li = el('li');
        const b = el('button', 'pruef-treffer-eintrag',
          `<span>${esc(h.name)}</span>${h.treffer && h.treffer !== h.name ? `<small>${esc(h.treffer)} · ${esc(sprachName(h.sprache))}</small>` : ''}`);
        b.type = 'button';
        b.setAttribute('role', 'option');
        b.addEventListener('click', () => {
          treffer.hidden = true;
          feld.value = h.name;
          druckeLaden(h.oracle_id, h.name);
        });
        li.append(b);
        return li;
      }));
      if (!a.treffer.length) treffer.replaceChildren(el('li', 'pruef-treffer-leer', 'Keine Karte mit diesem Namen gefunden.'));
      treffer.hidden = false;
    } catch (f) {
      if (mein === zaehler) meldung.textContent = f.message;
    }
  }

  feld.addEventListener('input', () => {
    clearTimeout(uhr);
    uhr = setTimeout(() => suchen(feld.value), SUCH_VERZOEGERUNG);
  });
  feld.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); clearTimeout(uhr); suchen(feld.value); }
  });
  raster.addEventListener('click', (e) => {
    const b = e.target.closest('.pruef-kachel');
    if (!b) return;
    ausgewaehlt = drucke.find((p) => p.scryfall_id === b.dataset.id) ?? null;
    raster.querySelectorAll('.pruef-kachel').forEach((x) => x.setAttribute('aria-selected', String(x === b)));
    uebernehmen.disabled = !ausgewaehlt;
    if (ausgewaehlt) {
      wahlText.textContent = `Ausgewählt: ${ausgewaehlt.set_name ?? ausgewaehlt.set_code} · Nr. ${ausgewaehlt.nummer}`;
    }
  });
  mehrKnopf.addEventListener('click', () => { sichtbar += DRUCKE_ERSTE; zeichneDrucke(); });
  foilKnopf.addEventListener('click', () => {
    foil = !foil;
    foilKnopf.setAttribute('aria-pressed', String(foil));
    foilKnopf.textContent = foil ? 'Foil: ja' : 'Foil: nein';
  });
  abbrechen.addEventListener('click', () => d.schliessen(undefined));
  uebernehmen.addEventListener('click', async () => {
    if (!ausgewaehlt) return;
    uebernehmen.disabled = true;
    meldung.textContent = '';
    try {
      const gewaehlt = { scryfall_id: ausgewaehlt.scryfall_id, sprache: sprachFeld.value, foil };
      for (const { z, feldEl } of zusatzFelder) {
        if (z.art === 'auswahl') {
          gewaehlt[z.name] = feldEl.value;
        } else {
          const n = Number(feldEl.value);
          if (!Number.isInteger(n) || n < (z.min ?? 1) || n > (z.max ?? 999)) {
            meldung.textContent = `${z.etikett}: bitte eine ganze Zahl von ${z.min ?? 1} bis ${z.max ?? 999} eingeben.`;
            feldEl.focus();
            uebernehmen.disabled = false;
            return;
          }
          gewaehlt[z.name] = n;
        }
      }
      d.schliessen(speichern ? await speichern(gewaehlt) : gewaehlt);
    } catch (f) {
      meldung.textContent = f.message;
      uebernehmen.disabled = false;
    }
  });

  if (!window.matchMedia('(pointer: coarse)').matches) { feld.focus(); feld.select(); }
  if (k.oracle_id) druckeLaden(k.oracle_id, k.name);
  else if (k.name) suchen(k.name);
  return d.fertig.then((w) => w ?? null);
}

// ---------- Prüfmodus ----------

let aktiv = null;

function modusBauen(wurzel) {
  const s = {
    liste: [], position: 0, katalog: true, fall: null, letzte: null, ende: null,
    anfrage: 0, beschaeftigt: false, cache: new Map(), mehr: false,
  };
  const puffer = new Puffer((n) => aktion('anzahl', n));
  wurzel.innerHTML = `
    <section class="pruef" aria-labelledby="pruef-titel">
      <header class="pruef-kopf">
        <button type="button" class="knopf knopf-sym pruef-zurueck" aria-label="Zurück">${IKON.zurueck}</button>
        <div class="pruef-titel">
          <h1 id="pruef-titel">Prüfen</h1>
          <p class="pruef-fortschritt" aria-live="polite"></p>
        </div>
        <button type="button" class="knopf knopf-sym pruef-vorher" aria-label="Vorheriger Fall" disabled>${IKON.hoch}</button>
      </header>
      <div class="pruef-balken" role="progressbar" aria-label="Fortschritt" aria-valuemin="0" aria-valuemax="1" aria-valuenow="0"><span></span></div>
      <div class="pruef-haupt"></div>
      <div class="pruef-unten">
        <p class="pruef-leiste" role="status" aria-live="polite" hidden></p>
        <div class="pruef-aktionen" role="group" aria-label="Aktionen"></div>
        <p class="pruef-hinweis" hidden></p>
      </div>
    </section>`;
  const q = (sel) => wurzel.querySelector(sel);
  const haupt = q('.pruef-haupt');
  const leiste = q('.pruef-leiste');
  const aktionen = q('.pruef-aktionen');
  const hinweis = q('.pruef-hinweis');
  const vorher = q('.pruef-vorher');
  q('.pruef-kopf').append(anmeldeKnopf());
  q('.pruef-zurueck').addEventListener('click', () => zurueck('#/'));
  vorher.addEventListener('click', () => befehl('vorher'));

  // ----- Anzeige

  function fortschritt() {
    const n = s.liste.length;
    const text = s.ende ? (n ? `${n} von ${n}` : '') : `${s.position + 1} von ${n}`;
    q('.pruef-fortschritt').textContent = text;
    const balken = q('.pruef-balken');
    const stand = s.ende ? n : s.position + 1;
    balken.setAttribute('aria-valuemax', String(Math.max(n, 1)));
    balken.setAttribute('aria-valuenow', String(n ? stand : 0));
    balken.firstElementChild.style.transform = `scaleX(${n ? stand / n : 0})`;
    vorher.disabled = s.ende ? !n : s.position <= 0;
  }

  function leisteZeigen({ text, rueckgaengig, fehler, zurueckgenommen }) {
    leiste.hidden = false;
    leiste.classList.toggle('pruef-leiste-fehler', Boolean(fehler));
    leiste.setAttribute('role', fehler ? 'alert' : 'status');
    if (fehler) {
      leiste.innerHTML = `<span class="pruef-leiste-text">${esc(text)}</span>`;
      return;
    }
    leiste.innerHTML = `<span class="pruef-leiste-text">${zurueckgenommen ? '<strong>Zurückgenommen</strong>' : '<strong>Gespeichert</strong>'} · ${esc(text)}</span>`
      + (rueckgaengig ? '<button type="button" class="knopf pruef-rueck">Rückgängig</button>' : '');
    leiste.querySelector('.pruef-rueck')?.addEventListener('click', () => befehl('rueckgaengig'));
  }

  function leisteLoeschen() {
    leiste.hidden = true;
    leiste.replaceChildren();
  }

  function taste(name, text, klasse = '', extra = '') {
    return `<button type="button" class="knopf pruef-taste ${klasse}" data-befehl="${name}"${extra}>${text}</button>`;
  }

  function aktionenZeichnen() {
    hinweis.hidden = true;
    if (s.ende) { aktionen.replaceChildren(); aktionen.hidden = true; return; }
    aktionen.hidden = false;
    const f = s.fall;
    if (!f) { aktionen.replaceChildren(); return; }
    let html;
    if (!angemeldet()) {
      html = taste('anmelden', 'Zum Prüfen anmelden', 'knopf-haupt pruef-taste-breit') + taste('weiter', 'Weiter');
    } else if (f.karte_id) {
      html = taste('stimmt', 'Stimmt', 'knopf-haupt')
        + taste('druck', 'Druck ändern', '', f.katalog ? '' : ' disabled aria-describedby="pruef-katalog-hinweis"')
        + (f.tasche_id ? taste('keine', 'Keine Karte') : '')
        + taste('weiter', 'Weiter');
      if (!f.katalog) {
        hinweis.id = 'pruef-katalog-hinweis';
        hinweis.textContent = 'Druck ändern und Namenssuche: Nur am PC mit Katalog.';
        hinweis.hidden = false;
      }
    } else {
      html = taste('keine', 'Keine Karte', 'knopf-haupt') + taste('weiter', 'Weiter');
    }
    aktionen.innerHTML = html;
    aktionen.dataset.anzahl = String(aktionen.children.length);
    aktionen.querySelectorAll('button').forEach((b) => { b.disabled = b.disabled || s.beschaeftigt; });
  }

  function optionen() {
    return {
      angemeldet: angemeldet(), gross: true, puffer, befehl,
      mehr: () => s.mehr, mehrMerken: (an) => { s.mehr = an; },
    };
  }

  function fallZeichnen() {
    haupt.replaceChildren(fallDom(s.fall, optionen()));
    aktionenZeichnen();
  }

  function meldung(html) {
    haupt.innerHTML = html;
    haupt.querySelector('[data-befehl]')?.focus({ preventScroll: true });
  }

  // ----- Laden

  async function warteschlangeLaden() {
    const a = await lesen('/api/pruefen/warteschlange');
    s.liste = a.faelle;
    s.katalog = a.katalog;
    s.cache.clear();
    return a;
  }

  async function detail(eintrag) {
    const schluessel = fallSchluessel(eintrag);
    if (!s.cache.has(schluessel)) s.cache.set(schluessel, lesen(fallPfad(eintrag)));
    const antwort = s.cache.get(schluessel);
    try {
      return await antwort;
    } catch (f) {
      s.cache.delete(schluessel);
      throw f;
    }
  }

  function vorladen(position) {
    const e = s.liste[position];
    if (!e) return;
    detail(e).then((f) => {
      if (f.karte) new Image().src = bildUrl('scryfall', f.karte.bild ?? '', 400);
      if (f.ausschnitt) new Image().src = bildUrl('foto', f.ausschnitt, 400);
    }).catch(() => {});
  }

  async function fallZeigen(position) {
    s.position = position;
    s.ende = null;
    const mein = ++s.anfrage;
    fortschritt();
    const eintrag = s.liste[position];
    let fall;
    try {
      fall = await detail(eintrag);
    } catch (f) {
      if (mein !== s.anfrage) return;
      if (f.status === 404) {  // inzwischen anderswo erledigt oder geändert
        s.liste.splice(position, 1);
        leisteZeigen({ text: 'Ein Fall wurde inzwischen anderswo erledigt und übersprungen.', fehler: true });
        return position < s.liste.length ? fallZeigen(position) : endeZeigen();
      }
      s.fall = null;
      aktionenZeichnen();
      meldung(`<p class="pruef-meldung">${esc(f.message)}</p><button type="button" class="knopf pruef-taste" data-befehl="neu">Erneut versuchen</button>`);
      return;
    }
    if (mein !== s.anfrage) return;
    s.fall = fall;
    s.katalog = fall.katalog;
    fallZeichnen();
    vorladen(position + 1);
  }

  async function endeZeigen() {
    s.fall = null;
    ++s.anfrage;
    let rest = [];
    try {
      rest = (await warteschlangeLaden()).faelle;
    } catch { /* Anzeige ohne aktuelle Zahl */ }
    s.ende = rest.length ? 'offen' : 'fertig';
    s.restZahl = rest.length;
    fortschritt();
    aktionenZeichnen();
    const zahl = s.liste.length;
    if (!rest.length) {
      meldung(`<div class="pruef-ende"><span class="pruef-ende-zeichen" aria-hidden="true">${IKON.haken}</span>
        <h2>Alles geprüft</h2><p>Es gibt keine unsicheren Karten mehr${zahl ? '' : ' – nichts zu tun'}.</p>
        <button type="button" class="knopf knopf-haupt pruef-taste" data-befehl="regal">Zurück ins Regal</button></div>`);
    } else {
      meldung(`<div class="pruef-ende"><h2>Ende der Liste</h2>
        <p>${rest.length === 1 ? 'Ein Fall ist noch offen' : `${rest.length} Fälle sind noch offen`} (übersprungen oder neu hinzugekommen).</p>
        <button type="button" class="knopf knopf-haupt pruef-taste" data-befehl="neu">Noch einmal ansehen</button>
        <button type="button" class="knopf pruef-taste" data-befehl="regal">Zurück ins Regal</button></div>`);
    }
  }

  async function start() {
    haupt.innerHTML = '<p class="pruef-meldung">Lade …</p>';
    s.ende = null;
    fortschritt();
    try {
      const a = await warteschlangeLaden();
      s.position = 0;
      leisteLoeschen();
      s.letzte = null;
      if (!a.faelle.length) return endeZeigen();
      return fallZeigen(0);
    } catch (f) {
      s.liste = [];
      fortschritt();
      meldung(`<p class="pruef-meldung">${esc(f.message)}</p><button type="button" class="knopf pruef-taste" data-befehl="neu">Erneut versuchen</button>`);
      return undefined;
    }
  }

  // ----- Befehle

  function weiter() {
    const n = s.position + 1;
    return n < s.liste.length ? fallZeigen(n) : endeZeigen();
  }

  async function aktion(name, wert, fallVorher = s.fall) {
    const fall = fallVorher;
    if (!fall || s.beschaeftigt) return;
    const index = s.position;
    s.beschaeftigt = true;
    aktionenZeichnen();
    try {
      const r = await AKTIONEN[name](fall, wert);
      if (!r) return;
      s.cache.clear();
      s.letzte = r.rueckgaengig ? { pfad: r.rueckgaengig, text: r.text, index } : null;
      leisteZeigen({ text: r.text, rueckgaengig: Boolean(r.rueckgaengig) });
      if (r.gespeichert) meldeAenderung(fall);
      if (r.weiter) {
        s.beschaeftigt = false;
        await weiter();
      } else {
        await fallZeigen(index);
      }
    } catch (f) {
      leisteZeigen({ text: f.message, fehler: true });
      if (f.status === 404 || f.status === 409) { s.cache.clear(); await fallZeigen(index).catch(() => {}); }
    } finally {
      s.beschaeftigt = false;
      aktionenZeichnen();
    }
  }

  async function zuruecknehmen(pfad, index, text) {
    s.beschaeftigt = true;
    aktionenZeichnen();
    try {
      const antwort = await senden(pfad);
      s.cache.clear();
      s.letzte = null;
      leisteZeigen({ text, zurueckgenommen: true });
      meldeAenderung(s.fall ?? s.liste[index] ?? null);
      if (antwort && index !== null && index < s.liste.length) {
        s.beschaeftigt = false;
        await fallZeigen(index);  // der Fall steht wieder da
      }
    } catch (f) {
      leisteZeigen({ text: f.message, fehler: true });
    } finally {
      s.beschaeftigt = false;
      aktionenZeichnen();
    }
  }

  async function befehl(name, wert) {
    if (name === 'neu') { await start(); return; }
    if (name === 'regal') { gehe('#/'); return; }
    if (name === 'anmelden') { if (await anmelden()) { aktionenZeichnen(); if (s.fall) fallZeichnen(); } return; }
    if (s.beschaeftigt) return;
    if (name !== 'anzahl') await puffer.leeren();
    if (name === 'weiter') { await weiter(); return; }
    if (name === 'vorher') {
      const n = s.ende ? s.liste.length - 1 : s.position - 1;
      if (n >= 0) await fallZeigen(n);
      return;
    }
    if (name === 'rueckgaengig') {
      if (s.letzte) await zuruecknehmen(s.letzte.pfad, s.letzte.index, s.letzte.text);
      return;
    }
    if (name === 'zuruecknehmen') {  // „Rückgängig: …" aus „Mehr" für den gezeigten Fall
      if (s.fall) await zuruecknehmen(s.fall.art === 'karte' ? `/api/karte/${s.fall.karte_id}/rueckgaengig`
        : `/api/tasche/${s.fall.tasche_id}/rueckgaengig`, s.position, s.fall.rueckgaengig);
      return;
    }
    await aktion(name, wert);
  }

  aktionen.addEventListener('click', (e) => {
    const b = e.target.closest('[data-befehl]');
    if (b && !b.disabled) befehl(b.dataset.befehl);
  });
  haupt.addEventListener('click', (e) => {
    const b = e.target.closest('[data-befehl]');
    if (b) befehl(b.dataset.befehl);
  });

  // ----- Ereignisse

  const beiSitzung = () => { aktionenZeichnen(); if (s.fall) fallZeichnen(); };
  const beiTaste = (e) => {
    if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey || dialogOffen() || wurzel.closest('[inert]')) return;
    if (/^(INPUT|SELECT|TEXTAREA)$/.test(e.target?.tagName ?? '')) return;
    if (e.key === 'ArrowRight' && !s.ende) befehl('weiter');
    else if (e.key === 'ArrowLeft') befehl('vorher');
  };
  window.addEventListener('binder:sitzung', beiSitzung);
  document.addEventListener('keydown', beiTaste);

  return {
    start,
    abraeumen() {
      puffer.verwerfen();
      window.removeEventListener('binder:sitzung', beiSitzung);
      document.removeEventListener('keydown', beiTaste);
    },
    /** Nach Änderungen außerhalb (zum Beispiel in der großen Karte): den gezeigten Fall neu laden. */
    async neuZeichnen() {
      if (s.beschaeftigt || !s.fall) return;
      s.cache.clear();
      try { await fallZeigen(s.position); } catch { /* bleibt, wie es ist */ }
    },
  };
}

export async function zeigen(wurzel) {
  aktiv?.abraeumen();
  aktiv = modusBauen(wurzel);
  await aktiv.start();
}

export async function aktualisieren(parameter) {
  if (aktiv && !parameter.karte) await aktiv.neuZeichnen();
}

export function verlassen() {
  aktiv?.abraeumen();
  aktiv = null;
  return Promise.resolve();
}

// ---------- Dialog für genau eine Karte (aus der Kartenansicht) ----------

/** Öffnet die Aktionen des Prüfmodus für eine Karte. Gibt zurück, ob etwas geändert wurde. */
export async function bearbeiten(kartenId, { titel = 'Karte prüfen' } = {}) {
  const id = Number(kartenId);
  const stelle = el('div', 'pruef-b', '<div class="pruef-haupt"></div><p class="pruef-leiste" role="status" aria-live="polite" hidden></p>');
  const d = dialog({ titel, klasse: 'pruef-dialog pruef-bearbeiten', voll: true, inhalt: stelle });
  const haupt = stelle.querySelector('.pruef-haupt');
  const leiste = stelle.querySelector('.pruef-leiste');
  let fall = null;
  let letzte = null;
  let geaendert = false;
  let beschaeftigt = false;
  let mehr = false;
  let entfernt = false;
  const puffer = new Puffer((n) => ausfuehren('anzahl', n));

  const fuss = () => {
    d.fuss.replaceChildren();
    const knopf = (befehlName, text, klasse = '', aus = false) => {
      const b = el('button', `knopf pruef-taste ${klasse}`, text);
      b.type = 'button';
      b.disabled = aus || beschaeftigt;
      b.addEventListener('click', () => befehl(befehlName));
      d.fuss.append(b);
    };
    if (fall && angemeldet()) {
      knopf('stimmt', 'Stimmt', 'knopf-haupt');
      knopf('druck', 'Druck ändern', '', !fall.katalog);
      if (fall.tasche_id) knopf('keine', 'Keine Karte');
    }
    const fertig = el('button', 'knopf pruef-taste', 'Fertig');
    fertig.type = 'button';
    fertig.addEventListener('click', async () => { await puffer.leeren(); d.schliessen(geaendert); });
    d.fuss.append(fertig);
    if (fall && !fall.katalog && angemeldet()) {
      d.fuss.append(el('p', 'pruef-hinweis', 'Druck ändern und Namenssuche: Nur am PC mit Katalog.'));
    }
  };

  const leisteZeigen = ({ text, rueckgaengig, fehler, zurueckgenommen }) => {
    leiste.hidden = false;
    leiste.classList.toggle('pruef-leiste-fehler', Boolean(fehler));
    leiste.setAttribute('role', fehler ? 'alert' : 'status');
    leiste.innerHTML = fehler ? `<span class="pruef-leiste-text">${esc(text)}</span>`
      : `<span class="pruef-leiste-text">${zurueckgenommen ? '<strong>Zurückgenommen</strong>' : '<strong>Gespeichert</strong>'} · ${esc(text)}</span>`
        + (rueckgaengig ? '<button type="button" class="knopf pruef-rueck">Rückgängig</button>' : '');
    leiste.querySelector('.pruef-rueck')?.addEventListener('click', () => befehl('rueckgaengig'));
  };

  const zeichnen = () => {
    if (entfernt || !fall) {
      haupt.innerHTML = `<p class="pruef-meldung">${entfernt ? 'Die Karte wurde entfernt.' : 'Die Karte ließ sich nicht laden.'}</p>`;
    } else {
      haupt.replaceChildren(fallDom(fall, {
        angemeldet: angemeldet(), gross: false, kompakt: true, puffer, befehl,
        mehr: () => mehr, mehrMerken: (an) => { mehr = an; },
      }));
    }
    fuss();
  };

  const laden = async () => {
    try {
      fall = await lesen(`/api/pruefen/karte/${id}`);
      entfernt = false;
    } catch (f) {
      fall = null;
      if (f.status !== 404) leisteZeigen({ text: f.message, fehler: true });
    }
    zeichnen();
  };

  async function ausfuehren(name, wert) {
    if (!fall || beschaeftigt) return;
    beschaeftigt = true;
    fuss();
    try {
      const r = await AKTIONEN[name](fall, wert);
      if (!r) return;
      geaendert = geaendert || r.gespeichert;
      letzte = r.rueckgaengig ? { pfad: r.rueckgaengig, text: r.text } : null;
      leisteZeigen({ text: r.text, rueckgaengig: Boolean(r.rueckgaengig) });
      if (r.gespeichert) meldeAenderung(fall);
      if (name === 'entfernen' && r.gespeichert) { entfernt = true; fall = null; zeichnen(); } else { await laden(); }
    } catch (f) {
      leisteZeigen({ text: f.message, fehler: true });
      if (f.status === 404 || f.status === 409) await laden();
    } finally {
      beschaeftigt = false;
      fuss();
    }
  }

  async function befehl(name, wert) {
    if (beschaeftigt) return;
    if (name === 'rueckgaengig' || name === 'zuruecknehmen') {
      const pfad = name === 'rueckgaengig' ? letzte?.pfad : `/api/karte/${id}/rueckgaengig`;
      const text = name === 'rueckgaengig' ? letzte?.text : fall?.rueckgaengig;
      if (!pfad) return;
      beschaeftigt = true;
      fuss();
      try {
        await senden(pfad);
        geaendert = true;
        letzte = null;
        leisteZeigen({ text, zurueckgenommen: true });
        meldeAenderung(fall ?? { ort: null });
        await laden();
      } catch (f) {
        leisteZeigen({ text: f.message, fehler: true });
      } finally {
        beschaeftigt = false;
        fuss();
      }
      return;
    }
    if (name !== 'anzahl') await puffer.leeren();
    await ausfuehren(name, wert);
  }

  const beiSitzung = () => zeichnen();
  window.addEventListener('binder:sitzung', beiSitzung);
  await laden();
  const ergebnisWert = await d.fertig;
  window.removeEventListener('binder:sitzung', beiSitzung);
  puffer.verwerfen();
  return ergebnisWert === true || geaendert;
}
