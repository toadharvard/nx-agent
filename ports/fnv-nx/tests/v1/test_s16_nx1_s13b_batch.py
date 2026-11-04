#!/usr/bin/env python3
"""test_s16_nx1_s13b_batch.py - V1 (D14): adversarial checks of the S16 / NX1 / S13b claims merged on 2026-11-04
(1cb88ec, 5a9828d, b663ae8, ef9bb69, 86e77a6, 716b6b0).

    python3 -I tests/v1/test_s16_nx1_s13b_batch.py     -> one line per case, exit 1 when a case is REFUTED

No Wine, no game data: compare_job.sh's functions are cut out of the script and run under bash with a made-up
artifacts repository; permcover.py runs beside stub perm.py / replay.py; check_platform.py runs on a temp copy of
toolchain/ with one define or NPDM field changed; causes.py and fuzzcache.py run on synthetic shard caches.

Cases expected to hold (DEFECT = the claim's behaviour does not hold):
  LK-1   1cb88ec  prefix_lock: a second process waits while the first holds the lock; a holder killed by SIGKILL
                  leaves no stale lock; a symlinked prefix shares the lock; a child the holder spawned (wineserver
                  daemonising) does not keep it after the holder exits; d9cap's class lock nests no replay.py ref
  PB-1   ef9bb69  perm.vs_job: a CTAB bone palette (Bones, 3 rows per bone) gets identity rows with |t| <= 0.1;
                  the seed is int(hash) + salt * K (salt 0 = the old seed); "_unreached" in --salts is harmless
  PC-1   ef9bb69  permcover.py keeps the first salt whose reference frame draws and lists the rest as _unreached
  SK-1   b663ae8  the salts key changes with the package, perm.py and permcover.py
  VM-1   86e77a6  check s4.vm_blocks passes 3072 extents and refuses 8192 (decimal)
  RH-1   716b6b0  a row fuzzed at head H1 keeps fuzz/<H1>/7of26 through a merge at a newer head H2 (other
                  function re-fuzzed), and that is where the H1 run published its div file
Refutation cases (REFUTED = reproduces as wrong):
  GC-1   5a9828d  a later run without gamedata/captures reports the "game" set of an earlier run: $W/run persists
                  (FARM_HOME cache) and only run_set clears its own set dir, so write_report reads a stale
                  run/game/gate/capgate.json: the gate's game metric is not this run's
  GC-2   5a9828d  without gamedata/captures (or without ARTIFACTS) the report has no game entry and no note saying
                  the real-game set was not measured: the gate passes on synthetic + perm alone, silently
  GC-3   5a9828d  two captures with the same file name in different directories (menu/f.nxd9, cell/f.nxd9) are
                  linked into one flat caps/game: one silently replaces the other (2 captures -> 1 measured)
  SK-2   b663ae8  permcover exits 0 when every reference replay fails (a broken Wine prefix, a timeout): every
                  program is "_unreached" and compare_job caches that file under the package key for good (no
                  retry until perm.py or the package changes)
  SK-3   b663ae8  the salts key omits the shader tools (s6b_asm, sdp2dksh: render/shaders' pins and patches) and
                  nxd9play.exe, which decide whether a seed's reference frame draws: new tools reuse old seeds
  VM-2   86e77a6  `#define FNVNX_VM_EXTENTS 0x2000` (8192) passes s4.vm_blocks: (\\d+) reads "0", worst = 4000
  VM-3   86e77a6  an NPDM with system_resource_size != 0 and address_space_type 32bit_no_reserved passes with
                  8192 extents, although (the commit's own finding) ldr gives a 32-bit space no system resource
  RH-2   716b6b0  a row seeded into 7of26 from another shard count's cache (fuzzcache seed, 3of13) keeps its
                  commit H1; --ref-by-head names fuzz/<H1>/7of26, but H1 published that div on fuzz/<H1>/3of13
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))
CMP = os.path.join(PORT, 'tools', 'compare')
JOB = os.path.join(PORT, 'tools', 'farm', 'compare_job.sh')
BAD = 0


def case(cid, ok, why, refutation=False):
    global BAD
    if ok:
        print('%s ok' % cid)
    else:
        print('%s %s: %s' % (cid, 'REFUTED' if refutation else 'DEFECT', why))
        BAD += 1


# ---- compare_job.sh pieces -------------------------------------------------------------------------------------------
def job_func(name):
    lines = open(JOB).read().split('\n')
    for i, l in enumerate(lines):
        if l.startswith(name + '() {'):
            if l.rstrip().endswith('}') and l.count('{') == l.count('}'):
                return l
            for j in range(i + 1, len(lines)):
                if lines[j] == '}':
                    return '\n'.join(lines[i:j + 1])
    raise SystemExit('compare_job.sh: no function %s' % name)


def job_tail():
    """the script's lines from fetch_captures to write_report (the game-set block)"""
    lines = open(JOB).read().split('\n')
    a = lines.index('fetch_captures')
    b = lines.index('write_report', a)
    return '\n'.join(lines[a:b + 1])


