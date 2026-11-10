/* test_winsock.cpp - the Winsock 1.1 layer (platform/win32/winsock.cpp) over the host's BSD sockets. */
#include "s4_test.h"

#include <stdint.h>
#include <unistd.h>

extern "C" {
int fnvnx_ws2_WSAStartup(WORD, void *);
int fnvnx_ws2_WSACleanup(void);
int fnvnx_ws2_WSAGetLastError(void);
void fnvnx_ws2_WSASetLastError(int);
UINT_PTR fnvnx_ws2_socket(int, int, int);
int fnvnx_ws2_closesocket(UINT_PTR);
unsigned short fnvnx_ws2_htons(unsigned short);
unsigned short fnvnx_ws2_ntohs(unsigned short);
unsigned long fnvnx_ws2_inet_addr(const char *);
char *fnvnx_ws2_inet_ntoa(unsigned long);
void *fnvnx_ws2_gethostbyname(const char *);
int fnvnx_ws2_gethostname(char *, int);
int fnvnx_ws2_fdisset(UINT_PTR, void *);
int fnvnx_ws2_bind(UINT_PTR, const void *, int);
int fnvnx_ws2_listen(UINT_PTR, int);
UINT_PTR fnvnx_ws2_accept(UINT_PTR, void *, int *);
int fnvnx_ws2_connect(UINT_PTR, const void *, int);
int fnvnx_ws2_send(UINT_PTR, const char *, int, int);
int fnvnx_ws2_recv(UINT_PTR, char *, int, int);
int fnvnx_ws2_setsockopt(UINT_PTR, int, int, const char *, int);
int fnvnx_ws2_ioctlsocket(UINT_PTR, long, unsigned long *);
int fnvnx_ws2_select(int, void *, void *, void *, const void *);
int fnvnx_ws2_WSAAsyncSelect(UINT_PTR, HWND, UINT, long);
}

#define WS_INVALID (~(UINT_PTR)0)
struct ws_in { uint16_t family, port; uint32_t addr; char zero[8]; };
struct ws_set { unsigned count; UINT_PTR arr[64]; };
struct ws_tv { int32_t sec, usec; };
struct ws_data { uint16_t ver, high; char desc[257], status[129]; uint16_t maxsock, maxudp; char *vendor; };
struct ws_hostent { char *name; char **aliases; int16_t type, len; char **addrs; };

static void start(void) { ws_data d; CHECK_EQ(fnvnx_ws2_WSAStartup(0x0101, &d), 0); }
static ws_in addr_of(uint32_t host_order_ip, int port)
{
    ws_in a; memset(&a, 0, sizeof a);
    a.family = 2; a.port = fnvnx_ws2_htons((unsigned short)port);
    a.addr = fnvnx_ws2_htons((unsigned short)(host_order_ip >> 16)) | ((uint32_t)fnvnx_ws2_htons((unsigned short)host_order_ip) << 16);
    return a;
}
static ws_in lo(int port) { return addr_of(0x7F000001u, port); }
#define LASTERR() fnvnx_ws2_WSAGetLastError()

static int g_port;
static UINT_PTR listener(void)
{
    UINT_PTR l = fnvnx_ws2_socket(2, 1, 0);
    for (int i = 0; i < 20; i++) {
        g_port = 20000 + (int)((getpid() * 7 + i * 131) % 30000);
        ws_in a = lo(g_port);
        if (fnvnx_ws2_bind(l, &a, sizeof a) == 0) return l;
        if (LASTERR() != 10048) break;
    }
    return l;
}

