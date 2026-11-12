#!/usr/bin/env python3
"""The programs shell scripts invoke, derived from a real parse (shfmt --tojson, the pinned shfmt package; stdlib otherwise).
    python3 -I script_programs.py FILE.sh... [--json]
Prints one program name per line, then, on stderr, one `dynamic:` line per command whose name is not a literal; any dynamic command
makes the exit status 1 (a name that is a variable or an expansion cannot be derived statically: the build declares it or removes it).
Rule: shfmt parses each file into a syntax tree; every command node (CallExpr: simple commands, also inside $(..), `..`, pipelines,
subshells, functions, loops, conditions, process substitutions) names its program by its first word. That word is literal when all its
parts are literal text (plain or quoted). Not programs: shell builtins and keywords (bash's own list, `bash -c compgen -b`), functions
the scripts define, and words with a `/` (files of the build). A runner is followed to the program it runs: exec command env nohup time
nice timeout sudo setsid stdbuf chroot xargs, `find ... -exec/-execdir CMD`, and `sh|bash|dash -c 'LITERAL'` (the literal is parsed
the same way). Here-documents are data."""
import json
import subprocess
import sys

WRAP = {"exec": 0, "command": 0, "env": 0, "nohup": 0, "time": 0, "nice": 0, "sudo": 0, "setsid": 0, "stdbuf": 0, "xargs": 0,
        "chroot": 1, "timeout": 1}   # runner -> positional arguments before the command (chroot DIR, timeout DURATION)
OPTARG = {"nice": {"-n"}, "timeout": {"-s", "-k"}, "env": {"-u", "-C", "-S"}, "sudo": {"-u", "-g", "-C", "-h", "-p", "-r", "-t", "-U"},
          "xargs": {"-n", "-I", "-P", "-L", "-d", "-s", "-a", "-E"}, "stdbuf": set(), "chroot": {"--userspec", "--groups"},
          "exec": {"-a"}, "command": set(), "nohup": set(), "time": {"-f", "-o"}, "setsid": set()}   # options of runners that take a value
SHELLS = {"sh", "bash", "dash"}


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


def programs(src, bi=None):
    """(set of programs, list of dynamic commands) of shell text SRC."""
    bi = bi if bi is not None else builtins()
    tree = parse(src)
    funcs = set()
    walk(tree, lambda n: funcs.add(n["Name"]["Value"]) if n.get("Type") == "FuncDecl" else None)
    progs, dyn = set(), []

    def name(args, i):
        """Resolve the command whose name is word I of ARGS (following runners); add it to progs/dyn."""
        while i < len(args):
            w = literal(args[i])
            if w is None:
                if "/" not in prefix(args[i]):   # a path with an expansion is a file of the build, not a program
                    dyn.append(src[args[i]["Pos"]["Offset"]:args[i]["End"]["Offset"]])
                return
            if w.startswith("-") or ("=" in w and w.split("=")[0].replace("_", "a").isalnum()):
                i += 1   # an option or VAR=value of a runner
                continue
            break
        else:
            return
        w = literal(args[i])
        if "/" in w or w in funcs or (w in bi and w not in WRAP):
            return
        if w not in bi:
            progs.add(w)
        if w in WRAP:
            j, skip = i + 1, WRAP[w]
            while j < len(args):   # the runner's own options (and their values), VAR=value words and positional arguments
                x = literal(args[j])
                if x is not None and x.startswith("-"):
                    j += 2 if x in OPTARG.get(w, ()) else 1
                elif x is not None and "=" in x and x.split("=")[0].replace("_", "a").isalnum():
                    j += 1
                elif skip:
                    skip -= 1
                    j += 1
                else:
                    break
            name(args, j)
        elif w in SHELLS:
            for j in range(i + 1, len(args) - 1):
                if literal(args[j]) == "-c":
                    s = literal(args[j + 1])
                    if s is None:
                        dyn.append("%s -c <non-literal>" % w)
                    else:
                        p2, d2 = programs(s, bi)
                        progs.update(p2)
                        dyn.extend(d2)
        elif w == "find":
            for j in range(i + 1, len(args) - 1):
                if literal(args[j]) in ("-exec", "-execdir"):
                    name(args, j + 1)

    def visit(n):
        if n.get("Type") == "CallExpr" and n.get("Args"):
            name(n["Args"], 0)
    walk(tree, visit)
    return progs, dyn


def main(a):
    files = [x for x in a if not x.startswith("--")]
    progs, dyn = set(), []
    for f in files:
        p, d = programs(open(f, encoding="utf-8").read())
        progs |= p
        dyn += ["%s: %s" % (f, x) for x in d]
    if "--json" in a:
        print(json.dumps({"programs": sorted(progs), "dynamic": dyn}))
    else:
        print("\n".join(sorted(progs)))
        for d in dyn:
            print("dynamic: " + d, file=sys.stderr)
    return 1 if dyn else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
