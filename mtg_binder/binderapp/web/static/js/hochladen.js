// Upload-Dialog: Fotos auswählen, anordnen, Leerseiten dazwischen, Binder wählen oder anlegen,
// Übertragung Foto für Foto mit Fortschritt.
//
//   oeffnen({ binderId?, nach?, neu?, seiteId?, seiteNummer? })
//     → Promise<{ binderId, seiten: [{ id, nummer }] } | null>   (null: nichts hochgeladen)
//
//   binderId     bestehender Binder (Vorbelegung)          nach   Vorbelegung „nach Seite k" (null = hinten)
//   neu          true: Vorbelegung „Neuer Binder"           seiteId, seiteNummer: ein Foto für diese Leerseite nachtragen
//
// Grundsätze: Seitenzahlen werden nie eingetippt, sondern als Stelle gewählt („hinten", „nach Seite k").
// Vorschaubilder entstehen nur klein im Browser (createImageBitmap mit resizeWidth), nie wird ein Foto in
// voller Größe als <img> geladen. Die Übertragung läuft strikt nacheinander, damit die Reihenfolge stimmt;
// bei Fehlern hält sie an und fragt.

import { dialog, bestaetigen } from './dialog.js';
import { holen, senden, geaendert, fehlerText, SchnittstellenFehler, adresse } from './gemeinsam.js';
import { laden, angemeldet, anmelden, sitzung } from './anmeldung.js';

const MB = 1024 * 1024;
const VORSCHAU_BREITE = 160;
const VORSCHAU_MAX = 25 * MB;
const TYPEN = new Set(['image/jpeg', 'image/heic', 'image/heif']);
const ENDUNG = /\.(jpe?g|heic|heif)$/i;
const RASTER = ['2x2', '3x3'];
const FARBEN = [['schwarz', 'Schwarz'], ['durchsichtig', 'Durchsichtig'], ['gelb', 'Gelb'], ['blau', 'Blau'],
  ['gruen', 'Grün'], ['rot', 'Rot'], ['braun', 'Braun']];
const ABBRUCH = { abbruch: true };

const svg = (pfade, g = 20) => `<svg viewBox="0 0 24 24" width="${g}" height="${g}" fill="none" stroke="currentColor"`
  + ` stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${pfade}</svg>`;
const SYM = {
  hoch: svg('<path d="M6 14l6-6 6 6"/>'),
  runter: svg('<path d="M6 10l6 6 6-6"/>'),
  weg: svg('<path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12M10.5 11v5M13.5 11v5"/>'),
  plus: svg('<path d="M12 5v14M5 12h14"/>', 18),
  bild: svg('<rect x="4" y="5" width="16" height="14" rx="2"/><circle cx="9" cy="10" r="1.5"/><path d="M5 17l5-5 4 4 2-2 3 3"/>', 26),
  leer: svg('<rect x="5" y="3.5" width="14" height="17" rx="2" stroke-dasharray="2.5 2.5"/>', 26),
  hinzu: svg('<path d="M12 16V5M7.5 9.5L12 5l4.5 4.5M5 15v3a1.5 1.5 0 0 0 1.5 1.5h11A1.5 1.5 0 0 0 19 18v-3"/>', 26),
};

let zaehler = 0;

const zahl = (n, ein, mehr) => `${n} ${n === 1 ? ein : mehr}`;

