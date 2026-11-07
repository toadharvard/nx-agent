#!/usr/bin/env python3
"""boot_trace.py - the lockstep trace: the original FalloutNV.exe under Wine (tools/lockstep/fnvtrace) against the
guest in the emulator (emu_guest.py --trace-fn), same function list, same game files and no input; reports the
first divergence at a function boundary and writes real arguments per function for fuzz seeding.

    boot_trace.py fnlist  --addrmap TSV [--units JSON] [--only REGEX] [--max N] > list.txt
    boot_trace.py compare GUEST.tsv WINE.tsv [--units JSON] [--resync N] [--json OUT] [--context K]
    boot_trace.py seeds   WINE.tsv [--units JSON] --out seeds.jsonl [--per-fn N]

fnlist: the functions both sides trace, one '<hex address> <name>' per line: every native the address map binds
(kind F, X, C) - optionally only names matching --only - minus compiler helpers and CRT (no native, kind E).

compare: both logs are reduced to the call tree of the main thread (the thread of the first logged call) in order:
call (original address), its arguments, its return value, and its writes to the objects it was given. The two
call streams are walked in lockstep by original address. A call present on one side only (GCC inlined what MSVC
called, or a stand-in skipped a body) is skipped when the other side's call is found within --resync calls ahead
(counted per function: 'inlined'); otherwise that is a control divergence and the walk stops. For each matched
pair the values are compared where they mean the same on both machines:
  - image addresses (0x00400000..0x02100000: code, vftables, globals) and small integers (< 0x10000) must be equal;
  - other values (heap and stack pointers, which differ by construction) are compared by what they point to when
    both sides dumped it: a C string must be equal;
  - with --units (the tree's units.json, MSVC decorated names), x86 arguments are mapped to logical ones by the
    calling convention (thiscall: ecx = this, then the stack; fastcall: ecx, edx; cdecl/stdcall: the stack) and
    compared with the guest's x0..x7; without it only the receiver/first argument (ecx or s0 vs x0) and the return
    value are compared;
  - writes: the x86 side logs 4-byte changes at offsets of each argument object (FNVTRACE_SNAP); the guest's
    absolute writes are mapped to the same (argument, offset) form; a final value that differs is a divergence.
The report names the first divergence: function, kind (control, arg <n>, return, write <arg>+<off>), both values,
and the call path (the enclosing calls on both sides) with --context lines before it.

seeds: per function, the distinct argument tuples of the reference run (values and the bytes behind pointers),
as JSON lines {"address", "name", "conv", "cases": [{"args": {label: hex}, "deref": {label: hex}}]}: the real
inputs of the original program, for tools/difffuzz (fuzz.py's queue takes them as first cases; labels this, p0..).

Our own code; it reads logs and names only addresses.
"""
import argparse
import collections
import json
import re
import sys

IMAGE_LO, IMAGE_HI = 0x00400000, 0x02100000


# ---- the logs ------------------------------------------------------------------------------------------------------
class Call:
    __slots__ = ("seq", "tid", "depth", "name", "orig", "regs", "ret", "deref", "writes", "kids", "parent", "lost",
                 "caller")

    def __init__(self, seq, tid, depth, name, orig, regs, caller=""):
        self.seq, self.tid, self.depth, self.name, self.orig, self.regs = seq, tid, depth, name, orig, regs
        self.ret, self.deref, self.writes, self.kids, self.parent, self.lost = None, {}, {}, [], None, False
        self.caller = caller


