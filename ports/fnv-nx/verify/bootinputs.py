#!/usr/bin/env python3
"""Converters from the L1 contract branch files (l1-contract/elf/<id>) to what boot.py takes. Pure, stdlib only. Every
row or line a converter does not understand is an InputError carrying the file line number: nothing is skipped.

  join       --dir D -o guest.elf     manifest.json (fnvnx-elf/1: name, size, sha256, parts [[file, size, sha256]]) -> the parts
                                      concatenated in manifest order; each part's size and sha256 and the whole size and sha256 checked
  reserved   --modules modules.tsv --exe FalloutNV.exe -o reserved.tsv
                                      modules.tsv (header `# name sha256 size preferred_base assigned_base size_of_image has_tls
                                      entry_rva static load_index`; one row per module) -> `name lo hi` rows (lo = assigned_base,
                                      hi = lo + size_of_image) plus the row `exe` = the exe's own PE ImageBase .. + SizeOfImage,
                                      sorted by lo
  keys       --entries entries.tsv --keys keys.tsv
                                      prints LO:HI = the import-key range: lowest import key of entries.tsv .. highest + 0x10 (every
                                      key 16-byte aligned); every key of keys.tsv (`key module name ordinal conv ret args symbol`)
                                      must be an import key of entries.tsv and the two table ids must be equal
  milestones --table milestones.tsv --run run.txt [--entries entries.tsv] [--stage-table FILE] -o table.txt
                                      milestones.tsv + the loader's console lines of the contract's run.txt (every line that is exactly
                                      three spaces + `fnvnx-start: ...`, in file order, each distinct) -> a bootmiles table.
                                      Accepted grammar of milestones.tsv (TAB separated; `# table_id H` once, which must equal entries.tsv's;
                                      any other comment and every other row is an error naming its line):
                                        exe_entry_va  <TAB> 0xADDR                    exactly once  -> milestone exe-entry (call)
                                        module_entry  <TAB> NAME <TAB> 0xADDR <TAB> S  S = 1|0 (static|not). S=1 -> milestone
                                                                                       module-entry-NAME (call); S=0 -> no milestone.
                                                                                       NAME unique, [A-Za-z0-9_.-]
                                        present_key   <TAB> 0xKEY                     one row per Present import key (must be an import
                                                                                       row of entries.tsv when --entries is given); all
                                                                                       together -> milestone first-present, group any,
                                                                                       one `call` part per key
                                      Milestones come out in this order: start-01.. (console lines), then the rows above in FILE order
                                      (file order = boot order, as the ELF owner states it), then the stage table.
                                      --stage-table FILE: a table in the bootmiles format (verify/bootmiles.py), its milestones appended after
                                      all of those. Errors: a name used by both sources; an address-kind part (call/ret/retzero/rettrue) whose
                                      exe address is not a `code` row of --entries (the stage cannot observe a call of an address without a
                                      lifted entry; --entries is then required); a table that bootmiles rejects.
  stage-table --old milestones.txt -o stage.milestones
                                      the old S5 table (`<name> [all|seq|all:ID|seq:ID] <kind> <target> [main-thread]`, `#` comments, trailing
                                      `# ...` allowed) -> bootmiles format by RULE. kinds: call->call, ret->ret, callok->retzero,
                                      calltrue->rettrue (same semantics: the callee returns to its caller on the same thread and sp; w0==0 /
                                      low byte of w0 != 0). Plain `all`/`seq` get the group id `all`/`seq`. Dropped by class, listed in
                                      `# dropped:` lines: kind data and kind store (the old store targets and data steps are not the
                                      bootmiles host/store events: class `kind-data`, `kind-store`), target re:REGEX (class `regex-target`:
                                      names no exe address). A group (name, group id) that loses any part is dropped WHOLE (class
                                      `group-incomplete`): a group with fewer parts would be a weaker condition. A milestone left without
                                      rows is listed as `# dropped-milestone:`.
"""
import argparse
import hashlib
import json
import os
import struct
import sys

import boot
import bootmiles

MODULE_HEADER = "# name\tsha256\tsize\tpreferred_base\tassigned_base\tsize_of_image\thas_tls\tentry_rva\tstatic\tload_index"
KEYS_STRIDE = 0x10
CONSOLE_PREFIX = "   fnvnx-start: "


class InputError(ValueError):
    pass


def _hex(tok, what, no):
    try:
        if not tok or any(c not in "0123456789abcdefABCDEF" for c in tok):
            raise ValueError
        return int(tok, 16)
    except ValueError:
        raise InputError(f"line {no}: {what} must be hex, got {tok!r}") from None


