"""C backend == SMT term check (called by prove.py --backends): every selected form is emitted as a C++ function (fnvlift.dsl Form.to_c), compiled with
g++ -std=c++17 -Wall -Wextra -Werror -fsanitize=undefined -fno-sanitize-recover=all, run on N random inputs, and compared with the z3 post-state of the
same form: 8 registers, six flags, DF, other EFLAGS bits, eip, the fault code, and four probed memory bytes (memory = hash of the address overlaid by writes)."""
import os, subprocess
import z3
from fnvlift import dsl
from proof.detrand import Rng
REGS = ('eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi'); FLAGS = ('cf', 'pf', 'af', 'zf', 'sf', 'of'); XIN = ('df', 'flo', 'fs_base', 'gs_base') + tuple('sel_' + s for s in dsl.SEGS)
NIN = len(REGS) + len(FLAGS) + len(XIN)
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
  unsigned idx; u32 v[%d];
  while (scanf("%%u", &idx) == 1) {
    for (int i = 0; i < %d; i++) if (scanf("%%x", &v[i]) != 1) return 2;
    ov.clear(); wlog.clear(); u32 eip = 0; u32 fault = TAB[idx](%s, eip);
    for (int i = 0; i < %d; i++) printf("%%x ", v[i]);
    printf("%%x %%x", eip, fault); u32 pa = wlog.empty() ? 0 : wlog.back() + 1;
    for (int k = 0; k < 4; k++) { u32 a = k < (int)wlog.size() ? wlog[k] : pa + (u32)k; printf(" %%x %%x", a, (u32)rb(a)); }
    printf("\n"); fflush(stdout);
  }
}
'''
SPECIAL = [0, 1, 2, 0x7f, 0x80, 0xff, 0x100, 0x7fff, 0x8000, 0xffff, 0x7fffffff, 0x80000000, 0xffffffff, 0xfffffffe, 0x12345678, 0xfffffff0, 0x10, 0x1f, 0x20, 0x401000]

def run(args, forms, items, post_of):
    keys, its = items(forms, args); seen = set(); sel = []
    for key, src, F, b, w, err in its:
        if F is not None and (args.all or key not in seen): seen.add(key); sel.append((F, b, w))
    wd = args.work; os.makedirs(wd, exist_ok=True)
    src = '\n'.join(['#include <cstdint>'] + dsl.PRELUDE.split('\n') + [HARN] + [F.to_c('f%d' % i) for i, (F, _, _) in enumerate(sel)])
    ps = ','.join('v[%d]' % i for i in range(NIN))
    src += '\ntypedef u32 (*FN)(%s);\nstatic FN TAB[] = {%s};\n' % (','.join(['u32&'] * (NIN + 1)), ','.join('f%d' % i for i in range(len(sel)))) + MAIN % (NIN, NIN, ps, NIN)
    open(wd + '/gen.cpp', 'w').write(src)
    cc = subprocess.run(['g++', '-std=c++17', '-O1', '-Wall', '-Wextra', '-Werror', '-fsanitize=undefined', '-fno-sanitize-recover=all', wd + '/gen.cpp', '-o', wd + '/gen'], capture_output=True, text=True)
    if cc.returncode: print('COMPILE FAILED\n' + cc.stderr[:1500]); return 1
    print('compiled %d forms as C++17 with -Wall -Wextra -Werror -fsanitize=undefined: ok' % len(sel))
    rng = Rng(7); lines = []; cases = []
    for i in range(len(sel)):
        for _ in range(args.n):
            pick = lambda: rng.choice(SPECIAL) if rng.getrandbits(1) else rng.getrandbits(32)
            vals = [pick() for _ in range(8)] + [rng.getrandbits(1) for _ in range(6)] + [rng.getrandbits(1), pick(), pick(), pick()] + [rng.getrandbits(16) for _ in dsl.SEGS]
            cases.append((i, vals)); lines.append('%d %s' % (i, ' '.join('%x' % x for x in vals)))
    rr = subprocess.run([wd + '/gen'], input='\n'.join(lines) + '\n', capture_output=True, text=True)
    out = rr.stdout.strip().split('\n') if rr.stdout.strip() else []
    if rr.returncode or len(out) != len(cases):
        bad = sel[cases[len(out)][0]][0].key if len(out) < len(cases) else '?'
        print('RUNTIME FAILURE (ubsan/abort) in form %s\n%s' % (bad, rr.stderr[:600])); return 1
    K = z3.BitVecVal(0x9E3779B1, 32); a = z3.BitVec('lam_a', 32); lam = z3.Lambda([a], z3.Extract(31, 24, a * K)); mem = z3.Array('mem', z3.BitVecSort(32), z3.BitVecSort(8))
    PR = [z3.BitVec('probe%d' % k, 32) for k in range(4)]; bad = ci = 0
    invars = [z3.BitVec(r, 32) for r in REGS] + [z3.BitVec(f, 1) for f in FLAGS] + [z3.BitVec('df', 1), z3.BitVec('flo', 32), z3.BitVec('fs_base', 32), z3.BitVec('gs_base', 32)] + [z3.BitVec('sel_' + s, 16) for s in dsl.SEGS]
    for i, (F, b, w) in enumerate(sel):
        D = F.post(); outs = [D['regs'][r] for r in REGS] + [D['flags'][f] for f in FLAGS] + [D['flags']['df'], D['flo']] + [z3.BitVecVal(0, 32)] * 2 + \
            [D['eip'], z3.ZeroExt(24, D['fault'])] + [z3.Select(D['mem'], p) for p in PR]
        widths = [32] * 8 + [1] * 6 + [1, 32, 32, 32, 32, 32] + [8] * 4; big = z3.substitute(z3.Concat(*outs), (mem, lam)); fails = 0; first = ''
        for _ in range(args.n):
            _, vals = cases[ci]; o = [int(x, 16) for x in out[ci].split()]; ci += 1
            # C output: NIN inputs-after-call, eip, fault, 4 (addr, byte)
            got_c = o[:14] + [o[14], o[15]] + [0, 0] + [o[NIN], o[NIN + 1]] + [o[NIN + 3 + 2 * k] for k in range(4)]
            subs = [(v, z3.BitVecVal(x, v.size())) for v, x in zip(invars, vals)] + [(PR[k], z3.BitVecVal(o[NIN + 2 + 2 * k], 32)) for k in range(4)]
            r = z3.simplify(z3.substitute(big, *subs)); assert z3.is_bv_value(r), 'not constant: ' + F.key
            r = r.as_long(); got = []
            for wd_ in reversed(widths): got.append(r & ((1 << wd_) - 1)); r >>= wd_
            got.reverse()
            if got != got_c:
                fails += 1
                if not first: first = 'in=%s z3=%s c=%s' % (' '.join('%x' % x for x in vals), ' '.join('%x' % x for x in got), ' '.join('%x' % x for x in got_c))
        bad += fails > 0
        print('%s %d %s %s %d/%d' % (F.key, w, b.hex(), 'agree' if not fails else 'DISAGREE', args.n - fails, args.n)); first and print('  ' + first[:400])
    print('TOTAL forms=%d agree=%d disagree=%d inputs/form=%d' % (len(sel), len(sel) - bad, bad, args.n))
    return bad
