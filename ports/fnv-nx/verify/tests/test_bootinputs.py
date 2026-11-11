#!/usr/bin/env python3
"""Tests of verify/bootinputs.py: the converters of the L1 contract branch files to boot.py inputs. Plain script, exit 0 on pass."""
import hashlib
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import bootinputs as bi  # noqa: E402
import boot  # noqa: E402

FAILS = []
TID = "cd" * 32


def case(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + str(detail)))
    if not ok:
        FAILS.append(name)


def raises(f, *a):
    try:
        f(*a)
    except bi.InputError as e:
        return str(e)
    except Exception as e:
        return "WRONG:" + repr(e)
    return None


def good(msg):
    return msg is not None and not msg.startswith("WRONG")


def pe(base, size):
    b = bytearray(0x200)
    b[:2] = b"MZ"
    struct.pack_into("<I", b, 0x3C, 0x80)
    b[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", b, 0x80 + 24, 0x10B)
    struct.pack_into("<II", b, 0x80 + 24 + 28, base, 0)
    struct.pack_into("<I", b, 0x80 + 24 + 56, size)
    return bytes(b)


def main():
    # join
    parts = {"part.000": b"abc", "part.001": b"defg"}
    whole = b"abcdefg"
    mj = {"schema": "fnvnx-elf/1", "name": "guest.elf", "size": 7, "sha256": hashlib.sha256(whole).hexdigest(),
          "parts": [[n, len(b), hashlib.sha256(b).hexdigest()] for n, b in parts.items()]}
    case("join: parts in manifest order", bi.join_elf(json.dumps(mj), parts.__getitem__) == whole)
    bad = dict(mj, parts=[mj["parts"][1], mj["parts"][0]])
    case("join: wrong order -> whole checksum mismatch", good(raises(bi.join_elf, json.dumps(dict(bad, parts=[["part.001", 3, mj["parts"][0][2]], mj["parts"][1]])), parts.__getitem__)))
    case("join: corrupt part", good(raises(bi.join_elf, json.dumps(mj), lambda n: b"xxx" if n == "part.000" else parts[n])))
    case("join: wrong whole sha", good(raises(bi.join_elf, json.dumps(dict(mj, sha256="0" * 64)), parts.__getitem__)))
    case("join: not a manifest", good(raises(bi.join_elf, "{}", parts.__getitem__)))
    # reserved
    H = bi.MODULE_HEADER
    row = lambda n, base, soi: "\t".join([n, "a" * 64, "10", "400000", base, soi, "0", "100", "1", "0"])
    txt = H + "\n" + row("zz.dll", "02000000", "00010000") + "\n" + row("aa.dll", "01400000", "00020000") + "\n"
    r = bi.reserved_rows(txt, pe(0x400000, 0x1000000))
    case("reserved: lo..hi from assigned_base+size_of_image, exe from its PE, sorted",
         r == [("exe", 0x400000, 0x1400000), ("aa.dll", 0x1400000, 0x1420000), ("zz.dll", 0x2000000, 0x2010000)], r)
    for k, t in (("no header", row("a.dll", "1000", "10") + "\n"), ("9 fields", H + "\na\tb\n"), ("bad hex", H + "\n" + row("a.dll", "xx", "10") + "\n"),
                 ("zero size", H + "\n" + row("a.dll", "1000", "0") + "\n"), ("stray comment", H + "\n# x\n")):
        case("reserved error: " + k, good(raises(bi.reserved_rows, t, pe(0x400000, 0x1000))))
    case("reserved error: exe not PE", good(raises(bi.reserved_rows, txt, b"nope")))
    # keys
    ent = "000ffff0\trt_a\timport\n00100000\tI_1\timport\n00401000\tL_1\tcode\n# table_id " + TID + "\n"
    kt = "# table_id " + TID + "\n00100000\tm.dll\tf\t1\tstdcall\ti\ti\tsym\n"
    lo, hi = bi.key_range(ent, kt)
    case("keys: values", (lo, hi) == (0xffff0, 0x100010), (hex(lo), hex(hi)))
    case("keys error: table id differs", good(raises(bi.key_range, ent, kt.replace(TID, "00" * 32))))
    case("keys error: key not an import row", good(raises(bi.key_range, ent, kt.replace("00100000", "00100010"))))
    case("keys error: 7 columns", good(raises(bi.key_range, ent, kt.replace("\tsym", ""))))
    case("keys error: unaligned key", good(raises(bi.key_range, ent.replace("00100000", "00100004"), kt)))
    # milestones
    mt = "# table_id " + TID + "\nexe_entry_va\t0x00ecc4db"
    run = "header\n   fnvnx-start: identity ok\n   fnvnx-start: image mapped\n   [fnvnx] other\n"
    t = bi.milestone_table(mt, run, TID)
    tab = bi.bootmiles.load_table(t)
    case("milestones: console lines in order, then exe-entry", [m["name"] for m in tab.milestones] == ["start-01", "start-02", "exe-entry"]
         and tab.milestones[0]["parts"][0]["target"] == boot.console_part("fnvnx-start: identity ok")
         and tab.milestones[2]["parts"][0]["target"] == 0xecc4db, t)
    case("milestones: no run.txt -> exe-entry only", [m["name"] for m in bi.bootmiles.load_table(bi.milestone_table(mt, "", TID)).milestones] == ["exe-entry"])
    case("milestones: a file without table_id is accepted (the contract's own form)",
         "exe-entry" in bi.milestone_table("exe_entry_va\t0x00ecc4db", run, TID))
    for k, a in (("other row", (mt + "\nfoo\t1", run)), ("id differs", (mt, run, "11" * 32)), ("duplicate console line", (mt, run + run)),
                 ("entry without 0x", (mt.replace("0x", ""), run)), ("row with 3 fields", (mt + "\nx\ty\tz", run))):
        case("milestones error: " + k, good(raises(bi.milestone_table, *a)))
    # contract row kinds: module_entry (static flag), present_key
    man = bi.bootmiles.load_manifest("00001000\tf1\tcode\n00002000\tf2\tcode\n00003000\tf3\tcode\n00100e40\tI_p\timport\n00100e50\tI_q\timport\n# table_id " + TID + "\n")
    mt2 = ("# table_id " + TID + "\nmodule_entry\ta.dll\t0x00001000\t1\nmodule_entry\tb.dll\t0x00002000\t0\n"
           "exe_entry_va\t0x00003000\npresent_key\t0x00100e40\npresent_key\t0x00100e50\n")
    t2 = bi.milestone_table(mt2, "", TID, None, man)
    tab2 = bi.bootmiles.load_table(t2)
    case("contract kinds: static module -> module-entry-<name> in file order, dynamic skipped, Present keys -> first-present any",
         [m["name"] for m in tab2.milestones] == ["module-entry-a.dll", "exe-entry", "first-present"]
         and [(p["group"], p["kind"], p["target"]) for p in tab2.milestones[2]["parts"]] == [("any", "call", 0x100e40), ("any", "call", 0x100e50)], t2)
    for k, a in (("module flag 2", (mt2.replace("0x00001000\t1", "0x00001000\t2"),)), ("module twice", (mt2 + "module_entry\ta.dll\t0x00001000\t1\n",)),
                 ("module 3 fields", (mt2 + "module_entry\tc.dll\t0x1\n",)), ("no exe entry", ("module_entry\ta.dll\t0x00001000\t1\n",)),
                 ("exe entry twice", (mt2 + "exe_entry_va\t0x00003000\n",)), ("bad module name", (mt2 + "module_entry\tx y\t0x1\t0\n",))):
        case("contract error: " + k, good(raises(bi.milestone_table, a[0], "", TID, None, man)))
    case("contract error: present key not an import row", good(raises(bi.milestone_table, mt2.replace("0x00100e50", "0x00001000"), "", TID, None, man)))
    # stage table
    st = "winmain any call 0x1000\nloaded all:x ret 0x2000\nloaded all:x retzero 0x3000 main-thread\n"
    t3 = bi.milestone_table(mt2, "", TID, st, man)
    case("stage table: appended after the contract rows", [m["name"] for m in bi.bootmiles.load_table(t3).milestones][-2:] == ["winmain", "loaded"], t3)
    sterr = lambda txt: raises(bi.milestone_table, mt2, "", TID, txt, man)
    case("stage error: name also from the contract", good(sterr("exe-entry any call 0x1000\n")))
    case("stage error: address without a code entry", good(sterr("w any call 0x4000\n")))
    case("stage error: address of an import row is not code", good(sterr("w any call 0x100e40\n")))
    case("stage error: bootmiles rejects the table", good(sterr("w any bogus 0x1000\n")))
    case("stage error: needs entries", good(raises(bi.milestone_table, mt2, "", TID, st, None)))
    case("stage: host/sym/store parts are not address-checked", not sterr("w any host console.ab\ns any store 0x9999\n"))
    # stage-table converter
    old = ("# c\nwinmain call 0x1000  # comment\nini data ini-read\nr all callok re:^Foo\nr all ret 0x2000\nr all:n calltrue 0x3000\n"
           "m all ret 0x2000\nm all callok 0x3000 main-thread\nm seq calltrue 0x4000\nf seq:d call 0x1000 main-thread\nf seq:d call re:X main-thread\n"
           "c seq store *0x11+0x40\nk any ret 0x5000\n")
    ot = bi.stage_table(old)
    ol = ot.split("\n")
    case("stage-table: kinds mapped, groups carried", "winmain any call 0x1000" in ol and "m all:all ret 0x2000" in ol
         and "m all:all retzero 0x3000 main-thread" in ol and "m seq:seq rettrue 0x4000" in ol and "r all:n rettrue 0x3000" in ol and "k any ret 0x5000" in ol, ot)
    case("stage-table: data/store/regex dropped by class, listed", "# dropped: ini any data ini-read : kind-data" in ol
         and "# dropped: r all:all callok re:^Foo : regex-target" in ol and "# dropped: c seq:seq store *0x11+0x40 : kind-store" in ol, ot)
    case("stage-table: group with a dropped part dropped whole", "# dropped: r all:all ret 0x2000 : group-incomplete" in ol
         and "# dropped: f seq:d call 0x1000 main-thread : group-incomplete" in ol and not any(x.startswith("f ") for x in ol), ot)
    case("stage-table: milestone without rows listed, kept ones not", "# dropped-milestone: ini" in ol and "# dropped-milestone: f" in ol
         and "# dropped-milestone: winmain" not in ol and "# dropped-milestone: m" not in ol, ot)
    case("stage-table: output loads in bootmiles", [m["name"] for m in bi.bootmiles.load_table(ot).milestones] == ["winmain", "r", "m", "k"], ot)
    case("stage-table error: unknown kind", good(raises(bi.stage_table, "a call2 0x1\n")) and good(raises(bi.stage_table, "a any call zz\n")))
    print("%s: %d failed" % ("FAIL" if FAILS else "PASS", len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
