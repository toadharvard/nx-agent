#!/usr/bin/env python3
"""tests/s12/run.py - tests of the static gate (tools/static). Needs aarch64-linux-gnu-g++, clang 18 and the
libclang-18-dev headers (fnvcheck is built into the scratch dir given as argument).

    tests/s12/run.py <scratch dir>

1. facts.cpp: GCC AArch64 ILP32 code generation facts behind the verdicts (real build flags, -O2), and what each
   proposed flag changes (`// fixed:` lines, compiled with PROPOSED_FLAGS of tools/static/flags.sh).
2. cases.cpp: every fnvcheck check fires where `// expect:` says, and nowhere else.
3. layouts.cpp: the msvc and gnu clang configurations give the expected sizes, and the gnu proxy agrees with real
   GCC AArch64 ILP32 on every record.
4. the layout steps of run.sh (clang_pass.sh, layout_diff.py, layout_probe.py) and report.py on a one-unit tree of
   layouts.cpp.
5. crosscheck.py attributes a site to the tagged function whose body contains it.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))
STATIC = os.path.join(PORT, 'tools', 'static')
FAIL = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        FAIL.append(what)


def bash_array(name):
    """an array of flags.sh / clang_env.sh, expanded by bash"""
    return subprocess.run(['bash', '-c', '. "%s/flags.sh"; printf "%%s\\n" "${%s[@]}"' % (STATIC, name)],
                          capture_output=True, text=True, check=True).stdout.split('\n')[:-1]


def clang_args(scratch, cfg):
    script = ('. "%s/clang_env.sh"; clang_env_setup "%s" "%s" >/dev/null; printf "%%s\\n" "${CLANG_%s[@]}" '
              '-resource-dir "$($CLANG -print-resource-dir)"' % (STATIC, scratch, PORT, cfg.upper()))
    return subprocess.run(['bash', '-c', script], capture_output=True, text=True, check=True).stdout.split('\n')[:-1]


def functions(asm):
    """mangled label -> body text (instructions and local labels)"""
    out, cur = {}, None
    for line in asm.splitlines():
        m = re.match(r'^([A-Za-z_][\w.$]*):', line)
        if m and not m.group(1).startswith('.L'):
            cur = m.group(1)
            out[cur] = []
        elif cur and (line.startswith('\t') and not line.startswith('\t.') or line.startswith('.L')):
            out[cur].append(line)
    return {k: '\n'.join(v) for k, v in out.items()}


def body_of(funcs, name):
    for k, v in funcs.items():
        if re.match(r'_Z%d%s' % (len(name), re.escape(name)), k):
            return v
    return None


def facts(scratch):
    src = os.path.join(HERE, 'facts.cpp')
    base = bash_array('BUILD_FLAGS')
    for tag, extra in (('fact', []), ('fixed', bash_array('PROPOSED_FLAGS'))):
        asm = os.path.join(scratch, 'facts_%s.s' % tag)
        r = subprocess.run(['aarch64-linux-gnu-g++'] + base + extra + ['-O2', '-w', '-S', '-o', asm, src],
                           capture_output=True, text=True)
        check(r.returncode == 0, '%s: facts.cpp compiles (%s)' % (tag, r.stderr.strip()[:200]))
        if r.returncode:
            continue
        funcs = functions(open(asm).read())
        for line in open(src):
            m = re.match(r'^// %s: (\w+)\s+(\S.*?)\s{2,}\(' % tag, line) or re.match(r'^// %s: (\w+)\s+(\S+)\s*$' % tag, line)
            if not m:
                continue
            fn, rx = m.group(1), m.group(2)
            body = body_of(funcs, fn)
            neg = rx.startswith('!')
            hit = body is not None and re.search(rx[1:] if neg else rx, body, re.S | re.M) is not None
            check(body is not None and hit != neg, '%s %s: %s' % (tag, fn, rx))


def run_fnvcheck(scratch, cfg, src, extra):
    exe = os.path.join(scratch, 'bin', 'fnvcheck')
    args = [exe, '--root=' + HERE] + extra + [src, '--'] + clang_args(scratch, cfg)
    r = subprocess.run(args, capture_output=True, text=True, cwd=HERE)
    return r.stdout


def cases(scratch):
    rows = run_fnvcheck(scratch, 'gnu', 'cases.cpp', []).splitlines()
    got = {}
    for r in rows:
        p = r.split('\t')
        got.setdefault(int(p[2]), set()).add(p[0] + ('/' + p[4] if p[0] == 'ORDER-ARGS' else ''))
    want = {}
    for n, line in enumerate(open(os.path.join(HERE, 'cases.cpp')), 1):
        m = re.search(r'// expect: (.*)$', line)
        if m:
            toks = m.group(1).split()
            w = set()
            for i, t in enumerate(toks):
                if t == 'ORDER-ARGS':
                    w.add('ORDER-ARGS/' + toks[i + 1])
                elif re.match(r'^[A-Z][A-Z0-9-]+$', t):
                    w.add(t)
            want[n] = w
    for n in sorted(set(want) | set(got)):
        check(want.get(n, set()) == got.get(n, set()),
              'cases.cpp:%d expect %s got %s' % (n, sorted(want.get(n, set())), sorted(got.get(n, set()))))


def layouts(scratch):
    src = os.path.join(HERE, 'layouts.cpp')
    sizes = {}
    for cfg in ('msvc', 'gnu'):
        for r in run_fnvcheck(scratch, cfg, 'layouts.cpp', ['--layout', '--no-ast']).splitlines():
            p = r.split('\t')
            if p[0] == 'L':
                sizes[(cfg, p[1].split('|')[1])] = int(p[2])
    want = []
    for line in open(src):
        m = re.search(r'// layout: (\w+) msvc=(\d+) gnu=(\d+)', line)
        if m:
            want.append((m.group(1), int(m.group(2)), int(m.group(3))))
    for name, ms, gs in want:
        check(sizes.get(('msvc', name)) == ms, 'layouts %s msvc %s == %d' % (name, sizes.get(('msvc', name)), ms))
        check(sizes.get(('gnu', name)) == gs, 'layouts %s gnu proxy %s == %d' % (name, sizes.get(('gnu', name)), gs))
    # real GCC: the sizes[] array of layouts.cpp, in the order of the `layout:` lines
    asm = os.path.join(scratch, 'layouts.s')
    inc = ['-include', os.path.join(PORT, 'shim', 'include', 'fnvnx_compat.h'), '-I' + os.path.join(PORT, 'shim', 'include'),
           '-I' + os.environ.get('SSE2NEON', os.path.join(PORT, 'ext', 'sse2neon'))]
    r = subprocess.run(['aarch64-linux-gnu-g++'] + bash_array('BUILD_FLAGS') + ['-w', '-S', '-o', asm] + inc + [src],
                       capture_output=True, text=True)
    check(r.returncode == 0, 'layouts.cpp compiles with GCC (%s)' % r.stderr.strip()[:200])
    if r.returncode == 0:
        vals = re.findall(r'^\t\.word\t(\d+)', open(asm).read().split('sizes:')[1], re.M)[:len(want)]
        for (name, ms, gs), v in zip(want, vals):
            check(int(v) == gs, 'layouts %s GCC aarch64 ilp32 %s == gnu proxy %d' % (name, v, gs))


def pipeline(scratch):
    """the layout steps of run.sh on a one-unit tree made of layouts.cpp: diff causes, and GCC agrees with the proxy"""
    import json
    import shutil
    e = os.path.join(scratch, 'e2e')
    shutil.rmtree(e, ignore_errors=True)
    for d in ('src', 'include', 'build'):
        os.makedirs(os.path.join(e, 'tree', d))
    shutil.copy(os.path.join(HERE, 'layouts.cpp'), os.path.join(e, 'tree', 'src'))
    json.dump({'units': [{'source': 'src/layouts.cpp', 'flags': ['/O2']}]}, open(os.path.join(e, 'tree', 'build', 'units.json'), 'w'))
    for cfg in ('gnu', 'msvc'):
        subprocess.run(['bash', os.path.join(STATIC, 'clang_pass.sh'), 'tree', 'out', cfg, '1'], cwd=e, capture_output=True)
    subprocess.run(['python3', os.path.join(STATIC, 'layout_diff.py'), 'out'], cwd=e, capture_output=True)
    rows = [l.split('\t') for l in open(os.path.join(e, 'out', 'layout', 'diff.tsv')).read().splitlines()[1:]]
    causes = {r[1].split('|')[1]: r[0] for r in rows}
    want = {'B1': 'bitfield', 'B2': 'bitfield', 'B3': 'bitfield', 'B4': 'bitfield', 'P1': 'pack-align',
            'P2': 'pack-align', 'TwoEmpty': 'empty-base', 'VEmpty': 'empty-base-offset', 'AlignPre': 'align-ignored',
            'UsesPre': 'align-ignored'}
    check(causes == want, 'layout_diff causes %s' % sorted(causes.items()))
    r = subprocess.run(['python3', os.path.join(STATIC, 'layout_probe.py'), 'tree', 'out'], cwd=e, capture_output=True, text=True)
    res = [l.split('\t') for l in open(os.path.join(e, 'out', 'probe', 'result.tsv')).read().splitlines()[1:]]
    check(len(res) >= 20 and all(x[2] == 'ok' for x in res), 'layout_probe: GCC agrees with the proxy (%s)' % r.stdout.strip())
    # report.py: clang's "place it after" warning is the ALIGN-IGNORED root, the two records LAYOUT-ALIGN-IGNORED
    r = subprocess.run(['python3', os.path.join(STATIC, 'report.py'), 'tree', 'out'], cwd=e, capture_output=True, text=True)
    counts = dict(l.rsplit('\t', 1) for l in open(os.path.join(e, 'out', 'counts.tsv')).read().splitlines())
    check(counts.get('ALIGN-IGNORED') == '1' and counts.get('LAYOUT-ALIGN-IGNORED') == '2',
          'report: ALIGN-IGNORED %s, LAYOUT-ALIGN-IGNORED %s (%s)' % (counts.get('ALIGN-IGNORED'),
                                                                      counts.get('LAYOUT-ALIGN-IGNORED'), r.stderr[-200:]))


def crosscheck():
    """crosscheck.py: a site belongs to the function whose tagged, brace-matched body contains it"""
    sys.path.insert(0, STATIC)
    import crosscheck as cc
    text = ('// @fnv 0x00401000\nint f(int a)\n{\n  const char *s = "}";\n  return a; // }\n}\n'
            'struct Between { int x; };\n'
            '// @fnv 0x00401010\nint g();\n'
            '// @fnv 0x00401020\nvoid h() { /* { */ if (1) { } }\n')
    spans = cc.function_spans(text)
    check(spans == [(1, 6, 0x401000), (10, 11, 0x401020)], 'crosscheck function_spans %s' % spans)


def main():
    scratch = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, 'out'))
    os.makedirs(scratch, exist_ok=True)
    r = subprocess.run(['bash', os.path.join(STATIC, 'build.sh'), scratch], capture_output=True, text=True)
    check(r.returncode == 0, 'fnvcheck builds (%s)' % r.stderr.strip()[-300:])
    facts(scratch)
    crosscheck()
    if r.returncode == 0:
        cases(scratch)
        layouts(scratch)
        pipeline(scratch)
    print('%d failures' % len(FAIL))
    sys.exit(1 if FAIL else 0)


if __name__ == '__main__':
    main()
