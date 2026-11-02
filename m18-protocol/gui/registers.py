"""
Decoding of M18 register values (as returned by m18.M18.read_id(output="array"),
keyed by address) into the snapshot dict used by the fleet database.

Shared by live read-outs and by readings imported from the ESP32 bridge, so both
produce identical database rows.
"""

import contextlib
import datetime as _dt
import re

from fleet import iso

def _date(v):
    """datetime from m18.py -> ISO string, ignoring unset (epoch-ish) dates."""
    if isinstance(v, _dt.datetime):
        if v.year < 2005:
            return None
        return iso(v if v.tzinfo else v.replace(tzinfo=_dt.timezone.utc))
    return None


def _int(v):
    return v if isinstance(v, int) else None


def _hhmmss(v):
    if not isinstance(v, str) or ":" not in v:
        return None
    try:
        h, m, s = (int(x) for x in v.split(":"))
        return h * 3600 + m * 60 + s
    except ValueError:
        return None


def decode_registers(by_addr):
    """Turn {address: value} from M18.read_id(output='array') into a snapshot."""
    g = by_addr.get
    sn = str(g(0x0004) or "")
    nums = re.findall(r"\d+", sn)
    type_code = int(nums[0]) if len(nums) >= 1 else None
    serial_num = int(nums[1]) if len(nums) >= 2 else None
    eserial = f"{type_code}-{serial_num}" if serial_num is not None else None

    cells_mv = [c for c in (g(0x400A) or []) if isinstance(c, int)]
    cells = [round(c / 1000.0, 3) for c in cells_mv]
    imbalance = (max(cells_mv) - min(cells_mv)) if cells_mv else None

    # Forge packs report 0x401F, others 0x4014; the unused one reads as 0.
    temp = None
    for addr in (0x401F, 0x4014):
        with contextlib.suppress(TypeError, ValueError):
            v = float(g(addr))
            if v != 0.0:
                temp = round(v, 1)
                break

    tool_time = 0
    have_hist = False
    for addr in range(0x903A, 0x9062, 2):  # discharge histogram 10A .. 200A+
        if isinstance(g(addr), int):
            tool_time += g(addr)
            have_hist = True

    note = str(g(0x0023) or "").strip('"').rstrip("-").strip()
    return {
        "ok": eserial is not None,
        "eserial": eserial,
        "type_code": type_code,
        "serial_num": serial_num,
        "serial": sn,
        "cell_type": _int(g(0x0000)),
        "manufactured": _date(g(0x0011)),
        "first_charge": _date(g(0x0015)) or _date(g(0x9000)),
        "last_charge": _date(g(0x0019)) or _date(g(0x9008)),
        "last_tool_use": _date(g(0x9004)),
        "note": note,
        "cells": cells,
        "pack_v": round(sum(cells), 2) if cells else None,
        "temp": temp,
        "imbalance_mv": imbalance,
        "counters": {
            "total_charges": _int(g(0x901A)),
            "redlink_charges": _int(g(0x9020)),
            "dumb_charges": _int(g(0x901E)),
            "discharge_as": _int(g(0x9012)),
            "discharge_ws": _int(g(0x9016)),
            "overheat": _int(g(0x9032)),
            "overcurrent": _int(g(0x9034)),
            "lowvolt": _int(g(0x9036)),
            "lowvolt_bounce": _int(g(0x9038)),
            "discharge_to_empty": _int(g(0x9030)),
            "lowv_charges": _int(g(0x902E)),
            "days": _int(g(0x9010)),
            "charge_time_s": _hhmmss(g(0x9024)),
            "tool_time_s": tool_time if have_hist else None,
        },
        "raw": {f"0x{a:04X}": (_date(v) if isinstance(v, _dt.datetime) else v)
                for a, v in by_addr.items()},
    }


