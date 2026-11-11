#!/usr/bin/env python3
"""Check of link/gate_names.py on REAL GCC objects (fixtures/names_basic.cc built by the one build: msvcmodel compiler + all plugins; see realobj.py)
and, for the cl side only, hand-built COFF objects (blocked: cl toolchain as recipe external, L1).  Negative GCC-side cases are byte mutations of the
real object (mutate.py: located by parsing with elfobj, never by fixed offsets).  No game data, no cl, no network.
A definition whose name cl does not define is red and named; an unnamed row, an unnamed vftable, a unit without a cl object, an unreadable cl
object and an unreadable GCC object are red; weak, COMDAT and reference rows are outside the universe; an empty universe is green; the gate file and
the exit status; the `u<N>` pairing of --units.  Run: python3 -I <this file>."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..'))
import elfobj
import gate_names
import mutate
import realobj
import synth
import unitpairs

fails = []


def check(what, got, want):
    if got != want:
        fails.append('%s: got %r, want %r' % (what, got, want))


G0 = open(realobj.objects(['names_basic'])['names_basic'], 'rb').read()      # the real object
E0 = elfobj.parse(G0, 'G0')
ROWS0 = [l.split('\t') for l in E0.section('.fnvnx.sympair').decode().split('\n') if l]
MSVC = sorted({r[1] for r in ROWS0 if r[1] and r[3] == 'def' and r[2] in ('func', 'data') or r[2].startswith(('ctor', 'dtor'))} - {''})
UNIT = 'names_basic.cc'
# the items of the gate's universe in this object: the strong definitions that carry a name (sorted by symbol)
ITEMS = ['_Z2f1v', '_Z2f2i', '_ZN1A1fEv', '_ZN1AC1Ev', '_ZN1AC2Ev', '_ZN1AD1Ev', '_ZN1AD2Ev', 'g_data']
check('the real object has the fixture\'s definitions', sorted(r[0] for r in ROWS0 if r[3] == 'def' and r[1] and not r[1].startswith('?_7')), sorted(ITEMS))
check('the real object has COMDAT groups (several .group sections)', len(E0.by_name['.group']) >= 2, True)


def edit_section(data, name, fn):
    """text of a plugin section -> rows (split by tab) -> fn -> text, written back by mutate.replace_section"""
    rows = [l.split('\t') for l in elfobj.parse(data, 'x').section(name).decode().split('\n') if l]
    rows = fn(rows)
    return mutate.replace_section(data, name, ''.join('\t'.join(r) + '\n' for r in rows).encode())


def cl_obj(defined=tuple(MSVC), data=(), undef=()):
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
UNITS = synth.write(tmp, 'units.json', json.dumps({'units': [{'source': UNIT}]}).encode())


def put(name, gdata, cdata=None):
    """a GCC dir with the unit's object and a cl dir with u0.obj (the --units pairing of the verdict)"""
    g, c = os.path.join(tmp, name, 'g'), os.path.join(tmp, name, 'c')
    synth.write(g, UNIT + '.o', gdata)
    if cdata is None:
        os.makedirs(c, exist_ok=True)
    else:
        synth.write(c, 'u0.obj', cdata)
    return g, c


def chk(g, c):
    return gate_names.check([g], [c], UNITS)


