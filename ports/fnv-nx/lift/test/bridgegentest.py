#!/usr/bin/env python3
"""Unit test of tools/bridgegen.py (the descriptors of DISPATCH1 -> bridges + exact-lookup table), no toolchain needed (the bridges' behaviour is test/disptest.py's).
    bridgegentest.py
Checks: the grammar (every accepted form, every class of rejected form), the cases the x86 conventions do not define are errors that list every bad descriptor, the output is a
pure function of the SET of descriptors, the table's lookup (rt/dispatch.c's formula, re-read from the generated text) finds every descriptor's own bridge, FNV-1a is the
published function, a hash collision is a build error, the empty set gives a table every lookup of which misses."""
import os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(LIFT, 'tools'))
import bridgegen
M = 0xffffffff
OK = ['stack:v:', 'this:i:i', 'this:q:i,q,f,d', 'fast:i:i,i', 'fast:i:i,q,i,i', 'stack:a8:i', 'stack:a12:i', 'stack:a16:', 'stack:a8hf:', 'stack:a8hd:', 'stack:a16hd:', 'stack:a12hf:i',
      'stack:m20:s4r', 'stack:m4:', 'this:m12:i,q', 'stack:f:s8hf,s16hd,s24hd,s32hd,s3g,s16g', 'stack:d:s100r', 'stack:a3:i', 'stack:a1:', 'this:v:i']
BAD = ['', 'stack', 'stack:v', 'stack:v:i:i', 'pascal:v:', 'stack:x:', 'stack:v:z', 'stack:a17:', 'stack:a0:', 'stack:aN:', 'stack:m0:', 'stack:v:s0g', 'stack:v:s17g', 'stack:v:s6hf',
       'stack:v:s20hf', 'stack:v:s40hd', 'stack:a12hd:', 'stack:v:s8', 'stack:v:s8x', 'stack:v:s-8g', 'this:v:', 'this:v:q', 'this:v:f,i', 'fast:m20:i,i', 'fast:a12:i', 'stack:v:i,',
       'stack:v:,i', 'stack: v:', 'STACK:v:']


def lookup(text, key):
    rows = re.findall(r'\{ 0x([0-9a-f]{8})u, "([^"]*)", \(void \(\*\)\(\)\)(\w+) \}', text)
    g = [int(x) for x in re.search(r'fnvnx_bridge_g\[\] = \{ ([^}]*) \}', text).group(1).split(',')]
    k1, k2, nb, tb = [int(x) for x in re.search(r'fnvnx_bridge_phf_params\[4\] = \{ ([^}]*) \}', text).group(1).split(',')]
    slots = re.findall(r'^    \{ (?:0x[0-9a-f]{8}u, "[^"]*", \(void \(\*\)\(\)\)\w+|0, 0, 0) \},$', text, re.M)
    i = (((key ^ g[((key * k1) & M) >> (32 - nb)]) * k2) & M) >> (32 - tb)
    m = re.match(r'    \{ 0x([0-9a-f]{8})u, "([^"]*)", \(void \(\*\)\(\)\)(\w+) \},', slots[i] + '')
    return (int(m.group(1), 16), m.group(2), m.group(3)) if m else None, len(slots), len(rows)


def main():
    bad = 0
    def check(name, ok):
        nonlocal bad
        print('%-72s %s' % (name, 'ok' if ok else 'FAIL')); bad += not ok
    check('FNV-1a is the published function (a -> e40c292c, foobar -> bf9cf968)', bridgegen.fnv1a('a') == 0xe40c292c and bridgegen.fnv1a('foobar') == 0xbf9cf968 and bridgegen.fnv1a('') == 2166136261)
    for d in OK:
        try: bridgegen.parse(d); ok = True
        except ValueError: ok = False
        check('accepts %r' % d, ok)
    for d in BAD:
        try: bridgegen.parse(d); ok = False
        except ValueError: ok = True
        check('rejects %r' % d, ok)
    # the x86 side of a few shapes: the conventions' consequences
    check('this: the object is not on the stack, the others are (4 words: q 2, f 1, d 2 minus nothing)', 'fnvnx_guest_invoke_regs(fp, a0, 0, 5,' in bridgegen.emit('this:q:i,q,f,d', 'b'))
    check('fast: ecx, edx take the first two `i`; a q in between is on the stack', 'fnvnx_guest_invoke_regs(fp, a0, a2, 2,' in bridgegen.emit('fast:i:i,q,i', 'b'))
    check('fast: i after two registers is on the stack', 'fnvnx_guest_invoke_regs(fp, a0, a1, 1,' in bridgegen.emit('fast:i:i,i,i', 'b'))
    check('result in memory: the hidden pointer is stack word 0', 'args[0] = (u32)(unsigned long)&res;' in bridgegen.emit('stack:m20:i', 'b'))
    check('a12 is in memory on x86 (not 1, 2, 4, 8 bytes), a8 is edx:eax', 'args[0] = (u32)(unsigned long)&res;' in bridgegen.emit('stack:a12:', 'b') and 'out.edx' in bridgegen.emit('stack:a8:', 'b'))
    check('x87 result: kind 2; q and an 8-byte aggregate: kind 1; others kind 0', all(('fnvnx_guest_invoke_regs(fp, 0, 0, 0, args, %d, &out)' % k) in bridgegen.emit(d, 'b') for d, k in (('stack:d:', 2), ('stack:f:', 2), ('stack:q:', 1), ('stack:a8:', 1), ('stack:a8hd:', 1), ('stack:i:', 0), ('stack:a4:', 0))))
    # pure function of the set
    good = sorted(set(OK))
    ok1, rows = bridgegen.generate(good)
    ok2, _ = bridgegen.generate(list(reversed(good)) + good[:3])
    check('output is a function of the SET of descriptors (order, duplicates)', ok1 == ok2)
    check('one bridge per descriptor (%d)' % len(good), len(rows) == len(good) and len({r[1] for r in rows}) == len(good) and ok1.count('static ') >= len(good))
    # the lookup
    allfound = True
    for d, name, key in rows:
        got, nslots, nrows = lookup(ok1, key)
        allfound &= got == (key, d, name) and nrows == len(good)
    check('the perfect hash finds every descriptor\'s own bridge (%d)' % len(rows), allfound)
    # errors: all listed at once, nothing generated
    try: bridgegen.generate(good + BAD[2:6]); ok = False
    except ValueError as e: ok = all(b in str(e) for b in BAD[2:6])
    check('bad descriptors: ValueError naming every one', ok)
    old = bridgegen.fnv1a
    bridgegen.fnv1a = lambda s: 7
    try: bridgegen.generate(['stack:v:', 'stack:i:']); ok = False
    except ValueError as e: ok = 'collides' in str(e)
    finally: bridgegen.fnv1a = old
    check('a hash collision is a build error', ok)
    t, r = bridgegen.generate([])
    check('the empty set: a table of 8 empty slots, no bridge', r == [] and t.count('{ 0, 0, 0 },') == 8)
    print('bridgegentest: %d failed' % bad); sys.exit(1 if bad else 0)


if __name__ == '__main__': main()
