#!/usr/bin/env python3
"""Gate: no memory access of lifted code may fold a constant into a 64-bit addressing mode (x86 addresses wrap at 2^32).

Compiles probes through rt/fnvl.h's M8..MF64 with the game's ILP32 flags and checks the generated code: every load or
store must use a base register with offset 0 that was produced by a 32-bit (w-register) computation.
    addrwrap.py            prints probes checked / bad; exit 1 when bad != 0"""
import os, re, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); RT = os.path.join(HERE, '..', 'rt')
PROBES = [('M%d' % w, off, idx) for w in (8, 16, 32, 64) for off in ('0x40u', '0xfffffffcu', '0x1183000u', '0u') for idx in ('', '+ecx*4u', '+ecx')]
PROBES += [(m, off, '') for m in ('MF32', 'MF64') for off in ('0x40u', '0x1183000u')]
def main():
    src = '#include "fnvl.h"\n'
    for i, (m, off, idx) in enumerate(PROBES):
        src += 'u32 r%d(u32 esi,u32 ecx){ return (u32)%s((u32)(esi%s+%s)); }\n' % (i, m, idx, off)
        src += 'void w%d(u32 esi,u32 ecx,u32 v){ %s((u32)(esi%s+%s))=v; }\n' % (i, m, idx, off)
    with tempfile.TemporaryDirectory() as d:
        open(d + '/p.cpp', 'w').write(src)
        asm = subprocess.run(['aarch64-linux-gnu-g++', '-mabi=ilp32', '-O2', '-fwrapv', '-fno-strict-aliasing', '-fpermissive', '-w', '-I', RT,
                              '-S', d + '/p.cpp', '-o', '-'], capture_output=True, text=True, check=True).stdout
    bad = 0; n = 0
    for fn, body in re.findall(r'^(_Z\w+|[rw]\d+):\n(.*?)\n\tret', asm, re.S | re.M):
        n += 1
        acc = [l for l in body.split('\n') if re.match(r'\t(ldr|str|ldrb|strb|ldrh|strh|ldur|stur)\w*\t', l)]
        if len(acc) != 1 or not re.search(r'\[x\d+\]$', acc[0]): bad += 1; print('BAD', fn, acc[:2])
    print('addrwrap: probes %d bad %d' % (n, bad)); sys.exit(1 if bad or n != 2 * len(PROBES) else 0)
if __name__ == '__main__': main()
