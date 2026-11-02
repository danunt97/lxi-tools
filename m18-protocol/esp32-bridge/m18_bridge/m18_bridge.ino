// M18 Akku-Brücke - ESP32-S3 (N16R8) between the m18 software and a Milwaukee M18 battery.
//
//  - Speaks the battery protocol itself (m18_proto.h) and replaces the
//    USB-serial adapter of m18-protocol.
//  - PC access over USB (115200 baud, text commands) or Wi-Fi (HTTP POST /cmd);
//    m18_bridge.py / the web GUI use this transparently.
//  - Own web interface: Wi-Fi setup in the customer's network (captive portal),
//    direct battery read-out with decoded values (m18_reader.h), status, console.
//  - The PC finds the bridge on the network automatically (UDP discovery on
//    port 18018 + mDNS <name>.local / _m18bridge._tcp).
//
// Arduino IDE settings for ESP32-S3-DevKitC-1 N16R8 (see README):
//   Board "ESP32S3 Dev Module", Flash Size 16MB, PSRAM "OPI PSRAM",
//   Partition Scheme "16M Flash (3MB APP/9.9MB FATFS)", USB CDC On Boot "Enabled".
//   Arduino-ESP32 core 2.0.x or 3.x, no extra libraries.

#include <WiFi.h>
#include <WiFiUdp.h>
#include <WebServer.h>
#include <DNSServer.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include <time.h>
#include "m18_commands.h"
#include "web_ui.h"

// ----------------------------------------------------------------- settings
#ifndef PIN_TX
#define PIN_TX 17          // -> level shifter -> battery J2
#endif
#ifndef PIN_RX
#define PIN_RX 18          // <- 1 kOhm <- battery J1
#endif
#ifndef PIN_RGB
#define PIN_RGB 48         // on-board RGB LED (DevKitC-1 v1.0: 48, v1.1: 38), -1 = none
#endif
#ifndef PIN_BOOT
#define PIN_BOOT 0         // BOOT button: hold 5 s = forget Wi-Fi
#endif
#define HOST_BAUD 115200
#define AP_PASSWORD "m18bridge"        // setup access point password (min. 8 chars)
#define DISCOVERY_PORT 18018
#define AUTO_IDLE_MS 8000              // TX low after this much inactivity
#define STA_CONNECT_MS 20000
#define AP_CLOSE_AFTER_MS 90000        // keep setup AP open this long after success
#define AP_FALLBACK_MS 60000           // open setup AP after this long without Wi-Fi
#define STA_RETRY_MS 60000             // retry saved Wi-Fi while in setup mode

HardwareSerial BatSerial(1);
WebServer server(80);
DNSServer dns;
WiFiUDP disc;
Preferences prefs;

// ------------------------------------------------------------ battery port
class EspPort : public m18::Port {
 public:
  void begin() {
    pinMode(PIN_TX, OUTPUT);
    digitalWrite(PIN_TX, LOW);         // J2 low as early as possible
  }
  void setIdle(bool idle) override {
    if (idle) {
      if (active_) { BatSerial.flush(); BatSerial.end(); active_ = false; }
      pinMode(PIN_TX, OUTPUT);
      digitalWrite(PIN_TX, LOW);
    } else if (!active_) {
      BatSerial.begin(4800, SERIAL_8N2, PIN_RX, PIN_TX);   // TX idles high
      active_ = true;
    }
  }
  void flushInput() override { if (active_) while (BatSerial.available()) BatSerial.read(); }
  void write(const uint8_t* d, size_t n) override { if (active_) { BatSerial.write(d, n); BatSerial.flush(); } }
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
  bool active_ = false;
};

EspPort port;
m18::Link batLink(port);
m18::Reader reader(batLink, port);
m18::CommandProcessor proc(batLink, reader);

static uint32_t unixNow() {
  time_t t = time(nullptr);
  return t > 1700000000 ? (uint32_t)t : 0;   // 0 = not synced, reader then uses the battery clock
}

// ------------------------------------------------------------------- LED
static void led(uint8_t r, uint8_t g, uint8_t b) {
#if PIN_RGB >= 0
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
  rgbLedWrite(PIN_RGB, r, g, b);
#else
  neopixelWrite(PIN_RGB, r, g, b);
#endif
#endif
}

