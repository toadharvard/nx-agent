/* shcache_key.c - key construction, the link-set rule and the payload container of shcache.h. */
#include "shcache.h"
#include <string.h>
#include "../translator/include/fnvnx_dk_layout.h"

uint64_t shcache_hash64(const void *p, size_t n)
{
    const uint8_t *b = (const uint8_t *)p; uint64_t h = 0xCBF29CE484222325ull;
    while (n--) { h ^= *b++; h *= 0x100000001B3ull; }
    return h;
}

uint64_t shcache_l_hash(const uint8_t *l, uint32_t n_l)
{
    uint64_t h;
    if (!n_l) return 0;
    h = shcache_hash64(l, n_l);
    return h ? h : 1;
}

/* sorts ascending into out, checks legality (usage 0..13, <= SHCACHE_MAX_L, no duplicate); 0 or -1 */
static int canon(const uint8_t *in, uint32_t n, uint8_t *out)
{
    uint32_t i, j;
    if (n > SHCACHE_MAX_L) return -1;
    for (i = 0; i < n; i++) {
        uint8_t v = in[i];
        if (v / 16 > 13) return -1;
        for (j = i; j > 0 && out[j - 1] > v; j--) out[j] = out[j - 1];
        out[j] = v;
    }
    for (i = 1; i < n; i++) if (out[i - 1] == out[i]) return -1;
    return 0;
}

int shcache_key_make(shcache_key *k, uint64_t code_hash, uint32_t code_len, int stage, uint32_t flags, const uint8_t *l, uint32_t n_l)
{
    memset(k, 0, sizeof *k);
    if ((stage != SHCACHE_STAGE_VERTEX && stage != SHCACHE_STAGE_PIXEL) || canon(l, n_l, k->l)) { memset(k, 0, sizeof *k); return -1; }
    k->code_hash = code_hash; k->code_len = code_len; k->stage = (uint8_t)stage; k->flags = flags & SHCACHE_FLAGS_MASK; k->n_l = (uint8_t)n_l;
    return 0;
}

/* Why this is total.  L is the union of the VS outputs (without the built-ins POSITION0 / PSIZE0, which live in gl_Position / gl_PointSize)
 * and the PS inputs: exactly the set both stages of the draw pass as link_pairs (fnvnx_dk_layout.h "Location rule"). The rule gives every
 * pair with a canonical slot (fnvnx_dk_varying_location >= 0) that slot whatever else is declared, so if EVERY pair of L is canonical each
 * stage's own declaration set yields the slots L would: the stage-alone translation is valid and the key's L part is empty. Any other
 * set contains a non-canonical pair (including a PS-read POSITION0 / PSIZE0, whose varying copy only exists when it is in L), whose slot
 * depends on the whole set: both stages are keyed by the canonical (sorted, unique) L. The two cases are exhaustive and decided by the set
 * alone (no run-time observation); the function is defined on all inputs: an illegal set (usage > 13, > 31 distinct pairs) returns -1,
 * which is the translator's rejection as well (fnvnx_dk_assign). */
int shcache_link_set(const uint8_t *vs_out, uint32_t n_vs, const uint8_t *ps_in, uint32_t n_ps, uint8_t *l, uint32_t *n_l)
{
    uint8_t u[2 * 224], s[SHCACHE_MAX_L]; uint32_t n = 0, i; int all_canonical = 1;
    for (i = 0; i < n_vs; i++) if (vs_out[i] != FNVNX_DK_PAIR(FNVNX_USAGE_POSITION, 0) && vs_out[i] != FNVNX_DK_PAIR(FNVNX_USAGE_PSIZE, 0)) { if (n == sizeof u) return -1; u[n++] = vs_out[i]; }
    for (i = 0; i < n_ps; i++) { if (n == sizeof u) return -1; u[n++] = ps_in[i]; }
    /* dedup (a pair may be declared by both stages), then legality */
    { uint32_t m = 0, j; uint8_t d[2 * 224];
      for (i = 0; i < n; i++) { for (j = 0; j < m && d[j] != u[i]; j++) ; if (j == m) d[m++] = u[i]; }
      memcpy(u, d, m); n = m; }
    for (i = 0; i < n; i++) { if (u[i] / 16 > 13) return -1; if (fnvnx_dk_varying_location(u[i] / 16, u[i] % 16) < 0) all_canonical = 0; }
    if (n > SHCACHE_MAX_L) return -1;
    if (all_canonical) { *n_l = 0; return 0; }
    if (canon(u, n, s)) return -1;
    memcpy(l, s, n); *n_l = n;
    return 0;
}

