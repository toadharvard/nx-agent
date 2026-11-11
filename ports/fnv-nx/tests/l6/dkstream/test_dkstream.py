#!/usr/bin/env python3
"""test_dkstream.py: tests of render/d3d9/backend/nx/dk_stream.c (deko3d backend: SUBMIT / WAIT_FRAME, the stream executor, the checker environment)
    python3 -I tests/l6/dkstream/test_dkstream.py [--no-mutations]     needs gcc, the devkitA64 cross compiler and libnx / deko3d headers (FNVNX_DK)
 a  static: dk_stream.c <= 300 lines, lines <= 160 chars, no stub-class words, no empty function body, no attribute
 b  cross build (aarch64, -Werror) by build_dk.sh; dk_stream.o defines exactly the handlers of its ops, SUBMIT / WAIT_FRAME and the checker environment
 c  host run of the real dk_stream.c + nxgpu4_check.c under ASan + UBSan (no recovery) against a recording double of the deko3d calls and of the other
    files' handlers (host_main.c): every check line 1, the exact number of checks, the named checks present
 d  mutations of copies of the product file: every one must be caught
Output: the check count, mutations caught, ALL OK."""
import os, re, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
BACK = os.path.join(ROOT, "render", "d3d9", "backend"); NXD = os.path.join(BACK, "nx")
DK = os.environ.get("FNVNX_DK", "/home/user/work/l6/dk")
FILES = ["dk_stream.c"]
EXPECT_CHECKS = 157
MUST_NAMES = ["init_ok", "init_first_chunk", "shutdown_order", "init_cmdbuf_fail_reported", "init_block_fail_reported", "init_scale_fail_returned", "submit_ok",
              "submit_start_barrier", "submit_order", "submit_recycles_buffer", "stats_written", "cmd_published_only_inside", "present_flag_ignored",
              "wait_frame_zero", "map_op_1", "map_op_27", "map_s6_reset", "map_image_update_routed", "chk_swap_colour_ok", "env_limits", "env_swap_resolves_front",
              "stop_later_ops_skipped", "stop_recorded_still_submitted", "barrier_kind1", "marker_short", "iu_good", "iu_short", "iu_box_rejected_once",
              "grow_chunk_sizes", "grow_released_after_wait", "grow_too_big_reported", "grow_block_refused_reported", "grow_listed_released_at_shutdown",
              "frame_draws_reset_new_frame", "submit_null_struct", "submit_bad_stream_address", "opcode_28_reported", "overrun_reported", "no_end_reported"]
HANDLERS = {"nxdk_submit", "nxdk_wait_frame", "nxdk_stream_init", "nxdk_stream_shutdown", "nxdk_chk_env", "nxdk_op_end", "nxdk_op_barrier", "nxdk_op_marker",
            "nxdk_op_image_update", "nxdk_s"}
FORBID = re.compile(r"stub|todo|not implemented|placeholder|fallback|no-op|noop|weak", re.I)

fails = []
def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)
def check(ok, msg):
    if not ok: fails.append(msg); print("FAIL", msg)
def tail(s, n=12): return "\n".join(s.strip().splitlines()[-n:])

# ---- a ------------------------------------------------------------------------------------------------------------------------------------------
src = open(os.path.join(NXD, "dk_stream.c")).read()
check(len(src.splitlines()) <= 300, "dk_stream.c is longer than 300 lines")
check(all(len(l) <= 160 for l in src.splitlines()), "dk_stream.c has a line longer than 160 characters")
m = FORBID.search(src); check(not m, "dk_stream.c contains the forbidden word " + (m.group(0) if m else ""))
check(not re.search(r"\)\s*\{\s*\}", src) and "__attribute__" not in src, "dk_stream.c has an empty function body or an attribute")
if fails: print("FAILED %d" % len(fails)); sys.exit(1)

