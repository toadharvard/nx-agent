#!/usr/bin/env python3
"""R9 report: A64 instruction counts of lifted objects, and the shape checks the R9 tests use. Every instruction is classified by DECODING the object's
.text with capstone (CS_ARCH_ARM64: instruction ids, operands, registers read/written, relocations); nothing is decided by text.
    r9report.py <dir | .o | .a>...  [--facts DIR]
Prints: total instructions, ldar*, stlr*, ldaxr/stlxr, dmb ishld / ishst / ish, alignment-check overhead (a `tst` + conditional branch pair that
guards an ldar/stlr/ldaxr/stlxr), plain loads/stores through guest pointers and how many of them lack their dmb; and from memclass' class_totals.tsv
the static accesses per class (default DIR = /home/user/l3facts/memclass).
As a module (test/r9test.py): functions(path) -> {name: [capstone insn]}, scan(fn) -> dict."""
import io, os, sys, tempfile
import capstone
from capstone import arm64_const as K
from elftools.elf.elffile import ELFFile

md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
md.detail = True
ids = lambda *ns: frozenset(getattr(K, 'ARM64_INS_' + n) for n in ns if hasattr(K, 'ARM64_INS_' + n))
LDAR, STLR = ids('LDAR', 'LDARB', 'LDARH', 'LDAPR', 'LDAPRB', 'LDAPRH'), ids('STLR', 'STLRB', 'STLRH')
LDAX, STLX = ids('LDAXR', 'LDAXRB', 'LDAXRH', 'LDAXP'), ids('STLXR', 'STLXRB', 'STLXRH', 'STLXP')
ACQREL = LDAR | STLR | LDAX | STLX
LDS = ids('LDR', 'LDRB', 'LDRH', 'LDRSB', 'LDRSH', 'LDRSW', 'LDUR', 'LDURB', 'LDURH', 'LDURSB', 'LDURSH', 'LDURSW', 'LDP', 'LDNP', 'LDPSW')
STS = ids('STR', 'STRB', 'STRH', 'STUR', 'STURB', 'STURH', 'STP', 'STNP')
BRANCH = ids('B', 'CBZ', 'CBNZ', 'TBZ', 'TBNZ')
GPR = {}
for _n in range(31):
    for _p in 'XW':
        GPR[getattr(K, 'ARM64_REG_%s%d' % (_p, _n))] = _n
CPU = 27                        # struct cpu pointer register (README)
DMB = {'ish': K.ARM64_BARRIER_ISH, 'ishld': K.ARM64_BARRIER_ISHLD, 'ishst': K.ARM64_BARRIER_ISHST}


class Fn(list):
    """the decoded instructions of one function; .ext[i] = name of the external symbol a call/branch at i reaches (relocation or linked symbol)"""
    ext = None


def functions(path):
    """{name: Fn} of the FUNC symbols of an ELF object or executable"""
    with open(path, 'rb') as f:
        e = ELFFile(f)
        text = e.get_section_by_name('.text'); data = text.data(); base = text['sh_addr']; ti = e.get_section_index('.text')
        sy = e.get_section_by_name('.symtab'); syms = list(sy.iter_symbols())
        byaddr = {s['st_value']: s.name for s in syms if s.name and s['st_info']['type'] in ('STT_FUNC', 'STT_NOTYPE') and s['st_shndx'] != 'SHN_UNDEF'}
        rel = {}
        rs = e.get_section_by_name('.rela.text')
        if rs is not None:
            for r in rs.iter_relocations(): rel[r['r_offset']] = syms[r['r_info_sym']].name
        out = {}
        for s in syms:
            if s['st_info']['type'] != 'STT_FUNC' or s['st_shndx'] != ti or not s['st_size']: continue
            lo = s['st_value']; fn = Fn(md.disasm(data[lo - base:lo - base + s['st_size']], lo)); fn.ext = {}
            for i, x in enumerate(fn):
                if x.address in rel: fn.ext[i] = rel[x.address]
                elif x.id in BRANCH | ids('BL') and x.operands and x.operands[-1].type == K.ARM64_OP_IMM and not (lo <= x.operands[-1].imm < lo + s['st_size']):
                    fn.ext[i] = byaddr.get(x.operands[-1].imm, '?')
            out[s.name] = fn
        return out


