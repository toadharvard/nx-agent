#!/usr/bin/env python3
"""test_dkquery.py: tests of render/d3d9/backend/nx/dk_query.c (deko3d backend: QueryBegin / QueryEnd / query_result, counter slices).
    python3 -I tests/l6/dkquery/test_dkquery.py [--no-mutations]    needs gcc, the devkitA64 cross compiler and libnx / deko3d headers (FNVNX_DK,
                                                                    default /home/user/work/l6/dk)
 a  static: the file is <= 300 lines, lines <= 160 chars, no stub-class word, no empty function body, no weak symbol
 b  cross build (aarch64, -Werror) by build_dk.sh; dk_query.o defines the handlers and calls only dk* library calls, libc and the contract symbols
 c  host run of the real product file under ASan + UBSan (no recovery, leak check) against a recording double of the deko3d calls and of the
    staging allocator (host_main.c): the exact recorded reset/report sequences and record addresses, exact sums from scripted counter values
    (sequential, nested, overlapping, across submits, pause/resume, restart, 64 bit, 1 to 5 record blocks), Event, Timestamp, every error path
 d  mutations of a copy of the product file: every one must be caught
Output: the check count, mutations caught, ALL OK."""
import os, re, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
BACK = os.path.join(ROOT, "render", "d3d9", "backend"); NXD = os.path.join(BACK, "nx")
DK = os.environ.get("FNVNX_DK", "/home/user/work/l6/dk")
NM = os.path.join(DK, "dkp", "devkitA64", "bin", "aarch64-none-elf-nm")
SRC = "dk_query.c"
EXPECT_CHECKS = 53
HANDLERS = {"nxdk_op_query_begin", "nxdk_op_query_end", "nxdk_query_result", "nxdk_query_submit_begin", "nxdk_query_submit_end", "nxdk_query_collect",
            "nxdk_query_pause", "nxdk_query_resume", "nxdk_query_shutdown"}
UNDEF_OK = {"nxchk_err", "nxdk_guest_ptr", "nxdk_stage_alloc", "nxdk_s", "malloc", "realloc", "free", "memset"}
LIBCALL = re.compile(r"^dk(CmdBuf|MemBlock)[A-Z]\w*$")
FORBID = re.compile(r"stub|todo|not implemented|placeholder|fallback|no-op|noop|weak", re.I)
MUST_NAMES = ["seq_events", "seq_q1", "seq_q2", "nest_events", "nest_sums", "overlap_sums", "xsub_events_1", "xsub_events_2", "xsub_sum", "pause_events",
              "pause_sum", "redefine_restarts", "redefine_to_event", "acc_kept", "event_result", "ts_events", "ts_last_end_wins", "ts_kept", "sum_64bit",
              "blocks_4096_one", "blocks_4200_two", "blocks_4200_sums", "blocks_grow_table", "err_begin_argc", "err_end_argc", "err_begin_id0",
              "err_begin_kind", "err_end_unbegun", "err_end_inactive", "err_res_id0", "err_res_wait2", "err_res_null_out", "err_stage_alloc_end",
              "err_ids_malloc", "err_end_keeps_active", "err_begin_close_fails", "err_begin_not_begun", "err_slice_realloc", "err_chunk_realloc", "shutdown_forgets", "after_shutdown_works"]

fails = []
def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)
def check(ok, msg):
    if not ok: fails.append(msg); print("FAIL", msg)
def tail(s, n=12): return "\n".join(s.strip().splitlines()[-n:])

# ---- a ------------------------------------------------------------------------------------------------------------------------------------------
txt = open(os.path.join(NXD, SRC)).read()
check(len(txt.splitlines()) <= 300, SRC + " is longer than 300 lines")
check(all(len(l) <= 160 for l in txt.splitlines()), SRC + " has a line longer than 160 characters")
m = FORBID.search(txt); check(not m, SRC + " contains the forbidden word " + (m.group(0) if m else ""))
check(not re.search(r"\)\s*\{\s*\}", txt) and "__attribute__" not in txt, SRC + " has an empty function body or an attribute")
if fails: print("FAILED %d" % len(fails)); sys.exit(1)

