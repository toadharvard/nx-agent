/* t_shcache.c - C side of test_shcache.py: drives render/shaders/cache/shcache.c (LP64 and ILP32 builds).  Modes (stdout is parsed by the test):
 *   lookup MAIN LOG|- BID ROWS    rows "hash len stage flags lhex|- payloadhex|MISS": prints "CHECKS n FAIL f"
 *   info MAIN LOG|- BID           "main_valid main_entries log_entries log_valid_len log_ignored"
 *   put MAIN LOG BID hash len stage flags lhex|- payloadhex   host duty (truncate the log to log_valid_len, append through the sink); prints the rc
 *   flips MAIN BID                accepted count after xoring each byte of MAIN in turn (must be 0)
 *   truncs MAIN LOG BID           for every prefix length k of LOG: "k log_entries log_valid_len"
 *   logflips MAIN LOG BID         for every byte i of LOG xored: "i log_entries log_valid_len"
 *   linkset                       exhaustive + random check of shcache_link_set against fnvnx_dk_assign
 *   verify MAIN BID DIR           every entry of DIR/entries.tsv (a shxlate output) is in MAIN with payload = pack(<id>.dksh, <id>.json): "CHECKS n FAIL f"
 *   keymake hash len stage flags lhex|-    "n_l lhex" or ERR       hash HEXBYTES  "sha256 l_key" (hex)
 *   refl JSON SECTION             pairs of a reflection json: hex list or ERR     payload   pack/unpack round trip */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "shcache.h"
#include "../../../render/shaders/include/fnvnx_dk_layout.h"
#include "fnvnx_sha256.h"

static int checks, fails;
#define CHECK(c) do { checks++; if (!(c)) { fails++; fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #c); } } while (0)

static uint8_t *slurp(const char *p, size_t *n)
{
    FILE *f = fopen(p, "rb"); uint8_t *b; long l;
    *n = 0; if (!f || !strcmp(p, "-")) { if (f) fclose(f); return NULL; }
    fseek(f, 0, SEEK_END); l = ftell(f); rewind(f); b = (uint8_t *)malloc(l ? (size_t)l : 1);
    if (fread(b, 1, (size_t)l, f) != (size_t)l) { free(b); fclose(f); return NULL; }
    fclose(f); *n = (size_t)l; return b;
}
static size_t unhex(const char *s, uint8_t *o) { size_t n = 0; if (!strcmp(s, "-")) return 0; for (; s[0] && s[1]; s += 2) { unsigned v; sscanf(s, "%2x", &v); o[n++] = (uint8_t)v; } return n; }
static void bid_of(const char *s, uint8_t *b) { memset(b, 0, 16); unhex(s, b); }
static int mkkey(shcache_key *k, const char *h, const char *len, const char *st, const char *fl, const char *lx)
{
    uint8_t l[64], ck[SHCACHE_KEY_LEN]; size_t n = unhex(lx, l);
    if (strlen(h) != 2 * SHCACHE_KEY_LEN || unhex(h, ck) != SHCACHE_KEY_LEN) return -1;   /* the code key: 64 hex digits */
    return shcache_key_make(k, ck, (uint32_t)strtoul(len, NULL, 10), atoi(st), (uint32_t)strtoul(fl, NULL, 16), l, (uint32_t)n);
}
static int file_sink(void *u, const void *b, size_t n) { return fwrite(b, 1, n, (FILE *)u) == n ? 0 : -1; }
static int fail_sink(void *u, const void *b, size_t n) { (void)u; (void)b; (void)n; return -1; }

