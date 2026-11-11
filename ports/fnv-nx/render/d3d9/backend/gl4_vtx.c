/* fnv-nx L6 GL 4.5 backend v4: vertex layouts and vertex streams.
 *
 * v4 layouts (NxOp_DefineLayout4) take up to 16 attributes from up to 16 streams; the attribute's `location` is the
 * shader input location (0-15). BindVtxN snapshots the bound range of a memory block (copy at bind, like every
 * bind of this backend); a draw stages the snapshots of the streams its layout reads into per-stream GL buffers
 * and binds them at offset 0. v3 layouts and BindVtx (stream 0, stride of the v3 layout) are served by the same
 * streams. A redefinition of a layout takes effect at the next BindState.
 *
 * Attribute types -> GL (the shader always sees D3D9's expansion: missing components 0, 0, 0, 1; GL's default for
 * a missing y, z, w is exactly that):
 *   Float1..4        GL float, 1..4 components
 *   D3DColor         GL_BGRA, unsigned byte, normalized: bytes B,G,R,A -> (R,G,B,A) / 255
 *   UByte4           4 x unsigned byte, not normalized (values 0..255 as floats)
 *   Short2 / Short4  signed short, not normalized
 *   UByte4N          4 x unsigned byte / 255;  UShort2N / UShort4N  unsigned short / 65535
 *   Short2N / Short4N  signed short / 32767, with -32768 -> -1.0 (GL's snorm rule; the D3D9 formula c / 32767 would
 *                    give -1.00003 for that one code; D3D9 drivers on D3D10 hardware clamp the same way)
 *   Float16_2/_4     GL half float: exact conversion to float
 *   UDec3            GL_UNSIGNED_INT_2_10_10_10_REV, size 4, not normalized: x, y, z = the 10-bit fields as
 *                    floats 0..1023; GL would read w from bits 30-31, D3D9 ignores them and gives w = 1:
 *                    EXACT CONVERSION: in the staged copy bits 30-31 of every UDEC3 word are set to 01 (w = 1)
 *   Dec3N            GL_INT_2_10_10_10_REV, size 4, normalized: x, y, z = signed 10-bit / 511 (-512 -> -1.0 as for
 *                    the short types); same conversion, bits 30-31 := 01 so that w = +1 (a signed 2-bit field)
 * The conversion is applied only to the staged copy and only for the streams whose layout has such attributes, so a
 * range bound once reads correctly through layouts that type the same bytes differently. */
#include "gl4.h"
#include <stdlib.h>
#include <string.h>

#define NEED(n, name) do { if (argc < (n)) { gl4_err(#name ": %u words, need %u", argc, (unsigned)(n)); return; } } while (0)

typedef struct { GLint size; GLenum type; GLboolean norm; uint8_t fixw; } AttrFmt;

static AttrFmt attr_fmt(uint32_t type) {
    switch ((enum NxAttrType4)type) {
    case NxAttr4_Float1: return (AttrFmt){1, GL_FLOAT, GL_FALSE, 0};
    case NxAttr4_Float2: return (AttrFmt){2, GL_FLOAT, GL_FALSE, 0};
    case NxAttr4_Float3: return (AttrFmt){3, GL_FLOAT, GL_FALSE, 0};
    case NxAttr4_Float4: return (AttrFmt){4, GL_FLOAT, GL_FALSE, 0};
    case NxAttr4_D3DColor: return (AttrFmt){GL_BGRA, GL_UNSIGNED_BYTE, GL_TRUE, 0};
    case NxAttr4_UByte4: return (AttrFmt){4, GL_UNSIGNED_BYTE, GL_FALSE, 0};
    case NxAttr4_Short2: return (AttrFmt){2, GL_SHORT, GL_FALSE, 0};
    case NxAttr4_Short4: return (AttrFmt){4, GL_SHORT, GL_FALSE, 0};
    case NxAttr4_UByte4N: return (AttrFmt){4, GL_UNSIGNED_BYTE, GL_TRUE, 0};
    case NxAttr4_Short2N: return (AttrFmt){2, GL_SHORT, GL_TRUE, 0};
    case NxAttr4_Short4N: return (AttrFmt){4, GL_SHORT, GL_TRUE, 0};
    case NxAttr4_UShort2N: return (AttrFmt){2, GL_UNSIGNED_SHORT, GL_TRUE, 0};
    case NxAttr4_UShort4N: return (AttrFmt){4, GL_UNSIGNED_SHORT, GL_TRUE, 0};
    case NxAttr4_UDec3: return (AttrFmt){4, GL_UNSIGNED_INT_2_10_10_10_REV, GL_FALSE, 1};
    case NxAttr4_Dec3N: return (AttrFmt){4, GL_INT_2_10_10_10_REV, GL_TRUE, 1};
    case NxAttr4_Float16_2: return (AttrFmt){2, GL_HALF_FLOAT, GL_FALSE, 0};
    case NxAttr4_Float16_4: return (AttrFmt){4, GL_HALF_FLOAT, GL_FALSE, 0};
    case NxAttr4_Count: break;
    }
    return (AttrFmt){0, 0, 0, 0};   /* not a type: nxchk_layout4 refuses it */
}

