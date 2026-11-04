#!/usr/bin/env python3
"""test_s13_tools.py - V1 (D14) against seven S13 / S13b / S6b merges. Synthetic inputs only (made-up records, MSVC
names, vtable words, tree hashes and hand-written shader tokens); nothing from the decomp, the game or MSVC.

    python3 -I tests/v1/test_s13_tools.py [--shaders-build DIR]   -> one line per case, exit 1 on a DEFECT

  E1  69a378e  causes.py run as a script (from another cwd): summary --effects and handoffs --effects reach the rules
  H1  ab7e255  handoffs: rows per owned cause == summary divergences; index sums; by-design none; non-ok, gone dropped
  O1  62b7ab4  pyelftools made unimportable (sys.modules['elftools'] = None): CLI warns once, summary says unavailable
  O2  62b7ab4  note: merge (the shards' totals) carries no native_objects; no object dir at all gives no warning
  V1  61fd136  Model.native's short set == pairs()'s short: n < s short, n = s / s + 1 exact, padding not short
  V2  61fd136  note: native_slots counts a following secondary's vcall/vbase offset words as slots of the vtable
  R1  545e0c5  a def row of unit A changes the callee convention a caller unit B uses, but B's key is not refreshed
  R2  545e0c5  a changed row refreshes its unit's keys; an unrelated unit's key is unchanged; a ref row stays local
  F1  a01a297  the link records tree hash H; only t/<L12> exists, with another hash: the fallback wait takes it
  F2  a01a297  note: a link whose meta.json records no tree hash takes the objects commit's tree unchecked
  F3  a01a297  control: the recorded tree's commit, hash matching, is taken without waiting
  K1  283fe49  census.py: programs = translated + failed, cause table sums to failed, unique counted once
  K2  283fe49  note: census's own package reader counts a package the runtime parser (fnvnx_sdp_parse) rejects
"""
import argparse
import hashlib
import importlib
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))
DF = os.path.join(PORT, 'tools', 'difffuzz')
WORK = tempfile.mkdtemp(prefix='v1-s13tools-')     # raises when it cannot: never an empty or relative path
os.environ['FNVNX_DFWORK'] = WORK
os.environ['FNVNX_DFLINK'] = os.path.join(WORK, 'nolink')
sys.path.insert(0, DF)

DEFECTS = []


def ok(cid, what):
    print('ok     %s %s' % (cid, what))


def defect(cid, what):
    print('DEFECT %s %s' % (cid, what))
    DEFECTS.append(cid)


def note(cid, what):
    print('NOTE   %s %s' % (cid, what))


def expect(cid, cond, what):
    (ok if cond else defect)(cid, what)


def w(*vals):
    return ''.join('%08x' % (v & 0xffffffff) for v in vals)


def rec(div, cause='unclassified', addr='00401000', source='src/t.cpp'):
    return dict(source=source, address=addr, symbol='?f@@YAHH@Z', conv='int f(int)', divergence=div,
                hint=dict(cause=cause), case={})


def campaign(root, name, rows, recs, recheck=None):
    d = os.path.join(root, name)
    os.makedirs(os.path.join(d, 'div'))
    open(os.path.join(d, 'results.jsonl'), 'w').write(''.join(json.dumps(r) + '\n' for r in rows))
    for n, r in recs.items():
        json.dump(r, open(os.path.join(d, 'div', n), 'w'))
    if recheck:
        open(os.path.join(d, 'recheck.jsonl'), 'w').write(''.join(json.dumps(x) + '\n' for x in recheck))
    return d


def run_causes(args, cwd, env_extra=None, no_elftools=False):
    """causes.py as a script, in its own process, from `cwd` (not tools/difffuzz)."""
    tool = os.path.join(DF, 'causes.py')
    env = dict(os.environ, **(env_extra or {}))
    if no_elftools:
        shim = os.path.join(WORK, 'shim_%d.py' % os.getpid())
        open(shim, 'w').write("import runpy, sys\nsys.modules['elftools'] = None\nsys.argv = sys.argv[1:]\n"
                              "sys.path.insert(0, %r)\nrunpy.run_path(sys.argv[0], run_name='__main__')\n" % DF)
        cmd = [sys.executable, shim, tool] + args
    else:
        cmd = [sys.executable, tool] + args
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env)


