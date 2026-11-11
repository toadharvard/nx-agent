#!/usr/bin/env python3
"""Exhaustive proof that rt/rcptab.c (fnvl_rcp32 / fnvl_rsqrt32) equals the real RCPSS / RSQRTSS on ALL 2^32 inputs of each.
Builds a native x86-64 program (must run on the reference CPU, see header of rt/rcptab.c), 3 threads. Prints
`rcp equal N different M`, `rsqrt equal N different M`, the first 5 differing inputs and SHA-256 over the 2^32 hardware
results (little-endian u32, input order 0..2^32-1) of each function. Exit 1 unless M == 0 for both.
Usage: test/rcptest.py [workdir]"""
import os, subprocess, sys
H = os.path.dirname(os.path.abspath(__file__)); R = os.path.dirname(H)
W = sys.argv[1] if len(sys.argv) > 1 else '/home/user/l3work/rcp'
os.makedirs(W, exist_ok=True)
C = r'''
#include <immintrin.h>
#include <pthread.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
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
#define NT 3
static u32 hw[BLK], cp[BLK];
static u32 base; static int fn;
static uint64_t ndiff[NT], first_n[NT]; static u32 first[NT][5];
static void *work(void *arg) {
    int t = (int)(intptr_t)arg; u32 lo = BLK / NT * t, hi = t == NT - 1 ? BLK : BLK / NT * (t + 1);
    for (u32 i = lo; i < hi; i++) {
        u32 x = base + i, h, c;
        if (fn == 0) { h = hw_rcp(x); c = fnvl_rcp32(x); } else { h = hw_rsq(x); c = fnvl_rsqrt32(x); }
        hw[i] = h;
        if (h != c) { if (ndiff[t] < 5) first[t][ndiff[t]] = x; ndiff[t]++; }
    }
    return 0;
}
int main(void) {
    _mm_setcsr(0x1f80);
    for (fn = 0; fn < 2; fn++) {
        sha_t s; sha_init(&s); uint64_t nd = 0; u32 fd[5]; int nf = 0;
        memset(ndiff, 0, sizeof ndiff);
        for (uint64_t b = 0; b < (1ull << 32); b += BLK) {
            base = (u32)b; pthread_t th[NT];
            for (int t = 0; t < NT; t++) pthread_create(&th[t], 0, work, (void *)(intptr_t)t);
            for (int t = 0; t < NT; t++) pthread_join(th[t], 0);
            unsigned char *p = (unsigned char *)cp; for (u32 i = 0; i < BLK; i++) { p[4*i] = hw[i]; p[4*i+1] = hw[i] >> 8; p[4*i+2] = hw[i] >> 16; p[4*i+3] = hw[i] >> 24; }
            sha_blocks(&s, p, BLK * 4 / 64);
        }
        for (int t = 0; t < NT; t++) { nd += ndiff[t]; for (uint64_t k = 0; k < ndiff[t] && k < 5 && nf < 5; k++) fd[nf++] = first[t][k]; }
        char dg[65]; sha_final(&s, (1ull << 32) * 4, dg);
        const char *nm = fn ? "rsqrt" : "rcp";
        printf("%s equal %llu different %llu\n", nm, (unsigned long long)((1ull << 32) - nd), (unsigned long long)nd);
        for (int i = 0; i < nf; i++) printf("%s differs at %08x: hw %08x tab %08x\n", nm, fd[i], fn ? hw_rsq(fd[i]) : hw_rcp(fd[i]), fn ? fnvl_rsqrt32(fd[i]) : fnvl_rcp32(fd[i]));
        printf("%s sha256 %s\n", nm, dg); fflush(stdout);
        if (nd) bad = 1;
    }
    return bad;
}
'''.replace('int main(void) {', 'static int bad;\nint main(void) {')
open(W + '/rcpcmp.c', 'w').write(C)
subprocess.check_call(['gcc', '-O2', '-msse2', '-pthread', '-o', W + '/rcpcmp', W + '/rcpcmp.c', R + '/rt/rcptab.c'])
sys.exit(1 if subprocess.call([W + '/rcpcmp']) else 0)
