#!/usr/bin/env python3
"""Bridge table for the import guest entries (contract with the lifter runtime).

Input: sys_names.tsv (dll, name) from pemanifest.py and a directory of export
specification files of the reference environment (Wine 9.0 dlls/<dll>/<dll>.spec),
which give every export's calling convention and argument slots.
Output rows: dll, name, ordinal, conv, ret, args, native_symbol, bind
  bind  sym    native_symbol is a defined external symbol of the platform objects (checked against the
               symbol list given as the 4th argument: `nm --defined-only -g` names, one per line)
        lookup the implementation has internal linkage or is the C library's: its address is
               fnvnx_win32_lookup_export(dll, name) at load time (native_symbol is then `-`). Decided by a FACT of
               the name table platform/crt/msvcrt_names.tsv, from which crt/msvcrt_table.gen.cpp is generated; msvcrt
               rows are `lookup` iff the table carries the name
        l3     provided by the lifter runtime under native_symbol
        data   the slot holds the address of the object native_symbol (a defined external symbol, checked)
        none   no implementation yet: a build that needs this row must fail
  conv  stdcall | cdecl | vararg | thiscall (this in ecx) | data (the slot is the address of an object)
  ret   v | i | q | f | d      (a spec file does not state the return type: `i` is written
                                unless RET below names the function; the list is closed by
                                the check at the end: a float-returning name not in RET with
                                a double argument is reported)
  args  x86 stack slots: i (32 bit, pointers too), q (64 bit), f, d
A name missing from its spec file, a forward to another DLL that cannot be followed,
or an unknown argument type is an error: the table is total or the script fails.

Usage: bridgetable.py sys_names.tsv <spec-dir> out.tsv <defined-symbols.txt> <msvcrt_names.tsv> [extra-rows.tsv ...]
(a row of msvcrt.dll is `lookup` iff msvcrt_names.tsv carries its name; the gate `imports` (verify/gate_imports.py) proves the symbol)
"""
import os, re, sys

ARG = {"long": "i", "ptr": "i", "str": "i", "wstr": "i", "word": "i", "segptr": "i", "segstr": "i",
       "int64": "q", "double": "d", "float": "f", "": ""}
# return types that are not a 32-bit integer in eax (from the C prototypes of these exports)
RET = {("msvcrt.dll", n): "d" for n in
       "atof ceil floor frexp ldexp modf strtod".split()}
RET.update({("msvcrt.dll", n): "q" for n in ["_lseeki64"]})
RET.update({(d, n): "v" for d, n in [
    ("kernel32.dll", n) for n in "DebugBreak DeleteCriticalSection EnterCriticalSection ExitProcess ExitThread "
    "GetLocalTime GetStartupInfoA GetSystemInfo GetSystemTime GetSystemTimeAsFileTime GlobalMemoryStatus "
    "InitializeCriticalSection LeaveCriticalSection OutputDebugStringA RaiseException RtlUnwind SetLastError "
    "Sleep".split()] + [("ole32.dll", n) for n in "CoFreeUnusedLibraries CoTaskMemFree CoUninitialize".split()] +
    [("d3d9.dll", "D3DPERF_SetOptions")] +
    [("msvcrt.dll", n) for n in "_amsg_exit _initterm _lock _purecall _unlock abort exit free qsort srand "
     "??3@YAXPAX@Z".split()]})
SPEC_OF = {"xinput1_3.dll": "xinput1_3", "wsock32.dll": "wsock32"}


def load(spec_dir, dll, cache={}):
    if dll in cache:
        return cache[dll]
    by_name, by_ord = {}, {}
    path = os.path.join(spec_dir, SPEC_OF.get(dll, dll[:-4]) + ".spec")
    for line in open(path, encoding="latin1"):
        line = line.split("#")[0].strip()
        m = re.match(r"(@|\d+)\s+(stdcall|cdecl|varargs|thiscall|extern|stub|fastcall)\s+((?:-\w+(?:=\S+)?\s+)*)"
                     r"(\S+?)\s*(?:\(([^)]*)\))?(?:\s+(\S+))?$", line)
        if not m:
            continue
        ordn, kind, flags, name, args, target = m.groups()
        ent = (kind, (args or "").split(), target, ordn)
        by_name[name] = ent
        if ordn != "@":
            by_ord[int(ordn)] = (name, ent)
    cache[dll] = (by_name, by_ord)
    return cache[dll]


