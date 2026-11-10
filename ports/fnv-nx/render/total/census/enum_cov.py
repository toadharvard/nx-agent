#!/usr/bin/env python3
"""enum_cov.py <workdir> <censusdir> : merge the static reading of the code (rules below, with file:line evidence looked
up at run time) and the dynamic probe (enum_probe.out) into enum_coverage.tsv, enum_coverage_sv.tsv (per state x value),
enum_skips.tsv and enum_summary.tsv.  Measurement only.

Status (worst wins when one enum member is used by several states):
  covered | ignored (stored/translated, no downstream effect) | fallback_default (silent default or approximation)
  | unsupported_mark (NX_PLAT_UNSUPPORTED fires) | skip_path (draw/record skipped or rejected)
  | not_reachable_in_code (no code in the census scope mentions it)  | sentinel (*_FORCE_DWORD, not counted)"""
import os, re, sys, collections
W = sys.argv[1]; H = sys.argv[2]
sys.path.insert(0, H)
import enum_spec as S

ROOT = S.ROOT
F = dict(st='render/native/front/d3d9_state.c', ex='render/native/capture/d3d9_exec.cpp', fr='render/native/front/front.cpp',
         gl='render/native/gl/nxgl.c', dk='render/native/host/nxgpu.c', hd='render/native/front/d3d9_state.h',
         ly='render/shaders/include/fnvnx_dk_layout.h', cap='render/native/capture/d3d9cap.h')
_cache = {}
def lines(k):
    if k not in _cache: _cache[k] = open(os.path.join(ROOT, F[k]), encoding='latin-1').read().split('\n')
    return _cache[k]
def ln(k, pat, nth=0):
    hits = [i + 1 for i, l in enumerate(lines(k)) if re.search(pat, l)]
    return '%s:%d' % (F[k], hits[nth]) if len(hits) > nth else '%s:?' % F[k]

ORDER = ['covered', 'ignored', 'fallback_default', 'unsupported_mark', 'skip_path', 'not_reachable_in_code']
def worst(sts):
    sts = [s for s in sts if s in ORDER]
    return max(sts, key=ORDER.index) if sts else 'covered'

# ---- probe -----------------------------------------------------------------------------------------------------
rsv, spv, fmt, cnv, bias, usg = {}, {}, {}, {}, {}, {}
for l in open(os.path.join(W, 'enum_probe.out')):
    p = l.rstrip('\n').split('\t')
    if p[0] == 'RSV': rsv[(int(p[1]), int(p[2]))] = (int(p[3]), int(p[4]), int(p[5]), p[6] if len(p) > 6 else '')
    elif p[0] == 'SPV':
        k = (int(p[1]), int(p[2]))
        o = spv.get(k, [0, 0, 1, ''])
        o[0] += int(p[4]); o[1] |= int(p[5]); o[2] &= int(p[6]); o[3] = o[3] or (p[7] if len(p) > 7 else '')
        spv[k] = o
    elif p[0] == 'FMT': fmt[int(p[1])] = dict(marks=int(p[2]), fmt=int(p[3]), flags=int(p[4]), sw=p[5:9], bytes=int(p[9]), src=int(p[10]), name=p[11] if len(p) > 11 else '')
    elif p[0] == 'CNV': cnv[int(p[1])] = (int(p[2]), int(p[3]))
    elif p[0] == 'BIAS': bias[int(p[1])] = float(p[2])
    elif p[0] == 'USG': usg[(int(p[1]), int(p[2]))] = int(p[3])
NXFMT_DEPTH, NXFMT_RENDER, NXFMT_CONVERT = None, None, None
NXF = {}
NXFMT_COMPRESSED = NXFMT_SRGB = NXFMT_STENCIL = None
for l in open(os.path.join(ROOT, 'render/native/include/nx/state.h'), encoding='latin-1'):
    m = re.match(r'#define\s+NXFMT_(\w+)\s+(\S+)', l)
    if m and m.group(1) in ('DEPTH', 'RENDER', 'CONVERT', 'COMPRESSED', 'SRGB', 'STENCIL'):
        NXF[m.group(1)] = 0
        globals()['NXFMT_' + m.group(1)] = eval(m.group(2).replace('(', '').replace(')', '').replace('u', '').replace('U', '').replace('1<<', '1<<'))
def rs_changed(i): return any(rsv[(i, v)][1] for (j, v) in rsv if j == i)
def sp_changed(i): return any(spv[(i, v)][1] for (j, v) in spv if j == i)

# ---- state rows ---------------------------------------------------------------------------------------------------
# render states the downstream path (translator output -> exec -> backend) does not consume although the translator
# computes a value (evidence: no reader of NXRS_R_FLAT / NXRS_R_POINTSPRITE / dyn.pointSize / clip planes)
RS_TRANSLATED_UNCONSUMED = {
    9: 'NXRS_R_FLAT is set by nx_d3d9_raster_word but no backend, exec or front code reads it',
    152: 'NXRS_R_CLIP: nxgl.c enables GL_CLIP_DISTANCE bits, but exec passes no clip_planes (in.clip_planes = 0) and nxgpu.c ignores it',
    154: 'dyn.pointSize is computed; no consumer (exec, front, nxgl, nxgpu) reads it',
    155: 'dyn.pointSize (clamp) is computed; no consumer', 166: 'dyn.pointSize (clamp) is computed; no consumer',
    156: 'NXRS_R_POINTSPRITE is set; no consumer'}