def _rows(text, header=None, ncols=None):
    """(line number, fields) of the data rows; `# table_id` and header comment lines are checked/skipped, other comments rejected"""
    out, tid, seen_header = [], None, header is None
    for no, line in enumerate(text.split("\n"), 1):
        line = line.rstrip("\r")
        if not line:
            continue
        if line.startswith("# table_id "):
            if tid is not None:
                raise InputError(f"line {no}: repeated table_id")
            tid = line[len("# table_id "):].strip()
            continue
        if line.startswith("#"):
            if header is not None and line == header and not seen_header:
                seen_header = True
                continue
            raise InputError(f"line {no}: unexpected comment {line[:60]!r}")
        f = line.split("\t")
        if ncols is not None and len(f) != ncols:
            raise InputError(f"line {no}: expected {ncols} tab-separated fields, got {len(f)}")
        out.append((no, f))
    if not seen_header:
        raise InputError(f"missing header line {header[:40]!r}...")
    return out, tid


def join_elf(manifest_text, read_part):
    """read_part(name) -> bytes; returns the joined ELF bytes after every check"""
    try:
        m = json.loads(manifest_text)
        if m["schema"] != "fnvnx-elf/1" or sorted(m) != ["name", "parts", "schema", "sha256", "size"]:
            raise ValueError("schema/keys")
        parts = [(n, int(sz), h) for n, sz, h in m["parts"]]
        size, sha = int(m["size"]), m["sha256"]
    except (ValueError, KeyError, TypeError) as e:
        raise InputError(f"manifest.json: not an fnvnx-elf/1 manifest ({e})") from None
    data = bytearray()
    for n, sz, h in parts:
        b = read_part(n)
        if len(b) != sz or hashlib.sha256(b).hexdigest() != h:
            raise InputError(f"part {n}: size {len(b)} / sha256 {hashlib.sha256(b).hexdigest()} differs from the manifest ({sz} / {h})")
        data += b
    if len(data) != size or hashlib.sha256(data).hexdigest() != sha:
        raise InputError(f"joined ELF: size {len(data)} / sha256 {hashlib.sha256(data).hexdigest()} differs from the manifest ({size} / {sha})")
    return bytes(data)


def pe_image(raw):
    """(ImageBase, SizeOfImage) of a PE32 file"""
    try:
        if raw[:2] != b"MZ":
            raise ValueError("no MZ")
        pe, = struct.unpack_from("<I", raw, 0x3C)
        if raw[pe:pe + 4] != b"PE\0\0":
            raise ValueError("no PE signature")
        opt = pe + 24
        if struct.unpack_from("<H", raw, opt)[0] != 0x10B:
            raise ValueError("not PE32")
        return struct.unpack_from("<I", raw, opt + 28)[0], struct.unpack_from("<I", raw, opt + 56)[0]
    except (ValueError, struct.error) as e:
        raise InputError(f"exe: not a PE32 image ({e})") from None


def reserved_rows(modules_text, exe_raw):
    rows, _ = _rows(modules_text, MODULE_HEADER, 10)
    out = []
    for no, f in rows:
        name, sha, _size, _pref, base, soi, tls, _entry, static, _idx = f
        if not name or len(sha) != 64 or tls not in ("0", "1") or static not in ("0", "1"):
            raise InputError(f"line {no}: bad name/sha256/has_tls/static field")
        lo, size = _hex(base, "assigned_base", no), _hex(soi, "size_of_image", no)
        if not size:
            raise InputError(f"line {no}: size_of_image is 0")
        out.append((name, lo, lo + size))
    base, size = pe_image(exe_raw)
    out.append(("exe", base, base + size))
    return sorted(out, key=lambda r: r[1])


def key_range(entries_text, keys_text):
    man = bootmiles.load_manifest(entries_text)
    if man.table_id is None or not man.imports:
        raise InputError("entries.tsv: no table_id trailer or no import rows")
    if any(k % KEYS_STRIDE for k in man.imports):
        raise InputError("entries.tsv: an import key is not 16-byte aligned")
    rows, tid = _rows(keys_text, ncols=8)
    if tid != man.table_id:
        raise InputError(f"keys.tsv table_id {tid} differs from entries.tsv {man.table_id}")
    for no, f in rows:
        k = _hex(f[0], "key", no)
        if k not in man.imports:
            raise InputError(f"line {no}: key 0x{k:x} is not an import row of entries.tsv")
    return min(man.imports), max(man.imports) + KEYS_STRIDE


