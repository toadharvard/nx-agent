/* winsock.cpp - Winsock 1.1 (wsock32 / ws2_32) over the platform's BSD sockets (libnx and the host tests both
 * provide <sys/socket.h>). SOCKET values are private table indexes; host fds never leave this file. The
 * functions are named fnvnx_ws2_* so they cannot collide with the BSD API of the target libc (docs/RUNTIME.md).
 * Network failures are whatever the BSD call reports (errno), translated by map_errno(); an errno without a
 * Winsock equal maps to WSAEINVAL (10022), the one "the request is not valid in this state" code. */
#include "w32.h"

#include <type_traits>

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <strings.h>
#include <stdint.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>

extern "C" BOOL GetComputerNameA(LPSTR, LPDWORD);

namespace {
enum { MAXS = 256, FD_RD = 1, FD_WR = 2, FD_OOB_ = 4, FD_ACC = 8, FD_CONN = 0x10, FD_CLS = 0x20 };
enum { F_NB = 1, F_LISTEN = 2, F_CONNECTING = 4, F_CONNECTED = 8, F_RD_POSTED = 16, F_ACC_POSTED = 32,
       F_CLS_POSTED = 64, F_OOB_POSTED = 128, F_WR_BLOCKED = 256, F_WR_NOW = 512, F_DGRAM = 1024 };
struct Sock { int fd; unsigned f; HWND hwnd; UINT msg; long mask; };
struct Ev { HWND h; UINT m; UINT_PTR s; LPARAM l; };
struct ws_set { uint32_t count; UINT_PTR arr[64]; };
struct ws_data { uint16_t ver, high; char desc[257], status[129]; uint16_t maxsock, maxudp; char *vendor; };   /* WSADATA */
struct ws_hostent { char *name; char **aliases; int16_t type, len; char **addrs; };

os_lock_t g_lk;
Sock g_s[MAXS];           /* fd == 0 and f == 0: free (fd 0 is never a socket here: slots are marked by f bit 31) */
const unsigned F_USED = 0x80000000u;
int g_init;
os_thread *g_pump;
bool g_pump_run;

const struct { int e, w; } kErr[] = {
    { EWOULDBLOCK, 10035 }, { EAGAIN, 10035 }, { EINPROGRESS, 10036 }, { EALREADY, 10037 }, { ENOTSOCK, 10038 },
    { EBADF, 10038 }, { EDESTADDRREQ, 10039 }, { EMSGSIZE, 10040 }, { EPROTOTYPE, 10041 }, { ENOPROTOOPT, 10042 },
    { EPROTONOSUPPORT, 10043 }, { ESOCKTNOSUPPORT, 10044 }, { EOPNOTSUPP, 10045 }, { EPFNOSUPPORT, 10046 },
    { EAFNOSUPPORT, 10047 }, { EADDRINUSE, 10048 }, { EADDRNOTAVAIL, 10049 }, { ENETDOWN, 10050 },
    { ENETUNREACH, 10051 }, { ENETRESET, 10052 }, { ECONNABORTED, 10053 }, { ECONNRESET, 10054 },
    { ENOBUFS, 10055 }, { ENOMEM, 10055 }, { EISCONN, 10056 }, { ENOTCONN, 10057 }, { ESHUTDOWN, 10058 },
    { EPIPE, 10058 }, { ETIMEDOUT, 10060 }, { ECONNREFUSED, 10061 }, { EHOSTDOWN, 10064 }, { EHOSTUNREACH, 10065 },
    { EINTR, 10004 }, { EACCES, 10013 }, { EPERM, 10013 }, { EFAULT, 10014 }, { EINVAL, 10022 }, { EMFILE, 10024 },
    { ENFILE, 10024 },
};
int map_errno(int e)
{
    for (const auto &r : kErr)
        if (r.e == e)
            return r.w;
    return 10022;
}
int fail(int w) { SetLastError((DWORD)w); return -1; }
int fail_errno() { return fail(map_errno(errno)); }

Sock *slot(UINT_PTR s) { return (s >= 1 && s <= MAXS && (g_s[s - 1].f & F_USED)) ? &g_s[s - 1] : NULL; }
template <class F> bool with(UINT_PTR s, F fn)
{
    os_lock(&g_lk);
    Sock *k = slot(s);
    if (k)
        fn(*k);
    os_unlock(&g_lk);
    return k != NULL;
}
/* copy of the table entry of s; sets the Winsock error when there is none */
bool lookup(UINT_PTR s, Sock *out)
{
    os_lock(&g_lk);
    Sock *k = g_init ? slot(s) : NULL;
    int init = g_init;
    if (k)
        *out = *k;
    os_unlock(&g_lk);
    if (!k)
        SetLastError(init ? 10038 : 10093);
    return k != NULL;
}
bool started() { os_lock(&g_lk); int i = g_init; os_unlock(&g_lk); if (!i) SetLastError(10093); return i != 0; }
UINT_PTR add(int fd, unsigned f, const Sock *inherit)
{
    UINT_PTR r = 0;
    os_lock(&g_lk);
    for (int i = 0; i < MAXS && !r; i++)
        if (!(g_s[i].f & F_USED)) {
            g_s[i] = Sock{ fd, f | F_USED, NULL, 0, 0 };
            if (inherit) { g_s[i].hwnd = inherit->hwnd; g_s[i].msg = inherit->msg; g_s[i].mask = inherit->mask; }
            r = (UINT_PTR)(i + 1);
        }
    os_unlock(&g_lk);
    return r;
}
void set_nb(int fd, bool nb)
{
    int fl = fcntl(fd, F_GETFL, 0);
    if (fl >= 0)
        fcntl(fd, F_SETFL, nb ? (fl | O_NONBLOCK) : (fl & ~O_NONBLOCK));
}

/* Windows sockaddr_in: u16 family (LE), u16 port (network order), 4 address bytes, 8 zero bytes. */
template <class T, class = void> struct has_sin_len : std::false_type {};
template <class T> struct has_sin_len<T, std::void_t<decltype(T::sin_len)>> : std::true_type {};
template <class T> void setlen(T &a)   /* BSD-derived sockaddrs carry their length */
{
    if constexpr (has_sin_len<T>::value)
        a.sin_len = sizeof a;
}
bool to_host(const void *p, int len, sockaddr_in *o)
{
    const uint8_t *b = (const uint8_t *)p;
    if (!p || len < 16) { SetLastError(10014); return false; }
    if ((b[0] | b[1] << 8) != 2) { SetLastError(10047); return false; }
    memset(o, 0, sizeof *o);
    setlen(*o);
    o->sin_family = AF_INET;
    memcpy(&o->sin_port, b + 2, 2);
    memcpy(&o->sin_addr, b + 4, 4);
    return true;
}
void from_host(const sockaddr_in &a, void *p)
{
    uint8_t *b = (uint8_t *)p;
    memset(b, 0, 16);
    b[0] = 2;
    memcpy(b + 2, &a.sin_port, 2);
    memcpy(b + 4, &a.sin_addr, 4);
}
int xlat_flags(int f, int *o)
{
    if (f & ~7) return fail(10022);
    *o = ((f & 1) ? MSG_OOB : 0) | ((f & 2) ? MSG_PEEK : 0) | ((f & 4) ? MSG_DONTROUTE : 0);
#ifdef MSG_NOSIGNAL
    *o |= MSG_NOSIGNAL;
#endif
    return 0;
}

/* ---- WSAAsyncSelect: one poll thread posts the FD_* messages */
Ev g_ev[3 * MAXS + 8];
int g_nev;
pollfd g_pf[MAXS];
UINT_PTR g_ph[MAXS];
void push(Sock &k, UINT_PTR h, int ev, int err)
{
    if ((k.mask & ev) && g_nev < (int)(sizeof g_ev / sizeof g_ev[0]))
        g_ev[g_nev++] = Ev{ k.hwnd, k.msg, h, (LPARAM)(((unsigned)err << 16) | (unsigned)ev) };
}
int so_error(int fd)
{
    int e = 0;
    socklen_t n = sizeof e;
    if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &e, &n) < 0)
        e = errno;
    return e ? map_errno(e) : 0;
}
void pump(void *)
{
    for (;;) {
        int n = 0;
        os_lock(&g_lk);
        for (int i = 0; i < MAXS; i++) {
            Sock &k = g_s[i];
            if (!(k.f & F_USED) || !k.mask) continue;
            short ev = 0;
            if (k.f & F_LISTEN) { if (!(k.f & F_ACC_POSTED)) ev |= POLLIN; }
            else if (k.f & F_CONNECTING) ev |= POLLOUT;
            else {
                if (!(k.f & (F_RD_POSTED | F_CLS_POSTED))) ev |= POLLIN;
                if (k.f & F_WR_BLOCKED) ev |= POLLOUT;
                if ((k.mask & FD_OOB_) && !(k.f & F_OOB_POSTED)) ev |= POLLPRI;
            }
            g_pf[n] = pollfd{ k.fd, ev, 0 };
            g_ph[n++] = (UINT_PTR)(i + 1);
        }
        os_unlock(&g_lk);
        if (n) poll(g_pf, (nfds_t)n, 10); else os_sleep_ns(10000000ull);
        os_lock(&g_lk);
        g_nev = 0;
        for (int i = 0; i < n; i++) {
            Sock *k = slot(g_ph[i]);
            short r = g_pf[i].revents;
            if (!k || k->fd != g_pf[i].fd || !(k->mask)) continue;
            if (k->f & F_LISTEN) {
                if (r & POLLIN) { push(*k, g_ph[i], FD_ACC, 0); k->f |= F_ACC_POSTED; }
            } else if (k->f & F_CONNECTING) {
                if (r & (POLLOUT | POLLERR | POLLHUP)) {
                    int e = so_error(k->fd);
                    k->f &= ~F_CONNECTING;
                    if (!e) k->f |= F_CONNECTED;
                    push(*k, g_ph[i], FD_CONN, e);
                    if (!e) push(*k, g_ph[i], FD_WR, 0);
                }
            } else {
                if ((r & (POLLIN | POLLHUP | POLLERR)) && !(k->f & (F_RD_POSTED | F_CLS_POSTED))) {
                    char c;
                    ssize_t got = recv(k->fd, &c, 1, MSG_PEEK | MSG_DONTWAIT);
                    int e = errno;
                    if (got > 0 || (got == 0 && (k->f & F_DGRAM))) { push(*k, g_ph[i], FD_RD, 0); k->f |= F_RD_POSTED; }
                    else if (got == 0 || (e != EAGAIN && e != EINTR)) {
                        push(*k, g_ph[i], FD_CLS, got == 0 ? 0 : map_errno(e));
                        k->f |= F_CLS_POSTED;
                    }
                }
                if ((r & POLLOUT) && (k->f & F_WR_BLOCKED)) { k->f &= ~F_WR_BLOCKED; push(*k, g_ph[i], FD_WR, 0); }
                if (r & POLLPRI) { push(*k, g_ph[i], FD_OOB_, 0); k->f |= F_OOB_POSTED; }
            }
        }
        for (int i = 0; i < MAXS; i++)
            if ((g_s[i].f & F_USED) && (g_s[i].f & F_WR_NOW)) { g_s[i].f &= ~F_WR_NOW; push(g_s[i], (UINT_PTR)(i + 1), FD_WR, 0); }
        Ev local[sizeof g_ev / sizeof g_ev[0]];
        int ln = g_nev;
        memcpy(local, g_ev, sizeof(Ev) * (size_t)ln);
        os_unlock(&g_lk);
        for (int i = 0; i < ln; i++)
            PostMessageA(local[i].h, local[i].m, (WPARAM)local[i].s, local[i].l);
    }
}
bool start_pump()
{
    os_lock(&g_lk);
    if (!g_pump_run) {
        g_pump = os_thread_create(pump, NULL, 128 * 1024, OS_PRIO_NORMAL, -1);
        if (g_pump && os_thread_start(g_pump) == 0) g_pump_run = true;
        else if (g_pump) { os_thread_free(g_pump); g_pump = NULL; }
    }
    bool ok = g_pump_run;
    os_unlock(&g_lk);
    return ok;
}

