#!/usr/bin/env python3
"""report.py - per-stream verdict table and divergence catalogue from campaign results.

    report.py <campaign dir> [<campaign dir> ...] [--rules rules.json] [--bridges bridges.json] [--out report]

Stream attribution of a fuzzed function:
  S1-rules:<FAMILY>  its body differs between pristine and prepared source (attrib.py rules; family by pattern)
  S8-asm             the change is an asm replacement (ASM2/ASM4)
  S1-sse             the change is an SSE rule (SSE3/SSE4)
  S2-link            it calls out of its unit through a bridge (attrib.py bridges) or the divergence is a link cause
  S0-compile         body unchanged: any divergence is the compiler (J-0034 semantics), the ABI or the link
Hard divergences change behaviour for valid inputs or point at a port bug; soft ones are representation-level
(local statics, dead vptr stores) or need invalid inputs (bool bytes other than 0/1, address wraparound).
Writes <out>.md and <out>.json.
"""
import argparse
import collections
import glob
import json
import os
import re

import config

SOFT_CAUSES = {'local-static', 'vptr-store', 'bool-repr', 'ilp32-pointer', 'division', 'uninit-x86', 'nan-bits',
               'call-order', 'array-new-check', 'vptr-unpaired'}
HARNESS_CAUSES = {'x86-custom-cc', 'msvc-inlined', 'runtime-equiv', 'oracle-vcall', 'stack-arg', 'literal-arg'}     # oracle-model gaps: neither hard nor soft, reported apart
LINK_CAUSES = {'split-global', 'call-args', 'callee-saved'}
try:        # S13b-causes policy (causes.py): its by-design causes are soft, its artefacts oracle gaps
    import causes as _causes
    SOFT_CAUSES |= {c for c, v in _causes.POLICY.items() if v[0] == 'by-design'}
    HARNESS_CAUSES |= {c for c, v in _causes.POLICY.items() if v[0] == 'artifact'} - SOFT_CAUSES
    LINK_CAUSES |= {c for c, v in _causes.POLICY.items() if v[0] == 'real-bug' and v[1] == 'S2c'} - SOFT_CAUSES
except ImportError:  # pragma: no cover
    _causes = None


_UC = None


def _custom_cc(rec):
    """Registers the oracle reads on entry outside its declared convention (classify.entry_register_reads)."""
    global _UC
    try:
        import classify
        import conv as convmod
        import engine
        if _UC is None:
            _UC = engine.UnitCache(str(config.LINK), str(config.A64WORK))
        a = int(rec['address'], 16)
        fn = _UC.x86_unit(rec['source'], a).funcs[a]
        cv = convmod.Conv(_UC.tree_def.get(a, fn['symbol']), rec['source'])
        return classify.entry_register_reads(fn, cv)
    except Exception:  # noqa: BLE001
        return []


DEALLOCATORS = {'00401030', '00aa1460', '00aa4060', '00ecd291'}     # operator delete, sized delete, Deallocate, free


def _call_count_cause(rec, d):
    """A call-count divergence explained by the record alone:
    - `deleting-dtor-flags` (causes.py, S13b): an MSVC deleting destructor ??_G fuzzed with flags bit 0 clear
      (destroy, do not free) makes the oracle's calls; the native ??_G is the Itanium D0, which always frees: the
      native makes the same calls plus deallocator calls only (natively such a call reaches D1). Decided here
      because the falloff? fallback below came first;
    - `native-undefined` / `after-native-undefined`: the oracle makes no call while the native's first call /
      a later call goes to a named stub (a C++ name the link binds to nothing): the native ran on a canned result
      where the oracle ran the body (S2c / S2d bind the name)."""
    tx, ta = d.get('x86_trace'), d.get('arm_trace')
    if not isinstance(tx, list) or not isinstance(ta, list):
        return None
    if (rec.get('symbol') or '').startswith('??_G') and ta[:len(tx)] == tx and len(ta) > len(tx) and \
            all(str(t) in DEALLOCATORS for t in ta[len(tx):]):
        try:
            flags = int.from_bytes(bytes.fromhex(((rec.get('case') or {}).get('args') or {}).get('p0') or '00')[:4],
                                   'little')
        except ValueError:
            flags = 1
        if not flags & 1:
            return 'deleting-dtor-flags'
    if d.get('x86') == 0 and ta:
        if str(ta[0]).startswith('name:'):
            return 'native-undefined'
        if any(str(t).startswith('name:') for t in ta):
            return 'after-native-undefined'
    return None


