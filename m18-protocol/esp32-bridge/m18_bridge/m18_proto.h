// M18 bridge protocol core.
//
// Pure C++ (no Arduino headers) so it can be unit-tested on a PC. The sketch
// (m18_bridge.ino) supplies an M18Port implementation for the ESP32 UART.
//
// Battery side (same as m18.py with a USB-serial adapter):
//   4800 baud, 8 data bits, no parity, 2 stop bits, every byte bit-reversed.
//   "idle"  = TX held low (break)      -> battery ignores us, no charge counted
//   "reset" = TX low 300 ms, high 300 ms, send 0xAA, battery echoes 0xAA
//   read    = [0x01, 0x04, 0x03, addr_hi, addr_lo, len] + 16-bit sum checksum
//             -> [0x81, .., .., data[len], cksum_hi, cksum_lo]
//   note    = [0x01, 0x05, 0x03, 0x00, addr, byte] (only 0x0023..0x0036)
//
// Host side: one text command per line, one reply line per command:
//   PING                -> OK M18BRIDGE <version>
//   RESET               -> OK ACK | ERR NOACK [byte]
//   IDLE | HIGH         -> OK
//   READ <addr> <len>   -> OK <hex response frame> | ERR TIMEOUT
//   NOTE <addr> <byte>  -> OK <hex response frame> | ERR ...
//   INFO                -> OK {json}
// Numbers may be decimal or 0x-prefixed hex.

#pragma once
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

#define M18BRIDGE_VERSION "1.0"

namespace m18 {

// Hardware abstraction implemented by the sketch (or by the PC test).
struct Port {
  virtual ~Port() {}
  virtual void setIdle(bool idle) = 0;              // true: hold TX low, false: UART active (TX idles high)
  virtual void flushInput() = 0;
  virtual void write(const uint8_t* data, size_t n) = 0;
  virtual int readByte(uint32_t timeoutMs) = 0;     // -1 on timeout
  virtual void delayMs(uint32_t ms) = 0;
  virtual uint32_t millis() = 0;
};

static const uint8_t SYNC_BYTE = 0xAA;
static const uint16_t NOTE_FIRST = 0x0023;
static const uint16_t NOTE_LAST = 0x0036;           // 20 ascii bytes
static const uint32_t READ_TIMEOUT_MS = 800;        // same as m18.py (Serial timeout=0.8)
static const size_t MAX_FRAME = 80;                 // biggest read in m18.py is 0x3A + 5

inline uint8_t rev8(uint8_t b) {
  b = (uint8_t)((b & 0xF0) >> 4 | (b & 0x0F) << 4);
  b = (uint8_t)((b & 0xCC) >> 2 | (b & 0x33) << 2);
  b = (uint8_t)((b & 0xAA) >> 1 | (b & 0x55) << 1);
  return b;
}

class Link {
 public:
  explicit Link(Port& p) : port_(p), idle_(true) {}

  bool isIdle() const { return idle_; }
  uint32_t lastActivity() const { return lastActivity_; }

  void idle() { port_.setIdle(true); idle_ = true; touch(); }
  void high() { port_.setIdle(false); idle_ = false; touch(); }

  // Returns true when the battery echoed the sync byte. `got` receives the byte (-1 none).
  bool reset(int* got) {
    port_.setIdle(true);
    idle_ = true;
    port_.delayMs(300);
    port_.setIdle(false);
    idle_ = false;
    port_.delayMs(300);
    uint8_t s = SYNC_BYTE;
    sendRaw(&s, 1);
    uint8_t buf[2];
    size_t n = readResponse(buf, 1, sizeof(buf));
    port_.delayMs(10);
    touch();
    if (got) *got = n ? buf[0] : -1;
    return n >= 1 && buf[0] == SYNC_BYTE;
  }

  // Sends a 6-byte command plus checksum and reads up to `respLen` bytes.
  // Returns the number of bytes received (0 = timeout). Bytes are un-reversed.
  size_t command(uint8_t c0, uint8_t c1, uint8_t c2, uint8_t a, uint8_t b, uint8_t c,
                 uint8_t* out, size_t respLen, size_t outCap) {
    uint8_t pkt[8] = {c0, c1, c2, a, b, c, 0, 0};
    uint16_t sum = 0;
    for (int i = 0; i < 6; i++) sum += pkt[i];
    pkt[6] = (uint8_t)(sum >> 8);
    pkt[7] = (uint8_t)(sum & 0xFF);
    sendRaw(pkt, 8);
    size_t n = readResponse(out, respLen, outCap);
    touch();
    return n;
  }

