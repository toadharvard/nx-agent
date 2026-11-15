#!/usr/bin/env python3
"""Tests of verify/bootstart.py: the loader's start rules S1..S8 as assertions over a boot trace. Hand-built trace documents of a hand-laid
tiny PE (memory laid out page by page here, not by the checker's functions); one pass and at least one fail per rule. Exit 0 on pass."""
import copy
import hashlib
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import boot  # noqa: E402
import bootstart as bs  # noqa: E402

FAILS = []
BASE = 0x400000
PAGE = 4096
STACK_LIM, STACK_BASE, TEB, VEC = 0x20001000, 0x20003000, 0x20003000, 0x20004000
BLOCK = VEC + 0x10


def case(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + str(detail)))
    if not ok:
        FAILS.append(name)


def make_exe():
    f = bytearray(0x600)
    f[:2] = b"MZ"
    struct.pack_into("<I", f, 0x3C, 0x80)
    f[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", f, 0x84, 0x14C, 2)
    struct.pack_into("<H", f, 0x80 + 20, 0xE0)
    o = 0x98
    struct.pack_into("<H", f, o, 0x10B)
    struct.pack_into("<I", f, o + 16, 0x1000)       # entry rva
    struct.pack_into("<I", f, o + 28, BASE)
    struct.pack_into("<II", f, o + 32, 0x1000, 0x200)
    struct.pack_into("<I", f, o + 56, 0x4000)       # SizeOfImage
    struct.pack_into("<I", f, o + 60, 0x200)        # SizeOfHeaders
    struct.pack_into("<I", f, o + 72, 0x2000)       # SizeOfStackReserve
    struct.pack_into("<I", f, o + 92, 16)
    struct.pack_into("<II", f, o + 96 + 8, 0x2000, 40)         # import dir
    struct.pack_into("<II", f, o + 96 + 72, 0x20C0, 24)        # tls dir
    sec = 0x98 + 0xE0
    f[sec:sec + 8] = b".text\0\0\0"
    struct.pack_into("<IIIIIIHHI", f, sec + 8, 0x200, 0x1000, 0x200, 0x200, 0, 0, 0, 0, 0x60000020)
    f[sec + 40:sec + 48] = b".data\0\0\0"
    struct.pack_into("<IIIIIIHHI", f, sec + 48, 0x1000, 0x2000, 0x200, 0x400, 0, 0, 0, 0, 0xC0000040)
    for i in range(0x200):
        f[0x200 + i] = (i * 7 + 1) & 0xFF            # .text
    d = 0x400
    struct.pack_into("<IIIII", f, d, 0x2040, 0, 0, 0x20A0, 0x2060)             # descriptor: OFT, -, -, name, FT
    struct.pack_into("<III", f, d + 0x40, 0x2080, 0x80000005, 0)
    struct.pack_into("<III", f, d + 0x60, 0x2080, 0x80000005, 0)
    f[d + 0x80:d + 0x88] = b"\0\0Foo\0\0\0"
    f[d + 0xA0:d + 0xAD] = b"KERNEL32.dll\0"
    struct.pack_into("<6I", f, d + 0xC0, 0x402100, 0x402108, 0x4020E0, 0x4020F0, 8, 0x00300000)
    struct.pack_into("<I", f, d + 0xE0, 0x11223344)  # the index word in the file: not 0
    struct.pack_into("<II", f, d + 0xF0, 0x401010, 0)
    f[d + 0x100:d + 0x108] = b"TEMPLATE"
    return bytes(f)


EXE = make_exe()
IAT = [(0x402060, "KERNEL32.dll", "Foo", "key", 0x000F0010), (0x402064, "KERNEL32.dll", "Bar", "key", 0x000F0020)]
MODS = [("m.dll", 0x10000000, 0x10010000)]
# the contract's facts: modules.tsv (m.dll: static, load index 1, has TLS) and tls.tsv + tls/ (the exe row first, then m.dll's)
MF = {"m.dll": {"pref": 0x10000000, "base": 0x10000000, "soi": 0x10000, "tls": True, "static": True, "idx": 1}}
DLLT = b"DLLT"
TLS = [{"name": "FalloutNV.exe", "idx": None, "start": 0x402100, "end": 0x402108, "index_va": 0x4020E0, "cb_va": 0x4020F0, "rva": 0x2100, "size": 8, "zero": 8,
        "align_f": 3, "sha": hashlib.sha256(b"TEMPLATE").hexdigest(), "callbacks": [0x1010], "bin": b"TEMPLATE"},
       {"name": "m.dll", "idx": 1, "start": 0x10003000, "end": 0x10003004, "index_va": 0x10002000, "cb_va": 0, "rva": 0x3000, "size": 4, "zero": 4,
        "align_f": 0, "sha": hashlib.sha256(DLLT).hexdigest(), "callbacks": [], "bin": DLLT}]
BLOCK2 = BLOCK + 0x10
SHA = hashlib.sha256(EXE).hexdigest()


def make_sym_elf(syms, table_id):
    """a minimal ELF32 with one PT_LOAD (vaddr 0x1000, file offset 0x100) holding fnvnx_import_table_id, and a .symtab/.strtab"""
    syms = dict(syms, fnvnx_import_table_id=0x1000)
    names = b"\0" + b"".join(n.encode() + b"\0" for n in sorted(syms))
    st = bytearray(16)
    off = 1
    for n in sorted(syms):
        st += struct.pack("<IIIBBH", off, syms[n], 0, 0x11, 0, 1)
        off += len(n) + 1
    f = bytearray(0x600)
    f[:16] = b"\x7fELF\1\1\1" + bytes(9)
    struct.pack_into("<HHIIIIIHHHHHH", f, 16, 3, 183, 1, 0, 52, 0x500, 0, 52, 32, 1, 40, 3, 0)
    struct.pack_into("<8I", f, 52, 1, 0x100, 0x1000, 0x1000, 0x80, 0x80, 5, 0x1000)
    f[0x100:0x100 + 64] = table_id.encode()
    f[0x200:0x200 + len(st)] = st
    f[0x400:0x400 + len(names)] = names
    struct.pack_into("<10I", f, 0x500 + 40, 0, 2, 0, 0, 0x200, len(st), 2, 0, 4, 16)
    struct.pack_into("<10I", f, 0x500 + 80, 0, 3, 0, 0, 0x400, len(names), 0, 0, 1, 0)
    return bytes(f)


def pages():
    """the memory the loader leaves, laid by hand: page address -> 4096 bytes"""
    hdr = EXE[:0x200].ljust(PAGE, b"\0")
    text = EXE[0x200:0x400].ljust(PAGE, b"\0")
    data = bytearray(EXE[0x400:0x600].ljust(PAGE, b"\0"))
    struct.pack_into("<II", data, 0x60, 0x000F0010, 0x000F0020)   # S4
    struct.pack_into("<I", data, 0xE0, 0)                          # S5
    return {BASE: hdr, BASE + 0x1000: text, BASE + 0x2000: bytes(data), BASE + 0x3000: bytes(PAGE)}


def good():
    pg = pages()
    esp = (STACK_BASE - 16) & ~15
    head = bytearray(0x30)
    struct.pack_into("<IIIII", head, 0, 0xFFFFFFFF, STACK_BASE, STACK_LIM, 0, 0)
    struct.pack_into("<I", head, 0x18, TEB)
    struct.pack_into("<I", head, 0x2C, VEC)
    stack = lambda *w: struct.pack("<4I", *w).hex()  # noqa: E731
    mem = lambda w: {"x1:16": stack(*w), "x2:48": bytes(head).hex(), "x2->0x2c:4": struct.pack("<I", BLOCK).hex(),
                     "x2->0x2c:8": struct.pack("<II", BLOCK, BLOCK2).hex(), "x2->0x2c->0:16": (b"TEMPLATE" + bytes(8)).hex(),
                     "x2->0x2c->4:8": (DLLT + bytes(4)).hex()}
    calls = [(0x10001000, [0x10000000, 1, 1]), (0x401010, [BASE, 1, 0]), (0x401000, [0, 0, 0])]
    wl = [{"seq": i, "tid": 1, "t_ns": i, "kind": "call", "sym": bs.LIFT, "pc": 0, "sp": 0, "x0": eip, "x30": 0, "args": [eip, esp, TEB, 0],
           "mem": mem([bs.SENTINEL] + a)} for i, (eip, a) in enumerate(calls)]
    runs = [[BASE, BASE + 0x1000, 1], [BASE + 0x1000, BASE + 0x2000, 1], [BASE + 0x2000, BASE + 0x3000, 3], [BASE + 0x3000, BASE + 0x4000, 0],
            [STACK_LIM - PAGE, STACK_LIM, 0], [STACK_LIM, TEB + 0x2000, 3]]
    dump = {"iat:00402060": {"addr": 0x402060, "hex": struct.pack("<II", 0x000F0010, 0x000F0020).hex()},
            "tls_index": {"addr": 0x4020E0, "hex": "00000000"}, "tls_index:m.dll": {"addr": 0x10002000, "hex": "01000000"}}
    st = {"at": bs.START, "t_ns": 0, "regs": {}, "runs": runs, "dump": dump,
          "pages": {"image": {"lo": BASE, "sha256": [hashlib.sha256(pg[a]).hexdigest() for a in sorted(pg)]}}}
    console = "   fnvnx-start: identity ok\n   fnvnx-start: image mapped\n   fnvnx-start: imports bound 2\n   fnvnx-start: enter 00401000\n"
    return {"start_state": st, "watch_log": wl}, console


def run(doc, console, exe=EXE, sha=SHA, iat=IAT, mods=MODS, mf=MF, tls=TLS, iat_data=None, elf=None):
    return {v.rule: v for v in bs.check(doc, console, bs.parse_pe(exe), sha, iat, mods, mf, tls, iat_data, elf)}


def main():
    pe = bs.parse_pe(EXE)
    case("parse: base, entry, stack, 2 sections", (pe["base"], pe["entry_rva"], pe["stack_reserve"], len(pe["sections"])) == (BASE, 0x1000, 0x2000, 2))
    case("import slots read from the PE's own directory", bs.import_slots(pe) == {0x402060: ("KERNEL32.dll", "Foo"), 0x402064: ("KERNEL32.dll", "#5")}, bs.import_slots(pe))
    t = bs.tls_dir(pe)
    case("tls directory: template 8, zero 8, align 4, callbacks [0x401010]", (t["size"], t["zero"], t["align"], t["callbacks"]) == (8, 8, 4, [0x401010]), t)
    case("request: iat run dumped, tls index dumped, lift_enter watch with the block spec",
         bs.request(pe, IAT, MF, TLS)[1]["dump"] == [{"name": "iat:00402060", "addr": 0x402060, "len": 8}, {"name": "tls_index", "addr": 0x4020E0, "len": 4},
                                                     {"name": "tls_index:m.dll", "addr": 0x10002000, "len": 4}]
         and "x2->0x2c->0:16" in bs.request(pe, IAT, MF, TLS)[0] and "x2->0x2c->4:8" in bs.request(pe, IAT, MF, TLS)[0] and "x2->0x2c:8" in bs.request(pe, IAT, MF, TLS)[0],
         bs.request(pe, IAT, MF, TLS))
    case("the longest memory spec is boot.py's", bs.MEM_MAX == boot.MEM_MAX)
    import tempfile
    with tempfile.TemporaryDirectory() as td0:
        class Sy(dict):
            funcs = {bs.LIFT}
        wf0 = os.path.join(td0, "w.txt")
        open(wf0, "w").write(bs.request(pe, IAT, MF, TLS)[0])
        rq = boot._watch_names(wf0, Sy({bs.LIFT: 1}))
        case("the generated watch file parses in boot.py's grammar, with the until of the entry", rq[0] == [bs.LIFT] and len(rq[2][bs.LIFT]) == 6 and rq[3] == {bs.LIFT: (0, 0x401000)}, rq)
    case("request: mem capture ends at the entry call (until x0 = ImageBase + entry RVA)", bs.request(pe, IAT, MF, TLS)[0].rstrip().endswith("until x0=0x00401000"))
    d, c = good()
    r = run(d, c)
    case("good trace: every rule passes", all(v.verdict() == "pass" for v in r.values()), {k: (v.fail, v.undec) for k, v in r.items() if v.verdict() != "pass"})
    case("every verdict names what it read", all(v.reads for v in r.values()))

    def only(rule, d, c, exe=EXE, **kw):
        r = run(d, c, exe=exe, **kw)
        return r[rule].verdict(), sorted(k for k, v in r.items() if v.verdict() == "fail")

    # S1
    case("S1 fail: wrong configured sha", only("S1", *good(), sha="0" * 64)[0] == "fail")
    case("S1 fail: no identity ok line", only("S1", good()[0], "   fnvnx-start: image mapped\n")[0] == "fail")
    case("S1 fail: loader reports a mismatch", only("S1", good()[0], good()[1] + "   fnvnx-start: identity mismatch: x\n")[0] == "fail")
    # S2
    case("S2 fail: PE headers error line, no image mapped", only("S2", good()[0], "   fnvnx-start: identity ok\n   fnvnx-start: PE headers: bad\n")[0] == "fail")
    # S3
    d, c = good()
    d["start_state"]["pages"]["image"]["sha256"][1] = "00" * 32
    case("S3 fail: the .text page differs from the file", only("S3", d, c) == ("fail", ["S3"]), only("S3", d, c))
    d, c = good()
    d["start_state"]["pages"]["image"]["sha256"][3] = hashlib.sha256(b"x" * PAGE).hexdigest()
    case("S3 fail: the claimed gap page is not zero", only("S3", d, c)[0] == "fail")
    d, c = good()
    d["start_state"]["pages"]["image"]["sha256"][2] = None
    case("S3 fail: a page is not mapped", only("S3", d, c)[0] == "fail")
    d, c = good()
    d["start_state"]["dump"]["iat:00402060"]["hex"] = struct.pack("<II", 1, 2).hex()
    case("S3 fail: the import page no longer matches once the slots are taken as dumped", only("S3", d, c)[0] == "fail")
    d, c = good()
    del d["start_state"]
    case("S3 fail: no start_state", only("S3", d, c)[0] == "fail")
    # S4
    d, c = good()
    d["start_state"]["dump"]["iat:00402060"]["hex"] = struct.pack("<II", 0x000F0010, 0x000F0030).hex()
    case("S4 fail: a slot holds another key", only("S4", d, c)[0] == "fail")
    case("S4 fail: iat.tsv misses a slot of the PE", only("S4", *good(), iat=IAT[:1])[0] == "fail")
    d, c = good()
    # S4 data slots: the ELF address of the native_symbol, table_id equal to the ELF's
    import tempfile
    TID = "ab" * 32
    with tempfile.TemporaryDirectory() as td:
        ep = os.path.join(td, "g.elf")
        open(ep, "wb").write(make_sym_elf({"fnvnx_obj": 0x2000}, TID))
        elf = bs.read_elf(ep)
        DATA = [IAT[0], (0x402064, "KERNEL32.dll", "Bar", "data", None)]
        IBASE = 0x30000000
        data = lambda tid=TID, sym="fnvnx_obj", key=("KERNEL32.dll", "Bar"): (tid, {key: sym})  # noqa: E731
        d, c = good()
        d["plan"] = {"image": {"base": IBASE}}
        d["start_state"]["dump"]["iat:00402060"]["hex"] = struct.pack("<II", 0x000F0010, IBASE + 0x2000).hex()
        v = run(d, c, iat=DATA, iat_data=data(), elf=elf)["S4"]
        case("S4 pass: a data slot holds the ELF address of its native_symbol (no undecided)", v.verdict() == "pass", (v.fail, v.undec))
        d["start_state"]["dump"]["iat:00402060"]["hex"] = struct.pack("<II", 0x000F0010, IBASE + 0x2004).hex()
        case("S4 fail: a data slot holds another address", run(d, c, iat=DATA, iat_data=data(), elf=elf)["S4"].verdict() == "fail")
        d["start_state"]["dump"]["iat:00402060"]["hex"] = struct.pack("<II", 0x000F0010, IBASE + 0x2000).hex()
        case("S4 fail: iat_data.tsv's table_id is not the ELF's", run(d, c, iat=DATA, iat_data=data(tid="cd" * 32), elf=elf)["S4"].verdict() == "fail")
        case("S4 fail: a data slot has no row in iat_data.tsv", run(d, c, iat=DATA, iat_data=data(key=("KERNEL32.dll", "Baz")), elf=elf)["S4"].verdict() == "fail")
        case("S4 fail: the native_symbol is not in the ELF", run(d, c, iat=DATA, iat_data=data(sym="nothere"), elf=elf)["S4"].verdict() == "fail")
        for what, kw in (("iat_data", {"elf": elf}), ("elf", {"iat_data": data()})):
            try:
                run(d, c, iat=DATA, **kw)
                case("S4: a data slot without --%s is an input error" % what.replace("_", "-"), False)
            except bs.StartError:
                case("S4: a data slot without --%s is an input error" % what.replace("_", "-"), True)
        case("S4 without data slots needs neither", run(*good())["S4"].verdict() == "pass")
        di = bs.read_iat_data("# table_id %s\nKERNEL32.dll\tBar\t12\tdata\tfnvnx_obj\n" % TID)
        case("read_iat_data: table_id and rows", di == (TID, {("KERNEL32.dll", "Bar"): "fnvnx_obj"}), di)
        case("read_elf/elf_cstr: the table id string and the symbol", bs.elf_cstr(elf, "fnvnx_import_table_id", 64) == TID and elf["syms"]["fnvnx_obj"] == 0x2000)
    # CLI: request writes both files; --tls is required when an image has a TLS directory
    with tempfile.TemporaryDirectory() as td:
        P = lambda n: os.path.join(td, n)  # noqa: E731
        open(P("exe"), "wb").write(EXE)
        open(P("iat.tsv"), "w").write("".join("%08x\t%s\t%s\t%s\t%s\n" % (a, d_, n, k, "" if v is None else "%08x" % v) for a, d_, n, k, v in IAT))
        open(P("modules.tsv"), "w").write("# name\tsha256\tsize\tpreferred_base\tassigned_base\tsize_of_image\thas_tls\tentry_rva\tstatic\tload_index\n"
                                          "m.dll\t%s\t100\t10000000\t10000000\t00010000\t1\t00001000\t1\t1\n" % ("0" * 64))
        os.makedirs(P("tls"))
        open(P("tls/FalloutNV.exe.bin"), "wb").write(b"TEMPLATE")
        open(P("tls/m.dll.bin"), "wb").write(DLLT)
        open(P("tls.tsv"), "w").write("# header\n" + "".join("%s\t%s\t%08x\t%08x\t%08x\t%08x\t%08x\t%d\t%d\t%d\t%s\t%s\n" % (
            r["name"], "" if r["idx"] is None else r["idx"], r["start"], r["end"], r["index_va"], r["cb_va"], r["rva"], r["size"], r["zero"], r["align_f"], r["sha"],
            ";".join("%08x" % c for c in r["callbacks"])) for r in TLS))
        base = ["--exe", P("exe"), "--iat", P("iat.tsv"), "--modules", P("modules.tsv")]
        out = ["--watch-out", P("w.txt"), "--state-out", P("s.json")]
        rc = bs.main(["request"] + base + ["--tls", P("tls.tsv")] + out)
        case("CLI request with --tls: exit 0, the DLL's index word is dumped",
             rc == 0 and "tls_index:m.dll" in open(P("s.json")).read() and "x2->0x2c->4:8" in open(P("w.txt")).read(), rc)
        import io
        import contextlib
        with contextlib.redirect_stderr(io.StringIO()) as se:
            rc2 = bs.main(["request"] + base + out)
        case("CLI request without --tls: exit 1 naming --tls", rc2 == 1 and "--tls" in se.getvalue(), (rc2, se.getvalue()))
        rd = bs.read_tls(open(P("tls.tsv")).read(), P("tls"))
        case("read_tls: the rows of tls.tsv with their templates", [r["name"] for r in rd] == ["FalloutNV.exe", "m.dll"] and rd[1]["bin"] == DLLT and rd[0]["idx"] is None
             and rd[1]["idx"] == 1 and rd[0]["callbacks"] == [0x1010], rd)
        case("read_modules: the load index, bases and has_tls", bs.read_modules(open(P("modules.tsv")).read())["m.dll"] == MF["m.dll"], bs.read_modules(open(P("modules.tsv")).read()))
    # blocks over 64 KiB: captured whole, in chunks; pass and fail, never undecided for size
    big = bytes((i * 31 + 7) & 0xFF for i in range(70000))
    TB = [TLS[0], dict(TLS[1], size=70000, end=0x10003000 + 70000, zero=100, sha=hashlib.sha256(big).hexdigest(), bin=big)]
    obs, errs = bs.tls_plan(bs.parse_pe(EXE), TB, MF)
    ob = [o for o in obs if o["name"] == "m.dll"][0]
    sp = bs.block_specs(ob)
    case("a block over 64 KiB is captured by chunk specs of <= MEM_MAX with growing +DELTA", errs == [] and len(sp) == 2 and sp[0] == "x2->0x2c->4:65536" and sp[1] == "x2->0x2c->4+65536:4564", (errs, sp))
    case("request lists every chunk", all(x in bs.request(bs.parse_pe(EXE), IAT, MF, TB)[0] for x in sp))
    want = big + bytes(100)

    def bigdoc(mut=None, drop=None):
        d, c = good()
        for k, x in enumerate(sp):
            d["watch_log"][0]["mem"][x] = want[k * 65536:k * 65536 + 65536][:4564 if k else 65536].hex() if mut is None or k != mut else (
                bytes([want[k * 65536] ^ 1]) + want[k * 65536 + 1:k * 65536 + 4564]).hex()
        if drop is not None:
            d["watch_log"][0]["mem"][sp[drop]] = None
        return d, c
    d, c = bigdoc()
    v = run(d, c, tls=TB)["S5"]
    case("S5 pass: a block over 64 KiB equal to template + zero fill (no undecided)", v.verdict() == "pass", (v.fail, v.undec))
    d, c = bigdoc(mut=1)
    case("S5 fail: a byte beyond 64 KiB differs", run(d, c, tls=TB)["S5"].verdict() == "fail")
    d, c = bigdoc(drop=1)
    case("S5 fail: the second chunk is unmapped", run(d, c, tls=TB)["S5"].verdict() == "fail")
    # S5
    d, c = good()
    d["start_state"]["dump"]["tls_index"]["hex"] = "01000000"
    case("S5 fail: the exe's TLS index not 0", only("S5", d, c)[0] == "fail")
    d, c = good()
    d["start_state"]["dump"]["tls_index:m.dll"]["hex"] = "02000000"
    case("S5 fail: a DLL's index word is not its load index", only("S5", d, c)[0] == "fail")
    d, c = good()
    d["watch_log"][0]["mem"]["x2->0x2c->4:8"] = (DLLT + b"\1" + bytes(3)).hex()
    case("S5 fail: a DLL's block differs from its template + zero fill", only("S5", d, c)[0] == "fail")
    d, c = good()
    d["watch_log"][0]["mem"]["x2->0x2c:8"] = struct.pack("<II", BLOCK, 0).hex()
    case("S5 fail: a DLL's vector slot is empty (no block)", only("S5", d, c)[0] == "fail")
    d, c = good()
    del d["watch_log"][0]["mem"]["x2->0x2c->4:8"]
    case("S5 fail: a DLL's block is unmapped", only("S5", d, c)[0] == "fail")
    d, c = good()
    case("S5 fail: the DLL rows of tls.tsv differ from modules.tsv", run(d, c, tls=TLS[:1])["S5"].verdict() == "fail")
    case("S5 fail: a tls/<name>.bin that does not match template_sha256", run(d, c, tls=[TLS[0], dict(TLS[1], bin=b"DLLX")])["S5"].verdict() == "fail")
    case("S5 fail: tls.tsv's exe row disagrees with the PE", run(d, c, tls=[dict(TLS[0], zero=7), TLS[1]])["S5"].verdict() == "fail")
    case("S5 fail: two images share a vector slot", run(d, c, tls=[TLS[0], dict(TLS[1], idx=0)], mf={"m.dll": dict(MF["m.dll"], idx=0)})["S5"].verdict() == "fail")
    case("S5 fail: a load_index that is not modules.tsv's", run(d, c, tls=[TLS[0], dict(TLS[1], idx=2)])["S5"].verdict() == "fail")
    try:
        run(d, c, tls=None)
        case("S5: tls.tsv is required when an image has a TLS directory", False)
    except bs.StartError:
        case("S5: tls.tsv is required when an image has a TLS directory", True)
    d, c = good()
    d["watch_log"][0]["mem"]["x2->0x2c->0:16"] = (b"TEMPLATX" + bytes(8)).hex()
    case("S5 fail: block differs from the template", only("S5", d, c)[0] == "fail")
    d, c = good()
    d["watch_log"][0]["mem"]["x2->0x2c:8"] = struct.pack("<II", BLOCK + 1, BLOCK2).hex()
    case("S5 fail: block not at the alignment", only("S5", d, c)[0] == "fail")
    case("S5 fail: no lift_enter record", only("S5", dict(good()[0], watch_log=[]), good()[1])[0] == "fail")
    # S6
    d, c = good()
    d["start_state"]["runs"][1][2] = 5
    case("S6 fail: .text host-executable", only("S6", d, c)[0] == "fail")
    d, c = good()
    d["start_state"]["runs"][2][2] = 1
    case("S6 fail: .data not writable", only("S6", d, c)[0] == "fail")
    d, c = good()
    d["start_state"]["runs"][3][2] = 3
    case("S6 fail: the gap is accessible", only("S6", d, c)[0] == "fail")
    d, c = good()
    d["start_state"]["runs"][0][2] = 3
    case("S6 fail: headers writable", only("S6", d, c)[0] == "fail")
    d, c = good()
    del d["start_state"]["runs"][3]
    case("S6 fail: a page of the range is unmapped", only("S6", d, c)[0] == "fail")
    # S7
    d, c = good()
    d["watch_log"][0]["mem"]["x2:48"] = bytes(0x30).hex()
    case("S7 fail: TEB +0 is not -1", only("S7", d, c)[0] == "fail")
    d, c = good()
    d["start_state"]["runs"][4][2] = 3
    case("S7 fail: the guard page is accessible", only("S7", d, c)[0] == "fail")
    d, c = good()
    d["start_state"]["runs"][5][1] -= PAGE
    case("S7 fail: the stack is shorter than SizeOfStackReserve", only("S7", d, c)[0] == "fail")
    case("S7 fail: the stack lies in a module's range", only("S7", *good(), mods=[("m.dll", 0x20000000, 0x20010000)])[0] == "fail")
    d, c = good()
    d["watch_log"][1]["args"][2] = TEB + PAGE
    case("S7 fail: two TEBs on the first thread", only("S7", d, c)[0] == "fail")
    # S8
    d, c = good()
    d["watch_log"][1], d["watch_log"][2] = d["watch_log"][2], d["watch_log"][1]
    case("S8 fail: entry before the TLS callback", only("S8", d, c)[0] == "fail")
    d, c = good()
    d["watch_log"][2]["mem"]["x1:16"] = struct.pack("<4I", bs.SENTINEL, 1, 0, 0).hex()
    case("S8 fail: entry called with arguments", only("S8", d, c)[0] == "fail")
    d, c = good()
    d["watch_log"][2]["args"][1] -= 16
    case("S8 fail: esp is not (stack_base - 16) & ~15", only("S8", d, c)[0] == "fail")
    case("S8 fail: no enter line", only("S8", good()[0], "   fnvnx-start: identity ok\n")[0] == "fail")
    d, c = good()
    d["watch_log"].append({"seq": 3, "tid": 2, "t_ns": 9, "kind": "call", "sym": bs.LIFT, "pc": 0, "sp": 0, "x0": 0x401500, "x30": 0, "args": [0x401500, 0x30000000, 0x30001000, 0]})
    r = run(d, c)
    case("a run past the entry: later lift_enter records carry args and no mem, every rule still passes", all(v.verdict() == "pass" for v in r.values()), {k: v.fail for k, v in r.items()})
    d, c = good()
    del d["watch_log"][2]
    case("S8 fail: the entry is never called", only("S8", d, c)[0] == "fail")
    # boot.py's grammar of the new observation requests
    case("mem spec: x2->0x2c->0:16 = reg 2, offsets [0x2c, 0], 16 bytes", boot.parse_mem_spec("x2->0x2c->0:16") == (2, [0x2C, 0], 16, 0))
    case("mem spec: a +DELTA after the chain", boot.parse_mem_spec("x2->0x2c->4+65536:100") == (2, [0x2C, 4], 100, 65536) and boot.parse_mem_spec("x1+0x10:4") == (1, [], 4, 16))
    class Pk:
        def peek(self, a, n):
            return (a.to_bytes(4, "little") + bytes(n))[:n] if a < 0x1000 else None
    case("read_chain adds DELTA after the last load", boot.read_chain(Pk(), [0, 0x100, 0, 0], boot.parse_mem_spec("x1+8:4")) == (0x108).to_bytes(4, "little"))
    for bad in ("x9:4", "x1", "x1:0", "x1:0x20000", "x1+:4", "x1->:4", "sp:4"):
        try:
            boot.parse_mem_spec(bad)
            case("mem spec error: " + bad, False)
        except boot.bootmiles.ResolveError:
            case("mem spec error: " + bad, True)
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        class Syms(dict):
            funcs = {"f", "g"}
        sy = Syms(f=1, g=2)
        wf = os.path.join(td, "w.txt")
        open(wf, "w").write("f ret mem x1:16 x2->4:8\ng\n# c\nf mem x0:4\n")
        names, rets, mems, _u = boot._watch_names(wf, sy)
        case("watch grammar: NAME [ret] [mem SPEC...]", names == ["f", "g", "f"] and rets == {"f"} and mems == {"f": ["x1:16", "x2->4:8", "x0:4"]}, (names, rets, mems))
        open(wf, "w").write("f mem x0:4 until x0=0x401000\ng\n")
        r4 = boot._watch_names(wf, sy)
        case("watch grammar: until REG=VALUE", r4[2] == {"f": ["x0:4"]} and r4[3] == {"f": (0, 0x401000)}, r4)
        for k, line in (("until without mem", "f until x0=1\n"), ("until bad reg", "f mem x0:4 until x9=1\n"), ("until bad value", "f mem x0:4 until x0=zz\n"),
                        ("until trailing token", "f mem x0:4 until x0=1 x1:4\n"), ("mem without spec", "f mem\n"), ("junk word", "f xyz\n"), ("bad spec", "f mem x9:1\n"), ("unknown name", "h\n")):
            open(wf, "w").write(line)
            try:
                boot._watch_names(wf, sy)
                case("watch grammar error: " + k, False)
            except boot.bootmiles.ResolveError:
                case("watch grammar error: " + k, True)
        rf = os.path.join(td, "r.json")
        open(rf, "w").write('{"hash": [{"name": "i", "lo": "0x1000", "hi": 8192}], "dump": [{"name": "d", "addr": 16, "len": 4}]}')
        case("state request parses (hex strings and integers)", boot.load_state_request(rf) == {"hash": [{"lo": 4096, "hi": 8192, "name": "i"}], "dump": [{"addr": 16, "len": 4, "name": "d"}]})
        for k, body in (("unknown key", '{"x": []}'), ("lo >= hi", '{"hash": [{"name": "i", "lo": 5, "hi": 5}]}'), ("missing field", '{"dump": [{"name": "d", "addr": 1}]}'), ("len 0", '{"dump": [{"name": "d", "addr": 1, "len": 0}]}')):
            open(rf, "w").write(body)
            try:
                boot.load_state_request(rf)
                case("state request error: " + k, False)
            except boot.bootmiles.ResolveError:
                case("state request error: " + k, True)
    print("\ntest_bootstart: %d failed" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