BOOL_RESULT = re.compile(r'@@(?:Y[AGI]|[A-Z][A-Z0-9][AEGI]?)_N')     # MSVC decorated name returning bool


def _signedness_by_design(rec):
    """The top-bit probe (`signedness`: clearing a top bit of an input byte removes the divergence) also fires on
    shapes whose cause is by design, the top bit being a float sign, a bool byte, INT_MIN or the top of the address
    space: a float argument within the x87 tolerance (x87-precision / nan-bits), a bool result with the oracle's
    truth value in another byte (bool-repr), x86 #DE where AArch64 divides (division), a native access that
    crosses 4 GiB where the x86 address wraps (ilp32-pointer). And a first difference that is a native call of a named
    stub is native-undefined (S2c / S2d), whatever the probe saw. None otherwise: the probe's cause stands."""
    d = rec.get('_now') or rec.get('divergence') or {}
    k = d.get('kind')
    if k == 'call-args' and d.get('cls') in ('F32', 'F64') and _causes is not None:
        got = _causes._rule_call_args(d)
        if got in ('x87-precision', 'nan-bits'):
            return got
    if k == 'return' and BOOL_RESULT.search(rec.get('symbol') or ''):
        try:
            bx, ba = bytes.fromhex(d.get('x86') or ''), bytes.fromhex(d.get('arm') or '')
        except ValueError:
            bx = ba = b''
        if len(bx) == 1 and len(ba) == 1 and bx[0] in (0, 1) and ba[0] not in (0, 1) and bx[0] == 1:
            return 'bool-repr'
    if k == 'call-target' and str(d.get('arm') or '').startswith('name:'):
        return 'native-undefined'           # not by design: the first difference is a call of an unbound name
    if k == 'error' and d.get('x86') in ('div0', 'exc:0') and not d.get('arm'):
        return 'division'
    if k == 'error' and not d.get('x86') and str(d.get('arm') or '').startswith(('fault:r:', 'fault:w:')):
        try:
            a = int(str(d['arm']).split(':')[2], 16)
        except (ValueError, IndexError):
            a = 0
        if a >= 0xfffffff0:
            return 'ilp32-pointer'
    return None