/* inet_addr's classic grammar: 1-4 parts, each decimal, 0octal or 0xhex; the last part fills the remaining bytes */
bool parse_ip(const char *s, uint32_t *out)
{
    uint32_t part[4];
    int n = 0;
    for (;;) {
        unsigned base = 10;
        if (*s == '0') { base = 8; s++; if ((*s | 32) == 'x') { base = 16; s++; } else if (!(*s >= '0' && *s <= '9')) { base = 8; s--; } }
        uint64_t v = 0;
        int digits = 0;
        for (;; s++, digits++) {
            int d = (*s >= '0' && *s <= '9') ? *s - '0' : ((*s | 32) >= 'a' && (*s | 32) <= 'f') ? (*s | 32) - 'a' + 10 : 99;
            if (d >= (int)base) break;
            v = v * base + (unsigned)d;
            if (v > 0xFFFFFFFFull) return false;
        }
        if (!digits) return false;
        if (n == 4) return false;
        part[n++] = (uint32_t)v;
        if (*s == '.') { s++; continue; }
        break;
    }
    if (*s && *s != ' ' && *s != '\t' && *s != '\n' && *s != '\r') return false;
    uint32_t v = part[n - 1];
    for (int i = 0; i < n - 1; i++) if (part[i] > 255) return false;
    if (v > (n == 4 ? 255u : 0xFFFFFFFFu >> (8 * (n - 1)))) return false;
    for (int i = 0; i < n - 1; i++) v |= part[i] << (24 - 8 * i);
    *out = v;
    return true;
}
uint32_t bswap(uint32_t v) { return (v >> 24) | ((v >> 8) & 0xFF00) | ((v << 8) & 0xFF0000) | (v << 24); }
}  // namespace

