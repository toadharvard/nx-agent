#!/usr/bin/env python3
"""tests/nx1: toolchain/nro/shaders_nx.py on synthetic packages with a stand-in sdp2dksh (no game data, no uam):
the pack is NXSP v1 sorted by bytecode hash, duplicates are packed once, --verify sees full coverage, a program
without DKSH stops the build (no pack) and --partial packs the rest and lists it."""
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
        if not bad:
            open(os.path.join(out, "%%016x.dksh" %% h), "wb").write(b"DKSH" + bytes(60) + bc)
        idx.write("%%016x\t-\t%%s\tvs_3_0\t%%s\t0\t0\t0\t0\t0\t%%s\n" %% (h, name, "fail" if bad else "ok",
                  "uam: error" if bad else ""))
'''


def sdp(path, entries):
    body = b"".join(n.encode().ljust(256, b"\0") + struct.pack("<I", len(bc)) + bc for n, bc in entries)
    open(path, "wb").write(struct.pack("<III", 100, len(entries), len(body)) + body)


def prog(stage, tag):
    return struct.pack("<I", (0xFFFE if stage == 0 else 0xFFFF) << 16 | 0x0300) + tag + struct.pack("<I", 0xFFFF)


def main():
    bad = 0

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
        pak = os.path.join(t, "shaders_nx.pak")
        run = lambda *a: subprocess.run([sys.executable, "-I", os.path.join(ROOT, "toolchain", "nro", "shaders_nx.py"),
                                         *a], capture_output=True, text=True)
        r = run(d, "-o", pak, "--sdp2dksh", stub)
        ok(r.returncode == 0 and os.path.isfile(pak), "build: exit 0 and a pack (%s)" % (r.stderr.strip()[-200:]))
        rows, pid = shaderpack.parse(open(pak, "rb").read())
        names = [x[0] for x in rows]
        ok(len(rows) == 3, "duplicate bytecode packed once: 3 programs from 4 entries")
        ok(names == sorted(names) and all(len(n) == 16 for n in names), "index sorted by 16-digit bytecode hash")
        ok({x[3] for x in rows} == {0, 1} and rows[[n for n, *_ in rows].index(
            "%016x" % shaders_nx.fnv1a64(prog(1, b"bbbb")))][3] == 1, "stage from the version token")
        ok(all(x[1] == "shaderpackage001" for x in rows if x[0] != "%016x" % shaders_nx.fnv1a64(prog(0, b"cccc"))),
           "family = first package holding the program")
        ok(run("--verify", pak, d).returncode == 0, "--verify: full coverage")
        sdp(os.path.join(d, "shaderpackage003.sdp"), [("X.vso", prog(0, b"BADx"))])
        ok(run("--verify", pak, d).returncode == 1, "--verify: a program without DKSH in the pack fails")
        os.remove(pak)
        r = run(d, "-o", pak, "--sdp2dksh", stub)
        ok(r.returncode == 1 and not os.path.exists(pak), "a failed translation stops the build, no pack")
        r = run(d, "-o", pak, "--sdp2dksh", stub, "--partial")
        import json
        man = json.load(open(os.path.join(t, "shaders_nx.json")))
        ok(r.returncode == 0 and man["packed"] == 3 and len(man["missing"]) == 1, "--partial: 3 packed, 1 listed")
        ok(man["missing"][0]["error"] == "uam: error", "the failure reason comes from sdp2dksh's index.tsv")
        r = run(d, "-o", pak, "--sdp2dksh", stub, "--partial", "-j", "1")
        ok(r.returncode == 0 and json.load(open(os.path.join(t, "shaders_nx.json")))["packed"] == 3, "-j 1: same")
    print("test_shaders_nx: %d failed" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
