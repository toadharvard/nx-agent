// invented fixture: record types for the layout rule R7 (one definition each; shapes mirror the cases of test_gate_r7.py)
struct UPlain { int a; char b; };
struct USizeBad { int a; char b; };
struct UOffBad { int a; char b; };
struct UMissing { int a; char b; };
struct UBits { unsigned p : 5; unsigned x : 3; };
struct UBitsBad { unsigned p : 5; unsigned x : 3; };
struct UBitsUnnamed { unsigned : 5; unsigned x : 3; };
struct UDer : UPlain { int z; };
struct UDerBad : UPlain { int z; };
struct UVf { virtual void f(); int m; };
struct UVfDiff { virtual void f(); int m; };
struct UVb : virtual UPlain { };
struct UAlt { int a; } __attribute__((aligned(4)));
struct UAlign { int a; } __attribute__((aligned(8)));
struct __attribute__((packed)) UPk { char a; };
struct UFwd { int q; };
struct UHold { UFwd h; int i; };
struct UHoldPlain { UPlain h; };
struct UReal { long double r; };
struct UPtr { int *p; };
struct UPtrMem { int UPlain::*m; };
struct UArr { int a[3]; };
struct UMod { const int c; };
struct UAmb { int a; };
struct UDup { int a; int b; };
union TUn { int a; char b; };
struct UEmpty { };
struct UNest { struct Inner { }; int a; };
struct UNoCl { int a; };
namespace foo { struct E { int a; }; }
// each record is complete and used (by pointer) so the layout plugin sees it
int use_UPlain(UPlain *x) { return x != 0; }
int use_USizeBad(USizeBad *x) { return x != 0; }
int use_UOffBad(UOffBad *x) { return x != 0; }
int use_UMissing(UMissing *x) { return x != 0; }
int use_UBits(UBits *x) { return x != 0; }
int use_UBitsBad(UBitsBad *x) { return x != 0; }
int use_UBitsUnnamed(UBitsUnnamed *x) { return x != 0; }
int use_UDer(UDer *x) { return x != 0; }
int use_UDerBad(UDerBad *x) { return x != 0; }
int use_UVf(UVf *x) { return x != 0; }
int use_UVfDiff(UVfDiff *x) { return x != 0; }
int use_UVb(UVb *x) { return x != 0; }
int use_UAlt(UAlt *x) { return x != 0; }
int use_UAlign(UAlign *x) { return x != 0; }
int use_UPk(UPk *x) { return x != 0; }
int use_UFwd(UFwd *x) { return x != 0; }
int use_UHold(UHold *x) { return x != 0; }
int use_UHoldPlain(UHoldPlain *x) { return x != 0; }
int use_UReal(UReal *x) { return x != 0; }
int use_UPtr(UPtr *x) { return x != 0; }
int use_UPtrMem(UPtrMem *x) { return x != 0; }
int use_UArr(UArr *x) { return x != 0; }
int use_UMod(UMod *x) { return x != 0; }
int use_UAmb(UAmb *x) { return x != 0; }
int use_UDup(UDup *x) { return x != 0; }
int use_TUn(TUn *x) { return x != 0; }
int use_UEmpty(UEmpty *x) { return x != 0; }
int use_UNest(UNest *x) { return x != 0; }
int use_UNoCl(UNoCl *x) { return x != 0; }
int use_E(foo::E *x) { return x != 0; }
void UVf::f() {}
void UVfDiff::f() {}
struct { int a; } g_anon;
int use_local() { struct L { int a; }; L l; l.a = 1; return l.a; }
