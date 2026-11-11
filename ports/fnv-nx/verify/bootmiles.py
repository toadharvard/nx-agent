#!/usr/bin/env python3
"""Boot milestones on exe addresses and exact symbols: table -> watch points, and progress as events arrive.

Pure: no emulator, clock or environment; stdlib only. The emulator driver installs the watch points of resolve() and
reports each hit to Progress.event(part_id, tid).

Table (text, one part per line; '#' lines and blank lines allowed; anything else that does not parse is a TableError
carrying the file line number):  <name> <group> <kind> <target> [main-thread]
  group  any        this line alone reaches the milestone
         all:<id>   every part of the id (within the name), in any order
         seq:<id>   the parts of the id in file order; a part counts only after the parts above it have counted
  kind   call 0xEXE     guest pc reaches the entry of the exe address (through the entry manifest)
         ret/retzero/rettrue 0xEXE   the function entered at the exe address returns (any / w0 == 0 / low byte of w0 != 0);
                                     the driver reports the return and decides the value kind from the part's kind
         sym NAME       pc reaches the exact ELF symbol NAME (native platform/render entries)
         store 0xADDR   a non-zero 32-bit word is stored at that guest data address (not biased: a data address)
         host NAME      a named host-side event string reported by the driver
Milestone order is the order of first appearance of the names. Part ids are "<name>#<k>", k counting the name's lines.
A duplicate identical part, a group id used with both all and seq under one name, or an empty table is a TableError.

Entry manifest: `addr_hex<TAB>symbol[<TAB>kind]` per row, kind `code` (default; an exe code address) or `import` (an
import key, never exe code); a duplicated address or symbol over both kinds, an unknown kind, or any other column count is
a ManifestError. The result is a Manifest: the dict of the code rows, plus .imports (the import rows, a separate map) and
.table_id (from the one optional `# table_id <64 hex>` comment line; None when absent). call/ret/sym targets resolve in the
code map first, then in the import map, so a milestone on an import key is possible.

resolve() turns the table into {"pc", "ret", "store", "host"}: name -> part ids in table order (guest addresses are the
ELF symbol value + bias; call and sym parts go to "pc", the three return kinds to "ret"). Every exe address missing from
the manifest and every symbol missing from the ELF is collected and reported together in one ResolveError.

Progress rule: a milestone is REACHED only if every earlier milestone (table order) is already reached. A hit earlier is
reported with out_of_order=True, is not reached and is not credited later; only a new complete hit after the prerequisites
counts. A part event is a hit when, after it is applied, its milestone part is complete: `any` always; `all` when every
part of the group has been seen (parts stay seen); `seq` when every part has counted in order (an event of a part whose
predecessors have not counted is ignored). Once a group is complete, every later event of one of its parts is a hit again.
`main-thread` parts ignore events with tid != main_tid. Events are counted from 0 over every call of event(); an unknown
part id is a KeyError. first_event_index is the index of the event that reached the milestone (None until then).
"""
import argparse
import json
import struct
import sys

FORMAT = "fnvnx-bootmiles/1"
KINDS = ("call", "ret", "retzero", "rettrue", "sym", "store", "host")
ADDR_KINDS = ("call", "ret", "retzero", "rettrue", "store")
ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
SYM_CHARS = ID_CHARS | frozenset("$")


class TableError(ValueError):
    pass


class ManifestError(ValueError):
    pass


class ResolveError(ValueError):
    pass


class ElfError(ValueError):
    pass


class Symbols(dict):
    """{name: value} of the unambiguous symbols; .ambiguous {name: sorted values} of the local-only names at several values"""
    def __init__(self, *a):
        super().__init__(*a)
        self.ambiguous = {}


class Table:
    def __init__(self, milestones):
        self.milestones = milestones
        self.parts = {p["id"]: p for m in milestones for p in m["parts"]}


def _word(tok, chars):
    return bool(tok) and all(c in chars for c in tok)


def _parse_line(no, toks):
    if len(toks) not in (4, 5):
        raise TableError(f"line {no}: expected '<name> <group> <kind> <target> [main-thread]', got {len(toks)} fields")
    name, group, kind, target = toks[:4]
    if len(toks) == 5 and toks[4] != "main-thread":
        raise TableError(f"line {no}: fifth field must be 'main-thread', got {toks[4]!r}")
    if not _word(name, ID_CHARS):
        raise TableError(f"line {no}: bad milestone name {name!r}")
    gk, _, gid = group.partition(":")
    if gk not in ("any", "all", "seq") or (gk == "any") != (group == "any") or (gk != "any" and not _word(gid, ID_CHARS)):
        raise TableError(f"line {no}: bad group {group!r} (any | all:<id> | seq:<id>)")
    if kind not in KINDS:
        raise TableError(f"line {no}: unknown kind {kind!r} (one of {', '.join(KINDS)})")
    if kind in ADDR_KINDS:
        try:
            if not target.startswith("0x"):
                raise ValueError
            value = int(target, 16)
        except ValueError:
            raise TableError(f"line {no}: target of {kind} must be 0x<hex>, got {target!r}") from None
    elif _word(target, SYM_CHARS if kind == "sym" else ID_CHARS):
        value = target
    else:
        raise TableError(f"line {no}: bad {kind} target {target!r}")
    return {"name": name, "group": group, "gkind": gk, "gid": gid, "kind": kind, "target": value, "main": len(toks) == 5}


