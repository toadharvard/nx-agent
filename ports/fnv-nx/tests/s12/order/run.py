#!/usr/bin/env python3
"""tests/s12/order/run.py - MSVC x86 memory ordering on AArch64 (S12-ordering, docs/ORDERING.md).

    tests/s12/order/run.py <scratch dir>

1. units.cpp: the fnvnx_order plugin and fnvnx_compat.h's barriers give the expected AArch64 ILP32 code with the
   build's flags (tools/link/compile.sh), in the modes volatile (the build), keys, tso and off.
2. litmus.cpp: multicore message-passing litmus tests with volatile flags (a reused result slot, a polled queue, a
   spin lock), freestanding ILP32 under qemu-aarch64 with 4 host threads. With the plugin no test may show a bad
   outcome; without it mp2 must (the test bites).

qemu-aarch64 on an x86-64 host runs the guest threads in parallel, but on TSO hardware: what the litmus test sees
is the compiler's reordering, not the A57's. The hardware half is item 1 - every ordered access is ldar/stlr or
fenced, the standard C11 -> ARMv8 mapping, which the Arm memory model proves sufficient for message passing.
Needs aarch64-linux-gnu-g++, gcc-13-plugin-dev-aarch64-linux-gnu, g++ and qemu-aarch64; SSE2NEON (default
ext/sse2neon) for fnvnx_compat.h.
"""
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, os.path.join(PORT, 'tools', 'verify', 'v1_link'))
FAIL = []
# the semantics flags of tools/link/compile.sh (minus -c / -fPIC / paths), for the code generation checks
BUILD = ['-mabi=ilp32', '-std=gnu++17', '-O2', '-fms-extensions', '-fshort-wchar', '-frounding-math', '-fsigned-char',
         '-fno-strict-aliasing', '-fwrapv', '-fno-delete-null-pointer-checks', '-ffp-contract=off', '-fPIC',
         '-fpermissive', '-Wno-narrowing', '-march=armv8-a+crc+crypto', '-mtune=cortex-a57', '-fno-stack-protector',
         '-fno-stack-clash-protection', '-mno-outline-atomics']


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        FAIL.append(what)


def functions(asm):
    out, cur = {}, None
    for line in asm.splitlines():
        m = re.match(r'^([A-Za-z_][\w.$]*):', line)
        if m and not m.group(1).startswith('.L'):
            cur = m.group(1)
            out[cur] = []
        elif cur and (line.startswith('\t') and not line.startswith('\t.') or line.startswith('.L')):
            out[cur].append(line)
    return {k: '\n'.join(v) + '\n' for k, v in out.items()}


def body_of(funcs, name):
    for k, v in funcs.items():
        if re.match(r'_Z%d%s' % (len(name), re.escape(name)), k):
            return v
    return None


def expectations(src):
    """[(function, mode, kind, regex)] from the comment lines before each definition"""
    pend, out = [], []
    for line in open(src):
        m = re.match(r'\s*//\s*(?:(\w+)-)?(expect|reject):\s?(.*)$', line)
        if m:
            pend.append((m.group(1) or 'volatile', m.group(2), m.group(3)))
            continue
        d = re.match(r'^[\w\s\*&:<>]*?\b(\w+)\s*\([^;]*\)\s*\{', line)
        if d and pend:
            out += [(d.group(1), mode, kind, rx) for mode, kind, rx in pend]
            pend = []
    return out