TEST(ws_startup_counting)
{
    UINT_PTR s;
    CHECK_EQ(fnvnx_ws2_socket(2, 1, 0), WS_INVALID);
    CHECK_EQ(LASTERR(), 10093);
    char b[8]; CHECK_EQ(fnvnx_ws2_recv(1, b, 1, 0), -1); CHECK_EQ(LASTERR(), 10093);
    CHECK_EQ(fnvnx_ws2_gethostname(b, 8), -1); CHECK_EQ(LASTERR(), 10093);
    CHECK(fnvnx_ws2_gethostbyname("localhost") == NULL); CHECK_EQ(LASTERR(), 10093);
    CHECK_EQ(fnvnx_ws2_WSACleanup(), -1); CHECK_EQ(LASTERR(), 10093);
    ws_data d; memset(&d, 0xAA, sizeof d);
    CHECK_EQ(fnvnx_ws2_WSAStartup(0x0000, &d), 10092);
    CHECK_EQ(fnvnx_ws2_WSAStartup(0x0101, NULL), 10014);
    CHECK_EQ(fnvnx_ws2_WSAStartup(0x0101, &d), 0);
    CHECK_EQ(d.ver, 0x0101); CHECK_EQ(d.high, 0x0101);
    CHECK_STR(d.desc, "WinSock 1.1"); CHECK_STR(d.status, "Running");
    CHECK_EQ(d.maxsock, 32767); CHECK_EQ(d.maxudp, 65467); CHECK(d.vendor == NULL);
    CHECK_EQ(fnvnx_ws2_WSAStartup(0x0202, &d), 0);        /* negotiated down to 1.1 */
    CHECK_EQ(d.ver, 0x0101); CHECK_EQ(d.high, 0x0101);
    CHECK_EQ(fnvnx_ws2_WSAStartup(0x0001, &d), 0);        /* 1.0 is honoured */
    CHECK_EQ(d.ver, 0x0001);
    s = fnvnx_ws2_socket(2, 1, 0);
    CHECK(s != WS_INVALID);
    CHECK_EQ(fnvnx_ws2_WSACleanup(), 0);
    CHECK_EQ(fnvnx_ws2_WSACleanup(), 0);
    CHECK_EQ(fnvnx_ws2_closesocket(s), 0);                /* still live: two of three starts released */
    CHECK_EQ(fnvnx_ws2_WSACleanup(), 0);                  /* last: closes everything */
    CHECK_EQ(fnvnx_ws2_socket(2, 1, 0), WS_INVALID);
    CHECK_EQ(LASTERR(), 10093);
    fnvnx_ws2_WSASetLastError(1234);
    CHECK_EQ(fnvnx_ws2_WSAGetLastError(), 1234);
    CHECK_EQ(GetLastError(), 1234);
    CHECK_EQ(fnvnx_ws2_htons(0x1234), 0x3412);
    CHECK_EQ(fnvnx_ws2_ntohs(0x3412), 0x1234);
}

TEST(ws_last_cleanup_closes_sockets)
{
    start();
    UINT_PTR s = fnvnx_ws2_socket(2, 1, 0);
    CHECK(s != WS_INVALID);
    fnvnx_ws2_WSACleanup();
    start();
    char b[4];
    CHECK_EQ(fnvnx_ws2_recv(s, b, 4, 0), -1);             /* the handle died with the last cleanup */
    CHECK_EQ(LASTERR(), 10038);
    fnvnx_ws2_WSACleanup();
}

TEST(ws_inet_addr_syntax)
{
    static const struct { const char *s; unsigned long v; } t[] = {
        { "127.0.0.1", 0x0100007Ful }, { "1.2.3.4", 0x04030201ul }, { "0x7f.1", 0x0100007Ful },
        { "127.1", 0x0100007Ful }, { "10.0.513", 0x0102000Aul }, { "0x01020304", 0x04030201ul },
        { "017.0x10.010.1", 0x0108100Ful }, { "4294967295", 0xFFFFFFFFul }, { "255.255.255.255", 0xFFFFFFFFul },
        { "16909060", 0x04030201ul }, { "0", 0ul }, { "1.2.3.4 ", 0x04030201ul },
        { "", 0xFFFFFFFFul }, { "1.2.3.4.5", 0xFFFFFFFFul }, { "256.1.1.1", 0xFFFFFFFFul },
        { "1.2.65536", 0xFFFFFFFFul }, { "08", 0xFFFFFFFFul }, { "a.b.c.d", 0xFFFFFFFFul },
        { "1..2", 0xFFFFFFFFul }, { "1.2.3.", 0xFFFFFFFFul }, { "0x", 0xFFFFFFFFul }, { "1.2.3.4x", 0xFFFFFFFFul },
    };
    for (unsigned i = 0; i < sizeof t / sizeof t[0]; i++) {
        unsigned long r = fnvnx_ws2_inet_addr(t[i].s);
        if ((r & 0xFFFFFFFFul) != t[i].v) fprintf(stderr, "  inet_addr(\"%s\") = %lx\n", t[i].s, r);
        CHECK_EQ(r & 0xFFFFFFFFul, t[i].v);
    }
    CHECK_STR(fnvnx_ws2_inet_ntoa(0x0100007Ful), "127.0.0.1");
    CHECK_STR(fnvnx_ws2_inet_ntoa(0x04030201ul), "1.2.3.4");
}

