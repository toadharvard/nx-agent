#!/usr/bin/env python3
"""test_dkshader.py: tests of render/d3d9/backend/nx/dk_shader.c (deko3d backend: SHADER_CREATE, SHADER_CREATE_D3D, SHADER_DESTROY).
    python3 -I tests/l6/dkshader/test_dkshader.py [--no-mutations]    needs gcc, the devkitA64 cross compiler, libnx / deko3d headers (FNVNX_DK, default
                                                                      /home/user/work/l6/dk) and uam (FNVNX_SHADERS_DEPS or UAM) for the real DKSH blobs
 a  static: <= 300 lines, no stub-class word, no empty function body, no weak symbol, nxdk_shader_pack is declared in dk.h and not defined
 b  cross build (aarch64, -Werror) by build_dk.sh; the undefined symbols of dk_shader.o are the embedder hooks, nxdk / nxchk_err, deko3d calls and libc
 c  key equality: the pack builder's key (toolchain/nro/shaders_nx.py: "%016x" % fnv1a64(bytecode), stage) is the key the product looks up; the FNV-1a 64 of the
    translator (render/shaders/src/translate.c, function text compiled here) agrees with both on several inputs
 d  host run of the real product file under ASan + UBSan against a recording double (host_main.c): real DKSH blobs accepted, 14+ corrupted images each
    refused with its own text, boundary images accepted, descriptor errors, pack hit / miss / damaged packs, memo and refcounts, ids, release
 e  mutations of a copy of the product file: every one must be caught
Output: the check count, mutations caught, ALL OK."""
import os, re, shutil, struct, subprocess, sys, tempfile, zlib
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
BACK = os.path.join(ROOT, "render", "d3d9", "backend"); NXD = os.path.join(BACK, "nx")
sys.path.insert(0, os.path.join(ROOT, "toolchain", "nro"))
import shaderpack, shaders_nx   # the pack builder
DK = os.environ.get("FNVNX_DK", "/home/user/work/l6/dk")
NM = os.path.join(DK, "dkp", "devkitA64", "bin", "aarch64-none-elf-nm")
FORBID = re.compile(r"stub|todo|not implemented|placeholder|fallback|no-op|noop|weak", re.I)
BAD_CALL = 0xFFFFFFFFFFFFFF02
OK_UND = re.compile(r"^(dk[A-Z]\w*|nxdk|nxchk_err|nxdk_guest_ptr|nxdk_shader_pack|memset|memcpy|memcmp|strncmp|strcmp|snprintf|vsnprintf|malloc|free)$")

def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)
def tail(s, n=8): return "\n".join(s.strip().splitlines()[-n:])
fails = []
def check(ok, msg):
    if not ok: fails.append(msg); print("FAIL", msg)

tmp = tempfile.mkdtemp(prefix="dkshader_")
SRC = open(os.path.join(NXD, "dk_shader.c")).read()

# ---- a ------------------------------------------------------------------------------------------------------------------------------------------
check(len(SRC.splitlines()) <= 300, "dk_shader.c is longer than 300 lines")
m = FORBID.search(SRC); check(not m, "dk_shader.c contains the forbidden word " + (m.group(0) if m else ""))
check(not re.search(r"\)\s*\{\s*\}", SRC), "dk_shader.c has an empty function body")
check("__attribute__" not in SRC and not re.search(r"nxdk_shader_pack\s*\([^;{]*\)\s*\{", SRC), "dk_shader.c defines the embedder hook or uses an attribute")
check(re.search(r"const void\* nxdk_shader_pack\(uint64_t\* size\);", open(os.path.join(NXD, "dk.h")).read()) is not None, "dk.h does not declare nxdk_shader_pack")

