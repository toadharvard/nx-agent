#!/usr/bin/env python3
"""tests/farm/test_fuzz_summary.py - fuzz_job.sh's report step on a synthetic run directory (J-0138): the report and
its machine-readable summary fnvnx-fuzz-summary/1 (<report>.json and the report's last block) carry the coverage and
the hard divergences by cause for R0; tools/gates/ready.py still parses the report.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
JOB = os.path.join(HERE, '..', '..', 'tools', 'farm', 'fuzz_job.sh')
FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


def block():
    s = open(JOB).read()
    m = re.search(r'"\$NOTE_EXE" <<\'EOF\'\n(.*?)\nEOF\n', s, re.S)
    return m.group(1)


def main():
    with tempfile.TemporaryDirectory() as run:
        def w(name, obj):
            p = os.path.join(run, name)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, 'w') as f:
                f.write(obj if isinstance(obj, str) else json.dumps(obj))
        w('targets.json', dict(catalogued=100, shard=100, no_native_object=4, not_modelled=2,
                               not_modelled_unresolved=2, not_modelled_indirect=0, indirect=30, targets=94,
                               vtable_model=True))
        w('status.json', dict(shard=94, no_key=1, fuzzed_this_head=60, reused=30, pending_changed=0, pending_new=3,
                              crashed=0))
        rows = ''.join(json.dumps(dict(address='%08x' % (0x401000 + i), status='ok', execs=10)) + '\n'
                       for i in range(90))
        w('camp/results.jsonl', rows[:len(rows) // 2])
        w('cum/results.jsonl', rows)
        table = '# difffuzz verdicts\n\n| stream | x |\n|---|---|\n| ALL | 1 |\n'
        w('verdicts_new.md', table)
        w('verdicts_cum.md', table)
        w('verdicts_cum.json', dict(ALL=dict(functions=90, hard=2, soft=3, harness=4, setup=0,
                                             causes={'native-trap': 2, 'nan-bits': 3, 'oracle-vcall': 4})))
        w('hard_cum.tsv', '00401000\tnative-trap\tS0-compile\terror\tsrc/a.cpp\n'
                          '00401010\tnative-trap\tS0-compile\terror\tsrc/a.cpp\n')
        w('stop.txt', 'deadline\n')
        rep = os.path.join(run, 'out', 'abc-fuzz.md')
        os.makedirs(os.path.dirname(rep))
        py = os.path.join(run, 'block.py')
        open(py, 'w').write(block())
        r = subprocess.run([sys.executable, py, run, rep, 'c' * 40, 'd' * 12, '3', '26', '2987', '40', '8', '4', '1',
                            '2', '3', 'link/x', 'tree', 'the player\'s exe'], capture_output=True, text=True,
                           env=dict(os.environ, MY_HARNESS='difffuzz-13'))
        check(r.returncode == 0, 'report step runs (%s)' % r.stderr[-300:])
        if r.returncode:
            return 1
        s = json.load(open(os.path.join(run, 'out', 'abc-fuzz.json')))
        check(s['format'] == 'fnvnx-fuzz-summary/1' and (s['shard'], s['shards']) == (3, 26), 'format, shard')
        check(s['coverage_pct'] == round(100 * 90 / 96, 2), 'coverage = verdicts / functions with a native object')
        check(s['hard'] == dict(divergences=2, functions=2, by_cause={'native-trap': 2}), 'hard by cause')
        check(s['status'] == 'partial' and s['stopped'] == 'deadline' and s['exe'] is True, 'status partial, deadline')
        text = open(rep).read()
        m = re.search(r'```json fnvnx-fuzz-summary\n(.*)\n```', text)
        check(m is not None and json.loads(m.group(1)) == s, 'the report carries the same summary')
        sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'gates'))
        try:
            import ready
            d = ready.parse_fuzz(text)
            check(d is not None and d['not_modelled'] == 2 and d['fuzzable'] == 94 and d['verdicts'] == 90,
                  'ready.py parses the report (%s)' % ({k: d.get(k) for k in ('not_modelled', 'fuzzable', 'verdicts')}
                                                       if d else None))
        except ImportError as e:
            check(False, 'ready.py imports: %s' % e)
    print('%d failures' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