# ------------------------------------------------------------------------------------------------ causes.py (S13b)

def case_e1():
    root = tempfile.mkdtemp(dir=WORK)
    d = campaign(root, '1of1', [dict(address='00401000', source='src/t.cpp', status='ok', size=16)],
                 {'00401000-0.json': rec(dict(kind='mem-write', addr='03000010', x86='01000000', arm='02000000'))})
    eff = os.path.join(root, 'eff.jsonl')
    open(eff, 'w').write(json.dumps(dict(file='elsewhere/00401000-0.json', first='width')) + '\n')
    out = os.path.join(root, 'c.json')
    r = run_causes(['summary', d, '--effects', eff, '--out', out], cwd=root, env_extra=dict(FNVNX_CAUSES_OBJ=root))
    c = json.load(open(out))['causes'] if r.returncode == 0 else {}
    expect('E1', 'field-width' in c and 'unclassified' not in c,
           'script summary --effects from another cwd: %s (rc %d)' % (sorted(c), r.returncode))
    ho = os.path.join(root, 'ho')
    r = run_causes(['handoffs', d, '--out-dir', ho, '--effects', eff], cwd=root, env_extra=dict(FNVNX_CAUSES_OBJ=root))
    files = sorted(os.listdir(ho)) if os.path.isdir(ho) else []
    rows = [l.rstrip('\n').split('\t') for l in open(os.path.join(ho, 'handoffs_S1.tsv'))][1:] \
        if 'handoffs_S1.tsv' in files else []
    expect('E1', rows and rows[0][1] == 'field-width' and rows[0][5] == 'width',
           'script handoffs --effects: the record is field-width (S1) with its first effect (%s, %s)' % (files, rows))


def case_h1():
    import causes
    root = tempfile.mkdtemp(dir=WORK)
    rows = [dict(address=a, source='src/t.cpp', status='ok', size=64)
            for a in ('00401000', '00402000', '00403000', '00405000', '00406000')]
    rows.append(dict(address='00404000', source='src/t.cpp', status='setup:KeyError'))
    recs = {'00401000-0.json': rec(dict(kind='error', x86=None, arm='exc:1@0')),
            '00401000-1.json': rec(dict(kind='return', x86=w(1), arm=w(2)), 'bool-repr'),
            '00401000-2.json': rec(dict(kind='return', x86=w(1), arm=w(3)), 'native-trap'),
            '00402000-0.json': rec(dict(kind='return', x86=w(1), arm=w(2)), addr='00402000'),
            '00403000-0.json': rec(dict(kind='return', x86=w(1), arm=w(2)), 'local-static', addr='00403000'),
            '00404000-0.json': rec(dict(kind='error', x86=None, arm='exc:1@0'), addr='00404000'),
            '00405000-0.json': rec(dict(kind='return', x86=w(1), arm=w(2)), 'some-new-cause', addr='00405000'),
            '00406000-0.json': rec(dict(kind='return', x86=w(1), arm=w(2)), 'native-trap', addr='00406000')}
    d1 = campaign(root, '1of2', rows[:3] + rows[5:], {k: v for k, v in recs.items() if k[:4] != '0040' or
                                                      k[4] in '1234'},
                  recheck=[dict(file='00403000-0.json', status='gone')])
    d2 = campaign(root, '2of2', rows[3:5], {k: v for k, v in recs.items() if k[4] in '56'})
    saved = dict(causes.EFFECTS)
    try:
        causes.EFFECTS.clear()
        summ = causes.summarize([d1, d2])
        ho = os.path.join(root, 'ho')
        index = causes.write_handoffs([d1, d2], ho)
    finally:
        causes.EFFECTS.clear()
        causes.EFFECTS.update(saved)
    want = {c: r['divergences'] for c, r in summ['causes'].items() if r['verdict'] != 'by-design'}
    got = {}
    nrows = 0
    for fn in os.listdir(ho):
        if fn == 'index.tsv':
            continue
        lines = [l.rstrip('\n').split('\t') for l in open(os.path.join(ho, fn))][1:]
        nrows += len(lines)
        for l in lines:
            got[l[1]] = got.get(l[1], 0) + 1
            if causes.policy(l[1])[0] == 'by-design':
                defect('H1', 'a by-design cause in %s' % fn)
    expect('H1', got == want, 'rows per cause == summary divergences of every owned non-by-design cause '
                              '(handoffs %s, summary %s)' % (sorted(got.items()), sorted(want.items())))
    expect('H1', sum(r[3] for r in index) == nrows and '00404000' not in json.dumps(got),
           'index rows sum to the files\' rows (%d); the setup function and the gone replay are not listed' % nrows)
    expect('H1', ('triage', 'S13b') in {(r[1], r[2]) for r in index},
           'a cause outside the policy table goes to triage S13b (not dropped)')


