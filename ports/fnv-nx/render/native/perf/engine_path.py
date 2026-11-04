#!/usr/bin/env python3
"""engine_path.py - the engine's own D3D9 draw path, counted on the AArch64 objects (stream P6n, RENDER_PERF.md §13).

    python3 -I render/native/perf/engine_path.py <obj dir> <link out dir> <spec.json> <out.json> [--explore]

<obj dir>       the prepared AArch64 ILP32 objects of one commit (fnv-nx-build `c/<c12>/<k>of4`, `obj/` of all shards)
<link out dir>  a link's `out/` of the same decomp commit (fnv-nx-build `link/<c12>`: addrmap.tsv, refs.tsv); it maps
                every undefined call of an object to an address and every address to the object and symbol defining it
<spec.json>     the path to count. It names engine addresses and holds vtables read from the user's exe
                (render/native/tools/exe_vftables.py), so it is a LOCAL file, never committed (RENDER_PERF.md §13
                lists what it holds). The count takes seconds: no farm job. Keys:
                  roles     {"<role>": {"addr": "<hex>"} | {"override": "<substring of an overrides.tsv symbol>"}}
                  per_draw / per_frame  {"<role>": <invocations>}
                  hot       [<function hex>]: direct calls followed (others count their call only); empty = all
                  vtables   {"<class>": [<slot target hex> | "d3d:<IDirect3DDevice9 method>"]}
                  regs      {"<function hex>": {"<register at entry>": "<class>"}}   (this pointers, arguments)
                  fields    {"<class>": {"<offset hex>": "<class>"}}; globals {"<data symbol>": "<class>"}
                  vcalls    {"<function hex>": {"<slot hex>" | "@<call-site offset hex>": "<function hex>" |
                             "<class>:<slot hex>" | "d3d:<method>" | "skip"}}
                  branches  {"<function hex>": {"<branch offset hex>": P(taken)}}
                  loops     {"<function hex>": {"<loop header offset hex>": trips}}
                  labels    {"<function hex>": "<name in report.json>"} (unlabelled functions sum as "other")
<out.json>      aggregate counts only: per role and per draw/frame instructions, self words per labelled function,
                D3D calls per draw, coverage. engine_fold.py / report.py fold it into report.json (`engine_d3d9_path`).
                With --explore it also prints every reached function by address (local inspection only).

Counting (static, per invocation, AArch64 instructions = 4-byte words):
* llvm-objdump -d -r of the defining object; basic blocks split at branch targets and after branches; a `br` that is
  not a tail call is a jump table whose cases are the blocks nothing else reaches (equal probability); back edges
  (DFS from the entry) are removed and a loop body adds (trips - 1) more iterations of its expected cost;
* `min` (shortest path to an exit, loops once), `exp` (expectation: spec `branches`, else the return heuristic -
  a short exit block is the cold side, P = 0.2 - else 1/2; loops at their trips), `max` (longest path); each call on
  a path adds the callee's inclusive count of the same kind (call cycles count 0);
* direct calls: same object -> its symbol; else refs.tsv (object, symbol) -> address -> addrmap.tsv -> defining
  object and symbol; bridges count their target;
* indirect calls (blr / br through a vtable slot): the receiver class comes from a straight-line register type flow
  (regs at entry, field loads, globals, vptr load, slot load) and the class's vtable, or from `vcalls`; unresolved
  ones count the call sequence only and are listed (coverage.vcalls_unresolved);
* `d3d:` targets are IDirect3DDevice9 methods: they end at our D3D9 front (RENDER_PERF.md §1 counts that side) and
  are tallied (d3d_calls_exp); libc/runtime calls count the call only.
"""
import argparse, collections, json, os, re, subprocess

