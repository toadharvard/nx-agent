#!/usr/bin/env python3
"""Boot frontier: what an emulated boot hit that has no native translation, mapped to the decomp units that
define it and their compile status. This is S1's priority list: the first untranslated address in boot order
is where the boot stops being the game.

    python3 -I tests/s5/frontier.py <emu log> --addrmap <S2 out>/addrmap.tsv --units <tree>/build/units.json
        [--objects <compile.sh obj dir>/objects.tsv ...] [--census <census out>/units.tsv] [--imports imports.tsv]
        [--out <prefix>] [--top N]

Inputs:
  emu log        tests/s5/emu_guest.py output. Its `trap-table` lines (toolchain/game/boot.cpp keeps per-key hit
                 counts) give the hits and first-hit order. Older logs fall back to the `trap #N: unresolved` lines,
                 which have the order but no counts.
  --addrmap      S2's address map: orig address <-> link symbol, native symbol, object.
  --units        units.json of the tree the objects were compiled from: which unit defines which address.
  --objects      tools/link/compile.sh's objects.tsv (unit, object, ok|fail). Each failed unit's first error is
                 read from the .log next to the object. This is the compile status of the linked object set.
  --census       tools/census.sh's units.tsv (unit, errors, first error), e.g. S1's latest census. This is the
                 unit's current status.

Output (<prefix>.tsv, <prefix>_units.tsv and a summary on stdout), one row per trapped key in first-hit order:
  rank, seq, hits, phase (init|winmain), kind (sym|ptr|iat), key (symbol, or orig address for ptr/iat),
  orig (the original address, if known), class (why it is untranslated), units (the units that define the
  address), link (compile status in the linked set), census (current errors and first error), caller (first
  caller, symbolized). Per unit: first rank, keys, hits, link status, census status, first error.
The tool reads the decomp-derived files at run time and writes its reports wherever --out says (keep them out of
the repository); it contains no decomp data itself.
"""
import argparse
import bisect
import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _table(name):
    """a decomp-derived table of fnv-nx-build (tools/tables.py: FNVNX_TABLES, sha256 against tools/tables.pin.json)"""
    import importlib.util
    s = importlib.util.spec_from_file_location('fnvnx_tables', os.path.join(HERE, "..", "..", 'tools', 'tables.py'))
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m.path(name)


def read_log(path):
    """-> (records [(seq, hits, phase, kind, key, caller)], has_counts, faults, stop)"""
    recs, fallback, faults, stop, skips = [], [], [], [], []
    phase = "init"
    lines = open(path, errors="replace").read().splitlines()
    for i, l in enumerate(lines):
        if l.startswith("trap-table\t"):
            f = l.split("\t")
            recs.append((int(f[1]), int(f[2]), f[3], f[4], f[5], f[6] if len(f) > 6 else "-"))
            continue
        if l.startswith("skip-summary\t"):   # every skipped initializer, grouped (emu_guest.py)
            f = l.split("\t")
            skips.append((int(f[1]), f[2], f[3], f[4] if len(f) > 4 else ""))
            continue
        if "fnvnx boot: calling WinMain" in l:
            phase = "winmain"
        m = re.search(r"trap #(\d+): unresolved (\S+) \(caller (0x[0-9a-f]+)\)", l)
        if m:
            fallback.append((len(fallback) + 1, 0, phase, "sym", m.group(2), m.group(3)))
        m = re.search(r"trap: (IAT slot|image code pointer to) (0x[0-9a-f]+)", l)
        if m:
            k = "iat" if m.group(1) == "IAT slot" else "ptr"
            if not any(r[4] == m.group(2) and r[3] == k for r in fallback):
                fallback.append((len(fallback) + 1, 0, phase, k, m.group(2), "-"))
        m = re.match(r"\s*skipped initializer (0x[0-9a-f]+) (.*?): (.*?) \(UC_ERR_\w+\) at (0x[0-9a-f]+) (.*)$", l)
        if m:
            faults.append((m.group(2), m.group(3), m.group(5)))
        if l.startswith("== trace ("):
            stop = [l] + [x for x in lines[i + 1:i + 10] if x.strip().startswith(("pc", "lr", "#", "S10"))]
        if l.startswith("STOPPED") or l in ("PASS", "FAIL"):
            stop.append(l)
    if skips:   # the complete grouping replaces the first 30 printed skip lines
        faults = [(f"{n} x", why, site + " (first: " + first + ")") for n, why, site, first in skips]
    if recs:
        return sorted(recs), True, faults, stop
    return fallback, False, faults, stop


