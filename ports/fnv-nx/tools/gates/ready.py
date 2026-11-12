#!/usr/bin/env python3
"""ready.py - first-launch readiness scorecard (stream R0-gates, docs/READINESS.md).

    tools/gates/ready.py [--artifacts DIR] [--fetch] [--fixtures DIR] [--head COMMIT] [--thresholds FILE]
                         [--md OUT.md] [--json OUT.json]

Reads the farm reports and link artifacts of the private artifacts repository (toadharvard/fnv-nx-build):
branches reports/<farm> and link/<commit12>, plus the sha256 of the owner's exe on gamedata/exe. For every farm it
takes the newest report that parses (newest by publish time), and checks the numbers against the thresholds of
tools/gates/thresholds.json. Every gate gets a value, a threshold, its source (report path and commit, and how far
that commit is behind the integration head) and a status: pass, fail or no-data. A pass whose source is more than
`max_commits_behind` commits behind the head is marked stale. The verdict is READY only when every gate passes on
fresh evidence.

--artifacts   a git directory of the artifacts repository (default $ARTIFACTS, else /home/user/fnv-nx-build); read
              with `git --git-dir`, so a bare partial clone works; blobs are fetched lazily
--fetch       first fetch reports/*, link/* and gamedata/exe into refs/remotes/origin/ (blob:none)
--fixtures    read <DIR>/<branch>/<path> instead (tests); <DIR>/_commits.json maps commit12 -> commits behind head
--head        the integration head (default: HEAD of this checkout); distances come from this checkout's history

Output: the markdown scorecard (stdout unless --md) and a JSON file (--json). Both hold numbers, addresses, rule and
class names and report paths only: no prepared source, no unit text, no game data.
"""
import argparse
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))
PASS, FAIL, NODATA = 'pass', 'fail', 'no-data'


def readf(p):
    with open(p, errors='replace') as f:
        return f.read()


def writef(p, text):
    with open(p, 'w') as f:
        f.write(text)


# ------------------------------------------------------------------------------------------------- stores

class GitStore:
    """branches and files of the artifacts repository, read through `git --git-dir` (refs/remotes/origin/...)."""

    def __init__(self, gitdir):
        self.gitdir = gitdir
        self._files, self._branches = {}, {}

    def git(self, *args, check=False):
        r = subprocess.run(['git', '--git-dir', self.gitdir, *args], capture_output=True, text=True)
        if check and r.returncode:
            raise RuntimeError('git %s: %s' % (' '.join(args), r.stderr.strip()))
        return r.stdout if r.returncode == 0 else None

    def now(self):
        return int(time.time())

    def fetch(self):
        self.git('fetch', '-q', '--filter=blob:none', 'origin',
                 '+refs/heads/reports/*:refs/remotes/origin/reports/*',
                 '+refs/heads/link/*:refs/remotes/origin/link/*',
                 '+refs/heads/gamedata/exe:refs/remotes/origin/gamedata/exe')

    def branches(self, prefix):
        """branch names under refs/remotes/origin/ that start with prefix ('reports/fz' -> reports/fz1, ...)"""
        if prefix not in self._branches:
            d = prefix.rsplit('/', 1)[0] + '/' if '/' in prefix else ''
            out = self.git('for-each-ref', '--format=%(refname)', 'refs/remotes/origin/' + d) or ''
            names = (l[len('refs/remotes/origin/'):] for l in out.split())
            self._branches[prefix] = sorted(n for n in names if n.startswith(prefix))
        return self._branches[prefix]

    def files(self, branch):
        """[(path, published epoch)], newest first: the last commit that touched each file."""
        if branch not in self._files:
            out = self.git('log', '--format=@%ct', '--name-only', 'refs/remotes/origin/' + branch) or ''
            seen, t = {}, 0
            for l in out.splitlines():
                if l.startswith('@'):
                    t = int(l[1:])
                elif l.strip() and l not in seen:
                    seen[l] = t
            self._files[branch] = sorted(seen.items(), key=lambda x: (-x[1], x[0]))
        return self._files[branch]

    def read(self, branch, path):
        return self.git('show', 'refs/remotes/origin/%s:%s' % (branch, path))

    def exists(self, branch, path):
        return subprocess.run(['git', '--git-dir', self.gitdir, 'cat-file', '-e',
                               'refs/remotes/origin/%s:%s' % (branch, path)], capture_output=True).returncode == 0


class DirStore:
    """fixtures: <root>/<branch>/<path>; publish time from <root>/_published.json ({"<branch>/<path>": epoch}),
    else the file's mtime (git does not keep mtimes, so committed fixtures list their order there)."""

    def __init__(self, root):
        self.root = root
        pj = os.path.join(root, '_published.json')
        self.published = json.loads(readf(pj)) if os.path.isfile(pj) else {}

    def fetch(self):
        pass

    def now(self):
        """fixtures: the newest publish time stands for now (deterministic)"""
        return max(self.published.values()) if self.published else int(time.time())

    def branches(self, prefix):
        out = []
        for dp, dn, fn in os.walk(self.root):
            rel = os.path.relpath(dp, self.root)
            if rel.startswith(prefix) and rel.count('/') == prefix.count('/'):
                out.append(rel)
        return sorted(out)

    def files(self, branch):
        base = os.path.join(self.root, branch)
        res = []
        for dp, _, fn in os.walk(base):
            for n in fn:
                p = os.path.join(dp, n)
                rel = os.path.relpath(p, base)
                res.append((rel, int(self.published.get('%s/%s' % (branch, rel), os.path.getmtime(p)))))
        return sorted(res, key=lambda x: (-x[1], x[0]))

    def read(self, branch, path):
        p = os.path.join(self.root, branch, path)
        return readf(p) if os.path.isfile(p) else None

    def exists(self, branch, path):
        return os.path.isfile(os.path.join(self.root, branch, path))


