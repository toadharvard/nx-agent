"""gen_wire_errno: the list is total over the guest libc's errno names that Linux defines (synthetic include tree)."""
import os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
G = os.path.join(HERE, "..", "gen_wire_errno.py")
with tempfile.TemporaryDirectory() as d:
    os.mkdir(os.path.join(d, "sys"))
    open(os.path.join(d, "sys", "errno.h"), "w").write(
        "#define EPERM 1\n#define EAGAIN 35\n#define EWOULDBLOCK EAGAIN\n#define ENOTSUP 134\n#define EOPNOTSUPP 45\n"
        "#ifdef __LINUX_ERRNO_EXTENSIONS__\n#define ENOKEYLESSFOO 9999\n#define EFTYPE 79\n#endif\n")
    out = os.path.join(d, "o.h")
    run = lambda *a: subprocess.run([sys.executable, "-I", G, "--newlib-include", d, "--out", out] + list(a), capture_output=True, text=True)
    assert run().returncode == 0
    t = open(out).read()
    assert "X(EPERM, 1)" in t and "X(EAGAIN, 11)" in t and "X(EWOULDBLOCK, 11)" in t      # Linux numbers, not the libc's
    assert "X(EOPNOTSUPP, 95) X(ENOTSUP, 95)" in t                                           # numeric definition first
    assert "ENOKEYLESSFOO" in t.split("X(")[0] and "EFTYPE" in t.split("X(")[0]             # no Linux name: no wire number
    assert "X(EFTYPE" not in t
    assert run("--check").returncode == 0
    open(out, "a").write("\n")
    r = run("--check")
    assert r.returncode != 0 and "differs" in r.stderr                                      # a hand edit is red
print("wire errno generator: 6 checks ok")
