#!/usr/bin/env python3
"""build.py - the fnv-nx build pipeline (stream S9-build; docs/BUILD.md). Entry point: ports/fnv-nx/Makefile.

    tools/pipeline/build.py [VAR=value ...] [stage ...]      run the stages (and what they need); default: all
    tools/pipeline/build.py [VAR=value ...] status           table of the recorded stage states
    tools/pipeline/build.py [VAR=value ...] clean [stage...] remove build/ (or the named stage directories)

Stages, in order: fetch toolchain prepare census compile link guest nine host package tests; `report` runs last
on every invocation and writes build/report.md (+ report.json, history/). Each stage owns build/<stage>/ and
records build/<stage>/stage.json (status ok|warn|fail|skipped|disabled, fingerprint, metrics, artifacts, log). A
stage whose input fingerprint and recorded status are unchanged is not run again (`cached`); inside compile,
link and tests the tools' own incremental rules apply as well. A failing stage never stops the pipeline: the
stages that need it are `skipped`, the others run; the exit code is 1 when any stage failed.

Everything under build/ is derived from the decomp or the player's game: local only, never committed.
"""
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
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
    ('TC_DL', '', 'only to build the toolchain: directory with the binutils/gcc/newlib tarballs'),
    ('JOBS', str(os.cpu_count() or 4), 'parallel jobs'),
    ('GAME_CXX', 'auto', 'compiler of the game units: tc (fnvnx-a64 g++), standin (aarch64-linux-gnu-g++), a command; '
                         'auto = tc when the toolchain is there'),
    ('OPT', '-O2', 'optimisation flags of the game units (tools/link/compile.sh $OPT)'),
    ('CENSUS', '1', '0: skip the -fsyntax-only census'),
    ('OVERRIDE', '', 'fnvlink resolve --override table (native replacements)'),
    ('EXTERN_TEXT', '', 'fnvlink resolve --extern-text list (S4 addr_ids.txt)'),
    ('GUEST_LINK', 'auto', 'guest link script: auto = toolchain/game/build.sh (S5) when present, else the adapter '
                           'tools/pipeline/guest_link.py'),
    ('VORBIS', '', 'libogg/libvorbis ILP32 prefix for the guest link (S5 script only)'),
    ('NINE', '0', '1: Gallium Nine (render/nine) in the host module; 0: no D3D9 backend in the host (null device)'),
    ('NINE_SRC', '', 'directory holding mesa/ and libdrm_nouveau/ checkouts to copy instead of cloning'),
    ('PROGRAM_ID', '0100000000000000', 'title whose exefs is overridden (SD path atmosphere/contents/<id>/)'),
    ('TESTS', 'all', 'comma list of test suites to run (names in docs/BUILD.md), or all'),
    ('EMU_TIMEOUT', '900', 'seconds for the emulator run of the game guest (informational)'),
    ('FORCE', '', 'comma list of stages to rerun even when cached (all = every stage)'),
]
STAGE_NAMES = ['fetch', 'toolchain', 'prepare', 'census', 'compile', 'link', 'guest', 'nine', 'host', 'package',
               'tests']


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
        self.run_id = time.strftime('%Y%m%d-%H%M%S', time.gmtime())
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
                self.recs[stage] = Record(json.load(open(p)))
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


class Fetch(Stage):
    name = 'fetch'
    always = True

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


