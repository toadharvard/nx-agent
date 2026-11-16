#!/usr/bin/env python3
"""gen_units - the unit list of the CORE-MATH root (recipe `generated` stage of ports/fnv-nx/platform/coremath).

    gen_units.py EXT OUT FUNCTION...   EXT = the pinned CORE-MATH external (/ext/core-math); OUT = the stage's /out (writes OUT/units.list)

One `core-math:<path>` line per function: the binary64 source src/binary64/<f>/<f>.c of the external, the form the recipe's generated `sources` takes. The function set is the
argument list, which build.json holds once. A unit is copied alone into the unit view, so a header that it includes by a quoted name and that lies beside it in the external
(tint.h, dint_log2p1.h) is written verbatim to OUT/inc/<name>, the one include directory of the root (build.json `include`). The set of headers is the closure of the
quoted includes of the units, found by reading them, not a list. Red: a function whose source is no file of the external; a quoted include found beside no unit and
in no earlier header's directory; two headers of one name with different content."""
import os
import re
import sys


def main(argv):
    if len(argv) < 3:
        sys.exit("usage: gen_units.py EXT OUT FUNCTION...")
    ext, out, funcs = argv[0], argv[1], argv[2:]
    lines = [a for a in funcs if ":" in a]   # an argument `<run step>:<dir>` is a `sources` line as it stands (the units of a run step's directory output, RECIPE.md)
    funcs = [a for a in funcs if ":" not in a]
    for f in funcs:
        rel = "src/binary64/%s/%s.c" % (f, f)
        if not os.path.isfile(os.path.join(ext, rel)):
            sys.exit("gen_units: %s is no file of the external" % rel)
        lines.append("core-math:%s" % rel)
    os.makedirs(os.path.join(out, "inc"), exist_ok=True)
    seen = {}
    todo = ["src/binary64/%s/%s.c" % (f, f) for f in funcs]
    while todo:
        rel = todo.pop()
        here = os.path.dirname(rel)
        with open(os.path.join(ext, rel), errors="surrogateescape") as fh:
            for m in re.finditer(r'^\s*#\s*include\s+"([^"]+)"', fh.read(), re.M):
                inc = os.path.normpath(os.path.join(here, m[1]))
                if not os.path.isfile(os.path.join(ext, inc)):
                    sys.exit("gen_units: %s includes \"%s\", which is no file beside it in the external" % (rel, m[1]))
                name = os.path.basename(inc)
                data = open(os.path.join(ext, inc), "rb").read()
                if name in seen and seen[name] != data:
                    sys.exit("gen_units: two headers named %s differ" % name)
                if name not in seen:
                    seen[name] = data
                    todo.append(inc)
    for name, data in seen.items():
        with open(os.path.join(out, "inc", name), "wb") as fh:
            fh.write(data)
    with open(os.path.join(out, "units.list"), "w") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
