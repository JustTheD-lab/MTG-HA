// Bibliothek im Stil „Galerie“: Jeder Binder steht als schlichter Rücken auf einer Messinglinie;
// Antippen nimmt ihn heraus (der Rücken gleitet nach vorn, dann öffnet sich der Lesetisch).
// Darunter ein zweiter Regalboden „Kisten“ (Bulkkisten ab Datenbankversion 3): Antippen zieht die Kiste
// nach vorn, dann öffnet sich die Kistenansicht. Im Kopf stehen Binder und Kisten mit ihren Karten getrennt
// (nie zusammengezählt), die Prüfen-Pille und der Zustand der Erkennung.
// Darüber die Suche über alle Binder mit Treffern als Karteikarten.
import { holen, senden, esc, gehe, bildUrl, bildSrcset, bewegungReduziert, euro, scryfallBild } from './gemeinsam.js';
import { knopf as anmeldeKnopf, angemeldet, laden, sitzung } from './anmeldung.js';
import { dialog, dialogOffen } from './dialog.js';
import { KISTENFARBEN, farbwahl, istEigen, kistenAussehen, rueckenStil } from './farbrad.js';

const ABSTAND = 4;                 // Lücke zwischen zwei Rücken in px (wie im CSS)
const FARBEN = ['schwarz', 'gelb', 'durchsichtig', 'blau', 'gruen', 'rot', 'braun'];
const NAME_MAX = 60;               // Länge eines Kistennamens (wie auf dem Server)
const STATUS_ABSTAND = 30000;      // Erkennung und Prüfen-Zähler: höchstens alle 30 s
const STATUS_MINDEST = 2000;       // beim Zurückkehren in den Tab nicht öfter als alle 2 s

let zustand = null;                // Elemente und Aufräumfunktionen der gerade gezeigten Ansicht
let letzteSuche = '';              // beim Zurückkommen aus einem Binder wieder da

// ---------- Hilfen ----------

/** Wartet auf das Ende genau dieser CSS-Animation (nicht auf Übergänge wie das Anheben beim
    Darüberfahren mit der Maus), höchstens `hoechstens` ms. Ohne Bewegung sofort fertig. */
function animationFertig(element, name, hoechstens) {
  if (bewegungReduziert()) return Promise.resolve();
  return new Promise((fertig) => {
    const ende = (ereignis) => {
      if (ereignis.animationName !== name) return;
      clearTimeout(uhr);
      element.removeEventListener('animationend', ende);
      fertig();
    };
    const uhr = setTimeout(() => ende({ animationName: name }), hoechstens);
    element.addEventListener('animationend', ende);
  });
}

const zahl = (n) => Number(n ?? 0).toLocaleString('de-DE');
const mehrzahl = (n, eins, viele) => `${zahl(n)} ${Number(n) === 1 ? eins : viele}`;

function farbklasse(farbe) {
  const f = String(farbe ?? '').toLowerCase().replace(/ü/g, 'ue').replace(/ß/g, 'ss').trim();
  if (istEigen(f)) return 'eigen';   // eigene Farbe aus dem Farbrad: Stil über rueckenStil
  return FARBEN.includes(f) ? f : 'braun';
}

/** Rückenbreite wächst mit der Seitenzahl (in px, ohne Maßstab). */
function grundbreite(b) {
  return Math.round(Math.min(120, Math.max(70, 62 + (b.seiten ?? 0) * 2.8)));
}

/** Höhenfaktor: 2x2 niedriger als 3x3, größere Raster etwas höher; je Binder leicht verschieden. */
function hoehenfaktor(b) {
  const zeilen = parseInt(b.raster, 10) || 3;
  const stufe = zeilen <= 2 ? 0.86 : zeilen === 3 ? 1 : 1.07;
  const abweichung = ((Number(b.id) * 7) % 5 - 2) * 0.01;
  return +(stufe + abweichung).toFixed(3);
}

/** Erste Schätzung der Titelgröße (rem); `titelAnpassen` verkleinert bei Bedarf weiter. */
function titelgroesse(name) {
  const n = [...String(name)].length;
  return n <= 12 ? 1.3 : n <= 18 ? 1.12 : n <= 26 ? 0.95 : 0.82;
}

/** Hinweise zum Zustand eines Binders: Karten zu prüfen, Fotos in der Erkennung, Fotos mit Fehler. */
function hinweiseBinder(b) {
  const hinweise = [];
  if (b.offen > 0) hinweise.push(`${mehrzahl(b.offen, 'Karte', 'Karten')} noch zu prüfen`);
  if (b.in_arbeit > 0) hinweise.push(`${mehrzahl(b.in_arbeit, 'Foto', 'Fotos')} in der Erkennung`);
  if (b.fehler > 0) hinweise.push(`${mehrzahl(b.fehler, 'Foto', 'Fotos')} mit Fehler`);
  return hinweise;
}

function textBinder(b) {
  const teile = [b.name, mehrzahl(b.karten, 'Karte', 'Karten'), `Raster ${b.raster}`, mehrzahl(b.seiten, 'Seite', 'Seiten'),
    ...(b.wert != null ? [`Wert ${euro(b.wert)}`] : []), ...hinweiseBinder(b)];
  return teile.join(', ');
}

function rueckenHtml(b) {
  const hinweise = hinweiseBinder(b);
  const titel = hinweise.length ? ` title="${esc(hinweise.join(', '))}"` : '';
  return `<li class="regal-platz"><button type="button" class="regal-ruecken regal-farbe-${farbklasse(b.farbe)}"`
    + ` data-binder="${b.id}" aria-label="${esc(textBinder(b))}"${titel}`
    + ` style="--faktor:${hoehenfaktor(b)};--titel:${titelgroesse(b.name)}rem;${rueckenStil(b.farbe)}">`
    + (b.offen > 0 ? '<span class="regal-offen" aria-hidden="true"></span>' : '')
    + (b.fehler > 0 ? '<span class="regal-fehler" aria-hidden="true"></span>' : '')
    + (b.in_arbeit > 0 ? '<span class="regal-arbeit" aria-hidden="true"></span>' : '')
    + `<span class="regal-titelfeld"><span class="regal-titel">${esc(b.name)}</span></span>`
    + `<span class="regal-zahlfeld"><span class="regal-zahl-karten">${esc(zahl(b.karten))}</span>`
    + `<span class="regal-zahl-raster">${esc(b.raster)}</span>`
    + (b.wert != null ? `<span class="regal-zahl-wert wert">${esc(euro(b.wert, { rund: b.wert >= 100 }))}</span>` : '')
    + '</span>'
    + '</button></li>';
}

function elementAus(html) {
  const vorlage = document.createElement('template');
  vorlage.innerHTML = html;
  return vorlage.content.firstElementChild;
}

// ---------- Kisten ----------

