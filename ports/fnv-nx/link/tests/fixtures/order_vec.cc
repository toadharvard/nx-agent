// vector, complex and long double accesses through shared pointers: copied unit-wise
typedef float f4 __attribute__((vector_size(16)));
void vcopy(f4 *d, const f4 *s) { *d = *s; }
f4 vadd(const f4 *a, const f4 *b) { return *a + *b; }
void vstore(f4 *d, f4 v) { *d = v; }
float cplx(const _Complex float *p) { return __real__ *p + __imag__ *p; }
long double ld(const long double *p) { return *p + 1; }
void lds(long double *p, long double v) { *p = v; }
struct Pk { char c; int i; } __attribute__((packed));
int packed(const Pk *p) { return p->i; }
void pstore(Pk *p, int v) { p->i = v; }
