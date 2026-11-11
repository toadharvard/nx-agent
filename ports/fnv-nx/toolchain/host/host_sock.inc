/* fnv-nx host: BSD socket services of the platform module (FNVNX_PLAT_SOCK_*, hostapi.h; the wire: fnvnx/sockwire.h;
 * the guest side: toolchain/guest/sock_guest.c). #included by host.c; in tests/s4/sockwire it is built against glibc.
 *
 * Rules:
 *  - A handle is the descriptor number of a socket this file created (SOCKET, ACCEPT); the registry below is the set of
 *    open ones, so the guest can never name another descriptor of the host (-EBADF).
 *  - Every constant is translated by NAME through the X-macro lists of sockwire.h (errno, families, types, options,
 *    flags, poll events, getaddrinfo flags and codes), so libnx's (BSD) and glibc's numbers need not agree.
 *  - New and accepted sockets are blocking (BSD accept() hands on the listener's O_NONBLOCK, which is cleared).
 *  - Sends use MSG_NOSIGNAL where the OS has it; EINTR cannot reach the guest (retried here).
 *  - Switch: the socket service is initialised by host_sock_init() and released by host_sock_exit(). */
#include <errno.h>
#include <fcntl.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <pthread.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#include "fnvnx/hostapi.h"
#include "fnvnx/sockwire.h"

#ifdef __SWITCH__
#define SK_BSD_SOCKADDR 1   /* BSD-derived sockaddrs (sa_len; libnx's sockaddr_in6 has its family at offset 0 and sa_family at 1) */
#endif

/* 0, or the Result of the socket service's initialisation */
static unsigned host_sock_init(void) {
#ifdef __SWITCH__
    return socketInitializeDefault();
#else
    return 0;
#endif
}
static void host_sock_exit(void) {
#ifdef __SWITCH__
    socketExit();
#endif
}

static pthread_mutex_t sk_mu = PTHREAD_MUTEX_INITIALIZER;
static int *sk_h;
static size_t sk_n, sk_cap;
static int sk_add(int fd) {
    int rc = 0;
    pthread_mutex_lock(&sk_mu);
    if (sk_n == sk_cap) {
        size_t nc = sk_cap ? sk_cap * 2 : 16;
        int *p = realloc(sk_h, nc * sizeof *p);
        if (p) { sk_h = p; sk_cap = nc; } else rc = -1;
    }
    if (!rc) sk_h[sk_n++] = fd;
    pthread_mutex_unlock(&sk_mu);
    return rc;
}
static int sk_has(int fd) {
    int f = 0;
    pthread_mutex_lock(&sk_mu);
    for (size_t i = 0; i < sk_n && !f; i++) f = sk_h[i] == fd;
    pthread_mutex_unlock(&sk_mu);
    return f;
}
static int sk_del(int fd) {   /* 1 if it was open */
    int f = 0;
    pthread_mutex_lock(&sk_mu);
    for (size_t i = 0; i < sk_n && !f; i++)
        if (sk_h[i] == fd) { sk_h[i] = sk_h[--sk_n]; f = 1; }
    pthread_mutex_unlock(&sk_mu);
    return f;
}

/* ---- by name */
static int sk_werr(int e) {
#define X(N, v) if (e == N) return v;
    FNVNX_WIRE_ERRNO_LIST(X)
#undef X
    return FNVNX_WE_IO;
}
static uint64_t sk_err(void) { return (uint64_t)-(int64_t)sk_werr(errno); }
static uint64_t sk_e(int wire_errno) { return (uint64_t)-(int64_t)wire_errno; }
static uint64_t sk_h_err(int host_errno) { return sk_e(sk_werr(host_errno)); }

