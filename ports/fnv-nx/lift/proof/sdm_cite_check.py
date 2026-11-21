#!/usr/bin/env python3
"""Mechanical quote check of proof/x87spec.py against the pinned Intel SDM text.
    python3 -I sdm_cite_check.py SDM.txt [x87spec.py] [sdm_pages.tsv]
Every quoted fragment of the spec (text in '...' or "..." of at least 15 characters that has a letter) is attributed to the nearest preceding citation key
`SDM <vol>:<id>` within 400 characters and 3 lines, and must occur (whitespace, dashes and quote marks normalised; '...' splits it into segments that must follow each
other in order) on the key's PDF page or one of the next two pages of sdm_pages.tsv.  Output: one line per quote, `OK|MISS|NOKEY  line  key  pdf  quote`.
Exit 1 if any quote is MISS or NOKEY.  A quote is a statement of the spec's source; a MISS is a defect of the spec's citation (rule 8), never of the tool."""
import re, sys

def norm(t):
    t = t.replace('−', '-').replace('–', '-').replace('—', '-').replace('’', "'").replace('“', '"').replace('”', '"').replace('∗', '*')
    t = re.sub(r'-\s*\n\s*', '-', t)             # a hyphenated line break (the SDM wraps "instruc-\\ntions")
    return re.sub(r'\s+', ' ', t).strip()

def main(a):
    pg = open(a[1], encoding='utf-8', errors='replace').read().split('\f')
    src = open(a[2] if len(a) > 2 else 'x87spec.py', encoding='utf-8').read()
    tab = {}
    for l in open(a[3] if len(a) > 3 else 'sdm_pages.tsv', encoding='utf-8').read().split('\n')[1:]:
        f = l.split('\t')
        if len(f) >= 2 and f[1].isdigit(): tab[f[0]] = int(f[1])
    keys = [(m.start(), m.group(0)) for m in re.finditer(r'SDM (?:1|2A|2B|2C|2D|3A):(?:T\d+-\d+|\d+(?:\.\d+)*|[A-Za-z][A-Za-z0-9]*)', src)]
    bad = 0
    plain = re.sub(r'"""', '\x00\x00\x00', src)                  # a docstring delimiter is not a quote of the SDM
    for m in re.finditer(r"(?<![A-Za-z0-9_)\]])'([^'\n]{15,}?)'(?![A-Za-z0-9_])|(?<![A-Za-z0-9_)\]\x00])\"([^\"\n\x00]{15,}?)\"(?![A-Za-z0-9_\x00])", plain):
        q = m.group(1) or m.group(2)
        if not re.search(r'[A-Za-z]{4}', q): continue
        if re.match(r'\s*(add\(|nd_|CLEARED_UD|\("[0-9a-f]{2}")', plain[plain.rfind('\n', 0, m.start()) + 1:]): continue     # a string of the code (a mnemonic, a name), not a quote
        prev = [k for k in keys if k[0] < m.start() and 0 < m.start() - k[0] < 400 and src.count('\n', k[0], m.start()) <= 3]
        ln = src.count('\n', 0, m.start()) + 1
        if not prev: print('NOKEY\t%d\t-\t-\t%s' % (ln, q)); bad += 1; continue
        # a quote is checked against every key of the cited window (the nearest first); OK if any page holds it
        segs = [norm(x) for x in re.split(r'\.\.\.|…', q) if len(norm(x)) > 3]
        ok = None
        for _, key in reversed(prev[-4:]):
            p = tab.get(key)
            if p is None: continue
            txt = norm(' '.join(pg[p - 1:p + 2]))
            pos = 0; good = True
            for sg in segs:
                j = txt.find(sg, pos)
                if j < 0: good = False; break
                pos = j + len(sg)
            if good: ok = (key, p); break
        if ok: print('OK\t%d\t%s\t%d\t%s' % (ln, ok[0], ok[1], q))
        else: print('MISS\t%d\t%s\t%s\t%s' % (ln, prev[-1][1], tab.get(prev[-1][1]), q)); bad += 1
    sys.exit(1 if bad else 0)

if __name__ == '__main__': main(sys.argv)