def read_log(path, side):
    """{seq: Call} and the calls in order. side 'guest': regs x0..x7; 'wine': ecx, edx, s0..s7."""
    calls, order = {}, []
    with open(path, errors="replace") as f:
        for line in f:
            if not line or line[0] == "#":
                continue
            p = line.rstrip("\n").split("\t")
            k = p[0]
            try:
                if k == "call":
                    seq, tid, depth = int(p[1]), int(p[2]), int(p[3])
                    nregs = 8 if side == "guest" else 10
                    regs = [int(v, 16) & 0xFFFFFFFF for v in p[6:6 + nregs]]
                    orig = int(p[5], 16) if p[5] not in ("-", "") else None
                    caller = p[6 + nregs + 2] if side == "guest" and len(p) > 6 + nregs + 2 else ""
                    c = Call(seq, tid, depth, p[4], orig, regs, caller)
                    calls[seq] = c
                    order.append(c)
                elif k == "ret":
                    c = calls.get(int(p[1]))
                    if c:
                        c.ret = int(p[5], 16) & 0xFFFFFFFF
                elif k == "arg":
                    c = calls.get(int(p[1]))
                    if c:
                        c.deref[p[2]] = bytes.fromhex(p[3])
                elif k == "wr":   # wine: slot, offset, old, new
                    c = calls.get(int(p[1]))
                    if c:
                        c.writes[(p[2], int(p[3]))] = bytes.fromhex(p[5])
                elif k == "w":    # guest: absolute address, size, value
                    c = calls.get(int(p[1]))
                    if c:
                        c.writes[("abs", int(p[3], 16))] = (int(p[4]), int(p[5], 16))
                elif k == "lost":
                    c = calls.get(int(p[1]))
                    if c:
                        c.lost = True
            except (ValueError, IndexError):
                continue
    return calls, order


def main_thread(order):
    """the calls of the thread of the first call, as a forest by depth"""
    if not order:
        return []
    tid = order[0].tid
    roots, stack = [], []
    for c in order:
        if c.tid != tid:
            continue
        while stack and stack[-1].depth >= c.depth:
            stack.pop()
        if stack:
            c.parent = stack[-1]
            stack[-1].kids.append(c)
        else:
            roots.append(c)
        stack.append(c)
    return [c for c in order if c.tid == tid]


# ---- calling conventions from the MSVC decorated name --------------------------------------------------------------
MEMBER_THIS = set("ABEFIJMNQRUV")   # access codes of non-static member functions (a this qualifier follows)
MEMBER_STATIC = set("CDKLST")


def msvc_conv(dec):
    """'thiscall' | 'fastcall' | 'cdecl' | 'stdcall' | None from a decorated name"""
    if not dec or not dec.startswith("?"):
        return None
    i = dec.find("@@")
    if i < 0:
        return None
    rest = dec[i + 2:]
    if not rest:
        return None
    if rest[0] == "Y":
        cc = rest[1:2]
    elif rest[0] in MEMBER_THIS:
        cc = rest[2:3]          # skip the this qualifier (A, B, ...)
    elif rest[0] in MEMBER_STATIC:
        cc = rest[1:2]
    else:
        return None
    return {"A": "cdecl", "E": "thiscall", "G": "stdcall", "I": "fastcall"}.get(cc)


def msvc_void(dec):
    """the decorated name's return type is void ('X' after the calling convention letter)"""
    i = dec.find("@@") if dec else -1
    if i < 0:
        return False
    rest = dec[i + 2:]
    k = 2 if rest[:1] == "Y" else (3 if rest[:1] in MEMBER_THIS else 2)
    return rest[k:k + 1] == "X"


def load_units(path):
    """{original address: MSVC decorated name} from units.json (any nesting: dicts with address + msvc name)"""
    if not path:
        return {}
    data = json.load(open(path))
    out = {}

    def walk(o):
        if isinstance(o, dict):
            a = o.get("address") or o.get("addr") or o.get("va")
            n = o.get("msvc") or o.get("decorated") or o.get("msvc_name") or o.get("mangled")
            if a is not None and isinstance(n, str) and n.startswith("?"):
                try:
                    out[int(a, 16) if isinstance(a, str) else int(a)] = n
                except ValueError:
                    pass
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(data)
    return out


