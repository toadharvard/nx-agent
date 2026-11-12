/* test_vm_private.cpp - the file-mapping backend of the product (platform/win32/filemap_private.cpp), vmp_* cases.
 * Compiled into both test binaries; each case first asks the backend identity and returns at once unless the binary
 * links the private backend (make -C tests/s4 private: s4_tests_private, with host_section.cpp: the host
 * implementation of the section services with their documented semantics and limits). The branches the reachability
 * gate proves unreachable end the process in the product fnvnx_reach_violation, which the tests observe in a child process (host_boundary.h REACH_EXPECT), so
 * the cases assert exactly which request reaches which fact. */
#include "s4_test.h"

#include <setjmp.h>
#include <signal.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "../../platform/win32/filemap_backing.h"
#include "../../platform/manifest/reach_local.h"
#include "host_boundary.h"

/* host_section.cpp (the private binary only; the cases run after PRIVATE_ONLY) */
extern "C" int host_section_live_sections(void) __attribute__((weak));
extern "C" int host_section_live_views(void) __attribute__((weak));
extern "C" void host_section_set_service(int on) __attribute__((weak));
#define HFILE REACH_KERNEL32_CreateFileMappingA_HFILE
#define PROT REACH_KERNEL32_CreateFileMappingA_PROT
#define WRITER REACH_KERNEL32_CreateFileMappingA_WRITER

#define VPD "Data\\vmp\\"
#define SYSERR GetLastError()
#define PRIVATE_ONLY() do { if (strcmp(fm_backend_name(), "private")) return; } while (0)

static sigjmp_buf g_jb;
static volatile int g_in_probe;
static void on_segv(int) { if (g_in_probe) siglongjmp(g_jb, 1); signal(SIGSEGV, SIG_DFL); }
static bool faults(volatile unsigned char *p, bool write)
{
    struct sigaction sa, old;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_segv;
    sa.sa_flags = SA_NODEFER;
    sigaction(SIGSEGV, &sa, &old);
    bool f = false;
    g_in_probe = 1;
    if (sigsetjmp(g_jb, 1) == 0) {
        if (write) *p = 0x11; else (void)*p;
    } else {
        f = true;
    }
    g_in_probe = 0;
    sigaction(SIGSEGV, &old, NULL);
    return f;
}
static MEMORY_BASIC_INFORMATION Q(const void *p)
{
    MEMORY_BASIC_INFORMATION m;
    memset(&m, 0xEE, sizeof m);
    if (VirtualQuery(p, &m, sizeof m) != sizeof m) memset(&m, 0, sizeof m);
    return m;
}
static bool map_faults(const void *p, int access)   /* the page map's verdict (fnvnx_win32_vm_fault): true = a violation */
{
    uint32_t code = 0;
    return fnvnx_win32_vm_fault((uintptr_t)p, access, &code) == 0;
}

