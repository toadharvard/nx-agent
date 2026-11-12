#!/usr/bin/env python3
"""ratchet.py - the regression ratchet (owner order, T1): a best-known ledger of the farms' numbers, and a REGRESSIONS
section on every farm report that names each item worse than its best and the head that first had it good.

    ratchet.py check --ledger <ledger.json> --report <in.md> --job <job> --head <commit> [--sidecar <in.json>]
                     [--repo <git dir>] [--wip <branch>] [--out <out.md>] [--update]
    ratchet.py accept --ledger <ledger.json> <metric> [...]      forget a metric's best (an accepted regression)
    ratchet.py show --ledger <ledger.json>

The ledger lives in the artifacts repository, branch reports/ratchet, file ledger.json (tools/farm/runner.sh
fetches it, runs `check --update` on each report before publishing, and pushes it back). Schema fnvnx-ratchet/1:
{"schema", "metrics": {name: entry}}. Two kinds of entry:
  value  {"kind": "value", "dir": "higher"|"lower", "best": n, "head": h12 (where best was first reached), "job"}
  items  {"kind": "good", "items": {name: h12 first good}}       items reported good (a milestone reached)
         {"kind": "bad", "bad": [names], "head": h12}            items reported bad (a unit without an object, a
                                                                 failing program): a name bad now and not in the
                                                                 baseline is a regression; the baseline's head is
                                                                 the head that had it good
Every entry may carry "regression": {"head", "value"/"items"} from the last check that found one (the zero table's
regression column counts them); a check without a regression clears it.

What is measured (each from the report itself, or its sidecar; a report that does not say leaves the metric out):
  full      trap stubs (lower; only when the guest linked), trial-link undefined (lower), compile objects (higher),
            units without an object (bad items; only when the list is complete), WinMain entered and the game
            milestones (good items), initializers skipped in the boot (lower)
  equiv     identical functions and identical units of the shard (higher)
  sweep     passing programs of the shard (checked - failing; higher; only when programs were checked) and the
            failing programs (bad items); a shard whose setup failed (sidecar "error") measures nothing
  scenario  each D25 target measured in at least one scenario (lower), per scenario count
  bootloop  the stub boot's game milestones (good items) and their count (higher), "(nulldev)" with FNVNX_STUB_NULLDEV=1
Unreliable boots: a head whose tests/s5/emu_guest.py has S5's timer slices (2d1afaeb..6da559aa: --slice-ms
without --hc-slice) booted nondeterministically; its boot metrics (full: boot / stub boot, bootloop, scenario) are
listed as not compared, and neither advance nor regress the ledger.
Comparison: a report of a head that is an ancestor of an entry's head (an older head, reported late) is not compared
with it. A WIP report (--wip) is compared but never advances the ledger. The ledger advances only on a check with no
regression (owner order): improved values take this head, new items are added, a "bad" baseline shrinks to the
current set. `accept` is the way to take an intended regression (it drops the metric; the next report sets it).

The report (--out, default in place): when there are regressions, a first line "- REGRESSION: <n> items vs <head>"
(the heads that had them good), and at the end a "## REGRESSIONS" section: one line per item (now, best, the head
that first had it good) and the commit range <good head>..<this head> (git log --oneline, from --repo). Exit 0;
2 on a usage or ledger error. Numbers and our own names only.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

SCHEMA = 'fnvnx-ratchet/1'


def num(s):
    s = (s or '').replace(',', '').strip()
    return int(s) if re.fullmatch(r'-?\d+', s) else None


class M:
    """one measured metric of a report"""
    def __init__(self, name, kind, value=None, dir=None, items=None, note=''):
        self.name, self.kind, self.value, self.dir, self.items, self.note = name, kind, value, dir, items, note


# ---- extractors: (report text, sidecar dict or None) -> [M] ---------------------------------------------------------
def x_full(t, side):
    out = []
    g = re.search(r'^\| guest \| (\w+) \|', t, re.M)
    m = re.search(r'^\| undefined symbols \| category \| owner \|\n\|[-|]+\|\n((?:\|.*\|\n)*)', t, re.M)
    if m:
        out.append(M('full: trap stubs', 'value', sum(num(r.split('|')[1]) or 0 for r in m.group(1).splitlines()), 'lower'))
    elif g and g.group(1) in ('ok', 'warn'):
        out.append(M('full: trap stubs', 'value', 0, 'lower'))
    m = re.search(r'trial link: ([\d,]+) objects, ([\d,]+) undefined', t)
    if m:
        out.append(M('full: trial-link undefined', 'value', num(m.group(2)), 'lower'))
    m = re.search(r'^- compile: ([\d,]+) units, ([\d,]+) objects, ([\d,]+) failed', t, re.M)
    if m:
        out.append(M('full: compile objects', 'value', num(m.group(2)), 'higher'))
        failed = num(m.group(3))
        s = re.search(r'^Units without an object[^\n]*\n\n((?:- `[^`]+`.*\n)*)', t, re.M)
        names = re.findall(r'^- `([^`]+)`', s.group(1), re.M) if s else []
        if failed == 0 or (names and len(names) == failed):
            out.append(M('full: units without an object', 'bad', items=sorted(set(names))))
    b = re.search(r'^## Emulated boot\n(.*?)(?=^## |\Z)', t, re.M | re.S)
    if b:
        # the raw boot, then the stub-fault boot (full_report.py stub_boot) as separate metrics; a stub boot run with
        # the null device (FNVNX_STUB_NULLDEV=1, S5 a3b368ed) gets past the renderer, so its milestones are not
        # comparable with one run without: its own metrics, "(nulldev)" in the name
        raw, _, stub = b.group(1).partition('\nStub-fault boot (')
        out += boot_metrics('full: boot', raw)
        if stub:
            nd = ' (nulldev)' if 'FNVNX_STUB_NULLDEV=1' in stub.split('\n', 1)[0] else ''
            out += boot_metrics('full: stub boot' + nd, stub)
    return out


def boot_metrics(pre, bt):
    """WinMain and the game milestones (good items), initializers skipped (lower) of one boot's lines"""
    out, items = [], {}
    w = re.search(r'^- WinMain entered: (yes|no)', bt, re.M | re.I)
    if w:
        items['WinMain entered'] = w.group(1).lower() == 'yes'
    gm = re.search(r'^- game milestones[^:]*: (.*?); last reached', bt, re.M)
    if gm:
        for part in gm.group(1).split(', '):
            p = part.rsplit(' ', 1)
            if len(p) == 2 and p[1] in ('yes', 'no'):
                items[p[0]] = p[1] == 'yes'
    if items:
        out.append(M(pre + ' milestones', 'good', items=items))
    s = re.search(r'initializers skipped after a fault: ([\d,]+)', bt)
    if s:
        out.append(M(pre + ' initializers skipped', 'value', num(s.group(1)), 'lower'))
    return out


