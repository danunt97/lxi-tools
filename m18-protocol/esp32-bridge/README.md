# M18 Akku-Brücke (ESP32-S3)

Ein **ESP32-S3-N16R8** sitzt zwischen dem Akku und der PC-Software und ersetzt den 3,3-V-USB-Seriell-Adapter.

- **Liest den Akku selbst aus** und zeigt die Werte auf seiner eigenen Weboberfläche: Zustand, Zellspannungen, Zähler und Daten. Dafür braucht es keinen PC, ein Handy genügt.
- **WLAN-Einrichtung beim Kunden** läuft über die Weboberfläche. Beim ersten Start öffnet der ESP ein eigenes WLAN, und auf dem Handy erscheint die Einrichtungsseite automatisch (Captive Portal).
- **Die PC-Software findet die Brücke automatisch**, entweder im WLAN (Knopf „Suchen“) oder am USB-Port (Espressif-USB wird erkannt). Alternativ geht `http://<name>.local`.
- **Die PC-Datenbank (Web-GUI)** liest über die Brücke alle 184 Register mit dem unveränderten `m18.py`, per USB oder WLAN.

```
                  ┌────────── Handy/Browser: http://m18-bridge-xxxx.local (Auslesen, WLAN-Einrichtung)
                  │
 PC (GUI) ──USB oder WLAN──▶ ESP32-S3 ──UART 4800 8N2──▶ Pegelwandler ──▶ Akku J2
                               ▲                                              │
                               └────────────────── 1 kΩ ◀──────────────────── Akku J1
```

## Verdrahtung (ESP32-S3-DevKitC-1 N16R8)

| ESP32-S3 | Weg | Akku |
|---|---|---|
| `GPIO17` (TX) | Pegelwandler LV1 → HV1 | **J2** |
| `GPIO18` (RX) | über 1 kΩ | **J1** |
| `3V3` | Pegelwandler LV | – |
| – | Pegelwandler HV | **B+** (wie im Original-Schaltplan `../docs/wiring.png`) |
| `GND` | Pegelwandler GND | **B−** |
| `GPIO17` | **2,2 kΩ nach GND** | hält J2 beim Booten auf Low |

- **J2 muss Low sein, solange nicht kommuniziert wird.** Liegt J2 länger als ca. 0,5 s auf High, zählt der Akku
  eine „Dumb Charge“. Die Firmware zieht TX deshalb als Allererstes auf Low und nach 8 s ohne Befehl automatisch wieder.
  Der 2,2-kΩ-Pull-Down überbrückt die Bootphase. Er muss kleiner sein als der 10-kΩ-Pull-Up auf der LV-Seite des Pegelwandlers.
- Beim **N16R8** belegt der Octal-PSRAM die Pins GPIO35–37, und GPIO26–32 gehören dem Flash. Diese Pins nicht verwenden.
  17/18 sind frei. Andere Pins stellst du oben im Sketch ein (`PIN_TX`, `PIN_RX`).
- Die **RGB-LED** sitzt auf GPIO48 (DevKitC-1 v1.0). Bei v1.1 ist es GPIO38; dann `PIN_RGB` anpassen.
  - Grün: im WLAN
  - Blau blinkend: Einrichtungsmodus
  - Lila: verbindet gerade
  - Weiß blinkend: Akku-Leitung aktiv
- Den ESP über USB versorgen, **nicht** direkt aus B+ (18–21 V).

## Flashen (Arduino IDE 2)

