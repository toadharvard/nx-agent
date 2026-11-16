"""CPUID-gated instructions: ONE rule, ONE source (the declared CPU record verify/cpuid_model.json).

THE RULE (CPUID-UD, owner decision, CLAUDE.md "The declared CPU"). The declared CPU (verify/cpuid_model.json, words derived by cpumodel_bits.model_words, the same words rt/cpumodel.c returns) advertises a set of
CPUID feature bits. The lifter's pinned decoder (capstone, env.json) tags an instruction with the groups of the features it needs (X86_GRP_*). Each feature group has one row in the record's `capstone_groups`
(group -> leaf, register, bit). An instruction is #UD exactly when one of its groups has a bit that is 0 on the declared CPU. A leaf above the record's max_basic / max_ext is all zero. The check is made in
core.dispatch (the gate it is given: core.set_emit(emit, missing=this module's `missing`)) before any handler of any family runs, so no handler implements an instruction the declared CPU's CPUID denies. The
lifted set (MMX, SSE, SSE2) is what the record's words leave set; nothing here restates it.

COMPLETENESS (total over the decoder's groups, no list of mnemonics): every X86_GRP_* constant of the pinned capstone is a key of `capstone_groups` or a member of `capstone_nonfeature` (tests/test_cleared_groups.py;
`_groups` raises otherwise, so a decoder with a new group cannot lift silently). There are no certificates: the instruction is lowered to the synchronous #UD raise of f_sys.ud_site at its address."""
import json

from fnvlift import inputs

_S = {}


def _model():
    path = inputs.get('cpumodel')
    if _S.get('path') != path:
        from fnvlift.cpumodel_bits import model_words, I
        m = json.load(open(path)); w = model_words(m)
        cpu = {(0, 'EAX'): I(m['max_basic']), (0x80000000, 'EAX'): I(m['max_ext']), (1, 'EDX'): w['leaf1_edx'], (1, 'ECX'): w['leaf1_ecx'], (0x80000001, 'EDX'): w['ext1_edx'], (0x80000001, 'ECX'): w['ext1_ecx']}
        _S.clear(); _S.update(path=path, cpu=cpu, groups=_groups(m), memo={})
    return _S


def _cpu():
    """{(leaf, reg): value, (0,'EAX'): max basic leaf, (0x80000000,'EAX'): max extended leaf}: the declared CPU's words, derived from the `cpumodel` input (verify/cpuid_model.json; cpumodel_bits.model_words)"""
    return _model()['cpu']


def _groups(m):
    """{capstone group id: (name, leaf, reg, bit)} of the record's `capstone_groups`; ValueError when a capstone X86_GRP_* constant is in neither `capstone_groups` nor `capstone_nonfeature`, or a row names no constant"""
    from capstone import x86_const as X
    from fnvlift.cpumodel_bits import I
    consts = {k[8:]: v for k, v in vars(X).items() if k.startswith('X86_GRP_')}
    rows, non = m['capstone_groups'], set(m['capstone_nonfeature'])
    bad = sorted(n for n in consts if n not in rows and n not in non) + sorted(n for n in list(rows) + list(non) if n not in consts)
    if bad or set(rows) & non:
        raise ValueError('cpuid_model.json capstone_groups / capstone_nonfeature do not partition capstone X86_GRP_*: %s' % ', '.join(bad or sorted(set(rows) & non)))
    return {consts[n]: (n, I(r[0]), r[1], r[2]) for n, r in rows.items()}


def _bit(S, leaf, reg, bit):
    """the declared value (0/1) of CPUID bit LEAF.REG[BIT]"""
    top = S['cpu'][(0, 'EAX')] if leaf < 0x80000000 else S['cpu'][(0x80000000, 'EAX')]
    return (S['cpu'].get((leaf, reg), 0) >> bit) & 1 if leaf <= top else 0


def gates(ins):
    """-> [(group, leaf, register, bit, value on the declared CPU)] of every feature group capstone tags the instruction with: the evidence of `missing` (value 0 = the feature is clear)"""
    S = _model()
    return sorted((n, leaf, reg, bit, _bit(S, leaf, reg, bit)) for n, leaf, reg, bit in (S['groups'][g] for g in ins.groups if g in S['groups']))


def missing(ins):
    """the feature groups of the instruction that the declared CPU's CPUID denies (non-empty = #UD)"""
    return [n for n, leaf, reg, bit, v in gates(ins) if v == 0]
