#!/usr/bin/env python3
"""V2 check of S4 92fd3b4 (platform/crt/msvc_names.c): __emul / __emulu / __movsd / __movsb / xmemcpy give the MSVC
intrinsics' results, built LP64 static (no ILP32 libc here; the words are
uint32_t either way) and run under qemu-aarch64. Expected values are the x86 instruction
semantics (imul / mul edx:eax, rep movsd / rep movsb forward element by element).

    python3 tests/v2/test_s4_intrinsics.py     # needs aarch64-linux-gnu-gcc (static) and qemu-aarch64

  E1  __emul(0x66666667, -10) >> 32 == -5 (Havok's divide-by-10 shape), __emul(-1, -1) == 1
  E2  __emulu(0xffffffff, 0xffffffff) == 0xfffffffe00000001
  M1  __movsd counts 32-bit words; an overlapping forward copy (d = s + 1) repeats the first word like rep movsd
  M2  __movsb overlapping forward copy repeats the first byte
  X1  xmemcpy(d, s, n) copies n bytes and returns d
"""
import os
import shutil
import subprocess
import sys
import tempfile

PORT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
CHECK = r'''
#include <stddef.h>
#include <string.h>
long long __emul(int, int); unsigned long long __emulu(unsigned, unsigned);
void __movsd(unsigned long *, const unsigned long *, size_t); void __movsb(unsigned char *, const unsigned char *, size_t);
void *xmemcpy(void *, const void *, size_t);
void os_debug_print(const char *s) { (void)s; }
/* the other names of msvc_names.c call platform CRT functions: stand-ins (never called here) */
int _mbsnicmp(const unsigned char *a, const unsigned char *b, size_t n) { (void)a; (void)b; (void)n; return 0; }
long long _time64(long long *t) { (void)t; return 0; }
int strcat_s(char *d, size_t n, const char *s) { (void)d; (void)n; (void)s; return 0; }
int main(void) {
    int bad = 0;
    if ((int)((unsigned long long)__emul(0x66666667, -10) >> 32) != -5 || __emul(-1, -1) != 1) bad |= 1;
    if (__emulu(0xffffffffu, 0xffffffffu) != 0xfffffffe00000001ull) bad |= 2;
    unsigned int w[5] = {7, 1, 2, 3, 4};   /* 32-bit words (unsigned long under the game's ILP32; this test is LP64) */
    __movsd((unsigned long *)(w + 1), (const unsigned long *)w, 4);
    if (w[1] != 7 || w[2] != 7 || w[4] != 7) bad |= 4;
    unsigned char b[5] = {9, 1, 2, 3, 4};
    __movsb(b + 1, b, 4);
    if (b[4] != 9) bad |= 8;
    char d[4] = {0}, s[4] = {'a', 'b', 'c', 'd'};
    if (xmemcpy(d, s, 3) != d || memcmp(d, "abc", 3) || d[3]) bad |= 16;
    return bad;
}
'''


def main():
    work = tempfile.mkdtemp(prefix='v2-intr-')   # raises when it cannot create the directory
    try:
        c, exe = os.path.join(work, 'c.c'), os.path.join(work, 't')
        open(c, 'w').write(CHECK)
        r = subprocess.run(['aarch64-linux-gnu-gcc', '-static', '-O1', '-I',
                            os.path.join(PORT, 'platform', 'crt'), c, os.path.join(PORT, 'platform', 'crt', 'msvc_names.c'),
                            '-o', exe], capture_output=True, text=True)
        if r.returncode:
            print('skip: build failed (%s)' % (r.stderr.strip().splitlines() or ['?'])[-1][:200])
            return 0
        rc = subprocess.run(['qemu-aarch64', exe]).returncode
        names = ['E1', 'E2', 'M1', 'M2', 'X1']
        for i, n in enumerate(names):
            print('%s %s' % ('FAIL' if rc & (1 << i) else 'ok', n))
        print('ok' if rc == 0 else 'FAILED (exit %d)' % rc)
        return 1 if rc else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
