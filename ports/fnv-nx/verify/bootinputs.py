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
  milestones --table milestones.tsv --run run.txt -o table.txt
                                      milestones.tsv (an optional `# table_id H`, which must equal entries.tsv's, and rows `exe_entry_va<TAB>0xADDR`) + the loader's console lines
                                      of the contract's run.txt (every line that is exactly three spaces + `fnvnx-start: ...`, in file
                                      order, each distinct) -> a bootmiles table: milestones start-01.. (host console.<hex>, one per
                                      line, in boot order) and then exe-entry (call ADDR)
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


def milestone_table(milestones_text, run_text, entries_id=None):
    rows, tid = _rows(milestones_text, ncols=2)
    if tid is not None and entries_id is not None and tid != entries_id:
        raise InputError(f"milestones.tsv table_id {tid} differs from entries.tsv {entries_id}")
    if [f[0] for _, f in rows] != ["exe_entry_va"]:
        raise InputError("milestones.tsv: the only row understood is exe_entry_va")
    tok = rows[0][1][1]
    if not tok.startswith("0x"):
        raise InputError(f"line {rows[0][0]}: exe_entry_va must be 0x<hex>, got {tok!r}")
    entry = _hex(tok[2:], "exe_entry_va", rows[0][0])
    lines = []
    for line in run_text.split("\n"):
        if line.startswith(CONSOLE_PREFIX):
            lines.append(line[3:].rstrip("\r"))
    if len(set(lines)) != len(lines):
        raise InputError("run.txt: a loader console line occurs twice; its milestone would be ambiguous")
    out = [f"# generated from milestones.tsv and the loader's console lines of run.txt (table_id {tid})"]
    for i, ln in enumerate(lines, 1):
        out.append("start-%02d any host %s" % (i, boot.console_part(ln)))
    out.append("exe-entry any call 0x%x" % entry)
    return "\n".join(out) + "\n"


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
    m.add_argument("-o", required=True)
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
        else:
            eid = bootmiles.load_manifest(rd(a.entries)).table_id if a.entries else None
            open(a.o, "w").write(milestone_table(rd(a.table), rd(a.run) if a.run else "", eid))
    except (InputError, bootmiles.ManifestError, OSError) as e:
        src = {"join": "manifest.json", "reserved": "modules.tsv", "keys": "keys.tsv", "milestones": "milestones.tsv"}[a.cmd]
        print(f"bootinputs {a.cmd} ({src}/entries.tsv/run.txt): {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
