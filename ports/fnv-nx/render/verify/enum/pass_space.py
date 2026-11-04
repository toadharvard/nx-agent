#!/usr/bin/env python3
# V6n (D14 verifier): the FNV render-pass space = emitted x configured, keyed by the TRUE pass ids
# (pass_names.py: the binary's pass-name function FUN_00b4f9d0, not the decomp's enum header).
#
#   python3 -I render/verify/enum/pass_space.py <fnv_decomp> <out dir>      (<out> outside git, e.g. build/v6n)
#
# Output (decomp-derived, never commit): <out>/pass_space.tsv (one row per pass id), <out>/emit_sites.tsv,
# <out>/overrides.tsv; stdout: counts per family only (these may go into docs).
#
# Sources and how each is read:
#  * names     FUN_00b4f9d0 case labels (742) + the hole fill of pass_names.py (header neighbour of the same run).
#  * emitted   every 8-argument call `x->Add(geom, PASS, first, nlights, l0..l3)` / `x->FUN_00ba9ee0(...)`
#              (BSShaderProperty::ShaderPassList::Add). PASS literal, or an expression resolved locally: C ternaries
#              and `+`, free boolean terms enumerated over {0,1}, one level of local `v = <expr>;` substitution.
#              Sites whose id comes from a parameter / table stay "unresolved" and are counted per function.
#  * configured
#     - ShadowLightShader: global NiD3DPass* array DAT_011fdff8[759] (pass id = (addr-0x011fdff8)/4). In every
#       function: `p = DAT_<pass>` / `DAT_<pass>->` / `DAT_011fdff8[N]` sets the current pass; then VS
#       (`vertexShader=`, `m_5c=`, `SetTex0x5c()`), PS (`pixelShader=`, `m_44=`, `SetTex0x44()`), render states
#       (`SetNodeValue(rs, v, ..)`, `SetRenderState(rs, v)`), sampler stages (`ConfigureBeamStage(..)` args) and the
#       per-pass int arrays 011fcc80 (flags, 0x800 = skinned), 011fc0a0, 011fb4c0, 011fa8e0 (writes `DAT_x = v`).
#       VS refs -> SLS1%03i.vso (DAT_011fdd88[31], dev table entry 125+i), SLS1S%03i.vso (011fde04[22], 103+i),
#       SLS2%03i.vso (011fde5c[103], i); PS refs -> SLS1%03i.pso (011fda48[0..47]) / SLS2%03i.pso (011fdb08[0..159]),
#       with the HLSL source + defines of the developer tables (VS: shaderEntries[k].fNN, PS: shaderModel{1,2}Defs).
#     - Lighting30: `index = pass - 376` + the special cases of its pass->index switch (3X block and 3XWATER_DEPTH).
#     - other classes: the id is a `case N:` label in a function of that class's source file(s) (pass dispatch).
#  * overrides functions that index the SLS pass array with a non-literal and set render state at render time.
import os, re, sys, collections, itertools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pass_names as PN

