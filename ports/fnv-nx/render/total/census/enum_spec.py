#!/usr/bin/env python3
"""enum_spec.py - parse the D3D9 enumerations of shim/include/d3d9types.h (+ d3d9.h for device-call flag families)
and write enum_universe.tsv and enum_state_domain.tsv (L6 R11 census; measurement only).

Usage: enum_spec.py [outdir]   (default: this directory).  Library use: import enum_spec; enum_spec.universe()."""
import os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))        # ports/fnv-nx
INC = os.path.join(ROOT, 'shim', 'include')


def strip_comments(s):
    s = re.sub(r'/\*.*?\*/', '', s, flags=re.S)
    return re.sub(r'//[^\n]*', '', s)


def ev(expr, env):
    e = expr.strip()
    e = re.sub(r"MAKEFOURCC\(\s*'(.)'\s*,\s*'(.)'\s*,\s*'(.)'\s*,\s*'(.)'\s*\)",
               lambda m: str(sum(ord(m.group(i + 1)) << (8 * i) for i in range(4))), e)
    e = re.sub(r'\b(0[xX][0-9a-fA-F]+|\d+)[uUlL]+\b', r'\1', e)
    e = re.sub(r'\(\s*(?:DWORD|D3DTRANSFORMSTATETYPE|int|unsigned)\s*\)', '', e)
    try:
        return eval(e, {'__builtins__': {}}, env)
    except Exception:
        return None


def parse_defines(text, env):
    """object-like #define NAME value, in order; returns list of (name, value)"""
    out = []
    for m in re.finditer(r'^[ \t]*#define[ \t]+(\w+)[ \t]+([^\n]+)$', text, flags=re.M):
        name, val = m.group(1), m.group(2).strip()
        v = ev(val, env)
        if isinstance(v, int):
            env[name] = v
            out.append((name, v))
    return out


def split_top(s):
    out, d, cur = [], 0, ''
    for ch in s:
        if ch == '(': d += 1
        elif ch == ')': d -= 1
        if ch == ',' and d == 0:
            out.append(cur); cur = ''
        else:
            cur += ch
    out.append(cur)
    return out


def parse_enums(text, env):
    """typedef enum {...} NAME; -> list of (enum, member, value, note)"""
    rows = []
    for m in re.finditer(r'typedef\s+enum\s+\w*\s*\{(.*?)\}\s*(\w+)\s*;', text, flags=re.S):
        body, name = m.group(1), m.group(2)
        prev, in9ex = -1, False
        for line in body.split('\n'):
            ls = line.strip()
            if ls.startswith('#'):
                if re.match(r'#\s*ifndef\s+D3D_DISABLE_9EX', ls): in9ex = True
                elif re.match(r'#\s*(endif|else)', ls): in9ex = False
                continue
            for part in split_top(ls):
                part = part.strip()
                if not part: continue
                mm = re.match(r'(\w+)\s*(?:=\s*(.+))?$', part)
                if not mm: continue
                mem = mm.group(1)
                v = ev(mm.group(2), env) if mm.group(2) is not None else prev + 1
                if not isinstance(v, int): continue
                env[mem] = v
                prev = v
                rows.append((name, mem, v, '9ex' if in9ex else ''))
    return rows