function textKiste(k) {
  return `Kiste ${k.name}, ${mehrzahl(k.karten, 'Karte', 'Karten')}, ${mehrzahl(k.posten, 'Posten', 'Posten')}`
    + (k.wert != null ? `, Wert ${euro(k.wert)}` : '');
}

/** Eine Kiste in Vorderansicht: oben angedeutete Kartenkanten, davor die dunkle Front mit Etikett. Metallfarben
    färben das Etikett, jede andere Farbe zeigt sich als Lesezeichen, das aus den Karten hängt (Wunsch des Nutzers,
    08.11.2026: vorher war die ganze Kiste farbig). Darunter Karten, Posten und Wert je in einer eigenen Zeile. */
function kisteHtml(k) {
  const aussehen = kistenAussehen(k.etikettfarbe);
  return `<li class="regal-kistenplatz"><button type="button" class="regal-kiste ${aussehen.klasse}"`
    + ` data-kiste="${k.id}" aria-label="${esc(textKiste(k))}"${aussehen.stil ? ` style="${aussehen.stil}"` : ''}>`
    + '<span class="regal-kiste-kanten" aria-hidden="true"></span>'
    + '<span class="regal-kiste-band" aria-hidden="true"></span>'
    + '<span class="regal-kiste-front">'
    + `<span class="regal-kiste-etikett"><span class="regal-kiste-name">${esc(k.name)}</span></span>`
    + '<span class="regal-kiste-zahlen etikett">'
    + `<span class="regal-kiste-karten">${esc(mehrzahl(k.karten, 'Karte', 'Karten'))}</span>`
    + `<span class="regal-kiste-posten">${esc(mehrzahl(k.posten, 'Posten', 'Posten'))}</span>`
    + (k.wert != null ? `<span class="regal-kiste-wert wert">${esc(euro(k.wert, { rund: k.wert >= 100 }))}</span>` : '')
    + '</span></span></button></li>';
}

// ---------- Decks ----------

function moebelBilderAbsichern(moebel) {
  moebel.addEventListener('error', (ereignis) => {
    const bild = ereignis.target;
    if (bild.tagName !== 'IMG' || !bild.closest('.regal-deck-bild')) return;
    const cdn = bild.dataset.sid ? scryfallBild(bild.dataset.sid) : null;
    if (cdn && !bild.dataset.cdn) {
      bild.dataset.cdn = '1';
      bild.src = cdn;
    } else {
      bild.closest('.regal-deck-bild').classList.add('regal-deck-bild--leer');
      bild.remove();
    }
  }, true);
}

/** Paket 10: aus der Sammlung baubar, nach der Einstellung des Decks (ältere Antworten ohne `baubar`: aus `summe`). */
function baubarVon(d) {
  const s = d.summe ?? {};
  return d.baubar ?? { karten: s.karten ?? 0, aus_sammlung: s.vorhanden ?? 0, proxy: s.proxy ?? 0, fehlt: s.fehlt ?? 0,
    prozent: s.karten ? Math.floor((s.vorhanden ?? 0) * 100 / s.karten) : null, abgleich: d.abgleich };
}

function baubarZusatz(b) {
  return [b.prozent != null ? `${b.prozent} %` : '', b.abgleich === 'druck' ? 'nach Druck' : 'nach Name',
    b.proxy ? `${zahl(b.proxy)} Proxy` : ''].filter(Boolean).join(' · ');
}

function textDeck(d) {
  const b = baubarVon(d);
  return `Deck ${d.name}, ${zahl(b.aus_sammlung)} von ${zahl(b.karten)} Karten aus der Sammlung baubar`
    + `${b.prozent != null ? `, ${b.prozent} Prozent` : ''}, Abgleich ${b.abgleich === 'druck' ? 'nach Druck' : 'nach Name'}`
    + (b.proxy ? `, ${zahl(b.proxy)} Proxy` : '')
    + (d.commander ? `, Commander ${d.commander}` : '');
}

/** Eine Deckbox: Ausschnitt aus dem Bild des Commanders oben, darunter Name und Anteil „34 / 65 baubar“. */
function deckHtml(d) {
  const b = baubarVon(d);
  const gesamt = b.karten ?? 0;
  const anteil = gesamt > 0 ? Math.min(100, Math.round((b.aus_sammlung ?? 0) / gesamt * 100)) : 0;
  const w = d.wert;
  const wertText = !w ? '' : d.gebaut && w.im_deck != null ? euro(w.im_deck)
    : w.liste != null ? `Liste ${euro(w.liste)}` : '';
  const bild = d.bild
    ? `<img alt="" width="63" height="88" loading="lazy" decoding="async" src="${esc(bildUrl('scryfall', d.bild, 400))}"`
      + ` data-sid="${esc(d.bild)}">`
    : '';
  return `<li class="regal-deckplatz"><button type="button" class="regal-deck" data-deck="${Number(d.id)}"`
    + ` aria-label="${esc(textDeck(d))}">`
    + `<span class="regal-deck-bild${bild ? '' : ' regal-deck-bild--leer'}" aria-hidden="true">${bild}</span>`
    + '<span class="regal-deck-front">'
    + `<span class="regal-deck-name">${esc(d.name)}</span>`
    + (d.commander ? `<span class="regal-deck-commander">${esc(d.commander)}</span>` : '')
    + `<span class="regal-deck-zahlen etikett">${esc(zahl(b.aus_sammlung))} / ${esc(zahl(gesamt))} baubar</span>`
    + `<span class="regal-deck-baubar">${esc(baubarZusatz(b))}</span>`
    + (wertText ? `<span class="regal-deck-wert">${esc(wertText)}</span>` : '')
    + `<span class="regal-deck-balken" aria-hidden="true"><span style="width:${anteil}%"></span></span>`
    + '</span></button></li>';
}

/** Dritter Regalboden „Decks“. Ohne Deck-Schnittstelle (`verfuegbar: false`) gibt es ihn nicht. */
function deckbodenHtml(decks) {
  return '<section class="regal-deckboden" aria-label="Decks im Regal">'
    + '<h2 class="regal-boden-titel etikett">Decks</h2>'
    + '<p class="regal-regeln-zeile"><button type="button" class="knopf regal-neues-deck" hidden'
    + ' aria-label="Neues Deck anlegen">' + PLUS + '<span>Neues Deck</span></button></p>'
    + (decks.length
      ? `<ul class="regal-decks" role="list">${decks.map(deckHtml).join('')}</ul>`
      : '<p class="regal-leer-hinweis">Noch keine Decks im Regal.</p>')
    + '</section>';
}

