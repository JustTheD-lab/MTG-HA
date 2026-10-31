// Kartenansicht: die Karte groß über dem Binder, daneben (PC) bzw. darunter (Handy) eine dunkle Tafel
// mit Daten, Erkennung und Änderungshistorie. Umschalter „Mein Foto / Scryfall-Bild".
//
//   oeffnen(kartenId)  zeigt die Karte. Ist dieselbe Karte schon offen, geschieht nichts.
//   schliessen()       schließt die Karte (Promise). Ohne offene Karte geschieht nichts.
//
// „×", Esc und Tippen auf den Hintergrund rufen zurueck(basis) auf. Der Wegweiser (app.js) ruft dann
// schliessen() auf. Gezeichnet wird in #ebene-karte; animiert werden nur transform und opacity.

import { holen, bildUrl, bildSrcset, BREITEN, esc, zurueck, bewegungReduziert, vorliebe, scryfallBild, euro }
  from './gemeinsam.js';
import { dialogOffen } from './dialog.js';
import { angemeldet, anmelden } from './anmeldung.js';
import { bearbeiten } from './pruefen.js';  // gegenseitiger Import: wird erst beim Antippen gebraucht

const ebene = document.getElementById('ebene-karte');
const buehne = document.getElementById('buehne');

/** Solange eine Karte offen ist: { id, overlay, zuvorFokus, aufraeumen }. */
let offen = null;

/** Sprachen, die die Prüfansicht setzen kann (Reihenfolge wie im Auswahlfeld). */
export const SPRACHEN = {
  de: 'Deutsch', en: 'Englisch', fr: 'Französisch', it: 'Italienisch', es: 'Spanisch', pt: 'Portugiesisch',
  ja: 'Japanisch', ko: 'Koreanisch', ru: 'Russisch', zhs: 'Chinesisch (vereinfacht)', zht: 'Chinesisch (traditionell)',
  la: 'Latein', he: 'Hebräisch', ar: 'Arabisch', grc: 'Altgriechisch', ph: 'Phyrexianisch',
};
export const sprachName = (code) => SPRACHEN[code] ?? (code ? String(code).toUpperCase() : '–');
/** Seltenheit wie bei Scryfall, auf Deutsch. */
export const SELTENHEIT = {
  common: 'Gewöhnlich', uncommon: 'Ungewöhnlich', rare: 'Selten', mythic: 'Mythisch selten',
  special: 'Besonders', bonus: 'Bonus',
};
const HAUPTTYPEN = {
  kreatur: 'Kreatur', planeswalker: 'Planeswalker', spontanzauber: 'Spontanzauber', hexerei: 'Hexerei',
  artefakt: 'Artefakt', verzauberung: 'Verzauberung', schlacht: 'Schlacht', land: 'Land', sonstige: 'Sonstiges',
};
const FARBEN = { W: 'Weiß', U: 'Blau', B: 'Schwarz', R: 'Rot', G: 'Grün' };
const QUELLEN = { erkennung: 'Erkennung', pruefliste: 'Prüfliste', zuordnung: 'Zuordnung', nutzer: 'Nutzer', manabox: 'ManaBox-Import' };
const AKTIONEN = { angelegt: 'angelegt', geaendert: 'geändert', geloescht: 'gelöscht' };
const ERKENNUNG = { sicher: 'sicher', name_sicher: 'Name sicher, Druck offen', unsicher: 'unsicher' };
const FELDER = {
  name: 'Name', set: 'Set', set_code: 'Set', nummer: 'Nummer', sprache: 'Sprache', foil: 'Foil',
  anzahl: 'Anzahl', status: 'Erkennung', konfidenz: 'Konfidenz', gruende: 'Gründe',
  geprueft: 'Von dir geprüft', bild: 'Scryfall-Bild', ausschnitt: 'Foto-Ausschnitt',
};
const HAKEN = '<svg class="karte-haken" viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.5l3 3 6-6.5"/></svg>';
const KREUZ = '<svg class="karte-kreuz" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';

// ---------- Texte und Werte ----------