class Toolchain(Stage):
    name = 'toolchain'
    always = True

    def run(self, b, r):
        log = r['log']
        d = b.sdir('toolchain')
        prefix = b.cfg['FNVNX_TC']
        dkp = b.cfg['DEVKITPRO']
        if not prefix and dkp and os.path.isdir(os.path.join(dkp, 'devkitA64')):
            prefix = os.path.realpath(os.path.join(dkp, 'devkitA64'))
            r.note('FNVNX_TC taken from $DEVKITPRO/devkitA64')
        own_prefix, own_dkp = os.path.join(d, 'prefix'), os.path.join(d, 'dkp')
        if not prefix and os.path.isfile(os.path.join(own_prefix, 'bin', 'aarch64-none-elf-gcc')):
            prefix = own_prefix
        if not prefix and b.cfg['TC_DL']:
            ext = b.art('fetch', 'ext') or os.path.join(ROOT, 'ext')
            bs = os.path.join(ext, 'buildscripts')
            if not os.path.isdir(bs):
                raise StageError('building the toolchain needs %s (git clone '
                                 'https://github.com/devkitPro/buildscripts)' % bs)
            b.say('toolchain: building fnvnx-a64 (toolchain/gcc/build.sh, ~45 min)')
            b.sh(['bash', os.path.join(ROOT, 'toolchain', 'gcc', 'build.sh'), b.cfg['TC_DL'],
                  os.path.join(d, 'work'), own_prefix, bs], log, check=True)
            prefix = own_prefix
        if not dkp and os.path.isfile(os.path.join(own_dkp, 'libnx', 'lib', 'libnx.a')):
            dkp = own_dkp
        if not dkp and prefix:
            ext = b.art('fetch', 'ext') or os.path.join(ROOT, 'ext')
            rules = os.path.join(ext, 'devkita64-rules')
            if os.path.isdir(rules) and os.path.isdir(os.path.join(ext, 'libnx')) and b.cfg['TC_DL']:
                b.say('toolchain: DEVKITPRO tree + libnx (toolchain/devkita64/setup.sh)')
                b.sh(['bash', os.path.join(ROOT, 'toolchain', 'devkita64', 'setup.sh'), prefix,
                      os.path.join(ext, 'libnx'), rules, own_dkp], log, check=True)
                dkp = own_dkp
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
            chk('fnvnx-a64 prefix', 'set FNVNX_TC or DEVKITPRO (or TC_DL to build it)', False)
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
            p = shutil.which(tool) or (shutil.which('llvm-undname-18') if tool == 'llvm-undname' else None)
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
            game_cxx = shutil.which('aarch64-linux-gnu-g++')
        else:
            game_cxx = want
            kind = 'custom'
        rc, out = b.capture(shlex.split(game_cxx or 'false') + ['--version'])
        gver = out.splitlines()[0] if not rc and out else None
        chk('GAME_CXX (%s)' % kind, game_cxx or '-', bool(gver))
        r.metric('game_cxx', '%s: %s' % (kind, gver))

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
        r.artifact('game_cxx', game_cxx)
        r.artifact('game_cxx_kind', kind)
        r['game_cxx_id'] = sha(game_cxx, gver)
        r['output_id'] = sha(prefix, dkp, game_cxx, gver, [c for c in checks])
        missing_req = [c[0] for c in checks if c[2] == 'MISSING']
        missing_opt = [c[0] for c in checks if c[2] == 'missing']
        r.metric('tc_missing_required', len(missing_req))
        r.metric('tc_missing_optional', len(missing_opt))
        r['summary'] = 'prefix %s, DEVKITPRO %s, GAME_CXX %s' % (prefix or '-', dkp or '-', kind)
        if missing_opt:
            r.note('optional tools missing: ' + ', '.join(missing_opt))
        if missing_req:
            r.note('required items missing: ' + ', '.join(missing_req))
            r['status'] = 'warn' if gver else 'fail'
            if not gver:
                raise StageError('no usable game compiler; missing: ' + ', '.join(missing_req))


def game_cxx(b):
    return b.art('toolchain', 'game_cxx') or shutil.which('aarch64-linux-gnu-g++') or 'aarch64-linux-gnu-g++'


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
    return tree_hash('tools/prepare.py', 'tools/rules', 'render/native/drop_units.txt')


class Prepare(Stage):
    name = 'prepare'
    deps = ('fetch',)

    def fingerprint(self, b):
        return ['prepare-v1', rules_hash(), up_id(b, 'fetch'), os.environ.get('FNVNX_PREPARE_SKIP', '')]

    def outputs_present(self, b):
        return os.path.isfile(os.path.join(b.out, 'prepare', 'tree', 'build', 'units.json'))

    def run(self, b, r):
        d = b.sdir('prepare')
        tree, staging = os.path.join(d, 'tree'), os.path.join(d, 'staging')
        shutil.rmtree(staging, ignore_errors=True)
        b.sh([PY, '-I', os.path.join(ROOT, 'tools', 'prepare.py'), b.art('fetch', 'decomp'), staging], r['log'],
             check=True)
        stats = os.path.join(staging, 'prepare_stats.tsv')
        rules = {}
        if os.path.isfile(stats):
            for line in open(stats):
                p = line.rstrip('\n').split('\t')
                if len(p) >= 3:
                    rules[p[0]] = (int(p[1].split()[0]), int(p[2].split()[0]))
            shutil.copyfile(stats, os.path.join(d, 'prepare_stats.tsv'))
        os.makedirs(tree, exist_ok=True)
        cid, added, changed, removed, same = sync_tree(staging, tree)
        with open(os.path.join(d, 'changed.txt'), 'w', errors='surrogateescape') as f:
            for tag, lst in (('A', added), ('M', changed), ('D', removed)):
                for p in lst:
                    f.write('%s\t%s\n' % (tag, p))
        units = json.load(open(os.path.join(tree, 'build', 'units.json')))['units']
        r.metric('prepare_rules', len(rules))
        r.metric('prepare_edits', sum(e for _, e in rules.values()))
        r.metric('prepare_units', len({u.get('source') for u in units}))
        r.metric('prepare_files_changed', len(added) + len(changed) + len(removed))
        r['rules'] = rules
        r.artifact('tree', tree)
        r['output_id'] = cid
        r['summary'] = '%d rules, %d edits; tree: %d added, %d changed, %d removed, %d unchanged' % (
            len(rules), sum(e for _, e in rules.values()), len(added), len(changed), len(removed), same)