NAME_CHARS = bootmiles.ID_CHARS
ADDR_PARTS = ("call", "ret", "retzero", "rettrue")
OLD_KINDS = {"call": "call", "ret": "ret", "callok": "retzero", "calltrue": "rettrue"}


def _addr(tok, what, no):
    if not tok.startswith("0x"):
        raise InputError(f"line {no}: {what} must be 0x<hex>, got {tok!r}")
    return _hex(tok[2:], what, no)


def stage_check(stage_text, man, taken):
    """the stage table's text after bootmiles' own parse and the rules: names not in `taken`, address-kind targets code rows of man"""
    try:
        tab = bootmiles.load_table(stage_text)
    except bootmiles.TableError as e:
        raise InputError(f"stage table: {e}") from None
    for m in tab.milestones:
        if m["name"] in taken:
            raise InputError(f"stage table: milestone {m['name']!r} is also built from the contract rows")
        for p in m["parts"]:
            if p["kind"] in ADDR_PARTS and p["target"] not in man:
                raise InputError(f"stage table: milestone {m['name']!r} part {p['kind']} 0x{p['target']:x}: no lifted code entry "
                                 "for this address in entries.tsv (the stage cannot observe it)")
    return [ln.strip() for ln in stage_text.split("\n") if ln.strip() and not ln.strip().startswith("#")]


def milestone_table(milestones_text, run_text, entries_id=None, stage_text=None, man=None):
    rows, tid = _rows(milestones_text)
    if tid is not None and entries_id is not None and tid != entries_id:
        raise InputError(f"milestones.tsv table_id {tid} differs from entries.tsv {entries_id}")
    lines = [line[3:].rstrip("\r") for line in run_text.split("\n") if line.startswith(CONSOLE_PREFIX)]
    if len(set(lines)) != len(lines):
        raise InputError("run.txt: a loader console line occurs twice; its milestone would be ambiguous")
    out = [f"# generated from milestones.tsv and the loader's console lines of run.txt (table_id {tid})"]
    for i, ln in enumerate(lines, 1):
        out.append("start-%02d any host %s" % (i, boot.console_part(ln)))
    names, entry_seen, present = set(), False, []
    present_at = None
    body = []   # (kind, ...) in file order
    for no, f in rows:
        k = f[0]
        if k == "exe_entry_va" and len(f) == 2:
            if entry_seen:
                raise InputError(f"line {no}: exe_entry_va twice")
            entry_seen = True
            body.append(("exe-entry", _addr(f[1], "exe_entry_va", no)))
        elif k == "module_entry" and len(f) == 4:
            if not f[1] or any(c not in NAME_CHARS for c in f[1]):
                raise InputError(f"line {no}: bad module name {f[1]!r}")
            if f[3] not in ("0", "1"):
                raise InputError(f"line {no}: the static flag must be 0 or 1, got {f[3]!r}")
            if f[1] in names:
                raise InputError(f"line {no}: module {f[1]!r} twice")
            names.add(f[1])
            addr = _addr(f[2], "module_entry address", no)
            if f[3] == "1":
                body.append(("module-entry-" + f[1], addr))
        elif k == "present_key" and len(f) == 2:
            key = _addr(f[1], "present_key", no)
            if man is not None and key not in man.imports:
                raise InputError(f"line {no}: present key 0x{key:x} is not an import row of entries.tsv")
            if key in present:
                raise InputError(f"line {no}: present key 0x{key:x} twice")
            present.append(key)
            if present_at is None:
                present_at = len(body)
                body.append(None)
        else:
            raise InputError(f"line {no}: row {k!r} with {len(f)} fields is not understood "
                             "(exe_entry_va/2, module_entry/4, present_key/2)")
    if not entry_seen:
        raise InputError("milestones.tsv: no exe_entry_va row")
    for item in body:
        if item is None:
            out += ["first-present any call 0x%x" % key for key in present]
        else:
            out.append("%s any call 0x%x" % item)
    if stage_text is not None:
        if man is None:
            raise InputError("a stage table needs entries.tsv (--entries)")
        taken = {item[0] for item in body if item} | {"first-present"} | {"start-%02d" % i for i in range(1, len(lines) + 1)}
        out += stage_check(stage_text, man, taken)
    return "\n".join(out) + "\n"


