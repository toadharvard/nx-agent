#!/usr/bin/env python3
"""tests/nx1: toolchain/nro/shaders_nx.py on synthetic packages with a stand-in sdp2dksh (no game data, no uam) and
the render machine's real packer (render/native/tools/nxsp_pack.py, or $NXSP_PACK): translated.nxsp is NXSP v1, a
`translated` row per unique bytecode sorted by its hash, `translated-name` rows for --names' package, no GLSL;
--verify sees full coverage; a program without DKSH stops the build (no pack) and --partial packs the rest and lists
it. Without the packer in the tree: exit 2 (skip), which check 9 reports as not passing."""
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "toolchain", "nro"))
import shaderpack  # noqa: E402
import shaders_nx  # noqa: E402

STUB = r'''#!/usr/bin/env python3
import os, struct, sys
sys.path.insert(0, %r)
import shaders_nx
out = sys.argv[sys.argv.index("-o") + 1]
idx = open(os.path.join(out, "index.tsv"), "w")
idx.write("hash\tpackage\tname\tmodel\tstatus\td3d_instr\tglsl_stmts\tdk_instr\tdk_gprs\tdk_code_bytes\terror\n")
assert sys.argv[sys.argv.index("-o") + 2] == "--blob"
for p in sys.argv[sys.argv.index("-o") + 3:]:
        bc = open(p, "rb").read(); name = os.path.basename(p); h = shaders_nx.fnv1a64(bc)
        bad = b"BAD" in bc
        ver = struct.unpack_from("<I", bc, 0)[0]
        model = "%%s_%%d_%%d" %% ("vs" if ver >> 16 == 0xFFFE else "ps", (ver >> 8) & 0xFF, ver & 0xFF)
        if not bad:
            seen = sys.argv.index(p) - sys.argv.index("--blob") - 1   # programs this process translated before
            open(os.path.join(out, "%%016x.dksh" %% h), "wb").write(b"DKSH" + bytes([seen]) + bytes(59) + bc)
        idx.write("%%016x\t-\t%%s\t%%s\t%%s\t0\t0\t0\t0\t0\t%%s\n" %% (h, name, model, "fail" if bad else "ok",
                  "uam: error" if bad else ""))
'''


def sdp(path, entries):
    body = b"".join(n.encode().ljust(256, b"\0") + struct.pack("<I", len(bc)) + bc for n, bc in entries)
    open(path, "wb").write(struct.pack("<III", 100, len(entries), len(body)) + body)


def prog(stage, tag):
    return struct.pack("<I", (0xFFFE if stage == 0 else 0xFFFF) << 16 | 0x0300) + tag + struct.pack("<I", 0xFFFF)


def main():
    bad = 0
    packer = os.environ.get("NXSP_PACK") or os.path.join(ROOT, "render", "native", "tools", "nxsp_pack.py")
    if not os.path.isfile(packer):
        print("test_shaders_nx: skip: no render/native/tools/nxsp_pack.py (the render machine's packer) in this tree")
        return 2

    def ok(cond, what):
        nonlocal bad
        print(("ok   " if cond else "FAIL ") + what)
        bad += not cond

    with tempfile.TemporaryDirectory() as t:
        stub = os.path.join(t, "sdp2dksh")
        open(stub, "w").write(STUB % os.path.join(ROOT, "toolchain", "nro"))
        os.chmod(stub, 0o755)
        d = os.path.join(t, "Shaders")
        os.makedirs(d)
        sdp(os.path.join(d, "shaderpackage001.sdp"), [("A.vso", prog(0, b"aaaa")), ("A.pso", prog(1, b"bbbb"))])
        sdp(os.path.join(d, "shaderpackage002.sdp"), [("A.vso", prog(0, b"aaaa")), ("C.vso", prog(0, b"cccc"))])
        pak = os.path.join(t, "translated.nxsp")
        run = lambda *a: subprocess.run([sys.executable, "-I", os.path.join(ROOT, "toolchain", "nro", "shaders_nx.py"),
                                         *a], capture_output=True, text=True)
        r = run(d, "-o", pak, "--sdp2dksh", stub, "--names", "shaderpackage001.sdp")
        ok(r.returncode == 0 and os.path.isfile(pak), "build: exit 0 and a pack (%s)" % (r.stderr.strip()[-200:]))
        allrows, pid = shaderpack.parse(open(pak, "rb").read())
        rows = [x for x in allrows if x[1] == "translated"]
        names = [x[0] for x in rows]
        ok(len(rows) == 3, "duplicate bytecode packed once: 3 programs from 4 entries")
        ok(names == sorted(names) and all(len(n) == 16 for n in names), "index sorted by 16-digit bytecode hash")
        ok({x[3] for x in rows} == {0, 1} and rows[names.index(
            "%016x" % shaders_nx.fnv1a64(prog(1, b"bbbb")))][3] == 1, "stage from the version token")
        nm = {x[0]: x[2] for x in allrows if x[1] == "translated-name"}
        ok(nm == {"A.vso": "%016x" % shaders_nx.fnv1a64(prog(0, b"aaaa")),
                  "A.pso": "%016x" % shaders_nx.fnv1a64(prog(1, b"bbbb"))},
           "translated-name rows: --names' package entries -> their hash (%s)" % sorted(nm))
        ok(not [x for x in allrows if x[1] == "translated-glsl"], "no GLSL rows (a console pack)")
        ok(all(x[4][4] == 0 for x in rows), "every program translated in a fresh process (the stand-in, like "
           "sdp2dksh, changes its output after earlier programs in the same process)")
        pak1 = os.path.join(t, "j1.nxsp")
        run(d, "-o", pak1, "--sdp2dksh", stub, "--names", "shaderpackage001.sdp", "-j", "1")
        ok(open(pak1, "rb").read() == open(pak, "rb").read(), "-j 1 and -j N give the same bytes")
        ok(run("--verify", pak, d).returncode == 0, "--verify: full coverage")
        sdp(os.path.join(d, "shaderpackage003.sdp"), [("X.vso", prog(0, b"BADx"))])
        ok(run("--verify", pak, d).returncode == 1, "--verify: a program without DKSH in the pack fails")
        os.remove(pak)
        r = run(d, "-o", pak, "--sdp2dksh", stub, "--names", "shaderpackage001.sdp")
        ok(r.returncode == 1 and not os.path.exists(pak), "a failed translation stops the build, no pack")
        r = run(d, "-o", pak, "--sdp2dksh", stub, "--partial", "--names", "shaderpackage001.sdp")
        import json
        man = json.load(open(os.path.join(t, "translated.json")))
        ok(r.returncode == 0 and man["packed"] == 3 and len(man["missing"]) == 1, "--partial: 3 packed, 1 listed")
        ok(man["missing"] and man["missing"][0]["error"] == "uam: error",
           "the failure reason comes from sdp2dksh's index.tsv")
        r = run(d, "-o", pak, "--sdp2dksh", stub, "--partial", "-j", "1", "--names", "shaderpackage001.sdp")
        ok(r.returncode == 0 and json.load(open(os.path.join(t, "translated.json")))["packed"] == 3, "-j 1: same")
    print("test_shaders_nx: %d failed" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
