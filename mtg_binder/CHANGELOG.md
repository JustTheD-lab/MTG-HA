# Änderungen

## 0.49.0
- **Limit je Karte als Bedingung der Sortierregeln.** Neue Bedingung „Über dem Limit“ (z. B. „mehr als 4
  Exemplare“): Gezählt wird je Druck, Foil getrennt, Sprache nicht; Binder, Kisten und Decks zählen mit. Steht eine
  solche Regel vor den anderen, gehen beim Einsortieren nur die Exemplare über dem Limit in ihre Kiste. Ein Posten mit
  mehreren Karten wird nicht geteilt. Bereits einsortierte Karten bleiben, wo sie sind.
- **Überzählige Karten.** Knopf „Überzählige“ in der Gesamtsammlung: alle Drucke über einem wählbaren Limit mit
  Orten, Preis der gewählten Preisart und Gesamtwert, als Text zum Kopieren und als CSV. Standardländer nur auf
  Wunsch.

## 0.48.2
- **Protokolle der PC-Erkennung sofort lesbar.** Erkennung und Nachladen der Kartenbilder schreiben ihren Fortschritt
  gleich ins Protokoll (`data\addon_zwischen\bilder_nachladen.log`); vorher stand dort erst am Ende etwas, und das
  Nachladen sah aus, als täte es nichts.
- Die automatischen Tests ändern den Eintrag unter „Apps“ des Entwicklungs-PCs nicht mehr.

## 0.48.1
- **Installation der PC-Erkennung ohne den Paketindex von Paddle.** Der Index von paddlepaddle.org.cn antwortete am
  10.11.2026 nur mit Fehler 502, die Installation brach bei „Umgebung der Texterkennung“ ab. PaddlePaddle 3.0.0 kommt
  jetzt direkt aus dem Speicher des Paddle-Teams, mit fester Prüfsumme; alle anderen Pakete kommen von PyPI. Ein
  abgebrochener Installer, der mit einer neueren Version fortgesetzt wird, lädt Programm und Paketlisten neu.

## 0.48.0
- **Startpaket für die PC-Erkennung.** Das Add-on hält auf dem Netzlaufwerk den fertigen Bildindex und das
  Namensverzeichnis bereit (Ordner `startpaket` neben den Bildern); die installierte PC-Erkennung schickt beides
  selbst, beim Start und danach täglich, wenn das Add-on keinen oder einen älteren Stand hat. Der Installer lädt
  Startpaket und Katalog über das Heimnetz (rund 1 GB) statt rund 20 GB von Scryfall und baut keinen Index mehr; die
  Kartenbilder lädt die Erkennung danach im Hintergrund nach. Ohne Netzlaufwerk oder Startpaket installiert der PC
  wie bisher. Neue Adressen am Port 8099: `GET /pc/startpaket`, `GET|PUT /pc/startpaket/{datei}`,
  `POST /pc/startpaket/fertig`, `GET /pc/katalog`.
- **Update der PC-Erkennung zuverlässiger.** Kurz gesperrte Dateien werden bei jedem Dateifehler wiederholt (nicht
  nur bei „Zugriff verweigert“), die neue exe liegt vor dem Tausch bereit, ein gesperrtes Setup kippt das Update
  nicht mehr, ein hängengebliebener Rückweg wird beim nächsten Start vollendet, und lässt sich die alte exe nicht
  ersetzen, läuft die neue weiter statt ohne Symbol zu enden. Wirkt ab der nächsten PC-Erkennung.

## 0.47.0
- **Regeln an der Kiste.** Jede Kiste zeigt unter dem Titel den Block „Regeln dieser Kiste“: die Sortierregeln mit
  dieser Kiste als Ziel, mit Bedingungen, an oder aus und ihrem Platz in der Reihenfolge. Dort lassen sich die Regeln
  ein- und ausschalten, kopieren, bearbeiten und löschen; „Regel hinzufügen“ hat die Kiste schon als Ziel, neue Regeln
  kommen ans Ende. Die Reihenfolge bleibt auf der Seite „Sortierregeln“. Eine Kiste ohne Regel zeigt „Bekommt nichts
  automatisch“. Keine Änderung an der Datenbank.

## 0.46.0
- **Gewinner und Verlierer.** Im Dashboard je zehn Karten mit der größten Preisänderung, jetzt auch für eine Woche
  und einen Monat (dazu wie bisher seit gestern und 3 Tage). Sortiert nach Euro (Anzahl mal Preisänderung) oder,
  umschaltbar, nach Prozent des Einzelpreises. Es zählen nur Karten, deren heutiger oder früherer Preis mindestens
  0,50 € ist. Alles aus echten Preistagen und in der gewählten Preisart.
- Home Assistant: neuer Sensor „Gewinner und Verlierer Woche“ mit den Top 5 der Woche als Attribute `gewinner` und
  `verlierer` (Beispielkarte in der Anleitung). Keine Änderung an der Datenbank.

## 0.45.0
- **Fehlende Karten je Set.** In jedem Set klappt „Fehlende Karten mit Preis“ die Namen auf, von denen aus diesem Set
  noch kein Druck in der Sammlung ist (Varianten zählen nicht extra), je Name mit dem Preis des günstigsten Drucks aus
  genau diesem Set in der gewählten Preisart (ohne Foil) und der Summe oben.
- „Liste kopieren“ legt die Liste als Text in die Zwischenablage, eine Zeile „1 Kartenname (SET)“ je Karte.
- Die Preise kommen aus der täglichen Preisdatei, auch für Karten, die noch nicht in der Sammlung sind. Die Liste lädt
  erst beim Aufklappen; die Sets-Übersicht bleibt so schnell wie bisher. Keine Änderung an der Datenbank.

## 0.44.0
- **Einräumen zurücknehmen.** Im Scan-Eingang zeigt „Einräumen zurücknehmen“ die letzten zehn automatischen
  Einsortier-Läufe nach dem Scannen mit Zeit, Kartenzahl und Zielkisten. Jeder Lauf lässt sich einzeln zurücknehmen:
  Die Karten liegen danach wieder im Scan-Eingang und werden nicht von selbst erneut einsortiert („Jetzt einsortieren“
  räumt sie wieder ein).
