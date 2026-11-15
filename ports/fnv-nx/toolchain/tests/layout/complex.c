/* CPLX1: _Complex types (C99 `_Complex float/double/long double`, the GNU `_Complex int`) in the declared types of external symbols: objects, fields, arrays,
 * pointers, a function with a complex parameter and result (newlib's libm/complex declares them all), qualified, and _Atomic (a C11 type qualifier) */
_Complex float c_f;
_Complex double c_d;
_Complex long double c_ld;
_Complex int c_i;
const _Complex double c_cd;
_Complex double *c_p;
_Complex float c_a[3];
struct HoldsC { char c; _Complex double z; } c_h;
_Complex double c_fn(_Complex float a, _Complex long double b);
_Complex double c_fn(_Complex float a, _Complex long double b) { (void)a; (void)b; return c_d; }
_Atomic int at_i;
_Atomic(_Complex float) at_cf;
volatile _Atomic long at_vl;