extern "C" int fnvnx_ws2_WSAStartup(WORD ver, void *data)
{
    unsigned v = ver & 0xFFFF;
    if ((v & 0xFF) < 1) return 10092;
    if (!data) return 10014;
    ws_data *d = (ws_data *)data;
    uint16_t neg = (v & 0xFF) > 1 || (v >> 8) > 1 ? 0x0101 : (uint16_t)v;
    memset(d, 0, sizeof *d);
    d->ver = neg; d->high = 0x0101; d->maxsock = 32767; d->maxudp = 65467;
    strcpy(d->desc, "WinSock 1.1");
    strcpy(d->status, "Running");
    os_lock(&g_lk);
    g_init++;
    os_unlock(&g_lk);
    return 0;
}
extern "C" int fnvnx_ws2_WSACleanup(void)
{
    os_lock(&g_lk);
    int ok = g_init > 0;
    if (ok && --g_init == 0) {
        for (Sock &k : g_s)
            if (k.f & F_USED) { close(k.fd); k = Sock{ 0, 0, NULL, 0, 0 }; }
    }
    os_unlock(&g_lk);
    return ok ? 0 : fail(10093);
}
extern "C" int fnvnx_ws2_WSAGetLastError(void) { return (int)GetLastError(); }
extern "C" void fnvnx_ws2_WSASetLastError(int e) { SetLastError((DWORD)e); }