# ---- b ------------------------------------------------------------------------------------------------------------------------------------------
out = os.path.join(tmp, "cross")
r = sh(["sh", os.path.join(NXD, "build_dk.sh"), out])
check(r.returncode == 0, "cross build failed: " + tail(r.stderr + r.stdout))
if r.returncode == 0:
    und = sh([NM, "-u", os.path.join(out, "dk_shader.o")]).stdout.split()
    syms = [s for s in und if s != "U"]
    check({"nxdk_shader_pack", "nxdk_guest_ptr", "nxchk_err", "nxdk"} <= set(syms), "dk_shader.o does not use the hooks: " + " ".join(syms))
    check(all(OK_UND.match(s) for s in syms), "unexpected undefined symbols: " + " ".join(s for s in syms if not OK_UND.match(s)))
    check(not re.search(r"^\S*\s+[Ww]\s", sh([NM, os.path.join(out, "dk_shader.o")]).stdout, re.M), "a weak symbol is present")

# ---- c ------------------------------------------------------------------------------------------------------------------------------------------
def fnv(b): return shaders_nx.fnv1a64(b)
tr = open(os.path.join(ROOT, "render", "shaders", "src", "translate.c")).read()
fm = re.search(r"uint64_t fnvnx_shader_hash\(.*?\n\}\n", tr, re.S)
check(fm is not None, "fnvnx_shader_hash not found in translate.c")
check('"%016x" % h' in open(os.path.join(ROOT, "toolchain", "nro", "shaders_nx.py")).read(), "shaders_nx.py does not key packed rows by \"%016x\" % hash")
inputs = [b"\x00\x03\xff\xff" + bytes(range(i, i + 28)) for i in range(0, 40, 8)] + [b"\x00" * 4, b"\xff" * 64]
g = 0
while True:   # an input whose hash has a zero top nibble: the name keeps its leading zero
    g += 1; cand = struct.pack("<II", 0xFFFF0300, g) + b"fnv"
    if fnv(cand) >> 60 == 0: inputs.append(cand); break
if fm:
    cc = os.path.join(tmp, "tr.c")
    open(cc, "w").write('#include <stdint.h>\n#include <stddef.h>\n#include <stdio.h>\n' + fm.group(0) +
                        'int main(void){unsigned char b[256];size_t n;while(scanf("%zu",&n)==1){for(size_t i=0;i<n;i++){unsigned v=0;if(scanf("%x",&v)!=1)return 1;b[i]=(unsigned char)v;}printf("%016llx\\n",(unsigned long long)fnvnx_shader_hash(b,n));}return 0;}\n')
    b = sh(["gcc", "-O1", "-Wall", "-Werror", cc, "-o", os.path.join(tmp, "tr")])
    check(b.returncode == 0, "translate.c hash build: " + tail(b.stderr))
    if b.returncode == 0:
        inp = "".join("%d %s\n" % (len(x), " ".join("%x" % c for c in x)) for x in inputs)
        got = sh([os.path.join(tmp, "tr")], input=inp).stdout.split()
        check(got == ["%016x" % fnv(x) for x in inputs], "fnvnx_shader_hash differs from shaders_nx.fnv1a64")

# ---- d ------------------------------------------------------------------------------------------------------------------------------------------
blobdir = os.path.join(tmp, "dksh")
env = dict(os.environ); env.setdefault("FNVNX_SHADERS_DEPS", "/home/user/work/l6/build/deps")
r = sh(["bash", os.path.join(BACK, "selftest", "build_dksh.sh"), blobdir], env=env)
check(r.returncode == 0, "build_dksh.sh (real DKSH blobs by uam): " + tail(r.stderr + r.stdout))
if r.returncode: print("FAILED %d" % len(fails)); sys.exit(1)
VS = open(os.path.join(blobdir, "ff_vs.dksh"), "rb").read(); FS = open(os.path.join(blobdir, "ff_fs.dksh"), "rb").read()
MAGIC, CS, KS = 0x48534B44, struct.unpack_from("<I", VS, 8)[0], struct.unpack_from("<I", VS, 12)[0]
consts = os.path.join(tmp, "consts.c")
open(consts, "w").write('#include <stdio.h>\n#include <deko3d.h>\nint main(void){printf("%u %u\\n",(unsigned)(DkMemBlockFlags_CpuUncached|DkMemBlockFlags_GpuCached|DkMemBlockFlags_Code),(unsigned)DK_SHADER_CODE_UNUSABLE_SIZE);return 0;}\n')
sh(["gcc", "-I" + os.path.join(DK, "deko3d", "include"), consts, "-o", os.path.join(tmp, "consts")])
CODE_FLAGS, UNUSABLE = map(int, sh([os.path.join(tmp, "consts")]).stdout.split())

