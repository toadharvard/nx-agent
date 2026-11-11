"""The socket services (module 11, fn 16..33, hostapi.h + sockwire.h) of the boot stage machine, for a host that has NO network interface.

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
  A3 getaddrinfo with no network: a numeric node is parsed (strict dotted quad, or an RFC 4291 text without a scope) and a null hints
     argument is a hints record of zeros (POSIX); the list of a numeric node is one record per socket type STREAM/TCP 6, DGRAM/UDP 17,
     RAW/0 (the type given in the hints keeps one; the protocol given keeps those with that protocol), in this order, ai_flags = hints flags.
  A4 EPROTONOSUPPORT: STREAM takes protocol 0 or 6, DGRAM 0 or 17; any other protocol is EPROTONOSUPPORT except those a real OS may
     implement for the type (1, 58, 132, 136, 262, or outside 0..255), which are not answered.

The header (hostapi.h at a1923f65) and host_sock.inc agree on every case below; where a result is "the platform's answer" and depends on
traffic or on the OS's own choice, the case is NOT answered.

Served per service (everything else listed here as NOT answered):
  SOCKET     the domain is checked before the type: unknown domain (not 0/2/10, or wider than int32) EAFNOSUPPORT; unknown type EINVAL; STREAM/DGRAM (domain 2 or 10) -> a handle
             (A1, A4). NOT answered: domain AF_UNSPEC, type RAW, the protocols of A4's exception.
  CLOSE      an open handle -> 0 and gone; not open -> EBADF.
  every handle-first service: a handle that is not open (or wider than int32) -> EBADF before anything else.
  BIND       null address EFAULT; family not 0/2/10 EAFNOSUPPORT; a local address (A2) or any of the socket's family -> 0 (recorded);
             a unicast address that is not local EADDRNOTAVAIL; a socket already bound or listening EINVAL (POSIX).
             NOT answered: family AF_UNSPEC or another than the socket's, a v4-mapped / multicast / broadcast / link-local address,
             flowinfo or scope_id != 0, a port 1..1023, a port also in a bound socket's overlapping address, a bound socket's port 0
             later asked by GETSOCKNAME.
  LISTEN     DGRAM EOPNOTSUPP; a bound STREAM -> 0. NOT answered: an unbound STREAM.
  ACCEPT     DGRAM EOPNOTSUPP; not listening EINVAL; listening: non-blocking EAGAIN, blocking waits for ever (nothing can arrive).
  CONNECT    (the host checks only null and family, then the system service) null address EFAULT; family not 0/2/10 EAFNOSUPPORT; an address that is not local (A2) ENETUNREACH; a local address on a
             DGRAM socket (port != 0) -> 0, the peer recorded. NOT answered: a local address on a STREAM socket (loopback traffic), AF_UNSPEC
             or another family than the socket's, multicast / broadcast / link-local / v4-mapped addresses, flowinfo or scope_id != 0,
             a listening socket, address any, port 0.
  SENDTO     len > 0x7FFFFFFF EINVAL; an unknown flag bit EOPNOTSUPP; a bad address family EAFNOSUPPORT; DGRAM to a non-local address
             ENETUNREACH; DGRAM without address and without peer EDESTADDRREQ. NOT answered: every STREAM socket, MSG_OOB, a socket
             shut down, a local destination or a peer (loopback traffic), other families / exotic addresses as for CONNECT.
  RECVFROM   len / flags as SENDTO; STREAM ENOTCONN; DGRAM with nothing to receive: non-blocking or MSG_DONTWAIT EAGAIN, blocking waits for ever,
             or SO_RCVTIMEO (set earlier, > 0) in virtual time and then EAGAIN. NOT answered: MSG_OOB, a socket shut down for reading.
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
  SHUTDOWN   how > 2 EINVAL; a socket with no peer ENOTCONN; a DGRAM socket with a peer -> 0 (recorded).
  GETSOCKNAME null EFAULT; a bound socket (port != 0) -> its address. NOT answered: an unbound socket, a bound port 0.
  GETPEERNAME null EFAULT; no peer ENOTCONN; a recorded peer -> it.
  GETFL / SETFL  RDWR | NONBLOCK as the header says; SETFL sets NONBLOCK from that bit, other bits are ignored, result 0 (always answered).
  POLL       nfds > 0x1000000 EINVAL, nfds > 0 with a null array EFAULT; a negative fd is ignored (revents 0); an fd that is not an open
             handle gets POLLNVAL, counts, and makes the call not wait (timeout 0); event bits outside the wire list are dropped; an open
             socket with no local peer has no traffic ever (no network): revents 0, and after the timeout (virtual time; for ever for -1)
             the result is 0. NOT answered: an open socket that has a local peer or could get one: a recorded peer, a bound or a listening
             socket (a local sender could produce traffic; the model never produces it, so the case is left to the OS).
  GAI_OPEN   hints checked first (BADFLAGS, FAMILY, SOCKTYPE), both nodes null NONAME; node a name: NONAME (the name "localhost" without AI_NUMERICHOST is not answered)
             (*sys_errno not written); node null with a family INET / INET6 in the hints, or numeric of that family or of AF_UNSPEC hints
             (A3), service null or decimal 0..65535 (no leading zero): a ticket; a non-numeric service with AI_NUMERICSERV NONAME.
             NOT answered: node null with family AF_UNSPEC, a numeric node of the other family, flags CANONNAME V4MAPPED ALL ADDRCONFIG,
             a service name, a node that is numeric by inet_aton's rules but not strict (1.2.3, 0x7f.1, 01.2.3.4), an unparsable text
             with ':' or '%', an empty node, a hints type/protocol combination without a record.
  GAI_FETCH  the ticket's records as host_sock.inc writes them; a ticket that is not open EINVAL.
"""
import ipaddress
import re
import struct

