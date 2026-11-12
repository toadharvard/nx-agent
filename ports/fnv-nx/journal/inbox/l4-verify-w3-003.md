# L4 worker w3b: provenance of libnx's nx/external/bsd (a fact, not a match; follow-up of l4-verify-w3-002)

Source: full history of github.com/switchbrew/libnx, master (fetched with --filter=blob:none; the tools/libnx mirror pin is 518bf11a).

## Import
- a3e90d68a098632d283c078d075fa347cf3284fc, TuxSH, 2018-01-29, "Add all the needed headers from FreeBSD... / ... clean them up, also fix some bugs in bsd.c/h, etc."
  Added nx/include/{arpa/inet.h, net/*, netinet/{in,tcp,udp}.h, poll.h, sys/{_iovec,_sockaddr_storage,filio,ioccom,ioctl,socket,sockio,sysctl}.h}
  (22 header files). Parent 4d78f128 (2018-01-23). The message and the files name NO FreeBSD revision, commit or release: the files carry only the
  `$FreeBSD$` keyword (keyword form of a tree exported from a git/svn checkout, no revision text), sys/socket.h line 1 is a libnx comment
  "TuxSH: removed definitions under _KERNEL ifdef blocks, modify the prototype of some functions, some other cleanup, etc.", sys/sockio.h:73 mentions
  "FreeBSD 9.x" only inside an old definition. No PR text is in the messages ((#68), (#187), (#605) are bare numbers).
  => The documented source is "FreeBSD" at an unnamed revision of about January 2018 (FreeBSD 11.1 was released 2017-07, 11.2 in 2018-06, 12.0 in
  2018-12). That is a date, not a revision; nothing is proven and nothing is picked.
- 133f8ec (2018-02-25) only moved the 22 files to nx/external/bsd/include (R100, no content change).

## Every later commit that changed the headers (git log on nx/include/** and nx/external/bsd/**)
- 0b92bb7 2018-01-30 "Add newlib wrapper for sockets..." sys/{ioccom,socket,sysctl}.h, 6+3 lines.
- 4b685d6 2018-02-04 "Add basic IPv6 definitions as sfdnsres seems to support it..." netinet/in.h +116: the commented-out
  `//#include <netinet6/in6.h>` is replaced by libnx-written "// TuxSH: modified" definitions: INET6_ADDRSTRLEN 46, IPV6_UNICAST_HOPS ..
  IPV6_V6ONLY, IN6_IS_ADDR_* (so these macros are libnx's, FreeBSD keeps them in netinet6/in6.h). The sockaddr_in6 of libnx never had sin6_len:
  `git log -S sin6_len` over the file finds no commit (the import version has none) => sockaddr_in6.sin6_family at offset 0, size 1 is libnx's own
  struct, which explains the one non-errno layout difference of the census (every tag has sin6_len).
- b8fb172 2018-02-04 "Add sfdnsres" (socket.h 1 line); d33b260 2018-02-19 "Add documentation to bsd.h and sfdnsres.h, etc." (5 files, 1 line each, comments).
- 4786624 2018-03-30 "Fix IN6_IS_ADDR macros in netinet/in.h (#68)" 16 lines; 493d433 2018-10-19 "netinet: fix missing declarations (#187)" in.h +9.
- 4c0991d 2023-04-02 "Added CMSG macros to /sys/socket.h (#605)" socket.h +29/-2.
- 1135047 2019-03-08 "Fixed ifr_flags type by introducing ifr_flagshigh (fixes #248)"; 2405872 2018-12-20 sys/poll.h redirect; 8b2b12b 2018-06-14 "fix _IOC cast";
  aee9ba4 2018-06-25 netdb.h socklen_t. (net/if.h, ioccom.h, poll, netdb: outside the four census headers.)
- sys/errno.h is not in libnx at all: errno is newlib's.

## What this explains of the census (journal/inbox/l4-verify-w3-002*)
- sin6_family difference: libnx-authored sockaddr_in6 (4b685d6).
- IPV6_*, INET6_ADDRSTRLEN: libnx-authored (4b685d6); FreeBSD defines them in netinet6/in6.h.
- The remaining non-errno "missing" definitions per tag (TCPOLEN_FAST_OPEN_*, IP_MAX_SOURCE_FILTER, IP_MIN_MEMBERSHIPS, IPPROTO_SEP, IP_ORIGDSTADDR ..)
  are in the import (a3e90d6) and not explained by a later libnx edit: they are the evidence of which FreeBSD revision the import came from. They place
  it before the removals of 12.1 (IP_MAX_SOURCE_FILTER, IP_MIN_MEMBERSHIPS) and after 11.4 (IP_ORIGDSTADDR). That is consistent with FreeBSD head of
  Jan 2018 (a 12-CURRENT snapshot), NOT a release. A diff of that snapshot needs the exact freebsd-src commit; with no named revision it is not proven.
  Not done: a search of freebsd-src history by blob equality (not asked; would pick a commit, which is not a documented fact).

## errno conversion (the documented reason errno cannot match a FreeBSD tag)
- nx/source/runtime/devices/convert_errno.c (commit 4e4d2fe, Dave Murphy, 2018-06-14, "translate horizon errno to newlib errno"):
  `_error_code_map[]` lines 14-153 is indexed by the console's errno number and holds newlib's constant (index 11 -> EAGAIN, line 31 and 61);
  `_convert_errno(int bsdErrno)` lines 158-164 returns the map entry, or 10000 + n for an unknown / unmapped number (line 160-161).
- nx/source/runtime/devices/socket.c:326: `errno_ = _convert_errno(g_bsdErrno); /* Nintendo actually used the Linux errno definitions for their
  FreeBSD build :) */`; g_bsdErrno is set in nx/source/services/bsd.c:167 from the service's reply (errno_ of the output, line 157).
- So the console's bsd service speaks Linux errno numbers (switchbrew "Sockets services": "the errno macro definitions being in use actually come from
  Linux (and not from FreeBSD as one would expect!)"); the wire numbering of sockwire.h is the same. A FreeBSD tag's sys/errno.h (EAGAIN 35) is
  therefore not the console's errno table whatever the release.
