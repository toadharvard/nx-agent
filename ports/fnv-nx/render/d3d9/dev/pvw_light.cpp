// pvw_light.cpp: the software lighting and fog of Wine 9.0 process_vertices_strided (device.c init_transformed_lights, light_set_vertex_data, compute_light, update_light_diffuse_specular, update_fog_factor,
// wined3d_calculate_fog_factor). Re-implemented with Wine's binary32 operation order (no fused operations); transcendentals (sqrtf, powf, expf, cosf) are the platform libm's, which is correctly rounded by the owner's FP rule.
// Facts taken from the d3d9 build of wined3d: legacy_lighting is false (d3d9_init passes no WINED3D_LEGACY_FFP_LIGHTING); lights are walked in ascending index order (Wine: red-black tree on the index), only ENABLED lights count (`enabled` is the
// flag LightEnable set, also for lights beyond the hardware slot count), at most 32 are used, in the order directional, point, spot. PARALLELPOINT (type 4) is walked as Wine does; any other type (GLSPOT) gets Wine's FIXME (id NXW_init_transformed_lights) and is skipped, as in init_transformed_lights.
// The derived per-light vectors of wined3d_device_context_set_light (position, direction w = 0, directional direction NEGATED) are recomputed from the D3DLIGHT9 at each call: every light type sets exactly the vectors its software path reads, so Wine's stale
// fields of a light that changed type are never read.
#pragma GCC optimize("fp-contract=off")
#include "pvw.h"
#include <math.h>

static PvCol colour_from_d3dcolor(uint32_t c) { PvCol o; o.r = ((c >> 16) & 0xff) / 255.0f; o.g = ((c >> 8) & 0xff) / 255.0f; o.b = (c & 0xff) / 255.0f; o.a = ((c >> 24) & 0xff) / 255.0f; return o; }
static PvCol colour_of(const D3DCOLORVALUE& v) { PvCol o = {v.r, v.g, v.b, v.a}; return o; }
static float float_state(const NxDeviceCore* d, uint32_t rs) { float f; memcpy(&f, &d->cur.rs[rs], 4); return f; }
static void rgb_mul_add(PvCol* dst, const PvCol* src, float c) { dst->r += src->r * c; dst->g += src->g * c; dst->b += src->b * c; }

