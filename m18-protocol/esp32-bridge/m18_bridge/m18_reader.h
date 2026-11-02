// Direct battery read-out on the ESP32: reads the same register blocks as
// m18.py (data_matrix), decodes them and estimates the state of health with
// the same formula as the PC database (gui/fleet.py).
//
// Pure C++ (no Arduino headers) so it is unit-tested on the PC.

#pragma once
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#include "m18_proto.h"

namespace m18 {

struct Block { uint16_t addr; uint8_t len; };

// Identical to data_matrix in m18.py.
static const Block BLOCKS[] = {
  {0x0000, 0x02}, {0x0002, 0x02}, {0x0004, 0x05}, {0x000D, 0x04}, {0x0011, 0x04},
  {0x0015, 0x04}, {0x0019, 0x04}, {0x0023, 0x14}, {0x0037, 0x04}, {0x0069, 0x02},
  {0x007B, 0x01}, {0x4000, 0x04}, {0x400A, 0x0A}, {0x4014, 0x02}, {0x4016, 0x02},
  {0x4019, 0x02}, {0x401B, 0x02}, {0x401D, 0x02}, {0x401F, 0x02}, {0x6000, 0x02},
  {0x6002, 0x02}, {0x6004, 0x04}, {0x6008, 0x04}, {0x600C, 0x02}, {0x9000, 0x3A},
  {0x903A, 0x3A}, {0x9074, 0x3A}, {0x90AE, 0x3A}, {0x90E8, 0x3A}, {0x9122, 0x30},
  {0x9152, 0x00}, {0xA000, 0x06},
};
static const int NUM_BLOCKS = sizeof(BLOCKS) / sizeof(BLOCKS[0]);

// type code -> nominal Ah and name (same table as M18.health() / fleet.py)
struct BatType { uint16_t code; float ah; const char* name; };
static const BatType BAT_TYPES[] = {
  {36, 1.5f, "1.5Ah CP"}, {37, 2.0f, "2Ah CP"}, {38, 3.0f, "3Ah XC"}, {39, 4.0f, "4Ah XC"},
  {40, 5.0f, "5Ah XC (<= 2018)"}, {165, 5.0f, "5Ah XC (2019-2021)"}, {306, 5.0f, "5Ah XC (2021-2023)"},
  {424, 5.0f, "5Ah XC (>= 2023)"}, {46, 6.0f, "6Ah XC"}, {47, 9.0f, "9Ah HD"}, {104, 3.0f, "3Ah HO"},
  {150, 5.5f, "5.5Ah HO (EU)"}, {106, 6.0f, "6Ah HO"}, {107, 8.0f, "8Ah HO"}, {108, 12.0f, "12Ah HO"},
  {383, 8.0f, "8Ah Forge"}, {384, 12.0f, "12Ah Forge"},
};

// Sparse copy of the battery's address space.
class Memory {
 public:
  void clear() { memset(valid_, 0, sizeof(valid_)); }
  void store(uint16_t addr, const uint8_t* d, size_t n) {
    for (size_t i = 0; i < n; i++) {
      int idx = index(addr + i);
      if (idx >= 0) { data_[idx] = d[i]; valid_[idx] = 1; }
    }
  }
  bool has(uint16_t addr, size_t n) const {
    for (size_t i = 0; i < n; i++) { int idx = index(addr + i); if (idx < 0 || !valid_[idx]) return false; }
    return true;
  }
  uint32_t be(uint16_t addr, size_t n) const {      // big-endian unsigned
    uint32_t v = 0;
    for (size_t i = 0; i < n; i++) v = v << 8 | data_[index(addr + i)];
    return v;
  }
  uint8_t at(uint16_t addr) const { return data_[index(addr)]; }

 private:
  // regions: 0x0000-0x007F, 0x4000-0x403F, 0x6000-0x601F, 0x9000-0x91FF, 0xA000-0xA00F
  static int index(uint32_t a) {
    if (a < 0x0080) return (int)a;
    if (a >= 0x4000 && a < 0x4040) return 0x80 + (int)(a - 0x4000);
    if (a >= 0x6000 && a < 0x6020) return 0xC0 + (int)(a - 0x6000);
    if (a >= 0x9000 && a < 0x9200) return 0xE0 + (int)(a - 0x9000);
    if (a >= 0xA000 && a < 0xA010) return 0x2E0 + (int)(a - 0xA000);
    return -1;
  }
  uint8_t data_[0x2F0] = {0};
  uint8_t valid_[0x2F0] = {0};
};

class Reader {
 public:
  explicit Reader(Link& link, Port& port) : link_(link), port_(port) {}

