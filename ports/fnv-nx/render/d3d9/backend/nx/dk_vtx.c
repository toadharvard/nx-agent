/* fnv-nx L6 deko3d backend: vertex layouts and vertex streams (ops DefineLayout, DefineLayout4, BindVtx, BindVtxN; the layout bind and the pre-draw flush).
 * Reference: gl4_vtx.c (same acceptance, same messages where the check is backend neutral, same pixels). The neutral checks are nxchk_layout4 / nxchk_bind_vtxn.
 * deko3d sources (gpu_3d_vbo.cpp): dkCmdBufBindVtxAttribState writes one record per shader attribute slot (array index = the shader input location) and
 * fills the slots from numAttribs on with IsFixed | 1x32 | Float; dkCmdBufBindVtxBufferState sets stride + divisor of the buffers 0..n-1 (the others are
 * disabled); dkCmdBufBindVtxBuffers binds {gpu address, size} extents, size 0 = an empty region.
 * Rules:
 *  V1 A layout is bound by value: a redefinition takes effect at the next nxdk_vtx_bind_layout (the ABI's "next BindState").
 *  V2 BindVtx / BindVtxN snapshot the range at the bind (the ABI: a snapshot, like every bind) into a host copy. The GPU never reads guest memory in place.
 *  V3 The flush (before a draw, when a bind or a layout bind happened since the last one) stages the snapshots of the streams the bound layout reads into
 *     ONE block of nxdk_stage_alloc (16-byte aligned, CpuUncached: no cache flush) and binds them. The stage list dies at the end of every SUBMIT and the
 *     SUBMIT starts with a BindState, so every layout bind marks the streams dirty again and the block is staged again: no block outlives its submit.
 *  V4 UDEC3 / DEC3N: bits 30-31 of every word of the attribute become 01 (w = +1 as D3D9 reads it) in the staged copy only (gl4_vtx.c stage()).
 *  V5 An attribute slot no attribute of the layout reads keeps deko3d's constant (0, 0, 0, 1) = D3D9's default, as GL's does. */
#include "dk.h"
#include <stdlib.h>
#include <string.h>

#define NEED(n, name) do { if (argc < (n)) { nxdk_err(#name ": %u words, need %u", argc, (unsigned)(n)); return; } } while (0)
#define F(s, t) { DkVtxAttribSize_##s, DkVtxAttribType_##t, 0, 0 }

typedef struct { uint8_t size, type, bgra, fixw; } AttrFmt;
static const AttrFmt F4[NxAttr4_Count] = {
    [NxAttr4_Float1] = F(1x32, Float), [NxAttr4_Float2] = F(2x32, Float), [NxAttr4_Float3] = F(3x32, Float), [NxAttr4_Float4] = F(4x32, Float),
    [NxAttr4_D3DColor] = { DkVtxAttribSize_4x8, DkVtxAttribType_Unorm, 1, 0 }, [NxAttr4_UByte4] = F(4x8, Uscaled),
    [NxAttr4_Short2] = F(2x16, Sscaled), [NxAttr4_Short4] = F(4x16, Sscaled), [NxAttr4_UByte4N] = F(4x8, Unorm),
    [NxAttr4_Short2N] = F(2x16, Snorm), [NxAttr4_Short4N] = F(4x16, Snorm), [NxAttr4_UShort2N] = F(2x16, Unorm), [NxAttr4_UShort4N] = F(4x16, Unorm),
    [NxAttr4_UDec3] = { DkVtxAttribSize_10_10_10_2, DkVtxAttribType_Uscaled, 0, 1 }, [NxAttr4_Dec3N] = { DkVtxAttribSize_10_10_10_2, DkVtxAttribType_Snorm, 0, 1 },
    [NxAttr4_Float16_2] = F(2x16, Float), [NxAttr4_Float16_4] = F(4x16, Float),
};
/* v3 types: Float32, Unorm8, Snorm16, Float16 by components 1..4 */
static const uint8_t SZ3[4][4] = {
    { DkVtxAttribSize_1x32, DkVtxAttribSize_2x32, DkVtxAttribSize_3x32, DkVtxAttribSize_4x32 },
    { DkVtxAttribSize_1x8, DkVtxAttribSize_2x8, DkVtxAttribSize_3x8, DkVtxAttribSize_4x8 },
    { DkVtxAttribSize_1x16, DkVtxAttribSize_2x16, DkVtxAttribSize_3x16, DkVtxAttribSize_4x16 },
    { DkVtxAttribSize_1x16, DkVtxAttribSize_2x16, DkVtxAttribSize_3x16, DkVtxAttribSize_4x16 } };