def x_equiv(t, side):
    h = re.search(r'^# MSVC equivalence gate - \S+ shard (\d+)/(\d+)', t, re.M)
    if not h:
        return []
    sh = '%sof%s' % h.groups()
    out = []
    f = re.search(r'^- functions: ([\d,]+) compared, ([\d,]+) differ', t, re.M)
    if f:
        out.append(M('equiv %s: identical functions' % sh, 'value', num(f.group(1)) - num(f.group(2)), 'higher'))
    u = re.search(r'^- units: ([\d,]+) compared; ([\d,]+) identical', t, re.M)
    if u:
        out.append(M('equiv %s: identical units' % sh, 'value', num(u.group(2)), 'higher'))
    return out


def x_sweep(t, side):
    j = side or {}
    if not j.get('k') or j.get('error') or 'failing_programs' not in j:
        return []
    sh = '%sof%s' % (j['k'], j.get('n'))
    if not j.get('programs_checked'):
        return []
    bad = sorted({p.get('hash') or p.get('name') for p in j['failing_programs']})
    return [M('sweep %s: passing programs' % sh, 'value', j['programs_checked'] - len(bad), 'higher'),
            M('sweep %s: failing programs' % sh, 'bad', items=bad)]


def x_scenario(t, side):
    j = side or {}
    if j.get('schema') != 'fnvnx-scenario-metric/1' or j.get('error'):
        return []
    out = []
    for n, tg in sorted((j.get('targets') or {}).items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 99):
        if tg and tg.get('measured_in') and tg.get('count') is not None:
            out.append(M('scenario D25 #%s (%s scenarios)' % (n, tg.get('of', '?')), 'value', tg['count'], 'lower'))
    return out


