// Scan-Ansicht (#/scan): Bulk-Scan mit dem Handy über einem „Card Slinger“. Das Handy schickt kleine
// Graustufenbilder der Scanzone an das Add-on; das Add-on entscheidet, wann ausgelöst wird. Bei
// „ausloesen“ nimmt das Handy ein Standbild in voller Auflösung auf und lädt es in einer Warteschlange hoch.
// Das Handy löst nie selbst aus.

import { holen, senden, gehe, vorliebe } from './gemeinsam.js';

const KARTE = 63 / 88;            // Kartenformat der Scanzone
const BREITE = 60;                // Größe des Kontrollbildes
const HOEHE = 84;
const MIN_ABSTAND_MS = 120;       // frühestens so lange nach dem Start des vorigen Kontrollbildes
const VORSCHAU_BREITE = 280;

// Warteschlange und Zähler leben außerhalb der Ansicht: Verlässt man sie, wird weiter hochgeladen.
const warteschlange = [];         // { blob, vorschau, id, nummer, vorschauOk, verwerfen, laeuft, vorschauUrl }
const fehlgeschlagen = [];        // vom Server dreimal abgelehnt (nicht Netzfehler): bleiben sichtbar gemeldet
const AUFNAHMEN_MERKEN = 30;      // für „Letztes verwerfen“; ältere Vorschau-Adressen werden freigegeben
let pumpeLaeuft = false;
let pumpeMeldung = '';            // z. B. Netzlaufwerk fehlt
let gesperrt = false;             // 503: Scannen pausiert, bis wieder Platz ist
let ansicht = null;               // laufender Zustand der Kamera-Ansicht oder null

const SYM = {
  zurueck: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M15 5l-7 7 7 7"/></svg>',
};

const beforeunload = (e) => {
  if (warteschlange.length) { e.preventDefault(); e.returnValue = ''; }
};

const warte = (ms) => new Promise((f) => setTimeout(f, ms));

function alsBlob(canvas, typ, qualitaet) {
  return new Promise((fertig) => canvas.toBlob((b) => fertig(b), typ, qualitaet));
}

/* ---------- Hochladen ---------- */

async function pumpe() {
  if (pumpeLaeuft) return;
  pumpeLaeuft = true;
  window.addEventListener('beforeunload', beforeunload);
  try {
    while (warteschlange.length) {
      const e = warteschlange[0];
      e.laeuft = true;
      try {
        if (!e.id) {
          // Der Server nimmt Fotos auch nach dem Beenden des Durchgangs an und erkennt Wiederholungen (gleiche Datei)
          const r = await senden(`/api/scan/durchgang/${e.durchgang}/foto`, { datei: e.blob, typ: 'image/jpeg' });
          e.id = r.id; e.nummer = r.nummer;
          e.blob = null;                    // liegt jetzt im Add-on: Speicher am Handy freigeben
        }
        if (e.vorschau && !e.vorschauOk) {
          try {
            await senden(`/api/scan/foto/${e.id}/vorschau`, { datei: e.vorschau, typ: 'image/jpeg' });
          } catch (f) {
            if (f.status === 0 || e.vorschauVersuche++ < 2) throw f;   // nach drei Fehlschlägen ohne Vorschau weiter
          }
          e.vorschauOk = true;
          e.vorschau = null;
        }
        if (e.verwerfen) await senden(`/api/scan/foto/${e.id}/verwerfen`, {});
        warteschlange.shift();
        pumpeMeldung = fehlgeschlagen.length ? pumpeMeldung : ''; gesperrt = false;
        e.laeuft = false;
      } catch (f) {
        e.laeuft = false;
        if (f.status === 503) {
          gesperrt = true;
          pumpeMeldung = f.message || 'Das Netzlaufwerk fehlt. Das Scannen ist pausiert.';
          anzeigen();
          await warte(5000);
        } else if (f.status === 0) {
          await warte(2000);                // Netzfehler: nach 2 s erneut, nichts verlieren
        } else if (++e.versuche < 3) {
          pumpeMeldung = f.message || 'Hochladen hat nicht geklappt – neuer Versuch.';
          await warte(4000);
        } else {
          // Dreimal abgelehnt: nicht endlos die Warteschlange blockieren, aber sichtbar und dauerhaft melden
          warteschlange.shift();
          fehlgeschlagen.push(e);
          pumpeMeldung = `${fehlgeschlagen.length} Foto(s) ließen sich nicht hochladen: ${f.message}`;
        }
      }
      anzeigen();
    }
  } finally {
    pumpeLaeuft = false;
    window.removeEventListener('beforeunload', beforeunload);
    anzeigen();
  }
}