def text_count(path):
    with open(path, 'rb') as f:
        e = ELFFile(f); t = e.get_section_by_name('.text')
        return sum(1 for _ in md.disasm(t.data(), 0)) if t else 0


def guest_flags(fn):
    """per instruction: a plain load/store through a GUEST pointer? A base register is a host pointer when on EVERY path to the access it was derived only
    from x27 (the cpu pointer) or from adrp (constant pool / symbol): forward must-analysis over the function's CFG; a call clobbers all but x27."""
    n = len(fn); idx = {x.address: i for i, x in enumerate(fn)}; succ = [[] for _ in range(n)]
    for i, x in enumerate(fn):
        if x.id in BRANCH and i not in fn.ext and x.operands and x.operands[-1].type == K.ARM64_OP_IMM and x.operands[-1].imm in idx: succ[i].append(idx[x.operands[-1].imm])
        uncond = (x.id == K.ARM64_INS_B and x.cc in (K.ARM64_CC_INVALID, K.ARM64_CC_AL)) or x.id in ids('RET', 'BR')
        if i + 1 < n and not uncond: succ[i].append(i + 1)
    # Spill slots: a host register spilled to [sp, #k] and reloaded keeps its status (host or not) when no instruction can have written the slot in between. State = registers
    # (ints) and slots (('s', k)). A slot dies when a store overlaps it or sp changes (writeback / sub / add sp). The address of the frame is taken by any other read of sp
    # (add x, sp, #t: the callee may write [t, ...), so slots >= t are not tracked at all; `mov x29, sp` followed by a use of x29 other than stp/ldp x29,x30: every slot).
    SPR = (K.ARM64_REG_SP, K.ARM64_REG_WSP); taken = 1 << 30
    fpuse = any(x.id not in (ids('STP', 'LDP')) and K.ARM64_REG_X29 in x.regs_access()[0] for x in fn)
    for x in fn:
        if x.id in LDS | STS: continue
        rd, wr = x.regs_access()
        if any(r in SPR for r in rd):
            if any(r in SPR for r in wr) or (x.id == K.ARM64_INS_MOV and x.operands and x.operands[0].type == K.ARM64_OP_REG and x.operands[0].reg == K.ARM64_REG_X29 and not fpuse): continue
            taken = min(taken, x.operands[-1].imm if x.id == K.ARM64_INS_ADD and x.operands[-1].type == K.ARM64_OP_IMM else 0)
    def slots(x):
        """-> (list of [offset, size] of an sp-based memory access of x, the data registers) or None"""
        m = [o.mem for o in x.operands if o.type == K.ARM64_OP_MEM]
        if not m or m[0].base not in SPR: return None
        rg = [o.reg for o in x.operands if o.type == K.ARM64_OP_REG]
        sz = 8 if x.reg_name(rg[0])[0] == 'x' else 4
        return [(m[0].disp + sz * k, sz) for k in range(len(rg))], [GPR.get(r) for r in rg]
    def tf(i, st):
        x = fn[i]
        if x.id in ids('BL', 'BLR'): return frozenset(e for e in st if isinstance(e, tuple) or 19 <= e <= 28)      # AAPCS: a call preserves x19..x28 (x27 = cpu pointer), spill slots are the caller's
        rd, wr = x.regs_access(); wr = {GPR[r] for r in wr if r in GPR}; out = set(st) - wr
        sl = slots(x) if x.id in LDS | STS else None
        if x.writeback or any(r in SPR for r in x.regs_access()[1]): out = {e for e in out if not isinstance(e, tuple)}
        elif sl and x.id in STS:
            out = {e for e in out if not (isinstance(e, tuple) and any(e[1] < o + z and o < e[1] + e[2] for o, z in sl[0]))}
            for (o, z), r in zip(sl[0], sl[1]):
                if r in st and o < taken: out.add(('s', o, z))
        elif sl and x.id in LDS:
            for (o, z), r in zip(sl[0], sl[1]):
                if ('s', o, z) in st and r is not None: out.add(r)
        if x.id == K.ARM64_INS_ADRP or (x.id in ids('MOV', 'ADD', 'SUB', 'ORR') and any(GPR.get(r) in st for r in rd)): out |= wr
        return frozenset(out)
    inn = [None] * n; inn[0] = frozenset([CPU]); work = [0]
    while work:
        i = work.pop(); o = tf(i, inn[i])
        for j in succ[i]:
            new = o if inn[j] is None else inn[j] & o
            if new != inn[j]: inn[j] = new; work.append(j)
    g = []
    for i, x in enumerate(fn):
        m = [o.mem for o in x.operands if o.type == K.ARM64_OP_MEM] if x.id in LDS | STS else []
        g.append(bool(m) and m[0].base != K.ARM64_REG_SP and (inn[i] is None or GPR.get(m[0].base) not in inn[i]))
    return g


