#!/usr/bin/env python3
"""x87emit: the EMITTED text of an x87 instruction as a ctrans function (the tie of a Form to what the lifter emits).

One path: the text is what fnvlift.lift emits for a synthetic image of the key's bytes plus RET (the same Image / lift entry as fnvlift/tests); the instruction's own lines are those between its
TRACE line and the label of the next instruction.  They are wrapped as `static void frag(void){ DECL; ... }` (DECL is the lifter's own local-declaration macro of rt/fnvl.h) and preprocessed with the
runtime headers' macros exactly as the helper closure is (FNVNX_PP's stock-cpp variant: mkdev); ctrans then executes `frag` on the symbolic state struct cpu.  Nothing of the text is restated or hand-translated.
"""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules
import os, re, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]
RT = os.path.join(os.path.dirname(HERE), 'rt')
BASE = 0x401000
_CONF = [False]


def configure():
    """the lifter's inputs, as the byte-buffer tests configure them (fnvlift/tests/_inputs.py): the SDM opcode-row table is the produced input SDMROWS (FNVNX_INPUT_SDMROWS)"""
    if _CONF[0]: return
    import fnvnx.lift.fnvlift.tests._inputs as _inputs
    _inputs.configure(); _CONF[0] = True


def lifted(raw):
    """the whole function the lifter emits for the instruction bytes RAW followed by `ret`: the static-stack prologue, the instruction's lines and the epilogue (the emitter's own text)"""
    configure()
    import fnvnx.lift.fnvlift as L
    from fnvnx.lift.fnvlift.image import Image
    code = bytes(raw) + b'\xc3'
    t, _ = L.lift(Image.from_bytes(code, BASE), BASE, len(code))
    return t


def emitted(raw):
    """the instruction's emitted lines for the instruction bytes RAW (a key)"""
    configure()
    import fnvnx.lift.fnvlift as L
    from fnvnx.lift.fnvlift.image import Image
    code = bytes(raw) + b'\xc3'
    t, _ = L.lift(Image.from_bytes(code, BASE), BASE, len(code))
    ls = t.split('\n'); i = next(j for j, l in enumerate(ls) if l.startswith('TRACE(')) + 1
    nxt = '\nL_%08x: ;' % (BASE + len(raw))
    j = t.index(nxt); k = t.index('\n'.join(ls[:i])) + len('\n'.join(ls[:i])) + 1
    return t[k:j]


def _sc(t):
    return re.sub(r'//[^\n]*', '', re.sub(r'/\*.*?\*/', ' ', t, flags=re.S))


def boundary():
    """-> (macro definition lines, C text of the accessor functions): the guest-memory access macros of rt/fnvl.h (M8..MF64, MK8..MKF64) rewritten to calls, and the accessor classes they name read as C functions (ctasm: the operator bodies are the definition; ctrans executes the asm they hold)"""
    import fnvnx.lift.proof.ctasm as ctasm
    t = _sc(open(os.path.join(RT, 'fnvl.h')).read()); t = re.sub(r'#include[^\n]*', '', t)
    o = subprocess.run(['cpp', '-E', '-P', '-x', 'c++', '-DFNVL_TYPES=1', '-'], input=t, capture_output=True, text=True).stdout
    defs = ctasm.macro_defs(t)
    uses = set()
    for m in re.finditer(r'^[ \t]*#define[ \t]+(\w+)\(([^)]*)\)[ \t]*(.*)$', t.replace('\\\n', ' '), re.M):
        if m.group(1) in defs: uses |= ctasm.rewrite_names(m.group(3))
    done, code = set(), ''
    while uses - done:
        batch = uses - done; done |= batch
        code += ctasm.accessor_c(o, batch)
        for cls, ta in batch:      # GMF is over GM<T>, GMKF over GMK<T,A> (the class text names them)
            if cls == 'GMF': uses.add(('GM', (ta[1],)))
            if cls == 'GMKF': uses.add(('GMK', (ta[1],)))
    return ['#define %s(%s) %s' % (k, a, b) for k, (a, b) in defs.items()], code


