# L4 VERIFY 009: bootsock answers every case (no NotDetermined), rule R-SOCK (decision, 2026-11-11 23:15Z)

Class: the socket model of the boot machine (verify/bootsock.py, module 11 fn 16..26, 30, 31) ends a run as
"ext-module-missing" from 18 raise sites of `NotDetermined` (measured at 1979832). That is a stop-with-a-report path
(forbidden, CLAUDE.md 2026-11-10). It is on the boot path: the exe imports 21 wsock32 functions (l5-platform-001), which
the platform maps onto these services.

The model is the KERNEL side of the one shared boot machine (the console's bsd service as seen through libnx), not a test
double. Its cases split into two classes, each closed by one rule:

R-SOCK-1 (loopback is part of the declared machine). The declared machine has no network connection, but every Windows
(and Wine 9.0, and the console's stack) has the loopback interface 127.0.0.0/8 and ::1. Traffic between sockets of the one
machine is deterministic and is modelled, not refused:
- STREAM: connect to a local address with a listening socket bound to it completes (the connection enters that listener's
  accept queue, up to its backlog); no listener: ECONNREFUSED; accept takes the queue head; send/recv move bytes through
  the pair's buffers (SO_SNDBUF/SO_RCVBUF sizes from R-SOCK-2); close gives the peer EOF; send after the peer closed EPIPE.
- DGRAM: sendto a local address with a socket bound to it appends a datagram to that socket's queue (dropped when the
  receive buffer is full, as UDP does); no bound socket: the datagram is dropped, sendto succeeds (UDP has no refusal
  without ICMP; FreeBSD reports a later ECONNREFUSED only on a connected socket - follow R-SOCK-2's reference exactly).
- poll readiness follows from those queues and buffers; blocking calls wait in virtual time and are woken by the peer.
- implicit bind on connect/sendto takes an ephemeral port by R-SOCK-2.
Order between guest threads is the order of the calls in the machine (the boot machine serialises host calls).

R-SOCK-2 (an OS's own choice has one reference). Every remaining case (option ranges and the stored form of an option,
privileged ports 1..1023, AF_UNSPEC, RAW, unsupported protocols, v4-mapped / multicast / broadcast / link-local addresses,
flowinfo / scope_id, listen on an unbound socket, connect on a listening socket, MSG_OOB, SO_ACCEPTCONN, ephemeral port
choice) is answered by the console's bsd service, whose documented base is the FreeBSD socket layer. The reference is
FreeBSD's source at the release that base names, pinned by commit and mirrored to fnv-nx-build `tools/freebsd-net`, and
cited per case (file:line). This is OWNER assumption 4 (the console's behaviour until a run on it); no new assumption.
- Ephemeral ports: the declared machine sets net.inet.ip.portrange.randomized = 0 (a documented FreeBSD setting), so the
  choice is the reference's sequential rule from the first port of its documented range. Nothing above the host may
  depend on the number (the same argument as A1).
- No root: the guest is an unprivileged process (privileged ports EACCES as the reference gives).
- If the documented base cannot be found, the worker stops this part and reports; it never guesses a release.

Check that proves it: `grep -c NotDetermined verify/bootsock.py` = 0 (the class is gone, not caught), every former raise
site has a test in tests/test_bootsock.py whose expected value is cited (R-SOCK-1 rule text or R-SOCK-2 file:line), and the
L1 verdict is green. bootmachine.py's `except bootsock.NotDetermined` goes with the class.
