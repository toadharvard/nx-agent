/* test_cpplib.cpp - MSVC 2008 C++ library internals (platform/crt/cpplib.cpp) and the CRT functions identified
 * in S4b (byteswap, _mbs*, _wcsicmp). */
#include "s4_test.h"

#include <errno.h>
#include <stdlib.h>

#include <atomic>
#include <thread>
#include <typeinfo>

#include "fnvnx_iobuf.h"
#include "fnvnx_msvc_cpp.h"
#include "fnvnx_msvcrt.h"

using namespace fnvnx::msvc;

namespace {
/* storage of a game object that embeds an MSVC std::exception (vptr, _m_what, _m_doFree) */
struct alignas(void *) exc_storage {
    unsigned char b[sizeof(exc<std::exception>)];
    exc_fields &f() { return *(exc_fields *)b; }
    std::exception &e() { return *(std::exception *)b; }
};
} // namespace

TEST(cpplib_exception_layout_and_semantics)
{
    CHECK_EQ(sizeof(exc<std::exception>), 2 * sizeof(void *) + sizeof(void *)); /* 12 on ILP32 */
    CHECK_EQ(offsetof(exc_fields, _m_what), sizeof(void *));
    CHECK_EQ(sizeof(exc<std::bad_alloc>), sizeof(exc<std::exception>));
    exc_storage a, b, c;
    const char *msg = "bad thing";
    CHECK(fnvnx_exception_ctor_str(a.b, &msg) == a.b);
    CHECK(a.f()._m_what != msg && !strcmp(a.f()._m_what, msg)); /* copied */
    CHECK_EQ(a.f()._m_doFree, 1);
    CHECK_STR(a.e().what(), "bad thing"); /* virtual call through the runtime's vtable */
    CHECK_STR(fnvnx_exception_what(a.b), "bad thing");
    CHECK(dynamic_cast<std::exception *>(&a.e()) != NULL);
    fnvnx_exception_copy_ctor(b.b, a.b); /* owned string: deep copy */
    CHECK(b.f()._m_what != a.f()._m_what && !strcmp(b.f()._m_what, msg));
    fnvnx_exception_dtor(a.b);
    CHECK(a.f()._m_what == NULL && a.f()._m_doFree == 0);
    CHECK_STR(b.e().what(), "bad thing");
    const char *none = NULL;
    fnvnx_exception_ctor_str(c.b, &none); /* the decomp's "int zero = 0; e.ctor(&zero)" */
    CHECK(c.f()._m_what == NULL && c.f()._m_doFree == 0);
    CHECK_STR(c.e().what(), "Unknown exception");
    fnvnx_exception_assign(c.b, b.b);
    CHECK(c.f()._m_what != b.f()._m_what && !strcmp(c.f()._m_what, msg));
    fnvnx_exception_dtor(b.b);
    fnvnx_exception_dtor(c.b);
    static const char lit[] = "literal";
    const char *lp = lit;
    fnvnx_exception_ctor_noalloc(a.b, &lp, 1); /* pointer kept */
    CHECK(a.f()._m_what == lit && a.f()._m_doFree == 0);
    fnvnx_exception_copy_ctor(b.b, a.b);
    CHECK(b.f()._m_what == lit); /* not owned: shared */
    fnvnx_exception_ctor(c.b);
    CHECK(c.f()._m_what == NULL);
    fnvnx_bad_cast_ctor(a.b, "bad cast");
    CHECK(dynamic_cast<std::bad_cast *>(&a.e()) != NULL);
    CHECK_STR(a.e().what(), "bad cast");
    fnvnx_exception_dtor(a.b);
    /* a complete runtime object destroyed through its own vtable frees the string */
    std::exception *heap = (std::exception *)new exc<std::exception>("x", true);
    delete heap;
}

TEST(cpplib_mutex_and_init_locks)
{
    mutex m = { NULL };
    CHECK(fnvnx_mutex_ctor(&m) == &m);
    CHECK(m._Mtx != NULL);
    fnvnx_mutex_lock(&m);
    fnvnx_mutex_lock(&m); /* recursive, as the CRITICAL_SECTION it replaces */
    fnvnx_mutex_unlock(&m);
    fnvnx_mutex_unlock(&m);
    std::atomic<int> inside(0), max_inside(0);
    int counter = 0;
    auto work = [&] {
        for (int i = 0; i < 20000; i++) {
            fnvnx_mutex_lock(&m);
            int n = ++inside;
            if (n > max_inside)
                max_inside = n;
            counter++;
            --inside;
            fnvnx_mutex_unlock(&m);
        }
    };
    std::thread t1(work), t2(work);
    t1.join();
    t2.join();
    CHECK_EQ(counter, 40000);
    CHECK_EQ(max_inside.load(), 1);
    fnvnx_mutex_dtor(&m);
    CHECK(m._Mtx == NULL);
    mutex z = { NULL }; /* zero-filled storage whose constructor never ran: created on first use */
    fnvnx_mutex_lock(&z);
    CHECK(z._Mtx != NULL);
    fnvnx_mutex_unlock(&z);
    fnvnx_mutex_dtor(&z);
    char il[4] = { 0 };
    CHECK(fnvnx_init_locks_ctor(il) == il);
    fnvnx_init_locks_dtor(il);
}

