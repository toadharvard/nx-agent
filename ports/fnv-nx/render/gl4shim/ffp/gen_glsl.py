#!/usr/bin/env python3
"""gen_glsl.py OUT_DIR SRC_DIR HEADER... (the headers include nx_gl4shim.h): the fixed-function programs of the GL 4.5 backend (render/gl4shim, rows FFP_* of nx_gl4shim.h).
Writes OUT_DIR/gl4shim_ffp.gen.h: the GLSL text of SRC_DIR/ffp.vert.glsl / ffp.frag.glsl with every #include expanded, in the two variants
of the programs (smooth, and flat = NX_FFP_FLAT, selected by D3DRS_SHADEMODE), each as a NUL-terminated char array
nx_gl4shim_ffp_<vs|ps>_<smooth|flat>.
Every binding of the programs comes from the headers that own it (HEADER... = ffp_state.h, ffp_vs_state.h, fnvnx_dk_layout.h): the generator reads their
`#define NAME <integer>` lines for CONSTS and writes them as `#define` lines after #version, and the sampler declarations of the line `//@SAMPLERS` of
ffp.frag.glsl (stage n: 2D = n, CUBE = FNVNX_DK_SAMP_CUBE_BASE + n, VOLUME = FNVNX_DK_SAMP_VOLUME_BASE + n) are written from them. A constant that is not defined
exactly once in the headers is an error.
The UBO records are the C structs NxFfpPsState (ffp_state.h) and NxFfpVsState (ffp_vs_state.h): the line `//@UBO <struct>` of a GLSL file is replaced by the GLSL structs
and the std140 uniform block of that struct, every member with its C name (float[4] = vec4, float[16] = mat4, uint32_t[4k] = uvec4[k], a struct = the struct of the same
name), and OUT_DIR/layout/ffp_layout_{ps,vs}.gen.h hold the std140 offset of every member and the size of every struct as typedef-char assertions against offsetof / sizeof, so a C
layout that differs from the GLSL one is a compile error of the unit that includes it. A member of another shape is an error."""
import os, re, sys
SOURCES = (("vs", "ffp.vert.glsl"), ("ps", "ffp.frag.glsl"))
VARIANTS = (("smooth", None), ("flat", "NX_FFP_FLAT"))
CONSTS = ("NX_FFP_UBO_BINDING", "NX_FFP_VS_UBO_BINDING", "NX_FFP_VS_PALETTE_BINDING", "NX_FFP_MAX_BLEND_MATRICES", "NX_FFP_STAGES", "NX_FFP_MAX_LIGHTS", "FNVNX_DK_UBO_DRIVER", "FNVNX_DK_SAMP_CUBE_BASE", "FNVNX_DK_SAMP_VOLUME_BASE")
BLOCKS = (("NxFfpPsState", "NX_FFP_UBO_BINDING"), ("NxFfpVsState", "NX_FFP_VS_UBO_BINDING"))   # the UBO records: the C structs of ffp_state.h / ffp_vs_state.h
SAMPLERS = (("t2d", "sampler2D", None), ("tcb", "samplerCube", "FNVNX_DK_SAMP_CUBE_BASE"), ("tvl", "sampler3D", "FNVNX_DK_SAMP_VOLUME_BASE"))


def version(headers):
    """the `#version` line of the backend: NX_GL4SHIM_GLSL_VERSION of nx_gl4shim.h (the sources carry no version line)"""
    for h in headers:
        m = re.search(r'^#define\s+NX_GL4SHIM_GLSL_VERSION\s+"(#version [^"]+)"\s*$', open(h).read(), re.M)
        if m: return m.group(1)
    raise SystemExit("gen_glsl: NX_GL4SHIM_GLSL_VERSION is defined in no header")


def consts(headers):
    v = {}
    for h in headers:
        for m in re.finditer(r"^#define\s+(\w+)\s+(\d+)\s*(?:/\*.*)?$", open(h).read(), re.M):
            if m.group(1) in CONSTS:
                if m.group(1) in v: raise SystemExit("gen_glsl: %s defined twice" % m.group(1))
                v[m.group(1)] = int(m.group(2))
    for c in CONSTS:
        if c not in v: raise SystemExit("gen_glsl: %s is defined in no header" % c)
    return v