TEST(ws_hostnames)
{
    start();
    ws_hostent *h = (ws_hostent *)fnvnx_ws2_gethostbyname("localhost");
    CHECK(h != NULL);
    if (h) {
        CHECK_EQ(h->type, 2); CHECK_EQ(h->len, 4);
        CHECK(h->addrs && h->addrs[0] && h->addrs[1] == NULL);
        CHECK(h->addrs && h->addrs[0] && memcmp(h->addrs[0], "\x7f\0\0\x01", 4) == 0);
        CHECK(h->aliases && h->aliases[0] == NULL);
    }
    h = (ws_hostent *)fnvnx_ws2_gethostbyname("10.1.2.3");
    CHECK(h != NULL);
    if (h) { CHECK_STR(h->name, "10.1.2.3"); CHECK(h->addrs && memcmp(h->addrs[0], "\x0a\x01\x02\x03", 4) == 0); }
    char name[64];
    CHECK_EQ(fnvnx_ws2_gethostname(name, sizeof name), 0);
    CHECK(name[0] != 0);
    h = (ws_hostent *)fnvnx_ws2_gethostbyname(name);
    CHECK(h != NULL);
    char tiny[1];
    CHECK_EQ(fnvnx_ws2_gethostname(tiny, 1), -1); CHECK_EQ(LASTERR(), 10014);   /* WSAEFAULT: buffer too small */
    CHECK(fnvnx_ws2_gethostbyname("no-such-host.invalid") == NULL);
    int e = LASTERR();
    CHECK(e == 11001 || e == 11002 || e == 11003 || e == 11004);
    fnvnx_ws2_WSACleanup();
}

TEST(ws_tcp_loopback_conversation)
{
    start();
    UINT_PTR l = listener();
    CHECK(l != WS_INVALID);
    int one = 1;
    CHECK_EQ(fnvnx_ws2_setsockopt(l, 0xFFFF, 4, (const char *)&one, 4), 0);
    CHECK_EQ(fnvnx_ws2_listen(l, 5), 0);
    UINT_PTR c = fnvnx_ws2_socket(2, 1, 6);
    ws_in a = lo(g_port);
    CHECK_EQ(fnvnx_ws2_connect(c, &a, sizeof a), 0);
    ws_set rs; rs.count = 1; rs.arr[0] = l;
    ws_tv tv = { 2, 0 };
    CHECK_EQ(fnvnx_ws2_select(0, &rs, NULL, NULL, &tv), 1);
    CHECK(fnvnx_ws2_fdisset(l, &rs));
    ws_in peer; int plen = sizeof peer;
    UINT_PTR s = fnvnx_ws2_accept(l, &peer, &plen);
    CHECK(s != WS_INVALID && s != l && s != c);
    CHECK_EQ(plen, 16); CHECK_EQ(peer.family, 2);
    CHECK_EQ(peer.addr, 0x0100007Fu);
    CHECK_EQ(fnvnx_ws2_send(c, "hello", 5, 0), 5);
    rs.count = 1; rs.arr[0] = s; tv.sec = 2;
    CHECK_EQ(fnvnx_ws2_select(0, &rs, NULL, NULL, &tv), 1);
    unsigned long n = 0;
    CHECK_EQ(fnvnx_ws2_ioctlsocket(s, (long)0x4004667F, &n), 0);
    CHECK_EQ(n, 5);
    char b[16] = {0};
    CHECK_EQ(fnvnx_ws2_recv(s, b, 3, 2 /* MSG_PEEK */), 3);
    CHECK(memcmp(b, "hel", 3) == 0);
    CHECK_EQ(fnvnx_ws2_recv(s, b, 16, 0), 5);
    CHECK(memcmp(b, "hello", 5) == 0);
    ws_set ws; ws.count = 1; ws.arr[0] = c; tv.sec = 0;
    CHECK_EQ(fnvnx_ws2_select(0, NULL, &ws, NULL, &tv), 1);       /* writable */
    rs.count = 1; rs.arr[0] = s;
    CHECK_EQ(fnvnx_ws2_select(0, &rs, NULL, NULL, &tv), 0);       /* nothing to read: set emptied */
    CHECK_EQ(rs.count, 0);
    CHECK(!fnvnx_ws2_fdisset(s, &rs));
    CHECK_EQ(fnvnx_ws2_select(0, NULL, NULL, NULL, &tv), -1); CHECK_EQ(LASTERR(), 10022);
    unsigned long nb = 1;
    CHECK_EQ(fnvnx_ws2_ioctlsocket(s, (long)0x8004667E, &nb), 0);
    CHECK_EQ(fnvnx_ws2_recv(s, b, 16, 0), -1);
    CHECK_EQ(LASTERR(), 10035);
    CHECK_EQ(fnvnx_ws2_closesocket(c), 0);
    nb = 0; fnvnx_ws2_ioctlsocket(s, (long)0x8004667E, &nb);
    CHECK_EQ(fnvnx_ws2_recv(s, b, 16, 0), 0);                     /* graceful close */
    CHECK_EQ(fnvnx_ws2_closesocket(s), 0);
    CHECK_EQ(fnvnx_ws2_closesocket(s), -1); CHECK_EQ(LASTERR(), 10038);
    CHECK_EQ(fnvnx_ws2_closesocket(l), 0);
    fnvnx_ws2_WSACleanup();
}

