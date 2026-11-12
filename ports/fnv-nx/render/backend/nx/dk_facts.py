#!/usr/bin/env python3
"""fnv-nx L6: facts of the pinned deko3d headers, extracted by the compiler (no prose regexes).

  dk_facts.py --inc <deko3d include dir> --sha <deko3d commit> [--src <deko3d source dir>] [--cc gcc]
              [--cross <aarch64 gcc>] --out dk_facts.tsv [--check dk_facts.tsv]

Method (all facts come from the C compiler, never from guessing):
  1. `cc -E -P` and `cc -E -dM` give the preprocessed header and its macro table. A tokenizer (not a text regex)
     walks the preprocessed token stream and lists the enum typedefs with their enumerator names, the struct/union
     typedefs with bodies, and the object-like DK_* macros whose body is a constant expression.
  2. A generated C probe (`#include <deko3d.h>`; one printf per name) is compiled and run on the build host: every
     enumerator value, macro value, sizeof and alignof is printed by the compiler's own evaluation.
  3. With --cross the same facts are re-proven as _Static_assert with the Switch compiler (compile only, nothing
     runs): the LP64 aarch64 layout equals the host-probe layout, or the script fails.
  4. With --src the pinned deko3d source's own format table (source/maxwell/format_traits.inc, which the library
     compiles into libdeko3d) is compiled with macros that print each row, giving per DkImageFormat the hardware
     texture format, the channel bit widths, bytes per block, block size, the TIC swizzle and the render capability.
Output: TSV with columns kind, name, value. kind is `sha`, an enum type name, `macro`, `sizeof`, `alignof`, or `layout`.
deko3d is zlib-licensed public code: the TSV is public."""
import argparse, os, re, subprocess, sys, tempfile

SKIP_MACROS = ('DK_CONSTEXPR', 'DK_DECL_', '__DK_', 'DK_DEPRECATED')


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        sys.exit('dk_facts: %s failed:\n%s%s' % (' '.join(cmd[:3]), r.stdout[-2000:], r.stderr[-2000:]))
    return r.stdout


TOKEN = re.compile(r'[A-Za-z_][A-Za-z0-9_]*|0[xX][0-9a-fA-F]+[uUlL]*|[0-9]+[uUlL]*|<<|>>|[{}();,=*\[\]:#<>+\-|&~^/%!]|\S')


def tokens(text):
    return TOKEN.findall(text)


def scan(toks):
    """Walk the token stream once: enum typedefs -> {name: [enumerators]}, struct/union typedefs -> [names]."""
    enums, structs = {}, []
    i, n = 0, len(toks)
    while i < n:
        if toks[i] == 'enum' and i + 1 < n and toks[i + 1] == '{':        # anonymous enum (DkImageFlags_*): kind = enumerator prefix
            m = toks.index('}', i)
            names, item = [], True
            for t in toks[i + 2:m]:
                if t == ',': item = True
                elif item and re.match(r'[A-Za-z_]', t): names.append(t)
                if t != ',': item = False
            for nm in names:
                enums.setdefault(nm.rsplit('_', 1)[0], []).append(nm)
            i = m + 1
            continue
        if toks[i] != 'typedef':
            i += 1
            continue
        j = i + 1
        if j < n and toks[j] in ('enum', 'struct', 'union'):
            kind = toks[j]
            k = j + 1
            if k < n and toks[k] != '{':
                k += 1                        # tag
            if k < n and toks[k] == '{':
                depth, m = 0, k
                while m < n:
                    if toks[m] == '{': depth += 1
                    elif toks[m] == '}':
                        depth -= 1
                        if depth == 0: break
                    m += 1
                body = toks[k + 1:m]
                name = toks[m + 1]
                if kind == 'enum':
                    names, depth, item = [], 0, True
                    for t in body:
                        if t in '({[': depth += 1
                        elif t in ')}]': depth -= 1
                        elif t == ',' and depth == 0:
                            item = True
                            continue
                        elif item and re.match(r'[A-Za-z_]', t):
                            names.append(t)
                        item = False
                    enums[name] = names
                else:
                    structs.append(name)
                i = m + 2
                continue
        i += 1
    return enums, structs


