/* d9stream.cpp: runtime of the D3D9 journaling wrapper (see d9stream.h, STREAM.md). One spin lock (d9wrap_port.h) guards the registry,
 * the id counters and record emission; it is never held while calling into the real runtime or an application callback. */
#include "d9stream.h"
#include <stdio.h>
#include <stdarg.h>

#define HASH 4096
static D9Lock g_lock; static D9Sink g_sink; static void *g_ctx; static uint32_t g_seq, g_nid, g_ntok, g_nthr;
static D9Obj *g_objs[HASH]; static D9W *g_ws[HASH];
struct Tn { uintptr_t raw; uint32_t id; Tn *next; };
static Tn *g_toks[HASH];
static D9W_TLS unsigned tl_thread, tl_depth;
static unsigned hp(uintptr_t p) { return (unsigned)((p >> 2) * 2654435761u >> 7) % HASH; }

static D9Obj *find_real(void *r) { for (D9Obj *o = g_objs[hp((uintptr_t)r)]; o; o = o->hnext) if (o->real == r) return o; return 0; }
static void link_w(D9Obj *o, void *w, void (*del)(void *), int iface) {
    D9W *n = (D9W *)calloc(1, sizeof *n); n->w = w; n->o = o; n->del = del; n->iface = iface;
    unsigned h = hp((uintptr_t)w); n->hnext = g_ws[h]; g_ws[h] = n;
    D9W **t = &o->ws; while (*t) t = &(*t)->onext; *t = n;
}
static D9Obj *adopt(void *real, int iface) {       /* lock held */
    D9Obj *o = find_real(real); if (o) return o;
    o = (D9Obj *)calloc(1, sizeof *o); o->real = real; o->iface = iface; o->tall = -1;
    unsigned h = hp((uintptr_t)real); o->hnext = g_objs[h]; g_objs[h] = o;
    if (iface < 0) link_w(o, real, 0, -1);
    return o;
}
static void unlink_w(D9W *n) { D9W **t = &g_ws[hp((uintptr_t)n->w)]; while (*t != n) t = &(*t)->hnext; *t = n->hnext; }
static void free_obj(D9Obj *o) {                    /* lock held. A container takes its sub-resources with it; a sub-resource leaves its container's list */
    for (D9Obj *k = o->kids, *nx; k; k = nx) { nx = k->knext; k->parent = 0; free_obj(k); }
    if (o->parent) { D9Obj **t = &o->parent->kids; while (*t && *t != o) t = &(*t)->knext; if (*t) *t = o->knext; }
    D9Obj **t = &g_objs[hp((uintptr_t)o->real)]; while (*t != o) t = &(*t)->hnext; *t = o->hnext;
    for (D9W *n = o->ws, *nx; n; n = nx) { nx = n->onext; unlink_w(n); if (n->del) n->del(n->w); free(n); }
    for (D9Lk *l = o->locks, *nx; l; l = nx) { nx = l->next; free(l->snap); free(l); }
    for (D9Pd *p = o->pds, *nx; p; p = nx) { nx = p->next; free(p); }
    for (D9Tv *p = o->tv, *nx; p; p = nx) { nx = p->next; free(p); }
    free(o);
}
D9Obj *d9s_obj(const void *wr) {
    if (!wr) return 0;
    d9port_lock(&g_lock);
    D9W *n = g_ws[hp((uintptr_t)wr)]; while (n && n->w != wr) n = n->hnext;
    D9Obj *o = n ? n->o : adopt((void *)wr, -1);
    d9port_unlock(&g_lock); return o;
}
void *d9s_real(const void *wr) { D9Obj *o = d9s_obj(wr); return o ? o->real : 0; }
void *d9s_wrap_out(void *real, int iface) {
    if (!real) return 0;
    d9port_lock(&g_lock);
    D9Obj *o = adopt(real, iface); void *res = 0;
    if (o->iface < 0 && iface >= 0) { for (D9W *n = o->ws; n; n = o->ws) { o->ws = n->onext; unlink_w(n); free(n); } o->iface = iface; }
    if (o->iface < 0) res = real;
    else for (D9W *n = o->ws; n && !res; n = n->onext) if (d9g_isa(n->iface, iface)) res = n->w;
    if (!res) { void (*del)(void *) = 0; res = d9g_make(iface, o, &del); link_w(o, res, del, iface); }
    d9port_unlock(&g_lock); return res;
}
void d9s_child(void *cw, D9Obj *parent, uint32_t k0, uint32_t k1) {
    D9Obj *c = d9s_obj(cw); if (!c || c == parent || c->parent) return;
    d9port_lock(&g_lock); c->parent = parent; c->k0 = k0; c->k1 = k1; c->knext = parent->kids; parent->kids = c; d9port_unlock(&g_lock);
}

