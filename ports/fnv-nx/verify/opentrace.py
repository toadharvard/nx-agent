#!/usr/bin/env python3
"""opentrace: prove a build stage is hermetic by checking every file read under strace.

  opentrace.py run   --manifest M --out R [--keep-trace F] -- cmd args...
  opentrace.py check --manifest M --trace T --out R
  opentrace.py key   --manifest M
Exit: 0 green, 2 red, 1 usage/tool error.
"""
import argparse, hashlib, json, os, re, subprocess, sys, tempfile

FORMAT = "fnvnx-opentrace/1"
TRACE_SET = "open,openat,openat2,execve,execveat,chdir,fchdir,clone,clone3,fork,vfork"
ENV_HINTS = ("CXX", "CC", "ORDER_KEYS", "JOBS")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(p):
    if os.path.islink(p):
        return sha(b"link:" + os.readlink(p).encode("utf-8", "surrogateescape"))
    with open(p, "rb") as f:
        return sha(f.read())


def input_files(inputs, strict=True):
    out = []
    for root in inputs:
        if not os.path.lexists(root):
            if not strict:
                continue
            raise SystemExit("opentrace: input missing: " + root)
        if os.path.islink(root) or os.path.isfile(root):
            out.append([os.path.basename(root), file_hash(root)])
            continue
        for d, dirs, files in os.walk(root, followlinks=False):
            for n in files + [x for x in dirs if os.path.islink(os.path.join(d, x))]:
                p = os.path.join(d, n)
                out.append([os.path.relpath(p, root), file_hash(p)])
    return sorted(out)


def stage_key(m, strict=True):
    with open(os.path.abspath(__file__), "rb") as f:
        tool = sha(f.read())
    doc = {"stage": m["stage"], "files": input_files(m.get("inputs", []), strict),
           "system_id": m.get("system_id", ""),
           "env": [[n, os.environ.get(n)] for n in sorted(m.get("env", []))],
           "tool": tool}
    return sha(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode())[:16]


def under(p, roots):
    return any(p == r.rstrip("/") or p.startswith(r.rstrip("/") + "/") for r in roots)


def unescape(s):
    def f(mo):
        g = mo.group(1)
        if g[0] == "x":
            return chr(int(g[1:], 16))
        if g[0] in "01234567":
            return chr(int(g, 8))
        return {"n": "\n", "t": "\t"}.get(g, g)
    return re.sub(r"\\(x[0-9a-fA-F]{2}|[0-7]{1,3}|.)", f, s)


LINE = re.compile(r"^(\d+)\s+(.*)$")
CALL = re.compile(r"^(\w+)\((.*)\)\s+=\s+(-?\d+|\?)(?:<(.*?)>)?(?:\s+(E[A-Z0-9]+))?")
STR = re.compile(r'"((?:[^"\\]|\\.)*)"')
FLAGS = re.compile(r"\bO_[A-Z_]+\b")


UNRESOLVED = "/<unresolved-cwd>"


def parse(lines, cwd0):
    """Yield events (pid, kind, path, flags) kind in open/exec/fail; tracks cwd per pid."""
    cwd, pend, clone_pend = {}, {}, []

    def get_cwd(pid):
        if pid not in cwd:
            # the first pid is the traced command; a new pid's parent is known only when exactly one clone is in
            # flight. Otherwise its cwd is unknown, and a relative path of it resolves under UNRESOLVED (red).
            cwd[pid] = cwd0 if not cwd else cwd[clone_pend[0]] if len(clone_pend) == 1 else UNRESOLVED
        return cwd[pid]

    for raw in lines:
        mo = LINE.match(raw.rstrip("\n"))
        if not mo or mo.group(2).startswith(("+++", "---")):
            continue
        pid, body = int(mo.group(1)), mo.group(2)
        get_cwd(pid)
        if body.endswith("<unfinished ...>"):
            pend[pid] = body[:-len("<unfinished ...>")].rstrip()
            if body.startswith(("clone", "fork", "vfork")):
                clone_pend.append(pid)
            continue
        r = re.match(r"<\.\.\. (\w+) resumed>(.*)$", body)
        if r:
            body = pend.pop(pid, r.group(1) + "(") + r.group(2)
        c = CALL.match(body)
        if not c:
            continue
        name, args, ret, retpath, err = c.groups()
        ok = ret not in ("-1", "?")
        if name in ("clone", "clone3", "fork", "vfork"):
            if pid in clone_pend:
                clone_pend.remove(pid)
            if ok and int(ret) > 0 and int(ret) not in cwd:
                cwd[int(ret)] = cwd[pid]
            continue
        sm = STR.search(args)
        if name == "fchdir":
            if ok and "<" in args:
                cwd[pid] = os.path.normpath(args[args.index("<") + 1:args.rindex(">")])
            continue
        if not sm:
            continue
        path = unescape(sm.group(1))
        base = cwd[pid]
        if name in ("openat", "openat2", "execveat"):
            d = re.match(r"\s*(AT_FDCWD|\d+)(?:<(.*?)>)?\s*,", args)
            if not d:
                continue
            if d.group(2):
                base = d.group(2)
        rest = args[sm.end():]
        full = os.path.normpath(os.path.join(base, path)) if path else None
        if full is None:
            continue
        if name == "chdir":
            if ok:
                cwd[pid] = full
        elif name in ("execve", "execveat"):
            yield (pid, "exec" if ok else "fail", full, [])
        elif name.startswith("open"):
            fl = FLAGS.findall(rest if name != "open" else rest.split(")")[0])
            yield (pid, "open" if ok else "fail", full, fl)


