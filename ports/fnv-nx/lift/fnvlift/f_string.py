"""String instructions with optional rep/repe/repne prefix.
All of movs/stos/lods/scas/cmps in b/w/d forms, with rep/repe/repne. A form with an xmm operand (SSE2 movsd, cmpsd) falls through to SSE.
(The PoC took them by `operand 0 is memory`, which missed scas/lods/cmps with a register first operand.)"""
from capstone import x86 as X
from .core import op, SKIP, MASK


@op(*[k + w for k in ('movs', 'stos', 'lods', 'scas', 'cmps') for w in 'bwd'])
def h_string(cx, ins):
    m, ops, pfx, em = cx.m, cx.ops, cx.pfx, cx.em
    if any(o.type == X.X86_OP_REG and ins.reg_name(o.reg).startswith('xmm') for o in ops):
        return SKIP     # SSE2 movsd/cmpsd have an xmm operand; the string forms never do
    zsp = cx.zsp
    w = {'b': 8, 'w': 16, 'd': 32}[m[-1]]
    st = 'u32 st=df?-%du:%du;' % (w // 8, w // 8)
    k = m[:-1]
    if ins.prefix[3] == 0x67:       # 16-bit address size (SDM string instructions: DS:SI / ES:DI / CX): the 16-bit registers address and count, the upper halves are kept
        return string16(cx, ins, w, k, st)
    body = {'movs': 'M%d(edi)=M%d(esi); esi+=st; edi+=st;' % (w, w),
            'stos': 'M%d(edi)=eax&%s; edi+=st;' % (w, MASK[w]),
            'lods': 'eax=(eax&~%s)|M%d(esi); esi+=st;' % (MASK[w], w),
            'scas': '{ u32 a=eax&%s, b=M%d(edi), r=(a-b)&%s; cf=a<b; of=(((a^b)&(a^r))>>%d)&1; af=((a^b^r)>>4)&1; %s } edi+=st;' % (MASK[w], w, MASK[w], w - 1, zsp(w)),
            'cmps': '{ u32 a=M%d(esi), b=M%d(edi), r=(a-b)&%s; cf=a<b; of=(((a^b)&(a^r))>>%d)&1; af=((a^b^r)>>4)&1; %s } esi+=st; edi+=st;' % (w, w, MASK[w], w - 1, zsp(w))}[k]
    if not pfx:
        em('{ %s %s }' % (st, body))
    elif k in ('movs', 'stos', 'lods'):
        em('{ %s while(ecx){ %s ecx--; } }' % (st, body))
    else:
        em('{ %s while(ecx){ %s ecx--; if(%s) break; } }' % (st, body, '!zf' if pfx in ('rep', 'repe') else 'zf'))


def string16(cx, ins, w, k, st):
    """the string instructions under the 0x67 prefix: SI, DI and CX replace ESI, EDI, ECX (segment bases are 0). Same bodies as above with 16-bit pointers: the address is the low word,
    the register update wraps within the low word."""
    SI, DI = '(esi&0xffffu)', '(edi&0xffffu)'
    zsp = cx.zsp
    ui = 'esi=(esi&0xffff0000u)|((esi+st)&0xffffu);'
    ud = 'edi=(edi&0xffff0000u)|((edi+st)&0xffffu);'
    body = {'movs': 'M%d(%s)=M%d(%s); %s %s' % (w, DI, w, SI, ui, ud),
            'stos': 'M%d(%s)=eax&%s; %s' % (w, DI, MASK[w], ud),
            'lods': 'eax=(eax&~%s)|M%d(%s); %s' % (MASK[w], w, SI, ui),
            'scas': '{ u32 a=eax&%s, b=M%d(%s), r=(a-b)&%s; cf=a<b; of=(((a^b)&(a^r))>>%d)&1; af=((a^b^r)>>4)&1; %s } %s' % (MASK[w], w, DI, MASK[w], w - 1, zsp(w), ud),
            'cmps': '{ u32 a=M%d(%s), b=M%d(%s), r=(a-b)&%s; cf=a<b; of=(((a^b)&(a^r))>>%d)&1; af=((a^b^r)>>4)&1; %s } %s %s' % (w, SI, w, DI, MASK[w], w - 1, zsp(w), ui, ud)}[k]
    dec = 'ecx=(ecx&0xffff0000u)|((ecx-1)&0xffffu);'
    pfx = cx.pfx
    if not pfx:
        cx.em('{ %s %s }' % (st, body))
    elif k in ('movs', 'stos', 'lods'):
        cx.em('{ %s while(ecx&0xffffu){ %s %s } }' % (st, body, dec))
    else:
        cx.em('{ %s while(ecx&0xffffu){ %s %s if(%s) break; } }' % (st, body, dec, '!zf' if pfx in ('rep', 'repe') else 'zf'))
