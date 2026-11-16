"""Gate `registry` (class G of verify/VERDICT.md, the declared machine's registry), pure rules: the product's hive holds exactly the keys that
the exe and the modules read, found statically from their call sites, with the values of the declared machine.

Values (no file, no process): `sites` text of regsites.tsv (module, site, api, root, subkey, value, status static|dynamic, reason: the registry-API
call sites of the exe and its modules, exe-derived and private), `declared` text of declared_registry.tsv (root, key, name, type, data, source:
public), `seed` text of the include the product is built from, `refs` {unit: set of referenced symbol names} of the product objects.
Items of the universe and their certification:
  declared:<root>\\<key>:<name>   every declared row; certified iff a static read site (role read, status static) reads it (root, key and
                                  name equal, case-insensitive as the registry compares)
  site:<module>:<site>:<api>      every site of role open, create or read whose root, key or value is not statically resolved; never certified
                                  (class dynamic). While one exists the verdict is a MEASUREMENT build, as for a dynamic-name GetProcAddress
                                  site in the reach gate (VERDICT.md): a declared row cannot be proven complete against an unresolved site
  seed                            certified iff `seed` equals, byte for byte, the include generate() makes from (sites, declared)
  writer:<unit>                   every product object that references a hive-writing API (role create or write); certified iff it is the
                                  declared seed unit: no other code of the product makes a hive key
generate() is the port of manifest/gen_registry_seed.py (L5): it refuses a declared row that no static read site reads, a malformed or
duplicate row, a value not valid for its type, and a `{` that is not `{game_dir}`. Assumptions (ASM-051): regsites.tsv is complete over the exe and the
modules (the registry-site pass reports what it cannot bind as dynamic, never skips); the seed unit and the registry implementation are the only
code that reaches the hive, and the implementation creates no key by itself (the writers rule closes the entry points, not the internals)."""

ROOTS = {"HKEY_CLASSES_ROOT": 0x80000000, "HKEY_CURRENT_USER": 0x80000001, "HKEY_LOCAL_MACHINE": 0x80000002, "HKEY_USERS": 0x80000003}
TYPES = {"REG_SZ": 1, "REG_EXPAND_SZ": 2, "REG_BINARY": 3, "REG_DWORD": 4, "REG_MULTI_SZ": 7, "REG_QWORD": 11}
TOKEN = "{game_dir}"
MARK = "\\001"
SITES_HEAD = "module site api root subkey value status reason".split()
DECL_HEAD = "root key name type data source".split()
_BASE = {"RegOpenKey": "open", "RegOpenKeyEx": "open", "RegCreateKey": "create", "RegCreateKeyEx": "create", "RegQueryValue": "read",
         "RegQueryValueEx": "read", "RegGetValue": "read", "RegSetValue": "write", "RegSetValueEx": "write", "RegEnumKey": "enum",
         "RegEnumKeyEx": "enum", "RegEnumValue": "enum", "RegDeleteKey": "delete", "RegDeleteKeyEx": "delete", "RegDeleteValue": "delete",
         "RegQueryInfoKey": "info", "SHGetValue": "read", "SHSetValue": "write", "SHDeleteKey": "delete", "SHDeleteValue": "delete"}
ROLES = {"RegCloseKey": "close"}
for _n in sorted(_BASE):
    ROLES[_n + "A"] = ROLES[_n + "W"] = _BASE[_n]
WRITERS = tuple(sorted(n for n, k in ROLES.items() if k in ("create", "write")))


def table(text, header, what):
    """[(line number, fields)] of a TSV with the exact header; comment lines start with #."""
    rows, head = [], False
    for n, line in enumerate(text.split("\n"), 1):
        if not line or line.startswith("#"):
            continue
        f = line.split("\t")
        if not head:
            if f != header:
                raise ValueError("%s:%d: header must be %s" % (what, n, "\\t".join(header)))
            head = True
            continue
        if len(f) != len(header) or f[0] == "" or f[1] == "":
            raise ValueError("%s:%d: expected %d tab-separated fields (root and key not empty), got %d" % (what, n, len(header), len(f)))
        rows.append((n, f))
    if not head:
        raise ValueError("%s: no header line" % what)
    return rows


def read_sites(text):
    """-> [fields] of regsites.tsv; an api without a registry role is an error"""
    out = [f for _, f in table(text, SITES_HEAD, "regsites")]
    for f in out:
        if f[2] not in ROLES:
            raise ValueError("regsites: api %s has no registry role" % f[2])
        if f[6] not in ("static", "dynamic"):
            raise ValueError("regsites: status %r of %s is not static or dynamic" % (f[6], f[1]))
    return out


