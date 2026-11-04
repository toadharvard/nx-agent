#!/usr/bin/env python3
"""tests/s13/test_fuzzcache.py - the fuzz farms' verdict cache (tools/difffuzz/fuzzcache.py, J-0138): the todo order
(key changed, new, then earlier harness failures last) and seeding from another shard layout without its harness
failures. Synthetic rows only.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, '..', '..', 'tools', 'difffuzz')
FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


def rows(path, rs):
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, 'results.jsonl'), 'w') as f:
        for r in rs:
            f.write(json.dumps(r) + '\n')


def fc(*args):
    subprocess.run([sys.executable, os.path.join(TOOLS, 'fuzzcache.py')] + list(args), check=True,
                   capture_output=True)


def main():
    with tempfile.TemporaryDirectory() as d:
        tg = os.path.join(d, 'targets.jsonl')
        addrs = ['00401000', '00401010', '00401020', '00401030', '00401040']
        with open(tg, 'w') as f:
            for a in addrs:
                f.write(json.dumps(dict(address=a, source='u.cpp')) + '\n')
        keys = {a: 'k' + a for a in addrs}
        json.dump(keys, open(os.path.join(d, 'keys.json'), 'w'))
        cache = os.path.join(d, 'cache')
        rows(cache, [dict(address='00401000', status='crash:ModuleNotFoundError', key='old'),     # failure
                     dict(address='00401010', status='ok', key='old'),                            # key changed
                     dict(address='00401020', status='ok', key='k00401020'),                      # up to date
                     dict(address='00401030', status='run:timeout', key='k00401030')])            # failure
        fc('todo', cache, tg, os.path.join(d, 'keys.json'), os.path.join(d, 'todo.jsonl'), os.path.join(d, 'c.json'))
        order = [json.loads(l)['address'] for l in open(os.path.join(d, 'todo.jsonl'))]
        check(order == ['00401010', '00401040', '00401000', '00401030'],
              'todo: key changed, new, then harness failures last (%s)' % order)
        c = json.load(open(os.path.join(d, 'c.json')))
        check((c['up_to_date'], c['changed'], c['new'], c['retry']) == (1, 1, 1, 2), 'todo counts %s' % c)
        other = os.path.join(d, 'other')
        rows(other, [dict(address='00401000', status='crash:ModuleNotFoundError', key='x'),
                     dict(address='00401010', status='ok', key='x'), dict(address='00402000', status='ok', key='x')])
        new = os.path.join(d, 'new')
        os.makedirs(new)
        fc('seed', new, tg, other)
        got = sorted(json.loads(l)['address'] for l in open(os.path.join(new, 'results.jsonl')))
        check(got == ['00401010'], 'seed: only this shard\'s verdicts, no harness failures (%s)' % got)
    print('%d failures' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