def dmb(x):
    return next((k for k, v in DMB.items() if x.id == K.ARM64_INS_DMB and x.operands[0].barrier == v), None)


def scan(fn):
    """-> dict: counts of ldar / stlr / ldaxr / stlxr, dmb kinds, plain guest loads / stores, `uncovered` (plain guest accesses whose dmb is missing: a load needs
    `dmb ishld` (or ish) after it, a store `dmb ishst` (or ish) before it, with no other guest access and no branch between), xchg loops without `dmb ish` right
    after their stlxr, alignment checks (tst + conditional branch guarding an acquire/release access), calls to external symbols by name"""
    g = guest_flags(fn); n = len(fn)
    # split-lock windows: `bl fnvl_world_stop` ... `bl fnvl_world_resume`, straight-line (no branch, call, return, acquire/release access or exclusive between) with `dmb ish`
    # as the last barrier before the resume call; windows = [(stop index, resume index, guest loads, guest stores)]. Every guest access inside a window is covered by the world stop (all other threads are stopped; rt/fnvl_mem.h contract).
    win = []; bad_win = 0; inwin = [False] * n
    for i, x in enumerate(fn):
        if x.id == K.ARM64_INS_BL and fn.ext.get(i) == 'fnvl_world_stop':
            j = next((j for j in range(i + 1, n) if fn[j].id in BRANCH | ids('RET', 'BR', 'BL', 'BLR') or fn[j].id in ACQREL), None)
            ok = j is not None and fn[j].id == K.ARM64_INS_BL and fn.ext.get(j) == 'fnvl_world_resume' and dmb(fn[j - 1]) == 'ish' and any(g[k] for k in range(i + 1, j))
            win.append((i, j, sum(1 for k in range(i + 1, j) if g[k] and fn[k].id in LDS), sum(1 for k in range(i + 1, j) if g[k] and fn[k].id in STS)) if ok else None); bad_win += not ok
            if ok:
                for k in range(i + 1, j): inwin[k] = True
    r = dict(total=n, windows=[w for w in win if w], bad_windows=bad_win, stops=sum(1 for i in fn.ext if fn.ext[i] == 'fnvl_world_stop'), resumes=sum(1 for i in fn.ext if fn.ext[i] == 'fnvl_world_resume'), ldar=0, stlr=0, ldaxr=0, stlxr=0, ishld=0, ishst=0, ish=0, plain_ld=0, plain_st=0, uncovered=[], xchg_uncovered=0, aligncheck=0, calls=[])
    for i, x in enumerate(fn):
        k = dmb(x)
        if x.id in LDAR: r['ldar'] += 1
        elif x.id in STLR: r['stlr'] += 1
        elif x.id in LDAX: r['ldaxr'] += 1
        elif x.id in STLX:
            r['stlxr'] += 1
            r['xchg_uncovered'] += 0 if any(dmb(y) == 'ish' for y in fn[i + 1:i + 6]) else 1      # stlxr; cbnz [; b.ne of a CAS retry]; dmb ish
        elif k: r[k] += 1
        elif x.id == K.ARM64_INS_BL and i in fn.ext: r['calls'].append(fn.ext[i])
        if x.id in ids('TBZ', 'TBNZ'):                  # a one-bit test (width 2) is a single tbnz
            tgt = x.operands[-1].imm; near = fn[i + 1:i + 4] + [y for y in fn if tgt <= y.address < tgt + 12]
            r['aligncheck'] += any(y.id in ACQREL for y in near)
        if x.id == K.ARM64_INS_TST:                    # the conditional branch of the test may be scheduled a few instructions after it
            j = next((j for j in range(i + 1, min(n, i + 12)) if fn[j].id == K.ARM64_INS_B and fn[j].cc != K.ARM64_CC_INVALID), None)
            if j is not None:
                tgt = fn[j].operands[-1].imm
                near = fn[j + 1:j + 4] + [y for y in fn if tgt <= y.address < tgt + 12]     # the fall-through side or the branch target side
                r['aligncheck'] += any(y.id in ACQREL for y in near)
        if g[i] and not inwin[i]:
            ld = x.id in LDS; r['plain_ld' if ld else 'plain_st'] += 1; cov = False
            for j in (range(i + 1, n) if ld else range(i - 1, -1, -1)):
                y = fn[j]
                if dmb(y) in (('ishld', 'ish') if ld else ('ishst', 'ish')): cov = True; break
                if y.id in BRANCH | ids('RET', 'BR', 'BL', 'BLR') or y.id in ACQREL or g[j]: break
            if not cov: r['uncovered'].append((i, x.address))
    return r