/** Feldwert für Menschen: Ja/Nein, Erkennung, Prozent, ausgeschriebene Sprache. */
function wert(feld, v) {
  if (v === null || v === undefined || v === '') return '–';
  if (feld === 'foil' || feld === 'geprueft') return v ? 'ja' : 'nein';
  if (feld === 'status') return ERKENNUNG[v] ?? String(v);
  if (feld === 'konfidenz') return prozent(v);
  if (feld === 'sprache') return SPRACHEN[v] ?? String(v);
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

/** Die Konfidenz liegt als Anteil von 0 bis 1 vor und wird in Prozent gezeigt. */
function prozent(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return 'keine Angabe';
  return `${Math.round((n <= 1 ? n * 100 : n))} %`;
}

/** „2026-10-26T14:05:00" wird zu „26.10.2026, 14:05 Uhr". */
function datum(zeit) {
  const m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(zeit ?? '');
  return m ? `${m[3]}.${m[2]}.${m[1]}, ${m[4]}:${m[5]} Uhr` : String(zeit ?? '');
}

/** Manakosten als lesbarer Text ohne Symbolgrafiken: „{2}{U}{U}" wird zu „2 + Blau + Blau". */
export function manakostenText(kosten) {
  const symbol = (s) => {
    if (/^\d+$/.test(s)) return s;
    const teile = s.split('/');
    const phyrexisch = teile.includes('P');
    const farben = teile.filter((t) => t !== 'P');
    const bekannt = farben.every((t) => Object.hasOwn(FARBEN, t) || /^\d+$/.test(t) || t === 'C');
    if (!bekannt || !farben.length) return { X: 'X', Y: 'Y', Z: 'Z', S: 'Schnee', E: 'Energie' }[s] ?? s;
    const text = farben.map((t) => (t === 'C' ? 'farblos' : FARBEN[t] ?? t)).join('/');
    return phyrexisch ? `${text} (oder 2 Leben)` : text;
  };
  return String(kosten ?? '').split(' // ').map((seite) => {
    const teile = [...seite.matchAll(/\{([^}]+)\}/g)].map((m) => symbol(m[1]));
    return teile.join(' + ');
  }).filter(Boolean).join(' // ');
}

/** Gründe stehen in der Datenbank als Liste (JSON) oder als Text. */
export function gruendeListe(g) {
  if (!g) return [];
  if (Array.isArray(g)) return g.map(String);
  const text = String(g).trim();
  if (text.startsWith('[')) {
    try {
      const liste = JSON.parse(text);
      if (Array.isArray(liste)) return liste.map(String);
    } catch { /* kein JSON: der Text wird so gezeigt */ }
  }
  // Reiner Text: mehrere Gründe sind mit „;" getrennt.
  return text.split(';').map((s) => s.trim()).filter(Boolean);
}

// ---------- Gründe in verständliche Sätze ----------

/** Quellen in den Gründen: Bezeichnung und Artikel (für den Satzanfang und das Satzende). */
const GRUND_QUELLEN = { bild: ['Bildvergleich', 'der'], name: ['Name', 'der'], fuss: ['Fußzeile', 'die'] };
/** Sprachkürzel aus den Gründen, ausgeschrieben (wie in „auf Deutsch"). */
const GRUND_SPRACHEN = {
  de: 'Deutsch', en: 'Englisch', fr: 'Französisch', it: 'Italienisch', es: 'Spanisch', pt: 'Portugiesisch',
  ja: 'Japanisch', ko: 'Koreanisch', ru: 'Russisch', zhs: 'Chinesisch', zht: 'Chinesisch',
};

/** Quellen einer Gruppe als Wortgruppe: „der Bildvergleich" bzw. „Name und Fußzeile". */
function quellenWort(gruppe, mitArtikel) {
  if (gruppe.length === 1) {
    const [nomen, artikel] = GRUND_QUELLEN[gruppe[0]];
    return mitArtikel ? `${artikel} ${nomen}` : nomen;
  }
  return gruppe.map((t) => GRUND_QUELLEN[t][0]).join(' und ');
}

function gross(s) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** „Widerspruch: bild gegen name+fuss" → „Der Bildvergleich sagt etwas anderes als Name und Fußzeile." */
function widerspruchSatz(m) {
  const teile = (s) => s.split('+').map((t) => t.trim());
  const links = teile(m[1]);
  const rechts = teile(m[2]);
  if (![...links, ...rechts].every((t) => Object.hasOwn(GRUND_QUELLEN, t))) return null;
  const verb = links.length === 1 ? 'sagt' : 'sagen';
  return `Widerspruch: ${gross(quellenWort(links, true))} ${verb} etwas anderes als ${quellenWort(rechts, true)}.`;
}

