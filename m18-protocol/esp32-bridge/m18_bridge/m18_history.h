// Measurement history of the M18 bridge: every successful read-out is stored in
// flash so the PC can import it into its database later (offline use with a phone).
//
// One file per record: <dir>/<id 8 digits>.json   (".s.json" once imported by the PC)
//   line 1: small meta JSON (id, time, battery, health, location, comment)
//   line 2: full read-out JSON from Reader::toJson() including raw register blocks
//
// Pure C++ (std::string/vector) on top of a tiny Storage interface: FFat/LittleFS
// on the ESP32, std::filesystem in the PC test.

#pragma once
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <algorithm>
#include <string>
#include <vector>

namespace m18 {

struct Storage {
  virtual ~Storage() {}
  virtual bool ok() = 0;
  virtual bool mkdir(const std::string& dir) = 0;
  virtual void list(const std::string& dir, std::vector<std::string>& names) = 0;   // base names
  virtual bool read(const std::string& path, std::string& out, bool firstLineOnly) = 0;
  virtual bool write(const std::string& path, const std::string& data) = 0;          // atomic (tmp + rename)
  virtual bool remove(const std::string& path) = 0;
  virtual bool rename(const std::string& from, const std::string& to) = 0;
  virtual uint64_t freeBytes() = 0;
  virtual uint64_t totalBytes() = 0;
};

struct RecordMeta {
  uint32_t ts = 0;               // unix time, 0 = unknown
  std::string tsSrc = "none";    // ntp | browser | pc | none
  uint32_t boot = 0;             // random id of the boot the record was taken in
  uint32_t upMs = 0;             // uptime when taken (lets the PC date it if ts is unknown)
  std::string loc, comment;      // optional, entered on the phone
};

inline std::string jsonEscape(const std::string& v) {
  std::string o;
  for (unsigned char c : v) {
    if (c == '"' || c == '\\') { o += '\\'; o += (char)c; }
    else if (c < 0x20) { char b[8]; snprintf(b, sizeof(b), "\\u%04x", c); o += b; }
    else o += (char)c;
  }
  return o;
}

// Minimal value lookup in our own flat JSON (keys are unique in Reader::toJson output).
inline std::string jsonRawValue(const std::string& json, const char* key) {
  std::string k = std::string("\"") + key + "\":";
  size_t p = json.find(k);
  if (p == std::string::npos) return "";
  p += k.size();
  if (p < json.size() && json[p] == '"') {
    size_t e = p + 1;
    while (e < json.size() && json[e] != '"') e += (json[e] == '\\') ? 2 : 1;
    return json.substr(p, e - p + 1);          // including quotes
  }
  size_t e = p;
  while (e < json.size() && json[e] != ',' && json[e] != '}' && json[e] != ']') e++;
  return json.substr(p, e - p);
}

class History {
 public:
  History(Storage& st, const char* dir = "/m18h") : st_(st), dir_(dir) {}

  size_t maxRecords = 3000;               // ~3 KB each
  uint64_t minFreeBytes = 256 * 1024;

  // Optional lock (the ESP sketch passes a FreeRTOS mutex): every public call is serialised.
  void setLock(void (*lock)(), void (*unlock)()) { lock_ = lock; unlock_ = unlock; }

  bool begin() {
    Guard g(this);
    ready_ = false;
    if (!st_.ok()) return false;
    st_.mkdir(dir_);
    rescan();
    ready_ = true;
    return true;
  }
  bool ready() const { return ready_; }

  // Stores a read-out; returns the new id (0 on failure).
  uint32_t add(const std::string& scanJson, const RecordMeta& m) {
    Guard g(this);
    if (!ready_) return 0;
    uint32_t id = nextId_++;
    std::string meta = "{\"id\":" + std::to_string(id) + ",\"ts\":" + std::to_string(m.ts) +
        ",\"ts_src\":\"" + jsonEscape(m.tsSrc) + "\",\"boot\":" + std::to_string(m.boot) +
        ",\"up\":" + std::to_string(m.upMs) + ",\"eserial\":" + orNull(jsonRawValue(scanJson, "eserial")) +
        ",\"model\":" + orNull(jsonRawValue(scanJson, "model")) + ",\"health\":" + orNull(jsonRawValue(scanJson, "health")) +
        ",\"note\":" + orNull(jsonRawValue(scanJson, "note")) + ",\"loc\":\"" + jsonEscape(m.loc) +
        "\",\"comment\":\"" + jsonEscape(m.comment) + "\"}";
    makeRoom();
    if (!st_.write(path(id, false), meta + "\n" + scanJson + "\n")) return 0;
    ids_.push_back(Entry{id, false});
    return id;
  }

  // Page of record metas, ascending ids > after. JSON: {"items":[...],"next":id|0,"count":n,"unsynced":u}
  std::string list(uint32_t after, int max, bool unsyncedOnly) {
    Guard g(this);
    std::string out = "{\"items\":[";
    int n = 0;
    uint32_t last = 0;
    bool more = false;
    for (const Entry& e : ids_) {
      if (e.id <= after || (unsyncedOnly && e.synced)) continue;
      if (n >= max) { more = true; break; }
      std::string line;
      if (!st_.read(path(e.id, e.synced), line, true) || line.size() < 2 || line[0] != '{') continue;
      if (n++) out += ",";
      out += withSynced(line, e.synced);
      last = e.id;
    }
    out += "],\"next\":" + std::to_string(more ? last : 0) + "," + statsFields() + "}";
    return out;
  }