/* ---- reflection JSON: the exact shape emitted by frontend.c (strings with \" \\ escapes, integers) ---- */
static const char *skipws(const char *p) { while (*p == ' ' || *p == '\n' || *p == '\t' || *p == '\r') p++; return p; }
/* p at '"': copies the string (unescaped) into buf (cap incl. NUL, truncating), returns the char after the closing quote or NULL */
static const char *str(const char *p, char *buf, size_t cap)
{
    size_t o = 0;
    if (*p++ != '"') return NULL;
    while (*p && *p != '"') {
        if (*p == '\\' && p[1]) p++;
        if (o + 1 < cap) buf[o++] = *p;
        p++;
    }
    if (*p != '"') return NULL;
    if (cap) buf[o] = 0;
    return p + 1;
}
/* skips one JSON value of any kind (string, scalar, nested array/object); returns the char after it or NULL */
static const char *skipval(const char *p)
{
    char t[1]; int d = 0;
    p = skipws(p);
    if (*p == '"') return str(p, t, 0);
    if (*p == '[' || *p == '{') {
        do {
            if (*p == '"') { if (!(p = str(p, t, 0))) return NULL; continue; }
            if (*p == '[' || *p == '{') d++;
            else if (*p == ']' || *p == '}') d--;
            else if (!*p) return NULL;
            p++;
        } while (d);
        return p;
    }
    while (*p && *p != ',' && *p != ']' && *p != '}' && *p != ' ' && *p != '\n') p++;
    return p;
}

int shcache_refl_pairs(const char *json, const char *section, uint8_t *pairs, uint32_t cap)
{
    static const char *names[] = { "POSITION", "BLENDWEIGHT", "BLENDINDICES", "NORMAL", "PSIZE", "TEXCOORD", "TANGENT", "BINORMAL", "TESSFACTOR", "POSITIONT", "COLOR", "FOG", "DEPTH", "SAMPLE" };
    const char *p = skipws(json); char key[64]; uint32_t n = 0; int found = 0;
    if (*p++ != '{') return -1;
    for (;;) {                                          /* members of the root object */
        p = skipws(p);
        if (*p == '}') break;
        if (!(p = str(p, key, sizeof key))) return -1;
        p = skipws(p); if (*p++ != ':') return -1;
        p = skipws(p);
        if (strcmp(key, section) == 0 && *p == '[') {
            found = 1; p = skipws(p + 1);
            while (*p != ']') {                           /* objects of the array */
                char usage[32] = "", name[128] = ""; int idx = -1, k, u = -1;
                if (*p++ != '{') return -1;
                for (;;) {
                    char mk[32]; p = skipws(p);
                    if (!(p = str(p, mk, sizeof mk))) return -1;
                    p = skipws(p); if (*p++ != ':') return -1; p = skipws(p);
                    if (!strcmp(mk, "usage")) { if (!(p = str(p, usage, sizeof usage))) return -1; }
                    else if (!strcmp(mk, "name")) { if (!(p = str(p, name, sizeof name))) return -1; }
                    else if (!strcmp(mk, "index")) { int neg = *p == '-'; if (neg) p++; idx = 0; while (*p >= '0' && *p <= '9') idx = idx * 10 + (*p++ - '0'); if (neg) idx = -1; }
                    else if (!(p = skipval(p))) return -1;
                    p = skipws(p);
                    if (*p == ',') { p++; continue; }
                    if (*p++ == '}') break;
                    return -1;
                }
                for (k = 0; k < 14; k++) if (!strcmp(usage, names[k])) u = k;
                if (u >= 0 && !(u == FNVNX_USAGE_COLOR && strstr(name, "oC"))) {
                    if (idx < 0 || idx > 15 || n == cap) return -1;
                    pairs[n++] = FNVNX_DK_PAIR(u, idx);
                }
                p = skipws(p);
                if (*p == ',') p = skipws(p + 1);
            }
            p++;
        } else if (!(p = skipval(p))) return -1;
        p = skipws(p);
        if (*p == ',') p++;
    }
    return found ? (int)n : -1;
}

int shcache_link_key(const char *vs_json, const char *ps_json, uint8_t *l, uint32_t *n_l)
{
    uint8_t vo[224], pi[224]; int nv = shcache_refl_pairs(vs_json, "outputs", vo, sizeof vo), np = shcache_refl_pairs(ps_json, "inputs", pi, sizeof pi);
    if (nv < 0 || np < 0) return -1;
    return shcache_link_set(vo, (uint32_t)nv, pi, (uint32_t)np, l, n_l);
}

static void put32(uint8_t *o, uint32_t v) { o[0] = (uint8_t)v; o[1] = (uint8_t)(v >> 8); o[2] = (uint8_t)(v >> 16); o[3] = (uint8_t)(v >> 24); }
static uint32_t get32(const uint8_t *p) { return p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }

size_t shcache_payload_pack(const void *dksh, uint32_t dn, const char *json, uint32_t jn, uint8_t *out, size_t cap)
{
    size_t need = 8 + (size_t)dn + jn;
    if (cap < need) return 0;
    put32(out, dn); put32(out + 4, jn); memcpy(out + 8, dksh, dn); memcpy(out + 8 + dn, json, jn);
    return need;
}

int shcache_payload_unpack(const void *p, size_t n, const uint8_t **dksh, uint32_t *dn, const char **json, uint32_t *jn)
{
    const uint8_t *b = (const uint8_t *)p; uint32_t d, j;
    if (n < 8) return -1;
    d = get32(b); j = get32(b + 4);
    if ((uint64_t)d + j + 8 != n) return -1;
    *dksh = b + 8; *dn = d; *json = (const char *)b + 8 + d; *jn = j;
    return 0;
}
