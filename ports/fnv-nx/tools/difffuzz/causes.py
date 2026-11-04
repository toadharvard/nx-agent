#!/usr/bin/env python3
"""causes.py - record-level cause refinement and the per-cause verdict policy (stream S13b-causes).

Everything here reads a saved divergence record (<campaign>/div/<addr>-<n>.json: `divergence`, `hint`, `conv`,
`symbol`, `case`) and nothing else: no replay, no emulator, no decomp. It runs downstream of the harness (fuzz.py,
classify.py, runner.py are not touched) and keeps their interface: a record's cause is still one string from the
same vocabulary, `report.reclassify` calls `refine` only where it would otherwise answer `unclassified`, `call-flow`
or `signedness?`.

    causes.py summary <campaign dir>... [--recheck-tag t] [--out causes.json]
        one row per cause: divergences, functions, functions whose worst cause it is, verdict class, owner, why
        (fnvnx-causes/1, read by tools/gates for R0); with --recheck-tag, replays that no longer reproduce are dropped
        exactly as report.py does
    causes.py explain <div.json>...
        the refined cause of each record and the rule that gave it

Verdict classes (POLICY):
  by-design  MSVC and the port differ only where C++ leaves it open or the input is invalid for the original game
             (operand order, NaN payloads, bool bytes other than 0/1, x87 extended intermediates, #DE on x86)
  artifact   the harness or its oracle model, not the port (fixed or being fixed in difffuzz itself)
  real-bug   the port behaves differently from the exe on a valid input; the owner is the stream that fixes it
  triage     not decided from the record alone; needs a replay (`difffuzz.py effects` / `triage --reads`)
"""
import argparse
import collections
import glob
import json
import math
import os
import struct
import sys

# address ranges of the shared world (world.py; duplicated so this module needs no emulator imports)
NULL_END = 0x10000
TEXT = (0x00400000, 0x00fdf000)
WINDOW = (0x00fdf000, 0x02100000)          # image data window (.rdata/.data/.bss of the exe)
BUFS = (0x03000000, 0x03040000)
HEAP = (0x03800000, 0x04000000)
STACK = (0x06e00000, 0x06f10000)
A64_STUBS = (0x08000000, 0x0a000000)       # __fnv_<X> code stubs and named stubs (brk #0)
A64_IMAGE = (0x0a000000, 0x0b800000)       # the native unit image (.text/.rodata/.data/.bss)
A64_PRIV = (0x08000000, 0x0c000000)
X86_PRIV = (0x0e000000, 0x0e100000)

