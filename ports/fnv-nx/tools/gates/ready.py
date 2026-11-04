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
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))
PASS, FAIL, NODATA = 'pass', 'fail', 'no-data'


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
    """fixtures: <root>/<branch>/<path>; publish time = file mtime."""

    def __init__(self, root):
        self.root = root

    def fetch(self):
        pass

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
                res.append((os.path.relpath(p, base), int(os.path.getmtime(p))))
        return sorted(res, key=lambda x: (-x[1], x[0]))

    def read(self, branch, path):
        p = os.path.join(self.root, branch, path)
        return open(p, errors='replace').read() if os.path.isfile(p) else None

    def exists(self, branch, path):
        return os.path.isfile(os.path.join(self.root, branch, path))


class History:
    """commits behind the integration head; negative: that many commits ahead of it (a farm ran a newer commit);
    None: unknown, or on another line of history."""

    def __init__(self, head=None, table=None, repo=PORT):
        self.table, self.repo, self.cache = table, repo, {}
        self.head = head or (None if table is not None else self._git('rev-parse', 'HEAD'))

    def _git(self, *a):
        r = subprocess.run(['git', '-C', self.repo, *a], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None

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


def parse_fuzz(text):
    m = re.search(r'^# fuzz \S+ shard (\d+)/(\d+)', text, re.M)
    if not m:
        return None
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
    return d


def parse_frameprof(text):
    """tools/perf/frameprof.py --json: [{label, instructions, subsystems, ...}]"""
    try:
        res = json.loads(text)
    except ValueError:
        return None
    if not isinstance(res, list):
        return None
    frames = [r['instructions'] for r in res if str(r.get('label', '')).startswith('frame ')]
    to_winmain, seen = 0, False
    for r in res:
        to_winmain += r.get('instructions', 0)
        if re.search(r'until (__fnv_0086a850|WinMain)', str(r.get('label', ''))):
            seen = True
            break
    return dict(frames=frames, to_winmain=to_winmain if seen else None, total=sum(r.get('instructions', 0) for r in res))


def placement_check(port):
    """core placement from toolchain/host/placement.h and the NPDM: main on core 0, workers on 1-2, core 3 opt-in"""
    ph = os.path.join(port, 'toolchain', 'host', 'placement.h')
    npdm = os.path.join(port, 'toolchain', 'npdm', 'fnvnx-app.json')
    if not (os.path.isfile(ph) and os.path.isfile(npdm)):
        return None
    t = open(ph).read()
    rows = {m.group(1): (int(m.group(2)), int(m.group(3), 0), int(m.group(4), 0)) for m in re.finditer(
        r'=\s*\{"([\w-]+)",\s*(-?\d+),\s*(0x[0-9A-Fa-f]+|\d+),\s*(0x[0-9A-Fa-f]+|\d+),', t)}
    core3 = first(r'#define FNVNX_CORE3_DEFAULT (\d+)', t)
    j = json.load(open(npdm))
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
    t = open(p).read()
    m = re.search(r'^## By-design MSVC differences.*?(?=^## )', t, re.M | re.S)
    return set(re.findall(r'\| 0x([0-9a-f]{8}) \|', m.group(0))) if m else set()


def static_verdicts(port):
    """class -> verdict of tools/static/report.py CLASSES (benign, per-site, rule, header, runtime, compiler, flag)"""
    p = os.path.join(port, 'tools', 'static', 'report.py')
    if not os.path.isfile(p):
        return {}
    import importlib.util
    spec = importlib.util.spec_from_file_location('fnvnx_static_report', p)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return {c[0]: c[3] for c in mod.CLASSES}
    except Exception:          # noqa: BLE001 - a broken classifier must not hide the other gates
        return {}


# ------------------------------------------------------------------------------------------------- scorecard

class Card:
    def __init__(self, store, hist, th, port=PORT, render=None):
        self.store, self.hist, self.th, self.port = store, hist, th, port
        self.render = render or {}      # load_render(): text, branch, path, hist (distances on the render branch)
        self.gates = []
        self.maxb = th.get('max_commits_behind', 25)

    # ---- sources
    def src(self, branch, path, commit, published=None):
        b = self.hist.behind(commit)
        return dict(branch=branch, path=path, commit=(commit or '')[:12], behind=b, published=published,
                    stale=b is None or b > self.maxb)   # b < 0: newer than the head, fresh

    def newest(self, prefix, pattern, parser, accept=lambda d: True):
        """{farm: (parsed, source)}: per farm branch under reports/<prefix>, the newest report matching pattern
        that parses and is accepted"""
        out = {}
        for br in self.store.branches('reports/' + prefix):
            farm = br.split('/', 1)[1]
            if re.match(r'try\d+of\d+$', farm):
                continue        # try farms run unmerged work
            for path, t in self.store.files(br):
                if not re.search(pattern, path):
                    continue
                text = self.store.read(br, path)
                if not text or failed_report(text):
                    continue
                d = parser(text)
                if d is None or not accept(d):
                    continue
                d['_text_commit'] = report_commit(text, path)
                out[farm] = (d, self.src(br, path, d['_text_commit'], t))
                break
        return out

    def gate(self, gid, group, title, value, threshold, status, sources, note='', rank=50):
        srcs = sources if isinstance(sources, list) else [sources] if sources else []
        stale = any(s['stale'] for s in srcs) if srcs else False
        self.gates.append(dict(id=gid, group=group, title=title, value=value, threshold=threshold, status=status,
                               stale=stale and status == PASS, sources=srcs, note=note, rank=rank))

    def nodata(self, gid, group, title, threshold, note, rank=50, sources=None):
        self.gate(gid, group, title, None, threshold, NODATA, sources or [], note, rank)

    # ---- link branch helpers
    def link_branch(self, commit):
        b = 'link/' + (commit or '')[:12]
        return b if commit and b in self.store.branches('link/') else None

    def link_json(self, branch, path):
        t = self.store.read(branch, path) if branch else None
        try:
            return json.loads(t) if t else None
        except ValueError:
            return None

    # ---- gates
    def compile_gates(self):
        units = self.th['units']
        c = self.newest('farm1', r'-census\.md$', parse_census)
        if 'farm1' in c:
            d, s = c['farm1']
            self.gate('compile.census', 'Compile', 'census: units that compile clean', '%d/%d' % (d['clean'], d['units']),
                      '%d/%d' % (units, units), PASS if d['clean'] == d['units'] == units else FAIL, s,
                      '%d failing, %s errors' % (d['failing'], d['errors']), rank=12)
        else:
            self.nodata('compile.census', 'Compile', 'census: units that compile clean', '%d/%d' % (units, units),
                        'no census report on reports/farm1', rank=12)
        n = self.th['compile_shards']
        shards = {}
        for farm, (d, s) in self.newest('farm', r'-compile\.md$', parse_compile, lambda d: d['n'] == n).items():
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
                      'apply failures %s' % L.get('apply_failures'), rank=9)
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
        if fb and fb[0][0].get('v1'):
            d, fs = fb[0]
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
            cl = {}
            for r in un:
                for c in re.sub(r'\([^)]*\)', '', r[6]).split(','):
                    if c not in explained:
                        cl[c] = cl.get(c, 0) + 1
            top = ', '.join('%s %d' % kv for kv in sorted(cl.items(), key=lambda x: -x[1])[:5])
            asrc = dict(s, branch=br, path='out/abi.tsv')
            self.gate('abi.x86_inconsistent', 'ABI', 'S2 x86-inconsistent pairs not explained',
                      len(un), '0', PASS if not un else FAIL, asrc,
                      '%d x86-inconsistent, %d explained (classes %s only); open: %s' % (
                          len(xinc), len(xinc) - len(un), '+'.join(sorted(explained)) or '-', top), rank=6)
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

    def equiv_gates(self):
        n = self.th['equiv_shards']
        shards = {}
        for farm, (d, s) in self.newest('eq', r'-equiv\.md$', parse_equiv, lambda d: d['n'] == n).items():
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
        for farm, (d, s) in self.newest('st', r'-static\.md$', parse_static, lambda d: d['n'] == n).items():
            if d['k'] not in shards or (s['published'] or 0) > (shards[d['k']][1]['published'] or 0):
                shards[d['k']] = (d, s)
        srcs = [v[1] for _, v in sorted(shards.items())]
        if len(shards) < n:
            self.nodata('static.hazards', 'Static target hazards', 'S12: open hazard sites', '0',
                        'static shards with a report: %d of %d' % (len(shards), n), rank=15, sources=srcs)
            return
        verdict = static_verdicts(self.port)
        closed = {}
        for cls, ev in self.th.get('static_closed', {}).items():
            if cls.startswith('_'):
                continue
            p = os.path.join(self.port, ev['file'])
            if os.path.isfile(p) and re.search(ev['regex'], open(p).read(), re.S):
                closed[cls] = ev['why']
        sites = set().union(*(d['sites'] for d, _ in shards.values()))
        exits = [d['exit'] for d, _ in shards.values()]
        uncovered = sum((d['units'] or 0) - (d['gcc'] or 0) for d, _ in shards.values())
        per = {}
        for f, l, c in sites:
            if verdict.get(c, 'per-site') != 'benign' and c not in closed:
                per[c] = per.get(c, 0) + 1
        openn = sum(per.values())
        self.gate('static.hazards', 'Static target hazards', 'S12: open hazard sites (non-benign classes, fix not landed)',
                  openn, '0', PASS if openn == 0 else FAIL, srcs, '%d unique sites; closed by a landed fix: %s; top open: '
                  '%s' % (len(sites), ', '.join(sorted(closed)) or 'none',
                          ', '.join('%s %d' % kv for kv in sorted(per.items(), key=lambda x: -x[1])[:6])), rank=14)
        self.gate('static.gate', 'Static target hazards', 'S12 gate: shards over baseline / units GCC did not compile',
                  '%d / %d' % (sum(1 for e in exits if e), uncovered), '0 / 0',
                  PASS if not any(exits) and uncovered == 0 else FAIL, srcs, rank=18)

    def fuzz_gates(self):
        n = self.th['fuzz_shards']
        shards = {}
        for farm, (d, s) in self.newest('fz', r'-fuzz\.md$', parse_fuzz, lambda d: d['n'] == n).items():
            if d['k'] not in shards or (s['published'] or 0) > (shards[d['k']][1]['published'] or 0):
                shards[d['k']] = (d, s)
        srcs = [v[1] for _, v in sorted(shards.items())]
        missing = sorted(set(range(1, n + 1)) - set(shards))
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
        if missing:
            note += '; shards without a %d-shard report: %s' % (n, ','.join(map(str, missing)))
        st = FAIL if un else (PASS if not missing else NODATA)
        self.gate('fuzz.hard', 'Fuzz', 'S13: hard divergences not by design', len(un) if shards else None, '0',
                  st if shards else NODATA, srcs, note, rank=11)
        if shards:
            with_obj = sum((d['shard'] or 0) - (d['no_native'] or 0) for d, _ in shards.values())
            ver = sum(d['verdicts'] or 0 for d, _ in shards.values())
            nm = sum(d['not_modelled'] or 0 for d, _ in shards.values())
            cov = ver / with_obj if with_obj else 0
            want = self.th.get('fuzz_min_coverage', 0.9)
            self.gate('fuzz.coverage', 'Fuzz', 'S13: functions with a verdict / functions with a native object',
                      '%.1f %% (%d/%d)' % (100 * cov, ver, with_obj), '>= %.0f %%, all %d shards' % (100 * want, n),
                      PASS if cov >= want and not missing else FAIL, srcs,
                      '%d not modelled (indirect control flow); %d of %d shards reported' % (nm, len(shards), n), rank=19)
        else:
            self.nodata('fuzz.coverage', 'Fuzz', 'S13 coverage', '>= 90 %', 'no fuzz report', rank=19)

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
        want = (self.store.read('gamedata/exe', 'FalloutNV.exe.sha256') or '').split()
        want = want[0] if want else None
        got = meta.get('boot_image_sha256')
        if want and got:
            real = got == want
            self.gate('boot.real_exe', 'Emulated boot', 'boot ran on the player\'s FalloutNV.exe', 'yes' if real else
                      'no (%s)' % ('synthesized image' if 'synth' in (d.get('image') or '') else 'another image'), 'yes',
                      PASS if real else FAIL, s, 'image sha256 %s.., exe %s..' % (got[:8], want[:8]), rank=1)
        else:
            real = None
            self.nodata('boot.real_exe', 'Emulated boot', 'boot ran on the player\'s FalloutNV.exe', 'yes',
                        'no image hash in meta.json' if want else 'gamedata/exe not readable', rank=1, sources=[s])
        reached = self.milestones(br, meta, d)
        for i, m in enumerate(names):
            r = reached.get(m)
            if r is None:
                self.nodata('boot.' + m, 'Emulated boot', 'milestone: ' + m, 'reached on the real exe',
                            'no machine-readable milestone `%s` yet (S5)' % m, rank=2 + i, sources=[s])
                continue
            ok = bool(r) and real is True
            note = '' if real else 'on the synthesized image only' if r else ''
            self.gate('boot.' + m, 'Emulated boot', 'milestone: ' + m, 'yes' if r else 'no', 'reached on the real exe',
                      PASS if ok else FAIL, s, note, rank=2 + i)
        sk = d.get('init_skips')
        self.gate('boot.init_skips', 'Emulated boot', 'static initializers skipped after a fault', sk, '0',
                  NODATA if sk is None else PASS if sk == 0 else FAIL, s, (d.get('stop') or '')[:120], rank=6)

    def milestones(self, br, meta, d):
        """S5's machine-readable milestones: boot/milestones.json ({name: 0|1}), meta.json boot_<name>, or
        `fnvnx milestone: <name>` lines in boot/summary.txt; WinMain also from the report"""
        out = {}
        mj = self.link_json(br, 'boot/milestones.json') if br else None
        if isinstance(mj, dict):
            for k, v in (mj.get('milestones', mj) or {}).items():
                out[k] = int(bool(v))
        for k in self.th['boot_milestones']:
            if 'boot_' + k in meta and k not in out:
                out[k] = int(bool(meta['boot_' + k]))
        summ = self.store.read(br, 'boot/summary.txt') if br else None
        if summ:
            for m in re.findall(r'fnvnx milestone: (\w+)', summ):
                out[m] = 1
            if 'winmain' not in out and 'fnvnx boot: calling WinMain' in summ:
                out['winmain'] = 1
        if 'winmain' not in out and d.get('winmain') is not None:
            out['winmain'] = d['winmain']
        return out

    def frames_gate(self):
        hits = []
        for br in self.store.branches('reports/'):
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
            g = json.loads(self.store.read(br, path))
        except ValueError:
            g = None
        if not isinstance(g, dict) or not isinstance(g.get('scenes'), list):
            self.nodata('frames.compare', 'Frame and scene comparison', 'S16: scenes matching the reference',
                        'all scenes', 'unreadable gate file', rank=5)
            return
        sc = g['scenes']
        ok = sum(1 for x in sc if x.get('pass'))
        self.gate('frames.compare', 'Frame and scene comparison', 'S16: scenes matching the reference',
                  '%d/%d' % (ok, len(sc)), 'all scenes', PASS if sc and ok == len(sc) else FAIL,
                  self.src(br, path, g.get('commit'), t), ', '.join(x.get('name', '?') for x in sc if not x.get('pass')),
                  rank=5)

    def perf_gates(self):
        p = self.th['perf']
        budget = p['cpu_hz'] / p['fps'] * p['ipc']
        hits = []
        for br in self.store.branches('reports/'):
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
        if prof and prof['frames']:
            worst = max(prof['frames'])
            self.gate('perf.frame', 'Performance', 'main-thread instructions per frame (worst frame)',
                      '{:,}'.format(worst), '<= {:,} ({} fps, IPC {}, {:.2f} GHz)'.format(int(budget), p['fps'], p['ipc'],
                                                                                    p['cpu_hz'] / 1e9),
                      PASS if worst <= budget else FAIL, ps, '%d frames profiled' % len(prof['frames']), rank=22)
        else:
            self.nodata('perf.frame', 'Performance', 'main-thread instructions per frame (worst frame)',
                        '<= {:,}'.format(int(budget)), 'no frame reached in a profiled run' if prof else
                        'no frameprof run published', rank=22, sources=[ps] if ps else None)
        if prof and prof['to_winmain'] is not None:
            sec = prof['to_winmain'] / p['ipc'] / p['cpu_hz']
            self.gate('perf.boot', 'Performance', 'estimated device time from start to WinMain', '%.2f s' % sec,
                      '<= %.1f s' % p['boot_to_winmain_s'], PASS if sec <= p['boot_to_winmain_s'] else FAIL, ps,
                      '{:,} instructions'.format(prof['to_winmain']), rank=21)
        else:
            self.nodata('perf.boot', 'Performance', 'estimated device time from start to WinMain',
                        '<= %.1f s' % p['boot_to_winmain_s'], 'no frameprof run reaching WinMain', rank=21,
                        sources=[ps] if ps else None)
        mem = self.th['memory']
        avail = mem['heap_region_bytes'] - mem['host_heap_bytes']
        fb = sorted(self.full.values(), key=lambda x: (x[1]['behind'] if x[1]['behind'] is not None else 1 << 30))
        meta = self.link_json(self.link_branch(fb[0][0]['_text_commit']), 'meta.json') if fb else None
        peak = (meta or {}).get('boot_peak_heap_bytes')
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
        """the render machine's summary block: main and render thread frame times against one frame"""
        rp = self.th.get('render_perf', {})
        lim = rp.get('frame_ms', 1000.0 / self.th['perf']['fps'])
        title = 'render perf: frame time, main / render thread (S6n render machine)'
        thr = '<= %.1f ms each' % lim
        r = self.render
        where = 'nx-agent:%s' % (r.get('branch') or rp.get('branch', '?'))
        try:
            j = json.loads(r['text']) if r.get('text') else None
        except ValueError:
            j = None
        if isinstance(j, dict) and not isinstance(j.get('frame_ms'), dict) and isinstance(j.get('summary'), dict):
            j = j['summary']
        if not isinstance(j, dict) or not isinstance(j.get('frame_ms'), dict):
            self.nodata('perf.render', 'Performance', title, thr,
                        'unreadable summary block in %s' % rp.get('path') if r.get('text') else
                        'no render perf report (%s:%s) yet' % (where, rp.get('path')), rank=22)
            return
        c = str(j.get('commit') or '')
        h = r.get('hist')
        b = h.behind(c) if h and c else None
        src = dict(branch=where, path=r.get('path') or rp.get('path'), commit=c[:12], behind=b, published=None,
                   stale=b is None or b > self.maxb)
        fm = j['frame_ms']
        vals = [fm.get('main'), fm.get('render')]
        if any(not isinstance(v, (int, float)) for v in vals):
            self.gate('perf.render', 'Performance', title, None, thr, NODATA, src, 'frame_ms.main/render missing',
                      rank=22)
            return
        pd = j.get('per_draw_us') or {}
        asm = j.get('assumptions') or {}
        note = 'per draw: native %s us, Nine %s us; assumes IPC %s at %s GHz' % (
            pd.get('native', '?'), pd.get('nine', '?'), asm.get('ipc', '?'), asm.get('clock_ghz', '?'))
        self.gate('perf.render', 'Performance', title, '%.1f / %.1f ms' % tuple(vals), thr,
                  PASS if max(vals) <= lim else FAIL, src, note, rank=22)

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
                  'set PROGRAM_ID=<the installed title the exefs overrides>', rank=20)
        pc = d.get('pcrel_abs')
        self.gate('package.pic', 'Packaging', 'guest PC-relative absolute references (D9 relocatable)', pc, '0',
                  NODATA if pc is None else PASS if pc == 0 else FAIL, s, rank=26)
        self.nodata('package.check', 'Packaging', 'tests/s5/check_package.py on the published package', 'exit 0',
                    'no farm runs check_package.py on the package yet (the package is not published)', rank=26)

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
        self.package_gates()
        return self


