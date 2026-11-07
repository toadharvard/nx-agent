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


if __name__ == '__main__':
    unittest.main()
