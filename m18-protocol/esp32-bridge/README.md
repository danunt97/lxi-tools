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
- Den ESP über USB versorgen, **nicht** direkt aus B+ (18–21 V).

## Status-LED (Onboard-RGB-LED)

Die WS2812-LED auf dem Board zeigt den Zustand. Ab Werk ist **GPIO48** eingestellt (DevKitC-1 v1.0 und die meisten
N16R8-Boards); bei DevKitC-1 v1.1 sitzt sie auf **GPIO38**. Pin und Helligkeit stellst du in der Weboberfläche unter
**Gerät → Status-LED** ein, ohne neu zu flashen. „Testen“ lässt die LED rot, grün, blau und weiß leuchten. Leuchtet dabei
nichts, ist der andere Pin der richtige.

| LED | Bedeutung |
|---|---|
| Grün (dauerhaft) | Im WLAN verbunden, bereit |
| Blau blinkend | Einrichtungsmodus (eigenes WLAN „M18-Bridge-XXXX“) |
| Lila | Verbindet gerade mit dem WLAN |
| Gelb pulsierend | Liest den Akku aus |
| Grün, 4 s | Auslesen erfolgreich |
| Rot blinkend, 4 s | Auslesen fehlgeschlagen |
| Weiß blinkend | Der PC liest gerade über die Brücke |
| Türkis blinkend | Einstellungen entsperrt (nach kurzem BOOT-Druck) |
| Lila schnell blinkend | Firmware-Update läuft |
| Rot schnell → rot dauerhaft | BOOT wird gehalten → nach 5 s werden die WLAN-Daten gelöscht |

## Zuverlässigkeit

| Maßnahme | Wirkung |
|---|---|
| J2 sofort beim Start Low, 2,2-kΩ-Pull-Down, Auto-Idle nach 8 s | Keine versehentlich gezählten „Dumb Charges“ |
| Ruhezustand über TX-Invertierung im UART statt UART aus/an | Kein Umhängen von Pins, gleiches Verhalten auf Core 2.x und 3.x |
| WLAN-Aufbau im Hintergrund | USB antwortet sofort nach dem Einschalten, auch während das WLAN verbindet |
| Bis zu 3 Reset-Versuche, Wiederholung und Resync pro Block | Ein verlorenes Byte bricht das Auslesen nicht ab |
| Abbruch nach 3 Blöcken ohne Antwort | Gezogener Akku → Fehlermeldung nach wenigen Sekunden statt Hängen |
| Plausibilitätsprüfung | Unplausible Zellspannungen, fehlende Blöcke und Prüfsummenabweichungen erscheinen als Warnung |
| Auslesen über die Webseite in eigenem Task | Webserver, LED, USB und BOOT-Taste bleiben bedienbar |
| Mutex auf der Akku-Leitung | PC und Webseite können nie gleichzeitig senden. Der andere bekommt `ERR BUSY`, die PC-Software wartet automatisch. |
| Task-Watchdog | Hängt die Hauptschleife, startet der ESP neu. Der Grund steht unter Gerät → „Letzter Neustart“. |
| Heap-Wächter | Bei Speicherknappheit kontrollierter Neustart, wenn gerade nichts läuft |
| WLAN-Stromsparmodus aus, mDNS-Neustart bei neuer IP | Zuverlässige Erreichbarkeit und Suche |
| Asynchrone WLAN-Suche | Der WLAN-Scan blockiert nichts |
| USB: Sende-Timeout 0, Zeilenlänge begrenzt | Kein Hängen, wenn kein PC am USB lauscht |

Hinweis für die Kommandozeile: Nach 8 s ohne Befehl legt die Brücke J2 automatisch auf Low. Wer in der
interaktiven Shell zwischen `m.reset()` und `m.cmd(...)` länger wartet, bekommt `ERR IDLE`, dann einfach erneut `m.reset()`.
`read_id()`, `health()` und die GUI sind davon nicht betroffen.
| PC-Seite: automatische Wiederholung, USB wird nach Reset neu geöffnet | WLAN-Aussetzer oder ein ESP-Neustart brechen einen Lesevorgang nicht ab |

**Sperre für Einstellungen:** WLAN-Daten ändern oder löschen und Firmware aufspielen geht nur
- über das Einrichtungs-WLAN der Brücke, oder
- über USB, oder
- **2 Minuten nach einem kurzen Druck auf die BOOT-Taste** (LED türkis).

So kann niemand aus dem Kunden-WLAN die Brücke umkonfigurieren oder fremde Firmware aufspielen. Auslesen,
Akku-Test und LED-Einstellung bleiben frei.

