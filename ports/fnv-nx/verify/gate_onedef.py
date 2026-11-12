#!/usr/bin/env python3
"""Gate "onedef" (fnvnx-gate/1): one definition, one source. A name, a COMDAT group, a source file and a header each come from ONE place.
    python3 -I gate_onedef.py        cwd = checkout root, env GATE_OUT, optional GATE_CACHE and GATE_COMMIT; writes GATE_OUT/onedef.json
Rules (by symbol tables and file content, never by a name's text):
  (a) Per linked image (a root with a `link`; its objects are those of the root itself and of every root of its `link.roots`): a GLOBAL or WEAK
      definition outside every COMDAT group comes from exactly ONE root. Two roots defining one name is `dup-def`, whatever the binding (strong, weak):
      the linker's choice between them would pick a body by link order.
  (b) COMDAT groups of an image. The instances of one signature that come from different roots must be one definition: one digest of the group's
      member sections (bytes, flags, alignment and relocations by offset, type, addend and target: gate_linkstrict.elf_comdat), else
      `comdat-differs` (flags, plugins or a body differ: an ODR violation). Instances of one group inside ONE root need no comparison.
      NOT EXACT (stated): this compares what the linker sees. "The same source definition" (same decl_file/decl_line) needs DWARF
      `DW_AT_decl_file/line`, and the objects of a recipe carry no debug info (flags are the root's -O/-W/-std only): until a recipe
      builds them with `-g1`-class line tables, a cross-root COMDAT group whose digests are equal is accepted on equal bytes alone.
  (c') Every translation-unit source file (.c .cc .cpp .cxx .s .S) of a product root is a unit of exactly ONE root: the content sha256 of a unit
      must not be the content of a unit of another root (`unit-copy`), and a path is a unit of one root only (`unit-path`).
  (c'') Headers are shared by include, never copied: a tracked header (.h .hh .hpp .hxx .inc .inl) whose content sha256 appears under two
      paths of the admitted roots is `header-copy` for every copy. Externals are pinned trees, outside this clause; generated outputs are
      not tracked, a tool writes them from tracked inputs (they are checked through the sources and headers they are generated from).
Universe = defined global names per image + COMDAT signatures per image + units of product roots + tracked headers. Certified = items with
no finding. No product root: units/headers still count (clauses (c'), (c'') are about the tree), (a) and (b) are empty."""
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_build  # noqa: E402
import gate_linkstrict as ls  # noqa: E402
import recipe  # noqa: E402

HDR_EXT = (".h", ".hh", ".hpp", ".hxx", ".inc", ".inl")


def judge_image(img, parts):
    """IMG an image id; PARTS {root: [(name, bind, comdat signature or None)] , digests {sig: digest}} as {root: (defs, digests)}.
    -> (items [(id, name)], findings [(id, name, kind, detail)])."""
    items, out, owners, copies = [], set(), {}, {}
    for root, (defs, digests) in sorted(parts.items()):
        for name, bind, sig in defs:
            if sig is None:
                owners.setdefault(name, set()).add(root)
        for sig, dg in digests.items():
            copies.setdefault(sig, {})[root] = dg
    for name, rs in sorted(owners.items()):
        items.append((img, name))
        if len(rs) > 1:
            out.add((img, name, "dup-def", "defined by %s" % ", ".join(sorted(rs))[:200]))
    for sig, c in sorted(copies.items()):
        items.append((img + "<comdat>", sig))
        if len(c) > 1 and len(set(c.values())) > 1:
            out.add((img + "<comdat>", sig, "comdat-differs", "instances of roots %s differ" % ", ".join(sorted(c))[:200]))
    return items, sorted(out)


def judge_files(units, headers):
    """UNITS {unit path: (root, sha256)}; HEADERS {header path: sha256} -> (items, findings) of clauses (c') and (c'')."""
    items, out, bysha = [], set(), {}
    for p, (root, h) in sorted(units.items()):
        items.append(("<unit>", p))
        bysha.setdefault(h, []).append((p, root))
    for h, lst in bysha.items():
        for p, root in lst:
            other = sorted({q for q, r in lst if r != root})
            if other:
                out.add(("<unit>", p, "unit-copy", "same content as a unit of another root: %s" % ", ".join(other)[:200]))
    byhdr = {}
    for p, h in sorted(headers.items()):
        items.append(("<header>", p))
        byhdr.setdefault(h, []).append(p)
    for h, ps in byhdr.items():
        if len(ps) > 1:
            for p in ps:
                out.add(("<header>", p, "header-copy", "same content as %s" % ", ".join(q for q in ps if q != p)[:200]))
    return items, sorted(out)


