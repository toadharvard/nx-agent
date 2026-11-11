/* fnv-nx L6 nxgpu v4: backend-neutral argument and stream checks (see nxgpu4_check.h). Moved from gl4_check.c,
 * gl4_pass.c and gl4_vtx.c without a change of rule, message or order.
 *   BeginPass4  targets that disagree in size or sample count, bad level / layer, no target at all;
 *   DefineLayout4, BindVtxN  attribute types, streams, locations, divisors and strides outside the ABI or the limits;
 *   DefineState  render-state w[3] bits that are reserved.
 * NXRS_M_LASTPIXEL is not checked: it has no GL state and never refuses a draw (Wine 9.0 wined3d keeps WINED3D_RS_LASTPIXEL
 * without a GL state and draws GL's diamond-exit lines whatever its value; the device logs the FIXME). */
#include "nxgpu4_check.h"
#include <string.h>

#define W3_RESERVED 0xFFFFFFF0ull   /* bits 4..31 of w[3]: NXRS_M_* use bits 0-3 and 32-63 */
#define L4WORDS (sizeof(NxVtxLayout4) / 4)

uint32_t nxchk_attr4_bytes(uint32_t type) {
    switch ((enum NxAttrType4)type) {
    case NxAttr4_Float1: case NxAttr4_D3DColor: case NxAttr4_UByte4: case NxAttr4_Short2: case NxAttr4_UByte4N:
    case NxAttr4_Short2N: case NxAttr4_UShort2N: case NxAttr4_UDec3: case NxAttr4_Dec3N: case NxAttr4_Float16_2:
        return 4;
    case NxAttr4_Float2: case NxAttr4_Short4: case NxAttr4_Short4N: case NxAttr4_UShort4N: case NxAttr4_Float16_4:
        return 8;
    case NxAttr4_Float3: return 12;
    case NxAttr4_Float4: return 16;
    case NxAttr4_Count: break;
    }
    return 0;
}

static uint32_t layers_of(const NxChkImage* im, uint32_t level) {
    return im->type == NxImgType_Cube ? 6u : im->type == NxImgType_3D ? nxchk_lvl(im->d, level) : 1u;
}

int nxchk_pass_parse4(const NxChkEnv* e, const uint32_t* a, uint32_t argc, NxChkPass* p) {
    memset(p, 0, sizeof *p);
    if (argc < 1 || a[0] > 4 || argc < 3 + 2 * a[0]) { nxchk_err("BeginPass4: %u words for %u colour targets", argc, argc ? a[0] : 0); return 1; }
    p->nColor = a[0];
    for (uint32_t i = 0; i < a[0]; i++) {
        p->c[i].image = a[1 + 2 * i] ? a[1 + 2 * i] : e->swapImage;
        p->c[i].sub = a[2 + 2 * i];
        if (!p->c[i].image) { nxchk_err("BeginPass4: colour target %u is the swapchain, which does not exist", i); return 1; }
    }
    p->d.image = a[1 + 2 * a[0]]; p->d.sub = a[2 + 2 * a[0]];
    return 0;
}

/* sizes: every target's subresource must have the size and sample count of the first */
int nxchk_pass_check(const NxChkEnv* e, const NxChkPass* p, uint32_t* w, uint32_t* h, uint32_t* samples) {
    if (!p->nColor && !p->d.image) { nxchk_err("pass: no target"); return 1; }
    int have = 0;
    for (uint32_t i = 0; i < p->nColor + 1; i++) {
        const NxChkTgt* t = i < p->nColor ? &p->c[i] : &p->d;
        if (i == p->nColor && !t->image) break;
        NxChkImage im;
        uint32_t level = t->sub & 0xFFFF, layer = t->sub >> 16;
        if (!e->image(e->ctx, t->image, &im) || im.isView || (i < p->nColor ? im.isDepth || im.compressed : !im.isDepth)) {
            nxchk_err("pass: target %u (image %u) is not a %s image", i, t->image, i < p->nColor ? "colour" : "depth-stencil");
            return 1;
        }
        if (level >= im.mips || layer >= layers_of(&im, level)) { nxchk_err("pass: target %u: level %u layer %u outside image %u", i, level, layer, t->image); return 1; }
        uint32_t tw = nxchk_lvl(im.w, level), th = nxchk_lvl(im.h, level);
        if (!have) { *w = tw; *h = th; *samples = im.samples; have = 1; }
        else if (tw != *w || th != *h || im.samples != *samples) {
            nxchk_err("pass: target %u is %ux%u x%u samples, the pass is %ux%u x%u", i, tw, th, im.samples, *w, *h, *samples);
            return 1;
        }
    }
    return 0;
}