/* ---------- Anzeige ---------- */

const ZUSTAND_TEXT = {
  startet: 'Starte …',
  bewegung: 'Bewegung …',
  beruhigt: 'Karte liegt …',
  leer: 'Scanzone leer erkannt – bereit',
  bereit: 'Bereit für die nächste Karte',
  ausloesen: 'Foto wird aufgenommen',
};

function anzeigen() {
  const a = ansicht;
  if (!a || !a.laufend) return;
  const n = warteschlange.length;
  a.el.zaehler.textContent = String(a.anzahl);
  a.el.warten.textContent = n ? `${n} warten auf Upload` : 'Alles hochgeladen';
  a.el.warten.classList.toggle('scan-aktiv', n > 0);
  a.el.fehler.textContent = pumpeMeldung || a.meldung || '';
  a.el.pause.textContent = a.pausiert ? 'Weiter' : 'Pause';
  a.el.status.textContent = gesperrt ? 'Pausiert – Netzlaufwerk fehlt'
    : a.pausiert ? 'Pausiert' : (ZUSTAND_TEXT[a.zustand] || 'Warte auf das erste Bild …');
  a.el.fps.textContent = a.fps ? `${a.fps.toFixed(1).replace('.', ',')} Bilder/s` : '';
  a.el.messwerte.hidden = !a.messwerteAn;
  if (a.messwerteAn) {
    const m = a.letzte || {};
    const z = (x) => (typeof x === 'number' ? x.toFixed(2).replace('.', ',') : '–');
    a.el.messwerte.textContent = `Abweichung ${z(m.abweichung)} · zur Aufnahme ${z(m.zur_aufnahme)} · `
      + `Spitze ${z(m.spitze)} · Grund der letzten Auslösung: ${a.letzterGrund || '–'}`;
  }
  a.el.verwerfen.disabled = !a.aufnahmen.length;
}

function rahmenLegen() {
  const a = ansicht;
  if (!a || !a.video.videoWidth) return;
  const vw = a.video.videoWidth, vh = a.video.videoHeight;
  const cw = a.el.buehne.clientWidth, ch = a.el.buehne.clientHeight;
  if (!cw || !ch) return;
  const massstab = Math.min(cw / vw, ch / vh);   // object-fit: contain
  const dx = (cw - vw * massstab) / 2, dy = (ch - vh * massstab) / 2;
  const z = zone();
  Object.assign(a.el.rahmen.style, {
    left: `${dx + z.x * massstab}px`, top: `${dy + z.y * massstab}px`,
    width: `${z.b * massstab}px`, height: `${z.h * massstab}px`, display: 'block',
  });
}

/** Scanzone in Videopixeln: größtes 63:88-Rechteck, mal Regler, mittig. */
function zone() {
  const a = ansicht;
  const vw = a.video.videoWidth, vh = a.video.videoHeight;
  let b, h;
  if (vw / vh > KARTE) { h = vh; b = h * KARTE; } else { b = vw; h = b / KARTE; }
  b *= a.prozent / 100; h *= a.prozent / 100;
  return { x: (vw - b) / 2, y: (vh - h) / 2, b, h };
}