// ------------------------------------------------------------------ Wi-Fi
enum class WState { AP, CONNECTING, STA, FAILED };
WState wstate = WState::AP;
String savedSsid, savedPass, hostName, apName, wifiError;
String pendSsid, pendPass;
bool apActive = false, dnsActive = false, portalConnect = false;
uint32_t connectStart = 0, apCloseAt = 0, lostSince = 0, lastStaRetry = 0;
String lastScan = "{}";
String infoExtra;
static char replyBuf[2400];

String jsonEsc(String v) {
  v.replace("\\", "\\\\");
  v.replace("\"", "\\\"");
  return v;
}

String ipString() {
  if (WiFi.status() == WL_CONNECTED) return WiFi.localIP().toString();
  return apActive ? WiFi.softAPIP().toString() : String("");
}

void loadPrefs() {
  prefs.begin("m18", true);
  savedSsid = prefs.getString("ssid", "");
  savedPass = prefs.getString("pass", "");
  hostName = prefs.getString("host", "");
  prefs.end();
  uint8_t mac[6];
  WiFi.macAddress(mac);
  char suffix[8];
  snprintf(suffix, sizeof(suffix), "%02x%02x", mac[4], mac[5]);
  if (hostName.isEmpty()) hostName = String("m18-bridge-") + suffix;
  String up = suffix;
  up.toUpperCase();
  apName = String("M18-Bridge-") + up;
}

void savePrefs(const String& ssid, const String& pass) {
  prefs.begin("m18", false);
  prefs.putString("ssid", ssid);
  prefs.putString("pass", pass);
  prefs.putString("host", hostName);
  prefs.end();
  savedSsid = ssid;
  savedPass = pass;
}

void startAP() {
  if (apActive) return;
  WiFi.mode(savedSsid.length() ? WIFI_AP_STA : WIFI_AP);
  WiFi.softAP(apName.c_str(), AP_PASSWORD);
  apActive = true;
  dns.start(53, "*", WiFi.softAPIP());   // captive portal: every name -> us
  dnsActive = true;
  apCloseAt = 0;
  if (wstate != WState::CONNECTING) wstate = WState::AP;
  Serial.printf("Setup-WLAN \"%s\" (Passwort %s) -> http://%s\n", apName.c_str(), AP_PASSWORD,
                WiFi.softAPIP().toString().c_str());
}

void stopAP() {
  if (!apActive) return;
  if (dnsActive) { dns.stop(); dnsActive = false; }
  WiFi.softAPdisconnect(true);
  WiFi.mode(WIFI_STA);
  apActive = false;
  apCloseAt = 0;
}

void beginConnect(const String& ssid, const String& pass) {
  WiFi.mode(apActive ? WIFI_AP_STA : WIFI_STA);
  WiFi.setHostname(hostName.c_str());
  WiFi.begin(ssid.c_str(), pass.c_str());
  wstate = WState::CONNECTING;
  connectStart = millis();
  wifiError = "";
}

void onConnected() {
  wstate = WState::STA;
  lostSince = 0;
  WiFi.setAutoReconnect(true);
  configTime(0, 0, "pool.ntp.org", "time.google.com");
  if (portalConnect) {                 // credentials entered on the web page: keep them now
    savePrefs(pendSsid, pendPass);
    portalConnect = false;
  }
  if (apActive) apCloseAt = millis() + AP_CLOSE_AFTER_MS;
  Serial.printf("WLAN verbunden: %s, http://%s.local (%s)\n", WiFi.SSID().c_str(), hostName.c_str(),
                WiFi.localIP().toString().c_str());
}