extern "C" UINT_PTR fnvnx_ws2_socket(int af, int type, int proto)
{
    if (!started()) return ~(UINT_PTR)0;
    int ht, hp;
    if (af != 2) { SetLastError(10047); return ~(UINT_PTR)0; }
    if (type == 1) ht = SOCK_STREAM; else if (type == 2) ht = SOCK_DGRAM; else { SetLastError(10044); return ~(UINT_PTR)0; }
    if (proto == 0) hp = 0; else if (proto == 6) hp = IPPROTO_TCP; else if (proto == 17) hp = IPPROTO_UDP;
    else { SetLastError(10043); return ~(UINT_PTR)0; }
    int fd = ::socket(AF_INET, ht, hp);
    if (fd < 0) { fail_errno(); return ~(UINT_PTR)0; }
    UINT_PTR s = add(fd, type == 2 ? F_DGRAM : 0, NULL);
    if (!s) { close(fd); SetLastError(10024); return ~(UINT_PTR)0; }
    return s;
}
extern "C" int fnvnx_ws2_closesocket(UINT_PTR s)
{
    Sock k;
    if (!lookup(s, &k)) return -1;
    with(s, [](Sock &x) { close(x.fd); x = Sock{ 0, 0, NULL, 0, 0 }; });   /* the selection dies with the slot; SO_LINGER is the host's */
    return 0;
}
extern "C" int fnvnx_ws2_bind(UINT_PTR s, const void *a, int len)
{
    Sock k; sockaddr_in h;
    if (!lookup(s, &k)) return -1;
    if (!to_host(a, len, &h)) return -1;
    return ::bind(k.fd, (sockaddr *)&h, sizeof h) < 0 ? fail_errno() : 0;
}
extern "C" int fnvnx_ws2_listen(UINT_PTR s, int backlog)
{
    Sock k;
    if (!lookup(s, &k)) return -1;
    if (::listen(k.fd, backlog) < 0) return fail_errno();
    with(s, [](Sock &x) { x.f |= F_LISTEN; });
    return 0;
}
extern "C" UINT_PTR fnvnx_ws2_accept(UINT_PTR s, void *addr, int *len)
{
    Sock k;
    if (!lookup(s, &k)) return ~(UINT_PTR)0;
    if (addr && len && *len < 16) { SetLastError(10014); return ~(UINT_PTR)0; }
    sockaddr_in h; socklen_t hl = sizeof h;
    memset(&h, 0, sizeof h);
    int fd = ::accept(k.fd, (sockaddr *)&h, &hl);
    int e = errno;
    with(s, [](Sock &x) { x.f &= ~F_ACC_POSTED; });
    if (fd < 0) { SetLastError((DWORD)map_errno(e)); return ~(UINT_PTR)0; }
    UINT_PTR n = add(fd, F_CONNECTED | (k.mask ? F_NB : 0) | (k.mask & FD_WR ? F_WR_NOW : 0), &k);   /* inherits the selection */
    if (!n) { close(fd); SetLastError(10024); return ~(UINT_PTR)0; }
    set_nb(fd, k.mask != 0);
    if (addr && len) { from_host(h, addr); *len = 16; }
    return n;
}
extern "C" int fnvnx_ws2_connect(UINT_PTR s, const void *a, int len)
{
    Sock k; sockaddr_in h;
    if (!lookup(s, &k)) return -1;
    if (!to_host(a, len, &h)) return -1;
    if (::connect(k.fd, (sockaddr *)&h, sizeof h) == 0) { with(s, [](Sock &x) { x.f |= F_CONNECTED; x.f &= ~F_CONNECTING; }); return 0; }
    int e = errno;
    if (e == EINPROGRESS) { with(s, [](Sock &x) { x.f |= F_CONNECTING; }); return fail(10035); }   /* Winsock: WSAEWOULDBLOCK */
    return fail(map_errno(e));
}
extern "C" int fnvnx_ws2_send(UINT_PTR s, const char *b, int n, int flags)
{
    Sock k; int hf;
    if (!lookup(s, &k)) return -1;
    if (xlat_flags(flags, &hf) < 0) return -1;
    if (!b && n) return fail(10014);
    ssize_t r = ::send(k.fd, b, (size_t)n, hf);
    if (r >= 0) return (int)r;
    int e = errno;
    if (e == EAGAIN || e == EWOULDBLOCK) with(s, [](Sock &x) { x.f |= F_WR_BLOCKED; });
    return fail(map_errno(e));
}
extern "C" int fnvnx_ws2_recv(UINT_PTR s, char *b, int n, int flags)
{
    Sock k; int hf;
    if (!lookup(s, &k)) return -1;
    if (xlat_flags(flags, &hf) < 0) return -1;
    if (!b && n) return fail(10014);
    ssize_t r = ::recv(k.fd, b, (size_t)n, hf);
    int e = errno;
    if (r >= 0 && !(flags & 2)) with(s, [flags](Sock &x) { x.f &= ~F_RD_POSTED; if (flags & 1) x.f &= ~F_OOB_POSTED; });   /* recv re-arms FD_READ */
    return r < 0 ? fail(map_errno(e)) : (int)r;
}
extern "C" int fnvnx_ws2_setsockopt(UINT_PTR s, int level, int opt, const char *val, int len)
{
    Sock k;
    if (!lookup(s, &k)) return -1;
    if (!val) return fail(10014);
    int hl, ho, v = 0;
    linger lg;
    const void *hv = &v;
    socklen_t hn = sizeof v;
    if (level == (int)0xFFFF && opt == 0x80) {                     /* SO_LINGER: struct of two u_short */
        if (len < 4) return fail(10014);
        uint16_t w[2]; memcpy(w, val, 4);
        lg.l_onoff = w[0]; lg.l_linger = w[1];
        hl = SOL_SOCKET; ho = SO_LINGER; hv = &lg; hn = sizeof lg;
    } else {
        if (len < 4) return fail(10014);
        memcpy(&v, val, 4);
        if (level == (int)0xFFFF) {
            hl = SOL_SOCKET;
            switch (opt) {
            case 4: ho = SO_REUSEADDR; break;
            case 8: ho = SO_KEEPALIVE; break;
            case 0x20: ho = SO_BROADCAST; break;
            case 0x1001: ho = SO_SNDBUF; break;
            case 0x1002: ho = SO_RCVBUF; break;
            case ~0x80: ho = SO_LINGER; lg.l_onoff = v ? 0 : 1; lg.l_linger = 0; hv = &lg; hn = sizeof lg; break;   /* SO_DONTLINGER */
            default: return fail(10042);
            }
        } else if (level == 6 && opt == 1) { hl = IPPROTO_TCP; ho = TCP_NODELAY; }
        else return fail(10042);
    }
    return setsockopt(k.fd, hl, ho, hv, hn) < 0 ? fail_errno() : 0;
}
extern "C" int fnvnx_ws2_ioctlsocket(UINT_PTR s, long cmd, unsigned long *arg)
{
    Sock k;
    if (!lookup(s, &k)) return -1;
    if (!arg) return fail(10014);
    switch ((uint32_t)cmd) {
    case 0x8004667E: {                                             /* FIONBIO */
        if (!*arg && k.mask) return fail(10022);                   /* an async selection requires non-blocking mode */
        set_nb(k.fd, *arg != 0);
        with(s, [arg](Sock &x) { if (*arg) x.f |= F_NB; else x.f &= ~F_NB; });
        return 0;
    }
    case 0x4004667F: {                                             /* FIONREAD: bytes (stream) or the next datagram's size */
        size_t cap = 65536;
        for (;;) {
            char *buf = (char *)malloc(cap);
            if (!buf) return fail(10055);
            ssize_t r = recv(k.fd, buf, cap, MSG_PEEK | MSG_DONTWAIT);
            int e = errno;
            free(buf);
            if (r < 0 && e != EAGAIN && e != EWOULDBLOCK) return fail(map_errno(e));
            if (r >= 0 && (size_t)r == cap && cap < (1u << 26)) { cap *= 2; continue; }
            *arg = r < 0 ? 0 : (unsigned long)r;
            return 0;
        }
    }
    case 0x40047307: {                                             /* SIOCATMARK: TRUE when no out-of-band data is pending */
        pollfd p = { k.fd, POLLPRI, 0 };
        poll(&p, 1, 0);
        *arg = (p.revents & POLLPRI) ? 0 : 1;
        return 0;
    }
    }
    return fail(10022);
}
extern "C" int fnvnx_ws2_select(int, void *r, void *w, void *x, const void *tvp)
{
    if (!started()) return -1;
    ws_set *sets[3] = { (ws_set *)r, (ws_set *)w, (ws_set *)x };
    pollfd pf[192]; int owner[192], idx[192], cerr[192], n = 0;
    bool conn[192];
    for (int t = 0; t < 3; t++) {
        if (!sets[t]) continue;
        for (uint32_t i = 0; i < sets[t]->count && i < 64; i++) {
            Sock k;
            if (!lookup(sets[t]->arr[i], &k)) return -1;
            short ev = t == 0 ? POLLIN : t == 1 ? POLLOUT : POLLPRI;
            conn[n] = (k.f & F_CONNECTING) != 0;
            if (conn[n]) ev |= POLLOUT;
            pf[n] = pollfd{ k.fd, ev, 0 }; owner[n] = t; idx[n] = (int)i; n++;
        }
    }
    if (!n) return fail(10022);
    int ms = -1;
    if (tvp) {
        const int32_t *tv = (const int32_t *)tvp;
        int64_t m = (int64_t)tv[0] * 1000 + (tv[1] + 999) / 1000;
        ms = m > 0x7FFFFFFF ? 0x7FFFFFFF : m < 0 ? 0 : (int)m;
    }
    int pr = poll(pf, (nfds_t)n, ms);
    if (pr < 0) return fail_errno();
    UINT_PTR keep[3][64]; uint32_t kc[3] = { 0, 0, 0 };
    for (int i = 0; i < n; i++) {
        cerr[i] = 0;
        if (conn[i] && (pf[i].revents & (POLLOUT | POLLERR | POLLHUP))) {
            int j = 0;
            while (j < i && pf[j].fd != pf[i].fd) j++;
            cerr[i] = j < i ? cerr[j] : so_error(pf[i].fd);       /* SO_ERROR is read once per fd */
        }
        short rv = pf[i].revents;
        bool ok = owner[i] == 0 ? (rv & (POLLIN | POLLHUP | POLLERR)) != 0
                : owner[i] == 1 ? (rv & POLLOUT) && !cerr[i]
                : (rv & POLLPRI) || cerr[i];
        if (ok) keep[owner[i]][kc[owner[i]]++] = sets[owner[i]]->arr[idx[i]];
        if (conn[i] && (rv & (POLLOUT | POLLERR | POLLHUP))) with(sets[owner[i]]->arr[idx[i]], [](Sock &k) { k.f &= ~F_CONNECTING; });
    }
    int total = 0;
    for (int t = 0; t < 3; t++)
        if (sets[t]) { memcpy(sets[t]->arr, keep[t], kc[t] * sizeof(UINT_PTR)); sets[t]->count = kc[t]; total += (int)kc[t]; }
    return total;
}
extern "C" int fnvnx_ws2_fdisset(UINT_PTR s, void *set)
{
    ws_set *f = (ws_set *)set;
    for (uint32_t i = 0; f && i < f->count && i < 64; i++)
        if (f->arr[i] == s) return 1;
    return 0;
}
extern "C" int fnvnx_ws2_WSAAsyncSelect(UINT_PTR s, HWND h, UINT msg, long ev)
{
    Sock k;
    if (!lookup(s, &k)) return -1;
    if (ev && !h) return fail(10022);
    if (ev && !start_pump()) return fail(10055);
    if (ev) set_nb(k.fd, true);
    with(s, [=](Sock &x) {
        x.hwnd = ev ? h : NULL; x.msg = msg; x.mask = ev & 0x3F;
        x.f &= ~(F_RD_POSTED | F_ACC_POSTED | F_CLS_POSTED | F_OOB_POSTED | F_WR_BLOCKED);
        if (ev) x.f |= F_NB;
        if (ev && (x.f & F_CONNECTED) && (ev & FD_WR)) x.f |= F_WR_NOW;
    });
    return 0;
}

