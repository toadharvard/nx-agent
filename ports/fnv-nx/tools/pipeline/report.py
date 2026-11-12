#!/usr/bin/env python3
"""report.py - build/report.md + build/report.json from the stage records (called by build.py after every run).

    tools/pipeline/report.py [build dir]      re-render the report from the recorded stage.json files

Every run also stores its snapshot as build/history/<run id>.json; the report diffs against the newest older one.
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))

OWNER = {'fetch': 'S9-build (environment)', 'toolchain': 'S5-toolchain', 'prepare': 'S1-compile',
         'census': 'S1-compile', 'compile': 'S1-compile', 'link': 'S2-link', 'guest': 'S5-toolchain (link); S2/S4 as noted',
         'boot': 'S5-toolchain (emulated boot); S1 (frontier units)',
         'nine': 'S6c-nine', 'host': 'S5-toolchain', 'package': 'S5-toolchain', 'tests': 'per suite'}
SUITE_OWNER = {'s2-link': 'S2-link', 's3': 'S3-dataimage', 's4': 'S4-runtime', 's5-tools': 'S5-toolchain',
               's5-emu-hello': 'S5-toolchain', 's5-rt': 'S5-toolchain', 's6b': 'S6b-shaders', 's7': 'S7-audio-input',
               's8': 'S8-asm', 's9': 'S9-build'}


def human(n):
    if n is None:
        return '-'
    n = float(n)
    for unit in ('B', 'KiB', 'MiB', 'GiB'):
        if n < 1024 or unit == 'GiB':
            return ('%d B' % n) if unit == 'B' else ('%.1f %s' % (n, unit))
        n /= 1024.0


def fmt(v):
    if isinstance(v, int):
        return '{:,}'.format(v)
    if isinstance(v, float):
        return '%g' % v
    return '-' if v is None else str(v)


def git_info():
    def g(*a):
        try:
            p = subprocess.run(['git', '-C', ROOT] + list(a), capture_output=True, text=True, timeout=20)
            return p.stdout.strip() if p.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None
    head = g('rev-parse', '--short=10', 'HEAD')
    dirty = g('status', '--porcelain', '--', '.')
    return {'commit': head, 'dirty': bool(dirty), 'branch': g('rev-parse', '--abbrev-ref', 'HEAD')}


def snapshot(b, stages):
    snap = {'run_id': b.run_id, 'date': time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime()), 'git': git_info(),
            'config': {k: b.cfg.get(k, '') for k in (
                'NINE', 'PROGRAM_ID', 'GAME_CXX', 'CROSS_BIN', 'OPT', 'JOBS', 'EXT', 'FNVNX_TC', 'DEVKITPRO',
                'GUEST_LINK', 'GAME_NEW', 'CENSUS', 'TESTS', 'OVERRIDE', 'EXTERN_TEXT', 'MSVC_OBJS', 'UNITS_RENAMES',
                'LINK_SEED', 'UNITS', 'PREPARED', 'FORCE_STALE', 'BOOT', 'BOOT_IMAGE', 'BOOT_CENSUS', 'CROSS', 'TC_DL',
                'VORBIS', 'PRISTINE_OBJS')},
            'head': b.head, 'prepared_from': b.rec('prepare').get('prepared_from'),
            'stages': {}, 'metrics': {}, 'tests': {}}
    for s in stages:
        r = b.rec(s.name)
        pf = r.get('prepared_from')
        snap['stages'][s.name] = {'status': r.get('status'), 'ran': r.get('ran'), 'seconds': r.get('seconds'),
                                  'summary': r.get('summary'), 'run_id': r.get('run_id'),
                                  'prepared_from': pf.get('port_commit') if pf else None, 'stale': r.get('stale')}
        for k, v in r.get('metrics', {}).items():
            snap['metrics'][k] = v
    for k, v in b.rec('tests').get('suites', {}).items():
        snap['tests'][k] = v.get('status')
    return snap


def previous(hist, run_id):
    try:
        names = sorted(n for n in os.listdir(hist) if n.endswith('.json') and n[:-5] < run_id)
    except OSError:
        return None
    for n in reversed(names):
        try:
            return json.load(open(os.path.join(hist, n)))
        except (OSError, ValueError):
            continue
    return None


def diff(prev, cur):
    rows = []
    if not prev:
        return rows
    for k in sorted(set(prev['metrics']) | set(cur['metrics'])):
        a, c = prev['metrics'].get(k), cur['metrics'].get(k)
        if a == c:
            continue
        if isinstance(a, (int, float)) and isinstance(c, (int, float)) and not isinstance(a, bool):
            d = c - a
            rows.append((k, fmt(a), fmt(c), ('+' if d > 0 else '') + fmt(round(d, 1) if isinstance(d, float) else d)))
        else:
            rows.append((k, fmt(a), fmt(c), ''))
    for s in sorted(set(prev['stages']) | set(cur['stages'])):
        a = (prev['stages'].get(s) or {}).get('status')
        c = (cur['stages'].get(s) or {}).get('status')
        if a != c:
            rows.append(('stage ' + s, fmt(a), fmt(c), ''))
    for s in sorted(set(prev.get('tests', {})) | set(cur.get('tests', {}))):
        a, c = prev.get('tests', {}).get(s), cur.get('tests', {}).get(s)
        if a != c:
            rows.append(('test ' + s, fmt(a), fmt(c), ''))
    return rows


def table(head, rows, align=None):
    out = ['| ' + ' | '.join(head) + ' |', '|' + '|'.join(align or ['---'] * len(head)) + '|']
    for r in rows:
        out.append('| ' + ' | '.join(str(x).replace('|', '\\|').replace('\n', ' ') for x in r) + ' |')
    return out


def clean(s):
    return (s or '-').replace(ROOT + '/', '')[:200]


def rel(p):
    if not p:
        return '-'
    p = os.path.abspath(p)
    return os.path.relpath(p, ROOT) if p.startswith(ROOT + os.sep) else p


def render(b, stages, snap, prev):
    R = b.rec
    m = snap['metrics']
    L = ['# fnv-nx build report', '']
    g = snap['git']
    L.append('Run `%s`, %s. Port tree `%s`%s (%s). Generated by `tools/pipeline/build.py`; everything under `build/` '
             'is derived from the decomp: local only, never committed.' % (
                 snap['run_id'], snap['date'], g.get('commit') or '?', ' + local changes' if g.get('dirty') else '',
                 g.get('branch') or '?'))
    L.append('')
    c = snap['config']
    L.append('Configuration: `NINE=%s PROGRAM_ID=%s GAME_CXX=%s OPT=%s JOBS=%s GUEST_LINK=%s GAME_NEW=%s`; EXT `%s`; '
             'FNVNX_TC `%s`; DEVKITPRO `%s`%s.' % (
                 c['NINE'], c['PROGRAM_ID'], c['GAME_CXX'], c['OPT'], c['JOBS'], c['GUEST_LINK'], c['GAME_NEW'],
                 c['EXT'] or 'ext/ (fetch.sh)', R('toolchain').get('artifacts', {}).get('prefix') or '-',
                 R('toolchain').get('artifacts', {}).get('devkitpro') or '-',
                 ''.join('; %s `%s`' % (k, c[k]) for k in ('CROSS', 'CROSS_BIN', 'TC_DL', 'VORBIS', 'UNITS', 'PREPARED',
                                                           'EXTERN_TEXT', 'MSVC_OBJS', 'UNITS_RENAMES', 'LINK_SEED',
                                                           'OVERRIDE', 'BOOT_IMAGE', 'PRISTINE_OBJS') if c.get(k))
                 + ('; **FORCE_STALE=1**' if c.get('FORCE_STALE') == '1' else '')))
    L.append('')
    # ---- provenance (J-0054)
    pf = snap.get('prepared_from') or {}
    head = snap.get('head')
    stale = [(n, v.get('stale'), v.get('status')) for n, v in snap['stages'].items() if v.get('stale')]
    L.append('## Provenance')
    L.append('')
    if pf:
        L.append('Head `%s`. Prepared tree (`%s`, %s): ports/fnv-nx `%s`, decomp `%s`%s. %s' % (
            head or 'unknown', rel(pf.get('tree')), pf.get('mode'), pf.get('port_commit') or 'unknown',
            pf.get('decomp_commit') or 'unknown',
            ' + %d uncommitted changes to prepare inputs' % len(pf['port_dirty']) if pf.get('port_dirty') else '',
            'Every tree-derived stage below was made from it.' if pf.get('port_commit') == head and not stale else
            '**The tree is not from the head: stages refuse it unless FORCE_STALE=1.**'
            if pf.get('port_commit') != head else ''))
    else:
        L.append('Head `%s`; no prepared tree recorded yet.' % (head or 'unknown'))
    if stale:
        L.append('')
    for n, why, st in stale:
        L.append('* `%s` %s: %s' % (n, 'refused its inputs' if st == 'fail' else 'consumed stale inputs (FORCE_STALE=1)',
                                    why))
    L.append('')
    # ---- stages
    L.append('## Stages')
    L.append('')
    rows = []
    for s in stages:
        r = R(s.name)
        st = r.get('status')
        st = 'not run' if st in (None, 'never') else st
        how ='ran' if r.get('ran') else ('cached' if st in ('ok', 'warn') else '-')
        pfs = r.get('prepared_from')
        prov = ('`%s`%s' % ((pfs.get('port_commit') or 'unknown')[:10], ' (stale)' if r.get('stale') else '')
                if pfs else '')
        rows.append(('`%s`' % s.name, '**%s**' % st if st == 'fail' else st, how,
                     ('%.0f s' % r['seconds']) if r.get('seconds') is not None and r.get('ran') else '', prov,
                     (r.get('summary') or '') + (' **error:** ' + r['error'] if r.get('error') and st == 'fail' else '')))
    L += table(['stage', 'status', 'run', 'time', 'prepared from', 'summary'], rows,
               ['---', '---', '---', '--:', '---', '---'])
    L.append('')
    # ---- inputs
    f = R('fetch')
    if f.get('metrics'):
        L.append('Pins (tools/fetch.sh, checked from the checkouts): ' + ', '.join(
            '%s `%s`' % (k[4:], v) for k, v in sorted(f['metrics'].items()) if k.startswith('pin_')) +
            ('; other checkouts in EXT: ' + ', '.join('%s `%s`' % kv for kv in sorted(f.get('optional', {}).items()))
             if f.get('optional') else '') + '.')
        L.append('')
    t = R('toolchain')
    if t.get('metrics'):
        L.append('Toolchain: %s; game units compiled with %s (cross tools: %s); Ogg Vorbis %s.%s' % (
            t['metrics'].get('tc_gcc'), t['metrics'].get('game_cxx'), t.get('cross', '-'),
            rel(t.get('artifacts', {}).get('vorbis')) if t.get('artifacts', {}).get('vorbis') else 'none',
            (' Notes: ' + ' '.join(t.get('notes', []))) if t.get('notes') else ''))
        L.append('')
    # ---- census / compile
    p, ce, co = R('prepare'), R('census'), R('compile')
    L.append('## Prepare, census, compile')
    L.append('')
    if p.get('metrics'):
        L.append('Prepare: %s rules, %s edits over %s units (`build/prepare/prepare_stats.tsv`); %s.' % (
            fmt(m.get('prepare_rules')), fmt(m.get('prepare_edits')), fmt(m.get('prepare_units')),
            'tree adopted from `%s` (PREPARED)' % rel(p['artifacts'].get('tree'))
            if (p.get('prepared_from') or {}).get('mode') == 'adopted' else
            '%s files of the prepared tree changed since the previous prepare' % fmt(m.get('prepare_files_changed'))))
        L.append('')
    rows = []
    if ce.get('metrics'):
        rows.append(('census (`-fsyntax-only`)', fmt(m.get('census_units')), fmt(m.get('census_clean')),
                     fmt(m.get('census_failing')), fmt(m.get('census_errors')) + ' errors'))
    if co.get('metrics'):
        rows.append(('compile (`-c %s`)' % c['OPT'], fmt(m.get('compile_units')), fmt(m.get('compile_ok')),
                     fmt(m.get('compile_failed')), 'built now %s, reused %s, objects %s MiB' % (
                         fmt(m.get('compile_built_now')), fmt(m.get('compile_reused')), fmt(m.get('compile_obj_mib')))))
    if rows:
        L += table(['', 'units', 'clean / object', 'failing', ''], rows, ['---', '--:', '--:', '--:', '---'])
        L.append('')
    if co.get('flags'):
        L.append('Compile flags (`tools/link/compile.sh`, part of the cache key): `%s`' % co['flags'])
        L.append('')
    if ce.get('top_errors'):
        L.append('Top census error kinds (`build/census/summary.txt`):')
        L.append('')
        L.append('```')
        L += ce['top_errors'][:10]
        L.append('```')
        L.append('')
    if co.get('failed_by_dir'):
        L.append('Units without an object, by directory (full list with the first error: `build/compile/failed.tsv`; '
                 'they are excluded from the link): ' + ', '.join('`%s` %d' % tuple(kv) for kv in co['failed_by_dir'][:12])
                 + '.')
        L.append('')
    # ---- link
    lk = R('link')
    L.append('## Link by address (S2 resolve / probe / apply / check)')
    L.append('')
    if lk.get('metrics'):
        L.append('%s objects, %s undefined (object, symbol) references, %s resolved by address (%s %%); %s bridges, '
                 'ABI manual %s, x86-inconsistent %s; %s layouts. Address map: %s. Trial link (`fnvlink check`): %s '
                 'objects, **%s names undefined**, %s multiple definitions.' % (
                     fmt(m.get('link_objects')), fmt(m.get('link_refs')), fmt(m.get('link_resolved')),
                     fmt(m.get('link_resolved_pct')), fmt(m.get('link_bridges')), fmt(m.get('link_abi_manual')),
                     fmt(m.get('link_abi_x86_inconsistent')), fmt(m.get('link_layouts')),
                     ' '.join('%s=%s' % (k[-1], fmt(v)) for k, v in sorted(m.items())
                              if k.startswith('link_addrmap_')),
                     fmt(m.get('link_check_objects')), fmt(m.get('link_check_undefined')),
                     fmt(m.get('link_check_multidef'))))
        L.append('')
        cc = lk.get('check_classes') or {}
        rc = lk.get('ref_classes') or {}
        if cc:
            L += table(['trial link: undefined names by class', 'names'], sorted(cc.items(), key=lambda x: -x[1]),
                       ['---', '--:'])
            L.append('')
        if rc:
            L.append('Reference classes (`build/link/out/stats.txt`): ' + ', '.join(
                '%s %s' % (k, fmt(v)) for k, v in sorted(rc.items(), key=lambda x: -x[1])) + '.')
            L.append('')
        L.append('resolve options: %s; S13 split-global check (`build/link/check_rebind.txt`): %s objects / %s '
                 'relocations bypass the rebinding (want 0); probe %s s (layouts %s s, vtables %s s, %s jobs). Run summary: '
                 '`build/link/summary.txt` (first line: the commit the tree was prepared from).' % (
                     ' '.join('`%s`' % a for a in lk.get('resolve_options') or []) or 'none',
                     fmt(m.get('link_bypassing_objects')), fmt(m.get('link_bypassing_relocs')),
                     fmt(m.get('link_probe_s')), fmt(m.get('link_probe_layouts_s')), fmt(m.get('link_probe_vtables_s')),
                     fmt(m.get('link_probe_jobs'))))
        L.append('')
        if lk.get('apply_failures'):
            L.append('apply could not rewrite %d objects (left out): %s' % (
                m.get('link_apply_failures', 0), '; '.join(lk['apply_failures'][:5])))
            L.append('')
    # ---- guest
    gu = R('guest')
    L.append('## Guest link (ILP32 game module)')
    L.append('')
    ad = gu.get('adapter') or {}
    if gu.get('method'):
        if not ad:
            L.append('Method: %s.%s' % (gu['method'], (' ' + gu['layout'] + '.') if gu.get('layout') else ''))
            L.append('')
    if ad:
        L.append('Method: %s. %s game objects, %s platform/loader objects, %s link passes, gc-sections %s, '
                 'D3D9 %s.' % (gu['method'], fmt(ad.get('game_objects')), fmt(ad.get('platform_units')),
                               fmt(ad.get('link_passes')), 'on' if ad.get('gc_sections') else 'off',
                               ad.get('d3d9', c['NINE'] == '1' and 'external' or 'null')))
        L.append('')
        if ad.get('platform_failed'):
            L.append('Platform / loader / runtime sources that do not compile for the guest (excluded):')
            L.append('')
            L += table(['source', 'first error'], ad['platform_failed'])
            L.append('')
        if ad.get('link_excluded'):
            L.append('Game objects the linker refuses as input, left out of the guest (the trial link with the '
                     'host binutils accepted them):')
            L.append('')
            L += table(['object', 'reason'], [('`%s`' % o, w) for o, w in ad['link_excluded']])
            L.append('')
        if ad.get('multidef_count'):
            L.append('%d symbols defined more than once (linked with --allow-multiple-definition): %s' % (
                ad['multidef_count'], ', '.join('`%s`' % n for n in ad.get('multidef', [])[:8])))
            L.append('')
    if gu.get('metrics'):
        L.append('D3D9 `%s`, GAME_NEW `%s`; PC-relative references to absolute image symbols %s (want 0: relocatable).' % (
                     gu.get('d3d9', '-'), gu.get('game_new', '-'), fmt(m.get('guest_pcrel_abs_refs'))))
        L.append('')
    if gu.get('undefined'):
        L.append('**Undefined symbols after the full link: %s** (each bound to a trap stub; per symbol: '
                 '`build/guest/undefined_by_category.tsv`):' % fmt(m.get('guest_undefined')))
        L.append('')
        L += table(['symbols', 'category', 'owner', 'meaning', 'example'],
                   [(fmt(x['count']), '`%s`' % x['key'], x['owner'], x['label'],
                     '`%s`' % x['examples'][0][:60] if x.get('examples') else '') for x in gu['undefined']],
                   ['--:', '---', '---', '---', '---'])
        L.append('')
    # ---- boot
    bo = R('boot')
    if bo.get('status') not in (None, 'never', 'disabled'):
        L.append('## Emulated boot and boot frontier (S5)')
        L.append('')
        L.append('Data image: %s.' % bo.get('image', '-'))
        L.append('')
        L.append('%s. Stop: `%s`. Trapped keys %s (%s hits), initializers skipped after a fault %s. Frontier '
                 '(`build/boot/frontier.txt`, `.tsv`, `_units.tsv`): %s untranslated keys, %s hits, %s units%s.' % (
                     bo.get('summary') or bo.get('error'), clean(bo.get('stop')), fmt(m.get('boot_trap_keys')),
                     fmt(m.get('boot_trap_hits')), fmt(m.get('boot_init_skips')), fmt(m.get('frontier_keys')),
                     fmt(m.get('frontier_hits')), fmt(m.get('frontier_units')),
                     '; compile status now from `%s`' % rel(bo['frontier_census']) if bo.get('frontier_census') else ''))
        L.append('')
        if bo.get('milestones'):
            L.append('```')
            L += bo['milestones'][:20]
            L.append('```')
            L.append('')
    # ---- host / nine / package
    ho, ni, pk = R('host'), R('nine'), R('package')
    L.append('## Host module, Nine, package')
    L.append('')
    if ho.get('method'):
        L.append('Host: %s.' % ho['method'])
        L.append('')
    if ni.get('status') not in (None, 'disabled', 'never'):
        L.append('Nine: %s' % (ni.get('summary') or ni.get('error')))
        L.append('')
    if pk.get('sd_files'):
        L.append('SD tree `build/package/sd/` (copy to the card root):')
        L.append('')
        L += table(['file', 'bytes'], [('`%s`' % n, fmt(s)) for n, s in pk['sd_files']], ['---', '--:'])
        L.append('')
    for n in pk.get('notes', []):
        L.append('* ' + n)
    if pk.get('notes'):
        L.append('')
    # ---- tests
    te = R('tests')
    L.append('## Host gates (tests)')
    L.append('')
    if te.get('suites'):
        rows = []
        for k, v in te['suites'].items():
            rows.append(('`%s`%s' % (k, ' (info)' if v.get('informational') else ''),
                         '**%s**' % v['status'] if v['status'] == 'fail' else v['status'],
                         'not selected (last result)' if v.get('not_selected') else
                         'cached' if v.get('cached') else '%s s' % fmt(v.get('seconds')),
                         SUITE_OWNER.get(k, '?'), v.get('summary', ''), rel(v.get('log'))))
        L += table(['suite', 'result', 'time', 'owner', 'summary', 'log'], rows)
        L.append('')
    # ---- artifacts
    arts = []
    for stage, name in (('guest', 'fnvnx.elf'), ('guest', 'guest.nso'), ('host', 'host.elf'), ('host', 'main'),
                        ('host', 'main.npdm'), ('package', 'exefs.nsp')):
        pth = R(stage).get('artifacts', {}).get(name)
        if pth and os.path.isfile(pth):
            arts.append(('`%s`' % rel(pth), fmt(os.path.getsize(pth)), human(os.path.getsize(pth))))
    if arts:
        L.append('## Artifacts')
        L.append('')
        L += table(['file', 'bytes', ''], arts, ['---', '--:', '--:'])
        L.append('')
    # ---- disk
    rows = []
    for s_ in stages:
        n = R(s_.name).get('disk_bytes')
        if n:
            rows.append(('`build/%s`' % s_.name, human(n)))
    if rows:
        st = os.statvfs(b.out)
        L.append('## Disk')
        L.append('')
        L.append('Free on the build disk: %s. Per stage (measured when the stage last ran; `make clean-work` removes '
                 're-creatable work trees, `CLEAN_WORK=1` does it after each success):' % human(st.f_bavail * st.f_frsize))
        L.append('')
        L += table(['directory', 'size'], rows, ['---', '--:'])
        L.append('')
    # ---- diff
    L.append('## Changes since the previous report')
    L.append('')
    if not prev:
        L.append('No previous report (first run).')
    else:
        rows = diff(prev, snap)
        L.append('Previous: run `%s` (%s, port tree `%s`).' % (prev['run_id'], prev.get('date'),
                                                            (prev.get('git') or {}).get('commit')))
        L.append('')
        if rows:
            L += table(['metric / state', 'previous', 'now', 'delta'], rows, ['---', '--:', '--:', '--:'])
        else:
            L.append('No metric or status changed.')
    L.append('')
    # ---- failures
    bad = []
    for s in stages:
        r = R(s.name)
        if r.get('status') == 'fail':
            bad.append(('`%s`' % s.name, 'fail', OWNER.get(s.name, '?'), r.get('error', '')))
        elif r.get('status') == 'warn':
            bad.append(('`%s`' % s.name, 'warn', OWNER.get(s.name, '?'), '; '.join(r.get('notes', [])) or
                        r.get('summary', '')))
        elif r.get('status') == 'skipped':
            bad.append(('`%s`' % s.name, 'skipped', OWNER.get(s.name, '?'), r.get('summary', '')))
    for k, v in (te.get('suites') or {}).items():
        if v['status'] in ('fail', 'skip'):
            bad.append(('test `%s`' % k, v['status'], SUITE_OWNER.get(k, '?'),
                        (v.get('summary') or '') + (' (log `%s`)' % rel(v.get('log')) if v.get('log') else '')))
    L.append('## Open items by owner')
    L.append('')
    if bad:
        L += table(['where', 'state', 'owner', 'what'], bad)
    else:
        L.append('Nothing failed.')
    L.append('')
    return '\n'.join(L)


def write(b, stages):
    hist = os.path.join(b.out, 'history')
    os.makedirs(hist, exist_ok=True)
    snap = snapshot(b, stages)
    prev = previous(hist, b.run_id)
    md = render(b, stages, snap, prev)
    with open(os.path.join(hist, b.run_id + '.json'), 'w') as f:
        json.dump(snap, f, indent=1, sort_keys=True)
    keep = max(2, int(b.cfg.get('KEEP_HISTORY', 50)))
    for old in sorted(n for n in os.listdir(hist) if n.endswith('.json'))[:-keep]:
        os.remove(os.path.join(hist, old))
    with open(os.path.join(b.out, 'report.json'), 'w') as f:
        json.dump(snap, f, indent=1, sort_keys=True)
    path = os.path.join(b.out, 'report.md')
    with open(path, 'w') as f:
        f.write(md)
    return path


if __name__ == '__main__':
    sys.path.insert(0, HERE)
    import build  # noqa: E402
    cfg, _ = build.parse_args(['BUILD_DIR=' + (sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'build'))])
    bb = build.Build(cfg)
    try:    # re-render the last run (same snapshot id), do not start a new history entry
        bb.run_id = json.load(open(os.path.join(bb.out, 'report.json')))['run_id']
    except (OSError, ValueError, KeyError):
        pass
    print(write(bb, build.STAGES))
