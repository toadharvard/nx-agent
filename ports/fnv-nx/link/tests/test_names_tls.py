#!/usr/bin/env python3
"""fnvnx_names `.fnvnx.tls`: the TLS model of every thread-local symbol the unit emits (a tls_model declaration is in neither ELF nor DWARF).
Real objects of fixtures/tls.cc built by the one build (realobj.build: msvcmodel compiler, all 8 plugins) at the root's flags and with
-ftls-model=initial-exec.  Expected values come from the compiler, not from this file: the relocation family the object carries for each
symbol (`readelf -rW`: TLSGD/TLSDESC, TLSLD/DTPREL, TLSIE, TLSLE) must agree with the model column; and the TLS symbols of the object's symbol
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


def reloc_models(path):
    """{symbol: {models}} from readelf -rW: the relocation types that name a TLS symbol"""
    out = subprocess.run(['aarch64-linux-gnu-readelf', '-rW', path], capture_output=True, text=True).stdout
    got = {}
    for ln in out.split('\n'):
        f = ln.split()
        if len(f) >= 5 and f[2].startswith('R_AARCH64'):
            m = family(f[2])
            if m:
                got.setdefault(f[4], set()).add(m)
    return got


def tls_symbols(data):
    st = ELFFile(io.BytesIO(data)).get_section_by_name('.symtab')
    return sorted(y.name for y in st.iter_symbols() if y.name and y['st_info']['type'] == 'STT_TLS')


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
    syms = tls_symbols(data)
    check('%s: STT_TLS symbols of the object == names in .fnvnx.tls' % label, syms == sorted(model), (syms, sorted(model)))
    rel = reloc_models(path)
    for nm, m in sorted(model.items()):
        seen = rel.get(nm)
        check('%s: %s: relocation family agrees with model %s' % (label, nm, m), seen is not None and m in seen and len(seen) <= 2, seen)
    if not flags:
        want = {'e_ie': 'initial-exec', 'd_le': 'local-exec', 'd_gd': 'global-dynamic', 'd_ld': 'local-dynamic'}
        for nm, m in want.items():
            check('%s: attribute %s gives %s' % (label, nm, m), model.get(nm) == m, model.get(nm))
        check('%s: static thread_local and the inline function one are present' % label,
              any(n.endswith('s_tl') or 's_tl' in n for n in model) and any('in_tl' in n for n in model), sorted(model))
    else:
        check('%s: the unattributed defined one follows the flag' % label, model.get('d_def') == 'initial-exec', model.get('d_def'))
        check('%s: attribute still wins' % label, model.get('d_le') == 'local-exec', model.get('d_le'))
print('FAILED: %s' % FAILED if FAILED else 'all passed')
sys.exit(1 if FAILED else 0)
