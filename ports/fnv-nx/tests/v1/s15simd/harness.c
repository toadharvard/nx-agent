/* harness.c - V1 (D14) against S15-simd's NEON overrides (tools/link/overrides): each override against the decomp's
 * own bodies (dec_*.cpp + the .inc files test_s15simd_decomp.py extracts at run time), both compiled for AArch64
 * ILP32 (the game's layouts) and run under unicorn, freestanding (no libc). Our code only.
 *
 * run(n, seed): n cases per kernel; g_res[k] counts cases, mismatches beyond a NaN payload ("bad") and cases whose
 * only differences are NaN payloads or signs ("nanonly"), and keeps the first mismatching cases.
 * gen(k, mode, in) / dec(k, in, out) / ovr(k, in, out): one case, for the dynamic counts. */
#include <arm_neon.h>
#include <stdint.h>

void *memcpy(void *, const void *, unsigned long);
void *memset(void *, int, unsigned long);

/* ---- the overrides' interfaces (their layouts, ILP32) */
struct fnvnx_xform { float m[13]; };
struct fnvnx_avobject {
    uint8_t pad0[0x18];
    const struct fnvnx_avobject *parent;
    uint8_t pad1[0x30 - 0x1c];
    uint32_t flags;
    struct fnvnx_xform local, world;
};
void fnvnx_update_world_loc_00a68bf0(struct fnvnx_avobject *self);
struct fnvnx_sphere { float c[3], r; };
struct fnvnx_cull_object;
typedef void (*fnvnx_cull_cb)(struct fnvnx_cull_object *, void *);
struct fnvnx_cull_object { fnvnx_cull_cb *vtable; uint32_t pad[7]; struct fnvnx_sphere *bound; };
struct fnvnx_cull_worker { uint32_t pad[11]; float planes[6][4]; uint32_t mask; };
void fnvnx_cull_worker_00a694e0(struct fnvnx_cull_worker *self, struct fnvnx_cull_object *obj);
struct fnvnx_plane { float n[3], k; };
int fnvnx_sphere_plane_004b6100(const struct fnvnx_sphere *s, const struct fnvnx_plane *p);
struct fnvnx_bound { float c[4]; };
struct fnvnx_transform { float m[13]; };
void fnvnx_nibound_update_00a7fdd0(struct fnvnx_bound *self, const struct fnvnx_bound *b, const struct fnvnx_transform *t);
struct fnvnx_mat3 { float m[9]; };
struct fnvnx_mat3 fnvnx_mat3_tmul_00a588a0(const struct fnvnx_mat3 *a, const struct fnvnx_mat3 *b);
struct fnvnx_quat { float c[4]; };
void fnvnx_quat_from_matrix_00a6df40(struct fnvnx_quat *self, const struct fnvnx_mat3 *m);
const int32_t __fnv_011a9e98[3] = {1, 2, 0};

/* ---- the decomp side (dec_*.cpp) */
void dec_xform(const uint32_t *in, uint32_t *out);
void dec_cull(const uint32_t *in, uint32_t *out);
void dec_plane(const uint32_t *in, uint32_t *out);
void dec_bound(const uint32_t *in, uint32_t *out);
void dec_mat3(const uint32_t *in, uint32_t *out);
void dec_quat(const uint32_t *in, uint32_t *out);

/* ---- support for the freestanding image */
uint32_t g_cb_called, g_cb_mask, g_cb_worker_ok;
double sqrt(double x) { double r; __asm__("fsqrt %d0, %d1" : "=w"(r) : "w"(x)); return r; }
float sqrtf(float x) { float r; __asm__("fsqrt %s0, %s1" : "=w"(r) : "w"(x)); return r; }

/* ---- inputs */
static uint64_t rng;
static uint32_t nx(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return (uint32_t)(rng >> 16); }
static float fb(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }
static uint32_t ub(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }
static float uni(float lo, float hi) { return lo + (hi - lo) * (float)(nx() & 0xffffff) / 16777216.0f; }
static const uint32_t EDGE[] = {0x00000000, 0x80000000, 0x00000001, 0x80000001, 0x007fffff, 0x00800000, 0x3f800000,
                                0xbf800000, 0x7f800000, 0xff800000, 0x7fc00000, 0xffc00000, 0x7fc00001, 0x7f800001,
                                0xff800123, 0x7fbfffff, 0xffbfff00, 0x7f7fffff, 0x3f000000, 0x40000000, 0xc0000000};
