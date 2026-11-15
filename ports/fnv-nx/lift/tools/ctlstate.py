#!/usr/bin/env python3
"""(f) inventory of the writers of the control state that decides whether an instruction raises a hardware exception at its own address.

    ctlstate.py IMAGE FACTS_DIR [IMAGE FACTS_DIR ...]      prints one JSON object: per image and writer class the shapes-free site counts (instructions, entries) and the sites

CONTROL STATE (owner order, orchestrator 2026-11-14): x87 control word, MXCSR, EFLAGS TF / AC / DF, Dr0-Dr7 (CONTEXT_DEBUG_REGISTERS). WRITERS, the instructions whose
architectural effect writes it: popf/popfd (EFLAGS image from the stack: TF AC DF), iret/iretd (EFLAGS from the stack), fldcw / fninit / finit / fldenv / frstor / fxrstor (x87 control word),
ldmxcsr / fxrstor (MXCSR), mov to dr0-dr7 (CPL 0 only: #GP at CPL 3, the lifter raises it), std / cld (DF), sahf (flags below bit 8 only: no TF/AC/DF), and the calls that load a CONTEXT:
SetThreadContext / NtSetContextThread / RtlRestoreContext / NtContinue / RtlUnwind(Ex) / longjmp family, plus _controlfp/_control87/_controlfp_s/__control87_2/_fpreset. A CONTEXT is also
written by a SEH handler or filter through the pointer it is given: those are the entries of the images with an exception-handler kind (eh-*, seh-*) in universe.tsv, listed as `context_handlers`.
This tool is the INVENTORY (a fact of the code, no value claims). The value of each writer is judged by the value analysis that follows it (R1-R3, see the report to the lead)."""
import collections, json, os, sys
sys.path[:0] = [os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'gates')]
from capstone import x86 as X
import gate_flags as g

CLASS = {'popf': 'popf', 'popfd': 'popf', 'popfw': 'popf', 'iret': 'iret', 'iretd': 'iret', 'iretw': 'iret',
         'fldcw': 'fldcw', 'fninit': 'fninit', 'finit': 'fninit', 'fldenv': 'fldenv', 'frstor': 'frstor', 'fxrstor': 'fxrstor', 'ldmxcsr': 'ldmxcsr',
         'std': 'df', 'cld': 'df', 'sahf': 'sahf', 'xrstor': 'xrstor'}
CTX_IMPORTS = ('SetThreadContext', 'NtSetContextThread', 'RtlRestoreContext', 'NtContinue', 'RtlUnwind', 'RtlUnwindEx', 'longjmp', '_longjmp', '_controlfp', '_control87', '_controlfp_s', '__control87_2', '_fpreset')
HANDLER_KINDS = ('eh-unwind', 'eh-catch', 'eh-cont', 'seh-filter', 'seh-handler', 'eh-throw')


def imports(facts):
    out = {}
    p = os.path.join(facts, 'imports.tsv')
    if os.path.exists(p):
        for l in open(p):
            t = l.rstrip('\n').split('\t')
            if len(t) >= 3 and not l.startswith('#') and t[2] != '-': out[int(t[2], 16)] = t[1]
    return out


def scan(pe, facts):
    base, rows = g.load_ranges(os.path.join(facts, 'ranges.tsv')); img = g.Image(pe, base)
    uni = g.load_universe(os.path.join(facts, 'universe.tsv')); imp = imports(facts)
    res = collections.defaultdict(lambda: dict(sites=0, entries=set(), addrs=[])); seen = set()
    for e in sorted(rows):
        for lo, hi in rows[e]:
            if (lo, hi) in seen: continue
            seen.add((lo, hi)); code = img.read(lo, hi)
            if code is None: continue
            for i in g._md.disasm(code, lo):
                m = i.mnemonic.split()[-1]; c = CLASS.get(m)
                if m == 'mov' and i.operands and i.operands[0].type == X.X86_OP_REG and i.reg_name(i.operands[0].reg)[:2] == 'dr': c = 'mov-dr'
                if m in ('call', 'jmp') and i.operands and i.operands[0].type == X.X86_OP_MEM and not i.operands[0].mem.base and not i.operands[0].mem.index:
                    n = imp.get(i.operands[0].mem.disp & 0xffffffff)
                    if n in CTX_IMPORTS: c = 'import:' + n
                if c: r = res[c]; r['sites'] += 1; r['entries'].add(e); r['addrs'].append(i.address)
    hk = sorted(a for a, (k, fl) in uni.items() if k in HANDLER_KINDS)
    out = {c: dict(sites=v['sites'], entries=len(v['entries']), addrs=['%08x' % a for a in v['addrs'][:2000]]) for c, v in sorted(res.items())}
    out['context_handlers'] = dict(entries=len(hk))
    out['imports_known'] = bool(imp)
    return out


def main(argv):
    if not argv or len(argv) % 2: print('usage: ctlstate.py IMAGE FACTS [IMAGE FACTS ...]', file=sys.stderr); return 2
    res = {os.path.basename(pe): scan(pe, f) for pe, f in zip(argv[::2], argv[1::2])}
    print(json.dumps(res, sort_keys=True, indent=1)); return 0


if __name__ == '__main__': sys.exit(main(sys.argv[1:]))
