#!/usr/bin/env python3
"""test_s2c_s4_batch.py - V1 (D14): adversarial checks of the S2c-abi / S2d / S4 claims merged on 2026-11-04
(8e9ad7a, e2dbe73, 0750425, 94a3d29, e0ed8d5, 944515f, 009bab5, afe8f43, 0c08b8d, b72b749).

    python3 -I tests/v1/test_s2c_s4_batch.py
        needs aarch64-linux-gnu-{g++,as,ld,objcopy}, qemu-aarch64, llvm-undname (as tools/link/test_bridges.py);
        SSE2NEON=<sse2neon checkout> when ext/sse2neon is not fetched (layout probe);
        FNVNX_EXE=<the player's FalloutNV.exe> (optional, python capstone): the BinkOpen call-site check

Only hand-written declarations and made-up code; nothing of the decomp or the exe is written anywhere (the exe is
read for instruction text at its BinkOpen call site, printed as addresses and mnemonics only).
Cases (REFUTED = the claim's behaviour reproduces as wrong; exit 1 when any case is REFUTED):
  ABI-R1    8e9ad7a  `int *const volatile __restrict` (?f@@YAXSIAH@Z) is a pointer: classify says `?:int *`, the
                     pair against `int *` stays manual as an aggregate by value (cv-pointer regex takes one qualifier)
  VA-U1     e2dbe73  unprototyped `int f(...)` against `void f(int, int)`: verdict `same`, but a call site passing
                     one 64-bit integer (two x86 words) puts it in x0 alone on AArch64: the second int is garbage
  VA-U2     e2dbe73  same pair, a double passed (two x86 words): AArch64 passes it in d0, the ints read x0/x1
  VA-U3     e2dbe73  `void f(int x5)`: a 20-byte struct passed by value is five x86 words, on AArch64 a pointer
  VA-U4     e2dbe73  `void f(int)`: a struct { float } (a 4-byte word that is a float) goes in s0 on AArch64 (HFA)
  VA-C0     control: the same harness with two int arguments passes (the qemu run itself is sound)
  OVR-F1    94a3d29  the Link fingerprint is the same for OVERRIDE=auto and OVERRIDE=none: turning the overrides off
                     (or on) keeps a cached link made the other way
  OVR-E1    94a3d29  "OVERRIDE=none (or empty) turns it off": an empty OVERRIDE in the environment of build.py is auto
  RPL-B1    e0ed8d5  abireplay.py strips `#vtables` but not `#bodiesN` from the object name: a body object's
                     by-value aggregate pair finds no layouts in the replay (resolve uses u.host for both)
  EXE-FP    009bab5  the Link fingerprint follows the exe's content (two exes differ; FNVNX_EXE and BOOT_IMAGE of
                     the same file agree; no exe differs from an exe)            [expected to hold]
  BINK-1    afe8f43  every `call [BinkOpen slot]` in the exe is followed by a null test of the stored handle
                                                                                  [expected to hold; needs FNVNX_EXE]
"""
import os
import struct
import subprocess
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(PORT, 'tools', 'link'))
sys.path.insert(0, os.path.join(PORT, 'tools', 'pipeline'))
import abi           # noqa: E402
import abi2          # noqa: E402
import fnvlink       # noqa: E402
import layout        # noqa: E402
import sigs          # noqa: E402
import test_bridges  # noqa: E402

RESULTS = []


def report(case, ok, evidence):
    RESULTS.append((case, ok))
    print('%-8s %s  %s' % (case, 'holds  ' if ok else 'REFUTED', evidence))


def mkdtemp():
    d = tempfile.mkdtemp(prefix='v1batch.')
    if not d or not os.path.isdir(d):
        raise SystemExit('tempfile.mkdtemp failed')
    return d


def parse(names):
    dem = fnvlink.batch_demangle(fnvlink.UNDNAME, names)
    return {n: sigs.parse_msvc(n, dem[n]) for n in names}


# ------------------------------------------------------------------------------------------------ ABI-R1 (8e9ad7a)

