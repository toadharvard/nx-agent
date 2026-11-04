#!/usr/bin/env python3
"""test_s12_keys.py - V1 (D14) against S12-ordering f241f21 (whole-program ordering keys, ATOMIC-PLAIN-STORE /
POLL-PLAIN) and 3426547 (ORDER-OBJECT / ORDER-OPERATOR switches object= / operator=).

Claims (docs/ORDERING.md): "every field or variable whose address any unit of the program passes to Interlocked*,
directly or through a forwarder (also forwarders of forwarders), is ordered like a volatile in every unit"; for a
wait-only poll loop "through the key list every store to them in any unit becomes stlr"; compile.sh keeps
<obj dir>/order.keys current itself; with the default object= / operator= the objects are byte-identical, and
object=last operator=rtl give MSVC's push order (arguments right to left, then the object) while direct calls stay
direct.

Every case is our own synthetic code: unit A (and B) are run through discover=, their keys merged as compile.sh does
(toolchain/order/keys_merge.py when the tree has it, else sort -u as compile.sh did), unit C is compiled with that list, and C's aarch64 asm is read for stlr / ldar / dmb.
  K1  control: A hands &o->m_lock to InterlockedCompareExchange, C's plain store is stlr
  K2  cross-unit forwarder: AtomicInc(long &) is defined in unit A, called with o->m_count only in unit B
  K3  Interlocked through a local pointer (`long *p = &o->m; InterlockedCompareExchange(p, 1, 0)`, the decompiler's
      own shape)
  K4  poll through `this` in an inline member (`while (!done) Sleep(0)`), writer in C: control
  K5  polled bit-field: the writer keeps a fenced store (dmb ish before it)
  K6  struct copy over a polled flag (`*p = *n`): the store to the flag is not ordered
  K7  typedef'd unnamed struct (`typedef struct { long lock; } SPIN`), template field, base-class field: controls
  K8  (note, not counted) store through a pointer alias (`int *q = &w->done; *q = 1`): beyond name matching
  K9  (note, not counted) `for (;;) { if (g) break; Sleep(0); }`: no loop condition, outside the claimed shape
  K10 compile.sh: the cached per-unit keys go stale when a tree header changes (only the unit's own mtime counts)
  O1  object= / operator= defaults: objects byte-identical with and without the arguments (virtual calls, << chains)
  O2  object=last operator=rtl under qemu: nested member calls and [] / << chains print MSVC's push order; direct
      calls stay `bl`
A case is a DEFECT when a store the claim covers stays a plain `str` with no stlr / dmb, or a switch does not do what
the docs say.
  python3 -I tests/v1/test_s12_keys.py     -> one line per case, exit 1 when any shows a defect (skip: no toolchain)
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))
CXX = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
WORK = tempfile.mkdtemp(prefix='v1-s12keys-')   # raises when it cannot: never an empty or relative path
FLAGS = ['-mabi=ilp32', '-std=gnu++17', '-O2', '-fms-extensions', '-fno-strict-aliasing', '-fwrapv', '-fpermissive',
         '-march=armv8-a+crc+crypto', '-mtune=cortex-a57', '-fno-stack-protector', '-mno-outline-atomics']
fails = 0


def out(case, defect, text):
    global fails
    if defect:
        fails += 1
    print('%-7s %-4s %s' % ('DEFECT' if defect else 'ok', case, text))


def note(case, text):
    print('%-7s %-4s %s' % ('note', case, text))


def skip(why):
    print('skip: ' + why)
    shutil.rmtree(WORK, ignore_errors=True)
    sys.exit(0)


if not shutil.which(CXX) or not shutil.which('g++'):
    skip('needs %s and g++' % CXX)
r = subprocess.run(['bash', os.path.join(PORT, 'toolchain', 'order', 'build.sh'), os.path.join(WORK, 'plug')],
                   capture_output=True, text=True, env=dict(os.environ, CXX=CXX))
if r.returncode != 0:
    skip('fnvnx_order plugin does not build: ' + r.stderr.strip()[-200:])
SO = r.stdout.strip()
PLUG = ['-fplugin=' + SO]

HDR = r'''
extern "C" long InterlockedIncrement(volatile long *);
extern "C" long InterlockedCompareExchange(volatile long *, long, long);
extern "C" void Sleep(unsigned);
struct Obj { long m_count; long m_lock; long m_alias; int m_plain; };
void AtomicInc(long &x);
struct W { int done; unsigned bit : 1; int data; void wait() { while (!done) Sleep(0); } };
struct P { int a; int flag; };
typedef struct { long lock; } SPIN;
template <class T> struct Q { long m_lock; T v; };
struct Base { long m_blk; };
struct Der : Base { int x; };
extern int g_brk;
'''
UNIT_A = r'''#include "h.h"
void AtomicInc(long &x) { InterlockedIncrement(&x); }
void lock(Obj *o) { while (InterlockedCompareExchange(&o->m_lock, 1, 0)) {} }
void alias(Obj *o) { long *p = &o->m_alias; InterlockedCompareExchange(p, 1, 0); }
void waitw(W *w) { w->wait(); }
void waitbit(W *w) { while (!w->bit) Sleep(0); }
void waitp(P *p) { while (!p->flag) Sleep(0); }
void spin(SPIN *s) { InterlockedIncrement(&s->lock); }
void qlock(Q<int> *q) { InterlockedIncrement(&q->m_lock); }
void blk(Der *d) { InterlockedIncrement(&d->m_blk); }
void brk() { for (;;) { if (g_brk) break; Sleep(0); } }
'''
UNIT_B = r'''#include "h.h"
void use(Obj *o) { AtomicInc(o->m_count); }
'''
UNIT_C = r'''#include "h.h"
void st_lock(Obj *o) { o->m_lock = 0; }
void st_count(Obj *o) { o->m_count = 0; }
void st_alias(Obj *o) { o->m_alias = 0; }
void st_done(W *w, int r) { w->data = r; w->done = 1; }
void st_bit(W *w) { w->bit = 1; }
void st_copy(P *p, const P *n) { *p = *n; }
void st_ptr(W *w) { int *q = &w->done; *q = 1; }
void st_spin(SPIN *s) { s->lock = 0; }
void st_q(Q<int> *q) { q->m_lock = 0; }
void st_blk(Base *b) { b->m_blk = 0; }
void st_brk() { g_brk = 1; }
'''


def write(path, text):
    with open(path, 'w') as f:
        f.write(text)


def gxx(args, cwd=WORK):
    return subprocess.run([CXX] + FLAGS + PLUG + args, capture_output=True, text=True, cwd=cwd)


def functions(asm):
    res, cur = {}, None
    for line in asm.splitlines():
        m = re.match(r'^([A-Za-z_][\w.$]*):', line)
        if m and not m.group(1).startswith('.L'):
            cur = m.group(1)
            res[cur] = []
        elif cur and line.startswith('\t') and not line.startswith('\t.'):
            res[cur].append(line.strip())
    return res


def body(funcs, name):
    for k, v in funcs.items():
        if k.startswith('_Z%d%s' % (len(name), name)):
            return v
    return []


def ordered(lines):
    return any(re.match(r'(stlr|ldar|dmb)', l) for l in lines)


# ---- K1-K9: discovery in A and B, the merged list, unit C compiled with it
write(os.path.join(WORK, 'h.h'), HDR)
keys = set()
for u, src in (('a', UNIT_A), ('b', UNIT_B)):
    write(os.path.join(WORK, u + '.cpp'), src)
    r = gxx(['-fsyntax-only', '-fplugin-arg-fnvnx_order-discover=%s.keys' % u, u + '.cpp'])
    if r.returncode != 0:
        print('FAIL unit %s does not compile: %s' % (u, r.stderr[-400:]))
        sys.exit(1)
    keys |= set(open(os.path.join(WORK, u + '.keys')).read().splitlines())
# the build's own merge: toolchain/order/keys_merge.py since 8c18138 (it resolves cross-unit forwarders); before
# that, compile.sh merged and sorted the unit files, as `sort -u` does here
MERGE = os.path.join(HERE, '..', '..', 'toolchain', 'order', 'keys_merge.py')
if os.path.isfile(MERGE):
    r = subprocess.run([sys.executable, '-I', MERGE, os.path.join(WORK, 'a.keys'), os.path.join(WORK, 'b.keys')],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print('FAIL keys_merge.py: %s' % r.stderr[-400:])
        sys.exit(1)
    write(os.path.join(WORK, 'all.keys'), r.stdout)
    keys = set(r.stdout.splitlines())
else:
    write(os.path.join(WORK, 'all.keys'), ''.join(k + '\n' for k in sorted(keys)))
write(os.path.join(WORK, 'c.cpp'), UNIT_C)
r = gxx(['-fplugin-arg-fnvnx_order-keys=all.keys', '-S', '-o', 'c.s', 'c.cpp'])
if r.returncode != 0:
    print('FAIL unit c does not compile: %s' % r.stderr[-400:])
    sys.exit(1)
C = functions(open(os.path.join(WORK, 'c.s')).read())


def st(case, fn, key, what, counted=True):
    b = body(C, fn)
    good = ordered(b)
    text = '%s: %s; key %s %s; %s() = %s' % (what, 'ordered' if good else 'plain store, no stlr/dmb',
                                              key, 'in list' if key in keys else 'MISSING from list', fn,
                                              ' / '.join(l for l in b if not l.startswith('ret')))
    if counted:
        out(case, not good, text)
    else:
        note(case, text)


st('K1', 'st_lock', 'field Obj::m_lock', 'control, direct InterlockedCompareExchange in A')
st('K2', 'st_count', 'field Obj::m_count', 'forwarder AtomicInc defined in unit A, called with o->m_count in unit B')
st('K3', 'st_alias', 'field Obj::m_alias', 'Interlocked operand through a local pointer (p = &o->m_alias)')
st('K4', 'st_done', 'field W::done', 'poll through this in inline W::wait(), writer w->done = 1')
st('K5', 'st_bit', 'field W::bit', 'polled bit-field, writer w->bit = 1 (fenced store expected)')
st('K6', 'st_copy', 'field P::flag', 'struct copy *p = *n over the polled P::flag')
st('K7a', 'st_spin', 'field SPIN::lock', 'typedef struct { long lock; } SPIN')
st('K7b', 'st_q', 'field Q::m_lock', 'template Q<int>::m_lock')
st('K7c', 'st_blk', 'field Base::m_blk', 'base-class field reached through Der in A, through Base in C')
st('K8', 'st_ptr', 'field W::done', 'store through int *q = &w->done', counted=False)
st('K9', 'st_brk', 'var g_brk', 'for (;;) { if (g_brk) break; Sleep(0); } poll', counted=False)


# ---- K10: compile.sh's per-unit key cache vs a changed tree header
def k10():
    s2n = os.environ.get('SSE2NEON', os.path.join(PORT, 'ext', 'sse2neon'))
    if not os.path.isfile(os.path.join(s2n, 'sse2neon.h')) or not shutil.which('jq'):
        note('K10', 'skipped: needs jq and SSE2NEON (sse2neon.h) for compile.sh')
        return
    tree, obj = os.path.join(WORK, 'tree'), os.path.join(WORK, 'obj')
    os.makedirs(os.path.join(tree, 'build'))
    os.makedirs(os.path.join(tree, 'include'))
    hdr = os.path.join(tree, 'include', 'k10.h')
    write(hdr, '#include "fnvnx_win32.h"\nstruct K { LONG m_lock; int m_flag; };\n'
               'inline void k_lock(K *k) { while (InterlockedCompareExchange(&k->m_lock, 1, 0)) {} }\n')
    write(os.path.join(tree, 'ka.cpp'), '#include "k10.h"\nvoid use_a(K *k) { k_lock(k); }\n')
    write(os.path.join(tree, 'kb.cpp'), '#include "k10.h"\nvoid st_b(K *k) { k->m_flag = 1; }\n')
    json.dump({'units': [{'source': 'ka.cpp'}, {'source': 'kb.cpp'}]}, open(os.path.join(tree, 'build', 'units.json'), 'w'))
    env = dict(os.environ, SSE2NEON=s2n, KEYS_ONLY='1', CXX=CXX)
    env.pop('ORDER_KEYS', None)
    cs = os.path.join(PORT, 'tools', 'link', 'compile.sh')
    r1 = subprocess.run(['bash', cs, tree, obj, '', '2'], capture_output=True, text=True, env=env)
    k1 = open(os.path.join(obj, 'order.keys')).read().splitlines() if os.path.exists(os.path.join(obj, 'order.keys')) else None
    if r1.returncode != 0 or k1 is None or 'field K::m_lock' not in k1:
        out('K10', True, 'compile.sh KEYS_ONLY=1 first run gave no K::m_lock (rc %d, keys %s): %s'
            % (r1.returncode, k1, (r1.stdout + r1.stderr)[-300:]))
        return
    # the header's inline lock now takes m_flag (a decomp fix in a header); the units' own text is unchanged
    time.sleep(1.1)
    write(hdr, '#include "fnvnx_win32.h"\nstruct K { LONG m_lock; LONG m_flag; };\n'
               'inline void k_lock(K *k) { while (InterlockedCompareExchange(&k->m_flag, 1, 0)) {} }\n')
    r2 = subprocess.run(['bash', cs, tree, obj, '', '2'], capture_output=True, text=True, env=env)
    k2 = open(os.path.join(obj, 'order.keys')).read().splitlines()
    fresh = subprocess.run([CXX] + FLAGS + PLUG + ['-include', os.path.join(PORT, 'shim', 'include', 'fnvnx_compat.h'),
                            '-I' + os.path.join(PORT, 'shim', 'include'), '-I' + s2n, '-I' + os.path.join(tree, 'include'),
                            '-fsyntax-only', '-fplugin-arg-fnvnx_order-discover=' + os.path.join(WORK, 'k10.keys'),
                            os.path.join(tree, 'ka.cpp')], capture_output=True, text=True)
    want = open(os.path.join(WORK, 'k10.keys')).read().splitlines() if fresh.returncode == 0 else ['field K::m_flag']
    out('K10', r2.returncode != 0 or 'field K::m_flag' not in k2,
        'header k10.h changed (lock moves to K::m_flag), units untouched: order.keys %s, a fresh discovery gives %s'
        % (k2, want))


k10()


# ---- O1: object= / operator= defaults are byte-identical
O_SRC = r'''
extern "C" int printf(const char *, ...);
static char buf[64]; static int nb;
int t(char c, int v) { buf[nb++] = c; return v; }
struct S { int v; virtual int vf(int a, int b) { return v + a + b; } __attribute__((noinline)) int f(int a, int b) { return v + a * b; }
           S &operator<<(int x) { v += x; return *this; } int operator[](int i) { return v + i; } };
S s0, s1;
S *obj(char c) { t(c, 0); return c == 'o' ? &s0 : &s1; }
struct T { int v; };
T &operator<<(T &a, int b) { a.v = a.v * 10 + b; return a; }
T tt; T &tobj() { t('T', 0); return tt; }
int run(const char *name, int (*fn)()) { nb = 0; int r = fn(); buf[nb] = 0; printf("%s %s %d\n", name, buf, r); return r; }
// direct member call: object o, arguments a b; then nested: object p, argument q() whose own object is r
int c1() { return obj('o')->f(t('a', 1), t('b', 2)); }
int c2() { return obj('o')->vf(t('a', 1), t('b', 2)); }
int c3() { return obj('p')->f(obj('r')->f(t('x', 1), t('y', 2)), t('q', 3)); }
int c4() { tobj() << t('a', 1) << t('b', 2); return tt.v; }
int c5() { return (*obj('o'))[t('i', 1)]; }
int main() { run("c1", c1); run("c2", c2); run("c3", c3); run("c4", c4); run("c5", c5); return 0; }
'''
# MSVC x86 push order (args right to left, then the object for thiscall, each call's own operands before the call)
O_MSVC = {'c1': 'bao', 'c2': 'bao', 'c3': 'qyxrp', 'c4': 'baT', 'c5': 'io'}
write(os.path.join(WORK, 'o.cpp'), O_SRC)
objs = {}
for tag, extra in (('none', []), ('first', ['-fplugin-arg-fnvnx_order-object=first']),
                   ('cpp17', ['-fplugin-arg-fnvnx_order-operator=cpp17']),
                   ('both', ['-fplugin-arg-fnvnx_order-object=first', '-fplugin-arg-fnvnx_order-operator=cpp17'])):
    r = gxx(extra + ['-c', '-o', 'o_%s.o' % tag, 'o.cpp'])
    objs[tag] = open(os.path.join(WORK, 'o_%s.o' % tag), 'rb').read() if r.returncode == 0 else None
same = objs['none'] is not None and all(objs[k] == objs['none'] for k in objs)
out('O1', not same, 'object=first / operator=cpp17 given explicitly vs omitted: objects %s'
    % ('byte-identical' if same else 'DIFFER (%s)' % {k: (len(v) if v else None) for k, v in objs.items()}))


# ---- O2: object=last operator=rtl, run under qemu (LP64 user mode; the order does not depend on the ABI)
def o2():
    if not shutil.which('qemu-aarch64'):
        note('O2', 'skipped: needs qemu-aarch64')
        return
    sysroot = '/usr/aarch64-linux-gnu'
    lp64 = [f for f in FLAGS if f != '-mabi=ilp32']
    res = {}
    for tag, extra in (('def', []), ('msvc', ['-fplugin-arg-fnvnx_order-object=last',
                                               '-fplugin-arg-fnvnx_order-operator=rtl'])):
        exe = os.path.join(WORK, 'o_' + tag)
        r = subprocess.run([CXX] + lp64 + PLUG + extra + ['-static', '-o', exe, 'o.cpp'], capture_output=True,
                           text=True, cwd=WORK)
        if r.returncode != 0:
            note('O2', 'skipped: cannot link a static aarch64 program: ' + r.stderr[-200:])
            return
        p = subprocess.run(['qemu-aarch64', '-L', sysroot, exe], capture_output=True, text=True, timeout=60)
        res[tag] = dict((l.split()[0], l.split()[1]) for l in p.stdout.splitlines() if len(l.split()) == 3)
        if tag == 'msvc':
            a = subprocess.run([CXX] + FLAGS + PLUG + extra + ['-S', '-o', '-', 'o.cpp'], capture_output=True,
                               text=True, cwd=WORK).stdout
            direct = functions(a)
    for c, want in sorted(O_MSVC.items()):
        got = res['msvc'].get(c)
        out('O2', got != want, '%s object=last operator=rtl prints %s, MSVC push order %s (defaults: %s)'
            % (c, got, want, res['def'].get(c)))
    b1 = body(direct, 'c1')
    bl = [l for l in b1 if l.startswith('bl') or l.startswith('b\t')]
    out('O2', not any('_ZN1S1fEii' in l for l in bl) or any(l.startswith('blr') for l in b1),
        'c1 under object=last calls S::f directly: %s' % ' / '.join(bl))


o2()
shutil.rmtree(WORK, ignore_errors=True)
print('%d defect(s)' % fails)
sys.exit(1 if fails else 0)
