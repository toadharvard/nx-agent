#!/usr/bin/env python3
"""Test of the .fnvnx.cookie section of toolchain/names/fnvnx_names.cc (S29): one row per array new-expression and per array delete-expression, from the
front end's own values (msvcmodel.patch: PLUGIN_FNVNX_COOKIE called by build_new_1 and build_vec_delete_1).

Row: <new|delete> TAB <file:line> TAB <elt size> TAB <cookie bytes> TAB <count offset> TAB <size arg> TAB <size arg is the elt size> TAB <dtor>.
The rule checked on every row (DATAMODEL S29): the cookie is 4 bytes exactly when the element class has a non-trivial destructor, whatever its alignment,
and none otherwise; an array new stores the count at cookie-4 (the last size_t of the cookie); an array delete passes the size of one element to
operator delete[]. ldbl/model.cpp holds the cases the pinned cl 15.00 was recorded on (DATAMODEL S29's source). An over-aligned element (16 and up) has no recorded cl
output: that case is undecided and is not tested; cookie/none.cpp has no array expression (the section is present and empty).
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
        r = pb.compile(os.path.join(HERE, 'ldbl', 'model.cpp'), obj, so, flags=pb.PRODUCT_FLAGS + ['-O0'])
        check('compiles', r.returncode == 0, r.stderr[-3000:])
        if r.returncode == 0:
            rows = rows_of(obj)
            check('every row satisfies the S29 formula', not [x for x in rows if rule(x)], [(x, rule(x)) for x in rows if rule(x)])
            got = sorted((x[0], x[2], x[3], x[7], x[5] if x[0] == 'delete' else '-') for x in rows)
            # (kind, elt size, cookie, dtor, delete size arg): each row is the pinned cl's output recorded in ldbl/model.cpp (DATAMODEL S29's source), by its comment
            want = sorted([
                ('new', '4', '4', '1', '-'),     # n_d:   cl: operator new(3*4 + 4), array = block + 4
                ('new', '8', '4', '1', '-'),     # n_a8:  cl: operator new(3*8 + 4), array = block + 4 (the 8-aligned element: the cookie is not padded)
                ('new', '20', '-', '0', '-'),    # n_nm:  cl: NM::operator new[](60): no count
                ('new', '24', '-', '0', '-'),    # n_nd:  cl: 72: no count
                ('new', '28', '4', '1', '-'),    # n_nv:  cl: 3*28 + 4 = 88
                ('new', '12', '4', '1', '-'),    # n_n:   cl: 3*12 + 4 = 40
                ('new', '4', '-', '0', '-'),     # n_var: cl: operator new(4*n), no cookie
                ('delete', '20', '-', '0', '20'),  # d_nm: cl: heap_free(p, 20)
                ('delete', '24', '-', '0', '24'),  # d_nd: cl: heap_free(p, 24)
                ('delete', '28', '4', '1', '28'),  # d_nv: cl: heap_free(p - 4, 28)
                ('delete', '12', '4', '1', '12'),  # d_n:  cl: heap_free(p - 4, 12)
            ])
            check('the rows are the recorded cl cases of ldbl/model.cpp, one each', got == want, (got, want))
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
