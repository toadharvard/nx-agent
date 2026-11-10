#!/usr/bin/env python3
"""Gate for the DSL forms (fnvlift/dsl.py, proof/forms.py).  Two independent checks:
  prove.py --model MODULE   obligation per encoding: exists pre-state with DSL post-state != reference post-state;  unsat = proved.
  prove.py --backends       emitted C++ (g++ -std=c++17 -Wall -Wextra -Werror -fsanitize=undefined) agrees with the z3 term on N inputs.
Options: --only SUBSTR (form key or encoding hex)  --n 1000  --forms PATH (alternative forms.py, e.g. a deliberately broken copy)
         --cert DIR (default /home/user/l3work/proof)  --timeout SEC (per form, default 300)  --all (backends: every encoding, not one per key)
Reference model plug-in: a module on sys.path (proof/ itself) with one function
    model_post(code: bytes, pre: dict) -> dict
  pre  = {'regs': {'eax'..'edi': z3 BitVec(name,32)}, 'flags': {'cf','pf','af','zf','sf','of': z3 BitVec(name,1)},
          'mem': z3 Array(BV32->BV8) named 'mem', 'eip': BitVecVal(ADDR,32)}      (ADDR = 0x401000, code is ONE instruction)
  ret  = {'regs': {8 names: BV32}, 'flags': {6 names: BV1}, 'mem': Array BV32->BV8 (post-state memory), 'eip': BV32}
  All terms are over the pre variables; flags the model does not touch are returned as the pre variables.
  Optional key 'assume': a Bool over the pre variables (and free model variables) restricting the obligation to the states for which the
  model defines the behaviour (e.g. no segment-limit fault); the solver gets it as a conjunct, and it is part of the model hash.
  A model that cannot express the instruction raises an exception whose class is named ModelGap (result model-gap)."""
import argparse, hashlib, importlib, importlib.util, json, os, random, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, '..'))
import z3
from capstone import Cs, CS_ARCH_X86, CS_MODE_32
from enc_table import encodings
ADDR = 0x401000
REGS = ('eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi'); FLAGS = ('cf', 'pf', 'af', 'zf', 'sf', 'of')

def load_forms(path):
    if not path: import forms; return forms
    sp = importlib.util.spec_from_file_location('forms_alt', path); m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m); return m

def items(forms, args):
    cs = Cs(CS_ARCH_X86, CS_MODE_32); cs.detail = True; out = []
    for b in encodings():
        ins = list(cs.disasm(b, ADDR))
        assert len(ins) == 1 and ins[0].size == len(b), b.hex()
        F = forms.build(ins[0], ADDR)
        if args.only and args.only not in F.key and args.only not in b.hex(): continue
        w = ins[0].operands[0].size * 8 if ins[0].operands else 32
        out.append((F, b, w))
    return out

def zser(terms):
    """canonical DAG text of z3 terms (hash input for the model-side certificate)"""
    ids, out = {}, []
    def go(e):
        if e.get_id() in ids: return ids[e.get_id()]
        a = [go(c) for c in e.children()]; ids[e.get_id()] = i = len(ids)
        out.append('n%d=%s %s %s' % (i, e.decl().name(), ','.join('n%d' % j for j in a), e.sexpr() if not a else ''))
        return i
    for k, t in terms: out.append('%s n%d' % (k, go(t)))
    return '\n'.join(out)

def pre_state():
    return {'regs': {r: z3.BitVec(r, 32) for r in REGS}, 'flags': {f: z3.BitVec(f, 1) for f in FLAGS},
            'mem': z3.Array('mem', z3.BitVecSort(32), z3.BitVecSort(8)), 'eip': z3.BitVecVal(ADDR, 32)}

def compare(D, M):
    probe = z3.BitVec('probe', 32); diffs = []                      # (field, dsl term, model term, difference condition)
    for r in REGS: diffs.append((r, D['regs'][r], M['regs'][r], D['regs'][r] != M['regs'][r]))
    for f in FLAGS: diffs.append((f, D['flags'][f], M['flags'][f], z3.And(D['defined'][f], D['flags'][f] != M['flags'][f])))
    dm, mm = z3.Select(D['mem'], probe), z3.Select(M['mem'], probe)
    diffs.append(('mem@probe', dm, mm, dm != mm)); diffs.append(('eip', D['eip'], M['eip'], D['eip'] != M['eip']))
    return diffs