U64 = (1 << 64) - 1
# sockwire.h numbers (tests/test_bootmachine.py compares them with the fixture header)
WE_BADF, WE_AGAIN, WE_FAULT, WE_INVAL, WE_OVERFLOW = 9, 11, 14, 22, 75
WE_DESTADDRREQ, WE_NOPROTOOPT, WE_PROTONOSUPPORT, WE_OPNOTSUPP, WE_AFNOSUPPORT = 89, 92, 93, 95, 97
WE_ADDRNOTAVAIL, WE_NETUNREACH, WE_NOTCONN = 99, 101, 107
WIRE_AF, WIRE_SOCKTYPE = (0, 2, 10), (1, 2, 3)   # FNVNX_WIRE_AF_LIST, FNVNX_WIRE_SOCKTYPE_LIST
AF_UNSPEC, AF_INET, AF_INET6 = WIRE_AF
STREAM, DGRAM, RAW = WIRE_SOCKTYPE
AI_PASSIVE, AI_CANONNAME, AI_NUMERICHOST, AI_NUMERICSERV = 0x1, 0x2, 0x4, 0x400
WIRE_AI_MASK = 0x1 | 0x2 | 0x4 | 0x8 | 0x10 | 0x20 | 0x400   # FNVNX_WIRE_AI_LIST
WEAI_BADFLAGS, WEAI_NONAME, WEAI_AGAIN, WEAI_FAMILY, WEAI_SOCKTYPE = 2001, 2002, 2003, 2005, 2006
WPOLLNVAL, POLL_MAX = 0x20, 0x1000000
WO_RDWR, WO_NONBLOCK = 2, 0x800
MSG_KNOWN, MSG_OOB, MSG_DONTWAIT = 0x1 | 0x2 | 0x4 | 0x100 | 0x40 | 0x4000, 0x1, 0x40
SOCKADDR, ADDRINFO, FIRST_HANDLE, INT32_MAX = 32, 56, 3, 0x7FFFFFFF   # sizeof(FnvnxWireSockaddr), sizeof(FnvnxWireAddrinfo)
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
STRICT_V4 = re.compile(r"^(0|[1-9][0-9]{0,2})(\.(0|[1-9][0-9]{0,2})){3}$")
LOOSE_V4 = re.compile(r"^(0[xX][0-9a-fA-F]+|[0-9]+)(\.(0[xX][0-9a-fA-F]+|[0-9]+)){0,3}$")
PORT = re.compile(r"^(0|[1-9][0-9]{0,4})$")
NAME_MAX = 255


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
        self.shut = 0           # bit 0: read shut, bit 1: write shut
        self.opts = {}          # (level, option) -> value set by the guest


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
        self.socks, self.tickets, self.tnext = {}, {}, 1

    # -- guest memory
    def _read(self, ptr, n):
        if not self.mem.can_read(ptr, ptr + n):
            raise BadGuestMemory("%#x..+%d is not readable guest memory" % (ptr, n))
        return self.mem.read(ptr, n)

    def _write(self, ptr, data):
        if not self.mem.can_write(ptr, ptr + len(data)):
            raise BadGuestMemory("%#x..+%d is not writable guest memory" % (ptr, len(data)))
        self.mem.write(ptr, data)

    def _cstr(self, ptr):
        out = b""
        while True:
            c = self._read(ptr + len(out), 1)
            if c == b"\0":
                return out
            out += c
            if len(out) > NAME_MAX + 1:
                raise NotDetermined("a node name of more than %d bytes" % NAME_MAX)

    def _sockaddr(self, ptr):
        fam, port, addr, flow, scope, _rsv = struct.unpack("<HH16sIII", self._read(ptr, SOCKADDR))
        return fam, port, addr, flow, scope

    # -- the dispatcher: fn 16..33, nargs already equal to the service's count
    def call(self, fn, a):
        if fn == 32:
            return self._gai_open(a)
        if fn == 33:
            return self._gai_fetch(a)
        if fn == 26:
            return self._poll(a)
        if fn == 16:
            return self._socket(a)
        s = self.socks.get(a[0])
        if s is None:
            return neg(WE_BADF)
        return getattr(self, "_" + {17: "close", 18: "bind", 19: "listen", 20: "accept", 21: "connect", 22: "sendto", 23: "recvfrom",
                                    24: "setsockopt", 25: "getsockopt", 27: "shutdown", 28: "getsockname", 29: "getpeername",
                                    30: "getfl", 31: "setfl"}[fn])(a, s)

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
        del self.socks[a[0]]
        return 0

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
        return 0

    def _accept(self, a, s):
        if s.typ == DGRAM:
            return neg(WE_OPNOTSUPP)
        if not s.listening:
            return neg(WE_INVAL)
        if s.shut:
            raise NotDetermined("accept on a shut socket")
        return neg(WE_AGAIN) if s.nonblock else (("sleep", None), neg(WE_AGAIN))   # nothing can ever arrive

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
        if s.typ == STREAM or kind == "any" or not port:
            raise NotDetermined("connect to a local address: loopback traffic, or the OS's reading of 'any' / port 0")
        s.peer = (fam, port, addr)
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
        if s.typ == STREAM or s.shut or a[3] & MSG_OOB:
            raise NotDetermined("sendto on a STREAM or shut socket, or with MSG_OOB")
        if d is None:
            if s.peer is None:
                return neg(WE_DESTADDRREQ)
            raise NotDetermined("sendto to the connected loopback peer")
        if d[3] == "exotic":
            raise NotDetermined("sendto: an address of no decided class")
        if d[3] == "other":
            return neg(WE_NETUNREACH)
        raise NotDetermined("sendto to a local address: loopback traffic")

    def _recvfrom(self, a, s):
        r = self._flags_len(a)
        if r is not None:
            return r
        if s.shut & 1 or a[3] & MSG_OOB:
            raise NotDetermined("recvfrom on a socket shut for reading, or with MSG_OOB")
        if s.typ == STREAM:
            return neg(WE_NOTCONN)
        if s.nonblock or a[3] & MSG_DONTWAIT:
            return neg(WE_AGAIN)
        tmo = s.opts.get((1, 20))
        return (("sleep", None if not tmo else self.now_ns() + tmo), neg(WE_AGAIN))

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

    def _shutdown(self, a, s):
        if a[1] > 2:
            return neg(WE_INVAL)
        if s.peer is None:
            return neg(WE_NOTCONN)
        s.shut |= (1, 2, 3)[a[1]]
        return 0

    def _getsockname(self, a, s):
        if not a[1]:
            return neg(WE_FAULT)
        if not s.bound or not s.bound[1]:
            raise NotDetermined("getsockname of an unbound socket or of a bound port 0 (the OS's choice)")
        self._write(a[1], wire_addr(*s.bound))
        return 0

    def _getpeername(self, a, s):
        if not a[1]:
            return neg(WE_FAULT)
        if s.peer is None:
            return neg(WE_NOTCONN)
        self._write(a[1], wire_addr(*s.peer))
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
        for f in fdv:   # an open socket with no local peer never has traffic: revents 0
            o = self.socks.get(f) if f >= 0 else None
            if o is not None and (o.peer is not None or o.bound or o.listening):
                raise NotDetermined("poll of an open socket with a local peer or a bound / listening socket: its readiness")
        nval = 0
        for i, fd in enumerate(fdv):   # a handle that is not open: POLLNVAL; a negative fd is ignored
            self.mem.write(fds + 8 * i + 6, struct.pack("<H", WPOLLNVAL if fd >= 0 and fd not in self.socks else 0))
            nval += fd >= 0 and fd not in self.socks
        if nval or tmo == 0:   # an entry that is ready already ends the wait
            return nval
        return (("sleep", None if tmo < 0 else self.now_ns() + tmo * 1_000_000), 0)

    # -- getaddrinfo
    def _gai_open(self, a):
        node_p, svc_p, hints_p = a[0], a[1], a[2]
        flags = fam = st = pr = 0
        if hints_p:
            flags, fam, st, pr = struct.unpack_from("<IiIi", self._read(hints_p, ADDRINFO))
            if flags & ~WIRE_AI_MASK:
                return neg(WEAI_BADFLAGS)
            if fam not in WIRE_AF:
                return neg(WEAI_FAMILY)
            if st and st not in WIRE_SOCKTYPE:
                return neg(WEAI_SOCKTYPE)
        if not node_p and not svc_p:
            return neg(WEAI_NONAME)
        node = self._cstr(node_p).decode("latin-1") if node_p else None
        kind = self._node_kind(node)
        if kind == "name":
            # declared machine (hostapi.h): a node that is not a numeric address and not the local host -> NONAME. The stage knows
            # one name of the local host, "localhost"; the list an OS returns for it is the OS's own, so that case is not answered.
            if node.rstrip(".").lower() == "localhost" and not flags & AI_NUMERICHOST:
                raise NotDetermined("gai_open: the local host name (the address list is the host's)")
            return neg(WEAI_NONAME)
        svc = self._cstr(svc_p).decode("latin-1") if svc_p else None
        if svc is None:
            port = 0
        elif PORT.match(svc) and int(svc) <= 65535:
            port = int(svc)
        elif flags & AI_NUMERICSERV:
            return neg(WEAI_NONAME)
        else:
            raise NotDetermined("gai_open: a service name (the services database)")
        if flags & ~(AI_PASSIVE | AI_NUMERICHOST | AI_NUMERICSERV):
            raise NotDetermined("gai_open: hint flags %#x (resolver-dependent)" % flags)
        if node is None:
            if fam == AF_UNSPEC:
                raise NotDetermined("gai_open: null node with family AF_UNSPEC (the order and set of families is the host's)")
            rec_fam = fam
            addr = (bytes(16) if flags & AI_PASSIVE else (b"\x7f\0\0\1" if fam == AF_INET else bytes(15) + b"\1")).ljust(16, b"\0")
        else:
            rec_fam = AF_INET if kind == "v4" else AF_INET6
            if fam not in (AF_UNSPEC, rec_fam):
                raise NotDetermined("gai_open: numeric node of another family than the hints")
            addr = (ipaddress.IPv4Address(node).packed if kind == "v4" else ipaddress.IPv6Address(node).packed).ljust(16, b"\0")
        recs = [(t, p) for t, p in ((STREAM, 6), (DGRAM, 17), (RAW, 0)) if (not st or t == st) and (not pr or p == pr)]
        if not recs:
            raise NotDetermined("gai_open: socktype %d protocol %d have no record in the model" % (st, pr))
        blob = b"".join(struct.pack("<iiiiII", flags, rec_fam, t, p, 0, 0) + wire_addr(rec_fam, port, addr) for t, p in recs)
        tid = self.tnext
        self.tnext = 1 if tid == INT32_MAX else tid + 1
        self.tickets[tid] = blob
        return tid

    @staticmethod
    def _node_kind(node):
        """None (null node) | 'v4' | 'v6' (numeric, strict) | 'name'; text that may be numeric for some resolver is not decided"""
        if node is None:
            return None
        if not node:
            raise NotDetermined("gai_open: empty node")
        if STRICT_V4.match(node) and all(int(p) < 256 for p in node.split(".")):
            return "v4"
        if LOOSE_V4.match(node):
            raise NotDetermined("gai_open: node %r is numeric for inet_aton but not strict" % node)
        if ":" in node or "%" in node:
            try:
                if "%" in node:
                    raise ValueError
                ipaddress.IPv6Address(node)
            except ValueError:
                raise NotDetermined("gai_open: node %r with ':' or '%%' is not a strict IPv6 text" % node) from None
            return "v6"
        return "name"

    def _gai_fetch(self, a):
        tid = a[0]
        if not 0 < tid <= INT32_MAX or tid not in self.tickets:
            return neg(WE_INVAL)
        blob = self.tickets[tid]
        if a[2] < len(blob):
            return len(blob)
        if a[1]:
            self._write(a[1], blob)
        del self.tickets[tid]
        return len(blob)
