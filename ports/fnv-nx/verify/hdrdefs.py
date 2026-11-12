#!/usr/bin/env python3
"""hdrdefs.py: the DEFINITIONS of a socket header set, extracted by the pinned compiler, and the comparison of two sets (stdlib only).

Used to derive, never to choose, the FreeBSD release that the console's bsd service is based on (journal/inbox/l4-verify-010.md, "Decided"):
a release matches when it agrees on every definition that both header sets contain and the reference set defines nothing the release lacks.

A set is a dict:
  ("macro", NAME)               -> integer value of an object-like macro defined in one of the target headers
  ("field", STRUCT, FIELD)      -> (offset, size) of the field; FIELD "<struct>" -> (alignment, sizeof the struct)
A value None means the compiler rejected the definition (a macro that is no integer constant expression; a struct or field the headers lack).

How (no regex reading of C, the compiler decides every value):
  1. `cc -E -dD` over the include file lists the macros and the file each is defined in; those defined in a TARGET header are kept
     (the preprocessor's own # line markers say where a #define sits; #undef removes).
  2. one C file holds, per macro, the initializer `(long long)(NAME)` and, per struct field, `offsetof` and `sizeof`; `cc -S` evaluates them.
     A line the compiler rejects (error at that line of the file) gets value None and the file is compiled again, until it compiles.
Values travel through the assembly as (index+1, value) pairs, so that no run of zero values collapses into `.zero`.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

TARGETS = ("sys/socket.h", "netinet/in.h", "netinet/tcp.h", "sys/errno.h", "sys/_sockaddr_storage.h")
ERRNO_TARGET = "sys/errno.h"
INCLUDES = ("sys/types.h", "sys/socket.h", "netinet/in.h", "netinet/tcp.h", "sys/errno.h", "sys/_timeval.h")
STRUCTS = {
    "sockaddr": ("sa_len", "sa_family", "sa_data"),
    "sockaddr_in": ("sin_len", "sin_family", "sin_port", "sin_addr", "sin_zero"),
    "sockaddr_in6": ("sin6_len", "sin6_family", "sin6_port", "sin6_flowinfo", "sin6_addr", "sin6_scope_id"),
    "linger": ("l_onoff", "l_linger"),
    "timeval": ("tv_sec", "tv_usec"),
    "msghdr": ("msg_name", "msg_namelen", "msg_iov", "msg_iovlen", "msg_control", "msg_controllen", "msg_flags"),
}
WORD = re.compile(r"^\s*\.(?:xword|dword|quad)\s+(\S+)\s*$")   # a value that is no integer is a relocation (an address): not an integer constant
ZERO = re.compile(r"^\s*\.zero\s+(\d+)\s*$")
ERR = re.compile(r"^([^:\n]+):(\d+):\d+: (?:fatal )?error:")


def run(cmd, **kw):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kw)


def compiler_include(cc):
    """the compiler's own freestanding headers (stddef.h, stdarg.h ...)"""
    return os.path.dirname(run([cc, "-print-file-name=include/stddef.h"]).stdout.strip())


def flags(cc, incs, abi):
    return [cc, "-x", "c", "-nostdinc", "-ffreestanding", "-w", "-fmax-errors=0"] + (["-mabi=ilp32"] if abi == "ilp32" else []) + \
           sum((["-I", d] for d in incs), []) + ["-isystem", compiler_include(cc)]


def target_of(path):
    path = path.replace("\\", "/")
    return next((t for t in TARGETS if path.endswith("/" + t) or path == t), None)


def macros(cc, incs, abi, tmp):
    """{name: target header} of the object-like macros defined in a TARGET header (after #undef)"""
    src = os.path.join(tmp, "m.c")
    with open(src, "w") as f:
        f.write("".join("#include <%s>\n" % h for h in INCLUDES))
    r = run(flags(cc, incs, abi) + ["-E", "-dD", src])
    if r.returncode:
        raise RuntimeError("preprocess failed:\n" + r.stderr[-3000:])
    cur, out = None, {}
    for line in r.stdout.splitlines():
        if line.startswith("# "):
            parts = line.split('"')
            if len(parts) >= 2:
                cur = target_of(parts[1])
        elif line.startswith("#define ") and cur:
            name = line[8:].split(None, 1)[0]
            if "(" not in name:   # a function-like macro has "(" right after its name
                out[name] = cur
        elif line.startswith("#undef "):
            out.pop(line[7:].strip(), None)
    return out