def prove(args):
    forms = load_forms(args.forms); mod = importlib.import_module(args.model); os.makedirs(args.cert, exist_ok=True)
    pre = pre_state(); tot = {'proved': 0, 'FAILED': 0, 'model-gap': 0, 'timeout': 0}; slow = []; T0 = time.time(); its = items(forms, args)
    for F, b, w in its:
        t0 = time.time(); res = None; ce = ''; msha = ''
        try:
            M = mod.model_post(b, pre); D = F.post(); diffs = compare(D, M)
            asm = M.get('assume')                                   # optional: pre-states the model defines (no fault); the obligation is over these
            msha = hashlib.sha256(zser([(k, m) for k, _, m, _ in diffs] + ([('assume', asm)] if asm is not None else [])).encode()).hexdigest()
            s = z3.Solver(); s.set('timeout', int(args.timeout * 1000)); s.add(z3.Or([d for *_, d in diffs]))
            if asm is not None: s.add(asm)
            r = s.check()
            if r == z3.unsat: res = 'proved'
            elif r == z3.sat:
                res = 'FAILED'; mo = s.model(); ev = lambda e: mo.eval(e, model_completion=True)
                fld = next(k for k, a, c, d in diffs if z3.is_true(ev(d)))
                inp = ' '.join('%s=%x' % (v, ev(z3.BitVec(v, 32)).as_long()) for v in REGS) + ' ' + ' '.join('%s=%d' % (v, ev(z3.BitVec(v, 1)).as_long()) for v in FLAGS)
                d = [(a, c) for k, a, c, _ in diffs if k == fld][0]
                ce = '  CE %s: %s | %s dsl=%s model=%s probe=%x' % (F.key, inp, fld, ev(d[0]), ev(d[1]), ev(z3.BitVec('probe', 32)).as_long())
            else: res = 'timeout'
        except Exception as e:
            if type(e).__name__ != 'ModelGap': raise
            res = 'model-gap'; ce = '  GAP %s: %s' % (F.key, str(e)[:100])
        dt = time.time() - t0; tot[res] += 1; slow.append((dt, F.key, b.hex()))
        print('%s %d %s %s %.2f' % (F.key, w, b.hex(), res, dt)); ce and print(ce)
        json.dump({'form': F.key, 'encoding': b.hex(), 'result': res, 'dsl_sha256': F.sha(), 'model_sha256': msha, 'z3': z3.get_version_string(),
                   'model': args.model, 'seconds': round(dt, 3)}, open(os.path.join(args.cert, '%s_%s.json' % (F.key.replace('|', '_').replace(',', '-'), b.hex())), 'w'))
    slow.sort(reverse=True)
    print('TOTAL forms=%d proved=%d FAILED=%d model-gap=%d timeout=%d seconds=%.1f slowest=%s %.1fs' % (
        len(its), tot['proved'], tot['FAILED'], tot['model-gap'], tot['timeout'], time.time() - T0, slow[0][1] if slow else '-', slow[0][0] if slow else 0))
    return tot['FAILED'] + tot['timeout']

HARN = r'''#include <cstdio>
#include <map>
#include <vector>
static std::map<u32, u8> ov; static std::vector<u32> wlog;
static u8 rb(u32 a) { auto it = ov.find(a); return it == ov.end() ? (u8)((a * 0x9E3779B1u) >> 24) : it->second; }
static void wb(u32 a, u8 v) { ov[a] = v; wlog.push_back(a); }
template<int N> struct Ref {
  u32 a; explicit Ref(u32 a_) : a(a_) {}
  operator u32() const { u32 v = 0; for (int i = N - 1; i >= 0; i--) v = (v << 8) | rb(a + (u32)i); return v; }
  Ref& operator=(u32 v) { for (int i = 0; i < N; i++) wb(a + (u32)i, (u8)(v >> (8 * i))); return *this; }
};
#define M8(a) Ref<1>{(a)}
#define M16(a) Ref<2>{(a)}
#define M32(a) Ref<4>{(a)}
'''
MAIN = r'''
int main() {
  unsigned idx; u32 v[14];
  while (scanf("%u", &idx) == 1) {
    for (int i = 0; i < 14; i++) if (scanf("%x", &v[i]) != 1) return 2;
    ov.clear(); wlog.clear(); u32 eip = 0; TAB[idx](v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7], v[8], v[9], v[10], v[11], v[12], v[13], eip);
    for (int i = 0; i < 14; i++) printf("%x ", v[i]);
    printf("%x", eip); u32 pa = wlog.empty() ? 0 : wlog.back() + 1;
    for (int k = 0; k < 4; k++) { u32 a = k < (int)wlog.size() ? wlog[k] : pa + (u32)k; printf(" %x %x", a, (u32)rb(a)); }
    printf("\n"); fflush(stdout);
  }
}
'''
SPECIAL = [0, 1, 2, 0x7f, 0x80, 0xff, 0x100, 0x7fff, 0x8000, 0xffff, 0x7fffffff, 0x80000000, 0xffffffff, 0xfffffffe, 0x12345678, 0xfffffff0, 0x10, 0x1f, 0x20]