/* ---- layouts ----------------------------------------------------------------------------------------------- */
void gl4_op_define_layout(const uint32_t* a, uint32_t argc) {
    NEED(10, DefineLayout);
    if (!a[0] || a[0] > GL4_MAX_LAYOUTS) { gl4_err("DefineLayout: id %u", a[0]); return; }
    Gl4Layout* l = &g4.lay[a[0]];
    memcpy(&l->l, a + 1, sizeof(NxVtxLayout));
    l->is4 = 0; l->defined = 1; l->stale = 1; l->gen++;
}

void gl4_op_define_layout4(const uint32_t* a, uint32_t argc) {
    if (nxchk_layout4(gl4_chk_env(), a, argc)) return;
    Gl4Layout* l = &g4.lay[a[0]];
    memcpy(&l->l4, a + 1, sizeof l->l4);
    l->is4 = 1; l->defined = 1; l->stale = 1; l->gen++;
}

static void build_v3(Gl4Layout* l, uint32_t id) {
    if (l->l.count > 8) gl4_err("layout %u: %u attributes", id, l->l.count);
    for (uint32_t i = 0; i < l->l.count && i < 8; i++) {
        GLint c = l->l.attr[i].components ? l->l.attr[i].components : 1;
        GLenum type = GL_FLOAT; GLboolean norm = GL_FALSE;
        switch ((enum NxAttrType)l->l.attr[i].type) {
        case NxAttr_Float32: break;
        case NxAttr_Unorm8: type = GL_UNSIGNED_BYTE; norm = GL_TRUE; break;
        case NxAttr_Snorm16: type = GL_SHORT; norm = GL_TRUE; break;
        case NxAttr_Float16: type = GL_HALF_FLOAT; break;
        }
        GLuint loc = l->l.attr[i].location ? (GLuint)(l->l.attr[i].location - 1) : i;
        if (loc > 15) { gl4_err("layout %u: location %u", id, loc); break; }
        p_glVertexAttribFormat(loc, c, type, norm, l->l.attr[i].offset);
        p_glVertexAttribBinding(loc, 0);
        p_glEnableVertexAttribArray(loc);
    }
}

static void build_v4(const Gl4Layout* l) {
    for (uint32_t i = 0; i < l->l4.attrCount; i++) {
        const NxVtxAttr4* at = &l->l4.attr[i];
        AttrFmt f = attr_fmt(at->type);
        p_glVertexAttribFormat(at->location, f.size, f.type, f.norm, at->offset);
        p_glVertexAttribBinding(at->location, at->stream);
        p_glEnableVertexAttribArray(at->location);
    }
    for (uint32_t s = 0; s < NX4_MAX_STREAMS; s++)
        p_glVertexBindingDivisor(s, (l->l4.instanceStreams >> s & 1) ? l->l4.divisor[s] : 0);
}

void gl4_bind_layout(uint32_t lay) {
    if (!lay || lay > GL4_MAX_LAYOUTS || !g4.lay[lay].defined) { p_glBindVertexArray(g4.lay[0].vao); return; }
    Gl4Layout* l = &g4.lay[lay];
    if (!l->vao || l->stale) {
        if (l->vao) p_glDeleteVertexArrays(1, &l->vao);
        p_glGenVertexArrays(1, &l->vao);
        p_glBindVertexArray(l->vao);
        if (l->is4) build_v4(l); else build_v3(l, lay);
        l->stale = 0;
    }
    p_glBindVertexArray(l->vao);
}