def const_macros(dm):
    out = []
    for line in dm.splitlines():
        p = line.split(None, 2)
        if len(p) < 3 or p[0] != '#define' or not p[1].startswith('DK_') or '(' in p[1]:
            continue
        if any(p[1].startswith(s) for s in SKIP_MACROS):
            continue
        body = tokens(p[2])
        if all(re.match(r'0[xX][0-9a-fA-F]+[uUlL]*$|[0-9]+[uUlL]*$|[A-Za-z_]\w*$|<<|>>|[()|+\-*/]', t) for t in body) \
                and any(re.match(r'\d|0[xX]|UINT|INT', t) for t in body):
            out.append(p[1])
    return sorted(set(out))


def build_facts(cc, inc):
    raw = run([cc, '-x', 'c', '-E', '-I', inc, os.path.join(inc, 'deko3d.h')])
    pre, keep = [], False                 # linemarkers `# N "file"`: keep only text that comes from the deko3d include dir
    for line in raw.splitlines():
        m = re.match(r'# \d+ "([^"]*)"', line)
        if m:
            keep = os.path.realpath(m.group(1)).startswith(os.path.realpath(inc) + os.sep)
        elif keep:
            pre.append(line)
    pre = '\n'.join(pre)
    dm = run([cc, '-x', 'c', '-E', '-dM', '-I', inc, os.path.join(inc, 'deko3d.h')])
    enums, structs = scan(tokens(pre))
    macros = const_macros(dm)
    probe = ['#include <stdio.h>', '#include <stddef.h>', '#include <deko3d.h>', 'int main(void){']
    for e, names in sorted(enums.items()):
        for nm in names:
            probe.append('printf("%s\\t%s\\t%%lld\\n", (long long)%s);' % (e, nm, nm))
    for m in macros:
        probe.append('printf("macro\\t%s\\t%%llu\\n", (unsigned long long)(%s));' % (m, m))
    for s in structs:
        probe.append('printf("sizeof\\t%s\\t%%zu\\n", sizeof(%s));' % (s, s))
        probe.append('printf("alignof\\t%s\\t%%zu\\n", _Alignof(%s));' % (s, s))
    probe.append('return 0;}')
    with tempfile.TemporaryDirectory() as d:
        c, exe = os.path.join(d, 'probe.c'), os.path.join(d, 'probe')
        open(c, 'w').write('\n'.join(probe))
        run([cc, '-std=gnu11', '-Wall', '-Werror', '-Wno-pragma-once-outside-header', '-I', inc, c, '-o', exe])
        rows = [l.split('\t') for l in run([exe]).splitlines()]
    return rows


def cross_check(cross, inc, rows):
    lines = ['#include <stddef.h>', '#include <deko3d.h>']
    for k, nm, v in rows:
        if k in ('sizeof',):
            lines.append('_Static_assert(sizeof(%s) == %s, "%s");' % (nm, v, nm))
        elif k == 'alignof':
            lines.append('_Static_assert(_Alignof(%s) == %s, "%s");' % (nm, v, nm))
        elif k == 'macro':
            lines.append('_Static_assert((unsigned long long)(%s) == %sULL, "%s");' % (nm, v, nm))
        elif k.startswith('Dk'):
            lines.append('_Static_assert((long long)%s == %sLL, "%s");' % (nm, v, nm))
    with tempfile.TemporaryDirectory() as d:
        c = os.path.join(d, 'x.c')
        open(c, 'w').write('\n'.join(lines))
        run([cross, '-std=gnu11', '-fsyntax-only', '-Wno-pragma-once-outside-header', '-I', inc, c])


