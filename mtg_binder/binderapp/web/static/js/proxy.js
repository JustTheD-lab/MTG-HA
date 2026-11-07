// Proxys: Deckliste einfügen, prüfen und als druckfertiges PDF (Silhouette Card Maker) erzeugen. Nur im Add-on.
//
//   #/proxy      Eingabe → Vorschau mit Status je Zeile → Rückseite → PDF erzeugen (Fortschritt) → Download
//
// Daten: /api/proxy (Stand, laufender Auftrag, Rückseiten, Formate), /api/proxy/pruefen, /api/proxy/auftrag,
// /api/proxy/pdfs, /api/proxy/rueckseiten. Aufrufe nur über holen und senden; Adressen relativ.

import { holen, senden, esc, zurueck, adresse, vorliebe } from './gemeinsam.js';
import { bestaetigen, dialogOffen } from './dialog.js';

const sym = (d, g = 18) => `<svg viewBox="0 0 24 24" width="${g}" height="${g}" fill="none" stroke="currentColor"`
  + ` stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${d}</svg>`;
const ZURUECK = sym('<path d="M15 5l-7 7 7 7"/>', 20);
const HOCH = sym('<path d="M12 16V4M7 9l5-5 5 5M5 20h14"/>', 18);
const LADEN = sym('<path d="M12 4v12M7 11l5 5 5-5M5 20h14"/>', 18);

const DRUCKHINWEIS = 'Drucken: Tatsächliche Größe / 100 %, beidseitig an der langen Kante. '
  + 'Schneiden in Silhouette Studio: Vorlage a4-standard-v5, Maschine „Cameo 5“ (3-Punkt-Passermarken).';
const LIZENZ = 'MIT-Lizenz, © Alan Cha; Proxys nur für Casual und Playtesting.';
const LAUFEND = ['wartet', 'laedt', 'baut'];
const ZUSTAENDE = {
  wartet: 'Wartet auf den PC …', laedt: 'Kartenbilder werden geladen …', fehlende: 'Einige Karten fehlen',
  baut: 'PDF wird gebaut …', fertig: 'Fertig', abgebrochen: 'Abgebrochen', fehler: 'Fehler',
};
const STATUS = {
  ok: 'Gefunden',
  druck_fehlt: 'Set/Nummer nicht im Katalog',
  unbekannt: 'Name nicht im Katalog – Scryfall versucht es trotzdem',
  ungeprueft: 'Nicht geprüft',
};
const TAKT = 1500;
// Gruppen einer Deckzeile (Entscheidung des Nutzers, 07.11.2026: vorgewählt fehlend und als Proxy markiert)
const DECK_GRUPPEN = { fehlt: 'Fehlende Karten', proxy: 'Als Proxy markiert', vorhanden: 'Vorhandene Karten',
  basics: 'Basic Lands' };
const DECK_STANDARD = ['fehlt', 'proxy'];

let Z = null;   // Zustand der gezeigten Ansicht

function el(tag, klasse, html) {
  const e = document.createElement(tag);
  if (klasse) e.className = klasse;
  if (html !== undefined) e.innerHTML = html;
  return e;
}

const zahl = (n) => Number(n ?? 0).toLocaleString('de-DE');
const mb = (b) => `${(Number(b ?? 0) / 1048576).toLocaleString('de-DE', { maximumFractionDigits: 1 })} MB`;
const sek = (s) => (s == null ? '–' : `${Number(s).toLocaleString('de-DE', { maximumFractionDigits: 1 })} s`);
const wert = (v) => (v && typeof v === 'object' ? String(v.tag ?? v.name ?? v.titel ?? '') : String(v ?? ''));