static int mode_lookup(char **a)
{
    size_t mn, ln, rn; uint8_t *m = slurp(a[0], &mn), *l = slurp(a[1], &ln), *rows = slurp(a[3], &rn), bid[16]; char *line, *save;
    shcache *c; bid_of(a[2], bid); c = shcache_open(m, mn, l, ln, bid);
    rows = (uint8_t *)realloc(rows, rn + 1); rows[rn] = 0;
    for (line = strtok_r((char *)rows, "\n", &save); line; line = strtok_r(NULL, "\n", &save)) {
        char h[80], len[20], st[8], fl[20], lx[100], px[200000]; shcache_key k; const void *p = NULL; size_t n = 0; int hit;
        if (sscanf(line, "%79s %19s %7s %19s %99s %199999s", h, len, st, fl, lx, px) != 6) { CHECK(0); continue; }
        CHECK(mkkey(&k, h, len, st, fl, lx) == 0);
        hit = shcache_lookup(c, &k, &p, &n);
        if (!strcmp(px, "MISS")) CHECK(!hit);
        else { uint8_t exp[100000]; size_t en = unhex(px, exp); CHECK(hit && n == en && !memcmp(p, exp, en)); }
    }
    printf("CHECKS %d FAIL %d\n", checks, fails); shcache_close(c); free(m); free(l); free(rows); return fails != 0;
}
static void print_info(const shcache *c) { shcache_info i; shcache_get_info(c, &i); printf("%d %u %u %zu %u\n", i.main_valid, i.main_entries, i.log_entries, i.log_valid_len, i.log_ignored); }
static int mode_info(char **a)
{
    size_t mn, ln; uint8_t *m = slurp(a[0], &mn), *l = slurp(a[1], &ln), bid[16]; shcache *c; bid_of(a[2], bid); c = shcache_open(m, mn, l, ln, bid); print_info(c); shcache_close(c); free(m); free(l); return 0;
}
static int mode_put(char **a)
{
    size_t mn, ln, pn; uint8_t *m = slurp(a[0], &mn), *l = slurp(a[1], &ln), bid[16], pay[100000]; shcache *c; shcache_key k; shcache_info in; FILE *f; int rc;
    bid_of(a[2], bid); c = shcache_open(m, mn, l, ln, bid); shcache_get_info(c, &in);
    if (mkkey(&k, a[3], a[4], a[5], a[6], a[7])) { puts("ERR"); shcache_close(c); free(m); free(l); return 1; }
    pn = unhex(a[8], pay);
    { FILE *t = fopen(a[1], "ab"); if (t) fclose(t); }
    { char *buf = (char *)malloc(in.log_valid_len + 1); FILE *r = fopen(a[1], "rb"); size_t got = r ? fread(buf, 1, in.log_valid_len, r) : 0; if (r) fclose(r);   /* truncate to the valid prefix */
      f = fopen(a[1], "wb"); fwrite(buf, 1, got, f); fclose(f); free(buf); }
    f = fopen(a[1], "ab"); rc = shcache_put(c, &k, pay, pn, file_sink, f); fclose(f);
    printf("%d\n", rc);
    shcache_close(c); free(m); free(l); return 0;
}
static int mode_flips(char **a)
{
    size_t mn, i; uint8_t *m = slurp(a[0], &mn), bid[16]; int accepted = 0; shcache_info in; bid_of(a[1], bid);
    for (i = 0; i < mn; i++) { shcache *c; m[i] ^= 0x55; c = shcache_open(m, mn, NULL, 0, bid); shcache_get_info(c, &in); accepted += in.main_valid; shcache_close(c); m[i] ^= 0x55; }
    { shcache *c = shcache_open(m, mn, NULL, 0, bid); shcache_get_info(c, &in); printf("%d %d\n", accepted, in.main_valid); shcache_close(c); }
    { shcache *c = shcache_open(m, mn, NULL, 0, NULL); shcache_get_info(c, &in); printf("nobid %d\n", in.main_valid); shcache_close(c); }
    accepted = 0;                                          /* every proper prefix of the file (a truncated download) */
    for (i = 0; i < mn; i++) { shcache *c = shcache_open(m, i, NULL, 0, bid); shcache_get_info(c, &in); accepted += in.main_valid; shcache_close(c); }
    printf("prefixes %d\n", accepted);
    free(m); return 0;
}
static int mode_logscan(char **a, int flip)
{
    size_t mn, ln, k; uint8_t *m = slurp(a[0], &mn), *l = slurp(a[1], &ln), bid[16]; bid_of(a[2], bid);
    for (k = 0; k <= ln; k++) {
        shcache *c; shcache_info in;
        if (flip && k == ln) break;
        if (flip) l[k] ^= 0x55;
        c = shcache_open(m, mn, l, flip ? ln : k, bid); shcache_get_info(c, &in); printf("%zu %u %zu\n", k, in.log_entries, in.log_valid_len); shcache_close(c);
        if (flip) l[k] ^= 0x55;
    }
    free(m); free(l); return 0;
}

