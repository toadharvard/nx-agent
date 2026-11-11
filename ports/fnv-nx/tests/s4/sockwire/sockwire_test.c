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
static unsigned port_of(int fd) {
    struct sockaddr_in a;
    socklen_t l = sizeof a;
    return getsockname(fd, (struct sockaddr *)&a, &l) == 0 && l == sizeof a ? bs(a.sin_port) : 0;
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
    struct sockaddr_in a = lo(0), b;
    socklen_t bl = sizeof b;
    CK(bind(s, (struct sockaddr *)&a, sizeof a) == 0);
    CK(getsockname(s, (struct sockaddr *)&b, &bl) == 0 && bl == sizeof b && is_lo(&b) && b.sin_port != 0);
    unsigned port = bs(b.sin_port);
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
    CK(ac >= 0 && pl == sizeof peer && is_lo(&peer) && bs(peer.sin_port) == port_of(c));
    CK(!nb(ac));   /* the listener is non-blocking, the accepted socket is not */
    struct sockaddr_in pn;
    socklen_t pnl = sizeof pn;
    CK(getpeername(c, (struct sockaddr *)&pn, &pnl) == 0 && is_lo(&pn) && bs(pn.sin_port) == port);

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

    /* shutdown / orderly close */
    CK(shutdown(c, 1) == 0);
    CK(recv(ac, buf, sizeof buf, 0) == 0);
    CKE(shutdown(c, 7), EINVAL);
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
    CK(d >= 20 && port_of(d) == 0 && nb(d));
    CK(fcntl(d, F_SETFL, 0) == 0 && !nb(n1));   /* one open file description */
    CK(close(d) == 0 && close(n1) == 0);
    CKE(fcntl(s, 99, 0), EINVAL);

    /* refused connect (blocking, then non-blocking) to a port nobody listens on */
    int dead = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in da = lo(0);
    CK(bind(dead, (struct sockaddr *)&da, sizeof da) == 0);
    struct sockaddr_in refuse = lo(port_of(dead));
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
    struct sockaddr_in z = lo(0);
    CK(bind(u1, (struct sockaddr *)&z, sizeof z) == 0);
    struct sockaddr_in to = lo(port_of(u1));
    CKE(listen(u1, 1), EOPNOTSUPP);
    CK(sendto(u2, "dgram", 5, 0, (struct sockaddr *)&to, sizeof to) == 5);
    short re = 0;
    CK(pollone(u1, POLLIN, 1000, &re) == 1);
    struct sockaddr_in from;
    socklen_t fl = sizeof from;
    char buf[16];
    CK(recvfrom(u1, buf, sizeof buf, 0, (struct sockaddr *)&from, &fl) == 5 && !memcmp(buf, "dgram", 5));
    CK(fl == sizeof from && is_lo(&from) && bs(from.sin_port) == port_of(u2) && port_of(u2) != 0);
    CKE(recvfrom(u1, buf, sizeof buf, MSG_DONTWAIT, NULL, NULL), EWOULDBLOCK);
    close(u1);
    close(u2);
}

static void test_gai(void) {
    struct addrinfo h, *r = NULL;
    memset(&h, 0, sizeof h);
    h.ai_family = AF_INET;
    h.ai_socktype = SOCK_STREAM;
    h.ai_flags = AI_NUMERICHOST;
    CK(getaddrinfo("127.0.0.1", "80", &h, &r) == 0 && r);
    if (r) {
        struct sockaddr_in *a = (struct sockaddr_in *)r->ai_addr;
        CK(r->ai_family == AF_INET && r->ai_socktype == SOCK_STREAM && r->ai_addrlen == sizeof *a && is_lo(a) && bs(a->sin_port) == 80);
        CK(r->ai_protocol == IPPROTO_TCP && !r->ai_next);
        freeaddrinfo(r);
    }
    r = NULL;
    memset(&h, 0, sizeof h);
    h.ai_family = AF_INET;
    h.ai_flags = AI_CANONNAME;
    CK(getaddrinfo("localhost", NULL, &h, &r) == 0 && r);
    if (r) {
        int found = 0, canon = r->ai_canonname != NULL;
        for (struct addrinfo *p = r; p; p = p->ai_next)
            found += p->ai_family == AF_INET && is_lo((struct sockaddr_in *)p->ai_addr);
        CK(found >= 1 && canon);
        freeaddrinfo(r);
    }
    r = NULL;
    memset(&h, 0, sizeof h);
    h.ai_family = AF_INET6;
    h.ai_flags = AI_NUMERICHOST;
    CK(getaddrinfo("::1", "9", &h, &r) == 0 && r);
    if (r) {
        struct sockaddr_in6 *a = (struct sockaddr_in6 *)r->ai_addr;
        static const unsigned char l6[16] = {[15] = 1};
        CK(r->ai_family == AF_INET6 && r->ai_addrlen == sizeof *a && ((struct sockaddr *)a)->sa_family == AF_INET6 &&
           !memcmp(&a->sin6_addr, l6, 16) && bs(a->sin6_port) == 9);
        freeaddrinfo(r);
    }
    r = NULL;
    memset(&h, 0, sizeof h);
    h.ai_family = AF_INET;
    h.ai_socktype = SOCK_DGRAM;
    h.ai_flags = AI_PASSIVE;
    CK(getaddrinfo(NULL, "5000", &h, &r) == 0 && r && is_lo((struct sockaddr_in *)r->ai_addr) == 0 &&
       ((struct sockaddr_in *)r->ai_addr)->sin_addr.s_addr == 0 && bs(((struct sockaddr_in *)r->ai_addr)->sin_port) == 5000);
    if (r) freeaddrinfo(r);
    /* the failures the guest tells apart: libnx's EAI numbers, not glibc's */
    memset(&h, 0, sizeof h);
    h.ai_flags = AI_NUMERICHOST;
    CK(getaddrinfo("localhost", NULL, &h, &r) == EAI_NONAME);
    h.ai_flags = 0x40000000;
    CK(getaddrinfo("127.0.0.1", NULL, &h, &r) == EAI_BADFLAGS);
    h.ai_flags = 0;
    h.ai_family = 77;
    CK(getaddrinfo("127.0.0.1", NULL, &h, &r) == EAI_FAMILY);
    h.ai_family = AF_INET;
    h.ai_socktype = 77;
    CK(getaddrinfo("127.0.0.1", NULL, &h, &r) == EAI_SOCKTYPE);
    h.ai_socktype = 0;
    h.ai_flags = AI_NUMERICSERV;
    CK(getaddrinfo("127.0.0.1", "http", &h, &r) == EAI_NONAME);
    CK(getaddrinfo(NULL, NULL, NULL, &r) == EAI_NONAME);
    h.ai_flags = AI_NUMERICHOST;
    CK(getaddrinfo("999.1.1.1", NULL, &h, &r) == EAI_NONAME);
}

