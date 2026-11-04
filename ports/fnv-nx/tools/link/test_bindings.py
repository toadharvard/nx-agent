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
  the data symbol bound to the native address point;
* split globals (S13): a variable defined at an image address by the unit itself (-O2: section anchors) - its own
  functions, its static initializer and a pointer in its data reach the rebound symbol, not `.data + off`;
* GAME1 (fnvnx_game_<name>): looked up by the unit's own name (a game function, another import's slot);
* GCC's global operator new/delete -> the game's, by the MSVC names the units bind (sized delete: the sized one);
* S1b units_renames.tsv: applied to a unit whose source carries the patch, not to an unpatched one;
* destructors defined nowhere that GCC calls where MSVC did not: one reached only by a constructor's exception
  cleanup -> trap stub; one at the end of a scope (normal code) stays undefined; one of a class sharing its deleting
  destructor (??_G address) with a class whose destructor is compiled -> that destructor;
* STATIC1 header variables: two objects define the same variable, only one unit's recipe names its address - both
  copies are rebound (none localized), the second object's own references included.
"""
import collections
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
           '0x00fdf108\td3dx9_38\tD3DXVec3Normalize\t1\t-\tS6\n'   # 0x00fdf104: in the span, unlisted
           '0x00fdf008\tkernel32\tInterlockedIncrement\t1\tEnterCriticalSection\tS4 platform/win32\n'
           '0x00fdf00c\tkernel32\tInterlockedDecrement\t1\tLeaveCriticalSection\tS4 platform/win32\n'
           '0x00fdf010\tkernel32\tLeaveCriticalSection\t1\t-\tS4 platform/win32\n')
EXPORTS = ('const exp g_exports[] = { E("advapi32", RegCloseKey), E("kernel32", InterlockedIncrement),\n'
           '    E("kernel32", InterlockedDecrement), E("kernel32", LeaveCriticalSection),\n'
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
    'src/r.cpp': 'struct RTTI { const char *name; RTTI(const char *n); };\n'      # split globals (compiled -O2)
                 '__attribute__((noinline)) RTTI::RTTI(const char *n) : name(n) {}\n'
                 'RTTI ms_kRTTI("Seq");\nconst RTTI *GetRTTI() { return &ms_kRTTI; }\n'
                 'int g_count = 5;\nint *g_pcount = &g_count;\nint bump() { return ++g_count; }\n',
    'src/g.cpp': 'unsigned long fnvnx_game_GetCurrentThreadId();\n'                # GAME1 spellings
                 'extern "C" void fnvnx_game_EnterCriticalSection(void *);\nvoid fnvnx_game_Sleep(unsigned long);\n'
                 'struct CS;\nvoid fnvnx_game_LeaveCriticalSection(CS *);\n'   # C++ linkage, C++-decorated import
                 'int game_calls(void *p) { fnvnx_game_EnterCriticalSection(p); fnvnx_game_Sleep(1); '
                 'fnvnx_game_LeaveCriticalSection((CS *)p); return (int)fnvnx_game_GetCurrentThreadId(); }\n',
    'src/h.cpp': 'unsigned long FUN_00830470() { return 7; }\nvoid FUN_00401410(unsigned long) {}\n'
                 'void *FUN_00402000(unsigned n) { return (void *)n; }\nvoid FUN_00402030(void *) {}\n'
                 'void FUN_00aa1460(void *, unsigned) {}\n',
    'src/n.cpp': 'struct Obj { int a, b; };\nObj *mk_obj() { return new Obj(); }\nvoid del_obj(Obj *p) { delete p; }\n'
                 'int *mk_arr(int n) { return new int[n]; }\nvoid del_arr(int *p) { delete[] p; }\n',
    'src/x.cpp': '/* fnv-nx XINC1: test */ struct FUN_00403000_s1b { void FUN_00403000(int); };\n'   # patched
                 'void FUN_00403000_s1b::FUN_00403000(int) {}\n'
                 'void call_x(FUN_00403000_s1b *p) { p->FUN_00403000(1); }\n',
    'src/z.cpp': 'void FUN_00404000(int) {}\n',                                   # not patched: keeps its name
    'src/s1.cpp': 'int g_hdr = 3;\nint get_hdr1() { return g_hdr; }\n',             # STATIC1 copies (-O2)
    'src/s2.cpp': 'int g_hdr = 3;\nint get_hdr2() { return g_hdr + 1; }\n',
    'src/e.cpp': 'struct BaseX { BaseX(); ~BaseX(); int a; };\nstruct Mem { Mem(); ~Mem(); };\n'   # defined nowhere
                 'struct DerX : BaseX { Mem m; DerX(); };\nDerX::DerX() : BaseX(), m() {}\n'   # cleanup: ~BaseX
                 'void scope_user() { Mem m2; }\n'                                            # normal: ~Mem
                 'struct Shell1 { ~Shell1(); int x; };\nvoid del_shell(Shell1 *p) { delete p; }\n'
                 'struct Real1 { ~Real1(); int x; };\nReal1::~Real1() { x = 0; }\n',
    'src/l.cpp': 'extern "C" void LeaveCriticalSection(void *);\nvoid plain_leave(void *p) { LeaveCriticalSection(p); }\n',
}
RENAMES = ('# source\tkind\taddress\told MSVC name\tnew MSVC name\n'
           'src/x.cpp\tdef\t0x00403000\t?FUN_00403000@@YGXH@Z\t?FUN_00403000@FUN_00403000_s1b@@QAEXH@Z\n'
           'src/x.cpp\tref\t0x00403000\t?FUN_00403000@@YGXH@Z\t?FUN_00403000@FUN_00403000_s1b@@QAEXH@Z\n'
           'src/z.cpp\tdef\t0x00404000\t?FUN_00404000@@YAXH@Z\t?FUN_00404000@FUN_00404000_s1b@@QAEXH@Z\n')
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
    {'source': 'src/r.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        F(0x401200, '??0RTTI@@QAE@PBD@Z'), F(0x401210, '?GetRTTI@@YAPBURTTI@@XZ'), F(0x401220, '?bump@@YAHXZ')],
     'symbols': {'?ms_kRTTI@@3URTTI@@A': '0x01000a00', '?g_count@@3HA': '0x01000a10',
                 '?g_pcount@@3PAHA': '0x01000a20'}},
    {'source': 'src/g.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [F(0x401400, '?game_calls@@YAHPAX@Z')],
     'symbols': {'?GetCurrentThreadId@@YGKXZ': '0x00830470', '__imp__EnterCriticalSection@4': '0x00fdf008',
                 '?Sleep@@YGXK@Z': '0x00401410',
                 '__imp_?LeaveCriticalSection@@YGXPAUCS@@@Z': '0x00fdf00c'}},
    {'source': 'src/l.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [F(0x401600, '?plain_leave@@YAXPAX@Z')],
     'symbols': {'__imp__LeaveCriticalSection@4': '0x00fdf010'}},     # the real slot, in another unit
    {'source': 'src/h.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        F(0x830470, '?FUN_00830470@@YAKXZ'), F(0x401410, '?FUN_00401410@@YAXK@Z'),
        F(0x402000, '?FUN_00402000@@YAPAXI@Z'), F(0x402030, '?FUN_00402030@@YAXPAX@Z'),
        F(0xaa1460, '?FUN_00aa1460@@YAXPAXI@Z')],
     'symbols': {'??3@YAXPAXI@Z': '0x00aa1460'}},
    {'source': 'src/n.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        F(0x401500, '?mk_obj@@YAPAUObj@@XZ'), F(0x401510, '?del_obj@@YAXPAUObj@@@Z'),
        F(0x401520, '?mk_arr@@YAPAHH@Z'), F(0x401530, '?del_arr@@YAXPAH@Z')],
     'symbols': {'??2@YAPAXI@Z': '0x00402000', '??3@YAXPAX@Z': '0x00402030', '??_U@YAPAXI@Z': '0x00402000',
                 '??_V@YAXPAX@Z': '0x00402030'}},
    {'source': 'src/x.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        F(0x403000, '?FUN_00403000@@YGXH@Z'), F(0x403010, '?call_x@@YAXPAUFUN_00403000_s1b@@@Z')],
     'symbols': {}},
    {'source': 'src/z.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [F(0x404000, '?FUN_00404000@@YAXH@Z')],
     'symbols': {}},
    {'source': 'src/s1.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [F(0x401800, '?get_hdr1@@YAHXZ')],
     'symbols': {'?g_hdr@@3HA': '0x01000b00'}},
    {'source': 'src/s2.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [F(0x401810, '?get_hdr2@@YAHXZ')],
     'symbols': {}},
    {'source': 'src/e.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        F(0x401700, '??0DerX@@QAE@XZ'), F(0x401710, '?scope_user@@YAXXZ'), F(0x401720, '?del_shell@@YAXPAUShell1@@@Z'),
        F(0x401730, '??1Real1@@QAE@XZ')],
     'symbols': {'??0BaseX@@QAE@XZ': '0x00401740', '??0Mem@@QAE@XZ': '0x00401750',
                 '??_GShell1@@QAEPAXI@Z': '0x00401760', '??_GReal1@@QAEPAXI@Z': '0x00401760'}},
]}

ok = True


def local_refs_into(path, names):
    """relocations of allocated sections that reach one of the defined symbols `names` through a local symbol"""
    o = vtables.Obj(path)
    rng = collections.defaultdict(list)
    for s_ in o.syms:
        if s_[0] in names and s_[5]:
            rng[s_[5]].append((s_[1], s_[1] + max(1, s_[2])))
    n = 0
    for i in range(len(o.secnames)):
        if not o.secs[i][2] & 2:   # allocated sections only (debug info keeps section-relative references)
            continue
        for off, (si, _, add) in o.rel(i).items():
            s_ = o.syms[si]
            if s_[4] == 0 and s_[5] in rng and s_[0] not in names:   # local (binding 0), same section
                at = (0 if s_[3] == 3 else s_[1]) + add
                n += any(lo <= at < hi for lo, hi in rng[s_[5]])
    return n


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
            run([CXX, '-mabi=ilp32', '-O2' if rel in ('src/r.cpp', 'src/s1.cpp', 'src/s2.cpp') else '-O1', '-c', p,
                 '-o', objs[rel]])
        units = os.path.join(tree, 'build', 'units.json')
        json.dump(UNITS, open(units, 'w'))
        with open(os.path.join(t, 'objects.tsv'), 'w') as f:
            for rel in SRC:
                f.write('%s\t%s\tok\n' % (rel, objs[rel]))
        plat = os.path.join(t, 'platform')
        os.makedirs(plat)
        for name, text in (('crt_symbols.tsv', CRT), ('imports.tsv', IMPORTS), ('exports.cpp', EXPORTS),
                           ('units_renames.tsv', RENAMES)):
            open(os.path.join(plat, name), 'w').write(text)
        r_before = local_refs_into(objs['src/r.cpp'], ('ms_kRTTI', 'g_count', 'g_pcount'))
        out, fixed = os.path.join(t, 'out'), os.path.join(t, 'fixed')
        run([sys.executable, FNVLINK, 'resolve', units, os.path.join(t, 'objects.tsv'), out, '--crt-symbols',
             os.path.join(plat, 'crt_symbols.tsv'), '--imports', os.path.join(plat, 'imports.tsv'),
             '--units-renames', os.path.join(plat, 'units_renames.tsv')])
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
        g = 'src/g.cpp'
        for name, want in (('_Z29fnvnx_game_GetCurrentThreadIdv', '__fnv_00830470'),
                           ('fnvnx_game_EnterCriticalSection', 'InterlockedIncrement'),
                           ('_Z16fnvnx_game_Sleepm', '__fnv_00401410'),
                           ('_Z31fnvnx_game_LeaveCriticalSectionP2CS', 'InterlockedDecrement')):
            check(refs.get((g, name), [''] * 6)[5] == want, 'GAME1 %s -> %s: %s' % (name, want, refs.get((g, name))))
        n_ = 'src/n.cpp'
        for name, want in (('_Znwm', '__fnv_00402000'), ('_ZdlPvm', '__fnv_00aa1460'), ('_Znam', '__fnv_00402000'),
                           ('_ZdaPv', '__fnv_00402030')):
            check(refs.get((n_, name), [''] * 6)[5] == want, 'game allocator %s -> %s: %s' % (name, want,
                                                                                       refs.get((n_, name))))
        check(rows.get(0x403000, [''] * 4)[1:4] == ['F', '__fnv_00403000', '_ZN16FUN_00403000_s1b12FUN_00403000Ei'],
              'S1b-renamed definition matched: %s' % rows.get(0x403000))
        check(refs.get(('src/x.cpp', '_ZN16FUN_00403000_s1b12FUN_00403000Ei'), [''] * 6)[5] in ('', '__fnv_00403000'),
              'S1b-renamed reference: %s' % refs.get(('src/x.cpp', '_ZN16FUN_00403000_s1b12FUN_00403000Ei')))
        check(rows.get(0x404000, [''] * 4)[1:4] == ['F', '__fnv_00404000', '_Z12FUN_00404000i'],
              'unpatched unit keeps its name: %s' % rows.get(0x404000))
        e_ = 'src/e.cpp'
        r_ = refs.get((e_, '_ZN5BaseXD2Ev'), [''] * 6)
        check(r_[2] == 'ctor-dtor-trap' and r_[5].startswith('__fnvnx_xtrap_'), 'cleanup-only dtor -> trap: %s' % r_)
        r_ = refs.get((e_, '_ZN3MemD1Ev'), [''] * 6)
        check(r_[2] == 'unresolved-ctor-dtor' and not r_[5], 'normal-path dtor stays undefined: %s' % r_)
        r_ = refs.get((e_, '_ZN6Shell1D1Ev'), [''] * 6)
        check(r_[2] == 'ctor-dtor-alias' and r_[5] == '_ZN5Real1D1Ev', 'dtor sharing a ??_G -> its native dtor: %s' % r_)
        check('constructor/destructor defined nowhere: _ZN5BaseXD2Ev (reached by exception unwinding)'
              in open(os.path.join(out, 'bridges.S')).read(), 'trap stub text')
        dd = {l.split('\t')[1] for l in open(os.path.join(out, 'data_defs.tsv'))}
        check({'ms_kRTTI', 'g_count', 'g_pcount'} <= dd, 'split-global variables are data defs: %s' % sorted(dd))
        hdr = {l.split('\t')[2].strip() for l in open(os.path.join(out, 'data_defs.tsv')) if l.split('\t')[1] == 'g_hdr'}
        check(hdr == {'src/s1.cpp', 'src/s2.cpp'}, 'every STATIC1 copy rebound: %s' % sorted(hdr))
        run([sys.executable, FNVLINK, 'apply', out, fixed])
        run([sys.executable, FNVLINK, 'check', out, fixed])
        t_o = vtables.Obj(os.path.join(fixed, os.path.basename(objs['src/t.cpp'])))
        call_t = [s_ for s_ in t_o.syms if s_[0] == 'call_t'][0]
        targets = {t_o.syms[si][0] for off, (si, _, _) in t_o.rel(call_t[5]).items()
                   if call_t[1] <= off < call_t[1] + call_t[2]}
        check('FUN_00ee6df2' not in t_o.defs and 'D3DXVec3TransformNormal' in targets,
              'the thunk\'s own unit calls the import: %s' % sorted(targets))
        r_obj = os.path.join(fixed, os.path.basename(objs['src/r.cpp']))
        check(r_before > 0, 'the -O2 unit reaches its variables through local symbols (%d relocations)' % r_before)
        check(local_refs_into(r_obj, ('__fnv_01000a00', '__fnv_01000a10', '__fnv_01000a20')) == 0,
              'no local-symbol reference into a rebound variable after apply')
        s2_obj = os.path.join(fixed, os.path.basename(objs['src/s2.cpp']))
        check(local_refs_into(s2_obj, ('__fnv_01000b00',)) == 0 and
              '__fnv_01000b00' in {vtables.Obj(s2_obj).syms[si][0] for i in range(len(vtables.Obj(s2_obj).secnames))
                                   for _, (si, _, _) in vtables.Obj(s2_obj).rel(i).items()},
              'the unbound copy\'s own reference goes to the image address')
        r_o = vtables.Obj(r_obj)
        by = collections.defaultdict(set)
        for i, sec in enumerate(r_o.secnames):
            for off, (si, _, _) in r_o.rel(i).items():
                by[sec].add(r_o.syms[si][0])
        check('__fnv_01000a00' in set().union(*(v for k, v in by.items() if k.startswith('.text'))),
              'GetRTTI / the static initializer reach __fnv_01000a00: %s' % dict(by))
        check('__fnv_01000a10' in set().union(*(v for k, v in by.items() if k.startswith('.data'))),
              'g_pcount points at __fnv_01000a10: %s' % dict(by))
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
        check(sym.get('__fnv_01000a00', (0, -1))[1] == 0x01000a00, 'rebound variable at its image address')
    print('ok' if ok else 'FAILED')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
