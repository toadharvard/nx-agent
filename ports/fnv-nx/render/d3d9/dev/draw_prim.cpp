// draw_prim.cpp: primitive types, vertex counts, instance count, D3DDECLTYPE -> NxAttrType4 (rules of the draw path, README of draw_api.cpp).
// Sources: Wine 9.0 d3d9/device.c vertex_count_from_primitive_count (the formula table below), wined3d_primitive_type_from_d3d; instance count: Wine d3d9_device_DrawIndexedPrimitive passes
// stateblock_state->streams[0].frequency as the instance count and wined3d_stateblock_set_stream_source_freq stores `divider & 0x7fffff` there; "Instancing is ignored for non-indexed draws" (Wine comments
// in DrawPrimitive / DrawPrimitiveUP). Both formulas are total switches over the enumeration (-Werror=switch): an enumerator without a case does not build; a DWORD that is no enumerator is tested first.
#include "draw_core.h"

bool nx_prim_valid(uint32_t t) { return t >= D3DPT_POINTLIST && t <= D3DPT_TRIANGLEFAN; }
// UINT arithmetic like Wine (a count that overflows wraps); t must be valid
uint32_t nx_prim_vertices(uint32_t t, uint32_t n) {
    switch ((D3DPRIMITIVETYPE)t) {
    case D3DPT_POINTLIST: return n;
    case D3DPT_LINELIST: return n * 2;
    case D3DPT_LINESTRIP: return n + 1;
    case D3DPT_TRIANGLELIST: return n * 3;
    case D3DPT_TRIANGLESTRIP: return n + 2;
    case D3DPT_TRIANGLEFAN: return n + 2;
    case D3DPT_FORCE_DWORD: return 0;
    }
    return 0;
}
uint32_t nx_prim_nx(uint32_t t) {
    switch ((D3DPRIMITIVETYPE)t) {
    case D3DPT_POINTLIST: return NxPrim_Points;
    case D3DPT_LINELIST: return NxPrim_Lines;
    case D3DPT_LINESTRIP: return NxPrim_LineStrip;
    case D3DPT_TRIANGLELIST: return NxPrim_Triangles;
    case D3DPT_TRIANGLESTRIP: return NxPrim_TriStrip;
    case D3DPT_TRIANGLEFAN: return NxPrim_TriFan;
    case D3DPT_FORCE_DWORD: return NxPrim_Triangles;
    }
    return NxPrim_Triangles;
}
uint32_t nx_instances(const NxDeviceState& s) { return s.freq[0] & NXD_FREQ_MASK; }

uint32_t nx_attr_type(uint32_t t) {   // t < D3DDECLTYPE_UNUSED (declarations are validated at creation)
    switch ((D3DDECLTYPE)t) {
    case D3DDECLTYPE_FLOAT1: return NxAttr4_Float1;
    case D3DDECLTYPE_FLOAT2: return NxAttr4_Float2;
    case D3DDECLTYPE_FLOAT3: return NxAttr4_Float3;
    case D3DDECLTYPE_FLOAT4: return NxAttr4_Float4;
    case D3DDECLTYPE_D3DCOLOR: return NxAttr4_D3DColor;
    case D3DDECLTYPE_UBYTE4: return NxAttr4_UByte4;
    case D3DDECLTYPE_SHORT2: return NxAttr4_Short2;
    case D3DDECLTYPE_SHORT4: return NxAttr4_Short4;
    case D3DDECLTYPE_UBYTE4N: return NxAttr4_UByte4N;
    case D3DDECLTYPE_SHORT2N: return NxAttr4_Short2N;
    case D3DDECLTYPE_SHORT4N: return NxAttr4_Short4N;
    case D3DDECLTYPE_USHORT2N: return NxAttr4_UShort2N;
    case D3DDECLTYPE_USHORT4N: return NxAttr4_UShort4N;
    case D3DDECLTYPE_UDEC3: return NxAttr4_UDec3;
    case D3DDECLTYPE_DEC3N: return NxAttr4_Dec3N;
    case D3DDECLTYPE_FLOAT16_2: return NxAttr4_Float16_2;
    case D3DDECLTYPE_FLOAT16_4: return NxAttr4_Float16_4;
    case D3DDECLTYPE_UNUSED: return NxAttr4_Float4;
    }
    return NxAttr4_Float4;
}