# ---- b ------------------------------------------------------------------------------------------------------------------------------------------
tmp = tempfile.mkdtemp(prefix="dkquery_")
out = os.path.join(tmp, "cross")
r = sh(["sh", os.path.join(NXD, "build_dk.sh"), out])
check(r.returncode == 0, "cross build failed: " + tail(r.stderr + r.stdout))
if r.returncode == 0:
    o = os.path.join(out, "dk_query.o")
    und = {l.split()[1] for l in sh([NM, "-u", o]).stdout.splitlines() if len(l.split()) == 2}
    dfn = {l.split()[2]: l.split()[1] for l in sh([NM, "--defined-only", o]).stdout.splitlines() if len(l.split()) == 3}
    ext = {s for s, t in dfn.items() if t in "TDBR"}
    check(ext == HANDLERS, "defined global symbols differ: extra %s missing %s" % (sorted(ext - HANDLERS), sorted(HANDLERS - ext)))
    bad = {s for s in und if not (s in UNDEF_OK or LIBCALL.match(s))}
    check(not bad, "unexpected undefined symbols: %s" % sorted(bad))
    check(not re.search(r"^\S*\s+[Ww]\s", sh([NM, "-C", o]).stdout, re.M), "a weak symbol is present")

# ---- c ------------------------------------------------------------------------------------------------------------------------------------------
def host(srcdir, exe):
    inc = ["-I" + os.path.join(HERE, "shim"), "-I" + os.path.join(DK, "dkp", "libnx", "include"), "-I" + os.path.join(DK, "deko3d", "include"),
           "-I" + NXD, "-I" + BACK]
    fl = ["gcc", "-std=gnu11", "-g", "-O1", "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined", "-Wall", "-Wextra", "-Werror"] + inc
    o1, o2 = exe + "_p.o", exe + "_h.o"
    r = sh(fl + ["-Dmalloc=t_malloc", "-Drealloc=t_realloc", "-Dfree=t_free", "-c", os.path.join(srcdir, SRC), "-o", o1])
    if r.returncode: return r
    r = sh(fl + ["-c", os.path.join(HERE, "host_main.c"), "-o", o2])
    if r.returncode: return r
    return sh(fl + [o1, o2, "-o", exe])

def run_host(srcdir, tag):
    exe = os.path.join(tmp, "host_" + tag)
    b = host(srcdir, exe)
    if b.returncode: return None, "build: " + tail(b.stderr, 6)
    r = sh([exe], env=dict(os.environ, ASAN_OPTIONS="detect_leaks=1", UBSAN_OPTIONS="print_stacktrace=1"), timeout=120)
    if r.returncode: return None, "exit %d: %s" % (r.returncode, tail(r.stderr, 6))
    chk, n = {}, 0
    for line in r.stdout.splitlines():
        m = re.match(r"CHK (\S+) ([01])$", line)
        if m: chk[m.group(1)] = chk.get(m.group(1), 1) & int(m.group(2))
        m = re.match(r"NCHK (\d+)$", line)
        if m: n = int(m.group(1))
    bad = [k for k, v in chk.items() if not v]
    if bad: return None, "failed checks: " + " ".join(bad[:8])
    if n != EXPECT_CHECKS: return None, "check count %d, expected %d" % (n, EXPECT_CHECKS)
    miss = [k for k in MUST_NAMES if k not in chk]
    if miss: return None, "missing checks: " + " ".join(miss[:8])
    return n, ""

n, why = run_host(NXD, "base")
check(n is not None, "host run: " + why)
if n is not None: print("checks passed %d/%d (ASan + UBSan)" % (n, n))