static uint32_t nan_bits(void)
{
    uint32_t m = nx() & 0x7fffff;
    if (!m) m = 1;
    return (nx() & 0x80000000u) | 0x7f800000u | m;   /* quiet or signalling, either sign */
}
/* modes: 0-2 range, 3 1/4 edge, 4 raw bits, 5 all NaN (distinct payloads), 6 denormal, 7 1/2 NaN */
#define NMODES 8
static float gf(uint32_t mode, float lo, float hi)
{
    switch (mode) {
    case 3: if (nx() % 4 == 0) return fb(EDGE[nx() % (sizeof EDGE / 4)]); break;
    case 4: return fb(nx() << 16 ^ nx());
    case 5: return fb(nan_bits());
    case 6: return fb((nx() << 16 ^ nx()) & 0x807fffffu);
    case 7: if (nx() & 1) return fb(nan_bits()); break;
    }
    return uni(lo, hi);
}
static float dist(const float *p, const float *c)
{   /* the decomp's order: ((n.x c.x + n.y c.y) + n.z c.z) - k */
    float d = p[0] * c[0] + p[1] * c[1];
    d = d + p[2] * c[2];
    return d - p[3];
}

enum { K_XFORM, K_CULL, K_PLANE, K_BOUND, K_MAT3, K_QUAT, NK };
static const uint8_t NOUT[NK] = {13, 4, 1, 4, 9, 4};
static const uint8_t FLOATOUT[NK] = {1, 0, 0, 1, 1, 1};

static void gen_xform(uint32_t mode, uint32_t *in)
{
    in[0] = nx() % 5 != 0;
    in[1] = (nx() & ~0x200u) | (nx() % 3 == 0 ? 0x200u : 0);
    for (int o = 2; o <= 15; o += 13) {
        float *x = (float *)(in + o);
        for (int k = 0; k < 9; k++) x[k] = gf(mode, -1, 1);
        for (int k = 9; k < 12; k++) x[k] = gf(mode, -100, 100);
        x[12] = gf(mode, 0, 2);
    }
    for (int k = 28; k < 41; k++) in[k] = nx() << 16 ^ nx();
}
static void gen_cull(uint32_t mode, uint32_t *in)
{
    uint32_t sel = nx() % 8;
    in[0] = sel == 0 ? (nx() << 16 ^ nx()) : sel == 1 ? 0x3f : sel == 2 ? 0 : (nx() & 0x3f) | (nx() & 0xffffffc0u & -(nx() & 1));
    in[1] = nx() % 32 != 0;
    float *s = (float *)(in + 2), *p = (float *)(in + 6);
    for (int k = 0; k < 3; k++) s[k] = gf(mode, -10, 10);
    s[3] = gf(mode, 0, 5);
    for (int i = 0; i < 6; i++) {
        for (int k = 0; k < 3; k++) p[4 * i + k] = gf(mode, -1, 1);
        p[4 * i + 3] = gf(mode, -10, 10);
    }
    switch (nx() % 4) {
    case 0: case 1: {   /* a plane exactly on the boundary: d == -r (behind, <=) or d == r (front, >=) */
        int j = nx() % 6;
        float d = dist(p + 4 * j, s);
        s[3] = fb(ub(d) & 0x7fffffffu);
        if (nx() % 2) {   /* and the next active planes straddle the same sphere */
            int j2 = (j + 1 + nx() % 5) % 6;
            p[4 * j2 + 3] = dist(p + 4 * j2, s) + p[4 * j2 + 3];
        }
        break;
    }
    case 2: s[3] = fb(EDGE[nx() % (sizeof EDGE / 4)]); break;   /* +-0, NaN, inf, negative, denormal radius */
    }
}
static void gen_plane(uint32_t mode, uint32_t *in)
{
    float *s = (float *)in, *p = (float *)(in + 4);
    for (int k = 0; k < 3; k++) s[k] = gf(mode, -10, 10);
    s[3] = gf(mode, 0, 5);
    for (int k = 0; k < 3; k++) p[k] = gf(mode, -1, 1);
    p[3] = gf(mode, -10, 10);
    uint32_t sel = nx() % 4;
    if (sel < 2)
        s[3] = fb(ub(dist(p, s)) & 0x7fffffffu);   /* on the boundary */
    else if (sel == 2)
        s[3] = fb(EDGE[nx() % (sizeof EDGE / 4)]);
}
static void gen_bound(uint32_t mode, uint32_t *in)
{
    float *b = (float *)in, *t = (float *)(in + 4);
    for (int k = 0; k < 3; k++) b[k] = gf(mode, -10, 10);
    b[3] = gf(mode, 0, 5);
    for (int k = 0; k < 9; k++) t[k] = gf(mode, -1, 1);
    for (int k = 9; k < 12; k++) t[k] = gf(mode, -100, 100);
    t[12] = gf(mode, 0, 2);
    in[17] = nx() % 4 == 0;
}
static void gen_mat3(uint32_t mode, uint32_t *in)
{
    for (int k = 0; k < 18; k++) ((float *)in)[k] = gf(mode, -2, 2);
}
static void gen_quat(uint32_t mode, uint32_t *in)
{
    float *m = (float *)in;
    uint32_t sel = nx() % 6;
    if (sel <= 2) {   /* a rotation; sel 1: about 180 degrees (trace near -1), sel 2: near identity */
        float q[4], s = 0;
        for (int k = 0; k < 4; k++) q[k] = uni(-1, 1);
        if (sel == 1) q[3] = uni(-1e-3f, 1e-3f);
        if (sel == 2) q[3] = 8.0f;
        for (int k = 0; k < 4; k++) s += q[k] * q[k];
        s = 1.0f / sqrtf(s);
        float x = q[0] * s, y = q[1] * s, z = q[2] * s, w = q[3] * s;
        m[0] = 1 - 2 * (y * y + z * z); m[1] = 2 * (x * y - z * w); m[2] = 2 * (x * z + y * w);
        m[3] = 2 * (x * y + z * w); m[4] = 1 - 2 * (x * x + z * z); m[5] = 2 * (y * z - x * w);
        m[6] = 2 * (x * z - y * w); m[7] = 2 * (y * z + x * w); m[8] = 1 - 2 * (x * x + y * y);
        if (mode >= 3)
            m[nx() % 9] = gf(mode, -1, 1);
    } else {
        for (int k = 0; k < 9; k++) m[k] = gf(mode, -2, 2);
    }
    if (sel == 4) {   /* diagonal ties */
        uint32_t t = nx() % 4;
        if (t == 0 || t == 1) m[4] = m[0];
        if (t == 0 || t == 2) m[8] = m[(t == 2 ? 4 : 0)];
        if (t == 3) m[8] = m[0];
    }
    if (sel == 5)   /* trace exactly 0 */
        m[8] = -(m[0] + m[4]);
    for (int k = 9; k < 13; k++) in[k] = nx() << 16 ^ nx();
}