def reclassify(rec):
    """Static re-classification of a saved divergence (causes added after the record was written)."""
    global _UC
    hint = rec.get('hint') or {}
    cause = hint.get('cause')
    # (an x86-custom-cc verdict is classify.hint's, on the case's own garbage probe since difffuzz-19; the static
    # entry-register test here overrode real-bug hints: S13b audit)
    if cause == 'signedness':
        got = _signedness_by_design(rec)
        if got:
            return got
    if cause not in (None, 'unclassified', 'call-flow', 'signedness?'):
        if _causes is not None and cause in _causes.HINT_REFINABLE:
            return _causes.refine(rec, cause)   # oracle-vcall: a tag, split by the record (causes._rule_vcall)
        return cause
    try:
        import check_rebind
        obj = config.LINK / 'fixed' / config.obj_name(rec['source'])
        if obj.exists() and int(rec['address'], 16) in check_rebind.bypassing_functions(str(obj)):
            return 'split-global'
    except Exception:  # noqa: BLE001
        pass
    d = rec['divergence']
    if rec.get('_now') is not None:
        d = rec['_now']
        if d.get('arm_local_static') and ((d.get('kind') == 'mem-write' and d.get('only_x86') == d.get('ndiff')) or
                                          (d.get('kind') == 'call-target' and d.get('arm_next') == d.get('x86') and
                                           d.get('x86_next') != d.get('arm'))):
            return 'local-static'
        if d.get('arm_private_reads'):
            return 'split-global'
    if d.get('bool_repr'):
        return 'bool-repr'
    if d.get('nrvo_copy'):
        return 'nrvo-copy'
    if d.get('nrvo_elided'):
        return 'nrvo-elision'
    if d.get('nan_bits'):
        return 'nan-bits'
    if d['kind'] in ('mem-write', 'return'):
        try:
            import classify
            bx, ba = bytes.fromhex(d.get('x86') or ''), bytes.fromhex(d.get('arm') or '')
            if len(bx) == len(ba) and len(bx) >= 4 and classify._nan_only(bx, ba):
                return 'nan-bits'
        except Exception:  # noqa: BLE001
            pass
    if d['kind'] == 'mem-write' and d.get('only_x86') == d.get('ndiff') and d.get('ndiff', 0) <= 4:
        try:
            import classify
            if int.from_bytes(bytes.fromhex(d['x86'])[:4], 'little') in classify.image_vftables():
                return 'vptr-store' if classify.is_dtor_name(rec.get('symbol') or '') else 'vptr-differs'
        except Exception:  # noqa: BLE001
            pass
    if d['kind'] == 'mem-write' and d.get('x86') and d.get('arm'):
        try:
            if _UC is None:
                import engine
                _UC = engine.UnitCache(str(config.LINK), str(config.A64WORK))
            vx = int.from_bytes(bytes.fromhex(d['x86'])[:4], 'little')
            va = int.from_bytes(bytes.fromhex(d['arm'])[:4], 'little')
            _UC.vtable_map(type('I', (), {'stubs': {}, 'syms': {}})())     # loads the vftable-by-class table
            vfts = set(_UC._vft_by_class.values())
            import classify
            vfts |= classify.image_vftables()
            if vx in vfts and (0x09000000 <= va < 0x0a000000 or 0x0a000000 <= va < 0x0b800000):
                # the oracle stores an image vftable, the native its own vtable address point that the link pairs
                # with none (no kind-V row, decomp-local class name): a vptr pairing gap (S2c), soft for behaviour
                return 'vptr-unpaired'
        except Exception:  # noqa: BLE001
            pass
    if d['kind'] == 'call-count':
        got = _call_count_cause(rec, d)
        if got:
            return got
    if d['kind'] == 'call-count' and d.get('x86') == 0:
        return 'falloff?'
    if d['kind'] == 'call-args' and d.get('cls') in ('I32', 'P'):
        try:
            vx = int.from_bytes(bytes.fromhex(d.get('x86') or '')[:4], 'little')
            va = int.from_bytes(bytes.fromhex(d.get('arm') or '')[:4], 'little')
            import world
            if world.STACK[0] <= vx < world.STACK[1] and world.STACK[0] <= va < world.STACK[1]:
                return 'stack-arg'       # each machine's own frame through an int parameter (harness, fixed since)
            # (an image address vs a native image address is left to causes.refine: native-address, a real bug, or
            # literal-arg when the oracle's value is in the literal pool; S13b audit)
        except ValueError:
            pass
    if d['kind'] == 'call-target' and str(d.get('arm', '')).startswith('name:') and \
            d.get('arm') != 'name:__cxa_throw_bad_array_new_length':
        try:
            if _UC is None:
                import engine
                _UC = engine.UnitCache(str(config.LINK), str(config.A64WORK))
            import libcalls
            nm = d['arm'][5:]
            if nm not in _UC.providers() and nm not in libcalls.EQUIV and nm not in libcalls.A64 and \
                    not (nm.startswith('__isoc23_') and nm[9:] in _UC.providers()):
                return 'native-undefined'    # the native code calls a name the link binds to nothing (S1 / S2c)
        except Exception:  # noqa: BLE001
            pass
    if d['kind'] == 'call-target' and d.get('arm') == 'name:__cxa_throw_bad_array_new_length':
        return 'array-new-check'  # GCC checks new[]'s count for overflow, MSVC 2008 does not (only huge counts)
    if d['kind'] == 'call-target':
        try:
            if _UC is None:
                import engine
                _UC = engine.UnitCache(str(config.LINK), str(config.A64WORK))
            import classify

            class _T:
                oracle_callees = staticmethod(_UC.oracle_callees)
            if classify.msvc_inlined(_T, d):
                return 'msvc-inlined'
            prov = _UC.providers()
            a, x = d.get('arm') or '', d.get('x86') or ''
            if a.startswith('name:') and prov.get(a[5:]) is not None and '%08x' % prov[a[5:]] == x:
                return 'runtime-equiv'   # the native provider of that CRT address (harness before difffuzz-8)
            import libcalls
            if x.startswith('imp:') and a[:1].isdigit() and _UC.iat_names().get(int(a, 16)) == x[4:]:
                return 'runtime-equiv'   # the import, called natively through its IAT slot
            fam = libcalls.FAMILY
            if fam.get(int(x, 16) if x[:1].isdigit() else None) and \
                    fam.get(int(x, 16)) == fam.get(int(a, 16) if a[:1].isdigit() else None):
                return 'runtime-equiv'
        except Exception:  # noqa: BLE001
            pass
    if d['kind'] == 'call-target' and d.get('x86_next') and d.get('x86_next') == d.get('arm') and \
            d.get('arm_next') == d.get('x86'):
        if d.get('call_order_unproven'):
            return 'call-order-unproven'     # (r4 uncls) the exe does not show the pair to be an argument list
        return 'call-order'      # two adjacent calls swapped: C++ leaves the evaluation order of operands open
    if _causes is not None:
        return _causes.refine(rec, cause)       # record-level rules for what the probes left open
    return cause or 'unclassified'


