/* cpplib.cpp - the MSVC 2008 C++ library (libcpmt, Dinkumware) internals that the game calls by address
 * (S4b-crt; addresses and identification in crt_symbols.tsv / docs/RUNTIME.md).
 *
 * Every function takes the object as its first argument (thiscall -> x0 on AArch64) and works on the MSVC object
 * layout (fnvnx_msvc_cpp.h), because the game's own objects embed these classes: the std::exception family,
 * std::_Mutex (in basic_streambuf), std::ios_base (in every stream), std::_Locinfo, and the global locale
 * implementation that the inline locale code of the game reads (_Facetvec, _Facetcount, _Xparent).
 *
 * Semantics are the "C" locale only: the C library locale of the port never changes, _Locinfo reports "C", the
 * ctype table is MSVC's C-locale table. The global locale has no facets: the game's inline use_facet creates each
 * one on first use (_Getcat) and registers it with _Facet_Register, as VC9's locale0 does. */
#include "fnvnx_msvcrt.h"
#include "fnvnx_iobuf.h"
#include "fnvnx_msvc_cpp.h"

#include <new>
#include <stdexcept>
#include <typeinfo>

#include "../os/os.h"

using namespace fnvnx::msvc;

/* ---- std::exception family (MSVC layout, MSVC semantics) */
extern "C" void *fnvnx_exception_ctor(void *self) { return new (self) exc<std::exception>(); }

/* exception(const char *const &what): copies the string (owned, freed by the destructor); NULL stays NULL */
extern "C" void *fnvnx_exception_ctor_str(void *self, const char *const *what)
{
    return new (self) exc<std::exception>(what ? *what : nullptr, true);
}

/* exception(const char *const &what, int): keeps the pointer (VC9's non-allocating constructor) */
extern "C" void *fnvnx_exception_ctor_noalloc(void *self, const char *const *what, int)
{
    return new (self) exc<std::exception>(what ? *what : nullptr, false);
}

extern "C" void *fnvnx_exception_copy_ctor(void *self, const void *other)
{
    exc<std::exception> *e = new (self) exc<std::exception>();
    if (other) {
        const exc_fields *o = (const exc_fields *)other;
        e->assign(o->_m_what, o->_m_doFree);
    }
    return self;
}

/* operator=: the fields only (the vptr of the target stays) */
extern "C" void *fnvnx_exception_assign(void *self, const void *other)
{
    if (self != other && other) {
        exc_fields *s = (exc_fields *)self;
        const exc_fields *o = (const exc_fields *)other;
        if (s->_m_doFree)
            free((void *)s->_m_what);
        s->_m_what = nullptr;
        s->_m_doFree = 0;
        if (o->_m_doFree && o->_m_what) {
            size_t n = strlen(o->_m_what) + 1;
            char *p = (char *)malloc(n);
            if (p)
                memcpy(p, o->_m_what, n);
            s->_m_what = p;
            s->_m_doFree = 1;
        } else {
            s->_m_what = o->_m_what;
        }
    }
    return self;
}

/* ~exception: frees an owned string; works on any MSVC-layout exception object (the vptr is not used) */
extern "C" void fnvnx_exception_dtor(void *self)
{
    exc_fields *s = (exc_fields *)self;
    if (s->_m_doFree)
        free((void *)s->_m_what);
    s->_m_what = nullptr;
    s->_m_doFree = 0;
}

extern "C" const char *fnvnx_exception_what(const void *self)
{
    const exc_fields *s = (const exc_fields *)self;
    return s->_m_what ? s->_m_what : "Unknown exception";
}

/* bad_alloc(const char *msg = "bad allocation"): an exception copying msg, catchable as std::bad_alloc */
extern "C" void *fnvnx_bad_alloc_ctor(void *self, const char *msg)
{
    return new (self) exc<std::bad_alloc>(msg ? msg : "bad allocation", true);
}