def resolve(spec_dir, dll, name, depth=0):
    by_name, by_ord = load(spec_dir, dll)
    ordn = ""
    if name.startswith("#"):
        ordn = name[1:]
        name, ent = by_ord[int(ordn)]
    else:
        ent = by_name[name]
        ordn = "" if ent[3] == "@" else ent[3]
    kind, args, target, _ = ent
    if target and "." in target and depth < 4:      # forward: other.dll export carries the signature
        tdll, tname = target.split(".", 1)
        r = resolve(spec_dir, tdll.lower() + ".dll", tname, depth + 1)
        return name, ordn, r[2], r[3]
    if kind == "extern":                            # a data export: the slot holds the address of the object
        return name, ordn, "data", ""
    if kind == "stub":
        raise KeyError("%s!%s is a stub in the reference spec" % (dll, name))
    conv = {"stdcall": "stdcall", "cdecl": "cdecl", "varargs": "vararg", "thiscall": "thiscall",
            "fastcall": "fastcall"}[kind]
    return name, ordn, conv, "".join(ARG[a] for a in args)


# msvcrt names provided by the lifter runtime (exception handling and x87 helpers work on the guest
# state directly; agreed with L3): symbol fnvl_msvcrt_<name without leading underscores and decoration>
L3_MSVCRT = set("__CxxFrameHandler _CxxThrowException _except_handler3 _setjmp3 longjmp _XcptFilter "
                "?terminate@@YAXXZ ??1type_info@@UAE@XZ _adjust_fdiv _clearfp _controlfp _fpclass _finite "
                "_isnan".split())
L3_NAMES = {"?terminate@@YAXXZ": "terminate", "??1type_info@@UAE@XZ": "type_info_dtor"}
# kernel32 services that act on the guest state (R4: the SEH dispatch walks the guest fs:[0] chain and calls lifted
# handlers; DebugBreak is the breakpoint exception at the caller): provided by the lifter runtime as fnvl_os_<name>.
# The platform raises its own exceptions through w32_raise (platform/win32/thread.cpp), whose backend that runtime installs.
L3_KERNEL32 = ("RaiseException", "RtlUnwind", "DebugBreak")


# msvcrt data exports whose object has another symbol name in platform/crt
DATA_SYMS = {"_iob": "fnvnx_iob"}


# msvcrt printf/scanf imports whose native body reads x86 stack words (platform/crt/msvcrt_format.cpp)
VA_MSVCRT = {"sprintf": "fnvnx_msvcrt_va_sprintf", "_snprintf": "fnvnx_msvcrt_va_snprintf",
             "sscanf": "fnvnx_msvcrt_va_sscanf", "_vsnprintf": "fnvnx_msvcrt_va_vsnprintf"}


def native(dll, name):
    if dll == "msvcrt.dll" and (name in L3_MSVCRT or name.startswith("_CI")):
        return "fnvl_msvcrt_" + L3_NAMES.get(name, name.lstrip("_"))
    if dll == "kernel32.dll" and name in L3_KERNEL32:
        return "fnvl_os_" + name
    if dll == "wsock32.dll":
        return "fnvnx_ws2_" + {"__WSAFDIsSet": "fdisset"}.get(name, name)
    if dll == "msvcrt.dll" and name.startswith("?"):
        return {"??2@YAPAXI@Z": "fnvnx_msvcrt_operator_new", "??3@YAXPAX@Z": "fnvnx_msvcrt_operator_delete"}[name]
    return name


