#!/usr/bin/env python3
"""d3d9_inventory.py - measure how the decomp uses Direct3D 9 / D3DX (stream S6-render, docs/RENDER.md).

    d3d9_inventory.py units <tree>          render units, one per line (source paths relative to <tree>)
    d3d9_inventory.py calls <census logs>   per-unit Interface::Method call counts from a FNVNX_D3D_TRACE census
    d3d9_inventory.py scan <tree>           source scan: names, stand-in interfaces and their vtable slots,
                                            call sites per SDK method (markdown, for docs/RENDER.md)

<tree> is a fnv_decomp checkout or a tree prepared by tools/prepare.py. The SDK side (interfaces, slots) is read
from our own shim/include/d3d9.h and d3dx9*.h, so the tool needs nothing else. Reads the decomp, writes only stdout.
"""
import collections, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHIM = os.path.normpath(os.path.join(HERE, '..', '..', 'shim', 'include'))
SRC_EXT = ('.cpp', '.h', '.hpp', '.inl', '.c')

D3D_NAME = re.compile(r'\b(IDirect3D\w+|ID3DX\w+|LPDIRECT3D\w+|LPD3DX\w+|_?D3D[A-Z][A-Z0-9_]*\w*|D3DX\w+)\b')
INCLUDE = re.compile(r'#\s*include\s*"([^"]+)"')
RENDER_DIRS = ('src/bsshader/', 'src/gamebryo2.2/corelibs/niconsolerenderer/')