1. *Datei → Einstellungen → Zusätzliche Boardverwalter-URLs*:
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`
2. *Boardverwalter*: „esp32 by Espressif Systems“ installieren (Version 3.x empfohlen, 2.0.x geht auch).
3. `esp32-bridge/m18_bridge/m18_bridge.ino` öffnen. Die `.h`-Dateien im selben Ordner werden mitkompiliert.
4. Unter *Werkzeuge* einstellen:

   | Einstellung | Wert |
   |---|---|
   | Board | **ESP32S3 Dev Module** |
   | Flash Size | **16MB (128Mb)** |
   | PSRAM | **OPI PSRAM** |
   | Partition Scheme | **16M Flash (3MB APP/9.9MB FATFS)** |
   | USB CDC On Boot | **Enabled** |
   | USB Mode | Hardware CDC and JTAG |

5. Board an die Buchse **„USB“** anschließen, Port wählen und **Hochladen** klicken.

Mit „USB CDC On Boot: Enabled“ funktionieren **beide** USB-Buchsen des DevKits für die PC-Software:
„USB“ (nativ) und „UART“ (CH343). Zusätzliche Bibliotheken sind nicht nötig.

Im seriellen Monitor (115200 Baud) erscheint `M18BRIDGE READY 1.1 host=m18-bridge-xxxx.local …`.
Ein Schnelltest: `PING`, dann `SCAN` (liest den Akku komplett aus und gibt JSON zurück).

## WLAN einrichten (beim Kunden)

1. Brücke einschalten. Ohne eingerichtetes WLAN öffnet sie das WLAN **`M18-Bridge-XXXX`** mit dem Passwort **`m18bridge`**.
2. Mit dem Handy verbinden. Die Einrichtungsseite öffnet sich automatisch; sonst http://192.168.4.1 aufrufen.
3. **Netzwerke suchen** → Kunden-WLAN antippen → Passwort eingeben → bei Bedarf den Gerätenamen ändern → **Verbinden**.
4. Die Seite zeigt **„Verbunden!“** mit Adresse (`http://m18-bridge-xxxx.local`) und IP. Der Einrichtungs-Hotspot
   schließt sich nach 90 s. Danach das Handy wieder ins normale WLAN bringen.

Weitere Fälle:

- **Gespeichertes WLAN nicht erreichbar** (z. B. Router aus): Nach 60 s öffnet die Brücke wieder den
  Einrichtungs-Hotspot. Solange niemand damit verbunden ist, versucht sie jede Minute, das gespeicherte WLAN zu erreichen.
- **Zurücksetzen:** im Tab WLAN auf „WLAN-Daten löschen“ tippen oder die **BOOT-Taste 5 s gedrückt halten**.
- **Über USB:** `WIFI MeinWLAN|MeinPasswort`, `HOST werkstatt-akku`, `RESTART`

## Akku direkt auf der Brücke auslesen

`http://m18-bridge-xxxx.local` öffnen → Tab **Akku** → **Akku auslesen** (ca. 7 s). Angezeigt werden:

- Zustand (Schätzung, gleiche Formel wie die PC-Datenbank) mit den einzelnen Abzügen
- Modell, E-Seriennummer, Notiz
- Pack-Spannung, Temperatur, Imbalance, Zellspannungen
- Vollzyklen, entladene Ah/Wh, Ladungen, Überhitzung, Unterspannung, Ladung bei Zelle < 2,5 V
- Herstell- und Ladedaten, Ladezeit, Zeit am Werkzeug

Die letzte Messung bleibt bis zum Neustart erhalten und ist als JSON abrufbar (`/api/last`).

Das Alter rechnet die Brücke mit der Internetzeit (NTP), ohne Internet mit der Uhr im Akku.

Für den **Verlauf und die Alterung über Zeit** gehört der Akku in die PC-Datenbank. Die Brücke zeigt nur die aktuelle Messung.

## Mit der PC-Software (Web-GUI) verwenden

`python gui/m18_gui.py` → **Auslesen** → Adapter wählen:

- **ESP32-Brücke (WLAN)**: Die GUI sucht automatisch. Ein Klick auf **Suchen** schickt einen UDP-Broadcast auf Port 18018,
  und alle Brücken im Netz melden sich. Alternativ trägst du `m18-bridge-xxxx.local` oder die IP direkt ein.
- **ESP32-Brücke (USB)**: Ein angeschlossener ESP32-S3 ist im Port-Menü als „ESP32-S3 (M18-Brücke)“ markiert und
  wird automatisch vorgewählt.

Danach **Verbinden** → **Akku auslesen** → speichern. Für WLAN braucht der PC nur die Python-Standardbibliothek,
für USB zusätzlich `pyserial`.

Sollte die Suche nichts finden (Firewall, Gäste-WLAN mit Client-Isolation), in der Windows-Firewall Python
für „Private Netzwerke“ erlauben oder die Adresse direkt eintragen.

**Kommandozeile:**

```bash
python m18_bridge.py --discover
```

