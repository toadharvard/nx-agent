#!/usr/bin/env python3
"""stackuse - the stack growth a codegen flag causes, from the compiler's own reports (measurement, never a gate input).

    python3 -I stackuse.py --base OBJECTS.json... --flag OBJECTS.json... [--json OUT]

Inputs: the objects.json files of two builds of the same roots made with `recipe.py build --stack-usage 1` (RECIPE.md): BASE without the flag, FLAG with it.
Every row of an objects.json names the unit's object (`object`) and its `-fstack-usage` report (`su`: `file:line:col:name TAB bytes TAB qualifier`).

(a) Frame growth. A function is keyed by (unit, file:line:col, name), the compiler's own identity in both reports. Growth = FLAG bytes - BASE bytes. Reported: the
    number of functions in both, the largest growth with its function, the sum of all growth, the functions in one report only, and the functions whose qualifier is
    not `static` (dynamic frames have no byte bound: counted, never given a number).
(b) The deepest native call chain. The call graph of the objects: an edge for every R_AARCH64_CALL26 / JUMP26 relocation, from the function symbol that contains the
    relocated word to the function symbol the relocation names (a section symbol + addend resolves to the function at that offset); a function whose code contains a
    BLR or BR instruction word (a superset of its indirect calls: a data word that looks like one only adds a report) has an INDIRECT call. A frame of a function is the
    `.su` bytes of the function of that symbol name in that object (the C name is the symbol; a C++ name in the report is not the mangled symbol, so such a
    function has no frame: it is counted as `noframe`). The depth of a function = its frame + the largest depth of its callees. A cycle, a function with an
    indirect call, a dynamic frame, a callee with no definition among the objects (runtime library, other image) or a function with no frame makes the depth of
    everything that reaches it `unbounded`, and it is listed; no value is guessed. Growth along the chain = the same walk over (FLAG - BASE) frame growth.
Output: a JSON document and one summary line per number. Exit 0.
"""
import argparse
import json
import os
import struct
import sys

R_CALL26, R_JUMP26 = 283, 282
STT_FUNC, STT_SECTION = 2, 3


def read_su(text):
    """[(key, bytes, qualifier)] of a .su report; key = (file, line, col, name). A line the format does not match is an error (ValueError)."""
    out = []
    for ln in text.split("\n"):
        if not ln:
            continue
        head, nbytes, qual = ln.split("\t")
        p = head.split(":", 3)
        if len(p) != 4 or not (p[1].isdigit() and p[2].isdigit()):
            raise ValueError("not a stack-usage line: %r" % ln)
        f, line, col, name = p
        out.append(((f, int(line), int(col), name), int(nbytes), qual))
    return out


def growth(base, flag):
    """base, flag: {unit: [(key, bytes, qual)]} -> dict of the numbers of (a)."""
    both, only_b, only_f, dyn, tot, worst = 0, [], [], [], 0, None
    for u in sorted(set(base) | set(flag)):
        b = {k: (n, q) for k, n, q in base.get(u, [])}
        f = {k: (n, q) for k, n, q in flag.get(u, [])}
        only_b += [(u, k[3]) for k in b if k not in f]
        only_f += [(u, k[3]) for k in f if k not in b]
        for k in b.keys() & f.keys():
            (nb, qb), (nf, qf) = b[k], f[k]
            if qb != "static" or qf != "static":
                dyn.append((u, k[3], qb, qf))
                continue
            both += 1
            g = nf - nb
            tot += g
            if worst is None or g > worst[0]:
                worst = (g, u, k[3], nb, nf)
    return {"functions": both, "max_growth": worst, "sum_growth": tot, "only_base": sorted(only_b), "only_flag": sorted(only_f), "dynamic": sorted(dyn)}