def run_job_snippet(t, body, env):
    pre = ['set -uo pipefail', 'NOTES=()', 'T0=$(date +%s)', 'CMP=%s' % CMP]
    pre += [job_func(n) for n in ('log', 'deadline_ok', 'key_of', 'fail', 'fetch_captures', 'glsl_dir', 'write_report')]
    p = os.path.join(t, 'snip.sh')
    open(p, 'w').write('\n'.join(pre) + '\n' + body + '\n')
    e = dict(os.environ, **env)
    return subprocess.run(['bash', p], capture_output=True, text=True, env=e, timeout=120)


def game_cases(t):
    # GC-1 / GC-2: no gamedata/captures (ARTIFACTS unset), $W/run holds an earlier run's game gate
    for cid in ('GC-2', 'GC-1'):
        w = os.path.join(t, cid, 'compare')
        os.makedirs(os.path.join(w, 'run'))
        if cid == 'GC-1':
            g = os.path.join(w, 'run', 'game', 'gate')
            os.makedirs(g)
            json.dump({'status': 'pass', 'summary': {'captures': 2, 'metric': {
                'frames_over': 0, 'frames': 40, 'mean_abs': 0.1, 'max_abs': 1, 'floor_mean_abs': 0.1,
                'floor_max_abs': 1, 'captures_incomplete': 0}}}, open(os.path.join(g, 'capgate.json'), 'w'))
        rep = os.path.join(t, cid, 'report.md')
        r = run_job_snippet(os.path.join(t, cid), job_tail(), {
            'W': w, 'REPORT': rep, 'JSON': rep[:-3] + '.json', 'COMMIT': 'f' * 40, 'SDP': os.path.join(t, 'x.sdp'),
            'ARTIFACTS': ''})
        if r.returncode:
            case(cid, False, 'snippet failed: ' + r.stderr[-300:])
            continue
        out = json.load(open(rep[:-3] + '.json'))
        if cid == 'GC-1':
            case(cid, 'game' not in out['sets'], 'no captures this run, yet the report carries a "game" set %s from '
                 'the stale $W/run/game' % out['sets'].get('game'), True)
        else:
            said = [n for n in out['notes'] if 'game' in n.lower() or 'captures' in n.lower()]
            case(cid, 'game' in out['sets'] or said, 'no game set and no note (%s): the report does not say the real-'
                 'game frames were not measured' % out['notes'], True)

    # GC-3: two captures with the same basename in different directories of gamedata/captures
    base = os.path.join(t, 'gc3')
    origin, work, art = (os.path.join(base, n) for n in ('origin.git', 'work', 'art'))
    git = lambda *a, **k: subprocess.run(['git', *a], check=True, capture_output=True, **k)
    git('init', '-q', '--bare', origin)
    git('init', '-q', work)
    for d, c in (('menu', b'NXD9menu'), ('cell', b'NXD9cell')):
        os.makedirs(os.path.join(work, d))
        open(os.path.join(work, d, 'f0001.nxd9'), 'wb').write(c)
    git('-C', work, 'add', '-A')
    git('-C', work, '-c', 'user.name=t', '-c', 'user.email=t@invalid', 'commit', '-qm', 'caps')
    git('-C', work, 'push', '-q', origin, 'HEAD:refs/heads/gamedata/captures')
    git('clone', '-q', origin, art)
    w = os.path.join(base, 'compare')
    os.makedirs(w)
    r = run_job_snippet(base, 'fetch_captures; echo "GAMECAPS=$GAMECAPS"; printf "%s\\n" "${NOTES[@]}"',
                        {'W': w, 'ARTIFACTS': art})
    caps = os.path.join(w, 'caps', 'game')
    got = sorted(open(os.path.join(caps, f), 'rb').read() for f in os.listdir(caps)) if os.path.isdir(caps) else []
    case('GC-3', len(got) == 2, 'gamedata/captures holds 2 captures (menu/, cell/), caps/game measures %d (%s); '
         'notes: %s' % (len(got), got, r.stdout.strip().splitlines()[1:]), True)


