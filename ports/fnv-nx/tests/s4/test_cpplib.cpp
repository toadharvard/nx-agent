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
    CHECK(dynamic_cast<std::exception *>(&a.e()) != NULL);
    fnvnx_exception_ctor(b.b);
    fnvnx_exception_assign(b.b, a.b); /* owned string: deep copy */
    CHECK(b.f()._m_what != a.f()._m_what && !strcmp(b.f()._m_what, msg));
    a.e().~exception();
    a.f()._m_what = NULL;
    a.f()._m_doFree = 0;
    CHECK(a.f()._m_what == NULL && a.f()._m_doFree == 0);
    CHECK_STR(b.e().what(), "bad thing");
    const char *none = NULL;
    fnvnx_exception_ctor_str(c.b, &none); /* the decomp's "int zero = 0; e.ctor(&zero)" */
    CHECK(c.f()._m_what == NULL && c.f()._m_doFree == 0);
    CHECK_STR(c.e().what(), "Unknown exception");
    fnvnx_exception_assign(c.b, b.b);
    CHECK(c.f()._m_what != b.f()._m_what && !strcmp(c.f()._m_what, msg));
    b.e().~exception();
    c.e().~exception();
    static const char lit[] = "literal";
    const char *lp = lit;
    fnvnx_exception_ctor_noalloc(a.b, &lp, 1); /* pointer kept */
    CHECK(a.f()._m_what == lit && a.f()._m_doFree == 0);
    fnvnx_exception_ctor(b.b);
    fnvnx_exception_assign(b.b, a.b);
    CHECK(b.f()._m_what == lit); /* not owned: shared */
    fnvnx_exception_ctor(c.b);
    CHECK(c.f()._m_what == NULL);
    fnvnx_bad_cast_ctor(a.b, "bad cast");
    CHECK(dynamic_cast<std::bad_cast *>(&a.e()) != NULL);
    CHECK_STR(a.e().what(), "bad cast");
    a.e().~exception();
    /* a complete runtime object destroyed through its own vtable frees the string */
    std::exception *heap = (std::exception *)new exc<std::exception>("x", true);
    delete heap;
}

TEST(cpplib_mutex)
{
    mutex m = { NULL }; /* zero-filled storage: the lock is created on first use */
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
    CHECK(m._Mtx != NULL);
}

namespace {
int g_facet_dtors;
struct test_facet : facet {
    ~test_facet() override { g_facet_dtors++; }
};
} // namespace

TEST(cpplib_facet_register)
{
    test_facet *f = new test_facet();
    f->_Refs = 1;
    size_t before = fnvnx_cpplib_facet_count();
    fnvnx_facet_register(f);
    CHECK_EQ(fnvnx_cpplib_facet_count(), before + 1);
    fnvnx_cpplib_facet_tidy(); /* _Decref to 0 -> deleted through its own vtable */
    CHECK_EQ(g_facet_dtors, 1);
    CHECK_EQ(fnvnx_cpplib_facet_count(), 0u);
}

TEST(cpplib_basic_ios_init)
{
    int strbuf;
    /* basic_ios::init for the decomp's istream copies (S4 STL2): every field but the vptr their constructor set */
    alignas(void *) unsigned char ib[sizeof(basic_ios)];
    memset(ib, 0xcc, sizeof ib);
    basic_ios *io = (basic_ios *)ib;
    const void *vp = io->base.vptr;
    fnvnx_basic_ios_init(io, &strbuf, 0);
    CHECK(io->base.vptr == vp);
    CHECK(io->_Mystrbuf == &strbuf && io->_Tiestr == NULL && io->_Fillch == ' ');
    CHECK_EQ(io->base._Mystate, 0);
    CHECK_EQ(io->base._Except, 0);
    CHECK_EQ(io->base._Fmtfl, 0x201);
    CHECK(io->base._Arr == NULL && io->base._Calls == NULL);
    CHECK(io->base._Ploc != NULL && io->base._Ploc->_Ptr != NULL);
    fnvnx_ios_base_dtor(&io->base);
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
    CHECK(fnvnx_wcscmp16(w1, w2) < 0);
    CHECK_EQ(fnvnx_wcscmp16(w1, w1), 0);
}
