#!/usr/bin/env python3
"""tests/s2d - STAND5 (tools/rules/s2_bodies.py) on a hand-written unit (no decomp content).

    python3 -I tests/s2d/test_stand5.py

An in-class body whose MSVC name the unit's symbols bind to an address another unit defines becomes a
declaration, and so do a constructor without an initializer list, a `__declspec(noinline)` member of a class with a
base, and a `__declspec(noinline)` member of a class template whose instantiations are all bound elsewhere (R6
class 5); one bound to this unit's own address, a tagged one, a virtual one, a constructor with an initializer
list, a template member without noinline and an unbound one keep their bodies. A static member (a thrower
stand-in such as `_Xlen`) is converted like any other member.
"""
import collections
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'rules'))
import s2_bodies   # noqa: E402

SRC = '''struct Item { int v; };
struct List
{
    Item* m_head;
    Item* NewItem();
    void* Add(const Item& p)
    {
        Item* i = NewItem();
        return i;
    }
    int Own(int k)
    {
        return k;
    }
    // @fnv 0x00402000
    int Tagged(int k)
    {
        return k + 1;
    }
    virtual int Virt(int k)
    {
        return k;
    }
    List()
    {
        m_head = 0;
    }
    List(int k) : m_head(0)
    {
        m_head += k;
    }
    int Free(int k)
    {
        return k * 2;
    }
    static void Xlen()
    {
        throw 1;
    }
};
struct Tail : public List
{
    __declspec(noinline) void AddTail(const Item& p)
    {
        m_head = NewItem();
    }
};
template <class T> class SList
{
  public:
    __declspec(noinline) void Remove(const T& k)
    {
        m_next = 0;
    }
    int Count()
    {
        return 7;
    }
    SList* m_next;
};
void Use(List& l, Item& i) { l.Add(i); }
'''


class Stand5(unittest.TestCase):
    def test_rule(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, 'build'))
            os.makedirs(os.path.join(d, 'src'))
            units = {'units': [
                {'source': 'src/a.cpp', 'flags': ['/Od'], 'functions': [{'symbol': '?Use@@YAXAAUList@@AAUItem@@@Z',
                                                                         'address': '0x00401100'},
                                                                        {'symbol': '?Own@List@@QAEHH@Z',
                                                                         'address': '0x00401200'}],
                 'symbols': {'?Add@List@@QAEPAXABUItem@@@Z': '0x00405000', '?Own@List@@QAEHH@Z': '0x00401200',
                             '?Tagged@List@@QAEHH@Z': '0x00405100', '?Virt@List@@UAEHH@Z': '0x00405200',
                             '??0List@@QAE@XZ': '0x00405300', '??0List@@QAE@H@Z': '0x00405300',
                             '?Xlen@List@@SAXXZ': '0x00405200',
                             '?AddTail@Tail@@QAEXABUItem@@@Z': '0x00405000',
                             '?Remove@?$SList@H@@QAEXABH@Z': '0x00405100', '?Count@?$SList@H@@QAEHXZ': '0x00405100'}},
                {'source': 'src/b.cpp', 'flags': ['/O2'], 'functions': [
                    {'symbol': '?Add@Arr@@QAEPAXABUItem@@@Z', 'address': '0x00405000'},
                    {'symbol': '?F@@YAXXZ', 'address': '0x00405100'},
                    {'symbol': '?G@@YAXXZ', 'address': '0x00405200'},
                    {'symbol': '?H@@YAXXZ', 'address': '0x00405300'}], 'symbols': {}}]}
            json.dump(units, open(os.path.join(d, 'build', 'units.json'), 'w'))
            path = os.path.join(d, 'src', 'a.cpp')
            open(path, 'w').write(SRC)
            st = collections.Counter()
            out = s2_bodies.r_stand_in_class_bodies(path, SRC, st)
        self.assertEqual(st['STAND5'], 5)
        for decl in ('void* Add(const Item& p); /* fnv-nx STAND5', 'List(); /* fnv-nx STAND5',
                     '__declspec(noinline) void AddTail(const Item& p); /* fnv-nx STAND5',
                     '__declspec(noinline) void Remove(const T& k); /* fnv-nx STAND5',
                     'static void Xlen(); /* fnv-nx STAND5'):
            self.assertIn(decl, out)
        for gone in ('Item* i = NewItem();', 'm_head = 0;', 'm_head = NewItem();', 'm_next = 0;', 'throw 1;'):
            self.assertNotIn(gone, out)
        for keep in ('return k;', 'return k + 1;', 'm_head += k;', 'return k * 2;', 'return 7;'):
            self.assertIn(keep, out)
        self.assertEqual(out.count('\n'), SRC.count('\n'))   # line numbers stay


STL_SRC = '''namespace fnv_00a00100 {
namespace std {
template <class C> struct Buf
{
    void* file;
    Buf* open(const char*, int);
    Buf* close()
    {
        return open(0, 2);
    }
    int Size()
    {
        return 4;
    }
};
template <class C> struct Out
{
    Buf<C> buf;
    struct Sentry
    {
        Out& o;
        Sentry(Out& s) : o(s)
        {
            o.buf.open(0, 1);
        }
    };
    Out(const char* n) : buf()
    {
        buf.open(n, 2);
    }
};
}
}
namespace other {
template <class C> struct Buf2
{
    int Close()
    {
        return 1;
    }
};
}
'''


