#!/usr/bin/env python3
"""A bool read from a char/int result (S2 s2d-010: 1,343 references to 205 definitions): abi.py and abi2.py give
the pair a bridge that normalises the result to (value != 0), and the emitted bridge runs right (system
aarch64-linux-gnu gcc, static, qemu-aarch64). A definition returning char 2 gives a bool holding 2 through a plain
bool-typed call (neither false nor true: GCC's bit tests read it as false), 1 through the bridge. Our own code only."""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import abi  # noqa: E402
import abi2  # noqa: E402
import fnvlink  # noqa: E402
import sigs  # noqa: E402

fails = 0


def ok(name, cond, info=''):
    global fails
    print(('ok   ' if cond else 'FAIL ') + name + ('' if cond else ': ' + str(info)[:600]))
    fails += not cond


DRIVER = r'''
#include <stdbool.h>
#include <stdio.h>
__attribute__((noinline)) char def_char(int v) { return (char)v; }     /* the definition: char */
__attribute__((noinline)) int def_int(int v) { return v; }
bool br_char(int v);                                                    /* the bridges (asm) */
bool br_int(int v);
typedef bool (*bool_fn)(int);
int main(void) {
    volatile bool_fn raw = (bool_fn)def_char;                           /* what an unbridged call does */
    int plain = raw(2), b2 = br_char(2), b0 = br_char(0), bi = br_int(0x100), bz = br_int(0);
    printf("plain=%d bridged2=%d bridged0=%d int256=%d int0=%d\n", plain, b2, b0, bi, bz);
    return !(b2 == 1 && b0 == 0 && bi == 1 && bz == 0);
}
'''


def main():
    names = ['?f@@YA_NH@Z', '?f@@YADH@Z', '?f@@YAHH@Z']
    d = fnvlink.batch_demangle(fnvlink.UNDNAME, names)
    S = {n: sigs.parse_msvc(n, d[n]) for n in names}
    v = abi.compare(S['?f@@YA_NH@Z'], S['?f@@YADH@Z'])
    ok('abi: bool ref -> char def is a bridge', v.kind == 'bridge' and 'ret-bool-from-int' in v.classes, (v.kind, v.classes))
    p = abi2.compare(S['?f@@YA_NH@Z'], S['?f@@YADH@Z'], lambda t: None, lambda t: None)
    ok('abi2: the same pair is a bridge', p is not None and p.kind == 'bridge' and 'ret-bool-from-int' in p.classes,
       p and (p.kind, p.classes))
    ok('char ref -> bool def stays same', abi.compare(S['?f@@YADH@Z'], S['?f@@YA_NH@Z']).kind == 'same')
    vi = abi.compare(S['?f@@YA_NH@Z'], S['?f@@YAHH@Z'])
    cc = shutil.which('aarch64-linux-gnu-gcc')
    qemu = shutil.which('qemu-aarch64') or shutil.which('qemu-aarch64-static')
    if not cc or not qemu:
        print('SKIP run: aarch64-linux-gnu-gcc or qemu-aarch64 missing')
    else:
        asm = '    .text\n' + abi.emit_bridge('br_char', 'def_char', v, 'bool <- char') + \
            abi2.emit('br_int', 'def_int', abi2.compare(S['?f@@YA_NH@Z'], S['?f@@YAHH@Z'], lambda t: None,
                                                          lambda t: None), 'bool <- int') + '\n'
        ok('abi: int def gets the 32-bit test', vi.ret_fix == ('ops', ('cmp w0, #0', 'cset w0, ne')), vi.ret_fix)
        with tempfile.TemporaryDirectory() as t:
            open(os.path.join(t, 'br.S'), 'w').write(asm)
            open(os.path.join(t, 'd.c'), 'w').write(DRIVER)
            exe = os.path.join(t, 'd')
            r = subprocess.run([cc, '-O2', '-static', '-o', exe, os.path.join(t, 'd.c'), os.path.join(t, 'br.S')],
                               capture_output=True, text=True)
            ok('bridges assemble and link', r.returncode == 0, r.stderr + asm)
            if r.returncode == 0:
                r = subprocess.run([qemu, exe], capture_output=True, text=True, timeout=60)
                ok('bridged: char 2 -> true, 0 -> false; int 0x100 -> true', r.returncode == 0, r.stdout + r.stderr)
                # without the bridge the bool holds 2: neither false nor true, which GCC's bit tests (tbz w0, #0)
                # and stores of a bool read as false
                ok('unbridged: char 2 read as bool is not 0/1 (the bug)', 'plain=0 ' not in r.stdout and
                   'plain=1 ' not in r.stdout, r.stdout)
    print('test_retbool: %d failed' % fails)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
