#!/usr/bin/env python3
"""Build (and diff) the versioned protobuf descriptor sets the MCP ships.

Cortex Control embeds each .proto's FileDescriptorProto in its binary; the QC's
wire schema changes between CorOS releases, so we keep one descriptor set per
protocol generation in `src/qc_mcp/descriptors/qc_descriptors-<major.minor>.pb`
and pick at runtime from the connected device's firmware (see protocol.py).

    tools/build_descriptors.py build 4.1            # from the installed app
    tools/build_descriptors.py build 4.1 --app /path/to/Other.app
    tools/build_descriptors.py list
    tools/build_descriptors.py diff 4.0 4.1         # what changed on the wire
"""
import argparse
import os
import sys

from google.protobuf import descriptor_pb2

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DESCRIPTORS = os.path.join(ROOT, "src", "qc_mcp", "descriptors")
DEFAULT_APP = "/Applications/Neural DSP/Cortex Control.app"
KEEP = ("Preset.proto", "ProductionAutomation.proto")

sys.path.insert(0, HERE)
import extract_protos  # noqa: E402


def _path(version):
    return os.path.join(DESCRIPTORS, f"qc_descriptors-{version}.pb")


def _load(version):
    fds = descriptor_pb2.FileDescriptorSet()
    with open(_path(version), "rb") as fh:
        fds.ParseFromString(fh.read())
    return {f.name: f for f in fds.file}


def build(version, app):
    binary = os.path.join(app, "Contents", "MacOS", "Cortex Control")
    if not os.path.exists(binary):
        sys.exit(f"error: no Cortex Control binary at {binary}")
    app_version = _app_version(app)
    if app_version and not app_version.startswith(version):
        print(f"warning: {app} is {app_version}, writing descriptors-{version}",
              file=sys.stderr)
    fdps = extract_protos.recover(binary)
    fds = descriptor_pb2.FileDescriptorSet()
    for name in KEEP:
        if name not in fdps:
            sys.exit(f"error: {name} not recovered from the binary")
        fds.file.add().CopyFrom(fdps[name])
    os.makedirs(DESCRIPTORS, exist_ok=True)
    with open(_path(version), "wb") as fh:
        fh.write(fds.SerializeToString())
    print(f"wrote {_path(version)} "
          f"({sum(len(f.message_type) for f in fds.file)} messages "
          f"from Cortex Control {app_version or '?'})")


def _app_version(app):
    plist = os.path.join(app, "Contents", "Info.plist")
    try:
        import plistlib
        with open(plist, "rb") as fh:
            return plistlib.load(fh).get("CFBundleShortVersionString")
    except Exception:
        return None


def list_():
    if not os.path.isdir(DESCRIPTORS):
        sys.exit(f"no descriptors dir at {DESCRIPTORS}")
    for fn in sorted(os.listdir(DESCRIPTORS)):
        if not fn.endswith(".pb"):
            continue
        version = fn[len("qc_descriptors-"):-len(".pb")]
        files = _load(version)
        msgs = sum(len(f.message_type) for f in files.values())
        print(f"  {version}: {msgs} messages, {len(files)} file(s)")


def _flatten(fdp):
    """Fully-qualified message name -> {field number: (name, type, type_name)}
    plus '<msg>|enums' -> {enum name: ['NAME=n', ...]}, for nested types too."""
    out = {}

    def walk(messages, prefix):
        for m in messages:
            full = prefix + m.name
            out[full] = {f.number: (f.name, f.type, f.type_name) for f in m.field}
            out[full + "|enums"] = {
                e.name: [f"{v.name}={v.number}" for v in e.value]
                for e in m.enum_type}
            walk(m.nested_type, full + ".")

    walk(fdp.message_type, "")
    return out


def diff(old_version, new_version):
    old_files, new_files = _load(old_version), _load(new_version)
    for fname in KEEP:
        if fname not in old_files or fname not in new_files:
            continue
        old, new = _flatten(old_files[fname]), _flatten(new_files[fname])
        print(f"\n=== {fname}: {old_version} -> {new_version} ===")
        added = sorted(k for k in new if k not in old and not k.endswith("|enums"))
        gone = sorted(k for k in old if k not in new and not k.endswith("|enums"))
        for k in added:
            print(f"  + message {k}")
        for k in gone:
            print(f"  - message {k}  REMOVED")
        for key in sorted(set(old) & set(new)):
            if key.endswith("|enums"):
                msg = key[:-len("|enums")]
                for enum in sorted(set(old[key]) | set(new[key])):
                    before, after = old[key].get(enum), new[key].get(enum)
                    if before == after:
                        continue
                    if before is None:
                        print(f"  + enum {msg}.{enum} {after}")
                    elif after is None:
                        print(f"  - enum {msg}.{enum}  REMOVED")
                    else:
                        for v in sorted(set(after) - set(before)):
                            print(f"  + enum {msg}.{enum}: {v}")
                        for v in sorted(set(before) - set(after)):
                            print(f"  - enum {msg}.{enum}: {v}")
                continue
            for num in sorted(set(old[key]) | set(new[key])):
                before, after = old[key].get(num), new[key].get(num)
                if before == after:
                    continue
                if before is None:
                    print(f"  + {key}.{after[0]} = {num}")
                elif after is None:
                    print(f"  - {key}.{before[0]} = {num}  REMOVED")
                else:
                    print(f"  ! {key} #{num}: {before[0]} -> {after[0]}  "
                          f"(INCOMPATIBLE — same field number, different meaning)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="extract descriptors from a Cortex Control app")
    b.add_argument("version", help="protocol generation, e.g. 4.1")
    b.add_argument("--app", default=DEFAULT_APP)
    sub.add_parser("list", help="list bundled descriptor sets")
    d = sub.add_parser("diff", help="structural diff between two generations")
    d.add_argument("old")
    d.add_argument("new")
    args = ap.parse_args()
    if args.cmd == "build":
        build(args.version, args.app)
    elif args.cmd == "list":
        list_()
    else:
        diff(args.old, args.new)


if __name__ == "__main__":
    main()
