#!/usr/bin/env python3
"""test_d3d9_state_table.py - the D3D9 state class against the engine's defaults passes (S6n, D21 coverage).

The native path records D3D9 state in nx::StateTracker. The engine sets its defaults in two passes that stay the
engine's (NiDX9RenderState slot 22: render states, slot 47: texture stage and sampler states) and talk to the device
directly, so they reach the tracker only through the NativeDevice (n1/engine_stubs.h). This test drives the stubs, the
native device and the tracker with a defaults pass (NiDX9RenderStateLayout::DefaultsPass, the same walks) and checks,
for every state of each table, that
  - the tracker (the device's state) ends at the pass's value;
  - the engine cache answers like the engine: render and sampler states the value, texture stage states unknown
    (0x7FFFFFFF: slot 47 writes their saved field only), types the cache does not hold 0;
  - every type the pass names is one the cache holds (slot 47 indexes the cache without a check);
  - after a draw changes every sampler state, slot 53 restores the pass's value.
Without --exe the tables are synthetic. With --exe the owner's FalloutNV.exe is read in place at run time (nothing
of it is written or kept): NiDX9RenderState's vftable (found through RTTI) must hold the overrides.tsv addresses for
the state rows, the cache offsets and the bounds of the cached types in the replaced functions must be the layout's
(NiDX9RenderStateLayout), and the tables the exe's slot 22 / slot 47 walk are the input of the check above; the test
prints how many of the engine's defaults differ from D3D9's (a count, never the values).
  python3 -I tests/s6n/test_d3d9_state_table.py --out <make -C tests/s6n OUT=<dir> n1 output> [--exe FalloutNV.exe]
Exit 1 on any failure.
"""
import argparse
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))
NATIVE = os.path.join(ROOT, 'render', 'native')

# overrides.tsv state rows: slot -> symbol fragment
SLOTS = {23: 'SetBlendFunc', 24: 'SetAlphaBlendEnable', 26: 'SetRenderState', 27: 'GetRenderState',
         48: 'SetTextureStageState', 49: 'GetTextureStageState', 51: 'SetSamplerState', 52: 'GetSamplerState',
         53: 'RestoreSamplerState', 55: 'SetTexture', 56: 'GetTexture', 57: 'ClearTextureUsage'}

DRIVER = r'''
#include "engine_stubs.h"
#include "../gl/nxgl.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
using namespace nx;
typedef engine::NiDX9RenderStateLayout L;
static uint32_t rs[1024], tss[128], samp[128];
static uint32_t ReadList(const char* s, uint32_t* out) {   // "t,v,t,v,..." hex -> pairs, then the end marker
    uint32_t n = 0;
    while (s && *s) { out[n++] = (uint32_t)strtoul(s, (char**)&s, 16); if (*s == ',') s++; }
    out[n] = 0xFFFFFFFFu;
    return n / 2;
}
int main(int argc, char** argv) {
    if (argc < 6) return 2;
    const uint32_t nrs = ReadList(argv[1], rs), ntss = ReadList(argv[2], tss), nsamp = ReadList(argv[4], samp);
    const uint32_t tssStages = (uint32_t)atoi(argv[3]), sampStages = (uint32_t)atoi(argv[5]);
    char err[256];
    const NxGpuApi* api = nxgl_create(0, 0, err, sizeof err);
    if (!api) { fprintf(stderr, "nxgl: %s\n", err); return 2; }
    Backend* be = GpuBackend(api);
    static Renderer r;
    NxImageDesc rt; memset(&rt, 0, sizeof rt);
    rt.width = 16; rt.height = 16; rt.format = NxFmt_RGBA8; rt.flags = NxImg_RenderTarget;
    if (!r.Init(be, 16, 16, be->ImageCreate(rt), 0)) return 2;
    StateTracker& st = r.state();
    static uint64_t mem[L::kSize / 8];
    engine::NativeDevice* dev = engine::CreateNativeDevice();
    L::Construct(mem, dev);
    engine::NiDX9RenderState* e = (engine::NiDX9RenderState*)(void*)mem;
    L::DefaultsPass(mem, dev, rs, tss, tssStages, samp, sampStages);
    int bad = 0, notD3D9 = 0;
    uint32_t d3d[NXD3D_RS_COUNT];
    nx_d3d9_default_rs(d3d);
    for (uint32_t i = 0; i < nrs; i++) {
        const uint32_t s = rs[2 * i], v = rs[2 * i + 1];
        if (s >= NXD3D_RS_COUNT) { printf("render state %u: not a D3D9 state\n", s); bad++; continue; }
        if (st.GetRenderState(s) != v || e->GetRenderState(s) != v) { printf("FAIL render state %u: tracker %u cache %u\n", s, st.GetRenderState(s), e->GetRenderState(s)); bad++; }
        notD3D9 += v != d3d[s];
    }
    for (uint32_t s = 0; s < tssStages; s++) {
        uint32_t def[NXD3D_TSS_COUNT];
        nx_d3d9_default_tss(def, s);
        for (uint32_t i = 0; i < ntss; i++) {
            const uint32_t t = tss[2 * i];
            uint32_t v = tss[2 * i + 1];
            if (t == 11) v = s; else if ((t == 1 || t == 4) && s) v = 1;
            if (L::TssSlot(t) < 0) { printf("FAIL texture stage type %u: in the defaults pass, not in the cache\n", t); bad++; continue; }
            if (st.GetTextureStageState(s, t) != v || e->GetTextureStageState(s, t) != L::kUnknown) {
                printf("FAIL texture stage %u type %u: tracker %u cache %u\n", s, t, st.GetTextureStageState(s, t), e->GetTextureStageState(s, t)); bad++;
            }
            notD3D9 += t < NXD3D_TSS_COUNT && v != def[t];
        }
    }
    uint32_t sd[NXD3D_SAMP_COUNT];
    nx_d3d9_default_samp(sd);
    for (uint32_t s = 0; s < sampStages; s++)
        for (uint32_t i = 0; i < nsamp; i++) {
            const uint32_t t = samp[2 * i], v = samp[2 * i + 1];
            if (L::SampSlot(t) < 0) { printf("FAIL sampler type %u: in the defaults pass, not in the cache\n", t); bad++; continue; }
            if (st.GetSamplerState(s, t) != v || e->GetSamplerState(s, t) != v) { printf("FAIL sampler %u type %u\n", s, t); bad++; }
            e->SetSamplerState(s, t, v ^ 1, 0);   // a draw
            e->RestoreSamplerState(s, t);
            if (st.GetSamplerState(s, t) != v || e->GetSamplerState(s, t) != v) { printf("FAIL sampler %u type %u: restore\n", s, t); bad++; }
            notD3D9 += t < NXD3D_SAMP_COUNT && v != sd[t];
        }
    for (uint32_t t = 0; t < NXD3D_SAMP_COUNT; t++)
        if (L::SampSlot(t) < 0 && e->GetSamplerState(0, t) != 0) { printf("FAIL sampler type %u: not cached, get must be 0\n", t); bad++; }
    printf("defaults pass: %u render, %u texture stage x %u stages, %u sampler x %u stages; %d checks failed; "
           "%d defaults differ from D3D9's\n", nrs, ntss, tssStages, nsamp, sampStages, bad, notD3D9);
    return bad ? 1 : 0;
}
'''