static const uint8_t TY3[4] = { DkVtxAttribType_Float, DkVtxAttribType_Unorm, DkVtxAttribType_Snorm, DkVtxAttribType_Float };

typedef struct { NxVtxLayout l3; NxVtxLayout4 l4; uint8_t defined, is4; } Layout;
typedef struct { uint8_t* data; uint32_t cap, size, stride; uint8_t bound, isV3; } Stream;
static Layout g_lay[NXDK_MAX_LAYOUTS + 1];
static Stream g_vtx[NX4_MAX_STREAMS];
static Layout g_cur;       /* the bound layout (V1) */
static uint32_t g_curId;   /* 0 = none */
static uint8_t g_dirty;

void nxdk_op_define_layout(const uint32_t* a, uint32_t argc) {
    NEED(10, DefineLayout);
    if (!a[0] || a[0] > NXDK_MAX_LAYOUTS) { nxdk_err("DefineLayout: id %u", a[0]); return; }
    memcpy(&g_lay[a[0]].l3, a + 1, sizeof(NxVtxLayout));
    g_lay[a[0]].is4 = 0; g_lay[a[0]].defined = 1;
}

void nxdk_op_define_layout4(const uint32_t* a, uint32_t argc) {
    if (nxchk_layout4(nxdk_chk_env(), a, argc)) return;
    memcpy(&g_lay[a[0]].l4, a + 1, sizeof(NxVtxLayout4));
    g_lay[a[0]].is4 = 1; g_lay[a[0]].defined = 1;
}

static void bind_stream(uint32_t stream, uint32_t block, uint32_t off, uint32_t size, uint32_t stride, int isV3) {
    Stream* v = &g_vtx[stream];
    g_dirty = 1;
    v->bound = 0;
    if (!block && !size) return;   /* unbinds the stream */
    const NxDkMem* m = nxdk_mem(block);
    if (!m || (uint64_t)off + size > m->size) { nxdk_err("BindVtx: range %u+%u outside block %u", off, size, block); return; }
    if (size > v->cap) {
        uint8_t* n = (uint8_t*)realloc(v->data, size);
        if (!n) { nxdk_err("BindVtx: out of memory"); return; }
        v->data = n; v->cap = size;
    }
    if (size) {
        const void* src = nxdk_guest_ptr(m->storage + off, size);
        if (!src) { nxdk_err("BindVtx: range %u+%u of block %u is unreadable", off, size, block); return; }
        memcpy(v->data, src, size);
    }
    v->size = size; v->stride = stride; v->isV3 = (uint8_t)isV3; v->bound = 1;
}

void nxdk_op_bind_vtx(const uint32_t* a, uint32_t argc) { NEED(3, BindVtx); bind_stream(0, a[0], a[1], a[2], 0, 1); }
void nxdk_op_bind_vtxn(const uint32_t* a, uint32_t argc) {
    if (nxchk_bind_vtxn(nxdk_chk_env(), a, argc)) return;
    bind_stream(a[0], a[1], a[2], a[3], a[4], 0);
}

uint32_t nxdk_vtx_bind_layout(uint32_t id) {
    DkVtxAttribState at[16] = { { 0 } };
    uint32_t n = 0;
    g_dirty = 1; g_curId = 0;
    if (!id || id > NXDK_MAX_LAYOUTS || !g_lay[id].defined) { dkCmdBufBindVtxAttribState(nxdk_s.cmd, at, 0); return 0; }
    const Layout* l = &g_lay[id];
    for (uint32_t i = 0; i < 16; i++) at[i] = (DkVtxAttribState){ .isFixed = 1, .size = DkVtxAttribSize_1x32, .type = DkVtxAttribType_Float };
    if (l->is4) {
        for (uint32_t i = 0; i < l->l4.attrCount; i++) {
            const NxVtxAttr4* v = &l->l4.attr[i];
            const AttrFmt* f = &F4[v->type];
            at[v->location] = (DkVtxAttribState){ .bufferId = v->stream, .offset = v->offset, .size = f->size, .type = f->type, .isBgra = f->bgra };
            if (v->location >= n) n = v->location + 1u;
        }
    } else {
        if (l->l3.count > 8) { nxdk_err("layout %u: %u attributes", id, l->l3.count); return 1; }
        for (uint32_t i = 0; i < l->l3.count; i++) {
            const uint32_t t = l->l3.attr[i].type, c = l->l3.attr[i].components ? l->l3.attr[i].components : 1u;
            const uint32_t loc = l->l3.attr[i].location ? l->l3.attr[i].location - 1u : i;
            if (loc > 15) { nxdk_err("layout %u: location %u", id, loc); return 1; }
            if (t > NxAttr_Float16 || c > 4) { nxdk_err("layout %u: attribute %u has type %u with %u components", id, i, t, c); return 1; }
            at[loc] = (DkVtxAttribState){ .offset = l->l3.attr[i].offset, .size = SZ3[t][c - 1], .type = TY3[t] };
            if (loc >= n) n = loc + 1u;
        }
    }
    dkCmdBufBindVtxAttribState(nxdk_s.cmd, at, n);
    g_cur = *l; g_curId = id;
    return 0;
}

