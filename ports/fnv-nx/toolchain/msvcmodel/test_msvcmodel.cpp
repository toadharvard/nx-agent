// test_msvcmodel.cpp - what toolchain/msvcmodel/test_msvcmodel.py compiles with the game's compiler (MSVCMODEL1).
// The numbers in the comments are the pinned cl's (MSVC 2008 SP1 /O2) for the same lines.
#ifdef _MSC_VER                 // the same file compiles with cl, to reread its numbers
typedef int ptrdiff_t;
#define SIZEOF_T size_t
#define PTRDIFF_T ptrdiff_t
#else
typedef __SIZE_TYPE__ size_t;
typedef __PTRDIFF_TYPE__ ptrdiff_t;
#define SIZEOF_T decltype(sizeof 0)
#define PTRDIFF_T decltype((char *)0 - (char *)0)
#endif
void types(size_t, ptrdiff_t, SIZEOF_T, PTRDIFF_T) {}                                        // cl: ?types@@YAXIHIH@Z
extern "C" void *heap_alloc(size_t);
extern "C" void heap_free(void *, size_t);
struct P { int a; };
struct D { ~D(); int a; };
struct A8 { ~A8(); double d; };                                                               // 8 bytes, aligned to 8
struct NM { int a[5]; static void *operator new[](size_t n) { return heap_alloc(n); }
            static void operator delete[](void *p, size_t n) { heap_free(p, n); } };        // sized delete[], trivial
struct ND : NM { int extra; };
struct NV : NM { ~NV(); int extra[2]; };
struct N { ~N(); int a, b, c; static void *operator new[](size_t n) { return heap_alloc(n); }
           static void operator delete[](void *p, size_t n) { heap_free(p, n); } };
D *n_d() { return new D[3]; }          // cl: operator new(3*4 + 4), array = block + 4
A8 *n_a8() { return new A8[3]; }       // cl: operator new(3*8 + 4), array = block + 4
NM *n_nm() { return new NM[3]; }       // cl: NM::operator new[](60): no count
ND *n_nd() { return new ND[3]; }       // cl: 72: no count
NV *n_nv() { return new NV[3]; }       // cl: 3*28 + 4 = 88
N *n_n() { return new N[3]; }          // cl: 3*12 + 4 = 40
void d_nm(NM *p) { delete[] p; }       // cl: heap_free(p, 20)
void d_nd(ND *p) { delete[] p; }       // cl: heap_free(p, 24)
void d_nv(NV *p) { delete[] p; }       // cl: ~NV last to first, heap_free(p - 4, 28)
void d_n(N *p) { delete[] p; }         // cl: heap_free(p - 4, 12)
int *n_var(size_t n) { return new int[n]; }     // cl: operator new(overflow ? 0xFFFFFFFF : 4*n), no throw
