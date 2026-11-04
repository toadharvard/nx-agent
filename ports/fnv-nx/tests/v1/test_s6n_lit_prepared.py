#!/usr/bin/env python3
"""test_s6n_lit_prepared.py - V1 (D14) against S6n d80096c: "lit permutations read the directional vector and
1/range^2 prepared per draw" (nx::Renderer::Render stores -normalize(direction) and 1 / range^2 in the slice for
lit[_spec]_d<D>p<P>; the dynamic lit / lit_spec loop keeps the D3D-style values). Claim under test: a static
permutation's image equals the dynamic shader's within the n1 selftest's tolerance (1/255 per channel).

The renderer (render/native/n1/renderer.cpp as it stands) draws one full-target lit quad on the GL reference backend
twice per case, permutations on and off (Renderer::SetPermutations), and the two read-backs are compared:
  L1  one point light, range 0;          L2  one point light, range +inf;      L3  range 1e30 (range^2 overflows)
  L4  range 1e-20 (range^2 underflows);  L5  negative range -40
  L6  directional light, direction of length 100;   L7  directional, length 1e-3
  L8  d0p8: eight point lights;          L9  d1p8: one directional + seven points (the 7-point permutation)
  L10 d1p2 reordered (point, directional, point) as the n1 selftest, plus lightCount 0 (d0p0 -> dynamic either way)
  L11 a point light on a pixel centre with range 0 (0 * inf in the prepared path, 0 / 0 in the dynamic one)
Each case runs with lit and lit_spec. One line per case; exit 1 when any case exceeds 1/255, no permutation was
used, or (except ranges 0 and 1e-20) the image equals the unlit one (sum= is the image's byte sum).  python3 -I tests/v1/test_s6n_lit_prepared.py --out <make -C tests/s6n OUT=<dir> n1 output>
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
NATIVE = os.path.normpath(os.path.join(HERE, '..', '..', 'render', 'native'))

DRIVER = r'''
#include "renderer.h"
#include "../gl/nxgl.h"
#include "nx/families.h"
#include "nx_shaders.h"
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
using namespace nx;
enum { W = 128, H = 96 };
static uint16_t shader(Backend* be, const char* name, uint32_t stage) {
    int i = nx_shader_find(name, stage);
    if (i < 0) return 0;
    NxShaderDesc d; memset(&d, 0, sizeof d);
    d.stage = stage; d.dksh = FNVNX_ADDR_(nx_shader_blobs[i].dksh); d.dksh_size = nx_shader_blobs[i].dksh_size;
    d.glsl = FNVNX_ADDR_(nx_shader_blobs[i].glsl); d.glsl_size = nx_shader_blobs[i].glsl_size;
    return (uint16_t)be->ShaderCreate(d);
}
int main(int argc, char** argv) {
    (void)argc; (void)argv;
    char err[256];
    const NxGpuApi* api = nxgl_create(0, 0, err, sizeof err);
    if (!api) { fprintf(stderr, "nxgl: %s\n", err); return 2; }
    Backend* be = GpuBackend(api);
    static Renderer r;
    NxImageDesc rt; memset(&rt, 0, sizeof rt);
    rt.width = W; rt.height = H; rt.format = NxFmt_RGBA8; rt.flags = NxImg_RenderTarget;
    uint32_t back = be->ImageCreate(rt);
    NxImageDesc ds = rt; ds.format = NxFmt_Z24S8; ds.flags = NxImg_RenderTarget | NxImg_Depth;
    uint32_t depth = be->ImageCreate(ds);
    if (!r.Init(be, W, H, back, depth)) return 2;
    static const uint32_t white = 0xFFFFFFFFu;
    NxImageDesc td; memset(&td, 0, sizeof td);
    td.width = 1; td.height = 1; td.format = NxFmt_BGRA8; td.data = FNVNX_ADDR_(&white); td.data_size = 4;
    uint32_t tex = be->ImageCreate(td);
    NxD3D9Texture kind = {21, 0, 0, 0, 0};
    struct LV { float x, y, z, u, v; uint32_t rgba; float nx, ny, nz; };
    NxVtxLayout ll; memset(&ll, 0, sizeof ll);
    ll.count = 4; ll.stride = 36;
    ll.attr[0].offset = 0; ll.attr[0].type = NxAttr_Float32; ll.attr[0].components = 3;
    ll.attr[1].offset = 12; ll.attr[1].type = NxAttr_Float32; ll.attr[1].components = 2;
    ll.attr[2].offset = 20; ll.attr[2].type = NxAttr_Unorm8; ll.attr[2].components = 4;
    ll.attr[3].offset = 24; ll.attr[3].type = NxAttr_Float32; ll.attr[3].components = 3;
    uint16_t lay = r.cache().Layout(ll), lvs = shader(be, "lit", 0);
    uint16_t lps[2] = {shader(be, "lit", 1), shader(be, "lit_spec", 1)};
    static StaticBuffer vb;
    if (!vb.Init(be, 64 * 1024)) return 2;
    uint32_t off;
    LV* lv = (LV*)vb.Alloc(4 * sizeof(LV), 16, &off);
    const float q[4][2] = {{0, 0}, {W, 0}, {0, H}, {W, H}};
    for (int i = 0; i < 4; i++) lv[i] = LV{q[i][0], q[i][1], 0, 0, 0, 0xFFFFFFFFu, 0, 0, -1};
    static uint8_t imgs[2][W * H * 4];
    char line[1024];
    // input line: name spec count then count x (px py pz range r g b dir)
    while (fgets(line, sizeof line, stdin)) {
        char name[32]; int spec = 0, n = 0, used = 0;
        if (sscanf(line, "%31s %d %d%n", name, &spec, &n, &used) < 3) continue;
        static NxLitDraw u;
        memset(&u, 0, sizeof u);
        u.wvp[0] = 2.0f / W; u.wvp[5] = -2.0f / H; u.wvp[10] = 0.5f; u.wvp[12] = -1; u.wvp[13] = 1; u.wvp[14] = 0.5f; u.wvp[15] = 1;
        u.world[0] = u.world[5] = u.world[10] = u.world[15] = 1;
        u.ambient[0] = u.ambient[1] = u.ambient[2] = 0.1f;
        u.diffuse[0] = u.diffuse[1] = u.diffuse[2] = 0.8f; u.diffuse[3] = 1;
        u.specular[0] = u.specular[1] = u.specular[2] = 0.5f; u.specular[3] = 8;
        u.eye[0] = 64; u.eye[1] = 48; u.eye[2] = -100;
        u.lightCount = (uint32_t)n;
        const char* p = line + used;
        for (int i = 0; i < n && i < 8; i++) {
            int k = 0;
            float* pr = u.lights[i].posRange; float* c = u.lights[i].color;
            sscanf(p, "%f %f %f %f %f %f %f %f%n", &pr[0], &pr[1], &pr[2], &pr[3], &c[0], &c[1], &c[2], &c[3], &k);
            p += k;
        }
        uint16_t ps[2];
        for (int pass = 0; pass < 2; pass++) {
            r.SetPermutations(pass == 0);
            r.state().Reset();
            r.state().SetTexture(0, tex, kind);
            r.Do_BeginFrame(); r.Do_BeginUsingRenderTargetGroup(nullptr, Renderer::CLEAR_ALL);
            Renderer::Draw d; memset(&d, 0, sizeof d);
            d.vs = lvs; d.ps = lps[spec & 1]; d.layout = lay; d.prim = NxPrim_TriStrip; d.vtxBlock = vb.block(); d.vtxOffset = off;
            d.vtxSize = 4 * sizeof(LV); d.count = 4; d.textures = 1; d.ubo = &u; d.uboSize = sizeof u;
            if (!r.Render(d)) { printf("%s render failed\n", name); return 1; }
            ps[pass] = r.lastPs();
            r.Do_EndUsingRenderTargetGroup(); r.Do_EndFrame();
            NxReadback rb; memset(&rb, 0, sizeof rb);
            rb.image = back; rb.w = W; rb.h = H; rb.dst = FNVNX_ADDR_(imgs[pass]); rb.dst_size = sizeof imgs[pass];
            if (!be->Readback(rb)) { printf("%s readback failed\n", name); return 1; }
        }
        int worst = 0, at = -1, over = 0;
        unsigned long sum = 0;
        for (int i = 0; i < W * H * 4; i++) sum += imgs[0][i];
        for (int i = 0; i < W * H * 4; i++) {
            int dd = abs((int)imgs[0][i] - (int)imgs[1][i]);
            if (dd > 1) over++;
            if (dd > worst) { worst = dd; at = i; }
        }
        printf("%s spec=%d static_ps=%u dynamic_ps=%u worst=%d over1=%d sum=%lu", name, spec, ps[0], ps[1], worst, over, sum);
        if (at >= 0) printf(" at=(%d,%d,c%d) static=%d dynamic=%d", (at / 4) % W, (at / 4) / W, at % 4, imgs[0][at], imgs[1][at]);
        printf("\n");
        fflush(stdout);
    }
    return 0;
}
'''

INF = 'inf'
P = lambda x, y, z, rng, r=1.0, g=0.6, b=0.3: '%s %s %s %s %s %s %s 0' % (x, y, z, rng, r, g, b)
DL = lambda x, y, z, r=0.3, g=0.5, b=0.9: '%s %s %s 0 %s %s %s 1' % (x, y, z, r, g, b)
CASES = [
    ('L1', 'range 0 point light', [P(40, 30, -20, 0)], True),
    ('L2', 'range +inf point light', [P(40, 30, -20, INF)], True),
    ('L3', 'range 1e30 (range^2 = inf in fp32)', [P(40, 30, -20, '1e30')], True),
    ('L4', 'range 1e-20 (range^2 = 0 in fp32)', [P(40, 30, -20, '1e-20')], True),
    ('L5', 'negative range -40', [P(40, 30, -20, -40)], True),
    ('L6', 'directional of length 100', [DL(0, 60, 80)], True),
    ('L7', 'directional of length 1e-3', [DL('0.0006', 0, '0.0008')], True),
    ('L8', 'd0p8: eight points', [P(10 * i + 5, 7 * i + 3, -10 - i, 30 + 5 * i, 0.1 * i, 0.5, 0.2) for i in range(8)], True),
    ('L9', 'd1p8: directional + seven points', [DL(0, 0, 1)] + [P(12 * i + 5, 9 * i + 3, -15, 40 + 3 * i) for i in range(7)], True),
    ('L10', 'd1p2 reordered (selftest lights)', [P(30, 40, -20, 80, 1, 0.2, 0.1), DL(0, 0, 1, 0, 0.25, 0.5), P(100, 60, -30, 70, 0.2, 0.9, 0.3)], True),
    ('L10b', 'lightCount 0', [], False),
    ('L11', 'point on a pixel centre (z = 0), range 0', [P('20.5', '20.5', 0, 0)], True),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    o = ap.parse_args()
    out = os.path.abspath(o.out)
    work = tempfile.mkdtemp(prefix='v1-lit-')   # raises when it cannot
    try:
        drv = os.path.join(work, 'driver.cpp')
        open(drv, 'w').write(DRIVER)
        exe = os.path.join(work, 'drv')
        r = subprocess.run(['g++', '-std=c++17', '-O1', '-fno-exceptions', '-fno-rtti', '-w',
                            '-I' + os.path.join(NATIVE, 'include'), '-I' + os.path.join(NATIVE, 'front'),
                            '-I' + os.path.join(out, 'shaders'), '-I' + os.path.join(NATIVE, 'n1'), '-o', exe, drv,
                            os.path.join(NATIVE, 'n1', 'renderer.cpp'), os.path.join(NATIVE, 'front', 'front.cpp')] +
                           [os.path.join(out, x) for x in ('nxgl.o', 'gpu_dispatch.o', 'd3d9_state_s.o', 'nx_shaders.o')] +
                           ['-lEGL', '-lm'], capture_output=True, text=True)
        if r.returncode:
            print('build failed\n' + r.stderr[-3000:])
            return 1
        inp = ''
        for cid, what, lights, perm in CASES:
            for spec in (0, 1):
                inp += '%s %d %d %s\n' % (cid, spec, len(lights), ' '.join(lights))
        p = subprocess.run([exe], input=inp, capture_output=True, text=True, timeout=300)
        rows = {}
        for ln in p.stdout.splitlines():
            f = ln.split()
            rows[(f[0], f[1])] = ln
        fails = 0
        for cid, what, lights, perm in CASES:
            for spec in (0, 1):
                ln = rows.get((cid, 'spec=%d' % spec))
                if not ln:
                    print('%s spec=%d: DEFECT - no result (%s)' % (cid, spec, p.stderr[-300:]))
                    fails += 1
                    continue
                kv = dict(x.split('=', 1) for x in ln.split()[1:] if '=' in x)
                worst = int(kv['worst'])
                static_used = kv['static_ps'] != kv['dynamic_ps']
                dark = rows.get(('L10b', 'spec=%d' % spec), '')
                lit = 'sum=' in dark and kv['sum'] != dict(x.split('=', 1) for x in dark.split()[1:] if '=' in x)['sum']
                bad = worst > 1 or (perm and not static_used)
                note = ''
                if perm and cid not in ('L1', 'L4', 'L11') and not lit:   # range ~0: no point light contribution either way
                    bad = True
                    note = ' (the image equals the unlit one: the light is not seen)'
                fails += bad
                print('%s spec=%d: %s - %s%s: %s' % (cid, spec, 'DEFECT' if bad else 'ok', what, note, ' '.join(ln.split()[2:])))
        return 1 if fails else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