/* A sub-resource is identified by (container, k0, k1) however it is reached: texture->LockRect(level) and surface(level)->LockRect are the same lock,
 * so the open-lock table lives on the container and a sub-resource's own key words replace the (0,0) its method passes. */
static D9Obj *lock_root(D9Obj *o, uint32_t *k0, uint32_t *k1) { if (o->parent) { *k0 = o->k0; *k1 = o->k1; return o->parent; } return o; }
D9Lk *d9s_lock_add(D9Obj *o, const D9Lk &l) {
    D9Lk *n = (D9Lk *)malloc(sizeof *n); *n = l; n->snap = 0; n->snaplen = 0; o = lock_root(o, &n->k0, &n->k1);
    d9port_lock(&g_lock); n->next = o->locks; o->locks = n; d9port_unlock(&g_lock); return n;
}
D9Lk *d9s_lock_find(D9Obj *o, uint32_t k0, uint32_t k1) {
    o = lock_root(o, &k0, &k1); d9port_lock(&g_lock); D9Lk *l = o->locks; while (l && !(l->k0 == k0 && l->k1 == k1)) l = l->next; d9port_unlock(&g_lock); return l;
}
size_t d9s_image(const D9Lk &l, unsigned char *dst) {
    size_t n = l.rows * l.rowbytes * l.depth; if (!dst || !n) return n;
    if (l.dc) { d9port_dc_pixels((void *)l.base, l.fmt, l.w, l.h, dst, n); return n; }
    for (size_t d = 0; d < l.depth; d++) for (size_t r = 0; r < l.rows; r++)
        memcpy(dst + (d * l.rows + r) * l.rowbytes, (const char *)l.base + d * l.spitch + r * l.pitch, l.rowbytes);
    return n;
}
void d9s_lock_snap(D9Lk *l) { l->snaplen = d9s_image(*l, 0); l->snap = (unsigned char *)malloc(l->snaplen + 1); d9s_image(*l, l->snap); }
void d9s_lock_del(D9Obj *o, D9Lk *l) {
    uint32_t k0, k1; o = lock_root(o, &k0, &k1); d9port_lock(&g_lock); D9Lk **t = &o->locks; while (*t != l) t = &(*t)->next; *t = l->next; d9port_unlock(&g_lock); free(l->snap); free(l);
}

