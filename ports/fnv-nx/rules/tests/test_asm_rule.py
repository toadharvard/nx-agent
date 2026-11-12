#!/usr/bin/env python3
"""test_asm_rule.py - the ASM rule (s8_asm.py): synthetic forms, the grammar's corner cases, the totality properties T1-T3
on every case and on a generated family of constructs, errors for what cannot be wrapped. No compiler is used: the cl and GCC
views are computed by s8_asm.gcc_view and by the token streams of mscgate.

    python3 -I rules/tests/test_asm_rule.py
"""
import itertools
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import support  # noqa: E402
from support import check  # noqa: E402
import mscgate  # noqa: E402
import s8_asm as M  # noqa: E402


def has_asm(s):
    return bool(re.search(r'\b__asm\b', s))


CASES = {
    'block': 'int f(int x)\n{\n    __asm {\n        mov eax, x\n        inc eax\n    }\n    return x;\n}\n',
    'block-oneline': 'void f() { __asm { int 3 } g(); }\n',
    'block-nested-braces-comments': 'void f()\n{\n    __asm { mov eax, 1 ; } not a close\n        mov ebx, 2 // }\n    }\n    h();\n}\n',
    'stmt': 'void f()\n{\n    __asm mov eax, 1\n    g();\n}\n',
    'stmt-run': 'void f()\n{\n    __asm mov eax, 1\n    __asm mov ebx, 2\n    __asm push ecx\n    g();\n}\n',
    'stmt-several-per-line': 'void f()\n{\n    __asm mov eax, 1 __asm mov ebx, 2 __asm nop\n    g();\n}\n',
    'stmt-in-braces': 'void f() { if (x) { __asm nop } g(); }\n',
    'stmt-semicolon-comment': 'void f()\n{\n    __asm mov eax, 1 ; this } is a comment __asm\n    g();\n}\n',
    'quoted-token-ignored': 'const char* s = "__asm { x }"; // __asm nop\n/* __asm { */ int y;\n',
    'define-continuation': '#define RDT(t) __asm { \\\n__asm rdtsc \\\n__asm mov dword ptr[t],eax \\\n}\nvoid f(unsigned& v) { RDT(v); }\n',
    'define-stmt-run': '#define NOPS __asm nop \\\n__asm nop\nvoid f() { NOPS; }\n',
    'define-oneline': '#define BRK() __asm { int 3 }\nvoid f() { BRK(); }\n',
    'header-inline-fn': '#pragma once\n__forceinline int F(float v)\n{\n    int i;\n    __asm {\n        fld v\n        fistp i\n    }\n    return i;\n}\n',
    'block-brace-same-line-no-space': 'void f()\n{\n    unsigned t;\n    __asm{\n        rdtsc\n        mov dword ptr[t], eax\n    }\n    use(t);\n}\n',
    'stmt-newline-after-asm': 'void f()\n{\n    __asm\n    g();\n}\n',
    'crlf-free-eof': 'void f() { __asm nop }',
    'two-functions': 'void f() { __asm nop }\nvoid g()\n{\n    __asm {\n        ret\n    }\n}\n',
}

for name, src in CASES.items():
    new, cnt = M.rewrite(name, src)
    if name == 'quoted-token-ignored':
        check('%s: untouched' % name, 'same' if new == src and not cnt['spans'] else 'changed', ['same'])
        continue
    check('%s: at least one span' % name, str(cnt['spans']), [], ['0'])
    g = M.gcc_view(new)
    check('%s: no __asm visible to GCC' % name, 'visible' if has_asm(g) else 'none', ['none'])
    check('%s: the GCC view holds the empty asm statement' % name, g, ['__asm__("")'])
    check('%s: no gcc leftover' % name, str(M.gcc_leftovers(new)), ['[]'])
    check('%s: the cl view is the input (token stream)' % name,
          'same' if M.tokens(M.gcc_view(new, msc=True)) == M.tokens(src) else 'differs', ['same'])
    for m in re.finditer(r'#ifdef _MSC_VER\n(.*?)\n#else\n', new, re.S):
        check('%s: pristine branch verbatim' % name, 'yes' if m[1] in src else 'no', ['yes'])
    check('%s: totality T1-T3' % name, str(M.totality_violations(name, src, new)), ['[]'])
    check('%s: permitted form for the dual-compile gate' % name, '\n'.join(mscgate.judge(src, new)) or 'clean', ['clean'])

