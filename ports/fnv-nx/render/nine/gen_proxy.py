#!/usr/bin/env python3
"""gen_proxy.py - ILP32 -> LP64 D3D9 COM proxy generator (stream S6c-nine, render/nine/README.md).

    gen_proxy.py <shim/include/d3d9.h> <out dir>        writes nine_proxy_guest.cpp, nine_proxy_host.c, nine_proxy_ids.h, methods.tsv
    gen_proxy.py <shim/include/d3d9.h> --report         prints the per-method classification

Model. The game (ILP32 guest) holds pointers to *proxy* objects: ILP32 C++ classes deriving from our SDK-order
interfaces in shim/include/d3d9.h, so the engine's vtable-slot calls land on them unchanged. Every proxy method
packs its arguments into a uint64_t block and makes ONE host call, nine.call(remote, method id, block); the LP64
host (S5 host module, Mesa Gallium Nine + nouveau) unpacks the block and calls the real Nine object through its C
vtable (Mesa include/D3D9/d3d9.h, lpVtbl). Both modules share one 32-bit address space, so:

  * scalars travel widened (floats as their bits);
  * pointers to plain-data structs whose layout is identical in ILP32 and LP64 travel as addresses (no copy);
  * interface pointers in are unwrapped (proxy -> remote), out-interfaces are wrapped (remote -> unique proxy);
  * structs containing pointers or HANDLEs are converted (generated converters, below);
  * the rest is marked MANUAL and gets a hand-written body (render/nine/manual_*.{cpp,c}; list via --report).
"""
import os, re, sys

# Structs whose ILP32 and LP64 layouts differ (pointer/HANDLE members): converted field by field.
# name: [(field, kind)], kind 'ptr' (void*/HANDLE, widened/narrowed) or 'pod:<bytes>' (copied as is)
CONVERT = {
    'D3DLOCKED_RECT': [('Pitch', 'i32'), ('pBits', 'ptr')],
    'D3DLOCKED_BOX': [('RowPitch', 'i32'), ('SlicePitch', 'i32'), ('pBits', 'ptr')],
    'D3DPRESENT_PARAMETERS': [('BackBufferWidth', 'u32'), ('BackBufferHeight', 'u32'), ('BackBufferFormat', 'u32'),
        ('BackBufferCount', 'u32'), ('MultiSampleType', 'u32'), ('MultiSampleQuality', 'u32'), ('SwapEffect', 'u32'),
        ('hDeviceWindow', 'ptr'), ('Windowed', 'i32'), ('EnableAutoDepthStencil', 'i32'),
        ('AutoDepthStencilFormat', 'u32'), ('Flags', 'u32'), ('FullScreen_RefreshRateInHz', 'u32'),
        ('PresentationInterval', 'u32')],
    'D3DDEVICE_CREATION_PARAMETERS': [('AdapterOrdinal', 'u32'), ('DeviceType', 'u32'), ('hFocusWindow', 'ptr'),
        ('BehaviorFlags', 'u32')],
}
# Methods that need hand-written marshalling (object lifetime, identity, HWND/HDC/HMONITOR, opaque pointers).
MANUAL = {
    'QueryInterface': 'IID -> proxy class mapping; out-interface of the queried type',
    'AddRef': 'proxy refcount mirrors remote; no host call when cached',
    'Release': 'frees the proxy and its map entry when the remote count reaches 0',
    'SetPrivateData': 'D3DSPD_IUNKNOWN: data is an IUnknown* (unwrap, AddRef on host)',
    'GetPrivateData': 'D3DSPD_IUNKNOWN data comes back as LP64 IUnknown* (wrap)',
    'GetContainer': 'riid decides the proxy class of the out-interface',
    'GetDC': 'HDC (GDI) unsupported on Switch: return D3DERR_INVALIDCALL',
    'ReleaseDC': 'HDC unsupported',
    'GetAdapterMonitor': 'HMONITOR return value (pointer-sized): fake monitor handle',
    'RegisterSoftwareDevice': 'function pointer across ABIs: return D3DERR_NOTAVAILABLE',
    'CreateDevice': 'HWND focus window -> nwindow; D3DPRESENT_PARAMETERS converted; wraps device',
    'CreateDeviceEx': 'as CreateDevice + D3DDISPLAYMODEEX',
    'CreateAdditionalSwapChain': 'D3DPRESENT_PARAMETERS + out swap chain',
    'SetCursorProperties': 'cursor bitmap surface (no cursor on Switch; accept and ignore)',
    'CheckDeviceState': 'HWND argument',
    'GetDirect3D': 'out interface is the process-wide IDirect3D9 proxy',
    'GetDevice': 'out interface is the existing device proxy (identity)',
}
IFACE = re.compile(r'^struct (I\w+)\s*:\s*public\s+(I\w+)')
METHOD = re.compile(r'/\*\s*(\d+)\s*\*/\s*FNVNX_D3D_METHOD\(([^,]+),\s*(\w+)\)\((.*)\)\s*=\s*0;')

