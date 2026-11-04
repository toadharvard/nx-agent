#!/usr/bin/env python3
"""test_s15simd_decomp.py - V1 (D14) against S15-simd's NEON overrides (tools/link/overrides) of UpdateWorldLoc
0x00a68bf0 (a931294), the culling worker 0x00a694e0 and the sphere/plane classification 0x004b6100 (a0381d4),
FromRotationMatrix 0x00a6df40 (7c4e9db), NiBound::Update 0x00a7fdd0 and the NiMatrix3 transpose product 0x00a588a0
(314a221), against the decomp's own bodies, not S15's transcriptions (tests/s15/simd/*_test.c).

    python3 -I tests/v1/test_s15simd_decomp.py [--n N] [--work DIR]   (FNV_DECOMP=<decomp checkout>)

The bodies (and the helpers each calls, bound by address as the overrides' headers say) are copied from the decomp
into a temporary directory at run time (no decomp text in git) and compiled with s15simd/dec_*.cpp for AArch64
ILP32 with compile.sh's code-generation flags (and its ftoi / order plugins when they build), so the objects have the
game's x86 layouts; the overrides with build.sh's flags. One freestanding image (s15simd/harness.c) runs under unicorn:

1. bit identity: N cases per kernel (range values, signed zeros, denormals, inf, quiet and signalling NaNs of either
   sign with distinct payloads, raw bits; culling and classification spheres exactly on a plane's boundary, d == -r and
   d == r, and radius +-0 / NaN / negative; FromRotationMatrix rotations, 180-degree turns, diagonal ties, trace 0).
   Every output word must equal the decomp's. Differences that are only a NaN's payload or sign are counted apart and
   are information only: PERF.md 11.6 scopes the claim to "any NaN matches any NaN" (GCC commutes operands, on both
   sides; found in xform, bound and mat3, never in cull, plane, quat).
2. dynamic A64 instructions per call (from the target's entry to its return, the cull callback excluded), decomp
   and override, by path, against the rows of 84d0516 / 68d0aab / beb2ba6 / 1c74384 (a row is refuted beyond 5 %).
   PL1 (68d0aab): 0x004b6100 is 19 only when it returns 2 (behind); 21 in front / straddling (+10.5 %).
   QF1 (beb2ba6): FromRotationMatrix is 77 only on the largest-diagonal path; the trace > 0 path is 34 (decomp 45).
Exit 1 when (1) has a difference beyond a NaN payload.
"""
import argparse
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))
SRC = os.path.join(os.environ.get('FNV_DECOMP') or os.path.join(PORT, 'ext', 'fnv_decomp'), 'src')
OVR = os.path.join(PORT, 'tools', 'link', 'overrides')
D = os.path.join(HERE, 's15simd')
G = 'gamebryo2.2/corelibs/'
# generated include -> [(decomp file, @fnv address or a definition regex)]
INCS = {
    'xform.inc': [(G + 'nimain/console/nitransform.inl', '0x00a68bf0'), (G + 'nimain/console/nimatrix3.inl', '0x0062c250'),
                  (G + 'nimain/console/nimatrix3.inl', '0x0043f8d0'), ('fallout_shared/globalfunc.cpp', '0x004b4500'),
                  (G + 'nianimation/niposdata.inl', '0x004a3760'), ('fallout/ai/combatcontroller.cpp', '0x00439e90')],
    'cull.inc': [(G + 'nimain/nicullingprocess.cpp', '0x00a694e0')],
    'plane.inc': [('fallout_shared/globalfunc.cpp', '0x004b6100'), ('fallout_shared/globalfunc.cpp', '0x004b6160'),
                  ('fallout_shared/globalfunc.cpp', '0x004b6190'), ('fallout/ai/combat/combatthreatmap.cpp', '0x0043d450')],
    'quat.inc': [(G + 'nimain/niquaternion.cpp', r'inline float QuaternionSqrt\('),
                 (G + 'nimain/niquaternion.cpp', '0x00a6df40')],
    'mat3.inc': [(G + 'nimain/console/nimatrix3.cpp', '0x00a588a0')],
}
COPY = [G + 'nimain/nibound.inl', G + 'nimain/nibound_decls.h']
OVRS = ['update_world_loc_00a68bf0.c', 'cull_worker_00a694e0.c', 'sphere_plane_004b6100.c', 'nibound_update_00a7fdd0.c',
        'mat3_tmul_00a588a0.c', 'quat_from_matrix_00a6df40.c']
