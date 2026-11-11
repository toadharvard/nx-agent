/* fnvnx_msvc_cpp.h - object layouts of the MSVC 2008 C++ library (Dinkumware, release build) that the game's
 * objects embed or that the runtime hands to game code (runtime-internal; S4b-crt).
 *
 * Offsets in the comments are for ILP32 (the game's data model) and are static_asserted there; on an LP64 host
 * (tests) the same fields are simply wider. Classes with a vptr carry an Itanium vtable of this runtime (the
 * game is compiled by GCC), so virtual calls from game code (what(), the facet destructor) work on them. */
#ifndef FNVNX_MSVC_CPP_H
#define FNVNX_MSVC_CPP_H

#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include <exception>
#include <new>
#include <typeinfo>

namespace fnvnx {
namespace msvc {

/* std::exception: vptr 0, const char *_m_what 4, int _m_doFree 8 (12 bytes). exc<B> derives from the libstdc++
 * class B (std::exception, std::bad_alloc, std::bad_cast, ...: no data members), so typed handlers for the
 * standard types catch it, and adds MSVC's two fields and semantics: what() is _m_what or "Unknown exception",
 * the string is owned (freed by the destructor) when _m_doFree is set. */
template <class Base> struct exc : Base {
    const char *_m_what;
    int _m_doFree;
    exc() noexcept : Base(), _m_what(nullptr), _m_doFree(0) {}
    exc(const char *w, bool copy) noexcept : Base(), _m_what(nullptr), _m_doFree(0)
    {
        if (copy)
            copy_str(w);
        else
            _m_what = w;
    }
    exc(const exc &o) noexcept : Base(), _m_what(nullptr), _m_doFree(0) { assign(o._m_what, o._m_doFree); }
    exc &operator=(const exc &o) noexcept
    {
        if (this != &o)
            assign(o._m_what, o._m_doFree);
        return *this;
    }
    ~exc() override { tidy(); }
    const char *what() const noexcept override { return _m_what ? _m_what : "Unknown exception"; }

    void copy_str(const char *w) noexcept
    {
        if (!w)
            return;
        size_t n = strlen(w) + 1;
        char *p = (char *)malloc(n);
        if (p)
            memcpy(p, w, n);
        _m_what = p;
        _m_doFree = 1;
    }
    void tidy() noexcept
    {
        if (_m_doFree)
            free((void *)_m_what);
        _m_what = nullptr;
        _m_doFree = 0;
    }
    void assign(const char *w, int dofree) noexcept
    {
        tidy();
        if (dofree)
            copy_str(w);
        else
            _m_what = w;
    }
};

/* the fields of any MSVC std::exception-derived object, whatever its vptr */
struct exc_fields {
    const void *vptr;
    const char *_m_what;
    int _m_doFree;
};

/* basic_string<char> (VC9 release, 28 bytes): _Alval 0 (empty allocator + padding), _Bx 4 (16-byte buffer or
 * heap pointer), _Mysize 20, _Myres 24 (heap when _Myres >= 16) */
struct string {
    uint32_t _Alval;
    union {
        char _Buf[16];
        char *_Ptr;
    } _Bx;
    size_t _Mysize;
    size_t _Myres;

    const char *c_str() const { return _Myres >= 16 ? _Bx._Ptr : _Bx._Buf; }
    void init(const char *s) /* a fresh string holding s (at most 15 characters) */
    {
        _Alval = 0;
        _Myres = 15;
        _Mysize = 0;
        _Bx._Buf[0] = 0;
        assign_short(s);
    }
    /* assigns into the existing storage; false (unchanged) if s does not fit */
    bool assign_short(const char *s)
    {
        size_t l = strlen(s);
        if (l > _Myres)
            return false;
        char *d = _Myres >= 16 ? _Bx._Ptr : _Bx._Buf;
        memcpy(d, s, l + 1);
        _Mysize = l;
        return true;
    }
};

/* std::logic_error / runtime_error and their derived classes: exception base, then string _Str (40 bytes) */
struct logic_error_fields {
    exc_fields base;
    string _Str;
};

/* locale::facet: vptr 0 (virtual destructor), size_t _Refs 4; _Refs == (size_t)-1 is immortal */
struct facet {
    virtual ~facet() = default;
    size_t _Refs;
};

/* locale::_Locimp: facet, _Facetvec 8, _Facetcount 0xc, _Catmask 0x10, _Xparent 0x14, string _Name 0x18 (0x34) */
struct locimp : facet {
    facet **_Facetvec;
    size_t _Facetcount;
    int _Catmask;
    bool _Xparent;
    string _Name;
};

struct locale {
    locimp *_Ptr;
};

/* ios_base: vptr 0, _Stdstr 4, _Mystate 8, _Except 0xc, _Fmtfl 0x10, _Prec 0x14, _Wide 0x18, _Arr 0x1c,
 * _Calls 0x20, locale *_Ploc 0x24 (0x28); streamsize is int in VC9 x86 */
struct iosarray {
    iosarray *_Next;
    int _Index;
    long _Lo;
    void *_Vp;
};
struct fnarray {
    fnarray *_Next;
    int _Index;
    void (*_Pfn)(int ev, void *ios, int index); /* event_callback (cdecl) */
};
struct ios_base {
    const void *vptr;
    size_t _Stdstr;
    int _Mystate;
    int _Except;
    int _Fmtfl;
    int _Prec;
    int _Wide;
    iosarray *_Arr;
    fnarray *_Calls;
    locale *_Ploc;
};
/* basic_ios<char>: ios_base, _Mystrbuf 0x28, _Tiestr 0x2c, _Fillch 0x30 (0x34) */
struct basic_ios {
    ios_base base;
    void *_Mystrbuf;
    void *_Tiestr;
    char _Fillch;
};
/* basic_ostream<char>: vbptr 0 (virtual base basic_ios), basic_ios at 4 (0x38) */
struct ostream {
    const int32_t *vbptr;
    basic_ios ios;
};

/* _Locinfo: _Lockit _Lock 0, string _Days 4, _Months 0x20, _Oldlocname 0x3c, _Newlocname 0x58 (0x74) */
struct locinfo {
    int _Lock;
    string _Days, _Months, _Oldlocname, _Newlocname;
};

/* _Mutex: void *_Mtx (4) */
struct mutex {
    void *_Mtx;
};

#if defined(__ILP32__) || (defined(__SIZEOF_POINTER__) && __SIZEOF_POINTER__ == 4)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Winvalid-offsetof"
static_assert(sizeof(exc<std::exception>) == 12 && offsetof(exc_fields, _m_doFree) == 8, "MSVC std::exception");
static_assert(sizeof(string) == 28 && offsetof(string, _Mysize) == 20 && offsetof(string, _Myres) == 24, "string");
static_assert(sizeof(logic_error_fields) == 40, "MSVC std::logic_error");
static_assert(sizeof(locimp) == 0x34 && offsetof(locimp, _Xparent) == 0x14 && offsetof(locimp, _Name) == 0x18,
              "locale::_Locimp");
static_assert(sizeof(ios_base) == 0x28 && offsetof(ios_base, _Ploc) == 0x24, "ios_base");
static_assert(sizeof(basic_ios) == 0x34 && sizeof(ostream) == 0x38, "basic_ios / basic_ostream");
static_assert(sizeof(locinfo) == 0x74 && offsetof(locinfo, _Newlocname) == 0x58, "_Locinfo");
static_assert(sizeof(iosarray) == 16 && sizeof(fnarray) == 12, "_Iosarray / _Fnarray");
#pragma GCC diagnostic pop
#endif

} // namespace msvc
} // namespace fnvnx

#endif
