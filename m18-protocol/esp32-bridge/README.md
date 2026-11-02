# ESP32-Brücke für M18-Akkus

Ein ESP32 übernimmt die Rolle des 3,3-V-USB-Seriell-Adapters. Er spricht das Akku-Protokoll selbst:

- 4800 Baud, 8N2
- bitgespiegelte Bytes
- Reset über TX-Low/High mit Sync auf 0xAA
- Leseabfragen mit Prüfsumme

Zum PC hin bietet er ein einfaches Textprotokoll, das auf zwei Wegen erreichbar ist:

- **USB**: ESP32 per USB am PC, 115200 Baud
- **WLAN**: HTTP im Heimnetz (z. B. `http://m18-bridge.local`). Der PC muss nicht neben dem Akku stehen.

`m18_bridge.py` stellt die Brücke wie ein normales `m18.M18`-Objekt bereit. Damit funktionieren
`read_id()`, `health()`, `write_message()` und die **Web-GUI** unverändert. In der GUI unter
**Auslesen** wählst du „ESP32-Brücke (USB)“ oder „ESP32-Brücke (WLAN)“.

```
 PC (GUI / m18.py) ──USB oder WLAN──▶ ESP32 ──UART 4800 8N2──▶ Pegelwandler ──▶ Akku J2
                                         ◀───────────────────────────────────── Akku J1
```

## Verdrahtung

Sie entspricht dem USB-Adapter aus dem Haupt-README (siehe `../docs/wiring.png`). Statt `TXD`/`RXD`
des Adapters nimmst du die ESP32-Pins:

| ESP32 | Weg | Akku |
|---|---|---|
| `GPIO17` (TX) | Pegelwandler LV1 → HV1 | **J2** |
| `GPIO16` (RX) | über 1 kΩ | **J1** |
| `3V3` | Pegelwandler LV | – |
| – | Pegelwandler HV | **B+** (wie im Original-Schaltplan) |
| `GND` | Pegelwandler GND | **B−** |
| `GPIO17` | **2,2 kΩ nach GND** | hält J2 beim Booten auf Low |

Wichtig:

- **J2 muss Low sein, solange nicht kommuniziert wird.** Liegt J2 länger als ca. 0,5 s auf High, zählt der Akku
  eine „Dumb Charge“. Die Firmware zieht TX deshalb als Allererstes auf Low und fällt nach 8 s ohne Befehl
  automatisch zurück auf Low. Der 2,2-kΩ-Pull-Down überbrückt die Bootphase, in der der Pin noch offen ist.
  Er muss deutlich kleiner sein als der 10-kΩ-Pull-Up auf der LV-Seite des Pegelwandlers.
- Den ESP32 über USB versorgen (PC oder USB-Netzteil), **nicht** direkt aus B+ (18–21 V).
- **ESP32-WROVER**: GPIO16/17 sind dort für den PSRAM belegt. Andere Pins wählen, z. B. 26/27 (siehe unten).
  ESP32-C3: z. B. TX 5 / RX 4. ESP32-S3: 17/18 passen.
- Mit `m18_idle.bat` bzw. der Weboberfläche („Akku testen“) prüfen: Im Idle muss J2 unter 1 V liegen.

Pins und LED stehen oben im Sketch (`PIN_TX`, `PIN_RX`, `PIN_LED`).

## Flashen (Arduino IDE 2)