def patch(blob, off, val, fmt="<I"):
    b = bytearray(blob); struct.pack_into(fmt, b, off, val); return bytes(b)
PO = struct.unpack_from("<I", VS, 16)[0]
def wr(name, data):
    p = os.path.join(tmp, name); open(p, "wb").write(data); return p
def build_pack(rows):   # rows: [(hash int, stage, blob)] -> the pack builder's NXSP v1
    return shaderpack.pack_rows([("%016x" % h, "translated", "model", st, bl) for h, st, bl in sorted(rows, key=lambda r: (r[0], r[1]))])[0]
def bytecode(stage, tag): return struct.pack("<II", 0xFFFF0300 if stage else 0xFFFE0300, tag) + bytes(8)

CORRUPT = [   # (name, image, stage, text of the refusal)
    ("short image", VS[:20], 0, "smaller than the 24-byte"),
    ("bad magic", patch(VS, 0, MAGIC ^ 1), 0, "bad magic"),
    ("header_sz 32", patch(VS, 4, 32), 0, "header_sz"),
    ("control_sz 0", patch(VS, 8, 0), 0, "control_sz is zero"),
    ("control_sz unaligned", patch(VS, 8, CS + 1), 0, "control_sz is zero or not a multiple"),
    ("code_sz 0", patch(VS, 12, 0), 0, "code_sz is zero"),
    ("code_sz unaligned", patch(VS, 12, KS + 8), 0, "code_sz is zero or not a multiple"),
    ("code_sz 2 MiB", patch(VS, 12, 0x200000), 0, "above 1 MiB"),
    ("sizes do not add up", VS + bytes(256), 0, "not the image size"),
    ("truncated by one block", VS[:-256], 0, "not the image size"),
    ("no program", patch(VS, 20, 0), 0, "exactly one program"),
    ("two programs", patch(VS, 20, 2), 0, "exactly one program"),
    ("program table past the control section", patch(VS, 16, CS), 0, "program header outside"),
    ("program table unaligned", patch(VS, 16, PO + 2), 0, "program header outside"),
    ("program table inside the header", patch(VS, 16, 8), 0, "program header outside"),
    ("vertex image as fragment", VS, 1, "program type is not the requested stage"),
    ("fragment image as vertex", FS, 0, "program type is not the requested stage"),
    ("program type 2", patch(VS, PO, 2), 0, "program type is not the requested stage"),
    ("entry at the end of the code", patch(VS, PO + 4, KS), 0, "entrypoint"),
    ("entry unaligned", patch(VS, PO + 4, 52), 0, "entrypoint"),
    ("constbuf1 start outside", patch(VS, PO + 12, KS + 4), 0, "constant buffer 1"),
    ("constbuf1 end outside", patch(patch(VS, PO + 12, KS - 4), PO + 16, 8), 0, "constant buffer 1"),
    ("constbuf1 wraps 32 bits", patch(patch(VS, PO + 12, 0xFFFFFFF0), PO + 16, 0x20), 0, "constant buffer 1"),
]
ACCEPT = [("constbuf1 ends at the code end", patch(patch(VS, PO + 12, KS - 4), PO + 16, 4), 0),
          ("entry at the last instruction", patch(VS, PO + 4, KS - 8), 0)]

