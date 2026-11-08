#!/usr/bin/env python3
"""The native TLS block fnvlink emits for the image's __declspec(thread) data (emit_tls) is per thread.

R2's s2d/list/97deacd38aec listed __fnvnx_tls_image and the __fnvnx_tls_<addr> slots among the guest's trap stubs
(data stubs are 4096-byte objects, and so is this block). This test assembles emit_tls's own output with a driver
(system aarch64-linux-gnu gcc, static, run under qemu-aarch64): every symbol is STT_TLS, each slot is the block +
(address - template page), and two threads get distinct blocks whose slots do not see each other's stores, as
toolchain/game/boot.cpp's image TLS hook and platform/win32/thread.cpp's fs:[0x2c][0] rely on. Our own code only."""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fnvlink  # noqa: E402

DRIVER = r'''
#include <pthread.h>
#include <stdio.h>
#include <stdint.h>
extern __thread unsigned char blk[] __asm__("__fnvnx_tls_image");
extern __thread uint32_t s0 __asm__("__fnvnx_tls_01272000");
extern __thread uint32_t s4 __asm__("__fnvnx_tls_01272004");
extern __thread uint32_t s268 __asm__("__fnvnx_tls_01272268");
extern __thread uint64_t own __asm__("__fnvnx_tls_0126fd00");
static uintptr_t addr[2]; static uint32_t seen[2];
static pthread_barrier_t bar;
static void *run(void *p) {
    int k = (int)(intptr_t)p;
    addr[k] = (uintptr_t)blk;
    s4 = 100 + k; own = 7 + k;
    pthread_barrier_wait(&bar);              /* both stored before either reads */
    seen[k] = s4 + (uint32_t)own * 1000;
    if ((uintptr_t)&s0 != (uintptr_t)blk || (uintptr_t)&s4 != (uintptr_t)blk + 4 ||
        (uintptr_t)&s268 != (uintptr_t)blk + 0x268 || s268 != 0) seen[k] = 0;
    return 0;
}
int main(void) {
    pthread_t t[2];
    pthread_barrier_init(&bar, 0, 2);
    for (int k = 0; k < 2; k++) pthread_create(&t[k], 0, run, (void *)(intptr_t)k);
    for (int k = 0; k < 2; k++) pthread_join(t[k], 0);
    printf("distinct=%d seen0=%u seen1=%u\n", addr[0] != addr[1], seen[0], seen[1]);
    return !(addr[0] != addr[1] && seen[0] == 7100 && seen[1] == 8101);
}
'''

fails = 0


def ok(name, cond, info=''):
    global fails
    print(('ok   ' if cond else 'FAIL ') + name + ('' if cond else ': ' + str(info)[:600]))
    fails += not cond


def main():
    cc = shutil.which('aarch64-linux-gnu-gcc')
    qemu = shutil.which('qemu-aarch64') or shutil.which('qemu-aarch64-static')
    if not cc or not qemu:
        print('SKIP: aarch64-linux-gnu-gcc or qemu-aarch64 missing')
        return 0
    asm = fnvlink.emit_tls([0x01272000, 0x01272004, 0x01272268, 0x0126fd00])
    ok('the block is declared a TLS object', '.type __fnvnx_tls_image, %tls_object' in asm, asm[:400])
    with tempfile.TemporaryDirectory() as t:
        open(os.path.join(t, 'tls.S'), 'w').write(asm)
        open(os.path.join(t, 'd.c'), 'w').write(DRIVER)
        exe = os.path.join(t, 'd')
        r = subprocess.run([cc, '-O1', '-static', '-pthread', '-o', exe, os.path.join(t, 'd.c'), os.path.join(t, 'tls.S')],
                           capture_output=True, text=True)
        ok('builds', r.returncode == 0, r.stderr)
        if r.returncode:
            return 1
        syms = subprocess.run(['aarch64-linux-gnu-readelf', '-sW', exe], capture_output=True, text=True).stdout
        types = {m.group(2): m.group(1) for m in re.finditer(r'\s(\w+)\s+GLOBAL\s+\w+\s+\S+\s+(__fnvnx_tls_\w+)$', syms, re.M)}
        want = ['__fnvnx_tls_image', '__fnvnx_tls_01272000', '__fnvnx_tls_01272004', '__fnvnx_tls_01272268',
                '__fnvnx_tls_0126fd00']
        ok('every block and slot symbol is STT_TLS (not a .bss data object)',
           all(types.get(n) == 'TLS' for n in want), types)
        r = subprocess.run([qemu, exe], capture_output=True, text=True, timeout=120)
        ok('two threads: distinct blocks, slots at block + offset, stores private', r.returncode == 0,
           r.stdout + r.stderr)
    print('test_tls_block: %d failed' % fails)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