KN = ['xform', 'cull', 'plane', 'bound', 'mat3', 'quat']
NOUT = [13, 4, 1, 4, 9, 4]
NMODES = 8
MODES = ['range', 'range', 'range', '1/4 edge', 'raw bits', 'all NaN', 'denormal', '1/2 NaN']
A32 = ['-mabi=ilp32', '-O2', '-fPIC', '-frounding-math', '-fsigned-char', '-fno-strict-aliasing', '-fwrapv',
       '-fno-delete-null-pointer-checks', '-ffp-contract=off', '-march=armv8-a+crc+crypto', '-mtune=cortex-a57',
       '-fno-stack-protector', '-fno-stack-clash-protection', '-mno-outline-atomics']
CXXF = ['-std=gnu++17', '-fms-extensions', '-fpermissive', '-ffreestanding', '-fno-rtti', '-fno-exceptions',
        '-D__thiscall=', '-D__cdecl=', '-D__fastcall=', '-w']


def body(src, key):
    if key.startswith('0x'):
        m = re.search(r'//\s*@fnv\s+' + key + r'\b', src, re.I)
        if not m:
            raise SystemExit('decomp: @fnv %s not found' % key)
        start = src.index('\n', m.end()) + 1
    else:
        m = re.search(key, src)
        if not m:
            raise SystemExit('decomp: %s not found' % key)
        start = m.start()
    i = src.index('{', start)
    d = 0
    for j in range(i, len(src)):
        d += {'{': 1, '}': -1}.get(src[j], 0)
        if d == 0:
            break
    return src[start:j + 1]


def sh(argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, **kw)


def build_c_flags():
    txt = open(os.path.join(OVR, 'build.sh')).read()
    return re.search(r'\$CC (.*?) -c ', txt).group(1).split()


def plugins(work):
    out = []
    for name, extra in (('ftoi', []), ('order', ['-fplugin-arg-fnvnx_order-mode=volatile',
                                                  '-fplugin-arg-fnvnx_order-args=rtl',
                                                  '-fplugin-arg-fnvnx_order-refcount=weak',
                                                  '-fplugin-arg-fnvnx_order-object=first',
                                                  '-fplugin-arg-fnvnx_order-operator=cpp17'])):
        r = sh(['bash', os.path.join(PORT, 'toolchain', name, 'build.sh'), os.path.join(work, '.' + name)],
               env=dict(os.environ, CXX='aarch64-linux-gnu-g++'))
        so = r.stdout.strip().splitlines()[-1] if r.returncode == 0 and r.stdout.strip() else ''
        if not so or not os.path.isfile(so):
            return [], 'plugins: %s does not build, decomp compiled without them' % name
        out += ['-fplugin=' + so] + extra
    return out, 'plugins: ftoi + order (volatile, rtl) as compile.sh'


