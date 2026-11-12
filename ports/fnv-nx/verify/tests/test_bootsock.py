#!/usr/bin/env python3
"""Tests of verify/bootsock.py (the socket services of the boot stage machine, a host with no network interface), table-driven, on a fake
guest memory: every served (service, case) with its result, every NOT answered one with NotDetermined. Plain script: exit 0 on pass."""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import bootsock  # noqa: E402
from bootsock import NotDetermined, BadGuestMemory, neg  # noqa: E402

FAILED, N = [], [0]
SA, BUF, VAL, LEN, FD = 0x1000, 0x2000, 0x2800, 0x2900, 0x2A00
V4, V6 = 2, 10


class Mem:
    def __init__(self):
        self.b = bytearray(0x4000)

    def can_read(self, lo, hi):
        return 0x1000 <= lo and hi <= 0x5000

    can_write = can_read

    def read(self, a, n):
        return bytes(self.b[a - 0x1000:a - 0x1000 + n])

    def write(self, a, d):
        self.b[a - 0x1000:a - 0x1000 + len(d)] = d


class Env:
    def __init__(self):
        self.mem, self.t = Mem(), [0]
        self.m = bootsock.SockModel(self.mem, lambda: self.t[0])

    def sa(self, fam, port, addr, flow=0, scope=0, at=SA):
        self.mem.write(at, struct.pack("<HH16sIII", fam, port, bytes(addr).ljust(16, b"\0"), flow, scope, 0))
        return at

    def s(self, dom=V4, typ=1, proto=0):
        return self.m.call(16, [dom, typ, proto])

    def val(self, v):
        self.mem.write(VAL, v)
        return VAL


def case(name, ok, detail=""):
    N[0] += 1
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else "  " + str(detail)))
    if not ok:
        FAILED.append(name)


def res(f):
    try:
        r = f()
        if isinstance(r, tuple) and len(r) == 2 and isinstance(r[0], tuple) and r[0][0] == "sock":   # (("sock", thunk, deadline), result): the thunk is tested apart
            return (("sock", r[0][2]), r[1])
        return r
    except NotDetermined:
        return "ND"
    except BadGuestMemory:
        return "MEM"


def table(title, rows):
    """rows: (name, lambda env -> result, expected); expected "ND" = NotDetermined, "MEM" = BadGuestMemory"""
    bad = []
    for name, fn, want in rows:
        e = Env()
        got = res(lambda: fn(e))
        if got != want:
            fmt = lambda v: v if isinstance(v, str) else hex(v) if isinstance(v, int) else repr(v)
            bad.append((name, fmt(got), fmt(want)))
    case("%s (%d cases)" % (title, len(rows)), not bad, bad)


LO4, LO6 = b"\x7f\0\0\1", bytes(15) + b"\1"
EXT4, EXT6 = b"\x08\x08\x08\x08", b"\x20\x01\x0d\xb8" + bytes(11) + b"\1"
c = bootsock.U64


