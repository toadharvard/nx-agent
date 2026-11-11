#!/usr/bin/env python3
"""test_dkvtx.py: tests of render/d3d9/backend/nx/dk_vtx.c (deko3d backend: DEFINE_LAYOUT, DEFINE_LAYOUT4, BIND_VTX, BIND_VTXN, bind_layout, flush).
    python3 -I tests/l6/dkvtx/test_dkvtx.py [--no-mutations]    needs gcc, the devkitA64 cross compiler and libnx / deko3d headers (FNVNX_DK, default /home/user/work/l6/dk)
 a  static: <= 300 lines, no stub-class word, no empty function body, no weak symbol
 b  cross build (aarch64, -Werror) by build_dk.sh; the undefined symbols of dk_vtx.o are the neighbour hooks, nxdk / nxchk_*, deko3d calls and libc
 c  the format table: every NxAttrType4 / NxAttrType maps to the deko3d size / type of the GL reference's format (gl4_vtx.c attr_fmt)
 d  host run of the real product file under ASan + UBSan against a recording double of deko3d (host_main.c)
 f  nxdk_vtx_shutdown: all statics zero (their start value) and every heap block freed once; script A twice = byte-identical logs; double shutdown
 e  mutations of a copy of the product file: every one must be caught
Output: the check count, mutations caught, ALL OK."""
import os, re, shutil, struct, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
BACK = os.path.join(ROOT, "render", "d3d9", "backend"); NXD = os.path.join(BACK, "nx")
DK = os.environ.get("FNVNX_DK", "/home/user/work/l6/dk")
NM = os.path.join(DK, "dkp", "devkitA64", "bin", "aarch64-none-elf-nm")
FORBID = re.compile(r"stub|todo|not implemented|placeholder|fallback|no-op|noop|weak", re.I)
OK_UND = re.compile(r"^(dk[A-Z]\w*|nxdk|nxdk_s|nxdk_mem|nxdk_guest_ptr|nxdk_chk_env|nxdk_stage_alloc|nxchk_err|nxchk_layout4|nxchk_bind_vtxn|memset|memcpy|memcmp|malloc|realloc|free)$")

def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)
def tail(s, n=8): return "\n".join(s.strip().splitlines()[-n:])
fails = []
def check(ok, msg):
    if not ok: fails.append(msg); print("FAIL", msg)

tmp = tempfile.mkdtemp(prefix="dkvtx_")
PROD = os.path.join(NXD, "dk_vtx.c")
SRC = open(PROD).read() if os.path.exists(PROD) else ""

# ---- a ------------------------------------------------------------------------------------------------------------------------------------------
check(bool(SRC), "dk_vtx.c does not exist")
check(len(SRC.splitlines()) <= 300, "dk_vtx.c is longer than 300 lines")
m = FORBID.search(SRC); check(not m, "dk_vtx.c contains the forbidden word " + (m.group(0) if m else ""))
check(not re.search(r"\)\s*\{\s*\}", SRC), "dk_vtx.c has an empty function body")
check("__attribute__" not in SRC, "dk_vtx.c uses an attribute")

# every file-static object (not a function, not const) has no initializer, so its start value is zero; the host checks each one after a shutdown
STAT = []
for l in SRC.splitlines():
    m = re.match(r"static\s+(?!const\b)([^()=;]*?)\b(\w+)(\[[^\]]*\])*\s*(=[^;]*)?;", l)
    if m:
        check(m.group(4) is None, "static %s has an initializer: its start value is not zero" % m.group(2)); STAT.append(m.group(2))
check(len(STAT) >= 5, "static scan found %s" % STAT)

# ---- b ------------------------------------------------------------------------------------------------------------------------------------------
out = os.path.join(tmp, "cross")
r = sh(["sh", os.path.join(NXD, "build_dk.sh"), out])
check(r.returncode == 0 and os.path.exists(os.path.join(out, "dk_vtx.o")), "cross build failed: " + tail(r.stderr + r.stdout))
if os.path.exists(os.path.join(out, "dk_vtx.o")):
    nm = sh([NM, os.path.join(out, "dk_vtx.o")]).stdout
    syms = [l.split()[-1] for l in nm.splitlines() if re.match(r"^\s+U ", l)]
    check({"nxdk_mem", "nxdk_guest_ptr", "nxdk_stage_alloc", "nxdk_chk_env", "nxchk_layout4", "nxchk_bind_vtxn", "nxchk_err"} <= set(syms), "dk_vtx.o does not use the hooks: " + " ".join(syms))
    check(all(OK_UND.match(s) for s in syms), "unexpected undefined symbols: " + " ".join(s for s in syms if not OK_UND.match(s)))
    check(not re.search(r"^\S*\s+[Ww]\s", nm, re.M), "a weak symbol is present")
    for s in ("nxdk_op_define_layout", "nxdk_op_define_layout4", "nxdk_op_bind_vtx", "nxdk_op_bind_vtxn", "nxdk_vtx_bind_layout", "nxdk_vtx_flush", "nxdk_vtx_shutdown"):
        check(re.search(r"\sT %s$" % s, nm, re.M) is not None, s + " is not defined")

