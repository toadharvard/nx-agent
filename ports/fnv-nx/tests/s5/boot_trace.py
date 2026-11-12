#!/usr/bin/env python3
"""boot_trace.py - the lockstep trace: the original FalloutNV.exe under Wine (tools/lockstep/fnvtrace) against the
guest in the emulator (emu_guest.py --trace-fn), same function list, same game files and no input; reports the
first divergence at a function boundary and writes real arguments per function for fuzz seeding.

    boot_trace.py fnlist  --addrmap TSV [--units JSON] [--only REGEX] [--max N] > list.txt
    boot_trace.py ref     <game dir> <function list> <out.tsv> [--seconds S] [--work DIR]
    boot_trace.py diff    <ref.tsv> <guest.tsv> [--units JSON] [--json OUT]      exit 0: no divergence, 1: one
    boot_trace.py compare GUEST.tsv WINE.tsv [--units JSON] [--resync N] [--json OUT] [--context K]
    boot_trace.py seeds   WINE.tsv [--units JSON] --out seeds.jsonl [--per-fn N]

fnlist: the functions both sides trace, one '<hex address> <name>' per line: every native the address map binds
(kind F, X, C) - optionally only names matching --only - minus compiler helpers and CRT (no native, kind E).

compare: both logs are reduced to the call tree of the main thread (the thread of the first logged call): call
(original address), its arguments, its return value, its writes to the objects it was given, its children. The
two trees are walked in lockstep, level by level: the children of each matched pair are aligned by original
address. A call present on one side only (GCC inlined what MSVC called, a stand-in skipped a body) is skipped
when the two sides meet again within --resync calls (counted per function); both sides skipping calls there is
a control divergence, and no meeting point ends that level. For each matched
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
inputs of the original program, as first fuzz cases (labels p0..).

Our own code; it reads logs and names only addresses.
"""
import argparse
import collections
import json
import os
import re
import sys

IMAGE_LO, IMAGE_HI = 0x00400000, 0x02100000
# The log format and tracer semantics of both sides; part of any cache key of a reference trace (T1: with the exe,
# the game data, the function list and tools/lockstep/fnvtrace.c). Bump it when a line shape or what is logged changes.
TRACE_FORMAT = "fnvtrace-2"   # 2: both sides boot with tools/lockstep/profile.py's profile


# ---- the logs ------------------------------------------------------------------------------------------------------
class Call:
    __slots__ = ("seq", "tid", "depth", "name", "orig", "regs", "ret", "deref", "writes", "kids", "parent", "lost",
                 "caller", "api")

    def __init__(self, seq, tid, depth, name, orig, regs, caller=""):
        self.seq, self.tid, self.depth, self.name, self.orig, self.regs = seq, tid, depth, name, orig, regs
        self.ret, self.deref, self.writes, self.kids, self.parent, self.lost = None, {}, {}, [], None, False
        self.caller = caller
        self.api = False


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
                    # an API entry is not a game function; main_thread() leaves it out (S4's plat baselines,
                    # tools/perf/plat_calls.py): Wine, a `dll!Export` list line (outside the game image); guest, a
                    # platform function traced by name (no original address)
                    c.api = (orig is not None and not (0x00400000 <= orig < 0x02100000)) if side == "wine" else orig is None
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
        if c.tid != tid or getattr(c, "api", False):
            continue   # an API entry's own calls (callbacks) attach to the game function around it
        while stack and stack[-1].depth >= c.depth:
            stack.pop()
        if stack:
            c.parent = stack[-1]
            stack[-1].kids.append(c)
        else:
            roots.append(c)
        stack.append(c)
    return [c for c in order if c.tid == tid and not getattr(c, "api", False)]


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
    """None: equal or not comparable; else a short reason. Exact only where a value means the same on both
    machines: two small integers, two image addresses, or an integer against a pointer; otherwise (heap, stack,
    the guest's own rodata against an image literal) by the C strings behind them when both sides dumped one."""
    sa_, sb_ = a < 0x10000, b < 0x10000
    ia, ib = IMAGE_LO <= a < IMAGE_HI, IMAGE_LO <= b < IMAGE_HI
    if sa_ or sb_:
        return None if a == b else f"{a:#x} != {b:#x}"
    if ia and ib and a != b:
        s1, s2 = cstring(da), cstring(db)
        if s1 is not None and s1 == s2:
            return None
        return f"{a:#x} != {b:#x}"
    s1, s2 = cstring(da), cstring(db)
    if s1 is not None and s2 is not None and s1 != s2:
        return f"string {s1[:40]!r} != {s2[:40]!r}"
    return None