def read_name_table(path):
    """{msvcrt name: (symbol, kind)} from platform/crt/msvcrt_names.tsv (name TAB symbol TAB func|data), the table the msvcrt lookup is generated from."""
    out = {}
    lines = open(path, encoding="latin1").read().split("\n")
    if lines[0] != "name\tsymbol\tkind":
        raise ValueError("%s: bad header" % path)
    for line in lines[1:]:
        if not line:
            continue
        f = line.split("\t")
        if len(f) != 3 or f[2] not in ("func", "data") or f[0] in out:
            raise ValueError("%s: bad line %r" % (path, line))
        out[f[0]] = (f[1], f[2])
    return out


def derive(names, spec_dir, syms, name_table, extra):
    """-> (rows, errors, extra_of): rows (dll, name, ordinal, conv, ret, args, native_symbol, bind) sorted; extra_of maps
    (dll, name) of a row taken from an extra-rows file to that file's path. `syms` = set of symbol names, `name_table` = read_name_table()."""
    rows, errors, extra_of = [], [], {}
    for line in open(names):
        dll, name = line.rstrip("\n").split("\t")[:2]
        if dll in ("galaxywrp.dll",):               # contract-only module: Steamworks prototypes, cdecl
            sig = {"SteamAPI_RegisterCallback": "ii", "SteamAPI_UnregisterCallback": "i"}.get(name, "")
            ret = "v" if name in ("SteamAPI_Shutdown", "SteamAPI_RunCallbacks", "SteamAPI_RegisterCallback",
                                  "SteamAPI_UnregisterCallback") else "i"
            rows.append((dll, name, "", "cdecl", ret, sig, name, "sym" if name in syms else "none"))
            continue
        try:
            real, ordn, conv, args = resolve(spec_dir, dll, name)
        except (KeyError, FileNotFoundError) as e:
            errors.append("%s!%s: %s" % (dll, name, e))
            continue
        ret = RET.get((dll, real), "i")
        sym = native(dll, real)
        if sym.startswith("fnvl_"):
            bind = "l3"
        elif conv == "data":
            # a data slot holds the address of the ELF symbol named here (loader rule, L1): the object's own symbol
            dsym = DATA_SYMS.get(real, real)
            sym, bind = (dsym, "data") if dsym in syms else ("-", "none")
        elif dll == "msvcrt.dll" and real in VA_MSVCRT:
            # the caller is translated x86 code: the body takes the fixed slots and a pointer to the remaining
            # stack words (sprintf, _snprintf, sscanf), or the game's va_list, which is such a pointer (_vsnprintf)
            sym = VA_MSVCRT[real]
            bind = "sym" if sym in syms else "none"
        elif dll == "msvcrt.dll":
            sym, bind = "-", ("lookup" if name in name_table else "none")
        else:
            bind = "sym" if sym in syms else "none"
        rows.append((dll, real, ordn, conv, ret, args, sym, bind))
    # rows of other owners in this format, without a header (L6: render/d3d9/gen/gen_bridge.py - the d3d9.dll exports and
    # the methods of the COM interfaces as dll `!<Interface>`, ordinal = vtable slot, first slot = this). A row of
    # the same (dll, name) replaces the one derived above; a duplicate inside the extra files is an error.
    seen = {}
    for path in extra:
        for line in open(path):
            r = tuple(line.rstrip("\n").split("\t"))
            if len(r) != 8:
                errors.append("%s: not 8 columns: %r" % (path, line))
            elif (r[0], r[1]) in seen:
                errors.append("%s!%s: given twice (%s, %s)" % (r[0], r[1], seen[(r[0], r[1])], path))
            else:
                seen[(r[0], r[1])] = path
                rows = [x for x in rows if (x[0], x[1]) != (r[0], r[1])] + [r]
    rows.sort(key=lambda r: (r[0].lower(), r[1]))
    return rows, errors, seen


def main(names, spec_dir, out, symfile, name_table_file, *extra):
    rows, errors, _ = derive(names, spec_dir, set(open(symfile).read().split()), read_name_table(name_table_file), extra)
    with open(out, "w") as f:
        f.write("dll\tname\tordinal\tconv\tret\targs\tnative_symbol\tbind\n")
        for r in rows:
            f.write("\t".join(r) + "\n")
    for e in errors:
        print("ERROR", e)
    print(len(rows), "rows,", len(errors), "errors")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main(*sys.argv[1:])