def read(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        return f.read()

def walk(tree):
    for top in ('src', 'include'):
        for d, _, names in os.walk(os.path.join(tree, top)):
            for n in names:
                if n.endswith(SRC_EXT):
                    p = os.path.join(d, n)
                    yield os.path.relpath(p, tree), p

# ------------------------------------------------------------------------------------------------- render units

def units(tree):
    cache = {}
    def mentions(path, seen):
        if path in seen: return False
        seen.add(path)
        if path not in cache:
            try: text = read(path)
            except OSError: cache[path] = (False, []); return False
            incs = []
            for m in INCLUDE.finditer(text):
                for base in (os.path.dirname(path), os.path.join(tree, 'include')):
                    p = os.path.normpath(os.path.join(base, m.group(1)))
                    if os.path.exists(p): incs.append(p); break
            cache[path] = (bool(D3D_NAME.search(text)) or '<d3d9.h>' in text, incs)
        hit, incs = cache[path]
        return hit or any(mentions(p, seen) for p in incs)
    srcs = sorted({u['source'] for u in json.load(open(os.path.join(tree, 'build', 'units.json')))['units']})
    return [u for u in srcs if u.startswith(RENDER_DIRS) or mentions(os.path.join(tree, u), set())]

# ------------------------------------------------------------------------------------------------- SDK side

def sdk_interfaces():
    """{interface: (base, [method names by slot])} from our shim headers (slots include the bases')."""
    ifaces = {'IUnknown': (None, ['QueryInterface', 'AddRef', 'Release'])}
    for n in sorted(os.listdir(SHIM)):
        if not (n.startswith('d3d9') or n.startswith('d3dx9')): continue
        text = read(os.path.join(SHIM, n))
        for m in re.finditer(r'^struct (\w+)(?: : public (\w+))?\n\{(.*?)\n\};', text, re.S | re.M):
            name, base, body = m.groups()
            own = re.findall(r'FNVNX_D3D_METHOD\(.*?,\s*(\w+)\)\(', body)
            if own: ifaces[name] = (base, own)
    out = {}
    def full(name):
        if name in out: return out[name]
        base, own = ifaces[name]
        out[name] = (full(base) if base else []) + own
        return out[name]
    for name in ifaces: full(name)
    return out

# ------------------------------------------------------------------------------------------------- trace census

TRACE = re.compile(r"warning: '(?:virtual )?[^']*?\b(I[A-Za-z0-9]+)::(\w+)\(|warning: '[^']*?\b(D3DX\w+|Direct3DCreate9\w*|D3DPERF_\w+)\(")

def calls(logdir):
    rows = collections.Counter()
    for n in sorted(os.listdir(logdir)):
        if not n.endswith('.log'): continue
        seen = set()
        for line in read(os.path.join(logdir, n)).splitlines():
            if 'is deprecated' not in line: continue
            m = TRACE.search(line)
            if not m: continue
            key = (line.split(': warning:')[0], m.group(1), m.group(2), m.group(3))  # one per source location
            if key in seen: continue
            seen.add(key)
            rows[(n[:-4], '%s::%s' % (m.group(1), m.group(2)) if m.group(1) else m.group(3))] += 1
    for (unit, what), c in sorted(rows.items()):
        print('%s\t%s\t%d' % (unit, what, c))

# ------------------------------------------------------------------------------------------------- source scan

CLASS = re.compile(r'\b(struct|class)\s+(?:__declspec\([^)]*\)\s*)?(\w+)\s*(?::\s*([^{;]+))?\{')
VIRT = re.compile(r'\bvirtual\b[^;{]*?\b(~?\w+)\s*\(')
FNPTR = re.compile(r'\(\s*(?:__stdcall|STDMETHODCALLTYPE|WINAPI)?\s*\*\s*(\w+)\s*\)\s*\(')

def match_brace(text, i):
    depth = 0
    for j in range(i, len(text)):
        c = text[j]
        if c == '{': depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0: return j + 1
    return len(text)

def strip(text):
    text = re.sub(r'/\*.*?\*/', lambda m: re.sub(r'[^\n]', ' ', m.group(0)), text, flags=re.S)
    return re.sub(r'//[^\n]*', '', text)

def classify(slots, sdk, hint):
    """Best SDK interface for a stand-in whose slot -> name map is `slots` (names matching at their slot)."""
    score = {iface: sum(1 for s, n in slots.items() if s < len(meths) and meths[s] == n) for iface, meths in sdk.items()}
    top = max(score.values())
    if not top: return 0, None
    cands = sorted(i for i, h in score.items() if h == top)
    if 'IUnknown' in cands: return top, 'IUnknown'      # only IUnknown slots are named: interface unknown
    if hint in cands: return top, hint
    if 'IDirect3DTexture9' in cands: return top, 'IDirect3DTexture9'
    return top, cands[0]

def scan(tree):
    sdk = sdk_interfaces()
    sdk_names = {n for ms in sdk.values() for n in ms}
    dev = sdk['IDirect3DDevice9']
    standins, lpvtbl, raw_slot_files = [], [], collections.Counter()
    call_sites = collections.Counter(); call_files = collections.defaultdict(set)
    name_counts = collections.Counter(); name_files = collections.defaultdict(set)
    files_d3d = set(); includes_d3d9 = []
    for rel, path in walk(tree):
        text = strip(read(path))
        names = D3D_NAME.findall(text)
        if not names and '<d3d9.h>' not in text: continue
        files_d3d.add(rel)
        if re.search(r'#\s*include\s*<d3d9\.h>', text): includes_d3d9.append(rel)
        for n in names:
            name_counts[n] += 1; name_files[n].add(rel)
        file_methods, other_names = {}, set()
        for m in CLASS.finditer(text):
            cname = m.group(2)
            end = match_brace(text, m.end() - 1)
            body = text[m.end():end]
            # top-level members only: drop nested braces (inline bodies)
            flat, depth = [], 0
            for ch in body:
                if ch == '{': depth += 1
                elif ch == '}': depth -= 1
                elif depth == 0: flat.append(ch)
            flat = ''.join(flat)
            virt = VIRT.findall(flat)
            ptrs = FNPTR.findall(flat) if not virt else []
            entries = virt or ptrs
            members = set(re.findall(r'\b(\w+)\s*\(', flat))
            # derived stand-ins (slots after the base's unknown here) and non-D3D classes: their member names
            # make same-named calls in this file ambiguous
            iface = None
            slots = {i: n for i, n in enumerate(entries) if n in sdk_names}
            if entries and slots and not (m.group(3) and virt):
                hint = 'IDirect3DDevice9' if re.search(r'Dev', cname) else None
                hits, iface = classify(slots, sdk, hint)
                if iface and hits < max(1, len(slots) // 2): iface = None
                if iface == 'IUnknown' and (len(slots) == len(entries) or not D3D_NAME.search(cname + ' ' + flat)):
                    iface = None  # COM object of unknown kind (DirectShow/DirectSound/XAudio objects too)
            if not iface:
                other_names |= members
                continue
            meths = sdk[iface]
            bad = {s: n for s, n in slots.items() if not (s < len(meths) and meths[s] == n)}
            rec = dict(file=rel, cls=cname, iface=iface, entries=len(entries), named=len(slots), bad=bad,
                       style='lpVtbl' if ptrs else 'virtual')
            (lpvtbl if ptrs else standins).append(rec)
            if iface == 'IUnknown': continue
            # every slot of the stand-in, named after the SDK or padding (pad44, vfn_2c, d0e4...), is the SDK method
            # at that slot: the vtable index is the contract, the decomp's label is not
            for s, n in enumerate(entries):
                if s >= len(meths) or s in bad: continue
                key = (iface, meths[s])
                if file_methods.get(n, key) != key: file_methods[n] = None   # same label, different methods
                else: file_methods[n] = key
        if re.search(r'\(\s*\(\s*(?:int|void|unsigned|long)\s*\*\*?\s*\)\s*\w+\s*\+\s*\d+\s*\)', text):
            raw_slot_files[rel] += 1
        # call sites: through a stand-in's slot label (exact interface), or by SDK name in files that use the SDK
        # interfaces (name-based: the interface is the first SDK interface declaring that name)
        uses_sdk = rel in includes_d3d9 or re.search(r'\bIDirect3D\w+\s*\*', text)
        for n, key in file_methods.items():
            if key is None or n in other_names: continue
            c = len(re.findall(r'(?:->|\.)\s*%s\s*\(' % re.escape(n), text))
            if c:
                call_sites[key] += c; call_files[key].add(rel)
        if uses_sdk:
            for n in sdk_names - set(file_methods) - other_names:
                c = len(re.findall(r'(?:->|\.)\s*%s\s*\(' % re.escape(n), text))
                if c:
                    call_sites[(None, n)] += c; call_files[(None, n)].add(rel)
    return dict(sdk=sdk, standins=standins, lpvtbl=lpvtbl, raw_slot_files=raw_slot_files, call_sites=call_sites,
                call_files=call_files, name_counts=name_counts, name_files=name_files, files_d3d=files_d3d,
                includes_d3d9=includes_d3d9)

def report(r):
    sdk = r['sdk']
    p = print
    p('files naming D3D9/D3DX: %d; including <d3d9.h>: %d (%s)' % (len(r['files_d3d']), len(r['includes_d3d9']),
                                                                 ', '.join(sorted(r['includes_d3d9']))))
    p('stand-in interface classes (C++ virtual): %d in %d files; C lpVtbl structs: %d; files with raw slot reads: %d'
      % (len(r['standins']), len({s['file'] for s in r['standins']}), len(r['lpvtbl']), len(r['raw_slot_files'])))
    by_iface = collections.Counter(s['iface'] for s in r['standins'] + r['lpvtbl'])
    p('stand-ins by interface: ' + ', '.join('%s %d' % kv for kv in by_iface.most_common()))
    bad = [s for s in r['standins'] + r['lpvtbl'] if s['bad']]
    p('stand-ins with a name at a slot that disagrees with the SDK: %d' % len(bad))
    for s in bad[:40]:
        p('  %s %s (%s): %s' % (s['file'], s['cls'], s['iface'],
                                ', '.join('slot %d %s (SDK: %s)' % (k, v, sdk[s['iface']][k] if k < len(sdk[s['iface']]) else '-')
                                          for k, v in sorted(s['bad'].items()))))
    p()
    p('| interface | method | slot | call sites | files | via |')
    p('|---|---|---|---|---|---|')
    owner = {}
    for iface in ('IUnknown', 'IDirect3D9', 'IDirect3DDevice9', 'IDirect3DResource9', 'IDirect3DBaseTexture9',
                  'IDirect3DTexture9', 'IDirect3DCubeTexture9', 'IDirect3DVolumeTexture9', 'IDirect3DSurface9',
                  'IDirect3DVolume9', 'IDirect3DVertexBuffer9', 'IDirect3DIndexBuffer9', 'IDirect3DSwapChain9',
                  'IDirect3DQuery9', 'IDirect3DStateBlock9', 'IDirect3DVertexDeclaration9',
                  'IDirect3DVertexShader9', 'IDirect3DPixelShader9', 'ID3DXBuffer', 'ID3DXConstantTable',
                  'ID3DXInclude'):
        if iface not in sdk: continue
        for slot, n in enumerate(sdk[iface]):
            owner.setdefault(n, (iface, slot))
    merged = {}
    for (iface, n), c in r['call_sites'].items():
        via = 'slot' if iface else 'name'
        if iface is None:
            if n not in owner: continue
            iface = owner[n][0]
        slot = sdk[iface].index(n)
        e = merged.setdefault((iface, slot, n), [0, set(), set()])
        e[0] += c; e[1] |= r['call_files'][(None if via == 'name' else iface, n)]; e[2].add(via)
    rows = sorted(merged.items())
    for (iface, slot, n), (c, files, via) in rows:
        p('| %s | %s | %d | %d | %d | %s |' % (iface, n, slot, c, len(files), '+'.join(sorted(via))))
    p()
    ifaces = collections.Counter(k[0] for k, _ in rows)
    p('distinct SDK methods called: %d (%s)' % (len(rows), ', '.join('%s %d' % kv for kv in ifaces.most_common())))
    p('call sites total: %d' % sum(v[0] for _, v in rows))
    p()
    p('D3D/D3DX names by occurrences (top 120): ' + ', '.join('%s %d' % kv for kv in r['name_counts'].most_common(120)))

if __name__ == '__main__':
    cmd = sys.argv[1]
    if cmd == 'units':
        print('\n'.join(units(sys.argv[2])))
    elif cmd == 'calls':
        calls(sys.argv[2])
    elif cmd == 'scan':
        report(scan(sys.argv[2]))
    else:
        sys.exit(__doc__)
