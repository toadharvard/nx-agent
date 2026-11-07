/* test_mem.cpp - the memory lane's recorder (platform/win32/memtrack.cpp, tools/perf/MEM.md): heap, virtual and
 * D3D9 resources through a fake d3d9.dll (its vtables in shim/include/d3d9.h's slot order), the peak, failures, the
 * site lines and the summary. Recording is stopped at the end, so the other tests run without it. */
#include "s4_test.h"

#include <string>

static std::string g_lines;
static void sink(const char *l) { g_lines += l; g_lines += '\n'; }

/* a fake backend: objects are {vtable, refs}; the vtables are 128 slots like the recorder's copies */
struct FakeObj { void **vt; long refs; };
static void *g_res_vt[128], *g_dev_vt[128], *g_d3d_vt[128];
static unsigned long f_release(void *s) { return (unsigned long)--((FakeObj *)s)->refs; }
static long f_tex(void *, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, void **pp, void *)
{
    *pp = new FakeObj{g_res_vt, 1};
    return 0;
}
static long f_vb(void *, uint32_t len, uint32_t, uint32_t, uint32_t, void **pp, void *)
{
    if (len == 0xFFFFFFFFu) return (long)(int32_t)0x8007000E;   /* E_OUTOFMEMORY */
    *pp = new FakeObj{g_res_vt, 1};
    return 0;
}
static long f_cube(void *, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, void **pp, void *)
{
    *pp = new FakeObj{g_res_vt, 1};
    return 0;
}
static long f_create_device(void *, uint32_t, uint32_t, void *, uint32_t, void *, void **pp)
{
    *pp = new FakeObj{g_dev_vt, 1};
    return 0;
}
static void *f_create9(unsigned) { return new FakeObj{g_d3d_vt, 1}; }
static FARPROC fake_resolve(const char *proc)
{
    return strcmp(proc, "Direct3DCreate9") ? (FARPROC)NULL : (FARPROC)(void *)f_create9;
}

typedef long (*tex_fn)(void *, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, void **, void *);
typedef long (*vb_fn)(void *, uint32_t, uint32_t, uint32_t, uint32_t, void **, void *);
typedef long (*cube_fn)(void *, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, void **, void *);
typedef long (*cd_fn)(void *, uint32_t, uint32_t, void *, uint32_t, void *, void **);
typedef unsigned long (*rel_fn)(void *);
static void **vt(void *o) { return *(void ***)o; }