PASS_LO, NPASS = 0x011fdff8, 759
INT_ARRAYS = {0x011fcc80: 'flags', 0x011fc0a0: 'a2', 0x011fb4c0: 'a3', 0x011fa8e0: 'a4'}
VS_ARR = [(0x011fdd88, 31, 'SLS1%03i.vso', 125), (0x011fde04, 22, 'SLS1S%03i.vso', 103), (0x011fde5c, 103, 'SLS2%03i.vso', 0)]
PS_ARR = [(0x011fda48, 48, 'SLS1%03i.pso', 'ps1'), (0x011fda48 + 48 * 4, 160, 'SLS2%03i.pso', 'ps2')]
RS = {7: 'ZENABLE', 8: 'FILLMODE', 9: 'SHADEMODE', 14: 'ZWRITEENABLE', 15: 'ALPHATESTENABLE', 19: 'SRCBLEND',
      20: 'DESTBLEND', 22: 'CULLMODE', 23: 'ZFUNC', 24: 'ALPHAREF', 25: 'ALPHAFUNC', 27: 'ALPHABLENDENABLE',
      28: 'FOGENABLE', 52: 'STENCILENABLE', 53: 'STENCILFAIL', 54: 'STENCILZFAIL', 55: 'STENCILPASS',
      56: 'STENCILFUNC', 57: 'STENCILREF', 58: 'STENCILMASK', 59: 'STENCILWRITEMASK', 168: 'COLORWRITEENABLE',
      171: 'BLENDOP', 174: 'SCISSORTESTENABLE', 175: 'SLOPESCALEDEPTHBIAS', 185: 'TWOSIDEDSTENCILMODE',
      194: 'SRGBWRITEENABLE', 195: 'DEPTHBIAS', 206: 'SEPARATEALPHABLENDENABLE', 207: 'SRCBLENDALPHA',
      208: 'DESTBLENDALPHA', 209: 'BLENDOPALPHA', 190: 'COLORWRITEENABLE1', 160: 'POINTSPRITEENABLE',
      29: 'SPECULARENABLE', 60: 'TEXTUREFACTOR', 193: 'BLENDFACTOR'}

# family -> shader class by true-name prefix (first match wins)
FAMILY = [('3X', 'Lighting30'), ('GRASS', 'TallGrass'), ('LEAVES', 'SpeedTreeLeaf'), ('FRONDS', 'SpeedTreeFrond'),
          ('DISTANT_TREE', 'DistantTree'), ('SKY', 'Sky'), ('SELFILLUM_SKY', 'Sky'), ('WATER_DEPTH', 'SLS-waterdepth'),
          ('NLWATER_DEPTH', 'NoLighting-waterdepth'), ('WATER', 'Water'), ('NOLIGHTING', 'NoLighting'),
          ('PARTICLE', 'Particle'), ('PRECIPITATION', 'Precipitation'), ('BOLT', 'Bolt'), ('BEAM', 'Beam'),
          ('GEOMDECAL', 'GeometryDecal'), ('BLOOD', 'BloodSplatter'), ('TILE', 'Tile'), ('IMAGESPACE', 'ImageSpace'),
          ('VFOG', 'VolumetricFog'), ('SHADOWVOLUME', 'ShadowVolume'), ('LANDLO', 'DistantLOD'),
          ('LANDO_Am', 'DistantLOD'), ('ADT_OptLOD', 'DistantLOD'), ('', 'ShadowLight')]
CLASS_FILES = {'TallGrass': ['tallgrassshader'], 'SpeedTreeLeaf': ['speedtreeleafshader'],
               'SpeedTreeFrond': ['speedtreefrondshader', 'debugshader'], 'DistantTree': ['bsdistanttreeshader'],
               'Sky': ['skyshader'], 'Water': ['watershader'], 'NoLighting': ['bsshadernolighting'],
               'NoLighting-waterdepth': ['bsshadernolighting'], 'Particle': ['particleshader'],
               'Precipitation': ['precipitationshader', 'bsshaderdata'], 'Bolt': ['boltshader'], 'Beam': ['beamshader'],
               'GeometryDecal': ['geometrydecalshader'], 'BloodSplatter': ['bsshaderbloodsplatter'],
               'Tile': ['tileshader'], 'ImageSpace': ['imagespace'], 'VolumetricFog': ['volumetricfogshader'],
               'ShadowVolume': ['shadowscene'], 'DistantLOD': ['distantlodshader']}

def cls_of(name):
    for pre, c in FAMILY:
        if name.startswith(pre):
            return c
    return 'ShadowLight'

