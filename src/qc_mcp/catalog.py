"""Device catalog: maps a block's numeric hash (== ModelRepo id) to its name,
the real gear it emulates (`tm`), category, and parameter schema.

Sourced from the QC's ModelRepo.xml (delivered over USB as the ModelRepo message,
a gzip+tar of ModelRepo.xml). A snapshot is bundled with the package; if the live
device sends a newer one, call load_from_bytes() to refresh.

The device names and emulated-gear references are Neural DSP's public device list:
https://neuraldsp.com/device-list
"""
from __future__ import annotations
import os
import xml.etree.ElementTree as ET

_XML = os.path.join(os.path.dirname(__file__), "ModelRepo.xml")
_by_id = None


def _parse(xml_bytes):
    root = ET.fromstring(xml_bytes)
    out = {}
    for cat in root.findall("Category"):
        cname = cat.get("name")
        cid = cat.get("id")
        for mdl in cat.findall("Model"):
            mid = mdl.get("id")
            params = [{"name": p.get("name"), "type": p.get("type"),
                       "default": p.get("defaultValue"), "min": p.get("min"),
                       "max": p.get("max"), "units": p.get("units")}
                      for p in mdl.findall("Parameter")]
            out[int(mid)] = {
                "id": int(mid), "name": mdl.get("name"),
                "tm": mdl.get("tm", ""), "category": cname,
                "category_id": int(cid), "params": params,
            }
    return out


def _catalog():
    global _by_id
    if _by_id is None:
        with open(_XML, "rb") as f:
            _by_id = _parse(f.read())
    return _by_id


def load_from_bytes(model_repo_payload):
    """Refresh the catalog from a live ModelRepo payload (gzip+tar of the xml)."""
    global _by_id
    import io, tarfile, zlib
    data = model_repo_payload
    if data[:3] == b"\x1f\x8b\x08":
        data = zlib.decompress(data, 16 + zlib.MAX_WBITS)
    tf = tarfile.open(fileobj=io.BytesIO(data))
    xml = tf.extractfile("ModelRepo.xml").read()
    _by_id = _parse(xml)
    return len(_by_id)


# Parameter taper (reverse-engineered by calibration): frequency/time/ratio
# params use a power taper  display = min + (max-min) * nv**LOG_TAPER  (nv in 0..1);
# level/dB/percent params are linear. Heuristic split: min>0 and max/min>=5 => log.
LOG_TAPER = 1.667

# Some ranges in ModelRepo.xml are symbolic names the app resolves from a table
# compiled into its binary, so the XML alone cannot convert those parameters to
# a display value. Each entry here is CALIBRATED against Cortex Control — read a
# stored normalized value, read the dB the app shows for it, solve the line — so
# only add a name once it has actually been measured. An unresolved name still
# falls through to "no conversion", exactly as before.
#
#   MIXER  LaneOutputControl(23000) VOLUME and the merge Mixer(11000) levels.
#          Measured: 0.5 -> -14.0 dB and the untouched default 0.769230783 -> 0 dB
#          on two lanes, which fits -40..+12 dB linear exactly (-40/52 = 10/13).
SYMBOLIC = {
    "MIN_MIXER_DB": -40.0,
    "MAX_MIXER_DB": 12.0,
}


def _bound(v):
    """A parameter bound as a float: a literal, a calibrated symbolic name, or None."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return SYMBOLIC.get(v)


def _prange(block_hash, param_index):
    m = lookup(block_hash)
    if not m or param_index >= len(m["params"]):
        return None
    p = m["params"][param_index]
    lo, hi = _bound(p["min"]), _bound(p["max"])
    if lo is None or hi is None:
        return None
    return lo, hi


def _is_log(lo, hi):
    return lo > 0 and hi / lo >= 5


def to_norm(block_hash, param_index, display_value):
    """Convert a display value to the normalized 0-1 the device stores."""
    rng = _prange(block_hash, param_index)
    if not rng:
        return float(display_value)
    lo, hi = rng
    if hi <= lo:
        return float(display_value)
    r = max(0.0, min(1.0, (float(display_value) - lo) / (hi - lo)))
    return r ** (1.0 / LOG_TAPER) if _is_log(lo, hi) else r


def to_display(block_hash, param_index, nv):
    """Convert a stored normalized 0-1 value back to its display value."""
    rng = _prange(block_hash, param_index)
    if not rng:
        return nv
    lo, hi = rng
    r = nv ** LOG_TAPER if _is_log(lo, hi) else nv
    return lo + r * (hi - lo)


def lookup(block_hash):
    """Return catalog info for a block hash, or None (hash 0 = empty slot)."""
    if not block_hash:
        return None
    return _catalog().get(int(block_hash))


def name_of(block_hash):
    m = lookup(block_hash)
    if not m:
        return "(empty)" if not block_hash else f"unknown#{block_hash}"
    tm = f" [{m['tm']}]" if m["tm"] else ""
    return f"{m['name']}{tm}"


def find(query=None, category=None):
    """Search the catalog by name/tm substring and/or category name substring."""
    q = (query or "").lower()
    c = (category or "").lower()
    res = []
    for m in _catalog().values():
        if q and q not in m["name"].lower() and q not in m["tm"].lower():
            continue
        if c and c not in m["category"].lower():
            continue
        res.append(m)
    return sorted(res, key=lambda m: m["id"])


def categories():
    cats = {}
    for m in _catalog().values():
        cats.setdefault((m["category_id"], m["category"]), 0)
        cats[(m["category_id"], m["category"])] += 1
    return sorted([(cid, cn, n) for (cid, cn), n in cats.items()])
