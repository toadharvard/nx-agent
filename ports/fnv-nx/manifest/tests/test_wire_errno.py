"""gen_wire_errno: the Linux numbers, named by libnx's table or by Linux, with libnx's 10000 + n encoding (synthetic inputs)."""
import os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
G = os.path.join(HERE, "..", "gen_wire_errno.py")
CONV = """#define EZERO 0
#define EGONE 0
static unsigned char _error_code_map[] = {
    0 , // 0
    EPERM ,
    EAGAIN , // 2: libnx names it EAGAIN
    EZERO ,  // 3: libnx has no name (comes out as 10003) and the libc has EZERO: named by Linux
    EGONE ,  // 4: neither
};
"""
NEWLIB = "#define EPERM 1\n#define EAGAIN 11\n#define EZERO 5\n#define EBAR 99\n"
BASE = "#define\tEPERM\t 1\t/* x */\n#define\tEWHATEVER\t 2\n"
ERRNO = "#define\tEZERO\t 3\n#define\tEGONE\t 4\n#define\tEALIAS\tEPERM\n"
with tempfile.TemporaryDirectory() as d:
    fs = {k: os.path.join(d, k) for k in ("c", "n", "b", "e", "o")}
    for k, v in (("c", CONV), ("n", NEWLIB), ("b", BASE), ("e", ERRNO)):
        open(fs[k], "w").write(v)
    run = lambda *a: subprocess.run([sys.executable, "-I", G, "--libnx-convert", fs["c"], "--newlib-errno", fs["n"], "--linux-errno-base", fs["b"],
                                     "--linux-errno", fs["e"]] + list(a), capture_output=True, text=True)
    assert run("--out", fs["o"]).returncode == 0
    t = open(fs["o"]).read()
    assert "X(EPERM, 1) X(EAGAIN, 2) X(EZERO, 3)" in t                       # the libnx name where non-zero, else the Linux name the libc has
    assert "UNNAMED(X) \\\n    X(EGONE, 4)" in t and "X(EALIAS" not in t      # no libc name: unnamed; an alias is not a second number
    assert "4 Linux numbers" in t and "2 numbers reach the host as 10000" in t
    assert "FNVNX_ERRNO_UNKNOWN_OFFSET 10000" in t and "fnvnx_wire_from_host_errno" in t
    assert run("--committed", fs["o"]).returncode == 0
    open(fs["o"], "a").write("\n")
    r = run("--committed", fs["o"])
    assert r.returncode != 0 and "differs" in r.stderr                       # a hand edit is red
print("wire errno generator: 7 checks ok")