TRAITS_PROBE = r'''
#include <stdio.h>
#include <stdint.h>
struct N { const char* s; constexpr N(int) : s(0) {} constexpr N(const char* x) : s(x) {} };
struct T { const char *fmt, *c[4], *sw[4]; };
struct Row { unsigned flags, r, g, b, a, z, s, bpb, bw, bh; N cck, dck, rfmt, e2d; T tic; };
#define _C(x) N(#x)
#define _D(x) N(#x)
#define _CS(x) N(#x)
#define _DS(x) N(#x)
#define _(fmt,r,g,b,a,x,y,z,w) T{#fmt,{#r,#g,#b,#a},{#x,#y,#z,#w}}
static const Row rows[] = {
#include "format_traits.inc"
};
int main() {
  for (unsigned i = 0; i < sizeof rows / sizeof rows[0]; i++) {
    const Row& r = rows[i];
    printf("%u\t%u,%u,%u,%u,%u,%u\t%u\t%ux%u\t%u\t%s\t%s,%s,%s,%s\t%s,%s,%s,%s\t%s\t%s\n", i + 1, r.r, r.g, r.b, r.a, r.z, r.s,
           r.bpb, r.bw, r.bh, r.flags, r.tic.fmt, r.tic.c[0], r.tic.c[1], r.tic.c[2], r.tic.c[3],
           r.tic.sw[0], r.tic.sw[1], r.tic.sw[2], r.tic.sw[3], r.rfmt.s ? r.rfmt.s : "-", r.dck.s ? r.dck.s : "-");
  }
}
'''


def layout_facts(cc, src, fmt_names):
    cxx = cc.replace('gcc', 'g++') if 'gcc' in cc else 'g++'
    with tempfile.TemporaryDirectory() as d:
        c, exe = os.path.join(d, 'tr.cpp'), os.path.join(d, 'tr')
        open(c, 'w').write(TRAITS_PROBE)
        run([cxx, '-std=c++17', '-I', os.path.join(src, 'source/maxwell'), c, '-o', exe])
        out = [l.split('\t') for l in run([exe]).splitlines()]
    if len(out) != len(fmt_names) - 1:
        sys.exit('dk_facts: traits rows %d != DkImageFormat enumerators %d' % (len(out), len(fmt_names) - 1))
    rows = []
    for (idx, bits, bpb, blk, flags, tic, comp, swz, rfmt, dck), nm in zip(out, fmt_names[1:]):
        flags = int(flags)
        rows.append(['layout', nm, 'bits_rgbazs=%s;bytes_per_block=%s;block=%s;tic=%s;tic_comp=%s;tic_swz=%s;'
                     'render=%d;load_store=%d;engine2d=%d;srgb=%d;rawint=%d;render_fmt=%s;depth_kind=%s'
                     % (bits, bpb, blk, tic, comp, swz, (flags >> 8) & 1, (flags >> 9) & 1, (flags >> 10) & 1,
                        (flags >> 1) & 1, flags & 1, rfmt, dck)])
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--inc', required=True)
    ap.add_argument('--sha', required=True)
    ap.add_argument('--src')
    ap.add_argument('--cc', default='gcc')
    ap.add_argument('--cross')
    ap.add_argument('--out')
    ap.add_argument('--check', help='fail unless the freshly extracted facts equal this TSV')
    a = ap.parse_args()
    rows = build_facts(a.cc, a.inc)
    if a.cross:
        cross_check(a.cross, a.inc, rows)
    if a.src:
        names = [r[1] for r in rows if r[0] == 'DkImageFormat' and not r[1].endswith('_Count')]
        rows += layout_facts(a.cc, a.src, names)
    text = 'kind\tname\tvalue\nsha\tdeko3d\t%s\n' % a.sha + ''.join('\t'.join(r) + '\n' for r in rows)
    if a.out:
        open(a.out, 'w').write(text)
    if a.check and open(a.check).read() != text:
        sys.exit('dk_facts: facts differ from %s' % a.check)
    kinds = {}
    for r in rows:
        kinds[r[0]] = kinds.get(r[0], 0) + 1
    print(' '.join('%s=%d' % kv for kv in sorted(kinds.items())))


if __name__ == '__main__':
    main()
