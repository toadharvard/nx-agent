#!/usr/bin/env python3
"""Unit test of fnvlink.abi_manual_trap (D24): a `manual` ABI pair that differs in an aggregate or the result
pointer stays unbound (cause abi-manual); varargs-only manual pairs keep their binding.

    python3 tools/link/test_abimanual.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import abi      # noqa: E402
import abi2     # noqa: E402
import fnvlink  # noqa: E402
import layout   # noqa: E402

FAILS = []


def v(*classes):
    x = abi.Verdict()
    x.kind, x.classes = 'manual', list(classes)
    return x


def check(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)


check(fnvlink.abi_manual_trap(v('arity(thiscall-member)', 'sret-explicit')), 'sret-explicit: unbound')
check(fnvlink.abi_manual_trap(v('ret-aggregate')), 'ret-aggregate: unbound')
check(fnvlink.abi_manual_trap(v('aggregate-param', 'ref-passes-fewer')), 'aggregate-param: unbound')
check(not fnvlink.abi_manual_trap(v('arity(free-cdecl)', 'varargs')), 'varargs only: kept')

# option (a): what keeps a pair abi-manual is counted by reason
check(fnvlink.abi_manual_why(['no layout: AGG:A<int, class std::B>; ?:B']) == 'no layout (?,AGG)', 'why: missing layouts by kind')
check(fnvlink.abi_manual_why(['variadic: the named part needs a bridge that would move the anonymous arguments'])
      == 'variadic', 'why: variadic')
check(fnvlink.abi_manual_why([]) == 'unknown', 'why: none given')
check(fnvlink.abi_manual_why(['no layout: ?:NavMeshPtr result']) == 'no layout (? result)', 'why: untagged result')

# a source spelling of a function pointer with its parameter name and no blank is a pointer (it was untagged '?:')
check(abi.classify('bool(__cdecl* handler)( char *, ::NiFile::OpenMode)') == 'P', 'named function pointer: P')
check(abi.classify('void(__cdecl*)(struct Pair*, struct Pair*)') == 'P', 'function pointer, no blank: P')
check(abi.classify('int (A::* f)(int)') == 'MP', 'named member pointer: MP')
check(abi.classify('WPARAM') == '?:WPARAM', 'typedef stays untagged')

# abi2.lookup: a bare name takes the unit's unique (or identical) qualified layout, never another unit's
L = layout.Layout
lk = abi2.lookup({('u', 'ns::W'): L(12, 4, 1, 0, 'H3x4', 1), ('u', 'a::D'): L(4, 4, 1, 0, 'G1', 1),
                  ('u', 'b::D'): L(8, 4, 1, 0, 'G1', 1), ('u', 'x::E'): L(4, 4, 1, 0, 'G1', 1),
                  ('u', 'y::E'): L(4, 4, 1, 0, 'G1', 1), ('w', 'q::W'): L(4, 4, 1, 0, 'G1', 1),
                  ('u', 'm::n::T<a::b>'): L(4, 4, 1, 0, 'G1', 1)})
check(lk('u')('W') is not None and lk('u')('W').size == 12, 'lookup: unique qualified name')
check(lk('u')('D') is None, 'lookup: two different qualified layouts: unknown')
check(lk('u')('E') is not None, 'lookup: identical qualified layouts')
check(lk('v')('W') is None, 'lookup: other unit')
check(lk('u')('n::T<a::b>') is not None and lk('u')('T<a::b>') is not None, 'lookup: template argument kept')
check(abi2.probe_name('?:PacketMask::Bits') == 'PacketMask::Bits' and abi2.probe_name('?:int(*)[3]') is None,
      'probe_name: named types only')
print('%d failed' % len(FAILS))
sys.exit(1 if FAILS else 0)