/** Bekannte Gründe mit Mustern. Ein Satz-Erzeuger gibt null zurück, wenn er doch nicht passt. */
const GRUND_MUSTER = [
  [/^Bild: Abstand zur nächsten Karte klein \([\d.]+\)$/,
    () => 'Im Bildvergleich sieht eine andere Karte fast genauso ähnlich aus.'],
  [/^Name: nicht gelesen$/, () => 'Der Kartenname ließ sich nicht lesen.'],
  [/^Name: Zuordnung schwach \(([\d.]+)\)$/,
    (m) => `Der gelesene Name passt nur ungefähr zu einer Karte (Übereinstimmung ${Math.round(Number(m[1]) * 100)} %).`],
  [/^Fuß: mit Namenshilfe gelesen, zählt nicht als Bestätigung$/,
    () => 'Die Fußzeile ließ sich nur mithilfe des Namens lesen und zählt deshalb nicht als eigene Bestätigung.'],
  [/^Bild: unklar, aber mit dem Text vereinbar$/,
    () => 'Der Bildvergleich war nicht eindeutig, passt aber zum gelesenen Text.'],
  [/^keine Quelle liefert eine Karte$/, () => 'Weder Bild noch Text haben eine Karte erkannt.'],
  [/^Widerspruch: (.+) gegen (.+)$/, widerspruchSatz],
  [/^nur eine Quelle$/, () => 'Nur eine Quelle hat die Karte erkannt; eine zweite Bestätigung fehlt.'],
  [/^Druck: Fußzeile und Bild zeigen verschiedene Illustrationen$/,
    () => 'Fußzeile und Bild deuten auf verschiedene Drucke (andere Illustration).'],
  [/^Druck: Bildabstand zwischen Drucken klein \([\d.]+\)$/,
    () => 'Mehrere Drucke dieser Karte sehen fast gleich aus; der genaue Druck ist nicht sicher.'],
  [/^Sprache: Druck in [„“"]([^„“"]+)[„“"] nicht im Scryfall-Katalog$/,
    (m) => `Diesen Druck führt Scryfall nicht auf ${GRUND_SPRACHEN[m[1]] ?? m[1].toUpperCase()}; `
      + 'gezeigt wird das Bild eines anderssprachigen Drucks.'],
  [/^Sprache nicht gelesen$/, () => 'Die Sprache der Karte ließ sich nicht lesen.'],
  [/^Kartenfinder: Tasche unsicher$/, () => 'Die Tasche selbst war auf dem Foto schwer zu erkennen.'],
];

/** Übersetzt einen Grund aus der Datenbank in einen verständlichen deutschen Satz. Unbekannte bleiben unverändert. */
export function grundText(roh) {
  const text = String(roh ?? '').trim();
  for (const [muster, satz] of GRUND_MUSTER) {
    const m = muster.exec(text);
    if (!m) continue;
    const ergebnis = satz(m);
    if (ergebnis) return ergebnis;
  }
  return text;
}

/** Erkennung als Text fürs Etikett, dazu „von dir geprüft" wenn bestätigt. */
function erkennung(k) {
  const text = ERKENNUNG[k.status] ?? 'unbekannt';
  return k.geprueft ? `${text} · von dir geprüft` : text;
}

/** Änderungen eines Historieneintrags als Zeilen „Feld: alt → neu". */
function aenderungenVon(h) {
  const alt = h.alt && typeof h.alt === 'object' ? h.alt : null;
  const neu = h.neu && typeof h.neu === 'object' ? h.neu : null;
  const namen = [...new Set([...Object.keys(alt ?? {}), ...Object.keys(neu ?? {})])];
  return namen
    .filter((f) => Object.hasOwn(FELDER, f) && JSON.stringify(alt?.[f]) !== JSON.stringify(neu?.[f]))
    .map((f) => {
      const nachher = wert(f, neu?.[f]);
      return alt ? `${FELDER[f]}: ${wert(f, alt[f])} → ${nachher}` : `${FELDER[f]}: ${nachher}`;
    });
}

// ---------- Aufbau ----------

/** Abzeichen an der oberen rechten Kartenecke: „?" (Erkennung unsicher) bzw. „i" (Druck offen).
    Von dir geprüft: nur der Ring mit einem Häkchen. */
function kennzeichen(k) {
  const gp = Boolean(k.geprueft);
  const geprueft = gp ? ' karte-abzeichen-geprueft' : '';
  if (k.markierung === 'unsicher') {
    const text = gp ? 'Erkennung unsicher – von dir geprüft' : 'Erkennung unsicher';
    return `<span class="karte-abzeichen karte-abzeichen-warnung${geprueft}" role="img" aria-label="${esc(text)}" title="${esc(text)}">${gp ? HAKEN : '?'}</span>`;
  }
  if (k.markierung === 'druck_offen') {
    const text = gp ? 'Druck unsicher – von dir geprüft' : 'Name sicher, Druck offen';
    return `<span class="karte-abzeichen karte-abzeichen-hinweis${geprueft}" role="img" aria-label="${esc(text)}" title="${esc(text)}">${gp ? HAKEN : 'i'}</span>`;
  }
  return '';
}

