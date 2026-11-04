#!/usr/bin/env python3
"""test_fastcall_nonpod.py - V1 (D14), open item since J-0119: where MSVC 2008 puts the hidden result buffer of a
__fastcall function, against tools/link/abi2.py's x86 model fed with tools/link/layout.py's `msvc pod` predicate.

    python3 -I tests/v1/test_fastcall_nonpod.py [--work DIR]
        (FNV_MSVC9_ROOT=<VC 2008 SP1 root, VC/bin/cl.exe> and WINEPREFIX: compile tests/v1/fastcall/msvc_probe.cpp
         with cl /O2 and read the placements from the object; otherwise the table MSVC gave on 2026-11-04 is used)

S1b (a9fbc1b, J-0119) found a non-POD's buffer in ecx with the arguments moved one place, a POD's on the first stack
word. This checks the rule on one made-up struct per C++03 POD condition (tests/v1/fastcall/kinds.h): destructor,
constructor only, copy constructor only, copy assignment only, private / protected members, a base (with and without
members), a virtual function, a member with a destructor / a constructor, a reference member, static members; 4, 8
and 12 bytes. Per kind: MSVC's placement (ecx, stk = first stack word, regs = eax:edx, no buffer) and abi2's for
`K __fastcall f(int)` with the layout probe's POD bit, and with V1's predicate (ALT). Exit 1 when any kind differs.
Result (2026-11-04): S1b's ecx rule holds for every kind MSVC calls non-POD, but layout.py's predicate (__is_pod &&
__is_aggregate && no base) calls three kinds non-POD that MSVC returns in eax:edx: a member with a user constructor,
with a user copy constructor, an array of such members (mctor8, mcctor8, arrct8). ALT matches all 24. The probe runs
on the host (LP64): ref8 and virt8 are 16 bytes there, 8 under ILP32; the POD bit does not depend on it.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))
SRC = os.path.join(HERE, 'fastcall')
sys.path.insert(0, os.path.join(PORT, 'tools', 'link'))
import abi2      # noqa: E402
import fnvlink   # noqa: E402
import layout    # noqa: E402
import sigs      # noqa: E402

# MSVC 2008 SP1 (cl 15.00.30729.01) /O2, 2026-11-04, from msvc_probe.cpp: the caller's code before the call
MSVC_2008 = {
    'pod8': 'regs', 'pod12': 'stk', 'pod4': 'regs', 'dtor8': 'ecx', 'dtor4': 'ecx', 'ctor8': 'ecx', 'ctor12': 'ecx',
    'ctor4': 'ecx', 'cctor8': 'ecx', 'assign8': 'ecx', 'priv8': 'ecx', 'prot8': 'ecx', 'base8': 'ecx',
    'ebase8': 'ecx', 'virt8': 'ecx', 'mdtor8': 'ecx', 'mctor8': 'regs', 'mcctor8': 'regs', 'massign8': 'ecx',
    'mpriv8': 'regs', 'mpod8': 'regs', 'arrct8': 'regs', 'ref8': 'ecx', 'static8': 'regs',
}


# what MSVC 2008 shows (V1): the class's own constructors, a base, virtual functions, private / protected members
# make a buffer, its members' constructors do not; a non-trivial destructor or copy assignment (own or a member's,
# and a reference member, whose copy assignment is deleted) does
ALT = ('(unsigned)(__is_aggregate(%s) && std::tr2::direct_bases<%s>::type::empty::value && '
       '__has_trivial_destructor(%s) && __is_trivially_assignable(%s &, const %s &))')


def kinds():
    return re.findall(r'^\s*X\((\w+),', open(os.path.join(SRC, 'kinds.h')).read(), re.M)


def msvc_placements(work):
    root, prefix = os.environ.get('FNV_MSVC9_ROOT'), os.environ.get('WINEPREFIX')
    cl = os.path.join(root or '', 'VC', 'bin', 'cl.exe')
    if not (root and prefix and os.path.isfile(cl)):
        return None
    for f in ('kinds.h', 'msvc_probe.cpp'):
        with open(os.path.join(SRC, f)) as a, open(os.path.join(work, f), 'w') as b:
            b.write(a.read())
    env = dict(os.environ, WINEDEBUG='-all', WINEARCH='win32')
    r = subprocess.run(['wine', cl, '/nologo', '/O2', '/c', 'msvc_probe.cpp'], cwd=work, capture_output=True,
                       text=True, env=env, timeout=300)
    obj = os.path.join(work, 'msvc_probe.obj')
    if r.returncode or not os.path.isfile(obj):
        raise SystemExit('cl failed: ' + (r.stdout + r.stderr)[-400:])
    dis = subprocess.run(['objdump', '-d', '-M', 'intel', '--no-show-raw-insn', obj], capture_output=True,
                         text=True).stdout
    out, name, before = {}, None, []
    for l in dis.splitlines():
        m = re.match(r'^[0-9a-f]+ <\?c_(\w+)@@YAXXZ>:', l)
        if m:
            name, before = m.group(1), []
            continue
        if name is None or not re.match(r'^\s+[0-9a-f]+:', l):
            continue
        ins = l.split(':', 1)[1].strip()
        if ins.startswith('call'):
            t = ' ; '.join(before)
            out[name] = ('ecx' if re.search(r'lea\s+ecx,', t) and 'edx,0x55' in t else
                         'stk' if re.search(r'push\s+e[a-z]x', t) and 'ecx,0x55' in t else
                         'regs' if 'ecx,0x55' in t else '? ' + t)
            name = None
        else:
            before.append(ins)
    return out


def gcc_layouts(work):
    """{kind: layout.Layout} with layout.py's own probe expressions (host g++, -std=gnu++17 as compile.sh)"""
    src, _ = layout.probe_source(os.path.join(SRC, 'kinds.h'), [])
    ks = kinds()
    prog = (src + '#include <stdio.h>\n#define X(name, ...) struct name __VA_ARGS__;\nKINDS(X)\n#undef X\n' +
            'int main() {\n' + ''.join(
                '  printf("%s %%u %%u %%u %%u %%u %%u\\n", (unsigned)sizeof(%s), (unsigned)alignof(%s), '
                '__fnvnx_probe::triv<%s>(), (unsigned)__is_empty(%s), __fnvnx_probe::pod<%s>(), %s);\n'
                % (k, k, k, k, k, k, ALT % (k, k, k, k, k)) for k in ks) + '  return 0;\n}\n')
    p = os.path.join(work, 'gcc_probe.cpp')
    open(p, 'w').write(prog.replace('extern "C" void __fnvnx_sink(void *);', ''))
    exe = os.path.join(work, 'gcc_probe')
    r = subprocess.run(['g++', '-std=gnu++17', '-fms-extensions', '-fpermissive', '-w', '-o', exe, p],
                       capture_output=True, text=True)
    if r.returncode:
        raise SystemExit('gcc probe: ' + r.stderr[-600:])
    out = {}
    for l in subprocess.run([exe], capture_output=True, text=True).stdout.splitlines():
        k, size, al, triv, empty, pod, alt = l.split()
        out[k] = (layout.Layout(int(size), int(al), int(triv), int(empty), 'G1', int(pod)), int(alt))
    return out