# bit-flag / constant families (#define) that are arguments of device calls or state values
FAMILIES = [   # (family label, name prefix regex, source header, what takes it)
    ('D3DCLEAR', r'D3DCLEAR_', 'd3d9types.h', 'Clear Flags'),
    ('D3DFVF', r'D3DFVF_', 'd3d9types.h', 'SetFVF / vertex format'),
    ('D3DLOCK', r'D3DLOCK_', 'd3d9types.h', 'Lock Flags'),
    ('D3DUSAGE', r'D3DUSAGE_', 'd3d9types.h', 'Create* Usage'),
    ('D3DTA', r'D3DTA_', 'd3d9types.h', 'D3DTSS_COLORARGn/ALPHAARGn/RESULTARG'),
    ('D3DTSS_TCI', r'D3DTSS_TCI_', 'd3d9types.h', 'D3DTSS_TEXCOORDINDEX high bits'),
    ('D3DCOLORWRITEENABLE', r'D3DCOLORWRITEENABLE_', 'd3d9types.h', 'D3DRS_COLORWRITEENABLE[1-3]'),
    ('D3DWRAPCOORD', r'D3DWRAPCOORD_', 'd3d9types.h', 'D3DRS_WRAP0..15'),
    ('D3DWRAP', r'D3DWRAP_[UVW]$', 'd3d9types.h', 'legacy D3DRS_WRAP bits'),
    ('D3DSTREAMSOURCE', r'D3DSTREAMSOURCE_', 'd3d9types.h', 'SetStreamSourceFreq'),
    ('D3DCLIPPLANE', r'D3DCLIPPLANE\d$', 'd3d9types.h', 'D3DRS_CLIPPLANEENABLE bits'),
    ('D3DISSUE', r'D3DISSUE_', 'd3d9types.h', 'IDirect3DQuery9::Issue'),
    ('D3DGETDATA', r'D3DGETDATA_', 'd3d9types.h', 'IDirect3DQuery9::GetData'),
    ('D3DPRESENTFLAG', r'D3DPRESENTFLAG_', 'd3d9types.h', 'D3DPRESENT_PARAMETERS.Flags'),
    ('D3DCS', r'D3DCS_', 'd3d9types.h', 'D3DCLIPSTATUS9 (output)'),
    ('D3DPV', r'D3DPV_', 'd3d9types.h', 'ProcessVertices'),
    ('D3DVERTEXTEXTURESAMPLER', r'D3D(VERTEXTEXTURESAMPLER\d|DMAPSAMPLER)$', 'd3d9types.h', 'sampler index space'),
    ('D3DCREATE', r'D3DCREATE_', 'd3d9.h', 'CreateDevice BehaviorFlags'),
    ('D3DPRESENT', r'D3DPRESENT_', 'd3d9.h', 'Present / PresentationInterval'),
    ('D3DENUM', r'D3DENUM_', 'd3d9.h', 'GetAdapterModeCount'),
    ('D3DSGR', r'D3DSGR_', 'd3d9.h', 'SetGammaRamp'),
    ('D3DCURSOR', r'D3DCURSOR_', 'd3d9.h', 'ShowCursor'),
    ('D3DSPD', r'D3DSPD_', 'd3d9.h', 'SetPaletteEntries'),
]


def load():
    env = {'MAKEFOURCC': None}
    t = strip_comments(open(os.path.join(INC, 'd3d9types.h'), encoding='latin-1').read())
    h = strip_comments(open(os.path.join(INC, 'd3d9.h'), encoding='latin-1').read())
    defs = parse_defines(t, env)
    enums = parse_enums(t, env)
    defs_h = parse_defines(h, env)
    return t, h, defs, enums, defs_h, env


