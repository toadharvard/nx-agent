#!/usr/bin/env python3
"""test_mscgate.py - the dual-compile check (mscgate.py): permitted forms accepted, every other introduced `_MSC_VER`
conditional refused, conditionals of the input passed through, the ASM rule's output accepted, a rule that emits a
forbidden split named by prepare.prepare. Synthetic snippets only.

    python3 -I rules/tests/test_mscgate.py
"""
import os
import sys
import tempfile
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import support  # noqa: E402
from support import check  # noqa: E402
import core  # noqa: E402
import mscgate  # noqa: E402
import prepare  # noqa: E402
import s8_asm  # noqa: E402


def judged(before, after):
    return '\n'.join(mscgate.judge(before, after)) or 'clean'


ASM_FORM = '#ifdef _MSC_VER\n__asm nop\n#else\n__asm__("");\n#endif\n'
SRC = 'void f() {\n'

# ---- positive
check('asm form with #ifdef accepted', judged(SRC, SRC + ASM_FORM + '}\n'), ['clean'])
check('asm form with defined(_MSC_VER) accepted', judged(SRC, SRC + ASM_FORM.replace('ifdef _MSC_VER', 'if defined(_MSC_VER)') + '}\n'), ['clean'])
check('asm form with defined _MSC_VER accepted', judged(SRC, SRC + ASM_FORM.replace('ifdef _MSC_VER', 'if defined _MSC_VER') + '}\n'), ['clean'])
check('swapped branches with #ifndef accepted', judged(SRC, SRC + '#ifndef _MSC_VER\n__asm__("");\n#else\n__asm nop\n#endif\n}\n'), ['clean'])
check('swapped branches with !defined accepted', judged(SRC, SRC + '#if !defined(_MSC_VER)\n__asm__("");\n#else\n__asm nop\n#endif\n}\n'), ['clean'])
check('lifted call form accepted', judged(SRC, SRC + '#ifdef _MSC_VER\nbody();\n#else\nfnvnx_lifted(0x10);\n#endif\n}\n'), ['clean'])
check('asm in a #define replacement list accepted',
      judged('', '#ifdef _MSC_VER\n#define X __asm nop\n#else\n#define X __asm__("");\n#endif\n'), ['clean'])
check('asm form in a comment-laden condition line accepted',
      judged('', '#ifdef _MSC_VER /* a rule */\n__asm nop\n#else // gcc\n__asm__ ( "" ) ;\n#endif\n'), ['clean'])

# ---- decomp's own conditionals pass through unchanged, whatever their form
_own = '#if _MSC_VER >= 1500\nint a;\n#elif defined(_MSC_VER)\nint b;\n#endif\n#ifdef _MSC_VER\nint c;\n#endif\n'
check('input conditionals of any form are not judged', judged(_own, _own.replace('int a;', 'int a2; /* edited */')), ['clean'])
check('input groups with a rule group of the same header added: only the permitted form may exceed the input',
      judged(_own, _own + ASM_FORM), ['clean'])
check('a group of the input\'s header added beyond the input\'s count, without else: refused',
      judged(_own, _own + '#ifdef _MSC_VER\nint d;\n#endif\n'), ['not of the permitted form (1 in the input)'])

# ---- negative
check('no else branch (GCC sees nothing): refused', judged(SRC, SRC + '#ifdef _MSC_VER\nint x;\n#endif\n}\n'), ['#ifdef _MSC_VER', 'not of the permitted form'])
check('else branch without the asm / lifted form: refused', judged(SRC, SRC + '#ifdef _MSC_VER\nint f(int);\n#else\nint f(int, int);\n#endif\n}\n'),
      ['not of the permitted form'])
check('#ifndef with only a GCC branch: refused', judged(SRC, SRC + '#ifndef _MSC_VER\nint g();\n#endif\n}\n'), ['not of the permitted form'])
check('#if !_MSC_VER style: refused', judged(SRC, SRC + '#if !_MSC_VER\nint g();\n#else\n__asm__("");\n#endif\n}\n'), ['not of the permitted form'])
check('version comparison: refused', judged(SRC, SRC + '#if _MSC_VER >= 1500\n__asm nop\n#else\n__asm__("");\n#endif\n}\n'), ['not of the permitted form'])
check('#elif inside the group: refused', judged(SRC, SRC + '#ifdef _MSC_VER\nx\n#elif 1\n__asm__("");\n#else\ny\n#endif\n}\n'), ['not of the permitted form'])
check('asm marker only in the cl branch: refused', judged(SRC, SRC + '#ifdef _MSC_VER\n__asm__("");\n#else\nint g();\n#endif\n}\n'), ['not of the permitted form'])
check('non-empty asm string: refused', judged(SRC, SRC + '#ifdef _MSC_VER\nx\n#else\n__asm__("nop");\n#endif\n}\n'), ['not of the permitted form'])
check('asm marker inside a comment only: refused', judged(SRC, SRC + '#ifdef _MSC_VER\nx\n#else\n/* __asm__("") */ int y;\n#endif\n}\n'),
      ['not of the permitted form'])
