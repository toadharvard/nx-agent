/* host side of the round trip: host_sock.inc against glibc, behind the test double of ext_call. */
#include "host_sock.inc"

/* ext_call(module, fn, args, nargs) as the guest's fnvnx_hcall4 calls it */
uint64_t sockwire_ext(uint64_t module, uint64_t fn, uint64_t args, uint64_t nargs) {
    if (module != FNVNX_EXT_PLATFORM) return FNVNX_EXT_NO_MODULE;
    return host_sock_call((uint32_t)fn, (const uint64_t *)(uintptr_t)args, (uint32_t)nargs);
}
unsigned sockwire_init(void) { return host_sock_init(); }
void sockwire_exit(void) { host_sock_exit(); }
/* a loopback port nothing holds, found by the host's own libc (the guest has no getsockname) */
unsigned sockwire_free_port(int dgram) {
    int fd = socket(AF_INET, dgram ? SOCK_DGRAM : SOCK_STREAM, 0);
    struct sockaddr_in a;
    socklen_t l = sizeof a;
    unsigned port = 0;
    memset(&a, 0, sizeof a);
    a.sin_family = AF_INET;
    a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (fd >= 0 && bind(fd, (struct sockaddr *)&a, sizeof a) == 0 && getsockname(fd, (struct sockaddr *)&a, &l) == 0) port = ntohs(a.sin_port);
    if (fd >= 0) close(fd);
    return port;
}