# ---- c: format tables ---------------------------------------------------------------------------------------------------------------------------
SZ = {(1, 32): 0x12, (2, 32): 0x04, (3, 32): 0x02, (4, 32): 0x01, (1, 16): 0x1b, (2, 16): 0x0f, (3, 16): 0x05, (4, 16): 0x03, (1, 8): 0x1d, (2, 8): 0x18, (3, 8): 0x13, (4, 8): 0x0a}
SZ_DEC = 0x30; NONE, SNORM, UNORM, SINT, UINT, USCALED, SSCALED, FLOAT = range(8)
# NxAttrType4 -> (size, type, isBgra, fixw)
T4 = [(SZ[1, 32], FLOAT, 0, 0), (SZ[2, 32], FLOAT, 0, 0), (SZ[3, 32], FLOAT, 0, 0), (SZ[4, 32], FLOAT, 0, 0),   # Float1..4
      (SZ[4, 8], UNORM, 1, 0), (SZ[4, 8], USCALED, 0, 0), (SZ[2, 16], SSCALED, 0, 0), (SZ[4, 16], SSCALED, 0, 0),   # D3DColor UByte4 Short2 Short4
      (SZ[4, 8], UNORM, 0, 0), (SZ[2, 16], SNORM, 0, 0), (SZ[4, 16], SNORM, 0, 0), (SZ[2, 16], UNORM, 0, 0), (SZ[4, 16], UNORM, 0, 0),   # UByte4N Short2N Short4N UShort2N UShort4N
      (SZ_DEC, USCALED, 0, 1), (SZ_DEC, SNORM, 0, 1), (SZ[2, 16], FLOAT, 0, 0), (SZ[4, 16], FLOAT, 0, 0)]   # UDec3 Dec3N Float16_2 Float16_4
BYTES4 = [4, 8, 12, 16, 4, 4, 4, 8, 4, 4, 8, 4, 8, 4, 4, 4, 8]
T3 = [(32, FLOAT), (8, UNORM), (16, SNORM), (16, FLOAT)]   # NxAttrType: Float32 Unorm8 Snorm16 Float16

class Host:
    def __init__(self, exe):
        self.p = subprocess.Popen([exe], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  env=dict(os.environ, ASAN_OPTIONS="detect_leaks=1", UBSAN_OPTIONS="print_stacktrace=1"))
    def cmd(self, line):
        self.p.stdin.write(line + "\n"); self.p.stdin.flush()
        out = self.p.stdout.readline()
        if not out: raise RuntimeError("host died on '%s': %s" % (line[:60], tail(self.p.stderr.read(), 6)))
        return out.rstrip("\n")
    def call(self, line):   # -> (ret, errors, oob, message, [calls])
        o = self.cmd(line)
        m = re.match(r"R ([0-9a-f]+) E (\d+) O (\d+) M (.*?) C (.*)$", o)
        if not m: raise RuntimeError("bad reply to %s: %s" % (line[:60], o))
        return int(m.group(1), 16), int(m.group(2)), int(m.group(3)), m.group(4), [] if m.group(5) == "-" else [c for c in m.group(5).split(";") if c]
    def live(self): return int(re.match(r"S live (\d+) made (\d+)", self.cmd("state")).group(1))
    def close(self):
        self.p.stdin.close(); rc = self.p.wait(timeout=60); return rc, self.p.stderr.read()

def pat(i): return (i * 7 + 1) & 255   # the bytes of a fresh guest block
def words(*w): return " ".join(str(x) for x in w)
def l3(count, stride, attrs):   # attrs: [(offset, type, components, location)] -> the 9 words of NxVtxLayout
    b = struct.pack("<BBBB", count, stride, 0, 0)
    for i in range(8): b += struct.pack("<BBBB", *(attrs[i] if i < len(attrs) else (0, 0, 0, 0)))
    return " ".join(str(x) for x in struct.unpack("<9I", b))