function klick(a) {
  try {
    const ctx = a.ton; if (!ctx) return;
    if (ctx.state === 'suspended') ctx.resume();
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.type = 'sine'; o.frequency.value = 1250;
    g.gain.setValueAtTime(0.0001, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.25, ctx.currentTime + 0.005);
    g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.07);
    o.connect(g).connect(ctx.destination);
    o.start(); o.stop(ctx.currentTime + 0.08);
  } catch { /* Ton ist nur ein Zusatz */ }
}

function blitz(a) {
  a.el.rahmen.classList.remove('scan-blitz');
  void a.el.rahmen.offsetWidth;
  a.el.rahmen.classList.add('scan-blitz');
}

/* ---------- Aufnahme ---------- */

async function aufnehmen(a, grund, quelle = a.video, z = zone()) {
  const voll = document.createElement('canvas');
  voll.width = a.video.videoWidth; voll.height = a.video.videoHeight;
  voll.getContext('2d').drawImage(quelle, 0, 0, voll.width, voll.height);
  const klein = document.createElement('canvas');
  klein.width = VORSCHAU_BREITE; klein.height = Math.round(VORSCHAU_BREITE * z.h / z.b);
  klein.getContext('2d').drawImage(voll, z.x, z.y, z.b, z.h, 0, 0, klein.width, klein.height);
  klick(a); blitz(a);
  const [blob, vorschau] = await Promise.all([alsBlob(voll, 'image/jpeg', 0.92), alsBlob(klein, 'image/jpeg', 0.8)]);
  if (!blob) { a.meldung = 'Das Standbild ließ sich nicht aufnehmen.'; return; }
  const eintrag = { durchgang: a.durchgang, blob, vorschau, id: null, nummer: null, vorschauOk: false, vorschauVersuche: 0,
    versuche: 0, verwerfen: false, laeuft: false, vorschauUrl: vorschau ? URL.createObjectURL(vorschau) : null };
  a.aufnahmen.push(eintrag);
  while (a.aufnahmen.length > AUFNAHMEN_MERKEN) {
    const alt = a.aufnahmen.shift();
    if (alt.vorschauUrl) { URL.revokeObjectURL(alt.vorschauUrl); alt.vorschauUrl = null; }
  }
  warteschlange.push(eintrag);
  a.anzahl += 1;
  a.letzterGrund = grund || '';
  if (eintrag.vorschauUrl) { a.el.letztes.src = eintrag.vorschauUrl; a.el.letztes.hidden = false; }
  anzeigen();
  pumpe();
}

async function letztesVerwerfen(a) {
  const e = a.aufnahmen.pop();
  if (!e) return;
  a.anzahl = Math.max(0, a.anzahl - 1);
  const pos = warteschlange.indexOf(e);
  if (pos >= 0 && !e.id) {
    if (e.laeuft) e.verwerfen = true;                // gerade im Flug: nach dem Hochladen verwerfen
    else warteschlange.splice(pos, 1);               // noch nicht begonnen: nur aus der Warteschlange nehmen
  } else if (pos >= 0) {
    e.verwerfen = true;                              // hochgeladen, Vorschau fehlt noch: danach verwerfen
  } else if (e.id) {
    try { await senden(`/api/scan/foto/${e.id}/verwerfen`, {}); }
    catch (f) { a.meldung = f.message; a.anzahl += 1; a.aufnahmen.push(e); }
  }
  const letzte = a.aufnahmen[a.aufnahmen.length - 1];
  if (letzte?.vorschauUrl) a.el.letztes.src = letzte.vorschauUrl; else a.el.letztes.hidden = true;
  anzeigen();
}

/* ---------- Bildschleife ---------- */

