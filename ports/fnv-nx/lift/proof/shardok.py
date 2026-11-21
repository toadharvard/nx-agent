#!/usr/bin/env python3
"""shardok JSONL ROWS: exit 0 iff the shard file has exactly ROWS records (one per line, valid JSON) and none has result=error; the one rule by which a published results branch counts as published."""
import json, sys

def ok(path, rows):
    n = 0
    try:
        for ln in open(path):
            if not ln.strip(): continue
            r = json.loads(ln); n += 1
            if r.get('result') == 'error': return False
    except (OSError, ValueError): return False
    return n == rows

def rows(queue, k, n, sl=1):
    """the number of queue rows of shard k of n (the rule shapeprove.py uses: every sl-th row, then position mod n == k-1)"""
    r = [l for l in open(queue) if l.strip() and l[0] != '#'][::sl]
    return len([1 for i, _ in enumerate(r) if i % n == k - 1])


if __name__ == '__main__':
    if sys.argv[1] == 'rows': print(rows(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]) if len(sys.argv) > 5 else 1)); sys.exit(0)
    sys.exit(0 if ok(sys.argv[1], int(sys.argv[2])) else 1)
