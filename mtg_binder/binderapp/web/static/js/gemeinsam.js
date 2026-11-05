// Gemeinsame Hilfen für alle Ansichten: Schnittstelle, Bildadressen, Navigation, Bewegung.

export const BREITEN = [200, 400, 672];

/** Adresse relativ zur Seite: '/api/regal' → 'api/regal'. Hinter Home-Assistant-Ingress liegt die App
    unter einem Präfix (…/api/hassio_ingress/<Kennung>/); absolute Pfade würden dort ins Leere zeigen.
    Alle Adressen zum eigenen Server laufen deshalb hierüber. */
export function adresse(pfad) {
  return String(pfad).replace(/^\/+/, '');
}

/** Holt JSON von der Schnittstelle, z. B. holen('/api/regal'). Wirft bei Fehlern. */
export async function holen(pfad) {
  const antwort = await fetch(adresse(pfad), { headers: { Accept: 'application/json' } });
  if (!antwort.ok) throw new Error(`${pfad}: ${antwort.status}`);
  return antwort.json();
}

/** Fehler einer schreibenden Anfrage: `message` ist ein verständlicher deutscher Satz für die
    Oberfläche, `status` der HTTP-Status (0 = keine Verbindung), `daten` die Antwort (z. B. `doppelt`). */
export class SchnittstellenFehler extends Error {
  constructor(meldung, status = 0, daten = null) {
    super(meldung);
    this.status = status;
    this.daten = daten;
  }
}

/** Ersatztexte, falls die Antwort keinen eigenen Satz mitbringt. */
export function fehlerText(status) {
  return {
    0: 'Keine Verbindung zum Server. Bist du im Heimnetz?',
    401: 'Bitte zuerst anmelden.',
    403: 'Das ist nicht erlaubt.',
    404: 'Nicht gefunden – vielleicht wurde es inzwischen geändert.',
    409: 'Das passt nicht zum aktuellen Stand.',
    413: 'Die Datei ist zu groß.',
    415: 'Falscher Dateityp.',
    429: 'Zu viele Versuche. Bitte kurz warten.',
  }[status] ?? 'Das hat nicht geklappt. Bitte noch einmal versuchen.';
}

/** Schreibt über die Schnittstelle: senden('/api/binder', { daten: {...} }), Methode POST, PATCH oder DELETE.
    Gibt die JSON-Antwort zurück. Wirft SchnittstellenFehler mit dem Satz aus {"fehler": "…"}. Meldet sich
    der Server mit 401 (Sitzung abgelaufen), gibt es zusätzlich das Ereignis 'binder:abgelaufen'
    (anmeldung.js macht daraus 'binder:sitzung'). `datei` (File, Blob oder Uint8Array) ist roh der Rumpf, als `typ`
    (Standard text/csv; Bulk-Scan: image/jpeg und application/octet-stream). `signal`: AbortSignal zum Abbrechen. */
export async function senden(pfad, { methode = 'POST', daten, datei, typ = 'text/csv', signal } = {}) {
  let antwort;
  const mitDatei = datei !== undefined && datei !== null;
  try {
    antwort = await fetch(adresse(pfad), {
      method: methode,
      signal,
      headers: {
        Accept: 'application/json',
        ...(mitDatei ? { 'Content-Type': typ } : daten !== undefined ? { 'Content-Type': 'application/json' } : {}),
      },
      body: mitDatei ? datei : (daten !== undefined ? JSON.stringify(daten) : undefined),
      credentials: 'same-origin',
    });
  } catch {
    throw new SchnittstellenFehler(fehlerText(0), 0);
  }
  let inhalt = null;
  try { inhalt = await antwort.json(); } catch { /* keine JSON-Antwort */ }
  if (!antwort.ok) {
    if (antwort.status === 401) window.dispatchEvent(new CustomEvent('binder:abgelaufen'));
    throw new SchnittstellenFehler(inhalt?.fehler || fehlerText(antwort.status), antwort.status, inhalt);
  }
  return inhalt;
}

/** Meldet allen Ansichten, dass sich ein Binder geändert hat (neue Seite, Leerseite, Name …). */
export function geaendert(binderId) {
  window.dispatchEvent(new CustomEvent('binder:geaendert', { detail: { binderId: Number(binderId) } }));
}

/** Adresse eines vorbereiteten Bildes. art: 'foto' (eigener Ausschnitt) oder 'scryfall'. */
export function bildUrl(art, schluessel, breite = 400) {
  return bildPfad(`${art}/${encodeURIComponent(schluessel)}_${breite}.webp`);
}

// Nach „Datenbank löschen“ beginnen die Nummern neu, der Browser hält Bilder aber eine Woche vor. Der Stand in der
// Adresse trennt neue Bilder von alten mit gleichem Namen.
let bildStand = 0;
export function bildStandSetzen(stand) { bildStand = Number(stand) || 0; }