try:
    g, c = put('ok', G0, cl_obj())
    check('all certified', chk(g, c), (8, []))
    # nothing certified by luck: cl defines one name fewer, one name off by a character, one name only referenced
    g, c = put('wrong', G0, cl_obj(defined=[n if n != '?f1@@YAXXZ' else '?f1@@YAXXY' for n in MSVC]))
    check('wrong name', chk(g, c), (8, ['%s:_Z2f1v: ?f1@@YAXXZ is not a definition of the cl object' % UNIT]))
    g, c = put('undef', G0, cl_obj(defined=[n for n in MSVC if n != '?f1@@YAXXZ'], undef=['?f1@@YAXXZ']))
    check('undefined in cl', chk(g, c)[1], ['%s:_Z2f1v: ?f1@@YAXXZ is not a definition of the cl object' % UNIT])
    # no cl object for the unit / unreadable cl object: every judged item red, the universe is the same
    g, c = put('cnone', G0)
    uni2, red2 = chk(g, c)
    check('no cl object', (uni2, red2), (8, sorted('%s:%s: no cl object for the unit' % (UNIT, i) for i in ITEMS)))
    g, c = put('cjunk', G0, b'not a coff object at all, no.')
    uni3, red3 = chk(g, c)
    check('unreadable cl universe', uni3, 8)
    check('unreadable cl red', [r.split(': the cl object is unreadable')[0] for r in red3], sorted('%s:%s' % (UNIT, i) for i in ITEMS))
    # an unnamed row and an unnamed vftable: the mutated rows of the real object are red as such
    unn = edit_section(G0, '.fnvnx.sympair', lambda rs: [[r[0], '', 'unnamed:no rule', r[3]] if r[0] == '_Z2f2i' else r for r in rs])
    unn = edit_section(unn, '.fnvnx.vtpair', lambda rs: [[r[0], r[1], 'unnamed:no rule', r[3]] for r in rs])
    g, c = put('unnamed', unn, cl_obj())
    uni, red = chk(g, c)
    check('unnamed row and unnamed vftable', (uni, red), (uni, ['%s:_Z2f2i:unnamed' % UNIT, '%s:_ZTV1A:vftable-unnamed' % UNIT]))
    check('unnamed: the universe counts both', uni, 9)

    # unreadable GCC objects: one error item, never a pass (every one a mutation of the real object)
    cases = (('truncated header', mutate.truncate(G0, 20)),
             ('bad magic', b'\x7fELX' + G0[4:]),
             ('bad row', edit_section(G0, '.fnvnx.sympair', lambda rs: [rs[0][:3]] + rs[1:])),
             ('no final LF', mutate.replace_section(G0, '.fnvnx.sympair', E0.section('.fnvnx.sympair')[:-1])),
             ('bad kind', edit_section(G0, '.fnvnx.sympair', lambda rs: [[rs[0][0], rs[0][1], 'frobnicate', rs[0][3]]] + rs[1:])),
             ('no symtab', mutate.drop_symtab(G0)),
             ('bad vtpair', mutate.replace_section(G0, '.fnvnx.vtpair', b'a\tx\tb\t0\n')))
    for what, data in cases:
        g, c = put('e_' + what.replace(' ', '_'), data, cl_obj())
        u, r = chk(g, c)
        check('error ' + what, (u, len(r), r[0].startswith('%s:error: ' % UNIT)), (1, 1, True))
    # an object cut anywhere is an error, never a pass and never an exception: every proper prefix of the real object, and of the cl object
    for n in range(0, len(G0)):
        g, c = put('cut_g', G0[:n], cl_obj())
        u, r = chk(g, c)
        if not (u == 1 and len(r) == 1 and r[0].startswith('%s:error: ' % UNIT)):
            fails.append('GCC object cut at %d: %r' % (n, (u, r)))
            break
        shutil.rmtree(os.path.join(tmp, 'cut_g'))
    gok, _ = put('okg', G0, cl_obj())
    cfull = cl_obj()
    for n in range(0, len(cfull)):
        g, c = put('cut_c', G0, cfull[:n])
        u, r = chk(g, c)
        if not (u == 8 and len(r) == 8):
            fails.append('cl object cut at %d: %r' % (n, (u, r)))
            break
        shutil.rmtree(os.path.join(tmp, 'cut_c'))
    # an object with none of the plugin's sections: empty universe
    bare = G0
    for s in ('.fnvnx.sympair', '.fnvnx.vtpair'):
        bare = mutate.rename_section(bare, s, '.fnvnx.' + 'x' * (len(s) - 7))
    g, c = put('empty', bare, cl_obj())
    check('empty universe', chk(g, c), (0, []))
    # a cl object that defines nothing: the universe is the same; weak/COMDAT/reference rows never appear
    g, c = put('cempty', G0, cl_obj(defined=()))
    red = chk(g, c)[1]
    check('outside the universe', sorted(red), sorted('%s:%s: %s is not a definition of the cl object' % (UNIT, r[0], r[1]) for r in ROWS0 if r[0] in ITEMS))
    check('outside the universe 2', [r for r in red if '_ZTV' in r or '_ZTS' in r or '_ZTI' in r], [])
    g, c = put('dd', mutate.duplicate_section(G0, '.fnvnx.sympair'), cl_obj())
    u, r = chk(g, c)
    check('doubly present .fnvnx.sympair', (u, len(r), r[0].startswith('%s:error: ' % UNIT) and '2 sections are named .fnvnx.sympair' in r[0]), (1, 1, True))

    # --units: the cl object u<N>.obj is the unit's N-th source; without --units the stems differ
    g, c = put('nounits', G0, cl_obj())
    check('no units: stems differ', gate_names.check([g], [c])[1][0].endswith('no cl object for the unit'), True)
    units2 = synth.write(tmp, 'units2.json', json.dumps({'units': [{'source': UNIT}, {'source': UNIT}]}).encode())
    g, c = put('cm', G0)
    synth.write(c, 'u0.obj', cl_obj(defined=()))
    synth.write(c, 'u1.obj', cl_obj())
    check('two cl objects, the second defines', gate_names.check([g], [c], units2), (8, []))
    g, c = put('cm2', G0)
    synth.write(c, 'u0.obj', cl_obj(defined=()))
    synth.write(c, 'u1.obj', cl_obj(defined=[n for n in MSVC if n != '?f1@@YAXXZ']))
    check('two cl objects, none defines f1 (reason)', gate_names.check([g], [c], units2)[1],
          ['%s:_Z2f1v: ?f1@@YAXXZ is not a definition of the cl object' % UNIT])
    g, c = put('cm3', G0)
    synth.write(c, 'u0.obj', b'junk')
    synth.write(c, 'u1.obj', cl_obj())
    check('an unreadable one does not hide a proof', gate_names.check([g], [c], units2), (8, []))
    g, c = put('cm4', G0)
    synth.write(c, 'u0.obj', b'junk')
    synth.write(c, 'u1.obj', cl_obj(defined=[n for n in MSVC if n != '?f1@@YAXXZ']))
    r4 = gate_names.check([g], [c], units2)[1]
    check('unreadable and no proof', r4, ['%s:_Z2f1v: ?f1@@YAXXZ is not a definition of the cl object (a cl object of the unit is unreadable)' % UNIT])
    check('pairs: the set, sorted by N', [os.path.basename(p) for p in unitpairs.pairs([g], [c], units2)[0][2]], ['u0.obj', 'u1.obj'])
    g, c = put('cu2', G0, cl_obj())
    synth.write(c, 'u7.obj', cl_obj())
    try:
        gate_names.check([g], [c], UNITS)
        fails.append('u7 outside the units list: no error')
    except unitpairs.GateInputError:
        pass

    # rows(): one row per item, (stem, symbol, msvc name, reason); '' = certified
    g, c = put('rows', unn, cl_obj(defined=[n for n in MSVC if n != '?f1@@YAXXZ']))
    rs = gate_names.rows([os.path.join(g, UNIT + '.o')], [os.path.join(c, 'u0.obj')], UNITS)
    check('rows count', len(rs), 9)
    check('rows sorted', rs, sorted(rs))
    check('rows certified', [r[1] for r in rs if not r[3]], sorted(i for i in ITEMS if i not in ('_Z2f1v', '_Z2f2i')))
    check('rows red', sorted((r[1], r[3]) for r in rs if r[3]), [('_Z2f1v', '?f1@@YAXXZ is not a definition of the cl object'), ('_Z2f2i', 'unnamed'), ('_ZTV1A', 'vftable-unnamed')])
    ge, _ = put('rerr', mutate.truncate(G0, 20), cl_obj())
    er = gate_names.rows([os.path.join(ge, UNIT + '.o')], [], None)
    check('rows error', er[0][:3] + ('',), (UNIT, '', '', ''))
    check('rows error reason', er[0][3].startswith('error: '), True)
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

    gg, cc = put('cli', unn, cl_obj(defined=[n for n in MSVC if n != '?f1@@YAXXZ']))
    rc, doc = run(['--gcc', gg, '--cl', cc, '--units', UNITS], os.path.join(tmp, 'o1'))
    check('red exit', rc, 1)
    check('red file', (doc['format'], doc['name'], doc['status'], doc['universe'], doc['certified'], len(doc['uncertified'])), ('fnvnx-gate/1', 'names', 'red', 9, 6, 3))
    with open(os.path.join(tmp, 'o1', 'names.ids')) as fh:
        check('ids file', fh.read().split('\n')[:-1], sorted(doc['uncertified']))
    g, c = put('cli2', G0, cl_obj())
    rc, doc = run(['--gcc', g, '--cl', c, '--units', UNITS], os.path.join(tmp, 'o2'))
    check('green', (rc, doc['status'], doc['universe'], doc['certified'], doc['uncertified']), (0, 'green', 8, 8, []))
    g, c = put('cli3', bare, cl_obj())
    rc, doc = run(['--gcc', g, '--cl', c, '--units', UNITS], os.path.join(tmp, 'o3'))
    check('green empty', (rc, doc['status'], doc['universe'], doc['certified']), (0, 'green', 0, 0))
    rc, doc = run(['--gcc', os.path.join(tmp, 'missing'), '--cl', cc], os.path.join(tmp, 'o4'))
    check('missing input', (rc, doc['status'], doc['universe'], doc['certified'], doc['uncertified'][0][:6]), (1, 'red', 1, 0, 'error:'))
    rc, doc = run(['--gcc', gg, '--cl', cc, '--units', os.path.join(tmp, 'nope.json')], os.path.join(tmp, 'o5'))
    check('missing units file', (rc, doc['status']), (1, 'red'))
    run(['--gcc', gg, '--cl', cc, '--units', UNITS], os.path.join(tmp, 'o6'))
    with open(os.path.join(tmp, 'o1', 'names.json'), 'rb') as a, open(os.path.join(tmp, 'o6', 'names.json'), 'rb') as b:
        check('deterministic', a.read(), b.read())
finally:
    shutil.rmtree(tmp, ignore_errors=True)

if fails:
    print('\n'.join(fails))
    sys.exit(1)
print('ok')