void wifiLoop() {
  wl_status_t st = WiFi.status();
  if (wstate == WState::CONNECTING) {
    if (st == WL_CONNECTED) onConnected();
    else if (millis() - connectStart > STA_CONNECT_MS) {
      bool fromPortal = portalConnect;          // entered on the web page vs. quiet background retry
      wstate = fromPortal ? WState::FAILED : WState::AP;
      if (fromPortal)
        wifiError = (st == WL_NO_SSID_AVAIL) ? "Netzwerk nicht gefunden" : "Verbindung fehlgeschlagen – Passwort prüfen";
      WiFi.setAutoReconnect(false);
      WiFi.disconnect();
      portalConnect = false;
      if (!apActive) startAP();
    }
  } else if (wstate == WState::STA) {
    if (st != WL_CONNECTED) {
      if (!lostSince) lostSince = millis();
      if (millis() - lostSince > AP_FALLBACK_MS && !apActive) {   // network gone: allow reconfiguration
        WiFi.setAutoReconnect(false);
        startAP();
        wstate = WState::AP;
        lastStaRetry = millis();
      }
    } else {
      lostSince = 0;
    }
  } else if (savedSsid.length() && apActive && WiFi.softAPgetStationNum() == 0 &&
             millis() - lastStaRetry > STA_RETRY_MS) {
    // In setup mode with saved credentials: retry quietly while nobody uses the setup AP.
    lastStaRetry = millis();
    beginConnect(savedSsid, savedPass);
  }
  if (apCloseAt && (int32_t)(millis() - apCloseAt) > 0 && WiFi.status() == WL_CONNECTED) stopAP();
}

const char* stateName() {
  switch (wstate) {
    case WState::CONNECTING: return "connecting";
    case WState::STA: return WiFi.status() == WL_CONNECTED ? "connected" : "connecting";
    case WState::FAILED: return "failed";
    default: return "ap";
  }
}

void updateInfo() {
  bool sta = WiFi.status() == WL_CONNECTED;
  infoExtra = String("\"wifi\":\"") + (sta ? "sta" : "ap") + "\",\"ip\":\"" + ipString() +
              "\",\"host\":\"" + hostName + ".local\",\"ssid\":\"" + jsonEsc(sta ? WiFi.SSID() : savedSsid) +
              "\",\"ap\":\"" + (apActive ? apName : String("")) + "\",\"ap_ip\":\"" +
              (apActive ? WiFi.softAPIP().toString() : String("")) + "\",\"rssi\":" + String(sta ? WiFi.RSSI() : 0) +
              ",\"mac\":\"" + WiFi.macAddress() + "\",\"uptime_s\":" + String(millis() / 1000) +
              ",\"heap_kb\":" + String(ESP.getFreeHeap() / 1024) + ",\"psram_kb\":" + String(ESP.getFreePsram() / 1024);
  uint32_t now = unixNow();
  if (now) {
    char b[32];
    time_t t = now;
    struct tm g;
    gmtime_r(&t, &g);
    strftime(b, sizeof(b), "%Y-%m-%d %H:%M UTC", &g);
    infoExtra += String(",\"time\":\"") + b + "\"";
  }
  proc.setInfoExtra(infoExtra.c_str());
}

// ------------------------------------------------------------- commands
String runCommand(const String& lineIn) {
  String line = lineIn;
  line.trim();
  String up = line;
  up.toUpperCase();
  if (up.startsWith("WIFI ")) {                 // WIFI <ssid>|<password>  or  WIFI OFF
    String rest = line.substring(5);
    if (rest.equalsIgnoreCase("OFF")) savePrefs("", "");
    else {
      int bar = rest.indexOf('|');
      savePrefs(bar < 0 ? rest : rest.substring(0, bar), bar < 0 ? "" : rest.substring(bar + 1));
    }
    return "OK saved, restart with RESTART";
  }
  if (up.startsWith("HOST ")) {
    hostName = line.substring(5);
    savePrefs(savedSsid, savedPass);
    return "OK saved, restart with RESTART";
  }
  if (up == "RESTART") {
    Serial.println("OK restarting");
    delay(200);
    ESP.restart();
    return "OK";
  }
  updateInfo();
  bool scan = (up == "SCAN");
  if (scan) led(20, 14, 0);                     // yellow while reading
  proc.handle(line.c_str(), replyBuf, sizeof(replyBuf));
  if (scan) {
    const char* json = strchr(replyBuf, '{');
    lastScan = json ? String(json) : String("{}");
  }
  return String(replyBuf);
}

// ------------------------------------------------------------------- HTTP
void sendJson(const String& s) { server.send(200, "application/json", s); }

bool captiveRedirect() {
  // Phones probe random URLs when joining the setup AP; send them to our page.
  // Only for clients on the setup AP - not for the customer's network.
  if (!apActive || server.client().localIP() != WiFi.softAPIP()) return false;
  String host = server.hostHeader();
  if (host == WiFi.softAPIP().toString() || host == hostName + ".local") return false;
  server.sendHeader("Location", String("http://") + WiFi.softAPIP().toString() + "/", true);
  server.send(302, "text/plain", "");
  return true;
}