# cause -> (verdict class, owner, hard?, why). `hard` mirrors report.py's split (False = soft or oracle gap).
POLICY = {
    # --- by design -------------------------------------------------------------------------------------------------
    'bool-repr':      ('by-design', '-', False, 'a bool byte other than 0/1: MSVC tests the byte, GCC bit 0; the '
                                                'exe never stores such a byte (D10 keeps bool 1 byte)'),
    'nan-bits':       ('by-design', '-', False, 'only the NaN payload/sign differ (x87 real indefinite 0xffc00000 '
                                                'vs AArch64 default NaN 0x7fc00000); no game code inspects payloads'),
    'x87-precision':  ('by-design', '-', False, 'x87 80-bit intermediates vs IEEE single/double, within 4 ulp or '
                                                'relative 1e-6/1e-12, or the native value equals the oracle\'s '
                                                'unrounded x87 value'),
    'division':       ('by-design', '-', False, 'x86 raises #DE (x/0, INT_MIN/-1) where sdiv/udiv give 0 / wrap: '
                                                'the exe would crash on that input'),
    'call-order':     ('by-design', '-', False, 'two adjacent calls swapped: C++ leaves operand evaluation order open'),
    'vptr-store':     ('by-design', '-', False, 'a dead vptr store GCC drops (D7: objects hold Itanium vptrs)'),
    'array-new-check': ('by-design', '-', False, 'GCC checks new[]\'s count for overflow, MSVC 2008 does not'),
    'ilp32-pointer':  ('by-design', '-', False, 'an address computed past 4 GiB (wraparound on x86): needs a pointer '
                                                'the exe never holds'),
    'float->int':     ('by-design', 'S12', False, 'out-of-range float->int (UB in C++); the FTOI2 plugin gives '
                                                  'MSVC semantics where the build loads it'),
    'shift-count':    ('by-design', '-', False, 'a shift count >= the operand width (UB in C++)'),
    'uninit-x86':     ('by-design', '-', False, 'the oracle\'s own result depends on garbage: undefined behaviour '
                                                'the exe gets away with; the port need not reproduce garbage'),
    # --- harness / oracle-model artefacts ----------------------------------------------------------------------------
    'local-static':   ('artifact', 'S13', False, 'MSVC keeps a function-local static and its guard in the image '
                                                 '(fuzzed bytes), GCC in .bss (zero): not compared state'),
    'uninit-both':    ('artifact', 'S13', False, 'both machines read garbage: the fuzzed case is invalid for both'),
    'oracle-vcall':   ('artifact', 'S13', False, 'the oracle faults on an indirect call the harness does not model '
                                                 '(S13 vftable modelling)'),
    'x86-custom-cc':  ('artifact', 'S13', False, 'MSVC whole-program custom register convention not in units.json'),
    'msvc-inlined':   ('artifact', 'S13', False, 'MSVC inlined a callee the native calls out of line'),
    'runtime-equiv':  ('artifact', 'S13', False, 'the same runtime function under two addresses (before difffuzz-8)'),
    'stack-arg':      ('artifact', 'S13', False, 'each machine\'s own stack address through an int (fixed since)'),
    'literal-arg':    ('artifact', 'S13', False, 'a literal at its image address vs the unit copy (fixed since)'),
    'literal-ret':    ('artifact', 'S13', False, 'a pointer result into the native unit\'s .rodata whose oracle '
                                                 'pointee the harness could not read (literal not pinned)'),
    'oracle-trap':    ('artifact', 'S13', False, 'the oracle executed int3/0xCC .text filler: code the harness did '
                                                 'not load (unit membership / preload gap)'),
    'no-native-entry': ('artifact', 'S13', False, 'the native image has no entry for the function (link subset)'),
    # --- real bugs -----------------------------------------------------------------------------------------------
    'uninit-arm':     ('real-bug', 'S1b', True, 'the native result depends on garbage: an argument slot the caller '
                                                'never wrote (ABI pop-mismatch) or an uninitialised local'),
    'native-garbage-arg': ('real-bug', 'S1b', True, 'the native passes one of its own garbage words as an argument '
                                                    '(runner arm_garbage): an argument nobody set'),
    'split-global':   ('real-bug', 'S2c', True, 'the native reaches its unit\'s copy of an image variable'),
    'native-undefined': ('real-bug', 'S1', True, 'the native calls a name the link binds to nothing'),
    'native-stub-data': ('real-bug', 'S2c', True, 'the native reads or writes a code/named stub as data: a data '
                                                  'symbol the link left as a stub (undefined variable)'),
    'native-address': ('real-bug', 'S2c', True, 'the native stores/passes/returns an address in its own image '
                                                'where the oracle has an image-window address: an unrebound '
                                                'variable or table (or a literal: check the pointee)'),
    'vptr-unpaired':  ('real-bug', 'S2c', False, 'no kind-V row pairs the native vtable with the image vftable'),
    'falloff?':       ('real-bug', 'S1', True, 'the native runs off a function end (FALL1)'),
    'native-trap':    ('real-bug', 'S1', True, 'the native executes a trap (FALL1 __builtin_trap or GCC '
                                               'unreachable) on a path where the exe returns'),
    'stack-protector': ('real-bug', 'S1', True, 'the native overruns a local buffer that MSVC\'s frame tolerated'),
    'native-null-read': ('real-bug', 'S1', True, 'the native dereferences a near-null address the oracle never '
                                                 'touches (a field read at another offset, or a hoisted load)'),
    'sret-mismatch':  ('real-bug', 'S1b', True, 'the hidden result pointer differs at a call (MSVC vs Itanium '
                                                'return-in-memory rules)'),
    'callee-saved':   ('real-bug', 'S1b', True, 'callee-saved registers clobbered: an ABI break'),
    'call-args':      ('real-bug', 'S2c', True, 'a bridge or definition delivers other argument bytes'),
    'signedness':     ('real-bug', 'S1', True, 'the divergence goes when an input byte >= 0x80 loses its top bit'),
    'float':          ('real-bug', 'S1', True, 'a float difference beyond tolerance (inf/NaN on one side)'),
    # --- not decided from the record -------------------------------------------------------------------------------
    'call-flow':      ('triage', 'S13b', True, 'the call sequence differs; inlining, rules and DECL1 rebinds explain '
                                               'most (replay with effects)'),
    'signedness?':    ('triage', 'S13b', True, 'a top-bit probe hit without a widening instruction in the oracle'),
    'native-early-exit': ('triage', 'S13b', True, 'the native makes a prefix of the oracle\'s calls and stops: an '
                                                  'early return or a branch on a field read differently'),
    'native-extra-calls': ('triage', 'S13b', True, 'the native makes the oracle\'s calls and more: a check the '
                                                   'oracle skips, or a call MSVC inlined into a loop'),
    'store-width':    ('triage', 'S13b', True, 'the same bytes stored with another width: one side writes bytes '
                                               'the other leaves (a bool/char vs dword member, a padded struct copy)'),
    'native-only-store': ('triage', 'S13b', True, 'the native writes memory the oracle never writes'),
    'x86-only-store': ('triage', 'S13b', True, 'the oracle writes memory the native never writes'),
    'oracle-fault':   ('triage', 'S13b', True, 'only the oracle faults on a data access: a dead load GCC dropped '
                                               '(by design) or a field the native reads at another offset (layout)'),
    'unclassified':   ('triage', 'S13b', True, 'no rule matched'),
}