def build(work):
    for inc, parts in INCS.items():
        with open(os.path.join(work, inc), 'w') as f:
            for rel, key in parts:
                f.write(body(open(os.path.join(SRC, rel), errors='replace').read(), key) + '\n\n')
    for rel in COPY:
        shutil.copy(os.path.join(SRC, rel), work)
    pl, note = plugins(work)
    print('info ' + note)
    objs = []
    cflags = build_c_flags()
    for n in ('dec_xform', 'dec_cull', 'dec_plane', 'dec_bound', 'dec_quat', 'dec_mat3'):
        o = os.path.join(work, n + '.o')
        r = sh(['aarch64-linux-gnu-g++', *A32, *CXXF, *pl, '-I' + work, '-c', os.path.join(D, n + '.cpp'), '-o', o])
        if r.returncode:
            raise SystemExit('%s: %s' % (n, r.stderr[-1500:]))
        objs.append(o)
    for src in OVRS:
        o = os.path.join(work, 'ovr_' + src[:-2] + '.o')
        r = sh(['aarch64-linux-gnu-gcc', *cflags, '-c', os.path.join(OVR, src), '-o', o])
        if r.returncode:
            raise SystemExit('%s: %s' % (src, r.stderr[-1500:]))
        objs.append(o)
    for n, extra in (('harness', []), ('support', ['-fno-builtin', '-fno-tree-loop-distribute-patterns'])):
        o = os.path.join(work, n + '.o')
        r = sh(['aarch64-linux-gnu-gcc', *cflags, '-ffreestanding', *extra, '-c', os.path.join(D, n + '.c'), '-o', o])
        if r.returncode:
            raise SystemExit('%s: %s' % (n, r.stderr[-1500:]))
        objs.append(o)
    elf = os.path.join(work, 'image.elf')
    r = sh(['aarch64-linux-gnu-ld', '-m', 'aarch64linux32', '-e', 'run', '-Ttext=0x10000', *objs, '-o', elf])
    if r.returncode:
        raise SystemExit('link: ' + r.stderr[-1500:])
    return elf


class Guest:
    STACK, RET = 0x7f0000, 0x7ff000

    def __init__(self, elf):
        from elftools.elf.elffile import ELFFile
        from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
        f = ELFFile(open(elf, 'rb'))
        self.sym, self.size = {}, {}
        for s in f.get_section_by_name('.symtab').iter_symbols():
            if s.name and s['st_info']['type'] in ('STT_FUNC', 'STT_OBJECT'):
                self.sym[s.name] = s['st_value']
                self.size[s.name] = s['st_size']
        self.mu = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        self.mu.mem_map(0, 0x800000)
        for seg in f.iter_segments():
            if seg['p_type'] == 'PT_LOAD':
                self.mu.mem_write(seg['p_vaddr'], seg.data())
        self.n = 0
        self.on = None   # (target entry, return address) while counting
        self.target = None
        self.excl = []
        self.hook_kind = UC_HOOK_CODE
        self.h = None   # the code hook is attached only while counting (it slows unicorn ~100x)
        self.hooking = False

    def find(self, part):
        c = [s for s in self.sym if part in s]
        if len(c) != 1:
            raise SystemExit('symbol %s: %s' % (part, c))
        return c[0]

    def _hook(self, uc, addr, size, ud):
        if not self.hooking:
            return
        from unicorn.arm64_const import UC_ARM64_REG_LR
        if self.on is None:
            if addr == self.target:
                self.on = uc.reg_read(UC_ARM64_REG_LR)
                self.n = 1
            return
        if addr == self.on:
            self.on = False
            return
        if self.on is not False and not any(a <= addr < b for a, b in self.excl):
            self.n += 1

    def call(self, fn, *args, count=None):
        from unicorn.arm64_const import UC_ARM64_REG_SP, UC_ARM64_REG_LR, UC_ARM64_REG_X0
        for i, a in enumerate(args):
            self.mu.reg_write(UC_ARM64_REG_X0 + i, a)
        self.mu.reg_write(UC_ARM64_REG_SP, self.STACK)
        self.mu.reg_write(UC_ARM64_REG_LR, self.RET)
        self.hooking = count is not None
        self.target, self.on, self.n = count, None, 0
        if self.hooking and self.h is None:   # once, for the counting phase; translated blocks are dropped
            self.h = self.mu.hook_add(self.hook_kind, self._hook)
            self.mu.ctl_flush_tb()
        self.mu.emu_start(self.sym[fn], self.RET)
        self.hooking = False
        return self.n

    def words(self, name, n, off=0):
        return list(struct.unpack('<%dI' % n, self.mu.mem_read(self.sym[name] + 4 * off, 4 * n)))