# ---- b663ae8: the salts cache ----------------------------------------------------------------------------------------
def salts_key_cases(t):
    src = open(JOB).read()
    m = re.search(r'SALTS=\$W/salts-\$\(key_of ([^)]*)\)\.json', src)
    if not m:
        case('SK-1', False, 'SALTS key expression not found')
        return
    args = m.group(1).split()
    want = {'"$SDP"', '"$CMP/perm.py"', '"$CMP/permcover.py"'}
    case('SK-1', want <= set(args), 'key inputs %s lack one of %s' % (args, want))
    # the key, computed by the job's own key_of over a fake package and tools: changing s6b_asm keeps it
    d = os.path.join(t, 'sk')
    os.makedirs(os.path.join(d, 'cmp'))
    os.makedirs(os.path.join(d, 'sh'))
    for n, c in (('cmp/perm.py', 'p'), ('cmp/permcover.py', 'c'), ('pkg.sdp', 'sdp'), ('sh/s6b_asm', 'asm1'),
                 ('sh/sdp2dksh', 'x1')):
        open(os.path.join(d, n), 'w').write(c)
    key = lambda: subprocess.run(['bash', '-c', job_func('key_of') + '\nW=/w\necho "$W/salts-$(key_of %s).json"'
                                  % m.group(1)], capture_output=True, text=True, timeout=30, env=dict(
        os.environ, SDP=os.path.join(d, 'pkg.sdp'), CMP=os.path.join(d, 'cmp'), SH=os.path.join(d, 'sh'))).stdout
    k0 = key()
    open(os.path.join(d, 'sh', 's6b_asm'), 'w').write('asm2')
    open(os.path.join(d, 'sh', 'sdp2dksh'), 'w').write('x2')
    k1 = key()
    open(os.path.join(d, 'cmp', 'perm.py'), 'w').write('p2')
    k2 = key()
    if k2 == k1:
        case('SK-1', False, 'a perm.py change keeps the key')
    case('SK-3', k1 != k0, 'new s6b_asm and sdp2dksh keep the salts key (%s): seeds chosen by the old tools\' '
         'reference frames are reused' % k0.strip(), True)


def write_stubs(d):
    """permcover.py + permreport.py beside a stub perm.py (manifest of --only with salts) and replay.py (a frame
    draws when STUB_DRAW[hash] <= salt; STUB_FAIL=1: the reference run fails and writes nothing)"""
    for n in ('permcover.py', 'permreport.py'):
        shutil.copy(os.path.join(CMP, n), os.path.join(d, n))
    open(os.path.join(d, 'perm.py'), 'w').write(r'''
import argparse, json, os
ap = argparse.ArgumentParser(); ap.add_argument('cmd')
for k in ('sdp', 'asm', 'sdp2dksh', 'out', 'only', 'salts'): ap.add_argument('--' + k)
a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
salts = json.load(open(a.salts)) if a.salts else {}
hs = a.only.split(',')
man = {'captures': {'c0': [{'frame': '%05d' % i, 'hash': h, 'salt': salts.get(h, 0)} for i, h in enumerate(hs)]}}
json.dump(man, open(os.path.join(a.out, 'manifest.json'), 'w')); open(os.path.join(a.out, 'c0.nxd9'), 'wb').write(b'x')
''')
    open(os.path.join(d, 'replay.py'), 'w').write(r'''
import argparse, json, os, sys
from PIL import Image
ap = argparse.ArgumentParser(); ap.add_argument('side')
for k in ('exe', 'prefix', 'out'): ap.add_argument('--' + k)
ap.add_argument('caps', nargs='*'); a = ap.parse_args()
if os.environ.get('STUB_FAIL'): sys.exit(1)
draw = json.loads(os.environ.get('STUB_DRAW', '{}'))
for c in a.caps:
    man = json.load(open(os.path.join(os.path.dirname(c), 'manifest.json')))
    for cap, lst in man['captures'].items():
        os.makedirs(os.path.join(a.out, cap), exist_ok=True)
        for e in lst:
            col = (200, 10, 10) if e['hash'] in draw and draw[e['hash']] <= e['salt'] else (0x40, 0x40, 0x40)
            Image.new('RGB', (4, 4), col).save(os.path.join(a.out, cap, 'f%s.png' % e['frame']))
''')