def x_bootloop(t, side):
    """tools/farm/bootloop_job.sh's sidecar (fnvnx-bootloop/1): the stub boot's game milestones (good items) and how
    many were reached (higher), apart for a boot with and without the null device"""
    j = side or {}
    if j.get('schema') != 'fnvnx-bootloop/1' or j.get('error') or not j.get('milestones'):
        return []
    pre = 'bootloop: stub boot%s milestones' % (' (nulldev)' if j.get('nulldev') == '1' else '')
    return [M(pre, 'good', items={n: bool(r) for n, r in j['milestones']}),
            M(pre + ' reached', 'value', j.get('reached'), 'higher')]


EXTRACT = {'bootloop': x_bootloop, 'full': x_full, 'equiv': x_equiv, 'sweep': x_sweep, 'scenario': x_scenario}


# ---- ledger ---------------------------------------------------------------------------------------------------------
def load_ledger(p):
    if not os.path.isfile(p):
        return {'schema': SCHEMA, 'metrics': {}}
    j = json.load(open(p))
    if j.get('schema') != SCHEMA:
        raise ValueError('%s: not %s' % (p, SCHEMA))
    j.setdefault('metrics', {})
    return j


def save_ledger(p, j):
    d = os.path.dirname(os.path.abspath(p))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix='.ledger.')
    with os.fdopen(fd, 'w') as f:
        json.dump(j, f, indent=1, sort_keys=True)
        f.write('\n')
    os.replace(tmp, p)


def git(repo, *a):
    if not repo:
        return None
    r = subprocess.run(['git', '-C', repo] + list(a), capture_output=True, text=True)
    return r


def boot_unreliable(repo, head):
    """why the emulated boots of `head` are not comparable, or None: S5's timer slices (2d1afaeb..6da559aa; an
    emu_guest.py with --slice-ms and without 620572bc's host-call preemption --hc-slice) were nondeterministic
    (unicorn's emu_stop from the timer thread can leave the PC at the block start, a resume re-runs instructions).
    Read from the head's own tests/s5/emu_guest.py, so it holds for the adopted commits of the integration branch."""
    r = git(repo, 'show', '%s:ports/fnv-nx/tests/s5/emu_guest.py' % head)
    if r is None or r.returncode:
        return None
    if '--slice-ms' in r.stdout and '--hc-slice' not in r.stdout:
        return 'the emulated boots of this head ran on timer slices, nondeterministic (S5 2d1afaeb..6da559aa)'
    return None


def is_boot_metric(job, name):
    """the metrics an emulated boot measures: the full report's boots, bootloop's, the scenarios'"""
    return job in ('bootloop', 'scenario') or (job == 'full' and ' boot' in name)


def is_ancestor(repo, a, b):
    """True when commit a is an ancestor of b (or equal); None when unknown"""
    if a == b:
        return True
    r = git(repo, 'merge-base', '--is-ancestor', a, b)
    if r is None or r.returncode not in (0, 1):
        return None
    return r.returncode == 0


def better(dir, a, b):
    return a > b if dir == 'higher' else a < b


