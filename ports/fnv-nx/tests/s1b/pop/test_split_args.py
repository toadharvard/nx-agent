#!/usr/bin/env python3
"""tests/s1b/pop/test_split_args.py - popgen.split_args on hand-written argument texts (no decomp content)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import popgen  # noqa: E402

FAILS = 0


def check(cond, what):
    global FAILS
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    FAILS += 0 if cond else 1


def parts(s):
    return [x.strip() for x in popgen.split_args(s)]


check(parts('a, b') == ['a', 'b'], 'plain split')
check(parts('p->x, 2') == ['p->x', '2'], '`->` is not a closing bracket')
check(parts('this->m->f(1, 2), q->r') == ['this->m->f(1, 2)', 'q->r'], 'chained `->` and nested parens')
check(parts('a >> 2, b << 1') == ['a >> 2', 'b << 1'], 'shifts')
check(parts('a <= b, c >= d') == ['a <= b', 'c >= d'], '<= and >=')
check(parts('a < b, c') == ['a < b', 'c'], 'a lone comparison is not a template bracket')
check(parts('std::pair<int, int> p, int n') == ['std::pair<int, int> p', 'int n'], 'template argument list')
check(parts('class std::basic_string<char,struct std::char_traits<char>,class std::allocator<char> > const &, unsigned int')
      == ['class std::basic_string<char,struct std::char_traits<char>,class std::allocator<char> > const &', 'unsigned int'],
      'nested MSVC template spelling')
check(parts('std::vector<std::vector<int>> v, x') == ['std::vector<std::vector<int>> v', 'x'], '`>>` closing two templates')
check(parts('"a, b", \'","\', c') == ['"a, b"', '\'","\'', 'c'], 'string and char literals')
check(parts('f(p->a, b), g<int>(c, d)') == ['f(p->a, b)', 'g<int>(c, d)'], '`->` inside a nested call, a template call')
check(parts('') == [], 'empty')
print('split_args: %d failed' % FAILS)
sys.exit(1 if FAILS else 0)
