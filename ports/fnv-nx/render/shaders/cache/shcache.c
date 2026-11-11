/* shcache.c - the cache file reader, the in-memory merge of the append log, and the log writer. See shcache.h for the formats. */
#include "shcache.h"
#include <stdlib.h>
#include <string.h>

typedef struct lent { shcache_key k; uint64_t lh; uint8_t *payload; size_t n; } lent;
struct shcache {
    const uint8_t *main; size_t main_n; uint32_t n_main;
    uint8_t build_id[SHCACHE_BUILD_ID_LEN];
    lent *ents; uint32_t n_ents, cap_ents;                /* log entries, sorted by tuple */
    shcache_info info;
};

static uint32_t rd32(const uint8_t *p) { return p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }
static uint64_t rd64(const uint8_t *p) { return rd32(p) | (uint64_t)rd32(p + 4) << 32; }
static void wr32(uint8_t *p, uint32_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24); }
static void wr64(uint8_t *p, uint64_t v) { wr32(p, (uint32_t)v); wr32(p + 4, (uint32_t)(v >> 32)); }

/* order of the index: (code_hash, stage, flags, l_hash, code_len) */
static int cmp(uint64_t h1, unsigned s1, uint32_t f1, uint64_t l1, uint32_t n1, uint64_t h2, unsigned s2, uint32_t f2, uint64_t l2, uint32_t n2)
{
    if (h1 != h2) return h1 < h2 ? -1 : 1;
    if (s1 != s2) return s1 < s2 ? -1 : 1;
    if (f1 != f2) return f1 < f2 ? -1 : 1;
    if (l1 != l2) return l1 < l2 ? -1 : 1;
    if (n1 != n2) return n1 < n2 ? -1 : 1;
    return 0;
}
static int l_ok(const uint8_t *l, uint32_t n)             /* ascending, unique, usage <= 13 */
{
    uint32_t i;
    for (i = 0; i < n; i++) if (l[i] / 16 > 13 || (i && l[i - 1] >= l[i])) return 0;
    return 1;
}
static uint32_t round4(uint32_t v) { return (v + 3) & ~3u; }

static int main_valid(shcache *c, const uint8_t *b, size_t n, const uint8_t *build_id)
{
    uint32_t cnt, run, i; const uint8_t *prev = NULL;
    if (!b || n < SHCACHE_MAIN_HEADER || memcmp(b, "FNSC", 4) || rd32(b + 4) != SHCACHE_VERSION) return 0;
    if (build_id && memcmp(b + 8, build_id, SHCACHE_BUILD_ID_LEN)) return 0;
    cnt = rd32(b + 24);
    if (rd32(b + 28) != SHCACHE_MAIN_HEADER || (uint64_t)cnt * SHCACHE_INDEX_ENTRY + SHCACHE_MAIN_HEADER > n) return 0;
    run = SHCACHE_MAIN_HEADER + cnt * SHCACHE_INDEX_ENTRY;
    if (rd32(b + 32) != run || rd32(b + 36) != n || n > 0xFFFFFFFFu) return 0;
    for (i = 48; i < 64; i++) if (b[i]) return 0;
    if (rd64(b + 40) != shcache_hash64(b + SHCACHE_MAIN_HEADER, n - SHCACHE_MAIN_HEADER)) return 0;
    for (i = 0; i < cnt; i++) {
        const uint8_t *e = b + SHCACHE_MAIN_HEADER + (size_t)i * SHCACHE_INDEX_ENTRY, *bl; uint32_t off = rd32(e + 28), sz = rd32(e + 32), nl = e[25];
        if (e[24] > 1 || (rd32(e + 20) & ~SHCACHE_FLAGS_MASK) || nl > SHCACHE_MAX_L || e[26] || e[27]) return 0;
        if (off != run || sz < round4(1 + nl) || (uint64_t)off + sz > n) return 0;
        bl = b + off;
        if (bl[0] != nl || !l_ok(bl + 1, nl) || rd64(e + 8) != shcache_l_hash(bl + 1, nl)) return 0;
        if (prev && cmp(rd64(prev), prev[24], rd32(prev + 20), rd64(prev + 8), rd32(prev + 16), rd64(e), e[24], rd32(e + 20), rd64(e + 8), rd32(e + 16)) >= 0) return 0;
        prev = e; run += sz;
    }
    if (run != n) return 0;
    c->main = b; c->main_n = n; c->n_main = cnt;
    return 1;
}

