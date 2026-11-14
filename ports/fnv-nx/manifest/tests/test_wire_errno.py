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
    fs = {k: os.path.join(d, k) for k in ("c", "n", "b", "e", "o", "u")}
    for k, v in (("c", CONV), ("n", NEWLIB), ("b", BASE), ("e", ERRNO)):
        open(fs[k], "w").write(v)
    run = lambda *a: subprocess.run([sys.executable, "-I", G, "--libnx-convert", fs["c"], "--newlib-errno", fs["n"], "--linux-errno-base", fs["b"],
                                     "--linux-errno", fs["e"]] + list(a), capture_output=True, text=True)
    assert run("--out", fs["o"]).returncode == 0
    t = open(fs["o"]).read()
    assert "X(EPERM, 1) X(EAGAIN, 2) X(EZERO, 3)" in t                       # the libnx name where non-zero, else the Linux name the libc has
    assert "UNNAMED(X) \\\n    X(EGONE, 4)" in t and "X(EALIAS" not in t      # no libc name: unnamed; an alias is not a second number
    assert "4 Linux numbers" in t and "2 numbers reach the host as 10000" in t
    assert "FNVNX_ERRNO_UNKNOWN_OFFSET 10000" in t and "int fnvnx_wire_from_host_errno(int e);" in t and "fnvnx_assume_violation" not in t.replace("OWNER-assumption exit", "")
    # the generated unit: the array, the cases of the check and the Linux numbers are one set; the miss is the exit with the literal OWNER id 4
    assert run("--unit", fs["u"]).returncode == 0
    u = open(fs["u"]).read()
    import re
    arr = [int(x) for x in re.search(r"fnvnx_dom_errno\[\d+\] = \{([^}]*)\}", u).group(1).split(",")]
    cases = [int(x) for x in re.findall(r"^    case (\d+):$", u, re.M)]
    assert arr == cases == [1, 2, 3, 4]
    chk = u[u.index("int fnvnx_dom_errno_check"):u.index("int fnvnx_wire_from_host_errno")]
    assert "        return v;\n    default:\n        fnvnx_assume_violation(4u, (uint64_t)(uint32_t)v);" in chk   # hit returns v, miss exits with id 4
    assert "if (e == EPERM) return 1;" in u and "if (e == EAGAIN) return 2;" in u and "if (e == EZERO)" not in u   # by name only the libnx-named numbers
    assert "return fnvnx_dom_errno_check(e - FNVNX_ERRNO_UNKNOWN_OFFSET);" in u                              # 10000 + n decodes to n in the domain
    assert run("--committed", fs["o"]).returncode == 0
    open(fs["o"], "a").write("\n")
    r = run("--committed", fs["o"])
    assert r.returncode != 0 and "differs" in r.stderr                       # a hand edit is red
print("wire errno generator: 12 checks ok")
