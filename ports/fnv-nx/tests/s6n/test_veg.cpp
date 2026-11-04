// render-veg CPU side: NxGrassDraw folding and the permutation names (render/native/n1/veg_grass.cpp).
#include "veg_families.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

static int checks, failed;
#define CHECK(c) do { checks++; if (!(c)) { failed++; printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); } } while (0)
static bool near(float a, float b) { return fabsf(a - b) <= 1e-6f * (1.0f + fabsf(b)); }

int main() {
    // std140 offsets of the GLSL block (grass.vert.glsl GrassDraw): mat4 at 0, 9 vec4 heads, inst at 208
    CHECK(offsetof(NxGrassDraw, lightDir) == 64);
    CHECK(offsetof(NxGrassDraw, fogColor) == 176);
    CHECK(offsetof(NxGrassDraw, alpha) == 192);
    CHECK(offsetof(NxGrassDraw, inst) == 208);

    NxGrassConstants c;
    memset(&c, 0, sizeof c);
    for (int i = 0; i < 16; i++) c.mvpRows[i] = (float)(i + 1);
    float dd[4] = {0.1f, 0.2f, 0.9f, 0}, dc[4] = {1, 0.5f, 0.25f, 1}, sm[4] = {0.5f, 0.5f, 1, 0};
    float wd[4] = {0.6f, 0.8f, 10, 3}, ap[4] = {0, 0, 1000, 400}, am[4] = {0.3f, 0.3f, 0.4f, 1}, ad[4] = {0.5f, 0, 0, 0};
    float fc[4] = {1.5f, 0.5f, -0.25f, 1}, fp[4] = {2000, 1500, 2, 0};
    memcpy(c.diffuseDir, dd, 16); memcpy(c.diffuseColor, dc, 16); memcpy(c.scaleMask, sm, 16); memcpy(c.windData, wd, 16);
    memcpy(c.alphaParam, ap, 16); memcpy(c.ambientColor, am, 16); memcpy(c.addlParams, ad, 16);
    memcpy(c.fogColor, fc, 16); memcpy(c.fogParam, fp, 16); c.alphaTestRef = 0.5f;
    float inst[3][4] = {{1, 2, 3, 10.5f}, {4, 5, 6, -3.25f}, {7, 8, 9, 0.75f}};
    static NxGrassDraw d;
    size_t n = nx_grass_fold(&c, inst, 3, &d);
    CHECK(n == 208 + 3 * 16);
    CHECK(d.mvp[0] == 1 && d.mvp[1] == 5 && d.mvp[4] == 2 && d.mvp[15] == 16);    // column-major from rows
    CHECK(near(d.diffuse[0], 0.5f) && near(d.diffuse[1], 0.25f) && near(d.diffuse[2], 0.125f));
    CHECK(near(d.scaleMask[0], 0.005f) && near(d.scaleMask[2], 0.01f));
    CHECK(near(d.wind[0], 6) && near(d.wind[1], 8) && d.wind[2] == 3);
    CHECK(d.fadeFog[0] == 1000 && near(d.fadeFog[1], 1.0f / 400) && d.fadeFog[2] == 2000 && near(d.fadeFog[3], 1.0f / 1500));
    CHECK(d.fogPow[0] == 2);
    CHECK(d.fogColor[0] == 1 && d.fogColor[1] == 0.5f && d.fogColor[2] == 0);         // saturated
    CHECK(d.alpha[0] == 0.5f);
    CHECK(d.inst[1][3] == -3.25f && d.inst[2][0] == 7);
    CHECK(nx_grass_fold(&c, inst, NX_GRASS_MAX_INSTANCES + 1, &d) == 0);
    c.alphaParam[3] = 0;                                                              // dk_rcp(0): huge, finite
    nx_grass_fold(&c, inst, 0, &d);
    CHECK(isfinite(d.fadeFog[1]) && d.fadeFog[1] > 1e38f);

    CHECK(!strcmp(nx_grass_vs(0), "grass") && !strcmp(nx_grass_vs(NxGrass_VertLit), "grass_vl"));
    CHECK(!strcmp(nx_grass_vs(NxGrass_FitToSlope), "grass_slope"));
    CHECK(!strcmp(nx_grass_vs(NxGrass_VertLit | NxGrass_FitToSlope), "grass_vlslope"));
    CHECK(!nx_grass_vs(NxGrass_PtLight) && !nx_grass_vs(NxGrass_Billboard) && !nx_grass_vs(NxGrass_ShadowMap));
    CHECK(!strcmp(nx_grass_ps(0), "grass") && !strcmp(nx_grass_ps(NxGrass_TransparencyMS), "grass_tms"));
    CHECK(!nx_grass_ps(NxGrass_PtLight) && !nx_grass_ps(NxGrass_ProjShadow));
    // particles: fVars0.y folded to its reciprocal, the rest copied in register order, 3 vec4 per particle
    NxParticleConstants pc;
    memset(&pc, 0, sizeof pc);
    for (int i = 0; i < 16; i++) pc.wvpRows[i] = (float)(i + 1);
    pc.vars0[0] = 50; pc.vars0[1] = 4; pc.vars1[2] = 0.8f; pc.wind[3] = 12;
    float pinst[6][4] = {{1, 2, 3, 4}, {5, 6, 7, 8}, {9, 10, 11, 12}, {13, 14, 15, 16}, {17, 18, 19, 20}, {21, 22, 23, 24}};
    static NxParticleDraw pd;
    CHECK(nx_particle_fold(&pc, pinst, 2, &pd) == 256 + 6 * 16);
    CHECK(pd.wvp[1] == 5 && pd.vars0[0] == 50 && pd.vars0[1] == 0.25f && pd.vars1[2] == 0.8f && pd.wind[3] == 12);
    CHECK(pd.inst[5][3] == 24 && pd.inst[3][0] == 13);
    CHECK(nx_particle_fold(&pc, pinst, NX_PARTICLE_MAX + 1, &pd) == 0);
    CHECK(offsetof(NxParticleDraw, wind) == 240 && offsetof(NxParticleDraw, inst) == 256);

    // tree leaves: DiffColor x SunDimmer.x, 1 / range for fog and the point light
    static NxTreeLeafConstants lc;
    static NxTreeLeafDraw ld;
    lc.diffColor[0] = 0.5f; lc.sunDimmer[0] = 0.5f; lc.fogParam[1] = 4; lc.lightPos[3] = 8; lc.leafBase[47][2] = 3;
    lc.windMatrices[15][3] = 7;
    CHECK(nx_tree_leaf_fold(&lc, &ld) == sizeof ld);
    CHECK(ld.diffuse[0] == 0.25f && ld.fog[1] == 0.25f && ld.lightPos[3] == 0.125f && ld.leafBase[47][2] == 3);
    CHECK(ld.wind[15][3] == 7 && offsetof(NxTreeLeafDraw, wind) == 256 && offsetof(NxTreeLeafDraw, leafBase) == 512);
    CHECK(!strcmp(nx_tree_leaf_vs(NxTree_Fog | NxTree_Pt), "tree_leaf_ptfog") && !strcmp(nx_tree_leaf_ps(NxTree_TransparencyMS), "tree_leaf_tms"));
    CHECK(!strcmp(nx_tree_leaf_ps(NxTree_Fog | NxTree_TransparencyMS), "tree_leaf_fogtms") && !strcmp(nx_tree_leaf_vs(0), "tree_leaf"));
    printf("veg: %d checks, %d failed\n", checks, failed);
    return failed != 0;
}
