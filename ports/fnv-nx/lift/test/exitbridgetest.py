#!/usr/bin/env python3
"""Test of tools/exitgen.py, the exit bridges X_<A> (lifted code -> a native body of an exe address): the descriptor classes as arguments and as results, in all three conventions.
    exitbridgetest.py [--work DIR] [--units-only]

Part 1 (no toolchain): the row checks. A row without descriptor or pop, a bad descriptor, a duplicate address or symbol, a hidden result pointer under `fast`, `fast` with caller-pop:
each is a build error that lists every bad row; nothing is generated.
Part 2: every descriptor of the matrix gets its X_ bridge, a NATIVE body of that descriptor's AAPCS signature (a function of this test, compiled by the lifted-code flags' compiler; it
is not a product symbol) that returns a fingerprint of the bytes it received, and the bridge is run as the lifted dispatcher runs it (guest registers in w19.., c in x27) under Unicorn on
a guest stack the test built by the MSVC x86 conventions (Microsoft "Argument Passing and Naming Conventions"; Agner Fog, Calling conventions, tables 6 and 7). The expectations are
written here, from the documents, not from the generator: the fingerprint of the argument images, where each result goes (eax, edx:eax, ST0, the bytes at the hidden pointer and eax =
the pointer; nothing written past N bytes), and the esp after the return (the return address and the callee-popped stack words, or the return address alone for cdecl).
Exit status 1 on any difference; 3 (BLOCKED) when the link has an undefined product symbol."""
import argparse, os, random, struct, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(HERE)
for p in ('tools', 'test', ''): sys.path.insert(0, os.path.join(LIFT, p))
import bridgegen, exitgen

M64 = (1 << 64) - 1
FNV0, FNVP = 1469598103934665603, 1099511628211
RETS = ['v', 'i', 'q', 'f', 'd', 'a1', 'a2', 'a4', 'a8', 'a12', 'a16', 'a8hf', 'a16hd', 'a12hf', 'a4hf', 'm20', 'm4', 'a8x', 'a4x', 'a16x']
ARGS = ['i', 'q', 'f', 'd', 's4g', 's8g', 's12g', 's16g', 's3g', 's8hf', 's16hf', 's16hd', 's8hd', 's20r', 's5r']


def descs():
    out = []
    for r in RETS: out.append('stack:%s:i,q,f,d,s12g,s8hf,s20r' % r)
    for r in RETS: out.append('stack:%s:' % r)
    for a in ARGS: out.append('stack:i:%s' % a)
    for a in ARGS: out.append('stack:v:i,%s,i' % a)
    out += ['this:%s:i,q,f,d,s12g' % r for r in ('v', 'i', 'q', 'f', 'd', 'a8', 'a12', 'a8hf', 'm20', 'a8x')]
    out += ['this:i:i', 'this:v:i,s8hd,s3g', 'this:q:i,i,i']
    out += ['fast:%s:i,q,i,d,i,s8g' % r for r in ('v', 'i', 'q', 'f', 'd', 'a8', 'a12', 'a8hf', 'a16')]
    out += ['fast:i:i,i', 'fast:i:q,i,i', 'fast:v:f,i,d,i', 'fast:i:s8g,i,i,i', 'fast:i:i', 'fast:v:']
    return out


def arg_bytes(t):
    """size of the argument's memory image from its grammar token (re-derived here from the documents, not by the generator)"""
    if t in ('i', 'f'): return 4
    if t in ('q', 'd'): return 8
    # s<N>g, s<N>r, s<N>hf, s<N>hd: N is the digits right after `s`
    k = 1
    while k < len(t) and t[k].isdigit(): k += 1
    return int(t[1:k])


def parse_ret(r):
    if r in ('v', 'i', 'q', 'f', 'd'): return r, 0, True
    xm = r.endswith('x')
    if xm: r = r[:-1]
    n = ''
    for ch in r[1:]:
        if ch.isdigit(): n += ch
        else: break
    n = int(n)
    return r[0], n, (r[0] == 'a' and not xm and n in (1, 2, 4, 8))