# ---- compare -------------------------------------------------------------------------------------------------------
def compare(guest, wine, units, resync=64, context=8, limit=50):
    """lockstep walk of the two call trees, level by level: the children of each matched pair are aligned by
    original address (resync at the nearest common call within `resync`), compared, and walked into. Returns
    counts and the divergences in pre-order (the first is the report's). Divergences (T1, owner: the seams as a
    list, function by function): a value that differs (arg, return, write), a call that differs ("control"), calls
    the exe makes that the guest does not ("missing": a run of them at one place is one item) and calls the guest
    makes that the exe does not ("extra"), the first `limit` of them."""
    inlined = collections.Counter()
    st = {"matched": 0}
    divs = []

    def runtime(c):   # the guest's runtime calls game functions the exe never calls from there: operator
        # new/delete on behalf of the platform layer and libstdc++, and platform code (no original address) inside
        # a traced game call; a top-level entry (WinMain from fnvnx_guest_main, the initializer runner) stays
        return bool(c.caller) and (RUNTIME_CALLER.match(c.caller) is not None
                                   or ("[orig" not in c.caller and c.parent is not None))

    def noop(c):      # delete of NULL: GCC calls it, MSVC tests first
        return c.regs[0] == 0 or (c.orig in NOOP_IF_ARG1_NULL and c.regs[1] == 0)

    def gkids(cs):    # the guest's calls at this level: runtime calls spliced out, their children kept
        out = []
        for c in cs:
            if c.orig is None or runtime(c):
                out.extend(gkids(c.kids))
            else:
                out.append(c)
        return out

    def add(kind, cg, cw, detail):
        """cg / cw: the guest's and the exe's call where it shows (for missing / extra calls: the next common call
        on the other side, so both caller chains are the place)"""
        if len(divs) < limit:
            divs.append({"kind": kind, "guest": cg, "wine": cw, "detail": detail})

    def values(cg, cw):
        conv = msvc_conv(units.get(cg.orig))
        if units and conv:
            pairs = list(zip(a64_logical(cg, conv), x86_logical(cw, conv)))[:6]
        elif NO_PARAMS.search(cg.name):   # a free function without parameters: its registers are leftovers
            pairs = []
        else:   # no convention: x0 is the receiver (ecx) or the first stack argument; differing from both counts
            why_e = same(cg.regs[0], cw.regs[0], cg.deref.get("x0"), cw.deref.get("ecx"))
            why_s = same(cg.regs[0], cw.regs[2], cg.deref.get("x0"), cw.deref.get("s0"))
            if why_e and why_s:
                add("arg x0", cg, cw, f"{why_s} (and != ecx {cw.regs[0]:#x})")
            pairs = []
        for (lab, va, sa), (_l, vb, sb) in pairs:
            why = same(va, vb, cg.deref.get(sa), cw.deref.get(sb))
            if why:
                add(f"arg {lab}", cg, cw, why)
                break
        dec = units.get(cg.orig)
        void = msvc_void(dec) if dec else cg.ret == cg.regs[0]
        if cg.ret is not None and cw.ret is not None and not void:
            why = same(cg.ret, cw.ret)
            if why and cg.ret in (0, 1) and (cw.ret & 0xFF) == cg.ret:   # a bool: MSVC sets al only
                why = None
            if why:
                add("return", cg, cw, why)
        if cw.writes and cg.writes:
            recv_w, base_g = ("ecx" if cw.regs[0] else "s0"), cg.regs[0]
            gw = {k[1] - base_g: v for k, v in cg.writes.items() if k[0] == "abs" and base_g <= k[1] < base_g + 256}
            for (slot, off), new in sorted(cw.writes.items()):
                if slot == recv_w and off in gw and gw[off][0] == 4:
                    why = same(gw[off][1] & 0xFFFFFFFF, int.from_bytes(new, "little"))
                    if why:
                        add(f"write this+{off:#x}", cg, cw, why)
                        break

    def level(g, w):
        """align one level: anchors first (functions called at most 4 times on each side, the same number of
        times, paired in order and kept only while both positions increase), then the windowed walk between them"""
        if len(g) > resync and len(w) > resync:
            cg_, cw_ = collections.Counter(c.orig for c in g), collections.Counter(c.orig for c in w)
            rare = {o for o, n in cg_.items() if n <= 4 and cw_.get(o) == n}
            if rare:
                pos_g = collections.defaultdict(list)
                for k, c in enumerate(g):
                    if c.orig in rare:
                        pos_g[c.orig].append(k)
                seen = collections.Counter()
                anchors, last = [], -1
                for k, c in enumerate(w):
                    if c.orig in rare:
                        gi = pos_g[c.orig][seen[c.orig]]
                        seen[c.orig] += 1
                        if gi > last:
                            anchors.append((gi, k))
                            last = gi
                if anchors:
                    pi = pj = 0
                    for gi, wj in anchors:
                        walk(g[pi:gi], w[pj:wj])
                        walk(g[gi:gi + 1], w[wj:wj + 1])
                        pi, pj = gi + 1, wj + 1
                    walk(g[pi:], w[pj:])
                    return
        walk(g, w)

    def walk(g, w):
        i = j = 0
        while i < len(g) and j < len(w):
            cg, cw = g[i], w[j]
            if cg.orig != cw.orig:
                best = None
                for d in range(1, resync + 1):
                    for di in range(d + 1):
                        dj = d - di
                        if i + di < len(g) and j + dj < len(w) and g[i + di].orig == w[j + dj].orig:
                            best = (di, dj)
                            break
                    if best:
                        break
                if best is None:
                    # T1: a wider resync (the nearest common call within WIDE on both sides, by original address),
                    # so one control divergence does not end the level: the calls after it are compared too
                    pos = {}
                    for k in range(j, min(len(w), j + WIDE)):
                        pos.setdefault(w[k].orig, k)
                    for di in range(min(len(g) - i, WIDE)):
                        k = pos.get(g[i + di].orig)
                        if k is not None and (best is None or di + (k - j) < sum(best)):
                            best = (di, k - j)
                        if best is not None and di >= sum(best):
                            break
                    add("control", cg, cw, f"no common call within {resync}: guest {cg.name} ({cg.orig:#010x}), "
                                           f"the exe {cw.name} ({cw.orig:#010x})"
                        + (f"; resynced {best[0]} guest and {best[1]} exe calls on" if best else
                           f"; none within {WIDE} either"))
                    if best is None:
                        return
                    di, dj = best
                    for c in g[i:i + di]:
                        inlined[("guest-only", c.name)] += 1
                    for c in w[j:j + dj]:
                        inlined[("wine-only", c.name)] += 1
                    i, j = i + di, j + dj
                    continue
                di, dj = best
                g_only = [c for c in g[i:i + di] if not noop(c)]
                for c in g[i:i + di]:
                    inlined[("guest-only" if c in g_only else "guest-noop", c.name)] += 1
                for c in w[j:j + dj]:
                    inlined[("wine-only", c.name)] += 1
                if g_only and dj:
                    add("control", g_only[0], w[j], f"guest calls {g_only[0].name} ({g_only[0].orig:#010x}) "
                                                    f"x{len(g_only)}, the exe {w[j].name} ({w[j].orig:#010x}) x{dj}")
                elif dj:      # the exe calls these here, the guest goes on with the next common call
                    names = collections.Counter(c.name for c in w[j:j + dj])
                    add("missing", g[i + di], w[j], f"the exe calls {w[j].name} ({w[j].orig:#010x})"
                        + (f" and {dj - 1} more ({', '.join('%s x%d' % kv for kv in names.most_common(3))})" if dj > 1 else "")
                        + f" before {g[i + di].name}; the guest does not")
                elif g_only:  # the guest calls these here, the exe does not
                    add("extra", g_only[0], w[j],
                        f"the guest calls {g_only[0].name} ({g_only[0].orig:#010x})"
                        + (f" and {len(g_only) - 1} more" if len(g_only) > 1 else "") + f" before {w[j].name}; "
                        "the exe does not")
                i, j = i + di, j + dj
                continue
            st["matched"] += 1
            values(cg, cw)
            level(gkids(cg.kids), cw.kids)
            i += 1
            j += 1
        rest_g = [c for c in g[i:] if not noop(c)]
        for c in g[i:]:
            inlined[("guest-only (after)", c.name)] += 1
        for c in w[j:]:
            inlined[("wine-only (after)", c.name)] += 1
        if rest_g and j < len(w):   # the level ends differently on the two sides
            add("control", rest_g[0], w[j], f"the level ends differently: guest {rest_g[0].name} "
                                            f"({rest_g[0].orig:#010x}) and {len(rest_g) - 1} more, the exe "
                                            f"{w[j].name} ({w[j].orig:#010x}) and {len(w) - j - 1} more")
        elif j < len(w) and g:   # the exe made calls here that the guest did not
            add("missing", g[-1], w[j], f"after {g[-1].name} the exe calls {w[j].name} ({w[j].orig:#010x}) "
                                           f"and {len(w) - j - 1} more; the guest none")

    sys.setrecursionlimit(max(10000, sys.getrecursionlimit()))
    groots = gkids([c for c in guest if c.parent is None])
    wroots = [c for c in wine if c.parent is None]
    level(groots, wroots)
    # the first divergence (the report's headline, cmd_diff's exit) is the first value or control difference; calls
    # one side makes and the other does not (GCC inlines what MSVC calls) are listed with the others, not first
    first = next((d for d in divs if d["kind"] not in ("missing", "extra")), None)
    return {"matched": st["matched"], "guest_calls": sum(1 for c in guest if c.orig is not None),
            "wine_calls": len(wine), "inlined": inlined, "first": first, "divergences": divs,
            "end": "divergence" if first else ("calls missing or extra only" if divs else
                   ("nothing to compare: no guest call of these functions" if not groots else "no divergence"))}