def codegen(scratch, plugin):
    src = os.path.join(HERE, 'units.cpp')
    sse = os.environ.get('SSE2NEON', os.path.join(PORT, 'ext', 'sse2neon'))
    if not os.path.isfile(os.path.join(sse, 'sse2neon.h')):
        check(False, 'codegen: sse2neon.h not found (set SSE2NEON)')
        return
    keys = os.path.join(scratch, 'order.keys')
    with open(keys, 'w') as f:
        f.write('field ns::Q::plain\nvar g_unused\n')
    modes = {'volatile': [], 'keys': ['-fplugin-arg-fnvnx_order-keys=' + keys],
             'tso': ['-fplugin-arg-fnvnx_order-mode=tso'], 'off': ['-fplugin-arg-fnvnx_order-mode=off']}
    exp = expectations(src)
    check(len(exp) > 30, 'codegen: %d expectations read from units.cpp' % len(exp))
    for mode, extra in modes.items():
        asm = os.path.join(scratch, 'units_%s.s' % mode)
        cmd = (['aarch64-linux-gnu-g++'] + BUILD + ['-include', os.path.join(PORT, 'shim', 'include', 'fnvnx_compat.h'),
               '-I' + os.path.join(PORT, 'shim', 'include'), '-I' + sse, '-fplugin=' + plugin] + extra +
               ['-w', '-S', '-o', asm, src])
        r = subprocess.run(cmd, capture_output=True, text=True)
        check(r.returncode == 0, 'codegen %s: units.cpp compiles (%s)' % (mode, r.stderr.strip()[-300:]))
        if r.returncode:
            continue
        funcs = functions(open(asm).read())
        for fn, m, kind, rx in exp:
            if m != mode:
                continue
            body = body_of(funcs, fn)
            if body is None:
                check(False, 'codegen %s: %s not found' % (mode, fn))
                continue
            hit = re.search(rx, body) is not None
            check(hit == (kind == 'expect'), 'codegen %s: %s %s /%s/' % (mode, fn, kind, rx))


def litmus(scratch, plugin):
    if not shutil.which('qemu-aarch64'):
        print('skip litmus: needs qemu-aarch64')
        return
    import qemu_ilp32 as q
    base = list(q.CXXFLAGS)
    flags = ['-fms-extensions', '-fno-strict-aliasing', '-fwrapv', '-mno-outline-atomics', '-mtune=cortex-a57']
    res = {}
    for tag, extra in (('plain', []), ('order', ['-fplugin=' + plugin])):
        q.CXXFLAGS = base + flags + extra
        try:
            exe = q.build([os.path.join(HERE, 'litmus.cpp'), os.path.join(HERE, 'litmus_rt.S')],
                          os.path.join(scratch, 'litmus_' + tag))
        except RuntimeError as e:
            check(False, 'litmus %s builds (%s)' % (tag, str(e)[-300:]))
            continue
        finally:
            q.CXXFLAGS = base
        rc, out = q.run(exe, timeout=300)
        rows = dict(re.findall(r'^(\w+) rounds=\d+ bad=(\d+)$', out, re.M))
        check(rc == 0 and len(rows) == 3, 'litmus %s runs (rc %d, %r)' % (tag, rc, out[-200:]))
        res[tag] = {k: int(v) for k, v in rows.items()}
        print('     litmus %s: %s' % (tag, out.strip().replace('\n', '; ')))
        if tag == 'order':
            dis = subprocess.run(['aarch64-linux-gnu-objdump', '-d', os.path.join(scratch, 'litmus_order.d',
                                  'prog32.elf')], capture_output=True, text=True).stdout
            for fn, need in (('mp1_publish', ['stlr']), ('mp1_consume', ['stlr', 'ldar']), ('mp2_take', ['ldar', 'stlr']),
                             ('lock_add', ['stlr', 'ld[a]?xr'])):
                m = re.search(r'<_Z\d+%s[^>]*>:\n(.*?)\n\n' % fn, dis, re.S)
                body = m.group(1) if m else ''
                check(all(re.search(r'\t%s\t' % n, body) for n in need), 'litmus order: %s has %s' % (fn, need))
    if 'order' in res:
        check(all(v == 0 for v in res['order'].values()), 'litmus with the plugin: no bad outcome %s' % res['order'])
    if 'plain' in res:
        check(res['plain'].get('mp2', 0) > 0, 'litmus without the plugin: mp2 shows the stale read (the test bites)')


def main():
    scratch = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else '/tmp/s12_order')
    os.makedirs(scratch, exist_ok=True)
    r = subprocess.run(['bash', os.path.join(PORT, 'toolchain', 'order', 'build.sh'), os.path.join(scratch, 'plugin')],
                       capture_output=True, text=True)
    check(r.returncode == 0, 'fnvnx_order plugin builds (%s)' % r.stderr.strip()[-300:])
    if r.returncode:
        return 1
    plugin = r.stdout.strip()
    codegen(scratch, plugin)
    litmus(scratch, plugin)
    print('%d failed' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