def classify(m, events):
    inputs, system = m.get("inputs", []), m.get("system", [])
    outputs, caches = m.get("outputs", []), m.get("caches", [])
    ignore = m.get("ignore", [])
    written, seen, red, probes = set(), {}, {}, set()
    for pid, kind, p, fl in events:
        if under(p, ignore):
            continue
        if kind == "fail":
            if not any(under(p, g) for g in (inputs, system, outputs, caches)):
                probes.add(p)
            continue
        if kind == "open":
            wr = ("O_WRONLY" in fl or "O_RDWR" in fl) and ("O_CREAT" in fl or "O_TRUNC" in fl)
            if wr:
                written.add(p)
            if "O_PATH" in fl or not ("O_RDONLY" in fl or "O_RDWR" in fl):
                continue
        how = "exec" if kind == "exec" else "listdir" if "O_DIRECTORY" in fl else "open"
        if under(p, inputs):
            cls = "input"
        elif under(p, system):
            cls = "system"
        elif under(p, caches):
            cls = "cache"
        elif under(p, outputs):
            cls = "own" if p in written else "stale"
        else:
            cls = "outside"
        seen.setdefault(cls, set()).add(p)
        if cls in ("stale", "outside") and p not in red:
            red[p] = {"path": p, "class": cls, "pid": pid, "how": how}
    return seen, red, probes


def build_report(m, trace_lines, cwd0, cmd_rc=0):
    seen, red, probes = classify(m, parse(trace_lines, cwd0))
    reds = [red[p] for p in sorted(red)]
    dirs = {}
    for x in reds:
        d = os.path.dirname(x["path"])
        dirs[d] = dirs.get(d, 0) + 1
    top = sorted(dirs.items(), key=lambda kv: (-kv[1], kv[0]))[:30]
    listed = set(m.get("env", []))
    unlisted = sorted(n for n in os.environ
                      if (n.startswith("FNVNX_") or n in ENV_HINTS) and n not in listed)
    rep = {"format": FORMAT, "stage": m["stage"], "key": stage_key(m, cmd_rc is not None),
           "verdict": "red" if reds or cmd_rc != 0 else "green",
           "counts": {k: len(v) for k, v in sorted(seen.items())},
           "red": reds[:200], "red_dirs": [{"dir": d, "count": n} for d, n in top],
           "probe_outside": len(probes), "env_unlisted": unlisted}
    if cmd_rc is not None:
        rep["cmd_rc"] = cmd_rc
    return rep


def finish(rep, out):
    with open(out, "w") as f:
        json.dump(rep, f, indent=1, sort_keys=True)
        f.write("\n")
    print("opentrace %s: %s key=%s counts=%s probe_outside=%d" % (
        rep["stage"], rep["verdict"], rep["key"], rep["counts"], rep["probe_outside"]))
    return 0 if rep["verdict"] == "green" else 2


def main(argv):
    ap = argparse.ArgumentParser(prog="opentrace.py")
    ap.add_argument("mode", choices=["run", "check", "key"])
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out")
    ap.add_argument("--trace")
    ap.add_argument("--keep-trace")
    ap.add_argument("cmd", nargs="*")
    if "--" in argv:
        i = argv.index("--")
        argv, cmd = argv[:i], argv[i + 1:]
    else:
        cmd = []
    a = ap.parse_args(argv)
    try:
        with open(a.manifest) as f:
            m = json.load(f)
        cwd0 = m.get("cwd") or os.getcwd()
        if a.mode == "key":
            print(stage_key(m))
            return 0
        if not a.out:
            raise SystemExit("opentrace: --out required")
        if a.mode == "check":
            if not a.trace:
                raise SystemExit("opentrace: --trace required")
            with open(a.trace, errors="surrogateescape") as f:
                return finish(build_report(m, f.readlines(), cwd0, None), a.out)
        if not cmd:
            raise SystemExit("opentrace: run needs -- cmd")
        tf = a.keep_trace or tempfile.mkstemp(prefix="opentrace-", suffix=".strace")[1]
        try:
            rc = subprocess.run(["strace", "-f", "-qq", "-y", "-e", "trace=" + TRACE_SET,
                                 "-o", tf, "--"] + cmd).returncode
            with open(tf, errors="surrogateescape") as f:
                lines = f.readlines()
        finally:
            if not a.keep_trace and os.path.exists(tf):
                os.unlink(tf)
        return finish(build_report(m, lines, cwd0, rc), a.out)
    except (OSError, KeyError, ValueError) as e:
        print("opentrace: error: %r" % (e,), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
