// pvw_stream.cpp: wined3d_stream_info_from_declaration (wine-9.0 dlls/wined3d/context.c) for the fixed-function inputs, as process_vertices uses it (the vertex shader is hidden: use_vs() is false).
// Per declaration element, in order: an element whose stream has no buffer is skipped; fixed_get_input maps (usage, index) to a slot: POSITION or POSITIONT index 0 -> position, BLENDWEIGHT 0, BLENDINDICES 0, NORMAL 0, PSIZE 0,
// COLOR 0 -> diffuse, COLOR 1 -> specular, TEXCOORD n < 8 -> texcoord n; anything else is not used (WARN only). With generic attributes (Wine's GLSL backend: ffp_generic_attributes = TRUE) no element is dropped for its format.
// A later element of the same slot overwrites an earlier one. Stride, instancing flags and the stream frequency play no role. The element address is (u32)(src_start_idx * stride) + stream offset + element offset into the buffer
// (the mapping box of wined3d_device_process_vertices starts at src_start_idx * stride of each used element; stream offsets are added on top).
#include "pvw.h"

void pvw_stream_info(PvInfo* si, const NxDeviceCore* d, uint32_t start) {
    memset((void*)si, 0, sizeof *si);
    const NxDeviceState& s = d->cur; if (!s.vdecl[0]) return;
    const NxResDecl* decl = static_cast<const NxResDecl*>(nx_obj_of((IUnknown*)s.vdecl[0]));
    for (uint32_t k = 0; k + 1 < decl->n; k++) {
        const D3DVERTEXELEMENT9& e = decl->el[k]; if (e.Stream >= NX_D3D_MAX_STREAMS || !s.stream[e.Stream].vb) continue;
        uint32_t slot;
        if ((e.Usage == D3DDECLUSAGE_POSITION || e.Usage == D3DDECLUSAGE_POSITIONT) && !e.UsageIndex) slot = 0;
        else if (e.Usage == D3DDECLUSAGE_BLENDWEIGHT && !e.UsageIndex) slot = 1;
        else if (e.Usage == D3DDECLUSAGE_BLENDINDICES && !e.UsageIndex) slot = 2;
        else if (e.Usage == D3DDECLUSAGE_NORMAL && !e.UsageIndex) slot = 3;
        else if (e.Usage == D3DDECLUSAGE_PSIZE && !e.UsageIndex) slot = 4;
        else if (e.Usage == D3DDECLUSAGE_COLOR && e.UsageIndex < 2) slot = 5 + e.UsageIndex;
        else if (e.Usage == D3DDECLUSAGE_TEXCOORD && e.UsageIndex < 8) slot = 7 + e.UsageIndex;
        else continue;
        const NxStream& st = s.stream[e.Stream]; PvElem& o = si->e[slot];
        o.buf = static_cast<const NxBufCore*>(nx_obj_of((IUnknown*)st.vb)); o.stride = st.stride; o.type = e.Type;
        o.base = (uint64_t)(uint32_t)(start * st.stride) + st.offset + e.Offset; si->use_map |= 1u << slot;
    }
}