# ---------------------------------------------------------------------------------------------------- 4 census

class Census(Stage):
    name = 'census'
    deps = ('prepare',)
    soft = ('toolchain',)

    def enabled(self, b):
        return b.cfg['CENSUS'] != '0'

    def fingerprint(self, b):
        return ['census-v1', tree_hash('tools/census.sh', 'shim/include'), up_id(b, 'prepare'), game_cxx_id(b),
                b.rec('fetch').get('metrics', {}).get('pin_sse2neon')]

    def outputs_present(self, b):
        return os.path.isfile(os.path.join(b.out, 'census', 'summary.txt'))

    def run(self, b, r):
        d = b.sdir('census')
        for sub in ('logs',):
            shutil.rmtree(os.path.join(d, sub), ignore_errors=True)
        b.say('census: -fsyntax-only of every unit (%s)' % os.path.basename(game_cxx(b)))
        b.sh(['bash', os.path.join(ROOT, 'tools', 'census.sh'), b.art('prepare', 'tree'), d, b.jobs], r['log'],
             env=b.env({'CXX': game_cxx(b)}), check=True)
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
        r['output_id'] = sha(units, clean, e.group(1) if e else None)
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


def unit_base(objdir, src):
    return os.path.join(objdir, src.replace('/', '_'))


class Compile(Stage):
    name = 'compile'
    deps = ('prepare',)
    soft = ('toolchain',)

    def fingerprint(self, b):
        return ['compile-v1', file_hash(os.path.join(ROOT, 'tools', 'link', 'compile.sh')), up_id(b, 'prepare'),
                game_cxx_id(b), b.cfg['OPT'], tree_hash('shim/include'),
                b.rec('fetch').get('metrics', {}).get('pin_sse2neon')]

    def outputs_present(self, b):
        return os.path.isfile(os.path.join(b.out, 'compile', 'objects.tsv'))

    def run(self, b, r):
        d = b.sdir('compile')
        obj = os.path.join(d, 'obj')
        os.makedirs(obj, exist_ok=True)
        tree = b.art('prepare', 'tree')
        cxx = game_cxx(b)
        toolset = sha(file_hash(os.path.join(ROOT, 'tools', 'link', 'compile.sh')), game_cxx_id(b), b.cfg['OPT'],
                      'compile-v1')
        tsf = os.path.join(d, 'toolset.id')
        if not os.path.isfile(tsf) or open(tsf).read().strip() != toolset:
            if os.listdir(obj):
                r.note('compiler, flags or compile.sh changed: all objects rebuilt')
            shutil.rmtree(obj)
            os.makedirs(obj)
            open(tsf, 'w').write(toolset + '\n')
        units = sorted({u['source'] for u in json.load(open(os.path.join(tree, 'build', 'units.json')))['units']})
        # objects whose headers changed (dependency files from -MD; compile.sh itself checks the source only)
        mt = {}

        def mtime(p):
            if p not in mt:
                try:
                    mt[p] = os.stat(p if os.path.isabs(p) else os.path.join(tree, p)).st_mtime_ns
                except OSError:
                    mt[p] = None
            return mt[p]

        stale = kept = 0
        rebuild = []
        for src in units:
            base = unit_base(obj, src)
            o = base + '.o'
            if os.path.isfile(o):
                deps = parse_depfile(base + '.o.d')
                ot = os.stat(o).st_mtime_ns
                if deps is not None and any((mtime(p) is None or mtime(p) > ot) for p in deps):
                    os.remove(o)
                    stale += 1
                else:
                    kept += 1
                    continue
            rebuild.append(src)
        lst = os.path.join(d, 'rebuild.list')
        with open(lst, 'w', errors='surrogateescape') as f:
            f.write(''.join(s + '\n' for s in rebuild))
        t0 = time.time()
        if rebuild:
            b.say('compile: %d units to build (%d up to date, %d stale by header)' % (len(rebuild), kept, stale))
            b.sh(['bash', os.path.join(ROOT, 'tools', 'link', 'compile.sh'), tree, obj, lst, b.jobs], r['log'],
                 env=b.env({'CXX': cxx + ' -MD', 'OPT': b.cfg['OPT']}), check=True)
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


