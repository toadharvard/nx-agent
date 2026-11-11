"""sm_hand.py - hand encoder: D3D9 shader assembly subset -> SM1-3 token stream (independent of mojoshader_assembler).
Used by sm_census.py when s6b_asm cannot produce a case, and to cross-check s6b_asm on the cases it can. Not a general assembler:
it accepts exactly the syntax sm_cases.py emits."""
import re, struct

SRCMODS = {"": 0, "neg": 1, "bias": 2, "biasneg": 3, "sign": 4, "signneg": 5, "comp": 6, "x2": 7, "x2neg": 8, "dz": 9,
           "dw": 10, "abs": 11, "absneg": 12, "not": 13}
USAGES = ["position", "blendweight", "blendindices", "normal", "psize", "texcoord", "tangent", "binormal", "tessfactor",
          "positiont", "color", "fog", "depth", "sample"]
TEXTYPE = {"2d": 2, "cube": 3, "volume": 4, "1d": 1}
CMP = {"gt": 1, "eq": 2, "ge": 3, "lt": 4, "ne": 5, "le": 6}
SHIFT = {"x2": 1, "x4": 2, "x8": 3, "d8": 13, "d4": 14, "d2": 15}
RES = {"sat": 1, "pp": 2, "centroid": 4}
NODST = {"NOP", "RET", "ELSE", "ENDIF", "ENDLOOP", "ENDREP", "BREAK", "CALL", "CALLNZ", "LABEL", "LOOP", "REP", "IF", "IFC",
         "BREAKC", "BREAKP", "PHASE"}
ALIAS = {"tex": "TEXLD", "texcoord": "TEXCRD", "texcrd": "TEXCRD", "texld": "TEXLD", "texldp": "TEXLD", "texldb": "TEXLD",
         "if": "IF", "break": "BREAK"}

def load_opcodes(hdr):
    """opcode number = row index in mojoshader_internal.h's INSTRUCTION table (the table is indexed by the token)."""
    names = []
    for ln in open(hdr):
        m = re.match(r"INSTRUCTION(?:_STATE)?\((\w+),", ln)
        if m: names.append(m[1])
    return {n: i for i, n in enumerate(names) if n != "RESERVED"} | {"PHASE": 0xFFFD}

def regspec(t):
    """register text (no modifiers) -> (type, num, rel_token or None, swizzle)"""
    m = re.fullmatch(r"(\w+?)(?:\[(.*)\])?(?:\.(\w+))?", t)
    base, rel, sw = m[1], m[2], m[3]
    named = {"oPos": (4, 0), "oFog": (4, 1), "oPts": (4, 2), "oDepth": (9, 0), "aL": (15, 0), "vPos": (17, 0), "vFace": (17, 1)}
    if base in named: ty, n = named[base]
    else:
        mm = re.fullmatch(r"(oD|oT|oC|[rvctasbiplo])(\d*)", base)
        if not mm: raise ValueError("register " + t)
        k, n = mm[1], int(mm[2] or 0)
        ty = {"r": 0, "v": 1, "c": 2, "a": 3, "t": 3, "oD": 5, "oT": 6, "o": 6, "oC": 8, "s": 10, "b": 14, "i": 7, "l": 18,
              "p": 19}[k]
    rt = None
    if rel is not None:
        r = re.fullmatch(r"\s*(a0|aL)(?:\.(\w))?\s*(?:\+\s*(\d+))?\s*", rel)
        if not r: raise ValueError("relative " + t)
        n = int(r[3] or 0)
        if r[1] == "aL": rt = 0x80000000 | (7 << 28) | (1 << 11) | (0xE4 << 16)
        else: rt = 0x80000000 | (3 << 28) | ("xyzw".index(r[2] or "x") * 0x55 << 16)
    return ty, n, rt, sw

major_of = [0]

def tybits(ty): return ((ty & 7) << 28) | (((ty >> 3) & 3) << 11)

def swz(s):
    s = s or "xyzw"
    s = (s + s[-1] * 4)[:4]
    return sum("xyzw".index(c) << (2 * i) for i, c in enumerate(s))