def case_restrict_cv():
    s = parse(['?f@@YAXSIAH@Z', '?f@@YAXPAH@Z'])
    raw = s['?f@@YAXSIAH@Z'].raw_params[0]
    cls = abi.classify(raw)
    v = abi.compare(s['?f@@YAXSIAH@Z'], s['?f@@YAXPAH@Z'])
    report('ABI-R1', cls == 'P' and v.kind == 'same',
           'classify(%r) = %s; pair vs `int *`: %s %s' % (raw, cls, v.kind, ','.join(v.classes)))


# ------------------------------------------------------------------------------------------- VA-* (e2dbe73) qemu

VA_TYPES = r'''
struct B5 { int a[5]; };
struct F1 { float f; };
'''

# (case, ref, def, C++ definition, C++ check calling br_<case>) - the x86 reading of each call is what the check wants
VA_CASES = [
    ('VA-C0', '?p@@YAHZZ', '?p@@YAXHH@Z',
     'extern "C" int g_c0; int g_c0; extern "C" void def_c0(int a, int b) { g_c0 = a * 1000 + b; }',
     'extern "C" int br_c0(...);\nstatic int t_c0() { g_c0 = 0; br_c0(1, 42); return g_c0 == 1042 ? 0 : 1; }'),
    # x86: the 8-byte argument is pushed as two words, low first: a = 1, b = 42
    ('VA-U1', '?p@@YAHZZ', '?p@@YAXHH@Z',
     'extern "C" int g_u1; int g_u1; extern "C" void def_u1(int a, int b) { g_u1 = a * 1000 + b; }',
     'extern "C" int br_u1(...);\n'
     'static int t_u1() { g_u1 = 0; br_u1(0x0000002a00000001LL); return g_u1 == 1042 ? 0 : 1; }'),
    # x86: the double's two words (a decompiled `(int lo, int hi)` reading of a double argument)
    ('VA-U2', '?p@@YAHZZ', '?p@@YAXHH@Z',
     'extern "C" unsigned g_u2[2]; unsigned g_u2[2]; extern "C" void def_u2(int lo, int hi) '
     '{ g_u2[0] = lo; g_u2[1] = hi; }',
     'extern "C" int br_u2(...);\n'
     'static int t_u2() { double d = 2.5; unsigned w[2]; __builtin_memcpy(w, &d, 8); g_u2[0] = g_u2[1] = 0;'
     ' br_u2(d); return g_u2[0] == w[0] && g_u2[1] == w[1] ? 0 : 1; }'),
    # x86: a 20-byte struct by value is five stack words
    ('VA-U3', '?p@@YAHZZ', '?p@@YAXHHHHH@Z',
     'extern "C" int g_u3; int g_u3; extern "C" void def_u3(int a, int b, int c, int d, int e) '
     '{ g_u3 = a + 2 * b + 3 * c + 4 * d + 5 * e; }',
     'extern "C" int br_u3(...);\n'
     'static int t_u3() { g_u3 = 0; B5 s = {{1, 2, 3, 4, 5}}; br_u3(s); return g_u3 == 55 ? 0 : 1; }'),
    # x86: a struct holding one float is one word, read here as its bits
    ('VA-U4', '?p@@YAHZZ', '?p@@YAXH@Z',
     'extern "C" int g_u4; int g_u4; extern "C" void def_u4(int bits) { g_u4 = bits; }',
     'extern "C" int br_u4(...);\n'
     'static int t_u4() { g_u4 = 0; F1 f = {1.0f}; br_u4(f); return g_u4 == 0x3f800000 ? 0 : 1; }'),
]


def case_varargs():
    s = parse(sorted({c[1] for c in VA_CASES} | {c[2] for c in VA_CASES}))
    for case, rm, dm, cdef, ccheck in VA_CASES:
        v = abi.compare(s[rm], s[dm])
        tag = case.split('-')[1].lower()
        if v.kind != 'same':
            report(case, True, 'verdict %s (%s): not passed without a bridge' % (v.kind, '; '.join(v.notes)))
            continue
        asm = ['    .text', '    .p2align 2\n    .globl br_%s\nbr_%s:\n    b def_%s\n' % (tag, tag, tag)]
        tree = mkdtemp()
        bad = test_bridges.execute(tree, VA_TYPES, [cdef], [ccheck], ['t_%s()' % tag], asm)
        report(case, bad == 0, 'abi verdict same (%s): ILP32 qemu run of the x86-consistent call %s' % (
            ','.join(v.classes), 'passes' if bad == 0 else 'reads other values'))


