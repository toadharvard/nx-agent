#!/usr/bin/env python3
"""gatecert - the proof half of the gate_reach request kind `gate_cert=<gate>` (L1 CONTRACT).

    A fact `site symbol S, argument k` is certified by the green gate G when every value that can reach argument k of every call of S
    comes from a producer in the universe G declared.  The proof is over the machine code of the linked objects, decoded by the pinned
    binutils objdump (`objdump -dr -t --no-show-raw-insn`, ILP32 AArch64); this module reads only that text.

    prove(prog, site, k, producers) -> (reasons [(reason, where, detail)], callers [str], values [str])    certified iff reasons is empty
    parse_objdump(text) -> Prog

Rule.  The set CALLERS(F) of a function F is every function holding a CALL26/JUMP26 relocation to F (a jump is a tail call: the argument
registers are those at the jump).  F's address taken anywhere (any other relocation to F, outside debug/unwind sections) means an indirect
caller; that is allowed only when each such relocation lies inside a producer data symbol (the producer row IS the indirect call path:
the table slot of the declared producer); otherwise `address-taken`.  The value of argument register R of a call site is the set of
VALUES found by a backward walk over the control-flow graph of the caller (every path; a join takes the union; a loop adds nothing new):
  - `mov`/`orr` from a register: the value of that register before the instruction;
  - result of a call (x0 after `bl F`): value PROD(F); any other caller-saved register after a call is clobbered (`clobbered`);
  - `adrp` + `add :lo12:` of the same symbol, or `adrp` + `ldr :lo12:` : the address of / a load from a constant offset of symbol Y;
  - `ldr` from a register that holds the address of Y plus a constant: load from Y at that offset; a register index is `index-unbounded`;
  - the value of an x0..x7 at the function entry: PARAM(j), judged by the callers of the function (the call graph, recursively);
  - anything else (constants, stack loads, arithmetic, an unknown call result) has no certificate (`constant`, `stack-load`, `computed`, ...).
A value is accepted iff it is PROD(F) with F a producer of kind function, or an address of / load from Y at a constant offset < size(Y) with
Y a producer of kind data (sizes from the symbol table), or PARAM(j) whose callers are all accepted.  A requested site with no call is
`no-site` (a vacuous certificate is no certificate).  Assumptions: the compiler (OWNER 1) and the pinned objdump decode the objects.
"""
import re

CALLER_SAVED = set(range(0, 19))
NOWRITE = {'str', 'strb', 'strh', 'stp', 'stur', 'sturb', 'sturh', 'stlr', 'stlrb', 'stlrh', 'stxr', 'stlxr', 'cmp', 'cmn', 'tst', 'ccmp', 'ccmn',
           'b', 'br', 'ret', 'cbz', 'cbnz', 'tbz', 'tbnz', 'nop', 'prfm', 'dmb', 'dsb', 'isb', 'bl', 'blr', 'svc', 'brk'}
TERMINATORS = {'b', 'br', 'ret'}
IGNORED_SECTIONS = ('.debug', '.eh_frame', '.note', '.comment', '.gnu', '.ARM', '.stack')


class Insn:
    __slots__ = ('addr', 'mn', 'ops', 'rel')

    def __init__(self, addr, mn, ops):
        self.addr, self.mn, self.ops, self.rel = addr, mn, ops, None


class Prog:
    def __init__(self):
        self.funcs = {}       # (object, name) -> {'start': int, 'section': str, 'insns': [Insn]}
        self.syms = {}        # (object, name) -> {'size': int, 'section': str, 'value': int, 'global': bool, 'defined': bool}
        self.datarel = []     # (object, section, offset, type, symbol) relocations of non-code sections
        self.objects = []

    def resolve(self, obj, name):
        """The canonical key of a symbol referenced from `obj`: its definition (a local of obj first, else the one global), else ('', name)."""
        if (obj, name) in self.syms and self.syms[(obj, name)]['defined']:
            return (obj, name)
        glob = sorted(k for k, s in self.syms.items() if k[1] == name and s['defined'] and s['global'])
        return glob[0] if len(glob) == 1 else ('', name)


def split_ops(s):
    out, cur, depth = [], '', 0
    for c in s:
        if c in '[{':
            depth += 1
        elif c in ']}':
            depth -= 1
        if c == ',' and depth == 0:
            out.append(cur.strip())
            cur = ''
        else:
            cur += c
    if cur.strip():
        out.append(cur.strip())
    return out


