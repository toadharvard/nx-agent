#!/usr/bin/env python3 -I
"""toolflags: gate for rule R12-T class 2 (external tools): a stage whose command is a compiler/assembler/linker/archiver/
interpreter is deterministic BY CONSTRUCTION OF ITS INVOCATION when it carries the tool's documented reproducibility
switches and the environment that removes the documented ambient inputs.

    python3 -I toolflags.py check STAGE.json... [--root DIR] [--json OUT.json]   exit 0 clean, 2 violations, 1 usage/unreadable
    python3 -I toolflags.py policy                                                 print the policy table (toolflags_policy.py)

Input: stage documents "fnvnx-stage/1" (sandbox.py) plus "tool", the DECLARED kind (never guessed from a file name): gcc as
ld ar objcopy python msvc-cl msvc-link msvc-lib wine; sh/make: `tool-unanalysable` (child commands are not visible in argv);
others `tool-unlisted`; none `tool-undeclared`. wine needs "wine_tool" (an msvc-* kind) applied to the argv after wine.
GNU response files `@F`: F under a declared input, read from ROOT + F (--root), expanded before the rules run (no --root,
unreadable or a cycle: `response-file-unreadable`). Output `STAGE: RULE detail [citation]`, summary, JSON "fnvnx-toolflags/1".
Declared dependencies, NOT checked here: "no_date_time_macros": {"checked_by": NAME} names the stage/gate proving the sources
use no __DATE__/__TIME__ (VC9 has no switch); "coff_normalize"/"pe_normalize": the consumer normalizes time stamps
(verify/coffnorm.py); a python script must itself pass puregate.
ASSUMPTION A-env: external tools invoked this way are deterministic. It rests on each tool's reproducible-builds (ASM-038)
documentation; the gate enforces the invocation, it does not prove the tool.
"""
import json
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from toolflags_policy import (FIXED, FORBID_ENV, GNU, KINDS, MSVC, POLICY, PYENV_OK)  # noqa: E402

FORMAT = "fnvnx-toolflags/1"

def under(p, bases):
    if not isinstance(p, str) or not p.startswith("/"):
        return False
    p = os.path.normpath(p)
    return any(p == b or p.startswith(b.rstrip("/") + "/") for b in bases)

class Ctx:
    def __init__(self, doc, root):
        self.doc, self.root, self.out = doc, root, []
        self.ins, self.outs = [os.path.normpath(i["at"]) for i in doc["inputs"]], [os.path.normpath(o) for o in doc["outputs"]]

    def v(self, rule, detail, key):
        item = (rule, detail, POLICY[key][2])
        if item not in self.out:
            self.out.append(item)

    def need(self, cond, what, key):
        if not cond:
            self.v("flag-missing", what, key)

    def need_under(self, path, what, key, bases=None):
        if not under(path, self.ins if bases is None else bases):
            self.v("path-outside-inputs", "%s %s" % (what, path), key)

def check_env(c):
    e = c.doc["env"]
    for k, want in FIXED:
        if k not in e:
            c.v("env-missing", "%s=%s" % (k, want), "env.fixed")
        elif e[k] != want:
            c.v("env-value", "%s=%s, want %s" % (k, e[k], want), "env.fixed")
    for k in ("SOURCE_DATE_EPOCH", "COLUMNS"):
        if k not in e:
            c.v("env-missing", "%s=<decimal integer>" % k, "env.fixed")
        elif not re.fullmatch("[0-9]+", e[k]):
            c.v("env-value", "%s=%s, want a decimal integer" % (k, e[k]), "env.fixed")
    for k in sorted(e):
        if (k in FORBID_ENV or k.startswith(("CCACHE_", "DISTCC_")) or (k.startswith("LC_") and k != "LC_ALL")
                or (k.startswith("PYTHON") and k not in PYENV_OK)):
            c.v("env-forbidden", k, "env.forbidden")
    for comp in e["PATH"].split(":") if "PATH" in e else c.v("env-missing", "PATH", "env.path") or []:
        if not under(comp, c.ins):
            c.v("env-path", "PATH component '%s' not under a declared input" % comp, "env.path")
    if "TMPDIR" in e and not under(e["TMPDIR"], c.outs):
        c.v("env-tmpdir", "TMPDIR=%s not under a declared output" % e["TMPDIR"], "env.tmpdir")

