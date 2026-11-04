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


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('build')
    ap.add_argument('report')
    ap.add_argument('--commit', default='?')
    ap.add_argument('--make-rc', default='?')
    ap.add_argument('--seconds', type=float)
    ap.add_argument('--hide', nargs='*', default=[])
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
        L.append('- %s; D3D9 %s, GAME_NEW %s, Ogg Vorbis %s; guest.nso %s bytes; PC-relative absolute references %s; '
                 'initializers with a trap %s of %s, finalizers %s of %s' % (
                     gu.get('method'), gu.get('d3d9', '-'), gu.get('game_new', '-'), 'yes' if gu.get('vorbis') else 'no',
                     fmt(m.get('guest_guest_nso_bytes')), fmt(m.get('guest_pcrel_abs_refs')),
                     fmt(m.get('guest_dyninit_brk')), fmt(m.get('guest_dyninit_functions')),
                     fmt(m.get('guest_dynexit_brk')), fmt(m.get('guest_dynexit_functions'))))
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
    else:
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