/* bad_cast(const char *msg = "bad cast"): an exception copying msg, catchable as std::bad_cast */
extern "C" void *fnvnx_bad_cast_ctor(void *self, const char *msg)
{
    return new (self) exc<std::bad_cast>(msg ? msg : "bad cast", true);
}

/* ---- _Mutex (basic_streambuf::_Mylock): a recursive lock behind void *_Mtx */
namespace {
struct rmtx {
    os_lock_t l;
    volatile uint32_t owner;
    int depth;
};

uint32_t thread_token()
{
    static __thread uint32_t tok;
    static volatile uint32_t next = 1;
    if (!tok)
        tok = __atomic_fetch_add(&next, 1, __ATOMIC_RELAXED);
    return tok;
}

/* the lock of a _Mutex; created on first use when the object was only zero-filled */
rmtx *mtx_of(void *self)
{
    void **slot = &((mutex *)self)->_Mtx;
    rmtx *m = (rmtx *)__atomic_load_n(slot, __ATOMIC_ACQUIRE);
    if (m)
        return m;
    rmtx *n = (rmtx *)calloc(1, sizeof(rmtx));
    if (!n)
        os_fatal("out of memory in std::_Mutex");
    void *expected = nullptr;
    if (__atomic_compare_exchange_n(slot, &expected, (void *)n, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE))
        return n;
    free(n);
    return (rmtx *)expected;
}

volatile int g_init_locks;
} // namespace

extern "C" void *fnvnx_mutex_ctor(void *self)
{
    ((mutex *)self)->_Mtx = calloc(1, sizeof(rmtx));
    if (!((mutex *)self)->_Mtx)
        os_fatal("out of memory in std::_Mutex");
    return self;
}

extern "C" void fnvnx_mutex_dtor(void *self)
{
    free(((mutex *)self)->_Mtx);
    ((mutex *)self)->_Mtx = nullptr;
}

extern "C" void fnvnx_mutex_lock(void *self)
{
    rmtx *m = mtx_of(self);
    uint32_t me = thread_token();
    if (__atomic_load_n(&m->owner, __ATOMIC_RELAXED) == me) {
        m->depth++;
        return;
    }
    os_lock(&m->l);
    __atomic_store_n(&m->owner, me, __ATOMIC_RELAXED);
    m->depth = 1;
}

extern "C" void fnvnx_mutex_unlock(void *self)
{
    rmtx *m = mtx_of(self);
    if (__atomic_load_n(&m->owner, __ATOMIC_RELAXED) != thread_token() || m->depth <= 0)
        return; /* not held by this thread: ignore, as a CRITICAL_SECTION would corrupt silently */
    if (--m->depth == 0) {
        __atomic_store_n(&m->owner, 0u, __ATOMIC_RELAXED);
        os_unlock(&m->l);
    }
}

/* _Init_locks: VC9 initializes the eight _Lockit locks here; the runtime's are statically initialized */
extern "C" void *fnvnx_init_locks_ctor(void *self)
{
    __atomic_add_fetch(&g_init_locks, 1, __ATOMIC_RELAXED);
    return self;
}

extern "C" void fnvnx_init_locks_dtor(void *self)
{
    (void)self;
    __atomic_sub_fetch(&g_init_locks, 1, __ATOMIC_RELAXED);
}