def members(p):
    """paths of the object files at p: a file, a directory (recursively), an ar archive (extracted to a temp dir)"""
    if os.path.isdir(p):
        for d, _, fs in sorted(os.walk(p)):
            for f in sorted(fs):
                if f.endswith(('.o', '.a')): yield from members(os.path.join(d, f))
    elif p.endswith('.a'):
        b = open(p, 'rb').read(); assert b[:8] == b'!<arch>\n'; pos, names, td = 8, b'', tempfile.mkdtemp(prefix='r9ar')
        while pos + 60 <= len(b):
            h = b[pos:pos + 60]; nm = h[:16].decode().strip(); sz = int(h[48:58]); body = b[pos + 60:pos + 60 + sz]; pos += 60 + sz + (sz & 1)
            if nm == '//': names = body; continue
            if nm in ('/', '/SYM64/'): continue
            if nm.startswith('/') and nm[1:].isdigit(): nm = names[int(nm[1:]):].split(b'/\n')[0].decode()
            q = os.path.join(td, nm.rstrip('/')); open(q, 'wb').write(body)
            if body[:4] == b'\x7fELF': yield q
    elif os.path.exists(p): yield p


def main():
    a = [x for x in sys.argv[1:] if not x.startswith('--')]; facts = sys.argv[sys.argv.index('--facts') + 1] if '--facts' in sys.argv else '/home/user/l3facts/memclass'
    if '--facts' in sys.argv: a = [x for x in a if x != facts]
    T = dict(files=0, total=0, ldar=0, stlr=0, ldaxr=0, stlxr=0, ishld=0, ishst=0, ish=0, aligncheck=0, plain_ld=0, plain_st=0, unc=0, xchg_unc=0)
    for p in a:
        for o in members(p):
            T['files'] += 1; T['total'] += text_count(o)
            for fn in functions(o).values():
                s = scan(fn)
                for k in ('ldar', 'stlr', 'ldaxr', 'stlxr', 'ishld', 'ishst', 'ish', 'aligncheck', 'plain_ld', 'plain_st'): T[k] += s[k]
                T['unc'] += len(s['uncovered']); T['xchg_unc'] += s['xchg_uncovered']
    print('objects %(files)d  A64 instructions %(total)d' % T)
    print('ldar* %(ldar)d  stlr* %(stlr)d  ldaxr/stlxr %(ldaxr)d/%(stlxr)d  dmb ishld %(ishld)d  dmb ishst %(ishst)d  dmb ish %(ish)d' % T)
    print('alignment checks (tst + b.cond guarding ldar/stlr/ldaxr/stlxr) %(aligncheck)d (%(share).2f%% of instructions)' % dict(T, share=100.0 * T['aligncheck'] / max(T['total'], 1)))
    print('plain loads/stores through guest pointers %d, of which without their dmb %d; xchg loops without `dmb ish` %d' % (T['plain_ld'] + T['plain_st'], T['unc'], T['xchg_unc']))
    ct = os.path.join(facts, 'class_totals.tsv')
    if os.path.exists(ct):
        rows = [l.rstrip('\n').split('\t') for l in open(ct)][1:]; tot = sum(int(r[1]) + int(r[2]) for r in rows); pl = sum(int(r[1]) + int(r[2]) for r in rows if r[3] == '1')
        print('static accesses per class (memclass): ' + ' '.join('%s=%d' % (r[0], int(r[1]) + int(r[2])) for r in rows) + '; plain %d of %d (%.1f %%)' % (pl, tot, 100.0 * pl / max(tot, 1)))


if __name__ == '__main__':
    main()