namespace {
int g_facet_dtors;
struct test_facet : facet {
    ~test_facet() override { g_facet_dtors++; }
};
/* the game's inline locale::_Getfacet, written against the MSVC layout */
const facet *getfacet(const locimp *p, size_t id)
{
    const facet *f = id < p->_Facetcount ? p->_Facetvec[id] : 0;
    if (f || !p->_Xparent)
        return f;
    const locimp *g = (const locimp *)fnvnx_locale_getgloballocale();
    return id < g->_Facetcount ? g->_Facetvec[id] : 0;
}
} // namespace

TEST(cpplib_locale)
{
    locimp *g = (locimp *)fnvnx_locale_init();
    CHECK(g != NULL);
    CHECK(g == fnvnx_locale_getgloballocale());
    CHECK_EQ(g->_Refs, (size_t)-1); /* immortal: _Incref/_Decref of the game never free it */
    CHECK_EQ(g->_Facetcount, 0u);
    CHECK(!g->_Xparent);
    CHECK_EQ(g->_Catmask, 0x3f);
    CHECK_STR(g->_Name.c_str(), "C");
    CHECK_EQ(g->_Name._Mysize, 1u);
    CHECK(getfacet(g, 3) == NULL); /* use_facet then builds the facet itself */
    test_facet *f = new test_facet();
    f->_Refs = 1;
    size_t before = fnvnx_cpplib_facet_count();
    fnvnx_facet_register(f);
    CHECK_EQ(fnvnx_cpplib_facet_count(), before + 1);
    fnvnx_cpplib_facet_tidy(); /* _Decref to 0 -> deleted through its own vtable */
    CHECK_EQ(g_facet_dtors, 1);
    CHECK_EQ(fnvnx_cpplib_facet_count(), 0u);
}

TEST(cpplib_locinfo_and_ctype)
{
    locinfo li;
    memset(&li, 0, sizeof li);
    li._Days.init("");
    li._Months.init("");
    li._Oldlocname.init("");
    li._Newlocname.init("");
    fnvnx_locinfo_ctor(&li, "C");
    CHECK_STR(li._Oldlocname.c_str(), "C");
    CHECK_STR(li._Newlocname.c_str(), "C");
    fnvnx_locinfo_ctor(&li, "German_Germany.1252");
    CHECK_STR(li._Newlocname.c_str(), "*"); /* setlocale would fail: only "C" exists */
    CHECK_EQ(li._Newlocname._Mysize, 1u);
    fnvnx_locinfo_ctor(&li, "");
    CHECK_STR(li._Newlocname.c_str(), "C");
    char heap[32];
    li._Newlocname._Myres = 31; /* a string with heap storage keeps it */
    li._Newlocname._Bx._Ptr = heap;
    fnvnx_locinfo_ctor(&li, NULL);
    CHECK_STR(heap, "*");
    li._Newlocname.init("");
    fnvnx_locinfo_dtor(&li);

    fnvnx_ctypevec v = fnvnx_getctype();
    CHECK_EQ(v._Hand, 0u);
    CHECK_EQ(v._Delfl, 0);
    const short *t = v._Table;
    CHECK(t == fnvnx_msvc_ctype_table());
    CHECK_EQ(t['A'], 0x181);
    CHECK_EQ(t['G'], 0x101);
    CHECK_EQ(t['f'], 0x182);
    CHECK_EQ(t['z'], 0x102);
    CHECK_EQ(t['5'], 0x84);
    CHECK_EQ(t[' '], 0x48);
    CHECK_EQ(t['\t'], 0x68);
    CHECK_EQ(t['\n'], 0x28);
    CHECK_EQ(t[0], 0x20);
    CHECK_EQ(t[0x7f], 0x20);
    CHECK_EQ(t['!'], 0x10);
    CHECK_EQ(t['~'], 0x10);
    CHECK_EQ(t[0xe9], 0);
    int alpha = 0;
    for (int c = 0; c < 256; c++)
        alpha += (t[c] & 0x103) != 0;
    CHECK_EQ(alpha, 52);
    CHECK_EQ(fnvnx_tolower_ctype('Q', &v), 'q');
    CHECK_EQ(fnvnx_tolower_ctype('q', &v), 'q');
    CHECK_EQ(fnvnx_tolower_ctype((char)0xC9, &v), (char)0xC9); /* C locale: ASCII only */
    CHECK_EQ(fnvnx_tolower_ctype(0x7f00 | 'Q', &v) & 0xff, 'q'); /* a char argument with unspecified upper bits */
    CHECK_EQ(fnvnx_toupper_ctype('q', NULL), 'Q');
    CHECK_EQ(fnvnx_toupper_ctype('[', NULL), '[');
}

