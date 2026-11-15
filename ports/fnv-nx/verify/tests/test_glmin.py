#!/usr/bin/env python3
"""Tests for verify/glmin.py: the minimum core GL version derived from a gl.xml registry, and its judgement of the declared host_gl.version.
The registry here is a reduced document in the real gl.xml shape (feature/require/remove); targets.json's host_gl citations are checked against the pinned file by targets.py."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import glmin  # noqa: E402
import targets  # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        fails.append(name)
        print("   ", detail)


XML = """<registry>
<feature api="gl" name="GL_VERSION_1_0" number="1.0"><require><command name="glGetError"/><command name="glBegin"/></require>
  <require profile="compatibility"><command name="glOld"/></require></feature>
<feature api="gl" name="GL_VERSION_1_1" number="1.1"><require><command name="glBindTexture"/></require></feature>
<feature api="gl" name="GL_VERSION_3_2" number="3.2"><remove profile="core"><command name="glBegin"/></remove></feature>
<feature api="gl" name="GL_VERSION_4_5" number="4.5"><require><command name="glClipControl"/></require></feature>
<feature api="gles2" name="GL_ES_VERSION_3_2" number="3.2"><require><command name="glEsOnly"/></require></feature>
</registry>"""
tab = glmin.core_minimum(XML)
check("first requiring feature, core", tab["glGetError"] == (1, 0) and tab["glBindTexture"] == (1, 1) and tab["glClipControl"] == (4, 5), tab)
check("compat-only, removed-from-core and other-api commands have no core version", not ({"glOld", "glBegin", "glEsOnly"} & set(tab)), tab)
check("declared = max over the referenced commands", glmin.judge(tab, "4.5", ["glGetError", "glClipControl"]) == [])
check("declared above the need is red", len(glmin.judge(tab, "4.6", ["glClipControl"])) == 1)
check("declared below the need is red", len(glmin.judge(tab, "4.1", ["glClipControl"])) == 1)
check("a command with no core version is red", glmin.judge(tab, "4.5", ["glClipControl", "glFooEXT"]) == ["glFooEXT has no core version in the registry"])
check("no command is red", glmin.judge(tab, "4.5", []) != [])
check("targets.json declares host_gl 4.5 core with one citation per field", targets.load()["host_gl"]["version"] == "4.5" and
      all(targets.load()["host_gl"]["cite"].get(f) for f in ("api", "version", "profile", "registry")))
raise SystemExit(1 if fails else 0)