## Firmware-Update ohne Arduino IDE

1. In der Arduino IDE *Sketch → Kompilierte Binärdatei exportieren*.
   Hochgeladen wird `m18_bridge.ino.bin`, nicht die Datei mit „merged“ oder „bootloader“ im Namen.
2. Am Gerät kurz **BOOT** drücken (LED türkis).
3. In der Weboberfläche unter **Gerät → Firmware-Update** die Datei wählen und auf **Firmware hochladen** klicken. Das Gerät startet danach neu.

Die Partition „16M Flash (3MB APP/9.9MB FATFS)“ hat zwei App-Bereiche. Ein abgebrochenes Update lässt die alte Firmware
lauffähig.

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

Im seriellen Monitor (115200 Baud) erscheint `M18BRIDGE READY 1.3 host=m18-bridge-xxxx.local …`.
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
- **Zurücksetzen:** im Tab WLAN auf „WLAN-Daten löschen“ tippen (dafür vorher kurz BOOT drücken) oder die **BOOT-Taste 5 s gedrückt halten**.
- **Später ein anderes WLAN:** Kurz BOOT drücken und innerhalb von 2 Minuten im Tab WLAN neu verbinden.
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

## Messungen speichern und in die PC-Datenbank übernehmen

**Jede erfolgreiche Messung wird auf der Brücke gespeichert.** Das gilt für das Handy, die Weboberfläche und `SCAN`.
Gespeichert werden Zeitpunkt, optional Standort/Kunde und Kommentar (vor dem Auslesen eingeben), die dekodierten Werte
und **alle Rohregister**. Der Speicher reicht für mehrere tausend Messungen (FAT-Partition, ca. 3 KB pro Messung).
Wird er knapp, werden zuerst die ältesten schon übernommenen Messungen gelöscht.

**Übernehmen in die PC-Datenbank:**
1. `python gui/m18_gui.py` → **Auslesen** → Brücke verbinden (WLAN oder USB).
2. Die Karte **„Auf der Brücke gespeichert“** zeigt alle neuen Messungen. Auswählen und auf **Übernehmen** klicken.
3. Übernommene Messungen sind auf der Brücke als „übernommen“ markiert und werden nicht erneut angeboten.

Was beim Übernehmen passiert:
- Neue Akkus werden automatisch angelegt.
- Bei bekannten Akkus wird der Standort aktualisiert und der Kommentar mit Datum angehängt.
- Doppelte Messungen erkennt das Programm und überspringt sie.
- Warnungen (Zustandsabfall, Imbalance …) entstehen genau wie bei einer Messung am PC.

Ohne Netzwerkverbindung zwischen PC und Brücke: Im Tab **Verlauf** auf **„Alle als JSON exportieren“** tippen (z. B. am Handy),
dann in der PC-Software **Einstellungen → „Messungen der ESP32-Brücke importieren“**. Bereits vorhandene Messungen werden übersprungen.

Kommandozeile:

```bash
python m18_bridge.py --bridge m18-bridge-ab12.local --sync
```

**Zeitstempel ohne Internet:**
- Beim Auslesen über die Webseite schickt das Handy seine Uhrzeit mit.
- Beim Verbinden mit der PC-Software stellt der PC die Uhr der Brücke.
- Fehlt beides, errechnet der PC den Zeitpunkt aus der Laufzeit der Brücke (solange sie seit der Messung nicht neu gestartet ist).
  Sonst nimmt er die Uhr im Akku.

Die PC-Software dekodiert gespeicherte Messungen aus den **Rohregistern mit demselben Code wie eine Live-Messung**.
Für den Alterungsverlauf macht es also keinen Unterschied, ob am PC oder mit dem Handy gemessen wurde.

## Passwortschutz (optional)

In der Weboberfläche unter **Gerät → Zugriffsschutz** setzt du ein Passwort. Dafür vorher kurz BOOT drücken, oder über USB `PASS meinpasswort`.

- Danach fragen Browser **und** PC-Software danach. Der Benutzer ist immer `m18`.
- Die PC-Software merkt sich das Passwort lokal in ihrer Datenbank.
- Ab 8 Zeichen gilt das Passwort auch für das Einrichtungs-WLAN (statt `m18bridge`).
- Fehlversuche werden zunehmend verzögert.
- **Passwort vergessen:** BOOT 5 s halten setzt WLAN-Daten und Passwort zurück.
- Ausschalten: Feld leer lassen und speichern, oder `PASS OFF` über USB.

