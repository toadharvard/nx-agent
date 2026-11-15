#!/usr/bin/env python3
"""Gate "guestlink" (fnvnx-gate/1, class A of VERDICT.md): the guest link's libraries are what the pinned guest runtime's own driver derives, and nothing
is stored: link/guest.json holds the script only (a stored copy of the derivation is a second declaration), and the link derives the libraries itself
(recipe.Job.guest_libs, keyed stages). This gate recomputes the same derivation and reds when it does not hold.
    python3 -I gate_guestlink.py    cwd = checkout root, env GATE_OUT, optional GATE_CACHE and GATE_COMMIT; writes GATE_OUT/guestlink.json
Derivation (RECIPE.md, Link): the C++ driver of the pinned runtime external (verify/guest_compiler.json `runtime_driver`) is run, as the hermetic recipe
stage `drv:<root>`, with the guest semantic flags (flags.semantics.common, filtered for C++ by the pinned compiler) and `-pie -###` on a one-line C++
file; its last line is the link command: recipe.parse_driver_link reads startfiles, dirs, names and endfiles. An object of startfiles/endfiles is
excluded by the one-definition rule (recipe.onedef over the symbols of the green guest roots) or the entry rule (recipe.entry_object), nothing else.
Rule (red otherwise): link/guest.json is {script}; every derived start/end file lies under the pinned runtime external (recipe.outside_files) and in the
multilib directory the semantic flags select (`-print-multi-directory`, recipe.in_multilib). Items: every derived entry of dirs, names, startfiles and
endfiles; certified = all of them when the rule holds. A red guest root build or a failing driver run is red with the reason."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_build  # noqa: E402
import recipe  # noqa: E402

KEYS = ("dirs", "names", "startfiles", "endfiles")


def derive(repo, cache):
    """(derived libs without `external`, the excluded objects, pin): the driver's link command read by recipe.parse_driver_link, minus the objects of
    recipe.onedef over the symbols of every green guest root. Raises ValueError (input), recipe.RecipeError, recipe.BuildRed."""
    admit = recipe.load_admit(repo)
    pin = recipe.load_pin(repo)
    if pin is None or not isinstance(pin.get("runtime_driver"), str):
        raise ValueError("verify/guest_compiler.json with runtime_driver is required")
    guests = [r for r in gate_build.buildable_roots(repo) if recipe.entry_of(admit, r).get("target") == "guest"]
    if not guests:
        raise ValueError("no guest root")
    rootdef = set()
    for r in guests:
        res, _ = gate_build.ensure(r, cache, repo)
        if res["status"] == "red":
            raise ValueError("%s: build red at stage %s" % (r, res["failure"]["stage"]))
        rootdef |= {d["symbol"] for d in res["defined"]}
    j = recipe.Job(repo, guests[0], os.path.join(cache, "store"), 1, os.path.join(cache, "external"))
    prof = recipe.load_guest_link(repo)
    with tempfile.TemporaryDirectory(prefix="guestlink-") as ws:
        derived, gone = j.guest_libs(ws, rootdef, open(os.path.join(repo, recipe.TREE, prof["script"])).read())
    return derived, gone, pin


def main():
    tmp = None if os.environ.get("GATE_CACHE") else tempfile.mkdtemp(prefix="gate-guestlink-")
    cache, repo = os.environ.get("GATE_CACHE") or tmp, "."
    why, derived, gone = "", {}, []
    try:
        prof = recipe.load_guest_link(repo)
        files = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.split("\n")
        errs = recipe.guest_link_errors(prof, recipe.load_pin(repo), files, recipe.load_admit(repo)) if prof is not None else ["link/guest.json is required"]
        if errs:
            raise ValueError("; ".join(errs))
        derived, gone, pin = derive(repo, cache)
    except recipe.RecipeError as e:
        why = "invalid recipe: %s" % "; ".join(e.errors)[:300]
    except recipe.BuildRed as e:
        why = "derivation red: %s" % (" | ".join(e.args[0].get("stderr", []))[:400] or str(e.args[0])[:300],)
    except (ValueError, KeyError, OSError) as e:
        why = "input error: %s" % (str(e)[:300] or type(e).__name__)
    finally:
        if tmp and not os.environ.get("GATE_KEEP"):
            shutil.rmtree(tmp, ignore_errors=True)
    items = sorted((k, v) for k in KEYS for v in derived.get(k, []) if isinstance(v, str)) if not why else []
    status = "red" if why else "green"
    detail = why or "%d entries derived from the runtime driver, all under the pinned runtime and in the selected multilib; excluded by onedef/entry: %s" % (len(items), ", ".join(gone) or "none")
    res = {"format": "fnvnx-gate/1", "name": "guestlink", "status": status, "universe": len(items), "certified": 0 if why else len(items),
           "uncertified": [], "detail": detail}
    with open(os.path.join(os.environ["GATE_OUT"], "guestlink.json"), "w") as fh:
        json.dump(res, fh, sort_keys=True)
    print("guestlink: %s %d/%d %s" % (status, res["certified"], len(items), detail[:300]))
    return 0 if status == "green" else 2


if __name__ == "__main__":
    sys.exit(main())
