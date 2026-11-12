#!/usr/bin/env python3 -I
"""Static census of the font requests of a PE module set (L5 declared machine; the declared font set is the faces the exe and its modules can request).

Rule (mechanical, from the images only; the engine is pemanifest.Mod and regsites.Analyzer, the same as filesites.py):
  sites    every code reference to an imported font API (FONT_API: CreateFont*, AddFontResource*, AddFontMemResource, EnumFont*, RemoveFontResource*)
           and to GetStockObject, in every module of the closure (pemanifest: static imports; `dynamic` modules by argument).
           GetStockObject: the constant argument (a stock FONT is id 10..17; any other id is a brush, pen or palette, not a request) or `dynamic`.
  reach    a font-API site is a request only if its function can be entered. The backward closure over direct `call rel32` callers is taken from
           the function that holds the site; every entry that is not a direct call (jmp, address word in code, vtable slot, data word) is
           reported. The closure ends at the exports of the module: a request is possible only if the exe imports one of those exports
           (static import) or a GetProcAddress site names it (dynsites). Any other end (no caller and no export, or a non-call entry) is
           `unresolved`, never guessed.
Output rows: module, site, api, function, kind, detail. `kind` = stock-font | stock-other | api-site; `detail` = the stock id, or the exports that
the backward closure ends at (`unresolved: ...` when it does not end at exports only), and `imported` when the exe imports one.
Assumptions: those of regsites.py (ASM-001, ASM-003, ASM-006, ASM-012, ASM-013; registry verify/ASSUMPTIONS.json); Win32 as documented (ASM-003), the stock object ids of wingdi.h.
Usage: fontfaces.py <dir-with-modules> <root.exe> <out.tsv> --bridge bridge.tsv --dllkind dllkind.py --dllkind-sha SHA256 [--facts DIR]
Image set: the root and the dlls of dllkind role `image` (pemanifest.closure); no name list.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import funcfacts, pemanifest, regsites, pefile, capstone

FONT_API = {"CreateFontA", "CreateFontW", "CreateFontIndirectA", "CreateFontIndirectW", "CreateFontIndirectExA", "CreateFontIndirectExW",
            "AddFontResourceA", "AddFontResourceW", "AddFontResourceExA", "AddFontResourceExW", "AddFontMemResourceEx",
            "EnumFontsA", "EnumFontsW", "EnumFontFamiliesA", "EnumFontFamiliesW", "EnumFontFamiliesExA", "EnumFontFamiliesExW",
            "RemoveFontResourceA", "RemoveFontResourceW", "RemoveFontResourceExA", "RemoveFontResourceExW"}
STOCK_FONT = {10: "OEM_FIXED_FONT", 11: "ANSI_FIXED_FONT", 12: "ANSI_VAR_FONT", 13: "SYSTEM_FONT", 14: "DEVICE_DEFAULT_FONT", 16: "SYSTEM_FIXED_FONT", 17: "DEFAULT_GUI_FONT"}


def stock_arg(m, site, entry):
    """The constant pushed for the last `push imm` before `call [GetStockObject]` at `site` (the only argument), read in the decoded runs
    of `entry`; the push must be the instruction just before the call in the same run. Else None."""
    ins = m.ff.ins(entry)
    k = next((j for j, i in enumerate(ins) if i.address <= site < i.address + i.size), None)   # the call instruction holding the slot reference
    if k is None or k == 0 or ins[k].mnemonic != "call":
        return None
    p = ins[k - 1]
    if p.address + p.size == ins[k].address and p.mnemonic == "push" and p.operands[0].type == capstone.x86.X86_OP_IMM:
        return p.operands[0].imm & 0xffffffff
    return None


def exports_of(path, base):
    pe = pefile.PE(path)
    return {base + s.address: (s.name or b"#%d" % s.ordinal).decode() for s in pe.DIRECTORY_ENTRY_EXPORT.symbols} if hasattr(pe, "DIRECTORY_ENTRY_EXPORT") else {}


def main(d, root, out, argv=()):
    mods = pemanifest.closure_from_argv(list(argv), d, root)[0]
    files = {f.lower(): os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith((".exe", ".dll"))}
    rootmod = mods[root.lower()]
    imported = {}   # module -> {export names the closure imports from it}
    for m in mods.values():
        for dll, nm, _ in m.imports:
            imported.setdefault(dll, set()).add(nm)
    rows, counts = [], {"stock-font": 0, "stock-other": 0, "api-site": 0, "reachable": 0, "unresolved": 0, "sites-without-function": 0, "sites-in-several-entries": 0}
    for name in sorted(mods):
        m = mods[name]
        slots = {s: n for _, n, s in m.imports if n in FONT_API or n == "GetStockObject"}
        if not slots:
            continue
        code, _ = m.refs()
        an = regsites.Analyzer(m, {})
        ex = exports_of(files[name], m.base)
        for slot, api in sorted(slots.items()):
            for site in sorted(code.get(slot, [])):
                fs = m.entries_of(site)   # the site is analysed in every entry that contains it: one row per entry
                if not fs:
                    rows.append((name, site, api, None, "api-site" if api != "GetStockObject" else "stock-dynamic",
                                 "unresolved: " + m.unresolved_why(site))); counts["unresolved"] += 1
                    counts["sites-without-function"] += 1
                    continue
                counts["sites-in-several-entries"] += len(fs) > 1
                for f in fs:
                    if api == "GetStockObject":
                        a = stock_arg(m, site, f)
                        if a is None:
                            rows.append((name, site, api, f, "stock-dynamic", "argument not constant")); counts["unresolved"] += 1
                        elif a in STOCK_FONT:
                            rows.append((name, site, api, f, "stock-font", "%d %s" % (a, STOCK_FONT[a]))); counts["stock-font"] += 1
                        else:
                            rows.append((name, site, api, f, "stock-other", "%d (not a font)" % a)); counts["stock-other"] += 1
                        continue
                    counts["api-site"] += 1
                    seen, todo2, ends, bad = {f}, [f], set(), []
                    while todo2:
                        x = todo2.pop()
                        if x in ex:
                            ends.add(ex[x])
                        cs, en = an.callers(x), an.entries_x(x)
                        bad += ["%s" % e["text"] for e in en if x not in ex or e["kind"] != "jmp"] if en else []
                        if not cs and x not in ex and not en:
                            bad.append("no caller and no export 0x%08x" % x)
                        for _, st in cs:
                            if st not in seen:
                                seen.add(st); todo2.append(st)
                    if bad:
                        rows.append((name, site, api, f, "api-site", "unresolved: " + "; ".join(sorted(set(bad))[:4]))); counts["unresolved"] += 1
                    else:
                        imp = sorted(e for e in ends if e in imported.get(name, ()))
                        rows.append((name, site, api, f, "api-site", "ends at exports %s%s" % (",".join(sorted(ends)), "; imported: " + ",".join(imp) if imp else "; none imported"))); counts["reachable"] += 1 if imp else 0
    with open(out, "w") as fo:
        fo.write("module\tsite\tapi\tfunction\tkind\tdetail\n")
        for r in rows:
            fo.write("%s\t0x%08x\t%s\t%s\t%s\t%s\n" % (r[0], r[1], r[2], "0x%08x" % r[3] if r[3] else "-", r[4], r[5]))
    sys.stderr.write("modules %d (%s); font-API sites %d, reachable through an imported export %d; GetStockObject: font %d, other %d; unresolved %d; sites in no entry %d, in several entries %d\n"
                     % (len(mods), ",".join(sorted(mods)), counts["api-site"], counts["reachable"], counts["stock-font"], counts["stock-other"], counts["unresolved"], counts["sites-without-function"], counts["sites-in-several-entries"]))


if __name__ == "__main__":
    funcfacts.facts_dir_from_argv(sys.argv)
    if len(sys.argv) < 4:
        sys.stderr.write(__doc__); sys.exit(2)
    main(*sys.argv[1:4], argv=sys.argv[4:])
