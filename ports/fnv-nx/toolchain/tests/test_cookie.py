#!/usr/bin/env python3
"""Test of the .fnvnx.cookie section of toolchain/names/fnvnx_names.cc (S29): one row per array new-expression and per array delete-expression, from the
front end's own values (msvcmodel.patch: PLUGIN_FNVNX_COOKIE called by build_new_1 and build_vec_delete_1).

Row: <new|delete> TAB <file:line> TAB <elt size> TAB <cookie bytes> TAB <count offset> TAB <size arg> TAB <size arg is the elt size> TAB <dtor>.
The rule checked on every row (DATAMODEL S29): the cookie is 4 bytes exactly when the element class has a non-trivial destructor, whatever its alignment,
and none otherwise; an array new stores the count at cookie-4 (the last size_t of the cookie); an array delete passes the size of one element to
operator delete[]. cookie/cookie.cpp holds a class with a non-trivial destructor, a trivial one, an over-aligned one and a delete[] through a
base pointer; cookie/none.cpp has no array expression (the section is present and empty).
Compiled by the recipe's guest compiler with the sdd and names plugins. Run: python3 -I <this file> from the checkout root."""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plugbuild as pb  # noqa: E402

check = pb.Checks('test_cookie')


def rows_of(obj):
    return [l.split('\t') for l in pb.section(obj, '.fnvnx.cookie').splitlines() if l]


def rule(r):
    """the reasons a row breaks S29's formula ([] = it holds)"""
    kind, _, esz, cookie, cnt, sarg, same, dtor = r
    w = []
    if cookie != ('4' if dtor == '1' else '-'):
        w.append('cookie %s with dtor=%s' % (cookie, dtor))
    if kind == 'new' and cnt != ('0' if cookie == '4' else '-'):
        w.append('count offset %s with cookie %s' % (cnt, cookie))
    if kind == 'delete' and not (same == '1' and sarg == esz):
        w.append('size arg %s is not the element size %s' % (sarg, esz))
    return w


def main():
    td = tempfile.mkdtemp(prefix='test-cookie-')
    try:
        so = [pb.build(n, td) for n in ('sdd', 'names')]
        obj = os.path.join(td, 'c.o')
        r = pb.compile(os.path.join(HERE, 'cookie', 'cookie.cpp'), obj, so, flags=pb.PRODUCT_FLAGS + ['-O0'])
        check('compiles', r.returncode == 0, r.stderr[-3000:])
        if r.returncode == 0:
            rows = rows_of(obj)
            check('8 rows: 4 array new, 4 array delete', sorted(x[0] for x in rows) == ['delete'] * 4 + ['new'] * 4, rows)
            check('every row satisfies the S29 formula', not [x for x in rows if rule(x)], [(x, rule(x)) for x in rows if rule(x)])
            check('the dtor classes have a 4-byte cookie, the trivial one none, the over-aligned one too',
                  sorted((x[0], x[2], x[3]) for x in rows if x[7] == '1') == sorted([(k, s, '4') for k in ('new', 'delete') for s in ('4', '16', '8')])
                  and all(x[3] == '-' for x in rows if x[7] == '0'), rows)
        o2 = os.path.join(td, 'n.o')
        r = pb.compile(os.path.join(HERE, 'cookie', 'none.cpp'), o2, so, flags=pb.PRODUCT_FLAGS + ['-O0'])
        check('a unit with no array new/delete compiles', r.returncode == 0, r.stderr[-2000:])
        if r.returncode == 0:
            check('its .fnvnx.cookie is present and empty', pb.section_size(o2, '.fnvnx.cookie') == 0, pb.section_size(o2, '.fnvnx.cookie'))
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