static int sk_af_from_wire(int w, int *h) {
#define X(N, v) if (w == (v)) { *h = N; return 0; }
    FNVNX_WIRE_AF_LIST(X)
#undef X
    return -1;
}
static int sk_af_to_wire(int h, int *w) {
#define X(N, v) if (h == N) { *w = v; return 0; }
    FNVNX_WIRE_AF_LIST(X)
#undef X
    return -1;
}
static int sk_type_from_wire(int w, int *h) {
#define X(N, v) if (w == (v)) { *h = N; return 0; }
    FNVNX_WIRE_SOCKTYPE_LIST(X)
#undef X
    return -1;
}
static int sk_type_to_wire(int h, int *w) {
#define X(N, v) if (h == N) { *w = v; return 0; }
    FNVNX_WIRE_SOCKTYPE_LIST(X)
#undef X
    return -1;
}
static int sk_msg_from_wire(int w, int *h) {   /* all bits must be known */
    int r = 0;
#define X(N, v) if (w & (v)) { w &= ~(v); r |= N; }
    FNVNX_WIRE_MSG_LIST(X)
#undef X
    if (w) return -1;
#ifdef MSG_NOSIGNAL
    r |= MSG_NOSIGNAL;
#endif
    *h = r;
    return 0;
}
static int sk_ai_from_wire(int w, int *h) {
    int r = 0;
#define X(N, v) if (w & (v)) { w &= ~(v); r |= N; }
    FNVNX_WIRE_AI_LIST(X)
#undef X
    if (w) return -1;
    *h = r;
    return 0;
}
static int sk_ai_to_wire(int h) {
    int r = 0;
#define X(N, v) if (h & N) r |= (v);
    FNVNX_WIRE_AI_LIST(X)
#undef X
    return r;
}
static int sk_eai_to_wire(int h) {   /* a code outside POSIX's set is a failure of the resolver: EAI_FAIL */
#define X(N, v) if (h == N) return v;
    FNVNX_WIRE_EAI_LIST(X)
#undef X
    return 2004;
}
static short sk_poll_from_wire(unsigned w) {
    int r = 0;
#define X(N, v) if (w & (v)) r |= N;
    FNVNX_WIRE_POLL_LIST(X)
#undef X
    return (short)r;
}
static uint16_t sk_poll_to_wire(int h) {
    unsigned r = 0;
#define X(N, v) if (h & N) r |= (v);
    FNVNX_WIRE_POLL_LIST(X)
#undef X
    return (uint16_t)r;
}

/* ---- addresses */
static uint16_t sk_bswap16(uint16_t v) { return (uint16_t)((v >> 8) | (v << 8)); }
/* wire -> host; 0 or a host errno */
static int sk_sa_from_wire(const FnvnxWireSockaddr *w, struct sockaddr_storage *ss, socklen_t *len) {
    memset(ss, 0, sizeof *ss);
    if (w->family == FNVNX_WAF_INET) {
        struct sockaddr_in *a = (struct sockaddr_in *)ss;
        a->sin_family = AF_INET;
        a->sin_port = sk_bswap16(w->port);
        memcpy(&a->sin_addr, w->addr, 4);
#ifdef SK_BSD_SOCKADDR
        a->sin_len = sizeof *a;
#endif
        *len = sizeof *a;
    } else if (w->family == FNVNX_WAF_INET6) {
        struct sockaddr_in6 *a = (struct sockaddr_in6 *)ss;
        a->sin6_port = sk_bswap16(w->port);
        memcpy(&a->sin6_addr, w->addr, 16);
        a->sin6_flowinfo = w->flowinfo;
        a->sin6_scope_id = w->scope_id;
#ifdef SK_BSD_SOCKADDR
        ((struct sockaddr *)a)->sa_len = sizeof *a;
        ((struct sockaddr *)a)->sa_family = AF_INET6;
#else
        a->sin6_family = AF_INET6;
#endif
        *len = sizeof *a;
    } else if (w->family == FNVNX_WAF_UNSPEC) {
        ss->ss_family = AF_UNSPEC;
        *len = sizeof(struct sockaddr);
    } else {
        return EAFNOSUPPORT;
    }
    return 0;
}
/* host -> wire; an address of no wire form (or none) is WAF_UNSPEC */
static void sk_sa_to_wire(const struct sockaddr *sa, socklen_t len, FnvnxWireSockaddr *w) {
    memset(w, 0, sizeof *w);
    if (!sa || len < 2) return;
    int fam = sa->sa_family;
    if (len >= sizeof(struct sockaddr_in6) && ((const uint8_t *)sa)[0] == AF_INET6) fam = AF_INET6;
    if (fam == AF_INET && len >= sizeof(struct sockaddr_in)) {
        const struct sockaddr_in *a = (const struct sockaddr_in *)sa;
        w->family = FNVNX_WAF_INET;
        w->port = sk_bswap16(a->sin_port);
        memcpy(w->addr, &a->sin_addr, 4);
    } else if (fam == AF_INET6 && len >= sizeof(struct sockaddr_in6)) {
        const struct sockaddr_in6 *a = (const struct sockaddr_in6 *)sa;
        w->family = FNVNX_WAF_INET6;
        w->port = sk_bswap16(a->sin6_port);
        memcpy(w->addr, &a->sin6_addr, 16);
        w->flowinfo = a->sin6_flowinfo;
        w->scope_id = a->sin6_scope_id;
    }
}
static int sk_put(void *p, const void *src, size_t n) { memcpy(p, src, n); return 0; }

