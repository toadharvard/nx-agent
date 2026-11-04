#!/usr/bin/env python3
"""c5_pic.py - V1-link claim C5: with -fPIC no reference bypasses the by-address rebinding of variables that objects
define natively at image addresses (no section-anchor offset or local-symbol relocation reaches such a variable).

    c5_pic.py <fixed dir> <objcopy dir> <units.json> <out tsv>

Independent of tools/link (own ELF reader elf32.py, own disassembly with capstone). Per rewritten object:

* rebound variables: defined data symbols named __fnv_<addr8> / __fnvnx_tls_<addr8> (what apply turned a native
  definition into), and every other symbol that shares their storage (aliases);
* every relocation whose symbol is a section symbol or a defined symbol other than the rebound name, whose target
  (symbol value + addend) lies inside a rebound variable -> 'direct' bypass;
* every code address materialization (adrp/add/ldr/str lo12, adr) through such a local symbol into a section that holds
  rebound variables -> followed through the instructions that use the register as a base (immediate offsets, add
  #imm, pre/post index) until it is overwritten; a reach into a rebound variable -> 'anchor' bypass;
* relocations against the variable's original name still left in the object -> 'unrenamed'.

Second part (missed rebinding, independent of apply's list): every variable a unit binds to an image address in
units.json (MSVC data names, `@@3`/`@@2`, demangled by llvm-undname) that the unit's original object still defines
(c++filt names, same qualified name) but whose fixed object defines no __fnv_ symbol for it -> 'missed' (local
statics included: their references never go through a symbol).

Writes the findings as TSV (local, decomp-derived names) and prints the counts.
"""
import collections
import json
import os
import re
import subprocess
import sys

import capstone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import elf32  # noqa: E402

IMG_LO, IMG_HI = 0x00fdf000, 0x02100000      # data image range (.rdata .. end of .data/.tls of FalloutNV.exe 1.4.0.525)
REB = re.compile(r'^__fnv(?:nx_tls)?_([0-9a-f]{8})$')
ADDRISH = {'ADR_PREL_PG_HI21', 'ADR_PREL_LO21', 'ADD_ABS_LO12_NC', 'LD_PREL_LO19', 'LDST8_ABS_LO12_NC',
           'LDST16_ABS_LO12_NC', 'LDST32_ABS_LO12_NC', 'LDST64_ABS_LO12_NC', 'LDST128_ABS_LO12_NC',
           'TLSDESC_ADR_PAGE21', 'TLSDESC_LD32_LO12_NC', 'TLSDESC_ADD_LO12_NC'}
CS = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
CS.detail = True


def reg_name(r):
    n = CS.reg_name(r)
    return 'x' + n[1:] if n and n[0] == 'w' else n


def follow(code, off, base_reg, base_val, limit=96):
    """Targets reached from register base_reg (holding base_val) in the instructions after offset off."""
    tracked = {base_reg: base_val}
    out = []
    for ins in CS.disasm(code[off + 4:off + 4 + 4 * limit], off + 4):
        ops = ins.operands
        for o in ops:
            if o.type == capstone.arm64.ARM64_OP_MEM:
                b = reg_name(o.mem.base)
                if b in tracked and o.mem.index == 0:
                    out.append((ins.address, tracked[b] + o.mem.disp, ins.mnemonic))
        # post-index writeback forms carry an extra imm operand
        if ins.mnemonic in ('ldr', 'str', 'ldp', 'stp', 'ldrb', 'strb', 'ldrh', 'strh', 'ldrsw', 'ldrsb', 'ldrsh',
                            'ldur', 'stur') and ops and ops[-1].type == capstone.arm64.ARM64_OP_IMM:
            for o in ops:
                if o.type == capstone.arm64.ARM64_OP_MEM and reg_name(o.mem.base) in tracked:
                    out.append((ins.address, tracked[reg_name(o.mem.base)] + ops[-1].imm, ins.mnemonic + '!'))
        if ins.mnemonic in ('add', 'sub') and len(ops) == 3 and ops[0].type == capstone.arm64.ARM64_OP_REG and \
                ops[1].type == capstone.arm64.ARM64_OP_REG and ops[2].type == capstone.arm64.ARM64_OP_IMM:
            src = reg_name(ops[1].reg)
            if src in tracked:
                v = tracked[src] + (ops[2].imm << (ops[2].shift.value if ops[2].shift.type else 0)) * \
                    (1 if ins.mnemonic == 'add' else -1)
                tracked[reg_name(ops[0].reg)] = v
                out.append((ins.address, v, ins.mnemonic))
                continue
        if ins.mnemonic == 'mov' and len(ops) == 2 and ops[1].type == capstone.arm64.ARM64_OP_REG and \
                reg_name(ops[1].reg) in tracked:
            tracked[reg_name(ops[0].reg)] = tracked[reg_name(ops[1].reg)]
            continue
        # a written register stops being tracked
        regs_w = ins.regs_access()[1]
        for r in regs_w:
            tracked.pop(reg_name(r), None)
        if not tracked:
            break
        if ins.mnemonic in ('ret', 'b', 'br'):
            break
    return out