def x86_logical(c, conv):
    """logical argument list (label, value, deref slot) of a wine call"""
    ecx, edx, st = c.regs[0], c.regs[1], c.regs[2:]
    stk = [(f"p{i}", v, f"s{i}") for i, v in enumerate(st)]
    if conv == "thiscall":
        return [("this", ecx, "ecx")] + [(f"p{i}", v, f"s{i}") for i, v in enumerate(st)]
    if conv == "fastcall":
        return [("p0", ecx, "ecx"), ("p1", edx, "edx")] + [(f"p{i + 2}", v, f"s{i}") for i, v in enumerate(st)]
    return stk


def a64_logical(c, conv):
    labels = (["this"] + [f"p{i}" for i in range(7)]) if conv == "thiscall" else [f"p{i}" for i in range(8)]
    return [(labels[i], c.regs[i], f"x{i}") for i in range(8)]


# ---- value comparison ----------------------------------------------------------------------------------------------
def cstring(b):
    if not b:
        return None
    z = b.find(b"\0")
    s = b if z < 0 else b[:z]
    if len(s) < 2 or any(ch < 0x20 or ch > 0x7e for ch in s):
        return None
    return s


def same(a, b, da=None, db=None):
    """None: equal or not comparable; else a short reason"""
    ia, ib = IMAGE_LO <= a < IMAGE_HI, IMAGE_LO <= b < IMAGE_HI
    if a < 0x10000 or b < 0x10000 or ia or ib:
        if a != b and not (ia and ib and False):
            return f"{a:#x} != {b:#x}"
        return None
    sa, sb = cstring(da), cstring(db)
    if sa is not None and sb is not None and sa != sb:
        return f"string {sa[:40]!r} != {sb[:40]!r}"
    return None


# ---- compare -------------------------------------------------------------------------------------------------------
def compare(guest, wine, units, resync=64, context=8, limit=20):
    """lockstep walk; returns counts and the divergences in order (the first is the report's)"""
    # the guest's own runtime calls game functions the exe never calls from there: operator new/delete
    # (global_new.cpp -> MemoryManager) on behalf of the platform layer and libstdc++, and any caller without an
    # original address (platform code); they are left out of the stream
    def runtime(c):
        return bool(c.caller) and (RUNTIME_CALLER.match(c.caller) is not None or "[orig" not in c.caller)
    g = [c for c in guest if c.orig is not None and not runtime(c)]
    w = wine
    i = j = 0
    inlined = collections.Counter()
    matched = 0
    divs = []

    def add(kind, cg, cw, detail):
        if len(divs) < limit:
            divs.append({"kind": kind, "guest": cg, "wine": cw, "detail": detail})

    def noop(c):   # a guest call with null receiver/first argument (delete of NULL: GCC calls, MSVC tests first)
        return c.regs[0] == 0 or (c.orig in NOOP_IF_ARG1_NULL and c.regs[1] == 0)
    while i < len(g) and j < len(w):
        cg, cw = g[i], w[j]
        if cg.orig != cw.orig:
            # resync at the nearest common call (smallest di + dj): one-sided calls are inlining or extra calls
            best = None
            for d in range(1, resync + 1):
                for di in range(0, d + 1):
                    dj = d - di
                    if i + di < len(g) and j + dj < len(w) and g[i + di].orig == w[j + dj].orig:
                        best = (di, dj)
                        break
                if best:
                    break
            if best is None:
                add("control", cg, cw, f"no common call within {resync}: guest {cg.name} ({cg.orig:#010x}), "
                                       f"the exe {cw.name} ({cw.orig:#010x})")
                break
            di, dj = best
            g_only = [c for c in g[i:i + di] if not noop(c)]
            for c in g[i:i + di]:
                inlined[("guest-only" if c in g_only else "guest-noop", c.name)] += 1
            for c in w[j:j + dj]:
                inlined[("wine-only", c.name)] += 1
            if g_only and dj:   # both sides made calls the other did not: the paths differ here
                add("control", g_only[0], w[j], f"guest calls {g_only[0].name} ({g_only[0].orig:#010x}) x{len(g_only)}"
                                                f", the exe {w[j].name} ({w[j].orig:#010x}) x{dj}")
            i, j = i + di, j + dj
            continue
        matched += 1
        conv = msvc_conv(units.get(cg.orig))
        if units and conv:
            la, lb = a64_logical(cg, conv), x86_logical(cw, conv)
            pairs = list(zip(la, lb))[:6]
        else:
            pairs = [(("x0", cg.regs[0], "x0"), ("ecx|s0", cw.regs[0] if cw.regs[0] else cw.regs[2],
                                                  "ecx" if cw.regs[0] else "s0"))]
        for (lab, va, sa), (_lab2, vb, sb) in pairs:
            why = same(va, vb, cg.deref.get(sa), cw.deref.get(sb))
            if why:
                add(f"arg {lab}", cg, cw, why)
                break
        dec = units.get(cg.orig)
        void = msvc_void(dec) if dec else cg.ret == cg.regs[0]   # without a decorated name: x0 untouched
        if cg.ret is not None and cw.ret is not None and not void:
            why = same(cg.ret, cw.ret)
            if why:
                add("return", cg, cw, why)
        if cw.writes and cg.writes:
            recv_w = "ecx" if cw.regs[0] else "s0"
            base_g = cg.regs[0]
            gw = {}
            for key, val in cg.writes.items():
                if key[0] == "abs" and base_g <= key[1] < base_g + 256:
                    gw[key[1] - base_g] = val
            for (slot, off), new in sorted(cw.writes.items(), key=lambda kv: (kv[0][0], kv[0][1])):
                if slot != recv_w or off not in gw:
                    continue
                size, v = gw[off]
                why = same(v & 0xFFFFFFFF, int.from_bytes(new, "little")) if size == 4 else None
                if why:
                    add(f"write this+{off:#x}", cg, cw, why)
                    break
        i += 1
        j += 1
    end = "guest ended" if i >= len(g) else ("wine ended" if j >= len(w) else "no resync")
    return {"matched": matched, "guest_calls": len(g), "wine_calls": len(w), "inlined": inlined,
            "first": divs[0] if divs else None, "divergences": divs, "end": end}


