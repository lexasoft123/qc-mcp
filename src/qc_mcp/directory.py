"""Structure and search the Quad Cortex on-device DIRECTORY.

The device answers a `File` READ by streaming one `File{action:UPDATE}` message per
folder (presets, IRs, and neural captures live on the device filesystem). Each carries
a `folder{key,name,is_factory,is_downloads,is_user_default, files[...]}`. The
`File.type` field segments the three catalogs:

    0 = Presets     (My Presets, Factory Library, cloud downloads, plugin banks)
    1 = IRs         (impulse responses; file keys prefixed "CIR_")
    2 = Captures    (neural captures; file keys are 64-hex content hashes)

Folder keys: user folders use short ids ("1_q", "2_q", ...) or a path under
`/media/p4/Presets/...`; factory folders live under `/opt/neuraldsp/...`; cloud/download
folders are `cloud-<type>-<n>`. To LOAD a preset, recall its (folder_key, position,
is_factory) via SetlistPosition — see transport.recall(). Captures/IRs are used by
referencing their file key (hash) as a block model in the grid.
"""

FILE_TYPE = {0: "presets", 1: "irs", 2: "captures"}


def _file_dict(f):
    return {
        "index": f.index,
        "name": f.name,
        "key": f.key,
        "cloud_id": getattr(f, "cloud_id", "") or "",
        "author": getattr(f, "author", "") or "",
        "version": getattr(f, "coros_version", "") or "",
        "instrument": getattr(f, "instrument", 0),
        "is_readonly": getattr(f, "is_readonly", False),
        "date_ms": getattr(f, "date_ms_since_epoch", 0),
    }


def structure_directory(file_msgs):
    """file_msgs: iterable of decoded File protobuf messages (action=UPDATE, with a
    folder). Returns {'presets':[folder...], 'irs':[...], 'captures':[...]} where each
    folder is {key,name,is_factory,is_downloads,is_user_default,files:[...]}. Later
    messages for the same folder key replace earlier ones (the stream is authoritative).
    """
    cats = {"presets": {}, "irs": {}, "captures": {}}
    for m in file_msgs:
        if not getattr(m, "type", None) in FILE_TYPE:
            continue
        if not m.HasField("folder"):
            continue
        fo = m.folder
        cats[FILE_TYPE[m.type]][fo.key] = {
            "key": fo.key,
            "name": fo.name,
            "is_factory": fo.is_factory,
            "is_downloads": fo.is_downloads,
            "is_user_default": getattr(fo, "is_user_default", False),
            "files": [_file_dict(f) for f in fo.files],
        }
    # deterministic order: user folders first, then by name
    out = {}
    for cat, folders in cats.items():
        out[cat] = sorted(folders.values(),
                          key=lambda d: (d["is_factory"], d["name"].lower()))
    return out


def iter_files(catalog, category):
    """Yield (folder, file) for every file in a category ('presets'|'irs'|'captures')."""
    for folder in catalog.get(category, []):
        for f in folder["files"]:
            yield folder, f


def search(catalog, query, category=None, limit=50):
    """Case-insensitive substring search over file names (and folder names). Returns
    a flat list of {category, folder_key, folder_name, is_factory, ...file fields}."""
    q = (query or "").lower()
    cats = [category] if category else list(FILE_TYPE.values())
    hits = []
    for cat in cats:
        for folder, f in iter_files(catalog, cat):
            if not q or q in f["name"].lower() or q in folder["name"].lower():
                hits.append({"category": cat, "folder_key": folder["key"],
                             "folder_name": folder["name"],
                             "is_factory": folder["is_factory"], **f})
    hits.sort(key=lambda d: (d["is_factory"], d["name"].lower()))
    return hits[:limit]


def counts(catalog):
    """Summary counts per category: folders and files."""
    out = {}
    for cat in FILE_TYPE.values():
        folders = catalog.get(cat, [])
        out[cat] = {"folders": len(folders),
                    "files": sum(len(fo["files"]) for fo in folders)}
    return out
