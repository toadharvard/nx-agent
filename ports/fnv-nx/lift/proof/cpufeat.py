"""CPUID-gated instruction forms: ONE rule, ONE source (the Intel SDM opcode tables).

THE RULE (CPUID-UD). The declared CPU (verify/cpuid_model.json, derived by cpumodel_bits.model_words, the same words rt/cpumodel.c returns) advertises a set of CPUID feature flags. A decoded form is matched to the SDM's
opcode-table rows by proof/sdmfeat.match_form (the one match that proof/sdmfeat.py `rows` (L3b's cleared-feature partition) and proof/udsail.py class (a) use): (mandatory prefix, opcode bytes, ModRM.reg or fixed ModRM byte).
The rows give the form's `CPUID Feature Flag` expression (sdmfeat.needs_of: the column, or the `#UD If CPUID...` lines of the section for the older table layout). The expression is evaluated over the SDM's own flag table
(Vol. 1 ch. 21, the `sdmrows` input) and the declared CPU. The form is #UD (`missing` non-empty) exactly when the expression is false AND the SDM states no `executed as` alias for the flag (TZCNT as BSF, LZCNT as BSR, PREFETCHW as a
hint) AND the flag is not one the SDM defines as NOP without it (MPX). The check is made in core.dispatch (the gate it is given: core.set_emit(emit, missing=this module's `missing`)) BEFORE any handler of any family runs, so no handler can implement a form that the declared CPU's CPUID denies.

COMPLETENESS (the rule is total over the decoded forms, with no list of names or capstone groups). Every form the lifter decodes has one of three outcomes, decided only by the SDM text:
  * its rows name one flag expression: present on the declared CPU (lifted by its handler) or absent (#UD), or no flag (base ISA);
  * no row, but the opcode lies in an opcode-map entry the SDM defines (sdmfeat.OPMAP: x87 escape tables, the 0F map rows with no CPUID flag): base ISA;
  * no row and no opcode-map entry: the pinned Sail model decides the form's bytes (proof/udsail.py rule b, the `udsail` input): every path #UD -> status `sail`, #UD (needs the call-graph certificate); a completing
    path is a defined instruction and `Unsupported` until it has a Form; a model error is `Unsupported`;
  * rows with several flag expressions (ambiguous) or a flag name the SDM's table does not define: `Unsupported`, never a silent default. Such a site is reported (the build is red), not lifted.
The capstone instruction-group tags and the per-mnemonic tables are NOT read: capstone's classification is not a source (Assumption removed: ASM-203 is deleted with them).

Source of the declared CPU's words: fnvlift/cpumodel_bits.py (one derivation). The flag leaf is within the declared max basic leaf (cpuid_model.json max_basic) or max extended leaf, as in sdmfeat.present."""
import json

from fnvlift import inputs

_S = {}


def _cpu():
    """{(leaf, reg): value, (0,'EAX'): max basic leaf, (0x80000000,'EAX'): max extended leaf}: the declared CPU's words, derived from the `cpumodel` input (verify/cpuid_model.json; cpumodel_bits.model_words)"""
    from fnvlift.cpumodel_bits import model_words, I
    m = json.load(open(inputs.get('cpumodel'))); w = model_words(m)
    return {(0, 'EAX'): I(m['max_basic']), (0x80000000, 'EAX'): I(m['max_ext']), (1, 'EDX'): w['leaf1_edx'], (1, 'ECX'): w['leaf1_ecx'], (0x80000001, 'EDX'): w['ext1_edx'], (0x80000001, 'ECX'): w['ext1_ecx']}


def _state():
    path = inputs.get('sdmrows')
    if _S.get('path') != path:
        from proof import sdmfeat
        rows, table, nopflags = sdmfeat.load_rows(path)
        _S.clear(); _S.update(path=path, idx=sdmfeat.index_rows(rows), table=table, nop=nopflags, cpu=_cpu(), memo={})
    return _S


def decide(ins):
    """-> dict(status found|map|sail, needs, value none|present|absent, exec_as, rows) of a decoded form (sail: value absent); Unsupported when its rows name several flag expressions or an unknown flag, or no row and no Sail #UD verdict"""
    from proof import sdmfeat
    from fnvlift.core import Unsupported
    S = _state(); key = bytes(ins.bytes)
    r = S['memo'].get(key)
    if r is None:
        cand, kind, mp = sdmfeat.match_form(ins, S['idx'])
        if cand:
            st, needs, val, ea, bad = sdmfeat.needs_of(cand, S['table'], S['cpu'], S['nop'])
            if st != 'found' or bad:
                r = ('Unsupported', 'SDM rows of %s: %s' % (key.hex(), bad))
            else:
                r = dict(status=st, needs=needs, value=val, exec_as=ea, rows=cand)
        elif mp:
            r = dict(status='map', needs='-', value='none', exec_as='-', rows=[])
        else:
            # no SDM row (the text parse can miss rows; silicon executes some such encodings as aliases, and capstone decoding the form is evidence it is defined): the pinned Sail model decides it on its bytes, by the rule
            # of proof/udsail.py (b). Every path #UD (a row of the `udsail` input) -> #UD, class sail. A path that completes is a defined instruction and needs its Form; a model error is a gap: both are red here.
            from fnvlift import f_sys
            u = f_sys.ud_gap(key)
            if u is None:
                r = ('Unsupported', 'no SDM row for %s and no Sail #UD verdict (udsail input): a defined instruction needs a Form, a model gap is red' % key.hex())
            else:
                r = dict(status='sail', needs='Sail #UD (%s)' % u.ud_class, value='absent', exec_as='-', rows=[])
        S['memo'][key] = r
    if isinstance(r, tuple):
        raise Unsupported(r[1])
    return r


def missing(ins):
    """the CPUID flag expressions the form needs that the declared CPU denies, with no SDM alias (non-empty = #UD)"""
    d = decide(ins)
    return [d['needs']] if d['value'] == 'absent' and d['exec_as'] == '-' else []


def gates(ins):
    """-> [(flag, leaf, register, bit, value on the declared CPU)] of every flag the form's expression names: the evidence of `missing` (value 0 = the flag is clear)"""
    import re
    from proof import sdmfeat
    S = _state(); d = decide(ins); out = []
    for f in sorted({t for t in re.findall(r'[A-Za-z0-9_\.]+', d['needs']) if d['status'] == 'found' and t not in ('AND', 'OR', '-')}):
        for leaf, sub, reg, bit in sorted(S['table'].addresses(f))[:1]:
            out.append((f, leaf, reg, bit, int(sdmfeat.present(f, S['table'], S['cpu']))))
    return out
