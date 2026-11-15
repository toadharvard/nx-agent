#!/usr/bin/env python3
"""sdcheck.py - rule: the SD tree equals the reference manifest.

  manifest GAME_DIR -o game.manifest.json     file list (path, size, sha256) of the reference game install
  package  PKG_SD_DIR -o port.manifest.json   the same for the built package's sd/ tree (the port's own files)
  check --game G --port P --layout L SD_ROOT --fs fat32|exfat [--no-hash] [--json F]
  emit-c --game G --port P --layout L -o sd_manifest.inc

Exit: 0 green, 2 error, 3 incomplete (size-only pass). The manifest of the game is private (kept in the private
repository); nothing here carries game data. FAT32 limit: a file may hold at most 2^32 - 1 bytes (the spec text
"size >= 4 GiB - 1" is read as "size > 2^32 - 1": a file of exactly 2^32 - 1 bytes is representable)."""
import argparse
import hashlib
import json
import os
import stat
import sys

FORMAT = "fnvnx-sdmanifest/1"
FAT32_MAX = 2 ** 32 - 1
BAD_CHARS = set('<>:"/\\|?*')
CLASSES = ("missing", "extra", "size-diff", "hash-diff", "name-violation", "over-limit", "collision", "space")


class Fail(Exception):
    pass


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                return h.hexdigest()
            h.update(b)


def walk(root, hashing=True, strict=True):
    """[(relpath, size, sha256|None)] sorted; raises Fail on symlinks / non-regular files."""
    out = []
    for d, dirs, files in os.walk(root):
        dirs.sort()
        for n in sorted(dirs + files):
            p = os.path.join(d, n)
            m = os.lstat(p).st_mode
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            if stat.S_ISDIR(m):
                continue
            if not stat.S_ISREG(m):
                raise Fail("not a regular file (symlink or special): %s" % rel)
            out.append((rel, os.lstat(p).st_size, sha256(p) if hashing else None))
    out.sort(key=lambda e: e[0])
    return out


def fold_collisions(paths):
    seen, bad = {}, []
    for p in paths:
        k = p.casefold()
        if k in seen and seen[k] != p:
            bad.append([seen[k], p])
        seen.setdefault(k, p)
    return sorted(bad)


def name_violation(rel):
    for c in rel.split("/"):
        try:
            if len(c.encode("utf-16-le")) // 2 > 255:
                return "component longer than 255 UTF-16 units: %s" % c[:40]
        except UnicodeEncodeError:
            return "not valid Unicode: %r" % c
        if any(ch in BAD_CHARS or ord(ch) < 32 for ch in c):
            return "forbidden character in %r" % c
        if c.endswith(".") or c.endswith(" "):
            return "trailing dot or space in %r" % c
    return None


def build_manifest(root):
    files = walk(root)
    coll = fold_collisions([f[0] for f in files])
    if coll:
        raise Fail("case-fold collisions: %s" % "; ".join(" vs ".join(c) for c in coll[:20]))
    return {"format": FORMAT, "count": len(files), "total_bytes": sum(f[1] for f in files),
            "max_file_bytes": max([f[1] for f in files], default=0), "case_fold_collisions": [],
            "files": [{"path": p, "size": s, "sha256": h} for p, s, h in files]}


def dump(obj, path):
    with open(path, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=True, ensure_ascii=False)
        f.write("\n")


def load(path):
    with open(path) as f:
        m = json.load(f)
    if m.get("format") != FORMAT:
        raise Fail("%s: not a %s file" % (path, FORMAT))
    return m


def join(root, rel):
    return "/".join(x for x in (root.strip("/"), rel) if x)


def expected(game, port, layout):
    """{card path: entry}; a path required by both manifests is an error."""
    exp = {}
    for m, root in ((game, layout["game_root"]), (port, layout["port_root"])):
        for e in m["files"]:
            p = join(root, e["path"])
            if p in exp:
                raise Fail("game and port manifests both place %s" % p)
            exp[p] = e
    return exp


def in_writable(p, writable):
    return any(p == w.strip("/") or p.startswith(w.strip("/") + "/") for w in writable)