def abi2_placement(k, lay):
    n = '?f_%s@@YI?AU%s@@H@Z' % (k, k)          # K __fastcall f_K(int)
    sig = sigs.parse_msvc(n, fnvlink.batch_demangle(fnvlink.UNDNAME, [n])[n])
    vals, ret = abi2.values(sig, lambda t: lay if t == k else None)
    sret = [sp for lab, sp, _, _ in abi2.x86(sig, vals, ret) if lab == 'sret']
    return sret[0] if sret else 'regs'


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--work')
    a = ap.parse_args(argv)
    work = a.work or tempfile.mkdtemp(prefix='v1-fastcall-')
    os.makedirs(work, exist_ok=True)
    ms = msvc_placements(work)
    src = 'MSVC 2008 now' if ms else 'MSVC 2008 table of 2026-11-04 (no FNV_MSVC9_ROOT/WINEPREFIX)'
    ms = ms or MSVC_2008
    lays = gcc_layouts(work)
    bad = bad_alt = 0
    print('placements of the hidden result buffer of K __fastcall f(int): x86 from %s, abi2 from layout.py\'s POD bit'
          % src)
    for k in kinds():
        want, (lay, alt) = ms.get(k), lays[k]
        got = abi2_placement(k, lay)
        got_alt = abi2_placement(k, lay._replace(pod=alt))
        bad_alt += got_alt != want
        rec = MSVC_2008.get(k)
        ok = want == got
        bad |= not ok
        note = '' if rec == want else ' (recorded table says %s)' % rec
        print('%-6s %-8s size %2d pod %d trivial %d: MSVC %-4s abi2 %-4s (with the V1 predicate: pod %d, %s)%s' % (
            'ok' if ok else 'DIFF', k, lay.size, lay.pod, lay.trivial, want, got, alt, got_alt, note))
    print('layout.py predicate: %d of %d kinds differ from MSVC; V1 predicate (ALT): %d' % (
        sum(1 for k in kinds() if abi2_placement(k, lays[k][0]) != ms.get(k)), len(kinds()), bad_alt))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
