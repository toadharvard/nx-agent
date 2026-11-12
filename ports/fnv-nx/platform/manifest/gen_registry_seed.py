#!/usr/bin/env python3
"""The registry seed of the platform, from the static read sites and the declared machine (L5).

  gen_registry_seed.py <regsites.tsv> <declared_registry.tsv> <out.inc> [--uncovered <report.tsv>]

regsites.tsv is the output of the registry-site pass (platform/manifest/regsites.py): the call sites of the registry APIs in the exe
and its modules. declared_registry.tsv is the declared machine's table (public). The include holds the declared rows only (no
exe-derived data), so it is the same whatever regsites file validated it; platform/win32/registry_seed.cpp applies it when the hive
file does not exist.

Build-time rule: every declared row (root, key, value name) must be read by at least one static read site of regsites.tsv
(RegQueryValue*, RegGetValue*, SHGetValue*; value name equal, key and root equal, case-insensitive as the registry compares). A
declared row that nothing reads, an unreadable or missing input, a malformed row, a duplicate, a value not valid for its type, or a
`{` that is not `{game_dir}` stops with exit status 2 and writes no output. --uncovered writes the static read sites with no
declared row (the keys a real Windows or Wine populates; they stay absent here): module, site, api, root, key, value.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import regsites

ROOTS = {"HKEY_CLASSES_ROOT": 0x80000000, "HKEY_CURRENT_USER": 0x80000001, "HKEY_LOCAL_MACHINE": 0x80000002, "HKEY_USERS": 0x80000003}
TYPES = {"REG_SZ": 1, "REG_EXPAND_SZ": 2, "REG_BINARY": 3, "REG_DWORD": 4, "REG_MULTI_SZ": 7, "REG_QWORD": 11}
TOKEN = "{game_dir}"
MARK = "\\001"  # in the include, the byte 0x01 stands for the game directory; declared text cannot hold control characters


def die(msg):
    sys.stderr.write("gen_registry_seed: " + msg + "\n")
    sys.exit(2)


def table(path, header, what):
    try:
        lines = open(path, encoding="utf-8").read().split("\n")
    except OSError as e:
        die("cannot read %s %s: %s" % (what, path, e.strerror))
    rows, head = [], False
    for n, l in enumerate(lines, 1):
        if not l or l.startswith("#"):
            continue
        f = l.split("\t")
        if not head:
            if f != header:
                die("%s:%d: header must be %s" % (path, n, "\\t".join(header)))
            head = True
            continue
        if len(f) != len(header) or any(x == "" for x in f[:1] + f[1:2]):
            die("%s:%d: expected %d tab-separated fields (root and key not empty), got %d" % (path, n, len(header), len(f)))
        rows.append((n, f))
    if not head:
        die("%s: no header line" % path)
    return rows


def encode(n, typ, data):
    """(C literal body, byte size or None for text). Text keeps {game_dir} as the 0x01 mark."""
    if typ in ("REG_SZ", "REG_EXPAND_SZ"):
        out, i = [], 0
        while i < len(data):
            if data.startswith(TOKEN, i):
                out.append(MARK)
                i += len(TOKEN)
                continue
            c = data[i]
            if c == "{" or not (32 <= ord(c) < 127):
                die("declared row %d: text must be printable ASCII and `{` only as %s" % (n, TOKEN))
            out.append("\\%03o" % ord(c) if c in '"\\?' else c)
            i += 1
        return "".join(out), None
    if typ in ("REG_DWORD", "REG_QWORD"):
        size = 4 if typ == "REG_DWORD" else 8
        try:
            v = int(data, 0)
        except ValueError:
            die("declared row %d: %r is not a number" % (n, data))
        if not 0 <= v < 1 << (8 * size) or data.strip() != data:
            die("declared row %d: %r does not fit %s" % (n, data, typ))
        b = v.to_bytes(size, "little")
    else:
        try:
            b = bytes.fromhex(data)
        except ValueError:
            die("declared row %d: %r is not hex pairs" % (n, data))
        if typ == "REG_MULTI_SZ" and b[-1:] != b"\0":
            die("declared row %d: REG_MULTI_SZ data must end with a terminator" % n)
    return "".join("\\%03o" % c for c in b), len(b)


def main(argv):
    unc = None
    if "--uncovered" in argv:
        k = argv.index("--uncovered")
        if k + 1 >= len(argv):
            die("--uncovered needs a file")
        unc = argv[k + 1]
        argv = argv[:k] + argv[k + 2:]
    if len(argv) != 3:
        die("usage: gen_registry_seed.py <regsites.tsv> <declared_registry.tsv> <out.inc> [--uncovered <report.tsv>]")
    sites_path, decl_path, out = argv
    sites = [f for _, f in table(sites_path, "module site api root subkey value status reason".split(), "regsites")]
    reads = {}
    for f in sites:
        role = regsites.ROLES.get(f[2])
        if role is None:
            die("%s: api %s has no registry role" % (sites_path, f[2]))
        if role[5] == "read" and f[6] == "static" and f[7] != regsites.NO_VALUES:
            reads.setdefault((f[3].lower(), f[4].lower(), f[5].lower()), []).append(f)
    decl, seen, body = table(decl_path, "root key name type data source".split(), "declared table"), set(), []
    for n, (root, key, name, typ, data, source) in decl:
        if root not in ROOTS:
            die("declared row %d: unknown root %s" % (n, root))
        if typ not in TYPES:
            die("declared row %d: unknown type %s" % (n, typ))
        if not source.strip():
            die("declared row %d: the source of the value is missing" % n)
        if key.startswith("\\") or key.endswith("\\"):
            die("declared row %d: bad key path %r" % (n, key))
        k = (root.lower(), key.lower(), name.lower())
        if k in seen:
            die("declared row %d: duplicate of an earlier row" % n)
        seen.add(k)
        if k not in reads:
            die("declared row %d (%s\\%s : %s): no static read site of the exe or a module reads it" % (n, root, key, name or "(default)"))
        lit, size = encode(n, typ, data)
        body.append('    { 0x%08xu, "%s", "%s", %du, "%s", %s },\n' % (ROOTS[root], key.replace("\\", "\\\\"), name, TYPES[typ], lit,
                                                                  "0" if size is None else str(size)))
    if unc:
        with open(unc, "w") as f:
            f.write("module\tsite\tapi\troot\tkey\tvalue\n")
            for k, rs in sorted(reads.items()):
                if k not in seen:
                    for r in rs:
                        f.write("\t".join((r[0], r[1], r[2], r[3], r[4], r[5])) + "\n")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "w") as f:
        f.write("/* generated by platform/manifest/gen_registry_seed.py from declared_registry.tsv: do not edit. Declared rows only.\n"
                " * { root, key, value name, type, data, size }: size 0 = text (0x01 stands for the game directory, a NUL ends it) */\n")
        f.writelines(body)
        f.write("    { 0, 0, 0, 0, 0, 0 }\n")
    os.replace(tmp, out)


if __name__ == "__main__":
    main(sys.argv[1:])
