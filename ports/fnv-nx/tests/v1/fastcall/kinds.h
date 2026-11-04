/* kinds.h - the class kinds of V1's fastcall hidden-result check (tests/v1/test_fastcall_nonpod.py): one made-up
 * struct per C++03 POD rule, 4, 8 and 12 bytes. Compiled by MSVC 2008 (which register gets the hidden result
 * buffer of `T __fastcall f(int)`) and by GCC (tools/link/layout.py's `msvc pod` predicate). */
struct A0 { int a; };
struct Empty {};
struct Dt { int a, b; ~Dt(); };
struct Ct { Ct(); int a, b; };
struct Ct4 { Ct4(); int a; };
struct Cc { Cc(const Cc &); int a, b; };
struct As { As &operator=(const As &); int a, b; };
struct Pv { int get() const { return a; } private: int a, b; };
struct Pd { int a, b; };
#define KINDS(X) \
  X(pod8, { int a, b; }) \
  X(pod12, { int a, b, c; }) \
  X(pod4, { int a; }) \
  X(dtor8, { int a, b; ~dtor8(); }) \
  X(dtor4, { int a; ~dtor4(); }) \
  X(ctor8, { ctor8(); int a, b; }) \
  X(ctor12, { ctor12(); int a, b, c; }) \
  X(ctor4, { ctor4(); int a; }) \
  X(cctor8, { cctor8(const cctor8 &); int a, b; }) \
  X(assign8, { assign8 &operator=(const assign8 &); int a, b; }) \
  X(priv8, { int get() const { return a; } private: int a, b; }) \
  X(prot8, { protected: int a, b; }) \
  X(base8, : A0 { int b; }) \
  X(ebase8, : Empty { int a, b; }) \
  X(virt8, { virtual void v(); int a; }) \
  X(mdtor8, { Dt m; }) \
  X(mctor8, { Ct m; }) \
  X(mcctor8, { Cc m; }) \
  X(massign8, { As m; }) \
  X(mpriv8, { Pv m; }) \
  X(mpod8, { Pd m; }) \
  X(arrct8, { Ct4 m[2]; }) \
  X(ref8, { int &r; int b; }) \
  X(static8, { static int s; void f(); int a, b; })