OBJDUMP = os.environ.get('OBJDUMP', 'llvm-objdump')
EXIT_WORDS, EXIT_P = 4, 0.2          # return heuristic (Ball and Larus 1993): early exits are the cold side
COND = ('b.', 'cbz', 'cbnz', 'tbz', 'tbnz')
SYMLINE = re.compile(r'^([0-9a-f]+) <(.+)>:$')
INSLINE = re.compile(r'^\s*([0-9a-f]+):\s+(\S+)\s*(.*)$')
RELLINE = re.compile(r'^\s*([0-9a-f]+):\s+(R_AARCH64_\S+)\s+(\S+)')
HEX = re.compile(r'0x([0-9a-f]+)')
SLOT = re.compile(r'^w(\d+), \[x(\d+), #0x([0-9a-f]+)\]$')
VPTR = re.compile(r'^w(\d+), \[x(\d+)\]$')


class Obj:
    """One object's functions: symbol -> list of [offset, mnemonic, operands, reloc symbol or None]."""
    def __init__(self, path):
        out = subprocess.run([OBJDUMP, '-d', '-r', '--no-show-raw-insn', path], capture_output=True, text=True,
                             check=True).stdout
        self.funcs = {}
        cur = None; byoff = {}
        for line in out.splitlines():
            if line.startswith('Disassembly of section'):
                cur = None; byoff = {}; continue
            m = SYMLINE.match(line)
            if m:
                cur = []; self.funcs[m.group(2)] = cur; continue
            if cur is None:
                continue
            m = RELLINE.match(line)
            if m:
                off = int(m.group(1), 16)
                if off in byoff:
                    byoff[off][3] = m.group(3).split('+')[0]
                continue
            m = INSLINE.match(line)
            if m:
                ins = [int(m.group(1), 16), m.group(2), m.group(3).split('//')[0].strip(), None]
                cur.append(ins); byoff[ins[0]] = ins


def branch_target(ins, i, offs):
    off, mn, ops, rel = ins[i]
    if rel is not None or not (mn == 'b' or mn.startswith(COND)):
        return None
    t = HEX.findall(ops.split('<')[0])
    if t and int(t[-1], 16) in offs:
        return offs[int(t[-1], 16)]
    return None


def cfg(ins):
    """Blocks: list of (start, end exclusive, successors, exit kind)."""
    if not ins:
        return []
    offs = {x[0]: i for i, x in enumerate(ins)}
    lead = {0}
    for i, (off, mn, ops, rel) in enumerate(ins):
        if mn == 'b' or mn.startswith(COND) or mn in ('ret', 'br'):
            if i + 1 < len(ins):
                lead.add(i + 1)
            t = branch_target(ins, i, offs)
            if t is not None:
                lead.add(t)
    lead = sorted(lead)
    bidx = {s: k for k, s in enumerate(lead)}
    blocks = []
    for k, s in enumerate(lead):
        e = lead[k + 1] if k + 1 < len(lead) else len(ins)
        mn = ins[e - 1][1]
        t = branch_target(ins, e - 1, offs)
        tgt = bidx[t] if t is not None else None
        nxt = k + 1 if k + 1 < len(lead) else None
        if mn in ('ret', 'br'):
            succ, kind = [], mn
        elif mn == 'b':
            succ, kind = ([tgt], None) if tgt is not None else ([], 'tail')
        elif mn.startswith(COND):
            succ = [x for x in (tgt, nxt) if x is not None]; kind = None if succ else 'ret'
        else:
            succ, kind = ([nxt], None) if nxt is not None else ([], 'ret')
        blocks.append((s, e, succ, kind))
    # jump tables: a `br` not fed by `mov x16, xN` (the tail-call form) is a switch; its cases are the blocks no
    # other edge reaches (the table itself is in .rodata without relocations), taken with equal probability
    has_pred = {x for (_, _, sc, _) in blocks for x in sc}
    heads = [k for k, (s, e, sc, kd) in enumerate(blocks)
             if k and k not in has_pred and any(ins[i][1] != 'nop' for i in range(s, e))]
    for k, (s, e, sc, kd) in enumerate(blocks):
        if kd == 'br' and not (e - 2 >= s and ins[e - 2][1] == 'mov' and ins[e - 2][2].startswith('x16,')):
            cases = [h for h in heads if h != k]
            if cases:
                blocks[k] = (s, e, cases, None)
    return blocks


