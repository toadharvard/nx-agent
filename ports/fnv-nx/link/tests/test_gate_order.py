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
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

TOOL = os.path.join(HERE, '..', '..', 'toolchain', 'order', 'fnvnx_order.cc')
CXX = 'aarch64-linux-gnu-g++-13'
FLAGS = ['-mabi=ilp32', '-std=gnu++17', '-O2', '-fwrapv', '-fno-strict-aliasing', '-march=armv8-a+crc+crypto', '-mtune=cortex-a57',
         '-fno-stack-protector', '-fno-stack-clash-protection', '-mno-outline-atomics', '-fno-exceptions', '-fno-rtti']
PLUGIN_ARGS = ['args=rtl', 'object=msvc', 'operator=rtl', 'assign=msvc']
fails = testcase.FAILED
tmp = tempfile.mkdtemp(prefix='gate-order-')




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


def compile_cxx(src, name, plugin):
    p = os.path.join(tmp, name + '.cpp')
    o = os.path.join(tmp, name + '.o')
    open(p, 'w').write(src)
    pl = ['-fplugin=' + plugin] + ['-fplugin-arg-fnvnx_order-' + a for a in PLUGIN_ARGS] if plugin else []
    r = run([CXX] + FLAGS + pl + ['-c', p, '-o', o])
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
case('parse empty', got=P(b'', 'x'), want=[])
case('parse lines', got=P(b'a\t\nb\tdynamic\nc\t-16:12 -4:4 0:8\n', 'x'), want=[('a', ()), ('b', 'dynamic'), ('c', ((-16, 12), (-4, 4), (0, 8)))])
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
case('ok functions certify', got=[(k, v) for k, v in s.rows if v], want=[])
case('rules seen', got=sorted(s.stats), want=sorted(['ordered', 'frame', 'fenced-load', 'fenced-store', 'readonly', 'linker', 'literal', 'frame-escape']))
r, j = gate(good)
case('gate green', got=(r.returncode, j['status'], j['certified'] == j['universe'], j['uncertified']), want=(0, 'green', True, []))

for n in BADS:
    o = assemble(n, {n: FUNCS[n]}, {n: REC[n]})
    rs = red(o)
    case('bad', got=len(rs) >= 1 and all(k.startswith(n + '+0x') for k in rs), want=True, key=n + ' is red')
bad_all = assemble('bad', {n: FUNCS[n] for n in BADS}, {n: REC[n] for n in BADS})
rs = red(bad_all)
case('exact: plain store', got=rs.get('bad_plain_store+0x0'), want='plain-store')
case('exact: plain load', got=rs.get('bad_plain_load+0x0'), want='plain-load')
case('exact: dynamic sp', got=rs.get('bad_dynsp+0x4'), want='plain-store')
case('exact: no frame pointer after overwrite', got=rs.get('bad_x29+0xc'), want='plain-store')
case('exact: second load after GOT', got=rs.get('bad_second_load+0x8'), want='plain-load')
case('exact: weak atomic', got=rs.get('bad_lse+0x0'), want='weak-atomic')
case('exact: plain sp store to an escaped slot', got=rs.get('bad_escape_plain+0x14'), want='plain-store')
case('exact: plain x29 store to an escaped slot', got=rs.get('bad_escape_plain_x29+0x18'), want='plain-store')
case('only that store in the escaped twins', got=sorted(k for k in rs if k.startswith('bad_escape_plain')),
      want=['bad_escape_plain+0x14', 'bad_escape_plain_x29+0x18'])
