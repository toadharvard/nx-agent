/* Round trip of the BSD socket services over loopback: the guest's BSD API (sock_guest.c, newlib + libnx headers) ->
 * ext_call -> host_sock.inc (glibc). Checks values and the errno THE GUEST sees (newlib's numbers, which differ from
 * Linux's for most socket errors: ECONNREFUSED 61/111, EADDRINUSE 112/98, ENOTSOCK 108/88, EINPROGRESS 119/115). */
#include <errno.h>
#include <fcntl.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <stdio.h>
#include <string.h>
#include <sys/iosupport.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#include "fnvnx/hostapi.h"
#include "fnvnx/sockwire.h"

extern void sockwire_shim_init(void);
extern unsigned sockwire_init(void);
extern void sockwire_exit(void);
extern unsigned sockwire_free_port(int dgram);   /* a loopback port that nothing holds (host_glue.c, the host's own libc) */
extern void fnvnx_guest_sock_init(void);
extern const FnvnxHostApi *fnvnx_host;
extern int AddDevice(const devoptab_t *);

static int g_checks, g_fails;
#define CK(c) do { g_checks++; if (!(c)) { g_fails++; printf("FAIL line %d: %s (errno %d)\n", __LINE__, #c, errno); } } while (0)
#define CKE(call, e) do { long r_; errno = 0; g_checks++; r_ = (long)(call); \
    if (r_ != -1 || errno != (e)) { g_fails++; printf("FAIL line %d: %s -> %ld errno %d, want %d\n", __LINE__, #call, r_, errno, (e)); } } while (0)

static uint16_t bs(uint16_t v) { return (uint16_t)((v >> 8) | (v << 8)); }
static struct sockaddr_in lo(unsigned port) {
    struct sockaddr_in a;
    memset(&a, 0, sizeof a);
    a.sin_len = sizeof a;
    a.sin_family = AF_INET;
    a.sin_port = bs((uint16_t)port);
    const unsigned char ip[4] = {127, 0, 0, 1};
    memcpy(&a.sin_addr, ip, 4);
    return a;
}
static int is_lo(const struct sockaddr_in *a) {
    const unsigned char ip[4] = {127, 0, 0, 1};
    return a->sin_family == AF_INET && a->sin_len == sizeof *a && !memcmp(&a->sin_addr, ip, 4);
}
static int nb(int fd) { int f = fcntl(fd, F_GETFL); return f >= 0 && (f & O_NONBLOCK); }
static int pollone(int fd, short ev, int ms, short *re) {
    struct pollfd p = {fd, ev, 0};
    int n = poll(&p, 1, ms);
    if (re) *re = p.revents;
    return n;
}
static uint64_t raw(uint32_t fn, uint32_t n, uint64_t a0, uint64_t a1, uint64_t a2, uint64_t a3, uint64_t a4) {
    uint64_t args[5] = {a0, a1, a2, a3, a4};
    return fnvnx_hcall4(fnvnx_host->ext_call, FNVNX_EXT_PLATFORM, fn, FNVNX_ADDR(args), n);
}

static void test_tcp(void) {
    int s = socket(AF_INET, SOCK_STREAM, 0);
    CK(s >= 0);
    CK(!nb(s));
    int one = 1, v = 0;
    socklen_t vl = sizeof v;
    CK(setsockopt(s, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one) == 0);
    CK(getsockopt(s, SOL_SOCKET, SO_REUSEADDR, &v, &vl) == 0 && v != 0 && vl == sizeof v);
    unsigned port = sockwire_free_port(0);
    struct sockaddr_in a = lo(port);
    CK(port != 0 && bind(s, (struct sockaddr *)&a, sizeof a) == 0);
    CK(listen(s, 4) == 0);
    vl = sizeof v;
    CK(getsockopt(s, SOL_SOCKET, SO_ACCEPTCONN, &v, &vl) == 0 && v != 0);
    vl = sizeof v;
    CK(getsockopt(s, SOL_SOCKET, SO_TYPE, &v, &vl) == 0 && v == SOCK_STREAM);
    vl = sizeof v;
    CK(getsockopt(s, SOL_SOCKET, SO_ERROR, &v, &vl) == 0 && v == 0);

    int s2 = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in same = lo(port);
    CKE(bind(s2, (struct sockaddr *)&same, sizeof same), EADDRINUSE);
    close(s2);

    /* non-blocking listener: nothing pending */
    CK(fcntl(s, F_SETFL, O_NONBLOCK) == 0 && nb(s));
    CKE(accept(s, NULL, NULL), EWOULDBLOCK);
    short re = 0;
    CK(pollone(s, POLLIN, 0, &re) == 0 && re == 0);

    int c = socket(AF_INET, SOCK_STREAM, 0);
    CK(connect(c, (struct sockaddr *)&same, sizeof same) == 0);
    CK(pollone(s, POLLIN, 1000, &re) == 1 && (re & POLLIN));
    struct sockaddr_in peer;
    socklen_t pl = sizeof peer;
    int ac = accept(s, (struct sockaddr *)&peer, &pl);
    CK(ac >= 0 && pl == sizeof peer && is_lo(&peer));
    CK(!nb(ac));   /* the listener is non-blocking, the accepted socket is not */

    /* data both ways: send/recv and write/read (the descriptor layer's close_r/read_r/write_r) */
    char buf[64];
    CK(send(c, "hello", 5, 0) == 5);
    CK(pollone(ac, POLLIN, 1000, &re) == 1 && (re & POLLIN));
    CK(recv(ac, buf, sizeof buf, MSG_PEEK) == 5 && !memcmp(buf, "hello", 5));
    CK(recv(ac, buf, sizeof buf, 0) == 5 && !memcmp(buf, "hello", 5));
    CKE(recv(ac, buf, sizeof buf, MSG_DONTWAIT), EWOULDBLOCK);
    CK(write(ac, "world!", 6) == 6);
    CK(pollone(c, POLLIN, 1000, NULL) == 1);
    CK(read(c, buf, sizeof buf) == 6 && !memcmp(buf, "world!", 6));
    CKE(send(c, "x", 1, 0x10000000), EOPNOTSUPP);
    CK(pollone(c, POLLOUT, 0, &re) == 1 && (re & POLLOUT));

    /* poll over several: a socket, a negative fd, a closed/never-open fd, a non-socket descriptor */
    int dev = AddDevice(&(const devoptab_t){.name = "fil", .structSize = 4});
    extern int __alloc_handle(int);
    int ff = __alloc_handle(dev);
    struct pollfd pf[4] = {{c, POLLOUT, 0}, {-1, POLLIN, 0}, {999, POLLIN, 0}, {ff, POLLIN, 0}};
    CK(poll(pf, 4, 0) == 3);
    CK((pf[0].revents & POLLOUT) && pf[1].revents == 0 && pf[2].revents == POLLNVAL && pf[3].revents == POLLNVAL);
    CKE(send(ff, "x", 1, 0), ENOTSOCK);
    CKE(listen(ff, 1), ENOTSOCK);
    CKE(send(999, "x", 1, 0), EBADF);
    CKE(close(999), EBADF);
    CKE(fcntl(ff, F_GETFL), ENOSYS);   /* not a socket: the fd layer's old answer */
    close(ff);

    /* orderly close */
    CK(close(c) == 0);
    CKE(send(c, "x", 1, 0), EBADF);   /* a closed descriptor */
    CK(pollone(ac, POLLIN, 1000, &re) == 1 && (re & (POLLIN | POLLHUP)));
    /* writing to a peer that is gone: EPIPE (or ECONNRESET), never a signal */
    int e = 0;
    for (int i = 0; i < 20 && !e; i++) {
        if (send(ac, "xxxx", 4, 0) < 0) e = errno;
        poll(NULL, 0, 20);
    }
    CK(e == EPIPE || e == ECONNRESET);
    CK(close(ac) == 0);
    CKE(close(ac), EBADF);

    /* F_DUPFD / dup / SOCK_NONBLOCK | SOCK_CLOEXEC */
    int n1 = socket(AF_INET, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    CK(n1 >= 0 && nb(n1) && (fcntl(n1, F_GETFD) & FD_CLOEXEC));
    CK(fcntl(n1, F_SETFD, 0) == 0 && fcntl(n1, F_GETFD) == 0);
    int d = fcntl(n1, F_DUPFD, 20);
    CK(d >= 20 && nb(d));
    CK(fcntl(d, F_SETFL, 0) == 0 && !nb(n1));   /* one open file description */
    CK(close(d) == 0 && close(n1) == 0);
    CKE(fcntl(s, 99, 0), EINVAL);

    /* refused connect (blocking, then non-blocking) to a port nobody listens on */
    int dead = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in refuse = lo(sockwire_free_port(0));
    close(dead);
    int r1 = socket(AF_INET, SOCK_STREAM, 0);
    CKE(connect(r1, (struct sockaddr *)&refuse, sizeof refuse), ECONNREFUSED);
    close(r1);
    int r2 = socket(AF_INET, SOCK_STREAM | SOCK_NONBLOCK, 0);
    errno = 0;
    int cr = connect(r2, (struct sockaddr *)&refuse, sizeof refuse);
    CK(cr == -1 && (errno == EINPROGRESS || errno == ECONNREFUSED));
    if (errno == EINPROGRESS) {
        CK(pollone(r2, POLLOUT, 2000, &re) == 1);
        vl = sizeof v;
        CK(getsockopt(r2, SOL_SOCKET, SO_ERROR, &v, &vl) == 0 && v == ECONNREFUSED);
        vl = sizeof v;
        CK(getsockopt(r2, SOL_SOCKET, SO_ERROR, &v, &vl) == 0 && v == 0);   /* reading clears it */
    }
    close(r2);
    close(s);
}

static void test_args(void) {
    CKE(socket(AF_UNIX, SOCK_STREAM, 0), EAFNOSUPPORT);
    CKE(socket(AF_INET, 99, 0), EINVAL);
    CKE(socket(AF_INET, SOCK_DGRAM, IPPROTO_TCP), EPROTONOSUPPORT);
    int s = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in a = lo(1);
    CKE(bind(s, (struct sockaddr *)&a, 4), EINVAL);
    CKE(bind(s, NULL, 0), EINVAL);
    struct sockaddr_in un = lo(1);
    un.sin_family = AF_UNIX;
    CKE(connect(s, (struct sockaddr *)&un, sizeof un), EAFNOSUPPORT);
    CKE(accept(s, NULL, NULL), EINVAL);   /* not listening */
    char b[4];
    CKE(recv(s, b, 4, 0), ENOTCONN);
    CKE(setsockopt(s, SOL_SOCKET, 0x7777, b, 4), ENOPROTOOPT);
    int one = 1;
    CKE(setsockopt(s, SOL_SOCKET, SO_ERROR, &one, 4), ENOPROTOOPT);   /* read only */
    CKE(setsockopt(s, SOL_SOCKET, SO_REUSEADDR, &one, 2), EINVAL);
    CKE(setsockopt(s, 77, 1, &one, 4), ENOPROTOOPT);
    /* option round trips */
    int v = 0;
    socklen_t vl = sizeof v;
    one = 1;
    CK(setsockopt(s, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one) == 0);
    CK(getsockopt(s, IPPROTO_TCP, TCP_NODELAY, &v, &vl) == 0 && v != 0);
    int ttl = 33;
    vl = sizeof v;
    CK(setsockopt(s, IPPROTO_IP, IP_TTL, &ttl, sizeof ttl) == 0 && getsockopt(s, IPPROTO_IP, IP_TTL, &v, &vl) == 0 && v == 33);
    struct timeval tv = {1, 500000}, tg;
    vl = sizeof tg;
    CK(setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv) == 0);
    CK(getsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &tg, &vl) == 0 && vl == sizeof tg && tg.tv_sec == 1 && tg.tv_usec == 500000);
    struct linger lg = {1, 5}, lgg;
    vl = sizeof lgg;
    CK(setsockopt(s, SOL_SOCKET, SO_LINGER, &lg, sizeof lg) == 0);
    CK(getsockopt(s, SOL_SOCKET, SO_LINGER, &lgg, &vl) == 0 && lgg.l_onoff && lgg.l_linger == 5);
    vl = 2;   /* a short buffer: truncated, *len = the option's size */
    v = 0x7777;
    CK(getsockopt(s, SOL_SOCKET, SO_SNDBUF, &v, &vl) == 0 && vl == sizeof(int));
    close(s);
    /* UDP: sendto / recvfrom carry the addresses, listen is refused */
    int u1 = socket(AF_INET, SOCK_DGRAM, 0), u2 = socket(AF_INET, SOCK_DGRAM, 0);
    unsigned pu1 = sockwire_free_port(1), pu2 = sockwire_free_port(1);
    struct sockaddr_in z = lo(pu1), z2 = lo(pu2);
    CK(pu1 != pu2 && bind(u1, (struct sockaddr *)&z, sizeof z) == 0 && bind(u2, (struct sockaddr *)&z2, sizeof z2) == 0);
    struct sockaddr_in to = lo(pu1);
    CKE(listen(u1, 1), EOPNOTSUPP);
    CK(sendto(u2, "dgram", 5, 0, (struct sockaddr *)&to, sizeof to) == 5);
    short re = 0;
    CK(pollone(u1, POLLIN, 1000, &re) == 1);
    struct sockaddr_in from;
    socklen_t fl = sizeof from;
    char buf[16];
    CK(recvfrom(u1, buf, sizeof buf, 0, (struct sockaddr *)&from, &fl) == 5 && !memcmp(buf, "dgram", 5));
    CK(fl == sizeof from && is_lo(&from) && bs(from.sin_port) == pu2);
    CKE(recvfrom(u1, buf, sizeof buf, MSG_DONTWAIT, NULL, NULL), EWOULDBLOCK);
    close(u1);
    close(u2);
}

/* the raw services: argument counts, handles, the answer of every number that is not a declared service */
static void test_raw(void) {
    CK(raw(FNVNX_PLAT_SOCK_LISTEN, 1, 5, 0, 0, 0, 0) == (uint64_t)-FNVNX_WE_INVAL);   /* wrong nargs */
    CK(raw(FNVNX_PLAT_SOCK_LISTEN, 2, 12345, 1, 0, 0, 0) == (uint64_t)-9);            /* a handle that is not ours */
    CK(raw(FNVNX_PLAT_SOCK_CLOSE, 1, 0, 0, 0, 0, 0) == (uint64_t)-9);                  /* e.g. the host's stdin */
    CK(raw(FNVNX_PLAT_SOCK_SOCKET, 3, 99, 1, 0, 0, 0) == (uint64_t)-97);               /* wire EAFNOSUPPORT */
    CK(raw(FNVNX_PLAT_TZ_AT, 0, 0, 0, 0, 0, 0) == FNVNX_EXT_NO_MODULE);
    for (uint32_t fn = 27; fn <= 29; fn++) CK(raw(fn, 2, 5, 0, 0, 0, 0) == FNVNX_EXT_NO_SERVICE);   /* removed services */
    CK(raw(32, 4, 0, 0, 0, 0, 0) == FNVNX_EXT_NO_SERVICE && raw(33, 3, 0, 0, 0, 0, 0) == FNVNX_EXT_NO_SERVICE);
}

int main(void) {
    sockwire_shim_init();
    CK(sockwire_init() == 0);
    fnvnx_guest_sock_init();
    test_tcp();
    test_args();
    test_raw();
    sockwire_exit();
    printf("sockwire: %d checks, %d failed\n", g_checks, g_fails);
    return g_fails != 0;
}
