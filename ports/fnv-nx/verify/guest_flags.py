#!/usr/bin/env python3
"""guest_flags.py - the semantic flag set of the guest compiler for one language, as the recipe applies it.
  python3 verify/guest_flags.py --lang c|c++|asm [--origin native|lifted] [--cache DIR]   prints a JSON array on stdout.
It is recipe.semantics_for(pinned driver, language, origin=ORIGIN) on the flags.semantics of verify/guest_compiler.json: the one function the recipe uses
for every guest compile stage (the set of the root's origin, native = common, lifted = common + lifted_extra, minus the flags the pinned driver itself
says do not apply to the language). There is no second list.
The pinned compiler is fetched by the pin of guest_compiler.json (recipe.fetch_externals) into DIR (default $GATE_CACHE, else ~/.cache/fnvnx-guest-flags).
Exit 1 with the reason on stderr if the pinned compiler cannot be fetched."""
import argparse
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import recipe  # noqa: E402

KIND = {"c": "c", "c++": "cxx", "asm": "asm"}


def flags_for(lang, cache, origin="native"):
    """The JSON-able list for LANG ("c", "c++", "asm") using the pinned compiler fetched under CACHE."""
    pin = json.load(open(os.path.join(HERE, "guest_compiler.json")))
    ext = pin["external"]
    os.makedirs(os.path.join(cache, "store"), exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gflags-") as ws:   # the checkout is scratch: nothing is left behind
        got = recipe.fetch_externals([ext], os.path.join(cache, "store"), os.path.join(cache, "external"), ws)
        pre = got[ext["name"]]["host"] + "/bin/aarch64-linux-gnu-"
        return recipe.semantics_for(pre + ("g++" if lang == "c++" else "gcc"), KIND[lang], origin=origin)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lang", required=True, choices=sorted(KIND))
    ap.add_argument("--origin", default="native", choices=["native", "lifted"], help="the root's cxx_origin class")
    ap.add_argument("--cache", default=os.environ.get("GATE_CACHE") or os.path.expanduser("~/.cache/fnvnx-guest-flags"))
    a = ap.parse_args(argv)
    try:
        out = flags_for(a.lang, a.cache, a.origin)
    except recipe.BuildRed as e:
        print("guest_flags: %s" % (e.args,), file=sys.stderr)
        return 1
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