/* ---- options: (wire level, wire option) -> host (level, option) + shape; -1 = ENOPROTOOPT */
static int sk_opt(unsigned wl, unsigned wo, int *hl, int *ho, int *shape) {
#define X(N, v, s) if (wo == (unsigned)(v)) { *ho = N; *shape = s; return 0; }
    if (wl == FNVNX_WLVL_SOCKET) {
        *hl = SOL_SOCKET;
        FNVNX_WIRE_SO_OPT_LIST(X)
    } else if (wl == 6) {
        *hl = IPPROTO_TCP;
        FNVNX_WIRE_TCP_OPT_LIST(X)
    } else if (wl == 0) {
        *hl = IPPROTO_IP;
        FNVNX_WIRE_IP_OPT_LIST(X)
    } else if (wl == 41) {
        *hl = IPPROTO_IPV6;
        FNVNX_WIRE_IPV6_OPT_LIST(X)
    }
#undef X
    return -1;
}
/* BSD-derived stacks take a u_char for these two */
static int sk_uchar_opt(int hl, int ho) {
#ifdef SK_BSD_SOCKADDR
    return hl == IPPROTO_IP && (ho == IP_MULTICAST_TTL || ho == IP_MULTICAST_LOOP);
#else
    (void)hl; (void)ho;
    return 0;
#endif
}

/* ---- getaddrinfo tickets */
typedef struct { int id; struct addrinfo *res; } SkTicket;
static SkTicket *sk_t;
static size_t sk_tn, sk_tcap;
static int sk_tnext = 1;
static int sk_tadd(struct addrinfo *res) {   /* a ticket > 0, or 0 when out of memory */
    int id = 0;
    pthread_mutex_lock(&sk_mu);
    if (sk_tn == sk_tcap) {
        size_t nc = sk_tcap ? sk_tcap * 2 : 8;
        SkTicket *p = realloc(sk_t, nc * sizeof *p);
        if (p) { sk_t = p; sk_tcap = nc; }
    }
    if (sk_tn < sk_tcap) {
        id = sk_tnext;
        sk_tnext = sk_tnext == INT32_MAX ? 1 : sk_tnext + 1;
        sk_t[sk_tn].id = id;
        sk_t[sk_tn++].res = res;
    }
    pthread_mutex_unlock(&sk_mu);
    return id;
}
static struct addrinfo *sk_tfind(int id, int take) {
    struct addrinfo *r = NULL;
    pthread_mutex_lock(&sk_mu);
    for (size_t i = 0; i < sk_tn && !r; i++)
        if (sk_t[i].id == id) {
            r = sk_t[i].res;
            if (take) sk_t[i] = sk_t[--sk_tn];
        }
    pthread_mutex_unlock(&sk_mu);
    return r;
}
static size_t sk_al8(size_t n) { return (n + 7) & ~(size_t)7; }
static int sk_wire_family(const struct addrinfo *a) {   /* 0: no wire form, the record is not carried */
    int w;
    if (a->ai_family == AF_UNSPEC || sk_af_to_wire(a->ai_family, &w) || !a->ai_addr) return 0;
    return 1;
}

