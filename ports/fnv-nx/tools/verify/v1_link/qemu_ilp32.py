#!/usr/bin/env python3
"""qemu_ilp32.py - run freestanding AArch64 ILP32 test programs under qemu-aarch64 (V1-link harness).

qemu-aarch64 only loads ELF64 images and puts the stack above 4 GB, where ILP32 code cannot address it. So the
program is linked as an ILP32 static image at 0x10000000 (one RWX segment), flattened, and wrapped in a minimal ELF64
header written here; the runtime (_start below) moves sp to a 1 MiB stack inside the image and pre-fills it with a
junk pattern (so bridges that read uninitialized frame bytes are caught), then calls main(); exit status = main().

    build(sources, out)          -> path of the ELF64 executable (sources: .c / .cpp / .S, compiled -mabi=ilp32)
    run(exe) -> (exit status, stdout)
"""
import os
import struct
import subprocess

CC = os.environ.get('V1_CC', 'aarch64-linux-gnu-gcc')
CXX = os.environ.get('V1_CXX', 'aarch64-linux-gnu-g++')
LD = os.environ.get('V1_LD', 'aarch64-linux-gnu-ld')
OBJCOPY = os.environ.get('V1_OBJCOPY', 'aarch64-linux-gnu-objcopy')
NM = os.environ.get('V1_NM', 'aarch64-linux-gnu-nm')
BASE = 0x10000000

RT_S = r'''
    .text
    .globl _start
    .type _start, %function
_start:
    adrp x0, __v1_stack_end
    add x0, x0, :lo12:__v1_stack_end
    adrp x1, __v1_stack
    add x1, x1, :lo12:__v1_stack
    movz x2, #0xbeef
    movk x2, #0xdead, lsl #16
    movk x2, #0xa5a5, lsl #32
    movk x2, #0x5a5a, lsl #48
1:  str x2, [x1], #8              // junk-fill the stack: bridges must not depend on zeroed frames
    cmp x1, x0
    b.lo 1b
    mov sp, x0
    fmov d16, x2                  // junk in the scratch SIMD registers as well
    bl main
    mov x8, #93
    svc 0
    .bss
    .p2align 4
__v1_stack:
    .space 1048576
__v1_stack_end:
'''

RT_C = r'''
typedef unsigned long size_t;
void *memcpy(void *d, const void *s, size_t n) { unsigned char *a = d; const unsigned char *b = s; while (n--) *a++ = *b++; return d; }
void *memmove(void *d, const void *s, size_t n) { unsigned char *a = d; const unsigned char *b = s;
    if (a < b) while (n--) *a++ = *b++; else { a += n; b += n; while (n--) *--a = *--b; } return d; }
void *memset(void *d, int c, size_t n) { unsigned char *a = d; while (n--) *a++ = (unsigned char)c; return d; }
int memcmp(const void *x, const void *y, size_t n) { const unsigned char *a = x, *b = y;
    for (; n; n--, a++, b++) if (*a != *b) return *a - *b; return 0; }
static long v1_sys(long n, long a, long b, long c) {
    register long x8 __asm__("x8") = n; register long x0 __asm__("x0") = a;
    register long x1 __asm__("x1") = b; register long x2 __asm__("x2") = c;
    __asm__ volatile("svc 0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2) : "memory");
    return x0;
}
void v1_write(const char *s, size_t n) { v1_sys(64, 1, (long)s, (long)n); }
void v1_puts(const char *s) { size_t n = 0; while (s[n]) n++; v1_write(s, n); }
void v1_hex(const void *p, size_t n) { static const char h[] = "0123456789abcdef"; char buf[2];
    const unsigned char *b = p; for (size_t i = 0; i < n; i++) { buf[0] = h[b[i] >> 4]; buf[1] = h[b[i] & 15]; v1_write(buf, 2); } }
void __cxa_pure_virtual(void) { v1_puts("pure virtual\n"); v1_sys(93, 99, 0, 0); }
void abort(void) { v1_sys(93, 98, 0, 0); for (;;) {} }
'''

