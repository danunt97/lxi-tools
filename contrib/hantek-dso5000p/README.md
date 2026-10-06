# dso5000p.py – Hantek DSO5102P am PC (USB)

Ein Kommandozeilen-Tool für die Hantek-Oszilloskope **DSO5072P / DSO5102P / DSO5202P**.
Es spricht direkt das USB-Protokoll des Scopes und braucht dafür keine Hantek-Software.
Es ist ein einzelnes Python-Script, die einzige Abhängigkeit ist `pyusb`.

Was es kann:

| Befehl | Funktion |
|---|---|
| `info` | aktuelle Einstellungen anzeigen (V/div, Zeitbasis, Trigger, Frequenz …) |
| `screenshot` | Bildschirm des Scopes als PNG speichern (800×480, Farbe) |
| `capture` | Kurvendaten beider Kanäle **in Volt** als CSV speichern, plus Vpp/Min/Max/Mittel/RMS |
| `plot` | Kurve einmal holen und anzeigen oder als Bild speichern (PNG/SVG/PDF) |
| `live` | Live-Ansicht am PC (ca. 10 Bilder/s), mit Vpp und Frequenz |
| `monitor` | Datenlogger: Messwerte im festen Intervall in eine CSV schreiben |
| `key` | Tasten am Scope fernbedienen (z. B. `autoset`, `runstop`, `time-up`) |
| `keys` | alle Tastennamen auflisten |
| `lock on/off` | Bedienfeld am Scope sperren oder entsperren |
| `getfile` | Datei vom internen Dateisystem des Scopes lesen |

## Installation

### Linux (empfohlen, am einfachsten)

```sh
sudo apt install python3-pip python3-usb python3-matplotlib   # Debian/Ubuntu/Mint
# oder: pip install pyusb matplotlib

# Damit kein sudo nötig ist: udev-Regel installieren
sudo cp 99-hantek-dso5000p.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
# Scope einmal ab- und wieder anstecken
```

Linux meldet das Scope zuerst als Netzwerkgerät `usb0` an (Treiber `cdc_subset`).
Das Tool löst diesen Treiber automatisch. Ein separater Treiber ist unter Linux nicht nötig.

### Windows

Der mitgelieferte **Hantek-Treiber funktioniert mit diesem Tool nicht**, denn `pyusb` braucht den
generischen **WinUSB**-Treiber:

1. Python installieren (python.org, beim Setup „Add to PATH“ anhaken).
2. `pip install pyusb libusb-package matplotlib`
3. [Zadig](https://zadig.akeo.ie) starten, *Options → List All Devices*, das Gerät mit
   **049F 505A** wählen, als Treiber **WinUSB** einstellen und *Replace Driver* klicken.

Danach läuft die Hantek-Software nicht mehr. Willst du zurück, kannst du im Geräte-Manager
den alten Treiber wiederherstellen („Treiber → Vorheriger Treiber“) oder ihn neu installieren.

Die nötige `libusb-1.0.dll` bringt das Paket `libusb-package` mit. Das Tool nutzt sie
automatisch, wenn keine andere libusb gefunden wird.

## Benutzung

```sh
./dso5000p.py info                       # Einstellungen anzeigen
./dso5000p.py screenshot                 # -> dso-20261006-142233.png
./dso5000p.py screenshot -o bild.png
./dso5000p.py capture                    # CH1+CH2 -> dso-<zeit>.csv  (time_s, ch1_V, ch2_V)
./dso5000p.py capture -c 1 -o messung.csv
./dso5000p.py plot                       # Fenster mit der Kurve
./dso5000p.py plot -o kurve.svg          # als Vektorgrafik speichern
./dso5000p.py live                       # Live-Ansicht
./dso5000p.py live -c 1                  # nur CH1
./dso5000p.py monitor -i 5 -o log.csv    # alle 5 s Vpp/Min/Max/Mittel/RMS/Frequenz loggen
./dso5000p.py key autoset                # AUTOSET drücken
./dso5000p.py key runstop                # RUN/STOP
./dso5000p.py key ch1-volts-up time-down # mehrere Tasten hintereinander
./dso5000p.py lock on                    # Bedienfeld sperren (lock off zum Entsperren)
```

Unter Windows startest du es mit `python dso5000p.py …`.

Beispiel für die Ausgabe von `capture`:

```
3200 Punkte gespeichert: dso-20261006-142233.csv
CH1: Vpp 10.2V  Vmin -5.08V  Vmax 5.12V  Mittel 12.5mV  RMS 5.02V
Frequenz (Scope-Zaehler): 1kHz
```

## Gut zu wissen

- Das Scope liefert pro Kanal **3200 Punkte**, die genau dem sichtbaren Bildschirm entsprechen
  (16 Div × 200 Punkte). Die Zeitachse beginnt am linken Bildrand.
- Die Auflösung ist 8 Bit, das sind 25,6 Stufen pro Division. Für genaue Werte stellst du die
  Kurve am Scope möglichst bildschirmfüllend ein.
- Der Tastkopf-Faktor (x1/x10 …) aus dem Kanalmenü des Scopes wird automatisch eingerechnet.
  Er muss deshalb dort richtig eingestellt sein.
- Steht das Scope auf **STOP**, kommen meist keine Kurvendaten. Abhilfe: `./dso5000p.py key runstop`.
- Ein Screenshot dauert etwa 1 Sekunde.
- Es darf immer nur **ein** Programm auf das Scope zugreifen. Die Hantek-Software oder ein zweites
  Script musst du vorher schließen.

## Tests

Die Tests laufen gegen ein simuliertes Scope, ein echtes Gerät ist dafür nicht nötig:

```sh
python3 -m unittest -v
```

## Quellen und Dank

Das Protokoll wurde von der Community reverse-engineered:

- <https://elinux.org/Das_Oszi_Protocol>
- <https://github.com/titos-carrasco/DSO5102P-Python> (MIT): erste Python-Umsetzung
- <https://github.com/crt0512/xdso>: Settings-Layout, Spannungs-Skalierung (Vorzeichen-Bit, Position)
  und die Timing-Eigenheiten des Scopes
