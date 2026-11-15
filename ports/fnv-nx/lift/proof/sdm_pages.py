#!/usr/bin/env python3
"""Page table of the citations of proof/x87spec.py in the pinned Intel SDM.
    python3 -I sdm_pages.py SDM.txt [x87spec.py] > sdm_pages.tsv
    python3 -I sdm_pages.py --cites SDM.txt sdm_oracle_req.tsv > tests/sdm_oracle_cites.txt     (the oracle cites: the SDM text of each statement a test relies on, see cites())
SDM.txt is `pdftotext -layout` of the pinned PDF (fnv-nx-build tools/intel-sdm commit 0768b0baf29a625871084414338c12f65d0f2cc8, PDF sha256 a4a62e6a7ba11a76c7753a195b825087306812aac39b930973f9168ee599f321; its MANIFEST.json holds edition, order numbers and sha256). A citation key is
`SDM <vol>:<id>`: id = a section number (8.5.1.1), a table (T8-6), or an instruction page (FADD, F2XM1). The search for a key is limited to the pages of its volume (page_volume/volume_range: the pages whose own footer names the volume; a key never resolves into another volume). The tool resolves every distinct key that the spec's text carries
to the printed page of its heading ("Vol. 1 8-19", "Vol. 2A 3-123") and the PDF page index (1-based form-feed count). The pdf_page column is the key (exact: the 1-based page index of the PDF, equal to its /PageLabels); printed_page is read from that page's own text and is empty where the page has none. A key not found is `?`: a
defect of the citation, not of the tool. Rules: a section heading is a line that starts with the number followed by a capitalised title, the last match after the table of contents;
a table heading is a line `Table <n>-<m>. ` or `Table <n>-<m> Title`, last match; an instruction page is the first line after the table of contents that starts with the mnemonic or a slash
list containing it, followed by an em dash (the title of the instruction pages)."""
import re, sys

def pages(txt): return txt.split('\f')

def printed(pgs, i):
    """the printed page read from the page's own footer text ("Vol. 2A 3-123"); empty when the page's text has none (never inferred from another page)"""
    m = re.findall(r'Vol\. (1|2A|2B|2C|2D|3A|3B|3C|3D|4) (\d+-\d+)', pgs[i])
    return 'Vol. %s %s' % m[-1] if m else ''


def page_volume(p):
    """the volume named by the page's own footer (its last or second-to-last non-empty line: "FPREM—Partial Remainder    Vol. 2A 3-366" or "9-8 Vol. 1"), else None"""
    ls = [l.strip() for l in p.split('\n') if l.strip()]
    for l in ls[-2:]:
        m = re.search(r'Vol\. (1|2A|2B|2C|2D|3A|3B|3C|3D|4) \d+-\d+$', l) or re.match(r'\d+-\d+ Vol\. (1|2A|2B|2C|2D|3A|3B|3C|3D|4)$', l)
        if m: return m.group(1)
    return None


def volume_range(pg, vol):
    """(first, last) 0-based page indices of volume `vol`: the largest cluster of pages whose footer names it, a cluster being broken by a gap of more than 50
    pages (a cross-reference line can end a page of another volume and must not stretch the range)"""
    h = [i for i, p in enumerate(pg) if page_volume(p) == vol]
    if not h: return None
    cl, cur = [], [h[0]]
    for i in h[1:]:
        if i - cur[-1] > 50: cl.append(cur); cur = []
        cur.append(i)
    cl.append(cur)
    best = max(cl, key=len)
    return best[0], best[-1]


def resolve(txt, vol, ident):
    pg = pages(txt)
    rng = volume_range(pg, vol)
    if rng is None: return None
    pat = None
    if re.fullmatch(r'P\d+', ident):          # SDM <vol>:P<n>: the PDF page n itself (an Operation section spans pages the instruction-page key does not reach); it must be a page of the volume
        n = int(ident[1:])
        return (n, printed(pg, n - 1)) if 1 <= n <= len(pg) and rng[0] <= n - 1 <= rng[1] else None
    if re.fullmatch(r'T\d+-\d+', ident):
        n, m = ident[1:].split('-')
        pat = re.compile(r'^\s*Table %s-%s(?:\.|\s+[A-Z])' % (n, m), re.M)
    elif re.fullmatch(r'\d+(\.\d+)*', ident):
        pat = re.compile(r'^\s*%s\s+\S' % re.escape(ident), re.M)
    elif re.fullmatch(r'[A-Za-z0-9]+', ident):
        pat = re.compile(r'^\s*(?:[A-Za-z0-9]+/ ?)*%s(?:/ ?[A-Za-z0-9]+)*\s*[—-]' % ident, re.M)
    if pat is None: return None
    hits = [i for i, p in enumerate(pg) if rng[0] <= i <= rng[1] and any(pat.search(l) and '. . .' not in l for l in p.split('\n'))]    # a line with dot leaders is the table of contents
    if not hits: return None
    i = hits[0] if re.fullmatch(r'[A-Za-z0-9]+', ident) and not re.fullmatch(r'\d+(\.\d+)*', ident) else hits[-1]
    return i + 1, printed(pg, i)