RUNTIME_CALLER = re.compile(r"^(_Znw|_Zna|_Zdl|_Zda|operator new|operator delete)")
NOOP_IF_ARG1_NULL = {0x00aa4060, 0x00401030}   # MemoryManager::Deallocate(mm, NULL), FormHeap_Free(NULL)


def path(c):
    out = []
    while c is not None:
        out.append(c)
        c = c.parent
    return list(reversed(out))


def fmt_call(c, side):
    regs = " ".join(f"{v:#x}" for v in c.regs[:4 if side == "guest" else 5])
    ret = f" -> {c.ret:#x}" if c.ret is not None else ""
    orig = f"{c.orig:#010x}" if c.orig is not None else "-"
    return f"#{c.seq} {c.name} [{orig}] ({regs}){ret}"


def cmd_compare(a):
    _, g_order = read_log(a.guest, "guest")
    _, w_order = read_log(a.wine, "wine")
    g, w = main_thread(g_order), main_thread(w_order)
    units = load_units(a.units)
    r = compare(g, w, units, a.resync, a.context)
    print(f"lockstep: guest {r['guest_calls']} calls, exe {r['wine_calls']} calls (main thread), {r['matched']} "
          f"matched in order; ended: {r['end']}")
    top = r["inlined"].most_common(15)
    if top:
        print(f"  skipped on one side (inlined or not reached): {sum(r['inlined'].values())} calls; top:")
        for (side, name), n in top:
            print(f"    {n:6d}  {side:10s}  {name}")
    f = r["first"]
    if f:
        print(f"FIRST DIVERGENCE: {f['kind']} in {f['guest'].name}: {f['detail']}")
        print("  guest path: " + " > ".join(fmt_call(c, 'guest') for c in path(f['guest'])[-a.context:]))
        print("  exe path:   " + " > ".join(fmt_call(c, 'wine') for c in path(f['wine'])[-a.context:]))
        for d in r["divergences"][1:]:
            print(f"  then: {d['kind']} in {d['guest'].name}: {d['detail']}")
    else:
        print("no divergence in the matched calls")
    if a.json:
        out = {k: v for k, v in r.items() if k not in ("first", "inlined", "divergences")}
        out["inlined"] = [[s, n, c] for (s, n), c in r["inlined"].most_common()]
        out["divergences"] = [{"kind": d["kind"], "detail": d["detail"], "function": d["guest"].name,
                               "orig": f"{d['guest'].orig:08x}"} for d in r["divergences"]]
        if f:
            out["first"] = {"kind": f["kind"], "detail": f["detail"], "function": f["guest"].name,
                            "orig": f"{f['guest'].orig:08x}",
                            "guest_path": [fmt_call(c, "guest") for c in path(f["guest"])],
                            "exe_path": [fmt_call(c, "wine") for c in path(f["wine"])]}
        json.dump(out, open(a.json, "w"), indent=1)
    return 0