class Host:
    def __init__(self, exe):
        self.p = subprocess.Popen([exe], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  env=dict(os.environ, ASAN_OPTIONS="detect_leaks=1", UBSAN_OPTIONS="print_stacktrace=1"))
    def cmd(self, line):
        self.p.stdin.write(line + "\n"); self.p.stdin.flush()
        out = self.p.stdout.readline()
        if not out: raise RuntimeError("host died: " + tail(self.p.stderr.read(), 6))
        return out.rstrip("\n")
    def call(self, line):   # -> (ret, errors, oob, message)
        m = re.match(r"R ([0-9a-f]+) E (\d+) O (\d+) M (.*)$", self.cmd(line))
        if not m: raise RuntimeError("bad reply to " + line)
        return int(m.group(1), 16), int(m.group(2)), int(m.group(3)), m.group(4)
    def state(self):
        m = re.match(r"S blocks (\d+) made (\d+) inits (\d+) oob (\d+)", self.cmd("state")); return tuple(map(int, m.groups()))
    def close(self):
        self.p.stdin.close(); rc = self.p.wait(timeout=60); return rc, self.p.stderr.read()

def scenarios(exe):
    """Every scenario of the product against the double: returns (checks, failures)."""
    f, n = [], [0]
    def ck(ok, msg):
        n[0] += 1
        if not ok: f.append(msg)
    h = Host(exe)
    def load(name, data): h.cmd("load %s %s" % (name, wr(name, data)))
    load("vs", VS); load("fs", FS)
    # real blobs
    r = h.call("create dksh=@vs size=%d stage=0" % len(VS)); ck(r[0] == 1 and r[1] == 0 and r[2] == 0, "vs accepted as id 1: %s" % (r,))
    r = h.call("create dksh=@fs size=%d stage=1" % len(FS)); ck(r[0] == 2 and r[1] == 0, "fs accepted as id 2: %s" % (r,))
    ck(h.cmd("lookup 1") == "L 1 0" and h.cmd("lookup 2") == "L 1 4" and h.cmd("lookup 3") == "L 0 -1" and h.cmd("lookup 0") == "L 0 -1", "nxdk_shader(id) stage / dead: %s %s %s %s" % (h.cmd("lookup 1"), h.cmd("lookup 2"), h.cmd("lookup 3"), h.cmd("lookup 0")))
    mb = re.match(r"B size (\d+) flags (\d+) live 1 dev (\w+) data (\w*)", h.cmd("block 0 %d" % KS))
    ck(mb and int(mb.group(1)) == ((KS + UNUSABLE + 4095) & ~4095) and int(mb.group(2)) == CODE_FLAGS and mb.group(3) == "d0", "code block: size rounded to 4 KiB over code + 0x400, Code flag, the device")
    ck(mb and bytes.fromhex(mb.group(4)) == VS[CS:], "code block holds exactly the code section")
    ck(h.state() == (2, 2, 2, 0), "two blocks, two initialisations: %s" % (h.state(),))
    r = h.call("destroy 1"); ck(r[:2] == (0, 0) and h.state()[0] == 1, "destroy frees the code block")
    r = h.call("destroy 1"); ck(r[0] == BAD_CALL and r[1] == 1 and "not a live shader" in r[3], "destroy of a dead id is BAD_CALL: %s" % (r,))
    for bad in ("0", "4097", "99999"):
        r = h.call("destroy " + bad); ck(r[0] == BAD_CALL and r[1] == 1, "destroy %s is BAD_CALL" % bad)
    r = h.call("create dksh=@vs size=%d stage=0" % len(VS)); ck(r[0] == 1, "lowest free id is reused: %s" % (r,))
    h.call("destroy 1"); h.call("destroy 2")
    # corrupted images
    for name, img, stage, text in CORRUPT:
        load("c", img); b0 = h.state()
        r = h.call("create dksh=@c size=%d stage=%d" % (len(img), stage)); b1 = h.state()
        ck(r[0] == 0 and r[1] == 1 and r[2] == 0 and ("shader_create: bad DKSH: " in r[3]) and text in r[3], "corrupt '%s' refused with '%s': %s" % (name, text, r))
        ck(b1[0] == b0[0] and b1[2] == b0[2], "corrupt '%s' left a block or an initialisation" % name)
        h.cmd("unload c")
    for name, img, stage in ACCEPT:
        load("c", img); r = h.call("create dksh=@c size=%d stage=%d" % (len(img), stage)); ck(r[0] != 0 and r[1] == 0, "boundary '%s' accepted: %s" % (name, r))
        h.call("destroy %d" % r[0]); h.cmd("unload c")
    # descriptor errors
    ck(h.call("create dksh=@vs size=%d stage=2" % len(VS))[3].startswith("shader_create: stage 2"), "stage 2")
    ck("reserved" in h.call("create dksh=@vs size=%d stage=0 pad=1" % len(VS))[3], "pad")
    ck("missing" in h.call("create dksh=0 size=%d stage=0" % len(VS))[3], "null dksh")
    ck("missing" in h.call("create dksh=@vs size=0 stage=0")[3], "zero size")
    r = h.call("create dksh=@vs size=%d stage=0" % 0x400001); ck(r[0] == 0 and "above 4 MiB" in r[3] and r[2] == 0, "size above 4 MiB is refused before any read: %s" % (r,))
    r = h.call("create dksh=1 size=%d stage=0" % len(VS)); ck(r[0] == 0 and "not readable" in r[3] and r[2] == 1, "unreadable image: %s" % (r,))
    r = h.call("create dksh=@vs size=%d stage=0" % (len(VS) + 1)); ck(r[0] == 0 and r[2] == 1 and "not readable" in r[3], "range beyond the buffer is not read: %s" % (r,))
    r = h.call("create dksh=@vs size=%d stage=0 desc=1" % len(VS)); ck(r[0] == 0 and "descriptor" in r[3] and r[2] == 1, "unreadable descriptor: %s" % (r,))
    r = h.call("destroy 1"); ck(r[0] == BAD_CALL, "no id left over from the errors")
    h.cmd("failblock 1"); r = h.call("create dksh=@vs size=%d stage=0" % len(VS)); h.cmd("failblock 0")
    ck(r[0] == 0 and "refused a code block" in r[3] and h.state()[0] == 0, "deko3d refusing the block is an error, nothing registered: %s" % (r,))
    # D3D: pack
    bcs = {(s, t): bytecode(s, t) for s in (0, 1) for t in range(3)}
    for k, v in bcs.items(): load("bc%d_%d" % k, v)
    hh = {k: fnv(v) for k, v in bcs.items()}
    def d3d(s, t, **kw):
        a = {"bc": "@bc%d_%d" % (s, t), "size": len(bcs[(s, t)]), "stage": s, "hash": "0x%x" % hh[(s, t)]}; a.update({k: str(v) for k, v in kw.items()})
        return h.call("d3d " + " ".join("%s=%s" % kv for kv in a.items()))
    pk = build_pack([(hh[(0, 0)], 0, VS), (hh[(1, 0)], 1, FS), (hh[(0, 1)], 0, VS), (hh[(1, 2)], 1, FS)]); load("pk", pk); h.cmd("pack pk")
    s0 = h.state(); r = d3d(0, 0); ck(r[0] == 1 and r[1] == 0 and r[2] == 0, "pack hit (vertex): %s" % (r,))
    r = d3d(1, 0); ck(r[0] == 2 and r[1] == 0, "pack hit (pixel): %s" % (r,))
    mb = re.match(r"B size \d+ flags (\d+) live 1 dev \w+ data (\w*)", h.cmd("block %d %d" % (s0[1], KS))); ck(mb and bytes.fromhex(mb.group(2)) == VS[CS:], "pack hit: the pack's code section is in the block: %s" % (mb and mb.group(0)[:60],))
    ck(h.state()[:3] == (2, s0[1] + 2, s0[2] + 2), "pack hit: one block and one initialisation per shader: %s after %s" % (h.state(), s0))
    b0 = h.state(); r = d3d(0, 0); ck(r[0] == 1 and r[1] == 0 and h.state() == b0, "same key: same id, no new block: %s" % (r,))
    r = h.call("destroy 1"); ck(r[0] == 0 and h.cmd("lookup 1") == "L 1 0", "one owner of two left: the shader lives")
    r = h.call("destroy 1"); ck(r[0] == 0 and h.cmd("lookup 1") == "L 0 -1", "the last owner released it")
    r = h.call("destroy 1"); ck(r[0] == BAD_CALL, "a third destroy is BAD_CALL")
    r = d3d(0, 0); ck(r[0] == 1, "a destroyed key is created again under the lowest free id: %s" % (r,))
    # misses and damaged packs
    key = lambda s, t: "shader %016x (stage %d)" % (hh[(s, t)], s)
    r = d3d(1, 1); ck(r[0] == 0 and r[1] == 1 and "shader_create_d3d: translation failed: " + key(1, 1) + ": not in the pack" == r[3], "miss names the key: %s" % (r,))
    r = d3d(0, 2); ck(r[0] == 0 and key(0, 2) in r[3], "miss: the stage is part of the key (row exists for stage 1 only)")
    ck(h.state()[0] == 2, "misses create nothing")
    h.cmd("pack none"); r = d3d(0, 1); ck(r[0] == 0 and "no shader pack is installed" in r[3] and key(0, 1) in r[3], "no pack: %s" % (r,))
    r = d3d(0, 0); ck(r[0] == 1, "no pack: a live key is still answered by the memo")
    def dam(name, data, text, s=0, t=1):
        load(name, data); h.cmd("pack " + name); r = d3d(s, t)
        ck(r[0] == 0 and r[1] == 1 and text in r[3] and (key(s, t) in r[3] or "bad DKSH" in text) and r[2] == 0, "pack '%s' refused with '%s': %s" % (name, text, r)); h.cmd("pack pk")
    dam("p1", b"NXSX" + pk[4:], "not an NXSP v1"); dam("p2", pk[:4] + struct.pack("<I", 2) + pk[8:], "not an NXSP v1")
    dam("p3", patch(pk, 8, 100000), "not an NXSP v1"); dam("p4", pk[:16], "no shader pack"); dam("p5", patch(pk, 16, len(pk) + 256), "not an NXSP v1")
    dam("p6", patch(pk, 12, 8), "not an NXSP v1")
    rows = [struct.unpack_from("<48s24s16sIIII", pk, 32 + i * 104) for i in range(4)]; i01 = [i for i, r_ in enumerate(rows) if r_[0].rstrip(b"\0").decode() == "%016x" % hh[(0, 1)]][0]
    ro = 32 + i01 * 104
    dam("p7", patch(pk, ro + 92, 1 << 30), "outside the pack"); dam("p8", patch(pk, ro + 92, rows[i01][4] + 1), "outside the pack")
    dam("p9", patch(pk, ro + 96, 0), "outside the pack"); dam("p10", patch(pk, ro + 100, rows[i01][6] ^ 1), "checksum")
    dam("p11", patch(pk, rows[i01][4] + 5, pk[rows[i01][4] + 5] ^ 1, "<B"), "checksum")
    dam("p12", build_pack([(hh[(0, 1)], 0, VS), (hh[(0, 1)], 0, VS)]), "holds the key twice")
    dam("p13", shaderpack.pack_rows([("%016x" % hh[(0, 1)], "translated-glsl", "", 0, VS)])[0], "not in the pack")
    dam("p14", shaderpack.pack_rows([("%016x" % hh[(0, 1)] + "0", "translated", "", 0, VS)])[0], "not in the pack")
    dam("p15", build_pack([(hh[(0, 1)], 1, VS)]), "not in the pack")
    dam("p16", build_pack([(hh[(0, 1)], 0, patch(VS, 0, MAGIC ^ 1))]), "bad DKSH: bad magic")
    dam("p17", build_pack([(hh[(0, 1)], 0, FS)]), "bad DKSH: program type")
    ck(h.state()[0] == 2, "damaged packs create nothing")
    # D3D: descriptor errors
    r = d3d(0, 1, hash="0x1"); ck(r[0] == 0 and "FNV-1a 64" in r[3] and r[1] == 1, "wrong hash: %s" % (r,))
    for kw, text in ((dict(stage=2), "stage is neither"), (dict(pad0=1), "reserved"), (dict(pad2=1), "reserved"), (dict(size=0), "bytecode size"), (dict(size=6), "bytecode size"),
                     (dict(size=0x100004), "bytecode size"), (dict(variant=2), "variant"), (dict(nl=32), "more than 31"), (dict(bc=1), "unreadable"), (dict(desc=1), "descriptor"),
                     (dict(variant=1, stage=1), "variant")):
        r = d3d(0, 1, **kw); ck(r[0] == 0 and r[1] == 1 and text in r[3], "d3d %s refused with '%s': %s" % (kw, text, r))
    r = d3d(0, 1, variant=1); ck(r[0] == 0 and "NXSP v1 has no VS_COMPLETE or link-set field: variant 1 with 0 link pairs" in r[3], "variant 1 is refused by name (pack gap): %s" % (r,))
    load("lp1", b"\x10"); load("lp2", b"\x20\x10"); load("lp3", b"\xe0")
    r = d3d(0, 1, nl=1, lp="@lp1"); ck(r[0] == 0 and "link-set field" in r[3] and "1 link pairs" in r[3], "link pairs are refused by name: %s" % (r,))
    r = d3d(0, 1, nl=2, lp="@lp2"); ck(r[0] == 0 and "ascending" in r[3], "unsorted link pairs: %s" % (r,))
    r = d3d(0, 1, nl=1, lp="@lp3"); ck(r[0] == 0 and "ascending" in r[3], "usage above 13: %s" % (r,))
    r = d3d(0, 1, nl=1, lp="1"); ck(r[0] == 0 and "unreadable" in r[3] and r[2] >= 1, "unreadable link set: %s" % (r,))
    ck(h.state()[0] == 2, "descriptor errors create nothing")
    # ids: exhaustion and release
    h.cmd("release"); ck(h.state()[0] == 0 and h.cmd("lookup 1") == "L 0 -1", "release_all destroys every block")
    ids = [h.call("create dksh=@fs size=%d stage=1" % len(FS))[0] for _ in range(4096)]
    ck(ids == list(range(1, 4097)), "4096 ids, lowest free first")
    r = h.call("create dksh=@fs size=%d stage=1" % len(FS)); ck(r[0] == 0 and "table is full" in r[3] and h.state()[0] == 4096, "id 4097 is refused: %s" % (r,))
    h.call("destroy 77"); r = h.call("create dksh=@fs size=%d stage=1" % len(FS)); ck(r[0] == 77, "the freed id 77 is the next: %s" % (r,))
    h.cmd("release")
    rc, err = h.close(); ck(rc == 0 and not err.strip(), "host exit %d: %s" % (rc, tail(err, 6)))
    return n[0], f