namespace {
int g_cb_event = -1, g_cb_index = -1;
void *g_cb_ios;
void ios_callback(int ev, void *ios, int index)
{
    g_cb_event = ev;
    g_cb_ios = ios;
    g_cb_index = index;
}
} // namespace

TEST(cpplib_ios_base_and_ostream)
{
    ios_base a, b;
    memset(&a, 0, sizeof a);
    memset(&b, 0, sizeof b);
    fnvnx_ios_base_addstd(&a);
    fnvnx_ios_base_addstd(&b);
    CHECK(a._Stdstr >= 1 && b._Stdstr == a._Stdstr + 1);
    fnvnx_ios_base_addstd(&a); /* the same stream again: same slot, opened twice */
    a._Ploc = (locale *)::operator new(sizeof(locale));
    a._Ploc->_Ptr = (locimp *)fnvnx_locale_init();
    fnarray *cb = (fnarray *)::operator new(sizeof(fnarray));
    cb->_Next = NULL;
    cb->_Index = 7;
    cb->_Pfn = ios_callback;
    a._Calls = cb;
    a._Arr = (iosarray *)::operator new(sizeof(iosarray));
    memset(a._Arr, 0, sizeof *a._Arr);
    fnvnx_ios_base_dtor(&a); /* first close of a doubly opened standard stream: nothing yet */
    CHECK(a._Ploc != NULL && g_cb_event == -1);
    fnvnx_ios_base_dtor(&a);
    CHECK_EQ(g_cb_event, 0); /* erase_event */
    CHECK_EQ(g_cb_index, 7);
    CHECK(g_cb_ios == &a);
    CHECK(a._Ploc == NULL && a._Calls == NULL && a._Arr == NULL);
    fnvnx_ios_base_dtor(&b);
    ios_base c;
    memset(&c, 0, sizeof c);
    fnvnx_ios_base_addstd(&c); /* a's slot is free again */
    CHECK_EQ(c._Stdstr, a._Stdstr);
    fnvnx_ios_base_dtor(&c);

    /* the library's standard ostream object: constructed by the game's initializer, destroyed at exit */
    alignas(void *) unsigned char ob[sizeof(ostream)];
    int strbuf;
    memset(ob, 0xcc, sizeof ob);
    CHECK(fnvnx_ostream_ctor(ob, &strbuf, 0, 1) == ob);
    ostream *o = (ostream *)ob;
    CHECK_EQ(o->vbptr[1], (int32_t)offsetof(ostream, ios)); /* the game's destructor finds the vbase here */
    CHECK(o->ios._Mystrbuf == &strbuf && o->ios._Tiestr == NULL && o->ios._Fillch == ' ');
    CHECK_EQ(o->ios.base._Fmtfl, 0x201);
    CHECK_EQ(o->ios.base._Prec, 6);
    CHECK_EQ(o->ios.base._Mystate, 0);
    CHECK_EQ(o->ios.base._Stdstr, 0u);
    CHECK(o->ios.base._Ploc != NULL && o->ios.base._Ploc->_Ptr == fnvnx_locale_getgloballocale());
    fnvnx_ios_base_dtor((char *)ob + o->vbptr[1]);
    CHECK(o->ios.base._Ploc == NULL);
    fnvnx_ostream_ctor(ob, NULL, 1, 1);
    CHECK_EQ(o->ios.base._Mystate, 4); /* badbit without a buffer */
    CHECK(o->ios.base._Stdstr >= 1);
    fnvnx_ios_base_dtor(&o->ios.base);
}

