#!/usr/bin/env python3
"""rtasm - the memory-ordering class check of the built guest runtime's assembly (orchestrator decision of 2026-11-15, CLAUDE.md "memory
ordering of runtime calls"; docs/ORDERING.md).

    judge(objs, ...)  ->  Report        every memory-access instruction of every assembled object of the runtime, in exactly one class
    python3 rtasm.py --ext DIR --dirs D.. --names N.. [--startfiles F..] [--endfiles F..] --jmpbuf-bytes N [--out report.json]

UNIVERSE (one rule over the runtime, never a file list): every member of every archive of the runtime (the `link.libs` of the recipe: the
archives `lib<name>.a` found in `dirs`, first dir wins, as the gcc driver searches them) and every start/end file. ASSEMBLED = the object
has no `.comment` section. The compiler writes `.comment` (the `.ident` directive of every translation unit it compiles) and the assembler
never does; an object that carries no producer string was not produced by a compiler front end, so it is assembly (.S or .s). Objects that
carry it are compiled code: the plugins order them, this check does not look at them.

CLASSES. Every memory-access instruction of an assembled object (a64order.Func: capstone decode, the frame dataflow, the ordered forms) is in
exactly one class; anything else is a violation, with object, function, offset and instruction. There is no allow-list.
 D1   touches only the frame or the jmp_buf: the access goes through the frame of the routine (sp/x29, or a register that holds a frame
      address) and no escape of a frame address reaches it on any path from the entry (rule D, a64order), or through the argument register of a
      routine that takes a jmp_buf (the routines the runtime's setjmp.h declares with a jmp_buf parameter), x0 of the entry not rewritten on any
      path to the access, with a constant offset inside sizeof(jmp_buf).
 BT   before threads: the access sits in code that only the entry reaches before any thread can exist (see BEFORE-THREADS below).
 KC   kernel call: a store of a value the kernel call returned in x1..x7 (the wrapper delivering its documented outputs through its out
      pointer), the register not rewritten since the `svc`.
 AT   atomic with its documented ordering: an acquire/release form (ldar/stlr/ldaxr/stlxr/casal...), or a relaxed or one-sided form of a
      routine whose own name documents that ordering by its suffix (libgcc lse.S: `__aarch64_<op><size>_<relax|acq|rel|acq_rel|sync>`), the
      instruction's acquire/release flags equal to the suffix's.
 RO   a word the linker fills or a read-only section holds (a64order rule L: GOT/TLS slot, a relocation to a symbol of an allocated section
      without SHF_WRITE, a literal pool of the routine's own read-only section, a base register that holds such an address).
BEFORE-THREADS (proof, over the whole universe). The call graph has a node per function symbol and an edge per direct branch or call (a
relocation on a `bl`/`b`, or an in-object target). A node is BEFORE-THREADS iff
  (1) it is an entry (a global function of a start file) or every reference to it anywhere in the universe is a call site that is itself clean,
      in a node that is BEFORE-THREADS, and no reference is a relocation of a data section or an address materialisation (adrp/add, ldr);
  (2) it is reachable from an entry through clean call sites.
A call site is CLEAN iff no thread-possible call reaches it on the CFG of its function. A call is thread-possible iff its target is outside the
universe (the code after it is the program's), indirect, or a node that holds a thread-possible call (least fixpoint). An access is BT iff its node
is BEFORE-THREADS and no thread-possible call reaches it. The sets are the greatest fixpoint of (1) inside the reachability of (2).
PREMISE named in the report, not decided here: a global symbol of a BEFORE-THREADS node may be referenced by code outside the universe (the product's
objects); the link-level reference check owns that.
Pure: a function of the object bytes, the jmp_buf size and the routine names."""
import argparse
import collections
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import a64obj  # noqa: E402
import a64order as A  # noqa: E402
import relocs  # noqa: E402
from capstone import arm64_const as K  # noqa: E402