extern "C" unsigned short fnvnx_ws2_htons(unsigned short v) { return (unsigned short)((v >> 8) | (v << 8)); }
extern "C" unsigned short fnvnx_ws2_ntohs(unsigned short v) { return (unsigned short)((v >> 8) | (v << 8)); }
extern "C" unsigned long fnvnx_ws2_inet_addr(const char *s)
{
    uint32_t v;
    return s && parse_ip(s, &v) ? (unsigned long)bswap(v) : 0xFFFFFFFFul;
}
extern "C" char *fnvnx_ws2_inet_ntoa(unsigned long a)
{
    static __thread char buf[16];
    snprintf(buf, sizeof buf, "%u.%u.%u.%u", (unsigned)(a & 255), (unsigned)((a >> 8) & 255), (unsigned)((a >> 16) & 255), (unsigned)((a >> 24) & 255));
    return buf;
}
extern "C" int fnvnx_ws2_gethostname(char *b, int n)
{
    if (!started()) return -1;
    DWORD len = (DWORD)(n > 0 ? n : 0);
    if (!b || !GetComputerNameA(b, &len)) return fail(10014);   /* WSAEFAULT: the name does not fit */
    return 0;
}
extern "C" void *fnvnx_ws2_gethostbyname(const char *name)
{
    static __thread struct { ws_hostent h; char name[256]; char *alias[1]; char *list[9]; uint8_t ad[8][4]; } t;
    if (!started()) return NULL;
    if (!name) { SetLastError(10014); return NULL; }
    int na = 0;
    uint32_t ip;
    char self[64] = "";
    DWORD sl = sizeof self;
    GetComputerNameA(self, &sl);
    if (parse_ip(name, &ip)) { uint32_t b = bswap(ip); memcpy(t.ad[na++], &b, 4); }   /* memory order a.b.c.d = network order */
    else if (!strcasecmp(name, "localhost") || (self[0] && !strcasecmp(name, self))) { static const uint8_t lo[4] = { 127, 0, 0, 1 }; memcpy(t.ad[na++], lo, 4); }
    else {
        addrinfo hints, *res = NULL;
        memset(&hints, 0, sizeof hints);
        hints.ai_family = AF_INET; hints.ai_socktype = SOCK_STREAM;
        int g = getaddrinfo(name, NULL, &hints, &res);
        if (g) {
            SetLastError(g == EAI_NONAME ? 11001 : g == EAI_AGAIN ? 11002 :
#ifdef EAI_NODATA
                         g == EAI_NODATA ? 11004 :
#endif
                         11003);
            return NULL;
        }
        for (addrinfo *a = res; a && na < 8; a = a->ai_next)
            if (a->ai_family == AF_INET) memcpy(t.ad[na++], &((sockaddr_in *)a->ai_addr)->sin_addr, 4);
        freeaddrinfo(res);
        if (!na) { SetLastError(11004); return NULL; }
    }
    snprintf(t.name, sizeof t.name, "%s", name);
    t.alias[0] = NULL;
    for (int i = 0; i < na; i++) t.list[i] = (char *)t.ad[i];
    t.list[na] = NULL;
    t.h = ws_hostent{ t.name, t.alias, 2, 4, t.list };
    return &t.h;
}