static int find_main(const shcache *c, const shcache_key *k, const uint8_t **pay, size_t *n)
{
    uint64_t lh = shcache_l_hash(k->l, k->n_l); uint32_t lo = 0, hi = c->n_main;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2; const uint8_t *e = c->main + SHCACHE_MAIN_HEADER + (size_t)mid * SHCACHE_INDEX_ENTRY;
        int r = cmp(rd64(e), e[24], rd32(e + 20), rd64(e + 8), rd32(e + 16), k->code_hash, k->stage, k->flags, lh, k->code_len);
        if (r < 0) lo = mid + 1; else if (r > 0) hi = mid;
        else {
            const uint8_t *bl = c->main + rd32(e + 28); uint32_t sz = rd32(e + 32), pre = round4(1 + k->n_l);
            if (bl[0] != k->n_l || memcmp(bl + 1, k->l, k->n_l)) return 0;
            *pay = bl + pre; *n = sz - pre; return 1;
        }
    }
    return 0;
}

static int find_log(const shcache *c, const shcache_key *k, uint32_t *pos)   /* 1 found at *pos, else *pos = insertion point */
{
    uint64_t lh = shcache_l_hash(k->l, k->n_l); uint32_t lo = 0, hi = c->n_ents;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2; const lent *e = &c->ents[mid];
        int r = cmp(e->k.code_hash, e->k.stage, e->k.flags, e->lh, e->k.code_len, k->code_hash, k->stage, k->flags, lh, k->code_len);
        if (r < 0) lo = mid + 1; else if (r > 0) hi = mid;
        else { *pos = mid; return e->k.n_l == k->n_l && !memcmp(e->k.l, k->l, k->n_l); }
    }
    *pos = lo; return 0;
}

int shcache_lookup(const shcache *c, const shcache_key *k, const void **payload, size_t *n)
{
    const uint8_t *p; size_t m; uint32_t pos;
    if (!c || !k) return 0;
    if (find_main(c, k, &p, &m)) { *payload = p; *n = m; return 1; }
    if (find_log(c, k, &pos)) { *payload = c->ents[pos].payload; *n = c->ents[pos].n; return 1; }
    return 0;
}

static int add_ent(shcache *c, const shcache_key *k, const void *payload, size_t n)   /* copies payload; 0 ok, -1 oom */
{
    uint32_t pos; uint8_t *copy;
    if (find_log(c, k, &pos)) return 0;
    if (c->n_ents == c->cap_ents) {
        uint32_t cap = c->cap_ents ? c->cap_ents * 2 : 16; lent *e = (lent *)realloc(c->ents, cap * sizeof *e);
        if (!e) return -1;
        c->ents = e; c->cap_ents = cap;
    }
    copy = (uint8_t *)malloc(n ? n : 1);
    if (!copy) return -1;
    if (n) memcpy(copy, payload, n);
    memmove(&c->ents[pos + 1], &c->ents[pos], (c->n_ents - pos) * sizeof(lent));
    c->ents[pos].k = *k; c->ents[pos].lh = shcache_l_hash(k->l, k->n_l); c->ents[pos].payload = copy; c->ents[pos].n = n;
    c->n_ents++;
    return 0;
}

#define REC_FIXED 30u                                       /* u32 len, u64 chk, u64 hash, u32 len, u32 flags, u8 stage, u8 n_l */
static uint64_t rec_chk(const uint8_t *rec, uint32_t len) { uint8_t l4[4]; uint64_t h; wr32(l4, len); h = shcache_hash64(l4, 4);
    { uint32_t i; for (i = 12; i < len; i++) { h ^= rec[i]; h *= 0x100000001B3ull; } } return h; }

