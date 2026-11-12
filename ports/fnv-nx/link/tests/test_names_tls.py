#!/usr/bin/env python3
"""fnvnx_names `.fnvnx.tls`: the TLS model of every thread-local symbol the unit emits (a tls_model declaration is in neither ELF nor DWARF).
Real objects of fixtures/tls.cc built by the one build (realobj.build: msvcmodel compiler, all 8 plugins) at the root's flags and with
-ftls-model=initial-exec.  Expected values come from the compiler, not from this file: the relocation family the object carries for each
symbol (a section anchor stands for the variable at its value + addend; under the TLS descriptor dialect the AArch64 backend
generates local-dynamic as the descriptor sequence, so a local-dynamic row may carry the global-dynamic family) (`readelf -rW`: TLSGD/TLSDESC, TLSLD/DTPREL, TLSIE, TLSLE) must agree with the model column; and the TLS symbols of the object's symbol
table (STT_TLS, defined and undefined) must equal the names of the section.  Cases: extern with tls_model("initial-exec"), defined without
attribute, local-exec, global-dynamic, local-dynamic, a static thread_local, a thread_local in an emitted inline function.
Run: python3 -I <this file> (needs the pinned compiler: ARTIFACTS_URL)."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..'))
import io
from elftools.elf.elffile import ELFFile
import realobj

FAILED = []


def check(name, ok, detail=''):
    print('%s %s%s' % ('PASS' if ok else 'FAIL', name, (': ' + str(detail)[:900]) if detail and not ok else ''), flush=True)
    if not ok:
        FAILED.append(name)
    return ok


FAMILY = (('TLSDESC', 'global-dynamic'), ('TLSGD', 'global-dynamic'), ('DTPREL', 'local-dynamic'), ('TLSLD', 'local-dynamic'),
          ('TLSIE', 'initial-exec'), ('GOTTPREL', 'initial-exec'), ('TPREL', 'local-exec'), ('TLSLE', 'local-exec'))


def family(typ):
    for k, m in FAMILY:
        if k in typ:
            return m
    return None


def readelf(flag, path):
    return subprocess.run(['aarch64-linux-gnu-readelf', flag, path], capture_output=True, text=True).stdout


def tls_table(path):
    """[(name, value, ndx)] of the STT_TLS symbols of the object, from `readelf -sW` (the compiler's and assembler's fact)"""
    out = []
    for ln in readelf('-sW', path).split('\n'):
        f = ln.split()
        if len(f) == 8 and f[0].endswith(':') and f[3] == 'TLS':
            out.append((f[7], int(f[1], 16), f[6]))
    return out


def reloc_models(path):
    """{symbol named by a TLS-family relocation: {families}} from `readelf -rW` (a section anchor `.LANCHORn` is an assembler-private label shared
    by several variables: it is kept under its own name and only enters the set-level check)"""
    got = {}
    for ln in readelf('-rW', path).split('\n'):
        f = ln.split()
        if len(f) >= 5 and f[2].startswith('R_AARCH64'):
            m = family(f[2])
            if m:
                got.setdefault(f[4], set()).add(m)
    return got


def tls_symbols(path):
    """the names of the object's TLS symbols; the assembler-private labels (`.L*`, here the section anchors) and the AArch64 mapping symbols
    (`$d`, `$x`, ELF for the Arm 64-bit architecture) are not variables"""
    return sorted(nm for nm, _v, _n in tls_table(path) if not nm.startswith(('.L', '$')))


def section_rows(data):
    sec = ELFFile(io.BytesIO(data)).get_section_by_name('.fnvnx.tls')
    return sec.data().decode() if sec is not None else None


SRC = open(os.path.join(realobj.FIXDIR, 'tls.cc')).read()
for label, flags in (('product flags', []), ('-ftls-model=initial-exec', ['-ftls-model=initial-exec']), ('-fno-section-anchors', ['-fno-section-anchors'])):
    res = realobj.build({'tls': SRC}, flags=flags)
    if not check('%s: build green' % label, res['status'] == 'green', res.get('failure')):
        continue
    path = res['objects'][0]['object']
    data = open(path, 'rb').read()
    txt = section_rows(data)
    if not check('%s: section present' % label, txt is not None):
        continue
    rows = txt.split('\n')
    check('%s: final LF, no empty row, sorted, unique' % label, txt.endswith('\n') and rows[-1] == '' and '' not in rows[:-1] and rows[:-1] == sorted(set(rows[:-1])), repr(txt))
    model = dict(r.split(' ') for r in rows[:-1])
    check('%s: every row is `<name> <model>`' % label, all(len(r.split(' ')) == 2 and r.split(' ')[1] in ('global-dynamic', 'local-dynamic', 'initial-exec', 'local-exec') for r in rows[:-1]), txt)
    syms = tls_symbols(path)
    check('%s: STT_TLS symbols of the object == names in .fnvnx.tls' % label, syms == sorted(model), (syms, sorted(model)))
    rel = reloc_models(path)
    eff = {nm: ({'global-dynamic'} if m == 'local-dynamic' else {m}) for nm, m in model.items()}   # descriptor dialect: LD is generated as GD
    for nm, m in sorted(model.items()):
        if nm in rel:
            check('%s: %s: relocation family agrees with model %s' % (label, nm, m), rel[nm] <= eff[nm] | ({m} if m == 'local-dynamic' else set()), rel[nm])
        elif not any(k.startswith('.L') for k in rel):
            check('%s: %s: has relocations' % (label, nm), False, rel)
    used = set().union(*rel.values()) if rel else set()
    allowed = set().union(*[eff[n] | {model[n]} for n in model])
    check('%s: families of all relocations are produced by the rows' % label, used <= allowed, (used, allowed))
    check('%s: every row model is used by some relocation' % label, set().union(*eff.values()) <= used, (used, eff))
    if label != '-ftls-model=initial-exec':
        want = {'e_ie': 'initial-exec', 'd_le': 'local-exec', 'd_gd': 'global-dynamic', 'd_ld': 'local-dynamic'}
        for nm, m in want.items():
            check('%s: attribute %s gives %s' % (label, nm, m), model.get(nm) == m, model.get(nm))
        check('%s: static thread_local and the inline function one are present' % label,
              any(n.endswith('s_tl') or 's_tl' in n for n in model) and any('in_tl' in n for n in model), sorted(model))
    else:
        check('%s: attribute still wins' % label, model.get('d_le') == 'local-exec' and model.get('e_ie') == 'initial-exec', model)
print('FAILED: %s' % FAILED if FAILED else 'all passed')
sys.exit(1 if FAILED else 0)