TEST(ws_error_matrix)
{
    start();
    UINT_PTR l = listener();
    fnvnx_ws2_closesocket(l);                                     /* a port nobody listens on */
    UINT_PTR c = fnvnx_ws2_socket(2, 1, 0);
    ws_in a = lo(g_port);
    CHECK_EQ(fnvnx_ws2_connect(c, &a, sizeof a), -1);
    CHECK_EQ(LASTERR(), 10061);
    fnvnx_ws2_closesocket(c);
    char b[4];
    CHECK_EQ(fnvnx_ws2_recv(9999, b, 4, 0), -1); CHECK_EQ(LASTERR(), 10038);
    CHECK_EQ(fnvnx_ws2_send(WS_INVALID, b, 4, 0), -1); CHECK_EQ(LASTERR(), 10038);
    CHECK_EQ(fnvnx_ws2_socket(23, 1, 0), WS_INVALID); CHECK_EQ(LASTERR(), 10047);
    CHECK_EQ(fnvnx_ws2_socket(2, 9, 0), WS_INVALID); CHECK_EQ(LASTERR(), 10044);
    CHECK_EQ(fnvnx_ws2_socket(2, 1, 17), WS_INVALID); CHECK(LASTERR() == 10043 || LASTERR() == 10041);
    UINT_PTR u = fnvnx_ws2_socket(2, 2, 17);
    CHECK(u != WS_INVALID);
    int v = 1;
    CHECK_EQ(fnvnx_ws2_setsockopt(u, 0xFFFF, 0x20, (const char *)&v, 4), 0);   /* SO_BROADCAST */
    CHECK_EQ(fnvnx_ws2_setsockopt(u, 0xFFFF, 0x7777, (const char *)&v, 4), -1);
    CHECK_EQ(LASTERR(), 10042);
    uint16_t lg[2] = { 1, 0 };
    CHECK_EQ(fnvnx_ws2_setsockopt(u, 0xFFFF, 0x80, (const char *)lg, 4), 0);   /* SO_LINGER */
    CHECK_EQ(fnvnx_ws2_setsockopt(u, 0xFFFF, ~0x80, (const char *)&v, 4), 0);  /* SO_DONTLINGER */
    CHECK_EQ(fnvnx_ws2_setsockopt(u, 0xFFFF, 0x1001, (const char *)&v, 4), 0);
    ws_in bad = lo(1); bad.family = 23;
    CHECK_EQ(fnvnx_ws2_bind(u, &bad, sizeof bad), -1); CHECK_EQ(LASTERR(), 10047);
    CHECK_EQ(fnvnx_ws2_bind(u, &bad, 4), -1); CHECK_EQ(LASTERR(), 10014);
    unsigned long nb = 1;
    CHECK_EQ(fnvnx_ws2_ioctlsocket(u, (long)0x12345678, &nb), -1); CHECK_EQ(LASTERR(), 10022);
    CHECK_EQ(fnvnx_ws2_ioctlsocket(u, (long)0x8004667E, &nb), 0);
    CHECK_EQ(fnvnx_ws2_recv(u, b, 4, 0), -1); CHECK_EQ(LASTERR(), 10035);
    fnvnx_ws2_closesocket(u);
    fnvnx_ws2_WSACleanup();
}

