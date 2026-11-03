// V1-link MSVC x86 oracle: what Visual C++ 2008 really passes / returns for the shapes abi.py / abi2.py model.
// Hand-written (no decomp content). Compile: msvc_cl.sh /c /nologo /O2 /Gy x86conv.cpp /Fox86conv.obj
// then objdump -d -r -M intel x86conv.obj.
struct P8 { int a, b; };                      // C++03 POD, 8 bytes
struct P4f { float x; };                      // POD, 4 bytes, float member
struct C8 { int a, b; C8(); C8(int, int); };  // user-provided ctor; trivially copyable and destructible (Itanium: trivial)
struct D8 { int a, b; ~D8(); };               // non-trivial destructor
struct Q8 { int a; private: int b; };         // private member: not an aggregate (C++03 non-POD), trivially copyable
struct B8 : P4f { float y; };                 // base class: not C++03 POD, trivially copyable
struct P12 { int a, b, c; };
struct P16d { double a, b; };

extern P8 free_p8();
extern C8 free_c8();
extern D8 free_d8();
extern Q8 free_q8();
extern B8 free_b8();
extern P4f free_p4f();
struct T {
    P8 m_p8();
    C8 m_c8();
    P4f m_p4f();
    P8 __stdcall s_p8(int);
    P8 __cdecl c_p8(int);
    static P8 st_p8(int);
    int m_i(int);
};
extern P12 __fastcall fc_p12(int a, int b);
extern P8 __fastcall fc_p8(int a, int b);
extern int __fastcall fc_i64(__int64 a, int b, int c);
extern int __fastcall fc_dbl(double a, int b, int c);
extern void take_bool(bool);
extern void take_char(char);
extern void take_uchar(unsigned char);
extern void take_short(short);
extern float gf();
extern double gd();
extern int gi();

int sink;
void call_free_p8() { P8 r = free_p8(); sink = r.a + r.b; }
void call_free_c8() { C8 r = free_c8(); sink = r.a + r.b; }
void call_free_d8() { D8 r = free_d8(); sink = r.a + r.b; }
void call_free_q8() { Q8 r = free_q8(); sink = r.a; }
void call_free_b8() { B8 r = free_b8(); sink = (int)(r.x + r.y); }
void call_free_p4f() { P4f r = free_p4f(); sink = (int)r.x; }
void call_m_p8(T *t) { P8 r = t->m_p8(); sink = r.a + r.b; }
void call_m_c8(T *t) { C8 r = t->m_c8(); sink = r.a + r.b; }
void call_m_p4f(T *t) { P4f r = t->m_p4f(); sink = (int)r.x; }
void call_s_p8(T *t) { P8 r = t->s_p8(0x1111); sink = r.a + r.b; }
void call_c_p8(T *t) { P8 r = t->c_p8(0x2222); sink = r.a + r.b; }
void call_st_p8() { P8 r = T::st_p8(0x3333); sink = r.a + r.b; }
void call_fc_p12() { P12 r = fc_p12(0x44, 0x55); sink = r.a + r.c; }
void call_fc_p8() { P8 r = fc_p8(0x44, 0x55); sink = r.a + r.b; }
void call_fc_i64() { sink = fc_i64(0x123456789LL, 0x66, 0x77); }
void call_fc_dbl() { sink = fc_dbl(1.5, 0x66, 0x77); }
void call_narrow(int x) { take_bool(x != 0); take_char((char)x); take_char((char)(x + 1)); take_uchar((unsigned char)(x * 3)); take_short((short)(x << 3)); }
double call_gf_as_double() { return gf(); }
float call_gd_as_float() { return (float)gd(); }
int call_gi() { return gi() + 1; }

// round 2: fastcall hidden-result order, odd sizes, struct{double}
struct P1 { char a; };
struct P6 { short a, b, c; };
struct PD { double d; };
struct E0 { };
extern P12 __fastcall fc3_p12(int a, int b, int c, int d);
extern P12 __fastcall fcf_p12(float f, int a);
extern P1 free_p1();
extern P6 free_p6();
extern PD free_pd();
extern E0 free_e0();
extern void take_e0(E0, int);
struct C4 { int a; C4() {} };
extern C4 free_c4();
extern int take_c4(C4, int);
void call_fc3_p12() { P12 r = fc3_p12(0x41, 0x42, 0x43, 0x44); sink = r.a + r.c; }
void call_fcf_p12() { P12 r = fcf_p12(2.5f, 0x45); sink = r.a + r.c; }
void call_free_p1() { sink = free_p1().a; }
void call_free_p6() { sink = free_p6().c; }
void call_free_pd() { sink = (int)free_pd().d; }
void call_free_e0() { free_e0(); sink = 1; }
void call_take_e0() { E0 e; take_e0(e, 0x46); }
void call_free_c4() { sink = free_c4().a; }
void call_take_c4() { C4 c; c.a = 0x47; sink = take_c4(c, 0x48); }
