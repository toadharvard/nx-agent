#!/usr/bin/env python3
"""Tests of verify/hdrdefs.py on hand-written header trees (expected values are written out here, never computed by the tool). Plain script."""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import hdrdefs  # noqa: E402

CC = "aarch64-linux-gnu-gcc"
FAILED, N = [], [0]

TYPES = "typedef unsigned char u8; typedef unsigned short u16; typedef unsigned int u32; typedef long long i64;\n"
TREE = {
    "sys/types.h": TYPES,
    "sys/errno.h": "extern int g;\n#define EAGAIN 35\n#define EWEIRD (&g)\n",
    "sys/_timeval.h": "struct timeval { i64 tv_sec; long tv_usec; };\n",
    "sys/socket.h": "#define AF_INET 2\n#define SOCK_RAW (3 | 0)\n#define MSG_X 0x80000000u\n#define NAME \"text\"\n#define EMPTY\n"
                    "#define FUNC(x) (x)\n#define BIG (1ull << 40)\n#undef GONE\n#define GONE 7\n#undef GONE\n"
                    "struct sockaddr { u8 sa_len; u8 sa_family; char sa_data[14]; };\n"
                    "struct linger { int l_onoff; int l_linger; };\n"
                    "struct msghdr { void *msg_name; u32 msg_namelen; void *msg_iov; int msg_iovlen; void *msg_control; u32 msg_controllen; int msg_flags; };\n",
    "netinet/in.h": "#define IPPROTO_TCP 6\nstruct sockaddr_in { u8 sin_len; u8 sin_family; u16 sin_port; u32 sin_addr; char sin_zero[8]; };\n"
                    "struct sockaddr_in6 { u8 sin6_len; u8 sin6_family; u16 sin6_port; u32 sin6_flowinfo; char sin6_addr[16]; u32 sin6_scope_id; };\n",
    "netinet/tcp.h": "#define TCP_NODELAY 1\n",
}


def case(name, ok, detail=""):
    N[0] += 1
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else "  " + str(detail)))
    if not ok:
        FAILED.append(name)


def tree(files):
    d = tempfile.mkdtemp()
    for p, text in files.items():
        os.makedirs(os.path.dirname(os.path.join(d, p)), exist_ok=True)
        open(os.path.join(d, p), "w").write(text)
    return d


def main():
    d = tree(TREE)
    s = hdrdefs.extract(CC, [d])
    for k, want in [
        (("macro", "AF_INET"), 2), (("macro", "SOCK_RAW"), 3), (("macro", "BIG"), 1 << 40), (("macro", "IPPROTO_TCP"), 6), (("macro", "TCP_NODELAY"), 1),
        (("macro", "EAGAIN"), 35),
        (("macro", "MSG_X"), 0x80000000),                # an unsigned value is kept as written
        (("macro", "NAME"), None), (("macro", "EMPTY"), None), (("macro", "EWEIRD"), None),   # no integer constant: None
        (("field", "sockaddr", "sa_family"), (1, 1)), (("field", "sockaddr", "sa_data"), (2, 14)), (("field", "sockaddr", "<struct>"), (1, 16)),
        (("field", "sockaddr_in", "sin_port"), (2, 2)), (("field", "sockaddr_in", "sin_addr"), (4, 4)), (("field", "sockaddr_in", "<struct>"), (4, 16)),
        (("field", "sockaddr_in6", "sin6_scope_id"), (24, 4)), (("field", "sockaddr_in6", "<struct>"), (4, 28)),
        (("field", "timeval", "tv_usec"), (8, 8)), (("field", "linger", "l_linger"), (4, 4)),
        (("field", "msghdr", "msg_iovlen"), (24, 4)), (("field", "msghdr", "msg_flags"), (44, 4)),
        (("field", "linger", "nosuchfield"), None),
    ]:
        if k[0] == "field" and k[2] == "nosuchfield":
            case("%s absent from the set (not in STRUCTS)" % (k,), k not in s)
        else:
            case("%s = %r" % (k, want), s.get(k, "absent") == want, s.get(k, "absent"))
    case("function-like macro and #undef'd macro are not definitions", ("macro", "FUNC") not in s and ("macro", "GONE") not in s)
    case("a macro of a non-target header is not a definition", ("macro", "TYPES") not in s)
    ilp = hdrdefs.extract(CC, [d], "ilp32")
    case("ilp32: timeval.tv_usec is 4 bytes (long)", ilp[("field", "timeval", "tv_usec")] == (8, 4), ilp[("field", "timeval", "tv_usec")])

    # compare: a tag with a different value, with a missing definition, and an equal one
    b = dict(TREE)
    b["sys/socket.h"] = TREE["sys/socket.h"].replace("#define AF_INET 2", "#define AF_INET 3").replace("#define BIG (1ull << 40)\n", "")
    b["sys/errno.h"] = "#define EAGAIN 11\n"
    d2 = tree(b)
    t = hdrdefs.extract(CC, [d2])
    differ, missing = hdrdefs.compare(s, t)
    case("compare: AF_INET differs, EAGAIN differs", {k for k, _, _ in differ} == {("macro", "AF_INET"), ("macro", "EAGAIN")}, differ)
    case("compare: BIG missing from the tag", missing == [("macro", "BIG")], missing)
    case("compare: a set equals itself", hdrdefs.compare(s, s) == ([], []))
    case("compare: the tag's extra definitions are not held against it", hdrdefs.compare(t, s) == ([(("macro", "AF_INET"), 3, 2), (("macro", "EAGAIN"), 11, 35)], []))
    rows = hdrdefs.matrix(s, {"a": {"defs": s}, "b": {"defs": t}, "c": {"error": "x"}}, {"EAGAIN", "EWEIRD"})
    case("matrix: match / differs / no-extract", [r[1] for r in rows] == ["match", "differs", "no-extract"], rows)
    b["sys/errno.h"] = "extern int g;\n#define EAGAIN 11\n#define EWEIRD (&g)\n"
    b["sys/socket.h"] = TREE["sys/socket.h"]
    t2 = hdrdefs.extract(CC, [tree(b)])
    case("matrix: agrees except the errno macros -> match-without-errno",
         hdrdefs.matrix(s, {"e": {"defs": t2}}, {"EAGAIN", "EWEIRD"})[0][1] == "match-without-errno")
    for x in (d, d2):
        shutil.rmtree(x, ignore_errors=True)
    print("%d cases, %d failed" % (N[0], len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