```bash
python m18_bridge.py --bridge m18-bridge-ab12.local --health
```

```bash
python m18_bridge.py --bridge COM7
```

Die erste Zeile sucht Brücken im Netz. Die zweite gibt einen Health-Report aus. Die dritte öffnet eine interaktive Shell mit `m.scan()`, `m.health()` und `m.read_id()`.

## Schnittstellen

**Textprotokoll** (USB 115200 Baud oder `POST /cmd`, eine Zeile pro Befehl, eine Antwortzeile pro Befehl):

| Befehl | Antwort | Bedeutung |
|---|---|---|
| `PING` | `OK M18BRIDGE 1.1` | Erkennung |
| `RESET` | `OK ACK` / `ERR NOACK [xx]` | TX 300 ms Low, 300 ms High, Sync 0xAA |
| `IDLE` / `HIGH` | `OK` | TX auf Low halten / UART aktiv |
| `READ <addr> <len>` | `OK <Antwortrahmen hex>` | Register lesen (wie `M18.cmd`) |
| `NOTE <addr> <byte>` | `OK <hex>` | Ein Zeichen der Notiz schreiben, **nur 0x23–0x36** |
| `SCAN` | `OK {json}` | Komplett auslesen und dekodieren |
| `INFO` | `OK {json}` | Version, WLAN, IP, Speicher … |
| `WIFI ssid\|pass`, `WIFI OFF`, `HOST name`, `RESTART` | `OK …` | Einstellungen |

**HTTP:**

| Pfad | Inhalt |
|---|---|
| `GET /` | Weboberfläche |
| `POST /api/read` | Akku auslesen (JSON) |
| `GET /api/last` | Letzte Messung (JSON) |
| `GET /api/info` | Gerätestatus |
| `GET /api/networks` | WLAN-Scan |
| `GET /api/wifi`, `POST /api/wifi` (ssid, pass, host) | WLAN-Status / verbinden |
| `POST /api/wifi/forget` | WLAN-Daten löschen |
| `POST /cmd` | Textprotokoll (siehe oben) |

**Auffinden:**

- UDP-Broadcast `M18BRIDGE?` an Port 18018 → Antwort `M18BRIDGE 1.1 host=… ip=… http=80 mac=…`
- mDNS `<name>.local`, Dienste `_http._tcp` und `_m18bridge._tcp`

Bewusst eingeschränkt: Die Brücke kann nur **lesen** und die **Notiz** schreiben – so wie `m18.py` mit
`read_id()`/`write_message()`. Zähler, Kalibrierung oder Firmware des Akkus fasst sie nicht an.
Die Weboberfläche hat keine Anmeldung. Jeder im selben WLAN kann den Akku auslesen.

## Dateien

| Datei | Inhalt |
|---|---|
| `m18_bridge/m18_bridge.ino` | Sketch: UART, USB, WLAN, Captive Portal, mDNS/UDP-Suche, Webserver, LED, BOOT-Taste |
| `m18_bridge/m18_proto.h` | Akku-Protokoll (Reset/Sync, Rahmen, Prüfsumme, Bitspiegelung) |
| `m18_bridge/m18_reader.h` | Direktes Auslesen und Dekodieren, Zustandsschätzung |
| `m18_bridge/m18_commands.h` | Textprotokoll |
| `m18_bridge/web_ui.h` | Weboberfläche (HTML/JS im Flash, ohne Internet) |
| `test/sim_bridge.cpp` | PC-Test des Protokollkerns mit simuliertem Akku |
| `test/test_bridge.py` | Ende-zu-Ende-Test: `m18_bridge.py`, `m18.py` und GUI-Backend über USB (pty) und HTTP |

## Tests ohne Hardware

**Selbsttest des Protokollkerns:**

```bash
g++ -std=c++17 -I esp32-bridge/m18_bridge esp32-bridge/test/sim_bridge.cpp -o sim_bridge
```

```bash
./sim_bridge test
```

**Ende-zu-Ende-Test:**

```bash
python -m unittest esp32-bridge/test/test_bridge.py -v
```

Die `.h`-Dateien sind reines C++ und laufen gleich auf dem ESP32 und auf dem PC. Der Sketch selbst
(WLAN/Webserver) lässt sich nur mit dem ESP32-Core bauen.
