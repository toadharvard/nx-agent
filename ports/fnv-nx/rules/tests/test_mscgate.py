#!/usr/bin/env python3
"""test_mscgate.py - the dual-compile check (mscgate.py): permitted forms accepted, every other introduced `_MSC_VER`
conditional refused, conditionals of the input passed through, the ASM rule's output accepted, a rule that emits a
forbidden split named by prepare.prepare. Synthetic snippets only.

    python3 -I rules/tests/test_mscgate.py
"""
import os
import sys
import tempfile
import atexit  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
tempfile.tempdir = tempfile.mkdtemp(prefix="scratch-")  # every scratch dir of this test lives here and is removed at exit
atexit.register(shutil.rmtree, tempfile.tempdir, True)
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import support  # noqa: E402
import core  # noqa: E402
import mscgate  # noqa: E402
import prepare  # noqa: E402
import s8_asm  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402


def judged(before, after):
    return '\n'.join(mscgate.judge(before, after)) or 'clean'


ASM_FORM = '#ifdef _MSC_VER\n__asm nop\n#else\n__asm__("");\n#endif\n'
SRC = 'void f() {\n'

# ---- positive
case('asm form with #ifdef accepted', got=judged(SRC, SRC + ASM_FORM + '}\n'), contains=['clean'])
case('asm form with defined(_MSC_VER) accepted', got=judged(SRC, SRC + ASM_FORM.replace('ifdef _MSC_VER', 'if defined(_MSC_VER)') + '}\n'), contains=['clean'])
case('asm form with defined _MSC_VER accepted', got=judged(SRC, SRC + ASM_FORM.replace('ifdef _MSC_VER', 'if defined _MSC_VER') + '}\n'), contains=['clean'])
case('swapped branches with #ifndef accepted', got=judged(SRC, SRC + '#ifndef _MSC_VER\n__asm__("");\n#else\n__asm nop\n#endif\n}\n'), contains=['clean'])
case('swapped branches with !defined accepted', got=judged(SRC, SRC + '#if !defined(_MSC_VER)\n__asm__("");\n#else\n__asm nop\n#endif\n}\n'), contains=['clean'])
case('lifted call form accepted', got=judged(SRC, SRC + '#ifdef _MSC_VER\nbody();\n#else\nfnvnx_lifted(0x10);\n#endif\n}\n'), contains=['clean'])
case('asm in a #define replacement list accepted',
      got=judged('', '#ifdef _MSC_VER\n#define X __asm nop\n#else\n#define X __asm__("");\n#endif\n'), contains=['clean'])
case('asm form in a comment-laden condition line accepted',
      got=judged('', '#ifdef _MSC_VER /* a rule */\n__asm nop\n#else // gcc\n__asm__ ( "" ) ;\n#endif\n'), contains=['clean'])

# ---- decomp's own conditionals pass through unchanged, whatever their form
_own = '#if _MSC_VER >= 1500\nint a;\n#elif defined(_MSC_VER)\nint b;\n#endif\n#ifdef _MSC_VER\nint c;\n#endif\n'
case('input conditionals of any form are not judged', got=judged(_own, _own.replace('int a;', 'int a2; /* edited */')), contains=['clean'])
case('input groups with a rule group of the same header added: only the permitted form may exceed the input',
      got=judged(_own, _own + ASM_FORM), contains=['clean'])
case('a group of the input\'s header added beyond the input\'s count, without else: refused',
      got=judged(_own, _own + '#ifdef _MSC_VER\nint d;\n#endif\n'), contains=['not of the permitted form (1 in the input)'])

# ---- negative
case('no else branch (GCC sees nothing): refused', got=judged(SRC, SRC + '#ifdef _MSC_VER\nint x;\n#endif\n}\n'), contains=['#ifdef _MSC_VER', 'not of the permitted form'])
case('else branch without the asm / lifted form: refused', got=judged(SRC, SRC + '#ifdef _MSC_VER\nint f(int);\n#else\nint f(int, int);\n#endif\n}\n'),
      contains=['not of the permitted form'])
