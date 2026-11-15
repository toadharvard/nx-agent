#!/usr/bin/env python3
"""Tests for the guest link profile (link/guest.json, recipe.with_guest_link, the guest part of recipe.check): one fact for every guest link.
Pure: the real checkout's profile and admit.json, synthetic roots, no sandbox."""
import copy
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import recipe  # noqa: E402

REPO = os.path.abspath(os.path.join(VERIFY, "..", "..", ".."))
FAILED = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:400]))
    if not cond:
        FAILED.append(name)


files = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True).stdout.split("\n")
roots = recipe.load_admit(REPO)
pin = recipe.load_pin(REPO)
prof = recipe.load_guest_link(REPO)
EXE, TST, HOST = "ports/fnv-nx/gx", "ports/fnv-nx/gt", "ports/fnv-nx/hx"
roots = dict(roots)
for p, tg, cl in ((EXE, "guest", "product"), (TST, "guest", "test"), (HOST, "host", "product")):
    roots[p] = {"path": p, "class": cl, "target": tg}
RT = dict(pin["runtime"])


def rec(output, **lk):
    return {"format": recipe.FORMAT, "compiler": {}, "flags": {}, "include": [], "units": "all", "external": [RT],
            "link": dict({"output": output, "flags": ["-m", "aarch64linux32"]}, **lk)}


def link_errs(r, root):
    return [e for e in recipe.check(r, root, files, roots, pin, REPO) if "link" in e.lower() or "guest.json" in e or "guest_compiler.json" in e]


check("the profile is valid", recipe.guest_link_errors(prof, pin, files, roots) == [], recipe.guest_link_errors(prof, pin, files, roots))
check("the profile holds the script only: no stored libraries", set(prof) == {"script"}, prof)
check("a guest exe root with no script and no libs has no link error", link_errs(rec("a.elf"), EXE) == [], link_errs(rec("a.elf"), EXE))
check("a guest test root likewise", link_errs(rec("t.elf"), TST) == [], link_errs(rec("t.elf"), TST))
e = link_errs(rec("a.elf", script="link.ld"), EXE)
check("a guest root that declares script is red", any("link.script is declared" in x for x in e), e)
e = link_errs(rec("a.elf", libs={"names": []}), TST)
check("a guest root that declares libs is red", any("link.libs is declared" in x for x in e), e)
r = rec("a.elf")
r["external"] = []
e = link_errs(r, EXE)
check("a guest root without the runtime external is red", any("not declared in external" in x for x in e), e)
e = link_errs(rec("h.elf", kind="exe", flags=["-m", "aarch64linux"]), HOST)
check("a host root is unaffected (no profile text in its errors, script still required for pie)", not any("guest.json" in x for x in e), e)
check("a host pie root without script is still red", any("link must be" in x for x in link_errs(rec("h.elf"), HOST)))
a, t, h = (recipe.with_guest_link(rec(o), p, roots, REPO) for o, p in (("a.elf", EXE), ("t.elf", TST), ("h.elf", HOST)))
ka = {k: v for k, v in a["link"].items() if k != "output"}
kt = {k: v for k, v in t["link"].items() if k != "output"}
check("guest exe and guest test get identical script and a derive marker with the pinned runtime (links differ only by output and objects)", ka == kt and ka["libs"] == {"external": pin["runtime"]["name"], "derive": True}
      and os.path.isfile(os.path.join(REPO, ka["script"])), (ka, kt))
check("a host root's link is unchanged", h == rec("h.elf"))
check("a profile that stores libs is red (they are derived, never stored)", recipe.guest_link_errors(dict(prof, libs={}), pin, files, roots) != [])
bad = dict(prof, script="../x.ld")
check("a profile script outside the tool tree is red", recipe.guest_link_errors(bad, pin, files, roots) != [])
check("a profile with another key is red", recipe.guest_link_errors(dict(prof, extra=1), pin, files, roots) != [])
e = link_errs(rec("s.so", kind="shared", version_script="ports/fnv-nx/link/guest.ld", flags=["-m", "aarch64linux"]), HOST)
check("version_script: a tracked file of an admitted tool root's tree, by repo path, is accepted", not any("link must be" in x for x in e), e)
e = link_errs(rec("s.so", kind="shared", version_script="ports/fnv-nx/nothere.map", flags=["-m", "aarch64linux"]), HOST)
check("version_script: an untracked file outside the root is red", any("link must be" in x for x in e), e)
e = link_errs(rec("s.so", kind="shared", version_script="ports/fnv-nx/docs/ARCHITECTURE.md", flags=["-m", "aarch64linux"]), HOST)
check("version_script: a tracked file of a doc root is red", any("link must be" in x for x in e), e)

# the tools of the ldcheck stage (link/ldcheck.py, link/gateids.py, contract/gatefile.py): tracked files under a tree of an admitted tool root
need = lambda rs: [e for e in recipe.check(rec("a.elf"), EXE, files, rs, pin, REPO) if "ldcheck stage needs" in e]  # noqa: E731
check("link tools under the declared trees of the tool root (contract, link) are valid", need(roots) == [], need(roots))
cr = next(r for r in roots.values() if r.get("class") == "tool" and "ports/fnv-nx/link" in (r.get("trees") or []))
nt = dict(roots, **{cr["path"]: {k: v for k, v in cr.items() if k != "trees"}})
check("the same tools with no admitted tree that holds them are red, one finding per tool", len(need(nt)) >= 2, need(nt))
dc = dict(roots, **{cr["path"]: dict(cr, **{"class": "doc"})})
check("a tree of a root that is not a tool root does not admit a link tool", len(need(dc)) >= 2, need(dc))
check("tool_file is the one membership function: a file under a declared tree is a tool file, one outside every tree is not",
      recipe.tool_file(roots, files, "ports/fnv-nx/link/ldcheck.py") and not recipe.tool_file(nt, files, "ports/fnv-nx/link/ldcheck.py"))
print("FAILED: %s" % FAILED if FAILED else "all pass")
sys.exit(1 if FAILED else 0)