class History:
    """commits behind the integration head; negative: that many commits ahead of it (a farm ran a newer commit);
    None: unknown, or on another line of history."""

    def __init__(self, head=None, table=None, repo=PORT):
        self.table, self.repo, self.cache = table, repo, {}
        self.head = head or (None if table is not None else self._git('rev-parse', 'HEAD'))
        if table is None and self.head:      # a short or symbolic head: the full commit, so distances can be counted
            self.head = self._git('rev-parse', '--verify', '-q', self.head + '^{commit}') or self.head

    def _git(self, *a):
        r = subprocess.run(['git', '-C', self.repo, *a], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None

    patch_root = None           # fixtures: <dir>/<commit12>/*.s1b stands for patches/ at that commit

    def patch_sites(self, commit):
        """the S1b sites (src, caller address) of patches/*.s1b at commit; None when that commit is unknown"""
        if not commit:
            return None
        if self.table is not None:
            d = os.path.join(self.patch_root or '', commit[:12])
            if not (self.patch_root and os.path.isdir(d)):
                return None
            return patch_sites(readf(os.path.join(d, f)) for f in sorted(os.listdir(d)) if f.endswith('.s1b'))
        c = self._git('rev-parse', '--verify', '-q', commit + '^{commit}')
        if not c:
            return None
        names = self._git('ls-tree', '--name-only', c, 'patches/')
        if names is None:
            return None
        return patch_sites(self._git('show', '%s:./%s' % (c, n)) or '' for n in names.split() if n.endswith('.s1b'))

    def behind(self, commit):
        if not commit:
            return None
        c12 = commit[:12]
        if self.table is not None:
            return self.table.get(c12)
        if c12 not in self.cache:
            n = None
            if subprocess.run(['git', '-C', self.repo, 'merge-base', '--is-ancestor', c12, self.head],
                              capture_output=True).returncode == 0:
                v = self._git('rev-list', '--count', '%s..%s' % (c12, self.head))
                n = int(v) if v and v.isdigit() else None
            elif subprocess.run(['git', '-C', self.repo, 'merge-base', '--is-ancestor', self.head, c12],
                                capture_output=True).returncode == 0:
                v = self._git('rev-list', '--count', '%s..%s' % (self.head, c12))
                n = -int(v) if v and v.isdigit() else None
            self.cache[c12] = n
        return self.cache[c12]


# ------------------------------------------------------------------------------------------------- parsers

def patch_sites(texts):
    """(src, caller address) of every `@@ RULE src 0xADDR` hunk (patches/*.s1b, as tests/s1b/pop/abiclass.py)"""
    out = set()
    for t in texts:
        for m in re.finditer(r'^@@ \w+ (\S+) (0x[0-9a-fA-F]+)', t or '', re.M):
            out.add((m.group(1), int(m.group(2), 16)))
    return out


ABICLASS_RANK = ['fix-other', 'fix-donor', 'ecx', 'bridge-gap', 'other', 'patched', 'benign-more', 'benign-unread',
                 'benign-nocall', 'benign-conv']        # tests/s1b/pop/abiclass.py: a pair's verdict is its worst call's


def parse_abiclass(text):
    """S1b's abiclass.py table (#verdict boot src caller callee ref_msvc abi_verdict classes) -> {(abi verdict,
    src, callee, ref): [(verdict, caller address or None)]}; None when it is not that table"""
    if not text or not text.startswith('#verdict\tboot\tsrc\tcaller'):
        return None
    out = {}
    for l in text.splitlines()[1:]:
        f = l.split('\t')
        if len(f) < 8 or l.startswith('#'):
            continue
        caller = int(f[3], 16) if re.fullmatch(r'[0-9a-fA-F]+', f[3]) else None
        out.setdefault((f[6], f[2], f[4], f[5]), []).append((f[0], caller))
    return out


def num(s):
    return int(s.replace(',', '').replace(' ', ''))


def first(rx, text, conv=num, flags=0):
    m = re.search(rx, text, flags)
    return conv(m.group(1)) if m else None


def report_commit(text, path):
    c = first(r'commit:? `?([0-9a-f]{40})', text, str) or first(r'^- commit ([0-9a-f]{40})', text, str, re.M)
    if c:
        return c
    m = re.match(r'(?:.*/)?([0-9a-f]{12})-', path)
    return m.group(1) if m else None


def failed_report(text):
    return bool(re.match(r'# \S+ \S+ FAILED', text or ''))


def shard_of(d, n):
    """a sharded report counts only as shard k of exactly n, 1 <= k <= n: "shard 3/2" is no shard of 2, so it can
    neither stand in for a missing one nor make the count of reported shards look complete"""
    return d['n'] == n and 1 <= d['k'] <= n


def parse_census(text):
    m = re.search(r'units: (\d+)\s+clean: (\d+)\s+failing: (\d+)', text)
    if not m:
        return None
    return dict(units=int(m.group(1)), clean=int(m.group(2)), failing=int(m.group(3)),
                errors=first(r'errors total: (\d+)', text))


def parse_compile(text):
    m = re.search(r'^# compile \S+ shard (\d+)/(\d+)', text, re.M)
    ok = first(r'^- ok (\d+), failed \d+', text, flags=re.M)
    if not m or ok is None:
        return None
    return dict(k=int(m.group(1)), n=int(m.group(2)), ok=ok, failed=first(r'^- ok \d+, failed (\d+)', text, flags=re.M))


def parse_link_text(text):
    """the link numbers of a link_job / full_job report"""
    d = {}
    m = re.search(r'trial link: (\d[\d,]*) objects, (\d[\d,]*) undefined(?: names)?, (\d[\d,]*) multiple definitions', text)
    if not m:
        return None
    d['objects'], d['undefined'], d['multidef'] = num(m.group(1)), num(m.group(2)), num(m.group(3))
    m = re.search(r'split-global check:? (\d[\d,]*) objects / (\d[\d,]*) relocations', text)
    if m:
        d['split_objects'], d['split_relocs'] = num(m.group(1)), num(m.group(2))
    d['apply_failures'] = first(r'apply failures (\d[\d,]*)', text)
    d['x86_inconsistent'] = first(r'x86-inconsistent (\d[\d,]*)', text)
    d['abi_manual'] = first(r'ABI manual (\d[\d,]*)', text)
    d['bridges'] = first(r'(\d[\d,]*) bridges', text)
    d['classes'] = {k.strip(): num(v) for k, v in re.findall(r'^\| ([^|]+?) \| (\d[\d,]*) \|$', text, re.M)
                    if not k.startswith('trial link')} if 'undefined names by class' in text else {}
    return d


def parse_full(text):
    d = dict(link=parse_link_text(text) or {})
    d['stages'] = {m.group(1): m.group(2) for m in re.finditer(r'^\| (\w+) \| (ok|warn|fail|skipped|disabled|never)'
                                                                 r' \|', text, re.M)}
    if 'boot' not in d['stages'] and 'guest' not in d['stages']:
        return None
    d['guest_undefined'] = first(r'(\d[\d,]*) undefined symbols bound to trap stubs', text)
    d['winmain'] = {'yes': 1, 'no': 0}.get(first(r'WinMain entered: (yes|no)', text, str))
    d['image'] = first(r'^- data image: (.+)$', text, str, re.M)
    d['init_skips'] = first(r'initializers skipped after a fault: (\d[\d,]*)', text)
    m = re.search(r'trapped keys (\d[\d,]*) \((\d[\d,]*) hits\)', text)
    d['trap_keys'], d['trap_hits'] = (num(m.group(1)), num(m.group(2))) if m else (None, None)
    d['stop'] = first(r'^- stop: (.+)$', text, str, re.M)
    d['exefs'] = re.findall(r'(\S+)\s+offset=0x[0-9a-f]+ size=0x[0-9a-f]+', text)
    d['guest_in_sd'] = {'yes': 1, 'no': 0}.get(first(r'guest\.nso in the SD tree: (yes|no)', text, str))
    d['program_id'] = first(r'for program id ([0-9a-f]{16})', text, str)
    d['pcrel_abs'] = first(r'PC-relative absolute references (\d[\d,]*)', text)
    d['check_package_rc'] = first(r'check_package: exit (\d+)', text)
    d['v1'] = {m.group(1): int(m.group(2)) for m in re.finditer(r'^\| ([a-z0-9][\w-]*) \| (\d+) \| \d+ \|', text, re.M)}
    return d


def parse_equiv(text):
    m = re.search(r'shard (\d+)/(\d+)', text.splitlines()[0] if text else '')
    u = re.search(r'units: (\d+) compared; (\d+) identical, (\d+) differ, (\d+) prepared side does not compile, '
                  r'(\d+) pristine does not compile', text)
    f = re.search(r'functions: (\d+) compared, (\d+) differ \(EH-only (\d+), retarget (\d+), code (\d+)\)', text)
    if not (m and u and f):
        return None
    d = dict(k=int(m.group(1)), n=int(m.group(2)), units=int(u.group(1)), identical=int(u.group(2)),
             differ=int(u.group(3)), nobuild=int(u.group(4)), nobuild_pristine=int(u.group(5)),
             functions=int(f.group(1)), fn_differ=int(f.group(2)), eh=int(f.group(3)), retarget=int(f.group(4)),
             code=int(f.group(5)), code_fns=[], nobuild_rules=[])
    sec = None
    for l in text.splitlines():
        if l.startswith('## '):
            sec = 'fns' if l.startswith('## Differing functions') else 'nb' if 'does not compile' in l else None
            continue
        p = re.split(r'\s{2,}', l.strip())
        if sec == 'fns' and len(p) >= 4 and re.fullmatch(r'0x[0-9a-f]{8}', p[1]):
            if p[2] == 'code':
                d['code_fns'].append((p[1][2:], p[3]))
        elif sec == 'nb' and len(p) >= 2 and not l.startswith('```'):
            d['nobuild_rules'].append(p[1])
    return d


def parse_static(text):
    m = re.search(r'^# static gate \S+, shard (\d+) of (\d+)', text, re.M)
    if not m:
        return None
    d = dict(k=int(m.group(1)), n=int(m.group(2)), exit=first(r'gate exit code: (\d+)', text),
             units=first(r'units in the shard: (\d+)', text), gcc=first(r'GCC compiled: (\d+)', text),
             classes={}, sites=set())
    sec = None
    for l in text.splitlines():
        if l.startswith('## '):
            sec = 'classes' if l.startswith('## Sites per class') else 'sites' if l.startswith('## Sites (') else None
            continue
        if sec == 'classes':
            mm = re.match(r'^\| ([A-Z0-9][A-Z0-9-]*) \| (\d+) \|$', l)
            if mm:
                d['classes'][mm.group(1)] = int(mm.group(2))
        elif sec == 'sites':
            p = l.split()
            if len(p) == 3 and p[1].isdigit():
                d['sites'].add((p[0], int(p[1]), p[2]))
    return d


def fuzz_summary(text):
    """the report's ```json fnvnx-fuzz-summary``` block (the same object as the
    <head12>-fuzz.json beside it); None when absent, {} when present but unreadable"""
    m = re.search(r'^```json fnvnx-fuzz-summary\n(.*?)\n```', text or '', re.M | re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(1))
    except ValueError:
        return {}
    return d if isinstance(d, dict) and d.get('format') == 'fnvnx-fuzz-summary/1' else {}


def parse_fuzz(text):
    m = re.search(r'^# fuzz \S+ shard (\d+)/(\d+)(.*)$', text, re.M)
    if not m or 'HARNESS BROKEN' in m.group(3):
        return None         # a broken harness's verdicts are not meaningful
    d = dict(k=int(m.group(1)), n=int(m.group(2)), catalogued=first(r'functions catalogued (\d+)', text),
             shard=first(r'in this shard (\d+)', text), no_native=first(r'no native object (\d+)', text),
             not_modelled=first(r'control flow not modelled[^)]*\) (\d+)', text),
             fuzzable=first(r'fuzzable (\d+)', text), verdicts=first(r'cumulative \(verdicts valid for this head\): '
                                                                    r'(\d+) functions', text),
             pending=first(r'pending (\d+)', text), crash=first(r'fuzzed this head: crash (\d+)', text), hard=[])
    sec = False
    for l in text.splitlines():
        if l.startswith('## '):
            sec = l.startswith('## Hard divergences, cumulative')
            continue
        mm = re.match(r'^\| (\*?)([0-9a-f]{8}) \| ([^|]+?) \| ([^|]+?) \| ([^|]+?) \|', l) if sec else None
        if mm:
            d['hard'].append((mm.group(2), mm.group(3), mm.group(5)))
    d['causes'] = causes_block(text)
    d['with_obj'] = (d['shard'] or 0) - (d['no_native'] or 0)
    d['status'] = None
    sm = fuzz_summary(text)
    if sm is not None:
        # fnvnx-fuzz-summary/1: it must describe this shard, and a harness-broken run is no evidence
        fn = dct(sm.get('functions'))
        if not sm or (sm.get('shard'), sm.get('shards')) != (d['k'], d['n']) or sm.get('status') == 'harness-broken':
            return None
        if isinstance(fn.get('with_native_object'), int) and isinstance(fn.get('verdicts'), int):
            d['with_obj'], d['verdicts'] = fn['with_native_object'], fn['verdicts']
        if isinstance(fn.get('not_modelled'), int):
            d['not_modelled'] = fn['not_modelled']
        d['status'] = sm.get('status')
        d['summary_commit'] = sm.get('commit')
    return d


def causes_block(text):
    """the ```json fnvnx-causes``` block of a fuzz report (S13b), or a fnvnx-causes/1 JSON
    file (causes.py summary / merge); None when absent or unreadable"""
    i = (text or '').find('```json fnvnx-causes')
    if i >= 0:
        i = text.find('\n', i) + 1
        j = text.find('```', i)
        text = text[i:j if j >= 0 else len(text)]
    elif not (text or '').lstrip().startswith('{'):
        return None
    try:
        d = json.loads(text)
    except ValueError:
        return None
    return d if isinstance(d, dict) and isinstance(d.get('causes'), dict) else None


def causes_shards(d):
    """the fuzz shards a causes.py summary covers, from its campaign directories (c<k>, <k>of<N>); None: unknown"""
    ks = set()
    for c in d.get('campaigns') or []:
        m = re.search(r'(?:^|[/_-])c(\d+)$|(\d+)of\d+$', str(c).rstrip('/'))
        if m:
            ks.add(int(m.group(1) or m.group(2)))
    return ks or None


def parse_frameprof(text):
    """tools/perf/frameprof.py --json: [{label, instructions, subsystems, ...}]"""
    try:
        res = json.loads(text)
    except ValueError:
        return None
    if not isinstance(res, list):
        return None
    res = [r for r in res if isinstance(r, dict)]
    ins = lambda r: r.get('instructions')
    # a frame or a phase is a measurement only with a positive instruction count: 0, a negative or a missing count
    # is a broken profile (no frame time is ever derived from it), listed in `bad`
    bad = ['%s: %r' % (str(r.get('label', '?'))[:40], ins(r)) for r in res if not posnum(ins(r))]
    frames = [ins(r) for r in res if str(r.get('label', '')).startswith('frame ')]
    to_winmain, seen = 0, False
    for r in res:
        to_winmain += ins(r) if isnum(ins(r)) else 0
        if re.search(r'until (__fnv_0086a850|WinMain)', str(r.get('label', ''))):
            seen = True
            break
    return dict(frames=frames, to_winmain=to_winmain if seen else None, bad=bad,
                total=sum(ins(r) for r in res if isnum(ins(r))))


def isnum(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def posnum(v):
    """a finite number > 0: a time, an instruction count, a budget fraction"""
    return isnum(v) and v > 0


def dct(v):
    """v when it is a JSON object, else {}: a producer's file of the wrong shape reads as empty, never crashes"""
    return v if isinstance(v, dict) else {}


def bound(v, key='gate'):
    """a render-report value: a number, or a range {lo, hi, gate}; the gate value (= hi) by default"""
    if isinstance(v, dict):
        return v.get(key, v.get('hi'))
    return v


def bad_bounds(v, what):
    """why a render-report value (a number or a {lo, hi, gate} range) is not a plausible time: [] when it is"""
    if v is None:
        return []
    vals = v if isinstance(v, dict) else {'value': v}
    bad = ['%s.%s %r' % (what, k, x) for k, x in vals.items() if k in ('lo', 'hi', 'gate', 'mid', 'value')
           and not posnum(x)]
    if not bad and isinstance(v, dict) and isnum(v.get('lo')) and isnum(v.get('hi')) and v['lo'] > v['hi']:
        bad.append('%s lo %s > hi %s' % (what, v['lo'], v['hi']))
    return bad


def render_insane(j):
    """why the render report's summary is not a measurement: every frame time it gives (frame_ms main / render,
    lo, hi, gate) must be a positive finite number with lo <= hi, and every budget fraction positive. A report that
    contradicts itself cannot pass a gate through its other numbers. [] when plausible"""
    bad = []
    fm = j.get('frame_ms')
    if fm is not None and not isinstance(fm, dict):
        bad.append('frame_ms %r' % (fm,))
    for k, v in dct(fm).items():
        bad += bad_bounds(v, 'frame_ms.' + k)
    tbf = j.get('targets_budget_fraction')
    if tbf is not None and not isinstance(tbf, dict):
        bad.append('targets_budget_fraction %r' % (tbf,))
    for fps, row in dct(tbf).items():
        if not isinstance(row, dict):
            bad.append('targets_budget_fraction.%s %r' % (fps, row))
            continue
        # clock keys ("1.02") are what the gate reads; other numeric keys (gpu_*, frame_*) must still be positive
        bad += ['targets_budget_fraction.%s.%s %r' % (fps, k, v) for k, v in row.items()
                if (re.fullmatch(r'\d+(\.\d+)?', str(k)) or isinstance(v, (int, float))) and not posnum(v)]
    return bad


def fmt_ms(v):
    return '%.2f' % v if isnum(v) else '?'


def placement_check(port):
    """core placement from toolchain/host/placement.h and the NPDM: main on core 0, workers on 1-2, core 3 opt-in"""
    ph = os.path.join(port, 'toolchain', 'host', 'placement.h')
    npdm = os.path.join(port, 'toolchain', 'npdm', 'fnvnx-app.json')
    if not (os.path.isfile(ph) and os.path.isfile(npdm)):
        return None
    t = readf(ph)
    rows = {m.group(1): (int(m.group(2)), int(m.group(3), 0), int(m.group(4), 0)) for m in re.finditer(
        r'=\s*\{"([\w-]+)",\s*(-?\d+),\s*(0x[0-9A-Fa-f]+|\d+),\s*(0x[0-9A-Fa-f]+|\d+),', t)}
    core3 = first(r'#define FNVNX_CORE3_DEFAULT (\d+)', t)
    j = json.loads(readf(npdm))
    caps = {c['type']: c['value'] for c in j.get('kernel_capabilities', [])}
    kf = caps.get('kernel_flags', {})
    problems = []
    main = rows.get('main')
    if not main or main[0] != 0 or main[1] != 0x1:
        problems.append('main thread not pinned to core 0 (%s)' % (main,))
    for role, (ideal, mask, mask3) in rows.items():
        if role == 'main':
            continue
        if mask & ~0x6:
            problems.append('%s may run on core %s (mask %#x)' % (role, '0' if mask & 1 else '3', mask))
        if ideal not in (-1, 1, 2):
            problems.append('%s ideal core %d' % (role, ideal))
        if mask3 and mask3 & ~0xE:
            problems.append('%s opt-in mask %#x reaches core 0' % (role, mask3))
    if core3 != 0:
        problems.append('core 3 is on by default (FNVNX_CORE3_DEFAULT=%s)' % core3)
    if j.get('default_cpu_id') != 0:
        problems.append('NPDM default_cpu_id %s' % j.get('default_cpu_id'))
    if kf.get('highest_cpu_id') != 3 or kf.get('lowest_cpu_id') != 0:
        problems.append('NPDM cores %s..%s (want 0..3 so that the opt-in can work)' % (kf.get('lowest_cpu_id'),
                                                                                   kf.get('highest_cpu_id')))
    return dict(roles=len(rows), problems=problems,
                workers=sorted(r for r in rows if r != 'main'))


def by_design_patch_addrs(port):
    """addresses of docs/PATCHES.md "By-design MSVC differences (S11 gate)" (S1b sites whose MSVC code differs)"""
    p = os.path.join(port, 'docs', 'PATCHES.md')
    if not os.path.isfile(p):
        return set()
    t = readf(p)
    m = re.search(r'^## By-design MSVC differences.*?(?=^## )', t, re.M | re.S)
    return set(re.findall(r'\| 0x([0-9a-f]{8}) \|', m.group(0))) if m else set()


def load_module(port, rel, name):
    """a tool of the checkout as a module (its own PORT is then that checkout); None when absent or broken"""
    p = os.path.join(port, rel)
    if not os.path.isfile(p):
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:          # noqa: BLE001 - a broken tool must not hide the other gates
        return None
    return mod


def static_classifier(port):
    """tools/static/report.py of the checkout: (class -> verdict of CLASSES (benign, per-site, rule, header, runtime,
    compiler, flag), class -> what closes it in the current build (CLOSED: flags, rules and build_closures(), which
    reads compile.sh, fnvnx_compat.h and sync.cpp, so a class counts closed only while its mechanism is in place))"""
    mod = load_module(port, os.path.join('tools', 'static', 'report.py'), 'fnvnx_static_report')
    if mod is None:
        return {}, {}
    try:
        verdicts = {c[0]: c[3] for c in mod.CLASSES}
    except Exception:          # noqa: BLE001
        verdicts = {}
    closed = getattr(mod, 'CLOSED', None)
    return verdicts, dict(closed) if isinstance(closed, dict) else {}


def static_verdicts(port):
    return static_classifier(port)[0]


# ------------------------------------------------------------------------------------------------- scorecard

class Card:
    def __init__(self, store, hist, th, port=PORT, render=None, abiclass_dir=None):
        self.store, self.hist, self.th, self.port = store, hist, th, port
        self.render = render or {}      # load_render(): text, branch, path, hist (distances on the render branch)
        self.gates = []
        self.boot_info = {}             # S5's boot/milestones.json beyond the milestones: image, peak heap, stub
        self.maxb = th.get('max_commits_behind', 25)
        self._now = None
        self.abiclass_dir = abiclass_dir        # the gates job's abiclass tables, <c12>.tsv (decomp-derived: never published)

    # ---- sources
    def src(self, branch, path, commit, published=None):
        b = self.hist.behind(commit)
        s = dict(branch=branch, path=path, commit=(commit or '')[:12], behind=b, published=published,
                 stale=b is None or b > self.maxb)      # b < 0: newer than the head, fresh
        due = self.th.get('report_due_s')
        if due and b and b > 0 and published and self.store_now() - published > due:
            # the head moved on and this farm has published nothing newer for longer than a report takes: overdue
            s.update(stale=True, overdue=True)
        return s

    def store_now(self):
        if self._now is None:
            self._now = self.store.now() if hasattr(self.store, 'now') else int(time.time())
        return self._now

    def report_branches(self, prefix=''):
        """reports/<prefix>* branches, try farms left out: they run agents' unmerged wip heads (push_wip.sh), so
        nothing they publish is evidence for the integration head (V1 R14)"""
        return [br for br in self.store.branches('reports/' + prefix)
                if not re.match(r'try\d+of\d+$', br.split('/', 1)[1])]

    def newest(self, prefix, pattern, parser, accept=lambda d: True):
        """{farm: (parsed, source)}: per farm branch under reports/<prefix>, the newest report matching pattern
        that parses and is accepted"""
        out = {}
        for br in self.report_branches(prefix):
            farm = br.split('/', 1)[1]
            newer = None                # a newer report of this farm that FAILED or does not parse
            for path, t in self.store.files(br):
                if not re.search(pattern, path):
                    continue
                text = self.store.read(br, path)
                if not text or failed_report(text):
                    newer = newer or path
                    continue
                d = parser(text)
                if d is None:
                    newer = newer or path
                    continue
                if not accept(d):
                    continue            # another shard count: not this farm's newer run of the same job
                d['_text_commit'] = report_commit(text, path)
                s = self.src(br, path, d['_text_commit'], t)
                if newer:
                    # the farm ran again and that run failed: this older pass is not today's evidence
                    s.update(stale=True, superseded_by=newer)
                out[farm] = (d, s)
                break
        return out

    def gate(self, gid, group, title, value, threshold, status, sources, note='', rank=50, **extra):
        """extra: required=False marks an informative gate (shown, not counted for the verdict); levels=..."""
        srcs = sources if isinstance(sources, list) else [sources] if sources else []
        stale = any(s['stale'] for s in srcs) if srcs else False
        note = re.sub(r':\s*(?=;|\)|$)', ': none', note or '')       # an empty "open: ..." list
        self.gates.append(dict(id=gid, group=group, title=title, value=value, threshold=threshold, status=status,
                               stale=stale and status == PASS, sources=srcs, note=note, rank=rank,
                               required=extra.pop('required', True), **extra))

    def nodata(self, gid, group, title, threshold, note, rank=50, sources=None, **extra):
        self.gate(gid, group, title, None, threshold, NODATA, sources or [], note, rank, **extra)

    # ---- frame-rate levels (thresholds perf.fps, perf.clocks)
    def fps_threshold(self):
        f = self.th['perf']['fps']
        return 'target %d fps (%.1f ms, worst frame); floor %d fps (%.1f ms); %d fps (%.1f ms) informational' % (
            f['target'], 1000.0 / f['target'], f['floor'], 1000.0 / f['floor'], f['info'], 1000.0 / f['info'])

    @staticmethod
    def level_name(lv):
        return ('fail' if not lv['floor'] else 'below info' if not lv['info'] else
                'below target' if not lv['target'] else 'target')

    def fps_levels(self, worst_ms, median_ms=None):
        """{floor, info, target: bool}, the level name, and the status: pass only at the target. Every level is
        judged on the worst frame (J-0150: 60 fps whole frame at the 1.02 GHz retail clock)"""
        f = self.th['perf']['fps']
        lv = {k: worst_ms <= 1000.0 / f[k] for k in ('floor', 'info', 'target')}
        return lv, self.level_name(lv), PASS if lv['target'] else FAIL

    def fps_note(self, lv, name):
        f = self.th['perf']['fps']
        return 'level %s: target %d fps %s, floor %d fps %s, %d fps (informational) %s' % (
            name, f['target'], 'yes' if lv['target'] else 'no', f['floor'], 'yes' if lv['floor'] else 'NO',
            f['info'], 'yes' if lv['info'] else 'no')

    # ---- link branch helpers
    def link_branch(self, commit):
        b = 'link/' + (commit or '')[:12]
        return b if commit and b in self.store.branches('link/') else None

    def link_json(self, branch, path):
        t = self.store.read(branch, path) if branch else None
        try:
            d = json.loads(t) if t else None
        except ValueError:
            return None
        return d if isinstance(d, dict) else None       # meta.json and boot/milestones.json are JSON objects

    # ---- gates
    def compile_gates(self):
        units = self.th['units']
        c = self.newest('farm1', r'-census\.md$', parse_census)
        if 'farm1' in c:
            d, s = c['farm1']
            self.gate('compile.census', 'Compile', 'census: units that compile clean', '%d/%d' % (d['clean'], d['units']),
                      '%d/%d' % (units, units), PASS if d['clean'] == d['units'] == units else FAIL, s,
                      '%d failing, %s errors' % (d['failing'], d['errors']), rank=12, errors=d['errors'],
                      failing=d['failing'])
        else:
            self.nodata('compile.census', 'Compile', 'census: units that compile clean', '%d/%d' % (units, units),
                        'no census report on reports/farm1', rank=12)
        n = self.th['compile_shards']
        shards = {}
        for farm, (d, s) in self.newest('farm', r'-compile\.md$', parse_compile, lambda d: shard_of(d, n)).items():
            if d['k'] not in shards or (s['published'] or 0) > (shards[d['k']][1]['published'] or 0):
                shards[d['k']] = (d, s)
        if len(shards) == n:
            ok = sum(d['ok'] for d, _ in shards.values())
            failed = sum(d['failed'] for d, _ in shards.values())
            self.gate('compile.objects', 'Compile', 'objects built for the link (compile shards)',
                      '%d/%d' % (ok, ok + failed), '%d/%d' % (units, units), PASS if failed == 0 and ok == units else FAIL,
                      [v[1] for _, v in sorted(shards.items())], '%d units without an object' % failed, rank=13)
        else:
            self.nodata('compile.objects', 'Compile', 'objects built for the link (compile shards)',
                        '%d/%d' % (units, units), 'compile shards with a report: %d of %d' % (len(shards), n), rank=13,
                        sources=[v[1] for _, v in sorted(shards.items())])

    def pick_link(self):
        """the link evidence closest to the head: link1's report or a full report, with its link/<c12> branch"""
        cands = []
        for farm, (d, s) in self.newest('link', r'-link\.md$', parse_link_text).items():
            cands.append(('link', d, s))
        for farm, (d, s) in self.newest('full', r'-full\.md$', parse_full).items():
            if d.get('link'):
                cands.append(('full', d['link'], s))
        if not cands:
            return None, None
        cands.sort(key=lambda x: (x[2]['behind'] if x[2]['behind'] is not None else 1 << 30, -(x[2]['published'] or 0)))
        return cands[0][1], cands[0][2]

    def link_gates(self):
        L, s = self.pick_link()
        self.full = self.newest('full', r'-full\.md$', parse_full)
        if not L:
            for gid, t in (('link.undefined', 'trial link: undefined names'), ('link.multidef', 'multiple definitions'),
                           ('link.split_global', 'split-global references')):
                self.nodata(gid, 'Link', t, '0', 'no link report', rank=10)
        else:
            cls = ', '.join('%s %d' % kv for kv in L.get('classes', {}).items())
            self.gate('link.undefined', 'Link', 'trial link: undefined names that matter', L['undefined'], '0',
                      PASS if L['undefined'] == 0 else FAIL, s, cls, rank=9)
            self.gate('link.multidef', 'Link', 'multiple definitions', L['multidef'], '0',
                      PASS if L['multidef'] == 0 else FAIL, s, rank=9)
            sg = (L.get('split_objects'), L.get('split_relocs'))
            self.gate('link.split_global', 'Link', 'split-global check (objects / relocations)', '%s / %s' % sg,
                      '0 / 0', PASS if sg == (0, 0) else NODATA if None in sg else FAIL, s,
                      '' if L.get('apply_failures') is None else 'apply failures %s' % L['apply_failures'], rank=9)
        # traps and audits come from the newest full build (its guest)
        fb = sorted(self.full.values(), key=lambda x: (x[1]['behind'] if x[1]['behind'] is not None else 1 << 30))
        if fb and fb[0][0].get('guest_undefined') is not None:
            d, fs = fb[0]
            meta = self.link_json(self.link_branch(d['_text_commit']), 'meta.json') or {}
            tr = meta.get('guest_undefined', d['guest_undefined'])
            self.gate('link.traps', 'Link', 'guest symbols bound to trap stubs', tr, '0', PASS if tr == 0 else FAIL, fs,
                      'boot trap hits: %s keys, %s hits' % (d.get('trap_keys'), d.get('trap_hits')), rank=5)
        else:
            self.nodata('link.traps', 'Link', 'guest symbols bound to trap stubs', '0', 'no full build report', rank=5)
        # the newest full report that ran the audits (a full build without link outputs skips them)
        fv = sorted(self.newest('full', r'-full\.md$', parse_full, lambda d: bool(d.get('v1'))).values(),
                    key=lambda x: (x[1]['behind'] if x[1]['behind'] is not None else 1 << 30))
        if fv:
            d, fs = fv[0]
            bad = sorted(k for k, v in d['v1'].items() if v)
            self.gate('link.v1', 'Link', 'V1-link audits failing (D14)', '%d of %d' % (len(bad), len(d['v1'])), '0',
                      PASS if not bad else FAIL, fs, ', '.join(bad), rank=20)
        else:
            self.nodata('link.v1', 'Link', 'V1-link audits failing (D14)', '0', 'no V1 audit table in a full report',
                        rank=20)
        return L, s

    def abi_gates(self, L, s):
        br = self.link_branch(s['commit']) if s else None
        tsv = self.store.read(br, 'out/abi.tsv') if br else None
        if not tsv:
            # the link report's branch was pruned or has no table: the kept link branch closest to the head that has one
            near = sorted((self.hist.behind(x[5:]) if self.hist.behind(x[5:]) is not None else 1 << 30, x)
                          for x in self.store.branches('link/') if self.store.exists(x, 'out/abi.tsv'))
            if near:
                br = near[0][1]
                tsv = self.store.read(br, 'out/abi.tsv')
                s = self.src(br, 'out/abi.tsv', br[5:])
        explained = set(self.th.get('abi_explained_classes', []))
        if tsv:
            rows = [l.split('\t') for l in tsv.splitlines() if l and not l.startswith('#')]
            xinc = [r for r in rows if len(r) > 6 and r[5] == 'x86-inconsistent']
            un = [r for r in xinc if not set(re.sub(r'\([^)]*\)', '', r[6]).split(',')) <= explained]
            manual = [r for r in rows if len(r) > 6 and r[5] == 'manual']
            un, by_verdict, ac_src, ac_note = self.abiclass_explained(br, un)
            cl = {}
            for r in un:
                for c in re.sub(r'\([^)]*\)', '', r[6]).split(','):
                    if c not in explained:
                        cl[c] = cl.get(c, 0) + 1
            top = ', '.join('%s %d' % kv for kv in sorted(cl.items(), key=lambda x: -x[1])[:5])
            asrc = dict(s, branch=br, path='out/abi.tsv')
            by_class = len(xinc) - len(un) - sum(v for k, v in by_verdict.items() if k in self.abi_verdicts())
            self.gate('abi.x86_inconsistent', 'ABI', 'S2 x86-inconsistent pairs not explained',
                      len(un), '0', PASS if not un else FAIL, [asrc] + ([ac_src] if ac_src else []),
                      '%d x86-inconsistent; explained: %d by class (%s), %d by S1b abiclass verdicts (%s); open: %s; '
                      'open by abiclass verdict: %s' % (
                          len(xinc), by_class, '+'.join(sorted(explained)) or '-',
                          sum(v for k, v in by_verdict.items() if k in self.abi_verdicts()),
                          ac_note or ', '.join('%s %d' % (k, by_verdict[k]) for k in sorted(by_verdict)
                                               if k in self.abi_verdicts()) or 'none', top,
                          ', '.join('%s %d' % kv for kv in sorted(by_verdict.items(), key=lambda x: (-x[1], x[0]))
                                    if kv[0] not in self.abi_verdicts()) or '-'),
                      rank=6, abiclass=by_verdict)
            self.gate('abi.manual', 'ABI', 'S2 pairs with a manual verdict (no automatic bridge)', len(manual), '0',
                      PASS if not manual else FAIL, asrc, rank=7)
        elif L and L.get('x86_inconsistent') is not None:
            self.gate('abi.x86_inconsistent', 'ABI', 'S2 x86-inconsistent pairs not explained',
                      L['x86_inconsistent'], '0', FAIL if L['x86_inconsistent'] else PASS, s,
                      'from the report total (out/abi.tsv not readable): none counted as explained', rank=6)
            self.gate('abi.manual', 'ABI', 'S2 pairs with a manual verdict (no automatic bridge)', L['abi_manual'], '0',
                      FAIL if L['abi_manual'] else PASS, s, rank=7)
        else:
            self.nodata('abi.x86_inconsistent', 'ABI', 'S2 x86-inconsistent pairs not explained', '0', 'no link', rank=6)
            self.nodata('abi.manual', 'ABI', 'S2 pairs with a manual verdict', '0', 'no link', rank=7)

    def abi_verdicts(self):
        """abiclass verdicts that explain a pair (thresholds abi_explained_verdicts; S1b, J-0141)"""
        return set(self.th.get('abi_explained_verdicts', []))

    def abiclass_explained(self, br, un):
        """(rows still open, {verdict: rows}, source, note): S1b's abiclass.py table for this link
        (<--abiclass dir>/<c12>.tsv from the gates job, else link/<c12>/out/abiclass.tsv) explains a row when every
        call of its pair has a verdict of abi_explained_verdicts. `patched` holds only while the site is an S1b hunk
        at the head and was not yet one at the link's commit: a site the link already had and whose row is still
        there was not fixed by its patch (patched-in-link); a site the head no longer has is patched-gone; with the
        link commit's patches unknown, patched-unverified. Rows the table does not cover: no-verdict"""
        ok = self.abi_verdicts()
        if not ok or not br:
            return un, {}, None, 'abi_explained_verdicts not set' if not ok else None
        c12 = br[5:]
        text, src = None, None
        f = os.path.join(self.abiclass_dir, c12 + '.tsv') if self.abiclass_dir else None
        if f and os.path.isfile(f):
            text = readf(f)
            src = dict(branch='gates job', path='S1b abiclass.py of link/%s at the head' % c12,
                       commit=(self.hist.head or '')[:12], behind=0, published=None, stale=False)
        elif self.store.exists(br, 'out/abiclass.tsv'):
            text = self.store.read(br, 'out/abiclass.tsv')
            src = self.src(br, 'out/abiclass.tsv', c12)
        tbl = parse_abiclass(text)
        if tbl is None:
            return un, {}, None, 'no abiclass table for %s' % br
        head_sites = patch_sites(readf(os.path.join(self.port, 'patches', n))
                                 for n in sorted(os.listdir(os.path.join(self.port, 'patches')))
                                 if n.endswith('.s1b')) if os.path.isdir(os.path.join(self.port, 'patches')) else set()
        link_sites = self.hist.patch_sites(c12)
        rest, by = [], {}
        for r in un:
            calls = tbl.get((r[5], r[0].replace('#vtables', ''), r[2], r[3]))
            vs = []
            for v, caller in calls or []:
                site = (r[0].replace('#vtables', ''), caller)
                if v == 'patched':
                    v = ('patched-gone' if site not in head_sites else 'patched-unverified' if link_sites is None
                         else 'patched-in-link' if site in link_sites else v)
                vs.append(v)
            # a pair counts by its worst call (abiclass's rank; patched-gone / -in-link / -unverified rank first)
            rank = lambda v: ABICLASS_RANK.index(v) if v in ABICLASS_RANK else -1
            if vs and all(v in ok for v in vs):
                worst = min(vs, key=rank)
            else:
                worst = min((v for v in vs if v not in ok), key=rank) if vs else 'no-verdict'
                rest.append(r)
            by[worst] = by.get(worst, 0) + 1
        return rest, by, src, None

    def equiv_gates(self):
        n = self.th['equiv_shards']
        shards = {}
        for farm, (d, s) in self.newest('eq', r'-equiv\.md$', parse_equiv, lambda d: shard_of(d, n)).items():
            if d['k'] not in shards or (s['published'] or 0) > (shards[d['k']][1]['published'] or 0):
                shards[d['k']] = (d, s)
        srcs = [v[1] for _, v in sorted(shards.items())]
        if len(shards) < n:
            self.nodata('equiv.code', 'Rule equivalence', 'S11: code differences not by design', '0',
                        'equivalence shards with a report: %d of %d' % (len(shards), n), rank=16, sources=srcs)
            return
        bd_rules = set(self.th['equiv_by_design']['code_rules'])
        bd_addrs = by_design_patch_addrs(self.port)
        code = [(a, r) for d, _ in shards.values() for a, r in d['code_fns']]
        un = [(a, r) for a, r in code if r not in bd_rules and a not in bd_addrs]
        per = {}
        for _, r in un:
            per[r] = per.get(r, 0) + 1
        fns = sum(d['functions'] for d, _ in shards.values())
        self.gate('equiv.code', 'Rule equivalence', 'S11: functions whose code differs, not by design', len(un), '0',
                  PASS if not un else FAIL, srcs, '%d code-differing of %d compared; by design: rules %s, %d S1b sites '
                  '(PATCHES.md); open by rule: %s' % (len(code), fns, '+'.join(sorted(bd_rules)), len(bd_addrs),
                                                      ', '.join('%s %d' % kv for kv in sorted(per.items(), key=lambda x: -x[1]))),
                  rank=16)
        bd_nb = set(self.th['equiv_by_design']['nobuild_rules'])
        nb = [r for d, _ in shards.values() for r in d['nobuild_rules']]
        nbs = sum(d['nobuild'] for d, _ in shards.values())
        by = sum(1 for r in nb if r in bd_nb)
        rest = {}
        for r in nb:
            if r not in bd_nb:
                rest[r] = rest.get(r, 0) + 1
        self.gate('equiv.nobuild', 'Rule equivalence', 'S11: units MSVC cannot compare (prepared side), not by design',
                  nbs - by, '0', PASS if nbs - by == 0 else FAIL, srcs, '%d no-build units, %d by design (%s); by rule: %s'
                  % (nbs, by, '+'.join(sorted(bd_nb)), ', '.join('%s %d' % kv for kv in sorted(rest.items(),
                                                                                         key=lambda x: -x[1])[:6])),
                  rank=17)

    def static_gates(self):
        n = self.th['static_shards']
        shards = {}
        for farm, (d, s) in self.newest('st', r'-static\.md$', parse_static, lambda d: shard_of(d, n)).items():
            if d['k'] not in shards or (s['published'] or 0) > (shards[d['k']][1]['published'] or 0):
                shards[d['k']] = (d, s)
        srcs = [v[1] for _, v in sorted(shards.items())]
        if len(shards) < n:
            self.nodata('static.hazards', 'Static target hazards', 'S12: open hazard sites', '0',
                        'static shards with a report: %d of %d' % (len(shards), n), rank=15, sources=srcs)
            return
        # closed: what tools/static/report.py's CLOSED says the current build closes (build_closures() reads the
        # build config), plus thresholds.json static_closed (file + regex) for a classifier that predates CLOSED
        verdict, closed = static_classifier(self.port)
        for cls, ev in self.th.get('static_closed', {}).items():
            if cls.startswith('_'):
                continue
            p = os.path.join(self.port, ev['file'])
            if os.path.isfile(p) and re.search(ev['regex'], readf(p), re.S):
                closed[cls] = ev['why']
        sites = set().union(*(d['sites'] for d, _ in shards.values()))
        exits = [d['exit'] for d, _ in shards.values()]
        uncovered = sum((d['units'] or 0) - (d['gcc'] or 0) for d, _ in shards.values())
        per, shut = {}, {}
        for f, l, c in sites:
            if c in closed:
                shut[c] = shut.get(c, 0) + 1
            elif verdict.get(c, 'per-site') != 'benign':
                per[c] = per.get(c, 0) + 1
        openn = sum(per.values())
        self.gate('static.hazards', 'Static target hazards', 'S12: open hazard sites (non-benign classes, fix not landed)',
                  openn, '0', PASS if openn == 0 else FAIL, srcs, '%d unique sites; closed by the build (%d sites): %s; '
                  'top open: %s' % (len(sites), sum(shut.values()),
                                    ', '.join('%s %d' % (c, shut.get(c, 0)) for c in sorted(closed)) or 'none',
                                    ', '.join('%s %d' % kv for kv in sorted(per.items(), key=lambda x: -x[1])[:6])),
                  rank=14, closed=sorted(closed))
        self.gate('static.gate', 'Static target hazards', 'S12 gate: shards over baseline / units GCC did not compile',
                  '%d / %d' % (sum(1 for e in exits if e), uncovered), '0 / 0',
                  PASS if not any(exits) and uncovered == 0 else FAIL, srcs, rank=18)

    def fuzz_gates(self):
        n = self.th['fuzz_shards']
        shards = {}
        for farm, (d, s) in self.newest('fz', r'-fuzz\.md$', parse_fuzz, lambda d: shard_of(d, n)).items():
            if d['k'] not in shards or (s['published'] or 0) > (shards[d['k']][1]['published'] or 0):
                shards[d['k']] = (d, s)
        srcs = [v[1] for _, v in sorted(shards.items())]
        missing = sorted(set(range(1, n + 1)) - set(shards))
        if not self.fuzz_causes_gates(shards, n):
            self.fuzz_table_gate(shards, srcs, missing, n)
        if shards:
            # per shard (V1 R16/R17): a shard's verdicts count up to its own functions with a native object, a shard
            # without a positive count of such functions is broken, and every shard must reach the threshold on its
            # own, so no surplus of one shard hides the gap of another
            want = self.th.get('fuzz_min_coverage', 0.9)
            per, broken, over = {}, [], []
            for k, (d, _) in sorted(shards.items()):
                wo, v = d.get('with_obj'), d.get('verdicts') or 0
                if not isinstance(wo, int) or wo <= 0 or not isinstance(v, int) or v < 0:
                    broken.append(k)
                    continue
                if v > wo:
                    over.append(k)
                per[k] = (min(v, wo), wo)
            with_obj = sum(wo for _, wo in per.values())
            ver = sum(v for v, _ in per.values())
            nm = sum(d['not_modelled'] or 0 for d, _ in shards.values())
            cov = ver / with_obj if with_obj > 0 else 0
            low = sorted(k for k, (v, wo) in per.items() if v / wo < want)
            worst = min(per, key=lambda k: per[k][0] / per[k][1]) if per else None
            summ = sorted(k for k, (d, _) in shards.items() if d.get('status'))
            part = sorted(k for k, (d, _) in shards.items() if d.get('status') == 'partial')
            note = '%d not modelled (indirect control flow); %d of %d shards reported' % (nm, len(shards), n)
            if missing:
                note += ' (missing: %s)' % ','.join(map(str, missing))
            if worst is not None:
                note += '; lowest shard %d: %.1f %%' % (worst, 100.0 * per[worst][0] / per[worst][1])
            if low:
                note += '; shards below %.0f %%: %s' % (100 * want, ','.join(map(str, low)))
            if broken:
                note += '; shards with no positive function count: %s' % ','.join(map(str, broken))
            if over:
                note += '; shards with more verdicts than functions (capped): %s' % ','.join(map(str, over))
            note += '; fnvnx-fuzz-summary/1 from %d shards' % len(summ)
            if part:
                note += ', partial (pending functions): %s' % ','.join(map(str, part))
            ok = per and not missing and not broken and not low and not over
            self.gate('fuzz.coverage', 'Fuzz', 'S13: functions with a verdict / functions with a native object',
                      '%.1f %% (%d/%d)' % (100 * cov, ver, with_obj), '>= %.0f %% in every one of %d shards' % (
                          100 * want, n), PASS if ok else FAIL, srcs, note, rank=19,
                      per_shard={str(k): [v, wo] for k, (v, wo) in per.items()})
        else:
            self.nodata('fuzz.coverage', 'Fuzz', 'S13 coverage', '>= 90 %', 'no fuzz report', rank=19)

    def fuzz_table_gate(self, shards, srcs, missing, n):
        """fuzz.hard from the reports' hard-divergence tables (reports without an fnvnx-causes block)"""
        bd = self.th.get('fuzz_by_design_causes', {})
        hard = sorted(set(h for d, _ in shards.values() for h in d['hard']))
        un = [h for h in hard if h[1] not in bd]
        fns = len(set(h[0] for h in un))
        per = {}
        for h in un:
            per[h[1]] = per.get(h[1], 0) + 1
        note = '%d hard divergences in %d functions not by design (by design: %s); causes: %s' % (
            len(un), fns, '+'.join(sorted(bd)) or '-', ', '.join('%s %d' % kv for kv in sorted(per.items(),
                                                                                           key=lambda x: -x[1])[:6]))
        note += '; from the hard-divergence tables (no fnvnx-causes block)'
        if missing:
            note += '; shards without a %d-shard report: %s' % (n, ','.join(map(str, missing)))
        st = FAIL if un else (PASS if not missing else NODATA)
        self.gate('fuzz.hard', 'Fuzz', 'S13: hard divergences not by design', len(un) if shards else None, '0',
                  st if shards else NODATA, srcs, note, rank=11)

    def fuzz_causes(self, shards):
        """the S13b cause summary with the widest shard coverage: the fnvnx-causes blocks of the shards' newest
        reports, merged, or S13b's whole-campaign summary (thresholds fuzz_causes: branch + path, causes_all.json).
        Returns (summary, shards covered, sources, what) or None"""
        cands = []
        rep = {k: (d['causes'], s) for k, (d, s) in shards.items() if d.get('causes')}
        if rep:
            tot = dict(functions_ok=0, functions_diverging=0, causes={})
            for c, _ in rep.values():
                tot['functions_ok'] += c.get('functions_ok') or 0
                tot['functions_diverging'] += c.get('functions_diverging') or 0
                for cause, r in c['causes'].items():
                    t_ = tot['causes'].setdefault(cause, dict(verdict=r.get('verdict'), divergences=0, functions=0,
                                                              worst_of=0))
                    for k_ in ('divergences', 'functions', 'worst_of'):
                        t_[k_] += r.get(k_) or 0
            cands.append((tot, set(rep), [s for _, (_, s) in sorted(rep.items())], 'the shards\' report blocks'))
        fc = self.th.get('fuzz_causes', {})
        br, path = fc.get('branch'), fc.get('path')
        if br and path and br in self.store.branches(br):
            pub = dict(self.store.files(br)).get(path)
            d = causes_block(self.store.read(br, path) or '')
            if d:
                ks = causes_shards(d) or set()
                cands.append((d, ks, [self.src(br, path, d.get('commit'), pub)], 'S13b\'s campaign summary'))
        if not cands:
            return None
        return max(cands, key=lambda c: (len(c[1]), c[3].startswith('the shards')))

    def fuzz_causes_gates(self, shards, n):
        """fuzz.hard from the S13b causes (fnvnx-causes/1): only causes whose class is real-bug or triage
        (thresholds fuzz_hard_classes) count; by-design and artifact causes are not a
        divergence of the port. Harness artifacts are shown as an informative gate (fuzz.artifacts, S13)"""
        got = self.fuzz_causes(shards)
        if not got:
            return False
        cs, covered, srcs, what = got
        hard_cls = set(self.th.get('fuzz_hard_classes', ['real-bug', 'triage']))

        def cls(cause, r):
            return r.get('verdict') or 'triage'      # the class the report's causes block carries
        by = {}
        for cause, r in cs['causes'].items():
            c = cls(cause, r)
            b = by.setdefault(c, dict(functions=0, divergences=0, causes={}))
            b['functions'] += r.get('worst_of') or 0               # functions whose worst cause it is: no overlaps
            b['divergences'] += r.get('divergences') or 0
            b['causes'][cause] = r.get('worst_of') or 0
        hard_fn = sum(by.get(c, {}).get('functions', 0) for c in hard_cls)
        hard_div = sum(by.get(c, {}).get('divergences', 0) for c in hard_cls)
        owners = {}
        for cause, r in cs['causes'].items():
            if cls(cause, r) in hard_cls:
                o = r.get('owner') or '?'
                owners[o] = owners.get(o, 0) + (r.get('worst_of') or 0)
        top = sorted(((f, c) for b in (by.get(x, {}) for x in hard_cls) for c, f in b.get('causes', {}).items()),
                     key=lambda x: (-x[0], x[1]))[:6]
        nocov = sorted(set(range(1, n + 1)) - covered)
        note = '%d functions, %d divergences (%s); by class: %s; owners: %s; top: %s; from %s, %d of %d shards' % (
            hard_fn, hard_div, '+'.join(sorted(hard_cls)),
            ', '.join('%s %d' % (c, by[c]['functions']) for c in ('real-bug', 'triage', 'artifact', 'by-design')
                      if c in by),
            ', '.join('%s %d' % kv for kv in sorted(owners.items(), key=lambda x: -x[1])),
            ', '.join('%s %d' % (c, f) for f, c in top), what, len(covered), n)
        if nocov:
            note += '; shards not covered: %s' % ','.join(map(str, nocov))
        st = FAIL if hard_fn else PASS if not nocov else NODATA
        self.gate('fuzz.hard', 'Fuzz', 'S13: functions with a real-bug or triage divergence (causes block of the report)', hard_fn,
                  '0', st, srcs, note, rank=11, by_class={c: by[c]['functions'] for c in by}, owners=owners,
                  divergences=hard_div)
        art = by.get('artifact', dict(functions=0, divergences=0, causes={}))
        self.gate('fuzz.artifacts', 'Fuzz', 'S13: functions whose worst divergence is a harness artifact (S13 fixes)',
                  art['functions'], '0', PASS if not art['functions'] else FAIL, srcs, 'not the port (the report\'s class); '
                  'by cause: %s' % ', '.join('%s %d' % (c, f) for c, f in sorted(art['causes'].items(),
                                                                                 key=lambda x: (-x[1], x[0]))[:6]),
                  rank=19, required=False)
        return True

    def player_exe(self):
        """(sha256 of the player's FalloutNV.exe, why not) from gamedata/exe's checksum file: only its line for the
        file FalloutNV.exe counts (a line for another file proves nothing about the exe, J-0135 G1), and with
        thresholds player_exe_sha256 that line must name the pinned GOG 1.4.0.525(a) exe"""
        text = self.store.read('gamedata/exe', 'FalloutNV.exe.sha256')
        if not text:
            return None, 'gamedata/exe not readable'
        sums = {}
        for l in text.splitlines():
            m = re.match(r'\s*([0-9a-fA-F]{64})\s+\*?(\S.*?)\s*$', l)
            if m:
                sums.setdefault(m.group(2), m.group(1).lower())
        want = sums.get('FalloutNV.exe')
        if not want:
            return None, 'gamedata/exe/FalloutNV.exe.sha256 has no line for FalloutNV.exe (names: %s)' % (
                ', '.join(sorted(sums)) or 'none')
        pin = str(self.th.get('player_exe_sha256') or '').lower()
        if pin and want != pin:
            return None, 'gamedata/exe holds another exe (%s.., pinned %s..)' % (want[:8], pin[:8])
        return want, None

    def boot_gates(self):
        fb = sorted(self.full.values(), key=lambda x: (x[1]['behind'] if x[1]['behind'] is not None else 1 << 30))
        names = self.th['boot_milestones']
        if not fb:
            self.nodata('boot.real_exe', 'Emulated boot', 'boot ran on the player\'s FalloutNV.exe', 'yes',
                        'no full build report', rank=1)
            for i, m in enumerate(names):
                self.nodata('boot.' + m, 'Emulated boot', 'milestone: ' + m, 'reached on the real exe',
                            'no full build report', rank=2 + i)
            return
        d, s = fb[0]
        br = self.link_branch(d['_text_commit'])
        meta = self.link_json(br, 'meta.json') or {}
        stage = d['stages'].get('boot')
        if stage not in ('ok', 'warn'):
            # the newest full build has no boot of its own: what link/<c12> still holds may come from another run
            # (published before link_job cleared build/boot, J-0135), so nothing of it counts
            why = ('the boot stage of this full build failed' if stage == 'fail' else
                   'this full build did not run the boot (stage %s)' % (stage or 'missing'))
            st = FAIL if stage == 'fail' else NODATA
            self.gate('boot.real_exe', 'Emulated boot', 'boot ran on the player\'s FalloutNV.exe',
                      'no (boot stage %s)' % stage if stage == 'fail' else None, 'yes', st, s, why, rank=1)
            for i, m in enumerate(names):
                self.gate('boot.' + m, 'Emulated boot', 'milestone: ' + m, 'no' if stage == 'fail' else None,
                          'reached on the real exe', st, s, why, rank=2 + i)
            self.gate('boot.init_skips', 'Emulated boot', 'static initializers skipped after a fault', None, '0',
                      NODATA, s, why, rank=6)
            return
        want, why_not = self.player_exe()
        got = meta.get('boot_image_sha256')
        got = got.lower() if isinstance(got, str) else None
        reached = self.milestones(br, meta, d)
        labels = self.image_labels(meta, d)
        label = labels[0][1] if labels and len(set(v for _, v in labels)) == 1 else None
        if got and want:
            real = got == want
            self.gate('boot.real_exe', 'Emulated boot', 'boot ran on the player\'s FalloutNV.exe', 'yes' if real else
                      'no (%s)' % ('synthesized image' if 'synth' in (d.get('image') or '') else 'another image'), 'yes',
                      PASS if real else FAIL, s, 'image sha256 %s.., exe %s..' % (got[:8], want[:8]), rank=1)
        elif got:
            # an image hash that cannot be checked: neither it nor a label stands in for the check
            real = None
            self.nodata('boot.real_exe', 'Emulated boot', 'boot ran on the player\'s FalloutNV.exe', 'yes',
                        'image sha256 %s.. not checked: %s' % (got[:8], why_not), rank=1, sources=[s])
        elif len(set(v for _, v in labels)) > 1:
            # no hash, and the labels disagree: none of them stands for the image (V1 R13)
            real = None
            self.nodata('boot.real_exe', 'Emulated boot', 'boot ran on the player\'s FalloutNV.exe', 'yes',
                        'no image hash, and the image labels contradict each other: %s' % ', '.join(
                            '%s "%s"' % kv for kv in labels), rank=1, sources=[s])
        elif label == 'synthesized' and labels[0][0] == 'report data image':
            # no hash to compare (link/<c12> not kept), but the report names the synthesized image
            real = False
            self.gate('boot.real_exe', 'Emulated boot', 'boot ran on the player\'s FalloutNV.exe', 'no (synthesized image)',
                      'yes', FAIL, s, 'data image %s; no image hash (link/%s not kept)' % (
                          os.path.basename(d['image']), (d['_text_commit'] or '')[:12]), rank=1)
        elif label in ('player exe', 'synthesized') and any(k != 'report data image' for k, _ in labels):
            # no hash to compare: S5's image label (boot/milestones.json "image", meta.json boot_image), and nothing
            # that says otherwise; the report's data image line alone only ever fails the gate (its text is free)
            real = label == 'player exe'
            self.gate('boot.real_exe', 'Emulated boot', 'boot ran on the player\'s FalloutNV.exe',
                      'yes' if real else 'no (synthesized image)', 'yes', PASS if real else FAIL, s,
                      'image "%s" (S5 label: %s; no image hash to check)' % (label, ' = '.join(k for k, _ in labels)), rank=1)
        else:
            real = None
            self.nodata('boot.real_exe', 'Emulated boot', 'boot ran on the player\'s FalloutNV.exe', 'yes',
                        'no image hash in meta.json', rank=1, sources=[s])
        stub = dct(self.boot_info.get('stub'))
        for i, m in enumerate(names):
            r = reached.get(m)
            if r is None:
                self.nodata('boot.' + m, 'Emulated boot', 'milestone: ' + m, 'reached on the real exe',
                            'no machine-readable milestone `%s` yet (S5)' % m, rank=2 + i, sources=[s])
                continue
            ok = bool(r) and real is True
            note = '' if real else 'on the synthesized image only' if r and real is False else \
                'the image is not checked' if r else ''
            if not r and stub.get(m):
                note = 'the stub-fault boot reaches it (known faults stubbed: not the guest\'s own progress)'
            elif not r and m == next((x for x in names if not reached.get(x)), None) and self.boot_info.get('stop'):
                note = 'raw boot stops: %s' % str(self.boot_info['stop'])[:120]
            self.gate('boot.' + m, 'Emulated boot', 'milestone: ' + m, 'yes' if r else 'no', 'reached on the real exe',
                      PASS if ok else FAIL, s, note, rank=2 + i)
        sk = d.get('init_skips')
        self.gate('boot.init_skips', 'Emulated boot', 'static initializers skipped after a fault', sk, '0',
                  NODATA if sk is None else PASS if sk == 0 else FAIL, s, (d.get('stop') or '')[:120], rank=6)

    def image_labels(self, meta, d):
        """[(where, "player exe" | "synthesized" | the unknown value)]: every claim about the boot image besides its
        hash: S5's boot/milestones.json image (and its stub boot's), meta.json boot_image, and the full report's
        `- data image:` line (synth* -> synthesized, FalloutNV.exe / the owner's exe -> player exe). A value that is
        none of the two is kept as itself, so it contradicts any known label rather than vanishing"""
        out = []
        for where, v in (('milestones.json image', self.boot_info.get('image')),
                         ('milestones.json stub image', dct(self.boot_info.get('stub')).get('image')),
                         ('meta.json boot_image', meta.get('boot_image'))):
            if v is not None:
                out.append((where, v if isinstance(v, str) else repr(v)))
        im = d.get('image')
        if im:
            b = os.path.basename(im.strip())
            out.append(('report data image', 'synthesized' if 'synth' in b.lower() else
                        'player exe' if b.lower() == 'falloutnv.exe' or im.strip() == "the owner's FalloutNV.exe"
                        else 'data image ' + b))
        return out

    def milestones(self, br, meta, d):
        """S5's machine-readable milestones, by precedence: boot/milestones.json (fnvnx-boot-milestones/1,
        tests/s5/boot_milestones.py: the raw boot's {name: true|false|null} at the top level, "stub" for the
        stub-fault boot, plus image, milestone, boot_peak_heap_bytes, stop; null is not measured) decides every
        milestone it gives; meta.json boot_<name> and `fnvnx milestone: <name>` lines in boot/summary.txt only fill
        what it leaves out (a summary line never overrides a false); WinMain also from the report"""
        out = {}
        names = self.th['boot_milestones']
        given = set()                       # milestones milestones.json speaks about, even as null (not measured)
        mj = self.link_json(br, 'boot/milestones.json') if br else None
        if mj is not None:
            top = mj['milestones'] if isinstance(mj.get('milestones'), dict) else mj
            for k in names:
                for key in (k, k.replace('_', '-')):
                    if key in top:
                        v = top[key]
                        given.add(k)
                        if isinstance(v, bool) or v in (0, 1):
                            out[k] = int(bool(v))
                        break
            self.boot_info = dict(image=mj.get('image'), last=mj.get('milestone'), stop=mj.get('stop'),
                                  peak=mj.get('boot_peak_heap_bytes'),
                                  stub=mj['stub'] if isinstance(mj.get('stub'), dict) else None)
        for k in names:
            v = meta.get('boot_' + k)
            if k not in given and k not in out and (isinstance(v, bool) or v in (0, 1)):
                out[k] = int(bool(v))
        summ = self.store.read(br, 'boot/summary.txt') if br else None
        if summ:
            for m in re.findall(r'fnvnx milestone: (\w+)', summ):
                if m in names and m not in given and m not in out:
                    out[m] = 1
            if 'winmain' not in given and 'winmain' not in out and 'fnvnx boot: calling WinMain' in summ:
                out['winmain'] = 1
        if 'winmain' not in given and 'winmain' not in out and d.get('winmain') is not None:
            out['winmain'] = d['winmain']
        return out

    def frames_gate(self):
        hits = []
        for br in self.report_branches():
            for path, t in self.store.files(br):
                if re.search(r'-frames\.json$', path):
                    hits.append((t, br, path))
                    break
        if not hits:
            self.nodata('frames.compare', 'Frame and scene comparison', 'S16: scenes matching the reference',
                        'all scenes', 'no S16 gate file (reports/<farm>/<c12>-frames.json) yet', rank=5)
            return
        t, br, path = max(hits)
        try:
            g = json.loads(self.store.read(br, path) or '')
        except ValueError:
            g = None
        title = 'S16: scenes matching the reference'
        if not isinstance(g, dict) or not isinstance(g.get('scenes'), list):
            self.nodata('frames.compare', 'Frame and scene comparison', title, 'all scenes', 'unreadable gate file',
                        rank=5)
            return
        sc = [x for x in g['scenes'] if isinstance(x, dict)]
        note = []
        if g.get('format') == 'fnvnx-gate/1':
            # S16's own gate file (tools/compare/compare.py): scenes[].status, candidate.commit / kind, a top-level
            # status that must agree with the scenes
            cand = dct(g.get('candidate'))
            commit = cand.get('commit')
            name = lambda x: str(x.get('id') or x.get('name') or '?')
            passed = lambda x: x.get('status') == 'pass'
            kinds = self.th.get('frames', {}).get('candidate_kinds', ['port'])
            if cand.get('kind') not in kinds:
                self.nodata('frames.compare', 'Frame and scene comparison', title, 'all scenes',
                            'the candidate run is "%s", not the port (%s)' % (cand.get('kind'), '/'.join(kinds)),
                            rank=5, sources=[self.src(br, path, commit, t)])
                return
            top_ok = g.get('status') == 'pass'
            if not top_ok:
                note.append('gate status %s' % g.get('status'))
        else:
            # R0's first format: scenes[].pass (a JSON true, nothing else) and a top-level commit
            commit, top_ok = g.get('commit'), True
            name = lambda x: str(x.get('name') or '?')
            passed = lambda x: x.get('pass') is True
        ok = sum(1 for x in sc if passed(x))
        good = bool(sc) and ok == len(sc) == len(g['scenes']) and top_ok
        if len(sc) != len(g['scenes']):
            note.append('%d malformed scene entries' % (len(g['scenes']) - len(sc)))
        note += ['%s %s' % (name(x), x.get('status', '')) if 'status' in x else name(x) for x in sc if not passed(x)]
        self.gate('frames.compare', 'Frame and scene comparison', title, '%d/%d' % (ok, len(g['scenes'])),
                  'all scenes', PASS if good else FAIL, self.src(br, path, commit, t), ', '.join(note), rank=5,
                  scenes_ok=ok, scenes=len(g['scenes']))

    def perf_gates(self):
        p = self.th['perf']
        hits = []
        for br in self.report_branches():
            for path, t in self.store.files(br):
                if path.endswith('-frameprof.json'):
                    hits.append((t, br, path))
                    break
        prof, ps = None, None
        if hits:
            t, br, path = max(hits)
            prof = parse_frameprof(self.store.read(br, path) or '')
            m = re.match(r'([0-9a-f]{12})-', os.path.basename(path))
            ps = self.src(br, path, m.group(1) if m else None, t)
        broken = 'broken frameprof run (not a measurement): %s' % short_list(prof['bad'], 4) if prof and prof['bad'] \
            else None
        for clk in p['clocks']:
            gid, req = 'perf.frame.' + clk['name'], clk.get('required', True)
            title = 'main-thread frame time, worst / median (%s clock %.3f GHz)' % (clk['name'], clk['hz'] / 1e9)
            if broken:
                self.nodata(gid, 'Performance', title, self.fps_threshold(), broken, rank=22, sources=[ps],
                            required=req)
            elif prof and prof['frames']:
                ms = sorted(i / (p['ipc'] * clk['hz']) * 1000 for i in prof['frames'])
                worst, med = ms[-1], statistics.median(ms)
                lv, name, st = self.fps_levels(worst, med)
                self.gate(gid, 'Performance', title, '%.1f / %.1f ms (%.0f fps worst)' % (worst, med, 1000 / worst),
                          self.fps_threshold(), st, ps, '%s; %d frames profiled, worst %s instructions at IPC %s' % (
                              self.fps_note(lv, name), len(ms), '{:,}'.format(max(prof['frames'])), p['ipc']),
                          rank=22, required=req, levels=dict(lv, level=name))
            else:
                self.nodata(gid, 'Performance', title, self.fps_threshold(), 'no frame reached in a profiled run'
                            if prof else 'no frameprof run published', rank=22, sources=[ps] if ps else None,
                            required=req)
        bc = next((c for c in p['clocks'] if c['name'] == p.get('boot_clock')), p['clocks'][0])
        title = 'estimated device time from start to WinMain (%s clock)' % bc['name']
        if broken:
            self.nodata('perf.boot', 'Performance', title, '<= %.1f s' % p['boot_to_winmain_s'], broken, rank=21,
                        sources=[ps])
        elif prof and prof['to_winmain'] is not None:
            secs = [(c['name'], prof['to_winmain'] / p['ipc'] / c['hz']) for c in p['clocks']]
            sec = dict(secs)[bc['name']]
            self.gate('perf.boot', 'Performance', title, '%.2f s' % sec, '<= %.1f s' % p['boot_to_winmain_s'],
                      PASS if sec <= p['boot_to_winmain_s'] else FAIL, ps, '%s instructions; %s' % (
                          '{:,}'.format(prof['to_winmain']), ', '.join('%s %.2f s' % kv for kv in secs)), rank=21)
        else:
            self.nodata('perf.boot', 'Performance', title, '<= %.1f s' % p['boot_to_winmain_s'],
                        'no frameprof run reaching WinMain', rank=21, sources=[ps] if ps else None)
        mem = self.th['memory']
        avail = mem['heap_region_bytes'] - mem['host_heap_bytes']
        fb = sorted(self.full.values(), key=lambda x: (x[1]['behind'] if x[1]['behind'] is not None else 1 << 30))
        meta = self.link_json(self.link_branch(fb[0][0]['_text_commit']), 'meta.json') if fb else None
        peak = (meta or {}).get('boot_peak_heap_bytes')
        if peak is None:
            peak = self.boot_info.get('peak')          # S5's boot/milestones.json
        if peak is not None:
            frac = peak / avail
            self.gate('perf.memory', 'Performance', 'peak guest memory / memory the guest can have',
                      '%.0f %%' % (100 * frac), '<= %.0f %%' % (100 * mem['max_use_fraction']),
                      PASS if frac <= mem['max_use_fraction'] else FAIL, fb[0][1], rank=23)
        else:
            self.nodata('perf.memory', 'Performance', 'peak guest memory / memory the guest can have',
                        '<= %.0f %% of %d MiB' % (100 * mem['max_use_fraction'], avail >> 20),
                        'no peak heap measurement (meta.json boot_peak_heap_bytes) yet', rank=23)
        pc = placement_check(self.port)
        hs = dict(branch='nx-agent', path='toolchain/host/placement.h, toolchain/npdm/fnvnx-app.json',
                  commit=(self.hist.head or '')[:12], behind=0, published=None, stale=False)
        if pc is None:
            self.nodata('perf.cores', 'Performance', 'core placement', 'main 0, workers 1-2, core 3 opt-in',
                        'placement table not found', rank=24)
        else:
            self.gate('perf.cores', 'Performance', 'core placement (main 0, workers 1-2, core 3 opt-in, off)',
                      'ok' if not pc['problems'] else '%d problems' % len(pc['problems']),
                      'main 0, workers 1-2, core 3 opt-in', PASS if not pc['problems'] else FAIL, hs,
                      '; '.join(pc['problems']) or '%d roles checked' % pc['roles'], rank=24)

    def render_gate(self):
        """the render machine's report (docs/RENDER_PERF.md section 5): the renderer's CPU share of each frame-rate
        target's budget, and the GPU model.

        perf.render.<clock>: the renderer's worst thread (main side or render thread) as a fraction of the frame
        budget, `targets_budget_fraction` of the summary (hi bound: IPC 0.6, mix_hi). Its `targets` verdicts are not
        used: they compare the renderer alone with the whole frame, while the owner's targets are for the whole frame.
        The renderer has a share of the frame (RENDER_NATIVE.md section 9.4: render thread 4.5 of 22.2 ms, 3.5 of
        16.7 ms; packet build 4.0 / 3.0 ms on main), thresholds render_perf.max_budget_fraction. A level is met when
        the renderer fits its share of that target's frame. A report without the fractions gives them from
        frame_ms (gate = hi), scaled from the clock it was measured at.

        perf.gpu.<config>: the GPU frame time of the report's model (gpu.summary_ms.<mix>.<config>, efficiency 1.0 -
        0.6; the 0.6 bound is judged) against the frame-rate levels. A model with no measured anchor: informative
        unless thresholds render_perf.gpu says otherwise."""
        rp, p = self.th.get('render_perf', {}), self.th['perf']
        r = self.render
        where = r.get('branch') or rp.get('branch', '?')
        where = where if where.startswith('nx-agent') else 'nx-agent:%s' % where
        try:
            full = json.loads(r['text']) if r.get('text') else None
        except ValueError:
            full = None
        j = full
        if isinstance(j, dict) and not isinstance(j.get('frame_ms'), dict) and isinstance(j.get('summary'), dict):
            j = j['summary']
        share = rp.get('max_budget_fraction', 0.2)
        f = p['fps']
        src, note0 = None, ''
        if isinstance(j, dict):
            c = str(j.get('commit') or '')
            h = r.get('hist')
            b = h.behind(c) if h and c else None
            src = dict(branch=where, path=r.get('path') or rp.get('path'), commit=c[:12], behind=b, published=None,
                       stale=b is None or b > self.maxb)
            pd, fm = j.get('per_draw_us') or {}, j.get('frame_ms') or {}
            note0 = 'per draw: native %s us, Nine %s us; frame main %s ms, render %s ms (gate = hi: %s)' % (
                fmt_ms(bound(pd.get('native'))), fmt_ms(bound(pd.get('nine'))), fmt_ms(bound(fm.get('main'))),
                fmt_ms(bound(fm.get('render'))), (j.get('assumptions') or {}).get('gate_value') or 'as given')
        for clk in p['clocks']:
            gid, req = 'perf.render.' + clk['name'], clk.get('required', True)
            title = 'render perf: renderer CPU share of the frame, worst thread (%s clock %.3f GHz)' % (
                clk['name'], clk['hz'] / 1e9)
            thr = 'renderer <= %.0f %% of the frame (RENDER_NATIVE 9.4) at: %s' % (100 * share, self.fps_threshold())
            insane = render_insane(j) if isinstance(j, dict) else []
            fr, how = self.render_fractions(j, clk) if isinstance(j, dict) and not insane else (None, None)
            if insane:
                self.nodata(gid, 'Performance', title, thr, 'the render report is not a plausible measurement: %s'
                            % short_list(insane, 4), rank=22, sources=[src] if src else None, required=req)
                continue
            if not fr:
                why = ('no render perf report (%s:%s) yet' % (where, rp.get('path')) if not r.get('text') else
                       'unreadable summary block' if not isinstance(j, dict) else
                       'neither targets_budget_fraction nor frame_ms.main/render in the summary')
                self.nodata(gid, 'Performance', title, thr, why, rank=22, sources=[src] if src else None, required=req)
                continue
            lv = {k: fr[f[k]] <= share for k in ('floor', 'info', 'target')}
            name = self.level_name(lv)
            ms = fr[f['target']] * 1000.0 / f['target']
            self.gate(gid, 'Performance', title, '%.0f %% of the %d fps frame (%.1f ms)' % (
                100 * fr[f['target']], f['target'], ms), thr, PASS if lv['target'] else FAIL, src,
                      '%s; fractions %s (%s); %s' % (
                          self.fps_note(lv, name), ', '.join('%d fps %.0f %%' % (k, 100 * fr[k]) for k in sorted(fr)),
                          how, note0), rank=22, required=req, levels=dict(lv, level=name), fractions=fr)
        self.gpu_gates(full if isinstance(full, dict) else None, src, where)

    def render_fractions(self, j, clk):
        """{fps: the renderer's worst thread / that target's frame} at clk, and where the numbers came from"""
        f = self.th['perf']['fps']
        want = sorted({f['floor'], f['info'], f['target']})
        tbf = j.get('targets_budget_fraction')
        if isinstance(tbf, dict):
            out = {}
            for fps in want:
                row = tbf.get(str(fps)) or tbf.get(fps)
                if not isinstance(row, dict):
                    break
                ks = {float(k): v for k, v in row.items() if isnum(v) and re.fullmatch(r'\d+(\.\d+)?', str(k))}
                if not ks:
                    break
                hit = next((v for k, v in ks.items() if abs(k * 1e9 - clk['hz']) < 1e6), None)
                if hit is None:                     # another clock: scale the lowest clock's fraction (CPU-bound)
                    k0 = min(ks)
                    hit = ks[k0] * k0 * 1e9 / clk['hz']
                out[fps] = float(hit)
            if len(out) == len(want):
                return out, 'targets_budget_fraction, hi bound'
        fm = j.get('frame_ms')
        if not isinstance(fm, dict):
            return None, None
        m, rr = bound(fm.get('main')), bound(fm.get('render'))
        if not (isnum(m) and isnum(rr)):
            return None, None
        ghz = (j.get('assumptions') or {}).get('clock_ghz')
        if isinstance(ghz, list):
            ghz = min((g for g in ghz if isnum(g)), default=None)   # the hi bound is measured at the lowest clock
        how = 'frame_ms gate values'
        if not (isnum(ghz) and ghz > 0):
            ghz = self.th['perf']['clocks'][0]['hz'] / 1e9
            how += '; no assumptions.clock_ghz: taken as measured at the %s clock' % self.th['perf']['clocks'][0]['name']
        worst = max(m, rr) * float(ghz) * 1e9 / clk['hz']
        return {fps: worst / (1000.0 / fps) for fps in want}, how

    def gpu_gates(self, full, src, where):
        rp = self.th.get('render_perf', {})
        mix = rp.get('gpu_mix', 'mix_hi')
        gpu = dct(dct(full).get('gpu'))
        sm = dct(dct(gpu.get('summary_ms')).get(mix))
        for cfg in rp.get('gpu', []):
            name, req = cfg['name'], cfg.get('required', False)
            title = 'GPU frame time, %s (model, %s)' % (name.replace('_', ' '), mix)
            row = next((sm[k] for k in [name] + cfg.get('aliases', []) if k in sm), None)
            lo = row.get(cfg.get('lo', 'eff_1.0')) if isinstance(row, dict) else None
            hi = row.get(cfg.get('hi', 'eff_0.6')) if isinstance(row, dict) else None
            if not (posnum(lo) and posnum(hi) and lo <= hi):
                self.nodata('perf.gpu.' + name, 'Performance', title, self.fps_threshold(),
                            ('no gpu.summary_ms.%s.%s in the render report (%s)' % (mix, name, where)
                             if lo is None and hi is None else 'gpu.summary_ms.%s.%s is not a plausible time (%r - %r)'
                             % (mix, name, lo, hi)), rank=22, sources=[src] if src else None, required=req)
                continue
            lv, lname, st = self.fps_levels(hi, hi)
            tg = dct(gpu.get('targets'))
            self.gate('perf.gpu.' + name, 'Performance', title, '%.1f - %.1f ms' % (lo, hi), self.fps_threshold(), st,
                      src, '%s; judged on the efficiency-0.6 bound; model, not measured (RENDER_PERF.md section 9); '
                      'report: %s' % (self.fps_note(lv, lname), ', '.join(
                          '%s fps %s' % (k, next((dct(tg.get(k))[a] for a in [name] + cfg.get('aliases', [])
                                                 if a in dct(tg.get(k))), '?')) for k in sorted(tg))), rank=22,
                      required=req, levels=dict(lv, level=lname), ms=dict(lo=lo, hi=hi))

    def budget_gate(self):
        """S15's whole-frame CPU budget (tools/perf/frame_budget.py, fnvnx-frame-budget/1): the newest
        reports/<farm>/<c12>-frame-budget.json, else the checkout's docs/PERF_budget.json (the model alone). Per clock,
        the scenario thresholds perf.budget.scenario (the build as placed today, not a plan) and its verdict for the
        target: pass (the hi case fits) counts only with a measurement in the inputs (frameprof or the render report);
        a model-only pass, or uncertain (lo fits, hi does not), is no-data; fail (even the lo case misses) fails."""
        bt = self.th['perf'].get('budget', {})
        scen_id, target = bt.get('scenario', 'placed'), bt.get('target', 'target_60')
        doc, src = None, None
        hits = []
        for br in self.report_branches():
            for path, t in self.store.files(br):
                if path.endswith('-frame-budget.json'):
                    hits.append((t, br, path))
                    break
        for t, br, path in sorted(hits, reverse=True):
            try:
                d = json.loads(self.store.read(br, path) or '')
            except ValueError:
                continue
            if isinstance(d, dict) and str(d.get('schema', '')).startswith('fnvnx-frame-budget/'):
                m = re.match(r'([0-9a-f]{12})-', os.path.basename(path))
                doc, src = d, self.src(br, path, m.group(1) if m else None, t)
                break
        if doc is None:
            rel = bt.get('model', 'docs/PERF_budget.json')
            f = os.path.join(self.port, rel)
            try:
                d = json.loads(readf(f)) if os.path.isfile(f) else None
            except ValueError:
                d = None
            if isinstance(d, dict) and str(d.get('schema', '')).startswith('fnvnx-frame-budget/'):
                doc = d
                src = dict(branch='nx-agent', path=rel, commit=(self.hist.head or '')[:12], behind=0, published=None,
                           stale=False)
        inputs = (doc or {}).get('inputs') or {}
        measured = [k for k in ('frameprof', 'render') if inputs.get(k)]
        scen = {s.get('id'): s for s in (doc or {}).get('scenarios') or [] if isinstance(s, dict)}
        for clk in self.th['perf']['clocks']:
            gid, req = 'perf.budget.' + clk['name'], clk.get('required', True)
            title = 'S15 whole-frame CPU budget, scenario %s (%s clock %.3f GHz)' % (scen_id, clk['name'], clk['hz'] / 1e9)
            tms = round(1000.0 / self.th['perf']['fps']['target'], 1)
            thr = 'worst frame <= %.1f ms (hi case) on a measured frame' % tms
            s = scen.get(scen_id)
            res = ((s or {}).get('results') or {}).get(str(int(round(clk['hz'] / 1e6))))
            if not isinstance(res, dict):
                self.nodata(gid, 'Performance', title, thr, 'no fnvnx-frame-budget/1 file' if doc is None else
                            'no scenario %s at %d MHz' % (scen_id, clk['hz'] / 1e6), rank=22,
                            sources=[src] if src else None, required=req)
                continue
            # J-0150 / V1 R16: the gate judges the worst frame itself against the target's frame time; S15's own
            # verdicts (a stretch target is judged on the typical frame) are shown, never used
            wf = dct(res.get('worst_frame_ms'))
            lo, hi = wf.get('lo'), wf.get('hi')
            v = (None if not (posnum(lo) and posnum(hi) and lo <= hi) else
                 'pass' if hi <= tms else 'fail' if lo > tms else 'uncertain')
            others = ', '.join('%s %s' % (k, (((x.get('results') or {}).get(str(int(round(clk['hz'] / 1e6)))) or {})
                                             .get('level', '?'))) for k, x in scen.items() if k != scen_id)
            note = 'worst frame against %.1f ms: %s; S15 verdicts (not used) %s; level (mid case) %s; critical core %s; inputs: %s; other scenarios: %s' % (
                tms, v or 'no worst_frame_ms', ', '.join('%s %s' % kv for kv in dct(res.get('verdicts')).items()),
                res.get('level'),
                res.get('critical_core'), '+'.join(measured) or 'the model alone [E]', others or '-')
            st = FAIL if v == 'fail' else PASS if v == 'pass' and measured else NODATA
            if st == NODATA:
                note = ('worst frame %s; ' % (v or 'missing') + ('needs a measured frame (frameprof on the main loop, '
                        'B1 on hardware)' if v != 'pass' else 'model only, needs a measurement') + '; ' + note)
            self.gate(gid, 'Performance', title, 'worst frame %s ms mid (%s - %s)' % (
                wf.get('mid', '?'), wf.get('lo', '?'), wf.get('hi', '?')), thr, st, src, note, rank=22, required=req,
                      level=res.get('level'), verdicts=res.get('verdicts'), worst_frame_ms=wf,
                      measured='+'.join(measured) or None)

    def package_gates(self):
        fb = sorted(self.full.values(), key=lambda x: (x[1]['behind'] if x[1]['behind'] is not None else 1 << 30))
        if not fb:
            self.nodata('package.exefs', 'Packaging', 'exefs.nsp = main + main.npdm, guest.nso on the SD tree', 'yes',
                        'no full build report', rank=25)
            self.nodata('package.program_id', 'Packaging', 'program id of an installed title', 'not the placeholder',
                        'no full build report', rank=20)
            return
        d, s = fb[0]
        st = d['stages'].get('package')
        ex = sorted(d.get('exefs') or [])
        ok = st in ('ok', 'warn') and ex == ['main', 'main.npdm'] and d.get('guest_in_sd') == 1
        self.gate('package.exefs', 'Packaging', 'exefs.nsp = main + main.npdm, guest.nso on the SD tree',
                  'stage %s; exefs %s; guest.nso %s' % (st, '+'.join(ex) or '-', {1: 'yes', 0: 'no'}.get(d.get('guest_in_sd'),
                                                                                                      '?')),
                  'main+main.npdm, guest.nso yes', PASS if ok else FAIL, s, rank=25)
        pid = d.get('program_id')
        self.gate('package.program_id', 'Packaging', 'program id of an installed title', pid or '-',
                  'not 0100000000000000', NODATA if not pid else FAIL if pid == '0100000000000000' else PASS, s,
                  'set PROGRAM_ID=<the installed title the exefs overrides>' if pid == '0100000000000000' else '',
                  rank=20)
        pc = d.get('pcrel_abs')
        self.gate('package.pic', 'Packaging', 'guest PC-relative absolute references (D9 relocatable)', pc, '0',
                  NODATA if pc is None else PASS if pc == 0 else FAIL, s, rank=26)
        meta = self.link_json(self.link_branch(d['_text_commit']), 'meta.json') or {}
        rc = meta.get('check_package_rc', d.get('check_package_rc'))
        if rc is None:
            self.nodata('package.check', 'Packaging', 'tests/s5/check_package.py on the published package', 'exit 0',
                        'no check_package_rc in meta.json and no `check_package: exit N` line in the full report yet',
                        rank=26, sources=[s])
        else:
            self.gate('package.check', 'Packaging', 'tests/s5/check_package.py on the published package', 'exit %d' % rc,
                      'exit 0', PASS if rc == 0 else FAIL, s, rank=26)

    def run(self):
        self.compile_gates()
        L, s = self.link_gates()
        self.abi_gates(L, s)
        self.equiv_gates()
        self.static_gates()
        self.fuzz_gates()
        self.boot_gates()
        self.frames_gate()
        self.perf_gates()
        self.render_gate()
        self.budget_gate()
        self.package_gates()
        return self


# ------------------------------------------------------------------------------------------------- output

ORDER = ['Compile', 'Link', 'ABI', 'Rule equivalence', 'Static target hazards', 'Fuzz', 'Emulated boot',
         'Frame and scene comparison', 'Performance', 'Packaging']


def verdict(gates):
    c = {PASS: 0, FAIL: 0, NODATA: 0, 'stale': 0, 'informative': 0}
    for g in gates:
        if not g.get('required', True):
            c['informative'] += 1
            continue
        if g['status'] == PASS and g['stale']:
            c['stale'] += 1             # a stale pass is not a pass (R0 regression detector)
        else:
            c[g['status']] += 1
    ready = c[FAIL] == 0 and c[NODATA] == 0 and c['stale'] == 0
    return ('READY' if ready else 'NOT READY'), c


def distance(b):
    if b is None:
        return 'not on the head\'s history'
    return '%d ahead' % -b if b < 0 else '%d behind' % b


def short_list(xs, n=6):
    return ', '.join(xs) if len(xs) <= n else '%s, ... %s (%d)' % (', '.join(xs[:n - 1]), xs[-1], len(xs))


def fmt_src(srcs):
    if not srcs:
        return '-'
    if len(srcs) == 1:
        s = srcs[0]
        return '`%s:%s` @%s (%s)' % (s['branch'], s['path'], s['commit'] or '?', distance(s['behind']))
    bs = [s['behind'] for s in srcs]
    known = [b for b in bs if b is not None]
    worst = 'up to %s' % distance(max(known)) if known else distance(None)
    if None in bs and known:
        worst += ', %d not on the head\'s history' % bs.count(None)
    farms = sorted(set(s['branch'].split('/')[-1] for s in srcs), key=lambda f: (re.sub(r'\d+$', '', f),
                                                                                 int(re.search(r'\d*$', f).group() or 0)))
    commits = sorted(set(s['commit'] for s in srcs))
    return '%d reports (%s) @%s (%s)' % (len(srcs), short_list(farms), short_list(commits, 3), worst)


def blockers(gates, n=5):
    """the failing gates (and stale passes) that matter most, one per group: the lowest rank of each group"""
    best = {}
    for g in sorted(gates, key=lambda g: (g['rank'], g['id'])):
        if (g['status'] == FAIL or g['stale']) and g.get('required', True) and g['group'] not in best:
            best[g['group']] = g
    return sorted(best.values(), key=lambda g: (g['rank'], g['id']))[:n]


def missing(gates):
    return sorted((g for g in gates if g['status'] == NODATA and g.get('required', True)),
                  key=lambda g: (g['rank'], g['id']))


def scorecard(gates, th):
    """the honest scorecard: one number per stage, the gap still to close, taken from the gates (so from the farm
    reports they cite). Rows: [{stage, what, value, target, gap, unit, status: done|open|no-data, stale, gates,
    sources, note}]; gap is how far the value is from its target (0 = done), None when nobody measures it"""
    G = {g['id']: g for g in gates}
    sc = th.get('scorecard', {})
    fps = sc.get('frame_fps', 60)
    clocks = {c['name']: c for c in th['perf']['clocks']}
    clk = clocks.get(sc.get('frame_clock', 'stock')) or list(clocks.values())[0]
    target_ms = round(1000.0 / fps, 1)
    rows = []

    def row(rid, stage, what, ids, value, target, gap, unit='', note=''):
        gs = [G[i] for i in ids if i in G]
        srcs = list({(s['branch'], s['path']): s for g in gs for s in g['sources']}.values())
        sup = sorted(set(s['superseded_by'] for s in srcs if s.get('superseded_by')))
        if sup:
            # the farm's newer run failed: the number shown is an older run's, so this stage is not measured now
            gap, note = None, 'newer report failed or unreadable: %s; %s' % (', '.join(sup), note)
        st = 'no-data' if gap is None else 'done' if gap <= 0 else 'open'
        rows.append(dict(id=rid, stage=stage, what=what, value=value, target=target, gap=gap, unit=unit, status=st,
                         stale=any(s['stale'] for s in srcs), gates=[g['id'] for g in gs], sources=srcs, note=note))

    def val(i, key='value'):
        g = G.get(i)
        if not g or g['status'] == NODATA:
            return None
        v = g.get(key)
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    e = val('compile.census', 'errors')
    row('compile.errors', 'compile', 'census errors', ['compile.census'], e, 0, e,
        note='%s failing units' % G['compile.census'].get('failing', '?') if 'compile.census' in G else '')
    u = val('link.undefined')
    row('link.undefined', 'link', 'trial-link undefined names', ['link.undefined'], u, 0, u)
    tr = val('link.traps')
    row('link.traps', 'link', 'guest functions bound to trap stubs', ['link.traps'], tr, 0, tr)
    x, m = val('abi.x86_inconsistent'), val('abi.manual')
    row('abi.rows', 'ABI', 'ABI rows open (x86-inconsistent not explained + manual)', ['abi.x86_inconsistent', 'abi.manual'],
        None if x is None or m is None else x + m, 0, None if x is None or m is None else x + m,
        note='x86-inconsistent %s, manual %s' % (x, m))
    fd, ff = val('fuzz.hard', 'divergences'), val('fuzz.hard')
    fz = fd if fd is not None else ff
    row('fuzz.hard', 'fuzz', 'hard divergences (real-bug + triage)' if fd is not None else 'functions with a hard divergence',
        ['fuzz.hard', 'fuzz.coverage'], fz, 0, fz,
        note='%s functions; coverage %s' % (ff, G['fuzz.coverage']['value'] if 'fuzz.coverage' in G else '-'))
    ec, en = val('equiv.code'), val('equiv.nobuild')
    eq = None if ec is None or en is None else ec + en
    row('equiv.diffs', 'equivalence', 'S11 diffs not by design (functions + units)', ['equiv.code', 'equiv.nobuild'], eq, 0, eq,
        note='functions %s, units %s' % (ec, en))
    names = th['boot_milestones']
    bg = [G.get('boot.' + n) for n in names]
    known = [g for g in bg if g and g['status'] != NODATA]
    reached = sum(1 for g in bg if g and g['status'] == PASS)
    real = G.get('boot.real_exe', {}).get('status')
    row('boot.milestones', 'boot', 'milestones reached on the real exe', ['boot.real_exe'] + ['boot.' + n for n in names],
        '%d/%d' % (reached, len(names)), '%d/%d' % (len(names), len(names)),
        (len(names) - reached) if known or real == FAIL else None, 'milestones',
        note='real exe: %s; %s' % (real or '-', ', '.join('%s %s' % (n, g['status'] if g else '-')
                                                          for n, g in zip(names, bg))))
    fr = G.get('frames.compare')
    ok, n = (fr.get('scenes_ok'), fr.get('scenes')) if fr and fr['status'] != NODATA else (None, None)
    row('render.scenes', 'render parity', 'S16 scenes matching the reference', ['frames.compare'],
        None if n is None else '%d/%d' % (ok, n), 'all', None if n is None else (n - ok) if n else None, 'scenes',
        note=fr['note'] if fr else '')
    bgid = 'perf.budget.' + clk['name']
    b = G.get(bgid)
    wf = dct(b.get('worst_frame_ms')) if b else {}
    mid = wf.get('mid') if posnum(wf.get('mid')) else None      # S15's headline number; lo / hi in the note
    row('frame.budget', 'frame budget', 'worst frame at %.2f GHz (S15, mid case), target %d fps' % (clk['hz'] / 1e9, fps), [bgid],
        mid, target_ms, None if mid is None else round(max(0.0, mid - target_ms), 1), 'ms',
        note='lo %s, hi %s; %s' % (wf.get('lo', '?'), wf.get('hi', '?'),
                                     'measured: ' + b['measured'] if b and b.get('measured') else
                                     'model only [E], not measured'))
    cfg = sc.get('gpu_config', 'handheld_720p')
    gg = G.get('perf.gpu.' + cfg)
    gh = dct(gg.get('ms')).get('hi') if gg else None
    row('frame.gpu', 'frame budget', 'GPU frame, %s (render model, efficiency 0.6)' % cfg.replace('_', ' '), ['perf.gpu.' + cfg],
        gh, target_ms, None if not posnum(gh) else round(max(0.0, gh - target_ms), 1), 'ms',
        note='model, not measured (RENDER_PERF.md section 9)')
    return rows


def trend_entry(head, generated, rows):
    """one fnvnx-trend/1 entry: per scorecard row its gap (None: not measured) and the source commits it was
    measured at, oldest (largest distance) to newest"""
    m = {}
    for r in rows:
        cs = sorted(((s['behind'] if s['behind'] is not None else 1 << 30), s['commit']) for s in r['sources']
                    if s.get('commit'))
        m[r['id']] = dict(gap=r['gap'], value=r['value'], **{'from': cs[-1][1] if cs else None,
                                                             'to': cs[0][1] if cs else None})
    return dict(head=(head or '')[:12], generated=generated, metrics=m)


def trend_update(prior, entry, keep=400):
    """the trend with entry appended; a re-score of the same head that changes nothing replaces the last entry"""
    tr = [e for e in prior if isinstance(e, dict) and isinstance(e.get('metrics'), dict)]
    if tr and tr[-1].get('head') == entry['head'] and tr[-1]['metrics'] == entry['metrics']:
        tr[-1] = entry
    else:
        tr.append(entry)
    return tr[-keep:]


def regressions(trend):
    """every metric whose gap rose (or whose measurement was lost) from one entry to the next: {metric, kind:
    rose | lost, before, after, range: {from, to} (source commits: the earlier entry's oldest .. the later entry's
    newest, the commits to bisect), heads: {from, to}, open}. A regression closes when a later entry is back at or
    below its `before`"""
    out = []
    ids = []
    for e in trend:
        ids += [k for k in e['metrics'] if k not in ids]
    for mid in ids:
        last, last_num, opened = None, None, []
        for e in trend:
            o = e['metrics'].get(mid)
            if o is None:
                continue
            g = o.get('gap')
            if last is not None:
                kind = None
                if g is None and last.get('gap') is not None:
                    kind, base, bo = 'lost', last['gap'], last
                elif g is not None and last_num is not None and g > last_num[0]['gap']:
                    kind, base, bo = 'rose', last_num[0]['gap'], last_num[0]
                if kind:
                    r = dict(metric=mid, kind=kind, before=base, after=g,
                             range={'from': bo.get('from'), 'to': o.get('to')},
                             heads={'from': (last_num[1] if kind == 'rose' else last_head), 'to': e['head']},
                             open=True)
                    out.append(r)
                    opened.append(r)
            if g is not None:
                for r in opened:
                    if r['open'] and g <= r['before']:
                        r['open'] = False
                        r['closed_at'] = e['head']
                opened = [r for r in opened if r['open']]
                last_num = (o, e['head'])
            last, last_head = o, e['head']
    return out


def regressions_md(regs):
    op = [r for r in regs if r['open']]
    L = ['## Regressions', '', 'Metrics of the scorecard that rose (or lost their measurement) between two consecutive '
         'scored heads and have not come back; bisect the source commit range (fnvnx-trend/1 in the JSON).', '']
    if not op:
        return L + ['None open.', '']
    L += ['| metric | change | source commits to bisect | heads |', '|---|---|---|---|']
    for r in op:
        L.append('| %s | %s %s -> %s | %s..%s | %s..%s |' % (
            r['metric'], r['kind'], '-' if r['before'] is None else r['before'],
            '-' if r['after'] is None else r['after'], r['range']['from'] or '?', r['range']['to'] or '?',
            r['heads']['from'] or '?', r['heads']['to'] or '?'))
    return L + ['']


def prior_trend(store, th):
    """the trend of the newest gates report that carries one: reports/<trend.farm>/*-gates.json"""
    farm = dct(th.get('trend')).get('farm', 'gates1')
    br = 'reports/' + farm
    for path, _ in store.files(br) if br in store.branches(br) else []:
        if not path.endswith('-gates.json'):
            continue
        try:
            d = json.loads(store.read(br, path) or '')
        except ValueError:
            continue
        if isinstance(d, dict) and isinstance(d.get('trend'), list):
            return d['trend']
    return []


def scorecard_md(rows):
    L = ['## Scorecard', '', 'One number per stage and the gap still to close (0 = done); sources are the farm reports '
         'the gates cite (docs/READINESS.md, "Scorecard").', '',
         '| stage | what | value | target | gap | status | source | note |', '|---|---|---|---|---|---|---|---|']
    fmt = lambda v: '-' if v is None else '{:,}'.format(v) if isinstance(v, int) else str(v)
    for r in rows:
        L.append('| %s | %s | %s | %s | %s | **%s%s** | %s | %s |' % (
            r['stage'], r['what'], fmt(r['value']), fmt(r['target']),
            fmt(r['gap']) + (' ' + r['unit'] if r['unit'] and r['gap'] is not None else ''), r['status'],
            ' (stale)' if r['stale'] and r['status'] == 'done' else '', fmt_src(r['sources']),
            str(r['note']).replace('|', '/')))
    return L + ['']


def markdown(head, gates, risks, generated, rows=None, regs=None):
    v, c = verdict(gates)
    L = ['# fnv-nx first-launch readiness %s' % (head or '?')[:12], '',
         '- integration head: `%s`; generated %s' % (head or '?', generated),
         '- verdict: **%s**: %d pass, %d fail, %d no-data, %d stale passes, of %d gates; %d more informative' % (
             v, c[PASS], c[FAIL], c[NODATA], c['stale'], len(gates) - c['informative'], c['informative']),
         '- thresholds: tools/gates/thresholds.json; gates and why: docs/READINESS.md', '']
    if rows:
        L += scorecard_md(rows)
    if regs is not None:
        L += regressions_md(regs)
    for grp in ORDER:
        gs = [g for g in gates if g['group'] == grp]
        if not gs:
            continue
        L += ['## %s' % grp, '', '| gate | value | threshold | status | source | note |', '|---|---|---|---|---|---|']
        for g in gs:
            st = g['status'] + (' (stale)' if g['stale'] else '') + ('' if g.get('required', True) else ', informative')
            val = '-' if g['value'] is None else '{:,}'.format(g['value']) if isinstance(g['value'], int) else str(g['value'])
            L.append('| %s | %s | %s | **%s** | %s | %s |' % (g['title'], val, g['threshold'], st,
                                                            fmt_src(g['sources']), g['note'].replace('|', '/')))
        L.append('')
    L += ['## Top blockers', '', 'The failing gate that matters most in each group, ranked (docs/READINESS.md).', '']
    for i, g in enumerate(blockers(gates), 1):
        L.append('%d. %s: **%s** (%s): %s against %s. %s' % (
            i, g['group'], g['title'], g['status'] + (' stale' if g['stale'] else ''),
            '-' if g['value'] is None else g['value'], g['threshold'], g['note']))
    nd = missing(gates)
    if nd:
        L += ['', '## Missing evidence', '', 'Gates no farm measures yet; the verdict stays NOT READY while any is open.', '']
        L += ['- %s: %s. %s' % (g['group'], g['title'], g['note']) for g in nd]
    L += ['', '## Hardware-only risks', '', '| risk | why the emulator cannot see it | mitigation | evidence on device |',
          '|---|---|---|---|']
    for r in risks:
        L.append('| %s | %s | %s | %s |' % (r['risk'], r['why'], r['mitigation'], r['check']))
    return '\n'.join(L) + '\n'


def load_render(th, port, fetch=False, fixtures=None, table=None, head=None):
    """the render machine's perf report: from <fixtures>/_render/report.json, else from the render branch of this
    repository (`git fetch origin <branch>` with --fetch; then refs/remotes/origin/<branch>:<path>), else the copy
    merged into this checkout (distances then count on the integration head)"""
    rp = th.get('render_perf', {})
    br, path = rp.get('branch', 'claude/port-nsw-render'), rp.get('path')
    if fixtures:
        f = os.path.join(fixtures, '_render', 'report.json')
        return dict(text=readf(f) if os.path.isfile(f) else None, branch=br, path=path,
                    hist=History(head='fixture', table=table or {}))
    g = lambda *a: subprocess.run(['git', '-C', port, *a], capture_output=True, text=True)
    if fetch:
        g('fetch', '-q', 'origin', br)
    ref = 'refs/remotes/origin/' + br
    tip = g('rev-parse', '--verify', '-q', ref).stdout.strip()
    r = g('show', '%s:%s' % (ref, path)) if tip and path else None
    if r is not None and r.returncode == 0:
        return dict(text=r.stdout, branch=br, path=path, hist=History(head=tip, repo=port))
    rel = re.sub(r'^ports/fnv-nx/', '', path or '')
    f = os.path.join(port, rel) if rel else ''
    if f and os.path.isfile(f):
        return dict(text=readf(f), branch='nx-agent (checkout)', path=rel, hist=History(head=head, repo=port))
    return dict(text=None, branch=br, path=path, hist=None)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--artifacts')
    ap.add_argument('--fetch', action='store_true')
    ap.add_argument('--fixtures')
    ap.add_argument('--head')
    ap.add_argument('--port', default=PORT, help='ports/fnv-nx checkout at the head (placement, PATCHES.md, ...)')
    ap.add_argument('--thresholds', default=os.path.join(HERE, 'thresholds.json'))
    ap.add_argument('--risks', default=os.path.join(HERE, 'hardware_risks.json'))
    ap.add_argument('--abiclass', help='directory of S1b abiclass.py tables, <link c12>.tsv (tools/farm/gates_job.sh)')
    ap.add_argument('--md')
    ap.add_argument('--json')
    a = ap.parse_args(argv)
    th = json.loads(readf(a.thresholds))
    if a.fixtures:
        store = DirStore(a.fixtures)
        cj = os.path.join(a.fixtures, '_commits.json')
        hist = History(head=a.head or 'fixture', table=json.loads(readf(cj)) if os.path.isfile(cj) else {})
        hist.patch_root = os.path.join(a.fixtures, '_patches')
    else:
        gd = a.artifacts or os.environ.get('ARTIFACTS') or '/home/user/fnv-nx-build'
        if os.path.isdir(os.path.join(gd, '.git')):
            gd = os.path.join(gd, '.git')
        store = GitStore(gd)
        if a.fetch:
            store.fetch()
        hist = History(head=a.head, repo=a.port)
    render = load_render(th, a.port, a.fetch, a.fixtures, hist.table, None if a.fixtures else hist.head)
    card = Card(store, hist, th, port=a.port, render=render, abiclass_dir=a.abiclass).run()
    risks = json.loads(readf(a.risks)) if os.path.isfile(a.risks) else []
    gen = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    rows = scorecard(card.gates, th)
    trend = trend_update(prior_trend(store, th), trend_entry(hist.head, gen, rows),
                         dct(th.get('trend')).get('keep', 400))
    regs = regressions(trend)
    md = markdown(hist.head, card.gates, risks, gen, rows, regs)
    v, c = verdict(card.gates)
    if a.md:
        writef(a.md, md)
    else:
        sys.stdout.write(md)
    if a.json:
        writef(a.json, json.dumps(dict(head=hist.head, generated=gen, verdict=v, counts=c, max_commits_behind=card.maxb,
                       gates=card.gates, blockers=[g['id'] for g in blockers(card.gates)],
                       missing=[g['id'] for g in missing(card.gates)], scorecard=rows, hardware_risks=risks,
                       trend_format='fnvnx-trend/1', trend=trend, regressions=regs),
                                  indent=1) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
