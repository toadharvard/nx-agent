#!/usr/bin/env python3
"""Native-host check of bt / bts / btr / btc with a MEMORY operand and a REGISTER bit offset (0F A3 / AB / B3 / BB /r, modrm 00 reg=eax rm=ebx),
16- and 32-bit operand size, against the DSL semantics (proof/forms_alu.py `bt`, built by forms.build and evaluated with the DSL's own
concrete evaluator symform._ev).
  python3 test/bthost.py [--only bt] [--width 16|32]
Host side: a C driver (source below, built in /home/user/l3work/bthost, not in the repo) JIT-assembles `[66] 0F xx 03` (op [rbx], eax|ax) into an RWX
buffer; memory is a sparse 570 MB MAP_NORESERVE region around BASE = 0x20000000 filled, per case, in a 32-byte window around the byte floor(off/8)
with the pattern pat(seed, A); the driver returns CF and the window after the instruction.  Two pattern seeds.  The DSL side reads memory through the
same pattern function and applies its stores to the window.  Compared: CF and all 32 window bytes (so a wrong word/byte, a wrong bit, a wrong store
address or value all show).  Undefined flags (OF SF AF PF) are not compared (SDM: undefined).
Cover.  Result of the instruction on the offset register o (signed value s = sext(o, w)): SDM vol. 2 BT, "Bit(BitBase, BitOffset)": the operand is the
w/8-byte unit at EffectiveAddress + (w/8) * (s DIV w) (DIV = floor), the selected bit is s MOD w (floor, non-negative).  So the outcome depends on o only
through (q = floor(s / w), r = s mod w), and linearly in q for the address.
  w = 16: ALL 2^16 values of o (every (q, r)), exhaustive.
  w = 32: q ranges over the 2^27 values that keep the access inside the mapped window; the cover is: ALL r in 0..31 x q in {-4096..4095} (both signs and
  the sign change at 0) x for every k in 12..26 the boundaries +-2^k, +-2^k - 1, +-2^k + 1 x the extremes q = -2^26, -2^26 + 1, 2^26 - 1 (o = 0x80000000...
  0x7fffffff).  Argument: the address is base + 4 q computed in a 32-bit register, so a failure that depends on q only through carries/borrows or the
  sign shows at the power-of-two boundaries and the sign change; a failure of the bit selection depends on r (and the sign of s), covered for all r with
  both signs.  The DSL reads o only through ashr(o, lg) (q) and o & (w-1) (r) (forms_alu.py `bt`), by inspection of the builder."""
import argparse, os, struct, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, '..'))
from capstone import Cs, CS_ARCH_X86, CS_MODE_32
from proof import forms
from proof.symform import _ev