def l4(attrs, inst=0, div=None):   # attrs: [(offset, stream, type, location)] -> the 50 words of NxVtxLayout4
    d = list(div or [0] * 16); b = struct.pack("<BBBBI", len(attrs), 0, 0, 0, inst) + struct.pack("<16I", *d)
    for i in range(16): b += struct.pack("<HBBBBBB", *(((attrs[i][0], attrs[i][1], attrs[i][2], attrs[i][3], 0, 0, 0)) if i < len(attrs) else (0,) * 7))
    return " ".join(str(x) for x in struct.unpack("<50I", b))
def attr_line(i, buf, off, size, typ, bgra, fixed): return "A %d %d %d 0x%x %d %d %d" % (i, buf, off, size, typ, bgra, fixed)
OFF = lambda i: attr_line(i, 0, 0, 0x12, FLOAT, 0, 1)   # a slot no attribute reads: deko3d's own fill (constant 0, 0, 0, 1 as the reference's GL default)
def X(i, off, size, data=None):
    return "X %d %x %d %s %s" % (i, off, size, "stage" if size else "none", data.hex() if data else "")
def mem(off, size): return bytes(pat(i) for i in range(off, off + size))

def script_a():
    # a fresh process answers the first lines the same way every time: nothing defined, nothing bound
    return ["layout 5", "flush", "mem 1 256", "bindvtx 1 0 16", "def4 5 " + l4([(0, 0, 3, 0), (12, 0, 5, 1), (0, 2, 1, 2), (0, 3, 13, 3)], inst=1 << 2, div=[0, 0, 3] + [0] * 13),
            "layout 5", "flush", "bindvtxn 0 1 0 20 20", "bindvtxn 2 1 100 40 8", "bindvtxn 3 1 8 16 4", "layout 5", "flush", "stagefree",
            "bindvtxn 0 1 0 100 20", "bindvtxn 0 1 0 250 20", "bindvtxn 1 1 0 64 16", "bindvtxn 2 1 0 8 8", "layout 5", "flush", "stagefree",
            "def3 6 " + l3(2, 16, [(0, 0, 3, 0), (12, 1, 4, 0)]), "layout 6", "bindvtx 1 3 200", "flush", "bindvtxn 3 0 0 0 0", "layout 5", "flush", "stagefree",
            "layout 6", "flush", "layout 77", "flush", "bindvtxn 0 1 250 10 4", "def3 0 " + l3(1, 16, [(0, 0, 3, 0)])]

def run_a(h): return [h.cmd(c) if c.split()[0] in ("mem", "stagefree") else "|".join(map(str, h.call(c))) for c in script_a()]

def shutdown_checks(h, ck):
    # the heap, double shutdown, every static back to its start value
    ck(h.cmd("heap") != "H 0", "the scenarios left host copies allocated (the heap check would prove nothing)")
    h.cmd("stagefree"); h.cmd("shutdown")
    ck(h.cmd("heap") == "H 0", "heap blocks of dk_vtx.c after shutdown: " + h.cmd("heap"))
    z = h.cmd("zero"); ck(re.fullmatch(r"(\w+=1 )+", z) is not None, "statics after shutdown not all zero: " + z)
    ck(sorted(h.cmd("statics").split()) == sorted(STAT), "host checks %s, the file declares %s" % (h.cmd("statics"), STAT))
    h.cmd("shutdown"); ck(h.cmd("heap") == "H 0" and re.fullmatch(r"(\w+=1 )+", h.cmd("zero")) is not None, "second shutdown in a row")
    # the op handlers behave as in a fresh process: A, shutdown, A, shutdown shutdown, A
    logs = []
    for rnd in range(3):
        logs.append(run_a(h)); h.cmd("stagefree")
        ck(h.cmd("heap") != "H 0", "script A allocates host copies")
        h.cmd("shutdown")
        if rnd == 1: h.cmd("shutdown")
        ck(h.cmd("heap") == "H 0" and re.fullmatch(r"(\w+=1 )+", h.cmd("zero")) is not None, "after round %d: heap / statics" % rnd)
    ck(logs[0] == logs[1] == logs[2], "script A after a shutdown differs from the first run: " + str([(a, b) for a, b in zip(logs[0], logs[1]) if a != b][:2]))
    ck(logs[0][0].startswith("0|0|0|-|['AN 0']") and "has no BindVtxN" in "".join(logs[0]), "script A probes the fresh state: " + logs[0][0])