LDS = '''
ENTRY(_start)
SECTIONS {
  . = 0x%x;
  .all : { *(.text .text.*) *(.rodata .rodata.*) *(.data .data.*) *(.eh_frame*) *(.gcc_except_table*) }
  .bss (NOLOAD) : { *(.bss .bss.* COMMON) }
  /DISCARD/ : { *(.note*) *(.comment) }
}
''' % BASE

CFLAGS = ['-mabi=ilp32', '-O2', '-ffreestanding', '-fno-stack-protector', '-fno-exceptions', '-fno-asynchronous-unwind-tables',
          '-fno-unwind-tables', '-fno-pic', '-fno-pie']
CXXFLAGS = CFLAGS + ['-fno-rtti', '-std=gnu++17', '-fno-threadsafe-statics', '-fshort-wchar', '-fsigned-char']


def _run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        raise RuntimeError('%s failed:\n%s' % (' '.join(cmd[:3]), r.stderr[-4000:]))
    return r


def build(sources, out, extra_objs=()):
    work = out + '.d'
    os.makedirs(work, exist_ok=True)
    objs = []
    rt = [(os.path.join(work, 'rt.S'), RT_S), (os.path.join(work, 'rt.c'), RT_C)]
    for path, text in rt:
        with open(path, 'w') as f:
            f.write(text)
    for src in [p for p, _ in rt] + list(sources):
        o = os.path.join(work, os.path.basename(src) + '.o')
        if src.endswith('.cpp'):
            _run([CXX, *CXXFLAGS, '-c', src, '-o', o])
        elif src.endswith('.c'):
            _run([CC, *CFLAGS, '-c', src, '-o', o])
        else:
            _run([CC, '-mabi=ilp32', '-c', src, '-o', o])
        objs.append(o)
    lds = os.path.join(work, 'link.ld')
    with open(lds, 'w') as f:
        f.write(LDS)
    elf32 = os.path.join(work, 'prog32.elf')
    _run([LD, '-m', 'aarch64linux32', '-static', '-T', lds, '-o', elf32, *objs, *extra_objs])
    flat = os.path.join(work, 'prog.bin')
    _run([OBJCOPY, '-O', 'binary', '-j', '.all', elf32, flat])
    syms = {}
    for line in _run([NM, elf32]).stdout.splitlines():
        f = line.split()
        if len(f) == 3:
            syms[f[2]] = int(f[0], 16)
    image = open(flat, 'rb').read()
    memsz = syms['__v1_stack_end'] - BASE + 16 if '__v1_stack_end' in syms else len(image) + (2 << 20)
    memsz = max(memsz, len(image))
    hdr = 64 + 56
    off = 0x10000
    eh = struct.pack('<4sBBBBB7sHHIQQQIHHHHHH', b'\x7fELF', 2, 1, 1, 0, 0, b'\0' * 7, 2, 183, 1, syms['_start'], 64, 0,
                     0, 64, 56, 1, 64, 0, 0)
    ph = struct.pack('<IIQQQQQQ', 1, 7, off, BASE, BASE, len(image), memsz, 0x10000)
    with open(out, 'wb') as f:
        f.write(eh + ph + b'\0' * (off - hdr) + image)
    os.chmod(out, 0o755)
    return out


def run(exe, timeout=60):
    r = subprocess.run(['qemu-aarch64', exe], capture_output=True, timeout=timeout)
    return r.returncode, r.stdout.decode('latin-1')


if __name__ == '__main__':
    import sys
    import tempfile
    d = tempfile.mkdtemp(prefix='v1q_')
    src = os.path.join(d, 't.c')
    open(src, 'w').write('void v1_puts(const char*); int g = 5; int main(void) { v1_puts("ilp32 ok\\n"); '
                         'int x; return ((unsigned long)&x >> 32) == 0 && g == 5 ? 0 : 1; }\n')
    exe = build([src], os.path.join(d, 't'))
    print(run(exe))
    sys.exit(0)
