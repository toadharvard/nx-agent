/* fnv-nx ILP32 guest: BSD sockets through the host's FNVNX_PLAT_SOCK_* services (hostapi.h, wire: fnvnx/sockwire.h).
 *
 * Mechanism (the one libnx uses for its own "soc" device): a devoptab named "soc" is registered once, by the
 * first socket()/accept() (sock_dev: a compare-and-swap once, so nothing depends on which start-up code runs first, or
 * at all; fnvnx_guest_sock_init is the same once, callable at any time). socket()/accept() ask the host for a handle
 * and allocate a newlib descriptor for it with libsysbase's __alloc_handle(dev); the handle number lives in the
 * descriptor's fileStruct (SockObj). From there close/read/write/dup/dup2 are the library's own and reach close_r /
 * read_r / write_r below, which call CLOSE / RECVFROM / SENDTO. fcntl is defined here (newlib's is an ENOSYS stub):
 * on a socket descriptor F_GETFL/F_SETFL act through GETFL/SETFL, F_GETFD/F_SETFD keep FD_CLOEXEC in the SockObj (no
 * exec exists; dup'ed descriptors share the object, hence the flag), F_DUPFD(_CLOEXEC) is dup() until the number is
 * high enough; any other descriptor gets what the guest's fd layer answered before: -1/ENOSYS.
 *
 * Every constant that crosses the boundary is translated by NAME through the X-macro lists of sockwire.h; errno
 * numbers of the wire are Linux's, translated to newlib's. Addresses are BSD style (sa_len, sa_family at offset 1);
 * libnx's sockaddr_in6 names its family at offset 0, so a sockaddr of at least sizeof(sockaddr_in6) bytes whose
 * offset 0 holds AF_INET6 is read as IPv6 too (the rule of to_wire_sa). Written addresses carry both.
 * Contract: every host implements services 16..33 (host_sock.inc); there is no fallback for a host without them. */
#include <errno.h>
#include <fcntl.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <reent.h>
#include <sched.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/iosupport.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#include "fnvnx/hostapi.h"
#include "fnvnx/reach.h"
#include "fnvnx/sockwire.h"

#ifndef FNVNX_SOCK_NAME   /* the test build renames the guest functions so they do not clash with the host libc */
#define FNVNX_SOCK_NAME(n) n
#endif

typedef struct { int h; int cloexec; } SockObj;
static int g_dev = -1;
static const devoptab_t dotab_soc;

/* ---- the call into the host */
extern const FnvnxHostApi *fnvnx_host;
static int64_t sc(uint32_t fn, uint32_t n, uint64_t a0, uint64_t a1, uint64_t a2, uint64_t a3, uint64_t a4) {
    uint64_t args[5] = {a0, a1, a2, a3, a4};
    return (int64_t)fnvnx_hcall4(fnvnx_host->ext_call, FNVNX_EXT_PLATFORM, fn, FNVNX_ADDR(args), n);
}
#define A(p) FNVNX_ADDR(p)

/* ---- errno / constants by name */
static int local_errno(int w) {
#define X(N, v) if (w == (v)) return N;
    FNVNX_WIRE_ERRNO_LIST(X)
#undef X
#define X(N, v) if (w == (v)) return FNVNX_ERRNO_UNKNOWN_OFFSET + (v);   /* no libc name: libnx's own encoding of the number */
    FNVNX_WIRE_ERRNO_UNNAMED(X)
#undef X
    fnvnx_assume_violation(FNVNX_ASM_004, (uint64_t)(uint32_t)w);   /* the host answers only numbers of the two generated tables while ASM-004 holds */
}
/* a negative service result -> errno + -1 */
static int fail(int64_t r) { errno = local_errno((int)-r); return -1; }
static int fail_e(int e) { errno = e; return -1; }