def case_o1():
    root = tempfile.mkdtemp(dir=WORK)
    obj = os.path.join(root, 'obj')
    os.makedirs(obj)
    open(os.path.join(obj, 'src_t.cpp.o'), 'wb').write(b'\x7fELF\x01\x01\x01' + bytes(57))
    d = campaign(root, '1of1', [dict(address='00401000', source='src/t.cpp', status='ok', size=64)],
                 {'00401000-0.json': rec(dict(kind='call-count', x86=['00402000', '00403000'], arm=['00403000'])),
                  '00401000-1.json': rec(dict(kind='error', x86=None, arm='exc:1@0'))})
    out = os.path.join(root, 'c.json')
    r = run_causes(['summary', d, '--out', out], cwd=root, env_extra=dict(FNVNX_CAUSES_OBJ=obj), no_elftools=True)
    s = json.load(open(out)) if r.returncode == 0 else {}
    no = s.get('native_objects') or {}
    expect('O1', r.returncode == 0 and r.stderr.count('pyelftools is not installed') == 1,
           'no pyelftools: one warning on stderr (rc %d, %r)' % (r.returncode, r.stderr[-200:]))
    expect('O1', no.get('reader') == 'unavailable' and no.get('scanned') == 0 and no.get('dir') == obj,
           'summary native_objects says the reader is unavailable (%s)' % no)
    expect('O1', 'native objects: reader unavailable' in r.stdout, 'the CLI prints the reader line')
    expect('O1', not any(c in s.get('causes', {}) for c in ('native-inlined', 'new-null', 'after-native-undefined')),
           'no object-dependent cause is counted without the reader (%s)' % sorted(s.get('causes', {})))
    out2 = os.path.join(root, 'c2.json')
    r2 = run_causes(['summary', d, '--out', out2], cwd=root, env_extra=dict(FNVNX_CAUSES_OBJ=obj))
    no2 = (json.load(open(out2)) if r2.returncode == 0 else {}).get('native_objects') or {}
    expect('O1', no2.get('reader') == 'pyelftools' and 'not installed' not in r2.stderr,
           'with pyelftools: reader pyelftools, no warning (%s)' % no2)
    # O2: the totals of several shards
    rep = os.path.join(root, 'r.md')
    open(rep, 'w').write('x\n```json fnvnx-causes\n%s\n```\n' % json.dumps(s))
    out3 = os.path.join(root, 'm.json')
    run_causes(['merge', rep, '--out', out3], cwd=root)
    m = json.load(open(out3)) if os.path.exists(out3) else {}
    if 'native_objects' not in m:
        note('O2', 'causes.py merge drops native_objects: a merged total of reader-less shards does not say so '
                   '(fuzz_job.sh\'s report block slims it out too; the farm requires pyelftools at start, so low risk)')
    out4 = os.path.join(root, 'c4.json')
    r4 = run_causes(['summary', d, '--out', out4], cwd=root, no_elftools=True,
                    env_extra=dict(FNVNX_CAUSES_OBJ='', FNVNX_DFLINK=os.path.join(root, 'nolink')))
    no4 = (json.load(open(out4)) if r4.returncode == 0 else {}).get('native_objects') or {}
    if 'not installed' not in r4.stderr:
        note('O2', 'no object dir: the object rules are off with reader %s, missing %s and no warning '
                   '(reported in the summary only)' % (no4.get('reader'), no4.get('missing')))


# ------------------------------------------------------------------------------------------------ vtmodel (S13)

class _Exe:
    def __init__(self, vft):
        self.vft = vft


class _Img:
    syms, stubs = {}, {}