def elf_graph(data):
    """-> (funcs {name: (section index, value, size)}, edges [(caller, callee or None, 'call'|'unresolved')], indirect {caller}) of an ELF64 AArch64 relocatable."""
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise ValueError("not an ELF64 little-endian object")
    shoff, = struct.unpack_from("<Q", data, 0x28)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", data, 0x3A)
    sh = []
    for i in range(shnum):
        name, typ, flags, addr, off, size, link, info, align, entsize = struct.unpack_from("<IIQQQQIIQQ", data, shoff + i * shentsize)
        sh.append({"name": name, "type": typ, "off": off, "size": size, "link": link, "info": info, "entsize": entsize})
    symtab = next((s for s in sh if s["type"] == 2), None)
    if symtab is None:
        raise ValueError("no symbol table")
    strtab = sh[symtab["link"]]

    def cstr(off):
        e = data.index(b"\0", strtab["off"] + off)
        return data[strtab["off"] + off:e].decode("latin1")
    syms = []
    for i in range(symtab["size"] // 24):
        name, info, other, shndx, value, size = struct.unpack_from("<IBBHQQ", data, symtab["off"] + i * 24)
        syms.append({"name": cstr(name), "type": info & 0xf, "shndx": shndx, "value": value, "size": size})
    funcs = {s["name"]: (s["shndx"], s["value"], s["size"]) for s in syms if s["type"] == STT_FUNC and s["shndx"] and s["shndx"] < 0xff00}
    by_sec = {}
    for n, (sec, v, z) in funcs.items():
        by_sec.setdefault(sec, []).append((v, z, n))

    def containing(sec, off):
        return next((n for v, z, n in by_sec.get(sec, []) if v <= off < v + max(z, 1)), None)
    edges, indirect = [], set()
    for sec, lst in by_sec.items():   # BLR (0xd63f0000) and BR (0xd61f0000) words: Rn is bits 5-9, the rest fixed
        for v, z, n in lst:
            code = data[sh[sec]["off"] + v: sh[sec]["off"] + v + z]
            for i in range(0, len(code) - 3, 4):
                w, = struct.unpack_from("<I", code, i)
                if (w & 0xfffffc1f) in (0xd63f0000, 0xd61f0000):
                    indirect.add(n)
    for s in sh:
        if s["type"] != 4:   # SHT_RELA
            continue
        target = s["info"]
        for i in range(s["size"] // 24):
            off, info, addend = struct.unpack_from("<QQq", data, s["off"] + i * 24)
            rtype, si = info & 0xffffffff, info >> 32
            if rtype not in (R_CALL26, R_JUMP26):
                continue
            caller = containing(target, off)
            sym = syms[si]
            if sym["type"] == STT_SECTION:
                callee = containing(sym["shndx"], addend)
                edges.append((caller, callee, "call" if callee else "unresolved"))
            else:
                edges.append((caller, sym["name"], "call"))
    return funcs, edges, indirect


def depth(frames, edges, indirect, defined):
    """frames {fn: bytes or None (no frame / dynamic)}, edges {fn: set of callees}, indirect {fn}, defined {fn}: -> ({fn: bytes or None}, {fn: why}).
    None = unbounded; why names the first reason found (cycle, indirect, noframe, undefined callee)."""
    memo, why, onpath = {}, {}, set()

    def walk(f):
        if f in memo:
            return memo[f]
        if f in onpath:
            why.setdefault(f, "cycle")
            return None
        if f not in defined:
            why[f] = "undefined callee"
            memo[f] = None
            return None
        onpath.add(f)
        fr = frames.get(f)
        best, bad = 0, False
        if fr is None:
            why.setdefault(f, "noframe")
            bad = True
        if f in indirect:
            why.setdefault(f, "indirect call")
            bad = True
        for c in sorted(edges.get(f, ())):
            d = walk(c)
            if d is None:
                why.setdefault(f, "reaches %s (%s)" % (c, why.get(c, "?")))
                bad = True
            else:
                best = max(best, d)
        onpath.discard(f)
        memo[f] = None if bad else (fr or 0) + best
        return memo[f]
    for f in sorted(defined):
        walk(f)
    return memo, why


def load_rows(paths):
    """objects.json files -> {unit key: (object path, su path)}; the key is root:unit so that two roots never collide."""
    rows = {}
    for p in paths:
        d = json.load(open(p))
        for r in d["objects"]:
            if r.get("su"):
                rows[(d["root"], r["unit"])] = (r["object"], r["su"])
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", nargs="+", required=True)
    ap.add_argument("--flag", nargs="+", required=True)
    ap.add_argument("--json")
    a = ap.parse_args()
    base, flag = load_rows(a.base), load_rows(a.flag)
    sus = lambda rows: {k: read_su(open(v[1]).read()) for k, v in rows.items()}  # noqa: E731
    sb, sf = sus(base), sus(flag)
    g = growth({"%s:%s" % k: v for k, v in sb.items()}, {"%s:%s" % k: v for k, v in sf.items()})

    def graph(rows, su):
        funcs, edges, ind, frames = {}, {}, set(), {}
        for k, (obj, _) in rows.items():
            f, e, i = elf_graph(open(obj, "rb").read())
            funcs.update({n: k for n in f})
            for c, d, kind in e:
                if c:
                    edges.setdefault(c, set()).add(d or "<unresolved>")
            ind |= i
            for key, nbytes, qual in su[k]:
                if key[3] in f:
                    frames[key[3]] = nbytes if qual == "static" else None
        return set(funcs), edges, ind, frames
    out = {"growth": g}
    dps = {}
    for tag, rows, su in (("base", base, sb), ("flag", flag, sf)):
        defined, edges, ind, frames = graph(rows, su)
        dp, why = depth(frames, edges, ind, defined)
        dps[tag] = dp
        bounded = {f: d for f, d in dp.items() if d is not None}
        top = max(bounded.items(), key=lambda kv: kv[1]) if bounded else None
        out[tag] = {"functions": len(defined), "bounded": len(bounded), "unbounded": len(dp) - len(bounded), "deepest_bounded": top,
                    "reasons": {r: sum(1 for w in why.values() if w.startswith(r)) for r in ("cycle", "indirect call", "noframe", "undefined callee", "reaches")}}
    both = [(dps["flag"][f] - dps["base"][f], f) for f in dps["base"] if dps["base"].get(f) is not None and dps["flag"].get(f) is not None]
    out["chain_growth"] = max(both) if both else None   # the largest (flag depth - base depth) over the functions bounded in both builds
    if a.json:
        json.dump(out, open(a.json, "w"), indent=1, sort_keys=True)
    print("(a) functions in both: %d, largest growth %s, sum of growth %d bytes; only base %d, only flag %d, dynamic %d" % (
        g["functions"], g["max_growth"], g["sum_growth"], len(g["only_base"]), len(g["only_flag"]), len(g["dynamic"])))
    for tag in ("base", "flag"):
        o = out[tag]
        print("(b) %s: %d functions, %d bounded, %d unbounded %s, deepest bounded chain %s" % (tag, o["functions"], o["bounded"], o["unbounded"], o["reasons"], o["deepest_bounded"]))
    print("(b) largest growth of a bounded chain (flag depth - base depth, over the functions bounded in both builds): %s" % (out["chain_growth"],))
    return 0


if __name__ == "__main__":
    sys.exit(main())
