/* host_section.cpp - TEST ONLY, linked into s4_tests_private only (make -C tests/s4 private): the host test's
 * implementation of the guest section wrappers fnvnx_section_create / map / unmap / free (toolchain/include/fnvnx/guest.h)
 * with the documented semantics and limits of the host services FNVNX_PLAT_SECTION_* (toolchain/include/fnvnx/hostapi.h):
 * a section is a memfd (zero-filled), a view is a MAP_SHARED | MAP_FIXED mapping of a range of it over an address
 * range that the caller reserved (os_vm_reserve: PROT_NONE anonymous, nothing else there), always read-write; 128
 * sections, 512 views; free is deferred until the last view is gone; the error codes are the table of the header.
 * The product backend (platform/win32/filemap_private.cpp) is the only caller, so its logic is what the tests exercise. */
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#include "fnvnx/hostapi.h"

#define MAX_SECTIONS 128
#define MAX_VIEWS 512
#define SPAGE 0x1000u
#define SGRAN 0x10000u

namespace {
struct Sec {
    bool used;
    bool freed;      /* FREE was called: the id is dead, the pages live until the last view goes */
    uint32_t id;
    int fd;
    uint64_t size;
    int views;
};
struct View {
    bool used;
    bool host_range;   /* addr == 0 at MAP: the range is the host's own, released at UNMAP */
    uint32_t addr, size;
    int sec;           /* index into g_sec */
};
Sec g_sec[MAX_SECTIONS];
View g_view[MAX_VIEWS];
uint32_t g_next_id = 1;
bool g_service = true;   /* false: a host without the service answers FNVNX_SEC_E_NOSERVICE (host_section_set_service) */

int live_views() { int n = 0; for (auto &v : g_view) n += v.used; return n; }

void sec_release(Sec &s)
{
    close(s.fd);
    memset(&s, 0, sizeof s);
}

/* [a, a + n) lies completely inside mappings that are anonymous PROT_NONE (the caller's reservation): 0, or the error
 * the service gives (REGION: part of it is not mapped at all; RANGE: it is mapped, but committed or a view) */
int in_reservation(uint64_t a, uint64_t n)
{
    FILE *f = fopen("/proc/self/maps", "r");
    if (!f)
        return FNVNX_SEC_E_NOMEM;
    char line[512];
    uint64_t at = a, end = a + n;
    int res = FNVNX_SEC_E_REGION;
    while (fgets(line, sizeof line, f)) {
        unsigned long lo, hi, ino;
        char perms[8], path[256];
        path[0] = 0;
        unsigned long off, dmaj, dmin;
        if (sscanf(line, "%lx-%lx %7s %lx %lx:%lx %lu %255[^\n]", &lo, &hi, perms, &off, &dmaj, &dmin, &ino, path) < 7)
            continue;
        if (hi <= at || lo >= end)
            continue;
        if (lo > at)   /* a hole before this mapping */
            break;
        bool resv = !strcmp(perms, "---p") && ino == 0 && (!path[0] || path[0] == '[');
        if (!resv) {
            res = FNVNX_SEC_E_RANGE;
            at = end + 1;
            break;
        }
        at = hi;
        if (at >= end) {
            res = 0;
            break;
        }
    }
    fclose(f);
    return at >= end && res == 0 ? 0 : res;
}
}   // namespace