def rs_use_lines():
    """first uses of RS_<NAME> in d3d9_state.c outside the constant enum and nx_d3d9_default_rs"""
    L = lines('st'); out = collections.defaultdict(list)
    end_enum = next(i for i, l in enumerate(L) if l.startswith('};'))
    d0 = next(i for i, l in enumerate(L) if l.startswith('void nx_d3d9_default_rs'))
    d1 = next(i for i in range(d0, len(L)) if L[i].startswith('}'))
    for i, l in enumerate(L):
        if i <= end_enum or d0 <= i <= d1: continue
        for m in re.finditer(r'\bRS_(\w+)\b', l): out[m.group(1)].append(i + 1)
    return out
RSU = rs_use_lines()
def exec_rs_lines(idx): return [i + 1 for i, l in enumerate(lines('ex')) if re.search(r'\brs\[%d\]' % idx, l)]

def rs_row(mem, v):
    nm = mem[6:]; ch = rs_changed(v); eu = exec_rs_lines(v)
    ul = RSU.get(nm, [])
    if v in RS_TRANSLATED_UNCONSUMED:
        return 'ignored', '%s:%s' % (F['st'], ul[0] if ul else '?'), 'translator computes it; ' + RS_TRANSLATED_UNCONSUMED[v], 'affects(translator)'
    if ch:
        ev = '%s:%d' % (F['st'], ul[0]) if ul else ''
        if eu: ev += ' ; %s:%d' % (F['ex'], eu[0])
        return 'covered', ev, 'read by the translator' + (' and by exec' if eu else ''), 'affects'
    if eu: return 'covered', '%s:%d' % (F['ex'], eu[0]), 'read by exec only', 'no_effect(translator)'
    if v == 7: return 'covered', '', '', ''
    note = 'stored (exec kD9RState) and never read' + ('; referenced in d3d9_state.c only by the constant table and nx_d3d9_default_rs' if not ul else '; referenced in d3d9_state.c:%d but without effect on any output' % ul[0])
    return 'ignored', ln('ex', r'case kD9RState'), note, 'no_effect'

# ---- value enums: uses = (class, state index, explicit values, unconsumed values -> status override)
def U_(cls, idx, explicit, override=None): return (cls, idx, set(explicit), override or {})
UN = {1: 'ignored'}
VALUE_ENUMS = {
 'D3DCMPFUNC': [U_('RS', 23, range(1, 9)), U_('RS', 25, range(1, 9)), U_('RS', 56, range(1, 9)), U_('RS', 189, range(1, 9))],
 'D3DSTENCILOP': [U_('RS', i, range(1, 9)) for i in (53, 54, 55, 186, 187, 188)],
 'D3DBLENDOP': [U_('RS', 171, range(1, 6)), U_('RS', 209, range(1, 6))],
 'D3DBLEND': [U_('RS', i, range(1, 18)) for i in (19, 20, 207, 208)],
 'D3DCULL': [U_('RS', 22, (1, 2, 3))],
 'D3DFILLMODE': [U_('RS', 8, (1, 2, 3))],
 'D3DSHADEMODE': [U_('RS', 9, (1, 2), {1: 'ignored'})],
 'D3DZBUFFERTYPE': [U_('RS', 7, (0, 1))],
 'D3DFOGMODE': [U_('RS', 35, (0, 1, 2, 3)), U_('RS', 140, (), 'ALL_IGNORED')],
 'D3DMATERIALCOLORSOURCE': [U_('RS', i, (), 'ALL_IGNORED') for i in (145, 146, 147, 148)],
 'D3DVERTEXBLENDFLAGS': [U_('RS', 151, (), 'ALL_IGNORED')],
 'D3DPATCHEDGESTYLE': [U_('RS', 163, (), 'ALL_IGNORED')],
 'D3DDEGREETYPE': [U_('RS', 172, (), 'ALL_IGNORED'), U_('RS', 173, (), 'ALL_IGNORED')],
 'D3DDEBUGMONITORTOKENS': [U_('RS', 165, (), 'ALL_IGNORED')],
 'D3DTEXTUREADDRESS': [U_('SP', i, range(1, 6)) for i in (1, 2, 3)],
}
# D3DTEXTUREFILTERTYPE: valid (state, member) pairs per the D3DPTFILTERCAPS_* bit names in shim d3d9caps.h
FILT_VALID = {5: {1, 2, 3, 6, 7, 4, 5}, 6: {1, 2, 3, 6, 7}, 7: {0, 1, 2}}   # MAG, MIN, MIP ; 8 CONVOLUTIONMONO unknown
FILT_EXPL = {5: {1, 2, 3}, 6: {1, 2, 3}, 7: {0, 1, 2}}
CLASS_DOC = {}

def dyn_of(cls, idx, v):
    return (rsv if cls == 'RS' else spv).get((idx, v))

