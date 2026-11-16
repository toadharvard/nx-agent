#!/usr/bin/env python3
# reads: ports/fnv-nx/verify/env.json
"""artifacts - the one source of the artifacts repository URL (L1; stdlib only, so the farm's setup scripts can run it before anything is installed).
    artifacts.py url [--ext DIR] [--env F]     prints the URL: the record DIR/artifacts.url when DIR has one, else the repository pinned by env.json (debs.mirror.repo)
A run's caller records its URL beside the external cache (l1.py --artifacts, recipe.py --artifacts: `record`); nothing reads the process environment."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FILE = "artifacts.url"


def pinned(env=None):
    """The artifacts repository of env.json (debs.mirror.repo)."""
    with open(env or os.path.join(HERE, "env.json")) as f:
        return "https://github.com/%s" % json.load(f)["debs"]["mirror"]["repo"]


def recorded(extcache):
    """The URL recorded in EXTCACHE, or None."""
    p = os.path.join(extcache, FILE)
    if os.path.isfile(p):
        with open(p) as f:
            return f.read().strip()
    return None


def record(extcache, url):
    os.makedirs(extcache, exist_ok=True)
    with open(os.path.join(extcache, FILE), "w") as f:
        f.write(url + "\n")


def main(a):
    if a[:1] != ["url"]:
        print("usage: artifacts.py url [--ext DIR] [--env F]", file=sys.stderr)
        return 1
    o = dict(zip(a[1::2], a[2::2]))
    print((recorded(o["--ext"]) if "--ext" in o else None) or pinned(o.get("--env")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
