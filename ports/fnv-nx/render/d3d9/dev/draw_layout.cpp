// draw_layout.cpp: vertex layouts. A layout is a function of (bound declaration, vertex shader input signature, stream frequencies, which streams have data), cached by content.
//  L1 each shader input (usage, index) takes the declaration element of the same (usage, index); with several such elements the LAST one wins (wined3d_stream_info_from_declaration walks the elements in order and
//     each match overwrites the input's slot). The attribute's location is the input's location (draw_prog.cpp P1), its type nx_attr_type(D3DDECLTYPE), its offset the element's Offset.
//  L2 a declaration element no input reads is not an attribute (nxgpu4.h).
//  L3 UNBOUND INPUT RULE: an input with no declaration element reads the constant (0, 0, 0, 0): Wine 9.0 wined3d_context_gl_load_numbered_arrays disables the array of an input without element and sets the
//     default attribute value glVertexAttrib4f(i, 0, 0, 0, 0) (1,1,1,1 only for COLOR0 of the fixed-function pipeline, whose vertex program the fixed-function module provides: it reads the inputs its
//     decl-flags name, draw_ffp.h). Here it is a Float4 attribute of one extra stream with stride 0 over 16 zero bytes (BindVtxN: "stride 0 = one element for every vertex"). A free stream always exists: at most 16 inputs,
//     one of them unbound, so at most 15 streams carry real attributes.
//  L4 instancing: stream s with D3DSTREAMSOURCE_INSTANCEDATA in the frequency word is an instance stream with divisor = the low 23 bits (>= 1: SetStreamSourceFreq refuses 0); every other stream (INDEXEDDATA or none) is per vertex.
//  L5 an input that takes an element of a stream with no vertex data (no buffer bound, no UP data) = the draw reads an unbound stream: nx_layout_build returns false and the draw has no vertex data (draw_api.cpp).
#include "draw_core.h"

bool nx_layout_build(const NxResDecl* decl, const NxVsSig& sig, const uint32_t* freq, uint32_t bound, NxLayout* out) {
    memset((void*)out, 0, sizeof *out); out->zero_stream = -1; NxVtxLayout4& L = out->v; uint32_t used = 0, nunb = 0, unb[16];
    for (uint32_t k = 0; k < sig.n; k++) {
        const D3DVERTEXELEMENT9* e = nullptr;
        for (uint32_t i = 0; i + 1 < decl->n; i++) { const D3DVERTEXELEMENT9& c = decl->el[i]; if (c.Stream < 16 && FNVNX_DK_PAIR(c.Usage, c.UsageIndex) == sig.pair[k] && c.Usage <= 13 && c.UsageIndex <= 15) e = &c; }
        if (!e) { unb[nunb++] = k; continue; }
        if (!(bound >> e->Stream & 1)) return false;
        NxVtxAttr4& a = L.attr[L.attrCount++]; a.offset = (uint16_t)e->Offset; a.stream = (uint8_t)e->Stream; a.type = (uint8_t)nx_attr_type(e->Type); a.location = sig.loc[k]; used |= 1u << e->Stream;
    }
    out->streams = used;
    if (nunb) {
        uint32_t z = 0; while (used >> z & 1) z++;
        out->zero_stream = (int32_t)z;
        for (uint32_t j = 0; j < nunb; j++) { NxVtxAttr4& a = L.attr[L.attrCount++]; a.offset = 0; a.stream = (uint8_t)z; a.type = NxAttr4_Float4; a.location = sig.loc[unb[j]]; }
    }
    for (uint32_t s = 0; s < NX4_MAX_STREAMS; s++) if ((used >> s & 1) && (freq[s] & NXD_STREAM_INSTANCE)) { L.instanceStreams |= 1u << s; L.divisor[s] = freq[s] & NXD_FREQ_MASK ? freq[s] & NXD_FREQ_MASK : 1; }
    return true;
}
