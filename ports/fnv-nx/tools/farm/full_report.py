#!/usr/bin/env python3
"""full_report.py - the text report of a farm full build (tools/farm/full_job.sh) from the pipeline's records.

    tools/farm/full_report.py <build dir> <report.md> [--commit C] [--make-rc N] [--seconds S] [--hide PATH...]

Reads <build dir>/<stage>/stage.json (tools/pipeline/build.py) and a few of the stages' own text outputs:
provenance, the stage table, compile and link counts, undefined names by class (trial link) and by category
(guest), the emulated boot (milestones, WinMain, the fault that stopped it, skipped initializers by fault), the
boot frontier's first 20 entries and the package check. The repository is public: the report holds numbers, unit
names and short diagnostics (symbol names, first errors) only, never prepared source, objects or address lists.
--hide replaces machine paths (FARM_HOME, the checkout) with a placeholder.
"""
import argparse
import json
import os
import re
import sys

STAGES = ['fetch', 'toolchain', 'prepare', 'census', 'compile', 'link', 'guest', 'boot', 'nine', 'host', 'package',
          'tests']


def rec(bd, s):
    try:
        with open(os.path.join(bd, s, 'stage.json')) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def fmt(v):
    if isinstance(v, int) and not isinstance(v, bool):
        return '{:,}'.format(v)
    return '-' if v is None else str(v)


def table(head, rows):
    out = ['| ' + ' | '.join(head) + ' |', '|' + '|'.join('---' for _ in head) + '|']
    for r in rows:
        out.append('| ' + ' | '.join(str(x).replace('|', '\\|').replace('\n', ' ') for x in r) + ' |')
    return out


def stub_boot(bd, clean):
    """Lines for S5's stub-fault boot (full_job.sh 4b: build/boot/stub/emu_stub.log): how far the boot gets once
    tests/s5/known_faults.txt runs past the known faults, which of them fired, where it stopped."""
    log = os.path.join(bd, 'boot', 'stub', 'emu_stub.log')
    if not os.path.isfile(log):
        return []
    lines = open(log, errors='replace').read().splitlines()
    winmain = any('fnvnx boot: calling WinMain' in l for l in lines)
    skips = next((m.group(1) for l in reversed(lines)
                  for m in [re.search(r'initializers skipped after a fault: (\d+)', l)] if m), None)
    stop = next((l for l in reversed(lines) if l.startswith('STOPPED')), None)
    try:   # tests/s5/farm_boot.sh --summary-json: game milestones, and the stop when the game exited by itself
        bs = json.load(open(os.path.join(bd, 'boot', 'stub', 'boot_summary_stub.json')))
    except (OSError, ValueError):
        bs = {}
    if stop is None:
        st = bs.get('stop') or {}
        stop = ' / '.join([st.get('reason') or 'no STOPPED line'] + (st.get('messages') or [])[:3])
    try:   # full_job.sh 4b: FNVNX_STUB_NULLDEV of this boot (S5 a3b368ed: CreateDevice on the null device)
        nd = open(os.path.join(bd, 'boot', 'stub', 'nulldev')).read().strip()
    except OSError:
        nd = ''
    ndl = {'1': '; null device: FNVNX_STUB_NULLDEV=1', '0': '; null device: off'}.get(nd, '')
    L = ['', 'Stub-fault boot (tests/s5/known_faults.txt, docs/TOOLCHAIN.md section 18%s):' % ndl, '',
         '- WinMain entered: %s; initializers skipped after a fault: %s' % ('yes' if winmain else 'NO', fmt(skips)),
         '- stop: %s' % clean(stop)]
    if bs.get('milestones'):
        L.append('- game milestones: %s; last reached: %s' % (
            ', '.join('%s %s' % (clean(m.get('name'), 30), 'yes' if m.get('reached') else 'no') for m in bs['milestones']),
            clean(bs.get('milestone') or 'none')))
    starts = [i for i, l in enumerate(lines) if l.startswith('== trace (')]
    if starts:
        L += ['', '```'] + [clean(l.strip(), 160) for l in lines[starts[-1]:starts[-1] + 12]
                             if l.startswith('== trace') or re.match(r'\s+(pc|lr|call|trap|#\d+)', l)] + ['```']
    rows = [l.split('\t') for l in lines if l.startswith(('stubbed-fault\t', 'replaced-call\t'))]
    if rows:
        L.append('')
        L += table(['stubbed', 'count', 'function', 'fault'],
                   [(r[0].split('-')[0], r[1], clean(r[2], 50), clean(r[3] if len(r) > 3 else '', 60)) for r in rows[:20]])
    hv = os.path.join(bd, 'boot', 'hand_vptrs.txt')   # tests/s5/hand_vptrs.py's summary: counts only
    if os.path.isfile(hv):
        L += ['', 'Hand-stored image vptrs (tests/s5/hand_vptrs.py; the list is boot/hand_vptrs.tsv of link/<commit12>):',
              '', '```'] + [clean(l.rstrip(), 160) for l in open(hv, errors='replace')][:12] + ['```']
    fr = os.path.join(bd, 'boot', 'stub', 'frontier_stub.txt')
    if os.path.isfile(fr):
        first = next((l for l in open(fr, errors='replace') if 'untranslated keys hit' in l), '')
        if first:
            L += ['', '- frontier of the stub-fault boot: %s' % clean(first.split(': ', 1)[-1].strip())]
    return L


