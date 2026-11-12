"""The socket services (module 11, fn 16..26, 30, 31, hostapi.h + sockwire.h) of the boot stage machine, for a host that has NO network interface.

Model (owner decision "The declared machine", CLAUDE.md: the machine has no network connection and every network call returns the
documented error of that state, never a fake success): the machine's host has only the loopback addresses; no packet ever arrives and nothing can connect. A socket is an entry of a
table of open handles; every result is derived from the header text and host_sock.inc (the reference host: its argument checks, in its
order) plus POSIX where the header states "an OS result" for a situation that exists without traffic. A (service, case) whose result
needs real traffic, or an OS's own choice, is NOT answered: the model raises NotDetermined and the machine ends the run as
"ext-module-missing" naming the service. A guest address that is not readable / writable memory raises BadGuestMemory (a violated
precondition of the header; the machine ends the run as a host-call fault). Never an invented value.

Stated assumptions (each is a fact of the stage's model, not of the header):
  A1 handle numbers (a rule of the model, deterministic): a new handle is the lowest number >= 3 that is not open (0..2 are stdio); a
     number is free again after its close. This is the descriptor rule of POSIX.1-2017 2.14 "File Descriptor Allocation" (the lowest
     number not open), which the bsd service's descriptors follow. Nothing above the host may depend on the number: the header says only
     that a handle is the descriptor number the service returned, and on the Windows side a SOCKET is an opaque handle whose only
     special value is INVALID_SOCKET (Winsock "Socket Data Type"), so a program correct on Windows reads no meaning from it. SOCKET
     never fails for lack of room.
  A2 addresses (facts, cited): loopback is 127.0.0.0/8 (RFC 1122 3.2.1.3 (g)) and ::1 (RFC 4291 2.5.3); "any" is 0.0.0.0 (RFC 1122
     3.2.1.3 (a)) and :: (RFC 4291 2.5.2); the declared machine has no network connection (CLAUDE.md "The declared machine"), so it has
     no interface but loopback and every other unicast address is not local.
  A4 EPROTONOSUPPORT: STREAM takes protocol 0 or 6, DGRAM 0 or 17; any other protocol is EPROTONOSUPPORT except those a real OS may
     implement for the type (1, 58, 132, 136, 262, or outside 0..255), which are not answered.

The header (hostapi.h at a1923f65) and host_sock.inc agree on every case below; where a result is "the platform's answer" and depends on
traffic or on the OS's own choice, the case is NOT answered.

R-SOCK-1 (journal/inbox/l4-verify-009.md, loopback is part of the declared machine): traffic between sockets of the one machine is modelled.
STREAM connect to a bound listener enters its accept queue (no listener: ECONNREFUSED), accept takes the head, send / recv move bytes in order,
close gives the peer EOF after the buffered bytes, send after the peer closed is EPIPE; DGRAM sendto appends to the queue of the socket bound to
the address (none: dropped, sendto succeeds); poll readiness follows from the queues; a blocking call waits in virtual time and is woken by the
peer: the model returns (("sock", thunk, deadline), timeout result) and the machine runs thunk() when it picks a thread (None = still blocked).
R-SOCK-2 GAP (reported in journal/inbox/l4-verify-w3-001.md): the cases that are the OS's own choice (below, "NOT answered") need the FreeBSD
release of the console's bsd service. switchbrew "Sockets services" names "the FreeBSD kernel's socket stack" with no release, so no source can
be pinned and no answer is invented: those sites still raise NotDetermined.

Served per service (everything else listed here as NOT answered):
  SOCKET     the domain is checked before the type: unknown domain (not 0/2/10, or wider than int32) EAFNOSUPPORT; unknown type EINVAL; STREAM/DGRAM (domain 2 or 10) -> a handle
             (A1, A4). NOT answered: domain AF_UNSPEC, type RAW, the protocols of A4's exception.
  CLOSE      an open handle -> 0 and gone; not open -> EBADF.
  every handle-first service: a handle that is not open (or wider than int32) -> EBADF before anything else.
  BIND       null address EFAULT; family not 0/2/10 EAFNOSUPPORT; a local address (A2) or any of the socket's family -> 0 (recorded);
             a unicast address that is not local EADDRNOTAVAIL; a socket already bound or listening EINVAL (POSIX).
             NOT answered: family AF_UNSPEC or another than the socket's, a v4-mapped / multicast / broadcast / link-local address,
             flowinfo or scope_id != 0, a port 1..1023, a port also in a bound socket's overlapping address.
  LISTEN     DGRAM EOPNOTSUPP; a bound STREAM -> 0. NOT answered: an unbound STREAM.
  ACCEPT     DGRAM EOPNOTSUPP; not listening EINVAL; listening: non-blocking EAGAIN, blocking waits for ever (nothing can arrive).
  CONNECT    (the host checks only null and family, then the system service) null address EFAULT; family not 0/2/10 EAFNOSUPPORT; an address that is not local (A2) ENETUNREACH; a local address on a
             DGRAM socket (port != 0) -> 0, the peer recorded. NOT answered: a local address on a STREAM socket (loopback traffic), AF_UNSPEC
             or another family than the socket's, multicast / broadcast / link-local / v4-mapped addresses, flowinfo or scope_id != 0,
             a listening socket, address any, port 0.
  SENDTO     len > 0x7FFFFFFF EINVAL; an unknown flag bit EOPNOTSUPP; a bad address family EAFNOSUPPORT; DGRAM to a non-local address
             ENETUNREACH; DGRAM without address and without peer EDESTADDRREQ. NOT answered: every STREAM socket, MSG_OOB,
             a local destination or a peer (loopback traffic), other families / exotic addresses as for CONNECT.
  RECVFROM   len / flags as SENDTO; STREAM ENOTCONN; DGRAM with nothing to receive: non-blocking or MSG_DONTWAIT EAGAIN, blocking waits for ever,
             or SO_RCVTIMEO (set earlier, > 0) in virtual time and then EAGAIN. NOT answered: MSG_OOB.
  SETSOCKOPT unknown (level, option) / read-only (SO_TYPE, SO_ERROR) ENOPROTOOPT, null value EFAULT, vallen != the shape's size EINVAL,
             TCP level on a non-STREAM, IP level on a non-INET, IPV6 level on a non-INET6 socket ENOPROTOOPT; then, for the boolean options
             (SO_DEBUG REUSEADDR DONTROUTE BROADCAST KEEPALIVE OOBINLINE REUSEPORT, TCP_NODELAY), SO_LINGER (onoff, linger_s >= 0) and
             SO_RCVTIMEO / SO_SNDTIMEO (sec >= 0, 0 <= usec < 1000000): 0 and recorded. NOT answered: every other option (its accepted
             range is the OS's), a linger / timeval outside those ranges.
  GETSOCKOPT order: handle EBADF, unknown (level, option) ENOPROTOOPT, null value EFAULT (also with capacity 0), null vallen EFAULT; then,
             for an option never set on this socket: SO_TYPE, SO_ERROR 0, the boolean options 0, SO_LINGER {0, 0}, the timeouts {0, 0}:
             min(capacity, size) bytes are written and *vallen is ALWAYS the full size (INT/TYPE/ERRNO 4, LINGER 8, TIMEVAL 16), result 0.
             NOT answered: an option that was set (the OS stores its own form), every other option, SO_ACCEPTCONN, an option of another
             protocol level than the socket's.
  GETFL / SETFL  RDWR | NONBLOCK as the header says; SETFL sets NONBLOCK from that bit, other bits are ignored, result 0 (always answered).
  POLL       nfds > 0x1000000 EINVAL, nfds > 0 with a null array EFAULT; a negative fd is ignored (revents 0); an fd that is not an open
             handle gets POLLNVAL, counts, and makes the call not wait (timeout 0); event bits outside the wire list are dropped; an open
             socket with no local peer has no traffic ever (no network): revents 0, and after the timeout (virtual time; for ever for -1)
             the result is 0. NOT answered: an open socket that has a local peer or could get one: a recorded peer, a bound or a listening
             socket (a local sender could produce traffic; the model never produces it, so the case is left to the OS).
"""
import ipaddress
import struct

