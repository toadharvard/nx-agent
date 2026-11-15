#!/usr/bin/env python3
"""Gate "guestlink" (fnvnx-gate/1, class A of VERDICT.md): the guest link profile link/guest.json holds exactly the values the pinned guest runtime's
own driver derives, so a hand-picked startfile, library directory or library name is red.
    python3 -I gate_guestlink.py [--write]    cwd = checkout root, env GATE_OUT, optional GATE_CACHE and GATE_COMMIT; writes GATE_OUT/guestlink.json
    (--write first rewrites `libs` of link/guest.json from the derivation: the way the profile is made; the gate then compares it)
Derivation (RECIPE.md, Link): the C++ driver of the pinned runtime external (verify/guest_compiler.json `runtime_driver`) is run, as the hermetic recipe
stage `drv:<root>`, with the guest semantic flags (flags.semantics.common, filtered for C++ by the pinned compiler) and `-pie -###` on a one-line C++
file. Its last line is the link command of the multilib the flags select; recipe.parse_driver_link reads startfiles (.o before the library group),
dirs (-L inside the external), names (-l, in order) and endfiles (.o after the group) from it. An object of startfiles/endfiles is then excluded by
the one-definition rule only (recipe.onedef): it defines a global or weak symbol that an admitted guest product root defines (the roots' symbols are
those of their green builds, gate_build.ensure). Nothing is excluded by a name.
Rule: the derived libs equal `libs` of link/guest.json, key by key and in order, and `libs.external` is the pinned runtime. A difference is a finding
naming the key, the derived values and the profile's. Items: every entry of dirs, names, startfiles and endfiles, derived or profiled. Certified =
the entries present in both. A red guest root build, or a driver run that fails, is red with the reason."""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_build  # noqa: E402
import recipe  # noqa: E402

KEYS = ("dirs", "names", "startfiles", "endfiles")


def judge(derived, prof):
    """(items, findings): ITEMS [(key, value)] over derived and profile entries, FINDINGS [str], one per key whose derived list differs from the profile's."""
    items, finds = set(), []
    for k in KEYS:
        d, p = derived.get(k, []), prof.get(k, [])
        items |= {(k, v) for v in list(d) + list(p) if isinstance(v, str)}
        if d != p:
            finds.append("%s: derived %s, profile %s" % (k, d, p))
    return sorted(items), finds


def derive(repo, cache):
    """(derived libs without `external`, the excluded objects, pin): the driver's link command read by recipe.parse_driver_link, minus the objects of
    recipe.onedef over the symbols of every green guest root. Raises ValueError (input), recipe.RecipeError, recipe.BuildRed."""
    admit = recipe.load_admit(repo)
    pin = recipe.load_pin(repo)
    if pin is None or not isinstance(pin.get("runtime_driver"), str):
        raise ValueError("verify/guest_compiler.json with runtime_driver is required")
    guests = [r for r in gate_build.buildable_roots(repo) if admit[r].get("target") == "guest"]
    if not guests:
        raise ValueError("no guest root")
    rootdef = set()
    for r in guests:
        res, _ = gate_build.ensure(r, cache, repo)
        if res["status"] == "red":
            raise ValueError("%s: build red at stage %s" % (r, res["failure"]["stage"]))
        rootdef |= {d["symbol"] for d in res["defined"]}
    j = recipe.Job(repo, guests[0], os.path.join(cache, "store"), 1, os.path.join(cache, "external"))
    with tempfile.TemporaryDirectory(prefix="guestlink-") as ws:
        text, at = j.link_probe(ws)
        raw = recipe.parse_driver_link(text, at)
        host = j.ext[pin["runtime"]["name"]]["host"]
        objs = {p: {d["symbol"] for d in recipe.symbols(os.path.join(host, p), p)[0]} for p in raw["startfiles"] + raw["endfiles"]}
    gone = recipe.onedef(objs, rootdef)
    return {k: ([x for x in v if x not in gone] if k in ("startfiles", "endfiles") else v) for k, v in raw.items()}, gone, pin


def main():
    write = "--write" in sys.argv[1:]
    tmp = None if os.environ.get("GATE_CACHE") else tempfile.mkdtemp(prefix="gate-guestlink-")
    cache, repo = os.environ.get("GATE_CACHE") or tmp, "."
    why, derived, prof, gone = "", {}, {}, []
    try:
        prof_all = recipe.load_guest_link(repo)
        if prof_all is None:
            raise ValueError("link/guest.json is required")
        derived, gone, pin = derive(repo, cache)
        if write:   # regenerate the profile's libs from the derivation: the only way a value of link/guest.json is made
            prof_all["libs"] = dict(derived, external=pin["runtime"]["name"])
            with open(os.path.join(repo, recipe.GUEST_LINK), "w") as fh:
                json.dump(prof_all, fh, indent=1)
                fh.write("\n")
        prof = prof_all.get("libs", {})
        if prof_all["libs"].get("external") != pin["runtime"]["name"]:
            why = "libs.external %r is not the pinned runtime %r" % (prof_all["libs"].get("external"), pin["runtime"]["name"])
    except recipe.RecipeError as e:
        why = "invalid recipe: %s" % "; ".join(e.errors)[:300]
    except recipe.BuildRed as e:
        why = "derivation stage red: %s" % (str(e.args[0])[:300],)
    except (ValueError, KeyError, OSError) as e:
        why = "input error: %s" % (str(e)[:300] or type(e).__name__)
    finally:
        if tmp and not os.environ.get("GATE_KEEP"):
            shutil.rmtree(tmp, ignore_errors=True)
    items, finds = judge(derived, prof) if not why else ([], [])
    bad = {(k, v) for k in KEYS for v in set(derived.get(k, [])) ^ set(prof.get(k, [])) if isinstance(v, str)} if finds else set()
    cert = len([i for i in items if i not in bad])
    if why:
        status, detail = "red", why
    elif finds:
        status, detail = "red", "%d keys differ: %s" % (len(finds), "; ".join(finds)[:600])
    else:
        status = "green"
        detail = "%d entries derived from the runtime driver equal link/guest.json; excluded by onedef: %s" % (len(items), ", ".join(gone) or "none")
    res = {"format": "fnvnx-gate/1", "name": "guestlink", "status": status, "universe": len(items), "certified": cert,
           "uncertified": sorted("%s:%s" % x for x in bad)[:50], "detail": detail}
    with open(os.path.join(os.environ["GATE_OUT"], "guestlink.json"), "w") as fh:
        json.dump(res, fh, sort_keys=True)
    print("guestlink: %s %d/%d %s" % (status, cert, len(items), detail[:300]))
    return 0 if status == "green" else 2


if __name__ == "__main__":
    sys.exit(main())
