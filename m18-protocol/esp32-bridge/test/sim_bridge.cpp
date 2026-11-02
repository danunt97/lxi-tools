// PC test for the ESP32 bridge protocol core (m18_proto.h) with a simulated
// M18 battery on the other end of the "UART".
//
//   g++ -std=c++17 -Wall -Wextra -I ../m18_bridge sim_bridge.cpp -o sim_bridge
//   ./sim_bridge test     # self-test
//   ./sim_bridge serve    # speak the bridge line protocol on stdin/stdout
//                         # (used by tests/test_bridge.py through a pty / HTTP shim)

#include <cassert>
#include <cstdio>
#include <cstring>
#include <deque>
#include <iostream>
#include <map>
#include <string>
#include <vector>

#include "m18_proto.h"

using m18::rev8;

// A fake battery that understands the wire format (bit-reversed bytes).
class SimBattery : public m18::Port {
 public:
  bool present = true;
  bool idle = true;
  uint32_t clock = 0;
  std::map<uint16_t, uint8_t> mem;
  std::deque<uint8_t> rx;   // bytes travelling battery -> bridge (already reversed on the wire)

  SimBattery() {
    auto put = [&](uint16_t a, std::vector<uint8_t> v) { for (size_t i = 0; i < v.size(); i++) mem[a + i] = v[i]; };
    put(0x0000, {0x00, 0x05});
    put(0x0004, {0x01, 0x32, 0x06, 0x4C, 0xCD});                  // type 306, serial 412877
    put(0x0011, {0x62, 0x2E, 0x85, 0x80});                        // 2022-03-14
    std::string note = "A-07----------------";
    for (size_t i = 0; i < note.size(); i++) mem[0x23 + i] = (uint8_t)note[i];
    put(0x400A, {0x0F, 0xAC, 0x0F, 0xAA, 0x0F, 0xAF, 0x0F, 0xA9, 0x0F, 0xAB});  // 4012,4010,4015,4009,4011 mV
    put(0x4014, {0x02, 0xB9});                                    // thermistor ADC ~ 23 C (non-Forge)
    put(0x9010, {0x06, 0x86});                                    // 1670 days
    put(0x9012, {0x00, 0x36, 0xEE, 0x80});                        // 3,600,000 As
    put(0x901A, {0x00, 0x00, 0x01, 0x04});                        // 260 charges
    put(0x9032, {0x00, 0x02});                                    // 2 overheats
  }

  // --- m18::Port
  void setIdle(bool i) override { idle = i; }
  void flushInput() override { rx.clear(); }
  void write(const uint8_t* d, size_t n) override {
    if (idle || !present) return;                 // TX held low: nothing reaches the pack
    std::vector<uint8_t> p;
    for (size_t i = 0; i < n; i++) p.push_back(rev8(d[i]));
    if (p.size() == 1 && p[0] == 0xAA) { reply({0xAA}); return; }
    if (p.size() != 8) return;
    uint16_t sum = 0;
    for (int i = 0; i < 6; i++) sum += p[i];
    if (((sum >> 8) & 0xFF) != p[6] || (sum & 0xFF) != p[7]) { reply({0x82, 0x01}); return; }
    uint16_t addr = (uint16_t)(p[3] << 8 | p[4]);
    if (p[0] == 0x01 && p[1] == 0x04 && p[2] == 0x03) {
      std::vector<uint8_t> r = {0x81, 0x04, p[5]};
      for (int i = 0; i < p[5]; i++) r.push_back(mem.count(addr + i) ? mem[addr + i] : 0);
      uint16_t cs = 0;
      for (uint8_t b : r) cs += b;
      r.push_back((uint8_t)(cs >> 8));
      r.push_back((uint8_t)cs);
      reply(r);
    } else if (p[0] == 0x01 && p[1] == 0x05 && p[2] == 0x03) {
      mem[addr] = p[5];
      reply({0x81, 0x05});
    } else {
      reply({0x82, 0x02});
    }
  }
  int readByte(uint32_t timeoutMs) override {
    if (rx.empty()) { clock += timeoutMs; return -1; }
    uint8_t b = rx.front();
    rx.pop_front();
    clock += 2;  // ~2 ms per byte at 4800 baud 8N2
    return b;
  }
  void delayMs(uint32_t ms) override { clock += ms; }
  uint32_t millis() override { return clock; }

 private:
  void reply(std::vector<uint8_t> v) { for (uint8_t b : v) rx.push_back(rev8(b)); }
};

static std::string run(m18::CommandProcessor& p, const char* line) {
  char buf[200];
  p.handle(line, buf, sizeof(buf));
  return buf;
}

static int failures = 0;
static void expect(const std::string& got, const std::string& want, bool prefix = false) {
  bool ok = prefix ? got.rfind(want, 0) == 0 : got == want;
  std::printf("%s  %-46s %s\n", ok ? "ok  " : "FAIL", got.c_str(), ok ? "" : ("(want " + want + ")").c_str());
  if (!ok) failures++;
}

int selftest() {
  assert(rev8(0x01) == 0x80 && rev8(0xAA) == 0x55 && rev8(0x81) == 0x81 && rev8(0x82) == 0x41);
  SimBattery bat;
  m18::Link link(bat);
  m18::CommandProcessor p(link);
  expect(run(p, "ping"), "OK M18BRIDGE " M18BRIDGE_VERSION);
  expect(run(p, "READ 0x400A 10"), "ERR IDLE", true);                  // must RESET first
  expect(run(p, "RESET"), "OK ACK");
  expect(run(p, "READ 0x400A 10"), "OK 81040A0FAC0FAA0FAF0FA90FAB0433");
  expect(run(p, "READ 0x0004 5"), "OK 8104050132064CCD", true);
  expect(run(p, "READ 16394 10"), "OK 81040A0FAC", true);               // decimal address
  expect(run(p, "NOTE 0x23 0x42"), "OK 8105");
  expect(run(p, "READ 0x0023 1"), "OK 8104014200C8");
  expect(run(p, "NOTE 0x9032 0"), "ERR ADDR", true);                    // counters are read-only
  expect(run(p, "READ 0x400A 99"), "ERR LEN");
  expect(run(p, "READ zz 1"), "ERR ARGS");
  expect(run(p, "FLASH"), "ERR UNKNOWN FLASH");
  expect(run(p, "IDLE"), "OK");
  expect(bat.idle ? "idle" : "high", "idle");
  expect(run(p, "INFO"), "OK {\"version\":\"" M18BRIDGE_VERSION "\",\"line\":\"idle\"}");
  bat.present = false;                                                   // battery unplugged
  expect(run(p, "RESET"), "ERR NOACK");
  expect(run(p, "READ 0x400A 10"), "ERR TIMEOUT");
  std::printf("%s (%d failures)\n", failures ? "FAILED" : "ALL PASSED", failures);
  return failures ? 1 : 0;
}

int serve() {
  SimBattery bat;
  m18::Link link(bat);
  m18::CommandProcessor p(link);
  std::printf("M18BRIDGE READY %s wifi=off ip=\n", M18BRIDGE_VERSION);
  std::fflush(stdout);
  std::string line;
  while (std::getline(std::cin, line)) {
    if (!line.empty() && line.back() == '\r') line.pop_back();
    if (line.empty()) continue;
    std::printf("%s\n", run(p, line.c_str()).c_str());
    std::fflush(stdout);
  }
  return 0;
}

int main(int argc, char** argv) {
  if (argc > 1 && !std::strcmp(argv[1], "serve")) return serve();
  return selftest();
}
