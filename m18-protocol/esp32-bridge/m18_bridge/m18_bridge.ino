// M18 bridge - ESP32 between the m18 software (PC) and a Milwaukee M18 battery.
//
// Replaces the 3.3 V USB-serial adapter from the m18-protocol README. The ESP32
// speaks the battery protocol itself (see m18_proto.h); the PC talks to the
// ESP32 with simple text commands
//   - over USB  (115200 baud, one command per line), or
//   - over Wi-Fi (HTTP POST /cmd, same commands, one per line).
// m18_bridge.py / the web GUI use this transparently.
//
// Board: any ESP32 (classic, S2, S3, C3) with the Arduino-ESP32 core 2.x or 3.x.
// Arduino IDE: Tools > Board > "ESP32 Dev Module" (or your board), upload.
// No extra libraries needed.
//
// Wiring (same as the USB adapter, see README in this folder):
//   PIN_TX --> level shifter LV1 ... HV1 --> battery J2   (shifter HV side from B+)
//   PIN_RX <-- 1 kOhm ------------------- battery J1
//   GND    ---------------------------------- battery B-
//   2.2 kOhm from PIN_TX to GND keeps J2 low while the ESP32 boots
//   (must beat the 10 kOhm pull-up on the level shifter's LV side).

#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include "m18_proto.h"

// ----------------------------------------------------------------- settings
#ifndef PIN_TX
#define PIN_TX 17          // to level shifter -> J2
#endif
#ifndef PIN_RX
#define PIN_RX 16          // from J1
#endif
#ifndef PIN_LED
#define PIN_LED 2          // on-board LED, -1 = none
#endif
#define HOST_BAUD 115200
#define AP_PASSWORD "m18bridge"      // password of the setup access point (min. 8 chars)
#define AUTO_IDLE_MS 8000            // drop TX low after this much inactivity
#define WIFI_CONNECT_MS 15000

HardwareSerial BatSerial(1);         // UART1 exists on every ESP32 variant
WebServer server(80);
Preferences prefs;

// ------------------------------------------------------------ battery port
class EspPort : public m18::Port {
 public:
  void begin() {
    pinMode(PIN_TX, OUTPUT);
    digitalWrite(PIN_TX, LOW);       // idle: J2 low as early as possible
  }
  void setIdle(bool idle) override {
    if (idle) {
      if (active_) {
        BatSerial.flush();
        BatSerial.end();
        active_ = false;
      }
      pinMode(PIN_TX, OUTPUT);
      digitalWrite(PIN_TX, LOW);
      led(false);
    } else if (!active_) {
      BatSerial.begin(4800, SERIAL_8N2, PIN_RX, PIN_TX);  // TX idles high
      active_ = true;
      led(true);
    }
  }
  void flushInput() override {
    if (active_) while (BatSerial.available()) BatSerial.read();
  }
  void write(const uint8_t* d, size_t n) override {
    if (!active_) return;
    BatSerial.write(d, n);
    BatSerial.flush();
  }
  int readByte(uint32_t timeoutMs) override {
    if (!active_) return -1;
    uint32_t t0 = ::millis();
    do {
      if (BatSerial.available()) return BatSerial.read();
      delay(1);
    } while (::millis() - t0 < timeoutMs);
    return -1;
  }
  void delayMs(uint32_t ms) override { delay(ms); }
  uint32_t millis() override { return ::millis(); }

 private:
  static void led(bool on) {
    if (PIN_LED >= 0) digitalWrite(PIN_LED, on ? HIGH : LOW);
  }
  bool active_ = false;
};

EspPort port;
m18::Link batLink(port);
m18::CommandProcessor proc(batLink);

String wifiMode = "off", ipAddr = "", hostName = "m18-bridge", apName = "";
String infoExtra;

void updateInfo() {
  infoExtra = "\"wifi\":\"" + wifiMode + "\",\"ip\":\"" + ipAddr + "\",\"host\":\"" + hostName + ".local\"";
  if (wifiMode == "ap") infoExtra += ",\"ap\":\"" + apName + "\"";
  if (wifiMode == "sta") infoExtra += ",\"rssi\":" + String(WiFi.RSSI());
  proc.setInfoExtra(infoExtra.c_str());
}