def permcover_cases(t):
    d = os.path.join(t, 'pc')
    os.makedirs(d)
    write_stubs(d)
    from PIL import Image
    hs = ['aaaa0001', 'aaaa0002', 'aaaa0003']
    man = {'captures': {'c0': [{'frame': '%05d' % i, 'hash': h} for i, h in enumerate(hs)]}}
    json.dump(man, open(os.path.join(d, 'manifest.json'), 'w'))
    ref = os.path.join(d, 'ref', 'c0')
    os.makedirs(ref)
    for i in range(3):
        Image.new('RGB', (4, 4), (0x40, 0x40, 0x40)).save(os.path.join(ref, 'f%05d.png' % i))
    base = [sys.executable, '-I', os.path.join(d, 'permcover.py'), '--sdp', 'x', '--asm', 'x', '--sdp2dksh', 'x',
            '--exe', 'x', '--prefix', 'x', '--manifest', os.path.join(d, 'manifest.json'), '--ref',
            os.path.join(d, 'ref'), '--tries', '4']
    r = subprocess.run(base + ['--out', os.path.join(d, 's1.json')], capture_output=True, text=True, timeout=120,
                       env=dict(os.environ, STUB_DRAW=json.dumps({'aaaa0001': 1, 'aaaa0002': 3})))
    s = json.load(open(os.path.join(d, 's1.json'))) if r.returncode == 0 else {}
    case('PC-1', s == {'aaaa0001': 1, 'aaaa0002': 3, '_unreached': ['aaaa0003']},
         'salts %s (rc %d %s)' % (s, r.returncode, r.stderr[-200:]))
    r = subprocess.run(base + ['--out', os.path.join(d, 's2.json')], capture_output=True, text=True, timeout=120,
                       env=dict(os.environ, STUB_FAIL='1', STUB_DRAW=json.dumps({h: 1 for h in hs})))
    s2 = json.load(open(os.path.join(d, 's2.json'))) if os.path.isfile(os.path.join(d, 's2.json')) else None
    # compare_job: `permcover ... && mv "$SALTS.tmp" "$SALTS"`: rc 0 caches the file under the package key
    case('SK-2', r.returncode != 0, 'every reference replay failed, permcover exit 0 with %s: compare_job caches it '
         '(mv to salts-<key>.json) and never retries' % s2, True)


def perm_cases():
    sys.path.insert(0, CMP)
    try:
        import perm
    except Exception as e:  # noqa: BLE001
        case('PB-1', False, 'import perm: %r' % e)
        return
    import random
    j = {'inputs': [{'usage': 'POSITION', 'index': 0}, {'usage': 'BLENDINDICES', 'index': 0},
                    {'usage': 'BLENDWEIGHT', 'index': 0}],
         'ctab': [{'name': 'Bones', 'set': 'float', 'class': 'vector', 'reg': 10, 'count': 6},
                  {'name': 'ModelViewProj', 'set': 'float', 'class': 'matrix_columns', 'reg': 0, 'rows': 4,
                   'count': 4},
                  {'name': 'Tint', 'set': 'float', 'class': 'vector', 'reg': 20, 'count': 1}]}
    vsc = perm.vs_job(j, random.Random(5))[4]
    bones = all(vsc[10 + e][:3] == [1.0 if i == e % 3 else 0.0 for i in range(3)] and abs(vsc[10 + e][3]) <= 0.1
                for e in range(6))
    mvp = vsc[2] == [0.0, 0.0, 0.5, 0.0]
    tint = vsc[20] != [1.0, 0.0, 0.0, vsc[20][3]] and all(-1 <= x <= 1 for x in vsc[20])
    srcp = open(os.path.join(CMP, 'perm.py')).read()
    seed = 'random.Random(int(h, 16) + (salts or {}).get(h, 0) * 0x9E3779B97F4A7C15)' in srcp
    case('PB-1', bones and mvp and tint and seed, 'bones %s mvp %s other %s seed formula %s' % (bones, mvp, tint, seed))