CLASS_ORDER = ('real-bug', 'triage', 'artifact', 'by-design')


def policy(cause):
    return POLICY.get(cause, ('triage', 'S13b', True, 'cause not in the policy table'))


# --------------------------------------------------------------------------------------------- record-level rules

def _in(v, rng):
    return rng[0] <= v < rng[1]


def _word(hexs, off=0):
    b = bytes.fromhex(hexs or '')
    if len(b) < off + 4:
        return None
    return struct.unpack_from('<I', b, off)[0]


def _float_close(bx, ba):
    """(tolerant, both_nan) of two IEEE values of the same width (runner.float_verdict's tolerance)."""
    fmt = {4: ('<f', '<i', 1e-6), 8: ('<d', '<q', 1e-12)}.get(len(bx))
    if fmt is None or len(ba) != len(bx):
        return False, False
    fx, fa = struct.unpack(fmt[0], bx)[0], struct.unpack(fmt[0], ba)[0]
    if math.isnan(fx) and math.isnan(fa):
        return True, True
    if math.isnan(fx) or math.isnan(fa) or math.isinf(fx) or math.isinf(fa):
        return False, False
    ulp = abs(struct.unpack(fmt[1], bx)[0] - struct.unpack(fmt[1], ba)[0])
    rel = abs(fx - fa) / max(abs(fx), abs(fa), 1e-300)
    return ulp <= 4 or rel <= fmt[2], False


def _fault_addr(err):
    """('r'|'w'|'x', address) of a 'fault:<k>:<hex>' error, None otherwise (ilp32-high and uc faults too)."""
    p = str(err or '').split(':')
    if len(p) == 3 and p[0] == 'fault' and p[1] in ('r', 'w', 'x'):
        try:
            return p[1], int(p[2], 16)
        except ValueError:
            return None
    return None


def _rule_error(d):
    x, a = d.get('x86'), d.get('arm')
    if x is None and isinstance(a, str):
        if a.startswith('exc:'):
            return 'native-trap'
        if a == 'stack-protector':
            return 'stack-protector'
        if a == 'no-native-entry':
            return 'no-native-entry'
        fa = _fault_addr(a)
        if fa:
            if _in(fa[1], A64_PRIV) and fa[0] in ('r', 'w'):
                return 'native-stub-data'
            if fa[1] < NULL_END:
                return 'native-null-read'
    if a is None and isinstance(x, str):
        if x.startswith('exc:3') or x == 'exc:3':
            return 'oracle-trap'
        fx = _fault_addr(x)
        if fx and fx[0] in ('r', 'w'):
            return 'oracle-fault'
    return None


def _rule_call_args(d):
    if d.get('arm_garbage'):
        return 'native-garbage-arg'
    if d.get('arg') == 'sret':
        return 'sret-mismatch'
    bx, ba = bytes.fromhex(d.get('x86') or ''), bytes.fromhex(d.get('arm') or '')
    if d.get('cls') in ('F32', 'F64'):
        tol, nan = _float_close(bx, ba)
        if nan:
            return 'nan-bits'
        if tol:
            return 'x87-precision'
    if d.get('cls') in ('I32', 'P') and len(bx) >= 4 and len(ba) >= 4:
        vx, va = _word(d['x86']), _word(d['arm'])
        if _in(vx, WINDOW) and _in(va, A64_IMAGE):
            return 'native-address'
        if _in(va, A64_STUBS) and not _in(vx, A64_STUBS):
            return 'native-stub-data'
    return None