case('#ifndef with only a GCC branch: refused', got=judged(SRC, SRC + '#ifndef _MSC_VER\nint g();\n#endif\n}\n'), contains=['not of the permitted form'])
case('#if !_MSC_VER style: refused', got=judged(SRC, SRC + '#if !_MSC_VER\nint g();\n#else\n__asm__("");\n#endif\n}\n'), contains=['not of the permitted form'])
case('version comparison: refused', got=judged(SRC, SRC + '#if _MSC_VER >= 1500\n__asm nop\n#else\n__asm__("");\n#endif\n}\n'), contains=['not of the permitted form'])
case('#elif inside the group: refused', got=judged(SRC, SRC + '#ifdef _MSC_VER\nx\n#elif 1\n__asm__("");\n#else\ny\n#endif\n}\n'), contains=['not of the permitted form'])
case('asm marker only in the cl branch: refused', got=judged(SRC, SRC + '#ifdef _MSC_VER\n__asm__("");\n#else\nint g();\n#endif\n}\n'), contains=['not of the permitted form'])
case('non-empty asm string: refused', got=judged(SRC, SRC + '#ifdef _MSC_VER\nx\n#else\n__asm__("nop");\n#endif\n}\n'), contains=['not of the permitted form'])
case('asm marker inside a comment only: refused', got=judged(SRC, SRC + '#ifdef _MSC_VER\nx\n#else\n/* __asm__("") */ int y;\n#endif\n}\n'),
      contains=['not of the permitted form'])
case('lifted name without a call: refused', got=judged(SRC, SRC + '#ifdef _MSC_VER\nx\n#else\nint fnvnx_lifted;\n#endif\n}\n'), contains=['not of the permitted form'])
case('group closed by no #endif: refused', got=judged(SRC, SRC + '#ifdef _MSC_VER\nx\n#else\n__asm__("");\n'), contains=['not of the permitted form'])
case('a conditional on another macro is not judged', got=judged(SRC, SRC + '#ifdef OTHER\nint z;\n#endif\n}\n'), contains=['clean'])
case('_MSC_VER in a string or comment is no condition',
      got=judged(SRC, SRC + '#ifdef OTHER\nconst char* s = "#ifdef _MSC_VER";\n#endif // _MSC_VER\n}\n'), contains=['clean'])

# ---- per-rule check names the rule; tree check and census
bad = mscgate.check_rule_edit('r_example', SRC, SRC + '#ifdef _MSC_VER\nint x;\n#endif\n}\n')
case('check_rule_edit names the rule', got='\n'.join(bad), contains=['rule r_example'])
case('check_rule_edit: an edit that adds nothing is clean', got=str(mscgate.check_rule_edit('r', SRC, SRC + 'int q;\n')), contains=['[]'])
tree = {'a.cpp': (SRC, SRC + '#ifdef _MSC_VER\nint x;\n#endif\n}\n'), 'b.cpp': (SRC, SRC + ASM_FORM + '}\n')}
case('check_tree lists the file', got='\n'.join(mscgate.check_tree(tree)), contains=['a.cpp'], lacks=['b.cpp'])
c = mscgate.census({'a.cpp': (_own, _own + ASM_FORM), 'b.cpp': (SRC, SRC + '#ifdef _MSC_VER\nx\n#else\nfnvnx_lifted(1);\n#endif\n}\n')})
case('census by origin', got=str(sorted(c.items())), contains=["('asm', 1)", "('decomp', 2)", "('lifted', 1)", "('input', 2)"])

# ---- the ASM rule's own output is always of the permitted form
for name, src in (('block', 'int f(int x)\n{\n    __asm {\n        mov eax, x\n    }\n    return x;\n}\n'),
                  ('stmt-run', 'void f()\n{\n    __asm mov eax, 1\n    __asm mov ebx, 2\n    g();\n}\n'),
                  ('define', '#define RDT(t) __asm { \\\n__asm rdtsc \\\n}\nvoid f(unsigned& v) { RDT(v); }\n'),
                  ('header-inline', '#pragma once\n__forceinline int F(float v)\n{\n    int i;\n    __asm { fld v\n fistp i }\n    return i;\n}\n')):
    new = s8_asm.r_inline_asm('x.cpp', src, Counter())
    case('ASM rule output "%s" accepted by the gate', got=judged(src, new), contains=['clean'], key=name)
    case('ASM rule output "%s" has only asm-form groups', got=str(sorted(mscgate.census({'x': (src, new)}).items())),
          contains=["('asm', "], lacks=["'other'", "'decomp'"], key=name)

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
case('prepare.prepare: ASM rule only, no violation', got=str(found) + str(dict(stats)), contains=["[]", "ASM-spans"])
case('prepare.prepare: the output holds the asm form', got=open(os.path.join(out_root, 'src', 'a.cpp')).read(), contains=['#ifdef _MSC_VER', '__asm__("");'])
orig = prepare.rule_list
prepare.rule_list = lambda: orig() + [bad_rule]
_s, _f, _t, found = prepare.prepare(src_root, os.path.join(work, 'out2'), '', '')
prepare.rule_list = orig
case('prepare.prepare: a rule emitting a cl-only group is named', got='\n'.join(found), contains=['bad_rule', 'a.cpp'])
sys.exit(testcase.finish())
