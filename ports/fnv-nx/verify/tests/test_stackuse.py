#!/usr/bin/env python3
"""Tests for verify/stackuse.py on synthetic inputs (an ELF64 AArch64 relocatable built here, hand-written .su text). Plain script: exit 0 on pass."""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import stackuse as S  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)))
    fails += 0 if ok else 1


def elf(funcs, relocs):
    """funcs [(name, code bytes)] each in its own .text.<name>; relocs [(caller, callee symbol name or ('sec', fn name, addend), type)] -> object bytes."""
    names = [b"\0"]
    def add(b):
        names.append(b + b"\0")
        return sum(len(x) for x in names[:-1])
    secs = [(0, 0, b"", 0, 0, 0)]   # (name off, type, data, link, info, entsize)
    shstr = [b"\0"]
    def sname(n):
        off = sum(len(x) for x in shstr)
        shstr.append(n + b"\0")
        return off
    syms = [struct.pack("<IBBHQQ", 0, 0, 0, 0, 0, 0)]
    symidx, secidx = {}, {}
    for n, code in funcs:
        secs.append((sname(b".text." + n.encode()), 1, code, 0, 0, 0))
        secidx[n] = len(secs) - 1
    for n, code in funcs:
        symidx[n] = len(syms)
        syms.append(struct.pack("<IBBHQQ", add(n.encode()), (1 << 4) | 2, 0, secidx[n], 0, len(code)))
    secsym = {}
    for n, _ in funcs:
        secsym[n] = len(syms)
        syms.append(struct.pack("<IBBHQQ", 0, 3, 0, secidx[n], 0, 0))
    ext = {}
    for c, tgt, t in relocs:
        if isinstance(tgt, str) and tgt not in symidx and tgt not in ext:
            ext[tgt] = len(syms)
            syms.append(struct.pack("<IBBHQQ", add(tgt.encode()), (1 << 4) | 0, 0, 0, 0, 0))
    first_symtab = len(secs) + len(relocs and {c for c, _, _ in relocs})
    by_caller = {}
    for c, tgt, t in relocs:
        by_caller.setdefault(c, []).append((tgt, t))
    rela_secs = []
    symtab_index = len(secs) + len(by_caller)
    for c, lst in by_caller.items():
        data = b""
        for tgt, t in lst:
            if isinstance(tgt, tuple):
                si, addend = secsym[tgt[1]], tgt[2]
            else:
                si, addend = (symidx[tgt] if tgt in symidx else ext[tgt]), 0
            data += struct.pack("<QQq", 0, (si << 32) | t, addend)
        rela_secs.append((sname((".rela.text." + c).encode()), 4, data, symtab_index, secidx[c], 24))
    secs += rela_secs
    secs.append((sname(b".symtab"), 2, b"".join(syms), symtab_index + 1, 0, 24))
    secs.append((sname(b".strtab"), 3, b"".join(names), 0, 0, 0))
    secs.append((sname(b".shstrtab"), 3, b"".join(shstr), 0, 0, 0))
    body, hdrs, off = b"", [], 64
    for nm, typ, data, link, info, ent in secs:
        hdrs.append(struct.pack("<IIQQQQIIQQ", nm, typ, 0, 0, off if typ else 0, len(data), link, info, 1, ent))
        body += data
        off += len(data)
    shoff = 64 + len(body)
    hdr = b"\x7fELF" + bytes([2, 1, 1, 0]) + bytes(8) + struct.pack("<HHIQQQIHHHHHH", 1, 183, 1, 0, 0, shoff, 0, 64, 0, 0, 64, len(secs), len(secs) - 1)
    return hdr + body + b"".join(hdrs)


BL, RET, BLR = 0x94000000, 0xd65f03c0, 0xd63f0100
w = lambda *ws: b"".join(struct.pack("<I", x) for x in ws)  # noqa: E731
obj = elf([("a", w(BL, RET)), ("b", w(BLR, RET)), ("c", w(BL, BL, RET))],
          [("a", "b", S.R_CALL26), ("c", ("sec", "a", 0), S.R_JUMP26), ("c", "puts", S.R_CALL26)])
funcs, edges, ind = S.elf_graph(obj)
check("functions are the FUNC symbols with their section and size", sorted(funcs) == ["a", "b", "c"] and funcs["a"][2] == 8, funcs)
check("edges: a->b by symbol, c->a through a section symbol, c->puts (undefined)", sorted((c, d) for c, d, _ in edges) == [("a", "b"), ("c", "a"), ("c", "puts")], edges)
check("a function that holds a BLR word has an indirect call", ind == {"b"}, ind)
try:
    S.elf_graph(b"nope")
    check("a non-ELF input is an error", False)
except ValueError:
    check("a non-ELF input is an error", True)

su = S.read_su("/src/x.c:3:5:f\t48\tstatic\n/src/x.c:9:1:g\t16\tdynamic,bounded\n")
check("read_su: key, bytes, qualifier", su == [(("/src/x.c", 3, 5, "f"), 48, "static"), (("/src/x.c", 9, 1, "g"), 16, "dynamic,bounded")], su)
try:
    S.read_su("garbage\t1\tstatic\n")
    check("a line that is not a stack-usage line is an error", False)
except ValueError:
    check("a line that is not a stack-usage line is an error", True)

K = lambda n, l=1: ("/src/x.c", l, 1, n)  # noqa: E731
g = S.growth({"u": [(K("f"), 48, "static"), (K("g", 2), 16, "static"), (K("only"), 8, "static"), (K("d", 3), 4, "dynamic")]},
             {"u": [(K("f"), 64, "static"), (K("g", 2), 16, "static"), (K("new", 5), 8, "static"), (K("d", 3), 4, "dynamic")]})
check("growth: functions in both, largest, sum, one-report-only and dynamic ones are counted apart",
      (g["functions"], g["max_growth"], g["sum_growth"], g["only_base"], g["only_flag"], [d[1] for d in g["dynamic"]]) == (2, (16, "u", "f", 48, 64), 16, [("u", "only")], [("u", "new")], ["d"]), g)

fr = {"a": 16, "b": 32, "c": 8, "d": 4}
dp, why = S.depth(fr, {"a": {"b"}, "b": {"c"}}, set(), {"a", "b", "c", "d"})
check("depth of a clean chain: frame + the deepest callee", (dp["a"], dp["b"], dp["c"]) == (56, 40, 8), dp)
dp, why = S.depth(fr, {"a": {"b"}, "b": {"a"}}, set(), {"a", "b", "c", "d"})
check("a cycle is unbounded, never a number", dp["a"] is None and dp["b"] is None and dp["c"] == 8, dp)
dp, why = S.depth(fr, {"a": {"b"}}, {"b"}, {"a", "b", "c", "d"})
check("an indirect call makes the function and its callers unbounded", dp["b"] is None and dp["a"] is None and "indirect" in why["b"], (dp, why))
dp, why = S.depth(fr, {"a": {"puts"}}, set(), {"a", "b", "c", "d"})
check("a callee with no definition makes the caller unbounded", dp["a"] is None and "undefined" in why["puts"], (dp, why))
dp, why = S.depth({"a": 16, "b": None}, {"a": {"b"}}, set(), {"a", "b"})
check("a function with no frame (dynamic or unmatched) makes its callers unbounded", dp["a"] is None and why["b"] == "noframe", (dp, why))
sys.exit(1 if fails else 0)