# ---- d ------------------------------------------------------------------------------------------------------------------------------------------
MUT = [
    ("no counter reset at slice open", "dkCmdBufResetCounter(nxdk_s.cmd, DkCounter_SamplesPassed);", ""),
    ("slice closed with the timestamp counter", "dkCmdBufReportCounter(nxdk_s.cmd, DkCounter_SamplesPassed, a);", "dkCmdBufReportCounter(nxdk_s.cmd, DkCounter_Timestamp, a);"),
    ("record stride 8", "(DkGpuAddr)(*idx % CHUNK_RECS) * REC_BYTES", "(DkGpuAddr)(*idx % CHUNK_RECS) * 8"),
    ("slice value assigned, not added", "qry[sl[k].ids[j]].acc += v;", "qry[sl[k].ids[j]].acc = v;"),
    ("restart ignored: old slices still counted", "if (k >= qry[sl[k].ids[j]].since)", "if (1)"),
    ("pause does nothing", "void nxdk_query_pause(void) { (void)slice_close(); }", "void nxdk_query_pause(void) { (void)0; }"),
    ("resume does nothing", "void nxdk_query_resume(void) { slice_open(); }", "void nxdk_query_resume(void) { (void)0; }"),
    ("slice open twice", "if (cur || !any_active()) return;", "if (!any_active()) return;"),
    ("submit end leaves the slice open", "void nxdk_query_submit_end(void) { (void)slice_close(); }", "void nxdk_query_submit_end(void) { (void)0; }"),
    ("submit start does not reopen", "void nxdk_query_submit_begin(void) { slice_open(); }", "void nxdk_query_submit_begin(void) { (void)0; }"),
    ("timestamp read from the counter word", "q->ts = rec_ptr(q->rec)[1];", "q->ts = rec_ptr(q->rec)[0];"),
    ("count read from the timestamp word", "const uint64_t v = rec_ptr(sl[k].rec)[0];", "const uint64_t v = rec_ptr(sl[k].rec)[1];"),
    ("one record block only", "if (nRec / CHUNK_RECS == nChunk) {", "if (nRec == 0) {"),
    ("not issued, no wait: ready", "if (!wait) return 1;", "if (0) return 1;"),
    ("wait value 2 accepted", "wait > 1 ||", ""),
    ("null out pointer accepted", "wait > 1 || !res)", "wait > 1)"),
    ("event result 0", "case NxQuery_Event: *res = 1; return 0;", "case NxQuery_Event: *res = 0; return 0;"),
    ("inactive occlusion End accepted", "if (!q->active) { nxdk_err(\"QueryEnd: occlusion", "if (0) { nxdk_err(\"QueryEnd: occlusion"),
    ("restart point not set", "q->since = nSl;", "q->since = 0;"),
    ("timestamp record never read", "q->rec = r; q->tsPend = 1; q->issued = 1;", "q->rec = r; q->issued = 1;"),
    ("records not recycled", "nChunk = 0; nRec = 0;", "nChunk = 0;"),
    ("slices not forgotten", "    nSl = 0;\n    for (uint32_t i = 1; i <= NXDK_MAX_QUERIES; i++) {\n        Qry* q", "    for (uint32_t i = 1; i <= NXDK_MAX_QUERIES; i++) {\n        Qry* q"),
    ("kind 3 accepted", "a[1] > NxQuery_TimestampNs", "a[1] > NxQuery_TimestampNs + 1"),
    ("id 0 accepted", "!a[0] || a[0] > NXDK_MAX_QUERIES || a[1]", "a[0] > NXDK_MAX_QUERIES || a[1]"),
    ("id lists leaked", "        free(sl[k].ids);\n    }\n    nSl = 0;", "    }\n    nSl = 0;"),
    ("ended query still active", "return q->used && q->kind == NxQuery_Occlusion && q->active; }", "return q->used && q->kind == NxQuery_Occlusion; }"),
    ("Begin ignores a failed slice close", "if (slice_close()) return;   /* the set of active queries changes */", "(void)slice_close();"),
    ("End ignores a failed slice close", "        if (slice_close()) return;\n        q->active = 0;", "        (void)slice_close();\n        q->active = 0;"),
    ("timestamp ignores a failed record", "if (rec_alloc(&r, &addr)) return;", "(void)rec_alloc(&r, &addr);"),
    ("shutdown keeps the queries", "    memset(qry, 0, sizeof qry);\n", ""),
]
caught = 0
if "--no-mutations" not in sys.argv and n is not None:
    for i, (name, a, b) in enumerate(MUT):
        d = os.path.join(tmp, "mut%d" % i); os.makedirs(d)
        shutil.copy(os.path.join(NXD, SRC), d)
        t = open(os.path.join(d, SRC)).read()
        if t.count(a) != 1:
            check(False, "mutation '%s': anchor occurs %d times" % (name, t.count(a))); continue
        open(os.path.join(d, SRC), "w").write(t.replace(a, b))
        m, why = run_host(d, "m%d" % i)
        if m is None: caught += 1; print("  caught: %s (%s)" % (name, why.splitlines()[0][:90]))
        else: check(False, "mutation not caught: " + name)
    print("mutations caught %d/%d" % (caught, len(MUT)))
shutil.rmtree(tmp, ignore_errors=True)
if fails: print("FAILED %d" % len(fails)); sys.exit(1)
print("ALL OK")
