"""Declared defects of the reference model (Sail x86 via isla), each with the SDM sentence that decides against the model.
prove.py classifies a disagreement as FAILED(model-defect:<id>) only if the whole disagreement lies in the region of a declared defect
(the obligation with that region excluded is unsat); anything else is FAILED(dsl).  A region only EXCLUDES pre-states from a field's
obligation after the fact, it never changes the DSL form.
The four former entries (rot-count-modulo, ror-cf, rot-of-count0, sar-cf-big-count) were model defects fixed by the transcription patches of the pinned IR (x86ir, bump IR 7c307464: zrol_spec, zror_spec, zsar_spec); they are deleted, not gated (by-construction row #8).
A region here is a MODEL GAP (the pinned model does not implement the instruction as the SDM states): the SDM text is the decision, the model source is cited.
entry: id, keys (regex on the form key), parts = [(fields or None = every other field, region(ctx) -> z3 Bool)], sdm."""
import re, z3
ALL = None
def prefix_skip(code):
    i = 0
    while code[i] in (0x66, 0x67, 0xf0, 0xf2, 0xf3, 0x2e, 0x36, 0x3e, 0x26, 0x64, 0x65): i += 1
    return i
class Ctx:
    def __init__(s, code, pre, key, fields=None):
        s.code, s.pre, s.key = code, pre, key
        t = key.split('|')[1].split(',')[0]; s.w = int(re.sub(r'\D', '', t.split(':')[0])) if re.search(r'\d', t) else 32
        i = prefix_skip(code); op = code[i]; ecx = pre['regs']['ecx']
        if op in (0xd0, 0xd1): s.n = z3.BitVecVal(1, 32)
        elif op in (0xd2, 0xd3): s.n = z3.ZeroExt(24, z3.Extract(7, 0, ecx)) & 31
        elif op in (0xc0, 0xc1): s.n = z3.BitVecVal(code[-1] & 31, 32) if not fields else z3.ZeroExt(24, z3.Extract(7, 0, z3.BitVec('fld_imm', 8 * fields['imm'][1]))) & 31      # shape proof: the count byte is the free field
        else: s.n = z3.BitVecVal(0, 32)
        s.ecx0 = ecx == 0
DEFECTS = [
 dict(id='rep-prefix-ignored', keys=r'^rep', parts=[({'ecx', 'eip'}, lambda c: z3.BoolVal(True)), (ALL, lambda c: c.ecx0)],
      model='sail-x86-from-acl2 0c6c023d (sail_pins.txt): the x86 model whose Sail output is the pinned IR; its one-iteration treatment of a REP-prefixed string instruction is observed in the shape proofs (journal/inbox/l3b-w4-forms.md), the ACL2 source line is not cited here',
      sdm='SDM REP/REPE/REPNE: repeat the string instruction ECX times (nothing when ECX = 0), decrementing ECX; the model executes the body once, ignores the prefix, leaves ECX and sets EIP to the next instruction'),
]
def regions(key, ctx_args, fields=None):
    """[(id, sdm, {field: region})] for the entries whose key regex matches; region None for fields not listed under ALL"""
    out = []
    for d in DEFECTS:
        if not re.search(d['keys'], key): continue
        c = Ctx(*ctx_args, key, fields); fr = {}; other = None
        for fields, fn in d['parts']:
            if fields is ALL: other = fn(c)
            else:
                for f in fields: fr[f] = fn(c)
        out.append((d['id'], d['sdm'], fr, other))
    return out
