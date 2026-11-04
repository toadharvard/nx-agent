#!/usr/bin/env python3
"""test_nxgpu_ubo_cache.py - V1 (D14) against S6n 6d305cd3 (J-0149): "nxgpu record: per stage/slot cache of the
bound range, so a bind of the same range is skipped ... output unchanged".

The NxOp_BindUbo case of record() in render/native/host/nxgpu.c is taken as it stands (the text from
`case NxOp_BindUbo: {` to its `break;` and closing brace) and compiled on the host inside a stand-in record loop: a
stub dkCmdBufBindUniformBuffer logs what the GPU would have bound per stage and slot, and each sequence is checked
against the binding state an uncached backend leaves (every NxOp_BindUbo bound):
  U1  slot 1 bound twice to the same range: the second bind is skipped (the claim; not a defect either way)
  U2  slot 0 -> X, slot 1 -> Y, slot 0 -> X: the hardware state equals the uncached one
  U3  slot 0 -> X, slot 4 -> Y (deko3d has 16 UBO slots per stage; gpu_abi.h does not limit the slot), slot 0 -> Y
  U4  slot 0 -> X with size 256, then the same address with size 512
  U5  slot 15 and slot 0 kept apart; vertex slot 15 rebound to the same range: skipped (6 hardware binds)
The cache declarations (uboAddr/uboSize) are taken from nxgpu.c itself, not copied into the harness.
A sequence is a DEFECT when the GPU's bound (address, size) for any stage/slot differs from the uncached backend's.
  python3 -I tests/v1/test_nxgpu_ubo_cache.py   -> one line per case, exit 1 when any shows a defect
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
NXGPU = os.path.join(HERE, '..', '..', 'render', 'native', 'host', 'nxgpu.c')

HARNESS = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint64_t DkGpuAddr;
typedef void* DkCmdBuf;
enum { DkStage_Vertex = 0, DkStage_Fragment = 4 };
static DkGpuAddr hw[2][16]; static uint32_t hwsz[2][16]; static int nbinds;
static void dkCmdBufBindUniformBuffer(DkCmdBuf cb, int stage, uint32_t slot, DkGpuAddr ad, uint32_t size) {
    (void)cb; int s = stage == DkStage_Fragment; hw[s][slot & 15] = ad; hwsz[s][slot & 15] = size; nbinds++;
}
static DkGpuAddr mem_addr(uint32_t id, uint32_t off) { return ((DkGpuAddr)id << 32) + off; }
enum { NxOp_BindUbo = 1 };
/* one command list: ops of 5 words (stage mask, slot, block, offset, size) */
static int run(const uint32_t (*ops)[5], int n, int check_uncached) {
    DkGpuAddr ref[2][16]; uint32_t refsz[2][16];
    memset(hw, 0, sizeof hw); memset(hwsz, 0, sizeof hwsz); memset(ref, 0, sizeof ref); memset(refsz, 0, sizeof refsz);
    nbinds = 0;
    DkCmdBuf cb = 0;
@DECL@
    for (int i = 0; i < n; i++) {
        const uint32_t* a = ops[i]; uint32_t argc = 5;
        for (int s = 0; s < 2; s++) if (a[0] & (1u << s)) { ref[s][a[1] & 15] = mem_addr(a[2], a[3]); refsz[s][a[1] & 15] = a[4]; }
        switch (NxOp_BindUbo) {
@CASE@
        }
    }
    int bad = 0;
    for (int s = 0; s < 2; s++) for (int k = 0; k < 16; k++) if (hw[s][k] != ref[s][k] || hwsz[s][k] != refsz[s][k]) {
        printf("    stage %s slot %d: GPU %llx/%u, uncached %llx/%u\n", s ? "fragment" : "vertex", k,
               (unsigned long long)hw[s][k], hwsz[s][k], (unsigned long long)ref[s][k], refsz[s][k]);
        bad = 1;
    }
    (void)check_uncached;
    return bad;
}
int main(void) {
    int fails = 0, b;
    static const uint32_t u1[][5] = {{3, 1, 7, 0, 256}, {3, 1, 7, 0, 256}};
    b = run(u1, 2, 1); printf("U1: %s - same range twice: %d hardware binds for 2 ops x 2 stages\n", b ? "DEFECT" : "ok", nbinds); fails += b;
    static const uint32_t u2[][5] = {{3, 0, 7, 0, 256}, {3, 1, 7, 256, 256}, {3, 0, 7, 0, 256}};
    b = run(u2, 3, 1); printf("U2: %s - slot 0 X, slot 1 Y, slot 0 X\n", b ? "DEFECT" : "ok"); fails += b;
    static const uint32_t u3[][5] = {{3, 0, 7, 0, 256}, {3, 4, 7, 512, 256}, {3, 0, 7, 512, 256}};
    b = run(u3, 3, 1); printf("U3: %s - slot 0 X, slot 4 Y, slot 0 Y%s\n", b ? "DEFECT" : "ok",
                             b ? ": the slot 0 bind is skipped (slot 4 wrote the cache entry of slot 4 & 3 = 0)" : ""); fails += b;
    static const uint32_t u4[][5] = {{2, 2, 7, 0, 256}, {2, 2, 7, 0, 512}};
    b = run(u4, 2, 1); printf("U4: %s - same address, larger size\n", b ? "DEFECT" : "ok"); fails += b;
    static const uint32_t u5[][5] = {{3, 15, 7, 0, 256}, {3, 0, 7, 256, 256}, {3, 15, 7, 256, 256}, {1, 15, 7, 256, 256}};
    b = run(u5, 4, 1); printf("U5: %s - slot 15 X, slot 0 Y, slot 15 Y, vertex slot 15 Y again: %d binds\n",
                             b || nbinds != 6 ? "DEFECT" : "ok", nbinds); fails += b || nbinds != 6;
    return fails ? 1 : 0;
}
'''


def main():
    src = open(NXGPU).read()
    m = re.search(r'^([ \t]*)case NxOp_BindUbo: \{\n.*?\n\1\}\n', src, re.M | re.S)
    if not m:
        print('the NxOp_BindUbo case is not found in nxgpu.c')
        return 1
    # the cache arrays as nxgpu.c declares them (a harness copy of the old [2][4] made the fixed code overflow it)
    decl = re.findall(r'^[ \t]*(?:DkGpuAddr|uint32_t)[ \t]+ubo(?:Addr|Size)\[[^;]*;', src, re.M)
    if len(decl) != 2:
        print('the uboAddr/uboSize declarations are not found in nxgpu.c: %r' % decl)
        return 1
    work = tempfile.mkdtemp(prefix='v1-ubo-')   # raises when it cannot: never an empty or relative path
    try:
        c = os.path.join(work, 'h.c')
        with open(c, 'w') as f:
            f.write(HARNESS.replace('@CASE@', m.group(0)).replace('@DECL@', '\n'.join(decl)))
        exe = os.path.join(work, 'h')
        r = subprocess.run(['gcc', '-O1', '-Wno-unused', '-o', exe, c], capture_output=True, text=True)
        if r.returncode:
            print('build failed:\n' + r.stderr[-2000:])
            return 1
        return subprocess.run(['stdbuf', '-oL', exe]).returncode and 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
