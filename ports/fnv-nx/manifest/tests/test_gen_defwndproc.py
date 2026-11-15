#!/usr/bin/env python3
"""gen_defwndproc: the table is the case labels of the reference's default_window_proc, bound to handlers by name; a label with no handler, a handler
with no label, a label with no id are errors; an alias id (WM_KEYDOWN = WM_KEYFIRST) and a private id resolve. Plain script: exit 0 on pass."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(os.path.dirname(HERE), "gen_defwndproc.py")
d = tempfile.mkdtemp(prefix="defwnd-")
__import__('atexit').register(__import__('shutil').rmtree, d, True)   # a test leaves no scratch behind
os.makedirs(d + "/tree/dlls/win32u")
os.makedirs(d + "/tree/include")
open(d + "/tree/include/winuser.rh", "w").write("#define WM_KEYFIRST 0x0100\n#define WM_KEYDOWN WM_KEYFIRST\n#define WM_CLOSE 0x0010\n")
open(d + "/tree/dlls/win32u/ntuser_private.h", "w").write("#define WM_POPUPSYSTEMMENU  0x0313\n")
open(d + "/tree/dlls/win32u/defwnd.c", "w").write(
    "static int other(int a) { switch (a) { case WM_NOPE: return 1; } return 0; }\n"
    "LRESULT default_window_proc( HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam, BOOL ansi )\n{\n    switch (msg)\n    {\n"
    "    /* case WM_COMMENT: */\n    case WM_CLOSE:\n        break;\n    case WM_KEYDOWN:\n    case WM_POPUPSYSTEMMENU:\n        return 1;\n    }\n    return 0;\n}\n")
H = "static LRESULT defwnd_%s(window *w, HWND h, WPARAM wp, LPARAM lp) { return 0; }\n"
FAILED = []


def run(handlers):
    open(d + "/w.cpp", "w").write("".join(H % n for n in handlers))
    p = subprocess.run([sys.executable, TOOL, d + "/tree", d + "/w.cpp", d + "/out.inc"], capture_output=True, text=True)
    return p.returncode, p.stderr


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        FAILED.append(name)


rc, err = run(["WM_CLOSE", "WM_KEYDOWN", "WM_POPUPSYSTEMMENU"])
out = open(d + "/out.inc").read() if rc == 0 else ""
check("every label has a handler: the table is sorted by id with aliases and private ids resolved",
      rc == 0 and out.index("0x0010") < out.index("0x0100") < out.index("0x0313") and "defwnd.c:default_window_proc case WM_KEYDOWN (line 9)" in out)
check("a label with no handler is an error", run(["WM_CLOSE", "WM_KEYDOWN"])[0] == 1)
check("a handler with no label is an error", run(["WM_CLOSE", "WM_KEYDOWN", "WM_POPUPSYSTEMMENU", "WM_NOPE"])[0] == 1)
check("a case outside default_window_proc and in a comment is not a label", "WM_NOPE" not in out and "WM_COMMENT" not in out)
print("%d failed" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