Über USB ist kein Passwort nötig, denn wer das Kabel einstecken kann, hat das Gerät ohnehin in der Hand.
HTTP-Basic-Auth ist im lokalen Netz unverschlüsselt; als Schutz gegen neugierige Mitbenutzer im Kunden-WLAN reicht es.

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
| `PING` | `OK M18BRIDGE 1.3` | Erkennung |
| `RESET` | `OK ACK` / `ERR NOACK [xx]` | TX 300 ms Low, 300 ms High, Sync 0xAA |
| `IDLE` / `HIGH` | `OK` | TX auf Low halten / UART aktiv |
| `READ <addr> <len>` | `OK <Antwortrahmen hex>` | Register lesen (wie `M18.cmd`) |
| `NOTE <addr> <byte>` | `OK <hex>` | Ein Zeichen der Notiz schreiben, **nur 0x23–0x36** |
| `SCAN` | `OK {json}` | Komplett auslesen, dekodieren, im Verlauf speichern |
| `TIME <unix>` | `OK <jetzt>` | Uhr stellen, falls nicht per NTP gestellt |
| `HSTAT` | `OK {count, unsynced, free_kb, …}` | Speicherstand |
| `HLIST <nach-id> [max] [new\|all]` | `OK {"items":[…],"next":id}` | Gespeicherte Messungen (seitenweise) |
| `HGET <id>` | `OK {"meta":{…},"scan":{…}}` | Eine Messung komplett |
| `HACK <id>` / `HDEL <id>` / `HCLEAR [all]` | `OK` | Als übernommen markieren / löschen / übernommene löschen |
| `PASS <pw>` / `PASS OFF` | `OK` | Passwort setzen (nur USB oder entsperrt) |
| `INFO` | `OK {json}` | Version, WLAN, IP, Speicher … |
| `WIFI ssid\|pass`, `WIFI OFF`, `HOST name`, `RESTART` | `OK …` | Einstellungen |

**HTTP:**

| Pfad | Inhalt |
|---|---|
| `GET /` | Weboberfläche |
| `POST /api/read` (t, loc, comment) | Auslesen starten (läuft im Hintergrund, wird gespeichert) |
| `GET /api/history?after=&max=&filter=new\|all` | Gespeicherte Messungen |
| `GET /api/history/item?id=`, `GET /api/history/stats` | Eine Messung / Speicherstand |
| `POST /api/history/delete` (id), `POST /api/history/clear` | Löschen |
| `POST /api/password` (pass) | Passwort setzen (nur entsperrt) |
| `GET /api/last` | Letzte Messung (JSON) |
| `GET /api/info` | Gerätestatus |
| `GET /api/status` | Läuft ein Auslesen? Fortschritt in % |
| `GET /api/networks` | WLAN-Scan (asynchron, wiederholt abfragen bis `scanning:false`) |
| `POST /api/led` (pin, brightness), `POST /api/led/test` | Status-LED |
| `POST /api/update` | Firmware hochladen (nur entsperrt) |
| `GET /api/wifi`, `POST /api/wifi` (ssid, pass, host) | WLAN-Status / verbinden (nur entsperrt) |
| `POST /api/wifi/forget` | WLAN-Daten löschen (nur entsperrt) |
| `POST /cmd` | Textprotokoll (siehe oben) |

**Auffinden:**

- UDP-Broadcast `M18BRIDGE?` an Port 18018 → Antwort `M18BRIDGE 1.3 host=… ip=… http=80 mac=… auth=0|1 unsynced=N`
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
| `m18_bridge/m18_history.h` | Messungsspeicher (Datei pro Messung, Rotation, Übernahme-Markierung) |
| `m18_bridge/m18_commands.h` | Textprotokoll |
| `m18_bridge/web_ui.h` | Weboberfläche (HTML/JS im Flash, ohne Internet) |
| `test/sim_bridge.cpp` | PC-Test des Protokollkerns mit simuliertem Akku |
| `test/test_bridge.py` | Ende-zu-Ende-Test: `m18_bridge.py`, `m18.py` und GUI-Backend über USB (pty) und HTTP |
| `test/test_history.py` | Speichern → Übernehmen in die Datenbank, Zeitbestimmung, Datei-Import, Passwort |

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
python -m unittest esp32-bridge/test/test_bridge.py esp32-bridge/test/test_history.py -v
```

Die `.h`-Dateien sind reines C++ und laufen gleich auf dem ESP32 und auf dem PC. Der Sketch selbst
(WLAN/Webserver) lässt sich nur mit dem ESP32-Core bauen.