class Link(Stage):
    name = 'link'
    deps = ('compile',)
    soft = ('toolchain',)

    def fingerprint(self, b):
        return ['link-v1', tree_hash('tools/link'), up_id(b, 'compile'), up_id(b, 'prepare'),
                file_hash(b.cfg['OVERRIDE']), file_hash(b.cfg['EXTERN_TEXT']), game_cxx_id(b)]

    def outputs_present(self, b):
        return os.path.isfile(os.path.join(b.out, 'link', 'out', 'aliases.ld'))

    def run(self, b, r):
        d = b.sdir('link')
        out, fixed = os.path.join(d, 'out'), os.path.join(d, 'fixed')
        os.makedirs(out, exist_ok=True)
        tree = b.art('prepare', 'tree')
        fl = os.path.join(ROOT, 'tools', 'link', 'fnvlink.py')
        units_json = os.path.join(tree, 'build', 'units.json')
        args = [units_json, b.art('compile', 'objects'), out]
        for opt, key in (('--override', 'OVERRIDE'), ('--extern-text', 'EXTERN_TEXT')):
            if b.cfg[key]:
                args += [opt, os.path.abspath(b.cfg[key])]
        # adapter: resolve writes out/objcopy/<obj>.* per object but never removes old ones; apply would then
        # rewrite objects of units that no longer compile (S2 interface gap, reported in docs/BUILD.md)
        shutil.rmtree(os.path.join(out, 'objcopy'), ignore_errors=True)
        env = b.env({'CXX': game_cxx(b)})
        log = r['log']
        b.say('link: fnvlink resolve')
        b.sh([PY, '-I', fl, 'resolve'] + args, log, env=env, check=True)
        lay = os.path.join(out, 'layouts.tsv')
        before = file_hash(lay)
        b.say('link: fnvlink probe (aggregate layouts)')
        rc = b.sh([PY, '-I', fl, 'probe', out, tree, b.jobs], log, env=env)
        if rc:
            r.note('probe exited %d; layouts as before' % rc)
        if file_hash(lay) != before:
            b.say('link: fnvlink resolve (with the new layouts)')
            shutil.rmtree(os.path.join(out, 'objcopy'), ignore_errors=True)
            b.sh([PY, '-I', fl, 'resolve'] + args, log, env=env, check=True)
        shutil.rmtree(fixed, ignore_errors=True)
        b.say('link: fnvlink apply')
        alog = os.path.join(d, 'apply.log')
        open(alog, 'w').close()
        b.sh([PY, '-I', fl, 'apply', out, fixed], alog, env=env, check=True)
        afail = [line.strip() for line in open(alog, errors='replace')
                 if line.startswith(('objcopy failed', 'as failed', 'cc addrmap.c failed'))]
        b.say('link: fnvlink check (trial link)')
        clog = os.path.join(d, 'check.txt')
        open(clog, 'w').close()
        b.sh([PY, '-I', fl, 'check', out, fixed], clog, env=env, check=True)
        m, classes = parse_stats(os.path.join(out, 'stats.txt'))
        cm, cclasses = parse_check(open(clog, errors='replace').read())
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
        for k in ('aliases.ld', 'addrmap.tsv', 'refs.tsv', 'abi.tsv', 'stats.txt', 'bridges.S'):
            r.artifact(k, os.path.join(out, k))
        r.artifact('out', out)
        r.artifact('fixed', fixed)
        r['output_id'] = sha(file_hash(os.path.join(out, 'aliases.ld')),
                             listing_id([os.path.join(fixed, n) for n in os.listdir(fixed)]))
        r['summary'] = '%s refs, %s resolved by address (%s %%), %s bridges; trial link: %s undefined, %s multidef' % (
            m.get('link_refs'), m.get('link_resolved'), r['metrics'].get('link_resolved_pct'), m.get('link_bridges'),
            cm.get('link_check_undefined'), cm.get('link_check_multidef'))
        if afail or cm.get('link_check_multidef'):
            r['status'] = 'warn'
        if afail:
            r.note('%d objects could not be rewritten by apply (see build/link/apply.log); they are left out' %
                   len(afail))


# ----------------------------------------------------------------------------------------------------- 7 guest

def s5_guest_script():
    p = os.path.join(ROOT, 'toolchain', 'game', 'build.sh')
    return p if os.path.isfile(p) else None


def guest_script(b):
    g = b.cfg['GUEST_LINK']
    if g == 'auto':
        return s5_guest_script() or os.path.join(HERE, 'guest_link.py')
    if g == 'adapter':
        return os.path.join(HERE, 'guest_link.py')
    return os.path.abspath(g)