/** Ein Feld: Beschriftung (.etikett) über dem Wert. */
function feldHtml(name, inhalt, breit = false) {
  return `<div class="karte-wert${breit ? ' karte-breit' : ''}"><dt class="etikett">${name}</dt><dd>${inhalt}</dd></div>`;
}

/** Kistenkarten kommen aus dem ManaBox-Import und haben Vorschaubilder nur in 200 und 400 Pixel. */
const istKistenkarte = (k) => k.kiste_id != null;
const importiert = (k) => k.quelle === 'manabox';

function zahlText(n) {
  return String(n).replace('.', ',');
}

/** Kartendaten aus dem Katalog (Manawert, Manakosten, Typ, Seltenheit, Farbe); nur, was da ist. */
function kartendatenHtml(k) {
  const teile = [];
  if (k.manawert != null) teile.push(feldHtml('Manawert', esc(zahlText(k.manawert))));
  if (k.seltenheit) teile.push(feldHtml('Seltenheit', esc(SELTENHEIT[k.seltenheit] ?? k.seltenheit)));
  const kosten = k.manakosten ? manakostenText(k.manakosten) : '';
  if (kosten) teile.push(feldHtml('Manakosten', `<span title="${esc(k.manakosten)}">${esc(kosten)}</span>`, true));
  if (k.typzeile || k.haupttyp) {
    const typ = HAUPTTYPEN[k.haupttyp] ?? '';
    const zeile = k.typzeile ? `<span class="karte-neben">${esc(k.typzeile)}</span>` : '';
    teile.push(feldHtml('Typ', `${esc(typ)}${typ && zeile ? ' ' : ''}${zeile}`, true));
  }
  if (k.farben != null && k.manawert != null) {
    const farben = [...String(k.farben)].map((f) => FARBEN[f] ?? f).join(', ');
    teile.push(feldHtml('Farbe', esc(farben || 'farblos')));
  }
  return teile.join('');
}

function datenHtml(k) {
  const gruende = gruendeListe(k.gruende);
  let lage;
  if (istKistenkarte(k)) lage = `Kiste ${k.kiste_name ?? ''}`.trim();
  else if (k.binder_id == null) lage = 'nicht im Binder';
  else {
    lage = [k.binder_name, k.seite != null && `Seite ${k.seite}`, k.zeile != null && `Zeile ${k.zeile}`,
      k.spalte != null && `Spalte ${k.spalte}`].filter(Boolean).join(' · ');
  }
  return `<dl class="karte-daten">
      ${feldHtml('Foil', k.foil ? 'ja' : 'nein')}
      ${feldHtml('Anzahl', esc(k.anzahl ?? 1))}
      ${kartendatenHtml(k)}
      ${importiert(k) && !gruende.length ? '' : feldHtml('Erkennung', esc(erkennung(k)), true)}
      ${importiert(k) && k.konfidenz == null ? '' : feldHtml('Konfidenz', esc(k.konfidenz == null ? 'keine Angabe' : prozent(k.konfidenz)))}
      ${gruende.length ? feldHtml('Gründe', `<ul>${gruende.map((g) => `<li title="${esc(g)}">${esc(grundText(g))}</li>`).join('')}</ul>`, true) : ''}
      ${feldHtml('Lage', esc(lage), true)}
    </dl>`;
}

/** Prozent mit Vorzeichen und Richtung („+12,5 %“ grün, „−3,0 %“ rot); ohne Verlauf „–“. */
function veraenderungHtml(p) {
  if (p == null) return '<span class="preis-leer" title="wird noch gesammelt">–</span>';
  const text = `${p > 0 ? '+' : p < 0 ? '−' : '±'}${Math.abs(p).toLocaleString('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} %`;
  return `<span class="preis-${p > 0 ? 'auf' : p < 0 ? 'ab' : 'gleich'}">${text}</span>`;
}

/** Preise von Cardmarket: Low und Durchschnitt 7 Tage, dazu die Veränderung gegen Vortag, 3 und 30 Tage
    (nur aus echtem Verlauf; Entscheidung des Nutzers, 31.10.2026). Foil-Karten: Foil-Preise. */