  // Full read like m18.py read_id(): dummy pass, idle, reset, real pass, idle.
  // Hardened: several reset attempts, one retry + resync per block, abort as soon
  // as the battery stops answering (unplugged) instead of waiting for every timeout.
  // Returns the number of blocks that answered in the real pass.
  int read() {
    mem_.clear();
    uint32_t t0 = port_.millis();
    uint8_t buf[MAX_FRAME];
    ok_ = false;
    answered_ = 0;
    retries_ = 0;
    cksumMismatch_ = 0;
    error_ = nullptr;
    progress_ = 0;
    if (!resetWithRetry()) {
      fail("Akku antwortet nicht (kein Sync) – Akku aufgesteckt, Verkabelung J1/J2/B- ok?", t0);
      return 0;
    }
    int fails = 0;
    for (int i = 0; i < NUM_BLOCKS; i++) {           // dummy pass: refreshes the 0x9000 RAM copy
      fails = readBlock(BLOCKS[i], buf, false) ? 0 : fails + 1;
      progress_++;
      if (fails >= ABORT_AFTER_FAILS) { fail("Verbindung zum Akku abgebrochen", t0); return 0; }
    }
    link_.idle();
    port_.delayMs(100);
    if (!resetWithRetry()) { fail("Akku antwortet nach dem ersten Durchlauf nicht mehr", t0); return 0; }
    fails = 0;
    for (int i = 0; i < NUM_BLOCKS; i++) {
      bool okb = readBlock(BLOCKS[i], buf, true);
      if (!okb) {                                     // retry once, then resync with a reset
        retries_++;
        port_.delayMs(40);
        okb = readBlock(BLOCKS[i], buf, true);
        if (!okb) {
          if (!resetWithRetry(2)) { fail("Verbindung zum Akku abgebrochen", t0); return answered_; }
          retries_++;
          okb = readBlock(BLOCKS[i], buf, true);
        }
      }
      progress_++;
      if (okb) { answered_++; fails = 0; }
      else if (++fails >= ABORT_AFTER_FAILS) { fail("Verbindung zum Akku abgebrochen", t0); return answered_; }
    }
    link_.idle();
    durationMs_ = port_.millis() - t0;
    ok_ = mem_.has(0x0004, 5);
    if (!ok_) error_ = "Seriennummer nicht lesbar";
    return answered_;
  }

  // Progress of a running read() in percent (safe to poll from another task).
  int progressPercent() const { return progress_ * 100 / (2 * NUM_BLOCKS); }

  bool ok() const { return ok_; }
  const Memory& memory() const { return mem_; }

