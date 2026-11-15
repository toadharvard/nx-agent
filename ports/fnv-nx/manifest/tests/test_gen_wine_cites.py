#!/usr/bin/env python3
"""gen_wine_cites: a citation of a missing path or of a function that the file does not define is an error; a call, a prototype and a comment
mention are not definitions; a definition in Wine's style (return type on its own line, name at column 0) is found. Plain script: exit 0 on pass."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(os.path.dirname(HERE), "gen_wine_cites.py")
d = tempfile.mkdtemp(prefix="wine-cites-")
os.makedirs(d + "/tree/dlls/x")
open(d + "/tree/dlls/x/a.c", "w").write("static int helper(int a);\n/*\n * old_fn(int x)\n */\nstatic unsigned int\nreal_fn(int x)\n{\n    return helper(x) + call_only(x);\n}\n"
                                        "#define MAC(x) x\nint WINAPI Api(int a)\n{\n    return 0;\n}\n")
FAILED = []


def run(text):
    open(d + "/in.txt", "w").write(text)
    p = subprocess.run([sys.executable, TOOL, d + "/tree", d + "/out.tsv", d + "/in.txt"], capture_output=True, text=True)
    return p.returncode, p.stderr


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        FAILED.append(name)


check("a definition (return type on the line before, and on the same line, and a macro) resolves",
      run("wine-9.0:dlls/x/a.c:real_fn wine-9.0:dlls/x/a.c:Api wine-9.0:dlls/x/a.c:MAC")[0] == 0 and "dlls/x/a.c:6" in open(d + "/out.tsv").read())
check("a prototype is not a definition", run("wine-9.0:dlls/x/a.c:helper")[0] == 1)
check("a call is not a definition", run("wine-9.0:dlls/x/a.c:call_only")[0] == 1)
check("a mention in a comment is not a definition", run("wine-9.0:dlls/x/a.c:old_fn")[0] == 1)
check("a missing path is an error", run("wine-9.0:dlls/x/none.c:real_fn")[0] == 1)
check("a path that leaves the tree is an error", run("wine-9.0:../a.c:real_fn")[0] == 1)
print("%d failed" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
