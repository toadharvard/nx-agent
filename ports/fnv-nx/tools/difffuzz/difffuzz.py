#!/usr/bin/env python3
"""difffuzz.py - differential fuzzing of the native AArch64 port against the MSVC 2008 oracle, per function.

    difffuzz.py one <source> <addr>            run one function once (zero world), print both Results
    difffuzz.py fuzz <targets.jsonl> [opts]    the coverage-guided campaign over a list of functions
    difffuzz.py repro <divergence.json>        re-run a saved minimal reproducer
    difffuzz.py lockstep <addr> [opts]         run a call tree on both machines, compare the trace by address

The x86 side is the oracle (pristine decomp built with MSVC 2008, byte-identical to FalloutNV.exe); the AArch64 side
is our candidate object linked by address (tools/link). See docs/DIFFFUZZ.md. Everything read is local and derived
from the decomp: no decomp source or Microsoft binary is produced here, only addresses, counts and divergence facts.
"""
import argparse
import json
import os
import sys

import config
import engine
import world


def _cache():
    return engine.UnitCache(str(config.WORK.parent / 'link'), str(config.WORK / 'a64'))


def cmd_one(a):
    uc = _cache()
    addr = int(a.addr, 16)
    pair, t = uc.pair(a.source, addr)
    case = world.Case(seed=a.seed, bg=a.bg)
    rx, ra, div = pair.run(case)
    print('conv:', t.conv.describe())
    for side, r in (('x86', rx), ('arm', ra)):
        print(side + ':', 'ret=%s' % (r.ret.hex() if r.ret else None), 'calls=%d' % len(r.calls),
              'writes=%d' % len(r.writes), 'blocks=%d' % len(r.blocks), 'err=%s' % r.error)
    print('calls x86:', ['%08x' % (c.target & 0xffffffff) for c in rx.calls])
    print('calls arm:', ['%08x' % (c.target & 0xffffffff) for c in ra.calls])
    print('divergence:', json.dumps(div) if div else 'none')


def cmd_fuzz(a):
    import fuzz
    fuzz.campaign(a)


def cmd_repro(a):
    uc = _cache()
    d = json.load(open(a.file))
    case = world.Case.from_json(d['case'])
    pair, t = uc.pair(d['source'], int(d['address'], 16))
    if a.trace:
        import trace
        trace.attach(pair, t)
    rx, ra, div = pair.run(case)
    print('conv:', t.conv.describe())
    for side, r in (('x86', rx), ('arm', ra)):
        print(side + ':', 'ret=%s' % (r.ret.hex() if r.ret else None), 'err=%s' % r.error,
              'calls=%s' % ['%08x' % (c.target & 0xffffffff) for c in r.calls],
              'writes=%s' % {('%08x' % k): v for k, v in sorted(r.writes.items())[:16]})
    print('divergence:', json.dumps(div, indent=1) if div else 'NONE (no longer reproduces)')


def cmd_lockstep(a):
    import lockstep
    lockstep.run(a)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('one'); p.add_argument('source'); p.add_argument('addr')
    p.add_argument('--seed', type=int, default=0); p.add_argument('--bg', default='zero'); p.set_defaults(fn=cmd_one)
    p = sub.add_parser('fuzz'); p.add_argument('targets'); p.add_argument('--out', default=str(config.WORK / 'campaign'))
    p.add_argument('--iters', type=int, default=200); p.add_argument('--jobs', type=int, default=1)
    p.add_argument('--max-steps', type=int, default=200000); p.add_argument('--time', type=float, default=0)
    p.add_argument('--resume', action='store_true'); p.add_argument('--tag', default='run'); p.set_defaults(fn=cmd_fuzz)
    p = sub.add_parser('repro'); p.add_argument('file'); p.add_argument('--trace', action='store_true')
    p.set_defaults(fn=cmd_repro)
    p = sub.add_parser('lockstep'); p.add_argument('addr'); p.add_argument('--source', default=None)
    p.add_argument('--bg', default='zero'); p.add_argument('--out', default=str(config.WORK / 'lockstep'))
    p.add_argument('--max-steps', type=int, default=2000000); p.add_argument('--depth', type=int, default=64)
    p.set_defaults(fn=cmd_lockstep)
    a = ap.parse_args()
    config.WORK.mkdir(parents=True, exist_ok=True)
    a.fn(a)


if __name__ == '__main__':
    main()