def value_rows():
    """(enum, value) -> (status, evidence, note, dyn) and the per-use rows for the sv file"""
    res, sv = {}, []
    for en, uses in VALUE_ENUMS.items():
        members = sorted(set(r['value'] for r in U if r['enum'] == en and r['kind'] == 'enum'))
        for v in members:
            sts, notes, dyns = [], [], []
            for (cls, idx, expl, ov) in uses:
                d = dyn_of(cls, idx, v)
                sname = ('D3DRS_' if cls == 'RS' else 'D3DSAMP_') + STATE_NAME[(cls, idx)]
                if ov == 'ALL_IGNORED': st = 'ignored'; why = 'state never read'
                elif d is None: st = 'not_reachable_in_code'; why = 'not probed'
                elif d[0] > 0: st = 'unsupported_mark'; why = 'NX_PLAT_UNSUPPORTED(%s) fires' % d[3]
                elif v in expl:
                    st = ov.get(v, 'covered') if isinstance(ov, dict) else 'covered'
                    why = 'explicit arm' if st == 'covered' else 'translated but unconsumed downstream'
                elif d[2]: st = 'fallback_default'; why = 'default arm, output = fallback'
                else: st = 'covered'; why = 'distinct output without an explicit arm (check)'
                if en == 'D3DSHADEMODE' and v == 3: st, why = 'fallback_default', 'PHONG -> smooth (D3D9 itself has no Phong)'
                if en == 'D3DZBUFFERTYPE' and v == 2: st, why = 'fallback_default', 'USEW: depth test on Z, no W-buffer semantics'
                dyn = 'n/a' if d is None else 'marks=%d changed=%d eq_fallback=%d' % (d[0], d[1], d[2])
                sv.append((sname, en, v, st, why, dyn)); sts.append(st); notes.append('%s:%s' % (sname[5:], st)); dyns.append(dyn)
            res[(en, v)] = (worst(sts), ' ; '.join(sorted(set(notes))), '')
    # texture filter
    for v in sorted(set(r['value'] for r in U if r['enum'] == 'D3DTEXTUREFILTERTYPE' and r['kind'] == 'enum')):
        sts, notes = [], []
        for idx in (5, 6, 7):
            d = spv.get((idx, v)); sname = 'D3DSAMP_' + STATE_NAME[('SP', idx)]
            valid = v in FILT_VALID[idx]
            if d is None: st, why = 'not_reachable_in_code', 'not probed'
            elif d[0] > 0: st, why = 'unsupported_mark', 'mark'
            elif v in FILT_EXPL[idx]: st, why = 'covered', 'explicit arm'
            elif d[2]: st, why = 'fallback_default', 'default arm (treated as linear / linear mip)'
            else: st, why = 'covered', 'distinct (check)'
            if v == 8: why += '; validity of CONVOLUTIONMONO for this state unknown (9Ex video filter)'
            sv.append((sname + ('' if valid else ' [invalid use per caps bits]'), 'D3DTEXTUREFILTERTYPE', v, st, why,
                       'n/a' if d is None else 'marks=%d changed=%d eq_fallback=%d' % (d[0], d[1], d[2])))
            if valid or v == 8: sts.append(st); notes.append('%s:%s' % (sname[5:], st))
        res[('D3DTEXTUREFILTERTYPE', v)] = (worst(sts), ' ; '.join(notes), '')
    return res, sv

U = S.universe()
STATE_NAME = {}
for r in U:
    if r['enum'] == 'D3DRENDERSTATETYPE' and r['kind'] == 'enum': STATE_NAME[('RS', r['value'])] = r['member'][6:]
    if r['enum'] == 'D3DSAMPLERSTATETYPE' and r['kind'] == 'enum': STATE_NAME[('SP', r['value'])] = r['member'][8:]
VR, SV = value_rows()

# ---- format -------------------------------------------------------------------------------------------------------
def fcc(s): return sum(ord(c) << (8 * i) for i, c in enumerate(s))
DEPTH_FMT = {70, 71, 73, 75, 77, 79, 80, 82, 83, 84, 85} | {fcc(x) for x in ('INTZ', 'DF16', 'DF24', 'RAWZ')}
VI_FMT = {100, 101, 102}
RT_FMT = {20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 111, 112, 113, 114, 115, 116}
def fmt_role(v, name):
    if v in DEPTH_FMT: return 'D'
    if v in VI_FMT: return 'V'
    if v in (0, 199, 0xFFFFFFFF) or name.endswith('FORCE_DWORD'): return '-'
    return 'RT' if v in RT_FMT else 'T'
