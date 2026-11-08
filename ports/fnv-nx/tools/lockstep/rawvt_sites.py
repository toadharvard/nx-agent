#!/usr/bin/env python3
"""rawvt_sites.py - S1's raw-slot sites under Wine (T1, rawvt-wine): the receiver's vptr at each site, from the
player's FalloutNV.exe running under Wine (fnvtrace.dll site mode: FNVTRACE_LIST lines `@<addr> <reg> <name>`).

    rawvt_sites.py list   <sites.tsv> <FalloutNV.exe> <out list> <out map.tsv>
    rawvt_sites.py report <sites.tsv> <FalloutNV.exe> <map.tsv> <fnvtrace log> <out.tsv>

sites.tsv is S1's (fnv-nx-build recon/s1_rawvt_sites rawvt_wine_sites.tsv): unit, exe_fn, receiver, slot, exe_call,
slot_load_at, vptr_reg, msvc_symbol. A row with slot_load_at gives one site: that instruction, vptr_reg. A row
without one ('-') gives a site at every indirect call `call [reg + disp]` of exe_fn (the decomp's slot may not be the
exe's), reg being the base register: the function is disassembled (capstone, x86-32) from exe_fn to its last ret
past every branch target. The report has one line per site: the row, the site, its register, the hits, the distinct
vptr values (up to 4) and what each is (the exe vftable's RTTI class, a vftable of no RTTI, or outside the image:
COM / DLL), then the sites never reached, in their own section. Game-derived: it lives on fnv-nx-build only.
"""
import collections, struct, sys

REGS = ('eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi')


class PE:
    def __init__(self, path):
        self.d = open(path, 'rb').read()
        pe = struct.unpack_from('<I', self.d, 0x3c)[0]
        nsec = struct.unpack_from('<H', self.d, pe + 6)[0]
        opt = pe + 24
        self.base = struct.unpack_from('<I', self.d, opt + 28)[0]
        self.size = struct.unpack_from('<I', self.d, opt + 56)[0]
        sh = opt + struct.unpack_from('<H', self.d, pe + 20)[0]
        self.secs = []
        for i in range(nsec):
            vs, va, rs, ro = struct.unpack_from('<IIII', self.d, sh + 40 * i + 8)
            self.secs.append((self.base + va, max(vs, rs), ro, rs))

    def off(self, va):
        for s, n, ro, rs in self.secs:
            if s <= va < s + n:
                return ro + (va - s) if va - s < rs else None
        return None

    def u32(self, va):
        o = self.off(va)
        return struct.unpack_from('<I', self.d, o)[0] if o is not None and o + 4 <= len(self.d) else None

    def cstr(self, va, n=200):
        o = self.off(va)
        if o is None:
            return None
        e = self.d.find(b'\0', o, o + n)
        return self.d[o:e if e >= 0 else o + n].decode('latin-1')

    def inside(self, va):
        return self.base <= va < self.base + self.size

    def vftable_class(self, vt):
        """The RTTI class of the vftable at vt (its [-1] is the complete object locator), or None."""
        col = self.u32(vt - 4)
        if col is None or not self.inside(col):
            return None
        td = self.u32(col + 12)
        if td is None or not self.inside(td):
            return None
        name = self.cstr(td + 8)
        return name if name and name.startswith('.?A') else None


def rows(path):
    out = []
    for line in open(path, errors='replace'):
        if line.startswith('#') or not line.strip():
            continue
        p = line.rstrip('\n').split('\t')
        p += ['-'] * (8 - len(p))
        out.append(dict(zip(('unit', 'exe_fn', 'receiver', 'slot', 'exe_call', 'slot_load_at', 'vptr_reg', 'msvc_symbol'), p)))
    return out


def indirect_calls(pe, fn):
    """[(address, base register)] of the `call [reg + disp]` of the function at fn."""
    import capstone
    from capstone import x86
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    o = pe.off(fn)
    if o is None:
        return []
    code = pe.d[o:o + 0x4000]
    far, out, recent = fn, [], []
    for ins in md.disasm(code, fn):
        if ins.group(x86.X86_GRP_JUMP) and ins.operands and ins.operands[0].type == x86.X86_OP_IMM:
            far = max(far, ins.operands[0].imm)
        if ins.mnemonic == 'call' and ins.operands and ins.operands[0].type == x86.X86_OP_MEM:
            m = ins.operands[0].mem
            if m.base and not m.index:
                out.append((ins.address, ins.reg_name(m.base)))
        elif ins.mnemonic == 'call' and ins.operands and ins.operands[0].type == x86.X86_OP_REG:
            # `mov edx, [eax + off]; ...; call edx`: the slot load is the latest mov into the called register, and its
            # base register holds the vptr there
            cr = ins.operands[0].reg
            for prev in reversed(recent):
                if prev.mnemonic == 'mov' and len(prev.operands) == 2 and prev.operands[0].type == x86.X86_OP_REG \
                        and prev.operands[0].reg == cr:
                    m = prev.operands[1].mem if prev.operands[1].type == x86.X86_OP_MEM else None
                    if m is not None and m.base and not m.index:
                        out.append((prev.address, prev.reg_name(m.base)))
                    break
        recent = (recent + [ins])[-8:]
        if ins.mnemonic in ('ret', 'retn') and ins.address >= far:
            break
    return out


