"""Preset model + builder: construct/inspect a full BinaryPreset (chains, blocks,
per-scene params, splitters/mixers, routing, bypass, metadata) so we can build
complex multi-amp presets.

`describe(bp)` -> plain-dict spec; `build(spec)` -> BinaryPreset. The two are
inverses on every field they cover, so `describe(build(spec)) == spec`. A
`PresetBuilder` gives an ergonomic API on top.
"""
from __future__ import annotations

from . import protocol as P

SCENES = 8


def _cls(name):
    return P._msg_class(P.pool().FindMessageTypeByName(name))


def BinaryPreset():
    return _cls("BinaryPreset")()


# --- describe: BinaryPreset -> spec -----------------------------------------
def _pv(pv):
    # ParamValue is a oneof(int/float/string); keep only the active field non-None so
    # presence round-trips exactly (e.g. string_value="" is distinct from float 0.0).
    w = pv.WhichOneof("value")
    return [pv.float_value if w == "float_value" else None,
            pv.int_value if w == "int_value" else None,
            pv.string_value if w == "string_value" else None]


def _describe_model(m):
    return {
        "hash": m.hash, "column": m.column,
        "notify_flag": m.notify_flag,
        "params": [{"index": p.index, "scene_mode": p.scene_mode,
                    "expression": p.expression,
                    "expr_min": round(p.expression_min, 6),
                    "expr_max": round(p.expression_max, 6),
                    "values": [_pv(pv) for pv in p.param_values]}
                   for p in m.params],
    }


def _describe_chain(ch):
    md = _describe_model
    return {
        "row": ch.row, "in_portid": ch.in_portid, "out_portid": ch.out_portid,
        "split_points": [[s.split, s.mix] for s in ch.split_control_points],
        "models": [md(m) for m in ch.models],
        "splitter": [md(m) for m in ch.splitter],
        "mixer": [md(m) for m in ch.mixer],
        "combined_splitter": [md(m) for m in ch.combined_splitter],
        "input_control": [md(m) for m in ch.input_control],
        "output_control": [md(m) for m in ch.output_control],
        "split_bypass": [sb.bypass for sb in ch.splitBypass],
        "mix_bypass": [sb.bypass for sb in ch.mixBypass],
    }


def _describe_bypass(b):
    return {"row": b.row,
            "cols": [{"column": cb.column, "scene_mode": cb.sceneMode,
                      "scene_bypass": [sb.bypass for sb in cb.sceneBypass]}
                     for cb in b.colBypass]}


def describe(bp):
    return {
        "name": bp.name, "tempo": bp.tempo,
        "volume": round(bp.volume, 6), "pan": round(bp.pan, 6),
        "default_scene": bp.default_scene,
        "scene_labels": list(bp.scene_labels),
        "scene_colors": list(bp.scene_colors),
        "chains": [_describe_chain(ch) for ch in bp.chains],
        "bypass": [_describe_bypass(b) for b in bp.bypass],
    }


# --- build: spec -> BinaryPreset --------------------------------------------
def _build_model(dst, spec):
    dst.hash = spec.get("hash", 0)
    dst.column = spec.get("column", 0)
    if spec.get("notify_flag"):
        dst.notify_flag = True
    for ps in spec.get("params", []):
        p = dst.params.add()
        p.index = ps.get("index", 0)
        if ps.get("scene_mode"):
            p.scene_mode = True
        if ps.get("expression"):
            p.expression = ps["expression"]
        if ps.get("expr_min"):
            p.expression_min = ps["expr_min"]
        if ps.get("expr_max") is not None:
            p.expression_max = ps["expr_max"]
        for f, i, s in ps.get("values", []):
            pv = p.param_values.add()
            if f is not None:
                pv.float_value = f
            elif i is not None:
                pv.int_value = i
            elif s is not None:
                pv.string_value = s
    return dst


def _build_chain(dst, spec):
    dst.row = spec.get("row", 0)
    dst.in_portid = spec.get("in_portid", 0)
    dst.out_portid = spec.get("out_portid", 0)
    for sp in spec.get("split_points", []):
        scp = dst.split_control_points.add()
        scp.split, scp.mix = sp[0], sp[1]
    for field in ("models", "splitter", "mixer", "combined_splitter",
                  "input_control", "output_control"):
        repeated = getattr(dst, field)
        for ms in spec.get(field, []):
            _build_model(repeated.add(), ms)
    for b in spec.get("split_bypass", []):
        dst.splitBypass.add().bypass = b
    for b in spec.get("mix_bypass", []):
        dst.mixBypass.add().bypass = b
    return dst