def fmt_row(mem, v):
    f = fmt.get(v)
    role = fmt_role(v, mem)
    if v in (101, 102):
        return 'covered', '%s:%d' % (F['ex'], [i + 1 for i, l in enumerate(lines('ex')) if 'format == 102' in l][0]), 'index format: exec IB size (102 -> 4 bytes, else 2); not via nx_d3d9_format', 'role=V'
    if v == 100: return 'not_reachable_in_code', '', 'VERTEXDATA: vertex buffers carry no format in NXD9 (VB record is bytes only)', 'role=V'
    if v == 0: return 'fallback_default', ln('st', r'default: if \(d\) NX_PLAT'), 'UNKNOWN -> NxFmt_None without a mark (if (d) mark)', 'role=-'
    if f is None: return 'not_reachable_in_code', '', 'not probed', 'role=' + role
    if f['fmt'] != 0:    # NxFmt_None == 0 ?
        pass
    none = (f['bytes'] == 0 and f['marks'] > 0) or f['marks'] > 0
    flags = f['flags']
    extra = 'role=%s flags=%s' % (role, (','.join(n for n, b in (('DEPTH', NXFMT_DEPTH), ('RENDER', NXFMT_RENDER), ('CONVERT', NXFMT_CONVERT), ('COMPRESSED', NXFMT_COMPRESSED), ('SRGB', NXFMT_SRGB)) if b and flags & b) or '-'))
    if f['marks'] > 0:
        return 'unsupported_mark', ln('st', r'NX_PLAT_UNSUPPORTED\("D3DFORMAT"'), 'nx_d3d9_format -> NxFmt_None; mark D3DFORMAT fires', extra
    return 'covered', fmt_line(v), 'nx_d3d9_format table', extra

def fmt_line(v):
    L = lines('st'); start = next(i for i, l in enumerate(L) if l.startswith('NxFormatInfo nx_d3d9_format'))
    if v < 1000: pat = r'\bcase %d:' % v
    else:
        c = [chr(v >> (8 * i) & 255) for i in range(4)]
        pat = r"FOURCC\('%s', '%s', '%s', '%s'\)" % tuple(c)
    for i in range(start, len(L)):
        if re.search(pat, L[i]): return '%s:%d' % (F['st'], i + 1)
    return ''