// Handles one command line from USB or HTTP. Wi-Fi settings are handled here
// because they need Preferences; everything else goes to the protocol core.
String runCommand(const String& lineIn) {
  String line = lineIn;
  line.trim();
  String up = line;
  up.toUpperCase();
  if (up.startsWith("WIFI ")) {                       // WIFI <ssid>|<password>   or   WIFI OFF
    String rest = line.substring(5);
    prefs.begin("m18", false);
    if (rest.equalsIgnoreCase("OFF")) {
      prefs.putString("ssid", "");
      prefs.putString("pass", "");
    } else {
      int bar = rest.indexOf('|');
      prefs.putString("ssid", bar < 0 ? rest : rest.substring(0, bar));
      prefs.putString("pass", bar < 0 ? "" : rest.substring(bar + 1));
    }
    prefs.end();
    return "OK saved, restart with RESTART";
  }
  if (up.startsWith("HOST ")) {
    prefs.begin("m18", false);
    prefs.putString("host", line.substring(5));
    prefs.end();
    return "OK saved, restart with RESTART";
  }
  if (up == "RESTART") {
    Serial.println("OK restarting");
    delay(200);
    ESP.restart();
    return "OK";
  }
  updateInfo();
  char reply[200];
  proc.handle(line.c_str(), reply, sizeof(reply));
  return String(reply);
}

