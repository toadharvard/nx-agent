/* test_guestfn.cpp - shim/include/fnvnx_guestfn.h: the type of a game code pointer and its marshalling into x86 stack
 * words. The boundary's guest_invoke is replaced by a recording test double (host_boundary.h g_bnd_invoke_hook): the
 * words, ecx, count and result kind of every call are exactly what the header specifies. */
#include "s4_test.h"

#include "fnvnx_guestfn.h"
#include "host_boundary.h"

namespace {
struct rec {
    int calls;
    fnvnx_gaddr addr;
    uint32_t ecx, nargs, kind;
    uint32_t w[16];
    struct fnvnx_bnd_ret ret;   /* what the double answers */
};
rec g_rec;

int hook(fnvnx_gaddr addr, uint32_t ecx, uint32_t nargs, const fnvnx_gword *args, uint32_t kind, struct fnvnx_bnd_ret *out)
{
    g_rec.calls++;
    g_rec.addr = addr, g_rec.ecx = ecx, g_rec.nargs = nargs, g_rec.kind = kind;
    for (uint32_t i = 0; i < nargs && i < 16; i++)
        g_rec.w[i] = (uint32_t)args[i];   /* the product's words are 32 bits */
    if (out)
        *out = g_rec.ret;
    return 1;
}
struct install {
    install() { memset(&g_rec, 0, sizeof g_rec); g_bnd_invoke_hook = hook; }
    ~install() { g_bnd_invoke_hook = nullptr; }
};

/* ---- the properties the rule rests on, at compile time */
typedef fnvnx_guestfn<long(void *, unsigned, unsigned long, long)> wndproc_t;
static_assert(sizeof(wndproc_t) == sizeof(void *) && alignof(wndproc_t) == alignof(void *), "pointer-sized");
static_assert(sizeof(fnvnx_guestfn<void(void)>) == sizeof(void *), "pointer-sized (void)");
static_assert(sizeof(fnvnx_guestfn<double(double, long long)>) == sizeof(void *), "pointer-sized (double)");
static_assert(std::is_trivially_copyable<wndproc_t>::value, "trivially copyable");
static_assert(!std::is_convertible<wndproc_t, long (*)(void *, unsigned, unsigned long, long)>::value, "no conversion to a native pointer");
static_assert(!std::is_convertible<long (*)(void *, unsigned, unsigned long, long), wndproc_t>::value, "no implicit native pointer in");
static_assert(!std::is_constructible<fnvnx_guestfn<void(void)>, void (*)(void)>::value, "a native function pointer cannot be wrapped");
static_assert(std::is_constructible<fnvnx_guestfn<void(void)>, uintptr_t>::value, "explicit from uintptr_t");
static_assert(std::is_constructible<fnvnx_guestfn<void(void)>, void *>::value, "explicit from void *");
static_assert(fnvnx_gfn::classify<int>() == fnvnx_gfn::cls::w1s && fnvnx_gfn::classify<unsigned char>() == fnvnx_gfn::cls::w1u, "int classes");
static_assert(fnvnx_gfn::classify<void (*)(void)>() == fnvnx_gfn::cls::bad, "a function pointer argument is rejected");
static_assert(fnvnx_gfn::classify<struct fnvnx_bnd_ret>() == fnvnx_gfn::cls::bad, "a class argument is rejected");
static_assert(fnvnx_gfn::classify<__int128>() == fnvnx_gfn::cls::bad, "an odd integer width is rejected");
static_assert(fnvnx_gfn::nwords<long long>() == 2 && fnvnx_gfn::nwords<double>() == 2 && fnvnx_gfn::nwords<float>() == 1 &&
                  fnvnx_gfn::nwords<long>() == 1 && fnvnx_gfn::nwords<const char *>() == 1,
              "word counts");

int native_add(int a, int b) { return a + b; }
long native_proc(void *h, unsigned m, unsigned long w, long l) { return (long)(uintptr_t)h + (long)m * 10 + (long)w + l; }
int native_deref(const int *p, int k) { return *p * k; }
}   // namespace

TEST(gfn_layout_and_values)
{
    wndproc_t n;
    CHECK(n == nullptr && !(n != nullptr) && nullptr == n);
    CHECK(!n);
    CHECK_EQ((long long)n.value(), 0);
    wndproc_t a((uintptr_t)0x401000u), b((uintptr_t)0x401000u), c((uintptr_t)0x402000u);
    CHECK(a == b && a != c && a != nullptr && !!a);
    CHECK_EQ((long long)(uintptr_t)a, 0x401000);
    CHECK((void *)a == (void *)(uintptr_t)0x401000u);
    wndproc_t d((void *)(uintptr_t)0x403000u);
    CHECK_EQ((long long)d.value(), 0x403000);
    wndproc_t cp = d;   /* trivially copyable */
    CHECK(cp == d);
    wndproc_t arr[3] = {};
    CHECK(arr[0] == nullptr && arr[2] == nullptr);
    struct S { int x; wndproc_t p; } s = {1, a};
    CHECK(s.p == a);
}

