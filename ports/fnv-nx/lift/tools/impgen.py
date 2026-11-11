#!/usr/bin/env python3
"""Import entries: one guest entry per row of the platform's bridge table (L5: fnv-nx-build l5-platform/manifest bridge.tsv).
    impgen.py --bridge bridge.tsv --out DIR [--need iat.tsv ...] [--l3 rt/bridge_l3.tsv]
Writes DIR/imp.cpp (compile with the lifted-code flags, -I rt), DIR/imp_entries.tsv (key, symbol, dll, name, bind) and DIR/imp_blocked.tsv
(key, dll, name, reason). Deterministic; no clocks.

Rule (the contract agreed with L1 CONTRACT and L5 PLATFORM):
- key of a row = 0x00100000 + 16 * its index among the non-data rows sorted by (dll in lower case, name): the rule of L1's contract/iat.py (keys.tsv);
  the outputs carry `# table_id` = SHA-256 of bridge.tsv. An import slot of a loaded image holds the key; `call [slot]` reaches the entry through
  the dispatcher like any other code address. GetProcAddress of a platform export returns the same key (the loader's fnvnx_import_key).
  Rows of COM interfaces (dll `!<Interface>`, name = method, `this` is the first stack slot) are ordinary stdcall rows.
- the entry is derived from the row alone: conv (stdcall | cdecl | thiscall | vararg), ret (v i q f d), args (x86 stack slots i q f d).
  Arguments are read from the guest stack at esp+4.. (thiscall: `this` from ecx first), the native function is called with the C prototype
  made of u32 / u64 / float / double, the result goes to eax, edx:eax or ST0, the entry pops the return address and, for stdcall and
  thiscall, the argument bytes. Everything around the native call (guest registers parked, FPCR/FPSR, unwinding through) is rt/imp.h.
- bind sym: the native symbol is called directly. bind lookup: the address is fnvnx_win32_lookup_export(dll, name), resolved for every such
  row by fnvnx_lift_imports_init() before the first guest instruction; an unresolved row makes it return (row index + 1), the loader then
  fails the start exactly as the Windows loader fails on a missing export. bind l3 (or a row named by the --l3 overlay: services that work on
  the guest state itself, e.g. RaiseException, RtlUnwind): the symbol IS the guest entry (void f(void), lifted-code conventions), no thunk.
- conv vararg (cdecl, the caller pops): `args` lists the fixed slots only; the native body is called with them followed by the guest address
  of the first stack word after them (the x86 argument words: int/pointer 1 word, 64-bit and double 2 words, low word first).
- no entry can be generated for: bind none (the platform has no body), bind data (the slot holds an object address: the
  loader's business, no entry). They are listed in imp_blocked.tsv; if a row listed there is named by a --need file (column layout of L1's
  iat.tsv: slot, dll, name, ...), the generator fails: a build that needs the row must fail."""
import argparse, collections, os, sys
CT = {'i': 'u32', 'q': 'u64', 'f': 'float', 'd': 'double', 'v': 'void'}
RD = {'i': ('M32', 4), 'q': ('M64', 8), 'f': ('MF32', 4), 'd': ('MF64', 8)}
KEY0, KSTEP = 0x00100000, 16


def rows(path):
    """-> (rows, table id). Key rule (one source: L1 contract/iat.py system_keys): 0x00100000 + 16 * index among the non-data rows sorted by (dll lower case, name)."""
    import hashlib
    raw = open(path, 'rb').read(); out = []
    for i, l in enumerate(raw.decode().splitlines()):
        if i == 0:
            assert l.split('\t')[:2] == ['dll', 'name'], 'bridge.tsv header'
            continue
        dll, name, ordinal, conv, ret, args, sym, bind = l.split('\t')
        out.append(dict(i=i - 1, key=None, dll=dll.lower(), name=name, conv=conv, ret=ret, args=args, sym=sym, bind=bind))
    code = sorted((r for r in out if r['bind'] != 'data' and r['conv'] != 'data'), key=lambda r: (r['dll'], r['name']))
    assert len(set((r['dll'], r['name']) for r in code)) == len(code), 'bridge.tsv: (dll, name) is not unique'
    for j, r in enumerate(code): r['key'] = KEY0 + KSTEP * j
    return out, hashlib.sha256(raw).hexdigest()


def cstr(s):
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def thunk(r, target):
    conv, ret, args = r['conv'], r['ret'], r['args']
    types, vals, off = [], [], 4
    if conv == 'thiscall':
        types.append('u32'); vals.append('ecx')
    for a in args:
        m, n = RD[a]
        types.append(CT[a]); vals.append('%s(sp+%du)' % (m, off)); off += n
    if conv == 'vararg':        # the row lists the fixed slots; the native body takes them and the guest address of the first variadic stack word
        types.append('u32'); vals.append('(u32)(sp+%du)' % off)
    pop = off if conv in ('stdcall', 'thiscall') else 4
    call = '((%s(*)(%s))%s)(%s)' % (CT[ret], ','.join(types) or 'void', target, ','.join(vals))
    if ret == 'v': do, put = call + ';', ''
    elif ret == 'i': do, put = 'u32 r=' + call + ';', 'eax=r;'
    elif ret == 'q': do, put = 'u64 r=' + call + ';', 'eax=(u32)r; edx=(u32)(r>>32);'
    else: do, put = 'double r=' + call + ';', 'fnvl_ret_st0(r);'
    return ('extern "C" __attribute__((section(".text.fnvl"))) void I_%08x(void)   /* %s!%s %s %s(%s) */\n'
            '{ struct fnvl_nframe nf; u32 sp=esp; fnvl_native_in(&nf);\n'
            '  if(__builtin_setjmp((void **)nf.jb)){ c->nf=nf.prev; return; }\n'
            '  %s\n'
            '  fnvl_native_out(&nf); REGS(); %s c->ra=M32(sp); esp=sp+%du; }\n') % (r['key'], r['dll'], r['name'], conv, ret, args, do, put, pop)


