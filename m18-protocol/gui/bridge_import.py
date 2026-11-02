"""
Import read-outs stored on the ESP32 bridge (offline measurements, e.g. taken with
a phone at a customer's site) into the fleet database.

Each bridge record holds the decoded values for display *and* the raw register
blocks. The import decodes the raw blocks with exactly the same code as a live
read-out (m18_bridge.registers_from_memory + registers.decode_registers), so an
imported reading is indistinguishable from one taken at the PC.
"""

import datetime as _dt

import m18_bridge
from fleet import parse_ts, utcnow
from registers import decode_registers


def snapshot_from_record(rec):
    scan = rec.get("scan") or {}
    return decode_registers(m18_bridge.registers_from_memory(scan.get("mem") or {}))


def record_time(meta, scan, boot_info=None, now=None):
    """Best available timestamp for a stored record -> (datetime, how)."""
    now = now or utcnow()
    ts = int(meta.get("ts") or 0)
    if ts > 1600000000:
        return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc), meta.get("ts_src") or "bridge"
    # no clock on the bridge, but it has not rebooted since: count back its uptime
    if boot_info and meta.get("boot") is not None and meta.get("boot") == boot_info.get("boot"):
        age_ms = int(boot_info.get("uptime_ms", 0)) - int(meta.get("up") or 0)
        if age_ms >= 0:
            return (now - _dt.timedelta(milliseconds=age_ms)).replace(microsecond=0), "uptime"
    bd = parse_ts(scan.get("battery_date"))
    if bd:
        return bd.replace(hour=12), "battery_clock"
    return now, "import"


def _is_duplicate(db, bid, snap):
    """Same pack, same counters and same cell voltages = the same read-out."""
    c = snap.get("counters") or {}
    for r in db.readings(bid):
        rc = r.get("counters") or {}
        if (rc.get("discharge_as") == c.get("discharge_as") and rc.get("total_charges") == c.get("total_charges")
                and [round(v, 3) for v in r.get("cells") or []] == [round(v, 3) for v in snap.get("cells") or []]):
            return True
    return False


def import_records(db, records, boot_info=None, now=None):
    """Import bridge records ({"meta": {...}, "scan": {...}}). Returns a summary dict;
    "done_ids" lists the bridge record ids that are now in the database (imported
    or already present) and may be acknowledged on the bridge."""
    now = now or utcnow()
    res = {"imported": 0, "created": 0, "duplicates": 0, "errors": [], "items": [], "done_ids": []}
    for rec in records:
        meta = rec.get("meta") or {}
        rid = meta.get("id")
        try:
            snap = snapshot_from_record(rec)
        except Exception as e:  # corrupt record: report, keep going
            res["errors"].append(f"Messung #{rid}: nicht lesbar ({e})")
            continue
        if not snap.get("ok"):
            res["errors"].append(f"Messung #{rid}: keine Seriennummer in den Rohdaten")
            continue
        ts, how = record_time(meta, rec.get("scan") or {}, boot_info, now)
        bat = db.get_battery(snap["eserial"])
        created = False
        if bat is None:
            bat = db.create_battery(snap, {"name": snap.get("note") or "", "location": meta.get("loc") or "",
                                           "comment": meta.get("comment") or ""})
            created = True
            res["created"] += 1
        if _is_duplicate(db, bat["id"], snap):
            res["duplicates"] += 1
            res["done_ids"].append(rid)
            res["items"].append({"id": rid, "eserial": bat["eserial"], "name": bat["name"], "status": "duplicate"})
            continue
        fields = {}
        if meta.get("loc") and not created:
            fields["location"] = meta["loc"]
        if meta.get("comment") and not created:
            stamp = ts.strftime("%d.%m.%Y")
            fields["comment"] = ((bat.get("comment") or "") + f"\n[{stamp}] {meta['comment']}").strip()
        if fields:
            bat = db.update_battery(bat["id"], fields)
        reading, alerts = db.add_reading(bat["id"], snap, ts=ts, source="esp")
        res["imported"] += 1
        res["done_ids"].append(rid)
        res["items"].append({"id": rid, "eserial": bat["eserial"], "name": bat["name"], "status": "imported",
                             "created": created, "ts": reading["ts"], "time_source": how,
                             "health": reading["health"], "alerts": alerts})
    return res


def import_from_bridge(db, bridge, ids=None):
    """Fetch new records from a connected M18Bridge, import them and acknowledge
    them on the bridge (so they are not offered again)."""
    metas = bridge.history_list(new_only=True)
    if ids is not None:
        wanted = {int(i) for i in ids}
        metas = [m for m in metas if int(m["id"]) in wanted]
    boot = bridge.boot_info()
    records = [bridge.history_get(m["id"]) for m in metas]
    res = import_records(db, records, boot_info=boot)
    for rid in res["done_ids"]:
        try:
            bridge.history_ack(rid)
        except m18_bridge.BridgeError as e:
            res["errors"].append(f"Messung #{rid}: Bestätigung an die Brücke fehlgeschlagen ({e})")
    return res
