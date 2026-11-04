#!/usr/bin/env python3
"""tests/s12/order/run.py - MSVC x86 memory ordering on AArch64 (S12-ordering, docs/ORDERING.md).

    tests/s12/order/run.py <scratch dir>

1. units.cpp: the fnvnx_order plugin and fnvnx_compat.h's barriers give the expected AArch64 ILP32 code with the
   build's flags (tools/link/compile.sh), in the modes volatile (the build), keys, tso and off.
2. args.cpp: call arguments are evaluated right to left, as MSVC x86 does (args=rtl), ILP32 under qemu; the expected
   order (args.expect) is checked against GCC x86-32, which pushes too, when the host has gcc -m32.
3. litmus.cpp: multicore message-passing litmus tests with volatile flags (a reused result slot, a polled queue, a
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


def refcount_codegen(scratch, plugin):
    """refcount.cpp: per class of Interlocked* site, the code fnvnx_order and the inline Interlocked* give"""
    src = os.path.join(HERE, 'refcount.cpp')
    sse = os.environ.get('SSE2NEON', os.path.join(PORT, 'ext', 'sse2neon'))
    exp = expectations(src)
    check(len(exp) > 30, 'refcount: %d expectations read from refcount.cpp' % len(exp))
    for mode, extra in (('volatile', []), ('full', ['-fplugin-arg-fnvnx_order-refcount=full'])):
        asm = os.path.join(scratch, 'refcount_%s.s' % mode)
        cmd = (['aarch64-linux-gnu-g++'] + BUILD + ['-include', os.path.join(PORT, 'shim', 'include', 'fnvnx_compat.h'),
               '-I' + os.path.join(PORT, 'shim', 'include'), '-I' + sse, '-fplugin=' + plugin,
               '-fplugin-arg-fnvnx_order-verbose'] + extra + ['-w', '-S', '-o', asm, src])
        r = subprocess.run(cmd, capture_output=True, text=True)
        check(r.returncode == 0, 'refcount %s: refcount.cpp compiles (%s)' % (mode, r.stderr.strip()[-300:]))
        if r.returncode:
            continue
        m = re.search(r'(\d+) refcount increments relaxed, (\d+) refcount decrements release, (\d+) full', r.stderr)
        got = tuple(int(x) for x in m.groups()) if m else None
        want = (1, 3, 7) if mode == 'volatile' else (0, 0, 11)
        check(got == want, 'refcount %s: classes inc-relaxed/dec-release/full = %s (want %s)' % (mode, got, want))
        funcs = functions(open(asm).read())
        for fn, m_, kind, rx in exp:
            if m_ != mode:
                continue
            body = None
            for k, v in funcs.items():
                if re.match(r'_Z(N\d+NiRefObject)?%d%s' % (len(fn), re.escape(fn)), k):
                    body = v
            if body is None:
                check(False, 'refcount %s: %s not found' % (mode, fn))
                continue
            hit = re.search(rx, body) is not None
            check(hit == (kind == 'expect'), 'refcount %s: %s %s /%s/' % (mode, fn, kind, rx))


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


def wholeprog(scratch, plugin):
    """wholeprog_a.cpp / wholeprog_b.cpp: the whole-program key list (keys.sh, discover=). Unit A's syntax-only
    discovery finds its Interlocked operands (also through forwarders) and plain polls; unit B, which does neither,
    gets ordered stores of them only from that list (ATOMIC-PLAIN-STORE, the writer side of POLL-PLAIN)"""
    sse = os.environ.get('SSE2NEON', os.path.join(PORT, 'ext', 'sse2neon'))
    inc = ['-include', os.path.join(PORT, 'shim', 'include', 'fnvnx_compat.h'), '-I' + os.path.join(PORT, 'shim', 'include'),
           '-I' + sse, '-I' + HERE, '-fplugin=' + plugin, '-w']
    a, b = os.path.join(HERE, 'wholeprog_a.cpp'), os.path.join(HERE, 'wholeprog_b.cpp')
    keys = os.path.join(scratch, 'wholeprog.keys')
    if os.path.exists(keys):
        os.unlink(keys)
    r = subprocess.run(['aarch64-linux-gnu-g++'] + [f for f in BUILD if f != '-O2'] + inc +
                       ['-fsyntax-only', '-fplugin-arg-fnvnx_order-discover=' + keys, a], capture_output=True, text=True)
    check(r.returncode == 0 and os.path.exists(keys), 'wholeprog: syntax-only discovery of unit A (%s)' % r.stderr[-300:])
    if r.returncode or not os.path.exists(keys):
        return
    got = open(keys).read().splitlines()
    want = sorted(l.split('discover:', 1)[1].strip() for l in open(a) if '// discover:' in l)
    check(got == want, 'wholeprog: discovered keys of unit A = %s (want %s)' % (got, want))
    a_asm = os.path.join(scratch, 'wholeprog_a.s')
    r = subprocess.run(['aarch64-linux-gnu-g++'] + BUILD + inc + ['-S', '-o', a_asm, a], capture_output=True, text=True)
    check(r.returncode == 0, 'wholeprog: unit A compiles (%s)' % r.stderr[-300:])
    jobs = [('volatile', a, a_asm)]
    for mode, extra in (('keys', ['-fplugin-arg-fnvnx_order-keys=' + keys]), ('nokeys', [])):
        asm = os.path.join(scratch, 'wholeprog_b_%s.s' % mode)
        r = subprocess.run(['aarch64-linux-gnu-g++'] + BUILD + inc + extra + ['-S', '-o', asm, b], capture_output=True,
                           text=True)
        check(r.returncode == 0, 'wholeprog: unit B compiles, %s (%s)' % (mode, r.stderr[-300:]))
        jobs.append((mode, b, asm))
    for mode, src, asm in jobs:
        if not os.path.exists(asm):
            continue
        funcs = functions(open(asm).read())
        exp = [e for e in expectations(src) if e[1] == mode]
        check(len(exp) >= 4, 'wholeprog %s: %d expectations in %s' % (mode, len(exp), os.path.basename(src)))
        for fn, _, kind, rx in exp:
            body = body_of(funcs, fn)
            if body is None:
                check(False, 'wholeprog %s: %s not found' % (mode, fn))
                continue
            check((re.search(rx, body) is not None) == (kind == 'expect'), 'wholeprog %s: %s %s /%s/' % (mode, fn, kind, rx))


def compile_sh_keys(scratch):
    """tools/link/compile.sh keeps the whole-program key list itself: a two-unit tree, compiled as a one-unit shard
    (only unit B), still gives B the keys of unit A; ORDER_KEYS=unit does not; a second run rebuilds nothing"""
    if not shutil.which('jq'):
        print('skip compile.sh keys: needs jq')
        return
    import json
    tree = os.path.join(scratch, 'cs_tree')
    out = os.path.join(scratch, 'cs_obj')
    shutil.rmtree(tree, ignore_errors=True)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(os.path.join(tree, 'build'))
    os.makedirs(os.path.join(tree, 'include'))
    for f in ('wholeprog_a.cpp', 'wholeprog_b.cpp'):
        shutil.copy(os.path.join(HERE, f), os.path.join(tree, f))
    shutil.copy(os.path.join(HERE, 'wholeprog.h'), os.path.join(tree, 'include', 'wholeprog.h'))
    json.dump({'units': [{'source': 'wholeprog_a.cpp'}, {'source': 'wholeprog_b.cpp'}]},
              open(os.path.join(tree, 'build', 'units.json'), 'w'))
    shard = os.path.join(scratch, 'cs_shard.list')
    open(shard, 'w').write('wholeprog_b.cpp\n')
    env = dict(os.environ)
    env.setdefault('SSE2NEON', os.path.join(PORT, 'ext', 'sse2neon'))
    env.pop('ORDER_KEYS', None)
    cs = os.path.join(PORT, 'tools', 'link', 'compile.sh')

    def run(o, extra=None):
        e = dict(env, **(extra or {}))
        return subprocess.run(['bash', cs, tree, o, shard, '2'], capture_output=True, text=True, env=e)

    def disasm(o):
        obj = os.path.join(o, 'wholeprog_b.cpp.o')
        if not os.path.exists(obj):
            return ''
        return subprocess.run(['aarch64-linux-gnu-objdump', '-d', '--no-show-raw-insn', obj], capture_output=True,
                              text=True).stdout

    r = run(out)
    check(r.returncode == 0 and '1 ok, 0 failed' in r.stdout, 'compile.sh keys: shard of unit B compiles (%s)'
          % (r.stdout + r.stderr)[-300:])
    keys = os.path.join(out, 'order.keys')
    want = sorted(l.split('discover:', 1)[1].strip() for l in open(os.path.join(HERE, 'wholeprog_a.cpp'))
                  if '// discover:' in l)
    got = open(keys).read().splitlines() if os.path.exists(keys) else None
    check(got == want, 'compile.sh keys: <obj dir>/order.keys covers the whole tree, not the shard (%s)' % got)
    d = disasm(out)
    m = re.search(r'<_Z6unlockP3Obj>:\n(.*?)\n\n', d, re.S)
    check(bool(m) and '\tstlr\t' in m.group(1), 'compile.sh keys: unit B\'s unlock() stores with stlr')
    mt = os.path.getmtime(os.path.join(out, 'wholeprog_b.cpp.o'))
    r = run(out)
    check(r.returncode == 0 and os.path.getmtime(os.path.join(out, 'wholeprog_b.cpp.o')) == mt and
          os.path.getmtime(keys) <= mt, 'compile.sh keys: a second run keeps the object and the key list')
    r = run(out + '_unit', {'ORDER_KEYS': 'unit'})
    m = re.search(r'<_Z6unlockP3Obj>:\n(.*?)\n\n', disasm(out + '_unit'), re.S)
    check(r.returncode == 0 and bool(m) and '\tstlr\t' not in m.group(1),
          'compile.sh keys: ORDER_KEYS=unit leaves unlock() plain (the default bites)')
    r = run(out + '_missing', {'ORDER_KEYS': os.path.join(scratch, 'no-such.keys')})
    check(r.returncode == 2, 'compile.sh keys: a missing ORDER_KEYS file is an error, not a silent fallback')


X86_RT = r'''
void v1_puts(const char *s) { int n = 0; while (s[n]) n++; int r;
    __asm__ volatile("int $0x80" : "=a"(r) : "a"(4), "b"(1), "c"(s), "d"(n) : "memory"); }
int main(void);
void _start(void) { int r = main(); __asm__ volatile("int $0x80" :: "a"(1), "b"(r)); for (;;) {} }
'''


def args_order(scratch, plugin):
    expect = open(os.path.join(HERE, 'args.expect')).read()
    src = os.path.join(HERE, 'args.cpp')
    # oracle: GCC x86-32 evaluates arguments right to left like MSVC (freestanding, Linux int 0x80)
    rt = os.path.join(scratch, 'x86rt.c')
    open(rt, 'w').write(X86_RT)
    exe86 = os.path.join(scratch, 'args86')
    common = ['-m32', '-O2', '-ffreestanding', '-fno-pic', '-fno-stack-protector']
    ok86 = (subprocess.run(['gcc'] + common + ['-c', rt, '-o', rt + '.o'], capture_output=True).returncode == 0 and
            subprocess.run(['g++'] + common + ['-std=gnu++17', '-fno-exceptions', '-fno-rtti', '-fno-threadsafe-statics',
                                               '-c', src, '-o', exe86 + '.o'], capture_output=True).returncode == 0 and
            subprocess.run(['ld', '-m', 'elf_i386', '-static', '-e', '_start', '-o', exe86, rt + '.o', exe86 + '.o'],
                           capture_output=True).returncode == 0)
    if ok86:
        out86 = subprocess.run([exe86], capture_output=True, text=True).stdout
        check(out86 == expect, 'args: GCC x86-32 (right to left) gives args.expect (%r)' % out86[:200])
    else:
        print('skip args x86-32 oracle: no gcc -m32')
    if not shutil.which('qemu-aarch64'):
        print('skip args: needs qemu-aarch64')
        return
    import qemu_ilp32 as q
    base = list(q.CXXFLAGS)
    for tag, extra, want in (('rtl', ['-fplugin=' + plugin], True),
                             ('ltr', ['-fplugin=' + plugin, '-fplugin-arg-fnvnx_order-args=ltr'], False)):
        q.CXXFLAGS = base + ['-fms-extensions', '-fno-strict-aliasing', '-fwrapv', '-mno-outline-atomics'] + extra
        try:
            exe = q.build([src], os.path.join(scratch, 'args_' + tag))
        except RuntimeError as e:
            check(False, 'args %s builds (%s)' % (tag, str(e)[-300:]))
            continue
        finally:
            q.CXXFLAGS = base
        rc, out = q.run(exe)
        if want:
            bad = [l for l, e in zip(out.splitlines(), expect.splitlines()) if l != e]
            check(rc == 0 and out == expect, 'args ILP32 with the plugin: MSVC order in all %d cases (%s)'
                  % (len(expect.splitlines()), bad[:3]))
        else:
            check(out != expect, 'args ILP32 with args=ltr: GCC\'s own order differs (the test bites)')


def litmus(scratch, plugin):
    if not shutil.which('qemu-aarch64'):
        print('skip litmus: needs qemu-aarch64')
        return
    import qemu_ilp32 as q
    base = list(q.CXXFLAGS)
    flags = ['-fms-extensions', '-fno-strict-aliasing', '-fwrapv', '-mno-outline-atomics', '-mtune=cortex-a57']
    res = {}
    for tag, extra in (('plain', []), ('order', ['-fplugin=' + plugin]),
                       ('order-full', ['-fplugin=' + plugin, '-fplugin-arg-fnvnx_order-refcount=full'])):
        q.CXXFLAGS = base + flags + ['-I' + os.path.join(PORT, 'shim', 'include')] + extra
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
        check(rc == 0 and len(rows) == 4, 'litmus %s runs (rc %d, %r)' % (tag, rc, out[-200:]))
        res[tag] = {k: int(v) for k, v in rows.items()}
        print('     litmus %s: %s' % (tag, out.strip().replace('\n', '; ')))
        if tag == 'order':
            dis = subprocess.run(['aarch64-linux-gnu-objdump', '-d', os.path.join(scratch, 'litmus_order.d',
                                  'prog32.elf')], capture_output=True, text=True).stdout
            for fn, need in (('mp1_publish', ['stlr']), ('mp1_consume', ['stlr', 'ldar']), ('mp2_take', ['ldar', 'stlr']),
                             ('lock_add', ['stlr', 'ld[a]?xr']), ('ref_inc', ['ldxr', 'stxr']),
                             ('ref_dec', ['ldxr', 'stlxr', 'dmb'])):
                m = re.search(r'<_Z\d+%s[^>]*>:\n(.*?)\n\n' % fn, dis, re.S)
                body = m.group(1) if m else ''
                check(all(re.search(r'\t%s\t' % n, body) for n in need), 'litmus order: %s has %s' % (fn, need))
    for tag in ('order', 'order-full'):
        if tag in res:
            check(all(v == 0 for v in res[tag].values()), 'litmus %s: no bad outcome %s' % (tag, res[tag]))
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
    refcount_codegen(scratch, plugin)
    wholeprog(scratch, plugin)
    compile_sh_keys(scratch)
    args_order(scratch, plugin)
    litmus(scratch, plugin)
    print('%d failed' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