function preiseHtml(k) {
  const p = k.preise;
  if (!p) return '';
  if (p.cardmarket_id == null) {
    return `<section class="karte-preise"><h3 class="etikett">Preise</h3>
      <p class="karte-neben">Für diesen Druck ist kein Cardmarket-Produkt bekannt.</p></section>`;
  }
  const v = p.veraenderung ?? { low: {}, avg7: {} };
  const zeile = (name, feld) => `<tr><th scope="row">${name}</th><td class="preis-betrag">${euro(p[feld])}</td>`
    + ['vortag', 'tage3', 'tage30'].map((t) => `<td>${veraenderungHtml(v[feld]?.[t])}</td>`).join('') + '</tr>';
  const stand = p.tag ? new Date(`${p.tag}T12:00:00`).toLocaleDateString('de-DE') : '';
  const fehltNoch = ['vortag', 'tage3', 'tage30'].some((t) => v.low?.[t] == null && v.avg7?.[t] == null);
  return `<section class="karte-preise" aria-labelledby="karte-preise-titel">
      <h3 class="etikett" id="karte-preise-titel">Preise${p.foil ? ' (Foil)' : ''}</h3>
      <table class="preis-tabelle">
        <thead><tr><th></th><th scope="col">Preis</th><th scope="col">Vortag</th><th scope="col">3 Tage</th><th scope="col">30 Tage</th></tr></thead>
        <tbody>${zeile('Low', 'low')}${zeile('Ø 7 Tage', 'avg7')}</tbody>
      </table>
      <p class="karte-neben">Cardmarket, Stand ${esc(stand)}${fehltNoch ? ' · Veränderungen erscheinen, sobald genug Tage gesammelt sind' : ''}</p>
    </section>`;
}

/** Dezente Herkunft der Karte, ganz unten auf der Tafel. */
function quelleHtml(k) {
  const text = { manabox: 'aus ManaBox', manuell: 'von Hand angelegt' }[k.quelle];
  return text ? `<p class="karte-quelle etikett">${text}</p>` : '';
}

/** Knopf „Prüfen" (unsichere, ungeprüfte Karte) bzw. „Bearbeiten"; nur angemeldet sichtbar. */
function aktionenHtml(k) {
  const pruefbar = k.status !== 'sicher' && !k.geprueft;
  return `<div class="karte-aktionen">
      <button type="button" class="knopf ${pruefbar ? 'knopf-haupt' : ''} karte-bearbeiten" data-titel="${pruefbar ? 'Karte prüfen' : 'Karte bearbeiten'}" hidden>${pruefbar ? 'Prüfen' : 'Bearbeiten'}</button>
      <button type="button" class="knopf karte-anmelden" hidden>Zum Bearbeiten anmelden</button>
    </div>`;
}

function historieHtml(liste) {
  const eintraege = liste.map((h) => {
    const aenderungen = aenderungenVon(h).map((z) => `<li>${esc(z)}</li>`).join('');
    return `<li><time datetime="${esc(h.zeitpunkt)}">${esc(datum(h.zeitpunkt))}</time>
        · ${esc(QUELLEN[h.quelle] ?? h.quelle)} · ${esc(AKTIONEN[h.aktion] ?? h.aktion)}
        ${aenderungen ? `<ul>${aenderungen}</ul>` : ''}</li>`;
  }).join('');
  return `<details class="karte-historie">
      <summary>Änderungshistorie (${liste.length})</summary>
      ${liste.length ? `<ol>${eintraege}</ol>` : '<p>Noch keine Änderungen.</p>'}
    </details>`;
}

function inhaltHtml(k) {
  const setKuerzel = k.set && String(k.set).toUpperCase();
  const druck = [k.set_name && setKuerzel ? `${k.set_name} (${setKuerzel})` : setKuerzel, k.nummer && `Nr. ${k.nummer}`,
    k.sprache && (SPRACHEN[k.sprache] ?? k.sprache)].filter(Boolean).join(' · ');
  const kiste = istKistenkarte(k);
  const mitFoto = Boolean(k.ausschnitt) && !kiste;
  return `
    <button type="button" class="knopf karte-schliessen" aria-label="Karte schließen">${KREUZ}</button>
    <div class="karte-spalte">
      <figure class="karte-karte">
        <div class="karte-bildrahmen"></div>
        ${kennzeichen(k)}
      </figure>
      ${mitFoto || !kiste ? `<div class="karte-schalter" role="group" aria-label="Kartenbild wählen">
        <button type="button" class="knopf karte-schalter-teil" data-bild="foto" aria-pressed="false"${mitFoto ? '' : ' disabled'}>Mein Foto</button>
        <button type="button" class="knopf karte-schalter-teil" data-bild="scryfall" aria-pressed="false"${k.bild ? '' : ' disabled'}>Scryfall-Bild</button>
      </div>` : ''}
    </div>
    <section class="karte-etikett" aria-labelledby="karte-titel">
      <h2 id="karte-titel" class="karte-name">${esc(k.name)}</h2>
      ${druck ? `<p class="karte-druck">${esc(druck)}</p>` : ''}
      ${aktionenHtml(k)}
      ${preiseHtml(k)}
      ${datenHtml(k)}
      ${historieHtml(k.historie ?? [])}
      ${quelleHtml(k)}
    </section>`;
}

