#!/usr/bin/env python3
"""Self-test of tools/link/call_cycles.py on two small static aarch64 links (system cross gcc): one where
CopyMemoryBlock forwards to memmove (no cycle), one where memmove calls back into CopyMemoryBlock through a helper
(a cycle). Our own code only."""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, 'call_cycles.py')
fails = 0

GOOD = r'''
typedef unsigned long size_t;
__attribute__((noinline)) void *memmove(void *d, const void *s, size_t n) { char *a = d; const char *b = s;
  if (a < b) while (n--) *a++ = *b++; else while (n--) a[n] = b[n]; return d; }
void *memcpy(void *d, const void *s, size_t n) { return memmove(d, s, n); }
__attribute__((noinline)) void *FUN_00401460(void *d, const void *s, unsigned n) { return memmove(d, s, n); }
extern void *__fnv_00401460(void *, const void *, unsigned) __attribute__((alias("FUN_00401460")));
void (*volatile slot)(void);
void _start(void) { char b[8]; FUN_00401460(b, b + 1, 4); memcpy(b, b + 2, 2); if (slot) slot(); for (;;); }
'''
BAD = r'''
typedef unsigned long size_t;
void *FUN_00401460(void *d, const void *s, unsigned n);
__attribute__((noinline)) static void *helper(void *d, const void *s, size_t n) { return FUN_00401460(d, s, n); }
__attribute__((noinline)) void *memmove(void *d, const void *s, size_t n) { if (n > 64) return helper(d, s, n);
  char *a = d; const char *b = s; while (n--) *a++ = *b++; return d; }
__attribute__((noinline)) void *FUN_00401460(void *d, const void *s, unsigned n) { return memmove(d, s, n); }
void _start(void) { char b[8]; FUN_00401460(b, b + 1, 4); for (;;); }
'''


def ok(name, cond, info=''):
    global fails
    print(('ok   ' if cond else 'FAIL ') + name + ('' if cond else ': ' + str(info)[:800]))
    fails += not cond


def build(t, name, src):
    c = os.path.join(t, name + '.c')
    open(c, 'w').write(src)
    e = os.path.join(t, name + '.elf')
    subprocess.run(['aarch64-linux-gnu-gcc', '-O2', '-fno-builtin', '-ffreestanding', '-nostdlib', '-static', '-o', e, c],
                   check=True)
    return e


def main():
    if not shutil.which('aarch64-linux-gnu-gcc') or not shutil.which('aarch64-linux-gnu-objdump'):
        print('SKIP: aarch64-linux-gnu gcc/objdump not installed')
        return 0
    with tempfile.TemporaryDirectory() as t:
        good, bad = build(t, 'good', GOOD), build(t, 'bad', BAD)
        r = subprocess.run([sys.executable, '-I', TOOL, good, '--json', os.path.join(t, 'g.json')], capture_output=True,
                           text=True)
        ok('forwarder to memmove: no cycle, exit 0', r.returncode == 0 and 'CYCLE' not in r.stdout, r.stdout + r.stderr)
        ok('the forwarder calls memmove', 'calls memmove' in r.stdout and 'FUN_00401460' in r.stdout, r.stdout)
        ok('the alias __fnv_00401460 is the same body', '__fnv_00401460 (FUN_00401460)' in r.stdout or
           'FUN_00401460 (__fnv_00401460)' in r.stdout, r.stdout)
        ok('absent roots skipped', '__fnv_00ec7230' not in r.stdout, r.stdout)
        r = subprocess.run([sys.executable, '-I', TOOL, bad], capture_output=True, text=True)
        ok('memmove back into CopyMemoryBlock: a cycle, exit 1', r.returncode == 1 and
           'CYCLE memmove -> helper -> FUN_00401460 -> memmove' in r.stdout, r.stdout + r.stderr)
        r = subprocess.run([sys.executable, '-I', TOOL, os.path.join(t, 'missing.elf')], capture_output=True, text=True)
        ok('missing ELF: exit 2 or no roots', r.returncode in (0, 2) and 'CYCLE' not in r.stdout, r.stdout)
    print('test_call_cycles: %d failed' % fails)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