def header_macros(body, have):
    """the #define lines of rt/fnvl.h for every macro the text BODY names (transitively) that is not yet defined (HAVE): the header's own macro table is the source, nothing is listed here"""
    t = _sc(open(os.path.join(RT, 'fnvl.h')).read()).replace('\\\n', ' ')
    tab = {}
    for m in re.finditer(r'(?m)^[ \t]*#define[ \t]+(\w+)(\([^)]*\))?[ \t]*(.*)$', t): tab[m.group(1)] = m.group(0).strip()
    out, todo = [], set(re.findall(r'\w+', body))
    done = set(have)
    while todo:
        n = todo.pop()
        if n in done or n not in tab: continue
        done.add(n); out.append(tab[n]); todo |= set(re.findall(r'\w+', tab[n]))
    return '\n'.join(out)


def unit(body, whole=False):
    """the preprocessed unit: the runtime's type prelude, x87.h, fnvl_x87.h, x87.c (as FNVNX_PP's closure) and `frag` with BODY"""
    rd = lambda n: open(os.path.join(RT, n)).read()
    pre = '\n'.join(l for l in _sc(rd('fnvl.h')).split('\n') if l.startswith(('typedef', 'struct fv ', 'struct cpu ', 'struct fnvl_nframe;')))
    decl = next(l for l in rd('fnvl.h').split('\n') if l.startswith('#define DECL '))
    segcs = '\n'.join(re.findall(r'^#define \w+ (?:0x[0-9a-fA-F]+|\d+)u?\s*$', _sc(rd('fnvl.h')), re.M) and [l for l in _sc(rd('fnvl.h')).split('\n') if re.fullmatch(r'#define \w+ (?:0x[0-9a-fA-F]+|\d+)u?\s*', l)])      # the integer-constant macros of the header
    txt = pre + '\n' + '\n'.join(_sc(rd(n)) for n in ('x87.h', 'fnvl_x87.h', 'x87.c'))
    txt = '#define FNVL_RA_T u32\n' + re.sub(r'#include[^\n]*', '', txt)       # the writer's view of struct cpu::ra (rt/fnvl.h: FNVL_PRELUDE_H)
    txt = re.sub(r'unsigned\s+__int128', 'u128', txt)
    mdefs, acc = boundary()
    txt += '\n' + '\n'.join(mdefs) + '\n' + acc
    if body is not None and whole:       # BODY is a whole lifted function (the emitter's own text): its macros from the header's table, then the function
        have = set(re.findall(r'(?m)^#define\s+(\w+)', txt + '\n' + decl + '\n' + segcs))
        txt += '\n' + header_macros(body, have) + '\n' + decl + '\n' + segcs + '\n' + re.sub(r'^extern "C" ', '', body)
    elif body is not None:
        have = set(re.findall(r'(?m)^#define\s+(\w+)', txt)) | set(re.findall(r'(?m)^#define\s+(\w+)', decl + '\n' + segcs))
        txt += '\n' + header_macros(body, have)       # the macros of rt/fnvl.h the emitted text names (and, transitively, their bodies): found by name over the header's #define table
    if body is not None and not whole: txt += '\n' + decl + '\n' + segcs + '\nstatic void frag(void){ DECL;\n' + body + '\n}\n'
    p = subprocess.run(['cpp', '-E', '-x', 'c', '-DFNVL_TYPES=1', '-'], input=txt, capture_output=True, text=True)
    if p.returncode: raise SystemExit(p.stderr[:800])
    return p.stdout




# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# Execution of the emitted fragment under ctrans
def _frag(raw, top):
    """-> (machine, result) of `frag` for the instruction bytes RAW on a symbolic state with TOP = top (0..7), the x87 sticky flags and the owner clear at entry; the registers c.fx[i] are free (kind, v, e)"""
    import z3, fnvnx.lift.proof.ctrans as ctrans, fnvnx.lift.proof.x87carrier as XC
    body = lifted(raw); u = unit(body, whole=True)
    fd, p = tempfile.mkstemp(suffix='.i'); os.write(fd, u.encode()); os.close(fd)
    try:
        mc, src, refused = ctrans.load([p], XC.MACRO_FILES, None)
    finally:
        os.unlink(p)
    fn = 'L_%08x' % BASE
    if fn not in mc.funcs: raise SystemExit('%s not parsed: %s' % (fn, refused.get(fn)))
    mc.G.pop('c', None); mc.cpu(); c = mc.G['c']
    c['ftop'] = ctrans.V(z3.BitVecVal(top, 32), ('i', 32, 0)); c['fex'] = ctrans.V(z3.BitVecVal(0, 32), ('i', 32, 0))
    r = mc.apply(fn, [])
    return mc, r


