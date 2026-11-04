#!/usr/bin/env python3
"""Self-tests of the S15-perf tools (tools/perf): object census, flag diff, frame profiler attribution.

    python3 -I tests/s15/test_perf.py        (needs an AArch64 cross gcc: $CC or aarch64-linux-gnu-gcc; skips without)
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'perf'))
import objcensus  # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


PROBE = r'''
extern int g_ext;
int g_def;
extern void callee(int);
int probe(int *p) { char buf[64]; for (int i = 0; i < 64; i++) buf[i] = (char)p[i]; callee(buf[p[1] & 63]);
                    return g_ext + g_def; }
int leaf(int a) { return a * 3; }
'''


def test_census(cc, tmp):
    src = os.path.join(tmp, 'p.c')
    open(src, 'w').write(PROBE)
    objs = {}
    for v, flags in (('base', ['-fPIC', '-fstack-protector-strong']), ('noprot', ['-fPIC', '-fno-stack-protector'])):
        os.makedirs(os.path.join(tmp, v))
        o = os.path.join(tmp, v, 'p.o')
        subprocess.run([cc, '-mabi=ilp32', '-O2', '-c', src, '-o', o] + flags, check=True)
        objs[v] = o

    class A:
        disasm, only, funcs = True, None, None
    tot, rows, mix = objcensus.census([objs['base']], A)
    check(tot['protected_funcs'] == 1, 'one stack-protected function (probe), leaf unprotected: %d'
          % tot['protected_funcs'])
    check(tot['got_stack_guard'] >= 2, 'canary read through the GOT twice: %d' % tot['got_stack_guard'])
    check(tot['got_other'] >= 2, 'g_ext and g_def through the GOT: %d' % tot['got_other'])
    check(tot['call_stack_chk_fail'] == 1 and tot['calls'] >= 2, 'calls classified: %s' % dict(
        (k, v) for k, v in tot.items() if k.startswith('call')))
    check(mix['total'] == tot['text_insns'], 'instruction mix covers the text')
    import flagdiff
    b = flagdiff.load(objs['base'])
    n = flagdiff.load(objs['noprot'])
    check(b['probe']['insns'] > n['probe']['insns'] and b['leaf']['insns'] == n['leaf']['insns'],
          'flagdiff: the protector costs probe %d -> %d instructions, leaf unchanged'
          % (b['probe']['insns'], n['probe']['insns']))


def test_attribution(tmp):
    import frameprof
    m = os.path.join(tmp, 'g.map')
    open(m, 'w').write(' .text          0x0000000030001000      0x100 /x/obj/src_havok_physics_a.cpp.o\n'
                       ' .text.fnvnx_lock\n                0x0000000030001100       0x40 /x/obj/toolchain_guest_threads.c.o\n'
                       ' .text          0x0000000030001200      0x80 /x/obj/platform_win32_sync.cpp.o\n')
    secs = frameprof.parse_map(m)
    check(len(secs) == 3 and secs[1][0] == 0x30001100, 'link map: 3 text input sections (%d)' % len(secs))

    class E:
        relocatable, base, link_base = False, 0, 0
        sym_addrs = [0x30001000, 0x30001100, 0x30001200]
        sym_names = [['hk_fn', '__fnv_00d2d110'], ['fnvnx_lock'], ['InterlockedIncrement']]
    at = frameprof.Attributor(E, {0x00d2d110: 'src/havok/common/base/thread/x.cpp'}, secs)
    lab, orig, obj = at.func(0x30001010)
    check(orig == 0x00d2d110 and at.subsystem(lab, orig, obj) == 'havok', 'image function -> unit -> havok')
    lab, orig, obj = at.func(0x30001104)
    check(orig is None and at.subsystem(lab, orig, obj) == 'guest-runtime', 'native lock -> guest-runtime')
    lab, orig, obj = at.func(0x30001210)
    check(at.subsystem(lab, orig, obj) == 'platform-win32', 'Interlocked -> platform-win32')


def main():
    tmp = tempfile.mkdtemp(prefix='s15-')
    try:
        cc = os.environ.get('CC') or shutil.which('aarch64-linux-gnu-gcc')
        if cc:
            test_census(cc, tmp)
        else:
            print('skip census tests: no AArch64 cross gcc')
        test_attribution(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('%d failed' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
