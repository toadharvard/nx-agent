#!/usr/bin/env python3
"""Selftest of filesites.py and fileset_classes.py on a synthetic PE image built here from byte snippets (no game bytes).

The image holds, at 16-aligned entries:
  F1  CreateFileA("Data\\x.esm", ...)                                         constant name (static, class i via the shipped set)
  W   wrapper: DeleteFileA(parameter [ebp+8]);  C  calls W("a.tmp") and W("b.tmp")   (static through the callers)
  F3  GetModuleFileNameA(0, [ebp-0x104], 260) then CreateFileA(that buffer)   (dynamic: `<GetModuleFileNameA>+?`, suffix not resolved)
  F4  CreateFileA([ecx+0x10])                                                  (dynamic: object field of an untyped receiver)
  F5  CreateDirectoryA("RTF", 0)                                              (class ii)
  F6  LoadLibraryA("nvapi.dll"), F7 LoadLibraryA("user32.dll"), F8 CreateFileA("CONOUT$")   (class iv, sysmod, device)
Then the classes of fileset_classes.py are checked, and the domain gate on an import without a ROLES row.
Usage: check_filesites.py
"""
import os, struct, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import funcfacts, pemanifest, filesites, fileset_classes

BASE = 0x400000
IMP = {"CreateFileA": 0x403000, "DeleteFileA": 0x403004, "GetModuleFileNameA": 0x403008, "CreateDirectoryA": 0x40300c,
       "LoadLibraryA": 0x403010}


def build():
    img, rel, cur = bytearray(0x4000), [], [0x403100]

    def put(va, b):
        img[va - BASE:va - BASE + len(b)] = b

    def string(s):
        a = cur[0]
        put(a, s.encode() + b"\0")
        cur[0] += (len(s) + 4) & ~3
        return a

    runs = {}

    def asm(va, parts):
        b = bytearray()
        for p in parts:
            if isinstance(p, tuple):
                if p[0] == "abs":
                    rel.append(va + len(b))
                    b += struct.pack("<I", p[1])
                else:
                    b += struct.pack("<i", p[1] - (va + len(b) + 4))
            else:
                b += bytes(p)
        put(va, bytes(b))
        runs[va] = [(va, va + len(b))]
        put(va + len(b), b"\xcc" * ((-(va + len(b))) % 16 or 16))

    pro = [b"\x55\x8b\xec"]
    ret = [b"\x8b\xe5\x5d\xc3"]
    call = lambda api: [b"\xff\x15", ("abs", IMP[api])]
    z = lambda n: [b"\x6a\x00"] * n
    # CreateFileA(name, 0x80000000, 1, 0, 3, 0, 0): pushes right to left
    cf = lambda name: z(1) + [b"\x6a\x00", b"\x6a\x03", b"\x6a\x00", b"\x6a\x01", b"\x68\x00\x00\x00\x80"] + name + call("CreateFileA")
    s = {k: string(k) for k in ("Data\\x.esm", "a.tmp", "b.tmp", "RTF", "nvapi.dll", "user32.dll", "CONOUT$")}
    asm(0x401000, pro + cf([b"\x68", ("abs", s["Data\\x.esm"])]) + ret)
    asm(0x401040, pro + [b"\x8b\x45\x08\x50"] + call("DeleteFileA") + [b"\x5d\xc2\x04\x00"])
    asm(0x401090, pro + [b"\x68", ("abs", s["a.tmp"]), b"\xe8", ("rel", 0x401040), b"\x83\xc4\x04", b"\x68", ("abs", s["b.tmp"]),
                         b"\xe8", ("rel", 0x401040), b"\x83\xc4\x04\x5d\xc3"])
    buf = [b"\x8d\x85\xfc\xfe\xff\xff\x50"]  # lea eax,[ebp-0x104]; push eax
    asm(0x4010d0, pro + [b"\x81\xec\x04\x01\x00\x00", b"\x68\x04\x01\x00\x00"] + buf + z(1) + call("GetModuleFileNameA") + cf(buf) + ret)
    asm(0x401150, pro + [b"\x8b\x41\x10"] + z(1) + [b"\x6a\x00", b"\x6a\x03", b"\x6a\x00", b"\x6a\x01", b"\x68\x00\x00\x00\x80", b"\x50"]
        + call("CreateFileA") + ret)
    asm(0x401190, pro + z(1) + [b"\x68", ("abs", s["RTF"])] + call("CreateDirectoryA") + [b"\x5d\xc3"])
    asm(0x4011d0, pro + [b"\x68", ("abs", s["nvapi.dll"])] + call("LoadLibraryA") + [b"\x5d\xc3"])
    asm(0x401210, pro + [b"\x68", ("abs", s["user32.dll"])] + call("LoadLibraryA") + [b"\x5d\xc3"])
    asm(0x401250, pro + cf([b"\x68", ("abs", s["CONOUT$"])]) + ret)
    m = pemanifest.Mod.synthetic("synth.exe", BASE, img, rel, [("kernel32.dll", n, a) for n, a in IMP.items()], [(0x401000, 0x400)], runs)
    return m


