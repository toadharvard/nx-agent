// test_layout.cpp - synthetic records for fnvnx_layout / tools/equiv/r7.py: one translation unit both compilers build.
// Every record is used in use() so that cl's debug info (/Z7) carries it.
#ifdef _MSC_VER
#pragma warning(disable : 4201 4200)
#else
typedef long long __int64;
#endif

struct Plain { int a; char b; short c; };                       // plain struct

struct Base1 { int x; };
struct Base2 { char y; };
struct Multi : Base1, Base2 { int z; };                         // multiple inheritance

struct Virt { virtual void f(); int a; };                       // vfptr
struct VirtDer : Virt { double d; };                            // vfptr, then a double (alignment 8)
struct Virt2 { virtual void g(); short s; };
struct VirtMulti : Virt, Virt2 { char c; };                     // two vfptrs

struct VBase { int v; };
struct VDer : virtual VBase { int a; };                         // virtual base (cl: vbptr, gcc: vptr)
struct VDer2 : VDer { char c; };                                // indirect virtual base

struct Empty { };
struct EmptyDer : Empty { int a; };                             // empty base
struct EmptyMember { Empty e; int a; };
struct Empty2 { };
struct TwoEmpty : Empty, Empty2 { int a; };                     // two empty bases
struct EmptyAfter : Plain, Empty { };                           // empty base after a non-empty one

struct Bits { unsigned a : 3; unsigned b : 5; int c : 20; char d : 2; short e : 9; };
struct Bits2 { unsigned a : 3; unsigned : 0; unsigned b : 4; };
struct Wide { char c; double d; __int64 i; };                   // alignment 8

#pragma pack(push, 1)
struct Packed1 { char c; int i; double d; };                    // #pragma pack
#pragma pack(pop)
#pragma pack(push, 2)
struct Packed2 { char c; int i; short s; };
#pragma pack(pop)

template <class T> struct Tmpl { T v; char c; };                // a class template instantiation
struct Outer { struct Inner { char a; int b; } in; char c; Tmpl<int> t; };   // a nested class
union Un { char c; double d; int i[3]; };                       // a union
struct Anon { int a; struct { int x; char y; }; union { int u; float f; }; int tail; };   // anonymous members
struct Arr { char c[3]; short s[2]; Plain p[2]; long double ld; };
struct PtrMem { int Plain::*pm; void (Virt::*pf)(); char c; };

namespace ns { struct In { char c; }; }

void use()
{
  Plain p; Multi m; VirtDer vd; VirtMulti vm; VDer v; VDer2 v2; EmptyDer ed; EmptyMember em; TwoEmpty te; EmptyAfter ea;
  Bits b; Bits2 b2; Wide w; Packed1 k1; Packed2 k2; Tmpl<double> td; Tmpl<char> tc; Outer o; Un u; Anon an; Arr ar; PtrMem pm;
  ns::In ni;
  (void)p; (void)m; (void)vd; (void)vm; (void)v; (void)v2; (void)ed; (void)em; (void)te; (void)ea; (void)b; (void)b2;
  (void)w; (void)k1; (void)k2; (void)td; (void)tc; (void)o; (void)u; (void)an; (void)ar; (void)pm; (void)ni;
}
