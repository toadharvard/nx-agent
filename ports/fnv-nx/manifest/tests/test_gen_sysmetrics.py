#!/usr/bin/env python3
"""tests of manifest/gen_sysmetrics.py on a miniature Wine tree"""
import os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
GEN = os.path.join(HERE, "..", "gen_sysmetrics.py")
SYS = """int get_system_metrics( int index )
{
    switch (index)
    {
    case SM_CXSCREEN:
        return 1; /* not literal-checked? it is */
    case SM_CXBORDER:
    case SM_CYBORDER:
        return 1;
    case SM_CMETRICS:
        return SM_CMETRICS;
    default:
        return 0;
    }
}
"""
H = "#define SM_CXSCREEN\t0\n#define SM_CXBORDER\t5\n#define SM_CYBORDER\t6\n#define SM_CMETRICS\t90\n"
def run(cpp):
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(d + "/dlls/win32u"); os.makedirs(d + "/include")
        open(d + "/dlls/win32u/sysparams.c", "w").write(SYS)
        open(d + "/include/winuser.h", "w").write(H)
        open(d + "/d.cpp", "w").write(cpp)
        r = subprocess.run([sys.executable, "-I", GEN, d, d + "/d.cpp", d + "/o.inc"], capture_output=True, text=True)
        return r, (open(d + "/o.inc").read() if r.returncode == 0 else "")
OK = ("static int sm_SM_CXSCREEN(void) { return 1; }\nstatic int sm_SM_CXBORDER(void) { return 1; }\n"
      "static int sm_SM_CYBORDER(void) { return 1; }\nstatic int sm_SM_CMETRICS(void) { return 90; }\n")
r, o = run(OK); assert r.returncode == 0, r.stderr
assert o.count("{ ") >= 4 and "case SM_CMETRICS (line" in o and "{ 90, sm_SM_CMETRICS" in o, o
r, _ = run(OK.replace("SM_CMETRICS(void) { return 90; }", "SM_CMETRICS(void) { return 91; }")); assert r.returncode == 1 and "literal" in r.stderr
r, _ = run(OK + "static int sm_SM_STARTER(void) { return 0; }\n"); assert r.returncode == 1 and "no `case SM_STARTER" in r.stderr
r, _ = run(OK.replace("static int sm_SM_CYBORDER(void) { return 1; }\n", "")); assert r.returncode == 1 and "no handler sm_SM_CYBORDER" in r.stderr
print("ok")
