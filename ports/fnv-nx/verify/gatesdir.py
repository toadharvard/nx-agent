"""verify/gates.d: one file per gate (L1 CONTRACT).
    gates.d/<name>.json   {"format": "fnvnx-gatefile/1", "name": NAME, "argv": [...], "result": "NAME.json", "covers": [ROOT_CLASS...]}
The gates of a tree are the files of this directory, sorted by name; there is no shared list, so adding a gate touches only its own file
and two branches that add different gates never conflict. The verdict (l1.py) assembles the sorted entries into the canonical gates
document (format fnvnx-gates/1) of the commit under test; that document is a value in memory and in the verdict's temporary directory,
never a tracked file.
`covers` is the set of ROOT CLASSES the gate judges, declared from what the gate reads: a class is a predicate over root facts, not a list of
roots. The classes of the admitted roots (verify/admit.json) are derived by one rule, root_classes: class doc -> doc; class tool -> tool; class ref -> ref (a reference root) and class test -> test (gate_admit.py);
class product with "target": "host" -> host-product; product with a guest target whose "cxx_origin" is {"lifted_by", "pack"} -> lifted; any
other product -> product-guest; and a product root whose build.json declares a link stage (`link` not null) is also `linked`. The contract
gates cover `linked`: a link-null root is outside their universe (they judge a link), and the other gates still cover it. verdict.py judges a gate
red when its `covers` is missing or not a non-empty list of ROOT_CLASSES, and red when its universe is 0 while an admitted root of a covered
class exists (a gate that judges nothing where there is something to judge is not green: no vacuous green); with no root of a covered
class and universe 0 it is green. Proof: the universe of every gate is counted from the roots it reads, so universe 0 with a covered root
present means the gate did not see that root. Assumption: ASM-001 (the compiler) and ASM-004 (the console), as for every gate."""
import json
import os
import subprocess

TREE = "ports/fnv-nx"
DIR = TREE + "/verify/gates.d"
ADMIT = TREE + "/verify/admit.json"
FILE_FORMAT = "fnvnx-gatefile/1"
FORMAT = "fnvnx-gates/1"
ROOT_CLASSES = ("doc", "tool", "ref", "test", "product-guest", "lifted", "host-product", "linked")


class Bad(ValueError):
    pass


def root_class(root):
    """The root class of one admit.json entry (see the module docstring)."""
    c = root.get("class")
    if c in ("doc", "tool", "ref", "test"):
        return c
    if c != "product":
        return None
    if root.get("target") == "host":
        return "host-product"
    o = root.get("cxx_origin")
    return "lifted" if isinstance(o, dict) and "lifted_by" in o else "product-guest"


def has_link(repo, path):
    """The root fact `linked`: the root's build.json declares a link stage (`link` is not null). A root without build.json has none."""
    try:
        with open(os.path.join(repo, path, "build.json"), encoding="utf-8") as fh:
            return bool(json.load(fh).get("link"))
    except (OSError, ValueError, AttributeError):
        return False


def root_classes(admit, repo=None):
    """{root path: set of root classes} of a parsed admit.json: the one class of root_class, plus `linked` for a product root whose
    build.json (read under REPO) has a link stage. A class is a predicate over root facts, so a gate covers the roots that satisfy it."""
    out = {}
    for r in admit.get("roots", []):
        if isinstance(r, dict) and isinstance(r.get("path"), str):
            c = root_class(r)
            s = {c} if c else set()
            if c in ("product-guest", "lifted", "host-product") and repo is not None and has_link(repo, r["path"]):
                s.add("linked")
            out[r["path"]] = s
    return out


def parse_entry(name, text):
    """One gate file -> its entry dict; Bad when it is not a gate file of this name. `covers` is NOT checked here (verdict.py judges it)."""
    try:
        d = json.loads(text)
    except ValueError as e:
        raise Bad("gates.d/%s.json: %s" % (name, e))
    if not isinstance(d, dict) or d.get("format") != FILE_FORMAT or d.get("name") != name:
        raise Bad("gates.d/%s.json: format is not %s or name is not %s" % (name, FILE_FORMAT, name))
    if not (isinstance(d.get("argv"), list) and d["argv"] and all(isinstance(a, str) for a in d["argv"])
            and isinstance(d.get("result"), str) and d["result"]):
        raise Bad("gates.d/%s.json: argv or result is malformed" % name)
    out = {"name": name, "argv": d["argv"], "result": d["result"]}
    if "covers" in d:
        out["covers"] = d["covers"]
    return out


def document(entries):
    """The gates document of a list of entries, sorted by name."""
    return {"format": FORMAT, "gates": sorted(entries, key=lambda g: g["name"])}


def load_dir(path=DIR):
    """The gates document of a checkout's gates.d directory. Bad when it does not exist, is empty or holds a file that is no gate file."""
    if not os.path.isdir(path):
        raise Bad("%s does not exist" % path)
    entries = []
    for f in sorted(os.listdir(path)):
        if not f.endswith(".json"):
            raise Bad("%s/%s is not a .json gate file" % (path, f))
        with open(os.path.join(path, f), encoding="utf-8") as fh:
            entries.append(parse_entry(f[:-5], fh.read()))
    if not entries:
        raise Bad("%s holds no gate file" % path)
    return document(entries)


def load_dir_or_empty(path=DIR):
    """load_dir for the gates that only need the names or an argv: {} when the directory is unusable (their own errors name it)."""
    try:
        return load_dir(path)
    except (Bad, OSError):
        return {}


def from_git(repo, rev):
    """The gates document of the gates.d directory of REV's tree. Bad when it is missing or malformed."""
    p = subprocess.run(["git", "-C", repo, "ls-tree", "--name-only", "-z", rev, DIR + "/"], capture_output=True)
    names = [x for x in p.stdout.decode("utf-8", "surrogateescape").split("\0") if x] if p.returncode == 0 else []
    if not names:
        raise Bad("no %s in %s" % (DIR, rev))
    entries = []
    for n in sorted(names):
        base = os.path.basename(n)
        if not base.endswith(".json"):
            raise Bad("%s is not a .json gate file" % n)
        s = subprocess.run(["git", "-C", repo, "show", "%s:%s" % (rev, n)], capture_output=True)
        if s.returncode:
            raise Bad("cannot read %s in %s" % (n, rev))
        entries.append(parse_entry(base[:-5], s.stdout.decode("utf-8")))
    return document(entries)