class Stand5Stl(unittest.TestCase):
    def test_unit_copies_of_the_stl(self):
        """In the decomp's unit copies of the MSVC STL (`namespace fnv_<a> { namespace std`), members of class
        templates, of classes nested in them and constructors with an initializer list become declarations when
        every symbol of the copy's MSVC name binds an address another unit defines; an unbound member keeps its
        body, and so does a template member outside such a copy."""
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, 'build'))
            os.makedirs(os.path.join(d, 'src'))
            units = {'units': [
                {'source': 'src/a.cpp', 'flags': ['/O2'], 'functions': [], 'symbols': {
                    '?close@?$Buf@D@std@fnv_00a00100@@QAEPAU123@XZ': '0x00406000',
                    '??0Sentry@?$Out@D@std@fnv_00a00100@@QAE@AAU123@@Z': '0x00406100',
                    '??0?$Out@D@std@fnv_00a00100@@QAE@PBD@Z': '0x00406200',
                    '?Close@?$Buf2@D@other@@QAEHXZ': '0x00406300'}},
                {'source': 'src/b.cpp', 'flags': ['/O2'], 'functions': [
                    {'symbol': '?F%d@@YAXXZ' % i, 'address': '0x0040%d' % (6000 + 100 * i)} for i in range(4)],
                 'symbols': {}}]}
            json.dump(units, open(os.path.join(d, 'build', 'units.json'), 'w'))
            path = os.path.join(d, 'src', 'a.cpp')
            open(path, 'w').write(STL_SRC)
            import s1_compile   # the rules cache units.json per process: read this test's
            s1_compile._UNIT_SYMBOLS = s1_compile._UNIT_INFO = s1_compile._ALL_FUNCTIONS = None
            s2_bodies._FUNCTION_NAMES.clear()
            st = collections.Counter()
            out = s2_bodies.r_stand_in_class_bodies(path, STL_SRC, st)
        self.assertEqual(st['STAND5'], 3)
        for decl in ('Buf* close(); /* fnv-nx STAND5', 'Sentry(Out& s); /* fnv-nx STAND5',
                     'Out(const char* n); /* fnv-nx STAND5'):
            self.assertIn(decl, out)
        for gone in ('return open(0, 2);', 'o.buf.open(0, 1);', 'buf.open(n, 2);'):
            self.assertNotIn(gone, out)
        for keep in ('return 4;', 'return 1;'):
            self.assertIn(keep, out)
        self.assertEqual(out.count('\n'), STL_SRC.count('\n'))


STR_SRC = '''namespace fnv_00a00200 {
namespace std {
template <class C, class Tr, class A> class basic_string
{
  public:
    void _Tidy(bool, unsigned int);
    basic_string& assign(const basic_string&, unsigned int, unsigned int);
    static const unsigned int npos;
    basic_string& operator=(const basic_string&);
    ~basic_string();
};
}
}
namespace fnv_00a00300 {
namespace std {
template <class C, class Tr, class A> class basic_string
{
  public:
    void _Tidy(bool built = false, unsigned n = 0)
    {
    }
    basic_string& assign(const basic_string&, unsigned, unsigned);
    basic_string& operator=(const basic_string&);
    ~basic_string()
    {
        _Tidy(true, 0);
    }
};
}
}
namespace fnv_00a00400 {
namespace std {
template <class C, class Tr, class A> class basic_string
{
  public:
    basic_string& operator=(const basic_string&);
    ~basic_string();
};
}
}
namespace other {
template <class C, class Tr, class A> class basic_string
{
  public:
    void _Tidy(bool, unsigned int);
    ~basic_string();
};
}
'''


class Stand5String(unittest.TestCase):
    def test_string_members_the_exe_inlined(self):
        """The unit copies of basic_string declare `~basic_string()` and `operator=` and nothing defines them: the exe
        inlined `_Tidy(true, 0)` and `assign(r, 0, npos)`. A copy gets each body only when it declares the helper (with
        `npos`, or `-1` where the model has none); a copy without the helper, or outside a unit namespace, keeps its
        declaration."""
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, 'build'))
            os.makedirs(os.path.join(d, 'src'))
            units = {'units': [{'source': 'src/a.cpp', 'flags': ['/O2'], 'functions': [], 'symbols': {'?x@@3HA': '0x1'}}]}
            json.dump(units, open(os.path.join(d, 'build', 'units.json'), 'w'))
            path = os.path.join(d, 'src', 'a.cpp')
            open(path, 'w').write(STR_SRC)
            import s1_compile
            s1_compile._UNIT_SYMBOLS = s1_compile._UNIT_INFO = s1_compile._ALL_FUNCTIONS = None
            s2_bodies._FUNCTION_NAMES.clear()
            st = collections.Counter()
            out = s2_bodies.r_stand_in_class_bodies(path, STR_SRC, st)
        self.assertEqual(st['STAND5'], 3)
        self.assertEqual(out.count('~basic_string() { _Tidy(true, 0); }'), 1)
        self.assertIn('basic_string& operator=(const basic_string& _Right) { return assign(_Right, 0, npos); }', out)
        self.assertIn('basic_string& operator=(const basic_string& _Right) { return assign(_Right, 0, -1); }', out)
        self.assertIn('    basic_string& operator=(const basic_string&);\n    ~basic_string();\n};\n}\n}\nnamespace other',
                      out)   # fnv_00a00400 (no helper) and `other` (no unit namespace): untouched
        self.assertEqual(out.count('\n'), STR_SRC.count('\n'))


if __name__ == '__main__':
    unittest.main()
