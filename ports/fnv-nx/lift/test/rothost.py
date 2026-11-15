#!/usr/bin/env python3
"""Exhaustive check of the (patched) Sail x86 model against the native host for rol/ror/rcl/rcr/shl/shr/sar on 8- and 16-bit registers.
  python3 test/rothost.py [--only rol] [--width 8] [--quick]
Host side: a small C driver (source below, built in /home/user/l3work/rothost, not in the repo) JIT-assembles ONE instruction
(`op al|ax, cl` or `op al|ax, imm8`) into an mmap'd RWX buffer, loads EFLAGS with popfq, runs it and reads EFLAGS with pushfq, for ALL values
x ALL 8-bit counts (0..255) x both CF-in values.
Model side (path taken: evaluate the model's own z3 term in bulk): proof/model_sail.model_post gives the post-state z3 terms once per form (cl form:
one term, count = ecx; imm form: one term per masked count 0..31, plus direct terms for imm 32,33,63,100,255 that are checked to give the
same outputs as imm&31).  For each count the term is simplified with the count substituted and compiled to numpy; it is evaluated over
all (value, CF-in) cases at once (uint64 arrays, Python-int arrays for the few nodes wider than 64 bits).  Values the model leaves undefined
(free variables: undefined flags, upper register halves) are detected by evaluating twice (free = 0 and free = all ones); a gated output that
differs between the two is counted as `mfree` (model under-specified), never as equal.
SDM-defined outputs (gated): result; CF (all counts; shl/shr: not for masked count > width); OF only for masked count == 1; SF ZF PF for shifts
(count >= 1) and unchanged for rotates and for count 0; AF never.  Everything else the host shows is information (`undef`: cases where an
SDM-undefined output of the host differs from the model's value)."""
import argparse, os, subprocess, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, '..', 'proof')); sys.path.insert(0, os.path.join(HERE, '..'))
import z3
from proof import model_sail

