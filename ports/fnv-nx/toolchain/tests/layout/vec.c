/* VEC1: vector types in the declared types of external symbols: the NEON builtin vector types (the compiler's own, no header: the
 * header declares ~4000 extern inline functions) and vector_size vectors */
__Int32x4_t n_s32;
__Uint8x16_t n_u8;
__Float32x2_t n_f32;
typedef int vs4 __attribute__((vector_size(16)));
typedef unsigned int vu4 __attribute__((vector_size(16)));
typedef int vs2 __attribute__((vector_size(8)));
typedef double vd2 __attribute__((vector_size(16)));
vs4 g_s4;
vu4 g_u4;
vs2 g_s2;
vd2 g_d2;
const vs4 g_c4;
vs4 *g_p4;
vs4 g_a[2];
struct HoldsV { char c; vs4 v; } g_h;
__Int32x4_t n_fn(__Uint8x16_t a, vs4 b);
__Int32x4_t n_fn(__Uint8x16_t a, vs4 b) { (void)a; (void)b; return n_s32; }
/* variable-length SVE vectors (builtin types of the target, size N = c0 + c1*x) and an opaque vector (the result type of a vector comparison) */
__SVFloat32_t sv_f32(__SVFloat32_t, __SVBool_t);   /* a variable-length type cannot be an object: functions declare them (glibc's math-vector.h does) */
__SVInt8_t sv_i8(void);
__SVBool_t sv_b(__SVInt8_t *);
__typeof__(g_s4 == g_s4) g_cmp;
#if __STDC_VERSION__ >= 202000L
__typeof__(nullptr) np;
#endif