- Zusammengefasste Posten werden dabei wieder getrennt: Die eingeräumte Karte entsteht im Scan-Eingang mit ihrem
  Scan-Foto, der Posten in der Kiste behält den Rest.
- Karten, die du seit dem Einräumen bewegt oder geändert hast, bleiben, wo sie sind; die App nennt sie nach dem
  Zurücknehmen.
- Läufe vor diesem Update stehen nicht in der Liste. Keine Änderung an der Datenbank.

## 0.43.0
- **Preisart wählbar.** Im Dashboard steht neben dem Wert der Sammlung die Wahl der Preisart: Low, Trend, Ø 1 Tag,
  Ø 7 Tage (wie bisher) oder Ø 30 Tage von Cardmarket, bei Foil-Karten jeweils der Foil-Preis. Die Wahl gilt überall:
  Karten, Binder, Kisten, Sammlung, Sets, Decks, Sortierung nach Preis, Dashboard und die Werte in Home Assistant.
- Die Kartenansicht zeigt alle fünf Preise, die gewählte Art ist hervorgehoben. Auf den Karten steht weiter „ab“ (Low)
  und daneben die gewählte Art.
- Sortierregeln behalten ihre eigene Preisart; neu sind alle fünf Arten und „wie Einstellung“.
- Trend, Ø 1 Tag und Ø 30 Tage speichert das Add-on ab dem ersten Preisabruf nach dem Update. Veränderungen dieser
  Arten (Vortag, 3 und 30 Tage) erscheinen, sobald genug Tage gesammelt sind.
- Datenbank Version 21 (wird beim Start angepasst).
- **PC-Erkennung: Updates zuverlässiger.** Ist eine Datei beim Austauschen kurz gesperrt (zum Beispiel durch den
  Virenscanner), versucht das Update es rund 20 Sekunden lang erneut, statt abzubrechen. Jeder Fehler beim Update steht
  mit allen Einzelheiten in `data\addon_zwischen\update.log` im Programmordner. Scheitert das Update auf 0.43.0 noch
  mit der alten Version, einmal das neue Setup über die bestehende Installation ausführen.

## 0.42.0
- **Sicherung zurückspielen.** In der Verwaltung gibt es „Sicherung zurückspielen …“: eine Liste aller Sicherungen
  (täglich im Add-on und auf dem Netzlaufwerk, vor Updates, Importen und Abzügen, vor „Datenbank löschen“) mit Datum,
  Herkunft und Größe. Nach der Wahl zeigt die App, was in der Sicherung steckt und was heute da ist; bestätigt wird mit
  dem eingetippten Wort ZURÜCK.
- Vorher sichert das Add-on den jetzigen Stand. Er steht danach in derselben Liste als „vor einem Zurückspielen“, das
  Zurückspielen lässt sich also selbst zurücknehmen.
- Der Preisverlauf bleibt erhalten. Fotos und Vorschaubilder liegen nicht in der Sicherung: Bilder, die seitdem
  gelöscht wurden, kommen nicht zurück.

## 0.41.0
- **Secret Lair, fertige Decks: Das ganze Deck zählt.** Bisher standen bei „Teuerste Reprints“ und „Günstigste
  Reprints“ nur die Secret-Lair-Karten des Decks, die übrigen Karten der Deckliste fehlten. Jetzt zählen sie mit ihrem
  Preis im Deck dazu („Ganzes Deck, teuerste Reprints“), auch im Verhältnis zu deinen Kosten.
- **Neu: „Ganzes Deck, günstigste Drucke“.** Das komplette Deck so billig wie möglich: je Karte der günstigste Druck.
  In der Liste der weiteren Karten steht je Karte, welcher Druck das ist.

## 0.40.0
- **Eigenes Logo:** Binder als Buchrücken im Regal, in den Farben der App (dunkles Leder, Messing). Es erscheint im
  Home-Assistant-Store, als Symbol der Web-App (Browser-Tab und Handy-Startbildschirm, die App lässt sich jetzt auch
  als eigene App auf den Startbildschirm legen), als Symbol der Erkennung neben der Uhr (mit farbigem Punkt für den
  Zustand) und für die exe und den Installer am PC. Ein eigenes Motiv, nichts aus dem Magic-Design.
- Die Erkennung am PC holt sich das neue Symbol über das automatische Update.

## 0.39.0
- **Erkennung für Windows zum Herunterladen.** In der App unter „PC verbinden“ gibt es jetzt den Knopf
  „Installer herunterladen“. Der Installer (`MTG-Binder-Setup.exe`) liegt im GitHub-Release dieses Repositorys und
  richtet alles am PC ein: Python (offizielles Paket, im Programmordner), die Erkennung, die Python-Pakete, die
  Kartenbilder von Scryfall und den Bildindex. Zielordner frei wählbar, Autostart, Desktop-Verknüpfung, Eintrag
  unter „Apps“ zum Deinstallieren, die Verbindung zu diesem Add-on gleich mit. Ein Abbruch setzt beim nächsten Start
  fort.
- **Automatische Updates der PC-Erkennung.** Ist das Add-on neuer, lädt die Erkennung die passende Version selbst
  nach, sobald sie nichts zu tun hat, prüft jede Datei gegen die Prüfsummen aus dem Add-on und startet neu.
- Keine Änderung an der Datenbank.

## 0.38.0
- Secret Lair: **Sonderausgaben.** Manche Drops gibt es zusätzlich mit besonderem Foil als eigene Drucke (Raised Foil,
  Confetti Foil, Pool Party Foil, Foil Etched …). Bisher stand dann jede Karte doppelt im Drop (zum Beispiel
  „VroooOOOMMMMMM!“) und die Summen stimmten nicht. Jetzt hat der Drop seine Karten einmal, die Sonderausgabe steht
  darunter mit eigener Summe und lässt sich als Kauf wählen. Betroffen: 7 Drops.