case('exact: frame address out of every range', got=rs.get('bad_escape_unrecorded+0xc/escape'), want='frame-escape-unrecorded')
case('exact: frame address of an unknown stack pointer', got=rs.get('bad_escape_unknown+0x8/escape'), want='frame-escape-unknown')
case('only bad functions named', got=sorted({k.split('+')[0] for k in rs}), want=sorted(BADS))
r, j = gate(bad_all)
case('gate red', got=(r.returncode, j['status']), want=(2, 'red'))
case('gate lists the plain store', got='%s:bad_plain_store+0x0' % bad_all in j['uncertified'], want=True)
case('uncertified sorted', got=j['uncertified'], want=sorted(j['uncertified']))
r2, j2 = gate(bad_all, good)
r3, j3 = gate(good, bad_all)
case('deterministic', got=j2, want=j3)
# the same hand-assembled escape with the ordered store and with a record that does not cover the slot
o = assemble('esc_norec', {'f': FUNCS['ok_escape']}, {'f': ''})
case('escape of a slot the record lacks is red', got=red(o), want={'f+0x10/escape': 'frame-escape-unrecorded'})
o = assemble('esc_rec', {'f': FUNCS['ok_escape']}, {'f': '-8:4'})
case('escape into a recorded slot is green', got=red(o), want={})
o = assemble('rec_overlap', {'f': ['sub sp, sp, #16', 'str w0, [sp, #4]', 'str w0, [sp, #12]', 'add sp, sp, #16', 'ret']}, {'f': '-4:4'})
case('plain store beside a recorded slot is F, onto it is not', got=red(o), want={'f+0x8': 'plain-store'})
o = assemble('rec_above', {'f': ['ldr w0, [sp, #8]', 'ret']}, {'f': ''})
case('access above the CFA (incoming stack argument) is not F', got=red(o), want={'f+0x0': 'plain-load'})
o = assemble('rec_pair', {'f': ['sub sp, sp, #16', 'stp x0, x1, [sp, #0]', 'add sp, sp, #16', 'ret']}, {'f': '-12:4'})
case('a pair store sizes 16 bytes', got=red(o), want={'f+0x4': 'plain-store'})

# frame record problems
o = assemble('noline', {'fa': FUNCS['ok_frame'], 'fb': FUNCS['ok_frame']}, {'fa': ''})
case('function without a line: its frame accesses are not F either', got=sorted(red(o)), want=sorted(
    ['fb'] + ['fb+0x%x' % x for x in (0, 8, 0xc, 0x14, 0x18)]))
case('function without a line is named', got=red(o).get('fb'), want='no-frame-record')
case('function with a line is not', got=[k for k in red(o) if k.startswith('fa')], want=[])
o = assemble('nosec', {'fa': FUNCS['ok_frame']}, None)
case('no section: object and function', got=(red(o).get(''), red(o).get('fa')), want=('no-frame-section', 'no-frame-record'))
o = assemble('badsec', {'fa': FUNCS['ok_frame']}, 'fa\\t1:0\\n')
case('malformed section', got=(red(o).get(''), red(o).get('fa')), want=('bad-frame-section', 'no-frame-record'))
o = assemble('dynrec', {'fa': FUNCS['ok_frame']}, {'fa': 'dynamic'})
case('dynamic record: no frame access is F', got=sorted(k for k in red(o) if '+' in k), want=['fa+0x0', 'fa+0x14', 'fa+0x18', 'fa+0x8', 'fa+0xc'])
o = assemble('tworec', {'fa': FUNCS['ok_frame']}, {'fa': ''}, '')
o2 = assemble('dup', {'fa': FUNCS['ok_frame']}, 'fa\\t\\nfa\\t\\n')
case('two records for one function', got=red(o2).get('fa'), want='no-frame-record')
alias = '\t.global fb\n\t.set fb, fa\n'
o = assemble('alias_ok', {'fa': FUNCS['ok_frame']}, {'fa': '', 'fb': ''}, alias)
case('alias with the same record', got=red(o), want={})
o = assemble('alias_bad', {'fa': FUNCS['ok_frame']}, {'fa': '-8:4', 'fb': ''}, alias)
case('alias with another record', got=red(o).get('fa'), want='frame-record-mismatch')

# an undecodable word inside a function, and a function symbol of size 0
o = assemble('undec', {}, {'u1': '', 'z0': ''}, '\t.global u1\n\t.type u1,%function\nu1:\n\t.inst 0x00100000\n\tret\n\t.size u1,.-u1\n'
             '\t.global z0\n\t.type z0,%function\nz0:\n\tldar w0, [x0]\n\tret\n')
case('undecodable word and size-0 symbol', got={k: v for k, v in red(o).items()}, want={'u1+0x0': 'undecodable', 'z0': 'size0'})

junk = os.path.join(tmp, 'junk.o')
open(junk, 'wb').write(b'not an object')
r, j = gate(junk)
case('unreadable object', got=(r.returncode, j['uncertified']), want=(2, [junk]))