U64 = (1 << 64) - 1
# sockwire.h numbers (tests/test_bootmachine.py compares them with the fixture header)
WE_BADF, WE_AGAIN, WE_FAULT, WE_INVAL, WE_OVERFLOW = 9, 11, 14, 22, 75
WE_DESTADDRREQ, WE_NOPROTOOPT, WE_PROTONOSUPPORT, WE_OPNOTSUPP, WE_AFNOSUPPORT = 89, 92, 93, 95, 97
WE_ADDRNOTAVAIL, WE_NETUNREACH, WE_NOTCONN = 99, 101, 107
WE_PIPE, WE_CONNREFUSED = 32, 111
MSG_PEEK, MSG_WAITALL = 0x2, 0x100
POLL_IN, POLL_OUT, POLL_RDNORM, POLL_WRNORM, POLL_KNOWN = 0x1, 0x4, 0x40, 0x100, 0x1FF
WIRE_AF, WIRE_SOCKTYPE = (0, 2, 10), (1, 2, 3)   # FNVNX_WIRE_AF_LIST, FNVNX_WIRE_SOCKTYPE_LIST
AF_UNSPEC, AF_INET, AF_INET6 = WIRE_AF
STREAM, DGRAM, RAW = WIRE_SOCKTYPE
WIRE_AI_MASK = 0x1 | 0x2 | 0x4 | 0x8 | 0x10 | 0x20 | 0x400   # FNVNX_WIRE_AI_LIST
WEAI_BADFLAGS, WEAI_NONAME, WEAI_AGAIN, WEAI_FAMILY, WEAI_SOCKTYPE = 2001, 2002, 2003, 2005, 2006
WPOLLNVAL, POLL_MAX = 0x20, 0x1000000
WO_RDWR, WO_NONBLOCK = 2, 0x800
MSG_KNOWN, MSG_OOB, MSG_DONTWAIT = 0x1 | 0x2 | 0x4 | 0x100 | 0x40 | 0x4000, 0x1, 0x40
SOCKADDR, FIRST_HANDLE = 32, 3   # sizeof(FnvnxWireSockaddr)
# option shapes and their sizes (sockwire.h); level 1 = SOL_SOCKET, the others IANA protocol numbers
INT, LINGER, TIMEVAL, TYPE, ERRNO = 0, 1, 2, 3, 4
SIZE = {INT: 4, LINGER: 8, TIMEVAL: 16, TYPE: 4, ERRNO: 4}
OPTS = {(1, 1): INT, (1, 2): INT, (1, 3): TYPE, (1, 4): ERRNO, (1, 5): INT, (1, 6): INT, (1, 7): INT, (1, 8): INT, (1, 9): INT,
        (1, 10): INT, (1, 13): LINGER, (1, 15): INT, (1, 18): INT, (1, 19): INT, (1, 20): TIMEVAL, (1, 21): TIMEVAL, (1, 30): INT,
        (6, 1): INT, (0, 1): INT, (0, 2): INT, (0, 33): INT, (0, 34): INT, (41, 16): INT, (41, 18): INT, (41, 19): INT, (41, 26): INT}