static LRESULT ws_proc(HWND h, UINT m, WPARAM w, LPARAM l) { return DefWindowProcA(h, m, w, l); }

/* pump until a message 0x5000 with the wanted event arrives (max 2 s); returns its error code or -1 */
static int wait_event(HWND h, UINT_PTR s, int ev)
{
    for (int i = 0; i < 200; i++) {
        MSG m;
        while (PeekMessageA(&m, h, 0, 0, PM_REMOVE)) {
            if (m.message == 0x5000 && (UINT_PTR)m.wParam == s && (int)(m.lParam & 0xFFFF) == ev)
                return (int)((m.lParam >> 16) & 0xFFFF);
        }
        Sleep(10);
    }
    return -1;
}

TEST(ws_async_select)
{
    start();
    WNDCLASSA wc; memset(&wc, 0, sizeof wc);
    wc.lpfnWndProc = ws_proc; wc.lpszClassName = "WsAsyncClass";
    RegisterClassA(&wc);
    HWND h = CreateWindowExA(0, "WsAsyncClass", "ws", 0x80000000u, 0, 0, 10, 10, NULL, NULL, NULL, NULL);
    CHECK(h != NULL);
    UINT_PTR l = listener();
    fnvnx_ws2_listen(l, 5);
    CHECK_EQ(fnvnx_ws2_WSAAsyncSelect(l, h, 0x5000, 8 /* FD_ACCEPT */), 0);
    UINT_PTR c = fnvnx_ws2_socket(2, 1, 0);
    CHECK_EQ(fnvnx_ws2_WSAAsyncSelect(c, h, 0x5000, 0x10 | 2 | 1 | 0x20), 0);   /* connect, write, read, close */
    ws_in a = lo(g_port);
    int r = fnvnx_ws2_connect(c, &a, sizeof a);
    CHECK(r == 0 || (r == -1 && LASTERR() == 10035));            /* non-blocking now */
    CHECK_EQ(wait_event(h, l, 8), 0);                              /* FD_ACCEPT */
    UINT_PTR s = fnvnx_ws2_accept(l, NULL, NULL);
    CHECK(s != WS_INVALID);
    CHECK_EQ(wait_event(h, c, 0x10), 0);                           /* FD_CONNECT */
    CHECK_EQ(wait_event(h, c, 2), 0);                              /* FD_WRITE */
    CHECK_EQ(fnvnx_ws2_send(s, "ping", 4, 0), 4);                  /* s inherited FD_ACCEPT only: it posts nothing */
    CHECK_EQ(wait_event(h, c, 1), 0);                              /* FD_READ on c */
    char b[8];
    CHECK_EQ(fnvnx_ws2_recv(c, b, 8, 0), 4);                       /* re-arms FD_READ */
    fnvnx_ws2_send(s, "x", 1, 0);
    CHECK_EQ(wait_event(h, c, 1), 0);
    CHECK_EQ(fnvnx_ws2_recv(c, b, 8, 0), 1);
    fnvnx_ws2_closesocket(s);
    CHECK_EQ(wait_event(h, c, 0x20), 0);                           /* FD_CLOSE */
    CHECK_EQ(fnvnx_ws2_WSAAsyncSelect(c, h, 0, 0), 0);             /* cancel */
    fnvnx_ws2_closesocket(c);
    fnvnx_ws2_closesocket(l);
    DestroyWindow(h);
    fnvnx_ws2_WSACleanup();
}
