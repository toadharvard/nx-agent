#!/usr/bin/env python3
"""Tests for contract/modules.py's TLS directory facts (tls.tsv, tls/<name>.bin) on synthetic PE images built here. No game data."""
import hashlib
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.append(os.path.join(HERE, "..", "..", "verify"))
import scratch  # noqa: E402

scratch.install()
import exepin  # noqa: E402
import modules  # noqa: E402

FAILED = []
BASE = 0x10000000


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:300]))
    if not cond:
        FAILED.append(name)


def pe(tls=None, dir_rva=0x1000, dir_size=24, chars=0xc0000040, flags=0x2102):
    """A PE32 i386 image with one 4 KiB section at rva 0x1000. tls = (start_va, end_va, index_va, callbacks_va, zero_fill, tls_chars, callback_array_words)
    or None (no TLS directory). The TLS directory is at rva 0x1000, the template at 0x1100, the index at 0x1200, the callback array at 0x1300."""
    sec = bytearray(0x1000)
    sec[0x100:0x120] = bytes(range(0x40, 0x60))           # the template bytes
    if tls is not None:
        start, end, index, cbs, zero, tchars, words = tls
        sec[0:24] = struct.pack("<6I", start, end, index, cbs, zero, tchars)
        for i, w in enumerate(words):
            sec[0x300 + 4 * i:0x304 + 4 * i] = struct.pack("<I", w)
    h = bytearray(0x200)
    h[0:2] = b"MZ"
    struct.pack_into("<I", h, 0x3c, 0x80)
    h[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", h, 0x84, 0x14c, 1, 0, 0, 0, 224, flags)
    o = 0x98
    struct.pack_into("<H", h, o, 0x10b)
    struct.pack_into("<I", h, o + 28, BASE)
    struct.pack_into("<II", h, o + 32, 0x1000, 0x200)
    struct.pack_into("<II", h, o + 56, 0x2000, 0x200)
    struct.pack_into("<I", h, o + 92, 16)
    if tls is not None:
        struct.pack_into("<II", h, o + 96 + 8 * 9, dir_rva, dir_size)
    struct.pack_into("<8sIIIIIIHHI", h, o + 224, b".data", 0x1000, 0x1000, 0x1000, 0x200, 0, 0, 0, 0, chars)
    return bytes(h) + bytes(sec)


def good():
    return (BASE + 0x1100, BASE + 0x1120, BASE + 0x1200, BASE + 0x1300, 0x40, 0x00300000, [BASE + 0x1010, BASE + 0x1020, 0])


def red(name, data, frag):
    try:
        modules.PE(data, "x").tls()
        check(name, False, "no error")
    except modules.StageError as e:
        check(name, frag in str(e), e)


class M:
    """The part of modules.Module that tls_rows reads."""
    def __init__(self, name, data):
        self.name, self.pe = name, modules.PE(data, name)


t = modules.PE(pe(good()), "a.dll").tls()
check("a valid directory: raw VAs, template rva and size, zero fill, align field (bits 20-23), callbacks as rvas up to the null",
      (t["start_va"], t["end_va"], t["index_va"], t["callbacks_va"], t["template_rva"], t["template_size"], t["zero_fill"], t["align"], t["callbacks"])
      == (BASE + 0x1100, BASE + 0x1120, BASE + 0x1200, BASE + 0x1300, 0x1100, 0x20, 0x40, 3, [0x1010, 0x1020]), t)
check("the template is the mapped image's bytes", t["template"] == bytes(range(0x40, 0x60)))
check("alignment bits 0 stay 0", modules.PE(pe((BASE + 0x1100, BASE + 0x1100, BASE + 0x1200, 0, 0, 0, [])), "a").tls()["align"] == 0)
z = modules.PE(pe((BASE + 0x1100, BASE + 0x1100, BASE + 0x1200, 0, 0, 0, [])), "a").tls()
check("an empty template and callbacks pointer 0: no callbacks, size 0", z["template_size"] == 0 and z["callbacks"] == [] and z["template"] == b"")
check("a first callback word of 0: an empty list", modules.PE(pe((BASE + 0x1100, BASE + 0x1120, BASE + 0x1200, BASE + 0x1300, 0, 0, [0])), "a").tls()["callbacks"] == [])
check("no directory: None", modules.PE(pe(None), "a").tls() is None)
red("end below start is red", pe((BASE + 0x1120, BASE + 0x1100, BASE + 0x1200, 0, 0, 0, [])), "is below")
red("a template past the end of the image is red", pe((BASE + 0x1f00, BASE + 0x2100, BASE + 0x1200, 0, 0, 0, [])), "template")
red("a template below the image base is red", pe((BASE - 0x10, BASE + 0x1100, BASE + 0x1200, 0, 0, 0, [])), "template")
red("a callback array at the very end of the image (no word left for a terminator) is red", pe((BASE + 0x1100, BASE + 0x1120, BASE + 0x1200, BASE + 0x2000, 0, 0, [])), "terminator")
# the array starts 8 bytes before the end of the image and holds two nonzero words: no terminator inside the image
_b = bytearray(pe((BASE + 0x1100, BASE + 0x1120, BASE + 0x1200, BASE + 0x1ff8, 0, 0, [])))
_b[0x200 + 0xff8:0x200 + 0x1000] = struct.pack("<II", BASE + 0x1010, BASE + 0x1020)
red("a callback array without a null terminator before the image ends is red", bytes(_b), "terminator")
red("a callback outside the image is red", pe((BASE + 0x1100, BASE + 0x1120, BASE + 0x1200, BASE + 0x1300, 0, 0, [BASE + 0x9000, 0])), "callback")
red("a directory shorter than 24 bytes is red", pe(good(), dir_size=16), "24")
red("a directory outside the image is red", pe(good(), dir_rva=0x1ff8, dir_size=24), "directory 9")

# the universe: the exe first, then exactly the images of modules.tsv with has TLS = 1, sorted by name; the files written
exe = modules.PE(pe(good()), exepin.NAME)
order = [M("b.dll", pe(good())), M("a.dll", pe(None)), M("c.dll", pe(good()))]
rows = modules.tls_rows(exe, order, {"b.dll": 0, "c.dll": 2})
check("rows: exe, b.dll, c.dll (a.dll has no directory); load_index of the exe is empty",
      [(n, i) for n, i, _ in rows] == [(exepin.NAME, ""), ("b.dll", 0), ("c.dll", 2)], [(n, i) for n, i, _ in rows])
check("the DLL rows equal the count of has_tls = 1 over the images of modules.tsv",
      len(rows) - 1 == sum(1 for m in order if m.pe.dirs[9][1]) == 2)
out = tempfile.mkdtemp(prefix="tls-")
modules.write_tls(out, rows)
lines = [l for l in open(os.path.join(out, "tls.tsv")).read().splitlines() if not l.startswith("#")]
f = lines[1].split("\t")
check("tls.tsv: one row per image, 12 columns, the row of b.dll as specified",
      len(lines) == 3 and all(len(l.split("\t")) == 14 for l in lines)
      and f == ["b.dll", "0", "10001100", "10001120", "10001200", "10001300", "00001100", "32", "64", "3", hashlib.sha256(bytes(range(0x40, 0x60))).hexdigest(), "00001010;00001020", "1", "4"], f)
g = lines[2].split("\t")
check("tls_slot: the exe 0, then the static images with a directory from 1 in load_index order (b.dll index 0 -> 1, c.dll index 2 -> 2)",
      [l.split("\t")[12] for l in lines] == ["0", "1", "2"], [l.split("\t")[12] for l in lines])
rs = modules.tls_slots([(exepin.NAME, "", {}), ("z.dll", 5, {}), ("m.dll", "", {}), ("a.dll", 1, {})])
check("tls_slot: by load_index not by name; an image loaded on demand has none", rs == {exepin.NAME: 0, "a.dll": 1, "z.dll": 2}, rs)
check("align_bytes: 0 -> 1, n -> 1 << (n-1) for n in 1..14 (tls.c:38)", [modules.align_bytes(n) for n in range(0, 15)] == [1] + [1 << (n - 1) for n in range(1, 15)])
check("align_bytes column: the raw 3 of b.dll is 4 bytes", f[13] == "4" and g[13] == "4" and lines[0].split("\t")[13] == "4", (f, g))
check("tls/<name>.bin holds the template of each row",
      sorted(os.listdir(os.path.join(out, "tls"))) == sorted([exepin.NAME + ".bin", "b.dll.bin", "c.dll.bin"])
      and open(os.path.join(out, "tls", "b.dll.bin"), "rb").read() == bytes(range(0x40, 0x60)))

# ---- the loader tables: run(..., c_out, keys_out) writes the module table and the import table from one exe, one bridge, the module dirs
import tempfile  # noqa: E402


def exe_with_import():
    """A PE32 image named like the pinned exe, with one import descriptor: kernel32.dll!ExitProcess (slot rva 0x1180)."""
    d = bytearray(pe())
    struct.pack_into("<II", d, 0x98 + 96 + 8, 0x1000, 40)                      # data directory 1: the import directory at rva 0x1000
    sec = 0x200
    struct.pack_into("<IIIII", d, sec, 0x1100, 0, 0, 0x1300, 0x1180)           # descriptor: oft, _, _, name, ft; the terminator is the zero rest
    struct.pack_into("<II", d, sec + 0x100, 0x1200, 0)                          # the lookup thunks
    struct.pack_into("<II", d, sec + 0x180, 0x1200, 0)                          # the first thunks
    d[sec + 0x200:sec + 0x200 + 14] = b"\0\0ExitProcess\0"
    d[sec + 0x300:sec + 0x300 + 13] = b"kernel32.dll\0"
    return bytes(d)


with tempfile.TemporaryDirectory() as tmp:
    exe = os.path.join(tmp, exepin.NAME)
    data = exe_with_import()
    open(exe, "wb").write(data)
    bridge = os.path.join(tmp, "bridge.tsv")
    open(bridge, "w").write("kernel32.dll\tExitProcess\t\tstdcall\tv\ti\tfnvnx_win_ExitProcess\tbind\n")
    declared = os.path.join(tmp, "declared.tsv")
    open(declared, "w").write("# name\tsha\nkernel32.dll\t00\n")
    os.makedirs(os.path.join(tmp, "dir"))
    pin = dict(exepin.pe_fields(data), size=len(data), sha256=hashlib.sha256(data).hexdigest())
    old = exepin.load_exe.__defaults__
    exepin.load_exe.__defaults__ = (pin,)
    try:
        mc, kc = os.path.join(tmp, "modules.c"), os.path.join(tmp, "import_keys.c")
        modules.run(exe, bridge, [os.path.join(tmp, "dir")], None, declared, mc, kc, os.path.join(tmp, "keys.tsv"), os.path.join(tmp, "iat_data.tsv"))
    finally:
        exepin.load_exe.__defaults__ = old
    mt, kt = open(mc).read(), open(kc).read()
    check("c_out: the module table of an exe with no module has the count 0 and the table symbols", "fnvnx_modules[]" in mt and "fnvnx_module_count = 0u" in mt, mt)
    check("keys_c_out: the import table has the row of the exe's import, the counts and the by-key index",
          '"kernel32.dll", "ExitProcess"' in kt and "fnvnx_import_row_count = 1;" in kt and "fnvnx_import_key_count = 1;" in kt and "fnvnx_import_by_key_idx[]" in kt, kt)
    check("out None: the table tree is not written (a stage has no scratch directory)", not os.path.exists(os.path.join(tmp, "o")) and "tempfile" not in open(modules.__file__).read(), None)
    kv, dv = open(os.path.join(tmp, "keys.tsv")).read(), open(os.path.join(tmp, "iat_data.tsv")).read()
    check("keys_tsv_out: keys.tsv has the row of the exe's import and data_out: iat_data.tsv has the table_id line", "ExitProcess" in kv and dv.startswith("# table_id "), (kv, dv))
print("FAILED: %d" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