int nxchk_layout4(const NxChkEnv* e, const uint32_t* a, uint32_t argc) {
    int bad = 0;
    if (argc < 1 + L4WORDS) { nxchk_err("DefineLayout4: %u words, need %u", argc, (unsigned)(1 + L4WORDS)); return 1; }
    if (!a[0] || a[0] > NXCHK_MAX_LAYOUTS) { nxchk_err("DefineLayout4: id %u", a[0]); return 1; }
    NxVtxLayout4 l;
    memcpy(&l, a + 1, sizeof l);
    if (l.attrCount > NX4_MAX_ATTRS || l.instanceStreams >> NX4_MAX_STREAMS) {
        nxchk_err("DefineLayout4 %u: %u attributes, instance streams 0x%x", a[0], l.attrCount, l.instanceStreams);
        return 1;
    }
    uint32_t used = 0;
    for (uint32_t i = 0; i < l.attrCount; i++) {
        const NxVtxAttr4* at = &l.attr[i];
        if (!nxchk_attr4_bytes(at->type) || at->stream >= NX4_MAX_STREAMS || at->location >= NX4_MAX_ATTRS ||
            (used >> at->location & 1) || at->offset > (uint32_t)e->maxAttrOffset ||
            ((l.instanceStreams >> at->stream & 1) && !l.divisor[at->stream])) {
            nxchk_err("DefineLayout4 %u: attribute %u (type %u stream %u location %u offset %u) is invalid", a[0], i, at->type, at->stream, at->location, at->offset);
            bad = 1;
        }
        used |= 1u << (at->location & 31);
    }
    return bad;
}

int nxchk_bind_vtxn(const NxChkEnv* e, const uint32_t* a, uint32_t argc) {
    if (argc < 5) { nxchk_err("BindVtxN: %u words", argc); return 1; }
    if (a[0] >= NX4_MAX_STREAMS || a[4] > (uint32_t)e->maxVtxStride) {
        nxchk_err("BindVtxN: stream %u stride %u (max %d)", a[0], a[4], (int)e->maxVtxStride);
        return 1;
    }
    return 0;
}

int nxchk_stream(const NxChkEnv* e, const uint32_t* w, const uint32_t* end) {
    while (w < end) {
        uint32_t op = w[0] & 0xFFFF, argc = w[0] >> 16;
        const uint32_t* a = w + 1;
        if (op == NxOp_End) break;
        if (op == NxOp_DefineState && argc >= 12 && a[0] && a[0] <= NXCHK_MAX_STATES) {
            uint64_t w3 = a[10] | (uint64_t)a[11] << 32;
            if (w3 & W3_RESERVED) { nxchk_err("DefineState %u: reserved w[3] bits 0x%llx", a[0], (unsigned long long)(w3 & W3_RESERVED)); return 0; }
        } else if (op == NxOp_BeginPass4) {
            NxChkPass p;
            uint32_t pw, ph, ps;
            if (nxchk_pass_parse4(e, a, argc, &p) || nxchk_pass_check(e, &p, &pw, &ph, &ps)) return 0;
        } else if (op == NxOp_DefineLayout4) {
            if (nxchk_layout4(e, a, argc)) return 0;
        } else if (op == NxOp_BindVtxN) {
            if (nxchk_bind_vtxn(e, a, argc)) return 0;
        }
        w += 1 + argc;
    }
    return 1;
}
