#!/usr/bin/env python3
"""Bindings of the link step that do not go to a decomp definition, on hand-written units (no decomp content).

    python3 tools/link/test_bindings.py     # needs aarch64-linux-gnu-{g++,ld,objcopy,objdump}, llvm-undname

* CRT/library code (S4's crt_symbols.tsv `provider` column): a call of a referenced .text address no unit defines
  binds to the provider symbol (`platform/crt: strcpy_s`, the ws2_32 thunk through exports.cpp), and the address is
  an F entry naming it; a `mid_data` row is no E entry;
* imports (imports.tsv + exports.cpp): a call through an IAT slot binds to the slot's api, whatever the unit calls
  it; import thunks (6-byte decomp functions whose body only calls an import) bind to the import, not to their
  compiled body (which loses or retypes arguments); a 6-byte getter stays native;
* thread-local variables at image .tls addresses (two units, two names, one address; a native definition at
  another): TLS references to slots of one native TLS block, never to an absolute image address;
* image vftable addresses stored into vptrs by hand: V entries (named vftable; class of the storing constructor) and
  the data symbol bound to the native address point.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vtables  # noqa: E402

FNVLINK = os.path.join(HERE, 'fnvlink.py')
CXX = os.environ.get('CXX', 'aarch64-linux-gnu-g++')

CRT = ('# address\tclass\tidentification\thow\tunits\tsrc_files\tprovider\n'
       '0x00ec1000\tcrt\tstrcpy_s\tn\t1\t1\tplatform/crt: strcpy_s\n'
       '0x00ec2000\tthunk\tws2_32 closesocket\ti\t1\t1\tfnvnx_ws2_* (see imports.tsv)\n'
       '0x00ec3000\tmid_data\t?g_bogus@@3HA\tn\t1\t1\tnone: data symbol / bogus address\n'
       '0x00ec4000\thelper\t_alloca_probe\tn\t1\t1\timplicit: GCC\n')
IMPORTS = ('# iat_slot\tdll\tapi\tunits\tother_decomp_names\tprovider\n'
           '0x00fdf004\tadvapi32\tRegCloseKey\t2\tEnterCriticalSection\tS4 platform/win32\n'
           '0x00fdf100\td3dx9_38\tD3DXMatrixRotationYawPitchRoll\t1\t-\tS6\n'
           '0x00fdf108\td3dx9_38\tD3DXVec3Normalize\t1\t-\tS6\n')   # 0x00fdf104: in the span, unlisted
EXPORTS = ('const exp g_exports[] = { E("advapi32", RegCloseKey),\n'
           '    { "ws2_32", "closesocket", (void *)&fnvnx_ws2_closesocket } };\n')
SRC = {
    'src/a.cpp': 'extern "C" int FUN_00ec1000(char *, unsigned, const char *);\n'
                 'extern "C" int FUN_00ec2000(unsigned);\n'
                 'extern "C" void EnterCriticalSection(void *);\n'      # the decomp's wrong name for the slot
                 'int use_crt(char *d) { EnterCriticalSection(d); FUN_00ec2000(3); return FUN_00ec1000(d, 4, "x"); }\n'
                 'extern __thread int g_tlsA;\nint get_a() { return g_tlsA; }\n'
                 '__thread int g_tlsDef = 0;\nint get_def() { return g_tlsDef; }\n',
    'src/t.cpp': 'extern "C" void *D3DXMatrixRotationYawPitchRoll(void *, unsigned, unsigned, unsigned);\n'
                 'void *FUN_00ee6e04(void *o, unsigned y, unsigned p, unsigned r) '
                 '{ return D3DXMatrixRotationYawPitchRoll(o, y, p, r); }\n'
                 'struct ImportEntry { void (*fn)(); void operator()() { fn(); } };\n'
                 'extern ImportEntry g_D3DXVec3TransformNormal;\n'
                 'extern "C" void FUN_00ee6df2() { g_D3DXVec3TransformNormal(); }\n'
                 'extern int g_x;\nextern "C" int *FUN_00ee7000() { return &g_x; }\n'
                 'extern "C" void call_t(float *v) { ((void (*)(float *))FUN_00ee6df2)(v); FUN_00ee7000(); }\n',
    'src/b.cpp': 'extern __thread int t_otherName;\nint get_b() { return t_otherName + 1; }\n'
                 'struct Poly { virtual int f(); void *pad; Poly(); };\nint Poly::f() { return 1; }\n'
                 'extern "C" char DAT_01000600[];\nPoly::Poly() { *(void **)this = DAT_01000600; pad = 0; }\n'
                 'struct Named { virtual int g(); };\nint Named::g() { return 2; }\n'
                 'struct Shell { void *vtbl; void init(); };\nextern "C" char DAT_01000700[];\n'
                 'void Shell::init() { vtbl = DAT_01000700; }\nNamed *mk() { return new Named; }\n'
                 'extern "C" void FUN_00ee6df2();\nvoid call_b() { FUN_00ee6df2(); }\n'
                 'struct Dt { virtual int h(); ~Dt(); };\nint Dt::h() { return 3; }\n'
                 'extern "C" char DAT_01000800[], DAT_01000810[];\n'
                 'Dt::~Dt() { *(void **)this = DAT_01000800; *(void **)this = DAT_01000810; }\n',
}
F = lambda a, s: {'address': '0x%08x' % a, 'symbol': s, 'size': 16}   # noqa: E731
F6 = lambda a, s: {'address': '0x%08x' % a, 'symbol': s, 'size': 6}   # noqa: E731
UNITS = {'units': [
    {'source': 'src/t.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [   # import thunks (6 bytes: jmp [slot])
        F6(0xee6e04, '?FUN_00ee6e04@@YGPAXPAXIII@Z'), F6(0xee6df2, '?FUN_00ee6df2@@YAXXZ'),
        F6(0xee7000, '?FUN_00ee7000@@YAPAHXZ'), F(0x401300, '_call_t')],
     'symbols': {'__imp__D3DXMatrixRotationYawPitchRoll@16': '0x00fdf100',
                 '?g_D3DXVec3TransformNormal@@3UImportEntry@@A': '0x00fdf104', '?g_x@@3HA': '0x01000900',
                 '_FUN_00ee6df2': '0x00ee6df2', '_FUN_00ee7000': '0x00ee7000'}},
    {'source': 'src/a.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        F(0x401000, '?use_crt@@YAHPAD@Z'), F(0x401010, '?get_a@@YAHXZ'), F(0x401020, '?get_def@@YAHXZ')],
     'symbols': {'_FUN_00ec1000': '0x00ec1000', '_FUN_00ec2000': '0x00ec2000',
                 '__imp__EnterCriticalSection@4': '0x00fdf004', '?g_tlsA@@3HA': '0x01272010',
                 '?g_tlsDef@@3HA': '0x01272020'}},
    {'source': 'src/b.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        F(0x401100, '?get_b@@YAHXZ'), F(0x401110, '?f@Poly@@UAEHXZ'), F(0x401120, '??0Poly@@QAE@XZ'),
        F(0x401130, '?g@Named@@UAEHXZ'), F(0x401140, '?init@Shell@@QAEXXZ'), F(0x401150, '?mk@@YAPAUNamed@@XZ'),
        F(0x401160, '?h@Dt@@UAEHXZ'), F(0x401170, '??1Dt@@UAE@XZ'), F(0x401180, '?call_b@@YAXXZ')],
     'symbols': {'?t_otherName@@3HA': '0x01272010', '_DAT_01000600': '0x01000600', '_DAT_01000700': '0x01000700',
                 '??_7Named@@6B@': '0x01000700', '_DAT_01000800': '0x01000800', '_DAT_01000810': '0x01000810',
                 '_FUN_00ee6df2': '0x00ee6df2'}},
]}

ok = True


def check(cond, what):
    global ok
    if not cond:
        ok = False
        print('FAIL', what)
    return cond


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        raise SystemExit('FAILED: %s\n%s%s' % (' '.join(cmd), r.stdout, r.stderr))
    return r.stdout


def main():
    with tempfile.TemporaryDirectory() as t:
        tree = os.path.join(t, 'tree')
        os.makedirs(os.path.join(tree, 'build'))
        objs = {}
        for rel, text in SRC.items():
            p = os.path.join(tree, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, 'w').write(text)
            objs[rel] = os.path.join(t, rel.replace('/', '_') + '.o')
            run([CXX, '-mabi=ilp32', '-O1', '-c', p, '-o', objs[rel]])
        units = os.path.join(tree, 'build', 'units.json')
        json.dump(UNITS, open(units, 'w'))
        with open(os.path.join(t, 'objects.tsv'), 'w') as f:
            for rel in SRC:
                f.write('%s\t%s\tok\n' % (rel, objs[rel]))
        plat = os.path.join(t, 'platform')
        os.makedirs(plat)
        for name, text in (('crt_symbols.tsv', CRT), ('imports.tsv', IMPORTS), ('exports.cpp', EXPORTS)):
            open(os.path.join(plat, name), 'w').write(text)
        out, fixed = os.path.join(t, 'out'), os.path.join(t, 'fixed')
        run([sys.executable, FNVLINK, 'resolve', units, os.path.join(t, 'objects.tsv'), out, '--crt-symbols',
             os.path.join(plat, 'crt_symbols.tsv'), '--imports', os.path.join(plat, 'imports.tsv')])
        refs = {(r[0], r[1]): r for r in (l.rstrip('\n').split('\t') for l in open(os.path.join(out, 'refs.tsv'))
                                          if not l.startswith('#'))}
        rows = {int(r[0], 16): r for r in (l.rstrip('\n').split('\t') for l in open(os.path.join(out, 'addrmap.tsv'))
                                          if not l.startswith('#'))}
        a = 'src/a.cpp'
        check(refs.get((a, 'FUN_00ec1000'), [''] * 6)[5] == 'strcpy_s', 'CRT call -> provider: %s'
              % refs.get((a, 'FUN_00ec1000')))
        check(refs.get((a, 'FUN_00ec2000'), [''] * 6)[5] == 'fnvnx_ws2_closesocket', 'ws2 thunk -> fnvnx_ws2_*')
        check(refs.get((a, 'EnterCriticalSection'), [''] * 6)[5] == 'RegCloseKey', 'import -> the slot\'s api: %s'
              % refs.get((a, 'EnterCriticalSection')))
        check(rows.get(0x00ec1000, [''] * 4)[1:4] == ['F', 'strcpy_s', 'strcpy_s'], 'provider F entry')
        check(0x00ec3000 not in rows and rows.get(0x00ec4000, [''] * 2)[1] == 'E', 'mid_data dropped, helper E')
        for unit, name in ((a, 'g_tlsA'), ('src/b.cpp', 't_otherName')):
            r = refs.get((unit, name), [''] * 6)
            check(r[2] == 'tls' and r[5] == '__fnvnx_tls_01272010', 'TLS ref %s: %s' % (name, r))
        check(rows.get(0x01000600, [''] * 3)[1:3] == ['V', '_ZTV4Poly'], 'hand-written vptr, storing ctor\'s class: %s'
              % rows.get(0x01000600))
        check(rows.get(0x01000700, [''] * 3)[1:3] == ['V', '_ZTV5Named'], 'hand-written vptr, named vftable')
        check(0x01000800 not in rows and 0x01000810 not in rows, 'a function storing two vftables maps neither')
        for a_, sym in ((0xee6e04, 'D3DXMatrixRotationYawPitchRoll'), (0xee6df2, 'D3DXVec3TransformNormal')):
            check(rows.get(a_, [''] * 4)[1:4] == ['F', sym, sym], 'import thunk %x -> %s: %s' % (a_, sym, rows.get(a_)))
        check(refs.get(('src/b.cpp', 'FUN_00ee6df2'), [''] * 6)[5] == 'D3DXVec3TransformNormal',
              'a call of the thunk goes to the import: %s' % refs.get(('src/b.cpp', 'FUN_00ee6df2')))
        check(rows.get(0xee7000, [''] * 3)[1:3] == ['F', '__fnv_00ee7000'], '6-byte getter stays native')
        ld = open(os.path.join(out, 'aliases.ld')).read()
        check('__fnv_01000600 = _ZTV4Poly + 8;' in ld and '__fnv_01000700 = _ZTV5Named + 8;' in ld,
              'vptr data symbols bound to the native address points')
        check('__fnv_01272010' not in ld and '__fnv_01272020' not in ld, 'no absolute TLS address')
        run([sys.executable, FNVLINK, 'apply', out, fixed])
        run([sys.executable, FNVLINK, 'check', out, fixed])
        t_o = vtables.Obj(os.path.join(fixed, os.path.basename(objs['src/t.cpp'])))
        call_t = [s_ for s_ in t_o.syms if s_[0] == 'call_t'][0]
        targets = {t_o.syms[si][0] for off, (si, _, _) in t_o.rel(call_t[5]).items()
                   if call_t[1] <= off < call_t[1] + call_t[2]}
        check('FUN_00ee6df2' not in t_o.defs and 'D3DXVec3TransformNormal' in targets,
              'the thunk\'s own unit calls the import: %s' % sorted(targets))
        log = open(os.path.join(out, 'trial_link.log')).read()
        check('multiple definition' not in log and 'TLS' not in log and 'relocation' not in log,
              'trial link: %s' % log[:2000])
        e = vtables.Obj(os.path.join(out, 'trial.elf'))
        sym = {s[0]: s for s in e.syms if s[0]}
        for n in ('__fnvnx_tls_01272010', '__fnvnx_tls_01272020'):
            check(n in sym and sym[n][3] == 6, '%s is a TLS symbol' % n)       # STT_TLS
        check(sym.get('__fnvnx_tls_01272020', (0, -1))[1] - sym.get('__fnvnx_tls_image', (0, 0))[1] == 0x20,
              'TLS slot offset in the block')
        check(sym.get('__fnv_01000600', (0, -1))[1] == sym.get('_ZTV4Poly', (0, 0))[1] + 8, 'linked vptr value')
    print('ok' if ok else 'FAILED')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