def host_build(src, exe):
    inc = ["-I" + os.path.join(DK, "dkp", "libnx", "include"), "-I" + os.path.join(DK, "deko3d", "include"), "-I" + NXD, "-I" + BACK, "-I" + os.path.join(ROOT, "tests", "l6", "dkcore", "shim")]
    return sh(["gcc", "-std=gnu11", "-g", "-O1", "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined", "-Wall", "-Wextra", "-Werror"] + inc +
              [os.path.join(HERE, "host_main.c"), src, "-o", exe])

def run_host(src, tag):
    exe = os.path.join(tmp, "host_" + tag); b = host_build(src, exe)
    if b.returncode: return None, ["build: " + tail(b.stderr, 6)]
    try: return scenarios(exe)
    except Exception as e: return None, ["host: " + str(e)[:300]]

n, bad = run_host(os.path.join(NXD, "dk_shader.c"), "base")
for b_ in bad or []: check(False, "host run: " + b_)
if n is not None and not bad: print("checks passed %d/%d (ASan + UBSan)" % (n, n))

# ---- e ------------------------------------------------------------------------------------------------------------------------------------------
MUT = [
    ("magic not checked", 'if (rd32(d) != 0x48534B44u) return "bad magic (not DKSH)";', ""),
    ("header_sz not checked", "if (hsz != HDR_SZ)", "if (0)"),
    ("control_sz alignment", "(cs & (DK_SHADER_CODE_ALIGNMENT - 1))", "0"),
    ("code_sz limit", "if (ks > MAX_CODE)", "if (0)"),
    ("sizes need not add up", "if ((uint64_t)cs + ks != n)", "if (0)"),
    ("program count", "if (np != 1)", "if (np > 1)"),
    ("program table bound", "(uint64_t)po + PROG_SZ > cs", "(uint64_t)po + PROG_SZ > cs + 1"),
    ("stage not compared", "if (rd32(p) != stage)", "if (0)"),
    ("entry bound off by one", "if (entry >= ks ||", "if (entry > ks ||"),
    ("constbuf bound off by one", "if ((uint64_t)cb + cbsz > ks)", "if ((uint64_t)cb + cbsz >= ks)"),
    ("constbuf sum in 32 bits", "if ((uint64_t)cb + cbsz > ks)", "if ((uint32_t)(cb + cbsz) > ks)"),
    ("code block without the unusable tail", "ks + DK_SHADER_CODE_UNUSABLE_SIZE +", "ks +"),
    ("code copied from the image start", "memcpy(dkMemBlockGetCpuAddr(blk), d + cs, ks)", "memcpy(dkMemBlockGetCpuAddr(blk), d, ks)"),
    ("Code flag dropped", " | DkMemBlockFlags_Code;", ";"),
    ("id search not lowest free", "uint32_t id = 1;", "uint32_t id = 2;"),
    ("destroy of a dead id", "!g_sh[id].used) {", "0) {"),
    ("destroy frees at the first owner", "if (--g_sh[id].refs) return 0;", "if (g_sh[id].refs--, 0) return 0;"),
    ("memo ignores the stage", " && g_sh[i].stage == d.stage", ""),
    ("memo does not count the owner", "g_sh[i].refs++; id = i;", "id = i;"),
    ("hash not checked", "else if (h != d.hash)", "else if (0)"),
    ("variant / link pairs answered", "else if (d.variant || d.n_link_pairs)", "else if (0)"),
    ("pack stage ignored", " || rd32(r + 88) != stage) continue;", ") continue;"),
    ("pack family ignored", '|| strncmp((const char*)r + 48, "translated", 24)', ""),
    ("pack checksum ignored", "if (crc32(pk + off, sz) != rd32(hit + 100))", "if (0)"),
    ("duplicate key ignored", 'if (hit) { *why = "the pack holds the key twice"; return NULL; }', ""),
    ("pack row offset unchecked", "(off & 255) || off < dat ||", ""),
    ("pack header unchecked", 'memcmp(pk, "NXSP", 4) ||', ""),
    ("key name without zero padding", 'snprintf(name, sizeof name, "%016llx", (unsigned long long)hash);', 'snprintf(name, sizeof name, "%llx", (unsigned long long)hash);'),
    ("FNV prime", "h *= 0x100000001b3ull;", "h *= 0x100000001b5ull;"),
    ("block leaked at failure", "dkMemBlockDestroy(blk); memset(s, 0, sizeof *s);", "memset(s, 0, sizeof *s);"),
    ("table size", "while (id <= NXDK_MAX_SHADERS && g_sh[id].used) id++;", "while (id < NXDK_MAX_SHADERS && g_sh[id].used) id++;"),
    ("release_all keeps the blocks", "if (g_sh[id].used) dkMemBlockDestroy(g_sh[id].blk);", "if (g_sh[id].used) (void)id;"),
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
    check(caught >= 8, "fewer than 8 mutations caught")
shutil.rmtree(tmp, ignore_errors=True)
print("ALL OK" if not fails else "FAILED %d" % len(fails))
sys.exit(1 if fails else 0)
