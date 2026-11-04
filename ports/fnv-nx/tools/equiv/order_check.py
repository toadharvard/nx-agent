#!/usr/bin/env python3
"""order_check.py - S11 equivalence check of evaluation order for S12's ORDER-OBJECT and ORDER-OPERATOR classes.

tests/s11/order/order.cpp logs, per case, the order in which a call's object expression, operands and arguments ran.
MSVC 2008 (SP1 and RTM, the decomp's own flag sets, run under Wine) gives the order the exe has; the build's GCC
(AArch64 ILP32, tools/link/compile.sh's semantics flags and the fnvnx_order plugin, run under qemu-aarch64) gives the
order the port has. Each case is equivalent when GCC's order is MSVC's.

    order_check.py <scratch> [--msvc] [--gcc] [--record tests/s11/order/order.msvc] [--out R.json] [--md R.md]
                   [--gcc-extra FLAGS]... [--strict]

  --msvc         build and run the MSVC side (needs FNV_MSVC9_ROOT, FNV_MSVC9_RTM_ROOT, FNV_MSVC9_PREFIX, DECOMP
                 or ext/fnv_decomp for tools/msvc.py, Wine). Without it the recorded MSVC results are used
                 (tests/s11/order/order.msvc: our own program's output, no compiler needed on S12's machines).
  --record F     write the MSVC results there (with --msvc).
  --gcc          build and run the GCC side (aarch64-linux-gnu-g++, gcc-13-plugin-dev-aarch64-linux-gnu, qemu-aarch64);
                 variants: `build` (the plugin as compile.sh loads it) plus each --gcc-extra NAME=FLAGS (default:
                 -fstrong-eval-order=some and =none, the candidates ORDERING.md names).
  --strict       exit 1 when a case of the build variant differs from an MSVC order that every MSVC configuration
                 agrees on.

Verdicts per case: `same` (GCC's order is MSVC's in every MSVC configuration), `differs` (MSVC agrees with itself,
GCC does something else), `msvc-varies` (MSVC's own order depends on the compiler or optimization level: the order
is unspecified for the exe too, and each site's x86 code decides; GCC matches some configuration or none, as noted).
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
PORT = HERE.parent.parent
SRC = PORT / 'tests/s11/order/order.cpp'
RECORD = PORT / 'tests/s11/order/order.msvc'
FORMAT = 'fnvnx-order-check/1'
# the decomp's most common flag sets (build/units.json): /O2 and /Od units of both compilers
MSVC_CONFIGS = [('sp1', 'O2', '/MT /O2 /GS /EHsc /Gy'), ('sp1', 'Od', '/MT /Od /Oy- /GS /EHsc /Gy'),
                ('sp1', 'O2Ob1', '/MT /O2 /Ob1 /GS /EHsc /Gy'),
                ('rtm', 'O2', '/MT /O2 /GS /EHsc /Gy'), ('rtm', 'Od', '/MT /Od /Oy- /GS /EHsc /Gy')]
# tools/link/compile.sh's semantics flags that the freestanding qemu harness can take (no -fPIC: static image)
GCC_SEM = ['-fms-extensions', '-frounding-math', '-fno-strict-aliasing', '-fwrapv', '-fno-delete-null-pointer-checks',
           '-ffp-contract=off', '-fpermissive', '-Wno-narrowing', '-mno-outline-atomics']
GCC_VARIANTS = {'strong-some': ['-fstrong-eval-order=some'], 'strong-none': ['-fstrong-eval-order=none']}
LINE = re.compile(r'^(\S+) (\S*)\s*$')


def cases(src=SRC):
    """[(name, S12 class)] in program order, from the `out("name"); // class: X` lines of order.cpp."""
    out = []
    for line in Path(src).read_text().splitlines():
        m = re.search(r'out\("([\w-]+)"\);\s*//\s*class:\s*([A-Z-]+)', line)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def parse(text):
    """program output -> {case: log}"""
    res = {}
    for line in text.replace('\r', '').splitlines():
        m = LINE.match(line)
        if m:
            res[m.group(1)] = m.group(2)
    return res


def run_msvc(scratch, decomp):
    msvc = Path(decomp, 'tools/msvc.py')
    if not msvc.exists():
        raise SystemExit('order_check: no %s (set DECOMP)' % msvc)
    res = {}
    for comp, tag, flags in MSVC_CONFIGS:
        name = '%s-%s' % (comp, tag)
        exe, obj = scratch / ('msvc-%s.exe' % name), scratch / ('msvc-%s.obj' % name)
        r = subprocess.run([sys.executable, str(msvc), '--compiler', comp, '/nologo'] + flags.split() +
                           [str(SRC), '/Fe' + str(exe), '/Fo' + str(obj)], capture_output=True, text=True,
                           errors='replace', cwd=scratch)
        if r.returncode or not exe.exists():
            raise SystemExit('order_check: MSVC %s failed:\n%s' % (name, (r.stdout + r.stderr)[-2000:]))
        env = dict(os.environ, WINEPREFIX=os.environ['FNV_MSVC9_PREFIX'], WINEDEBUG='-all')
        r = subprocess.run(['wine', str(exe)], capture_output=True, text=True, errors='replace', env=env,
                           stdin=subprocess.DEVNULL, timeout=300)
        res[name] = dict(flags=flags, results=parse(r.stdout))
    return res


def run_gcc(scratch, extra_variants):
    sys.path.insert(0, str(PORT / 'tools/verify/v1_link'))
    import qemu_ilp32 as q
    r = subprocess.run(['bash', str(PORT / 'toolchain/order/build.sh'), str(scratch / 'plugin')], capture_output=True,
                       text=True)
    if r.returncode:
        raise SystemExit('order_check: the fnvnx_order plugin does not build: %s' % r.stderr[-500:])
    plugin = r.stdout.strip()
    build = ['-fplugin=' + plugin, '-fplugin-arg-fnvnx_order-mode=volatile', '-fplugin-arg-fnvnx_order-args=rtl',
             '-fplugin-arg-fnvnx_order-refcount=weak']
    variants = dict(build=build)
    for name, flags in extra_variants.items():
        variants[name] = build + flags
    base = list(q.CXXFLAGS)
    res = {}
    for name, flags in variants.items():
        q.CXXFLAGS = base + GCC_SEM + flags
        try:
            exe = q.build([str(SRC)], str(scratch / ('gcc-' + name)))
        finally:
            q.CXXFLAGS = base
        rc, text = q.run(exe)
        res[name] = dict(flags=' '.join(flags[4:]) or '(compile.sh)', rc=rc, results=parse(text))
    return res


def compare(case_list, msvc, gcc):
    rows = []
    for name, cls in case_list:
        mo = {cfg: v['results'].get(name) for cfg, v in msvc.items()}
        orders = sorted({o for o in mo.values() if o is not None})
        go = {var: v['results'].get(name) for var, v in gcc.items()}
        b = go.get('build')
        if len(orders) == 1:
            verdict = 'same' if b == orders[0] else 'differs'
        elif not orders:
            verdict = 'no-msvc'
        else:
            verdict = 'msvc-varies'
        rows.append(dict(case=name, cls=cls, msvc=mo, gcc=go, verdict=verdict,
                         gcc_matches=[cfg for cfg, o in mo.items() if o == b]))
    return rows


def summary(rows):
    out = {}
    for r in rows:
        s = out.setdefault(r['cls'], {})
        s[r['verdict']] = s.get(r['verdict'], 0) + 1
    return out


def markdown(rows, msvc, gcc):
    cfgs, vars_ = list(msvc), list(gcc)
    out = ['# Evaluation order: MSVC 2008 vs the build (tools/equiv/order_check.py)', '',
           'Log letters: o/p the object or left operand (n `next()`, m the member pointer), a/b/c the arguments or '
           'right operand, in the order they ran; after `:` what the callee received.', '',
           '| case | class | ' + ' | '.join('MSVC ' + c for c in cfgs) + ' | ' +
           ' | '.join('GCC ' + v for v in vars_) + ' | verdict |',
           '|---|---|' + '---|' * (len(cfgs) + len(vars_)) + '---|']
    for r in rows:
        out.append('| %s | %s | %s | %s | %s |' % (r['case'], r['cls'], ' | '.join(str(r['msvc'][c]) for c in cfgs),
                                                    ' | '.join(str(r['gcc'].get(v)) for v in vars_), r['verdict']))
    out += ['', '| class | ' + ' | '.join(('same', 'differs', 'msvc-varies')) + ' |', '|---|---:|---:|---:|']
    for cls, s in summary(rows).items():
        out.append('| %s | %d | %d | %d |' % (cls, s.get('same', 0), s.get('differs', 0), s.get('msvc-varies', 0)))
    return '\n'.join(out) + '\n'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('scratch')
    ap.add_argument('--msvc', action='store_true')
    ap.add_argument('--gcc', action='store_true')
    ap.add_argument('--record')
    ap.add_argument('--msvc-results', default=str(RECORD), help='recorded MSVC results (without --msvc)')
    ap.add_argument('--gcc-extra', action='append', help='NAME=FLAGS (space-separated) for another GCC variant')
    ap.add_argument('--out')
    ap.add_argument('--md')
    ap.add_argument('--strict', action='store_true')
    a = ap.parse_args()
    scratch = Path(a.scratch).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    if a.msvc:
        msvc = run_msvc(scratch, os.environ.get('DECOMP') or str(PORT / 'ext/fnv_decomp'))
        if a.record:
            Path(a.record).write_text(json.dumps(dict(format=FORMAT, source='tests/s11/order/order.cpp',
                                                      msvc=msvc), indent=1, sort_keys=True) + '\n')
    else:
        msvc = json.loads(Path(a.msvc_results).read_text())['msvc']
    extra = dict(GCC_VARIANTS)
    if a.gcc_extra:
        extra = {k: v.split() for k, _, v in (g.partition('=') for g in a.gcc_extra)}
    gcc = run_gcc(scratch, extra) if a.gcc else {}
    rows = compare(cases(), msvc, gcc)
    if a.out:
        Path(a.out).write_text(json.dumps(dict(format=FORMAT, msvc=msvc, gcc=gcc, cases=rows,
                                               summary=summary(rows)), indent=1) + '\n')
    md = markdown(rows, msvc, gcc)
    if a.md:
        Path(a.md).write_text(md)
    print(md)
    if a.strict and gcc and any(r['verdict'] == 'differs' for r in rows):
        sys.exit(1)


if __name__ == '__main__':
    main()