def check(ledger, ms, head, job, repo, wip):
    """-> (regressions [(metric, item or None, now, best, good head)], notes, updates [callables])"""
    h12 = head[:12]
    regs, notes, upd, new = [], [], [], []   # upd: advances (only without a regression); new: first entries
    L = ledger['metrics']
    compared = set()
    older = lambda h: h and h[:12] != h12 and is_ancestor(repo, h12, h)    # this head is older than h
    for m in ms:
        if m.kind == 'skip':
            notes.append('%s: not compared (%s)' % (m.name, m.note))
            continue
        e = L.get(m.name)
        if e and e.get('kind') != 'good' and e.get('head') and is_ancestor(repo, h12, e['head']) and h12 != e['head'][:12]:
            notes.append('%s: not compared (this head is older than the ledger\'s %s)' % (m.name, e['head']))
            continue
        compared.add(m.name)
        if m.kind == 'value':
            if m.value is None:
                continue
            if not e or e.get('kind') != 'value':
                new.append(lambda m=m: L.__setitem__(m.name, {'kind': 'value', 'dir': m.dir, 'best': m.value,
                                                              'head': h12, 'job': job}))
            elif better(e['dir'], e['best'], m.value):
                regs.append((m.name, None, m.value, e['best'], e['head']))
            elif better(e['dir'], m.value, e['best']):
                upd.append(lambda m=m, e=e: e.update(best=m.value, head=h12))
        elif m.kind == 'good':
            if not e or e.get('kind') != 'good':
                new.append(lambda m=m: L.__setitem__(m.name, {'kind': 'good', 'job': job, 'items': {
                    k: h12 for k, v in m.items.items() if v}}))
                continue
            for k, v in sorted(m.items.items()):
                if not v and k in e['items'] and older(e['items'][k]):
                    notes.append('%s / %s: not compared (this head is older than %s)' % (m.name, k, e['items'][k]))
                elif not v and k in e['items']:
                    regs.append((m.name, k, 'no', 'yes', e['items'][k]))
                elif v and k not in e['items']:
                    upd.append(lambda e=e, k=k: e['items'].__setitem__(k, h12))
        elif m.kind == 'bad':
            if not e or e.get('kind') != 'bad':
                new.append(lambda m=m: L.__setitem__(m.name, {'kind': 'bad', 'job': job, 'bad': list(m.items),
                                                              'head': h12}))
                continue
            base = set(e['bad'])
            for k in m.items:
                if k not in base:
                    regs.append((m.name, k, 'bad', 'good', e['head']))
            if set(m.items) < base:
                upd.append(lambda m=m, e=e: e.update(bad=list(m.items), head=h12))
    # the regression marks (the zero table's column): set on the metrics that regressed, cleared on the others
    names = compared
    rn = {}
    for r in regs:
        rn.setdefault(r[0], []).append(r)

    def mark():
        for n in names:
            e = L.get(n)
            if not e:
                continue
            if n in rn:
                e['regression'] = {'head': h12, 'items': len(rn[n]), 'wip': wip or None}
            elif e.get('regression') and not older(e['regression']['head']):
                e.pop('regression', None)     # cleared by a report of the same or a newer head
    if wip:              # a WIP report is compared, and changes nothing
        return regs, notes, []
    if regs:             # owner order: the ledger advances only on a check with no regression
        upd = []
    else:
        if not older(ledger.get('head')):
            upd.append(lambda: ledger.__setitem__('head', h12))
    return regs, notes, new + upd + [mark]


def commit_range(repo, good, head):
    if not repo:
        return []
    r = git(repo, 'log', '--oneline', '--no-decorate', '%s..%s' % (good, head))
    if r is None or r.returncode:
        return ['(the range %s..%s is not in %s)' % (good, head[:12], repo)]
    return r.stdout.splitlines()


