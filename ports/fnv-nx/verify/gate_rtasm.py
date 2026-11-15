#!/usr/bin/env python3
"""Gate "rtasm" (fnvnx-gate/1, class A): the guest runtime's assembly objects order their memory accesses (CLAUDE.md, "memory ordering of runtime calls").
    python3 -I gate_rtasm.py        cwd = checkout root, env GATE_OUT, optional GATE_CACHE; writes GATE_OUT/rtasm.json
RULE.  The guest runtime (newlib, libstdc++, libgcc, libnx) is built by msvcmodel with every plugin; its C and C++ bodies order their own accesses (gate order).
The remaining assembly is closed here: every memory access of an assembled object is certified by one stated class, every other access is red, with no allow-list.
  (a) frame: the access is in the routine's own frame and no escape of a frame address reaches it on any path from the entry (rule D1 of link/a64order.py,
      with the whole frame as one escaping range, ASM-006 for stale pointers); a call or a store of a frame address ends the class for what follows.
  (d) atomic: an ordered form (ldar, stlr, ldaxr, stlxr, LSE acquire-release forms: rule O), whose ordering is the documented one of the instruction (Arm ARM).
  (c) kernel call: `svc` and its argument moves are no memory accesses; they are no item, and a routine made of them only has none.
  Not yet closed (reported red, the input is not in the record): (b) runs before threads (the link record's call graph), and a jmp_buf parameter register (the declared prototype).
UNIVERSE.  (1) The product's own assembly units: the objects of every admitted product root with "target": "guest" whose recipe unit has an assembly suffix
(recipe.ASM_EXT: .s, .S).  (2) The objects of the runtime external (newlib, libstdc++, libgcc, libnx: archive members from tools/fnvnx-a64-msvcmodel13, which are no recipe
units) that were assembled, read from the object itself: the DWARF compile unit's DW_AT_language is DW_LANG_Mips_Assembler (GNU as sets it with its "GNU AS" producer); an
object with no DWARF compile unit is red `<object>:origin [origin unknown]` (the runtime build keeps -g).  The member set is the closure of the guest link (the
archive members the link pulls in, from the link record), not the whole archive.  Items: one per memory-access instruction (`<object>:<symbol>+0xOFF`), one per object
that cannot be parsed or whose origin is unknown.  Red iff an item is uncertified or a root's build is not green.  No assembly object: universe 0, green.
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "link"))
sys.path.insert(0, os.path.join(HERE, "..", "contract"))
import a64order  # noqa: E402
import gate_build  # noqa: E402
import recipe  # noqa: E402


DW_LANG_MIPS_ASSEMBLER = 0x8001


def origin(path):
    """'asm' | 'other' | 'unknown' of the object, from its first DWARF compile unit: DW_AT_language == DW_LANG_Mips_Assembler is assembly; any other compile unit is 'other'; no compile unit (no -g) is 'unknown'"""
    from elftools.elf.elffile import ELFFile
    with open(path, "rb") as f:
        elf = ELFFile(f)
        if not elf.has_dwarf_info():
            return "unknown"
        for cu in elf.get_dwarf_info(relocate_dwarf_sections=False).iter_CUs():
            top = cu.get_top_DIE()
            lang = top.attributes.get("DW_AT_language")
            # the relocatable's DW_AT_producer is a .debug_str offset resolved by a relocation (ILP32 types pyelftools does not apply), so the language,
            # a constant form, decides; GNU as writes DW_LANG_Mips_Assembler together with its `GNU AS` producer into every compile unit it makes
            return "asm" if lang is not None and lang.value == DW_LANG_MIPS_ASSEMBLER else "other"
    return "unknown"


def judge_objects(objs, suffix_asm=None):
    """OBJS [(id, path)] -> (universe, certified, uncertified item ids, detail).  SUFFIX_ASM {id: bool}: the objects that are recipe units, with the recipe's suffix fact
    (assembly or not); every other object is a member of the runtime external, whose origin is read from its DWARF."""
    suffix_asm = suffix_asm or {}
    items, bad, n_asm = 0, [], 0
    for oid, path in objs:
        if oid in suffix_asm:
            asm = suffix_asm[oid]
        else:
            try:
                o = origin(path)
            except Exception as e:      # a file that is no ELF object or whose DWARF is malformed is red, never passed
                items += 1
                bad.append("%s:origin [unreadable: %s]" % (oid, str(e)[:100]))
                continue
            if o == "unknown":
                items += 1
                bad.append("%s:origin [origin unknown: no DWARF compile unit]" % oid)
                continue
            asm = o == "asm"
        if not asm:
            continue
        n_asm += 1
        try:
            rows = a64order.scan_asm(path)
        except (ValueError, OSError, KeyError, IndexError, TypeError) as e:
            items += 1
            bad.append("%s [scan failed: %s]" % (oid, str(e)[:120]))
            continue
        for item, kind in rows:
            items += 1
            if kind:
                bad.append("%s:%s [%s]" % (oid, item, kind))
    return items, items - len(bad), bad, "%d assembly objects of %d" % (n_asm, len(objs))


def judge_root(repo, cache, root):
    roots = recipe.load_admit(repo)
    try:
        res, _ = gate_build.ensure(root, cache, repo)
    except recipe.RecipeError as e:
        return 1, 0, [root + ":recipe"], "invalid recipe: %s" % "; ".join(e.errors)[:300]
    if res["status"] != "green":
        return 1, 0, [root + ":build"], "build %s" % res["status"]
    objs = [(root + "/" + o["unit"], o["object"]) for o in res["objects"]]
    return judge_objects(objs, {root + "/" + o["unit"]: o["unit"].endswith(recipe.ASM_EXT) for o in res["objects"]})


def main():
    repo, tmp = ".", None
    cache = os.environ.get("GATE_CACHE")
    if not cache:
        cache = tmp = tempfile.mkdtemp(prefix="gate-rtasm-")
    try:
        u = c = 0
        ids, why = [], []
        adm = recipe.load_admit(repo)
        roots = [r for r in gate_build.product_roots(repo) if recipe.entry_of(adm, r).get("target") == "guest"]
        for r in roots:
            ru, rc, rids, rd = judge_root(repo, cache, r)
            u, c, ids = u + ru, c + rc, ids + rids
            if rc != ru:
                why.append("%s: %s" % (r, rd))
    finally:
        if tmp and not os.environ.get("GATE_KEEP"):
            shutil.rmtree(tmp, ignore_errors=True)
    detail = "; ".join(why) or ("%d items of %d guest roots" % (u, len(roots)) if roots else "no guest root (universe 0)")
    res = {"format": "fnvnx-gate/1", "name": "rtasm", "status": "red" if why else "green", "universe": u, "certified": c,
           "uncertified": sorted(ids)[:50], "detail": detail[:900]}
    with open(os.path.join(os.environ["GATE_OUT"], "rtasm.json"), "w") as f:
        json.dump(res, f, sort_keys=True)
    print("rtasm: %s %d/%d %s" % (res["status"], c, u, detail[:200]))
    return 2 if why else 0


if __name__ == "__main__":
    sys.exit(main())