async function schleife(a) {
  const hilf = document.createElement('canvas');
  hilf.width = BREITE; hilf.height = HOEHE;
  const ctx = hilf.getContext('2d', { willReadFrequently: true });
  const zeiten = [];
  while (a.laufend) {
    const start = performance.now();
    if (a.pausiert || gesperrt || document.hidden || !a.video.videoWidth || a.beendet) {
      a.neu = true;
      await warte(150);
      continue;
    }
    const z = zone();
    // Dasselbe Videobild für das Kontrollbild und (bei Auslösung) das Standbild merken
    let quelle = a.video;
    if (typeof createImageBitmap === 'function') {
      try {
        const neuesBild = await createImageBitmap(a.video);
        try { a.bitmap?.close(); } catch { /* egal */ }
        a.bitmap = neuesBild;
        quelle = neuesBild;
      } catch { /* Rückfall: aktuelles Videobild */ }
    }
    ctx.drawImage(quelle, z.x, z.y, z.b, z.h, 0, 0, BREITE, HOEHE);
    const rgba = ctx.getImageData(0, 0, BREITE, HOEHE).data;
    const grau = new Uint8Array(BREITE * HOEHE);
    for (let i = 0, j = 0; j < grau.length; i += 4, j += 1) grau[j] = (rgba[i] * 77 + rgba[i + 1] * 150 + rgba[i + 2] * 29) >> 8;
    const neu = a.neu, jetzt = a.jetzt;
    a.neu = false; a.jetzt = false;
    a.lauf += 1;
    const pfad = `/api/scan/durchgang/${a.durchgang}/bild?b=${BREITE}&h=${HOEHE}&t=${Math.round(start)}&n=${a.lauf}`
      + `${neu ? '&neu=1' : ''}${jetzt ? '&jetzt=1' : ''}`;
    a.imFlug = true;
    try {
      let r;
      for (let versuch = 0; ; versuch += 1) {
        try {
          r = await senden(pfad, { datei: grau, typ: 'application/octet-stream' });
          break;
        } catch (f) {
          // Netzfehler: dasselbe Bild mit derselben Laufnummer noch einmal. Hatte das Add-on schon ausgelöst,
          // antwortet es mit derselben Entscheidung, und das Foto stammt weiter aus genau diesem Videobild.
          if (f.status !== 0 || versuch >= 2 || !a.laufend) throw f;
          await warte(300);
        }
      }
      if (!a.laufend) break;
      a.zustand = r.zustand; a.letzte = r;
      if (a.meldung && a.meldungKurz) { a.meldung = ''; a.meldungKurz = false; }
      zeiten.push(performance.now());
      while (zeiten.length > 12) zeiten.shift();
      if (zeiten.length > 2) a.fps = (zeiten.length - 1) * 1000 / (zeiten[zeiten.length - 1] - zeiten[0]);
      if (r.ausloesen) {
        a.zustand = 'ausloesen';
        await aufnehmen(a, r.grund, quelle, z);
        a.neu = false;
      }
    } catch (f) {
      if (!a.laufend) break;
      a.neu = neu || a.neu; a.jetzt = jetzt || a.jetzt;     // nichts verlieren
      if (f.status === 409) {
        a.meldung = `${f.message || 'Der Durchgang ist beendet.'} Starte einen neuen Durchgang.`;
        a.beendet = true;
        a.el.fort.hidden = false;
      } else { a.meldung = f.status === 0 ? 'Keine Verbindung – ich versuche es weiter.' : f.message; a.meldungKurz = true; }
      await warte(1000);
    } finally {
      a.imFlug = false;
    }
    anzeigen();
    const rest = MIN_ABSTAND_MS - (performance.now() - start);
    if (rest > 0) await warte(rest);
  }
}

/* ---------- Kamera ---------- */

async function kameraHolen() {
  if (typeof window.binderScanQuelle === 'function') return window.binderScanQuelle();
  if (!navigator.mediaDevices?.getUserMedia) {
    const f = new Error('Die Kamera geht im Browser nur über HTTPS. Öffne Home Assistant über die Nabu-Casa-Adresse (nicht über http://…:8123).');
    f.sprechend = true;
    throw f;
  }
  return navigator.mediaDevices.getUserMedia({
    video: { facingMode: { ideal: 'environment' }, width: { ideal: 3840 }, height: { ideal: 2160 } }, audio: false,
  });
}

async function wachhalten(a) {
  try { a.sperre = await navigator.wakeLock?.request('screen'); } catch { /* geht nicht überall */ }
}

