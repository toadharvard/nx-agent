#!/usr/bin/env python3
"""test_s6n_render_reuse.py - V1 (D14) against the S6n render perf round's "skip work while unchanged" caches:
  89ee4a8  per-stage sampler cache (StateTracker::Handles translates only after a changed sampler state / kind)
  90fc3c2  Render reuses the previous draw's state id and texture block while unchanged
  831830a  Render reuses the epilogue block and dynamic record while the state is unchanged
  f7b342a  Frame::Finish skips the dynamic-state compares for a draw with the same record
  c94cc7d  epilogue permutation lookup by table (off: the list scans, re-learned per draw as before)
Claim under test: the encoded stream and the frame's constant ring are those of the uncached code.

render/native/n1/renderer.cpp and front/front.cpp are taken as they stand and compiled in variants: `ref` with
every cache switched off by a source edit (the cache condition made false, or the compare made unconditional),
`full` as committed, and one variant per cache with only that cache on. Each variant runs the same scripted
engine-shaped call sequences (driver below, GL reference backend) and prints, per frame, the md5 of the encoded
stream words and of the frame's used constant-ring bytes. A case is a DEFECT when any variant's frames differ from
`ref`'s. Cases (the attacks named for these claims):
  R1  texture image changed between two draws of a frame (same stage count)
  R2  the same state and texture over two frames (new frame: no block of the previous frame reused)
  R3  scissor rect / scissor test changed between draws;  R4  viewport (screen-space camera port) changed
  R5  d.vs / d.layout changed with the same pixel shader and state
  R6  StateTracker::Reset between two draws of a frame;  R7  stage-1 sampler state and SRGBTEXTURE toggled
  R8  render target group switch (depth format and target size) mid-frame
  R9  permutations toggled mid-frame; alpha test / fog toggled between draws (needEpi alternates)
  R10 texture kind changes (cube, managed LOD, sRGB class, same-class format) with the same image
  R11 a draw without textures between two textured draws; stage count 1 -> 2 -> 1
  F1..F40 random sequences (fixed seeds) over all of the above
  M1..M5 sensitivity: five known-wrong cache edits (a new image keeping the generation, reuse across frames, the
         layout ignored, Finish's skip keyed on a constant) must each differ from `ref` in some case
  python3 -I tests/v1/test_s6n_render_reuse.py --out <s6n OUT dir with front.o deps built: make -C tests/s6n n1>
Exit 1 when any case shows a defect or a variant cannot be built from the source as it stands.
"""
import argparse
import hashlib
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
NATIVE = os.path.normpath(os.path.join(HERE, '..', '..', 'render', 'native'))

# (file, name, committed text, cache-off text)
SWITCHES = [
    ('renderer.cpp', 'sampler', 'uint32_t id = stageSmp_[s];\n        if (!id) {',
     'uint32_t id = 0;\n        if (!id) {'),
    ('renderer.cpp', 'state', 'if (state_.resolves() == lastResolve_ && d.vs == lastVs_',
     'if (false && state_.resolves() == lastResolve_ && d.vs == lastVs_'),
    ('renderer.cpp', 'texblock', 'if (n && n == lastTexN_ && state_.textureGeneration()',
     'if (false && n && n == lastTexN_ && state_.textureGeneration()'),
    ('renderer.cpp', 'epidyn', 'const bool sameResolve = state_.resolves()',
     'const bool sameResolve = false && state_.resolves()'),
    ('renderer.cpp', 'epitable', 'if (ps < kEpiIds) {   // one table read',
     'if (false && ps < kEpiIds) {   // one table read'),
    ('front.cpp', 'finishdyn', 'if (dynId != curDyn) {', 'if (true || dynId != curDyn) {'),
]
# Sensitivity: known-wrong caches (all caches on plus one edit) must differ from `ref` in at least one case.
MUTANTS = [
    ('M1', 'renderer.cpp', 'if (image_[stage] != image) { image_[stage] = image; texGen_++; }',
     'if (image_[stage] != image) { image_[stage] = image; }', 'a new image keeps the texture generation'),
    ('M2', 'renderer.cpp', '&& frameNo_ == lastEpiFrame_', '', 'epilogue / dyn reuse across frames'),
    ('M3', 'renderer.cpp', '&& d.layout == lastLayout_', '', 'state id reuse ignores the layout'),
    ('M4', 'renderer.cpp', '&& frameNo_ == lastTexFrame_', '', 'texture block reuse across frames'),
    ('M5', 'front.cpp', 'const uint32_t dynId = p.dyn && p.dyn <= nDyn_ ? p.dyn : 0;', 'const uint32_t dynId = 1;',
     'Finish keys the skip on a constant instead of the record id'),
]

