#!/usr/bin/env python3
"""Check of link/gate_order.py, link/a64order.py, link/framesec.py and toolchain/order/fnvnx_order.cc.  Fixtures: hand-assembled ILP32
AArch64 objects (the machine-code rules are independent of the plugin) and C++ compiled with the distro cross compiler, the product
flags and the order plugin built from the tracked source (product args `args=rtl object=msvc operator=rtl assign=msvc`).  Cases:
certified: ordered forms, frame accesses outside the recorded variables, a frame address that leaves the function and points into a
recorded variable, linker and read-only loads, fenced runs; plugin output for a non-escaped local, an escaped local written after
the escape (ordered by the plugin), a return slot (sret) aggregate store, an aggregate load into a local, a bit-field store.
Red and named `object:item`: a plain str through a non-frame register; a plain sp store to a recorded (escaped) slot, in a
hand-assembled twin of the plugin's code; a function without record, a missing and a malformed `.fnvnx.frame` section, a `dynamic`
record (alloca); a frame address that leaves the function outside every recorded range (a by-value aggregate argument copy); fences
on one path only; an undecodable word; a size-0 function symbol; an unreadable object.  Also: the section parser, gate file, exit
status, determinism.  No network, no game data.  Run: python3 -I <this file>."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'contract'))
import a64order
import framesec

TOOL = os.path.join(HERE, '..', '..', 'toolchain', 'order', 'fnvnx_order.cc')
CXX = 'aarch64-linux-gnu-g++-13'
FLAGS = ['-mabi=ilp32', '-std=gnu++17', '-O2', '-fwrapv', '-fno-strict-aliasing', '-march=armv8-a+crc+crypto', '-mtune=cortex-a57',
         '-fno-stack-protector', '-fno-stack-clash-protection', '-mno-outline-atomics', '-fno-exceptions', '-fno-rtti']
PLUGIN_ARGS = ['args=rtl', 'object=msvc', 'operator=rtl', 'assign=msvc']
fails = []
tmp = tempfile.mkdtemp(prefix='gate-order-')


def check(what, got, want):
    if got != want:
        fails.append('%s: got %r, want %r' % (what, got, want))


def run(argv):
    return subprocess.run(argv, capture_output=True, text=True)


def build_plugin():
    pdir = run([CXX, '-print-file-name=plugin']).stdout.strip()
    d = os.path.join(tmp, 'plug')
    os.makedirs(d)
    host = 'x86_64-linux-gnu-g++-13'
    crt = [run([host, '-print-file-name=' + n]).stdout.strip() for n in ('crti.o', 'crtbeginS.o')]
    end = [run([host, '-print-file-name=' + n]).stdout.strip() for n in ('crtendS.o', 'crtn.o')]
    r = run([host, '-shared', '-nostdlib', '-fPIC', '-fno-rtti', '-fno-exceptions', '-O2', '-I' + pdir + '/include', '-o',
             os.path.join(d, 'fnvnx_order.so')] + crt + [TOOL] + end)
    if r.returncode:
        fails.append('plugin build: ' + r.stderr[-800:])
        return None
    return os.path.join(d, 'fnvnx_order.so')


def compile_cxx(src, name, plugin, extra=()):
    p = os.path.join(tmp, name + '.cpp')
    o = os.path.join(tmp, name + '.o')
    open(p, 'w').write(src)
    pl = ['-fplugin=' + plugin] + ['-fplugin-arg-fnvnx_order-' + a for a in PLUGIN_ARGS] if plugin else []
    r = run([CXX] + FLAGS + list(extra) + pl + ['-c', p, '-o', o])
    if r.returncode:
        fails.append('compile %s: %s' % (name, r.stderr[-800:]))
    return o


def func(name, body):
    return '\t.global %s\n\t.type %s,%%function\n%s:\n%s\t.size %s,.-%s\n' % (name, name, name, ''.join('\t' + l + '\n' for l in body), name, name)


def assemble(name, funcs, frame=None, extra=''):
    """funcs {symbol: [asm lines]}; frame {symbol: record text} (None: no .fnvnx.frame section; a missing symbol has no line;
    a str value is the raw section text)"""
    t = '\t.text\n' + ''.join(func(n, b) for n, b in funcs.items()) + extra + '\t.section .rodata\ntab:\t.word 1,2,3,4\n\t.data\ngv:\t.word 0\n'
    if isinstance(frame, str):
        t += '\t.section .fnvnx.frame,"",%progbits\n\t.ascii "' + frame + '"\n'
    elif frame is not None:
        t += '\t.section .fnvnx.frame,"",%progbits\n' + ''.join('\t.ascii "%s\\t%s\\n"\n' % (k, v) for k, v in sorted(frame.items()))
    s = os.path.join(tmp, name + '.s')
    o = os.path.join(tmp, name + '.o')
    open(s, 'w').write(t)
    r = run(['aarch64-linux-gnu-as', '-mabi=ilp32', '-march=armv8.1-a', s, '-o', o])
    if r.returncode:
        fails.append('assemble %s: %s' % (name, r.stderr[-500:]))
    return o


def gate(*objs):
    d = tempfile.mkdtemp(dir=tmp)
    r = subprocess.run([sys.executable, '-I', os.path.join(HERE, '..', 'gate_order.py')] + list(objs), capture_output=True, text=True,
                       env={'GATE_OUT': d, 'PATH': os.environ.get('PATH', '')})
    return r, json.load(open(os.path.join(d, 'order.json')))


def red(o):
    return {k: v for k, v in a64order.scan(o).rows if v}


# ---- the section parser
P = framesec.parse_frame
check('parse empty', P(b'', 'x'), [])
check('parse lines', P(b'a\t\nb\tdynamic\nc\t-16:12 -4:4 0:8\n', 'x'), [('a', ()), ('b', 'dynamic'), ('c', ((-16, 12), (-4, 4), (0, 8)))])
fr3 = P(b'a\t-4:4 unk:mem unk:reg\targs:16\nb\t\targs:0\nc\tdynamic\targs:8\nd\t4:4\n', 'x')
check('parse 3-field rows', [(tuple(r), r.args, r.unk) for r in fr3], [(('a', 'dynamic'), 16, ('mem', 'reg')), (('b', ()), 0, ()), (('c', 'dynamic'), 8, ()), (('d', ((4, 4),)), None, ())])
for bad in (b'a\t\targs:01\n', b'a\t\targs:-1\n', b'a\t\tx:1\n', b'a\tunk:foo\targs:0\n', b'a\tunk:reg unk:mem\targs:0\n', b'a\tunk:reg 4:4\targs:0\n', b'a\t\t\targs:0\n'):
    try:
        P(bad, 'x')
        fails.append('parser accepted %r' % bad)
    except ValueError:
        pass
for bad in (b'a\t', b'a', b'a\t\tb\n', b'\t\n', b'a b\t\n', b'a\t1:0\n', b'a\t01:4\n', b'a\t+1:4\n', b'a\t4:1 -4:4\n', b'a\tx\n', b'a\t1:2 \n',
            b'a\tDynamic\n', b'a\t1:2:3\n', b'\xff\t\n', b'a\t-0:4\n', b'a\t 1:2\n'):
    try:
        P(bad, 'x')
        fails.append('parser accepted %r' % bad)
    except ValueError:
        pass

# ---- hand-assembled fixtures
FUNCS = {
    'ok_ordered': ['ldar w1, [x0]', 'stlr w1, [x0]', 'ldaxr w2, [x0]', 'stlxr w3, w2, [x0]', 'ldarb w1, [x0]', 'ret'],
    'ok_frame': ['stp x29, x30, [sp, #-32]!', 'mov x29, sp', 'str w0, [x29, #28]', 'ldr w1, [sp, #28]', 'bl ext',
                 'ldr w2, [x29, #28]', 'ldp x29, x30, [sp], #32', 'ret'],
    'ok_fenced': ['ldr w1, [x0]', 'dmb ishld', 'dmb ish', 'str w1, [x0, #4]', 'ret'],
    'ok_fenced_branch': ['cbz w1, 1f', 'ldr w2, [x0]', 'dmb ishld', '1:', 'ret'],
    'ok_ro': ['adrp x1, tab', 'add x1, x1, :lo12:tab', 'ldr w0, [x1, x0, lsl #2]', 'adrp x2, :got:gv', 'ldr w2, [x2, :got_lo12:gv]', 'ret'],
    'ok_literal': ['ldr w0, lit', 'ret', 'lit: .word 5'],
    'ok_escape': ['sub sp, sp, #16', 'add x1, sp, #8', 'adrp x2, gv', 'add x2, x2, :lo12:gv', 'stlr w1, [x2]', 'stlr wzr, [x1]',
                  'add sp, sp, #16', 'ret'],
    'ok_local_base': ['sub sp, sp, #16', 'add x1, sp, #8', 'str w0, [x1]', 'ldr w2, [x1]', 'add sp, sp, #16', 'ret'],
    'bad_plain_store': ['str w1, [x0]', 'ret'],
    'bad_plain_load': ['ldr w0, [x1, #8]', 'ret'],
    'bad_dynsp': ['sub sp, sp, x0', 'str w1, [sp]', 'ret'],
    'bad_x29': ['stp x29, x30, [sp, #-16]!', 'mov x29, sp', 'mov x29, x0', 'str w1, [x29]', 'ldp x29, x30, [sp], #16', 'ret'],
    'bad_one_path': ['cbz w1, 1f', 'dmb ishld', '1:', 'ldr w2, [x0]', 'ret'],
    'bad_one_path2': ['ldr w2, [x0]', 'cbz w1, 1f', 'dmb ishld', '1:', 'ret'],
    'bad_call': ['stp x29, x30, [sp, #-16]!', 'ldr w2, [x0]', 'bl ext', 'dmb ishld', 'ldp x29, x30, [sp], #16', 'ret'],
    'bad_store_late': ['str w1, [x0]', 'dmb ish', 'ret'],
    'bad_store_ro': ['adrp x1, tab', 'add x1, x1, :lo12:tab', 'str w0, [x1]', 'ret'],
    'bad_second_load': ['adrp x2, :got:gv', 'ldr w2, [x2, :got_lo12:gv]', 'ldr w0, [x2]', 'ret'],
    'bad_excl': ['ldxr w1, [x0]', 'stxr w2, w1, [x0]', 'ret'],
    'bad_lse': ['ldadd w1, w2, [x0]', 'ret'],
    'bad_ro_written': ['adrp x1, tab', 'add x1, x1, :lo12:tab', 'mov x1, x0', 'ldr w0, [x1]', 'ret'],
    'bad_load_only_ishst': ['ldr w1, [x0]', 'dmb ishst', 'ret'],
    # the plugin's escaped local, with the write after the escape made plain: the twin of the ordered ok_escape
    'bad_escape_plain': ['sub sp, sp, #16', 'add x1, sp, #8', 'adrp x2, gv', 'add x2, x2, :lo12:gv', 'stlr w1, [x2]', 'str wzr, [sp, #8]',
                         'add sp, sp, #16', 'ret'],
    'bad_escape_plain_x29': ['stp x29, x30, [sp, #-32]!', 'mov x29, sp', 'add x1, x29, #24', 'adrp x2, gv', 'add x2, x2, :lo12:gv',
                             'stlr w1, [x2]', 'str wzr, [x29, #24]', 'ldp x29, x30, [sp], #32', 'ret'],
    'bad_escape_unrecorded': ['sub sp, sp, #32', 'add x0, sp, #16', 'str x1, [sp, #4]', 'bl ext', 'add sp, sp, #32', 'ret'],
    'bad_escape_unknown': ['sub sp, sp, x1', 'mov x0, sp', 'bl ext', 'ret'],
}
BADS = [n for n in FUNCS if n.startswith('bad_')]
OKS = [n for n in FUNCS if n.startswith('ok_')]
# the recorded frame: ok_escape's slot sp+8 = CFA-8 (16-byte frame) is recorded, so is bad_escape_plain's, bad_escape_plain_x29's
REC = {n: '' for n in FUNCS}
REC['ok_escape'] = '-8:4'
REC['bad_escape_plain'] = '-8:4'
REC['bad_escape_plain_x29'] = '-8:4'
REC['bad_dynsp'] = ''

good = assemble('good', {n: FUNCS[n] for n in OKS}, {n: REC[n] for n in OKS})
s = a64order.scan(good)
check('ok functions certify', [(k, v) for k, v in s.rows if v], [])
check('rules seen', sorted(s.stats), sorted(['ordered', 'frame', 'fenced-load', 'fenced-store', 'readonly', 'linker', 'literal', 'frame-escape']))
r, j = gate(good)
check('gate green', (r.returncode, j['status'], j['certified'] == j['universe'], j['uncertified']), (0, 'green', True, []))

for n in BADS:
    o = assemble(n, {n: FUNCS[n]}, {n: REC[n]})
    rs = red(o)
    check(n + ' is red', len(rs) >= 1 and all(k.startswith(n + '+0x') for k in rs), True)
bad_all = assemble('bad', {n: FUNCS[n] for n in BADS}, {n: REC[n] for n in BADS})
rs = red(bad_all)
check('exact: plain store', rs.get('bad_plain_store+0x0'), 'plain-store')
check('exact: plain load', rs.get('bad_plain_load+0x0'), 'plain-load')
check('exact: dynamic sp', rs.get('bad_dynsp+0x4'), 'plain-store')
check('exact: no frame pointer after overwrite', rs.get('bad_x29+0xc'), 'plain-store')
check('exact: second load after GOT', rs.get('bad_second_load+0x8'), 'plain-load')
check('exact: weak atomic', rs.get('bad_lse+0x0'), 'weak-atomic')
check('exact: plain sp store to an escaped slot', rs.get('bad_escape_plain+0x14'), 'plain-store')
check('exact: plain x29 store to an escaped slot', rs.get('bad_escape_plain_x29+0x18'), 'plain-store')
check('only that store in the escaped twins', sorted(k for k in rs if k.startswith('bad_escape_plain')),
      ['bad_escape_plain+0x14', 'bad_escape_plain_x29+0x18'])
check('exact: frame address out of every range', rs.get('bad_escape_unrecorded+0xc/escape'), 'frame-escape-unrecorded')
check('exact: frame address of an unknown stack pointer', rs.get('bad_escape_unknown+0x8/escape'), 'frame-escape-unknown')
check('only bad functions named', sorted({k.split('+')[0] for k in rs}), sorted(BADS))
r, j = gate(bad_all)
check('gate red', (r.returncode, j['status']), (2, 'red'))
check('gate lists the plain store', '%s:bad_plain_store+0x0' % bad_all in j['uncertified'], True)
check('uncertified sorted', j['uncertified'], sorted(j['uncertified']))
r2, j2 = gate(bad_all, good)
r3, j3 = gate(good, bad_all)
check('deterministic', j2, j3)
# the same hand-assembled escape with the ordered store and with a record that does not cover the slot
o = assemble('esc_norec', {'f': FUNCS['ok_escape']}, {'f': ''})
check('escape of a slot the record lacks is red', red(o), {'f+0x10/escape': 'frame-escape-unrecorded'})
o = assemble('esc_rec', {'f': FUNCS['ok_escape']}, {'f': '-8:4'})
check('escape into a recorded slot is green', red(o), {})
o = assemble('rec_overlap', {'f': ['sub sp, sp, #16', 'add x1, sp, #12', 'str w0, [sp, #4]', 'stlr x1, [x3]', 'str w0, [sp, #12]', 'add sp, sp, #16', 'ret']}, {'f': '-4:4'})
check('plain store beside a recorded slot is F, onto a slot whose address is formed it is not', red(o), {'f+0x10': 'plain-store'})
o = assemble('rec_above', {'f': ['ldr w0, [sp, #8]', 'ret']}, {'f': ''})
check('access above the CFA (incoming stack argument) is not F', red(o), {'f+0x0': 'plain-load'})
o = assemble('rec_pair', {'f': ['sub sp, sp, #16', 'add x2, sp, #4', 'stlr x2, [x3]', 'stp x0, x1, [sp, #0]', 'add sp, sp, #16', 'ret']}, {'f': '-12:4'})
check('a pair store sizes 16 bytes', red(o), {'f+0xc': 'plain-store'})

# frame record problems
o = assemble('noline', {'fa': FUNCS['ok_frame'], 'fb': FUNCS['ok_frame']}, {'fa': ''})
check('function without a line: its frame accesses are not F either', sorted(red(o)), sorted(
    ['fb'] + ['fb+0x%x' % x for x in (0, 8, 0xc, 0x14, 0x18)]))
check('function without a line is named', red(o).get('fb'), 'no-frame-record')
check('function with a line is not', [k for k in red(o) if k.startswith('fa')], [])
o = assemble('nosec', {'fa': FUNCS['ok_frame']}, None)
check('no section: object and function', (red(o).get(''), red(o).get('fa')), ('no-frame-section', 'no-frame-record'))
o = assemble('badsec', {'fa': FUNCS['ok_frame']}, 'fa\\t1:0\\n')
check('malformed section', (red(o).get(''), red(o).get('fa')), ('bad-frame-section', 'no-frame-record'))
o = assemble('dynrec', {'fa': FUNCS['ok_frame']}, {'fa': 'dynamic'})
check('dynamic record: no frame access is F', sorted(k for k in red(o) if '+' in k), ['fa+0x0', 'fa+0x14', 'fa+0x18', 'fa+0x8', 'fa+0xc'])
o = assemble('tworec', {'fa': FUNCS['ok_frame']}, {'fa': ''}, '')
o2 = assemble('dup', {'fa': FUNCS['ok_frame']}, 'fa\\t\\nfa\\t\\n')
check('two records for one function', red(o2).get('fa'), 'no-frame-record')
alias = '\t.global fb\n\t.set fb, fa\n'
o = assemble('alias_ok', {'fa': FUNCS['ok_frame']}, {'fa': '', 'fb': ''}, alias)
check('alias with the same record', red(o), {})
o = assemble('alias_bad', {'fa': FUNCS['ok_frame']}, {'fa': '-8:4', 'fb': ''}, alias)
check('alias with another record', red(o).get('fa'), 'frame-record-mismatch')

# an undecodable word inside a function, and a function symbol of size 0
o = assemble('undec', {}, {'u1': '', 'z0': ''}, '\t.global u1\n\t.type u1,%function\nu1:\n\t.inst 0x00100000\n\tret\n\t.size u1,.-u1\n'
             '\t.global z0\n\t.type z0,%function\nz0:\n\tldar w0, [x0]\n\tret\n')
check('undecodable word and size-0 symbol', {k: v for k, v in red(o).items()}, {'u1+0x0': 'undecodable', 'z0': 'size0'})

junk = os.path.join(tmp, 'junk.o')
open(junk, 'wb').write(b'not an object')
r, j = gate(junk)
check('unreadable object', (r.returncode, j['uncertified']), (2, [junk]))

# ---- C++ compiled with the order plugin
SRC = r'''
extern "C" void sink(void*); extern "C" void sink2(int); extern "C" void ext(void);
struct S { int a, b, c, d, e; };
struct B { unsigned a:3, b:5; };
S gs, gs2; B gb; int g, *gptr;
int nonesc(int x) { sink2(1); return x + 1; }
int esc() { int x = 0; gptr = &x; sink2(x); x = 5; sink2(x); return x; }
int esc2() { int x[4]; gptr = x; for (int i = 0; i < 4; i++) x[i] = i; sink(x); return x[2]; }
S ret() { return gs; }
int agg() { S l = gs2; return l.a + l.e; }
void stb() { gb.b = 3; }
int ldb() { return gb.b; }
int cpy() { gs = gs2; return g; }
struct __attribute__((packed)) PB { unsigned first:24; unsigned cat:8; unsigned short delta; };
PB gpb[4];
static const PB cpb[] = {{1,2,3},{4,5,6},{7,8,9},{3,3,3},{1,1,1}};
int pbl(int m) { return gpb[m].first; }
void pbs(int m) { gpb[m].first = 7; }
static inline int pbis(unsigned u, const PB* t, int n) {
  const PB* b = t;
  while (n > 0) { int mid = n / 2; const PB* p = b + mid; if (u > p->first + p->delta) { b = p + 1; n -= mid + 1; } else n = mid; }
  return b->cat;
}
int pbc(unsigned u) { return pbis(u, cpb, 5); }
void takes(S s);
void pass() { takes(gs); }
char* dyn(int n) { char* p = (char*)__builtin_alloca(n); sink(p); p[1] = 2; return p; }
'''
LANES_SRC = r'''
struct H { unsigned spares[32]; };
void sw(H* __restrict d, const H* __restrict s) {
  for (int i = 0; i < 32; i++) { const char* p = (const char*)&s->spares[i]; char* q = (char*)&d->spares[i]; q[0]=p[3]; q[1]=p[2]; q[2]=p[1]; q[3]=p[0]; }
}
'''
EH_SRC = r'''
static unsigned char tab[97];
void init() { __builtin_init_dwarf_reg_size_table(tab); }
void* f(void* a) { return __builtin_extract_return_addr(a); }
void* g(void* a) { return __builtin_frob_return_addr(a); }
void h(long a, void* b) { __builtin_eh_return(a, b); }
'''
plug = build_plugin()
if plug:
    po = compile_cxx(SRC, 'plug', plug)
    plain = compile_cxx(SRC, 'plain', None)
    rows = a64order.scan(po).rows
    rs = {k: v for k, v in rows if v}
    fr = framesec.read(po)
    rec = dict(fr)
    check('record: every function has one line', len(rec), len(fr))
    check('record: non-escaped local has none', rec.get('_Z6nonesci'), ())
    check('record: escaped scalar', rec['_Z3escv'], ((-4, 4),))
    check('record: escaped array', rec['_Z4esc2v'], ((-16, 16),))
    check('record: alloca is dynamic', rec['_Z3dyni'], 'dynamic')
    names = sorted({k.split('+')[0].split('/')[0] for k in rs})
    check('plugin output: red functions are exactly the documented ones', names, ['_Z3dyni', '_Z4passv'])
    check('alloca: its frame accesses are not F', any(k.startswith('_Z3dyni+') and v == 'plain-store' for k, v in rs.items()), True)
    check('by-value aggregate argument copy is an escape no record covers',
          [k.split('+')[0] for k, v in rs.items() if v == 'frame-escape-unrecorded'], ['_Z4passv'])
    ok = {k for k, v in rows if not v}
    check('non-escaped local: frame accesses certified', any(k.startswith('_Z6nonesci+') for k in ok), True)
    check('escaped local: certified and ordered', not any(k.startswith('_Z3escv') for k in rs) and any(k.startswith('_Z3escv+') for k in ok), True)
    check('escaped array: certified', not any(k.startswith('_Z4esc2v') for k in rs), True)
    check('sret aggregate store certified', not any(k.startswith('_Z3retv') for k in rs), True)
    check('aggregate load certified', not any(k.startswith('_Z3aggv') or k.startswith('_Z3cpyv') for k in rs), True)
    check('packed bit-field (no representative): load, store and the read-only table walk certify',
          [k for k in rs if k.startswith(('_Z3pbli', '_Z3pbsi', '_Z3pbcj'))], [])
    dis0 = run(['aarch64-linux-gnu-objdump', '-d', '--no-show-raw-insn', po]).stdout
    pbl = dis0.split('<_Z3pbli>:')[1].split('\n\n')[0]
    pbs = dis0.split('<_Z3pbsi>:')[1].split('\n\n')[0]
    pbc = dis0.split('<_Z3pbcj>:')[1].split('\n\n')[0]
    check('packed bit-field load: its 3 bytes are ldarb in address order, no plain load of the field', (pbl.count('ldarb'), 'ldr\tw' in pbl), (3, False))
    check('packed bit-field store: read-modify-write of the 3 bytes, release byte stores', (pbs.count('ldarb'), pbs.count('stlrb')), (3, 3))
    check('table walk through a derived pointer: read-only by provenance, no ordered access', ('ldar' in pbc, 'stlr' in pbc), (False, False))
    check('the walk records the table in .fnvnx.ro', '_ZL3cpb' in run(['aarch64-linux-gnu-readelf', '-p', '.fnvnx.ro', po]).stdout, True)
    # the vectorizer's load/store-lanes form (-O3): `MEM <char[64]> [p] = .STORE_LANES (array)` writes shared memory through an internal function
    lanes = compile_cxx(LANES_SRC, 'lanes', plug, ['-O3'])
    lr = {k: v for k, v in a64order.scan(lanes).rows if v}
    check('lanes: the shared load and store of the byte-swap loop certify', lr, {})
    ldis = run(['aarch64-linux-gnu-objdump', '-d', '--no-show-raw-insn', lanes]).stdout
    check('lanes: the shared bytes are read ldarb and written stlrb (no plain access of the shared span)', ('ldarb' in ldis, 'stlrb' in ldis), (True, True))
    # the unwinder's context builtins (libgcc unwind-dw2.c): value-only return-address/EH builtins, and the dwarf register-size table filler
    ehs = compile_cxx(EH_SRC, 'eh', plug)
    er = {k: v for k, v in a64order.scan(ehs).rows if v}
    check('unwinder builtins: no ORD-T error, every access certified', er, {})
    edis = run(['aarch64-linux-gnu-objdump', '-d', '--no-show-raw-insn', ehs]).stdout
    check('init_dwarf_reg_sizes: the shared table is read ldarb and written stlrb, the builtin fills a private copy',
          ('ldarb' in edis.split('<_Z4initv>:')[1].split('\n\n')[0], 'stlrb' in edis.split('<_Z4initv>:')[1].split('\n\n')[0]), (True, True))
    check('bit-field store certified', not any(k.startswith('_Z3stbv') or k.startswith('_Z3ldbv') for k in rs), True)
    dis = run(['aarch64-linux-gnu-objdump', '-d', '--no-show-raw-insn', po]).stdout
    esc = dis.split('<_Z3escv>:')[1].split('\n\n')[0]
    check('plugin: the writes to the escaped local are stlr', 'stlr\twzr' in esc and 'stlr\tw0' in esc, True)
    retf = dis.split('<_Z3retv>:')[1].split('\n\n')[0]
    check('plugin: the return slot aggregate is stored unit-wise, 5 stlr and no plain store of it', (retf.count('stlr'), 'stp\tx2, x3, [x8]' in retf), (5, False))
    r, j = gate(po)
    check('gate on plugin object is red for the two documented functions', (r.returncode, j['status']), (2, 'red'))
    r, j = gate(plain)
    check('no plugin, no section: red', (r.returncode, j['status']), (2, 'red'))
    check('no plugin: object item named', plain in j['uncertified'] and '%s:_Z3escv' % plain in j['uncertified'], True)
    check('no plugin: plain accesses to globals named', any(k.startswith(plain + ':_Z3retv+0x') for k in j['uncertified']), True)

shutil.rmtree(tmp, ignore_errors=True)
if fails:
    print('FAIL\n' + '\n'.join(fails))
    sys.exit(1)
print('ok')
