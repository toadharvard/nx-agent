#!/usr/bin/env python3
"""Tests for the command line of recipe.py (one normalization of --root, no uncaught exception on a bad argument) and for the stage progress lines
(unitstage.progress: two flushed lines per stage on stderr). Plain script: exit 0 on pass. The progress cases use stages whose result is already in a
scratch store (hit, and a stored failure), so no sandbox run is needed; a built stage prints through the same function."""
import contextlib
import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import recipe  # noqa: E402
import sandbox  # noqa: E402
import unitstage  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)))
    fails += 0 if ok else 1


os.chdir(os.path.join(VERIFY, "..", "..", ".."))   # the checkout root
ROOT = "ports/fnv-nx/loader"
check("a relative root is unchanged", recipe.norm_root(".", ROOT) == ROOT)
check("an absolute root inside the checkout becomes the relative id", recipe.norm_root(".", os.path.abspath(ROOT)) == ROOT)
check("a variant suffix is kept", recipe.norm_root(".", os.path.abspath(ROOT) + "@v") == ROOT + "@v")
for bad, want in ((tempfile.gettempdir(), "root must be inside the checkout: " + tempfile.gettempdir()), ("/", "root must be inside the checkout: /"),
                  (os.path.abspath("."), "root must be inside the checkout")):
    try:
        recipe.norm_root(".", bad)
        check("outside the checkout is red: " + bad, False)
    except recipe.RecipeError as e:
        check("outside (or the checkout itself) is red: " + bad, want in e.errors[0], e.errors)
try:
    recipe.norm_root(".", "ports/fnv-nx/nope")
    check("an unknown root is red", False)
except recipe.RecipeError as e:
    check("an unknown root is red, not a KeyError", "not an admitted root" in e.errors[0], e.errors)
out, err = io.StringIO(), io.StringIO()
with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
    rc = recipe.main(["check", "--root", tempfile.gettempdir()])
check("a bad --root of a command exits red with the message, no exception", rc == 2 and "root must be inside the checkout" in err.getvalue(), (rc, err.getvalue()))

store = tempfile.mkdtemp(prefix="recipe-cli-")
docs = [{"format": "fnvnx-stage/1", "stage": "t:%d" % i, "argv": ["/bin/x"], "cwd": "/", "env": {}, "inputs": [], "outputs": ["/out"]} for i in (1, 2)]
sandbox._store_result(store, sandbox.key_of(docs[0]), {"rc": 0})
sandbox._store_result(store, sandbox.key_of(docs[1]), {"rc": 1})
err = io.StringIO()
with contextlib.redirect_stderr(err):
    for d in docs:
        unitstage.run_stage(store, d, False)
lines = err.getvalue().splitlines()
k = [sandbox.key_of(d) for d in docs]
check("a two-stage build prints 4 lines in order", len(lines) == 4 and [l.split()[1] for l in lines] == ["t:1", "t:1", "t:2", "t:2"], lines)
check("a line is `stage ID KEY start` then `stage ID KEY hit|built|failed SECONDS`",
      lines[0] == "stage t:1 %s start" % k[0] and lines[1].startswith("stage t:1 %s hit " % k[0]) and lines[1].endswith("s")
      and lines[2] == "stage t:2 %s start" % k[1] and lines[3].startswith("stage t:2 %s failed " % k[1]), lines)
sys.exit(1 if fails else 0)