/* the raw services: argument counts, handles, the GAI_FETCH capacity protocol */
static void test_raw(void) {
    CK(raw(FNVNX_PLAT_SOCK_LISTEN, 1, 5, 0, 0, 0, 0) == (uint64_t)-FNVNX_WE_INVAL);   /* wrong nargs */
    CK(raw(FNVNX_PLAT_SOCK_LISTEN, 2, 12345, 1, 0, 0, 0) == (uint64_t)-9);            /* a handle that is not ours */
    CK(raw(FNVNX_PLAT_SOCK_CLOSE, 1, 0, 0, 0, 0, 0) == (uint64_t)-9);                  /* e.g. the host's stdin */
    CK(raw(FNVNX_PLAT_SOCK_GAI_FETCH, 3, 4242, 0, 0, 0, 0) == (uint64_t)-FNVNX_WE_INVAL);
    CK(raw(FNVNX_PLAT_SOCK_SOCKET, 3, 99, 1, 0, 0, 0) == (uint64_t)-97);               /* wire EAFNOSUPPORT */
    CK(raw(FNVNX_PLAT_TZ_AT, 0, 0, 0, 0, 0, 0) == FNVNX_EXT_NO_MODULE);
    FnvnxWireAddrinfo hint;
    memset(&hint, 0, sizeof hint);
    hint.family = FNVNX_WAF_INET;
    hint.socktype = FNVNX_WSOCK_STREAM;
    hint.flags = 0x4;   /* AI_NUMERICHOST */
    int32_t sys = 0;
    int64_t t = (int64_t)raw(FNVNX_PLAT_SOCK_GAI_OPEN, 4, (uint64_t)(uintptr_t)"127.0.0.1", 0, (uint64_t)(uintptr_t)&hint, (uint64_t)(uintptr_t)&sys, 0);
    CK(t > 0);
    uint64_t total = raw(FNVNX_PLAT_SOCK_GAI_FETCH, 3, (uint64_t)t, 0, 0, 0, 0);
    CK(total == sizeof(FnvnxWireAddrinfo));
    char big[256];
    CK(raw(FNVNX_PLAT_SOCK_GAI_FETCH, 3, (uint64_t)t, (uint64_t)(uintptr_t)big, total - 1, 0, 0) == total);   /* too small: stays */
    CK(raw(FNVNX_PLAT_SOCK_GAI_FETCH, 3, (uint64_t)t, 0, total, 0, 0) == total);                               /* buf 0: released */
    CK(raw(FNVNX_PLAT_SOCK_GAI_FETCH, 3, (uint64_t)t, 0, 0, 0, 0) == (uint64_t)-FNVNX_WE_INVAL);
    t = (int64_t)raw(FNVNX_PLAT_SOCK_GAI_OPEN, 4, (uint64_t)(uintptr_t)"127.0.0.1", 0, (uint64_t)(uintptr_t)&hint, 0, 0);
    CK(t > 0 && raw(FNVNX_PLAT_SOCK_GAI_FETCH, 3, (uint64_t)t, (uint64_t)(uintptr_t)big, sizeof big, 0, 0) == total);
    FnvnxWireAddrinfo w;
    memcpy(&w, big, sizeof w);
    CK(w.family == FNVNX_WAF_INET && w.socktype == FNVNX_WSOCK_STREAM && w.addr.family == FNVNX_WAF_INET &&
       w.addr.addr[0] == 127 && w.addr.addr[3] == 1 && w.canon_len == 0);
    CK(raw(FNVNX_PLAT_SOCK_GAI_FETCH, 3, (uint64_t)t, 0, 0, 0, 0) == (uint64_t)-FNVNX_WE_INVAL);   /* taken: gone */
    hint.flags = 0x40000000;
    CK((int64_t)raw(FNVNX_PLAT_SOCK_GAI_OPEN, 4, (uint64_t)(uintptr_t)"127.0.0.1", 0, (uint64_t)(uintptr_t)&hint, 0, 0) == -FNVNX_WEAI_BADFLAGS);
}

int main(void) {
    sockwire_shim_init();
    CK(sockwire_init() == 0);
    fnvnx_guest_sock_init();
    test_tcp();
    test_args();
    test_gai();
    test_raw();
    sockwire_exit();
    printf("sockwire: %d checks, %d failed\n", g_checks, g_fails);
    return g_fails != 0;
}
