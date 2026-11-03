/* fnvnx_addrmap.h - runtime form of the link step's address map (docs/LINK.md, "Address map").
 *
 * tools/link/fnvlink.py writes addrmap.c: one entry per original code address the native build defines, sorted by
 * address, so a loader can binary-search it. `native` is the address of the native function the original address
 * was linked to (its __fnv_<addr> symbol). */
#ifndef FNVNX_ADDRMAP_H
#define FNVNX_ADDRMAP_H

#ifdef __cplusplus
extern "C" {
#endif

struct fnvnx_addrmap_entry {
    unsigned int orig;   /* address in FalloutNV.exe 1.4.0.525 */
    void *native;        /* 32-bit on AArch64 ILP32 */
};

extern const struct fnvnx_addrmap_entry fnvnx_addrmap[];
extern const unsigned fnvnx_addrmap_count;

#ifdef __cplusplus
}
#endif
#endif