WIDE = 2048    # the wider resync after a control divergence (calls per side)
NO_PARAMS = re.compile(r"^_Z\d+\w+?v$")   # free function, no parameters (Itanium: ...v)
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
    # T1: a side with no trace is an error that names it, not a traceback (bt1 0e2a4b160f9e: the guest wrote none)
    for side, tp in (("guest", a.guest), ("wine", a.wine)):
        if not os.path.isfile(tp) or os.path.getsize(tp) == 0:
            print(f"compare: no {side} trace ({tp} {'is empty' if os.path.isfile(tp) else 'does not exist'})",
                  file=sys.stderr)
            return 2
    _, g_order = read_log(a.guest, "guest")
    _, w_order = read_log(a.wine, "wine")
    if getattr(a, "only_list", None):
        # the functions of one reference's list only (T1: one guest trace on the union of several references'
        # lists, compared with each); main_thread nests by depth, so leaving calls out keeps the nesting
        keep = {int(l.split()[0], 16) for l in open(a.only_list) if l.strip() and not l.startswith("#")
                and re.fullmatch(r"[0-9a-fA-F]{6,8}", l.split()[0])}
        g_order = [c for c in g_order if c.orig in keep]
        w_order = [c for c in w_order if c.orig in keep or c.api]
    g, w = main_thread(g_order), main_thread(w_order)
    units = load_units(a.units)
    r = compare(g, w, units, a.resync, a.context, limit=a.max_div)
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
    elif not r["guest_calls"]:
        print("nothing compared: the guest trace has no call of these functions")
    else:
        print("no divergence in the matched calls")
    if r["divergences"]:
        n = len(r["divergences"])
        print(f"DIVERGENCES (the first {n}{'; --max-div for more' if n >= a.max_div else ''}), each with both "
              f"caller chains (the last {a.context} calls):")
        for k, d in enumerate(r["divergences"], 1):
            print(f"{k:3d}. {d['kind']} in {d['guest'].name} (guest #{d['guest'].seq}, exe #{d['wine'].seq}): {d['detail']}")
            print("       guest: " + " > ".join(fmt_call(c, 'guest') for c in path(d['guest'])[-a.context:]))
            print("       exe:   " + " > ".join(fmt_call(c, 'wine') for c in path(d['wine'])[-a.context:]))
        kinds = collections.Counter(d["kind"].split()[0] for d in r["divergences"])
        print("  by kind: " + ", ".join("%s %d" % kv for kv in kinds.most_common()))
    if a.json:
        out = {k: v for k, v in r.items() if k not in ("first", "inlined", "divergences")}
        out["inlined"] = [[s, n, c] for (s, n), c in r["inlined"].most_common()]
        out["divergences"] = [{"kind": d["kind"], "detail": d["detail"], "function": d["guest"].name,
                               "orig": f"{d['guest'].orig:08x}", "guest_seq": d["guest"].seq,
                               "ref_seq": d["wine"].seq, "exe_function": d["wine"].name,
                               "guest_path": [fmt_call(c, "guest") for c in path(d["guest"])],
                               "exe_path": [fmt_call(c, "wine") for c in path(d["wine"])]} for d in r["divergences"]]
        if f:
            out["first"] = {"kind": f["kind"], "detail": f["detail"], "function": f["guest"].name,
                            "guest_seq": f["guest"].seq, "ref_seq": f["wine"].seq, "format": TRACE_FORMAT,
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


def cmd_ref(a):
    """the reference trace alone: tools/lockstep/wine_trace.sh (its output depends only on the exe, the game data,
    the list and the harness, so it can be cached)"""
    import os
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    tool = os.path.join(here, "..", "..", "tools", "lockstep", "wine_trace.sh")
    work = a.work or os.path.join(os.path.dirname(os.path.abspath(a.out)), "wine_work")
    r = subprocess.run(["bash", tool, a.game, work, a.list, a.out, str(a.seconds)])
    if r.returncode or not os.path.exists(a.out):
        print(f"ref: wine_trace.sh exit {r.returncode}", file=sys.stderr)
        return 2
    print(f"ref: {TRACE_FORMAT} -> {a.out}")
    return 0


def cmd_diff(a):
    """compare with the reference first; exit 1 on a divergence, with its seq, function and field"""
    a.guest, a.wine = a.guest_tsv, a.ref_tsv
    a.resync, a.context = 64, 8
    a.max_div = getattr(a, "max_div", 50)
    out = a.json
    rc = cmd_compare(a)
    if rc:
        return rc
    if out:
        r = json.load(open(out))
        return 1 if r.get("first") else 0
    _, g_order = read_log(a.guest, "guest")
    _, w_order = read_log(a.wine, "wine")
    r = compare(main_thread(g_order), main_thread(w_order), load_units(a.units))
    return 1 if r["first"] else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("compare")
    p.add_argument("guest")
    p.add_argument("wine")
    p.add_argument("--units")
    p.add_argument("--resync", type=int, default=64)
    p.add_argument("--context", type=int, default=8)
    p.add_argument("--max-div", type=int, default=50, help="report the first N divergences (default 50)")
    p.add_argument("--only-list", help="compare only the functions of this list (fnlist format <hex> <name>)")
    p.add_argument("--json")
    p.set_defaults(fn=cmd_compare)
    p = sub.add_parser("ref")
    p.add_argument("game")
    p.add_argument("list")
    p.add_argument("out")
    p.add_argument("--seconds", type=int, default=300)
    p.add_argument("--work")
    p.set_defaults(fn=cmd_ref)
    p = sub.add_parser("diff")
    p.add_argument("ref_tsv")
    p.add_argument("guest_tsv")
    p.add_argument("--units")
    p.add_argument("--json")
    p.set_defaults(fn=cmd_diff)
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
