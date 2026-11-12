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
    """{variable: {models}} from `readelf -rW`: each relocation of a TLS family names a symbol; a section anchor (.LANCHORn, an assembler-private
    label) stands for the variable defined at its value + addend in the same section"""
    tab = tls_table(path)
    byval = {(v, n): nm for nm, v, n in tab if not nm.startswith(('.L', '$'))}
    anchor = {nm: (v, n) for nm, v, n in tab if nm.startswith('.L')}
    got = {}
    for ln in readelf('-rW', path).split('\n'):
        f = ln.split()
        if len(f) >= 7 and f[2].startswith('R_AARCH64'):
            m = family(f[2])
            if not m:
                continue
            nm, add = f[4], int(f[6]) if len(f) > 6 and f[5] == '+' else 0
            if nm in anchor:
                v, n = anchor[nm]
                nm = byval.get((v + add, n), nm)
            got.setdefault(nm, set()).add(m)
    return got


def tls_symbols(path):
    """the names of the object's TLS symbols; the assembler-private labels (`.L*`, here the section anchors) and the AArch64 mapping symbols
    (`$d`, `$x`, ELF for the Arm 64-bit architecture) are not variables"""
    return sorted(nm for nm, _v, _n in tls_table(path) if not nm.startswith(('.L', '$')))


def section_rows(data):
    sec = ELFFile(io.BytesIO(data)).get_section_by_name('.fnvnx.tls')
    return sec.data().decode() if sec is not None else None


SRC = open(os.path.join(realobj.FIXDIR, 'tls.cc')).read()
for label, flags in (('product flags', []), ('-ftls-model=initial-exec', ['-ftls-model=initial-exec'])):
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
    for nm, m in sorted(model.items()):
        seen = rel.get(nm)
        check('%s: %s: relocation family agrees with model %s' % (label, nm, m), seen is not None and (seen == {m} or (m == 'local-dynamic' and seen == {'global-dynamic'})), seen)
    if not flags:
        want = {'e_ie': 'initial-exec', 'd_le': 'local-exec', 'd_gd': 'global-dynamic', 'd_ld': 'local-dynamic'}
        for nm, m in want.items():
            check('%s: attribute %s gives %s' % (label, nm, m), model.get(nm) == m, model.get(nm))
        check('%s: static thread_local and the inline function one are present' % label,
              any(n.endswith('s_tl') or 's_tl' in n for n in model) and any('in_tl' in n for n in model), sorted(model))
    else:
        check('%s: attribute still wins' % label, model.get('d_le') == 'local-exec' and model.get('e_ie') == 'initial-exec', model)
print('FAILED: %s' % FAILED if FAILED else 'all passed')
sys.exit(1 if FAILED else 0)
