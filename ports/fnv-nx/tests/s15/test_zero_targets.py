#!/usr/bin/env python3
"""tools/report/zero_targets.py on a synthetic reports tree (D24, D25): every source's number, the newest head with a
report (stale when older than the head), the delta against the previous head, fuzz and sweep shards summed, the
scenario metric first and the lanes where the scenarios did not measure. No farm, no game data.

    python3 -I tests/s15/test_zero_targets.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
Z = os.path.join(HERE, '..', '..', 'tools', 'report', 'zero_targets.py')
fails = []
H = ['a' * 12, 'b' * 12, 'c' * 12]   # newest first


def ok(name, cond, detail=''):
    print(('ok   ' if cond else 'FAIL ') + name + (': ' + str(detail)[-500:] if detail and not cond else ''))
    if not cond:
        fails.append(name)


def put(root, farm, name, text):
    os.makedirs(os.path.join(root, farm), exist_ok=True)
    with open(os.path.join(root, farm, name), 'w') as f:
        f.write(text if isinstance(text, str) else json.dumps(text))


def full_md(traps, undef, multi):
    return ('# full\n\n- trial link: 9,000 objects, %s undefined, %s multiple definitions; split-global check\n\n'
            '## Guest\n\n| undefined symbols | category | owner |\n|---|---|---|\n| %s | game | S2 |\n| 10 | crt | S4 |\n\n'
            % (undef, multi, traps))


def scen(counts):
    t = {str(n): {'target': 'x', 'measured_in': 1 if c is not None else 0, 'of': 2, 'count': c or 0}
         for n, c in counts.items()}
    return {'schema': 'fnvnx-scenario-metric/1', 'scenarios': ['boot-menu', 'saveload'], 'targets': t}


def main():
    with tempfile.TemporaryDirectory() as d:
        root = os.path.join(d, 'reports')
        # head a: full + fuzz (2 of 2 shards) + scenario (3 not measured) + plat
        put(root, 'full1', H[0] + '-full.md', full_md('1,000', '1,200', '5'))
        put(root, 'f1', H[0] + '-fuzz.json', {'format': 'fnvnx-fuzz-summary/1', 'shard': 1, 'shards': 2, 'hard': {'functions': 30}})
        put(root, 'f2', H[0] + '-fuzz.json', {'format': 'fnvnx-fuzz-summary/1', 'shard': 2, 'shards': 2, 'hard': {'functions': 12}})
        put(root, 'sc1', H[0] + '-scenario.json', scen({1: 4, 2: None, 3: None, 4: 2, 9: 0, 12: None}))
        put(root, 'flt', H[0] + '-faults.json', {'schema': 'fnvnx-faults/1', 'total_groups': 6, 'new': 1, 'known': 5, 'fixed': 2})
        put(root, 'qk1', H[0] + '-plat.json', {'schema': 'fnvnx-plat/1', 'parts': {'unimpl': 7, 'open': 3, 'decode': 1, 'input': 2}})
        # head b: older full, fuzz (one shard), sweep shards, scenario, threads lane
        put(root, 'full1', H[1] + '-full.md', full_md('1,050', '1,300', '6'))
        put(root, 'f1', H[1] + '-fuzz.json', {'format': 'fnvnx-fuzz-summary/1', 'shard': 1, 'shards': 2, 'hard': {'functions': 40}})
        put(root, 's1', H[1] + '-sweep.json', {'failing_programs': [{'hash': 'p1'}, {'hash': 'p2'}], 'programs_vacuous_in_some_set': 3})
        put(root, 's2', H[1] + '-sweep.json', {'failing_programs': [{'hash': 'p2'}], 'programs_vacuous_in_some_set': 1})
        put(root, 'sc1', H[1] + '-scenario.json', scen({1: 9, 4: 2}))
        put(root, 'qk1', H[1] + '-threads.json', {'schema': 'fnvnx-threads-job/1', 'metric': 5, 'report': {'races': 5}})
        # head c: plat only
        put(root, 'qk1', H[2] + '-plat.json', {'schema': 'fnvnx-plat/1', 'parts': {'unimpl': 9, 'open': 3, 'decode': 0, 'input': 0}})
        heads = os.path.join(d, 'heads.txt')
        open(heads, 'w').write(''.join(h + '0' * 28 + '\n' for h in H))
        out = os.path.join(d, 'zero.md')
        r = subprocess.run([sys.executable, '-I', Z, root, '--heads', heads, '--out', out], capture_output=True, text=True)
        ok('runs', r.returncode == 0, r.stderr)
        j = json.load(open(out[:-3] + '.json'))
        row = {x['key']: x for x in j['rows']}
        ok('16 rows', len(j['rows']) == 16)
        ok('trap stubs: table sum, delta vs b', row['traps']['value'] == 1010 and row['traps']['delta'] == -50, row['traps'])
        ok('link errors: undefined + multiple', row['link']['value'] == 1205 and row['link']['delta'] == -101)
        ok('fuzz: shards summed, complete', row['fuzz']['value'] == 42 and row['fuzz']['note'] == '' and row['fuzz']['delta'] == 2)
        ok('fuzz at b: partial', 'partial' in (subprocess.run([sys.executable, '-I', Z, root, '--heads', heads, '--head',
                                                                 H[1], '--out', os.path.join(d, 'zb.md')],
                                                                capture_output=True).returncode == 0 and
                                               json.load(open(os.path.join(d, 'zb.json')))['rows'][2]['note'] or ''))
        ok('shaders: from b, stale; distinct failing + vacuous', row['shaders']['value'] == 2 + 4 and row['shaders']['stale']
           and row['shaders']['head'] == H[1], row['shaders'])
        ok('#1 scenario, delta vs b', row['#1']['value'] == 4 and row['#1']['delta'] == -5)
        ok('#1 faults lane beside it, with new/known/fixed', row['#1']['lane']['value'] == 6 and
           'new 1, known 5, fixed 2' in row['#1']['lane']['note'], row['#1'])
        ok('#3 not measured by scenarios: the plat lane, delta vs c', row['#3']['value'] == 7 and row['#3']['delta'] == -2,
           row['#3'])
        ok('#4 scenario measured: scenario number, lane beside it', row['#4']['value'] == 2 and row['#4']['lane']['value'] == 3)
        ok('#7 from the threads lane at b (stale)', row['#7']['value'] == 5 and row['#7']['stale'])
        ok('#9 zero counts as measured', row['#9']['value'] == 0)
        ok('#12 unmeasured', row['#12']['value'] is None)
        md = open(out).read()
        ok('md table', '| D24 | trap stubs | 1010 | -50 (vs bbbbbbbbbbbb) |' in md and '(stale)' in md, md[:600])
    print('test_zero_targets: %d failed' % len(fails))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