def functions(src):
    """yield (relpath, addr, text) for every @fnv function in every .cpp"""
    fre = re.compile(r'^// @fnv (0x[0-9a-fA-F]+)', re.M)
    for dp, dn, fn in os.walk(src):
        for f in sorted(fn):
            if not f.endswith('.cpp'):
                continue
            p = os.path.join(dp, f)
            t = open(p, errors='replace').read()
            ms = list(fre.finditer(t))
            for i, m in enumerate(ms):
                end = ms[i + 1].start() if i + 1 < len(ms) else len(t)
                yield os.path.relpath(p, src), int(m.group(1), 16), t[m.start():end]

def split_args(s, i):
    """args of the call whose '(' is at s[i]; returns (list, end) or (None, i)"""
    depth, cur, out, j = 0, '', [], i
    while j < len(s):
        ch = s[j]
        if ch in '([':
            depth += 1
            if depth > 1:
                cur += ch
        elif ch in ')]':
            depth -= 1
            if depth == 0:
                out.append(cur.strip())
                return out, j
            cur += ch
        elif ch == ',' and depth == 1:
            out.append(cur.strip()); cur = ''
        else:
            cur += ch
        j += 1
    return None, i

def c2py(e):
    e = re.sub(r'[\w>\-.]+\([^()]*\)', '_call', e)
    e = re.sub(r'\((?:unsigned\s+)?(?:short|int|char|bool|long)\)', '', e)
    e = re.sub(r'\b(0x[0-9a-fA-F]+|\d+)[uUlL]+\b', r'\1', e)
    e = e.replace('!=', ' != ').replace('&&', ' and ').replace('||', ' or ')
    e = re.sub(r'!(?!=)', ' not ', e)
    e = e.replace('false', '0').replace('true', '1')
    # ternary a ? b : c  ->  (b if a else c), innermost first
    for _ in range(8):
        m = re.search(r'([^?:()]+)\?([^?:()]+):([^?:()]+)', e)
        if not m:
            break
        e = e[:m.start()] + '((%s) if (%s) else (%s))' % (m.group(2), m.group(1), m.group(3)) + e[m.end():]
    return e

def resolve_var(n, text, depth):
    vals = set()
    for m in re.finditer(r'(?<![\w.>])%s\s*=\s*([^;=][^;]*);' % re.escape(n), text):
        r = resolve(m.group(1), text, depth + 1)
        if r is None:
            return None
        vals |= r
    if not vals:
        return None
    for m in re.finditer(r'(?<![\w.>])%s\s*\+=\s*([^;]+);' % re.escape(n), text):
        r = resolve(m.group(1), text, depth + 1)
        if r is None:
            return None
        vals = {v + w for v in vals for w in r}
    return vals

def resolve(expr, text, depth=0):
    """set of int values, or None when the id is not local (parameter, table, call result)"""
    e = expr.strip()
    try:
        return {int(e, 0)}
    except ValueError:
        pass
    py = c2py(e)
    names = sorted(set(re.findall(r'(?<![\w.])[A-Za-z_][\w.>\-]*(?:\([^()]*\))?', py)) - {'if', 'else', 'and', 'or', 'not'})
    if len(names) > 4:
        return None
    dom = []
    for n in names:
        r = resolve_var(n, text, depth) if depth < 3 and re.fullmatch(r'[A-Za-z_]\w*', n) else None
        if r is None:
            if re.match(r'(entry|item|p|pass|id|code|aus|us|ui|i|usPassType|passType|passEnum)$', n):
                return None
            r = {0, 1}          # a free boolean term (flag, skin, cond ? : , call result used as a condition)
        dom.append(sorted(r))
    vals = set()
    for combo in itertools.product(*dom):
        q = py
        for n, v in sorted(zip(names, combo), key=lambda x: -len(x[0])):
            q = re.sub(r'(?<![\w.])' + re.escape(n) + r'(?![\w])', str(v), q)
        try:
            vals.add(int(eval(q, {'__builtins__': {}}, {})))
        except Exception:
            return None
    return vals

