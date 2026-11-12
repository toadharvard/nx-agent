/* fnv-nx L6 GL 4.5 backend v4: queries (NxOp_QueryBegin / QueryEnd, query_result). Ids are 1..GL4_MAX_QUERIES; a
 * QueryBegin (re)defines the id (kind, a fresh state: a query that is begun again restarts).
 *
 *   Occlusion  the number of samples that passed (= pixels on a single-sample target) of the draws between Begin and
 *              End. GL allows one active SAMPLES_PASSED query, D3D9 several that overlap, so the count is made of
 *              slices: one GL query for every interval in which the set of active D3D queries is constant (every
 *              Begin, End, submit boundary and internal draw closes the slice and opens the next); a slice's result
 *              is added to every query active in it, after glFinish at the end of the submit. Counts add over
 *              disjoint intervals, so the result is exact. The backend's own draws (scaling blits) pause the slice.
 *   Event      QueryEnd inserts a GPU fence (GL sync); the result is 1 once all work before it has completed.
 *   Timestamp  QueryEnd records GL_TIMESTAMP: the result is the GPU time in nanoseconds (arbitrary epoch).
 * query_result(id, wait, out): 0 = ready, *out written; 1 = not ready (wait = 0 only: not yet completed, or QueryEnd
 * not issued yet); wait = 1 blocks until ready; wait = 1 on a query whose QueryEnd was never issued can never
 * complete and is BAD_CALL, as is an id that was never begun. Submits are synchronous, so at any call between
 * submits every issued query has completed. */
#include "gl4.h"
#include <stdlib.h>
#include <string.h>

#define NEED(n, name) do { if (argc < (n)) { gl4_err(#name ": %u words, need %u", argc, (unsigned)(n)); return; } } while (0)

static int any_active(void) {
    for (uint32_t i = 1; i <= GL4_MAX_QUERIES; i++) if (g4.qry[i].used && g4.qry[i].kind == NxQuery_Occlusion && g4.qry[i].active) return 1;
    return 0;
}

static void slice_close(void) {
    if (!g4.occCur) return;
    p_glEndQuery(GL_SAMPLES_PASSED);
    if (g4.nSlices == g4.capSlices) {
        uint32_t cap = g4.capSlices ? g4.capSlices * 2 : 16;
        Gl4Slice* n = (Gl4Slice*)realloc(g4.slices, cap * sizeof *n);
        if (!n) { gl4_err("query: out of memory"); return; }
        g4.slices = n; g4.capSlices = cap;
    }
    Gl4Slice* sl = &g4.slices[g4.nSlices++];
    uint32_t n = 0;
    for (uint32_t i = 1; i <= GL4_MAX_QUERIES; i++) n += g4.qry[i].used && g4.qry[i].kind == NxQuery_Occlusion && g4.qry[i].active;
    sl->q = g4.occCur; sl->n = 0;
    sl->ids = (uint32_t*)malloc(n * sizeof(uint32_t));
    g4.occCur = 0;
    if (!sl->ids) { gl4_err("query: out of memory"); return; }
    for (uint32_t i = 1; i <= GL4_MAX_QUERIES; i++)
        if (g4.qry[i].used && g4.qry[i].kind == NxQuery_Occlusion && g4.qry[i].active) sl->ids[sl->n++] = i;
}
static void slice_open(void) {
    if (g4.occCur || !any_active()) return;
    p_glGenQueries(1, &g4.occCur);
    p_glBeginQuery(GL_SAMPLES_PASSED, g4.occCur);
}

void gl4_query_pause(void) { slice_close(); }
void gl4_query_resume(void) { slice_open(); }
void gl4_query_submit_begin(void) { slice_open(); }
void gl4_query_submit_end(void) { slice_close(); }

void gl4_query_collect(void) {   /* after glFinish: every closed slice has its result */
    for (uint32_t k = 0; k < g4.nSlices; k++) {
        Gl4Slice* sl = &g4.slices[k];
        uint64_t v = 0;
        p_glGetQueryObjectui64v(sl->q, GL_QUERY_RESULT, &v);
        for (uint32_t j = 0; sl->ids && j < sl->n; j++) g4.qry[sl->ids[j]].acc += v;
        p_glDeleteQueries(1, &sl->q);
        free(sl->ids);
    }
    g4.nSlices = 0;
}

