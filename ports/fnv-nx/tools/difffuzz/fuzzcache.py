"""fuzzcache.py - the verdict cache of an incremental fuzz farm (tools/farm/fuzz_job.sh).

A cache directory has a campaign's layout: results.jsonl (one row per function, as fuzz.py writes it, plus `key`
from fnkeys.py and `commit`, the head it was fuzzed on) and div/<addr>-<n>.json (the minimised reproducers).

    fuzzcache.py todo  <cache> <targets.jsonl> <keys.json> <out todo.jsonl> <out counts.json>
        targets whose key changed (first), that the cache does not hold yet, then earlier harness failures (last);
        targets without a key (no native definition in the link) are counted, not fuzzed; a cached verdict whose
        saved divergence a scoped harness change made after it explains differently (fnkeys.CHANGES recheck) goes
        with the changed ones (`recheck`)
    fuzzcache.py merge <cache> <campaign> <keys.json> <commit>
        the campaign's finished functions replace their cache rows and reproducers
    fuzzcache.py view  <cache> <targets.jsonl> <keys.json> <out dir>
        the cumulative campaign of this head: cache rows whose key is the current one (reproducers linked)
    fuzzcache.py status <cache> <targets.jsonl> <keys.json> <commit> <out counts.json>
        fuzzed on this head / reused from an earlier head / pending (key changed, never fuzzed) / no key
    fuzzcache.py timeouts <campaign> <chunk.jsonl>
        the chunk's functions without a result after the watchdog fired: rows status run:timeout
    fuzzcache.py dropcrash <campaign>
        forget the campaign's harness crashes (crash:<exception> rows), so a rerun on the same head fuzzes them again
    fuzzcache.py seed  <cache> <targets.jsonl> <other cache>...
        rows of other caches (another shard count) for this shard's functions: their keys decide reuse later
        (harness failures are not seeded)
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


VERDICT_SETUP = ('setup:Unsupported', 'setup:KeyError', 'setup:ValueError')   # deterministic: no function to fuzz


def harness_failure(r):
    st = str(r.get('status', ''))
    return st.startswith(('crash:', 'run:')) or (st.startswith('setup:') and st not in VERDICT_SETUP)


def change_tags():
    import fnkeys
    return [c.tag for c in fnkeys.CHANGES]


def recheck_due(cache, changes=None, rows=None):
    """Addresses of cached verdicts a scoped change wants fuzzed again: the row was fuzzed before the change (its
    `changes` lacks the tag) and one of its saved divergences matches the change's recheck predicate."""
    import fnkeys
    changes = [c for c in (fnkeys.CHANGES if changes is None else changes) if c.recheck is not None]
    rows = load_rows(cache) if rows is None else rows
    due = set()
    for a, r in rows.items():
        todo = [c for c in changes if c.tag not in (r.get('changes') or ())]
        if not todo or harness_failure(r):
            continue
        for p in divs_of(cache, a):
            try:
                rec = json.load(open(p))
            except (OSError, ValueError):
                continue
            if any(c.recheck(rec) for c in todo):
                due.add(a)
                break
    return due


def cmd_todo(cache, targets, keys, out, counts_out):
    rows, keys = load_rows(cache), json.load(open(keys))
    due = recheck_due(cache, rows=rows)
    stale, new, retry, c = [], [], [], dict(shard=0, no_key=0, up_to_date=0, changed=0, new=0, retry=0, recheck=0)
    for line in open(targets):
        t = json.loads(line)
        c['shard'] += 1
        k = keys.get(t['address'])
        if k is None:
            c['no_key'] += 1
            continue
        r = rows.get(t['address'])
        # a harness failure on that function (crash:*, run:*, a setup error other than an unsupported
        # signature or a missing definition) is not a verdict: fuzz it again
        if r is not None and r.get('key') == k and not harness_failure(r) and t['address'] in due:
            c['recheck'] += 1
            stale.append(t)
        elif r is not None and r.get('key') == k and not harness_failure(r):
            c['up_to_date'] += 1
        elif r is not None and harness_failure(r):
            c['retry'] += 1
            retry.append(t)
        elif r is not None:
            c['changed'] += 1
            stale.append(t)
        else:
            c['new'] += 1
            new.append(t)
    with open(out, 'w') as f:
        # earlier harness failures (crash, run:timeout, run:slow) go last: a function that hung a chunk or ran out
        # of its bound must not hold back the rest of the shard on every run (fz1, J-0138)
        for t in stale + new + retry:
            f.write(json.dumps(t) + '\n')
    json.dump(c, open(counts_out, 'w'))
    print(c)


