#!/usr/bin/env python3
"""build.py - the fnv-nx build pipeline (stream S9-build; docs/BUILD.md). Entry point: ports/fnv-nx/Makefile.

    tools/pipeline/build.py [VAR=value ...] [stage ...]      run the stages (and what they need); default: all
    tools/pipeline/build.py [VAR=value ...] status           table of the recorded stage states
    tools/pipeline/build.py [VAR=value ...] clean [stage...] remove build/ (or the named stage directories)

Stages, in order: fetch toolchain prepare census compile link guest boot nine host package tests; `report` runs
last on every invocation and writes build/report.md (+ report.json, history/). Each stage owns build/<stage>/ and
records build/<stage>/stage.json (status ok|warn|fail|skipped|disabled, fingerprint, metrics, artifacts, log). A
stage whose input fingerprint and recorded status are unchanged is not run again (`cached`); inside compile,
link and tests the tools' own incremental rules apply as well. A failing stage never stops the pipeline: the
stages that need it are `skipped`, the others run; the exit code is 1 when any stage failed.

Provenance (J-0054): prepare records the commit of ports/fnv-nx and of the decomp the tree was prepared from
(build/prepare/prepared_from.txt, .json). Every stage that consumes tree-derived outputs (census, compile, link,
guest, boot, package) carries that record in its stage.json and in the report, and refuses inputs prepared from
another commit than the current head unless FORCE_STALE=1.

Everything under build/ is derived from the decomp or the player's game: local only, never committed.
"""
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, HERE)
import report as reportmod  # noqa: E402
import undef as undefmod    # noqa: E402

PY = sys.executable
DATA_START = 0x00fdf000

# key, default, help (docs/BUILD.md repeats this table)
CONFIG = [
    ('BUILD_DIR', os.path.join(ROOT, 'build'), 'output root'),
    ('EXT', '', 'directory with the pinned checkouts (fnv_decomp, sse2neon, libnx); empty: ext/ via tools/fetch.sh'),
    ('ALLOW_UNPINNED', '0', '1: a checkout at another commit than tools/fetch.sh pins is a warning, not a failure'),
    ('FNVNX_TC', '', 'fnvnx-a64 prefix (toolchain/gcc/build.sh output); empty: $DEVKITPRO/devkitA64 or build it'),
    ('DEVKITPRO', '', 'DEVKITPRO tree with libnx (toolchain/devkita64/setup.sh output)'),
    ('TC_DL', 'auto', 'how fnvnx-a64 + DEVKITPRO are built when FNVNX_TC / DEVKITPRO are not given: auto = fetch '
                      'the pinned sources (toolchain/sources.sh) and build them in build/toolchain; a directory = the '
                      'binutils/gcc/newlib tarballs from there; none = never build'),
    ('CROSS', 'auto', 'the aarch64-linux-gnu tools: auto = the system\'s when they are the pinned Ubuntu build, else '
                      'unpack the pinned packages (toolchain/cross/setup.sh) into build/toolchain/cross; pinned = '
                      'always those; system = whatever is on PATH'),
    ('JOBS', str(os.cpu_count() or 4), 'parallel jobs'),
    ('GAME_CXX', 'standin', 'compiler of the game units: standin (aarch64-linux-gnu-g++, the compiler of every '
                            'measured link and boot so far), tc (fnvnx-a64 g++), auto (tc when the toolchain is '
                            'there), or a command'),
    ('CROSS_BIN', '', 'directory put first on PATH for the aarch64-linux-gnu-* cross tools (a private copy such as '
                      'S2c\'s xtc/bin when the system packages are missing)'),
    ('OPT', '-O2', 'optimisation flags of the game units (tools/link/compile.sh $OPT)'),
    ('UNITS', '', 'file listing the units (sources as in units.json, one per line, # comments) that census and '
                  'compile handle, so the link and guest see only those; empty: every unit'),
    ('PREPARED', '', 'an existing prepared tree to adopt (read only) instead of running tools/prepare.py; its '
                     'prepared_from.txt (in the tree or next to it) gives its provenance'),
    ('OBJECTS', '', 'objects.tsv of objects another job compiled from that tree (the compile farms: '
                    'tools/farm/fetch_objs.sh), used in place instead of compiling'),
    ('FORCE_STALE', '0', '1: stages consume inputs prepared from another commit than the current head (recorded as '
                         'stale, status warn); 0: they refuse them'),
    ('PORT_COMMIT', '', 'commit (40 hex digits) of ports/fnv-nx when it is a copy without git metadata (a git '
                        'archive snapshot); in a checkout it must equal HEAD'),
    ('CENSUS', '1', '0: skip the -fsyntax-only census'),
    ('OVERRIDE', 'auto', 'fnvlink resolve --override table (native replacements): auto = the port\'s own, built by '
                         'tools/link/overrides/build.sh in the link stage; none = off; or a table'),
    ('LINK_OVERRIDES', 'auto', 'native replacements by name, added to OVERRIDE\'s: render = render/native/overrides.tsv '
                               'with --override-lib libnxrender.a (RENDER_LIB; the guest links the archive, the engine\'s '
                               'D3D9 entry points go to the native renderer); auto = render when the toolchain stage '
                               'has DEVKITPRO and fnvnx-a64 (a build that links a guest: the full build), else none; '
                               'none = the pristine path (fuzz, equivalence, the link farm)'),
    ('RENDER_LIB', '', 'libnxrender.a for LINK_OVERRIDES=render; empty: built by render/native/n1/build.sh in the link '
                       'stage with the toolchain stage\'s DEVKITPRO and fnvnx-a64 and UAM (the environment\'s, else '
                       'toolchain/uam/build.sh, cached next to the link stage)'),
    ('EXTERN_TEXT', 'auto', 'fnvlink resolve --extern-text list: auto = made from the tree\'s units.json by '
                            'tools/pipeline/extern_text.py (S4\'s addr_ids.txt), none = off, or a file'),
    ('MSVC_OBJS', '', 'fnvlink resolve --msvc-objs: pristine MSVC 2008 objects (S11) for S2b\'s vtable slots'),
    ('MSVC_CODE', '', 'fnvlink resolve --msvc-code: the same objects, only for the definitions\' register reads '
                      '(ecx-not-passed / this-not-passed judged on the MSVC code); without it the verdicts are as '
                      'before'),
    ('UNITS_RENAMES', '', 'fnvlink resolve --units-renames table (empty: fnvlink\'s default, the port\'s '
                          'patches/units_renames.tsv; none: off)'),
    ('LINK_SEED', '', 'fnvlink out dir of an earlier run: its demangle cache (cache/) seeds build/link/out'),
    ('GUEST_LINK', 'auto', 'guest link script: auto = toolchain/game/build.sh (S5) when present, else the adapter '
                           'tools/pipeline/guest_link.py'),
    ('GAME_NEW', 'runtime', 'global operator new/delete of the guest (toolchain/game/build.sh GAME_NEW): runtime '
                            '(malloc stand-ins) or game (the game objects\' own)'),
    ('VORBIS', 'auto', 'libogg/libvorbis ILP32 prefix for the guest link (S5 script only): auto = built by the '
                       'toolchain stage (toolchain/vorbis/build.sh), none = without, or a prefix'),
    ('BOOT', '0', '1: the boot stage (emulated boot of the game guest + boot frontier) runs with `all`; it also runs '
                  'when named as a goal (make boot)'),
    ('BOOT_IMAGE', 'auto', 'data image for the emulated boot (sdmc:/switch/fnv/FalloutNV.exe): auto = S14\'s '
                           'synthesized image when PRISTINE_OBJS is given, else none; none = no image (all-zero '
                           'data); or an exe file'),
    ('PRISTINE_OBJS', '', 'MSVC 2008 objects of the pristine decomp (S11, tools/equiv: u<index>.obj), for S14\'s '
                          'synthesized data image (loader/synth/synth_image.py)'),
    ('BOOT_CENSUS', '', 'census units.tsv the frontier reports the current compile status from (empty: this '
                        'build\'s census when it ran)'),
    ('NINE', '0', '1: Gallium Nine (render/nine) in the host module; 0: no D3D9 backend in the host (null device)'),
    ('HOST_BACKEND', 'native', 'host module: native = host.c without console + the native deko3d renderer (render/native/host, '
                       'toolchain/host/build_native.sh; owner decision); plain = host.c with its text console (opt-in); '
                       'NINE=1 overrides both with the Nine host'),
    ('NINE_SRC', '', 'directory holding mesa/ and libdrm_nouveau/ checkouts to copy instead of cloning'),
    ('PROGRAM_ID', '0100000000000000', 'program id written into main.npdm; ldr replaces it with the title the launcher '
                                       'fnv.nro restarts, so the placeholder is fine (NX1)'),
    ('TESTS', 'all', 'comma list of test suites to run (names in docs/BUILD.md), all, or none'),
    ('EMU_TIMEOUT', '3000', 'seconds for the emulated boot of the game guest (boot stage)'),
    ('EMU_BUDGET', 'auto', 'instructions per scheduling slice of the emulated boot; auto: 0 (threads switch at host '
                           'calls, deterministically) when tests/s5/emu_guest.py has --hc-slice (S5 620572bc), else '
                           '6000000000. An instruction count makes unicorn hook every instruction, about 10x slower'),
    ('EMU_SLICE_MS', '', 'emu_guest.py --slice-ms (its fallback timer for a thread making no host call); empty: '
                         'emu_guest.py\'s default (60 s). A short timer makes boots nondeterministic (S5)'),
    ('FORCE', '', 'comma list of stages to rerun even when cached (all = every stage)'),
    ('CLEAN_WORK', '1', '1: remove re-creatable work trees (clones, third-party build dirs) once their stage or test '
                        'suite succeeded; 0: keep them for faster reruns'),
    ('MIN_FREE_GB', '10', 'free disk space (GiB) below which the toolchain stage warns'),
    ('KEEP_HISTORY', '50', 'report snapshots kept in build/history'),
]
STAGE_NAMES = ['fetch', 'toolchain', 'prepare', 'census', 'compile', 'link', 'guest', 'boot', 'nine', 'host',
               'package', 'tests']
# the files tools/prepare.py and its rule modules read besides the decomp (the prepare fingerprint)
PREPARE_INPUTS = ('tools/prepare.py', 'tools/rules', 'patches', 'shim/include/fnvnx_win32.h',
                  'render/native/drop_units.txt')


# ------------------------------------------------------------------------------------------------- hashing helpers

def sha(*items):
    h = hashlib.sha256()
    for it in items:
        h.update(repr(it).encode('utf-8', 'surrogateescape'))
        h.update(b'\0')
    return h.hexdigest()[:16]


def file_hash(path):
    if not path or not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()[:16]


SKIP_DIRS = {'__pycache__', 'build', '.git', 'node_modules'}


def tree_hash(*paths):
    """Content hash of files and directories (relative to ROOT when inside it); skips build/, caches, .git."""
    h = hashlib.sha256()
    for p in paths:
        p = p if os.path.isabs(p) else os.path.join(ROOT, p)
        if os.path.isfile(p):
            h.update((os.path.relpath(p, ROOT) + ':' + (file_hash(p) or '')).encode())
            continue
        for dp, dn, fn in os.walk(p):
            dn[:] = sorted(d for d in dn if d not in SKIP_DIRS)
            for n in sorted(fn):
                if n.endswith(('.pyc', '.pyo')):
                    continue
                f = os.path.join(dp, n)
                h.update((os.path.relpath(f, ROOT) + ':' + (file_hash(f) or '')).encode())
    return h.hexdigest()[:16]


def listing_id(paths):
    """Cheap identity of a set of files: (name, size, mtime_ns)."""
    items = []
    for p in sorted(paths):
        try:
            st = os.stat(p)
            items.append((os.path.basename(p), st.st_size, st.st_mtime_ns))
        except OSError:
            items.append((os.path.basename(p), None))
    return sha(items)


def du(path):
    """Bytes under path (files only, symlinks not followed)."""
    total = 0
    for dp, dn, fn in os.walk(path):
        for n in fn:
            try:
                total += os.lstat(os.path.join(dp, n)).st_size
            except OSError:
                pass
    return total


def free_bytes(path):
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


def remove_work(b, r, paths, why):
    """CLEAN_WORK=1: drop re-creatable work trees (they are rebuilt or re-cloned when needed again)."""
    if b.cfg['CLEAN_WORK'] == '0':
        return 0
    freed = 0
    for p in paths:
        if os.path.lexists(p):
            n = du(p) if os.path.isdir(p) else os.path.getsize(p)
            shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) and not os.path.islink(p) else os.remove(p)
            freed += n
    if freed and r is not None:
        r.note('CLEAN_WORK: removed %s of %s' % (human(freed), why))
    return freed


def now_iso():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def read_git_head(path):
    """Commit checked out in a git checkout, read from the files (no git process): None if not a checkout."""
    dotgit = os.path.join(path, '.git')
    if os.path.isfile(dotgit):
        m = re.match(r'gitdir:\s*(.+)', open(dotgit).read().strip())
        if not m:
            return None
        gitdir = m.group(1) if os.path.isabs(m.group(1)) else os.path.join(path, m.group(1))
    elif os.path.isdir(dotgit):
        gitdir = dotgit
    else:
        return None
    try:
        head = open(os.path.join(gitdir, 'HEAD')).read().strip()
    except OSError:
        return None
    if re.fullmatch(r'[0-9a-f]{40}', head):
        return head
    m = re.match(r'ref:\s*(\S+)', head)
    if not m:
        return None
    ref = m.group(1)
    common = gitdir
    cf = os.path.join(gitdir, 'commondir')
    if os.path.isfile(cf):
        c = open(cf).read().strip()
        common = c if os.path.isabs(c) else os.path.normpath(os.path.join(gitdir, c))
    for d in (gitdir, common):
        rp = os.path.join(d, ref)
        if os.path.isfile(rp):
            return open(rp).read().strip()
    pr = os.path.join(common, 'packed-refs')
    if os.path.isfile(pr):
        for line in open(pr):
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref:
                return parts[0]
    return None


def find_checkout(path):
    """Top of the git checkout (or worktree) holding path, or None for a copy without git metadata."""
    p = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(p, '.git')):
            return p
        up = os.path.dirname(p)
        if up == p:
            return None
        p = up


