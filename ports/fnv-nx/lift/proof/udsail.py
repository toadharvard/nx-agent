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
  FETCH_OUT  the verdict needs more bytes than the sequence has (K > len)
  DEFAULT    the opcode is in the default arm of a decode table of the model (not an architectural statement)
The #UD class of a model fault text is read from the model's own words (UD_TEXT, case-insensitive): `(:UD)` / `(:ud)` and the ACL2 `#UD Encountered!`.  No opcode table, no mnemonic list.

SOUNDNESS OF (b) (pinned model: sail-x86-from-acl2 0c6c023d, model/*.sail).
  * The two exception kinds are different functions: prelude.sail:67 x86_model_error = the model's own state error ("Opcode Unimplemented in x86isa!", ACL2 x86-step-unimplemented: GAP here), prelude.sail:70
    x86_fault = an architectural fault, of which `(:UD)` and `(X86-ILLEGAL-INSTRUCTION "#UD Encountered!" ...)` (ACL2 x86-illegal-instruction) are the #UD ones.  A `throw(Emsg("Translation error: ... not translated"))`
    arm has no x86_fault call, so the call count of a run does not match its path count and classify says GAP.
  * A decode-table DEFAULT arm (`_ => x86_fault(#UD Encountered!)`) is NOT an architectural statement: it is "the ACL2 model does not list this opcode".  The pinned source has exactly two: step.sail one-byte map
    (arms 255 of 256: the default is 0xd6 alone) and two_byte_opcodes_dispatch.sail (21 opcodes).  default_opcodes() derives them from the model source (the complement of the explicit arms of the `match opcode`
    that has a `_` arm), and verdict() returns DEFAULT (red) for an opcode in them, whatever the run says.  The `else` of a ModRM.reg group (c7 /1-/6, 8f /1-/7, fe /2-/7, ff /7 ...) is not such a default: the model lists
    the whole group by reg, so its fault is an explicit arm of a region the model covers completely.
  * The model has every CPUID feature on (prelude.sail feature_flag_fn = 1), so no UD it raises is a feature-absent UD and none it fails to raise is a claim: the feature UDs of the declared CPU are class (a).
  * System state of a run: CPL 3 (seg_visibles = 0x2b), CS.D = 1 (attrs 0xc9a), EFER.LMA = 0, the control registers of the record where it decides a bit, and a bit the record leaves undecided is tried at both values:
    the verdict must be #UD in every variation (sysvariants), so it never depends on a system bit the declared machine does not state.
  * A fetch beyond the bytes given is FETCH_OUT (red).  The bytes after the decode extent are FREE in the run, so a #UD verdict does not depend on any byte beyond K, and the baddecode row's 8 bytes suffice; if K exceeds
    them the sequence is FETCH_OUT.
Rows carry their class: `sail` (rule b) or `feature` (rule a).  A `feature` row decides the class only: the site still needs its call-graph unreachability certificate on the declared CPU (CLAUDE.md, the declared CPU).

The decision function is verdict(raw, run): `run(code16, free_from)` is model_sail.run_isla (cached by its key).  tools: main() writes udsail.tsv for the refused sequences of the images' baddecode.tsv files;
the lifter reads that file (inputs `udsail`) through f_sys.ud_gap and never runs isla."""
import argparse, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
FORMAT = '# fnvnx-udsail/2'
UD_TEXT = re.compile(r'\(:UD\)|#UD', re.I)                       # the model's #UD texts (model/*.sail x86_fault arguments)
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

class _Bytes:
    def __init__(s, b): s.bytes = b

def sdm_feature(raw, rows, table, cpu, nopflags=frozenset()):
    """-> (flag, extent) when the legacy-encoded sequence raw matches the SDM opcode-table rows (proof/sdmfeat.match_rows, the L3b rule of the cleared-feature partition) of exactly one CPUID flag expression, that flag is
    absent on the declared CPU (sdmfeat.eval_flag over the SDM's own flag table and lift/rt/cpumodel.c), and the SDM states no `executed as` alias and no NOP-without-flag rule for it; else None.  extent: prefixes + opcode bytes
    (+ the ModRM byte of a /digit or ModRM-class row).  A cleared-feature row decides the CLASS of the sequence (rule a); the site still needs its call-graph certificate"""
    from proof import sdmfeat
    mand, body, pos = sdmfeat.form_of(_Bytes(raw))
    if not body or body[:1] in (b'\xc4', b'\xc5'): return None
    cand, _kind = sdmfeat.match_rows(mand, body, rows)
    flags = {r['flag'] for r in cand}
    if len(flags) != 1 or any(r['exec_as'] for r in cand): return None
    flag = flags.pop()
    if not flag or flag in nopflags: return None
    present, unknown = sdmfeat.eval_flag(flag, table, cpu)
    if present or unknown: return None
    d = cand[0]['p']
    return flag, pos + len(d['bytes']) + (1 if d['digit'] is not None or cand[0].get('modc', 'any') != 'any' else 0)

def default_opcodes(model_dir):
    """{'1': set, '0f': set}: the opcodes that the model's decode tables send to their DEFAULT arm `_ => x86_fault(#UD Encountered!)`: for each `(match opcode {` of step.sail (one-byte map) and
    two_byte_opcodes_dispatch.sail (0F map) that has a `_` arm, the values 0..255 that no explicit arm lists.  Derived from the pinned model source, never written down"""
    res = {}
    for f, key in (('step.sail', '1'), ('two_byte_opcodes_dispatch.sail', '0f')):
        lines = open(os.path.join(model_dir, f)).read().split('\n')
        for i, l in enumerate(lines):
            m = re.match(r'(\s*)\(match opcode\s*\{', l)
            if not m: continue
            base, listed, dflt, j = len(m.group(1)), set(), False, i + 1
            while j < len(lines) and not re.match(r'\s{%d}\}\)' % base, lines[j]):
                a = re.match(r'\s{%d}((?:\d+|0x[0-9a-fA-F]+)(?:\s*\|\s*(?:\d+|0x[0-9a-fA-F]+))*)\s*=>' % (base + 2), lines[j])
                if a: listed |= set(int(t, 0) for t in re.split(r'\s*\|\s*', a.group(1)))
                if re.match(r'\s{%d}_\s*=>' % (base + 2), lines[j]): dflt = True
                j += 1
            if dflt: res.setdefault(key, set()).update(x for x in range(256) if x not in listed)
    assert set(res) == {'1', '0f'}, 'default_opcodes: no `match opcode` with a default arm found in %s' % model_dir
    return res

PREFIXES = (0x2e, 0x36, 0x3e, 0x26, 0x64, 0x65, 0x66, 0x67, 0xf0, 0xf2, 0xf3)      # the legacy prefix bytes (32-bit mode has no REX)

def opcode_of(raw):
    """('1' | '0f', opcode byte) of the sequence after its legacy prefixes; None when the sequence ends there"""
    i = 0
    while i < len(raw) and raw[i] in PREFIXES: i += 1
    if i >= len(raw): return None
    if raw[i] == 0x0f: return ('0f', raw[i + 1]) if i + 1 < len(raw) else None
    return ('1', raw[i])

def bitfield(model_dir, name):
    """{field: (hi, lo)} of `bitfield <name>` in structures.sail"""
    t = open(os.path.join(model_dir, 'structures.sail')).read()
    m = re.search(r'bitfield %s\s*:[^=]*=\s*\{(.*?)\}' % name, t, re.S)
    return {f: (int(h), int(l or h)) for f, h, l in re.findall(r'(\w+)\s*:\s*(\d+)(?:\.\.(\d+))?', m.group(1))}

def sysvariants(record, model_dir):
    """the control-register states a run is made in: CR0 and CR4 (17 words, element 0 and 4) with every bit the record decides at its value, every other bit 0; and, for the bits the record leaves undecided (cr0, cr4, cr4_sse
    entries without a `value`), each bit set alone and all of them set: a #UD verdict must hold in every one, so it does not depend on a system bit the declared machine does not state"""
    f0, f4 = bitfield(model_dir, 'cr0bits'), bitfield(model_dir, 'cr4bits')
    base, und = [0, 0], []
    for sec in ('cr0', 'cr4', 'cr4_sse'):
        for n, v in record[sec].items():
            reg, (hi, lo) = (0, f0[n]) if sec == 'cr0' else (1, f4[n])
            assert hi == lo, n
            if 'value' in v: base[reg] |= v['value'] << lo
            else: und.append((reg, lo))
    vs = [tuple(base)] + [tuple(b | (1 << lo if r == i else 0) for i, b in enumerate(base)) for r, lo in und]
    allset = list(base)
    for r, lo in und: allset[r] |= 1 << lo
    out = []
    for c0, c4 in vs + [tuple(allset)]:
        w = [0] * 17; w[0], w[4] = c0, c4
        if w not in out: out.append(w)
    return out

def verdict(raw, run, split_blocks, feature_ud, defaults, variants):
    """-> (verdict, extent, why, class) for the refused sequence raw.  feature_ud(raw) -> kind or None: the encoding space of an absent feature (f_sys.encoding rule: class `feature`).  defaults: default_opcodes().
    variants: the system states (sysvariants); run(code, free, ctrs) is model_sail.run_isla.  #UD is claimed only when every variant says #UD"""
    raw = bytes(raw[:16])
    kind = feature_ud(raw)      # a kind string (the prefix spaces of f_sys.encoding) or a (flag, extent) of the SDM rows
    if isinstance(kind, tuple): return 'UD', kind[1], 'feature:' + kind[0] + ' (SDM opcode-table row, flag absent on the declared CPU)', 'feature'
    if kind: return 'UD', 2 if kind in ('vex', 'evex', 'xop') else 3 if kind in ('map38', 'map3a') else 2, 'feature:' + kind, 'feature'
    oc = opcode_of(raw)
    if oc is not None and oc[1] in defaults[oc[0]]: return 'DEFAULT', 0, 'opcode %s%02x is in the default arm of the model\'s decode table' % ('0f ' if oc[0] == '0f' else '', oc[1]), 'sail'
    n = len(raw); res = None
    for k in range(1, n + 1):
        vs = []
        for ctrs in variants:
            out, err = run(raw[:k].ljust(16, b'\0'), (k, 16 - k), ctrs)      # the bytes from k on are free; a longer prefix only refines the paths, so the least k that is #UD on every path is the extent
            vs.append(classify(out, err, split_blocks))
        if all(v == 'UD' for v, _ in vs): return 'UD', k, vs[0][1], 'sail'
        res = next(((v, w) for v, w in vs if v != 'UD'), vs[0])
    v, why = res
    return ('FETCH_OUT' if v == 'VALID' else v), n, why, 'sail'      # VALID on some continuation of all the bytes we have: the verdict needs bytes beyond the sequence; GAP: the model does not say #UD

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--sail-work', required=True); ap.add_argument('--sail-ir', required=True); ap.add_argument('--isla', required=True)
    ap.add_argument('--x86sys', required=True); ap.add_argument('--cpumodel', required=True)
    ap.add_argument('--sdmrows', required=True, help='the sdmrows stage output (proof/sdmfeat.py dumprows): the SDM opcode rows, their flag table and NOP flags')
    ap.add_argument('--model-src', required=True, help='the pinned sail-x86-from-acl2 model/ directory (default arms, bit fields)')
    ap.add_argument('--bad', action='append', default=[], help='an image BADDECODE (entry, address, 8 bytes): the sequences capstone refuses (f_sys.decode_gap)')
    ap.add_argument('--shapes', action='append', default=[], help='an image SHAPES file (first column the shape key, `<hex of the representative bytes>/...`; # comments): the decoded shapes of the image.  Those with no SDM row and no opcode-map entry (sdmfeat.match_form, the rule of fnvlift/cpufeat.decide) get a Sail verdict too')
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    from fnvlift import inputs
    inputs.configure(x86sys=os.path.abspath(a.x86sys), cpumodel=os.path.abspath(a.cpumodel))
    from proof import model_sail as M
    from fnvlift import f_sys
    M.configure(a.sail_work, a.sail_ir, a.isla)
    class B:
        def __init__(s, b): s.bytes = b
    import json
    model = os.path.abspath(a.model_src); record = json.load(open(a.x86sys)); defaults = default_opcodes(model); variants = sysvariants(record, model)
    from proof import sdmfeat
    from proof import cpufeat
    allrows, table, nopflags = sdmfeat.load_rows(a.sdmrows); cpu = cpufeat._cpu()
    def feature_ud(r):
        kd = f_sys.encoding(B(r))[0]
        return kd if kd in ('vex', 'evex', 'xop', 'map38', 'map3a', 'map0f') else sdm_feature(r, allrows, table, cpu, nopflags)
    def run(code, free, ctrs):
        return M.run_isla(code, sym=([free], False) if free else None, ctrs=ctrs)
    rows = {}
    for f in a.bad:
        for ln in open(f):
            if ln.startswith('#') or not ln.strip(): continue
            e, x, h = ln.split('\t')[:3]; rows.setdefault(h.strip(), []).append((os.path.basename(os.path.dirname(os.path.abspath(f))), x))
    from proof import shape
    md = shape.decoder()
    for f in a.shapes:
        idx = sdmfeat.index_rows(allrows)
        for ln in open(f):
            if ln.startswith('#') or not ln.strip(): continue
            raw = shape.rep_bytes(ln.split('\t')[0].strip()); ins = sdmfeat.decode(raw, md)
            if ins is None or bytes(ins.bytes) != raw: continue         # a sequence capstone refuses is BADDECODE's
            cand, _kind, mp = sdmfeat.match_form(ins, idx)
            if not cand and not mp: rows.setdefault(raw.hex(), []).append((os.path.basename(os.path.dirname(os.path.abspath(f))), '0'))
    if not a.bad and not a.shapes: ap.error('--bad or --shapes is required')
    res, red = [], 0
    for h in sorted(rows):
        raw = bytes.fromhex(h)
        v, k, why, cl = verdict(raw, run, M.split_blocks, feature_ud, defaults, variants)
        res.append((raw[:k].hex() if v == 'UD' else h, v, k, why, cl)); red += v != 'UD'
        print('%s %s extent %d class %s: %s' % (h, v, k, cl, why))
    seen = {}
    for r in res:
        if r[1] == 'UD': seen.setdefault(r[0], r)
    with open(a.out, 'w') as o:
        o.write(FORMAT + '; columns: decided bytes (the decode extent), verdict, extent, class (sail: rule b; feature: rule a, which still needs its call-graph certificate), evidence.  Rows of verdict UD only (proof/udsail.py)\n')
        for k in sorted(seen): o.write('%s\tUD\t%d\t%s\t%s\n' % (seen[k][0], seen[k][2], seen[k][4], seen[k][3]))
    print('refused sequences %d, #UD %d, not decided %d' % (len(res), len(res) - red, red))
    return 1 if red else 0

if __name__ == '__main__': sys.exit(main())
