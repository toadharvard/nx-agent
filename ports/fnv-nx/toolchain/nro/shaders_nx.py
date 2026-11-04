#!/usr/bin/env python3
"""shaders_nx.py - the game's own D3D9 shaders, precompiled for the console at install time (D6; owner rule: no
shader is compiled on the console). Runs on the owner's side (PC or agent container), never on a farm that
publishes to nx-agent.

    shaders_nx.py <Data/Shaders dir | shaderpackage*.sdp ...> -o <shaders_nx.pak> [--sdp2dksh BIN] [--work DIR]
                  [--partial] [-j N]
    shaders_nx.py --verify <shaders_nx.pak> <Data/Shaders dir | shaderpackage*.sdp ...>

Input: the player's shaderpackageNNN.sdp (render/shaders/SDP.md). Every program in them is translated by the
render machine's sdp2dksh (render/shaders: MojoShader glsldk -> uam -> DKSH, its CLI as the API: `-o DIR --blob
FILE...` gives DIR/<hash>.dksh per bytecode and DIR/index.tsv), each unique bytecode once, -j jobs in parallel. sdp2dksh comes from --sdp2dksh, $SDP2DKSH,
render/shaders/build/host or ~/.cache/fnvnx-shaders/host; else render/shaders/build.sh builds it into the cache.

Output: shaders_nx.pak, the NXSP v1 format of the renderer's shaders.nxsp (toolchain/nro/shaderpack.py:
256-aligned blobs, crc32 each, pack id), one entry per unique bytecode, sorted by hash:
    name = the bytecode hash (fnvnx_shader_hash, FNV-1a 64, 16 lowercase hex digits; what the translated path looks
           up), family = the first package holding it (shaderpackage019), variant = the model (vs_3_0), stage
           0 vertex / 1 pixel.
and shaders_nx.json next to it: pack id, counts, each package's sha256 and entry count, the programs left out.
A program sdp2dksh cannot translate is an error (exit 1, no pack) unless --partial, which packs the rest and lists
it in the manifest. The host finds the pack at FNVNX_SD_SHADERS_NX (sdmc:/switch/fnv/shaders_nx.pak, next to the
game). Both files are game-derived: they stay on the owner's side and never enter nx-agent (check 9, game.*).
--verify re-reads the .sdp and checks every program has a DKSH of its stage in the pack (exit 0 = full coverage).
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import shaderpack  # noqa: E402

CACHE = os.path.join(os.path.expanduser("~"), ".cache", "fnvnx-shaders")


def fnv1a64(b: bytes) -> int:
    h = 0xcbf29ce484222325
    for x in b:
        h = ((h ^ x) * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return h


def sdp_entries(path: str, blobs: dict | None = None) -> list[tuple[str, int, str, int]]:
    """[(entry name, stage, model, hash)] of one package (SDP.md; independent of sdp2dksh's reader); blobs, when
    given, collects {hash: bytecode}."""
    b = open(path, "rb").read()
    magic, count, size = struct.unpack_from("<III", b, 0)
    if magic != 100 or size != len(b) - 12:
        raise ValueError(f"{path}: not a shader package")
    out, o = [], 12
    for _ in range(count):
        name = b[o:o + 256].split(b"\0")[0].decode("latin-1")
        n = struct.unpack_from("<I", b, o + 256)[0]
        bc = b[o + 260:o + 260 + n]
        o += 260 + n
        if blobs is not None:
            blobs.setdefault(fnv1a64(bc), bc)
        ver = struct.unpack_from("<I", bc, 0)[0] if n >= 4 else 0
        stage = {0xFFFE: 0, 0xFFFF: 1}.get(ver >> 16, -1)
        model = "%s_%d_%d" % ("vs" if stage == 0 else "ps", (ver >> 8) & 0xFF, ver & 0xFF)
        out.append((name, stage, model, fnv1a64(bc)))
    if o != len(b):
        raise ValueError(f"{path}: trailing bytes")
    return out


def packages(args: list[str]) -> list[str]:
    out = []
    for a in args:
        out += sorted(glob.glob(os.path.join(a, "shaderpackage*.sdp"))) if os.path.isdir(a) else [a]
    if not out:
        raise SystemExit("shaders_nx: no shaderpackage*.sdp given")
    return out


def find_sdp2dksh(given: str | None) -> str:
    for c in (given, os.environ.get("SDP2DKSH"), os.path.join(ROOT, "render", "shaders", "build", "host", "sdp2dksh"),
              os.path.join(CACHE, "host", "sdp2dksh")):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    env = dict(os.environ, FNVNX_SHADERS_DEPS=os.path.join(CACHE, "deps"), FNVNX_SHADERS_BUILD=os.path.join(CACHE, "host"))
    print("shaders_nx: building sdp2dksh (render/shaders/build.sh) into %s" % CACHE, file=sys.stderr)
    subprocess.run(["bash", os.path.join(ROOT, "render", "shaders", "build.sh")], env=env, check=True,
                   stdout=sys.stderr)
    return os.path.join(CACHE, "host", "sdp2dksh")


def build(sdps, out, tool, work, partial, jobs) -> int:
    progs = {}            # hash -> (package stem, model, stage, [package:entry])
    pk, blobs = [], {}
    for p in sdps:
        stem = os.path.splitext(os.path.basename(p))[0]
        ents = sdp_entries(p, blobs)
        pk.append({"package": os.path.basename(p), "sha256": hashlib.sha256(open(p, "rb").read()).hexdigest(),
                   "entries": len(ents)})
        for name, stage, model, h in ents:
            progs.setdefault(h, [stem, model, stage, []])[3].append(f"{stem}:{name}")
    # each unique bytecode once (a package repeats most programs of the others: 15,791 entries, 1,809 programs in
    # the owner's 16), through sdp2dksh --blob, in one job per CPU, each with its own output directory
    bc = os.path.join(work, "bytecode")
    os.makedirs(bc, exist_ok=True)
    files = []
    for h in sorted(blobs):
        f = os.path.join(bc, "%016x.bin" % h)
        open(f, "wb").write(blobs[h])
        files.append(f)
    n = max(1, min(jobs, len(files)))
    procs = []
    for k in range(n):
        out_k = os.path.join(work, "j%d" % k)
        os.makedirs(out_k, exist_ok=True)
        procs.append((out_k, subprocess.Popen([tool, "-o", out_k, "--blob"] + files[k::n], stdout=subprocess.DEVNULL,
                                              stderr=open(os.path.join(out_k, "sdp2dksh.err"), "w"))))
    failed = {}
    for out_k, pr in procs:
        pr.wait()
        idx = os.path.join(out_k, "index.tsv")
        for ln in open(idx, errors="replace") if os.path.isfile(idx) else ():
            f = ln.rstrip("\n").split("\t")
            if len(f) >= 5 and f[4] != "ok" and f[0] != "hash":   # the first line is the column header
                failed[int(f[0], 16)] = f[-1]
        for f in os.listdir(out_k):
            if f.endswith(".dksh"):
                os.replace(os.path.join(out_k, f), os.path.join(work, f))
    rows, missing = [], []
    for h in sorted(progs):
        stem, model, stage, where = progs[h]
        d = os.path.join(work, "%016x.dksh" % h)
        blob = open(d, "rb").read() if os.path.isfile(d) else b""
        if stage < 0 or blob[:4] != b"DKSH" or h in failed:
            missing.append({"hash": "%016x" % h, "model": model, "where": where[:4],
                            "error": failed.get(h, "no DKSH" if stage >= 0 else "not a shader")})
            continue
        rows.append(("%016x" % h, stem, model, stage, blob))
    total = len(progs)
    if missing and not partial:
        print(f"shaders_nx: {len(missing)} of {total} programs have no DKSH (no pack written; a missing DKSH is a "
              f"package error, nothing compiles on the console; --partial packs the rest):", file=sys.stderr)
        for m in missing[:10]:
            print(f"  {m['hash']} {m['model']} {m['where'][0]}: {m['error'][:120]}", file=sys.stderr)
        return 1
    pack, pid = shaderpack.pack_rows(rows)
    open(out, "wb").write(pack)
    man = {"format": "fnvnx-shaders-nx/1", "pack": os.path.basename(out), "pack_id": pid.hex(), "bytes": len(pack),
           "programs": total, "packed": len(rows), "entries": sum(p["entries"] for p in pk), "packages": pk,
           "missing": missing, "sdp2dksh": tool}
    json.dump(man, open(os.path.splitext(out)[0] + ".json", "w"), indent=1)
    print(f"shaders_nx: {len(rows)}/{total} programs ({man['entries']} entries in {len(pk)} packages) -> {out}, "
          f"{len(pack)} bytes, pack id {pid.hex()}" + (f"; {len(missing)} left out (--partial)" if missing else ""))
    return 0


def verify(pak, sdps) -> int:
    rows, pid = shaderpack.parse(open(pak, "rb").read())
    have = {(n, st) for n, _f, _v, st, b in rows if b[:4] == b"DKSH"}
    want, miss = set(), []
    for p in sdps:
        for name, stage, model, h in sdp_entries(p):
            want.add(("%016x" % h, stage))
            if ("%016x" % h, stage) not in have:
                miss.append(f"{os.path.basename(p)}:{name} ({model}, {h:016x})")
    sorted_ok = [r[0] for r in rows] == sorted(r[0] for r in rows)
    print(f"shaders_nx verify: {len(want) - len({m.rsplit(', ', 1)[1] for m in miss})}/{len(want)} programs have "
          f"a DKSH, pack id {pid.hex()}, index sorted {sorted_ok}")
    for m in miss[:10]:
        print("  missing", m)
    return 0 if not miss and sorted_ok else 1


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("-o", dest="out")
    ap.add_argument("--sdp2dksh")
    ap.add_argument("--work")
    ap.add_argument("--partial", action="store_true")
    ap.add_argument("-j", dest="jobs", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--verify")
    a = ap.parse_args(argv)
    if a.verify:
        return verify(a.verify, packages(a.inputs))
    if not a.out:
        ap.error("-o shaders_nx.pak is required")
    sdps = packages(a.inputs)
    tool = find_sdp2dksh(a.sdp2dksh)
    work = a.work or tempfile.mkdtemp(prefix="shaders_nx-")
    os.makedirs(work, exist_ok=True)
    try:
        return build(sdps, a.out, tool, work, a.partial, a.jobs)
    finally:
        if not a.work:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