JB_BASE = 1 << 20          # the synthetic frame offset that x0 of a jmp_buf routine holds at entry (far above any real frame)
SVC_OUT = frozenset(range(1, 8))   # x1..x7: the registers a Horizon svc returns its outputs in (libnx svc.h, one wrapper per call)
MODELS = {'relax': (False, False), 'acq': (True, False), 'rel': (False, True), 'acq_rel': (True, True), 'sync': (True, True)}
MODEL_RE = re.compile(r'_(relax|acq_rel|acq|rel|sync)$')
RMW_RE = re.compile(r'^(CASP?|SWP|LD(?:ADD|CLR|EOR|SET|SMAX|SMIN|UMAX|UMIN))(A?)(L?)([BH]?)$')
ST_RMW_RE = re.compile(r'^ST(?:ADD|CLR|EOR|SET|SMAX|SMIN|UMAX|UMIN)(L?)([BH]?)$')
CLASSES = ('D1', 'BT', 'KC', 'AT', 'RO')


Access = collections.namedtuple('Access', 'obj func off text cls detail')


def jmpbuf_routines(header_text):
    """the routines a setjmp.h declares with a jmp_buf (or sigjmp_buf) first parameter: `name (jmp_buf ...)` / `name (sigjmp_buf ...)`. Read from the
    runtime's own header, never listed here."""
    return sorted(set(re.findall(r'\b([A-Za-z_]\w*)\s*\(\s*(?:jmp_buf|sigjmp_buf)\b', header_text)))


def exclusive_ordering(i):
    """(acquire, release, is_load_like) of an atomic or exclusive instruction from its mnemonic, None for any other instruction"""
    n = A._name(i)
    if n in ('LDXR', 'LDXRB', 'LDXRH', 'LDXP'):
        return (False, False, True)
    if n in ('LDAXR', 'LDAXRB', 'LDAXRH', 'LDAXP', 'LDAR', 'LDARB', 'LDARH'):
        return (True, False, True)
    if n in ('STXR', 'STXRB', 'STXRH', 'STXP'):
        return (False, False, False)
    if n in ('STLXR', 'STLXRB', 'STLXRH', 'STLXP', 'STLR', 'STLRB', 'STLRH'):
        return (False, True, False)
    m = RMW_RE.match(n)
    if m:
        return (bool(m.group(2)), bool(m.group(3)), None)
    m = ST_RMW_RE.match(n)
    if m:
        return (False, bool(m.group(1)), None)
    return None


class RtFunc(A.Func):
    """a64order.Func whose entry state may hold the jmp_buf argument: x0 as a frame address of the synthetic region JB_BASE"""
    entry_regs = ()


def svc_taint(f):
    """{instruction index: frozenset of registers that hold a value returned by the last svc on EVERY path to it}: forward must-analysis, join =
    intersection, a write to a register removes it; `svc` (a call for the frame dataflow) sets x1..x7"""
    n = len(f.ins)
    state = [None] * n
    if not n:
        return state
    state[0] = frozenset()
    work, queued = collections.deque([0]), {0}
    while work:
        k = work.popleft()
        queued.discard(k)
        cur = state[k]
        i = f.ins[k]
        out = set(cur) - set(i.wr)
        if i.id == K.ARM64_INS_SVC:
            out = set(SVC_OUT)
        out = frozenset(out)
        for s in f.succ[k]:
            nw = out if state[s] is None else state[s] & out
            if nw != state[s]:
                state[s] = nw
                if s not in queued:
                    queued.add(s)
                    work.append(s)
    return state


def reach_from(f, sources):
    """the set of instruction indices reachable from any of SOURCES by at least one CFG edge"""
    seen, stack = set(), []
    for k in sources:
        stack.extend(f.succ[k])
    while stack:
        k = stack.pop()
        if k in seen:
            continue
        seen.add(k)
        stack.extend(f.succ[k])
    return seen


class Obj:
    """one runtime object: label, parsed form, assembled flag"""
    def __init__(self, label, data, startfile):
        self.label, self.startfile = label, startfile
        fd, p = tempfile.mkstemp(prefix='rtasm-', suffix='.o')
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        try:
            self.o = a64obj.read(p)
        finally:
            os.unlink(p)
        self.assembled = not any(s.name == '.comment' for s in self.o.sections)


