"""gen_wire_errno: the list is the names of the guest libc that libnx's bsd errno table produces (synthetic inputs)."""
import os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
G = os.path.join(HERE, "..", "gen_wire_errno.py")
CONV = """#define EZERO 0
static unsigned char _error_code_map[] = {
    0 , // 0
    EPERM ,
    EAGAIN , // 2
    EZERO ,
    EAGAIN , // 4: a second number of one name is listed once, at the first
    EFOO ,
};
"""
NEWLIB = "#define EPERM 1\n#define EAGAIN 11\n#define EZERO 5\n#define EBAR 99\n#ifdef X\n#define EFOO 7\n#endif\n"
with tempfile.TemporaryDirectory() as d:
    c, n, out = (os.path.join(d, x) for x in ("c.c", "n.h", "o.h"))
    open(c, "w").write(CONV)
    open(n, "w").write(NEWLIB)
    run = lambda *a: subprocess.run([sys.executable, "-I", G, "--libnx-convert", c, "--newlib-errno", n] + list(a), capture_output=True, text=True)
    assert run("--out", out).returncode == 0
    t = open(out).read()
    assert "X(EPERM, 1) X(EAGAIN, 2) X(EFOO, 5)" in t                     # table numbers, first index of a name
    assert "EZERO" not in t.split("X(")[1] and "X(EZERO" not in t           # a name the libc lacks (#define 0) is out
    assert ", no wire number): EBAR EZERO" in t                             # libc names the service cannot produce
    assert run("--committed", out).returncode == 0
    open(out, "a").write("\n")
    r = run("--committed", out)
    assert r.returncode != 0 and "differs" in r.stderr                     # a hand edit is red
print("wire errno generator: 6 checks ok")
