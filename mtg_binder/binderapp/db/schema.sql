-- Datenmodell der MTG-Binder-App (Stand 03.11.2026, Version 8)
--
-- Binder → Seite → Tasche → Karte. Jede Sammlung gehört einem Nutzer.
-- Fotos tragen den Verarbeitungsstatus (späterer Upload-Puffer, Phase 5).
-- Jede Änderung an Binder, Seite, Tasche, Kiste und Karte steht in „historie".
-- Bilder liegen als Dateien neben der Datenbank; hier stehen nur Bildschlüssel.
--
-- Version 2 (Upload und Leerseiten): Spalten am Ende von foto und seite, Tabelle dienst.
-- Version 3 (Bulkkisten, ManaBox-Import, Kartendaten): Tabellen kiste und kartendaten,
-- Spalten am Ende von karte (kiste_id, quelle, extern_id, zustand).
-- Ältere Datenbanken bringt `binderapp.db.migrieren` auf denselben Stand
-- (ALTER TABLE … ADD COLUMN), deshalb stehen neue Spalten immer am Ende der Tabelle.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS nutzer (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL UNIQUE,
    angelegt  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

-- Originalfoto einer Binderseite. Das Original selbst wird nie verändert.
CREATE TABLE IF NOT EXISTS foto (
    id         INTEGER PRIMARY KEY,
    nutzer_id  INTEGER NOT NULL REFERENCES nutzer(id),
    datei      TEXT NOT NULL UNIQUE,       -- relativ zu data/photos, z. B. abnahme/IMG_5465.JPEG
    sha256     TEXT,                       -- Prüfsumme des Originals
    status     TEXT NOT NULL DEFAULT 'neu'
               CHECK (status IN ('neu', 'in_verarbeitung', 'erkannt', 'fehler')),
    fehler     TEXT,                       -- Fehlermeldung bei Status „fehler"
    erkennung  TEXT,                       -- Erkennungsstand (Commit), z. B. c804be1
    angelegt   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    geaendert  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    -- ab Version 2
    quelle      TEXT NOT NULL DEFAULT 'import' CHECK (quelle IN ('import', 'upload')),
    dateiname   TEXT,                      -- ursprünglicher Dateiname vom Gerät, nur zur Anzeige
    groesse     INTEGER,                   -- Bytes des Originals
    format      TEXT,                      -- am Dateianfang erkannt: jpeg oder heic
    hochgeladen TEXT                       -- Zeitpunkt des Hochladens (UTC)
);

CREATE TABLE IF NOT EXISTS binder (
    id          INTEGER PRIMARY KEY,
    nutzer_id   INTEGER NOT NULL REFERENCES nutzer(id),
    name        TEXT NOT NULL,
    farbe       TEXT NOT NULL,             -- schwarz, durchsichtig, gelb, blau …
    raster      TEXT NOT NULL CHECK (raster GLOB '[1-9]x[1-9]'),  -- Zeilen x Spalten
    regalplatz  INTEGER NOT NULL DEFAULT 0,  -- Reihenfolge im Bücherregal
    angelegt    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    geaendert   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    UNIQUE (nutzer_id, name)
);

-- Vorder- und Rückseite eines Blattes sind getrennte Seiten mit je einem Foto.
-- Die Nummer vergibt das System (binderapp.db.seiten): ab 1, lückenlos, aufsteigend.
-- Eine Leerseite (art = leer) hat kein Foto und nur leere Taschen.
CREATE TABLE IF NOT EXISTS seite (
    id         INTEGER PRIMARY KEY,
    binder_id  INTEGER NOT NULL REFERENCES binder(id) ON DELETE CASCADE,
    nummer     INTEGER NOT NULL CHECK (nummer >= 1),  -- Blätterreihenfolge
    foto_id    INTEGER REFERENCES foto(id),
    -- ab Version 2
    art        TEXT NOT NULL DEFAULT 'foto' CHECK (art IN ('foto', 'leer')),
    UNIQUE (binder_id, nummer)
);

CREATE TABLE IF NOT EXISTS tasche (
    id          INTEGER PRIMARY KEY,
    seite_id    INTEGER NOT NULL REFERENCES seite(id) ON DELETE CASCADE,
    zeile       INTEGER NOT NULL CHECK (zeile >= 1),
    spalte      INTEGER NOT NULL CHECK (spalte >= 1),
    inhalt      TEXT NOT NULL CHECK (inhalt IN ('karte', 'leer', 'rueckseite', 'unsicher')),
    ausschnitt  TEXT,                      -- Bildschlüssel des Foto-Ausschnitts, z. B. IMG_5451_z1s1
    UNIQUE (seite_id, zeile, spalte)
);

-- Bulkkiste (ab Version 3): Karten ohne festen Platz, sortierbar nach Kartendaten.
-- extern_name: Name des ManaBox-Binders, aus dem die Kiste importiert wurde (bleibt beim Umbenennen).
CREATE TABLE IF NOT EXISTS kiste (
    id           INTEGER PRIMARY KEY,
    nutzer_id    INTEGER NOT NULL REFERENCES nutzer(id),
    name         TEXT NOT NULL,
    etikettfarbe TEXT NOT NULL DEFAULT 'messing',
    regalplatz   INTEGER NOT NULL DEFAULT 0,  -- Reihenfolge auf dem Regalboden „Kisten"
    extern_name  TEXT,
    angelegt     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    geaendert    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    UNIQUE (nutzer_id, name)
);

-- Bestandsposten: ein genauer Druck mit Anzahl. Liegt in einer Tasche (Binder), in einer
-- Kiste oder (ohne beides) lose in der Sammlung; nie zugleich in Tasche und Kiste.
-- quelle: scan (Erkennung), manabox (CSV-Import) oder manuell. Karten der Quelle manabox
-- werden nie mit Scan-Karten zusammengezählt.
CREATE TABLE IF NOT EXISTS karte (
    id           INTEGER PRIMARY KEY,
    nutzer_id    INTEGER NOT NULL REFERENCES nutzer(id),
    tasche_id    INTEGER REFERENCES tasche(id) ON DELETE SET NULL,
    scryfall_id  TEXT,                     -- Druck in der gelesenen Sprache, falls bekannt
    oracle_id    TEXT,
    name         TEXT NOT NULL,
    set_code     TEXT,
    nummer       TEXT,                     -- Sammlernummer (Text, z. B. „123a")
    sprache      TEXT,
    foil         INTEGER NOT NULL DEFAULT 0 CHECK (foil IN (0, 1)),
    anzahl       INTEGER NOT NULL DEFAULT 1 CHECK (anzahl >= 1),
    status       TEXT NOT NULL CHECK (status IN ('sicher', 'name_sicher', 'unsicher')),  -- Erkennung
    konfidenz    REAL,
    gruende      TEXT,                     -- Begründung der Erkennung
    geprueft     INTEGER NOT NULL DEFAULT 0 CHECK (geprueft IN (0, 1)),  -- vom Nutzer bestätigt
    bild         TEXT,                     -- Bildschlüssel des Scryfall-Bildes (meist scryfall_id)
    angelegt     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    geaendert    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    -- ab Version 3
    kiste_id     INTEGER REFERENCES kiste(id) CHECK (kiste_id IS NULL OR tasche_id IS NULL),
    quelle       TEXT NOT NULL DEFAULT 'scan' CHECK (quelle IN ('scan', 'manabox', 'manuell')),
    extern_id    TEXT,                     -- Schlüssel der Zeile in der Quelle (ManaBox: Binder, ID, Foil, Sprache, Zustand)
    zustand      TEXT,                     -- Erhaltung laut Quelle, z. B. near_mint
    -- ab Version 6 (Sortierregeln)
    sortieren    TEXT CHECK (sortieren IS NULL OR sortieren = 'offen'),  -- offen: wartet aufs Einsortieren (Bulk-Scan)
    vorgemerkt_binder_id INTEGER REFERENCES binder(id) ON DELETE SET NULL,  -- schwebend: für diesen Binder vorgemerkt
    -- ab Version 8 (Deckbau): dritter Ort „Deck“ (die App sorgt dafür, dass nur einer von Tasche, Kiste, Deck gesetzt
    -- ist); die Herkunft merkt sich, woher eine Karte ins Deck kam (Zerlegen legt sie dorthin zurück)
    deck_id      INTEGER REFERENCES deck(id),
    herkunft_tasche_id INTEGER REFERENCES tasche(id) ON DELETE SET NULL,
    herkunft_kiste_id  INTEGER REFERENCES kiste(id) ON DELETE SET NULL,
    -- ab Version 11 (ManaBox-Import in der App): Kaufpreis je Exemplar laut Quelle, damit ein Export ihn zurückgibt
    kaufpreis    REAL,
    kaufpreis_waehrung TEXT
);

-- Kartendaten je Druck (ab Version 3), abgeleitet aus dem Scryfall-Katalog (binderapp.katalog),
-- nur für Drucke, die in Bindern oder Kisten vorkommen. Damit sortieren und filtern Kisten auch
-- ohne Katalog (Raspberry Pi). Abgeleitete Daten, deshalb ohne Historie.
CREATE TABLE IF NOT EXISTS kartendaten (
    scryfall_id     TEXT PRIMARY KEY,           -- wie karte.scryfall_id (auch Sprachdrucke)
    oracle_id       TEXT,
    name            TEXT NOT NULL,              -- englischer Name laut Scryfall
    set_code        TEXT,                       -- klein, wie Scryfall
    set_name        TEXT,
    set_erschienen  TEXT,                       -- Erscheinungsdatum des Sets, JJJJ-MM-TT
    nummer          TEXT,                       -- Sammlernummer als Text
    nummer_zahl     INTEGER,                    -- Zahlanteil zum Sortieren (123a → 123, ★ → NULL)
    sprache         TEXT,                       -- Sprache des Katalogdrucks, aus dem die Daten stammen
    manakosten      TEXT,                       -- Vorderseite
    manawert        REAL NOT NULL DEFAULT 0,    -- Scryfall cmc
    farben          TEXT NOT NULL DEFAULT '',   -- Vorderseite, Reihenfolge WUBRG, '' = farblos
    farbidentitaet  TEXT NOT NULL DEFAULT '',   -- Reihenfolge WUBRG
    farbgruppe      TEXT NOT NULL CHECK (farbgruppe IN ('W','U','B','R','G','M','C','L')),
    typzeile        TEXT,                       -- Vorderseite
    haupttyp        TEXT NOT NULL CHECK (haupttyp IN ('kreatur','planeswalker','spontanzauber','hexerei',
                        'artefakt','verzauberung','schlacht','land','sonstige')),
    seltenheit      TEXT,                       -- common, uncommon, rare, mythic, special, bonus
    bild            TEXT,                       -- Scryfall-ID eines Drucks mit lokalem Bild (für Vorschaubilder)
    gefunden_ueber  TEXT NOT NULL CHECK (gefunden_ueber IN ('id','sprachdruck','set_nummer','oracle')),
    geaendert       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    cardmarket_id   INTEGER,                    -- ab Version 4: Produkt bei Cardmarket (Preise)
    -- ab Version 6 (Sortierregeln, Katalog Version 3); NULL = unbekannt (älterer Katalog)
    besonderheiten  TEXT,                       -- Merkmale mit Kommas, z. B. borderless,showcase ('' = keine)
    promo           INTEGER,                    -- Scryfall promo (0/1)
    set_art         TEXT                        -- Scryfall set_type (expansion, box, promo …)
);

-- Preise je Tag (ab Version 4) aus dem Price Guide von Cardmarket, nur für Produkte der Sammlung. Abgeleitete
-- Daten, deshalb ohne Historie; der Verlauf selbst ist die Tabelle. Preise in Euro, NULL = kein Wert.
CREATE TABLE IF NOT EXISTS preis (
    cardmarket_id  INTEGER NOT NULL,
    tag            TEXT NOT NULL,              -- JJJJ-MM-TT (Erstellungstag der Preisdatei)
    low            REAL,
    avg7           REAL,
    low_foil       REAL,
    avg7_foil      REAL,
    PRIMARY KEY (cardmarket_id, tag)
);

-- Ein Eintrag je Abruf der Preisdatei (ab Version 4)
CREATE TABLE IF NOT EXISTS preisabruf (
    tag            TEXT PRIMARY KEY,           -- JJJJ-MM-TT, wie preis.tag
    erstellt       TEXT,                       -- createdAt der Datei bei Cardmarket
    abgerufen      TEXT NOT NULL,              -- Zeitpunkt des Abrufs (UTC)
    produkte       INTEGER NOT NULL,           -- Produkte in der Datei
    gespeichert    INTEGER NOT NULL            -- davon in der Sammlung, gespeichert
);

-- Bulk-Scan (ab Version 5): ein Durchgang je Scan-Sitzung am Handy (Card Slinger), darin die Einzelfotos, die
-- das Add-on aus dem Livestream ausgelöst hat (binderapp.scan). Fotos werden nie gelöscht, „verworfen“ ist ein Status.
CREATE TABLE IF NOT EXISTS scan_durchgang (
    id         INTEGER PRIMARY KEY,
    nutzer_id  INTEGER NOT NULL REFERENCES nutzer(id),
    begonnen   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    beendet    TEXT,
    geaendert  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    -- ab Version 6: erkannte Karten nach den Sortierregeln einsortieren (Einstellung je Durchgang)
    einsortieren INTEGER NOT NULL DEFAULT 1 CHECK (einsortieren IN (0, 1)),
    -- ab Version 9 (04.11.2026): alle Karten des Durchgangs sind Foil; erlaubte Sets als JSON-Liste der Set-Codes
    -- (NULL = alle). Die Sets wirken nur auf die Wahl des Drucks, die Karte bestimmt die Erkennung wie bisher.
    foil INTEGER NOT NULL DEFAULT 0 CHECK (foil IN (0, 1)),
    sets TEXT
);

CREATE TABLE IF NOT EXISTS scan_foto (
    id            INTEGER PRIMARY KEY,
    durchgang_id  INTEGER NOT NULL REFERENCES scan_durchgang(id),
    nummer        INTEGER NOT NULL CHECK (nummer >= 1),   -- Reihenfolge im Durchgang
    datei         TEXT NOT NULL UNIQUE,      -- relativ zum Fotoordner, z. B. scan/3/00012.jpg
    vorschau      TEXT,                      -- relativ zu bilder/, vom Handy verkleinert (kein Bildpaket im Add-on)
    sha256        TEXT NOT NULL,
    groesse       INTEGER NOT NULL,
    status        TEXT NOT NULL DEFAULT 'neu'
                  CHECK (status IN ('neu', 'in_verarbeitung', 'erkannt', 'fehler', 'verworfen')),
    fehler        TEXT,
    erkennung     TEXT,                      -- Erkennungsstand (Commit), Teilrunde B
    karte_id      INTEGER REFERENCES karte(id) ON DELETE SET NULL,
    ausloeser     TEXT,                      -- JSON: Grund und Messwerte der Auslösung (zum Einstellen)
    aufgenommen   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    geaendert     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    ausschnitt    TEXT,                      -- ab Version 14: Bildschlüssel des Ausschnitts, z. B. scan-12_z1s1
    UNIQUE (durchgang_id, nummer)
);

-- Messwerte je Scan-Foto (ab Version 10, Bulk-Scan schneller): keine Sammlungsdaten, deshalb ohne Historie.
-- Zeiten in Sekunden seit 1970 auf der Uhr des Add-ons; die _ms-Werte misst das Handy mit seiner eigenen Uhr.
CREATE TABLE IF NOT EXISTS scan_zeit (
    scan_foto_id  INTEGER PRIMARY KEY REFERENCES scan_foto(id),
    ausgeloest    REAL,      -- Add-on hat ausgelöst
    angekommen    REAL,      -- Foto vollständig im Add-on
    aufnahme_ms   INTEGER,   -- Handy: Auslösen bis JPEG fertig
    warte_ms      INTEGER,   -- Handy: JPEG fertig bis Beginn des Hochladens (Warteschlange)
    hochladen_ms  INTEGER,   -- Handy: Dauer des Hochladens
    name_ms       INTEGER    -- Handy: Auslösen bis Name auf der Scan-Seite
);

-- Sortierregeln (ab Version 6): geordnete Liste je Nutzer, die erste passende aktive Regel bestimmt das Ziel
-- (binderapp.sortieren). Bedingungen als JSON aus festen Feldern; Ziel ist eine Kiste oder ein Binder (Vormerkung).
CREATE TABLE IF NOT EXISTS sortierregel (
    id           INTEGER PRIMARY KEY,
    nutzer_id    INTEGER NOT NULL REFERENCES nutzer(id),
    platz        INTEGER NOT NULL DEFAULT 0,      -- Reihenfolge (aufsteigend)
    name         TEXT NOT NULL,
    aktiv        INTEGER NOT NULL DEFAULT 1 CHECK (aktiv IN (0, 1)),
    bedingungen  TEXT NOT NULL DEFAULT '[]',
    kiste_id     INTEGER REFERENCES kiste(id),
    binder_id    INTEGER REFERENCES binder(id),
    angelegt     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    geaendert    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    CHECK ((kiste_id IS NULL) <> (binder_id IS NULL))
);

-- Fächer einer Kiste oder eines Binders (ab Version 6): wie die Regeln, die erste passende gewinnt. Das Fach einer
-- Karte wird berechnet, nicht gespeichert.
CREATE TABLE IF NOT EXISTS sortierfach (
    id           INTEGER PRIMARY KEY,
    nutzer_id    INTEGER NOT NULL REFERENCES nutzer(id),
    kiste_id     INTEGER REFERENCES kiste(id),
    binder_id    INTEGER REFERENCES binder(id),
    platz        INTEGER NOT NULL DEFAULT 0,
    name         TEXT NOT NULL,
    bedingungen  TEXT NOT NULL DEFAULT '[]',
    angelegt     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    geaendert    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    CHECK ((kiste_id IS NULL) <> (binder_id IS NULL))
);

-- Decks (ab Version 7): Deckliste aus Archidekt (verknüpft, „Neu laden“) oder aus eingefügtem Text, nur das Hauptdeck
-- mit Commander. Der Abgleich mit der Sammlung wird berechnet (binderapp.decks), nicht gespeichert.
CREATE TABLE IF NOT EXISTS deck (
    id           INTEGER PRIMARY KEY,
    nutzer_id    INTEGER NOT NULL REFERENCES nutzer(id),
    name         TEXT NOT NULL,
    quelle       TEXT NOT NULL CHECK (quelle IN ('archidekt', 'text')),
    extern_id    TEXT,                       -- Archidekt: Deck-ID (Ziffern)
    extern_stand TEXT,                       -- Archidekt: updatedAt beim letzten Laden
    abgleich     TEXT NOT NULL DEFAULT 'name' CHECK (abgleich IN ('name', 'druck')),
    basics_auslassen INTEGER NOT NULL DEFAULT 1 CHECK (basics_auslassen IN (0, 1)),
    regalplatz   INTEGER NOT NULL DEFAULT 0,
    geladen      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    angelegt     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    geaendert    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

-- Zeilen einer Deckliste. `druck_angegeben` = 0: nur der Name stand in der Liste (scryfall_id ist dann ein Druck zum
-- Anzeigen, set_code und nummer sind leer). Ohne Katalog-Treffer bleibt oracle_id leer, abgeglichen wird dann nach Name.
CREATE TABLE IF NOT EXISTS deck_karte (
    id           INTEGER PRIMARY KEY,
    deck_id      INTEGER NOT NULL REFERENCES deck(id) ON DELETE CASCADE,
    scryfall_id  TEXT,
    oracle_id    TEXT,
    name         TEXT NOT NULL,
    set_code     TEXT,
    nummer       TEXT,
    druck_angegeben INTEGER NOT NULL DEFAULT 1 CHECK (druck_angegeben IN (0, 1)),
    anzahl       INTEGER NOT NULL CHECK (anzahl >= 1),
    foil         INTEGER NOT NULL DEFAULT 0 CHECK (foil IN (0, 1)),
    commander    INTEGER NOT NULL DEFAULT 0 CHECK (commander IN (0, 1)),
    basic        INTEGER NOT NULL DEFAULT 0 CHECK (basic IN (0, 1)),
    proxy        INTEGER NOT NULL DEFAULT 0 CHECK (proxy IN (0, 1)),  -- zählt als vorhanden, nicht in der Fehlt-Liste
    -- ab Version 8: Zahl der Cardmarket-Produkte in deck_produkt (NULL = noch nicht im Katalog gesucht)
    produkte     INTEGER
);

-- Cardmarket-Produkte je Deckzeile (ab Version 8), abgeleitet aus dem Katalog, ohne Historie: bei angegebenem Druck
-- genau eins, bei nur einem Namen alle Drucke der Karte (Preis = günstigster Druck, Entscheidung des Nutzers).
-- Der tägliche Preisabruf speichert auch diese Produkte.
CREATE TABLE IF NOT EXISTS deck_produkt (
    deck_karte_id INTEGER NOT NULL REFERENCES deck_karte(id) ON DELETE CASCADE,
    cardmarket_id INTEGER NOT NULL,
    PRIMARY KEY (deck_karte_id, cardmarket_id)
);

-- Haken der Entnahme beim Deckbau (ab Version 8), bis „Fertig“ (dann wandern die Karten ins Deck). `abgehakt` = 0:
-- nur ein anderes Exemplar gewählt. Zwischenstand, ohne Historie.
CREATE TABLE IF NOT EXISTS deck_entnahme (
    id            INTEGER PRIMARY KEY,
    deck_id       INTEGER NOT NULL REFERENCES deck(id) ON DELETE CASCADE,
    deck_karte_id INTEGER NOT NULL REFERENCES deck_karte(id) ON DELETE CASCADE,
    karte_id      INTEGER NOT NULL REFERENCES karte(id) ON DELETE CASCADE,
    anzahl        INTEGER NOT NULL CHECK (anzahl >= 1),
    abgehakt      INTEGER NOT NULL DEFAULT 0 CHECK (abgehakt IN (0, 1)),
    UNIQUE (deck_karte_id, karte_id)
);

-- Änderungshistorie: eine Zeile je Änderung, mit Quelle und den geänderten Feldern als JSON.
CREATE TABLE IF NOT EXISTS historie (
    id         INTEGER PRIMARY KEY,
    nutzer_id  INTEGER NOT NULL REFERENCES nutzer(id),
    zeitpunkt  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    tabelle    TEXT NOT NULL,              -- binder, seite, tasche, kiste, karte, foto
    datensatz  INTEGER NOT NULL,
    aktion     TEXT NOT NULL CHECK (aktion IN ('angelegt', 'geaendert', 'geloescht')),
    quelle     TEXT NOT NULL,              -- erkennung, pruefliste, zuordnung, nutzer, manabox
    alt        TEXT,                       -- JSON der Felder vorher
    neu        TEXT,                       -- JSON der Felder nachher
    -- ab Version 3 (Rückgängig in der Prüfansicht): Zeilen eines Bedienschritts tragen dieselbe
    -- Kennung; ein Rückgängig-Schritt nennt den Schritt, den er zurücknimmt.
    schritt          TEXT,
    rueckgaengig_von TEXT
);

-- Lebenszeichen der Dienste (ab Version 2), z. B. der Verarbeiter auf dem PC.
-- Kein Sammlungsinhalt, deshalb ohne Historie.
CREATE TABLE IF NOT EXISTS dienst (
    name       TEXT PRIMARY KEY,           -- verarbeiter
    zeitpunkt  TEXT NOT NULL,              -- letztes Lebenszeichen (UTC)
    info       TEXT                        -- JSON, z. B. {"pid": 1234, "zustand": "wartet"}
);

-- Secret-Lair-Drops (ab Version 12, secretlair/drops.py): keine Sammlung, sondern Marktdaten, deshalb ohne Historie.
-- Die App bewertet Drops der letzten 12 Monate und kommende; ihre Preise landen zusätzlich im Verlauf `preis`.
CREATE TABLE IF NOT EXISTS secret_drop (
    id         INTEGER PRIMARY KEY,
    schluessel TEXT NOT NULL UNIQUE,       -- wiki:<Name>, mtgjson:<Name>, scryfall:<Tag>:<erste Nummer>, hand:<Kennung>
    name       TEXT NOT NULL,
    quelle     TEXT NOT NULL CHECK (quelle IN ('wiki', 'mtgjson', 'scryfall', 'hand')),   -- Wiki ab Version 16
    verkauf    TEXT,                       -- Verkaufstag JJJJ-MM-TT (aus Scryfall oder vom Nutzer)
    kosten     REAL,                       -- eigene Kosten dieses Drops in Euro, NULL = Standard
    umbenannt  INTEGER NOT NULL DEFAULT 0 CHECK (umbenannt IN (0, 1)),   -- Name vom Nutzer: der Abgleich lässt ihn
    -- ab Version 15 (Wunsch des Nutzers, 09.11.2026)
    datum_eigen INTEGER NOT NULL DEFAULT 0 CHECK (datum_eigen IN (0, 1)),   -- Verkaufstag vom Nutzer: der Abgleich lässt ihn
    wunsch     INTEGER NOT NULL DEFAULT 0 CHECK (wunsch IN (0, 1)),   -- der Nutzer möchte ihn kaufen (Home Assistant erinnert)
    angelegt   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE TABLE IF NOT EXISTS secret_karte (
    id            INTEGER PRIMARY KEY,
    drop_id       INTEGER NOT NULL REFERENCES secret_drop(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    oracle_id     TEXT,                    -- NULL: Name nicht im Katalog gefunden
    scryfall_id   TEXT,                    -- der neue Druck, sobald der Katalog ihn kennt
    nummer        TEXT,
    cardmarket_id INTEGER,
    anzahl        INTEGER NOT NULL DEFAULT 1 CHECK (anzahl >= 1),
    preis         REAL,                    -- dieser Druck, Ø 7 Tage, sonst Low
    teuer         REAL,                    -- teuerster Nonfoil-Reprint (Wunsch des Nutzers)
    teuer_set     TEXT,
    teuer_nummer  TEXT,
    guenstig      REAL,                    -- günstigster Nonfoil-Reprint
    guenstig_set  TEXT,
    nur_sld       INTEGER NOT NULL DEFAULT 0 CHECK (nur_sld IN (0, 1)),  -- Basic Land: nur andere Secret Lairs zählen
    -- ab Version 13 (Bilder in der Ansicht): die Drucke hinter den Vergleichswerten
    teuer_id      TEXT,
    guenstig_id   TEXT,
    guenstig_nummer TEXT,
    -- ab Version 16: Bonuskarte oder Promo des Drops (zählt nicht zu seinem Wert)
    bonus         INTEGER NOT NULL DEFAULT 0 CHECK (bonus IN (0, 1))
);

-- ab Version 17 (Wunsch des Nutzers, 09.11.2026): der Drop als Cardmarket-Produkt, mit und ohne Endung „Set“
CREATE TABLE IF NOT EXISTS secret_produkt (
    id            INTEGER PRIMARY KEY,
    drop_id       INTEGER NOT NULL REFERENCES secret_drop(id) ON DELETE CASCADE,
    cardmarket_id INTEGER NOT NULL,
    name          TEXT NOT NULL,              -- Name bei Cardmarket
    set_fassung   INTEGER NOT NULL DEFAULT 0 CHECK (set_fassung IN (0, 1)),   -- Name endet auf „Set“
    low           REAL,                       -- ab-Preis, Preistrend, dasselbe für Foil (aus der Preisdatei)
    trend         REAL,
    low_foil      REAL,
    trend_foil    REAL,
    UNIQUE (drop_id, cardmarket_id)
);

CREATE TABLE IF NOT EXISTS secret_wert (
    name  TEXT PRIMARY KEY,                -- kosten (Standard in Euro), abgleich, bewertet, fehler, uhrzeit,
                                           -- wiki, wiki_stand, cm_produkte (gemerkte Quellen)
    wert  TEXT
);

CREATE INDEX IF NOT EXISTS i_secret_karte_drop ON secret_karte(drop_id);
CREATE INDEX IF NOT EXISTS i_secret_produkt_drop ON secret_produkt(drop_id);
CREATE INDEX IF NOT EXISTS i_seite_binder   ON seite(binder_id, nummer);
CREATE INDEX IF NOT EXISTS i_tasche_seite   ON tasche(seite_id);
CREATE INDEX IF NOT EXISTS i_karte_tasche   ON karte(tasche_id);
CREATE INDEX IF NOT EXISTS i_karte_name     ON karte(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS i_karte_nutzer   ON karte(nutzer_id);
CREATE INDEX IF NOT EXISTS i_historie_satz  ON historie(tabelle, datensatz);
CREATE INDEX IF NOT EXISTS i_foto_status    ON foto(status);
CREATE INDEX IF NOT EXISTS i_foto_sha256    ON foto(nutzer_id, sha256);
-- ab Version 3: Kisten blättern, sortieren und filtern (Name, Set, Manawert), Import wiederholbar
CREATE INDEX IF NOT EXISTS i_karte_kiste_name ON karte(kiste_id, name COLLATE NOCASE, id);
CREATE INDEX IF NOT EXISTS i_karte_kiste_set  ON karte(kiste_id, set_code, nummer);
CREATE INDEX IF NOT EXISTS i_karte_scryfall   ON karte(scryfall_id);
CREATE UNIQUE INDEX IF NOT EXISTS i_karte_extern ON karte(nutzer_id, quelle, extern_id) WHERE extern_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS i_kiste_nutzer     ON kiste(nutzer_id, regalplatz);
CREATE INDEX IF NOT EXISTS i_kd_manawert      ON kartendaten(manawert);
CREATE INDEX IF NOT EXISTS i_kd_set           ON kartendaten(set_erschienen, set_code, nummer_zahl);
CREATE INDEX IF NOT EXISTS i_historie_schritt ON historie(schritt);
-- ab Version 4: Preise
CREATE INDEX IF NOT EXISTS i_kd_cardmarket    ON kartendaten(cardmarket_id);
CREATE INDEX IF NOT EXISTS i_preis_tag        ON preis(tag);
-- ab Version 5: Bulk-Scan
CREATE INDEX IF NOT EXISTS i_scan_foto_status ON scan_foto(status, id);
CREATE INDEX IF NOT EXISTS i_scan_durchgang   ON scan_durchgang(nutzer_id, id);
-- ab Version 6: Sortierregeln
CREATE INDEX IF NOT EXISTS i_sortierregel     ON sortierregel(nutzer_id, platz, id);
CREATE INDEX IF NOT EXISTS i_sortierfach      ON sortierfach(kiste_id, binder_id, platz, id);
CREATE INDEX IF NOT EXISTS i_karte_sortieren  ON karte(sortieren) WHERE sortieren IS NOT NULL;
CREATE INDEX IF NOT EXISTS i_karte_vorgemerkt ON karte(vorgemerkt_binder_id) WHERE vorgemerkt_binder_id IS NOT NULL;
-- ab Version 7: Decks
CREATE INDEX IF NOT EXISTS i_deck_nutzer      ON deck(nutzer_id, regalplatz, id);
CREATE INDEX IF NOT EXISTS i_deck_karte       ON deck_karte(deck_id);
CREATE INDEX IF NOT EXISTS i_karte_oracle     ON karte(oracle_id);
-- ab Version 8: Deckbau
CREATE INDEX IF NOT EXISTS i_karte_deck       ON karte(deck_id) WHERE deck_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS i_deck_produkt_cm  ON deck_produkt(cardmarket_id);
CREATE INDEX IF NOT EXISTS i_deck_entnahme    ON deck_entnahme(deck_id);
-- ab Version 14: Ausschnitt je Scan-Foto
CREATE INDEX IF NOT EXISTS i_scan_foto_karte  ON scan_foto(karte_id);

PRAGMA user_version = 17;