def parse(path):
    ifaces, order, cur = {}, [], None
    ifaces['IUnknown'] = {'base': None, 'methods': [
        (0, 'HRESULT', 'QueryInterface', [('REFIID', 'riid'), ('void**', 'ppvObject')]),
        (1, 'ULONG', 'AddRef', []), (2, 'ULONG', 'Release', [])]}
    for line in open(path):
        m = IFACE.match(line)
        if m:
            cur = m.group(1); ifaces[cur] = {'base': m.group(2), 'methods': []}; order.append(cur); continue
        m = METHOD.search(line)
        if m and cur:
            args = []
            a = m.group(4).strip()
            if a and a != 'void':
                for i, p in enumerate(split_args(a)):
                    p = p.strip()
                    mm = re.match(r'(.*?)(\w+)$', p)
                    t, n = (mm.group(1).strip(), mm.group(2)) if mm and mm.group(1).strip() else (p, 'a%d' % i)
                    args.append((norm(t), n))
            ifaces[cur]['methods'].append((int(m.group(1)), m.group(2).strip(), m.group(3), args))
        if line.startswith('};'): cur = None
    return ifaces, order

def split_args(s):
    out, depth, cur = [], 0, ''
    for ch in s:
        if ch == '(' : depth += 1
        if ch == ')' : depth -= 1
        if ch == ',' and depth == 0: out.append(cur); cur = ''
        else: cur += ch
    out.append(cur); return out

def norm(t):
    t = re.sub(r'\bstruct\s+', '', t)
    t = re.sub(r'\s*\*\s*', '*', t).replace(' *', '*')
    return re.sub(r'\s+', ' ', t).strip()

def all_methods(ifaces, name):
    chain = []
    while name: chain.append(name); name = ifaces[name]['base']
    ms = []
    for n in reversed(chain): ms += ifaces[n]['methods']
    return ms

FLOAT = {'float', 'FLOAT'}
def classify(t):
    base = t.replace('const ', '').strip()
    if re.fullmatch(r'I\w+9(Ex)?\*', base): return 'iface_in'
    if re.fullmatch(r'I\w+9(Ex)?\*\*', base): return 'iface_out'
    if base in ('void**',): return 'rawptr_out'
    if base in ('HANDLE*',): return 'shared_handle'        # must be NULL (no cross-process sharing)
    if base.rstrip('*') in CONVERT and base.endswith('*'): return 'convert'
    if base in ('HWND', 'HDC', 'HMONITOR', 'HANDLE'): return 'handle'
    if base in FLOAT: return 'float'
    if base.endswith('*') or base in ('REFIID', 'REFGUID'): return 'pod_ptr'
    return 'scalar'

def method_kind(name, args):
    if name in MANUAL: return 'manual', MANUAL[name]
    kinds = [classify(t) for t, _ in args]
    if 'handle' in kinds: return 'manual', 'HWND/HANDLE argument'
    return 'auto', ','.join(sorted(set(kinds))) or '-'