def _norm(t):
    t = t.replace('\u2212', '-').replace('\u2013', '-').replace('\u2014', '-').replace('\u2019', "'").replace('\u201c', '"').replace('\u201d', '"').replace('\u2217', '*')
    t = re.sub(r'-\s*\n\s*', '-', t)
    return re.sub(r'\s+', ' ', t).strip()


def _footer(p):
    """the printed page of a page whose footer is `3-16 Vol. 1` (volume 1 and some 3x pages print the number first)"""
    m = re.findall(r'^\s*(\d+-\d+) Vol\. (1|2A|2B|2C|2D|3A|3B|3C|3D|4)\s*$', p, re.M)
    return 'Vol. %s %s' % (m[-1][1], m[-1][0]) if m else ''


def extract(txt, vol, ident, start, end):
    """the text of the pinned SDM from the first `start` to the next `end` (both fragments of the page text, compared with whitespace and dashes normalised), searched on the page of key `SDM vol:ident` and the two pages after it (an Operation
    section spans pages).  -> (pdf_page, printed_page, text) with the text as the PDF prints it, normalised; None when the key, start or end is not there.  The text is never typed: it is the slice of the extracted PDF text."""
    pg = pages(txt); r = resolve(txt, vol, ident)
    if r is None: return None
    for i in range(r[0] - 1, min(r[0] + 2, len(pg))):
        t = _norm(' '.join(pg[i:i + 2])); a = t.find(_norm(start))
        if a < 0: continue
        b = t.find(_norm(end), a)
        if b < 0: return None
        return i + 1, printed(pg, i) or _footer(pg[i]), t[a:b + len(_norm(end))]
    return None


def cites(txt, req):
    """the oracle cites file of the specs whose expected values are SDM statements (CERTS.md "Oracle references").  `req` is the text of sdm_oracle_req.tsv: a line `## name` or `# comment` is copied; any other line is
    `SDM <vol>:<id><TAB>start<TAB>end`.  Output line: `SDM <vol>:<id> | <printed page> | pdf <n> | <text>`.  A request that does not resolve is `MISSING` in the output (and main exits 1): a defect of the request, never of the tool."""
    out = []; bad = 0
    for l in req.split('\n'):
        if not l.strip() or l.startswith('#'): out.append(l) if l.strip() else None; continue
        key, start, end = (l.split('\t') + ['', ''])[:3]
        m = re.fullmatch(r'SDM (1|2A|2B|2C|2D|3A|3B|3C|3D):(\S+)', key)
        r = extract(txt, m.group(1), m.group(2), start, end or start) if m else None
        if r is None: out.append('MISSING | %s | %s' % (key, start)); bad += 1
        else: out.append('%s | %s | pdf %d | %s' % (key, r[1], r[0], r[2]))
    return '\n'.join(out) + '\n', bad

def main(argv):
    if argv[1] == '--cites':          # python3 -I sdm_pages.py --cites SDM.txt tests/sdm_oracle_req.tsv > tests/sdm_oracle_cites.txt
        o, bad = cites(open(argv[2], encoding='utf-8', errors='replace').read(), open(argv[3], encoding='utf-8').read()); sys.stdout.write(o); sys.exit(1 if bad else 0)
    txt = open(argv[1], encoding='utf-8', errors='replace').read()
    spec = open(argv[2] if len(argv) > 2 else __file__.replace('sdm_pages.py', 'x87spec.py'), encoding='utf-8').read()
    keys = sorted(set(re.findall(r'SDM (1|2A|2B|2C|2D|3A):(T\d+-\d+|\d+(?:\.\d+)*|[A-Za-z][A-Za-z0-9]*)', spec)), key=lambda k: (k[0], k[1]))
    print('key\tpdf_page\tprinted_page')
    for vol, ident in keys:
        r = resolve(txt, vol, ident)
        print('SDM %s:%s\t%s\t%s' % (vol, ident, *(r if r else ('?', '?'))))

if __name__ == '__main__':
    main(sys.argv)