def kind_domain(K):
    """the constructors' postcondition over every register of struct cpu (x87kind.fvvar's invariant), on the machine's own input variables c.fx[i].k/v/e"""
    import z3
    inv = []
    for i in range(8):
        v, e, k = z3.BitVec('c.fx[%d].v' % i, 64), z3.BitVec('c.fx[%d].e' % i, 32), z3.BitVec('c.fx[%d].k' % i, 32)
        inv.append(K.kind_invariant(v, e, k))
    return z3.And(*inv)


def emitted_de(raw, top):
    """the DE flag (0 or 1) that the emitted text raises, a z3 term: c->fex bit 1 | the live host FPSR flag the x87 family owns (fnvl_fp_x87_flags: IDC bit 7 -> DE), with the flags clear at entry"""
    import z3
    mc, r = _frag(raw, top)
    fex = mc.G['c']['fex'].e; fpsr = r.fpsr
    live = z3.If(z3.Extract(25, 24, fex) == 1, z3.Extract(7, 7, fpsr), z3.BitVecVal(0, 1)) if fpsr.size() >= 8 else z3.BitVecVal(0, 1)
    return z3.Extract(1, 1, fex) | live, mc, r


def class_reps(paths):
    """{class: representative key bytes}: x87class's classes over the shape files PATHS, the first key of each (the universe is the distinct keys of the files)"""
    import fnvnx.lift.proof.x87class as x87class
    from fnvnx.lift.proof import shape
    configure()
    dec = shape.decoder(); seen, reps = set(), {}
    for p in paths:
        for ln in open(p):
            if ln[0] == '#' or not ln.strip(): continue
            k = ln.split('\t')[0]
            if k in seen: continue
            seen.add(k)
            try: ins = next(dec.disasm(shape.rep_bytes(k), 0))
            except Exception: continue
            if shape.family(ins) != 'x87': continue
            raw = bytes(ins.bytes); reps.setdefault(x87class.x87_class(raw), raw)
    return reps


def de_row(raw, budget_ms=120000, tops=(3,)):
    """-> {spec, emitted, note}: whether the spec can raise DE for the instruction bytes RAW (x87spec Out.raised bit 1 satisfiable under the spec's own precondition), and whether the EMITTED text can raise DE for any state of the kind domain"""
    import z3, fnvnx.lift.proof.x87form as XF, fnvnx.lift.proof.x87kind as K
    configure()
    row = {}
    try:
        d, o, pcs, ef, m = XF.run(*XF.spec_key(raw))
        s = z3.Solver(); s.set('timeout', budget_ms); s.add(o.pre, z3.Extract(1, 1, o.raised) == 1)
        row['spec'] = str(s.check())
    except Exception as e:
        row['spec'] = 'error: %s' % str(e)[:80]
    try:
        res = 'unsat'
        for t in tops:
            de, mc, r = emitted_de(raw, t)
            s = z3.Solver(); s.set('timeout', budget_ms); s.add(kind_domain(K), de == 1)
            a = str(s.check())
            if a != 'unsat': res = a
            if a == 'sat': break
        row['emitted'] = res
    except SystemExit as e:
        row['emitted'] = 'refused'; row['note'] = str(e)[:160]
    except Exception as e:
        row['emitted'] = 'refused'; row['note'] = '%s %s' % (type(e).__name__, str(e)[:160])
    return row


if __name__ == '__main__':
    if sys.argv[1] == 'reps':
        for c, r in sorted(class_reps(sys.argv[2:]).items(), key=lambda kv: kv[0][:2] + (kv[0][2] or 0, kv[0][3] or 0)): print(c[0], c[1], c[2], c[3], r.hex())
    elif sys.argv[1] == 'pp':      # the helper closure's preprocessed unit of this checkout's rt (the stand-in of the recipe PP stage's .i for FNVNX_PP)
        open(sys.argv[2], 'w').write(unit(None))
    elif sys.argv[1] == 'de1':
        print(de_row(bytes.fromhex(sys.argv[2])))