static int initial_taint(D9Obj *r, uint32_t k0, uint32_t k1) {      /* no lock held: queries the real object */
    if (!d9g_has_prop(r->iface, D9P_USAGE)) return 0;
    uint64_t u = d9g_prop(r, D9P_USAGE); int li = d9g_level_idx(r->iface); uint32_t lv = li < 0 ? 0 : (li ? k1 : k0);
    return (u & (D3DUSAGE_RENDERTARGET | D3DUSAGE_DEPTHSTENCIL)) != 0 || ((u & D3DUSAGE_AUTOGENMIPMAP) && lv >= 1);
}
int d9s_taint(D9Obj *o, uint32_t k0, uint32_t k1) {
    D9Obj *r = o->parent ? o->parent : o; if (o->parent) { k0 = o->k0; k1 = o->k1; }
    int ini = initial_taint(r, k0, k1); d9port_lock(&g_lock);
    int v = r->tall >= 0 ? r->tall : ini; for (D9Tv *t = r->tv; t; t = t->next) if (t->k0 == k0 && t->k1 == k1) { v = t->v; break; }
    d9port_unlock(&g_lock); return v;
}
static void set_tv(D9Obj *o, int v) {                                // lock held
    D9Obj *r = o->parent ? o->parent : o;
    if (o->parent) { D9Tv *t = r->tv; while (t && !(t->k0 == o->k0 && t->k1 == o->k1)) t = t->next;
                     if (!t) { t = (D9Tv *)calloc(1, sizeof *t); t->k0 = o->k0; t->k1 = o->k1; t->next = r->tv; r->tv = t; } t->v = v; return; }
    for (D9Tv *t = r->tv, *nx; t; t = nx) { nx = t->next; free(t); }
    r->tv = 0; r->tall = v;
}
void d9s_pix(D9Obj *o) { if (!o) return; d9port_lock(&g_lock); set_tv(o, 1); d9port_unlock(&g_lock); }
void d9s_copy(D9Obj *dst, D9Obj *src) {                              /* dst := taint of src (whole object when src is a container) */
    if (!dst || !src) return;
    int v = d9s_taint(src, 0, 0);
    if (!src->parent) { int li = d9g_level_idx(src->iface); if (li >= 0) v |= initial_taint(src, li ? 0 : 1, li ? 1 : 0);
                        d9port_lock(&g_lock); for (D9Tv *t = src->tv; t; t = t->next) v |= t->v; d9port_unlock(&g_lock); }
    d9port_lock(&g_lock); set_tv(dst, v); d9port_unlock(&g_lock);
}
void d9s_pd_set(D9Obj *o, const void *g, int iunk) {
    d9port_lock(&g_lock); D9Pd *p = o->pds; while (p && memcmp(p->guid, g, 16)) p = p->next;
    if (!p) { p = (D9Pd *)calloc(1, sizeof *p); memcpy(p->guid, g, 16); p->next = o->pds; o->pds = p; } p->iunk = iunk; d9port_unlock(&g_lock);
}
void d9s_pd_free(D9Obj *o, const void *g) {
    d9port_lock(&g_lock); for (D9Pd **t = &o->pds; *t; t = &(*t)->next) if (!memcmp((*t)->guid, g, 16)) { D9Pd *d = *t; *t = d->next; free(d); break; }
    d9port_unlock(&g_lock);
}
int d9s_pd_iunk(D9Obj *o, const void *g) {
    d9port_lock(&g_lock); D9Pd *p = o->pds; while (p && memcmp(p->guid, g, 16)) p = p->next; int v = p ? p->iunk : 0; d9port_unlock(&g_lock); return v;
}

int d9s_trace;
void d9s_tr(const char *fmt, ...) { char b[512]; va_list ap; va_start(ap, fmt); int n = vsnprintf(b, sizeof b - 1, fmt, ap); va_end(ap); if (n < 0) return; if (n > (int)sizeof b - 2) n = sizeof b - 2; b[n++] = '\n'; fwrite(b, 1, n, stderr); fflush(stderr); }
const char *d9s_boxs(const void *p, int n) {
    static D9W_TLS char b[96]; if (!p) return "NULL"; const int32_t *q = (const int32_t *)p; int k = 0;
    for (int i = 0; i < n && k < 80; i++) k += snprintf(b + k, sizeof b - k, i ? ",%d" : "%d", (int)q[i]);
    return b;
}
void d9s_open(D9Sink w, void *ctx, const unsigned char sha[32]) {
    unsigned char h[44]; uint32_t one = 1; g_sink = w; g_ctx = ctx;
    memcpy(h, "D9S1", 4); memcpy(h + 4, &one, 4); memcpy(h + 8, sha, 32); memset(h + 40, 0, 4); w(ctx, h, 44);
}