PTR_RET = {'HMONITOR', 'HANDLE', 'HDC', 'HWND'}

def host_arg(t, i):
    """LP64 expression for argument i of declared (ILP32 SDK) type t, from the uint64_t block a[]."""
    k = classify(t)
    if k == 'float': return 'nx_f(a[%d])' % i
    if k == 'scalar': return '(%s)a[%d]' % (t, i)
    if k == 'shared_handle': return 'NULL'
    return '(%s)(uintptr_t)a[%d]' % (t, i)   # pointers, interfaces, handles, REFIID (Mesa: const GUID*)

def guest_pack(t, n, i, body, post):
    k = classify(t)
    if k == 'scalar': body.append('a[%d] = (uint64_t)(int64_t)%s;' % (i, n))
    elif k == 'float': body.append('{ union { float f; uint32_t u; } c; c.f = %s; a[%d] = c.u; }' % (n, i))
    elif k == 'handle': body.append('a[%d] = FNVNX_ADDR(%s);' % (i, n))
    elif k == 'pod_ptr': body.append('a[%d] = FNVNX_ADDR(%s);' % (i, '&' + n if t in ('REFIID', 'REFGUID') else n))
    elif k == 'iface_in': body.append('a[%d] = nx_unwrap(%s);' % (i, n))
    elif k == 'iface_out':
        it = t.replace('const ', '')[:-2]
        body.append('uint64_t o%d = 0; a[%d] = %s ? FNVNX_ADDR(&o%d) : 0;' % (i, i, n, i))
        post.append('if (%s) *%s = o%d ? nx_wrap_%s(o%d) : nullptr;' % (n, n, i, it, i))
    elif k == 'rawptr_out':
        body.append('uint64_t o%d = 0; a[%d] = %s ? FNVNX_ADDR(&o%d) : 0;' % (i, i, n, i))
        post.append('if (%s) *%s = (void*)(uintptr_t)o%d;' % (n, n, i))
    elif k == 'shared_handle': body.append('a[%d] = 0; if (%s) *%s = nullptr;' % (i, n, n))
    elif k == 'convert':
        st = t.replace('const ', '').rstrip('*')
        body.append('NxW_%s w%d; if (%s) nx_to_wide_%s(&w%d, %s); a[%d] = %s ? FNVNX_ADDR(&w%d) : 0;' % (st, i, n, st, i, n, i, n, i))
        post.append('if (%s) nx_from_wide_%s(%s, &w%d);' % (n, st, n, i))