def check(a, game, port, layout):
    exp = expected(game, port, layout)
    res = {c: [] for c in CLASSES}
    ok = 0
    card = walk(a.sd_root, hashing=False)
    on = dict((p, s) for p, s, _ in card)
    for p in sorted(exp):
        e = exp[p]
        if p not in on:
            res["missing"].append(p)
        elif on[p] != e["size"]:
            res["size-diff"].append("%s (card %d, manifest %d)" % (p, on[p], e["size"]))
        elif not a.no_hash and sha256(os.path.join(a.sd_root, p)) != e["sha256"]:
            res["hash-diff"].append(p)
        else:
            ok += 1
    for p in sorted(on):
        if p not in exp and not in_writable(p, layout.get("writable", [])):
            res["extra"].append(p)
    for c in fold_collisions(sorted(set(on) | set(exp))):
        res["collision"].append(" vs ".join(c))
    if a.fs == "fat32":
        for p in sorted(set(on) | set(exp)):
            v = name_violation(p)
            if v:
                res["name-violation"].append("%s: %s" % (p, v))
        for p in sorted(on):
            if on[p] > FAT32_MAX:
                res["over-limit"].append("%s (%d > 2^32-1 = %d)" % (p, on[p], FAT32_MAX))
        for p in sorted(exp):
            if exp[p]["size"] > FAT32_MAX and p not in on:
                res["over-limit"].append("%s (manifest %d > %d)" % (p, exp[p]["size"], FAT32_MAX))
    need = layout.get("free_bytes_min", 0)
    sv = os.statvfs(a.sd_root)
    free = sv.f_bavail * sv.f_frsize
    if free < need:
        res["space"].append("free space: required %d bytes, found %d" % (need, free))
    return ok, res, need, free


def cmd_check(a):
    game, port, layout = load(a.game), load(a.port), json.load(open(a.layout))
    ok, res, need, free = check(a, game, port, layout)
    bad = any(res.values())
    verdict = "RED" if bad else ("INCOMPLETE" if a.no_hash else "GREEN")
    lines = ["sd root %s, fs %s, hashes %s" % (a.sd_root, a.fs, "NOT CHECKED" if a.no_hash else "checked"),
             "free space: required %d, found %d" % (need, free),
             "ok %d  " % ok + "  ".join("%s %d" % (c, len(res[c])) for c in CLASSES)]
    for c in CLASSES:
        if res[c]:
            lines.append("%s (%d):" % (c, len(res[c])))
            lines += ["  " + x for x in res[c][:20]]
    lines.append("sdcheck: %s ok=%d errors=%d%s" % (verdict, ok, sum(len(v) for v in res.values()),
                                                    " HASH NOT CHECKED (sizes only)" if a.no_hash else ""))
    print("\n".join(lines[:60]))
    if a.json:
        dump({"verdict": verdict, "ok": ok, "classes": res, "required_free": need, "free": free}, a.json)
    return 2 if bad else (3 if a.no_hash else 0)


def cesc(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def cmd_emit(a):
    exp = expected(load(a.game), load(a.port), json.load(open(a.layout)))
    rows = ["    { %s, %dULL }," % (cesc(p), exp[p]["size"]) for p in sorted(exp)]
    with open(a.o, "w") as f:
        f.write("/* generated; private build artifact, never committed to the public repository */\n"
                "/* sdcheck.py emit-c: every file the SD card must hold; paths relative to sdmc:/ */\n"
                "static const struct { const char *path; unsigned long long size; } fnvnx_sd_manifest[] = {\n")
        f.write("\n".join(rows) + "\n};\n")
        f.write("static const unsigned long fnvnx_sd_manifest_count = %d;\n" % len(rows))
    return 0


def main():
    ap = argparse.ArgumentParser(prog="sdcheck.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for n in ("manifest", "package"):
        s = sub.add_parser(n)
        s.add_argument("dir")
        s.add_argument("-o", required=True)
    for n in ("check", "emit-c"):
        s = sub.add_parser(n)
        s.add_argument("--game", required=True)
        s.add_argument("--port", required=True)
        s.add_argument("--layout", required=True)
        if n == "check":
            s.add_argument("sd_root")
            s.add_argument("--fs", choices=("fat32", "exfat"), required=True)
            s.add_argument("--no-hash", action="store_true")
            s.add_argument("--json")
        else:
            s.add_argument("-o", required=True)
    a = ap.parse_args()
    try:
        if a.cmd in ("manifest", "package"):
            dump(build_manifest(a.dir), a.o)
            return 0
        return cmd_check(a) if a.cmd == "check" else cmd_emit(a)
    except (Fail, OSError, ValueError, KeyError) as e:
        print("sdcheck: ERROR %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