def read_addrmap(path):
    by_orig, by_native = {}, collections.defaultdict(set)
    for l in open(path, errors="replace"):
        if not l[:1].isalnum() or "\t" not in l:
            continue
        f = l.rstrip("\n").split("\t")
        try:
            a = int(f[0], 16)
        except ValueError:
            continue
        row = {"kind": f[1], "link": f[2] if len(f) > 2 else "", "native": f[3] if len(f) > 3 else "",
               "object": f[4] if len(f) > 4 else "", "note": f[5] if len(f) > 5 else ""}
        by_orig[a] = row
        for n in (row["native"], row["link"]):
            if n:
                by_native[n].add(a)
    return by_orig, by_native


def read_units(path):
    """-> (exact {addr: [(unit, symbol, size)]}, sorted starts, start -> (unit, symbol, size))"""
    d = json.load(open(path))
    exact = collections.defaultdict(list)
    for u in d["units"]:
        for f in u.get("functions", []):
            exact[int(f["address"], 16)].append((u["source"], f["symbol"], int(f.get("size") or 0)))
    starts = sorted(exact)
    return exact, starts


def read_objects(path):
    """compile.sh objects.tsv -> {unit: (status, first error)}"""
    out = {}
    for l in open(path, errors="replace"):
        f = l.rstrip("\n").split("\t")
        if len(f) < 3:
            continue
        unit, obj, st = f[0], f[1], f[2]
        err = ""
        if st != "ok":
            log = obj[:-2] + ".log" if obj.endswith(".o") else obj + ".log"
            try:
                for e in open(log, errors="replace"):
                    if ": error:" in e:
                        err = re.sub(r"^[^:]+:\d+:\d+: error: ", "", e.strip())
                        break
            except OSError:
                err = "(no compile log)"
        out[unit] = (st, err)
    return out


def read_census(path):
    out = {}
    for l in open(path, errors="replace"):
        f = l.rstrip("\n").split("\t")
        if len(f) >= 2 and f[1].isdigit():
            out[f[0]] = (int(f[1]), f[2] if len(f) > 2 else "")
    return out


def read_imports(path):
    out = {}
    if path and os.path.exists(path):
        for l in open(path, errors="replace"):
            f = l.rstrip("\n").split("\t")
            if l.startswith("0x") and len(f) >= 3:
                out[int(f[0], 16)] = f"{f[1]}!{f[2]}"
    return out


def read_map(path):
    """GNU ld map of the guest link -> sorted [(start, end, object basename)] of its .text input sections"""
    out, pending = [], None
    one = re.compile(r"^ (\.text\S*)\s+0x([0-9a-f]+)\s+0x([0-9a-f]+)\s+(\S.*)$")
    cont = re.compile(r"^\s+0x([0-9a-f]+)\s+0x([0-9a-f]+)\s+(\S.*)$")
    for l in open(path, errors="replace"):
        l = l.rstrip("\n")
        m = one.match(l)
        if m:
            a, n, obj = int(m.group(2), 16), int(m.group(3), 16), m.group(4)
        elif pending and cont.match(l):
            m = cont.match(l)
            a, n, obj = int(m.group(1), 16), int(m.group(2), 16), m.group(3)
        else:
            pending = re.match(r"^ \.text\S*$", l) is not None
            continue
        pending = False
        if n:
            out.append((a, a + n, os.path.basename(obj)))
    out.sort()
    return out