class Guest(Stage):
    name = 'guest'
    deps = ('link', 'toolchain')
    soft = ('nine',)

    def fingerprint(self, b):
        scr = guest_script(b)
        own = scr.startswith(HERE)
        return ['guest-v1', scr, tree_hash(os.path.dirname(scr)) if not own else
                tree_hash('tools/pipeline/guest_link.py', 'tools/pipeline/guest_stub.c', 'tools/pipeline/undef.py'),
                tree_hash('toolchain/guest', 'toolchain/include', 'toolchain/tools', 'platform', 'loader',
                          'shim/include'),
                up_id(b, 'link'), up_id(b, 'toolchain'), b.cfg['NINE'], up_id(b, 'nine') if b.cfg['NINE'] == '1' else '',
                b.cfg['VORBIS']]

    def outputs_present(self, b):
        return os.path.isfile(os.path.join(b.out, 'guest', 'guest.nso'))

    def run(self, b, r):
        d = b.sdir('guest')
        scr = guest_script(b)
        lout, fixed = b.art('link', 'out'), b.art('link', 'fixed')
        if not b.art('toolchain', 'devkitpro') or not b.art('toolchain', 'prefix'):
            raise StageError('the guest link needs FNVNX_TC and DEVKITPRO')
        for n in ('fnvnx.elf', 'guest.nso', 'undefined.txt'):
            if os.path.exists(os.path.join(d, n)):
                os.remove(os.path.join(d, n))
        extra = {'FNVNX_TC': b.art('toolchain', 'prefix')}
        if b.cfg['NINE'] == '1':
            px = b.art('nine', 'guest_objs')
            extra['D3D9'] = 'external'
            if px:
                extra['EXTRA_OBJS'] = px
        else:
            extra['D3D9'] = 'null'
        r.artifact('script', b.rel(scr))
        if scr.startswith(HERE):
            r['method'] = 'pipeline adapter tools/pipeline/guest_link.py'
        elif scr.endswith(os.path.join('toolchain', 'game', 'build.sh')):
            r['method'] = 'S5 toolchain/game/build.sh' + ('' if scr == s5_guest_script() else ' (%s)' % scr)
        else:
            r['method'] = scr
        b.say('guest: ILP32 guest link via %s' % r['method'])
        argv = ([PY, '-I', scr] if scr.endswith('.py') else ['bash', scr]) + [lout, fixed, d]
        if b.cfg['VORBIS'] and not scr.endswith('.py'):
            argv.append(b.cfg['VORBIS'])
        rc = b.sh(argv, r['log'], env=b.env(extra), timeout=7200)
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
            for k in ('platform_units', 'platform_failed', 'multidef', 'game_objects'):
                if k in s:
                    r.metric('guest_' + k, s[k] if not isinstance(s[k], list) else len(s[k]))
        for n in ('fnvnx.elf', 'guest.nso'):
            p = os.path.join(d, n)
            if os.path.isfile(p):
                r.artifact(n, p)
                r.metric('guest_%s_bytes' % n.replace('.', '_'), os.path.getsize(p))
        nso = os.path.join(d, 'guest.nso')
        if rc or not os.path.isfile(nso):
            raise StageError('guest link failed (exit %d), see %s: %s' % (rc, b.rel(r['log']),
                                                                         ' | '.join(Build.tail(r['log'], 3))))
        rc, dump = b.capture([PY, '-I', os.path.join(ROOT, 'toolchain', 'tools', 'elf2nso.py'), '--dump', nso])
        r['nso_dump'] = dump.splitlines()[:12]
        if rc:
            r.note('elf2nso --dump failed on guest.nso')
            r['status'] = 'warn'
        r['output_id'] = file_hash(nso)
        r['summary'] = '%s: guest.nso %s, ELF %s, %s undefined symbols bound to trap stubs' % (
            r['method'].split(' ')[0] if 'S5' in r['method'] else 'adapter', human(os.path.getsize(nso)),
            human(r.get('metrics', {}).get('guest_fnvnx_elf_bytes')), r.get('metrics', {}).get('guest_undefined'))
        if r.get('metrics', {}).get('guest_undefined'):
            r['status'] = 'warn'


# ------------------------------------------------------------------------------------------------------ 8 nine

