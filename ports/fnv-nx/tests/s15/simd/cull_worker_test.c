/* cull_worker_test.c - tools/link/overrides/cull_worker_00a694e0.c against a scalar reference of the culling worker
 * 0x00a694e0 in the decomp's order (the plane walk with its getter 0x0043d450 and classification 0x004b6100):
 * whether the callback runs, the mask it sees, the mask after return, on random worker masks (incl. bits above 5),
 * planes, spheres and edge values (tests/s15/simd/run.py). Our own code: no decomp text.
 * Prints "cull_worker: <n> cases, <m> mismatches". */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

struct fnvnx_sphere { float c[3], r; };
struct fnvnx_cull_object;
typedef void (*fnvnx_cull_cb)(struct fnvnx_cull_object *, void *);
struct fnvnx_cull_object {
    fnvnx_cull_cb *vtable;
    uint32_t pad[7];
    struct fnvnx_sphere *bound;
};
struct fnvnx_cull_worker {
    uint32_t pad[11];
    float planes[6][4];
    uint32_t mask;
};
void fnvnx_cull_worker_00a694e0(struct fnvnx_cull_worker *self, struct fnvnx_cull_object *obj);

/* the test is LP64: the struct offsets of the ILP32 game differ, the logic does not */

static int called;
static uint32_t seen_mask;
static void *seen_worker;
static void callback(struct fnvnx_cull_object *o, void *w)
{
    (void)o;
    called++;
    seen_worker = w;
    seen_mask = ((struct fnvnx_cull_worker *)w)->mask;
}
static fnvnx_cull_cb vt[54];

static __attribute__((noinline)) int ref_classify(const struct fnvnx_sphere *s, const float *p)
{
    float d = p[0] * s->c[0] + p[1] * s->c[1] + p[2] * s->c[2];
    d = d - p[3];
    if (d <= -s->r)
        return 2;
    else if (d >= s->r)
        return 1;
    return 0;
}
static __attribute__((noinline)) void ref_worker(struct fnvnx_cull_worker *w, struct fnvnx_cull_object *o)
{
    struct fnvnx_sphere *b = o->bound;
    if (!b)
        return;
    if (b->r == 0.0f)
        return;
    uint32_t saved = w->mask, mask = 1;
    unsigned i = 0;
    for (; i < 6; ++i, mask = (mask << 1) | (mask >> 31)) {
        if (w->mask & mask) {
            int side = ref_classify(o->bound, w->planes[i]);
            if (side == 2)
                break;
            if (side == 1)
                w->mask &= ~mask;
        }
    }
    if (i == 6)
        o->vtable[53](o, w);
    w->mask = saved;
}

static uint64_t rng = 0x9e3779b97f4a7c15ull;
static uint32_t next(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return (uint32_t)(rng >> 16); }
static float frand(float lo, float hi) { return lo + (hi - lo) * (float)(next() & 0xffffff) / 16777216.0f; }
static float fbits(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }
static const uint32_t EDGE[] = {0x00000000, 0x80000000, 0x3f800000, 0xbf800000, 0x7f800000, 0xff800000, 0x7fc00000,
                                0x00000001, 0x7f7fffff};

int main(void)
{
    for (int k = 0; k < 54; k++) vt[k] = 0;
    vt[53] = callback;
    long cases = 0, bad = 0;
    for (int i = 0; i < 400000; i++) {
        struct fnvnx_sphere s;
        struct fnvnx_cull_worker w;
        struct fnvnx_cull_object o;
        memset(&w, 0, sizeof w);
        memset(&o, 0, sizeof o);
        o.vtable = vt;
        int mode = (int)(next() % 4);
        for (int k = 0; k < 3; k++) s.c[k] = frand(-5000.0f, 5000.0f);
        s.r = frand(0.0f, 300.0f);
        for (int p = 0; p < 6; p++) {
            float n[3], l = 0;
            for (int k = 0; k < 3; k++) { n[k] = frand(-1.0f, 1.0f); l += n[k] * n[k]; }
            l = __builtin_sqrtf(l);
            for (int k = 0; k < 3; k++) w.planes[p][k] = l > 0 ? n[k] / l : n[k];
            float d = w.planes[p][0] * s.c[0] + w.planes[p][1] * s.c[1] + w.planes[p][2] * s.c[2];
            /* mostly in front, some behind, some straddling: a frustum's mix */
            w.planes[p][3] = d - frand(-1.2f, 3.0f) * s.r;
        }
        if (mode == 1) {        /* random bits anywhere */
            for (int p = 0; p < 6; p++)
                for (int k = 0; k < 4; k++)
                    if (next() % 3 == 0) w.planes[p][k] = fbits(next());
            if (next() % 2) s.r = fbits(next());
        } else if (mode == 2) { /* edge values */
            for (int p = 0; p < 6; p++)
                if (next() % 4 == 0) w.planes[p][next() % 4] = fbits(EDGE[next() % 9]);
            if (next() % 3 == 0) s.r = fbits(EDGE[next() % 9]);
            if (next() % 3 == 0) s.c[next() % 3] = fbits(EDGE[next() % 9]);
        }
        w.mask = next() % 4 ? 0x3f : (next() % 2 ? next() : next() & 0x3f);
        o.bound = next() % 50 ? &s : 0;
        struct fnvnx_cull_worker w2 = w;
        struct fnvnx_cull_object o2 = o;

        called = 0; seen_mask = 0xdeadbeef; seen_worker = 0;
        fnvnx_cull_worker_00a694e0(&w, &o);
        int c1 = called; uint32_t m1 = seen_mask; void *sw1 = seen_worker;
        called = 0; seen_mask = 0xdeadbeef; seen_worker = 0;
        ref_worker(&w2, &o2);
        int c2 = called; uint32_t m2 = seen_mask; void *sw2 = seen_worker;
        cases++;
        int ok = c1 == c2 && m1 == m2 && w.mask == w2.mask && (sw1 == &w) == (sw2 == &w2) &&
                 memcmp(w.planes, w2.planes, sizeof w.planes) == 0;
        if (!ok && bad++ < 8)
            printf("mismatch case %d: called %d/%d mask seen %08x/%08x after %08x/%08x\n", i, c1, c2, m1, m2,
                   w.mask, w2.mask);
    }
    printf("cull_worker: %ld cases, %ld mismatches\n", cases, bad);
    return bad != 0;
}
