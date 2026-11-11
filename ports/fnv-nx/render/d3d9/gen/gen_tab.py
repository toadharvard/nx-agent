"""gen_tab.py: emitters for the spec-derived tables of d9wrap.gen.cpp (struct descriptors, ids, formats, primitives, shader walk, object properties)."""
import re
from gen_expr import GenError

# object property sources: iface -> (setup C++ run on `r`, {prop: C++ value expression}); tables of D3D9 itself (desc getters), not per-item guesses
_S = ("D3DSURFACE_DESC d; r->%s;", {"width": "d.Width", "height": "d.Height", "fmt": "d.Format", "usage": "d.Usage", "pool": "d.Pool"})
_V = ("D3DVOLUME_DESC d; r->%s;", {"width": "d.Width", "height": "d.Height", "depth": "d.Depth", "fmt": "d.Format", "usage": "d.Usage", "pool": "d.Pool"})
_B = ("%s d; r->GetDesc(&d);", {"length": "d.Size", "fmt": "d.Format", "usage": "d.Usage", "pool": "d.Pool"})
_T = {"levels": "r->GetLevelCount()"}
PROPSRC = {
    "IDirect3DSurface9": (_S[0] % "GetDesc(&d)", _S[1]), "IDirect3DVolume9": (_V[0] % "GetDesc(&d)", _V[1]),
    "IDirect3DTexture9": (_S[0] % "GetLevelDesc(0, &d)", {**_S[1], **_T}), "IDirect3DCubeTexture9": (_S[0] % "GetLevelDesc(0, &d)", {**_S[1], **_T}),
    "IDirect3DVolumeTexture9": (_V[0] % "GetLevelDesc(0, &d)", {**_V[1], **_T}),
    "IDirect3DVertexBuffer9": (_B[0] % "D3DVERTEXBUFFER_DESC", _B[1]), "IDirect3DIndexBuffer9": (_B[0] % "D3DINDEXBUFFER_DESC", _B[1]),
    "IDirect3DVertexShader9": ("UINT n = 0; r->GetFunction(0, &n);", {"shader_bytes": "n"}),
    "IDirect3DPixelShader9": ("UINT n = 0; r->GetFunction(0, &n);", {"shader_bytes": "n"}),
    "IDirect3DVertexDeclaration9": ("UINT n = 0; r->GetDeclaration(0, &n);", {"decl_bytes": "8 * (uint64_t)n"}),
    "IDirect3DQuery9": ("", {"data_size": "r->GetDataSize()", "qtype": "(uint64_t)r->GetType()"}),
}
PIDS = {"length": "D9P_LENGTH", "fmt": "D9P_FMT", "width": "D9P_WIDTH", "height": "D9P_HEIGHT", "depth": "D9P_DEPTH", "levels": "D9P_LEVELS", "usage": "D9P_USAGE",
        "pool": "D9P_POOL", "shader_bytes": "D9P_SHADER_BYTES", "decl_bytes": "D9P_DECL_BYTES", "data_size": "D9P_DATA_SIZE", "qtype": "D9P_QTYPE"}


def props_of(iface):
    return set(PROPSRC[iface][1]) if iface in PROPSRC else None


def structs(spec):
    L = [f"extern const D9SDesc d9_sd_{n};" for n in spec["structs"]]
    for n, st in spec["structs"].items():
        rows = []
        for f in st["fields"]:
            k = f["kind"]
            if k in ("int", "enum", "flags", "float", "char"): rows.append(f"{{0, {f['off']}, {f['size']}, 1, 0}}")
            elif k in ("ptr", "ohandle"): rows.append(f"{{1, {f['off']}, 4, 1, 0}}")
            elif k == "pad": rows.append(f"{{2, {f['off']}, {f['size']}, 1, 0}}")
            elif k == "struct": rows.append(f"{{3, {f['off']}, {f['size']}, {f.get('count', 1)}, &d9_sd_{f['struct']}}}")
            else: raise GenError(f"struct {n}.{f['name']}: field kind {k} has no emitter")
        L.append(f"static const D9SField d9_sf_{n}[] = {{{', '.join(rows)}}};")
        L.append(f"const D9SDesc d9_sd_{n} = {{d9_sf_{n}, {len(rows)}}};")
    return L


def ids(spec, names):
    L = [f"enum {{ {', '.join(f'D9I_{n} = {i}' for i, n in enumerate(names))} }};", f"static const int D9_NIFACE = {len(names)};"]
    rows = []
    has = []
    for n in names:
        iid = spec["interfaces"][n]["iid"]; has.append(int(bool(iid)))       # `iid: null` = no source gives the IID (IDirect3DShaderValidator9): the interface is never the answer of an IID lookup
        b = bytes.fromhex((iid or "0" * 32).replace("-", ""))
        le = b[0:4][::-1] + b[4:6][::-1] + b[6:8][::-1] + b[8:]
        rows.append("{" + ",".join(str(x) for x in le) + "}")
    L.append(f"static const unsigned char d9_iid[{len(names)}][16] = {{{', '.join(rows)}}};")
    L.append(f"static const unsigned char d9_has_iid[{len(names)}] = {{{', '.join(map(str, has))}}};")
    L.append("int d9s_iid_iface(const void *g) { for (int i = 0; i < D9_NIFACE; i++) if (d9_has_iid[i] && !memcmp(g, d9_iid[i], 16)) return i; return -1; }")
    isa = []
    for a in names:
        row, c = [0] * len(names), a
        while c:
            row[names.index(c)] = 1; c = spec["interfaces"][c]["base"]
        isa.append("{" + ",".join(map(str, row)) + "}")
    L.append(f"static const unsigned char d9_isa[{len(names)}][{len(names)}] = {{{', '.join(isa)}}};")
    L.append("bool d9g_isa(int a, int b) { return a >= 0 && b >= 0 && d9_isa[a][b]; }")
    return L


