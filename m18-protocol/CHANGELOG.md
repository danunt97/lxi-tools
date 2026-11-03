# Changelog

## 1.3 – Offline-Messungen und Passwort
- ESP32-Brücke speichert jede Messung im Flash: Zeit, Standort/Kunde, Kommentar und alle Rohregister.
- Neuer Tab „Verlauf“ auf der Brücke: Liste, Ansicht, Löschen, JSON-Export.
- PC-Software übernimmt die Messungen in die Datenbank. Doppelte werden erkannt und übersprungen, die Zeit stammt von NTP, Handy, PC oder der Laufzeit der Brücke.
- Import einer JSON-Exportdatei der Brücke in den Einstellungen.
- Optionaler Passwortschutz (Weboberfläche und PC-Zugriff). BOOT 5 s setzt ihn zurück.

## 1.2 – Status-LED und Härtung
- Onboard-RGB-LED: Pin und Helligkeit sind in der Weboberfläche einstellbar, mit Test-Knopf.
- Lesen mit Wiederholungen, Resync und schnellem Abbruch bei gezogenem Akku; Plausibilitätsprüfung.
- Auslesen im Hintergrund-Task, Mutex für die Akku-Leitung, Watchdog, Heap-Wächter.
- Einstellungen nur noch über das Einrichtungs-WLAN, USB oder nach einem BOOT-Tastendruck änderbar.
- Firmware-Update im Browser.
- J2-Ruhezustand über TX-Invertierung. USB ist sofort nach dem Start bereit.

## 1.1 – ESP32-S3-Brücke
- WLAN-Einrichtung mit Captive Portal, automatische Suche (UDP und mDNS).
- Direktes Auslesen auf der Brücke mit eigener Weboberfläche.
- Die GUI erkennt ESP32-S3 am USB und findet Brücken im WLAN.

## 1.0 – Akku-Verwaltung
- Webbasierte Akku-Verwaltung mit SQLite-Datenbank, Flottenübersicht und Alterungsdiagrammen.
- Warnungen, Standort und Kommentar je Akku.
- QR-Etiketten (P-touch/Brother QL), Berichte, CSV-Export, Demo-Modus.
