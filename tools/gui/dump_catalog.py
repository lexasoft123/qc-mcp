#!/usr/bin/env python3
"""Build a structured DIRECTORY catalog snapshot (presets / IRs / captures) from the
interposer log's captured File messages, and write it to JSON. Non-destructive — reads
already-captured traffic, sends nothing.

    python3 tools/gui/dump_catalog.py [out.json]
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
from mine_log import messages  # noqa: E402
import qc_mcp.protocol as P  # noqa: E402
from qc_mcp import directory  # noqa: E402


def main(argv):
    out = argv[0] if argv else os.path.join(os.path.dirname(__file__), "..", "..",
                                            "interceptor", "catalog.json")
    file_msgs = []
    fav = {"recents": [], "favorites": []}
    for ts, d, cid, name, proto, gz in messages():
        if name == "File" and d == "IN":
            try:
                m = P.message_class("File")()
                m.ParseFromString(proto)
                if m.type in directory.FILE_TYPE and m.HasField("folder"):
                    file_msgs.append(m)
            except Exception:
                pass
        elif name == "RecentsFavorites" and d == "IN":
            m = P.message_class("RecentsFavorites")()
            m.ParseFromString(proto)
            bucket = "favorites" if getattr(m, "is_favorites", False) else "recents"
            fav[bucket] = [{"name": it.name, "folder_key": it.folder_key,
                            "folder_name": it.folder_name,
                            "is_factory": getattr(it, "is_factory", False),
                            "is_plugin": getattr(it, "is_plugin", False)}
                           for it in m.items]
    catalog = directory.structure_directory(file_msgs)
    catalog["_favorites"] = fav
    catalog["_counts"] = directory.counts(catalog)
    with open(out, "w") as fh:
        json.dump(catalog, fh, indent=1)
    print(f"wrote {out}")
    print("counts:", json.dumps(catalog["_counts"]))
    print("recents:", len(fav["recents"]), "favorites:", len(fav["favorites"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