extern "C" {

int host_section_live_sections(void)
{
    int n = 0;
    for (auto &s : g_sec)
        n += s.used;
    return n;
}
int host_section_live_views(void) { return live_views(); }
void host_section_set_service(int on) { g_service = on != 0; }

int fnvnx_section_create(uint32_t size, uint32_t *id)
{
    if (!g_service)
        return -(int)FNVNX_SEC_E_NOSERVICE;
    if (!size || (size & (SPAGE - 1)) || (uint64_t)size > FNVNX_SEC_MAX_SIZE)
        return -(int)FNVNX_SEC_E_SIZE;
    int i = 0;
    while (i < MAX_SECTIONS && g_sec[i].used)
        i++;
    if (i == MAX_SECTIONS)
        return -(int)FNVNX_SEC_E_FULL;
    int fd = memfd_create("fnvnx-section", MFD_CLOEXEC);
    if (fd < 0)
        return -(int)FNVNX_SEC_E_NOMEM;
    if (ftruncate(fd, (off_t)size)) {
        close(fd);
        return -(int)FNVNX_SEC_E_NOMEM;
    }
    Sec &s = g_sec[i];
    s.used = true;
    s.freed = false;
    s.id = g_next_id++;
    s.fd = fd;
    s.size = size;
    s.views = 0;
    *id = s.id;
    return 0;
}

int fnvnx_section_map(uint32_t id, uint32_t offset, uint32_t size, uint32_t addr_hint, uint32_t *addr)
{
    if (!g_service)
        return -(int)FNVNX_SEC_E_NOSERVICE;
    int si = -1;
    for (int i = 0; i < MAX_SECTIONS; i++)
        if (g_sec[i].used && !g_sec[i].freed && g_sec[i].id == id)
            si = i;
    if (si < 0)
        return -(int)FNVNX_SEC_E_ID;
    Sec &s = g_sec[si];
    if (!size || (size & (SPAGE - 1)))
        return -(int)FNVNX_SEC_E_SIZE;
    if ((offset & (SPAGE - 1)) || (addr_hint & (SPAGE - 1)))
        return -(int)FNVNX_SEC_E_ALIGN;
    if ((uint64_t)offset + size > s.size)
        return -(int)FNVNX_SEC_E_RANGE;
    int vi = 0;
    while (vi < MAX_VIEWS && g_view[vi].used)
        vi++;
    if (vi == MAX_VIEWS)
        return -(int)FNVNX_SEC_E_FULL;
    void *p;
    if (addr_hint) {
        int e = in_reservation(addr_hint, size);
        if (e)
            return -e;
        p = mmap((void *)(uintptr_t)addr_hint, size, PROT_READ | PROT_WRITE, MAP_SHARED | MAP_FIXED, s.fd, (off_t)offset);
    } else {   /* the host's own range, 64 KiB aligned, below 4 GiB (a guest address) */
        char *q = (char *)mmap(NULL, (size_t)size + SGRAN, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE | MAP_32BIT, -1, 0);
        if (q == MAP_FAILED)
            return -(int)FNVNX_SEC_E_NOMEM;
        char *a = (char *)(((uintptr_t)q + SGRAN - 1) & ~(uintptr_t)(SGRAN - 1));
        p = mmap(a, size, PROT_READ | PROT_WRITE, MAP_SHARED | MAP_FIXED, s.fd, (off_t)offset);
        if (a > q)
            munmap(q, (size_t)(a - q));
        if (a + size < q + size + SGRAN)
            munmap(a + size, (size_t)(q + size + SGRAN - (a + size)));
    }
    if (p == MAP_FAILED)
        return -(int)FNVNX_SEC_E_NOMEM;
    View &v = g_view[vi];
    v.used = true;
    v.host_range = addr_hint == 0;
    v.addr = (uint32_t)(uintptr_t)p;
    v.size = size;
    v.sec = si;
    s.views++;
    *addr = v.addr;
    return 0;
}

int fnvnx_section_unmap(uint32_t addr, uint32_t size)
{
    if (!g_service)
        return -(int)FNVNX_SEC_E_NOSERVICE;
    int vi = -1;
    for (int i = 0; i < MAX_VIEWS; i++)
        if (g_view[i].used && g_view[i].addr == addr && g_view[i].size == size)
            vi = i;
    if (vi < 0)
        return -(int)FNVNX_SEC_E_RANGE;
    View v = g_view[vi];
    if (v.host_range)
        munmap((void *)(uintptr_t)v.addr, v.size);
    else   /* the caller's reservation is what it was before the view */
        mmap((void *)(uintptr_t)v.addr, v.size, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE | MAP_FIXED, -1, 0);
    memset(&g_view[vi], 0, sizeof g_view[vi]);
    Sec &s = g_sec[v.sec];
    if (--s.views == 0 && s.freed)
        sec_release(s);
    return 0;
}

int fnvnx_section_free(uint32_t id)
{
    if (!g_service)
        return -(int)FNVNX_SEC_E_NOSERVICE;
    for (int i = 0; i < MAX_SECTIONS; i++)
        if (g_sec[i].used && !g_sec[i].freed && g_sec[i].id == id) {
            g_sec[i].freed = true;
            if (g_sec[i].views == 0)
                sec_release(g_sec[i]);
            return 0;
        }
    return -(int)FNVNX_SEC_E_ID;
}

}   // extern "C"