def load_table(text):
    order, parts, seen, gkinds = [], {}, set(), {}
    for no, line in enumerate(text.split("\n"), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        p = _parse_line(no, line.split())
        key = (p["name"], p["group"], p["kind"], p["target"], p["main"])
        if key in seen:
            raise TableError(f"line {no}: duplicate part")
        seen.add(key)
        if p["gkind"] != "any":
            if gkinds.setdefault((p["name"], p["gid"]), p["gkind"]) != p["gkind"]:
                raise TableError(f"line {no}: group id {p['gid']!r} of {p['name']!r} is used with both all and seq")
        if p["name"] not in parts:
            parts[p["name"]] = []
            order.append(p["name"])
        p["id"] = f"{p['name']}#{len(parts[p['name']])}"
        parts[p["name"]].append(p)
    if not order:
        raise TableError("empty table")
    return Table([{"name": n, "parts": parts[n]} for n in order])


class Manifest(dict):
    """the code rows {exe_addr: symbol}; .imports {key: symbol}; .table_id str or None"""
    imports = {}
    table_id = None


def load_manifest(text):
    out, syms = Manifest(), {}
    out.imports, out.table_id = {}, None
    for no, line in enumerate(text.split("\n"), 1):
        if line.startswith("# table_id "):
            tid = line[len("# table_id "):].strip()
            if out.table_id is not None or len(tid) != 64 or any(c not in "0123456789abcdef" for c in tid):
                raise ManifestError(f"manifest line {no}: bad or repeated table_id {tid!r} (one 64-digit lowercase hex)")
            out.table_id = tid
            continue
        if not line.strip() or line.startswith("#"):
            continue
        f = line.rstrip("\r").split("\t")
        if len(f) not in (2, 3) or not f[1] or (len(f) == 3 and f[2] not in ("code", "import")):
            raise ManifestError(f"manifest line {no}: expected '<addr_hex><TAB><symbol>[<TAB>code|import]'")
        target = out.imports if len(f) == 3 and f[2] == "import" else out
        try:
            a = int(f[0], 16)
        except ValueError:
            raise ManifestError(f"manifest line {no}: bad hex address {f[0]!r}") from None
        if a in out or a in out.imports:
            raise ManifestError(f"manifest line {no}: duplicate address 0x{a:x}")
        if f[1] in syms:
            raise ManifestError(f"manifest line {no}: duplicate symbol {f[1]!r} (also 0x{syms[f[1]]:x})")
        target[a], syms[f[1]] = f[1], a
    return out


def _why(symbols, name):
    amb = getattr(symbols, "ambiguous", {}).get(name)
    return "not in the ELF" if amb is None else "is ambiguous in the ELF (local symbols at %s)" % ", ".join("0x%x" % v for v in amb)


def resolve(table, manifest, symbols, bias=0):
    watch = {"pc": {}, "ret": {}, "store": {}, "host": {}}
    missing = []

    def need(item):
        if item not in missing:
            missing.append(item)

    for m in table.milestones:
        for p in m["parts"]:
            k, t = p["kind"], p["target"]
            if k in ("call", "ret", "retzero", "rettrue"):
                imports = getattr(manifest, "imports", {})
                if t not in manifest and t not in imports:
                    need(f"exe address 0x{t:x} not in the manifest")
                    continue
                sym = manifest[t] if t in manifest else imports[t]
                if sym not in symbols:
                    need(f"symbol {sym} (manifest entry of 0x{t:x}) " + _why(symbols, sym))
                    continue
                watch["pc" if k == "call" else "ret"].setdefault(symbols[sym] + bias, []).append(p["id"])
            elif k == "sym":
                if t not in symbols:
                    need(f"symbol {t} " + _why(symbols, t))
                    continue
                watch["pc"].setdefault(symbols[t] + bias, []).append(p["id"])
            else:
                watch[k].setdefault(t, []).append(p["id"])
    if missing:
        raise ResolveError(f"{len(missing)} unresolved: " + "; ".join(missing))
    return watch


class Progress:
    def __init__(self, table):
        self.table = table
        self.n = 0
        self.rows = [{"name": m["name"], "reached": False, "hits": 0, "out_of_order_hits": 0, "first_event_index": None}
                     for m in table.milestones]
        self.row_of = {m["name"]: r for m, r in zip(table.milestones, self.rows)}
        self.seen = set()   # part ids seen (all) or counted (seq)

    def _complete(self, p):
        peers = [q for q in self.table.parts.values() if q["name"] == p["name"] and q["group"] == p["group"]]
        return all(q["id"] in self.seen for q in peers)

    def event(self, part_id, tid, main_tid=1):
        p = self.table.parts[part_id]
        idx, self.n = self.n, self.n + 1
        if p["main"] and tid != main_tid:
            return []
        if p["gkind"] == "all":
            self.seen.add(part_id)
        elif p["gkind"] == "seq":
            peers = [q for q in self.table.parts.values() if q["name"] == p["name"] and q["group"] == p["group"]]
            if all(q["id"] in self.seen for q in peers[:peers.index(p)]):
                self.seen.add(part_id)
        if p["gkind"] != "any" and not self._complete(p):
            return []
        row = self.row_of[p["name"]]
        if all(r["reached"] for r in self.rows[:self.rows.index(row)]):
            row["hits"] += 1
            if not row["reached"]:
                row["reached"], row["first_event_index"] = True, idx
            return [(p["name"], False)]
        row["out_of_order_hits"] += 1
        return [(p["name"], True)]

    def state(self):
        return [dict(r) for r in self.rows]

    def last_reached(self):
        names = [r["name"] for r in self.rows if r["reached"]]
        return names[-1] if names else None


def read_symtab(path):
    """ELF32 little-endian .symtab -> Symbols {name: value} (defined, named symbols). Rule: a GLOBAL/WEAK name at two values is
    an ElfError (the linker forbids it); a name defined only by STB_LOCAL symbols may repeat at several values (compiler clones
    such as `f.isra.0`, one per translation unit): it is AMBIGUOUS, left out of the map and listed in .ambiguous, and resolve()
    reports it only if a table row needs it; a global name wins over local symbols of the same name (as in a link).
    AArch64 mapping symbols ($x, $d, $x.<any>, $d.<any>: AAELF64 "Mapping symbols") mark code/data runs, name nothing
    and repeat by design: they are left out."""
    with open(path, "rb") as f:
        d = f.read()
    if d[:4] != b"\x7fELF" or len(d) < 52 or d[4] != 1 or d[5] != 1:
        raise ElfError(f"{path}: not a little-endian ELF32 file")
    shoff, = struct.unpack_from("<I", d, 32)
    shentsize, shnum = struct.unpack_from("<HH", d, 46)
    secs = [struct.unpack_from("<10I", d, shoff + i * shentsize) for i in range(shnum)]
    tabs = [s for s in secs if s[1] == 2]
    if len(tabs) != 1:
        raise ElfError(f"{path}: expected exactly one .symtab section, found {len(tabs)}")
    _, _, _, _, off, size, link, _, _, ent = tabs[0]
    stroff, strsize = secs[link][4], secs[link][5]
    out, loc = {}, {}
    for o in range(off, off + size, ent or 16):
        nm, val, _, info, _, shndx = struct.unpack_from("<IIIBBH", d, o)
        if nm == 0 or shndx == 0:
            continue
        e = d.index(b"\0", stroff + nm, stroff + strsize)
        name = d[stroff + nm:e].decode("ascii")
        if name in ("$x", "$d") or name[:3] in ("$x.", "$d."):
            continue
        if info >> 4 == 0:
            loc.setdefault(name, set()).add(val)
        elif out.setdefault(name, val) != val:
            raise ElfError(f"{path}: symbol {name} defined at two values (0x{out[name]:x}, 0x{val:x})")
    res = Symbols(out)
    for name, vals in loc.items():
        if name not in out:
            if len(vals) == 1:
                res[name] = next(iter(vals))
            else:
                res.ambiguous[name] = sorted(vals)
    return res


def main(argv):
    ap = argparse.ArgumentParser(description="boot milestones: table + entry manifest + ELF symbols -> watch points")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("resolve", help="resolve the milestone table to guest watch points (WATCH.json, fnvnx-bootmiles/1)")
    sp.add_argument("--table", required=True, help="milestone table text file")
    sp.add_argument("--manifest", required=True, help="entry manifest TSV: exe_addr_hex<TAB>symbol")
    sp.add_argument("--elf", required=True, help="guest ELF32 with a .symtab")
    sp.add_argument("--bias", type=lambda s: int(s, 0), default=0, help="added to every symbol address (default 0)")
    sp.add_argument("-o", required=True, help="output WATCH.json")
    a = ap.parse_args(argv)
    try:
        with open(a.table) as f:
            table = load_table(f.read())
        with open(a.manifest) as f:
            manifest = load_manifest(f.read())
        w = resolve(table, manifest, read_symtab(a.elf), a.bias)
        doc = {"format": FORMAT, "bias": a.bias, "manifest_id": manifest.table_id,
               "milestones": [{"name": m["name"], "parts": [{k: (f"0x{p[k]:x}" if k == "target" and isinstance(p[k], int) else p[k])
                                                              for k in ("id", "group", "kind", "target", "main")}
                                                             for p in m["parts"]]} for m in table.milestones]}
        for k in ("pc", "ret", "store"):
            doc[k] = {f"0x{x:x}": v for x, v in w[k].items()}
        doc["host"] = w["host"]
        with open(a.o, "w") as f:
            f.write(json.dumps(doc, sort_keys=True, indent=1) + "\n")
    except (ValueError, OSError) as e:
        print(f"bootmiles resolve: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