TEST(gfn_marshal_one_word_args)
{
    install in;
    fnvnx_guestfn<void(int, unsigned, short, unsigned short, signed char, unsigned char, bool, long, unsigned long, char)> f(
        (uintptr_t)0x1234u);
    f(-1, 0xFFFFFFFEu, (short)-3, (unsigned short)0xFFFEu, (signed char)-5, (unsigned char)0xFBu, true, -7L, 0xFFFFFFF0ul, 'A');
    CHECK_EQ(g_rec.calls, 1);
    CHECK_EQ(g_rec.addr, 0x1234);
    CHECK_EQ(g_rec.ecx, 0);
    CHECK_EQ(g_rec.nargs, 10);
    CHECK_EQ(g_rec.kind, FNVNX_BND_RET_INT);
    CHECK(g_rec.w[0] == 0xFFFFFFFFu);   /* int -1: two's complement */
    CHECK(g_rec.w[1] == 0xFFFFFFFEu);
    CHECK(g_rec.w[2] == 0xFFFFFFFDu);   /* short -3 sign-extended */
    CHECK(g_rec.w[3] == 0xFFFEu);       /* unsigned short zero-extended */
    CHECK(g_rec.w[4] == 0xFFFFFFFBu);
    CHECK(g_rec.w[5] == 0xFBu);
    CHECK(g_rec.w[6] == 1);
    CHECK(g_rec.w[7] == 0xFFFFFFF9u);   /* long -7 (a Win32 LONG) */
    CHECK(g_rec.w[8] == 0xFFFFFFF0u);   /* unsigned long (a DWORD) */
    CHECK(g_rec.w[9] == 'A');
}

TEST(gfn_marshal_pointers_and_enums)
{
    install in;
    enum E1 { E_NEG = -2, E_POS = 9 };
    enum class E2 : unsigned char { A = 200 };
    int x = 0;
    fnvnx_guestfn<void(void *, const char *, int *, E1, E1, E2, decltype(nullptr) *)> f((uintptr_t)0x10u);
    f((void *)(uintptr_t)0x2000u, "s", &x, E_NEG, E_POS, E2::A, nullptr);
    CHECK_EQ(g_rec.nargs, 7);
    CHECK(g_rec.w[0] == 0x2000u);
    CHECK(g_rec.w[1] == (uint32_t)(uintptr_t)"s");   /* the 32-bit view of the pointer */
    CHECK(g_rec.w[3] == 0xFFFFFFFEu);
    CHECK(g_rec.w[4] == 9);
    CHECK(g_rec.w[5] == 200);
    CHECK(g_rec.w[6] == 0);
}

TEST(gfn_marshal_two_word_args)
{
    install in;
    fnvnx_guestfn<void(int, long long, double, float, unsigned long long, int)> f((uintptr_t)0x20u);
    f(7, 0x1122334455667788ll, 1.5, 1.5f, 0xFFFFFFFFFFFFFFFEull, -1);
    /* words: int | ll lo hi | double lo hi | float | ull lo hi | int */
    CHECK_EQ(g_rec.nargs, 1 + 2 + 2 + 1 + 2 + 1);
    CHECK(g_rec.w[0] == 7);
    CHECK(g_rec.w[1] == 0x55667788u && g_rec.w[2] == 0x11223344u);
    CHECK(g_rec.w[3] == 0x00000000u && g_rec.w[4] == 0x3FF80000u);   /* 1.5 = 0x3FF8000000000000 */
    CHECK(g_rec.w[5] == 0x3FC00000u);                                  /* 1.5f */
    CHECK(g_rec.w[6] == 0xFFFFFFFEu && g_rec.w[7] == 0xFFFFFFFFu);
    CHECK(g_rec.w[8] == 0xFFFFFFFFu);
    /* negative 64-bit and double */
    fnvnx_guestfn<void(long long, double)> g((uintptr_t)0x21u);
    g(-2ll, -2.0);
    CHECK_EQ(g_rec.nargs, 4);
    CHECK(g_rec.w[0] == 0xFFFFFFFEu && g_rec.w[1] == 0xFFFFFFFFu);
    CHECK(g_rec.w[2] == 0 && g_rec.w[3] == 0xC0000000u);
    /* no arguments */
    fnvnx_guestfn<void(void)> h((uintptr_t)0x22u);
    h();
    CHECK_EQ(g_rec.nargs, 0);
    CHECK_EQ(g_rec.addr, 0x22);
    /* the widest call the host implementation carries */
    fnvnx_guestfn<void(double, double, double, double, double, double, double, double)> m((uintptr_t)0x23u);
    m(1, 2, 3, 4, 5, 6, 7, 8);
    CHECK_EQ(g_rec.nargs, 16);
}