function aufraeumen() {
  const a = ansicht;
  if (!a) return;
  a.laufend = false;
  clearInterval(a.erkennUhr);
  try { a.bitmap?.close(); } catch { /* egal */ }
  a.strom?.getTracks().forEach((t) => t.stop());
  a.video.srcObject = null;
  try { a.sperre?.release(); } catch { /* egal */ }
  try { a.ton?.close(); } catch { /* egal */ }
  document.removeEventListener('visibilitychange', a.sichtbar);
  window.removeEventListener('resize', a.groesse);
  a.beobachter?.disconnect();
  ansicht = null;
}

async function starten(a) {
  a.ton = new (window.AudioContext || window.webkitAudioContext)();
  a.ton.resume?.();
  a.el.start.disabled = true;
  a.el.fehlerStart.textContent = '';
  try {
    const r = await senden('/api/scan/durchgang', { daten: a.neuerDurchgang ? { neu: true } : {} });
    a.durchgang = r.id;
    a.fortgesetzt = !r.neu;
    try {
      const d = await holen(`/api/scan/durchgang/${r.id}`);
      a.anzahl = (d.fotos || []).filter((f) => f.status !== 'verworfen').length;
    } catch { a.anzahl = 0; }
  } catch (f) {
    a.el.fehlerStart.textContent = f.status === 503 ? (f.message || 'Das Netzlaufwerk fehlt. Scannen geht erst wieder, wenn es erreichbar ist.') : f.message;
    a.el.start.disabled = false;
    return;
  }
  if (ansicht !== a) return;               // inzwischen verlassen
  try {
    a.strom = await kameraHolen();
    if (ansicht !== a) {                   // während der Kamerafreigabe verlassen: Kamera gleich wieder aus
      a.strom.getTracks().forEach((t) => t.stop());
      return;
    }
  } catch (f) {
    a.el.fehlerStart.textContent = f.sprechend ? f.message
      : f.name === 'NotAllowedError' ? 'Die Kamera ist nicht freigegeben. Erlaube den Zugriff in den Einstellungen des Browsers oder der Home-Assistant-App und versuche es noch einmal.'
      : f.name === 'NotFoundError' ? 'Auf diesem Gerät wurde keine Kamera gefunden.'
      : `Die Kamera ließ sich nicht starten (${f.name || f.message}).`;
    a.el.start.disabled = false;
    return;
  }
  a.video.srcObject = a.strom;
  try { await a.video.play(); } catch { /* autoplay greift meist schon */ }
  if (ansicht !== a) { a.strom.getTracks().forEach((t) => t.stop()); return; }
  const spur = a.strom.getVideoTracks()[0];
  if (spur?.getCapabilities?.().torch) {
    a.spur = spur;
    a.el.lampe.hidden = false;
  }
  a.el.start_.hidden = true;
  a.el.lauf.hidden = false;
  a.el.fort.hidden = !a.fortgesetzt;
  if (a.fortgesetzt) a.meldung = 'Ein offener Durchgang wurde fortgesetzt.';
  a.laufend = true; a.neu = true;
  wachhalten(a);
  a.groesse = () => rahmenLegen();
  window.addEventListener('resize', a.groesse);
  a.beobachter = new ResizeObserver(a.groesse);
  a.beobachter.observe(a.el.buehne);
  a.video.addEventListener('loadedmetadata', a.groesse);
  rahmenLegen();
  a.sichtbar = () => {
    if (!document.hidden) { a.neu = true; wachhalten(a); }
  };
  document.addEventListener('visibilitychange', a.sichtbar);
  anzeigen();
  schleife(a);
  erkennungZeigen(a);
  a.erkennUhr = setInterval(() => erkennungZeigen(a), 8000);
}

