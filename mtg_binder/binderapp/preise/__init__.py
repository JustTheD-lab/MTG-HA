"""Preise aus dem Price Guide von Cardmarket (Entscheidung des Nutzers, 31.10.2026).

`abruf.py` lädt die Datei einmal täglich (im Add-on selbst) und speichert für die Produkte der Sammlung Low und
Durchschnitt 7 Tage (normal und Foil) je Tag in der Tabelle `preis`. `werte.py` liest daraus die aktuellen Preise,
die Veränderung gegen Vortag, 3 und 30 Tage (nur aus echtem Verlauf) und die Werte je Binder und Kiste.
Nur Standardbibliothek, läuft im Add-on.
"""
