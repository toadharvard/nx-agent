// Thunks the compiler builds itself: every kind of GCC 13.3 (symtab-thunks.cc) must get an N3 row of its own, never an error.
struct A1 { virtual void f1(); int a; };
void A1::f1() {}
struct CB { virtual CB *cl(); virtual void g(); int b; };
CB *CB::cl() { return this; }
void CB::g() {}
struct CD : A1, CB { CD *cl() override; void g() override; };          // _ZTc (covariant, a GIMPLE body), _ZTh (this-adjusting)
CD *CD::cl() { return this; }
void CD::g() {}
struct VB { virtual void h(); int v; };
void VB::h() {}
struct VD : virtual VB { void h() override; };                           // _ZTv (a virtual-offset this adjustment)
void VD::h() {}
struct R0 { virtual R0 *c(); };
R0 *R0::c() { return this; }
struct R1 : virtual R0 {};
struct R2 { virtual R0 *c(); };
struct R3 : R2 { R1 *c() override; };                                    // a covariant return through a virtual base
R0 *R2::c() { return 0; }
R1 *R3::c() { return 0; }
