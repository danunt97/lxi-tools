// Host command protocol of the M18 bridge (USB serial and HTTP POST /cmd).
// One text command per line, one reply line per command:
//   PING                    -> OK M18BRIDGE <version>
//   RESET                   -> OK ACK | ERR NOACK [byte]
//   IDLE | HIGH             -> OK
//   READ <addr> <len>       -> OK <hex response frame> | ERR TIMEOUT
//   NOTE <addr> <byte>      -> OK <hex response frame>     (note area 0x23-0x36 only)
//   SCAN                    -> OK {decoded read-out + raw blocks, "history_id"} | ERR {json}
//   INFO                    -> OK {json}
//   TIME <unix>             -> OK <unix now>   (sets the clock if it is not synced yet)
//   HSTAT                   -> OK {"count","unsynced","free_kb","total_kb","ready"}
//   HLIST <after> [max] [new|all] -> OK {"items":[meta...],"next":id|0,...}
//   HGET <id>               -> OK {"meta":{...},"scan":{...}}
//   HACK <id>               -> OK            (record imported by the PC)
//   HDEL <id>               -> OK
//   HCLEAR [synced|all]     -> OK <n deleted>
// Numbers may be decimal or 0x-prefixed hex.

#pragma once
#include <string>
#include "m18_proto.h"
#include "m18_reader.h"
#include "m18_history.h"

namespace m18 {

class CommandProcessor {
 public:
  CommandProcessor(Link& link, Reader& reader) : link_(link), reader_(reader) {}

  // Unix time source for the age calculation in SCAN (return 0 if unknown).
  void setClock(uint32_t (*clock)()) { clock_ = clock; }
  // TIME <unix>: set the clock (returns false if refused, e.g. already NTP-synced).
  void setTimeSetter(bool (*setter)(uint32_t)) { setTime_ = setter; }
  // History store and the meta (time, location, ...) for records created by SCAN.
  void setHistory(History* h) { history_ = h; }
  void setMetaProvider(void (*fn)(RecordMeta&)) { meta_ = fn; }
  // Optional extra info for INFO (set by the sketch, e.g. Wi-Fi state as JSON fields).
  void setInfoExtra(const char* json) { infoExtra_ = json; }

  // Last SCAN result (JSON without "OK "), e.g. for the web UI.
  const char* lastScanJson() const { return scanBuf_; }

  std::string handle(const char* lineIn) {
    char line[128];
    strncpy(line, lineIn, sizeof(line) - 1);
    line[sizeof(line) - 1] = 0;
    char* argv[6] = {0};
    int argc = 0;
    for (char* tok = strtok(line, " \t\r\n"); tok && argc < 6; tok = strtok(NULL, " \t\r\n"))
      argv[argc++] = tok;
    if (argc == 0) return "ERR EMPTY";
    for (char* p = argv[0]; *p; ++p) *p = (char)toupper((unsigned char)*p);
    const char* cmd = argv[0];
    char r[200];

    if (!strcmp(cmd, "SCAN")) {                 // full direct read-out, decoded as JSON
      uint32_t id = scan();
      std::string out = reader_.ok() ? "OK " : "ERR ";
      out += scanBuf_;
      if (id) out.insert(out.size() - 1, ",\"history_id\":" + std::to_string(id));
      return out;
    }
    if (!strcmp(cmd, "PING")) { snprintf(r, sizeof(r), "OK M18BRIDGE %s", M18BRIDGE_VERSION); return r; }
    if (!strcmp(cmd, "IDLE")) { link_.idle(); return "OK"; }
    if (!strcmp(cmd, "HIGH")) { link_.high(); return "OK"; }
    if (!strcmp(cmd, "RESET")) {
      int got = -1;
      if (link_.reset(&got)) return "OK ACK";
      if (got < 0) return "ERR NOACK";
      snprintf(r, sizeof(r), "ERR NOACK %02X", got);
      return r;
    }
    if (!strcmp(cmd, "INFO")) {
      std::string out = std::string("OK {\"version\":\"") + M18BRIDGE_VERSION + "\",\"line\":\"" +
                        (link_.isIdle() ? "idle" : "active") + "\"";
      if (infoExtra_ && *infoExtra_) { out += ","; out += infoExtra_; }
      return out + "}";
    }
    if (!strcmp(cmd, "TIME")) {
      long t;
      if (argc != 2 || !num(argv[1], &t) || t < 1600000000L) return "ERR ARGS";
      if (setTime_) setTime_((uint32_t)t);
      return "OK " + std::to_string(clock_ ? clock_() : 0);
    }
    if (cmd[0] == 'H' && strlen(cmd) >= 4 && strncmp(cmd, "HIGH", 4)) return history(cmd, argc, argv);
    if (!strcmp(cmd, "READ") || !strcmp(cmd, "NOTE")) {
      long a, b;
      if (argc != 3 || !num(argv[1], &a) || !num(argv[2], &b) || a < 0 || a > 0xFFFF || b < 0 || b > 0xFF)
        return "ERR ARGS";
      if (link_.isIdle()) return "ERR IDLE (RESET first)";
      uint8_t frame[MAX_FRAME];
      size_t n;
      if (cmd[0] == 'R') {
        if ((size_t)b + 5 > MAX_FRAME) return "ERR LEN";
        n = link_.command(0x01, 0x04, 0x03, (uint8_t)(a >> 8), (uint8_t)a, (uint8_t)b,
                          frame, (size_t)b + 5, sizeof(frame));
      } else {
        // Only the 20-byte user note may be written (same as m18.py write_message).
        if (a < NOTE_FIRST || a > NOTE_LAST) return "ERR ADDR (note area 0x23-0x36 only)";
        n = link_.command(0x01, 0x05, 0x03, 0x00, (uint8_t)a, (uint8_t)b, frame, 2, sizeof(frame));
      }
      if (n == 0) return "ERR TIMEOUT";
      std::string out = "OK ";
      for (size_t i = 0; i < n; i++) { char hx[3]; snprintf(hx, sizeof(hx), "%02X", frame[i]); out += hx; }
      return out;
    }
    snprintf(r, sizeof(r), "ERR UNKNOWN %s", cmd);
    return r;
  }