DRIVER = r'''
#include "renderer.h"
#include "../gl/nxgl.h"
#include "nx/families.h"
#include "nx_shaders.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <openssl/md5.h>
using namespace nx;
static uint16_t shader(Backend* be, const char* name, uint32_t stage) {
    int i = nx_shader_find(name, stage);
    if (i < 0) return 0;
    NxShaderDesc d; memset(&d, 0, sizeof d);
    d.stage = stage; d.dksh = FNVNX_ADDR_(nx_shader_blobs[i].dksh); d.dksh_size = nx_shader_blobs[i].dksh_size;
    d.glsl = FNVNX_ADDR_(nx_shader_blobs[i].glsl); d.glsl_size = nx_shader_blobs[i].glsl_size;
    return (uint16_t)be->ShaderCreate(d);
}
static void hex(const uint8_t* p, size_t n, char* o) { unsigned char h[16]; MD5(p, n, h); for (int i = 0; i < 16; i++) sprintf(o + 2 * i, "%02x", h[i]); }
enum { W = 64, H = 48 };
int main() {
    char err[256];
    const NxGpuApi* api = nxgl_create(0, 0, err, sizeof err);
    if (!api) { fprintf(stderr, "nxgl: %s\n", err); return 2; }
    Backend* be = GpuBackend(api);
    static Renderer r;
    NxImageDesc rt; memset(&rt, 0, sizeof rt);
    rt.width = W; rt.height = H; rt.format = NxFmt_RGBA8; rt.flags = NxImg_RenderTarget;
    uint32_t back = be->ImageCreate(rt);
    NxImageDesc rt2 = rt; rt2.width = 32; rt2.height = 32;
    uint32_t menu = be->ImageCreate(rt2);
    NxImageDesc ds = rt; ds.format = NxFmt_Z24S8; ds.flags = NxImg_RenderTarget | NxImg_Depth;
    uint32_t depth = be->ImageCreate(ds);
    if (!r.Init(be, W, H, back, depth)) { fprintf(stderr, "init\n"); return 2; }
    static const int key = 0;
    r.RegisterTargetGroup(&key, menu, 0, 32, 32);
    uint32_t img[4];
    for (int i = 0; i < 4; i++) {
        static uint32_t px[4][4];
        for (int k = 0; k < 4; k++) px[i][k] = 0xFF000000u | (uint32_t)(i * 0x404040 + k * 0x10);
        NxImageDesc td; memset(&td, 0, sizeof td);
        td.width = 2; td.height = 2; td.format = NxFmt_BGRA8; td.data = FNVNX_ADDR_(px[i]); td.data_size = 16;
        img[i] = be->ImageCreate(td);
    }
    static const NxD3D9Texture kinds[] = {{21, 0, 0, 0, 0}, {22, 0, 0, 0, 0}, {23, 0, 0, 0, 0}, {50, 0, 0, 0, 0},
                                          {21, 1, 0, 0, 0}, {21, 0, 0, 2, 0}, {0x31545844u, 0, 0, 0, 0}, {0, 0, 0, 0, 0}};
    NxVtxLayout tl; memset(&tl, 0, sizeof tl);
    tl.count = 3; tl.stride = 24;
    tl.attr[0].offset = 0; tl.attr[0].type = NxAttr_Float32; tl.attr[0].components = 3;
    tl.attr[1].offset = 12; tl.attr[1].type = NxAttr_Float32; tl.attr[1].components = 2;
    tl.attr[2].offset = 20; tl.attr[2].type = NxAttr_Unorm8; tl.attr[2].components = 4;
    NxVtxLayout ll = tl; ll.count = 4; ll.stride = 36;
    ll.attr[3].offset = 24; ll.attr[3].type = NxAttr_Float32; ll.attr[3].components = 3;
    uint16_t lays[2] = {r.cache().Layout(tl), r.cache().Layout(ll)};
    struct Set { uint16_t vs, ps; int lit; } sets[6];
    sets[0] = {shader(be, "tile", 0), r.FragmentShader("tile"), 0};
    sets[1] = {shader(be, "lit", 0), r.FragmentShader("lit_spec"), 1};
    sets[2] = {shader(be, "lit", 0), r.FragmentShader("lit"), 1};
    sets[3] = {shader(be, "tile", 0), shader(be, "tile", 1), 0};     // learned on first use (ShaderGlsl)
    sets[4] = {shader(be, "lit", 0), shader(be, "lit_spec", 1), 1};
    sets[5] = {shader(be, "nolight", 0), shader(be, "nolight", 1), 0};
    static StaticBuffer vb;
    if (!vb.Init(be, 64 * 1024)) return 2;
    uint32_t voff;
    float* v = (float*)vb.Alloc(4 * 36, 16, &voff);
    for (int i = 0; i < 4 * 9; i++) v[i] = (float)(i % 5) * 0.25f;
    const uint32_t frames = be->Info().frames_in_flight;
    char line[256];
    uint32_t frame = 0;
    bool inFrame = false;
    static uint8_t ubo[sizeof(NxLitDraw)];
    while (fgets(line, sizeof line, stdin)) {
        unsigned a = 0, b = 0, c = 0, e = 0;
        switch (line[0]) {
        case 'B': sscanf(line + 1, "%u", &a);
            r.Do_BeginFrame(); r.Do_BeginUsingRenderTargetGroup(a ? (const void*)&key : nullptr, Renderer::CLEAR_ALL); inFrame = true; break;
        case 'G': sscanf(line + 1, "%u", &a);
            r.Do_EndUsingRenderTargetGroup(); r.Do_BeginUsingRenderTargetGroup(a ? (const void*)&key : nullptr, 0); break;
        case 'E': {
            r.Do_EndUsingRenderTargetGroup(); r.Do_EndFrame(); inFrame = false;
            char h1[33], h2[33];
            hex((const uint8_t*)r.frame().encodedData(), r.frame().encodedWords() * 4, h1);
            const Ring& u = r.frame().ubo();
            hex(u.cpu() + (frame % frames) * u.region_size(), u.used(), h2);
            printf("frame %u words %u stream %s ring %u %s binds %u\n", frame, r.frame().encodedWords(), h1, u.used(), h2,
                   r.frame().stateBindsEncoded());
            frame++;
            break;
        }
        case 'R': sscanf(line + 1, "%u %u", &a, &b); r.state().SetRenderState(a, b); break;
        case 'S': sscanf(line + 1, "%u %u %u", &a, &b, &c); r.state().SetSamplerState(a, b, c); break;
        case 'T': sscanf(line + 1, "%u %u %u", &a, &b, &c); r.state().SetTexture(a, b ? img[(b - 1) & 3] : 0, kinds[c & 7]); break;
        case 'C': { sscanf(line + 1, "%u %u %u %u", &a, &b, &c, &e); NxD3D9Rect s = {(int32_t)a, (int32_t)b, (int32_t)c, (int32_t)e}; r.SetScissorRect(&s); break; }
        case 'c': r.SetScissorRect(nullptr); break;
        case 'V': { sscanf(line + 1, "%u %u %u %u", &a, &b, &c, &e); NiRectF p; p.left = a / 8.0f; p.right = b / 8.0f; p.top = c / 8.0f; p.bottom = e / 8.0f;
                    r.Do_SetScreenSpaceCameraData(&p); break; }
        case 'v': r.Do_SetScreenSpaceCameraData(nullptr); break;
        case 'X': r.state().Reset(); break;
        case 'P': sscanf(line + 1, "%u", &a); r.SetPermutations(a != 0); break;
        case 'D': {   // set layout textures seed
            unsigned seed = 0;
            sscanf(line + 1, "%u %u %u %u", &a, &b, &c, &seed);
            const Set& s = sets[a % 6];
            srand(seed);
            memset(ubo, 0, sizeof ubo);
            uint32_t size = sizeof(NxTileDraw);
            if (s.lit) {
                NxLitDraw* L = (NxLitDraw*)ubo; size = sizeof *L;
                for (int i = 0; i < 16; i++) L->wvp[i] = L->world[i] = (i % 5 == 0) ? 1.0f : 0.0f;
                L->lightCount = (uint32_t)(rand() % 9);
                for (uint32_t i = 0; i < 8; i++) {
                    for (int k = 0; k < 3; k++) { L->lights[i].posRange[k] = (float)(rand() % 200 - 100); L->lights[i].color[k] = (rand() % 100) / 100.0f; }
                    L->lights[i].posRange[3] = (float)(rand() % 100);
                    L->lights[i].color[3] = rand() % 4 == 0 ? 1.0f : 0.0f;
                }
            } else {
                float* f = (float*)ubo;
                for (uint32_t i = 0; i < size / 4; i++) f[i] = (float)(rand() % 64) / 16.0f;
            }
            Renderer::Draw d; memset(&d, 0, sizeof d);
            d.vs = s.vs; d.ps = s.ps; d.layout = lays[b & 1]; d.prim = NxPrim_TriStrip; d.layer = (uint8_t)(seed % 3);
            d.depthKey = (uint16_t)(seed % 7);
            d.vtxBlock = vb.block(); d.vtxOffset = voff; d.vtxSize = 4 * 36; d.count = 4; d.textures = c;
            d.ubo = ubo; d.uboSize = size;
            r.Render(d);
            break;
        }
        default: break;
        }
    }
    if (inFrame) { r.Do_EndUsingRenderTargetGroup(); r.Do_EndFrame(); }
    return 0;
}
'''