# ---- hand rules for the remaining enums ---------------------------------------------------------------------------
SKIP_FF = lambda why: ('skip_path', ln('ex', r'fixed-function draw'), why)
NR = lambda why: ('not_reachable_in_code', '', why)
SHADER_NOTE = 'shader bytecode is translated outside the census scope (S6b mojoshader->GLSL); d3d9_exec only receives GLSL (kD9VShader/PShader)'
def other_row(en, mem, v, kind):
    if en == 'D3DPRIMITIVETYPE':
        if 1 <= v <= 6: return 'covered', ln('ex', r'case 1: verts64'), 'Draw switch: kPrim[] and vertex count', ''
    if en == 'D3DDECLTYPE':
        if v in (13, 14): return 'fallback_default', ln('ex', r'UDEC3 / DEC3N'), 'Comps()=3 accepts the element, Decode() default arm writes 0,0,0,1 silently', ''
        if v == 17: return 'covered', ln('ex', r'static const int c\[18\]'), 'UNUSED = declaration terminator (Comps 0: element ends the list, not data)', ''
        if 0 <= v <= 16: return 'covered', ln('ex', r'void Decode'), 'Decode()/ElemBytes()/Comps()', ''
    if en == 'D3DDECLMETHOD':
        if v == 0: return 'covered', ln('ex', r'struct Elem'), 'DEFAULT', ''
        return 'ignored', ln('ex', r'struct Elem'), 'Elem.method is stored (DECL record) and never read', ''
    if en == 'D3DDECLUSAGE':
        idx0 = usg.get((v, 0), -1)
        if v == 9: return 'skip_path', ln('ex', r'POSITIONT'), 'POSITIONT: the whole draw is skipped', ''
        if idx0 < 0: return 'fallback_default', ln('ex', r'int AttribLocation'), 'no attribute location (fnvnx_dk_attrib_location = -1): element silently dropped', ''
        n = sum(1 for i in range(16) if usg.get((v, i), -1) >= 0)
        return 'fallback_default' if n < 16 else 'covered', ln('ex', r'int AttribLocation'), 'index 0..%d mapped, %d..15 silently dropped' % (n - 1, n) if n < 16 else 'all indices mapped', 'indices mapped=%d/16' % n
    if en == 'D3DDECLUSAGE.x.INDEX':
        u, i = v // 16, v % 16
        if u == 9: return 'skip_path', ln('ex', r'POSITIONT'), 'POSITIONT: draw skipped', ''
        loc = usg.get((u, i), -1)
        if loc >= 0: return 'covered', ln('ex', r'int AttribLocation'), 'attribute location %d' % loc, ''
        return 'fallback_default', ln('ex', r'int AttribLocation'), 'no location (-1): element silently dropped', ''
    if en == 'D3DCLEAR':
        return 'covered', ln('ex', r'bool Clear\('), 'Clear(): TARGET->colour, ZBUFFER, STENCIL (only when a depth image is bound)', ''
    if en == 'D3DCOLORWRITEENABLE':
        return 'covered', ln('st', r'COLORWRITEENABLE\] & 15'), 'mask & 15 -> NXRS_B_MASK; nxgl.c/nxgpu.c apply the mask per RT', ''
    if en in ('D3DWRAPCOORD', 'D3DWRAP'):
        return 'ignored', ln('ex', r'case kD9RState'), 'D3DRS_WRAP0..15 stored, never read (no wrap of texture coordinates)', ''
    if en == 'D3DCLIPPLANE':
        return 'ignored', ln('st', r'RS_CLIPPLANEENABLE\] & 0xFF'), 'bits reach NXRS_R_CLIP; exec supplies no clip plane equations and nxgpu.c ignores NXRS_R_CLIP', ''
    if en in ('D3DTEXTUREOP', 'D3DTEXTURESTAGESTATETYPE', 'D3DTA', 'D3DTSS_TCI', 'D3DTEXTURETRANSFORMFLAGS'):
        return 'skip_path', ln('ex', r'case kD9TSState'), 'TSS records are dropped (kD9TSState: break); fixed-function draws are skipped (no VS or PS)', ''
    if en in ('D3DTRANSFORMSTATETYPE', 'D3DTRANSFORMSTATETYPE.WORLDMATRIX', 'D3DLIGHTTYPE'):
        return 'skip_path', ln('ex', r'fixed-function draw'), 'no NXD9 tag for transforms/lights/materials; fixed-function draws skipped', ''
    if en in ('D3DFVF', 'D3DFVF_TEXCOORDSIZE'):
        return 'skip_path', ln('ex', r'no vertex declaration \(FVF\)'), 'FVF draws unsupported (d3d9cap.h kD9SetDecl note; Skip "no vertex declaration (FVF)")', ''
    if en == 'D3DFORMAT.VENDOR':
        pass
    if en in ('D3DFORMAT', 'D3DFORMAT.VENDOR'):
        if mem.endswith('FORCE_DWORD'): return 'sentinel', '', '', ''
        return fmt_row(mem, v)
    if en == 'D3DRESOURCETYPE':
        if v in (1, 3, 5, 6, 7): return 'covered', ln('ex', r'case kD9Texture'), {1: 'SURF record', 3: 'TEX record', 5: 'TEX flag 2 / TEXC records', 6: 'VB record', 7: 'IB record'}[v], ''
        return 'skip_path', ln('fr', r'caps2 & 0x200 /\* cubemap'), 'volume resources: no NXD9 tag; front.cpp DDS loader returns false for volume', ''
    if en == 'D3DCUBEMAP_FACES':
        return ('covered', ln('ex', r'case kD9TexCube'), 'TEXC face index 0..5 (face-major regroup in MakeImage)', '') if v < 6 else ('sentinel', '', '', '')
    if en == 'D3DMULTISAMPLE_TYPE':
        return ('covered', ln('ex', r'case kD9Device'), 'NONE: DEV record carries no multisample type (always 1 sample)', '') if v == 0 else NR('no multisample type in NXD9 (DEV/TEX/SURF); MULTISAMPLEANTIALIAS/MASK states stored only') + ('',)
    if en in ('D3DVERTEXTEXTURESAMPLER',):
        return NR('sampler index >= 16 is dropped at kD9SState/kD9SetTex; vertex-stage textures are not bound (BindTextures NxStage_Fragment only)') + ('',)
    if en == 'D3DSTREAMSOURCE':
        return NR('kD9Stream carries stream/vb/offset/stride only; no frequency (instancing) and streams != 0 are dropped') + ('',)
    if en.startswith('D3DSHADER') or en in ('D3DSAMPLER_TEXTURE_TYPE', 'D3DVS_RASTOUT_OFFSETS', 'D3DVS_ADDRESSMODE_TYPE', 'D3DSHADER_COMPARISON'):
        return NR(SHADER_NOTE) + ('',)
    if en in ('D3DUSAGE', 'D3DLOCK', 'D3DPOOL', 'D3DSWAPEFFECT', 'D3DBACKBUFFER_TYPE', 'D3DDEVTYPE', 'D3DQUERYTYPE', 'D3DISSUE', 'D3DGETDATA',
              'D3DSTATEBLOCKTYPE', 'D3DBASISTYPE', 'D3DPRESENTFLAG', 'D3DCS', 'D3DPV', 'D3DCREATE', 'D3DPRESENT', 'D3DENUM', 'D3DSGR',
              'D3DCURSOR', 'D3DSPD', 'D3DDISPLAYROTATION', 'D3DSCANLINEORDERING', 'D3DCOMPOSERECTSOP'):
        return NR('device/API-level argument; NXD9 (d3d9cap.h) has no field for it and the census-scope files never mention it (the guest D3D9 front end in render/nine is outside scope)') + ('',)
    return None

def sentinel(m): return m.endswith('_FORCE_DWORD') or 'FORCE_DWORD' in m

