#!/usr/bin/env python3
"""The programs shell scripts invoke, derived from a real parse (shfmt --tojson, the pinned shfmt package; stdlib otherwise).
    python3 -I script_programs.py FILE.sh... [--json]
Prints one program name per line, then, on stderr, one `dynamic:` line per command whose name is not a literal; any dynamic command
makes the exit status 1 (a name that is a variable or an expansion cannot be derived statically: the build declares it or removes it).
Every scanned program has one record in program_classes.json (leaf / runner with its argv rule and the man page it cites / opaque); a
program without a record is printed as `unclassified:` and makes the exit status 1. Rule: shfmt parses each file into a syntax tree; every command node (CallExpr: simple commands, also inside $(..), `..`, pipelines,
subshells, functions, loops, conditions, process substitutions) names its program by its first word. That word is literal when all its
parts are literal text (plain or quoted). Not programs: shell builtins and keywords (bash's own list, `bash -c compgen -b`), functions
the scripts define, and words with a `/` (files of the build). A runner is followed to the program it runs: exec command env nohup time
nice timeout sudo setsid stdbuf chroot xargs, `find ... -exec/-execdir CMD`, and `sh|bash|dash -c 'LITERAL'` (the literal is parsed
the same way). Here-documents are data."""
import json
import os
import subprocess
import sys

CLASSES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "program_classes.json")


def load_classes(path=None):
    """{program: record} of program_classes.json (see its `doc`): the classification of every program a scanned script may invoke."""
    return json.load(open(path or CLASSES))["programs"]


def parse(src):
    p = subprocess.run(["shfmt", "--tojson"], input=src, capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit("script_programs: shfmt: " + p.stderr.strip()[:300])
    return json.loads(p.stdout)


def prefix(w):
    """The literal text of Word W up to its first non-literal part (a path such as ../gcc-$VER/configure has the prefix ../gcc-)."""
    out = []
    for part in w.get("Parts", []):
        if part["Type"] not in ("Lit", "SglQuoted"):
            break
        out.append(part["Value"])
    return "".join(out)


def literal(w):
    """The text of Word W if every part is literal, else None."""
    out = []
    for part in w.get("Parts", []):
        t = part["Type"]
        if t == "Lit":
            out.append(part["Value"])
        elif t == "SglQuoted":
            out.append(part["Value"])
        elif t == "DblQuoted":
            inner = literal({"Parts": part.get("Parts", [])})
            if inner is None:
                return None
            out.append(inner)
        else:
            return None
    return "".join(out)


def walk(n, f):
    if isinstance(n, dict):
        f(n)
        for v in n.values():
            walk(v, f)
    elif isinstance(n, list):
        for v in n:
            walk(v, f)


def builtins():
    p = subprocess.run(["bash", "-c", "compgen -b; compgen -k"], capture_output=True, text=True, check=True)
    return set(p.stdout.split())


def programs(src, classes=None, bi=None):
    """(programs, dynamic, unclassified) of shell text SRC: the programs it invokes, the commands whose name is not a literal, and the
    programs that have no record in CLASSES (a scanned program without a classification is red). A `runner` record is followed by its
    rule (argv: after `skip` positionals and the options, `optarg` options take a value; flag: after one of the flags, find -exec;
    string: the literal after the flag is shell text and is scanned too); a builtin is skipped unless it has a record (exec, command,
    time are runners)."""
    classes = classes if classes is not None else load_classes()
    bi = bi if bi is not None else builtins()
    tree = parse(src)
    funcs = set()
    walk(tree, lambda n: funcs.add(n["Name"]["Value"]) if n.get("Type") == "FuncDecl" else None)
    progs, dyn, unc = set(), [], set()

    def isopt(x):
        return x is not None and x.startswith("-")

    def isassign(x):
        return x is not None and "=" in x and x.split("=")[0].replace("_", "a").isalnum()

    def name(args, i):
        """The command at word I of ARGS: scan leading VAR=value words, resolve it, follow it by its record."""
        while i < len(args) and isassign(literal(args[i])):
            i += 1
        if i >= len(args):
            return
        w = literal(args[i])
        if w is None:
            if "/" not in prefix(args[i]):   # a path with an expansion is a file of the build, not a program
                dyn.append(src[args[i]["Pos"]["Offset"]:args[i]["End"]["Offset"]])
            return
        if "/" in w or w in funcs or (w in bi and w not in classes):
            return
        if w not in bi:
            progs.add(w)
        rec = classes.get(w)
        if rec is None:
            unc.add(w)
            return
        if rec["class"] != "runner":
            return
        rule = rec["rule"]
        if rule["kind"] == "argv":
            j, skip = i + 1, rule["skip"]
            while j < len(args):   # the runner's options (and their values), VAR=value words, positionals
                x = literal(args[j])
                if isopt(x):
                    j += 2 if x in rule["optarg"] else 1
                elif isassign(x):
                    j += 1
                elif skip:
                    skip -= 1
                    j += 1
                else:
                    break
            name(args, j)
        elif rule["kind"] == "flag":
            for j in range(i + 1, len(args) - 1):
                if literal(args[j]) in rule["flags"]:
                    name(args, j + 1)
        elif rule["kind"] == "string":
            for j in range(i + 1, len(args) - 1):
                if literal(args[j]) in rule["flags"]:
                    t = literal(args[j + 1])
                    if t is None:
                        dyn.append("%s %s <non-literal>" % (w, literal(args[j])))
                    else:
                        p2, d2, u2 = programs(t, classes, bi)
                        progs.update(p2)
                        dyn.extend(d2)
                        unc.update(u2)

    def visit(n):
        if n.get("Type") == "CallExpr" and n.get("Args"):
            name(n["Args"], 0)
    walk(tree, visit)
    return progs, dyn, unc


def main(a):
    files = [x for x in a if not x.startswith("--")]
    progs, dyn, unc = set(), [], set()
    for f in files:
        p, d, u = programs(open(f, encoding="utf-8").read())
        progs |= p
        unc |= u
        dyn += ["%s: %s" % (f, x) for x in d]
    if "--json" in a:
        print(json.dumps({"programs": sorted(progs), "dynamic": dyn, "unclassified": sorted(unc)}))
    else:
        print("\n".join(sorted(progs)))
        for d in dyn:
            print("dynamic: " + d, file=sys.stderr)
        for u in sorted(unc):
            print("unclassified: " + u, file=sys.stderr)
    return 1 if dyn or unc else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
