/* test_eh.cpp - _CxxThrowException -> typed GCC handlers (platform/crt/eh.cpp). The image path builds MSVC x86
 * EH tables (32-bit absolute addresses) in memory below 4 GiB, as the data image would hold them. */
#include "s4_test.h"

#include <stdlib.h>

#include <ios>
#include <stdexcept>
#include <string>

#include "../../platform/os/os.h"
#include "fnvnx_msvc_cpp.h"
#include "fnvnx_msvcrt.h"

using namespace fnvnx::msvc;

namespace {
int g_copies, g_dtors;
struct GameBase {
    virtual ~GameBase() { g_dtors++; }
    int code = 0;
};
struct GameError : GameBase {
    std::string text;
    GameError(int c, const char *t) : text(t) { code = c; }
    GameError(const GameError &o) : GameBase(o), text(o.text) { g_copies++; }
};

/* an MSVC std::logic_error-family object as game code builds it (exception base + VC9 string _Str) */
struct msvc_logic_obj {
    logic_error_fields f;
    char heap[64];
    msvc_logic_obj(const char *s)
    {
        memset(this, 0, sizeof *this);
        f.base.vptr = (const void *)(uintptr_t)0x010173dc; /* image vftable: never used by the runtime */
        size_t l = strlen(s);
        if (l < 16) {
            f._Str.init(s);
        } else {
            memcpy(heap, s, l + 1);
            f._Str._Bx._Ptr = heap;
            f._Str._Myres = sizeof heap - 1;
            f._Str._Mysize = l;
        }
    }
};

/* ---- a small "image" below 4 GiB */
unsigned char *g_img;
uint32_t g_used;
const size_t IMG_SIZE = 0x10000;

unsigned char *image()
{
    if (g_img)
        return g_img;
    for (uintptr_t hint = 0x30000000; hint < 0xF0000000u && !g_img; hint += 0x10000000) {
        void *p = os_vm_reserve((void *)hint, IMG_SIZE);
        if (p && os_vm_commit(p, IMG_SIZE) == 0)
            g_img = (unsigned char *)p;
    }
    return g_img;
}

uint32_t va(const void *p) { return (uint32_t)(uintptr_t)p; }

void *alloc(size_t n)
{
    void *p = image() + g_used;
    g_used += (uint32_t)((n + 7) & ~(size_t)7);
    return p;
}

uint32_t type_descriptor(const char *name)
{
    size_t l = strlen(name);
    fnvnx_msvc_type_descriptor *td = (fnvnx_msvc_type_descriptor *)alloc(sizeof *td + l);
    td->pVFTable = 0x01000000;
    memcpy(td->name, name, l + 1);
    return va(td);
}

uint32_t catchable(uint32_t td, uint32_t props, int32_t size, uint32_t copy)
{
    fnvnx_msvc_catchable_type *ct = (fnvnx_msvc_catchable_type *)alloc(sizeof *ct);
    ct->properties = props;
    ct->pType = td;
    ct->mdisp = 0;
    ct->pdisp = -1;
    ct->vdisp = 0;
    ct->sizeOrOffset = size;
    ct->copyFunction = copy;
    return va(ct);
}

const void *throw_info(uint32_t unwind, int n, const uint32_t *cts)
{
    fnvnx_msvc_catchable_type_array *a =
        (fnvnx_msvc_catchable_type_array *)alloc(sizeof(int32_t) + sizeof(uint32_t) * (size_t)n);
    a->nCatchableTypes = n;
    for (int i = 0; i < n; i++)
        a->arrayOfCatchableTypes[i] = cts[i];
    fnvnx_msvc_throw_info *ti = (fnvnx_msvc_throw_info *)alloc(sizeof *ti);
    ti->attributes = 0;
    ti->pmfnUnwind = unwind;
    ti->pForwardCompat = 0;
    ti->pCatchableTypeArray = va(a);
    return ti;
}

/* the x86 code addresses in the tables -> native functions (S2's address map on the target) */
void copy_game_error(void *d, const void *s) { new (d) GameError(*(const GameError *)s); }
void destroy_game_error(void *p) { ((GameError *)p)->~GameError(); }
void *resolve(uint32_t v)
{
    if (v == 0x00501000)
        return (void *)&copy_game_error;
    if (v == 0x00501010)
        return (void *)&destroy_game_error;
    return NULL;
}
} // namespace

