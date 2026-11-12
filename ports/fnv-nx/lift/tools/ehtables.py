#!/usr/bin/env python3
"""Exception-handling facts of an image, all by dataflow over the decoded code (no byte windows, no name heuristics); the flow engine is tools/ehflow.py.
  C++ FuncInfo   every decoded `mov eax, imm32; jmp rel32` whose imm32 is a relocated FuncInfo (magic 0x1993052x), parsed with every bound checked.
  ThrowInfo (R1) the 2nd argument of each call of the thrower (the noreturn seed): a constant or set of constants by the backward cone, or 0 with object 0 (a rethrow);
                 an incoming argument k of the caller is the union over EVERY direct call site (recursive, memoised; a cycle without a constant is red);
                 a caller whose address is taken or that is entered other than by a direct call is red (`throw_sites[].kind == 'red'`, `thrower_escapes`).
  Callee-saved (R2) ebx/esi/edi/ebp survive a call only where the callee is proven to restore them on every path to each ret (`preserves`), or is an import
                 (Win32 as documented, ASSUMPTIONS.json 3); otherwise unknown.
  SEH (R3)       registrations are the decoded stores of an esp-derived address to fs:[0]; the record is {next, handler, field, trylevel}. The handler is a constant; the scope
                 table is the constant `field` (plain, or xor-ed with a value independent of every argument), or, in a helper whose field is its incoming argument k,
                 the constant of argument k at every direct call site. A field that is neither (cookie, computed) yields no scope table.
  Table length (R4) 1 + the largest constant trylevel stored to the frame's trylevel slot (ebp + frame_disp derived from the registration); a non-constant store is
                 `nonconst_stores` (red); an empty table is valid iff no level >= 0 is stored.
  Also: the catch continuations (`mov eax, imm32; ret` of a catch funclet) and the frame-handler stubs (`live` iff the stub address is a relocated constant of the universe).
Used by universe.py, which supplies the descent state (tools/modlift.py supplies it for a module image read through pemod: the same code, rebased addresses; running this
file runs the whole pipeline). Output: <facts>/ehtables.json (exe) and <facts>/<module>/ehtables.json, which tools/gate_eh.py reads (schema: its docstring). Environment:
FNVNX_KEYS (L1 keys.tsv with the import conventions; default keys.tsv beside the directory of FNVNX_MODULES)."""
import os, re, sys, json, bisect
from collections import Counter, defaultdict, deque
from itertools import islice
import capstone
from capstone import x86 as cx
import ehflow
from ehflow import Flow, FULL, M32, St, sload, cset
CALLEE_ORDER = ('ebx', 'esi', 'edi', 'ebp')
MAGIC = (0x19930520, 0x19930521, 0x19930522)
EXC_CODE = 0xE06D7363           # the thrower is the function whose code copies the EXCEPTION_RECORD template starting with this code
RE_STUB = re.compile(rb'(?=\xb8(.{4})\xe9(.{4}))', re.S)
RE_PUSHH = re.compile(rb'(?=\x68(.{4})\x64(?:\xa1|\xff\x35)\x00\x00\x00\x00)', re.S)
h = lambda a: '0x%08x' % a
def le(b): return int.from_bytes(b, 'little')

