#!/usr/bin/env python3
"""Bindings of the link step that do not go to a decomp definition, on hand-written units (no decomp content).

    python3 tools/link/test_bindings.py     # needs aarch64-linux-gnu-{g++,ld,objcopy,objdump}, llvm-undname

* imports (imports.tsv + exports.cpp): a call through an IAT slot binds to the slot's api, whatever the unit calls
  it; import thunks (6-byte decomp functions whose body only calls an import) bind to the import, not to their
  compiled body (which loses or retypes arguments); a 6-byte getter stays native;
* thread-local variables at image .tls addresses (two units, two names, one address; a native definition at
  another): TLS references to slots of one native TLS block, never to an absolute image address;
* image vftable addresses stored into vptrs by hand: V entries (named vftable; class of the storing constructor) and
  the data symbol bound to the native address point;
* split globals (S13): a variable defined at an image address by the unit itself - compiled like compile.sh
  (all units here: -O2 -fPIC, through the GOT by symbol; plain -O2 uses section anchors, the control), its functions, its static
  initializer and a pointer in its data reach the rebound symbol, and no PC-relative relocation names an absolute
  image symbol (S5's relocatable guest);
* GAME1 (fnvnx_game_<name>): looked up by the unit's own name (a game function, another import's slot);
* GCC's global operator new/delete -> the game's, by the MSVC names the units bind (sized delete: the sized one);
* S1b units_renames.tsv: applied to a unit whose source carries the patch, not to an unpatched one;
* destructors defined nowhere that GCC calls where MSVC did not: one reached only by a constructor's exception
  cleanup -> trap stub; one at the end of a scope (normal code) stays undefined; one of a class sharing its deleting
  destructor (??_G address) with a class whose destructor is compiled -> that destructor;
* a direct call of an IAT slot's address (a decomp dllimport function GCC calls with bl) -> the slot's api;
* an ecx-not-passed pair (stdcall f(void*) referenced, fastcall f(p0, edx, p2) defined): listed x86-inconsistent,
  still bridged for the argument the reference passes;
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
READELF = os.environ.get('READELF', 'aarch64-linux-gnu-readelf')
PIC = ['-O2', '-fPIC']   # compile.sh's addressing of the variables a unit defines

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
                 'int g_count = 5;\nint *g_pcount = &g_count;\nint bump() { return ++g_count; }\n'
                 # a second definition of g_count's image address in the same object (tesform.cpp, S2c-abi)
                 'namespace fnv_00401230 { int g_count; int bump2() { return ++g_count; } }\n',
    # S2c-abi: an override of a secondary base's virtual, entered by MSVC with this at the base (thisadj.py)
    'src/mi.cpp': 'struct A { virtual void fa(); int a; };\nstruct B { virtual int fb(int); int b; };\n'
                  'struct D : A, B { void fa(); int fb(int); int d; };\n'
                  'void A::fa() {}\nint B::fb(int x) { return x; }\nvoid D::fa() {}\nint D::fb(int x) { return d + x; }\n',
    # V2 T1: a shell declared at D's B subobject calls D::fb's address directly, passing the subobject
    'src/sub.cpp': 'struct Sub { int fb(int); int b; };\nint call_sub(Sub *s) { return s->fb(1); }\n',
    # a variable placed at a .text address nothing defines: its address is the x86 value (S2c-abi)
    'src/ta.cpp': 'extern const char g_011c5a4c_arg1[];\nconst char *ta_arg() { return g_011c5a4c_arg1; }\n'
                  # V2 X2: a char label at a .text address passed to a call (atexit) may be code: no absolute value
                  'extern char UNK_00fdd3b0;\nextern char FUN_00a00010;\nvoid reg_cb(void *);\nvoid *g_dat;\n'
                  'void ta_reg() { reg_cb(&UNK_00fdd3b0); g_dat = &FUN_00a00010; }\n',
    'src/g.cpp': 'unsigned long fnvnx_game_GetCurrentThreadId();\n'                # GAME1 spellings
                 'extern "C" void fnvnx_game_EnterCriticalSection(void *);\nvoid fnvnx_game_Sleep(unsigned long);\n'
                 'struct CS;\nvoid fnvnx_game_LeaveCriticalSection(CS *);\n'   # C++ linkage, C++-decorated import
                 'int game_calls(void *p) { fnvnx_game_EnterCriticalSection(p); fnvnx_game_Sleep(1); '
                 'fnvnx_game_LeaveCriticalSection((CS *)p); return (int)fnvnx_game_GetCurrentThreadId(); }\n',
    'src/h.cpp': 'unsigned long FUN_00830470() { return 7; }\nvoid FUN_00401410(unsigned long) {}\n'
                 'void *FUN_00402000(unsigned n) { return (void *)n; }\nvoid FUN_00402030(void *) {}\n'
                 'void FUN_00aa1460(void *, unsigned) {}\n',
    # S13 J-0114: a unit-local namespaced copy of a class binds like the global class (by address, else by the
    # native definition of the global name)
    'src/ns.cpp': 'template <class T> struct PL { T *p; ~PL(); };\n'
                  'namespace fnv_00406400 { struct NS { int a; int get() const; };\n'
                  'struct NT { int b; int peek() const; };\n'
                  'int use_ns(NS *p, NT *q) { return p->get() + q->peek(); }\n'
                  'void drop_pl(PL<NT*> *x) { x->~PL(); } }\n',
    'src/nsg.cpp': 'struct NS { int a; int get() const; };\nint NS::get() const { return a; }\n'
                   'struct NT { int b; int peek() const; };\nint NT::peek() const { return b; }\n'
                   'template <class T> struct PL { T *p; ~PL() { p = 0; } };\ntemplate struct PL<NT*>;\n',
    # J-0108: a class's own operator new / delete that nothing defines -> the game's allocator
    'src/cd.cpp': 'struct CD { virtual ~CD(); int x; static void *operator new(unsigned long); '
                  'static void operator delete(void *); static void operator delete[](void *); };\n'
                  'CD::~CD() {}\nCD *mk_cd() { return new CD; }\nvoid del_cd(CD *p) { delete p; }\n'
                  'void del_cds(CD *p) { ::operator delete[](p); CD::operator delete[](p); }\n',
    'src/n.cpp': 'struct Obj { int a, b; };\nObj *mk_obj() { return new Obj(); }\nvoid del_obj(Obj *p) { delete p; }\n'
                 'int *mk_arr(int n) { return new int[n]; }\nvoid del_arr(int *p) { delete[] p; }\n',
    'src/x.cpp': '/* fnv-nx XINC1: test */ struct FUN_00403000_s1b { void FUN_00403000(int); };\n'   # patched
                 'void FUN_00403000_s1b::FUN_00403000(int) {}\n'
                 'void call_x(FUN_00403000_s1b *p) { p->FUN_00403000(1); }\n',
    'src/z.cpp': 'void FUN_00404000(int) {}\n',                                   # not patched: keeps its name
    'src/c2.cpp': 'extern "C" void FUN_00fdf008(void *);\nvoid call_iat(void *p) { FUN_00fdf008(p); }\n',
    # S2 s2d-010 item: an import declared without extern "C" (C++-mangled), the slot's api has no exports.cpp entry
    # (a platform module defines it under its C name): the reference binds to the api's C name
    'src/d3.cpp': 'void *D3DXVec3Normalize(void *, const void *);\n'
                  'void *call_vn(void *o, const void *v) { return D3DXVec3Normalize(o, v); }\n',
    'src/q1.cpp': 'bool FUN_00405000(void *, int, void *p2) { return p2 != 0; }\n',
    'src/q2.cpp': 'bool FUN_00405000(void *);\nbool call_q(void *p) { return FUN_00405000(p); }\n',
    'src/s1.cpp': 'int g_hdr = 3;\nint get_hdr1() { return g_hdr; }\n',             # STATIC1 copies (-O2)
    'src/s2.cpp': 'int g_hdr = 3;\nint get_hdr2() { return g_hdr + 1; }\n',
    'src/e.cpp': 'struct BaseX { BaseX(); ~BaseX(); int a; };\nstruct Mem { Mem(); ~Mem(); };\n'   # defined nowhere
                 'struct DerX : BaseX { Mem m; DerX(); };\nDerX::DerX() : BaseX(), m() {}\n'   # cleanup: ~BaseX
                 'void scope_user() { Mem m2; }\n'                                            # normal: ~Mem
                 'struct Shell1 { ~Shell1(); int x; };\nvoid del_shell(Shell1 *p) { delete p; }\n'
                 'struct Real1 { ~Real1(); int x; };\nReal1::~Real1() { x = 0; }\n',
    'src/l.cpp': 'extern "C" void LeaveCriticalSection(void *);\nvoid plain_leave(void *p) { LeaveCriticalSection(p); }\n',
    # S5 J-0102: decomp shells storing image vtables with `*(int*)this = (int)&DAT_x`; another unit names the
    # shells' addresses as the constructors of Real2 (compiled vtable) and Lazy (polymorphic, no vtable anywhere)
    'src/v.cpp': 'struct ShellV { void FUN_00406000(); void FUN_00406010(); };\n'
                 'extern int DAT_01000820;\nextern int DAT_01000830;\n'
                 '// @fnv 0x00406000\nvoid ShellV::FUN_00406000() { *(int*)this = (int)&DAT_01000820; }\n'
                 '// @fnv 0x00406010\nvoid ShellV::FUN_00406010() { *(int*)this = (int)&DAT_01000830; }\n',
    # S5 J-0109: a V entry whose vptr store is not recognised (a comparison) and a secondary vptr stored at +8
    'src/m.cpp': 'struct Named2 { virtual int g2(); };\nint Named2::g2() { return 6; }\n'
                 'Named2 *mk_n2() { return new Named2; }\nextern int DAT_01000740;\n'
                 'bool is_n2(void *p) { return *(void **)p == (void *)&DAT_01000740; }\n'
                 'struct MB1 { virtual int b1(); int x; };\nstruct MB2 { virtual int b2(); };\nint MB1::b1() { return 1; }\n'
                 'int MB2::b2() { return 2; }\nstruct MI : MB1, MB2 { void set(); };\nMI *mk_mi() { return new MI; }\n'
                 'extern int DAT_01000850;\n'
                 'void MI::set() { *(void **)((char *)this + 8) = &DAT_01000850; }\n',
    'src/w.cpp': 'struct Real2 { virtual int r(); virtual int s(); Real2(); };\nint Real2::r() { return 4; }\n'
                 'int Real2::s() { return 5; }\nstruct Lazy { virtual int z(); Lazy(); };\n'
                 'Lazy *mk_lazy() { return new Lazy; }\n',
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
    {'source': 'src/d3.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [F(0x401380, '?call_vn@@YAPAXPAXPBX@Z')],
     'symbols': {'__imp_?D3DXVec3Normalize@@YGPAXPAXPBX@Z': '0x00fdf108'}},
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
        F(0x401200, '??0RTTI@@QAE@PBD@Z'), F(0x401210, '?GetRTTI@@YAPBURTTI@@XZ'), F(0x401220, '?bump@@YAHXZ'),
        F(0x401230, '?bump2@fnv_00401230@@YAHXZ')],
     'symbols': {'?ms_kRTTI@@3URTTI@@A': '0x01000a00', '?g_count@@3HA': '0x01000a10',
                 '?g_count@fnv_00401230@@3HA': '0x01000a10',
                 '?g_pcount@@3PAHA': '0x01000a20'}},
    {'source': 'src/mi.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        F(0x401580, '?fb@D@@UAEHH@Z'), F(0x401590, '?fa@D@@UAEXXZ')], 'symbols': {}},
    {'source': 'src/sub.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [F(0x4015a0, '?call_sub@@YAHPAUSub@@@Z')],
     'symbols': {'?fb@Sub@@QAEHH@Z': '0x00401580'}},
    {'source': 'src/ta.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [F(0x401240, '?ta_arg@@YAPBDXZ'), F(0x401250, '?ta_reg@@YAXXZ')],
     'symbols': {'?g_011c5a4c_arg1@@3QBDB': '0x00a00000', '?UNK_00fdd3b0@@3DA': '0x00fdd3b0',
                 '?FUN_00a00010@@3DA': '0x00a00010'}},
    {'source': 'src/g.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [F(0x401400, '?game_calls@@YAHPAX@Z')],
     'symbols': {'?GetCurrentThreadId@@YGKXZ': '0x00830470', '__imp__EnterCriticalSection@4': '0x00fdf008',
                 '?Sleep@@YGXK@Z': '0x00401410',
                 '__imp_?LeaveCriticalSection@@YGXPAUCS@@@Z': '0x00fdf00c'}},
    {'source': 'src/l.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [F(0x401600, '?plain_leave@@YAXPAX@Z')],
     'symbols': {'__imp__LeaveCriticalSection@4': '0x00fdf010',      # the real slot, in another unit
                 '?FUN_00fdf008@@3P6GXPAX@ZA': '0x00fdf008'}},        # the slot as a function-pointer variable
    {'source': 'src/c2.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [F(0x401900, '?call_iat@@YAXPAX@Z')],
     'symbols': {}},
    {'source': 'src/q1.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [F(0x405000, '?FUN_00405000@@YI_NPAXH0@Z')],
     'symbols': {}},
    {'source': 'src/q2.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [F(0x405100, '?call_q@@YA_NPAX@Z')],
     'symbols': {'?FUN_00405000@@YG_NPAX@Z': '0x00405000'}},
    {'source': 'src/h.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        F(0x830470, '?FUN_00830470@@YAKXZ'), F(0x401410, '?FUN_00401410@@YAXK@Z'),
        F(0x402000, '?FUN_00402000@@YAPAXI@Z'), F(0x402030, '?FUN_00402030@@YAXPAX@Z'),
        F(0xaa1460, '?FUN_00aa1460@@YAXPAXI@Z')],
     'symbols': {'??3@YAXPAXI@Z': '0x00aa1460'}},
    {'source': 'src/ns.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        F(0x406400, '?use_ns@fnv_00406400@@YAHPAUNS@1@PAUNT@1@@Z')], 'symbols': {}},
    {'source': 'src/nsg.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [F(0x406410, '?get@NS@@QBEHXZ')],
     'symbols': {}},
    {'source': 'src/cd.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        F(0x406300, '??1CD@@UAE@XZ'), F(0x406310, '?mk_cd@@YAPAUCD@@XZ'), F(0x406320, '?del_cd@@YAXPAUCD@@@Z'),
        F(0x406330, '?del_cds@@YAXPAUCD@@@Z')], 'symbols': {}},
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
    {'source': 'src/m.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        F(0x406200, '?g2@Named2@@UAEHXZ'), F(0x406210, '?is_n2@@YA_NPAX@Z'), F(0x406220, '?set@MI@@QAEXXZ')],
     'symbols': {'??_7Named2@@6B@': '0x01000740', '?DAT_01000740@@3HA': '0x01000740',
                 '?DAT_01000850@@3HA': '0x01000850'}},
    {'source': 'src/v.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        F(0x406000, '?FUN_00406000@ShellV@@QAEXXZ'), F(0x406010, '?FUN_00406010@ShellV@@QAEXXZ')],
     'symbols': {'?DAT_01000820@@3HA': '0x01000820', '?DAT_01000830@@3HA': '0x01000830'}},
    {'source': 'src/w.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        F(0x406100, '?r@Real2@@UAEHXZ'), F(0x406110, '?s@Real2@@UAEHXZ'), F(0x406120, '?mk_lazy@@YAPAULazy@@XZ')],
     'symbols': {'??0Real2@@QAE@XZ': '0x00406000', '??0Lazy@@QAE@XZ': '0x00406010'}},
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
            opt = PIC   # like compile.sh
            run([CXX, '-mabi=ilp32', *opt, '-c', p, '-o', objs[rel]])
        units = os.path.join(tree, 'build', 'units.json')
        json.dump(UNITS, open(units, 'w'))
        with open(os.path.join(t, 'objects.tsv'), 'w') as f:
            for rel in SRC:
                f.write('%s\t%s\tok\n' % (rel, objs[rel]))
        plat = os.path.join(t, 'platform')
        os.makedirs(plat)
        for name, text in (('imports.tsv', IMPORTS), ('exports.cpp', EXPORTS),
                           ('units_renames.tsv', RENAMES)):
            open(os.path.join(plat, name), 'w').write(text)
        r_before = local_refs_into(objs['src/r.cpp'], ('ms_kRTTI', 'g_count', 'g_pcount'))
        anchored = os.path.join(t, 'r_anchor.o')     # the control: plain -O2 addresses them from a section anchor
        run([CXX, '-mabi=ilp32', '-O2', '-c', os.path.join(tree, 'src/r.cpp'), '-o', anchored])
        r_anchor = local_refs_into(anchored, ('ms_kRTTI', 'g_count', 'g_pcount'))
        out, fixed = os.path.join(t, 'out'), os.path.join(t, 'fixed')
        sec = os.path.join(t, 'secondary.tsv')   # what thisadj reads from the exe's vftables: D::fb only in B's
        open(sec, 'w').write('00401580\t8\n')
        os.environ['FNVNX_SECONDARY_ENTRIES'] = sec
        run([sys.executable, FNVLINK, 'resolve', units, os.path.join(t, 'objects.tsv'), out, '--imports', os.path.join(plat, 'imports.tsv'),
             '--units-renames', os.path.join(plat, 'units_renames.tsv')])
        refs = {(r[0], r[1]): r for r in (l.rstrip('\n').split('\t') for l in open(os.path.join(out, 'refs.tsv'))
                                          if not l.startswith('#'))}
        rows = {int(r[0], 16): r for r in (l.rstrip('\n').split('\t') for l in open(os.path.join(out, 'addrmap.tsv'))
                                          if not l.startswith('#'))}
        a = 'src/a.cpp'
        check(refs.get((a, 'EnterCriticalSection'), [''] * 6)[5] == 'RegCloseKey', 'import -> the slot\'s api: %s'
              % refs.get((a, 'EnterCriticalSection')))
        for unit, name in ((a, 'g_tlsA'), ('src/b.cpp', 't_otherName')):
            r = refs.get((unit, name), [''] * 6)
            check(r[2] == 'tls' and r[5] == '__fnvnx_tls_01272010', 'TLS ref %s: %s' % (name, r))
        check(rows.get(0x01000600, [''] * 3)[1:3] == ['V', '_ZTV4Poly'], 'hand-written vptr, storing ctor\'s class: %s'
              % rows.get(0x01000600))
        check(rows.get(0x01000700, [''] * 3)[1:3] == ['V', '_ZTV5Named'], 'hand-written vptr, named vftable')
        check(0x01000800 not in rows and 0x01000810 not in rows, 'a function storing two vftables maps neither')
        check(rows.get(0x01000820, [''] * 3)[1:3] == ['V', '_ZTV5Real2'],
              '`*(int*)this = (int)&DAT_x` in a shell -> the class constructed at its address: %s' % rows.get(0x01000820))
        r_ = refs.get(('src/ns.cpp', '_ZNK12fnv_004064002NS3getEv'), [''] * 6)
        check(r_[3].startswith('ns/') and r_[4] == '00406410', 'namespaced copy bound by the global name\'s address: %s'
              % r_)
        r_ = refs.get(('src/ns.cpp', '_ZNK12fnv_004064002NT4peekEv'), [''] * 6)
        check(r_[2] == 'ns-alias' and r_[5] == '_ZNK2NT4peekEv', 'namespaced copy -> the native global definition: %s'
              % r_)
        r_ = refs.get(('src/ns.cpp', '_ZN2PLIPN12fnv_004064002NTEED1Ev'), [''] * 6)
        check(r_[2] == 'ns-alias' and r_[5] == '_ZN2PLIP2NTED1Ev', 'template over a pointer to a namespaced copy -> the '
              'global instantiation\'s D1: %s' % r_)
        for name, want in (('_ZN2CDnwEm', '__fnv_00402000'), ('_ZN2CDdlEPv', '__fnv_00402030'),
                           ('_ZN2CDdaEPv', '__fnv_00402030')):
            r_ = refs.get(('src/cd.cpp', name), [''] * 6)
            check(r_[3] == 'game/newdel-member' and r_[5] == want, 'class operator %s -> the game allocator: %s'
                  % (name, r_))
        ld_ = open(os.path.join(out, 'aliases.ld')).read()
        check('__fnv_01000740 = _ZTV6Named2 + 8;' in ld_, 'a V entry without a recognised store: its data symbol is '
              'the address point: %s' % [l for l in ld_.splitlines() if '01000740' in l])
        check(rows.get(0x01000850, [''] * 3)[1:3] == ['V', '_ZTV2MI'] and '__fnv_01000850 = _ZTV2MI + 20;' in ld_,
              'a vptr stored at +8: MI\'s secondary address point: %s %s' % (
                  rows.get(0x01000850), [l for l in ld_.splitlines() if '01000850' in l]))
        hv = {r[0]: r for r in (l.rstrip('\n').split('\t') for l in open(os.path.join(out, 'hand_vptrs.tsv'))
                                if not l.startswith('#'))}
        req = open(os.path.join(out, 'vtable_requests.tsv')).read()
        check(hv.get('01000830', [''] * 2)[1] == 'requested for synthesis: Lazy' and 'Lazy' in req and
              'hand-vptr' in req and 0x01000830 not in rows,
              'no vtable yet: Lazy requested for synthesis: %s / %s' % (hv.get('01000830'),
                                                                        [l for l in req.splitlines() if 'Lazy' in l]))
        for a_, sym in ((0xee6e04, 'D3DXMatrixRotationYawPitchRoll'), (0xee6df2, 'D3DXVec3TransformNormal')):
            check(rows.get(a_, [''] * 4)[1:4] == ['F', sym, sym], 'import thunk %x -> %s: %s' % (a_, sym, rows.get(a_)))
        r_ = refs.get(('src/d3.cpp', '_Z17D3DXVec3NormalizePvPKv'), [''] * 6)
        check(r_[2] == 'import' and r_[5] == 'D3DXVec3Normalize',
              'a C++-declared import with no export entry binds to the api\'s C name: %s' % (r_,))
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
        r_ = refs.get(('src/q2.cpp', '_Z12FUN_00405000Pv'), [''] * 6)
        abi_ = [l.split('\t') for l in open(os.path.join(out, 'abi.tsv')) if l.startswith('src/q2.cpp\t')]
        check(r_[2] == 'code-bridge' and r_[5].startswith('__fnvnx_br_00405000') and abi_ and
              abi_[0][5] == 'x86-inconsistent' and 'ecx-not-passed' in abi_[0][6],
              'ecx-not-passed: listed x86-inconsistent, still bridged: %s %s' % (r_, abi_[:1]))
        r_ = refs.get(('src/c2.cpp', 'FUN_00fdf008'), [''] * 6)
        check(r_[2] == 'import' and r_[5] == 'InterlockedIncrement', 'call of an IAT slot address -> its api: %s' % r_)
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
        check(r_anchor > 0 and r_before == 0, 'plain -O2 reaches the variables through an anchor (%d), -fPIC by '
              'symbol (%d)' % (r_anchor, r_before))
        pcrel = [l for f_ in (r_obj, os.path.join(fixed, os.path.basename(objs['src/s2.cpp'])))
                 for l in run([READELF, '-rW', f_]).split('\n') if '__fnv_0100' in l and
                 any(k in l for k in ('ADR_PREL', 'ABS_LO12', 'LD_PREL', 'PREL32'))]
        check(not pcrel, 'no PC-relative relocation against an image symbol: %s' % pcrel[:3])
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
        check(rows.get(0x401580, [''] * 4)[2:4] == ['__fnvnx_msvc_00401580', '_ZThn8_N1D2fbEi'] and
              rows.get(0x401590, [''] * 3)[2] == '__fnv_00401590' and
              sym.get('__fnvnx_msvc_00401580', (0, -1))[1] == sym.get('_ZThn8_N1D2fbEi', (0, -2))[1] and
              sym.get('__fnv_00401580', (0, -1))[1] == sym.get('_ZN1D2fbEi', (0, -2))[1],
              'secondary-base override: the map names the thunk, __fnv_ stays the primary entry: %s %s' % (
                  rows.get(0x401580), rows.get(0x401590)))
        r_ = refs.get(('src/sub.cpp', '_ZN3Sub2fbEi'), [''] * 6)
        check(r_[5] == '__fnvnx_msvc_00401580', 'a call through a shell at the subobject reaches the thunk: %s' % r_)
        r_ = refs.get(('src/ta.cpp', 'UNK_00fdd3b0'), [''] * 6)
        r2_ = refs.get(('src/ta.cpp', 'FUN_00a00010'), [''] * 6)
        check(r_[2] == 'extern-text' and r2_[2] == 'text-addr',
              'a char label passed to a call stays extern-text, one stored as data is text-addr: %s %s' % (r_, r2_))
        r_ = refs.get(('src/ta.cpp', 'g_011c5a4c_arg1'), [''] * 6)
        check(r_[2] == 'text-addr' and sym.get('__fnvnx_textaddr_00a00000', (0, -1))[1] == 0x00a00000,
              'a variable at an undefined .text address -> absolute symbol there: %s %s' % (
                  r_, sym.get('__fnvnx_textaddr_00a00000')))
        check(sym.get('__fnv_01000a10_d1', (0, -1))[1] == 0x01000a10 and
              '__fnv_01000a10_d1 = __fnv_01000a10;' in ld_ and
              '__fnv_01000a10_d1' in set().union(*(v for k, v in by.items() if k.startswith('.text'))),
              'a second definition of the address in one object reaches the image too: %s' % (
                  sym.get('__fnv_01000a10_d1'),))
    print('ok' if ok else 'FAILED')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