rows, svrows = [], []
for r in U:
    en, mem, v = r['enum'], r['member'], r['value']
    if r['kind'] == 'sentinel': rows.append((r, 'sentinel', '', '', '', '')); continue
    if en == 'D3DRENDERSTATETYPE':
        st, ev, note, dyn = rs_row(mem, v)
        rows.append((r, st, ev, note, dyn, '')); continue
    if en == 'D3DSAMPLERSTATETYPE':
        ch = sp_changed(v); nm = mem[8:]
        if ch:
            ul = [i + 1 for i, l in enumerate(lines('st')) if re.search(r'SAMP_%s\b' % nm, l)]
            rows.append((r, 'covered', '%s:%d' % (F['st'], ul[1] if len(ul) > 1 else ul[0]), 'read by nx_d3d9_sampler; exec: %s' % ln('ex', r'nx_d3d9_sampler\(samp'), 'affects', ''))
        else:
            rows.append((r, 'ignored', ln('ex', r'case kD9SState'), 'stored (kD9SState) and not read by nx_d3d9_sampler', 'no_effect', ''))
        continue
    if en == 'D3DTEXTURESTAGESTATETYPE':
        rows.append((r, 'skip_path', ln('ex', r'case kD9TSState'), 'record dropped (comment in d3d9cap.h: fixed function recorded, unused); 8 stages x state all dropped', 'n/a', '')); continue
    if (en, v) in VR and r['kind'] == 'enum':
        st, ev, note = VR[(en, v)]
        rows.append((r, st, '', 'per use: ' + ev, 'see enum_coverage_sv.tsv', '')); continue
    o = other_row(en, mem, v, r['kind'])
    if o is None: o = ('not_reachable_in_code', '', 'not classified', '')
    if len(o) == 3: o = o + ('',)
    rows.append((r, o[0], o[1], o[2], o[3], ''))

def tsv(path, header, data):
    with open(os.path.join(H, path), 'w') as f:
        f.write('\t'.join(header) + '\n')
        for d in data: f.write('\t'.join(str(x) for x in d) + '\n')

tsv('enum_coverage.tsv', ['enum', 'member', 'value', 'kind', 'status', 'evidence', 'note', 'dynamic'],
    [(r['enum'], r['member'], r['value'], r['kind'], st, ev, note, dyn) for (r, st, ev, note, dyn, _) in rows])
tsv('enum_coverage_sv.tsv', ['state', 'enum', 'value', 'status', 'why', 'dynamic_probe'], SV)

# ---- sampler/TSS pair counts ------------------------------------------------------------------------------------
# ---- skips --------------------------------------------------------------------------------------------------------
EX = lines('ex'); skips = []
for i, l in enumerate(EX):
    for m in re.finditer(r'\bSkip\("([^"]*)"\)', l):
        if 'void Skip' in l: continue
        skips.append(('Skip', '%s:%d' % (F['ex'], i + 1), m.group(1), 'draw skipped (returns true, st->skipped++)', l.strip()[:110]))
for i, l in enumerate(EX):
    for m in re.finditer(r'\bFail\("([^"]*)"', l):
        msg = m.group(1)
        cls = 'unsupported' if re.search(r'unsupported|no GLSL', msg) else ('resource-limit' if re.search(r'arena|limit|overflow|cache full|memory|MemCreate', msg) else 'malformed-input')
        skips.append(('Fail/' + cls, '%s:%d' % (F['ex'], i + 1), msg, 'whole run aborted (returns false)', l.strip()[:110]))
SILENT = [   # (regex, enum/state affected, trigger)
 (r'case kD9TSState: break', 'D3DTSS_* x 8 stages (all)', 'texture stage state record dropped'),
 (r'U\(p, 0\) < NXD3D_RS_COUNT\) rs\[', 'D3DRS_* index >= 210', 'render state record ignored'),
 (r'U\(p, 0\) < 16 && U\(p, 1\) < NXD3D_SAMP_COUNT', 'sampler index >= 16 (vertex samplers 257..260, DMAP 256) or state >= 14', 'sampler state record ignored'),
 (r'case kD9SetTex: if \(n >= 8 && U\(p, 0\) < 16\)', 'sampler index >= 16', 'SetTexture ignored'),
 (r'e\.stream != 0 \|\| loc < 0 \|\| el\.size\(\) >= 8', 'D3DDECLUSAGE (unmapped usage/index: PSIZE, TESSFACTOR, FOG, DEPTH, SAMPLE, idx>0 of single-slot usages), stream != 0, 9th+ element', 'vertex element silently dropped'),
 (r'default: break;   // UDEC3 / DEC3N', 'D3DDECLTYPE_UDEC3, D3DDECLTYPE_DEC3N', 'decoded as (0,0,0,1) silently'),
 (r'cube render target: unsupported', 'cube render-target textures', 'TEX record dropped (sampled as no texture)'),
 (r'case kD9Stream: if \(n >= 16 && U\(p, 0\) == 0\)', 'stream index != 0', 'SetStreamSource ignored'),
 (r'if \(n < 8 \|\| U\(p, 0\) != 0\) break', 'render target index != 0 (MRT)', 'SetRenderTarget ignored'),
 (r'if \(n < 20\) return true', 'D3DCLEAR (short record)', 'Clear ignored'),
 (r'default: break;\s*$', 'unknown NXD9 tag (queries, transforms, lights, materials, FVF, stream frequency, ...)', 'record ignored'),
 (r'if \(!nr\) \|\| a2 <= a1|if \(a2 <= a1 \|\| b2 <= b1 \|\| !nf\) continue', 'D3DCLEAR rect empty / flags without target and bound depth', 'clear rect ignored'),
 (r'vertex declaration|kD9SetDecl', '', ''),
]
for pat, aff, trig in SILENT[:-1]:
    hits = [i + 1 for i, l in enumerate(EX) if re.search(pat, l)]
    if pat.startswith("default: break;\\s"): hits = hits[-1:]
    if not hits: skips.append(('silent-drop', F['ex'] + ':?', trig, aff, pat)); continue
    skips.append(('silent-drop', '%s:%d' % (F['ex'], hits[0]), trig, aff, EX[hits[0] - 1].strip()[:110]))