static uint64_t sk_gai_open(const uint64_t *a) {
    const char *node = (const char *)(uintptr_t)a[0], *svc = (const char *)(uintptr_t)a[1];
    struct addrinfo hints, *hp = NULL, *res = NULL;
    if (a[2]) {
        FnvnxWireAddrinfo wh;
        memcpy(&wh, (const void *)(uintptr_t)a[2], sizeof wh);
        memset(&hints, 0, sizeof hints);
        if (sk_ai_from_wire(wh.flags, &hints.ai_flags)) return sk_e(2001);
        if (sk_af_from_wire(wh.family, &hints.ai_family)) return sk_e(2005);
        if (wh.socktype && sk_type_from_wire(wh.socktype, &hints.ai_socktype)) return sk_e(2006);
        hints.ai_protocol = wh.protocol;
        hp = &hints;
    }
    errno = 0;
    int rc = getaddrinfo(node, svc, hp, &res);
    if (rc) {
        if (rc == EAI_SYSTEM && a[3]) {
            int32_t we = (int32_t)sk_werr(errno);
            memcpy((void *)(uintptr_t)a[3], &we, sizeof we);
        }
        return sk_e(sk_eai_to_wire(rc));
    }
    if (!res) return sk_e(2002);
    int id = sk_tadd(res);
    if (!id) { freeaddrinfo(res); return sk_e(2008); }
    return (uint64_t)id;
}
static uint64_t sk_gai_fetch(const uint64_t *a) {
    int id = (int)a[0];
    if ((uint64_t)id != a[0] || id <= 0) return sk_e(FNVNX_WE_INVAL);
    struct addrinfo *res = sk_tfind(id, 0);
    if (!res) return sk_e(FNVNX_WE_INVAL);
    uint64_t cap = a[2];
    uint64_t total = 0;
    for (struct addrinfo *p = res; p; p = p->ai_next)
        if (sk_wire_family(p)) total += sizeof(FnvnxWireAddrinfo) + sk_al8(p->ai_canonname ? strlen(p->ai_canonname) + 1 : 0);
    if (cap < total) return total;
    uint8_t *out = (uint8_t *)(uintptr_t)a[1];
    if (out) {
        for (struct addrinfo *p = res; p; p = p->ai_next) {
            if (!sk_wire_family(p)) continue;
            FnvnxWireAddrinfo w;
            size_t cl = p->ai_canonname ? strlen(p->ai_canonname) + 1 : 0;
            memset(&w, 0, sizeof w);
            w.flags = sk_ai_to_wire(p->ai_flags);
            sk_af_to_wire(p->ai_family, &w.family);
            if (p->ai_socktype) { int t = 0; if (!sk_type_to_wire(p->ai_socktype, &t)) w.socktype = t; }
            w.protocol = p->ai_protocol;
            w.canon_len = (uint32_t)cl;
            sk_sa_to_wire(p->ai_addr, p->ai_addrlen, &w.addr);
            sk_put(out, &w, sizeof w);
            memset(out + sizeof w, 0, sk_al8(cl));
            if (cl) memcpy(out + sizeof w, p->ai_canonname, cl);
            out += sizeof w + sk_al8(cl);
        }
    }
    sk_tfind(id, 1);
    freeaddrinfo(res);
    return total;
}

