#!/usr/bin/env python3
"""test_dkdraw.py: tests of render/d3d9/backend/nx/dk_draw.c (deko3d backend: DefineState, BindState, BindUbo, BindIdx, BindTextures, Draw, DrawIndexed, Viewport,
Scissor, StencilRef, BlendConst, DepthBias, nxdk_draw_neutral).
    python3 -I tests/l6/dkdraw/test_dkdraw.py [--no-mutations]    needs gcc, the devkitA64 cross compiler and libnx / deko3d headers (FNVNX_DK)
 a  static: <= 300 lines, lines <= 160 characters, no stub-class word, no empty function body, no weak symbol
 b  cross build (aarch64, -Werror) by build_dk.sh; the undefined symbols of dk_draw.o are deko3d calls, the other backend files' entry points, nxdk and libc
 c  host run of the real dk_draw.c + dk_state.c under ASan + UBSan against a recording double (host_main.c): exact recorded calls and arguments, every error
    path (one error, nothing recorded), flush ranges, stats, scissor order both ways, neutral forcing
 d  mutations of a copy of the product file: every one must be caught
Output: the check count, mutations caught, ALL OK."""
import os, re, shutil, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
BACK = os.path.join(ROOT, "render", "d3d9", "backend"); NXD = os.path.join(BACK, "nx")
DK = os.environ.get("FNVNX_DK", "/home/user/work/l6a/dk")
NM = os.path.join(DK, "dkp", "devkitA64", "bin", "aarch64-none-elf-nm")
FORBID = re.compile(r"stub|todo|not implemented|placeholder|fallback|no-op|noop|weak", re.I)
OK_UND = re.compile(r"^(dk[A-Z]\w*|nxdk|nxdk_s|nxchk_err|nxdk_mem|nxdk_shader|nxdk_smp|nxdk_img|nxdk_img_view_id|nxdk_vtx_bind_layout|nxdk_vtx_flush|nxdk_pass_srgb|"
                    r"nxdk_state|nxdk_dyn|memcpy|memset|memcmp|strcmp)$")

def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)
def tail(s, n=8): return "\n".join(s.strip().splitlines()[-n:])
fails = []
def check(ok, msg):
    if not ok: fails.append(msg); print("FAIL", msg)

tmp = tempfile.mkdtemp(prefix="dkdraw_")
PATH = os.path.join(NXD, "dk_draw.c")
SRC = open(PATH).read()

# ---- a ------------------------------------------------------------------------------------------------------------------------------------------
check(len(SRC.splitlines()) <= 300, "dk_draw.c is longer than 300 lines")
check(all(len(l) <= 160 for l in SRC.splitlines()), "dk_draw.c has a line longer than 160 characters")
m = FORBID.search(SRC); check(not m, "dk_draw.c contains the forbidden word " + (m.group(0) if m else ""))
check(not re.search(r"\)\s*\{\s*\}", SRC), "dk_draw.c has an empty function body")

# ---- b ------------------------------------------------------------------------------------------------------------------------------------------
out = os.path.join(tmp, "cross")
r = sh(["sh", os.path.join(NXD, "build_dk.sh"), out])
check(r.returncode == 0, "cross build failed: " + tail(r.stderr + r.stdout))
if r.returncode == 0:
    syms = [s for s in sh([NM, "-u", os.path.join(out, "dk_draw.o")]).stdout.split() if s != "U"]
    check(all(OK_UND.match(s) for s in syms), "unexpected undefined symbols: " + " ".join(s for s in syms if not OK_UND.match(s)))
    check({"nxdk_s", "nxchk_err", "nxdk_mem", "nxdk_vtx_flush", "nxdk_state"} <= set(syms), "dk_draw.o does not use the expected entry points: " + " ".join(syms))
    check(not re.search(r"^\S*\s+[Ww]\s", sh([NM, os.path.join(out, "dk_draw.o")]).stdout, re.M), "a weak symbol is present")

# ---- c ------------------------------------------------------------------------------------------------------------------------------------------
def host_build(src, exe):
    inc = ["-I" + os.path.join(DK, "dkp", "libnx", "include"), "-I" + os.path.join(DK, "deko3d", "include"), "-I" + NXD, "-I" + BACK,
           "-I" + os.path.join(ROOT, "tests", "l6", "dkcore", "shim")]
    return sh(["gcc", "-std=gnu11", "-g", "-O1", "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined", "-Wall", "-Wextra", "-Werror"] + inc +
              [os.path.join(HERE, "host_main.c"), src, os.path.join(NXD, "dk_state.c"), "-o", exe])