# ---------------------------------------------------------------------------------------- OVR-* / EXE-FP (build.py)

def link_fp(build, cfg_over, env_exe=None):
    saved = {k: getattr(build, k) for k in ('link_tool_inputs', 'up_id', 'extern_text_id', 'msvc_objs_id',
                                            'game_cxx_id')}
    old_env = os.environ.get('FNVNX_EXE')
    try:
        build.link_tool_inputs = lambda b: 'tools'
        build.up_id = lambda b, s: s
        build.extern_text_id = lambda b: 'et'
        build.msvc_objs_id = lambda b, key='MSVC_OBJS': key
        build.game_cxx_id = lambda b: 'cxx'
        if env_exe is None:
            os.environ.pop('FNVNX_EXE', None)
        else:
            os.environ['FNVNX_EXE'] = env_exe
        cfg = dict(OVERRIDE='auto', BOOT_IMAGE='')
        cfg.update(cfg_over)
        return build.Link().fingerprint(types.SimpleNamespace(cfg=cfg))
    finally:
        for k, f in saved.items():
            setattr(build, k, f)
        if old_env is None:
            os.environ.pop('FNVNX_EXE', None)
        else:
            os.environ['FNVNX_EXE'] = old_env


def case_build():
    import build
    cwd = os.getcwd()
    work = mkdtemp()
    os.chdir(work)   # file_hash('auto') / ('none') look for such files in the current directory
    try:
        fa, fn = link_fp(build, {'OVERRIDE': 'auto'}), link_fp(build, {'OVERRIDE': 'none'})
        report('OVR-F1', fa != fn, 'Link fingerprint auto == none: %s (file_hash of a non-file is None for both)'
               % (fa == fn))
        old = os.environ.get('OVERRIDE')
        os.environ['OVERRIDE'] = ''
        try:
            cfg, _ = build.parse_args([])
        finally:
            if old is None:
                os.environ.pop('OVERRIDE')
            else:
                os.environ['OVERRIDE'] = old
        report('OVR-E1', cfg['OVERRIDE'] in ('', 'none'),
               'OVERRIDE= in the environment -> cfg %r (CONFIG default via `or d`)' % cfg['OVERRIDE'])
        e1, e2 = os.path.join(work, 'a.exe'), os.path.join(work, 'b.exe')
        open(e1, 'wb').write(b'MZ synthetic one')
        open(e2, 'wb').write(b'MZ synthetic two')
        f1, f2 = link_fp(build, {}, e1), link_fp(build, {}, e2)
        fb = link_fp(build, {'BOOT_IMAGE': e1})
        f0 = link_fp(build, {})
        report('EXE-FP', f1 != f2 and f1 == fb and f0 != f1,
               'two exes differ %s; FNVNX_EXE == BOOT_IMAGE of one file %s; none != one %s'
               % (f1 != f2, f1 == fb, f0 != f1))
    finally:
        os.chdir(cwd)


# ------------------------------------------------------------------------------------------------ RPL-B1 (e0ed8d5)

def case_replay():
    work = mkdtemp()
    os.makedirs(os.path.join(work, 'src'))
    os.makedirs(os.path.join(work, 'include'))
    open(os.path.join(work, 'src', 'a.cpp'), 'w').write('struct Q { int a, b, c, d; };\n')
    lay = layout.probe_unit(work, work, 'src/a.cpp', ['Q'])['Q']
    if lay is None:
        report('RPL-B1', True, 'SKIP: layout probe failed (set SSE2NEON)')
        return
    out = os.path.join(work, 'out')
    os.makedirs(out)
    with open(os.path.join(out, 'layouts.tsv'), 'w') as f:
        f.write('src/a.cpp\tQ\t%d\t%d\t%d\t%d\t%s\n' % (lay.size, lay.align, lay.trivial, lay.empty, lay.cls))
    rm, dm = '?g@@YAXUQ@@@Z', '?g@@YAXHHHH@Z'
    dem = fnvlink.batch_demangle(fnvlink.UNDNAME, [rm, dm])
    with open(os.path.join(out, 'abi.tsv'), 'w') as f:
        f.write('#object\tundefined\taddress\tref_msvc\tdef_msvc\tverdict\tclasses\tmoves\tnotes\tref\tdef\n')
        for obj in ('src/a.cpp#vtables', 'src/a.cpp#bodies7'):
            f.write('\t'.join([obj, '_Z1g1Q', '00401000', rm, dm, 'manual', 'aggregate-param', '', '', dem[rm],
                               dem[dm]]) + '\n')
    units = os.path.join(work, 'units.json')
    open(units, 'w').write('{"units": [{"source": "src/a.cpp", "functions": [{"address": "0x00401000"}]}]}')
    rows = os.path.join(work, 'rows.tsv')
    r = subprocess.run([sys.executable, '-I', os.path.join(PORT, 'tools', 'link', 'abireplay.py'), out, units,
                        '--rows', rows], capture_output=True, text=True)
    got = {}
    for line in open(rows) if os.path.exists(rows) else ():
        if not line.startswith('#'):
            f = line.rstrip('\n').split('\t')
            got[f[0]] = f[5]
    vt, bd = got.get('src/a.cpp#vtables'), got.get('src/a.cpp#bodies7')
    report('RPL-B1', vt == bd, 'replayed verdict: #vtables row %s, #bodies7 row %s (rc %d)' % (vt, bd, r.returncode))