static int mode_verify(char **a)
{
    size_t mn, n; uint8_t *m = slurp(a[0], &mn), bid[16]; shcache *c; char path[1100], *tsv, *line, *save; bid_of(a[1], bid); c = shcache_open(m, mn, NULL, 0, bid);
    snprintf(path, sizeof path, "%s/entries.tsv", a[2]); tsv = (char *)slurp(path, &n); tsv = (char *)realloc(tsv, n + 1); tsv[n] = 0;
    line = strtok_r(tsv, "\n", &save);                                                         /* header */
    for (line = strtok_r(NULL, "\n", &save); line; line = strtok_r(NULL, "\n", &save)) {
        char id[96], h[80], st[4], fl[8], lx[100]; unsigned len; shcache_key k; size_t dn, jn; uint8_t *d, *j, *pk; const void *p = NULL; size_t pn = 0; int hit;
        if (sscanf(line, "%95s %79s %u %3s %7s %99s", id, h, &len, st, fl, lx) != 6) { CHECK(0); continue; }
        { char len_s[24]; sprintf(len_s, "%u", len); CHECK(mkkey(&k, h, len_s, st[0] == 'p' ? "1" : "0", fl, lx) == 0); }
        snprintf(path, sizeof path, "%s/%s.dksh", a[2], id); d = slurp(path, &dn); snprintf(path, sizeof path, "%s/%s.json", a[2], id); j = slurp(path, &jn);
        pk = (uint8_t *)malloc(8 + dn + jn); pn = shcache_payload_pack(d, (uint32_t)dn, (const char *)j, (uint32_t)jn, pk, 8 + dn + jn);
        hit = shcache_lookup(c, &k, &p, &n); CHECK(hit && n == pn && !memcmp(p, pk, pn)); free(d); free(j); free(pk);
    }
    printf("CHECKS %d FAIL %d\n", checks, fails); shcache_close(c); free(m); free(tsv); return fails != 0;
}

static unsigned rng_s = 12345u;
static unsigned rnd(void) { rng_s ^= rng_s << 13; rng_s ^= rng_s >> 17; rng_s ^= rng_s << 5; return rng_s; }
static int is_builtin(uint8_t p) { return p == FNVNX_DK_PAIR(FNVNX_USAGE_POSITION, 0) || p == FNVNX_DK_PAIR(FNVNX_USAGE_PSIZE, 0); }
static int valid_pair(int p) { return p / 16 <= 13; }