static int af_to_wire(int af, int *w) {
#define X(N, v) if (af == N) { *w = v; return 0; }
    FNVNX_WIRE_AF_LIST(X)
#undef X
    return -1;
}
static int af_from_wire(int w, int *af) {
#define X(N, v) if (w == (v)) { *af = N; return 0; }
    FNVNX_WIRE_AF_LIST(X)
#undef X
    return -1;
}
static int type_to_wire(int t, int *w) {
#define X(N, v) if (t == N) { *w = v; return 0; }
    FNVNX_WIRE_SOCKTYPE_LIST(X)
#undef X
    return -1;
}
static int type_from_wire(int w, int *t) {
#define X(N, v) if (w == (v)) { *t = N; return 0; }
    FNVNX_WIRE_SOCKTYPE_LIST(X)
#undef X
    return -1;
}
/* flag sets: all bits must be known, else -1 */
static int msg_to_wire(int g, int *w) {
    int r = 0;
#define X(N, v) if (g & N) { g &= ~N; r |= (v); }
    FNVNX_WIRE_MSG_LIST(X)
#undef X
    if (g) return -1;
    *w = r;
    return 0;
}
static uint16_t poll_to_wire(int g) {
    uint16_t r = 0;
#define X(N, v) if (g & N) r |= (v);
    FNVNX_WIRE_POLL_LIST(X)
#undef X
    return r;
}
static short poll_from_wire(uint16_t w) {
    int r = 0;
#define X(N, v) if (w & (v)) r |= N;
    FNVNX_WIRE_POLL_LIST(X)
#undef X
    return (short)r;
}

/* ---- addresses */
static inline uint16_t bswap16(uint16_t v) { return (uint16_t)((v >> 8) | (v << 8)); }
/* 0, or an errno of the guest */
static int to_wire_sa(const struct sockaddr *sa, socklen_t len, FnvnxWireSockaddr *w) {
    memset(w, 0, sizeof *w);
    if (!sa || len < 2) return EINVAL;
    int fam = sa->sa_family;
    if (len >= sizeof(struct sockaddr_in6) && ((const uint8_t *)sa)[0] == AF_INET6) fam = AF_INET6;
    if (fam == AF_INET) {
        if (len < sizeof(struct sockaddr_in)) return EINVAL;
        const struct sockaddr_in *a = (const struct sockaddr_in *)sa;
        w->family = FNVNX_WAF_INET;
        w->port = bswap16(a->sin_port);
        memcpy(w->addr, &a->sin_addr, 4);
        return 0;
    }
    if (fam == AF_INET6) {
        if (len < sizeof(struct sockaddr_in6)) return EINVAL;
        const struct sockaddr_in6 *a = (const struct sockaddr_in6 *)sa;
        w->family = FNVNX_WAF_INET6;
        w->port = bswap16(a->sin6_port);
        memcpy(w->addr, &a->sin6_addr, 16);
        w->flowinfo = a->sin6_flowinfo;
        w->scope_id = a->sin6_scope_id;
        return 0;
    }
    if (fam == AF_UNSPEC) return 0;   /* wire family 0 */
    return EAFNOSUPPORT;
}
/* The size the wire address needs as a guest sockaddr (0 for "no address"); writes min(cap, size) bytes. */
static socklen_t from_wire_sa(const FnvnxWireSockaddr *w, struct sockaddr *out, socklen_t cap) {
    union { struct sockaddr_in a4; struct sockaddr_in6 a6; } u;
    socklen_t n = 0;
    memset(&u, 0, sizeof u);
    if (w->family == FNVNX_WAF_INET) {
        u.a4.sin_len = sizeof u.a4;
        u.a4.sin_family = AF_INET;
        u.a4.sin_port = bswap16(w->port);
        memcpy(&u.a4.sin_addr, w->addr, 4);
        n = sizeof u.a4;
    } else if (w->family == FNVNX_WAF_INET6) {
        struct sockaddr *h = (struct sockaddr *)&u.a6;
        u.a6.sin6_port = bswap16(w->port);
        memcpy(&u.a6.sin6_addr, w->addr, 16);
        u.a6.sin6_flowinfo = w->flowinfo;
        u.a6.sin6_scope_id = w->scope_id;
        h->sa_len = sizeof u.a6;      /* offset 0: libnx's sin6_family slot */
        h->sa_family = AF_INET6;      /* offset 1: the BSD place */
        n = sizeof u.a6;
    }
    if (out && n) memcpy(out, &u, cap < n ? cap : n);
    return n;
}

