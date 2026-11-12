/* rcpcert.c: certifies rt/rcptab.c against the real RCPSS / RSQRTSS of THIS host on all 2^32 inputs of each function and writes
 * the certificate (see farm/rcpcert.sh, which builds and runs it; usage: rcpcert SOURCE_SHA256 HEADER_SHA256 OUT.json).
 *   1. CPUID (instruction, not /proc): vendor, family, model, stepping; refused unless equal to the constants of rt/rcpref.h.
 *   2. For every x in 0..2^32-1 and both functions: hardware result (_mm_rcp_ss / _mm_rsqrt_ss, MXCSR 0x1f80) against
 *      fnvl_rcp32 / fnvl_rsqrt32 (rt/rcptab.c compiled for this host), bit for bit; mismatches are counted, none is skipped.
 *   3. SHA-256 over the stream of the 2^32 little-endian u32 hardware results in input order (fixed blocks of 2^22 inputs, each
 *      block split into contiguous slices over all cores, the hash taken over the blocks in order: the digest does not depend on
 *      the core count).
 *   4. Certificate only if mismatches == 0 (exit 0); otherwise nothing is written and the exit status is 1 (2: not the reference CPU). */
#include <cpuid.h>
#include <immintrin.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "../rt/rcpref.h"
typedef uint32_t u32;
u32 fnvl_rcp32(u32); u32 fnvl_rsqrt32(u32);
static u32 hw_rcp(u32 b) { float f; memcpy(&f, &b, 4); float r = _mm_cvtss_f32(_mm_rcp_ss(_mm_set_ss(f))); u32 o; memcpy(&o, &r, 4); return o; }
static u32 hw_rsq(u32 b) { float f; memcpy(&f, &b, 4); float r = _mm_cvtss_f32(_mm_rsqrt_ss(_mm_set_ss(f))); u32 o; memcpy(&o, &r, 4); return o; }
/* SHA-256 */
static const u32 K[64] = {0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
typedef struct { u32 h[8]; } sha_t;
#define ROR(x,n) (((x) >> (n)) | ((x) << (32 - (n))))
static void sha_init(sha_t *s) { static const u32 i[8] = {0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19}; memcpy(s->h, i, 32); }
static void sha_blocks(sha_t *s, const unsigned char *p, size_t n) {   /* n 64-byte blocks */
    while (n--) {
        u32 w[64], a, b, c, d, e, f, g, h;
        for (int i = 0; i < 16; i++) w[i] = (u32)p[4*i] << 24 | p[4*i+1] << 16 | p[4*i+2] << 8 | p[4*i+3];
        for (int i = 16; i < 64; i++) { u32 s0 = ROR(w[i-15],7) ^ ROR(w[i-15],18) ^ (w[i-15] >> 3), s1 = ROR(w[i-2],17) ^ ROR(w[i-2],19) ^ (w[i-2] >> 10); w[i] = w[i-16] + s0 + w[i-7] + s1; }
        a = s->h[0]; b = s->h[1]; c = s->h[2]; d = s->h[3]; e = s->h[4]; f = s->h[5]; g = s->h[6]; h = s->h[7];
        for (int i = 0; i < 64; i++) {
            u32 t1 = h + (ROR(e,6) ^ ROR(e,11) ^ ROR(e,25)) + ((e & f) ^ (~e & g)) + K[i] + w[i], t2 = (ROR(a,2) ^ ROR(a,13) ^ ROR(a,22)) + ((a & b) ^ (a & c) ^ (b & c));
            h = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
        }
        s->h[0] += a; s->h[1] += b; s->h[2] += c; s->h[3] += d; s->h[4] += e; s->h[5] += f; s->h[6] += g; s->h[7] += h;
        p += 64;
    }
}
static void sha_final(sha_t *s, uint64_t total, char *out) {       /* total bytes, multiple of 64 already hashed */
    unsigned char b[64] = {0x80}; b[56] = (total * 8) >> 56; b[57] = (total * 8) >> 48; b[58] = (total * 8) >> 40; b[59] = (total * 8) >> 32;
    b[60] = (total * 8) >> 24; b[61] = (total * 8) >> 16; b[62] = (total * 8) >> 8; b[63] = total * 8;
    sha_blocks(s, b, 1);
    for (int i = 0; i < 8; i++) sprintf(out + 8 * i, "%08x", s->h[i]);
}
#define BLK (1u << 22)
#define MAXT 256
static u32 hw[BLK];
static u32 base; static int fn, nt;
static uint64_t ndiff[MAXT];
static void *work(void *arg) {
    int t = (int)(intptr_t)arg; u32 lo = (u32)((uint64_t)BLK * t / nt), hi = (u32)((uint64_t)BLK * (t + 1) / nt);
    uint64_t nd = 0;
    for (u32 i = lo; i < hi; i++) {
        u32 x = base + i, h, c;
        if (fn == 0) { h = hw_rcp(x); c = fnvl_rcp32(x); } else { h = hw_rsq(x); c = fnvl_rsqrt32(x); }
        hw[i] = h;
        nd += h != c;
    }
    ndiff[t] = nd;
    return 0;
}
int main(int argc, char **argv) {
    if (argc != 4) { fprintf(stderr, "usage: rcpcert SOURCE_SHA256 HEADER_SHA256 OUT.json\n"); return 3; }
    unsigned a, b, c, d; char vendor[13];
    if (!__get_cpuid(0, &a, &b, &c, &d)) { fprintf(stderr, "no CPUID\n"); return 2; }
    memcpy(vendor, &b, 4); memcpy(vendor + 4, &d, 4); memcpy(vendor + 8, &c, 4); vendor[12] = 0;
    __get_cpuid(1, &a, &b, &c, &d);
    unsigned fam = (a >> 8) & 0xf, mod = (a >> 4) & 0xf, step = a & 0xf;
    if (fam == 0xf) fam += (a >> 20) & 0xff;
    if (fam == 6 || fam == 0xf) mod |= ((a >> 16) & 0xf) << 4;
    printf("cpu %s family %u model %u stepping %u\n", vendor, fam, mod, step);
    if (strcmp(vendor, FNVL_RCP_REF_VENDOR) || fam != FNVL_RCP_REF_FAMILY || mod != FNVL_RCP_REF_MODEL || step != FNVL_RCP_REF_STEPPING) {
        fprintf(stderr, "this host is not the reference CPU (%s family %d model %d stepping %d): no certificate\n", FNVL_RCP_REF_VENDOR, FNVL_RCP_REF_FAMILY, FNVL_RCP_REF_MODEL, FNVL_RCP_REF_STEPPING);
        return 2;
    }
    long n = sysconf(_SC_NPROCESSORS_ONLN); nt = n < 1 ? 1 : n > MAXT ? MAXT : (int)n;
    _mm_setcsr(0x1f80);
    static unsigned char blk[BLK * 4];
    char dg[2][65]; uint64_t mism = 0;
    for (fn = 0; fn < 2; fn++) {
        sha_t s; sha_init(&s);
        for (uint64_t bb = 0; bb < (1ull << 32); bb += BLK) {
            base = (u32)bb; pthread_t th[MAXT];
            for (int t = 0; t < nt; t++) pthread_create(&th[t], 0, work, (void *)(intptr_t)t);
            for (int t = 0; t < nt; t++) { pthread_join(th[t], 0); mism += ndiff[t]; }
            for (u32 i = 0; i < BLK; i++) { blk[4*i] = hw[i]; blk[4*i+1] = hw[i] >> 8; blk[4*i+2] = hw[i] >> 16; blk[4*i+3] = hw[i] >> 24; }
            sha_blocks(&s, blk, BLK * 4 / 64);
        }
        sha_final(&s, (1ull << 32) * 4, dg[fn]);
        printf("%s sha256 %s\n", fn ? "rsqrt" : "rcp", dg[fn]); fflush(stdout);
    }
    printf("mismatches %llu\n", (unsigned long long)mism);
    if (mism) { fprintf(stderr, "mismatches: no certificate\n"); return 1; }
    FILE *f = fopen(argv[3], "w");
    if (!f) { perror(argv[3]); return 3; }
    fprintf(f, "{\"cpu\": {\"family\": %u, \"model\": %u, \"stepping\": %u, \"vendor\": \"%s\"}, \"format\": \"fnvnx-rcpcert/1\", \"header_sha256\": \"%s\", "
               "\"mismatches\": 0, \"rcp_stream_sha256\": \"%s\", \"rsqrt_stream_sha256\": \"%s\", \"source_sha256\": \"%s\"}\n",
            fam, mod, step, vendor, argv[2], dg[0], dg[1], argv[1]);
    if (fclose(f)) { perror(argv[3]); return 3; }
    return 0;
}
