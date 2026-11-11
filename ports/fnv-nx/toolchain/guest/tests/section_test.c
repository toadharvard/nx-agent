/* section_test.c - guest program for the memory-section services (hostapi.h 11..14), run in the emulator by section_e2e.sh
 * (loader/guest/e2e_host.py holds the model of the host; the device's host_section.inc is covered by
 * tests/s4/backends/host_section_test.c). Prints `section-test: PASS <n> checks` and returns 0, or a FAIL line per check. */
#include <stdint.h>
#include <stdio.h>

#include "fnvnx/guest.h"
#include "fnvnx/hostapi.h"

static int g_n, g_bad;
#define CK(c) do { g_n++; if (!(c)) { g_bad++; printf("section-test: FAIL line %d: %s\n", __LINE__, #c); } } while (0)
#define ERR(code) (-(int)(code))
#define B(a) ((volatile uint8_t *)(uintptr_t)(a))

static uint64_t vmn(uint32_t fn, uint32_t n, uint64_t a0, uint64_t a1)
{
    uint64_t arg[2] = {a0, a1};
    return fnvnx_hcall4(fnvnx_host->ext_call, FNVNX_EXT_PLATFORM, fn, FNVNX_ADDR(arg), n);
}

int main(void)
{
    uint32_t id = 0, a = 0, b = 0, c = 0, d = 0;
    CK(fnvnx_section_create(0, &id) == ERR(FNVNX_SEC_E_SIZE) && fnvnx_section_create(0x1001, &id) == ERR(FNVNX_SEC_E_SIZE));
    CK(fnvnx_section_create(0x10000, &id) == 0 && id == 1);

    /* two overlapping views, one host-chosen, one fixed inside a VM reservation */
    uint32_t resv = (uint32_t)vmn(FNVNX_PLAT_VM_RESERVE, 2, 0, 0x40000);
    CK(resv != 0);
    CK(fnvnx_section_map(id, 0, 0x4000, 0, &a) == 0 && a != 0 && !(a & 0xFFFF));
    uint32_t fx = resv + 0x10000;
    CK(fnvnx_section_map(id, 0x2000, 0x4000, fx, &b) == 0 && b == fx);
    int zero = 1;
    for (uint32_t i = 0; i < 0x4000; i++) zero &= B(a)[i] == 0 && B(b)[i] == 0;
    CK(zero);
    B(a)[0x2000] = 0xA5; B(a)[0x3FFF] = 0x5A;
    CK(B(b)[0] == 0xA5 && B(b)[0x1FFF] == 0x5A);                 /* a's offset 0x2000 is b's 0 */
    B(b)[0x1000] = 0x77; B(b)[0x3FFF] = 0x33;
    CK(B(a)[0x3000] == 0x77 && B(a)[0x3FFF] == 0x5A && B(b)[0x3FFF] == 0x33);

    /* a view of everything and a view of one page at offset 0x8000 start at the right page */
    CK(fnvnx_section_map(id, 0, 0x10000, 0, &d) == 0);
    for (int p = 0; p < 16; p++) B(d)[p * 0x1000 + 5] = (uint8_t)(0x10 + p);
    CK(fnvnx_section_map(id, 0x8000, 0x1000, 0, &c) == 0 && B(c)[5] == 0x18 && B(d)[0x2000] == 0xA5);
    B(c)[6] = 0xEE;
    CK(B(d)[0x8006] == 0xEE);

    /* errors */
    uint32_t t;
    CK(fnvnx_section_map(id, 0, 0, 0, &t) == ERR(FNVNX_SEC_E_SIZE) && fnvnx_section_map(id, 0, 0x1800, 0, &t) == ERR(FNVNX_SEC_E_SIZE));
    CK(fnvnx_section_map(id, 0x800, 0x1000, 0, &t) == ERR(FNVNX_SEC_E_ALIGN) && fnvnx_section_map(id, 0, 0x1000, resv + 0x800, &t) == ERR(FNVNX_SEC_E_ALIGN));
    CK(fnvnx_section_map(99, 0, 0x1000, 0, &t) == ERR(FNVNX_SEC_E_ID) && fnvnx_section_map(0, 0, 0x1000, 0, &t) == ERR(FNVNX_SEC_E_ID));
    CK(fnvnx_section_map(id, 0xF000, 0x2000, 0, &t) == ERR(FNVNX_SEC_E_RANGE));
    CK(fnvnx_section_map(id, 0, 0x1000, fx, &t) == ERR(FNVNX_SEC_E_RANGE));          /* over a view */
    CK(fnvnx_section_map(id, 0, 0x2000, fx + 0x3000, &t) == ERR(FNVNX_SEC_E_RANGE)); /* half over a view */
    CK(fnvnx_section_map(id, 0, 0x1000, 0x3A000000u, &t) == ERR(FNVNX_SEC_E_REGION)); /* free, not in a reservation */
    CK(fnvnx_section_map(id, 0, 0x2000, resv + 0x40000 - 0x1000, &t) == ERR(FNVNX_SEC_E_REGION));

    /* VM services and views */
    CK(vmn(FNVNX_PLAT_VM_COMMIT, 2, fx, 0x1000) == 0xD401 && vmn(FNVNX_PLAT_VM_COMMIT, 2, resv, 0x20000) == 0xD401);
    CK(vmn(FNVNX_PLAT_VM_COMMIT, 2, resv, 0x8000) == 0);
    CK(fnvnx_section_map(id, 0, 0x1000, resv + 0x4000, &t) == ERR(FNVNX_SEC_E_RANGE));   /* over committed memory */
    CK(vmn(FNVNX_PLAT_VM_DECOMMIT, 2, fx, 0x1000) == 0xD401 && vmn(FNVNX_PLAT_VM_DECOMMIT, 2, resv, 0x20000) == 0xD401);
    uint64_t pr[3] = {fx, 0x1000, FNVNX_VM_PROT_R};
    CK(fnvnx_hcall4(fnvnx_host->ext_call, FNVNX_EXT_PLATFORM, FNVNX_PLAT_VM_PROTECT, FNVNX_ADDR(pr), 3) == 0xD401);
    CK(vmn(FNVNX_PLAT_VM_DECOMMIT, 2, resv, 0x8000) == 0);
    CK(vmn(FNVNX_PLAT_VM_RELEASE, 1, resv, 0) == 0xD401 && B(b)[0x1000] == 0x77);

    /* unmap: exactly a view; the others and the data stay */
    CK(fnvnx_section_unmap(a, 0x3000) == ERR(FNVNX_SEC_E_RANGE) && fnvnx_section_unmap(a + 0x1000, 0x1000) == ERR(FNVNX_SEC_E_RANGE));
    CK(fnvnx_section_unmap(0x1234000, 0x1000) == ERR(FNVNX_SEC_E_RANGE));
    CK(fnvnx_section_unmap(a, 0x4000) == 0 && fnvnx_section_unmap(a, 0x4000) == ERR(FNVNX_SEC_E_RANGE));
    CK(B(b)[0] == 0xA5 && B(b)[0x1000] == 0x77 && B(d)[0x2000] == 0xA5 && B(c)[5] == 0x18);
    B(b)[2] = 0x99;
    CK(B(d)[0x2002] == 0x99);
    CK(fnvnx_section_unmap(b, 0x4000) == 0 && vmn(FNVNX_PLAT_VM_RELEASE, 1, resv, 0) == 0);

    /* free with live views is deferred, the id dies, the data lives, the last unmap releases */
    CK(fnvnx_section_free(id) == 0 && fnvnx_section_free(id) == ERR(FNVNX_SEC_E_ID));
    CK(fnvnx_section_map(id, 0, 0x1000, 0, &t) == ERR(FNVNX_SEC_E_ID) && B(d)[0x2000] == 0xA5);
    CK(fnvnx_section_unmap(c, 0x1000) == 0 && B(d)[0x8006] == 0xEE);
    CK(fnvnx_section_unmap(d, 0x10000) == 0);
    uint32_t id2 = 0;
    CK(fnvnx_section_create(0x2000, &id2) == 0 && id2 == 2);          /* ids are not reused */
    CK(fnvnx_section_free(id2) == 0 && fnvnx_section_free(id2) == ERR(FNVNX_SEC_E_ID));

    /* a section view is zero again in a new section (pages are not recycled with their contents) */
    uint32_t id3 = 0, e = 0;
    CK(fnvnx_section_create(0x1000, &id3) == 0 && fnvnx_section_map(id3, 0, 0x1000, 0, &e) == 0 && B(e)[5] == 0);
    CK(fnvnx_section_unmap(e, 0x1000) == 0 && fnvnx_section_free(id3) == 0);

    if (g_bad) { printf("section-test: %d of %d checks FAILED\n", g_bad, g_n); return 1; }
    printf("section-test: PASS %d checks\n", g_n);
    return 0;
}