# D3DRS: 7 ZENABLE, 14 ZWRITE, 15 ALPHATESTENABLE, 19/20 blend, 22 CULL, 24 ALPHAREF, 25 ALPHAFUNC, 27 BLEND,
# 28 FOGENABLE, 35 FOGTABLEMODE, 34 FOGCOLOR, 52 STENCILENABLE, 57 STENCILREF, 174 SCISSORTEST, 193 BLENDFACTOR,
# 195 DEPTHBIAS, 176 SLOPESCALEDEPTHBIAS, 168 COLORWRITE
RS = [(7, [0, 1]), (14, [0, 1]), (15, [0, 1]), (24, [0, 128, 255]), (25, [1, 5, 6, 8]), (27, [0, 1]), (28, [0, 1]),
      (35, [0, 3]), (34, [0, 0xFF808080]), (52, [0, 1]), (57, [0, 3, 7]), (174, [0, 1]), (193, [0xFFFFFFFF, 0x80402010]),
      (195, [0, 0x3A000000]), (176, [0, 0x3F800000]), (22, [1, 2, 3]), (168, [15, 7])]
# D3DSAMP: 1-3 address, 5 MAG, 6 MIN, 7 MIP, 9 MAXMIPLEVEL, 11 SRGBTEXTURE
SAMP = [(1, [1, 3]), (2, [1, 3]), (5, [1, 2]), (6, [1, 2, 3]), (7, [0, 1, 2]), (9, [0, 3]), (11, [0, 1])]

