# L4 worker w3b: R-SOCK partial, R-SOCK-2 blocked by an undocumented FreeBSD release

- R-SOCK-1 is done in verify/bootsock.py and verify/bootmachine.py (new wait kind ("sock", thunk, deadline); the thunk is run when a thread is
  picked and gives the call's result once the peer made it possible). tests/test_bootsock.py: 9 tables, 0 failed, 44 new R-SOCK-1 cases with
  expectations from the rule text (POSIX send() ENOTCONN for an unconnected stream).
- R-SOCK-2 is NOT done. The documented base (switchbrew "Sockets services": "Nintendo has the FreeBSD kernel's socket stack") names no
  release; libnx has no statement either. No release is guessed, no source is mirrored, no case is cited.
- NotDetermined stays at 24 raise lines (was 18 sites): every one is an R-SOCK-2 case (option ranges and stored forms, privileged ports, AF_UNSPEC,
  RAW, SCTP and other protocols, exotic addresses, flowinfo/scope, listen on an unbound socket, connect on a listening / connected socket,
  non-blocking connect result, MSG_OOB, MSG_WAITALL, SO_ACCEPTCONN, port taken, listener backlog full, and the ephemeral port of an implicit bind
  on connect/sendto, which also needs SO_RCVBUF/SO_SNDBUF capacities). Without the ephemeral-port rule a STREAM client must be explicitly bound.
- Needed from the orchestrator: the FreeBSD release (a documented pin), or the owner's statement of it.
