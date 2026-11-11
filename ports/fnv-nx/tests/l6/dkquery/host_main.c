/* Host harness of test_dkquery.py: dk_query.c (the real product file) against a recording double of the deko3d calls and of the staging
 * allocator. The double writes the scripted counter value into the report record when ReportCounter is recorded (the GPU ran it); what
 * the product does with it is checked here: the exact recorded sequence, record addresses, sums, results, messages and error paths. */
#define _GNU_SOURCE
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "dk.h"

void nxdk_query_shutdown(void);
static char evs[16384][40]; static int nev;
static void ev(const char* fmt, ...) { va_list ap; va_start(ap, fmt); if (nev < 16384) vsnprintf(evs[nev++], 40, fmt, ap); va_end(ap); }
static int is(int i, const char* s) { return i >= 0 && i < nev && !strcmp(evs[i], s); }
static int cnt(const char* s) { int c = 0; for (int i = 0; i < nev; i++) c += !strcmp(evs[i], s); return c; }
static int nchk;
#define CHK(name, cond) do { printf("CHK %s %d\n", name, (cond) ? 1 : 0); nchk++; } while (0)

/* ---- allocation failure injection (the product is built with -Dmalloc=t_malloc ...) ------------------------------------------------------------- */
static int fail_malloc, fail_realloc;
void* t_malloc(size_t n) { return fail_malloc ? NULL : malloc(n); }
void* t_realloc(void* p, size_t n) { return fail_realloc ? NULL : realloc(p, n); }
void t_free(void* p) { free(p); }

/* ---- errors ---------------------------------------------------------------------------------------------------------------------------------- */
static char msg[160]; static int nerr;
void nxchk_err(const char* fmt, ...) { va_list ap; va_start(ap, fmt); vsnprintf(msg, sizeof msg, fmt, ap); va_end(ap); nerr++; }
void* nxdk_guest_ptr(uint64_t a, uint64_t len) { (void)len; return (void*)(uintptr_t)a; }
NxDkStream nxdk_s;

/* ---- staging allocator and counters ---------------------------------------------------------------------------------------------------------- */
enum { MAXBLK = 8 };
static int n_alloc; static uint8_t* blk[MAXBLK]; static uint32_t blksz[MAXBLK]; static int nblk, fail_stage;
DkMemBlock nxdk_stage_alloc(uint32_t bytes) {
    if (fail_stage) { nxchk_err("staging: deko3d refused a block of %u bytes", bytes); return NULL; }
    blk[nblk] = (uint8_t*)aligned_alloc(4096, bytes); memset(blk[nblk], 0xA5, bytes); blksz[nblk] = bytes;
    ev("alloc %u", bytes); n_alloc++;
    return (DkMemBlock)(uintptr_t)++nblk;
}
static void free_blocks(void) { for (int i = 0; i < nblk; i++) free(blk[i]); nblk = 0; }
void* dkMemBlockGetCpuAddr(DkMemBlock b) { return blk[(uintptr_t)b - 1]; }
DkGpuAddr dkMemBlockGetGpuAddr(DkMemBlock b) { return 0x100000000ull + 0x1000000ull * (uintptr_t)b; }
static uint64_t script[16384]; static int nscript, ipos;   /* the GPU's counter value for each ReportCounter, in recording order */
static int run_reset;
void dkCmdBufResetCounter(DkCmdBuf c, DkCounter t) { (void)c; ev("reset%d", (int)t); run_reset++; }
void dkCmdBufReportCounter(DkCmdBuf c, DkCounter t, DkGpuAddr a) {
    (void)c;
    int b = (int)((a - 0x100000000ull) >> 24) - 1; uint64_t off = (a - 0x100000000ull) & 0xFFFFFF;
    ev("report%d b%d+%llu", (int)t, b, (unsigned long long)off);
    uint64_t* r = (uint64_t*)(blk[b] + off);
    if (ipos >= nscript) { r[0] = r[1] = 0xDEADDEADDEADDEADull; return; }
    if (t == DkCounter_Timestamp) { r[0] = 0xBAD0BAD0BAD0BAD0ull; r[1] = script[ipos++]; }
    else { r[0] = script[ipos++]; r[1] = 0x7171717171717171ull; }
}
static uint8_t cmdobj;
static void sc(int n, ...) { va_list ap; va_start(ap, n); nscript = n; ipos = 0; for (int i = 0; i < n; i++) script[i] = va_arg(ap, uint64_t); va_end(ap); }
static void begin_sub(void) { nxdk_s.cmd = (DkCmdBuf)&cmdobj; nxdk_query_submit_begin(); }
static void end_sub(void) { nxdk_query_submit_end(); nxdk_query_collect(); free_blocks(); nxdk_s.cmd = NULL; }
static void reset_log(void) { nev = 0; nerr = 0; msg[0] = 0; }
static void QB(uint32_t id, uint32_t kind) { uint32_t a[2] = { id, kind }; nxdk_op_query_begin(a, 2); }
static void QE(uint32_t id) { uint32_t a[1] = { id }; nxdk_op_query_end(a, 1); }
static uint64_t out;
static uint64_t R(uint64_t id, uint64_t wait) { out = 0x5555; return nxdk_query_result(id, wait, (uint64_t)(uintptr_t)&out); }
#define EVS(...) (nev == (int)(sizeof((const char*[]){ __VA_ARGS__ }) / sizeof(char*)) && evseq(__VA_ARGS__, NULL))
static int evseq(const char* first, ...) {
    va_list ap; va_start(ap, first); int i = 0, ok = 1;
    for (const char* s = first; s; s = va_arg(ap, const char*), i++) if (!is(i, s)) { ok = 0; break; }
    va_end(ap); return ok;
}