void pvw_lights_init(PvLights* ls, const NxDeviceCore* d, bool compute_lighting, bool log_fixme) {
    const NxDeviceState& s = d->cur; memset((void*)ls, 0, sizeof *ls);
    ls->lighting = compute_lighting;
    ls->fog_mode = (uint8_t)(s.rs[D3DRS_FOGVERTEXMODE] & 3);                              // 2 bit field
    ls->fog_coord_mode = s.rs[D3DRS_RANGEFOGENABLE] ? 3 : 2;                              // WINED3D_FFP_VS_FOG_RANGE : WINED3D_FFP_VS_FOG_DEPTH
    ls->fog_start = float_state(d, D3DRS_FOGSTART); ls->fog_end = float_state(d, D3DRS_FOGEND); ls->fog_density = float_state(d, D3DRS_FOGDENSITY);
    if (ls->fog_mode == D3DFOG_NONE && !compute_lighting) return;
    PvMat view, world; memcpy(&view, &s.xf[nx_dxf(D3DTS_VIEW)], sizeof view); memcpy(&world, &s.xf[nx_dxf(D3DTS_WORLD)], sizeof world);
    pvw_mul(&ls->modelview, &view, &world);
    if (!compute_lighting) return;
    PvMat mv = ls->modelview; pvw_invert(&mv, &mv);                                       // compute_normal_matrix: a singular modelview is used unchanged; normal matrix = transpose of the upper 3x3
    for (int i = 0; i < 3; i++) for (int j = 0; j < 3; j++) ls->nm[i][j] = mv.m[j][i];
    ls->ambient_light = colour_from_d3dcolor(s.rs[D3DRS_AMBIENT]); ls->legacy = 0;
    ls->normalise = s.rs[D3DRS_NORMALIZENORMALS] != 0; ls->localviewer = s.rs[D3DRS_LOCALVIEWER] != 0;
    const D3DLIGHT9* act[PVW_MAX_LIGHTS]; uint32_t n = 0;
    for (uint32_t i = 0; i < d->lights.n && n < PVW_MAX_LIGHTS; i++) {
        const NxLightEnt& e = d->lights.a[i]; if (!e.enabled) continue;
        if (e.light.Type == D3DLIGHT_DIRECTIONAL) ls->n_dir++; else if (e.light.Type == D3DLIGHT_POINT) ls->n_point++; else if (e.light.Type == D3DLIGHT_SPOT) ls->n_spot++; else if ((int)e.light.Type == PVW_LIGHT_PARALLELPOINT) ls->n_par++; else { if (log_fixme) fnvnx_wine_fixme(NXW_init_transformed_lights); continue; }   // Wine: FIXME("Unhandled light type")
        act[n++] = &e.light;
    }
    uint32_t idx = 0;
    for (int pass = 0; pass < 4; pass++) for (uint32_t i = 0; i < n; i++) {                // directional, then point, then spot
        const D3DLIGHT9& p = *act[i]; int want = pass == 0 ? (int)D3DLIGHT_DIRECTIONAL : pass == 1 ? (int)D3DLIGHT_POINT : pass == 2 ? (int)D3DLIGHT_SPOT : (int)PVW_LIGHT_PARALLELPOINT; if ((int)p.Type != want) continue;
        PvLight* L = &ls->l[idx++]; PvVec4 v4, pos = {p.Position.x, p.Position.y, p.Position.z, 1.0f};
        L->diffuse = colour_of(p.Diffuse); L->ambient = colour_of(p.Ambient); L->specular = colour_of(p.Specular);
        if (pass == 0) { PvVec4 dir = {-p.Direction.x, -p.Direction.y, -p.Direction.z, 0.0f}; pvw_xform4(&v4, &dir, &view); L->direction = {v4.x, v4.y, v4.z}; pvw_normalise(&L->direction); }
        else if (pass == 3) { pvw_xform4(&v4, &pos, &view); L->position.x = v4.x; L->position.y = v4.y; L->position.z = v4.z; pvw_normalise((PvVec3*)&L->position); }   // parallel point: only x, y, z are used
        else {
            pvw_xform4(&L->position, &pos, &view); L->range = p.Range; L->c_att = p.Attenuation0; L->l_att = p.Attenuation1; L->q_att = p.Attenuation2;
            if (pass == 2) {
                PvVec4 dir = {p.Direction.x, p.Direction.y, p.Direction.z, 0.0f}; pvw_xform4(&v4, &dir, &view); L->direction = {v4.x, v4.y, v4.z}; pvw_normalise(&L->direction);
                L->falloff = p.Falloff; L->cos_htheta = cosf(p.Theta / 2.0f); L->cos_hphi = cosf(p.Phi / 2.0f);
            }
        }
    }
}
void pvw_light_vertex(PvLights* ls, const PvVec4* position) {
    if (ls->fog_mode == D3DFOG_NONE && !ls->lighting) return;
    pvw_xform4(&ls->pos_t, position, &ls->modelview);
    float s = 1.0f / ls->pos_t.w; ls->pos_t.x *= s; ls->pos_t.y *= s; ls->pos_t.z *= s;
}
static void diffuse_specular(PvCol* diffuse, PvCol* specular, const PvVec3* dir, float att, float shininess, const PvVec3* n, const PvVec3* pn, const PvLight* L, const PvLights* ls) {
    float c = pvw_clamp(pvw_dot(dir, n), 0.0f, 1.0f); rgb_mul_add(diffuse, &L->diffuse, c * att);
    PvVec3 v = *dir;
    if (ls->localviewer) { v.x -= pn->x; v.y -= pn->y; v.z -= pn->z; } else v.z -= 1.0f;
    pvw_normalise(&v);
    float t = pvw_dot(n, &v);
    if (t > 0.0f && (!ls->legacy || shininess > 0.0f) && pvw_dot(dir, n) > 0.0f) rgb_mul_add(specular, &L->specular, att * powf(t, shininess));
}
void pvw_compute_light(PvCol* ambient, PvCol* diffuse, PvCol* specular, PvLights* ls, const PvVec3* normal, float shininess) {
    PvVec3 pn = {ls->pos_t.x, ls->pos_t.y, ls->pos_t.z}, nt = {0.0f, 0.0f, 0.0f}, dir, dst; pvw_normalise(&pn);
    if (normal) {
        PvVec3 v = *normal; nt.x = v.x * ls->nm[0][0] + v.y * ls->nm[1][0] + v.z * ls->nm[2][0]; nt.y = v.x * ls->nm[0][1] + v.y * ls->nm[1][1] + v.z * ls->nm[2][1]; nt.z = v.x * ls->nm[0][2] + v.y * ls->nm[1][2] + v.z * ls->nm[2][2];
        if (ls->normalise) pvw_normalise(&nt);
    }
    PvCol zero = {0.0f, 0.0f, 0.0f, 0.0f}; *diffuse = zero; *specular = zero; *ambient = ls->ambient_light;
    uint32_t index = 0; const PvLight* L; float att;
    for (uint32_t i = 0; i < ls->n_dir; i++, index++) {
        L = &ls->l[index]; rgb_mul_add(ambient, &L->ambient, 1.0f);
        if (normal) diffuse_specular(diffuse, specular, &L->direction, 1.0f, shininess, &nt, &pn, L, ls);
    }
    for (uint32_t i = 0; i < ls->n_point; i++, index++) {
        L = &ls->l[index]; dir.x = L->position.x - ls->pos_t.x; dir.y = L->position.y - ls->pos_t.y; dir.z = L->position.z - ls->pos_t.z;
        dst.z = pvw_dot(&dir, &dir); dst.y = sqrtf(dst.z); dst.x = 1.0f;
        if (!(dst.y <= L->range)) continue;                                                // non-legacy lighting
        att = dst.x * L->c_att + dst.y * L->l_att + dst.z * L->q_att; att = 1.0f / att;
        rgb_mul_add(ambient, &L->ambient, att);
        if (normal) { pvw_normalise(&dir); diffuse_specular(diffuse, specular, &dir, att, shininess, &nt, &pn, L, ls); }
    }
    for (uint32_t i = 0; i < ls->n_spot; i++, index++) {
        L = &ls->l[index]; dir.x = L->position.x - ls->pos_t.x; dir.y = L->position.y - ls->pos_t.y; dir.z = L->position.z - ls->pos_t.z;
        dst.z = pvw_dot(&dir, &dir); dst.y = sqrtf(dst.z); dst.x = 1.0f;
        if (!(dst.y <= L->range)) continue;
        pvw_normalise(&dir);
        float t = -pvw_dot(&dir, &L->direction);
        if (t > L->cos_htheta) att = 1.0f; else if (t <= L->cos_hphi) att = 0.0f; else att = powf((t - L->cos_hphi) / (L->cos_htheta - L->cos_hphi), L->falloff);
        t = dst.x * L->c_att + dst.y * L->l_att + dst.z * L->q_att; att /= t;
        rgb_mul_add(ambient, &L->ambient, att);
        if (normal) diffuse_specular(diffuse, specular, &dir, att, shininess, &nt, &pn, L, ls);
    }
    for (uint32_t i = 0; i < ls->n_par; i++, index++) {                                    // parallel point: no attenuation, direction = normalised view-space position
        L = &ls->l[index]; rgb_mul_add(ambient, &L->ambient, 1.0f);
        if (normal) diffuse_specular(diffuse, specular, (const PvVec3*)&L->position, 1.0f, shininess, &nt, &pn, L, ls);
    }
}
float pvw_fog_factor(const PvLights* ls, float cur) {
    if (ls->fog_mode == D3DFOG_NONE) return cur;
    PvVec3 p = {ls->pos_t.x, ls->pos_t.y, ls->pos_t.z}; float c = ls->fog_coord_mode == 3 ? sqrtf(pvw_dot(&p, &p)) : fabsf(ls->pos_t.z);   // range fog: distance, else |z|
    switch (ls->fog_mode) {
    case D3DFOG_LINEAR: return (ls->fog_end - c) / (ls->fog_end - ls->fog_start);
    case D3DFOG_EXP: return expf(-c * ls->fog_density);
    default: return expf(-c * c * ls->fog_density * ls->fog_density);                      // EXP2: ((-c * c) * d) * d
    }
}