TEST(mem_lane_heap_virtual_gpu)
{
    unsigned long long live0[3], live[3], peak, fails0, fails;
    fnvnx_mem_start(0, sink);   /* no sampler: the test flushes itself */
    fnvnx_mem_counts(live0, NULL, &fails0);
    /* heap: 3 x 1 MiB, one freed, one grown to 2 MiB */
    HANDLE h = GetProcessHeap();
    void *a = HeapAlloc(h, 0, 1 << 20), *b = HeapAlloc(h, 0, 1 << 20), *c = HeapAlloc(h, HEAP_ZERO_MEMORY, 1 << 20);
    CHECK(a && b && c);
    HeapFree(h, 0, b);
    c = HeapReAlloc(h, 0, c, 2 << 20);
    CHECK(c != NULL);
    fnvnx_mem_counts(live, NULL, NULL);
    CHECK_EQ(live[0] - live0[0], 3ull << 20);
    /* virtual: reserve 4 MiB, commit 2 MiB, decommit 1 MiB */
    char *v = (char *)VirtualAlloc(NULL, 4 << 20, MEM_RESERVE, PAGE_READWRITE);
    CHECK(v != NULL);
    CHECK(VirtualAlloc(v, 2 << 20, MEM_COMMIT, PAGE_READWRITE) == v);
    CHECK(VirtualAlloc(v, 1 << 20, MEM_COMMIT, PAGE_READWRITE) == v);   /* already committed: not counted twice */
    fnvnx_mem_counts(live, NULL, NULL);
    CHECK_EQ(live[1] - live0[1], 2ull << 20);
    CHECK(VirtualFree(v + (1 << 20), 1 << 20, MEM_DECOMMIT));
    fnvnx_mem_counts(live, NULL, NULL);
    CHECK_EQ(live[1] - live0[1], 1ull << 20);
    /* gpu: a fake d3d9 module (the hook keys on the proc name), through LoadLibrary / GetProcAddress as the engine does */
    g_res_vt[2] = (void *)f_release;
    g_dev_vt[2] = g_d3d_vt[2] = (void *)f_release;
    g_dev_vt[23] = (void *)f_tex;
    g_dev_vt[25] = (void *)f_cube;
    g_dev_vt[26] = (void *)f_vb;
    g_d3d_vt[16] = (void *)f_create_device;
    fnvnx_register_module("memtest_d3d9.dll", fake_resolve);   /* not d3d9.dll: test_misc checks it is absent */
    HMODULE m = LoadLibraryA("memtest_d3d9.dll");
    CHECK(m != NULL);
    void *(*create9)(unsigned) = (void *(*)(unsigned))(void *)GetProcAddress(m, "Direct3DCreate9");
    CHECK(create9 != NULL && (void *)create9 != (void *)f_create9);   /* the recorder's wrapper */
    void *d3d = create9(32), *dev = NULL, *tex = NULL, *cube = NULL, *vb = NULL;
    CHECK(((cd_fn)vt(d3d)[16])(d3d, 0, 1, NULL, 0, NULL, &dev) == 0 && dev);
    /* 256x256 A8R8G8B8, full chain: 4 * (65536 + 16384 + ... + 1) = 349524 bytes */
    CHECK(((tex_fn)vt(dev)[23])(dev, 256, 256, 0, 0, 21, 0, &tex, NULL) == 0 && tex);
    /* cube 64, 1 level, DXT1: 6 faces x 16 x 16 blocks x 8 bytes = 12288 */
    CHECK(((cube_fn)vt(dev)[25])(dev, 64, 1, 0, 0x31545844u, 0, &cube, NULL) == 0 && cube);
    CHECK(((vb_fn)vt(dev)[26])(dev, 65536, 0, 0, 0, &vb, NULL) == 0 && vb);
    void *none = NULL;
    CHECK(((vb_fn)vt(dev)[26])(dev, 0xFFFFFFFFu, 0, 0, 0, &none, NULL) < 0);   /* a failure */
    fnvnx_mem_counts(live, NULL, NULL);
    CHECK_EQ(live[2] - live0[2], 349524ull + 12288ull + 65536ull);
    ((rel_fn)vt(tex)[2])(tex);   /* refs 1 -> 0: freed */
    ((rel_fn)vt(vb)[2])(vb);
    fnvnx_mem_counts(live, NULL, NULL);
    CHECK_EQ(live[2] - live0[2], 12288ull);
    /* heap failures: HeapAlloc and VirtualAlloc that cannot succeed */
    CHECK(HeapAlloc(h, 0, (SIZE_T)-1 / 2) == NULL);
    CHECK(VirtualAlloc(NULL, (SIZE_T)-1 / 2 & ~(SIZE_T)0xFFFF, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE) == NULL);
    fnvnx_mem_counts(live, &peak, &fails);
    CHECK_EQ(fails - fails0, 3ull);
    CHECK(peak >= (3ull << 20) + (2ull << 20));
    fnvnx_mem_flush();
    CHECK(g_lines.find("mem: fail gpu 4294967295") != std::string::npos);
    CHECK(g_lines.find("mem: fail heap") != std::string::npos);
    CHECK(g_lines.find("mem: site gpu 0x") != std::string::npos);
    CHECK(g_lines.find("mem: site virtual 0x") != std::string::npos);
    CHECK(g_lines.find("mem: peak ") != std::string::npos);
    CHECK(g_lines.find("mem: summary heap ") != std::string::npos);
    HeapFree(h, 0, a);
    HeapFree(h, 0, c);
    VirtualFree(v, 0, MEM_RELEASE);
    fnvnx_mem_counts(live, NULL, NULL);
    CHECK_EQ(live[0] - live0[0], 0ull);
    CHECK_EQ(live[1] - live0[1], 0ull);
    fnvnx_mem_stop();
    /* off: nothing is recorded */
    void *z = HeapAlloc(h, 0, 4096);
    fnvnx_mem_counts(live, NULL, NULL);
    CHECK_EQ(live[0] - live0[0], 0ull);
    HeapFree(h, 0, z);
}