def backends(args):
    forms = load_forms(args.forms); its = items(forms, args); seen = set(); sel = []
    for F, b, w in its:
        if args.all or F.key not in seen: seen.add(F.key); sel.append((F, b, w))
    wd = args.work; os.makedirs(wd, exist_ok=True)
    src = '\n'.join(['#include <cstdint>', 'typedef unsigned char u8; typedef unsigned short u16; typedef unsigned int u32; typedef unsigned long long u64;'] +
                    [l for l in importlib.import_module('fnvlift.dsl').PRELUDE.split('\n')[1:]] + [HARN] + [F.to_c('f%d' % i) for i, (F, _, _) in enumerate(sel)])
    src += '\ntypedef void (*FN)(%s);\nstatic FN TAB[] = {%s};\n' % (','.join(['u32&'] * 15), ','.join('f%d' % i for i in range(len(sel)))) + MAIN
    open(wd + '/gen.cpp', 'w').write(src); t0 = time.time()
    cc = subprocess.run(['g++', '-std=c++17', '-O1', '-Wall', '-Wextra', '-Werror', '-fsanitize=undefined', '-fno-sanitize-recover=all', wd + '/gen.cpp', '-o', wd + '/gen'],
                        capture_output=True, text=True)
    if cc.returncode: print('COMPILE FAILED\n' + cc.stderr[:1500]); return 1
    print('compiled %d forms as C++17 with -Wall -Wextra -Werror -fsanitize=undefined: ok (%.1fs)' % (len(sel), time.time() - t0))
    rng = random.Random(7); lines = []; cases = []
    for i in range(len(sel)):
        for _ in range(args.n):
            pick = lambda: rng.choice(SPECIAL) if rng.random() < .5 else rng.getrandbits(32)
            vals = [pick() for _ in range(8)] + [rng.getrandbits(1) for _ in range(6)]
            cases.append((i, vals)); lines.append('%d %s' % (i, ' '.join('%x' % x for x in vals)))
    rr = subprocess.run([wd + '/gen'], input='\n'.join(lines) + '\n', capture_output=True, text=True)
    out = rr.stdout.strip().split('\n') if rr.stdout.strip() else []
    if rr.returncode or len(out) != len(cases):
        bad = sel[cases[len(out)][0]][0].key if len(out) < len(cases) else '?'
        print('RUNTIME FAILURE (ubsan/abort) in form %s\n%s' % (bad, rr.stderr[:600])); return 1
    K = z3.BitVecVal(0x9E3779B1, 32); a = z3.BitVec('lam_a', 32); lam = z3.Lambda([a], z3.Extract(31, 24, a * K)); mem = z3.Array('mem', z3.BitVecSort(32), z3.BitVecSort(8))
    PR = [z3.BitVec('probe%d' % k, 32) for k in range(4)]; bad = 0; ci = 0; T0 = time.time()
    for i, (F, b, w) in enumerate(sel):
        D = F.post(); outs = [D['regs'][r] for r in REGS] + [D['flags'][f] for f in FLAGS] + [D['eip']] + [z3.Select(D['mem'], p) for p in PR]
        widths = [32] * 8 + [1] * 6 + [32] + [8] * 4; big = z3.substitute(z3.Concat(*outs), (mem, lam)); fails = 0; first = ''
        invars = [z3.BitVec(r, 32) for r in REGS] + [z3.BitVec(f, 1) for f in FLAGS]
        for _ in range(args.n):
            _, vals = cases[ci]; o = out[ci].split(); ci += 1
            cv = [int(x, 16) for x in o[:15]] + [int(x, 16) for x in o[15:]]
            subs = [(v, z3.BitVecVal(x, v.size())) for v, x in zip(invars, vals)] + [(PR[k], z3.BitVecVal(cv[15 + 2 * k], 32)) for k in range(4)]
            r = z3.simplify(z3.substitute(big, *subs)); assert z3.is_bv_value(r), 'not constant: ' + F.key
            r = r.as_long(); got = []
            for wd_ in reversed(widths): got.append(r & ((1 << wd_) - 1)); r >>= wd_
            got.reverse(); exp = cv[:15] + [cv[16 + 2 * k] for k in range(4)]
            if got != exp:
                fails += 1
                if not first: first = 'in=%s z3=%s c=%s' % (' '.join('%x' % x for x in vals), ' '.join('%x' % x for x in got), ' '.join('%x' % x for x in exp))
        bad += fails > 0
        print('%s %d %s %s %d/%d' % (F.key, w, b.hex(), 'agree' if not fails else 'DISAGREE', args.n - fails, args.n)); first and print('  ' + first)
    print('TOTAL forms=%d agree=%d disagree=%d inputs/form=%d compile+run+z3 %.1fs' % (len(sel), len(sel) - bad, bad, args.n, time.time() - t0))
    return bad

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--model'); ap.add_argument('--backends', action='store_true'); ap.add_argument('--only')
    ap.add_argument('--n', type=int, default=1000); ap.add_argument('--forms'); ap.add_argument('--cert', default='/home/user/l3work/proof')
    ap.add_argument('--timeout', type=float, default=300); ap.add_argument('--all', action='store_true'); ap.add_argument('--work', default='/home/user/l3work/proof/backends')
    a = ap.parse_args()
    if a.backends: sys.exit(1 if backends(a) else 0)
    if not a.model: sys.exit('prove.py: give --model MODULE (reference model plug-in, see the docstring) or --backends')
    sys.exit(1 if prove(a) else 0)