def _rule_return(d, conv):
    if 'pointee_x86' in d and (not d.get('pointee_x86') or not d.get('pointee_arm')):
        return 'literal-ret'
    fl = d.get('float') or {}
    if fl and not fl.get('tolerant'):
        try:
            if fl.get('x87') is not None and float(fl['x87']) == float(fl.get('arm')):
                return 'x87-precision'      # the native value is the oracle's unrounded x87 value
        except (TypeError, ValueError):
            pass
    vx, va = _word(d.get('x86')), _word(d.get('arm'))
    if vx is not None and va is not None and _in(vx, WINDOW) and _in(va, A64_IMAGE):
        return 'native-address'
    return None


def _rule_mem_write(d):
    fw = d.get('float_word') or {}
    if fw.get('tolerant'):
        return 'x87-precision'
    n, ox, oa = d.get('ndiff', 0), d.get('only_x86', 0), d.get('only_arm', 0)
    bx, ba = bytes.fromhex(d.get('x86') or ''), bytes.fromhex(d.get('arm') or '')
    if len(bx) >= 4 and len(ba) >= 4:
        tol, nan = _float_close(bx[:4], ba[:4])
        if nan:
            return 'nan-bits'
        vx, va = _word(d['x86']), _word(d['arm'])
        if _in(vx, WINDOW) and _in(va, A64_IMAGE):
            return 'native-address'
        if _in(va, A64_STUBS) and not _in(vx, A64_STUBS):
            return 'native-stub-data'
    if n and ox == n:
        # the oracle writes bytes the native leaves; when the native wrote the same value in the lower bytes
        # (only the upper bytes are missing) it is a narrower store, otherwise a store the native does not make
        return 'store-width' if _narrower(bx, ba) else 'x86-only-store'
    if n and oa == n:
        return 'store-width' if _narrower(ba, bx) else 'native-only-store'
    if n and ox + oa == n and ox and oa:
        return 'store-width'
    return None


def _narrower(wide, narrow):
    """`narrow` equals `wide` on a non-empty prefix and is zero after it (a byte/short store vs a dword)."""
    if len(wide) != len(narrow):
        return False
    k = len(narrow.rstrip(b'\0'))
    return 0 < k < len(wide) and narrow[:k] == wide[:k]


def _rule_calls(d):
    tx, ta = d.get('x86_trace'), d.get('arm_trace')
    if d.get('kind') == 'call-count' and isinstance(tx, list) and isinstance(ta, list):
        if len(ta) < len(tx) and tx[:len(ta)] == ta:
            return 'native-early-exit'
        if len(ta) > len(tx) and ta[:len(tx)] == tx:
            return 'native-extra-calls'
    return None


REFINABLE = (None, 'unclassified', 'call-flow', 'signedness?')


def refine(rec, cause=None):
    """The record-level cause of a divergence the probes left at `cause` (unclassified / call-flow / signedness?),
    or `cause` itself when no rule applies. Never overrides a probe's definite cause."""
    if cause not in REFINABLE:
        return cause
    d = (rec.get('_now') or rec.get('divergence') or {})
    k = d.get('kind')
    got = None
    if k == 'error':
        got = _rule_error(d)
    elif k == 'call-args':
        got = _rule_call_args(d)
    elif k == 'return':
        got = _rule_return(d, rec.get('conv'))
    elif k == 'mem-write':
        got = _rule_mem_write(d)
    elif k in ('call-count', 'call-target'):
        got = _rule_calls(d)
    if got is None:
        return cause or 'unclassified'
    # a probe that saw call-flow stays call-flow unless the record names something more specific
    return got


# ------------------------------------------------------------------------------------------------------- summary