def main():
    dec, out = sys.argv[1], sys.argv[2]
    src = os.path.join(dec, 'src') if os.path.isdir(os.path.join(dec, 'src')) else dec
    hdr, _ = PN.header_enum(os.path.join(src, 'bsshader/bsshader_shader_layout.h'))
    pairs = PN.name_fn(os.path.join(src, 'bsshader/bsshadermanager.cpp'))
    name = {v: n for v, n in pairs}
    named_ids = set(name)
    # hole fill: header neighbour with the offset of the run the neighbour belongs to
    tn = {n: v for v, n in pairs}
    for n, hv in hdr.items():
        if n in tn or n == 'DISABLED':
            continue
        for d in (1, -1, 2, -2, 3, -3):
            nb = [m for m, v in hdr.items() if v == hv + d and m in tn]
            if nb:
                tid = hv + tn[nb[0]] - hdr[nb[0]]
                if tid not in name and 0 < tid < NPASS:
                    name[tid] = n + '*'      # '*' = inferred from the header neighbour
                break
    if 'IMAGESPACE' in hdr and NPASS - 1 not in name:
        name[NPASS - 1] = 'IMAGESPACE*'
    for i in range(1, NPASS):
        name.setdefault(i, '?%d' % i)
    funcs = list(functions(src))

    # ---- emission
    emit = collections.defaultdict(list)
    unresolved = collections.Counter()
    sites = []
    call = re.compile(r'(?:->|\.)(?:Add|FUN_00ba9ee0)\s*\(')
    for rel, addr, text in funcs:
        if addr == 0x00ba9ee0:
            continue
        for m in call.finditer(text):
            args, _ = split_args(text, m.end() - 1)
            if not args or len(args) != 8:
                continue
            vals = resolve(args[1], text)
            if vals is None:
                unresolved['%s 0x%08x' % (rel, addr)] += 1
                sites.append((rel, addr, args[1], 'unresolved'))
                continue
            vals = {v for v in vals if 0 < v < NPASS}
            kind = 'literal' if re.fullmatch(r'\s*(0x[0-9a-fA-F]+|\d+)\s*', args[1]) else 'computed'
            for v in vals:
                emit[v].append((rel, addr, kind))
            sites.append((rel, addr, args[1], ' '.join(map(str, sorted(vals)))))

    # ---- SLS configuration
    def pass_of(a):
        return (a - PASS_LO) // 4 if PASS_LO <= a < PASS_LO + NPASS * 4 and (a - PASS_LO) % 4 == 0 else None
    def shader_ref(s, arrs):
        m = re.search(r'DAT_([0-9a-fA-F]{8})\s*(?:\[\s*(\d+)\s*\])?', s)
        if not m:
            return s.strip()
        a = int(m.group(1), 16) + 4 * int(m.group(2) or 0)
        for base, n, fmt, extra in arrs:
            if base <= a < base + 4 * n:
                return fmt % ((a - base) // 4)
        return 'DAT_%08x' % a
    cfg = collections.defaultdict(lambda: {'vs': set(), 'ps': set(), 'rs': {}, 'stage': [], 'int': {}, 'fn': set()})
    overrides = []
    for rel, addr, text in funcs:
        cur = None
        alias = {}
        for line in text.split('\n'):
            l = line.strip()
            if l.startswith('extern') or l.startswith('//'):
                continue
            m = re.match(r'(?:[\w:<>\*\s]+\s)?(\w+)\s*=\s*DAT_([0-9a-fA-F]{8})\s*;', l)
            if m and pass_of(int(m.group(2), 16)) is not None:
                alias[m.group(1)] = cur = pass_of(int(m.group(2), 16))
                cfg[cur]['fn'].add(addr)
                continue
            m = re.search(r'DAT_011fdff8\s*\[\s*(\d+|0x[0-9a-fA-F]+)\s*\]', l) or None
            if m:
                cur = int(m.group(1), 0); cfg[cur]['fn'].add(addr)
            elif re.search(r'DAT_011fdff8\s*\[\s*[A-Za-z_]', l) and re.search(r'SetProperty|SetRenderState|SetNodeValue|FUN_00b71a10', l):
                overrides.append((rel, addr, l[:160]))
            m = re.search(r'DAT_([0-9a-fA-F]{8})\s*->', l)
            if m and pass_of(int(m.group(1), 16)) is not None:
                cur = pass_of(int(m.group(1), 16)); cfg[cur]['fn'].add(addr)
            m = re.match(r'(\w+)\s*->', l)
            if m and m.group(1) in alias:
                cur = alias[m.group(1)]
            # int arrays
            m = re.match(r'DAT_([0-9a-fA-F]{8})\s*=\s*([^;]+);', l)
            if m:
                a = int(m.group(1), 16)
                for base, nm in INT_ARRAYS.items():
                    if base <= a < base + 4 * NPASS and (a - base) % 4 == 0:
                        cfg[(a - base) // 4]['int'][nm] = m.group(2).strip()
                        cfg[(a - base) // 4]['fn'].add(addr)
            if cur is None:
                continue
            m = re.search(r'(?:->(?:vertexShader|m_5c|m_pVertexShader)\s*=\s*([^;]+);|SetTex0x5c\s*\(([^;]+)\)\s*;)', l)
            if m:
                cfg[cur]['vs'].add(shader_ref(m.group(1) or m.group(2), VS_ARR))
            m = re.search(r'(?:->(?:pixelShader|m_44|m_pPixelShader)\s*=\s*([^;]+);|SetTex0x44\s*\(([^;]+)\)\s*;)', l)
            if m:
                cfg[cur]['ps'].add(shader_ref(m.group(1) or m.group(2), PS_ARR))
            m = re.search(r'(?:SetNodeValue|SetRenderState)\s*\(\s*(0x[0-9a-fA-F]+|\d+)\s*,\s*([^,)]+)', l)
            if m:
                rs = int(m.group(1), 0)
                cfg[cur]['rs'][RS.get(rs, 'RS%d' % rs)] = m.group(2).strip()
            m = re.search(r'ConfigureBeamStage\s*\(\s*\w+(?:\.object)?\s*,([^;]*)\)\s*;', l)
            if m:
                cfg[cur]['stage'].append(re.sub(r'\s+', '', m.group(1)))

    # ---- SLS developer tables (HLSL source + defines)
    sls = next(t for r, a, t in funcs if r.endswith('shadowlightshader.cpp') and 'shaderEntries[125]' in t)
    vsdev = collections.defaultdict(dict)
    for m in re.finditer(r'shaderEntries\[(\d+)\]\.f(\d\d)\s*=\s*("(?:[^"\\]|\\.)*"|0)\s*;', sls):
        vsdev[int(m.group(1))][int(m.group(2))] = m.group(3).strip('"')
    def vs_desc(b):
        for base, n, fmt, off in VS_ARR:
            for i in range(n):
                if b == fmt % i:
                    e = vsdev.get(off + i, {})
                    f = [e.get(k, '0') for k in range(19)]
                    if f[0] in ('0', ''):
                        return 'unset'
                    defs = []
                    for k in range(1, 19, 2):
                        if f[k] in ('0', ''):
                            break
                        defs.append(f[k] + ('=' + f[k + 1] if f[k + 1] not in ('', '0') else ''))
                    return f[0].replace('\\\\', '/') + ' ' + ','.join(defs)
        return ''
    psfn = next((t for r, a, t in funcs if 'shaderModel2Defs[160]' in t), '')
    strs = dict(re.findall(r'extern const char (\w+)\[\];\s*//\s*"((?:[^"\\]|\\.)*)"', open(os.path.join(src, 'bsshader/shadowlightshader.cpp'), errors='replace').read()))
    psdev = {}
    for tab, key in (('shaderModel1Defs', 'SLS1%03i.pso'), ('shaderModel2Defs', 'SLS2%03i.pso')):
        m = re.search(tab + r'\[\d+\]\[19\]\s*=\s*\{', psfn)
        if not m:
            continue
        j = m.end(); depth = 1; k = j
        while depth and k < len(psfn):
            depth += {'{': 1, '}': -1}.get(psfn[k], 0); k += 1
        rows = re.findall(r'\{([^{}]*)\}', psfn[j:k - 1])
        for i, r in enumerate(rows):
            items = [x.strip() for x in r.split(',') if x.strip()]
            if not items or items[0] == '0':
                continue
            s = [strs.get(x, x) for x in items if x != '0']
            src_ = s[0].replace('\\\\', '/')
            defs = [s[k] + ('=' + s[k + 1] if k + 1 < len(s) and s[k + 1] else '') for k in range(1, len(s), 2)]
            psdev[key % i] = src_ + ' ' + ','.join(defs)

    # ---- Lighting30 pass -> local index
    l30 = {}
    for r, a, t in funcs:
        if r.endswith('lighting30shader.cpp') and 'index = pass - 376' in t:
            for m in re.finditer(r'case (\d+):\s*index = (\d+);', t):
                l30[int(m.group(1))] = int(m.group(2))
            break
    # ---- other classes: ids their own files dispatch on (case labels, ==, range checks a<=x<=b, x-a<=c)
    caselab = collections.defaultdict(set)
    for r, a, t in funcs:
        b = os.path.basename(r)
        for c, pref in CLASS_FILES.items():
            if not any(b.startswith(p) for p in pref):
                continue
            S = caselab[c]
            num = r'(0x[0-9a-fA-F]+|\d+)'
            for m in re.finditer(r'case\s+' + num + r'\s*:', t):
                S.add(int(m.group(1), 0))
            for m in re.finditer(r'==\s*' + num + r'\b', t):
                S.add(int(m.group(1), 0))
            for m in re.finditer(r'(\w+)\s*>=\s*' + num + r'\s*&&\s*\1\s*<=\s*' + num, t):
                S.update(range(int(m.group(2), 0), int(m.group(3), 0) + 1))
            for m in re.finditer(r'(\w+)\s*-\s*' + num + r'\s*(<=|<)\s*' + num, t):
                lo, n = int(m.group(2), 0), int(m.group(4), 0)
                S.update(range(lo, lo + n + (1 if m.group(3) == '<=' else 0)))
    # ---- accumulator render groups: RenderPassGroup(first, last, ...) literal ranges = the ids the frame renders
    groups = set()
    for r, a, t in funcs:
        for m in re.finditer(r'RenderPassGroup\s*\(\s*(0x[0-9a-fA-F]+|\d+)\s*,\s*(0x[0-9a-fA-F]+|\d+)\s*,', t):
            groups.add((int(m.group(1), 0), int(m.group(2), 0)))
    in_group = set(i for lo, hi in groups for i in range(lo, hi + 1))
    # Water: member array spRenderPass[74] (InitWaterPasses); the accumulator renders it as 0x288..0x2ce; base 648
    water_arr = set(range(648, 648 + 74))
    # ---- join
    os.makedirs(out, exist_ok=True)
    fam = collections.defaultdict(lambda: collections.Counter())
    rows = []
    famsize = collections.Counter(cls_of(name[i].rstrip('*')) for i in range(1, NPASS))
    caselab_hit = {c: any(cls_of(name[i].rstrip('*')) == c for i in caselab[c] if 0 < i < NPASS) for c in CLASS_FILES}
    for i in range(1, NPASS):
        n = name[i]; c = cls_of(n.rstrip('*'))
        e = emit.get(i, [])
        g = cfg.get(i)
        if c == 'Lighting30':
            conf = 376 <= i <= 479 or i in l30
            how = 'l30-index %d' % (l30[i] if i in l30 else i - 376) if conf else ''
        elif c == 'Water' and i in water_arr:
            conf, how = True, 'class-array(base 648, inferred)'
        elif c in CLASS_FILES:
            conf = i in caselab[c] or bool(g and (g['vs'] or g['ps'] or g['rs']))
            how = 'case-label' if i in caselab[c] else ('sls-array' if conf else '')
            if not conf and famsize[c] <= 2 and not caselab_hit[c]:
                conf, how = True, 'single-pass class (inferred)'
        else:
            conf = bool(g and (g['vs'] or g['ps'] or g['rs'] or g['int']))
            how = 'sls-array' if conf else ''
        if c == 'ShadowLight' and g and (g['vs'] or g['ps'] or g['rs']):
            how = 'sls-array'
        f = fam[c]
        f['declared'] += 1
        f['named'] += i in named_ids
        f['configured'] += conf
        f['emitted'] += bool(e)
        f['reachable'] += bool(e) and conf and i in in_group
        f['rendered'] += i in in_group
        vs = sorted(g['vs']) if g else []
        ps = sorted(g['ps']) if g else []
        rows.append([i, n, c, int(i in in_group), len(e), ','.join(sorted(set(k for _, _, k in e))), how,
                     ' '.join(vs), ' | '.join(vs_desc(v) for v in vs), ' '.join(ps), ' | '.join(psdev.get(p, '') for p in ps),
                     ' '.join('%s=%s' % kv for kv in sorted(g['rs'].items())) if g else '',
                     ' '.join(g['stage']) if g else '',
                     ' '.join('%s=%s' % kv for kv in sorted(g['int'].items())) if g else '',
                     'skinned' if g and g['int'].get('flags') in ('0x800', '2048') else '',
                     'Vc' if 'Vc' in n or 'VC' in n else ''])
    with open(os.path.join(out, 'pass_space.tsv'), 'w') as fo:
        fo.write('\t'.join(['id', 'name', 'class', 'in_render_group', 'emit_sites', 'emit_kind', 'configured_by', 'vs', 'vs_src_defines',
                            'ps', 'ps_src_defines', 'render_states', 'stages', 'int_arrays', 'skin', 'vc']) + '\n')
        for r in rows:
            fo.write('\t'.join(map(str, r)) + '\n')
    with open(os.path.join(out, 'emit_sites.tsv'), 'w') as fo:
        for s in sites:
            fo.write('%s\t0x%08x\t%s\t%s\n' % s)
    with open(os.path.join(out, 'overrides.tsv'), 'w') as fo:
        for o in overrides:
            fo.write('%s\t0x%08x\t%s\n' % o)
    tot = collections.Counter()
    print('%-22s %8s %6s %10s %7s %9s %9s' % ('class', 'declared', 'named', 'configured', 'emitted', 'in-group', 'reachable'))
    for c in sorted(fam, key=lambda c: -fam[c]['declared']):
        f = fam[c]; tot.update(f)
        print('%-22s %8d %6d %10d %7d %9d %9d' % (c, f['declared'], f['named'], f['configured'], f['emitted'], f['rendered'], f['reachable']))
    print('%-22s %8d %6d %10d %7d %9d %9d' % ('TOTAL', tot['declared'], tot['named'], tot['configured'], tot['emitted'], tot['rendered'], tot['reachable']))
    print('accumulator render groups: %s' % ' '.join('%d-%d' % g for g in sorted(groups)))
    nres = sum(1 for s in sites if s[3] != 'unresolved')
    print('emit sites: %d (resolved %d, unresolved %d in %d functions); render-time override sites: %d'
          % (len(sites), nres, len(sites) - nres, len(unresolved), len(overrides)))
    print('SLS: passes with VS %d, PS %d, render states %d; VS dev entries %d, PS dev entries %d'
          % (sum(1 for g in cfg.values() if g['vs']), sum(1 for g in cfg.values() if g['ps']),
             sum(1 for g in cfg.values() if g['rs']), len(vsdev), len(psdev)))
    print('wrote %s/pass_space.tsv, emit_sites.tsv, overrides.tsv' % out)

if __name__ == '__main__':
    main()
