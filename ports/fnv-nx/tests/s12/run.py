#!/usr/bin/env python3
"""tests/s12/run.py - tests of the static gate (tools/static). Needs aarch64-linux-gnu-g++, clang 18 and the
libclang-18-dev headers (fnvcheck is built into the scratch dir given as argument).

    tests/s12/run.py <scratch dir>

1. facts.cpp: GCC AArch64 ILP32 code generation facts behind the verdicts (real build flags, -O2), and what each
   proposed flag changes (`// fixed:` lines, compiled with PROPOSED_FLAGS of tools/static/flags.sh).
2. cases.cpp: every fnvcheck check fires where `// expect:` says, and nowhere else.
3. layouts.cpp: the msvc and gnu clang configurations give the expected sizes, and the gnu proxy agrees with real
   GCC AArch64 ILP32 on every record.
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


def main():
    scratch = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, 'out'))
    os.makedirs(scratch, exist_ok=True)
    r = subprocess.run(['bash', os.path.join(STATIC, 'build.sh'), scratch], capture_output=True, text=True)
    check(r.returncode == 0, 'fnvcheck builds (%s)' % r.stderr.strip()[-300:])
    facts(scratch)
    if r.returncode == 0:
        cases(scratch)
        layouts(scratch)
    print('%d failures' % len(FAIL))
    sys.exit(1 if FAIL else 0)


if __name__ == '__main__':
    main()