function zeit(text) {
  if (!text) return '–';
  const d = new Date(text);
  return Number.isNaN(d.getTime()) ? String(text)
    : d.toLocaleString('de-DE', { day: 'numeric', month: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function fehlerSatz(f) {
  return f?.message || 'Das hat nicht geklappt. Bitte noch einmal versuchen.';
}

function meldung(bereich, text) {
  bereich.textContent = text || '';
}

const druckhinweisHtml = () => `<p class="proxy-druck" role="note">${esc(DRUCKHINWEIS)}</p>`;

// ---------------------------------------------------------------- Eingabe

function eingabeHtml(formate) {
  const optionen = ['<option value="">Automatisch erkennen</option>']
    .concat(formate.map((f) => `<option value="${esc(f.name)}">${esc(f.titel)}</option>`)).join('');
  return '<h2 class="proxy-titel">Deckliste</h2>'
    + '<div class="proxy-deck" data-deckwahl hidden>'
    + '<label class="etikett feld-etikett" for="proxy-deck">Aus Deck übernehmen</label>'
    + '<div class="proxy-zeile"><select id="proxy-deck" class="feld proxy-deck-wahl"></select>'
    + '<button type="button" class="knopf" data-deck-laden>Übernehmen</button></div>'
    + '<div class="proxy-deck-gruppen" role="group" aria-label="Welche Karten aus dem Deck">'
    + Object.entries(DECK_GRUPPEN).map(([g, t]) => `<label class="proxy-haken"><input type="checkbox" data-gruppe="${g}"`
      + `${DECK_STANDARD.includes(g) ? ' checked' : ''}><span>${esc(t)}<span class="proxy-hinweis" data-zahl="${g}"></span>`
      + '</span></label>').join('')
    + '</div><p class="proxy-hinweis" role="status" data-deck-status></p></div>'
    + '<label class="etikett feld-etikett" for="proxy-text">Deckliste einfügen</label>'
    + '<textarea id="proxy-text" class="feld proxy-text" rows="10" spellcheck="false" autocomplete="off"'
    + ' autocapitalize="off" placeholder="1 Sol Ring&#10;1 Command Tower (CMM) 350&#10;…"></textarea>'
    + '<div class="proxy-zeile"><label class="knopf proxy-datei" for="proxy-datei">Textdatei wählen</label>'
    + '<input id="proxy-datei" class="nur-vorleser" type="file" accept=".txt,text/plain">'
    + '<span class="proxy-hinweis" data-dateiname></span></div>'
    + '<div class="proxy-felder"><div><label class="etikett feld-etikett" for="proxy-name">Name</label>'
    + '<input id="proxy-name" class="feld" type="text" maxlength="80" placeholder="Deck" autocomplete="off"></div>'
    + '<div><label class="etikett feld-etikett" for="proxy-format">Format</label>'
    + `<select id="proxy-format" class="feld">${optionen}</select></div></div>`
    + '<label class="proxy-haken"><input type="checkbox" id="proxy-sets"><span>Sets ignorieren</span></label>'
    + '<label class="proxy-haken"><input type="checkbox" id="proxy-extra"><span>Extra-Art bevorzugen</span></label>'
    + '<div class="proxy-zeile"><button type="button" class="knopf knopf-haupt proxy-pruefen" data-pruefen>Prüfen</button>'
    + '<span class="proxy-hinweis" role="status" data-pruef-status></span></div>'
    + '<p class="meldung" role="alert" data-pruef-fehler></p>';
}

async function pruefen() {
  const z = Z;
  const text = z.feld.text.value;
  if (!text.trim()) { meldung(z.feld.fehler, 'Bitte zuerst eine Deckliste einfügen oder eine Textdatei wählen.'); return; }
  meldung(z.feld.fehler, '');
  z.feld.knopf.disabled = true;
  z.feld.status.textContent = 'Wird geprüft … das dauert ein paar Sekunden.';
  try {
    const antwort = await senden('/api/proxy/pruefen', { daten: {
      text, format: z.formatWahl || null, sets_ignorieren: z.feld.sets.checked } });
    if (Z !== z) return;
    z.vorschau = antwort;
    z.geprueftText = text;
    z.geprueftSets = z.feld.sets.checked;
    if (z.feld.name.value.trim() === '' && !z.nameGesetzt) z.feld.name.placeholder = 'Deck';
    z.feld.status.textContent = '';
  } catch (f) {
    if (Z !== z) return;
    z.vorschau = null;
    meldung(z.feld.fehler, fehlerSatz(f));
    z.feld.status.textContent = '';
  } finally {
    if (Z === z) z.feld.knopf.disabled = false;
  }
  if (Z === z) { vorschauZeichnen(); erzeugenZeichnen(); }
}

// ---------------------------------------------------------------- Vorschau

function veraltet(z) {
  return Boolean(z.vorschau) && (z.feld.text.value !== z.geprueftText || z.feld.sets.checked !== z.geprueftSets);
}

function vorschauZeichnen() {
  const z = Z;
  const v = z.vorschau;
  const wurzel = z.teil.vorschau;
  if (!v) { wurzel.hidden = true; wurzel.innerHTML = ''; return; }
  wurzel.hidden = false;
  const zeilen = v.zeilen ?? [];
  const schlecht = zeilen.filter((r) => r.status !== 'ok');
  const gut = zeilen.filter((r) => r.status === 'ok');
  const formate = v.formate ?? [];
  const formatOptionen = formate.map((f) => `<option value="${esc(f.name)}"${f.name === v.format ? ' selected' : ''}>`
    + `${esc(f.titel)} (${zahl(f.karten)} ${f.karten === 1 ? 'Karte' : 'Karten'})</option>`).join('');
  const reihe = (r) => {
    const ok = r.status === 'ok';
    const ort = [r.set ? String(r.set).toUpperCase() : '', r.nummer ?? ''].filter(Boolean).join(' · ');
    return `<li class="proxy-zeile-karte${ok ? '' : ' proxy-auffaellig'}">`
      + `<span class="proxy-anzahl">${zahl(r.anzahl)}×</span>`
      + `<span class="proxy-kartenname">${esc(r.name)}${ort ? `<span class="proxy-ort"> ${esc(ort)}</span>` : ''}</span>`
      + `<span class="proxy-status${ok ? '' : ' proxy-status-warn'}">${ok ? '' : '<span class="proxy-punkt" aria-hidden="true">!</span>'}`
      + `${esc(STATUS[r.status] ?? r.status)}</span></li>`;
  };
  wurzel.innerHTML = '<h2 class="proxy-titel">Vorschau</h2>'
    + `<div class="proxy-felder"><div><label class="etikett feld-etikett" for="proxy-format2">Erkanntes Format</label>`
    + `<select id="proxy-format2" class="feld">${formatOptionen || `<option>${esc(v.format ?? '–')}</option>`}</select></div>`
    + `<p class="proxy-zahlen"><strong>${zahl(zeilen.length)}</strong> ${zeilen.length === 1 ? 'Zeile' : 'Zeilen'}`
    + ` · <strong>${zahl(v.exemplare)}</strong> ${v.exemplare === 1 ? 'Exemplar' : 'Exemplare'}</p></div>`
    + (veraltet(z) ? '<p class="proxy-warnung" role="status">Die Liste wurde seit der Prüfung geändert. Bitte neu prüfen.</p>' : '')
    + (v.zu_viele ? `<p class="proxy-warnung" role="alert">Diese Liste hat ${esc(v.exemplare)} Karten, ein PDF höchstens `
      + `${esc(v.hoechstens)}. Bitte die Liste aufteilen.</p>` : '')
    + (v.katalog === false ? '<p class="proxy-hinweis-tafel">Der Katalog ist nicht eingerichtet. Die Zeilen sind nicht geprüft; '
      + 'Scryfall versucht beim Erzeugen jede Karte.</p>' : '')
    + (schlecht.length ? `<h3 class="proxy-unter">Zu prüfen (${zahl(schlecht.length)})</h3>`
      + `<ul class="proxy-liste">${schlecht.map(reihe).join('')}</ul>` : '')
    + (gut.length ? `<details class="proxy-gut"${schlecht.length ? '' : ' open'}><summary>Gefunden (${zahl(gut.length)})</summary>`
      + `<ul class="proxy-liste">${gut.map(reihe).join('')}</ul></details>` : '')
    + ((v.uebersprungen ?? []).length
      ? `<h3 class="proxy-unter">Nicht gelesen (${zahl(v.uebersprungen.length)})</h3>`
        + '<p class="proxy-hinweis">Diese Zeilen konnten nicht als Karte gelesen werden und fehlen im PDF.</p>'
        + `<ul class="proxy-liste proxy-uebersprungen">${v.uebersprungen.map((t) => `<li>${esc(t)}</li>`).join('')}</ul>`
      : '');
}

// ---------------------------------------------------------------- Rückseiten

function rueckseiteGewaehlt(z) {
  const liste = z.daten.rueckseiten ?? [];
  const gemerkt = vorliebe('proxy.rueckseite');
  if (liste.some((r) => r.id === gemerkt)) return gemerkt;
  return (liste.find((r) => r.id === 'beispiel') ?? liste[0])?.id ?? null;
}

function rueckseitenZeichnen() {
  const z = Z;
  const liste = z.daten.rueckseiten ?? [];
  const gewaehlt = z.rueckseite;
  const kachel = (r) => {
    const an = r.id === gewaehlt;
    const mass = r.breite && r.hoehe ? `${zahl(r.breite)} × ${zahl(r.hoehe)} px` : '';
    return `<div class="proxy-kachel-huelle"><button type="button" class="proxy-kachel" role="radio" aria-checked="${an}"`
      + ` data-rueck="${esc(r.id)}" aria-label="Rückseite ${esc(r.name)}${an ? ', gewählt' : ''}">`
      + `<img src="${esc(adresse(`api/proxy/rueckseite/${encodeURIComponent(r.id)}/bild`))}" alt="" loading="lazy" decoding="async">`
      + `<span class="proxy-kachel-name">${esc(r.name)}</span>`
      + `<span class="proxy-kachel-mass">${esc(r.beispiel ? 'Beispiel' : mass)}</span></button>`
      + (r.id === 'beispiel' ? '' : `<button type="button" class="knopf proxy-weg" data-weg="${esc(r.id)}"`
        + ` aria-label="Rückseite ${esc(r.name)} entfernen">Entfernen</button>`)
      + (r.seitenverhaeltnis_ok === false
        ? '<p class="proxy-warnung proxy-klein" role="note">Seitenverhältnis weicht von 63 : 88 ab. Das Bild wird verzerrt oder beschnitten.</p>' : '')
      + '</div>';
  };
  z.teil.rueck.innerHTML = '<h2 class="proxy-titel">Rückseite</h2>'
    + '<p class="proxy-hinweis">Wird auf die Rückseite jedes Blattes gedruckt.</p>'
    + `<div class="proxy-kacheln" role="radiogroup" aria-label="Rückseite wählen">${liste.map(kachel).join('')}</div>`
    + `<div class="proxy-zeile"><label class="knopf proxy-datei" for="proxy-rueck-datei">${HOCH}<span>Rückseite hochladen</span></label>`
    + '<input id="proxy-rueck-datei" class="nur-vorleser" type="file" accept="image/png,image/jpeg">'
    + '<span class="proxy-hinweis">PNG oder JPEG, höchstens 40 MB; große Bilder werden vorher verkleinert</span></div>'
    + '<p class="meldung" role="alert" data-rueck-fehler></p>';
}

async function rueckseitenNeuLaden() {
  const z = Z;
  try {
    const antwort = await holen('/api/proxy/rueckseiten');
    if (Z !== z) return;
    z.daten.rueckseiten = Array.isArray(antwort) ? antwort : (antwort.rueckseiten ?? z.daten.rueckseiten);
  } catch { /* alte Liste bleibt */ }
  if (Z === z) { z.rueckseite = rueckseiteGewaehlt(z); rueckseitenZeichnen(); }
}

// Home Assistant reicht über Ingress nur Anfragen bis 16 MB weiter (Antwort 413 ohne Text der App). Größere Bilder
// rechnet der Browser darum vorher herunter: Silhouette Card Maker druckt mit 300 ppi, also 744 Pixel Kartenbreite;
// 1488 Pixel (doppelt) lassen Reserve. Kleine Bilder gehen unverändert hoch.
const RUECK_UNVERAENDERT = 12 * 1048576;
const RUECK_BREITE = 1488;

function alsBlob(leinwand, typ, guete) {
  return new Promise((ok) => leinwand.toBlob(ok, typ, guete));
}

async function rueckseiteVerkleinern(datei) {
  if (datei.size <= RUECK_UNVERAENDERT) return { datei, typ: datei.type };
  const bild = await createImageBitmap(datei);
  try {
    const faktor = Math.min(1, RUECK_BREITE / bild.width);
    const leinwand = document.createElement('canvas');
    leinwand.width = Math.round(bild.width * faktor);
    leinwand.height = Math.round(bild.height * faktor);
    const ctx = leinwand.getContext('2d');
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(bild, 0, 0, leinwand.width, leinwand.height);
    let blob = datei.type === 'image/png' ? await alsBlob(leinwand, 'image/png') : null;
    if (blob && blob.size <= RUECK_UNVERAENDERT) return { datei: blob, typ: 'image/png' };
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

async function rueckseiteHochladen(datei) {
  const z = Z;
  const fehler = () => z.teil.rueck.querySelector('[data-rueck-fehler]');
  if (!datei) return;
  if (!/^image\/(png|jpeg)$/.test(datei.type)) { meldung(fehler(), 'Bitte ein PNG oder JPEG wählen.'); return; }
  if (datei.size > 40 * 1048576) { meldung(fehler(), 'Das Bild ist zu groß, höchstens 40 MB.'); return; }
  let hochzuladen = { datei, typ: datei.type };
  if (datei.size > RUECK_UNVERAENDERT) {
    meldung(fehler(), 'Bild wird verkleinert …');
    try {
      hochzuladen = await rueckseiteVerkleinern(datei);
    } catch {
      if (Z === z) meldung(fehler(), 'Das Bild ließ sich nicht verkleinern. Bitte ein kleineres Bild wählen (höchstens 12 MB).');
      return;
    }
    if (Z !== z) return;
  }
  meldung(fehler(), 'Wird hochgeladen …');
  try {
    const eintrag = await senden(`/api/proxy/rueckseiten?name=${encodeURIComponent(datei.name)}`, hochzuladen);
    if (Z !== z) return;
    await rueckseitenNeuLaden();
    if (Z !== z) return;
    if (eintrag?.id && (z.daten.rueckseiten ?? []).some((r) => r.id === eintrag.id)) {
      z.rueckseite = eintrag.id;
      vorliebe('proxy.rueckseite', eintrag.id);
      rueckseitenZeichnen();
    }
  } catch (f) {
    if (Z === z) meldung(fehler(), fehlerSatz(f));
  }
}

async function rueckseiteEntfernen(id) {
  const z = Z;
  const r = (z.daten.rueckseiten ?? []).find((x) => x.id === id);
  if (!r) return;
  const ja = await bestaetigen({ titel: 'Rückseite entfernen?', text: `„${r.name}“ wird gelöscht.`, ja: 'Entfernen', gefahr: true });
  if (!ja || Z !== z) return;
  try {
    await senden(`/api/proxy/rueckseite/${encodeURIComponent(id)}`, { methode: 'DELETE' });
    if (Z !== z) return;
    if (z.rueckseite === id) vorliebe('proxy.rueckseite', '');
    await rueckseitenNeuLaden();
  } catch (f) {
    if (Z === z) meldung(z.teil.rueck.querySelector('[data-rueck-fehler]'), fehlerSatz(f));
  }
}

// ---------------------------------------------------------------- Erzeugen, Auftrag

function laeuft(z) {
  return Boolean(z.auftrag) && (LAUFEND.includes(z.auftrag.zustand) || z.auftrag.zustand === 'fehlende');
}

function auftragHtml(a) {
  const text = ZUSTAENDE[a.zustand] ?? a.zustand;
  const kopf = `<h3 class="proxy-unter">${esc(a.name || 'Deck')}</h3>`;
  if (LAUFEND.includes(a.zustand)) {
    const anteil = a.gesamt > 0 ? Math.min(1, a.fertig / a.gesamt) : 0;
    return `${kopf}<div class="proxy-fortschritt" role="progressbar" aria-valuemin="0" aria-valuemax="${a.gesamt || 0}"`
      + ` aria-valuenow="${a.fertig || 0}" aria-label="Fortschritt"><span style="transform:scaleX(${anteil})"></span></div>`
      + `<p class="proxy-hinweis" role="status">${esc(text)} ${a.gesamt ? `${zahl(a.fertig)} von ${zahl(a.gesamt)}` : ''}`
      + `${a.meldung ? ` · ${esc(a.meldung)}` : ''}</p>`;
  }
  if (a.zustand === 'fehlende') {
    const f = a.fehlende ?? [];
    return `${kopf}<p class="proxy-warnung" role="alert">${f.length === 1 ? 'Diese Karte konnte' : 'Diese Karten konnten'} `
      + 'nicht geladen werden:</p>'
      + `<ul class="proxy-liste">${f.map((x) => `<li class="proxy-zeile-karte proxy-auffaellig"><span class="proxy-kartenname">`
        + `${esc(x.name)}</span><span class="proxy-status proxy-status-warn">${esc(x.grund || x.fehler || '')}</span></li>`).join('')}</ul>`
      + '<div class="proxy-zeile"><button type="button" class="knopf knopf-haupt" data-weiter="ja">'
      + 'PDF ohne diese Karten erzeugen</button><button type="button" class="knopf" data-weiter="nein">Abbrechen</button></div>';
  }
  if (a.zustand === 'fertig' && a.pdf) {
    const z = a.zeiten ?? {};
    return `${kopf}<a class="knopf knopf-haupt proxy-gross" href="${esc(adresse(`api/proxy/pdf/${encodeURIComponent(a.pdf)}`))}"`
      + ` download="${esc(a.pdf)}">${LADEN}<span>PDF herunterladen<small>${esc(a.pdf)}</small></span></a>`
      + `<p class="proxy-hinweis">Laden ${esc(sek(z.laden_s))} · PDF ${esc(sek(z.pdf_s))} · gesamt ${esc(sek(z.gesamt_s))}`
      + `${a.cache ? ` · ${zahl(a.cache.treffer)} aus dem Zwischenspeicher, ${zahl(a.cache.neu)} neu geladen` : ''}</p>`
      + ((a.fehlende ?? []).length
        ? `<p class="proxy-warnung" role="note">Ohne: ${esc(a.fehlende.map((x) => x.name).join(', '))}</p>` : '')
      + druckhinweisHtml();
  }
  const grund = a.zustand === 'fehler' ? (a.meldung || 'Beim Erzeugen ist ein Fehler aufgetreten.') : (a.meldung || '');
  return `${kopf}<p class="${a.zustand === 'fehler' ? 'proxy-warnung' : 'proxy-hinweis'}" role="status">${esc(text)}`
    + `${grund ? `: ${esc(grund)}` : ''}</p>`;
}

function erzeugenZeichnen() {
  const z = Z;
  const v = z.vorschau;
  const sperre = !z.daten.ablage ? 'Netzlaufwerk nicht erreichbar'
    : laeuft(z) ? 'Es läuft schon ein Auftrag'
      : !v ? 'Zuerst die Liste prüfen'
        : v.zu_viele ? 'Die Liste hat zu viele Karten'
          : veraltet(z) ? 'Die Liste wurde geändert, bitte neu prüfen' : '';
  z.teil.erzeugen.innerHTML = '<h2 class="proxy-titel">PDF erzeugen</h2>'
    + `<div class="proxy-zeile"><button type="button" class="knopf knopf-haupt" data-erzeugen${sperre ? ' disabled' : ''}>`
    + 'PDF erzeugen</button>'
    + `<span class="proxy-hinweis" role="status">${esc(sperre)}</span></div>`
    + '<p class="meldung" role="alert" data-erzeugen-fehler></p>'
    + (z.auftrag ? `<div class="proxy-auftrag">${auftragHtml(z.auftrag)}</div>` : '')
    + (z.auftrag?.zustand === 'fertig' ? '' : druckhinweisHtml());
}

async function erzeugen() {
  const z = Z;
  const v = z.vorschau;
  if (!v || v.zu_viele || veraltet(z) || laeuft(z)) return;
  const fehler = z.teil.erzeugen.querySelector('[data-erzeugen-fehler]');
  meldung(fehler, '');
  try {
    const name = z.feld.name.value.trim() || 'Deck';
    const a = await senden('/api/proxy/auftrag', { daten: {
      text: z.geprueftText, format: v.format, name, rueckseite: z.rueckseite,
      sets_ignorieren: z.geprueftSets, extra_art: z.feld.extra.checked } });
    if (Z !== z) return;
    z.auftrag = a;
    erzeugenZeichnen();
    planen();
  } catch (f) {
    if (Z === z) meldung(fehler, fehlerSatz(f));
  }
}

async function weiter(ja) {
  const z = Z;
  try {
    z.auftrag = await senden(`/api/proxy/auftrag/${encodeURIComponent(z.auftrag.id)}/weiter`, { daten: { weiter: ja } });
    if (Z !== z) return;
    erzeugenZeichnen();
    planen();
  } catch (f) {
    if (Z === z) meldung(z.teil.erzeugen.querySelector('[data-erzeugen-fehler]'), fehlerSatz(f));
  }
}

/** Fortschritt abfragen, solange etwas läuft (alle 1,5 s); der Takt ruht im verborgenen Tab. */
function planen() {
  const z = Z;
  clearTimeout(z.uhr);
  const stand = z.daten.stand ?? {};
  if (!(z.auftrag && LAUFEND.includes(z.auftrag.zustand)) && !stand.sucht) return;
  z.uhr = setTimeout(abfragen, document.hidden ? TAKT * 4 : TAKT);
}

async function abfragen() {
  const z = Z;
  if (!z) return;
  try {
    if (z.auftrag && LAUFEND.includes(z.auftrag.zustand)) {
      const vorher = z.auftrag.zustand;
      z.auftrag = await holen(`/api/proxy/auftrag/${encodeURIComponent(z.auftrag.id)}`);
      if (Z !== z) return;
      erzeugenZeichnen();
      if (vorher !== z.auftrag.zustand && z.auftrag.zustand === 'fertig') pdfsLaden();
    }
    if (z.daten.stand?.sucht) {
      const neu = await holen('/api/proxy');
      if (Z !== z) return;
      z.daten.stand = neu.stand;
      standZeichnen();
    }
  } catch { /* beim nächsten Takt noch einmal */ }
  if (Z === z) planen();
}

// ---------------------------------------------------------------- PDFs

async function pdfsLaden() {
  const z = Z;
  try {
    const antwort = await holen('/api/proxy/pdfs');
    if (Z !== z) return;
    z.pdfs = antwort.pdfs ?? [];
  } catch {
    if (Z === z) z.pdfs = null;
  }
  if (Z === z) pdfsZeichnen();
}

function pdfsZeichnen() {
  const z = Z;
  const liste = z.pdfs;
  const zeile = (p) => `<li class="proxy-pdf"><div class="proxy-pdf-text"><strong>${esc(p.name || p.datei)}</strong>`
    + `<span class="proxy-hinweis">${esc(zeit(p.erstellt))} · ${zahl(p.karten)} ${p.karten === 1 ? 'Karte' : 'Karten'}`
    + ` · ${zahl(p.seiten)} ${p.seiten === 1 ? 'Seite' : 'Seiten'} · ${esc(mb(p.bytes))}</span>`
    + ((p.fehlende ?? []).length ? `<span class="proxy-warnung proxy-klein">Ohne: ${esc(p.fehlende.join(', '))}</span>` : '')
    + `</div><a class="knopf" href="${esc(adresse(`api/proxy/pdf/${encodeURIComponent(p.datei)}`))}" download="${esc(p.datei)}"`
    + ` aria-label="PDF ${esc(p.name || p.datei)} herunterladen">${LADEN}<span>Laden</span></a></li>`;
  z.teil.pdfs.innerHTML = '<h2 class="proxy-titel">Letzte PDFs</h2>'
    + (liste === null ? '<p class="proxy-hinweis">Die Liste ließ sich nicht laden.</p>'
      : liste.length === 0 ? '<p class="proxy-hinweis">Noch keine PDFs erzeugt.</p>'
        : `<ul class="proxy-pdfs">${liste.slice(0, 50).map(zeile).join('')}</ul>`)
    + druckhinweisHtml();
}

// ---------------------------------------------------------------- Silhouette Card Maker

function standZeichnen() {
  const z = Z;
  const s = z.daten.stand ?? {};
  const commit = wert(s.commit);
  const quelle = wert(s.quelle);
  const zeile = (k, v) => (v ? `<div><dt class="etikett">${esc(k)}</dt><dd>${esc(v)}</dd></div>` : '');
  z.teil.stand.innerHTML = '<h2 class="proxy-titel">Silhouette Card Maker</h2>'
    + '<dl class="proxy-stand">'
    + zeile('Aktiver Stand', wert(s.aktiv) || (s.eingerichtet ? '' : 'nicht eingerichtet'))
    + zeile('Commit', commit.slice(0, 10))
    + zeile('Eingerichtet', s.eingerichtet && s.eingerichtet !== true ? zeit(wert(s.eingerichtet)) : s.eingerichtet === true ? 'ja' : '')
    + zeile('Neuester Stand', wert(s.neuester) && wert(s.neuester) !== wert(s.aktiv) ? wert(s.neuester) : '')
    + zeile('Zuletzt gesucht', s.gesucht ? zeit(wert(s.gesucht)) : '')
    + '</dl>'
    + (s.sucht ? '<p class="proxy-hinweis" role="status">Es wird nach einem neuen Stand gesucht …</p>' : '')
    + (s.meldung ? `<p class="proxy-hinweis" role="status">${esc(wert(s.meldung))}</p>` : '')
    + '<div class="proxy-zeile">'
    + `<button type="button" class="knopf" data-suchen${s.sucht ? ' disabled' : ''}>Jetzt nach neuem Stand suchen</button>`
    + (s.vorher ? '<button type="button" class="knopf" data-vorheriger>Vorherigen Stand verwenden</button>' : '')
    + '</div><p class="meldung" role="alert" data-stand-fehler></p>'
    + (/^https:\/\//.test(quelle) ? `<p class="proxy-hinweis"><a class="proxy-link" href="${esc(quelle)}" target="_blank"`
      + ' rel="noopener noreferrer">Quelle: Silhouette Card Maker</a></p>' : '')
    + `<p class="proxy-hinweis">${esc(LIZENZ)}</p>`;
}

async function standAktion(pfad, bestaetigung) {
  const z = Z;
  const fehler = () => z.teil.stand.querySelector('[data-stand-fehler]');
  if (bestaetigung) {
    const ja = await bestaetigen(bestaetigung);
    if (!ja || Z !== z) return;
  }
  try {
    await senden(pfad);
    const neu = await holen('/api/proxy');
    if (Z !== z) return;
    z.daten.stand = neu.stand;
    standZeichnen();
    planen();
  } catch (f) {
    if (Z === z) meldung(fehler(), fehlerSatz(f));
  }
}

// ---------------------------------------------------------------- Aus Deck übernehmen

/** Decks der App in die Auswahl; mit `deckId` (aus #/proxy/deck/7) gleich übernehmen und prüfen. */
async function deckwahlLaden(deckId) {
  const z = Z;
  const block = z.teil.eingabe.querySelector('[data-deckwahl]');
  let antwort;
  try {
    antwort = await holen('/api/proxy/decks');
  } catch {
    return;   // ohne Decks bleibt der Block verborgen
  }
  if (Z !== z || !antwort.decks?.length) return;
  const wahl = block.querySelector('#proxy-deck');
  wahl.innerHTML = antwort.decks.map((d) => `<option value="${Number(d.id)}">${esc(d.name)}</option>`).join('');
  block.hidden = false;
  if (deckId != null && antwort.decks.some((d) => Number(d.id) === Number(deckId))) {
    wahl.value = String(deckId);
    await deckUebernehmen();
  }
}

async function deckUebernehmen() {
  const z = Z;
  const block = z.teil.eingabe.querySelector('[data-deckwahl]');
  const status = block.querySelector('[data-deck-status]');
  const id = Number(block.querySelector('#proxy-deck').value);
  if (!id) return;
  const gruppen = [...block.querySelectorAll('[data-gruppe]')].filter((h) => h.checked).map((h) => h.dataset.gruppe);
  if (!gruppen.length) { status.textContent = 'Bitte mindestens eine Gruppe ankreuzen.'; return; }
  status.textContent = 'Deck wird gelesen …';
  try {
    const d = await holen(`/api/proxy/deck/${id}?gruppen=${gruppen.join(',')}`);
    if (Z !== z) return;
    for (const [g, n] of Object.entries(d.zahlen ?? {})) {
      const ziel = block.querySelector(`[data-zahl="${g}"]`);
      if (ziel) ziel.textContent = ` (${zahl(n)})`;
    }
    if (!d.text) {
      status.textContent = `In „${d.name}“ gibt es für diese Auswahl keine Karten.`;
      return;
    }
    z.feld.text.value = d.text;
    z.feld.name.value = d.name;
    z.formatWahl = d.format;
    z.feld.format.value = d.format;
    status.textContent = `${zahl(d.exemplare)} Karten aus „${d.name}“ übernommen.`;
    await pruefen();
  } catch (f) {
    if (Z === z) status.textContent = fehlerSatz(f);
  }
}

// ---------------------------------------------------------------- Ansicht

function verdrahten(bereich) {
  const z = Z;
  bereich.querySelector('.proxy-zurueck').addEventListener('click', () => zurueck('#/'));
  bereich.addEventListener('click', (e) => {
    const k = e.target.closest('button');
    if (!k || k.disabled) return;
    if (k.matches('[data-pruefen]')) pruefen();
    else if (k.matches('[data-deck-laden]')) deckUebernehmen();
    else if (k.matches('[data-erzeugen]')) erzeugen();
    else if (k.dataset.weiter) weiter(k.dataset.weiter === 'ja');
    else if (k.dataset.rueck) {
      z.rueckseite = k.dataset.rueck;
      vorliebe('proxy.rueckseite', z.rueckseite);
      rueckseitenZeichnen();
      z.teil.rueck.querySelector(`[data-rueck="${CSS.escape(z.rueckseite)}"]`)?.focus({ preventScroll: true });
    } else if (k.dataset.weg) rueckseiteEntfernen(k.dataset.weg);
    else if (k.matches('[data-suchen]')) standAktion('/api/proxy/stand/suchen');
    else if (k.matches('[data-vorheriger]')) {
      standAktion('/api/proxy/stand/zurueck', { titel: 'Vorherigen Stand verwenden?',
        text: 'Der Silhouette Card Maker wird auf den vorherigen Stand zurückgesetzt.', ja: 'Zurücksetzen' });
    }
  });
  bereich.addEventListener('change', (e) => {
    const t = e.target;
    if (t.id === 'proxy-datei') {
      const datei = t.files?.[0];
      if (!datei) return;
      datei.text().then((inhalt) => {
        if (Z !== z) return;
        z.feld.text.value = inhalt;
        bereich.querySelector('[data-dateiname]').textContent = datei.name;
        aenderung();
      }).catch(() => meldung(z.feld.fehler, 'Die Datei ließ sich nicht lesen.'));
      t.value = '';
    } else if (t.id === 'proxy-rueck-datei') {
      rueckseiteHochladen(t.files?.[0]);
      t.value = '';
    } else if (t.id === 'proxy-format') {
      z.formatWahl = t.value || null;
      if (z.vorschau && !veraltet(z)) pruefen();
    } else if (t.id === 'proxy-format2') {
      z.formatWahl = t.value;
      z.feld.format.value = t.value;
      pruefen();
    } else if (t.id === 'proxy-sets') {
      aenderung();
    }
  });
  z.feld.text.addEventListener('input', aenderung);
}

/** Text oder Sets geändert: Vorschau und Knopf zeigen „bitte neu prüfen“. */
function aenderung() {
  const z = Z;
  if (!z.vorschau) return;
  vorschauZeichnen();
  erzeugenZeichnen();
}

export async function zeigen(wurzel, parameter) {
  verlassen();
  const bereich = el('section', 'proxy');
  bereich.setAttribute('aria-label', 'Proxys');
  bereich.innerHTML = '<header class="proxy-kopfzeile">'
    + `<button type="button" class="knopf knopf-sym proxy-zurueck" aria-label="Zurück ins Regal">${ZURUECK}</button>`
    + '<h1 class="proxy-seitentitel">Proxys</h1></header>'
    + '<div class="proxy-inhalt"><p class="proxy-hinweis" role="status">Proxys werden geladen …</p></div>';
  wurzel.replaceChildren(bereich);
  const z = { bereich, daten: null, vorschau: null, geprueftText: '', geprueftSets: false, formatWahl: null, rueckseite: null,
    auftrag: null, pdfs: [], uhr: null, teil: {}, feld: {}, taste: null, nameGesetzt: false };
  Z = z;
  bereich.querySelector('.proxy-zurueck').addEventListener('click', () => zurueck('#/'));
  z.taste = (e) => {
    if (e.key === 'Escape' && !dialogOffen() && Z === z && !/^(TEXTAREA|INPUT|SELECT)$/.test(document.activeElement?.tagName)) {
      zurueck('#/');
    }
  };
  document.addEventListener('keydown', z.taste);

  let daten;
  try {
    daten = await holen('/api/proxy');
  } catch {
    if (Z === z) {
      bereich.querySelector('.proxy-inhalt').innerHTML = '<p class="proxy-hinweis" role="status">'
        + 'Der Bereich „Proxys“ gibt es nur im Home-Assistant-Add-on.</p>';
    }
    return;
  }
  if (Z !== z) return;
  z.daten = daten;
  z.auftrag = daten.auftrag ?? null;
  z.rueckseite = rueckseiteGewaehlt(z);

  const inhalt = bereich.querySelector('.proxy-inhalt');
  inhalt.innerHTML = '';
  const tafel = (name, klasse = '') => {
    const t = el('section', `proxy-tafel ${klasse}`);
    t.dataset.teil = name;
    inhalt.append(t);
    z.teil[name] = t;
    return t;
  };
  tafel('eingabe').innerHTML = eingabeHtml(daten.formate ?? []);
  tafel('vorschau').hidden = true;
  tafel('rueck');
  tafel('erzeugen');
  tafel('pdfs');
  tafel('stand');
  if (daten.hinweis) {
    const h = el('p', 'proxy-hinweis-tafel', esc(daten.hinweis));
    inhalt.prepend(h);
  }
  if (daten.katalog === false) {
    inhalt.prepend(el('p', 'proxy-hinweis-tafel', 'Der Katalog ist nicht eingerichtet. Die Zeilen werden nicht vorab geprüft.'));
  }
  const e = z.teil.eingabe;
  z.feld = { text: e.querySelector('#proxy-text'), name: e.querySelector('#proxy-name'), format: e.querySelector('#proxy-format'),
    sets: e.querySelector('#proxy-sets'), extra: e.querySelector('#proxy-extra'), knopf: e.querySelector('[data-pruefen]'),
    status: e.querySelector('[data-pruef-status]'), fehler: e.querySelector('[data-pruef-fehler]') };
  verdrahten(bereich);
  rueckseitenZeichnen();
  erzeugenZeichnen();
  pdfsZeichnen();
  standZeichnen();
  pdfsLaden();
  planen();
  deckwahlLaden(parameter?.deck ?? null);
}

export function verlassen() {
  if (!Z) return Promise.resolve();
  clearTimeout(Z.uhr);
  if (Z.taste) document.removeEventListener('keydown', Z.taste);
  Z = null;
  return Promise.resolve();
}
