#!/usr/bin/env python3
"""Decoder completeness of #UD: the verdict of the pinned Sail x86 model (OWNER 9) on a byte sequence that capstone refuses.

RULE.  A refused sequence is #UD on the declared machine iff
  (a) it lies in the encoding space of a feature that the declared CPU does not have (CPUREF: VEX / EVEX / XOP prefixes, the 0F 0F / 0F 38 / 0F 3A maps: fnvlift/f_sys.encoding, the rule h_absent_ext
      already uses; the Sail model implements those spaces, the declared CPU does not advertise them), or
  (b) the pinned model's step function, started on the sequence, ends EVERY feasible path in a fault whose model text is a #UD, and it does so with the bytes after the first K bytes left FREE
      (isla symbolic code bytes): K, the least such length, is the decode extent of the #UD (the bytes the model needed to decide).
Every other outcome is not decided and the sequence stays a lift red:
  VALID      some path completes: the encoding is defined, so the decoder (capstone) has a gap
  GAP        a path ends in a model_error ("Opcode Unimplemented in x86isa!") or in a fault that is not a #UD (#GP, #NM): the model does not define #UD for it
  TRUNCATED  the verdict needs more bytes than the sequence has (K > len)
The #UD class of a model fault text is read from the model's own words (UD_TEXT): `(:UD)` and the ACL2 `#UD Encountered!`.  No opcode table, no mnemonic list.

The decision function is verdict(raw, run): `run(code16, free_from)` is model_sail.run_isla (cached by its key).  tools: main() writes udsail.tsv for the refused sequences of the images' baddecode.tsv files;
the lifter reads that file (inputs `udsail`) through f_sys.ud_gap and never runs isla."""
import argparse, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
FORMAT = '# fnvnx-udsail/1'
UD_TEXT = re.compile(r'\(:UD\)|#UD')                       # the model's #UD texts (model/*.sail x86_fault arguments)
CALLS = re.compile(r'Calling x86_(fault|model_error)<\w+>\(\["(.*?)"\]\)')

def messages(err):
    """[(kind, text)] of every model exception call of an isla run, one per path, in order"""
    return [(k, re.sub(r'\\+"', '"', m).strip('"')) for k, m in CALLS.findall(err)]

def classify(out, err, split_blocks):
    """-> (verdict, why) of one isla run: 'UD' iff there is no completing path and every path of the run is a #UD fault"""
    blocks = split_blocks(out)
    ok = [b for b in blocks if b[0] and b[0].startswith('Result: (_ unit)')]
    bad = [b for b in blocks if b not in ok]
    if ok: return 'VALID', '%d of %d paths complete' % (len(ok), len(blocks))
    if not bad: return 'GAP', 'isla returned no path: ' + err[-120:].replace('\n', ' ')
    msgs = messages(err)
    if len(msgs) != len(bad): return 'GAP', '%d paths, %d model exception calls: %s' % (len(bad), len(msgs), (bad[0][0] or '')[:80])
    if all(k == 'fault' and UD_TEXT.search(t) for k, t in msgs): return 'UD', sorted(set(t for _, t in msgs))[0]
    return 'GAP', '; '.join(sorted(set('%s: %s' % (k, t) for k, t in msgs))[:3])

def verdict(raw, run, split_blocks, feature_ud):
    """-> (verdict, extent, why) for the refused sequence raw (bytes, at most 16 used).  feature_ud(raw) -> kind or None: the encoding space of an absent feature (f_sys.encoding rule)"""
    raw = bytes(raw[:16])
    kind = feature_ud(raw)
    if kind: return 'UD', 2 if kind in ('vex', 'evex', 'xop') else 3 if kind in ('map38', 'map3a') else 2, 'feature:' + kind
    n = len(raw); v = why = None
    for k in range(1, n + 1):
        out, err = run(raw[:k].ljust(16, b'\0'), (k, 16 - k))      # the bytes from k on are free; a longer prefix only refines the paths, so the least k that is #UD on every path is the extent
        v, why = classify(out, err, split_blocks)
        if v == 'UD': return 'UD', k, why
    return ('TRUNCATED' if v == 'VALID' else v), n, why           # VALID on some continuation of all the bytes we have: not decided (the sequence ends before the model's decision); GAP: the model does not say #UD

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--sail-work', required=True); ap.add_argument('--sail-ir', required=True); ap.add_argument('--isla', required=True)
    ap.add_argument('--x86sys', required=True); ap.add_argument('--cpufeat', required=True)
    ap.add_argument('--bad', action='append', required=True, help='an image baddecode.tsv (entry, address, 8 bytes)')
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    from fnvlift import inputs
    inputs.configure(x86sys=os.path.abspath(a.x86sys), cpufeat=os.path.abspath(a.cpufeat))
    from proof import model_sail as M
    from fnvlift import f_sys
    M.configure(a.sail_work, a.sail_ir, a.isla)
    class B:
        def __init__(s, b): s.bytes = b
    def run(code, free):
        return M.run_isla(code, sym=([free], False) if free else None)
    rows = {}
    for f in a.bad:
        for ln in open(f):
            if ln.startswith('#') or not ln.strip(): continue
            e, x, h = ln.split('\t')[:3]; rows.setdefault(h.strip(), []).append((os.path.basename(os.path.dirname(os.path.abspath(f))), x))
    res, red = [], 0
    for h in sorted(rows):
        raw = bytes.fromhex(h)
        v, k, why = verdict(raw, run, M.split_blocks, lambda r: (lambda kd: kd if kd in ('vex', 'evex', 'xop', 'map38', 'map3a', 'map0f') else None)(f_sys.encoding(B(r))[0]))
        res.append((raw[:k].hex() if v == 'UD' else h, v, k, why)); red += v != 'UD'
        print('%s %s extent %d: %s' % (h, v, k, why))
    seen = {}
    for r in res:
        if r[1] == 'UD': seen.setdefault(r[0], r)
    with open(a.out, 'w') as o:
        o.write(FORMAT + '; columns: decided bytes (the decode extent), verdict, extent, evidence.  Rows of verdict UD only; the pinned Sail model decided each (proof/udsail.py)\n')
        for k in sorted(seen): o.write('%s\tUD\t%d\t%s\n' % (seen[k][0], seen[k][2], seen[k][3]))
    print('refused sequences %d, #UD %d, not decided %d' % (len(res), len(res) - red, red))
    return 1 if red else 0

if __name__ == '__main__': sys.exit(main())
