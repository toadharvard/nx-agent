#!/usr/bin/env python3
"""hostapi_total.py - "declared but not served" is a test failure.

1. The services hostapi.h declares are read by the C preprocessor, not from prose: `gcc -dM -E` lists the macros, every FNVNX_PLAT_*
   macro (the services of ext_call module FNVNX_EXT_PLATFORM; the range markers *_FIRST / *_LAST are not services) is evaluated by compiling
   and running a small C program with the host gcc, so the numbers are the compiler's.
2. The console host's dispatch (toolchain/host/host_platform.inc with the handler files host.c includes) is compiled into
   tests/s4/backends/host_total_test.c for AArch64 and run under qemu-aarch64 with `--served`: every declared service is called with
   nargs = 0 and must answer a stated code, never FNVNX_EXT_NO_SERVICE; undeclared numbers must answer NO_SERVICE; every service gets
   its wrong-nargs and invalid-argument cases.
3. The totality table in the header (between the markers TOTALITY TABLE BEGIN / END) names every declared service and every test name it
   lists exists in a tests/s4/backends/*_test.c.
Usage: hostapi_total.py [out dir]      env: DEVKITPRO (libnx headers), XCC (default aarch64-linux-gnu-gcc), CC (host gcc)."""
import os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
HDR = os.path.join(ROOT, "toolchain", "include", "fnvnx", "hostapi.h")
DKP = os.environ.get("DEVKITPRO", "/root/fnvnx-farm/toolchain-1fc1f7b1ef44/toolchain/dkp")
OUT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(os.environ.get("TMPDIR", "/tmp"), "hostapi_total")
CC = os.environ.get("CC", "gcc")
XCC = os.environ.get("XCC", "aarch64-linux-gnu-gcc")
os.makedirs(OUT, exist_ok=True)


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def fail(msg):
    print("hostapi_total: FAIL", msg)
    sys.exit(1)


# 1. the declared services, from the preprocessor
r = run([CC, "-dM", "-E", "-I", os.path.join(ROOT, "toolchain", "include"), HDR])
if r.returncode:
    fail("preprocessing hostapi.h: " + r.stderr[:300])
names = sorted({m.split()[1] for m in r.stdout.splitlines() if m.startswith("#define FNVNX_PLAT_") and len(m.split()) >= 3})
names = [n for n in names if not n.endswith("_FIRST") and not n.endswith("_LAST") and "(" not in n]
if not names:
    fail("no FNVNX_PLAT_ service macro found")
src = '#include <stdio.h>\n#include "fnvnx/hostapi.h"\nint main(void){\n' + "".join(
    '  printf("%%s %%llu\\n", "%s", (unsigned long long)(%s));\n' % (n, n) for n in names) + "  return 0;}\n"
open(os.path.join(OUT, "values.c"), "w").write(src)
r = run([CC, "-I", os.path.join(ROOT, "toolchain", "include"), os.path.join(OUT, "values.c"), "-o", os.path.join(OUT, "values")])
if r.returncode:
    fail("compiling the macro values: " + r.stderr[:300])
vals = {}
for line in run([os.path.join(OUT, "values")]).stdout.splitlines():
    n, v = line.split()
    vals[n] = int(v)
seen = {}
for n, v in vals.items():
    if v == 0 or v >= 64:
        fail("%s = %d is not a service number of module 11 (1..63)" % (n, v))
    if v in seen:
        fail("%s and %s are both %d" % (n, seen[v], v))
    seen[v] = n
open(os.path.join(OUT, "decl.h"), "w").write(
    "static const struct { const char *name; unsigned v; } g_decl[] = {\n" + "".join('    {"%s", %d},\n' % (n, v) for n, v in sorted(vals.items(), key=lambda x: x[1])) + "};\n")
print("hostapi_total: %d declared services: %s" % (len(vals), " ".join("%d=%s" % (v, n[len("FNVNX_PLAT_"):]) for n, v in sorted(vals.items(), key=lambda x: x[1]))))

# 2. the host dispatch under qemu
exe = os.path.join(OUT, "host_total_test")
cmd = [XCC, "-O2", "-static", "-std=gnu11", "-Wall", "-D__SWITCH__", '-DFNVNX_DECL_H="%s"' % os.path.join(OUT, "decl.h"),
       "-I", os.path.join(DKP, "libnx", "include"), "-I", os.path.join(ROOT, "tests", "s4", "ilp32-stubs"),
       "-I", os.path.join(ROOT, "toolchain", "include"), os.path.join(HERE, "host_total_test.c"), "-o", exe, "-lm"]
r = run([XCC, "-O2", "-c", "-std=gnu11", "-I", os.path.join(ROOT, "toolchain", "include"), os.path.join(HERE, "host_sock_total.c"), "-o", os.path.join(OUT, "host_sock_total.o")])
if r.returncode:
    fail("compiling host_sock_total.c: " + r.stderr[-600:])
cmd.insert(-3, os.path.join(OUT, "host_sock_total.o"))
r = run(cmd)
if r.returncode:
    fail("compiling host_total_test.c: " + r.stderr[-600:])
r = run(["qemu-aarch64", exe, "--served"])
sys.stdout.write(r.stdout)
failed = r.returncode != 0   # reported at the end, after the table check

# 3. the table in the header
text = open(HDR).read()
m = re.search(r"TOTALITY TABLE BEGIN(.*?)TOTALITY TABLE END", text, re.S)
if not m:
    fail("hostapi.h has no TOTALITY TABLE BEGIN / END block")
table = m.group(1)
for n in vals:
    if n[len("FNVNX_PLAT_"):] not in table:
        fail("%s is declared but not in the totality table" % n)
tests = set()
for f in os.listdir(HERE):
    if f.endswith("_test.c"):
        tests.add(open(os.path.join(HERE, f)).read())
blob = "\n".join(tests)
for t in sorted(set(re.findall(r"\btest:([a-z0-9_]+)", table))):
    if not re.search(r"\b%s\b" % re.escape(t), blob):
        fail("the table names the test %s, which no tests/s4/backends/*_test.c mentions" % t)
if failed:
    fail("host_total_test --served: a declared service has no handler, or a case failed (lines above)")
print("hostapi_total: ok")