function fehlerHtml(fehler) {
  const nichtDa = /: 404$/.test(String(fehler?.message ?? ''));
  const text = nichtDa ? 'Diese Karte gibt es nicht (mehr).' : 'Die Karte ließ sich nicht laden. Läuft der Server?';
  return `<button type="button" class="knopf karte-schliessen" aria-label="Karte schließen">${KREUZ}</button>
    <p class="hinweis">${esc(text)}</p>`;
}

function karteDom(k, fehler) {
  const overlay = document.createElement('div');
  overlay.className = 'karte-hintergrund';
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-modal', 'true');
  if (k) overlay.setAttribute('aria-labelledby', 'karte-titel');
  else overlay.setAttribute('aria-label', 'Karte');
  overlay.tabIndex = -1;
  const inhalt = document.createElement('div');
  inhalt.className = 'karte-inhalt';
  inhalt.innerHTML = k ? inhaltHtml(k) : fehlerHtml(fehler);
  overlay.append(inhalt);
  return overlay;
}

// ---------- Bilder ----------

function schluessel(art, k) {
  if (art === 'foto') return istKistenkarte(k) ? null : k.ausschnitt;
  return k.bild;
}

/** Markiert im Umschalter, welches Bild gerade gezeigt wird. */
function markieren(overlay, art) {
  overlay.querySelectorAll('.karte-schalter-teil').forEach((knopf) => {
    knopf.setAttribute('aria-pressed', String(knopf.dataset.bild === art));
  });
}

/** Schlichte Kartenfläche mit Namen, wenn kein Bild da ist oder geladen werden kann. */
function kartenflaeche(k) {
  const flaeche = document.createElement('div');
  flaeche.className = 'karte-flaeche';
  const set = [k.set && String(k.set).toUpperCase(), k.nummer].filter(Boolean).join(' ');
  flaeche.innerHTML = `<p class="karte-flaeche-name">${esc(k.name)}</p>${set ? `<p class="karte-flaeche-set">${esc(set)}</p>` : ''}`;
  return flaeche;
}

/** Zeigt das gewünschte Bild. Fehlt es oder lädt es nicht, folgt das andere, zuletzt die Kartenfläche. */
function bildZeigen(overlay, k, wahl, versucht = new Set()) {
  const rahmen = overlay.querySelector('.karte-bildrahmen');
  const andere = wahl === 'foto' ? 'scryfall' : 'foto';
  const art = [wahl, andere].find((a) => !versucht.has(a) && schluessel(a, k));
  rahmen.replaceChildren();
  markieren(overlay, art ?? null);
  if (!art) {
    rahmen.append(kartenflaeche(k));
    return;
  }
  const img = document.createElement('img');
  img.className = 'karte-bildinhalt';
  img.decoding = 'async';
  img.loading = 'lazy';
  img.sizes = '(min-width: 900px) 460px, 92vw';
  // Kistenkarten haben nur Vorschaubilder in 200 und 400 Pixel; andere Breiten gäbe es nicht.
  img.srcset = istKistenkarte(k)
    ? BREITEN.filter((b) => b <= 400).map((b) => `${bildUrl(art, schluessel(art, k), b)} ${b}w`).join(', ')
    : bildSrcset(art, schluessel(art, k));
  img.src = bildUrl(art, schluessel(art, k), 400);
  img.alt = art === 'foto' ? `Eigenes Foto von ${k.name}` : `Scryfall-Bild von ${k.name}`;
  img.addEventListener('error', () => {
    // Noch kein Vorschaubild (z. B. eben hinzugefügt): einmal direkt von Scryfall, bis der PC es nachliefert
    const direkt = art === 'scryfall' && !img.dataset.direkt ? scryfallBild(schluessel(art, k)) : null;
    if (direkt) {
      img.dataset.direkt = '1';
      img.removeAttribute('srcset');
      img.src = direkt;
      return;
    }
    versucht.add(art);
    bildZeigen(overlay, k, wahl, versucht);
  });
  rahmen.append(img);
}

// ---------- Ereignisse ----------

/** Adresse der Ansicht ohne /karte/ID: dorthin führt das Schließen. */
function basis() {
  return location.hash.replace(/\/karte\/\d+\/?$/, '');
}