def main():
    table("SOCKET: domains, types, protocols, handle numbers", [
        ("stream-v4", lambda e: e.s(V4, 1), 3), ("dgram-v6", lambda e: e.s(V6, 2), 3),
        ("tcp", lambda e: e.s(V4, 1, 6), 3), ("udp", lambda e: e.s(V4, 2, 17), 3),
        ("second", lambda e: (e.s(), e.s())[1], 4),
        ("lowest-free", lambda e: (e.s(), e.s(), e.m.call(17, [3]), e.s())[3], 3),
        ("af-bad", lambda e: e.s(1), neg(97)), ("af-wide", lambda e: e.s(0x100000002), neg(97)), ("af-neg", lambda e: e.s(c), neg(97)),
        ("type-bad", lambda e: e.s(V4, 4), neg(22)), ("type-0", lambda e: e.s(V4, 0), neg(22)),
        ("stream-udp", lambda e: e.s(V4, 1, 17), neg(93)), ("dgram-tcp", lambda e: e.s(V4, 2, 6), neg(93)), ("proto-99", lambda e: e.s(V4, 1, 99), neg(93)),
        ("af-unspec", lambda e: e.s(0), "ND"), ("raw", lambda e: e.s(V4, 3), "ND"), ("sctp", lambda e: e.s(V4, 1, 132), "ND"),
        ("proto-wide", lambda e: e.s(V4, 1, 0x100000006), 3), ("proto-neg", lambda e: e.s(V4, 1, c), "ND"), ("proto-300", lambda e: e.s(V4, 1, 300), "ND"),
    ])
    table("CLOSE and the handle check of every handle-first service", [
        ("close", lambda e: (e.s(), e.m.call(17, [3]))[1], 0), ("close-twice", lambda e: (e.s(), e.m.call(17, [3]), e.m.call(17, [3]))[2], neg(9)),
        ("close-unopen", lambda e: e.m.call(17, [5]), neg(9)),
        *[("ebadf-%d" % f, lambda e, f=f: e.m.call(f, [0x100000003] + [0] * 4), neg(9)) for f in (17, 18, 19, 20, 21, 22, 23, 24, 25, 27, 28, 29, 30, 31)],
        ("wide-handle", lambda e: (e.s(), e.m.call(30, [0x100000003]))[1], neg(9)),
        ("negative-handle", lambda e: (e.s(), e.m.call(30, [0xFFFFFFFF]))[1], neg(9)),
    ])
    table("BIND / LISTEN / ACCEPT", [
        ("bind-null", lambda e: (e.s(), e.m.call(18, [3, 0]))[1], neg(14)),
        ("bind-badfam", lambda e: (e.s(), e.m.call(18, [3, e.sa(7, 80, LO4)]))[1], neg(97)),
        ("bind-loop-v4", lambda e: (e.s(), e.m.call(18, [3, e.sa(V4, 8080, LO4)]))[1], 0),
        ("bind-any-v4-port0", lambda e: (e.s(), e.m.call(18, [3, e.sa(V4, 0, bytes(4))]))[1], 0),
        ("bind-loop-v6", lambda e: (e.s(V6), e.m.call(18, [3, e.sa(V6, 8080, LO6)]))[1], 0),
        ("bind-any-v6", lambda e: (e.s(V6), e.m.call(18, [3, e.sa(V6, 8080, bytes(16))]))[1], 0),
        ("bind-external-v4", lambda e: (e.s(), e.m.call(18, [3, e.sa(V4, 8080, EXT4)]))[1], neg(99)),
        ("bind-external-v6", lambda e: (e.s(V6), e.m.call(18, [3, e.sa(V6, 8080, EXT6)]))[1], neg(99)),
        ("bind-twice", lambda e: (e.s(), e.m.call(18, [3, e.sa(V4, 8080, LO4)]), e.m.call(18, [3, e.sa(V4, 8081, LO4)]))[2], neg(22)),
        ("bind-unmem", lambda e: (e.s(), e.m.call(18, [3, 0x10]))[1], "MEM"),
        ("bind-unspec", lambda e: (e.s(), e.m.call(18, [3, e.sa(0, 80, LO4)]))[1], "ND"),
        ("bind-other-family", lambda e: (e.s(), e.m.call(18, [3, e.sa(V6, 8080, LO6)]))[1], "ND"),
        ("bind-priv-port", lambda e: (e.s(), e.m.call(18, [3, e.sa(V4, 80, LO4)]))[1], "ND"),
        ("bind-scope", lambda e: (e.s(V6), e.m.call(18, [3, e.sa(V6, 8080, LO6, 0, 5)]))[1], "ND"),
        ("bind-multicast", lambda e: (e.s(), e.m.call(18, [3, e.sa(V4, 8080, b"\xe0\0\0\1")]))[1], "ND"),
        ("bind-port-taken", lambda e: (e.s(), e.s(), e.m.call(18, [3, e.sa(V4, 8080, LO4)]), e.m.call(18, [4, e.sa(V4, 8080, LO4)]))[3], "ND"),
        ("bind-port-other-addr", lambda e: (e.s(), e.s(), e.m.call(18, [3, e.sa(V4, 8080, LO4)]), e.m.call(18, [4, e.sa(V4, 8080, b"\x7f\0\0\2")]))[3], 0),
        ("listen-dgram", lambda e: (e.s(V4, 2), e.m.call(19, [3, 5]))[1], neg(95)),
        ("listen-unbound", lambda e: (e.s(), e.m.call(19, [3, 5]))[1], "ND"),
        ("listen-bound", lambda e: (e.s(), e.m.call(18, [3, e.sa(V4, 8080, LO4)]), e.m.call(19, [3, 5]))[2], 0),
        ("accept-dgram", lambda e: (e.s(V4, 2), e.m.call(20, [3, 0]))[1], neg(95)),
        ("accept-not-listening", lambda e: (e.s(), e.m.call(20, [3, 0]))[1], neg(22)),
        ("accept-nonblock", lambda e: (e.s(), e.m.call(18, [3, e.sa(V4, 8080, LO4)]), e.m.call(19, [3, 1]), e.m.call(31, [3, 0x800]), e.m.call(20, [3, 0]))[4], neg(11)),
        ("accept-blocks", lambda e: (e.s(), e.m.call(18, [3, e.sa(V4, 8080, LO4)]), e.m.call(19, [3, 1]), e.m.call(20, [3, 0]))[3], (("sock", None), neg(11))),
    ])
    table("CONNECT / SENDTO / RECVFROM", [
        ("connect-null", lambda e: (e.s(), e.m.call(21, [3, 0]))[1], neg(14)),
        ("connect-badfam", lambda e: (e.s(), e.m.call(21, [3, e.sa(1, 80, EXT4)]))[1], neg(97)),
        ("connect-ext-stream", lambda e: (e.s(), e.m.call(21, [3, e.sa(V4, 80, EXT4)]))[1], neg(101)),
        ("connect-ext-dgram", lambda e: (e.s(V4, 2), e.m.call(21, [3, e.sa(V4, 80, EXT4)]))[1], neg(101)),
        ("connect-ext-v6", lambda e: (e.s(V6), e.m.call(21, [3, e.sa(V6, 80, EXT6)]))[1], neg(101)),
        ("connect-ext-nonblock", lambda e: (e.s(), e.m.call(31, [3, 0x800]), e.m.call(21, [3, e.sa(V4, 80, EXT4)]))[2], neg(101)),
        ("connect-loop-stream", lambda e: (e.s(), e.m.call(21, [3, e.sa(V4, 80, LO4)]))[1], "ND"),
        ("connect-loop-dgram", lambda e: (e.s(V4, 2), e.m.call(21, [3, e.sa(V4, 80, LO4)]))[1], 0),
        ("connect-any-dgram", lambda e: (e.s(V4, 2), e.m.call(21, [3, e.sa(V4, 80, bytes(4))]))[1], "ND"),
        ("connect-unspec", lambda e: (e.s(V4, 2), e.m.call(21, [3, e.sa(0, 80, LO4)]))[1], "ND"),
        ("connect-other-family", lambda e: (e.s(V4, 2), e.m.call(21, [3, e.sa(V6, 80, EXT6)]))[1], "ND"),
        ("sendto-len", lambda e: (e.s(V4, 2), e.m.call(22, [3, BUF, 0x80000000, 0, 0]))[1], neg(22)),
        ("sendto-flag", lambda e: (e.s(V4, 2), e.m.call(22, [3, BUF, 1, 0x8000, 0]))[1], neg(95)),
        ("sendto-flag-wide", lambda e: (e.s(V4, 2), e.m.call(22, [3, BUF, 1, 1 << 32, 0]))[1], neg(95)),
        ("sendto-badfam", lambda e: (e.s(V4, 2), e.m.call(22, [3, BUF, 1, 0, e.sa(5, 1, EXT4)]))[1], neg(97)),
        ("sendto-ext", lambda e: (e.s(V4, 2), e.m.call(22, [3, BUF, 1, 0, e.sa(V4, 9, EXT4)]))[1], neg(101)),
        ("sendto-nodest", lambda e: (e.s(V4, 2), e.m.call(22, [3, BUF, 1, 0, 0]))[1], neg(89)),
        ("sendto-loop", lambda e: (e.s(V4, 2), e.m.call(22, [3, BUF, 1, 0, e.sa(V4, 9, LO4)]))[1], "ND"),
        ("sendto-peer", lambda e: (e.s(V4, 2), e.m.call(21, [3, e.sa(V4, 9, LO4)]), e.m.call(22, [3, BUF, 1, 0, 0]))[2], "ND"),
        ("sendto-stream", lambda e: (e.s(), e.m.call(22, [3, BUF, 1, 0, e.sa(V4, 9, EXT4)]))[1], "ND"),
        ("sendto-oob", lambda e: (e.s(V4, 2), e.m.call(22, [3, BUF, 1, 1, e.sa(V4, 9, EXT4)]))[1], "ND"),
        ("recv-len", lambda e: (e.s(V4, 2), e.m.call(23, [3, BUF, 0x80000000, 0, 0]))[1], neg(22)),
        ("recv-flag", lambda e: (e.s(V4, 2), e.m.call(23, [3, BUF, 1, 0x8000, 0]))[1], neg(95)),
        ("recv-stream", lambda e: (e.s(), e.m.call(23, [3, BUF, 1, 0, 0]))[1], neg(107)),
        ("recv-nonblock", lambda e: (e.s(V4, 2), e.m.call(31, [3, 0x800]), e.m.call(23, [3, BUF, 1, 0, 0]))[2], neg(11)),
        ("recv-dontwait", lambda e: (e.s(V4, 2), e.m.call(23, [3, BUF, 1, 0x40, 0]))[1], neg(11)),
        ("recv-blocks", lambda e: (e.s(V4, 2), e.m.call(23, [3, BUF, 1, 0, 0]))[1], (("sock", None), neg(11))),
        ("recv-timeout", lambda e: (e.s(V4, 2), e.m.call(24, [3, 1, 20, e.val(struct.pack("<qq", 2, 500000)), 16]), e.m.call(23, [3, BUF, 1, 0, 0]))[2],
         (("sock", 2500000000), neg(11))),
        ("recv-oob", lambda e: (e.s(V4, 2), e.m.call(23, [3, BUF, 1, 1, 0]))[1], "ND"),
    ])
    table("GETFL / SETFL / SETSOCKOPT / GETSOCKOPT", [
        ("getfl", lambda e: (e.s(), e.m.call(30, [3]))[1], 2), ("setfl-nb", lambda e: (e.s(), e.m.call(31, [3, 0x800]), e.m.call(30, [3]))[2], 0x802),
        ("setfl-clear", lambda e: (e.s(), e.m.call(31, [3, 0x800]), e.m.call(31, [3, 0]), e.m.call(30, [3]))[3], 2),
        ("setfl-other-bit", lambda e: (e.s(), e.m.call(31, [3, 0x402]), e.m.call(30, [3]))[2], 2),
        ("setfl-other-bit-nb", lambda e: (e.s(), e.m.call(31, [3, 0x1802]), e.m.call(30, [3]))[2], 0x802),
        ("setfl-other-bit-result", lambda e: (e.s(), e.m.call(31, [3, 0x802]))[1], 0),
        ("set-unknown-level", lambda e: (e.s(), e.m.call(24, [3, 5, 1, VAL, 4]))[1], neg(92)),
        ("set-unknown-opt", lambda e: (e.s(), e.m.call(24, [3, 1, 99, VAL, 4]))[1], neg(92)),
        ("set-wide-opt", lambda e: (e.s(), e.m.call(24, [3, 1, 1 << 32 | 2, VAL, 4]))[1], neg(92)),
        ("set-null-val", lambda e: (e.s(), e.m.call(24, [3, 1, 2, 0, 4]))[1], neg(14)),
        ("set-readonly", lambda e: (e.s(), e.m.call(24, [3, 1, 3, VAL, 4]))[1], neg(92)),
        ("set-len", lambda e: (e.s(), e.m.call(24, [3, 1, 2, VAL, 3]))[1], neg(22)),
        ("set-tcp-on-dgram", lambda e: (e.s(V4, 2), e.m.call(24, [3, 6, 1, VAL, 4]))[1], neg(92)),
        ("set-ip-on-v6", lambda e: (e.s(V6), e.m.call(24, [3, 0, 1, VAL, 4]))[1], neg(92)),
        ("set-reuseaddr", lambda e: (e.s(), e.m.call(24, [3, 1, 2, e.val(struct.pack("<i", 1)), 4]))[1], 0),
        ("set-nodelay", lambda e: (e.s(), e.m.call(24, [3, 6, 1, e.val(struct.pack("<i", 1)), 4]))[1], 0),
        ("set-linger", lambda e: (e.s(), e.m.call(24, [3, 1, 13, e.val(struct.pack("<ii", 1, 5)), 8]))[1], 0),
        ("set-linger-neg", lambda e: (e.s(), e.m.call(24, [3, 1, 13, e.val(struct.pack("<ii", 1, -5)), 8]))[1], "ND"),
        ("set-timeval-bad", lambda e: (e.s(), e.m.call(24, [3, 1, 20, e.val(struct.pack("<qq", 1, 1000000)), 16]))[1], "ND"),
        ("set-sndbuf", lambda e: (e.s(), e.m.call(24, [3, 1, 7, e.val(struct.pack("<i", 4096)), 4]))[1], "ND"),
        ("get-unknown", lambda e: (e.s(), e.m.call(25, [3, 1, 99, VAL, LEN]))[1], neg(92)),
        ("get-null-vallen", lambda e: (e.s(), e.m.call(25, [3, 1, 3, VAL, 0]))[1], neg(14)),
        ("get-null-val-cap", lambda e: (e.s(), e.mem.write(LEN, struct.pack("<I", 4)), e.m.call(25, [3, 1, 3, 0, LEN]))[2], neg(14)),
        ("get-null-val-cap0", lambda e: (e.s(), e.mem.write(LEN, struct.pack("<I", 0)), e.m.call(25, [3, 1, 3, 0, LEN]))[2], neg(14)),
        ("get-null-val-null-vallen", lambda e: (e.s(), e.m.call(25, [3, 1, 3, 0, 0]))[1], neg(14)),
        ("get-null-val-unknown-opt", lambda e: (e.s(), e.m.call(25, [3, 1, 99, 0, 0]))[1], neg(92)),
        ("get-cap0", lambda e: (e.s(), e.mem.write(VAL, b"\xAA" * 4), e.mem.write(LEN, struct.pack("<I", 0)), e.m.call(25, [3, 1, 3, VAL, LEN]),
                               e.mem.read(VAL, 4) + e.mem.read(LEN, 4))[4], b"\xAA" * 4 + struct.pack("<I", 4)),
        ("get-cap-big", lambda e: (e.s(), e.mem.write(LEN, struct.pack("<I", 100)), e.m.call(25, [3, 1, 4, VAL, LEN]), e.mem.read(LEN, 4))[3], struct.pack("<I", 4)),
        ("get-type", lambda e: (e.s(V4, 2), e.mem.write(LEN, struct.pack("<I", 4)), e.m.call(25, [3, 1, 3, VAL, LEN]), e.mem.read(VAL, 4) + e.mem.read(LEN, 4))[3],
         struct.pack("<ii", 2, 4)),
        ("get-error", lambda e: (e.s(), e.mem.write(LEN, struct.pack("<I", 4)), e.m.call(25, [3, 1, 4, VAL, LEN]), e.mem.read(VAL, 4))[3], bytes(4)),
        ("get-reuse-unset", lambda e: (e.s(), e.mem.write(LEN, struct.pack("<I", 8)), e.m.call(25, [3, 1, 2, VAL, LEN]), e.mem.read(LEN, 4))[3], struct.pack("<I", 4)),
        ("get-linger-unset", lambda e: (e.s(), e.mem.write(LEN, struct.pack("<I", 8)), e.m.call(25, [3, 1, 13, VAL, LEN]), e.mem.read(VAL, 8))[3], bytes(8)),
        ("get-timeo-unset", lambda e: (e.s(), e.mem.write(LEN, struct.pack("<I", 16)), e.m.call(25, [3, 1, 20, VAL, LEN]), e.mem.read(LEN, 4))[3], struct.pack("<I", 16)),
        ("get-cap-small", lambda e: (e.s(), e.mem.write(VAL, b"\xAA" * 4), e.mem.write(LEN, struct.pack("<I", 2)), e.m.call(25, [3, 1, 3, VAL, LEN]),
                                    e.mem.read(VAL, 4) + e.mem.read(LEN, 4))[4], b"\x01\x00\xAA\xAA" + struct.pack("<I", 4)),
        ("get-cap-small-result", lambda e: (e.s(), e.mem.write(LEN, struct.pack("<I", 2)), e.m.call(25, [3, 1, 3, VAL, LEN]))[2], 0),
        ("get-cap-small-linger", lambda e: (e.s(), e.mem.write(LEN, struct.pack("<I", 3)), e.m.call(25, [3, 1, 13, VAL, LEN]), e.mem.read(LEN, 4))[3], struct.pack("<I", 8)),
        ("get-after-set", lambda e: (e.s(), e.m.call(24, [3, 1, 2, e.val(struct.pack("<i", 1)), 4]), e.mem.write(LEN, struct.pack("<I", 4)), e.m.call(25, [3, 1, 2, VAL, LEN]))[3], "ND"),
        ("get-sndbuf", lambda e: (e.s(), e.mem.write(LEN, struct.pack("<I", 4)), e.m.call(25, [3, 1, 7, VAL, LEN]))[2], "ND"),
        ("get-acceptconn", lambda e: (e.s(), e.mem.write(LEN, struct.pack("<I", 4)), e.m.call(25, [3, 1, 30, VAL, LEN]))[2], "ND"),
        ("get-tcp-on-dgram", lambda e: (e.s(V4, 2), e.mem.write(LEN, struct.pack("<I", 4)), e.m.call(25, [3, 6, 1, VAL, LEN]))[2], "ND"),
    ])
    pf = lambda e, fds: [e.mem.write(FD + 8 * i, struct.pack("<iHH", f, 1, 0xFFFF)) for i, f in enumerate(fds)]
    table("POLL", [
        ("big", lambda e: e.m.call(26, [FD, 0x1000001, 0]), neg(22)), ("null", lambda e: e.m.call(26, [0, 1, 0]), neg(14)),
        ("nfds0", lambda e: e.m.call(26, [0, 0, 0]), 0), ("nfds0-wait", lambda e: e.m.call(26, [0, 0, 7]), (("sock", 7000000), 0)),
        ("forever", lambda e: e.m.call(26, [0, 0, 0xFFFFFFFF]), (("sock", None), 0)),
        ("nval", lambda e: (pf(e, [-1, 5, 7]), e.m.call(26, [FD, 3, 0]))[1], 2),
        ("nval-revents", lambda e: (pf(e, [-1, 5, 7]), e.m.call(26, [FD, 3, 0]), e.mem.read(FD + 6, 2) + e.mem.read(FD + 14, 2) + e.mem.read(FD + 22, 2))[2],
         struct.pack("<HHH", 0, 0x20, 0x20)),
        ("open-socket", lambda e: (e.s(), pf(e, [3]), e.m.call(26, [FD, 1, 0]))[2], 0),
        ("open-socket-revents", lambda e: (e.s(), pf(e, [3]), e.m.call(26, [FD, 1, 0]), e.mem.read(FD + 6, 2))[3], bytes(2)),
        ("open-socket-wait", lambda e: (e.s(), pf(e, [3]), e.m.call(26, [FD, 1, 9]))[2], (("sock", 9000000), 0)),
        ("open-socket-forever", lambda e: (e.s(), pf(e, [3]), e.m.call(26, [FD, 1, 0xFFFFFFFF]))[2], (("sock", None), 0)),
        ("open-socket-unknown-events", lambda e: (e.s(), e.mem.write(FD, struct.pack("<iHH", 3, 0x8000, 0xFFFF)), e.m.call(26, [FD, 1, 0]), e.mem.read(FD + 6, 2))[3], bytes(2)),
        ("open-and-nval", lambda e: (e.s(), pf(e, [3, 9]), e.m.call(26, [FD, 2, 50]), e.mem.read(FD + 6, 2) + e.mem.read(FD + 14, 2))[3],
         struct.pack("<HH", 0, 0x20)),
        ("open-and-nval-nowait", lambda e: (e.s(), pf(e, [3, 9]), e.m.call(26, [FD, 2, 50]))[2], 1),
        ("bound-socket", lambda e: (e.s(), e.m.call(18, [3, e.sa(V4, 8080, LO4)]), pf(e, [3]), e.m.call(26, [FD, 1, 0]))[3], 0),
        ("listening-socket", lambda e: (e.s(), e.m.call(18, [3, e.sa(V4, 8080, LO4)]), e.m.call(19, [3, 1]), pf(e, [3]), e.m.call(26, [FD, 1, 0]))[4], 0),
        ("peer-socket", lambda e: (e.s(V4, 2), e.m.call(21, [3, e.sa(V4, 9, LO4)]), pf(e, [3]), e.m.call(26, [FD, 1, 0]))[3], 0),
        ("closed-socket", lambda e: (e.s(), e.m.call(17, [3]), pf(e, [3]), e.m.call(26, [FD, 1, 0]))[3], 1),
    ])

    # R-SOCK-1 (journal/inbox/l4-verify-009.md): loopback traffic between the sockets of the one machine. Expected values are the rule's text.
    def pair(e, typ=1):
        """a listener (handle 3, 127.0.0.1:8080) and a bound client (handle 4, 127.0.0.1:9000)"""
        e.s(V4, typ)
        e.m.call(18, [3, e.sa(V4, 8080, LO4)])
        if typ == 1:
            e.m.call(19, [3, 4])
        e.s(V4, typ)
        e.m.call(18, [4, e.sa(V4, 9000, LO4, at=SA + 0x100)])
        return e

    def conn(e):
        pair(e)
        e.m.call(21, [4, e.sa(V4, 8080, LO4)])
        return e.m.call(20, [3, FD])   # the server side: handle 5

    def snd(e, h, data, flags=0, to=0):
        e.mem.write(BUF, data)
        return e.m.call(22, [h, BUF, len(data), flags, to])

    def rcv(e, h, n, flags=0, frm=0):
        r = e.m.call(23, [h, BUF + 0x100, n, flags, frm])
        return r if isinstance(r, tuple) else (r, e.mem.read(BUF + 0x100, r) if r < 1 << 31 else b"")

    def wake(e, r):
        """the call blocked (("sock", thunk, deadline), timeout result): run the machine's wake check, the thunk's value (None = still blocked)"""
        assert isinstance(r, tuple) and r[0][0] == "sock"
        return r[0][1]()

    table("R-SOCK-1 STREAM: connect, accept, send, recv, close, EOF, EPIPE", [
        ("connect-no-listener", lambda e: (e.s(), e.m.call(18, [3, e.sa(V4, 9000, LO4)]), e.m.call(21, [3, e.sa(V4, 8080, LO4)]))[2], neg(111)),   # no listener: ECONNREFUSED
        ("connect-completes", lambda e: (pair(e), e.m.call(21, [4, e.sa(V4, 8080, LO4)]))[1], 0),
        ("accept-takes-head", lambda e: conn(e), 5),
        ("accept-queue-empty-after", lambda e: (conn(e), e.m.call(31, [3, 0x800]), e.m.call(20, [3, 0]))[2], neg(11)),
        ("accept-fifo", lambda e: (pair(e), e.s(), e.m.call(18, [5, e.sa(V4, 9001, LO4, at=SA + 0x200)]), e.m.call(21, [4, e.sa(V4, 8080, LO4)]),
                                    e.m.call(21, [5, e.sa(V4, 8080, LO4)]), e.m.call(20, [3, FD]), e.mem.read(FD + 2, 2), e.m.call(20, [3, FD]),
                                    e.mem.read(FD + 2, 2))[6::2],
         (struct.pack("<H", 9000), struct.pack("<H", 9001))),   # R-SOCK-1: accept takes the queue head
        ("accept-peer-address", lambda e: (conn(e), e.mem.read(FD, 24)[:2] + e.mem.read(FD + 2, 2))[1], struct.pack("<HH", 2, 9000)),
        ("accept-blocking-woken", lambda e: (pair(e), (lambda r: (wake(e, r), e.m.call(21, [4, e.sa(V4, 8080, LO4)]), wake(e, r)))(e.m.call(20, [3, 0])))[1], (None, 0, 5)),
        ("send-recv", lambda e: (conn(e), snd(e, 4, b"hello"), rcv(e, 5, 16))[2], (5, b"hello")),
        ("send-result", lambda e: (conn(e), snd(e, 4, b"hello"))[1], 5),
        ("send-recv-partial", lambda e: (conn(e), snd(e, 4, b"hello"), rcv(e, 5, 2), rcv(e, 5, 16))[3], (3, b"llo")),
        ("recv-both-directions", lambda e: (conn(e), snd(e, 5, b"back"), rcv(e, 4, 16))[2], (4, b"back")),
        ("recv-order", lambda e: (conn(e), snd(e, 4, b"ab"), snd(e, 4, b"cd"), rcv(e, 5, 16))[3], (4, b"abcd")),
        ("recv-peek", lambda e: (conn(e), snd(e, 4, b"xy"), rcv(e, 5, 16, 2), rcv(e, 5, 16))[3], (2, b"xy")),
        ("recv-nonblock", lambda e: (conn(e), e.m.call(31, [5, 0x800]), rcv(e, 5, 4))[2], (neg(11), b"")),
        ("recv-blocks-woken", lambda e: (conn(e), (lambda r: (wake(e, r), snd(e, 4, b"hi"), wake(e, r)))(e.m.call(23, [5, BUF + 0x100, 8, 0, 0])))[1], (None, 2, 2)),
        ("recv-from-address", lambda e: (conn(e), snd(e, 4, b"z"), rcv(e, 5, 4, 0, FD), e.mem.read(FD, 4))[3], struct.pack("<HH", 2, 9000)),
        ("close-gives-eof", lambda e: (conn(e), e.m.call(17, [4]), rcv(e, 5, 4))[2], (0, b"")),
        ("close-eof-after-data", lambda e: (conn(e), snd(e, 4, b"q"), e.m.call(17, [4]), rcv(e, 5, 4), rcv(e, 5, 4))[3:], ((1, b"q"), (0, b""))),
        ("close-wakes-blocked-recv", lambda e: (conn(e), (lambda r: (e.m.call(17, [4]), wake(e, r)))(e.m.call(23, [5, BUF + 0x100, 8, 0, 0])))[1], (0, 0)),
        ("send-after-close-epipe", lambda e: (conn(e), e.m.call(17, [5]), snd(e, 4, b"x"))[2], neg(32)),
        ("send-unconnected", lambda e: (e.s(), snd(e, 3, b"x"))[1], neg(107)),   # POSIX.1-2017 send(): ENOTCONN
        ("close-unaccepted-gives-eof", lambda e: (pair(e), e.m.call(21, [4, e.sa(V4, 8080, LO4)]), e.m.call(17, [3]), rcv(e, 4, 4))[3], (0, b"")),
        ("loopback-any-listener", lambda e: (e.s(), e.m.call(18, [3, e.sa(V4, 8080, bytes(4))]), e.m.call(19, [3, 4]), e.s(), e.m.call(18, [4, e.sa(V4, 9000, LO4)]),
                                              e.m.call(21, [4, e.sa(V4, 8080, b"\x7f\0\0\5", at=SA + 0x100)]))[5], 0),   # 127/8 is local; the listener is bound to 'any'
    ])
    table("R-SOCK-1 DGRAM: queues", [
        ("sendto-recvfrom", lambda e: (pair(e, 2), snd(e, 4, b"dg", 0, e.sa(V4, 8080, LO4)), rcv(e, 3, 16))[2], (2, b"dg")),
        ("sendto-result", lambda e: (pair(e, 2), snd(e, 4, b"dg", 0, e.sa(V4, 8080, LO4)))[1], 2),
        ("datagram-boundaries", lambda e: (pair(e, 2), snd(e, 4, b"a", 0, e.sa(V4, 8080, LO4)), snd(e, 4, b"bc", 0, e.sa(V4, 8080, LO4)), rcv(e, 3, 16), rcv(e, 3, 16))[3:],
         ((1, b"a"), (2, b"bc"))),
        ("datagram-truncated", lambda e: (pair(e, 2), snd(e, 4, b"abcd", 0, e.sa(V4, 8080, LO4)), rcv(e, 3, 2))[2], (2, b"ab")),
        ("datagram-rest-discarded", lambda e: (pair(e, 2), snd(e, 4, b"abcd", 0, e.sa(V4, 8080, LO4)), rcv(e, 3, 2), e.m.call(31, [3, 0x800]), rcv(e, 3, 2))[4], (neg(11), b"")),
        ("recvfrom-source", lambda e: (pair(e, 2), snd(e, 4, b"s", 0, e.sa(V4, 8080, LO4)), rcv(e, 3, 4, 0, FD), e.mem.read(FD, 4))[3], struct.pack("<HH", 2, 9000)),
        ("sendto-no-bound-socket-dropped", lambda e: (pair(e, 2), snd(e, 4, b"x", 0, e.sa(V4, 7777, LO4)))[1], 1),   # UDP has no refusal without ICMP
        ("dropped-datagram-not-queued", lambda e: (pair(e, 2), snd(e, 4, b"x", 0, e.sa(V4, 7777, LO4)), e.m.call(31, [3, 0x800]), rcv(e, 3, 4))[3], (neg(11), b"")),
        ("sendto-connected-peer", lambda e: (pair(e, 2), e.m.call(21, [4, e.sa(V4, 8080, LO4)]), snd(e, 4, b"p"), rcv(e, 3, 4))[3], (1, b"p")),
        ("recv-blocks-woken-by-sendto", lambda e: (pair(e, 2), (lambda r: (wake(e, r), snd(e, 4, b"w", 0, e.sa(V4, 8080, LO4)), wake(e, r)))(e.m.call(23, [3, BUF + 0x100, 8, 0, 0])))[1], (None, 1, 1)),
        ("recv-peek-keeps", lambda e: (pair(e, 2), snd(e, 4, b"k", 0, e.sa(V4, 8080, LO4)), rcv(e, 3, 4, 2), rcv(e, 3, 4))[3], (1, b"k")),
        ("connected-socket-filters-source", lambda e: (pair(e, 2), e.s(V4, 2), e.m.call(18, [5, e.sa(V4, 9001, LO4, at=SA + 0x200)]), e.m.call(21, [3, e.sa(V4, 9000, LO4)]),
                                                         snd(e, 5, b"o", 0, e.sa(V4, 8080, LO4)), e.m.call(31, [3, 0x800]), rcv(e, 3, 4))[6], (neg(11), b"")),
    ])
    table("R-SOCK-1 poll readiness", [
        ("pollin-listener-with-connection", lambda e: (pair(e), e.m.call(21, [4, e.sa(V4, 8080, LO4)]), pf(e, [3]), e.m.call(26, [FD, 1, 0]), e.mem.read(FD + 6, 2))[4], struct.pack("<H", 1)),
        ("pollin-listener-empty", lambda e: (pair(e), pf(e, [3]), e.m.call(26, [FD, 1, 0]))[2], 0),
        ("pollin-stream-data", lambda e: (conn(e), snd(e, 4, b"d"), pf(e, [5]), e.m.call(26, [FD, 1, 0]))[3], 1),
        ("pollin-stream-eof", lambda e: (conn(e), e.m.call(17, [4]), pf(e, [5]), e.m.call(26, [FD, 1, 0]))[3], 1),
        ("pollin-stream-empty", lambda e: (conn(e), pf(e, [5]), e.m.call(26, [FD, 1, 0]))[2], 0),
        ("pollin-dgram-queue", lambda e: (pair(e, 2), snd(e, 4, b"d", 0, e.sa(V4, 8080, LO4)), pf(e, [3]), e.m.call(26, [FD, 1, 0]))[3], 1),
        ("pollout-connected-stream", lambda e: (conn(e), e.mem.write(FD, struct.pack("<iHH", 4, 4, 0xFFFF)), e.m.call(26, [FD, 1, 0]), e.mem.read(FD + 6, 2))[3], struct.pack("<H", 4)),
        ("pollout-unconnected-stream", lambda e: (e.s(), e.mem.write(FD, struct.pack("<iHH", 3, 4, 0xFFFF)), e.m.call(26, [FD, 1, 0]))[2], 0),
        ("poll-woken-by-peer", lambda e: (conn(e), pf(e, [5]), (lambda r: (wake(e, r), snd(e, 4, b"d"), wake(e, r)))(e.m.call(26, [FD, 1, 0xFFFFFFFF])))[2], (None, 1, 1)),
    ])
    print("%d cases, %d failed" % (N[0], len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