def case_v1():
    import vtmodel
    vft = {0x1000: dict(off=0, cls='A', slots=[1, 2, 3]),     # native 3: exact
           0x2000: dict(off=0, cls='B', slots=[1, 2, 3]),     # native 4: exact (D1/D0)
           0x3000: dict(off=0, cls='C', slots=[1, 2, 3]),     # native 2: short
           0x4000: dict(off=0, cls='D', slots=[1, 2]),        # native 5: longer
           0x5000: dict(off=0, cls='E', slots=[1, 2, 3, 4])}  # native 0: short
    groups = {'A': [0, None, 'a1', 'a2', 'a3'], 'B': [0, None, 'b1', 'b2', 'b3', 'b4'], 'C': [0, None, 'c1', 'c2'],
              'D': [0, None, 'd1', 'd2', 'd3', 'd4', 'd5'], 'E': [0, None]}
    m = object.__new__(vtmodel.Model)
    m.exe = _Exe(vft)
    m.rows = {v: ('_ZTV1' + e['cls'], 'a.o', 8) for v, e in vft.items()}
    m._groups = {('_ZTV1' + k, 'a.o'): (g, 4 * len(g)) for k, g in groups.items()}
    m._alias = {}
    m.short = set()
    m.linkdir = WORK
    c, _ = vtmodel.pairs(m)
    ps = set()
    for v, e in vft.items():
        n = vtmodel.native_slots(groups[e['cls']], 8)
        if n < len(e['slots']):
            ps.add(v)
    ap, mem, _ = m.native(sorted(vft), _Img(), {})
    expect('V1', m.short == ps == {0x3000, 0x5000} and c[(True, 'short')] == 2 and c[(True, 'exact')] == 2
           and c[(True, 'longer')] == 1, 'native() short %s == pairs short %s; counts %s' % (
               sorted('%x' % v for v in m.short), sorted('%x' % v for v in ps), dict(c)))
    sizes = {v: len(mem[ap[v] - 8]) // 4 - 2 for v in vft}
    expect('V1', all(sizes[v] >= len(vft[v]['slots']) + 2 for v in vft),
           'every copy still gets its padding to the exe count + 2 (%s)' % sizes)
    # V2: D : virtual B: [vbase off, off-to-top 0, typeinfo, d1 | vcall off, off-to-top -8, typeinfo, g]
    words = [8, 0, None, 'd1', 0, -8, None, 'g']
    n = vtmodel.native_slots(words, 12)
    if n != 1:
        note('V2', 'native_slots of a 1-slot primary followed by a secondary with a vcall offset = %d: the offset '
                   'word counts as a slot, so a pair one short of the exe reads exact (pre-existing; only with '
                   'virtual bases)' % n)


# ------------------------------------------------------------------------------------------------ renames (S13)

OLD = '?f@@YG_NPAX@Z'             # bool __stdcall f(void *)
NEW = '?f@S@@QAE_NPAX@Z'          # bool __thiscall S::f(void *)


def fresh_config(tree, tsv):
    os.environ['FNVNX_TREE'] = tree
    os.environ['FNVNX_UNITS_RENAMES'] = tsv
    import config
    return importlib.reload(config)


def fnkeys_for(config, fnkeys, targets, rows_file):
    """fnkeys.main on synthetic targets with a stub Native (same native hash per function in both runs)."""
    tdir = tempfile.mkdtemp(dir=WORK)
    tp, cp, op = (os.path.join(tdir, x) for x in ('t.jsonl', 'c.jsonl', 'k.json'))
    open(tp, 'w').write(''.join(json.dumps(dict(address=a, source=s)) + '\n' for a, s in targets))
    open(cp, 'w').write(''.join(json.dumps(dict(address=a, ohash='o' + a)) + '\n' for a, _ in targets))

    class Nat:
        def __init__(self, _):
            pass

        def keys(self, source):
            return {a: 'n' + a for a, s in targets if s == source}
    saved_nat, saved_argv, saved_out = fnkeys.Native, sys.argv, sys.stdout
    try:
        fnkeys.Native, sys.argv, sys.stdout = Nat, ['fnkeys.py', tp, cp, op], io.StringIO()
        fnkeys.config = config
        fnkeys.main()
    finally:
        fnkeys.Native, sys.argv, sys.stdout = saved_nat, saved_argv, saved_out
    return json.load(open(op))


def case_r():
    d = tempfile.mkdtemp(dir=WORK)
    tree = os.path.join(d, 'tree')
    os.makedirs(os.path.join(tree, 'src'))
    open(os.path.join(tree, 'src', 'a.cpp'), 'w').write('struct S {}; /* fnv-nx XINC1: 0x00402000 */\n')
    open(os.path.join(tree, 'src', 'b.cpp'), 'w').write('int g() { return 0; }\n')
    tsv1 = os.path.join(d, 'r1.tsv')
    open(tsv1, 'w').write('src/a.cpp\tdef\t0x00402000\t%s\t%s\n' % (OLD, NEW))
    tsv2 = os.path.join(d, 'r2.tsv')
    open(tsv2, 'w').write('src/a.cpp\tdef\t0x00402000\t%s\t%s\n' % (OLD, NEW) +
                          'src/a.cpp\tref\t0x00403000\t?h@@YAXXZ\t?h@@YGXXZ\n')
    config = fresh_config(tree, tsv1)
    rows = config.units_renames()
    if [r[0] for r in rows] != ['src/a.cpp']:
        defect('R1', 'setup: the S1b marker was not recognised (%s)' % rows)
        return
    import engine
    import fnkeys

    def uc_with(rows_):
        uc = object.__new__(engine.UnitCache)
        uc.tree_def = {0x00402000: OLD}
        uc.tree_ref = engine.apply_renames(uc.tree_def, rows_)
        uc.by_source = {'src/a.cpp': [(0, dict(symbols={OLD: '0x00402000'}))],
                        'src/b.cpp': [(0, dict(symbols={OLD: '0x00402000', '?h@@YAXXZ': '0x00403000'}))]}
        uc.def_at = {0x00402000: ('src/a.cpp', OLD)}
        uc._callee = {}
        uc.x86_unit = lambda *a: (_ for _ in ()).throw(KeyError('no x86 here'))
        uc.oracle_ret_pop = lambda a: None
        return uc
    before = uc_with([]).callee_info_for('src/b.cpp')
    after = uc_with(rows).callee_info_for('src/b.cpp')
    cb, ca = before.conv(0x00402000), after.conv(0x00402000)
    changed = (cb is not None and ca is not None and cb.describe() != ca.describe()) or before(0x00402000) != after(
        0x00402000)
    targets = [('00402000', 'src/a.cpp'), ('00405000', 'src/b.cpp')]
    k0 = fnkeys_for(fresh_config(tree, 'none'), fnkeys, targets, None)
    k1 = fnkeys_for(fresh_config(tree, tsv1), fnkeys, targets, None)
    k2 = fnkeys_for(fresh_config(tree, tsv2), fnkeys, targets, None)
    if changed and k0.get('00405000') == k1.get('00405000'):
        defect('R1', 'unit B (no rows) calls A\'s 0x00402000: its callee convention there goes %s -> %s with A\'s def '
                     'row (call-args compare, canned kind), but B\'s key stays %s: B keeps a verdict made under the '
                     'old convention' % (cb.describe() if cb else None, ca.describe() if ca else None,
                                         k1.get('00405000')))
    else:
        ok('R1', 'callers of a renamed definition: convention changed %s, key %s -> %s' % (
            changed, k0.get('00405000'), k1.get('00405000')))
    expect('R2', k0['00402000'] != k1['00402000'] != k2['00402000'] and k0['00405000'] == k1['00405000'] ==
           k2['00405000'], 'A\'s key changes with each change of its rows; B\'s never (%s / %s / %s)' % (
               k0['00402000'], k1['00402000'], k2['00402000']))
    ref = engine.apply_renames({}, fresh_config(tree, tsv2).units_renames())
    ub = uc_with(fresh_config(tree, tsv2).units_renames())
    expect('R2', ref == {('src/a.cpp', 0x00403000): '?h@@YGXXZ'} and
           ub.callee_info_for('src/b.cpp').caller_conv(0x00403000).describe() ==
           uc_with([]).callee_info_for('src/b.cpp').caller_conv(0x00403000).describe(),
           'a ref row without a def applies to its own unit only (B\'s declaration of 0x00403000 unchanged)')
    fresh_config(tree, 'none')


# ------------------------------------------------------------------------------------------------ fuzz_job tree

STUB = r'''#!/usr/bin/env bash
c12=${1:0:12}; echo "$1 $3" >> "$CALLS"
for kv in $AVAIL; do
  if [ "${kv%%=*}" = "$c12" ]; then mkdir -p "$2"; echo "${kv#*=}"; exit 0; fi
done
echo "no prepared tree for $c12" >&2; exit 1
'''


def git(repo, *a):
    return subprocess.run(['git', '-C', repo] + list(a), check=True, capture_output=True, text=True).stdout.strip()


def tree_block():
    s = open(os.path.join(PORT, 'tools', 'farm', 'fuzz_job.sh')).read()
    m = re.search(r"\n(# --- the link's tree.*?\n)# --- the player's", s, re.S)
    return m.group(1)


def run_tree(tmp, cs, meta, avail):
    d = tempfile.mkdtemp(dir=tmp)
    farm, runp, link, here = (os.path.join(d, x) for x in ('farm', 'run', 'link', 'here'))
    for p in (farm, runp, link, here):
        os.makedirs(p)
    open(os.path.join(here, 'fetch_tree.sh'), 'w').write(STUB)
    if meta is not None:
        json.dump(meta, open(os.path.join(link, 'meta.json'), 'w'))
    calls = os.path.join(d, 'calls')
    script = ('set -euo pipefail\nfail_report() { echo "FAILED: $2"; exit "$1"; }\nL12=${LCOMMIT:0:12}\n' +
              tree_block() + 'echo "NOTE $NOTE_TREE"\n')
    env = dict(os.environ, FARM_HOME=farm, RUN=runp, LINK=link, HERE=here, REPO=cs['repo'], LCOMMIT=cs['L'],
               ARTIFACTS=d, CALLS=calls, FUZZ_TREE_WAIT='3',
               AVAIL=' '.join('%s=%s' % (cs[k][:12], h) for k, h in avail.items()))
    env.pop('FUZZ_TREE', None)
    r = subprocess.run(['bash', '-c', script], capture_output=True, text=True, env=env)
    log = open(os.path.join(runp, 'fetch_tree.log')).read() if os.path.exists(os.path.join(runp, 'fetch_tree.log')) \
        else ''
    return r, log


def case_f():
    tmp = tempfile.mkdtemp(dir=WORK)
    repo = os.path.join(tmp, 'repo')
    os.makedirs(repo)
    git(repo, 'init', '-q')
    cs = {}
    for name in ('T', 'O', 'L'):
        git(repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', name)
        cs[name] = git(repo, 'rev-parse', 'HEAD')
    cs['repo'] = repo
    H, OTHER = 'aaaa1111bbbb2222', 'cccc3333dddd4444'
    meta = dict(commit=cs['L'], objects_commit=cs['O'],
                tree='prepare farm, tree (published for %s), tree hash %s' % (cs['T'][:12], H))
    r, log = run_tree(tmp, cs, meta, {'L': OTHER})
    if r.returncode == 0 and ('tree hash %s' % OTHER) in r.stdout:
        defect('F1', 'link made on tree %s; only t/<L12> exists, hash %s: rejected in the candidate loop, then taken '
                     'by the fallback wait on L without the check (rc 0, %r; the mismatch only in fetch_tree.log)'
               % (H, OTHER, r.stdout.strip().splitlines()[-1]))
    else:
        ok('F1', 'a mismatched tree is not taken (rc %d)' % r.returncode)
    r, _ = run_tree(tmp, cs, dict(commit=cs['L'], objects_commit=cs['O']), {'O': OTHER})
    if r.returncode == 0 and cs['O'][:12] in r.stdout:
        note('F2', 'meta.json without a tree note: the objects commit\'s tree (hash %s) is taken unchecked' % OTHER)
    r, log = run_tree(tmp, cs, meta, {'T': H, 'L': OTHER})
    expect('F3', r.returncode == 0 and ('prepare farm tree of %s (tree hash %s)' % (cs['T'][:12], H)) in r.stdout,
           'the recorded tree (hash matching) wins over t/<L12> with another hash (%r)' % r.stdout.strip()[-90:])


# ------------------------------------------------------------------------------------------------ census (S6b)

VS = 'vs_2_0\ndcl_position v0\nmov oPos, v0\n'
PS = 'ps_2_0\ndef c0, 1.0, 0.5, 0.25, 1.0\nmov oC0, c0\n'


def sdp(entries, count=None):
    body = b''.join(n.encode()[:256].ljust(256, b'\0') + struct.pack('<I', len(b)) + b for n, b in entries)
    return struct.pack('<III', 100, len(entries) if count is None else count, len(body)) + body


def case_k(build):
    tool, asm = os.path.join(build, 'sdp2dksh'), os.path.join(build, 's6b_asm')
    if not (os.path.exists(tool) and os.path.exists(asm)):
        print('SKIP   K1 no sdp2dksh / s6b_asm in %s (render/shaders/build.sh --tests)' % build)
        return
    d = tempfile.mkdtemp(dir=WORK)
    bins = {}
    for n, src in (('vs', VS), ('ps', PS)):
        open(os.path.join(d, n + '.asm'), 'w').write(src)
        subprocess.run([asm, os.path.join(d, n + '.asm'), os.path.join(d, n + '.bin')], check=True,
                       capture_output=True)
        bins[n] = open(os.path.join(d, n + '.bin'), 'rb').read()
    bad1 = struct.pack('<4I', 0xFFFE0300, 0x7777, 0x7778, 0x0000FFFF)      # vs_3_0, unknown opcodes, end
    bad2 = struct.pack('<3I', 0xFFFF0200, 0x0123, 0x0000FFFF)              # ps_2_0, unknown opcode, end
    pa, pb = os.path.join(d, 'a.sdp'), os.path.join(d, 'b.sdp')
    open(pa, 'wb').write(sdp([('v.vso', bins['vs']), ('p.pso', bins['ps']), ('x.vso', bad1)]))
    open(pb, 'wb').write(sdp([('v2.vso', bins['vs']), ('y.pso', bad2), ('x2.vso', bad1)]))
    r = subprocess.run([sys.executable, '-I', os.path.join(PORT, 'tests', 's6b', 'census.py'), pa, pb, '--build', build,
                        '--work', os.path.join(d, 'work')], capture_output=True, text=True, timeout=300)
    m = re.search(r'programs (\d+) \(unique (\d+)\): translated (\d+), failed (\d+) \(unique (\d+)\)', r.stdout)
    if not m:
        defect('K1', 'census did not report (rc %d): %s' % (r.returncode, (r.stderr or r.stdout)[-300:]))
        return
    progs, uniq, tr, fl, ufl = map(int, m.groups())
    table = [l.split('|') for l in r.stdout.splitlines() if l.startswith('| ') and not l.startswith('| cause')]
    tp, tu = sum(int(x[2]) for x in table), sum(int(x[3]) for x in table)
    expect('K1', (progs, uniq, tr, fl, ufl) == (6, 4, 3, 3, 2) and tp == fl and tu == ufl,
           'programs 6 = translated 3 + failed 3, unique 4 (2 failing); table %d programs / %d unique (%s)' % (
               tp, tu, m.group(0)))
    # K2: a package whose count field says 3 for 2 entries plus 300 trailing bytes
    pc = os.path.join(d, 'c.sdp')
    open(pc, 'wb').write(sdp([('v.vso', bins['vs']), ('p.pso', bins['ps'])], count=3) + bytes(300))
    rt = subprocess.run([tool, '--list', pc], capture_output=True, text=True)
    rc = subprocess.run([sys.executable, '-I', os.path.join(PORT, 'tests', 's6b', 'census.py'), pc, '--build', build,
                         '--work', os.path.join(d, 'work2')], capture_output=True, text=True, timeout=300)
    m2 = re.search(r'programs (\d+).*translated (\d+)', rc.stdout)
    if rt.returncode != 0 and m2:
        note('K2', 'a package fnvnx_sdp_parse rejects (%s) is counted by census as %s programs, %s translated: '
                   'census.extract ignores the header count and trailing bytes (well-formed packages unaffected)' % (
                       (rt.stderr.strip().split(': ', 1) + [''])[1], m2.group(1), m2.group(2)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--shaders-build', default=os.environ.get('FNVNX_SHADERS_BUILD',
                                                              os.path.join(PORT, 'render/shaders/build/host')))
    a = ap.parse_args()
    try:
        case_e1()
        case_h1()
        case_o1()
        case_v1()
        case_r()
        case_f()
        case_k(a.shaders_build)
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    print('%d defect case(s): %s' % (len(set(DEFECTS)), ' '.join(sorted(set(DEFECTS))) or '-'))
    return 1 if DEFECTS else 0


if __name__ == '__main__':
    raise SystemExit(main())