#define FSIZE 0x23800u   /* 2 whole granules and a partial third one that ends inside a page */
static unsigned char pat(size_t i) { return (unsigned char)(i * 7 + (i >> 8) + 1); }
static void mkfile(const char *p, const unsigned char *d, size_t n)
{
    CreateDirectoryA("Data", NULL);
    CreateDirectoryA("Data\\vmp", NULL);
    HANDLE w = CreateFileA(p, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
    DWORD got = 0;
    WriteFile(w, d, (DWORD)n, &got, NULL);
    CloseHandle(w);
}
static void mkdata(const char *p)
{
    unsigned char *d = (unsigned char *)malloc(FSIZE);
    for (size_t i = 0; i < FSIZE; i++) d[i] = pat(i);
    mkfile(p, d, FSIZE);
    free(d);
}
/* the opens: R (read, share read: no writer can exist), RS (read, share read+write), RW (read+write, share read) */
static HANDLE open_r(const char *p) { return CreateFileA(p, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL); }
static HANDLE open_rs(const char *p) { return CreateFileA(p, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL); }
static HANDLE open_rw(const char *p) { return CreateFileA(p, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL); }
static bool same(const unsigned char *v, size_t off, size_t n)   /* v[0..n) equals the pattern at off, zero past the file */
{
    for (size_t i = 0; i < n; i++)
        if (v[i] != (off + i < FSIZE ? pat(off + i) : 0))
            return false;
    return true;
}
static bool file_is_pattern(const char *p)
{
    HANDLE h = open_rs(p);
    unsigned char *b = (unsigned char *)malloc(FSIZE);
    DWORD got = 0;
    bool ok = ReadFile(h, b, FSIZE, &got, NULL) && got == FSIZE && same(b, 0, FSIZE);
    free(b);
    CloseHandle(h);
    return ok;
}

/* a small PE image: headers, .text (R-X, raw 0x200 at 0x200), .data (RW, 0x2000 virtual, raw 0x200 at 0x400) */
static void p16(unsigned char *p, unsigned v) { p[0] = (unsigned char)v; p[1] = (unsigned char)(v >> 8); }
static void p32(unsigned char *p, unsigned v) { p16(p, v & 0xFFFF); p16(p + 2, v >> 16); }
static void build_pe(unsigned char *f /* 0x600 */)
{
    memset(f, 0, 0x600);
    f[0] = 'M'; f[1] = 'Z';
    p32(f + 0x3C, 0x80);
    unsigned char *nt = f + 0x80;
    memcpy(nt, "PE\0\0", 4);
    p16(nt + 4, 0x14C); p16(nt + 6, 2); p16(nt + 20, 0xE0); p16(nt + 22, 0x102);
    unsigned char *oh = nt + 24;
    p16(oh, 0x10B); p32(oh + 28, 0x400000); p32(oh + 32, 0x1000); p32(oh + 36, 0x200); p32(oh + 56, 0x4000); p32(oh + 60, 0x200);
    unsigned char *s = nt + 24 + 0xE0;
    memcpy(s, ".text", 5);
    p32(s + 8, 0x200); p32(s + 12, 0x1000); p32(s + 16, 0x200); p32(s + 20, 0x200); p32(s + 36, 0x60000020);
    s += 40;
    memcpy(s, ".data", 5);
    p32(s + 8, 0x2000); p32(s + 12, 0x2000); p32(s + 16, 0x200); p32(s + 20, 0x400); p32(s + 36, 0xC0000040);
    memcpy(f + 0x200, "TEXTSEC", 7);
    memcpy(f + 0x400, "DATASEC", 7);
}
static void mkpe(const char *p)
{
    unsigned char pe[0x600];
    build_pe(pe);
    mkfile(p, pe, sizeof pe);
}

/* ---- the backing set: every valid flProtect x SEC_* x backing, and what the backend makes of it */
static const DWORD k_bases[] = { PAGE_READONLY, PAGE_READWRITE, PAGE_WRITECOPY, PAGE_EXECUTE_READ, PAGE_EXECUTE_READWRITE, 0x80 /* PAGE_EXECUTE_WRITECOPY */ };
static const DWORD k_secs[] = { 0, SEC_IMAGE, SEC_RESERVE, SEC_COMMIT };

TEST(vmp_backing_set)
{
    PRIVATE_ONLY();
    mkdata(VPD "data.bin");
    mkpe(VPD "img.dll");
    int checked = 0, writers = 0, shared_n = 0;
    for (DWORD base : k_bases)
        for (DWORD sec : k_secs)
            for (int backing = 0; backing < 4; backing++) {   /* 0 pagefile, 1 file R, 2 file RS, 3 file RW */
                bool image = sec == SEC_IMAGE, reserve = sec == SEC_RESERVE, rw = base == PAGE_READWRITE || base == PAGE_EXECUTE_READWRITE;
                const char *path = image ? VPD "img.dll" : VPD "data.bin";
                HANDLE f = backing == 0 ? INVALID_HANDLE_VALUE : backing == 1 ? open_r(path) : backing == 2 ? open_rs(path) : open_rw(path);
                CHECK(f != INVALID_HANDLE_VALUE || backing == 0);
                /* rejected before any backing: SEC_IMAGE of the paging file, SEC_RESERVE of a file, a writable section
                 * over a handle without write access (ACCESS_DENIED) */
                bool rejected = (backing == 0 && image) || (backing != 0 && reserve) || (backing != 0 && rw && backing != 3);
                /* one host section object: the paging file; a writable file section (not an image) */
                bool shared = !rejected && (backing == 0 || (rw && !image));
                /* private copies over a file that writers can change: the proven-unreachable branch WRITER */
                bool writer = !rejected && !shared && (backing == 2 || backing == 3);
                int s0 = host_section_live_sections();
                HANDLE m = NULL;
                SetLastError(0);
                uint32_t fact = REACH_EXPECT(m = CreateFileMappingA(f, NULL, base | sec, 0, backing == 0 ? 0x10000 : 0, NULL));
                int hit = fact != 0 ? 1 : 0;
                bool good = writer ? (fact == WRITER && hit == 1 && m == NULL) : rejected ? (fact == 0 && hit == 0 && m == NULL)
                                                                                           : (fact == 0 && hit == 0 && m != NULL);
                CHECK(good);
                CHECK_EQ(host_section_live_sections(), s0 + (shared && m ? 1 : 0));   /* a section object exists for kinds (1)/(2) only */
                if (!good)
                    fprintf(stderr, "    combination base=%#x sec=%#x backing=%d\n", (unsigned)base, (unsigned)sec, backing);
                writers += writer;
                shared_n += shared;
                if (m) CloseHandle(m);
                if (f != INVALID_HANDLE_VALUE) CloseHandle(f);
                CHECK_EQ(host_section_live_sections(), s0);   /* no view: the object is gone with the last handle */
                checked++;
            }
    CHECK_EQ(checked, 6 * 4 * 4);
    CHECK(writers > 0 && shared_n > 0);
    /* the writer fact is the share-mode fact of the handle */
    HANDLE f = open_rs(VPD "data.bin");
    HANDLE m = NULL;
    CHECK_EQ(REACH_EXPECT(m = CreateFileMappingA(f, NULL, PAGE_READONLY, 0, 0, NULL)), WRITER);
    CHECK(m == NULL);
    CloseHandle(f);
    f = open_rw(VPD "data.bin");   /* write access, share read: private copies would not follow the writer; shared sections do not care */
    CHECK_EQ(REACH_EXPECT(m = CreateFileMappingA(f, NULL, PAGE_WRITECOPY, 0, 0, NULL)), WRITER);
    m = CreateFileMappingA(f, NULL, PAGE_READWRITE, 0, 0, NULL);
    CHECK(m != NULL);
    if (m) CloseHandle(m);
    CloseHandle(f);
    f = open_r(VPD "data.bin");
    m = CreateFileMappingA(f, NULL, PAGE_READONLY, 0, 0, NULL);
    CHECK(m != NULL);
    CloseHandle(m);
    CloseHandle(f);
    /* the section object's size limit (2 GiB) belongs to the creation facts of its kind */
    CHECK_EQ(REACH_EXPECT(m = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 1, 0, NULL)), HFILE);
    m = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, 0x80000000u, NULL);
    CHECK(m != NULL);
    if (m) CloseHandle(m);
    CHECK_EQ(host_section_live_sections(), 0);
}