/* one case: returns after CHECKs. Oracle = the translator's own assignment (fnvnx_dk_assign) of each stage's set vs the draw's union. */
static void linkcase(const uint8_t *vo, int nv, const uint8_t *pi, int np)
{
    uint8_t own_v[224], u[448], l[SHCACHE_MAX_L]; signed char lo_v[224], lo_p[224], lo_u[448]; int nov = 0, nu = 0, i, j, canon = 1, rc; uint32_t nl = 99;
    for (i = 0; i < nv; i++) if (!is_builtin(vo[i])) own_v[nov++] = vo[i];
    for (i = 0; i < nov; i++) u[nu++] = own_v[i];
    for (i = 0; i < np; i++) { for (j = 0; j < nu && u[j] != pi[i]; j++) ; if (j == nu) u[nu++] = pi[i]; }
    rc = shcache_link_set(vo, (uint32_t)nv, pi, (uint32_t)np, l, &nl);
    if (nu > SHCACHE_MAX_L) { CHECK(rc == -1); return; }
    CHECK(rc == 0);
    for (i = 0; i < nu; i++) if (fnvnx_dk_varying_location(u[i] / 16, u[i] % 16) < 0) canon = 0;
    if (canon) {                                          /* stage alone must be valid: same slots as under the union */
        CHECK(nl == 0);
        CHECK(fnvnx_dk_assign(FNVNX_DK_KIND_VARYING, u, nu, lo_u) == 0 && fnvnx_dk_assign(FNVNX_DK_KIND_VARYING, own_v, nov, lo_v) == 0 && fnvnx_dk_assign(FNVNX_DK_KIND_VARYING, pi, np, lo_p) == 0);
        for (i = 0; i < nov; i++) { for (j = 0; u[j] != own_v[i]; j++) ; CHECK(lo_v[i] == lo_u[j]); }
        for (i = 0; i < np; i++) { for (j = 0; u[j] != pi[i]; j++) ; CHECK(lo_p[i] == lo_u[j]); }
    } else {                                              /* L = sorted union, both stages keyed by it */
        CHECK(nl == (uint32_t)nu);
        for (i = 0; i < nu; i++) { int cnt = 0; for (j = 0; j < (int)nl; j++) cnt += l[j] == u[i]; CHECK(cnt == 1); }
        for (j = 1; j < (int)nl; j++) CHECK(l[j - 1] < l[j]);
    }
}
static int mode_linkset(void)
{
    int a, b, t, i; uint8_t vo[40], pi[40];
    for (a = 0; a < 224; a++) for (b = 0; b < 224; b++) { vo[0] = (uint8_t)a; pi[0] = (uint8_t)b; if (valid_pair(a) && valid_pair(b)) linkcase(vo, 1, pi, 1); }
    for (t = 0; t < 30000; t++) {
        int nv = rnd() % 14, np = rnd() % 14, k; unsigned canon_only = rnd() % 3 == 0;
        for (k = 0; k < nv + np; k++) {
            uint8_t v; int tries = 0, dup;
            do { v = (uint8_t)(canon_only ? (rnd() % 2 ? FNVNX_DK_PAIR(FNVNX_USAGE_TEXCOORD, rnd() % 16) : FNVNX_DK_PAIR(FNVNX_USAGE_COLOR, rnd() % 2)) : rnd() % 224);
                 dup = 0; for (i = 0; i < (k < nv ? k : k - nv); i++) if ((k < nv ? vo[i] : pi[i]) == v) dup = 1; } while (dup && ++tries < 50);
            if (dup) { if (k < nv) nv = k; else np = k - nv; break; }
            if (k < nv) vo[k] = v; else pi[k - nv] = v;
        }
        linkcase(vo, nv, pi, np);
    }
    { uint8_t big[40], l[32]; uint32_t nl; for (i = 0; i < 32; i++) big[i] = FNVNX_DK_PAIR(FNVNX_USAGE_TEXCOORD, i % 16) + (i >= 16 ? 16 : 0); CHECK(shcache_link_set(big, 32, NULL, 0, l, &nl) == -1); }   /* 32 distinct pairs */
    { uint8_t l[32]; uint32_t nl; uint8_t bad = 14 * 16; CHECK(shcache_link_set(&bad, 1, NULL, 0, l, &nl) == -1); }                                                            /* usage 14 */
    printf("CHECKS %d FAIL %d\n", checks, fails); return fails != 0;
}
static int mode_payload(void)
{
    uint8_t out[64]; const uint8_t *d; const char *j; uint32_t dn, jn; const char js[] = "{\"x\": 1}", dk[] = "DKSH\1\2\3";
    size_t n = shcache_payload_pack(dk, 7, js, 8, out, sizeof out);
    CHECK(n == 8 + 7 + 8); CHECK(shcache_payload_unpack(out, n, &d, &dn, &j, &jn) == 0); CHECK(dn == 7 && jn == 8 && !memcmp(d, dk, 7) && !memcmp(j, js, 8));
    CHECK(shcache_payload_unpack(out, n - 1, &d, &dn, &j, &jn) == -1); CHECK(shcache_payload_unpack(out, 4, &d, &dn, &j, &jn) == -1); CHECK(shcache_payload_pack(dk, 7, js, 8, out, n - 1) == 0);
    { shcache *c = shcache_open(NULL, 0, NULL, 0, NULL); shcache_key k; shcache_info in; CHECK(c != NULL); { uint8_t ck[SHCACHE_KEY_LEN] = { 1 }; shcache_key_make(&k, ck, 2, 0, 0, NULL, 0); }
      CHECK(shcache_put(c, &k, "ab", 2, fail_sink, NULL) == -1);                        /* a sink failure stores nothing */
      { const void *p; size_t pn; CHECK(!shcache_lookup(c, &k, &p, &pn)); }
      shcache_get_info(c, &in); CHECK(!in.main_valid && in.log_entries == 0 && in.log_valid_len == 0); shcache_close(c); }
    printf("CHECKS %d FAIL %d\n", checks, fails); return fails != 0;
}