def scenarios(exe):
    h = Host(exe); n = [0]; f = []
    def ck(ok, msg):
        n[0] += 1
        if not ok: f.append(msg)
    # attribute type table (c), one layout per type
    for t in range(17):
        h.call("def4 3 " + l4([(4, 0, t, 5)]))
        r = h.call("layout 3")
        sz, ty, bgra, _ = T4[t]
        ck(r[0] == 0 and r[1] == 0 and r[4] == ["AN 6"] + [OFF(i) for i in range(5)] + [attr_line(5, 0, 4, sz, ty, bgra, 0)], "v4 type %d: %s" % (t, r))
    for t in range(4):
        for comp in (0, 1, 2, 3, 4):
            h.call("def3 4 " + l3(1, 16, [(2, t, comp, 0)]))
            r = h.call("layout 4"); bits, ty = T3[t]
            ck(r[0] == 0 and r[4] == ["AN 1", attr_line(0, 0, 2, SZ[(comp or 1, bits)], ty, 0, 0)], "v3 type %d components %d: %s" % (t, comp, r))
    # layouts: errors of the define ops
    r = h.call("def3 1 " + l3(1, 16, [(0, 0, 3, 0)]).rsplit(" ", 1)[0]); ck(r[1] == 1 and r[3] == "DefineLayout: 9 words, need 10", "def3 short: %s" % (r,))
    for bad in (0, 257):
        r = h.call("def3 %d %s" % (bad, l3(1, 16, [(0, 0, 3, 0)]))); ck(r[1] == 1 and r[3] == "DefineLayout: id %d" % bad, "def3 id %d: %s" % (bad, r))
    r = h.call("def3 256 " + l3(1, 16, [(0, 0, 3, 0)])); ck(r[1] == 0, "def3 id 256 is the last id: %s" % (r,))
    r = h.call("def4 1 " + l4([(0, 0, 3, 0), (0, 0, 3, 0)])); ck(r[1] >= 1 and "DefineLayout4 1" in r[3], "def4 duplicate location is the neutral check's: %s" % (r,))
    ck(h.call("layout 1")[4] == ["AN 0"], "a layout whose define was refused stays undefined")
    r = h.call("def4 2 " + l4([(5000, 0, 3, 0)])); ck(r[1] >= 1 and "attribute 0" in r[3], "def4 offset above the limit: %s" % (r,))
    h.cmd("limits 2048 8000"); r = h.call("def4 2 " + l4([(5000, 0, 3, 0)])); ck(r[1] == 0, "the limit comes from nxdk_chk_env: %s" % (r,)); h.cmd("limits 2048 2047")
    r = h.call("def4 2 " + " ".join(l4([]).split()[:20])); ck(r[1] == 1 and "DefineLayout4: 21 words, need 51" in r[3], "def4 short: %s" % (r,))
    # bind layout: undefined / v3 mapping / errors
    r = h.call("layout 0"); ck(r == (0, 0, 0, "-", ["AN 0"]), "layout 0 binds no attribute: %s" % (r,))
    r = h.call("layout 77"); ck(r == (0, 0, 0, "-", ["AN 0"]), "an undefined layout binds no attribute: %s" % (r,))
    h.call("bindvtxn 0 1 0 16 16"); r = h.call("flush"); ck(r == (0, 0, 0, "-", []), "a draw after an undefined layout binds no stream: %s" % (r,))
    h.call("def3 5 " + l3(3, 20, [(0, 0, 3, 0), (12, 1, 4, 4), (16, 2, 2, 1)]))   # location 0 -> 0; 4 -> 3; 1 -> 0 (the later one wins)
    r = h.call("layout 5"); ck(r[4] == ["AN 4", attr_line(0, 0, 16, SZ[2, 16], SNORM, 0, 0), OFF(1), OFF(2), attr_line(3, 0, 12, SZ[4, 8], UNORM, 0, 0)], "v3 locations: %s" % (r,))
    h.call("def3 6 " + l3(1, 16, [(0, 0, 3, 16)]))
    r = h.call("layout 6"); ck(r[0] == 0 and r[4][0] == "AN 16" and r[4][-1] == attr_line(15, 0, 0, SZ[3, 32], FLOAT, 0, 0), "v3 location 16 is shader location 15: %s" % (r,))
    h.call("def3 6 " + l3(9, 16, [(0, 0, 3, 0)] * 8)); r = h.call("layout 6"); ck(r[0] == 1 and r[1] == 1 and r[3] == "layout 6: 9 attributes" and r[4] == [], "v3 with 9 attributes: %s" % (r,))
    h.call("def3 6 " + l3(1, 16, [(0, 0, 3, 17)])); r = h.call("layout 6"); ck(r[0] == 1 and r[3] == "layout 6: location 16" and r[4] == [], "v3 location 17: %s" % (r,))
    h.call("def3 6 " + l3(1, 16, [(0, 0, 5, 0)])); r = h.call("layout 6"); ck(r[0] == 1 and r[1] == 1 and "layout 6" in r[3] and r[4] == [], "v3 components 5: %s" % (r,))
    h.call("def3 6 " + l3(1, 16, [(0, 9, 3, 0)])); r = h.call("layout 6"); ck(r[0] == 1 and "layout 6" in r[3] and r[4] == [], "v3 attribute type 9: %s" % (r,))
    # a redefinition takes effect at the next bind
    h.call("def4 7 " + l4([(0, 0, 3, 0)])); h.call("layout 7"); h.call("def4 7 " + l4([(8, 0, 1, 2)]))
    ck(h.call("flush")[4][0] == "BN 1", "the bound layout is a copy: a redefinition does not change it")
    r = h.call("layout 7"); ck(r[4] == ["AN 3", OFF(0), OFF(1), attr_line(2, 0, 8, SZ[2, 32], FLOAT, 0, 0)], "the redefinition is bound at the next layout: %s" % (r,))
    # streams: bind errors
    h.cmd("mem 1 256"); h.cmd("mem 2 64")
    r = h.call("bindvtx 1 0"); ck(r[1] == 1 and r[3] == "BindVtx: 2 words, need 3", "bindvtx short: %s" % (r,))
    r = h.call("bindvtx 1 250 10"); ck(r[1] == 1 and r[3] == "BindVtx: range 250+10 outside block 1" and r[2] == 0, "bindvtx past the block: %s" % (r,))
    r = h.call("bindvtx 1 0 257"); ck(r[1] == 1 and r[3] == "BindVtx: range 0+257 outside block 1", "bindvtx one byte past: %s" % (r,))
    r = h.call("bindvtx 1 0xfffffff0 0x20"); ck(r[1] == 1 and r[3] == "BindVtx: range 4294967280+32 outside block 1", "bindvtx wrapping 32 bits: %s" % (r,))
    r = h.call("bindvtx 9 0 16"); ck(r[1] == 1 and r[3] == "BindVtx: range 0+16 outside block 9", "bindvtx of a dead block: %s" % (r,))
    r = h.call("bindvtx 1 240 16"); ck(r[1] == 0 and r[2] == 0, "bindvtx up to the block end: %s" % (r,))
    r = h.call("bindvtxn 1 2 3 4"); ck(r[1] == 1 and r[3] == "BindVtxN: 4 words", "bindvtxn short is the neutral check's: %s" % (r,))
    r = h.call("bindvtxn 16 1 0 16 16"); ck(r[1] == 1 and r[3].startswith("BindVtxN: stream 16"), "bindvtxn stream 16: %s" % (r,))
    r = h.call("bindvtxn 0 1 0 16 2049"); ck(r[1] == 1 and "stride 2049" in r[3], "bindvtxn stride above the limit: %s" % (r,))
    r = h.call("bindvtxn 15 1 250 10 4"); ck(r[1] == 1 and r[3] == "BindVtx: range 250+10 outside block 1", "bindvtxn past the block: %s" % (r,))
    # v3 flush: stride of the layout, snapshot at bind, one staged block
    h.call("def3 1 " + l3(2, 16, [(0, 0, 3, 0), (12, 1, 4, 0)])); h.call("layout 1")
    h.cmd("memfill 1 32 0102030405060708090a0b0c0d0e0f10")
    r = h.call("bindvtx 1 32 40"); snap = mem(0, 256)[32:72]
    h.cmd("memfill 1 32 ffffffffffffffffffffffffffffffff")   # after the bind: the draw reads the bytes of the bind
    h.call("layout 1"); r = h.call("flush")
    exp = bytes.fromhex("0102030405060708090a0b0c0d0e0f10") + mem(0, 256)[48:72]
    ck(r[0] == 0 and r[1] == 0 and r[4] == ["BN 1", "B 0 16 0", X(0, 0, 40, exp)], "v3 flush: %s" % (r,))
    ck(h.call("flush") == (0, 0, 0, "-", []), "a second flush without a change is silent")
    ck(h.live() == 1, "one stage block for one flush")
    h.cmd("memfill 1 32 0102030405060708090a0b0c0d0e0f10")
    h.call("bindvtxn 0 1 0 64 24"); r = h.call("flush")
    ck(r[0] == 0 and r[4] == ["BN 1", "B 0 24 0", X(0, 0, 64, mem(0, 32) + bytes.fromhex("0102030405060708090a0b0c0d0e0f10") + mem(48, 16))], "v3 layout over BindVtxN: the stride of BindVtxN: %s" % (r,))
    h.call("bindvtx 1 0 16"); r = h.call("flush"); ck(r[4][:2] == ["BN 1", "B 0 16 0"], "a v3 layout over BindVtx: the layout's stride: %s" % (r,))
    h.call("bindvtxn 0 0 0 0 0"); r = h.call("flush")
    ck(r == (0, 0, 0, "-", ["BN 1", "B 0 16 0", X(0, 0, 0)]), "a v3 draw without a stream reads nothing (a size-0 extent): %s" % (r,))
    h.call("bindvtxn 0 1 0 0 16"); r = h.call("flush"); ck(r[0] == 0 and r[4] == ["BN 1", "B 0 16 0", X(0, 0, 0)], "a bound empty range: %s" % (r,))
    h.cmd("stagefree"); ck(h.live() == 0, "stagefree frees every block")
    h.call("bindvtx 1 0 16"); h.call("layout 1"); r = h.call("flush"); ck(r[0] == 0 and r[4][2] == X(0, 0, 16, mem(0, 16)), "after the submit's stage free the next layout bind stages again: %s" % (r,))
    # v4: streams, divisors, alignment, errors
    h.cmd("stagefree")
    h.call("def4 8 " + l4([(0, 0, 3, 0), (12, 0, 5, 1), (0, 2, 1, 2)], inst=1 << 2, div=[0, 0, 3] + [0] * 13))
    h.call("bindvtxn 0 1 0 20 20"); h.call("bindvtxn 2 1 100 40 8"); h.call("layout 8"); r = h.call("flush")
    ck(r[0] == 0 and r[1] == 0 and r[4] == ["BN 3", "B 0 20 0", "B 1 0 0", "B 2 8 3", X(0, 0, 20, mem(0, 20)), X(1, 0, 0), X(2, 32, 40, mem(100, 40))],
       "v4 flush: streams 0 and 2, instance divisor, 16-byte alignment, stream 1 empty: %s" % (r,))
    h.cmd("stagefree")
    h.call("bindvtxn 2 0 0 0 0"); h.call("layout 8"); r = h.call("flush")
    ck(r[0] == 1 and r[1] == 1 and r[3] == "draw: layout 8 reads stream 2, which has no BindVtxN" and r[4][:3] == ["BN 3", "B 0 20 0", "B 1 0 0"] and X(2, 0, 0) in r[4],
       "v4 draw over an unbound stream is one error and the rest is bound: %s" % (r,))
    h.call("bindvtx 1 0 16"); h.call("layout 8"); r = h.call("flush")
    ck(r[0] == 1 and r[3] == "draw: layout 8 reads stream 2, which has no BindVtxN", "stream 2 still unbound: %s" % (r,))
    h.call("bindvtx 1 0 16"); h.call("bindvtxn 2 1 0 8 8"); h.call("bindvtxn 0 0 0 0 0")   # BindVtx binds stream 0 as a v3 stream
    h.call("bindvtx 1 0 16"); h.call("layout 8"); r = h.call("flush")
    ck(r[0] == 1 and r[1] == 1 and "reads stream 0" in r[3], "a v4 layout over a BindVtx stream is refused: %s" % (r,))
    h.call("bindvtxn 0 0 0 0 0"); h.call("layout 8"); r = h.call("flush")
    ck(r[0] == 1 and r[1] == 1 and "reads stream 0" in r[3], "a v4 layout over an unbound stream 0: %s" % (r,))
    h.call("bindvtxn 1 0 0 0 0"); h.call("bindvtxn 2 0 0 0 0")
    h.call("def4 9 " + l4([(0, 3, 0, 0), (0, 1, 0, 1)])); h.call("layout 9"); r = h.call("flush")
    ck(r[0] == 1 and r[1] == 2 and "stream 1" in r[3] or r[1] == 2, "two unbound streams are two errors: %s" % (r,))
    # the UDEC3 / DEC3N fix: bits 30-31 := 01 in the staged copy only
    h.cmd("stagefree"); h.cmd("mem 3 64"); h.cmd("memfill 3 0 " + "ff" * 64)
    h.call("def4 10 " + l4([(4, 0, 13, 0), (8, 0, 14, 1), (0, 0, 0, 2), (0, 1, 13, 3)]))
    h.call("bindvtxn 0 3 0 36 16"); h.call("bindvtxn 1 3 0 8 0"); h.call("layout 10"); r = h.call("flush")
    s0 = bytearray(b"\xff" * 36)
    for pos in (4, 20, 8, 24):   # words at offset + k * stride that fit: 4, 20 (36 - 4 = 32 -> 4+16+16 = 36 > 36 - 4: the third does not fit); 8, 24
        s0[pos + 3] = 0x7f   # (0xff & 0x3f) | 0x40 in the top byte: 0x7f
    s1 = bytearray(b"\xff" * 8); s1[3] = 0x7f
    ck(r[0] == 0 and r[4] == ["BN 2", "B 0 16 0", "B 1 0 0", X(0, 0, 36, bytes(s0)), X(1, 48, 8, bytes(s1))], "UDEC3 / DEC3N fix on the staged copy: %s" % (r,))
    h.cmd("memfill 3 0 ffffffff"); ck(h.call("def4 11 " + l4([(0, 0, 4, 0)]))[1] == 0, "unrelated define")
    h.call("layout 11"); h.call("bindvtxn 0 3 0 8 8"); r = h.call("flush")
    ck(r[4][-1] == X(0, 0, 8, b"\xff" * 8), "streams of other types are copied unchanged: %s" % (r,))
    h.call("bindvtxn 0 3 0 4 4"); h.call("def4 12 " + l4([(1, 0, 13, 0)])); h.call("layout 12"); r = h.call("flush")
    ck(r[4][-1] == X(0, 0, 4, b"\xff" * 4), "a word that does not fit in the range is not touched: %s" % (r,))
    # stage failures
    h.cmd("stagefree"); h.cmd("failstage 0"); h.call("bindvtxn 0 3 0 16 16"); h.call("layout 11"); r = h.call("flush")
    ck(r[0] == 1 and r[1] == 1 and "staging" in r[3] and r[4] == [], "a refused stage block fails the flush with the stage's own error: %s" % (r,))
    h.cmd("failstage -1")
    # all 16 streams, the 16th stream and the highest stream bound
    h.cmd("stagefree"); h.cmd("mem 1 256")
    at = [(0, s, 3, s) for s in range(16)]
    h.call("def4 13 " + l4(at))
    for s in range(16): h.call("bindvtxn %d 1 %d 16 16" % (s, s * 4))
    h.call("layout 13"); r = h.call("flush")
    ck(r[0] == 0 and r[4][0] == "BN 16" and [c for c in r[4] if c.startswith("X ")] == [X(s, s * 16, 16, mem(s * 4, 16)) for s in range(16)], "16 streams: %s" % (r,))
    ck(h.live() == 1, "16 streams are one block")
    shutdown_checks(h, ck)
    rc, err = h.close(); ck(rc == 0 and not err.strip(), "host exit %d: %s" % (rc, tail(err, 6)))
    return n[0], f