def sha_of(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def tree_files(repo, adm, files):
    """(units {path: (root, sha)}, headers {path: sha}, unit-path findings) of the tracked tree."""
    units, owners, finds = {}, {}, []
    for spec in gate_build.product_roots(repo):
        root = recipe.split_spec(spec)[0]
        if spec != root:
            continue          # a variant's sources are its base's
        rec = recipe.load_recipe(repo, adm, root)
        for u in recipe.units_of(rec, root, files) if isinstance(rec, dict) else []:
            p = root + "/" + u
            owners.setdefault(p, set()).add(root)
            if p in files:
                units[p] = (root, sha_of(os.path.join(repo, p)))
    for p, rs in owners.items():
        own = {r for r in adm if adm[r].get("class") == "product" and recipe.under(p, r)}
        if len(own) > 1:
            finds.append(("<unit>", p, "unit-path", "a unit of roots %s" % ", ".join(sorted(own))[:200]))
    headers = {p: sha_of(os.path.join(repo, p)) for p in files if p.endswith(HDR_EXT) and any(recipe.under(p, r) for r in adm)}
    return units, headers, finds


def image_parts(spec, group, cache, repo):
    """{root spec: (defs, digests)} of the objects of the root specs of GROUP; a red build is a finding of the caller."""
    parts = {}
    for s in group:
        res, _ = gate_build.ensure(s, cache, repo)
        if res["status"] == "red" and not (res["failure"]["stage"].startswith("ld:") and res.get("objects")):
            # a red LINK still has its objects, which is what this gate judges (a duplicate definition makes the link red AND this gate red)
            raise recipe.RecipeError(["%s: build red (stage %s)" % (s, res["failure"]["stage"])])
        defs, dig = [], {}
        for o in res["objects"]:
            syms = ls.elf_symbols(o["object"]) or []
            member, groups = ls.elf_comdat(o["object"])
            for name, val, size, typ, bind, shn in syms:
                if bind in (ls.GLOBAL, ls.WEAK, ls.UNIQUE) and shn != 0:
                    defs.append((name, bind, member.get(shn)))
            for sig, d in groups.items():
                dig[sig] = hashlib.sha256((dig[sig] + d).encode()).hexdigest() if sig in dig else d
        parts[s] = (defs, dig)
    return parts


def main():
    tmp = None if os.environ.get("GATE_CACHE") else tempfile.mkdtemp(prefix="gate-onedef-")
    cache, repo = os.environ.get("GATE_CACHE") or tmp, "."
    items, finds, why = [], [], ""
    try:
        adm, files = recipe.load_admit(repo), recipe.tracked(repo)
        units, headers, pf = tree_files(repo, adm, files)
        fi, ff = judge_files(units, headers)
        items, finds = items + fi, finds + ff + pf
        for spec in gate_build.product_roots(repo):
            rec = recipe.load_recipe(repo, adm, spec)
            if not (isinstance(rec, dict) and rec.get("link")):
                continue
            group = [spec] + list(rec["link"].get("roots", []))
            ii, ifs = judge_image(spec, image_parts(spec, group, cache, repo))
            items, finds = items + ii, finds + ifs
    except recipe.RecipeError as e:
        why = "invalid recipe or red build: %s" % "; ".join(e.errors)[:300]
    except (ValueError, KeyError, OSError, struct.error, subprocess.CalledProcessError) as e:
        why = "input error: %s" % (str(e)[:300] or type(e).__name__)
    finally:
        if tmp and not os.environ.get("GATE_KEEP"):
            shutil.rmtree(tmp, ignore_errors=True)
    bad = {(o, s) for o, s, _, _ in finds}
    cert = len([it for it in items if it not in bad])
    kinds = {}
    for _, _, k, _ in finds:
        kinds[k] = kinds.get(k, 0) + 1
    if why:
        status, detail = "red", why
    elif finds:
        status, detail = "red", "%d findings: %s" % (len(finds), ", ".join("%s %d" % kv for kv in sorted(kinds.items())))
    else:
        status, detail = "green", "%d items: one definition, one source" % len(items)
    res = {"format": "fnvnx-gate/1", "name": "onedef", "status": status, "universe": len(items), "certified": cert,
           "uncertified": sorted("%s:%s" % x for x in bad)[:50], "detail": detail}
    os.makedirs(os.environ.get("GATE_OUT", "."), exist_ok=True)
    with open(os.path.join(os.environ.get("GATE_OUT", "."), "onedef.json"), "w") as fh:
        json.dump(res, fh, sort_keys=True)
    print("onedef: %s %d/%d %s" % (status, cert, len(items), detail[:200]))
    return 0 if status == "green" else 2


if __name__ == "__main__":
    sys.exit(main())
