"""The privileged-mnemonic set: read from sdm_priv_rows.tsv, the rows sdmpriv.py extracts from the pinned Intel SDM (rule: sdmpriv.py docstring; header: the SDM sha256).
A mnemonic is privileged at CPL 3 under the declared facts (declared.json: cpl 3, iopl 0, io_bitmap_grants none) when a row of class cpl0 or iopl names it.  A `cond` row (a #GP(0) that needs another condition, e.g. CR4.PCE clear for RDPMC) is not in the set
until declared.json states that condition as a fact.  `mov` is the operand-qualified MOV to/from CRn/DRn, not a mnemonic of the set (its two rows are told apart by heading)."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROWS = os.path.join(HERE, 'sdm_priv_rows.tsv')
OPERAND_QUALIFIED = ('mov',)


def rows(path=ROWS):
    out = []
    with open(path) as fh:
        head = fh.readline()
        assert head.startswith('# fnvnx-sdm-priv-rows/1 sdm_sha256='), head
        for ln in fh:
            if ln.strip() and ln[0] != '#':
                h, mn, cls, sent, cite = ln.rstrip('\n').split('\t')
                out.append({'heading': h, 'mnemonics': mn.split(), 'class': cls, 'sentence': sent, 'citation': cite})
    return out


def citations(path=ROWS):
    """{mnemonic: (class, citation, sentence)} of the privileged set"""
    return {m: (r['class'], r['citation'], r['sentence']) for r in rows(path) if r['class'] in ('cpl0', 'iopl') for m in r['mnemonics'] if m not in OPERAND_QUALIFIED}


def mnemonics(path=ROWS):
    return tuple(sorted(citations(path)))
