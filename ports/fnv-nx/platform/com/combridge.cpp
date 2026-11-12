/* combridge.cpp - faces and proxies (the model is described in combridge.h). The tables come from combridge.gen.cpp.
 *
 * Records are never freed. A Face is a pure function of its key (the interface pointer of the object, the interface) and a
 * ProxyRec of (the game's pointer, the interface): neither holds state that outlives a reference count, so a record that
 * outlives its object is harmless (the same address again is the same record, and a proxy reads the vtable of the game object
 * at every call). Freeing them would break the one thing COM allows a callee to do with a pointer it was only lent: keep it
 * unreferenced for as long as the other object lives (the weak graph pointer of JoinFilterGraph). */
#include "combridge.h"

#include "../include/fnvnx_boundary.h"
#include "../include/fnvnx_guestfn.h"
#include "../os/os.h"

#include <stdlib.h>
#include <string.h>

namespace nxcom {
namespace {

os_lock_t g_lock = {0};
unsigned g_init_state;   /* 0: not run, 1: running, 2: done */
unsigned g_missing;

enum { NBUCKET = 1024 };
Face *g_faces[NBUCKET];            /* by (obj, iface) */
ProxyRec *g_by_gp[NBUCKET];        /* by (gp, iface) */
ProxyRec *g_by_obj[NBUCKET];       /* by the proxy object */

inline unsigned hash2(const void *p, unsigned iface) { return (unsigned)(((uintptr_t)p >> 3) * 2654435761u + iface * 40503u) % NBUCKET; }
inline unsigned hash1(const void *p) { return hash2(p, 0); }

ProxyRec *px_find_gp(const void *gp, unsigned iface)
{
    for (ProxyRec *r = g_by_gp[hash2(gp, iface)]; r; r = r->next_gp)
        if (r->gp == gp && r->iface == iface) return r;
    return nullptr;
}
ProxyRec *px_find_obj(const void *obj)
{
    for (ProxyRec *r = g_by_obj[hash1(obj)]; r; r = r->next_obj)
        if (r->obj == obj) return r;
    return nullptr;
}
/* the proxy of (gp, iface), made when there is none; lock held; null when out of memory or when the interface has no proxy */
ProxyRec *px_get(void *gp, unsigned iface)
{
    if (ProxyRec *r = px_find_gp(gp, iface)) return r;
    if (!g_tables.ifaces[iface].make_proxy) return nullptr;   /* an interface whose methods are wider than a boundary call */
    ProxyRec *r = (ProxyRec *)calloc(1, sizeof *r);
    if (!r) return nullptr;
    r->gp = gp;
    r->iface = iface;
    r->obj = g_tables.ifaces[iface].make_proxy(r);
    if (!r->obj) { free(r); return nullptr; }
    unsigned a = hash2(gp, iface), b = hash1(r->obj);
    r->next_gp = g_by_gp[a];
    g_by_gp[a] = r;
    r->next_obj = g_by_obj[b];
    g_by_obj[b] = r;
    return r;
}
/* the face of (obj, iface), made when there is none; lock held */
Face *face_get(void *obj, unsigned iface)
{
    unsigned h = hash2(obj, iface);
    for (Face *f = g_faces[h]; f; f = f->next)
        if (f->obj == obj && f->iface == iface) return f;
    Face *f = (Face *)calloc(1, sizeof *f);
    if (!f) return nullptr;
    f->vt = g_tables.keys + g_tables.ifaces[iface].keyoff;
    f->obj = obj;
    f->iface = iface;
    f->next = g_faces[h];
    g_faces[h] = f;
    return f;
}

void game_addref(void *gp) { fnvnx_guest_com_call<unsigned long>(gp, 1); }
void game_release(void *gp) { fnvnx_guest_com_call<unsigned long>(gp, 2); }

bool is_face_vt(const uintptr_t *vt, unsigned *iface)
{
    const Tables &t = g_tables;
    if (vt < t.keys || vt >= t.keys + t.nkeys) return false;
    unsigned off = (unsigned)(vt - t.keys);
    for (unsigned i = 0; i < t.niface; i++)
        if (!t.ifaces[i].hidden && t.ifaces[i].keyoff == off) { *iface = i; return true; }
    return false;
}

}   // namespace

unsigned nxcom_iface_count() { return g_tables.niface; }

unsigned live_faces()
{
    unsigned n = 0;
    os_lock(&g_lock);
    for (unsigned i = 0; i < NBUCKET; i++)
        for (Face *f = g_faces[i]; f; f = f->next) n += f->refs > 0;
    os_unlock(&g_lock);
    return n;
}
unsigned live_proxies()
{
    unsigned n = 0;
    os_lock(&g_lock);
    for (unsigned i = 0; i < NBUCKET; i++)
        for (ProxyRec *r = g_by_gp[i]; r; r = r->next_gp) n += r->prefs > 0;
    os_unlock(&g_lock);
    return n;
}

unsigned init()
{
    if (__atomic_load_n(&g_init_state, __ATOMIC_ACQUIRE) == 2) return g_missing;
    os_lock(&g_lock);
    if (g_init_state == 0) {
        g_init_state = 1;
        gen_init();
        unsigned missing = 0;
        for (unsigned i = 0; i < g_tables.niface; i++) {
            const Iface &f = g_tables.ifaces[i];
            if (f.hidden) continue;
            char dll[96];
            size_t n = strlen(f.name);
            dll[0] = '!';
            memcpy(dll + 1, f.name, n + 1);
            for (unsigned s = 0; s < f.nslots; s++) {
                g_tables.keys[f.keyoff + s] = (uintptr_t)fnvnx_bnd_export_value(dll, f.names[s], s, f.thunks[s]);
                if (!g_tables.keys[f.keyoff + s]) missing++;
            }
        }
        g_missing = missing;
        __atomic_store_n(&g_init_state, 2, __ATOMIC_RELEASE);
    }
    os_unlock(&g_lock);
    return g_missing;
}

bool iface_is(unsigned have, unsigned want)
{
    for (int i = (int)have; i >= 0; i = g_tables.ifaces[i].base)
        if ((unsigned)i == want || g_tables.ifaces[want].alias == i) return true;
    return false;
}

int iface_for_iid(const uint8_t canon[16], unsigned family)
{
    init();
    int any = -1;
    for (unsigned i = 0; i < g_tables.niid; i++) {
        if (memcmp(g_tables.iids[i].bytes, canon, 16)) continue;
        unsigned f = g_tables.iids[i].iface;
        if (family && g_tables.ifaces[f].family == family) return (int)f;
        if (any < 0) any = (int)f;
    }
    return any;
}

Face *face_from(const void *gp)
{
    if (!gp) return nullptr;
    init();
    unsigned iface;
    const uintptr_t *vt = *(const uintptr_t *const *)gp;
    if (!is_face_vt(vt, &iface)) return nullptr;
    return (Face *)gp;
}

void *to_game(void *obj, unsigned iface)
{
    if (!obj) return nullptr;
    init();
    os_lock(&g_lock);
    if (ProxyRec *r = px_find_obj(obj)) {   /* a game object on its way back: the game gets its own pointer */
        void *gp = r->gp;
        bool more = --r->prefs > 0;
        os_unlock(&g_lock);
        if (more) game_addref(gp);   /* the platform still holds the reference it had; the game gets one of its own */
        return gp;
    }
    Face *f = face_get(obj, iface);
    if (f) f->refs++;
    os_unlock(&g_lock);
    return f;
}

void *borrow_to_game(void *obj, unsigned iface)
{
    if (!obj) return nullptr;
    init();
    os_lock(&g_lock);
    if (ProxyRec *r = px_find_obj(obj)) {
        void *gp = r->gp;
        os_unlock(&g_lock);
        return gp;
    }
    Face *f = face_get(obj, iface);
    os_unlock(&g_lock);
    return f;
}

void *from_game(const void *gp, unsigned iface)
{
    if (!gp) return nullptr;
    init();
    Face *f = face_from(gp);
    if (f && iface_is(f->iface, iface)) return f->obj;
    os_lock(&g_lock);
    ProxyRec *r = px_get((void *)gp, iface);
    os_unlock(&g_lock);
    return r ? r->obj : nullptr;
}

void *adopt_game(void *gp, unsigned iface)
{
    if (!gp) return nullptr;
    init();
    Face *f = face_from(gp);
    if (f && iface_is(f->iface, iface)) {   /* the game handed back a platform object: its reference becomes the platform's */
        void *obj = f->obj;
        face_unref(f);
        return obj;
    }
    os_lock(&g_lock);
    ProxyRec *r = px_get(gp, iface);
    bool surplus = false;
    if (r) {
        surplus = r->prefs > 0;   /* the proxy already holds a reference of the game object: this one is one too many */
        r->prefs++;
    }
    os_unlock(&g_lock);
    if (!r) { game_release(gp); return nullptr; }   /* no proxy can be made: the reference of the game object goes back */
    if (surplus) game_release(gp);
    return r->obj;
}

void face_ref(Face *f)
{
    os_lock(&g_lock);
    f->refs++;
    os_unlock(&g_lock);
}
void face_unref(Face *f)
{
    os_lock(&g_lock);
    if (f->refs > 0) f->refs--;
    os_unlock(&g_lock);
}

unsigned long px_addref(ProxyRec *r)
{
    os_lock(&g_lock);
    int was = r->prefs++;
    os_unlock(&g_lock);
    if (!was) game_addref(r->gp);
    return (unsigned long)(was + 1);
}
unsigned long px_release(ProxyRec *r)
{
    os_lock(&g_lock);
    int before = r->prefs;
    int n = before > 0 ? --r->prefs : 0;
    bool last = before == 1;
    os_unlock(&g_lock);
    if (last) game_release(r->gp);
    return (unsigned long)n;
}
int32_t px_qi(ProxyRec *r, const uint8_t canon[16], const void *riid, void **out, unsigned family)
{
    if (!out) return (int32_t)0x80004003;   /* E_POINTER */
    *out = nullptr;
    int ix = iface_for_iid(canon, family);
    if (ix < 0) return (int32_t)0x80004002;   /* E_NOINTERFACE: the platform consumes only the interfaces of its spec */
    int32_t hr = fnvnx_guest_com_call<int32_t>(r->gp, 0, riid, (void **)out);
    if (hr >= 0 && *out) {
        *out = adopt_game(*out, (unsigned)ix);
        if (!*out) return (int32_t)0x80004002;   /* E_NOINTERFACE: no proxy exists for the interface */
    }
    return hr;
}

}   // namespace nxcom