# synthetic tables (type, value): not the engine's
SYN_RS = [(7, 1), (14, 1), (22, 1), (27, 0), (168, 0xF), (166, 0x42800000), (193, 0x80808080)]
SYN_TSS = [(1, 4), (2, 2), (3, 1), (4, 2), (5, 2), (6, 1), (24, 0)]
SYN_SAMP = [(1, 2), (2, 4), (5, 3), (6, 3), (7, 1)]


def pe(d):
    p = struct.unpack_from('<I', d, 0x3c)[0]
    n, opt = struct.unpack_from('<H', d, p + 6)[0], struct.unpack_from('<H', d, p + 20)[0]
    base = struct.unpack_from('<I', d, p + 24 + 28)[0]
    secs = []
    for i in range(n):
        o = p + 24 + opt + 40 * i
        secs.append((struct.unpack_from('<I', d, o + 12)[0] + base, struct.unpack_from('<I', d, o + 16)[0],
                     struct.unpack_from('<I', d, o + 20)[0]))
    return secs


def layout_constants():
    h = open(os.path.join(NATIVE, 'n1', 'engine_stubs.h')).read()
    m = re.search(r'struct NiDX9RenderStateLayout \{.*?enum : uint32_t \{(.*?)\};', h, re.S)
    return {k: int(v, 0) for k, v in re.findall(r'(k\w+) = (0x[0-9A-Fa-f]+|\d+)', m.group(1))}


