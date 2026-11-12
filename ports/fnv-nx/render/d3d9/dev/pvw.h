// pvw.h: IDirect3DDevice9::ProcessVertices as Wine 9.0 does it (task PVW). Shared declarations of pvw_math.cpp, pvw_light.cpp, pvw_core.cpp, pvw_api.cpp.
// Sources (wine-9.0): dlls/d3d9/device.c d3d9_device_ProcessVertices; dlls/wined3d/device.c wined3d_device_process_vertices, process_vertices_strided, init_transformed_lights, light_set_vertex_data,
// compute_light, update_light_diffuse_specular, update_fog_factor, wined3d_colour_from_mcs, wined3d_format_get_colour; dlls/wined3d/context.c wined3d_stream_info_from_declaration;
// dlls/wined3d/utils.c multiply_matrix, invert_matrix, compute_normal_matrix, wined3d_format_convert_from_float. Rules and decisions: pvw_api.cpp header.
#pragma once
#include "res_core.h"
struct PvVec3 { float x, y, z; };
struct PvVec4 { float x, y, z, w; };
struct PvCol { float r, g, b, a; };
struct PvMat { float m[4][4]; };                 // m[row][col] = wined3d _{row+1}{col+1}
void pvw_mul(PvMat* dst, const PvMat* s1, const PvMat* s2);      // multiply_matrix: dst = s2 x s1 in row-vector convention (Wine's argument order), dst may alias
bool pvw_invert(PvMat* out, const PvMat* in);                    // invert_matrix: false (out untouched) for a singular matrix
float pvw_dot(const PvVec3* a, const PvVec3* b);                 // x*x' + y*y' + z*z' left to right
void pvw_normalise(PvVec3* v);                                   // wined3d_vec3_normalise: scales by 1/sqrtf(dot) iff that is finite
void pvw_xform4(PvVec4* dst, const PvVec4* v, const PvMat* m);   // wined3d_vec4_transform (row vector times matrix)
float pvw_clamp(float v, float lo, float hi);                    // wined3d_clamp: NaN passes through
// ---- lights (pvw_light.cpp)
enum { PVW_MAX_LIGHTS = 32 };                                    // WINED3D_MAX_SOFTWARE_ACTIVE_LIGHTS
struct PvLight { PvCol diffuse, specular, ambient; PvVec4 position; PvVec3 direction; float range, falloff, c_att, l_att, q_att, cos_htheta, cos_hphi; };
struct PvLights {
    PvLight l[PVW_MAX_LIGHTS]; PvCol ambient_light; PvMat modelview; float nm[3][3]; PvVec4 pos_t; float fog_start, fog_end, fog_density;
    uint8_t n_point, n_spot, n_dir, n_par, lighting, legacy, normalise, localviewer, fog_coord_mode, fog_mode;   // fog_mode / fog_coord_mode are 2 bit fields in Wine: stored & 3
};
void pvw_lights_init(PvLights* ls, const NxDeviceCore* d, bool compute_lighting, bool log_fixme);
void pvw_light_vertex(PvLights* ls, const PvVec4* position);
void pvw_compute_light(PvCol* ambient, PvCol* diffuse, PvCol* specular, PvLights* ls, const PvVec3* normal, float shininess);
float pvw_fog_factor(const PvLights* ls, float current_a);       // update_fog_factor: the new alpha of the specular colour (current_a when fog is off)
// ---- the processing (pvw_core.cpp)
enum { PVW_FFP_POSITION = 0, PVW_FFP_NORMAL = 3, PVW_FFP_DIFFUSE = 5, PVW_FFP_SPECULAR = 6, PVW_FFP_TEXCOORD0 = 7, PVW_FFP_COUNT = 15 };   // enum wined3d_ffp_idx
struct PvElem { const NxBufCore* buf; uint64_t base; uint32_t stride, type; };   // base: byte offset of vertex 0 (src_start_idx applied) in buf->mem
struct PvInfo { uint32_t use_map; PvElem e[PVW_FFP_COUNT]; };
void pvw_stream_info(PvInfo* si, const NxDeviceCore* d, uint32_t start);   // wined3d_stream_info_from_declaration with the vertex shader hidden (fixed-function inputs only)
enum { PVW_LIGHT_PARALLELPOINT = 4 };                            // WINED3D_LIGHT_PARALLELPOINT (GLSPOT = 5)
HRESULT pvw_process(NxDeviceCore* d, UINT start, UINT dst_idx, UINT count, NxBufCore* dst);   // wined3d_device_process_vertices (after its FIXME for the declaration argument)
#include "const_fixme.h"   // fnvnx_wine_fixme and the NXW_ ids of verify/wine_fixme.tsv