def evaluate(cc, incs, abi, tmp, entries):
    """entries: list of (key, c expression pair text). Values by the compiler; a line it rejects gets None."""
    keys = list(entries)
    vals = {}
    while True:
        src = os.path.join(tmp, "v.c")
        lines = ["#include <%s>" % h for h in INCLUDES]
        lines.append("const long long V[] = {")
        first = len(lines) + 1
        live = [k for k in keys if k not in vals]
        for i, k in enumerate(live):
            lines.append("  %d, (long long)(%s)," % (i + 1, entries[k]) if not entries[k].startswith("@") else "  %d, %s," % (i + 1, entries[k][1:]))
        lines.append("};")
        open(src, "w").write("\n".join(lines) + "\n")
        r = run(flags(cc, incs, abi) + ["-S", "-o", "-", src])
        # an error inside a header ("note: in expansion of macro") still names the line of this file that caused it
        bad = {int(m.group(1)) for m in re.finditer(re.escape(src) + r":(\d+):\d+:", r.stderr)}
        if r.returncode == 0:
            break
        bad = {b for b in bad if first <= b < first + len(live)}
        if not bad:
            raise RuntimeError("compile failed without a located error:\n" + r.stderr[-3000:])
        for b in bad:
            vals[live[b - first]] = None
    nums = []
    for line in r.stdout.splitlines():
        m, z = WORD.match(line), ZERO.match(line)
        if m:
            nums.append(int(m.group(1), 0) if re.fullmatch(r"-?(0x[0-9a-fA-F]+|\d+)", m.group(1)) else None)
        elif z:
            nums += [0] * (int(z.group(1)) // 8)
    live = [k for k in keys if k not in vals]
    pairs = nums[:2 * len(live)]
    for i, k in enumerate(live):
        assert pairs[2 * i] == i + 1, "assembly pairs out of step at %s" % (k,)
        vals[k] = pairs[2 * i + 1]   # None: not an integer constant
    return vals


def extract(cc, incs, abi="lp64"):
    """the set of definitions of the headers found through incs (list of include dirs, in order)"""
    with tempfile.TemporaryDirectory() as tmp:
        ent = {}
        for name in macros(cc, incs, abi, tmp):
            ent[("macro", name)] = name
        out = {k: v for k, v in evaluate(cc, incs, abi, tmp, ent).items()} if ent else {}
        ent = {}
        for s, fields in STRUCTS.items():
            for fld in fields:
                ent[("field", s, fld, "o")] = "__builtin_offsetof(struct %s, %s)" % (s, fld)
                ent[("field", s, fld, "s")] = "@sizeof(((struct %s *)0)->%s)" % (s, fld)
            ent[("field", s, "<struct>", "a")] = "@_Alignof(struct %s)" % s
            ent[("field", s, "<struct>", "s")] = "@sizeof(struct %s)" % s
        v = evaluate(cc, incs, abi, tmp, ent)
        for s, fields in STRUCTS.items():
            for fld in list(fields) + ["<struct>"]:
                a, b = v[("field", s, fld, "a" if fld == "<struct>" else "o")], v[("field", s, fld, "s")]
                out[("field", s, fld)] = None if a is None or b is None else (a, b)
        return out


def compare(ref, tag, errno_names=()):
    """(differ, missing): keys the two sets define differently; keys the reference defines that the tag lacks.
    A tag matches when both are empty. The reference's None entries (it lacks the definition) are not compared."""
    differ, missing = [], []
    for k, v in sorted(ref.items(), key=lambda kv: str(kv[0])):
        if v is None:
            continue
        t = tag.get(k)
        if t is None:
            missing.append(k)
        elif t != v:
            differ.append((k, v, t))
    return differ, missing


STAGE_DIRS = (("sys", "sys/sys"), ("netinet", "sys/netinet"), ("netinet6", "sys/netinet6"), ("net", "sys/net"), ("x86", "sys/x86/include"))


def stage_tag(git_dir, ref, dest):
    """export the headers of a FreeBSD tree (git_dir has ref fetched, blobs may be filtered) to dest as an include tree:
    sys, netinet, netinet6, net, x86 and machine (arm64 where the tree has sys/arm64, else amd64). Returns the machine dir name."""
    def git(*a, **kw):
        return subprocess.run(["git", "-C", git_dir] + list(a), stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
    top = git("ls-tree", "--name-only", ref, "sys/").stdout.decode().split()
    arch = "arm64" if "sys/arm64" in top else "amd64"
    dirs = list(STAGE_DIRS) + [("machine", "sys/%s/include" % arch)]
    want = {}
    for name, path in dirs:
        r = git("ls-tree", "-r", ref, path + "/")
        for line in r.stdout.decode().splitlines():
            meta, p = line.split("\t", 1)
            if p.endswith(".h") and (name in ("machine", "x86") or "/" not in p[len(path) + 1:]):
                want[p] = (name, path, meta.split()[2])
    shas = [v[2] for v in want.values()]
    for i in range(0, len(shas), 200):   # one request per 200 blobs (a blob-by-blob lazy fetch is one round trip each)
        git("fetch", "-q", "--no-tags", "--no-write-fetch-head", "origin", *shas[i:i + 200])
    batch = git("cat-file", "--batch", input="".join(v[2] + "\n" for v in want.values()).encode())
    data, pos = batch.stdout, 0
    for p, (name, path, sha) in want.items():
        head, _, _ = data[pos:].partition(b"\n")
        size = int(head.split()[2])
        pos += len(head) + 1
        body = data[pos:pos + size]
        pos += size + 1
        out = os.path.join(dest, name, p[len(path) + 1:])
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(body)
    return arch


def split_errno(defs, errno_names):
    """the same set without the macros of sys/errno.h (errno_names: their names)"""
    return {k: v for k, v in defs.items() if not (k[0] == "macro" and k[1] in errno_names)}


def matrix(ref, tags, errno_names):
    """tags: {tag: {"defs": set} or {"error": text}}. Rows: (tag, status, differ, missing, differ_without_errno, missing_without_errno);
    a tag matches when differ and missing are both empty."""
    rows = []
    ref_ne = split_errno(ref, errno_names)
    for tag, r in sorted(tags.items()):
        if "defs" not in r:
            rows.append((tag, "no-extract", None, None, None, None))
            continue
        d, m = compare(ref, r["defs"])
        d2, m2 = compare(ref_ne, split_errno(r["defs"], errno_names))
        rows.append((tag, "match" if not d and not m else "match-without-errno" if not d2 and not m2 else "differs", len(d), len(m), len(d2), len(m2)))
    return rows


def to_json(s):
    return {json.dumps(k): v for k, v in s.items()}


def from_json(d):
    return {tuple(json.loads(k)): (tuple(v) if isinstance(v, list) else v) for k, v in d.items()}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract")
    e.add_argument("--cc", required=True)
    e.add_argument("--abi", default="lp64", choices=("lp64", "ilp32"))
    e.add_argument("--out", required=True)
    e.add_argument("incs", nargs="+")
    t = sub.add_parser("tag", help="extract the definitions of one FreeBSD tree: git dir, ref (fetched), out json")
    t.add_argument("--cc", required=True)
    t.add_argument("--abi", default="lp64", choices=("lp64", "ilp32"))
    t.add_argument("git_dir")
    t.add_argument("ref")
    t.add_argument("out")
    c = sub.add_parser("compare")
    c.add_argument("ref")
    c.add_argument("tag")
    a = ap.parse_args()
    if a.cmd == "tag":
        with tempfile.TemporaryDirectory() as d:
            arch = stage_tag(a.git_dir, a.ref, d)
            res = {"arch": arch}
            try:
                res["defs"] = to_json(extract(a.cc, [d], a.abi))
            except RuntimeError as e:
                res["error"] = str(e)[-1500:]
            json.dump(res, open(a.out, "w"), sort_keys=True)
    elif a.cmd == "extract":
        json.dump(to_json(extract(a.cc, a.incs, a.abi)), open(a.out, "w"), sort_keys=True)
    else:
        d, m = compare(from_json(json.load(open(a.ref))), from_json(json.load(open(a.tag))))
        print(json.dumps({"differ": len(d), "missing": len(m)}))
        return 1 if d or m else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
