#!/usr/bin/env python3
"""shaders_nx.py - the game's own D3D9 shaders, precompiled for the console at install time (D6; owner rule: no
shader is compiled on the console). Runs on the owner's side (PC or agent container), never on a farm that
publishes to nx-agent. It drives the render machine's tools and adds no format of its own.

    shaders_nx.py <Data/Shaders dir | shaderpackage*.sdp ...> -o <translated.nxsp> [--names shaderpackage019.sdp]
                  [--sdp2dksh BIN] [--work DIR] [--partial] [-j N]
    shaders_nx.py --verify <translated.nxsp> <Data/Shaders dir | shaderpackage*.sdp ...>

1. Every program of the player's shaderpackageNNN.sdp (render/shaders/SDP.md) is translated by S6b's sdp2dksh
   (render/shaders: MojoShader glsldk -> uam -> DKSH; `-o DIR --blob FILE...` gives DIR/<hash>.dksh + .json and
   DIR/index.tsv). Each unique bytecode is translated once (the owner's 16 packages: 15,791 entries, 1,809 programs),
   each in a fresh process (sdp2dksh's output depends on the programs a process translated before), -j at a time. sdp2dksh comes from --sdp2dksh, $SDP2DKSH, render/shaders/build/host or
   ~/.cache/fnvnx-shaders/host; else render/shaders/build.sh builds it into the cache (uam.patch needs meson >= 1.4).
2. The results go into one directory with the index.tsv sdp2dksh would write for the packages (one line per
   package entry), and the render machine's render/native/tools/nxsp_pack.py packs it: translated.nxsp, NXSP v1
   (toolchain/nro/shaderpack.py) with rows `translated` (name = fnvnx_shader_hash, variant = model/input mask,
   the DKSH) and `translated-name` for --names' package (RENDER_NATIVE.md 4.5). Never `translated-glsl`: this is a
   console pack. With --check (default): a program without DKSH stops it (exit 1, no pack); --partial packs the rest.
3. translated.json next to the pack: pack id, counts, each package's sha256 and entry count, programs left out.

The owner copies translated.nxsp to FNVNX_SD_TRANSLATED (sdmc:/switch/fnv/translated.nxsp, next to the game; not
fnv.nro's RomFS: the farm builds fnv.nro, and this pack is made from the player's files). The host checks its header
at start and logs it. Both files are game-derived: they stay on the owner's side and never enter nx-agent (check 9).
--verify re-reads the .sdp: every program has a `translated` DKSH of its stage, no GLSL rows, the pack parses.
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
from concurrent.futures import ThreadPoolExecutor

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


def build(sdps, out, tool, work, partial, jobs, names) -> int:
    pk, blobs, ents = [], {}, []        # ents: (package path, entry name, hash)
    for p in sdps:
        e = sdp_entries(p, blobs)
        pk.append({"package": os.path.basename(p), "sha256": hashlib.sha256(open(p, "rb").read()).hexdigest(),
                   "entries": len(e)})
        ents += [(p, name, h) for name, _st, _m, h in e]
    packer = os.environ.get("NXSP_PACK") or os.path.join(ROOT, "render", "native", "tools", "nxsp_pack.py")
    if not os.path.isfile(packer):
        raise SystemExit("shaders_nx: no render/native/tools/nxsp_pack.py (the render machine's translated.nxsp packer)")
    bc, pdir = os.path.join(work, "bytecode"), os.path.join(work, "pack")
    os.makedirs(bc, exist_ok=True)
    os.makedirs(pdir, exist_ok=True)
    # each unique bytecode once, every one in a fresh sdp2dksh process (--blob), -j at a time. sdp2dksh's output
    # depends on what the same process translated before (measured: 1 previous program changes a byte of Maxwell
    # code; its serial package run differs from fresh processes in 47 of 776 programs of packages 002+003), so only
    # a fresh process per program gives a pack that does not depend on order or job count. It is also faster: a
    # long-lived process slows down (776 programs: 5.4 s fresh on 4 CPUs).
    files = []
    for h in sorted(blobs):
        f = os.path.join(bc, "%016x.bin" % h)
        open(f, "wb").write(blobs[h])
        files.append(f)

    def one(i_f):
        i, f = i_f
        out_k = os.path.join(work, "p%05d" % i)
        os.makedirs(out_k, exist_ok=True)
        with open(os.path.join(out_k, "sdp2dksh.err"), "w") as err:
            subprocess.run([tool, "-o", out_k, "--blob", f], stdout=subprocess.DEVNULL, stderr=err)
        return out_k

    header, stats = None, {}            # hash -> the index.tsv columns after name
    with ThreadPoolExecutor(max(1, jobs)) as ex:
        outs = list(ex.map(one, enumerate(files)))
    for out_k in outs:
        idx = os.path.join(out_k, "index.tsv")
        for ln in open(idx, errors="replace") if os.path.isfile(idx) else ():
            f = ln.rstrip("\n").split("\t")
            if f[0] == "hash":
                header = ln
            elif len(f) >= 5:
                stats[int(f[0], 16)] = f[3:]
        for f in os.listdir(out_k):
            if f.endswith((".dksh", ".json")):
                os.replace(os.path.join(out_k, f), os.path.join(pdir, f))
        shutil.rmtree(out_k, ignore_errors=True)
    # the index.tsv of sdp2dksh -o over the packages themselves: one line per entry (nxsp_pack's --names reads the
    # package and entry names); a program sdp2dksh did not list is a failure
    with open(os.path.join(pdir, "index.tsv"), "w") as idx:
        idx.write(header or "hash\tpackage\tname\tmodel\tstatus\n")
        for p, name, h in ents:
            st = stats.get(h) or ["??", "fail", "0", "0", "0", "0", "0", "sdp2dksh gave no result"]
            idx.write("%016x\t%s\t%s\t%s\n" % (h, p, name, "\t".join(st)))
    missing = sorted({"%016x" % h for h in blobs if (stats.get(h) or ["", "fail"])[1] != "ok"
                      or not os.path.isfile(os.path.join(pdir, "%016x.dksh" % h))})
    argv = [sys.executable, "-I", packer, pdir, out, "--names", names] + ([] if partial else ["--check"])
    r = subprocess.run(argv, capture_output=True, text=True, errors="replace")
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    if r.returncode or not os.path.isfile(out):
        if os.path.exists(out) and r.returncode:
            os.remove(out)
        print(f"shaders_nx: nxsp_pack.py exit {r.returncode}: no pack ({len(missing)} of {len(blobs)} programs have "
              f"no DKSH; a missing DKSH is a package error, nothing compiles on the console; --partial packs the "
              f"rest)", file=sys.stderr)
        return 1
    rows, pid = shaderpack.parse(open(out, "rb").read())
    man = {"format": "fnvnx-translated-install/1", "pack": os.path.basename(out), "pack_id": pid.hex(),
           "bytes": os.path.getsize(out), "programs": len(blobs),
           "packed": sum(1 for x in rows if x[1] == "translated"),
           "names": sum(1 for x in rows if x[1] == "translated-name"), "names_package": names,
           "entries": len(ents), "packages": pk, "missing": [{"hash": h, "error": (stats.get(int(h, 16)) or
                                                                                   ["", "", "no result"])[-1]}
                                                              for h in missing], "sdp2dksh": tool,
           "packer": "render/native/tools/nxsp_pack.py"}
    json.dump(man, open(os.path.splitext(out)[0] + ".json", "w"), indent=1)
    print(f"shaders_nx: {man['packed']}/{len(blobs)} programs ({len(ents)} entries in {len(pk)} packages), "
          f"{man['names']} names -> {out}, {man['bytes']} bytes, pack id {pid.hex()}"
          + (f"; {len(missing)} left out (--partial)" if missing else ""))
    return 0


def verify(pak, sdps) -> int:
    rows, pid = shaderpack.parse(open(pak, "rb").read())
    have = {(n, st) for n, fam, _v, st, b in rows if fam == "translated" and b[:4] == b"DKSH"}
    glsl = sum(1 for _n, fam, *_ in rows if fam == "translated-glsl")
    want, miss = set(), []
    for p in sdps:
        for name, stage, model, h in sdp_entries(p):
            k = ("%016x" % h, stage)
            want.add(k)
            if k not in have:
                miss.append((k, f"{os.path.basename(p)}:{name} ({model}, {h:016x})"))
    hashes = [n for n, fam, *_ in rows if fam == "translated"]
    sorted_ok = hashes == sorted(hashes)
    nmiss = len({k for k, _ in miss})
    print(f"shaders_nx verify: {len(want) - nmiss}/{len(want)} programs have a DKSH, {glsl} GLSL rows, pack id "
          f"{pid.hex()}, index sorted {sorted_ok}")
    for _k, m in miss[:10]:
        print("  missing", m)
    return 0 if not miss and sorted_ok and not glsl else 1


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("-o", dest="out")
    ap.add_argument("--sdp2dksh")
    ap.add_argument("--work")
    ap.add_argument("--partial", action="store_true")
    ap.add_argument("--names", default="shaderpackage019.sdp", help="the package whose program names the pack maps")
    ap.add_argument("-j", dest="jobs", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--verify")
    a = ap.parse_args(argv)
    if a.verify:
        return verify(a.verify, packages(a.inputs))
    if not a.out:
        ap.error("-o translated.nxsp is required")
    sdps = packages(a.inputs)
    tool = find_sdp2dksh(a.sdp2dksh)
    work = a.work or tempfile.mkdtemp(prefix="shaders_nx-")
    os.makedirs(work, exist_ok=True)
    try:
        return build(sdps, a.out, tool, work, a.partial, a.jobs, a.names)
    finally:
        if not a.work:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