def read_keys(sites):
    """{(root, key, value) lowercased: [site rows]} of the static read sites"""
    reads = {}
    for f in sites:
        if ROLES[f[2]] == "read" and f[6] == "static":
            reads.setdefault((f[3].lower(), f[4].lower(), f[5].lower()), []).append(f)
    return reads


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
                raise ValueError("declared row %d: text must be printable ASCII and `{` only as %s" % (n, TOKEN))
            out.append("\\%03o" % ord(c) if c in '"\\?' else c)
            i += 1
        return "".join(out), None
    if typ in ("REG_DWORD", "REG_QWORD"):
        size = 4 if typ == "REG_DWORD" else 8
        try:
            v = int(data, 0)
        except ValueError:
            raise ValueError("declared row %d: %r is not a number" % (n, data))
        if not 0 <= v < 1 << (8 * size) or data.strip() != data:
            raise ValueError("declared row %d: %r does not fit %s" % (n, data, typ))
        b = v.to_bytes(size, "little")
    else:
        try:
            b = bytes.fromhex(data)
        except ValueError:
            raise ValueError("declared row %d: %r is not hex pairs" % (n, data))
        if typ == "REG_MULTI_SZ" and b[-1:] != b"\0":
            raise ValueError("declared row %d: REG_MULTI_SZ data must end with a terminator" % n)
    return "".join("\\%03o" % c for c in b), len(b)


def generate(sites_text, declared_text):
    """-> include text (declared rows only). Raises ValueError exactly where gen_registry_seed.py exits with status 2."""
    reads = read_keys(read_sites(sites_text))
    seen, body = set(), []
    for n, (root, key, name, typ, data, source) in table(declared_text, DECL_HEAD, "declared table"):
        if root not in ROOTS:
            raise ValueError("declared row %d: unknown root %s" % (n, root))
        if typ not in TYPES:
            raise ValueError("declared row %d: unknown type %s" % (n, typ))
        if not source.strip():
            raise ValueError("declared row %d: the source of the value is missing" % n)
        if key.startswith("\\") or key.endswith("\\"):
            raise ValueError("declared row %d: bad key path %r" % (n, key))
        k = (root.lower(), key.lower(), name.lower())
        if k in seen:
            raise ValueError("declared row %d: duplicate of an earlier row" % n)
        seen.add(k)
        if k not in reads:
            raise ValueError("declared row %d (%s\\%s : %s): no static read site of the exe or a module reads it" % (n, root, key, name or "(default)"))
        lit, size = encode(n, typ, data)
        body.append('    { 0x%08xu, "%s", "%s", %du, "%s", %s },\n' % (ROOTS[root], key.replace("\\", "\\\\"), name, TYPES[typ], lit,
                                                                  "0" if size is None else str(size)))
    return ("/* generated by manifest/gen_registry_seed.py from declared_registry.tsv: do not edit. Declared rows only.\n"
            " * { root, key, value name, type, data, size }: size 0 = text (0x01 stands for the game directory, a NUL ends it) */\n"
            + "".join(body) + "    { 0, 0, 0, 0, 0, 0 }\n")


def judge(sites_text, declared_text, seed_text, refs, seed_unit):
    """-> [(item id, class or None)] of the universe in a fixed order; raises ValueError on a malformed input"""
    sites = read_sites(sites_text)
    reads = read_keys(sites)
    out = []
    for n, f in table(declared_text, DECL_HEAD, "declared table"):
        out.append(("declared:%s\\%s:%s" % (f[0], f[1], f[2]), None if (f[0].lower(), f[1].lower(), f[2].lower()) in reads else "no-read-site"))
    for f in sites:
        if ROLES[f[2]] in ("open", "create", "read") and f[6] == "dynamic":
            out.append(("site:%s:%s:%s" % (f[0], f[1], f[2]), "dynamic"))
    try:
        cls = None if seed_text == generate(sites_text, declared_text) else "seed-differs"
    except ValueError as e:
        cls = "generator: " + str(e)[:160]
    out.append(("seed", cls))
    for unit in sorted(refs):
        if any(w in refs[unit] for w in WRITERS):
            out.append(("writer:%s" % unit, None if unit == seed_unit else "foreign-writer"))
    return out