WORK = '/home/user/l3work/rothost'
OPS = ['rol', 'ror', 'rcl', 'rcr', 'shl', 'shr', 'sar']; REG = {'rol': 0, 'ror': 1, 'rcl': 2, 'rcr': 3, 'shl': 4, 'shr': 5, 'sar': 7}
CSRC = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <sys/mman.h>
typedef void (*fn_t)(uint64_t, uint64_t, uint64_t, uint32_t *);
int main(void) {
  uint8_t *buf = mmap(0, 4096, PROT_READ | PROT_WRITE | PROT_EXEC, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (buf == MAP_FAILED) return 2;
  int op, w, form, cnt, fv;
  while (scanf("%d %d %d %d %d", &op, &w, &form, &cnt, &fv) == 5) {
    uint8_t *p = buf;
    const uint8_t pro[] = {0x49, 0x89, 0xc8, 0x89, 0xd1, 0x89, 0xf8, 0x56, 0x9d};          /* mov r8,rcx; mov ecx,edx; mov eax,edi; push rsi; popfq */
    memcpy(p, pro, sizeof pro); p += sizeof pro;
    if (w == 16) *p++ = 0x66;
    uint8_t modrm = 0xc0 | (op << 3);
    if (form == 0) { *p++ = (w == 8) ? 0xd2 : 0xd3; *p++ = modrm; }
    else { *p++ = (w == 8) ? 0xc0 : 0xc1; *p++ = modrm; *p++ = (uint8_t)cnt; }
    const uint8_t epi[] = {0x9c, 0x41, 0x59, 0x41, 0x89, 0x00, 0x45, 0x89, 0x48, 0x04, 0xc3}; /* pushfq; pop r9; mov [r8],eax; mov [r8+4],r9d; ret */
    memcpy(p, epi, sizeof epi);
    fn_t f = (fn_t)buf; uint32_t n = 1u << w, mask = n - 1; uint32_t out[2];
    static uint16_t rec[2 * 65536 * 2];
    for (uint32_t cf = 0; cf < 2; cf++) for (uint32_t v = 0; v < n; v++) {
      uint64_t fl = 2 | cf | (fv ? 0x8d4 : 0);
      f(v, fl, (uint64_t)cnt, out);
      uint32_t i = cf * n + v; rec[2 * i] = (uint16_t)(out[0] & mask); rec[2 * i + 1] = (uint16_t)(out[1] & 0x8d5);
    }
    fwrite(rec, 4, 2 * n, stdout); fflush(stdout);
  }
  return 0;
}
'''
def build_host():
    os.makedirs(WORK, exist_ok=True); c = WORK + '/host.c'; b = WORK + '/host'
    if not os.path.exists(b) or open(c).read() != CSRC:
        open(c, 'w').write(CSRC); subprocess.run(['gcc', '-O1', '-o', b, c], check=True)
    return subprocess.Popen([b], stdin=subprocess.PIPE, stdout=subprocess.PIPE)

def host_cases(proc, op, w, form, cnt, fv):
    proc.stdin.write(('%d %d %d %d %d\n' % (REG[op], w, form, cnt, fv)).encode()); proc.stdin.flush()
    n = 2 << w; raw = proc.stdout.read(4 * n)
    a = np.frombuffer(raw, dtype=np.uint16).reshape(n, 2)
    return a[:, 0].astype(np.uint64), a[:, 1].astype(np.uint64)

# ------------------------------------------------------------------------ z3 term -> numpy
K = z3
M64 = (1 << 64) - 1
def msk(w): return (1 << w) - 1
def wide(x): return np.asarray(x).astype(object) if not (isinstance(x, np.ndarray) and x.dtype == object) else x
def fit(x, w):
    if w > 64:
        return wide(x) & msk(w) if not isinstance(x, int) else x & msk(w)
    if isinstance(x, np.ndarray) and x.dtype == object: x = np.array([int(i) & msk(w) for i in x.flat], dtype=np.uint64).reshape(x.shape)
    if isinstance(x, int): return np.uint64(x & msk(w))
    return (x & np.uint64(msk(w))) if w < 64 else x

class Compiled:
    """z3 term -> list of numpy ops; inputs by name"""
    def __init__(self, terms): self.terms = terms
    def run(self, env):
        memo = {}
        def go(e):
            i = e.get_id()
            if i in memo: return memo[i]
            r = ev(e); memo[i] = r; return r
        def ev(e):
            k = e.decl().kind(); n = e.num_args()
            if z3.is_bool(e):
                if k == z3.Z3_OP_TRUE: return np.bool_(True)
                if k == z3.Z3_OP_FALSE: return np.bool_(False)
                a = [go(e.arg(j)) for j in range(n)]
                if k == z3.Z3_OP_NOT: return ~a[0]
                if k == z3.Z3_OP_AND: r = a[0]; [r := r & x for x in a[1:]]; return r
                if k == z3.Z3_OP_OR: r = a[0]; [r := r | x for x in a[1:]]; return r
                if k == z3.Z3_OP_XOR: return a[0] ^ a[1]
                if k == z3.Z3_OP_IMPLIES: return (~a[0]) | a[1]
                if k == z3.Z3_OP_EQ: return a[0] == a[1] if not z3.is_bool(e.arg(0)) else a[0] == a[1]
                if k == z3.Z3_OP_DISTINCT: return a[0] != a[1]
                if k == z3.Z3_OP_ITE: return np.where(a[0], a[1], a[2])
                w = e.arg(0).size()
                sg = lambda x: (x.astype(np.int64) if w <= 63 and not (isinstance(x, np.ndarray) and x.dtype == object) else x)
                def s(x):
                    x = wide(np.asarray(x)); return np.where((x >> (w - 1)) & 1 == 1, x - (1 << w), x)
                if k == z3.Z3_OP_ULT: return wide(a[0]) < wide(a[1]) if w > 64 else a[0] < a[1]
                if k == z3.Z3_OP_ULEQ: return wide(a[0]) <= wide(a[1]) if w > 64 else a[0] <= a[1]
                if k == z3.Z3_OP_UGT: return wide(a[0]) > wide(a[1]) if w > 64 else a[0] > a[1]
                if k == z3.Z3_OP_UGEQ: return wide(a[0]) >= wide(a[1]) if w > 64 else a[0] >= a[1]
                if k == z3.Z3_OP_SLT: return s(a[0]) < s(a[1])
                if k == z3.Z3_OP_SLEQ: return s(a[0]) <= s(a[1])
                if k == z3.Z3_OP_SGT: return s(a[0]) > s(a[1])
                if k == z3.Z3_OP_SGEQ: return s(a[0]) >= s(a[1])
                raise ValueError('bool op %s' % e.decl().name())
            w = e.size()
            if k == z3.Z3_OP_BNUM: return fit(e.as_long(), w) if w <= 64 else e.as_long()
            if k == z3.Z3_OP_UNINTERPRETED: return env[e.decl().name()]
            a = [go(e.arg(j)) for j in range(n)]
            if w > 64: a = [wide(x) if not isinstance(x, int) else x for x in a]
            if k == z3.Z3_OP_ITE: return np.where(a[0], a[1], a[2])
            if k == z3.Z3_OP_BAND: r = a[0]; [r := r & x for x in a[1:]]; return r
            if k == z3.Z3_OP_BOR: r = a[0]; [r := r | x for x in a[1:]]; return r
            if k == z3.Z3_OP_BXOR: r = a[0]; [r := r ^ x for x in a[1:]]; return r
            if k == z3.Z3_OP_BNOT: return fit(~wide(a[0]) if w > 64 else ~a[0], w)
            if k == z3.Z3_OP_BNEG: return fit((msk(w) + 1 - wide(a[0])) if w > 64 else (~a[0] + np.uint64(1)), w)
            if k == z3.Z3_OP_BADD: r = a[0]; [r := fit(r + x, w) for x in a[1:]]; return r
            if k == z3.Z3_OP_BSUB: return fit(a[0] - a[1], w)
            if k == z3.Z3_OP_BMUL: r = a[0]; [r := fit(r * x, w) for x in a[1:]]; return r
            if k in (z3.Z3_OP_BSHL, z3.Z3_OP_BLSHR, z3.Z3_OP_BASHR):
                x, sh = a[0], a[1]
                if w > 64:
                    x = wide(x); sh = wide(sh); big = sh >= w
                    shs = np.where(big, 0, sh).astype(object)
                    if k == z3.Z3_OP_BSHL: r = np.where(big, 0, fit(x << shs, w))
                    elif k == z3.Z3_OP_BLSHR: r = np.where(big, 0, x >> shs)
                    else:
                        neg = (x >> (w - 1)) & 1 == 1; r = np.where(big, np.where(neg, msk(w), 0), np.where(neg, fit((x >> shs) | (msk(w) ^ (msk(w) >> shs)), w), x >> shs))
                    return r
                sh = np.asarray(sh, dtype=np.uint64); big = sh >= np.uint64(w); shs = np.where(big, np.uint64(0), sh).astype(np.uint64)
                if k == z3.Z3_OP_BSHL: return np.where(big, np.uint64(0), fit(a[0] << shs, w))
                if k == z3.Z3_OP_BLSHR: return np.where(big, np.uint64(0), a[0] >> shs)
                sx = np.asarray(a[0], dtype=np.uint64); neg = ((sx >> np.uint64(w - 1)) & np.uint64(1)) == 1
                fill = np.uint64(msk(w)) ^ (np.uint64(msk(w)) >> shs)
                return np.where(big, np.where(neg, np.uint64(msk(w)), np.uint64(0)), np.where(neg, fit((sx >> shs) | fill, w), sx >> shs))
            if k == z3.Z3_OP_CONCAT:
                r = a[0]; wr = e.arg(0).size()
                for j in range(1, n):
                    wj = e.arg(j).size(); tot = wr + wj
                    if tot > 64: r = (wide(r) << wj) | wide(a[j])
                    else: r = (r << np.uint64(wj)) | a[j]
                    wr = tot
                return r
            if k == z3.Z3_OP_EXTRACT:
                hi, lo = e.params(); x = a[0]
                if isinstance(x, int) or (isinstance(x, np.ndarray) and x.dtype == object):
                    r = (np.asarray(x, dtype=object) >> lo) & msk(w)
                    return r if w > 64 else (np.uint64(int(r)) if np.ndim(r) == 0 else np.array(r.tolist(), dtype=np.uint64))
                return fit(np.asarray(x, dtype=np.uint64) >> np.uint64(lo), w)
            if k == z3.Z3_OP_ZERO_EXT: return a[0] if w <= 64 else wide(a[0])
            if k == z3.Z3_OP_SIGN_EXT:
                w0 = e.arg(0).size(); x = a[0]
                if w <= 64:
                    x = np.asarray(x, dtype=np.uint64); neg = ((x >> np.uint64(w0 - 1)) & np.uint64(1)) == 1
                    return np.where(neg, x | np.uint64(msk(w) ^ msk(w0)), x)
                x = wide(x) if not isinstance(x, int) else np.asarray(x, dtype=object); neg = ((x >> (w0 - 1)) & 1) == 1
                return np.where(neg, x | (msk(w) ^ msk(w0)), x)
            raise ValueError('bv op %s' % e.decl().name())
        return [go(t) for t in self.terms]

def frees(terms, known):
    seen, out, st = set(), set(), list(terms)
    while st:
        e = st.pop()
        if e.get_id() in seen: continue
        seen.add(e.get_id())
        if z3.is_const(e) and e.decl().kind() == z3.Z3_OP_UNINTERPRETED and e.decl().name() not in known: out.add((e.decl().name(), e.sort()))
        st.extend(e.children())
    return out

def pre_state():
    R = ('eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi'); F = ('cf', 'pf', 'af', 'zf', 'sf', 'of')
    return {'regs': {r: z3.BitVec(r, 32) for r in R}, 'flags': {f: z3.BitVec(f, 1) for f in F}, 'mem': z3.Array('mem', z3.BitVecSort(32), z3.BitVecSort(8)),
            'eip': z3.BitVecVal(0x401000, 32)}

def code_for(op, w, form, imm):
    modrm = 0xc0 | (REG[op] << 3)
    b = (b'\x66' if w == 16 else b'') + bytes([(0xd2 if w == 8 else 0xd3) if form == 0 else (0xc0 if w == 8 else 0xc1), modrm])
    return b + (bytes([imm]) if form else b'')

FL = {'cf': 1, 'pf': 4, 'zf': 0x40, 'sf': 0x80, 'of': 0x800}

def model_terms(op, w, form, imm):
    pre = pre_state(); M = model_sail.model_post(code_for(op, w, form, imm), pre)
    outs = [z3.Extract(w - 1, 0, M['regs']['eax'])] + [M['flags'][f] for f in ('cf', 'pf', 'zf', 'sf', 'of')]
    asm = M.get('assume')
    return outs, asm

def eval_model(outs, w, cnt, fv, n, val, cfin):
    """outputs as numpy arrays (result, cf, pf, zf, sf, of) for modes free=0 / free=ones"""
    pre_names = {'eax', 'ecx', 'cf', 'pf', 'af', 'zf', 'sf', 'of'}
    sub = [(z3.BitVec('ecx', 32), z3.BitVecVal(cnt, 32))] if cnt is not None else []
    ts = [z3.simplify(z3.substitute(t, *sub)) if sub else z3.simplify(t) for t in outs]
    fr = frees(ts, pre_names | {'ebx', 'edx', 'esp', 'ebp', 'esi', 'edi'})
    res = []
    for mode in (0, 1):
        env = {'eax': val, 'ecx': np.uint64(cnt or 0), 'cf': cfin, 'pf': np.uint64(fv), 'af': np.uint64(fv), 'zf': np.uint64(fv), 'sf': np.uint64(fv), 'of': np.uint64(fv)}
        for r in ('ebx', 'edx', 'esp', 'ebp', 'esi', 'edi'): env[r] = np.uint64(0)
        for name, srt in fr:
            sz = srt.size() if (srt.kind() == z3.Z3_BV_SORT) else 1
            v = msk(sz) if mode else 0
            env[name] = np.bool_(bool(v)) if (srt.kind() == z3.Z3_BOOL_SORT) else (np.uint64(v) if sz <= 64 else v)
        out = Compiled(ts).run(env)
        res.append([np.asarray(o, dtype=np.uint64) if not (isinstance(o, np.ndarray) and o.dtype == object) else o.astype(np.uint64) for o in out])
    return res

def gated(op, w, mc):
    """which of (cf, pf, zf, sf, of) are SDM-defined for masked count mc"""
    g = {'cf': True, 'pf': False, 'zf': False, 'sf': False, 'of': mc == 1}
    if op in ('shl', 'shr') and mc > w: g['cf'] = False
    if op in ('shl', 'shr', 'sar'): g['pf'] = g['zf'] = g['sf'] = True     # set from the result for mc >= 1, unchanged for mc == 0
    else: g['pf'] = g['zf'] = g['sf'] = True                              # rotates: never affected
    return g

def run_form(proc, op, w, form, counts, fv, stats):
    n = 2 << w; val = np.tile(np.arange(1 << w, dtype=np.uint64), 2); cfin = np.repeat(np.arange(2, dtype=np.uint64), 1 << w)
    cache = {}
    for c in counts:
        mc = c & 31
        if form == 0: outs, asm = cache.setdefault('cl', model_terms(op, w, 0, 0)); cnt = c
        else:
            if mc not in cache: cache[mc] = model_terms(op, w, 1, mc)
            outs, asm = cache[mc]; cnt = None
        hres, hfl = host_cases(proc, op, w, form, c, fv)
        (A, B) = eval_model(outs, w, cnt, fv, n, val, cfin)
        if form == 1 and c != mc and c in (32, 33, 63, 100, 255):                  # the imm term for c itself must give the same outputs as c&31
            o2, _ = model_terms(op, w, 1, c); (A2, _) = eval_model(o2, w, None, fv, n, val, cfin)
            stats['immchk'] += 1; stats['immbad'] += int(any((x != y).any() for x, y in zip(A, A2)))
        g = gated(op, w, mc); bad = np.zeros(n, bool); mfree = np.zeros(n, bool); undef = np.zeros(n, bool)
        # result
        bad |= (A[0] != hres) & (A[0] == B[0]); mfree |= (A[0] != B[0])
        for j, f in enumerate(('cf', 'pf', 'zf', 'sf', 'of'), 1):
            h = ((hfl & FL[f]) != 0).astype(np.uint64); free = (A[j] != B[j]); diff = (A[j] != h) & ~free
            if g[f]: bad |= diff; mfree |= free
            else: undef |= diff | free
        # AF is never compared
        ok = ~bad & ~mfree
        stats['cases'] += n; stats['equal'] += int(ok.sum()); stats['diff'] += int(bad.sum()); stats['mfree'] += int((mfree & ~bad).sum()); stats['undef'] += int(undef.sum())
        if bad.any() and stats['ex'] < 3:
            i = int(np.argmax(bad)); stats['ex'] += 1
            stats['exs'].append('count %d val %#x cf_in %d: host res %#x fl %#x model res %s' % (c, val[i], cfin[i], hres[i], hfl[i], [hex(int(x[i])) for x in A]))

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--only'); ap.add_argument('--width', type=int); ap.add_argument('--quick', action='store_true'); a = ap.parse_args()
    print('model IR', model_sail.IR, model_sail.ir_sha()[:16])
    proc = build_host(); tot = {'cases': 0, 'equal': 0, 'diff': 0, 'mfree': 0, 'undef': 0}
    for w in (8, 16):
        if a.width and a.width != w: continue
        for op in OPS:
            if a.only and a.only != op: continue
            for form in (0, 1):
                st = {'cases': 0, 'equal': 0, 'diff': 0, 'mfree': 0, 'undef': 0, 'ex': 0, 'exs': [], 'immchk': 0, 'immbad': 0}
                counts = range(0, 256, 17) if a.quick else range(256)
                run_form(proc, op, w, form, counts, 0, st)
                extra = [0, 1, 2, w - 1, w, w + 1, 31, 255] if w == 16 else list(counts)
                run_form(proc, op, w, form, [c for c in extra if c < 256], 1, st)             # second pre-flags pattern (PF AF ZF SF OF = 1)
                print('%s w%d %-3s: cases %d equal %d diff %d mfree %d undef-info %d  immcheck %d/%d bad' % (op, w, 'cl' if form == 0 else 'imm', st['cases'], st['equal'], st['diff'], st['mfree'], st['undef'], st['immchk'], st['immbad']))
                for e in st['exs']: print('    e.g.', e)
                for k in tot: tot[k] += st[k]
                sys.stdout.flush()
    print('TOTAL cases %d equal %d diff %d mfree %d undef-info %d' % (tot['cases'], tot['equal'], tot['diff'], tot['mfree'], tot['undef']))
    return 1 if tot['diff'] else 0

if __name__ == '__main__': sys.exit(main())