for pat, aff, trig in ((r'Skip\(const char', 'counter', 'definition (st->skipped++; firstSkip)'),):
    pass
tsv('enum_skips.tsv', ['kind', 'site', 'trigger', 'effect_or_affected', 'source'], skips)

# ---- summary ------------------------------------------------------------------------------------------------------
cnt = collections.OrderedDict()
for (r, st, ev, note, dyn, _) in rows:
    c = cnt.setdefault(r['enum'], collections.Counter()); c[st] += 1; c['total'] += 1
cols = ['total', 'covered', 'ignored', 'fallback_default', 'unsupported_mark', 'skip_path', 'not_reachable_in_code', 'sentinel']
tsv('enum_summary.tsv', ['enum'] + cols, [(en,) + tuple(c[x] for x in cols) for en, c in cnt.items()])
# mismatch report: static covered but dynamic marks, or covered value whose output equals the fallback and has no explicit arm
mm = [s for s in SV if s[3] == 'covered' and 'marks=' in s[5] and not s[5].startswith('marks=0') and s[5] != 'n/a']
open(os.path.join(W, 'enum_mismatch.txt'), 'w').write('\n'.join(map(str, mm)) + '\n')
print('rows', len(rows), 'skip rows', len(skips), 'Skip calls', sum(1 for s in skips if s[0] == 'Skip'), 'mismatches', len(mm))

# ---- README -------------------------------------------------------------------------------------------------------
def cnt_of(names, pred=None):
    c = collections.Counter()
    for (r, st, ev, note, dyn, _) in rows:
        if r['enum'] in names and st != 'sentinel' and (pred is None or pred(r)): c[st] += 1; c['n'] += 1
    return c
def line(label, c):
    return '%-34s %4d  %4d %4d %4d %4d %4d %4d' % (label, c['n'], c['covered'], c['ignored'], c['fallback_default'], c['unsupported_mark'], c['skip_path'], c['not_reachable_in_code'])
rsig = [r for (r, st, ev, note, dyn, _) in rows if r['enum'] == 'D3DRENDERSTATETYPE' and st == 'ignored']
absent = [r['member'] for (r, st, ev, note, dyn, _) in rows if r['enum'] == 'D3DRENDERSTATETYPE' and 'never read; referenced in d3d9_state.c only' in note]
unc = [r['member'] for (r, st, ev, note, dyn, _) in rows if r['enum'] == 'D3DRENDERSTATETYPE' and note.startswith('translator computes it')]
def role_cnt(role):
    c = collections.Counter()
    for (r, st, ev, note, dyn, _) in rows:
        if r['enum'] in ('D3DFORMAT', 'D3DFORMAT.VENDOR') and ('role=%s ' % role in dyn + ' ' or dyn == 'role=' + role) and st != 'sentinel':
            c[st] += 1; c['n'] += 1
    return c