def generate(bridge, out, need=(), l3=None, rt_defined=None):
    """rt_defined: the global symbols the runtime objects define (nm); a bind-l3 row whose symbol is not among them has no body yet and is blocked like a
    bind-none row (None: not checked, the unit test's case)."""
    rs, table_id = rows(bridge)
    over = {}
    if l3 and os.path.exists(l3):
        for l in open(l3):
            if l.strip() and l[0] != '#':
                d, n, s = l.split(); over[(d.lower(), n)] = s
    needed = set()
    for f in need:
        for l in open(f):
            p = l.rstrip('\n').split('\t')
            if len(p) >= 4 and p[3] == 'key': needed.add((p[1].lower(), p[2]))
    T = ['#include "fnvl.h"', '#include "imp.h"', 'extern "C" { void *fnvnx_win32_lookup_export(const char *dll, const char *name); int fnvnx_lift_imports_init(void); void fnvl_runtime_install(void); }']
    ent, blocked, look, body = [], [], [], []
    for r in rs:
        k = (r['dll'], r['name'])
        if r['key'] is None:          # the slot holds the address of an object (bind data, or conv data provided by the lifter runtime): no entry, the loader stores the address
            blocked.append((0xffffffff, r['dll'], r['name'], 'object address, not code (data)')); continue
        if k in over or r['bind'] == 'l3':
            s = over.get(k, r['sym'])
            if rt_defined is not None and s not in rt_defined:
                blocked.append((r['key'], r['dll'], r['name'], 'no body in the lifter runtime (bind l3)')); continue
            ent.append((r['key'], s, r['dll'], r['name'], 'l3')); continue
        why = ('no body in the platform (bind none)' if r['bind'] == 'none' else 'object address, not code (bind data)' if r['bind'] == 'data' or r['conv'] == 'data'
               else None)
        if why is None and (r['conv'] not in ('stdcall', 'cdecl', 'thiscall', 'vararg') or r['ret'] not in CT or any(a not in RD for a in r['args']) or r['bind'] not in ('sym', 'lookup')):
            sys.exit('impgen: row %d (%s!%s) is outside the table grammar' % (r['i'], r['dll'], r['name']))
        if why:
            blocked.append((r['key'] if r['key'] is not None else 0xffffffff, r['dll'], r['name'], why)); continue
        if r['bind'] == 'sym':
            T.append('extern "C" void %s(void);' % r['sym']); target = r['sym']
        else:
            target = 'fnvl_imp_cell[%d]' % len(look); look.append(r)
        body.append(thunk(r, target)); ent.append((r['key'], 'I_%08x' % r['key'], r['dll'], r['name'], r['bind']))
    T.append('static void *fnvl_imp_cell[%d];' % max(len(look), 1))
    T.append('static const struct { const char *dll, *name; u32 row; } fnvl_imp_look[] = {%s{0,0,0}};' % ''.join('{%s,%s,%d},' % (cstr(r['dll']), cstr(r['name']), r['i']) for r in look))
    T.append('extern "C" int fnvnx_lift_imports_init(void) { fnvl_runtime_install(); for(u32 i=0;fnvl_imp_look[i].dll;i++){ void *p=fnvnx_win32_lookup_export(fnvl_imp_look[i].dll,fnvl_imp_look[i].name); if(!p) return (int)fnvl_imp_look[i].row+1; fnvl_imp_cell[i]=p; } return 0; }')
    os.makedirs(out, exist_ok=True)
    open(out + '/imp.cpp', 'w').write('\n'.join(T) + '\n' + ''.join(body))
    w = lambda n, rr: open('%s/%s.tsv' % (out, n), 'w').write('# table_id %s\n' % table_id + ''.join('\t'.join(('%08x' % x[0],) + tuple(x[1:])) + '\n' for x in rr))
    w('imp_entries', ent); w('imp_blocked', blocked)
    hit = sorted((d, n) for k, d, n, why in blocked if (d, n) in needed)
    known = {(r['dll'], r['name']) for r in rs}
    absent = sorted(needed - known)
    c = collections.Counter(e[4] for e in ent); cb = collections.Counter(b[3].split(' (')[0].split(':')[0] for b in blocked)
    print('impgen: rows %d, entries %d (%s), blocked %d (%s); needed rows %d: blocked %d, not in the table %d' % (
        len(rs), len(ent), ', '.join('%s %d' % kv for kv in sorted(c.items())), len(blocked), ', '.join('%s %d' % kv for kv in sorted(cb.items())), len(needed), len(hit), len(absent)))
    for d, n in hit[:20]: print('  BLOCKED needed row: %s!%s' % (d, n))
    for d, n in absent[:20]: print('  needed row not in the bridge table: %s!%s' % (d, n))
    return ent, blocked, hit, absent


def main():
    ap = argparse.ArgumentParser(); A = ap.add_argument
    A('--bridge', default=os.environ.get('FNVNX_BRIDGE', '/home/user/wt-l5/manifest/bridge.tsv')); A('--out', required=True); A('--need', action='append', default=[])
    A('--l3', default=None)
    a = ap.parse_args()
    ent, blocked, hit, absent = generate(a.bridge, a.out, a.need, a.l3)
    sys.exit(1 if hit or absent else 0)


if __name__ == '__main__': main()