1. *Datei → Einstellungen → Zusätzliche Boardverwalter-URLs*:
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`
2. *Boardverwalter*: „esp32 by Espressif Systems“ installieren (Version 2.x oder 3.x).
3. `esp32-bridge/m18_bridge/m18_bridge.ino` öffnen. `m18_proto.h` liegt im selben Ordner und wird automatisch mitkompiliert.
4. Board wählen (z. B. „ESP32 Dev Module“), Port wählen, **Hochladen**.

Zusätzliche Bibliotheken sind nicht nötig.

Im seriellen Monitor (115200 Baud) erscheint `M18BRIDGE READY 1.0 …`. Ein Test: `PING`, dann `RESET`
(mit angeschlossenem Akku sollte `OK ACK` kommen), dann `IDLE`.

## WLAN einrichten

- **Über das Handy oder den Laptop:** Beim ersten Start öffnet die Brücke das WLAN **`M18-Bridge-XXXX`** mit dem Passwort `m18bridge`.
  Verbinde dich damit und öffne http://192.168.4.1. Dort trägst du Heim-WLAN und Gerätenamen ein und klickst „Speichern“.
  Danach ist die Brücke unter **http://m18-bridge.local** erreichbar (oder über ihre IP, die im seriellen Monitor steht).
- **Über USB:** Im seriellen Monitor `WIFI MeinWLAN|MeinPasswort` eingeben, dann `RESTART`.
  `WIFI OFF` löscht die Daten wieder, `HOST werkstatt-akku` ändert den Namen.

Die Weboberfläche der Brücke zeigt den Status, hat einen Knopf „Akku testen“ und eine Befehlskonsole.

Hinweis: Die Brücke hat keine Anmeldung. Jeder im selben WLAN kann den Akku auslesen und die Notiz schreiben, aber nichts anderes.

## Benutzung

**Web-GUI:** `python gui/m18_gui.py` starten, dann unter **Auslesen** den Adapter wählen:
- „ESP32-Brücke (USB)“ und dazu den COM-Port, oder
- „ESP32-Brücke (WLAN)“ und dazu `m18-bridge.local` bzw. die IP.

Danach **Verbinden** und **Akku auslesen**.

**Kommandozeile:**

```bash
python m18_bridge.py --bridge m18-bridge.local --health
```

```bash
python m18_bridge.py --bridge COM7
```

Der zweite Aufruf öffnet eine interaktive Shell mit `m.health()`, `m.read_id()` und `m.write_message("A-07")`.

Für WLAN braucht der PC nur die Python-Standardbibliothek. Für USB wird `pyserial` benötigt.

## Textprotokoll

Jeweils ein Befehl pro Zeile, eine Antwortzeile pro Befehl. Zahlen können dezimal oder hex (`0x…`) sein.

| Befehl | Antwort | Bedeutung |
|---|---|---|
| `PING` | `OK M18BRIDGE 1.0` | Erkennung |
| `RESET` | `OK ACK` / `ERR NOACK [xx]` | TX 300 ms Low, 300 ms High, Sync 0xAA |
| `IDLE` / `HIGH` | `OK` | TX auf Low halten / UART aktiv |
| `READ <addr> <len>` | `OK <Antwortrahmen hex>` / `ERR TIMEOUT` | Register lesen (wie `M18.cmd`) |
| `NOTE <addr> <byte>` | `OK <hex>` | Ein Zeichen der Notiz schreiben, **nur 0x23–0x36** |
| `INFO` | `OK {json}` | Version, Leitungszustand, WLAN |
| `WIFI ssid\|pass`, `WIFI OFF`, `HOST name`, `RESTART` | `OK …` | Einstellungen |

Über WLAN schickst du dieselben Zeilen als `POST /cmd` (Content-Type `text/plain`) und erhältst die Antworten
zeilenweise zurück. `GET /info` liefert JSON.

Bewusst eingeschränkt: Die Brücke kann nur **lesen** und die **Notiz** schreiben – so wie `m18.py` mit
`read_id()`/`write_message()`. Zähler, Kalibrierung oder Firmware des Akkus fasst sie nicht an.

## Tests ohne Hardware

`m18_proto.h` ist reines C++ und wird auf dem PC zusammen mit einem simulierten Akku getestet.

**Selbsttest des Protokollkerns:**

```bash
g++ -std=c++17 -I esp32-bridge/m18_bridge esp32-bridge/test/sim_bridge.cpp -o sim_bridge
```

```bash
./sim_bridge test
```

**Ende-zu-Ende-Test:** Er prüft `m18_bridge.py`, `m18.py` (`read_id`) und das GUI-Backend über USB (pty) und HTTP:

```bash
python -m unittest esp32-bridge/test/test_bridge.py -v
```
