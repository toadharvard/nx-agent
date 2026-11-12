/* section_guest.c - guest side of the host's memory sections (hostapi.h FNVNX_PLAT_SECTION_*, host_section.inc): the calls the
 * platform's CreateFileMapping / MapViewOfFile / UnmapViewOfFile / CloseHandle make over the paging file. Each is one
 * ext_call; the host's result is an int64 with the top bit set for an error (-(code)). */
#include <stdint.h>

#include "fnvnx/guest.h"
#include "fnvnx/hostapi.h"

static int64_t sec_call(uint32_t fn, uint64_t a0, uint64_t a1, uint64_t a2, uint64_t a3, uint32_t n)
{
    uint64_t arg[4] = {a0, a1, a2, a3};
    if (!fnvnx_host->ext_call) return -(int64_t)FNVNX_SEC_E_NOSERVICE;
    return (int64_t)fnvnx_hcall4(fnvnx_host->ext_call, FNVNX_EXT_PLATFORM, fn, FNVNX_ADDR(arg), n);
}

int fnvnx_section_create(uint32_t size, uint32_t *id)
{
    int64_t r = sec_call(FNVNX_PLAT_SECTION_CREATE, size, 0, 0, 0, 1);
    if (r < 0) return (int)r;
    *id = (uint32_t)r;
    return 0;
}

int fnvnx_section_map(uint32_t id, uint32_t offset, uint32_t size, uint32_t addr_hint, uint32_t *addr)
{
    int64_t r = sec_call(FNVNX_PLAT_SECTION_MAP, id, addr_hint, offset, size, 4);
    if (r < 0) return (int)r;
    *addr = (uint32_t)r;
    return 0;
}

int fnvnx_section_unmap(uint32_t addr, uint32_t size)
{
    int64_t r = sec_call(FNVNX_PLAT_SECTION_UNMAP, addr, size, 0, 0, 2);
    return r < 0 ? (int)r : 0;
}

int fnvnx_section_free(uint32_t id)
{
    int64_t r = sec_call(FNVNX_PLAT_SECTION_FREE, id, 0, 0, 0, 1);
    return r < 0 ? (int)r : 0;
}