def section(regs, notes, ms, head, repo, wip):
    L = ['', '## REGRESSIONS', '']
    if not ms:
        L.append('- none: this report has no ratchet metrics')
    elif not regs:
        L.append('- none (%d metrics compared with the ledger%s)' % (len([m for m in ms if m.kind != 'skip']),
                                                                    '; WIP: the ledger is not advanced' if wip else ''))
    for name, item, now, best, gh in regs:
        L.append('- %s%s: %s now, %s at %s (first good there)' % (name, (' / ' + item) if item else '', now, best, gh))
    for n in notes:
        L.append('- note: %s' % n)
    for gh in dict.fromkeys(r[4] for r in regs):
        cs = commit_range(repo, gh, head)
        L.append('')
        L.append('commits %s..%s (%d):' % (gh, head[:12], len(cs)))
        L.append('')
        L.extend('    ' + c for c in cs[:40])
        if len(cs) > 40:
            L.append('    ... %d more' % (len(cs) - 40))
    return '\n'.join(L) + '\n'


def strip_previous(text):
    """the report as the job wrote it: a REGRESSION first line and the REGRESSIONS section of an earlier check
    removed (the runner retries a check when the ledger moved under it)"""
    lines = text.split('\n')
    if lines and lines[0].startswith('- REGRESSION: '):
        lines = lines[1:]
    text = '\n'.join(lines)
    i = text.find('\n\n## REGRESSIONS\n')
    return text[:i + 1] if i >= 0 else text


def cmd_check(a):
    try:
        ledger = load_ledger(a.ledger)
    except (OSError, ValueError) as ex:
        print('ratchet: %s' % ex, file=sys.stderr)
        return 2
    text = strip_previous(open(a.report, errors='replace').read())
    side = None
    if a.sidecar and os.path.isfile(a.sidecar):
        try:
            side = json.load(open(a.sidecar))
        except ValueError:
            side = None
    fn = EXTRACT.get(a.job)
    ms = fn(text, side) if fn else []
    why = boot_unreliable(a.repo, a.head)
    if why:   # the boots of this head ran on a nondeterministic emulator: their numbers neither advance nor regress
        ms = [M(m.name, 'skip', note='unreliable: ' + why) if is_boot_metric(a.job, m.name) else m for m in ms]
    regs, notes, upd = check(ledger, ms, a.head, a.job, a.repo, a.wip)
    if a.update:
        for u in upd:
            u()
        save_ledger(a.ledger, ledger)
    out = text.rstrip('\n') + '\n' + section(regs, notes, ms, a.head, a.repo, a.wip)
    if regs:
        heads = list(dict.fromkeys(r[4] for r in regs))
        out = '- REGRESSION: %d items vs %s\n' % (len(regs), ', '.join(heads)) + out
    open(a.out or a.report, 'w').write(out)
    print('ratchet: %s %s: %d metrics, %d regressions%s' % (a.job, a.head[:12], len(ms), len(regs),
                                                           '' if a.update else ' (ledger not written)'))
    return 0


def cmd_accept(a):
    ledger = load_ledger(a.ledger)
    for n in a.metric:
        if ledger['metrics'].pop(n, None) is None:
            print('ratchet: no metric %r' % n, file=sys.stderr)
    save_ledger(a.ledger, ledger)
    return 0


def cmd_show(a):
    ledger = load_ledger(a.ledger)
    for n, e in sorted(ledger['metrics'].items()):
        if e['kind'] == 'value':
            v = '%s best %s at %s' % (e['dir'], e['best'], e['head'])
        elif e['kind'] == 'good':
            v = '%d good items' % len(e['items'])
        else:
            v = '%d bad at %s' % (len(e['bad']), e['head'])
        print('%s: %s%s' % (n, v, ' REGRESSED at %s' % e['regression']['head'] if e.get('regression') else ''))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('check')
    p.add_argument('--ledger', required=True); p.add_argument('--report', required=True)
    p.add_argument('--job', required=True); p.add_argument('--head', required=True)
    p.add_argument('--sidecar'); p.add_argument('--repo'); p.add_argument('--wip')
    p.add_argument('--out'); p.add_argument('--update', action='store_true')
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser('accept'); p.add_argument('--ledger', required=True); p.add_argument('metric', nargs='+')
    p.set_defaults(fn=cmd_accept)
    p = sub.add_parser('show'); p.add_argument('--ledger', required=True); p.set_defaults(fn=cmd_show)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == '__main__':
    sys.exit(main())