# ------------------------------------------------------------------------------------------------- output

ORDER = ['Compile', 'Link', 'ABI', 'Rule equivalence', 'Static target hazards', 'Fuzz', 'Emulated boot',
         'Frame and scene comparison', 'Performance', 'Packaging']


def verdict(gates):
    c = {PASS: 0, FAIL: 0, NODATA: 0, 'stale': 0}
    for g in gates:
        c[g['status']] += 1
        c['stale'] += g['stale']
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
                                                                                 int(re.sub(r'^\D*', '', f) or 0)))
    commits = sorted(set(s['commit'] for s in srcs))
    return '%d reports (%s) @%s (%s)' % (len(srcs), short_list(farms), short_list(commits, 3), worst)


def blockers(gates, n=5):
    """the failing gates (and stale passes) that matter most, one per group: the lowest rank of each group"""
    best = {}
    for g in sorted(gates, key=lambda g: (g['rank'], g['id'])):
        if (g['status'] == FAIL or g['stale']) and g['group'] not in best:
            best[g['group']] = g
    return sorted(best.values(), key=lambda g: (g['rank'], g['id']))[:n]


def missing(gates):
    return sorted((g for g in gates if g['status'] == NODATA), key=lambda g: (g['rank'], g['id']))


def markdown(head, gates, risks, generated):
    v, c = verdict(gates)
    L = ['# fnv-nx first-launch readiness %s' % (head or '?')[:12], '',
         '- integration head: `%s`; generated %s' % (head or '?', generated),
         '- verdict: **%s**: %d pass, %d fail, %d no-data, %d stale passes, of %d gates' % (
             v, c[PASS], c[FAIL], c[NODATA], c['stale'], len(gates)),
         '- thresholds: tools/gates/thresholds.json; gates and why: docs/READINESS.md', '']
    for grp in ORDER:
        gs = [g for g in gates if g['group'] == grp]
        if not gs:
            continue
        L += ['## %s' % grp, '', '| gate | value | threshold | status | source | note |', '|---|---|---|---|---|---|']
        for g in gs:
            st = g['status'] + (' (stale)' if g['stale'] else '')
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


