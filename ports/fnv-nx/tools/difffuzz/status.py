"""status.py - one line per function of a campaign's results.jsonl (progress check)."""
import json
import sys

for line in open(sys.argv[1]):
    r = json.loads(line)
    print(r['address'], r.get('status'), r.get('execs'), r.get('coverage'), r.get('seconds'),
          [d['kind'] + '/' + str(d['cause']) for d in r.get('divergences', [])], (r.get('error') or '')[:160])