# ---- C++ compiled with the order plugin
SRC = r'''
extern "C" void sink(void*); extern "C" void sink2(int); extern "C" void ext(void);
struct S { int a, b, c, d, e; };
struct B { unsigned a:3, b:5; };
S gs, gs2; B gb; int g, *gptr;
int nonesc(int x) { sink2(1); return x + 1; }
int esc() { int x = 0; gptr = &x; x = 5; sink2(x); return x; }
int esc2() { int x[4]; gptr = x; for (int i = 0; i < 4; i++) x[i] = i; sink(x); return x[2]; }
S ret() { return gs; }
int agg() { S l = gs2; return l.a + l.e; }
void stb() { gb.b = 3; }
int ldb() { return gb.b; }
int cpy() { gs = gs2; return g; }
void takes(S s);
void pass() { takes(gs); }
char* dyn(int n) { char* p = (char*)__builtin_alloca(n); sink(p); p[1] = 2; return p; }
'''
plug = build_plugin()
if plug:
    po = compile_cxx(SRC, 'plug', plug)
    plain = compile_cxx(SRC, 'plain', None)
    rows = a64order.scan(po).rows
    rs = {k: v for k, v in rows if v}
    fr = framesec.read(po)
    rec = dict(fr)
    case('record: every function has one line', got=len(rec), want=len(fr))
    case('record: non-escaped local has none', got=rec.get('_Z6nonesci'), want=())
    case('record: escaped scalar', got=rec['_Z3escv'], want=((-4, 4),))
    case('record: escaped array', got=rec['_Z4esc2v'], want=((-16, 16),))
    case('record: alloca is dynamic', got=rec['_Z3dyni'], want='dynamic')
    names = sorted({k.split('+')[0].split('/')[0] for k in rs})
    case('plugin output: red functions are exactly the documented ones', got=names, want=['_Z3dyni', '_Z4passv'])
    case('alloca: its frame accesses are not F', got=any(k.startswith('_Z3dyni+') and v == 'plain-store' for k, v in rs.items()), want=True)
    case('by-value aggregate argument copy is an escape no record covers',
          got=[k.split('+')[0] for k, v in rs.items() if v == 'frame-escape-unrecorded'], want=['_Z4passv'])
    ok = {k for k, v in rows if not v}
    case('non-escaped local: frame accesses certified', got=any(k.startswith('_Z6nonesci+') for k in ok), want=True)
    case('escaped local: certified and ordered', got=not any(k.startswith('_Z3escv') for k in rs) and any(k.startswith('_Z3escv+') for k in ok), want=True)
    case('escaped array: certified', got=not any(k.startswith('_Z4esc2v') for k in rs), want=True)
    case('sret aggregate store certified', got=not any(k.startswith('_Z3retv') for k in rs), want=True)
    case('aggregate load certified', got=not any(k.startswith('_Z3aggv') or k.startswith('_Z3cpyv') for k in rs), want=True)
    case('bit-field store certified', got=not any(k.startswith('_Z3stbv') or k.startswith('_Z3ldbv') for k in rs), want=True)
    dis = run(['aarch64-linux-gnu-objdump', '-d', '--no-show-raw-insn', po]).stdout
    esc = dis.split('<_Z3escv>:')[1].split('\n\n')[0]
    case('plugin: the writes to the escaped local are stlr', got='stlr\twzr' in esc and 'stlr\tw0' in esc, want=True)
    retf = dis.split('<_Z3retv>:')[1].split('\n\n')[0]
    case('plugin: the return slot store is preceded by dmb ish', got=retf.index('dmb\tish\n') < retf.index('stp\tx2, x3, [x8]'), want=True)
    r, j = gate(po)
    case('gate on plugin object is red for the two documented functions', got=(r.returncode, j['status']), want=(2, 'red'))
    r, j = gate(plain)
    case('no plugin, no section: red', got=(r.returncode, j['status']), want=(2, 'red'))
    case('no plugin: object item named', got=plain in j['uncertified'] and '%s:_Z3escv' % plain in j['uncertified'], want=True)
    case('no plugin: plain accesses to globals named', got=any(k.startswith(plain + ':_Z3retv+0x') for k in j['uncertified']), want=True)

shutil.rmtree(tmp, ignore_errors=True)
if fails:
    print('FAIL\n' + '\n'.join(fails))
    sys.exit(1)
print('ok')
