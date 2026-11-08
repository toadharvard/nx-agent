#!/usr/bin/env python3
"""tests/s2d - W37: two more ways a declared-only member gets its body from the tree (tools/link/bodies.py), on
hand-made trees (no decomp content).

    python3 -I tests/s2d/test_w37.py        # needs c++filt and aarch64-linux-gnu-g++

1. copy_key / tree-inline across the decomp's copy namespaces of `std` (COPY_NS_STD): a file declares its traits class
   in a namespace of its own with no bodies (`ac8parse::char_traits<T>`) and defines the same class in `std`; the
   host's emitted `std::char_traits<char>::_Copy_s` / `assign` become the wanted `ac8parse::` names when the layout
   gate gives both classes one size, and not when it does not or when the namespace is not listed.
2. member_inst_line / member_inst_hosts: a library member a unit declares as an explicit specialization
   (`template <> R C<A>::m(P);`, never defined there) is instantiated explicitly in a unit that includes the body
   (here from a shim directory) and declares no such specialization; the plain name comes out (GCC alone leaves only
   a local `.isra` clone of an inlined member).
"""
import collections
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'link'))
import bodies    # noqa: E402
import fnvlink   # noqa: E402
import layout    # noqa: E402
import sigs      # noqa: E402

R = collections.namedtuple('R', 'name units')


def itanium(n):
    d = subprocess.run(['c++filt', n], capture_output=True, text=True).stdout.strip()
    return sigs.parse_itanium(n, d)


class Gate:
    def __init__(self, sizes):
        self.sizes_ = sizes

    def same(self, ref_units, ref_types, def_unit, def_types):
        for u in ref_units:
            for rt, dt in zip(ref_types, def_types):
                a, b = self.sizes_.get((u, rt)), self.sizes_.get((def_unit, dt))
                if a is None or b is None or a != b:
                    return False, 'no'
        return True, 'sizes'


def write(root, files):
    for p, text in files.items():
        os.makedirs(os.path.dirname(os.path.join(root, p)), exist_ok=True)
        with open(os.path.join(root, p), 'w') as f:
            f.write(text)