  // Writes the decoded read-out as JSON. `now` = unix time (0 = use the battery clock).
  size_t toJson(char* out, size_t cap, uint32_t now) const {
    Json j(out, cap);
    j.raw("{");
    j.kv("ok", ok_);
    j.kvn("blocks_ok", answered_); j.kvn("blocks_total", NUM_BLOCKS); j.kvn("duration_ms", durationMs_);
    j.kvn("retries", retries_); j.kvn("checksum_mismatch", cksumMismatch_);
    if (!ok_) { j.kvs("error", error_ ? error_ : "Akku antwortet nicht", false); j.raw("}"); return j.len(); }

    uint16_t type = (uint16_t)mem_.be(0x0004, 2);
    uint32_t serial = mem_.be(0x0006, 3);
    const BatType* bt = lookup(type);
    char es[24];
    snprintf(es, sizeof(es), "%u-%lu", (unsigned)type, (unsigned long)serial);
    j.kvs("eserial", es);
    j.kvn("type_code", type);
    j.kvs("model", bt ? bt->name : "Unbekannt");
    if (bt) j.kvf("capacity_ah", bt->ah, 1);

    uint32_t batNow = date(0x0037);
    if (!now) now = batNow;
    j.kvd("manufactured", date(0x0011));
    uint32_t first = date(0x0015) ? date(0x0015) : date(0x9000);
    uint32_t last = date(0x0019) ? date(0x0019) : date(0x9008);
    j.kvd("first_charge", first);
    j.kvd("last_charge", last);
    j.kvd("last_tool_use", date(0x9004));
    j.kvd("battery_date", batNow);

    char note[21] = {0};
    if (mem_.has(0x0023, 20)) {
      for (int i = 0; i < 20; i++) { uint8_t c = mem_.at(0x0023 + i); note[i] = (c >= 32 && c < 127) ? (char)c : ' '; }
      for (int i = 19; i >= 0 && (note[i] == '-' || note[i] == ' '); i--) note[i] = 0;
    }
    j.kvs("note", note);

    // cells
    int cells[5] = {0}, ncell = 0, lo = 99999, hi = 0;
    long sum = 0;
    if (mem_.has(0x400A, 10)) {
      for (int i = 0; i < 5; i++) {
        cells[i] = (int)mem_.be(0x400A + 2 * i, 2);
        sum += cells[i];
        if (cells[i] < lo) lo = cells[i];
        if (cells[i] > hi) hi = cells[i];
        ncell++;
      }
    }
    // Plausibility: Li-ion cells are 2.0-4.4 V; anything else means a bad read or a broken pack.
    bool cellsPlausible = ncell == 5;
    for (int i = 0; i < ncell; i++) if (cells[i] < 2000 || cells[i] > 4400) cellsPlausible = false;
    j.key("warnings"); j.raw("[");
    bool firstW = true;
    auto warn = [&](const char* w) { if (!firstW) j.raw(","); firstW = false; j.str(w); };
    if (!cellsPlausible) warn("Zellspannungen unplausibel – Messung wiederholen");
    if (answered_ < NUM_BLOCKS) warn("Nicht alle Registerblöcke gelesen");
    if (cksumMismatch_) warn("Prüfsummen-Abweichungen – Verkabelung/Störungen prüfen");
    for (int i = 0; i < ncell; i++) if (cells[i] >= 2000 && cells[i] < 2500) { warn("Zelle unter 2,5 V – tiefentladen"); break; }
    if (u(0x902E, 2) > 0) warn("Wurde schon mit einer Zelle < 2,5 V geladen");
    if (ncell && hi - lo >= 100) warn("Zell-Imbalance >= 100 mV");
    j.raw("],");
    j.key("cells_mv"); j.raw("[");
    for (int i = 0; i < ncell; i++) { char b[12]; snprintf(b, sizeof(b), "%s%d", i ? "," : "", cells[i]); j.raw(b); }
    j.raw("],");
    int imb = ncell ? hi - lo : -1;
    if (ncell) { j.kvf("pack_v", sum / 1000.0f, 2); j.kvn("imbalance_mv", imb); }

    // temperature: Forge register first, else thermistor ADC (formula from m18.py)
    float temp = 0; bool haveTemp = false;
    if (mem_.has(0x401F, 2) && (mem_.at(0x401F) || mem_.at(0x4020))) {
      temp = mem_.at(0x401F) + mem_.at(0x4020) / 256.0f; haveTemp = true;
    } else if (mem_.has(0x4014, 2) && mem_.be(0x4014, 2)) {
      float adc = (float)mem_.be(0x4014, 2);
      float r = 10e3f + (adc - 0x0180) * (20e3f - 10e3f) / (0x022E - 0x0180);
      float m = (35.0f - 50.0f) / (20e3f - 10e3f);
      temp = m * r + (50.0f - m * 10e3f); haveTemp = true;
    }
    if (haveTemp) j.kvf("temp_c", temp, 1);

    // counters
    long das = u(0x9012, 4), dws = u(0x9016, 4);
    long toolTime = 0;
    for (uint16_t a = 0x903A; a <= 0x9060; a += 2) toolTime += u(a, 2) > 0 ? u(a, 2) : 0;
    j.key("counters"); j.raw("{");
    j.kvn("total_charges", u(0x901A, 4)); j.kvn("redlink_charges", u(0x9020, 2)); j.kvn("dumb_charges", u(0x901E, 2));
    j.kvn("discharge_as", das); j.kvn("discharge_ws", dws);
    j.kvn("overheat", u(0x9032, 2)); j.kvn("overcurrent", u(0x9034, 2)); j.kvn("lowvolt", u(0x9036, 2));
    j.kvn("lowvolt_bounce", u(0x9038, 2)); j.kvn("discharge_to_empty", u(0x9030, 2));
    j.kvn("lowv_charges", u(0x902E, 2)); j.kvn("days", u(0x9010, 2));
    j.kvn("charge_time_s", u(0x9024, 4)); j.kvn("tool_time_s", toolTime);
    j.endObj(); j.raw(",");
    if (das >= 0) j.kvf("discharge_ah", das / 3600.0f, 1);
    if (dws >= 0) j.kvf("discharge_wh", dws / 3600.0f, 0);

    // health estimate (same rules as gui/fleet.py health_estimate)
    float total = 0;
    j.key("health_parts"); j.raw("[");
    bool firstPart = true;
    char det[48];
    if (das >= 0 && bt) {
      float efc = das / 3600.0f / bt->ah;
      snprintf(det, sizeof(det), "%.0f äquivalente Vollzyklen", efc);
      total += part(j, firstPart, "Zyklenverschleiß", min_(45.0f, efc * 0.04f), det);
    }
    if (imb >= 0) {
      snprintf(det, sizeof(det), "%d mV Spreizung", imb);
      total += part(j, firstPart, "Zell-Imbalance", min_(40.0f, max_(0.0f, (imb - 20) * 0.35f)), det);
    }
    long lvc = u(0x902E, 2);
    if (lvc > 0) {
      snprintf(det, sizeof(det), "%ld× tiefentladen geladen", lvc);
      total += part(j, firstPart, "Ladung mit Zelle < 2,5 V", min_(30.0f, lvc * 10.0f), det);
    }
    long oh = u(0x9032, 2);
    if (oh > 0) {
      snprintf(det, sizeof(det), "%ld Ereignisse", oh);
      total += part(j, firstPart, "Überhitzung", min_(15.0f, oh * 1.5f), det);
    }
    uint32_t man = date(0x0011);
    if (man && now > man) {
      float years = (now - man) / 86400.0f / 365.25f;
      snprintf(det, sizeof(det), "%.1f Jahre", years);
      total += part(j, firstPart, "Kalenderalter", min_(15.0f, years * 2.0f), det);
    }
    j.raw("],");
    if (das >= 0 && bt) j.kvf("cycles", das / 3600.0f / bt->ah, 1);
    int health = (int)(max_(0.0f, 100.0f - total) + 0.5f);
    j.kvn("health", firstPart ? -1 : health);
    j.kvs("status", firstPart ? "unknown" : health >= 80 ? "good" : health >= 60 ? "watch" : "weak");
    // Raw register blocks (hex), so the PC can decode a stored reading with the same
    // code as a live read-out (m18_bridge.registers_from_memory + decode_registers).
    j.key("mem"); j.raw("{");
    bool firstM = true;
    for (int i = 0; i < NUM_BLOCKS; i++) {
      const Block& b = BLOCKS[i];
      if (!b.len || !mem_.has(b.addr, b.len)) continue;
      char k[16];
      snprintf(k, sizeof(k), "%s\"%04X\":\"", firstM ? "" : ",", b.addr);
      firstM = false;
      j.raw(k);
      for (int x = 0; x < b.len; x++) { char hx[3]; snprintf(hx, sizeof(hx), "%02X", mem_.at(b.addr + x)); j.raw(hx); }
      j.raw("\"");
    }
    j.raw("}}");
    return j.len();
  }

