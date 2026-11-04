// Farbwahl für Binder und Kisten (Wunsch des Nutzers, 04.11.2026): die festen Farben als schnelle Auswahl und
// ein eigenes Farbrad. Im Farbkreis bestimmt der Winkel den Farbton und der Abstand zur Mitte die Sättigung, der
// Regler darunter die Helligkeit. Eigene Farben gehen als #rrggbb an den Server. Nur Canvas und Zeigerereignisse,
// ohne Build; sieht am iPhone, in der Home-Assistant-App und am PC gleich aus.
import { esc } from './gemeinsam.js';

/** Feste Binderfarben (Werte wie auf dem Server, Farbton wie im Regal). */
export const BINDERFARBEN = [
  { wert: 'schwarz', text: 'Schwarz', hex: '#26262a' },
  { wert: 'durchsichtig', text: 'Durchsichtig', hex: '#d9d5cd' },
  { wert: 'gelb', text: 'Gelb', hex: '#d6a440' },
  { wert: 'blau', text: 'Blau', hex: '#2f4a6b' },
  { wert: 'gruen', text: 'Grün', hex: '#2f5444' },
  { wert: 'rot', text: 'Rot', hex: '#6a2b2a' },
  { wert: 'braun', text: 'Braun', hex: '#5b4a3c' },
];

/** Kisten: Messing, Silber und Kupfer sind dunkle Kisten mit Metalletikett; jede andere Farbe färbt die ganze Kiste
    (das Etikett bleibt dann Messing). */
export const KISTENFARBEN = [
  { wert: 'messing', text: 'Dunkel mit Messing', metall: '#c9a36a' },
  { wert: 'silber', text: 'Dunkel mit Silber', metall: '#b9bdc4' },
  { wert: 'kupfer', text: 'Dunkel mit Kupfer', metall: '#c07a52' },
  { wert: 'gruen', text: 'Grün', hex: '#2f5444' },
  { wert: 'blau', text: 'Blau', hex: '#2f4a6b' },
  { wert: 'rot', text: 'Rot', hex: '#6a2b2a' },
];

const DUNKEL = '#232120';          // Kistenkörper der Metall-Varianten im Farbfeld
const START = '#3a6f9c';           // erste eigene Farbe, solange noch keine gewählt ist

// ---------- Farbrechnung ----------

export const istEigen = (farbe) => /^#[0-9a-f]{6}$/i.test(String(farbe ?? ''));