void setupHttp() {
  server.on("/", HTTP_GET, []() {
    if (captiveRedirect()) return;
    server.send_P(200, "text/html; charset=utf-8", WEB_UI);
  });
  server.on("/cmd", HTTP_POST, []() {
    String body = server.arg("plain"), out;
    int start = 0, count = 0;
    while (start <= (int)body.length() && count < 400) {
      int nl = body.indexOf('\n', start);
      if (nl < 0) nl = body.length();
      String line = body.substring(start, nl);
      line.trim();
      if (line.length()) { out += runCommand(line); out += '\n'; count++; }
      start = nl + 1;
    }
    server.send(200, "text/plain", out);
  });
  server.on("/api/info", HTTP_GET, []() {
    updateInfo();
    proc.handle("INFO", replyBuf, sizeof(replyBuf));
    sendJson(String(replyBuf + 3));
  });
  server.on("/api/read", HTTP_POST, []() {
    String r = runCommand("SCAN");
    int brace = r.indexOf('{');
    sendJson(brace >= 0 ? r.substring(brace) : String("{\"ok\":false}"));
  });
  server.on("/api/last", HTTP_GET, []() { sendJson(lastScan); });
  server.on("/api/networks", HTTP_GET, []() {
    int n = WiFi.scanNetworks(false, false);
    String out = "[";
    for (int i = 0; i < n && i < 25; i++) {
      String ssid = WiFi.SSID(i);
      if (ssid.isEmpty()) continue;
      if (out.length() > 1) out += ",";
      out += "{\"ssid\":\"" + jsonEsc(ssid) + "\",\"rssi\":" + String(WiFi.RSSI(i)) +
             ",\"secure\":" + (WiFi.encryptionType(i) == WIFI_AUTH_OPEN ? "false" : "true") + "}";
    }
    WiFi.scanDelete();
    sendJson(out + "]");
  });
  server.on("/api/wifi", HTTP_GET, []() {
    bool sta = WiFi.status() == WL_CONNECTED;
    int closesIn = apCloseAt ? (int)((int32_t)(apCloseAt - millis()) / 1000) : 0;
    sendJson(String("{\"state\":\"") + stateName() + "\",\"ssid\":\"" +
             jsonEsc(portalConnect || wstate == WState::FAILED ? pendSsid : (sta ? WiFi.SSID() : savedSsid)) +
             "\",\"ip\":\"" + (sta ? WiFi.localIP().toString() : String("")) + "\",\"host\":\"" + hostName +
             ".local\",\"rssi\":" + String(sta ? WiFi.RSSI() : 0) + ",\"ap\":\"" + apName +
             "\",\"ap_closes_in\":" + String(closesIn > 0 ? closesIn : 0) + ",\"error\":\"" + wifiError + "\"}");
  });
  server.on("/api/wifi", HTTP_POST, []() {
    pendSsid = server.arg("ssid");
    pendPass = server.arg("pass");
    String h = server.arg("host");
    h.trim();
    if (h.length()) hostName = h;
    portalConnect = true;
    if (!apActive && WiFi.status() != WL_CONNECTED) startAP();
    beginConnect(pendSsid, pendPass);
    MDNS.end();                                   // re-announce with the (possibly) new name
    MDNS.begin(hostName.c_str());
    MDNS.addService("http", "tcp", 80);
    MDNS.addService("m18bridge", "tcp", 80);
    sendJson("{\"ok\":true}");
  });
  server.on("/api/wifi/forget", HTTP_POST, []() {
    savePrefs("", "");
    sendJson("{\"ok\":true}");
    delay(500);
    ESP.restart();
  });
  server.onNotFound([]() {
    if (captiveRedirect()) return;
    server.send(404, "text/plain", "not found");
  });
  server.begin();
}