# ---- b ------------------------------------------------------------------------------------------------------------------------------------------
tmp = tempfile.mkdtemp(prefix="dkstream_")
out = os.path.join(tmp, "cross")
r = sh(["sh", os.path.join(NXD, "build_dk.sh"), out])
check(r.returncode == 0, "cross build failed: " + tail(r.stderr + r.stdout))
if r.returncode == 0:
    nm = sh([os.path.join(DK, "dkp", "devkitA64", "bin", "aarch64-none-elf-nm"), "-C", os.path.join(out, "dk_stream.o")]).stdout
    dfn = {l.split()[-1] for l in nm.splitlines() if len(l.split()) == 3 and l.split()[1] in "TtDdBbRrCc"}
    glob = {l.split()[-1] for l in nm.splitlines() if len(l.split()) == 3 and l.split()[1] in "TDBR"}
    check(glob == HANDLERS, "dk_stream.o defines %s, expected %s" % (sorted(glob ^ HANDLERS), sorted(HANDLERS)))
    check(not re.search(r"^\S*\s+[Ww]\s", nm, re.M), "a weak symbol is present")

# ---- c ------------------------------------------------------------------------------------------------------------------------------------------
def host(srcdir, exe):
    inc = ["-I" + os.path.join(HERE, "shim"), "-I" + os.path.join(DK, "dkp", "libnx", "include"), "-I" + os.path.join(DK, "deko3d", "include"),
           "-I" + NXD, "-I" + BACK]
    cmd = ["gcc", "-std=gnu11", "-g", "-O1", "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined", "-Wall", "-Wextra", "-Werror"] + inc + \
          [os.path.join(HERE, "host_main.c"), os.path.join(BACK, "nxgpu4_check.c")] + [os.path.join(srcdir, f) for f in FILES] + ["-o", exe]
    return sh(cmd)

def run_host(srcdir, tag):
    exe = os.path.join(tmp, "host_" + tag)
    b = host(srcdir, exe)
    if b.returncode: return None, "build: " + tail(b.stderr, 6)
    r = sh([exe], env=dict(os.environ, ASAN_OPTIONS="detect_leaks=1", UBSAN_OPTIONS="print_stacktrace=1"), timeout=120)
    if r.returncode: return None, "exit %d: %s" % (r.returncode, tail(r.stderr, 6))
    chk = {}; n = 0
    for line in r.stdout.splitlines():
        m = re.match(r"CHK (\S+) ([01])$", line)
        if m: chk[m.group(1)] = chk.get(m.group(1), 1) & int(m.group(2))
        m = re.match(r"NCHK (\d+)$", line)
        if m: n = int(m.group(1))
    bad = [k for k, v in chk.items() if not v]
    if bad: return None, "failed checks: " + " ".join(bad[:8])
    if n != EXPECT_CHECKS or len(chk) != n: return None, "check count %d (%d names), expected %d" % (n, len(chk), EXPECT_CHECKS)
    miss = [k for k in MUST_NAMES if k not in chk]
    if miss: return None, "missing checks: " + " ".join(miss[:8])
    return n, ""

n, why = run_host(NXD, "base")
check(n is not None, "host run: " + why)
if n is not None: print("checks passed %d/%d (ASan + UBSan)" % (n, n))

