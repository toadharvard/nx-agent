#!/usr/bin/env python3
"""test_s6n_overrides_slots.py - V1 (D14) against S6n 68ccc78: "overrides - slots 48/49 are texture stage states;
sampler states are slots 51-53" (render/native/overrides.tsv, n1/engine_stubs.cpp). The commit also says slot 53
(0x00e91120, Restore) "re-sets the value saved by the defaults pass of slot 47 through slot 51" and that slots
48/49 are "recorded with D3D9 defaults".
  O1  (--exe only) NiDX9RenderState's vftable, found through its RTTI complete object locator in the owner's exe:
      slots 48/49/51/52/53 are the rows' addresses; 0x00e88930 calls the device at +0x10C (SetTextureStageState),
      0x00e910a0 at +0x114 (SetSamplerState), 0x00e91120 calls this->vftable[51] (+0xCC) with the saved value;
      NiD3DRenderState's slots 48/49 are the same functions (inherited). Without --exe: the rows only.
  O2  slot 53 after the defaults pass: the exe's slot 47 (0x00e88060) stores each default in the saved field and
      calls slot 51 with it (4th argument 0); a draw then sets another value; slot 53 must restore the default.
      Driven through the stubs (engine_stubs.cpp as it stands) on the GL backend with MAGFILTER / ADDRESSU / MIPFILTER
      defaults that differ from D3D9's (with --exe: the exe's own sampler defaults table, read at run time).
  O3  the texture stage state stubs' defaults for every D3DTSS type 1..32 against D3D9's (COLORARG0, ALPHAARG0 and
      RESULTARG are D3DTA_CURRENT; D3DTSS_RESULTARG is 28, COLORARG0 26, ALPHAARG0 27).
  python3 -I tests/v1/test_s6n_overrides_slots.py --out <make -C tests/s6n OUT=<dir> n1 output> [--exe FalloutNV.exe]
The exe is the owner's game data: read in place, nothing of it is written or kept. Exit 1 when any case is a DEFECT.
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

ROWS = {48: 0x00e88930, 49: 0x00e88980, 51: 0x00e910a0, 52: 0x00e910f0, 53: 0x00e91120}
SYMS = {48: 'SetTextureStageState', 49: 'GetTextureStageState', 51: 'SetSamplerState', 52: 'GetSamplerState',
        53: 'RestoreSamplerState'}

DRIVER = r'''
#include "engine_stubs.h"
#include "../gl/nxgl.h"
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
using namespace nx;
int main(int argc, char** argv) {
    char err[256];
    const NxGpuApi* api = nxgl_create(0, 0, err, sizeof err);
    if (!api) { fprintf(stderr, "nxgl: %s\n", err); return 2; }
    Backend* be = GpuBackend(api);
    static Renderer r;
    NxImageDesc rt; memset(&rt, 0, sizeof rt);
    rt.width = 16; rt.height = 16; rt.format = NxFmt_RGBA8; rt.flags = NxImg_RenderTarget;
    if (!r.Init(be, 16, 16, be->ImageCreate(rt), 0)) return 2;
    static int obj;
    engine::NiDX9RenderState* ers = (engine::NiDX9RenderState*)(void*)&obj;
    int bad = 0;
    // O2: argv = stage type default other ...
    for (int i = 1; i + 3 < argc; i += 4) {
        unsigned st = (unsigned)atoi(argv[i]), ty = (unsigned)atoi(argv[i + 1]), def = (unsigned)atoi(argv[i + 2]), oth = (unsigned)atoi(argv[i + 3]);
        ers->SetSamplerState(st, ty, def, 0);       // slot 47's call (saved field written by the engine itself)
        ers->SetSamplerState(st, ty, oth, 0);       // a draw's state
        ers->RestoreSamplerState(st, ty);            // slot 53
        unsigned got = ers->GetSamplerState(st, ty);
        printf("O2 stage %u type %u: default %u, set %u, after restore %u -> %s\n", st, ty, def, oth, got, got == def ? "ok" : "DEFECT");
        bad += got != def;
    }
    // O3: D3D9 texture stage state defaults
    static const char* names[33] = {0, "COLOROP", "COLORARG1", "COLORARG2", "ALPHAOP", "ALPHAARG1", "ALPHAARG2", "BUMPENVMAT00",
        "BUMPENVMAT01", "BUMPENVMAT10", "BUMPENVMAT11", "TEXCOORDINDEX", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, "BUMPENVLSCALE",
        "BUMPENVLOFFSET", "TEXTURETRANSFORMFLAGS", 0, "COLORARG0", "ALPHAARG0", "RESULTARG", 0, 0, 0, "CONSTANT"};
    int tbad = 0;
    for (unsigned s = 0; s < 8; s++)
        for (unsigned t = 1; t < 33; t++) {
            if (!names[t]) continue;
            unsigned want = 0;
            switch (t) {
            case 1: want = s ? 1 : 4; break;            // COLOROP MODULATE / DISABLE
            case 2: case 5: want = 2; break;             // ARG1 TEXTURE
            case 3: case 6: want = 1; break;             // ARG2 CURRENT
            case 4: want = s ? 1 : 2; break;             // ALPHAOP SELECTARG1 / DISABLE
            case 11: want = s; break;                    // TEXCOORDINDEX
            case 26: case 27: case 28: want = 1; break;  // COLORARG0, ALPHAARG0, RESULTARG: D3DTA_CURRENT
            default: want = 0;
            }
            unsigned got = ers->GetTextureStageState(s, t);
            if (got != want) { if (tbad < 8) printf("    O3 stage %u %s (%u): stub %u, D3D9 %u\n", s, names[t], t, got, want); tbad++; }
        }
    printf("O3: %s - texture stage state defaults (stages 0-7, types 1-32): %d differ from D3D9\n", tbad ? "DEFECT" : "ok", tbad);
    return bad || tbad ? 1 : 0;
}
'''


def pe_sections(d):
    pe = struct.unpack_from('<I', d, 0x3c)[0]
    nsec = struct.unpack_from('<H', d, pe + 6)[0]
    opt = struct.unpack_from('<H', d, pe + 20)[0]
    base = struct.unpack_from('<I', d, pe + 24 + 28)[0]
    secs = []
    for i in range(nsec):
        o = pe + 24 + opt + 40 * i
        va, rs, ro = struct.unpack_from('<I', d, o + 12)[0], struct.unpack_from('<I', d, o + 16)[0], struct.unpack_from('<I', d, o + 20)[0]
        secs.append((va + base, rs, ro))
    return secs


def exe_checks(path):
    d = open(path, 'rb').read()
    secs = pe_sections(d)
    off = lambda a: next((ro + a - va for va, rs, ro in secs if va <= a < va + rs), None)
    va_of = lambda o: next((va + o - ro for va, rs, ro in secs if ro <= o < ro + rs), None)
    u32 = lambda a: struct.unpack_from('<I', d, off(a))[0]

    def vft(cls):
        i = d.find(b'.?AV' + cls + b'@@')
        if i < 0:
            return None
        td = va_of(i - 8)
        for m in re.finditer(re.escape(struct.pack('<I', td)), d):
            if m.start() < 12 or struct.unpack_from('<III', d, m.start() - 12) [0] != 0 or struct.unpack_from('<III', d, m.start() - 12)[1] != 0:
                continue
            col = va_of(m.start() - 12)
            for m2 in re.finditer(re.escape(struct.pack('<I', col)), d):
                return va_of(m2.start()) + 4
        return None
    fails, notes = 0, []
    v9, v3 = vft(b'NiDX9RenderState'), vft(b'NiD3DRenderState')
    if not v9 or not v3:
        return 1, ['RTTI for NiDX9RenderState / NiD3DRenderState not found'], None
    for s, a in ROWS.items():
        got = u32(v9 + 4 * s)
        if got != a:
            fails += 1
            notes.append('NiDX9RenderState slot %d is 0x%08x, the row says 0x%08x' % (s, got, a))
    for s in (48, 49):
        if u32(v3 + 4 * s) != ROWS[s]:
            fails += 1
            notes.append('NiD3DRenderState slot %d is not 0x%08x' % (s, ROWS[s]))

    def body(a):
        b = d[off(a):off(a) + 0x100]
        return b[:b.find(b'\xcc\xcc')]
    for a, disp, what in ((0x00e88930, 0x10C, 'device SetTextureStageState'), (0x00e910a0, 0x114, 'device SetSamplerState'),
                          (0x00e91120, 0xCC, 'this->vftable[51]')):
        if not re.search(rb'\x8b[\x80-\xbf]' + re.escape(struct.pack('<I', disp)), body(a)):
            fails += 1
            notes.append('0x%08x does not load +0x%x (%s)' % (a, disp, what))
        else:
            notes.append('0x%08x loads +0x%x (%s)' % (a, disp, what))
    # slot 47's sampler defaults: the (type, value) list it walks (mov esi, [table] / mov ebx, table)
    b47 = d[off(u32(v9 + 4 * 47)):off(u32(v9 + 4 * 47)) + 0x140]
    m = re.search(rb'\x8b\x35(....)\xbb(....)', b47, re.S)
    defaults = []
    if m and m.group(1) == m.group(2):
        t = struct.unpack('<I', m.group(1))[0]
        while u32(t) != 0xFFFFFFFF and len(defaults) < 14:
            defaults.append((u32(t), u32(t + 4)))
            t += 8
    notes.append('slot 47 (0x%08x) sampler defaults (type, value): %s' % (u32(v9 + 4 * 47), defaults))
    return fails, notes, defaults


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--exe')
    o = ap.parse_args()
    out = os.path.abspath(o.out)
    fails = 0
    tsv = open(os.path.join(NATIVE, 'overrides.tsv')).read()
    rows_ok = all(re.search(r'^%08x\t\S*%s\S*\t.*vft=NiDX9RenderState:%d$' % (a, SYMS[s], s), tsv, re.M) for s, a in ROWS.items())
    defaults = None
    if o.exe:
        f, notes, defaults = exe_checks(o.exe)
        fails += f or not rows_ok
        print('O1: %s - overrides.tsv rows%s; exe: %s' % ('ok' if not f and rows_ok else 'DEFECT', '' if rows_ok else ' (a row is missing)', '; '.join(notes)))
    else:
        fails += not rows_ok
        print('O1: %s - overrides.tsv rows for slots 48/49/51/52/53 (no --exe: the vftable is not checked)' % ('ok' if rows_ok else 'DEFECT'))
    # O2 inputs: (stage, type, default, other); D3D9 defaults: ADDRESSU WRAP 1, MAGFILTER POINT 1, MIPFILTER NONE 0
    o2 = [(0, 5, 2, 1), (1, 1, 3, 1), (0, 7, 2, 0)]
    if defaults:
        o2 = [(s, t, v, 1 if v != 1 else 2) for s in (0, 1) for t, v in defaults]
    work = tempfile.mkdtemp(prefix='v1-ovr-')   # raises when it cannot
    try:
        drv = os.path.join(work, 'driver.cpp')
        open(drv, 'w').write(DRIVER)
        exe = os.path.join(work, 'drv')
        n1 = os.path.join(NATIVE, 'n1')
        r = subprocess.run(['g++', '-std=c++17', '-O1', '-fno-exceptions', '-fno-rtti', '-w', '-I' + os.path.join(NATIVE, 'include'),
                            '-I' + os.path.join(NATIVE, 'front'), '-I' + os.path.join(out, 'shaders'), '-I' + n1, '-o', exe, drv,
                            os.path.join(n1, 'renderer.cpp'), os.path.join(n1, 'engine_stubs.cpp'), os.path.join(n1, 'engine_textures.cpp'),
                            os.path.join(out, 'front.o')] +
                           [os.path.join(out, x) for x in ('nxgl.o', 'gpu_dispatch.o', 'd3d9_state_s.o', 'nx_shaders.o')] +
                           ['-lEGL', '-lm'], capture_output=True, text=True)
        if r.returncode:
            print('build failed\n' + r.stderr[-3000:])
            return 1
        p = subprocess.run([exe] + [str(x) for row in o2 for x in row], capture_output=True, text=True, timeout=120)
        lines = p.stdout.splitlines()
        o2bad = [ln for ln in lines if ln.startswith('O2') and ln.endswith('DEFECT')]
        print('O2: %s - slot 53 after the defaults pass returns to the default: %d of %d states do not' %
              ('DEFECT' if o2bad else 'ok', len(o2bad), len(o2)))
        for ln in lines:
            if ln.startswith('O2'):
                print('    ' + ln[3:])
        for ln in lines:
            if not ln.startswith('O2'):
                print(ln)
        if p.returncode not in (0, 1):
            print('driver: exit %d %s' % (p.returncode, p.stderr[-300:]))
        fails += p.returncode != 0
        return 1 if fails else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