def universe(blobs):
    """[(label, bytes, startfile)] -> [Obj]; a member that is no AArch64 ILP32 relocatable object is an error"""
    return [Obj(lb, d, sf) for lb, d, sf in blobs]


def runtime_blobs(ext_root, dirs, names, startfiles=(), endfiles=()):
    """the universe of a recipe's `link.libs`: start files, then for each lib name the first `lib<name>.a` in DIRS (all members), then end files"""
    out = []
    for f in startfiles:
        p = os.path.join(ext_root, f)
        out.append((f, open(p, 'rb').read(), True))
    for nm in names:
        for d in dirs:
            p = os.path.join(ext_root, d, 'lib%s.a' % nm)
            if os.path.exists(p):
                for mem, _, body in relocs.ar_members(p):
                    out.append(('%s/lib%s.a:%s' % (d, nm, mem), body, False))
                break
        else:
            raise ValueError('lib%s.a is in none of %s' % (nm, dirs))
    for f in endfiles:
        out.append((f, open(os.path.join(ext_root, f), 'rb').read(), False))
    return out


MERGED = ('.init', '.fini')   # gABI 4.? special sections: the input sections of these names of all objects, in link order, are ONE function body (`_init`, `_fini`)


def merged_object(objs, name):
    """a synthetic Obj holding the concatenation of every object's executable section NAME (link order), its relocations and the symbols they name, or None"""
    parts = [(ob, si) for ob in objs for si, sec in enumerate(ob.o.sections) if sec.name == name and sec.flags & a64obj.SHF_EXECINSTR]
    if not parts:
        return None
    m = a64obj.Obj('<%s>' % name)
    m.sections.append(a64obj.Sec(0, '', 'SHT_NULL', 0, 0, b''))
    code = b''
    for ob, si in parts:
        code += ob.o.sections[si].data
    m.sections.append(a64obj.Sec(1, name, 'SHT_PROGBITS', parts[0][0].o.sections[parts[0][1]].flags, len(code), code))
    base = 0
    rel = {}
    secmap = {}
    for ob, si in parts:
        o = ob.o
        smap = {si: 1}
        for j, sec in enumerate(o.sections):          # the other sections are kept so that a relocation's target keeps its flags
            if j != si and j not in smap:
                smap[j] = len(m.sections)
                m.sections.append(sec._replace(index=len(m.sections)))
        symmap = {}
        for y in o.symbols:
            symmap[y.index] = len(m.symbols)
            sh = None if y.shndx is None else smap[y.shndx]
            val = y.value + (base if y.shndx == si else 0)
            m.symbols.append(a64obj.Sym(len(m.symbols), y.name, val, y.size, y.type, y.bind, sh))
        for off, r in o.relocs.get(si, {}).items():
            rel[off + base] = a64obj.Reloc(symmap[r.sym], r.type, r.addend)
        for j, rels in o.relocs.items():
            if j != si:
                m.relocs.setdefault(smap[j], {}).update({off: a64obj.Reloc(symmap[r.sym], r.type, r.addend) for off, r in rels.items()})
        for j, mp in o.mapping.items():
            if j == si:
                m.mapping.setdefault(1, []).extend((v + base, k) for v, k in mp)
        base += o.sections[si].size
    m.relocs[1] = rel
    m.mapping = {k: sorted(v) for k, v in m.mapping.items()}
    fns = {}
    for y in m.symbols:
        if y.shndx == 1 and y.type in ('STT_FUNC', 'STT_GNU_IFUNC'):
            fns.setdefault((1, y.value, y.size), []).append(y.name)
    m.functions = [(k[0], k[1], k[2], sorted(v)) for k, v in sorted(fns.items())]
    return m, any(ob.assembled for ob, si in parts), any(ob.startfile for ob, si in parts)