def strip_comments(t):
    return re.sub(r"//[^\n]*|/\*.*?\*/", "", t, flags=re.S)


def structs(headers, defs):
    """{name: [(member, base, dims)]}: the `typedef struct N { ... } N;` of the headers. A member is `float|uint32_t|<struct> a[, b]` with array dimensions
    that are integers or defined integers; anything else is an error."""
    out = {}
    for h in headers:
        for m in re.finditer(r"typedef\s+struct\s+(\w+)\s*\{([^}]*)\}\s*(\w+)\s*;", strip_comments(open(h).read())):
            if m.group(1) != m.group(3): raise SystemExit("gen_glsl: struct %s is typedef'd as %s" % (m.group(1), m.group(3)))
            mem = []
            try: mem = read_members(m.group(1), m.group(2), defs)
            except SystemExit as e: mem = e   # a struct that is no UBO record may have any members; its error is raised when a record needs it
            out[m.group(1)] = mem
    return out


def read_members(name, body, defs):
    if True:
        if True:
            mem = []
            for st in body.split(";"):
                st = st.strip()
                if not st: continue
                t = re.match(r"(float|uint32_t|\w+)\s+(.*)$", st, re.S)
                for d in t.group(2).split(","):
                    dm = re.match(r"\s*(\w+)((?:\s*\[\s*\w+\s*\])*)\s*$", d)
                    if not dm: raise SystemExit("gen_glsl: cannot read member '%s' of %s" % (d.strip(), name))
                    dims = [defs[x] if x in defs else int(x) for x in re.findall(r"\[\s*(\w+)\s*\]", dm.group(2))]
                    mem.append((dm.group(1), t.group(1), dims))
            return mem


