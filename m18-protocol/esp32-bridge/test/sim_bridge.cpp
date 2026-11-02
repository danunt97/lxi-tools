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

#include "m18_commands.h"

using m18::rev8;

// A fake battery that understands the wire format (bit-reversed bytes).
class SimBattery : public m18::Port {
 public:
  bool present = true;
  bool idle = true;
  // fault injection
  int dropEvery = 0;      // swallow every n-th command reply
  int failResets = 0;     // ignore the first n sync bytes
  int dieAfter = 0;       // battery "unplugged" after n commands
  int commands = 0;
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
    put(0x9036, {0x00, 0x06});                                    // 6 low-voltage events
    put(0x0037, {0x69, 0x05, 0x4D, 0x80});                        // battery clock 2025-11-01
    put(0x903A, {0x0E, 0x10, 0x07, 0x08});                        // 3600 s @10-20A, 1800 s @20-30A
  }

  // --- m18::Port
  void setIdle(bool i) override { idle = i; }
  void flushInput() override { rx.clear(); }
  void write(const uint8_t* d, size_t n) override {
    if (idle || !present) return;                 // TX held low: nothing reaches the pack
    std::vector<uint8_t> p;
    for (size_t i = 0; i < n; i++) p.push_back(rev8(d[i]));
    if (p.size() == 1 && p[0] == 0xAA) {
      if (failResets > 0) { failResets--; return; }
      reply({0xAA});
      return;
    }
    if (p.size() != 8) return;
    commands++;
    if (dieAfter && commands >= dieAfter) { present = false; return; }
    if (dropEvery && commands % dropEvery == 0) return;
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
  static char buf[2048];
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
  m18::Reader reader(link, bat);
  m18::CommandProcessor p(link, reader);
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
  // direct read-out on the bridge
  std::string scan = run(p, "SCAN");
  std::printf("      %s\n", scan.c_str());
  for (const char* want : {"OK {\"ok\":true", "\"blocks_ok\":32,\"blocks_total\":32", "\"eserial\":\"306-412877\"",
                           "\"model\":\"5Ah XC (2021-2023)\"", "\"manufactured\":\"2022-03-14\"",
                           "\"battery_date\":\"2025-11-01\"", "\"note\":\"B-07\"",
                           "\"cells_mv\":[4012,4010,4015,4009,4011]", "\"pack_v\":20.06", "\"imbalance_mv\":6",
                           "\"temp_c\":23.0", "\"total_charges\":260", "\"overheat\":2", "\"lowvolt\":6",
                           "\"tool_time_s\":5400", "\"discharge_ah\":1000.0", "\"cycles\":200.0",
                           "\"health\":82", "\"status\":\"good\"", "\"label\":\"Kalenderalter\""}) {
    expect(scan.find(want) != std::string::npos ? want : scan.substr(0, 40), want);
  }
  expect(bat.idle ? "idle" : "high", "idle");                            // SCAN leaves J2 low

  {  // flaky line: lost replies and ignored resets are recovered by retries
    SimBattery b2;
    b2.dropEvery = 7;
    b2.failResets = 2;
    m18::Link l2(b2);
    m18::Reader r2(l2, b2);
    m18::CommandProcessor p2(l2, r2);
    std::string sc = run(p2, "SCAN");
    expect(sc.find("\"blocks_ok\":32,") != std::string::npos ? "flaky: 32/32" : sc.substr(0, 90), "flaky: 32/32");
    expect(sc.find("\"retries\":0,") == std::string::npos ? "flaky: retried" : "no retries", "flaky: retried");
    expect(sc.rfind("OK {", 0) == 0 ? "flaky: OK" : sc.substr(0, 60), "flaky: OK");
  }
  {  // battery pulled mid-read: quick abort with a clear error
    SimBattery b3;
    b3.dieAfter = 45;
    m18::Link l3(b3);
    m18::Reader r3(l3, b3);
    m18::CommandProcessor p3(l3, r3);
    std::string sc = run(p3, "SCAN");
    expect(sc.find("Verbindung zum Akku abgebrochen") != std::string::npos ? "unplug: error" : sc.substr(0, 120), "unplug: error");
    expect(b3.clock < 15000 ? "unplug: fast" : "unplug: slow " + std::to_string(b3.clock), "unplug: fast");
    expect(b3.idle ? "idle" : "high", "idle");
  }
  {  // implausible cell voltages are flagged
    SimBattery b4;
    b4.mem[0x400A] = 0x00; b4.mem[0x400B] = 0x00;     // cell 1 = 0 mV
    m18::Link l4(b4);
    m18::Reader r4(l4, b4);
    m18::CommandProcessor p4(l4, r4);
    std::string sc = run(p4, "SCAN");
    expect(sc.find("Zellspannungen unplausibel") != std::string::npos ? "plausibility" : sc.substr(0, 80), "plausibility");
  }
  bat.present = false;                                                   // battery unplugged
  expect(run(p, "SCAN"), "ERR {\"ok\":false,\"blocks_ok\":0", true);
  expect(run(p, "RESET"), "ERR NOACK");
  expect(run(p, "READ 0x400A 10"), "ERR TIMEOUT");
  std::printf("%s (%d failures)\n", failures ? "FAILED" : "ALL PASSED", failures);
  return failures ? 1 : 0;
}

int serve() {
  SimBattery bat;
  m18::Link link(bat);
  m18::Reader reader(link, bat);
  m18::CommandProcessor p(link, reader);
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