def run_host(src, tag):
    """(total checks, [problems]); a crash, a sanitizer report or a missing TOTAL line is a problem"""
    exe = os.path.join(tmp, "host_" + tag); b = host_build(src, exe)
    if b.returncode: return None, ["build: " + tail(b.stderr, 6)]
    r = sh([exe]); bad = []; total = None
    for l in r.stdout.splitlines():
        p = l.split()
        if l.startswith("CHK ") and p[-1] == "0": bad.append("check '" + " ".join(p[1:-1]) + "'")
        if l.startswith("TOTAL"): total = int(p[1])
    if r.returncode or total is None: bad.append("host exit %d: %s" % (r.returncode, tail(r.stderr, 6)))
    return total, bad, r.stdout

res = run_host(PATH, "base")
n, bad = res[0], res[1]
for b_ in bad: check(False, "host run: " + b_)
if bad and len(res) > 2: print(tail(res[2], 40))
if n is not None and not bad: print("checks passed %d/%d (ASan + UBSan)" % (n, n))

# ---- d ------------------------------------------------------------------------------------------------------------------------------------------
MUT = [
    ("DefineState argc", "NEED(12, DefineState)", "NEED(11, DefineState)"),
    ("DefineState id bound", '|| a[0] > NXDK_MAX_STATES) { nxdk_err("DefineState', '|| a[0] >= NXDK_MAX_STATES) { nxdk_err("DefineState'),
    ("DefineState layout id unchecked", "(a[1] | a[2] | a[3]) > 0xFFFFu", "(a[1] | a[2]) > 0xFFFFu"),
    ("DefineState high word", "(uint64_t)a[5 + 2 * i] << 32", "(uint64_t)a[4 + 2 * i] << 32"),
    ("DefineState not marked defined", "    s->defined = 1;", "    (void)s;"),
    ("BindState does not count", "    nxdk_s.stats->state_binds++;", ""),
    ("BindState does not count blocks", "    nxdk_s.stats->block_binds++;", ""),
    ("BindState ps not checked", "if (!sh[0] || !sh[1]) {", "if (!sh[0]) {"),
    ("BindState names the wrong shader", "sh[0] ? s->ps : s->vs", "s->vs"),
    ("BindState layout result ignored", "if (nxdk_vtx_bind_layout(s->layout)) return;", "nxdk_vtx_bind_layout(s->layout);"),
    ("clip planes accepted", "if (v->clipPlaneMask) {", "if (0) {"),
    ("flat shading accepted", "if (v->flatShade) {", "if (0) {"),
    ("point sprites accepted", "if (v->pointSprite) {", "if (0) {"),
    ("nxdk_dyn error ignored", "if (!e) e = nxdk_dyn(rs, &zero, samples_now(), &c->d);", ""),
    ("sRGB write not forwarded", "nxdk_pass_srgb(c->s.derived.srgbWrite != 0);", "nxdk_pass_srgb(0);"),
    ("blend states of one target", "dkCmdBufBindBlendStates(cmd, 0, bl, 4);", "dkCmdBufBindBlendStates(cmd, 0, bl, 1);"),
    ("multisample rasterizer mode", "ms.mode = ms.rasterizerMode = c->s.derived.msMode;", "ms.mode = c->s.derived.msMode;"),
    ("stencil masks swapped", "d->stencilWriteMask, d->stencilRef, d->stencilReadMask", "d->stencilReadMask, d->stencilRef, d->stencilWriteMask"),
    ("stencil ref not remembered", "    nxdk_s.stencilRef = d->stencilRef;", ""),
    ("sample mask fixed", "dkCmdBufSetSampleMask(cmd, c->d.sampleMask);", "dkCmdBufSetSampleMask(cmd, 0xFFFF);"),
    ("scissor always on", "set_scissor(cmd, c->s.derived.scissorEnable);", "set_scissor(cmd, 1);"),
    ("scissor test off not honoured", "    if (enabled) {", "    if (1) {"),
    ("scissor origin not clamped", "x0 = nxdk_s.scissor[0] > 0 ? nxdk_s.scissor[0] : 0;", "x0 = nxdk_s.scissor[0];"),
    ("scissor y origin not clamped", "y0 = nxdk_s.scissor[1] > 0 ? nxdk_s.scissor[1] : 0;", "y0 = nxdk_s.scissor[1];"),
    ("scissor not clipped to the pass", "if (nxdk_s.inPass) { x1 =", "if (0) { x1 ="),
    ("scissor sum in 32 bits", "x1 = (int64_t)nxdk_s.scissor[0] + nxdk_s.scissor[2];", "x1 = (int32_t)(nxdk_s.scissor[0] + nxdk_s.scissor[2]);"),
    ("scissor empty rect wraps", "x1 > x0 ? (uint32_t)(x1 - x0) : 0", "(uint32_t)(x1 - x0)"),
    ("scissor height empty rect wraps", "y1 > y0 ? (uint32_t)(y1 - y0) : 0", "(uint32_t)(y1 - y0)"),
    ("Ubo stage mask unchecked", "if (a[0] & ~3u) {", "if (0) {"),
    ("Ubo slot bound", "if (a[1] >= 16 ||", "if (a[1] > 16 ||"),
    ("Ubo size 0", "|| !a[4] ||", "||"),
    ("Ubo size limit", "a[4] > DK_UNIFORM_BUF_MAX_SIZE ||", "a[4] >= DK_UNIFORM_BUF_MAX_SIZE ||"),
    ("Ubo alignment", "a[3] % DK_UNIFORM_BUF_ALIGNMENT)", "0)"),
    ("Ubo flush result ignored", "if (flush(a[2], a[3], a[4])) return;", "flush(a[2], a[3], a[4]);"),
    ("Ubo vertex stage", "if (a[0] & 1) dkCmdBufBindUniformBuffer", "if (a[0] & 3) dkCmdBufBindUniformBuffer"),
    ("Ubo fragment stage", "if (a[0] & 2) dkCmdBufBindUniformBuffer", "if (a[0]) dkCmdBufBindUniformBuffer"),
    ("Ubo offset not in the address", "dkMemBlockGetGpuAddr(m->blk) + a[3];", "dkMemBlockGetGpuAddr(m->blk);"),
    ("Idx type bound", "if (a[3] > 1) {", "if (a[3] > 2) {"),
    ("Idx formats swapped", "dkCmdBufBindIdxBuffer(nxdk_s.cmd, a[3] ? DkIdxFormat_Uint32 : DkIdxFormat_Uint16,",
     "dkCmdBufBindIdxBuffer(nxdk_s.cmd, a[3] ? DkIdxFormat_Uint16 : DkIdxFormat_Uint32,"),
    ("Idx type not remembered", "nxdk_s.idxType = a[3];", ""),
    ("flush range off by one", "off + size > m->size", "off + size >= m->size"),
    ("flush result ignored", "if (dkMemBlockFlushCpuCache(m->blk, (uint32_t)off, (uint32_t)size) != DkResult_Success)", "if (dkMemBlockFlushCpuCache(m->blk, (uint32_t)off, (uint32_t)size) == 7)"),
    ("Textures stage", "a[0] != NxStage_Vertex && a[0] != NxStage_Fragment", "a[0] > 2"),
    ("Textures count in 32 bits", "NEED(3 + (uint64_t)a[2], BindTextures);", "NEED(3 + a[2], BindTextures);"),
    ("Textures slots in 32 bits", "(uint64_t)a[1] + a[2] > 16", "a[1] + a[2] > 16"),
    ("Textures slots bound", "a[2] > 16) { nxdk_err", "a[2] >= 16) { nxdk_err"),
    ("Textures sampler shift", "a[3 + i] >> 20;", "a[3 + i] >> 21;"),
    ("Textures image mask", "a[3 + i] & 0xFFFFF,", "a[3 + i] & 0xFFFFFF,"),
    ("Textures dead image", "|| !nxdk_img(img)) {", ") {"),
    ("Textures sampler not checked", "if (!ss) {", "if (0) {"),
    ("Textures sRGB view", "(ss->filter & NXSMP_SRGB) != 0", "0"),
    ("Textures empty bind", "if (a[2]) dkCmdBufBindTextures", "if (1) dkCmdBufBindTextures"),
    ("Draw without state", 'if (!nxdk_s.boundState) { nxdk_err("Draw:', 'if (0) { nxdk_err("Draw:'),
    ("Draw without pass", 'if (!nxdk_s.inPass) { nxdk_err("Draw:', 'if (0) { nxdk_err("Draw:'),
    ("primitive bound", "p >= sizeof T / sizeof T[0]", "p > sizeof T / sizeof T[0]"),
    ("Draw not counted", "dkCmdBufDraw(nxdk_s.cmd, p, a[1], a[2], a[3], a[4]);\n    nxdk_s.stats->draws++; nxdk_s.frameDraws++;", "dkCmdBufDraw(nxdk_s.cmd, p, a[1], a[2], a[3], a[4]);"),
    ("Draw arguments", "p, a[1], a[2], a[3], a[4]);", "p, a[1], a[2], a[4], a[3]);"),
    ("Draw without vertex flush", "if (prim_of(a[0], &p) || nxdk_vtx_flush()) return;", "if (prim_of(a[0], &p)) return;"),
    ("DrawIndexed without state", 'if (!nxdk_s.boundState) { nxdk_err("DrawIndexed:', 'if (0) { nxdk_err("DrawIndexed:'),
    ("DrawIndexed without pass", 'if (!nxdk_s.inPass) { nxdk_err("DrawIndexed:', 'if (0) { nxdk_err("DrawIndexed:'),
    ("DrawIndexed index size", "nxdk_s.idxType ? 4 : 2;", "nxdk_s.idxType ? 2 : 4;"),
    ("DrawIndexed flush offset", "(uint64_t)a[3] * isz", "(uint64_t)a[3]"),
    ("DrawIndexed flush size", "(uint64_t)a[1] * isz)", "(uint64_t)a[1])"),
    ("DrawIndexed without index rebind",
     "    dkCmdBufBindIdxBuffer(nxdk_s.cmd, nxdk_s.idxType ? DkIdxFormat_Uint32 : DkIdxFormat_Uint16, dkMemBlockGetGpuAddr(m->blk) + nxdk_s.idxOffset);\n", ""),
    ("DrawIndexed arguments", "a[3], (int32_t)a[4], a[5]);", "a[3], (int32_t)a[5], a[4]);"),
    ("DrawIndexed not counted", "a[3], (int32_t)a[4], a[5]);\n    nxdk_s.stats->draws++; nxdk_s.frameDraws++;", "a[3], (int32_t)a[4], a[5]);"),
    ("Viewport value order", "asf(a[0]), asf(a[1]), asf(a[2]), asf(a[3]), asf(a[4]), asf(a[5])", "asf(a[0]), asf(a[1]), asf(a[3]), asf(a[2]), asf(a[4]), asf(a[5])"),
    ("Scissor negative height", "|| (int32_t)a[3] < 0) {", ") {"),
    ("Scissor not recorded", "if (nxdk_s.boundState) set_scissor(", "if (0) set_scissor("),
    ("Scissor test bit ignored", "NXRS_GET(nxdk_s.st[nxdk_s.boundState].rs.w[2], NXRS_R_SCISSOR, 1)", "1"),
    ("StencilRef without state", 'if (!nxdk_s.boundState) { nxdk_err("StencilRef:', 'if (0) { nxdk_err("StencilRef:'),
    ("BlendConst order", "asf(a[0]), asf(a[1]), asf(a[2]), asf(a[3]));", "asf(a[3]), asf(a[1]), asf(a[2]), asf(a[0]));"),
    ("DepthBias order", "asf(a[0]), 0.0f, asf(a[1])", "asf(a[0]), asf(a[1]), 0.0f"),
    ("neutral keeps the bound state", "    record(cmd, &c);\n    nxdk_s.boundState = 0;", "    record(cmd, &c);"),
    ("neutral write mask", "{ { 0xFFFFull << 32, 1ull << NXRS_D_WRITE", "{ { 0xFFull << 32, 1ull << NXRS_D_WRITE"),
    ("neutral without MSAA", "0, 1ull << NXRS_M_MSAA | 0xFFFFFFFFull << NXRS_M_SAMPLEMASK", "0, 0xFFFFFFFFull << NXRS_M_SAMPLEMASK"),
    ("neutral conversion error ignored", 'if (convert("draw_neutral", &rs, &c)) return;', 'convert("draw_neutral", &rs, &c);'),
    ("single sample outside a pass", "nxdk_s.inPass ? nxdk_s.passSamples : 1", "nxdk_s.passSamples"),
]

def mutant(i):
    name, a, b = MUT[i]
    if SRC.count(a) != 1: return name, None, "anchor occurs %d times" % SRC.count(a)
    p = os.path.join(tmp, "mut%d.c" % i); open(p, "w").write(SRC.replace(a, b))
    mres = run_host(p, "m%d" % i)
    return name, (mres[0] is None or bool(mres[1])), (mres[1] or ["?"])[0][:70]

caught = 0
if "--no-mutations" not in sys.argv and n is not None and not bad:
    with ThreadPoolExecutor(max_workers=max(2, (os.cpu_count() or 2))) as ex:
        for name, ok, why in ex.map(mutant, range(len(MUT))):
            if ok is None: check(False, "mutation '%s': %s" % (name, why))
            elif ok: caught += 1; print("  mutation %-40s caught (%s)" % (name, why))
            else: check(False, "mutation '%s' NOT CAUGHT" % name)
    print("d  mutations caught %d/%d" % (caught, len(MUT)))
    check(caught >= 8, "fewer than 8 mutations caught")
shutil.rmtree(tmp, ignore_errors=True)
print("ALL OK" if not fails else "FAILED %d" % len(fails))
sys.exit(1 if fails else 0)