# ---- d ------------------------------------------------------------------------------------------------------------------------------------------
MUT = [
    ("command memory flags", "dk_stream.c", "DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached;", "DkMemBlockFlags_CpuUncached;"),
    ("chunk rounding dropped", "dk_stream.c", "((uint32_t)min + DK_MEMBLOCK_ALIGNMENT - 1) / DK_MEMBLOCK_ALIGNMENT * DK_MEMBLOCK_ALIGNMENT", "(uint32_t)min"),
    ("huge request accepted", "dk_stream.c", "min > UINT32_MAX - NXDK_STREAM_CHUNK", "0"),
    ("added chunk not listed", "dk_stream.c", "    c->next = extra;\n    extra = c;\n", ""),
    ("clear after the release", "dk_stream.c", "    dkCmdBufClear(rec);\n    while (extra) {", "    while (extra) {"),
    ("first chunk not re-added", "dk_stream.c", "    dkCmdBufAddMemory(rec, firstBlk, 0, NXDK_STREAM_CHUNK);\n}", "}"),
    ("scale shutdown skipped", "dk_stream.c", "if (scaleInit) nxdk_scale_shutdown();", ""),
    ("first chunk leaked", "dk_stream.c", "    if (firstBlk) dkMemBlockDestroy(firstBlk);\n", ""),
    ("extra chunks leaked at shutdown", "dk_stream.c", "    if (rec) dkCmdBufDestroy(rec);\n    while (extra) {", "    if (rec) dkCmdBufDestroy(rec);\n    while (0) {"),
    ("scale init result dropped", "dk_stream.c", "return nxdk_scale_init();", "nxdk_scale_init(); return 0;"),
    ("swapchain id not substituted", "dk_stream.c", "e.swapImage = NXDK_SWAP_ID;", "e.swapImage = 0;"),
    ("swap id not resolved", "dk_stream.c", "nxdk_img(id == NXDK_SWAP_ID ? 0 : id)", "nxdk_img(id)"),
    ("depth flag dropped", "dk_stream.c", "out->isDepth = (im->flags & NxImg_Depth) != 0;", "out->isDepth = 0;"),
    ("compressed flag dropped", "dk_stream.c", "out->compressed = (dk_fmt(im->fmt).flags & DKFMT_COMPRESSED) != 0;", "out->compressed = 0;"),
    ("view flag dropped", "dk_stream.c", "out->isView = (uint8_t)im->isView;", "out->isView = 0;"),
    ("stride limit 13 bits", "dk_stream.c", "NXDK_MAX_VTX_STRIDE = 0xFFF", "NXDK_MAX_VTX_STRIDE = 0x1FFF"),
    ("attribute offset limit 15 bits", "dk_stream.c", "NXDK_MAX_ATTR_OFFSET = 0x3FFF", "NXDK_MAX_ATTR_OFFSET = 0x7FFF"),
    ("barrier kind unchecked", "dk_stream.c", "    if (a[0] != 0) { nxdk_err(\"Barrier: kind %u\", a[0]); return; }\n", ""),
    ("barrier flags", "dk_stream.c", "DkBarrier_Fragments, DkInvalidateFlags_Image)", "DkBarrier_Full, DkInvalidateFlags_Image)"),
    ("marker length unchecked", "dk_stream.c", "if (argc < 3) nxdk_err(\"Marker", "if (argc < 2) nxdk_err(\"Marker"),
    ("image update views accepted", "dk_stream.c", "im->isView || ", ""),
    ("image update multisampled accepted", "dk_stream.c", " || im->samples != 1", ""),
    ("image zero accepted", "dk_stream.c", "a[0] ? nxdk_img(a[0]) : NULL", "nxdk_img(a[0])"),
    ("box check dropped", "dk_stream.c", "    if (nxdk_box_check(im, \"ImageUpdate\", level, layer, box)) return;\n", ""),
    ("pitch below row accepted", "dk_stream.c", " && pitch >= rb", ""),
    ("source size below need accepted", "dk_stream.c", " && need <= a[10]", ""),
    ("source range in 32 bits", "dk_stream.c", "(uint64_t)a[9] + a[10] <= m->size", "a[9] + a[10] <= m->size"),
    ("layer dropped", "dk_stream.c", "const uint32_t level = a[1] & 0xFFFF, layer = a[1] >> 16,", "const uint32_t level = a[1] & 0xFFFF, layer = 0,"),
    ("unknown opcode accepted", "dk_stream.c", "op >= NxOp_Count4 || !k_op[op]", "!k_op[op]"),
    ("overrun check off by one", "dk_stream.c", "w + 1 + argc > end", "w + argc > end"),
    ("no-End stream accepted", "dk_stream.c", "    nxdk_err(\"stream: no End op\");\n    return 0;", "    return 1;"),
    ("replay continues after an error", "dk_stream.c", "if (nxdk.errCount != errs) return;", ""),
    ("pass state not reset", "dk_stream.c", "nxdk_s.boundState = 0; nxdk_s.inPass = 0;", "nxdk_s.boundState = 0;"),
    ("cmd published after submit", "dk_stream.c", "    nxdk_s.cmd = NULL;\n    recycle();", "    recycle();"),
    ("start barrier dropped", "dk_stream.c", "    dkCmdBufBarrier(rec, DkBarrier_Full, DkInvalidateFlags_Descriptors | DkInvalidateFlags_Shader | DkInvalidateFlags_Image);\n", ""),
    ("descriptors not bound", "dk_stream.c", "    nxdk_bind_descriptors(rec);\n", ""),
    ("no wait for the queue", "dk_stream.c", "    dkQueueWaitIdle(nxdk.q);\n", ""),
    ("staging released before the wait", "dk_stream.c", "    dkQueueSubmitCommands(nxdk.q, dkCmdBufFinishList(rec));\n    dkQueueWaitIdle(nxdk.q);\n    nxdk_query_collect();\n    nxdk_stage_free();\n",
     "    nxdk_stage_free();\n    dkQueueSubmitCommands(nxdk.q, dkCmdBufFinishList(rec));\n    dkQueueWaitIdle(nxdk.q);\n    nxdk_query_collect();\n"),
    ("queries not collected", "dk_stream.c", "    nxdk_query_collect();\n", ""),
    ("query begin dropped", "dk_stream.c", "    nxdk_query_submit_begin();\n", ""),
    ("query end dropped", "dk_stream.c", "    nxdk_query_submit_end();\n", ""),
    ("stats not published", "dk_stream.c", "        nxdk_s.stats = &st;\n", ""),
    ("frame draws never reset", "dk_stream.c", "nxdk_s.drawsFrame != (uint64_t)s->frame + 1", "0"),
    ("frame wraps at 32 bits", "dk_stream.c", "nxdk_s.drawsFrame = (uint64_t)s->frame + 1;", "nxdk_s.drawsFrame = s->frame + 1;"),
    ("stream length in words", "dk_stream.c", "(uint64_t)s->words * 4)", "(uint64_t)s->words)"),
    ("stream length in 32 bits", "dk_stream.c", "(uint64_t)s->words * 4)", "s->words * 4)"),
    ("stats pointer unchecked", "dk_stream.c", "if (out) memcpy(out, &st, sizeof st); else nxdk_err(\"submit: stats address\");", "memcpy(out, &st, sizeof st);"),
    ("null struct silent", "dk_stream.c", "if (!s) { nxdk_err(\"submit: struct address\"); return NXGPU_BAD_CALL; }", "if (!s) return NXGPU_BAD_CALL;"),
    ("result ignores errors", "dk_stream.c", "return nxdk.errCount != errs ? NXGPU_BAD_CALL : 0;", "return 0;"),
    ("checker skipped", "dk_stream.c", " && nxchk_stream(nxdk_chk_env(), w, w + s->words)", ""),
    ("cmd words not recorded", "dk_stream.c", "st.frame = s->frame; st.cmd_words = s->words;", "st.frame = s->frame;"),
    ("wait frame nonzero", "dk_stream.c", "uint64_t nxdk_wait_frame(uint64_t f) { (void)f; return 0; }", "uint64_t nxdk_wait_frame(uint64_t f) { (void)f; return 1; }"),
    ("op table entry swapped", "dk_stream.c", "#define OPFN_Scissor nxdk_op_scissor", "#define OPFN_Scissor nxdk_op_viewport"),
    ("op table End swapped", "dk_stream.c", "#define OPFN_Clear nxdk_op_clear", "#define OPFN_Clear nxdk_op_blit"),
]
caught = 0
if "--no-mutations" not in sys.argv and n is not None:
    for i, (name, f, a, b) in enumerate(MUT):
        d = os.path.join(tmp, "mut%d" % i); os.makedirs(d)
        for g in FILES: shutil.copy(os.path.join(NXD, g), d)
        txt = open(os.path.join(d, f)).read()
        if txt.count(a) != 1:
            check(False, "mutation '%s': anchor occurs %d times" % (name, txt.count(a))); continue
        open(os.path.join(d, f), "w").write(txt.replace(a, b))
        m, why = run_host(d, "m%d" % i)
        if m is None: caught += 1; print("  caught: %s (%s)" % (name, why.splitlines()[0][:90]))
        else: check(False, "mutation not caught: " + name)
    print("mutations caught %d/%d" % (caught, len(MUT)))
shutil.rmtree(tmp, ignore_errors=True)
if fails: print("FAILED %d" % len(fails)); sys.exit(1)
print("ALL OK")
