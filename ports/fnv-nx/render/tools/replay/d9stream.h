/* d9stream.h: runtime of the D3D9 journaling wrapper: object registry (COM identity, ids), thread/depth bookkeeping, the D9S1 record
 * builder and the open-lock / taint / private-data state. Format: STREAM.md. No exceptions, RTTI or iostream; output goes to a sink. */
#ifndef D9STREAM_H
#define D9STREAM_H
#include "d9wrap_port.h"

typedef void (*D9Sink)(void *ctx, const void *p, size_t n);
struct D9Obj; struct D9SDesc;
struct D9Lk { uint32_t k0, k1; uintptr_t base; size_t pitch, rowbytes, rows, spitch, depth; int dc, onunlock, cls; uint32_t fmt, w, h;
              unsigned char *snap; size_t snaplen; D9Lk *next; };
struct D9Pd { unsigned char guid[16]; int iunk; D9Pd *next; };
struct D9Tv { uint32_t k0, k1; int v; D9Tv *next; };
struct D9W { void *w; D9Obj *o; D9W *hnext, *onext; void (*del)(void *); int iface; };
struct D9Obj { void *real; uint32_t id; int iface; D9Obj *hnext; D9W *ws; D9Obj *parent; uint32_t k0, k1; int tall; D9Tv *tv; D9Lk *locks; D9Pd *pds;
              D9Obj *kids, *knext; /* sub-resources handed out by this container (their Release is the container's) */ };
struct D9SField { uint8_t kind; uint16_t off, size, count; const D9SDesc *sub; };   /* kind 0 raw bytes, 1 token, 2 pad (zeros), 3 nested struct */
struct D9SDesc { const D9SField *f; unsigned n; };

/* supplied by the generated d9wrap.gen.cpp */
void *d9g_make(int iface, D9Obj *o, void (**del)(void *));   /* new W_<iface> around o */
bool d9g_isa(int sub, int base);
bool d9g_has_prop(int iface, int prop);
uint64_t d9g_prop(D9Obj *o, int prop);                       /* this.<prop> of the real object (queried from it) */
int d9g_level_idx(int iface);                                /* which of the lock-match key words is the mip level, -1 none */
int d9g_qtype_cls(uint32_t qtype);                           /* class of GetData for a D3DQUERYTYPE (0 det, 1 env, 2 pix) */
enum { D9P_LENGTH, D9P_FMT, D9P_WIDTH, D9P_HEIGHT, D9P_DEPTH, D9P_LEVELS, D9P_USAGE, D9P_POOL, D9P_SHADER_BYTES, D9P_DECL_BYTES, D9P_DATA_SIZE, D9P_QTYPE };