// ---------------------------------------------------------------- discovery
// The PC broadcasts "M18BRIDGE?" to UDP port 18018; every bridge answers.
void discoveryLoop() {
  int len = disc.parsePacket();
  if (len <= 0) return;
  char buf[32] = {0};
  disc.read(buf, sizeof(buf) - 1);
  if (strncmp(buf, "M18BRIDGE?", 10) != 0) return;
  String reply = String("M18BRIDGE ") + M18BRIDGE_VERSION + " host=" + hostName + ".local ip=" + ipString() +
                 " http=80 mac=" + WiFi.macAddress() + "\n";
  disc.beginPacket(disc.remoteIP(), disc.remotePort());
  disc.write((const uint8_t*)reply.c_str(), reply.length());
  disc.endPacket();
}

// ------------------------------------------------------------- USB input
struct LineInput {
  Stream* s;
  String buf;
  void poll() {
    while (s->available()) {
      char ch = (char)s->read();
      if (ch == '\n' || ch == '\r') {
        if (buf.length()) { s->println(runCommand(buf)); buf = ""; }
      } else if (buf.length() < 120) {
        buf += ch;
      }
    }
  }
};
LineInput usbIn{&Serial, ""};
#if ARDUINO_USB_CDC_ON_BOOT
LineInput uartIn{&Serial0, ""};                 // second USB socket ("UART", CH343/CP2102)
#endif

// --------------------------------------------------------------------- main
uint32_t bootPressedSince = 0;

void setup() {
  port.begin();                                 // J2 low first - before anything else
  pinMode(PIN_BOOT, INPUT_PULLUP);
  led(0, 0, 8);
  Serial.begin(HOST_BAUD);
#if ARDUINO_USB_CDC_ON_BOOT
  Serial0.begin(HOST_BAUD);
#endif
  proc.setClock(unixNow);

  WiFi.persistent(false);
  loadPrefs();
  WiFi.setHostname(hostName.c_str());
  if (savedSsid.length()) {
    WiFi.mode(WIFI_STA);
    beginConnect(savedSsid, savedPass);
    while (WiFi.status() != WL_CONNECTED && millis() - connectStart < 12000) delay(100);
  }
  if (WiFi.status() == WL_CONNECTED) {
    onConnected();
  } else {
    // No (reachable) Wi-Fi: stop background reconnects (they disturb the AP), open the setup AP.
    WiFi.setAutoReconnect(false);
    WiFi.disconnect();
    wstate = WState::AP;
    startAP();
    lastStaRetry = millis();
  }

  if (MDNS.begin(hostName.c_str())) {
    MDNS.addService("http", "tcp", 80);
    MDNS.addService("m18bridge", "tcp", 80);
    MDNS.addServiceTxt("m18bridge", "tcp", "version", M18BRIDGE_VERSION);
  }
  disc.begin(DISCOVERY_PORT);
  setupHttp();
  Serial.printf("\nM18BRIDGE READY %s host=%s.local ip=%s\n", M18BRIDGE_VERSION, hostName.c_str(), ipString().c_str());
}

void loop() {
  server.handleClient();
  if (dnsActive) dns.processNextRequest();
  discoveryLoop();
  usbIn.poll();
#if ARDUINO_USB_CDC_ON_BOOT
  uartIn.poll();
#endif
  wifiLoop();

  // Safety: never leave J2 high when the PC went away mid-session.
  if (!batLink.isIdle() && millis() - batLink.lastActivity() > AUTO_IDLE_MS) batLink.idle();

  // BOOT button held 5 s: forget Wi-Fi and restart in setup mode.
  if (digitalRead(PIN_BOOT) == LOW) {
    if (!bootPressedSince) bootPressedSince = millis();
    if (millis() - bootPressedSince > 5000) {
      led(30, 0, 0);
      savePrefs("", "");
      delay(300);
      ESP.restart();
    }
  } else {
    bootPressedSince = 0;
  }

  // LED: green = in Wi-Fi, blue = setup mode, purple = connecting, white blink = battery line active
  static uint32_t lastLed = 0;
  if (millis() - lastLed > 250) {
    lastLed = millis();
    bool blink = (millis() / 250) % 2;
    if (!batLink.isIdle()) led(blink ? 12 : 0, blink ? 12 : 0, blink ? 12 : 0);
    else if (wstate == WState::CONNECTING) led(8, 0, 10);
    else if (WiFi.status() == WL_CONNECTED) led(0, 8, 0);
    else led(0, 0, blink ? 10 : 2);
  }
}
