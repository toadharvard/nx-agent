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

if __name__ == '__main__': sys.exit(0 if ok(sys.argv[1], int(sys.argv[2])) else 1)