void gen(uint32_t k, uint32_t mode, uint32_t *in)
{
    memset(in, 0, 48 * 4);
    switch (k) {
    case K_XFORM: gen_xform(mode, in); break;
    case K_CULL: gen_cull(mode, in); break;
    case K_PLANE: gen_plane(mode, in); break;
    case K_BOUND: gen_bound(mode, in); break;
    case K_MAT3: gen_mat3(mode, in); break;
    case K_QUAT: gen_quat(mode, in); break;
    }
}

/* ---- the override side, from the same inputs */
static struct fnvnx_avobject o_child, o_parent;
static struct fnvnx_cull_worker o_worker;
static struct fnvnx_sphere o_sphere;
static fnvnx_cull_cb o_vt[54];
static void o_callback(struct fnvnx_cull_object *o, void *w)
{
    (void)o;
    g_cb_called++;
    g_cb_mask = ((struct fnvnx_cull_worker *)w)->mask;
    g_cb_worker_ok = w == (void *)&o_worker;
}
void ovr_cull_call(struct fnvnx_cull_worker *w, struct fnvnx_cull_object *o) __attribute__((noinline));
void ovr_cull_call(struct fnvnx_cull_worker *w, struct fnvnx_cull_object *o) { fnvnx_cull_worker_00a694e0(w, o); }