/** Esc schließt, Tab bleibt innerhalb des Dialogs. */
function taste(e) {
  if (!offen || !offen.overlay) return;
  if (dialogOffen()) return;  // ein Dialog (zum Beispiel „Prüfen") steht über der Karte: Esc und Tab gehören ihm
  if (e.key === 'Escape') {
    e.preventDefault();
    zurueck(basis());
    return;
  }
  if (e.key !== 'Tab') return;
  const knoepfe = [...offen.overlay.querySelectorAll('button:not([disabled]), summary')];
  if (!knoepfe.length) return;
  const erster = knoepfe[0];
  const letzter = knoepfe[knoepfe.length - 1];
  const aktiv = document.activeElement;
  if (e.shiftKey && (aktiv === erster || aktiv === offen.overlay)) {
    e.preventDefault();
    letzter.focus();
  } else if (!e.shiftKey && aktiv === letzter) {
    e.preventDefault();
    erster.focus();
  }
}

/** Zeigt „Prüfen/Bearbeiten" nur angemeldet, sonst einen ruhigen Anmelde-Knopf. */
function aktionenZeigen(overlay) {
  const an = angemeldet();
  const bearbeitenKnopf = overlay.querySelector('.karte-bearbeiten');
  const anmeldenKnopf = overlay.querySelector('.karte-anmelden');
  if (bearbeitenKnopf) bearbeitenKnopf.hidden = !an;
  if (anmeldenKnopf) anmeldenKnopf.hidden = an;
}

/** Lädt die Karte neu und zeichnet sie ohne Animation (nach Änderungen im Dialog „Prüfen/Bearbeiten"). */
async function neuLaden(eintrag) {
  let k = null;
  try {
    k = await holen(`/api/karte/${eintrag.id}`);
  } catch (err) {
    if (/: 404$/.test(String(err?.message ?? ''))) { zurueck(basis()); return; }  // Karte gibt es nicht mehr
    console.error(err);
    return;
  }
  if (offen !== eintrag) return;
  const overlay = karteDom(k, null);
  const alt = eintrag.overlay;
  const hoehe = alt?.scrollTop ?? 0;
  eintrag.aufraeumen?.();
  eintrag.overlay = overlay;
  ebene.replaceChildren(overlay);
  verdrahten(overlay, k, eintrag);
  overlay.scrollTop = hoehe;
  overlay.focus({ preventScroll: true });
}

function verdrahten(overlay, k, eintrag = offen) {
  overlay.querySelector('.karte-schliessen').addEventListener('click', () => zurueck(basis()));
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay || e.target.classList.contains('karte-inhalt')) zurueck(basis());
  });
  if (!k) return;
  // Prüfen / Bearbeiten: dieselben Aktionen wie im Prüfmodus, für genau diese Karte
  aktionenZeigen(overlay);
  const beiSitzung = () => aktionenZeigen(overlay);
  window.addEventListener('binder:sitzung', beiSitzung);
  if (eintrag) eintrag.aufraeumen = () => window.removeEventListener('binder:sitzung', beiSitzung);
  overlay.querySelector('.karte-bearbeiten')?.addEventListener('click', async (e) => {
    const knopf = e.currentTarget;
    knopf.disabled = true;
    try {
      const geaendert = await bearbeiten(k.id, { titel: knopf.dataset.titel });
      if (geaendert && eintrag && offen === eintrag) await neuLaden(eintrag);
    } finally {
      knopf.disabled = false;
    }
  });
  overlay.querySelector('.karte-anmelden')?.addEventListener('click', async () => {
    if (await anmelden()) aktionenZeigen(overlay);
  });
  overlay.querySelectorAll('.karte-schalter-teil').forEach((knopf) => {
    knopf.addEventListener('click', () => {
      if (knopf.getAttribute('aria-pressed') === 'true') return;
      vorliebe('kartenbild', knopf.dataset.bild);
      bildZeigen(overlay, k, knopf.dataset.bild);
    });
  });
  bildZeigen(overlay, k, vorliebe('kartenbild') === 'foto' ? 'foto' : 'scryfall');
}

// ---------- Bewegung ----------

