#!/usr/bin/env python3
"""T1: tools/farm/ratchet.py - the regression ratchet. Synthetic reports in the farms' formats (numbers made up):
the first report sets the ledger; a better one advances it; a worse one gets "- REGRESSION: <n> items vs <head>" as
its first line and a REGRESSIONS section naming each item, its best and the head that first had it good, with the
commit range between the heads, and does not advance the ledger (not even its improved metrics); a good report
clears the marks. An older head reported late is not compared; a WIP report is compared and changes nothing; a
sweep shard whose setup failed measure nothing; checking twice gives one
section; `accept` drops a metric; zero_targets.py's regressions column counts the marks.
    python3 tests/t1/test_ratchet.py"""
import json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(P, 'tools', 'farm'))
import ratchet  # noqa: E402

fails = 0


def ok(name, cond):
    global fails
    print(('ok   ' if cond else 'FAIL ') + name)
    fails += 0 if cond else 1


def full_md(h, traps=(100, 20), undef=500, objs=2900, failed=(), winmain='yes', milestones=None, skipped=40):
    ms = milestones or {'winmain': 'yes', 'ini-read': 'no'}
    L = ['# full build %s' % h, '', '## Stages', '', '| stage | status | run | time | summary |', '|---|---|---|---|---|',
         '| guest | warn | ran | 1 s | x |', '', '## Census and compile', '',
         '- compile: 2,978 units, {:,} objects, {} failed'.format(objs, len(failed)), '']
    if failed:
        L += ['Units without an object (errors, first error):', ''] + ['- `%s` 1: error' % u for u in failed] + ['']
    L += ['## Link', '', '- trial link: 3,824 objects, {:,} undefined, 0 multiple definitions'.format(undef), '',
          '## Guest', '', '| undefined symbols | category | owner |', '|---|---|---|'] + \
         ['| {:,} | c{} | o |'.format(n, i) for i, n in enumerate(traps)] + \
         ['', '## Emulated boot', '', '- WinMain entered: %s; initializers skipped after a fault: %d; trapped keys 0' % (winmain, skipped),
          '- game milestones (tests/s5/milestones.txt): %s; last reached: x' % ', '.join('%s %s' % kv for kv in ms.items()), '']
    return '\n'.join(L) + '\n'