/** Etikett „Sammlung“ über den Böden: Gesamtzahl aus Bindern und Kisten, Tippen öffnet die Gesamtsammlung. */
function sammlungHtml(s) {
  if (!s || typeof s.karten !== 'number') return '';
  const text = `Gesamtsammlung, ${mehrzahl(s.karten, 'Karte', 'Karten')}, ${mehrzahl(s.posten, 'Posten', 'Posten')}`;
  return `<button type="button" class="regal-sammlung" data-sammlung aria-label="${esc(text)}">`
    + '<span class="regal-sammlung-etikett">Sammlung</span>'
    + `<span class="regal-sammlung-zahlen etikett">${esc(mehrzahl(s.karten, 'Karte', 'Karten'))}`
    + `<span aria-hidden="true"> · </span>${esc(mehrzahl(s.posten, 'Posten', 'Posten'))}</span>`
    + '<svg class="regal-sammlung-pfeil" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor"'
    + ' stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'
    + '<path d="M9 5l7 7-7 7"/></svg></button>';
}

/** Unaufdringlicher Einstieg „Sets“ (Vollständigkeit je Set) unter dem Platz „Sammlung“; nur mit Sammlung. */
function setsHtml(s) {
  if (!s || typeof s.karten !== 'number') return '';
  return '<p class="regal-sets-zeile"><button type="button" class="knopf regal-sets"'
    + ' aria-label="Sets öffnen: wie vollständig die Sammlung je Set ist">Sets</button>'
    + '<button type="button" class="knopf regal-kaufliste"'
    + ' aria-label="Kaufliste öffnen: fehlende Karten und Wunschpreise">Kaufliste</button>'
    + '<button type="button" class="knopf regal-dashboard"'
    + ' aria-label="Dashboard öffnen: Werte und Graphen der ganzen Sammlung">Dashboard</button>'
    + `<button type="button" class="knopf regal-secretlair"${sitzung().ha ? '' : ' hidden'}`
    + ' aria-label="Secret Lair öffnen: Drops mit Vergleichswerten">Secret Lair</button>'
    + '<button type="button" class="knopf regal-proxy" hidden'
    + ' aria-label="Proxys öffnen: Decklisten als druckfertige PDF">Proxys</button></p>';
}

/** Platz „Scan-Eingang“ im Stil der Sammlung (Bulk-Scan). Ohne Schnittstelle (alte Datenbank) gibt es ihn nicht. */
function scanPlatzHtml(s) {
  const g = s?.gesamt;
  if (!g || typeof g.fotos !== 'number') return '';
  const text = `${mehrzahl(g.fotos, 'Foto', 'Fotos')} · ${g.wartend ?? 0} warten`;
  return `<button type="button" class="regal-sammlung scan-platz" aria-label="Scan-Eingang, ${esc(text)}">`
    + '<span class="regal-sammlung-etikett">Scan-Eingang</span>'
    + `<span class="regal-sammlung-zahlen etikett">${esc(text)}</span>`
    + '<svg class="regal-sammlung-pfeil" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor"'
    + ' stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'
    + '<path d="M9 5l7 7-7 7"/></svg></button>';
}

/** Zweiter Regalboden unter den Bindern. Ohne Kisten-Schnittstelle (Datenbank Version 2) gibt es ihn nicht. */
function kistenbodenHtml(kisten) {
  return '<section class="regal-kistenboden" aria-label="Kisten im Regal">'
    + '<h2 class="regal-boden-titel etikett">Kisten</h2>'
    + '<p class="regal-regeln-zeile"><button type="button" class="knopf regal-regeln"'
    + ' aria-label="Sortierregeln öffnen: wohin neue Karten einsortiert werden">Sortierregeln</button></p>'
    + (kisten.length
      ? `<ul class="regal-kisten" role="list">${kisten.map(kisteHtml).join('')}</ul>`
      : '<p class="regal-leer-hinweis">Noch keine Kisten im Regal.</p>')
    + '</section>';
}

/** Dialog „Neue Kiste“: Name, Farbe (feste Farben oder Farbrad) und wahlweise eine Kiste, deren Farbe und Fächer die
    neue übernimmt. Gibt `{id}` der neuen Kiste zurück, sonst `undefined`. */
async function neueKisteFragen(kisten = []) {
  const form = document.createElement('form');
  form.id = 'kiste-neu-form';
  form.noValidate = true;
  form.autocomplete = 'off';
  form.innerHTML = '<label class="etikett feld-etikett" for="kiste-neu-name">Name</label>'
    + `<input id="kiste-neu-name" class="feld" type="text" name="name" maxlength="${NAME_MAX}" autocomplete="off"`
    + ' autocapitalize="sentences" spellcheck="false" enterkeyhint="done" required'
    + ' aria-describedby="kiste-neu-meldung">'
    + (kisten.length
      ? '<label class="etikett feld-etikett" for="kiste-neu-vorlage">Einstellungen übernehmen von</label>'
        + '<select id="kiste-neu-vorlage" class="feld"><option value="">Keiner Kiste</option>'
        + kisten.map((k) => `<option value="${esc(k.id)}">${esc(k.name)}</option>`).join('') + '</select>'
      : '')
    + '<p class="meldung" id="kiste-neu-meldung" role="alert" aria-live="assertive"></p>';
  const wahl = farbwahl({ farben: KISTENFARBEN, wert: 'messing' });
  form.querySelector('.meldung').before(wahl);
  // Vorlage gewählt: Ihre Farbe erscheint in der Farbwahl und lässt sich dort noch ändern; die Fächer kopiert der Server
  const vorlage = form.querySelector('#kiste-neu-vorlage');
  vorlage?.addEventListener('change', () => {
    const k = kisten.find((x) => String(x.id) === vorlage.value);
    if (k) wahl.wert = k.etikettfarbe;
  });

  const d = dialog({ titel: 'Neue Kiste', klasse: 'dialog-klein', inhalt: form });
  const feld = form.querySelector('#kiste-neu-name');
  const meldung = form.querySelector('.meldung');
  const abbrechen = Object.assign(document.createElement('button'),
    { type: 'button', className: 'knopf', textContent: 'Abbrechen' });
  const los = Object.assign(document.createElement('button'),
    { type: 'submit', className: 'knopf knopf-haupt', textContent: 'Anlegen' });
  los.setAttribute('form', form.id);
  abbrechen.addEventListener('click', () => d.schliessen(undefined));
  d.fuss.append(abbrechen, los);

  form.addEventListener('submit', async (ereignis) => {
    ereignis.preventDefault();
    const name = feld.value.trim();
    const laenge = [...name].length;
    if (!laenge) { meldung.textContent = 'Bitte einen Namen für die Kiste eingeben.'; feld.focus(); return; }
    if (laenge > NAME_MAX) {
      meldung.textContent = `Der Name ist zu lang (höchstens ${NAME_MAX} Zeichen).`;
      feld.focus();
      return;
    }
    const farbe = wahl.wert;
    los.disabled = true;
    meldung.textContent = '';
    try {
      const daten = { name, etikettfarbe: farbe };
      if (vorlage?.value) daten.vorlage = Number(vorlage.value);
      const antwort = await senden('/api/kisten', { daten });
      d.schliessen({ id: antwort?.id ?? null });
    } catch (fehler) {
      meldung.textContent = fehler.message;   // zum Beispiel: Eine Kiste „X“ gibt es schon.
      feld.focus();
      feld.select();
    } finally {
      los.disabled = false;
    }
  });
  feld.focus();
  return d.fertig;
}