TEST(gfn_result_kinds)
{
    install in;
    /* int / void / pointer / bool: kind 0, eax */
    g_rec.ret.eax = 0xFFFFFFFEu;   /* int -2 */
    fnvnx_guestfn<int(void)> fi((uintptr_t)0x30u);
    CHECK_EQ(fi(), -2);
    CHECK_EQ(g_rec.kind, FNVNX_BND_RET_INT);
    fnvnx_guestfn<long(void)> fl((uintptr_t)0x31u);
    g_rec.ret.eax = (fnvnx_gword)(uintptr_t)(sizeof(void *) > 4 ? (uintptr_t)-5 : 0xFFFFFFFBu);
    CHECK_EQ(fl(), -5);
    CHECK_EQ(g_rec.kind, FNVNX_BND_RET_INT);
    fnvnx_guestfn<unsigned(void)> fu((uintptr_t)0x32u);
    g_rec.ret.eax = 0xFFFFFFFFu;
    CHECK(fu() == 0xFFFFFFFFu);
    fnvnx_guestfn<void(void)> fv((uintptr_t)0x33u);
    fv();
    CHECK_EQ(g_rec.kind, FNVNX_BND_RET_INT);
    fnvnx_guestfn<void *(void)> fp((uintptr_t)0x34u);
    g_rec.ret.eax = (fnvnx_gword)0x5000u;
    CHECK(fp() == (void *)(uintptr_t)0x5000u);
    CHECK_EQ(g_rec.kind, FNVNX_BND_RET_INT);
    fnvnx_guestfn<bool(void)> fb((uintptr_t)0x35u);
    g_rec.ret.eax = 0x100u;   /* only al counts */
    CHECK(fb() == false);
    g_rec.ret.eax = 0x01u;
    CHECK(fb() == true);
    fnvnx_guestfn<short(void)> fs((uintptr_t)0x36u);
    g_rec.ret.eax = 0xFFFFu;
    CHECK_EQ(fs(), -1);
    /* 64-bit: kind 1, edx:eax */
    fnvnx_guestfn<long long(void)> f64((uintptr_t)0x37u);
    g_rec.ret.eax = 0x55667788u, g_rec.ret.edx = 0x11223344u;
    CHECK(f64() == 0x1122334455667788ll);
    CHECK_EQ(g_rec.kind, FNVNX_BND_RET_I64);
    g_rec.ret.eax = 0xFFFFFFFEu, g_rec.ret.edx = 0xFFFFFFFFu;
    CHECK(f64() == -2ll);
    fnvnx_guestfn<unsigned long long(void)> fu64((uintptr_t)0x38u);
    CHECK(fu64() == 0xFFFFFFFFFFFFFFFEull);
    CHECK_EQ(g_rec.kind, FNVNX_BND_RET_I64);
    /* float / double: kind 2, st0 */
    g_rec.ret.st0 = -2.5;
    fnvnx_guestfn<double(void)> fd((uintptr_t)0x39u);
    CHECK(fd() == -2.5);
    CHECK_EQ(g_rec.kind, FNVNX_BND_RET_ST0);
    fnvnx_guestfn<float(void)> ff((uintptr_t)0x3Au);
    g_rec.ret.st0 = 0.25;
    CHECK(ff() == 0.25f);
    CHECK_EQ(g_rec.kind, FNVNX_BND_RET_ST0);
}

TEST(gfn_com_call)
{
    install in;
    /* an object of the game: [vtable pointer], vtable entries are the method addresses */
    uintptr_t vt[8] = {0, 0, 0x7000u, 0x7100u, 0x7200u, 0x7300u, 0, 0};
    struct { uintptr_t *vtbl; } obj = {vt};
    g_rec.ret.eax = 0x80004005u;
    long hr = fnvnx_guest_com_call<long>(&obj, 4, (unsigned)5, (void *)(uintptr_t)0x9000u, -1);
    CHECK_EQ(g_rec.calls, 1);
    CHECK_EQ(g_rec.addr, 0x7200);
    CHECK_EQ(g_rec.nargs, 4);   /* this first, as every COM method */
    CHECK(g_rec.w[0] == (uint32_t)(uintptr_t)&obj);
    CHECK(g_rec.w[1] == 5 && g_rec.w[2] == 0x9000u && g_rec.w[3] == 0xFFFFFFFFu);
    CHECK_EQ(g_rec.ecx, (uint32_t)(uintptr_t)&obj);
    CHECK_EQ(g_rec.kind, FNVNX_BND_RET_INT);
    CHECK((uint32_t)hr == 0x80004005u);
    fnvnx_guest_com_call<void>(&obj, 2);
    CHECK_EQ(g_rec.addr, 0x7000);
    CHECK_EQ(g_rec.nargs, 1);
}

TEST(gfn_native_through_host_boundary)
{
    /* the host implementation of guest_invoke calls "the game" natively with the words as integer arguments */
    fnvnx_guestfn<int(int, int)> add((uintptr_t)&native_add);
    CHECK_EQ(add(2, 3), 5);
    CHECK_EQ(add(-4, 1), -3);
    fnvnx_guestfn<long(void *, unsigned, unsigned long, long)> proc((uintptr_t)&native_proc);
    CHECK_EQ(proc((void *)(uintptr_t)100, 3, 20, -7), 100 + 30 + 20 - 7);
    int v = 6;
    fnvnx_guestfn<int(const int *, int)> deref((void *)&native_deref);
    CHECK_EQ(deref(&v, 7), 42);
    CHECK(g_bnd_invokes > 0);
}