 private:
  static const int ABORT_AFTER_FAILS = 3;

  bool resetWithRetry(int attempts = 3) {
    for (int attempt = 0; attempt < attempts; attempt++) {
      if (link_.reset(nullptr)) return true;
      link_.idle();
      port_.delayMs(250);
    }
    return false;
  }

  void fail(const char* why, uint32_t t0) {
    link_.idle();
    error_ = why;
    ok_ = false;
    durationMs_ = port_.millis() - t0;
  }

  bool readBlock(const Block& b, uint8_t* buf, bool store) {
    size_t want = (size_t)b.len + 5;
    size_t n = link_.command(0x01, 0x04, 0x03, (uint8_t)(b.addr >> 8), (uint8_t)b.addr, b.len,
                             buf, want, MAX_FRAME);
    if (n < (size_t)b.len + 3 || buf[0] != 0x81) return false;      // too short / error reply
    if (n == want) {                                                  // full frame: check the sum
      uint16_t sum = 0;
      for (size_t i = 0; i < n - 2; i++) sum += buf[i];
      if (((sum >> 8) & 0xFF) != buf[n - 2] || (sum & 0xFF) != buf[n - 1]) cksumMismatch_++;
    }
    if (store) mem_.store(b.addr, buf + 3, b.len);
    return true;
  }
  long u(uint16_t a, size_t n) const { return mem_.has(a, n) ? (long)mem_.be(a, n) : -1; }
  uint32_t date(uint16_t a) const {
    if (!mem_.has(a, 4)) return 0;
    uint32_t v = mem_.be(a, 4);
    return v < 1104537600UL ? 0 : v;   // before 2005 = unset
  }
  static const BatType* lookup(uint16_t code) {
    for (const BatType& t : BAT_TYPES) if (t.code == code) return &t;
    return nullptr;
  }
  static float min_(float a, float b) { return a < b ? a : b; }
  static float max_(float a, float b) { return a > b ? a : b; }

