#!/usr/bin/env python3
"""NX1 check 9: no shader is compiled on the console (owner rule).

    check_shaders.py [--nro fnv.nro] [--guest-elf fnvnx.elf] [--host-elf host.elf] [--ref-bin <switch-tools bin>]
                     [--game-pak translated.nxsp --game-sdp Data/Shaders]   (or $FNVNX_GAME_SHADERS_PAK, $FNVNX_GAME_SHADERS)

METRIC of this check: shaders compiled at run time = 0, and DKSH coverage of every variant the renderer requests.
  pack      fnv.nro's asset RomFS (romfs.py, byte-checked against switch-tools build_romfs with --ref-bin) holds
            shaders.nxsp (toolchain/nro/shaderpack.py: one pack, indexed by family/variant, every blob a DKSH with a
            matching crc32) and no GLSL file;
  coverage  every shader render/native/shaders/build.py generates (all families, nx-variants and epilogue
            permutations, listed by running it without uam) and every name the renderer asks for by literal
            (BlobShader(..., "name", ...) in render/native/n1) has a DKSH in the pack, for its stage;
  guest     (with --guest-elf) the guest links the console table (nx_shader_find) and carries the pack's id in its
            image (the table belongs to this pack), and no GLSL text (#version) anywhere in it;
  compiler  no shader compiler in the guest or the host (no uam/glslang/SPIR-V/GLSL symbols); the device backend's
            shader_create (render/native/host/nxgpu.c) accepts DKSH only;
  game      the game's own D3D9 shaders are translated at install time from the owner's shaderpackage*.sdp by
            sdp2dksh (D6) into the owner's local translated.nxsp by toolchain/nro/shaders_nx.py, which drives the
            render machine's render/native/tools/nxsp_pack.py (game.packer: shaders_nx.py passes
            tests/nx1/test_shaders_nx.py with that packer); that file and the .sdp never enter nx-agent (git). Given the
            owner's pack and packages (on the owner's side only), game.coverage checks every program in them has a
            DKSH of its stage in the pack (shaders_nx.py --verify), and game.outside_repo that neither is in nx-agent.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "toolchain", "tools"))
sys.path.insert(0, os.path.join(ROOT, "toolchain", "nro"))
import romfs  # noqa: E402
import shaderpack  # noqa: E402
from check_fs import strip_comments  # noqa: E402
from check_nro import Checks  # noqa: E402

COMPILER_SYMS = re.compile(r'glslang|\buam_|spirv|SPIRV|GlslCompile|glsl_compile|nvnProgramSetShaders|CompileShader',
                           re.I)


def nro_romfs(nro: bytes) -> bytes | None:
    if nro[0x10:0x14] != b"NRO0":
        return None
    size = struct.unpack_from("<I", nro, 0x18)[0]
    if nro[size:size + 4] != b"ASET":
        return None
    off, n = struct.unpack_from("<QQ", nro, size + 8 + 32)
    return nro[size + off:size + off + n] if n else None


def generated_shaders() -> set[tuple[str, int]] | None:
    """(name, stage) of every shader build.py generates, from a GLSL-only run (no uam needed)."""
    with tempfile.TemporaryDirectory() as t:
        r = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "render", "native", "shaders", "build.py"), t],
                           capture_output=True, text=True, errors="replace", env={k: v for k, v in os.environ.items()
                                                                                  if k != "UAM"})
        if r.returncode:
            return None
        return {(m.group(1), 0 if m.group(2) == "vert" else 1) for f in os.listdir(t)
                if (m := re.match(r"^([a-z0-9_]+)\.(vert|frag)\.glsl$", f))}


def requested_literals() -> set[tuple[str, int]]:
    out = set()
    n1 = os.path.join(ROOT, "render", "native", "n1")
    for f in os.listdir(n1):
        if f.endswith(".cpp"):
            s = strip_comments(open(os.path.join(n1, f), errors="replace").read())
            for m in re.finditer(r'BlobShader\(\s*\w+\s*,\s*"([a-z0-9_]+)"\s*,\s*NxStage_(Vertex|Fragment)', s):
                out.add((m.group(1), 0 if m.group(2) == "Vertex" else 1))
    return out


def syms(nm, elf):
    return subprocess.run([nm, elf], capture_output=True, text=True, errors="replace").stdout if nm and elf else ""


def check(nro_path, guest_elf, host_elf, ref_bin, game_pak=None, game_sdp=None) -> Checks:
    c = Checks()
    pack = None
    if nro_path:
        nro = open(nro_path, "rb").read()
        img = nro_romfs(nro)
        if c.ok("pack.romfs", img is not None, "fnv.nro has no RomFS asset: the precompiled shaders are not in the package"):
            files = romfs.read(img)
            c.ok("pack.no_glsl", not [n for n in files if n.endswith(".glsl")], "GLSL sources in the RomFS")
            if c.ok("pack.present", "shaders.nxsp" in files, f"no shaders.nxsp in the RomFS ({sorted(files)})"):
                try:
                    pack = shaderpack.parse(files["shaders.nxsp"])
                    c.ok("pack.valid", all(b[:4] == b"DKSH" for *_, b in pack[0]), "a pack entry is not a DKSH")
                except ValueError as e:
                    c.ok("pack.valid", False, str(e))
            if ref_bin and os.path.exists(os.path.join(ref_bin, "build_romfs")):
                with tempfile.TemporaryDirectory() as t:
                    d = os.path.join(t, "d")
                    os.makedirs(d)
                    for n, b in files.items():
                        open(os.path.join(d, n), "wb").write(b)
                    subprocess.run([os.path.join(ref_bin, "build_romfs"), d, os.path.join(t, "r")], capture_output=True)
                    ref = open(os.path.join(t, "r"), "rb").read() if os.path.exists(os.path.join(t, "r")) else b""
                    c.ok("pack.romfs_roundtrip", ref == img, "switch-tools build_romfs makes a different RomFS")
    else:
        c.ok("pack.romfs", False, "no fnv.nro: the shader pack cannot be checked")
    gen = generated_shaders()
    c.ok("coverage.generated", gen is not None and len(gen) > 0, "render/native/shaders/build.py did not run")
    lits = requested_literals()
    if gen is not None:
        c.ok("coverage.literals_generated", lits <= gen, f"the renderer asks for shaders build.py does not make: "
             f"{sorted(lits - gen)[:6]}")
    if pack and gen is not None:
        have = {(n, st) for n, _f, _v, st, b in pack[0] if b}
        miss = sorted((gen | lits) - have)
        c.ok("coverage.dksh", not miss, f"{len(miss)} shaders without DKSH in the pack: {miss[:6]}")
        print(f"info coverage: {len(have & (gen | lits))}/{len(gen | lits)} shaders precompiled, "
              f"{len({f for _, f, *_ in pack[0]})} families; shaders compiled at run time: 0 by construction")
    nm = shutil.which("aarch64-linux-gnu-nm") or shutil.which("nm")
    if guest_elf:
        g = open(guest_elf, "rb").read()
        gs = syms(nm, guest_elf)
        c.ok("guest.table", "nx_shader_find" in gs, "the guest links no shader table (no native renderer)")
        c.ok("guest.no_glsl", b"#version 4" not in g, "GLSL text (#version) in the guest image")
        if pack:
            c.ok("guest.pack_id", pack[1] in g, "the guest's shader table was generated for another pack")
        c.ok("compiler.guest", not COMPILER_SYMS.search(gs), "shader compiler symbols in the guest")
    else:
        c.ok("guest.table", False, "no guest ELF")
    if host_elf:
        c.ok("compiler.host", not COMPILER_SYMS.search(syms(nm, host_elf)), "shader compiler symbols in the host")
    gpu = strip_comments(open(os.path.join(ROOT, "render", "native", "host", "nxgpu.c"), errors="replace").read())
    sc = re.search(r'api_shader_create\s*\([^)]*\)\s*\{(.*?)\n\}', gpu, re.S)
    c.ok("compiler.backend_dksh_only", sc is not None and "0x48534B44" in sc.group(1) and "glsl" not in sc.group(1),
         "the device backend's shader_create does not insist on DKSH")
    tracked = subprocess.run(["git", "-C", ROOT, "ls-files"], capture_output=True, text=True).stdout.splitlines()
    leaked = [f for f in tracked if re.search(r'(^|/)shaders_nx\.pak$|translated\.nxsp$|shaderpackage\d*\.sdp$', f, re.I)]
    c.ok("game.not_in_git", not leaked, f"the owner's shader files are tracked in nx-agent: {leaked}")
    packer = os.path.join(ROOT, "toolchain", "nro", "shaders_nx.py")
    t = subprocess.run([sys.executable, "-I", os.path.join(HERE, "test_shaders_nx.py")], capture_output=True, text=True,
                       errors="replace") if os.path.isfile(packer) else None
    c.ok("game.packer", t is not None and t.returncode == 0,
         "no install-time packer (toolchain/nro/shaders_nx.py)" if t is None else
         "shaders_nx.py fails its test: " + (t.stdout + t.stderr).strip().splitlines()[-1][:160])
    if game_pak or game_sdp:
        r = subprocess.run([sys.executable, "-I", packer, "--verify", game_pak or "", game_sdp or ""],
                           capture_output=True, text=True, errors="replace") if game_pak and game_sdp else None
        c.ok("game.coverage", r is not None and r.returncode == 0,
             "give both the pack and the packages" if r is None else (r.stdout + r.stderr).strip()[:200])
        if r is not None:
            print("info game: " + r.stdout.strip().splitlines()[0] if r.stdout.strip() else "info game: no output")
        inside = [x for x in (game_pak, game_sdp) if x and os.path.realpath(x).startswith(ROOT + os.sep)]
        c.ok("game.outside_repo", not inside, f"the owner's shader files are inside nx-agent: {inside}")
    return c


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--nro")
    ap.add_argument("--guest-elf")
    ap.add_argument("--host-elf")
    ap.add_argument("--ref-bin")
    ap.add_argument("--game-pak", default=os.environ.get("FNVNX_GAME_SHADERS_PAK"))
    ap.add_argument("--game-sdp", default=os.environ.get("FNVNX_GAME_SHADERS"))
    a = ap.parse_args(argv)
    c = check(a.nro, a.guest_elf, a.host_elf, a.ref_bin, a.game_pak, a.game_sdp)
    c.print()
    print(f"shaders: {sum(r[1] for r in c.rows)}/{len(c.rows)} pass -> {'PASS' if c.passed else 'FAIL'}")
    return 0 if c.passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