  // Mirrors M18.read_response(): read 1 byte; 0x82 means a 2-byte error reply,
  // otherwise read the remaining size-1 bytes. Then wait 50 ms.
  size_t readResponse(uint8_t* out, size_t size, size_t cap) {
    if (size == 0 || cap == 0) return 0;
    int first = port_.readByte(READ_TIMEOUT_MS);
    if (first < 0) return 0;
    out[0] = rev8((uint8_t)first);
    size_t want = (out[0] == 0x82) ? 2 : size;
    if (want > cap) want = cap;
    size_t n = 1;
    uint32_t t0 = port_.millis();
    while (n < want) {
      uint32_t el = port_.millis() - t0;
      if (el >= READ_TIMEOUT_MS) break;
      int v = port_.readByte(READ_TIMEOUT_MS - el);
      if (v < 0) break;
      out[n++] = rev8((uint8_t)v);
    }
    port_.delayMs(50);
    return n;
  }

 private:
  void sendRaw(const uint8_t* data, size_t n) {
    uint8_t tmp[16];
    port_.flushInput();
    for (size_t i = 0; i < n && i < sizeof(tmp); i++) tmp[i] = rev8(data[i]);
    port_.write(tmp, n < sizeof(tmp) ? n : sizeof(tmp));
  }
  void touch() { lastActivity_ = port_.millis(); }

  Port& port_;
  bool idle_;
  uint32_t lastActivity_ = 0;
};

// Parses and executes one host command line, writes one reply line (no newline).
class CommandProcessor {
 public:
  explicit CommandProcessor(Link& link) : link_(link) {}

  // Optional extra info for INFO (set by the sketch, e.g. Wi-Fi state as JSON fields).
  void setInfoExtra(const char* json) { infoExtra_ = json; }

  void handle(const char* lineIn, char* reply, size_t cap) {
    char line[96];
    strncpy(line, lineIn, sizeof(line) - 1);
    line[sizeof(line) - 1] = 0;
    char* argv[5] = {0};
    int argc = 0;
    for (char* tok = strtok(line, " \t\r\n"); tok && argc < 5; tok = strtok(NULL, " \t\r\n"))
      argv[argc++] = tok;
    if (argc == 0) { snprintf(reply, cap, "ERR EMPTY"); return; }
    for (char* p = argv[0]; *p; ++p) *p = (char)toupper((unsigned char)*p);
    const char* cmd = argv[0];

    if (!strcmp(cmd, "PING")) { snprintf(reply, cap, "OK M18BRIDGE %s", M18BRIDGE_VERSION); return; }
    if (!strcmp(cmd, "IDLE")) { link_.idle(); snprintf(reply, cap, "OK"); return; }
    if (!strcmp(cmd, "HIGH")) { link_.high(); snprintf(reply, cap, "OK"); return; }
    if (!strcmp(cmd, "RESET")) {
      int got = -1;
      if (link_.reset(&got)) snprintf(reply, cap, "OK ACK");
      else if (got < 0) snprintf(reply, cap, "ERR NOACK");
      else snprintf(reply, cap, "ERR NOACK %02X", got);
      return;
    }
    if (!strcmp(cmd, "INFO")) {
      snprintf(reply, cap, "OK {\"version\":\"%s\",\"line\":\"%s\"%s%s}", M18BRIDGE_VERSION,
               link_.isIdle() ? "idle" : "active", infoExtra_ && *infoExtra_ ? "," : "",
               infoExtra_ ? infoExtra_ : "");
      return;
    }
    if (!strcmp(cmd, "READ") || !strcmp(cmd, "NOTE")) {
      long a, b;
      if (argc != 3 || !num(argv[1], &a) || !num(argv[2], &b) || a < 0 || a > 0xFFFF || b < 0 || b > 0xFF) {
        snprintf(reply, cap, "ERR ARGS");
        return;
      }
      if (link_.isIdle()) { snprintf(reply, cap, "ERR IDLE (RESET first)"); return; }
      uint8_t frame[MAX_FRAME];
      size_t n;
      if (cmd[0] == 'R') {
        if ((size_t)b + 5 > MAX_FRAME) { snprintf(reply, cap, "ERR LEN"); return; }
        n = link_.command(0x01, 0x04, 0x03, (uint8_t)(a >> 8), (uint8_t)a, (uint8_t)b,
                          frame, (size_t)b + 5, sizeof(frame));
      } else {
        // Only the 20-byte user note may be written (same as m18.py write_message).
        if (a < NOTE_FIRST || a > NOTE_LAST) { snprintf(reply, cap, "ERR ADDR (note area 0x23-0x36 only)"); return; }
        n = link_.command(0x01, 0x05, 0x03, 0x00, (uint8_t)a, (uint8_t)b, frame, 2, sizeof(frame));
      }
      if (n == 0) { snprintf(reply, cap, "ERR TIMEOUT"); return; }
      size_t pos = (size_t)snprintf(reply, cap, "OK ");
      for (size_t i = 0; i < n && pos + 3 < cap; i++) pos += (size_t)snprintf(reply + pos, cap - pos, "%02X", frame[i]);
      return;
    }
    snprintf(reply, cap, "ERR UNKNOWN %s", cmd);
  }

 private:
  static bool num(const char* s, long* out) {
    char* end = NULL;
    long v = strtol(s, &end, 0);
    if (!end || *end) return false;
    *out = v;
    return true;
  }
  Link& link_;
  const char* infoExtra_ = NULL;
};

}  // namespace m18