class Nine(Stage):
    name = 'nine'
    deps = ('toolchain',)
    soft = ('fetch',)     # sse2neon for the guest proxy (it includes the decomp dialect header)

    def enabled(self, b):
        return b.cfg['NINE'] == '1'

    def fingerprint(self, b):
        return ['nine-v1', tree_hash('render/nine', 'shim/include/d3d9.h', 'shim/include/d3d9types.h',
                                     'shim/include/d3d9caps.h'), up_id(b, 'toolchain'), b.cfg['NINE_SRC']]

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
        b.say('nine: render/nine/build.sh (Mesa st/nine + nouveau, LP64)')
        b.sh(['bash', os.path.join(ROOT, 'render', 'nine', 'build.sh'), work, b.jobs], r['log'], timeout=4 * 3600,
             check=True)
        libdir = os.path.join(work, 'out', 'lib')
        libs = sorted(n for n in os.listdir(libdir) if n.endswith('.a'))
        r['libs'] = {n: os.path.getsize(os.path.join(libdir, n)) for n in libs}
        r.metric('nine_libs', len(libs))
        r.metric('nine_libs_bytes', sum(r['libs'].values()))
        px = os.path.join(d, 'proxy')
        os.makedirs(px, exist_ok=True)
        b.sh([PY, '-I', os.path.join(ROOT, 'render', 'nine', 'gen_proxy.py'),
              os.path.join(ROOT, 'shim', 'include', 'd3d9.h'), px], r['log'], check=True)
        mt = os.path.join(px, 'methods.tsv')
        if os.path.isfile(mt):
            kinds = {}
            for line in open(mt):     # interface, slot, method, kind (auto|manual), note
                f = line.rstrip('\n').split('\t')
                if len(f) < 4 or f[3] == 'kind':
                    continue
                kinds[f[3]] = kinds.get(f[3], 0) + 1
            r['proxy_kinds'] = kinds
        # guest side of the proxy: ILP32 object for the guest link (S5's D3D9=external + EXTRA_OBJS)
        gcpp = os.path.join(px, 'nine_proxy_guest.cpp')
        gobj = os.path.join(px, 'nine_proxy_guest.o')
        if os.path.isfile(gcpp):
            cxx = os.path.join(b.art('toolchain', 'prefix'), 'bin', 'aarch64-none-elf-g++')
            rc = b.sh([cxx, '-mabi=ilp32', '-march=armv8-a+crc+crypto', '-mtune=cortex-a57', '-fno-pie',
                       '-ftls-model=local-exec', '-O2', '-std=gnu++17', '-fshort-wchar', '-fms-extensions',
                       '-fpermissive', '-w', '-include', os.path.join(ROOT, 'shim', 'include', 'fnvnx_compat.h'),
                       '-I' + os.path.join(ROOT, 'shim', 'include'), '-I' + os.path.join(ROOT, 'render', 'nine'),
                       '-I' + os.path.join(ROOT, 'toolchain', 'include'),
                       '-I' + os.path.join(b.art('fetch', 'ext') or os.path.join(ROOT, 'ext'), 'sse2neon'),
                       '-c', gcpp, '-o', gobj], r['log'])
            if rc == 0:
                r.artifact('guest_objs', gobj)
            else:
                r.note('nine_proxy_guest.cpp does not compile for ILP32 (S6c); guest links without the proxy')
                r['status'] = 'warn'
        r.artifact('work', work)
        r.artifact('proxy', px)
        r['output_id'] = sha(r['libs'], file_hash(os.path.join(px, 'nine_proxy_host.c')), file_hash(gobj))
        r['summary'] = '%d static libraries (%s), proxies %s' % (len(libs), human(r['metrics']['nine_libs_bytes']),
                                                                r.get('proxy_kinds'))


# ------------------------------------------------------------------------------------------------------ 9 host

class Host(Stage):
    name = 'host'
    deps = ('toolchain',)
    soft = ('nine',)

    def fingerprint(self, b):
        return ['host-v1', tree_hash('toolchain/host', 'toolchain/include', 'toolchain/npdm', 'toolchain/tools',
                                     'tests/s5/hello', 'tools/pipeline/host_nine.py'),
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
        hb = os.path.join(d, 's5-hello')
        b.say('host: LP64 host module (tests/s5/hello/build.sh recipe)')
        b.sh(['bash', os.path.join(ROOT, 'tests', 's5', 'hello', 'build.sh'), hb, b.cfg['PROGRAM_ID']], r['log'],
             check=True)
        r['method'] = 'S5 tests/s5/hello/build.sh (host.c + libnx)'
        if b.cfg['NINE'] == '1':
            if not b.ok('nine'):
                raise StageError('NINE=1 but the nine stage did not succeed')
            r['method'] = 'pipeline adapter tools/pipeline/host_nine.py (host.c + render/nine + Mesa + libnx)'
            b.say('host: linking render/nine into the host module')
            rc = b.sh([PY, '-I', os.path.join(HERE, 'host_nine.py'), b.art('nine', 'work'), b.art('nine', 'proxy'),
                       d], r['log'])
            sj = os.path.join(d, 'nine_link.json')
            if os.path.isfile(sj):
                s = json.load(open(sj))
                r['nine_link'] = s
                r.metric('host_nine_undefined', len(s.get('undefined', [])))
                r.metric('host_nine_failed_units', len(s.get('failed', [])))
            if rc or not os.path.isfile(os.path.join(d, 'host.elf')):
                raise StageError('host link with nine failed (see %s)' % b.rel(r['log']))
        else:
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
            'Nine' if b.cfg['NINE'] == '1' else 'no D3D9 backend', human(os.path.getsize(os.path.join(d, 'main'))),
            human(os.path.getsize(os.path.join(d, 'main.npdm'))))
        if r.get('metrics', {}).get('host_nine_undefined'):
            r['status'] = 'warn'
            r.note('host links with %d unresolved symbols (render/nine pieces not written yet)' %
                   r['metrics']['host_nine_undefined'])


# --------------------------------------------------------------------------------------------------- 10 package