/* ---- locale: one immortal global _Locimp ("C", all categories, no facets, not transparent) */
namespace {
struct lockit_guard { /* _Lockit(kind) for the scope */
    int k;
    explicit lockit_guard(int kind) { fnvnx_lockit_ctor(&k, kind); }
    ~lockit_guard() { fnvnx_lockit_dtor(&k); }
};
enum { LOCK_LOCALE = 0, LOCK_STREAM = 2 };

locimp *global_locimp()
{
    static locimp *p = [] {
        locimp *q = new locimp(); /* never destroyed: static destructors of the game may still use it */
        q->_Refs = (size_t)-1;
        q->_Facetvec = nullptr;
        q->_Facetcount = 0;
        q->_Catmask = 0x3f; /* locale::all */
        q->_Xparent = false;
        q->_Name.init("C");
        return q;
    }();
    return p;
}

/* facet::_Decref: the facet when its count drops to 0, else NULL */
facet *decref(facet *f)
{
    lockit_guard g(LOCK_LOCALE);
    if (0 < f->_Refs && f->_Refs != (size_t)-1)
        --f->_Refs;
    return f->_Refs == 0 ? f : nullptr;
}

void locale_destroy(locale *l) /* locale::~locale */
{
    if (l && l->_Ptr) {
        facet *f = decref(l->_Ptr);
        delete f; /* virtual: the object's own deleting destructor */
    }
}

os_lock_t g_fac_lock;
facet **g_facets;
size_t g_nfacets, g_capfacets;
} // namespace

extern "C" void *fnvnx_locale_init(void) { return global_locimp(); }            /* locale::_Init */

/* locale::facet::_Incref: one more reference, under the locale lock; an immortal facet ((size_t)-1) stays so */
extern "C" void fnvnx_facet_incref(void *fp)
{
    facet *f = (facet *)fp;
    lockit_guard g(LOCK_LOCALE);
    if (f->_Refs < (size_t)-1 - 1)
        ++f->_Refs;
}
extern "C" void *fnvnx_locale_getgloballocale(void) { return global_locimp(); } /* locale::_Getgloballocale */

/* locale::facet::_Facet_Register: remembered so fnvnx_cpplib_facet_tidy can release them (VC9 does at exit;
 * the runtime leaves them alive at exit, static destructors of the game may still use facets) */
extern "C" void fnvnx_facet_register(void *f)
{
    os_lock(&g_fac_lock);
    if (g_nfacets == g_capfacets) {
        size_t cap = g_capfacets ? g_capfacets * 2 : 32;
        facet **n = (facet **)realloc(g_facets, cap * sizeof *n);
        if (!n) {
            os_unlock(&g_fac_lock);
            return;
        }
        g_facets = n;
        g_capfacets = cap;
    }
    g_facets[g_nfacets++] = (facet *)f;
    os_unlock(&g_fac_lock);
}

extern "C" size_t fnvnx_cpplib_facet_count(void)
{
    os_lock(&g_fac_lock);
    size_t n = g_nfacets;
    os_unlock(&g_fac_lock);
    return n;
}

/* VC9's _Fac_tidy: drops the reference of each registered facet, deleting those that reach 0 */
extern "C" void fnvnx_cpplib_facet_tidy(void)
{
    for (;;) {
        os_lock(&g_fac_lock);
        facet *f = g_nfacets ? g_facets[--g_nfacets] : nullptr;
        os_unlock(&g_fac_lock);
        if (!f)
            break;
        delete decref(f);
    }
}

/* ---- _Locinfo: the port's C library locale is always "C".
 * _Locinfo_ctor(_Locinfo *, const string &): the exe passes the locale name as a VC9 std::string by reference (R2:
 * the byte-matched 0x00ad3ca0 does), so the name is read through its c_str() (inline when _Myres < 16, else heap). */
extern "C" void fnvnx_locinfo_ctor(void *self, const void *locname_str)
{
    locinfo *p = (locinfo *)self;
    const char *locname = locname_str ? ((const string *)locname_str)->c_str() : nullptr;
    p->_Oldlocname.assign_short("C");
    const char *n = "*"; /* setlocale failed */
    if (locname && (!*locname || !strcmp(locname, "C")))
        n = "C";
    p->_Newlocname.assign_short(n);
}

extern "C" void fnvnx_locinfo_dtor(void *self) { (void)self; /* would restore _Oldlocname: never changed */ }