def isnan(u):
    return (u & 0x7f800000) == 0x7f800000 and (u & 0x7fffff) != 0


def bitident(g, n, seed):
    g.call('run', n, seed)
    RW = 2 + 48 + 16 + 16
    per = 6 + 4 * RW + NMODES
    bad = 0
    stats = {}
    for k, name in enumerate(KN):
        w = g.words('g_res', per, k * per)
        cases, nbad, nnan, nrec = w[:4]
        pm = w[6 + 4 * RW:]
        stats[name] = (cases, nbad, nnan)
        tag = 'FAIL' if nbad else 'ok  ' if nnan == 0 else 'info'
        print('%s %-6s %d cases: %d differ beyond a NaN payload, %d only in a NaN payload/sign%s' % (
            tag, name, cases, nbad, nnan, (' (by input mode: %s)' % ', '.join(
                '%s %d' % (MODES[i], c) for i, c in enumerate(pm) if c)) if nbad else ''))
        for r in range(nrec):
            rw = w[6 + r * RW:6 + (r + 1) * RW]
            mode, nanonly, inp, od, oo = rw[0], rw[1], rw[2:50], rw[50:66], rw[66:82]
            diff = [j for j in range(NOUT[k]) if od[j] != oo[j]]
            print('     %s case (mode %s): in %s\n       out[%s]: decomp %s override %s' % (
                'NaN-payload' if nanonly else 'mismatch', MODES[mode],
                ' '.join('%08x' % x for x in inp[:{0: 41, 1: 30, 2: 8, 3: 18, 4: 18, 5: 13}[k]]),
                ','.join(map(str, diff)), ' '.join('%08x' % od[j] for j in diff), ' '.join('%08x' % oo[j] for j in diff)))
        bad += nbad
    return bad, stats


def dyn(g, calls):
    """Per-call dynamic counts by path; returns {(kernel, path): (decomp list, override list)}."""
    T = {'xform': (g.find('UpdateWorldLoc'), 'fnvnx_update_world_loc_00a68bf0'),
         'cull': (g.find('FUN_00a694e0'), 'fnvnx_cull_worker_00a694e0'),
         'plane': (g.find('ClassifySphereAgainstPlane'), 'fnvnx_sphere_plane_004b6100'),
         'bound': (g.find('NiBound6Update'), 'fnvnx_nibound_update_00a7fdd0'),
         'mat3': (g.find('FUN_00a588a0'), 'fnvnx_mat3_tmul_00a588a0'),
         'quat': (g.find('FromRotationMatrix'), 'fnvnx_quat_from_matrix_00a6df40')}
    cb = [s for s in g.sym if 'TestObj8callback' in s] + ['o_callback']
    g.excl = [(g.sym[s], g.sym[s] + g.size[s]) for s in cb]
    g.call('run', 0, 0)
    inp, outd, outo = g.sym['g_in'], g.sym['g_outd'], g.sym['g_outo']
    res = {}
    import random
    rnd = random.Random(11)
    for k, name in enumerate(KN):
        for i in range(calls):
            mode = 0 if i % 4 else rnd.choice([0, 3])
            g.mu.mem_write(g.sym['rng'], struct.pack('<Q', rnd.getrandbits(64) | 1))
            g.call('gen', k, mode, inp)
            w = g.words('g_in', 48)
            nd = g.call('dec', k, inp, outd, count=g.sym[T[name][0]])
            no = g.call('ovr', k, inp, outo, count=g.sym[T[name][1]])
            od, oo = g.words('g_outd', 16), g.words('g_outo', 16)
            if name == 'xform':
                path = 'copy local' if not w[0] else 'copy parent' if w[1] & 0x200 else 'compose'
            elif name == 'cull':
                r = struct.unpack('<f', struct.pack('<I', w[5]))[0]
                act = bin(w[0] & 0x3f).count('1')
                if not w[1]:
                    path = 'early exit, no bound'
                elif r == 0.0:
                    path = 'early exit, r == 0'
                elif not act:
                    path = 'no active plane'
                else:
                    path = '%d active, %s' % (act, 'passes' if od[0] else 'rejected')
            elif name == 'quat':
                fl = struct.unpack('<9f', struct.pack('<9I', *w[:9]))
                path = 'trace > 0' if (fl[0] + fl[4]) + fl[8] > 0 else 'largest diagonal'
            elif name == 'plane':
                path = {0: 'all, straddles (0)', 1: 'all, in front (1)', 2: 'all, behind (2)'}[od[0]]
            else:
                path = 'all'
            res.setdefault((name, path), ([], []))
            res[(name, path)][0].append(nd)
            res[(name, path)][1].append(no)
    return res