class Package(Stage):
    name = 'package'
    deps = ('host',)
    soft = ('guest',)

    def fingerprint(self, b):
        return ['package-v1', up_id(b, 'host'), up_id(b, 'guest'), b.cfg['PROGRAM_ID'], tree_hash('toolchain/tools')]

    def outputs_present(self, b):
        return os.path.isfile(os.path.join(b.out, 'package', 'exefs.nsp'))

    def run(self, b, r):
        d = b.sdir('package')
        pid = b.cfg['PROGRAM_ID'].lower()
        if not re.fullmatch(r'[0-9a-f]{16}', pid):
            raise StageError('PROGRAM_ID must be 16 hex digits, got %r' % pid)
        shutil.rmtree(os.path.join(d, 'sd'), ignore_errors=True)
        tools = os.path.join(ROOT, 'toolchain', 'tools')
        hd = b.sdir('host')
        nsp = os.path.join(d, 'exefs.nsp')
        b.sh([PY, '-I', os.path.join(tools, 'build_pfs0.py'), nsp, 'main=' + os.path.join(hd, 'main'),
              'main.npdm=' + os.path.join(hd, 'main.npdm')], r['log'], check=True)
        rc, lst = b.capture([PY, '-I', os.path.join(tools, 'build_pfs0.py'), '--list', nsp])
        r['pfs0'] = lst.splitlines()[:6]
        sd = os.path.join(d, 'sd')
        cdir = os.path.join(sd, 'atmosphere', 'contents', pid)
        os.makedirs(cdir)
        shutil.copyfile(nsp, os.path.join(cdir, 'exefs.nsp'))
        os.makedirs(os.path.join(sd, 'fnvnx'))
        gnso = b.art('guest', 'guest.nso') if b.ok('guest') else None
        if gnso and os.path.isfile(gnso):
            shutil.copyfile(gnso, os.path.join(sd, 'fnvnx', 'guest.nso'))
        files = []
        for dp, _, fn in os.walk(sd):
            for n in sorted(fn):
                p = os.path.join(dp, n)
                files.append((os.path.relpath(p, sd), os.path.getsize(p)))
        r['sd_files'] = files
        r.metric('package_exefs_bytes', os.path.getsize(nsp))
        r.metric('package_sd_bytes', sum(s for _, s in files))
        r.artifact('exefs.nsp', nsp)
        r.artifact('sd', sd)
        r['output_id'] = sha(file_hash(nsp), file_hash(gnso))
        r['summary'] = 'exefs.nsp %s; SD tree %s (%d files) for program id %s' % (
            human(os.path.getsize(nsp)), human(sum(s for _, s in files)), len(files), pid)
        if pid == '0100000000000000':
            r.note('PROGRAM_ID is the placeholder 0100000000000000: set PROGRAM_ID=<installed title> for hardware')
            r['status'] = 'warn'
        if not gnso:
            r.note('no guest.nso (guest stage did not succeed): SD tree holds the host only')
            r['status'] = 'warn'


# ----------------------------------------------------------------------------------------------------- 11 tests

class Suite:
    def __init__(self, name, inputs, cmds, needs=(), informational=False, fixed_inputs=None):
        self.name, self.inputs, self.cmds, self.needs = name, inputs, cmds, needs
        self.informational = informational
        self.fixed_inputs = fixed_inputs or (lambda b: [])


