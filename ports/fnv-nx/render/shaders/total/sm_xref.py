"""sm_xref.py - READ the opcode / register tables of two independent implementations as reference facts (nothing is copied):
wine  dlls/wined3d/shader_sm1.c (opcode min/max versions, dst/src counts), dlls/d3dcompiler_43/asmparser.c (register tables)
vkd3d libs/vkd3d-shader/d3dbc.c (opcode min/max versions, dst/src counts).  Parsed with regexes at generation time."""
import os, re
MODELS = ["vs_1_1", "vs_2_0", "vs_2_x", "vs_3_0", "ps_1_1", "ps_1_2", "ps_1_3", "ps_1_4", "ps_2_0", "ps_2_x", "ps_3_0"]
def vtuple(m): return (int(m[3]), 1 if m[5] == "x" else int(m[5]))
def in_range(m, lo, hi):
    v = vtuple(m); return (lo is None or v >= lo) and (hi is None or v <= hi)

def _enum(src, prefix):
    return {m.group(1): int(m.group(2), 16) for m in re.finditer(prefix + r"OP_(\w+)\s*=\s*(0x[0-9a-fA-F]+)", src)}

def wine_ops(root):
    """-> {(stage, opcode_number): [(ndst, nsrc, lo, hi)]}"""
    s = open(os.path.join(root, "dlls/wined3d/shader_sm1.c")).read()
    en = _enum(s, "WINED3D_SM1_")
    out = {}
    for st, tab in (("vs", "vs_opcode_table"), ("ps", "ps_opcode_table")):
        body = s[s.index(tab + "[]"):]; body = body[:body.index("};")]
        for m in re.finditer(r"\{WINED3D_SM1_OP_(\w+),\s*(\d+),\s*(\d+),\s*\w+,\s*(WINED3D_SHADER_VERSION\(\d,\s*\d\)|0),\s*(WINED3D_SHADER_VERSION\(\d,\s*\d\)|-1|0)\s*\}", body):
            def ver(x):
                x = x.strip(); k = re.match(r"WINED3D_SHADER_VERSION\((\d),\s*(\d)\)", x)
                return (int(k.group(1)), int(k.group(2))) if k else None
            out.setdefault((st, en[m.group(1)]), []).append((int(m.group(2)), int(m.group(3)) - int(m.group(2)), ver(m.group(4)), ver(m.group(5))))
    return out, en

def vkd3d_ops(root):
    s = open(os.path.join(root, "libs/vkd3d-shader/d3dbc.c")).read()
    en = _enum(s, "VKD3D_SM1_")
    out = {}
    for st, tab in (("vs", "vs_opcode_table"), ("ps", "ps_opcode_table")):
        body = s[s.index("struct vkd3d_sm1_opcode_info %s[]" % tab):]; body = body[:body.index("};")]
        for m in re.finditer(r"\{VKD3D_SM1_OP_(\w+),\s*(\d+),\s*(\d+),\s*\"(\w+)\",\s*\w+(?:,\s*\{(\d),\s*(\d)\})?(?:,\s*\{(\d),\s*(\d)\})?\}", body):
            lo = (int(m.group(5)), int(m.group(6))) if m.group(5) else None
            hi = (int(m.group(7)), int(m.group(8))) if m.group(7) else None
            out.setdefault((st, en[m.group(1)]), []).append((int(m.group(2)), int(m.group(3)), lo, hi))
    return out, en

def models_of(entries, stage):
    return sorted({m for m in MODELS if m[:2] == stage for (_, _, lo, hi) in entries if in_range(m, lo, hi)}, key=MODELS.index)

REGNAME = {"TEMP": "r", "INPUT": "v", "CONST": "c", "ADDR": "a0", "TEXTURE": "t", "RASTOUT": "rastout", "ATTROUT": "oD", "OUTPUT": "o",
           "TEXCRDOUT": "oT", "CONSTINT": "i", "COLOROUT": "oC", "DEPTHOUT": "oDepth", "SAMPLER": "s", "CONSTBOOL": "b", "LOOP": "aL",
           "MISCTYPE": "misc", "LABEL": "l", "PREDICATE": "p0"}
def wine_regs(root):
    """-> {table_name: {regtype: (count|None, reladdr)}} from d3dcompiler_43/asmparser.c allowed_reg_type tables"""
    s = open(os.path.join(root, "dlls/d3dcompiler_43/asmparser.c")).read()
    out = {}
    for m in re.finditer(r"allowed_reg_type (\w+_reg_allowed)\[\] =\s*\{(.*?)\};", s, re.S):
        d = {}
        for r in re.finditer(r"\{ *BWRITERSPR_(\w+),\s*(~0u|\d+),\s*(TRUE|FALSE) *\}", m.group(2)):
            d[REGNAME[r.group(1)]] = (None if r.group(2) == "~0u" else int(r.group(2)), r.group(3) == "TRUE")
        out[m.group(1)] = d
    return out
WINE_TABLE = {"vs_1_1": "vs_1_reg_allowed", "vs_2_0": "vs_2_reg_allowed", "vs_2_x": "vs_2_reg_allowed", "vs_3_0": "vs_3_reg_allowed",
              "ps_1_1": "ps_1_0123_reg_allowed", "ps_1_2": "ps_1_0123_reg_allowed", "ps_1_3": "ps_1_0123_reg_allowed",
              "ps_1_4": "ps_1_4_reg_allowed", "ps_2_0": "ps_2_0_reg_allowed", "ps_2_x": "ps_2_x_reg_allowed", "ps_3_0": "ps_3_reg_allowed"}