/* ---- ctype for the "C" locale (_Getctype, _Tolower, _Toupper) */
namespace {
/* MSVC's _pctype in the "C" locale: _UPPER 1, _LOWER 2, _DIGIT 4, _SPACE 8, _PUNCT 0x10, _CONTROL 0x20,
 * _BLANK 0x40, _HEX 0x80, C1_ALPHA 0x100 */
struct ctype_table {   /* constexpr: the table is constant-initialized, no static constructor (init order) */
    short t[256] = {};
    constexpr ctype_table()
    {
        for (int c = 0; c < 256; c++) {
            short m = 0;
            if (c < 0x20 || c == 0x7f)
                m = 0x20;
            if (c >= 9 && c <= 13)
                m |= 0x08;
            if (c == 9)
                m |= 0x40;
            if (c == ' ')
                m = 0x48;
            if ((c > ' ' && c < '0') || (c > '9' && c < 'A') || (c > 'Z' && c < 'a') || (c > 'z' && c < 0x7f))
                m = 0x10;
            if (c >= '0' && c <= '9')
                m = 0x84;
            if (c >= 'A' && c <= 'Z')
                m = (short)(0x101 | (c <= 'F' ? 0x80 : 0));
            if (c >= 'a' && c <= 'z')
                m = (short)(0x102 | (c <= 'f' ? 0x80 : 0));
            t[c] = m;
        }
    }
};
constexpr ctype_table g_ctype;
} // namespace

extern "C" const short *fnvnx_msvc_ctype_table(void) { return g_ctype.t; }

extern "C" fnvnx_ctypevec fnvnx_getctype(void)
{
    fnvnx_ctypevec v;
    v._Hand = 0; /* LCID of the C locale */
    v._Page = 0;
    v._Table = g_ctype.t;
    v._Delfl = 0; /* static table: ctype<char>::_Tidy must not free it */
    return v;
}

/* Only the low byte decides: the decomp declares these char(char, char *), and AAPCS64 leaves the upper bits of
 * a char argument unspecified. Only the C locale exists, so the _Ctypevec is not consulted. */
extern "C" int fnvnx_tolower_ctype(int c, const fnvnx_ctypevec *v)
{
    (void)v;
    unsigned char u = (unsigned char)c;
    return u >= 'A' && u <= 'Z' ? u + ('a' - 'A') : c;
}

extern "C" int fnvnx_toupper_ctype(int c, const fnvnx_ctypevec *v)
{
    (void)v;
    unsigned char u = (unsigned char)c;
    return u >= 'a' && u <= 'z' ? u - ('a' - 'A') : c;
}

/* ---- ios_base */
namespace {
const int NSTDSTR = 8;
ios_base *g_stdstr[NSTDSTR];
int g_stdopens[NSTDSTR];
} // namespace

/* ios_base::_Addstd: registers a standard stream (cin, cout, ...) in the next free slot 1..7 */
extern "C" void fnvnx_ios_base_addstd(void *self)
{
    ios_base *p = (ios_base *)self;
    lockit_guard g(LOCK_STREAM);
    for (p->_Stdstr = 0; ++p->_Stdstr < (size_t)NSTDSTR;)
        if (!g_stdstr[p->_Stdstr] || g_stdstr[p->_Stdstr] == p)
            break;
    if (p->_Stdstr >= (size_t)NSTDSTR) {
        p->_Stdstr = 0;
        return;
    }
    g_stdstr[p->_Stdstr] = p;
    ++g_stdopens[p->_Stdstr];
}

/* ios_base::_Ios_base_dtor: last close of a standard stream, or any other stream: erase_event callbacks, frees
 * the iword/pword and callback lists, deletes the locale */
extern "C" void fnvnx_ios_base_dtor(void *self)
{
    ios_base *p = (ios_base *)self;
    if (0 < p->_Stdstr && p->_Stdstr < (size_t)NSTDSTR) {
        lockit_guard g(LOCK_STREAM);
        if (0 < --g_stdopens[p->_Stdstr])
            return;
        g_stdstr[p->_Stdstr] = nullptr;
    }
    for (fnarray *f = p->_Calls; f; f = f->_Next)
        if (f->_Pfn)
            f->_Pfn(0 /* erase_event */, p, f->_Index);
    for (iosarray *a = p->_Arr, *n; a; a = n) {
        n = a->_Next;
        ::operator delete(a);
    }
    p->_Arr = nullptr;
    for (fnarray *f = p->_Calls, *n; f; f = n) {
        n = f->_Next;
        ::operator delete(f);
    }
    p->_Calls = nullptr;
    if (p->_Ploc) {
        locale_destroy(p->_Ploc);
        ::operator delete(p->_Ploc);
        p->_Ploc = nullptr;
    }
}