def universe():
    """list of dict rows: enum, member, value, kind(enum|define|generated), source, note"""
    t, h, defs, enums, defs_h, env = load()
    rows, seen = [], set()
    def add(en, mem, v, kind, src, note=''):
        k = (en, mem)
        if k in seen: return
        seen.add(k)
        rows.append(dict(enum=en, member=mem, value=v, kind=kind, source=src, note=note))
    for en, mem, v, note in enums:
        add(en, mem, v, 'sentinel' if mem.endswith('_FORCE_DWORD') else 'enum', 'd3d9types.h', note)
    for label, pat, src, what in FAMILIES:
        for name, v in (defs if src == 'd3d9types.h' else defs_h):
            if re.match(pat, name):
                add(label, name, v, 'define', src, what)
    # function-like macro families, expanded over their whole domain
    for n, bits in ((1, 3), (2, 0), (3, 1), (4, 2)):
        for i in range(8):
            add('D3DFVF_TEXCOORDSIZE', 'D3DFVF_TEXCOORDSIZE%d(%d)' % (n, i), bits << (i * 2 + 16), 'generated', 'd3d9types.h',
                'texcoord set %d is %d-component' % (i, n))
    for i in range(256):
        add('D3DTRANSFORMSTATETYPE.WORLDMATRIX', 'D3DTS_WORLDMATRIX(%d)' % i, 256 + i, 'generated', 'd3d9types.h',
            'D3DTS_WORLD=256; documented range 256..511')
    # vendor / driver FOURCC formats the code names but d3d9types.h does not
    def fcc(s): return sum(ord(c) << (8 * i) for i, c in enumerate(s))
    for s in ('ATI1', 'ATI2', 'INTZ', 'DF16', 'DF24', 'NULL', 'RAWZ', 'RESZ', 'ATOC'):
        add('D3DFORMAT.VENDOR', 'FOURCC_' + s, fcc(s), 'vendor', 'code/Nine', 'not in d3d9types.h; INTZ/DF16/DF24/ATI1/ATI2 are named in d3d9_state.c')
    # decl usage x usage index: every (usage, index 0..15)
    for en, mem, v, note in enums:
        if en == 'D3DDECLUSAGE':
            for i in range(16):
                add('D3DDECLUSAGE.x.INDEX', '%s[%d]' % (mem, i), v * 16 + i, 'generated', 'd3d9types.h', 'MAXD3DDECLUSAGEINDEX=15')
    return rows