int main(int argc, char **argv)
{
    const char *m = argc > 1 ? argv[1] : "";
    if (!strcmp(m, "lookup") && argc == 6) return mode_lookup(argv + 2);
    if (!strcmp(m, "info") && argc == 5) return mode_info(argv + 2);
    if (!strcmp(m, "put") && argc == 11) return mode_put(argv + 2);
    if (!strcmp(m, "flips") && argc == 4) return mode_flips(argv + 2);
    if (!strcmp(m, "truncs") && argc == 5) return mode_logscan(argv + 2, 0);
    if (!strcmp(m, "logflips") && argc == 5) return mode_logscan(argv + 2, 1);
    if (!strcmp(m, "linkset")) return mode_linkset();
    if (!strcmp(m, "verify") && argc == 5) return mode_verify(argv + 2);
    if (!strcmp(m, "payload")) return mode_payload();
    if (!strcmp(m, "keymake") && argc == 7) { shcache_key k; char o[80] = ""; int i; if (mkkey(&k, argv[2], argv[3], argv[4], argv[5], argv[6])) { puts("ERR"); return 0; } for (i = 0; i < k.n_l; i++) sprintf(o + 2 * i, "%02x", k.l[i]); printf("%u %s %u\n", k.n_l, o, k.flags); return 0; }
    if (!strcmp(m, "hash") && argc == 3) { uint8_t b[4096]; size_t n = unhex(argv[2], b); uint8_t d[32], lk[32]; char dh[65], lh[65]; int i; fnvnx_sha256(b, n, d); shcache_l_key(b, (uint32_t)n, lk); for (i = 0; i < 32; i++) { sprintf(dh + 2 * i, "%02x", d[i]); sprintf(lh + 2 * i, "%02x", lk[i]); } printf("%s %s\n", dh, lh); return 0; }
    if (!strcmp(m, "refl") && argc == 4) { size_t n; uint8_t *j = slurp(argv[2], &n), p[300]; int r, i; j = (uint8_t *)realloc(j, n + 1); j[n] = 0; r = shcache_refl_pairs((char *)j, argv[3], p, sizeof p);
        if (r < 0) puts("ERR"); else { for (i = 0; i < r; i++) printf("%02x", p[i]); puts(""); } free(j); return 0; }
    fprintf(stderr, "bad usage\n"); return 2;
}