- Secret Lair: **Foil-Fassung „★“.** Ist der normale Druck nur Nicht-Foil, druckt Wizards die Foil-Fassung als eigene
  Nummer mit ★ (meist Rainbow Foil). Diese 109 Drucke erschienen bisher als eigene Drops und die Foil-Werte fehlten.
  Jetzt gelten sie als Foil-Ausgabe ihres Drops (Bild und Preis im Foil-Modus); dicke Anzeigekarten mit ★ zählen nicht.
- Secret Lair: Seriennummerierte Drucke und Drucke desselben Drops zählen nicht mehr als „teuerster Reprint“
  (vorher zum Beispiel Mishra's Bauble mit BRR 97z für 1.149,90 €).
- Datenbank Version 20 (wird beim Start automatisch umgestellt). Nach dem Update gleicht das Add-on einmal neu ab.

## 0.37.0
- Secret Lair: **Fertige Decks mit ganzer Deckliste.** Bei Commander-Decks (und dem Dandân-Deck) zählte die App nur
  die Secret-Lair-Karten, im Preis des Decks steckt aber die ganze Liste. Jetzt zählen alle Karten, jede in der
  Ausführung, in der sie im Deck liegt (meist nur die Secret-Lair-Karten Foil, die übrigen sind The-List-Drucke). Die
  Liste kommt von MTGJSON. Tokens und Zweitfassungen, die nicht in der Deckliste stehen, zählen nicht mit. Die übrigen
  Karten stehen auf der Seite des Decks als Tabelle mit Druck, Ausführung und Preis. Schnäppchen und eigene Käufe
  rechnen mit der ganzen Liste.
- Datenbank Version 19 (wird beim Start automatisch umgestellt). Nach dem Update gleicht das Add-on einmal neu ab.

## 0.36.0
- Secret Lair: **Umschalter Nicht-Foil/Foil** oben in der Ansicht (merkt sich das Gerät). Alle Zahlen zeigen dann die
  gewählte Ausführung: Drucke einzeln, teuerste und günstigste Reprints, ganzer Drop bei Cardmarket, Faktor,
  Schnäppchen. Für Foil gibt es eigene Kosten je Drop.
- Secret Lair: **alle Drops seit 2019** statt nur der letzten 12 Monate, nach Jahr gegliedert, mit Filtern: Suche nach
  Namen, Jahr, kommend oder bereits im Verkauf, Wunsch, Schnäppchen, „Besitze ich“. Den täglichen Preisverlauf speichert
  das Add-on weiter nur für Drops der letzten 12 Monate und für eigene.
- Secret Lair: **Schnäppchen.** Kostet der ganze Drop bei Cardmarket ab höchstens 80 % seiner Drucke einzeln (gleiche
  Ausführung), steht „Schnäppchen −NN %“ in der Liste und auf der Seite des Drops. Die Schwelle ist einstellbar. Hat
  nicht jede Karte des Drops einen Preis, gibt es kein Schnäppchen.
- Secret Lair: **eigene Secret Lairs.** Auf der Seite eines Drops trägst du Käufe ein (Ausführung, Anzahl, Preis je
  Stück, Kaufdatum), auch mehrere, mit Rückgängig. Die App zeigt je Kauf und je Drop Kaufpreis, heutigen Wert und
  Gewinn oder Verlust in Euro und Prozent, oben in der Ansicht die Summe aller eigenen Drops und im Dashboard eine
  Kachel. Der Wert ist der ganze Drop bei Cardmarket (Trend, sonst ab), sonst die Drucke einzeln; die Grundlage steht
  dabei. Der Abgleich verliert keinen Kauf.
- Datenbank Version 18 (wird beim Start automatisch umgestellt). Nach dem Update gleicht das Add-on einmal neu ab.

## 0.35.2
- Secret Lair: Was die Drucke eines Drops einzeln bei Cardmarket zusammen kosten, heißt jetzt **„Drucke einzeln bei
  Cardmarket“** (vorher „Dieser Drop heute“) und steht auf der Seite des Drops auch im Abschnitt „Bei Cardmarket“
  direkt unter dem ganzen Drop. Fehlen einzelne Preise, steht dabei „5 von 6 Karten“.

## 0.35.1
- Secret Lair: Nach dem Update auf 0.35.0 hatte kein Drop einen Cardmarket-Preis. Der Abgleich, der den
  Cardmarket-Katalog lädt, lief erst 20 Stunden nach dem letzten. Jetzt gleicht das Add-on nach einem Update mit
  neuen Quellen gleich nach dem Start einmal ab (etwa 2 Minuten danach).

## 0.35.0
- Secret Lair: **Preis des Drops bei Cardmarket mit Link.** Auf der Seite eines Drops stehen beide Fassungen, die
  Cardmarket führt (mit und ohne „Set“), je mit „ab“ und Trend für Nicht-Foil und Foil und einem Link zum Produkt;
  in der Liste eine Zeile „Drop bei Cardmarket“. Die Zuordnung kommt aus dem Produktkatalog von Cardmarket (täglich),
  die Preise aus der täglichen Preisdatei; ist die Zuordnung unsicher, zeigt die App keine. In der Testkopie: 87 von
  97 Drops zugeordnet, 80 davon mit Preis.
- Datenbank Version 17 (wird beim Start automatisch umgestellt).

## 0.34.0
- Secret Lair: **Namen und Verkaufstage aus MTG Wiki.** Das Wiki nennt Drops mit Sammlernummern und Tag, neue
  Superdrops etwa zwei Wochen vor dem Verkauf. Reihenfolge: dein eigener Name und Tag, MTG Wiki, MTGJSON (jetzt
  auch der Drop-Name je Karte), sonst wie bisher nach Tag und Nummer. In der Testkopie sank die Zahl der Drops ohne
  Namen von 70 auf 4; Star Trek steht mit allen sechs Drops am 09.11.
- Secret Lair: **Bonuskarten und Promos** sind keine eigenen Drops mehr. Sie hängen als „Bonuskarte“ am Drop, zu
  dem sie gehören, und zählen nicht zu seinem Wert; ohne bekannten Drop erscheinen sie nicht.
- Katalog: Secret-Lair-Karten der letzten 60 Tage und alle kommenden werden täglich bei Scryfall neu geholt, damit
  ein korrigierter Verkaufstag ankommt.
- Datenbank Version 16 (wird beim Start automatisch umgestellt).

## 0.33.0
- Secret Lair: **Verkaufstag ändern** je Drop. Der Abgleich lässt einen geänderten Tag in Ruhe, „Wieder automatisch“
  nimmt wieder den Tag aus Scryfall.
- Secret Lair: **Möchte ich kaufen** markiert einen Drop als Wunsch (Marke in der Liste).
- Secret Lair: Einstellung **Verkauf ab 17:00 Uhr** für alle Drops (änderbar). Ein Drop gilt bis zu dieser Uhrzeit
  an seinem Verkaufstag als „kommend“.
- Home Assistant: neue Sensoren `sensor.mtg_secret_lair_nachster_wunsch` (Verkaufsbeginn des nächsten Wunschs als
  Zeitstempel, für eine Erinnerung per Automation) und `sensor.mtg_secret_lair_wunsche_kommend`. Beispiel in
  HOME-ASSISTANT.md.
- Datenbank Version 15 (wird beim Start automatisch umgestellt).

## 0.32.6
- Regal: Der Knopf **Secret Lair** steht jetzt auch neben „Dashboard“ (weiterhin auch in der Sets-Ansicht).

## 0.32.5
- Regal: Das Lesezeichen der farbigen Kisten ist jetzt ein **Leseband wie in einem alten Buch**: breiter, gerippte
  Seide mit feinen Querrippen, dunkleren Webkanten und mattem Glanz, knickt über die Oberkante der Kiste und fällt
  über das Etikett, mit V-Schnitt und goldener Steppnaht.

## 0.32.4
- Regal: Das Lesezeichen der farbigen Kisten ist jetzt ein **Stoffband**: breiter, kommt zwischen den Karten heraus und
  fällt bis auf das Etikett, matte Farbe mit feinem Gewebe und goldener Steppnaht an beiden Rändern.

## 0.32.3
- Regal: Farbige Kisten sind wieder dunkel wie die anderen; die Farbe zeigt ein schmales **Lesezeichen**, das oben aus
  den Karten hängt (auch vorn an der Kiste mit Fächern). Karten, Posten und Wert stehen je in einer eigenen Zeile,
  vorher lief die Zeile auf breiten Bildschirmen aus der Kiste.

## 0.32.2
- Sortierregeln und Fächer: **einzelne Bedingungen kopieren.** Jede Bedingung hat neben „nicht“ einen eigenen
  Kopierknopf; „Einfügen“ nennt dann die Bedingung („Bedingung „Preis“ aus Fach „Länder“ einfügen“). Der bisherige
  Knopf unter den Bedingungen heißt jetzt „Alle kopieren“.

## 0.32.1
- Behoben: Das Update auf 0.32.0 konnte scheitern, wenn PyPI beim Bauen zu langsam antwortete („Read timed out“).
  Die Pakete werden jetzt mit Wiederholungen geladen und bleiben bei späteren Updates im Zwischenspeicher, statt bei
  jeder neuen Version neu geladen zu werden.

## 0.32.0
- Sortierregeln und Fächer: **Bedingungen kopieren und einfügen.** An jeder Regel und in jedem Bedingungs-Editor
  (Regel bearbeiten, Fächer einer Kiste) gibt es „Kopieren“; „Einfügen“ hängt die kopierten Bedingungen in jeder
  anderen Regel oder jedem Fach an, auch in einer anderen Kiste. Gespeichert wird erst mit „Speichern“; die Ablage
  hält bis zum Neuladen der Seite.

## 0.31.2
- Behoben: Von Hand hinzugefügte Karten zeigten kein Bild, solange der PC noch kein Vorschaubild geliefert hat. Der
  Lesetisch nimmt dann das Bild direkt von Scryfall (das kannte er bisher gar nicht). Die Kistenansicht konnte das
  schon, erkannte die Bildadresse nach „Datenbank löschen“ aber nicht mehr (angehängter Stand `?s=…`).

## 0.31.1
- Behoben: Eine Leerseite, in die eine Karte gelegt wurde, zeigte weiter das Schild „Leerseite“ (ohne Umschalter
  Scryfall/Mein Foto, mit den Leerseiten-Knöpfen unter „Seiten verwalten“). Sie gilt jetzt als normale Seite; liegt
  keine Karte mehr darin, ist sie wieder eine Leerseite.

## 0.31.0
- Lesetisch: **Ganze Seite löschen** im Bearbeiten-Modus (Werkzeug „Löschen“, Knopf „Ganze Seite löschen …“), etwa
  wenn ein Foto für Fehler gesorgt hat. Die Karten der Seite werden aus der Sammlung entfernt, an ihrer Stelle bleibt
  eine Leerseite (die Seitenzahlen ändern sich nicht), das Originalfoto bleibt erhalten. Ein neues Foto für die
  Leerseite gibt es über „Seiten verwalten“. Mit Rückfrage und „Rückgängig“; bei einer Doppelseite wählt man die Seite.

## 0.30.0
- Proxys: **Archidekt-Link** einfügen und „Laden“: Hauptdeck mit Commander, Druck aus Archidekt, Basic Lands nur mit
  Haken. Die Liste landet im Eingabefeld und wird gleich geprüft. Das Deck muss öffentlich oder ungelistet sein.
- Behoben: Listen aus Decks der App und aus „Günstigster Druck“ verloren Drucke mit besonderen Nummern (The List wie
  `IMA-177`, Buchstaben wie `151p`, Sterne). Die App schreibt solche Listen jetzt im Deckstats-Format
  (`1 [PLST#IMA-177] Nature's Claim`), das jede Nummer liest.

## 0.29.0
- Proxys: Neue Auswahl **„Druck bevorzugen“**: wie in der Liste, **günstigster Druck** oder **erster Druck**.
- Günstigster Druck: nach dem Cardmarket-Preis Ø 7 Tage (ohne ihn Low) aus dem täglichen Price Guide, nur englische
  Papierdrucke mit gutem Scan (bei Scryfall nachgefragt). Nach dem PDF zeigt die App je Zeile, welcher Druck gewählt
  wurde, und die Summe.
- Erster Druck: der älteste normale Druck (ohne Promos), über Silhouette Card Maker.
- Beides gilt für Zeilen ohne Set und Nummer, mit „Sets ignorieren“ für alle Zeilen.

## 0.28.0
- Proxys: **Kalibrierung** von Vorder- und Rückseite aus der Oberfläche. Kalibrierbogen (A4) von Silhouette Card Maker
  herunterladen, das passende Feld eintragen, fertig; der Bogen kommt immer mit dem gespeicherten Versatz, so lassen
  sich auch große Abweichungen in mehreren Runden einstellen. Werte (x, y, Winkel) auch direkt eingeben.
- Der Druckversatz steckt automatisch in jedem neuen PDF, je Auftrag abschaltbar.
- Der Stand von Silhouette Card Maker bekommt dafür zusätzlich das Paket pypdfium2 (rund 6 MB). Ein schon
  eingerichteter Stand wird nach dem Update einmal neu eingerichtet (einige Minuten); bis dahin geht der Bogen nur
  ohne Versatz.

## 0.27.3
- Watchdog: Home Assistant prüft jetzt, ob der Server noch antwortet (`/api/gesund`), und startet das Add-on sonst
  neu. Einschalten mit dem Schalter „Watchdog“ auf der Seite des Add-ons. Ein fehlendes Netzlaufwerk löst keinen
  Neustart aus.

## 0.27.2
- Große Rückseiten (über 12 MB) verkleinert der Browser vor dem Hochladen auf 1488 Pixel Breite (doppelte
  Druckauflösung). Home Assistant reicht über Ingress nur Anfragen bis 16 MB weiter, darum kam bei großen Bildern
  „Die Datei ist zu groß“.

## 0.27.1
- Eigene Rückseiten dürfen jetzt bis 40 MB groß sein (vorher 10 MB). Hochauflösende Rückseiten mit 300 dpi waren
  zu groß.

## 0.27.0
- Proxys aus deinen Decks: Im Bereich „Proxys“ gibt es „Aus Deck übernehmen“, in der Deckansicht den Knopf „Proxys
  drucken“. Vorgewählt sind die fehlenden und die als Proxy markierten Karten (per Haken auch vorhandene Karten und
  Basic Lands), gedruckt wird der Druck aus der Deckliste.
- Keine Änderung an der Datenbank.

## 0.26.0
- Neu: Proxys drucken. Der Knopf „Proxys“ im Regal macht aus einer Deckliste ein druckfertiges PDF (A4, standard,
  3-Punkt-Passermarken) mit Silhouette Card Maker von Alan Cha (MIT-Lizenz): Format automatisch erkennen, Vorschau mit
  nicht gefundenen Karten, Rückseite wählen oder hochladen, PDF herunterladen, die letzten 50 PDFs aufheben.
- Das Add-on lädt neue Releases von Silhouette Card Maker selbst (beim Start und alle 24 Stunden), testet sie und
  schaltet erst dann um. Der vorherige Stand bleibt zum Zurückfallen. Die erste Einrichtung nach dem Update dauert
  einige Minuten.
- Das Abbild nutzt jetzt Python 3.12 (Silhouette Card Maker verlangt es). Keine Änderung an der Datenbank.

## 0.25.0
- Prüfansicht und Kartenansicht zeigen bei Kamera-Scans jetzt unter „Mein Foto“ den Ausschnitt, nach dem die Karte
  bestimmt wurde. Schon gescannte Karten bekommen ihn beim Start nachgetragen.
- Datenbank Version 14 (neue Spalte am Scan-Foto); die Sicherung vor dem Update läuft wie immer automatisch.

## 0.24.0
- Neu: Dashboard, erreichbar über den Knopf neben „Sets“ im Regal. Es zeigt den Wert der Sammlung mit Preisentwicklung,
  Kennzahlen, die teuersten Karten mit Bild, Gewinner und Verlierer, Verteilungen nach Ort, Set, Farbe, Seltenheit,
  Preisbereich, Kartentyp, Sprache und Manawert, dazu Zuwachs, Bulk-Scan, fast vollständige Sets und kommende Secret Lairs.
- Keine Änderung an der Datenbank.

## 0.23.0
- Secret Lair: Ein Drop zeigt jetzt Bilder, je Karte die Secret-Lair-Fassung, den teuersten und den günstigsten Reprint
  mit Preis und Druck. Ein Tipp vergrößert das Bild. Die Bilder lädt der Browser direkt von Scryfall.
- Datenbank Version 13 (die Sicherung entsteht beim Update wie immer). Die Bilder der Reprints erscheinen wenige Minuten
  nach dem Start, sobald die Drops neu bewertet sind.

## 0.22.2
- Nach einem Update bietet die App oben „Neu laden“ an, wenn die geöffnete Seite noch den alten Stand zeigt. Das wirkt ab
  dem nächsten Update; für dieses bitte die Seite einmal neu laden.

## 0.22.1
- Secret Lair ist jetzt ein Untermenü der Sets: Der Knopf steht oben rechts in der Sets-Ansicht, nicht mehr im Regal.

## 0.22.0
- Secret Lair: Der neue Knopf im Regal zeigt kommende Drops und die der letzten 12 Monate. Je Karte stehen der teuerste
  und der günstigste Nonfoil-Reprint, je Drop die Summen und das Verhältnis zu deinen Kosten. Das sind Zahlen zum
  Vergleich, keine Kaufempfehlung.
- Die App findet Drops täglich selbst (Scryfall, Namen von MTGJSON). Fehlende Karten fügst du als Text aus der
  Ankündigung ein. Die Kosten je Drop sind einstellbar.
- Für diese Drops speichert das Add-on jetzt täglich die Preise, auch wenn du die Karten nicht besitzt.
- Home Assistant: neue Sensoren `sensor.mtg_secret_lair_kommend` und `sensor.mtg_secret_lair_neuester_drop`.
- Datenbank Version 12 (die Sicherung entsteht beim Update wie immer).

## 0.21.0
- ManaBox-Import in der App: Unter „Verwaltung“ einen ManaBox-Export wählen und je Binder, Liste und Deck entscheiden:
  neue Kiste, vorhandene Kiste, nach Sortierregeln oder überspringen. Listen und Decks sind zum Überspringen vorgewählt.
- Ein Probelauf zeigt das Ergebnis, bevor etwas geschrieben wird. Vorher entsteht eine Sicherung, und der ganze Import
  lässt sich mit einem Tipp zurücknehmen. Ein neuerer Export aktualisiert vorhandene Karten, löscht aber nie welche.
- Kaufpreise aus ManaBox werden jetzt mit gespeichert (Datenbank Version 11, die Sicherung entsteht beim Update wie immer).

## 0.20.0
- Tägliche Sicherung: Das Add-on sichert die Datenbank jetzt jeden Tag von selbst und prüft jede Sicherung. Die letzten 7
  bleiben im Add-on, die letzten 30 auf dem Netzlaufwerk unter `sicherung/`.
- Neuer Knopf „Verwaltung“ im Regal: zeigt den Stand der Sicherungen und führt zu „Datenbank löschen“.
- „Datenbank löschen“ für den Neubeginn: zeigt vorher, was gelöscht wird, und fragt, ob die Original-Fotos ins Archiv
  sollen. Eine Sicherung der Datenbank kommt vorher in den Ordner `archiv/`. Der Kartenkatalog und die Verbindung zum
  PC bleiben. Solange die Erkennung am PC arbeitet oder das Netzlaufwerk fehlt, ist das Löschen gesperrt.

## 0.19.1
- Die Kiste bleibt bedienbar, wenn man ein Fach einsteckt, während seine Karten noch laden.
- Eine neue Karte in einer leeren Tasche zeigt nicht mehr das Foto, das dort vorher lag.
- Rückgängig stellt beim Abzug und beim Entfernen einer Karte auch die Verbindung zum Scan-Foto wieder her.
- Die Kiste bleibt nach einer Änderung in der Ansicht, die gerade zu sehen ist.
- Beim Einsortieren werden nur Karten mit gleichem Zustand zusammengefasst, und ein Posten bleibt bei höchstens 999 Karten.
- Die Auswahl für eine leere Tasche zeigt bis zu 2.000 zurückgestellte Posten und sagt, wenn weitere nicht hineinpassen.

## 0.19.0
- Eine Kiste mit Fächern zeigt sich jetzt als echte Kiste: Jedes Fach steht als Trennkarte mit Reiter darin, dahinter
  ragen seine ersten Karten hervor. Dazu stehen Karten und Wert jedes Fachs, auch leere Fächer sind zu sehen.
- Ein Tipp zieht das Fach heraus und zeigt nur seine Karten, mit Sortieren, Filtern, Auswählen und Statistik wie
  gewohnt. „Zurück“ steckt es wieder ein.
- „Alle Karten“ zeigt die bisherige Gesamtansicht; die Kiste merkt sich die Wahl. Kisten ohne Fächer bleiben unverändert.

## 0.18.1
- Kisten mit Fächern zeigen das Fach jetzt an jeder Karte, in allen drei Ansichten. Unter „Gruppieren“ → „Fach“ lässt
  sich die Kiste weiter nach Fächern ordnen.

## 0.18.0
- Karten aus dem Scan-Eingang für einen Binder zurückstellen: Im Auswahlmodus legt „Für Binder zurückstellen“ die
  gewählten Karten in die neue Kiste „Zurückgestellt“. Unsichere Karten bleiben im Eingang, bis sie geprüft sind.
- Im Binder unter „Bearbeiten“ zeigt ein Tipp auf eine leere Tasche zuerst diese Karten. Von einem Stapel geht ein
  Exemplar in die Tasche. „Andere Karte suchen“ öffnet wie bisher die Katalogsuche. Alles mit Rückgängig.
- Korrektur: Die Auswahlleiste einer Kiste steht wieder unten am Bildschirm statt am Ende der Seite.

## 0.17.0
- Scan-Eingang: Der neue Knopf „Jetzt einsortieren“ wendet die Sortierregeln auf alle Karten im Eingang an, auch auf
  solche, zu denen beim Scan keine Regel passte. Vorher zeigt die App, wie viele Karten wohin gehen und was bleibt.
- Unsichere Karten bleiben im Eingang, bis sie geprüft sind. Das Einsortieren lässt sich als Ganzes rückgängig machen.

## 0.16.0
- Kisten kopieren: Der neue Knopf „Kopieren“ in der Kiste legt eine leere Kiste mit derselben Farbe und denselben
  Fächern an. Im Dialog „Neue Kiste“ lässt sich dafür eine Kiste unter „Einstellungen übernehmen von“ wählen.
- Karten bleiben in der alten Kiste, Sortierregeln zeigen weiter auf sie.

## 0.15.0
- Bulk-Scan schneller: Der PC erfährt sofort von neuen Fotos, statt alle 30 Sekunden nachzusehen. Sobald die Scan-Seite
  Bilder schickt, lädt er schon die Modelle. Die Scan-Seite zeigt den Namen, sobald die Karte erkannt ist.
- Messwerte je Scan-Foto (Datenbank Version 10, beim Start mit Sicherung): Sie zeigen, wo die Zeit bleibt. Die
  Erkennung am PC zeigt sie im neuen Statistikfenster.
- Die Erkennung am PC muss dazu neu gebaut werden. Mit der alten exe läuft das Add-on weiter, nur ohne diese Vorteile.

## 0.14.2
- Preisverlauf begrenzt: Die Preise bleiben Tag für Tag gespeichert. Erreichen sie etwa 200 MB, löscht das Add-on
  beim täglichen Abruf die ältesten Tage; die letzten 40 Tage bleiben immer.

## 0.14.1
- Binder bearbeiten: Taschen mit Rückseite (und unklare Taschen ohne Karte) lassen sich jetzt bearbeiten.
  Antippen legt eine Karte hinein, das Werkzeug „Löschen“ markiert die Tasche als leer, beim Verschieben sind
  sie ein Ziel. Alles mit Rückgängig.

## 0.14.0
- Bulk-Scan: je Durchgang „Alle Karten Foil“ und „Sets wählen“. Foil-Karten werden wie gewohnt zu Posten
  zusammengefasst und nach Foil-Preis bewertet.
- Sets wählen: Suche nach Name oder Code; zu einem Set schlägt die App die zugehörigen Codes vor (Hauptset,
  Commander, Bonus-Reihen und Special Guests vom selben Tag, z. B. bei Reality Fracture FRA, FRC und SPG).
- Die Karte erkennt die App wie bisher; nur der Druck wird unter den gewählten Sets gesucht. Liegt eine Karte in
  keinem davon, kommt sie als „unsicher“ zur Prüfung. Ein neuer Durchgang beginnt ohne Foil und ohne Auswahl.
- Datenbank Version 9 (zwei neue Spalten je Durchgang; die Sicherung entsteht beim Update wie immer).

## 0.13.0
- Farbrad für Binder und Kisten: Neben den festen Farben lässt sich jede Farbe wählen (Farbkreis, Helligkeit,
  Farbcode). Gilt beim Anlegen eines Binders, unter „Binder bearbeiten“, bei „Neue Kiste“ und bei „Kiste
  bearbeiten“ (früher „Umbenennen“, jetzt mit Farbe).
- Farbige Kisten: Die ganze Kiste bekommt die Farbe, das Etikett bleibt Messing. Messing, Silber und Kupfer sind
  weiter dunkle Kisten mit Metalletikett. Schrift auf hellen Farben wird automatisch dunkel.

## 0.12.0
- Nur noch das Add-on fragt Scryfall: Es merkt sich neue und geänderte Karten, die Erkennung am PC holt sie über
  das Heimnetz ab (Port 8099, mit Schlüssel) und fragt Scryfall nicht mehr selbst. Die Bilder neuer Karten lädt
  sie weiter direkt vom Bildserver von Scryfall.
- Junge Drucke ohne fertigen Scan fragt das Add-on täglich nach, damit der PC das Bild bekommt, sobald es fertig
  ist. Beim ersten Abgleich nach dem Update kommen alle Drucke der letzten 120 Tage einmal in die Liste.

## 0.11.0
- Neue Karten von Scryfall: Das Add-on gleicht seinen Kartenkatalog täglich selbst mit Scryfall ab und holt nur,
  was neu ist (alle Sprachen). Junge Drucke ohne Cardmarket-Produkt werden bis zu 120 Tage lang nachgefragt,
  danach haben sie Preise. Nichts wird gelöscht.
- Die Erkennung am PC gleicht ihren Katalog ebenso ab, wenn keine Fotos warten, und nimmt neue Karten in das
  Namensverzeichnis und den Bildindex auf (nur fertige Scans, keine Vorschaubilder).

## 0.10.0
- Deckbau: Ein Deck ist jetzt ein eigener Ort wie eine Kiste.
  - „Deck bauen“ zeigt eine Entnahmeliste nach Ort (Kisten nach Fach, Binder nach Seite und Platz). Die App schlägt
    das Exemplar vor (genauer Druck zuerst, dann Kisten vor Bindern), ein anderes lässt sich wählen.
  - Abhaken merkt nur vor (bleibt gespeichert, auch beim Gerätewechsel); „Fertig“ legt alle abgehakten Karten in
    einem Schritt ins Deck, mit Rückgängig. Tauchen fehlende Karten später auf, bietet das Deck einen Nachtrag an.
  - „Zerlegen“ legt die Karten an ihren alten Platz zurück (sonst Herkunftskiste, Sortierregel oder eine Kiste
    nach Wahl). Ein Deck mit Karten lässt sich erst nach dem Zerlegen löschen.
  - „Deck zur Sammlung hinzufügen“ (z. B. Precon): legt alle Karten der Liste neu im Deck an (Basics ja, Proxys
    nein).
- Deckpreise: Wert der Deckliste, Kosten der Fehlt-Liste, Wert im Deck mit Veränderung zum Vortag, Preis je Karte
  (sortierbar). Steht nur der Name in der Liste, zählt der günstigste Druck. Der tägliche Preisabruf speichert
  dafür auch die Karten der Decklisten.
- Karten in Decks zählen zur Sammlung; in anderen Decks stehen sie als „steckt in Deck X“. Gesamtsammlung und
  Kartenansicht kennen den Ort „Deck“. Home Assistant: je Deck Sensoren für Wert und Karten.
- Die Datenbank wird beim Start auf Version 8 gebracht, vorher legt das Add-on eine Sicherung an.

## 0.9.1
- Archidekt-Import korrigiert: Es zählt nur die erste Kategorie einer Karte (wie bei Archidekt). Karten mit einer
  zusätzlichen Kategorie „Maybeboard“ und Karten in eigenen Kategorien wie „Tokens“ fehlten bisher. Schon
  importierte Decks mit „Neu laden“ ergänzen.

## 0.9.0
- Decks: neuer Regalboden „Decks“.
  - Import per Archidekt-Link (öffentliche oder ungelistete Decks) oder als eingefügter Export-Text (Moxfield,
    Archidekt). Nur das Hauptdeck mit Commander.
  - Abgleich mit der Sammlung: vorhanden, wo die Karte liegt (Binder, Seite, Platz oder Kiste), was fehlt. Der
    Abgleich zählt wahlweise den Namen oder nur den genauen Druck.
  - Schalter „Basic Lands auslassen“; einzelne Karten als Proxy markieren.
  - Fehlt-Liste zum Kopieren für die Cardmarket-Wunschliste.
  - „Neu laden“ holt Änderungen aus Archidekt, vorher mit Vorschau.
- Die Datenbank wird beim Start auf Version 7 gebracht, vorher legt das Add-on eine Sicherung an.

## 0.8.0
- Binder bearbeiten: Knopf „Bearbeiten“ im Lesetisch mit den Werkzeugen Foil, Verschieben und Löschen.
  - Verschieben geht in eine andere Tasche (auch auf einer anderen Seite), in einen anderen Binder (nächste freie
    Tasche) oder in eine Kiste. Auf einer besetzten Tasche tauschen die Karten die Plätze.
  - Löschen entfernt die Karte ganz aus der Sammlung.
  - Ein Tipp auf eine leere Tasche fügt eine Karte aus dem Katalog hinzu.
  - Jede Aktion lässt sich rückgängig machen.
- Foil-Modus: Ein Tipp schaltet ein Exemplar um, ein Stapel teilt sich dabei. Dazu „Ganze Seite Foil“.
  - Den Foil-Modus gibt es auch in Kiste und Gesamtsammlung.
  - Foil-Karten zeigen einen Regenbogen-Schimmer und einen Stern.

## 0.7.0
- Werte an Home Assistant per MQTT mit Discovery (Gerät „MTG“): Gesamtwert mit Veränderung zum Vortag, nach 3 und
  30 Tagen (Euro und Prozent), Kartenzahlen, je Binder und Kiste ein Wert- und ein Kartensensor, Top 5 teuerste
  Karten, Top 5 Gewinner und Verlierer, Warteschlange, Karten zur Prüfung, PC verbunden, letzter Scan und letzter
  Preisabruf.
- Braucht das Mosquitto-Add-on; den Zugang holt das Add-on selbst beim Supervisor. Abschaltbar in der Konfiguration
  („Werte an Home Assistant (MQTT)“). Ist das Add-on aus, zeigen die Sensoren „nicht verfügbar“.
- Nur lesend, kein neuer Port, keine neue Abhängigkeit.

## 0.6.3
- Sortierung „Preis (Ø 7 Tage)“ in der Gesamtsammlung und in jeder Kiste (Foil-Karten mit Foil-Preis, ohne Preis
  zuletzt).
- Neue Ansicht „Sets“ (Regal unter „Sammlung“ oder Kopf der Gesamtsammlung): je Set die Vollständigkeit nach
  Drucken (jede Sammlernummer, auch Borderless und Showcase) und nach Namen, mit Karten und Wert. Im Set alle
  Drucke oder Namen, gefiltert nach vorhanden oder fehlend. Braucht den Katalog im Add-on.

## 0.6.2
- Kisten lassen sich jetzt auch mit Karten löschen („Löschen“ in der Kiste, Name eintippen und noch einmal
  bestätigen). Karten und Fächer werden mit Historie gelöscht; eine Kiste, in die eine Sortierregel einsortiert,
  bleibt stehen. Der Scan-Eingang entsteht beim nächsten Scan neu.

## 0.6.1
- Fotos hochladen: Mehrere Fotos auf einmal kommen nach dem Dateinamen sortiert in die Liste (IMG_2 vor IMG_10);
  vorher bestimmten Windows-Dateidialog und Ziehen die Reihenfolge.
- „Binder löschen“ unter „Seiten verwalten“ (Name eintippen und noch einmal bestätigen): löscht Binder, Seiten und
  Karten mit Historie. Die Originalfotos bleiben liegen und lassen sich ohne Doppel-Hinweis neu hochladen.

## 0.6.0
- Sortierregeln: frei einstellbare Regeln („Wenn … → Kiste“) mit Farbe, Manawert, Typ, Seltenheit, Set, Art des
  Sets, Special Print, Promo, Foil, Sprache, Name und Preis; Reihenfolge = Vorrang. Probe für eine Karte.
- Fächer je Kiste und Gruppierung „Fach“ in der Kistenansicht.
- Bulk-Scan: sicher erkannte Karten wandern sofort in ihre Kiste (je Durchgang abschaltbar), unsichere nach dem
  Prüfen; fehlt ein Wert (z. B. der Preis), wartet die Karte im Scan-Eingang, ohne Preis bei Cardmarket zählt sie
  als 0 €. Ziel oder Wartegrund im Scan-Eingang.
- Datenbank Version 6 (vorher automatische Sicherung), Katalog Version 3 (Special-Print-Merkmale).

## 0.5.2
- Preise neuer Karten (Erkennung, Bulk-Scan, Druck ändern, Karte hinzufügen) erscheinen nach etwa einer halben
  Minute statt erst beim nächsten stündlichen Blick.

## 0.5.1
- Bulk-Scan: Eine gleichnamige Kiste aus einem ManaBox-Import wird nicht mehr zum Scan-Eingang (dann
  „Scan-Eingang 2“). Link im Scan-Eingang auch für Karten, die inzwischen nicht mehr in einer Kiste liegen.

## 0.5.0
- Bulk-Scan: Läuft die Erkennung am PC, holt sie auch die Scan-Fotos ab und erkennt je Foto eine Karte. Erkannte
  Karten kommen in die Kiste „Scan-Eingang“ (gleiche sichere Drucke als ein Posten mit Anzahl, unsichere einzeln
  zum Prüfen). Der Scan-Eingang und die Scan-Seite zeigen die erkannten Namen.

## 0.4.1
- Der PC kann Scan-Fotos als Kopie laden (Entwicklungsset für die Erkennung der Einzelfotos), ohne ihren Stand
  im Add-on zu ändern.

## 0.4.0
- Bulk-Scan mit dem Card Slinger: „Scannen“ im Regal filmt die Scanzone mit dem Handy, das Add-on entscheidet,
  wann ein Foto entsteht (eine Karte je Wurf), und legt die Fotos in den Scan-Eingang auf dem Netzlaufwerk.
  Verwerfen und Zurückholen im Scan-Eingang. Die Erkennung der Einzelfotos am PC folgt.
- Datenbank Version 5 (vorher automatische Sicherung).

## 0.3.0
- Preise von Cardmarket: Low und Durchschnitt 7 Tage auf jeder Karte, Gesamtwert je Binder, Kiste und Sammlung,
  Veränderung gegen Vortag, 3 und 30 Tage in der Kartenansicht. Das Add-on lädt die Preise täglich selbst.
- Datenbank Version 4 (vorher automatische Sicherung).

## 0.2.0
- Erkennung am PC über Port 8099 mit Schlüssel („PC verbinden“ im Regal), Kartenkatalog im Add-on.
- Bild neuer Karten direkt von Scryfall, bis der PC die Vorschau liefert.

## 0.1.0
- Erste Fassung: Oberfläche über Ingress, Fotos und Bilder auf dem Netzlaufwerk, Testkopie einspielen.
