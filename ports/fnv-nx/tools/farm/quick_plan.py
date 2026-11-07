#!/usr/bin/env python3
"""quick_plan.py - what quick_job.sh must run for a commit, from its diff against the last verified head (T1-fastbench).

    quick_plan.py plan <repo> <base> <head> [--out plan.json]     the checks and why
    quick_plan.py cover <capture store> [--max N] [--out cells.tsv] scenes that cover the store's programs

plan: every path changed under ports/fnv-nx/ is put in one class (CLASSES, first match wins); the union of the classes'
checks is the plan. A path no class names selects everything (unknown means unsafe). The checks:
  build    prepare, compile, link, guest, emulated boot (full_job.sh; prepare, compile and link are incremental)
  equiv    the MSVC equivalence gate (equiv_job.sh 1 of 1; the object cache recompiles only changed units)
  fuzz     differential fuzzing (fuzz_job.sh 1 of 1; the verdict cache re-fuzzes only functions whose key changed)
  audit    the overhead audit (audit_job.sh 1 of 1; the row cache re-measures only changed keys)
  replay   the cell sweep on cached reference captures (sweep_job.sh, SWEEP_CAPTURE=replay, the cover scenes)
  programs the shader-program sweep (sweep_job.sh's perm.py part), when the translator changed
  frames   the per-thread CPU and frame-pacing lane (frames_job.sh): the profiler's tests, then this head's guest under
           linhost --profile, frames cut at the D3D9 Present (after build, on its build directory)
  mem      the memory lane (mem_job.sh): mem.py's tests, then this head's guest relinked with the recorder and run
           under qemu-aarch64; the peak against the 3.2 GiB budget, failures, top sites (after build)
  threads  D25 #7's report lane (threads_job.sh): the race checker's and the watchdog's tests, then this head's
           guest relinked with the watchdog and run under qemu-aarch64 (after build, on its build directory)
The caches make each selected check cost what changed; the plan only skips checks that nothing can affect.
A plan with "full" set needs the full farm run: a key that every cached result of a check depends on changed - the
toolchain or the pins (FULL), a version constant (KEYS: difffuzz HARNESS, AUDIT_HARNESS, unitcache CACHE_V, the
capture store's FORMAT / REF_FORMAT / CAPTURE_V) or a capture-key input (CAPTURE_INPUTS). quick_job still runs the
selected checks; the sharded farms refill the cold cache (the rule for when a full run is needed).

cover: a greedy set cover of the VS and PS program hashes in the capture store's index (capstore.py), most new
programs first, ties by the smaller capture; the rows are cells.tsv-shaped (id, kind, cell, world) from the store's
cells.tsv lines when the index holds them, else (id) alone.
"""
import argparse, json, os, re, subprocess, sys

ALL = ('build', 'equiv', 'fuzz', 'audit', 'replay', 'programs', 'threads', 'frames', 'mem')
# (regex on the path relative to ports/fnv-nx/, checks); first match wins
CLASSES = [
    (r'^(docs/|journal/|JOURNAL\.md$|README\.md$|reports/)', ()),
    (r'^tools/farm/(quick_plan\.py|README\.md)$', ()),
    (r'^tests/s6n/', ('replay', 'build')),            # nx_render's build
    (r'^tests/s5/', ('build',)),                      # the boot scripts
    (r'^tests/s15/mtqemu/', ('threads', 'frames', 'mem')),  # the race checker, linhost (the profiler), boot_game.sh
    (r'^tests/s15/(test_mtqemu|test_threads_report)\.py$', ('threads',)),
    (r'^tests/s15/test_frames\.py$', ('frames',)),
    (r'^tests/s15/test_mem_report\.py$', ('mem',)),
    (r'^tests/', ()),                                 # tests run by the streams and V1, not by a check here
    (r'^render/shaders/', ('programs', 'replay', 'build')),
    (r'^render/', ('replay', 'build')),               # the guest links the native renderer (LINK_OVERRIDES=render)
    (r'^tools/compare/', ('replay',)),                # the harness; a change to a capture-key input makes a new key
    (r'^tools/equiv/', ('equiv',)),
    (r'^tools/difffuzz/', ('fuzz', 'audit')),         # the audit runs on the fuzz harness and its keys
    (r'^tools/perf/(frames\.py|FRAMES\.md)$', ('frames',)),
    (r'^tools/perf/(mem\.py|mem_wrap\.c|mem_autostart\.c|MEM\.md)$', ('mem',)),
    (r'^tools/perf/', ('audit',)),
    (r'^tools/farm/(sweep_job|capstore)', ('replay',)),
    (r'^tools/farm/(equiv_job)', ('equiv',)),
    (r'^tools/farm/(fuzz_job)', ('fuzz', 'audit')),
    (r'^tools/farm/(audit_job)', ('audit',)),
    (r'^tools/farm/threads_job', ('threads',)),
    (r'^tools/farm/frames_job', ('frames',)),
    (r'^tools/farm/mem_job', ('mem',)),
    (r'^tools/threads/report\.py$', ('threads', 'frames', 'mem')),   # frames.py and mem.py import its Symbols
    (r'^tools/threads/variant\.sh$', ('threads', 'mem')),
    (r'^tools/threads/', ('threads',)),
    (r'^tools/farm/', ('build',)),                    # link/full/compile jobs and their library
]
# a change here invalidates every cached result of a check: quick_job runs it all (a full run on one machine is slow:
# the farms' sharded run is the better tool)
FULL = [
    (r'^toolchain/', 'the toolchain (every object, the guest and the boot)'),
    (r'^tools/fetch\.sh$', 'the pinned decomp, sse2neon, libnx (every key)'),
]
# version constants that are part of every key of one cache: a different value between base and head means that
# cache is cold (the sharded farms refill it: the rule for a full run of that check)
KEYS = [
    ('tools/difffuzz/fnkeys.py', r"^HARNESS = '([^']*)'", 'fuzz and audit: difffuzz HARNESS'),
    ('tools/perf/overhead_audit.py', r"^AUDIT_HARNESS = '([^']*)'", 'audit: AUDIT_HARNESS'),
    ('tools/equiv/unitcache.py', r"^CACHE_V = '([^']*)'", 'equivalence: unitcache CACHE_V'),
    ('tools/compare/capstore.py', r"^FORMAT = '([^']*)'", 'capture store: FORMAT (a new capture key)'),
    ('tools/compare/scene_stats.py', r"^REF_FORMAT = '([^']*)'", 'capture store: REF_FORMAT (a new capture key)'),
    ('tools/farm/sweep_job.sh', r"^CAPTURE_V=(\S+)", 'capture store: CAPTURE_V (a new capture key)'),
]
CAPTURE_INPUTS = r'^tools/compare/(wine_ref\.sh|nxd9trim\.py|nxd9norm\.py|nxd9io\.py|replay\.py|ref_ini\.py|d3d9/)'