def orig_of(kind, key, by_native):
    if kind in ("ptr", "iat"):
        return int(key, 16)
    m = re.match(r"__fnv_([0-9a-fA-F]{8})", key)
    if m:
        return int(m.group(1), 16)
    a = by_native.get(key)
    return min(a) if a else None


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("log")
    ap.add_argument("--addrmap", required=True)
    ap.add_argument("--units", required=True)
    ap.add_argument("--objects", action="append", default=[],
                    help="compile.sh objects.tsv; repeat for incremental sets (later files override earlier ones)")
    ap.add_argument("--census")
    ap.add_argument("--imports", default=None, help="imports.tsv (default: the pinned table of FNVNX_TABLES, tools/tables.py)")
    ap.add_argument("--map", help="the guest link's map (fnvnx.map): callers without [orig] -> their object's unit")
    ap.add_argument("--out", help="output prefix (default: <log> without .log + _frontier)")
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args(argv)

    recs, counted, faults, stop = read_log(a.log)
    by_orig, by_native = read_addrmap(a.addrmap)
    exact, starts = read_units(a.units)
    objects = {}
    unit_of_obj = {}
    for path in a.objects:
        objects.update(read_objects(path))
        for l in open(path, errors="replace"):
            f = l.rstrip("\n").split("\t")
            if len(f) >= 2:
                unit_of_obj[os.path.basename(f[1])] = f[0]
    lmap = read_map(a.map) if a.map else []
    lmap_starts = [s for s, _, _ in lmap]

    def unit_of_caller(caller):
        m = re.match(r"(0x[0-9a-f]+)", caller)
        if not m or not lmap:
            return []
        c = int(m.group(1), 16)
        i = bisect.bisect_right(lmap_starts, c) - 1
        if i >= 0 and lmap[i][0] <= c < lmap[i][1]:
            obj = lmap[i][2]
            return [unit_of_obj.get(obj, "(object) " + obj)]
        return []
    census = read_census(a.census) if a.census else {}
    imports = read_imports(a.imports or _table("imports.tsv"))

    def units_at(addr):
        """units defining addr exactly, else the function containing it"""
        if addr is None:
            return [], ""
        if addr in exact:
            return exact[addr], ""
        i = bisect.bisect_right(starts, addr) - 1
        if i >= 0:
            s = starts[i]
            hit = [u for u in exact[s] if s < addr < s + max(u[2], 1)]
            if hit:
                return hit, f"inside {s:#010x}+{addr - s:#x}"
        return [], ""

    def classify(kind, key, addr, us):
        row = by_orig.get(addr) if addr is not None else None
        if kind == "iat":
            return "IAT slot " + imports.get(addr, "not named in imports.tsv") + " without an export"
        if addr is None:
            if key.startswith("_Z"):
                return "C++ name without definition (declared only, or the defining unit failed)"
            return "symbol without definition"
        if not us:
            if row and row["kind"] == "E":
                return "no unit defines it (" + (row["note"] or "CRT/thunk") + ")"
            return "no unit defines it (not decompiled)"
        sts = [objects.get(u[0], ("?", ""))[0] for u in us]
        if objects and all(s != "ok" for s in sts):
            if census and all(census.get(u[0], (1, ""))[0] == 0 for u in us):
                return "defining unit failed in the linked set, clean in the census now (needs a fresh S2 run)"
            return "defining unit failed to compile"
        if objects and any(s == "ok" for s in sts):
            return "defining unit compiled, function not in the link (S2 binding or not emitted)"
        return "defined by a unit"

    rows = []
    for rank, (seq, hits, phase, kind, key, caller) in enumerate(recs, 1):
        addr = orig_of(kind, key, by_native)
        us, inside = units_at(addr)
        cls = classify(kind, key, addr, us)
        unames = sorted({u[0] for u in us})
        link = "; ".join(f"{objects[u][0]}" for u in unames if u in objects) if objects else ""
        cen = "; ".join(f"{census[u][0]} errors" + (f": {census[u][1]}" if census[u][1] else "")
                        for u in unames if u in census) if census else ""
        if not cen and objects:
            cen = "; ".join(objects[u][1] for u in unames if u in objects and objects[u][1])
        m = re.search(r"\[orig ([0-9a-f]{8})\]", caller)
        caller_units = sorted({u[0] for u in units_at(int(m.group(1), 16))[0]}) if m else []
        if not caller_units:
            caller_units = unit_of_caller(caller)
        rows.append(dict(rank=rank, seq=seq, hits=hits if counted else "", phase=phase, kind=kind, key=key,
                         orig=f"{addr:#010x}" if addr is not None else "", cls=cls + (f" ({inside})" if inside else ""),
                         units=",".join(unames), link=link, census=cen, caller=caller,
                         caller_units=",".join(caller_units)))

    out = a.out or re.sub(r"\.log$", "", a.log) + "_frontier"
    cols = ["rank", "seq", "hits", "phase", "kind", "key", "orig", "cls", "units", "link", "census", "caller",
            "caller_units"]
    with open(out + ".tsv", "w") as f:
        f.write("# " + "\t".join(cols) + "\n")
        for r in rows:
            f.write("\t".join(str(r[c]).replace("\t", " ") for c in cols) + "\n")

    # per unit: the unit that defines the address, else (for names without an address) the caller's unit
    per = collections.OrderedDict()
    for r in rows:
        owners = r["units"].split(",") if r["units"] else ([f"(caller) {u}" for u in r["caller_units"].split(",")]
                                                           if r["caller_units"] else ["(no unit)"])
        for u in owners:
            p = per.setdefault(u, dict(first=r["rank"], phase=r["phase"], keys=0, hits=0))
            p["keys"] += 1
            p["hits"] += r["hits"] or 0
    with open(out + "_units.tsv", "w") as f:
        f.write("# unit\tfirst_rank\tphase\tkeys\thits\tlink\tcensus_errors\tfirst_error\n")
        for u, p in per.items():
            name = u.replace("(caller) ", "")
            st, err = objects.get(name, ("", ""))
            ce, cerr = census.get(name, ("", ""))
            f.write(f"{u}\t{p['first']}\t{p['phase']}\t{p['keys']}\t{p['hits']}\t{st}\t{ce}\t{cerr or err}\n")

    # summary
    print(f"{a.log}: {len(rows)} untranslated keys hit" + (f", {sum(r['hits'] for r in rows)} hits" if counted else
                                                          " (no trap-table in the log: order only, no counts)"))
    by_cls = collections.Counter(re.sub(r" \(.*", "", r["cls"]) if not r["cls"].startswith("IAT") else "IAT slot"
                                 for r in rows)
    for c, n in by_cls.most_common():
        print(f"  {n:5d}  {c}")
    by_phase = collections.Counter(r["phase"] for r in rows)
    print("  by phase: " + ", ".join(f"{k} {v}" for k, v in by_phase.items()))

    def show(r):
        u = r["units"] or (f"(caller) {r['caller_units']}" if r["caller_units"] else "-")
        st = r["link"] + (f" | {r['census'][:90]}" if r["census"] else "")
        return f"  #{r['rank']:<4} {str(r['hits']):>6} {r['phase']:7} {r['key'][:44]:44} {u[:60]:60} {st}"
    print(f"\nfirst {a.top} in boot order (rank, hits, phase, key, unit, link | census):")
    for r in rows[:a.top]:
        print(show(r))
    if counted:
        print(f"\ntop {a.top} by hits:")
        for r in sorted(rows, key=lambda r: -r["hits"])[:a.top]:
            print(show(r))
    print(f"\nunits in boot order (first {a.top}):")
    for u, p in list(per.items())[:a.top]:
        name = u.replace("(caller) ", "")
        st, err = objects.get(name, ("", ""))
        ce, cerr = census.get(name, ("", ""))
        print(f"  #{p['first']:<4} {p['keys']:3} keys {p['hits']:6} hits  {u[:64]:64} {st:4} "
              f"{(str(ce) + ' errors') if ce != '' else ''} {(cerr or err)[:80]}")
    if faults and faults[0][0].endswith(" x"):
        print("\ninitializers skipped after a fault, by fault and faulting function (all of them):")
        for n, why, site in faults[:15]:
            m = re.search(r"\[orig ([0-9a-f]{8})\]", site)
            us = sorted({u[0] for u in units_at(int(m.group(1), 16))[0]}) if m else []
            print(f"  {n:>8}  {why[:34]:34} {site[:110]}" + (f"  <- {','.join(us)[:60]}" if us else ""))
    elif faults:
        fs = collections.Counter(re.sub(r"\+0x[0-9a-f]+$", "", f[2]) for f in faults)
        print(f"\ninitializer faults (printed: {len(faults)}), by faulting function:")
        for k, n in fs.most_common(8):
            print(f"  {n:5d}  {k}")
    if stop:
        print("\nstop:")
        for l in stop:
            print("  " + l.strip())
    print(f"\nwrote {out}.tsv and {out}_units.tsv")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