def shard_of(cache):
    """The shard name a cache directory stands for (`fuzzcache-7of26` or `7of26` -> `7of26`): the per-head branch
    the farm publishes a run's reproducers on is fuzz/<commit12>/<shard>."""
    n = os.path.basename(os.path.normpath(cache))
    return n[len('fuzzcache-'):] if n.startswith('fuzzcache-') else n


def cmd_merge(cache, camp, keys, commit):
    rows, keys = load_rows(cache), json.load(open(keys))
    tags = change_tags()
    os.makedirs(os.path.join(cache, 'div'), exist_ok=True)
    n = 0
    for a, r in load_rows(camp).items():
        if a not in keys or (a in rows and rows[a].get('key') == keys[a] and rows[a].get('commit') == commit
                             and rows[a].get('changes') == tags):
            continue
        for p in divs_of(cache, a):
            os.remove(p)
        for p in divs_of(camp, a):
            shutil.copy(p, os.path.join(cache, 'div', os.path.basename(p)))
        # the scoped changes this verdict saw; shard: where this head published the reproducer
        r = dict(r, key=keys[a], commit=commit, changes=tags, shard=shard_of(cache))
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
    due = recheck_due(cache, rows=rows)
    c = dict(shard=0, no_key=0, fuzzed_this_head=0, reused=0, pending_changed=0, pending_new=0, crashed=0)
    for line in open(targets):
        a = json.loads(line)['address']
        c['shard'] += 1
        k, r = keys.get(a), rows.get(a)
        if k is None:
            c['no_key'] += 1
        elif r is not None and harness_failure(r):
            c['crashed'] += 1                     # a harness exception, fuzzed again by the next run
        elif r is not None and r.get('key') == k and a in due:
            c['pending_changed'] += 1             # a scoped harness change wants this verdict again (recheck)
        elif r is not None and r.get('key') == k:
            c['fuzzed_this_head' if r.get('commit') == commit else 'reused'] += 1
        else:
            c['pending_changed' if r is not None else 'pending_new'] += 1
    json.dump(c, open(out, 'w'))
    print(c)


def cmd_timeouts(camp, chunk):
    rows = load_rows(camp)
    n = 0
    for line in open(chunk):
        t = json.loads(line)
        if t['address'] not in rows:
            rows[t['address']] = dict(source=t['source'], address=t['address'], symbol=t.get('symbol'),
                                      unit=t.get('unit'), kind=t.get('kind'), execs=0, divergences=[],
                                      status='run:timeout', error='chunk watchdog')
            n += 1
    save_rows(camp, rows)
    print('recorded %d timed-out functions' % n)


def cmd_dropcrash(camp):
    rows = load_rows(camp)
    keep = {a: r for a, r in rows.items() if not harness_failure(r)}
    if len(keep) != len(rows):
        save_rows(camp, keep)
    print('dropped %d crashed functions' % (len(rows) - len(keep)))


def cmd_seed(cache, targets, others):
    rows = load_rows(cache)
    want = {json.loads(line)['address'] for line in open(targets)}
    os.makedirs(os.path.join(cache, 'div'), exist_ok=True)
    n = 0
    for o in others:
        for a, r in load_rows(o).items():
            # a harness failure is no verdict to reuse (the 32-shard caches held only crash:ModuleNotFoundError
            # rows of 9a0243c: seeding them made every new 26-shard cache start with a backlog of failures)
            if a in want and a not in rows and not harness_failure(r):
                rows[a] = dict(r, shard=r.get('shard') or shard_of(o))   # it stays published under its source shard
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
    elif cmd == 'dropcrash':
        cmd_dropcrash(*args)
    elif cmd == 'timeouts':
        cmd_timeouts(*args)
    elif cmd == 'seed':
        cmd_seed(args[0], args[1], args[2:])
    else:
        print(__doc__)