/* ---- read-only sections: content, protection, ranges, lifetime, errors */
TEST(vmp_readonly_view)
{
    PRIVATE_ONLY();
    mkdata(VPD "ro.bin");
    HANDLE f = open_r(VPD "ro.bin");
    HANDLE m = CreateFileMappingA(f, NULL, PAGE_READONLY, 0, 0, NULL);
    CHECK(m != NULL);
    unsigned char *v = (unsigned char *)MapViewOfFile(m, FILE_MAP_READ, 0, 0, 0);
    CHECK(v != NULL);
    if (v) {
        CHECK(same(v, 0, 0x24000));   /* the file, then zero past its end within the page */
        MEMORY_BASIC_INFORMATION q = Q(v);
        CHECK(q.Type == MEM_MAPPED && q.Protect == PAGE_READONLY && q.AllocationBase == v && q.RegionSize == 0x24000);
        CHECK(faults(v, true) && faults(v + 0x23000, true));
        CHECK(!faults(v, false) && map_faults(v, 1) && !map_faults(v, 0) && map_faults(v, 8));
    }
    /* a view at an offset, of a few bytes: the page holds the file's bytes of the page */
    unsigned char *w = (unsigned char *)MapViewOfFile(m, FILE_MAP_READ, 0, 0x10000, 0x100);
    CHECK(w != NULL && Q(w).RegionSize == 0x1000);
    if (w) CHECK(same(w, 0x10000, 0x1000));
    unsigned char *e = (unsigned char *)MapViewOfFile(m, FILE_MAP_READ, 0, 0x20000, 0);   /* to the end of the section */
    CHECK(e != NULL);
    if (e) {
        CHECK_EQ(Q(e).RegionSize, 0x4000);
        CHECK(same(e, 0x20000, 0x4000));
    }
    /* the section's size rules */
    SetLastError(0);
    CHECK(MapViewOfFile(m, FILE_MAP_READ, 0, 0x1000, 0) == NULL && SYSERR == ERROR_MAPPED_ALIGNMENT);
    CHECK(MapViewOfFile(m, FILE_MAP_READ, 0, 0x30000, 0) == NULL && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(MapViewOfFile(m, FILE_MAP_READ, 0, 0x20000, 0x3800 + 1) == NULL && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(MapViewOfFile(m, FILE_MAP_WRITE, 0, 0, 0) == NULL && SYSERR == ERROR_ACCESS_DENIED);
    CHECK(MapViewOfFile(m, FILE_MAP_COPY, 0, 0, 0) == NULL && SYSERR == ERROR_ACCESS_DENIED);
    CHECK(MapViewOfFile(m, FILE_MAP_READ | FILE_MAP_EXECUTE, 0, 0, 0) == NULL && SYSERR == ERROR_ACCESS_DENIED);
    CHECK(CreateFileMappingA(f, NULL, PAGE_READONLY, 0, FSIZE + 1, NULL) == NULL && SYSERR == ERROR_NOT_ENOUGH_MEMORY);
    HANDLE m2 = CreateFileMappingA(f, NULL, PAGE_READONLY, 0, 0x11000, NULL);   /* a smaller section */
    CHECK(m2 != NULL);
    unsigned char *s = (unsigned char *)MapViewOfFile(m2, FILE_MAP_READ, 0, 0, 0);
    CHECK(s != NULL && Q(s).RegionSize == 0x11000 && same(s, 0, 0x11000));
    CHECK(MapViewOfFile(m2, FILE_MAP_READ, 0, 0x10000, 0x1001) == NULL && SYSERR == ERROR_INVALID_PARAMETER);
    /* the views outlive the mapping handles and the file handle; unmapping in any order */
    CloseHandle(m2);
    CloseHandle(m);
    CloseHandle(f);
    if (s) CHECK(same(s, 0, 0x11000));
    if (v) CHECK(same(v, 0, 0x24000));
    CHECK(UnmapViewOfFile(w));
    CHECK(Q(w).State == MEM_FREE);
    if (v) CHECK(same(v, 0, 0x24000));
    CHECK(UnmapViewOfFile(v));
    CHECK(UnmapViewOfFile(s));
    CHECK(UnmapViewOfFile(e));
    SetLastError(0);
    CHECK(!UnmapViewOfFile(e) && SYSERR == ERROR_INVALID_ADDRESS);
    CHECK(file_is_pattern(VPD "ro.bin"));
}

/* ---- copy-on-write sections and FILE_MAP_COPY views */
TEST(vmp_copy_views)
{
    PRIVATE_ONLY();
    mkdata(VPD "cp.bin");
    for (int exec = 0; exec < 2; exec++) {
        HANDLE f = open_r(VPD "cp.bin");
        HANDLE m = CreateFileMappingA(f, NULL, exec ? 0x80 : PAGE_WRITECOPY, 0, 0, NULL);
        CHECK(m != NULL);
        DWORD x = exec ? FILE_MAP_EXECUTE : 0, wc = exec ? 0x80 : PAGE_WRITECOPY;
        unsigned char *a = (unsigned char *)MapViewOfFile(m, FILE_MAP_COPY | x, 0, 0, 0);
        unsigned char *b = (unsigned char *)MapViewOfFile(m, FILE_MAP_COPY | x, 0, 0, 0);
        unsigned char *w = (unsigned char *)MapViewOfFile(m, FILE_MAP_WRITE | x, 0, 0, 0);   /* a write view of a copy section is a copy view */
        unsigned char *r = (unsigned char *)MapViewOfFile(m, FILE_MAP_READ | x, 0, 0, 0);
        CHECK(a && b && w && r && a != b && a != w && a != r);
        if (a && b && w && r) {
            CHECK(same(a, 0, 0x24000) && same(b, 0, 0x24000) && same(r, 0, 0x24000));
            MEMORY_BASIC_INFORMATION q = Q(a);
            CHECK(q.Type == MEM_MAPPED && q.Protect == wc && q.RegionSize == 0x24000);
            CHECK_EQ(Q(w).Protect, wc);
            CHECK_EQ(Q(r).Protect, exec ? PAGE_EXECUTE_READ : PAGE_READONLY);
            CHECK(!faults(a, true) && !faults(a + 0x23800, true) && !faults(w, true));
            a[0x10] ^= 0xFF;
            a[0x23000] = 0x5A;
            CHECK(a[0x10] != pat(0x10) && a[0x23000] == 0x5A);
            CHECK(b[0x10] == pat(0x10) && b[0x23000] == pat(0x23000));   /* other views do not see the copy */
            CHECK(r[0x10] == pat(0x10) && w[0x10] == pat(0x10));
            CHECK(faults(r, true));                                       /* the read view stays read-only */
            CHECK(!faults(b, true));
            CHECK(!map_faults(a, 1) && map_faults(r, 1) && (!map_faults(r, 8) == (exec != 0)));
        }
        /* the file never sees a copy */
        CHECK(file_is_pattern(VPD "cp.bin"));
        CloseHandle(m);
        CloseHandle(f);
        if (a) CHECK(UnmapViewOfFile(a) && (!b || (b[0x10] == pat(0x10))));   /* the copy's pages go; the others stay */
        if (w) CHECK(UnmapViewOfFile(w));
        if (b) CHECK(UnmapViewOfFile(b));
        if (r) CHECK(UnmapViewOfFile(r));
        CHECK(file_is_pattern(VPD "cp.bin"));
        /* a view made after the copy's death is the file again */
        f = open_r(VPD "cp.bin");
        m = CreateFileMappingA(f, NULL, exec ? 0x80 : PAGE_WRITECOPY, 0, 0, NULL);
        unsigned char *c = (unsigned char *)MapViewOfFile(m, FILE_MAP_COPY | x, 0, 0x10000, 0);
        CHECK(c != NULL && same(c, 0x10000, 0x14000));
        if (c) CHECK(UnmapViewOfFile(c));
        CloseHandle(m);
        CloseHandle(f);
    }
}

/* ---- PAGE_EXECUTE_READ sections */
TEST(vmp_execute_read)
{
    PRIVATE_ONLY();
    mkdata(VPD "xr.bin");
    HANDLE f = open_r(VPD "xr.bin");
    HANDLE m = CreateFileMappingA(f, NULL, PAGE_EXECUTE_READ, 0, 0, NULL);
    CHECK(m != NULL);
    unsigned char *x = (unsigned char *)MapViewOfFile(m, FILE_MAP_READ | FILE_MAP_EXECUTE, 0, 0, 0);
    unsigned char *r = (unsigned char *)MapViewOfFile(m, FILE_MAP_READ, 0, 0, 0);
    CHECK(x != NULL && r != NULL);
    if (x && r) {
        CHECK(same(x, 0, 0x24000) && same(r, 0, 0x24000));
        CHECK_EQ(Q(x).Protect, PAGE_EXECUTE_READ);
        CHECK_EQ(Q(r).Protect, PAGE_READONLY);
        CHECK(faults(x, true) && faults(r, true) && !faults(x, false));
        CHECK(!map_faults(x, 8) && map_faults(r, 8) && map_faults(x, 1));   /* the map permits an execute of x only */
    }
    SetLastError(0);
    CHECK(MapViewOfFile(m, FILE_MAP_WRITE, 0, 0, 0) == NULL && SYSERR == ERROR_ACCESS_DENIED);
    CHECK(MapViewOfFile(m, FILE_MAP_COPY, 0, 0, 0) == NULL && SYSERR == ERROR_ACCESS_DENIED);
    CloseHandle(m);
    CloseHandle(f);
    if (x) CHECK(same(x, 0, 0x24000) && UnmapViewOfFile(x));
    if (r) CHECK(UnmapViewOfFile(r));
}

/* ---- SEC_IMAGE sections */
TEST(vmp_image)
{
    PRIVATE_ONLY();
    mkpe(VPD "img2.dll");
    const DWORD prots[] = { PAGE_READONLY, PAGE_EXECUTE_READ, PAGE_WRITECOPY };
    for (DWORD prot : prots) {
        HANDLE f = open_r(VPD "img2.dll");
        HANDLE m = CreateFileMappingA(f, NULL, prot | SEC_IMAGE, 0, 0, NULL);
        CHECK(m != NULL);
        unsigned char *v = (unsigned char *)MapViewOfFile(m, FILE_MAP_READ, 0, 0, 0);
        unsigned char *u = (unsigned char *)MapViewOfFile(m, FILE_MAP_READ, 0, 0, 0);
        CHECK(v != NULL && u != NULL && u != v);
        if (v && u) {
            CHECK(v[0] == 'M' && v[1] == 'Z' && !memcmp(v + 0x1000, "TEXTSEC", 7) && !memcmp(v + 0x2000, "DATASEC", 7));
            CHECK(v[0x1100] == 0 && v[0x3FFF] == 0);
            MEMORY_BASIC_INFORMATION q = Q(v);
            CHECK(q.Type == MEM_IMAGE && q.AllocationBase == v && q.Protect == PAGE_READONLY && q.RegionSize == 0x1000);
            q = Q(v + 0x1000);
            CHECK(q.Type == MEM_IMAGE && q.Protect == PAGE_EXECUTE_READ && q.RegionSize == 0x1000);
            q = Q(v + 0x2000);
            CHECK(q.Protect == PAGE_WRITECOPY && q.RegionSize == 0x2000);
            CHECK(faults(v, true) && faults(v + 0x1000, true) && !faults(v + 0x2000, true) && !faults(v + 0x3000, true));
            CHECK(!map_faults(v + 0x1000, 8) && map_faults(v + 0x2000, 8) && map_faults(v, 1));
            /* the data the first view wrote is private to it */
            CHECK(!memcmp(u + 0x2000, "DATASEC", 7) && u[0x2000 + 0x3FF] == 0);
            v[0x2000] = 'X';
            CHECK(u[0x2000] == 'D');
            SetLastError(0);
            CHECK(MapViewOfFile(m, FILE_MAP_READ, 0, 0x10000, 0) == NULL && SYSERR != 0);
        }
        CloseHandle(m);
        CloseHandle(f);
        if (v) CHECK(v[0x2000] == 'X' && UnmapViewOfFile(v));
        if (u) CHECK(!memcmp(u + 0x2000, "DATASEC", 7) && UnmapViewOfFile(u));
        /* the file is untouched, and a later view is the pristine image */
        f = open_r(VPD "img2.dll");
        m = CreateFileMappingA(f, NULL, prot | SEC_IMAGE, 0, 0, NULL);
        unsigned char *c = (unsigned char *)MapViewOfFile(m, FILE_MAP_READ, 0, 0, 0);
        CHECK(c != NULL && !memcmp(c + 0x2000, "DATASEC", 7));
        if (c) CHECK(UnmapViewOfFile(c));
        CloseHandle(m);
        CloseHandle(f);
    }
    /* not an image: refused before any backing exists */
    mkdata(VPD "notpe.bin");
    HANDLE f = open_r(VPD "notpe.bin");
    SetLastError(0);
    CHECK(CreateFileMappingA(f, NULL, PAGE_READONLY | SEC_IMAGE, 0, 0, NULL) == NULL && SYSERR == ERROR_BAD_EXE_FORMAT);
    CloseHandle(f);
}

/* ---- paging-file-backed sections: one host section object, views that share their pages */
TEST(vmp_pagefile_shared)
{
    PRIVATE_ONLY();
    int s0 = host_section_live_sections(), v0 = host_section_live_views();
    HANDLE m = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, FSIZE, NULL);
    CHECK(m != NULL);
    CHECK_EQ(host_section_live_sections(), s0 + 1);
    unsigned char *a = (unsigned char *)MapViewOfFile(m, FILE_MAP_WRITE, 0, 0, 0);
    unsigned char *b = (unsigned char *)MapViewOfFile(m, FILE_MAP_ALL_ACCESS, 0, 0, 0x10000);   /* overlaps a */
    unsigned char *c = (unsigned char *)MapViewOfFile(m, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0x10000, 0);
    CHECK(a && b && c && a != b && a != c && b != c);
    CHECK_EQ(host_section_live_views(), v0 + 3);
    if (a && b && c) {
        bool zero = true;
        for (size_t i = 0; i < 0x24000; i++) zero = zero && a[i] == 0;   /* zero filled, to the end of the page */
        CHECK(zero);
        MEMORY_BASIC_INFORMATION q = Q(a);
        CHECK(q.Type == MEM_MAPPED && q.Protect == PAGE_READWRITE && q.AllocationBase == a && q.RegionSize == 0x24000);
        CHECK(Q(b).RegionSize == 0x10000 && Q(c).RegionSize == 0x14000);
        CHECK(!faults(a, true) && !faults(a + 0x23000, true) && !map_faults(a, 1));
        a[5] = 0x77;
        CHECK_EQ(b[5], 0x77);
        b[0x100] = 0x33;
        CHECK_EQ(a[0x100], 0x33);
        c[0x10] = 0x99;
        CHECK_EQ(a[0x10010], 0x99);
        a[0x23800 - 1] = 0x5A;
        CHECK_EQ(c[0x13800 - 1], 0x5A);
    }
    /* the views outlive the handle, unmapping in any order; the object lives with the last view */
    CloseHandle(m);
    CHECK_EQ(host_section_live_sections(), s0 + 1);
    if (a) CHECK_EQ(a[5], 0x77);
    CHECK(UnmapViewOfFile(b));
    CHECK(Q(b).State == MEM_FREE);
    if (a) CHECK_EQ(a[0x100], 0x33);
    CHECK(UnmapViewOfFile(a));
    CHECK_EQ(host_section_live_sections(), s0 + 1);
    if (c) CHECK_EQ(c[0x10], 0x99);
    CHECK(UnmapViewOfFile(c));
    CHECK_EQ(host_section_live_sections(), s0);
    CHECK_EQ(host_section_live_views(), v0);
    SetLastError(0);
    CHECK(!UnmapViewOfFile(c) && SYSERR == ERROR_INVALID_ADDRESS);
    /* a named section: a second handle of the same object, views of both alias */
    HANDLE m1 = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, 0x2000, "vmp_pf_named");
    HANDLE m2 = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, 0x2000, "vmp_pf_named");
    CHECK(m1 && m2 && host_section_live_sections() == s0 + 1);
    unsigned char *x = (unsigned char *)MapViewOfFile(m1, FILE_MAP_WRITE, 0, 0, 0), *y = (unsigned char *)MapViewOfFile(m2, FILE_MAP_WRITE, 0, 0, 0);
    CHECK(x && y && x != y);
    if (x && y) { x[0x1FFF] = 0x42; CHECK_EQ(y[0x1FFF], 0x42); }
    CloseHandle(m1); CloseHandle(m2);
    if (x) CHECK(UnmapViewOfFile(x));
    if (y) CHECK(UnmapViewOfFile(y));
    CHECK_EQ(host_section_live_sections(), s0);
    /* execute-read-write: the page map records it (the OS pages are the service's read-write) */
    m = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_EXECUTE_READWRITE, 0, 0x1000, NULL);
    unsigned char *e = (unsigned char *)MapViewOfFile(m, FILE_MAP_WRITE | FILE_MAP_EXECUTE, 0, 0, 0);
    CHECK(m != NULL && e != NULL);
    if (e) {
        CHECK_EQ(Q(e).Protect, PAGE_EXECUTE_READWRITE);
        CHECK(!map_faults(e, 8) && !map_faults(e, 1));
        CHECK(UnmapViewOfFile(e));
    }
    if (m) CloseHandle(m);
    CHECK_EQ(host_section_live_sections(), s0);
}