const LUPE = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5"'
  + ' stroke-linecap="round" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5L20 20"/></svg>';
const KREUZ = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5"'
  + ' stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
const PLUS = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5"'
  + ' stroke-linecap="round" aria-hidden="true" focusable="false"><path d="M12 5v14M5 12h14"/></svg>';
const HAKEN = '<svg viewBox="0 0 12 12" width="9" height="9" fill="none" stroke="currentColor" stroke-width="1.8"'
  + ' stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.5 6.3l2.4 2.4 4.6-5"/></svg>';

// ---------- Regal legen ----------

/** Verfügbare Breite eines Fachs (ohne Innenrand), gemessen an einem leeren Probefach. */
function fachbreite(korpus) {
  const probe = document.createElement('ul');
  probe.className = 'regal-fach';
  probe.style.height = '0';
  korpus.append(probe);
  const stil = getComputedStyle(probe);
  const breite = probe.clientWidth - parseFloat(stil.paddingLeft) - parseFloat(stil.paddingRight);
  probe.remove();
  return breite;
}

/** Verteilt die Rücken auf Regalböden, setzt Buchstütze und Kerzenhalter und baut die Böden neu auf,
    sobald sich etwas geändert hat (Breite des Fensters). */
function legen() {
  if (!zustand) return;
  const { korpus, plaetze } = zustand;
  const skala = window.innerWidth >= 1100 ? 1.2 : window.innerWidth >= 700 ? 1.1 : 1;
  const innen = fachbreite(korpus);
  if (innen <= 0) return;
  for (const p of plaetze) p.breite = Math.round(p.grund * skala);

  const reihen = [{ plaetze: [], benutzt: 0 }];
  for (const p of plaetze) {
    const reihe = reihen[reihen.length - 1];
    const noetig = (reihe.plaetze.length ? ABSTAND : 0) + p.breite;
    if (reihe.plaetze.length && reihe.benutzt + noetig > innen) reihen.push({ plaetze: [p], benutzt: p.breite });
    else { reihe.plaetze.push(p); reihe.benutzt += noetig; }
  }
  // ein freies Fach darunter; steht darunter der Kistenboden, übernimmt dieser die Rolle des zweiten Fachs
  if (reihen.length < 2 && !zustand.kistenboden) reihen.push({ plaetze: [], benutzt: 0 });

  const schluessel = JSON.stringify([skala, reihen.map((r) => r.plaetze.map((p) => p.id))]);
  for (const p of plaetze) p.knopf.style.setProperty('--breite', `${p.breite}px`);
  if (schluessel !== zustand.schluessel) {
    zustand.schluessel = schluessel;
    const boeden = reihen.map((reihe, i) => {
      const boden = document.createElement('div');
      boden.className = 'regal-boden';
      const fach = document.createElement('ul');
      fach.className = 'regal-fach';
      fach.setAttribute('role', 'list');
      if (!reihe.plaetze.length) fach.classList.add('regal-fach--frei');   // freies Fach: niedriger
      fach.append(...reihe.plaetze.map((p) => p.li));
      if (!plaetze.length && i === 0) {
        fach.append(elementAus('<li class="regal-leer-hinweis">Noch keine Binder im Regal.</li>'));
      }
      boden.append(fach, Object.assign(document.createElement('div'), { className: 'regal-brett' }));
      boden.lastChild.setAttribute('aria-hidden', 'true');
      return boden;
    });
    korpus.replaceChildren(...boeden);
  }
  titelAnpassen();
}

/** Verkleinert Titel, die senkrecht nicht ins Feld passen, bis sie passen (mindestens 11 px). */
function titelAnpassen() {
  if (!zustand) return;
  const bereich = document.createRange();
  for (const t of zustand.szene.querySelectorAll('.regal-titel')) {
    if (!t.clientHeight) continue;   // Regal gerade verborgen
    t.style.fontSize = '';
    let px = parseFloat(getComputedStyle(t).fontSize);
    bereich.selectNodeContents(t);
    // Länge des Textes (senkrecht) messen; scrollHeight erfasst den Auslass durch „…" nicht zuverlässig
    while (Math.ceil(bereich.getBoundingClientRect().height) > t.clientHeight - 2 && px > 11) {
      px -= 0.5;
      t.style.fontSize = `${px}px`;
    }
  }
}

// ---------- Suche (Karteikasten) ----------

function markierungsText(t) {
  if (t.markierung === 'unsicher') return t.geprueft ? 'Erkennung unsicher – von dir geprüft' : 'Erkennung unsicher';
  if (t.markierung === 'druck_offen') return t.geprueft ? 'Druck unsicher – von dir geprüft' : 'Name sicher, Druck offen';
  return '';
}

function zeichenHtml(t) {
  // Abzeichen an der oberen rechten Ecke: gefüllt = offen, Ring mit Häkchen = von dir geprüft
  const ring = t.geprueft ? ' regal-abzeichen--geprueft' : '';
  if (t.markierung === 'unsicher') {
    return `<span class="regal-abzeichen regal-siegel${ring ? ' regal-siegel--geprueft' : ''}${ring}" aria-hidden="true">${t.geprueft ? HAKEN : '?'}</span>`;
  }
  if (t.markierung === 'druck_offen') {
    return `<span class="regal-abzeichen regal-lesezeichen${ring ? ' regal-lesezeichen--geprueft' : ''}${ring}" aria-hidden="true">${t.geprueft ? HAKEN : 'i'}</span>`;
  }
  return '';
}

function vorschauHtml(t) {
  const quellen = [];
  if (t.bild) quellen.push(['scryfall', t.bild]);
  if (t.ausschnitt) quellen.push(['foto', t.ausschnitt]);
  const hinweis = markierungsText(t);
  const titel = hinweis ? ` title="${esc(hinweis)}"` : '';
  if (!quellen.length) {
    return `<span class="regal-vorschau regal-vorschau--leer"${titel}>${zeichenHtml(t)}</span>`;
  }
  const [art, schluessel] = quellen[0];
  const rueckfall = quellen[1] ? bildUrl(quellen[1][0], quellen[1][1], 200) : '';
  return `<span class="regal-vorschau"${titel}><img alt="" width="63" height="88" loading="lazy" decoding="async"`
    + ` src="${esc(bildUrl(art, schluessel, 200))}" srcset="${esc(bildSrcset(art, schluessel))}" sizes="60px"`
    + ` data-rueckfall="${esc(rueckfall)}">${zeichenHtml(t)}</span>`;
}