class EH(Flow):
    def __init__(s, X):
        s.X = X; s.fi = {}; s.stubs = {}; s.noise = Counter(); s.sites = {}; s.scopes = {}; s.helpers = {}; s.seenh = set()
        s.regs = {}; s.unknown = 0; s.throws = {}; s.catch = []; s.contdone = set(); s.conts = {}; s.emitted = 0; s.seenstub = set(); s.throwers = []; s.tsites = {}; s.dc = {}; s.slot = {}; s.lead = set(); s.jt = defaultdict(list); s.nseen = 0; s.rv = None; s.rvk = -1; s.rst = False; s.sb = {}; s.rl = {}; s.relset = None
        s.cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); s.cs.detail = True; s.flow_init(); s.am = {}; s.fsc_new = []; s.fsw = set(); s.fsc_seen = 0; s.regsites = {}; s.hlp = {}; s.seh_red = []; s.fs0_other = 0

    @property
    def thrower(s): return s.throwers[0] if s.throwers else None      # the noreturn seed of universe.py / modlift.py (a statically linked CRT has exactly one)

    def mk(s, a, n, ref):
        """EH structures that lie in an executable section (merged .rdata) are data named by the relocation at ref."""
        if n > 0 and s.X.intext(a): s.X.mark(a, n, ref)

    def emit(s, tgt, kind, site, parent=None):
        if site not in s.sites: s.emitted += 1
        s.sites[site] = kind; s.X.emit(tgt, kind, site)

    # ---- C++ FuncInfo
    def parse_fi(s, a):
        X = s.X; u = X.u32; magic = u(a); ms, pu, nt, pt, ni, pi = (u(a + 4 * i) for i in range(1, 7))
        r = {'addr': a, 'magic': magic, 'maxState': ms, 'unwind': [], 'tries': [], 'ipmap': [], 'ESTypeList': 0, 'flags': 0}
        if ms > 0x4000 or nt > 0x1000 or ni > 0x10000: return {'err': 'size'}
        if ms and (a + 8 not in X.rset or X.sec(pu) is None): return {'err': 'unwindmap'}
        if nt and (a + 16 not in X.rset or X.sec(pt) is None): return {'err': 'trymap'}
        for i in range(ms):
            e = pu + 8 * i; to = u(e); act = u(e + 4)
            if to != 0xffffffff and to >= ms: return {'err': 'toState@%x' % e}
            if act and (e + 4 not in X.rset or not X.intext(act)): return {'err': 'action@%x' % e}
            r['unwind'].append((to, act, e + 4))
        for i in range(nt):
            e = pt + 20 * i; lo, hi, ch, nc, ph = (u(e + 4 * k) for k in range(5)); hs = []
            if hi >= ms and hi != 0xffffffff or lo > hi or nc > 0x100 or (nc and (e + 16 not in X.rset or X.sec(ph) is None)): return {'err': 'try@%x' % e}
            for j in range(nc):
                q = ph + 16 * j; adj, ty, disp, ad = (u(q + 4 * k) for k in range(4))
                if (ty and (q + 4 not in X.rset or X.sec(ty) is None)) or q + 12 not in X.rset or not X.intext(ad): return {'err': 'handler@%x' % q}
                hs.append((adj, ty, disp, ad, q + 12))
            r['tries'].append(((lo, hi, ch), hs))
        if ni:
            if a + 24 not in X.rset: return {'err': 'ipmap'}
            r['ipmap'] = [(u(pi + 8 * i), u(pi + 8 * i + 4)) for i in range(ni)]; r['ipt'] = pi
        if magic >= MAGIC[1]: r['ESTypeList'] = u(a + 28)
        if magic >= MAGIC[2]: r['flags'] = u(a + 32)
        return r

    def scan_stubs(s):
        X = s.X; n = 0
        for m in RE_STUB.finditer(X.text):
            a = X.T0 + m.start()
            if a in s.seenstub or X.cov[m.start()] != 1 or (a + 1) not in X.rset: continue
            imm = le(m[1])
            if not X.isdata(imm): continue
            s.seenstub.add(a); tgt = (a + 10 + int.from_bytes(m[2], 'little', signed=True)) & 0xffffffff
            if X.u32(imm) not in MAGIC: s.noise[tgt] += 1; s.unknown += X.u32(imm) >> 4 == 0x1993052; continue
            s.stubs[a] = (imm, tgt); n += 1
            if imm not in s.fi:
                r = s.fi[imm] = s.parse_fi(imm)
                if 'err' in r: continue
                s.mk(imm, 28 + 4 * MAGIC.index(r['magic']), a + 1); s.mk(X.u32(imm + 8), 8 * r['maxState'], imm + 8); s.mk(X.u32(imm + 16), 20 * len(r['tries']), imm + 16)
                s.mk(X.u32(imm + 24), 8 * len(r['ipmap']), imm + 24)
                for i, (rng, hs) in enumerate(r['tries']): s.mk(X.u32(X.u32(imm + 16) + 20 * i + 16), 16 * len(hs), X.u32(imm + 16) + 20 * i + 16)
                for to, act, site in r['unwind']:
                    if act: s.emit(act, 'eh-unwind', site)
                for rng, hs in r['tries']:
                    for adj, ty, disp, ad, site in hs: s.emit(ad, 'eh-catch', site); s.catch.append(ad)
        return n

    def has_fs(s, a):
        ins = s.dis(a)
        return ins is not None and any(op.type == cx.X86_OP_MEM and op.mem.segment == cx.X86_REG_FS for op in ins.operands)

    # ---- SEH (R3, R4): a registration record is found by flow, never by name or byte pattern
    def fs0_writers(s):
        """decoded instructions that write fs:[0] (the instructions of the image carrying an fs prefix, decoded)"""
        for a in s.fsc_new:
            ins = s.dis(a); w = False
            if ins is not None:
                for op in ins.operands:
                    if op.type == cx.X86_OP_MEM and op.mem.segment == cx.X86_REG_FS and not op.mem.base and not op.mem.index and op.mem.disp == 0 and op.access & capstone.CS_AC_WRITE: w = True
            if w: s.fsw.add(a)
        s.fsc_new = []
        return sorted(s.fsw)

    def fs0_value(s, a):
        st = s.state_before(a).cp(); s.ev = []; s.step(s.dis(a), st); ev = s.ev; s.ev = None
        return ev[0][1] if ev else None

    def field_class(s, v):
        """classify the value of the record field after the handler -> ('const', consts) | ('arg', (R, k)) | ('none', None) | ('red', why). The informational part of the field is one
        leaf: a constant (the scope table, plain or xor-ed with values independent of every argument: the pointer encoded with the cookie) or the incoming argument that is that
        value (the helper's scope-table argument); every other leaf (a value loaded from a fixed address, an esp-derived address, fs:[0]) is independent of the arguments."""
        cs = cset(v)
        if cs is not None: return 'const', cs
        ints = []; args = set(); bad = []
        def walk(x):
            if x is None: bad.append('unknown value')
            elif isinstance(x, int): ints.append(x)
            elif x[0] == 'arg': args.add((x[1], x[2]))
            elif x[0] == 'xor': walk(x[1]); walk(x[2])
            elif x[0] in ('mem', 'sp', 'fs0'): pass
            else: bad.append('value %s' % (x[0],))
        walk(v)
        if bad: return 'red', bad[0]
        if len(args) > 1 or (args and ints) or len(ints) > 1: return 'red', 'more than one informational leaf'
        if args: return 'arg', next(iter(args))
        if ints: return 'const', frozenset(ints)
        return 'none', None

    def scan_seh(s):
        X = s.X; s.regsites = {}; s.hlp = {}; s.seh_red = []; s.fs0_other = 0; s.scopes_in = {}
        for a in s.fs0_writers():
            v = s.fs0_value(a)
            if v is None: s.seh_red.append((a, 'fs0 store of an unknown value')); continue
            if not (isinstance(v, tuple) and v[0] == 'sp'): s.fs0_other += 1; continue
            st = s.state_before(a); o = v[1]; Hv = sload(st, o + 4); Hs = cset(Hv)
            if Hs is None or not all(X.intext(h_) for h_ in Hs): s.seh_red.append((a, 'registration record without a constant handler')); continue
            for h_ in sorted(Hs): s.emit(h_, 'seh-frame', a)
            cls, arg = s.field_class(sload(st, o + 8)); e = st.regs.get('ebp')
            disp = (o + 12 - e[1]) if isinstance(e, tuple) and e[0] == 'sp' else None
            r = s.regsites[a] = {'site': a, 'handlers': sorted(Hs), 'kind': cls, 'disp': disp}
            if cls == 'red': s.seh_red.append((a, 'record field: ' + arg)); continue
            if cls == 'const':
                for S in sorted(arg):
                    if X.sec(S) is not None and X.isdata(S): s.add_scope(S, sorted(Hs), a, disp)
            elif cls == 'arg':
                R, k = arg; eff = s.summary(R); eo = eff[2].get('ebp') if eff else None
                hd = s.hlp.setdefault(R, {'addr': R, 'k': k, 'handlers': set(), 'disp': None, 'sites': set()})
                if hd['k'] != k: s.seh_red.append((a, 'helper %x: two scope arguments' % R)); continue
                hd['handlers'].update(Hs); hd['sites'].add(a); hd['disp'] = (o + 12 - eo) if eo is not None else None
        for R in sorted(s.hlp):
            hd = s.hlp[R]; vals, err, sites = s.argvals(R, hd['k']); hd['err'] = err
            if err: s.seh_red.append((R, 'helper scope argument: ' + err)); continue
            for c, cs in sorted(sites.items()):
                for S in sorted(cs):
                    if X.sec(S) is not None and X.isdata(S): s.add_scope(S, sorted(hd['handlers']), c, hd['disp'])

    def add_scope(s, S, Hs, site, disp):
        sc = s.scopes.get(S)
        if sc is None: sc = s.scopes[S] = {'addr': S, 'handlers': set(), 'sites': set(), 'recs': [], 'ver': 3, 'disps': set()}
        sc['handlers'].update(Hs); sc['sites'].add(site); sc['disps'].add(disp)

    # ---- R1: the value of incoming stack argument k of R = the union over every direct call site (recursively); a function whose address is taken, or that is entered other than by a direct call, has none
    def clean(s, R):
        s.relocvals()
        return R not in s.rv and R not in s.pe_entries() and not s.preds(R)

    def argvals(s, R0, k0):
        """-> (frozenset of constants or None, error or None, {direct call site: constants})"""
        key = (R0, k0)
        if key in s.am: return s.am[key]
        nodes = {}; stack = [key]
        while stack:
            key = stack.pop()
            if key in nodes or key in s.am: continue
            R, k = key; nd = nodes[key] = {'c': set(), 'r': set(), 'e': None, 'sites': {}}
            if not s.clean(R): nd['e'] = 'function entered other than by a direct call (address taken at %x or a jump)' % R; continue
            sites = s.X.callers.get(R, ())
            if not sites: nd['e'] = 'no direct caller'; continue
            for c in sorted(sites):
                st = s.state_before(c); v = None if st.sp is None else sload(st, st.sp + 4 * k); cs = cset(v)
                if cs is not None: nd['c'] |= cs; nd['sites'][c] = cs
                elif isinstance(v, tuple) and v[0] == 'arg': nd['r'].add((v[1], v[2])); stack.append((v[1], v[2]))
                else: nd['e'] = nd['e'] or 'argument %d of %x not constant at %x' % (k, R, c)
        bad = {n for n, d in nodes.items() if d['e']}; ch = True
        while ch:
            ch = False
            for n, d in nodes.items():
                if n in bad: continue
                for r in d['r']:
                    if r in bad or (r in s.am and s.am[r][1]): bad.add(n); d['e'] = 'depends on %x arg %d' % r; ch = True; break
        V = {n: set(d['c']) for n, d in nodes.items()}; ch = True
        while ch:
            ch = False
            for n, d in nodes.items():
                for r in d['r']:
                    add = V[r] if r in V else (s.am[r][0] or set())
                    if not add <= V[n]: V[n] |= add; ch = True
        for n, d in nodes.items():
            if n not in bad and not V[n]: bad.add(n); d['e'] = 'no constant'
            s.am[n] = (None, d['e'], {}) if n in bad else (frozenset(V[n]), None, d['sites'])
        return s.am[(R0, k0)]

    # ---- decoding and in-block constant propagation (shared by the trylevel and the throw rules)
    def dis(s, a):
        r = s.dc.get(a)
        if r is None:
            o = a - s.X.T0; r = s.dc[a] = next(s.cs.disasm(s.X.text[o:o + 15], a, 1), False)
        return r or None

    def sync(s):
        """incremental reverse map of the decoded flow: every address that is the target of a decoded instruction (the block leaders) and the jmp sources per target"""
        X = s.X
        s.relocvals()
        if len(X.insns) < s.nseen: s.nseen = 0; s.lead = set(); s.jt = defaultdict(list); s.fsc_new = []; s.fsw = set()
        for a, v in islice(X.insns.items(), s.nseen, None):
            s.lead.update(v[4])
            if v[2] & 4 or (X.strict and s.has_fs(a)): s.fsc_new.append(a)      # exe: the descent's fs-prefix feature; module engine: decoded here
            for t in v[4]: s.jt[t].append(a)
        if s.nseen != len(X.insns): s.sb = {}; s.sm = {}; s.am = {}; s.ip = set()
        s.nseen = len(X.insns)

    def relocvals(s):
        """s.rv: the values of the relocated dwords that can be control-flow targets. A dword of the IP column of a parsed FuncInfo IP-to-state map is excluded: the table is a sorted
        key list read only by the C++ frame handler (assumption), and its keys lie mid-block."""
        k = sum(1 for r in s.fi.values() if r.get('ipt'))
        if s.rv is None or k != s.rvk:
            ip = {r['ipt'] + 8 * i for r in s.fi.values() if r.get('ipt') for i in range(len(r['ipmap']))}
            s.rv = {v for r, v in s.X.rvals if r not in ip}; s.rvk = k; s.sb = {}
        return s.rv

    def leader(s, a):
        X = s.X
        if a in s.lead or a in s.rv or a in X.callers: return True
        try: return X.entry_of(a) == a
        except IndexError: return True

    def prevs(s, a):
        """every decoded instruction that falls through into a (decodes of different alignment may overlap; the one that is never executed has no seed above it and so
        contributes nothing to the dataflow)"""
        X = s.X; r = []
        for k in range(1, 16):
            p = a - k
            if p >= X.T0 and X.cov[p - X.T0] == 1 and p in X.insns and X.insns[p][0] == k and X.insns[p][3]: r.append(p)
        return r

    def extent(s, a):
        """the decoded instructions reachable from a over fall-through, jump, branch and jump-table edges (no size cap)"""
        X = s.X; seen = set(); st = [a]
        while st:
            x = st.pop()
            if x in seen or x not in X.insns: continue
            seen.add(x); sz, mn, ft, fall, tg = X.insns[x]; st.extend(tg)
            if fall: st.append(x + sz)
        return seen

    def real(s, a):
        """a decoded instruction is code only if every relocated dword it overlaps is one of its own operands (its immediate or displacement). Assumption: a relocated dword in the
        middle of an instruction's bytes is a pointer in a data table (jump table, vtable) that the linear decode ran through; it is never executed."""
        r = s.rl.get(a)
        if r is None:
            ins = s.dis(a); sz = s.X.insns[a][0]; r = ins is not None
            if r:
                if s.relset is None: s.relset = set(s.X.relocs)
                for q in range(a - 3, a + sz):
                    if q in s.relset and not (q == a + ins.imm_offset and ins.imm_size == 4 or q == a + ins.disp_offset and ins.disp_size == 4):
                        r = False; break
            s.rl[a] = r
        return r

    def preds(s, x):
        """the decoded predecessors of x that are code (real): sources of jumps, branches and jump-table edges to x, and the instruction that falls through into x"""
        return [q for q in list(s.jt.get(x, ())) + s.prevs(x) if s.real(q)]

    def seeded(s, x):
        """control may reach x by an edge the decoder does not see: x is a call target, a relocated code address (see relocvals; owner entries, handler and filter funclets are
        such), or a PE entry point or export"""
        return x in s.rv or x in s.X.callers or x in s.pe_entries()

    # ---- R4: the scope table length is 1 + the largest constant trylevel stored to the record's trylevel field (ebp + disp, disp derived from the registration)
    def slotop(s, a, disp):
        """None if the instruction at a does not touch the trylevel slot [ebp+disp, +4) through an ebp-based operand; else ('c', v) a constant store, ('r', reg) a store of a register,
        ('n', why) a store the rule cannot resolve, including any partial overlap and any lea of the slot."""
        key = (a, disp); r = s.slot.get(key, 0)
        if r != 0: return r
        ins = s.dis(a); r = None
        if ins is not None:
            for k, op in enumerate(ins.operands):
                if op.type != cx.X86_OP_MEM or op.mem.base != cx.X86_REG_EBP or op.mem.index or op.mem.segment: continue
                lo = op.mem.disp; hi = lo + max(op.size, 1)
                if hi <= disp or lo >= disp + 4: continue
                if ins.mnemonic == 'lea': r = ('n', 'lea'); break
                if not op.access & capstone.CS_AC_WRITE: continue
                o = ins.operands[1 - k] if len(ins.operands) == 2 else None
                if lo != disp or op.size != 4 or o is None: r = ('n', 'partial'); break
                if ins.mnemonic == 'mov' and o.type == cx.X86_OP_IMM: r = ('c', o.imm & M32)
                elif ins.mnemonic == 'mov' and o.type == cx.X86_OP_REG and o.size == 4 and FULL.get(ins.reg_name(o.reg)): r = ('r', FULL[ins.reg_name(o.reg)])
                elif ins.mnemonic == 'or' and o.type == cx.X86_OP_IMM and o.imm & M32 == M32: r = ('c', M32)
                elif ins.mnemonic == 'and' and o.type == cx.X86_OP_IMM and o.imm & M32 == 0: r = ('c', 0)
                else: r = ('n', ins.mnemonic)
                break
        s.slot[key] = r; return r

    def levels(s, code, disp):
        """-> (largest constant trylevel or -1, [addresses of non-constant stores]) over the instructions of a frame; a store of a register is constant iff the register holds a constant
        (or a set of constants) on every path (flow, with callee-saved registers surviving a call only where the callee is proven to restore them)"""
        mx = -1; bad = []
        for a in sorted(code):
            r = s.slotop(a, disp)
            if r is None: continue
            if r[0] == 'c': cs = {r[1]}
            elif r[0] == 'r':
                cs = cset(s.state_before(a).regs.get(r[1]))
                if cs is None: bad.append(a); continue
            else: bad.append(a); continue
            for v in cs:
                if v < 0x80000000: mx = max(mx, v)
        return mx, bad

    def recs_of(s, S, n):
        X = s.X; u = X.u32
        try:
            v4 = u(S + 16) == 0xfffffffe and (S + 24) in X.rset; R = S + 16 if v4 else S; recs = []
            for i in range(n):
                b = R + 12 * i; enc, flt, hnd = u(b), u(b + 4), u(b + 8)
                if (b + 8) not in X.rset or not X.intext(hnd) or (flt and ((b + 4) not in X.rset or not X.intext(flt))) or not (enc >= 0xfffffffe or enc < n): return (4 if v4 else 3), recs, 'record %d invalid' % i
                recs.append((enc, flt, hnd, b))
        except Exception: return 3, [], 'table unreadable'
        return (4 if v4 else 3), recs, None

    def parse_scopes(s):
        """exactly n = 1 + (largest trylevel constant stored to the trylevel slot in the frame) records, n = 0 if no level >= 0 is stored; the frame is the code the owner functions
        reach plus the code of every handler and filter of its records (a funclet stores levels into the same frame; the iteration is monotone)."""
        X = s.X
        for S in sorted(s.scopes):
            sc = s.scopes[S]; roots = {X.entry_of(x) for x in sc['sites']}; done = set(); code = set(); disps = sc['disps']
            disp = next(iter(disps)) if len(disps) == 1 and None not in disps else None
            if disp is None: sc.update(ver=3, recs=[], err='trylevel slot not derivable (frame register unknown or two derivations)', level=-1, nonconst=[], funcs=[]); continue
            while True:
                for r in sorted(roots - done): code.update(s.extent(r)); done.add(r)
                mx, bad = s.levels(code, disp); n = mx + 1 if mx >= 0 else 0; err = 'trylevel %d' % mx if mx > 4096 else None
                ver, recs, e2 = s.recs_of(S, 0 if err else n); err = err or e2
                new = {x for e, f, hd, b in recs for x in (f, hd) if x and X.intext(x)} - done
                if not new: break
                roots |= new
            sc.update(ver=ver, recs=recs, err=err, level=mx, nonconst=bad, funcs=sorted(done), disp=disp)
            s.mk(S, (16 if ver == 4 else 0) + 12 * len(recs), min(sc['sites']) + 1)
            for enc, flt, hnd, b in recs:
                if flt: s.emit(flt, 'seh-filter', b + 4)
                s.emit(hnd, 'seh-handler', b + 8)

    # ---- throw side
    def parse_ti(s, S):
        X = s.X; u = X.u32
        if S + 12 not in X.rset or (u(S + 4) and (S + 4 not in X.rset or not X.intext(u(S + 4)))): return None
        cta = u(S + 12); n = u(cta)
        if not (0 < n <= 64) or X.sec(cta) is None: return None
        cts = []
        for i in range(n):
            e = cta + 4 + 4 * i; ct = u(e)
            if e not in X.rset or X.sec(ct) is None or (ct + 4) not in X.rset: return None
            cp = u(ct + 24)
            if cp and ((ct + 24) not in X.rset or not X.intext(cp)): return None
            cts.append((u(ct), u(ct + 4), u(ct + 20), cp, ct + 24))
        return {'addr': S, 'attrs': u(S), 'unwind': u(S + 4), 'unwind_site': S + 4, 'cta': cta, 'types': cts}

    def find_throwers(s):
        """_CxxThrowException of a statically linked CRT, identified structurally: it is the function whose code references (relocated operand) the data cell that holds the
        C++ exception code 0xE06D7363, the first dword of the EXCEPTION_RECORD template it copies into RaiseException's argument. Every function that does so is a thrower."""
        X = s.X
        return sorted({X.entry_of(r) for r in X.relocs if X.intext(r) and X.cov[r - X.T0] == 2 and X.isdata(X.u32(r)) and X.u32(X.u32(r)) == EXC_CODE})

    def thr_slots(s):
        """IAT slots that import _CxxThrowException by name (a thrower in another DLL), and the instructions `call [slot]` / `jmp [slot]` that use them"""
        if getattr(s, '_slots', None) is None:
            X = s.X; sl = {a for a, n in X.imports.items() if n.split('!', 1)[-1] == '_CxxThrowException'}; s._slots = sl
            s._slotrefs = [r for r in X.relocs if X.u32(r) in sl] if sl else []
        return s._slots, s._slotrefs

    def throw_sites(s):
        """-> {site: displacement of the argument block}: every call of a thrower, direct (`call rel32`, also through a jmp-only thunk: an entry whose instruction is `jmp thrower`
        or `jmp [slot]`) or through an import slot (`call [slot]`), has the object at [esp] and the ThrowInfo at [esp+4] (displacement 0); a tail `jmp` into a thrower
        that is not a thunk has them at [esp+4] and [esp+8] (displacement 4)."""
        X = s.X; s.sync(); slots, refs = s.thr_slots(); R = set(s.throwers); sites = {}
        ind = []
        for r in refs:
            p = r - 2
            if X.intext(r) and X.cov[p - X.T0] == 1 and X.text[p - X.T0] == 0xff and X.text[p - X.T0 + 1] in (0x15, 0x25): ind.append(p)
        for p in ind:
            if X.text[p - X.T0 + 1] == 0x25 and s.leader_entry(p): R.add(p)
        grow = True
        while grow:
            grow = False
            for t in sorted(R):
                for j in s.jt.get(t, ()):
                    if j not in R and X.insns[j][1] == 'jmp' and s.leader_entry(j): R.add(j); grow = True
        for t in sorted(R):
            for c in X.callers.get(t, ()): sites[c] = 0
            for j in s.jt.get(t, ()):
                if j not in R: sites[j] = 4
        for p in ind:
            if p not in R: sites[p] = 0 if X.text[p - X.T0 + 1] == 0x15 else 4
        s.R = R
        return sites

    def pe_entries(s):
        """code VAs the loader or other images may enter: the PE entry point and every export that is not a forwarder (TLS callbacks are relocated dwords, so in s.rv)"""
        if getattr(s, '_pe', None) is None:
            X = s.X; d = X.d; pe = le(d[0x3c:0x40]); soh = le(d[pe + 20:pe + 22]); rva0 = le(d[pe + 24 + soh + 12:pe + 24 + soh + 16])
            b = X.secs[0][1] - rva0; E = {b + le(d[pe + 40:pe + 44])}; er = le(d[pe + 24 + 96:pe + 24 + 100]); es = le(d[pe + 24 + 100:pe + 24 + 104])
            if er:
                e = b + er; af = b + X.u32(e + 28)
                for i in range(X.u32(e + 20)):
                    r = X.u32(af + 4 * i)
                    if r and not er <= r < er + es: E.add(b + r)
            s._pe = E
        return s._pe

    def leader_entry(s, a):
        try: return s.X.entry_of(a) == a
        except IndexError: return False

    def resolve(s, site, off):
        """R1: -> (constants or None, kind, why): kind 'const' | 'rethrow' | 'red'. The ThrowInfo is the 2nd argument of the call: a constant, a set of constants, or an incoming
        argument of the caller (the union over every direct call site, argvals); a rethrow is 0 with a 0 object."""
        st = s.state_before(site)
        if st.sp is None: return None, 'red', 'esp unknown at the call'
        ti = sload(st, st.sp + off + 4); obj = sload(st, st.sp + off); cs = cset(ti)
        if cs is None:
            if isinstance(ti, tuple) and ti[0] == 'arg':
                cs, err, _ = s.argvals(ti[1], ti[2])
                if err: return None, 'red', err
            else: return None, 'red', 'ThrowInfo not constant'
        if cs == {0}: return (cs, 'rethrow', None) if cset(obj) == {0} else (None, 'red', 'null ThrowInfo with an object')
        if 0 in cs: return None, 'red', 'null ThrowInfo among the values'
        return cs, 'const', None
    def reg_ti(s, S, ref):
        X = s.X
        if S in s.throws: return
        r = s.parse_ti(S)
        if not r: return
        s.throws[S] = r; s.mk(S, 16, ref); s.mk(r['cta'], 4 + 4 * len(r['types']), S + 12)
        for k, t in enumerate(r['types']): s.mk(X.u32(r['cta'] + 4 + 4 * k), 28, r['cta'] + 4 + 4 * k); s.mk(t[1], 8 + len(X.cstr(t[1] + 8)) + 1, X.u32(r['cta'] + 4 + 4 * k) + 4)
        if r['unwind']: s.emit(r['unwind'], 'eh-throw', r['unwind_site'])
        for t in r['types']:
            if t[3]: s.emit(t[3], 'eh-throw', t[4])

    def scan_throw(s):
        """every ThrowInfo is the second argument of a call of the thrower (R1)."""
        X = s.X
        if not s.throwers: s.throwers = s.find_throwers()
        for site, off in sorted(s.throw_sites().items()):
            if site in s.tsites: continue
            cs, kind, why = s.resolve(site, off); s.tsites[site] = (cs, kind, why, off)
            for ti in sorted(cs or ()):
                if ti and X.isdata(ti): s.reg_ti(ti, site)
    def scan_conts(s):
        """catch funclets end in `mov eax, imm32(reloc); ret`: the continuation address in the parent."""
        X = s.X
        for a in s.catch:
            if a in s.contdone or a not in X.insns: continue
            s.contdone.add(a)
            for x in X.extent_insns(a):
                o = x - X.T0
                if X.text[o] == 0xb8 and X.text[o + 5] == 0xc3 and (x + 1) in X.rset and X.intext(X.u32(x + 1)):
                    s.conts[X.u32(x + 1)] = a; s.emit(X.u32(x + 1), 'eh-cont', x + 1)

    def scan(s):
        z = lambda: s.emitted + len(s.fi) + len(s.stubs) + len(s.hlp) + len(s.regsites) + len(s.scopes) + len(s.throws) + len(s.conts) + sum(len(sc['recs']) for sc in s.scopes.values()) + len(s.tsites)
        e0 = z(); s.sync(); s.scan_stubs(); s.scan_seh(); s.parse_scopes(); s.scan_throw(); s.scan_conts(); s.scan_n = len(s.X.insns)
        return z() - e0

    def is_slot_use(s, r, v):
        """the relocated dword at r is the displacement of a decoded `call [slot]` / `jmp [slot]` (the slot v)"""
        ins = s.dis(r - 2)
        return (ins is not None and ins.mnemonic in ('call', 'jmp') and ins.size == 6 and ins.disp_offset == 2 and ins.operands[0].type == cx.X86_OP_MEM
                and not ins.operands[0].mem.base and not ins.operands[0].mem.index and ins.operands[0].mem.disp & M32 == v)

    # ---- report
    def finish(s, entries):
        """owners, JSON, count-only report lines."""
        X = s.X; es = sorted(entries); refs = defaultdict(list)
        s.sync()
        if getattr(s, 'scan_n', -1) != len(X.insns): s.scan_seh(); s.parse_scopes()      # the descent added instructions after the last scan: the facts are of the final code
        s.throwers = s.find_throwers(); s.tsites = {}; sites = s.throw_sites(); tinfo = {}; tfail = []; tsl = []
        for site, off in sorted(sites.items()):
            cs, kind, why = s.resolve(site, off); s.tsites[site] = (cs, kind, why, off)
            tsl.append({'site': h(site), 'tail': bool(off), 'kind': kind, 'tis': [h(x) for x in sorted(cs)] if kind == 'const' else [], 'red': why})
            for ti in sorted(cs or ()) if kind == 'const' else ():
                r = s.throws.get(ti) or (s.parse_ti(ti) if X.isdata(ti) else None)
                if r: tinfo[ti] = r
                else: tfail.append({'site': h(site), 'ti': h(ti)})
        slots = s.thr_slots()[0]; esc = []
        for r in X.relocs:
            v = X.u32(r)
            if v in s.R or v in slots:
                if v in slots and X.intext(r) and X.cov[r - X.T0] == 2 and s.is_slot_use(r, v): continue
                esc.append(r)
        ss = sorted(s.stubs); live = {}
        for r in X.relocs:
            v = X.u32(r)
            if not X.intext(v): continue
            i = bisect.bisect_left(ss, v)
            if i == len(ss) or ss[i] - v >= 0x30 or live.get(ss[i], -1) >= v: continue
            if X.intext(r):
                cv = X.cov[r - X.T0]
                if cv == 1: continue
                if cv == 2:         # a constant in code is an immediate operand (not a displacement)
                    ok = False
                    for k in range(1, 15):
                        q = r - k
                        if q >= X.T0 and X.cov[q - X.T0] == 1 and q in X.insns and X.insns[q][0] > k:
                            ins = s.dis(q); ok = ins is not None and ins.imm_size == 4 and ins.imm_offset == k; break
                    if not ok: continue
            live[ss[i]] = v
        for r, v in X.rvals: refs[v].append(r)
        def own(a):
            f = X.indec(a)
            if f: return f[0]
            i = bisect.bisect_right(es, a) - 1; return es[i] if i >= 0 else None
        owners = defaultdict(set); fiown = defaultdict(set)
        for a, (fi, tgt) in s.stubs.items():
            i = bisect.bisect_right(es, a) - 1; st = es[i] if i >= 0 and a - es[i] < 0x60 else a
            for r in refs.get(st, ()):
                o = own(r)
                if o is not None: fiown[fi].add(o); owners[o].add(fi)
        ok = {a: r for a, r in s.fi.items() if 'err' not in r}; bad = {a: r['err'] for a, r in s.fi.items() if 'err' in r}
        J = {'funcinfo': [], 'scopes': [], 'throwinfo': []}; fil = defaultdict(bool)
        for a_, (fi_, tg_) in s.stubs.items(): fil[fi_] |= a_ in live
        for a in sorted(ok):
            r = ok[a]; J['funcinfo'].append({'addr': h(a), 'magic': h(r['magic']), 'maxState': r['maxState'], 'owners': [h(o) for o in sorted(fiown[a])],
                'stubs': [{'addr': h(k), 'head': h(live[k]) if k in live else 0, 'live': k in live, 'target': h(v[1])} for k, v in sorted(s.stubs.items()) if v[0] == a],
                'unwind': [[to if to < 0x80000000 else -1, h(act) if act else 0] for to, act, _ in r['unwind']],
                'tries': [{'low': lo, 'high': hi, 'catchHigh': ch, 'handlers': [{'adj': ad, 'type': h(ty) if ty else 0, 'disp': dp, 'addr': h(addr)} for ad, ty, dp, addr, _ in hs]} for (lo, hi, ch), hs in r['tries']],
                'ipmap': r['ipmap'], 'ESTypeList': h(r['ESTypeList']) if r['ESTypeList'] else 0, 'flags': r['flags']})
        for a in sorted(s.scopes):
            sc = s.scopes[a]; J['scopes'].append({'addr': h(a), 'ver': sc['ver'], 'handler': h(min(sc['handlers'])), 'handlers': [h(x) for x in sorted(sc['handlers'])], 'frame_disp': sc.get('disp'), 'use': [h(x) for x in sorted(sc['sites'])],
                'owners': sorted({h(own(x)) for x in sc['sites'] if own(x) is not None}), 'funcs': [h(x) for x in sc['funcs']], 'trylevel_max': sc['level'], 'nonconst_stores': [h(x) for x in sc['nonconst']], 'err': sc['err'], 'records': [[e if e < 0x80000000 else -1 - (0xffffffff - e), h(f) if f else 0, h(hd)] for e, f, hd, _ in sc['recs']]})
        for a in sorted(tinfo):
            t = tinfo[a]; J['throwinfo'].append({'addr': h(a), 'attrs': t['attrs'], 'unwind': h(t['unwind']) if t['unwind'] else 0,
                'types': [{'type': h(ty), 'size': sz, 'copy': h(cp) if cp else 0} for _, ty, sz, cp, _ in t['types']]})
        J['conts'] = {h(k): h(v) for k, v in sorted(s.conts.items())}
        J['registrations'] = [{'site': h(a), 'handlers': [h(x) for x in r['handlers']], 'kind': r['kind'], 'frame_disp': r['disp']} for a, r in sorted(s.regsites.items())]
        J['helpers'] = [{'addr': h(R), 'arg': d['k'], 'handlers': [h(x) for x in sorted(d['handlers'])], 'frame_disp': d['disp'], 'err': d.get('err')} for R, d in sorted(s.hlp.items())]
        J['jtab_red'] = [{'site': h(a), 'why': w} for a, w in sorted(getattr(X, 'jtab_red', {}).items())]; J['jtab_sites'] = len(getattr(X, 'jtab', ()))
        J['jtab_tables'] = [{'site': h(a), 'base': h(t.base), 'lo': t.lo, 'hi': t.hi, 'words': [h(w) for w in t.addrs], 'targets': [h(w) for w in t]} for a, t in sorted(getattr(X, 'jtab_ok', {}).items())]
        J['seh_red'] = [{'site': h(a), 'why': w} for a, w in s.seh_red]; J['fs0_restores'] = s.fs0_other
        pres = {}; MASK = {'ebx': 1, 'esi': 2, 'edi': 4, 'ebp': 8}
        for e in es:
            r = s.summary(e) if e in X.insns else None
            pres[h(e)] = [sum(MASK[x] for x in r[0]), -1 if r[1] is None else r[1]] if r else [0, -1]
        pw = Counter(); loc = []
        for e in es:
            r = s.summary(e) if e in X.insns else None
            for x in CALLEE_ORDER:
                if r is None or x not in r[0]:
                    c = s.why(e, x) if r is not None else 'exit'; pw[x + ':' + c] += 1
                    if c == 'local': loc.append(h(e) + ':' + x)
        J['preserves_why'] = dict(sorted(pw.items())); J['preserves_local'] = loc
        J['preserves'] = pres      # entry -> [bitmask of ebx 1, esi 2, edi 4, ebp 8 proven restored on every path to every ret, ret n if every ret is balanced else -1]
        J['throw_sites'] = tsl; J['failed_throwinfo'] = tfail; J['throwers'] = [h(x) for x in s.throwers]; J['thrower_escapes'] = [h(x) for x in esc]
        J['text'] = [h(X.T0), h(X.T1)]                        # [lo, hi): the executable span of this image (rebased for a module); tools/gate_eh.py assigns the pack's code entries to images by it
        J['failed_funcinfo'] = [{'addr': h(k), 'err': v, 'live': fil[k]} for k, v in sorted(bad.items())]       # a frame-handler stub names a FuncInfo that did not parse: its entries are missing, the gate is red on any
        json.dump(J, open(X.outdir + '/ehtables.json', 'w'), indent=1, sort_keys=True)
        L = []; bm = Counter(r['magic'] for r in s.fi.values()); L.append('FuncInfo found %d parsed %d failed %d; by magic %s' % (len(s.fi), len(ok), len(bad), {h(k): v for k, v in sorted(bm.items())}))
        if bad: L.append('  failed: ' + ' '.join('%x:%s' % kv for kv in sorted(bad.items())[:5]))
        tys = {ty for r in ok.values() for _, hs in r['tries'] for _, ty, *_ in hs if ty}; thr = {t[1] for r in tinfo.values() for t in r['types']}
        L.append('unwind actions %d (!=0) of %d entries, try blocks %d, catch handlers %d, TypeDescriptors catch %d / throw %d' % (
            sum(1 for r in ok.values() for x in r['unwind'] if x[1]), sum(len(r['unwind']) for r in ok.values()), sum(len(r['tries']) for r in ok.values()),
            sum(len(hs) for r in ok.values() for _, hs in r['tries']), len(tys), len(thr)))
        sv = Counter(sc['ver'] for sc in s.scopes.values()); recs = [r for sc in s.scopes.values() for r in sc['recs']]
        L.append('scope tables v3 %d v4 %d (empty %d), records %d: filters %d, handlers %d (finally %d); SEH helpers %s' % (sv[3], sv[4], sum(1 for sc in s.scopes.values() if not sc['recs']),
            len(recs), sum(1 for r in recs if r[1]), len(recs), sum(1 for r in recs if not r[1]), ' '.join(h(k) for k in sorted(s.helpers))))
        L.append('throwers (_CxxThrowException, by EXCEPTION_RECORD template) %s; throw sites %d (non-constant ThrowInfo %d, rethrow %d), ThrowInfo %d (pmfnUnwind %d, copyFunction %d), failed %d, thrower escapes %d; catch continuations %d' % (
            ' '.join('%x' % x for x in s.throwers) or '-', len(tsl), sum(1 for x in tsl if x['ti'] is None), sum(1 for x in tsl if x['ti'] == h(0)), len(tinfo), sum(1 for t in tinfo.values() if t['unwind']),
            len({t[3] for r in tinfo.values() for t in r['types'] if t[3]}), len(tfail), len(esc), len(s.conts)))
        L.append('SEH functions %d, non-constant trylevel stores %d, scope tables with err %d; stubs live %d unreferenced %d' % (len({x for sc in s.scopes.values() for x in sc['funcs']}), sum(len(sc['nonconst']) for sc in s.scopes.values()),
            sum(1 for sc in s.scopes.values() if sc['err']), len(live), len(s.stubs) - len(live)))
        L.append('frame-handler targets (stubs with FuncInfo): ' + ', '.join('%s x%d' % kv for kv in sorted(Counter(h(v[1]) for v in s.stubs.values()).items())) + '; mov-eax/jmp stubs whose target is not FuncInfo: ' + (', '.join('%x x%d' % kv for kv in s.noise.most_common(4)) or 'none'))
        L.append('functions owning a FuncInfo %d, of which decomp %d; FuncInfo without a referenced stub %d' % (len(owners), sum(1 for o in owners if o in X.fsize), sum(1 for a in ok if not fiown[a])))
        scan = set()
        for nm, va, vs, ro, rs in X.secs:
            if X.intext(va): continue
            for mg in MAGIC:
                b = mg.to_bytes(4, 'little'); i = X.d.find(b, ro, ro + min(vs, rs))
                while i >= 0:
                    if (i - ro) % 4 == 0: scan.add(va + i - ro)
                    i = X.d.find(b, i + 1, ro + min(vs, rs))
        miss = sorted(scan - set(s.fi)); tmpl = set()
        for r in X.relocs:
            v = X.u32(r)
            for a in miss:
                if a - 0x30 <= v < a: tmpl.add(a)
        L.append('cross-check: .rdata/.data magic scan %d, walk %d, scan-only %d: %s' % (len(scan), len(s.fi), len(miss), ' '.join('%x%s' % (a, '(EXCEPTION_RECORD template)' if a in tmpl else '(unreferenced)') for a in miss[:5])))
        if miss: L.append('  gap: scan-only hits are the ExceptionInformation[0] magic of EXCEPTION_RECORD templates (copied by the thrower / used by the filter), not FuncInfo; no stub points at them')
        return L

if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import universe

