// draw_ids.cpp: backend ids of the draw path, process-global because the backend is one global instance (nxgpu4.h: layout ids 1-256 and state ids 1-4096 are one id space of the backend).
//  I1 layouts and states are cached by CONTENT; an id is (re)defined in the stream by NxOp_DefineLayout4 / NxOp_DefineState before its first use (stream order = replay order, so redefining an id after earlier draws used
//     it is safe: "a redefinition takes effect at the next BindState").
//  I2 when a table is full the least recently used entry is redefined. A state's key holds its layout's definition serial, so a state whose layout id was redefined can never match again (no invalidation pass needed).
//  I3 a state key = (vertex shader id, pixel shader id, layout id + serial, the four words of NxRenderState); `serial` of a state = its definition counter, the draw path rebinds it when the pair (id, serial) changed.
#include "draw_core.h"

namespace {
struct LayE { NxVtxLayout4 v; uint32_t hash, stamp, serial; bool used; };
struct StKey { uint32_t vs, ps, lay, lserial; uint64_t w[4]; };
struct StE { StKey k; uint32_t hash, stamp, serial, next; bool used; };
enum { NLAY = 256, NST = 4096, NBUCKET = 8192 };
LayE g_lay[NLAY]; StE g_st[NST]; uint32_t g_head[NBUCKET + 1]; uint32_t g_stamp = 0, g_serial = 0, g_nst = 0;   // g_head: index + 1 of the chain head, 0 = empty
uint32_t fnv(const void* p, size_t n) { const uint8_t* b = (const uint8_t*)p; uint32_t h = 2166136261u; for (size_t i = 0; i < n; i++) { h ^= b[i]; h *= 16777619u; } return h; }
void unlink_st(uint32_t i) {
    uint32_t* p = &g_head[g_st[i].hash % NBUCKET];
    while (*p && *p != i + 1) p = &g_st[*p - 1].next;
    if (*p) *p = g_st[i].next;
    g_st[i].used = false;
}
}

uint32_t nx_layout_id(const NxVtxLayout4& v, uint32_t* serial) {
    uint32_t h = fnv(&v, sizeof v), victim = 0, best = ~0u; ++g_stamp;
    for (uint32_t i = 0; i < NLAY; i++) {
        LayE& e = g_lay[i];
        if (e.used && e.hash == h && !memcmp(&e.v, &v, sizeof v)) { e.stamp = g_stamp; *serial = e.serial; return i + 1; }
        if (!e.used) { victim = i; best = 0; } else if (best && e.stamp < best) { best = e.stamp; victim = i; }
    }
    LayE& e = g_lay[victim]; e.v = v; e.hash = h; e.stamp = g_stamp; e.serial = ++g_serial; e.used = true; *serial = e.serial;
    uint32_t* w = nxs_op(NxOp_DefineLayout4, 1 + sizeof(NxVtxLayout4) / 4); w[0] = victim + 1; memcpy(w + 1, &v, sizeof v);
    return victim + 1;
}
// returns the state id; *serial = the definition counter of the entry
uint32_t nx_state_id(uint32_t vs, uint32_t ps, uint32_t lay, uint32_t lserial, const NxRenderState& rs, uint32_t* serial) {
    StKey k = {vs, ps, lay, lserial, {rs.w[0], rs.w[1], rs.w[2], rs.w[3]}}; uint32_t h = fnv(&k, sizeof k); ++g_stamp;
    for (uint32_t c = g_head[h % NBUCKET]; c; c = g_st[c - 1].next) { StE& e = g_st[c - 1]; if (e.hash == h && !memcmp(&e.k, &k, sizeof k)) { e.stamp = g_stamp; *serial = e.serial; return c; } }
    uint32_t i;
    if (g_nst < NST) i = g_nst++;
    else { i = 0; uint32_t best = ~0u; for (uint32_t j = 0; j < NST; j++) { if (!g_st[j].used) { i = j; break; } if (g_st[j].stamp < best) { best = g_st[j].stamp; i = j; } } if (g_st[i].used) unlink_st(i); }
    StE& e = g_st[i]; e.k = k; e.hash = h; e.stamp = g_stamp; e.serial = ++g_serial; e.used = true; e.next = g_head[h % NBUCKET]; g_head[h % NBUCKET] = i + 1; *serial = e.serial;
    uint32_t* w = nxs_op(NxOp_DefineState, 12); w[0] = i + 1; w[1] = vs; w[2] = ps; w[3] = lay;
    for (int q = 0; q < 4; q++) { w[4 + 2 * q] = (uint32_t)rs.w[q]; w[5 + 2 * q] = (uint32_t)(rs.w[q] >> 32); }
    return i + 1;
}
void nx_state_forget_shader(uint32_t id) { for (uint32_t i = 0; i < g_nst; i++) if (g_st[i].used && (g_st[i].k.vs == id || g_st[i].k.ps == id)) unlink_st(i); }