# ---- 1cb88ec: prefix_lock --------------------------------------------------------------------------------------------
HOLD = r'''
import os, subprocess, sys, time
sys.path.insert(0, sys.argv[1]); import replay
l = replay.prefix_lock(sys.argv[2])
print('got', flush=True)
if sys.argv[3] == 'child': subprocess.Popen(['sleep', '20'], start_new_session=True)
time.sleep(float(sys.argv[4]))
'''


def lock_cases(t):
    pfx = os.path.join(t, 'lk', 'pfx')
    hold = os.path.join(t, 'hold.py')
    open(hold, 'w').write(HOLD)
    sp = lambda p, mode='-', secs='30': subprocess.Popen([sys.executable, '-I', hold, CMP, p, mode, secs],
                                                         stdout=subprocess.PIPE, text=True)
    errs = []
    a = sp(pfx)
    if a.stdout.readline().strip() != 'got':
        errs.append('first holder did not get the lock')
    b = sp(pfx, secs='0')
    time.sleep(1.5)
    if b.poll() is not None:
        errs.append('second process did not wait')
    a.send_signal(signal.SIGKILL)
    a.wait()
    try:
        b.wait(timeout=5)
    except subprocess.TimeoutExpired:
        errs.append('stale lock after SIGKILL')
        b.kill()
    # symlinked prefix shares the lock
    ln = os.path.join(t, 'lk', 'link')
    os.symlink(pfx, ln)
    a = sp(pfx)
    a.stdout.readline()
    b = sp(ln, secs='0')
    time.sleep(1.5)
    if b.poll() is not None:
        errs.append('a symlinked prefix does not share the lock')
    a.kill(); a.wait()
    b.wait(timeout=5)
    # a daemonised child of the holder does not keep the lock
    a = sp(pfx, 'child', '0.5')
    a.stdout.readline()
    a.wait()
    b = sp(pfx, secs='0')
    try:
        b.wait(timeout=5)
    except subprocess.TimeoutExpired:
        errs.append('the holder\'s child kept the lock')
        b.kill()
    subprocess.run(['pkill', '-f', 'sleep 20'], capture_output=True)
    # d9cap's class lock: no replay.py ref (which takes the lock again) under it
    d9 = open(os.path.join(PORT, 'tests', 's16', 'test_d9cap.py')).read()
    if re.search(r"replay\.py'?\s*,\s*'ref'|replay\.main\(", d9):
        errs.append('test_d9cap runs replay.py ref while holding the prefix lock (self-deadlock)')
    case('LK-1', not errs, '; '.join(errs))


# ---- 86e77a6: check s4.vm_blocks ------------------------------------------------------------------------------------
def vm_cases(t):
    sys.path.insert(0, os.path.join(PORT, 'tests', 'nx1'))
    import check_platform

    def run(extents=None, npdm=None, tag='x'):
        root = os.path.join(t, 'vm-' + tag)
        for r in ('toolchain', 'platform/audio'):
            shutil.copytree(os.path.join(PORT, r), os.path.join(root, r), ignore=shutil.ignore_patterns('__pycache__'))
        if extents is not None:
            p = os.path.join(root, 'toolchain', 'host', 'host_vm.inc')
            s = open(p).read()
            s2 = re.sub(r'#define FNVNX_VM_EXTENTS \d+', '#define FNVNX_VM_EXTENTS ' + extents, s)
            assert s2 != s
            open(p, 'w').write(s2)
        if npdm:
            p = os.path.join(root, 'toolchain', 'npdm', 'fnvnx-app.json')
            j = json.load(open(p))
            j.update(npdm)
            json.dump(j, open(p, 'w'))
        old = check_platform.ROOT
        check_platform.ROOT = root
        try:
            rows = {cid: good for cid, good, _ in check_platform.check(False).rows}
        finally:
            check_platform.ROOT = old
        return rows.get('s4.vm_blocks')

    a, b = run(tag='as-is'), run('8192', tag='dec')
    case('VM-1', a is True and b is False, 's4.vm_blocks: 3072 -> %s, 8192 -> %s' % (a, b))
    h = run('0x2000', tag='hex')
    case('VM-2', h is False, '`#define FNVNX_VM_EXTENTS 0x2000` (8192) passes s4.vm_blocks', True)
    s = run('8192', {'system_resource_size': '0x1000000', 'address_space_type': '32bit_no_reserved'}, 'srs')
    case('VM-3', s is False, 'system_resource_size 0x1000000 in a 32bit_no_reserved NPDM passes 8192 extents '
         '(ldr gives a 32-bit space no system resource)', True)