extern int d9s_trace;                                                 /* D9REC_TRACE=1: one stderr line before and after every Lock/Unlock/GetDC/ReleaseDC call (and its result) */
void d9s_tr(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
const char *d9s_boxs(const void *p, int n);                           /* "NULL" or the n int words of a RECT/D3DBOX, for the trace */
void d9s_open(D9Sink w, void *ctx, const unsigned char sha256[32]);   /* once, before the first wrapped call */
D9Obj *d9s_obj(const void *wrapper);                                  /* object of a wrapper; unknown non-NULL pointers become foreign */
void *d9s_real(const void *wrapper);
void *d9s_wrap_out(void *real, int iface);                            /* the unique wrapper of real for interface iface */
void d9s_child(void *childw, D9Obj *parent, uint32_t k0, uint32_t k1);
D9Lk *d9s_lock_add(D9Obj *o, const D9Lk &l);
D9Lk *d9s_lock_find(D9Obj *o, uint32_t k0, uint32_t k1);              /* a lock's identity is (container, k0, k1): see d9s_lock_root */
void d9s_lock_snap(D9Lk *l);                                          /* reads the extent now (before the real Unlock) */
void d9s_lock_del(D9Obj *o, D9Lk *l);
int d9s_taint(D9Obj *o, uint32_t k0, uint32_t k1);                    /* 1 = pix, 0 = det */
void d9s_pix(D9Obj *o);
void d9s_copy(D9Obj *dst, D9Obj *src);
void d9s_pd_set(D9Obj *o, const void *guid, int iunk);
void d9s_pd_free(D9Obj *o, const void *guid);
int d9s_pd_iunk(D9Obj *o, const void *guid);
int d9s_iid_iface(const void *guid);                                  /* interface index of an IID, -1 unknown (generated table) */
size_t d9s_image(const D9Lk &l, unsigned char *dst);                  /* packed bytes of a locked extent (dst may be NULL: size only) */

struct D9Rec {
    D9Obj *self; unsigned iface, slot, depth, thread; unsigned char *b; size_t n, cap; struct Fx { size_t off; int kind; uintptr_t v; } *fx; unsigned nfx, capfx;
    D9Obj **ret; unsigned nret, capret;   /* objects whose life ends with this call: the family of the released object */
    D9Rec(unsigned iface, unsigned slot, D9Obj *self);
    ~D9Rec();
    void put(const void *p, size_t k);   void pad();
    void w(uint32_t v);                  void hdr(unsigned cls, unsigned rec) { w(cls | (rec << 8)); }
    void raw(const void *p, size_t k);   void zeros(size_t k);   void buf(const void *p, size_t k);
    void obj(const void *wrapper);       void tok(uintptr_t raw);  void tokn(uintptr_t raw);   void tokd(uintptr_t raw);
    void canon(const void *p, size_t k, int as_handle);
    void st(const D9SDesc *d, const void *p);   /* pointee of a struct param */
    void img(const D9Lk &l);                    /* BUF of a locked extent, read now */
    void retire(D9Obj *o);
    void commit();                              /* ids, header, sink; may delete the receiver's wrappers (Release to 0) */
};
static inline uint32_t d9s_fbits(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }
/* the closed function vocabulary that needs no spec table */
static inline uint64_t d9x_div(uint64_t a, uint64_t b) { return b ? a / b : 0; }
static inline uint64_t d9x_lvl(uint64_t d, uint64_t l) { uint64_t v = l >= 32 ? 0 : d >> l; return v ? v : 1; }
static inline uint64_t d9x_u32(const void *p, uint64_t off) { uint32_t v; memcpy(&v, (const char *)p + off, 4); return v; }
static inline uint64_t d9x_rect_w(const void *r, uint64_t d) { const int32_t *q = (const int32_t *)r; return r ? (uint64_t)(int64_t)(q[2] - q[0]) : d; }
static inline uint64_t d9x_rect_h(const void *r, uint64_t d) { const int32_t *q = (const int32_t *)r; return r ? (uint64_t)(int64_t)(q[3] - q[1]) : d; }
static inline uint64_t d9x_box_w(const void *r, uint64_t d) { const uint32_t *q = (const uint32_t *)r; return r ? (uint64_t)(q[2] - q[0]) : d; }
static inline uint64_t d9x_box_h(const void *r, uint64_t d) { const uint32_t *q = (const uint32_t *)r; return r ? (uint64_t)(q[3] - q[1]) : d; }
static inline uint64_t d9x_box_d(const void *r, uint64_t d) { const uint32_t *q = (const uint32_t *)r; return r ? (uint64_t)(q[5] - q[4]) : d; }
static inline uint64_t d9x_str_bytes(const void *p) { const char *s = (const char *)p; size_t i = 0; while (s[i]) i++; return i + 1; }
static inline uint64_t d9x_wstr_bytes(const void *p) { const uint16_t *s = (const uint16_t *)p; size_t i = 0; while (s[i]) i++; return 2 * (i + 1); }
static inline uint64_t d9x_decl_bytes(const void *p) { const uint16_t *s = (const uint16_t *)p; size_t i = 0; while (s[4 * i] != 0xFF) i++; return 8 * (i + 1); }
#endif