function groesseText(bytes) {
  if (bytes >= MB) return `${(bytes / MB).toFixed(1).replace('.', ',')} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function pruefeDatei(datei) {
  const typOk = TYPEN.has((datei.type || '').toLowerCase());
  if (!typOk && !ENDUNG.test(datei.name)) return 'Falscher Dateityp: nur JPEG oder HEIC';
  if (datei.size === 0) return 'Die Datei ist leer.';
  const max = sitzung().max_foto_mb || 60;
  if (datei.size > max * MB) return `Foto zu groß, höchstens ${max} MB`;
  return null;
}

function knopfMachen(text, klasse, klick) {
  const k = document.createElement('button');
  k.type = 'button';
  k.className = `knopf${klasse ? ` ${klasse}` : ''}`;
  k.textContent = text;
  k.addEventListener('click', klick);
  return k;
}

export async function oeffnen(ziel = {}) {
  await laden();
  const p = `hl${++zaehler}`;
  const nachtragen = ziel.seiteId != null;

  // ---------- Zustand ----------
  const z = {
    phase: 'auswahl',            // auswahl | senden | fertig
    eintraege: [],
    naechsteId: 0,
    binderListe: [],
    listeFehler: false,
    wahl: nachtragen ? String(ziel.binderId ?? '') : '',   // '' | 'neu' | Binder-ID
    name: '', raster: '3x3', farbe: 'schwarz',
    stelle: 'ende',              // 'ende' | Zahl als Text (Nach Seite k, 0 = vor Seite 1)
    k: 0,                        // beim Übertragen: Nummer der Seite, nach der eingefügt wird
    binderId: ziel.binderId ?? null,
    hochgeladen: [],             // [{ id, nummer, art }]
    abbruch: false,
    geschlossen: false,
    xhr: null,
    frage: null,                 // laufende Rückfrage: { loesen(wert) }
    fragtSchliessen: false,
    uebersprungen: 0,
  };

  // ---------- Gerüst ----------
  const wurzel = document.createElement('div');
  wurzel.className = 'hl-wurzel';
  wurzel.dataset.phase = 'auswahl';
  wurzel.innerHTML = `
    <section class="hl-abschnitt" data-teil="nachtrag" hidden>
      <h3 class="etikett">Leerseite</h3>
      <p class="hl-text-gross">Foto für Leerseite ${Number(ziel.seiteNummer) || ''}</p>
    </section>
    <section class="hl-abschnitt" data-teil="binder">
      <h3 class="etikett">Binder</h3>
      <label class="nur-vorleser" for="${p}-binder">Ziel-Binder</label>
      <select class="feld" id="${p}-binder"></select>
      <div class="hl-neu" hidden>
        <label class="etikett feld-etikett" for="${p}-name">Name</label>
        <input class="feld" id="${p}-name" maxlength="60" autocomplete="off" spellcheck="false" enterkeyhint="done"
               placeholder="z. B. Schwarzer Binder">
        <p class="meldung" role="alert" data-meldung="name"></p>
        <fieldset class="hl-gruppe">
          <legend class="etikett">Raster</legend>
          <div class="hl-segmente">${RASTER.map((r) => `<label class="hl-segment"><input type="radio" name="${p}-raster" value="${r}"`
            + `${r === '3x3' ? ' checked' : ''}><span>${r}</span></label>`).join('')}</div>
        </fieldset>
        <fieldset class="hl-gruppe">
          <legend class="etikett">Farbe <span class="hl-farbname">Schwarz</span></legend>
          <div class="hl-farben">${FARBEN.map(([wert, text]) => `<label class="hl-farbe" title="${text}"><input type="radio"`
            + ` name="${p}-farbe" value="${wert}"${wert === 'schwarz' ? ' checked' : ''}><span class="hl-punkt"`
            + ` data-farbe="${wert}"></span><span class="nur-vorleser">${text}</span></label>`).join('')}</div>
        </fieldset>
      </div>
    </section>
    <section class="hl-abschnitt" data-teil="stelle" hidden>
      <h3 class="etikett">Stelle</h3>
      <label class="nur-vorleser" for="${p}-stelle">Stelle im Binder</label>
      <select class="feld" id="${p}-stelle"></select>
    </section>
    <section class="hl-lauf" hidden aria-label="Übertragung">
      <p class="hl-ziel"></p>
      <div class="hl-gesamt" role="progressbar" aria-label="Gesamtfortschritt" aria-valuemin="0" aria-valuemax="100"
           aria-valuenow="0"><span class="hl-fuell"></span></div>
      <p class="hl-lauftext" aria-live="polite"></p>
      <div class="hl-frage" role="alert" hidden><p class="hl-frage-name"></p><p class="hl-frage-text"></p></div>
      <div class="hl-abschluss" role="status" hidden><p class="hl-abschluss-text"></p><p class="hl-abschluss-erkennung"></p></div>
    </section>
    <section class="hl-abschnitt" data-teil="fotos">
      <h3 class="etikett">${nachtragen ? 'Foto' : 'Fotos'}</h3>
      <div class="hl-ablage">
        ${SYM.hinzu}
        <button type="button" class="knopf hl-wahl">${nachtragen ? 'Foto auswählen' : 'Fotos auswählen'}</button>
        <p class="hl-ablage-text"><span class="hl-nur-maus">Oder hierher ziehen. </span>${nachtragen
    ? 'Nur JPEG oder HEIC.' : 'Mehrmals auswählen hängt an. Nur JPEG oder HEIC.'}</p>
        <input type="file" class="hl-datei" accept="image/*"${nachtragen ? '' : ' multiple'} hidden>
      </div>
      <button type="button" class="hl-zwischen" data-zp="0" ${nachtragen ? 'hidden' : ''}>${SYM.plus}<span>Leerseite hier</span></button>
      <ol class="hl-liste" aria-label="${nachtragen ? 'Gewähltes Foto' : 'Ausgewählte Fotos und Leerseiten'}"></ol>
      <p class="hl-summe"></p>
      <p class="meldung" role="alert" data-meldung="allgemein"></p>
    </section>
    <div class="nur-vorleser" aria-live="polite" data-ansage></div>`;

  const q = (s) => wurzel.querySelector(s);
  const binderWahl = q(`#${p}-binder`);
  const stelleWahl = q(`#${p}-stelle`);
  const neuBereich = q('.hl-neu');
  const nameFeld = q(`#${p}-name`);
  const farbName = q('.hl-farbname');
  const liste = q('.hl-liste');
  const dateiFeld = q('.hl-datei');
  const ablage = q('.hl-ablage');
  const summe = q('.hl-summe');
  const lauf = q('.hl-lauf');
  const gesamt = q('.hl-gesamt');
  const gesamtFuell = q('.hl-gesamt .hl-fuell');
  const laufText = q('.hl-lauftext');
  const frageBox = q('.hl-frage');
  const abschlussBox = q('.hl-abschluss');
  const ansage = q('[data-ansage]');
  const meldung = (n) => q(`[data-meldung="${n}"]`);

  const d = dialog({ titel: 'Fotos hinzufügen', voll: true, klasse: 'hl-dialog', inhalt: wurzel });

  if (nachtragen) {
    q('[data-teil="nachtrag"]').hidden = false;
    q('[data-teil="binder"]').hidden = true;
  }

  // ---------- Hilfen ----------
  const gueltig = (e) => e.art === 'leer' || !e.fehler;
  const aktive = () => z.eintraege.filter(gueltig);
  const binderAktuell = () => z.binderListe.find((b) => String(b.id) === z.wahl) ?? null;
  const seitenZahl = () => binderAktuell()?.seiten ?? 0;

  /** Nummer der ersten neuen Seite (vor dem Übertragen aus der Wahl, währenddessen aus k). */
  function startSeite() {
    if (nachtragen) return Number(ziel.seiteNummer) || 0;
    if (z.phase !== 'auswahl') return z.k + 1;
    if (z.wahl === 'neu') return 1;
    return (z.stelle === 'ende' ? seitenZahl() : Number(z.stelle)) + 1;
  }

  function ansagen(text) {
    ansage.textContent = '';
    requestAnimationFrame(() => { ansage.textContent = text; });
  }

  /** Seitennummer, die ein Eintrag bekommt (nur gültige und noch nicht erledigte zählen). */
  function zielSeite(e) {
    if (nachtragen) return startSeite();
    let rang = 0;
    for (const x of z.eintraege) {
      if (x === e) break;
      if (z.phase === 'auswahl' ? gueltig(x) : (x.zustand === 'wartet' || x.zustand === 'laeuft')) rang += 1;
    }
    return startSeite() + rang;
  }

  // ---------- Binder und Stelle ----------
  function binderOptionen() {
    binderWahl.replaceChildren();
    const neuOption = new Option('Neuer Binder …', 'neu');
    if (z.binderListe.length) {
      binderWahl.append(new Option('Binder wählen …', '', false, z.wahl === ''));
      binderWahl.options[0].disabled = true;
    }
    for (const b of z.binderListe) {
      binderWahl.append(new Option(`${b.name} · ${b.raster} · ${zahl(b.seiten, 'Seite', 'Seiten')}`, String(b.id), false, String(b.id) === z.wahl));
    }
    binderWahl.append(neuOption);
    binderWahl.value = z.wahl;
    if (binderWahl.value !== z.wahl) binderWahl.selectedIndex = 0;
  }

  function stelleOptionen() {
    const bereich = q('[data-teil="stelle"]');
    const n = seitenZahl();
    const zeigen = !nachtragen && z.wahl !== 'neu' && z.wahl !== '' && n > 0;
    bereich.hidden = !zeigen;
    stelleWahl.replaceChildren(new Option(`Hinten anhängen (ab Seite ${n + 1})`, 'ende'));
    if (zeigen) {
      stelleWahl.append(new Option('Vor Seite 1', '0'));
      for (let s = 1; s < n; s += 1) stelleWahl.append(new Option(`Nach Seite ${s}`, String(s)));
    }
    if (![...stelleWahl.options].some((o) => o.value === z.stelle)) z.stelle = 'ende';
    stelleWahl.value = z.stelle;
  }

  function wahlGeaendert() {
    z.binderId = z.wahl && z.wahl !== 'neu' ? Number(z.wahl) : null;
    neuBereich.hidden = z.wahl !== 'neu';
    stelleOptionen();
    meldung('name').textContent = '';
    zeichneListe();
  }

  binderWahl.addEventListener('change', () => {
    z.wahl = binderWahl.value;
    z.stelle = 'ende';
    wahlGeaendert();
  });
  stelleWahl.addEventListener('change', () => { z.stelle = stelleWahl.value; zeichneListe(); });
  nameFeld.addEventListener('input', () => { z.name = nameFeld.value; meldung('name').textContent = ''; pruefen(); });
  wurzel.addEventListener('change', (ev) => {
    const t = ev.target;
    if (t.name === `${p}-raster`) z.raster = t.value;
    if (t.name === `${p}-farbe`) {
      z.farbe = t.value;
      farbName.textContent = FARBEN.find(([w]) => w === t.value)?.[1] ?? '';
    }
  });

  // ---------- Vorschaubilder (klein, nacheinander) ----------
  let kette = Promise.resolve();
  function vorschauAnstellen(e) {
    kette = kette.then(() => vorschauZeichnen(e)).catch(() => {});
  }
  async function vorschauZeichnen(e) {
    if (e.entfernt || z.geschlossen) return;
    if (e.datei.size > VORSCHAU_MAX || typeof createImageBitmap !== 'function') return;
    let bmp;
    try {
      bmp = await createImageBitmap(e.datei, { resizeWidth: VORSCHAU_BREITE, resizeQuality: 'low' });
    } catch { return; }
    try {
      if (e.entfernt || z.geschlossen) return;
      const c = document.createElement('canvas');
      c.width = VORSCHAU_BREITE;
      c.height = Math.max(1, Math.round((VORSCHAU_BREITE * bmp.height) / bmp.width));
      c.getContext('2d').drawImage(bmp, 0, 0, c.width, c.height);
      c.className = 'hl-bild';
      e.canvas = c;
      e.vorschauEl?.replaceChildren(c);
    } finally {
      bmp.close?.();
    }
  }

  // ---------- Liste ----------
  function metaText(e) {
    switch (e.zustand) {
      case 'wartet': return `Wartet · wird Seite ${zielSeite(e)}`;
      case 'laeuft': return e.art === 'leer' ? 'Wird eingefügt …' : `Wird übertragen … ${Math.round(e.anteil * 100)} %`;
      case 'speichern': return 'Wird gespeichert …';
      case 'fertig': return `Fertig · Seite ${e.nummer}`;
      case 'uebersprungen': return 'Übersprungen';
      case 'abgebrochen': return 'Nicht hochgeladen';
      case 'fehler': return 'Fehler';
      default:
        if (!gueltig(e)) return e.art === 'foto' ? groesseText(e.datei.size) : '';
        return `${e.art === 'foto' ? `${groesseText(e.datei.size)} · ` : ''}wird Seite ${zielSeite(e)}`;
    }
  }

  function eintragAktualisieren(e) {
    if (!e.el) return;
    e.el.dataset.zustand = !gueltig(e) ? 'ungueltig' : (e.zustand ?? 'bereit');
    e.metaEl.textContent = metaText(e);
    e.fehlerEl.textContent = e.fehlerText || (!gueltig(e) ? e.fehler : '');
    e.fehlerEl.hidden = !e.fehlerEl.textContent;
    e.fuellEl.style.transform = `scaleX(${e.zustand === 'fertig' ? 1 : e.anteil || 0})`;
    e.balkenEl.hidden = !(e.zustand === 'laeuft' || e.zustand === 'speichern');
  }

  function baueEintrag(e, i, n) {
    const huelle = document.createElement('li');
    huelle.className = 'hl-huelle';
    const karte = document.createElement('div');
    karte.className = 'hl-eintrag';
    karte.dataset.eid = e.id;
    const bez = e.art === 'leer' ? 'Leerseite' : e.name;
    karte.innerHTML = '<div class="hl-vorschau" aria-hidden="true"></div>'
      + '<div class="hl-text"><span class="hl-name"></span><span class="hl-meta"></span>'
      + '<span class="hl-fehler" hidden></span><span class="hl-balken" hidden><span class="hl-fuell"></span></span></div>'
      + '<div class="hl-knoepfe"></div>';
    karte.querySelector('.hl-name').textContent = bez;
    e.el = karte;
    e.vorschauEl = karte.querySelector('.hl-vorschau');
    e.metaEl = karte.querySelector('.hl-meta');
    e.fehlerEl = karte.querySelector('.hl-fehler');
    e.balkenEl = karte.querySelector('.hl-balken');
    e.fuellEl = karte.querySelector('.hl-balken .hl-fuell');
    if (e.canvas) e.vorschauEl.append(e.canvas);
    else e.vorschauEl.innerHTML = e.art === 'leer' ? SYM.leer : SYM.bild;
    const knoepfe = karte.querySelector('.hl-knoepfe');
    const sym = (akt, name, symbol, klick, aus) => {
      const k = document.createElement('button');
      k.type = 'button';
      k.className = `knopf knopf-sym${akt === 'weg' ? ' hl-weg' : ''}`;
      k.dataset.akt = akt;
      k.setAttribute('aria-label', `${bez}: ${name}`);
      k.title = name;
      k.innerHTML = symbol;
      k.disabled = aus;
      k.addEventListener('click', klick);
      knoepfe.append(k);
    };
    if (!nachtragen) {
      sym('hoch', 'nach oben', SYM.hoch, () => verschieben(e, -1), i === 0);
      sym('runter', 'nach unten', SYM.runter, () => verschieben(e, 1), i === n - 1);
    }
    sym('weg', 'entfernen', SYM.weg, () => entfernen(e), false);
    huelle.append(karte);
    if (!nachtragen) {
      const zw = document.createElement('button');
      zw.type = 'button';
      zw.className = 'hl-zwischen';
      zw.dataset.zp = String(i + 1);
      zw.setAttribute('aria-label', `Leerseite nach ${bez} einfügen`);
      zw.innerHTML = `${SYM.plus}<span>Leerseite hier</span>`;
      zw.addEventListener('click', () => leerseiteEinfuegen(i + 1));
      huelle.append(zw);
    }
    eintragAktualisieren(e);
    return huelle;
  }

  function zeichneListe(fokus) {
    const n = z.eintraege.length;
    liste.replaceChildren(...z.eintraege.map((e, i) => baueEintrag(e, i, n)));
    const nAktiv = aktive();
    const fotos = nAktiv.filter((e) => e.art === 'foto').length;
    const leer = nAktiv.length - fotos;
    const ungueltig = n - nAktiv.length;
    const teile = [];
    if (fotos) teile.push(zahl(fotos, 'Foto', 'Fotos'));
    if (leer) teile.push(zahl(leer, 'Leerseite', 'Leerseiten'));
    let text = '';
    if (teile.length) {
      const von = startSeite();
      const bis = von + nAktiv.length - 1;
      text = `${teile.join(' und ')} → ${nachtragen ? `Leerseite ${von}` : (nAktiv.length === 1 ? `Seite ${von}` : `Seiten ${von} bis ${bis}`)}`;
      if (!nachtragen && z.wahl !== 'neu' && z.stelle !== 'ende') text += '. Die folgenden Seiten rücken nach.';
    } else {
      text = nachtragen ? 'Noch kein Foto gewählt.' : 'Noch nichts gewählt.';
    }
    if (ungueltig) text += ` ${zahl(ungueltig, 'Eintrag wird', 'Einträge werden')} nicht hochgeladen.`;
    summe.textContent = text;
    if (fokus) {
      const ziel1 = wurzel.querySelector(fokus);
      if (ziel1 && !ziel1.disabled) ziel1.focus();
      else if (fokus.includes('[data-akt="hoch"]')) wurzel.querySelector(fokus.replace('hoch', 'runter'))?.focus();
      else if (fokus.includes('[data-akt="runter"]')) wurzel.querySelector(fokus.replace('runter', 'hoch'))?.focus();
    }
    pruefen();
  }

  function verschieben(e, richtung) {
    const i = z.eintraege.indexOf(e);
    const j = i + richtung;
    if (i < 0 || j < 0 || j >= z.eintraege.length) return;
    z.eintraege.splice(i, 1);
    z.eintraege.splice(j, 0, e);
    zeichneListe(`[data-eid="${e.id}"] [data-akt="${richtung < 0 ? 'hoch' : 'runter'}"]`);
    ansagen(`${e.art === 'leer' ? 'Leerseite' : e.name} steht jetzt an Position ${j + 1} von ${z.eintraege.length}.`);
  }

  function entfernen(e) {
    const i = z.eintraege.indexOf(e);
    if (i < 0) return;
    e.entfernt = true;
    z.eintraege.splice(i, 1);
    const naechster = z.eintraege[Math.min(i, z.eintraege.length - 1)];
    zeichneListe(naechster ? `[data-eid="${naechster.id}"] .hl-weg` : '.hl-wahl');
    ansagen(`${e.art === 'leer' ? 'Leerseite' : e.name} entfernt.`);
  }

  function leerseiteEinfuegen(position) {
    if (nachtragen) return;
    const e = { id: ++z.naechsteId, art: 'leer', name: 'Leerseite', zustand: null, anteil: 0 };
    z.eintraege.splice(position, 0, e);
    zeichneListe(`[data-zp="${position}"]`);
    ansagen(`Leerseite an Position ${position + 1} eingefügt.`);
  }
  q('.hl-zwischen[data-zp="0"]').addEventListener('click', () => leerseiteEinfuegen(0));

  // ---------- Dateien aufnehmen ----------
  function hinzufuegen(dateien) {
    let neu = [...dateien];
    if (!neu.length || z.phase !== 'auswahl') return;
    meldung('allgemein').textContent = '';
    if (nachtragen) {
      for (const alt of z.eintraege) alt.entfernt = true;
      z.eintraege = [];
      neu = neu.slice(0, 1);
    }
    for (const datei of neu) {
      const e = { id: ++z.naechsteId, art: 'foto', datei, name: datei.name, fehler: pruefeDatei(datei), zustand: null, anteil: 0 };
      z.eintraege.push(e);
      if (!e.fehler) vorschauAnstellen(e);
    }
    zeichneListe();
    ansagen(`${zahl(neu.length, 'Datei', 'Dateien')} hinzugefügt.`);
  }

  q('.hl-wahl').addEventListener('click', () => dateiFeld.click());
  dateiFeld.addEventListener('change', () => {
    hinzufuegen(dateiFeld.files);
    dateiFeld.value = '';
  });
  // Ziehen und Ablegen (PC): überall im Dialog erlaubt, damit der Browser kein Foto öffnet
  let ziehen = 0;
  const hatDateien = (ev) => [...(ev.dataTransfer?.types ?? [])].includes('Files');
  d.element.addEventListener('dragenter', (ev) => {
    if (!hatDateien(ev)) return;
    ev.preventDefault();
    ziehen += 1;
    if (z.phase === 'auswahl') ablage.classList.add('hl-ziehen');
  });
  d.element.addEventListener('dragover', (ev) => { if (hatDateien(ev)) ev.preventDefault(); });
  d.element.addEventListener('dragleave', () => {
    ziehen = Math.max(0, ziehen - 1);
    if (!ziehen) ablage.classList.remove('hl-ziehen');
  });
  d.element.addEventListener('drop', (ev) => {
    if (!hatDateien(ev)) return;
    ev.preventDefault();
    ziehen = 0;
    ablage.classList.remove('hl-ziehen');
    hinzufuegen(ev.dataTransfer.files);
  });

  // ---------- Fuß und Hauptknopf ----------
  let hauptknopf = null;
  function fuss(...knoepfe) { d.fuss.replaceChildren(...knoepfe); }

  function pruefen() {
    if (!hauptknopf || z.phase !== 'auswahl') return;
    const nAktiv = aktive();
    const fotos = nAktiv.filter((e) => e.art === 'foto').length;
    let ok = nAktiv.length > 0 && !z.listeFehler;
    if (nachtragen) ok = fotos === 1 && !z.listeFehler ? ok : false;
    else if (z.wahl === '') ok = false;
    else if (z.wahl === 'neu') ok = ok && z.name.trim().length >= 1 && z.name.trim().length <= 60;
    hauptknopf.disabled = !ok;
  }

  function auswahlFuss() {
    hauptknopf = knopfMachen('Hochladen', 'knopf-haupt', starten);
    hauptknopf.disabled = true;
    fuss(knopfMachen('Abbrechen', '', () => d.schliessen()), hauptknopf);
    pruefen();
  }

  // ---------- Übertragen ----------
  function phase(neu) {
    z.phase = neu;
    wurzel.dataset.phase = neu;
    lauf.hidden = neu === 'auswahl';
  }

  function gesamtAktualisieren() {
    const teile = z.eintraege.filter((e) => e.art === 'foto' && gueltig(e) && e.zustand !== 'uebersprungen');
    const summeBytes = teile.reduce((s, e) => s + e.datei.size, 0);
    let erledigt = 0;
    let anteil;
    if (summeBytes > 0) {
      erledigt = teile.reduce((s, e) => s + (e.zustand === 'fertig' ? e.datei.size : e.datei.size * (e.anteil || 0)), 0);
      anteil = erledigt / summeBytes;
    } else {
      const alle = z.eintraege.filter((e) => gueltig(e) && e.zustand !== 'uebersprungen');
      anteil = alle.length ? alle.filter((e) => e.zustand === 'fertig').length / alle.length : 0;
    }
    gesamtFuell.style.transform = `scaleX(${Math.min(1, anteil)})`;
    gesamt.setAttribute('aria-valuenow', String(Math.round(anteil * 100)));
    return anteil;
  }

  function laufTextSetzen(e) {
    const ziele = z.eintraege.filter((x) => gueltig(x) && x.zustand !== 'uebersprungen');
    const nr = ziele.indexOf(e) + 1;
    laufText.textContent = `Übertragung ${nr} von ${ziele.length} · Seite ${z.k + 1}`;
  }

  function fehlerAnzeigen(f) {
    return f instanceof SchnittstellenFehler ? f.message : fehlerText(0);
  }

  function fragen(e, f) {
    const doppelt = f.status === 409 && f.daten?.doppelt;
    frageBox.hidden = false;
    frageBox.querySelector('.hl-frage-name').textContent = e.art === 'leer' ? 'Leerseite' : e.name;
    frageBox.querySelector('.hl-frage-text').textContent = fehlerAnzeigen(f);
    e.zustand = 'fehler';
    e.fehlerText = '';
    e.anteil = 0;
    eintragAktualisieren(e);
    gesamtAktualisieren();
    return new Promise((loesen) => {
      const wahl = (wert) => {
        z.frage = null;
        frageBox.hidden = true;
        laufTextSetzen(e);
        fuss(knopfMachen('Abbrechen', '', abbrechen));
        loesen(wert);
      };
      z.frage = { loesen: wahl };
      const abbr = knopfMachen('Abbrechen', '', () => wahl('abbrechen'));
      const ueber = knopfMachen('Überspringen', doppelt ? 'knopf-haupt' : '', () => wahl('ueberspringen'));
      if (doppelt) {
        fuss(abbr, knopfMachen('Trotzdem hochladen', '', () => wahl('trotzdem')), ueber);
        ueber.focus();
      } else {
        const nochmal = knopfMachen('Erneut versuchen', 'knopf-haupt', () => wahl('nochmal'));
        fuss(abbr, ueber, nochmal);
        nochmal.focus();
      }
    });
  }

  function uebertragen(e, trotzdem) {
    if (e.art === 'leer') {
      return senden(`/api/binder/${z.binderId}/leerseiten`, { daten: { nach: z.k, anzahl: 1 } })
        .then((r) => r.seiten[0]);
    }
    const name = `name=${encodeURIComponent(e.datei.name)}${trotzdem ? '&trotzdem=1' : ''}`;
    const url = nachtragen
      ? `/api/seite/${ziel.seiteId}/foto?${name}`
      : `/api/binder/${z.binderId}/fotos?nach=${z.k}&${name}`;
    return new Promise((ok, nein) => {
      const xhr = new XMLHttpRequest();
      z.xhr = xhr;
      xhr.open('POST', adresse(url));
      xhr.setRequestHeader('Content-Type', e.datei.type || 'application/octet-stream');
      xhr.setRequestHeader('Accept', 'application/json');
      let letzte = -1;
      xhr.upload.onprogress = (ev) => {
        e.anteil = ev.lengthComputable && ev.total ? ev.loaded / ev.total : 0;
        const prozent = Math.round(e.anteil * 100);
        if (prozent !== letzte) {
          letzte = prozent;
          if (e.zustand === 'laeuft') eintragAktualisieren(e);
          gesamtAktualisieren();
        }
      };
      xhr.upload.onload = () => {
        e.anteil = 1;
        e.zustand = 'speichern';
        eintragAktualisieren(e);
        gesamtAktualisieren();
      };
      xhr.onload = () => {
        z.xhr = null;
        let inhalt = null;
        try { inhalt = JSON.parse(xhr.responseText); } catch { /* keine JSON-Antwort */ }
        if (xhr.status >= 200 && xhr.status < 300 && inhalt?.seite) ok(inhalt.seite);
        else nein(new SchnittstellenFehler(inhalt?.fehler || fehlerText(xhr.status), xhr.status, inhalt));
      };
      xhr.onerror = () => { z.xhr = null; nein(new SchnittstellenFehler(fehlerText(0), 0)); };
      xhr.ontimeout = xhr.onerror;
      xhr.onabort = () => { z.xhr = null; nein(ABBRUCH); };
      xhr.send(e.datei);
    });
  }

  function abbrechen() {
    z.abbruch = true;
    laufText.textContent = 'Wird abgebrochen …';
    z.xhr?.abort();
    z.frage?.loesen('abbrechen');
  }

  async function starten() {
    meldung('allgemein').textContent = '';
    meldung('name').textContent = '';
    hauptknopf.disabled = true;
    if (!angemeldet() && !(await anmelden())) {
      meldung('allgemein').textContent = 'Zum Hochladen musst du angemeldet sein.';
      pruefen();
      return;
    }
    if (z.geschlossen) return;

    // Binder anlegen (erst jetzt)
    if (z.wahl === 'neu') {
      const daten = { name: z.name.trim(), raster: z.raster, farbe: z.farbe };
      for (let versuch = 0; ; versuch += 1) {
        try {
          const r = await senden('/api/binder', { daten });
          z.binderListe.push({ id: r.id, name: daten.name, raster: daten.raster, farbe: daten.farbe, seiten: 0 });
          z.wahl = String(r.id);
          z.binderId = r.id;
          binderOptionen();
          wahlGeaendert();
          geaendert(r.id);
          break;
        } catch (f) {
          if (f.status === 401 && versuch === 0 && await anmelden()) continue;
          meldung('name').textContent = fehlerAnzeigen(f);
          nameFeld.focus();
          pruefen();
          return;
        }
      }
    }

    // Stelle bestimmen
    if (nachtragen) {
      z.k = 0;
    } else if (z.stelle === 'ende') {
      try {
        const b = await holen(`/api/binder/${z.binderId}`);
        z.k = b.seiten.length;
      } catch {
        meldung('allgemein').textContent = 'Der Binder konnte nicht gelesen werden. Bitte noch einmal versuchen.';
        pruefen();
        return;
      }
    } else {
      z.k = Number(z.stelle);
    }

    // Übertragung
    const binder = binderAktuell();
    q('.hl-ziel').textContent = nachtragen
      ? `Foto für Leerseite ${ziel.seiteNummer}`
      : `Binder „${binder?.name ?? ''}“ · ab Seite ${z.k + 1}`;
    for (const e of z.eintraege) {
      e.zustand = gueltig(e) ? 'wartet' : null;
      e.anteil = 0;
    }
    phase('senden');
    fuss(knopfMachen('Abbrechen', '', abbrechen));
    zeichneListe();
    gesamtAktualisieren();

    for (const e of z.eintraege.filter(gueltig)) {
      if (z.abbruch || z.geschlossen) break;
      let trotzdem = false;
      let anmeldeVersuch = 0;
      e.zustand = 'laeuft';
      laufTextSetzen(e);
      eintragAktualisieren(e);
      for (;;) {
        try {
          e.zustand = 'laeuft';
          e.fehlerText = '';
          const seite = await uebertragen(e, trotzdem);
          e.zustand = 'fertig';
          e.anteil = 1;
          e.nummer = seite.nummer;
          z.hochgeladen.push({ id: seite.id, nummer: seite.nummer, art: e.art });
          if (!nachtragen) z.k += 1;
          eintragAktualisieren(e);
          gesamtAktualisieren();
          if (z.binderId != null) geaendert(z.binderId);
          break;
        } catch (f) {
          if (f === ABBRUCH || z.abbruch || z.geschlossen) { z.abbruch = true; e.zustand = 'abgebrochen'; e.anteil = 0; break; }
          e.anteil = 0;
          if (f.status === 401 && anmeldeVersuch < 2) {
            anmeldeVersuch += 1;
            window.dispatchEvent(new CustomEvent('binder:abgelaufen'));
            if (await anmelden()) continue;
          }
          const wahl = await fragen(e, f);
          if (wahl === 'nochmal') continue;
          if (wahl === 'trotzdem') { trotzdem = true; continue; }
          if (wahl === 'ueberspringen') { e.zustand = 'uebersprungen'; e.fehlerText = fehlerAnzeigen(f); z.uebersprungen += 1; break; }
          z.abbruch = true;
          e.zustand = 'abgebrochen';
          break;
        }
      }
      eintragAktualisieren(e);
      gesamtAktualisieren();
      for (const x of z.eintraege) if (x.zustand === 'wartet') eintragAktualisieren(x);
    }
    if (z.geschlossen) return;
    for (const e of z.eintraege) {
      if (e.zustand === 'wartet' || e.zustand === 'laeuft') e.zustand = 'abgebrochen';
      eintragAktualisieren(e);
    }
    await abschliessen();
  }

  // ---------- Abschluss ----------
  async function abschliessen() {
    phase('fertig');
    laufText.textContent = '';
    frageBox.hidden = true;
    const fotos = z.hochgeladen.filter((s) => s.art === 'foto').length;
    const leer = z.hochgeladen.length - fotos;
    const teile = [];
    if (fotos) teile.push(zahl(fotos, 'Foto', 'Fotos'));
    if (leer) teile.push(zahl(leer, 'Leerseite', 'Leerseiten'));
    let text;
    if (!z.hochgeladen.length) {
      text = z.abbruch ? 'Abgebrochen. Es wurde nichts hochgeladen.' : 'Es wurde nichts hochgeladen.';
    } else {
      const nummern = z.hochgeladen.map((s) => s.nummer);
      const von = Math.min(...nummern);
      const bis = Math.max(...nummern);
      const wo = nachtragen ? `Leerseite ${von}` : (von === bis ? `Seite ${von}` : `Seiten ${von}–${bis}`);
      text = `${z.abbruch ? 'Abgebrochen. ' : ''}${teile.join(' und ')} hochgeladen, ${wo}.`;
      if (z.abbruch) text += ' Der Rest wurde nicht hochgeladen.';
    }
    if (z.uebersprungen) text += ` ${zahl(z.uebersprungen, 'Eintrag', 'Einträge')} übersprungen.`;
    q('.hl-abschluss-text').textContent = text;
    abschlussBox.hidden = false;
    gesamtAktualisieren();
    if (z.hochgeladen.length) {
      fuss(knopfMachen('Zum Binder', 'knopf-haupt', () => d.schliessen()));
    } else {
      fuss(knopfMachen('Schließen', 'knopf-haupt', () => d.schliessen()));
    }
    d.fuss.querySelector('.knopf')?.focus();
    if (fotos) {
      try {
        const v = await holen('/api/verarbeiter');
        if (z.geschlossen) return;
        q('.hl-abschluss-erkennung').textContent = v.aktiv
          ? 'Die Erkennung läuft; die Karten erscheinen nach und nach.'
          : 'Erkennung nicht aktiv – am PC Starten.bat doppelklicken';
      } catch { /* Hinweis entfällt */ }
    }
  }

  // ---------- Schließen während der Übertragung ----------
  async function schliessenFragen() {
    if (z.phase !== 'senden') return true;
    if (z.fragtSchliessen) return false;
    z.fragtSchliessen = true;
    const ja = await bestaetigen({
      titel: 'Übertragung abbrechen?',
      text: 'Schon übertragene Seiten bleiben erhalten, der Rest wird nicht hochgeladen.',
      ja: 'Abbrechen', nein: 'Weiter übertragen', gefahr: true,
    });
    z.fragtSchliessen = false;
    return ja;
  }
  d.element.addEventListener('click', async (ev) => {
    if (z.phase !== 'senden' || !ev.target.closest?.('.dialog-zu')) return;
    ev.stopPropagation();
    if (await schliessenFragen()) d.schliessen();
  }, true);
  d.element.addEventListener('cancel', async (ev) => {
    if (z.phase !== 'senden') return;
    ev.preventDefault();
    if (await schliessenFragen()) d.schliessen();
  });
  d.element.addEventListener('close', () => {
    z.geschlossen = true;
    z.abbruch = true;
    z.xhr?.abort();
    z.frage?.loesen('abbrechen');
    for (const e of z.eintraege) e.entfernt = true;
  });

  // ---------- Start ----------
  auswahlFuss();
  zeichneListe();
  if (!nachtragen) {
    try {
      const r = await holen('/api/regal');
      z.binderListe = r.binder ?? [];
    } catch {
      z.listeFehler = true;
      meldung('allgemein').textContent = 'Die Binder konnten nicht geladen werden. Bitte den Dialog schließen und noch einmal öffnen.';
    }
    if (ziel.binderId != null && z.binderListe.some((b) => b.id === ziel.binderId)) z.wahl = String(ziel.binderId);
    else if (ziel.neu || !z.binderListe.length) z.wahl = 'neu';
    if (z.wahl !== 'neu' && ziel.nach != null && z.wahl !== '') {
      const n = binderAktuell()?.seiten ?? 0;
      z.stelle = ziel.nach >= n ? 'ende' : String(Math.max(0, ziel.nach));
    }
    binderOptionen();
    wahlGeaendert();
    if (z.wahl === 'neu') nameFeld.focus();
  }
  pruefen();

  await d.fertig;
  if (!z.hochgeladen.length) return null;
  return { binderId: z.binderId, seiten: z.hochgeladen.map(({ id, nummer }) => ({ id, nummer })) };
}