all_listed = set()
L = []
L.append('# L6 R11 enum census (measurement only). Rebuild ./enum_run.sh. Universe = d3d9types.h (+d3d9.h flags). Columns: members(no FORCE_DWORD) covered ignored fallback mark skip notreach')
L.append('Scope: translator d3d9_state.c + exec d3d9_exec.cpp + front/gl/host consumers. Not read: render/nine (guest D3D9 front end), shader-side use of the exec UBO.')
L.append('```')
groups = [
 ('D3DRENDERSTATETYPE', ['D3DRENDERSTATETYPE']), ('D3DSAMPLERSTATETYPE', ['D3DSAMPLERSTATETYPE']),
 ('D3DTEXTURESTAGESTATETYPE', ['D3DTEXTURESTAGESTATETYPE']), ('D3DTEXTUREOP', ['D3DTEXTUREOP']),
 ('D3DTA + D3DTSS_TCI + TEXTURETRANSFORMFLAGS', ['D3DTA', 'D3DTSS_TCI', 'D3DTEXTURETRANSFORMFLAGS']),
 ('D3DDECLTYPE', ['D3DDECLTYPE']), ('D3DDECLUSAGE (member)', ['D3DDECLUSAGE']), ('D3DDECLUSAGE x index 0..15', ['D3DDECLUSAGE.x.INDEX']),
 ('D3DDECLMETHOD', ['D3DDECLMETHOD']), ('D3DPRIMITIVETYPE', ['D3DPRIMITIVETYPE']), ('D3DFVF bits + TEXCOORDSIZE', ['D3DFVF', 'D3DFVF_TEXCOORDSIZE']),
 ('D3DTRANSFORMSTATETYPE (10 + 256 WORLD)', ['D3DTRANSFORMSTATETYPE', 'D3DTRANSFORMSTATETYPE.WORLDMATRIX']),
 ('D3DLIGHTTYPE', ['D3DLIGHTTYPE']), ('D3DMATERIALCOLORSOURCE', ['D3DMATERIALCOLORSOURCE']),
 ('D3DFOGMODE (table fog: all covered)', ['D3DFOGMODE']), ('D3DCLIPPLANE bits', ['D3DCLIPPLANE']),
 ('D3DFILLMODE + D3DCULL', ['D3DFILLMODE', 'D3DCULL']), ('D3DSHADEMODE', ['D3DSHADEMODE']),
 ('D3DCMPFUNC', ['D3DCMPFUNC']), ('D3DSTENCILOP', ['D3DSTENCILOP']), ('D3DBLEND', ['D3DBLEND']), ('D3DBLENDOP', ['D3DBLENDOP']),
 ('D3DTEXTUREADDRESS', ['D3DTEXTUREADDRESS']), ('D3DTEXTUREFILTERTYPE', ['D3DTEXTUREFILTERTYPE']), ('D3DZBUFFERTYPE', ['D3DZBUFFERTYPE']),
 ('D3DCOLORWRITEENABLE bits', ['D3DCOLORWRITEENABLE']), ('D3DWRAPCOORD+D3DWRAP bits', ['D3DWRAPCOORD', 'D3DWRAP']), ('D3DCLEAR flags', ['D3DCLEAR']),
 ('D3DMULTISAMPLE_TYPE', ['D3DMULTISAMPLE_TYPE']), ('D3DSWAPEFFECT + D3DBACKBUFFER_TYPE', ['D3DSWAPEFFECT', 'D3DBACKBUFFER_TYPE']),
 ('D3DLOCK + D3DUSAGE flags', ['D3DLOCK', 'D3DUSAGE']), ('D3DQUERYTYPE + D3DSTATEBLOCKTYPE', ['D3DQUERYTYPE', 'D3DSTATEBLOCKTYPE']),
 ('D3DRESOURCETYPE', ['D3DRESOURCETYPE']), ('D3DPOOL', ['D3DPOOL']), ('D3DCUBEMAP_FACES', ['D3DCUBEMAP_FACES']),
 ('D3DPATCHEDGESTYLE+DEGREETYPE+BASISTYPE', ['D3DPATCHEDGESTYLE', 'D3DDEGREETYPE', 'D3DBASISTYPE']),
 ('D3DVERTEXBLENDFLAGS + DEBUGMONITORTOKENS', ['D3DVERTEXBLENDFLAGS', 'D3DDEBUGMONITORTOKENS']),
]
for lab, names in groups:
    all_listed |= set(names); L.append(line(lab, cnt_of(set(names))))
L.append(line('D3DFORMAT texture-role (T+RT)', role_cnt('T') + role_cnt('RT')))
L.append(line('D3DFORMAT render-target-role', role_cnt('RT')))
L.append(line('D3DFORMAT depth-role (+INTZ/DF16/DF24/RAWZ)', role_cnt('D')))
L.append(line('D3DFORMAT vertex/index-role', role_cnt('V')))
rest = collections.Counter()
for (r, st, ev, note, dyn, _) in rows:
    if r['enum'] not in all_listed and not r['enum'].startswith('D3DFORMAT') and st != 'sentinel': rest[st] += 1; rest['n'] += 1
L.append(line('other: shader tokens, caps/create/present flags', rest))
L.append('```')
L.append('RS detail: %d of %d D3DRS_* change translated output and reach a consumer; %d ignored = %d translated but unconsumed (%s) + %d stored, never read.' % (
    cnt_of({'D3DRENDERSTATETYPE'})['covered'], cnt_of({'D3DRENDERSTATETYPE'})['n'], cnt_of({'D3DRENDERSTATETYPE'})['ignored'], len(unc),
    ','.join(m[6:] for m in unc), len(absent)))
L[-1] += (' Point sprites/scale: 9 states (POINTSIZE/MIN/MAX, SPRITEENABLE, SCALEENABLE, SCALE_A..C) all ignored; N-patch/tessellation: 9 states ignored; user clip planes: no plane data path.')
L.append('Sampler: 13 states x 16 = 208 pairs, 176 covered, 32 ignored (ELEMENTINDEX, DMAPOFFSET). TSS: 18 x 8 stages = 144 pairs, all skip_path. Fixed function absent: every draw without VS+PS is skipped.')
L.append('Skip/Fail: enum_skips.tsv = %d Skip( sites, %d Fail( sites (2 unsupported, 18 limit, 32 malformed), %d silent-drop sites. Probe: %d cases, %d static/dynamic mismatches. Per state x value: enum_coverage_sv.tsv.' % (
    sum(1 for s in skips if s[0] == 'Skip'), sum(1 for s in skips if s[0].startswith('Fail')), sum(1 for s in skips if s[0] == 'silent-drop'), len(rsv) + len(spv) + len(fmt), len(mm)))
L.append('Undetermined: RT-role format list is from D3D9 docs memory; CONVOLUTIONMONO validity per sampler state; whether WRAPn applies to shader texcoords; shader-side handling of exec UBO.')
open(os.path.join(H, 'enum_README.md'), 'w').write('\n'.join(L) + '\n')
print('README lines', len(L))