def main():
    m = build()
    rows = filesites.rows_of(m)
    show = "\n".join(repr(r) for r in rows)
    in_ = lambda lo, hi: sorted(r for r in rows if lo <= r[0] < hi and r[1] != "GetModuleFileNameA")
    # row = (site, api, kind, name, "-", status, reason, via)
    r1 = in_(0x401000, 0x401040)
    assert [(r[1], r[3], r[5]) for r in r1] == [("CreateFileA", "Data\\x.esm", "static")], show
    w = in_(0x401040, 0x401090)
    assert sorted((r[3], r[5]) for r in w) == [("a.tmp", "static"), ("b.tmp", "static")] and all(r[1] == "DeleteFileA" for r in w), show
    f3 = in_(0x4010d0, 0x401150)
    assert len(f3) == 1 and f3[0][3] == "<GetModuleFileNameA>+?" and f3[0][5] == "dynamic" and "suffix not resolved" in f3[0][6], show
    f4 = in_(0x401150, 0x401190)
    assert len(f4) == 1 and f4[0][3] == "dynamic" and f4[0][5] == "dynamic", show
    assert [(r[1], r[3], r[5]) for r in in_(0x401190, 0x4011d0)] == [("CreateDirectoryA", "RTF", "static")], show
    assert [r[3] for r in in_(0x4011d0, 0x401250)] == ["nvapi.dll", "user32.dll"], show
    assert len([r for r in rows if r[1] == "GetModuleFileNameA"]) == 1, show
    # classes
    with tempfile.TemporaryDirectory() as d:
        tsv, ship, plat, out = (os.path.join(d, n) for n in ("s.tsv", "ship.txt", "plat.txt", "o.tsv"))
        with open(tsv, "w") as f:
            f.write("module\tsite\tapi\tkind\tname\tstatus\treason\n")
            for r in rows:
                f.write("synth.exe\t0x%08x\t%s\t%s\t%s\t%s\t%s\n" % (r[0], r[1], r[2], r[3], r[5], r[6] or ""))
        open(ship, "w").write("Data\\x.esm\n")
        open(plat, "w").write("user32.dll\n")
        fileset_classes.main(tsv, ship, plat, out)
        cl = {}
        for l in list(open(out))[1:]:
            f = l.rstrip("\n").split("\t")
            cl.setdefault(f[3] + ":" + f[4], f[5])
    exp = {"open:Data\\x.esm": "i", "delete:a.tmp": "unresolved", "delete:b.tmp": "unresolved", "dir:RTF": "ii", "load:nvapi.dll": "iv", "load:user32.dll": "sysmod",
           "open:CONOUT$": "device", "open:<GetModuleFileNameA>+?": "unresolved", "open:dynamic": "unresolved", "module:-": "module"}
    assert cl == exp, (cl, exp)
    m.imports.append(("kernel32.dll", "CreateFileTransactedA", 0x403014))
    assert filesites.gate_domain({"synth.exe": m}) == [("synth.exe", "kernel32.dll", "CreateFileTransactedA")]
    print("filesites selftest ok: %d rows" % len(rows))


if __name__ == "__main__":
    main()