  // Minimal JSON writer into a fixed buffer.
  class Json {
   public:
    Json(char* o, size_t c) : o_(o), c_(c), n_(0) { if (c) o[0] = 0; }
    void raw(const char* s) { size_t l = strlen(s); if (n_ + l < c_) { memcpy(o_ + n_, s, l); n_ += l; o_[n_] = 0; } }
    void key(const char* k) { raw("\""); raw(k); raw("\":"); }
    void kv(const char* k, bool v, bool comma = true) { key(k); raw(v ? "true" : "false"); if (comma) raw(","); }
    void kvn(const char* k, long v, bool comma = true) {
      key(k); char b[24];
      if (v < 0) snprintf(b, sizeof(b), "null"); else snprintf(b, sizeof(b), "%ld", v);
      raw(b); if (comma) raw(",");
    }
    void kvf(const char* k, float v, int dec, bool comma = true) {
      key(k); char b[24]; snprintf(b, sizeof(b), "%.*f", dec, (double)v); raw(b); if (comma) raw(",");
    }
    void kvs(const char* k, const char* v, bool comma = true) { key(k); str(v); if (comma) raw(","); }
    void kvd(const char* k, uint32_t t, bool comma = true) {
      key(k);
      if (!t) raw("null");
      else { time_t tt = (time_t)t; struct tm g; gmtime_r(&tt, &g); char b[16]; strftime(b, sizeof(b), "\"%Y-%m-%d\"", &g); raw(b); }
      if (comma) raw(",");
    }
    void str(const char* s) {
      raw("\"");
      for (; *s; s++) {
        char b[8];
        if (*s == '"' || *s == '\\') { b[0] = '\\'; b[1] = *s; b[2] = 0; }
        else if ((unsigned char)*s < 0x20) snprintf(b, sizeof(b), "\\u%04x", *s);
        else { b[0] = *s; b[1] = 0; }
        raw(b);
      }
      raw("\"");
    }
    void endObj() { if (n_ && o_[n_ - 1] == ',') n_--; o_[n_] = 0; raw("}"); }
    size_t len() const { return n_; }
   private:
    char* o_; size_t c_; size_t n_;
  };

  static float part(Json& j, bool& first, const char* label, float penalty, const char* detail) {
    char b[24];
    if (!first) j.raw(",");
    first = false;
    j.raw("{\"label\":"); j.str(label);
    snprintf(b, sizeof(b), ",\"penalty\":%.1f,", (double)penalty); j.raw(b);
    j.raw("\"detail\":"); j.str(detail); j.raw("}");
    return penalty;
  }

  Link& link_;
  Port& port_;
  Memory mem_;
  bool ok_ = false;
  int answered_ = 0;
  int retries_ = 0;
  int cksumMismatch_ = 0;
  volatile int progress_ = 0;
  const char* error_ = nullptr;
  uint32_t durationMs_ = 0;
};

}  // namespace m18
