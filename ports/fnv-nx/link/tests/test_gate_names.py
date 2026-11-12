#!/usr/bin/env python3
"""Check of link/gate_names.py on synthetic ELF and COFF objects (invented names, no game data, no cl, no network):
a definition whose name cl does not define is red and named; an unnamed row, an unnamed vftable, a unit without a cl object, an
unreadable cl object and an unreadable GCC object are red; weak, COMDAT, reference and nameless rows are outside the universe;
an empty universe is green; the gate file and the exit status; the `u<N>` pairing of --units.  Run: python3 -I <this file>."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..'))
import gate_names
import synth
import unitpairs
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

fails = testcase.FAILED
def sympair(*rows):
    return ''.join('\t'.join(r) + '\n' for r in rows).encode()


# symbols: (name, bind, type, shndx, value); sections: .fnvnx.sympair (1), .fnvnx.vtpair (2), .text (3), .data (4), .text.grp (5, SHF_GROUP)
def gcc_obj(rows, vt=b'', is64=False, symbols=None, sections=None):
    secs = sections or [('.fnvnx.sympair', rows, 0), ('.fnvnx.vtpair', vt, 0), ('.text', b'\0' * 4, 6), ('.data', b'\0' * 4, 3), ('.text.grp', b'\0' * 4, 6 | 0x200)]
    syms = symbols if symbols is not None else [
        ('_Z1fv', 1, 2, 3, 0), ('_Z1gv', 1, 2, 3, 4), ('_Z1hv', 2, 2, 3, 8), ('_Z1iv', 1, 2, 5, 0), ('_Z1jv', 1, 0, 0, 0),
        ('_ZN1AC1Ev', 1, 2, 3, 12), ('_ZN1AC2Ev', 1, 2, 3, 12), ('gdata', 1, 1, 4, 0), ('_Z1kv', 1, 2, 3, 16), ('', 3, 3, 3, 0), ('x.cpp', 0, 4, 0xfff1, 0)]
    return synth.elf(secs, syms, is64)


ROWS = sympair(('_Z1fv', '?f@@YAXXZ', 'func', 'def'), ('_Z1gv', '?g@@YAXXZ', 'func', 'def'), ('_Z1hv', '?h@@YAXXZ', 'func', 'def'),
               ('_Z1iv', '?i@@YAXXZ', 'func', 'def'), ('_Z1jv', '?j@@YAXXZ', 'func', 'ref'), ('_ZTV1A', '', 'vtable', 'def'),
               ('_Z1kv', '', 'unnamed:no rule', 'def'), ('_ZN1AC1Ev', '??0A@@QAE@XZ', 'ctor.c1', 'def'),
               ('_ZN1AC2Ev', '??0A@@QAE@XZ', 'ctor.c2', 'def'), ('gdata', '?gdata@@3HA', 'data', 'def'))
VT = b'_ZTV1A\t8\t??_7A@@6B@\t0\n_ZTV1B\t8\tunnamed:no rule\t0\n'


def cl_obj(defined=('?f@@YAXXZ', '??0A@@QAE@XZ'), data=('?gdata@@3HA',), undef=('?g@@YAXXZ',)):
    secs, syms = [], []
    for n in defined:
        secs.append(dict(name=b'.text$mn', chars=0x60501020, data=b'\xc3'))
        syms.append(dict(name=n.encode(), value=0, sec=len(secs), type=0x20, cls=2))
    for n in data:
        secs.append(dict(name=b'.data', chars=0xc0300040, data=b'\0\0\0\0'))
        syms.append(dict(name=n.encode(), value=0, sec=len(secs), type=0, cls=2))
    for n in undef:
        syms.append(dict(name=n.encode(), value=0, sec=0, type=0x20, cls=2))
    return synth.coff(secs, syms)


tmp = tempfile.mkdtemp()
try:
    g, c = os.path.join(tmp, 'g'), os.path.join(tmp, 'c')
    synth.write(g, 'u.o', gcc_obj(ROWS, VT))
    synth.write(c, 'u.obj', cl_obj())
    uni, red = gate_names.check([g], [c])
    # items: f ok, g red, ctor c1 ok, ctor c2 ok, gdata ok, unnamed row, unnamed vftable
    case('universe', got=uni, want=7)
    case('red', got=red, want=['u:_Z1gv: ?g@@YAXXZ is not a definition of the cl object', 'u:_Z1kv:unnamed', 'u:_ZTV1B:vftable-unnamed'])
    # ELF64 gives the same verdicts
    g64 = os.path.join(tmp, 'g64')
    synth.write(g64, 'u.o', gcc_obj(ROWS, VT, is64=True))
    case('elf64', got=gate_names.check([g64], [c]), want=(uni, red))
    # nothing wrong: only certified items
    ok_rows = sympair(('_Z1fv', '?f@@YAXXZ', 'func', 'def'), ('_ZN1AC1Ev', '??0A@@QAE@XZ', 'ctor.c1', 'def'), ('_ZN1AC2Ev', '??0A@@QAE@XZ', 'ctor.c2', 'def'))
    gok = os.path.join(tmp, 'gok')
    synth.write(gok, 'u.o', gcc_obj(ok_rows))
    case('all certified', got=gate_names.check([gok], [c]), want=(3, []))
    # one character off is red and named; a definition in another COMDAT of the same cl object does not make it right
    cbad = os.path.join(tmp, 'cbad')
    synth.write(cbad, 'u.obj', cl_obj(defined=('?f@@YAXXY', '??0A@@QAE@XZ')))
    case('wrong name', got=gate_names.check([gok], [cbad]), want=(3, ['u:_Z1fv: ?f@@YAXXZ is not a definition of the cl object']))
    # an undefined (referenced) name of cl is no definition
    cundef = os.path.join(tmp, 'cundef')
    synth.write(cundef, 'u.obj', cl_obj(defined=('??0A@@QAE@XZ',), undef=('?f@@YAXXZ',)))
    case('undefined in cl', got=gate_names.check([gok], [cundef])[1], want=['u:_Z1fv: ?f@@YAXXZ is not a definition of the cl object'])
    # no cl object for the unit / unreadable cl object: every judged item red, the universe is the same
    cnone = os.path.join(tmp, 'cnone')
    os.makedirs(cnone)
    uni2, red2 = gate_names.check([gok], [cnone])
    case('no cl object', got=(uni2, red2), want=(3, ['u:_Z1fv: no cl object for the unit', 'u:_ZN1AC1Ev: no cl object for the unit', 'u:_ZN1AC2Ev: no cl object for the unit']))
    cjunk = os.path.join(tmp, 'cjunk')
    synth.write(cjunk, 'u.obj', b'not a coff object at all, no.')
    uni3, red3 = gate_names.check([gok], [cjunk])
    case('unreadable cl universe', got=uni3, want=3)
    case('unreadable cl red', got=[r.split(': the cl object is unreadable')[0] for r in red3], want=['u:_Z1fv', 'u:_ZN1AC1Ev', 'u:_ZN1AC2Ev'])
    # unreadable GCC objects: one error item, never a pass
    for what, data in (('junk', b'\x7fELF junk'), ('bad row', gcc_obj(b'only\tthree\tfields\n')),
                       ('no final LF', gcc_obj(b'a\tb\tfunc\tdef')), ('bad kind', gcc_obj(sympair(('a', 'b', 'frobnicate', 'def')))),
                       ('no symtab', synth.elf([('.fnvnx.sympair', ok_rows, 0), ('.text', b'\0', 6)], None)),
                       ('bad vtpair', gcc_obj(ok_rows, b'a\tx\tb\t0\n'))):
        d = os.path.join(tmp, 'e_' + what.replace(' ', '_'))
        synth.write(d, 'u.o', data)
        u, r = gate_names.check([d], [c])
        case('error ', got=(u, len(r), r[0].startswith('u:error: ')), want=(1, 1, True), key=what)
    # a cut-off object is an error, never a pass and never an exception: every proper prefix of the GCC object and of the cl object
    gfull, cfull = gcc_obj(ok_rows), cl_obj()
    for n in range(0, len(gfull)):
        d = os.path.join(tmp, 'cut_g')
        shutil.rmtree(d, ignore_errors=True)
        synth.write(d, 'u.o', gfull[:n])
        u, r = gate_names.check([d], [c])
        if not (u == 1 and len(r) == 1 and r[0].startswith('u:error: ')):
            fails.append('GCC object cut at %d: %r' % (n, (u, r)))
            break
    for n in range(0, len(cfull)):
        d = os.path.join(tmp, 'cut_c')
        shutil.rmtree(d, ignore_errors=True)
        synth.write(d, 'u.obj', cfull[:n])
        u, r = gate_names.check([gok], [d])
        if not (u == 3 and len(r) == 3):
            fails.append('cl object cut at %d: %r' % (n, (u, r)))
            break
    # an object with none of the plugin's sections: empty universe
    gempty = os.path.join(tmp, 'gempty')
    synth.write(gempty, 'u.o', synth.elf([('.text', b'\0', 6)], [('main', 1, 2, 1, 0)]))
    case('empty universe', got=gate_names.check([gempty], [c]), want=(0, []))
    # a function defined in a COMDAT group, weak, or only referenced is not judged even if cl lacks the name
    cempty = os.path.join(tmp, 'cempty')
    synth.write(cempty, 'u.obj', cl_obj(defined=(), data=(), undef=()))
    case('outside the universe', got=gate_names.check([g], [cempty])[1][0], want='u:_Z1fv: ?f@@YAXXZ is not a definition of the cl object')
    case('outside the universe 2', got=[r for r in gate_names.check([g], [cempty])[1] if '_Z1hv' in r or '_Z1iv' in r or '_Z1jv' in r], want=[])
    # --units: u0.obj is the unit of source a/u.cpp, whose GCC object is a_u.cpp.o
    gu, cu = os.path.join(tmp, 'gu'), os.path.join(tmp, 'cu')
    synth.write(gu, 'a_u.cpp.o', gcc_obj(ok_rows))
    synth.write(cu, 'u0.obj', cl_obj())
    units = synth.write(tmp, 'units.json', json.dumps({'units': [{'source': 'a/u.cpp'}]}).encode())
    case('units pairing', got=gate_names.check([gu], [cu], units), want=(3, []))
    case('no units: stems differ', got=gate_names.check([gu], [cu])[1][0].endswith('no cl object for the unit'), want=True)
    # a source listed by two units: the cl side is the set u0.obj, u1.obj; one cl object that defines the name proves it
    units2 = synth.write(tmp, 'units2.json', json.dumps({'units': [{'source': 'a/u.cpp'}, {'source': 'a/u.cpp'}]}).encode())
    cm = os.path.join(tmp, 'cm')
    synth.write(cm, 'u0.obj', cl_obj(defined=(), data=(), undef=()))
    synth.write(cm, 'u1.obj', cl_obj())
    case('two cl objects, the second defines', got=gate_names.check([gu], [cm], units2), want=(3, []))
    cm2 = os.path.join(tmp, 'cm2')
    synth.write(cm2, 'u0.obj', cl_obj(defined=(), data=(), undef=()))
    synth.write(cm2, 'u1.obj', cl_obj(defined=('??0A@@QAE@XZ',)))
    case('two cl objects, none defines f (reason)', got=gate_names.check([gu], [cm2], units2)[1][0], want='a_u.cpp:_Z1fv: ?f@@YAXXZ is not a definition of the cl object')
    cm3 = os.path.join(tmp, 'cm3')
    synth.write(cm3, 'u0.obj', b'junk')
    synth.write(cm3, 'u1.obj', cl_obj())
    case('an unreadable one does not hide a proof', got=gate_names.check([gu], [cm3], units2), want=(3, []))
    cm4 = os.path.join(tmp, 'cm4')
    synth.write(cm4, 'u0.obj', b'junk')
    synth.write(cm4, 'u1.obj', cl_obj(defined=('??0A@@QAE@XZ',)))
    case('unreadable and no proof', got=gate_names.check([gu], [cm4], units2)[1],
          want=['a_u.cpp:_Z1fv: ?f@@YAXXZ is not a definition of the cl object (a cl object of the unit is unreadable)'])
    case('pairs: the set, sorted by N', got=[os.path.basename(p) for p in unitpairs.pairs([gu], [cm], units2)[0][2]], want=['u0.obj', 'u1.obj'])
    # ELF section names are not unique: two .group sections and one .fnvnx.sympair read fine; a doubly present asked section is an error
    two = synth.elf([('.group', b'\0' * 8, 0), ('.group', b'\0' * 8, 0), ('.fnvnx.sympair', ok_rows, 0), ('.text', b'\0' * 4, 6),
                     ('.data', b'\0' * 4, 3)], [('_Z1fv', 1, 2, 4, 0), ('_ZN1AC1Ev', 1, 2, 4, 0), ('_ZN1AC2Ev', 1, 2, 4, 0)])
    d2 = os.path.join(tmp, 'g2')
    synth.write(d2, 'u.o', two)
    case('two .group sections', got=gate_names.check([d2], [c]), want=(3, []))
    dd = os.path.join(tmp, 'gdd')
    synth.write(dd, 'u.o', synth.elf([('.fnvnx.sympair', ok_rows, 0), ('.fnvnx.sympair', ok_rows, 0), ('.text', b'\0', 6)], [('_Z1fv', 1, 2, 3, 0)]))
    u, r = gate_names.check([dd], [c])
    case('doubly present .fnvnx.sympair', got=(u, len(r), r[0].startswith('u:error: ') and '2 sections are named .fnvnx.sympair' in r[0]), want=(1, 1, True))

    cu2 = os.path.join(tmp, 'cu2')
    synth.write(cu2, 'u7.obj', cl_obj())
    try:
        gate_names.check([gu], [cu2], units)
        fails.append('u7 outside the units list: no error')
    except unitpairs.GateInputError:
        pass

    # rows(): file lists, one row per item, (stem, symbol, msvc name, reason); '' = certified
    rs = gate_names.rows([os.path.join(g, 'u.o')], [os.path.join(c, 'u.obj')])
    case('rows count', got=len(rs), want=7)
    case('rows sorted', got=rs, want=sorted(rs))
    case('rows certified', got=[r for r in rs if not r[3]], want=[('u', '_Z1fv', '?f@@YAXXZ', ''), ('u', '_ZN1AC1Ev', '??0A@@QAE@XZ', ''), ('u', '_ZN1AC2Ev', '??0A@@QAE@XZ', ''), ('u', 'gdata', '?gdata@@3HA', '')])
    case('rows red', got=sorted((r[1], r[3]) for r in rs if r[3]), want=[('_Z1gv', '?g@@YAXXZ is not a definition of the cl object'), ('_Z1kv', 'unnamed'), ('_ZTV1B', 'vftable-unnamed')])
    case('rows error', got=gate_names.rows([os.path.join(tmp, 'e_junk', 'u.o')], [os.path.join(c, 'u.obj')])[0][:3] + ('',), want=('u', '', '', ''))
    case('rows error reason', got=gate_names.rows([os.path.join(tmp, 'e_junk', 'u.o')], [os.path.join(c, 'u.obj')])[0][3].startswith('error: '), want=True)
    try:
        gate_names.rows([os.path.join(tmp, 'missing.o')], [])
        fails.append('rows: missing file is no error')
    except unitpairs.GateInputError:
        pass

    # the gate file and the exit status (a separate process, as the verdict runs it)
    def run(args, out):
        os.makedirs(out, exist_ok=True)
        p = subprocess.run([sys.executable, '-I', os.path.join(HERE, '..', 'gate_names.py')] + args, env={'GATE_OUT': out}, capture_output=True, text=True)
        doc = None
        if os.path.exists(os.path.join(out, 'names.json')):
            with open(os.path.join(out, 'names.json')) as fh:
                doc = json.load(fh)
        return p.returncode, doc

    rc, doc = run(['--gcc', g, '--cl', c], os.path.join(tmp, 'o1'))
    case('red exit', got=rc, want=1)
    case('red file', got=(doc['format'], doc['name'], doc['status'], doc['universe'], doc['certified'], len(doc['uncertified'])), want=('fnvnx-gate/1', 'names', 'red', 7, 4, 3))
    with open(os.path.join(tmp, 'o1', 'names.ids')) as fh:
        case('ids file', got=fh.read().split('\n')[:-1], want=sorted(doc['uncertified']))
    rc, doc = run(['--gcc', gok, '--cl', c], os.path.join(tmp, 'o2'))
    case('green', got=(rc, doc['status'], doc['universe'], doc['certified'], doc['uncertified']), want=(0, 'green', 3, 3, []))
    rc, doc = run(['--gcc', gempty, '--cl', c], os.path.join(tmp, 'o3'))
    case('green empty', got=(rc, doc['status'], doc['universe'], doc['certified']), want=(0, 'green', 0, 0))
    rc, doc = run(['--gcc', os.path.join(tmp, 'missing'), '--cl', c], os.path.join(tmp, 'o4'))
    case('missing input', got=(rc, doc['status'], doc['universe'], doc['certified'], doc['uncertified'][0][:6]), want=(1, 'red', 1, 0, 'error:'))
    rc, doc = run(['--gcc', g, '--cl', c, '--units', os.path.join(tmp, 'nope.json')], os.path.join(tmp, 'o5'))
    case('missing units file', got=(rc, doc['status']), want=(1, 'red'))
    # deterministic: the same inputs give the same file
    run(['--gcc', g, '--cl', c], os.path.join(tmp, 'o6'))
    with open(os.path.join(tmp, 'o1', 'names.json'), 'rb') as a, open(os.path.join(tmp, 'o6', 'names.json'), 'rb') as b:
        case('deterministic', got=a.read(), want=b.read())
finally:
    shutil.rmtree(tmp, ignore_errors=True)

if fails:
    print('\n'.join(fails))
    sys.exit(1)
print('ok')