WORK = '/home/user/l3work/bthost'; BASE = 0x20000000; ADDR = 0x401000
OPC = {'bt': 0xA3, 'bts': 0xAB, 'btr': 0xB3, 'btc': 0xBB}; OPN = ['bt', 'bts', 'btr', 'btc']
CSRC = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <sys/mman.h>
typedef uint64_t (*fn_t)(uint64_t, uint64_t);
#define BASE 0x20000000ull
static uint8_t pat(uint32_t seed, uint32_t a) { return (uint8_t)((((a * 0x9E3779B1u) + seed * 0x85EBCA6Bu) >> 11) ^ (a >> 3)); }
int main(int argc, char **argv) {
  uint8_t *reg = mmap((void *)0x0F000000ull, 0x22000000ull, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE | MAP_FIXED_NOREPLACE, -1, 0);
  uint8_t *buf = mmap(0, 4096, PROT_READ | PROT_WRITE | PROT_EXEC, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (reg == MAP_FAILED || buf == MAP_FAILED) return 2;
  int opc = atoi(argv[1]), w = atoi(argv[2]); uint32_t seed = atoi(argv[3]), n;
  uint8_t *p = buf;
  const uint8_t pro[] = {0x53, 0x48, 0x89, 0xfb, 0x89, 0xf0};                 /* push rbx; mov rbx,rdi; mov eax,esi */
  memcpy(p, pro, sizeof pro); p += sizeof pro;
  if (w == 16) *p++ = 0x66;
  *p++ = 0x0f; *p++ = (uint8_t)opc; *p++ = 0x03;                              /* op [rbx], eax|ax */
  const uint8_t epi[] = {0x0f, 0x92, 0xc1, 0x0f, 0xb6, 0xc1, 0x5b, 0xc3};   /* setc cl; movzx eax,cl; pop rbx; ret */
  memcpy(p, epi, sizeof epi);
  fn_t f = (fn_t)buf;
  while (fread(&n, 4, 1, stdin) == 1) {
    uint32_t *offs = malloc(4 * (size_t)n); if (fread(offs, 4, n, stdin) != n) return 3;
    uint8_t *out = malloc(33 * (size_t)n);
    for (uint32_t i = 0; i < n; i++) {
      uint32_t o = offs[i]; int64_t s = (w == 16) ? (int64_t)(int16_t)o : (int64_t)(int32_t)o;
      uint64_t a = BASE + (uint64_t)(s >> 3);
      for (int k = 0; k < 32; k++) reg[a - 16 + k - 0x0F000000ull] = pat(seed, (uint32_t)(a - 16 + k));
      out[33 * i] = (uint8_t)f(BASE, o);
      for (int k = 0; k < 32; k++) out[33 * i + 1 + k] = reg[a - 16 + k - 0x0F000000ull];
    }
    fwrite(out, 33, n, stdout); fflush(stdout); free(offs); free(out);
  }
  return 0;
}
'''
def build_host():
    os.makedirs(WORK, exist_ok=True); c = WORK + '/host.c'; b = WORK + '/host'
    if not os.path.exists(b) or open(c).read() != CSRC:
        open(c, 'w').write(CSRC); subprocess.run(['gcc', '-O1', '-o', b, c], check=True)
    return b

def pat(seed, a): return ((((a * 0x9E3779B1) + seed * 0x85EBCA6B) & 0xFFFFFFFF) >> 11 ^ (a >> 3)) & 0xFF

def dsl_form(op, w):
    cs = Cs(CS_ARCH_X86, CS_MODE_32); cs.detail = True
    raw = bytes(([0x66] if w == 16 else []) + [0x0f, OPC[op], 0x03]); ins = list(cs.disasm(raw, ADDR)); assert len(ins) == 1 and ins[0].size == len(raw)
    return forms.build(ins[0], ADDR)

def dsl_eval(F, o, seed):
    """-> (cf, {addr: byte} of the window after the stores).  Registers: ebx = BASE, eax = o; flags other than cf irrelevant."""
    env = {'eax': o, 'ebx': BASE}; memo = {}
    def go(n):
        r = memo.get(n.u)
        if r is not None: return r
        if n.op == 'const': r = n.x
        elif n.op == 'var': r = env[n.x]
        elif n.op == 'load': a = go(n.a[0]); r = sum(pat(seed, (a + i) & 0xFFFFFFFF) << (8 * i) for i in range(n.w // 8))
        else: r = _ev(n, [go(c) for c in n.a])
        memo[n.u] = r; return r
    cf = next(go(v) for f, v, _ in F.flags if f == 'cf'); writes = {}
    for a, v in F.stores:
        av, vv = go(a), go(v)
        for i in range(v.w // 8): writes[(av + i) & 0xFFFFFFFF] = (vv >> (8 * i)) & 0xFF
    return cf, writes

def cases(w):
    if w == 16: return list(range(1 << 16))
    qs = set(range(-4096, 4096)) | {-(1 << 26), -(1 << 26) + 1, (1 << 26) - 1}
    for k in range(12, 27):
        for sgn in (1, -1):
            for d in (-1, 0, 1):
                q = sgn * (1 << k) + d
                if -(1 << 26) <= q < (1 << 26): qs.add(q)
    return sorted(((q * 32 + r) & 0xFFFFFFFF) for q in qs for r in range(32))

def run(args):
    host = build_host(); bad = 0; total = 0; stock_bad = 0
    for w in ([args.width] if args.width else [16, 32]):
        offs = cases(w)
        for op in ([args.only] if args.only else OPN):
            F = dsl_form(op, w); nbad = 0
            for seed in (1, 2):
                p = subprocess.Popen([host, str(OPC[op]), str(w), str(seed)], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
                CH = 1 << 15
                for i in range(0, len(offs), CH):
                    part = offs[i:i + CH]; p.stdin.write(struct.pack('<I', len(part)) + struct.pack('<%dI' % len(part), *part)); p.stdin.flush()
                    raw = p.stdout.read(33 * len(part))
                    for j, o in enumerate(part):
                        rec = raw[33 * j:33 * j + 33]; s = o - (1 << w) if (o >> (w - 1)) & 1 else o & ((1 << w) - 1)
                        s = (o & ((1 << w) - 1)); s = s - (1 << w) if s >> (w - 1) else s
                        a = (BASE + (s >> 3)) & 0xFFFFFFFF; win = [pat(seed, (a - 16 + k) & 0xFFFFFFFF) for k in range(32)]
                        cf, wr = dsl_eval(F, o & 0xFFFFFFFF if w == 32 else (o & 0xFFFF) | 0, seed)
                        for ad, b in wr.items():
                            k = (ad - (a - 16)) & 0xFFFFFFFF
                            if k < 32: win[k] = b
                            else: win = None; break
                        total += 1
                        if op == 'bt':       # the stock Sail formula (bit.sail x86_bt_0f_a3): byte floor(s/8), bit |s| mod 8 -- reported, not counted
                            mbit = (win[16] >> (abs(s) % 8)) & 1 if win is not None else 0
                            stock_bad += mbit != rec[0]
                        if win is None or cf != rec[0] or bytes(win) != rec[1:]:
                            nbad += 1
                            if nbad <= 5: print('MISMATCH %s w=%d seed=%d o=%#x s=%d: host cf=%d dsl cf=%d%s' % (op, w, seed, o, s, rec[0], cf, '' if win is not None and bytes(win) == rec[1:] else ' (memory differs)'))
                p.stdin.close(); p.wait()
            print('%-3s m%d reg-offset: %d offsets x 2 seeds, mismatches %d' % (op, w, len(offs), nbad), flush=True); bad += nbad
    print('total cases %d, mismatches %d; stock Sail bt formula (|s| mod 8) disagrees with the host in %d bt cases' % (total, bad, stock_bad)); return 1 if bad else 0

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--only'); ap.add_argument('--width', type=int); sys.exit(run(ap.parse_args()))