def main():
    t = tempfile.mkdtemp(prefix='ratchet-')
    try:
        repo = os.path.join(t, 'repo')
        g = lambda *a: subprocess.run(['git', '-C', repo, '-c', 'user.name=t', '-c', 'user.email=t@t'] + list(a),
                                      capture_output=True, text=True, check=True).stdout.strip()
        os.makedirs(repo); g('init', '-q')
        H = []
        for i in range(6):
            g('commit', '-q', '--allow-empty', '-m', 'change %d' % i)
            H.append(g('rev-parse', 'HEAD'))
        led = os.path.join(t, 'ratchet', 'ledger.json')
        rep = os.path.join(t, 'r.md')

        def check(job, head, text, side=None, wip=None, update=True):
            open(rep, 'w').write(text)
            argv = ['check', '--ledger', led, '--report', rep, '--job', job, '--head', head, '--repo', repo]
            if side is not None:
                json.dump(side, open(rep[:-3] + '.json', 'w'))
                argv += ['--sidecar', rep[:-3] + '.json']
            argv += ['--wip', wip] if wip else []
            argv += ['--update'] if update else []
            assert ratchet.main(argv) == 0
            return open(rep).read()

        L = lambda: json.load(open(led))['metrics']
        r0 = check('full', H[0], full_md(H[0]))
        ok('first report: ledger set, no regression', 'REGRESSION' not in r0.split('\n')[0] and
           L()['full: trap stubs']['best'] == 120 and L()['full: trap stubs']['head'] == H[0][:12] and
           '## REGRESSIONS' in r0 and '- none (' in r0)
        r1 = check('full', H[1], full_md(H[1], traps=(90, 20), objs=2901, milestones={'winmain': 'yes', 'ini-read': 'yes'}))
        ok('better: advanced to the new head', L()['full: trap stubs']['best'] == 110 and
           L()['full: trap stubs']['head'] == H[1][:12] and L()['full: compile objects']['best'] == 2901 and
           L()['full: boot milestones']['items'].get('ini-read') == H[1][:12])
        r3 = check('full', H[3], full_md(H[3], traps=(95, 20), undef=400, failed=('src/a.cpp',),
                                         milestones={'winmain': 'yes', 'ini-read': 'no'}, objs=2900))
        first = r3.split('\n')[0]
        ok('worse: first line "- REGRESSION: <n> items vs <head>"', first.startswith('- REGRESSION: 4 items vs ') and
           sorted(first.split(' vs ')[1].split(', ')) == sorted([H[0][:12], H[1][:12]]))
        ok('each item named with its best and first-good head', '- full: trap stubs: 115 now, 110 at %s' % H[1][:12] in r3
           and '- full: boot milestones / ini-read: no now, yes at %s' % H[1][:12] in r3 and
           '- full: units without an object / src/a.cpp: bad now, good at %s' % H[0][:12] in r3 and
           '- full: compile objects: 2900 now, 2901 at %s' % H[1][:12] in r3)
        ok('the commit range between the heads', 'commits %s..%s (2):' % (H[1][:12], H[3][:12]) in r3 and
           'change 3' in r3 and 'change 2' in r3 and 'commits %s..%s (3):' % (H[0][:12], H[3][:12]) in r3)
        ok('a regression: the ledger not advanced, its improved metric neither', L()['full: trial-link undefined']['best'] == 500
           and L()['full: trap stubs']['regression']['head'] == H[3][:12] and 'regression' not in L()['full: boot initializers skipped'])
        r3b = check('full', H[3], r3, update=False)
        ok('checking a checked report again: one section, one first line', r3b.count('## REGRESSIONS') == 1 and
           r3b.count('- REGRESSION:') == 1)
        # an older head reported late (H[2] is an ancestor of the ledger's H[1]? no: H[0] is)
        r0b = check('full', H[0], full_md(H[0], traps=(500, 0)))
        ok('an older head reported late: not compared', 'REGRESSION' not in r0b.split('\n')[0] and
           'this head is older' in r0b)
        ok('the older head did not move the ledger\'s head back', json.load(open(led)).get('head') == H[1][:12])
        before = open(led).read()
        rw = check('full', H[4], full_md(H[4], traps=(999, 0)), wip='wip/x')
        ok('WIP: compared (regression named), the ledger unchanged', rw.startswith('- REGRESSION: ') and
           open(led).read() == before)
        r4 = check('full', H[4], full_md(H[4], traps=(80, 10), undef=300, objs=2905, skipped=30,
                                         milestones={'winmain': 'yes', 'ini-read': 'yes'}))
        ok('good again: advanced, marks cleared', not r4.startswith('- REGRESSION') and L()['full: trap stubs']['best'] == 90
           and not any('regression' in e for e in L().values()) and json.load(open(led))['head'] == H[4][:12])
        # equiv, sweep, scenario
        eq = check('equiv', H[4], '# MSVC equivalence gate - x shard 1/1\n\n- units: 2988 compared; 2407 identical, 580 differ\n'
                   '- functions: 68456 compared, 1141 differ (EH-only 789)\n')
        ok('equiv: identical functions and units', L()['equiv 1of1: identical functions']['best'] == 67315 and
           L()['equiv 1of1: identical units']['best'] == 2407 and '- none (2 metrics' in eq)
        sw = {'k': '4', 'n': '36', 'programs_checked': 10, 'failing_programs': [{'hash': 'aa', 'name': 'A'}]}
        check('sweep', H[4], '# sweep\n', side=sw)
        s5 = check('sweep', H[5], '# sweep\n', side=dict(sw, failing_programs=[{'hash': 'aa'}, {'hash': 'bb'}]))
        ok('sweep: passing programs and a newly failing program named', '- sweep 4of36: failing programs / bb: bad now' in s5
           and '- sweep 4of36: passing programs: 8 now, 9 at' in s5)
        se = check('sweep', H[5], '# sweep\n\n- FAIL: setup failed: x\n', side=dict(sw, error='setup failed: x', programs_checked=0))
        ok('sweep whose setup failed: no metrics', '- none: this report has no ratchet metrics' in se)
        sc = {'schema': 'fnvnx-scenario-metric/1', 'targets': {'3': {'count': 2, 'measured_in': 5, 'of': 5},
                                                              '9': {'count': 0, 'measured_in': 0, 'of': 5}}}
        check('scenario', H[4], '# scenarios\n', side=sc)
        s2 = check('scenario', H[5], '# scenarios\n', side={'schema': 'fnvnx-scenario-metric/1', 'targets': {
            '3': {'count': 4, 'measured_in': 5, 'of': 5}}})
        ok('scenario: a D25 target measured, worse', '- scenario D25 #3 (5 scenarios): 4 now, 2 at %s' % H[4][:12] in s2
           and 'scenario D25 #9 (5 scenarios)' not in L())
        # the zero table's column: the marks of the sweep and scenario regressions above
        zr = os.path.join(t, 'z')
        os.makedirs(os.path.join(zr, 'ratchet'))
        shutil.copy(led, os.path.join(zr, 'ratchet', 'ledger.json'))
        hf = os.path.join(t, 'heads'); open(hf, 'w').write('\n'.join(reversed(H)) + '\n')
        subprocess.run([sys.executable, '-I', os.path.join(P, 'tools', 'report', 'zero_targets.py'), zr, '--heads', hf,
                        '--out', os.path.join(t, 'zero.md')], check=True, capture_output=True)
        z = json.load(open(os.path.join(t, 'zero.json')))
        rg = {r['key']: r['regressions'] for r in z['rows']}
        zm = open(os.path.join(t, 'zero.md')).read()
        ok('zero table: regressions per target from the ledger', rg['shaders'] == 2 and rg['#3'] == 1
           and rg['traps'] == 0 and '| regressions |' in zm and '- regressions (the ratchet ledger' in zm)
        ratchet.main(['accept', '--ledger', led, 'scenario D25 #3 (5 scenarios)'])
        ok('accept: the metric dropped', 'scenario D25 #3 (5 scenarios)' not in L())
        # S5's timer slices (2d1afaeb..6da559aa) booted nondeterministically: a head whose emu_guest.py has them
        # gets its boot metrics listed as not compared (neither advance nor regress); the others are compared
        em = os.path.join(repo, 'ports', 'fnv-nx', 'tests', 's5')
        os.makedirs(em)
        open(os.path.join(em, 'emu_guest.py'), 'w').write('ap.add_argument("--slice-ms")\n')
        g('add', '-A'); g('commit', '-qm', 'timer slices')
        U = g('rev-parse', 'HEAD')
        before = L()['full: boot milestones']
        ru = check('full', U, full_md(U, traps=(80, 10), undef=300, objs=2905, skipped=99,
                                      milestones={'winmain': 'no', 'ini-read': 'no'}))
        ok('timer-sliced emulator: the boot metrics not compared, the others are', 'unreliable: the emulated boots' in ru
           and not ru.startswith('- REGRESSION') and L()['full: boot milestones'] == before and
           L()['full: boot initializers skipped']['best'] == 30 and L()['full: trap stubs']['head'] == H[4][:12])
        bl = check('bootloop', U, '# bootloop\n', side={'schema': 'fnvnx-bootloop/1', 'nulldev': '1', 'reached': 9,
                                                       'milestones': [['winmain', True]] * 1, 'error': None})
        ok('timer-sliced emulator: a bootloop report changes nothing', 'unreliable' in bl and
           'bootloop: stub boot (nulldev) milestones reached' not in L())
        open(os.path.join(em, 'emu_guest.py'), 'w').write('ap.add_argument("--slice-ms")\nap.add_argument("--hc-slice")\n')
        g('add', '-A'); g('commit', '-qm', 'host-call preemption')
        V = g('rev-parse', 'HEAD')
        rv = check('full', V, full_md(V, traps=(80, 10), undef=300, objs=2905, skipped=99,
                                      milestones={'winmain': 'yes', 'ini-read': 'yes'}))
        ok('host-call preemption (620572bc): boot metrics compared again', 'unreliable' not in rv and
           '- full: boot initializers skipped: 99 now, 30 at' in rv)
        nj = check('quick', H[5], '# quick\n')
        ok('a job without metrics: a REGRESSIONS section that says so', '- none: this report has no ratchet metrics' in nj)
    finally:
        shutil.rmtree(t, ignore_errors=True)
    print('test_ratchet: all ok' if not fails else 'test_ratchet: %d failed' % fails)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