/** Adresse eines Bilds, `pfad` relativ zu bilder/. */
export function bildPfad(pfad) {
  return `bilder/${pfad}${bildStand ? `?s=${bildStand}` : ''}`;
}

/** Bild direkt von Scryfall (Ausweg, solange der PC noch kein Vorschaubild erzeugt hat, z. B. für eine eben
    von Hand hinzugefügte Karte; Entscheidung des Nutzers, 30.10.2026). null bei ungültiger ID. */
export function scryfallBild(id) {
  const s = String(id ?? '').toLowerCase();
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(s)) return null;
  return `https://cards.scryfall.io/normal/front/${s[0]}/${s[1]}/${s}.jpg`;
}

/** Euro-Betrag wie „1,23 €“; ohne Wert „–“. `rund`: ganze Euro (für große Gesamtwerte). */
export function euro(wert, { rund = false } = {}) {
  if (wert == null || !Number.isFinite(Number(wert))) return '–';
  return Number(wert).toLocaleString('de-DE', {
    style: 'currency', currency: 'EUR', minimumFractionDigits: rund ? 0 : 2, maximumFractionDigits: rund ? 0 : 2,
  });
}

/** Preisstreifen im unteren Sechzehntel einer Karte (Wunsch des Nutzers): „ab“ = Low, „Ø“ = Durchschnitt 7 Tage
    (bei Foil-Karten die Foil-Preise). Leer, wenn es keinen Preis gibt. Das Elternelement braucht position: relative
    und container-type: inline-size (siehe .preis-streifen in basis.css). */
export function preisStreifen(preis) {
  if (!preis || (preis.low == null && preis.avg7 == null)) return '';
  return `<span class="preis-streifen" aria-hidden="true"><span>ab ${euro(preis.low)}</span>`
    + `<span>Ø ${euro(preis.avg7)}</span></span>`;
}

/** Derselbe Preis als Satz für Vorleser. */
export function preisText(preis) {
  if (!preis || (preis.low == null && preis.avg7 == null)) return '';
  return `Preis ab ${euro(preis.low)}, Durchschnitt 7 Tage ${euro(preis.avg7)}`;
}

/** srcset über alle vorbereiteten Breiten. */
export function bildSrcset(art, schluessel) {
  return BREITEN.map((b) => `${bildUrl(art, schluessel, b)} ${b}w`).join(', ');
}

/** Text sicher in HTML einsetzen. */
export function esc(text) {
  return String(text ?? '').replace(/[&<>"']/g, (z) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[z]));
}

/** Navigation über den Hash. ersetzen=true für Blättern (kein neuer Verlaufseintrag). */
export function gehe(hash, { ersetzen = false } = {}) {
  if (location.hash === hash) return;
  if (ersetzen) history.replaceState(history.state, '', hash);
  else history.pushState({ vonApp: true }, '', hash);
  window.dispatchEvent(new HashChangeEvent('hashchange'));
}

/** Schließen-Knöpfe: einen Schritt zurück, wenn die App den Eintrag angelegt hat,
    sonst (direkt aufgerufene Adresse) durch `ersatz` ersetzen. */
export function zurueck(ersatz) {
  if (history.state && history.state.vonApp) history.back();
  else gehe(ersatz, { ersetzen: true });
}

export function bewegungReduziert() {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export const warten = (ms) => new Promise((fertig) => setTimeout(fertig, ms));

/** Wartet auf das Ende einer CSS-Animation oder eines Übergangs am Element selbst, höchstens
    `hoechstens` ms. Mit `name` zählt nur diese Animation bzw. diese Eigenschaft (z. B. 'transform'),
    sonst könnte ein Hover-Übergang zu früh weiterschalten. */
export function animationEnde(element, hoechstens = 1000, name = null) {
  if (bewegungReduziert()) return Promise.resolve();
  return new Promise((fertig) => {
    const passt = (e) => e.target === element && (!name || e.animationName === name || e.propertyName === name);
    const ende = (e) => {
      if (e && !passt(e)) return;
      clearTimeout(uhr);
      element.removeEventListener('animationend', ende);
      element.removeEventListener('transitionend', ende);
      fertig();
    };
    const uhr = setTimeout(ende, hoechstens);
    element.addEventListener('animationend', ende);
    element.addEventListener('transitionend', ende);
  });
}

/** Breite Ansicht (PC, Tablet quer): Doppelseite. Sonst eine Seite. */
export function istBreit() {
  return window.matchMedia('(min-width: 900px) and (min-aspect-ratio: 1/1)').matches;
}

/** Gespeicherte Vorliebe lesen/schreiben (darf fehlen, z. B. im privaten Modus). */
export function vorliebe(name, wert) {
  try {
    if (wert === undefined) return localStorage.getItem(`binder.${name}`);
    localStorage.setItem(`binder.${name}`, wert);
  } catch { /* ohne Speicher weiter */ }
  return wert ?? null;
}