def summarize_output(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    pats = [r'^Ran \d+ tests?', r'^(OK|FAILED)\b.*', r'\d+ tests?, \d+ checks', r'\bPASS\b', r'\bFAIL\b',
            r'passed|failed', r': ok\b', r'^ok\b']
    found = []
    for rx in pats:
        for l in reversed(lines):
            if re.search(rx, l, re.I) and not l.startswith('$ ') and not l.startswith('[exit'):
                if l not in found:
                    found.append(l)
                break
        if len(found) >= 2:
            break
    return ' / '.join(found[:2])[:200]


def suites(b):
    ext = b.art('fetch', 'ext') or os.path.join(ROOT, 'ext')
    t = os.path.join(b.out, 'tests')
    libnx = os.path.join(ext, 'libnx')
    s = []
    s.append(Suite('s2-link', ['tools/link'], [
        [PY, '-I', 'tools/link/test_link.py'], [PY, '-I', 'tools/link/test_override.py'],
        [PY, '-I', 'tools/link/test_bridges.py']], needs=('qemu-aarch64', 'llvm-undname')))
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
        s.append(Suite('s5-emu-hello', ['tests/s5', 'toolchain'], [
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
            needs=('cmake', 'ninja', 'meson')))
    if os.path.isfile(os.path.join(ROOT, 'tests', 's8', 'run.sh')):
        s.append(Suite('s8', ['tests/s8', 'shim/include'], [['bash', 'tests/s8/run.sh', os.path.join(t, 's8')]]))
    known = {'s3', 's4', 's5', 's6b', 's7', 's8'}
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
    # informational: the game guest under the emulator (S5's boot code prints "fnvnx boot")
    gnso = os.path.join(b.out, 'guest', 'guest.nso')
    if b.rec('guest').get('method', '').startswith('S5') and os.path.isfile(gnso):
        s.append(Suite('emu-game', ['tests/s5/emu_guest.py'], [
            [PY, '-I', 'tests/s5/emu_guest.py', gnso, '--expect', 'fnvnx boot']], needs=('unicorn',),
            informational=True, fixed_inputs=lambda b: [up_id(b, 'guest')]))
    return s


class Tests(Stage):
    name = 'tests'
    soft = ('toolchain', 'fetch')    # the game guest's emulator run uses whatever build/guest holds
    always = True

    def run(self, b, r):
        d = b.sdir('tests')
        want = b.cfg['TESTS']
        sel = None if want == 'all' else set(x.strip() for x in want.split(',') if x.strip())
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
            fp = sha(tree_hash(*su.inputs), up_id(b, 'toolchain'), up_id(b, 'fetch'), su.cmds, su.fixed_inputs(b))
            p = prev.get(su.name)
            if p and p.get('fingerprint') == fp and p.get('status') in ('pass', 'fail') and not force:
                p['cached'] = True
                p.pop('not_selected', None)
                results[su.name] = p
                continue
            miss = [n for n in su.needs if not (have.get(n) if n in have else shutil.which(n))]
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
            timeout = int(b.cfg['EMU_TIMEOUT']) if su.name == 'emu-game' else 3600
            for c in su.cmds:
                rc = b.sh(c, log, cwd=ROOT, timeout=timeout)
                if rc:
                    break
            text = open(log, errors='replace').read()
            res.update(status='pass' if rc == 0 else 'fail', rc=rc, seconds=round(time.time() - t0, 1),
                       summary=summarize_output(text) or ('exit %d' % rc))
            if rc:
                lines = [l.strip() for l in text.splitlines() if l.strip()]
                res['tail'] = lines[-8:]
                first = next((l for l in lines if re.search(r'error:|\bFAIL|Traceback|Error:|STOPPED|mismatch', l)
                              and not re.search(r'\*\*\* \[|Error \d+$', l)), None)
                if first:
                    res['summary'] = re.sub(r'^' + re.escape(ROOT) + '/', '', first)[:220]
            results[su.name] = res
        for k, v in prev.items():     # suites not selected this run keep their last result
            if k not in results:
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


STAGES = [Fetch(), Toolchain(), Prepare(), Census(), Compile(), Link(), Guest(), Nine(), Host(), Package(), Tests()]
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
        fp = sha(st.fingerprint(b)) if not st.always else None
        prev = b.rec(st.name)
        if (fp and prev.get('fingerprint') == fp and prev.get('status') in ('ok', 'warn') and st.outputs_present(b)
                and st.name not in force and 'all' not in force):
            prev['ran'] = False
            prev['cached_at'] = b.run_id
            b.save(prev)
            b.say('%-9s cached   %s' % (st.name, prev.get('summary', '')))
            continue
        open(r['log'], 'w').close()
        r.update(status='ok', fingerprint=fp, started=now_iso(), ran=True, run_id=b.run_id)
        t0 = time.time()
        b.say('%-9s running' % st.name)
        try:
            st.run(b, r)
        except StageError as ex:
            r['status'] = 'fail'
            r['error'] = str(ex)
        except Exception as ex:   # a bug in a stage must not stop the others
            import traceback
            r['status'] = 'fail'
            r['error'] = '%s: %s' % (type(ex).__name__, ex)
            with open(r['log'], 'a') as f:
                f.write(traceback.format_exc())
        r['seconds'] = round(time.time() - t0, 1)
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
    for k in ('EXT', 'FNVNX_TC', 'DEVKITPRO', 'TC_DL', 'OVERRIDE', 'EXTERN_TEXT', 'VORBIS', 'NINE_SRC', 'FORCE'):
        cfg[k] = os.environ.get(k, '')
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
    if cmd == 'status':
        for st in STAGES:
            r = b.rec(st.name)
            print('%-9s %-9s %s' % (st.name, r.get('status'), r.get('summary', '')))
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
    if not stages:   # `report` alone: re-render the last run, no new history entry
        try:
            b.run_id = json.load(open(os.path.join(b.out, 'report.json')))['run_id']
        except (OSError, ValueError, KeyError):
            pass
    b.say('fnv-nx pipeline run %s: %s (NINE=%s PROGRAM_ID=%s JOBS=%d)' % (b.run_id, ' '.join(targets), cfg['NINE'],
                                                                         cfg['PROGRAM_ID'], b.jobs))
    failed = execute(b, stages) if stages else False
    path = reportmod.write(b, STAGES)
    b.say('report: %s' % path)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