/* ---- file-backed read-write sections: content loaded at creation, written back at flush / unmap */
static unsigned file_byte(const char *p, unsigned off)
{
    HANDLE h = open_rs(p);
    unsigned char c = 0xEE;
    DWORD got = 0;
    SetFilePointer(h, (LONG)off, NULL, FILE_BEGIN);
    if (!ReadFile(h, &c, 1, &got, NULL) || got != 1) c = 0xEE;
    CloseHandle(h);
    return c;
}
TEST(vmp_file_shared)
{
    PRIVATE_ONLY();
    int s0 = host_section_live_sections();
    mkdata(VPD "fs.bin");
    HANDLE f = open_rw(VPD "fs.bin");
    HANDLE m = CreateFileMappingA(f, NULL, PAGE_READWRITE, 0, 0, NULL);
    CHECK(m != NULL);
    CHECK_EQ(host_section_live_sections(), s0 + 1);
    unsigned char *v = (unsigned char *)MapViewOfFile(m, FILE_MAP_WRITE, 0, 0, 0);
    unsigned char *w = (unsigned char *)MapViewOfFile(m, FILE_MAP_WRITE, 0, 0x10000, 0);
    CHECK(v && w);
    if (v && w) {
        CHECK(same(v, 0, 0x24000) && same(w, 0x10000, 0x14000));   /* the file as of the creation, zero past its end */
        CHECK(Q(v).Type == MEM_MAPPED && Q(v).Protect == PAGE_READWRITE);
        unsigned orig = pat(0x10010);
        w[0x10] ^= 0xFF;
        CHECK_EQ(v[0x10010], (unsigned char)(orig ^ 0xFF));   /* the views share their pages */
        v[0x20] ^= 0xFF;
        CHECK_EQ(file_byte(VPD "fs.bin", 0x10010), orig);     /* nothing goes to the file before a flush or an unmap */
        CHECK_EQ(file_byte(VPD "fs.bin", 0x20), (unsigned)pat(0x20));
        CHECK(FlushViewOfFile(v + 0x20, 1));
        CHECK_EQ(file_byte(VPD "fs.bin", 0x20), (unsigned)(pat(0x20) ^ 0xFF));
        CHECK(UnmapViewOfFile(w));
        CHECK_EQ(file_byte(VPD "fs.bin", 0x10010), (unsigned)(orig ^ 0xFF));   /* the unmap writes the view back */
    }
    CloseHandle(m);
    CloseHandle(f);
    CHECK_EQ(host_section_live_sections(), s0 + 1);   /* the view holds the section */
    if (v) {
        v[0x30] = 0x66;
        CHECK(UnmapViewOfFile(v));
        CHECK_EQ(file_byte(VPD "fs.bin", 0x30), 0x66u);
        CHECK_EQ(file_byte(VPD "fs.bin", 0x23800 - 1), (unsigned)pat(0x23800 - 1));
    }
    CHECK_EQ(host_section_live_sections(), s0);
    /* a section larger than the file extends it, zero filled; the file ends where the section does */
    f = open_rw(VPD "fs.bin");
    m = CreateFileMappingA(f, NULL, PAGE_READWRITE, 0, FSIZE + 0x2000, NULL);
    unsigned char *x = m ? (unsigned char *)MapViewOfFile(m, FILE_MAP_WRITE, 0, 0, 0) : NULL;
    CHECK(x != NULL);
    if (x) {
        CHECK(x[FSIZE] == 0 && x[FSIZE + 0x1FFF] == 0 && x[0x30] == 0x66);
        x[FSIZE + 0x1FFF] = 0x7B;
        CHECK(UnmapViewOfFile(x));
        CHECK_EQ(file_byte(VPD "fs.bin", FSIZE + 0x1FFF), 0x7Bu);
    }
    if (m) CloseHandle(m);
    CloseHandle(f);
    CHECK_EQ(host_section_live_sections(), s0);
}