def glsl_type(base, dims, sts):
    """-> (GLSL type, array count or None, std140 size of one element, bytes of the member) by the one rule of the records: float[4] = vec4, float[16] = mat4 (the D3D row-major bytes),
    uint32_t[4k] = uvec4[k], a further leading dimension is an array of those, a struct is the GLSL struct of the same name. Any other shape is not std140-expressible: change the struct."""
    if base == "float":
        if not dims or dims[-1] not in (4, 16): raise SystemExit("gen_glsl: float member of shape %s is neither vec4 nor mat4" % dims)
        t, sz = ("vec4", 16) if dims[-1] == 4 else ("mat4", 64); lead = dims[:-1]
    elif base == "uint32_t":
        if len(dims) != 1 or dims[0] % 4: raise SystemExit("gen_glsl: uint32_t member of shape %s is not uvec4[k]" % dims)
        t, sz = "uvec4", 16; lead = [dims[0] // 4] if dims[0] > 4 else []
    else:
        if base not in sts: raise SystemExit("gen_glsl: member type %s is no struct of the headers" % base)
        t, sz = base, size_of(base, sts); lead = dims
    if len(lead) > 1: raise SystemExit("gen_glsl: more than one array dimension: %s" % dims)
    n = lead[0] if lead else None
    return t, n, sz, sz * (n or 1)


def size_of(name, sts):
    off = 0
    for _, base, dims in members(name, sts): off += glsl_type(base, dims, sts)[3]
    return (off + 15) // 16 * 16


def members(name, sts):
    if isinstance(sts[name], SystemExit): raise sts[name]
    return sts[name]


def emit_struct(name, sts):
    """the GLSL declaration of struct NAME and the offset of every member (std140: every member of the records is a 16-byte multiple, so the offsets are running sums)"""
    body, offs, off = "", [], 0
    for m, base, dims in members(name, sts):
        t, n, _, size = glsl_type(base, dims, sts)
        body += " %s %s%s;" % (t, m, "[%d]" % n if n else ""); offs.append((m, off)); off += size
    return body, offs, size_of(name, sts)


def blocks(sts):
    """-> ({block name: the GLSL text of the record: its structs and its UBO block}, {block name: the C header that asserts every offset of the record})"""
    text, chk = {}, {}
    def need(name, acc):
        for _, base, _ in members(name, sts):
            if base in sts and base not in acc: need(base, acc)
        if name not in acc: acc.append(name)
        return acc
    for name, bind in BLOCKS:
        g, c = "", ["// generated by render/gl4shim/ffp/gen_glsl.py: the std140 offset of every member of %s (and its structs), asserted against the C struct; include after its header. Do not edit" % name,
                    "#include <stddef.h>", "#define NX_FFP_LAYOUT_SA(n, c) typedef char nx_ffp_layout_##n[(c) ? 1 : -1]"]
        for s in need(name, []):
            body, offs, size = emit_struct(s, sts)
            for m, o in offs: c.append("NX_FFP_LAYOUT_SA(%s_%s, offsetof(%s, %s) == %d);" % (s, m, s, m, o))
            c.append("NX_FFP_LAYOUT_SA(%s_size, sizeof(%s) == %d);" % (s, s, size))
            g += ("layout (std140, binding = %s) uniform %s {%s };\n" % (bind, s, body)) if s == name else "struct %s {%s };\n" % (s, body)
        text[name], chk[name] = g, "\n".join(c) + "\n"
    return text, chk


def samplers(v):
    """the sampler declarations of the pixel program, one line per stage"""
    return "\n".join("layout (binding = %d) uniform %s %s%d;" % (n + (v[b] if b else 0), t, p, n) for n in range(8) for p, t, b in SAMPLERS)


def expand(path, seen=()):
    """the file with every `#include "x"` replaced by the text of x (relative to the including file), recursively"""
    out = []
    for line in open(path).read().split("\n"):
        m = re.match(r'\s*#include\s+"([^"]+)"\s*$', line)
        if m:
            inc = os.path.normpath(os.path.join(os.path.dirname(path), m.group(1)))
            if inc in seen: raise SystemExit("gen_glsl: include cycle at " + inc)
            out.append(expand(inc, seen + (inc,)))
        else:
            out.append(line)
    return "\n".join(out)


def variant(text, define, v, ver):
    """the #version line, the constants of the headers and `#define <define> 1`, then the text"""
    if re.search(r"^\s*#\s*version", text, re.M): raise SystemExit("gen_glsl: a source carries a #version line; it is NX_GL4SHIM_GLSL_VERSION")
    text, i = ver + "\n" + text, len(ver) + 1
    d = "".join("#define %s %d\n" % (k, v[k]) for k in CONSTS) + ("#define %s 1\n" % define if define else "")
    return text[:i] + d + text[i:]


def carray(name, data):
    return "static const char %s[%d] = {%s};\n" % (name, len(data) + 1, ",".join(str(b) for b in data + b"\0"))


def main():
    if len(sys.argv) < 4: raise SystemExit("usage: gen_glsl.py OUT_DIR SRC_DIR HEADER...")
    out, src, v = sys.argv[1], sys.argv[2], consts(sys.argv[3:])
    ver = version(sys.argv[3:])
    sts = structs(sys.argv[3:], v)
    btext, layout = blocks(sts)
    os.makedirs(out, exist_ok=True)
    g = "// generated by render/gl4shim/ffp/gen_glsl.py from ffp.vert.glsl / ffp.frag.glsl (includes expanded); do not edit\n"
    for stage, f in SOURCES:
        text = expand(os.path.join(src, f))
        if "//@SAMPLERS\n" in text: text = text.replace("//@SAMPLERS\n", samplers(v) + "\n")
        for n in btext: text = text.replace("//@UBO %s\n" % n, btext[n])
        for vname, d in VARIANTS:
            b = variant(text, d, v, ver).encode(); n = "nx_gl4shim_ffp_%s_%s" % (stage, vname)
            g += carray(n, b)
    open(os.path.join(out, "gl4shim_ffp.gen.h"), "w").write(g)
    os.makedirs(os.path.join(out, "layout"), exist_ok=True)
    for n, f in (("NxFfpPsState", "ffp_layout_ps.gen.h"), ("NxFfpVsState", "ffp_layout_vs.gen.h")): open(os.path.join(out, "layout", f), "w").write(layout[n])


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    main()
