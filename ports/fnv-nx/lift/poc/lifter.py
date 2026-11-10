#!/usr/bin/env python3
"""Minimal AOT lifter PoC: x86-32 function bytes of the exe -> C (one C function per exe function).
Recursive-descent decode inside [addr, addr+size); jump tables and address-taken labels through .reloc."""
import struct, bisect, capstone
from capstone import x86 as X
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); md.detail = True

class Unsupported(Exception):
    pass

R32 = ['eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi']
SUB = {}
for i, r in enumerate(R32):
    SUB[r] = (r, 0, 32); SUB[r[1:]] = (r, 0, 16)
for r in 'abcd':
    SUB[r + 'l'] = ('e' + r + 'x', 0, 8); SUB[r + 'h'] = ('e' + r + 'x', 8, 8)
for r in ('si', 'di', 'bp', 'sp'):
    SUB[r + 'l'] = ('e' + r, 0, 8)
MASK = {8: '0xffu', 16: '0xffffu', 32: '0xffffffffu'}
CC = {'o': 'of', 'no': '!of', 'b': 'cf', 'ae': '!cf', 'e': 'zf', 'ne': '!zf', 'be': '(cf|zf)', 'a': '!(cf|zf)',
      's': 'sf', 'ns': '!sf', 'p': 'pf', 'np': '!pf', 'l': '(sf!=of)', 'ge': '(sf==of)', 'le': '(zf||sf!=of)',
      'g': '(!zf&&sf==of)'}