def pulled(objs, product_symbols):
    """the labels of the objects a linker takes: start/end files and loose objects always; an archive member iff it defines a symbol that is
    undefined so far (archives in universe order, repeated to a fixpoint: the `--start-group` rule). PRODUCT_SYMBOLS (defined, undefined) names of the
    product's own objects."""
    defd, undef = set(product_symbols[0]), set(product_symbols[1])
    take = {ob.label for ob in objs if ':' not in ob.label}

    def globals_of(ob):
        d = {y.name for y in ob.o.symbols if y.shndx is not None and y.bind in ('STB_GLOBAL', 'STB_WEAK') and y.name}
        u = {y.name for y in ob.o.symbols if y.shndx is None and y.name and y.bind in ('STB_GLOBAL', 'STB_WEAK')}
        return d, u
    for ob in objs:
        if ob.label in take:
            d, u = globals_of(ob)
            defd |= d
            undef |= u
    ch = True
    while ch:
        ch = False
        for ob in objs:
            if ob.label in take:
                continue
            d, u = globals_of(ob)
            if d & (undef - defd):
                take.add(ob.label)
                defd |= d
                undef |= u
                ch = True
    return take


def bodies(ob):
    """[(RtFunc, names, kind)] of every executable body of an object: built, with the dataflow run"""
    out = []
    fb, extra = A.functions_of(ob.o)
    for si, a, b, names, kind in fb:
        if (b - a) % 4 or a % 4:
            out.append((None, names, 'odd-code-range'))
            continue
        out.append((si, a, b, names, kind))
    return out, extra


def text_of(o, shndx, off):
    for c in A._MD.disasm(o.sections[shndx].data[off:off + 4], off, 1):
        return ('%s %s' % (c.mnemonic, c.op_str)).strip()
    return '<undecodable>'


def call_edges(ob, f, names):
    """[(k, target)] for the call and jump instructions of a body that name a function: target = ('local', object label, name) | ('ext', symbol) |
    None for an indirect transfer. Direct targets come from the relocation on the instruction, else from the in-object branch immediate."""
    o = ob.o
    out = []
    for k, i in enumerate(f.ins):
        c = f.ctl[k]
        if c not in ('call', 'jmp', 'cond', 'ijmp'):
            continue
        if i.id == K.ARM64_INS_SVC:
            continue
        if c == 'ijmp' and (f.state[k] is None):
            continue
        if c == 'ijmp':
            if k in f.promoted:
                continue     # an indirect jump inside the function (a jump table): no function edge
            out.append((k, None))                 # a tail call through a register: indirect
            continue
        if c == 'call' and any(op.type == K.ARM64_OP_REG for op in i.ops) and not any(op.type == K.ARM64_OP_IMM for op in i.ops):
            out.append((k, None))                 # blr
            continue
        rel = f.rel.get(i.off)
        if rel is not None:
            y = o.symbols[rel.sym]
            if y.shndx is not None and y.type == 'STT_SECTION':
                out.append((k, ('sec', y.shndx, rel.addend)))
            elif y.shndx is not None:
                out.append((k, ('sym', y.shndx, y.value)))
            else:
                out.append((k, ('ext', y.name)))
            continue
        tgt = [op.imm for op in i.ops if op.type == K.ARM64_OP_IMM]
        if not tgt:
            out.append((k, None))
            continue
        if c in ('jmp', 'cond') and tgt[-1] in f.pos:
            continue                              # a branch inside the body
        out.append((k, ('sec', f.shndx, tgt[-1])))
    return out