def host_build(src, exe):
    inc = ["-I" + os.path.join(DK, "dkp", "libnx", "include"), "-I" + os.path.join(DK, "deko3d", "include"), "-I" + NXD, "-I" + BACK, "-I" + os.path.join(ROOT, "tests", "l6", "dkcore", "shim")]
    return sh(["gcc", "-std=gnu11", "-g", "-O1", "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined", "-Wall", "-Wextra", "-Werror"] + inc +
              ['-DVTX_SRC="%s"' % src, os.path.join(HERE, "host_main.c"), os.path.join(BACK, "nxgpu4_check.c"), "-o", exe])

def run_host(src, tag):
    exe = os.path.join(tmp, "host_" + tag); b = host_build(src, exe)
    if b.returncode: return None, ["build: " + tail(b.stderr, 6)]
    try: return scenarios(exe)
    except Exception as e: return None, ["host: " + str(e)[:300]]

n = bad = None
if SRC:
    n, bad = run_host(PROD, "base")
    for b_ in bad or []: check(False, "host run: " + b_)
    if n is not None and not bad: print("checks passed %d/%d (ASan + UBSan)" % (n, n))

# ---- e ------------------------------------------------------------------------------------------------------------------------------------------
MUT = [
    ("undefined layout still bound", "!g_lay[id].defined", "0"),
    ("v3 location limit", "if (loc > 15)", "if (loc > 16)"),
    ("v3 attribute count limit", "l->l3.count > 8", "l->l3.count > 9"),
    ("v3 location base", "l->l3.attr[i].location - 1u", "l->l3.attr[i].location"),
    ("UDEC3 fix dropped", "word = (word & 0x3FFFFFFFu) | 0x40000000u;", "word = word;"),
    ("fix applied to bits 29-30", "0x3FFFFFFFu) | 0x40000000u", "0xBFFFFFFFu) | 0x40000000u"),
    ("range sum in 32 bit", "(uint64_t)off + size > m->size", "off + size > m->size"),
    ("bind not marking dirty", "    g_dirty = 1;\n    v->bound = 0;", "    v->bound = 0;"),
    ("v3 stride from stream always", "v->bound && !v->isV3 ? v->stride : g_cur.l3.stride", "v->stride"),
    ("divisor ignored", "g_cur.l4.divisor[s] : 0", "0 : 0"),
    ("no 16 byte alignment", "total = (total + 15) & ~(uint64_t)15;", ""),
    ("unbound v4 stream not an error", "if (!v->bound || v->isV3) {", "if (0) {"),
    ("layout bind not dirtying", "g_dirty = 1; g_curId = 0;", "g_curId = 0;"),
    ("bgra lost", "DkVtxAttribType_Unorm, 1, 0 }", "DkVtxAttribType_Unorm, 0, 0 }"),
    ("components 0 not 1", "components ? l->l3.attr[i].components : 1u", "components"),
    # nxdk_vtx_shutdown: dropped free, dropped reset of one static, free twice, partial loops
    ("shutdown: free dropped", "free(g_vtx[s].data);", ";"),
    ("shutdown: free dropped, pointers zeroed (LSan leak)", "free(g_vtx[s].data);", "(void)g_vtx[s].data;"),
    ("shutdown: last stream not freed", "s < NX4_MAX_STREAMS; s++) { free", "s + 1 < NX4_MAX_STREAMS; s++) { free"),
    ("shutdown: first stream not freed", "for (uint32_t s = 0; s < NX4_MAX_STREAMS; s++) { free", "for (uint32_t s = 1; s < NX4_MAX_STREAMS; s++) { free"),
    ("shutdown: free twice", "free(g_vtx[s].data);", "free(g_vtx[s].data); if (s == 3) free(g_vtx[s].data);"),
    ("shutdown: g_vtx not reset (dangling copy)", "memset(g_vtx, 0, sizeof g_vtx);", ""),
    ("shutdown: g_vtx pointer nulled only", "memset(g_vtx, 0, sizeof g_vtx);", "for (uint32_t s = 0; s < NX4_MAX_STREAMS; s++) { g_vtx[s].data = NULL; }"),
    ("shutdown: g_lay not reset", "memset(g_lay, 0, sizeof g_lay);", ""),
    ("shutdown: g_cur not reset", "memset(&g_cur, 0, sizeof g_cur);", ""),
    ("shutdown: g_curId not reset", "g_curId = 0; g_dirty = 0;", "g_dirty = 0;"),
    ("shutdown: g_dirty not reset", "g_curId = 0; g_dirty = 0;", "g_curId = 0;"),
    ("shutdown: only half of g_lay reset", "memset(g_lay, 0, sizeof g_lay);", "memset(g_lay, 0, sizeof g_lay / 2);"),
    ("shutdown: g_vtx cap kept (pointer null, cap stale)", "memset(g_vtx, 0, sizeof g_vtx);", "for (uint32_t s = 0; s < NX4_MAX_STREAMS; s++) { free(g_vtx[s].data); g_vtx[s].data = NULL; g_vtx[s].size = 0; g_vtx[s].bound = 0; }"),
]
caught = 0
if "--no-mutations" not in sys.argv and n is not None and not bad:
    for i, (name, a, b) in enumerate(MUT):
        if SRC.count(a) != 1: check(False, "mutation '%s': anchor occurs %d times" % (name, SRC.count(a))); continue
        p = os.path.join(tmp, "mut%d.c" % i); open(p, "w").write(SRC.replace(a, b))
        mn, mbad = run_host(p, "m%d" % i)
        if mn is None or mbad: caught += 1; print("  mutation %-40s caught (%s)" % (name, (mbad or ["?"])[0][:70]))
        else: check(False, "mutation '%s' NOT CAUGHT" % name)
    print("e  mutations caught %d/%d" % (caught, len(MUT)))
    check(caught == len(MUT), "not every mutation caught")
shutil.rmtree(tmp, ignore_errors=True)
print("ALL OK" if not fails else "FAILED %d" % len(fails))
sys.exit(1 if fails else 0)
