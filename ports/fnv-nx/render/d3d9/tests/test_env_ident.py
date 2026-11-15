#!/usr/bin/env python3
"""test_env_ident.py: gen_env.py's ident_handheld.bin / ident_docked.bin (the adapter identity of the declared machine, 1100-byte D3DADAPTER_IDENTIFIER9)
equal the T_IDENT table of the emitted env_table.gen.cpp (the table the product unit compiles), the description at offset 512 is NUL-terminated
"NVIDIA GeForce GTX 470", and one flipped byte in one profile's row makes the generator fail (the two images must be byte-equal)."""
import os, re, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ENV = os.path.normpath(os.path.join(HERE, "..", "env"))


def gen(out, tsv_dir=None):
    env = dict(os.environ)
    if tsv_dir: env["ENV_TSV_DIR"] = tsv_dir
    return subprocess.run([sys.executable, "-I", os.path.join(ENV, "gen_env.py"), out], capture_output=True, text=True, env=env)


def t_ident(cpp):
    """the bytes of T_IDENT in the emitted source: shared (T_IDENT_A[1100]) or one row per profile (T_IDENT_A[2][1100])"""
    m = re.search(r"static const uint8_t T_IDENT_A((?:\[\d+\])+) = \{(.*?)\};\n", cpp, re.S); assert m, "T_IDENT_A"
    v = bytes(int(x) for x in re.findall(r"\d+", m.group(2)))
    return [v[:1100]] * 2 if m.group(1) == "[1100]" else [v[:1100], v[1100:]]


with tempfile.TemporaryDirectory() as t:
    r = gen(t); assert r.returncode == 0, r.stderr
    cpp = open(os.path.join(t, "env_table.gen.cpp")).read(); rows = t_ident(cpp)
    for i, p in enumerate(("handheld", "docked")):
        b = open(os.path.join(t, f"ident_{p}.bin"), "rb").read()
        assert len(b) == 1100 and b == rows[i], p
        d = b[512:640]; assert d.index(0) == len(b"NVIDIA GeForce GTX 470") and d[:d.index(0)] == b"NVIDIA GeForce GTX 470", p
    assert open(os.path.join(t, "ident_handheld.bin"), "rb").read() == open(os.path.join(t, "ident_docked.bin"), "rb").read()
    # one byte of one profile's row flipped: the VendorId of the docked row (10de -> 10df)
    tsv = os.path.join(t, "tsv"); os.makedirs(tsv)
    for p in ("handheld", "docked"): shutil.copy(os.path.join(ENV, f"env_{p}.tsv"), tsv)
    f = os.path.join(tsv, "env_docked.tsv"); s = open(f).read()
    assert s.count("VendorId=10de") >= 1; open(f, "w").write(s.replace("VendorId=10de", "VendorId=10df", 1))
    r = gen(os.path.join(t, "o2"), tsv); assert r.returncode != 0 and "RULE VIOLATED" in r.stderr, (r.returncode, r.stderr[-300:])
    # the same flip in every identifier row of the docked profile: each profile is self-consistent, only the profiles differ: the cross-profile rule fails
    ls = [(l.replace("VendorId=10de", "VendorId=10df") if l.startswith("GetAdapterIdentifier(0,") else l) for l in s.split("\n")]; open(f, "w").write("\n".join(ls))
    r2 = gen(os.path.join(t, "o3"), tsv); assert r2.returncode != 0 and "profiles differ" in r2.stderr, (r2.returncode, r2.stderr[-300:])
    print("ok: ident_*.bin == T_IDENT (1100 bytes each), Description@512 = NVIDIA GeForce GTX 470, one flipped row byte fails the generator:", r.stderr.strip()[-90:])