def judge(objs, jb_names=(), jb_bytes=0, linked=None):
    """-> Report dict: {'objects': n, 'assembled': [labels], 'accesses': [Access], 'violations': [Access], 'counts': {class: n}, 'premises': [...]}"""
    rep = {'objects': len(objs), 'assembled': [], 'accesses': [], 'violations': [], 'counts': collections.Counter(), 'premises': [], 'bt_nodes': []}
    # -- function table of the whole universe: (label, section, start) -> node; global names -> node
    node_of, glob_def, info = {}, {}, {}
    for ob in objs:
        for si, a, sz, names in ob.o.functions:
            nd = (ob.label, si, a)
            node_of[nd] = nd
            info[nd] = (ob, names)
            for y in ob.o.symbols:
                if y.shndx == si and y.value == a and y.type in ('STT_FUNC', 'STT_GNU_IFUNC') and y.bind in ('STB_GLOBAL', 'STB_WEAK'):
                    glob_def.setdefault(y.name, nd)

    def resolve(ob, tgt):
        if tgt is None:
            return None
        if tgt[0] == 'ext':
            return glob_def.get(tgt[1], ('outside', tgt[1]))
        shndx, val = tgt[1], tgt[2] if tgt[0] == 'sym' else tgt[2]
        best = None
        for si, a, sz, names in ob.o.functions:
            if si == shndx and a <= val < a + max(sz, 1):
                best = (ob.label, si, a)
        return best if best is not None else ('outside', '%s+0x%x' % (ob.o.sections[shndx].name, val))

    # -- bodies (every executable range of every object: the call graph spans compiled and assembled code alike)
    built = {}
    allobjs = list(objs)
    for nm in MERGED:
        mo = merged_object(objs, nm)
        if mo is not None:
            ob = Obj.__new__(Obj)
            ob.merged = True
            ob.label, ob.o, ob.assembled, ob.startfile = '<%s of %d objects>' % (nm, sum(1 for x in objs for sec in x.o.sections if sec.name == nm)), mo[0], mo[1], mo[2]
            allobjs.append(ob)
    for ob in allobjs:
        fb, extra = A.functions_of(ob.o)
        for si, a, b, names, kind in fb:
            if ob.o.sections[si].name in MERGED and not getattr(ob, 'merged', False):
                continue          # judged once, as the concatenation of all objects' sections of that name
            if (b - a) % 4 or a % 4:
                continue
            f = RtFunc(ob.o, si, a, b, names[0], ob.o.relocs.get(si, {}), A.ASM_FRAME)
            f.args = 0 if kind == 'func' else None
            if ob.assembled and kind == 'func' and set(names) & set(jb_names):
                f.entry_regs = ((0, ('D', JB_BASE)),)
            f.build()
            f.dataflow()
            built[(ob.label, si, a)] = (ob, f, names, kind)
    # -- call graph
    edges = {}      # node -> [(k, target node | ('outside', name) | None)]
    for key, (ob, f, names, kind) in built.items():
        edges[key] = [(k, resolve(ob, t)) for k, t in call_edges(ob, f, names)]
    # data / address-materialisation referrers: any relocation of a non-call instruction or of a data section names a function
    data_ref = collections.defaultdict(list)
    call_sites = collections.defaultdict(list)    # node -> [(referrer node, k)]
    for key, es in edges.items():
        for k, t in es:
            if isinstance(t, tuple) and len(t) == 3 and t in built:
                call_sites[t].append((key, k))
    callk = {key: {k for k, t in es} for key, es in edges.items()}
    for ob in objs:
        for si, rels in ob.o.relocs.items():
            sec = ob.o.sections[si]
            if sec.name in ('.eh_frame',) or sec.name.startswith('.debug'):
                continue
            for off, r in rels.items():
                y = ob.o.symbols[r.sym]
                if y.shndx is None and y.name:
                    tg = glob_def.get(y.name)
                elif y.shndx is not None and y.type == 'STT_SECTION':
                    tg = resolve(ob, ('sec', y.shndx, r.addend))
                elif y.shndx is not None:
                    tg = resolve(ob, ('sym', y.shndx, y.value))
                else:
                    tg = None
                if not (isinstance(tg, tuple) and len(tg) == 3 and tg in built):
                    continue
                if sec.flags & a64obj.SHF_EXECINSTR:
                    # the relocation of a call or jump instruction is the call-site edge itself; any other instruction's (adrp/add, ldr) takes the address
                    fk = next((kk for kk, (ob2, f2, n2, kd) in built.items() if ob2 is ob and f2.shndx == si and f2.a <= off < f2.b), None)
                    if fk is not None and off in built[fk][1].pos and built[fk][1].ctl[built[fk][1].pos[off]] in ('call', 'jmp', 'cond', 'ijmp'):
                        continue
                data_ref[tg].append((ob.label, sec.name, off))
    # -- thread-possible
    thr = {}
    for key, es in edges.items():
        thr[key] = any(t is None or (isinstance(t, tuple) and t[0] == 'outside') for k, t in es)
    ch = True
    while ch:
        ch = False
        for key, es in edges.items():
            if not thr[key] and any(isinstance(t, tuple) and len(t) == 3 and thr.get(t) for k, t in es):
                thr[key], ch = True, True
    tp_calls = {}    # node -> instruction indices of thread-possible calls
    for key, es in edges.items():
        tp_calls[key] = [k for k, t in es if t is None or (isinstance(t, tuple) and t[0] == 'outside') or (isinstance(t, tuple) and len(t) == 3 and thr.get(t))]
    after_tp = {}
    for key in edges:
        after_tp[key] = reach_from(built[key][1], tp_calls[key])
    # entries: the code of a start file (its global function symbols are the process's first instructions; an assembled symbol of size 0 makes the
    # code between symbols one `gap` body, which is the same code)
    entries = {key for key, (ob, f, names, kd) in built.items() if ob.startfile}
    cand = set(built)
    while True:
        # reachability through clean call sites, inside cand
        reach, stack = set(), [e for e in entries if e in cand]
        while stack:
            nd = stack.pop()
            if nd in reach:
                continue
            reach.add(nd)
            for k, t in edges[nd]:
                if isinstance(t, tuple) and len(t) == 3 and t in cand and k not in after_tp[nd]:
                    stack.append(t)
        drop = set()
        for nd in cand:
            if nd not in reach:
                drop.add(nd)
                continue
            if nd in entries:
                continue
            if data_ref.get(nd):
                drop.add(nd)
                continue
            for rn, k in call_sites.get(nd, []):
                if rn not in cand or rn not in reach or k in after_tp[rn]:
                    drop.add(nd)
                    break
        if not drop:
            break
        cand -= drop
    bt = cand
    for nd in sorted(bt):
        ob, f, names, kd = built[nd]
        g = [y.name for y in ob.o.symbols if y.name in names and y.bind in ('STB_GLOBAL', 'STB_WEAK')]
        rep['bt_nodes'].append('%s:%s' % (ob.label, names[0]))
        if g and nd not in entries:
            rep['premises'].append('%s:%s is global: no reference from outside the universe (link-level check)' % (ob.label, names[0]))
    # -- classify the accesses of the assembled objects
    for key, (ob, f, names, kind) in built.items():
        if not ob.assembled:
            continue
        if ob.label not in rep['assembled']:
            rep['assembled'].append(ob.label)
        res = f.judge()
        taint = svc_taint(f) if any(i.id == K.ARM64_INS_SVC for i in f.ins) else None
        model = next((MODELS[m.group(1)] for m in (MODEL_RE.search(n) for n in names) if m), None)
        name = names[0]
        for off, (what, rule) in sorted(res.items()):
            k = f.pos[off]
            i = f.ins[k]
            sp, rgt = f.fact(k)
            rg = dict(rgt)
            ac = lambda cls, det='': Access(ob.label, name, off - f.a if kind == 'func' else off, text_of(ob.o, f.shndx, off), cls, det)   # noqa: E731
            cls, det = None, ''
            ex = exclusive_ordering(i)
            if rule == 'O':
                cls, det = 'AT', 'ordered form'
            elif rule in ('F', 'D'):
                cls, det = 'D1', 'frame'
            elif rule == 'L':
                cls, det = 'RO', what
            if cls is None and f.entry_regs and jb_bytes:
                cs = f.frame_offsets(i, sp, rg)
                sz = A.acc_size(i)
                if cs is not None and sz is not None and all(JB_BASE <= c and c + sz <= JB_BASE + jb_bytes for c in cs):
                    cls, det = 'D1', 'jmp_buf'
            if cls is None and ex is not None and model is not None:
                acq, rel, loadlike = ex
                want_acq, want_rel = model
                if loadlike is True:
                    ok = acq == want_acq
                elif loadlike is False:
                    ok = rel == want_rel
                else:
                    ok = (acq, rel) == (want_acq, want_rel)
                if ok:
                    cls, det = 'AT', 'documented %s' % MODEL_RE.search(next(n for n in names if MODEL_RE.search(n))).group(1)
            if cls is None and taint is not None and A.mem_kind(i) == 'store' and taint[k] is not None:
                data = [A.REG[o.reg] for o in i.ops if o.type == K.ARM64_OP_REG and o.reg in A.REG]
                if data and set(data) <= taint[k] and i.mem is not None and A.REG.get(i.mem.base) not in data:
                    cls, det = 'KC', 'svc result'
            if cls is None and key in bt and k not in after_tp[key]:
                cls, det = 'BT', 'before threads'
            a = ac(cls or 'VIOLATION', det or what)
            if cls is None:
                rep['violations'].append(a)
            else:
                rep['counts'][cls] += 1
                rep['accesses'].append(a)
        for off in f.undec:
            rep['violations'].append(Access(ob.label, name, off - f.a, '<undecodable>', 'VIOLATION', 'undecodable'))
    rep['counts'] = dict(rep['counts'])
    if linked is not None:
        rep['linked'] = sorted(linked)
    return rep


