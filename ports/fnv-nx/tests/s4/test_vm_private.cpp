/* test_vm_private.cpp - the file-mapping backend of the product (platform/win32/filemap_private.cpp), vmp_* cases.
 * Compiled into both test binaries; each case first asks the backend identity and returns at once unless the binary
 * links the private backend (make -C tests/s4 private: s4_tests_private, with private_residue_probe.cpp). The probe
 * records every call that reaches the residue, so the cases assert exactly which CreateFileMappingA arguments do. */
#include "s4_test.h"

#include <setjmp.h>
#include <signal.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "../../platform/win32/filemap_backing.h"

struct fm_probe { int calls; struct fm_spec spec; };
extern struct fm_probe g_fm_probe __attribute__((weak));   /* defined by private_residue_probe.cpp (private binary only) */

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

/* ---- the residue set: every valid flProtect x SEC_* x backing, asserted through the probe */
static const DWORD k_bases[] = { PAGE_READONLY, PAGE_READWRITE, PAGE_WRITECOPY, PAGE_EXECUTE_READ, PAGE_EXECUTE_READWRITE, 0x80 /* PAGE_EXECUTE_WRITECOPY */ };
static const DWORD k_secs[] = { 0, SEC_IMAGE, SEC_RESERVE, SEC_COMMIT };

TEST(vmp_residue_set)
{
    PRIVATE_ONLY();
    mkdata(VPD "data.bin");
    mkpe(VPD "img.dll");
    int checked = 0;
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
                /* the residue: the paging file; a writable file section (not an image); a file that writers can change */
                bool residue = !rejected && (backing == 0 || (rw && !image) || backing == 2 || backing == 3);
                int c0 = g_fm_probe.calls;
                SetLastError(0);
                HANDLE m = CreateFileMappingA(f, NULL, base | sec, 0, backing == 0 ? 0x10000 : 0, NULL);
                DWORD err = SYSERR;
                int hit = g_fm_probe.calls - c0;
                if (residue) {
                    CHECK(hit == 1 && m == NULL && err == ERROR_INVALID_PARAMETER);
                    CHECK(g_fm_probe.spec.prot == base && g_fm_probe.spec.file == (backing != 0) && g_fm_probe.spec.image == image);
                } else if (rejected) {
                    CHECK(hit == 0 && m == NULL);
                } else {
                    CHECK(hit == 0 && m != NULL);
                }
                if (hit != (residue ? 1 : 0) || (m != NULL) != (!residue && !rejected))
                    fprintf(stderr, "    combination base=%#x sec=%#x backing=%d\n", (unsigned)base, (unsigned)sec, backing);
                if (m) CloseHandle(m);
                if (f != INVALID_HANDLE_VALUE) CloseHandle(f);
                checked++;
            }
    CHECK_EQ(checked, 6 * 4 * 4);
    /* the spec the residue gets: writers is the share-mode fact of the handle */
    HANDLE f = open_rs(VPD "data.bin");
    int c0 = g_fm_probe.calls;
    CHECK(CreateFileMappingA(f, NULL, PAGE_READONLY, 0, 0, NULL) == NULL && g_fm_probe.calls == c0 + 1);
    CHECK(g_fm_probe.spec.writers && g_fm_probe.spec.size == FSIZE);
    CloseHandle(f);
    f = open_rw(VPD "data.bin");
    CHECK(CreateFileMappingA(f, NULL, PAGE_READWRITE, 0, 0, NULL) == NULL && g_fm_probe.calls == c0 + 2);
    CloseHandle(f);
    f = open_r(VPD "data.bin");
    HANDLE m = CreateFileMappingA(f, NULL, PAGE_READONLY, 0, 0, NULL);
    CHECK(m != NULL && g_fm_probe.calls == c0 + 2);
    CloseHandle(m);
    CloseHandle(f);
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
    int c0 = g_fm_probe.calls;
    CHECK(CreateFileMappingA(f, NULL, PAGE_READONLY | SEC_IMAGE, 0, 0, NULL) == NULL && SYSERR == ERROR_BAD_EXE_FORMAT && g_fm_probe.calls == c0);
    CloseHandle(f);
}
