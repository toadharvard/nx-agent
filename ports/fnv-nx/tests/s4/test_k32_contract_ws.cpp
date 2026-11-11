/* test_k32_contract_ws.cpp - setsockopt and WSAAsyncSelect on their documented (MSDN) domain (task W20 group 10).
 * Winsock's getsockopt is not an import of the exe (manifest), so options are checked by their results, error codes and
 * their effect on the socket's behaviour. ENV N1: loopback only. */
#include "s4_test.h"

#include <stdint.h>
#include <time.h>
#include <unistd.h>

extern "C" {
int fnvnx_ws2_WSAStartup(WORD, void *);
int fnvnx_ws2_WSAGetLastError(void);
int fnvnx_ws2_WSACleanup(void);
UINT_PTR fnvnx_ws2_socket(int, int, int);
int fnvnx_ws2_closesocket(UINT_PTR);
int fnvnx_ws2_bind(UINT_PTR, const void *, int);
int fnvnx_ws2_recv(UINT_PTR, char *, int, int);
int fnvnx_ws2_setsockopt(UINT_PTR, int, int, const char *, int);
int fnvnx_ws2_ioctlsocket(UINT_PTR, long, unsigned long *);
int fnvnx_ws2_WSAAsyncSelect(UINT_PTR, HWND, UINT, long);
}

#define SO(s, lvl, opt, p, n) fnvnx_ws2_setsockopt((s), (lvl), (opt), (const char *)(p), (n))
#define LASTERR() fnvnx_ws2_WSAGetLastError()
#define SOL 0xFFFF

