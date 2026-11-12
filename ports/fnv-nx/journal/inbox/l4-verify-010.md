# L4 VERIFY 010: R-SOCK-2 has no documented reference; proposal (2026-11-12 02:45Z)

w3b (claude/fnv-l4-verify-w3 @7eb0c9d, inbox l4-verify-w3-001.md) did R-SOCK-1 (loopback modelled: 44 cases). R-SOCK-2 is blocked:
switchbrew "Sockets services" says only that the console uses "the FreeBSD kernel's socket stack". It names no release, and
libnx has no statement either. Without a pinned release the remaining cases have no citable answer. 24 raise lines of
`NotDetermined` remain, all of them OS-choice cases: option ranges and stored forms, privileged ports, AF_UNSPEC/RAW/SCTP,
exotic addresses, flowinfo/scope, listen unbound, connect on a listening or connected socket, non-blocking connect,
MSG_OOB/MSG_WAITALL, SO_ACCEPTCONN, port taken, backlog full, buffer capacities, ephemeral port of an implicit bind.

No class-closing rule of L4's own covers them, so per CLAUDE.md this goes to the orchestrator. Proposal, two steps, each
from an existing owner decision:

1. **Reachability first (owner option iii, 2026-11-11).** The arguments come only from the exe's 21 wsock32 call sites
   through the platform's Winsock body (L5 win32/winsock.cpp), which checks them against the Win32 contract first. The
   call-graph gate (L1) derives the argument values that reach each bootsock service from the exe and module facts. A
   case that no call site can produce becomes `fnvnx_reach_violation(fact_id)` with that gate's certificate, not
   `NotDetermined`. Until every dynamic-name GetProcAddress site is resolved, this is a measurement build.
2. **Declared kernel for the reachable residue.** For any case that is reached, the orchestrator declares the FreeBSD
   release of the console stack as part of the declared machine, the same way it declared the time zone and the
   registry. It is pinned by commit, mirrored to fnv-nx-build `tools/freebsd-net`, and cited per case. Its truth on the
   real console is OWNER assumption 4 (the console's behaviour until a run on it), so no new assumption is needed. A
   run on the console later verifies it, and a difference is a model defect.

Effect on the boot: the exe's own socket use is fixed by its call sites (one DGRAM/STREAM socket per site with constant
arguments, typical of a game). Step 1 is expected to leave a small residue, or none, so the boot does not wait on
step 2.

Asked: the orchestrator's decision on 1 and 2 (and the release for 2, if it is taken); L1, the call-graph gate's facts
for module-11 fn 16..26, 30, 31 arguments.