def load_render(th, port, fetch=False, fixtures=None, table=None):
    """the render machine's perf report: from <fixtures>/_render/report.json, else from the render branch of this
    repository (`git fetch origin <branch>` with --fetch; then refs/remotes/origin/<branch>:<path>)"""
    rp = th.get('render_perf', {})
    br, path = rp.get('branch', 'claude/port-nsw-render'), rp.get('path')
    if fixtures:
        f = os.path.join(fixtures, '_render', 'report.json')
        return dict(text=open(f).read() if os.path.isfile(f) else None, branch=br, path=path,
                    hist=History(head='fixture', table=table or {}))
    g = lambda *a: subprocess.run(['git', '-C', port, *a], capture_output=True, text=True)
    if fetch:
        g('fetch', '-q', 'origin', br)
    ref = 'refs/remotes/origin/' + br
    tip = g('rev-parse', '--verify', '-q', ref).stdout.strip()
    r = g('show', '%s:%s' % (ref, path)) if tip and path else None
    return dict(text=r.stdout if r is not None and r.returncode == 0 else None, branch=br, path=path,
                hist=History(head=tip, repo=port) if tip else None)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--artifacts')
    ap.add_argument('--fetch', action='store_true')
    ap.add_argument('--fixtures')
    ap.add_argument('--head')
    ap.add_argument('--port', default=PORT, help='ports/fnv-nx checkout at the head (placement, PATCHES.md, ...)')
    ap.add_argument('--thresholds', default=os.path.join(HERE, 'thresholds.json'))
    ap.add_argument('--risks', default=os.path.join(HERE, 'hardware_risks.json'))
    ap.add_argument('--md')
    ap.add_argument('--json')
    a = ap.parse_args(argv)
    th = json.load(open(a.thresholds))
    if a.fixtures:
        store = DirStore(a.fixtures)
        cj = os.path.join(a.fixtures, '_commits.json')
        hist = History(head=a.head or 'fixture', table=json.load(open(cj)) if os.path.isfile(cj) else {})
    else:
        gd = a.artifacts or os.environ.get('ARTIFACTS') or '/home/user/fnv-nx-build'
        if os.path.isdir(os.path.join(gd, '.git')):
            gd = os.path.join(gd, '.git')
        store = GitStore(gd)
        if a.fetch:
            store.fetch()
        hist = History(head=a.head, repo=a.port)
    render = load_render(th, a.port, a.fetch, a.fixtures, hist.table)
    card = Card(store, hist, th, port=a.port, render=render).run()
    risks = json.load(open(a.risks)) if os.path.isfile(a.risks) else []
    gen = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    md = markdown(hist.head, card.gates, risks, gen)
    v, c = verdict(card.gates)
    if a.md:
        open(a.md, 'w').write(md)
    else:
        sys.stdout.write(md)
    if a.json:
        json.dump(dict(head=hist.head, generated=gen, verdict=v, counts=c, max_commits_behind=card.maxb,
                       gates=card.gates, blockers=[g['id'] for g in blockers(card.gates)],
                       missing=[g['id'] for g in missing(card.gates)], hardware_risks=risks),
                  open(a.json, 'w'), indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