/* ---- descriptors */
/* The registration happens exactly once whatever the interleaving: the thread that moves g_dev_state 0 -> 1 registers
 * and publishes 2 (release); every other caller waits (yielding) for 2 before it reads g_dev. */
static int g_dev_state;
static int sock_dev(void) {
    int st = __atomic_load_n(&g_dev_state, __ATOMIC_ACQUIRE);
    if (st == 0 && __atomic_compare_exchange_n(&g_dev_state, &st, 1, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
        g_dev = AddDevice(&dotab_soc);
        __atomic_store_n(&g_dev_state, 2, __ATOMIC_RELEASE);
        return g_dev;
    }
    while (__atomic_load_n(&g_dev_state, __ATOMIC_ACQUIRE) != 2) sched_yield();
    return g_dev;
}
void fnvnx_guest_sock_init(void) { (void)sock_dev(); }
static int sock_fd(int h) {   /* a descriptor for host handle h (closes h on failure) */
    int dev = sock_dev();
    int fd = dev < 0 ? -1 : __alloc_handle(dev);
    if (fd < 0) {
        sc(FNVNX_PLAT_SOCK_CLOSE, 1, (uint64_t)h, 0, 0, 0, 0);
        return fail_e(EMFILE);
    }
    SockObj *o = __get_handle(fd)->fileStruct;
    o->h = h;
    o->cloexec = 0;
    return fd;
}
static SockObj *sock_of(int fd) {   /* the SockObj of a socket descriptor, or NULL */
    __handle *hd = fd < 0 ? NULL : __get_handle(fd);
    if (!hd || hd->device >= 256 || devoptab_list[hd->device] != &dotab_soc) return NULL;
    return hd->fileStruct;
}
/* with errno EBADF (no descriptor) / ENOTSOCK (another kind of descriptor) */
static SockObj *obj_of(int fd) {
    SockObj *o = sock_of(fd);
    if (!o) errno = (fd >= 0 && __get_handle(fd)) ? ENOTSOCK : EBADF;
    return o;
}
#define OBJ(fd, o) SockObj *o = obj_of(fd); if (!o) return -1

static int soc_close_r(struct _reent *r, void *fo) {
    int64_t v = sc(FNVNX_PLAT_SOCK_CLOSE, 1, (uint64_t)((SockObj *)fo)->h, 0, 0, 0, 0);
    if (v < 0) { r->_errno = local_errno((int)-v); return -1; }
    return 0;
}
static ssize_t soc_write_r(struct _reent *r, void *fo, const char *p, size_t n) {
    int64_t v = sc(FNVNX_PLAT_SOCK_SENDTO, 5, (uint64_t)((SockObj *)fo)->h, A(p), n, 0, 0);
    if (v < 0) { r->_errno = local_errno((int)-v); return -1; }
    return (ssize_t)v;
}
static ssize_t soc_read_r(struct _reent *r, void *fo, char *p, size_t n) {
    int64_t v = sc(FNVNX_PLAT_SOCK_RECVFROM, 5, (uint64_t)((SockObj *)fo)->h, A(p), n, 0, 0);
    if (v < 0) { r->_errno = local_errno((int)-v); return -1; }
    return (ssize_t)v;
}
static const devoptab_t dotab_soc = {
    .name = "soc", .structSize = sizeof(SockObj),
    .close_r = soc_close_r, .write_r = soc_write_r, .read_r = soc_read_r,
};

/* ---- the 12 + the rest of the BSD set */
int FNVNX_SOCK_NAME(socket)(int domain, int type, int protocol) {
    int wd, wt;
    if (af_to_wire(domain, &wd)) return fail_e(EAFNOSUPPORT);
    int extra = type & (SOCK_NONBLOCK | SOCK_CLOEXEC);
    if (type_to_wire(type & ~extra, &wt)) return fail_e(EINVAL);
    int64_t h = sc(FNVNX_PLAT_SOCK_SOCKET, 3, (uint64_t)wd, (uint64_t)wt, (uint64_t)(uint32_t)protocol, 0, 0);
    if (h < 0) return fail(h);
    if (extra & SOCK_NONBLOCK) {
        int64_t v = sc(FNVNX_PLAT_SOCK_SETFL, 2, (uint64_t)h, FNVNX_WO_NONBLOCK, 0, 0, 0);
        if (v < 0) { sc(FNVNX_PLAT_SOCK_CLOSE, 1, (uint64_t)h, 0, 0, 0, 0); return fail(v); }
    }
    int fd = sock_fd((int)h);
    if (fd >= 0 && (extra & SOCK_CLOEXEC)) ((SockObj *)__get_handle(fd)->fileStruct)->cloexec = 1;
    return fd;
}
int FNVNX_SOCK_NAME(bind)(int fd, const struct sockaddr *sa, socklen_t len) {
    OBJ(fd, o);
    FnvnxWireSockaddr w;
    int e = to_wire_sa(sa, len, &w);
    if (e) return fail_e(e);
    int64_t v = sc(FNVNX_PLAT_SOCK_BIND, 2, (uint64_t)o->h, A(&w), 0, 0, 0);
    return v < 0 ? fail(v) : 0;
}
int FNVNX_SOCK_NAME(listen)(int fd, int backlog) {
    OBJ(fd, o);
    int64_t v = sc(FNVNX_PLAT_SOCK_LISTEN, 2, (uint64_t)o->h, (uint64_t)(int64_t)backlog, 0, 0, 0);
    return v < 0 ? fail(v) : 0;
}
int FNVNX_SOCK_NAME(accept)(int fd, struct sockaddr *sa, socklen_t *len) {
    OBJ(fd, o);
    FnvnxWireSockaddr w;
    int want = sa && len;
    int64_t h = sc(FNVNX_PLAT_SOCK_ACCEPT, 2, (uint64_t)o->h, want ? A(&w) : 0, 0, 0, 0);
    if (h < 0) return fail(h);
    if (want) *len = from_wire_sa(&w, sa, *len);
    return sock_fd((int)h);
}
int FNVNX_SOCK_NAME(connect)(int fd, const struct sockaddr *sa, socklen_t len) {
    OBJ(fd, o);
    FnvnxWireSockaddr w;
    int e = to_wire_sa(sa, len, &w);
    if (e) return fail_e(e);
    int64_t v = sc(FNVNX_PLAT_SOCK_CONNECT, 2, (uint64_t)o->h, A(&w), 0, 0, 0);
    return v < 0 ? fail(v) : 0;
}
ssize_t FNVNX_SOCK_NAME(sendto)(int fd, const void *buf, size_t n, int flags, const struct sockaddr *to, socklen_t tolen) {
    OBJ(fd, o);
    int wf;
    if (msg_to_wire(flags, &wf)) return fail_e(EOPNOTSUPP);
    FnvnxWireSockaddr w;
    if (to) {
        int e = to_wire_sa(to, tolen, &w);
        if (e) return fail_e(e);
    }
    int64_t v = sc(FNVNX_PLAT_SOCK_SENDTO, 5, (uint64_t)o->h, A(buf), n, (uint64_t)wf, to ? A(&w) : 0);
    return v < 0 ? fail(v) : (ssize_t)v;
}
ssize_t FNVNX_SOCK_NAME(send)(int fd, const void *buf, size_t n, int flags) {
    return FNVNX_SOCK_NAME(sendto)(fd, buf, n, flags, NULL, 0);
}
ssize_t FNVNX_SOCK_NAME(recvfrom)(int fd, void *buf, size_t n, int flags, struct sockaddr *from, socklen_t *fromlen) {
    OBJ(fd, o);
    int wf;
    if (msg_to_wire(flags, &wf)) return fail_e(EOPNOTSUPP);
    FnvnxWireSockaddr w;
    int want = from && fromlen;
    int64_t v = sc(FNVNX_PLAT_SOCK_RECVFROM, 5, (uint64_t)o->h, A(buf), n, (uint64_t)wf, want ? A(&w) : 0);
    if (v < 0) return fail(v);
    if (want) *fromlen = from_wire_sa(&w, from, *fromlen);
    return (ssize_t)v;
}
ssize_t FNVNX_SOCK_NAME(recv)(int fd, void *buf, size_t n, int flags) {
    return FNVNX_SOCK_NAME(recvfrom)(fd, buf, n, flags, NULL, NULL);
}
/* ---- options */
static int opt_lookup(int level, int opt, int *wl, int *wo, int *shape) {
#define X(N, v, s) if (opt == N) { *wo = v; *shape = s; return 0; }
    if (level == SOL_SOCKET) {
        *wl = FNVNX_WLVL_SOCKET;
        FNVNX_WIRE_SO_OPT_LIST(X)
        return -1;
    }
    *wl = level;   /* IANA numbers */
    if (level == IPPROTO_TCP) {
        FNVNX_WIRE_TCP_OPT_LIST(X)
    } else if (level == IPPROTO_IP) {
        FNVNX_WIRE_IP_OPT_LIST(X)
    } else if (level == IPPROTO_IPV6) {
        FNVNX_WIRE_IPV6_OPT_LIST(X)
    }
#undef X
    return -1;
}
/* BSD's IP_MULTICAST_TTL / _LOOP take a u_char (or an int) */
static int is_uchar_opt(int level, int opt) { return level == IPPROTO_IP && (opt == IP_MULTICAST_TTL || opt == IP_MULTICAST_LOOP); }

int FNVNX_SOCK_NAME(setsockopt)(int fd, int level, int opt, const void *val, socklen_t len) {
    OBJ(fd, o);
    int wl, wo, shape;
    if (opt_lookup(level, opt, &wl, &wo, &shape)) return fail_e(ENOPROTOOPT);
    union { int32_t i; FnvnxWireLinger l; FnvnxWireTimeval t; } w;
    uint32_t wlen;
    if (shape == FNVNX_WOPT_TYPE || shape == FNVNX_WOPT_ERRNO) return fail_e(ENOPROTOOPT);   /* read only */
    if (!val) return fail_e(EFAULT);
    if (shape == FNVNX_WOPT_INT) {
        if (is_uchar_opt(level, opt) && len == 1) w.i = *(const uint8_t *)val;
        else if (len >= sizeof(int)) { int v; memcpy(&v, val, sizeof v); w.i = v; }
        else return fail_e(EINVAL);
        wlen = 4;
    } else if (shape == FNVNX_WOPT_LINGER) {
        if (len < sizeof(struct linger)) return fail_e(EINVAL);
        struct linger l;
        memcpy(&l, val, sizeof l);
        w.l.onoff = l.l_onoff;
        w.l.linger_s = l.l_linger;
        wlen = sizeof w.l;
    } else {
        if (len < sizeof(struct timeval)) return fail_e(EINVAL);
        struct timeval t;
        memcpy(&t, val, sizeof t);
        w.t.sec = t.tv_sec;
        w.t.usec = t.tv_usec;
        wlen = sizeof w.t;
    }
    int64_t v = sc(FNVNX_PLAT_SOCK_SETSOCKOPT, 5, (uint64_t)o->h, (uint64_t)wl, (uint64_t)wo, A(&w), wlen);
    return v < 0 ? fail(v) : 0;
}
int FNVNX_SOCK_NAME(getsockopt)(int fd, int level, int opt, void *val, socklen_t *len) {
    OBJ(fd, o);
    int wl, wo, shape;
    if (opt_lookup(level, opt, &wl, &wo, &shape)) return fail_e(ENOPROTOOPT);
    if (!val || !len) return fail_e(EFAULT);
    union { int32_t i; FnvnxWireLinger l; FnvnxWireTimeval t; } w;
    uint32_t wlen = sizeof w;
    int64_t v = sc(FNVNX_PLAT_SOCK_GETSOCKOPT, 5, (uint64_t)o->h, (uint64_t)wl, (uint64_t)wo, A(&w), A(&wlen));
    if (v < 0) return fail(v);
    union { int i; struct linger l; struct timeval t; uint8_t c; } g;
    socklen_t gl;
    if (shape == FNVNX_WOPT_LINGER) {
        g.l.l_onoff = w.l.onoff;
        g.l.l_linger = w.l.linger_s;
        gl = sizeof g.l;
    } else if (shape == FNVNX_WOPT_TIMEVAL) {
        g.t.tv_sec = (time_t)w.t.sec;
        g.t.tv_usec = (suseconds_t)w.t.usec;
        gl = sizeof g.t;
    } else {
        int x = w.i;
        if (shape == FNVNX_WOPT_TYPE) { int t; if (!type_from_wire(x, &t)) x = t; }
        else if (shape == FNVNX_WOPT_ERRNO) x = x ? local_errno(x) : 0;
        if (is_uchar_opt(level, opt) && *len == 1) { g.c = (uint8_t)x; gl = 1; }
        else { g.i = x; gl = sizeof g.i; }
    }
    memcpy(val, &g, *len < gl ? *len : gl);
    *len = gl;
    return 0;
}

int FNVNX_SOCK_NAME(poll)(struct pollfd *fds, nfds_t nfds, int timeout) {
    FnvnxWirePollfd stack[32], *w = stack;
    if (nfds && !fds) return fail_e(EFAULT);
    if (nfds > 32 && !(w = malloc(nfds * sizeof *w))) return fail_e(ENOMEM);
    for (nfds_t i = 0; i < nfds; i++) {
        int fd = fds[i].fd;
        SockObj *o = sock_of(fd);
        w[i].fd = fd < 0 ? -1 : o ? o->h : INT32_MAX;   /* INT32_MAX: not an open socket handle -> POLLNVAL */
        w[i].events = poll_to_wire(fds[i].events);
        w[i].revents = 0;
    }
    int64_t v = sc(FNVNX_PLAT_SOCK_POLL, 3, A(w), nfds, (uint64_t)(uint32_t)timeout, 0, 0);
    int n = 0;
    if (v >= 0)
        for (nfds_t i = 0; i < nfds; i++) {
            fds[i].revents = poll_from_wire(w[i].revents);
            if (fds[i].revents) n++;
        }
    if (w != stack) free(w);
    return v < 0 ? fail(v) : n;
}

int FNVNX_SOCK_NAME(fcntl)(int fd, int cmd, ...) {
    SockObj *o = sock_of(fd);
    if (!o) return fail_e(ENOSYS);   /* not a socket: what the guest's fd layer has always answered */
    va_list ap;
    va_start(ap, cmd);
    int arg = va_arg(ap, int);
    va_end(ap);
    switch (cmd) {
    case F_GETFL: {
        int64_t v = sc(FNVNX_PLAT_SOCK_GETFL, 1, (uint64_t)o->h, 0, 0, 0, 0);
        if (v < 0) return fail(v);
        return O_RDWR | ((v & FNVNX_WO_NONBLOCK) ? O_NONBLOCK : 0);
    }
    case F_SETFL: {
        int64_t v = sc(FNVNX_PLAT_SOCK_SETFL, 2, (uint64_t)o->h, (arg & O_NONBLOCK) ? FNVNX_WO_NONBLOCK : 0, 0, 0, 0);
        return v < 0 ? fail(v) : 0;
    }
    case F_GETFD: return o->cloexec ? FD_CLOEXEC : 0;
    case F_SETFD: o->cloexec = (arg & FD_CLOEXEC) != 0; return 0;
    case F_DUPFD:
    case F_DUPFD_CLOEXEC: {
        if (arg < 0) return fail_e(EINVAL);
        int tmp[64], nt = 0, nd = -1;
        while (nd < arg) {   /* dup() hands out the lowest free number; keep the ones below arg until released */
            nd = dup(fd);
            if (nd < 0) break;
            if (nd < arg) {
                if (nt == 64) { close(nd); errno = EMFILE; nd = -1; break; }
                tmp[nt++] = nd;
            }
        }
        int save = errno;
        while (nt) close(tmp[--nt]);
        errno = save;
        if (nd >= 0 && cmd == F_DUPFD_CLOEXEC) o->cloexec = 1;
        return nd;
    }
    default: return fail_e(EINVAL);
    }
}