TEST(k32c_setsockopt_matrix)
{
    struct { uint16_t family, port; uint32_t addr; char zero[8]; } a;
    WORD wv = 0x0101;
    char wsd[512];
    fnvnx_ws2_WSAStartup(wv, wsd);
    UINT_PTR tcp = fnvnx_ws2_socket(2, 1, 0), udp = fnvnx_ws2_socket(2, 2, 17);
    CHECK(tcp != ~(UINT_PTR)0 && udp != ~(UINT_PTR)0);
    int one = 1, zero = 0, big = 300, neg = -1;
    uint32_t ms = 150;
    /* every BOOL option at SOL_SOCKET and the others take a 4-byte value */
    static const int bools[] = { 4, 8, 0x10, 0x20, 0x100, ~4, 1, 0x40, 0x3002 };
    for (int o : bools) {
        CHECK_EQ(SO(tcp, SOL, o, &one, 4), 0);
        CHECK_EQ(SO(tcp, SOL, o, &zero, 4), 0);
        CHECK_EQ(SO(tcp, SOL, o, &one, 3), -1);
        CHECK_EQ(LASTERR(), 10014);                      /* WSAEFAULT: buffer too small */
        CHECK_EQ(SO(tcp, SOL, o, NULL, 4), -1);
        CHECK_EQ(LASTERR(), 10014);
    }
    CHECK_EQ(SO(tcp, SOL, 0x1001, &big, 4), 0);          /* SO_SNDBUF, SO_RCVBUF */
    CHECK_EQ(SO(tcp, SOL, 0x1002, &big, 4), 0);
    CHECK_EQ(SO(tcp, SOL, 0x1001, &neg, 4), -1);
    CHECK_EQ(LASTERR(), 10022);
    CHECK_EQ(SO(tcp, SOL, 0x1005, &ms, 4), 0);           /* SO_SNDTIMEO, SO_RCVTIMEO: DWORD milliseconds */
    CHECK_EQ(SO(tcp, SOL, 0x1006, &ms, 4), 0);
    CHECK_EQ(SO(tcp, SOL, 0x1006, &ms, 2), -1);
    CHECK_EQ(LASTERR(), 10014);
    uint16_t lg[2] = { 1, 5 };
    CHECK_EQ(SO(tcp, SOL, 0x80, lg, 4), 0);              /* SO_LINGER: struct linger */
    CHECK_EQ(SO(tcp, SOL, 0x80, lg, 3), -1);
    CHECK_EQ(LASTERR(), 10014);
    /* get-only options, watermarks and unknown options: WSAENOPROTOOPT */
    static const int no[] = { 2, 0x1007, 0x1008, 0x2001, 0x2003, 0x1003, 0x1004, 0x7777 };
    for (int o : no) {
        CHECK_EQ(SO(tcp, SOL, o, &one, 4), -1);
        CHECK_EQ(LASTERR(), 10042);
    }
    /* levels: IP, TCP, UDP; any other level is WSAEINVAL */
    CHECK_EQ(SO(tcp, 6, 1, &one, 4), 0);                 /* TCP_NODELAY */
    CHECK_EQ(SO(tcp, 6, 1, &one, 1), -1);
    CHECK_EQ(LASTERR(), 10014);
    CHECK_EQ(SO(tcp, 6, 0x7000, &one, 4), 0);            /* TCP_BSDURGENT */
    CHECK_EQ(SO(tcp, 6, 2, &one, 4), -1);
    CHECK_EQ(LASTERR(), 10042);
    CHECK_EQ(SO(udp, 6, 1, &one, 4), -1);                /* TCP options do not exist on a datagram socket */
    CHECK_EQ(LASTERR(), 10042);
    CHECK_EQ(SO(tcp, 0x1234, 1, &one, 4), -1);
    CHECK_EQ(LASTERR(), 10022);
    CHECK_EQ(SO(tcp, 41 /* IPPROTO_IPV6 */, 1, &one, 4), -1);
    CHECK_EQ(LASTERR(), 10022);
    /* IPPROTO_IP */
    int ttl = 64;
    CHECK_EQ(SO(udp, 0, 4, &ttl, 4), 0);                 /* IP_TTL */
    CHECK_EQ(SO(udp, 0, 3, &ttl, 4), 0);                 /* IP_TOS */
    CHECK_EQ(SO(udp, 0, 10, &ttl, 4), 0);                /* IP_MULTICAST_TTL */
    CHECK_EQ(SO(udp, 0, 11, &zero, 4), 0);               /* IP_MULTICAST_LOOP */
    CHECK_EQ(SO(udp, 0, 4, &big, 4), -1);                /* above 255 */
    CHECK_EQ(LASTERR(), 10022);
    CHECK_EQ(SO(udp, 0, 4, &neg, 4), -1);
    CHECK_EQ(LASTERR(), 10022);
    uint32_t lo = 0x0100007Fu;                           /* 127.0.0.1 in network order */
    CHECK_EQ(SO(udp, 0, 9, &lo, 4), 0);                  /* IP_MULTICAST_IF */
    uint32_t mreq[2] = { 0x010000E0u + 0x00000700u, lo };   /* 224.0.0.x group via loopback: the group is a multicast address */
    mreq[0] = 0x000000E0u | (0x7B << 24);                /* 224.0.0.123 */
    int r = SO(udp, 0, 12, mreq, 8);
    CHECK(r == 0 || (r == -1 && (LASTERR() == 10049 || LASTERR() == 10065 || LASTERR() == 10050 || LASTERR() == 10051)));   /* the stack decides about the interface */
    if (r == 0)
        CHECK_EQ(SO(udp, 0, 13, mreq, 8), 0);
    CHECK_EQ(SO(udp, 0, 12, mreq, 7), -1);
    CHECK_EQ(LASTERR(), 10014);
    CHECK_EQ(SO(tcp, 0, 12, mreq, 8), -1);               /* membership needs a datagram socket */
    CHECK_EQ(LASTERR(), 10042);
    CHECK_EQ(SO(udp, 0, 2, &one, 4), -1);                /* IP_HDRINCL: raw sockets only */
    CHECK_EQ(LASTERR(), 10042);
    CHECK_EQ(SO(udp, 17, 1, &one, 4), 0);                /* UDP_NOCHECKSUM */
    CHECK_EQ(SO(tcp, 17, 1, &one, 4), -1);
    CHECK_EQ(LASTERR(), 10042);
    /* SO_RCVTIMEO: a blocking receive on an empty socket ends with WSAETIMEDOUT after about that time */
    a.family = 2; a.port = 0; a.addr = 0x0100007Fu; memset(a.zero, 0, 8);
    CHECK_EQ(fnvnx_ws2_bind(udp, &a, 16), 0);
    uint32_t t100 = 100;
    CHECK_EQ(SO(udp, SOL, 0x1006, &t100, 4), 0);
    char b[8];
    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    CHECK_EQ(fnvnx_ws2_recv(udp, b, sizeof b, 0), -1);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    CHECK_EQ(LASTERR(), 10060);
    long dms = (long)((t1.tv_sec - t0.tv_sec) * 1000 + (t1.tv_nsec - t0.tv_nsec) / 1000000);
    CHECK(dms >= 80 && dms < 2000);
    unsigned long nb = 1;                                 /* non-blocking: WSAEWOULDBLOCK, not a timeout */
    fnvnx_ws2_ioctlsocket(udp, (long)0x8004667E, &nb);
    CHECK_EQ(fnvnx_ws2_recv(udp, b, sizeof b, 0), -1);
    CHECK_EQ(LASTERR(), 10035);
    fnvnx_ws2_closesocket(tcp);
    fnvnx_ws2_closesocket(udp);
    fnvnx_ws2_WSACleanup();
}

