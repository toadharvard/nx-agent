"""sm_docs.py - extract SM1-3 facts from the Microsoft D3D9 asm reference pages (learn.microsoft.com direct3dhlsl,
dx9-graphics-reference-asm-*).  Pages are fetched by fetch() into a cache dir (text with one table cell per line)."""
import html, os, re, urllib.request
BASE = "https://learn.microsoft.com/en-us/windows/win32/direct3dhlsl/"
VSV = ["vs_1_1", "vs_2_0", "vs_2_x", "vs_2_sw", "vs_3_0", "vs_3_sw"]
PSV = ["ps_1_1", "ps_1_2", "ps_1_3", "ps_1_4", "ps_2_0", "ps_2_x", "ps_2_sw", "ps_3_0", "ps_3_sw"]
INDEX = ["ps-1-x", "ps-2-0", "ps-2-x", "ps-3-0", "vs-1-1", "vs-2-0", "vs-2-x", "vs-3-0"]

def _txt(t):
    t = re.sub(r'(?s)<(script|style|nav|header|footer).*?</\1>', '', t)
    t = re.sub(r'(?s)<tr[^>]*>', '\n', t); t = re.sub(r'</t[dh]>', ' | ', t)
    t = re.sub(r'<(br|p|li|h\d)[^>]*>', '\n', t); t = re.sub(r'<[^>]+>', '', t)
    return re.sub(r'\n\s*\n+', '\n', html.unescape(t))

def get(page, cache):
    f = os.path.join(cache, page + ".txt")
    if not os.path.exists(f):
        os.makedirs(cache, exist_ok=True)
        t = urllib.request.urlopen(BASE + page, timeout=30).read().decode("utf8", "replace")
        open(f, "w").write(_txt(t))
    t = open(f).read()
    i = t.find("Feedback"); t = t[i + 8:]
    return t[:t.find("Was this page helpful")].replace("Summarize this article for me", "")

def fetch_all(cache):
    """crawl the asm reference: section index -> instruction index pages -> every instruction page; returns page list"""
    pages = ["dx9-graphics-reference-asm-" + x for x in ["software-shaders", "ps-differences", "vs-differences"] + INDEX]
    todo = []
    for ix in INDEX:
        stage = ix[:2]
        for tail in ("instructions-" + ix, "registers-" + ix if stage == "vs" else "registers-" + ix):
            todo.append("dx9-graphics-reference-asm-%s-%s" % (stage, tail))
    todo += ["dx9-graphics-reference-asm-%s-instructions-flow-control" % s for s in ("vs", "ps")]
    for p in todo + pages:
        try: get(p, cache)
        except Exception: pass
    names = set()
    for p in todo:
        f = os.path.join(cache, p + ".raw")
        try: raw = urllib.request.urlopen(BASE + p, timeout=30).read().decode("utf8", "replace")
        except Exception: continue
        names |= set(re.findall(r'href="([a-z0-9][a-z0-9-]*---[a-z0-9-]+)"', raw))
    for n in sorted(names):
        try: get(n, cache)
        except Exception: pass
    return sorted(names)

def version_table(page, cache):
    """-> {row_label: [models with x]} from the 'Pixel/Vertex shader versions' table of an instruction page"""
    t = get(page, cache)
    m = re.search(r'(Pixel|Vertex) shader versions \|\s*\n', t)
    if not m: return None
    ln = [x.strip() for x in t[m.end():].split("\n")]
    cols = PSV if m.group(1) == "Pixel" else VSV
    hdr = [c.rstrip(" |").strip() for c in ln[:len(cols)]]
    assert hdr == [c[3:] for c in cols], (page, hdr)
    out, i = {}, len(cols)
    while i + len(cols) < len(ln) + 1:
        lab = ln[i].rstrip(" |").strip()
        cells = [c.rstrip(" |").strip().lower() for c in ln[i + 1:i + 1 + len(cols)]]
        if not lab or len(cells) < len(cols) or any(c not in ("", "x") for c in cells): break
        out[lab] = [cols[k] for k, c in enumerate(cells) if c == "x"]
        i += 1 + len(cols)
    return out

def grid(page, cache, ncols):
    """generic 'cell per line' table after its header: returns rows (list of cell lists) of ncols cells each"""
    ln = [x.rstrip(" |").strip() for x in get(page, cache).split("\n")]
    return ln
