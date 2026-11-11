/* host side of the round trip: host_sock.inc against glibc, behind the test double of ext_call. */
#include "host_sock.inc"

/* ext_call(module, fn, args, nargs) as the guest's fnvnx_hcall4 calls it */
uint64_t sockwire_ext(uint64_t module, uint64_t fn, uint64_t args, uint64_t nargs) {
    if (module != FNVNX_EXT_PLATFORM || fn < FNVNX_PLAT_SOCK_FIRST || fn > FNVNX_PLAT_SOCK_LAST) return FNVNX_EXT_NO_MODULE;
    return host_sock_call((uint32_t)fn, (const uint64_t *)(uintptr_t)args, (uint32_t)nargs);
}
unsigned sockwire_init(void) { return host_sock_init(); }
void sockwire_exit(void) { host_sock_exit(); }
