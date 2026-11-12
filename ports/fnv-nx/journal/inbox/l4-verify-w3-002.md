# L4 worker w3b: derivation of the FreeBSD release of the console's bsd service (task of l4-verify-010): ZERO tags match, nothing picked

## Result
No FreeBSD release tag matches the socket headers of the pinned guest stack, for lp64 and for ilp32, with or without the errno macros. Nothing is
mirrored to fnv-nx-build and no case is cited. Table of every tag: journal/inbox/l4-verify-w3-002-census.tsv (167 tags x 2 ABIs; 51 tags of
release/2.x..7.x do not preprocess with the pinned gcc 13 and are "no-extract": K&R-era headers).

## Inputs (commit of each)
- reference set D_newlib: the headers the guest actually sees. newlib's include tree has NO sys/socket.h, netinet/in.h or netinet/tcp.h: they come
  from libnx's `nx/external/bsd/include` (FreeBSD-derived, "$FreeBSD$" keyword, no revision), fnv-nx-build tools/libnx @ 518bf11ad6a52149e43fbd0fa83b0087a430f446;
  sys/errno.h and the rest of the include path from fnv-nx-build tools/fnvnx-a64-msvcmodel @ 00e3d42acd10593d534b9a70bb8940cee7991436
  (newlib 4.6.0.20260123, devkitPro patch -4). 697 definitions (13 rejected = not integer constants).
- extractor: verify/hdrdefs.py @ 92b4aa0 (tests/test_hdrdefs.py, 32 cases); compiler aarch64-linux-gnu-gcc 13.3.0 (env.json pin), `-nostdinc -ffreestanding`.
- tags: freebsd/freebsd-src release/* (commit of every tag in the tsv); headers from sys/sys, sys/netinet, sys/netinet6, sys/net, sys/x86/include and
  sys/arm64/include (tags with sys/arm64, i.e. 11.0 on) else sys/amd64/include as `machine`.
- A tag's macros count from ANY header of its include closure (FreeBSD keeps IPV6_* in netinet6/in6.h, _ALIGNBYTES in machine/_align.h, libnx merged
  them into the four headers); the reference's macros are those of its four headers (+ _sockaddr_storage.h).

## Rule applied
A tag matches when it agrees on every definition both sets share and the reference has no definition missing from the tag (hdrdefs.compare).

## Findings (lp64; the tsv has all)
- errno: newlib's sys/errno.h uses the Linux numbering (EAGAIN 11, FreeBSD 35): 49..52 values differ in EVERY tag, so no tag matches the literal rule.
- Without errno no tag matches either. Nearest: release/12.0.0 (4ba619efbdd2476fb4ac075c6728e17ee02d6bcd): 4 definitions: sockaddr_in6.sin6_family
  (libnx (0,1), tag (1,1): libnx has no sin6_len), TCPOLEN_FAST_OPEN_MAX/MIN and __ELASTERROR (libnx defines them, 12.0.0 does not).
  12.1..12.4 each lack more (IP_MAX_SOURCE_FILTER, IP_MIN_MEMBERSHIPS, IPPROTO_SEP); 11.3/11.4 lack IP_ORIGDSTADDR, IP_RECVORIGDSTADDR,
  SCM_TIME_INFO, SF_USER_READAHEAD, SO_MAX_PACING_RATE, ST_INFO_HW(_HPREC); 13.x differs in AF_MAX/PF_MAX (42 vs 43) and lacks AF_VENDOR02.
- ilp32: the same ordering; 12.0.0 differs in 8 (timeval, _ALIGNBYTES, _SS_* sizes follow the data model of each tree's machine headers).
- Reading: libnx's headers are a FreeBSD snapshot between releases (it has TCPOLEN_FAST_OPEN and the IP_*SOURCE_FILTER macros 12.0.0 / 12.1.0 lack and
  later tags removed) with its own edits (no sin6_len, Linux errno). The release tags do not contain that snapshot. No tag is picked.

## What this leaves open (for the orchestrator)
R-SOCK-2 stays blocked: there is no release to cite. Options (none taken): (a) a documented FreeBSD revision for libnx's bsd headers (libnx commit
history of nx/external/bsd, or Nintendo's OSS notice), then mirror that revision; (b) the owner's statement of the reference.
R-SOCK-1 head: 7eb0c9d (L1 verdict green on it, 26 gates, selftests 56/56, 211.7 s; test_bootmachine.py 145 cases ok with unicorn).