/** Läuft der PC, erkennt er die Fotos nach und nach: Zahl und zuletzt erkannte Karte dieses Durchgangs zeigen. */
async function erkennungZeigen(a) {
  if (document.hidden || !a.durchgang) return;
  try {
    const d = await holen(`/api/scan/durchgang/${a.durchgang}`);
    if (ansicht !== a) return;
    const erkannt = (d.fotos || []).filter((f) => f.karte);
    const pruefen = erkannt.filter((f) => f.karte.status !== 'sicher' && !f.karte.geprueft).length;
    const letzte = erkannt.sort((x, y) => y.nummer - x.nummer)[0];
    a.el.erkannt.hidden = !erkannt.length;
    a.el.erkannt.textContent = erkannt.length
      ? `${erkannt.length} erkannt${pruefen ? ` (${pruefen} zu prüfen)` : ''} · zuletzt: ${letzte.karte.name}`
      : '';
  } catch { /* nur eine Anzeige */ }
}

async function fertig(a) {
  a.el.fertig.disabled = true;
  a.pausiert = true;
  a.el.status.textContent = 'Lade die letzten Fotos hoch …';
  // Erst das Bild abwarten, das gerade beim Add-on ist (es kann noch eine Aufnahme auslösen), dann die Warteschlange
  while (a.imFlug || warteschlange.length) { await warte(300); anzeigen(); if (ansicht !== a) return; }
  try {
    await senden(`/api/scan/durchgang/${a.durchgang}/beenden`, {});
  } catch (f) {
    a.meldung = f.message; a.pausiert = false; a.neu = true; a.el.fertig.disabled = false; anzeigen();
    return;
  }
  gehe('#/scan/eingang');
}

/* ---------- Ansicht ---------- */