// -------------------------------------------------------------------- Wi-Fi
static const char PAGE[] PROGMEM = R"HTML(<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>M18-Brücke</title>
<style>body{font-family:system-ui,sans-serif;max-width:560px;margin:20px auto;padding:0 14px;background:#1b1d20;color:#eceef0}
h1{font-size:20px}h1 b{background:#e4002b;color:#fff;border-radius:6px;padding:2px 7px;margin-right:6px}
.card{background:#23262a;border:1px solid #34383e;border-radius:10px;padding:14px;margin:12px 0}
label{display:block;font-size:13px;color:#959ba3;margin:8px 0 3px}input,textarea{width:100%;box-sizing:border-box;background:#1a1c1f;color:#eceef0;border:1px solid #34383e;border-radius:6px;padding:8px}
button{background:#e4002b;color:#fff;border:0;border-radius:6px;padding:9px 14px;margin-top:10px;cursor:pointer}
pre{background:#1a1c1f;padding:10px;border-radius:6px;white-space:pre-wrap;font-size:12px}small{color:#959ba3}</style></head><body>
<h1><b>M18</b>Akku-Brücke</h1>
<div class="card"><div id="st">…</div><button onclick="test()">Akku testen</button> <small>Reset → Antwort 0xAA?</small><pre id="out"></pre></div>
<div class="card"><b>WLAN</b><form method="post" action="/wifi">
<label>Netzwerk (SSID)</label><input name="ssid" id="ssid"><label>Passwort</label><input name="pass" type="password">
<label>Gerätename (mDNS)</label><input name="host" id="host"><button>Speichern &amp; neu starten</button></form></div>
<div class="card"><b>Befehle</b> <small>PING, RESET, READ 0x400A 10, IDLE …</small><textarea id="cmd" rows="3">RESET
READ 0x400A 10
IDLE</textarea><button onclick="run()">Senden</button><pre id="res"></pre></div>
<script>
async function info(){const j=await (await fetch('/info')).json();
document.getElementById('st').innerHTML='Version '+j.version+' · WLAN: '+(j.wifi=='sta'?'verbunden, '+j.ip+' ('+j.host+')':j.wifi=='ap'?'Einrichtungsmodus ('+j.ap+', '+j.ip+')':'aus')+' · Leitung: '+j.line;
document.getElementById('host').value=j.host.replace('.local','');}
async function send(t){return (await fetch('/cmd',{method:'POST',headers:{'Content-Type':'text/plain'},body:t})).text();}
async function test(){document.getElementById('out').textContent='…';const r=await send('RESET\nIDLE');document.getElementById('out').textContent=r.startsWith('OK ACK')?'✓ Akku antwortet\n'+r:'✗ Keine Antwort – Verkabelung prüfen\n'+r;info();}
async function run(){document.getElementById('res').textContent=await send(document.getElementById('cmd').value);info();}
info();</script></body></html>)HTML";

void handleCmd() {
  String body = server.arg("plain");
  String out;
  int start = 0, count = 0;
  while (start <= (int)body.length() && count < 400) {
    int nl = body.indexOf('\n', start);
    if (nl < 0) nl = body.length();
    String line = body.substring(start, nl);
    line.trim();
    if (line.length()) {
      out += runCommand(line);
      out += '\n';
      count++;
    }
    start = nl + 1;
  }
  server.send(200, "text/plain", out);
}

void handleInfo() {
  updateInfo();
  char reply[200];
  proc.handle("INFO", reply, sizeof(reply));
  server.send(200, "application/json", String(reply + 3));   // strip "OK "
}

void handleWifi() {
  prefs.begin("m18", false);
  prefs.putString("ssid", server.arg("ssid"));
  prefs.putString("pass", server.arg("pass"));
  if (server.arg("host").length()) prefs.putString("host", server.arg("host"));
  prefs.end();
  server.send(200, "text/html; charset=utf-8",
              "<meta charset=utf-8><body style='font-family:sans-serif'>Gespeichert – die Brücke startet neu "
              "und verbindet sich mit dem WLAN. Danach erreichbar unter http://" + hostName + ".local</body>");
  delay(500);
  ESP.restart();
}

void startWifi() {
  prefs.begin("m18", true);
  String ssid = prefs.getString("ssid", "");
  String pass = prefs.getString("pass", "");
  hostName = prefs.getString("host", "m18-bridge");
  prefs.end();

  WiFi.persistent(false);
  WiFi.setHostname(hostName.c_str());
  if (ssid.length()) {
    WiFi.mode(WIFI_STA);
    WiFi.begin(ssid.c_str(), pass.c_str());
    uint32_t t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < WIFI_CONNECT_MS) delay(200);
  }
  if (WiFi.status() == WL_CONNECTED) {
    WiFi.setAutoReconnect(true);
    wifiMode = "sta";
    ipAddr = WiFi.localIP().toString();
  } else {
    // No Wi-Fi configured or not reachable: open a setup access point.
    uint8_t mac[6];
    WiFi.macAddress(mac);
    char name[32];
    snprintf(name, sizeof(name), "M18-Bridge-%02X%02X", mac[4], mac[5]);
    apName = name;
    WiFi.mode(WIFI_AP);
    WiFi.softAP(name, AP_PASSWORD);
    wifiMode = "ap";
    ipAddr = WiFi.softAPIP().toString();
  }
  if (MDNS.begin(hostName.c_str())) MDNS.addService("http", "tcp", 80);

  server.on("/", HTTP_GET, []() { server.send_P(200, "text/html; charset=utf-8", PAGE); });
  server.on("/cmd", HTTP_POST, handleCmd);
  server.on("/info", HTTP_GET, handleInfo);
  server.on("/wifi", HTTP_POST, handleWifi);
  server.begin();
  updateInfo();
}

// --------------------------------------------------------------------- main
String usbLine;

void setup() {
  port.begin();                     // J2 low first - before anything else
  if (PIN_LED >= 0) { pinMode(PIN_LED, OUTPUT); digitalWrite(PIN_LED, LOW); }
  Serial.begin(HOST_BAUD);
  delay(50);
  startWifi();
  Serial.printf("\nM18BRIDGE READY %s wifi=%s ip=%s\n", M18BRIDGE_VERSION, wifiMode.c_str(), ipAddr.c_str());
}

void loop() {
  server.handleClient();

  while (Serial.available()) {
    char ch = (char)Serial.read();
    if (ch == '\n' || ch == '\r') {
      if (usbLine.length()) {
        Serial.println(runCommand(usbLine));
        usbLine = "";
      }
    } else if (usbLine.length() < 120) {
      usbLine += ch;
    }
  }

  // Safety: never leave J2 high when the PC went away mid-session.
  if (!batLink.isIdle() && millis() - batLink.lastActivity() > AUTO_IDLE_MS) batLink.idle();
}