def _build_bypass(dst, spec):
    dst.row = spec.get("row", 0)
    for cs in spec.get("cols", []):
        cb = dst.colBypass.add()
        cb.column = cs.get("column", 0)
        if cs.get("scene_mode"):
            cb.sceneMode = True
        for b in cs.get("scene_bypass", []):
            cb.sceneBypass.add().bypass = b
    return dst


def build(spec):
    bp = BinaryPreset()
    bp.name = spec.get("name", "")
    bp.tempo = spec.get("tempo", 0)
    if spec.get("volume") is not None:
        bp.volume = spec["volume"]
    if spec.get("pan") is not None:
        bp.pan = spec["pan"]
    bp.default_scene = spec.get("default_scene", 0)
    for lbl in spec.get("scene_labels", []):
        bp.scene_labels.append(lbl)
    for col in spec.get("scene_colors", []):
        bp.scene_colors.append(col)
    for cs in spec.get("chains", []):
        _build_chain(bp.chains.add(), cs)
    for bs in spec.get("bypass", []):
        _build_bypass(bp.bypass.add(), bs)
    return bp


# --- ergonomic builder ------------------------------------------------------
class PresetBuilder:
    """Assemble a preset spec, then .build() a BinaryPreset. Positions are
    (row 0-3, column 0-7). Params are keyed by index -> value or per-scene list."""

    def __init__(self, name="", tempo=0, default_scene=0, rows=4):
        self.spec = {"name": name, "tempo": tempo, "default_scene": default_scene,
                     "chains": [{"row": r, "models": []} for r in range(rows)],
                     "bypass": []}

    def _chain(self, row):
        return self.spec["chains"][row]

    def _param_values(self, value):
        # value: scalar (all scenes) or list of up to 8 per-scene values
        vals = value if isinstance(value, (list, tuple)) else [value] * SCENES
        vals = list(vals)[:SCENES] + [0.0] * (SCENES - len(vals))
        return [[float(v), None, None] for v in vals]

    def add_block(self, row, column, model_hash, params=None):
        m = {"hash": model_hash, "column": column, "params": []}
        for idx, val in (params or {}).items():
            m["params"].append({"index": idx, "scene_mode": isinstance(val, (list, tuple)),
                                 "values": self._param_values(val)})
        self._chain(row)["models"].append(m)
        return self

    def set_routing(self, row, in_portid=None, out_portid=None):
        ch = self._chain(row)
        if in_portid is not None:
            ch["in_portid"] = in_portid
        if out_portid is not None:
            ch["out_portid"] = out_portid
        return self

    def add_splitter(self, row, model_hash=10004, split_col=-1, mix_col=None, params=None):
        ch = self._chain(row)
        ch.setdefault("splitter", []).append(
            _mk_block(model_hash, 0, params))
        ch.setdefault("split_points", []).append(
            [split_col, mix_col if mix_col is not None else -1])
        return self

    def add_mixer(self, row, model_hash=11000, params=None):
        self._chain(row).setdefault("mixer", []).append(_mk_block(model_hash, 0, params))
        return self

    def set_bypass(self, row, column, scene_bypass, scene_mode=True):
        # scene_bypass: bool (all scenes) or list of 8 bools
        arr = scene_bypass if isinstance(scene_bypass, (list, tuple)) else [scene_bypass] * SCENES
        for b in self.spec["bypass"]:
            if b["row"] == row:
                b["cols"].append({"column": column, "scene_mode": scene_mode,
                                  "scene_bypass": list(arr)})
                return self
        self.spec["bypass"].append({"row": row, "cols": [
            {"column": column, "scene_mode": scene_mode, "scene_bypass": list(arr)}]})
        return self

    def scenes(self, labels=None, colors=None, default=None):
        if labels is not None:
            self.spec["scene_labels"] = labels
        if colors is not None:
            self.spec["scene_colors"] = colors
        if default is not None:
            self.spec["default_scene"] = default
        return self

    def build(self):
        return build(self.spec)


def _mk_block(model_hash, column, params):
    m = {"hash": model_hash, "column": column, "params": []}
    for idx, val in (params or {}).items():
        vals = val if isinstance(val, (list, tuple)) else [val] * SCENES
        m["params"].append({"index": idx,
                            "values": [[float(v), None, None] for v in vals]})
    return m
