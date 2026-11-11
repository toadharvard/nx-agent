/* host_sock_total.c - the platform lead's socket handler (toolchain/host/host_sock.inc, verbatim) built against glibc, exported as
 * host_sock_call_ext so that host_total_test.c (a libnx translation unit, which cannot include glibc's socket headers) dispatches the
 * services 16..33 through the REAL handler. Linked in by run_qemu.sh and hostapi_total.py. */
#include "../../../toolchain/host/host_sock.inc"
uint64_t host_sock_call_ext(uint32_t fn, const uint64_t *a, uint32_t n) { return host_sock_call(fn, a, n); }
