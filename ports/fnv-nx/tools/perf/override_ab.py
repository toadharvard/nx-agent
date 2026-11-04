#!/usr/bin/env python3
"""override_ab.py - before/after of a native override (tools/link/overrides) on S13's differential harness: dynamic
A64 instructions and agreement with the MSVC oracle, on the same fuzzed cases (stream S15-simd, docs/PERF.md 11.6).

    override_ab.py <source>:<0xaddr> --override <file.c> --native <symbol> [--seeds N] [--keep DIR]

The link of the harness profile (FNVNX_DFPROFILE / FNVNX_DFLINK, tools/difffuzz/config.py) is the "before". The
"after" is a copy of it (symlinks) whose unit object has the function's definitions (its C++ name and the
`__fnv_<addr>` alias, every global symbol at the function's value) made weak, relinked (`ld -r`) with the override
compiled as tools/link/overrides/build.sh compiles it, plus aliases of those names to the override's symbol: the
binding `fnvlink.py resolve --override` makes in the game link, here for this unit only. Then dyncount.py runs the
function on both, with fresh A64 caches. Prints both dyncount tables. Counts only: the copies stay in --keep (default
a temporary directory) and are derived from the decomp; never commit them.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(HERE, '..', 'difffuzz'))
import config  # noqa: E402  (reads the profile)

CC = os.environ.get('CC', 'aarch64-linux-gnu-gcc')
NM = os.environ.get('NM', 'aarch64-linux-gnu-nm')
OBJCOPY = os.environ.get('OBJCOPY', 'aarch64-linux-gnu-objcopy')
LD = os.environ.get('LD', 'aarch64-linux-gnu-ld')


def build_flags():
    txt = open(os.path.join(PORT, 'tools', 'link', 'overrides', 'build.sh')).read()
    return re.search(r'\$CC (.*?) -c ', txt).group(1).split()


def patched_link(link, source, addr, override, native, out):
    """-> a link directory like `link` whose unit object of `source` binds the function at `addr` to `native`."""
    os.makedirs(os.path.join(out, 'fixed'), exist_ok=True)
    for d in os.listdir(link):
        if d != 'fixed' and not os.path.exists(os.path.join(out, d)):
            os.symlink(os.path.join(link, d), os.path.join(out, d))
    name = config.obj_name(source)
    for f in os.listdir(os.path.join(link, 'fixed')):
        if f != name and not os.path.exists(os.path.join(out, 'fixed', f)):
            os.symlink(os.path.join(link, 'fixed', f), os.path.join(out, 'fixed', f))
    orig = os.path.join(link, 'fixed', name)
    syms = subprocess.run([NM, orig], capture_output=True, text=True, check=True).stdout.split('\n')
    alias = '__fnv_%08x' % addr
    value = next((ln.split()[0] for ln in syms if ln.endswith(' ' + alias) and ' T ' in ln), None)
    if value is None:
        raise SystemExit('%s defines no %s' % (orig, alias))
    names = [ln.split()[2] for ln in syms if len(ln.split()) == 3 and ln.split()[0] == value and ln.split()[1] == 'T']
    tmp = os.path.join(out, 'work')
    os.makedirs(tmp, exist_ok=True)
    weak = os.path.join(tmp, 'unit_weak.o')
    subprocess.run([OBJCOPY, *['--weaken-symbol=' + n for n in names], orig, weak], check=True)
    wrap = os.path.join(tmp, 'override_alias.c')
    open(wrap, 'w').write('#include "%s"\n' % override + ''.join(
        'extern void s15_alias_%d(void) __asm__("%s") __attribute__((alias("%s")));\n' % (i, n, native)
        for i, n in enumerate(names)))
    ovr = os.path.join(tmp, 'override.o')
    subprocess.run([CC, *build_flags(), '-Wno-attribute-alias', '-c', wrap, '-o', ovr], check=True)
    dst = os.path.join(out, 'fixed', name)
    if os.path.lexists(dst):
        os.remove(dst)
    subprocess.run([LD, '-m', 'aarch64linux32', '-r', weak, ovr, '-o', dst], check=True)
    return names


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('target', help='<source>:<0xaddr>')
    ap.add_argument('--override', required=True)
    ap.add_argument('--native', required=True, help="the override's symbol")
    ap.add_argument('--seeds', type=int, default=8)
    ap.add_argument('--keep')
    a = ap.parse_args()
    source, _, addr = a.target.rpartition(':')
    out = a.keep or tempfile.mkdtemp(prefix='override_ab_')
    names = patched_link(str(config.LINK), source, int(addr, 16), os.path.abspath(a.override), a.native,
                         os.path.join(out, 'link'))
    print('bound to %s: %s' % (a.native, ' '.join(names)))
    dc = [sys.executable, os.path.join(HERE, 'dyncount.py'), a.target, '--seeds', str(a.seeds)]
    for tag, link in (('before', str(config.LINK)), ('after', os.path.join(out, 'link'))):
        env = dict(os.environ, FNVNX_DFLINK=link, FNVNX_A64OBJ=os.path.join(link, 'fixed'),
                   FNVNX_DFA64=os.path.join(out, 'a64_' + tag), FNVNX_DFWORK=os.path.join(out, 'work_' + tag))
        env.pop('FNVNX_DFPROFILE', None)
        print('== %s' % tag)
        sys.stdout.flush()
        subprocess.run(dc, env=env, check=False)
    if not a.keep:
        shutil.rmtree(out, ignore_errors=True)


if __name__ == '__main__':
    main()