BOOLEAN = {(1, 1), (1, 2), (1, 5), (1, 6), (1, 9), (1, 10), (1, 15), (6, 1)}
LEVEL_FAMILY = {0: AF_INET, 41: AF_INET6}
LEVEL_TYPE = {6: STREAM}
UNDECIDED_PROTOCOLS = {1, 58, 132, 136, 262}


class NotDetermined(Exception):
    """the result of this (service, case) needs traffic or an OS's own choice: the machine ends the run"""


class BadGuestMemory(Exception):
    """a guest pointer the header requires to be readable / writable memory is not"""


def neg(e):
    return -e & U64


class Sock:
    def __init__(self, dom, typ):
        self.dom, self.typ = dom, typ
        self.nonblock = False
        self.bound = None       # (family, port, 16 address bytes)
        self.listening = False
        self.peer = None        # (family, port, 16 address bytes)
        self.opts = {}          # (level, option) -> value set by the guest
        self.backlog = 0
        self.acceptq = []       # R-SOCK-1: connections of a listener not yet accepted (server-side Socks)
        self.link = None        # STREAM: the other end of the connection (kept after the peer closed)
        self.peer_closed = False   # STREAM: the other end closed (EOF after the buffered bytes, EPIPE on send)
        self.rbuf = bytearray() # STREAM: bytes sent by the peer, not yet received
        self.dq = []            # DGRAM: datagrams (source or None, bytes) not yet received


