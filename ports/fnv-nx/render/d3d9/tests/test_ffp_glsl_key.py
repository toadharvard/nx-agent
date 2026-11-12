#!/usr/bin/env python3
"""test_ffp_glsl_key.py: every FFP program blob emitted by ffp/gen_glsl.py (ffp_glsl.gen.h, the header ffp_prog.cpp includes and
sends as NxShaderDesc.glsl / glsl_size / key) has key == sha256(blob bytes). Recomputed here from the emitted bytes (hashlib), never
from the generator's own state. Also: the key arrays are 32 bytes, ffp_prog.cpp sends exactly the generated names and no DKSH name
exists anywhere in the root, and flipping one key byte of the header makes the check fail."""
import hashlib, os, re, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.normpath(os.path.join(HERE, ".."))
NAMES = ["nx_ffp_glsl_%s_%s" % (s, v) for s in ("vs", "ps") for v in ("smooth", "flat")]


def parse(txt):
    arr = {m.group(1): bytes(int(x) for x in m.group(3).split(",") if x) for m in re.finditer(r"static const unsigned char (\w+)\[(\d+)\] = \{([0-9,]*)\};", txt)}
    sz = {m.group(1): int(m.group(2)) for m in re.finditer(r"static const unsigned (\w+)_size = (\d+);", txt)}
    return arr, sz


def check(txt):
    """list of problems"""
    arr, sz = parse(txt); bad = []
    for n in NAMES:
        if n not in arr or n + "_key" not in arr or n not in sz: bad.append(n + ": missing"); continue
        if sz[n] != len(arr[n]) or sz[n] == 0: bad.append(n + ": size")
        if len(arr[n + "_key"]) != 32: bad.append(n + ": key length")
        if hashlib.sha256(arr[n]).digest() != arr[n + "_key"]: bad.append(n + ": key != sha256(blob)")
    return bad


with tempfile.TemporaryDirectory() as t:
    r = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "ffp", "gen_glsl.py"), t], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert sorted(os.listdir(t)) == ["ffp_glsl.gen.h"], os.listdir(t)           # the generator emits no DKSH header
    txt = open(os.path.join(t, "ffp_glsl.gen.h")).read()
assert check(txt) == [], check(txt)
arr, _ = parse(txt); assert len(arr) == 8 and len({arr[n + "_key"] for n in NAMES}) == 4
# one flipped key byte (each of the four keys, first / last byte) is caught
for n in NAMES:
    k = arr[n + "_key"]
    for i in (0, 31):
        f = bytes(k[j] ^ (1 if j == i else 0) for j in range(32))
        bad = check(txt.replace(",".join(map(str, k)), ",".join(map(str, f)), 1))
        assert bad == [n + ": key != sha256(blob)"], (n, i, bad)
# the product unit sends the generated names, and no DKSH path remains in the root
src = open(os.path.join(ROOT, "dev", "ffp_prog.cpp")).read()
assert "nx_ffp_glsl_##s##_##v##_key" in src and "nx_ffp_glsl_##s##_##v##_size" in src
for s_, v_ in (("vs", "smooth"), ("vs", "flat"), ("ps", "smooth"), ("ps", "flat")): assert "NXB_(%s, %s)" % (s_, v_) in src
assert "memcpy(d->key, b.key" in src
for dp, _, fs in os.walk(ROOT):
    for f in fs:
        if f.endswith((".py", ".cpp", ".h", ".json")) and f != os.path.basename(__file__):
            s = open(os.path.join(dp, f), errors="replace").read()
            assert "NX_FFP_DKSH_BUILT" not in s and "ffp_dksh" not in s and "nx_ffp_dksh" not in s, f
print("ok: 4 programs, sha256 keys equal; 8 single-byte key flips caught")
