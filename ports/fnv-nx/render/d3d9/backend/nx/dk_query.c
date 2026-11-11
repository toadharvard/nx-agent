/* fnv-nx L6 deko3d backend: queries (NxOp_QueryBegin / QueryEnd, query_result); the behaviour is that of gl4_query.c.
 *
 *   Occlusion  samples that passed (= pixels on a single-sample target) between Begin and End. Several D3D queries may overlap, one deko3d
 *              counter is running, so the count is made of slices: one counter interval for every stretch in which the set of active D3D
 *              queries is constant. A slice opens with dkCmdBufResetCounter(SamplesPassed) and closes with dkCmdBufReportCounter into a
 *              16-byte record (u64 count, u64 timestamp); every Begin, End, pause and submit end closes the slice, every Begin, End, resume
 *              and submit start opens the next. After the queue has gone idle nxdk_query_collect adds each record to every query that was
 *              active in its slice. Counts add over disjoint intervals, so the result is exact.
 *              A Begin of a used id restarts it: `since` = the number of slices closed before it, collect ignores older slices for that id.
 *   Event      the result is 1 once QueryEnd was issued: SUBMIT waits for the queue (dk.h S1), so every issued query has completed.
 *   Timestamp  QueryEnd reports DkCounter_Timestamp into a record; collect keeps its second u64 (the GPU timestamp, nanoseconds on
 *              Maxwell/Tegra, arbitrary epoch) as the result; the latest QueryEnd of the id wins.
 * Records live in staging blocks of CHUNK_RECS records (nxdk_stage_alloc), one more block whenever the last is full: no limit on the slice count.
 * They are read by collect, which dk.h S1 runs before nxdk_stage_free, so a block never outlives its use. */
#include "dk.h"
#include <stdlib.h>
#include <string.h>

#define NEED(n, name) do { if (argc < (n)) { nxdk_err(#name ": %u words, need %u", argc, (unsigned)(n)); return; } } while (0)

enum { REC_BYTES = 16, CHUNK_RECS = 4096 };
typedef struct Qry { uint64_t acc, ts; uint32_t since, rec; uint8_t used, kind, active, issued, tsPend; } Qry;
typedef struct Slice { uint32_t rec, n; uint32_t* ids; } Slice;
static Qry qry[NXDK_MAX_QUERIES + 1];
static Slice* sl; static uint32_t nSl, capSl;                       /* closed slices of this submit */
static DkMemBlock* chunk; static uint32_t nChunk, capChunk, nRec;    /* record blocks and records handed out since the last collect */
static uint32_t cur;                                                 /* 1 = a counter interval is open */

static int is_occ(const Qry* q) { return q->used && q->kind == NxQuery_Occlusion && q->active; }
static int any_active(void) { for (uint32_t i = 1; i <= NXDK_MAX_QUERIES; i++) if (is_occ(&qry[i])) return 1; return 0; }

/* the next record: its index and GPU address; 1 = refused (nxdk_err'd by nxdk_stage_alloc or here) */
static int rec_alloc(uint32_t* idx, DkGpuAddr* addr) {
    if (nRec / CHUNK_RECS == nChunk) {
        if (nChunk == capChunk) {
            DkMemBlock* n = (DkMemBlock*)realloc(chunk, (capChunk ? capChunk * 2 : 4) * sizeof *n);
            if (!n) { nxdk_err("query: out of memory"); return 1; }
            chunk = n; capChunk = capChunk ? capChunk * 2 : 4;
        }
        DkMemBlock b = nxdk_stage_alloc(CHUNK_RECS * REC_BYTES);
        if (!b) return 1;
        chunk[nChunk++] = b;
    }
    *idx = nRec++;
    *addr = dkMemBlockGetGpuAddr(chunk[*idx / CHUNK_RECS]) + (DkGpuAddr)(*idx % CHUNK_RECS) * REC_BYTES;
    return 0;
}
static const uint64_t* rec_ptr(uint32_t idx) {
    return (const uint64_t*)((const uint8_t*)dkMemBlockGetCpuAddr(chunk[idx / CHUNK_RECS]) + (size_t)(idx % CHUNK_RECS) * REC_BYTES);
}

static int slice_close(void) {   /* 0 = ok, 1 = nxdk_err'd (the interval is lost with the failing op) */
    if (!cur) return 0;
    cur = 0;
    uint32_t r;
    DkGpuAddr a;
    if (rec_alloc(&r, &a)) return 1;
    dkCmdBufReportCounter(nxdk_s.cmd, DkCounter_SamplesPassed, a);
    if (nSl == capSl) {
        Slice* n = (Slice*)realloc(sl, (capSl ? capSl * 2 : 16) * sizeof *n);
        if (!n) { nxdk_err("query: out of memory"); return 1; }
        sl = n; capSl = capSl ? capSl * 2 : 16;
    }
    uint32_t n = 0;
    for (uint32_t i = 1; i <= NXDK_MAX_QUERIES; i++) n += is_occ(&qry[i]);   /* >= 1: the set cannot change while a slice is open */
    uint32_t* ids = (uint32_t*)malloc(n * sizeof *ids);
    if (!ids) { nxdk_err("query: out of memory"); return 1; }
    Slice* s = &sl[nSl++];
    s->rec = r; s->n = 0; s->ids = ids;
    for (uint32_t i = 1; i <= NXDK_MAX_QUERIES; i++) if (is_occ(&qry[i])) s->ids[s->n++] = i;
    return 0;
}
static void slice_open(void) {
    if (cur || !any_active()) return;
    dkCmdBufResetCounter(nxdk_s.cmd, DkCounter_SamplesPassed);
    cur = 1;
}