S1B = ('XINC1', 'FTOL1', 'RET1', 'DBUG1')


def streams_of(r, rules, bridges, ttags=None):
    tags = []
    fr = rules.get(r['address'])
    if fr:
        fams = fr['rules']
        for f in fams:
            if f in ('ASM2', 'ASM4'):
                tags.append('S8-asm')
            elif f in ('SSE3', 'SSE4'):
                tags.append('S1-sse')
            elif f in S1B:
                tags.append('S1b:' + f)
            elif f != 'GONE':
                tags.append('S1-rules:' + f)
        if not tags:
            tags.append('S0-compile')
    else:
        tags.append('S0-compile')
    t = (ttags or {}).get(r['address'])
    if t and t.split(':')[0] in ('bridge', 'manual', 'x86-inconsistent'):
        tags.append('S2-abi:' + t.split(':')[0])
    elif r.get('kind') != 'leaf' and bridges.get(r['source'], {}).get('code-bridge'):
        tags.append('S2-link')
    return tags


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dirs', nargs='+')
    ap.add_argument('--rules', default=str(config.WORK / 'rules.json'))
    ap.add_argument('--bridges', default=str(config.WORK / 'bridges.json'))
    ap.add_argument('--out', default=str(config.WORK / 'report'))
    ap.add_argument('--recheck-tag', default='', help='use <dir>/recheck_<tag>.jsonl (a replay on other objects)')
    ap.add_argument('--targets', action='append', default=[], help='target lists whose `tag` field groups rows')
    ap.add_argument('--hard-tsv', default='', help='write the hard divergences: address, cause, streams, kind, unit')
    a = ap.parse_args()
    rules = json.load(open(a.rules))['functions'] if os.path.exists(a.rules) else {}
    bridges = json.load(open(a.bridges)) if os.path.exists(a.bridges) else {}
    results, divs = {}, []
    ttags = {}
    for tp in a.targets:
        for line in open(tp):
            x = json.loads(line)
            if x.get('tag'):
                ttags[x['address']] = x['tag']
    for d in a.dirs:
        for line in open(os.path.join(d, 'results.jsonl')):
            r = json.loads(line)
            results[r['address']] = r
        gone, now = set(), {}
        rp = os.path.join(d, 'recheck%s.jsonl' % ('_' + a.recheck_tag if a.recheck_tag else ''))
        if os.path.exists(rp):
            for line in open(rp):
                x = json.loads(line)
                if x['status'] == 'gone':
                    gone.add(x['file'])
                elif x['status'] == 'still':
                    now[x['file']] = x['divergence']
        for p in sorted(glob.glob(os.path.join(d, 'div', '*.json'))):
            if os.path.basename(p) in gone:
                continue        # no longer reproduces: it was a harness limit fixed since
            rec = json.load(open(p))
            rec['_file'] = p
            rec['_now'] = now.get(os.path.basename(p))
            rec['_cause'] = reclassify(rec)
            divs.append(rec)
    by_fn = collections.defaultdict(list)
    for rec in divs:
        by_fn[rec['address']].append(rec)
    table = collections.defaultdict(lambda: dict(functions=0, execs=0, hard=0, soft=0, harness=0, setup=0,
                                                 causes=collections.Counter()))
    hard_rows = []
    for addr, r in results.items():
        tags = streams_of(r, rules, bridges, ttags) + ['ALL']
        recs = by_fn.get(addr, [])
        for d in recs:
            if d['_cause'] in LINK_CAUSES and 'S2-link' not in tags:
                tags.append('S2-link')
        if r.get('status') == 'ok':
            for d in recs:
                if d['_cause'] not in SOFT_CAUSES | HARNESS_CAUSES and not d['divergence'].get('soft'):
                    hard_rows.append((addr, d['_cause'], ','.join(t for t in tags if t != 'ALL'),
                                      (d.get('_now') or d['divergence']).get('kind', ''), r.get('source', '')))
        for t in tags:
            row = table[t]
            if r.get('status') != 'ok':
                row['setup'] += 1
                continue
            row['functions'] += 1
            row['execs'] += r.get('execs', 0)
            hard = [d for d in recs if d['_cause'] not in SOFT_CAUSES | HARNESS_CAUSES
                    and not d['divergence'].get('soft')]
            if hard:
                row['hard'] += 1
            elif any(d['_cause'] in HARNESS_CAUSES for d in recs):
                row['harness'] += 1
            elif recs:
                row['soft'] += 1
            for d in recs:
                row['causes'][d['_cause']] += 1
    lines = ['# difffuzz verdicts', '',
             '| stream | functions | executions | hard | soft | oracle gap | not set up | causes |',
             '|---|---:|---:|---:|---:|---:|---:|---|']
    order = sorted(table, key=lambda t: (t != 'ALL', t))
    for t in order:
        row = table[t]
        lines.append('| %s | %d | %d | %d | %d | %d | %d | %s |' % (
            t, row['functions'], row['execs'], row['hard'], row['soft'], row['harness'], row['setup'],
            ', '.join('%s %d' % kv for kv in row['causes'].most_common())))
    lines += ['', '## Divergences by cause', '']
    by_cause = collections.defaultdict(list)
    for rec in divs:
        by_cause[rec['_cause']].append(rec)
    for cause in sorted(by_cause, key=lambda c: -len(by_cause[c])):
        recs = by_cause[cause]
        lines.append('### %s (%d)' % (cause, len(recs)))
        for rec in recs[:6]:
            d = rec['divergence']
            lines.append('* `%s` %s `%s` - %s' % (rec['address'], rec['source'], rec['symbol'],
                                                  json.dumps({k: v for k, v in d.items()
                                                              if k in ('kind', 'x86', 'arm', 'addr', 'arg', 'index',
                                                                       'target')})[:240]))
        lines.append('')
    open(a.out + '.md', 'w').write('\n'.join(lines) + '\n')
    if a.hard_tsv:
        with open(a.hard_tsv, 'w') as f:
            for row in sorted(set(hard_rows)):
                f.write('\t'.join(row) + '\n')
    json.dump({t: dict(v, causes=dict(v['causes'])) for t, v in table.items()}, open(a.out + '.json', 'w'),
              indent=1)
    print('\n'.join(lines[:4 + len(order)]))


if __name__ == '__main__':
    main()