function karteikarteHtml(t) {
  const daten = [t.set && String(t.set).toUpperCase(), t.nummer, t.sprache && String(t.sprache).toUpperCase()]
    .filter(Boolean).join(' · ');
  const lage = [t.seite != null && `Seite ${t.seite}`, t.zeile != null && `Zeile ${t.zeile}, Spalte ${t.spalte}`]
    .filter(Boolean).join(' · ');
  const hinweis = markierungsText(t);
  const ziel = `#/binder/${t.binder_id}/${t.seite ?? 1}/karte/${t.id}`;
  return `<li><a class="regal-karteikarte${t.geprueft ? ' regal-karte--geprueft' : ''}" href="${ziel}">`
    + vorschauHtml(t)
    + '<span class="regal-karte-text">'
    + `<span class="regal-karte-name">${esc(t.name)}</span>`
    + (daten ? `<span class="regal-karte-daten">${esc(daten)}</span>` : '')
    + `<span class="regal-karte-ort">${esc(t.binder_name)}</span>`
    + (lage ? `<span class="regal-karte-lage">${esc(lage)}</span>` : '')
    + (hinweis ? `<span class="regal-karte-hinweis">${esc(hinweis)}</span>` : '')
    + '</span></a></li>';
}

/** Zeigt Treffer (oder eine Meldung) anstelle des Regals; ohne Suchtext wieder das Regal. */
function trefferZeigen(inhaltHtml, ansage) {
  const { treffer, moebel, status } = zustand;
  if (inhaltHtml === null) {
    treffer.hidden = true;
    treffer.replaceChildren();
    moebel.hidden = false;
    status.textContent = '';
    legen();                       // das Regal war verborgen und konnte nicht gemessen werden
    return;
  }
  treffer.innerHTML = inhaltHtml;
  treffer.hidden = false;
  moebel.hidden = true;
  status.textContent = ansage;
}

async function suchen() {
  if (!zustand) return;
  const text = zustand.eingabe.value.trim();
  const meine = ++zustand.suchlauf;
  if (text.length < 2) { trefferZeigen(null); return; }
  let antwort;
  try {
    antwort = await holen(`/api/suche?q=${encodeURIComponent(text)}`);
  } catch (fehler) {
    console.error(fehler);
    if (zustand && meine === zustand.suchlauf) {
      trefferZeigen('<p class="regal-treffer-kopf">Die Suche ist gerade nicht erreichbar.</p>',
        'Die Suche ist gerade nicht erreichbar.');
    }
    return;
  }
  if (!zustand || meine !== zustand.suchlauf) return;   // inzwischen neu getippt
  const liste = antwort.treffer ?? [];
  if (!liste.length) {
    trefferZeigen(`<p class="regal-treffer-kopf">Keine Karte unter „${esc(text)}“ gefunden.</p>`, 'Keine Treffer');
    return;
  }
  const kopf = `${mehrzahl(liste.length, 'Treffer', 'Treffer')} für „${text}“`;
  trefferZeigen(
    `<p class="regal-treffer-kopf">${esc(kopf)}</p>`
    + `<ul class="regal-karteikarten">${liste.map(karteikarteHtml).join('')}</ul>`
    + (liste.length >= 50 ? '<p class="regal-treffer-hinweis">Es werden die ersten 50 Treffer gezeigt.</p>' : ''),
    kopf);
}

function leeren() {
  const { eingabe, leerknopf } = zustand;
  eingabe.value = '';
  letzteSuche = '';
  leerknopf.hidden = true;
  clearTimeout(zustand.uhr);
  suchen();
}

// ---------- Erkennung und Prüfen (Statuszeile im Kopf) ----------

/** Zeichnet die Statuszeile neu, aber nur, wenn sich etwas geändert hat (kein unnötiges Vorlesen). */
function statusZeichnen(z) {
  const { erkennung, pille } = z;
  const schluesselE = String(z.erkennungAktiv);
  if (z.gezeichnetE !== schluesselE) {
    z.gezeichnetE = schluesselE;
    erkennung.hidden = z.erkennungAktiv === null;
    erkennung.classList.toggle('regal-erkennung--aktiv', z.erkennungAktiv === true);
    if (z.erkennungAktiv === true) {
      erkennung.removeAttribute('title');
      erkennung.innerHTML = '<span class="regal-punkt" aria-hidden="true"></span><span>Erkennung aktiv</span>';
    } else if (z.erkennungAktiv === false) {
      const tipp = sitzung().ha ? 'am PC die Erkennung (Symbol neben der Uhr) starten' : 'am PC Starten.bat doppelklicken';
      erkennung.title = tipp;
      erkennung.innerHTML = '<span class="regal-punkt" aria-hidden="true"></span><span>Erkennung nicht aktiv</span>'
        + `<span class="nur-vorleser">. ${tipp[0].toUpperCase()}${tipp.slice(1)}.</span>`;
    }
  }
  const n = z.pruefenAnzahl;
  if (z.gezeichnetP !== n) {
    z.gezeichnetP = n;
    pille.hidden = !(n > 0);
    if (n > 0) {
      pille.innerHTML = `Prüfen · <span class="regal-pruefen-zahl">${esc(zahl(n))}</span>`;
      pille.setAttribute('aria-label',
        `Prüfen öffnen: ${zahl(n)} ${n === 1 ? 'Erkennung wartet' : 'Erkennungen warten'} auf deine Prüfung`);
    }
  }
}

/** Holt Zustand der Erkennung und (nur bei Datenbankversion 3) die Länge der Prüf-Warteschlange. */
async function statusHolen(z) {
  if (z.statusLaeuft) return;
  z.statusLaeuft = true;
  try {
    const [verarbeiter, schlange] = await Promise.all([
      holen('/api/verarbeiter').catch(() => null),
      z.pruefenMoeglich ? holen('/api/pruefen/warteschlange').catch(() => null) : null,
    ]);
    if (zustand !== z) return;
    if (verarbeiter && typeof verarbeiter.aktiv === 'boolean') z.erkennungAktiv = verarbeiter.aktiv;
    if (z.pruefenMoeglich) z.pruefenAnzahl = Number.isFinite(schlange?.gesamt) ? schlange.gesamt : 0;
    statusZeichnen(z);
  } finally {
    z.statusLaeuft = false;
    z.statusZeit = Date.now();
  }
}

/** Nächste Abfrage in 30 s; im verborgenen Tab ruht sie, beim Zurückkehren holt `sichtbar` nach. */
function statusPlanen(z) {
  clearTimeout(z.statusUhr);
  if (document.hidden) return;
  z.statusUhr = setTimeout(async () => {
    await statusHolen(z);
    if (zustand === z) statusPlanen(z);
  }, STATUS_ABSTAND);
}

// ---------- Ansicht ----------

function aufraeumen() {
  if (!zustand) return;
  clearTimeout(zustand.uhr);
  clearTimeout(zustand.statusUhr);
  zustand.beobachter?.disconnect();
  document.removeEventListener('keydown', zustand.taste);
  document.removeEventListener('visibilitychange', zustand.sichtbar);
  window.removeEventListener('binder:sitzung', zustand.sitzung);
  zustand.suchlauf += 1;
  zustand = null;
}