export async function zeigen(wurzel) {
  aufraeumen();
  const bereich = document.createElement('section');
  bereich.className = 'scan';
  bereich.setAttribute('aria-label', 'Scannen');
  bereich.innerHTML = `
    <header class="scan-kopf">
      <button type="button" class="knopf knopf-sym scan-zurueck" aria-label="Zurück zum Regal">${SYM.zurueck}</button>
      <h1 class="scan-titel">Scannen</h1>
      <span class="scan-zaehler-box" hidden><span class="scan-zaehler">0</span><span class="etikett">Fotos</span></span>
    </header>
    <div class="scan-start">
      <p class="scan-text">Lege das Handy so über den Card Slinger, dass die Scanzone auf der Karte liegt. Jede eingeworfene Karte wird erkannt und fotografiert, sobald sie ruhig liegt.</p>
      <button type="button" class="knopf knopf-haupt scan-los">Scan starten</button>
      <p class="meldung scan-fehler-start" role="alert"></p>
    </div>
    <div class="scan-lauf" hidden>
      <div class="scan-buehne">
        <video class="scan-video" playsinline muted autoplay></video>
        <div class="scan-rahmen" style="display:none"></div>
        <img class="scan-letztes" alt="Letztes Foto" hidden>
      </div>
      <div class="scan-info">
        <p class="scan-status" role="status"></p>
        <p class="scan-nebenzeile"><span class="scan-warten"></span><span class="scan-fps"></span></p>
        <p class="scan-erkannt" hidden></p>
        <p class="scan-messwerte" hidden></p>
        <p class="meldung scan-fehler" role="alert"></p>
        <div class="scan-regler"><label for="scan-groesse" class="etikett">Scanzone</label>
          <input id="scan-groesse" type="range" min="50" max="100" step="1"><output class="scan-prozent"></output></div>
        <label class="scan-umschalter"><input type="checkbox" class="scan-messwerte-an"> <span>Messwerte</span></label>
      </div>
      <div class="scan-knoepfe">
        <button type="button" class="knopf scan-pause">Pause</button>
        <button type="button" class="knopf knopf-haupt scan-jetzt">Jetzt auslösen</button>
        <button type="button" class="knopf scan-verwerfen">Letztes verwerfen</button>
        <button type="button" class="knopf scan-lampe" hidden aria-pressed="false">Taschenlampe</button>
        <button type="button" class="knopf scan-neu-durchgang" hidden>Neuer Durchgang</button>
        <button type="button" class="knopf knopf-haupt scan-fertig">Fertig</button>
      </div>
    </div>`;
  wurzel.replaceChildren(bereich);
  const q = (s) => bereich.querySelector(s);
  const a = {
    el: {
      buehne: q('.scan-buehne'), rahmen: q('.scan-rahmen'), letztes: q('.scan-letztes'), zaehler: q('.scan-zaehler'),
      warten: q('.scan-warten'), fps: q('.scan-fps'), status: q('.scan-status'), messwerte: q('.scan-messwerte'),
      erkannt: q('.scan-erkannt'),
      fehler: q('.scan-fehler'), pause: q('.scan-pause'), verwerfen: q('.scan-verwerfen'), lampe: q('.scan-lampe'),
      fertig: q('.scan-fertig'), fort: q('.scan-neu-durchgang'), start: q('.scan-los'), start_: q('.scan-start'),
      lauf: q('.scan-lauf'), fehlerStart: q('.scan-fehler-start'),
    },
    video: q('.scan-video'), laufend: false, pausiert: false, neu: true, jetzt: false, zustand: null, letzte: null,
    letzterGrund: '', messwerteAn: false, anzahl: 0, aufnahmen: [], meldung: '', meldungKurz: false, fps: 0,
    prozent: Math.min(100, Math.max(50, Number(vorliebe('scan.zone')) || 85)), durchgang: null, fortgesetzt: false,
    neuerDurchgang: false, beendet: false, lauf: 0, imFlug: false,
  };
  ansicht = a;

  // Stand der Schnittstelle: bei alter Datenbank (409) ausblenden
  try { await holen('/api/scan'); } catch (f) {
    q('.scan-start').innerHTML = '<p class="scan-text">Scannen ist mit dieser Datenbank noch nicht möglich. Sie braucht ein Update.</p>';
    q('.scan-zurueck').addEventListener('click', () => gehe('#/'));
    return;
  }
  q('.scan-zurueck').addEventListener('click', () => gehe('#/'));
  a.el.start.addEventListener('click', () => starten(a));

  const regler = q('#scan-groesse'), prozent = q('.scan-prozent');
  regler.value = String(a.prozent); prozent.textContent = `${a.prozent} %`;
  regler.addEventListener('input', () => {
    a.prozent = Number(regler.value);
    prozent.textContent = `${a.prozent} %`;
    vorliebe('scan.zone', String(a.prozent));
    a.neu = true;
    rahmenLegen();
  });
  q('.scan-messwerte-an').addEventListener('change', (e) => { a.messwerteAn = e.target.checked; anzeigen(); });
  a.el.pause.addEventListener('click', () => { a.pausiert = !a.pausiert; if (!a.pausiert) a.neu = true; anzeigen(); });
  q('.scan-jetzt').addEventListener('click', () => { a.jetzt = true; });
  a.el.verwerfen.addEventListener('click', () => letztesVerwerfen(a));
  a.el.fertig.addEventListener('click', () => fertig(a));
  a.el.lampe.addEventListener('click', async () => {
    const an = a.el.lampe.getAttribute('aria-pressed') !== 'true';
    try {
      await a.spur.applyConstraints({ advanced: [{ torch: an }] });
      a.el.lampe.setAttribute('aria-pressed', String(an));
    } catch { a.meldung = 'Die Taschenlampe lässt sich nicht schalten.'; anzeigen(); }
  });
  a.el.fort.addEventListener('click', async () => {
    try {
      const r = await senden('/api/scan/durchgang', { daten: { neu: true } });
      a.durchgang = r.id; a.anzahl = 0; a.aufnahmen = []; a.neu = true; a.fortgesetzt = false; a.beendet = false;
      a.el.fort.hidden = true; a.meldung = 'Neuer Durchgang gestartet.'; a.el.letztes.hidden = true;
      anzeigen();
    } catch (f) { a.meldung = f.message; anzeigen(); }
  });
  q('.scan-zaehler-box').hidden = false;
}

/** Kamera aus, Schleife beenden, Wake Lock frei – beim Verlassen der Ansicht. */
export async function verlassen() {
  aufraeumen();
}
