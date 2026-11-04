#!/usr/bin/env python3
"""test_ready_fuzz_shards.py - V1 (D14) against gates 82805db (tools/gates/ready.py, fnvnx-fuzz-summary/1 per
shard): fuzz.coverage on tests/gates' all-pass world (made-up numbers only), one change per case.

    python3 -I tests/v1/test_ready_fuzz_shards.py     -> one line per case, exit 1 when any case shows a defect

  R16 one shard's surplus hides another shard's gap: shard 1 reports 18 verdicts for its 10 functions with a native
      object, shard 2 reports 0 of 10; the sum is 18/20 = 90 %. fuzz.coverage must not pass (shard 2 has no verdict).
      R16a through the report text, R16b through fnvnx-fuzz-summary/1 blocks (82805db's path).
  R17 a summary with with_native_object -10 in shard 2      fuzz.coverage must not pass
"""
import io
import json
import os
import sys
import tempfile
import contextlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'gates'))
import test_ready  # noqa: E402  (World, C)

C = test_ready.C
FAIL = 0


def case(label, change):
    global FAIL
    with tempfile.TemporaryDirectory() as tmp:
        w = test_ready.World(tmp)
        change(w)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                card = w.run()
        except Exception as e:  # noqa: BLE001
            print('%s: CRASH: %s: %s' % (label, type(e).__name__, e))
            FAIL = 1
            return
        g = card['by_id']['fuzz.coverage']
        bad = 'fuzz.coverage passes: %s' % g.get('value') if g['status'] == 'pass' else ''
        FAIL |= bool(bad)
        print('%s: %s' % (label, bad or 'ok (%s, %s)' % (g['status'], g.get('value'))))


def fz(k):
    return 'reports/fz%d/%s-fuzz.md' % (k, C)


def text_counts(w):
    w.sub(fz(1), 'cumulative (verdicts valid for this head): 10 functions',
          'cumulative (verdicts valid for this head): 18 functions')
    w.sub(fz(2), 'cumulative (verdicts valid for this head): 10 functions',
          'cumulative (verdicts valid for this head): 0 functions')


def summary(k, with_obj, ver):
    blk = {'format': 'fnvnx-fuzz-summary/1', 'shard': k, 'shards': 2, 'status': 'complete', 'commit': C,
           'functions': {'with_native_object': with_obj, 'verdicts': ver, 'not_modelled': 0}}
    return '\n```json fnvnx-fuzz-summary\n%s\n```\n' % json.dumps(blk)


def add_summaries(pairs):
    def f(w):
        for k, (wo, v) in enumerate(pairs, 1):
            w.write(fz(k), w.read(fz(k)) + summary(k, wo, v))
    return f


def main():
    case('R16a text: 18/10 + 0/10', text_counts)
    case('R16b summary: 18/10 + 0/10', add_summaries([(10, 18), (10, 0)]))
    case('R17 summary: 10/10 + 0/-10', add_summaries([(10, 10), (-10, 0)]))
    return FAIL


if __name__ == '__main__':
    sys.exit(main())