TEST(eh_native_type_registered)
{
    static const int ti_site = 0; /* stands for a ThrowInfo address of the image */
    fnvnx_eh_register_type<GameError>(&ti_site);
    CHECK_STR(fnvnx_eh_describe(&ti_site), typeid(GameError).name());
    g_copies = g_dtors = 0;
    bool caught = false;
    {
        GameError e(42, "malformed flare info");
        try {
            fnvnx_cxx_throw_exception(&e, (void *)&ti_site);
        } catch (GameBase &b) { /* a typed handler for a base class */
            caught = b.code == 42 && &b != &e && static_cast<GameError &>(b).text == "malformed flare info";
        }
        CHECK_EQ(g_copies, 1);
        CHECK_EQ(g_dtors, 1); /* the copy, after the handler */
    }
    CHECK(caught);
    CHECK_EQ(g_dtors, 2);
}

TEST(eh_msvc_std_registered)
{
    /* the decomp's _Allocate overflow path: exception(&null) + bad_alloc vftable + ThrowInfo 0x01140ef8 */
    static const int ti_bad_alloc = 0, ti_length = 0, ti_bad_cast = 0, ti_runtime = 0;
    CHECK_EQ(fnvnx_eh_register_msvc_std(&ti_bad_alloc, ".?AVbad_alloc@std@@"), 0);
    CHECK_EQ(fnvnx_eh_register_msvc_std(&ti_length, ".?AVlength_error@std@@"), 0);
    CHECK_EQ(fnvnx_eh_register_msvc_std(&ti_bad_cast, ".?AVbad_cast@std@@"), 0);
    CHECK_EQ(fnvnx_eh_register_msvc_std(&ti_runtime, ".?AVruntime_error@std@@"), 0);
    CHECK_EQ(fnvnx_eh_register_msvc_std(&ti_runtime, ".?AVnot_a_std_class@@"), -1);
    CHECK_STR(fnvnx_eh_describe(&ti_bad_alloc), ".?AVbad_alloc@std@@");
    fnvnx_eh_register_fnv_known(); /* keys only: the image addresses are not read */
    CHECK_STR(fnvnx_eh_describe((const void *)(uintptr_t)0x01140ef8u), ".?AVbad_alloc@std@@");
    CHECK_STR(fnvnx_eh_describe((const void *)(uintptr_t)0x01140cb0u), ".?AVlength_error@std@@");
    alignas(void *) unsigned char obj[sizeof(exc<std::exception>)];
    const char *none = NULL;
    fnvnx_exception_ctor_str(obj, &none);
    ((exc_fields *)obj)->vptr = (const void *)(uintptr_t)0x010176f8;
    std::string what;
    try {
        fnvnx_cxx_throw_exception(obj, (void *)&ti_bad_alloc);
    } catch (std::bad_alloc &e) {
        what = e.what();
    }
    CHECK(what == "Unknown exception"); /* MSVC's what() for exception(NULL) */
    bool as_base = false;
    try {
        fnvnx_cxx_throw_exception(obj, (void *)&ti_bad_alloc);
    } catch (std::exception &e) {
        as_base = dynamic_cast<std::bad_alloc *>(&e) != NULL;
    }
    CHECK(as_base);
    msvc_logic_obj s("vector<T> too long"); /* 18 characters: heap string */
    try {
        fnvnx_cxx_throw_exception(&s, (void *)&ti_length);
    } catch (std::length_error &e) {
        what = e.what();
    }
    CHECK(what == "vector<T> too long");
    msvc_logic_obj r("short");
    try {
        fnvnx_cxx_throw_exception(&r, (void *)&ti_runtime);
    } catch (std::runtime_error &e) {
        what = e.what();
    }
    CHECK(what == "short");
    fnvnx_bad_cast_ctor(obj, "bad locale");
    try {
        fnvnx_cxx_throw_exception(obj, (void *)&ti_bad_cast);
    } catch (std::bad_cast &e) {
        what = e.what();
    }
    CHECK(what == "bad locale");
    fnvnx_exception_dtor(obj);
}