void nxdk_query_pause(void) { (void)slice_close(); }   /* a failure was reported by slice_close through nxdk_err */
void nxdk_query_resume(void) { slice_open(); }
void nxdk_query_submit_begin(void) { slice_open(); }
void nxdk_query_submit_end(void) { (void)slice_close(); }

void nxdk_query_collect(void) {   /* after the queue went idle: every closed slice and timestamp record is written */
    for (uint32_t k = 0; k < nSl; k++) {
        const uint64_t v = rec_ptr(sl[k].rec)[0];
        for (uint32_t j = 0; j < sl[k].n; j++) if (k >= qry[sl[k].ids[j]].since) qry[sl[k].ids[j]].acc += v;
        free(sl[k].ids);
    }
    nSl = 0;
    for (uint32_t i = 1; i <= NXDK_MAX_QUERIES; i++) {
        Qry* q = &qry[i];
        q->since = 0;
        if (q->tsPend) { q->ts = rec_ptr(q->rec)[1]; q->tsPend = 0; }
    }
    nChunk = 0; nRec = 0;   /* the blocks go with nxdk_stage_free */
}

/* CONTRACT-REQUEST: declare in dk.h and call from nxdk_stream_shutdown: frees this file's tables and forgets every query */
void nxdk_query_shutdown(void);
void nxdk_query_shutdown(void) {
    for (uint32_t k = 0; k < nSl; k++) free(sl[k].ids);
    free(sl); free(chunk);
    sl = NULL; chunk = NULL; nSl = capSl = nChunk = capChunk = nRec = cur = 0;
    memset(qry, 0, sizeof qry);
}

void nxdk_op_query_begin(const uint32_t* a, uint32_t argc) {
    NEED(2, QueryBegin);
    if (!a[0] || a[0] > NXDK_MAX_QUERIES || a[1] > NxQuery_TimestampNs) { nxdk_err("QueryBegin: id %u kind %u", a[0], a[1]); return; }
    if (slice_close()) return;   /* the set of active queries changes */
    Qry* q = &qry[a[0]];
    memset(q, 0, sizeof *q);
    q->used = 1; q->kind = (uint8_t)a[1]; q->since = nSl;
    q->active = a[1] == NxQuery_Occlusion;
    slice_open();
}

void nxdk_op_query_end(const uint32_t* a, uint32_t argc) {
    NEED(1, QueryEnd);
    Qry* q = a[0] && a[0] <= NXDK_MAX_QUERIES && qry[a[0]].used ? &qry[a[0]] : NULL;
    if (!q) { nxdk_err("QueryEnd: query %u was not begun", a[0]); return; }
    uint32_t r;
    DkGpuAddr addr;
    switch ((enum NxQueryKind)q->kind) {
    case NxQuery_Occlusion:
        if (!q->active) { nxdk_err("QueryEnd: occlusion query %u is not active", a[0]); return; }
        if (slice_close()) return;
        q->active = 0; q->issued = 1;
        slice_open();
        return;
    case NxQuery_Event:
        q->issued = 1;
        return;
    case NxQuery_TimestampNs:
        if (rec_alloc(&r, &addr)) return;
        dkCmdBufReportCounter(nxdk_s.cmd, DkCounter_Timestamp, addr);
        q->rec = r; q->tsPend = 1; q->issued = 1;
        return;
    }
}

uint64_t nxdk_query_result(uint64_t id, uint64_t wait, uint64_t out) {
    Qry* q = id && id <= NXDK_MAX_QUERIES && qry[id].used ? &qry[id] : NULL;
    uint64_t* res = (uint64_t*)nxdk_guest_ptr(out, sizeof *res);
    if (!q || wait > 1 || !res) { nxdk_err("query_result: id %llu wait %llu", (unsigned long long)id, (unsigned long long)wait); return NXGPU_BAD_CALL; }
    if (!q->issued) {
        if (!wait) return 1;
        nxdk_err("query_result: wait on query %llu whose QueryEnd was never issued", (unsigned long long)id);
        return NXGPU_BAD_CALL;
    }
    switch ((enum NxQueryKind)q->kind) {
    case NxQuery_Occlusion: *res = q->acc; return 0;
    case NxQuery_Event: *res = 1; return 0;
    case NxQuery_TimestampNs: *res = q->ts; return 0;
    }
    return NXGPU_BAD_CALL;
}