D = lambda s, lay, tex, seed: 'D %d %d %d %d' % (s, lay, tex, seed)
BASE = ['T 0 1 0', 'T 1 2 0']

CASES = {
    'R1': BASE + ['B 0', D(0, 0, 1, 1), 'T 0 2 0', D(0, 0, 1, 1), 'T 0 3 0', D(0, 0, 1, 1), 'T 0 1 0', D(0, 0, 1, 1), 'E'],
    'R2': BASE + ['B 0', D(0, 0, 2, 1), D(0, 0, 2, 2), 'E', 'B 0', D(0, 0, 2, 1), D(0, 0, 2, 2), 'E',
                  'B 0', 'T 0 3 0', D(0, 0, 2, 1), 'E', 'B 0', D(0, 0, 2, 3), 'E'],
    'R3': BASE + ['R 174 1', 'B 0', 'C 0 0 10 10', D(0, 0, 1, 1), 'C 5 5 20 20', D(0, 0, 1, 1), 'c', D(0, 0, 1, 1),
                  'R 174 0', D(0, 0, 1, 1), 'C 1 1 2 2', D(0, 0, 1, 1), 'R 174 1', D(0, 0, 1, 1), 'E'],
    'R4': BASE + ['B 0', 'V 0 8 8 0', D(0, 0, 1, 1), 'V 0 4 8 4', D(0, 0, 1, 1), 'v', D(0, 0, 1, 1), 'V 2 6 6 2',
                  D(1, 1, 1, 5), D(1, 1, 1, 5), 'E'],
    'R5': BASE + ['B 0', D(0, 0, 1, 1), D(0, 1, 1, 1), D(5, 0, 1, 1), D(5, 1, 1, 1), D(3, 0, 1, 1), D(0, 0, 1, 1), 'E'],
    'R6': BASE + ['R 15 1', 'R 24 128', 'B 0', D(0, 0, 2, 1), 'X', D(0, 0, 2, 1), 'T 0 1 0', D(0, 0, 2, 1), 'X',
                  D(0, 0, 2, 1), 'E'],
    'R7': BASE + ['B 0', D(0, 0, 2, 1), 'S 1 5 1', D(0, 0, 2, 1), 'S 1 11 1', D(0, 0, 2, 1), 'S 1 11 0',
                  D(0, 0, 2, 1), 'S 0 11 1', D(0, 0, 1, 1), 'S 0 11 1', D(0, 0, 1, 1), 'E'],
    'R8': BASE + ['B 0', D(0, 0, 1, 1), 'G 1', D(0, 0, 1, 1), D(0, 0, 1, 2), 'G 0', D(0, 0, 1, 1), 'E'],
    'R9': BASE + ['B 0', D(1, 1, 1, 4), 'P 0', D(1, 1, 1, 4), 'P 1', D(1, 1, 1, 4), 'R 15 1', D(0, 0, 1, 1),
                  D(5, 0, 1, 1), D(0, 0, 1, 1), 'R 28 1', 'R 35 3', D(0, 0, 1, 1), D(5, 0, 1, 1), D(0, 0, 1, 1),
                  'R 15 0', 'R 28 0', D(0, 0, 1, 1), D(5, 0, 1, 1), D(4, 1, 1, 9), D(1, 1, 1, 9), 'E'],
    'R10': BASE + ['S 0 11 1', 'B 0'] + sum([['T 0 1 %d' % k, D(0, 0, 1, 1)] for k in [0, 4, 0, 5, 0, 3, 1, 2, 6, 7, 0]], []) + ['E'],
    'R11': BASE + ['B 0', D(0, 0, 1, 1), D(0, 0, 0, 1), D(0, 0, 1, 1), D(0, 0, 2, 1), D(0, 0, 1, 1), 'T 1 3 0',
                   D(0, 0, 1, 1), D(0, 0, 2, 1), 'E'],
}