def scan_object(path):
    o = elf32.Obj(path)
    rows = []
    reb = {}            # (shndx, value) -> (name, size)
    for s in o.syms:
        m = REB.match(s.name or '')
        if m and isinstance(s.shndx, int) and s.type in ('STT_OBJECT', 'STT_NOTYPE', 'STT_TLS'):
            reb[(s.shndx, s.value)] = (s.name, max(s.size, 1), s.bind, s.type)
    if not reb:
        o.close()
        return rows, 0, 0
    by_sec = collections.defaultdict(list)
    for (sh, v), (n, sz, b, t) in reb.items():
        by_sec[sh].append((v, v + sz, n))
    reb_names = {n for n, _, _, _ in reb.values()}
    # aliases: other defined symbols at a rebound variable's storage
    for s in o.syms:
        if isinstance(s.shndx, int) and s.shndx in by_sec and s.name and s.name not in reb_names and \
                s.type not in ('STT_SECTION', 'STT_FILE', 'STT_FUNC') and not s.name.startswith('$'):
            for lo, hi, n in by_sec[s.shndx]:
                if lo <= s.value < hi:
                    rows.append(('alias', os.path.basename(path), s.name, s.bind, n, '', ''))

    def hit(sh, off):
        for lo, hi, n in by_sec.get(sh, ()):
            if lo <= off < hi:
                return n, off - lo
        return None
    code_cache = {}
    nrel = nanch = 0
    for r in o.rels:
        s = r.sym
        if s.name in reb_names:
            continue
        if not isinstance(s.shndx, int) or s.shndx not in by_sec:
            continue
        if not (s.type == 'STT_SECTION' or s.bind == 'STB_LOCAL' or s.defined()):
            continue
        nrel += 1
        tgt = s.value + r.addend
        h = hit(s.shndx, tgt)
        where = '%s+%#x' % (o.secname(r.sec), r.offset)
        symtxt = '%s%+d' % (o.secname(s.shndx) if s.type == 'STT_SECTION' else s.name, r.addend)
        if h:
            rows.append(('direct', os.path.basename(path), where, r.type, symtxt, h[0], '+%d' % h[1]))
            continue
        if r.type in ('ADD_ABS_LO12_NC', 'ADR_PREL_LO21') and o.secs[r.sec]['sh_flags'] & 4:
            nanch += 1
            if r.sec not in code_cache:
                code_cache[r.sec] = o.secdata(r.sec)
            code = code_cache[r.sec]
            ins = next(CS.disasm(code[r.offset:r.offset + 4], r.offset), None)
            if ins is None or not ins.operands:
                continue
            dst = reg_name(ins.operands[0].reg)
            for at, t, mn in follow(code, r.offset, dst, tgt):
                h = hit(s.shndx, t)
                if h:
                    rows.append(('anchor', os.path.basename(path), '%s+%#x' % (o.secname(r.sec), at), mn,
                                 symtxt, h[0], '+%d' % h[1]))
    o.close()
    return rows, nrel, nanch


def undname(names):
    p = subprocess.run(['llvm-undname'], input='\n'.join(names) + '\n', capture_output=True, text=True)
    lines, out, i = p.stdout.split('\n'), {}, 0
    for n in names:
        while i < len(lines) and lines[i] != n:
            i += 1
        out[n] = lines[i + 1] if i + 1 < len(lines) else ''
        i += 2
    return out


def cxxfilt(names):
    p = subprocess.run(['c++filt'], input='\n'.join(names) + '\n', capture_output=True, text=True)
    return dict(zip(names, p.stdout.split('\n')))