# ---- 716b6b0: --ref-by-head ------------------------------------------------------------------------------------------
def ref_cases(t):
    sys.path.insert(0, os.path.join(PORT, 'tools', 'difffuzz'))
    import causes
    import fuzzcache
    fuzzcache.change_tags = lambda: []
    h1, h2 = '1111111111aa' + '0' * 28, '2222222222bb' + '0' * 28
    div = lambda a: dict(source='src/t.cpp', address=a, symbol='?f@@YAXXZ', case={}, hint=dict(cause='unclassified'),
                         divergence=dict(kind='error', x86=None, arm='exc:1@0'))
    published = {}   # branch -> files (fuzz_job.sh publish: the run's camp, fuzz/<C12>/<k>of<N>)

    def campaign(name, addrs):
        c = os.path.join(t, 'rh', name)
        os.makedirs(os.path.join(c, 'div'))
        fuzzcache.save_rows(c, {a: dict(address=a, source='src/t.cpp', status='ok') for a in addrs})
        for a in addrs:
            json.dump(div(a), open(os.path.join(c, 'div', '%s-0.json' % a), 'w'))
        return c

    def keys(addrs):
        p = os.path.join(t, 'rh', 'keys-%d.json' % len(published))
        json.dump({a: 'k' + a for a in addrs}, open(p, 'w'))
        return p

    # head H1 on 7of26 fuzzes A; H2 on 7of26 fuzzes B (A's key unchanged: reused)
    c7 = os.path.join(t, 'rh', '7of26')
    os.makedirs(c7)
    ca = campaign('h1camp', ['00401000'])
    fuzzcache.cmd_merge(c7, ca, keys(['00401000', '00402000', '00403000']), h1)
    published['fuzz/%s/7of26' % h1[:12]] = {'div/00401000-0.json'}
    cb = campaign('h2camp', ['00402000'])
    fuzzcache.cmd_merge(c7, cb, keys(['00401000', '00402000', '00403000']), h2)
    published['fuzz/%s/7of26' % h2[:12]] = {'div/00402000-0.json'}
    # H1 on the 13-shard layout fuzzed C (3of13); 7of26's cache was later seeded from fuzz/cache/3of13
    c3 = os.path.join(t, 'rh', '3of13')
    os.makedirs(c3)
    fuzzcache.cmd_merge(c3, campaign('h1camp13', ['00403000']), keys(['00403000']), h1)
    published['fuzz/%s/3of13' % h1[:12]] = {'div/00403000-0.json'}
    tg = os.path.join(t, 'rh', 'targets.jsonl')
    open(tg, 'w').write(''.join(json.dumps(dict(address=a)) + '\n' for a in ('00401000', '00402000', '00403000')))
    fuzzcache.cmd_seed(c7, tg, [c3])

    rows = [r for rs in causes.handoff_rows([c7], by_head=True).values() for r in rs]
    ref = {r[2]: r[4] for r in rows}

    def resolves(r):
        br, f = r.split(':', 1)
        return f in published.get(br, set())
    okA = ref.get('00401000') == 'fuzz/%s/7of26:div/00401000-0.json' % h1[:12] and resolves(ref['00401000'])
    okB = ref.get('00402000') == 'fuzz/%s/7of26:div/00402000-0.json' % h2[:12] and resolves(ref['00402000'])
    case('RH-1', okA and okB, 'refs %s' % ref)
    rc = ref.get('00403000', '')
    case('RH-2', rc and resolves(rc), 'the seeded row\'s reproducer %s is on no published branch (H1 published it on '
         'fuzz/%s/3of13)' % (rc, h1[:12]), True)


def main():
    t = tempfile.mkdtemp(prefix='v1-s16b-')
    if not os.path.isdir(t):
        raise SystemExit('mkdtemp failed')
    try:
        lock_cases(t)
        perm_cases()
        permcover_cases(t)
        salts_key_cases(t)
        game_cases(t)
        vm_cases(t)
        ref_cases(t)
    finally:
        shutil.rmtree(t, ignore_errors=True)
    print('%d cases refuted or defective' % BAD)
    return 1 if BAD else 0


if __name__ == '__main__':
    sys.exit(main())
