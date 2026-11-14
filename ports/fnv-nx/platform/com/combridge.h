/* combridge.h - the guest face of the COM objects of the platform and the proxies of the COM objects of the game.
 *
 * The game calls a COM method as `call [[obj] + 4 * slot]`; the dispatcher that runs the call maps the 32-bit value found in
 * the table to an entry, so a table the game reads holds IMPORT KEYS (fnvnx_bnd_export_value, fnvnx_boundary.h), never native
 * code addresses. The platform's own objects are C++ objects with native vtables, so they never reach the game directly:
 *
 *   face      a platform object (an interface pointer of it, `obj`, seen as interface `iface`) is given to the game as a Face: a
 *             small record whose FIRST WORD is the address of the key table of the interface (g_tables.keys + keyoff). The key of
 *             slot s is the key of the thunk nxb_<Interface>_<Method> (generated: gen_combridge.py), which takes the x86 stack
 *             words (this = the face first), converts every interface pointer at the boundary and calls the C++ method.
 *             Faces are per (obj, iface): the same interface pointer of an object always gets the same face, so COM identity
 *             (QueryInterface(IID_IUnknown) of one object) is the C++ object's own. face.refs counts the references the GAME
 *             holds through this face; the object's reference count stays the object's: AddRef/Release thunks forward to it.
 *   proxy     an interface pointer that arrives from the game and is not the face of a platform object is a game object. The
 *             platform keeps calling C++ interfaces: the generated class Px_<Interface> implements the interface, every method
 *             being a call through fnvnx_guest_com_call (fnvnx_guestfn.h) on the game object. One proxy per (game object,
 *             interface). The proxy holds ONE reference of the game object while the platform holds any reference of the proxy.
 *
 * Which of the three a pointer is decided by the pointer alone: its first word is a key table of this file (face) or it is not.
 * Reference rules (a pointer crosses with the reference it carries):
 *   to_game(obj)    the caller hands over one reference of obj (the one a C++ method returned): it becomes a reference of the game
 *                   (face.refs++); a proxy goes back as the game's own pointer.
 *   from_game(gp)   a borrowed pointer (an argument): the C++ object, or the proxy of the game object (no reference moves);
 *   adopt_game(gp)  a pointer the game returned with its reference (an out parameter of a game method): the platform now owns it.
 * Faces and proxies are never freed (combridge.cpp says why). The table of interfaces, IIDs and key slots is generated (combridge.gen.cpp) from com_spec.json. */
#ifndef FNVNX_COMBRIDGE_H
#define FNVNX_COMBRIDGE_H
#include <stddef.h>
#include <stdint.h>
#include <type_traits>

#ifdef __cplusplus
namespace nxcom {

struct ProxyRec;
struct Iface {
    const char *name;
    unsigned nslots;
    unsigned keyoff;            /* index of slot 0 in Tables::keys */
    int base;                   /* the interface this one derives from (an `IBaseFilter` pointer is an `IMediaFilter`), -1: none */
    bool hidden;                /* has no key table and no rows: dsh::Unk, the DirectShow spelling of IUnknown (its proxy exists) */
    int alias;                  /* an interface a pointer of which is also a pointer of this one (Unk: IUnknownCom), -1: none */
    unsigned family;            /* 1: GUID-typed IID arguments (DirectSound, DirectInput), 2: raw 16-byte IIDs (DirectShow) */
    const char *const *names;   /* the method names by slot */
    const void *const *thunks;  /* nxb_<Interface>_<Method> by slot (the native entry the key stands for) */
    void *(*make_proxy)(ProxyRec *);
    void (*free_proxy)(void *);
};
struct Iid {
    uint8_t bytes[16];          /* canonical: data1 (LE), data2, data3 (LE), data4 */
    unsigned iface;
};
struct Tables {
    unsigned niface;
    const Iface *ifaces;
    unsigned nkeys;
    uintptr_t *keys;
    unsigned niid;
    const Iid *iids;
};
extern const Tables g_tables;   /* generated */
unsigned nxcom_iface_count();
void gen_init();               /* generated: the IID table from the C++ constants */
unsigned selfcheck();          /* generated: the number of methods whose vtable slot differs from the spec (0) */

struct Face {
    const uintptr_t *vt;        /* FIRST WORD: the key table the game reads */
    void *obj;                  /* the interface pointer of the platform object (type: the cxx class of `iface`) */
    unsigned iface;
    int refs;                   /* references the game holds through this face */
    Face *next;
};
struct ProxyRec {
    void *gp;                   /* the game's interface pointer */
    unsigned iface;
    int prefs;                  /* references the platform holds of the proxy; the game object holds one while > 0 */
    void *obj;                  /* the proxy object (the generated class), what the platform calls */
    ProxyRec *next_gp, *next_obj;
};

/* Fills the key tables from the import table (fnvnx_bnd_export_value); returns the number of rows the import table lacks (0
 * in a build whose bridge table contains combridge_rows.tsv). Idempotent; called by every entry point below. */
unsigned init();

Face *face_from(const void *gp);                 /* gp is a face of this file: it, else null */
void *to_game(void *obj, unsigned iface);        /* see the header comment */
void *borrow_to_game(void *obj, unsigned iface);   /* an argument the platform passes to a game method: no reference moves */
void *from_game(const void *gp, unsigned iface);   /* a borrowed argument */
void *adopt_game(void *gp, unsigned iface);
void face_ref(Face *f);
void face_unref(Face *f);                        /* one game reference less (the object's own Release is the caller's) */
/* -1: not an interface of the spec. IUnknown has one entry per family (the C++ class of the object differs): the entry of `family`
 * wins, family 0 (a creation function: no object yet) takes the first. */
int iface_for_iid(const uint8_t canon[16], unsigned family = 0);
bool iface_is(unsigned have, unsigned want);     /* have == want or derives from it */

/* observers (tests): the faces and proxies the game / the platform holds a reference through */
unsigned live_faces();
unsigned live_proxies();

/* the platform calls a proxy's IUnknown */
unsigned long px_addref(ProxyRec *r);
unsigned long px_release(ProxyRec *r);
int32_t px_qi(ProxyRec *r, const uint8_t canon[16], const void *riid, void **out, unsigned family);

/* words of the x86 stack -> a value, by the C++ type (class closing: the type decides, no list) */
template <class T> inline T word(uintptr_t w)
{
    if constexpr (std::is_pointer<T>::value) return (T)w;
    else if constexpr (std::is_enum<T>::value) return (T)(uint32_t)w;
    else if constexpr (T(-1) < T(0)) return (T)(int32_t)(uint32_t)w;
    else return (T)(uint32_t)w;
}
inline uintptr_t ret_word(uint32_t v) { return (uintptr_t)v; }

}   // namespace nxcom
#endif
#endif
