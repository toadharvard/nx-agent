#!/usr/bin/env python3
"""The loader's start rules S1..S8 (loader/include/fnvnx_start.h) as assertions over the boot machine's trace of the REAL image.

  bootstart.py request --exe FalloutNV.exe --iat iat.tsv --watch-out W.txt --state-out S.json
      the two observation requests the exe's own PE and the contract's iat.tsv imply: the --watch-log file (fnvnx_lift_enter with the
      memory it needs) and the --state-request file (hashes of every page of the image range, dumps of the import slots and the TLS
      index word); run boot.py with  --watch-log W.txt --state-at fnvnx_start_enter --state-request S.json
  bootstart.py check --out OUT.json --exe FalloutNV.exe --iat iat.tsv --modules modules.tsv --sha256 HEX [--json VERDICT.json]
      one verdict per rule; exit 0 iff every rule is "pass"

A verdict is pass, fail or undecided. fail: the trace contradicts the rule. undecided: the rule's value is not in the supplied facts
(named); it is never turned into a guess, and it is not a pass. Every verdict lists the trace facts it read.

RULE -> OBSERVATION -> FIELD (E existing, N new; all N fields are recorded by the machine from the image's own execution):
  S1 identity        console "fnvnx-start: identity ok" before "image mapped", no "identity mismatch"; sha256 of --exe = --sha256     E console.txt
  S2 PE headers      no "fnvnx-start: PE headers" line, "image mapped" present; the PE parses (sections in rva order inside SizeOfImage) E console.txt
  S3 map             every page of [ImageBase, +SizeOfImage): sha256 = headers / section bytes / zero (min(raw,virt) bytes, rest 0),
                     except the import slots and the TLS index word (S4, S5); every page mapped                                    N start_state.pages, runs
  S4 imports         every slot of iat.tsv holds its value (key or module export); data slots: a mapped RW address; slot set = the PE's  N start_state.dump
  S5 TLS             index word 0; main thread block vec[0] = block, block = template + zero fill at the alignment; callbacks and entry
                     in executable sections                                                                       N start_state.dump, watch_log mem
  S6 protection      every page of the image range: headers R, section R/W by its characteristics, never X, claimed gaps none           N start_state.runs
  S7 thread block    TEB +0 -1, +4 stack base, +8 limit, +0x18 self, +0x2c vector; guard page none, stack RW of SizeOfStackReserve; the
                     TEB, vector, stack outside the reserved set (exe, module rows, [0xf0000, 0x200000))   N watch_log args, mem; start_state.runs
  S8 enter           the first exe-range fnvnx_lift_enter calls on one thread: the TLS callbacks (image_base, 1, 0) in list order, then
                     the entry (0, 0, 0); esp = (stack_base - 16) & ~15 holding the sentinel; console "enter <entry>"       N watch_log args, mem
Rules no observation can decide are listed in the verdicts as undecided, with the reason.
"""
import argparse
import hashlib
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bootinputs

PAGE = 4096
SENTINEL = 0x000FFFF0
RESERVED = (0x000F0000, 0x00200000)
MEM_READ, MEM_WRITE, MEM_EXEC = 0x40000000, 0x80000000, 0x20000000
LIFT, START = "fnvnx_lift_enter", "fnvnx_start_enter"
R, W, X = 1, 2, 4
TEB_HEAD = 0x30


class StartError(ValueError):
    pass


def up(n):
    return (n + PAGE - 1) // PAGE * PAGE