# the rows' per-call claims for the override (after) and the decomp (before), A64 instructions
CLAIMS = {('xform', 'copy local'): ((13, 14), None), ('xform', 'copy parent'): ((13, 14), None),
          ('xform', 'compose'): ((47, 50), None),
          ('plane', 'all, straddles (0)'): ((19, 19), (38, 38)), ('plane', 'all, in front (1)'): ((19, 19), (38, 38)),
          ('plane', 'all, behind (2)'): ((19, 19), (38, 38)), ('bound', 'all'): ((19, 19), (46, 46)),
          ('mat3', 'all'): ((39, 39), (64, 64)), ('quat', 'trace > 0'): ((77, 77), (89, 89)),
          ('quat', 'largest diagonal'): ((77, 77), (89, 89)),
          ('cull', 'early exit, no bound'): ((10, 10), (12, 12))}
for _n in range(4, 7):
    CLAIMS[('cull', '%d active, rejected' % _n)] = ((49, 49), (224, 330))
    CLAIMS[('cull', '%d active, passes' % _n)] = ((67, 67), (224, 330))


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=200000)
    ap.add_argument('--calls', type=int, default=400)
    ap.add_argument('--work')
    a = ap.parse_args(argv)
    for t in ('aarch64-linux-gnu-g++', 'aarch64-linux-gnu-gcc', 'aarch64-linux-gnu-ld'):
        if not shutil.which(t):
            print('skip: %s missing' % t)
            return 0
    if not os.path.isdir(SRC):
        print('skip: no decomp at ' + SRC)
        return 0
    work = a.work or tempfile.mkdtemp(prefix='v1-s15simd-')
    if not work or not os.path.isdir(os.path.dirname(work) or '.'):
        raise SystemExit('mkdtemp failed')
    os.makedirs(work, exist_ok=True)
    g = Guest(build(work))
    bad, _ = bitident(g, a.n, 0x5eed)
    res = dyn(g, a.calls)
    print('dynamic A64 instructions per call (ILP32, entry to return; min/mean/max; decomp -> override):')
    for (name, path), (d, o) in sorted(res.items()):
        cl = CLAIMS.get((name, path))
        note = ''
        if cl:
            lo, hi = cl[0]
            ok = lo * 0.95 <= min(o) and max(o) <= hi * 1.05
            note = ' | row claims %s -> %s: override %s' % (
                '%d' % cl[1][0] if cl[1] else '-', '%d' % lo if lo == hi else '%d-%d' % (lo, hi),
                'within 5 %' if ok else 'OUTSIDE 5 %')
        print('  %-6s %-34s n=%-4d %d/%.1f/%d -> %d/%.1f/%d%s' % (name, path, len(d), min(d), sum(d) / len(d), max(d),
                                                                 min(o), sum(o) / len(o), max(o), note))
    print('%d bit-identity differences beyond a NaN payload' % bad)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