def expand(c, a, seen=()):
    out = []
    for t in a:
        p = os.path.normpath(t[1:])
        if len(t) < 2 or t[0] != "@":
            out.append(t)
            continue
        if not under(p, c.ins):
            c.v("path-outside-inputs", "response file " + t[1:], "resp")
            continue
        try:
            with open(os.path.join(c.root, p.lstrip("/")), "rb") as f:
                toks = None if p in seen else shlex.split(f.read().decode("utf-8"))
        except (TypeError, OSError, ValueError):
            toks = None
        if toks is None:
            c.v("response-file-unreadable", p, "resp")
        else:
            out += expand(c, toks, seen + (p,))
    return out

def pyparse(a):
    """(flag letters, script index or None, bad options) of python's argv after the executable."""
    flags, bad, i = "", [], 0
    while i < len(a):
        t = a[i]
        if t == "-" or not t.startswith("-"):
            return flags, i, bad
        for j, ch in enumerate(t[1:] if t[1:2] != "-" else ""):
            if ch in "cm":
                bad.append("-" + ch)
                return flags, None, bad
            if ch in "WXQ":
                i += 1 if j == len(t) - 2 else 0
                break
            flags += ch
        i += 1
    return flags, None, bad

def opts(a, lower):
    return [(t[1:].lower() if lower else t[1:]) for t in a if t[:1] in ("/", "-") and len(t) > 1]

def chk_gcc(c, a):
    def has(p):
        return any(t.startswith(p) and len(t) > len(p) for t in a)
    c.need(has("-frandom-seed="), "-frandom-seed=<non-empty>", "gcc.seed")
    c.need(has("-ffile-prefix-map="), "-ffile-prefix-map=OLD=NEW", "gcc.prefix")
    if "-Werror=date-time" not in a:
        c.v("flag-missing", "-Werror=date-time", "gcc.date")
    for t in a:
        if t in ("-march=native", "-mtune=native", "-fbranch-probabilities", "-Wno-date-time", "-Wno-error=date-time") \
                or t.startswith(("-fprofile-generate", "-save-temps")):
            c.v("flag-forbidden", t, "gcc.date" if "date" in t else "gcc.forbid")
        elif t.startswith(("-fprofile-use", "-fauto-profile")):
            if "=" not in t or t.endswith("="):
                c.v("flag-forbidden", t, "gcc.forbid")
            else:
                c.need_under(t.split("=", 1)[1], t.split("=", 1)[0], "gcc.forbid")
        elif t.startswith(("-fplugin=", "-specs=")):
            c.need_under(t.split("=", 1)[1], t.split("=", 1)[0], "gcc.paths")

def chk_as(c, a):
    if any(t in ("-g", "--gen-debug") or re.fullmatch("--gdwarf-[2-5]", t) for t in a):
        c.need(any(t.startswith("--debug-prefix-map") for t in a), "--debug-prefix-map=OLD=NEW (debug info requested)", "as.debug")

def chk_ld(c, a):
    ids = [t for t in a if t == "--build-id" or t.startswith("--build-id=")]
    c.need(ids, "--build-id[=sha1|none]", "ld.buildid")
    for t in ids:
        if t != "--build-id" and not re.fullmatch("--build-id=(sha1|md5|none|0x[0-9a-fA-F]*)", t):
            c.v("flag-forbidden", t, "ld.buildid")
    for i, t in enumerate(a):
        for p in a[i + 1:i + 2] if t in ("-T", "--script", "-script") else [t[9:]] if t.startswith("--script=") else (
                [t[2:]] if t[:3] in ("-T/", "-T.") else []):
            c.need_under(p, "linker script", "ld.paths")