# ---- state value domains (from D3D9 documentation semantics; '?' = unsure) -------------------------------------
B, F, C, D = 'bool', 'float', 'color', 'dword'
RS_DOM = {
 'ZENABLE': ('enum', 'D3DZBUFFERTYPE'), 'FILLMODE': ('enum', 'D3DFILLMODE'), 'SHADEMODE': ('enum', 'D3DSHADEMODE'),
 'ZWRITEENABLE': (B, ''), 'ALPHATESTENABLE': (B, ''), 'LASTPIXEL': (B, ''),
 'SRCBLEND': ('enum', 'D3DBLEND'), 'DESTBLEND': ('enum', 'D3DBLEND'), 'CULLMODE': ('enum', 'D3DCULL'),
 'ZFUNC': ('enum', 'D3DCMPFUNC'), 'ALPHAREF': (D, '0..255 (low 8 bits)'), 'ALPHAFUNC': ('enum', 'D3DCMPFUNC'),
 'DITHERENABLE': (B, ''), 'ALPHABLENDENABLE': (B, ''), 'FOGENABLE': (B, ''), 'SPECULARENABLE': (B, ''),
 'FOGCOLOR': (C, ''), 'FOGTABLEMODE': ('enum', 'D3DFOGMODE'), 'FOGSTART': (F, ''), 'FOGEND': (F, ''), 'FOGDENSITY': (F, ''),
 'RANGEFOGENABLE': (B, ''), 'STENCILENABLE': (B, ''), 'STENCILFAIL': ('enum', 'D3DSTENCILOP'),
 'STENCILZFAIL': ('enum', 'D3DSTENCILOP'), 'STENCILPASS': ('enum', 'D3DSTENCILOP'), 'STENCILFUNC': ('enum', 'D3DCMPFUNC'),
 'STENCILREF': (D, '0..255'), 'STENCILMASK': (D, 'bitmask 8'), 'STENCILWRITEMASK': (D, 'bitmask 8'),
 'TEXTUREFACTOR': (C, ''), 'CLIPPING': (B, ''), 'LIGHTING': (B, ''), 'AMBIENT': (C, ''),
 'FOGVERTEXMODE': ('enum', 'D3DFOGMODE'), 'COLORVERTEX': (B, ''), 'LOCALVIEWER': (B, ''), 'NORMALIZENORMALS': (B, ''),
 'DIFFUSEMATERIALSOURCE': ('enum', 'D3DMATERIALCOLORSOURCE'), 'SPECULARMATERIALSOURCE': ('enum', 'D3DMATERIALCOLORSOURCE'),
 'AMBIENTMATERIALSOURCE': ('enum', 'D3DMATERIALCOLORSOURCE'), 'EMISSIVEMATERIALSOURCE': ('enum', 'D3DMATERIALCOLORSOURCE'),
 'VERTEXBLEND': ('enum', 'D3DVERTEXBLENDFLAGS'), 'CLIPPLANEENABLE': ('bitmask', 'D3DCLIPPLANE0..5'),
 'POINTSIZE': (F, ''), 'POINTSIZE_MIN': (F, ''), 'POINTSIZE_MAX': (F, ''), 'POINTSPRITEENABLE': (B, ''),
 'POINTSCALEENABLE': (B, ''), 'POINTSCALE_A': (F, ''), 'POINTSCALE_B': (F, ''), 'POINTSCALE_C': (F, ''),
 'MULTISAMPLEANTIALIAS': (B, ''), 'MULTISAMPLEMASK': (D, 'bitmask 32'), 'PATCHEDGESTYLE': ('enum', 'D3DPATCHEDGESTYLE'),
 'DEBUGMONITORTOKEN': ('enum', 'D3DDEBUGMONITORTOKENS'), 'INDEXEDVERTEXBLENDENABLE': (B, ''),
 'COLORWRITEENABLE': ('bitmask', 'D3DCOLORWRITEENABLE_*'), 'COLORWRITEENABLE1': ('bitmask', 'D3DCOLORWRITEENABLE_*'),
 'COLORWRITEENABLE2': ('bitmask', 'D3DCOLORWRITEENABLE_*'), 'COLORWRITEENABLE3': ('bitmask', 'D3DCOLORWRITEENABLE_*'),
 'TWEENFACTOR': (F, ''), 'BLENDOP': ('enum', 'D3DBLENDOP'), 'POSITIONDEGREE': ('enum', 'D3DDEGREETYPE'),
 'NORMALDEGREE': ('enum', 'D3DDEGREETYPE'), 'SCISSORTESTENABLE': (B, ''), 'SLOPESCALEDEPTHBIAS': (F, ''),
 'ANTIALIASEDLINEENABLE': (B, ''), 'MINTESSELLATIONLEVEL': (F, ''), 'MAXTESSELLATIONLEVEL': (F, ''),
 'ADAPTIVETESS_X': (F, ''), 'ADAPTIVETESS_Y': (F, ''), 'ADAPTIVETESS_Z': (F, ''), 'ADAPTIVETESS_W': (F, ''),
 'ENABLEADAPTIVETESSELLATION': (B, ''), 'TWOSIDEDSTENCILMODE': (B, ''), 'CCW_STENCILFAIL': ('enum', 'D3DSTENCILOP'),
 'CCW_STENCILZFAIL': ('enum', 'D3DSTENCILOP'), 'CCW_STENCILPASS': ('enum', 'D3DSTENCILOP'),
 'CCW_STENCILFUNC': ('enum', 'D3DCMPFUNC'), 'BLENDFACTOR': (C, ''), 'SRGBWRITEENABLE': (B, ''), 'DEPTHBIAS': (F, ''),
 'SEPARATEALPHABLENDENABLE': (B, ''), 'SRCBLENDALPHA': ('enum', 'D3DBLEND'), 'DESTBLENDALPHA': ('enum', 'D3DBLEND'),
 'BLENDOPALPHA': ('enum', 'D3DBLENDOP'),
}
for _i in range(16):
    RS_DOM['WRAP%d' % _i] = ('bitmask', 'D3DWRAPCOORD_0..3 (U,V,W,4th coord)')