def reg(tok):
    """Register number of a w/x register token, 'zr' for wzr/xzr, 'sp', None otherwise."""
    m = re.fullmatch(r'[wx](\d+)', tok.strip())
    if m:
        return int(m.group(1))
    return {'wzr': 'zr', 'xzr': 'zr', 'sp': 'sp', 'wsp': 'sp'}.get(tok.strip())


def parse_objdump(text):
    """The Prog of the concatenated `objdump -dr -t --no-show-raw-insn` of objects/archives (one `file format` header per object)."""
    p = Prog()
    obj, fn, section, datasec, last = None, None, None, None, None
    for ln in text.split('\n'):
        m = re.match(r'^(.*?):\s+file format', ln)
        if m:
            obj = m.group(1)
            p.objects.append(obj)
            fn = section = datasec = last = None
            continue
        if obj is None:
            continue
        m = re.match(r'^Disassembly of section (\S+):', ln)
        if m:
            section, datasec, fn = m.group(1), None, None
            continue
        m = re.match(r'^RELOCATION RECORDS FOR \[(\S+)\]:', ln)
        if m:
            datasec, fn = m.group(1), None
            continue
        m = re.match(r'^([0-9a-f]+) (.{7}) (\S+)\t([0-9a-f]+) (.*)$', ln)
        if m and datasec is None and not re.match(r'^[0-9a-f]+ <', ln):
            name = m.group(5).split()[-1]
            flags, sec = m.group(2), m.group(3)
            p.syms[(obj, name)] = dict(size=int(m.group(4), 16), section=sec, value=int(m.group(1), 16), glob='g' in flags or 'w' in flags,
                                       defined=sec != '*UND*')
            p.syms[(obj, name)]['global'] = p.syms[(obj, name)].pop('glob')
            continue
        m = re.match(r'^([0-9a-f]+) <(\S+)>:$', ln)
        if m:
            fn = (obj, m.group(2))
            p.funcs[fn] = dict(start=int(m.group(1), 16), section=section, insns=[])
            last = None
            continue
        m = re.match(r'^\s+([0-9a-f]+):\s+(R_AARCH64_\w+)\s+(\S+)', ln)
        if m and fn is not None and last is not None:
            sym, _, add = m.group(3).partition('+')
            last.rel = (m.group(2), sym, int(add, 16) if add else 0)
            continue
        m = re.match(r'^([0-9a-f]+) (R_AARCH64_\w+)\s+(\S+)', ln)
        if m and datasec is not None:
            sym, _, add = m.group(3).partition('+')
            p.datarel.append((obj, datasec, int(m.group(1), 16), m.group(2), sym, int(add, 16) if add else 0))
            continue
        m = re.match(r'^\s+([0-9a-f]+):\s+([a-z][\w.]*)\s*(.*)$', ln)
        if m and fn is not None:
            ops = m.group(3).split('//')[0].strip()
            last = Insn(int(m.group(1), 16), m.group(2), ops)
            p.funcs[fn]['insns'].append(last)
    return p


class Fn:
    """One function with the control-flow predecessors of every instruction."""

    def __init__(self, key, rec):
        self.key, self.insns = key, rec['insns']
        self.at = {i.addr: n for n, i in enumerate(self.insns)}
        self.preds = [set() for _ in self.insns]
        for n, i in enumerate(self.insns):
            if n and self.insns[n - 1].mn not in TERMINATORS:
                self.preds[n].add(n - 1)
            if (i.mn == 'b' or i.mn.startswith('b.') or i.mn in ('cbz', 'cbnz', 'tbz', 'tbnz')) and i.rel is None:
                m = re.search(r'(?:^|[\s,])([0-9a-f]+)\s+<', i.ops)
                if m and int(m.group(1), 16) in self.at:
                    self.preds[self.at[int(m.group(1), 16)]].add(n)


def writes(i, r):
    """The kind of write the instruction i makes to register number r: None (no write), 'bl' (a call), or the mnemonic."""
    if i.mn in ('bl', 'blr'):
        return 'bl'
    if i.mn in NOWRITE:
        return None
    ops = split_ops(i.ops)
    if i.mn in ('ldp', 'ldpsw') and ops and (reg(ops[0]) == r or (len(ops) > 1 and reg(ops[1]) == r)):
        return i.mn
    return i.mn if ops and reg(ops[0]) == r else None