def to_json(rep):
    d = dict(rep)
    d['accesses'] = len(rep['accesses'])
    d['violations'] = [a._asdict() for a in rep['violations']]
    d['assembled'] = sorted(rep['assembled'])
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ext', required=True)
    ap.add_argument('--dirs', nargs='+', required=True)
    ap.add_argument('--names', nargs='+', required=True)
    ap.add_argument('--startfiles', nargs='*', default=[])
    ap.add_argument('--endfiles', nargs='*', default=[])
    ap.add_argument('--setjmp-header', default=None, help='path (inside --ext) of the runtime setjmp.h the jmp_buf routines are read from')
    ap.add_argument('--jmpbuf-bytes', type=int, default=0)
    ap.add_argument('--product', nargs='*', default=[], help='the product objects (.o): the linked set is what a linker would pull for them')
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    objs = universe(runtime_blobs(a.ext, a.dirs, a.names, a.startfiles, a.endfiles))
    jb = jmpbuf_routines(open(os.path.join(a.ext, a.setjmp_header)).read()) if a.setjmp_header else []
    linked = None
    if a.product:
        d, u = set(), set()
        for p in a.product:
            o = a64obj.read(p)
            d |= {y.name for y in o.symbols if y.shndx is not None and y.bind in ('STB_GLOBAL', 'STB_WEAK') and y.name}
            u |= {y.name for y in o.symbols if y.shndx is None and y.name and y.bind in ('STB_GLOBAL', 'STB_WEAK')}
        linked = pulled(objs, (d, u))
    rep = judge(objs, jb, a.jmpbuf_bytes, linked)
    j = to_json(rep)
    if a.out:
        json.dump(j, open(a.out, 'w'), indent=1)
    print('objects %d, assembled %d, accesses %d, violations %d' % (rep['objects'], len(rep['assembled']), len(rep['accesses']) + len(rep['violations']), len(rep['violations'])))
    print('per class:', rep['counts'])
    if linked is not None:
        lv = [v for v in rep['violations'] if v.obj in linked or v.obj.startswith('<')]
        print('LINKED (pulled by the product): %d objects, assembled %d, violations %d' % (len(linked), len([x for x in rep['assembled'] if x in linked]), len(lv)))
        rep['violations_linked'] = lv
    per = collections.Counter((v.obj, v.func, v.detail) for v in rep['violations'] if linked is None or v.obj in linked or v.obj.startswith('<'))
    for (ob, fn, det), n in sorted(per.items()):
        print('  VIOLATION %s %s: %d x %s' % (ob, fn, n, det))
    return 1 if rep['violations'] else 0


if __name__ == '__main__':
    sys.exit(main())
