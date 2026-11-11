#!/usr/bin/env python3
"""tests/s12/order/run.py - MSVC x86 memory ordering on AArch64 (S12-ordering, docs/ORDERING.md).

    tests/s12/order/run.py <scratch dir>

0. The options of the removed contract (mode, keys, discover, uninit, refcount) and any unknown option are errors.
1. units.cpp: the fnvnx_order plugin (R9 TSO-MAP, no memory option) and fnvnx_compat.h's barriers give the expected
   AArch64 ILP32 code with the build's flags (tools/link/compile.sh); tso_objdump: ldar/stlr in the object for a
   global, a volatile and a non-volatile field, none for an address-not-taken local.
2. args.cpp: call arguments are evaluated right to left, as MSVC x86 does (args=rtl), ILP32 under qemu; the expected
   order (args.expect) is checked against GCC x86-32, which pushes too, when the host has gcc -m32.
   evalorder.cpp: the object of a member call and the operands of overloaded operators (ORDER-OBJECT and
   ORDER-OPERATOR, still open): the defaults give evalorder.gcc.expect, object=last operator=rtl give
   evalorder.msvc-guess.expect, and the two differ. With the defaults the objects of evalorder.cpp and units.cpp are
   byte-identical to those without the two arguments, and object=last keeps the non-virtual calls direct.
3. litmus.cpp: multicore message-passing litmus tests with shared flags (a reused result slot, a polled queue, a
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
    """[(function, kind, regex)] from the comment lines before each definition"""
    pend, out = [], []
    for line in open(src):
        m = re.match(r'\s*//\s*(expect|reject):\s?(.*)$', line)
        if m:
            pend.append((m.group(1), m.group(2)))
            continue
        d = re.match(r'^[\w\s\*&:<>]*?\b(\w+)\s*\([^;]*\)\s*\{', line)
        if d and pend:
            out += [(d.group(1), kind, rx) for kind, rx in pend]
            pend = []
    return out


def codegen(scratch, plugin):
    """units.cpp with no plugin option: R9 TSO-MAP, the one memory rule"""
    src = os.path.join(HERE, 'units.cpp')
    sse = os.environ.get('SSE2NEON', os.path.join(PORT, 'ext', 'sse2neon'))
    if not os.path.isfile(os.path.join(sse, 'sse2neon.h')):
        check(False, 'codegen: sse2neon.h not found (set SSE2NEON)')
        return
    exp = expectations(src)
    check(len(exp) > 30, 'codegen: %d expectations read from units.cpp' % len(exp))
    asm = os.path.join(scratch, 'units.s')
    cmd = (['aarch64-linux-gnu-g++'] + BUILD + ['-include', os.path.join(PORT, 'shim', 'include', 'fnvnx_compat.h'),
           '-I' + os.path.join(PORT, 'shim', 'include'), '-I' + sse, '-fplugin=' + plugin, '-w', '-S', '-o', asm, src])
    r = subprocess.run(cmd, capture_output=True, text=True)
    check(r.returncode == 0, 'codegen: units.cpp compiles (%s)' % r.stderr.strip()[-300:])
    if r.returncode:
        return
    funcs = functions(open(asm).read())
    for fn, kind, rx in exp:
        body = body_of(funcs, fn)
        if body is None:
            check(False, 'codegen: %s not found' % fn)
            continue
        check((re.search(rx, body) is not None) == (kind == 'expect'), 'codegen: %s %s /%s/' % (fn, kind, rx))


def tso_objdump(scratch, plugin):
    """R9 TSO-MAP with no option: a global, a volatile and a non-volatile field through a pointer are ldar/stlr in the
    object (objdump -d); an address-not-taken local is not"""
    src = os.path.join(scratch, 'tso_map.cpp')
    open(src, 'w').write('''struct S { int plain; volatile int vol; };
int g_plain;
volatile int g_vol;
extern "C" int g_load() { return g_plain; }
extern "C" void g_store(int v) { g_plain = v; }
extern "C" int gv_load() { return g_vol; }
extern "C" void gv_store(int v) { g_vol = v; }
extern "C" int f_load(S *s) { return s->plain; }
extern "C" void f_store(S *s, int v) { s->plain = v; }
extern "C" int fv_load(S *s) { return s->vol; }
extern "C" void fv_store(S *s, int v) { s->vol = v; }
extern "C" int local_only(int n) { int a = n; int b = a * 3; return b + a; }
''')
    obj = os.path.join(scratch, 'tso_map.o')
    r = subprocess.run(['aarch64-linux-gnu-g++'] + BUILD + ['-fplugin=' + plugin, '-c', '-o', obj, src],
                       capture_output=True, text=True)
    check(r.returncode == 0, 'tso map: compiles with no plugin option (%s)' % r.stderr.strip()[-300:])
    if r.returncode:
        return
    dis = subprocess.run(['aarch64-linux-gnu-objdump', '-d', '--no-show-raw-insn', obj], capture_output=True, text=True).stdout
    for fn, need in (('g_load', 'ldar'), ('g_store', 'stlr'), ('gv_load', 'ldar'), ('gv_store', 'stlr'), ('f_load', 'ldar'),
                     ('f_store', 'stlr'), ('fv_load', 'ldar'), ('fv_store', 'stlr')):
        m = re.search(r'<%s>:\n(.*?)\n\n' % fn, dis, re.S)
        check(bool(m) and re.search(r'\t%s\t' % need, m.group(1)) is not None, 'tso map: %s is %s' % (fn, need))
    m = re.search(r'<local_only>:\n(.*?)(?:\n\n|\Z)', dis, re.S)
    check(bool(m) and not re.search(r'\t(ldar|stlr|dmb)', m.group(1)), 'tso map: an address-not-taken local stays plain')


# the options of the removed contract (volatile as the mark of shared memory, key lists, wait-loop polls, weak
# refcounts, uninitialized locals read as 0) do not exist: any of them is a hard error, as is any unknown option
REMOVED = ['mode=volatile', 'mode=tso', 'mode=off', 'keys=/dev/null', 'discover=/dev/null', 'uninit=zero', 'uninit=keep',
           'refcount=weak', 'refcount=full', 'nosuchoption']


def removed_options(scratch, plugin):
    src = os.path.join(scratch, 'removed.cpp')
    open(src, 'w').write('int f(int *p) { return *p; }\n')
    r = subprocess.run(['aarch64-linux-gnu-g++'] + BUILD + ['-fplugin=' + plugin, '-fsyntax-only', src], capture_output=True, text=True)
    check(r.returncode == 0, 'removed options: the compile without options succeeds (%s)' % r.stderr.strip()[-200:])
    for o in REMOVED:
        r = subprocess.run(['aarch64-linux-gnu-g++'] + BUILD + ['-fplugin=' + plugin, '-fplugin-arg-fnvnx_order-' + o,
                           '-fsyntax-only', src], capture_output=True, text=True)
        check(r.returncode != 0 and 'unknown argument' in r.stderr, 'removed options: %s is a hard error' % o)


def v2_shapes(scratch):
    """V2's call shapes outside S11's table (tests/v2/test_s12_order_shapes.py, MSVC SP1 /O2 and /Od recorded there),
    built with compile.sh's own flags: every counted case must agree"""
    t = os.path.join(PORT, 'tests', 'v2', 'test_s12_order_shapes.py')
    if not os.path.exists(t) or not shutil.which('qemu-aarch64'):
        print('skip v2 shapes: needs tests/v2/test_s12_order_shapes.py and qemu-aarch64')
        return
    r = subprocess.run([sys.executable, '-I', t], capture_output=True, text=True, timeout=900)
    bad = [l for l in r.stdout.splitlines() if l.startswith(('FAIL', 'DEFECT')) or ' DEFECT ' in l]
    check(r.returncode == 0 and not bad, "v2 shapes: compile.sh's order matches MSVC on V2's shapes (%s)"
          % (bad or r.stdout.strip().splitlines()[-1:]))