TREE_STAGES = ['prepare', 'census', 'compile', 'link', 'guest', 'boot', 'nine', 'host', 'package', 'tests']


def provenance_failures(R, commit, since=None):
    """Why the build's records are not this commit's build (empty: PASS). A tree-derived record must be prepared from
    `commit` (prepared_from.port_commit), never another commit's (the 097cdd1504c8 report showed 06857911's tree,
    objects and link as "cached"), and when `since` (the job's start, build.py run_id format) is given, every record
    of a stage after fetch/toolchain must have been written or confirmed (cached_at) by a make run of this job."""
    out = []
    if not commit or commit == '?':
        return ['no --commit given']
    for s in TREE_STAGES:
        r = R.get(s) or {}
        if not r or r.get('status') in (None, 'disabled', 'skipped'):
            continue
        pc = (r.get('prepared_from') or {}).get('port_commit')
        if s == 'prepare' or r.get('prepared_from'):
            if not pc:
                out.append('%s: no provenance record' % s)
            elif not (pc.startswith(commit) or commit.startswith(pc)):
                out.append('%s: prepared from %s, not %s' % (s, pc[:12], commit[:12]))
        if r.get('stale'):
            out.append('%s: stale (%s)' % (s, r['stale']))
        when = max(str(r.get('run_id') or ''), str(r.get('cached_at') or ''))
        if since and when < since:
            out.append('%s: record of an earlier run (%s), not this job\'s' % (s, when or 'no run id'))
    return out


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('build')
    ap.add_argument('report')
    ap.add_argument('--commit', default='?')
    ap.add_argument('--make-rc', default='?')
    ap.add_argument('--seconds', type=float)
    ap.add_argument('--hide', nargs='*', default=[])
    ap.add_argument('--inputs', nargs='*', default=[], help='lines describing where the inputs came from')
    ap.add_argument('--since', help='the job\'s start (UTC, %%Y%%m%%d-%%H%%M%%S): records older than it are not this '
                    'build\'s')
    a = ap.parse_args(argv)
    bd = os.path.abspath(a.build)
    hides = sorted([(os.path.abspath(p), '$' + n) for n, p in (h.split('=', 1) for h in a.hide if '=' in h)],
                   key=lambda x: -len(x[0])) + [(bd, '$BUILD')]

    def clean(s, n=200):
        s = str(s or '')
        for p, name in hides:
            s = s.replace(p, name)
        return s[:n]

    R = {s: rec(bd, s) for s in STAGES}
    m = {}
    for s in STAGES:
        m.update(R[s].get('metrics', {}))
    L = ['# full build %s' % a.commit[:12], '']
    pf = R['prepare'].get('prepared_from') or {}
    L.append('- commit: `%s`; make exit %s%s' % (a.commit, a.make_rc,
                                                 '; %.0f min' % (a.seconds / 60) if a.seconds else ''))
    for line in a.inputs:
        L.append('- ' + clean(line, 300))
    L.append('- pins: ' + ', '.join('%s `%s`' % (k[4:], v) for k, v in sorted(R['fetch'].get('metrics', {}).items())
                                    if k.startswith('pin_')))
    t = R['toolchain']
    L.append('- toolchain: %s; units compiled with %s; cross tools: %s; Ogg Vorbis %s' % (
        clean(t.get('metrics', {}).get('tc_gcc')), clean(t.get('metrics', {}).get('game_cxx')), clean(t.get('cross')),
        'yes' if t.get('artifacts', {}).get('vorbis') else 'no'))
    L.append('')
    L.append('## Provenance')
    L.append('')
    L.append('- tree prepared from ports/fnv-nx `%s` (%s), decomp `%s`%s' % (
        pf.get('port_commit') or 'unknown', pf.get('mode') or '-', pf.get('decomp_commit') or 'unknown',
        '; uncommitted prepare inputs: %d' % len(pf['port_dirty']) if pf.get('port_dirty') else ''))
    stale = [s for s in STAGES if R[s].get('stale')]
    other = sorted({(R[s].get('prepared_from') or {}).get('port_commit') for s in STAGES
                    if R[s].get('prepared_from')} - {pf.get('port_commit')})
    L.append('- every tree-derived stage from that tree: %s' % ('yes' if not stale and not other else
                                                               'NO (stale: %s; other commits: %s)' % (
                                                                   ', '.join(stale) or '-', ', '.join(other) or '-')))
    pfail = provenance_failures(R, a.commit, a.since)
    L.append('- provenance: %s' % ('PASS (every tree-derived stage prepared from `%s`%s)' % (
        a.commit[:12], ', by this job' if a.since else '') if not pfail else
        'FAIL: ' + '; '.join(clean(x, 120) for x in pfail[:8])))
    L.append('- compiler of the game units: %s' % clean(t.get('metrics', {}).get('game_cxx') or 'unknown'))
    co_ad = R['compile'].get('adopted')
    L.append('- compile: %s' % ('objects adopted from %s' % clean(co_ad) if co_ad else
                                ('compiled by this build' if R['compile'].get('ran') else
                                 'cached from an earlier run of this build directory')))
    L.append('')
    L.append('## Stages')
    L.append('')
    rows = []
    for s in STAGES:
        r = R[s]
        st = r.get('status') or 'not run'
        rows.append((s, st, 'ran' if r.get('ran') else ('cached' if st in ('ok', 'warn') else '-'),
                     '%.0f s' % r['seconds'] if r.get('ran') and r.get('seconds') is not None else '',
                     clean(r.get('summary'), 160) + (' ERROR: ' + clean(r.get('error'), 200) if r.get('error') else '')))
    L += table(['stage', 'status', 'run', 'time', 'summary'], rows)
    L.append('')
    # compile
    co = R['compile']
    if co.get('metrics'):
        L.append('## Census and compile')
        L.append('')
        L.append('- census: %s units, %s clean, %s failing, %s errors' % (
            fmt(m.get('census_units')), fmt(m.get('census_clean')), fmt(m.get('census_failing')),
            fmt(m.get('census_errors'))))
        L.append('- compile: %s units, %s objects, %s failed' % (fmt(m.get('compile_units')), fmt(m.get('compile_ok')),
                                                               fmt(m.get('compile_failed'))))
        failed = os.path.join(bd, 'compile', 'failed.tsv')
        if os.path.isfile(failed):
            rows = [l.rstrip('\n').split('\t') for l in open(failed, errors='replace') if not l.startswith('#')]
            if rows:
                L.append('')
                L.append('Units without an object (errors, first error):')
                L.append('')
                for p in rows[:300]:
                    L.append('- `%s` %s: %s' % (p[0], p[1] if len(p) > 1 else '?', clean(p[2] if len(p) > 2 else '', 160)))
        L.append('')
    # link
    lk = R['link']
    if lk.get('metrics'):
        L.append('## Link')
        L.append('')
        L.append('- %s objects, %s references, %s by address (%s %%), %s bridges, ABI manual %s, x86-inconsistent %s' % (
            fmt(m.get('link_objects')), fmt(m.get('link_refs')), fmt(m.get('link_resolved')),
            fmt(m.get('link_resolved_pct')), fmt(m.get('link_bridges')), fmt(m.get('link_abi_manual')),
            fmt(m.get('link_abi_x86_inconsistent'))))
        L.append('- address map: ' + ' '.join('%s=%s' % (k[-1], fmt(v)) for k, v in sorted(m.items())
                                               if k.startswith('link_addrmap_')))
        L.append('- trial link: %s objects, %s undefined, %s multiple definitions; split-global check: %s objects / '
                 '%s relocations (want 0); extern-text addresses %s' % (
                     fmt(m.get('link_check_objects')), fmt(m.get('link_check_undefined')),
                     fmt(m.get('link_check_multidef')), fmt(m.get('link_bypassing_objects')),
                     fmt(m.get('link_bypassing_relocs')), fmt(m.get('link_extern_text_addrs'))))
        cc = lk.get('check_classes') or {}
        if cc:
            L.append('')
            L += table(['trial link: undefined names by class', 'names'],
                       [(k, fmt(v)) for k, v in sorted(cc.items(), key=lambda x: -x[1])])
        L.append('')
    gu = R['guest']
    if gu.get('metrics'):
        L.append('## Guest')
        L.append('')
        L.append('- %s; D3D9 %s, GAME_NEW %s, Ogg Vorbis %s; guest.nso %s bytes; PC-relative absolute references %s' % (
                     gu.get('method'), gu.get('d3d9', '-'), gu.get('game_new', '-'), 'yes' if gu.get('vorbis') else 'no',
                     fmt(m.get('guest_guest_nso_bytes')), fmt(m.get('guest_pcrel_abs_refs'))))
        if gu.get('layout'):
            L.append('- ' + clean(gu['layout']))
        if gu.get('undefined'):
            L.append('')
            L += table(['undefined symbols', 'category', 'owner'],
                       [(fmt(x['count']), x['key'], x['owner']) for x in gu['undefined']])
        L.append('')
    bo = R['boot']
    L.append('## Emulated boot')
    L.append('')
    if bo.get('status') in (None, 'disabled', 'skipped', 'never') or not bo.get('metrics'):
        L.append('- not run: %s' % clean(bo.get('summary') or bo.get('status') or 'no record'))
    else:
        L.append('- data image: %s' % clean(bo.get('image')))
        L.append('- WinMain entered: %s; initializers skipped after a fault: %s; trapped keys %s (%s hits)' % (
            'yes' if m.get('boot_winmain') else 'NO', fmt(m.get('boot_init_skips')), fmt(m.get('boot_trap_keys')),
            fmt(m.get('boot_trap_hits'))))
        L.append('- last milestones: ' + ' / '.join(clean(x, 100) for x in (bo.get('milestones') or [])[-3:]))
        if bo.get('game_milestones'):
            L.append('- game milestones (tests/s5/milestones.txt): %s; last reached: %s' % (
                ', '.join('%s %s' % (clean(n, 30), 'yes' if ok else 'no') for n, ok in bo['game_milestones']),
                clean(bo.get('milestone') or 'none')))
        L.append('- stop: %s' % clean(bo.get('stop')))
        if bo.get('fault'):
            L.append('')
            L.append('First fault:')
            L.append('')
            L.append('```')
            L += [clean(x, 160) for x in bo['fault']]
            L.append('```')
        if bo.get('skip_summary'):
            L.append('')
            L += table(['skipped', 'fault', 'faulting function', 'first initializer'],
                       [(x[0], clean(x[1], 60), clean(x[2], 90), clean(x[3], 70)) for x in bo['skip_summary'][:20]
                        if len(x) >= 4])
        fr = os.path.join(bd, 'boot', 'frontier.tsv')
        if os.path.isfile(fr):
            rows = [l.rstrip('\n').split('\t') for l in open(fr, errors='replace') if not l.startswith('#')]
            L.append('')
            L.append('Boot frontier: %s untranslated keys, %s hits, %s units; first 20 in boot order:' % (
                fmt(m.get('frontier_keys')), fmt(m.get('frontier_hits')), fmt(m.get('frontier_units'))))
            L.append('')
            L += table(['rank', 'hits', 'phase', 'key', 'why', 'units', 'link', 'census'],
                       [(p[0], p[2], p[3], clean(p[5], 60), clean(p[7], 50), clean(p[8], 70), clean(p[9], 20),
                         clean(p[10], 60)) for p in rows[:20] if len(p) > 10])
        L += stub_boot(bd, clean)
    L.append('')
    pk = R['package']
    L.append('## Package')
    L.append('')
    if pk.get('sd_files'):
        for n, sz in pk['sd_files']:
            L.append('- `%s` %s bytes' % (n, fmt(sz)))
        L.append('- exefs.nsp: ' + '; '.join(clean(x, 80) for x in (pk.get('pfs0') or [])[:3]))
        L.append('- guest.nso in the SD tree: %s' % ('yes' if any(n.endswith('guest.nso') for n, _ in pk['sd_files'])
                                                   else 'NO'))
        L.append('- fnv.nro in the SD tree: %s' % ('yes' if any(n.endswith('fnv.nro') for n, _ in pk['sd_files']) else 'NO'))
    if pk.get('check_package_rc') is not None:
        L.append('- check_package: exit %d' % pk['check_package_rc'])
    if pk.get('metrics', {}).get('ilp32_wrap_64bit') is not None:
        L.append('- toolchain ilp32_wrap (tests/s13/ilp32_wrap, fnvnx-a64, build flags): %s constant-base address '
                 'forms do not wrap at 2^32' % fmt(pk['metrics']['ilp32_wrap_64bit']))
    if pk.get('nx1'):
        L.append('- nx1: open device-readiness checks = %s of %d (tests/nx1/run.py)' % (
            fmt(pk.get('metrics', {}).get('nx1_open')), len(pk['nx1'])))
        L.append('')
        L += table(['check', 'name', 'status', 'note'], [(n, c, st, clean(note, 110)) for n, c, st, note in pk['nx1']])
    if not pk.get('sd_files') and not pk.get('nx1'):
        L.append('- %s' % clean(pk.get('summary') or pk.get('error') or pk.get('status') or 'not run'))
    L.append('')
    te = R['tests']
    if te.get('suites'):
        L.append('## Tests')
        L.append('')
        L += table(['suite', 'result', 'summary'], [(k, v.get('status'), clean(v.get('summary'), 140))
                                                    for k, v in sorted(te['suites'].items())])
        L.append('')
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, 'w') as f:
        f.write('\n'.join(L))
    print(a.report)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