@unittest.skipUnless(shutil.which('c++filt') and shutil.which('aarch64-linux-gnu-g++'), 'needs c++filt and the cross compiler')
class CopyNamespace(unittest.TestCase):
    COPY_S = '_ZN8ac8parse11char_traitsIcE7_Copy_sEPcjPKcj'
    ASSIGN = '_ZN8ac8parse11char_traitsIcE6assignERcRKc'
    SRC = ('namespace std {\n'
           'template <class E> struct char_traits {\n'
           '    static void assign(E& d, const E& v) { d = v; }\n'
           '    static E* _Copy_s(E* d, unsigned n, const E* s, unsigned c) { __builtin_memcpy(d, s, c); return d; }\n'
           '};\n}\n'
           'namespace ac8parse {\n'
           'template <class T> struct char_traits {\n'
           '    static T* _Copy_s(T*, unsigned, const T*, unsigned);\n'
           '    static void assign(T&, const T&);\n'
           '};\n}\n'
           'void use(char* d) { std::char_traits<char>::_Copy_s(d, 4, "ab", 2); std::char_traits<char>::assign(d[0], 1); }\n'
           'void use2(char* d) { ac8parse::char_traits<char>::_Copy_s(d, 4, "ab", 2); ac8parse::char_traits<char>::assign(d[0], 1); }\n')

    def test_keys(self):
        std_copy = '_ZNSt11char_traitsIcE7_Copy_sEPcjPKcj'
        self.assertEqual(bodies.copy_key(itanium(self.COPY_S)), bodies.copy_key(itanium(std_copy)))
        self.assertEqual(bodies.copy_key(itanium(self.ASSIGN)), bodies.copy_key(itanium('_ZNSt11char_traitsIcE6assignERcRKc')))
        # another namespace, member or overload is another key
        self.assertNotEqual(bodies.copy_key(itanium(self.COPY_S)),
                            bodies.copy_key(itanium('_ZN5other11char_traitsIcE7_Copy_sEPcjPKcj')))
        self.assertNotEqual(bodies.copy_key(itanium(self.COPY_S)),
                            bodies.copy_key(itanium('_ZNSt11char_traitsIcE7_Copy_sEPcjPKcm')))
        # the namespace inside a template argument and a parameter type
        a = '_ZN8ac8parse12basic_stringIcNS_11char_traitsIcEEE6appendERKS3_'
        b = '_ZNSt12basic_stringIcSt11char_traitsIcEE6appendERKS2_'
        self.assertEqual(bodies.copy_key(itanium(a))[0][0], 'std')
        self.assertEqual(bodies.copy_key(itanium(a))[0][1], bodies.copy_key(itanium(b))[0][1])
        self.assertEqual(bodies.copy_ns('ac8parse'), 'std')
        self.assertEqual(bodies.copy_ns('xac8parse::T'), 'xac8parse::T')
        self.assertEqual(bodies.copy_ns('A<ac8parse::T, fnv::ac8parse::U>'), 'A<std::T, fnv::ac8parse::U>')

    def synth(self, gate, namespaces=None):
        with tempfile.TemporaryDirectory() as d:
            write(d, {'src/t.cpp': self.SRC})
            old = bodies.COPY_NS_STD, bodies.COPY_NS_ANY
            if namespaces is not None:
                bodies.COPY_NS_STD = frozenset(namespaces)
                bodies.COPY_NS_ANY = bodies.re.compile(r'(?<![\w:])(?:%s)::' % '|'.join(sorted(namespaces)))
            try:
                dem = {}

                def it(n):
                    if n not in dem:
                        dem.update(fnvlink.batch_demangle(fnvlink.CXXFILT, [n]))
                    return sigs.parse_itanium(n, dem[n])
                rows = [bodies.Row(n, 'missing', None, None, 'src/t.cpp', '', None) for n in (self.COPY_S, self.ASSIGN)]
                return bodies.synth_host(d, d, 'src/t.cpp', rows, it, True, gate=gate)[1:]
            finally:
                bodies.COPY_NS_STD, bodies.COPY_NS_ANY = old

    def test_tree_inline(self):
        ok = Gate({('src/t.cpp', 'ac8parse::char_traits<char>'): 1, ('src/t.cpp', 'std::char_traits<char>'): 1})
        roots, failed = self.synth(ok)
        self.assertEqual((roots, failed), ({self.COPY_S, self.ASSIGN}, {}))
        no = Gate({('src/t.cpp', 'ac8parse::char_traits<char>'): 1, ('src/t.cpp', 'std::char_traits<char>'): 4})
        roots, failed = self.synth(no)
        self.assertEqual(roots, set())
        self.assertEqual(set(failed), {self.COPY_S, self.ASSIGN})
        # without the equivalence the host emits no such name
        roots, failed = self.synth(ok, namespaces=['nothing'])
        self.assertEqual(roots, set())


