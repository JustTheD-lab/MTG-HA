// Links zu Cardmarket: Produktseite über die Produktnummer (keine geratenen Adressen), mit den Angebotsfiltern
// von Cardmarket als Adressparameter. Die Filter sind Vorlieben im Browser und werden in der Kaufliste eingestellt
// (Bereich „Cardmarket-Filter“); sie gelten für jeden Knopf „Cardmarket“ der App (Kaufliste, Kartenansicht).
// Kein Preisabruf von der Website: Cardmarket steht hinter einer Bot-Prüfung, die Preise kommen aus der täglichen
// Preisdatei.

import { vorliebe } from './gemeinsam.js';

const CARDMARKET = 'https://www.cardmarket.com/de/Magic/Products';

export const CM_FILTER = {
  land: { name: 'Verkäuferland', param: 'sellerCountry', werte: [['', 'Alle'], ['7', 'Deutschland'], ['1', 'Österreich'],
    ['4', 'Schweiz'], ['23', 'Niederlande'], ['2', 'Belgien'], ['20', 'Luxemburg'], ['12', 'Frankreich'], ['17', 'Italien'],
    ['10', 'Spanien'], ['8', 'Dänemark'], ['25', 'Polen'], ['6', 'Tschechien']] },
  sprache: { name: 'Kartensprache', param: 'language', werte: [['', 'Alle'], ['1', 'Englisch'], ['3', 'Deutsch'],
    ['1,3', 'Englisch oder Deutsch'], ['2', 'Französisch'], ['5', 'Italienisch'], ['4', 'Spanisch'], ['7', 'Japanisch']] },
  zustand: { name: 'Mindestzustand', param: 'minCondition', werte: [['', 'Alle'], ['1', 'Mint'], ['2', 'Near Mint'],
    ['3', 'Excellent'], ['4', 'Good'], ['5', 'Light Played'], ['6', 'Played']] },
};

/** Gewählte Filter {land, sprache, zustand}; unbekannte Werte zählen als „Alle“. */
export function cmFilter() {
  const f = {};
  for (const [k, d] of Object.entries(CM_FILTER)) {
    const w = vorliebe(`kaufliste.cm.${k}`) ?? '';
    f[k] = d.werte.some(([v]) => v === w) ? w : '';
  }
  return f;
}

/** Adresse der Produktseite für `{cardmarket_id, foil}` mit den gewählten Filtern; Foil sucht nur Foil. */
export function cmAdresse(p) {
  const q = new URLSearchParams({ idProduct: String(p.cardmarket_id) });
  const f = cmFilter();
  for (const [k, d] of Object.entries(CM_FILTER)) if (f[k]) q.set(d.param, f[k]);
  q.set('isFoil', p.foil ? 'Y' : 'N');
  return `${CARDMARKET}?${q.toString().replaceAll('%2C', ',')}`;
}