def cmd_list(sites, exe, out_list, out_map):
    pe = PE(exe)
    seen, nosite = {}, []
    for i, r in enumerate(rows(sites)):
        if r['exe_fn'] in ('-', '') and r['slot_load_at'] in ('-', ''):
            nosite.append(i)
            continue
        if r['slot_load_at'] not in ('-', '') and r['vptr_reg'] in REGS:
            cand = [(int(r['slot_load_at'], 16), r['vptr_reg'], 'slot load')]
        else:
            cand = [(a, reg, 'slot load of an indirect call in exe_fn') for a, reg in indirect_calls(pe, int(r['exe_fn'], 16))
                    if reg in REGS]
        for a, reg, why in cand:
            seen.setdefault((a, reg), []).append((i, why))
    with open(out_list, 'w') as f, open(out_map, 'w') as m:
        m.write('# site\treg\trows\tkind\n')
        for k, ((a, reg), rs) in enumerate(sorted(seen.items())):
            f.write('@%08x %s s%d\n' % (a, reg, k))
            m.write('%08x\t%s\t%s\t%s\n' % (a, reg, ','.join(str(i) for i, _ in rs), rs[0][1]))
        for i in nosite:
            m.write('-\t-\t%d\tno exe function and no slot load in the row\n' % i)
    print('%d sites for %d rows (%d rows with no exe function) -> %s' % (len(seen), len(rows(sites)), len(nosite), out_list))


def cmd_report(sites, exe, mapf, log, out):
    pe = PE(exe)
    rs = rows(sites)
    smap = [l.rstrip('\n').split('\t') for l in open(mapf) if not l.startswith('#') and l.strip()]
    vals, hits = collections.defaultdict(collections.Counter), collections.Counter()
    for line in open(log, errors='replace'):
        p = line.rstrip('\n').split('\t')
        if p[0] == 'site' and len(p) >= 7:
            a = int(p[3], 16)
            hits[a] = max(hits[a], 1) if a not in hits else hits[a] + 1
            vals[a][int(p[6], 16)] += 1
        elif p[0] == 'sitecount' and len(p) >= 4:
            hits[int(p[1], 16)] = max(hits[int(p[1], 16)], int(p[3]))

    def what(v):
        if not pe.inside(v):
            return 'outside the image (COM/DLL)'
        c = pe.vftable_class(v)
        return c or 'in the image, no RTTI'
    lines, unreached, nosite = [], [], []
    for a_, reg, idx, kind in smap:
        if a_ == '-':
            r = rs[int(idx)]
            nosite.append([r['unit'], r['exe_fn'], r['slot'], r['exe_call'], '-', '-', kind, r['msvc_symbol']])
            continue
        a = int(a_, 16)
        for i in idx.split(','):
            r = rs[int(i)]
            base = [r['unit'], r['exe_fn'], r['slot'], r['exe_call'], a_, reg, kind, r['msvc_symbol']]
            if hits[a]:
                vv = ['%#x=%s' % (v, what(v)) for v, _ in vals[a].most_common(4)]
                lines.append(base + [str(hits[a]), ' '.join(vv)])
            else:
                unreached.append(base)
    hdr = ['unit', 'exe_fn', 'slot', 'exe_call', 'site', 'reg', 'kind', 'msvc_symbol']
    with open(out, 'w') as f:
        f.write('# rawvt-wine: the receiver vptr at S1\'s raw-slot sites, FalloutNV.exe under Wine (fnvtrace site mode)\n')
        f.write('#' + '\t'.join(hdr + ['hits', 'vptr=class ...']) + '\n')
        for l in lines:
            f.write('\t'.join(l) + '\n')
        f.write('# never reached by the original (no hit in the run)\n#' + '\t'.join(hdr) + '\n')
        for l in unreached:
            f.write('#\t' + '\t'.join(l) + '\n')
        f.write('# no site to trace (the row names no exe function)\n')
        for l in nosite:
            f.write('#\t' + '\t'.join(l) + '\n')
    print('%d sites hit, %d never reached, %d rows with no site -> %s' % (len(lines), len(unreached), len(nosite), out))


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] == ['list'] and len(a) == 5:
        cmd_list(*a[1:])
    elif a[:1] == ['report'] and len(a) == 6:
        cmd_report(*a[1:])
    else:
        sys.exit(__doc__)