/* basic_ostream<char>::basic_ostream(basic_streambuf *sb, bool isstd) with MSVC's hidden "construct the virtual
 * base" flag: the library's standard ostream object in the image (only constructed and destroyed by the game, never
 * written to). Builds the MSVC layout so that the game's destructor (vbptr lookup, _Ios_base_dtor) works; the
 * ios_base / basic_ios vptrs stay NULL - there is no native vftable for them. */
extern "C" void *fnvnx_ostream_ctor(void *self, void *strbuf, int isstd, int construct_vbase)
{
    static const int32_t vbtable[2] = { 0, (int32_t)offsetof(ostream, ios) };
    ostream *o = (ostream *)self;
    if (construct_vbase)
        o->vbptr = vbtable;
    basic_ios *io = &o->ios;
    ios_base *b = &io->base;
    /* ios_base::_Init */
    b->vptr = nullptr;
    b->_Ploc = nullptr;
    b->_Stdstr = 0;
    b->_Except = 0;
    b->_Fmtfl = 0x201; /* skipws | dec */
    b->_Prec = 6;
    b->_Wide = 0;
    b->_Arr = nullptr;
    b->_Calls = nullptr;
    b->_Mystate = 0;
    locale *l = (locale *)::operator new(sizeof(locale));
    l->_Ptr = global_locimp(); /* locale(): _Init(), _Incref() (immortal) */
    b->_Ploc = l;
    /* basic_ios::init */
    io->_Mystrbuf = strbuf;
    io->_Tiestr = nullptr;
    io->_Fillch = ' ';
    if (!strbuf)
        b->_Mystate |= 4; /* badbit */
    if (isstd)
        fnvnx_ios_base_addstd(b);
    return self;
}

/* ---- _Fiopen(const char *name, ios_base::openmode mode, int prot): the FILE behind basic_filebuf::open */
extern "C" void *fnvnx_fiopen(const char *name, int mode, int prot)
{
    enum { in = 1, out = 2, ate = 4, app = 8, trunc = 0x10, binary = 0x20, nocreate = 0x40, noreplace = 0x80 };
    static const char *const mods[] = { "r", "w", "w", "a", "rb", "wb", "wb", "ab",
                                        "r+", "w+", "a+", "r+b", "w+b", "a+b", nullptr };
    static const int valid[] = { in, out, out | trunc, out | app, in | binary, out | binary, out | trunc | binary,
                                 out | app | binary, in | out, in | out | trunc, in | out | app, in | out | binary,
                                 in | out | trunc | binary, in | out | app | binary, 0 };
    int atend = mode & ate, norep = mode & noreplace;
    if (mode & nocreate)
        mode |= in; /* the file must exist */
    if (mode & app)
        mode |= out;
    mode &= ~(ate | nocreate | noreplace);
    int n = 0;
    while (valid[n] && valid[n] != mode)
        n++;
    if (!valid[n])
        return nullptr;
    _iobuf *fp;
    if (norep && (mode & (out | app)) && (fp = fnvnx_iob_fsopen(name, "r", prot)) != nullptr) {
        fnvnx_iob_fclose(fp); /* exists: not replaced */
        return nullptr;
    }
    if (!(fp = fnvnx_iob_fsopen(name, mods[n], prot)))
        return nullptr;
    if (!atend || fnvnx_iob_fseek(fp, 0, SEEK_END) == 0)
        return fp;
    fnvnx_iob_fclose(fp);
    return nullptr;
}