size_t shcache_log_record(const shcache_key *k, const void *payload, size_t n, uint8_t *out, size_t cap)
{
    size_t len = REC_FIXED + k->n_l + n;
    if (cap < len || len > 0xFFFFFFFFu) return 0;
    wr32(out, (uint32_t)len); wr64(out + 12, k->code_hash); wr32(out + 20, k->code_len); wr32(out + 24, k->flags);
    out[28] = k->stage; out[29] = k->n_l; memcpy(out + REC_FIXED, k->l, k->n_l); if (n) memcpy(out + REC_FIXED + k->n_l, payload, n);
    wr64(out + 4, rec_chk(out, (uint32_t)len));
    return len;
}

static void log_load(shcache *c, const uint8_t *b, size_t n, const uint8_t *build_id)
{
    size_t pos = SHCACHE_LOG_HEADER;
    if (!b || n < SHCACHE_LOG_HEADER || memcmp(b, "FNSL", 4) || rd32(b + 4) != SHCACHE_VERSION) return;
    if (build_id && memcmp(b + 8, build_id, SHCACHE_BUILD_ID_LEN)) return;
    c->info.log_valid_len = pos;
    while (n - pos >= REC_FIXED) {
        const uint8_t *r = b + pos; uint32_t len = rd32(r), nl = r[29]; shcache_key k;
        if (len < REC_FIXED + nl || len > n - pos || nl > SHCACHE_MAX_L || r[28] > 1 || rd64(r + 4) != rec_chk(r, len)) break;
        if (shcache_key_make(&k, rd64(r + 12), rd32(r + 20), r[28], rd32(r + 24), r + REC_FIXED, nl) || k.flags != rd32(r + 24)) break;
        if (memcmp(k.l, r + REC_FIXED, nl)) break;                        /* the stored L must already be canonical */
        { const uint8_t *p; size_t m; uint32_t pos2;
          if (find_main(c, &k, &p, &m) || find_log(c, &k, &pos2)) c->info.log_ignored++;
          else if (add_ent(c, &k, r + REC_FIXED + nl, len - REC_FIXED - nl)) break;
          else c->info.log_entries++; }
        pos += len; c->info.log_valid_len = pos;
    }
}

shcache *shcache_open(const void *main_bytes, size_t main_n, const void *log_bytes, size_t log_n, const uint8_t *build_id)
{
    shcache *c = (shcache *)calloc(1, sizeof *c);
    if (!c) return NULL;
    if (build_id) memcpy(c->build_id, build_id, SHCACHE_BUILD_ID_LEN);
    c->info.main_valid = main_valid(c, (const uint8_t *)main_bytes, main_n, build_id);
    c->info.main_entries = c->info.main_valid ? c->n_main : 0;
    log_load(c, (const uint8_t *)log_bytes, log_n, build_id);
    return c;
}

void shcache_close(shcache *c)
{
    uint32_t i;
    if (!c) return;
    for (i = 0; i < c->n_ents; i++) free(c->ents[i].payload);
    free(c->ents); free(c);
}

void shcache_get_info(const shcache *c, shcache_info *info) { *info = c->info; }

int shcache_put(shcache *c, const shcache_key *k, const void *payload, size_t n, shcache_sink sink, void *user)
{
    const void *p; size_t m; uint8_t *buf; size_t hdr = c->info.log_valid_len ? 0 : SHCACHE_LOG_HEADER, len; int rc;
    if (shcache_lookup(c, k, &p, &m)) return 0;
    buf = (uint8_t *)malloc(hdr + REC_FIXED + k->n_l + n);
    if (!buf) return -1;
    if (hdr) { memcpy(buf, "FNSL", 4); wr32(buf + 4, SHCACHE_VERSION); memcpy(buf + 8, c->build_id, SHCACHE_BUILD_ID_LEN); }
    len = shcache_log_record(k, payload, n, buf + hdr, REC_FIXED + k->n_l + n);
    if (!len || add_ent(c, k, payload, n)) { free(buf); return -1; }
    rc = sink(user, buf, hdr + len);
    free(buf);
    if (rc) {                                             /* take the entry back out: nothing stored */
        uint32_t pos;
        if (find_log(c, k, &pos)) { free(c->ents[pos].payload); memmove(&c->ents[pos], &c->ents[pos + 1], (c->n_ents - pos - 1) * sizeof(lent)); c->n_ents--; }
        return -1;
    }
    c->info.log_valid_len += hdr + len; c->info.log_entries++;
    return 1;
}
