"""String instructions with optional rep/repe/repne prefix.
Quirk kept from the PoC: movsd/stosd etc. are taken whenever operand 0 is memory (or there are no operands),
so SSE `movsd [mem], xmm` is decided here too; with a register first operand dispatch falls through to SSE."""
from capstone import x86 as X
from .core import op, SKIP, MASK


@op('movsb', 'movsw', 'movsd', 'stosb', 'stosw', 'stosd', 'scasb', 'cmpsb', 'lodsb', 'lodsd')
def h_string(cx, ins):
    m, ops, pfx, em = cx.m, cx.ops, cx.pfx, cx.em
    if not (not ops or ops[0].type == X.X86_OP_MEM):
        return SKIP
    zsp = cx.zsp
    w = {'b': 8, 'w': 16, 'd': 32}[m[-1]]
    st = 'u32 st=df?-%du:%du;' % (w // 8, w // 8)
    k = m[:-1]
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