TEST(eh_throw_info_from_image)
{
    if (!image()) {
        CHECK(!"no memory below 4 GiB for the image tables");
        return;
    }
    fnvnx_eh_set_image_range(va(g_img), va(g_img) + (uint32_t)IMG_SIZE);
    fnvnx_eh_set_code_resolver(resolve);

    /* an MSVC std::out_of_range (catchable as out_of_range, logic_error, exception) */
    uint32_t oor[3] = { catchable(type_descriptor(".?AVout_of_range@std@@"), 0, 40, 0x00ec7944),
                        catchable(type_descriptor(".?AVlogic_error@std@@"), 0, 40, 0x00ec7944),
                        catchable(type_descriptor(".?AVexception@std@@"), 0, 12, 0x00ec7944) };
    const void *ti_oor = throw_info(0x00ec79a1, 3, oor);
    CHECK_STR(fnvnx_eh_describe(ti_oor), ".?AVout_of_range@std@@");
    msvc_logic_obj o("invalid vector<T> subscript");
    std::string what;
    try {
        fnvnx_cxx_throw_exception(&o, (void *)ti_oor);
    } catch (std::logic_error &e) {
        what = e.what();
        CHECK(dynamic_cast<std::out_of_range *>(&e) != NULL);
    }
    CHECK(what == "invalid vector<T> subscript");

    /* a game class whose TypeDescriptor S2 registered (??_R0 -> _ZTI), copied with its copy constructor and
     * destroyed with the ThrowInfo's pmfnUnwind (x86 addresses resolved to native functions) */
    uint32_t td_err = type_descriptor(".?AVGameError@@"), td_base = type_descriptor(".?AVGameBase@@");
    fnvnx_rtti_register((const void *)(uintptr_t)td_err, &typeid(GameError));
    uint32_t ge[2] = { catchable(td_err, 0, (int32_t)sizeof(GameError), 0x00501000),
                       catchable(td_base, 0, (int32_t)sizeof(GameBase), 0x00501020) };
    const void *ti_ge = throw_info(0x00501010, 2, ge);
    g_copies = g_dtors = 0;
    int code = 0;
    {
        GameError e(7, "malformed general wind information");
        try {
            fnvnx_cxx_throw_exception(&e, (void *)ti_ge);
        } catch (GameError &x) {
            code = x.code;
            what = x.text;
        }
        CHECK_EQ(g_copies, 1);
        CHECK_EQ(g_dtors, 1);
    }
    CHECK_EQ(code, 7);
    CHECK(what == "malformed general wind information");

    /* a simple (bitwise) type: int, TypeDescriptor ".H" */
    uint32_t td_int = type_descriptor(".H");
    fnvnx_rtti_register((const void *)(uintptr_t)td_int, &typeid(int));
    uint32_t ci[1] = { catchable(td_int, 1, 4, 0) };
    const void *ti_int = throw_info(0, 1, ci);
    int v = 1234, got = 0;
    try {
        fnvnx_cxx_throw_exception(&v, (void *)ti_int);
    } catch (int x) {
        got = x;
    }
    CHECK_EQ(got, 1234);

    /* nothing known about the type: catch (...) only */
    uint32_t cu[1] = { catchable(type_descriptor(".?AVUnknown@@"), 0, 4, 0) };
    const void *ti_unknown = throw_info(0, 1, cu);
    bool fallback = false;
    try {
        fnvnx_cxx_throw_exception(&v, (void *)ti_unknown);
    } catch (const fnvnx_msvc_exception &e) {
        fallback = e.object == &v && e.throw_info == ti_unknown;
    }
    CHECK(fallback);
    /* the decomp passing the ThrowInfo's first word instead of its address (DAT_01140ef8 by value): 0 */
    fallback = false;
    try {
        fnvnx_cxx_throw_exception(&v, NULL);
    } catch (...) {
        fallback = true;
    }
    CHECK(fallback);
    fnvnx_eh_set_code_resolver(NULL);
    fnvnx_eh_set_image_range(0, 0);
}

TEST(eh_rethrow)
{
    static const int ti_site = 0;
    fnvnx_eh_register_type<GameError>(&ti_site);
    const void *inner = NULL, *outer = NULL;
    try {
        try {
            GameError e(1, "x");
            fnvnx_cxx_throw_exception(&e, (void *)&ti_site);
        } catch (GameError &x) {
            inner = &x;
            fnvnx_cxx_throw_exception(NULL, NULL); /* "throw;" from inside the handler */
        }
    } catch (GameError &x) {
        outer = &x;
    }
    CHECK(inner != NULL && inner == outer); /* the same exception object */
}
