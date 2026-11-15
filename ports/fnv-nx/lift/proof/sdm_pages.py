#!/usr/bin/env python3
"""Page table of the citations of proof/x87spec.py in the pinned Intel SDM.
    python3 -I sdm_pages.py SDM.txt [x87spec.py] > sdm_pages.tsv
SDM.txt is `pdftotext -layout` of the pinned PDF (fnv-nx-build tools/intel-sdm commit 0768b0baf29a625871084414338c12f65d0f2cc8, PDF sha256 a4a62e6a7ba11a76c7753a195b825087306812aac39b930973f9168ee599f321; its MANIFEST.json holds edition, order numbers and sha256). A citation key is
`SDM <vol>:<id>`: id = a section number (8.5.1.1), a table (T8-6), or an instruction page (FADD, F2XM1). The tool resolves every distinct key that the spec's text carries
to the printed page of its heading ("Vol. 1 8-19", "Vol. 2A 3-123"; `~` = the footer of the neighbouring page) and the PDF page index (1-based form-feed count). The table lists what it could not find as `?`: such a key is a
defect of the citation, not of the tool. Rules: a section heading is a line that starts with the number followed by a capitalised title, the last match after the table of contents;
a table heading is a line `Table <n>-<m>. ` or `Table <n>-<m> Title`, last match; an instruction page is the first line after the table of contents that starts with the mnemonic or a slash
list containing it, followed by an em dash (the title of the instruction pages)."""
import re, sys

def pages(txt): return txt.split('\f')

def printed(pgs, i):
    """the footer of the page; a page without one (the layout dropped it) is numbered from the footer of a neighbouring page (next, else previous), marked '~'"""
    for d in (0, 1, -1):
        if 0 <= i + d < len(pgs):
            m = re.findall(r'Vol\. (1|2A|2B|2C|2D|3A|3B|3C|3D|4) (\d+)-(\d+)', pgs[i + d])
            if m:
                v, ch, n = m[-1]
                return ('' if d == 0 else '~') + 'Vol. %s %s-%d' % (v, ch, int(n) - d)
    return '?'

def resolve(txt, vol, ident):
    pg = pages(txt)
    pat = None
    if re.fullmatch(r'T\d+-\d+', ident):
        n, m = ident[1:].split('-')
        pat = re.compile(r'^\s*Table %s-%s(?:\.|\s+[A-Z])' % (n, m), re.M)
    elif re.fullmatch(r'\d+(\.\d+)*', ident):
        pat = re.compile(r'^\s*%s\s+\S' % re.escape(ident), re.M)
    elif re.fullmatch(r'[A-Za-z0-9]+', ident):
        pat = re.compile(r'^\s*(?:[A-Za-z0-9]+/ ?)*%s(?:/ ?[A-Za-z0-9]+)*\s*[—-]' % ident, re.M)
    if pat is None: return None
    hits = [i for i, p in enumerate(pg) if any(pat.search(l) and '. . .' not in l for l in p.split('\n'))]    # a line with dot leaders is the table of contents
    if not hits: return None
    i = hits[0] if re.fullmatch(r'[A-Za-z0-9]+', ident) and not re.fullmatch(r'\d+(\.\d+)*', ident) else hits[-1]
    return i + 1, printed(pg, i)

def main(argv):
    txt = open(argv[1], encoding='utf-8', errors='replace').read()
    spec = open(argv[2] if len(argv) > 2 else __file__.replace('sdm_pages.py', 'x87spec.py'), encoding='utf-8').read()
    keys = sorted(set(re.findall(r'SDM (1|2A|2B|2C|2D|3A):(T\d+-\d+|\d+(?:\.\d+)*|[A-Za-z][A-Za-z0-9]*)', spec)), key=lambda k: (k[0], k[1]))
    print('key\tpdf_page\tprinted_page')
    for vol, ident in keys:
        r = resolve(txt, vol, ident)
        print('SDM %s:%s\t%s\t%s' % (vol, ident, *(r if r else ('?', '?'))))

if __name__ == '__main__':
    main(sys.argv)