def parse_pe(raw):
    """-> dict of the PE32 facts the rules use (struct only); StartError when the file is not one the loader could open"""
    try:
        if raw[:2] != b"MZ":
            raise StartError("no MZ")
        pe, = struct.unpack_from("<I", raw, 0x3C)
        if raw[pe:pe + 4] != b"PE\0\0":
            raise StartError("no PE signature")
        nsec, = struct.unpack_from("<H", raw, pe + 6)
        osz, = struct.unpack_from("<H", raw, pe + 20)
        o = pe + 24
        if struct.unpack_from("<H", raw, o)[0] != 0x10B:
            raise StartError("not PE32")
        g = lambda off: struct.unpack_from("<I", raw, o + off)[0]  # noqa: E731
        d = {"entry_rva": g(16), "base": g(28), "salign": g(32), "size_of_image": g(56), "size_of_headers": g(60), "stack_reserve": g(72), "sections": []}
        nd = g(92)
        dirs = [struct.unpack_from("<II", raw, o + 96 + 8 * i) for i in range(min(nd, 16))]
        for i in range(nsec):
            s = o + osz + 40 * i
            vs, va, rs, rp = struct.unpack_from("<IIII", raw, s + 8)
            ch, = struct.unpack_from("<I", raw, s + 36)
            d["sections"].append({"name": raw[s:s + 8].rstrip(b"\0").decode("latin1"), "va": va, "vsize": vs or rs, "raw": rs, "ptr": rp, "ch": ch})
    except struct.error:
        raise StartError("truncated headers") from None
    last = 0
    for s in d["sections"]:
        if s["va"] < last or s["va"] % PAGE or s["va"] + s["vsize"] > d["size_of_image"]:
            raise StartError("section %s: not in rva order, not page aligned or outside SizeOfImage" % s["name"])
        last = s["va"] + up(s["vsize"])
    if d["sections"] and d["size_of_headers"] > d["sections"][0]["va"]:
        raise StartError("headers overlap the first section")
    d["import"], d["tls"] = (dirs[1] if len(dirs) > 1 else (0, 0)), (dirs[9] if len(dirs) > 9 else (0, 0))
    d["file"] = raw
    return d


def image_bytes(pe):
    """the mapped image the S3 rule gives, before S4/S5 write: bytes of [base, base + up(SizeOfImage))"""
    raw, img = pe["file"], bytearray(up(pe["size_of_image"]))
    h = raw[:pe["size_of_headers"]]
    img[:len(h)] = h
    for s in pe["sections"]:
        n = min(s["raw"], s["vsize"])
        img[s["va"]:s["va"] + n] = raw[s["ptr"]:s["ptr"] + n]
    return img