def random_case(seed):
    rng = random.Random(seed)
    ops = []
    for f in range(rng.randint(1, 4)):
        ops.append('B %d' % (rng.random() < 0.2))
        for _ in range(rng.randint(5, 60)):
            k = rng.random()
            if k < 0.45:
                ops.append(D(rng.randrange(6), rng.randrange(2), rng.choice([0, 1, 1, 1, 2, 2, 3]), rng.randrange(4)))
            elif k < 0.60:
                s, vals = rng.choice(RS); ops.append('R %d %d' % (s, rng.choice(vals)))
            elif k < 0.70:
                t, vals = rng.choice(SAMP); ops.append('S %d %d %d' % (rng.randrange(3), t, rng.choice(vals)))
            elif k < 0.80:
                ops.append('T %d %d %d' % (rng.randrange(3), rng.randrange(5), rng.randrange(8)))
            elif k < 0.85:
                ops.append(rng.choice(['C 0 0 %d %d' % (rng.randint(1, 64), rng.randint(1, 48)), 'c']))
            elif k < 0.90:
                ops.append(rng.choice(['V %d 8 8 %d' % (rng.randrange(4), rng.randrange(4)), 'v']))
            elif k < 0.93:
                ops.append('G %d' % rng.randrange(2))
            elif k < 0.96:
                ops.append('P %d' % rng.randrange(2))
            else:
                ops.append('X')
        ops.append('E')
    return ops


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True, help='make -C tests/s6n OUT=<dir> n1 output (front.o deps, shaders)')
    ap.add_argument('--random', type=int, default=40)
    o = ap.parse_args()
    out = os.path.abspath(o.out)
    srcs = {f: open(os.path.join(NATIVE, 'n1' if f == 'renderer.cpp' else 'front', f)).read()
            for f in ('renderer.cpp', 'front.cpp')}
    for f, name, on, off in SWITCHES:
        if srcs[f].count(on) != 1:
            print('%s: the %s cache condition is not found once in %s' % (name, name, f))
            return 1
    variants = {'ref': set(), 'full': {s[1] for s in SWITCHES}}
    for s in SWITCHES:
        variants['only_' + s[1]] = {s[1]}
    edits = {}
    for mid, f, a, b, what in MUTANTS:
        if srcs[f].count(a) != 1:
            print('%s: mutant text not found once in %s' % (mid, f))
            return 1
        variants[mid] = {s[1] for s in SWITCHES}
        edits[mid] = (f, a, b)
    work = tempfile.mkdtemp(prefix='v1-reuse-')   # raises when it cannot
    try:
        drv = os.path.join(work, 'driver.cpp')
        open(drv, 'w').write(DRIVER)
        cxx = ['g++', '-std=c++17', '-O1', '-fno-exceptions', '-fno-rtti', '-w',
               '-I' + os.path.join(NATIVE, 'include'), '-I' + os.path.join(NATIVE, 'front'),
               '-I' + os.path.join(out, 'shaders'), '-I' + os.path.join(NATIVE, 'n1')]
        bins = {}
        for v, on in variants.items():
            vd = os.path.join(work, v)
            os.makedirs(vd)
            paths = []
            for f in ('renderer.cpp', 'front.cpp'):
                t = srcs[f]
                for sf, name, a, b in SWITCHES:
                    if sf == f and name not in on:
                        t = t.replace(a, b)
                if v in edits and edits[v][0] == f:
                    t = t.replace(edits[v][1], edits[v][2])
                sub = os.path.join(vd, 'n1' if f == 'renderer.cpp' else 'front')
                os.makedirs(sub, exist_ok=True)
                p = os.path.join(sub, f)
                open(p, 'w').write(t)
                paths.append(p)
            # renderer.cpp includes "renderer.h" (n1/ on -I); front.cpp includes nx/front.h and d3d9_state.h
            exe = os.path.join(vd, 'drv')
            r = subprocess.run(cxx + ['-o', exe, drv] + paths +
                               [os.path.join(out, x) for x in ('nxgl.o', 'gpu_dispatch.o', 'd3d9_state_s.o', 'nx_shaders.o')] +
                               ['-lEGL', '-lm', '-lcrypto'], capture_output=True, text=True)
            if r.returncode:
                print('%s: build failed\n%s' % (v, r.stderr[-3000:]))
                return 1
            bins[v] = exe
        cases = dict(CASES)
        for i in range(o.random):
            cases['F%d' % (i + 1)] = random_case(1000 + i)
        fails = 0
        caught = {m[0]: [] for m in MUTANTS}
        for cid, ops in cases.items():
            inp = '\n'.join(ops) + '\n'
            res = {}
            for v, exe in bins.items():
                p = subprocess.run([exe], input=inp, capture_output=True, text=True, timeout=120)
                if p.returncode:
                    res[v] = 'exit %d: %s' % (p.returncode, p.stderr[-300:])
                else:
                    res[v] = p.stdout
            for m in caught:
                if res[m] != res['ref']:
                    caught[m].append(cid)
            bad = [v for v in bins if v not in caught and res[v] != res['ref']]
            if not res['ref'].count('frame ') or ' words 0 ' in res['ref']:
                bad.append('ref (no frame encoded)')
            nframes = res['ref'].count('frame ')
            draws = sum(1 for x in ops if x.startswith('D'))
            if bad:
                fails += 1
                print('%s: DEFECT - %s differ from the uncached reference (%d frames, %d draws)' % (cid, ', '.join(bad), nframes, draws))
                for v in bad:
                    if v not in res:
                        continue
                    for a, b in zip(res['ref'].splitlines(), res[v].splitlines()):
                        if a != b:
                            print('    ref : %s\n    %-4s: %s' % (a, v[:4], b))
                            break
            else:
                print('%s: ok - %d frames, %d draws: every variant equals the uncached stream and ring' % (cid, nframes, draws))
        for mid, f, a, b, what in MUTANTS:
            ok = bool(caught[mid])
            fails += not ok
            print('%s: %s - known-wrong cache (%s) %s' % (mid, 'ok' if ok else 'INSENSITIVE', what,
                  'differs from ref in ' + ' '.join(caught[mid][:6]) if ok else 'is not detected by any case'))
        return 1 if fails else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
