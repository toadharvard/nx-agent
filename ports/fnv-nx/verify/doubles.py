#!/usr/bin/env python3
"""doubles: the rule "no doubles in tests" (CLAUDE.md, owner order of 2026-11-11 night) as a pure function over the results of the
product roots' builds (objects.json of recipe.py, RECIPE.md "tests"). Called by gate_objstub.py: the check is part of the stub gate.
Rule, per test binary T of a root R: (symbols defined by T's own objects) INTERSECT (symbols defined by the objects of ALL admitted
product roots) = EMPTY. A symbol is any global, weak or unique definition of the ELF .symtab of an object (recipe.symbols). No allow-list,
no exclusion by name. Also red:
  - a test link that omits a product unit of its root (`linked` must contain every unit of the root);
  - a declared test without a linked ELF in the result;
  - two product objects of one root that both define the same GLOBAL symbol: this is how a test source that was not declared (and so is a
    product unit, linked into the product) is caught when it replaces a product body; a link of the root would fail the same way.
COMDAT (vague linkage, Itanium C++ ABI: one definition that several units may emit): a symbol defined in a member section of a COMDAT group by the
test and by the product is NOT a double iff every definition (the test's and all the product's) is in a COMDAT group and the group digests are
equal (recipe._comdat_digests: same signature, byte-equal member sections, equal relocations by symbol name, type and addend). Any difference, or
a weak/global definition outside a COMDAT group on either side, is a test-double. Such shared symbols are counted apart, `comdat_shared`.
Argument: the recipe makes every source of a root either a product unit or a unit of exactly one declared test (recipe.check), and a test
is linked by the recipe from the root's whole product unit set. A test therefore contains the real product objects; a double is a second
definition of a name, which is exactly the set intersection above. A source that is not declared as a test is product code and meets the
product-duplicate rule. Weak and unique symbols count like global ones unless the COMDAT rule above proves the two definitions the same code.
Depends only on the accepted OWNER item 1 (the compiler is correct): the symbol table of an object is the set of names it defines."""
import os


def judge(results):
    """RESULTS {root: objects dict of a green build} -> (items [(id, "test")], findings [(id, "test", kind, detail)], stats dict).
    One item per declared test and one `<root>:product-units` item per root; stats: tests_total, tests_with_doubles, double_symbols, comdat_shared."""
    product = {}   # symbol -> sorted product objects (repo-relative) defining it, over all roots
    pdig = {}      # symbol -> set of COMDAT digests of its product definitions (None: a definition outside a COMDAT group)
    items, finds, doubles, with_doubles, total, shared = [], [], 0, 0, 0, set()
    for root in sorted(results):
        res, strong = results[root], {}
        pid = root + ":product-units"
        items.append((pid, "test"))
        for d in res["defined"]:
            obj = "%s/%s" % (root, d["object"])
            product.setdefault(d["symbol"], set()).add(obj)
            pdig.setdefault(d["symbol"], set()).add(d.get("comdat"))
            if d["bind"] == "GLOBAL":
                strong.setdefault(d["symbol"], {})[obj] = d.get("comdat")
        for sym in sorted(s for s, o in strong.items() if len(o) > 1 and (None in o.values() or len(set(o.values())) > 1)):   # equal COMDAT groups link as one
            finds.append((pid, "test", "product-duplicate", "%s is defined by %s" % (sym, ", ".join(sorted(strong[sym])))))
    for root in sorted(results):
        res = results[root]
        units = set(res["units"])
        for t in res.get("tests", []):
            tid, total = "%s:test:%s" % (root, t["name"]), total + 1
            items.append((tid, "test"))
            tdig = {}
            for d in t["defined"]:
                tdig.setdefault(d["symbol"], set()).add(d.get("comdat"))
            bad = []
            for s in sorted(tdig):
                if s not in product:
                    continue
                alld = tdig[s] | pdig[s]
                if len(alld) == 1 and None not in alld:
                    shared.add(s)   # one COMDAT group digest on both sides: the same vague-linkage definition
                else:
                    bad.append((s, sorted(product[s])))
            doubles += len(bad)
            with_doubles += bool(bad)
            for s, objs in bad:
                finds.append((tid, "test", "test-double", "%s defined by the test is a symbol of product object %s" % (s, ", ".join(objs))))
            for u in sorted(units - set(t["linked"])):
                finds.append((tid, "test", "test-omits-unit", "the link of the test leaves out product unit %s/%s" % (root, u)))
            if not (t.get("elf") and t["elf"].get("sha256")):
                finds.append((tid, "test", "test-not-linked", "no linked ELF in the build result"))
    return items, finds, {"tests_total": total, "tests_with_doubles": with_doubles, "double_symbols": doubles,
                                  "comdat_shared": len(shared)}