D9Rec::D9Rec(unsigned i, unsigned s, D9Obj *o) : self(o), iface(i), slot(s), b(0), n(0), cap(0), fx(0), nfx(0), capfx(0), ret(0), nret(0), capret(0) {
    if (!tl_thread) { d9port_lock(&g_lock); tl_thread = ++g_nthr; d9port_unlock(&g_lock); }
    thread = tl_thread - 1; depth = tl_depth++;
}
D9Rec::~D9Rec() { free(b); free(fx); free(ret); }
void D9Rec::put(const void *p, size_t k) {
    if (n + k > cap) { cap = (n + k) * 2 + 256; b = (unsigned char *)realloc(b, cap); }
    memcpy(b + n, p, k); n += k;
}
void D9Rec::pad() { static const char z[4] = {0, 0, 0, 0}; if (n & 3) put(z, 4 - (n & 3)); }
void D9Rec::w(uint32_t v) { put(&v, 4); }
void D9Rec::raw(const void *p, size_t k) { put(p, k); pad(); }
void D9Rec::zeros(size_t k) { static const char z[16] = {0}; while (k) { size_t c = k < 16 ? k : 16; put(z, c); k -= c; } pad(); }
void D9Rec::buf(const void *p, size_t k) { w((uint32_t)k); raw(p, k); }
static void addfx(D9Rec *r, int kind, uintptr_t v) {
    if (r->nfx == r->capfx) { r->capfx = r->capfx * 2 + 16; r->fx = (D9Rec::Fx *)realloc(r->fx, r->capfx * sizeof *r->fx); }
    r->fx[r->nfx].off = r->n; r->fx[r->nfx].kind = kind; r->fx[r->nfx].v = v; r->nfx++; r->w(0);
}
void D9Rec::obj(const void *wr) { D9Obj *o = d9s_obj(wr); if (o) addfx(this, 0, (uintptr_t)o); else w(0); }
void D9Rec::tok(uintptr_t raw) { if (raw) addfx(this, 1, raw); else w(0); }
void D9Rec::tokn(uintptr_t raw) { if (raw) addfx(this, 2, raw); else w(0); }
void D9Rec::tokd(uintptr_t raw) { if (raw) addfx(this, 3, raw); else w(0); }
void D9Rec::canon(const void *p, size_t k, int as_handle) {
    const unsigned char *c = (const unsigned char *)p; size_t i = 0;
    w((uint32_t)k);
    for (; i + 4 <= k; i += 4) { uint32_t v; memcpy(&v, c + i, 4); if (as_handle) obj((const void *)(uintptr_t)v); else tok(v); }
    if (k > i) put(c + i, k - i);
    pad();
}
static void stf(D9Rec *r, const D9SDesc *d, const unsigned char *p) {
    for (unsigned i = 0; i < d->n; i++) { const D9SField &f = d->f[i]; const unsigned char *q = p + f.off;
        switch (f.kind) {
        case 0: r->put(q, f.size); break;
        case 1: { uint32_t v; memcpy(&v, q, 4); r->tok(v); break; }
        case 2: { unsigned char z[1] = {0}; for (unsigned k = 0; k < f.size; k++) r->put(z, 1); break; }
        default: for (unsigned k = 0; k < f.count; k++) stf(r, f.sub, q + k * (f.size / f.count)); } }
}
void D9Rec::st(const D9SDesc *d, const void *p) { stf(this, d, (const unsigned char *)p); pad(); }
void D9Rec::img(const D9Lk &l) {
    size_t k = d9s_image(l, 0); unsigned char *t = (unsigned char *)malloc(k + 1); d9s_image(l, t); buf(t, k); free(t);
}
static void ret_add(D9Rec *r, D9Obj *o) {
    for (unsigned i = 0; i < r->nret; i++) if (r->ret[i] == o) return;
    if (r->nret == r->capret) { r->capret = r->capret * 2 + 8; r->ret = (D9Obj **)realloc(r->ret, r->capret * sizeof *r->ret); }
    r->ret[r->nret++] = o;
}
/* Release returned 0: the object is gone. A level/face surface or volume shares its container's reference count (a Release on it returns the container's),
 * so its final Release is the container's, and the container's is that of all its levels: the whole family dies in this record and nothing keeps a
 * stale registry entry (a stale entry would be found again when the runtime reuses the pointer, with a dangling container). */
