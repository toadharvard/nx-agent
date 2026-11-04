#!/usr/bin/env python3
"""test_s6n_alpha_compare.py - V1 (D14) against S6n 021fc46: "branch-free alpha compare in the D3D9 epilogue:
identical to the switch for all functions with ordered and NaN operands".

nx_compare() is taken from render/native/shaders/d3d9_epilogue.glsl as it stands (GLSL subset that is also C:
uint, bool, ?:, 0u) and compiled on the host next to the switch it replaced (021fc46^, quoted below) and a D3D9
oracle. The function and the reference come through the real translator (front/d3d9_state.c nx_d3d9_translate:
D3DRS_ALPHAFUNC -> shader bits, D3DRS_ALPHAREF -> NxDynamicState.alphaRef, then nx_epilogue_fill), so the test
covers D3DCMP_* 1..8 as the device sets them, plus 0 and 9 (invalid: NEVER, as the translator maps them).
  A1  branch-free == switch: funcs 0..15 x every ref 0..255 x alpha {k/255, k in 0..255} and their float
      neighbours, 0, -0, 1, denormals, +-inf, NaN
  A2  D3D9 semantics: D3DCMP 1..8 x ref 0..255 x alpha k/255 (and neighbours): pass == (alpha OP ref/255), with
      LESSEQUAL / GREATEREQUAL / EQUAL passing at equality (alpha = k/255 with k = ref) and NOTEQUAL failing there
  A3  D3D9 edge values: ref 0 with alpha 0 (GREATER fails, GREATEREQUAL passes), ref 255 with alpha 1 (LESS fails,
      LESSEQUAL passes), NaN alpha (only NOTEQUAL and ALWAYS pass, as the switch did)
  A4  invalid D3DCMP 0 and 9: the translator's NEVER; nothing passes
  python3 -I tests/v1/test_s6n_alpha_compare.py   -> one line per case, exit 1 when any shows a defect
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
NATIVE = os.path.normpath(os.path.join(HERE, '..', '..', 'render', 'native'))
EPI = os.path.join(NATIVE, 'shaders', 'd3d9_epilogue.glsl')

OLD = r'''
bool nx_compare_switch(uint func, float a, float ref) {
    switch (func) {
    case 0u: return false;          // NEVER
    case 1u: return a < ref;        // LESS
    case 2u: return a == ref;       // EQUAL
    case 3u: return a <= ref;       // LESSEQUAL
    case 4u: return a > ref;        // GREATER
    case 5u: return a != ref;       // NOTEQUAL
    case 6u: return a >= ref;       // GREATEREQUAL
    default: return true;           // ALWAYS
    }
}
'''

HARNESS = r'''
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "d3d9_state.h"
typedef unsigned int uint;
@NEW@
@OLD@
static int d3d9(uint32_t d3dcmp, float a, float ref) {   /* the D3D9 / Nine alpha test: alpha OP ref/255 */
    switch (d3dcmp) {
    case 2: return a < ref; case 3: return a == ref; case 4: return a <= ref; case 5: return a > ref;
    case 6: return a != ref; case 7: return a >= ref; case 8: return 1; default: return 0;   /* 1 NEVER, invalid */
    }
}
static void xlat(uint32_t d3dcmp, uint32_t ref, uint32_t* func, float* fref) {
    uint32_t rs[NXD3D_RS_COUNT];
    nx_d3d9_default_rs(rs);
    rs[15] = 1; rs[24] = ref; rs[25] = d3dcmp;   /* ALPHATESTENABLE, ALPHAREF, ALPHAFUNC */
    NxD3D9Input in; memset(&in, 0, sizeof in);
    in.rs = rs; in.ps_version = 0x0200;
    in.viewport.Width = 64; in.viewport.Height = 64; in.viewport.MaxZ = 1; in.scissor.right = 64; in.scissor.bottom = 64;
    NxRenderState st; NxDynamicState dyn; uint32_t bits = 0;
    nx_d3d9_translate(&in, &st, &dyn, &bits);
    NxEpilogue e; memset(&e, 0, sizeof e);
    nx_epilogue_fill(&e, &dyn, bits);
    *func = e.alphaFunc; *fref = e.alphaRef;
}
static float vals[1200]; static int nv;
int main(void) {
    for (int k = 0; k < 256; k++) {
        float a = (float)k / 255.0f;
        vals[nv++] = a; vals[nv++] = nextafterf(a, -1.0f); vals[nv++] = nextafterf(a, 2.0f);
    }
    float ex[] = {0.0f, -0.0f, 1.0f, 1e-40f, -1e-40f, INFINITY, -INFINITY, NAN, -NAN, 2.0f, -1.0f};
    for (unsigned i = 0; i < sizeof ex / sizeof ex[0]; i++) vals[nv++] = ex[i];
    long a1 = 0, a1bad = 0, a2 = 0, a2bad = 0, a3bad = 0, a4bad = 0;
    for (uint f = 0; f < 16; f++)
        for (int r = 0; r < 256; r++)
            for (int i = 0; i < nv; i++) {
                float ref = (float)r / 255.0f;
                a1++;
                if (nx_compare(f, vals[i], ref) != nx_compare_switch(f, vals[i], ref)) {
                    if (a1bad++ < 5) printf("    A1 func %u ref %d alpha %a: branch-free %d switch %d\n", f, r, vals[i],
                                            nx_compare(f, vals[i], ref), nx_compare_switch(f, vals[i], ref));
                }
            }
    printf("A1: %s - branch-free vs switch: %ld comparisons, %ld differ\n", a1bad ? "DEFECT" : "ok", a1, a1bad);
    for (uint32_t d = 1; d <= 8; d++)
        for (uint32_t r = 0; r < 256; r++) {
            uint32_t func; float fref;
            xlat(d, r, &func, &fref);
            for (int i = 0; i < nv; i++) {   /* k/255 values and neighbours, 0, -0, 1, denormals, +-inf, 2, -1 */
                if (isnan(vals[i])) continue;
                a2++;
                const int want = d3d9(d, vals[i], (float)r / 255.0f), got = nx_compare(func, vals[i], fref);
                if (want != got && a2bad++ < 5) printf("    A2 D3DCMP %u ref %u alpha %a: epilogue %d D3D9 %d\n", d, r, vals[i], got, want);
            }
            /* A3: equality, the 0 / 255 ends, NaN */
            const float eq = (float)r / 255.0f;
            const int at_eq = nx_compare(func, eq, fref);
            const int want_eq = d == 3 || d == 4 || d == 7 || d == 8;
            if (at_eq != want_eq && a3bad++ < 5) printf("    A3 D3DCMP %u ref %u alpha == ref/255: %d, D3D9 %d\n", d, r, at_eq, want_eq);
            const int nan_pass = nx_compare(func, NAN, fref), want_nan = d == 6 || d == 8;
            if (nan_pass != want_nan && a3bad++ < 5) printf("    A3 D3DCMP %u ref %u alpha NaN: %d, want %d\n", d, r, nan_pass, want_nan);
        }
    {
        uint32_t func; float fref;
        xlat(5, 0, &func, &fref); if (nx_compare(func, 0.0f, fref)) a3bad++, printf("    A3 GREATER ref 0 alpha 0 passes\n");
        xlat(7, 0, &func, &fref); if (!nx_compare(func, 0.0f, fref)) a3bad++, printf("    A3 GREATEREQUAL ref 0 alpha 0 fails\n");
        xlat(2, 255, &func, &fref); if (nx_compare(func, 1.0f, fref)) a3bad++, printf("    A3 LESS ref 255 alpha 1 passes\n");
        xlat(4, 255, &func, &fref); if (!nx_compare(func, 1.0f, fref)) a3bad++, printf("    A3 LESSEQUAL ref 255 alpha 1 fails\n");
    }
    printf("A2: %s - D3DCMP 1..8 x ref 0..255 through the translator vs alpha OP ref/255: %ld comparisons, %ld differ\n",
           a2bad ? "DEFECT" : "ok", a2, a2bad);
    printf("A3: %s - equality (LESSEQUAL/GREATEREQUAL/EQUAL pass, NOTEQUAL fails), ref 0 / 255 ends, NaN alpha: %ld differ\n",
           a3bad ? "DEFECT" : "ok", a3bad);
    for (uint32_t d = 0; d <= 9; d += 9) {
        uint32_t func; float fref;
        xlat(d, 128, &func, &fref);
        for (int i = 0; i < nv; i++) if (nx_compare(func, vals[i], fref)) a4bad++;
    }
    printf("A4: %s - invalid D3DCMP 0 and 9 map to NEVER: %ld passing fragments\n", a4bad ? "DEFECT" : "ok", a4bad);
    return (a1bad || a2bad || a3bad || a4bad) ? 1 : 0;
}
'''


def main():
    src = open(EPI).read()
    m = re.search(r'^bool nx_compare\(uint func, float a, float ref\) \{\n.*?\n\}\n', src, re.M | re.S)
    if not m:
        print('nx_compare is not found in d3d9_epilogue.glsl')
        return 1
    work = tempfile.mkdtemp(prefix='v1-alpha-')   # raises when it cannot
    try:
        c = os.path.join(work, 'h.c')
        open(c, 'w').write(HARNESS.replace('@NEW@', m.group(0)).replace('@OLD@', OLD))
        exe = os.path.join(work, 'h')
        r = subprocess.run(['gcc', '-std=c99', '-O1', '-fno-fast-math', '-Wno-unused', '-I' + os.path.join(NATIVE, 'include'),
                            '-I' + os.path.join(NATIVE, 'front'), '-o', exe, c, os.path.join(NATIVE, 'front', 'd3d9_state.c'), '-lm'],
                           capture_output=True, text=True)
        if r.returncode:
            print('build failed:\n' + r.stderr[-2000:])
            return 1
        return subprocess.run([exe]).returncode and 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
