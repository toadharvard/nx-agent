#!/usr/bin/env python3
# reads: ports/fnv-nx/contract ports/fnv-nx/lib/sha256/build.json ports/fnv-nx/link ports/fnv-nx/toolchain/msvcmodel/DATAMODEL.json ports/fnv-nx/verify
"""Tests for recipe.resolve_link_root and its use in link_errors: a link.roots entry is the root's recipe for the image's own target. Pure."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import recipe  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)))
    fails += 0 if ok else 1


R = {"g": {"path": "g", "class": "product", "target": "guest", "variants": {"host": {"target": "host"}}},
     "h": {"path": "h", "class": "product", "target": "host"},
     "g2": {"path": "g2", "class": "product", "target": "guest", "variants": {"a": {"target": "host"}, "b": {"target": "host"}}},
     "g0": {"path": "g0", "class": "product", "target": "guest"}}
rs = lambda d, t: recipe.resolve_link_root(R, d, t)  # noqa: E731
check("a base root of the image's target is itself", rs("g", "guest")[0] == "g" and rs("h", "host")[0] == "h", (rs("g", "guest"), rs("h", "host")))
check("a guest base root in a host image is its variant of target host", rs("g", "host")[0] == "g@host", rs("g", "host"))
sp, why = rs("g0", "host")
check("no variant of the image's target: none, naming the root", sp is None and "g0" in why and "no variant" in why, (sp, why))
sp, why = rs("g2", "host")
check("two variants of the image's target are ambiguous: none (an explicit root@variant is needed)", sp is None and "several" in why, (sp, why))
check("an explicit root@variant of the image's target is itself", rs("g2@a", "host")[0] == "g2@a" and rs("g@host", "host")[0] == "g@host", (rs("g2@a", "host"), rs("g@host", "host")))
sp, why = rs("g@host", "guest")
check("an explicit root@variant of another target is none", sp is None and "target" in why, (sp, why))
sp, why = rs("h", "guest")
check("a host base root in a guest image, with no guest variant, is none", sp is None, (sp, why))
check("an unadmitted root is none", rs("zz", "host")[0] is None, rs("zz", "host"))
# link_errors on the real sha256 root (guest, variant host) and platform (guest, no variant)
files = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True).stdout.split("\n")
roots = dict(recipe.load_admit(REPO))
for p, tg in (("ports/fnv-nx/zh", "host"), ("ports/fnv-nx/zg", "guest")):
    roots[p] = {"path": p, "class": "product", "target": tg}


def errs(image, member, kind, flags):
    lk = {"output": "x.exe" if kind == "exe" else "x.elf", "kind": kind, "flags": flags, "roots": [member]}
    if kind == "pie":
        lk["script"] = "ports/fnv-nx/link/guest.ld"
    return [e for e in recipe.link_errors(lk, image, set(), roots, set(), REPO, files, "") if "link.roots" in e]


SHA = "ports/fnv-nx/lib/sha256"
e = errs("ports/fnv-nx/zh", SHA, "exe", ["-m", "aarch64linux"])
check("a host image that names a guest root with a host variant is valid (it links the variant)", e == [], e)
e = errs("ports/fnv-nx/zg", SHA, "pie", ["-m", "aarch64linux32"])
check("a guest image that names the same root links its base recipe: valid", e == [], e)
e = errs("ports/fnv-nx/zh", "ports/fnv-nx/platform", "exe", ["-m", "aarch64linux"])
check("a host image that names a guest root with no host variant is red", len(e) == 1 and "no variant" in e[0], e)
e = errs("ports/fnv-nx/zg", SHA + "@host", "pie", ["-m", "aarch64linux32"])
check("a guest image that names the host variant explicitly is red (target mismatch)", len(e) >= 1, e)
print("FAILED %d" % fails if fails else "all pass")
sys.exit(1 if fails else 0)