class Image:
    def __init__(self, path):
        d = self.d = open(path, 'rb').read()
        pe = struct.unpack_from('<I', d, 0x3c)[0]; n = struct.unpack_from('<H', d, pe + 6)[0]
        opt = struct.unpack_from('<H', d, pe + 20)[0]; self.base = struct.unpack_from('<I', d, pe + 52)[0]
        self.secs = []; o = pe + 24 + opt
        for i in range(n):
            name = d[o:o + 8].rstrip(b'\0').decode(); vs, va, rs, ro = struct.unpack_from('<IIII', d, o + 8)
            self.secs.append((name, self.base + va, vs, ro, rs)); o += 40
        rr, rsz = struct.unpack_from('<II', d, pe + 24 + 96 + 40)
        self.relocs = []; p = self.off(self.base + rr); end = p + rsz
        while p < end:
            page, bs = struct.unpack_from('<II', d, p)
            if not bs: break
            for i in range((bs - 8) // 2):
                e = struct.unpack_from('<H', d, p + 8 + 2 * i)[0]
                if e >> 12 == 3: self.relocs.append(self.base + page + (e & 0xfff))
            p += bs
        self.relocs.sort(); self.rset = set(self.relocs)
        # every reloc'd dword value -> sorted list (address-taken code candidates)
        self.taken = sorted(self.u32(r) for r in self.relocs)
    def off(self, a):
        for n, va, vs, ro, rs in self.secs:
            if va <= a < va + vs and a - va < rs: return ro + a - va
    def u32(self, a):
        o = self.off(a); return struct.unpack_from('<I', self.d, o)[0] if o is not None else 0
    def bytes(self, a, n):
        o = self.off(a); return self.d[o:o + n]

def lift(img, addr, size, popn=None):
    """-> (C text, meta). Raises Unsupported(reason)."""
    lo, hi = addr, addr + size
    code = img.bytes(addr, size)
    insns = {}; work = [addr]; tables = {}
    i0 = bisect.bisect_left(img.taken, lo); i1 = bisect.bisect_left(img.taken, hi)
    taken = sorted(set(img.taken[i0:i1]))
    def decode(a):
        g = md.disasm(code[a - lo:a - lo + 16], a, 1)
        for ins in g: return ins
        raise Unsupported('undecodable bytes')
    has_ijmp_reg = False
    while work:
        a = work.pop()
        while lo <= a < hi and a not in insns:
            ins = decode(a); insns[a] = ins; m = ins.mnemonic; nxt = a + ins.size
            ops = ins.operands
            if m == 'jmp':
                if ops[0].type == X.X86_OP_IMM: work.append(ops[0].imm & 0xffffffff)
                elif ops[0].type == X.X86_OP_MEM and ops[0].mem.scale == 4 and ops[0].mem.index and (a + ins.size - 4) in img.rset:
                    t = ops[0].mem.disp & 0xffffffff; ent = []
                    while t in img.rset and lo <= img.u32(t) < hi:
                        ent.append(img.u32(t)); t += 4
                        if lo <= t < hi and t in insns: break
                    tables[a] = ent; work.extend(ent)
                else:
                    has_ijmp_reg = True; work.extend(taken)
                break
            if m in ('ret', 'retf', 'int3', 'hlt', 'ud2'): break
            if m.startswith('j') or m in ('loop', 'loope', 'loopne'):
                work.append(ops[0].imm & 0xffffffff)
            a = nxt
    order = sorted(insns)
    L = []; em = L.append
    callrets = []; usesfs = False; kinds = set(); selfcalls = {}
    def lab(a): return 'L_%08x' % a
    def reg(name):
        if name not in SUB: raise Unsupported('register ' + name)
        return SUB[name]
    def ea(ins, op):
        nonlocal usesfs
        m = op.mem; parts = []
        if m.segment:
            if m.segment == X.X86_REG_FS: parts.append('fsb'); usesfs = True
            elif m.segment not in (X.X86_REG_DS, X.X86_REG_SS, X.X86_REG_ES, X.X86_REG_CS): raise Unsupported('segment')
        if m.base: parts.append(reg(ins.reg_name(m.base))[0])
        if m.index: parts.append('%s*%du' % (reg(ins.reg_name(m.index))[0], m.scale))
        if m.disp or not parts: parts.append('0x%xu' % (m.disp & 0xffffffff))
        return '(u32)(' + '+'.join(parts) + ')'
    def rd(ins, op, size=None):
        s = (size or op.size) * 8
        if op.type == X.X86_OP_IMM: return '0x%xu' % (op.imm & ((1 << s) - 1))
        if op.type == X.X86_OP_REG:
            b, sh, bits = reg(ins.reg_name(op.reg))
            return b if bits == 32 else ('((%s>>%d)&%s)' % (b, sh, MASK[bits]))
        return 'M%d(%s)' % (s, ea(ins, op))
    def wr(ins, op, v):
        if op.type == X.X86_OP_REG:
            b, sh, bits = reg(ins.reg_name(op.reg))
            if bits == 32: return '%s=(%s);' % (b, v)
            mk = (1 << bits) - 1
            return '%s=(%s&~0x%xu)|(((u32)(%s)&0x%xu)<<%d);' % (b, b, mk << sh, v, mk, sh)
        return 'M%d(%s)=(%s);' % (op.size * 8, ea(ins, op), v)
    def zsp(s): return 'zf=(r&%s)==0; sf=(r>>%d)&1; pf=PAR(r);' % (MASK[s], s - 1)
    def xr(ins, op):
        n = ins.reg_name(op.reg)
        if not n.startswith('xmm'): raise Unsupported('register ' + n)
        return 'x' + n[3:]
    def fmem(ins, op): return ('MF32' if op.size == 4 else 'MF64') + '(' + ea(ins, op) + ')'
    for idx, a in enumerate(order):
        ins = insns[a]; m = ins.mnemonic; ops = ins.operands; nxt = a + ins.size
        em('%s: ;' % lab(a))
        s = ops[0].size * 8 if ops else 32
        term = False
        pfx = ''
        if m.startswith('rep ') or m.startswith('repe ') or m.startswith('repne '): pfx, m = m.split(' ', 1)
        if m.startswith('lock '): m = m.split(' ', 1)[1]; kinds.add('lock')
        b0 = ins.opcode[0]; modrm = ins.modrm
        if m in ('mov',):
            em(wr(ins, ops[0], rd(ins, ops[1], ops[0].size)))
        elif m == 'lea': em(wr(ins, ops[0], ea(ins, ops[1])))
        elif m in ('movzx',): em(wr(ins, ops[0], rd(ins, ops[1])))
        elif m == 'movsx':
            em(wr(ins, ops[0], '(u32)(s32)(s%d)%s' % (ops[1].size * 8, rd(ins, ops[1]))))
        elif m in ('add', 'adc', 'sub', 'sbb', 'cmp'):
            em('{ u32 a=%s, b=%s, r; u64 t;' % (rd(ins, ops[0]), rd(ins, ops[1], ops[0].size)))
            M = MASK[s]
            if m == 'add': em('r=(a+b)&%s; cf=r<a; of=(((a^r)&(b^r))>>%d)&1;' % (M, s - 1))
            elif m == 'adc': em('t=(u64)a+b+cf; r=(u32)t&%s; of=(((a^r)&(b^r))>>%d)&1; cf=(t>>%d)&1;' % (M, s - 1, s))
            elif m in ('sub', 'cmp'): em('r=(a-b)&%s; cf=a<b; of=(((a^b)&(a^r))>>%d)&1;' % (M, s - 1))
            else: em('t=(u64)a-(u64)b-cf; r=(u32)t&%s; of=(((a^b)&(a^r))>>%d)&1; cf=(t>>%d)&1;' % (M, s - 1, s))
            em('af=((a^b^r)>>4)&1; ' + zsp(s))
            if m != 'cmp': em(wr(ins, ops[0], 'r'))
            em('}')
        elif m in ('and', 'or', 'xor', 'test'):
            o = {'and': '&', 'test': '&', 'or': '|', 'xor': '^'}[m]
            em('{ u32 r=(%s)%s(%s); cf=of=af=0; %s' % (rd(ins, ops[0]), o, rd(ins, ops[1], ops[0].size), zsp(s)))
            if m != 'test': em(wr(ins, ops[0], 'r'))
            em('}')
        elif m in ('inc', 'dec'):
            o = '+' if m == 'inc' else '-'
            em('{ u32 a=%s, r=(a%s1)&%s; of=%s; af=((a^1^r)>>4)&1; %s %s }' % (
                rd(ins, ops[0]), o, MASK[s],
                ('r==0x%xu' % (1 << (s - 1))) if m == 'inc' else ('a==0x%xu' % (1 << (s - 1))), zsp(s), wr(ins, ops[0], 'r')))
        elif m == 'neg':
            em('{ u32 a=%s, r=(0-a)&%s; cf=a!=0; of=a==0x%xu; af=((a^r)>>4)&1; %s %s }' % (rd(ins, ops[0]), MASK[s], 1 << (s - 1), zsp(s), wr(ins, ops[0], 'r')))
        elif m == 'not': em(wr(ins, ops[0], '~' + rd(ins, ops[0])))
        elif m in ('shl', 'sal', 'shr', 'sar'):
            cnt = rd(ins, ops[1]) if len(ops) > 1 else '1'
            em('{ u32 a=%s, n=(%s)&31, r, p; if(n){' % (rd(ins, ops[0]), cnt))
            if m in ('shl', 'sal'): em('p=a<<(n-1); r=(p<<1)&%s; cf=(p>>%d)&1; of=((p^r)>>%d)&1;' % (MASK[s], s - 1, s - 1))
            elif m == 'shr': em('p=a>>(n-1); r=p>>1; cf=p&1; of=((p^r)>>%d)&1;' % (s - 1))
            else: em('s32 sa=(s%d)a; p=(u32)(sa>>(n-1)); r=(u32)(sa>>n)&%s; cf=p&1; of=0;' % (s, MASK[s]))
            em('af=0; %s %s }}' % (zsp(s), wr(ins, ops[0], 'r')))
        elif m in ('rol', 'ror'):
            cnt = rd(ins, ops[1]) if len(ops) > 1 else '1'
            em('{ u32 a=%s, n=(%s)&31, k=n%%%d, r=a; if(n){ if(k) r=' % (rd(ins, ops[0]), cnt, s))
            if m == 'rol': em('((a<<k)|(a>>(%d-k)))&%s; cf=r&1; of=((r>>%d)&1)^cf;' % (s, MASK[s], s - 1))
            else: em('((a>>k)|(a<<(%d-k)))&%s; cf=(r>>%d)&1; of=((r>>%d)^(r>>%d))&1;' % (s, MASK[s], s - 1, s - 1, s - 2))
            em(wr(ins, ops[0], 'r') + ' }}')
        elif m in ('shld', 'shrd'):
            if s != 32: raise Unsupported(m + '16')
            em('{ u32 a=%s, b=%s, n=(%s)&31, r; if(n){' % (rd(ins, ops[0]), rd(ins, ops[1]), rd(ins, ops[2])))
            if m == 'shld': em('r=(a<<n)|(b>>(32-n)); cf=(a>>(32-n))&1; of=((a^r)>>31)&1;')
            else: em('r=(a>>n)|(b<<(32-n)); cf=(a>>(n-1))&1; of=((a^r)>>31)&1;')
            em('af=0; %s %s }}' % (zsp(32), wr(ins, ops[0], 'r')))
        elif m == 'imul':
            if len(ops) == 1:
                if s == 32: em('{ s64 t=(s64)(s32)eax*(s64)(s32)%s; eax=(u32)t; edx=(u32)(t>>32); cf=of=(t!=(s64)(s32)eax); u32 r=eax; af=0; %s }' % (rd(ins, ops[0]), zsp(32)))
                else: raise Unsupported('imul r/m%d' % s)
            else:
                a_ = rd(ins, ops[1] if len(ops) == 3 else ops[0], ops[0].size); b_ = rd(ins, ops[2] if len(ops) == 3 else ops[1], ops[0].size)
                em('{ s64 t=(s64)(s%d)%s*(s64)(s%d)%s; u32 r=(u32)t&%s; cf=of=(t!=(s64)(s%d)r); af=0; %s %s }' % (s, a_, s, b_, MASK[s], s, zsp(s), wr(ins, ops[0], 'r')))
        elif m == 'mul':
            if s != 32: raise Unsupported('mul r/m%d' % s)
            em('{ u64 t=(u64)eax*(u64)%s; eax=(u32)t; edx=(u32)(t>>32); cf=of=edx!=0; u32 r=eax; af=0; %s }' % (rd(ins, ops[0]), zsp(32)))
        elif m in ('div', 'idiv'):
            if s != 32: raise Unsupported(m + ' r/m%d' % s)
            if m == 'div': em('{ u32 d=%s; u64 n=((u64)edx<<32)|eax; if(d==0||n/d>0xffffffffull){SYNC_OUT; fnvl_trap(c,0x%xu,1); return;} eax=(u32)(n/d); edx=(u32)(n%%d); }' % (rd(ins, ops[0]), a))
            else: em('{ s32 d=(s32)%s; s64 n=(s64)(((u64)edx<<32)|eax); if(d==0||(n==(s64)0x8000000000000000ull&&d==-1)||n/d>0x7fffffffll||n/d<-0x80000000ll){SYNC_OUT; fnvl_trap(c,0x%xu,1); return;} eax=(u32)(n/d); edx=(u32)(n%%d); }' % (rd(ins, ops[0]), a))
        elif m == 'cdq': em('edx=(u32)((s32)eax>>31);')
        elif m == 'cwde': em('eax=(u32)(s32)(s16)eax;')
        elif m == 'cbw': em('eax=(eax&0xffff0000u)|(u16)(s16)(s8)eax;')
        elif m == 'xchg':
            em('{ u32 t0=%s, t1=%s; %s %s }' % (rd(ins, ops[0]), rd(ins, ops[1]), wr(ins, ops[0], 't1'), wr(ins, ops[1], 't0')))
        elif m in ('nop', 'wait', 'fnclex', 'fwait'): pass
        elif m == 'push':
            if s != 32: raise Unsupported('push16')
            em('{ u32 v=%s; esp-=4; M32(esp)=v; }' % rd(ins, ops[0]))
        elif m == 'pop':
            if s != 32: raise Unsupported('pop16')
            em('{ u32 v=M32(esp); esp+=4; %s }' % wr(ins, ops[0], 'v'))
        elif m == 'leave': em('esp=ebp; ebp=M32(esp); esp+=4;')
        elif m.startswith('set') and m[3:] in CC: em(wr(ins, ops[0], '(u32)(%s?1:0)' % CC[m[3:]]))
        elif m.startswith('cmov') and m[4:] in CC: em('if(%s){ %s }' % (CC[m[4:]], wr(ins, ops[0], rd(ins, ops[1]))))
        elif m == 'bt' and ops[0].type == X.X86_OP_REG or (m == 'bt' and ops[1].type == X.X86_OP_IMM):
            em('cf=((%s)>>((%s)&%d))&1;' % (rd(ins, ops[0]), rd(ins, ops[1], ops[0].size), s - 1))
        elif m in ('bts', 'btr', 'btc') and (ops[0].type == X.X86_OP_REG or ops[1].type == X.X86_OP_IMM):
            o = {'bts': 'a|b', 'btr': 'a&~b', 'btc': 'a^b'}[m]
            em('{ u32 a=%s, b=1u<<((%s)&%d); cf=(a&b)!=0; %s }' % (rd(ins, ops[0]), rd(ins, ops[1], ops[0].size), s - 1, wr(ins, ops[0], o)))
        elif m == 'bswap': em('%s=__builtin_bswap32(%s);' % (reg(ins.reg_name(ops[0].reg))[0], reg(ins.reg_name(ops[0].reg))[0]))
        elif m in ('cld', 'std'): em('df=%d;' % (m == 'std'))
        elif m == 'sahf': em('{ u32 h=eax>>8; cf=h&1; pf=(h>>2)&1; af=(h>>4)&1; zf=(h>>6)&1; sf=(h>>7)&1; }')
        elif m in ('movsb', 'movsw', 'movsd', 'stosb', 'stosw', 'stosd', 'scasb', 'cmpsb', 'lodsb', 'lodsd') and (not ops or ops[0].type == X.X86_OP_MEM):
            w = {'b': 8, 'w': 16, 'd': 32}[m[-1]]; st = 'u32 st=df?-%du:%du;' % (w // 8, w // 8); k = m[:-1]
            body = {'movs': 'M%d(edi)=M%d(esi); esi+=st; edi+=st;' % (w, w), 'stos': 'M%d(edi)=eax&%s; edi+=st;' % (w, MASK[w]),
                    'lods': 'eax=(eax&~%s)|M%d(esi); esi+=st;' % (MASK[w], w),
                    'scas': '{ u32 a=eax&%s, b=M%d(edi), r=(a-b)&%s; cf=a<b; of=(((a^b)&(a^r))>>%d)&1; af=((a^b^r)>>4)&1; %s } edi+=st;' % (MASK[w], w, MASK[w], w - 1, zsp(w)),
                    'cmps': '{ u32 a=M%d(esi), b=M%d(edi), r=(a-b)&%s; cf=a<b; of=(((a^b)&(a^r))>>%d)&1; af=((a^b^r)>>4)&1; %s } esi+=st; edi+=st;' % (w, w, MASK[w], w - 1, zsp(w))}[k]
            if not pfx: em('{ %s %s }' % (st, body))
            elif k in ('movs', 'stos', 'lods'): em('{ %s while(ecx){ %s ecx--; } }' % (st, body))
            else: em('{ %s while(ecx){ %s ecx--; if(%s) break; } }' % (st, body, '!zf' if pfx in ('rep', 'repe') else 'zf'))
        elif m == 'call':
            op = ops[0]; t = ('0x%xu' % (op.imm & 0xffffffff)) if op.type == X.X86_OP_IMM else rd(ins, op)
            if op.type == X.X86_OP_IMM and lo <= (op.imm & 0xffffffff) < hi: selfcalls[a] = (op.imm & 0xffffffff, nxt); kinds.add('selfcall')
            callrets.append(nxt)
            em('{ u32 t=%s; esp-=4; M32(esp)=0x%xu; SYNC_OUT; fnvl_call(c,t); SYNC_IN; }' % (t, nxt))
        elif m == 'jmp':
            op = ops[0]; term = True
            if op.type == X.X86_OP_IMM:
                t = op.imm & 0xffffffff
                if lo <= t < hi: em('goto %s;' % lab(t))
                else: em('SYNC_OUT; fnvl_tail(c,0x%xu); return;' % t); kinds.add('tailjmp')
            else:
                cands = tables.get(a)
                if cands is None: cands = [x for x in taken if x in insns]; kinds.add('jmp-indirect-untabled')
                else: kinds.add('jumptable')
                em('{ u32 t=%s; switch(t){' % rd(ins, op))
                for x in sorted(set(cands)): em('case 0x%xu: goto %s;' % (x, lab(x)))
                em('default: SYNC_OUT; fnvl_tail(c,t); return; } }')
        elif m[0] == 'j' and m[1:] in CC:
            t = ops[0].imm & 0xffffffff
            em('if(%s) goto %s;' % (CC[m[1:]], lab(t)) if lo <= t < hi else 'if(%s){ SYNC_OUT; fnvl_tail(c,0x%xu); return; }' % (CC[m[1:]], t))
        elif m == 'jecxz': em('if(!ecx) goto %s;' % lab(ops[0].imm & 0xffffffff))
        elif m == 'ret':
            em('esp+=%du; SYNC_OUT; return;' % (4 + (ops[0].imm if ops else 0))); term = True
        elif m == 'int3': em('SYNC_OUT; fnvl_trap(c,0x%xu,3); return;' % a); term = True
        # ---------------- x87 ----------------
        elif 0xd8 <= b0 <= 0xdf:
            kinds.add('x87')
            if modrm >= 0xc0:
                i = modrm & 7; g = (modrm >> 3) & 7; key = (b0, modrm & 0xf8)
                AR = {0: '+', 1: '*', 4: '-', 5: '-r', 6: '/', 7: '/r'}
                def ar(dst, x, y, o):  # dst = x o y
                    if o.endswith('r'): x, y = y, x; o = o[0]
                    return '{ double x_=(%s), y_=(%s); %s=FXN(x_%sy_,x_,y_); }' % (x, y, dst, o)
                if b0 == 0xd8 and g in AR: em(ar('ST(0)', 'ST(0)', 'ST(%d)' % i, AR[g]))
                elif b0 in (0xdc, 0xde) and g in AR:
                    o = {0: '+', 1: '*', 4: '-r', 5: '-', 6: '/r', 7: '/'}[g]
                    em(ar('ST(%d)' % i, 'ST(%d)' % i, 'ST(0)', o) + (' FPOP();' if b0 == 0xde else ''))
                elif b0 == 0xd8 and g in (2, 3): em('FCOM(ST(0),ST(%d));%s' % (i, ' FPOP();' if g == 3 else ''))
                elif b0 == 0xde and modrm == 0xd9: em('FCOM(ST(0),ST(1)); FPOP(); FPOP();')
                elif b0 == 0xdd and g in (4, 5): em('FCOM(ST(0),ST(%d));%s' % (i, ' FPOP();' if g == 5 else ''))
                elif b0 == 0xda and modrm == 0xe9: em('FCOM(ST(0),ST(1)); FPOP(); FPOP();')
                elif b0 == 0xdb and g in (5, 6) or b0 == 0xdf and g in (5, 6):
                    em('{ double x=ST(0), y=ST(%d); of=sf=af=0; if(x!=x||y!=y){zf=pf=cf=1;} else {zf=x==y; pf=0; cf=x<y;} }%s' % (i, ' FPOP();' if b0 == 0xdf else ''))
                elif b0 == 0xd9 and g == 0: em('{ double v=ST(%d); FPUSH(v); }' % i)
                elif b0 == 0xd9 and g == 1: em('{ double v=ST(0); ST(0)=ST(%d); ST(%d)=v; }' % (i, i))
                elif b0 == 0xdd and g == 2: em('ST(%d)=ST(0);' % i)
                elif b0 == 0xdd and g == 3: em('ST(%d)=ST(0); FPOP();' % i)
                elif b0 == 0xdd and g == 0: em('ftag&=~(1u<<((ftop+%d)&7));' % i)
                elif b0 == 0xd9 and modrm == 0xe0: em('ST(0)=-ST(0);')
                elif b0 == 0xd9 and modrm == 0xe1: em('ST(0)=__builtin_fabs(ST(0));')
                elif b0 == 0xd9 and modrm == 0xe4: em('FCOM(ST(0),0.0);')
                elif b0 == 0xd9 and modrm == 0xe8: em('FPUSH(1.0);')
                elif b0 == 0xd9 and modrm == 0xee: em('FPUSH(0.0);')
                elif b0 == 0xd9 and modrm == 0xfa: em('{ double x_=ST(0); ST(0)=FXN(__builtin_sqrt(x_),x_,x_); }')
                elif b0 == 0xd9 and modrm == 0xfc: em('ST(0)=fx_round(ST(0),(fcw>>10)&3);')
                elif b0 == 0xdf and modrm == 0xe0: em('eax=(eax&0xffff0000u)|FSW;')
                elif b0 == 0xdb and modrm in (0xe2, 0xe3): pass
                else: raise Unsupported('x87 ' + m)
            else:
                op = ops[0]; g = (modrm >> 3) & 7; A = ea(ins, op)
                AR = {0: '+', 1: '*', 4: '-', 5: '-r', 6: '/', 7: '/r'}
                def src():
                    if b0 == 0xd8: return '(double)MF32(%s)' % A
                    if b0 == 0xdc: return 'MF64(%s)' % A
                    if b0 == 0xda: return '(double)(s32)M32(%s)' % A
                    return '(double)(s16)M16(%s)' % A
                if b0 in (0xd8, 0xdc, 0xda, 0xde) and g in AR:
                    o = AR[g]; x, y = ('ST(0)', src())
                    if o.endswith('r'): x, y = y, x; o = o[0]
                    em('{ double x_=(%s), y_=(%s); ST(0)=FXN(x_%sy_,x_,y_); }' % (x, y, o))
                elif b0 in (0xd8, 0xdc, 0xda, 0xde) and g in (2, 3): em('FCOM(ST(0),%s);%s' % (src(), ' FPOP();' if g == 3 else ''))
                elif b0 == 0xd9 and g == 0: em('FPUSH((double)MF32(%s));' % A)
                elif b0 == 0xdd and g == 0: em('FPUSH(MF64(%s));' % A)
                elif b0 == 0xd9 and g in (2, 3): em('MF32(%s)=(float)ST(0);%s' % (A, ' FPOP();' if g == 3 else ''))
                elif b0 == 0xdd and g in (2, 3): em('MF64(%s)=ST(0);%s' % (A, ' FPOP();' if g == 3 else ''))
                elif b0 == 0xdb and g == 0: em('FPUSH((double)(s32)M32(%s));' % A)
                elif b0 == 0xdf and g == 0: em('FPUSH((double)(s16)M16(%s));' % A)
                elif b0 == 0xdf and g == 5: em('FPUSH((double)(s64)M64(%s));' % A)
                elif b0 == 0xdb and g in (2, 3): em('M32(%s)=fx_i32(ST(0),(fcw>>10)&3);%s' % (A, ' FPOP();' if g == 3 else ''))
                elif b0 == 0xdf and g in (2, 3): em('M16(%s)=fx_i16(ST(0),(fcw>>10)&3);%s' % (A, ' FPOP();' if g == 3 else ''))
                elif b0 == 0xdf and g == 7: em('M64(%s)=fx_i64(ST(0),(fcw>>10)&3); FPOP();' % A)
                elif b0 == 0xdb and g == 1: em('M32(%s)=fx_i32(ST(0),3); FPOP();' % A)
                elif b0 == 0xdd and g == 1: em('M64(%s)=fx_i64(ST(0),3); FPOP();' % A)
                elif b0 == 0xd9 and g == 5: em('fcw=M16(%s);' % A)
                elif b0 == 0xd9 and g == 7: em('M16(%s)=fcw;' % A)
                elif b0 == 0xdd and g == 7: em('M16(%s)=FSW;' % A)
                else: raise Unsupported('x87 ' + m)
        # ---------------- SSE subset ----------------
        elif ops and any(o.type == X.X86_OP_REG and ins.reg_name(o.reg).startswith('xmm') for o in ops):
            kinds.add('sse')
            def xs(op, fld, n):   # scalar/packed source as expression list
                if op.type == X.X86_OP_REG: return ['%s.%s[%d]' % (xr(ins, op), fld, k) for k in range(n)]
                ty = {'u': 'M32', 'f': 'MF32', 'd': 'MF64', 'q': 'M64'}[fld]; st = 8 if fld in 'dq' else 4
                return ['%s(%s+%du)' % (ty, ea(ins, op), k * st) for k in range(n)]
            d0 = ops[0]; s1 = ops[1] if len(ops) > 1 else None
            SS = {'addss': '+', 'subss': '-', 'mulss': '*', 'divss': '/'}; SD = {'addsd': '+', 'subsd': '-', 'mulsd': '*', 'divsd': '/'}
            PS = {'addps': '+', 'subps': '-', 'mulps': '*', 'divps': '/'}; LG = {'xorps': '^', 'andps': '&', 'orps': '|', 'pxor': '^', 'pand': '&', 'por': '|', 'xorpd': '^', 'andpd': '&'}
            if m in ('movss',):
                if d0.type == X.X86_OP_REG and s1.type == X.X86_OP_MEM: em('{ u32 v=M32(%s); %s.u[0]=v; %s.u[1]=%s.u[2]=%s.u[3]=0; }' % ((ea(ins, s1),) + (xr(ins, d0),) * 4))
                elif d0.type == X.X86_OP_MEM: em('M32(%s)=%s.u[0];' % (ea(ins, d0), xr(ins, s1)))
                else: em('%s.u[0]=%s.u[0];' % (xr(ins, d0), xr(ins, s1)))
            elif m in ('movsd',) and ops:
                if d0.type == X.X86_OP_REG and s1.type == X.X86_OP_MEM: em('{ u64 v=M64(%s); %s.q[0]=v; %s.q[1]=0; }' % (ea(ins, s1), xr(ins, d0), xr(ins, d0)))
                elif d0.type == X.X86_OP_MEM: em('M64(%s)=%s.q[0];' % (ea(ins, d0), xr(ins, s1)))
                else: em('%s.q[0]=%s.q[0];' % (xr(ins, d0), xr(ins, s1)))
            elif m in ('movaps', 'movups', 'movdqa', 'movdqu', 'movapd', 'movupd'):
                sv = xs(s1, 'u', 4)
                if d0.type == X.X86_OP_REG: em('{ u32 a0=%s,a1=%s,a2=%s,a3=%s; X.u[0]=a0;X.u[1]=a1;X.u[2]=a2;X.u[3]=a3; }'.replace('X', xr(ins, d0)) % tuple(sv))
                else: em(' '.join('M32(%s+%du)=%s;' % (ea(ins, d0), 4 * k, sv[k]) for k in range(4)))
            elif m == 'movd':
                if d0.type == X.X86_OP_REG and ins.reg_name(d0.reg).startswith('xmm'): em('{ u32 v=%s; X.u[0]=v; X.u[1]=X.u[2]=X.u[3]=0; }'.replace('X', xr(ins, d0)) % rd(ins, s1))
                else: em(wr(ins, d0, xr(ins, s1) + '.u[0]'))
            elif m == 'movq':
                if d0.type == X.X86_OP_REG: em('{ u64 v=%s; X.q[0]=v; X.q[1]=0; }'.replace('X', xr(ins, d0)) % xs(s1, 'q', 1)[0])
                else: em('M64(%s)=%s.q[0];' % (ea(ins, d0), xr(ins, s1)))
            elif m in SS: em('{ float x_=%s.f[0], y_=%s; %s.f[0]=fxnf(x_%sy_,x_,y_); }' % (xr(ins, d0), xs(s1, 'f', 1)[0], xr(ins, d0), SS[m]))
            elif m in SD: em('{ double x_=%s.d[0], y_=%s; %s.d[0]=fxn(x_%sy_,x_,y_); }' % (xr(ins, d0), xs(s1, 'd', 1)[0], xr(ins, d0), SD[m]))
            elif m in PS:
                sv = xs(s1, 'f', 4); em('{ float a0=%s,a1=%s,a2=%s,a3=%s; ' % tuple(sv) + ' '.join('%s.f[%d]=fxnf(%s.f[%d]%sa%d,%s.f[%d],a%d);' % (xr(ins, d0), k, xr(ins, d0), k, PS[m], k, xr(ins, d0), k, k) for k in range(4)) + ' }')
            elif m in LG:
                sv = xs(s1, 'u', 4); em('{ u32 a0=%s,a1=%s,a2=%s,a3=%s; ' % tuple(sv) + ' '.join('%s.u[%d]%s=a%d;' % (xr(ins, d0), k, LG[m], k) for k in range(4)) + ' }')
            elif m in ('andnps', 'pandn'):
                sv = xs(s1, 'u', 4); em('{ u32 a0=%s,a1=%s,a2=%s,a3=%s; ' % tuple(sv) + ' '.join('X.u[%d]=~X.u[%d]&a%d;'.replace('X', xr(ins, d0)) % (k, k, k) for k in range(4)) + ' }')
            elif m == 'sqrtss': em('{ float x_=%s; %s.f[0]=fxnf(__builtin_sqrtf(x_),x_,x_); }' % (xs(s1, 'f', 1)[0], xr(ins, d0)))
            elif m in ('minss', 'maxss'): em('{ float a=%s.f[0], b=%s; %s.f[0]=(a%sb)?a:b; }' % (xr(ins, d0), xs(s1, 'f', 1)[0], xr(ins, d0), '<' if m == 'minss' else '>'))
            elif m in ('comiss', 'ucomiss', 'comisd', 'ucomisd'):
                f = 'f' if m.endswith('ss') else 'd'
                em('{ double x=%s.%s[0], y=%s; of=sf=af=0; if(x!=x||y!=y){zf=pf=cf=1;} else {zf=x==y; pf=0; cf=x<y;} }' % (xr(ins, d0), f, xs(s1, f, 1)[0]))
            elif m == 'cvtsi2ss': em('%s.f[0]=(float)(s32)%s;' % (xr(ins, d0), rd(ins, s1)))
            elif m == 'cvtsi2sd': em('%s.d[0]=(double)(s32)%s;' % (xr(ins, d0), rd(ins, s1)))
            elif m in ('cvttss2si', 'cvttsd2si'): em(wr(ins, d0, 'fx_i32((double)%s,3)' % xs(s1, 'f' if 'ss' in m else 'd', 1)[0]))
            elif m in ('cvtss2si', 'cvtsd2si'): em(wr(ins, d0, 'fx_i32((double)%s,0)' % xs(s1, 'f' if 'ss' in m else 'd', 1)[0]))
            elif m == 'cvtss2sd': em('%s.d[0]=(double)%s;' % (xr(ins, d0), xs(s1, 'f', 1)[0]))
            elif m == 'cvtsd2ss': em('%s.f[0]=(float)%s;' % (xr(ins, d0), xs(s1, 'd', 1)[0]))
            elif m == 'shufps':
                im = ops[2].imm; sv = xs(s1, 'u', 4); D = xr(ins, d0)
                em('{ u32 a0=%s.u[0],a1=%s.u[1],a2=%s.u[2],a3=%s.u[3]; u32 b0=%s,b1=%s,b2=%s,b3=%s; ' % ((D,) * 4 + tuple(sv)) +
                   '%s.u[0]=a%d; %s.u[1]=a%d; %s.u[2]=b%d; %s.u[3]=b%d; }' % (D, im & 3, D, (im >> 2) & 3, D, (im >> 4) & 3, D, (im >> 6) & 3))
            elif m in ('unpcklps', 'unpckhps'):
                sv = xs(s1, 'u', 4); D = xr(ins, d0); h = 2 if m == 'unpckhps' else 0
                em('{ u32 a0=%s.u[%d],a1=%s.u[%d]; u32 b0=%s,b1=%s; %s.u[0]=a0; %s.u[1]=b0; %s.u[2]=a1; %s.u[3]=b1; }' % (D, h, D, h + 1, sv[h], sv[h + 1], D, D, D, D))
            elif m == 'movlhps': em('%s.q[1]=%s.q[0];' % (xr(ins, d0), xr(ins, s1)))
            elif m == 'movhlps': em('%s.q[0]=%s.q[1];' % (xr(ins, d0), xr(ins, s1)))
            elif m in ('movlps', 'movhps'):
                k = 1 if m == 'movhps' else 0
                if d0.type == X.X86_OP_REG: em('%s.q[%d]=M64(%s);' % (xr(ins, d0), k, ea(ins, s1)))
                else: em('M64(%s)=%s.q[%d];' % (ea(ins, d0), xr(ins, s1), k))
            else: raise Unsupported('sse ' + m)
        else:
            raise Unsupported('insn ' + (pfx + ' ' if pfx else '') + m)
        if not term and (idx + 1 == len(order) or order[idx + 1] != nxt):
            if nxt in insns: em('goto %s;' % lab(nxt))
            elif m not in ('ret', 'jmp', 'int3'): em('SYNC_OUT; fnvl_trap(c,0x%xu,2); return;' % nxt)
    body = '\n'.join(L)
    fl = ('SYNC_F_IN;' if 'x87' in kinds else '') + ('SYNC_X_IN;' if 'sse' in kinds else '')
    body = body.replace('SYNC_OUT;', 'SYNC_OUT;' + ('SYNC_F_OUT;' if 'x87' in kinds else '') + ('SYNC_X_OUT;' if 'sse' in kinds else ''))
    body = body.replace('SYNC_IN;', 'SYNC_IN;' + fl)
    txt = 'extern "C" void L_%08x(struct cpu *c)\n{ DECL; SYNC_IN;%s%s\n%s\n}\n' % (addr, fl, ' u32 fsb=c->fs;' if usesfs else '', body)
    if usesfs: kinds.add('fs')
    meta = dict(addr=addr, size=size, selfcalls=selfcalls, callrets=callrets, kinds=sorted(kinds), n=len(order), tables=len(tables))
    return txt, meta

PRELUDE = r'''
typedef unsigned char u8; typedef unsigned short u16; typedef unsigned int u32; typedef unsigned long long u64;
typedef signed char s8; typedef short s16; typedef int s32; typedef long long s64;
typedef u8 __attribute__((may_alias)) u8a; typedef u16 __attribute__((aligned(1),may_alias)) u16a;
typedef u32 __attribute__((aligned(1),may_alias)) u32a; typedef u64 __attribute__((aligned(1),may_alias)) u64a;
typedef float __attribute__((aligned(1),may_alias)) f32a; typedef double __attribute__((aligned(1),may_alias)) f64a;
typedef union { u32 u[4]; float f[4]; double d[2]; u64 q[2]; } __attribute__((may_alias)) xmm_t;
struct cpu { u32 r[8]; u32 fs; u32 ftop, ftag, fcw, fc; double st[8]; xmm_t x[8]; };
extern "C" { void fnvl_call(struct cpu*, u32 target); void fnvl_tail(struct cpu*, u32 target); void fnvl_trap(struct cpu*, u32 at, u32 why); }
#define P(a) ((unsigned long)(u32)(a))
#define M8(a) (*(u8a*)P(a))
#define M16(a) (*(u16a*)P(a))
#define M32(a) (*(u32a*)P(a))
#define M64(a) (*(u64a*)P(a))
#define MF32(a) (*(f32a*)P(a))
#define MF64(a) (*(f64a*)P(a))
#define PAR(x) (!(__builtin_popcount((x)&0xffu)&1))
#define DECL u32 eax,ecx,edx,ebx,esp,ebp,esi,edi; u32 cf=0,zf=0,sf=0,of=0,pf=0,af=0,df=0; u32 ftop=0,ftag=0,fcw=0x27f,c0=0,c2=0,c3=0; double st[8]; xmm_t x0,x1,x2,x3,x4,x5,x6,x7; (void)af;(void)pf;(void)df;(void)c2
#define SYNC_IN do{ eax=c->r[0];ecx=c->r[1];edx=c->r[2];ebx=c->r[3];esp=c->r[4];ebp=c->r[5];esi=c->r[6];edi=c->r[7]; }while(0)
#define SYNC_OUT do{ c->r[0]=eax;c->r[1]=ecx;c->r[2]=edx;c->r[3]=ebx;c->r[4]=esp;c->r[5]=ebp;c->r[6]=esi;c->r[7]=edi; }while(0)
#define SYNC_F_IN do{ ftop=c->ftop;ftag=c->ftag;fcw=c->fcw; for(int i_=0;i_<8;i_++) st[i_]=c->st[i_]; }while(0)
#define SYNC_F_OUT do{ c->ftop=ftop;c->ftag=ftag;c->fcw=fcw; for(int i_=0;i_<8;i_++) c->st[i_]=st[i_]; }while(0)
#define SYNC_X_IN do{ x0=c->x[0];x1=c->x[1];x2=c->x[2];x3=c->x[3];x4=c->x[4];x5=c->x[5];x6=c->x[6];x7=c->x[7]; }while(0)
#define SYNC_X_OUT do{ c->x[0]=x0;c->x[1]=x1;c->x[2]=x2;c->x[3]=x3;c->x[4]=x4;c->x[5]=x5;c->x[6]=x6;c->x[7]=x7; }while(0)
#define ST(i) st[(ftop+(i))&7]
#define FPUSH(v) do{ double v_=(v); ftop=(ftop-1)&7; st[ftop]=v_; ftag|=1u<<ftop; }while(0)
#define FPOP() do{ ftag&=~(1u<<ftop); ftop=(ftop+1)&7; }while(0)
#define FCOM(a,b) do{ double a_=(a), b_=(b); if(a_!=a_||b_!=b_){c3=c2=c0=1;} else {c3=a_==b_; c2=0; c0=a_<b_;} }while(0)
/* x86 default NaN (invalid operation) is the negative quiet NaN; AArch64 generates the positive one */
static inline double fxn(double r,double x,double y){ if(__builtin_expect(r!=r,0)&&x==x&&y==y) return -__builtin_nan(""); return r; }
#define FXN(r,x,y) fxn(r,x,y)
static inline float fxnf(float r,float x,float y){ if(__builtin_expect(r!=r,0)&&x==x&&y==y) return -__builtin_nanf(""); return r; }
#define FSW ((ftop<<11)|(c3<<14)|(c2<<10)|(c0<<8))
static inline double fx_round(double v,u32 rc){ return rc==0?__builtin_rint(v):rc==1?__builtin_floor(v):rc==2?__builtin_ceil(v):__builtin_trunc(v); }
static inline u32 fx_i32(double v,u32 rc){ double r=fx_round(v,rc); if(!(r>=-2147483648.0&&r<=2147483647.0)) return 0x80000000u; return (u32)(s32)r; }
static inline u32 fx_i16(double v,u32 rc){ double r=fx_round(v,rc); if(!(r>=-32768.0&&r<=32767.0)) return 0x8000u; return (u16)(s16)r; }
static inline u64 fx_i64(double v,u32 rc){ double r=fx_round(v,rc); if(!(r>=-9223372036854775808.0&&r<9223372036854775808.0)) return 0x8000000000000000ull; return (u64)(s64)r; }
'''
