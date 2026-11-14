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
SHA = hashlib.sha256(EXE).hexdigest()


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
                     "x2->0x2c->0:16": (b"TEMPLATE" + bytes(8)).hex()}
    calls = [(0x10001000, [0x10000000, 1, 1]), (0x401010, [BASE, 1, 0]), (0x401000, [0, 0, 0])]
    wl = [{"seq": i, "tid": 1, "t_ns": i, "kind": "call", "sym": bs.LIFT, "pc": 0, "sp": 0, "x0": eip, "x30": 0, "args": [eip, esp, TEB, 0],
           "mem": mem([bs.SENTINEL] + a)} for i, (eip, a) in enumerate(calls)]
    runs = [[BASE, BASE + 0x1000, 1], [BASE + 0x1000, BASE + 0x2000, 1], [BASE + 0x2000, BASE + 0x3000, 3], [BASE + 0x3000, BASE + 0x4000, 0],
            [STACK_LIM - PAGE, STACK_LIM, 0], [STACK_LIM, TEB + 0x2000, 3]]
    dump = {"iat:00402060": {"addr": 0x402060, "hex": struct.pack("<II", 0x000F0010, 0x000F0020).hex()},
            "tls_index": {"addr": 0x4020E0, "hex": "00000000"}}
    st = {"at": bs.START, "t_ns": 0, "regs": {}, "runs": runs, "dump": dump,
          "pages": {"image": {"lo": BASE, "sha256": [hashlib.sha256(pg[a]).hexdigest() for a in sorted(pg)]}}}
    console = "   fnvnx-start: identity ok\n   fnvnx-start: image mapped\n   fnvnx-start: imports bound 2\n   fnvnx-start: enter 00401000\n"
    return {"start_state": st, "watch_log": wl}, console


def run(doc, console, exe=EXE, sha=SHA, iat=IAT, mods=MODS):
    return {v.rule: v for v in bs.check(doc, console, bs.parse_pe(exe), sha, iat, mods)}


def main():
    pe = bs.parse_pe(EXE)
    case("parse: base, entry, stack, 2 sections", (pe["base"], pe["entry_rva"], pe["stack_reserve"], len(pe["sections"])) == (BASE, 0x1000, 0x2000, 2))
    case("import slots read from the PE's own directory", bs.import_slots(pe) == {0x402060: ("KERNEL32.dll", "Foo"), 0x402064: ("KERNEL32.dll", "#5")}, bs.import_slots(pe))
    t = bs.tls_dir(pe)
    case("tls directory: template 8, zero 8, align 4, callbacks [0x401010]", (t["size"], t["zero"], t["align"], t["callbacks"]) == (8, 8, 4, [0x401010]), t)
    case("request: iat run dumped, tls index dumped, lift_enter watch with the block spec",
         bs.request(pe, IAT)[1]["dump"] == [{"name": "iat:00402060", "addr": 0x402060, "len": 8}, {"name": "tls_index", "addr": 0x4020E0, "len": 4}]
         and "x2->0x2c->0:16" in bs.request(pe, IAT)[0], bs.request(pe, IAT))
    case("request specs parse in boot.py's grammar", all(boot.parse_mem_spec(s) for s in bs.request(pe, IAT)[0].split()[-4:] if ":" in s))
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
    d["start_state"]["dump"]["iat:00402060"]["hex"] = struct.pack("<II", 0x000F0010, VEC).hex()
    v = run(d, c, iat=[IAT[0], (0x402064, "KERNEL32.dll", "Bar", "data", None)])["S4"]
    case("S4 undecided, not pass: a data slot at a mapped RW address is only partly decidable", v.verdict() == "undecided" and not v.fail, (v.fail, v.undec))
    d["start_state"]["dump"]["iat:00402060"]["hex"] = struct.pack("<II", 0x000F0010, 0x30000000).hex()
    case("S4 fail: a data slot at an unmapped address", run(d, c, iat=[IAT[0], (0x402064, "KERNEL32.dll", "Bar", "data", None)])["S4"].verdict() == "fail")
    # S5
    d, c = good()
    d["start_state"]["dump"]["tls_index"]["hex"] = "01000000"
    case("S5 fail: TLS index not 0", only("S5", d, c)[0] == "fail")
    d, c = good()
    d["watch_log"][0]["mem"]["x2->0x2c->0:16"] = (b"TEMPLATX" + bytes(8)).hex()
    case("S5 fail: block differs from the template", only("S5", d, c)[0] == "fail")
    d, c = good()
    d["watch_log"][0]["mem"]["x2->0x2c:4"] = struct.pack("<I", BLOCK + 1).hex()
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
    del d["watch_log"][2]
    case("S8 fail: the entry is never called", only("S8", d, c)[0] == "fail")
    # boot.py's grammar of the new observation requests
    case("mem spec: x2->0x2c->0:16 = reg 2, offsets [0x2c, 0], 16 bytes", boot.parse_mem_spec("x2->0x2c->0:16") == (2, [0x2C, 0], 16))
    for bad in ("x9:4", "x1", "x1:0", "x1:0x20000", "x1->:4", "sp:4"):
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
        names, rets, mems = boot._watch_names(wf, sy)
        case("watch grammar: NAME [ret] [mem SPEC...]", names == ["f", "g", "f"] and rets == {"f"} and mems == {"f": ["x1:16", "x2->4:8", "x0:4"]}, (names, rets, mems))
        for k, line in (("mem without spec", "f mem\n"), ("junk word", "f xyz\n"), ("bad spec", "f mem x9:1\n"), ("unknown name", "h\n")):
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