static void release(Gl4Query* q) {
    if (q->sync) p_glDeleteSync(q->sync);
    if (q->q) p_glDeleteQueries(1, &q->q);
    memset(q, 0, sizeof *q);
}

void gl4_op_query_begin(const uint32_t* a, uint32_t argc) {
    NEED(2, QueryBegin);
    if (!a[0] || a[0] > GL4_MAX_QUERIES || a[1] > NxQuery_TimestampNs) { gl4_err("QueryBegin: id %u kind %u", a[0], a[1]); return; }
    Gl4Query* q = &g4.qry[a[0]];
    slice_close();   /* the set of active queries changes */
    if (q->used && q->kind == NxQuery_Occlusion) {   /* slices of the old query still name this id: settle them */
        p_glFinish();
        gl4_query_collect();
    }
    release(q);
    q->used = 1; q->kind = (uint8_t)a[1];
    if (a[1] == NxQuery_Occlusion) q->active = 1;
    slice_open();
}

void gl4_op_query_end(const uint32_t* a, uint32_t argc) {
    NEED(1, QueryEnd);
    Gl4Query* q = a[0] && a[0] <= GL4_MAX_QUERIES && g4.qry[a[0]].used ? &g4.qry[a[0]] : NULL;
    if (!q) { gl4_err("QueryEnd: query %u was not begun", a[0]); return; }
    switch ((enum NxQueryKind)q->kind) {
    case NxQuery_Occlusion:
        if (!q->active) { gl4_err("QueryEnd: occlusion query %u is not active", a[0]); return; }
        slice_close();
        q->active = 0; q->issued = 1;
        slice_open();
        return;
    case NxQuery_Event:
        if (q->sync) p_glDeleteSync(q->sync);
        q->sync = p_glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE, 0);
        q->issued = 1;
        return;
    case NxQuery_TimestampNs:
        if (!q->q) p_glGenQueries(1, &q->q);
        p_glQueryCounter(q->q, GL_TIMESTAMP);
        q->issued = 1;
        return;
    }
}

uint64_t gl4_api_query_result(uint64_t id, uint64_t wait, uint64_t out) {
    Gl4Query* q = id && id <= GL4_MAX_QUERIES && g4.qry[id].used ? &g4.qry[id] : NULL;
    uint64_t* res = (uint64_t*)nxgl4_guest_ptr(out, sizeof *res);
    if (!q || wait > 1 || !res) { gl4_err("query_result: id %llu wait %llu", (unsigned long long)id, (unsigned long long)wait); return NXGPU_BAD_CALL; }
    if (!q->issued) {
        if (!wait) return 1;
        gl4_err("query_result: wait on query %llu whose QueryEnd was never issued", (unsigned long long)id);
        return NXGPU_BAD_CALL;
    }
    switch ((enum NxQueryKind)q->kind) {
    case NxQuery_Occlusion:
        *res = q->acc;
        return 0;
    case NxQuery_Event: {
        GLenum r = p_glClientWaitSync(q->sync, GL_SYNC_FLUSH_COMMANDS_BIT, wait ? GL_TIMEOUT_IGNORED : 0);
        if (r == GL_ALREADY_SIGNALED || r == GL_CONDITION_SATISFIED) { *res = 1; return 0; }
        if (r == GL_TIMEOUT_EXPIRED) return 1;
        gl4_err("query_result: wait on the fence failed");
        return NXGPU_BAD_CALL;
    }
    case NxQuery_TimestampNs: {
        GLint avail = 0;
        p_glGetQueryObjectiv(q->q, GL_QUERY_RESULT_AVAILABLE, &avail);
        if (!avail && !wait) return 1;
        p_glGetQueryObjectui64v(q->q, GL_QUERY_RESULT, res);
        return 0;
    }
    }
    return NXGPU_BAD_CALL;
}