check('lifted name without a call: refused', judged(SRC, SRC + '#ifdef _MSC_VER\nx\n#else\nint fnvnx_lifted;\n#endif\n}\n'), ['not of the permitted form'])
check('group closed by no #endif: refused', judged(SRC, SRC + '#ifdef _MSC_VER\nx\n#else\n__asm__("");\n'), ['not of the permitted form'])
check('a conditional on another macro is not judged', judged(SRC, SRC + '#ifdef OTHER\nint z;\n#endif\n}\n'), ['clean'])
check('_MSC_VER in a string or comment is no condition',
      judged(SRC, SRC + '#ifdef OTHER\nconst char* s = "#ifdef _MSC_VER";\n#endif // _MSC_VER\n}\n'), ['clean'])

# ---- per-rule check names the rule; tree check and census
bad = mscgate.check_rule_edit('r_example', SRC, SRC + '#ifdef _MSC_VER\nint x;\n#endif\n}\n')
check('check_rule_edit names the rule', '\n'.join(bad), ['rule r_example'])
check('check_rule_edit: an edit that adds nothing is clean', str(mscgate.check_rule_edit('r', SRC, SRC + 'int q;\n')), ['[]'])
tree = {'a.cpp': (SRC, SRC + '#ifdef _MSC_VER\nint x;\n#endif\n}\n'), 'b.cpp': (SRC, SRC + ASM_FORM + '}\n')}
check('check_tree lists the file', '\n'.join(mscgate.check_tree(tree)), ['a.cpp'], ['b.cpp'])
c = mscgate.census({'a.cpp': (_own, _own + ASM_FORM), 'b.cpp': (SRC, SRC + '#ifdef _MSC_VER\nx\n#else\nfnvnx_lifted(1);\n#endif\n}\n')})
check('census by origin', str(sorted(c.items())), ["('asm', 1)", "('decomp', 2)", "('lifted', 1)", "('input', 2)"])

# ---- the ASM rule's own output is always of the permitted form
for name, src in (('block', 'int f(int x)\n{\n    __asm {\n        mov eax, x\n    }\n    return x;\n}\n'),
                  ('stmt-run', 'void f()\n{\n    __asm mov eax, 1\n    __asm mov ebx, 2\n    g();\n}\n'),
                  ('define', '#define RDT(t) __asm { \\\n__asm rdtsc \\\n}\nvoid f(unsigned& v) { RDT(v); }\n'),
                  ('header-inline', '#pragma once\n__forceinline int F(float v)\n{\n    int i;\n    __asm { fld v\n fistp i }\n    return i;\n}\n')):
    new = s8_asm.r_inline_asm('x.cpp', src, Counter())
    check('ASM rule output "%s" accepted by the gate' % name, judged(src, new), ['clean'])
    check('ASM rule output "%s" has only asm-form groups' % name, str(sorted(mscgate.census({'x': (src, new)}).items())),
          ["('asm', "], ["'other'", "'decomp'"])

# ---- prepare.prepare: a rule that emits a forbidden split is named; the permitted ASM rule passes
def bad_rule(path, text, stats):
    return text + '#ifdef _MSC_VER\nint only_cl;\n#endif\n' if path.endswith('.cpp') else text


work = tempfile.mkdtemp()
src_root, out_root = os.path.join(work, 'dec'), os.path.join(work, 'out')
for d in ('src', 'include', 'build'):
    os.makedirs(os.path.join(src_root, d))
with open(os.path.join(src_root, 'src', 'a.cpp'), 'w') as f:
    f.write('void f()\n{\n    __asm nop\n}\n')
with open(os.path.join(src_root, 'build', 'units.json'), 'w') as f:
    f.write('{"units": []}')
core.configure(src_root, '', '')
stats, files, texts, found = prepare.prepare(src_root, out_root, '', '')
check('prepare.prepare: ASM rule only, no violation', str(found) + str(dict(stats)), ["[]", "ASM-spans"])
check('prepare.prepare: the output holds the asm form', open(os.path.join(out_root, 'src', 'a.cpp')).read(), ['#ifdef _MSC_VER', '__asm__("");'])
orig = prepare.rule_list
prepare.rule_list = lambda: orig() + [bad_rule]
_s, _f, _t, found = prepare.prepare(src_root, os.path.join(work, 'out2'), '', '')
prepare.rule_list = orig
check('prepare.prepare: a rule emitting a cl-only group is named', '\n'.join(found), ['bad_rule', 'a.cpp'])
support.finish()