def chk_ar(c, a):
    i = 0
    while i < len(a) and a[i].startswith("--"):
        i += 2 if a[i] in ("--plugin", "--output", "--target") else 1
    letters = a[i].lstrip("-") if i < len(a) else ""
    c.need("D" in letters, "operation letter D (got '%s')" % letters, "ar.D")
    if "U" in letters:
        c.v("flag-forbidden", "operation letter U", "ar.D")

def chk_objcopy(c, a):
    def short(t, ch):
        return re.fullmatch("-[DpUSgxXsd]+", t) is not None and ch in t
    c.need(any(t == "--enable-deterministic-archives" or short(t, "D") for t in a), "-D", "objcopy.D")
    for t in a:
        if t in ("--preserve-dates", "--disable-deterministic-archives") or short(t, "p") or short(t, "U"):
            c.v("flag-forbidden", t, "objcopy.D")

def chk_python(c, a):
    flags, si, bad = pyparse(a)
    for b in bad + ["-" + f for f in "IE" if f in flags]:
        c.v("flag-forbidden", b, "python.flags")
    if "B" not in flags and c.doc["env"].get("PYTHONDONTWRITEBYTECODE") != "1":
        c.v("flag-missing", "-B or env PYTHONDONTWRITEBYTECODE=1", "python.flags")
    if "s" not in flags:
        c.v("flag-missing", "-s", "python.flags")
    if si is not None:
        c.need_under(a[si], "script", "python.script")
    elif not bad:
        c.v("flag-missing", "script path", "python.script")

def chk_msvc(c, kind, a):
    o = opts(a, kind != "msvc-cl")
    d = c.doc
    brepro = d.get("msvc_brepro") is True
    norm = d.get("coff_normalize") is True or (kind != "msvc-cl" and d.get("pe_normalize") is True)
    if brepro == norm:
        c.v("decl-conflict" if brepro else "decl-missing", "exactly one of msvc_brepro / coff_normalize"
            + ("" if kind == "msvc-cl" else " / pe_normalize"), "msvc.decl")
    elif brepro and "brepro" not in [x.lower() for x in o]:
        c.v("flag-missing", "/Brepro (msvc_brepro declared)", "msvc.decl")
    if any(t.startswith("@") for t in a):
        c.v("response-file-unsupported", "@file in an msvc argv", "resp")
    if kind == "msvc-cl":
        nd = d.get("no_date_time_macros")
        if not (isinstance(nd, dict) and isinstance(nd.get("checked_by"), str) and nd["checked_by"].strip()):
            c.v("decl-missing", 'no_date_time_macros {"checked_by": NAME}', "cl.nodate")
        for x in o:
            if x in ("Zi", "ZI") or x.startswith("Fd") or re.fullmatch("MP[0-9]*", x):
                c.v("flag-forbidden", "/" + x, "cl.forbid")
        return
    if kind == "msvc-link":
        c.need("incremental:no" in o, "/INCREMENTAL:NO", "link.incr")
    for x in o:
        if x in ("ltcg:pginstrument", "ltcg:pgoptimize", "ltcg:pgupdate"):
            c.v("flag-forbidden", "/" + x, "msvc.ltcg")
        elif kind == "msvc-link" and x.startswith("incremental") and x != "incremental:no":
            c.v("flag-forbidden", "/" + x, "link.incr")

