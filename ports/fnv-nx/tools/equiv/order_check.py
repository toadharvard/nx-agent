#!/usr/bin/env python3
"""order_check.py - S11 equivalence check of evaluation order for S12's ORDER-OBJECT and ORDER-OPERATOR classes.

tests/s11/order/order.cpp logs, per case, the order in which a call's object expression, operands and arguments ran.
MSVC 2008 (SP1 and RTM, the decomp's own flag sets, run under Wine) gives the order the exe has; the build's GCC
(AArch64 ILP32, tools/link/compile.sh's semantics flags and the fnvnx_order plugin, run under qemu-aarch64) gives the
order the port has. Each case is equivalent when GCC's order is MSVC's.

    order_check.py <scratch> [--msvc] [--gcc] [--record tests/s11/order/order.msvc] [--out R.json] [--md R.md]
                   [--gcc-extra NAME=FLAGS]... [--strict [--strict-variant NAME]]
    order_check.py <scratch> --msvc-stdout SRC.cpp OUT.expect

  --msvc         build and run the MSVC side (needs FNV_MSVC9_ROOT, FNV_MSVC9_RTM_ROOT, FNV_MSVC9_PREFIX, DECOMP
                 or ext/fnv_decomp for tools/msvc.py, Wine). Without it the recorded MSVC results are used
                 (tests/s11/order/order.msvc: our own program's output, no compiler needed on S12's machines).
  --record F     write the MSVC results there (with --msvc).
  --gcc          build and run the GCC side (aarch64-linux-gnu-g++, gcc-13-plugin-dev-aarch64-linux-gnu, qemu-aarch64);
                 variants: `build` (the plugin with every ORDER_* default of tools/link/compile.sh, read from that
                 file, environment overrides as there) plus each --gcc-extra NAME=FLAGS (default:
                 -fstrong-eval-order=some and =none, the candidates ORDERING.md names).
  --strict       needs --gcc (exit 2 without). Exit 1 when a case of the build variant (or --strict-variant NAME, e.g. a --gcc-extra candidate)
                 differs from an MSVC order that every MSVC configuration agrees on.
  --msvc-stdout  build another order program (plain C++03, prints its own log) in every MSVC configuration, run it
                 under Wine, and write its stdout to OUT when all configurations print the same; otherwise exit 1 and
                 name the lines that vary. S12's tests/s12/order/evalorder.cpp -> evalorder.msvc.expect.

Each GCC variant gets its own verdict per case (`verdicts`); `verdict` is the build variant's. Verdicts per case: `same` (GCC's order is MSVC's in every MSVC configuration), `differs` (MSVC agrees with itself,
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
# S12's switches (fnvnx_order object= / operator=, S12 2e2e9f9): measured whenever the plugin source has them
S12_VARIANTS = {'object-last': ['-fplugin-arg-fnvnx_order-object=last'],
                'object-last-rtl': ['-fplugin-arg-fnvnx_order-object=last', '-fplugin-arg-fnvnx_order-operator=rtl']}
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


def msvc_outputs(scratch, decomp, src=SRC):
    """{config: (flags, stdout)} of src built and run in every MSVC configuration (CR stripped)."""
    msvc = Path(decomp, 'tools/msvc.py')
    if not msvc.exists():
        raise SystemExit('order_check: no %s (set DECOMP)' % msvc)
    stem, res = Path(src).stem, {}
    for comp, tag, flags in MSVC_CONFIGS:
        name = '%s-%s' % (comp, tag)
        exe, obj = scratch / ('msvc-%s-%s.exe' % (stem, name)), scratch / ('msvc-%s-%s.obj' % (stem, name))
        r = subprocess.run([sys.executable, str(msvc), '--compiler', comp, '/nologo'] + flags.split() +
                           [str(src), '/Fe' + str(exe), '/Fo' + str(obj)], capture_output=True, text=True,
                           errors='replace', cwd=scratch)
        if r.returncode or not exe.exists():
            raise SystemExit('order_check: MSVC %s failed:\n%s' % (name, (r.stdout + r.stderr)[-2000:]))
        env = dict(os.environ, WINEPREFIX=os.environ['FNV_MSVC9_PREFIX'], WINEDEBUG='-all')
        r = subprocess.run(['wine', str(exe)], capture_output=True, text=True, errors='replace', env=env,
                           stdin=subprocess.DEVNULL, timeout=300)
        res[name] = (flags, r.stdout.replace('\r', ''))
    return res


def run_msvc(scratch, decomp):
    return {name: dict(flags=flags, results=parse(out)) for name, (flags, out) in msvc_outputs(scratch, decomp).items()}


def agreed_stdout(outputs):
    """(text, []) when every configuration printed the same, else (None, [(line number, {config: line})])."""
    texts = {cfg: out for cfg, (_, out) in outputs.items()}
    if len(set(texts.values())) == 1:
        return next(iter(texts.values())), []
    split = {cfg: t.splitlines() for cfg, t in texts.items()}
    n = max(len(v) for v in split.values())
    varies = []
    for i in range(n):
        at = {cfg: (v[i] if i < len(v) else None) for cfg, v in split.items()}
        if len(set(at.values())) > 1:
            varies.append((i + 1, at))
    return None, varies


COMPILE_SH = PORT / 'tools/link/compile.sh'
ORDER_KEYS = (('ORDER_MODE', 'mode'), ('ORDER_ARGS', 'args'), ('ORDER_REFCOUNT', 'refcount'),
              ('ORDER_OBJECT', 'object'), ('ORDER_OPERATOR', 'operator'), ('ORDER_ASSIGN', 'assign'))


def compile_sh_order_args(text=None, need=()):
    """the fnvnx_order plugin arguments compile.sh passes by default (`ORDER_X=${ORDER_X:-v}` lines whose variable it
    hands to `-fplugin-arg-fnvnx_order-<key>`); the environment overrides them as it does for compile.sh"""
    text = COMPILE_SH.read_text() if text is None else text
    out = []
    for var, key in ORDER_KEYS:
        m = re.search(r'^%s=\$\{%s:-([^}]*)\}' % (var, var), text, re.M)
        if m and ('fnvnx_order-%s=' % key) in text:
            out.append('-fplugin-arg-fnvnx_order-%s=%s' % (key, os.environ.get(var, m.group(1))))
    for key in ('mode=',) + tuple(need):
        if not any(a.startswith('-fplugin-arg-fnvnx_order-' + key) for a in out):
            raise SystemExit('order_check: compile.sh passes no fnvnx_order %s switch (%s)' % (key.rstrip('='),
                                                                                              COMPILE_SH))
    return out


def run_gcc(scratch, extra_variants):
    sys.path.insert(0, str(PORT / 'tools/verify/v1_link'))
    import qemu_ilp32 as q
    r = subprocess.run(['bash', str(PORT / 'toolchain/order/build.sh'), str(scratch / 'plugin')], capture_output=True,
                       text=True)
    if r.returncode:
        raise SystemExit('order_check: the fnvnx_order plugin does not build: %s' % r.stderr[-500:])
    plugin = r.stdout.strip()
    # the plugin exactly as compile.sh loads it; each named switch must be one compile.sh passes
    build = ['-fplugin=' + plugin, *compile_sh_order_args(need=('mode=', 'args=', 'refcount=', 'object=', 'operator=', 'assign='))]
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
        res[name] = dict(flags=' '.join(flags[1:]), rc=rc, results=parse(text))
    return res


def compare(case_list, msvc, gcc):
    rows = []
    for name, cls in case_list:
        mo = {cfg: v['results'].get(name) for cfg, v in msvc.items()}
        orders = sorted({o for o in mo.values() if o is not None})
        go = {var: v['results'].get(name) for var, v in gcc.items()}
        b = go.get('build')
        complete = bool(mo) and all(o is not None for o in mo.values())
        verdicts = {var: verdict_of(orders, o, complete) for var, o in go.items()}
        rows.append(dict(case=name, cls=cls, msvc=mo, gcc=go, verdict=verdict_of(orders, b, complete), verdicts=verdicts,
                         gcc_matches=[cfg for cfg, o in mo.items() if o == b]))
    return rows


def verdict_of(orders, got, complete=True):
    """same / differs / msvc-varies; no-msvc when no configuration logged the case, msvc-incomplete when some did
    not (neither counts as agreement)"""
    if not orders:
        return 'no-msvc'
    if not complete:
        return 'msvc-incomplete'
    if len(orders) > 1:
        return 'msvc-varies'
    return 'same' if got == orders[0] else 'differs'


STRICT_BAD = ('differs', 'no-msvc', 'msvc-incomplete')


def strict_failures(rows, variant='build'):
    """cases where the variant differs from an order every MSVC configuration agrees on, or where the MSVC record is
    missing in some configuration (no evidence is not agreement); msvc-varies is allowed"""
    return [r['case'] for r in rows if r.get('verdicts', {}).get(variant, r['verdict'] if variant == 'build' else None)
            in STRICT_BAD]


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
    if len(vars_) > 1:
        out += ['', '| GCC variant | cases that differ from an agreed MSVC order |', '|---|---|']
        for v in vars_:
            bad = strict_failures(rows, v)
            out.append('| %s | %d%s |' % (v, len(bad), (': ' + ', '.join(bad)) if bad else ''))
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
    ap.add_argument('--strict-variant', default='build')
    ap.add_argument('--msvc-stdout', nargs=2, metavar=('SRC', 'OUT'))
    a = ap.parse_args()
    scratch = Path(a.scratch).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    if a.strict and not a.gcc:
        ap.error('--strict needs --gcc (no GCC evidence to check)')
    decomp = os.environ.get('DECOMP') or str(PORT / 'ext/fnv_decomp')
    if a.msvc_stdout:
        text, varies = agreed_stdout(msvc_outputs(scratch, decomp, Path(a.msvc_stdout[0]).resolve()))
        if text is None:
            for line, at in varies:
                print('line %d varies: %s' % (line, ', '.join('%s %r' % kv for kv in sorted(at.items()))))
            sys.exit(1)
        Path(a.msvc_stdout[1]).write_text(text)
        print('order_check: %d lines, identical in %s' % (len(text.splitlines()), ', '.join(
            '%s-%s' % (c, t) for c, t, _ in MSVC_CONFIGS)))
        return
    if a.msvc:
        msvc = run_msvc(scratch, decomp)
        if a.record:
            Path(a.record).write_text(json.dumps(dict(format=FORMAT, source='tests/s11/order/order.cpp',
                                                      msvc=msvc), indent=1, sort_keys=True) + '\n')
    else:
        msvc = json.loads(Path(a.msvc_results).read_text())['msvc']
    extra = dict(GCC_VARIANTS)
    if '"object"' in (PORT / 'toolchain/order/fnvnx_order.cc').read_text():
        extra.update(S12_VARIANTS)      # A/B against the build (compile.sh's own switches)
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
    if a.strict:
        if not gcc:
            print('order_check: --strict needs --gcc (no GCC evidence)', file=sys.stderr)
            sys.exit(2)
        if a.strict_variant not in gcc:
            raise SystemExit('order_check: no GCC variant %s (have %s)' % (a.strict_variant, ', '.join(gcc)))
        if strict_failures(rows, a.strict_variant):
            sys.exit(1)


if __name__ == '__main__':
    main()
