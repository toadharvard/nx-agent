#!/usr/bin/env python3
"""The number of shards of a queue, derived from the measured per-shape wall time.
    python3 -I shardcount.py QUEUE.tsv BUDGET_SECONDS RESULTS.jsonl...
RESULTS: shard files of shapeprove.py (each record has `seconds`).  Rule: every shard must finish inside BUDGET with the p95 time per shape:
    shards = ceil(rows * p95 / BUDGET)     (rows = the shapes of the queue; a shard takes the shapes at position = K-1 mod shards, so every shard has ceil(rows / shards) shapes)
Prints `shards=<n> rows=<r> samples=<s> mean=<m> p95=<p> budget=<b>`; exit 1 when there are no timed records (no measurement, no count)."""
import json
import math
import sys


def main(argv):
    if len(argv) < 3:
        sys.stderr.write(__doc__); return 2
    rows = sum(1 for l in open(argv[0]) if l.strip() and l[0] != '#'); budget = float(argv[1]); t = []
    for p in argv[2:]:
        for l in open(p):
            try: r = json.loads(l)
            except ValueError: continue
            if 'seconds' in r: t.append(float(r['seconds']))
    if not t:
        print('no timed records'); return 1
    t.sort(); p95 = t[min(len(t) - 1, math.ceil(0.95 * len(t)) - 1)]
    print('shards=%d rows=%d samples=%d mean=%.2f p95=%.2f budget=%d' % (math.ceil(rows * p95 / budget), rows, len(t), sum(t) / len(t), p95, budget))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
