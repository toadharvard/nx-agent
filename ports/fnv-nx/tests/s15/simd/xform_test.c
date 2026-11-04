/* xform_test.c - bit-exact check of tools/link/overrides/update_world_loc_00a68bf0.c against a scalar transcription
 * of the original compose (NiAVObject::UpdateWorldLoc 0x00a68bf0 with NiTransform::operator* 0x0062c250 and its
 * helpers 0x0043f8d0, 0x004b4500, 0x004a3760, 0x00439e90), in their order of float operations. Random, edge and
 * flag/parent cases; the bytes around the world transform must stay untouched. Our own code: no decomp text.
 * tests/s15/simd/run.py builds it for AArch64 LP64 (the object layout below uses 32-bit parent fields, as ILP32). */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

struct fnvnx_avobject;
void fnvnx_update_world_loc_00a68bf0(struct fnvnx_avobject *self);

/* the override's view: parent is a 32-bit field; objects live below 4 GiB in a static pool (qemu-user, -static) */
static uint8_t pool[64][0xa0] __attribute__((aligned(16)));

static void ref(uint8_t *self)
{
    uint32_t parent, flags;
    memcpy(&parent, self + 0x18, 4);
    memcpy(&flags, self + 0x30, 4);
    float tmp[13];
    if (!parent) {
        memcpy(tmp, self + 0x34, 52);
    } else if ((flags >> 9) & 1) {
        memcpy(tmp, (uint8_t *)(uintptr_t)parent + 0x68, 52);
    } else {
        float P[13], L[13];
        memcpy(P, (uint8_t *)(uintptr_t)parent + 0x68, 52);
        memcpy(L, self + 0x34, 52);
        tmp[12] = P[12] * L[12];
        for (int i = 0; i < 3; i++)
            for (int j = 0; j < 3; j++)
                tmp[3 * i + j] = P[3 * i] * L[j] + P[3 * i + 1] * L[3 + j] + P[3 * i + 2] * L[6 + j];
        float v[3], s[3];
        for (int i = 0; i < 3; i++)
            v[i] = P[3 * i] * L[9] + P[3 * i + 1] * L[10] + P[3 * i + 2] * L[11];
        for (int i = 0; i < 3; i++)
            s[i] = P[12] * v[i];
        for (int i = 0; i < 3; i++)
            tmp[9 + i] = P[9 + i] + s[i];
    }
    memcpy(self + 0x68, tmp, 52);
}

static uint64_t rng = 0x243f6a8885a308d3ull;
static uint32_t next(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return (uint32_t)(rng >> 16); }
static float fbits(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }
static const uint32_t EDGE[] = {0, 0x80000000, 0x3f800000, 0xbf800000, 0x7f800000, 0xff800000, 0x7fc00000,
                                0x7fa00001, 0x00000001, 0x7f7fffff, 0x3f000000, 0x00800000};

static float pick(int mode)
{
    switch (mode) {
    case 0: return (float)((int32_t)next() % 20000) / 10000.0f;
    case 1: return fbits(next());
    default: return fbits(EDGE[next() % (sizeof EDGE / sizeof EDGE[0])]);
    }
}

int main(void)
{
    static uint8_t a[64][0xa0], b[64][0xa0];
    long cases = 0, bad = 0;
    if ((uintptr_t)pool > 0xffffffffu - sizeof pool) {
        puts("xform: pool above 4 GiB, cannot model ILP32 parents");
        return 1;
    }
    for (int round = 0; round < 100000; round++) {
        int mode = (int)(next() % 3);
        for (int o = 0; o < 4; o++)
            for (int k = 0; k < 0xa0; k += 4) {
                float f = pick(next() % 4 ? mode : 2);
                memcpy(&pool[o][k], &f, 4);
            }
        uint32_t flags = next();
        memcpy(&pool[0][0x30], &flags, 4);
        int pk = (int)(next() % 4);   /* 0: no parent, else parent pool[pk] */
        uint32_t par = pk ? (uint32_t)(uintptr_t)pool[pk] : 0;
        memcpy(&pool[0][0x18], &par, 4);
        memcpy(a, pool, sizeof a);
        memcpy(b, pool, sizeof b);
        /* both run on the same pool objects: run the reference on a copy of the pool, the override in place */
        memcpy(pool, a, sizeof a);
        ref(pool[0]);
        memcpy(b, pool, sizeof b);   /* reference result */
        memcpy(pool, a, sizeof a);
        fnvnx_update_world_loc_00a68bf0((struct fnvnx_avobject *)pool[0]);
        cases++;
        int differ = 0;
        for (int o = 0; o < 4; o++)
            for (int k = 0; k < 0xa0; k += 4) {
                uint32_t x, y;
                memcpy(&x, &pool[o][k], 4);
                memcpy(&y, &b[o][k], 4);
                int xn = (x & 0x7fffffff) > 0x7f800000, yn = (y & 0x7fffffff) > 0x7f800000;
                int inworld = o == 0 && k >= 0x68 && k < 0x68 + 52;
                if (inworld ? (xn != yn || (!xn && x != y)) : x != y)
                    differ = 1;
            }
        if (differ && bad++ < 8)
            printf("mismatch round %d (parent %d, flag %u)\n", round, pk, (flags >> 9) & 1);
    }
    printf("xform: %ld cases, %ld mismatches\n", cases, bad);
    return bad != 0;
}
