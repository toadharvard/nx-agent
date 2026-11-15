#!/usr/bin/env python3
"""A shape whose only disagreement with the pinned model lies in a declared model defect (defects.py) is not certified by the model: it is certified against an SDM
transcription (OWNER 10).  The universe is every shard record that names a defect (result FAILED(model-defect) or agree-except-declared), whatever the defect id.
    python3 defectspec.py SHARDS_DIR > defectspec.tsv        columns: shape, defects, result, note; the totals per defect id on stderr
A defect id without a spec in SPECS gives `no-spec` (reported, never passed); exit 1 if any row is FAILED or no-spec."""
import collections
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from proof import stringspec  # noqa: E402

SPECS = {'rep-prefix-ignored': stringspec.check}      # defect id -> check(shape key) -> (result, note); defects.py ids and these keys must be equal (test_defectspec)


def main(argv):
    rows, cnt, bad = [], collections.Counter(), 0
    for p in sorted(glob.glob(os.path.join(argv[0], '*.jsonl'))):
        for ln in open(p):
            r = json.loads(ln)
            if not r.get('defects'): continue
            res = []
            for d in r['defects']:
                cnt[d] += 1
                res.append(SPECS[d](r['shape']) if d in SPECS else ('no-spec', 'no SDM spec for defect ' + d))
            st = 'proved' if all(x[0] == 'proved' for x in res) else next(x[0] for x in res if x[0] != 'proved')
            bad += st != 'proved'
            rows.append((r['shape'], ','.join(r['defects']), st, '; '.join(x[1] for x in res)))
    for x in rows: print('\t'.join(x))
    by = collections.Counter((x[1], x[2]) for x in rows)
    sys.stderr.write('defectspec: %d rows; %s\n' % (len(rows), ', '.join('%s:%s=%d' % (d, s, n) for (d, s), n in sorted(by.items()))))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
