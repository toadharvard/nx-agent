"""CPUID-gated instructions: ONE rule, ONE source (the declared CPU record verify/cpuid_model.json).

THE RULE (CPUID-UD, owner decision, CLAUDE.md "The declared CPU"). The declared CPU (verify/cpuid_model.json, words derived by cpumodel_bits.model_words, the same words rt/cpumodel.c returns) advertises a set of
CPUID feature bits. The lifter's pinned decoder (capstone, env.json) tags an instruction with the groups of the features it needs (X86_GRP_*). Each feature group has one row in the record's `capstone_groups`
(group -> leaf, register, bit). An instruction is #UD exactly when one of its groups has a bit that is 0 on the declared CPU. A leaf above the record's max_basic / max_ext is all zero. The check is made in
core.dispatch (the gate it is given: core.set_emit(emit, missing=this module's `missing`)) before any handler of any family runs, so no handler implements an instruction the declared CPU's CPUID denies. The
lifted set (MMX, SSE, SSE2) is what the record's words leave set; nothing here restates it.

COMPLETENESS (total over the decoder's groups, no list of mnemonics): every X86_GRP_* constant of the pinned capstone is a key of `capstone_groups` or a member of `capstone_nonfeature` (tests/test_cleared_groups.py;
`_groups` raises otherwise, so a decoder with a new group cannot lift silently). There are no certificates: the instruction is lowered to the synchronous #UD raise of f_sys.ud_site at its address."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import json

from fnvnx.lift.fnvlift import inputs

_S = {}


def _model():
    path = inputs.get('cpumodel')
    if _S.get('path') != path:
        from fnvnx.lift.fnvlift import cpumodel_bits as B
        m = json.load(open(path))
        _S.clear(); _S.update(path=path, cpu=B.declared_cpu(m), groups=_groups(m), ud={k: (B.I(v[0]), v[1], v[2], v[3], v[4]) for k, v in m['ud_encodings'].items()}, exec_as=B.exec_as(m), memo={})
    return _S


def _cpu():
    """{(leaf, reg): value, (0,'EAX'): max basic leaf, (0x80000000,'EAX'): max extended leaf}: the declared CPU's words, derived from the `cpumodel` input (verify/cpuid_model.json; cpumodel_bits.declared_cpu)"""
    return _model()['cpu']


def _groups(m):
    """{capstone group id: (name, leaf, reg, bit)} of the record's `capstone_groups`; ValueError when a capstone X86_GRP_* constant is in neither `capstone_groups` nor `capstone_nonfeature`, or a row names no constant"""
    from capstone import x86_const as X
    from fnvnx.lift.fnvlift.cpumodel_bits import I
    consts = {k[8:]: v for k, v in vars(X).items() if k.startswith('X86_GRP_')}
    rows, non = m['capstone_groups'], set(m['capstone_nonfeature'])
    bad = sorted(n for n in consts if n not in rows and n not in non) + sorted(n for n in list(rows) + list(non) if n not in consts)
    if bad or set(rows) & non:
        raise ValueError('cpuid_model.json capstone_groups / capstone_nonfeature do not partition capstone X86_GRP_*: %s' % ', '.join(bad or sorted(set(rows) & non)))
    return {consts[n]: (n, I(r[0]), r[1], r[2]) for n, r in rows.items()}


def _bit(S, leaf, reg, bit):
    """the declared value (0/1) of CPUID bit LEAF.REG[BIT]"""
    from fnvnx.lift.fnvlift import cpumodel_bits as B
    return B.bit(S['cpu'], leaf, reg, bit)


def gates(ins):
    """-> [(group, leaf, register, bit, value on the declared CPU)] of every feature group capstone tags the instruction with: the evidence of `missing` (value 0 = the feature is clear)"""
    S = _model()
    return sorted((n, leaf, reg, bit, _bit(S, leaf, reg, bit)) for n, leaf, reg, bit in (S['groups'][g] for g in ins.groups if g in S['groups']))


PREFIXES = (0x66, 0xf2, 0xf3, 0x2e, 0x36, 0x3e, 0x26, 0x64, 0x65, 0x67, 0xf0)


def _prefix_end(raw):
    """index of the opcode byte after the legacy prefixes of RAW"""
    i = 0
    while i < len(raw) and raw[i] in PREFIXES: i += 1
    return i


def _enc(raw):
    """the opcode byte after the legacy prefixes, and the ModRM byte (None at the end of the bytes)"""
    i = _prefix_end(raw)
    return (raw[i] if i < len(raw) else None), (raw[i + 1] if i + 1 < len(raw) else None)


def _alias(raw, i, only_nop=None):
    """[(name, row)] of the record's `exec_as` rows whose encoding RAW is (I = the index of its opcode byte after the legacy prefixes; the row's mandatory prefix, when it has one, is among the prefixes) and whose CPUID bit is 0 on the declared CPU"""
    S = _model(); pre = bytes(raw[:i])
    return [(n, r) for n, r in S['exec_as'].items() if (only_nop is None or (r['as'] == 'nop') == only_nop) and not _bit(S, r['leaf'], r['reg'], r['bit'])
            and raw[i:i + len(r['op'][0]) // 2].hex() in r['op'] and (not r['prefix'] or bytes.fromhex(r['prefix']) in [pre[k:k + 1] for k in range(len(pre))])]


def missing(ins):
    """the features of the instruction that the declared CPU's CPUID denies (non-empty = #UD): its capstone groups with a clear bit, and the `ud_encodings` rows of the record that match its bytes.  An encoding of a feature whose
    CPUID bit is clear that the record's `exec_as` says executes as another instruction (TZCNT as BSF, MPX as NOP) is not denied by that feature's bit: it is no #UD"""
    raw = bytes(ins.bytes); i = _prefix_end(raw)
    ex = {(r['leaf'], r['reg'], r['bit']) for _n, r in _alias(raw, i)}
    out = [n for n, leaf, reg, bit, v in gates(ins) if v == 0 and (leaf, reg, bit) not in ex]
    S = _model(); op, modrm = _enc(raw)
    for name, (leaf, reg, bit, encs, cite) in S['ud'].items():
        if modrm is not None and not _bit(S, leaf, reg, bit) and any(e['op'] == '%02x' % op and e['reg'] == (modrm >> 3) & 7 and (not e['mem'] or modrm >> 6 != 3) for e in encs):
            out.append(name)
    return out


def nop(raw, i):
    """the feature (a key of the record's `exec_as`, `as` = 'nop') whose encoding RAW (the instruction's bytes, I = the index of its opcode byte after the legacy prefixes) is and whose CPUID bit is 0 on the declared CPU: such an instruction executes as NOP (the SDM's statement, cited in the record); None otherwise"""
    a = _alias(raw, i, True)
    return a[0][0] if a else None
