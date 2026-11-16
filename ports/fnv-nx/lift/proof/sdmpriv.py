#!/usr/bin/env python3
"""The privileged instructions of the x86 ISA, read from the Intel SDM and from nothing else (OWNER 10, ASM-009).

    python3 -I sdmpriv.py SDM.txt OUT_ROWS.tsv       SDM.txt: `pdftotext -layout` of the combined-volumes SDM (its sha256 is in the header, the pages cited are those of that edition)

Rule (the class): an instruction section of the SDM instruction reference is privileged at CPL 3 when its `Protected Mode Exceptions` block has a `#GP(0)` entry with a sentence of one of the forms
  cpl0    `If [the] CPL is not 0 / > 0 / greater than 0 / != 0`, `If the current privilege level is not 0` (any `or`-disjunct of the sentence that is only that test: the sentence holds whenever CPL > 0)
  iopl    `If the CPL is greater than (has less privilege) the I/O privilege level`, `If CPL is greater than IOPL ...` (whether it holds is decided by the declared machine record x86sys.json: cpl from cs, iopl; sysspec.py reads it)
A sentence that conjoins another condition (`... and the PCE flag in CR4 is clear`, `If the TSD flag in CR4 is set and the CPL is greater than 0`) is `cond`: it holds only under a declared fact about that condition; its row says so, no more.
The mnemonics of a row are the instruction heading's names split on `/` (`INS/INSB/INSW/INSD`), lower case.  Sections of the same name (MOV to/from CR, MOV to/from DR) are told apart by the heading text after the dash.
Output: `heading<TAB>mnemonics<TAB>class<TAB>sentence<TAB>citation` (citation: SDM heading, volume and page of the section)."""
import hashlib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sdmfeat as S  # noqa: E402

CPL = r'(?:the )?(?:CPL|current privilege level)'
CPL0 = re.compile(r'^' + CPL + r'\s+(?:is not 0|is greater than 0|> ?0|[≠!]=? ?0)\.?$', re.I)
IOPL = re.compile(r'^' + CPL + r'\s+is greater than(?: \(has less privilege\))? (?:the I/O privilege level|IOPL)', re.I)
HEADMN = re.compile(r'^([A-Za-z0-9/]+)—(.*)$')


def gp0_sentences(lines, sec):
    """the sentences of the `#GP(0)` entries of the Protected Mode Exceptions block of a section"""
    blk = None; txt = []
    for l in lines[sec[1]:sec[2]]:
        m = S.MODEHEAD.match(l)
        if m:
            blk = m.group(1)
        elif blk and blk.startswith('Protected Mode'):
            txt.append(l.rstrip())
    ents = []; cur = None
    for l in txt:
        m = re.match(r'^(#[A-Z]{2}(?:\([^)]*\))?)\s+(.*)$', l.strip())
        if m:
            cur = [m.group(1), m.group(2)]; ents.append(cur)
        elif cur is not None and l.strip() and not S.PAGE.search(l) and not re.search(r'—.*Vol\.', l):
            cur[1] += ' ' + l.strip()
    out = []
    for name, body in ents:
        if name == '#GP(0)':
            out += [s.strip() for s in re.split(r'(?<=[\.\]])\s+(?=If\b|For\b|[A-Z])', body) if s.strip()]
    return out


def classify(sentence):
    s = re.sub(r'^If\s+', '', sentence.strip())
    if CPL0.match(s) or any(CPL0.match(d.strip()) for d in re.split(r'\s+or\s+', s)):
        return 'cpl0'
    if IOPL.match(s):
        return 'iopl'
    if re.search(r'\b(CPL|current privilege level)\b', s):
        return 'cond'
    return None


def rows(lines):
    out = []
    for sec in S.sections(lines):
        head = sec[0]; m = HEADMN.match(head)
        if not m:
            continue
        best = None
        for s in gp0_sentences(lines, sec):
            c = classify(s)
            if c in ('cpl0', 'iopl') and (best is None or best[0] == 'cond'):
                best = (c, s)
            elif c == 'cond' and best is None:
                best = (c, s)
        if best:
            out.append((head, ' '.join(n.lower() for n in m.group(1).split('/')), best[0], best[1], 'SDM %s %s section "%s"' % (m.group(1), S.page_after(lines, sec[1]), head)))
    return out


def main(argv):
    sdm, outp = argv
    sha = S.sha_file(sdm)
    rs = rows(S.load(sdm))
    with open(outp, 'w') as fh:
        fh.write('# fnvnx-sdm-priv-rows/1 sdm_sha256=%s rows=%d\n# source: Intel 64 and IA-32 Architectures SDM combined volumes (pdftotext -layout), the `Protected Mode Exceptions` block of every instruction section; rule: sdmpriv.py docstring\n# heading\tmnemonics\tclass\tsentence\tcitation\n' % (sha, len(rs)))
        for r in rs:
            fh.write('\t'.join(r) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