TEST(cpplib_fiopen)
{
    enum { in = 1, out = 2, ate = 4, app = 8, trunc = 0x10, binary = 0x20, nocreate = 0x40, noreplace = 0x80 };
    CreateDirectoryA("Data", NULL);
    DeleteFileA("Data\\fio.txt");
    CHECK(fnvnx_fiopen("Data\\fio.txt", in, 0x40) == NULL);
    _iobuf *f = (_iobuf *)fnvnx_fiopen("Data\\fio.txt", out | trunc, 0x40);
    CHECK(f != NULL);
    fnvnx_iob_fputs("ab\n", f);
    fnvnx_iob_fclose(f);
    f = (_iobuf *)fnvnx_fiopen("Data\\fio.txt", in | binary, 0x40);
    char b[16];
    CHECK_STR(fnvnx_iob_fgets(b, sizeof b, f), "ab\r\n"); /* written in text mode, read in binary */
    fnvnx_iob_fclose(f);
    f = (_iobuf *)fnvnx_fiopen("Data\\fio.txt", in | ate, 0x40);
    CHECK_EQ(fnvnx_iob_ftell(f), 4);
    fnvnx_iob_fclose(f);
    f = (_iobuf *)fnvnx_fiopen("Data\\fio.txt", app | binary, 0x40); /* app implies out */
    fnvnx_iob_fputs("c", f);
    fnvnx_iob_fclose(f);
    f = (_iobuf *)fnvnx_fiopen("Data\\fio.txt", in | out | binary, 0x40);
    CHECK_STR(fnvnx_iob_fgets(b, sizeof b, f), "ab\r\n");
    CHECK_EQ(fnvnx_iob_fgetc(f), 'c');
    fnvnx_iob_fclose(f);
    CHECK(fnvnx_fiopen("Data\\fio.txt", out | noreplace, 0x40) == NULL);   /* exists */
    CHECK(fnvnx_fiopen("Data\\fio_n.txt", out | nocreate, 0x40) == NULL);  /* must exist */
    CHECK(fnvnx_fiopen("Data\\fio.txt", in | trunc, 0x40) == NULL);        /* no such mode */
    CHECK(fnvnx_fiopen("Data\\fio.txt", 0, 0x40) == NULL);
}

TEST(crt_identified_s4b)
{
    CHECK_EQ(fnvnx_byteswap_ulong(0x11223344ul), 0x44332211ul);
    CHECK_EQ(fnvnx_byteswap_ushort(0xA1B2), 0xB2A1);
    CHECK(fnvnx_byteswap_uint64(0x0102030405060708ull) == 0x0807060504030201ull);
    /* _mbstok has a position of its own */
    char a[] = "a,b,c", s[] = "x;y";
    char *ctx = NULL;
    CHECK_STR(strtok_s(a, ",", &ctx), "a");
    CHECK_STR(fnvnx_mbstok(s, ";"), "x");
    CHECK_STR(strtok_s(NULL, ",", &ctx), "b");
    CHECK_STR(fnvnx_mbstok(NULL, ";"), "y");
    CHECK(fnvnx_mbstok(NULL, ";") == NULL);
    unsigned char d[8];
    memset(d, 'Q', sizeof d);
    _mbsnbcpy(d, (const unsigned char *)"abc", 6);
    CHECK(!memcmp(d, "abc\0\0\0QQ", 8)); /* pads, as strncpy */
    _mbsncpy(d, (const unsigned char *)"longer!!", 4);
    CHECK(!memcmp(d, "long", 4));
    CHECK_EQ(_mbsnbcmp((const unsigned char *)"reload x", (const unsigned char *)"reload ", 7), 0);
    CHECK(_mbsnbcmp((const unsigned char *)"Reload", (const unsigned char *)"reload", 6) < 0);
    CHECK_EQ(_mbsnbicmp((const unsigned char *)"Reload", (const unsigned char *)"reload", 6), 0);
    CHECK_EQ(_mbsicmp((const unsigned char *)"Button", (const unsigned char *)"BUTTON"), 0);
    const unsigned short w1[] = { 'E', 'd', 'i', 't', 'o', 'r', 0 }, w2[] = { 'e', 'D', 'I', 'T', 'O', 'R', 0 };
    const unsigned short w3[] = { 'E', 'd', 0 };
    CHECK_EQ(fnvnx_wcsicmp16(w1, w2), 0);
    CHECK(fnvnx_wcsicmp16(w3, w1) < 0);
    CHECK(fnvnx_wcscmp16(w1, w2) < 0);
    CHECK_EQ(fnvnx_wcscmp16(w1, w1), 0);
}