def msvc_table(scratch, plugin):
    """S11's recorded MSVC 2008 order (tests/s11/order/order.msvc, five configurations) against order.cpp built with
    compile.sh's ORDER_OBJECT / ORDER_OPERATOR / ORDER_ASSIGN defaults: every case MSVC agrees on must match"""
    src = os.path.join(PORT, 'tests', 's11', 'order', 'order.cpp')
    rec = os.path.join(PORT, 'tests', 's11', 'order', 'order.msvc')
    if not (os.path.exists(src) and os.path.exists(rec) and shutil.which('qemu-aarch64')):
        print('skip msvc table: needs tests/s11/order and qemu-aarch64')
        return
    cs = open(os.path.join(PORT, 'tools', 'link', 'compile.sh')).read()
    opts = {k: re.search(r'^%s=\$\{%s:-(\w+)\}' % (k, k), cs, re.M).group(1)
            for k in ('ORDER_OBJECT', 'ORDER_OPERATOR', 'ORDER_ASSIGN')}
    import json
    import qemu_ilp32 as q
    base = list(q.CXXFLAGS)
    q.CXXFLAGS = base + ['-fms-extensions', '-fno-strict-aliasing', '-fwrapv', '-mno-outline-atomics', '-fplugin=' + plugin,
                         '-fplugin-arg-fnvnx_order-object=' + opts['ORDER_OBJECT'],
                         '-fplugin-arg-fnvnx_order-operator=' + opts['ORDER_OPERATOR'],
                         '-fplugin-arg-fnvnx_order-assign=' + opts['ORDER_ASSIGN']]
    try:
        exe = q.build([src], os.path.join(scratch, 'msvc_table'))
    except RuntimeError as e:
        check(False, 'msvc table: order.cpp builds (%s)' % str(e)[-300:])
        return
    finally:
        q.CXXFLAGS = base
    rc, out = q.run(exe)
    got = dict(l.split(' ', 1) for l in out.splitlines() if ' ' in l)
    m = json.load(open(rec))['msvc']
    cases = sorted(next(iter(m.values()))['results'])
    agreed = [c for c in cases if len({m[k]['results'][c] for k in m}) == 1]
    bad = ['%s MSVC %s GCC %s' % (c, m[next(iter(m))]['results'][c], got.get(c, '?').strip()) for c in agreed
           if got.get(c, '').strip() != m[next(iter(m))]['results'][c]]
    check(rc == 0 and len(agreed) >= 20 and not bad, 'msvc table: compile.sh order (%s) matches MSVC on all %d agreed cases %s'
          % (' '.join('%s=%s' % kv for kv in sorted(opts.items())), len(agreed), bad))


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


