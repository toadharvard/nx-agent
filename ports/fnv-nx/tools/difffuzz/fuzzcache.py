"""fuzzcache.py - the verdict cache of an incremental fuzz farm (tools/farm/fuzz_job.sh).

A cache directory has a campaign's layout: results.jsonl (one row per function, as fuzz.py writes it, plus `key`
from fnkeys.py and `commit`, the head it was fuzzed on) and div/<addr>-<n>.json (the minimised reproducers).

    fuzzcache.py todo  <cache> <targets.jsonl> <keys.json> <out todo.jsonl> <out counts.json>
        targets whose key changed (first) or that the cache does not hold yet; targets without a key (no native
        definition in the link) are counted, not fuzzed
    fuzzcache.py merge <cache> <campaign> <keys.json> <commit>
        the campaign's finished functions replace their cache rows and reproducers
    fuzzcache.py view  <cache> <targets.jsonl> <keys.json> <out dir>
        the cumulative campaign of this head: cache rows whose key is the current one (reproducers linked)
    fuzzcache.py status <cache> <targets.jsonl> <keys.json> <commit> <out counts.json>
        fuzzed on this head / reused from an earlier head / pending (key changed, never fuzzed) / no key
    fuzzcache.py seed  <cache> <targets.jsonl> <other cache>...
        rows of other caches (another shard count) for this shard's functions: their keys decide reuse later
"""
import glob
import json
import os
import shutil
import sys


def load_rows(d):
    rows = {}
    p = os.path.join(d, 'results.jsonl')
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line:
                r = json.loads(line)
                rows[r['address']] = r
    return rows


def save_rows(d, rows):
    p = os.path.join(d, 'results.jsonl')
    with open(p + '.tmp', 'w') as f:
        for a in sorted(rows):
            f.write(json.dumps(rows[a], sort_keys=True) + '\n')
    os.replace(p + '.tmp', p)


def divs_of(d, addr):
    return sorted(glob.glob(os.path.join(d, 'div', '%s-*.json' % addr)))


def cmd_todo(cache, targets, keys, out, counts_out):
    rows, keys = load_rows(cache), json.load(open(keys))
    stale, new, c = [], [], dict(shard=0, no_key=0, up_to_date=0, changed=0, new=0)
    for line in open(targets):
        t = json.loads(line)
        c['shard'] += 1
        k = keys.get(t['address'])
        if k is None:
            c['no_key'] += 1
            continue
        r = rows.get(t['address'])
        # 'crash:<exception>' is the harness failing on that function, not a verdict: fuzz it again
        if r is not None and r.get('key') == k and not str(r.get('status', '')).startswith('crash:'):
            c['up_to_date'] += 1
        elif r is not None:
            c['changed'] += 1
            stale.append(t)
        else:
            c['new'] += 1
            new.append(t)
    with open(out, 'w') as f:
        for t in stale + new:
            f.write(json.dumps(t) + '\n')
    json.dump(c, open(counts_out, 'w'))
    print(c)


def cmd_merge(cache, camp, keys, commit):
    rows, keys = load_rows(cache), json.load(open(keys))
    os.makedirs(os.path.join(cache, 'div'), exist_ok=True)
    n = 0
    for a, r in load_rows(camp).items():
        if a not in keys or (a in rows and rows[a].get('key') == keys[a] and rows[a].get('commit') == commit):
            continue
        for p in divs_of(cache, a):
            os.remove(p)
        for p in divs_of(camp, a):
            shutil.copy(p, os.path.join(cache, 'div', os.path.basename(p)))
        r = dict(r, key=keys[a], commit=commit)
        rows[a] = r
        n += 1
    save_rows(cache, rows)
    print('merged %d functions; cache holds %d' % (n, len(rows)))


def cmd_view(cache, targets, keys, out):
    rows, keys = load_rows(cache), json.load(open(keys))
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(os.path.join(out, 'div'))
    keep = {}
    for line in open(targets):
        a = json.loads(line)['address']
        r = rows.get(a)
        if r is not None and keys.get(a) is not None and r.get('key') == keys[a]:
            keep[a] = r
            for p in divs_of(cache, a):
                os.symlink(os.path.abspath(p), os.path.join(out, 'div', os.path.basename(p)))
    save_rows(out, keep)
    print('cumulative: %d functions' % len(keep))


def cmd_status(cache, targets, keys, commit, out):
    """Where this head stands: fuzzed on this head, verdict reused from an earlier head (key unchanged), pending
    (key changed or never fuzzed), no key."""
    rows, keys = load_rows(cache), json.load(open(keys))
    c = dict(shard=0, no_key=0, fuzzed_this_head=0, reused=0, pending_changed=0, pending_new=0)
    for line in open(targets):
        a = json.loads(line)['address']
        c['shard'] += 1
        k, r = keys.get(a), rows.get(a)
        if k is None:
            c['no_key'] += 1
        elif r is not None and r.get('key') == k:
            c['fuzzed_this_head' if r.get('commit') == commit else 'reused'] += 1
        else:
            c['pending_changed' if r is not None else 'pending_new'] += 1
    json.dump(c, open(out, 'w'))
    print(c)


def cmd_seed(cache, targets, others):
    rows = load_rows(cache)
    want = {json.loads(line)['address'] for line in open(targets)}
    os.makedirs(os.path.join(cache, 'div'), exist_ok=True)
    n = 0
    for o in others:
        for a, r in load_rows(o).items():
            if a in want and a not in rows:
                rows[a] = r
                for p in divs_of(o, a):
                    shutil.copy(p, os.path.join(cache, 'div', os.path.basename(p)))
                n += 1
    save_rows(cache, rows)
    print('seeded %d functions from %d caches' % (n, len(others)))


if __name__ == '__main__':
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == 'todo':
        cmd_todo(*args)
    elif cmd == 'merge':
        cmd_merge(*args)
    elif cmd == 'view':
        cmd_view(*args)
    elif cmd == 'status':
        cmd_status(*args)
    elif cmd == 'seed':
        cmd_seed(args[0], args[1], args[2:])
    else:
        print(__doc__)