SAMP_DOM = {
 'ADDRESSU': ('enum', 'D3DTEXTUREADDRESS'), 'ADDRESSV': ('enum', 'D3DTEXTUREADDRESS'), 'ADDRESSW': ('enum', 'D3DTEXTUREADDRESS'),
 'BORDERCOLOR': (C, ''), 'MAGFILTER': ('enum', 'D3DTEXTUREFILTERTYPE'), 'MINFILTER': ('enum', 'D3DTEXTUREFILTERTYPE'),
 'MIPFILTER': ('enum', 'D3DTEXTUREFILTERTYPE'), 'MIPMAPLODBIAS': (F, ''), 'MAXMIPLEVEL': (D, 'mip index'),
 'MAXANISOTROPY': (D, '1..MaxAnisotropy'), 'SRGBTEXTURE': (B, ''), 'ELEMENTINDEX': (D, 'N-patch element index'),
 'DMAPOFFSET': (D, 'displacement-map vertex offset'),
}
TSS_DOM = {
 'COLOROP': ('enum', 'D3DTEXTUREOP'), 'ALPHAOP': ('enum', 'D3DTEXTUREOP'),
 'COLORARG0': ('bitmask', 'D3DTA_* (select 0..6 | COMPLEMENT | ALPHAREPLICATE)'), 'COLORARG1': ('bitmask', 'D3DTA_*'),
 'COLORARG2': ('bitmask', 'D3DTA_*'), 'ALPHAARG0': ('bitmask', 'D3DTA_*'), 'ALPHAARG1': ('bitmask', 'D3DTA_*'),
 'ALPHAARG2': ('bitmask', 'D3DTA_*'), 'RESULTARG': ('bitmask', 'D3DTA_* (CURRENT or TEMP)'),
 'BUMPENVMAT00': (F, ''), 'BUMPENVMAT01': (F, ''), 'BUMPENVMAT10': (F, ''), 'BUMPENVMAT11': (F, ''),
 'BUMPENVLSCALE': (F, ''), 'BUMPENVLOFFSET': (F, ''),
 'TEXCOORDINDEX': ('bitmask', 'index 0..7 | D3DTSS_TCI_* (high 16 bits)'),
 'TEXTURETRANSFORMFLAGS': ('enum', 'D3DTEXTURETRANSFORMFLAGS (count | PROJECTED)'), 'CONSTANT': (C, ''),
}


def domain_rows():
    rows = []
    for r in universe():
        if r['enum'] == 'D3DRENDERSTATETYPE' and r['member'] != 'D3DRS_FORCE_DWORD':
            k = r['member'][6:]; kind, dom = RS_DOM.get(k, ('?', '?'))
            rows.append(('RS', r['member'], r['value'], kind, dom, '' if k in RS_DOM else 'domain=?'))
        elif r['enum'] == 'D3DSAMPLERSTATETYPE' and r['member'] != 'D3DSAMP_FORCE_DWORD':
            k = r['member'][8:]; kind, dom = SAMP_DOM.get(k, ('?', '?'))
            rows.append(('SAMP', r['member'], r['value'], kind, dom, '' if k in SAMP_DOM else 'domain=?'))
        elif r['enum'] == 'D3DTEXTURESTAGESTATETYPE' and r['member'] != 'D3DTSS_FORCE_DWORD':
            k = r['member'][7:]; kind, dom = TSS_DOM.get(k, ('?', '?'))
            rows.append(('TSS', r['member'], r['value'], kind, dom, '' if k in TSS_DOM else 'domain=?'))
    return rows


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else HERE
    u = universe()
    with open(os.path.join(out, 'enum_universe.tsv'), 'w') as f:
        f.write('enum\tmember\tvalue\tkind\tsource\tnote\n')
        for r in u:
            f.write('%s\t%s\t%d\t%s\t%s\t%s\n' % (r['enum'], r['member'], r['value'] & 0xFFFFFFFF if r['value'] < 0 else r['value'],
                                                    r['kind'], r['source'], r['note']))
    d = domain_rows()
    with open(os.path.join(out, 'enum_state_domain.tsv'), 'w') as f:
        f.write('class\tstate\tindex\tdomain\tenum_or_detail\tnote\n')
        for r in d:
            f.write('%s\t%s\t%d\t%s\t%s\t%s\n' % r)
    t, h, defs, enums, defs_h, env = load()
    names = sorted(set(r['enum'] for r in u))
    sys.stderr.write('universe rows %d in %d enums/families; state-domain rows %d (domain=? %d)\n' %
                     (len(u), len(names), len(d), sum(1 for r in d if r[3] == '?')))


if __name__ == '__main__':
    main()