void D9Rec::retire(D9Obj *o) {
    d9port_lock(&g_lock); D9Obj *root = o->parent ? o->parent : o; ret_add(this, root); for (D9Obj *k = root->kids; k; k = k->knext) ret_add(this, k); d9port_unlock(&g_lock);
}
void D9Rec::commit() {
    d9port_lock(&g_lock);
    uint32_t thisid = 0, *nl = (uint32_t *)malloc(8 * (nfx + 2 + nret)), *rl = (uint32_t *)malloc(4 * (nret + 1)); unsigned nn = 0;
    #define NEWID(o) do { if (!(o)->id) { (o)->id = ++g_nid; nl[2 * nn] = (o)->id; nl[2 * nn + 1] = (o)->iface < 0 ? 0xFFFF : (uint32_t)(o)->iface; nn++; } } while (0)
    if (self) { NEWID(self); thisid = self->id; }
    for (unsigned i = 0; i < nfx; i++) {
        uint32_t id = 0;
        if (fx[i].kind == 0) { D9Obj *o = (D9Obj *)fx[i].v; NEWID(o); id = o->id; }
        else {
            Tn **t = &g_toks[hp(fx[i].v)]; while (*t && (*t)->raw != fx[i].v) t = &(*t)->next;
            if (*t && fx[i].kind == 2) { Tn *d = *t; *t = d->next; free(d); t = &g_toks[hp(fx[i].v)]; while (*t) t = &(*t)->next; }
            if (!*t) { Tn *a = (Tn *)calloc(1, sizeof *a); a->raw = fx[i].v; a->id = ++g_ntok; *t = a; }
            id = (*t)->id;
            if (fx[i].kind == 3) { Tn *d = *t; *t = d->next; free(d); }
        }
        memcpy(b + fx[i].off, &id, 4);
    }
    unsigned nr = 0;   /* ids retired by this call: the family of the released object; a member the stream never named dies silently (it has no id) */
    for (unsigned i = 0; i < nret; i++) { if (ret[i] == self) NEWID(ret[i]); if (ret[i]->id) rl[nr++] = ret[i]->id; }
    for (unsigned i = 1; i < nr; i++) { uint32_t v = rl[i]; unsigned j = i; while (j && rl[j - 1] > v) { rl[j] = rl[j - 1]; j--; } rl[j] = v; }
    size_t tot = 4 * (8 + 2 * nn + nr) + n; unsigned char *o = (unsigned char *)malloc(tot); uint32_t *q = (uint32_t *)o;
    q[0] = (uint32_t)tot; q[1] = g_seq++; q[2] = thread; q[3] = (iface & 0xFFFF) | (slot << 16); q[4] = thisid; q[5] = depth; q[6] = nn;
    memcpy(q + 7, nl, 8 * nn); q += 7 + 2 * nn; *q++ = nr; memcpy(q, rl, 4 * nr); q += nr; if (n) memcpy(q, b, n);
    g_sink(g_ctx, o, tot);
    unsigned nroot = 0;   /* roots first: free_obj of a container frees its sub-resources, which must not be touched afterwards */
    for (unsigned i = 0; i < nret; i++) if (!ret[i]->parent) ret[nroot++] = ret[i];
    for (unsigned i = 0; i < nroot; i++) free_obj(ret[i]);
    d9port_unlock(&g_lock);
    free(o); free(nl); free(rl); tl_depth--;
}