void ovr(uint32_t k, const uint32_t *in, uint32_t *out)
{
    switch (k) {
    case K_XFORM:
        memcpy(&o_child.local, in + 2, 52);
        memcpy(&o_parent.world, in + 15, 52);
        memcpy(&o_child.world, in + 28, 52);
        o_child.flags = in[1];
        o_child.parent = in[0] ? &o_parent : 0;
        fnvnx_update_world_loc_00a68bf0(&o_child);
        memcpy(out, &o_child.world, 52);
        break;
    case K_CULL: {
        struct fnvnx_cull_object obj;
        memset(&obj, 0, sizeof obj);
        o_vt[53] = o_callback;
        obj.vtable = o_vt;
        memcpy(&o_sphere, in + 2, 16);
        obj.bound = in[1] ? &o_sphere : 0;
        memcpy(o_worker.planes, in + 6, 96);
        o_worker.mask = in[0];
        g_cb_called = g_cb_mask = g_cb_worker_ok = 0;
        ovr_cull_call(&o_worker, &obj);
        out[0] = g_cb_called;
        out[1] = g_cb_mask;
        out[2] = o_worker.mask;
        out[3] = g_cb_worker_ok;
        break;
    }
    case K_PLANE: {
        struct fnvnx_sphere s;
        struct fnvnx_plane p;
        memcpy(&s, in, 16);
        memcpy(&p, in + 4, 16);
        out[0] = (uint32_t)fnvnx_sphere_plane_004b6100(&s, &p);
        break;
    }
    case K_BOUND: {
        static struct fnvnx_bound self, b;
        static struct fnvnx_transform t;
        memcpy(&b, in, 16);
        memcpy(&t, in + 4, 52);
        if (in[17]) {
            fnvnx_nibound_update_00a7fdd0(&b, &b, &t);
            memcpy(out, &b, 16);
        } else {
            fnvnx_nibound_update_00a7fdd0(&self, &b, &t);
            memcpy(out, &self, 16);
        }
        break;
    }
    case K_MAT3: {
        static struct fnvnx_mat3 a, b, r;
        memcpy(&a, in, 36);
        memcpy(&b, in + 9, 36);
        r = fnvnx_mat3_tmul_00a588a0(&a, &b);
        memcpy(out, &r, 36);
        break;
    }
    case K_QUAT: {
        static struct fnvnx_mat3 m;
        static struct fnvnx_quat q;
        memcpy(&m, in, 36);
        memcpy(&q, in + 9, 16);
        fnvnx_quat_from_matrix_00a6df40(&q, &m);
        memcpy(out, &q, 16);
        break;
    }
    }
}

void dec(uint32_t k, const uint32_t *in, uint32_t *out)
{
    switch (k) {
    case K_XFORM: dec_xform(in, out); break;
    case K_CULL: dec_cull(in, out); break;
    case K_PLANE: dec_plane(in, out); break;
    case K_BOUND: dec_bound(in, out); break;
    case K_MAT3: dec_mat3(in, out); break;
    case K_QUAT: dec_quat(in, out); break;
    }
}

#define NREC 4
struct rec { uint32_t mode, nan_only, in[48], outd[16], outo[16]; };
struct res { uint32_t cases, bad, nanonly, nrec, nrec_nan, nrec_bad; struct rec r[NREC]; uint32_t per_mode_bad[NMODES]; };
struct res g_res[NK];
uint32_t g_in[48], g_outd[16], g_outo[16];

static int isnan_bits(uint32_t u) { return (u & 0x7f800000u) == 0x7f800000u && (u & 0x7fffff); }

void run(uint32_t n, uint32_t seed)
{
    rng = 0x9e3779b97f4a7c15ull ^ seed;
    for (uint32_t k = 0; k < NK; k++) {
        struct res *R = &g_res[k];
        for (uint32_t i = 0; i < n; i++) {
            uint32_t mode = nx() % NMODES;
            gen(k, mode, g_in);
            memset(g_outd, 0, sizeof g_outd);
            memset(g_outo, 0, sizeof g_outo);
            dec(k, g_in, g_outd);
            ovr(k, g_in, g_outo);
            R->cases++;
            int exact = 1, nanok = 1;
            for (int j = 0; j < NOUT[k]; j++)
                if (g_outd[j] != g_outo[j]) {
                    exact = 0;
                    if (!(FLOATOUT[k] && isnan_bits(g_outd[j]) && isnan_bits(g_outo[j])))
                        nanok = 0;
                }
            if (exact)
                continue;
            if (nanok)
                R->nanonly++;
            else {
                R->bad++;
                R->per_mode_bad[mode]++;
            }
            /* keep the first two of each kind */
            if (R->nrec < NREC && (nanok ? R->nrec_nan++ < 2 : R->nrec_bad++ < 2)) {
                struct rec *r = &R->r[R->nrec++];
                r->mode = mode;
                r->nan_only = nanok;
                memcpy(r->in, g_in, sizeof g_in);
                memcpy(r->outd, g_outd, sizeof g_outd);
                memcpy(r->outo, g_outo, sizeof g_outo);
            }
        }
    }
}
