// glue_query.cpp: the product definitions of the query hooks of res_core.h on nxgpu4.h (NxOp_QueryBegin / NxOp_QueryEnd, NXGPU_FN_QUERY_RESULT; ids 1..4096).
//  Mapping (kind per D3DQUERYTYPE): OCCLUSION -> NxQuery_Occlusion, EVENT -> NxQuery_Event, TIMESTAMP -> NxQuery_TimestampNs.
//  issue    flags BEGIN: QueryBegin (a begin again restarts); flags END: QueryEnd, preceded by QueryBegin when no Begin is open (an END without BEGIN is an empty interval: occlusion 0, an event / a timestamp
//           taken at that point). Both bits: BEGIN then END.
//  ready    the stream is submitted first (an End still in the pending stream would never complete: any poll makes progress), then QUERY_RESULT(id, wait 0): 0 = ready, the value is cached in the object.
//  flush    nxs_flush (D3DGETDATA_FLUSH).
//  result   min(size, data_size) low bytes of the cached value (little endian): occlusion = the samples that passed (D3D9's DWORD is the low 4 bytes, as wined3d copies it), event = 1 (TRUE) once
//           every earlier command completed, timestamp = GPU nanoseconds at End.
//  TIMESTAMPDISJOINT / TIMESTAMPFREQ have no backend object: the timestamp clock is NXGPU nanoseconds (nxgpu4.h NxQuery_TimestampNs), one fixed frequency, never reset: FREQ = 10^9 ticks / s and
//  DISJOINT = FALSE (the clock never jumps or changes rate), which is also what wined3d's timestamp_disjoint query reports (wined3d query.c: disjoint = FALSE, frequency 1000 * 1000 * 1000).
#include "glue_stream.h"
#include "res_core.h"
#include <string.h>

namespace {
enum { NX_TIMESTAMP_HZ = 1000000000 };
uint8_t g_used[NX_MAX_IMAGES / 8 + 1];   // query ids in use (1..4096 -> bit id - 1)
bool derived(const NxQueryCore* q) { return q->type == D3DQUERYTYPE_TIMESTAMPDISJOINT || q->type == D3DQUERYTYPE_TIMESTAMPFREQ; }
uint32_t kind(const NxQueryCore* q) { return q->type == D3DQUERYTYPE_OCCLUSION ? NxQuery_Occlusion : q->type == D3DQUERYTYPE_EVENT ? NxQuery_Event : NxQuery_TimestampNs; }
}

bool nxd3d_query_create(NxQueryCore* q) {
    if (derived(q)) return true;
    if (!nxs_reserve()) return false;   // glue_stream.h E1: issue / ready / flush of this query never need memory afterwards (the result goes through the ring's scratch, E2)
    for (uint32_t id = 1; id <= 4096; id++) if (!(g_used[(id - 1) >> 3] >> ((id - 1) & 7) & 1)) { g_used[(id - 1) >> 3] |= (uint8_t)(1u << ((id - 1) & 7)); q->gid = id; return true; }
    return false;
}
void nxd3d_query_destroy(NxQueryCore* q) { if (q->gid) { g_used[(q->gid - 1) >> 3] &= (uint8_t)~(1u << ((q->gid - 1) & 7)); q->gid = 0; } }
void nxd3d_query_issue(NxQueryCore* q, DWORD flags) {
    q->gready = 0;
    if (derived(q)) { q->gvalue = q->type == D3DQUERYTYPE_TIMESTAMPFREQ ? (uint64_t)NX_TIMESTAMP_HZ : 0; return; }
    bool ok = true;   // E1: reserved by nxd3d_query_create; false is a device bug, counted
    if (flags & D3DISSUE_BEGIN) { ok = nxs_query_begin(q->gid, kind(q)) && ok; q->gbegun = 1; }
    if (flags & D3DISSUE_END) { if (!q->gbegun) ok = nxs_query_begin(q->gid, kind(q)) && ok; ok = nxs_query_end(q->gid) && ok; q->gbegun = 0; }
    if (!ok) nxs_error();
}
bool nxd3d_query_ready(NxQueryCore* q) {
    if (q->gready || derived(q)) return true;
    nxs_flush();
    uint64_t* out = (uint64_t*)nxs_scratch(16); if (!out) { nxs_error(); return false; }   // E1 / E2: reserved by nxd3d_query_create; a device bug, counted
    *out = 0; uint64_t a[3] = {q->gid, 0, FNVNX_ADDR_(out)};
    if (nxd3d_gpu_call(NXGPU_FN_QUERY_RESULT, a, 3) != 0) return false;
    q->gvalue = *out; q->gready = 1; return true;
}
void nxd3d_query_flush(NxQueryCore*) { nxs_flush(); }
void nxd3d_query_result(NxQueryCore* q, void* out, uint32_t size) { uint32_t n = size < sizeof q->gvalue ? size : (uint32_t)sizeof q->gvalue; memcpy(out, &q->gvalue, n); if (size > n) memset((uint8_t*)out + n, 0, size - n); }
