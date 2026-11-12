#!/usr/bin/env python3
"""Gate "tlsmodel" (fnvnx-gate/1, class A of VERDICT.md): every thread-local reference of a guest object and of a linked guest output uses a
TLS access model the loader supports, and one symbol is accessed with one model.
    python3 -I gate_tlsmodel.py    cwd = checkout root, env GATE_OUT, optional GATE_CACHE and GATE_COMMIT; writes GATE_OUT/tlsmodel.json
Roots: every product and test root of verify/admit.json with "target": "guest", built by gate_build.ensure; a root's link group is its own
objects plus the objects of the roots in its recipe's link.roots (the objects that one linked output is made of), and its linked ELF.
Facts are the relocations of the pinned binutils' own decoding (`aarch64-linux-gnu-readelf -rW`, the binutils of verify/env.json): the type
NAME the ABI gives a relocation (R_AARCH64_P32_TLS..., R_AARCH64_TLS... for LP64), never a name of the program's text. A relocation is TLS when
its type name has the family prefix TLS (after an optional P32_) or contains TPREL or DTPREL; the family is the ABI's:
  TLSGD, TLSLD, TLSDESC (and the dynamic TLS_DTPMOD/TLS_DTPREL/TLS_TPREL/TLSDESC relocations of a linked output): dynamic TLS,
  TLSIE: initial-exec, TLSLE (and TPREL/DTPREL link-time offsets): local-exec, any other TLS name: unclassified.
Rules (a finding names the object, the symbol and the relocation):
  (1) `dynamic-tls`: 0 relocations of the dynamic family, in objects and in the linked output. Loader fact (docs/RUNTIME.md, TLS): the loader gives
      every thread a per-thread copy of the image template and has no dynamic TLS block (no __tls_get_addr, no TLS descriptors, no DTV).
  (2) `le-outside`: a local-exec relocation names a symbol that the objects of the link group define (or a section-local symbol): an offset
      from the thread pointer is only known for a symbol of the same linked output.
  (3) `ie-required`: a symbol the group does not define (host TLS) must be reached by initial-exec; it is the same finding as (2) for a
      local-exec relocation, and any other model is rule (1).
  (4) `mixed-model`: one symbol referenced with initial-exec by one relocation and local-exec by another within a group. A declaration
      `tls_model("initial-exec")` in a header makes every referencing unit use initial-exec; a unit that did not see it uses another model, so the
      mixed use is exactly the defect. NOT COVERED (stated gap, owner L3a/L2): the attribute is not recorded in the ELF or in DWARF, so a symbol
      declared initial-exec that EVERY unit references with local-exec is not seen; closing it needs the declaration as a fact (a plugin note).
  (5) `unclassified`: a TLS relocation of a name outside the families above.
Items: every object, every linked ELF, every TLS relocation (object, symbol, relocation name, section, offset). Certified = the items with no
finding. Counts per relocation family over all roots are in `detail`. No guest root: universe 0, green."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_build  # noqa: E402
import gate_linkstrict  # noqa: E402
import recipe  # noqa: E402

READELF = "aarch64-linux-gnu-readelf"
ROW = re.compile(r"^\s*([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+(R_\w+)\s*(.*)$")
DYNAMIC, IE, LE, OTHER = "dynamic", "ie", "le", "unclassified"


def family(rtype):
    """The TLS family of a relocation type name, or None when it is not a TLS relocation."""
    n = rtype[len("R_AARCH64_"):] if rtype.startswith("R_AARCH64_") else rtype
    n = n[4:] if n.startswith("P32_") else n
    if n.startswith(("TLSGD", "TLSLD", "TLSDESC", "TLS_DTPMOD", "TLS_DTPREL", "TLS_TPREL")):
        return DYNAMIC
    if n.startswith("TLSIE"):
        return IE
    if n.startswith("TLSLE"):
        return LE
    if n.startswith("TLS") or "TPREL" in n or "DTPREL" in n:
        return OTHER
    return None


def relocs(path):
    """[(section, offset, relocation type name, symbol)] of every relocation of an ELF file (readelf -rW). Symbol is "" when readelf names none,
    and a name starting with "." for a section symbol."""
    return parse_readelf(subprocess.run([READELF, "-rW", path], capture_output=True, text=True, check=True).stdout)


def parse_readelf(text):
    """relocs() of the text `readelf -rW` printed."""
    res, sec = [], ""
    for line in text.splitlines():
        if line.startswith("Relocation section"):
            sec = line.split("'")[1]
            continue
        m = ROW.match(line)
        if m:
            tail = re.split(r"\s[+-]\s", m.group(4).strip())[0].strip()
            toks = tail.split(None, 1)    # Sym. Value, Symbol's Name
            sym = toks[1] if len(toks) == 2 and re.fullmatch(r"[0-9a-fA-F]+", toks[0]) else (toks[0] if toks and not re.fullmatch(r"[0-9a-fA-F]+", toks[0]) else "")
            res.append((sec, int(m.group(1), 16), m.group(3), sym))
    return res


def judge(groups, linked):
    """GROUPS {group id: {object id: ({defined names}, [(section, offset, rtype, symbol)])}}; LINKED {elf id: [(section, offset, rtype, symbol)]}
    -> (items [(id, name)], findings [(id, name, KIND, detail)] sorted, counts {family: n}). Pure function of its arguments."""
    items, out, counts = [], set(), {}
    for gid, objs in sorted(groups.items()):
        defined = set().union(*[d for d, _ in objs.values()]) if objs else set()
        models = {}   # symbol -> {family: [(object, reloc text)]}
        for oid, (_, rl) in sorted(objs.items()):
            items.append((oid, "<object>"))
            for sec, off, rt, sym in rl:
                fam = family(rt)
                if fam is None:
                    continue
                counts[fam] = counts.get(fam, 0) + 1
                name = "%s %s %s+%#x" % (sym or "-", rt, sec, off)
                items.append((oid, name))
                where = "%s %s in %s+%#x" % (rt, sym or "(section)", sec, off)
                local = sym.startswith(".") or sym == ""
                if fam == DYNAMIC:
                    out.add((oid, name, "dynamic-tls", where + ": the loader has no dynamic TLS"))
                elif fam == OTHER:
                    out.add((oid, name, "unclassified", where))
                elif fam == LE and not local and sym not in defined:
                    out.add((oid, name, "le-outside", where + ": the symbol is not defined by the linked output (host TLS needs initial-exec)"))
                if fam in (IE, LE) and not local:
                    models.setdefault(sym, {}).setdefault(fam, []).append((oid, name))
        for sym, m in sorted(models.items()):
            if len(m) > 1:
                for fam, refs in m.items():
                    for oid, name in refs:
                        out.add((oid, name, "mixed-model", "%s is referenced with initial-exec and local-exec in %s" % (sym, gid)))
    for eid, rl in sorted(linked.items()):
        items.append((eid, "<elf>"))
        for sec, off, rt, sym in rl:
            fam = family(rt)
            if fam is None:
                continue
            counts[fam] = counts.get(fam, 0) + 1
            name = "%s %s %s+%#x" % (sym or "-", rt, sec, off)
            items.append((eid, name))
            out.add((eid, name, "dynamic-tls", "%s %s in %s+%#x: a TLS relocation left in the linked output (dynamic TLS)" % (rt, sym or "(section)", sec, off)))
    return sorted(set(items)), sorted(out), counts


def main():
    tmp = None if os.environ.get("GATE_CACHE") else tempfile.mkdtemp(prefix="gate-tlsmodel-")
    cache, repo = os.environ.get("GATE_CACHE") or tmp, "."
    admit, files = recipe.load_admit(repo), recipe.tracked(repo)
    groups, linked, why, built = {}, {}, "", {}
    try:
        def objects(root):
            if root not in built:
                res, _ = gate_build.ensure(root, cache, repo)
                if res["status"] == "red":
                    raise ValueError("%s: build red at stage %s" % (root, res["failure"]["stage"]))
                built[root] = res
            return {root + "/" + o["unit"]: (set(n for n, v, s, t, b, shn in gate_linkstrict.elf_symbols(o["object"]) or [] if shn != 0), relocs(o["object"]))
                    for o in built[root]["objects"]}, built[root]
        for root in gate_build.buildable_roots(repo):
            if admit[root].get("target") != "guest":
                continue
            objs, res = objects(root)
            rec = recipe.load_recipe(repo, admit, root)
            for r in (rec.get("link") or {}).get("roots", []):
                objs.update(objects(r)[0])
            groups[root] = objs
            if res["elf"]:
                linked[root + "/" + os.path.basename(res["elf"]["path"])] = relocs(res["elf"]["path"])
    except recipe.RecipeError as e:
        why = "invalid recipe: %s" % "; ".join(e.errors)[:300]
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as e:
        why = "input error: %s" % (str(e)[:300] or type(e).__name__)
    finally:
        if tmp and not os.environ.get("GATE_KEEP"):
            shutil.rmtree(tmp, ignore_errors=True)
    items, finds, counts = judge(groups, linked)
    bad = {(o, n) for o, n, _, _ in finds}
    cert = len([i for i in items if i not in bad])
    kinds = {}
    for _, _, k, _ in finds:
        kinds[k] = kinds.get(k, 0) + 1
    cnt = ", ".join("%s %d" % kv for kv in sorted(counts.items())) or "no TLS relocation"
    if why:
        status, detail = "red", why
    elif finds:
        status, detail = "red", "%d findings: %s; relocations: %s; first: %s" % (len(finds), ", ".join("%s %d" % kv for kv in sorted(kinds.items())), cnt, finds[0][3][:200])
    else:
        status, detail = "green", "%d items of %d guest roots; relocations by family: %s" % (len(items), len(groups), cnt)
    res = {"format": "fnvnx-gate/1", "name": "tlsmodel", "status": status, "universe": len(items), "certified": cert,
           "uncertified": sorted("%s:%s" % x for x in bad)[:50], "detail": detail}
    with open(os.path.join(os.environ["GATE_OUT"], "tlsmodel.json"), "w") as fh:
        json.dump(res, fh, sort_keys=True)
    print("tlsmodel: %s %d/%d %s" % (status, cert, len(items), detail[:300]))
    return 0 if status == "green" else 2


if __name__ == "__main__":
    sys.exit(main())