def page_owner(pe):
    """-> list per page of the image range: ("hdr",), ("sec", i), ("gap",) or ("shared",) when two sections claim the page"""
    n = up(pe["size_of_image"]) // PAGE
    own = [("gap",)] * n
    for p in range((pe["size_of_headers"] + PAGE - 1) // PAGE):
        own[p] = ("hdr",)
    for i, s in enumerate(pe["sections"]):
        for p in range(s["va"] // PAGE, (s["va"] + up(s["vsize"])) // PAGE):
            own[p] = ("shared",) if own[p] != ("gap",) else ("sec", i)
    return own


def va_to_off(pe, va):
    rva = va - pe["base"]
    for s in pe["sections"]:
        if s["va"] <= rva < s["va"] + s["vsize"]:
            return s
    return None


def read_va(pe, va, n):
    img = pe.setdefault("_img", image_bytes(pe))
    r = va - pe["base"]
    return bytes(img[r:r + n]) if 0 <= r and r + n <= len(img) else None


def tls_dir(pe):
    rva, size = pe["tls"]
    if not rva:
        return None
    b = read_va(pe, pe["base"] + rva, 24)
    if b is None or size < 24:
        raise StartError("TLS directory outside the image or shorter than 24 bytes")
    start, end, idx, cbs, zero, ch = struct.unpack("<6I", b)
    cb = []
    while cbs:
        w = read_va(pe, cbs + 4 * len(cb), 4)
        v = struct.unpack("<I", w)[0] if w else 0
        if not v:
            break
        cb.append(v)
    a = (ch >> 20) & 15
    return {"start": start, "size": end - start, "zero": zero, "index_va": idx, "callbacks": cb, "align": 1 << (a - 1) if a else 1}


def import_slots(pe):
    """the exe's import slots from its own import directory: {slot VA: (dll name, import name or '#ordinal')}"""
    rva, size = pe["import"]
    out = {}
    if not rva:
        return out
    cstr = lambda va: read_va(pe, va, 256).split(b"\0")[0].decode("latin1")  # noqa: E731
    for k in range(0, size or 1 << 20, 20):
        b = read_va(pe, pe["base"] + rva + k, 20)
        if b is None:
            break
        oft, _t, _f, name, ft = struct.unpack("<IIIII", b)
        if not name and not ft:
            break
        i = 0
        while True:
            w = read_va(pe, pe["base"] + (oft or ft) + 4 * i, 4)
            v = struct.unpack("<I", w)[0] if w else 0
            if not v:
                break
            imp = "#%d" % (v & 0xFFFF) if v & 0x80000000 else cstr(pe["base"] + v + 2)
            out[pe["base"] + ft + 4 * i] = (cstr(pe["base"] + name), imp)
            i += 1
    return out


def read_iat(text):
    """iat.tsv (contract/iat.py write_table): slot, dll, name, kind, value -> [(slot, dll, name, kind, value | None)]"""
    rows = []
    for no, line in enumerate(text.split("\n"), 1):
        if not line or line.startswith("#"):
            continue
        f = line.split("\t")
        if len(f) != 5:
            raise StartError("iat.tsv line %d: 5 tab-separated fields expected" % no)
        try:
            rows.append((int(f[0], 16), f[1], f[2], f[3], int(f[4], 16) if f[4] else None))
        except ValueError:
            raise StartError("iat.tsv line %d: not hex" % no) from None
    return rows


def iat_dumps(slots):
    """contiguous slot runs -> [(name, addr, len)]"""
    runs = []
    for s in sorted(slots):
        if runs and runs[-1][0] + runs[-1][1] == s:
            runs[-1][1] += 4
        else:
            runs.append([s, 4])
    return [("iat:%08x" % a, a, n) for a, n in runs]


def request(pe, iat):
    """-> (watch-log text, state-request dict)"""
    specs = ["x1:16", "x2:%d" % TEB_HEAD, "x2->0x2c:4"]
    t = tls_dir(pe)
    if t:
        specs.append("x2->0x2c->0:%d" % (t["size"] + t["zero"]))
    watch = ("# bootstart.py request: the loader's guest entries; mem = [esp] words, the TEB head, TLS vector slot 0, the exe's TLS block, captured up to and\n"
             "# including the call of the exe entry (S8: ImageBase + AddressOfEntryPoint); later calls record args only\n%s mem %s until x0=0x%08x\n" % (
                 LIFT, " ".join(specs), pe["base"] + pe["entry_rva"]))
    dump = [{"name": n, "addr": a, "len": l} for n, a, l in iat_dumps([r[0] for r in iat])]
    if t:
        dump.append({"name": "tls_index", "addr": t["index_va"], "len": 4})
    return watch, {"hash": [{"name": "image", "lo": pe["base"], "hi": pe["base"] + up(pe["size_of_image"])}], "dump": dump}


# ---------------------------------------------------------------------------------------------------------------- checks

class V:
    def __init__(self, rule, title):
        self.rule, self.title, self.reads, self.fail, self.undec = rule, title, [], [], []

    def read(self, *facts):
        self.reads.extend(f for f in facts if f not in self.reads)

    def verdict(self):
        return "fail" if self.fail else ("undecided" if self.undec else "pass")

    def doc(self):
        return {"rule": self.rule, "title": self.title, "verdict": self.verdict(), "reads": self.reads, "fail": self.fail[:20],
                "fail_count": len(self.fail), "undecided": self.undec}


def lines(console):
    out = []
    for ln in console.split("\n"):
        ln = ln.strip()
        if ln.startswith("fnvnx-start: "):
            out.append(ln[len("fnvnx-start: "):])
    return out


FAILURE = ("identity mismatch", "empty executable", "PE headers", "mapping ", "claiming ", "not usable", "TLS", "protecting", "entry point", "imports not bound",
           "stack size", "guest memory request", "cannot read")


def failure_lines(cl):
    return [c for c in cl if any(c.startswith(p) or p in c for p in FAILURE) and not c.startswith("identity ok")]


def runs_prot(runs, lo, hi):
    """per page of [lo, hi) the prot of its run, None when unmapped"""
    out = []
    for a in range(lo, hi, PAGE):
        out.append(next((p for l, h, p in runs if l <= a and a + PAGE <= h), None))
    return out


def snapshot(doc, v):
    st = doc.get("start_state")
    if st is None:
        v.fail.append("OUT.json has no start_state: %s was never entered" % START if "start_state" in doc else
                      "OUT.json has no start_state: the run did not use --state-at %s" % START)
    else:
        v.read("start_state")
    return st


def lift_calls(doc):
    return [r for r in doc.get("watch_log", []) if r.get("kind") == "call" and r.get("sym") == LIFT]


def mem(rec, spec):
    h = (rec.get("mem") or {}).get(spec)
    return bytes.fromhex(h) if h else None


def check(doc, console, pe, expect_sha, iat, modules):
    cl = lines(console)
    sha = hashlib.sha256(pe["file"]).hexdigest()
    base, soi = pe["base"], up(pe["size_of_image"])
    own = page_owner(pe)
    out = []
    # S1
    v = V("S1", "identity")
    v.read("console.txt: identity lines", "sha256(--exe)")
    if sha != expect_sha:
        v.fail.append("sha256 of the exe %s is not the configured %s" % (sha, expect_sha))
    if "identity ok" not in cl:
        v.fail.append('no "fnvnx-start: identity ok"')
    elif "image mapped" in cl and cl.index("identity ok") > cl.index("image mapped"):
        v.fail.append('"identity ok" comes after "image mapped"')
    if any(c.startswith("identity mismatch") for c in cl):
        v.fail.append("the loader reported an identity mismatch")
    out.append(v)
    # S2
    v = V("S2", "PE headers")
    v.read("console.txt: start lines", "PE of --exe")
    if "image mapped" not in cl:
        v.fail.append('no "fnvnx-start: image mapped"')
    bad = failure_lines(cl)
    if bad:
        v.fail.append("the loader logged failure lines: %s" % "; ".join(bad[:5]))
    out.append(v)
    st = None
    # S3
    v = V("S3", "map")
    st = snapshot(doc, v)
    if any(o == ("shared",) for o in own):
        v.undec.append("two sections share a page: the S3 rule gives no value for it")
    if st:
        img = bytearray(image_bytes(pe))
        for name, d in st["dump"].items():
            if name.startswith("iat:") or name == "tls_index":
                if d["hex"] is None:
                    v.fail.append("dump %s: unmapped" % name)
                else:
                    r = d["addr"] - base
                    img[r:r + len(d["hex"]) // 2] = bytes.fromhex(d["hex"])   # S4/S5 own these words: their value is checked there
        hs = st["pages"].get("image")
        v.read("start_state.pages.image", "start_state.dump (the S4/S5 words)")
        if not hs or hs["lo"] != base or len(hs["sha256"]) != soi // PAGE:
            v.fail.append("the page hashes do not cover [0x%08x, +0x%x)" % (base, soi))
        else:
            for i, h in enumerate(hs["sha256"]):
                want = hashlib.sha256(bytes(img[i * PAGE:(i + 1) * PAGE])).hexdigest()
                if h is None:
                    v.fail.append("page 0x%08x is not mapped" % (base + i * PAGE))
                elif h != want:
                    v.fail.append("page 0x%08x (%s) differs from the file's bytes" % (base + i * PAGE, own[i][0] if own[i][0] != "sec" else pe["sections"][own[i][1]]["name"]))
    out.append(v)
    # S4
    v = V("S4", "imports")
    v.read("iat.tsv", "PE import directory")
    ps = import_slots(pe)
    if set(ps) != {r[0] for r in iat}:
        miss = sorted(set(ps) ^ {r[0] for r in iat})
        v.fail.append("slots of the exe's import directory and iat.tsv differ at %d slots (first 0x%08x)" % (len(miss), miss[0]))
    if st:
        v.read("start_state.dump iat:*", "start_state.runs")
        words = {}
        for name, d in st["dump"].items():
            if name.startswith("iat:") and d["hex"] is not None:
                for k in range(0, len(d["hex"]) // 8):
                    words[d["addr"] + 4 * k] = int.from_bytes(bytes.fromhex(d["hex"][8 * k:8 * k + 8]), "little")
        ndata = 0
        for slot, dll, name, kind, val in iat:
            got = words.get(slot)
            if got is None:
                v.fail.append("slot 0x%08x (%s!%s): not dumped" % (slot, dll, name))
            elif kind == "data":
                ndata += 1
                pr = runs_prot(st["runs"], got & ~(PAGE - 1), (got & ~(PAGE - 1)) + PAGE) if got else [None]
                if pr[0] is None or pr[0] & (R | W) != (R | W):
                    v.fail.append("data slot 0x%08x (%s!%s) = 0x%08x is not a mapped read-write address" % (slot, dll, name, got))
            elif val is None or got != val:
                v.fail.append("slot 0x%08x (%s!%s) = 0x%08x, rule value %s" % (slot, dll, name, got, "none" if val is None else "0x%08x" % val))
        if ndata:
            v.undec.append("%d data slots: the rule gives the address of a native object bound by the loader; only 'mapped read-write' is observable" % ndata)
    out.append(v)
    # S5
    v = V("S5", "TLS")
    t = tls_dir(pe)
    v.read("PE TLS directory")
    calls = lift_calls(doc)
    if st and t:
        d = st["dump"].get("tls_index")
        v.read("start_state.dump tls_index")
        if not d or d["hex"] is None or int.from_bytes(bytes.fromhex(d["hex"]), "little") != 0:
            v.fail.append("the exe's TLS index word is not 0: %s" % (d and d["hex"]))
    if t:
        for cb in t["callbacks"] + [base + pe["entry_rva"]]:
            s = va_to_off(pe, cb)
            if s is None or not s["ch"] & MEM_EXEC:
                v.fail.append("0x%08x is not in an executable section" % cb)
        s = va_to_off(pe, t["start"])
        if s is None or not s["ch"] & MEM_READ:
            v.fail.append("the TLS template is not in a readable section")
        if t["align"] > PAGE:
            v.fail.append("TLS alignment above a page")
    if not calls:
        v.fail.append("no %s record in watch_log: the main thread block cannot be seen" % LIFT)
    else:
        c0 = calls[0]
        v.read("watch_log %s mem" % LIFT)
        vec0 = mem(c0, "x2->0x2c:4")
        got = int.from_bytes(vec0, "little") if vec0 else None
        if t:
            blk = mem(c0, "x2->0x2c->0:%d" % (t["size"] + t["zero"]))
            tmpl = read_va(pe, t["start"], t["size"])
            if got is None or got % t["align"] or blk is None:
                v.fail.append("vector slot 0 = %s: not a block address at the alignment, or the block is unmapped" % (got is not None and "0x%08x" % got))
            elif blk != tmpl + bytes(t["zero"]):
                v.fail.append("the main thread's TLS block is not the template followed by %d zero bytes" % t["zero"])
        elif got != 0:
            v.fail.append("the image has no TLS directory and vector slot 0 is %s, not 0" % got)
    out.append(v)
    # S6
    v = V("S6", "protection")
    if st:
        v.read("start_state.runs")
        pr = runs_prot(st["runs"], base, base + soi)
        for i, p in enumerate(pr):
            o = own[i]
            if o == ("shared",):
                continue
            want = R if o == ("hdr",) else (0 if o == ("gap",) else ((R if pe["sections"][o[1]]["ch"] & MEM_READ else 0) | (W if pe["sections"][o[1]]["ch"] & MEM_WRITE else 0)))
            if p is None:
                v.fail.append("page 0x%08x is not mapped" % (base + i * PAGE))
            elif p != want:
                v.fail.append("page 0x%08x (%s): prot %d, rule %d" % (base + i * PAGE, o[0] if o[0] != "sec" else pe["sections"][o[1]]["name"], p, want))
    if any(o == ("shared",) for o in own):
        v.undec.append("two sections share a page: the S6 rule gives no value for it")
    out.append(v)
    # S7
    v = V("S7", "thread block")
    if calls and st:
        c0 = calls[0]
        v.read("watch_log %s args, mem" % LIFT, "start_state.runs", "modules.tsv")
        teb = c0["args"][2]
        head = mem(c0, "x2:%d" % TEB_HEAD)
        if head is None or len(head) < TEB_HEAD:
            v.fail.append("the TEB at 0x%08x is not readable" % teb)
        else:
            w = lambda o: struct.unpack_from("<I", head, o)[0]  # noqa: E731
            base_, lim, vec = w(4), w(8), w(0x2C)
            if w(0) != 0xFFFFFFFF:
                v.fail.append("TEB+0 = 0x%08x, rule 0xffffffff" % w(0))
            if w(0x18) != teb:
                v.fail.append("TEB+0x18 = 0x%08x, rule the TEB itself 0x%08x" % (w(0x18), teb))
            if teb % PAGE or lim % PAGE or base_ % PAGE:
                v.fail.append("teb / stack limit / stack base not page aligned")
            if base_ - lim != up(pe["stack_reserve"]):
                v.fail.append("stack size 0x%x, rule SizeOfStackReserve rounded up 0x%x" % (base_ - lim, up(pe["stack_reserve"])))
            for what, lo, hi, want in (("guard", lim - PAGE, lim, 0), ("stack", lim, base_, R | W), ("TEB", teb, teb + PAGE, R | W), ("TLS vector", vec, vec + PAGE, R | W)):
                got = runs_prot(st["runs"], lo & ~(PAGE - 1), up(hi))
                if any(p != want for p in got):
                    v.fail.append("%s [0x%08x, 0x%08x): prot %s, rule %d" % (what, lo, hi, sorted(set(got), key=str), want))
            res = [(a, b) for _n, a, b in modules] + [RESERVED]
            for what, lo, hi in (("guest stack with guard", lim - PAGE, base_), ("TEB", teb, teb + PAGE), ("TLS vector", vec, vec + PAGE)):
                if any(lo < b and a < hi for a, b in res):
                    v.fail.append("%s [0x%08x, 0x%08x) intersects the reserved set" % (what, lo, hi))
        tebs = {r["args"][2] for r in calls if r["tid"] == c0["tid"]}
        if tebs != {teb}:
            v.fail.append("the first thread passes more than one TEB to %s: %s" % (LIFT, sorted("0x%08x" % t for t in tebs)))
    elif not calls:
        v.fail.append("no %s record in watch_log" % LIFT)
    out.append(v)
    # S8
    v = V("S8", "enter")
    v.read("watch_log %s args, mem" % LIFT, "console.txt: enter line", "start_state")
    entry = base + pe["entry_rva"]
    cbs = (t["callbacks"] if t else [])
    exe_calls = [r for r in calls if base <= r["args"][0] < base + soi]
    want = [(cb, [base, 1, 0]) for cb in cbs] + [(entry, [0, 0, 0])]
    if "enter %08x" % entry not in cl:
        v.fail.append('no console line "fnvnx-start: enter %08x"' % entry)
    if st is None:
        pass
    if len(exe_calls) < len(want):
        v.fail.append("%d exe-range %s calls, the rule needs %d (the TLS callbacks, then the entry)" % (len(exe_calls), LIFT, len(want)))
    else:
        first = exe_calls[:len(want)]
        for r, (eip, args) in zip(first, want):
            m = mem(r, "x1:16")
            esp = r["args"][1]
            words = list(struct.unpack("<4I", m)) if m and len(m) == 16 else None
            if r["args"][0] != eip:
                v.fail.append("call to 0x%08x where the rule has 0x%08x" % (r["args"][0], eip))
            if words != [SENTINEL] + args:
                v.fail.append("call 0x%08x: [esp..] = %s, rule %s" % (eip, words and ["0x%08x" % x for x in words], ["0x%08x" % x for x in [SENTINEL] + args]))
            if esp % 16:
                v.fail.append("call 0x%08x: esp 0x%08x not 16-aligned" % (eip, esp))
        if len({r["tid"] for r in first}) != 1 or len({r["args"][2] for r in first}) != 1:
            v.fail.append("the start calls are not all on one thread with one TEB")
        c0 = calls[0]
        head = mem(c0, "x2:%d" % TEB_HEAD)
        if head and len(head) == TEB_HEAD:
            sb = struct.unpack_from("<I", head, 4)[0]
            for r in first:
                if r["args"][1] != (sb - 16) & ~15 & 0xFFFFFFFF:
                    v.fail.append("call 0x%08x: esp 0x%08x, rule (stack_base - 16) & ~15 = 0x%08x" % (r["args"][0], r["args"][1], (sb - 16) & ~15))
    out.append(v)
    return out


def load_modules(path, exe_raw):
    with open(path, encoding="utf-8") as f:
        rows = bootinputs.reserved_rows(f.read(), exe_raw)
    return [r for r in rows if r[0] != "exe"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("request")
    for o in ("--exe", "--iat", "--watch-out", "--state-out"):
        q.add_argument(o, required=True)
    c = sub.add_parser("check")
    for o in ("--out", "--exe", "--iat", "--modules", "--sha256"):
        c.add_argument(o, required=True)
    c.add_argument("--console", help="default: OUT's .console.txt")
    c.add_argument("--json", help="write the verdicts here")
    a = ap.parse_args(argv)
    try:
        with open(a.exe, "rb") as f:
            raw = f.read()
        pe = parse_pe(raw)
        with open(a.iat, encoding="utf-8") as f:
            iat = read_iat(f.read())
        if a.cmd == "request":
            w, s = request(pe, iat)
            with open(a.watch_out, "w") as f:
                f.write(w)
            with open(a.state_out, "w") as f:
                f.write(json.dumps(s, indent=1, sort_keys=True) + "\n")
            return 0
        with open(a.out) as f:
            doc = json.load(f)
        with open(a.console or os.path.splitext(a.out)[0] + ".console.txt", encoding="utf-8") as f:
            console = f.read()
        res = check(doc, console, pe, a.sha256, iat, load_modules(a.modules, raw))
    except (StartError, bootinputs.InputError, OSError, ValueError) as e:
        print("bootstart: %s" % e, file=sys.stderr)
        return 1
    docs = [r.doc() for r in res]
    for d in docs:
        print("%s %-12s %-9s fail=%d undecided=%d  reads: %s" % (d["rule"], d["title"], d["verdict"], d["fail_count"], len(d["undecided"]), "; ".join(d["reads"])))
        for m in d["fail"][:5] + d["undecided"]:
            print("    " + m)
    if a.json:
        with open(a.json, "w") as f:
            f.write(json.dumps(docs, indent=1, sort_keys=True) + "\n")
    return 0 if all(d["verdict"] == "pass" for d in docs) else 2


if __name__ == "__main__":
    sys.exit(main())