def tables(spec):
    t = spec["tables"]; L = ["uint64_t d9g_rows(uint64_t fmt, uint64_t h) { switch ((uint32_t)fmt) {"]
    for k, (bw, bh, by) in t["format"].items(): L.append(f"case {k}: return (h + {bh - 1}) / {bh};")
    L += ["default: return h; } }", "uint64_t d9g_rowbytes(uint64_t fmt, uint64_t w) { switch ((uint32_t)fmt) {"]
    for k, (bw, bh, by) in t["format"].items(): L.append(f"case {k}: return ((w + {bw - 1}) / {bw}) * {by};")
    L += ["default: return 0; } }", "uint64_t d9g_verts(uint64_t type, uint64_t n) { if (!n) return 0; switch ((uint32_t)type) {"]
    for k, (a, b) in t["primitive"].items(): L.append(f"case {k}: return n * {a} + {b};")
    L += ["default: return 0; } }", "uint64_t d9g_index_size(uint64_t f) { return f == D3DFMT_INDEX16 ? 2 : f == D3DFMT_INDEX32 ? 4 : 0; }"]
    w = t["sm_walk"]; vt = w["version_token"]
    L += ["static unsigned d9_sm_params(uint32_t op, bool ps14) { switch (op) {"]
    for k, v in w["params_lt_2"].items():
        L.append(f"case {k}: return ps14 ? {w['ps_1_4'].get(k, v)} : {v};")
    L += ["default: return 0; } }", "uint64_t d9g_sm_bytes(const void *p) { const uint32_t *t = (const uint32_t *)p; uint32_t ver = t[0]; "
          f"unsigned major = (ver >> {vt['major_shift']}) & {vt['major_mask']}, minor = ver & 255; bool ps14 = (ver >> 16) == {vt['pixel_high']} && major == 1 && minor == 4; size_t i = 1;",
          f"for (;;) {{ uint32_t tok = t[i], op = tok & 0xFFFF; if (op == {w['end']}) return (i + 1) * 4;",
          f"if (op == {w['comment']['opcode']}) {{ i += 1 + ((tok >> {w['comment']['size_shift']}) & {w['comment']['size_mask']}); continue; }}",
          f"i += 1 + (major >= 2 ? (tok >> {w['length_ge_2']['shift']}) & {w['length_ge_2']['mask']} : d9_sm_params(op, ps14)); }} }}"]
    L += ["int d9g_qtype_cls(uint32_t q) { switch (q) {"]
    for k, v in t["query_type"].items(): L.append(f"case {k}: return {['det', 'env', 'pix'].index(v['cls'])};")
    L += ["default: return 2; } }"]
    return L


def props(spec, names, used):
    for (i, p) in sorted(used):
        if i not in PROPSRC or p not in PROPSRC[i][1]: raise GenError(f"{i}: rule uses this.{p} but no property source is defined for it")
    L = ["bool d9g_has_prop(int iface, int prop) { switch (iface) {"]
    for n in names:
        if n in PROPSRC: L.append(f"case D9I_{n}: switch (prop) {{ {' '.join(f'case {PIDS[p]}:' for p in PROPSRC[n][1])} return true; default: return false; }}")
    L += ["default: return false; } }", "uint64_t d9g_prop(D9Obj *o, int prop) { switch (o->iface) {"]
    for n in names:
        if n in PROPSRC:
            setup, pr = PROPSRC[n]
            L.append(f"case D9I_{n}: {{ {n} *r = ({n} *)o->real; (void)r; {setup} switch (prop) {{")
            L += [f"case {PIDS[p]}: return (uint64_t){e};" for p, e in pr.items()] + ["default: break; } break; }"]
    L += ["default: break; } return 0; }", "int d9g_level_idx(int iface) { switch (iface) {"]
    for n in names:
        m = [mm for mm in spec["methods"] if mm["iface"] == n]
        for mm in m:
            for q in mm["params"]:
                if q["kind"] == "locked" and q["lock"]["match"]:
                    ex = " ".join(str(v) for v in q["lock"].values())
                    lv = [i for i, (a, b) in enumerate(q["lock"]["match"]) if re.search(r"lvl\([^()]*,\s*" + a + r"\s*\)", ex)]
                    L.append(f"case D9I_{n}: return {lv[0] if lv else -1};"); break
            else: continue
            break
    L.append("default: return -1; } }")
    return L
