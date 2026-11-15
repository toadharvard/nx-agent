void use(void *);
namespace N0 {
struct S { char m0; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N1 {
struct S { short m0; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N2 {
struct S { int m0; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N3 {
struct S { int m0; int m1; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N4 {
struct S { int m0; int m1; int m2; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N5 {
struct S { char m0; S() {} };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N6 {
struct S { short m0; S() {} };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N7 {
struct S { int m0; S() {} };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N8 {
struct S { int m0; int m1; S() {} };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N9 {
struct S { int m0; int m1; int m2; S() {} };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N10 {
struct M { char m0; M() {} };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N11 {
struct M { short m0; M() {} };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N12 {
struct M { int m0; M() {} };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N13 {
struct M { int m0; int m1; M() {} };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N14 {
struct M { int m0; int m1; int m2; M() {} };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N15 {
struct M { char m0; M() {} };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N16 {
struct M { short m0; M() {} };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N17 {
struct M { int m0; M() {} };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N18 {
struct M { int m0; int m1; M() {} };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N19 {
struct M { int m0; int m1; int m2; M() {} };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N20 {
struct M { char m0; M() {} };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N21 {
struct M { short m0; M() {} };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N22 {
struct M { int m0; M() {} };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N23 {
struct M { int m0; int m1; M() {} };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N24 {
struct M { int m0; int m1; int m2; M() {} };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N25 {
struct S { char m0; S(const S &o) : m0(o.m0) {} };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N26 {
struct S { short m0; S(const S &o) : m0(o.m0) {} };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N27 {
struct S { int m0; S(const S &o) : m0(o.m0) {} };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N28 {
struct S { int m0; int m1; S(const S &o) : m0(o.m0), m1(o.m1) {} };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N29 {
struct S { int m0; int m1; int m2; S(const S &o) : m0(o.m0), m1(o.m1), m2(o.m2) {} };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N30 {
struct M { char m0; M(const M &o) : m0(o.m0) {} };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N31 {
struct M { short m0; M(const M &o) : m0(o.m0) {} };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N32 {
struct M { int m0; M(const M &o) : m0(o.m0) {} };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N33 {
struct M { int m0; int m1; M(const M &o) : m0(o.m0), m1(o.m1) {} };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N34 {
struct M { int m0; int m1; int m2; M(const M &o) : m0(o.m0), m1(o.m1), m2(o.m2) {} };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N35 {
struct M { char m0; M(const M &o) : m0(o.m0) {} };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N36 {
struct M { short m0; M(const M &o) : m0(o.m0) {} };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N37 {
struct M { int m0; M(const M &o) : m0(o.m0) {} };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N38 {
struct M { int m0; int m1; M(const M &o) : m0(o.m0), m1(o.m1) {} };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N39 {
struct M { int m0; int m1; int m2; M(const M &o) : m0(o.m0), m1(o.m1), m2(o.m2) {} };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N40 {
struct M { char m0; M(const M &o) : m0(o.m0) {} };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N41 {
struct M { short m0; M(const M &o) : m0(o.m0) {} };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N42 {
struct M { int m0; M(const M &o) : m0(o.m0) {} };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N43 {
struct M { int m0; int m1; M(const M &o) : m0(o.m0), m1(o.m1) {} };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N44 {
struct M { int m0; int m1; int m2; M(const M &o) : m0(o.m0), m1(o.m1), m2(o.m2) {} };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N45 {
struct S { char m0; S &operator=(const S &o) { m0 = o.m0; return *this; } };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N46 {
struct S { short m0; S &operator=(const S &o) { m0 = o.m0; return *this; } };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N47 {
struct S { int m0; S &operator=(const S &o) { m0 = o.m0; return *this; } };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N48 {
struct S { int m0; int m1; S &operator=(const S &o) { m0 = o.m0; m1 = o.m1; return *this; } };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N49 {
struct S { int m0; int m1; int m2; S &operator=(const S &o) { m0 = o.m0; m1 = o.m1; m2 = o.m2; return *this; } };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N50 {
struct M { char m0; M &operator=(const M &o) { m0 = o.m0; return *this; } };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N51 {
struct M { short m0; M &operator=(const M &o) { m0 = o.m0; return *this; } };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N52 {
struct M { int m0; M &operator=(const M &o) { m0 = o.m0; return *this; } };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N53 {
struct M { int m0; int m1; M &operator=(const M &o) { m0 = o.m0; m1 = o.m1; return *this; } };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N54 {
struct M { int m0; int m1; int m2; M &operator=(const M &o) { m0 = o.m0; m1 = o.m1; m2 = o.m2; return *this; } };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N55 {
struct M { char m0; M &operator=(const M &o) { m0 = o.m0; return *this; } };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N56 {
struct M { short m0; M &operator=(const M &o) { m0 = o.m0; return *this; } };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N57 {
struct M { int m0; M &operator=(const M &o) { m0 = o.m0; return *this; } };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N58 {
struct M { int m0; int m1; M &operator=(const M &o) { m0 = o.m0; m1 = o.m1; return *this; } };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N59 {
struct M { int m0; int m1; int m2; M &operator=(const M &o) { m0 = o.m0; m1 = o.m1; m2 = o.m2; return *this; } };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N60 {
struct M { char m0; M &operator=(const M &o) { m0 = o.m0; return *this; } };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N61 {
struct M { short m0; M &operator=(const M &o) { m0 = o.m0; return *this; } };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N62 {
struct M { int m0; M &operator=(const M &o) { m0 = o.m0; return *this; } };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N63 {
struct M { int m0; int m1; M &operator=(const M &o) { m0 = o.m0; m1 = o.m1; return *this; } };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N64 {
struct M { int m0; int m1; int m2; M &operator=(const M &o) { m0 = o.m0; m1 = o.m1; m2 = o.m2; return *this; } };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N65 {
struct S { char m0; ~S() {} };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N66 {
struct S { short m0; ~S() {} };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N67 {
struct S { int m0; ~S() {} };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N68 {
struct S { int m0; int m1; ~S() {} };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N69 {
struct S { int m0; int m1; int m2; ~S() {} };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N70 {
struct M { char m0; ~M() {} };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N71 {
struct M { short m0; ~M() {} };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N72 {
struct M { int m0; ~M() {} };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N73 {
struct M { int m0; int m1; ~M() {} };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N74 {
struct M { int m0; int m1; int m2; ~M() {} };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N75 {
struct M { char m0; ~M() {} };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N76 {
struct M { short m0; ~M() {} };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N77 {
struct M { int m0; ~M() {} };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N78 {
struct M { int m0; int m1; ~M() {} };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N79 {
struct M { int m0; int m1; int m2; ~M() {} };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N80 {
struct M { char m0; ~M() {} };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N81 {
struct M { short m0; ~M() {} };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N82 {
struct M { int m0; ~M() {} };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N83 {
struct M { int m0; int m1; ~M() {} };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N84 {
struct M { int m0; int m1; int m2; ~M() {} };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N85 {
struct S { char m0; virtual void v(); };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N86 {
struct S { short m0; virtual void v(); };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N87 {
struct S { int m0; virtual void v(); };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N88 {
struct S { int m0; int m1; virtual void v(); };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N89 {
struct S { int m0; int m1; int m2; virtual void v(); };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N90 {
struct M { char m0; virtual void v(); };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N91 {
struct M { short m0; virtual void v(); };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N92 {
struct M { int m0; virtual void v(); };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N93 {
struct M { int m0; int m1; virtual void v(); };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N94 {
struct M { int m0; int m1; int m2; virtual void v(); };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N95 {
struct M { char m0; virtual void v(); };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N96 {
struct M { short m0; virtual void v(); };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N97 {
struct M { int m0; virtual void v(); };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N98 {
struct M { int m0; int m1; virtual void v(); };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N99 {
struct M { int m0; int m1; int m2; virtual void v(); };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N100 {
struct M { char m0; virtual void v(); };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N101 {
struct M { short m0; virtual void v(); };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N102 {
struct M { int m0; virtual void v(); };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N103 {
struct M { int m0; int m1; virtual void v(); };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N104 {
struct M { int m0; int m1; int m2; virtual void v(); };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N105 {
struct SBase {};
struct S : SBase { char m0; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N106 {
struct SBase {};
struct S : SBase { short m0; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N107 {
struct SBase {};
struct S : SBase { int m0; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N108 {
struct SBase {};
struct S : SBase { int m0; int m1; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N109 {
struct SBase {};
struct S : SBase { int m0; int m1; int m2; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N110 {
struct MBase {};
struct M : MBase { char m0; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N111 {
struct MBase {};
struct M : MBase { short m0; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N112 {
struct MBase {};
struct M : MBase { int m0; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N113 {
struct MBase {};
struct M : MBase { int m0; int m1; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N114 {
struct MBase {};
struct M : MBase { int m0; int m1; int m2; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N115 {
struct MBase {};
struct M : MBase { char m0; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N116 {
struct MBase {};
struct M : MBase { short m0; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N117 {
struct MBase {};
struct M : MBase { int m0; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N118 {
struct MBase {};
struct M : MBase { int m0; int m1; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N119 {
struct MBase {};
struct M : MBase { int m0; int m1; int m2; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N120 {
struct MBase {};
struct M : MBase { char m0; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N121 {
struct MBase {};
struct M : MBase { short m0; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N122 {
struct MBase {};
struct M : MBase { int m0; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N123 {
struct MBase {};
struct M : MBase { int m0; int m1; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N124 {
struct MBase {};
struct M : MBase { int m0; int m1; int m2; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N125 {
struct SBase { int x; };
struct S : SBase { char m0; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N126 {
struct SBase { int x; };
struct S : SBase { short m0; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N127 {
struct SBase { int x; };
struct S : SBase { int m0; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N128 {
struct SBase { int x; };
struct S : SBase { int m0; int m1; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N129 {
struct SBase { int x; };
struct S : SBase { int m0; int m1; int m2; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N130 {
struct MBase { int x; };
struct M : MBase { char m0; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N131 {
struct MBase { int x; };
struct M : MBase { short m0; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N132 {
struct MBase { int x; };
struct M : MBase { int m0; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N133 {
struct MBase { int x; };
struct M : MBase { int m0; int m1; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N134 {
struct MBase { int x; };
struct M : MBase { int m0; int m1; int m2; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N135 {
struct MBase { int x; };
struct M : MBase { char m0; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N136 {
struct MBase { int x; };
struct M : MBase { short m0; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N137 {
struct MBase { int x; };
struct M : MBase { int m0; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N138 {
struct MBase { int x; };
struct M : MBase { int m0; int m1; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N139 {
struct MBase { int x; };
struct M : MBase { int m0; int m1; int m2; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N140 {
struct MBase { int x; };
struct M : MBase { char m0; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N141 {
struct MBase { int x; };
struct M : MBase { short m0; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N142 {
struct MBase { int x; };
struct M : MBase { int m0; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N143 {
struct MBase { int x; };
struct M : MBase { int m0; int m1; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N144 {
struct MBase { int x; };
struct M : MBase { int m0; int m1; int m2; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N145 {
struct S { private: char m0; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N146 {
struct S { private: short m0; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N147 {
struct S { private: int m0; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N148 {
struct S { private: int m0; int m1; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N149 {
struct S { private: int m0; int m1; int m2; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N150 {
struct M { private: char m0; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N151 {
struct M { private: short m0; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N152 {
struct M { private: int m0; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N153 {
struct M { private: int m0; int m1; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N154 {
struct M { private: int m0; int m1; int m2; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N155 {
struct M { private: char m0; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N156 {
struct M { private: short m0; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N157 {
struct M { private: int m0; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N158 {
struct M { private: int m0; int m1; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N159 {
struct M { private: int m0; int m1; int m2; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N160 {
struct M { private: char m0; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N161 {
struct M { private: short m0; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N162 {
struct M { private: int m0; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N163 {
struct M { private: int m0; int m1; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N164 {
struct M { private: int m0; int m1; int m2; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N165 {
struct S { protected: char m0; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N166 {
struct S { protected: short m0; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N167 {
struct S { protected: int m0; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N168 {
struct S { protected: int m0; int m1; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N169 {
struct S { protected: int m0; int m1; int m2; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N170 {
struct M { protected: char m0; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N171 {
struct M { protected: short m0; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N172 {
struct M { protected: int m0; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N173 {
struct M { protected: int m0; int m1; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N174 {
struct M { protected: int m0; int m1; int m2; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N175 {
struct M { protected: char m0; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N176 {
struct M { protected: short m0; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N177 {
struct M { protected: int m0; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N178 {
struct M { protected: int m0; int m1; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N179 {
struct M { protected: int m0; int m1; int m2; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N180 {
struct M { protected: char m0; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N181 {
struct M { protected: short m0; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N182 {
struct M { protected: int m0; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N183 {
struct M { protected: int m0; int m1; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N184 {
struct M { protected: int m0; int m1; int m2; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N185 {
struct S { char m0; int &r; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N186 {
struct S { short m0; int &r; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N187 {
struct S { int m0; int &r; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N188 {
struct S { int m0; int m1; int &r; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N189 {
struct S { int m0; int m1; int m2; int &r; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N190 {
struct M { char m0; int &r; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N191 {
struct M { short m0; int &r; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N192 {
struct M { int m0; int &r; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N193 {
struct M { int m0; int m1; int &r; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N194 {
struct M { int m0; int m1; int m2; int &r; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N195 {
struct M { char m0; int &r; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N196 {
struct M { short m0; int &r; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N197 {
struct M { int m0; int &r; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N198 {
struct M { int m0; int m1; int &r; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N199 {
struct M { int m0; int m1; int m2; int &r; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N200 {
struct M { char m0; int &r; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N201 {
struct M { short m0; int &r; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N202 {
struct M { int m0; int &r; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N203 {
struct M { int m0; int m1; int &r; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N204 {
struct M { int m0; int m1; int m2; int &r; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N205 {
struct S { char m0; static int s; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N206 {
struct S { short m0; static int s; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N207 {
struct S { int m0; static int s; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N208 {
struct S { int m0; int m1; static int s; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N209 {
struct S { int m0; int m1; int m2; static int s; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N210 {
struct M { char m0; static int s; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N211 {
struct M { short m0; static int s; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N212 {
struct M { int m0; static int s; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N213 {
struct M { int m0; int m1; static int s; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N214 {
struct M { int m0; int m1; int m2; static int s; };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N215 {
struct M { char m0; static int s; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N216 {
struct M { short m0; static int s; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N217 {
struct M { int m0; static int s; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N218 {
struct M { int m0; int m1; static int s; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N219 {
struct M { int m0; int m1; int m2; static int s; };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N220 {
struct M { char m0; static int s; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N221 {
struct M { short m0; static int s; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N222 {
struct M { int m0; static int s; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N223 {
struct M { int m0; int m1; static int s; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N224 {
struct M { int m0; int m1; int m2; static int s; };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N225 {
struct S { char m0; void mf(); };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N226 {
struct S { short m0; void mf(); };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N227 {
struct S { int m0; void mf(); };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N228 {
struct S { int m0; int m1; void mf(); };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N229 {
struct S { int m0; int m1; int m2; void mf(); };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N230 {
struct M { char m0; void mf(); };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N231 {
struct M { short m0; void mf(); };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N232 {
struct M { int m0; void mf(); };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N233 {
struct M { int m0; int m1; void mf(); };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N234 {
struct M { int m0; int m1; int m2; void mf(); };
struct S { M m; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N235 {
struct M { char m0; void mf(); };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N236 {
struct M { short m0; void mf(); };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N237 {
struct M { int m0; void mf(); };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N238 {
struct M { int m0; int m1; void mf(); };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N239 {
struct M { int m0; int m1; int m2; void mf(); };
struct S { M m[1]; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N240 {
struct M { char m0; void mf(); };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N241 {
struct M { short m0; void mf(); };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N242 {
struct M { int m0; void mf(); };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N243 {
struct M { int m0; int m1; void mf(); };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
namespace N244 {
struct M { int m0; int m1; int m2; void mf(); };
struct N { M m; };
struct S { N n; };
S f();
char sz[sizeof(S)];
void c() { S x = f(); use(&x); }
}