# ------------------------------------------------------------------------------------------------ BINK-1 (afe8f43)

def case_bink():
    exe = os.environ.get('FNVNX_EXE')
    if not exe or not os.path.isfile(exe):
        print('BINK-1   skip     (FNVNX_EXE not set)')
        return
    try:
        import capstone
    except ImportError:
        print('BINK-1   skip     (python capstone missing)')
        return
    data = open(exe, 'rb').read()
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    nsec, optsz = struct.unpack_from('<H', data, pe + 6)[0], struct.unpack_from('<H', data, pe + 20)[0]
    base = struct.unpack_from('<I', data, pe + 52)[0]
    secs = []
    for i in range(nsec):
        s = pe + 24 + optsz + 40 * i
        vsize, va, rawsz, raw = struct.unpack_from('<IIII', data, s + 8)
        secs.append((va, vsize, rawsz, raw, struct.unpack_from('<I', data, s + 36)[0]))

    def off(rva):
        return next(raw + rva - va for va, vs, rs, raw, _ in secs if va <= rva < va + max(vs, rs))
    slot = None
    rva, size = struct.unpack_from('<II', data, pe + 24 + 104)
    d = 0
    while rva and d + 20 <= size and slot is None:
        oft, _, _, name, ft = struct.unpack_from('<IIIII', data, off(rva + d))
        if not (oft or name or ft):
            break
        o = off(name)
        if data[o:data.index(b'\0', o)].lower() == b'binkw32.dll':
            j = 0
            while True:
                t = struct.unpack_from('<I', data, off((oft or ft) + 4 * j))[0]
                if not t:
                    break
                if not t & 0x80000000:
                    o2 = off(t + 2)
                    if data[o2:data.index(b'\0', o2)] == b'_BinkOpen@8':
                        slot = base + ft + 4 * j
                j += 1
        d += 20
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    sites, tested = [], 0
    pat = b'\xff\x15' + struct.pack('<I', slot or 0)
    for va, vs, rs, raw, ch in secs:
        if not ch & 0x20:
            continue
        blob = data[raw:raw + rs]
        k = blob.find(pat)
        while k >= 0:
            a = base + va + k
            ins = list(md.disasm(blob[k:k + 64], a, 10))[1:]
            sites.append(a)
            # the handle is stored (mov [r], eax) and tested (test eax,eax / cmp dword ptr [r], 0) before any use
            if any(i.mnemonic == 'test' and i.op_str == 'eax, eax' or
                   i.mnemonic == 'cmp' and i.op_str.startswith('dword ptr [') and i.op_str.endswith('], 0')
                   for i in ins[:6]):
                tested += 1
            k = blob.find(pat, k + 1)
    report('BINK-1', slot is not None and sites and tested == len(sites),
           'BinkOpen slot %s: %d call site(s) %s, %d null-tested' % (
               slot and '%#x' % slot, len(sites), ','.join('%#x' % a for a in sites), tested))


def main():
    case_restrict_cv()
    case_varargs()
    case_build()
    case_replay()
    case_bink()
    bad = [c for c, ok in RESULTS if not ok]
    print('refuted: %s' % (' '.join(bad) or 'none'))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