def _load_campaign(d, tag=''):
    import report                 # reclassify: same causes as the verdict tables
    results = {}
    rp = os.path.join(d, 'results.jsonl')
    if os.path.exists(rp):
        for line in open(rp):
            line = line.strip()
            if line:
                r = json.loads(line)
                results[r['address']] = r
    gone, now = set(), {}
    cp = os.path.join(d, 'recheck%s.jsonl' % ('_' + tag if tag else ''))
    if os.path.exists(cp):
        for line in open(cp):
            x = json.loads(line)
            if x['status'] == 'gone':
                gone.add(x['file'])
            elif x['status'] == 'still':
                now[x['file']] = x['divergence']
    divs = []
    for p in sorted(glob.glob(os.path.join(d, 'div', '*.json'))):
        if os.path.basename(p) in gone:
            continue
        rec = json.load(open(p))
        rec['_now'] = now.get(os.path.basename(p))
        try:
            rec['_cause'] = report.reclassify(rec)
        except Exception:  # noqa: BLE001 - a record the static reclassifier cannot read keeps its hint
            rec['_cause'] = refine(rec, (rec.get('hint') or {}).get('cause'))
        rec['_file'] = os.path.basename(p)
        divs.append(rec)
    return results, divs


def summarize(dirs, tag=''):
    rows = collections.defaultdict(lambda: dict(divergences=0, functions=set(), worst=0, kinds=collections.Counter(),
                                                examples=[]))
    by_fn = collections.defaultdict(list)
    results = {}
    for d in dirs:
        r, divs = _load_campaign(d, tag)
        results.update(r)
        for rec in divs:
            if results.get(rec['address'], {}).get('status', 'ok') != 'ok':
                continue
            by_fn[rec['address']].append(rec)
    for addr, recs in by_fn.items():
        worst = min(recs, key=lambda x: (CLASS_ORDER.index(policy(x['_cause'])[0]), not policy(x['_cause'])[2]))
        rows[worst['_cause']]['worst'] += 1
        for rec in recs:
            row = rows[rec['_cause']]
            row['divergences'] += 1
            row['functions'].add(addr)
            row['kinds'][(rec.get('_now') or rec['divergence']).get('kind')] += 1
            if len(row['examples']) < 5:
                row['examples'].append(dict(address=addr, unit=rec.get('source'), file=rec['_file']))
    causes = {}
    for c, row in sorted(rows.items(), key=lambda kv: (-kv[1]['divergences'], kv[0])):
        cls, owner, hard, why = policy(c)
        causes[c] = dict(verdict=cls, owner=owner, hard=hard, why=why, divergences=row['divergences'],
                         functions=len(row['functions']), worst_of=row['worst'], kinds=dict(row['kinds']),
                         examples=row['examples'])
    by_class = collections.Counter()
    fn_class = {}
    for addr, recs in by_fn.items():
        fn_class[addr] = min((policy(x['_cause'])[0] for x in recs), key=CLASS_ORDER.index)
        by_class[fn_class[addr]] += 1
    ok = sum(1 for r in results.values() if r.get('status') == 'ok')
    return {
        'format': 'fnvnx-causes/1',
        'campaigns': [os.path.abspath(d) for d in dirs],
        'functions_ok': ok,
        'functions_diverging': len(by_fn),
        'functions_by_class': {c: by_class.get(c, 0) for c in CLASS_ORDER},
        'causes': causes,
        'policy': {c: dict(verdict=v[0], owner=v[1], hard=v[2], why=v[3]) for c, v in sorted(POLICY.items())},
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('summary')
    s.add_argument('dirs', nargs='+')
    s.add_argument('--recheck-tag', default='')
    s.add_argument('--out', default='')
    e = sub.add_parser('explain')
    e.add_argument('files', nargs='+')
    a = ap.parse_args(argv)
    if a.cmd == 'summary':
        out = summarize(a.dirs, a.recheck_tag)
        txt = json.dumps(out, indent=1, sort_keys=True)
        if a.out:
            open(a.out, 'w').write(txt + '\n')
        print('functions ok %d, diverging %d: %s' % (out['functions_ok'], out['functions_diverging'],
                                                    ', '.join('%s %d' % kv for kv in out['functions_by_class'].items())))
        for c, row in out['causes'].items():
            print('  %-20s %-9s %-5s div %5d fn %5d worst %5d' % (c, row['verdict'], row['owner'], row['divergences'],
                                                                 row['functions'], row['worst_of']))
    else:
        for p in a.files:
            rec = json.load(open(p))
            h = (rec.get('hint') or {}).get('cause')
            print('%s\thint=%s\trefined=%s\t%s' % (os.path.basename(p), h, refine(rec, h), policy(refine(rec, h))[0]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