def fold(h, b):
    for x in b: h = ((h ^ x) * FNVP) & M64
    return h


def fillbytes(h, n): return bytes((h >> (8 * (k % 8))) & 0xff for k in range(n))


def expected(desc, imgs):
    """imgs: the memory image of each argument; returns the fingerprint h"""
    h = FNV0
    for b in imgs: h = fold(h, b)
    return h


NATIVE_HEAD = '''typedef unsigned char u8; typedef unsigned short u16; typedef unsigned int u32; typedef unsigned long long u64;
typedef float v8f __attribute__((vector_size(8))); typedef float v16f __attribute__((vector_size(16)));
static inline void cp(void *d, const void *s, u32 n) { u8 *a = (u8 *)d; const u8 *b = (const u8 *)s; for (u32 i = 0; i < n; i++) { a[i] = b[i]; __asm__ volatile("" ::: "memory"); } }
static inline u64 fold(u64 h, const void *p, u32 n) { const u8 *b = (const u8 *)p; for (u32 i = 0; i < n; i++) h = (h ^ b[i]) * 1099511628211ull; return h; }
static inline void fillb(void *d, u64 h, u32 n) { u8 *p = (u8 *)d; for (u32 i = 0; i < n; i++) p[i] = (u8)(h >> (8 * (i % 8))); }
'''


def native(k, desc):
    conv, ret, args, reg_ret = bridgegen.parse(desc)
    cls, rn, rk, re_ = ret
    rt = {'v': 'void', 'i': 'u32', 'q': 'u64', 'f': 'float', 'd': 'double', 'a': 'G%d' % rn, 'm': 'M%d' % rn, 'h': 'H%s_%d' % (re_, rk)}[cls]
    ps, body = [], ['u64 h = %du;' % FNV0 if False else 'u64 h = 1469598103934665603ull;']
    for i, (c, n, ak, e) in enumerate(args):
        pt = {'i': 'u32', 'q': 'u64', 'f': 'float', 'd': 'double', 'r': 'u32', 'g': 'G%d' % n, 'h': 'H%s_%d' % (e, ak) if c == 'h' else ''}[c]
        ps.append('%s a%d' % (pt, i))
        body.append('h = fold(h, (const void *)(unsigned long)a%d, %d);' % (i, n) if c == 'r' else 'h = fold(h, &a%d, sizeof a%d);' % (i, i))
    if cls == 'v': tail = '(void)h;'
    elif cls == 'i': tail = 'return (u32)h;'
    elif cls == 'q': tail = 'return h;'
    elif cls == 'f': tail = 'return (float)(u32)(h & 0xffff);'
    elif cls == 'd': tail = 'return (double)(u32)h;'
    elif cls == 'm': tail = '%s r; fillb(r.b, h, %d); return r;' % (rt, rn)
    else: tail = '%s r; fillb(&r, h, %d); return r;' % (rt, rn)
    return 'extern "C" %s tn_%d(%s) { %s %s }\n' % (rt, k, ', '.join(ps) or 'void', ' '.join(body), tail)