def gen(ifaces, order, out):
    guest, host, ids, tsv = [], [], [], ['interface\tslot\tmethod\tkind\tnote']
    guest.append('// Generated by render/nine/gen_proxy.py from shim/include/d3d9.h. Do not edit.\n'
                 '#include <d3d9.h>\n#include "nine_proxy.h"\n#include "nine_proxy_ids.h"\n')
    host.append('/* Generated by render/nine/gen_proxy.py from shim/include/d3d9.h. Do not edit. */\n'
                '#include <stdint.h>\n#include "nine_host.h"\n'
                'static inline float nx_f(uint64_t v) { union { uint32_t u; float f; } c; c.u = (uint32_t)v; return c.f; }\n'
                'uint64_t nine_dispatch(uint64_t remote, uint64_t method, const uint64_t* a)\n{\n    (void)a;\n    switch (method) {')
    ids.append('// Generated by render/nine/gen_proxy.py. Method id = interface index << 8 | vtable slot.\n#pragma once')
    decls = set()
    for ii, iface in enumerate(order):
        ms = all_methods(ifaces, iface)
        cls = 'Px' + iface[1:]
        ids.append('#define NXI_%s %d' % (iface, ii))
        guest.append('struct %s final : %s, NxProxy {' % (cls, iface))
        guest.append('    explicit %s(uint64_t r) : NxProxy(r) {}' % cls)
        for slot, ret, name, args in ms:
            kind, note = method_kind(name, args)
            mid = (ii << 8) | slot
            ids.append('#define NXM_%s_%s 0x%04x' % (iface, name, mid))
            tsv.append('%s\t%d\t%s\t%s\t%s' % (iface, slot, name, kind, note))
            params = ', '.join('%s %s' % (t, n) for t, n in args)
            if kind == 'manual':
                guest.append('    %s STDMETHODCALLTYPE %s(%s) override { return nx_m_%s(this, 0x%04x%s); }'
                             % (ret, name, params, name, mid, ''.join(', ' + n for _, n in args)))
                decls.add('%s nx_m_%s(NxProxy* self, uint32_t mid%s);' % (ret, name, ''.join(', %s %s' % (t, n) for t, n in args)))
            else:
                body, post = ['uint64_t a[%d];' % max(1, len(args))], []
                for i, (t, n) in enumerate(args): guest_pack(t, n, i, body, post)
                guest.append('    %s STDMETHODCALLTYPE %s(%s) override {' % (ret, name, params))
                guest.append('        ' + ' '.join(body))
                guest.append('        uint64_t r = nx_call(remote, 0x%04x, FNVNX_ADDR(a));' % mid)
                if post: guest.append('        ' + ' '.join(post))
                guest.append('        %s\n    }' % ('return (%s)(uintptr_t)r;' % ret if ret in PTR_RET else
                                                 'return (%s)r;' % ret if ret != 'void' else '(void)r;'))
            call = 'p->lpVtbl->%s(p%s)' % (name, ''.join(', ' + host_arg(t, i) for i, (t, _) in enumerate(args)))
            rexpr = ('return (uint64_t)(uintptr_t)%s;' % call if ret in PTR_RET else
                     '%s; return 0;' % call if ret == 'void' else 'return (uint64_t)(int64_t)%s;' % call)
            host.append('    case 0x%04x: { %s* p = (%s*)(uintptr_t)remote; %s }' % (mid, iface, iface, rexpr))
        guest.append('};')
        guest.append('static_assert(sizeof(%s) == 12, "proxy layout: vptr, NxProxy vptr, remote");' % cls)
        guest.append('%s* nx_new_%s(uint64_t r) { return new %s(r); }\n' % (iface, iface, cls))
    host.append('    default: return 0x8876086Cu; /* D3DERR_INVALIDCALL */\n    }\n}')
    ids += ['', '#ifdef __cplusplus', 'struct NxProxy;'] + sorted(decls)
    ids += ['%s* nx_new_%s(uint64_t r);' % (i, i) for i in order] + ['#endif']
    os.makedirs(out, exist_ok=True)
    open(os.path.join(out, 'nine_proxy_guest.cpp'), 'w').write('\n'.join(guest) + '\n')
    open(os.path.join(out, 'nine_proxy_host.c'), 'w').write('\n'.join(host) + '\n')
    open(os.path.join(out, 'nine_proxy_ids.h'), 'w').write('\n'.join(ids) + '\n')
    open(os.path.join(out, 'methods.tsv'), 'w').write('\n'.join(tsv) + '\n')
    return tsv

def report(ifaces, order):
    total = auto = 0; manual = []
    for iface in order:
        for slot, ret, name, args in ifaces[iface]['methods']:
            total += 1
            k, note = method_kind(name, args)
            if k == 'manual': manual.append((iface, slot, name, note))
            else: auto += 1
            conv = [t for t, _ in args if classify(t) == 'convert']
            if conv: print('convert  %s::%s (%s)' % (iface, name, ', '.join(conv)))
    for m in manual: print('manual   %s::%s slot %d: %s' % (m[0], m[2], m[1], m[3]))
    print('declared methods: %d (plus IUnknown 3), generated: %d, manual: %d' % (total, auto, len(manual)))

if __name__ == '__main__':
    if len(sys.argv) != 3: sys.exit(__doc__)
    ifs, order = parse(sys.argv[1])
    if sys.argv[2] == '--report': report(ifs, order)
    else: gen(ifs, order, sys.argv[2])