class Prover:
    def __init__(self, prog, producers):
        self.p = prog
        self.fns = {k: Fn(k, rec) for k, rec in prog.funcs.items()}
        self.prod_f = {s['symbol'] for s in producers if s['kind'] == 'function'}
        self.prod_d = {s['symbol'] for s in producers if s['kind'] == 'data'}
        self.reasons, self.callers, self.values = set(), set(), set()
        self.params_done = set()

    # ------------------------------------------------------------ values of a register before instruction n of function f
    def value(self, f, n, r, depth=0):
        """-> (set of values, set of fail reasons); values: ('prod', callee) ('page', sym) ('addr', sym, off) ('data', sym, off) ('param', j)."""
        vals, fails, seen, work = set(), set(), set(), [(n, r)]
        while work:
            i, rr = work.pop()
            if (i, rr) in seen:
                continue
            seen.add((i, rr))
            if i == 0:
                if isinstance(rr, int) and rr < 8:
                    vals.add(('param', rr))
                else:
                    fails.add('uninitialized')
            for q in f.preds[i]:
                ins = f.insns[q]
                if ins.mn in ('bl', 'blr'):
                    if not (isinstance(rr, int) and rr in CALLER_SAVED):
                        work.append((q, rr))              # callee-saved registers survive the call
                    elif ins.mn == 'bl' and ins.rel and rr == 0:
                        vals.add(('prod', self.p.resolve(f.key[0], ins.rel[1])[1]))
                    else:
                        fails.add('clobbered' if rr != 0 else 'call-result')
                    continue
                if not isinstance(rr, int) or writes(ins, rr) is None:
                    work.append((q, rr))
                    continue
                self.defn(f, q, ins, rr, vals, fails, work, depth)
        return vals, fails

    def defn(self, f, q, ins, rr, vals, fails, work, depth):
        ops = split_ops(ins.ops)
        mn = ins.mn
        if mn == 'mov' and len(ops) == 2 and reg(ops[1]) not in (None, 'zr') and not ops[1].startswith('#'):
            if reg(ops[1]) == 'sp':
                fails.add('computed')
            else:
                work.append((q, reg(ops[1])))
        elif mn == 'orr' and len(ops) == 3 and reg(ops[1]) == 'zr' and reg(ops[2]) is not None and not ops[2].startswith('#'):
            work.append((q, reg(ops[2])))
        elif mn == 'adrp' and ins.rel and 'ADR_PREL_PG_HI21' in ins.rel[0]:
            vals.add(('page', ins.rel[1], ins.rel[2]))
        elif mn == 'add' and len(ops) == 3 and ins.rel and 'ADD_ABS_LO12_NC' in ins.rel[0] and reg(ops[1]) is not None:
            base, bf = self.value(f, q, reg(ops[1]), depth + 1)
            fails |= bf
            for v in base:
                if v[0] == 'page' and v[1] == ins.rel[1]:
                    vals.add(('addr', ins.rel[1], ins.rel[2]))
                else:
                    fails.add('computed')
        elif mn == 'add' and len(ops) == 3 and ops[2].startswith('#') and reg(ops[1]) is not None and not ins.rel:
            base, bf = self.value(f, q, reg(ops[1]), depth + 1)
            fails |= bf
            c = int(ops[2][1:], 0)
            for v in base:
                if v[0] == 'addr':
                    vals.add(('addr', v[1], v[2] + c))
                else:
                    fails.add('computed')
        elif mn.startswith('ldr') and len(ops) >= 2 and ops[1].startswith('['):
            self.load(f, q, ins, ops, vals, fails, depth)
        elif mn in ('movz', 'movk', 'movn') or (mn == 'mov' and len(ops) == 2 and ops[1].startswith('#')):
            fails.add('constant')
        else:
            fails.add('computed')

    def load(self, f, q, ins, ops, vals, fails, depth):
        inner = [t.strip() for t in ops[1].strip('[]!').split(',')]
        if ops[1].endswith('!') or len(ops) > 2:
            fails.add('computed')
            return
        base = reg(inner[0])
        if base == 'sp' or base is None:
            fails.add('stack-load')
            return
        if len(inner) > 1 and not inner[1].startswith('#') and not (ins.rel and 'LO12' in ins.rel[0]):
            bv, bf = self.value(f, q, base, depth + 1)
            fails.add('index-unbounded' if any(v[0] in ('addr', 'page') for v in bv) else 'load')
            return
        off = int(inner[1][1:], 0) if len(inner) > 1 and inner[1].startswith('#') else 0
        bv, bf = self.value(f, q, base, depth + 1)
        fails |= bf
        for v in bv:
            if ins.rel and 'LDST' in ins.rel[0] and 'LO12' in ins.rel[0] and v[0] == 'page' and v[1] == ins.rel[1]:
                vals.add(('data', v[1], ins.rel[2]))
            elif not ins.rel and v[0] == 'addr':
                vals.add(('data', v[1], v[2] + off))
            else:
                fails.add('load')

    # ------------------------------------------------------------ the proof
    def judge(self, key, vals, where):
        for v in sorted(vals, key=repr):
            if v[0] == 'prod':
                if v[1] in self.prod_f:
                    self.values.add('prod:%s' % v[1])
                else:
                    self.reasons.add(('not-producer', where, v[1]))
            elif v[0] in ('addr', 'data'):
                s = self.sym(key[0], v[1])
                if v[1] in self.prod_d and s is not None and 0 <= v[2] < s['size']:
                    self.values.add('%s:%s' % (v[0], v[1]))
                else:
                    self.reasons.add(('not-producer', where, '%s+%d' % (v[1], v[2])))
            elif v[0] == 'page':
                self.reasons.add(('computed', where, 'page of %s' % v[1]))
            elif v[0] == 'param':
                self.param(key, v[1], where)

    def sym(self, obj, name):
        k = self.p.resolve(obj, name)
        return self.p.syms.get(k)

    def callsites(self, target):
        """[(function key, index)] of every call/jump relocation to the canonical symbol `target`."""
        out = []
        for k, f in sorted(self.fns.items()):
            for n, i in enumerate(f.insns):
                if i.mn in ('bl', 'b') and i.rel and re.search(r'(CALL|JUMP)26', i.rel[0]) and self.p.resolve(k[0], i.rel[1]) == target:
                    out.append((k, n))
        return out

    def address_taken(self, target):
        """The relocations that take the address of target outside a call: [(where, inside a producer data symbol?)]."""
        out = []
        for k, f in sorted(self.p.funcs.items()):
            if f['section'].startswith(IGNORED_SECTIONS):
                continue
            for i in f['insns']:
                if i.rel and not re.search(r'(CALL|JUMP)26', i.rel[0]) and self.p.resolve(k[0], i.rel[1]) == target:
                    out.append(('%s:%s+0x%x' % (k[0], k[1], i.addr), False))
        for obj, sec, off, typ, sym, add in self.p.datarel:
            if sec.startswith(IGNORED_SECTIONS) or self.p.resolve(obj, sym) != target:
                continue
            inside = any(s['section'] == sec and s['value'] <= off < s['value'] + s['size'] and k[1] in self.prod_d
                         for k, s in self.p.syms.items() if k[0] == obj and s['defined'])
            out.append(('%s:%s+0x%x' % (obj, sec, off), inside))
        return out

    def param(self, key, j, where):
        if (key, j) in self.params_done:
            return
        self.params_done.add((key, j))
        for w, inside in self.address_taken(key):
            if not inside:
                self.reasons.add(('address-taken', w, key[1]))
        sites = self.callsites(key)
        if not sites:
            if not self.address_taken(key):
                self.reasons.add(('no-caller', where, key[1]))
        for ck, n in sites:
            self.callers.add('%s:%s+0x%x' % (ck[0], ck[1], self.fns[ck].insns[n].addr))
            vals, fails = self.value(self.fns[ck], n, j)
            for r in sorted(fails):
                self.reasons.add((r, '%s:%s+0x%x' % (ck[0], ck[1], self.fns[ck].insns[n].addr), key[1]))
            self.judge(ck, vals, '%s:%s' % ck)

    def prove(self, site, k):
        target = self.p.resolve('', site)
        sites = self.callsites(target)
        if not sites:
            self.reasons.add(('no-site', site, '-'))
        for w, inside in self.address_taken(target):
            if not inside:
                self.reasons.add(('address-taken', w, site))
        for ck, n in sites:
            at = '%s:%s+0x%x' % (ck[0], ck[1], self.fns[ck].insns[n].addr)
            self.callers.add(at)
            vals, fails = self.value(self.fns[ck], n, k)
            for r in sorted(fails):
                self.reasons.add((r, at, site))
            self.judge(ck, vals, at)
        return sorted(self.reasons), sorted(self.callers), sorted(self.values)


def prove(prog, site, k, producers):
    return Prover(prog, producers).prove(site, k)