def key_bumps(repo, base, head, paths):
    out = []
    for f, rx, what in KEYS:
        if f not in paths:
            continue
        vals = []
        for c in (base, head):
            r = subprocess.run(['git', '-C', repo, 'show', '%s:ports/fnv-nx/%s' % (c, f)], capture_output=True, text=True)
            m = re.search(rx, r.stdout, re.M) if r.returncode == 0 else None
            vals.append(m.group(1) if m else None)
        if vals[0] != vals[1]:
            out.append('%s: %s -> %s (%s)' % (f, vals[0], vals[1], what))
    out += ['%s: a capture key input (a new capture/<key>: one capture pass of the 36 sweep shards)' % p
            for p in paths if re.search(CAPTURE_INPUTS, p)]
    return out


def changed(repo, base, head):
    r = subprocess.run(['git', '-C', repo, 'diff', '--name-only', '%s..%s' % (base, head), '--', 'ports/fnv-nx'],
                       capture_output=True, text=True, check=True)
    return [l[len('ports/fnv-nx/'):] for l in r.stdout.splitlines() if l.startswith('ports/fnv-nx/')]


def plan(paths):
    checks, why, full = {}, {}, []
    for p in paths:
        for rx, cs in CLASSES:
            if re.search(rx, p):
                break
        else:
            cs = ALL
        for c in cs:
            checks[c] = True
            why.setdefault(c, []).append(p)
        for rx, w in FULL:
            if re.search(rx, p):
                full.append('%s: %s' % (p, w))
    # fuzz, audit and the boot need the link of this head; equiv only the prepared tree (build's prepare step)
    if checks.get('fuzz') or checks.get('audit'):
        checks['build'] = True
        why.setdefault('build', []).append('(fuzz/audit need this head\'s link)')
    return {'files': len(paths), 'checks': {c: bool(checks.get(c)) for c in ALL},
            'why': {c: sorted(set(v))[:20] for c, v in why.items()}, 'full': full}


def cover(store, limit=0):
    idx = os.path.join(store, 'index')
    scenes = {}
    for f in sorted(os.listdir(idx)) if os.path.isdir(idx) else []:
        if not f.endswith('.tsv'):
            continue
        d = dict(l.rstrip('\n').split('\t', 1) for l in open(os.path.join(idx, f)) if '\t' in l)
        progs = {'v' + h for h in d.get('vs', '').split(',') if h} | {'p' + h for h in d.get('ps', '').split(',') if h}
        scenes[f[:-4]] = (progs, int(d.get('size', 0)), d.get('cell', ''))
    left = set().union(*(v[0] for v in scenes.values())) if scenes else set()
    total, out = len(left), []
    while left and (not limit or len(out) < limit):
        best = max(scenes, key=lambda s: (len(scenes[s][0] & left), -scenes[s][1], s))
        gain = scenes[best][0] & left
        if not gain:
            break
        out.append((best, len(gain)))
        left -= gain
    return out, total, total - len(left)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('plan'); p.add_argument('repo'); p.add_argument('base'); p.add_argument('head'); p.add_argument('--out')
    p = sub.add_parser('cover'); p.add_argument('store'); p.add_argument('--max', type=int, default=0); p.add_argument('--out')
    a = ap.parse_args()
    if a.cmd == 'plan':
        paths = changed(a.repo, a.base, a.head)
        r = plan(paths)
        r['full'] += key_bumps(a.repo, a.base, a.head, paths)
        r.update(base=a.base, head=a.head)
        s = json.dumps(r, indent=1)
        open(a.out, 'w').write(s + '\n') if a.out else print(s)
        return 0
    sel, total, covered = cover(a.store, a.max)
    rows = []
    for sid, _ in sel:
        cell = dict(l.rstrip('\n').split('\t', 1) for l in open(os.path.join(a.store, 'index', sid + '.tsv')) if '\t' in l).get('cell')
        rows.append(cell if cell else sid)
    txt = ''.join(r + '\n' for r in rows)
    open(a.out, 'w').write(txt) if a.out else sys.stdout.write(txt)
    print('cover: %d scenes cover %d of %d programs' % (len(sel), covered, total), file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