def var_qname(text):
    """'class Foo * ns::g_x' / 'public: static int A::b' / 'int (*g)[3]' -> 'ns::g_x' (None if not plain)."""
    t = re.sub(r'^(public|protected|private): ', '', text)
    t = re.sub(r'^static ', '', t)
    t = re.sub(r'\[[0-9]*\]', '', t).strip()
    m = re.search(r'([A-Za-z_][\w:<>,` \']*?)\s*$', t)
    if not m or '(' in t:
        return None
    return t.split(' ')[-1].lstrip('*&')


def missed(units_json, fixed_dir, src_map, out_rows):
    units = json.load(open(units_json))['units']
    by_src = {}
    for u in units:
        by_src.setdefault(u['source'], u)
    n_bound = n_def = n_missed = 0
    for fixed_name, orig in sorted(src_map.items()):
        if not fixed_name.startswith('src_'):
            continue
        # unit source from the object name is ambiguous; take the units whose object name maps here
        cands = [u for s, u in by_src.items() if s.replace('/', '_') + '.o' == fixed_name]
        if not cands or not os.path.exists(orig):
            continue
        u = cands[0]
        dnames = {n: int(a, 16) for n, a in u['symbols'].items()
                  if n.startswith('?') and re.search(r'@@[23]', n) and IMG_LO <= int(a, 16) < IMG_HI}
        cnames = {n: int(a, 16) for n, a in u['symbols'].items() if not n.startswith(('?', '_', '@'))
                  and IMG_LO <= int(a, 16) < IMG_HI}
        if not dnames and not cnames:
            continue
        o = elf32.Obj(orig, want_relocs=False)
        defs = [s for s in o.syms if s.name and isinstance(s.shndx, int) and s.type in ('STT_OBJECT', 'STT_TLS')]
        if not defs:
            o.close()
            continue
        dem = undname(sorted(dnames))
        want = collections.defaultdict(set)
        for n, a in dnames.items():
            q = var_qname(dem.get(n, ''))
            if q:
                want[q].add(a)
        for n, a in cnames.items():
            want[n].add(a)
        cx = cxxfilt([s.name for s in defs])
        f = elf32.Obj(os.path.join(fixed_dir, fixed_name), want_relocs=False)
        fixed_reb = {(s.shndx, s.value) for s in f.syms if REB.match(s.name or '') and isinstance(s.shndx, int)}
        fixed_reb_addr = {int(REB.match(s.name).group(1), 16) for s in f.syms
                          if REB.match(s.name or '') and isinstance(s.shndx, int)}
        f.close()
        for s in defs:
            q = cx.get(s.name, s.name)
            q = re.sub(r'\[[0-9]*\]$', '', q)
            if q not in want:
                continue
            n_bound += 1
            addrs = want[q]
            ok = any(a in fixed_reb_addr for a in addrs)
            n_def += 1
            if not ok:
                n_missed += 1
                out_rows.append(('missed', fixed_name, s.name, s.bind, ','.join('%08x' % a for a in sorted(addrs)),
                                 o.secname(s.shndx), '%d' % s.size))
        o.close()
    return n_bound, n_missed


def main():
    fixed, objcopy, units_json, out = sys.argv[1:5]
    src_map = {}
    for fn in os.listdir(objcopy):
        if fn.endswith('.src'):
            src_map[fn[:-4]] = open(os.path.join(objcopy, fn)).read().strip()
    rows, nobj, nrel, nanch, nreb_obj = [], 0, 0, 0, 0
    for fn in sorted(os.listdir(fixed)):
        if not fn.endswith('.o'):
            continue
        r, a, b = scan_object(os.path.join(fixed, fn))
        nobj += 1
        nrel += a
        nanch += b
        if a or r:
            nreb_obj += 1
        rows.extend(r)
    n_bound, n_missed = missed(units_json, fixed, src_map, rows)
    with open(out, 'w') as f:
        f.write('#kind\tobject\twhere/symbol\treloc/bind\tvia\tvariable\toffset\n')
        for r in rows:
            f.write('\t'.join(map(str, r)) + '\n')
    c = collections.Counter(r[0] for r in rows)
    print('objects scanned %d; relocations through a local/other symbol into a section with rebound variables %d '
          '(code address materializations followed: %d)' % (nobj, nrel, nanch))
    print('findings:', dict(c))
    print('variables a unit binds to an image address and its original object defines: %d; not rebound: %d'
          % (n_bound, n_missed))
    return 1 if c.get('direct') or c.get('anchor') or n_missed else 0


if __name__ == '__main__':
    sys.exit(main())