int main(void) {
    /* ---- sequential ---- */
    begin_sub(); sc(2, 7, 11);
    QB(1, 0); QE(1); QB(2, 0); QE(2);
    CHK("seq_events", EVS("reset2", "alloc 65536", "report2 b0+0", "reset2", "report2 b0+16"));
    CHK("seq_no_error", nerr == 0);
    end_sub();
    CHK("seq_q1", R(1, 0) == 0 && out == 7);
    CHK("seq_q2", R(2, 1) == 0 && out == 11);
    reset_log();
    /* ---- nested: begin1 begin2 end2 end1 ---- */
    begin_sub(); sc(3, 3, 5, 9);
    QB(1, 0); QB(2, 0); QE(2); QE(1);
    CHK("nest_events", EVS("reset2", "alloc 65536", "report2 b0+0", "reset2", "report2 b0+16", "reset2", "report2 b0+32"));
    end_sub();
    CHK("nest_sums", R(1, 0) == 0 && out == 17 && R(2, 0) == 0 && out == 5);
    reset_log();
    /* ---- overlapping: begin1 begin2 end1 end2 ---- */
    begin_sub(); sc(3, 2, 4, 8);
    QB(1, 0); QB(2, 0); QE(1); QE(2);
    end_sub();
    CHK("overlap_sums", R(1, 0) == 0 && out == 6 && R(2, 0) == 0 && out == 12);
    reset_log();
    /* ---- across submits: the slice closes at the submit end and the next submit reopens it ---- */
    begin_sub(); sc(2, 3, 6);
    QB(1, 0);
    CHK("xsub_not_issued_nowait", R(1, 0) == 1 && out == 0x5555);
    end_sub();
    CHK("xsub_events_1", EVS("reset2", "alloc 65536", "report2 b0+0"));
    CHK("xsub_pending_nowait", R(1, 0) == 1);
    nerr = 0;
    CHK("xsub_wait_without_end_bad", R(1, 1) == NXGPU_BAD_CALL && nerr == 1 && !strcmp(msg, "query_result: wait on query 1 whose QueryEnd was never issued"));
    reset_log();
    begin_sub(); QE(1);
    CHK("xsub_events_2", EVS("reset2", "alloc 65536", "report2 b0+0"));
    end_sub();
    CHK("xsub_sum", R(1, 1) == 0 && out == 9);
    reset_log();
    /* ---- pause / resume ---- */
    begin_sub(); sc(2, 4, 6);
    QB(1, 0); nxdk_query_pause(); nxdk_query_pause(); nxdk_query_resume(); nxdk_query_resume(); QE(1); nxdk_query_pause(); nxdk_query_resume();
    CHK("pause_events", EVS("reset2", "alloc 65536", "report2 b0+0", "reset2", "report2 b0+16"));
    end_sub();
    CHK("pause_sum", R(1, 0) == 0 && out == 10);
    reset_log();
    /* ---- a Begin of a used id restarts it (old slices ignored); kind change ---- */
    begin_sub(); sc(2, 5, 7);
    QB(1, 0); QB(1, 0); QE(1);
    end_sub();
    CHK("redefine_restarts", R(1, 0) == 0 && out == 7);
    reset_log();
    begin_sub(); sc(1, 5);
    QB(1, 0); QB(1, 1); QE(1);
    end_sub();
    CHK("redefine_to_event", R(1, 0) == 0 && out == 1);
    reset_log();
    begin_sub(); sc(1, 9);
    QB(1, 0); QE(1);
    end_sub();
    begin_sub(); end_sub();
    CHK("acc_kept", R(1, 0) == 0 && out == 9);
    reset_log();
    /* ---- Event ---- */
    begin_sub(); QB(3, 1);
    CHK("event_not_issued", R(3, 0) == 1);
    QE(3); QE(3);
    CHK("event_no_gpu_calls", nev == 0 && nerr == 0);
    end_sub();
    CHK("event_result", R(3, 0) == 0 && out == 1 && R(3, 1) == 0 && out == 1);
    reset_log();
    /* ---- Timestamp, interleaved with an occlusion slice (the slice stays open) ---- */
    begin_sub(); sc(3, 100, 200, 66);
    QB(4, 2); QB(5, 0); QE(4); QE(4); QE(5);
    CHK("ts_events", EVS("reset2", "alloc 65536", "report1 b0+0", "report1 b0+16", "report2 b0+32"));
    end_sub();
    CHK("ts_last_end_wins", R(4, 0) == 0 && out == 200);
    CHK("ts_occlusion_slice_unaffected", R(5, 0) == 0 && out == 66);
    reset_log();
    begin_sub(); end_sub();
    CHK("ts_kept", R(4, 0) == 0 && out == 200);
    reset_log();
    /* ---- 64-bit sums ---- */
    begin_sub(); sc(3, 0x100000005ull, 0x100000007ull, 0x200000001ull);
    QB(6, 0); QB(7, 0); QE(6); QE(7);
    end_sub();
    CHK("sum_64bit", R(6, 0) == 0 && out == 0x100000005ull + 0x100000007ull && R(7, 0) == 0 && out == 0x100000007ull + 0x200000001ull);
    reset_log();
    /* ---- many slices: 4096 records = one block, 4097 = two; ids restart ---- */
    begin_sub();
    for (int i = 0; i < 4096; i++) script[i] = (uint64_t)i + 1;
    nscript = 4096; ipos = 0;
    for (int i = 0; i < 4096; i++) { QB(10 + i % 100, 0); QE(10 + i % 100); }
    CHK("blocks_4096_one", cnt("alloc 65536") == 1 && nerr == 0 && cnt("report2 b0+65520") == 1);
    end_sub();
    reset_log();
    begin_sub();
    for (int i = 0; i < 4200; i++) script[i] = (uint64_t)i + 1;
    nscript = 4200; ipos = 0;
    for (int i = 0; i < 4200; i++) { QB(10 + i % 100, 0); QE(10 + i % 100); }
    CHK("blocks_4200_two", cnt("alloc 65536") == 2 && nerr == 0 && cnt("report2 b1+0") == 1 && cnt("report2 b1+16") == 1 && cnt("report2 b0+65520") == 1);
    end_sub();
    {
        int ok = 1;
        for (int i = 4100; i < 4200; i++) { if (R(10 + i % 100, 0) != 0 || out != (uint64_t)i + 1) ok = 0; }
        CHK("blocks_4200_sums", ok);
    }
    reset_log();
    /* ---- error paths ---- */
    { uint32_t a[2] = { 1, 0 }; nxdk_op_query_begin(a, 1); }
    CHK("err_begin_argc", nerr == 1 && !strcmp(msg, "QueryBegin: 1 words, need 2") && nev == 0);
    nerr = 0; { uint32_t a[1] = { 1 }; nxdk_op_query_end(a, 0); }
    CHK("err_end_argc", nerr == 1 && !strcmp(msg, "QueryEnd: 0 words, need 1"));
    nerr = 0; QB(0, 0);
    CHK("err_begin_id0", nerr == 1 && !strcmp(msg, "QueryBegin: id 0 kind 0"));
    nerr = 0; QB(NXDK_MAX_QUERIES + 1, 0);
    CHK("err_begin_id_big", nerr == 1 && !strcmp(msg, "QueryBegin: id 4097 kind 0"));
    nerr = 0; QB(1, 3);
    CHK("err_begin_kind", nerr == 1 && !strcmp(msg, "QueryBegin: id 1 kind 3") && nev == 0);
    nerr = 0; QE(0);
    CHK("err_end_id0", nerr == 1 && !strcmp(msg, "QueryEnd: query 0 was not begun"));
    nerr = 0; QE(NXDK_MAX_QUERIES + 1);
    CHK("err_end_id_big", nerr == 1 && !strcmp(msg, "QueryEnd: query 4097 was not begun"));
    nerr = 0; QE(4000);
    CHK("err_end_unbegun", nerr == 1 && !strcmp(msg, "QueryEnd: query 4000 was not begun"));
    begin_sub(); nerr = 0; QB(20, 0); QE(20); QE(20);
    CHK("err_end_inactive", nerr == 1 && !strcmp(msg, "QueryEnd: occlusion query 20 is not active"));
    end_sub();
    nerr = 0;
    CHK("err_res_id0", R(0, 0) == NXGPU_BAD_CALL && nerr == 1 && !strcmp(msg, "query_result: id 0 wait 0"));
    nerr = 0;
    CHK("err_res_id_big", R(NXDK_MAX_QUERIES + 1, 0) == NXGPU_BAD_CALL && nerr == 1 && !strcmp(msg, "query_result: id 4097 wait 0"));
    nerr = 0;
    CHK("err_res_unused", R(4001, 1) == NXGPU_BAD_CALL && nerr == 1 && !strcmp(msg, "query_result: id 4001 wait 1"));
    nerr = 0;
    CHK("err_res_wait2", R(20, 2) == NXGPU_BAD_CALL && nerr == 1 && !strcmp(msg, "query_result: id 20 wait 2"));
    nerr = 0;
    CHK("err_res_null_out", nxdk_query_result(20, 0, 0) == NXGPU_BAD_CALL && nerr == 1 && !strcmp(msg, "query_result: id 20 wait 0"));
    /* ---- allocation failures: one message, nothing recorded past it ---- */
    reset_log(); nxdk_query_shutdown();
    begin_sub(); QB(30, 0); fail_stage = 1; nerr = 0; QE(30); fail_stage = 0;
    CHK("err_stage_alloc_end", nerr == 1 && cnt("report2 b0+0") == 0);
    end_sub();
    reset_log();
    begin_sub(); QB(30, 0); fail_malloc = 1; nerr = 0; QE(30); fail_malloc = 0;
    CHK("err_ids_malloc", nerr == 1 && !strcmp(msg, "query: out of memory"));
    nerr = 0; QE(30);
    CHK("err_end_keeps_active", nerr == 0);
    QB(40, 0); fail_malloc = 1; nerr = 0; QB(41, 0); fail_malloc = 0;
    CHK("err_begin_close_fails", nerr == 1 && !strcmp(msg, "query: out of memory"));
    nerr = 0; QE(41);
    CHK("err_begin_not_begun", nerr == 1 && !strcmp(msg, "QueryEnd: query 41 was not begun"));
    end_sub();
    reset_log();
    nxdk_query_shutdown();   /* capSl = 16 after the first closed slice: the 17th needs a realloc */
    begin_sub();
    for (int i = 0; i < 16; i++) { QB(30, 0); QE(30); }
    QB(30, 0); fail_realloc = 1; nerr = 0; QE(30); fail_realloc = 0;
    CHK("err_slice_realloc", nerr == 1 && !strcmp(msg, "query: out of memory"));
    end_sub();
    reset_log();
    nxdk_query_shutdown();
    begin_sub(); QB(31, 2); fail_realloc = 1; nerr = 0; QE(31); fail_realloc = 0;
    CHK("err_chunk_realloc", nerr == 1 && !strcmp(msg, "query: out of memory"));
    end_sub();
    reset_log();
    /* the 5th block needs the block table to grow (4 -> 8): 16385 timestamp records */
    nxdk_query_shutdown(); n_alloc = 0;
    begin_sub(); nscript = 0; QB(32, 2);
    for (int i = 0; i < 16385; i++) QE(32);
    CHK("blocks_grow_table", n_alloc == 5 && nerr == 0);
    end_sub();
    reset_log();
    /* ---- shutdown forgets every query and frees; use afterwards is clean ---- */
    nxdk_query_shutdown(); nerr = 0;
    CHK("shutdown_forgets", R(1, 0) == NXGPU_BAD_CALL && nerr == 1);
    begin_sub(); sc(1, 3); QB(1, 0); QE(1); end_sub();
    CHK("after_shutdown_works", R(1, 0) == 0 && out == 3);
    nxdk_query_shutdown();
    printf("NCHK %d\n", nchk);
    return 0;
}