# a construct inside a conditional of the input: wrapped, the conditional kept, the gate accepts (the totality checker's views
# blank every other conditional's directive lines, so T2/T3 are not asserted for it)
_src = '#if SOMETHING\nvoid f() { __asm nop }\n#endif\n'
_new, _ = M.rewrite('cond', _src)
check('inside a decomp conditional: wrapped and accepted', _new + '\n' + ('\n'.join(mscgate.judge(_src, _new)) or 'clean'),
      ['#if SOMETHING', '#ifdef _MSC_VER', '__asm__("")', 'clean'])

# specific shapes
n, c = M.rewrite('t', CASES['stmt-run']); check('stmt-run is one construct', repr(dict(c)), ["'run': 1"], ["'block': 1"])
n, c = M.rewrite('t', CASES['stmt-several-per-line']); check('several per line: one run', repr(dict(c)), ["'run': 1"])
n, c = M.rewrite('t', CASES['block-nested-braces-comments'])
check('block ends at its own }', 'ok' if c['block'] == 1 and 'h();' in n.split('#else')[1].split('#endif')[1] else 'bad', ['ok'])
n, c = M.rewrite('t', CASES['define-continuation']); check('define: block in a #define', repr(dict(c)), ["'define': 1", "'block': 1"])
check('define emitted twice (cl and GCC branch)', str(n.count('#define RDT')), ['2'])
n, c = M.rewrite('t', CASES['stmt-semicolon-comment']); check('a ; comment keeps __asm inside it', n.split('#endif')[1], ['g();'])
n, _ = M.rewrite('t', CASES['block'])
check('#line after the span restores the numbering', n, ['#line 7'])
check('a block opens with the cl branch', n[:40], ['int f(int x)\n{\n#ifdef _MSC_VER\n'])

# hard errors: the rule refuses what it cannot wrap (and says where)
for name, bad in {'unclosed': 'void f() { __asm { mov eax, 1\n', 'directive-inside': 'void f()\n{\n __asm {\n#if 1\n nop\n#endif\n }\n}\n',
                  'asm-in-if': '#if __asm\n#endif\n'}.items():
    try:
        M.rewrite(name, bad)
        check('%s: error raised' % name, 'no error', ['error'])
    except M.ASMError as e:
        check('%s: error raised' % name, 'error ' + str(e), ['error', name])
st = Counter()
check('a file without asm is untouched', 'same' if M.r_inline_asm('a.h', 'int x;\n', st) == 'int x;\n' and not st else 'changed', ['same'])
st = Counter()
M.r_inline_asm('a.cpp', CASES['two-functions'], st)
check('rule counts', repr(sorted(st.items())), ["('ASM-spans', 2)"])

# totality over a generated family: every combination of a prefix, a construct form and a suffix keeps T1-T3
PRE = ['', 'int a;\n', '#define K 1\n', 'void p()\n{\n']
CONS = ['__asm nop\n', '__asm { nop }\n', '__asm {\n  mov eax, 1\n  ; } comment\n}\n', '__asm mov eax, 1 __asm mov ebx, 2\n',
        '__asm { mov eax, 1 } __asm { mov ebx, 2 }\n', '__asm { int 3 } // trailing\n']
SUF = ['', 'int b;\n', '}\n']
n_ok = n_all = 0
for p, c, s in itertools.product(PRE, CONS, SUF):
    src = p + ('    ' if p.endswith('{\n') else '') + c + s
    if p.endswith('{\n') and s != '}\n':
        continue
    n_all += 1
    try:
        new, cnt = M.rewrite('gen', src)
    except M.ASMError:
        continue
    if not M.totality_violations('gen', src, new) and not has_asm(M.gcc_view(new)) and not mscgate.judge(src, new):
        n_ok += 1
check('generated family: every accepted rewrite is total (%d of %d accepted)' % (n_ok, n_all), 'ok' if n_ok > 0 else 'none', ['ok'])
fails = 0
for p, c, s in itertools.product(PRE, CONS, SUF):
    src = p + ('    ' if p.endswith('{\n') else '') + c + s
    if p.endswith('{\n') and s != '}\n':
        continue
    try:
        new, cnt = M.rewrite('gen', src)
    except M.ASMError:
        continue
    if M.totality_violations('gen', src, new) or has_asm(M.gcc_view(new)) or mscgate.judge(src, new):
        fails += 1
check('generated family: no violation', str(fails), ['0'])
support.finish()
