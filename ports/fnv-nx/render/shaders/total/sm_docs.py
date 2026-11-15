"""sm_docs.py - extract SM1-3 facts from the Microsoft D3D9 asm reference pages: the markdown sources of MicrosoftDocs/win32
desktop-src/direct3dhlsl (dx9-graphics-reference-asm-*), the pinned external `msdocs-win32-d3d9` (fnv-nx-build). The reader turns a
page into one text form (a table cell per line) that the extractors below read; nothing is fetched from the network."""
import os, re
VSV = ["vs_1_1", "vs_2_0", "vs_2_x", "vs_2_sw", "vs_3_0", "vs_3_sw"]
PSV = ["ps_1_1", "ps_1_2", "ps_1_3", "ps_1_4", "ps_2_0", "ps_2_x", "ps_2_sw", "ps_3_0", "ps_3_sw"]
INDEX = ["ps-1-x", "ps-2-0", "ps-2-x", "ps-3-0", "vs-1-1", "vs-2-0", "vs-2-x", "vs-3-0"]

def _md(t):
    """markdown source of one page -> text, a table cell per line (each cell closed by ' | '), prose lines as they are"""
    t = re.sub(r'\A---\n.*?\n---\n', '', t, flags=re.S)
    out = []
    for ln in t.split("\n"):
        s = ln.strip()
        if s.startswith("```") or re.fullmatch(r'\|[-| :]+\|', s) or s in ("<dl> <dt>", "</dt> </dl>", "<dl>", "</dl>", "<dt>", "</dt>", "<dd>", "</dd>"): continue
        s = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', s)
        s = re.sub(r'</?[a-zA-Z][^>]*>', '', s)
        s = s.replace("\\_", "_").replace("**", "").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        if s.startswith("|"):
            out += [c.strip() + " | " for c in s.strip("|").split("|")]
        else:
            s = re.sub(r'\A#+\s*', '', s); s = re.sub(r'\A-\s+', '', s)
            if s: out.append(s)
    return "\n".join(out)

def get(page, cache):
    """text of one page of the pinned docs tree `cache` (the direct3dhlsl directory, or the mirror root)"""
    f = os.path.join(cache, page + ".md")
    if not os.path.exists(f): f = os.path.join(cache, "desktop-src", "direct3dhlsl", page + ".md")
    return "Feedback\n" + _md(open(f, encoding="utf8").read()) + "\nWas this page helpful"

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