/* V4: the staged copy of stream s, bits 30-31 of the words of the UDEC3 / DEC3N attributes of the bound layout := 01 */
static void fix_w(uint8_t* dst, uint32_t s, const Stream* v) {
    for (uint32_t i = 0; g_cur.is4 && i < g_cur.l4.attrCount; i++) {
        const NxVtxAttr4* at = &g_cur.l4.attr[i];
        if (at->stream != s || !F4[at->type].fixw) continue;
        for (uint64_t pos = at->offset; pos + 4 <= v->size; pos += v->stride) {
            uint32_t word;
            memcpy(&word, dst + pos, 4);
            word = (word & 0x3FFFFFFFu) | 0x40000000u;
            memcpy(dst + pos, &word, 4);
            if (!v->stride) break;
        }
    }
}

uint32_t nxdk_vtx_flush(void) {
    DkVtxBufferState bs[NX4_MAX_STREAMS];
    DkBufExtents ex[NX4_MAX_STREAMS];
    uint32_t off[NX4_MAX_STREAMS], len[NX4_MAX_STREAMS], mask = 0, errs = 0;
    uint64_t total = 0;
    if (!g_dirty) return 0;
    g_dirty = 0;
    if (!g_curId) return 0;   /* nothing is bound: a draw reads no vertex */
    if (g_cur.is4) for (uint32_t i = 0; i < g_cur.l4.attrCount; i++) mask |= 1u << g_cur.l4.attr[i].stream;
    else mask = 1;
    if (!mask) return 0;
    const uint32_t nb = 32u - (uint32_t)__builtin_clz(mask);
    for (uint32_t s = 0; s < nb; s++) {
        const Stream* v = &g_vtx[s];
        bs[s] = (DkVtxBufferState){ 0, 0 }; len[s] = 0; off[s] = 0;
        if (!(mask >> s & 1)) continue;
        if (g_cur.is4) {
            if (!v->bound || v->isV3) { nxdk_err("draw: layout %u reads stream %u, which has no BindVtxN", g_curId, s); errs++; continue; }
            bs[s].stride = v->stride;
            bs[s].divisor = (g_cur.l4.instanceStreams >> s & 1) ? g_cur.l4.divisor[s] : 0;
        } else {
            bs[s].stride = v->bound && !v->isV3 ? v->stride : g_cur.l3.stride;
            if (!v->bound) continue;
        }
        total = (total + 15) & ~(uint64_t)15; off[s] = (uint32_t)total; len[s] = v->size; total += v->size;
        if (total > UINT32_MAX) { nxdk_err("draw: the vertex streams of layout %u need %llu bytes", g_curId, (unsigned long long)total); return 1; }
    }
    DkMemBlock blk = NULL;
    if (total) {
        blk = nxdk_stage_alloc((uint32_t)total);
        if (!blk) return 1;
        uint8_t* dst = (uint8_t*)dkMemBlockGetCpuAddr(blk);
        for (uint32_t s = 0; s < nb; s++) if (len[s]) { memcpy(dst + off[s], g_vtx[s].data, len[s]); fix_w(dst + off[s], s, &g_vtx[s]); }
    }
    for (uint32_t s = 0; s < nb; s++) ex[s] = (DkBufExtents){ len[s] ? dkMemBlockGetGpuAddr(blk) + off[s] : 0, len[s] };
    dkCmdBufBindVtxBufferState(nxdk_s.cmd, bs, nb);
    dkCmdBufBindVtxBuffers(nxdk_s.cmd, 0, ex, nb);
    return errs != 0;
}
