"""Kleiner MQTT-Client (Version 3.1.1) nur mit der Standardbibliothek: verbinden, senden, Lebenszeichen, trennen.

Das Add-on sendet nur (QoS 0, meist mit „retain“) und abonniert nichts. Der „letzte Wille“ meldet Home Assistant
„offline“, wenn die Verbindung abreißt (Add-on gestoppt, abgestürzt, Netz weg); dann zeigen die Sensoren
„nicht verfügbar“. Entscheidung des Nutzers (02.11.2026): eigener Client statt einer neuen Abhängigkeit.
"""
from __future__ import annotations

import socket
import struct

PROTOKOLL = b"\x00\x04MQTT\x04"   # Protokollname und Stufe 4 (3.1.1)
CONNECT, CONNACK, PUBLISH, PINGREQ, PINGRESP, DISCONNECT = 0x10, 0x20, 0x30, 0xC0, 0xD0, 0xE0
ANTWORTEN = {1: "Protokollversion abgelehnt", 2: "Kennung abgelehnt", 3: "Broker nicht verfügbar",
             4: "Benutzer oder Passwort falsch", 5: "nicht berechtigt"}


class MqttFehler(OSError):
    pass


def _laenge(n: int) -> bytes:
    """Restlänge als variable Zahl (7 Bit je Byte)."""
    teile = bytearray()
    while True:
        n, rest = divmod(n, 128)
        teile.append(rest | (0x80 if n else 0))
        if not n:
            return bytes(teile)


def _text(wert: str | bytes) -> bytes:
    daten = wert.encode("utf-8") if isinstance(wert, str) else wert
    return struct.pack("!H", len(daten)) + daten


def paket(art: int, inhalt: bytes = b"") -> bytes:
    return bytes([art]) + _laenge(len(inhalt)) + inhalt


def connect_paket(kennung: str, benutzer: str | None, passwort: str | None, wille: tuple[str, str] | None,
                  keepalive: int) -> bytes:
    flags = 0x02   # saubere Sitzung
    nutzlast = _text(kennung)
    if wille:
        flags |= 0x04 | 0x20   # letzter Wille, mit „retain“ (QoS 0)
        nutzlast += _text(wille[0]) + _text(wille[1])
    if benutzer:
        flags |= 0x80
        nutzlast += _text(benutzer)
        if passwort is not None:
            flags |= 0x40
            nutzlast += _text(passwort)
    return paket(CONNECT, PROTOKOLL + bytes([flags]) + struct.pack("!H", keepalive) + nutzlast)


def publish_paket(thema: str, nutzlast: str | bytes, behalten: bool) -> bytes:
    daten = nutzlast.encode("utf-8") if isinstance(nutzlast, str) else nutzlast
    return paket(PUBLISH | (0x01 if behalten else 0), _text(thema) + daten)


class Verbindung:
    """Eine Verbindung zum Broker. Bei jedem Fehler `MqttFehler`; neu verbinden entscheidet der Aufrufer."""

    def __init__(self, host: str, port: int = 1883, benutzer: str | None = None, passwort: str | None = None,
                 kennung: str = "mtg-binder", wille: tuple[str, str] | None = None, keepalive: int = 120,
                 zeitlimit: float = 10.0):
        self.host, self.port, self.benutzer, self.passwort = host, port, benutzer, passwort
        self.kennung, self.wille, self.keepalive, self.zeitlimit = kennung, wille, keepalive, zeitlimit
        self.sock: socket.socket | None = None

    def _lesen(self, n: int) -> bytes:
        daten = b""
        while len(daten) < n:
            teil = self.sock.recv(n - len(daten))
            if not teil:
                raise MqttFehler("Verbindung vom Broker geschlossen")
            daten += teil
        return daten

    def _antwort(self) -> tuple[int, bytes]:
        kopf = self._lesen(1)[0]
        laenge, faktor = 0, 1
        for _ in range(4):
            b = self._lesen(1)[0]
            laenge += (b & 0x7F) * faktor
            if not b & 0x80:
                break
            faktor *= 128
        return kopf, self._lesen(laenge)

    def _senden(self, daten: bytes) -> None:
        if self.sock is None:
            raise MqttFehler("nicht verbunden")
        try:
            self.sock.sendall(daten)
        except OSError as f:
            self.schliessen()
            raise MqttFehler(f"Senden fehlgeschlagen: {f}") from f

    def verbinden(self) -> None:
        self.schliessen()
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.zeitlimit)
            self.sock.sendall(connect_paket(self.kennung, self.benutzer, self.passwort, self.wille, self.keepalive))
            kopf, inhalt = self._antwort()
        except OSError as f:
            self.schliessen()
            raise MqttFehler(f"Broker {self.host}:{self.port} nicht erreichbar: {f}") from f
        if kopf != CONNACK or len(inhalt) != 2 or inhalt[1] != 0:
            self.schliessen()
            code = inhalt[1] if kopf == CONNACK and len(inhalt) == 2 else None
            raise MqttFehler(f"Anmeldung am Broker abgelehnt: {ANTWORTEN.get(code, code)}")

    def senden(self, thema: str, nutzlast: str | bytes, behalten: bool = True) -> None:
        self._senden(publish_paket(thema, nutzlast, behalten))

    def ping(self) -> None:
        """Lebenszeichen mit Antwort: erkennt eine stille, abgerissene Verbindung."""
        self._senden(paket(PINGREQ))
        try:
            kopf, _ = self._antwort()
        except OSError as f:
            self.schliessen()
            raise MqttFehler(f"keine Antwort auf das Lebenszeichen: {f}") from f
        if kopf != PINGRESP:
            self.schliessen()
            raise MqttFehler(f"unerwartete Antwort 0x{kopf:02x}")

    @property
    def verbunden(self) -> bool:
        return self.sock is not None

    def trennen(self) -> None:
        """Sauber trennen (der letzte Wille wird dabei nicht gesendet)."""
        if self.sock is not None:
            try:
                self.sock.sendall(paket(DISCONNECT))
            except OSError:
                pass
        self.schliessen()

    def schliessen(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