static LRESULT ws2_proc(HWND h, UINT m, WPARAM w, LPARAM l) { return DefWindowProcA(h, m, w, l); }

TEST(k32c_wsaasyncselect_rules)
{
    WORD wv = 0x0101;
    char wsd[512];
    fnvnx_ws2_WSAStartup(wv, wsd);
    WNDCLASSA wc; memset(&wc, 0, sizeof wc);
    wc.lpfnWndProc = ws2_proc; wc.lpszClassName = "WsAsyncRules";
    RegisterClassA(&wc);
    HWND h = CreateWindowExA(0, "WsAsyncRules", "ws", 0x80000000u, 0, 0, 10, 10, NULL, NULL, NULL, NULL);
    UINT_PTR s = fnvnx_ws2_socket(2, 1, 0);
    CHECK(h && s != ~(UINT_PTR)0);
    CHECK_EQ(fnvnx_ws2_WSAAsyncSelect(s, h, 0x5000, 1), 0);
    CHECK_EQ(fnvnx_ws2_WSAAsyncSelect(s, (HWND)(uintptr_t)0x77777, 0x5000, 1), -1);   /* no such window */
    CHECK_EQ(LASTERR(), 10022);
    CHECK_EQ(fnvnx_ws2_WSAAsyncSelect(s, NULL, 0x5000, 1), -1);
    CHECK_EQ(LASTERR(), 10022);
    CHECK_EQ(fnvnx_ws2_WSAAsyncSelect(s, h, 0x5000, 0x4000), -1);                     /* not an event bit */
    CHECK_EQ(LASTERR(), 10022);
    CHECK_EQ(fnvnx_ws2_WSAAsyncSelect(s, h, 0x5000, 0x3F), 0);                        /* all of FD_READ..FD_CLOSE */
    CHECK_EQ(fnvnx_ws2_WSAAsyncSelect(s, h, 0, 0), 0);                                /* 0 cancels, any window */
    CHECK_EQ(fnvnx_ws2_WSAAsyncSelect(s, NULL, 0, 0), 0);
    CHECK_EQ(fnvnx_ws2_WSAAsyncSelect(~(UINT_PTR)0 - 5, h, 0x5000, 1), -1);
    CHECK_EQ(LASTERR(), 10038);                                                       /* WSAENOTSOCK */
    fnvnx_ws2_closesocket(s);
    DestroyWindow(h);
    fnvnx_ws2_WSACleanup();
}