@unittest.skipUnless(shutil.which('c++filt') and shutil.which('aarch64-linux-gnu-g++'), 'needs c++filt and the cross compiler')
class MemberInst(unittest.TestCase):
    PUT = '_ZN3BoxIiE3putERKS0_j'
    GET = '_ZNK3BoxIiE3getEPKc'
    FILES = {
        'shim/include/box.h': 'template <class T> struct Box {\n  T v;\n'
                              '  Box& put(const Box& o, unsigned a) { v = o.v + a; return *this; }\n'
                              '  int get(const char* p) const { return p[0] + v; }\n};\n',
        'src/ref.cpp': '#include "box.h"\ntemplate <> Box<int>& Box<int>::put(const Box<int>&, unsigned);\n'
                       'template <> int Box<int>::get(const char*) const;\n'
                       'int f(Box<int>& a, const Box<int>& b) { a.put(b, 1); return a.get("x"); }\n',
        'src/host.cpp': '#include "box.h"\nint host;\n',
        'src/host2.cpp': '#include "box.h"\ntemplate <> Box<int>& Box<int>::put(const Box<int>&, unsigned);\nint host2;\n',
        'src/nohdr.cpp': 'int nohdr;\n',
    }

    def test_line(self):
        self.assertEqual(
            bodies.member_inst_line(itanium(self.PUT)),
            'template decltype(fnvnx_declval_<Box<int>&>().put(fnvnx_declval_<Box<int> const&>(), fnvnx_declval_<unsigned int>())) '
            'Box<int>::put(Box<int> const&, unsigned int);')
        self.assertIn('fnvnx_declval_<const Box<int>&>().get(fnvnx_declval_<char const*>())) Box<int>::get(char const*) const;',
                      bodies.member_inst_line(itanium(self.GET)))
        for n in ('_ZN3BoxIiEC1Ev',            # a constructor
                  '_ZN3BoxIiED1Ev',            # a destructor
                  '_ZN3Lit3getEv',             # not a template
                  '_ZN12fnv_009dfa003BoxIiE3putEv',   # a unit-local copy
                  '_ZN3BoxIiEcvPvEv',          # a conversion operator
                  '_ZN3BoxIiEplEi'):           # an operator
            self.assertIsNone(bodies.member_inst_line(itanium(n)), n)

    def test_hosts_and_synth(self):
        with tempfile.TemporaryDirectory() as t:
            write(t, self.FILES)
            tree, shim = os.path.join(t), os.path.join(t, 'shim')
            os.makedirs(os.path.join(t, 'include'), exist_ok=True)
            units = {'src/ref.cpp', 'src/host.cpp', 'src/host2.cpp', 'src/nohdr.cpp'}
            # the unit includes "box.h", found in the shim directory only
            rows = [R(self.PUT, 'src/ref.cpp'), R(self.GET, 'src/ref.cpp')]
            got = bodies.member_inst_hosts(tree, rows, itanium, units, {r.name: ['src/ref.cpp'] for r in rows}, shim)
            # host: reaches the body, no referencing unit, no specialization of its own (host2 declares `put`)
            self.assertEqual(got[self.GET], ['src/host.cpp', 'src/host2.cpp'])
            self.assertEqual(got[self.PUT], ['src/host.cpp'])
            # no explicit specialization in the referencing unit: no evidence, no host
            self.assertEqual(bodies.member_inst_hosts(tree, rows, itanium, units, {r.name: ['src/host.cpp'] for r in rows},
                                                      shim), {})
            # the body is not reachable without the shim directory
            self.assertEqual(bodies.member_inst_hosts(tree, rows, itanium, units, {r.name: ['src/ref.cpp'] for r in rows}), {})
            # emit it
            dem = {}

            def it(n):
                if n not in dem:
                    dem.update(fnvlink.batch_demangle(fnvlink.CXXFILT, [n]))
                return sigs.parse_itanium(n, dem[n])
            brows = [bodies.Row(n, 'missing', None, None, 'src/ref.cpp', '', None) for n in (self.PUT, self.GET)]
            orig = layout.game_flags
            try:
                # the fixture's header lives in the shim directory: the host compile finds it through -I
                def gf(tree_, work_):
                    return list(orig(tree_, work_)) + ['-I' + os.path.join(shim, 'include')]
                layout.game_flags = gf
                gate = Gate({('src/ref.cpp', 'Box<int>'): 4, ('src/host.cpp', 'Box<int>'): 4})
                path, roots, failed = bodies.synth_host(tree, t, 'src/host.cpp', brows, it, True, gate=gate,
                                                        strict={self.PUT, self.GET}, minst={self.PUT, self.GET})
            finally:
                layout.game_flags = orig
            self.assertEqual((roots, failed), ({self.PUT, self.GET}, {}))
            nm = subprocess.run(['aarch64-linux-gnu-nm', path], capture_output=True, text=True).stdout
            self.assertIn(' W ' + self.PUT, nm)
            self.assertIn(' W ' + self.GET, nm)


if __name__ == '__main__':
    unittest.main()