/* ---- streams ----------------------------------------------------------------------------------------------- */
static void bind_stream(uint32_t stream, uint32_t block, uint32_t off, uint32_t size, uint32_t stride, int isV3) {
    Gl4Vtx* v = &g4.vtx[stream];
    v->bindSeq++;
    g4.vtxDirty = 1;
    if (!block && !size) { v->bound = 0; return; }   /* unbinds the stream */
    const Gl4Mem* m = gl4_mem(block);
    if (!m || (uint64_t)off + size > m->size) { gl4_err("BindVtx: range %u+%u outside block %u", off, size, block); v->bound = 0; return; }
    if (size > v->cap) {
        uint8_t* n = (uint8_t*)realloc(v->data, size);
        if (!n) { gl4_err("BindVtx: out of memory"); v->bound = 0; return; }
        v->data = n; v->cap = size;
    }
    if (size) memcpy(v->data, nxgl4_guest_ptr(m->storage + off, size), size);
    v->size = size; v->stride = stride; v->isV3 = (uint8_t)isV3; v->bound = 1;
}

void gl4_op_bind_vtx(const uint32_t* a, uint32_t argc) { NEED(3, BindVtx); bind_stream(0, a[0], a[1], a[2], 0, 1); }
void gl4_op_bind_vtxn(const uint32_t* a, uint32_t argc) {
    if (nxchk_bind_vtxn(gl4_chk_env(), a, argc)) return;
    bind_stream(a[0], a[1], a[2], a[3], a[4], 0);
}

/* the staged copy of stream s: the snapshot, with bits 30-31 of every UDEC3 / DEC3N word set to 01 for the
 * attributes of l that read it. sig identifies what was staged (0 = the snapshot unchanged). */
static void stage(uint32_t s, const Gl4Layout* l, uint32_t lay) {
    Gl4Vtx* v = &g4.vtx[s];
    uint64_t sig = 0;
    for (uint32_t i = 0; l->is4 && i < l->l4.attrCount; i++)
        if (l->l4.attr[i].stream == s && attr_fmt(l->l4.attr[i].type).fixw) sig = ((uint64_t)lay << 32 | l->gen) + 1;
    if (v->buf && v->stagedSeq == v->bindSeq && v->stagedSig == sig) return;   /* staged already */
    uint8_t* tmp = NULL;
    const uint8_t* src = v->data;
    if (sig) {
        tmp = (uint8_t*)malloc(v->size ? v->size : 1);
        if (!tmp) { gl4_err("vertex staging: out of memory"); return; }
        if (v->size) memcpy(tmp, v->data, v->size);
        src = tmp;
        for (uint32_t i = 0; i < l->l4.attrCount; i++) {
            const NxVtxAttr4* at = &l->l4.attr[i];
            if (at->stream != s || !attr_fmt(at->type).fixw) continue;
            for (uint64_t pos = at->offset; pos + 4 <= v->size; pos += v->stride) {
                uint32_t word;
                memcpy(&word, tmp + pos, 4);
                word = (word & 0x3FFFFFFFu) | 0x40000000u;
                memcpy(tmp + pos, &word, 4);
                if (!v->stride) break;
            }
        }
    }
    if (!v->buf) p_glGenBuffers(1, &v->buf);
    p_glBindBuffer(GL_COPY_WRITE_BUFFER, v->buf);
    p_glBufferData(GL_COPY_WRITE_BUFFER, v->size, src, GL_DYNAMIC_DRAW);
    v->stagedSeq = v->bindSeq; v->stagedSig = sig;
    free(tmp);
}

int gl4_vtx_flush(void) {
    if (!g4.vtxDirty) return 0;
    uint32_t lay = g4.st[g4.boundState].layout, errs = g4.errCount;
    g4.vtxDirty = 0;
    if (!lay || lay > GL4_MAX_LAYOUTS || !g4.lay[lay].defined) return 0;
    const Gl4Layout* l = &g4.lay[lay];
    uint32_t streams = 0;
    if (!l->is4) {
        Gl4Vtx* v = &g4.vtx[0];
        if (!v->bound) return 0;   /* a v3 layout draw without a vertex buffer reads nothing */
        stage(0, l, lay);
        p_glBindVertexBuffer(0, v->buf, 0, (GLsizei)(v->isV3 ? l->l.stride : v->stride));
        return g4.errCount != errs;
    }
    for (uint32_t i = 0; i < l->l4.attrCount; i++) streams |= 1u << l->l4.attr[i].stream;
    for (uint32_t s = 0; s < NX4_MAX_STREAMS; s++) {
        if (!(streams >> s & 1)) continue;
        Gl4Vtx* v = &g4.vtx[s];
        if (!v->bound || v->isV3) { gl4_err("draw: layout %u reads stream %u, which has no BindVtxN", lay, s); continue; }
        stage(s, l, lay);
        p_glBindVertexBuffer(s, v->buf, 0, (GLsizei)v->stride);
    }
    return g4.errCount != errs;
}