def port_dirty(paths):
    """Uncommitted changes under ports/fnv-nx `paths` (git status --porcelain lines: modified, staged, untracked),
    or None when there is no checkout or no git."""
    if not find_checkout(ROOT) or not shutil.which('git'):
        return None
    try:
        p = subprocess.run(['git', '--no-optional-locks', '-C', ROOT, 'status', '--porcelain', '--'] + list(paths),
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return [l for l in p.stdout.splitlines() if l.strip()] if p.returncode == 0 else None


def port_head(cfg):
    """The commit of ports/fnv-nx this run's tools come from: the checkout's HEAD (read from the files), or
    PORT_COMMIT for a copy without git metadata."""
    top = find_checkout(ROOT)
    head = read_git_head(top) if top else None
    want = cfg.get('PORT_COMMIT', '').strip().lower()
    if want and head and not head.startswith(want):
        sys.exit('PORT_COMMIT=%s but the checkout %s is at %s' % (want, top, head))
    if not head and re.fullmatch(r'[0-9a-f]{40}', want or ''):
        head = want
    return head


def short(commit):
    return commit[:10] if commit else 'unknown'


def read_prepared_from(tree):
    """Provenance of a prepared tree: build/prepare/prepared_from.json of a pipeline tree, else a prepared_from.txt
    (S2c's driver: the commit on the first line, next to the tree) in the tree's directory or the tree itself.
    -> dict (port_commit None when unknown)."""
    for d in (os.path.dirname(os.path.abspath(tree)), os.path.abspath(tree)):
        j = os.path.join(d, 'prepared_from.json')
        if os.path.isfile(j):
            try:
                pf = json.load(open(j))
                pf['source'] = j
                return pf
            except ValueError:
                pass
        t = os.path.join(d, 'prepared_from.txt')
        if os.path.isfile(t):
            lines = open(t, errors='replace').read().splitlines()
            m = re.search(r'\b([0-9a-f]{40})\b', lines[0] if lines else '')
            dm = next((re.search(r'\bdecomp\s+([0-9a-f]{40})\b', l) for l in lines[1:]
                       if re.search(r'\bdecomp\s+[0-9a-f]{40}\b', l)), None)
            return {'port_commit': m.group(1) if m else None, 'decomp_commit': dm.group(1) if dm else None,
                    'source': t}
    return {'port_commit': None, 'decomp_commit': None, 'source': None}


def read_units_list(path):
    """UNITS=<file>: unit sources, one per line; blank lines and # comments ignored."""
    out = []
    for line in open(path, errors='surrogateescape'):
        s = line.split('#', 1)[0].strip()
        if s:
            out.append(s)
    return out


def object_intact(path):
    """An object a container restart left truncated or zero-filled is not intact (header, section table and every
    section's data inside the file, last page not all zero; S2c's objcheck)."""
    try:
        with open(path, 'rb') as f:
            d = f.read()
    except OSError:
        return False
    if len(d) < 52 or d[:4] != b'\x7fELF' or d[4] != 1:
        return False
    shoff, = struct.unpack_from('<I', d, 32)
    shentsize, shnum = struct.unpack_from('<HH', d, 46)
    if shnum == 0 or shoff + shentsize * shnum > len(d):
        return False
    for i in range(shnum):
        _, typ, _, _, off, size = struct.unpack_from('<IIIIII', d, shoff + i * shentsize)
        if typ != 8 and off + size > len(d):     # SHT_NOBITS has no file data
            return False
    return any(d[-4096:])


def human(n):
    if n is None:
        return '-'
    for unit in ('B', 'KiB', 'MiB', 'GiB'):
        if n < 1024 or unit == 'GiB':
            return ('%d %s' % (n, unit)) if unit == 'B' else ('%.1f %s' % (n, unit))
        n /= 1024.0


# ----------------------------------------------------------------------------------------------------- the build

class Record(dict):
    """build/<stage>/stage.json."""

    def metric(self, k, v):
        self.setdefault('metrics', {})[k] = v

    def note(self, s):
        self.setdefault('notes', []).append(s)

    def artifact(self, name, path):
        self.setdefault('artifacts', {})[name] = path


class StageError(Exception):
    pass


class Build:
    def __init__(self, cfg):
        self.cfg = cfg
        self.out = os.path.abspath(cfg['BUILD_DIR'])
        self.jobs = max(1, int(cfg['JOBS']))
        self.recs = {}
        self.ran = []
        self.explicit = set()      # stage goals named on the command line (make boot)
        self.cross_bin = False     # set by the toolchain stage while it runs; False: take its record
        self.run_id = time.strftime('%Y%m%d-%H%M%S', time.gmtime())
        self.head = port_head(cfg)  # the commit every tree-derived output must be prepared from (J-0054)
        os.makedirs(self.out, exist_ok=True)
        self.plog = open(os.path.join(self.out, 'pipeline.log'), 'a')

    # ---- paths and state
    def sdir(self, stage):
        d = os.path.join(self.out, stage)
        os.makedirs(d, exist_ok=True)
        return d

    def rec(self, stage):
        if stage not in self.recs:
            p = os.path.join(self.out, stage, 'stage.json')
            try:
                with open(p) as f:
                    self.recs[stage] = Record(json.load(f))
            except (OSError, ValueError):
                self.recs[stage] = Record(stage=stage, status='never')
        return self.recs[stage]

    def save(self, r):
        self.recs[r['stage']] = r
        p = os.path.join(self.sdir(r['stage']), 'stage.json')
        with open(p + '.tmp', 'w') as f:
            json.dump(r, f, indent=1, sort_keys=True)
        os.replace(p + '.tmp', p)

    def art(self, stage, name, default=None):
        return self.rec(stage).get('artifacts', {}).get(name, default)

    def ok(self, stage):
        return self.rec(stage).get('status') in ('ok', 'warn')

    def say(self, msg):
        line = '[%s] %s' % (time.strftime('%H:%M:%S'), msg)
        print(line, flush=True)
        self.plog.write(line + '\n')
        self.plog.flush()

    # ---- environment of the tools
    def env(self, extra=None):
        e = dict(os.environ)
        path = e.get('PATH', '')
        dkp = self.art('toolchain', 'devkitpro')
        tc = self.art('toolchain', 'prefix')
        if tc:
            path = os.path.join(tc, 'bin') + ':' + path
        if dkp:
            e['DEVKITPRO'] = dkp
            path = os.path.join(dkp, 'tools', 'bin') + ':' + path
        cb = self.cfg.get('CROSS_BIN') or (self.cross_bin if self.cross_bin is not False
                                           else self.art('toolchain', 'cross_bin'))
        if cb:
            path = os.path.abspath(cb) + ':' + path
        e['PATH'] = path
        ext = self.art('fetch', 'ext')
        if ext:
            e['SSE2NEON'] = os.path.join(ext, 'sse2neon')
        e['FNVNX_TOOLCHAIN'] = os.path.join(ROOT, 'toolchain')
        e['JOBS'] = str(self.jobs)
        e.pop('PYTHONPATH', None)
        if extra:
            e.update({k: v for k, v in extra.items() if v is not None})
        return e

    def sh(self, argv, log, cwd=None, env=None, timeout=None, check=False, quiet=False):
        """Run argv, output appended to log; returns the exit code (124 on timeout)."""
        argv = [str(a) for a in argv]
        with open(log, 'a', errors='surrogateescape') as f:
            f.write('\n$ %s%s\n' % ('cd %s && ' % shlex.quote(cwd) if cwd else '', ' '.join(map(shlex.quote, argv))))
            f.flush()
            t0 = time.time()
            p = subprocess.Popen(argv, cwd=cwd, env=env or self.env(), stdout=f, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, start_new_session=True)
            try:
                rc = p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
                rc = 124
                f.write('[timeout after %d s]\n' % timeout)
            f.write('[exit %d, %.1f s]\n' % (rc, time.time() - t0))
        if check and rc:
            prog = [a for a in argv if not a.startswith('-') and a not in (PY, 'bash', 'env') and '=' not in a]
            raise StageError('%s exited %d (log %s): %s' % (os.path.basename(prog[0]) if prog else argv[0], rc,
                                                           self.rel(log), ' | '.join(self.tail(log, 4)[:-1])))
        return rc

    def which(self, tool):
        """shutil.which on the PATH the tools run with (CROSS_BIN, the toolchain prefix, DEVKITPRO tools)."""
        return shutil.which(tool, path=self.env()['PATH'])

    def units_filter(self):
        """UNITS=<file> -> (set of unit sources, content id), or (None, '') for every unit."""
        if not self.cfg.get('UNITS'):
            return None, ''
        p = os.path.abspath(self.cfg['UNITS'])
        if not os.path.isfile(p):
            raise StageError('UNITS=%s: no such file' % p)
        lst = read_units_list(p)
        return set(lst), sha(sorted(set(lst)))

    def capture(self, argv, env=None, cwd=None, timeout=60):
        try:
            p = subprocess.run([str(a) for a in argv], capture_output=True, text=True, env=env or self.env(),
                               cwd=cwd, timeout=timeout, errors='surrogateescape')
            return p.returncode, p.stdout + p.stderr
        except (OSError, subprocess.TimeoutExpired) as ex:
            return 127, str(ex)

    @staticmethod
    def rel(p):
        """Path for people: relative to ports/fnv-nx when inside it."""
        if not p:
            return p
        p = os.path.abspath(p)
        if p == ROOT or p.startswith(ROOT + os.sep):
            return os.path.relpath(p, ROOT)
        return p

    @staticmethod
    def tail(path, n=12):
        try:
            with open(path, errors='replace') as f:
                lines = f.read().splitlines()
        except OSError:
            return []
        return lines[-n:]


# ---------------------------------------------------------------------------------------------------- stage base

class Stage:
    name = ''
    deps = ()        # must be ok/warn, else this stage is skipped
    soft = ()        # used when available
    always = False   # cheap: run on every invocation (its output_id still decides what is downstream)
    prov = ()        # stages whose tree-derived outputs this one consumes: their prepared_from is checked against
                     # the current head and carried into this stage's record (J-0054)

    def enabled(self, b):
        return True

    def fingerprint(self, b):
        return None

    def outputs_present(self, b):
        return True

    def run(self, b, r):
        raise NotImplementedError


def up_id(b, stage):
    return b.rec(stage).get('output_id')


# ----------------------------------------------------------------------------------------------------- 1 fetch

def parse_pins():
    pins = []
    for line in open(os.path.join(ROOT, 'tools', 'fetch.sh')):
        m = re.match(r'^pin\s+(\S+)\s+(\S+)\s+([0-9a-f]{40})\b', line)
        if m:
            pins.append(m.groups())
    return pins


def fetch_ext(b):
    return os.path.abspath(b.cfg['EXT'] or os.path.join(ROOT, 'ext'))


class Fetch(Stage):
    name = 'fetch'

    def fingerprint(self, b):
        # the checkouts' HEADs, read from their git files: the stage itself does nothing more than this
        ext = fetch_ext(b)
        return ['fetch-v2', ext, bool(b.cfg['EXT']), b.cfg['ALLOW_UNPINNED'], parse_pins(),
                [(n, read_git_head(os.path.join(ext, n))) for n in [p[0] for p in parse_pins()] +
                 ['devkita64-rules', 'buildscripts', 'nx-hbloader']]]

    def outputs_present(self, b):
        return os.path.isfile(os.path.join(b.out, 'fetch', 'pins.tsv'))

    def run(self, b, r):
        log = r['log']
        pins = parse_pins()
        if not pins:
            raise StageError('no pins found in tools/fetch.sh')
        explicit = bool(b.cfg['EXT'])
        ext = os.path.abspath(b.cfg['EXT'] or os.path.join(ROOT, 'ext'))

        def check():
            rows = []
            for name, url, commit in pins:
                head = read_git_head(os.path.join(ext, name))
                rows.append((name, commit, head or '-', 'ok' if head == commit else ('missing' if head is None
                                                                                       else 'other-commit')))
            return rows

        rows = check()
        if any(st != 'ok' for *_, st in rows) and not explicit:
            b.say('fetch: running tools/fetch.sh %s' % b.rel(ext))
            b.sh(['bash', os.path.join(ROOT, 'tools', 'fetch.sh'), ext], log, timeout=3600)
            rows = check()
        with open(os.path.join(b.sdir('fetch'), 'pins.tsv'), 'w') as f:
            f.write('# name\tpinned\tchecked out\tstate\tpath\n')
            for name, commit, head, st in rows:
                f.write('%s\t%s\t%s\t%s\t%s\n' % (name, commit, head, st, os.path.join(ext, name)))
        bad = [x for x in rows if x[3] != 'ok']
        for name, commit, head, st in rows:
            r.metric('pin_' + name, head[:8] if head != '-' else 'missing')
        optional = {}
        for name in ('devkita64-rules', 'buildscripts', 'nx-hbloader'):
            hd = read_git_head(os.path.join(ext, name))
            if hd:
                optional[name] = hd[:8]
        r['optional'] = optional
        r.artifact('ext', ext)
        r.artifact('decomp', os.path.join(ext, 'fnv_decomp'))
        r['output_id'] = sha(ext, rows)
        r['summary'] = '%s: %s' % (b.rel(ext) if not explicit else ext,
                                   ', '.join('%s@%s' % (n, h[:8]) for n, _, h, _ in rows))
        if bad:
            msg = ', '.join('%s %s (pinned %s)' % (n, st, c[:8]) for n, c, h, st in bad)
            if b.cfg['ALLOW_UNPINNED'] == '1' and all(st == 'other-commit' for *_, st in bad):
                r['status'] = 'warn'
                r.note('unpinned checkouts allowed: ' + msg)
            else:
                raise StageError('not at the pinned commit: ' + msg)
        if explicit:
            r.note('EXT given: pins verified from the checkouts\' HEAD (read-only); local edits are not detected')


# ------------------------------------------------------------------------------------------------- 2 toolchain

HOST_TOOLS = [  # tool, needed by, required for the core pipeline
    ('jq', 'census.sh, compile.sh', True),
    ('aarch64-linux-gnu-g++', 'GAME_CXX=standin, S2 tests, tests/s4|s7|s8 ILP32 checks', False),
    ('aarch64-linux-gnu-objcopy', 'fnvlink apply', True),
    ('aarch64-linux-gnu-as', 'fnvlink apply (bridges)', True),
    ('aarch64-linux-gnu-ld', 'fnvlink check (trial link)', True),
    ('aarch64-linux-gnu-gcc', 'fnvlink apply (addrmap.c)', True),
    ('aarch64-linux-gnu-objdump', 'fnvlink probe', True),
    ('llvm-undname', 'fnvlink resolve', True),
    ('c++filt', 'fnvlink resolve', True),
    ('qemu-aarch64', 'tools/link/test_bridges.py', False),
    ('cmake', 'render/shaders (tests s6b)', False),
    ('ninja', 'render/shaders, render/nine', False),
    ('meson', 'render/shaders, render/nine', False),
    ('bison', 'render/shaders (mesa glsl)', False),
    ('flex', 'render/shaders (mesa glsl)', False),
    ('gcc', 'host tests', False),
    ('g++', 'host tests', False),
    ('git', 'fetch.sh, render/*/build.sh', False),
]


def stat_id(path):
    try:
        st = os.stat(path)
        return (path, st.st_size, st.st_mtime_ns)
    except (OSError, TypeError):
        return (path, None)


def cross_pins():
    """toolchain/cross/packages.tsv -> {package: version}."""
    pins = {}
    for line in open(os.path.join(ROOT, 'toolchain', 'cross', 'packages.tsv')):
        p = line.rstrip('\n').split('\t')
        if len(p) >= 5 and not p[0].startswith('#'):
            pins[p[0]] = p[1]
    return pins


def cross_pin_matches(b, path_prefix=None):
    """Whether the aarch64-linux-gnu tools on PATH are the pinned Ubuntu builds (g++ version string, ld 2.42) ->
    (bool, g++ version line)."""
    gxx = shutil.which('aarch64-linux-gnu-g++', path=path_prefix) if path_prefix else b.which('aarch64-linux-gnu-g++')
    if not gxx:
        return False, None
    pins = cross_pins()
    rc, out = b.capture([gxx, '--version'])
    first = out.splitlines()[0] if not rc and out else ''
    want_gcc = '(Ubuntu %s)' % pins['g++-13-aarch64-linux-gnu'].split('cross')[0]
    ld = os.path.join(os.path.dirname(gxx), 'aarch64-linux-gnu-ld')
    rc, lout = b.capture([ld if os.path.exists(ld) else 'aarch64-linux-gnu-ld', '--version'])
    want_ld = ' %s' % pins['binutils-aarch64-linux-gnu'].split('-')[0]
    ok = want_gcc in first and not rc and want_ld in (lout.splitlines() or [''])[0]
    return ok, first


def provide_cross(b, r, d):
    """The aarch64-linux-gnu tools (decomp compile, fnvlink): CROSS_BIN when given; CROSS=system: whatever is on
    PATH; CROSS=pinned, or CROSS=auto when PATH has no tools of the pinned Ubuntu build: toolchain/cross/setup.sh
    into build/toolchain/cross (the pinned packages, unpacked without root). -> the bin dir put first on PATH, or
    None."""
    mode = b.cfg['CROSS']
    if b.cfg['CROSS_BIN']:
        r['cross'] = 'CROSS_BIN'
        return os.path.abspath(b.cfg['CROSS_BIN'])
    if mode == 'system':
        r['cross'] = 'system (CROSS=system)'
        return None
    if mode == 'auto':
        b.cross_bin = None                          # the host's own PATH
        ok, ver = cross_pin_matches(b)
        if ok:
            r['cross'] = 'system, the pinned build (%s)' % ver
            return None
        r.note('the aarch64-linux-gnu tools on PATH are not the pinned build (%s): unpacking the pinned packages'
               % (ver or 'none'))
    out = os.path.join(d, 'cross')
    b.say('toolchain: pinned AArch64 cross tools (toolchain/cross/setup.sh)')
    b.sh(['bash', os.path.join(ROOT, 'toolchain', 'cross', 'setup.sh'), out], r['log'], check=True, timeout=3600)
    r['cross'] = 'pinned packages in %s' % b.rel(out)
    return os.path.join(out, 'bin')


def build_id(*paths, extra=()):
    return sha(tree_hash(*paths), list(extra))


def read_id(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def provide_fnvnx_a64(b, r, d):
    """fnvnx-a64 and the DEVKITPRO tree: FNVNX_TC / DEVKITPRO when given (never modified); else the ones this
    build dir holds when they were built by the current scripts; else, unless TC_DL=none, built here: the pinned
    sources (toolchain/sources.sh, or TC_DL=<dir> with the tarballs), toolchain/gcc/build.sh (~45 min on 4 cores),
    toolchain/devkita64/setup.sh with the pinned libnx. -> (prefix, devkitpro)."""
    log = r['log']
    prefix, dkp = b.cfg['FNVNX_TC'], b.cfg['DEVKITPRO']
    if not prefix and dkp and os.path.isdir(os.path.join(dkp, 'devkitA64')):
        prefix = os.path.realpath(os.path.join(dkp, 'devkitA64'))
        r.note('FNVNX_TC taken from $DEVKITPRO/devkitA64')
    own_prefix, own_dkp = os.path.join(d, 'prefix'), os.path.join(d, 'dkp')
    src = os.path.join(d, 'src')
    tc_id = build_id('toolchain/gcc/build.sh', 'toolchain/sources.sh')
    ext = b.art('fetch', 'ext') or os.path.join(ROOT, 'ext')
    dkp_id = build_id('toolchain/devkita64', 'toolchain/tools', extra=[tc_id, read_git_head(os.path.join(ext, 'libnx'))])

    def built(path, want):
        return read_id(os.path.join(path, '.fnvnx-build-id')) == want

    may_build = b.cfg['TC_DL'] != 'none'
    if not prefix and built(own_prefix, tc_id):
        prefix = own_prefix
    if not prefix and may_build:
        dl = os.path.abspath(b.cfg['TC_DL']) if b.cfg['TC_DL'] not in ('', 'auto') else os.path.join(d, 'dl')
        b.say('toolchain: fnvnx-a64 sources (toolchain/sources.sh)')
        b.sh(['bash', os.path.join(ROOT, 'toolchain', 'sources.sh'), dl, src], log, check=True, timeout=3600)
        work = os.path.join(d, 'work')
        if read_id(os.path.join(work, '.fnvnx-build-id')) != tc_id:
            # its step stamps belong to other scripts: start over; the same scripts resume after an interruption
            shutil.rmtree(work, ignore_errors=True)
            shutil.rmtree(own_prefix, ignore_errors=True)
            os.makedirs(work)
            with open(os.path.join(work, '.fnvnx-build-id'), 'w') as f:
                f.write(tc_id + '\n')
        b.say('toolchain: building fnvnx-a64 (toolchain/gcc/build.sh, ~45 min on 4 cores)')
        b.sh(['bash', os.path.join(ROOT, 'toolchain', 'gcc', 'build.sh'), dl, work, own_prefix,
              os.path.join(src, 'buildscripts')], log, check=True, timeout=6 * 3600)
        with open(os.path.join(own_prefix, '.fnvnx-build-id'), 'w') as f:
            f.write(tc_id + '\n')
        remove_work(b, r, [work], 'the fnvnx-a64 build tree')
        prefix = own_prefix
        r.note('fnvnx-a64 built in %s' % b.rel(own_prefix))
    if not dkp and built(own_dkp, dkp_id):
        dkp = own_dkp
    if not dkp and prefix and prefix != own_prefix:
        # setup.sh copies devkitPro's make rules into the prefix: never into one we were given
        r.note('FNVNX_TC given without DEVKITPRO: set DEVKITPRO (toolchain/devkita64/setup.sh), or give neither')
    elif not dkp and prefix and may_build:
        rules = os.path.join(src, 'devkita64-rules')
        if not os.path.isdir(rules):
            b.sh(['bash', os.path.join(ROOT, 'toolchain', 'sources.sh'), os.path.join(d, 'dl'), src], log, check=True,
                 timeout=3600)
        if not os.path.isdir(os.path.join(ext, 'libnx')):
            raise StageError('the DEVKITPRO tree needs the pinned libnx checkout in %s (tools/fetch.sh)' % ext)
        shutil.rmtree(own_dkp, ignore_errors=True)
        b.say('toolchain: DEVKITPRO tree + libnx (toolchain/devkita64/setup.sh)')
        b.sh(['bash', os.path.join(ROOT, 'toolchain', 'devkita64', 'setup.sh'), prefix, os.path.join(ext, 'libnx'),
              rules, own_dkp], log, check=True, timeout=3600)
        with open(os.path.join(own_dkp, '.fnvnx-build-id'), 'w') as f:
            f.write(dkp_id + '\n')
        dkp = own_dkp
    return prefix, dkp


def provide_vorbis(b, r, d, dkp):
    """libogg/libvorbis for the guest (platform/audio/vorbisfile.cpp): VORBIS=<prefix> when given, none: without;
    auto: toolchain/vorbis/build.sh into build/toolchain/vorbis (pinned git tags, a few files with fnvnx-a64)."""
    v = b.cfg['VORBIS']
    if v == 'none':
        return None
    if v and v != 'auto':
        return os.path.abspath(v)
    if not dkp:
        r.note('VORBIS=auto needs DEVKITPRO: the guest links without Ogg Vorbis')
        return None
    out, work = os.path.join(d, 'vorbis'), os.path.join(d, 'vorbis-work')
    vid = build_id('toolchain/vorbis', extra=[dkp])
    f = os.path.join(out, '.fnvnx-build-id')
    if read_id(f) == vid:
        return out
    shutil.rmtree(out, ignore_errors=True)
    b.say('toolchain: Ogg Vorbis for the guest (toolchain/vorbis/build.sh)')
    b.sh(['bash', os.path.join(ROOT, 'toolchain', 'vorbis', 'build.sh'), work, out], r['log'],
         env=b.env({'DEVKITPRO': dkp}), check=True, timeout=3600)
    with open(f, 'w') as fh:
        fh.write(vid + '\n')
    remove_work(b, r, [work], 'the Ogg Vorbis sources')
    return out


class Toolchain(Stage):
    name = 'toolchain'
    soft = ('fetch',)       # the pinned libnx for the DEVKITPRO tree it may build

    def fingerprint(self, b):
        # every tool the stage checks, by resolved path and (size, mtime): a reinstalled or removed package reruns it
        import importlib.util
        prefix = b.cfg['FNVNX_TC'] or (os.path.join(b.cfg['DEVKITPRO'], 'devkitA64') if b.cfg['DEVKITPRO'] else '')
        own = os.path.join(b.out, 'toolchain')
        keys = [os.path.join(prefix, 'bin', 'aarch64-none-elf-gcc'), os.path.join(prefix, 'bin', 'aarch64-none-elf-g++'),
                os.path.join(own, 'prefix', 'bin', 'aarch64-none-elf-gcc'), os.path.join(own, 'cross', '.setup-id')]
        if b.cfg['DEVKITPRO']:
            keys += [os.path.join(b.cfg['DEVKITPRO'], r_) for r_ in ('libnx/lib/libnx.a', 'libnx/switch.specs')]
        saved, b.cross_bin = b.cross_bin, None      # the host's own PATH (CROSS=auto decides from it)
        tools = [b.which(t) for t, _, _ in HOST_TOOLS] + [b.which('llvm-undname-18')]
        b.cross_bin = saved
        gc = b.cfg['GAME_CXX']
        return ['toolchain-v3', [b.cfg[k] for k in ('FNVNX_TC', 'DEVKITPRO', 'TC_DL', 'GAME_CXX', 'CROSS_BIN', 'CROSS',
                                                    'VORBIS', 'MIN_FREE_GB')],
                tree_hash('toolchain/cross', 'toolchain/sources.sh', 'toolchain/gcc', 'toolchain/devkita64',
                          'toolchain/vorbis'), up_id(b, 'fetch'),
                [stat_id(os.path.realpath(k)) for k in keys], [stat_id(os.path.realpath(t)) if t else None for t in tools],
                gc not in ('auto', 'tc', 'standin') and stat_id(b.which(shlex.split(gc)[0]) if gc else None),
                importlib.util.find_spec('unicorn') is not None, importlib.util.find_spec('elftools') is not None]

    def outputs_present(self, b):
        return os.path.isfile(os.path.join(b.out, 'toolchain', 'toolchain.env'))

    def run(self, b, r):
        d = b.sdir('toolchain')
        b.cross_bin = cross_bin = provide_cross(b, r, d)
        prefix, dkp = provide_fnvnx_a64(b, r, d)
        vorbis = provide_vorbis(b, r, d, dkp)
        checks = []   # (item, value, state)

        def chk(item, value, good, required=True):
            checks.append((item, value, 'ok' if good else ('MISSING' if required else 'missing')))
            return good

        cxx = None
        if prefix:
            prefix = os.path.realpath(prefix)
            gcc = os.path.join(prefix, 'bin', 'aarch64-none-elf-gcc')
            cxx = os.path.join(prefix, 'bin', 'aarch64-none-elf-g++')
            rc, out = b.capture([gcc, '--version'])
            ver = out.splitlines()[0] if not rc and out else None
            chk('fnvnx-a64 gcc', ver or gcc, bool(ver))
            r.metric('tc_gcc', ver or 'missing')
            rc, ml = b.capture([gcc, '-print-multi-lib'])
            chk('multilib ilp32', ' '.join(ml.split()), 'ilp32;' in ml)
            for lib in ('libc.a', 'libsysbase.a', 'libm.a', 'libgcc.a', 'libstdc++.a', 'libsupc++.a'):
                rc, p = b.capture([gcc, '-mabi=ilp32', '-print-file-name=' + lib])
                p = p.strip()
                chk('ilp32 ' + lib, p, os.path.isabs(p) and os.path.isfile(p))
        else:
            chk('fnvnx-a64 prefix', 'set FNVNX_TC or DEVKITPRO (TC_DL=none stops the stage from building it)', False)
        if dkp:
            dkp = os.path.realpath(dkp)
            for rel in ('libnx/lib/libnx.a', 'libnx/switch.specs', 'libnx/include/switch.h'):
                chk('DEVKITPRO ' + rel, os.path.join(dkp, rel), os.path.isfile(os.path.join(dkp, rel)))
            for t in ('elf2nso', 'npdmtool', 'build_pfs0', 'bin2s'):
                chk('DEVKITPRO tools/bin/' + t, '', os.access(os.path.join(dkp, 'tools', 'bin', t), os.X_OK),
                    required=False)
            da = os.path.join(dkp, 'devkitA64')
            if prefix and os.path.exists(da) and os.path.realpath(da) != prefix:
                r.note('DEVKITPRO/devkitA64 -> %s differs from FNVNX_TC %s (libnx was built with the former)'
                       % (os.path.realpath(da), prefix))
        else:
            chk('DEVKITPRO', 'set DEVKITPRO (toolchain/devkita64/setup.sh)', False)
        for tool, why, req in HOST_TOOLS:
            p = b.which(tool) or (b.which('llvm-undname-18') if tool == 'llvm-undname' else None)
            chk(tool, p or why, bool(p), required=req)
        rc, _ = b.capture([PY, '-I', '-c', 'import unicorn'])
        chk('python unicorn', 'tests/s5/emu_guest.py', rc == 0, required=False)

        # the compiler of the game units
        want = b.cfg['GAME_CXX']
        kind = want
        if want == 'auto':
            kind = 'tc' if cxx and os.path.isfile(cxx) else 'standin'
        if kind == 'tc':
            game_cxx = cxx
        elif kind == 'standin':
            game_cxx = b.which('aarch64-linux-gnu-g++')
        else:
            game_cxx = want
            kind = 'custom'
        rc, out = b.capture(shlex.split(game_cxx or 'false') + ['--version'])
        gver = out.splitlines()[0] if not rc and out else None
        chk('GAME_CXX (%s)' % kind, game_cxx or '-', bool(gver))
        r.metric('game_cxx', '%s: %s' % (kind, gver))
        if kind == 'standin':
            pinned, _ = cross_pin_matches(b)
            r.metric('cross_pinned', int(pinned))
            if not pinned:
                r.note('the standin compiler is not the pinned Ubuntu build (toolchain/cross/packages.tsv): objects '
                       'may differ from other machines\' (CROSS=auto unpacks the pinned one)')
                r['status'] = 'warn'
        r.metric('vorbis', 1 if vorbis else 0)

        free = free_bytes(b.out)
        r.metric('disk_free_gib', round(free / 2**30, 1))
        if free < float(b.cfg['MIN_FREE_GB']) * 2**30:
            r.note('only %s free on the build disk (MIN_FREE_GB=%s); a cold build with NINE=1 and all tests needs '
                   'about 8 GiB (make clean-work frees the re-creatable work trees)' % (human(free), b.cfg['MIN_FREE_GB']))
            r['status'] = 'warn'
        with open(os.path.join(d, 'tools.tsv'), 'w') as f:
            f.write('# item\tvalue\tstate\n')
            for c in checks:
                f.write('%s\t%s\t%s\n' % c)
        with open(os.path.join(d, 'toolchain.env'), 'w') as f:
            f.write('# generated by tools/pipeline/build.py\n')
            for k, v in (('FNVNX_TC', prefix), ('DEVKITPRO', dkp), ('GAME_CXX', game_cxx)):
                f.write('%s=%s\n' % (k, shlex.quote(v or '')))
        r.artifact('prefix', prefix)
        r.artifact('devkitpro', dkp)
        r.artifact('cross_bin', cross_bin)
        r.artifact('vorbis', vorbis)
        r.artifact('game_cxx', game_cxx)
        r.artifact('game_cxx_kind', kind)
        r['game_cxx_id'] = sha(game_cxx, gver)
        r['output_id'] = sha(prefix, dkp, game_cxx, gver, [c for c in checks], vorbis)
        missing_req = [c[0] for c in checks if c[2] == 'MISSING']
        missing_opt = [c[0] for c in checks if c[2] == 'missing']
        r.metric('tc_missing_required', len(missing_req))
        r.metric('tc_missing_optional', len(missing_opt))
        r['summary'] = 'prefix %s, DEVKITPRO %s, GAME_CXX %s, cross tools: %s, vorbis %s' % (
            prefix or '-', dkp or '-', kind, r.get('cross', '-'), b.rel(vorbis) or 'none')
        if missing_opt:
            r.note('optional tools missing: ' + ', '.join(missing_opt))
        if missing_req:
            r.note('required items missing: ' + ', '.join(missing_req))
            r['status'] = 'warn' if gver else 'fail'
            if not gver:
                raise StageError('no usable game compiler; missing: ' + ', '.join(missing_req))


def game_cxx(b):
    return b.art('toolchain', 'game_cxx') or b.which('aarch64-linux-gnu-g++') or 'aarch64-linux-gnu-g++'


def game_cxx_id(b):
    return b.rec('toolchain').get('game_cxx_id') or sha(game_cxx(b))


# --------------------------------------------------------------------------------------------------- 3 prepare

def sync_tree(src, dst):
    """Move src's files over dst, keeping dst's file (and its mtime) where the content is identical, so the
    compile step's mtime rules only see real changes. Returns (content id, added, changed, removed, same)."""
    h = hashlib.sha256()
    added, changed, removed, same = [], [], [], 0
    seen = set()
    for dp, dn, fn in os.walk(src):
        dn.sort()
        rel = os.path.relpath(dp, src)
        os.makedirs(os.path.join(dst, rel), exist_ok=True)
        for n in sorted(fn):
            r = os.path.normpath(os.path.join(rel, n))
            seen.add(r)
            s, d = os.path.join(src, r), os.path.join(dst, r)
            with open(s, 'rb') as f:
                data = f.read()
            h.update(r.encode('utf-8', 'surrogateescape') + b'\0' + hashlib.sha1(data).digest())
            if os.path.isfile(d) and os.path.getsize(d) == len(data):
                with open(d, 'rb') as f:
                    if f.read() == data:
                        same += 1
                        continue
            (changed if os.path.exists(d) else added).append(r)
            os.replace(s, d)
    for dp, dn, fn in os.walk(dst, topdown=False):
        rel = os.path.relpath(dp, dst)
        for n in fn:
            r = os.path.normpath(os.path.join(rel, n))
            if r not in seen:
                os.remove(os.path.join(dp, n))
                removed.append(r)
        if rel != '.' and not os.listdir(dp):
            os.rmdir(dp)
    shutil.rmtree(src, ignore_errors=True)
    return h.hexdigest()[:16], added, changed, removed, same


def rules_hash():
    return tree_hash(*PREPARE_INPUTS)


def tree_content_id(tree):
    """Content id of a prepared tree, the same value sync_tree() gives (paths + SHA-1 of every file)."""
    h = hashlib.sha256()
    for dp, dn, fn in os.walk(tree):
        dn.sort()
        rel = os.path.relpath(dp, tree)
        for n in sorted(fn):
            r = os.path.normpath(os.path.join(rel, n))
            with open(os.path.join(tree, r), 'rb') as f:
                h.update(r.encode('utf-8', 'surrogateescape') + b'\0' + hashlib.sha1(f.read()).digest())
    return h.hexdigest()[:16]


def read_rule_stats(path):
    rules = {}
    if os.path.isfile(path):
        for line in open(path):
            p = line.rstrip('\n').split('\t')
            if len(p) >= 3:
                try:
                    rules[p[0]] = (int(p[1].split()[0]), int(p[2].split()[0]))
                except (ValueError, IndexError):
                    pass
    return rules


def write_prepared_from(d, pf):
    """build/prepare/prepared_from.txt (the commit on the first line, like S2c's driver writes next to its tree) and
    prepared_from.json (everything)."""
    with open(os.path.join(d, 'prepared_from.txt'), 'w') as f:
        f.write('%s\n' % (pf.get('port_commit') or 'unknown'))
        f.write('decomp %s\n' % (pf.get('decomp_commit') or 'unknown'))
        f.write('mode %s %s\n' % (pf.get('mode'), pf.get('tree')))
        for line in pf.get('port_dirty') or []:
            f.write('uncommitted %s\n' % line)
    with open(os.path.join(d, 'prepared_from.json'), 'w') as f:
        json.dump(pf, f, indent=1, sort_keys=True)


def recon_id():
    """the recon directory REC1 reads (FNVNX_RECON, D20: tools/farm/recon.sh fills it from fnv-nx-build recon/*) as a
    content hash: part of prepare's key, so a new or changed recon prepares again; '' without one"""
    top = os.environ.get('FNVNX_RECON')
    if not top or not os.path.isdir(top):
        return ''
    h = hashlib.sha256()
    for dp, dns, fns in sorted(os.walk(top)):
        dns.sort()
        for f in sorted(fns):
            p = os.path.join(dp, f)
            h.update(os.path.relpath(p, top).encode() + b'\0')
            with open(p, 'rb') as fh:
                h.update(hashlib.sha256(fh.read()).digest())
    return h.hexdigest()[:16]


class Prepare(Stage):
    name = 'prepare'
    deps = ('fetch',)

    def fingerprint(self, b):
        if b.cfg['PREPARED']:
            t = os.path.abspath(b.cfg['PREPARED'])
            uj = os.path.join(t, 'build', 'units.json')
            st = os.stat(uj) if os.path.isfile(uj) else None
            # the tree's content too (T1: a tree synced in place under the same prepared_from record kept every stage
            # after it cached, with objects from the old content); about a second for the whole decomp
            return ['prepare-adopt-v2', t, st and (st.st_size, st.st_mtime_ns), read_prepared_from(t),
                    tree_content_id(t) if st else None]
        # the head is part of the key: a tree is always prepared from the commit the run's tools come from (J-0054);
        # an unchanged tree keeps its files' mtimes (sync_tree), so the stages after it stay cached
        return ['prepare-v3', rules_hash(), up_id(b, 'fetch'), os.environ.get('FNVNX_PREPARE_SKIP', ''), b.head,
                recon_id()]

    def outputs_present(self, b):
        t = os.path.abspath(b.cfg['PREPARED']) if b.cfg['PREPARED'] else os.path.join(b.out, 'prepare', 'tree')
        return os.path.isfile(os.path.join(t, 'build', 'units.json'))

    def run(self, b, r):
        d = b.sdir('prepare')
        decomp = b.art('fetch', 'decomp')
        if b.cfg['PREPARED']:
            return self.adopt(b, r, d, os.path.abspath(b.cfg['PREPARED']))
        tree, staging = os.path.join(d, 'tree'), os.path.join(d, 'staging')
        shutil.rmtree(staging, ignore_errors=True)
        dirty = port_dirty(PREPARE_INPUTS)
        pf = {'port_commit': b.head, 'port_dirty': dirty, 'decomp_commit': read_git_head(decomp), 'decomp': decomp,
              'recon': recon_id(), 'mode': 'prepare.py', 'tree': tree, 'at': now_iso()}
        # T1: prepare.py runs under tools/pipeline/traced.py, which records every file it read and every directory it
        # listed (its imports too). When all of them are unchanged and the tree is the one that run made, the tree is
        # what prepare.py would make at this head: it is kept, provenance names this head (572 s of every full build,
        # tree unchanged). PREPARE_REUSE=0 always runs prepare.py.
        script = os.path.join(ROOT, 'tools', 'prepare.py')
        deps = os.path.join(d, 'prepare.deps.json')
        argv = [script, decomp, staging]
        if os.environ.get('PREPARE_REUSE', '1') != '0' and not dirty and \
                os.path.isfile(os.path.join(tree, 'build', 'units.json')):
            if HERE not in sys.path:
                sys.path.insert(0, HERE)
            import traced
            try:
                rec = json.load(open(deps))
            except (OSError, ValueError):
                rec = {}
            rec_tree = rec.get('tree_id')
            t0 = time.time()
            # what the trace cannot see: prepare.py's subprocesses (s1_compile's `git rev-parse HEAD` of the decomp, for
            # the recon checks) - the decomp's head and the recon id are compared as well
            if rec_tree and rec.get('decomp_head') == pf['decomp_commit'] and rec.get('recon') == pf['recon'] and \
                    traced.check(deps, argv) and tree_content_id(tree) == rec_tree:
                prev = read_prepared_from(tree).get('port_commit')
                pf.update(mode='prepare.py (reused: inputs identical)', reused_from=prev)
                write_prepared_from(d, pf)
                rules = read_rule_stats(os.path.join(d, 'prepare_stats.tsv'))
                units = json.load(open(os.path.join(tree, 'build', 'units.json')))['units']
                ndeps = len(json.load(open(deps)).get('files', {}))
                r.metric('prepare_rules', len(rules))
                r.metric('prepare_edits', sum(e for _, e in rules.values()))
                r.metric('prepare_units', len({u.get('source') for u in units}))
                r.metric('prepare_files_changed', 0)
                r.metric('prepare_from', short(b.head))
                r.metric('prepare_reused', 1)
                r.metric('prepare_check_s', round(time.time() - t0, 1))
                r['rules'] = rules
                r['prepared_from'] = pf
                r.artifact('tree', tree)
                r.artifact('prepared_from', os.path.join(d, 'prepared_from.txt'))
                r['output_id'] = rec_tree
                r['summary'] = 'from %s: the tree of %s kept (every input prepare.py read is unchanged: %d files); ' \
                               '%d rules, %d edits' % (short(b.head), short(prev), ndeps, len(rules),
                                                      sum(e for _, e in rules.values()))
                return
        b.sh([PY, '-I', os.path.join(HERE, 'traced.py'), deps, '--exclude', staging, '--'] + argv, r['log'],
             check=True)
        r.metric('prepare_reused', 0)
        stats = os.path.join(staging, 'prepare_stats.tsv')
        rules = read_rule_stats(stats)
        if os.path.isfile(stats):
            shutil.copyfile(stats, os.path.join(d, 'prepare_stats.tsv'))
        os.makedirs(tree, exist_ok=True)
        cid, added, changed, removed, same = sync_tree(staging, tree)
        with open(os.path.join(d, 'changed.txt'), 'w', errors='surrogateescape') as f:
            for tag, lst in (('A', added), ('M', changed), ('D', removed)):
                for p in lst:
                    f.write('%s\t%s\n' % (tag, p))
        write_prepared_from(d, pf)
        try:   # the tree this record made: a later run keeps it only while it is still that tree
            dj = json.load(open(deps)); dj.update(tree_id=cid, decomp_head=pf['decomp_commit'], recon=pf['recon'])
            json.dump(dj, open(deps + '.tmp', 'w')); os.replace(deps + '.tmp', deps)
        except (OSError, ValueError):
            pass
        units = json.load(open(os.path.join(tree, 'build', 'units.json')))['units']
        r.metric('prepare_rules', len(rules))
        r.metric('prepare_edits', sum(e for _, e in rules.values()))
        r.metric('prepare_units', len({u.get('source') for u in units}))
        r.metric('prepare_files_changed', len(added) + len(changed) + len(removed))
        r.metric('prepare_from', short(b.head))
        r['rules'] = rules
        r['prepared_from'] = pf
        r.artifact('tree', tree)
        r.artifact('prepared_from', os.path.join(d, 'prepared_from.txt'))
        r['output_id'] = cid
        r['summary'] = 'from %s: %d rules, %d edits; tree: %d added, %d changed, %d removed, %d unchanged' % (
            short(b.head), len(rules), sum(e for _, e in rules.values()), len(added), len(changed), len(removed),
            same)
        if not b.head:
            r['status'] = 'warn'
            r.note('the port commit is unknown (no git metadata; set PORT_COMMIT for a snapshot copy): later stages '
                   'refuse this tree unless FORCE_STALE=1')
        if dirty:
            r['status'] = 'warn'
            r.note('prepared with uncommitted changes to its inputs (%d paths: %s): %s does not describe this tree '
                   'alone' % (len(dirty), ', '.join(l[3:] for l in dirty[:5]), short(b.head)))

    def adopt(self, b, r, d, tree):
        """PREPARED=<tree>: use a tree another run prepared (read only), with the provenance it states."""
        uj = os.path.join(tree, 'build', 'units.json')
        if not os.path.isfile(uj):
            raise StageError('PREPARED=%s is not a prepared tree (no build/units.json)' % tree)
        pf = dict(read_prepared_from(tree))
        pf.update(mode='adopted', tree=tree, adopted_at=now_iso())
        write_prepared_from(d, pf)
        units = json.load(open(uj))['units']
        rules = read_rule_stats(os.path.join(tree, 'prepare_stats.tsv'))
        if rules:
            shutil.copyfile(os.path.join(tree, 'prepare_stats.tsv'), os.path.join(d, 'prepare_stats.tsv'))
        r.metric('prepare_rules', len(rules))
        r.metric('prepare_edits', sum(e for _, e in rules.values()))
        r.metric('prepare_units', len({u.get('source') for u in units}))
        r.metric('prepare_from', short(pf.get('port_commit')))
        r['rules'] = rules
        r['prepared_from'] = pf
        r.artifact('tree', tree)
        r.artifact('prepared_from', os.path.join(d, 'prepared_from.txt'))
        b.say('prepare: hashing the adopted tree %s' % tree)
        r['output_id'] = tree_content_id(tree)
        r['summary'] = 'adopted %s, prepared from %s (%s)' % (tree, short(pf.get('port_commit')),
                                                             b.rel(pf.get('source')) or 'no prepared_from record')
        if pf.get('port_commit') != b.head:
            r['status'] = 'warn'
            r.note('the adopted tree was prepared from %s, the head is %s: later stages refuse it unless '
                   'FORCE_STALE=1' % (short(pf.get('port_commit')), short(b.head)))


# ---------------------------------------------------------------------------------------------------- 4 census

def selected_units(b, tree, r):
    """The units of the prepared tree this run handles: every unit, or those UNITS lists (the others are noted)."""
    allu = sorted({u['source'] for u in json.load(open(os.path.join(tree, 'build', 'units.json')))['units']})
    want, _ = b.units_filter()
    if want is None:
        return allu
    sel = [u for u in allu if u in want]
    miss = sorted(want - set(allu))
    r.metric('units_selected', len(sel))
    r.note('UNITS=%s: %d of %d units' % (b.cfg['UNITS'], len(sel), len(allu)) +
           ('; not in this tree: %s' % ', '.join(miss[:8]) if miss else ''))
    if not sel:
        raise StageError('UNITS=%s selects none of the tree\'s units' % b.cfg['UNITS'])
    return sel


class Census(Stage):
    name = 'census'
    deps = ('prepare',)
    soft = ('toolchain',)
    prov = ('prepare',)

    def enabled(self, b):
        return b.cfg['CENSUS'] != '0'

    def cache_key(self, b):
        return ['census-v1', tree_hash('tools/census.sh', 'shim/include'),
                script_flags(os.path.join(ROOT, 'tools', 'census.sh')), up_id(b, 'prepare'), game_cxx_id(b),
                b.rec('fetch').get('metrics', {}).get('pin_sse2neon')]

    def fingerprint(self, b):
        return self.cache_key(b) + [b.units_filter()[1]]

    def outputs_present(self, b):
        return os.path.isfile(os.path.join(b.out, 'census', 'summary.txt'))

    def run(self, b, r):
        d = b.sdir('census')
        shutil.rmtree(os.path.join(d, 'logs'), ignore_errors=True)
        r['flags'] = script_flags(os.path.join(ROOT, 'tools', 'census.sh'))
        # checkpoint: every unit's result is kept in cache/ under this stage's fingerprint, so a census that was
        # interrupted (container restart) resumes where it stopped instead of recompiling 2,978 units
        cache = os.path.join(d, 'cache')
        fp = sha(self.cache_key(b))      # without UNITS: a different subset keeps the results of the same tree
        fpf = os.path.join(cache, '.fingerprint')
        if not os.path.isfile(fpf) or open(fpf).read().strip() != fp:
            shutil.rmtree(cache, ignore_errors=True)
            os.makedirs(cache)
            open(fpf, 'w').write(fp + '\n')
        done = len([n for n in os.listdir(cache) if n.endswith('.done')])
        wrapper = os.path.join(d, 'cxx-cached')
        with open(wrapper, 'w') as f:
            f.write('#!/bin/sh\n# generated by tools/pipeline/build.py: census result cache (resumable census)\n'
                    'for a; do src=$a; done\n'
                    'c=%s/$(printf "%%s" "$src" | tr / _)\n'
                    'if [ -f "$c.done" ]; then cat "$c.log"; exit "$(cat "$c.done")"; fi\n'
                    '%s "$@" > "$c.log" 2>&1; rc=$?\n'
                    'cat "$c.log"; echo $rc > "$c.done"; exit $rc\n' % (
                        shlex.quote(cache), ' '.join(shlex.quote(x) for x in shlex.split(game_cxx(b)))))
        os.chmod(wrapper, 0o755)
        b.say('census: -fsyntax-only of %s (%s; %d unit results already in the census cache)' % (
            'the UNITS subset' if b.cfg['UNITS'] else 'every unit', os.path.basename(game_cxx(b)), done))
        r.metric('census_resumed_units', done)
        tree = b.art('prepare', 'tree')
        argv = ['bash', os.path.join(ROOT, 'tools', 'census.sh'), tree, d, b.jobs]
        if b.cfg['UNITS']:
            lst = os.path.join(d, 'units.selected')
            with open(lst, 'w', errors='surrogateescape') as f:
                f.write(''.join(u + '\n' for u in selected_units(b, tree, r)))
            argv.append(lst)
        b.sh(argv, r['log'], env=b.env({'CXX': wrapper}), check=True)
        lines = open(os.path.join(d, 'summary.txt')).read().splitlines()
        m = re.search(r'units:\s*(\d+)\s+clean:\s*(\d+)\s+failing:\s*(\d+)', lines[0] if lines else '')
        e = re.search(r'errors total:\s*(\d+)', lines[1] if len(lines) > 1 else '')
        if not m:
            raise StageError('cannot read census summary.txt')
        units, clean, failing = map(int, m.groups())
        r.metric('census_units', units)
        r.metric('census_clean', clean)
        r.metric('census_failing', failing)
        r.metric('census_errors', int(e.group(1)) if e else None)
        kinds = []
        sect = False
        for line in lines:
            if line.startswith('== error kinds'):
                sect = True
                continue
            if sect:
                if not line.strip():
                    break
                kinds.append(line.strip())
        r['top_errors'] = kinds[:12]
        r.artifact('summary', os.path.join(d, 'summary.txt'))
        r.artifact('units', os.path.join(d, 'units.tsv'))
        r['output_id'] = file_hash(os.path.join(d, 'units.tsv'))
        r['summary'] = '%d units, %d clean, %d failing, %s errors' % (units, clean, failing,
                                                                      e.group(1) if e else '?')
        if failing:
            r['status'] = 'warn'


# --------------------------------------------------------------------------------------------------- 5 compile

def parse_depfile(path):
    try:
        text = open(path, errors='surrogateescape').read()
    except OSError:
        return None
    text = text.replace('\\\n', ' ')
    i = text.find(': ')
    if i < 0:
        return []
    body = text[i + 2:]
    deps, cur, k = [], [], 0
    while k < len(body):
        c = body[k]
        if c == '\\' and k + 1 < len(body) and body[k + 1] == ' ':
            cur.append(' ')
            k += 2
            continue
        if c.isspace():
            if cur:
                deps.append(''.join(cur))
                cur = []
            if c == '\n':   # only the first rule matters (-MP adds phony targets after it)
                rest = body[k:].lstrip()
                if rest and ':' in rest.split('\n', 1)[0]:
                    break
        else:
            cur.append(c)
        k += 1
    if cur:
        deps.append(''.join(cur))
    return deps


def script_flags(path):
    """The FLAGS=( ... ) array of census.sh / compile.sh as text: part of the cache keys, so a flag change
    (e.g. J-0034's -fsigned-char ...) rebuilds every object even if the rest of the script stayed the same."""
    try:
        m = re.search(r'^FLAGS=\((.*?)\)\s*$', open(path).read(), re.S | re.M)
    except OSError:
        return None
    return ' '.join(m.group(1).split()) if m else None


def unit_base(objdir, src):
    return os.path.join(objdir, src.replace('/', '_'))


class Compile(Stage):
    name = 'compile'
    deps = ('prepare',)
    soft = ('toolchain',)
    prov = ('prepare',)

    def fingerprint(self, b):
        if b.cfg['OBJECTS']:
            src = os.path.abspath(b.cfg['OBJECTS'])
            return ['compile-adopt-v1', src, file_hash(src), up_id(b, 'prepare'), b.units_filter()[1]]
        return ['compile-v3', file_hash(os.path.join(ROOT, 'tools', 'link', 'compile.sh')),
                script_flags(os.path.join(ROOT, 'tools', 'link', 'compile.sh')), up_id(b, 'prepare'),
                game_cxx_id(b), b.cfg['OPT'], tree_hash('shim/include'),
                b.rec('fetch').get('metrics', {}).get('pin_sse2neon'), b.units_filter()[1]]

    def outputs_present(self, b):
        p = os.path.join(b.out, 'compile', 'objects.tsv')
        return os.path.isfile(p) and all(o is None or os.path.isfile(o) for o in objects_map(p).values())

    def adopt(self, b, r, d, tree, src):
        """OBJECTS=<objects.tsv>: objects another job compiled from this tree (the compile farms:
        tools/farm/fetch_objs.sh checks they were compiled from exactly this tree), read in place."""
        given = objects_map(src)
        units = selected_units(b, tree, r)
        ok, failed = [], []
        with open(os.path.join(d, 'objects.tsv'), 'w', errors='surrogateescape') as f:
            for u in units:
                o = given.get(u)
                st = 'ok' if o and os.path.isfile(o) and object_intact(o) else 'fail'
                (ok if st == 'ok' else failed).append(u)
                f.write('%s\t%s\t%s\n' % (u, o or unit_base(os.path.dirname(src), u) + '.o', st))
        with open(os.path.join(d, 'failed.tsv'), 'w', errors='surrogateescape') as f:
            f.write('# source\terrors\tfirst error\n')
            for u in failed:
                log = os.path.join(os.path.dirname(src), u.replace('/', '_') + '.log')
                errs = [l for l in open(log, errors='replace')] if os.path.isfile(log) else []
                errs = [l for l in errs if ': error:' in l or 'fatal error:' in l]
                first = re.sub(r'^[^:]+:\d+:\d+:\s*(fatal )?error:\s*', '', errs[0].strip())[:200] if errs else \
                    ('(not in %s)' % b.rel(src) if u not in given else '(no error line)')
                f.write('%s\t%d\t%s\n' % (u, len(errs), first))
        r['adopted'] = src
        r.metric('compile_units', len(units))
        r.metric('compile_ok', len(ok))
        r.metric('compile_failed', len(failed))
        r.metric('compile_built_now', 0)
        r.metric('compile_obj_mib', round(sum(os.path.getsize(given[u]) for u in ok) / 2**20, 1))
        r.artifact('objects', os.path.join(d, 'objects.tsv'))
        r.artifact('failed', os.path.join(d, 'failed.tsv'))
        r['output_id'] = sha([(u, file_hash(given[u])) for u in ok])
        r['summary'] = '%d/%d units -> objects adopted from %s, %d failed (listed, excluded)' % (
            len(ok), len(units), b.rel(src), len(failed))
        if not ok:
            raise StageError('no object in %s' % src)
        if failed:
            r['status'] = 'warn'

    def run(self, b, r):
        d = b.sdir('compile')
        tree = b.art('prepare', 'tree')
        if b.cfg['OBJECTS']:
            return self.adopt(b, r, d, tree, os.path.abspath(b.cfg['OBJECTS']))
        obj = os.path.join(d, 'obj')
        os.makedirs(obj, exist_ok=True)
        cxx = game_cxx(b)
        flags = script_flags(os.path.join(ROOT, 'tools', 'link', 'compile.sh'))
        r['flags'] = flags
        # the tree's path is part of it: objects are kept by mtime, and another tree (PREPARED) has its own mtimes
        toolset = sha(file_hash(os.path.join(ROOT, 'tools', 'link', 'compile.sh')), game_cxx_id(b), b.cfg['OPT'],
                      flags, os.path.abspath(tree), 'compile-v1')
        tsf = os.path.join(d, 'toolset.id')
        if not os.path.isfile(tsf) or open(tsf).read().strip() != toolset:
            if os.listdir(obj):
                r.note('compiler, flags, compile.sh or the tree changed: all objects rebuilt')
            shutil.rmtree(obj)
            os.makedirs(obj)
            open(tsf, 'w').write(toolset + '\n')
        units = selected_units(b, tree, r)
        # objects a container restart left truncated or zero-filled are compiled again (S2c's objcheck)
        broken = [s_ for s_ in units if os.path.isfile(unit_base(obj, s_) + '.o')
                  and not object_intact(unit_base(obj, s_) + '.o')]
        for s_ in broken:
            os.remove(unit_base(obj, s_) + '.o')
        if broken:
            r.note('%d damaged objects removed and rebuilt: %s' % (len(broken), ', '.join(broken[:5])))
        r.metric('compile_damaged_objects', len(broken))
        # objects whose headers changed (dependency files from -MD; compile.sh itself checks the source only)
        mt = {}

        def mtime(p):
            if p not in mt:
                try:
                    mt[p] = os.stat(p if os.path.isabs(p) else os.path.join(tree, p)).st_mtime_ns
                except OSError:
                    mt[p] = None
            return mt[p]

        # T1 (links 5ae3f0b13834, ef092d51de92 kept objects from before S1's RAWVT1-MEM): an object is kept only when its
        # stamp <unit>.o.hash, written after its compile, still matches the content of its prepared source and of every
        # dependency its .o.d names. A missing stamp, a changed byte in any of them: compiled again. Mtimes alone decide
        # nothing any more (a copy, a cache hit or a clock can make an old object look new).
        fh_ = {}

        def fhash(p):
            if p not in fh_:
                try:
                    with open(p if os.path.isabs(p) else os.path.join(tree, p), 'rb') as f_:
                        fh_[p] = hashlib.sha256(f_.read()).hexdigest()
                except OSError:
                    fh_[p] = 'missing'
            return fh_[p]

        def input_stamp(src, deps):
            h_ = hashlib.sha256()
            for p_ in [src] + sorted(set(deps or ())):
                h_.update(('%s\0%s\n' % (p_, fhash(p_))).encode('utf-8', 'surrogateescape'))
            return '%s\n%s\n' % (fhash(src), h_.hexdigest())

        def stamp_ok(base, src, deps):
            try:
                return open(base + '.o.hash').read() == input_stamp(src, deps)
            except OSError:
                return False

        # failed units are retried when one of their dependencies (from the .d GCC still writes when the error
        # comes after preprocessing) is newer than their log, when they have no .d, or when files were added to or
        # removed from the tree (a missing header may have appeared)
        listing = sha(sorted(os.path.relpath(os.path.join(dp, n), tree) for dp, _, fn in os.walk(tree) for n in fn))
        lf = os.path.join(d, 'tree_files.id')
        files_changed = not os.path.isfile(lf) or open(lf).read().strip() != listing
        stale = kept = known_fail = 0
        rebuild = []
        for src in units:
            base = unit_base(obj, src)
            o = base + '.o'
            if os.path.exists(base + '.o.tmp'):     # interrupted mid-compile (container restart)
                os.remove(base + '.o.tmp')
                open(base + '.o.tmp.interrupted', 'w').close()
            if os.path.isfile(o):
                deps = parse_depfile(base + '.o.d')
                ot = os.stat(o).st_mtime_ns
                # no dependency file: not known to be up to date, so compiled again (T1: the unit cache's objects had
                # none, so every one looked current and link/ef092d51de92 kept objects from before S1's RAWVT1-MEM)
                if deps is None or any((mtime(p) is None or mtime(p) > ot) for p in deps) or not stamp_ok(base, src, deps):
                    os.remove(o)
                    stale += 1
                else:
                    kept += 1
                    continue
            elif not files_changed and os.path.isfile(base + '.log') and \
                    not os.path.exists(base + '.o.tmp.interrupted'):
                deps = parse_depfile(base + '.o.d')
                lt = os.stat(base + '.log').st_mtime_ns
                with open(base + '.log', errors='replace') as lf_:
                    real_error = any(': error:' in l or 'fatal error:' in l for l in lf_)
                if real_error and deps and all(mtime(p) is not None and mtime(p) <= lt for p in deps + [src]):
                    known_fail += 1
                    continue
            rebuild.append(src)
        open(lf, 'w').write(listing + '\n')
        for src in rebuild:
            if os.path.exists(unit_base(obj, src) + '.o.tmp.interrupted'):
                os.remove(unit_base(obj, src) + '.o.tmp.interrupted')
        lst = os.path.join(d, 'rebuild.list')
        with open(lst, 'w', errors='surrogateescape') as f:
            f.write(''.join(s + '\n' for s in rebuild))
        t0 = time.time()
        if rebuild:
            b.say('compile: %d units to build (%d up to date, %d stale by header, %d failures with unchanged '
                  'inputs not retried)' % (len(rebuild), kept, stale, known_fail))
            b.sh(['bash', os.path.join(ROOT, 'tools', 'link', 'compile.sh'), tree, obj, lst, b.jobs], r['log'],
                 env=b.env({'CXX': cxx + ' -MD', 'OPT': b.cfg['OPT']}), check=True)
        # the stamps of what this run compiled: the source's and every dependency's content (see stamp_ok above)
        fh_.clear()
        for src in rebuild:
            base = unit_base(obj, src)
            if os.path.isfile(base + '.o'):
                with open(base + '.o.hash', 'w') as f_:
                    f_.write(input_stamp(src, parse_depfile(base + '.o.d') or []))
            else:
                try:
                    os.remove(base + '.o.hash')
                except OSError:
                    pass
        ok, failed = [], []
        with open(os.path.join(d, 'objects.tsv'), 'w', errors='surrogateescape') as f:
            for src in units:
                o = unit_base(obj, src) + '.o'
                st = 'ok' if os.path.isfile(o) else 'fail'
                (ok if st == 'ok' else failed).append(src)
                f.write('%s\t%s\t%s\n' % (src, o, st))
        by_dir = {}
        with open(os.path.join(d, 'failed.tsv'), 'w', errors='surrogateescape') as f:
            f.write('# source\terrors\tfirst error\n')
            for src in failed:
                log = unit_base(obj, src) + '.log'
                n, first = 0, ''
                try:
                    for line in open(log, errors='replace'):
                        if ': error:' in line or 'fatal error:' in line:
                            n += 1
                            if not first:
                                first = re.sub(r'^[^:]+:\d+:\d+:\s*(fatal )?error:\s*', '', line.strip())[:200]
                except OSError:
                    first = '(no log)'
                if not n and not first:
                    first = (Build.tail(log, 1) or ['(no error line)'])[0][:200]
                f.write('%s\t%d\t%s\n' % (src, n, first))
                top = '/'.join(src.split('/')[:2])
                by_dir[top] = by_dir.get(top, 0) + 1
        r['failed_by_dir'] = sorted(by_dir.items(), key=lambda x: -x[1])[:15]
        r.metric('compile_units', len(units))
        r.metric('compile_ok', len(ok))
        r.metric('compile_failed', len(failed))
        r.metric('compile_built_now', len(rebuild))
        r.metric('compile_reused', kept)
        r.metric('compile_failures_not_retried', known_fail)
        r.metric('compile_obj_mib', round(sum(os.path.getsize(unit_base(obj, s) + '.o') for s in ok) / 2**20, 1))
        r.artifact('objects', os.path.join(d, 'objects.tsv'))
        r.artifact('failed', os.path.join(d, 'failed.tsv'))
        r['output_id'] = listing_id([unit_base(obj, s) + '.o' for s in ok])
        r['summary'] = '%d/%d units -> objects, %d failed (listed, excluded); built now %d in %.0f s' % (
            len(ok), len(units), len(failed), len(rebuild), time.time() - t0)
        if not ok:
            raise StageError('no unit compiled')
        if failed:
            r['status'] = 'warn'


# ------------------------------------------------------------------------------------------------------ 6 link

def parse_stats(path):
    """Numbers of fnvlink resolve's stats.txt."""
    m = {}
    try:
        text = open(path).read()
    except OSError:
        return m, {}
    for key, rx in (('link_objects', r'objects:\s*(\d+)'), ('link_refs', r'undefined references \(object, symbol\):\s*(\d+)'),
                    ('link_resolved', r'resolved by address:\s*(\d+)'), ('link_bridges', r'bridges generated:\s*(\d+)'),
                    ('link_layouts', r'aggregate layouts known:\s*(\d+)'),
                    ('link_dup_localized', r'duplicate strong names localized:\s*(\d+)'),
                    ('link_native_vars', r'variables defined natively at a data-image address:\s*(\d+)')):
        mm = re.search(rx, text)
        if mm:
            m[key] = int(mm.group(1))
    am = re.search(r'address map entries by kind:\s*(.*)', text)
    if am:
        for k, v in re.findall(r'([A-Z])=(\d+)', am.group(1)):
            m['link_addrmap_' + k] = int(v)
    classes = {}
    block = text.split('\n\n')
    if len(block) > 1:
        for line in block[1].splitlines():
            mm = re.match(r'^(\S+)\s+(\d+)$', line.strip())
            if mm:
                classes[mm.group(1)] = int(mm.group(2))
    sect = text.split('ABI verdicts (kind, classes) over references:', 1)
    for line in (sect[1].split('\n\n', 1)[0].splitlines() if len(sect) > 1 else []):
        mm = re.match(r'^\s*(\d+)\s+(x86-inconsistent|manual|bridge)\s', line)
        if mm:
            k = 'link_abi_' + mm.group(2).replace('-', '_')
            m[k] = m.get(k, 0) + int(mm.group(1))
    return m, classes


def parse_check(text):
    m, classes = {}, {}
    mm = re.search(r'trial link: (\d+) objects; undefined names: (\d+); multiple definitions: (\d+)', text)
    if mm:
        m['link_check_objects'], m['link_check_undefined'], m['link_check_multidef'] = map(int, mm.groups())
    for line in text.splitlines():
        mm = re.match(r'^\s{2}(.+?)\s{2,}(\d+)$', line)
        if mm:
            classes[mm.group(1).strip()] = int(mm.group(2))
    return m, classes


def probe_cxx(b, d):
    """fnvlink probe/vtables compile with the one executable in $CXX plus layout.flags() (which carries
    compile.sh's J-0034 semantics flags since S2 aligned it): the game compiler, through a wrapper only when
    GAME_CXX is a command with arguments."""
    cxx = shlex.split(game_cxx(b))
    if len(cxx) == 1:
        return cxx[0]
    w = os.path.join(d, 'cxx-probe')
    body = '#!/bin/sh\n# generated by tools/pipeline/build.py: GAME_CXX for fnvlink probe/vtables\nexec %s "$@"\n' % (
        ' '.join(shlex.quote(x) for x in cxx))
    if not os.path.isfile(w) or open(w).read() != body:
        with open(w, 'w') as f:
            f.write(body)
        os.chmod(w, 0o755)
    return w


def link_tool_inputs(b):
    """What fnvlink reads besides its arguments: its own code, S4's provider tables, S1b's renames (default
    --units-renames), the shim and sse2neon (layout probes and vtable synthesis compile against them)."""
    rn = b.cfg['UNITS_RENAMES']
    return [tree_hash('tools/link', 'platform/crt/crt_symbols.tsv', 'platform/win32/imports.tsv',
                      'platform/win32/exports.cpp', 'shim/include', 'tools/pipeline/check_rebind.py'),
            file_hash(os.path.join(ROOT, 'patches', 'units_renames.tsv')) if not rn else
            (rn if rn == 'none' else file_hash(os.path.abspath(rn))),
            b.rec('fetch').get('metrics', {}).get('pin_sse2neon')]


def override_id(b):
    """OVERRIDE in the Link fingerprint: auto and off are values (their sources are in link_tool_inputs), a table is
    its content (V1 OVR-F1: file_hash of `auto` and `none` was None for both, so switching never relinked)."""
    v = b.cfg['OVERRIDE']
    if v in ('auto', 'none', ''):
        return 'override:' + (v or 'none')
    return file_hash(os.path.abspath(v))


def extern_text_id(b):
    et = b.cfg['EXTERN_TEXT']
    if et == 'auto':
        return 'auto:' + (file_hash(os.path.join(HERE, 'extern_text.py')) or '')
    return et if et in ('', 'none') else file_hash(os.path.abspath(et))


def msvc_objs_id(b, key='MSVC_OBJS'):
    d = b.cfg[key]
    if not d:
        return ''
    d = os.path.abspath(d)
    return sha(d, listing_id([os.path.join(d, n) for n in os.listdir(d)]) if os.path.isdir(d) else None)


def emu_slicing(b):
    """emu_guest.py's scheduling arguments for the boot stage. With an emu_guest.py that preempts at host calls
    (S5 620572bc, --hc-slice: deterministic, and about 10x faster than an instruction count, which makes unicorn
    hook every instruction): --budget 0 and emu_guest.py's own defaults; no timer of ours (its --slice-ms is now a
    60 s fallback; EMU_SLICE_MS passes one only when set). Without it, the old instruction count 6e9: the timer
    slices of 2d1afaeb..6da559aa were nondeterministic (emu_stop from the timer thread can leave the PC at the block
    start, a resume re-runs instructions), and budget 0 on an older emu_guest.py means no preemption at all.
    EMU_BUDGET other than auto is taken as given."""
    emu = os.path.join(ROOT, 'tests', 's5', 'emu_guest.py')
    text = open(emu, errors='replace').read() if os.path.isfile(emu) else ''
    hc = '--hc-slice' in text
    bud = b.cfg.get('EMU_BUDGET', 'auto')
    if bud == 'auto':
        bud = '0' if hc else '6000000000'
    sl = b.cfg.get('EMU_SLICE_MS') or ''
    return ['--budget', bud] + (['--slice-ms', sl] if sl and '--slice-ms' in text else [])


def link_exe(b):
    """The player's FalloutNV.exe for the link (S2c's resolve --exe: image vftables paired by its RTTI class, slot
    fill; S2d's bodies --exe exe-addr): FNVNX_EXE, else a BOOT_IMAGE that is a file; '' when there is none.
    Agent-only data (D3): it is read, never copied into the build's outputs."""
    exe = os.environ.get('FNVNX_EXE') or (b.cfg['BOOT_IMAGE'] if os.path.isfile(b.cfg['BOOT_IMAGE']) else '')
    return os.path.abspath(exe) if exe and os.path.isfile(exe) else ''


RENDER_LIB_SOURCES = ('render/native/front', 'render/native/capture', 'render/native/n1', 'render/native/include',
                      'render/native/shaders', 'toolchain/include', 'toolchain/guest')


def link_overrides_mode(b):
    """LINK_OVERRIDES resolved: render or none. auto: render when the toolchain stage provides DEVKITPRO and
    fnvnx-a64 (the full build: its guest needs the native renderer, the boot stops at renderer init without it),
    none otherwise (TC_DL=none: the link farm, fuzz and equivalence take the pristine path)."""
    lo = b.cfg['LINK_OVERRIDES'] or 'none'
    if lo == 'auto':
        return 'render' if b.art('toolchain', 'devkitpro') and b.art('toolchain', 'prefix') else 'none'
    return lo


def link_overrides(b):
    """(table, archive, id for the fingerprint) of LINK_OVERRIDES: render -> S6n's render/native/overrides.tsv and
    libnxrender.a (RENDER_LIB, else '' = built by the stage from RENDER_LIB_SOURCES); empty -> ('', '', ...). They
    are added to OVERRIDE's rows (override_id)."""
    lo = link_overrides_mode(b)
    if lo == 'none':
        return ('', '', 'link-overrides:none')
    if lo != 'render':
        raise StageError('LINK_OVERRIDES=%s: only render (or empty) is known' % lo)
    tsv = os.path.join(ROOT, 'render', 'native', 'overrides.tsv')
    lib = os.path.abspath(b.cfg['RENDER_LIB']) if b.cfg['RENDER_LIB'] else ''
    return (tsv, lib, 'render:%s:%s' % (file_hash(tsv), file_hash(lib) if lib else
                                       'built:' + tree_hash(*RENDER_LIB_SOURCES)))


def build_render_lib(b, r, d):
    """libnxrender.a for LINK_OVERRIDES=render without RENDER_LIB: render/native/n1/build.sh <d>/render-n1 with the
    toolchain stage's DEVKITPRO and fnvnx-a64 and the environment's UAM (render/native/env.sh --print)."""
    dkp, prefix, uam = b.art('toolchain', 'devkitpro'), b.art('toolchain', 'prefix'), os.environ.get('UAM', '')
    if not dkp or not prefix:
        raise StageError('LINK_OVERRIDES=render needs RENDER_LIB=<libnxrender.a>, or the toolchain stage\'s DEVKITPRO '
                         'and fnvnx-a64 to build it (LINK_OVERRIDES=none: the pristine path)')
    if not uam or not os.access(uam, os.X_OK):
        # no render machine environment (the farms): build uam at its pin once per container, in a cache every build
        # directory of the container shares ($FNVNX_TOOL_CACHE, default ~/.cache/fnvnx; toolchain/uam/build.sh stamps
        # the pin); only a failed build is an error
        up = os.path.join(os.environ.get('FNVNX_TOOL_CACHE') or os.path.join(os.path.expanduser('~'), '.cache', 'fnvnx'),
                          'uam')
        b.say('link: uam (toolchain/uam/build.sh, once per container)')
        if b.sh(['bash', os.path.join(ROOT, 'toolchain', 'uam', 'build.sh'), up], r['log'], timeout=3600):
            raise StageError('LINK_OVERRIDES=render: building uam failed (toolchain/uam/build.sh, see %s)' % b.rel(r['log']))
        uam = os.path.join(up, 'bin', 'uam')
    out = os.path.join(d, 'render-n1')
    b.say('link: libnxrender.a (render/native/n1/build.sh)')
    path = os.pathsep.join([os.path.join(dkp, 'tools', 'bin'), os.path.join(prefix, 'bin'), os.environ.get('PATH', '')])
    # NX_SHADER_TABLE=device: the console's table (no GLSL) and its precompiled pack shaders.nxsp next to the archive;
    # the package stage puts that pack into fnv.nro's RomFS (NX1 check 9: nothing is compiled on the console)
    b.sh(['bash', os.path.join(ROOT, 'render', 'native', 'n1', 'build.sh'), out], r['log'],
         env=b.env({'DEVKITPRO': dkp, 'UAM': uam, 'PATH': path, 'NX_SHADER_TABLE': 'device'}), check=True, timeout=3600)
    lib = os.path.join(out, 'libnxrender.a')
    with open(lib + '.abi', 'w') as f:   # the nxgpu ABI it was built against (NX1: the package checks host == guest)
        f.write(file_hash(GPU_ABI_H) + '\n')
    return lib


GPU_ABI_H = os.path.join(ROOT, 'render', 'native', 'include', 'nx', 'gpu_abi.h')


def render_lib_abi(lib):
    """The gpu_abi.h hash a libnxrender.a was built against: its <lib>.abi sidecar (build_render_lib writes it), else
    'unknown' (an external RENDER_LIB without one: its ABI cannot be shown to match the host's)"""
    try:
        return open(lib + '.abi').read().strip() or 'unknown'
    except OSError:
        return 'unknown'


def objects_map(objects_tsv):
    """compile.sh / fetch_objs.sh objects.tsv -> {unit: object path or None when it failed}."""
    m = {}
    for line in open(objects_tsv, errors='surrogateescape'):
        f = line.rstrip('\n').split('\t')
        if len(f) >= 3:
            m[f[0]] = f[1] if f[2] == 'ok' else None
    return m


def seed_link_out(b, r, out):
    """LINK_SEED=<fnvlink out dir of an earlier run>: what of its demangle cache (cache/), layouts.tsv and vtables/
    (without the probe work dir) a fresh build/link/out lacks, as S2c's driver seeds a new run. fnvlink keys its
    probe caches on each unit's content (layout.unit_key), so rows of another tree are probed again by fnvlink
    itself; the seed only saves the probes whose units did not change."""
    seed = b.cfg['LINK_SEED']
    if not seed:
        return
    seed = os.path.abspath(seed)
    got = []
    for n in ('cache', 'layouts.tsv', 'vtables'):
        src, dst = os.path.join(seed, n), os.path.join(out, n)
        if not os.path.exists(src) or os.path.exists(dst):
            continue
        if os.path.isdir(src):
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns('probe'))
        else:
            shutil.copyfile(src, dst)
        got.append(n)
    if got:
        r.note('seeded from %s: %s' % (seed, ', '.join(got)))
    r.metric('link_seeded', len(got))


def objcache_on(env):
    return env.get('FNVNX_LINK_OBJCACHE', 'default') not in ('', 'none', 'off', '0')


class Link(Stage):
    name = 'link'
    deps = ('compile',)
    soft = ('toolchain',)
    prov = ('compile', 'prepare')

    def fingerprint(self, b):
        return ['link-v3', link_tool_inputs(b), up_id(b, 'compile'), up_id(b, 'prepare'),
                override_id(b), link_overrides(b)[2], extern_text_id(b), msvc_objs_id(b), msvc_objs_id(b, 'MSVC_CODE'),
                game_cxx_id(b), 'exe:%s' % (file_hash(link_exe(b)) or 'none')]

    def outputs_present(self, b):
        return os.path.isfile(os.path.join(b.out, 'link', 'out', 'aliases.ld'))

    def run(self, b, r):
        d = b.sdir('link')
        out, fixed = os.path.join(d, 'out'), os.path.join(d, 'fixed')
        os.makedirs(out, exist_ok=True)
        tree = b.art('prepare', 'tree')
        fl = os.path.join(ROOT, 'tools', 'link', 'fnvlink.py')
        units_json = os.path.join(tree, 'build', 'units.json')
        objects = b.art('compile', 'objects')
        args = [units_json, objects, out]
        et = b.cfg['EXTERN_TEXT']
        if et == 'auto':
            # S4's addr_ids.txt, made from the tree's units.json (identical to S4's file for the f11/f12 tree)
            et = os.path.join(d, 'extern_text.txt')
            b.sh([PY, '-I', os.path.join(HERE, 'extern_text.py'), units_json, et], r['log'], check=True)
            r.metric('link_extern_text_addrs', sum(1 for _ in open(et)))
        if et and et != 'none':
            args += ['--extern-text', os.path.abspath(et)]
        else:
            r.note('EXTERN_TEXT=none: the referenced .text addresses without a definition get no E entries (the '
                   'hand flow passes S4\'s addr_ids.txt)')
        ov_tsv = ''
        if b.cfg['OVERRIDE'] == 'auto':
            # the port's native replacements (tools/link/overrides: the variadic logger 0x00703c00, ...); their
            # sources are in link_tool_inputs (tools/link)
            ovd = os.path.join(d, 'overrides')
            b.sh(['bash', os.path.join(ROOT, 'tools', 'link', 'overrides', 'build.sh'), ovd], r['log'],
                 env=b.env(), check=True)
            ov_tsv = os.path.join(ovd, 'override.tsv')
        elif b.cfg['OVERRIDE'] not in ('', 'none'):
            ov_tsv = os.path.abspath(b.cfg['OVERRIDE'])
        # LINK_OVERRIDES=render: S6n's table and libnxrender.a, added to OVERRIDE's rows (fnvlink reads one table);
        # the guest stage links the archive (apply copies only objects into fixed/)
        rv_tsv, rv_lib, _ = link_overrides(b)
        if rv_tsv:
            rv_lib = rv_lib or build_render_lib(b, r, d)
            if not os.path.isfile(rv_lib):
                raise StageError('LINK_OVERRIDES=render: no %s' % rv_lib)
            r.artifact('override_lib', rv_lib)
            r['render_abi'] = render_lib_abi(rv_lib)
            pack = os.path.join(os.path.dirname(rv_lib), 'shaders.nxsp')
            if os.path.isfile(pack):   # the pack the guest's shader table was generated against
                r.artifact('shader_pack', pack)
            both = os.path.join(d, 'overrides_all.tsv')
            with open(both, 'w', encoding='utf-8', errors='surrogateescape') as f:
                for t_ in (ov_tsv, rv_tsv):
                    if t_:
                        f.write(open(t_, encoding='utf-8', errors='surrogateescape').read().rstrip('\n') + '\n')
            args += ['--override', both, '--override-lib', rv_lib]
        elif ov_tsv:
            args += ['--override', ov_tsv]
        r['link_overrides'] = (b.cfg['OVERRIDE'] or 'none') + ('+render' if rv_tsv else '')
        for opt, key in (('--msvc-objs', 'MSVC_OBJS'), ('--msvc-code', 'MSVC_CODE'),
                         ('--units-renames', 'UNITS_RENAMES')):
            if b.cfg[key]:
                args += [opt, b.cfg[key] if b.cfg[key] == 'none' else os.path.abspath(b.cfg[key])]
        # S2c: the player's exe (RTTI pairing and slot fill); --exe none keeps resolve off FNVNX_EXE / PLAYER_EXE
        # when the stage has no exe, so the fingerprint says what was used
        exe = link_exe(b)
        args += ['--exe', exe or 'none']
        r.metric('link_exe', 1 if exe else 0)
        r['resolve_options'] = [b.rel(a) if a != exe else '<player exe>' for a in args[3:]]
        seed_link_out(b, r, out)
        # the whole-program ordering keys (S12, docs/ORDERING.md) for the code the link emits (vtables.py slot
        # functions, bodies.py bodies: compile.sh --print-flags): ORDER_KEYS (link_job.sh: keys_job's k/<c12>),
        # else computed here like compile.sh does (KEYS_ONLY=1, cached per unit in <link>/keys)
        keys = os.environ.get('ORDER_KEYS', '')
        if not keys:
            kd = os.path.join(d, 'keys')
            b.say('link: whole-program ordering keys (compile.sh KEYS_ONLY=1, cached)')
            b.sh(['bash', os.path.join(ROOT, 'tools', 'link', 'compile.sh'), tree, kd, '', str(b.jobs)], r['log'],
                 env=b.env({'KEYS_ONLY': '1', 'CXX': probe_cxx(b, d)}), check=True)
            keys = os.path.join(kd, 'order.keys')
        r['order_keys'] = b.rel(keys) if keys != 'unit' else 'unit'
        env = b.env({'CXX': probe_cxx(b, d), 'ORDER_KEYS': keys})
        log = r['log']

        def resolve(n):
            # resolve writes out/objcopy/<obj>.* per object but never removes old ones; apply would then rewrite
            # objects of units that no longer compile (S2 interface gap; S2c's driver starts every run in a new dir)
            shutil.rmtree(os.path.join(out, 'objcopy'), ignore_errors=True)
            b.say('link: fnvlink resolve %d' % n)
            t = time.time()
            b.sh([PY, '-I', fl, 'resolve'] + args, log, env=env, check=True)
            r.metric('link_resolve%d_s' % n, round(time.time() - t, 1))
            ph = re.findall(r'resolve phases: (.*)', open(log, errors='replace').read())
            for name, sec in re.findall(r'([\w+/() -]+?) ([\d.]+) s(?:,|$)', ph[-1] if ph else ''):
                r.metric('link_resolve%d_phase_%s_s' % (n, re.sub(r'\W+', '_', name.strip()).strip('_')), float(sec))
            shutil.copyfile(os.path.join(out, 'stats.txt'), os.path.join(d, 'resolve%d.stats.txt' % n))

        # S2c's order: resolve (asks for layouts and vtables), probe (layouts + synthesized vtables/typeinfo),
        # resolve again with both, apply, trial link. build/link/out persists between runs: resolve 1 starts from the
        # compiled objects only, without the previous run's synthesized objects (fnvlink.reset_synth)
        gone = subprocess.run([PY, '-I', '-c', 'import sys; sys.path.insert(0, sys.argv[1]); import fnvlink; '
                               'print(" ".join(fnvlink.reset_synth(sys.argv[2])))', os.path.dirname(fl), out],
                              capture_output=True, text=True)
        if gone.stdout.strip():
            r.note('previous run\'s synthesized objects dropped before resolve 1: ' + gone.stdout.strip())
        resolve(1)
        # fnvlink probe = the layout probe, then the vtable synthesis; run as its two documented halves (probe with
        # FNVNX_NO_VTABLES=1, then vtables) to time each
        b.say('link: fnvlink probe (aggregate layouts; %d jobs)' % b.jobs)
        t = time.time()
        rc = b.sh([PY, '-I', fl, 'probe', out, tree, b.jobs], log, env=dict(env, FNVNX_NO_VTABLES='1'))
        r.metric('link_probe_layouts_s', round(time.time() - t, 1))
        if os.path.exists(os.path.join(out, 'vtable_requests.tsv')):
            b.say('link: fnvlink vtables (synthesized vtables and typeinfo; %d jobs)' % b.jobs)
            t2 = time.time()
            rc = b.sh([PY, '-I', fl, 'vtables', out, tree, b.jobs], log, env=env) or rc
            r.metric('link_probe_vtables_s', round(time.time() - t2, 1))
        r.metric('link_probe_s', round(time.time() - t, 1))
        r.metric('link_probe_jobs', b.jobs)
        text = open(log, errors='replace').read()
        mm = re.findall(r'probe: (\d+) units, (\d+) types \((\d+) cached\)', text)
        if mm:
            r.metric('link_probe_units', int(mm[-1][0]))
            r.metric('link_probe_types', int(mm[-1][1]))
            r.metric('link_probe_cached', int(mm[-1][2]))
        mm = re.findall(r'probe: (\d+) cached rows, (\d+) dropped', text)
        if mm:
            r.metric('link_layouts_dropped', int(mm[-1][1]))
        mm = re.findall(r'vtables round (\d+): (\d+) classes, (\d+) host units', text)
        if mm:
            r.metric('link_vtable_rounds', len(mm))
            r.metric('link_vtable_host_probes', sum(int(x[2]) for x in mm))
        if rc:
            r.note('probe exited %d; resolve 2 uses the layouts and vtables it left' % rc)
        # ---- S2d (tools/link/bodies.py): the C++ names resolve 1 left undefined, bound by address, called through
        # the vptr or synthesized; resolve 2 reads out/bodies/ (FNVNX_NO_BODIES=1: left out)
        if not os.environ.get('FNVNX_NO_BODIES'):
            b.say('link: fnvlink bodies (declared-only functions; %d jobs)' % b.jobs)
            t = time.time()
            pr = b.cfg['MSVC_OBJS'] or b.cfg['MSVC_CODE']
            # the player's exe (FNVNX_EXE, else a BOOT_IMAGE file): addresses of the calls the pristine objects make
            exe = link_exe(b)
            brc = b.sh([PY, '-I', fl, 'bodies', out, tree, '--jobs', str(b.jobs)]
                       + (['--msvc-objs', os.path.abspath(pr)] if pr and pr != 'none' else [])
                       + (['--exe', exe] if exe else []), log, env=env)
            r.metric('link_bodies_s', round(time.time() - t, 1))
            bst = os.path.join(out, 'bodies', 'stats.txt')
            if brc:
                r.note('bodies exited %d; resolve 2 uses what it left' % brc)
            elif os.path.exists(bst):
                for line in open(bst):
                    mm = re.match(r'(addr|exe-addr|gdel|vcall|msvc-empty|msvc-inline|special-default|missing)\s+(\d+)', line)
                    if mm:
                        r.metric('link_bodies_' + mm.group(1).replace('-', '_'), int(mm.group(2)))
        # ---- end S2d
        resolve(2)
        shutil.rmtree(fixed, ignore_errors=True)
        b.say('link: fnvlink apply')
        alog = os.path.join(d, 'apply.log')
        open(alog, 'w').close()
        t = time.time()
        b.sh([PY, '-I', fl, 'apply', out, fixed], alog, env=env, check=True)
        r.metric('link_apply_s', round(time.time() - t, 1))
        afail = [line.strip() for line in open(alog, errors='replace')
                 if line.startswith(('objcopy failed', 'as failed', 'cc addrmap.c failed'))]
        b.say('link: fnvlink check (trial link)')
        clog = os.path.join(d, 'check.txt')
        open(clog, 'w').close()
        t = time.time()
        b.sh([PY, '-I', fl, 'check', out, fixed], clog, env=env, check=True)
        r.metric('link_check_s', round(time.time() - t, 1))
        # S13's split-global check on the rewritten objects (part of S2c's run summary): want 0
        rb = os.path.join(d, 'check_rebind.txt')
        open(rb, 'w').close()
        rrc = b.sh([PY, '-I', os.path.join(ROOT, 'tools', 'pipeline', 'check_rebind.py'), fixed], rb, env=env)
        rbtext = open(rb, errors='replace').read()
        mm = re.search(r'objects with bypassing references: (\d+); variables: (\d+); relocations: code (\d+), '
                       r'data (\d+)', rbtext)
        if mm:
            r.metric('link_bypassing_objects', int(mm.group(1)))
            r.metric('link_bypassing_relocs', int(mm.group(3)) + int(mm.group(4)))
        else:
            r.note('check_rebind.py gave no result (exit %d, %s)' % (rrc, b.rel(rb)))
        # T1, incremental link: the compile cache's use per step (tools/link/objcache.py) and the steps' own phases
        ltext = open(log, errors='replace').read()
        for tag, n_, h_, cs in re.findall(r'objcache (\w+): (\d+) compiles, (\d+) hits \(\d+ %\), \d+ compiled '
                                          r'\(([\d.]+) s\)', ltext):
            r.metric('link_objcache_%s_compiles' % tag, int(n_))
            r.metric('link_objcache_%s_hits' % tag, int(h_))
            r.metric('link_objcache_%s_compile_s' % tag, float(cs))
        # why the misses missed (objcache.py explain): the step's most common reasons, a note on the report
        for tag, why in re.findall(r'^objcache (\w+): .*?; misses: (.*)$', ltext, re.M):
            r[('link_objcache_%s_misses' % tag)] = why[:600]
            r.note('objcache %s misses: %s' % (tag, why[:300]))
        for step, body in re.findall(r'^(bodies|vtables) phases: (.*)$', ltext, re.M):
            for name, sec in re.findall(r'([\w+/ -]+?) ([\d.]+) s(?:,|$)', body):
                r.metric('link_%s_phase_%s_s' % (step, re.sub(r'\W+', '_', name.strip())), float(sec))
        if 'VERIFY MISMATCH' in ltext:
            r['status'] = 'warn'
            r.note('objcache: a cached compile differs from a fresh one (FNVNX_LINK_OBJCACHE_VERIFY=1, see the log)')
        oc = os.path.join(ROOT, 'tools', 'link', 'objcache.py')
        if objcache_on(env):   # least recently used results above FNVNX_LINK_OBJCACHE_MAX_MB (default 8192) go
            b.sh([PY, '-I', oc, 'prune'], log, env=env)
        m, classes = parse_stats(os.path.join(out, 'stats.txt'))
        ctext = open(clog, errors='replace').read()
        cm, cclasses = parse_check(ctext)
        for k, v in list(m.items()) + list(cm.items()):
            r.metric(k, v)
        if m.get('link_refs'):
            r.metric('link_resolved_pct', round(100.0 * m.get('link_resolved', 0) / m['link_refs'], 1))
        r['ref_classes'] = classes
        r['check_classes'] = cclasses
        r['apply_failures'] = afail[:50]
        r.metric('link_apply_failures', len(afail))
        nfixed = len([n for n in os.listdir(fixed) if n.endswith('.o')]) if os.path.isdir(fixed) else 0
        r.metric('link_fixed_objects', nfixed)
        # the run summary, first line as S2c's driver writes it (J-0054: every link run names its tree's commit)
        pf = b.rec('prepare').get('prepared_from') or {}
        with open(os.path.join(d, 'summary.txt'), 'w') as f:
            f.write('tree prepared from branch commit: %s\n' % (pf.get('port_commit') or 'unknown'))
            f.write('decomp %s; tree %s (%s); linked with port %s\n' % (
                pf.get('decomp_commit') or 'unknown', pf.get('tree'), pf.get('mode'), b.head or 'unknown'))
            f.write(''.join(open(os.path.join(out, 'stats.txt')).readlines()[:3]))
            mt = r.get('metrics', {})
            f.write('seconds: resolve 1 %s, probe %s (layouts %s, vtables %s; %s jobs), resolve 2 %s, apply %s, trial link '
                    '%s\n' % tuple(mt.get('link_%s' % k) for k in ('resolve1_s', 'probe_s', 'probe_layouts_s',
                                                                  'probe_vtables_s', 'probe_jobs', 'resolve2_s',
                                                                  'apply_s', 'check_s')))
            f.write(''.join(l + '\n' for l in ctext.splitlines() if l.strip() and not l.startswith(('$ ', '[exit'))))
            if mm:
                f.write(mm.group(0) + '\n')
        for k in ('aliases.ld', 'addrmap.tsv', 'refs.tsv', 'abi.tsv', 'stats.txt', 'bridges.S'):
            r.artifact(k, os.path.join(out, k))
        r.artifact('out', out)
        r.artifact('fixed', fixed)
        r.artifact('summary', os.path.join(d, 'summary.txt'))
        r['output_id'] = sha(file_hash(os.path.join(out, 'aliases.ld')),
                             listing_id([os.path.join(fixed, n) for n in os.listdir(fixed)]))
        r['summary'] = '%s refs, %s resolved by address (%s %%), %s bridges; trial link: %s undefined, %s multidef' % (
            m.get('link_refs'), m.get('link_resolved'), r['metrics'].get('link_resolved_pct'), m.get('link_bridges'),
            cm.get('link_check_undefined'), cm.get('link_check_multidef'))
        if afail or cm.get('link_check_multidef') or r['metrics'].get('link_bypassing_relocs'):
            r['status'] = 'warn'
        if afail:
            r.note('%d objects could not be rewritten by apply (see build/link/apply.log); they are left out' %
                   len(afail))


# ----------------------------------------------------------------------------------------------------- 7 guest

def s5_guest_script():
    p = os.path.join(ROOT, 'toolchain', 'game', 'build.sh')
    return p if os.path.isfile(p) else None


def nine_game_script():
    p = os.path.join(ROOT, 'render', 'nine', 'game', 'build.sh')
    return p if os.path.isfile(p) else None


def guest_script(b):
    """auto: NINE=1 -> render/nine/game/build.sh (S6c: S5's game link with D3D9=external + the proxy guest),
    NINE=0 -> toolchain/game/build.sh (S5, null D3D9 device); the pipeline adapter only when they are missing."""
    g = b.cfg['GUEST_LINK']
    if g == 'auto':
        if b.cfg['NINE'] == '1' and nine_game_script():
            return nine_game_script()
        return s5_guest_script() or os.path.join(HERE, 'guest_link.py')
    if g == 'adapter':
        return os.path.join(HERE, 'guest_link.py')
    return os.path.abspath(g)


class Guest(Stage):
    name = 'guest'
    deps = ('link', 'toolchain')
    soft = ('nine',)
    prov = ('link',)

    def fingerprint(self, b):
        scr = guest_script(b)
        own = scr.startswith(HERE)
        return ['guest-v3', scr, tree_hash(os.path.dirname(scr)) if not own else
                tree_hash('tools/pipeline/guest_link.py', 'tools/pipeline/guest_stub.c'),
                tree_hash('tools/pipeline/undef.py', 'toolchain/game', 'toolchain/guest', 'toolchain/include',
                          'toolchain/tools', 'platform', 'loader', 'startup', 'shim/include', 'tests/s5/dynbrk.py'),
                tree_hash('render/nine') if b.cfg['NINE'] == '1' else '',
                up_id(b, 'link'), up_id(b, 'toolchain'), b.cfg['NINE'], b.art('toolchain', 'vorbis'), b.cfg['GAME_NEW'],
                file_hash(b.art('link', 'override_lib')),
                b.rec('fetch').get('metrics', {}).get('pin_sse2neon')]

    def outputs_present(self, b):
        return os.path.isfile(os.path.join(b.out, 'guest', 'guest.nso'))

    def run(self, b, r):
        d = b.sdir('guest')
        scr = guest_script(b)
        lout, fixed = b.art('link', 'out'), b.art('link', 'fixed')
        if not b.art('toolchain', 'devkitpro') or not b.art('toolchain', 'prefix'):
            raise StageError('the guest link needs FNVNX_TC and DEVKITPRO')
        for n in ('fnvnx.elf', 'guest.nso', 'undefined.txt', 'summary.json'):
            if os.path.exists(os.path.join(d, n)):
                os.remove(os.path.join(d, n))
        # S5's hand flow: D3D9=null (headless null device), GAME_NEW=runtime, GUEST=pie with PIN=auto (the script's
        # defaults), DEVKITPRO / SSE2NEON / JOBS from the environment
        extra = {'FNVNX_TC': b.art('toolchain', 'prefix'), 'D3D9': 'external' if b.cfg['NINE'] == '1' else 'null',
                 'GAME_NEW': b.cfg['GAME_NEW']}
        r['d3d9'], r['game_new'] = extra['D3D9'], extra['GAME_NEW']
        olib = b.art('link', 'override_lib')
        if olib:   # LINK_OVERRIDES=render: the replacements' archive (the fixed objects reference its symbols)
            extra['EXTRA_OBJS'] = (os.environ.get('EXTRA_OBJS', '') + ' ' + olib).strip()
            r['override_lib'] = b.rel(olib)
        nine_game = scr == nine_game_script()
        prod = d                       # where the script leaves fnvnx.elf / guest.nso / undefined.txt
        if nine_game:
            extra['GUEST_ONLY'] = '1'  # the host module is the host stage's (render/nine/switch_host.sh)
            prod = os.path.join(d, 'nine', 'game')
        r.artifact('script', b.rel(scr))
        if scr.startswith(HERE):
            r['method'] = 'pipeline adapter tools/pipeline/guest_link.py'
        elif nine_game:
            r['method'] = 'S6c render/nine/game/build.sh (S5 game link, D3D9=external + Nine proxy guest)'
        elif scr.endswith(os.path.join('toolchain', 'game', 'build.sh')):
            r['method'] = 'S5 toolchain/game/build.sh' + ('' if scr == s5_guest_script() else ' (%s)' % scr)
        else:
            r['method'] = scr
        b.say('guest: ILP32 guest link via %s' % r['method'])
        argv = ([PY, '-I', scr] if scr.endswith('.py') else ['bash', scr]) + [lout, fixed,
                                                                          os.path.join(d, 'nine') if nine_game else d]
        vorbis = b.art('toolchain', 'vorbis')        # VORBIS: a prefix, built by the toolchain stage, or none
        r['vorbis'] = vorbis
        if vorbis and not scr.endswith('.py'):
            if nine_game:
                extra['VORBIS'] = vorbis
            else:
                argv.append(vorbis)
        rc = b.sh(argv, r['log'], env=b.env(extra), timeout=7200)
        if prod != d:
            for n in ('fnvnx.elf', 'guest.nso', 'undefined.txt', 'fnvnx.map'):
                if os.path.isfile(os.path.join(prod, n)):
                    shutil.copyfile(os.path.join(prod, n), os.path.join(d, n))
        und = os.path.join(d, 'undefined.txt')
        tree = b.art('prepare', 'tree')
        if os.path.isfile(und):
            cats = undefmod.categorize_file(und, addrmap=os.path.join(lout, 'addrmap.tsv'),
                                            units_json=os.path.join(tree, 'build', 'units.json'),
                                            objects_tsv=b.art('compile', 'objects'),
                                            out_tsv=os.path.join(d, 'undefined_by_category.tsv'))
            r['undefined'] = cats
            r.metric('guest_undefined', sum(c['count'] for c in cats))
            for c in cats:
                r.metric('guest_undef_' + c['key'], c['count'])
            r.artifact('undefined_by_category', os.path.join(d, 'undefined_by_category.tsv'))
        sj = os.path.join(d, 'summary.json')
        if os.path.isfile(sj):
            s = json.load(open(sj))
            r['adapter'] = s
            for k in ('platform_units', 'platform_failed', 'multidef', 'game_objects', 'link_excluded'):
                if k in s:
                    r.metric('guest_' + k, s[k] if not isinstance(s[k], list) else len(s[k]))
        for n in ('fnvnx.elf', 'guest.nso'):
            p = os.path.join(d, n)
            if os.path.isfile(p):
                r.artifact(n, p)
                r.metric('guest_%s_bytes' % n.replace('.', '_'), os.path.getsize(p))
        ex = (r.get('adapter') or {}).get('link_excluded') or []
        if ex:
            r.note('%d objects refused by ld and left out (S2-link: TLS-model references bound to absolute image '
                   'addresses, misaligned cross-unit variables): %s' % (len(ex), ', '.join(
                       os.path.basename(o) for o, _ in ex[:6])))
        pf = (r.get('adapter') or {}).get('platform_failed') or []
        if pf:
            r.note('%d platform sources do not compile for the guest (S4/S1 shim): %s' % (
                len(pf), ', '.join(f for f, _ in pf[:6])))
        nso = os.path.join(d, 'guest.nso')
        if rc or not os.path.isfile(nso):
            raise StageError('guest link failed (exit %d), see %s: %s' % (rc, b.rel(r['log']),
                                                                         ' | '.join(Build.tail(r['log'], 3))))
        # S5's checks of the linked guest: PC-relative references to absolute image symbols (want 0: relocatable),
        # static initializers / finalizers that hold a trap (want 0, tests/s5/dynbrk.py)
        pl = os.path.join(prod, 'pcrel_abs.log')
        if os.path.isfile(pl):
            mm = re.search(r'absolute symbols: (\d+) in (\d+) objects', open(pl, errors='replace').read())
            if mm:
                r.metric('guest_pcrel_abs_refs', int(mm.group(1)))
                r.metric('guest_pcrel_abs_objects', int(mm.group(2)))
        nm = os.path.join(b.art('toolchain', 'prefix'), 'bin', 'aarch64-none-elf-nm')
        rc, dyn = b.capture([PY, '-I', os.path.join(ROOT, 'tests', 's5', 'dynbrk.py'), os.path.join(d, 'fnvnx.elf'),
                             nm], timeout=600)
        with open(os.path.join(d, 'dynbrk.txt'), 'w') as f:
            f.write(dyn)
        for kind in ('dyninit', 'dynexit'):
            mm = re.search(r'\b%s\D*?(\d+)\D+?(\d+)' % kind, dyn)
            if mm:
                r.metric('guest_%s_functions' % kind, int(mm.group(1)))
                r.metric('guest_%s_brk' % kind, int(mm.group(2)))
        if rc:
            r.note('tests/s5/dynbrk.py exited %d (build/guest/dynbrk.txt)' % rc)
        if r.get('metrics', {}).get('guest_dyninit_brk') or r.get('metrics', {}).get('guest_dynexit_brk'):
            r['status'] = 'warn'
            r.note('static initializers / finalizers with a trap: see build/guest/dynbrk.txt')
        rc, dump = b.capture([PY, '-I', os.path.join(ROOT, 'toolchain', 'tools', 'elf2nso.py'), '--dump', nso])
        r['nso_dump'] = dump.splitlines()[:12]
        if rc:
            r.note('elf2nso --dump failed on guest.nso')
            r['status'] = 'warn'
        r['output_id'] = file_hash(nso)
        first = r['method'].split(' ')[0]
        lay = [l for l in Build.tail(r['log'], 400) if l.startswith('guest: link base')]
        if lay:
            r['layout'] = lay[-1]
        r['summary'] = '%s: guest.nso %s, ELF %s, %s undefined symbols bound to trap stubs' % (
            'adapter' if first == 'pipeline' else first, human(os.path.getsize(nso)),
            human(r.get('metrics', {}).get('guest_fnvnx_elf_bytes')), r.get('metrics', {}).get('guest_undefined'))
        if r.get('metrics', {}).get('guest_undefined'):
            r['status'] = 'warn'


# ------------------------------------------------------------------------------------------------------ 8 boot

def synth_inputs_id(b):
    """What S14's synthesized image depends on: the generator, S4's import names, the pristine units.json and the
    MSVC objects (by listing)."""
    po = b.cfg['PRISTINE_OBJS']
    if not po:
        return ''
    po = os.path.abspath(po)
    objs = sorted(os.path.join(po, n) for n in os.listdir(po) if n.endswith('.obj')) if os.path.isdir(po) else []
    return sha(tree_hash('loader/synth', 'platform/win32/imports.tsv'), listing_id(objs),
               file_hash(os.path.join(b.art('fetch', 'decomp') or '', 'build', 'units.json')))


def boot_image(b, r, d):
    """The data image of the emulated boot: BOOT_IMAGE=<exe>; auto: S14's synthesized image
    (loader/synth/synth_image.py over S11's pristine MSVC objects, PRISTINE_OBJS) when the objects are there, else
    none; none: no image (the boot maps an all-zero data window). -> path or None."""
    img = b.cfg['BOOT_IMAGE']
    if img in ('none', ''):
        return None
    if img != 'auto':
        return os.path.abspath(img)
    po = b.cfg['PRISTINE_OBJS']
    if not po or not os.path.isdir(po):
        r.note('BOOT_IMAGE=auto: no PRISTINE_OBJS (S11\'s MSVC 2008 objects of the pristine decomp, tools/equiv), so '
               'no synthesized data image: the boot runs on the all-zero image')
        return None
    sd = os.path.join(d, 'synth')
    os.makedirs(sd, exist_ok=True)
    out = os.path.join(sd, 'synth.exe')
    idf = os.path.join(sd, 'inputs.id')
    want = synth_inputs_id(b)
    if os.path.isfile(out) and read_id(idf) == want:
        return out
    for n in ('synth.exe', 'phase1.pickle', 'inputs.id'):
        if os.path.exists(os.path.join(sd, n)):
            os.remove(os.path.join(sd, n))
    b.say('boot: S14 synthesized data image (loader/synth/synth_image.py)')
    slog = os.path.join(sd, 'synth.log')
    open(slog, 'w').close()
    rc = b.sh([PY, '-I', os.path.join(ROOT, 'loader', 'synth', 'synth_image.py'),
               '--units', os.path.join(b.art('fetch', 'decomp'), 'build', 'units.json'), '--objdir', os.path.abspath(po),
               '--imports', os.path.join(ROOT, 'platform', 'win32', 'imports.tsv'), '--out', out,
               '--summary', os.path.join(sd, 'summary.txt'), '--report', os.path.join(sd, 'report.json'),
               '--missing', os.path.join(sd, 'missing.tsv'), '--cache', os.path.join(sd, 'phase1.pickle')],
              slog, cwd=ROOT, timeout=7200)
    if rc or not os.path.isfile(out):
        r.note('synth_image.py exited %d (build/boot/synth/synth.log): the boot runs on the all-zero image' % rc)
        r['status'] = 'warn'
        return None
    with open(idf, 'w') as f:
        f.write(want + '\n')
    m = re.search(r'objects (\d+): (\d+) items placed', open(slog, errors='replace').read())
    if m:
        r.metric('synth_objects', int(m.group(1)))
        r.metric('synth_items_placed', int(m.group(2)))
    if os.path.exists(os.path.join(sd, 'phase1.pickle')):
        os.remove(os.path.join(sd, 'phase1.pickle'))      # the object scan cache: large, and rebuilt when needed
    return out


class Boot(Stage):
    """S5's emulated boot of the game guest (tests/s5/emu_guest.py: traced boot without the player's exe, faulting
    static initializers skipped) and the boot frontier (tests/s5/frontier.py: every untranslated address the boot
    hit, mapped to its unit and that unit's compile status). Informational: the boot stops at a fault by design."""
    name = 'boot'
    deps = ('guest',)
    soft = ('census',)
    prov = ('guest',)

    def enabled(self, b):
        return b.cfg['BOOT'] == '1' or 'boot' in b.explicit

    def fingerprint(self, b):
        img, bc = b.cfg['BOOT_IMAGE'], b.cfg['BOOT_CENSUS']
        ini = os.path.join(os.path.dirname(os.path.abspath(img)), 'Fallout_default.ini') if os.path.isfile(img) else ''
        return ['boot-v4', tree_hash('tests/s5/emu_guest.py', 'tests/s5/frontier.py', 'tests/s5/emu_summary.py',
                                     'tests/s5/milestones.txt',
                                     'toolchain/tools', 'toolchain/game/undef_report.py', 'platform/win32/imports.tsv'),
                up_id(b, 'guest'), img if img in ('auto', 'none', '') else file_hash(os.path.abspath(img)),
                synth_inputs_id(b) if img == 'auto' else '',
                file_hash(os.path.abspath(bc)) if bc else up_id(b, 'census'), b.cfg['EMU_TIMEOUT'], emu_slicing(b),
                file_hash(ini) if ini and os.path.isfile(ini) else '']    # the ini mounted beside the image

    def outputs_present(self, b):
        return os.path.isfile(os.path.join(b.out, 'boot', 'emu.log'))

    def run(self, b, r):
        d = b.sdir('boot')
        g = b.rec('guest')
        if not g.get('method', '').startswith('S5'):
            raise StageError('the emulated boot needs the boot code of S5\'s game link (toolchain/game/build.sh); '
                             'this guest was linked by %s' % g.get('method'))
        nso, elf = b.art('guest', 'guest.nso'), b.art('guest', 'fnvnx.elf')
        gdir = os.path.dirname(nso)
        root = os.path.join(d, 'sdmc')            # sdmc:/ of the emulated host, removed afterwards
        shutil.rmtree(root, ignore_errors=True)
        os.makedirs(root)
        argv = [PY, '-I', os.path.join(ROOT, 'tests', 's5', 'emu_guest.py'), nso, '--elf', elf, '--skip-faulting-init',
                '--expect', 'fnvnx boot: calling WinMain', '--root', root] + emu_slicing(b) + [
                '--summary-json', os.path.join(d, 'boot_summary.json')]
        image = boot_image(b, r, d)
        r['image'] = b.rel(image) if image else 'none (all-zero data image)'
        if image:
            argv += ['--file', 'switch/fnv/FalloutNV.exe=' + image]
            # the owner's Fallout_default.ini beside the exe (gamedata/exe holds both): without it the boot stops at
            # the ini read (R4, recon4-013); S14's synthesized image has none beside it, so nothing changes there
            ini = os.path.join(os.path.dirname(image), 'Fallout_default.ini')
            if os.path.isfile(ini):
                argv += ['--file', 'switch/fnv/Fallout_default.ini=' + ini]
                r['ini'] = 'Fallout_default.ini beside the image'
        elog = os.path.join(d, 'emu.log')
        open(elog, 'w').close()
        tmo = int(b.cfg['EMU_TIMEOUT'])
        b.say('boot: emulated boot of the game guest (tests/s5/emu_guest.py, at most %d s)' % tmo)
        rc = b.sh(argv, elog, cwd=ROOT, timeout=tmo)
        shutil.rmtree(root, ignore_errors=True)
        lines = [l for l in open(elog, errors='replace').read().splitlines() if not l.startswith('$ ')]
        winmain = any('fnvnx boot: calling WinMain' in l for l in lines)
        stop = next((l.strip() for l in reversed(lines) if l.startswith('STOPPED:')), None)
        skipped = next((int(m.group(1)) for l in reversed(lines)
                        for m in [re.search(r'initializers skipped after a fault: (\d+)', l)] if m), None)
        table = [l.split('\t') for l in lines if l.startswith('trap-table\t')]
        r.metric('boot_exit', rc)
        r.metric('boot_winmain', int(winmain))
        r.metric('boot_init_skips', skipped)
        r.metric('boot_trap_keys', len(table))
        r.metric('boot_trap_hits', sum(int(t[2]) for t in table if len(t) > 2 and t[2].isdigit()))
        # S5's boot summary (tests/s5/milestones.txt): the game milestones reached, the stop with the game's own
        # messages (a boot that ends in ExitProcess has no STOPPED line)
        try:
            bs = json.load(open(os.path.join(d, 'boot_summary.json')))
        except (OSError, ValueError):
            bs = {}
        r.metric('boot_milestones_reached', bs.get('reached'))
        r['milestone'] = bs.get('milestone')
        r['game_milestones'] = [[m.get('name'), bool(m.get('reached'))] for m in bs.get('milestones', [])]
        if stop is None and bs.get('stop'):
            stop = ' / '.join([bs['stop'].get('reason') or ''] + (bs['stop'].get('messages') or [])[:3])
        r['stop'] = stop
        # the fault that stopped the boot (the last trace block: pc, lr, call frames) and the skipped initializers
        # grouped by fault (tests/s5/emu_guest.py skip-summary lines)
        starts = [i for i, l in enumerate(lines) if l.startswith('== trace (')]
        fault = []
        if starts:
            fault = [lines[starts[-1]].strip()]
            for l in lines[starts[-1] + 1:starts[-1] + 12]:
                if not re.match(r'\s+(pc|lr|#\d+|S10 )', l):
                    break
                fault.append(l.strip())
        r['fault'] = fault
        r['skip_summary'] = [l.split('\t')[1:] for l in lines if l.startswith('skip-summary\t')][:30]
        r['milestones'] = list(dict.fromkeys(l.strip() for l in lines if re.search(
            r'fnvnx boot: (?!trap|initializer #|import )', l)))[:80]
        if rc == 124:
            r.note('the emulator ran into EMU_TIMEOUT=%d s' % tmo)
        if any(l.startswith('Traceback') for l in lines) and stop is None:
            raise StageError('the emulator itself failed (build/boot/emu.log): %s' % ' | '.join(Build.tail(elog, 3)))
        # S5's summary (milestones, trap categories, skipped initializers by fault, stop reason)
        lout = b.art('link', 'out')
        rc2, summ = b.capture([PY, '-I', os.path.join(ROOT, 'tests', 's5', 'emu_summary.py'), elog,
                               os.path.join(lout, 'addrmap.tsv')], timeout=600)
        with open(os.path.join(d, 'summary.txt'), 'w') as f:
            f.write(summ)
        # the boot frontier: S1's priority list
        tree = b.art('prepare', 'tree')
        census = os.path.abspath(b.cfg['BOOT_CENSUS']) if b.cfg['BOOT_CENSUS'] else (
            b.art('census', 'units') if b.ok('census') else None)
        fargs = [PY, '-I', os.path.join(ROOT, 'tests', 's5', 'frontier.py'), elog,
                 '--addrmap', os.path.join(lout, 'addrmap.tsv'), '--units', os.path.join(tree, 'build', 'units.json'),
                 '--objects', b.art('compile', 'objects'), '--out', os.path.join(d, 'frontier'), '--top', '25']
        if os.path.isfile(os.path.join(gdir, 'fnvnx.map')):
            fargs += ['--map', os.path.join(gdir, 'fnvnx.map')]
        if census and os.path.isfile(census):
            fargs += ['--census', census]
            r['frontier_census'] = census
        flog = os.path.join(d, 'frontier.txt')
        open(flog, 'w').close()
        frc = b.sh(fargs, flog, cwd=ROOT, timeout=1800)
        ftext = open(flog, errors='replace').read()
        mm = re.search(r'(\d+) untranslated keys hit(?:, (\d+) hits)?', ftext)
        if mm:
            r.metric('frontier_keys', int(mm.group(1)))
            r.metric('frontier_hits', int(mm.group(2) or 0))
        fu = os.path.join(d, 'frontier_units.tsv')
        if os.path.isfile(fu):
            r.metric('frontier_units', sum(1 for l in open(fu, errors='replace') if l.strip() and not l.startswith(('#', 'rank', 'unit'))))
        if frc:
            r['status'] = 'warn'
            r.note('tests/s5/frontier.py exited %d (build/boot/frontier.txt)' % frc)
        for n, p in (('emu.log', elog), ('summary', os.path.join(d, 'summary.txt')), ('frontier', flog),
                     ('frontier.tsv', os.path.join(d, 'frontier.tsv')), ('frontier_units.tsv', fu)):
            if os.path.isfile(p):
                r.artifact(n, p)
        r['output_id'] = sha(file_hash(os.path.join(d, 'frontier.tsv')), stop, skipped, len(table))
        r['summary'] = '%s; %s initializers skipped, %d trapped keys; frontier %s keys in %s units; %s' % (
            'WinMain entered' if winmain else 'WinMain NOT entered', fmt_n(skipped), len(table),
            fmt_n(r['metrics'].get('frontier_keys')), fmt_n(r['metrics'].get('frontier_units')),
            clean_msg(stop or 'no STOPPED line (exit %d)' % rc)[:140])
        if not winmain:
            r['status'] = 'warn'


def fmt_n(v):
    return '-' if v is None else '{:,}'.format(v) if isinstance(v, int) else str(v)


# ------------------------------------------------------------------------------------------------------ 9 nine

class Nine(Stage):
    name = 'nine'
    deps = ('toolchain',)

    def enabled(self, b):
        return b.cfg['NINE'] == '1'

    def fingerprint(self, b):
        return ['nine-v2', tree_hash('render/nine/build.sh', 'render/nine/patches', 'render/nine/gen_proxy.py',
                                     'shim/include/d3d9.h'), up_id(b, 'toolchain'), b.cfg['NINE_SRC']]

    def outputs_present(self, b):
        return os.path.isdir(os.path.join(b.out, 'nine', 'work', 'out', 'lib'))

    def run(self, b, r):
        d = b.sdir('nine')
        work = os.path.join(d, 'work')
        src = os.path.join(work, 'src')
        os.makedirs(src, exist_ok=True)
        if b.cfg['NINE_SRC']:
            for n in ('mesa', 'libdrm_nouveau'):
                if not os.path.isdir(os.path.join(src, n)):
                    b.say('nine: copying %s from NINE_SRC' % n)
                    shutil.copytree(os.path.join(b.cfg['NINE_SRC'], n), os.path.join(src, n), symlinks=True,
                                    ignore=shutil.ignore_patterns('build-nine', 'build'))
        b.say('nine: render/nine/build.sh (Mesa st/nine + nouveau for Switch, LP64)')
        b.sh(['bash', os.path.join(ROOT, 'render', 'nine', 'build.sh'), work, b.jobs], r['log'], timeout=4 * 3600,
             check=True)
        libdir = os.path.join(work, 'out', 'lib')
        libs = sorted(n for n in os.listdir(libdir) if n.endswith('.a'))
        r['libs'] = {n: os.path.getsize(os.path.join(libdir, n)) for n in libs}
        r.metric('nine_libs', len(libs))
        r.metric('nine_libs_bytes', sum(r['libs'].values()))
        px = os.path.join(d, 'proxy')
        os.makedirs(px, exist_ok=True)
        rc = b.sh([PY, '-I', os.path.join(ROOT, 'render', 'nine', 'gen_proxy.py'),
                   os.path.join(ROOT, 'shim', 'include', 'd3d9.h'), px], r['log'])
        mt = os.path.join(px, 'methods.tsv')
        if rc == 0 and os.path.isfile(mt):
            kinds = {}
            for line in open(mt):     # interface, slot, method, kind (auto|manual), note
                f = line.rstrip('\n').split('\t')
                if len(f) < 4 or f[3] == 'kind':
                    continue
                kinds[f[3]] = kinds.get(f[3], 0) + 1
            r['proxy_kinds'] = kinds
        r.artifact('work', work)
        r['output_id'] = sha(r['libs'])
        remove_work(b, r, [os.path.join(src, 'mesa', 'build-nine')], 'the Mesa meson build directory')
        r['summary'] = '%d static libraries (%s), proxy methods %s' % (
            len(libs), human(r['metrics']['nine_libs_bytes']), r.get('proxy_kinds'))


# ----------------------------------------------------------------------------------------------------- 10 host

class Host(Stage):
    name = 'host'
    deps = ('toolchain',)
    soft = ('nine',)

    def fingerprint(self, b):
        return ['host-v3', tree_hash('toolchain/host', 'toolchain/include', 'toolchain/npdm', 'toolchain/tools',
                                     'tests/s5/hello', 'startup', 'toolchain/deko3d'),
                tree_hash('render/nine') if b.cfg['NINE'] == '1' else '',
                tree_hash('render/native/host', 'render/native/front', 'render/native/include')
                if b.cfg['HOST_BACKEND'] == 'native' and b.cfg['NINE'] != '1' else '', b.cfg['HOST_BACKEND'],
                up_id(b, 'toolchain'), b.cfg['NINE'], up_id(b, 'nine') if b.cfg['NINE'] == '1' else '',
                b.cfg['PROGRAM_ID']]

    def outputs_present(self, b):
        return os.path.isfile(os.path.join(b.out, 'host', 'main'))

    def run(self, b, r):
        d = b.sdir('host')
        if not b.art('toolchain', 'devkitpro'):
            raise StageError('the host module needs DEVKITPRO (libnx)')
        for n in ('host.elf', 'main', 'main.npdm'):
            if os.path.exists(os.path.join(d, n)):
                os.remove(os.path.join(d, n))
        if b.cfg['NINE'] == '1':
            if not b.ok('nine'):
                raise StageError('NINE=1 but the nine stage did not succeed')
            r['method'] = 'S6c render/nine/switch_host.sh (host.c without console + render/nine/host + Mesa + libnx)'
            b.say('host: Nine host module (render/nine/switch_host.sh)')
            nh = os.path.join(d, 'nine')
            b.sh(['bash', os.path.join(ROOT, 'render', 'nine', 'switch_host.sh'), nh], r['log'],
                 env=b.env({'NINE': b.art('nine', 'work')}), check=True)
            shutil.copyfile(os.path.join(nh, 'host.elf'), os.path.join(d, 'host.elf'))
        elif b.cfg['HOST_BACKEND'] == 'native':
            b.say('host: native renderer host module (toolchain/host/build_native.sh, deko3d)')
            # libdeko3d at its pins, cached across builds next to the host stage (toolchain/deko3d/build.sh)
            b.sh(['bash', os.path.join(ROOT, 'toolchain', 'host', 'build_native.sh'), os.path.join(d, 'native'),
                  os.path.join(d, 'deko3d')], r['log'], check=True, timeout=1800)
            r['method'] = 'NX1 toolchain/host/build_native.sh (host.c -DFNVNX_HOST_NO_CONSOLE + render/native/host + deko3d)'
            r['gpu_abi'] = file_hash(GPU_ABI_H)   # the guest's libnxrender.a must be built against the same header
            shutil.copyfile(os.path.join(d, 'native', 'host.elf'), os.path.join(d, 'host.elf'))
        elif b.cfg['HOST_BACKEND'] != 'plain':
            raise StageError('HOST_BACKEND must be native or plain, got %r' % b.cfg['HOST_BACKEND'])
        else:
            hb = os.path.join(d, 's5-hello')
            b.say('host: LP64 host module (tests/s5/hello/build.sh recipe)')
            b.sh(['bash', os.path.join(ROOT, 'tests', 's5', 'hello', 'build.sh'), hb, b.cfg['PROGRAM_ID']], r['log'],
                 check=True)
            r['method'] = 'S5 tests/s5/hello/build.sh (host.c + libnx + S10 window reservation)'
            shutil.copyfile(os.path.join(hb, 'host.elf'), os.path.join(d, 'host.elf'))
        tools = os.path.join(ROOT, 'toolchain', 'tools')
        b.sh([PY, '-I', os.path.join(tools, 'elf2nso.py'), os.path.join(d, 'host.elf'), os.path.join(d, 'main')],
             r['log'], check=True)
        b.sh([PY, '-I', os.path.join(tools, 'npdmtool.py'), os.path.join(ROOT, 'toolchain', 'npdm', 'fnvnx-app.json'),
              os.path.join(d, 'main.npdm')], r['log'], check=True)
        rc, dump = b.capture([PY, '-I', os.path.join(tools, 'npdmtool.py'), '--dump', os.path.join(d, 'main.npdm')])
        r['npdm_dump'] = dump.splitlines()[:10]
        for n in ('host.elf', 'main', 'main.npdm'):
            r.artifact(n, os.path.join(d, n))
            r.metric('host_%s_bytes' % n.replace('.', '_'), os.path.getsize(os.path.join(d, n)))
        r['output_id'] = sha(file_hash(os.path.join(d, 'main')), file_hash(os.path.join(d, 'main.npdm')))
        r['summary'] = '%s; main (NSO) %s, main.npdm %s' % (
            'Nine' if b.cfg['NINE'] == '1' else 'native renderer (deko3d)' if b.cfg['HOST_BACKEND'] == 'native'
            else 'plain host, no GPU backend', human(os.path.getsize(os.path.join(d, 'main'))),
            human(os.path.getsize(os.path.join(d, 'main.npdm'))))


# -------------------------------------------------------------------------------------------------- 11 package

class Package(Stage):
    """The SD tree of NX1 (toolchain/include/fnvnx/sdlayout.h): switch/fnv/{fnv.nro, fnv-exefs.nsp, guest.nso}. fnv.nro
    is the launcher (toolchain/nro/build.sh); started by title takeover it installs fnv-exefs.nsp as the override of
    that title and restarts it, so the package holds no program id. Then tests/s5/check_package.py and the NX1
    device-readiness checks (tests/nx1/run.py) on the tree."""
    name = 'package'
    deps = ('host',)
    soft = ('guest',)
    prov = ('guest',)      # only when the guest is packaged

    def fingerprint(self, b):
        return ['package-v3', up_id(b, 'host'), up_id(b, 'guest'), b.cfg['PROGRAM_ID'], tree_hash('toolchain/tools'),
                tree_hash('toolchain/nro', 'toolchain/include', 'tests/nx1', 'tests/s5/check_package.py',
                          'tests/s13/ilp32_wrap'), up_id(b, 'toolchain')]

    def outputs_present(self, b):
        return os.path.isfile(os.path.join(b.out, 'package', 'exefs.nsp'))

    def run(self, b, r):
        d = b.sdir('package')
        shutil.rmtree(os.path.join(d, 'sd'), ignore_errors=True)
        tools = os.path.join(ROOT, 'toolchain', 'tools')
        hd = b.sdir('host')
        nsp = os.path.join(d, 'exefs.nsp')
        b.sh([PY, '-I', os.path.join(tools, 'build_pfs0.py'), nsp, 'main=' + os.path.join(hd, 'main'),
              'main.npdm=' + os.path.join(hd, 'main.npdm')], r['log'], check=True)
        rc, lst = b.capture([PY, '-I', os.path.join(tools, 'build_pfs0.py'), '--list', nsp])
        r['pfs0'] = lst.splitlines()[:6]
        # the launcher (and the switch-tools reference packer for check 1's round trip, ~20 s, best effort)
        nd = os.path.join(d, 'nro')
        ref = os.path.join(d, 'ref')
        ref_bin = os.path.join(ref, 'bin')
        if b.sh(['bash', os.path.join(ROOT, 'tests', 'nx1', 'ref_tools.sh'), ref], r['log'], timeout=300):
            r.note('tests/nx1/ref_tools.sh failed: check 1 runs without the switch-tools round trip')
            ref_bin = None
        # the commit this package is built from: the NRO's NACP version and the stage record carry it, and
        # full_job.sh's publish_nro refuses to label any other package nro/<commit12> (V1 NX1 P1)
        r['port_commit'] = b.head
        # the guest's precompiled shader pack (link stage, LINK_OVERRIDES=render) goes into the NRO's RomFS
        pack = b.art('link', 'shader_pack')
        romfs = os.path.join(d, 'nro-romfs.img') if pack and os.path.isfile(pack) else None
        if romfs:
            b.sh([PY, '-I', os.path.join(ROOT, 'toolchain', 'tools', 'romfs.py'), '--build', romfs, pack], r['log'], check=True)
        else:
            r.note('no shader pack (the guest links no native renderer): fnv.nro has no RomFS, NX1 check 9 fails')
        nro_rc = b.sh(['bash', os.path.join(ROOT, 'toolchain', 'nro', 'build.sh'), nd] + ([b.head[:12]] if b.head else []),
                      r['log'],
                      env=b.env({'NX1_REF_BIN': ref_bin, 'NRO_ROMFS': romfs}), timeout=600)
        nro = os.path.join(nd, 'fnv.nro')
        if nro_rc or not os.path.isfile(nro):
            r.note('toolchain/nro/build.sh exited %d: no fnv.nro' % nro_rc)
            r['status'] = 'warn'
            nro = None
        sd = os.path.join(d, 'sd')
        fnv = os.path.join(sd, 'switch', 'fnv')
        os.makedirs(fnv)
        shutil.copyfile(nsp, os.path.join(fnv, 'fnv-exefs.nsp'))
        if nro:
            shutil.copyfile(nro, os.path.join(fnv, 'fnv.nro'))
        gnso = b.art('guest', 'guest.nso') if b.ok('guest') else None
        if gnso and os.path.isfile(gnso):
            shutil.copyfile(gnso, os.path.join(fnv, 'guest.nso'))
        with open(os.path.join(fnv, 'README.txt'), 'w') as f:
            f.write('fnv-nx: copy your own FalloutNV.exe and Data/ folder into this folder (sdmc:/switch/fnv/).\n'
                    'Start: hold R while starting any installed game, then pick fnv-nx in hbmenu.\n'
                    'Logs for a bug report: launcher.log, host.log, crash.log in this folder.\n')
        files = []
        for dp, _, fn in os.walk(sd):
            for n in sorted(fn):
                p = os.path.join(dp, n)
                files.append((os.path.relpath(p, sd), os.path.getsize(p)))
        r['sd_files'] = files
        gelf = b.art('guest', 'fnvnx.elf') if b.ok('guest') else None
        argv = [PY, '-I', os.path.join(ROOT, 'tests', 's5', 'check_package.py'), sd]
        if gelf and os.path.isfile(gelf):
            argv += ['--elf', gelf]
        r['check_package_rc'] = b.sh(argv, r['log'], timeout=600)
        r.metric('check_package_rc', r['check_package_rc'])
        nx1 = os.path.join(d, 'nx1.json')
        argv = [PY, '-I', os.path.join(ROOT, 'tests', 'nx1', 'run.py'), '--build', b.out, '--json', nx1]
        if ref_bin:
            argv += ['--ref-bin', ref_bin]
        b.sh(argv, r['log'], timeout=1200)
        try:
            nj = json.load(open(nx1))
            r.metric('nx1_open', nj['open'])
            r['nx1'] = [(c['n'], c['check'], c['status'], c['note'][:160]) for c in nj['checks']]
            r.artifact('nx1.json', nx1)
        except (OSError, ValueError, KeyError):
            r.note('tests/nx1/run.py wrote no nx1.json')
        self.ilp32_wrap(b, r, d)
        r.metric('package_exefs_bytes', os.path.getsize(nsp))
        r.metric('package_sd_bytes', sum(s for _, s in files))
        r.artifact('exefs.nsp', nsp)
        r.artifact('sd', sd)
        if nro:
            r.artifact('nro', nd)
        r['output_id'] = sha(file_hash(nsp), file_hash(gnso), file_hash(nro))
        r['summary'] = 'SD tree switch/fnv %s (%d files); fnv.nro %s; check_package exit %d; nx1 open %s' % (
            human(sum(s for _, s in files)), len(files), human(os.path.getsize(nro)) if nro else 'MISSING',
            r['check_package_rc'], r.get('metrics', {}).get('nx1_open', '?'))
        if r['check_package_rc']:
            r['status'] = 'warn'
        if not gnso:
            r.note('no guest.nso (guest stage did not succeed): SD tree holds the host only')
            r['status'] = 'warn'

    @staticmethod
    def ilp32_wrap(b, r, d):
        """Toolchain check: S13b's tests/s13/ilp32_wrap/check.sh with this build's fnvnx-a64 gcc. GCC's AArch64 ILP32
        code forms base + 32-bit index in 64 bits (add x, x, w, uxtw) on an integer-constant base, so it does not wrap
        at 2^32 the way the exe's does. The report (per flag set and form) is the artifact ilp32_wrap.txt; the metric
        ilp32_wrap_64bit counts the forms with the build's flags (the default row) that do not wrap. It reports,
        never gates: the sites are fixed in the prepared source (S1)."""
        prefix = b.art('toolchain', 'prefix')
        gcc = os.path.join(prefix, 'bin', 'aarch64-none-elf-gcc') if prefix else ''
        if not gcc or not os.path.isfile(gcc):
            r.note('ilp32_wrap: no fnvnx-a64 gcc (toolchain stage): not checked')
            return
        out = os.path.join(d, 'ilp32_wrap.txt')
        env = dict(b.env(), FNVNX_A64_CC=gcc)
        with open(out, 'w') as f:
            p = subprocess.run(['bash', os.path.join(ROOT, 'tests', 's13', 'ilp32_wrap', 'check.sh')], env=env,
                               stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=300)
        rows = [ln.split(None, 2) for ln in open(out, errors='replace') if ln.strip()]
        r.artifact('ilp32_wrap.txt', out)
        if p.returncode or not rows or rows[0][0] == 'skip:':
            r.note('ilp32_wrap: check.sh exit %d: %s' % (p.returncode, ' '.join(map(' '.join, rows[-2:]))[:200]))
            return
        bad = [w[1] for w in rows if len(w) == 3 and w[0] == 'default' and 'NOT' in w[2]]
        r.metric('ilp32_wrap_64bit', len(bad))
        if bad:
            r.note('ilp32_wrap: fnvnx-a64 forms %d of %d constant-base address forms in 64 bits (%s): such a site '
                   'does not wrap at 2^32 like the exe (tests/s13/ilp32_wrap, S13b)' % (
                       len(bad), sum(1 for w in rows if w[0] == 'default'), ', '.join(bad)))


# ---------------------------------------------------------------------------------------------------- 12 tests

class Suite:
    def __init__(self, name, inputs, cmds, needs=(), informational=False, fixed_inputs=None, after=(), work=()):
        self.name, self.inputs, self.cmds, self.needs, self.after = name, inputs, cmds, needs, after
        self.work = work     # re-creatable trees (clones, third-party builds): removed after a pass (CLEAN_WORK)
        self.informational = informational
        self.fixed_inputs = fixed_inputs or (lambda b: [])


def summarize_output(text):
    """One line for the report: the runner's own result lines (unittest, key=value metrics, PASS/ok lines)."""
    lines = [l.strip() for l in text.splitlines() if l.strip() and not l.lstrip().startswith(('|', '$ ', '[exit'))]
    pats = [r'^Ran \d+ tests?', r'^(OK|FAILED)\b', r'^\w+=\S+(\s+\w+=\S+)+$', r'\d+ tests?, \d+ checks',
            r'\bPASS\b', r'\b(passed|failed)\b', r': ok\b', r'^ok\b']
    found = []
    for rx in pats:
        hit = next((l for l in reversed(lines) if re.search(rx, l, re.I)), None)
        if hit and hit not in found:
            found.append(hit)
        if len(found) >= 2:
            break
    return clean_msg(' / '.join(found))[:200]


def clean_msg(s):
    """Shorter messages: paths relative to ports/fnv-nx, the cross tools by name."""
    s = s.replace(ROOT + '/', '')
    return re.sub(r'\S*/bin/(?:\.\./)*(?:[\w.-]+/)*(ld|aarch64-none-elf-[\w+-]+|collect2)\b', r'\1', s)


def suites(b):
    ext = b.art('fetch', 'ext') or os.path.join(ROOT, 'ext')
    t = os.path.join(b.out, 'tests')
    libnx = os.path.join(ext, 'libnx')
    tc = b.art('toolchain', 'prefix') or ''
    s = []
    lt = sorted(f for f in os.listdir(os.path.join(ROOT, 'tools', 'link')) if re.match(r'test_.*\.py$', f))
    s.append(Suite('s2-link', ['tools/link'], [[PY, '-I', 'tools/link/' + f] for f in lt],
                   needs=('qemu-aarch64', 'llvm-undname')))
    if os.path.isdir(os.path.join(ROOT, 'tests', 's3')):
        s.append(Suite('s3', ['tests/s3', 'loader'], [
            ['env', 'FNVNX_S3_OUT=' + os.path.join(t, 's3'), PY, '-I', 'tests/s3/test_s3.py'],
            ['make', '-C', 'loader', 'OUT=' + os.path.join(t, 's3', 'ilp32'), 'ilp32-check']]))
    for n in ('s4', 's7'):
        if os.path.isfile(os.path.join(ROOT, 'tests', n, 'Makefile')):
            s.append(Suite(n, ['tests/' + n, 'platform', 'shim/include'], [
                ['make', '-C', 'tests/' + n, 'OUT=' + os.path.join(t, n), 'test'],
                ['make', '-C', 'tests/' + n, 'OUT=' + os.path.join(t, n), 'LIBNX=' + libnx, 'ilp32']]))
    if os.path.isdir(os.path.join(ROOT, 'tests', 's5')):
        s.append(Suite('s5-tools', ['tests/s5/test_tools.py', 'toolchain/tools'], [
            [PY, '-I', 'tests/s5/test_tools.py']]))
        hello = os.path.join(t, 's5', 'hello')
        s.append(Suite('s5-emu-hello', ['tests/s5', 'toolchain', 'startup'], [
            ['bash', 'tests/s5/hello/build.sh', hello, b.cfg['PROGRAM_ID']],
            [PY, '-I', 'tests/s5/emu_guest.py', os.path.join(hello, 'guest.nso')]], needs=('DEVKITPRO', 'unicorn')))
        if os.path.isfile(os.path.join(ROOT, 'tests', 's5', 'rt', 'build.sh')):
            s.append(Suite('s5-rt', ['tests/s5', 'toolchain'], [
                ['bash', 'tests/s5/rt/build.sh', os.path.join(t, 's5', 'rt')]], needs=('DEVKITPRO', 'unicorn')))
    if os.path.isdir(os.path.join(ROOT, 'tests', 's6b')):
        deps, bld = os.path.join(t, 's6b', 'deps'), os.path.join(t, 's6b', 'host')
        s.append(Suite('s6b', ['tests/s6b', 'render/shaders'], [
            ['env', 'FNVNX_SHADERS_DEPS=' + deps, 'FNVNX_SHADERS_BUILD=' + bld, 'bash', 'render/shaders/build.sh',
             '--tests'],
            [PY, '-I', 'tests/s6b/run.py', '--build', bld, '--out', os.path.join(t, 's6b', 'out')]],
            needs=('cmake', 'ninja', 'meson'), work=(deps,)))
        if os.path.isfile(os.path.join(ROOT, 'tests', 's6d', 'run.py')):
            # nvc0 shader cache over the S6b and S6d corpora: x86-64 build of Nine's shader path (S6d) + S6b's asm
            w = os.path.join(t, 's6d', 'work')
            mesa = os.path.join(b.out, 'nine', 'work', 'src', 'mesa')
            env = ['env'] + (['MESA_SRC=' + mesa] if b.cfg['NINE'] == '1' and b.ok('nine') and os.path.isdir(mesa)
                             else [])
            s.append(Suite('s6d', ['tests/s6d', 'tests/s6b/shaders.s6b', 'render/nine/host', 'render/nine/patches',
                                   'render/nine/precompile'], [
                env + ['bash', 'render/nine/host/build_host.sh', w, str(b.jobs)],
                [PY, '-I', 'tests/s6d/run.py', '--host', os.path.join(w, 'out'), '--asm', os.path.join(bld, 's6b_asm'),
                 '--out', os.path.join(t, 's6d', 'out')]], after=('s6b',), work=(w,)))
    if os.path.isfile(os.path.join(ROOT, 'tests', 's8', 'run.sh')):
        s.append(Suite('s8', ['tests/s8', 'shim/include'], [['bash', 'tests/s8/run.sh', os.path.join(t, 's8')]]))
    if os.path.isfile(os.path.join(ROOT, 'tests', 's10', 'run.sh')):
        s.append(Suite('s10', ['tests/s10', 'startup', 'tests/s5/emu_guest.py'], [
            ['env', 'FNVNX_A64=' + tc, 'bash', 'tests/s10/run.sh', os.path.join(t, 's10')]]))
    if b.cfg['NINE'] == '1' and os.path.isfile(os.path.join(ROOT, 'render', 'nine', 'test', 'run_gate.sh')):
        # S6c's host gate of the D3D9 proxy path (Mesa st/nine + softpipe for x86-64, ILP32 test guest)
        s.append(Suite('nine-gate', ['render/nine'], [
            ['bash', 'render/nine/test/run_gate.sh', os.path.join(t, 'nine-gate'), str(b.jobs)]],
            needs=('DEVKITPRO', 'unicorn', 'meson', 'ninja'), work=(os.path.join(t, 'nine-gate', 'src'),)))
    known = {'s3', 's4', 's5', 's6b', 's6d', 's7', 's8', 's10'}
    for n in sorted(os.listdir(os.path.join(ROOT, 'tests'))):      # suites added later by other streams
        p = os.path.join(ROOT, 'tests', n)
        if n in known or not os.path.isdir(p):
            continue
        rel = 'tests/' + n
        if os.path.isfile(os.path.join(p, 'Makefile')):
            cmds = [['make', '-C', rel, 'OUT=' + os.path.join(t, n), 'test']]
        elif os.path.isfile(os.path.join(p, 'run.sh')):
            cmds = [['bash', rel + '/run.sh', os.path.join(t, n)]]
        elif os.path.isfile(os.path.join(p, 'run.py')):
            cmds = [[PY, '-I', rel + '/run.py']]
        else:
            cmds = [[PY, '-I', rel + '/' + f] for f in sorted(os.listdir(p)) if re.match(r'test_.*\.py$', f)]
        if cmds:
            s.append(Suite(n, [rel], cmds))
    # the game guest under the emulator is the boot stage (S5's emulated boot + frontier), not a suite
    return s


def suite_fp(b, su):
    return sha(tree_hash(*su.inputs), up_id(b, 'toolchain'), up_id(b, 'fetch'), su.cmds, su.fixed_inputs(b))


class Tests(Stage):
    name = 'tests'
    soft = ('toolchain', 'fetch')

    def fingerprint(self, b):
        # every selected suite's own fingerprint: unchanged suites that passed are cached anyway; a failing suite
        # makes the stage `fail`, which is never cached, so it runs again
        want = b.cfg['TESTS']
        sel = None if want == 'all' else set(x.strip() for x in want.split(',') if x.strip() and want != 'none')
        return ['tests-v2', want, [(su.name, suite_fp(b, su)) for su in suites(b) if sel is None or su.name in sel]]

    def outputs_present(self, b):
        return os.path.isfile(os.path.join(b.out, 'tests', 'results.json'))

    def run(self, b, r):
        d = b.sdir('tests')
        want = b.cfg['TESTS']
        sel = None if want == 'all' else set() if want == 'none' else \
            set(x.strip() for x in want.split(',') if x.strip())
        resf = os.path.join(d, 'results.json')
        try:
            prev = json.load(open(resf))
        except (OSError, ValueError):
            prev = {}
        have = {'DEVKITPRO': bool(b.art('toolchain', 'devkitpro')),
                'unicorn': subprocess.run([PY, '-I', '-c', 'import unicorn'], capture_output=True).returncode == 0}
        results = {}
        force = 'tests' in b.cfg['FORCE'].split(',') or b.cfg['FORCE'] == 'all'
        for su in suites(b):
            if sel is not None and su.name not in sel:
                continue
            fp = suite_fp(b, su)
            p = prev.get(su.name)
            if p and p.get('fingerprint') == fp and p.get('status') == 'pass' and not force:   # failures rerun
                p['cached'] = True
                p.pop('not_selected', None)
                if os.path.isfile(p.get('log', '')):      # summary rules may have improved since
                    p['summary'] = summarize_output(open(p['log'], errors='replace').read()) or p.get('summary')
                results[su.name] = p
                continue
            miss = [n for n in su.needs if not (have.get(n) if n in have else b.which(n))]
            miss += ['suite %s passing' % a for a in su.after if results.get(a, {}).get('status') != 'pass']
            log = os.path.join(d, su.name + '.log')
            open(log, 'w').close()
            res = {'fingerprint': fp, 'log': log, 'cached': False, 'informational': su.informational}
            if miss:
                res.update(status='skip', summary='missing: ' + ', '.join(miss), seconds=0)
                results[su.name] = res
                continue
            b.say('tests: %s' % su.name)
            t0 = time.time()
            rc = 0
            timeout = 5400
            for c in su.cmds:
                rc = b.sh(c, log, cwd=ROOT, timeout=timeout)
                if rc:
                    break
            text = open(log, errors='replace').read()
            res.update(status='pass' if rc == 0 else 'fail', rc=rc, seconds=round(time.time() - t0, 1),
                       summary=summarize_output(text) or ('exit %d' % rc))
            if rc == 0 and su.work:
                res['work_freed'] = remove_work(b, None, su.work, su.name)
            if rc:
                lines = [l.strip() for l in text.splitlines() if l.strip()]
                res['tail'] = lines[-8:]
                first = next((l for l in lines if re.search(r'error:|\bFAIL|Traceback|Error:|STOPPED|mismatch', l)
                              and not re.search(r'\*\*\* \[|Error \d+$', l)), None)
                if first:
                    res['summary'] = clean_msg(first)[:220]
            results[su.name] = res
            ck = dict(prev)
            ck.update(results)
            json.dump(ck, open(resf + '.tmp', 'w'), indent=1, sort_keys=True)
            os.replace(resf + '.tmp', resf)       # checkpoint: a restart keeps the suites already passed
        for k, v in prev.items():     # suites not selected this run keep their last result
            if k not in results and k != 'emu-game':      # emu-game: now the boot stage
                v['not_selected'] = True
                v['cached'] = True
                results[k] = v
        json.dump(results, open(resf, 'w'), indent=1, sort_keys=True)
        r['suites'] = results
        gates = {k: v for k, v in results.items() if not v.get('informational') and not v.get('not_selected')}
        npass = sum(1 for v in gates.values() if v['status'] == 'pass')
        nfail = sum(1 for v in gates.values() if v['status'] == 'fail')
        nskip = sum(1 for v in gates.values() if v['status'] == 'skip')
        r.metric('tests_suites', len(gates))
        r.metric('tests_pass', npass)
        r.metric('tests_fail', nfail)
        r.metric('tests_skip', nskip)
        r['output_id'] = sha(sorted((k, v['status']) for k, v in results.items()))
        r['summary'] = '%d suites: %d pass, %d fail, %d skipped%s' % (
            len(gates), npass, nfail, nskip, ''.join('; %s (info): %s' % (k, v['status'])
                                                    for k, v in results.items() if v.get('informational')))
        if nfail:
            r['status'] = 'fail'
            r['error'] = 'failing suites: ' + ', '.join(k for k, v in gates.items() if v['status'] == 'fail')
        elif nskip:
            r['status'] = 'warn'


STAGES = [Fetch(), Toolchain(), Prepare(), Census(), Compile(), Link(), Guest(), Boot(), Nine(), Host(), Package(),
          Tests()]
BYNAME = {s.name: s for s in STAGES}


# ------------------------------------------------------------------------------------------------------- driver

def closure(targets):
    need = set()

    def add(n):
        if n in need:
            return
        need.add(n)
        for x in BYNAME[n].deps + BYNAME[n].soft:
            add(x)
    for t in targets:
        add(t)
    return [s for s in STAGES if s.name in need]


def provenance(b, st):
    """-> (prepared_from record of the tree-derived outputs `st` consumes, or None; why they must not be consumed,
    or None). The record travels with the outputs: prepare writes it, every stage in `prov` copies its inputs'."""
    ups = [u for u in st.prov if b.ok(u)]
    if not ups:
        return None, None
    pfs = [(u, b.rec(u).get('prepared_from')) for u in ups]
    pf = pfs[0][1]
    missing = [u for u, p in pfs if not p]
    commits = sorted({p.get('port_commit') or 'unknown' for _, p in pfs if p})
    if missing:
        return pf, 'outputs of %s carry no provenance record (made by an older pipeline)' % ', '.join(missing)
    if len(commits) > 1:
        return pf, 'outputs prepared from different commits (%s)' % ', '.join('%s %s' % (
            u, short(p.get('port_commit'))) for u, p in pfs)
    if not pf.get('port_commit'):
        return pf, 'inputs prepared from an unknown commit'
    if pf.get('port_commit') != b.head:
        return pf, 'inputs prepared from %s, the head is %s' % (short(pf.get('port_commit')), short(b.head))
    return pf, None


def execute(b, targets):
    force = set(b.cfg['FORCE'].split(',')) if b.cfg['FORCE'] else set()
    failed = False
    for st in closure(targets):
        r = Record(stage=st.name, log=os.path.join(b.sdir(st.name), 'stage.log'))
        if not st.enabled(b):
            r.update(status='disabled', summary='disabled by configuration', ran=False, run_id=b.run_id)
            b.save(r)
            continue
        missing = [x for x in st.deps if not b.ok(x)]
        if missing:
            r.update(status='skipped', summary='needs %s (%s)' % (', '.join(missing), ', '.join(
                b.rec(x).get('status', '?') for x in missing)), ran=False, run_id=b.run_id)
            b.save(r)
            b.say('%-9s skipped: needs %s' % (st.name, ', '.join(missing)))
            continue
        # J-0054: tree-derived outputs are consumed only when they were prepared from the current head
        pf, stale = provenance(b, st)
        if stale and b.cfg['FORCE_STALE'] != '1':
            r.update(status='fail', ran=False, run_id=b.run_id, prepared_from=pf, stale=stale, summary='refused',
                     error='stale inputs: %s (prepare again at this head, or FORCE_STALE=1)' % stale)
            b.save(r)
            failed = True
            b.say('%-9s REFUSED  %s' % (st.name, r['error']))
            continue
        try:
            fp = sha(st.fingerprint(b)) if not st.always else None
        except StageError as ex:
            r.update(status='fail', ran=False, run_id=b.run_id, error=clean_msg(str(ex)), summary=clean_msg(str(ex)))
            b.save(r)
            failed = True
            b.say('%-9s fail     %s' % (st.name, r['error']))
            continue
        prev = b.rec(st.name)
        if (fp and prev.get('fingerprint') == fp and prev.get('status') in ('ok', 'warn') and st.outputs_present(b)
                and st.name not in force and 'all' not in force):
            prev['ran'] = False
            prev['cached_at'] = b.run_id
            if st.prov:
                # same fingerprint = the same inputs by content (it holds the upstream output ids): the outputs are
                # what this tree gives, so they take its (possibly re-prepared, identical) provenance
                if pf is not None:
                    prev['prepared_from'] = pf
                base = prev.get('status_run', prev.get('status'))
                prev['status'] = 'warn' if stale and base == 'ok' else base
                prev.pop('stale', None)
                if stale:
                    prev['stale'] = stale
            b.save(prev)
            b.say('%-9s cached   %s' % (st.name, prev.get('summary', '')))
            continue
        open(r['log'], 'w').close()
        r.update(status='running', fingerprint=None, started=now_iso(), ran=True, run_id=b.run_id)
        b.save(r)          # visible to `make status`; an interrupted stage is not mistaken for a finished one
        r.update(status='ok', fingerprint=fp)
        if pf is not None:
            r['prepared_from'] = pf
        t0 = time.time()
        b.say('%-9s running' % st.name)
        try:
            st.run(b, r)
        except StageError as ex:
            r['status'] = 'fail'
            r['error'] = clean_msg(str(ex))
        except Exception as ex:   # a bug in a stage must not stop the others
            import traceback
            r['status'] = 'fail'
            r['error'] = '%s: %s' % (type(ex).__name__, ex)
            with open(r['log'], 'a') as f:
                f.write(traceback.format_exc())
        r['seconds'] = round(time.time() - t0, 1)
        r['disk_bytes'] = du(os.path.join(b.out, st.name))
        r['status_run'] = r['status']
        if stale and r['status'] != 'fail':
            r['stale'] = stale
            r.note('FORCE_STALE=1: consumed %s' % stale)
            if r['status'] == 'ok':
                r['status'] = 'warn'
        if r['status'] == 'fail':
            r['fingerprint'] = None
            r.setdefault('summary', r.get('error', ''))
            r['log_tail'] = Build.tail(r['log'], 15)
            failed = True
        b.save(r)
        b.ran.append(st.name)
        b.say('%-9s %-7s %s (%.0f s)%s' % (st.name, r['status'], r.get('summary', ''), r['seconds'],
                                          ('\n          error: ' + r['error']) if r.get('error') else ''))
    return failed


def parse_args(argv):
    cfg = {k: os.environ.get(k, d) or d for k, d, _ in CONFIG}
    for k in ('EXT', 'FNVNX_TC', 'DEVKITPRO', 'NINE_SRC', 'FORCE'):
        cfg[k] = os.environ.get(k, '')
    if 'OVERRIDE' in os.environ:   # an empty OVERRIDE turns the overrides off, as documented (V1 OVR-E1)
        cfg['OVERRIDE'] = os.environ['OVERRIDE']
    targets = []
    for a in argv:
        if '=' in a and re.match(r'^[A-Z_][A-Z0-9_]*=', a):
            k, v = a.split('=', 1)
            if k not in cfg:
                sys.exit('unknown variable %s (known: %s)' % (k, ', '.join(cfg)))
            cfg[k] = v
        else:
            targets.append(a)
    return cfg, targets or ['all']


def main(argv):
    cfg, targets = parse_args(argv)
    b = Build(cfg)
    cmd = targets[0]
    if cmd == 'clean':
        names = targets[1:]
        if not names:
            shutil.rmtree(b.out, ignore_errors=True)
            print('removed %s' % b.out)
        for n in names:
            shutil.rmtree(os.path.join(b.out, n), ignore_errors=True)
            print('removed %s' % os.path.join(b.out, n))
        return 0
    if cmd == 'clean-work':     # re-creatable work trees of every stage and suite, whatever their state
        b.cfg['CLEAN_WORK'] = '1'
        t = os.path.join(b.out, 'tests')
        paths = [os.path.join(b.out, 'prepare', 'staging'), os.path.join(b.out, 'nine', 'work', 'src', 'mesa', 'build-nine'),
                 os.path.join(t, 's6b', 'deps'), os.path.join(t, 's6d', 'work'), os.path.join(t, 'nine-gate', 'src'),
                 os.path.join(b.out, 'guest', 'obj'), os.path.join(b.out, 'guest', 'nine')]
        print('freed %s' % human(remove_work(b, None, paths, 'work trees')))
        return 0
    if cmd == 'status':
        print('head %s' % (b.head or 'unknown'))
        for st in STAGES:
            r = b.rec(st.name)
            pf = r.get('prepared_from')
            print('%-9s %-9s %-11s %s' % (st.name, r.get('status'), short(pf.get('port_commit')) if pf else '-',
                                          r.get('summary', '')))
        return 0
    if cmd == 'help':
        print(__doc__)
        for k, d, h in CONFIG:
            print('  %-14s %s (default: %s)' % (k, h, d or '-'))
        return 0
    for t in targets:
        if t not in BYNAME and t not in ('all', 'report'):
            sys.exit('unknown stage %s (stages: %s, all, report, status, clean)' % (t, ' '.join(STAGE_NAMES)))
    stages = STAGE_NAMES if 'all' in targets else [t for t in targets if t != 'report']
    b.explicit = {t for t in targets if t in BYNAME}
    if not stages:   # `report` alone: re-render the last run, no new history entry
        try:
            b.run_id = json.load(open(os.path.join(b.out, 'report.json')))['run_id']
        except (OSError, ValueError, KeyError):
            pass
    b.say('fnv-nx pipeline run %s: %s (port %s, NINE=%s PROGRAM_ID=%s JOBS=%d%s)' % (
        b.run_id, ' '.join(targets), short(b.head), cfg['NINE'], cfg['PROGRAM_ID'], b.jobs,
        ', UNITS=%s' % cfg['UNITS'] if cfg['UNITS'] else ''))
    failed = execute(b, stages) if stages else False
    try:
        path = reportmod.write(b, STAGES)
        b.say('report: %s' % path)
    except Exception as ex:      # a report bug must not hide the stage results (build/<stage>/stage.json)
        import traceback
        traceback.print_exc()
        b.say('report: FAILED to render (%s: %s); stage records are in %s/<stage>/stage.json' % (
            type(ex).__name__, ex, b.out))
        return 1
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