def units_part():
    bad = 0
    def check(name, ok):
        nonlocal bad
        print('%-78s %s' % (name, 'ok' if ok else 'FAIL')); bad += not ok
    good = ['00401000\tf1\tstack:i:i\tcaller', '00401010\tf2\tthis:i:i,q\tcallee', '00401020\tf3\tfast:v:i,i\tcallee']
    rows, err = exitgen.parse_rows(good)
    check('good rows: 3 rows, no error', len(rows) == 3 and not err)
    cases = [('no descriptor', ['00401000\tf1\t\tcaller'], 'no descriptor'), ('no pop', ['00401000\tf1\tstack:i:i\t'], 'pop is'), ('a pop that is neither', ['00401000\tf1\tstack:i:i\tboth'], 'pop is'),
             ('a descriptor outside the grammar', ['00401000\tf1\tpascal:i:i\tcaller'], 'descriptor'), ('a duplicate address', good + ['00401000\tf9\tstack:i:i\tcaller'], 'twice'),
             ('a duplicate symbol', good + ['00402000\tf1\tstack:i:i\tcaller'], 'two addresses'), ('a hidden pointer under fast', ['00401000\tf1\tfast:m20:i\tcallee'], 'not documented'),
             ('fast with caller-pop', ['00401000\tf1\tfast:i:i\tcaller'], 'pops its own'), ('a non-hex address', ['zz\tf1\tstack:i:i\tcaller'], 'not a hex'), ('a short row', ['00401000\tf1'], 'a row is')]
    for name, lines, key in cases:
        rows, err = exitgen.parse_rows(lines)
        check('error: ' + name, bool(err) and any(key in e for e in err))
    rows, err = exitgen.parse_rows(['00401000\tf1\t\tcaller', '00401010\tf2\tpascal:i:i\tcaller', '00401020\tf3\tstack:i:i\tcaller'])
    check('every bad row is listed (2 of 3), the good one is kept', len(err) == 2 and len(rows) == 1)
    r = subprocess.run([sys.executable, '-I', os.path.join(LIFT, 'tools', 'exitgen.py'), '--rows', '/dev/null', '--out', '/dev/null'], capture_output=True, text=True, env={})
    check('command line: an empty row list generates an empty file, exit 0', r.returncode == 0)
    return bad


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--work', default='/home/user/l3work/exitbridgetest'); ap.add_argument('--units-only', action='store_true'); a = ap.parse_args()
    bad = units_part()
    if a.units_only: print('exitbridgetest units: %d failed' % bad); sys.exit(1 if bad else 0)
    import instest
    from elftools.elf.elffile import ELFFile
    from unicorn import Uc, UcError, UC_ARCH_ARM64, UC_MODE_ARM
    from unicorn.arm64_const import UC_ARM64_REG_CPACR_EL1, UC_ARM64_REG_SP, UC_ARM64_REG_X30, UC_ARM64_REG_X0, UC_ARM64_REG_X1
    w = a.work; os.makedirs(w, exist_ok=True)
    ds = descs(); rng = random.Random(26)
    pops = {}
    rows = []
    for k, d in enumerate(ds):
        conv = d.split(':')[0]
        pops[k] = 'callee' if conv in ('this', 'fast') else ('callee' if k % 2 else 'caller')       # both pops for the stack convention
        rows.append('%08x\ttn_%d\t%s\t%s' % (0x401000 + 16 * k, k, d, pops[k]))
    rr, err = exitgen.parse_rows(rows)
    if err: sys.exit('exitgen rejected the matrix:\n' + '\n'.join(err))
    open(w + '/exits.cc', 'w').write(exitgen.generate(rr))
    open(w + '/natives.cpp', 'w').write(NATIVE_HEAD + bridgegen.types(sorted(set(ds))) + ''.join(native(k, d) for k, d in enumerate(ds)))
    drv = ['#include "fnvl.h"', 'extern "C" {'] + ['void X_%08x(void);' % (0x401000 + 16 * k) for k in range(len(ds))] + ['}',
           'typedef void (*fn_t)(void);', 'static fn_t T[%d] = { %s };' % (len(ds), ', '.join('X_%08x' % (0x401000 + 16 * k) for k in range(len(ds)))),
           'struct Rec { u32 idx, esp, ecx, edx, oeax, oedx, oesp, ora, st_lo, st_hi, depth, pad; };', 'static struct cpu cpu0;',
           'extern "C" u32 test_main(Rec *r, u32 n) {', '  c = &cpu0;', '  for (u32 i = 0; i < n; i++) {', '    fnvl_x87_init();', '    esp = r[i].esp; ecx = r[i].ecx; edx = r[i].edx; eax = 0xDEAD0001u; c->ra = 0;',
           '    T[r[i].idx]();', '    r[i].oeax = eax; r[i].oedx = edx; r[i].oesp = esp; r[i].ora = c->ra;',
           '    u32 d = 0; for (u32 k = 0; k < 8; k++) if (c->fx[k].k) d++; r[i].depth = d;',
           '    fv *p = &c->fx[c->ftop & 7]; u64 v = p->k == 1 ? p->v : 0xDEADDEADDEADDEADull; r[i].st_lo = (u32)v; r[i].st_hi = (u32)(v >> 32);', '  }', '  return 0;', '}']
    open(w + '/driver.cpp', 'w').write('\n'.join(drv) + '\n')
    fl = [f for f in instest.FLAGS if f != '-DFNVL_FLAGS_IO'] + ['-I', LIFT + '/rt']
    CF = ['-mabi=ilp32', '-O2', '-fPIC', '-ffreestanding', '-fno-stack-protector', '-fno-asynchronous-unwind-tables', '-fno-stack-clash-protection', '-c']
    jobs = [(['aarch64-linux-gnu-g++'] + fl + ['-x', 'c++'], w + '/driver.cpp'), (['aarch64-linux-gnu-g++'] + fl + ['-x', 'c++'], w + '/exits.cc'),
            (['aarch64-linux-gnu-g++'] + fl + ['-x', 'c++'], w + '/natives.cpp'), (['aarch64-linux-gnu-gcc'] + CF + ['-I', LIFT + '/rt'], LIFT + '/rt/x87.c')]
    objs = []
    for cmd, src in jobs:
        o = '%s/%s.o' % (w, os.path.basename(src).rsplit('.', 1)[0]); objs.append(o)
        r = subprocess.run(cmd + [src, '-o', o], capture_output=True, text=True)
        if r.returncode: sys.exit('compile %s: %s' % (src, r.stderr[:1500]))
    elf = w + '/exit.elf'
    r = subprocess.run(['aarch64-linux-gnu-ld', '-m', 'aarch64elf32', '-Ttext=0xE0000000', '-e', 'test_main', '-o', elf] + objs, capture_output=True, text=True)
    if r.returncode:
        if 'undefined reference' in r.stderr: instest.blocked(r.stderr)
        sys.exit('link: ' + r.stderr[:800])
    with open(elf, 'rb') as fh:
        e = ELFFile(fh); segs = [(p['p_vaddr'], p.data(), p['p_memsz']) for p in e.iter_segments() if p['p_type'] == 'PT_LOAD']
        sym = {x.name: x['st_value'] for x in e.get_section_by_name('.symtab').iter_symbols() if x.name}
    # ---- the cases: for every descriptor, 3 frames of random argument images
    GS, TAB, HSTACK, STOP = 0x20000000, 0x10000000, 0x70000000, 0x7e000000
    cases, frames = [], bytearray()
    FR = 0x200
    mem = {}                                        # address -> bytes to write before the run
    for k, d in enumerate(ds):
        conv, r_, a_ = d.split(':')
        rcls, rn, regres = parse_ret(r_)
        toks = a_.split(',') if a_ else []
        for rep in range(3):
            base = GS + FR * len(cases); sp = base + 0x80       # [sp] = the return address
            imgs, words, regs, nreg = [], [], [0, 0], 0
            hid = base + 0x180
            if not regres and rcls != 'v': words.append(struct.pack('<I', hid)); mem[hid] = b'\xa5' * (rn + 16)
            for i, t in enumerate(toks):
                n = arg_bytes(t); b = bytes(rng.randrange(256) for _ in range(n))
                if t in ('f',): b = struct.pack('<f', rng.uniform(-1e6, 1e6))
                if t in ('d',): b = struct.pack('<d', rng.uniform(-1e9, 1e9))
                if t.endswith('hf'): b = struct.pack('<%df' % (n // 4), *[rng.uniform(-100, 100) for _ in range(n // 4)])
                if t.endswith('hd'): b = struct.pack('<%dd' % (n // 8), *[rng.uniform(-100, 100) for _ in range(n // 8)])
                imgs.append(b)
                if (conv == 'this' and i == 0) or (conv == 'fast' and t == 'i' and nreg < 2):
                    regs[0 if (conv == 'this' or nreg == 0) else 1] = struct.unpack('<I', b)[0]; nreg += 1
                else: words.append(b + b'\0' * (-len(b) % 4))
            stack = struct.pack('<I', 0xAAAA0000 + len(cases)) + b''.join(words)
            mem[sp] = stack
            nw = sum(len(x) for x in words) // 4
            esp_after = sp + 4 + (4 * nw if pops[k] == 'callee' else 0)
            cases.append(dict(k=k, d=d, sp=sp, regs=regs, imgs=imgs, hid=hid, rcls=rcls, rn=rn, regres=regres, esp_after=esp_after, ret=r_, ra=0xAAAA0000 + len(cases)))
            frames += struct.pack('<12I', k, sp, regs[0], regs[1], 0, 0, 0, 0, 0, 0, 0, 0)
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    lo = min(v for v, dd, m in segs) & ~0xfff; hi = (max(v + m for v, dd, m in segs) + 0xffff) & ~0xfff; uc.mem_map(lo, hi - lo)
    for v, dd, m in segs: uc.mem_write(v, dd)
    uc.mem_map(HSTACK, 0x40000); uc.mem_map(STOP, 0x1000); uc.mem_map(GS, 0x10000 * (len(cases) * FR // 0x10000 + 1)); uc.mem_map(TAB, 0x10000 * (len(frames) // 0x10000 + 1))
    for ad, b in mem.items(): uc.mem_write(ad, b)
    uc.mem_write(TAB, bytes(frames))
    uc.reg_write(UC_ARM64_REG_CPACR_EL1, 3 << 20); uc.reg_write(UC_ARM64_REG_SP, HSTACK + 0x3f000); uc.reg_write(UC_ARM64_REG_X30, STOP)
    uc.reg_write(UC_ARM64_REG_X0, TAB); uc.reg_write(UC_ARM64_REG_X1, len(cases))
    try: uc.emu_start(sym['test_main'], STOP, count=100000000)
    except UcError as ex: sys.exit('unicorn: %s' % ex)
    raw = bytes(uc.mem_read(TAB, len(frames)))
    nbad = 0
    for i, cs in enumerate(cases):
        idx, esp0, ecx0, edx0, oeax, oedx, oesp, ora, stlo, sthi, depth, pad = struct.unpack_from('<12I', raw, 48 * i)
        h = expected(cs['d'], cs['imgs']); why = []
        rc, rn, regres = cs['rcls'], cs['rn'], cs['regres']
        if oesp != cs['esp_after']: why.append('esp %x want %x' % (oesp, cs['esp_after']))
        if ora != cs['ra']: why.append('c->ra %x want %x' % (ora, cs['ra']))
        st = stlo | sthi << 32
        if rc in ('f', 'd'):
            want = struct.unpack('<Q', struct.pack('<d', float((h & 0xffff) if rc == 'f' else (h & 0xffffffff))))[0]
            if depth != 1 or st != want: why.append('ST0 %x depth %d want %x' % (st, depth, want))
        elif depth != 0: why.append('x87 depth %d, want 0' % depth)
        if rc == 'i' and oeax != h & 0xffffffff: why.append('eax %x want %x' % (oeax, h & 0xffffffff))
        if rc == 'q' and (oeax, oedx) != (h & 0xffffffff, h >> 32 & 0xffffffff): why.append('edx:eax')
        if rc in ('a', 'h', 'm'):
            fb = fillbytes(h, rn)
            if regres:
                got = struct.pack('<II', oeax, oedx)[:rn]
                if got != fb: why.append('regs %s want %s' % (got.hex(), fb.hex()))
            else:
                got = bytes(uc.mem_read(cs['hid'], rn + 16))
                if got != fb + b'\xa5' * 16: why.append('hidden %s want %s' % (got.hex(), (fb + b'\xa5' * 16).hex()))
                if oeax != cs['hid']: why.append('eax %x want the hidden pointer %x' % (oeax, cs['hid']))
        if why: nbad += 1; print('%-40s DIFF %s' % (cs['d'], '; '.join(why)))
    print('exitbridgetest: %d descriptors, %d frames, %d differ; units %d failed' % (len(ds), len(cases), nbad, bad))
    sys.exit(1 if (bad or nbad) else 0)


if __name__ == '__main__': main()