def classify(fam, addr):
    """'loop' | 'any' | 'other' (a unicast address that is no local address) | 'exotic' (not decided) for a wire address"""
    if fam == AF_INET:
        a = addr[:4]
        if a == b"\0\0\0\0":
            return "any"
        if a[0] == 127:
            return "loop"
        return "exotic" if a[0] >= 224 or a[0] == 0 or a[:2] == b"\xa9\xfe" else "other"
    ip = ipaddress.IPv6Address(bytes(addr))
    if ip == ipaddress.IPv6Address("::"):
        return "any"
    if ip == ipaddress.IPv6Address("::1"):
        return "loop"
    return "exotic" if ip.ipv4_mapped or ip.is_multicast or ip.is_link_local or ip.is_site_local or ip.packed[:12] == bytes(12) else "other"


def wire_addr(fam, port, addr, flow=0, scope=0):
    return struct.pack("<HH16sIII", fam, port, bytes(addr), flow, scope, 0)


class SockModel:
    """mem: read(addr, n), write(addr, data), can_read(lo, hi), can_write(lo, hi); now_ns(): the virtual clock"""

    def __init__(self, mem, now_ns):
        self.mem, self.now_ns = mem, now_ns
        self.socks = {}

    # -- guest memory
    def _read(self, ptr, n):
        if not self.mem.can_read(ptr, ptr + n):
            raise BadGuestMemory("%#x..+%d is not readable guest memory" % (ptr, n))
        return self.mem.read(ptr, n)

    def _write(self, ptr, data):
        if not self.mem.can_write(ptr, ptr + len(data)):
            raise BadGuestMemory("%#x..+%d is not writable guest memory" % (ptr, len(data)))
        self.mem.write(ptr, data)

    def _sockaddr(self, ptr):
        fam, port, addr, flow, scope, _rsv = struct.unpack("<HH16sIII", self._read(ptr, SOCKADDR))
        return fam, port, addr, flow, scope

    # -- the dispatcher: fn 16..26, 30, 31, nargs already equal to the service's count
    def call(self, fn, a):
        if fn == 26:
            return self._poll(a)
        if fn == 16:
            return self._socket(a)
        s = self.socks.get(a[0])
        if s is None:
            return neg(WE_BADF)
        return getattr(self, "_" + {17: "close", 18: "bind", 19: "listen", 20: "accept", 21: "connect", 22: "sendto", 23: "recvfrom",
                                    24: "setsockopt", 25: "getsockopt", 30: "getfl", 31: "setfl"}[fn])(a, s)

    def _socket(self, a):
        s64 = lambda v: v - (1 << 64) if v >> 63 else v
        if s64(a[0]) not in WIRE_AF:
            return neg(WE_AFNOSUPPORT)
        if s64(a[1]) not in WIRE_SOCKTYPE:
            return neg(WE_INVAL)
        dom, typ, proto = s64(a[0]), s64(a[1]), (a[2] & 0xFFFFFFFF) - (1 << 32) if a[2] & 0x80000000 else a[2] & 0xFFFFFFFF
        if dom == AF_UNSPEC or typ == RAW or proto in UNDECIDED_PROTOCOLS or not 0 <= proto <= 255:
            raise NotDetermined("socket domain %d type %d protocol %d" % (dom, typ, proto))
        if proto not in (0, 6 if typ == STREAM else 17):
            return neg(WE_PROTONOSUPPORT)
        h = FIRST_HANDLE
        while h in self.socks:
            h += 1
        self.socks[h] = Sock(dom, typ)
        return h

    def _close(self, a, s):
        """R-SOCK-1: close gives the peer EOF; the connections a listener never accepted are closed as well"""
        del self.socks[a[0]]
        if s.link is not None:
            s.link.peer_closed = True
        for c in s.acceptq:
            c.link.peer_closed = True
        return 0

    @staticmethod
    def _matches(o, fam, port, addr):
        """does the bound socket o receive what is addressed to (fam, port, addr)? (R-SOCK-1: the address is its own or o is bound to 'any')"""
        return bool(o.bound) and o.bound[0] == fam and o.bound[1] == port and (classify(fam, o.bound[2]) == "any" or o.bound[2] == addr)

    def _bind(self, a, s):
        if not a[1]:
            return neg(WE_FAULT)
        fam, port, addr, flow, scope = self._sockaddr(a[1])
        if fam not in WIRE_AF:
            return neg(WE_AFNOSUPPORT)
        if fam != s.dom or flow or scope or 0 < port < 1024:
            raise NotDetermined("bind: family %d port %d flowinfo/scope %d/%d on a family %d socket" % (fam, port, flow, scope, s.dom))
        kind, addr = classify(fam, addr), bytes(addr)[:4 if fam == AF_INET else 16].ljust(16, b"\0")
        if kind == "exotic":
            raise NotDetermined("bind: an address of no decided class")
        if kind == "other":
            return neg(WE_ADDRNOTAVAIL)
        if s.bound or s.listening:
            return neg(WE_INVAL)
        if port:
            for o in self.socks.values():
                if o is not s and o.typ == s.typ and o.bound and o.bound[1] == port and (
                        kind == "any" or classify(*o.bound[::2]) == "any" or o.bound[2] == addr):
                    raise NotDetermined("bind: port %d is bound by another socket of this model" % port)
        s.bound = (fam, port, addr)
        return 0

    def _listen(self, a, s):
        if s.typ == DGRAM:
            return neg(WE_OPNOTSUPP)
        if not s.bound:
            raise NotDetermined("listen on an unbound STREAM socket")
        s.listening = True
        s.backlog = a[1] & 0xFFFFFFFF
        return 0

    def _take(self, a, s):
        """accept the head of the queue: a new handle (the lowest free number, A1), or None"""
        if not s.acceptq:
            return None
        c = s.acceptq.pop(0)
        h = FIRST_HANDLE
        while h in self.socks:
            h += 1
        self.socks[h] = c
        if a[1]:
            self._write(a[1], wire_addr(*c.peer))
        return h

    def _accept(self, a, s):
        if s.typ == DGRAM:
            return neg(WE_OPNOTSUPP)
        if not s.listening:
            return neg(WE_INVAL)
        h = self._take(a, s)
        if h is not None:
            return h
        if s.nonblock:
            return neg(WE_AGAIN)
        return (("sock", lambda: self._take(a, s), None), neg(WE_AGAIN))   # woken by the peer's connect

    def _dest(self, ptr, s, what):
        """(family, port, addr, kind) of a destination, or an errno result"""
        fam, port, addr, flow, scope = self._sockaddr(ptr)
        if fam not in WIRE_AF:
            return neg(WE_AFNOSUPPORT)
        if fam != s.dom or flow or scope:
            raise NotDetermined("%s: family %d flowinfo/scope %d/%d on a family %d socket" % (what, fam, flow, scope, s.dom))
        return fam, port, bytes(addr)[:4 if fam == AF_INET else 16].ljust(16, b"\0"), classify(fam, addr)

    def _connect(self, a, s):
        if not a[1]:
            return neg(WE_FAULT)
        d = self._dest(a[1], s, "connect")
        if isinstance(d, int):
            return d
        if s.listening:
            raise NotDetermined("connect on a listening socket")
        fam, port, addr, kind = d
        if kind == "exotic":
            raise NotDetermined("connect: an address of no decided class")
        if kind == "other":
            return neg(WE_NETUNREACH)
        if kind == "any" or not port:
            raise NotDetermined("connect: the OS's reading of 'any' / port 0")
        if s.typ == DGRAM:
            s.peer = (fam, port, addr)
            return 0
        if s.link is not None or s.nonblock:
            raise NotDetermined("connect on a connected socket, or a non-blocking connect (the bsd service's result)")
        if s.bound is None:
            raise NotDetermined("connect: the implicit bind takes an ephemeral port (R-SOCK-2)")
        lst = next((o for o in self.socks.values() if o.listening and o.typ == STREAM and self._matches(o, fam, port, addr)), None)
        if lst is None:
            return neg(WE_CONNREFUSED)   # R-SOCK-1: no listener bound to it
        if len(lst.acceptq) >= lst.backlog:
            raise NotDetermined("connect: the listener's accept queue is full (backlog; R-SOCK-2)")
        c = Sock(lst.dom, STREAM)
        c.bound, c.peer, c.link, s.link, s.peer = (fam, port, addr), s.bound, s, c, (fam, port, addr)
        lst.acceptq.append(c)
        return 0

    def _flags_len(self, a):
        if a[2] > 0x7FFFFFFF:
            return neg(WE_INVAL)
        if a[3] > 0xFFFFFFFF or a[3] & ~MSG_KNOWN:
            return neg(WE_OPNOTSUPP)
        return None

    def _sendto(self, a, s):
        r = self._flags_len(a)
        if r is not None:
            return r
        d = None
        if a[4]:
            d = self._dest(a[4], s, "sendto")
            if isinstance(d, int):
                return d
        if a[3] & MSG_OOB:
            raise NotDetermined("sendto with MSG_OOB")
        if s.typ == STREAM:
            if d is not None:
                raise NotDetermined("sendto with an address on a STREAM socket")
            if s.link is None:
                return neg(WE_NOTCONN)   # POSIX.1-2017 send(): ENOTCONN, the socket is not connected
            if s.peer_closed:
                return neg(WE_PIPE)      # R-SOCK-1: send after the peer closed
            data = self._read(a[1], a[2]) if a[2] else b""
            s.link.rbuf += data          # the buffer's capacity (SO_RCVBUF) is R-SOCK-2
            return len(data)
        if d is None:
            if s.peer is None:
                return neg(WE_DESTADDRREQ)
            d = (*s.peer, classify(s.peer[0], s.peer[2]))
        if d[3] == "exotic":
            raise NotDetermined("sendto: an address of no decided class")
        if d[3] == "other":
            return neg(WE_NETUNREACH)
        fam, port, addr, kind = d
        if kind == "any" or not port:
            raise NotDetermined("sendto: the OS's reading of 'any' / port 0")
        if s.bound is None:
            raise NotDetermined("sendto: the implicit bind takes an ephemeral port (R-SOCK-2)")
        data = self._read(a[1], a[2]) if a[2] else b""
        src = (s.bound[0], s.bound[1], addr if classify(*s.bound[::2]) == "any" else s.bound[2])
        for o in self.socks.values():   # R-SOCK-1: appended to the queue of the socket bound to the address, else dropped (sendto succeeds)
            if o.typ == DGRAM and self._matches(o, fam, port, addr) and (o.peer is None or o.peer[1:] == src[1:]):
                o.dq.append((src, bytes(data)))   # the receive buffer's capacity is R-SOCK-2
        return len(data)

    def _recv_now(self, a, s):
        """the result of a receive that can complete now, or None (it has to wait)"""
        peek = a[3] & MSG_PEEK
        if s.typ == STREAM:
            if not s.rbuf and not s.peer_closed:
                return None if a[2] else 0
            n = min(a[2], len(s.rbuf))
            data = bytes(s.rbuf[:n])
            if not peek:
                del s.rbuf[:n]
            src = s.peer
        else:
            if not s.dq:
                return None
            src, full = s.dq[0]
            data = full[:a[2]]
            if not peek:
                s.dq.pop(0)
        if data:
            self._write(a[1], data)
        if a[4]:
            self._write(a[4], wire_addr(*src) if src else wire_addr(AF_UNSPEC, 0, bytes(16)))
        return len(data)

    def _recvfrom(self, a, s):
        r = self._flags_len(a)
        if r is not None:
            return r
        if a[3] & MSG_OOB:
            raise NotDetermined("recvfrom with MSG_OOB")
        if a[3] & MSG_WAITALL:
            raise NotDetermined("recvfrom with MSG_WAITALL")
        if s.typ == STREAM and s.link is None:
            return neg(WE_NOTCONN)
        r = self._recv_now(a, s)
        if r is not None:
            return r
        if s.nonblock or a[3] & MSG_DONTWAIT:
            return neg(WE_AGAIN)
        tmo = s.opts.get((1, 20))
        return (("sock", lambda: self._recv_now(a, s), None if not tmo else self.now_ns() + tmo), neg(WE_AGAIN))

    def _opt(self, a, s):
        """(key, shape) of the option, or an errno result"""
        key = (a[1] & 0xFFFFFFFF, a[2] & 0xFFFFFFFF)
        if key not in OPTS or a[1] > 0xFFFFFFFF or a[2] > 0xFFFFFFFF:
            return neg(WE_NOPROTOOPT)
        return key, OPTS[key]

    def _setsockopt(self, a, s):
        o = self._opt(a, s)
        if isinstance(o, int):
            return o
        key, shape = o
        if not a[3]:
            return neg(WE_FAULT)
        if shape in (TYPE, ERRNO):
            return neg(WE_NOPROTOOPT)
        if a[4] != SIZE[shape]:
            return neg(WE_INVAL)
        if LEVEL_TYPE.get(key[0], s.typ) != s.typ or LEVEL_FAMILY.get(key[0], s.dom) != s.dom:
            return neg(WE_NOPROTOOPT)
        raw = self._read(a[3], SIZE[shape])
        if shape == INT and key in BOOLEAN:
            val = 1
        elif shape == LINGER and struct.unpack("<ii", raw)[1] >= 0:
            val = 1
        elif shape == TIMEVAL and (lambda t: t[0] >= 0 and 0 <= t[1] < 1000000)(struct.unpack("<qq", raw)):
            sec, usec = struct.unpack("<qq", raw)
            val = (sec * 1000000 + usec) * 1000
        else:
            raise NotDetermined("setsockopt level %d option %d: the OS's accepted range" % key)
        s.opts[key] = val
        return 0

    def _getsockopt(self, a, s):
        o = self._opt(a, s)
        if isinstance(o, int):
            return o
        key, shape = o
        if not a[3] or not a[4]:
            return neg(WE_FAULT)
        if key in s.opts or not (shape in (TYPE, ERRNO, LINGER, TIMEVAL) or key in BOOLEAN):
            raise NotDetermined("getsockopt level %d option %d: the OS's own value" % key)
        if LEVEL_TYPE.get(key[0], s.typ) != s.typ or LEVEL_FAMILY.get(key[0], s.dom) != s.dom:
            raise NotDetermined("getsockopt level %d option %d on a socket of another protocol" % key)
        val = struct.pack("<i", s.typ) if shape == TYPE else bytes(SIZE[shape])
        cap = struct.unpack("<I", self._read(a[4], 4))[0]
        if cap:
            self._write(a[3], val[:cap])
        self._write(a[4], struct.pack("<I", len(val)))
        return 0

    def _getfl(self, a, s):
        return WO_RDWR | (WO_NONBLOCK if s.nonblock else 0)

    def _setfl(self, a, s):
        s.nonblock = bool(a[1] & WO_NONBLOCK)
        return 0

    def _poll(self, a):
        fds, nfds = a[0], a[1]
        tmo = (a[2] & 0xFFFFFFFF) - (1 << 32) if a[2] & 0x80000000 else a[2] & 0xFFFFFFFF
        if nfds > POLL_MAX:
            return neg(WE_INVAL)
        if nfds and not fds:
            return neg(WE_FAULT)
        if nfds and not (self.mem.can_read(fds, fds + 8 * nfds) and self.mem.can_write(fds, fds + 8 * nfds)):
            raise BadGuestMemory("sock_poll: fds %#x not readable and writable guest memory" % fds)
        fdv = [struct.unpack("<i", self.mem.read(fds + 8 * i, 4))[0] for i in range(nfds)]
        nval = sum(f >= 0 and f not in self.socks for f in fdv)
        r = self._poll_now(fds, fdv)
        if r or nval or tmo == 0:   # an entry that is ready already ends the wait
            return r
        return (("sock", lambda: self._poll_now(fds, fdv) or None, None if tmo < 0 else self.now_ns() + tmo * 1_000_000), 0)

    def _ready(self, o):
        """R-SOCK-1: readiness from the queues and buffers: readable = a connection to accept, bytes or EOF of a stream, a datagram;
        writable = a connected stream or a datagram socket (the buffers never fill, R-SOCK-2 gives their capacity)"""
        rd = bool(o.acceptq) if o.listening else bool(o.rbuf or o.peer_closed) if o.typ == STREAM else bool(o.dq)
        wr = o.typ == DGRAM or (o.link is not None and not o.listening)
        return (POLL_IN | POLL_RDNORM if rd else 0) | (POLL_OUT | POLL_WRNORM if wr else 0)

    def _poll_now(self, fds, fdv):
        """writes every revents, returns the number of entries with revents != 0"""
        n = 0
        for i, fd in enumerate(fdv):   # a handle that is not open: POLLNVAL; a negative fd is ignored
            o = self.socks.get(fd) if fd >= 0 else None
            ev = struct.unpack("<H", self.mem.read(fds + 8 * i + 4, 2))[0] & POLL_KNOWN
            rev = WPOLLNVAL if fd >= 0 and o is None else self._ready(o) & ev if o is not None else 0
            self.mem.write(fds + 8 * i + 6, struct.pack("<H", rev))
            n += rev != 0
        return n