MSVC_GUESS = ['-fplugin-arg-fnvnx_order-object=last', '-fplugin-arg-fnvnx_order-operator=rtl']
DEFAULTS = ['-fplugin-arg-fnvnx_order-object=first', '-fplugin-arg-fnvnx_order-operator=cpp17']
NONVIRTUAL = ('case_member', 'case_member2', 'case_retslot', 'case_shl', 'case_shr', 'case_index', 'case_assign',
              'case_addeq', 'case_subeq')


def eval_codegen(scratch, plugin):
    """object= / operator= at their defaults give the same objects as without them; object=last keeps direct calls"""
    sse = os.environ.get('SSE2NEON', os.path.join(PORT, 'ext', 'sse2neon'))
    units = (['-include', os.path.join(PORT, 'shim', 'include', 'fnvnx_compat.h'),
              '-I' + os.path.join(PORT, 'shim', 'include'), '-I' + sse])
    for name, pre in (('evalorder', ['-DFNVNX_FREESTANDING']), ('units', units)):
        src = os.path.join(HERE, name + '.cpp')
        objs = []
        for tag, extra in (('plain', []), ('defaults', DEFAULTS)):
            o = os.path.join(scratch, '%s_%s.o' % (name, tag))
            r = subprocess.run(['aarch64-linux-gnu-g++'] + BUILD + pre + ['-fplugin=' + plugin] + extra +
                               ['-w', '-c', '-o', o, src], capture_output=True, text=True)
            check(r.returncode == 0, 'evalorder codegen: %s.cpp compiles (%s) (%s)' % (name, tag, r.stderr.strip()[-300:]))
            objs.append(open(o, 'rb').read() if r.returncode == 0 else None)
        check(objs[0] is not None and objs[0] == objs[1],
              'evalorder codegen: %s.o with object=first operator=cpp17 is byte-identical to without them' % name)
    asm = os.path.join(scratch, 'evalorder_msvc-guess.s')
    r = subprocess.run(['aarch64-linux-gnu-g++'] + BUILD + ['-DFNVNX_FREESTANDING', '-fplugin=' + plugin] + MSVC_GUESS +
                       ['-w', '-S', '-o', asm, os.path.join(HERE, 'evalorder.cpp')], capture_output=True, text=True)
    check(r.returncode == 0, 'evalorder codegen: compiles with object=last operator=rtl (%s)' % r.stderr.strip()[-300:])
    if r.returncode:
        return
    funcs = functions(open(asm).read())
    for fn in NONVIRTUAL + ('case_virtual', 'case_virtual2'):
        body = body_of(funcs, fn)
        if body is None:
            check(False, 'evalorder codegen: %s not found' % fn)
        elif fn in NONVIRTUAL:
            check(not re.search(r'\tbl?r\t', body), 'evalorder object=last: %s keeps direct calls (no blr/br)' % fn)
        else:
            check(re.search(r'\tbl?r\t', body) is not None, 'evalorder object=last: %s still calls through the vtable' % fn)


