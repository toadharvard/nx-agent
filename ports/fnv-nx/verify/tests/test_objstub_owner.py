#!/usr/bin/env python3
"""Rule O of verify/objstub.py (owner-decision citation), judged on synthetic analysis results: no compiler, no game data.
Cases: a cited body in the anchor's root is accepted; an unknown id; a citation outside the anchor's root; an uncited body keeps its finding; two records;
the anchor reader (missing file, malformed, duplicate)."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
import objstub  # noqa: E402

fails = []


def check(what, ok, got=''):
    if not ok:
        fails.append('%s: %s' % (what, got))


def res(cite):
    return {'items': ['f'], 'finds': [('f', 'V', 'result x0=K0', None)], 'cite': cite, 'wine': [], 'wine_id': {}, 'wine_nz': {}, 'cite_sym': {}, 'terms': [], 'term_def': [],
            'nr': {}, 'tb': {}, 'exit_ok': [], 'reach': [], 'assume': [], 'unw': [], 'term_def_': [], 'alias': {}, 'globals': [], 'dref': [], 'flag': None, 'tabs': {}}


OWN = {'gl-shader-failure': ('GL shader compile/link failure', 'ports/fnv-nx/render/gl4')}
IN, OUT = 'ports/fnv-nx/render/gl4/gl4.o', 'ports/fnv-nx/render/other/x.o'


def kinds(oid, cite, owners=OWN, stats=None):
    items, finds = objstub.judge({oid: res(cite)}, {}, None, owners=owners, stats=stats)
    return sorted(k for _, _, k, _ in finds)


st = {}
check('cited body in the anchor root: accepted', kinds(IN, {'f': ['od:gl-shader-failure']}, stats=st) == [] and st.get('owner_decision') == 1, st)
check('unknown id', kinds(IN, {'f': ['od:no-such']}) == ['owner-decision-unknown-id'])
check('no anchors at all', kinds(IN, {'f': ['od:gl-shader-failure']}, owners={}) == ['owner-decision-unknown-id'])
check('citation outside the root', kinds(OUT, {'f': ['od:gl-shader-failure']}) == ['owner-decision-wrong-root'])
check('a root that merely shares a prefix is outside', kinds('ports/fnv-nx/render/gl4x/a.o', {'f': ['od:gl-shader-failure']}) == ['owner-decision-wrong-root'])
check('uncited body keeps its finding', kinds(IN, {}) == ['V'])
check('two records for one body', kinds(IN, {'f': ['od:gl-shader-failure', 'dm:31']}) == ['cite-mismatch'])

d = tempfile.mkdtemp(prefix='objstub-owner-')
p = os.path.join(d, 'CLAUDE.md')
check('missing CLAUDE.md: no anchors', objstub.read_owner_decisions(p) == {})
open(p, 'w').write('text\n<!-- fnvnx-owner-decision: gl-shader-failure | class: GL shader compile/link failure | root: ports/fnv-nx/render/gl4 -->\nmore\n')
check('anchor read', objstub.read_owner_decisions(p) == OWN, objstub.read_owner_decisions(p))
for what, body in (('malformed', '<!-- fnvnx-owner-decision: Bad_Id | class: c | root: r -->\n'), ('no class', '<!-- fnvnx-owner-decision: a | class:  | root: r -->\n'),
                   ('duplicate', '<!-- fnvnx-owner-decision: a | class: c | root: r -->\n<!-- fnvnx-owner-decision: a | class: c | root: r -->\n')):
    open(p, 'w').write(body)
    try:
        objstub.read_owner_decisions(p)
        check('anchor ' + what + ' is an error', False)
    except ValueError:
        pass
open(p, 'w').write('the marker fnvnx-owner-decision: x is prose\n')
check('prose mention is no anchor', objstub.read_owner_decisions(p) == {})
if fails:
    print('FAIL', *fails, sep='\n  ')
    sys.exit(1)
print('ok: rule O (owner-decision citation)')