# ---- seeds ---------------------------------------------------------------------------------------------------------
def cmd_seeds(a):
    _, order = read_log(a.wine, "wine")
    units = load_units(a.units)
    per = collections.OrderedDict()
    for c in order:
        if c.orig is None:
            continue
        conv = msvc_conv(units.get(c.orig))
        args = x86_logical(c, conv or "cdecl")[:6 if conv else 10]
        key = tuple(v for _l, v, _s in args)
        e = per.setdefault(c.orig, {"name": c.name, "conv": conv, "seen": set(), "cases": []})
        if key in e["seen"] or len(e["cases"]) >= a.per_fn:
            continue
        e["seen"].add(key)
        e["cases"].append({"args": {lab: v.to_bytes(4, "little").hex() for lab, v, _s in args},
                           "deref": {lab: c.deref[s].hex() for lab, _v, s in args if s in c.deref}})
    with open(a.out, "w") as f:
        for addr, e in per.items():
            f.write(json.dumps({"address": f"{addr:08x}", "name": e["name"], "conv": e["conv"],
                                "source": "wine-boot", "cases": e["cases"]}) + "\n")
    print(f"seeds: {len(per)} functions, {sum(len(e['cases']) for e in per.values())} cases -> {a.out}")
    return 0


# ---- fnlist --------------------------------------------------------------------------------------------------------
def cmd_fnlist(a):
    rx = re.compile(a.only) if a.only else None
    n = 0
    seen = set()
    for line in open(a.addrmap):
        if line.startswith("#"):
            continue
        f = line.rstrip("\n").split("\t")
        if len(f) < 4 or f[1] not in ("F", "X", "C"):
            continue
        addr = int(f[0], 16)
        name = f[3] or f[2]
        if addr in seen or not (0x00401000 <= addr < 0x00fdf000):
            continue
        if rx and not rx.search(name):
            continue
        seen.add(addr)
        print(f"{addr:08x} {name}")
        n += 1
        if a.max and n >= a.max:
            break
    print(f"# {n} functions", file=sys.stderr)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("compare")
    p.add_argument("guest")
    p.add_argument("wine")
    p.add_argument("--units")
    p.add_argument("--resync", type=int, default=64)
    p.add_argument("--context", type=int, default=8)
    p.add_argument("--json")
    p.set_defaults(fn=cmd_compare)
    p = sub.add_parser("seeds")
    p.add_argument("wine")
    p.add_argument("--units")
    p.add_argument("--out", required=True)
    p.add_argument("--per-fn", type=int, default=16)
    p.set_defaults(fn=cmd_seeds)
    p = sub.add_parser("fnlist")
    p.add_argument("--addrmap", required=True)
    p.add_argument("--only")
    p.add_argument("--max", type=int, default=0)
    p.set_defaults(fn=cmd_fnlist)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