function cssWert(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Dauer aus basis.css (z. B. „420ms") in Millisekunden. */
function dauer() {
  const m = /^([\d.]+)(ms|s)$/.exec(cssWert('--dauer'));
  if (!m) return 420;
  return m[2] === 's' ? Number(m[1]) * 1000 : Number(m[1]);
}

function kurve() {
  return cssWert('--kurve') || 'ease-out';
}

/** Rechteck der Tasche bzw. Karte im Binder, wenn sie gerade sichtbar ist. */
function quelle(id) {
  const el = document.querySelector(`[data-karte="${id}"]`);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  const sichtbar = r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight
    && r.right > 0 && r.left < innerWidth;
  return sichtbar ? r : null;
}

/** Verschiebung und Verkleinerung, die die Karte auf das Ausgangsrechteck legt. */
function flug(von, karte) {
  const nach = karte.getBoundingClientRect();
  const s = von.width / nach.width;
  const dx = (von.left + von.width / 2) - (nach.left + nach.width / 2);
  const dy = (von.top + von.height / 2) - (nach.top + nach.height / 2);
  return `translate(${dx}px, ${dy}px) scale(${s})`;
}

async function einblenden(overlay, karte, etikett, von) {
  if (bewegungReduziert()) return;
  const laenge = dauer();
  const ease = kurve();
  const laeufe = [overlay.animate([{ opacity: 0 }, { opacity: 1 }], { duration: laenge, easing: ease })];
  if (karte) {
    laeufe.push(von
      ? karte.animate([{ transform: flug(von, karte) }, { transform: 'none' }], { duration: laenge, easing: ease })
      : karte.animate([{ opacity: 0, transform: 'scale(0.97)' }, { opacity: 1, transform: 'none' }],
        { duration: laenge, easing: ease }));
  }
  if (etikett) {
    laeufe.push(etikett.animate([{ opacity: 0, transform: 'translateY(10px)' }, { opacity: 1, transform: 'none' }],
      { duration: laenge, delay: laenge * 0.35, easing: ease, fill: 'backwards' }));
  }
  await Promise.all(laeufe.map((a) => a.finished.catch(() => {})));
}

async function ausblenden(overlay, id) {
  if (bewegungReduziert()) return;
  overlay.getAnimations({ subtree: true }).forEach((a) => a.cancel());
  const laenge = dauer();
  const ease = kurve();
  const karte = overlay.querySelector('.karte-karte');
  const von = quelle(id);
  const laeufe = [overlay.animate([{ opacity: 1 }, { opacity: 0 }],
    { duration: laenge, easing: ease, fill: 'forwards' })];
  if (karte) {
    laeufe.push(von
      ? karte.animate([{ transform: 'none' }, { transform: flug(von, karte) }],
        { duration: laenge, easing: ease, fill: 'forwards' })
      : karte.animate([{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'scale(0.97)' }],
        { duration: laenge, easing: ease, fill: 'forwards' }));
  }
  await Promise.all(laeufe.map((a) => a.finished.catch(() => {})));
}

// ---------- Öffentliche Schnittstelle ----------

/** Entfernt eine Karte sofort, ohne Animation (wenn eine andere Karte geöffnet wird). */
function entfernen(eintrag) {
  if (offen === eintrag) offen = null;
  document.removeEventListener('keydown', taste);
  eintrag.aufraeumen?.();
  eintrag.overlay?.remove();
}

export async function oeffnen(kartenId) {
  const id = Number(kartenId);
  if (!Number.isInteger(id)) return;
  if (offen && offen.id === id) return;
  if (offen) entfernen(offen);

  const eintrag = { id, overlay: null, zuvorFokus: document.activeElement };
  offen = eintrag;
  let k = null;
  let fehler = null;
  try {
    k = await holen(`/api/karte/${id}`);
  } catch (err) {
    fehler = err;
    console.error(err);
  }
  if (offen !== eintrag) return;  // inzwischen geschlossen oder eine andere Karte geöffnet

  const von = quelle(id);
  const overlay = karteDom(k, fehler);
  eintrag.overlay = overlay;
  ebene.replaceChildren(overlay);
  buehne.inert = true;
  document.addEventListener('keydown', taste);
  verdrahten(overlay, k);
  overlay.focus({ preventScroll: true });
  await einblenden(overlay, overlay.querySelector('.karte-karte'), overlay.querySelector('.karte-etikett'), von);
}

export async function schliessen() {
  const eintrag = offen;
  if (!eintrag) return;
  offen = null;
  document.removeEventListener('keydown', taste);
  eintrag.aufraeumen?.();
  if (eintrag.overlay) {
    await ausblenden(eintrag.overlay, eintrag.id);
    eintrag.overlay.remove();
  }
  if (offen) return;  // inzwischen wurde eine andere Karte geöffnet
  buehne.inert = false;
  const ziel = document.querySelector(`[data-karte="${eintrag.id}"]`) ?? eintrag.zuvorFokus;
  if (ziel?.isConnected && typeof ziel.focus === 'function') ziel.focus({ preventScroll: true });
}