  // {"meta":{...},"scan":{...}}
  bool get(uint32_t id, std::string& out) {
    Guard g(this);
    const Entry* e = find(id);
    if (!e) return false;
    std::string all;
    if (!st_.read(path(id, e->synced), all, false)) return false;
    size_t nl = all.find('\n');
    if (nl == std::string::npos) return false;
    std::string scan = all.substr(nl + 1);
    while (!scan.empty() && (scan.back() == '\n' || scan.back() == '\r')) scan.pop_back();
    out = "{\"meta\":" + withSynced(all.substr(0, nl), e->synced) + ",\"scan\":" + scan + "}";
    return true;
  }

  bool ack(uint32_t id) {                // imported by the PC
    Guard g(this);
    Entry* e = find(id);
    if (!e) return false;
    if (e->synced) return true;
    if (!st_.rename(path(id, false), path(id, true))) return false;
    e->synced = true;
    return true;
  }

  bool remove(uint32_t id) {
    Guard g(this);
    Entry* e = find(id);
    if (!e) return false;
    bool ok = st_.remove(path(id, e->synced));
    if (ok) ids_.erase(ids_.begin() + (e - ids_.data()));
    return ok;
  }

  int clear(bool syncedOnly) {
    Guard g(this);
    int n = 0;
    for (size_t i = ids_.size(); i-- > 0;) {
      if (syncedOnly && !ids_[i].synced) continue;
      if (st_.remove(path(ids_[i].id, ids_[i].synced))) { ids_.erase(ids_.begin() + i); n++; }
    }
    return n;
  }

  size_t count() { Guard g(this); return ids_.size(); }
  size_t unsynced() { Guard g(this); return unsyncedNoLock(); }

  std::string stats() { Guard g(this); return "{" + statsFields() + "}"; }

 private:
  struct Entry { uint32_t id; bool synced; };
  struct Guard {
    explicit Guard(History* h) : h_(h) { if (h_->lock_) h_->lock_(); }
    ~Guard() { if (h_->unlock_) h_->unlock_(); }
    History* h_;
  };
  size_t unsyncedNoLock() const { size_t u = 0; for (auto& e : ids_) u += e.synced ? 0 : 1; return u; }

  std::string path(uint32_t id, bool synced) const {
    char b[24];
    snprintf(b, sizeof(b), "/%08lu%s.json", (unsigned long)id, synced ? ".s" : "");
    return dir_ + b;
  }
  static std::string orNull(const std::string& v) { return v.empty() ? "null" : v; }
  static std::string withSynced(const std::string& metaLine, bool synced) {
    return std::string("{\"synced\":") + (synced ? "true," : "false,") + metaLine.substr(1);
  }
  std::string statsFields() {
    return "\"count\":" + std::to_string(ids_.size()) + ",\"unsynced\":" + std::to_string(unsyncedNoLock()) +
           ",\"free_kb\":" + std::to_string((unsigned long long)(st_.freeBytes() / 1024)) +
           ",\"total_kb\":" + std::to_string((unsigned long long)(st_.totalBytes() / 1024)) +
           ",\"ready\":" + (ready_ ? "true" : "false");
  }
  Entry* find(uint32_t id) {
    for (auto& e : ids_) if (e.id == id) return &e;
    return nullptr;
  }

  void rescan() {
    ids_.clear();
    std::vector<std::string> names;
    st_.list(dir_, names);
    uint32_t maxId = 0;
    for (const std::string& n : names) {
      if (n.size() >= 4 && n.compare(n.size() - 4, 4, ".tmp") == 0) { st_.remove(dir_ + "/" + n); continue; }
      char* end = nullptr;
      unsigned long id = strtoul(n.c_str(), &end, 10);
      if (!id || !end) continue;
      bool synced = !strcmp(end, ".s.json");
      if (!synced && strcmp(end, ".json")) continue;
      ids_.push_back(Entry{(uint32_t)id, synced});
      if (id > maxId) maxId = (uint32_t)id;
    }
    std::sort(ids_.begin(), ids_.end(), [](const Entry& a, const Entry& b) { return a.id < b.id; });
    nextId_ = maxId + 1;
  }

  // Rotation: drop the oldest imported records first, then the oldest overall.
  void makeRoom() {
    while (!ids_.empty() && (ids_.size() >= maxRecords || st_.freeBytes() < minFreeBytes)) {
      size_t victim = 0;
      for (size_t i = 0; i < ids_.size(); i++) if (ids_[i].synced) { victim = i; break; }
      st_.remove(path(ids_[victim].id, ids_[victim].synced));
      ids_.erase(ids_.begin() + victim);
    }
  }

  Storage& st_;
  std::string dir_;
  std::vector<Entry> ids_;
  uint32_t nextId_ = 1;
  bool ready_ = false;
  void (*lock_)() = nullptr;
  void (*unlock_)() = nullptr;
};

}  // namespace m18
