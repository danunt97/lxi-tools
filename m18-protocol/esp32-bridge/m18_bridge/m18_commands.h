// Host command protocol of the M18 bridge (USB serial and HTTP POST /cmd).
// One text command per line, one reply line per command:
//   PING                -> OK M18BRIDGE <version>
//   RESET               -> OK ACK | ERR NOACK [byte]
//   IDLE | HIGH         -> OK
//   READ <addr> <len>   -> OK <hex response frame> | ERR TIMEOUT
//   NOTE <addr> <byte>  -> OK <hex response frame> | ERR ...   (note area 0x23-0x36 only)
//   SCAN                -> OK {decoded read-out as JSON} | ERR {json}
//   INFO                -> OK {json}
// Numbers may be decimal or 0x-prefixed hex. SCAN needs a reply buffer of ~2 KB.

#pragma once
#include "m18_proto.h"
#include "m18_reader.h"

namespace m18 {

// Parses and executes one host command line, writes one reply line (no newline).
class CommandProcessor {
 public:
  CommandProcessor(Link& link, Reader& reader) : link_(link), reader_(reader) {}

  // Unix time source for the age calculation in SCAN (return 0 if unknown).
  void setClock(uint32_t (*clock)()) { clock_ = clock; }

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

    if (!strcmp(cmd, "SCAN")) {                 // full direct read-out, decoded as JSON
      reader_.read();
      size_t pos = (size_t)snprintf(reply, cap, reader_.ok() ? "OK " : "ERR ");
      reader_.toJson(reply + pos, cap - pos, clock_ ? clock_() : 0);
      return;
    }
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
  Reader& reader_;
  uint32_t (*clock_)() = NULL;
  const char* infoExtra_ = NULL;
};

}  // namespace m18