/* ---- the dispatcher */
static uint64_t host_sock_call(uint32_t fn, const uint64_t *a, uint32_t n) {
    static const uint8_t want[] = {3, 1, 2, 2, 2, 2, 5, 5, 5, 5, 3, 2, 2, 2, 1, 2, 4, 3};   /* fn 16..33 */
    if (fn < FNVNX_PLAT_SOCK_FIRST || fn > FNVNX_PLAT_SOCK_LAST || n != want[fn - FNVNX_PLAT_SOCK_FIRST])
        return sk_e(FNVNX_WE_INVAL);
    if (!a) return FNVNX_EXT_BAD_NARGS;   /* every service here takes >= 1 word: a null array would be read at a[0] (hostapi.h: nargs > 0 with a null array) */
    if (fn == FNVNX_PLAT_SOCK_GAI_OPEN) return sk_gai_open(a);
    if (fn == FNVNX_PLAT_SOCK_GAI_FETCH) return sk_gai_fetch(a);
    if (fn == FNVNX_PLAT_SOCK_POLL) {
        FnvnxWirePollfd *w = (FnvnxWirePollfd *)(uintptr_t)a[0];
        uint64_t nf = a[1];
        int timeout = (int32_t)(uint32_t)a[2];
        if (nf > 0x1000000u) return sk_e(FNVNX_WE_INVAL);
        if (nf && !w) return sk_e(14);
        struct pollfd stack[32], *p = stack;
        if (nf > 32 && !(p = malloc(nf * sizeof *p))) return sk_e(12);
        unsigned nval = 0;
        for (uint64_t i = 0; i < nf; i++) {
            FnvnxWirePollfd e;
            memcpy(&e, &w[i], sizeof e);
            p[i].events = sk_poll_from_wire(e.events);
            p[i].revents = 0;
            p[i].fd = e.fd < 0 ? -1 : e.fd;
            if (e.fd >= 0 && !sk_has(e.fd)) { p[i].fd = -1; nval++; }
        }
        int rc;
        do rc = poll(p, (nfds_t)nf, nval ? 0 : timeout);   /* an entry that is ready already ends the wait */
        while (rc < 0 && errno == EINTR);
        uint64_t res;
        if (rc < 0) res = sk_err();
        else {
            uint64_t cnt = 0;
            for (uint64_t i = 0; i < nf; i++) {
                FnvnxWirePollfd e;
                memcpy(&e, &w[i], sizeof e);
                e.revents = (e.fd >= 0 && p[i].fd < 0) ? FNVNX_WPOLLNVAL : sk_poll_to_wire(p[i].revents);
                memcpy(&w[i], &e, sizeof e);
                cnt += e.revents != 0;
            }
            res = cnt;
        }
        if (p != stack) free(p);
        return res;
    }
    if (fn == FNVNX_PLAT_SOCK_SOCKET) {
        int d, t;
        if (sk_af_from_wire((int)a[0], &d) || (int64_t)a[0] != (int)a[0]) return sk_e(97);   /* EAFNOSUPPORT */
        if (sk_type_from_wire((int)a[1], &t) || (int64_t)a[1] != (int)a[1]) return sk_e(FNVNX_WE_INVAL);
        int fd = socket(d, t, (int)a[2]);
        if (fd < 0) return sk_err();
        if (sk_add(fd)) { close(fd); return sk_e(12); }
        return (uint64_t)fd;
    }
    /* the rest take a handle first */
    int h = (int)a[0];
    if ((int64_t)h != (int64_t)a[0] || !sk_has(h)) return sk_e(9);   /* EBADF */
    switch (fn) {
    case FNVNX_PLAT_SOCK_CLOSE: {
        sk_del(h);
        return close(h) ? sk_err() : 0;
    }
    case FNVNX_PLAT_SOCK_BIND:
    case FNVNX_PLAT_SOCK_CONNECT: {
        FnvnxWireSockaddr w;
        struct sockaddr_storage ss;
        socklen_t sl;
        if (!a[1]) return sk_e(14);
        memcpy(&w, (const void *)(uintptr_t)a[1], sizeof w);
        int e = sk_sa_from_wire(&w, &ss, &sl);
        if (e) return sk_h_err(e);
        int rc = fn == FNVNX_PLAT_SOCK_BIND ? bind(h, (struct sockaddr *)&ss, sl) : connect(h, (struct sockaddr *)&ss, sl);
        if (rc < 0 && errno == EINTR && fn == FNVNX_PLAT_SOCK_CONNECT) return sk_e(FNVNX_WE_INPROGRESS);   /* the connection goes on */
        return rc < 0 ? sk_err() : 0;
    }
    case FNVNX_PLAT_SOCK_LISTEN:
        return listen(h, (int)(int64_t)a[1]) ? sk_err() : 0;
    case FNVNX_PLAT_SOCK_ACCEPT: {
        struct sockaddr_storage ss;
        socklen_t sl = sizeof ss;
        int nfd;
        do nfd = accept(h, (struct sockaddr *)&ss, &sl);
        while (nfd < 0 && errno == EINTR);
        if (nfd < 0) return sk_err();
        int fl = fcntl(nfd, F_GETFL, 0);
        if (fl < 0 || ((fl & O_NONBLOCK) && fcntl(nfd, F_SETFL, fl & ~O_NONBLOCK) < 0)) {
            int e = errno;
            close(nfd);
            return sk_h_err(e);
        }
        if (sk_add(nfd)) { close(nfd); return sk_e(12); }
        if (a[1]) {
            FnvnxWireSockaddr w;
            sk_sa_to_wire((struct sockaddr *)&ss, sl, &w);
            memcpy((void *)(uintptr_t)a[1], &w, sizeof w);
        }
        return (uint64_t)nfd;
    }
    case FNVNX_PLAT_SOCK_SENDTO: {
        int fl;
        if (a[2] > 0x7FFFFFFFu) return sk_e(FNVNX_WE_INVAL);
        if (sk_msg_from_wire((int)a[3], &fl) || a[3] > 0xFFFFFFFFu) return sk_e(95);   /* EOPNOTSUPP */
        struct sockaddr_storage ss;
        socklen_t sl = 0;
        if (a[4]) {
            FnvnxWireSockaddr w;
            memcpy(&w, (const void *)(uintptr_t)a[4], sizeof w);
            int e = sk_sa_from_wire(&w, &ss, &sl);
            if (e) return sk_h_err(e);
        }
        ssize_t r;
        do r = sendto(h, (const void *)(uintptr_t)a[1], (size_t)a[2], fl, a[4] ? (struct sockaddr *)&ss : NULL, sl);
        while (r < 0 && errno == EINTR);
        return r < 0 ? sk_err() : (uint64_t)r;
    }
    case FNVNX_PLAT_SOCK_RECVFROM: {
        int fl;
        if (a[2] > 0x7FFFFFFFu) return sk_e(FNVNX_WE_INVAL);
        if (sk_msg_from_wire((int)a[3], &fl) || a[3] > 0xFFFFFFFFu) return sk_e(95);
        struct sockaddr_storage ss;
        socklen_t sl = sizeof ss;
        memset(&ss, 0, sizeof ss);
        ssize_t r;
        do r = recvfrom(h, (void *)(uintptr_t)a[1], (size_t)a[2], fl, a[4] ? (struct sockaddr *)&ss : NULL, a[4] ? &sl : NULL);
        while (r < 0 && errno == EINTR);
        if (r < 0) return sk_err();
        if (a[4]) {
            FnvnxWireSockaddr w;
            sk_sa_to_wire((struct sockaddr *)&ss, sl, &w);
            memcpy((void *)(uintptr_t)a[4], &w, sizeof w);
        }
        return (uint64_t)r;
    }
    case FNVNX_PLAT_SOCK_SETSOCKOPT:
    case FNVNX_PLAT_SOCK_GETSOCKOPT: {
        int hl, ho, shape;
        if (sk_opt((unsigned)a[1], (unsigned)a[2], &hl, &ho, &shape) || a[1] > 0xFFFFFFFFu || a[2] > 0xFFFFFFFFu)
            return sk_e(92);   /* ENOPROTOOPT */
        if (!a[3]) return sk_e(14);
        union { int i; struct linger l; struct timeval t; unsigned char c; } v;
        socklen_t vl;
        if (fn == FNVNX_PLAT_SOCK_SETSOCKOPT) {
            if (shape == FNVNX_WOPT_TYPE || shape == FNVNX_WOPT_ERRNO) return sk_e(92);   /* read only */
            if (a[4] != (shape == FNVNX_WOPT_INT ? sizeof(int32_t) : shape == FNVNX_WOPT_LINGER ? sizeof(FnvnxWireLinger) : sizeof(FnvnxWireTimeval)))
                return sk_e(FNVNX_WE_INVAL);
            memset(&v, 0, sizeof v);
            if (shape == FNVNX_WOPT_INT) {
                int32_t x;
                memcpy(&x, (const void *)(uintptr_t)a[3], sizeof x);
                if (sk_uchar_opt(hl, ho)) { v.c = (unsigned char)x; vl = 1; } else { v.i = x; vl = sizeof v.i; }
            } else if (shape == FNVNX_WOPT_LINGER) {
                FnvnxWireLinger x;
                memcpy(&x, (const void *)(uintptr_t)a[3], sizeof x);
                v.l.l_onoff = x.onoff;
                v.l.l_linger = x.linger_s;
                vl = sizeof v.l;
            } else {
                FnvnxWireTimeval x;
                memcpy(&x, (const void *)(uintptr_t)a[3], sizeof x);
                v.t.tv_sec = (time_t)x.sec;
                v.t.tv_usec = (suseconds_t)x.usec;
                vl = sizeof v.t;
            }
            return setsockopt(h, hl, ho, &v, vl) ? sk_err() : 0;
        }
        uint32_t cap;
        if (!a[4]) return sk_e(14);
        memcpy(&cap, (const void *)(uintptr_t)a[4], sizeof cap);
        vl = sizeof v;
        memset(&v, 0, sizeof v);
        if (getsockopt(h, hl, ho, &v, &vl)) return sk_err();
        union { int32_t i; FnvnxWireLinger l; FnvnxWireTimeval t; } w;
        uint32_t wl;
        memset(&w, 0, sizeof w);
        if (shape == FNVNX_WOPT_LINGER) {
            w.l.onoff = v.l.l_onoff;
            w.l.linger_s = v.l.l_linger;
            wl = sizeof w.l;
        } else if (shape == FNVNX_WOPT_TIMEVAL) {
            w.t.sec = v.t.tv_sec;
            w.t.usec = v.t.tv_usec;
            wl = sizeof w.t;
        } else {
            int x = vl == 1 ? v.c : v.i;
            if (shape == FNVNX_WOPT_TYPE) { int t; x = sk_type_to_wire(x, &t) ? -1 : t; }
            else if (shape == FNVNX_WOPT_ERRNO) x = x ? sk_werr(x) : 0;
            w.i = x;
            wl = sizeof w.i;
        }
        memcpy((void *)(uintptr_t)a[3], &w, cap < wl ? cap : wl);
        memcpy((void *)(uintptr_t)a[4], &wl, sizeof wl);
        return 0;
    }
    case FNVNX_PLAT_SOCK_SHUTDOWN:
        if (a[1] > 2) return sk_e(FNVNX_WE_INVAL);
        return shutdown(h, a[1] == 0 ? SHUT_RD : a[1] == 1 ? SHUT_WR : SHUT_RDWR) ? sk_err() : 0;
    case FNVNX_PLAT_SOCK_GETSOCKNAME:
    case FNVNX_PLAT_SOCK_GETPEERNAME: {
        struct sockaddr_storage ss;
        socklen_t sl = sizeof ss;
        memset(&ss, 0, sizeof ss);
        if (!a[1]) return sk_e(14);
        if ((fn == FNVNX_PLAT_SOCK_GETSOCKNAME ? getsockname : getpeername)(h, (struct sockaddr *)&ss, &sl)) return sk_err();
        FnvnxWireSockaddr w;
        sk_sa_to_wire((struct sockaddr *)&ss, sl, &w);
        memcpy((void *)(uintptr_t)a[1], &w, sizeof w);
        return 0;
    }
    case FNVNX_PLAT_SOCK_GETFL: {
        int fl = fcntl(h, F_GETFL, 0);
        if (fl < 0) return sk_err();
        return FNVNX_WO_RDWR | ((fl & O_NONBLOCK) ? FNVNX_WO_NONBLOCK : 0);
    }
    case FNVNX_PLAT_SOCK_SETFL: {
        int fl = fcntl(h, F_GETFL, 0);
        if (fl < 0) return sk_err();
        fl = (a[1] & FNVNX_WO_NONBLOCK) ? (fl | O_NONBLOCK) : (fl & ~O_NONBLOCK);
        return fcntl(h, F_SETFL, fl) < 0 ? sk_err() : 0;
    }
    }
    return sk_e(FNVNX_WE_INVAL);
}