def dag(blocks):
    """Back edges removed (iterative DFS from block 0): forward successors, post-order, back edges (u, header)."""
    n = len(blocks); state = [0] * n; out = [[] for _ in range(n)]; order = []; back = []
    if not n:
        return out, order, back
    state[0] = 1; stack = [(0, iter(blocks[0][2]))]
    while stack:
        b, it = stack[-1]
        nxt = next(it, None)
        if nxt is None:
            state[b] = 2; order.append(b); stack.pop(); continue
        if state[nxt] == 1:
            back.append((b, nxt)); continue
        out[b].append(nxt)
        if state[nxt] == 0:
            state[nxt] = 1; stack.append((nxt, iter(blocks[nxt][2])))
    return out, order, back


class Counter:
    def __init__(self, objdir, linkout, spec, overrides):
        self.objdir = objdir; self.objs = {}; self.spec = spec
        self.addr = {}; self.rev = {}
        for line in open(os.path.join(linkout, 'addrmap.tsv')):
            if line.startswith('#'):
                continue
            f = line.rstrip('\n').split('\t')
            if len(f) >= 5 and f[1] == 'F' and f[3] and f[4]:
                self.addr[f[0]] = (f[4], f[3]); self.rev[(f[4], f[3])] = f[0]
        self.refs = {}
        for line in open(os.path.join(linkout, 'refs.tsv')):
            if line.startswith('#'):
                continue
            f = line.rstrip('\n').split('\t')
            if len(f) >= 6:
                self.refs[(f[0], f[1])] = (f[2], f[4])
        self.unit = {u.replace('/', '_') + '.o': u for (u, _) in self.refs}
        self.ovr = []
        for line in open(overrides):
            if line.strip() and not line.startswith('#'):
                f = line.rstrip('\n').split('\t')
                self.ovr.append((f[0].lower().rjust(8, '0'), f[1]))
        norm = lambda d: {k.lower().rjust(8, '0'): v for k, v in d.items()}
        self.vcalls = norm(spec.get('vcalls', {})); self.loops = norm(spec.get('loops', {}))
        self.hot = {h.lower().rjust(8, '0') for h in spec.get('hot', [])}   # direct calls followed (others cut)
        self.vtables = {c: [a if a.startswith('d3d:') else a.lower().rjust(8, '0') for a in v] for c, v in spec.get('vtables', {}).items()}
        self.d3d_stub = {a.lower().rjust(8, '0') for a in spec.get('pure_stubs', [])}
        self.branches = norm(spec.get('branches', {}))
        self.regs = norm(spec.get('regs', {})); self.fields = spec.get('fields', {}); self.globals = spec.get('globals', {})
        self.memo = {}; self.notes = collections.defaultdict(collections.Counter)
        self.seen = {}                     # function address -> info for --explore

    def typeflow(self, fa, ins):
        """Receiver classes of indirect calls: index of blr/br -> (class, slot offset hex). Straight-line tracking
        in address order: `types` (register -> class of the object it points to, from spec `regs` at entry, `fields`
        loads and `globals`), `vptr` (register -> class whose vtable it holds), `slotreg` (register -> (class, slot))."""
        types = {r: c for r, c in self.regs.get(fa, {}).items()}
        got = {}; vptr = {}; slotreg = {}; out = {}
        mov = re.compile(r'^[wx](\d+), [wx](\d+)$')
        ld = re.compile(r'^w(\d+), \[x(\d+)(?:, #0x([0-9a-f]+))?\]$')
        for i, (off, mn, ops, rel) in enumerate(ins):
            dst = None
            m = mov.match(ops) if mn == 'mov' else None
            if m:
                d, s_ = m.group(1), m.group(2)
                for t in (types, vptr, slotreg):
                    if s_ in t:
                        t[d] = t[s_]
                    else:
                        t.pop(d, None)
                continue
            m = ld.match(ops) if mn == 'ldr' else None
            if m:
                d, b, f = m.group(1), m.group(2), m.group(3)
                new = {}
                if rel and f is None or (rel and 'GOT' in str(rel)):
                    pass
                if b in got and f is None and got[b] in self.globals:
                    new['types'] = self.globals[got[b]]
                elif rel is not None:
                    got[d] = rel
                elif b in vptr and f is not None:
                    new['slotreg'] = (vptr[b], f)
                elif b in types and (f is None or f == '0'):
                    new['vptr'] = types[b]
                elif b in types and f is not None:
                    fc = self.fields.get(types[b], {}).get(f)
                    if fc:
                        new['types'] = fc
                for name, t in (('types', types), ('vptr', vptr), ('slotreg', slotreg)):
                    if name in new:
                        t[d] = new[name]
                    else:
                        t.pop(d, None)
                if rel is None:
                    got.pop(d, None)
                continue
            if mn in ('blr', 'br'):
                r = ops[1:]
                if mn == 'br' and i:
                    mm = mov.match(ins[i - 1][2])
                    if mm and mm.group(1) == r and ins[i - 1][1] == 'mov':
                        r = mm.group(2)
                if r in slotreg:
                    out[i] = slotreg[r]
            if mn in ('bl', 'blr'):
                for t in (types, vptr, slotreg, got):
                    for k in [k for k in t if int(k) < 19]:
                        del t[k]
                continue
            # any other instruction writing a w/x register kills its facts
            m = re.match(r'^[wx](\d+),', ops)
            if m and not mn.startswith(('st', 'cmp', 'tst', 'cb', 'tb', 'b.')):
                for t in (types, vptr, slotreg, got):
                    t.pop(m.group(1), None)
        return out

    def obj(self, name):
        if name not in self.objs:
            p = os.path.join(self.objdir, name)
            self.objs[name] = Obj(p) if os.path.exists(p) else None
        return self.objs[name]

    def key(self, a):
        return self.addr.get(a.lower().rjust(8, '0'))

    def role_key(self, d):
        if 'addr' in d:
            return self.key(d['addr'])
        for a, sym in self.ovr:
            if d['override'] in sym:
                return self.key(a)
        return None

    def callee(self, objname, sym):
        o = self.obj(objname)
        if o is not None and sym in o.funcs:
            return (objname, sym), 'local'
        r = self.refs.get((self.unit.get(objname), sym))
        if r is None:
            return None, 'unknown'
        cls, a = r
        if cls.startswith('code') and a:
            k = self.addr.get(a)
            return k, ('bridge' if cls == 'code-bridge' else 'code') if k else 'nodef'
        return None, cls or 'unknown'

    def count(self, key, stack=()):
        """Inclusive (min, exp, max, d3d Counter at exp weight) of one invocation of key = (object, symbol)."""
        if key in self.memo:
            return self.memo[key]
        if key is None or key in stack:
            return (0, 0.0, 0, collections.Counter())
        o = self.obj(key[0])
        if o is None or key[1] not in o.funcs:
            self.notes['function_missing'][key[1]] += 1
            return (0, 0.0, 0, collections.Counter())
        fa = self.rev.get(key, '?')
        ins = o.funcs[key[1]]
        blocks = cfg(ins)
        out, order, back = dag(blocks)
        vm = self.vcalls.get(fa, {})
        recv = self.typeflow(fa, ins)
        info = self.seen.setdefault(fa, {'sym': key[1], 'words': len(ins), 'vcalls': [], 'loops': [], 'calls': []})
        own = []
        for (s, e, succ, kind) in blocks:
            c = [e - s, float(e - s), e - s]; d3 = collections.Counter({'@' + fa: e - s})
            reg_slot = {}
            for i in range(s, e):
                off, mn, ops, rel = ins[i]
                m = SLOT.match(ops) if mn == 'ldr' else None
                if m:
                    reg_slot[m.group(1)] = m.group(3)
                tgt = None
                if mn == 'bl' and rel:
                    tgt, how = self.callee(key[0], rel)
                    if tgt is None:
                        self.notes['call_' + how][rel] += 1
                    info['calls'].append((off - ins[0][0], how, self.rev.get(tgt, rel[:40]) if tgt else rel[:40]))
                elif mn == 'b' and rel and i == e - 1:
                    tgt, how = self.callee(key[0], rel)
                    info['calls'].append((off - ins[0][0], 'tail-' + how, self.rev.get(tgt, '-') if tgt else rel[:40]))
                elif mn in ('blr', 'br') and ops.startswith('x'):
                    r = ops[1:]
                    if mn == 'br':           # tail call through a register: the slot of the mov source
                        mm = re.match(r'^x(\d+), x(\d+)$', ins[i - 1][2]) if i else None
                        if mm and ins[i - 1][1] == 'mov' and mm.group(1) == r:
                            r = mm.group(2)
                    slot = reg_slot.get(r)
                    t = vm.get('@%x' % (off - ins[0][0]))
                    cls = None
                    if t is None and i in recv and recv[i][0] in self.vtables:
                        cls, cslot = recv[i]
                        tab = self.vtables.get(cls, [])
                        k_ = int(cslot, 16) // 4
                        t = tab[k_] if k_ < len(tab) else None
                        if t is not None and t in self.d3d_stub:
                            t = 'skip'
                        slot = '%s:%s' % (cls, cslot)
                    if t is None and slot and ':' not in slot:
                        t = vm.get(slot)
                    if t and ':' in t and not t.startswith('d3d:'):      # "<class>:<slot offset>"
                        c_, s_ = t.split(':')
                        tab = self.vtables.get(c_, [])
                        t = tab[int(s_, 16) // 4] if int(s_, 16) // 4 < len(tab) else None
                    if t is None:
                        self.notes['vcall_unresolved'][(fa, slot)] += 1
                        info['vcalls'].append((off - ins[0][0], mn, slot, None))
                    elif t.startswith('d3d:'):
                        d3[t[4:]] += 1
                        info['vcalls'].append((off - ins[0][0], mn, slot, t))
                    elif t != 'skip':
                        tgt = self.key(t)
                        info['vcalls'].append((off - ins[0][0], mn, slot, t))
                        if tgt is None:
                            self.notes['vcall_target_undefined'][t] += 1
                if tgt is not None and self.hot and self.rev.get(tgt) not in self.hot and mn in ('bl', 'b'):
                    self.notes['call_cut'][self.rev.get(tgt)] += 1
                    tgt = None
                if tgt is not None:
                    cc = self.count(tgt, stack + (key,))
                    c = [c[0] + cc[0], c[1] + cc[1], c[2] + cc[2]]; d3.update(cc[3])
            own.append((c, d3))
        n = len(blocks)
        # branch probabilities over the forward edges: explicit `branches` (offset of the branch from the function
        # start -> probability taken), else the return heuristic (a successor that is a short exit block - at most
        # EXIT_WORDS words ending in ret - gets EXIT_P, the hot path continues), else 1/2
        bp = self.branches.get(fa, {})
        def short_exit(x):
            s2, e2, sc2, k2 = blocks[x]
            return k2 == 'ret' and e2 - s2 <= EXIT_WORDS and not sc2
        P = []
        for b, (s, e, succ, kind) in enumerate(blocks):
            fw = out[b]
            if len(fw) <= 1:
                P.append({x: 1.0 for x in fw}); continue
            rel_off = '%x' % (ins[e - 1][0] - ins[0][0])
            if ins[e - 1][1] == 'br':
                P.append({x: 1.0 / len(fw) for x in fw}); continue
            taken, fall = succ[0], succ[1]
            if rel_off in bp:
                pt = float(bp[rel_off])
            elif short_exit(taken) != short_exit(fall):
                pt = EXIT_P if short_exit(taken) else 1 - EXIT_P
            else:
                pt = 0.5
            P.append({taken: pt, fall: 1 - pt} if taken != fall else {taken: 1.0})
        # loops: header h with back edges from u; body = blocks reaching u without passing h
        extra_exp = [0.0] * n; extra_max = [0] * n; extra_d3 = [collections.Counter() for _ in range(n)]
        lp = self.loops.get(fa, {})
        heads = collections.defaultdict(list)
        for u, h in back:
            heads[h].append(u)
        preds = collections.defaultdict(list)
        for b, (s, e, succ, kind) in enumerate(blocks):
            for x in succ:
                preds[x].append(b)
        topo = list(reversed(order))
        # natural loops, innermost first: an outer iteration repeats the inner loops' extra trips too (nested
        # loops multiply), so one iteration costs its blocks' own words plus the extra already on inner headers
        bodies = []
        for h, us in heads.items():
            body = {h}; work = list(us)
            while work:
                x = work.pop()
                if x in body:
                    continue
                body.add(x); work.extend(preds[x])
            bodies.append((h, body))
        bodies.sort(key=lambda hb: (len(hb[1]), hb[0]))
        for h, body in bodies:
            rel_off = '%x' % (ins[blocks[h][0]][0] - ins[0][0])
            trips = lp.get(rel_off, 1)
            info['loops'].append((rel_off, sum(blocks[b][1] - blocks[b][0] for b in body), trips))
            if trips <= 1:
                continue
            prob = {h: 1.0}; it_exp = 0.0; it_d3 = collections.Counter()
            for b in topo:
                if b not in body or b not in prob:
                    continue
                it_exp += prob[b] * (own[b][0][1] + extra_exp[b])
                for k2, v2 in own[b][1].items():
                    it_d3[k2] += prob[b] * v2
                for k2, v2 in extra_d3[b].items():
                    it_d3[k2] += prob[b] * v2
                for x, px in P[b].items():
                    if x in body:
                        prob[x] = prob.get(x, 0.0) + prob[b] * px
            it_max = sum(own[b][0][2] + extra_max[b] for b in body)
            extra_exp[h] += (trips - 1) * it_exp; extra_max[h] += (trips - 1) * it_max
            for k2, v2 in it_d3.items():
                extra_d3[h][k2] += (trips - 1) * v2
        INF = float('inf')
        mn_ = [INF] * n; ex = [0.0] * n; mx = [0] * n; dx = [collections.Counter() for _ in range(n)]
        for b in order:
            c, d3 = own[b]
            base_e = c[1] + extra_exp[b]; base_x = c[2] + extra_max[b]
            dd = collections.Counter(d3); dd.update(extra_d3[b])
            if not out[b]:
                mn_[b], ex[b], mx[b] = c[0], base_e, base_x
            else:
                mn_[b] = c[0] + min(mn_[x] for x in out[b])
                ex[b] = base_e + sum(ex[x] * px for x, px in P[b].items())
                mx[b] = base_x + max(mx[x] for x in out[b])
                for x, px in P[b].items():
                    for k2, v2 in dx[x].items():
                        dd[k2] += v2 * px
            dx[b] = dd
        r = (mn_[0] if n and mn_[0] != INF else 0, ex[0] if n else 0.0, mx[0] if n else 0,
             dx[0] if n else collections.Counter())
        self.memo[key] = r
        return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('objdir'); ap.add_argument('linkout'); ap.add_argument('spec'); ap.add_argument('out')
    ap.add_argument('--overrides', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..',
                                                        'overrides.tsv'))
    ap.add_argument('--explore', action='store_true', help='print every reached function (LOCAL use: addresses)')
    a = ap.parse_args()
    spec = json.load(open(a.spec))
    C = Counter(a.objdir, a.linkout, spec, a.overrides)
    keys = {r: C.role_key(d) for r, d in spec['roles'].items()}
    labels = {k.lower().rjust(8, '0'): v for k, v in spec.get('labels', {}).items()}
    res = {'schema': 'fnv-nx engine d3d9 path v1', 'roles': {}}
    def split(cnt, mult=1.0):
        d3 = collections.Counter(); fn = collections.Counter()
        for m, v in cnt.items():
            if m.startswith('@'):
                fn[labels.get(m[1:], 'other (%s)' % ('unlabelled' if not a.explore else m[1:]))] += v * mult
            else:
                d3[m] += v * mult
        return d3, fn
    for r, k in keys.items():
        if k is None:
            res['roles'][r] = {'found': False}; continue
        c = C.count(k)
        o = C.obj(k[0]); n = len(o.funcs[k[1]]) if o and k[1] in o.funcs else 0
        d3, fn = split(c[3])
        res['roles'][r] = {'found': n > 0, 'static_words': n, 'min': c[0], 'exp': round(c[1], 1), 'max': c[2],
                           'd3d_calls_exp': {m: round(v, 2) for m, v in sorted(d3.items())},
                           'self_exp_by_function': {m: round(v, 1) for m, v in fn.most_common()}}
    for part in ('per_draw', 'per_frame'):
        tot = [0.0, 0.0, 0.0]; d3 = collections.Counter(); fn = collections.Counter()
        for r, mult in spec.get(part, {}).items():
            rr = res['roles'].get(r, {})
            if rr.get('found'):
                for i, f in enumerate(('min', 'exp', 'max')):
                    tot[i] += rr[f] * mult
                for m, v in rr['d3d_calls_exp'].items():
                    d3[m] += v * mult
                for m, v in rr['self_exp_by_function'].items():
                    fn[m] += v * mult
        res[part] = {'min': round(tot[0]), 'exp': round(tot[1]), 'max': round(tot[2]),
                     'd3d_calls_exp': {m: round(v, 2) for m, v in sorted(d3.items())},
                     'self_exp_by_function': {m: round(v, 1) for m, v in fn.most_common()},
                     'invocations': spec.get(part, {})}
    res['coverage'] = {'functions_counted': len(C.seen), 'words_static': sum(v['words'] for v in C.seen.values()),
                       'functions_missing': sum(C.notes['function_missing'].values()),
                       'vcalls_unresolved': sum(C.notes['vcall_unresolved'].values()),
                       'vcalls_resolved': sum(1 for v in C.seen.values() for x in v['vcalls'] if x[3]),
                       'calls_external': {k[5:]: sum(v.values()) for k, v in C.notes.items() if k.startswith('call_')}}
    json.dump(res, open(a.out, 'w'), indent=1)
    if a.explore:
        for fa, v in sorted(C.seen.items()):
            print('%s %4d %s' % (fa, v['words'], v['sym'][:80]))
            for x in v['vcalls']:
                print('      %-3s +%-5x slot %-5s -> %s' % (x[1], x[0], x[2], x[3]))
            for x in v['loops']:
                print('      loop +%s body %d trips %s' % x)
            for x in v['calls']:
                k = C.key(x[2]) if re.match(r'^[0-9a-f]{8}$', x[2]) else None
                o = C.obj(k[0]) if k else None
                w = len(o.funcs.get(k[1], ())) if o else '-'
                print('      call +%-5x %-12s %s %s%s' % (x + (w, '' if x[2] in C.hot else ' CUT' if C.hot else '')))
        for k, v in C.notes.items():
            print(k, v.most_common(12))
    print(json.dumps({k: v for k, v in res.items() if k not in ('roles',)}, indent=1)[:6000])


if __name__ == '__main__':
    main()