/* ---- the requests the section service cannot express: each ends in the creation fact of its section's kind */
TEST(vmp_shared_reach)
{
    PRIVATE_ONLY();
    mkdata(VPD "rch.bin");
    struct { const char *name; DWORD base, sec; bool file; uint32_t fact; } k[] = {
        { "pagefile rw", PAGE_READWRITE, 0, false, HFILE },
        { "pagefile xrw", PAGE_EXECUTE_READWRITE, SEC_COMMIT, false, HFILE },
        { "pagefile ro", PAGE_READONLY, 0, false, HFILE },
        { "pagefile wc", PAGE_WRITECOPY, 0, false, HFILE },
        { "pagefile reserve", PAGE_READWRITE, SEC_RESERVE, false, HFILE },
        { "file rw", PAGE_READWRITE, 0, true, PROT },
        { "file xrw", PAGE_EXECUTE_READWRITE, 0, true, PROT },
    };
    for (auto &c : k) {
        HANDLE f = c.file ? open_rw(VPD "rch.bin") : INVALID_HANDLE_VALUE;
        HANDLE m = CreateFileMappingA(f, NULL, c.base | c.sec, 0, c.file ? 0 : 0x10000, NULL);
        CHECK(m != NULL);
        if (!m) { fprintf(stderr, "    %s\n", c.name); continue; }
        int v0 = host_section_live_views();
        bool rw = c.base == PAGE_READWRITE || c.base == PAGE_EXECUTE_READWRITE, resv = c.sec == SEC_RESERVE;
        /* every view that is not a plain write view: read-only, execute-read, copy (and the write view of a
         * copy-on-write section, a copy view), any view of a SEC_RESERVE section */
        CHECK_EQ(REACH_EXPECT(MapViewOfFile(m, FILE_MAP_READ, 0, 0, 0)), c.fact);
        if (c.base == PAGE_EXECUTE_READWRITE)
            CHECK_EQ(REACH_EXPECT(MapViewOfFile(m, FILE_MAP_READ | FILE_MAP_EXECUTE, 0, 0, 0)), c.fact);
        if (rw || c.base == PAGE_WRITECOPY)
            CHECK_EQ(REACH_EXPECT(MapViewOfFile(m, FILE_MAP_COPY, 0, 0, 0)), c.fact);
        if (c.base == PAGE_WRITECOPY)
            CHECK_EQ(REACH_EXPECT(MapViewOfFile(m, FILE_MAP_WRITE, 0, 0, 0)), c.fact);
        if (resv)
            CHECK_EQ(REACH_EXPECT(MapViewOfFile(m, FILE_MAP_WRITE, 0, 0, 0)), c.fact);
        /* the contract's errors above the request keep their answers (no fact) */
        SetLastError(0);
        if (c.base == PAGE_READONLY)
            CHECK(MapViewOfFile(m, FILE_MAP_WRITE, 0, 0, 0) == NULL && SYSERR == ERROR_ACCESS_DENIED);
        SetLastError(0);
        CHECK(MapViewOfFile(m, FILE_MAP_WRITE, 0, 0x1000, 0) == NULL && SYSERR == (c.base == PAGE_READONLY ? ERROR_ACCESS_DENIED : ERROR_MAPPED_ALIGNMENT));
        CHECK_EQ(host_section_live_views(), v0);   /* nothing was mapped or left behind by the requests above */
        if (rw && !resv) {
            unsigned char *v = (unsigned char *)MapViewOfFile(m, FILE_MAP_WRITE, 0, 0, 0);
            CHECK(v != NULL);
            if (v) {
                DWORD old = 0xABCD;
                /* VirtualProtect of the view's pages: the contract's errors first, then the fact */
                SetLastError(0);
                CHECK(!VirtualProtect(v, 0, PAGE_READONLY, &old) && SYSERR == ERROR_INVALID_PARAMETER);
                CHECK(!VirtualProtect(v, 0x1000, 3, &old) && SYSERR == ERROR_INVALID_PARAMETER);
                CHECK(!VirtualProtect(v, 0x1000, PAGE_READONLY, NULL) && SYSERR == ERROR_NOACCESS);
                CHECK(!VirtualProtect(v + 0x100000, 0x1000, PAGE_READONLY, &old) && SYSERR == ERROR_INVALID_ADDRESS);
                bool ok = false;
                CHECK_EQ(REACH_EXPECT(ok = VirtualProtect(v + 0x1000, 0x1000, PAGE_READONLY, &old)), c.fact);
                CHECK(!ok && old == 0xABCD);
                CHECK_EQ(REACH_EXPECT(VirtualProtect(v, 0x1000, PAGE_READWRITE, &old)), c.fact);   /* even the protection it has */
                CHECK_EQ(Q(v).Protect, PAGE_READWRITE);   /* no lock was left held, nothing changed */
                CHECK(!faults(v, true));
                CHECK(UnmapViewOfFile(v));
            }
        }
        CloseHandle(m);
        if (f != INVALID_HANDLE_VALUE) CloseHandle(f);
    }
    CHECK_EQ(host_section_live_sections(), 0);
    /* VirtualProtect of private-page views and of ordinary memory is untouched */
    HANDLE f = open_r(VPD "rch.bin");
    HANDLE m = CreateFileMappingA(f, NULL, PAGE_READONLY, 0, 0, NULL);
    unsigned char *r = (unsigned char *)MapViewOfFile(m, FILE_MAP_READ, 0, 0, 0);
    DWORD old = 0;
    CHECK(r != NULL && VirtualProtect(r, 0x1000, PAGE_NOACCESS, &old) && old == PAGE_READONLY);
    if (r) CHECK(UnmapViewOfFile(r));
    CloseHandle(m);
    CloseHandle(f);
    unsigned char *p = (unsigned char *)VirtualAlloc(NULL, 0x2000, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    CHECK(p && VirtualProtect(p, 0x1000, PAGE_READONLY, &old) && old == PAGE_READWRITE);
    if (p) VirtualFree(p, 0, MEM_RELEASE);
}

/* ---- the service's limits become Win32 errors: 128 sections, 512 views, a host without the service */
TEST(vmp_shared_limits)
{
    PRIVATE_ONLY();
    mkdata(VPD "lim.bin");
    HANDLE ms[128];
    for (int i = 0; i < 128; i++) {
        ms[i] = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, 0x1000, NULL);
        CHECK(ms[i] != NULL);
    }
    CHECK_EQ(host_section_live_sections(), 128);
    SetLastError(0);
    CHECK(CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, 0x1000, NULL) == NULL && SYSERR == ERROR_NOT_ENOUGH_MEMORY);
    HANDLE f = open_rw(VPD "lim.bin");
    SetLastError(0);
    CHECK(CreateFileMappingA(f, NULL, PAGE_READWRITE, 0, 0, NULL) == NULL && SYSERR == ERROR_NOT_ENOUGH_MEMORY);   /* a file-backed one too */
    CloseHandle(f);
    CHECK_EQ(host_section_live_sections(), 128);   /* nothing was left behind by the failures */
    f = open_r(VPD "lim.bin");   /* private-page sections need no host object */
    HANDLE pm = CreateFileMappingA(f, NULL, PAGE_READONLY, 0, 0, NULL);
    CHECK(pm != NULL);
    if (pm) CloseHandle(pm);
    CloseHandle(f);
    /* 512 views of one section */
    static unsigned char *vs[512];
    for (int i = 0; i < 512; i++) {
        vs[i] = (unsigned char *)MapViewOfFile(ms[0], FILE_MAP_WRITE, 0, 0, 0);
        CHECK(vs[i] != NULL);
        if (!vs[i]) break;
    }
    CHECK_EQ(host_section_live_views(), 512);
    SetLastError(0);
    CHECK(MapViewOfFile(ms[0], FILE_MAP_WRITE, 0, 0, 0) == NULL && SYSERR == ERROR_NOT_ENOUGH_MEMORY);
    CHECK_EQ(host_section_live_views(), 512);
    for (int i = 0; i < 512; i++)
        if (vs[i]) CHECK(UnmapViewOfFile(vs[i]));
    CHECK_EQ(host_section_live_views(), 0);
    for (int i = 0; i < 128; i++)
        CloseHandle(ms[i]);
    CHECK_EQ(host_section_live_sections(), 0);
    /* the view table full while a file-backed section loads its bytes through a view of its own */
    HANDLE m0 = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, 0x1000, NULL);
    for (int i = 0; i < 512; i++) vs[i] = (unsigned char *)MapViewOfFile(m0, FILE_MAP_WRITE, 0, 0, 0);
    f = open_rw(VPD "lim.bin");
    SetLastError(0);
    CHECK(CreateFileMappingA(f, NULL, PAGE_READWRITE, 0, 0, NULL) == NULL && SYSERR == ERROR_NOT_ENOUGH_MEMORY);
    CloseHandle(f);
    CHECK_EQ(host_section_live_sections(), 1);
    for (int i = 0; i < 512; i++)
        if (vs[i]) UnmapViewOfFile(vs[i]);
    CloseHandle(m0);
    CHECK_EQ(host_section_live_sections(), 0);
    /* a host without the section service has no capacity: sections cannot be made, nor a view of one that exists */
    HANDLE m1 = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, 0x1000, NULL);
    CHECK(m1 != NULL);
    host_section_set_service(0);
    SetLastError(0);
    CHECK(CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, 0x1000, NULL) == NULL && SYSERR == ERROR_NOT_ENOUGH_MEMORY);
    SetLastError(0);
    CHECK(MapViewOfFile(m1, FILE_MAP_WRITE, 0, 0, 0) == NULL && SYSERR == ERROR_NOT_ENOUGH_MEMORY);
    host_section_set_service(1);
    unsigned char *ok = (unsigned char *)MapViewOfFile(m1, FILE_MAP_WRITE, 0, 0, 0);   /* the failed attempt left no reservation behind */
    CHECK(ok != NULL);
    if (ok) CHECK(UnmapViewOfFile(ok));
    CloseHandle(m1);
    CHECK_EQ(host_section_live_sections(), 0);
}