def exe_inputs(path, fails):
    d = open(path, 'rb').read()
    secs = pe(d)
    off = lambda a: next((ro + a - va for va, rs, ro in secs if va <= a < va + rs), None)
    va_of = lambda o: next((va + o - ro for va, rs, ro in secs if ro <= o < ro + rs), None)
    u32 = lambda a: struct.unpack_from('<I', d, off(a))[0]
    i = d.find(b'.?AVNiDX9RenderState@@')
    if i < 0:
        fails.append('RTTI of NiDX9RenderState not found')
        return None
    td, vft = va_of(i - 8), None
    for m in re.finditer(re.escape(struct.pack('<I', td)), d):
        if struct.unpack_from('<II', d, m.start() - 12) != (0, 0):
            continue
        m2 = re.search(re.escape(struct.pack('<I', va_of(m.start() - 12))), d)
        if m2:
            vft = va_of(m2.start()) + 4
            break
    if not vft:
        fails.append('NiDX9RenderState vftable not found')
        return None
    tsv = open(os.path.join(NATIVE, 'overrides.tsv')).read()
    body = lambda a: d[off(a):off(a) + 0x200]
    for s, sym in SLOTS.items():
        a = u32(vft + 4 * s)
        if not re.search(r'^%08x\t\S*%s\S*\t.*vft=NiDX9RenderState:%d\b' % (a, sym, s), tsv, re.M):
            fails.append('slot %d (%s): overrides.tsv has no row for 0x%08x' % (s, sym, a))
    L = layout_constants()
    le = lambda v: struct.pack('<I', v)
    checks = [(26, le(L['kRsLock'])), (26, le(L['kRs'])), (27, le(L['kRs'])), (23, le(L['kDevice'])),
              (48, le(L['kTss'])), (48, b'\x66\x83\xf8' + bytes([L['kTssPerStage']])),
              (51, le(L['kSamp'])), (51, b'\x66\x83\xf8' + bytes([L['kSampPerStage']])),
              (52, b'\x66\x83\xfa' + bytes([L['kSampPerStage']])), (53, le(L['kSamp'] + 4)),
              (55, le(L['kTex'])), (57, le(L['kTex']))]
    for s, pat in checks:
        if pat not in body(u32(vft + 4 * s)):
            fails.append('slot %d does not use %s (NiDX9RenderStateLayout)' % (s, pat.hex()))
    ctor_fill = le(L['kUnknown'])
    if d.count(b'\xc7\x00' + ctor_fill) < 3:
        fails.append('the constructor does not fill the caches with kUnknown')

    def pairs(a):
        out = []
        while u32(a) != 0xFFFFFFFF and len(out) < 300:
            out.append((u32(a), u32(a + 4)))
            a += 8
        return out
    b22, b47 = body(u32(vft + 4 * 22)), body(u32(vft + 4 * 47))
    m22 = re.search(rb'\x8b\x0d(....)\xb8(....)', b22, re.S)
    mt = re.search(rb'\x39\x1d(....).{2,8}\xa1(....)\xb9(....)', b47, re.S)
    ms = re.search(rb'\x39\x05(....).{2,10}\x8b\x35(....)\xbb(....)', b47, re.S)
    if not (m22 and mt and ms and m22.group(1) == m22.group(2) and mt.group(2) == mt.group(3) and ms.group(2) == ms.group(3)):
        fails.append('the defaults passes (slots 22 / 47) do not have the expected shape')
        return None
    v = lambda g: struct.unpack('<I', g)[0]
    return (pairs(v(m22.group(1))), pairs(v(mt.group(2))), min(u32(v(mt.group(1))), 16),
            pairs(v(ms.group(2))), min(u32(v(ms.group(1))), 16))


def n1_sources(n1):
    # the N1 library sources, as the Makefile lists them (N1SRC)
    mk = open(os.path.join(HERE, 'Makefile')).read()
    m = re.search(r'^N1SRC := (.*)$', mk, re.M)
    return [os.path.join(n1, f) for f in re.findall(r'\$\(N1\)/(\S+)', m.group(1))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--exe')
    o = ap.parse_args()
    out = os.path.abspath(o.out)
    fails = []
    inputs = (SYN_RS, SYN_TSS, 2, SYN_SAMP, 3)
    if o.exe:
        got = exe_inputs(o.exe, fails)
        print('exe: %s' % ('; '.join(fails) if fails else 'vftable rows, cache layout and bounds as NiDX9RenderStateLayout'))
        if got:
            inputs = got
    work = tempfile.mkdtemp(prefix='s6n-state-')
    try:
        drv = os.path.join(work, 'driver.cpp')
        open(drv, 'w').write(DRIVER)
        exe = os.path.join(work, 'drv')
        n1 = os.path.join(NATIVE, 'n1')
        r = subprocess.run(['g++', '-std=c++17', '-O1', '-fno-exceptions', '-fno-rtti', '-w', '-I' + os.path.join(NATIVE, 'include'),
                            '-I' + os.path.join(NATIVE, 'front'), '-I' + os.path.join(out, 'shaders'), '-I' + n1, '-o', exe, drv,
                            os.path.join(out, 'front.o')] + n1_sources(n1) +
                           [os.path.join(out, x) for x in ('nxgl.o', 'gpu_dispatch.o', 'd3d9_state_s.o', 'nx_shaders.o')] +
                           ['-lEGL', '-lm'], capture_output=True, text=True)
        if r.returncode:
            print('build failed\n' + r.stderr[-3000:])
            return 1
        lst = lambda ps: ','.join('%x,%x' % p for p in ps)
        p = subprocess.run([exe, lst(inputs[0]), lst(inputs[1]), str(inputs[2]), lst(inputs[3]), str(inputs[4])],
                           capture_output=True, text=True, timeout=120)
        sys.stdout.write(p.stdout)
        if p.returncode:
            fails.append('driver: exit %d %s' % (p.returncode, p.stderr[-300:]))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('%s: %s' % ('exe tables' if o.exe else 'synthetic tables', 'FAIL ' + '; '.join(fails) if fails else 'ok'))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