  // Full read-out; stores it in the history when successful. Returns the history id (0 = not stored).
  // Callers must hold the battery line (the sketch's mutex).
  uint32_t scan() {
    reader_.read();
    reader_.toJson(scanBuf_, sizeof(scanBuf_), clock_ ? clock_() : 0);
    if (!reader_.ok() || !history_ || !history_->ready()) return 0;
    RecordMeta m;
    if (meta_) meta_(m);
    return history_->add(scanBuf_, m);
  }

 private:
  std::string history(const char* cmd, int argc, char** argv) {
    if (!history_ || !history_->ready()) return "ERR NOSTORAGE (Speicher nicht verfügbar)";
    long a = 0, b = 50;
    if (!strcmp(cmd, "HSTAT")) return "OK " + history_->stats();
    if (!strcmp(cmd, "HLIST")) {
      if (argc >= 2 && !num(argv[1], &a)) return "ERR ARGS";
      if (argc >= 3 && !num(argv[2], &b)) return "ERR ARGS";
      bool onlyNew = argc >= 4 && (argv[3][0] == 'n' || argv[3][0] == 'N');
      if (b < 1) b = 1;
      if (b > 100) b = 100;
      return "OK " + history_->list((uint32_t)(a < 0 ? 0 : a), (int)b, onlyNew);
    }
    if (!strcmp(cmd, "HCLEAR")) {
      bool all = argc >= 2 && (argv[1][0] == 'a' || argv[1][0] == 'A');
      return "OK " + std::to_string(history_->clear(!all));
    }
    if (argc != 2 || !num(argv[1], &a) || a <= 0) return "ERR ARGS";
    if (!strcmp(cmd, "HGET")) {
      std::string out;
      if (!history_->get((uint32_t)a, out)) return "ERR NOTFOUND";
      return "OK " + out;
    }
    if (!strcmp(cmd, "HACK")) return history_->ack((uint32_t)a) ? "OK" : "ERR NOTFOUND";
    if (!strcmp(cmd, "HDEL")) return history_->remove((uint32_t)a) ? "OK" : "ERR NOTFOUND";
    return std::string("ERR UNKNOWN ") + cmd;
  }

  static bool num(const char* s, long* out) {
    char* end = NULL;
    long v = strtol(s, &end, 0);
    if (!end || *end) return false;
    *out = v;
    return true;
  }
  Link& link_;
  Reader& reader_;
  History* history_ = nullptr;
  uint32_t (*clock_)() = NULL;
  bool (*setTime_)(uint32_t) = NULL;
  void (*meta_)(RecordMeta&) = NULL;
  const char* infoExtra_ = NULL;
  char scanBuf_[4608] = "{}";
};

}  // namespace m18