def check_doc(doc, root=None):
    """Violations [(rule, detail, citation)] of one stage document, in a fixed order."""
    c = Ctx(doc, root)
    check_env(c)
    kind, argv = doc.get("tool"), doc["argv"]
    if "jobs" in doc and (not isinstance(doc["jobs"], int) or isinstance(doc["jobs"], bool)):
        c.v("jobs-invalid", "jobs must be an integer", "args.jobs")
    if kind == "wine":
        e = doc["env"]
        for k, ok in (("WINEDEBUG", e.get("WINEDEBUG") == "-all"), ("WINEPREFIX", under(e.get("WINEPREFIX"), c.ins + c.outs))):
            if k not in e:
                c.v("env-missing", k, "wine.env")
            elif not ok:
                c.v("env-value", "%s=%s: want -all / a path under a declared input or output" % (k, e[k]), "wine.env")
        kind, argv = doc.get("wine_tool"), argv[1:]
        if kind not in MSVC:
            c.v("tool-undeclared" if kind is None else "tool-unlisted", "wine_tool %s" % kind, "wine.env")
            return c.out
    if kind not in KINDS:
        c.v({None: "tool-undeclared", "sh": "tool-unanalysable", "make": "tool-unanalysable"}.get(kind, "tool-unlisted"),
            str(kind), "tool.kind")
        return c.out
    a = expand(c, argv[1:]) if kind in GNU else argv[1:]
    for t in (a[:pyparse(a)[1] or 0] if kind == "python" else a):
        if re.fullmatch("-j[0-9]*", t) and kind != "objcopy" or t == "--jobs" or t.startswith("--jobs="):
            c.v("jobs-in-argv", t, "args.jobs")
    if kind in MSVC:
        chk_msvc(c, kind, a)
    else:
        {"gcc": chk_gcc, "as": chk_as, "ld": chk_ld, "ar": chk_ar, "objcopy": chk_objcopy, "python": chk_python}[kind](c, a)
    return c.out

def load(path):
    with open(path, "rb") as f:
        d = json.loads(f.read().decode("utf-8"))
    strs = lambda x: isinstance(x, list) and all(isinstance(y, str) for y in x)  # noqa: E731
    if not (isinstance(d, dict) and d.get("format", "fnvnx-stage/1") == "fnvnx-stage/1" and strs(d.get("argv")) and d["argv"]
            and strs(d.get("outputs")) and isinstance(d.get("env"), dict) and strs(list(d["env"].values()))
            and isinstance(d.get("inputs"), list) and all(isinstance(i, dict) and strs([i.get("at")]) for i in d["inputs"])):
        raise ValueError("not a fnvnx-stage/1 document")
    return d

def main(args):
    usage = "usage: toolflags.py check STAGE.json... [--root DIR] [--json OUT.json] | policy"
    if args == ["policy"]:
        for k in sorted(POLICY):
            print("%s | %s | %s" % POLICY[k])
        return 0
    opt, files, i = {"--root": None, "--json": None}, [], 1
    while args[:1] == ["check"] and i < len(args):
        if args[i] in opt and i + 1 < len(args):
            opt[args[i]] = args[i + 1]
            i += 2
        elif args[i].startswith("--"):
            break
        else:
            files.append(args[i])
            i += 1
    if args[:1] != ["check"] or i < len(args) or not files:
        print(usage, file=sys.stderr)
        return 1
    stages, counts, lines = [], {}, []
    for f in files:
        try:
            d = load(f)
        except (OSError, ValueError) as ex:
            print("toolflags: cannot read %s: %s" % (f, ex), file=sys.stderr)
            return 1
        name = d["stage"] if isinstance(d.get("stage"), str) else os.path.basename(f)
        vs = check_doc(d, opt["--root"])
        stages.append({"stage": name, "file": f, "violations": [dict(zip(("rule", "detail", "citation"), v)) for v in vs]})
        for r, x, z in vs:
            counts[r] = counts.get(r, 0) + 1
            lines.append("%s: %s %s [%s]" % (name, r, x, z))
    total = sum(counts.values())
    if len(lines) > 198:
        lines[198:] = ["... %d more (see --json)" % (len(lines) - 198)]
    for ln in lines:
        print(ln)
    print("toolflags: %d stages, %d violations (%s)" % (len(files), total, ", ".join(
        "%s: %d" % (r, counts[r]) for r in sorted(counts)) or "none"))
    if opt["--json"]:
        with open(opt["--json"], "w", encoding="utf-8") as f:
            json.dump({"format": FORMAT, "stages": stages, "summary": {"stages": len(files), "violations": total,
                       "by_rule": counts}}, f, indent=1, sort_keys=True)
    return 2 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