function rgb(hex) {
  const n = parseInt(String(hex).slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function hexAus(r, g, b) {
  return '#' + [r, g, b].map((x) => Math.round(Math.min(255, Math.max(0, x))).toString(16).padStart(2, '0')).join('');
}

/** Mischt `hex` mit `ziel` (Anteil 0 … 1 von `ziel`). */
export function mischen(hex, ziel, anteil) {
  const a = rgb(hex);
  const b = rgb(ziel);
  return hexAus(...a.map((x, i) => x + (b[i] - x) * anteil));
}

/** Helle Farbe? Dann braucht sie dunkle Schrift (relative Leuchtdichte nach WCAG). */
export function istHell(hex) {
  const [r, g, b] = rgb(hex).map((x) => {
    const c = x / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.22;
}

/** HSV (Farbton 0 … 360, Sättigung und Helligkeit 0 … 1) → [r, g, b] 0 … 255. */
function hsvZuRgb(h, s, v) {
  const f = (n) => {
    const k = (n + h / 60) % 6;
    return (v - v * s * Math.max(0, Math.min(k, 4 - k, 1))) * 255;
  };
  return [f(5), f(3), f(1)];
}

const hsvZuHex = (h, s, v) => hexAus(...hsvZuRgb(h, s, v));

function hexZuHsv(hex) {
  const [r, g, b] = rgb(hex).map((x) => x / 255);
  const max = Math.max(r, g, b);
  const d = max - Math.min(r, g, b);
  let h = 0;
  if (d) {
    if (max === r) h = 60 * (((g - b) / d) % 6);
    else if (max === g) h = 60 * ((b - r) / d + 2);
    else h = 60 * ((r - g) / d + 4);
  }
  return { h: (h + 360) % 360, s: max ? d / max : 0, v: max };
}

// ---------- Anzeige im Regal und auf dem Lesetisch ----------

/** Stil eines Binderrückens mit eigener Farbe (sonst leer; die festen Farben haben Klassen im CSS). */
export function rueckenStil(farbe) {
  if (!istEigen(farbe)) return '';
  const hell = istHell(farbe);
  return `--r-grund:${farbe};`
    + (hell ? '--r-text:#1d1b18;--r-text-2:rgba(29,27,24,0.7);--r-linie:rgba(29,27,24,0.2);' : '');
}

/** Kiste im Regal: Klasse und Stil. Metall = dunkle Kiste mit Metalletikett, sonst ist die ganze Kiste farbig. */
export function kistenAussehen(farbe) {
  const f = String(farbe ?? '').toLowerCase();
  const fest = KISTENFARBEN.find((k) => k.wert === f);
  if (!istEigen(f) && (!fest || fest.metall)) return { klasse: `regal-ek-${fest ? f : 'messing'}`, stil: '' };
  const hex = istEigen(f) ? f : fest.hex;
  const hell = istHell(hex);
  return {
    klasse: 'regal-ek-messing regal-kiste-farbig',
    stil: `--kf:${hex};--kf-kante:${mischen(hex, hell ? '#000000' : '#ffffff', 0.22)};`
      + (hell ? '--kt:#1d1b18;--kt2:rgba(29,27,24,0.72);' : '--kt:#f4ece4;--kt2:rgba(244,236,228,0.72);'),
  };
}

const TISCH_VARIABLEN = ['--einband', '--einband-kante', '--seite-grund'];

/** Farbe des aufgeschlagenen Binders (`data-farbe` für feste Farben, Variablen für eigene). */
export function tischFarbe(tisch, farbe) {
  for (const v of TISCH_VARIABLEN) tisch.style.removeProperty(v);
  if (!istEigen(farbe)) {
    tisch.dataset.farbe = farbe || 'schwarz';
    return;
  }
  tisch.dataset.farbe = 'eigen';
  tisch.style.setProperty('--einband', mischen(farbe, '#000000', istHell(farbe) ? 0.45 : 0.1));
  tisch.style.setProperty('--einband-kante', mischen(farbe, '#ffffff', 0.18));
  tisch.style.setProperty('--seite-grund', mischen(farbe, '#000000', 0.82));
}

/** Text zu einer Farbe (fest oder eigen). */
export function farbText(farben, farbe) {
  if (istEigen(farbe)) return `Eigene Farbe ${String(farbe).toLowerCase()}`;
  return farben.find((f) => f.wert === farbe)?.text ?? '';
}

// ---------- Farbwahl ----------

let laufendeNummer = 0;

/** Farbwahl als Element (fieldset). `farben`: BINDERFARBEN oder KISTENFARBEN, `wert`: feste Farbe oder #rrggbb.
    Das Element hat die Eigenschaft `wert` und meldet jede Änderung mit dem Ereignis „farbwahl“ (detail = Wert). */
export function farbwahl({ farben, wert, beschriftung = 'Farbe' }) {
  const id = `fw-${++laufendeNummer}`;
  let aktuell = istEigen(wert) ? String(wert).toLowerCase() : (farben.some((f) => f.wert === wert) ? wert : farben[0].wert);
  let hsv = hexZuHsv(istEigen(aktuell) ? aktuell : START);

  const wurzel = document.createElement('fieldset');
  wurzel.className = 'fw';
  const muster = (f) => (f.metall ? `linear-gradient(135deg, ${DUNKEL} 0 55%, ${f.metall} 55%)` : f.hex);
  wurzel.innerHTML = `<legend class="etikett feld-etikett">${esc(beschriftung)} <span class="fw-name"></span></legend>`
    + '<div class="fw-liste" role="radiogroup" aria-label="' + esc(beschriftung) + '">'
    + farben.map((f) => `<button type="button" class="fw-feld" role="radio" data-wert="${esc(f.wert)}"`
      + ` aria-label="${esc(f.text)}" title="${esc(f.text)}"><span class="fw-punkt" style="background:${muster(f)}"></span></button>`).join('')
    + '<button type="button" class="fw-feld" role="radio" data-wert="eigen" aria-label="Eigene Farbe" title="Eigene Farbe">'
    + '<span class="fw-punkt fw-regenbogen"></span></button></div>'
    + '<div class="fw-rad" hidden>'
    + '<div class="fw-kreis"><canvas class="fw-leinwand" aria-hidden="true"></canvas><span class="fw-marke" aria-hidden="true"></span></div>'
    + `<label class="etikett feld-etikett" for="${id}-hell">Helligkeit</label>`
    + `<input id="${id}-hell" class="fw-hell" type="range" min="5" max="100" step="1">`
    + '<div class="fw-zeile"><span class="fw-vorschau" aria-hidden="true"></span>'
    + `<label class="nur-vorleser" for="${id}-hex">Farbcode</label>`
    + `<input id="${id}-hex" class="feld fw-hex" type="text" maxlength="7" autocomplete="off" spellcheck="false"`
    + ' autocapitalize="off" enterkeyhint="done"></div></div>';

  const name = wurzel.querySelector('.fw-name');
  const rad = wurzel.querySelector('.fw-rad');
  const leinwand = wurzel.querySelector('.fw-leinwand');
  const marke = wurzel.querySelector('.fw-marke');
  const regler = wurzel.querySelector('.fw-hell');
  const vorschau = wurzel.querySelector('.fw-vorschau');
  const hexFeld = wurzel.querySelector('.fw-hex');
  let gezeichnetMit = null;

  function zeichnen() {
    const groesse = leinwand.clientWidth || 220;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const px = Math.round(groesse * dpr);
    if (gezeichnetMit === `${px}:${hsv.v}`) return;
    gezeichnetMit = `${px}:${hsv.v}`;
    leinwand.width = px;
    leinwand.height = px;
    const ctx = leinwand.getContext('2d');
    if (!ctx) return;
    const bild = ctx.createImageData(px, px);
    const r = px / 2;
    for (let y = 0; y < px; y++) {
      for (let x = 0; x < px; x++) {
        const dx = x + 0.5 - r;
        const dy = y + 0.5 - r;
        const abstand = Math.hypot(dx, dy);
        if (abstand > r) continue;
        const [cr, cg, cb] = hsvZuRgb((Math.atan2(dy, dx) * 180 / Math.PI + 360) % 360, abstand / r, hsv.v);
        const i = (y * px + x) * 4;
        bild.data[i] = cr; bild.data[i + 1] = cg; bild.data[i + 2] = cb;
        bild.data[i + 3] = abstand > r - 1 ? Math.round(255 * (r - abstand)) : 255;   // glatter Rand
      }
    }
    ctx.putImageData(bild, 0, 0);
  }

  function markeSetzen() {
    const winkel = hsv.h * Math.PI / 180;
    marke.style.left = `${50 + Math.cos(winkel) * hsv.s * 50}%`;
    marke.style.top = `${50 + Math.sin(winkel) * hsv.s * 50}%`;
  }

  function anzeigen() {
    const eigen = istEigen(aktuell);
    for (const feld of wurzel.querySelectorAll('.fw-feld')) {
      const an = eigen ? feld.dataset.wert === 'eigen' : feld.dataset.wert === aktuell;
      feld.setAttribute('aria-checked', String(an));
      feld.tabIndex = an ? 0 : -1;
    }
    name.textContent = farbText(farben, aktuell);
    rad.hidden = !eigen;
    if (eigen) {
      regler.value = String(Math.round(hsv.v * 100));
      vorschau.style.background = aktuell;
      if (document.activeElement !== hexFeld) hexFeld.value = aktuell;
      markeSetzen();
      marke.style.background = aktuell;
      zeichnen();
    }
  }

  function setzen(neu, melden = true) {
    if (neu === aktuell) { anzeigen(); return; }
    aktuell = neu;
    anzeigen();
    if (melden) wurzel.dispatchEvent(new CustomEvent('farbwahl', { detail: aktuell, bubbles: true }));
  }

  wurzel.querySelector('.fw-liste').addEventListener('click', (e) => {
    const feld = e.target.closest('.fw-feld');
    if (!feld) return;
    setzen(feld.dataset.wert === 'eigen' ? hsvZuHex(hsv.h, hsv.s, hsv.v) : feld.dataset.wert);
  });
  // Pfeiltasten wie bei Optionsfeldern
  wurzel.querySelector('.fw-liste').addEventListener('keydown', (e) => {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) return;
    const felder = [...wurzel.querySelectorAll('.fw-feld')];
    const jetzt = felder.indexOf(document.activeElement);
    if (jetzt < 0) return;
    e.preventDefault();
    const ziel = felder[(jetzt + (e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 1) + felder.length) % felder.length];
    ziel.focus();
    ziel.click();
  });

  function ausZeiger(e) {
    const rahmen = leinwand.getBoundingClientRect();
    const r = rahmen.width / 2;
    const dx = e.clientX - rahmen.left - r;
    const dy = e.clientY - rahmen.top - r;
    hsv = { h: (Math.atan2(dy, dx) * 180 / Math.PI + 360) % 360, s: Math.min(1, Math.hypot(dx, dy) / r), v: hsv.v };
    setzen(hsvZuHex(hsv.h, hsv.s, hsv.v));
  }
  leinwand.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    leinwand.setPointerCapture?.(e.pointerId);
    ausZeiger(e);
  });
  leinwand.addEventListener('pointermove', (e) => {
    if (leinwand.hasPointerCapture?.(e.pointerId)) ausZeiger(e);
  });
  regler.addEventListener('input', () => {
    hsv = { ...hsv, v: Number(regler.value) / 100 };
    setzen(hsvZuHex(hsv.h, hsv.s, hsv.v));
  });
  hexFeld.addEventListener('input', () => {
    let text = hexFeld.value.trim().toLowerCase();
    if (!text.startsWith('#')) text = `#${text}`;
    if (!istEigen(text)) return;
    hsv = hexZuHsv(text);
    setzen(text);
  });
  hexFeld.addEventListener('blur', () => { hexFeld.value = istEigen(aktuell) ? aktuell : ''; });

  Object.defineProperty(wurzel, 'wert', { get: () => aktuell });
  // Erst zeichnen, wenn das Element sichtbar ist (die Breite des Kreises kommt aus dem CSS)
  requestAnimationFrame(anzeigen);
  anzeigen();
  return wurzel;
}