def eval_order(scratch, plugin):
    """evalorder.cpp under qemu: the defaults give GCC's C++17 order, object=last operator=rtl the MSVC guess"""
    if not shutil.which('qemu-aarch64'):
        print('skip evalorder: needs qemu-aarch64')
        return
    import qemu_ilp32 as q
    base = list(q.CXXFLAGS)
    outs = {}
    for tag, extra, exp in (('default', [], 'evalorder.gcc.expect'), ('msvc-guess', MSVC_GUESS, 'evalorder.msvc-guess.expect')):
        expect = open(os.path.join(HERE, exp)).read()
        q.CXXFLAGS = base + ['-fms-extensions', '-fno-strict-aliasing', '-fwrapv', '-mno-outline-atomics',
                             '-DFNVNX_FREESTANDING', '-fplugin=' + plugin] + extra
        try:
            exe = q.build([os.path.join(HERE, 'evalorder.cpp')], os.path.join(scratch, 'evalorder_' + tag))
        except RuntimeError as e:
            check(False, 'evalorder %s builds (%s)' % (tag, str(e)[-300:]))
            continue
        finally:
            q.CXXFLAGS = base
        rc, out = q.run(exe)
        outs[tag] = out
        bad = [l for l, e in zip(out.splitlines(), expect.splitlines()) if l != e]
        check(rc == 0 and out == expect, 'evalorder ILP32 %s: %s in all %d cases (%s)'
              % (tag, exp, len(expect.splitlines()), bad[:3] or out[-200:] if out != expect else ''))
    if len(outs) == 2:
        check(outs['default'] != outs['msvc-guess'], 'evalorder: object=last operator=rtl changes the order (the options bite)')
    msvc = os.path.join(HERE, 'evalorder.msvc.expect')
    if os.path.isfile(msvc) and 'default' in outs:
        # S11's MSVC 2008 x86 output: which of the two the build has to follow (informational until it decides)
        m = open(msvc).read()
        print('     evalorder: MSVC 2008 matches %s' % ('the defaults' if m == outs['default'] else
              'object=last operator=rtl' if m == outs.get('msvc-guess') else 'neither'))


def litmus(scratch, plugin):
    if not shutil.which('qemu-aarch64'):
        print('skip litmus: needs qemu-aarch64')
        return
    import qemu_ilp32 as q
    base = list(q.CXXFLAGS)
    flags = ['-fms-extensions', '-fno-strict-aliasing', '-fwrapv', '-mno-outline-atomics', '-mtune=cortex-a57']
    res = {}
    for tag, extra in (('plain', []), ('order', ['-fplugin=' + plugin])):
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
    for tag in ('order',):
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
    removed_options(scratch, plugin)
    codegen(scratch, plugin)
    tso_objdump(scratch, plugin)
    args_order(scratch, plugin)
    eval_codegen(scratch, plugin)
    eval_order(scratch, plugin)
    msvc_table(scratch, plugin)
    v2_shapes(scratch)
    litmus(scratch, plugin)
    print('%d failed' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