/** Regal neu laden (zum Beispiel nach einer neuen Kiste) und danach die Kiste mit der ID `kisteId` zeigen. */
function neuLaden(wurzel, szene, kisteId = null) {
  if (zustand?.szene !== szene) return;
  zeigen(wurzel)
    .then(() => {
      if (!zustand) return;
      const k = kisteId != null ? zustand.szene.querySelector(`.regal-kiste[data-kiste="${Number(kisteId)}"]`) : null;
      if (k) {
        k.scrollIntoView({ block: 'center', behavior: bewegungReduziert() ? 'auto' : 'smooth' });
        k.focus({ preventScroll: true });
        if (!bewegungReduziert()) {         // die neue Kiste wird ins Regal gestellt
          k.classList.add('regal-zurueck');
          animationFertig(k, 'regal-kiste-hinein', 1200).then(() => k.classList.remove('regal-zurueck'));
        }
      } else {
        zustand.szene.querySelector('.regal-neu')?.focus();   // Fokus nicht verlieren
      }
    })
    .catch((fehler) => console.error(fehler));
}

export async function zeigen(wurzel, parameter, vorher) {
  aufraeumen();
  const kistenAnfrage = holen('/api/kisten').catch(() => null);   // ohne Kisten-Schnittstelle bleibt das Regal wie bisher
  // Version 2 der Datenbank (verfuegbar: false): kein Etikett „Sammlung“, die Route wird gar nicht erst gefragt
  const sammlungAnfrage = kistenAnfrage.then((k) => (k?.verfuegbar === true ? holen('/api/sammlung').catch(() => null) : null));
  const scanAnfrage = holen('/api/scan').catch(() => null);   // 409/Fehler: Scan-Funktionen ausblenden
  const decksAnfrage = holen('/api/decks').catch(() => null);   // alte Datenbank: kein Deckboden
  const [{ binder }, , kistenAntwort, sammlung, scanStand, decksAntwort] = await Promise.all([
    holen('/api/regal'), laden(), kistenAnfrage, sammlungAnfrage, scanAnfrage, decksAnfrage,
  ]);
  const decksDa = decksAntwort?.verfuegbar === true;
  const decks = decksDa && Array.isArray(decksAntwort.decks) ? decksAntwort.decks : [];
  const scanDa = Boolean(scanStand?.gesamt);
  const karten = binder.reduce((summe, b) => summe + (b.karten ?? 0), 0);
  // Version 2 der Datenbank: verfuegbar ist false, dann gibt es weder Kistenboden noch Zahlen dazu
  const kistenDa = kistenAntwort?.verfuegbar === true;
  const kisten = kistenDa && Array.isArray(kistenAntwort.kisten) ? kistenAntwort.kisten : [];
  const kistenKarten = kisten.reduce((summe, k) => summe + (k.karten ?? 0), 0);   // getrennt von den Binderkarten

  const szene = document.createElement('section');
  szene.className = 'regal-szene';
  szene.setAttribute('aria-label', 'Bibliothek');
  szene.innerHTML = '<header class="regal-kopf"><h1 class="regal-titel-haupt">Bibliothek</h1>'
    + '<p class="regal-zahlen">'
    + `<span class="regal-zahlen-teil">${esc(mehrzahl(binder.length, 'Binder', 'Binder'))} · ${esc(mehrzahl(karten, 'Karte', 'Karten'))}</span>`
    + (kisten.length
      ? `<span class="nur-vorleser">, </span><span class="regal-zahlen-teil">${esc(mehrzahl(kisten.length, 'Kiste', 'Kisten'))} · ${esc(mehrzahl(kistenKarten, 'Karte', 'Karten'))}</span>`
      : '')
    + '</p>'
    + '<div class="regal-status"><span class="regal-erkennung" hidden></span>'
    + '<button type="button" class="knopf regal-pruefen" hidden></button></div>'
    + '<div class="regal-aktionen"></div></header>'
    + '<form class="regal-kasten" role="search" aria-label="Karten in allen Bindern suchen" autocomplete="off">'
    + `<label class="nur-vorleser" for="regal-suche">Karte suchen</label><div class="regal-kasten-feld">${LUPE}`
    + '<input id="regal-suche" class="regal-kasten-eingabe" type="search" name="q" autocomplete="off"'
    + ' autocapitalize="off" spellcheck="false" enterkeyhint="search" aria-controls="regal-treffer"'
    + ' placeholder="Karte in allen Bindern suchen">'
    + `<button type="button" class="regal-kasten-leer" aria-label="Suche leeren" hidden>${KREUZ}</button>`
    + '</div></form>'
    + '<p class="nur-vorleser" role="status" aria-live="polite"></p>'
    + '<section class="regal-treffer" id="regal-treffer" aria-label="Treffer" hidden></section>'
    + '<div class="regal-moebel">' + sammlungHtml(sammlung) + setsHtml(sammlung) + scanPlatzHtml(scanStand)
    + '<div class="regal-korpus" role="group" aria-label="Binder im Regal"></div>'
    + (kistenDa ? kistenbodenHtml(kisten) : '')
    + (decksDa ? deckbodenHtml(decks) : '')
    + '</div>';
  wurzel.replaceChildren(szene);

  // Knopf „Proxys“ nur, wenn der Bereich da ist (im Add-on; am PC liefert die Schnittstelle 404)
  const proxyKnopf = szene.querySelector('.regal-proxy');
  if (proxyKnopf) {
    holen('/api/proxy').then(() => {
      proxyKnopf.hidden = false;
      if (vorher?.ansicht === 'proxy' && zustand?.szene === szene && letzteSuche.trim().length < 2) {
        proxyKnopf.focus({ preventScroll: true });
      }
    }).catch(() => { /* kein Proxy-Bereich */ });
  }

  // Kopf: „Neuer Binder" nur angemeldet; der Anmelde-Knopf steht immer da
  const neu = document.createElement('button');
  neu.type = 'button';
  neu.className = 'knopf regal-neu';
  neu.setAttribute('aria-label', 'Neuen Binder anlegen und Fotos hochladen');
  neu.innerHTML = `${PLUS}<span>Neuer Binder</span>`;
  neu.hidden = !angemeldet();
  neu.addEventListener('click', async () => {
    const { oeffnen } = await import('./hochladen.js');  // erst bei Bedarf laden: Regal startet schneller
    const r = await oeffnen({ neu: true });
    if (r?.binderId) { gehe(`#/binder/${r.binderId}/${r.seiten?.[0]?.nummer ?? 1}`); return; }
    // Es könnte trotzdem ein Binder entstanden sein: Regal neu holen (nur wenn es noch gezeigt wird)
    neuLaden(wurzel, szene);
  });

  // „Neue Kiste" nur angemeldet und nur, wenn die Datenbank Kisten kennt (Version 3)
  const neueKiste = document.createElement('button');
  neueKiste.type = 'button';
  neueKiste.className = 'knopf regal-neue-kiste';
  neueKiste.setAttribute('aria-label', 'Neue Kiste anlegen');
  neueKiste.innerHTML = `${PLUS}<span>Neue Kiste</span>`;
  neueKiste.hidden = !(kistenDa && angemeldet());
  neueKiste.addEventListener('click', async () => {
    const r = await neueKisteFragen(kisten);
    if (r) neuLaden(wurzel, szene, r.id);
  });
  // Add-on: „PC verbinden“ (Schlüssel für die Erkennung am PC); am PC selbst gibt es das nicht
  const pcKnopf = document.createElement('button');
  pcKnopf.type = 'button';
  pcKnopf.className = 'knopf regal-pc';
  pcKnopf.innerHTML = '<span>PC verbinden</span>';
  pcKnopf.hidden = !sitzung().ha;
  pcKnopf.addEventListener('click', async () => {
    if (pcKnopf.dataset.offen) return;   // Doppelklick: nur ein Dialog
    pcKnopf.dataset.offen = '1';
    try {
      const { oeffnen } = await import('./pcverbinden.js');
      await oeffnen();
    } finally {
      delete pcKnopf.dataset.offen;
    }
  });
  // Add-on: „Verwaltung“ (Sicherungen, Datenbank löschen); am PC gibt es das nicht
  const verwaltungKnopf = document.createElement('button');
  verwaltungKnopf.type = 'button';
  verwaltungKnopf.className = 'knopf regal-pc';
  verwaltungKnopf.innerHTML = '<span>Verwaltung</span>';
  verwaltungKnopf.hidden = !sitzung().ha;
  verwaltungKnopf.addEventListener('click', async () => {
    if (verwaltungKnopf.dataset.offen) return;   // Doppelklick: nur ein Dialog
    verwaltungKnopf.dataset.offen = '1';
    try {
      const { oeffnen } = await import('./verwaltung.js');
      await oeffnen();
    } finally {
      delete verwaltungKnopf.dataset.offen;
    }
  });
  const scannen = document.createElement('button');
  scannen.type = 'button';
  scannen.className = 'knopf regal-scannen';
  scannen.setAttribute('aria-label', 'Karten mit der Kamera scannen');
  scannen.innerHTML = '<span>Scannen</span>';
  scannen.hidden = !(scanDa && angemeldet());
  scannen.addEventListener('click', () => gehe('#/scan'));
  szene.querySelector('.regal-aktionen').append(neu, neueKiste, scannen, pcKnopf, verwaltungKnopf, anmeldeKnopf());

  const plaetze = binder.map((b) => {
    const li = elementAus(rueckenHtml(b));
    return { id: b.id, li, knopf: li.querySelector('button'), grund: grundbreite(b), breite: 0 };
  });
  zustand = {
    szene, plaetze, schluessel: '', suchlauf: 0, uhr: 0, beobachter: null,
    korpus: szene.querySelector('.regal-korpus'),
    moebel: szene.querySelector('.regal-moebel'),
    treffer: szene.querySelector('.regal-treffer'),
    status: szene.querySelector('[role="status"]'),
    eingabe: szene.querySelector('.regal-kasten-eingabe'),
    leerknopf: szene.querySelector('.regal-kasten-leer'),
    taste: null,
    kistenboden: kistenDa,
    // Statuszeile: Erkennung (true/false/null = unbekannt) und Zahl der zu prüfenden Fälle
    erkennung: szene.querySelector('.regal-erkennung'),
    pille: szene.querySelector('.regal-pruefen'),
    erkennungAktiv: null, pruefenAnzahl: 0, pruefenMoeglich: kistenDa,
    gezeichnetE: undefined, gezeichnetP: undefined,
    statusUhr: 0, statusLaeuft: false, statusZeit: 0, sichtbar: null,
    sitzung: () => {
      neu.hidden = !angemeldet(); neueKiste.hidden = !(kistenDa && angemeldet()); pcKnopf.hidden = !sitzung().ha;
      verwaltungKnopf.hidden = !sitzung().ha;
      const sl = szene.querySelector('.regal-secretlair');
      if (sl) sl.hidden = !sitzung().ha;   // Secret Lair gibt es nur im Add-on
      scannen.hidden = !(scanDa && angemeldet());
      if (neuesDeck) neuesDeck.hidden = !angemeldet();
    },
  };
  // „Neues Deck“ nur mit Schreibrecht; der Dialog (deck.js) wird erst bei Bedarf geladen
  const neuesDeck = szene.querySelector('.regal-neues-deck');
  if (neuesDeck) {
    neuesDeck.hidden = !angemeldet();
    neuesDeck.addEventListener('click', async () => {
      if (neuesDeck.dataset.offen) return;
      neuesDeck.dataset.offen = '1';
      try {
        const { neuesDeckFragen } = await import('./deck.js');
        const id = await neuesDeckFragen();
        if (id != null) gehe(`#/deck/${id}`); else neuLaden(wurzel, szene);
      } finally {
        delete neuesDeck.dataset.offen;
      }
    });
  }
  // Fehlendes Commander-Bild: direkt von Scryfall, sonst schlichte Fläche
  moebelBilderAbsichern(zustand.moebel);
  const { moebel, treffer, eingabe, leerknopf } = zustand;
  window.addEventListener('binder:sitzung', zustand.sitzung);

  // Binder oder Kiste herausnehmen: die Animation übernimmt `verlassen`, die Navigation geschieht hier
  moebel.addEventListener('click', (ereignis) => {
    if (ereignis.target.closest('.scan-platz')) { gehe('#/scan/eingang'); return; }
    if (ereignis.target.closest('.regal-regeln')) { gehe('#/regeln'); return; }
    if (ereignis.target.closest('.regal-sets')) { gehe('#/sets'); return; }
    if (ereignis.target.closest('.regal-dashboard')) { gehe('#/dashboard'); return; }
    if (ereignis.target.closest('.regal-kaufliste')) { gehe('#/kaufliste'); return; }
    if (ereignis.target.closest('.regal-secretlair')) { gehe('#/secretlair'); return; }
    if (ereignis.target.closest('.regal-proxy')) { gehe('#/proxy'); return; }
    if (ereignis.target.closest('.regal-sammlung')) { gehe('#/sammlung'); return; }
    const knopf = ereignis.target.closest('.regal-ruecken');
    if (knopf) { gehe(`#/binder/${knopf.dataset.binder}`); return; }
    const kiste = ereignis.target.closest('.regal-kiste');
    if (kiste) { gehe(`#/kiste/${kiste.dataset.kiste}`); return; }
    const deck = ereignis.target.closest('.regal-deck');
    if (deck) gehe(`#/deck/${deck.dataset.deck}`);
  });

  // Prüfen-Pille: zur Prüfansicht
  zustand.pille.addEventListener('click', () => gehe('#/pruefen'));

  // Karteikarten: über die App navigieren (neuer Verlaufseintrag), Strg/Cmd-Klick bleibt dem Browser
  treffer.addEventListener('click', (ereignis) => {
    const link = ereignis.target.closest('a.regal-karteikarte');
    if (!link || ereignis.button !== 0 || ereignis.ctrlKey || ereignis.metaKey || ereignis.shiftKey) return;
    ereignis.preventDefault();
    gehe(link.getAttribute('href'));
  });
  // Fehlendes Vorschaubild: erst das eigene Foto, dann eine schlichte Kartenfläche
  treffer.addEventListener('error', (ereignis) => {
    const bild = ereignis.target;
    if (bild.tagName !== 'IMG') return;
    const rueckfall = bild.dataset.rueckfall;
    if (rueckfall) {
      bild.dataset.rueckfall = '';
      bild.removeAttribute('srcset');
      bild.src = rueckfall;
    } else {
      bild.closest('.regal-vorschau')?.classList.add('regal-vorschau--leer');
    }
  }, true);

  // Ereignisse einer veralteten Ansicht (während eines Neuaufbaus) werden ignoriert
  const diese = zustand;
  eingabe.addEventListener('input', () => {
    if (zustand !== diese) return;
    letzteSuche = eingabe.value;
    leerknopf.hidden = eingabe.value === '';
    clearTimeout(diese.uhr);
    diese.uhr = setTimeout(suchen, 250);
  });
  szene.querySelector('.regal-kasten').addEventListener('submit', (ereignis) => {
    ereignis.preventDefault();
    if (zustand !== diese) return;
    clearTimeout(diese.uhr);
    suchen();
    eingabe.blur();                // schließt am Handy die Tastatur
  });
  leerknopf.addEventListener('click', () => {
    if (zustand !== diese) return;
    leeren();
    eingabe.focus();
  });
  zustand.taste = (ereignis) => {
    if (ereignis.key !== 'Escape' || !zustand) return;
    if (dialogOffen()) return;           // Esc gehört dem offenen Dialog
    if (zustand.eingabe.value === '' && zustand.treffer.hidden) return;
    ereignis.preventDefault();
    leeren();
    zustand.eingabe.focus();
  };
  document.addEventListener('keydown', zustand.taste);

  legen();
  if (typeof ResizeObserver === 'function') {
    zustand.beobachter = new ResizeObserver(() => legen());
    zustand.beobachter.observe(moebel);
  }
  document.fonts?.ready.then(() => titelAnpassen());

  // Erkennung und Prüfen-Zähler: einmal jetzt, dann alle 30 s; im verborgenen Tab ruht die Abfrage,
  // beim Zurückkehren wird einmal aktualisiert (nicht öfter als alle 2 s)
  const z = zustand;
  z.sichtbar = () => {
    if (zustand !== z) return;
    if (document.hidden) { clearTimeout(z.statusUhr); return; }
    if (Date.now() - z.statusZeit < STATUS_MINDEST) { statusPlanen(z); return; }
    statusHolen(z).then(() => { if (zustand === z) statusPlanen(z); });
  };
  document.addEventListener('visibilitychange', z.sichtbar);
  statusHolen(z).then(() => { if (zustand === z) statusPlanen(z); });

  // Zurück aus dem Lesetisch oder der Kistenansicht: Rücken bzw. Kiste gleiten an ihren Platz
  // (nicht bei wiederhergestellter Suche)
  const sucheDa = letzteSuche.trim().length >= 2;
  if (vorher?.ansicht === 'deck' && !sucheDa) {
    zustand.szene.querySelector(`.regal-deck[data-deck="${Number(vorher.deck)}"]`)?.focus({ preventScroll: true });
  } else if ((vorher?.ansicht === 'sets' || vorher?.ansicht === 'set') && !sucheDa) {
    zustand.szene.querySelector('.regal-sets')?.focus({ preventScroll: true });   // zurück aus den Sets
  } else if (vorher?.ansicht === 'kaufliste' && !sucheDa) {
    zustand.szene.querySelector('.regal-kaufliste')?.focus({ preventScroll: true });
  } else if (vorher?.ansicht === 'dashboard' && !sucheDa) {
    zustand.szene.querySelector('.regal-dashboard')?.focus({ preventScroll: true });
  } else if (vorher?.ansicht === 'secretlair' && !sucheDa) {
    zustand.szene.querySelector('.regal-secretlair')?.focus({ preventScroll: true });
  } else if (vorher?.ansicht === 'proxy' && !sucheDa) {
    zustand.szene.querySelector('.regal-proxy')?.focus({ preventScroll: true });
  } else if (vorher?.sammlung && !sucheDa) {
    zustand.szene.querySelector('.regal-sammlung')?.focus({ preventScroll: true });   // zurück aus der Sammlung
  } else if ((vorher?.ansicht === 'lesetisch' || vorher?.ansicht === 'kiste') && !sucheDa) {
    const istKiste = vorher.ansicht === 'kiste';
    const knopf = istKiste
      ? zustand.szene.querySelector(`.regal-kiste[data-kiste="${Number(vorher.kiste)}"]`)
      : zustand.plaetze.find((p) => p.id === vorher.binder)?.knopf;
    if (knopf) {
      const r = knopf.getBoundingClientRect();
      if (r.top < 0 || r.bottom > window.innerHeight) knopf.scrollIntoView({ block: 'center' });
      knopf.focus({ preventScroll: true });
      if (!bewegungReduziert()) {
        knopf.classList.add('regal-zurueck');
        animationFertig(knopf, istKiste ? 'regal-kiste-hinein' : 'regal-hinein', 1200)
          .then(() => knopf.classList.remove('regal-zurueck'));
      }
    }
  }

  // Frühere Suche wiederherstellen (z. B. nach dem Zurück aus einem Treffer)
  if (sucheDa) {
    eingabe.value = letzteSuche;
    leerknopf.hidden = false;
    await suchen();
  } else {
    letzteSuche = '';
  }
}

/** Abschied: Wird ein Binder geöffnet, gleitet sein Rücken nach vorn aus dem Regal; bei einer Kiste
    zieht sie sich nach vorn. Bei „Bewegung reduzieren" und bei allen anderen Zielen sofort. */
export async function verlassen(naechste) {
  const aktuell = zustand;
  if (!aktuell) return;
  if (!bewegungReduziert()) {
    let knopf = null;
    let name = 'regal-heraus';
    if (naechste?.ansicht === 'lesetisch') {
      knopf = aktuell.plaetze.find((p) => p.id === naechste.binder)?.knopf;
    } else if (naechste?.ansicht === 'kiste') {
      knopf = aktuell.szene.querySelector(`.regal-kiste[data-kiste="${Number(naechste.kiste)}"]`);
      name = 'regal-kiste-heraus';
    }
    if (knopf && knopf.offsetParent !== null) {
      aktuell.szene.classList.add('regal-geht');
      knopf.classList.add('regal-heraus');
      await animationFertig(knopf, name, 900);
    }
  }
  if (zustand === aktuell) aufraeumen();
}
