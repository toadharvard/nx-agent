#!/usr/bin/env python3
"""x87sites.py OUT.tsv NAME=FACTSDIR ... : the `x87-masked-sites` fact (L3b x87 precondition, CERTS.md "x87 precondition facts").
For every fldcw / fldenv / frstor / fxrstor site (x87 control word) and every ldmxcsr / fxrstor site (MXCSR) of every image, the value the site loads and whether it keeps the overflow and underflow
exception masks set. Inputs are the per-image outputs of tools/fpstate.py scan (FACTSDIR/fpstate/fcw_loads.tsv, mxcsr_loads.tsv: the shared decoder, CFG and value-set walk of fpmodes.py), at the image's
assigned load base. A site's value tokens: `c:V` a constant, `t:A:O` a transform new = (current & A) | O of the control word at that time, `u:why` not decided.
Verdict per site (all tokens must hold):
  masked          every token is a constant with the masks set, or a transform that keeps them (the masks are in A, or in O), starting from the reachable set whose start word has them
                  (x87 FCW 0x027F: OM bit 3 and UM bit 4, mask 0x18; MXCSR 0x1F80: OM bit 10 and UM bit 11, mask 0x0C00; thread start state, ASM-219)
  unmasks         a constant or transform that can clear OE or UE
  undecided:WHY   a token `u:`; the site is listed with the reason class, not guessed, and goes to the orchestrator
Output: a header with the totals, then one row per site: image, address, instruction, entry, value tokens, verdict. Exit 1 when any site is `unmasks` or `undecided`."""
import sys, os, collections
FCW_M, MX_M = 0x18, 0x0C00
def verdict(toks, mask):
    why = []; bad = False
    for t in toks:
        k = t[0]
        if k == 'c':
            if int(t.split(':')[1], 16) & mask != mask: return 'unmasks'
        elif k == 't':
            _, a, o = t.split(':')
            if (int(a, 16) & mask) != mask and (int(o, 16) & mask) != mask: return 'unmasks'
        else: why.append(t.split(':', 1)[1].split(':')[0])
    return 'undecided:' + ','.join(sorted(set(why))) if why else 'masked'
def rows(name, d, fn, mask, cls):
    p = os.path.join(d, 'fpstate', fn)
    for l in open(p):
        f = l.rstrip('\n').split('\t')
        if len(f) < 4: continue
        yield name, f[0], f[1], f[2][6:], ' '.join(f[3:]), verdict(' '.join(f[3:]).split(), mask), cls
def main(out, specs):
    R = []
    for s in specs:
        n, d = s.split('=', 1)
        R += list(rows(n, d, 'fcw_loads.tsv', FCW_M, 'x87')) + list(rows(n, d, 'mxcsr_loads.tsv', MX_M, 'mxcsr'))
    c = collections.Counter((r[0], r[6], r[5].split(':')[0]) for r in R); tot = collections.Counter(r[5].split(':')[0] for r in R)
    with open(out, 'w') as f:
        f.write('# x87-masked-sites/1 (tools/x87sites.py over fpstate scans): image, address, instruction, entry, value tokens (c:V constant, t:A:O transform, u:why undecided), verdict; masks kept: x87 FCW 0x18 (OE, UE), MXCSR 0x0C00; start words FCW 0x027F, MXCSR 0x1F80 (thread start, ASM-219)\n')
        f.write('# totals: %d sites: %s\n' % (len(R), ', '.join('%s %d' % kv for kv in sorted(tot.items()))))
        for im in sorted({r[0] for r in R}):
            f.write('# %s: %s\n' % (im, ', '.join('%s/%s %d' % (k[1], k[2], v) for k, v in sorted(c.items()) if k[0] == im)))
        for r in sorted(R, key=lambda r: (r[0], int(r[1], 16))): f.write('\t'.join(r[:6]) + '\n')
    print('x87-masked-sites: %d sites: %s' % (len(R), ', '.join('%s %d' % kv for kv in sorted(tot.items()))))
    for im in sorted({r[0] for r in R}): print('  %s: %s' % (im, ', '.join('%s/%s %d' % (k[1], k[2], v) for k, v in sorted(c.items()) if k[0] == im)))
    return 1 if tot['unmasks'] or sum(v for k, v in tot.items() if k.startswith('undecided')) else 0
if __name__ == '__main__': sys.exit(main(sys.argv[1], sys.argv[2:]))