def src(t):
    t = t.strip(); mod = ""
    neg = t.startswith("-"); t = t.lstrip("-")
    if t.startswith("!"): mod, t = "not", t[1:]
    elif t.startswith("1-"): mod, t = "comp", t[2:]
    ab = t.startswith("|")
    if ab: t = t.strip("|")
    if t.endswith("_abs"): ab, t = True, t[:-4]
    m = re.fullmatch(r"(.*?)_(bias|bx2|x2|dz|dw)", t)
    if m: t, sfx = m.groups(); mod = {"bias": "biasneg" if neg else "bias", "bx2": "signneg" if neg else "sign",
                                     "x2": "x2neg" if neg else "x2", "dz": "dz", "dw": "dw"}[sfx]; neg = False
    if ab: mod = "absneg" if neg else "abs"
    elif neg and not mod: mod = "neg"
    ty, n, rt, sw = regspec(t)
    tok = 0x80000000 | tybits(ty) | n | (1 << 13 if rt else 0) | (swz(sw) << 16) | (SRCMODS[mod] << 24)
    return [tok] + ([rt] if rt and major_of[0] >= 2 else [])

def dst(t, resmod=0, shift=0):
    ty, n, rt, sw = regspec(t)
    mask = 0xF if not sw else sum(1 << "xyzw".index(c) for c in sw)
    return [0x80000000 | tybits(ty) | n | (1 << 13 if rt else 0) | (mask << 16) | (resmod << 20) | (shift << 24)] + ([rt] if rt and major_of[0] >= 2 else [])

def assemble(text, ops):
    toks = []; major = minor = 0
    for ln in text.splitlines():
        ln = ln.split("//")[0].strip()
        if not ln: continue
        m = re.fullmatch(r"(vs|ps)_(\d)_(\w)", ln)
        if m:
            major_of[0] = int(m[2]); major, minor = int(m[2]), (1 if m[3] == "x" else int(m[3]))
            toks.append((0xFFFE if m[1] == "vs" else 0xFFFF) << 16 | major << 8 | minor); continue
        pred = None
        mp = re.match(r"\((!?p0(?:\.\w)?)\)\s*", ln)
        if mp: pred, ln = mp[1], ln[mp.end():]
        w, _, rest = ln.partition(" ")
        args = [a.strip() for a in re.split(r",(?![^\[]*\])", rest)] if rest.strip() else []
        parts = w.split("_"); op = parts[0]; sfx = parts[1:]
        if op == "phase": toks.append(0xFFFD); continue
        if op == "dcl":
            rm = sum(RES[x] for x in sfx if x in RES); sfx = [x for x in sfx if x not in RES]; t = 0
            if sfx and sfx[0] in TEXTYPE: t = TEXTYPE[sfx[0]] << 27
            elif sfx:
                u = re.fullmatch(r"([a-z]+)(\d*)", sfx[0])
                t = USAGES.index(u[1]) | int(u[2] or 0) << 16
            toks += [31 | (2 << 24 if major >= 2 else 0), 0x80000000 | t] + dst(args[0], rm); continue
        if op in ("def", "defi", "defb"):
            if op == "def": v = [struct.unpack("<I", struct.pack("<f", float(x)))[0] for x in args[1:]]
            elif op == "defi": v = [int(x) & 0xFFFFFFFF for x in args[1:]]
            else: v = [1 if args[1] == "true" else 0]
            d0 = dst(args[0]); toks += [ops[op.upper()] | ((len(d0) + len(v)) << 24 if major >= 2 else 0)] + d0 + v; continue
        ctrl = 0; res = 0; sh = 0
        for x in sfx:
            if x in CMP: ctrl = CMP[x]
            elif x in RES: res |= RES[x]
            elif x in SHIFT: sh = SHIFT[x]
        name = op
        if op in ("if", "break") and sfx and sfx[0] in CMP: name = "IFC" if op == "if" else "BREAKC"
        else: name = ALIAS.get(op, op.upper())
        if op == "texldp": ctrl = 1
        if op == "texldb": ctrl = 2
        body = []
        a = list(args)
        if name not in NODST and a: body += dst(a.pop(0), res, sh)
        if pred: body += [0x80000000 | tybits(19) | (swz(pred.lstrip("!").partition(".")[2]) << 16) | ((13 if pred.startswith("!") else 0) << 24)]
        for s in a: body += src(s)
        n = len(body)
        toks.append(ops[name] | (ctrl << 16) | ((n << 24) if major >= 2 else 0) | (0x10000000 if pred else 0))
        toks += body
    toks.append(0xFFFF)
    return b"".join(struct.pack("<I", t & 0xFFFFFFFF) for t in toks)

def strip_comments(b):
    """drop comment blocks (opcode 0xFFFE after the version token) so assembler output compares with hand output"""
    w = struct.unpack("<%dI" % (len(b) // 4), b); o = [w[0]]; i = 1
    while i < len(w):
        if (w[i] & 0xFFFF) == 0xFFFE: i += 1 + ((w[i] >> 16) & 0x7FFF)
        else: o.append(w[i]); i += 1
    return struct.pack("<%dI" % len(o), *o)