def stage_table(old_text, source="4b1a7bb"):
    """(bootmiles text, dropped lines) from the old S5 table; see the module docstring for the rules"""
    parts, order = [], []   # (no, name, gkey, kind, target, main)
    for no, line in enumerate(old_text.split("\n"), 1):
        line = line.split("#", 1)[0].split()
        if not line:
            continue
        main = line[-1] == "main-thread"
        if main:
            line = line[:-1]
        if len(line) == 3:
            line.insert(1, "any")
        if len(line) != 4:
            raise InputError(f"line {no}: expected '<name> [group] <kind> <target> [main-thread]'")
        name, group, kind, target = line
        gk, _, gid = group.partition(":")
        if gk not in ("any", "all", "seq"):
            raise InputError(f"line {no}: bad group {group!r}")
        parts.append((no, name, "any" if gk == "any" else f"{gk}:{gid or gk}", kind, target, main))
        if name not in order:
            order.append(name)
    reason = {}
    for i, (no, name, g, kind, target, main) in enumerate(parts):
        if kind in ("data", "store"):
            reason[i] = "kind-" + kind
        elif kind not in OLD_KINDS:
            raise InputError(f"line {no}: unknown kind {kind!r}")
        elif target.startswith("re:"):
            reason[i] = "regex-target"
        elif not target.startswith("0x"):
            raise InputError(f"line {no}: target {target!r} is neither 0x<hex> nor re:<regex>")
    for i, (no, name, g, *_r) in enumerate(parts):   # group closure: a group that lost a part loses all
        if g != "any" and i not in reason and any(j in reason for j, q in enumerate(parts) if q[1] == name and q[2] == g):
            reason[i] = "group-incomplete"
    out = [f"# generated by verify/bootinputs.py stage-table from the old S5 milestones table (source commit {source})"]
    kept, dropped, lost = set(), [], []
    for n in order:
        rows = [(i, q) for i, q in enumerate(parts) if q[1] == n]
        for i, (no, name, g, kind, target, main) in rows:
            if i in reason:
                dropped.append(f"# dropped: {name} {g} {kind} {target}{' main-thread' if main else ''} : {reason[i]}")
            else:
                kept.add(n)
                out.append(f"{name} {g} {OLD_KINDS[kind]} {target}" + (" main-thread" if main else ""))
        if n not in kept:
            lost.append(f"# dropped-milestone: {n}")
    return "\n".join(out + dropped + lost) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    j = sub.add_parser("join")
    j.add_argument("--dir", required=True)
    j.add_argument("-o", required=True)
    r = sub.add_parser("reserved")
    r.add_argument("--modules", required=True)
    r.add_argument("--exe", required=True)
    r.add_argument("-o", required=True)
    k = sub.add_parser("keys")
    k.add_argument("--entries", required=True)
    k.add_argument("--keys", required=True)
    m = sub.add_parser("milestones")
    m.add_argument("--table", required=True)
    m.add_argument("--run")
    m.add_argument("--entries", help="entries.tsv whose table_id the milestones.tsv must carry")
    m.add_argument("--stage-table", help="a bootmiles table appended after the contract's milestones (needs --entries)")
    m.add_argument("-o", required=True)
    st = sub.add_parser("stage-table")
    st.add_argument("--old", required=True)
    st.add_argument("--source", default="4b1a7bb")
    st.add_argument("-o", required=True)
    a = ap.parse_args(argv)
    rd = lambda p: open(p, encoding="utf-8").read()
    try:
        if a.cmd == "join":
            data = join_elf(rd(os.path.join(a.dir, "manifest.json")), lambda n: open(os.path.join(a.dir, os.path.basename(n)), "rb").read())
            open(a.o, "wb").write(data)
        elif a.cmd == "reserved":
            rows = reserved_rows(rd(a.modules), open(a.exe, "rb").read())
            open(a.o, "w").write("".join("%s\t%x\t%x\n" % r for r in rows))
        elif a.cmd == "keys":
            lo, hi = key_range(rd(a.entries), rd(a.keys))
            print("%x:%x" % (lo, hi))
        elif a.cmd == "stage-table":
            open(a.o, "w").write(stage_table(rd(a.old), a.source))
        else:
            man = bootmiles.load_manifest(rd(a.entries)) if a.entries else None
            open(a.o, "w").write(milestone_table(rd(a.table), rd(a.run) if a.run else "", man.table_id if man else None,
                                                 rd(a.stage_table) if a.stage_table else None, man))
    except (InputError, bootmiles.ManifestError, OSError) as e:
        src = {"join": "manifest.json", "reserved": "modules.tsv", "keys": "keys.tsv", "milestones": "milestones.tsv", "stage-table": "old table"}[a.cmd]
        print(f"bootinputs {a.cmd} ({src}/entries.tsv/run.txt): {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
