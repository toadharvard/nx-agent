"""Bridge table of the import guest entries, pure derivation (rows: dll, name, ordinal, conv, ret, args, native_symbol, bind).

A pure function of its arguments (no file, no process): the driver verify/gate_imports.py reads the declared inputs and calls derive().
  names       text of sys_names.tsv (dll TAB name per line) from the private manifest
  spec_text   function dll -> text of the export specification of the reference environment (Wine 9.0 dlls/<dll>/<dll>.spec)
  syms        the set of symbol names that the product objects define or reference (objects.json defined + undefined)
  name_table  {msvcrt name: (symbol, kind)} of the name table the product serves msvcrt names from (read_name_table)
  extra       texts of rows in this 8-column format (other owners' rows: COM interfaces); a row of the same (dll, name) replaces the derived one
bind  sym    native_symbol is a symbol of the product objects     lookup  msvcrt name served by the name table (native_symbol `-`)
      l3     provided by the lifter runtime under native_symbol   data    the slot holds the address of the object native_symbol
      none   no implementation: the gate `imports` is red for the row
conv  stdcall | cdecl | vararg | thiscall | fastcall | data;  ret v|i|q|f|d;  args: x86 stack slots i|q|f|d.
A name missing from its spec, a stub in the spec, a forward that cannot be followed or an unknown argument type is an error: total or failing.
`lookup` is decided by the name table, a declared input (a name is in it or not), not by a run."""
import re

ARG = {"long": "i", "ptr": "i", "str": "i", "wstr": "i", "word": "i", "segptr": "i", "segstr": "i",
       "int64": "q", "double": "d", "float": "f", "": ""}
RET = {("msvcrt.dll", n): "d" for n in "atof ceil floor frexp ldexp modf strtod".split()}
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
L3_MSVCRT = frozenset("__CxxFrameHandler _CxxThrowException _except_handler3 _setjmp3 longjmp _XcptFilter "
                      "?terminate@@YAXXZ ??1type_info@@UAE@XZ _adjust_fdiv _clearfp _controlfp _fpclass _finite "
                      "_isnan".split())
L3_NAMES = {"?terminate@@YAXXZ": "terminate", "??1type_info@@UAE@XZ": "type_info_dtor"}
L3_KERNEL32 = ("RaiseException", "RtlUnwind", "DebugBreak")
DATA_SYMS = {"_iob": "fnvnx_iob"}
VA_MSVCRT = {"sprintf": "fnvnx_msvcrt_va_sprintf", "_snprintf": "fnvnx_msvcrt_va_snprintf",
             "sscanf": "fnvnx_msvcrt_va_sscanf", "_vsnprintf": "fnvnx_msvcrt_va_vsnprintf"}
SPEC_LINE = re.compile(r"(@|\d+)\s+(stdcall|cdecl|varargs|thiscall|extern|stub|fastcall)\s+((?:-\w+(?:=\S+)?\s+)*)"
                       r"(\S+?)\s*(?:\(([^)]*)\))?(?:\s+(\S+))?$")


def read_name_table(text):
    """{name: (symbol, kind)} from `name TAB symbol TAB func|data` lines; a duplicate name or a bad line is an error."""
    out = {}
    for ln, line in enumerate(text.split("\n"), 1):
        if not line.strip():
            continue
        f = line.split("\t")
        if len(f) != 3 or not all(f) or f[2] not in ("func", "data"):
            raise ValueError("name table line %d: need name TAB symbol TAB func|data" % ln)
        if f[0] in out:
            raise ValueError("name table: %s given twice" % f[0])
        out[f[0]] = (f[1], f[2])
    return out


def load(spec_text, dll, cache):
    if dll in cache:
        return cache[dll]
    by_name, by_ord = {}, {}
    for line in spec_text(SPEC_OF.get(dll, dll[:-4])).split("\n"):
        m = SPEC_LINE.match(line.split("#")[0].strip())
        if not m:
            continue
        ordn, kind, _, name, args, target = m.groups()
        ent = (kind, (args or "").split(), target, ordn)
        by_name[name] = ent
        if ordn != "@":
            by_ord[int(ordn)] = (name, ent)
    cache[dll] = (by_name, by_ord)
    return cache[dll]


def resolve(spec_text, dll, name, cache, depth=0):
    by_name, by_ord = load(spec_text, dll, cache)
    if name.startswith("#"):
        name, ent = by_ord[int(name[1:])]
    else:
        ent = by_name[name]
    kind, args, target, ordn = ent
    ordn = "" if ordn == "@" else ordn
    if target and "." in target and depth < 4:
        tdll, tname = target.split(".", 1)
        r = resolve(spec_text, tdll.lower() + ".dll", tname, cache, depth + 1)
        return name, ordn, r[2], r[3]
    if kind == "extern":
        return name, ordn, "data", ""
    if kind == "stub":
        raise KeyError("%s!%s is a stub in the reference spec" % (dll, name))
    conv = {"stdcall": "stdcall", "cdecl": "cdecl", "varargs": "vararg", "thiscall": "thiscall", "fastcall": "fastcall"}[kind]
    return name, ordn, conv, "".join(ARG[a] for a in args)


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


def derive(names, spec_text, syms, name_table, extra):
    """-> (rows sorted by (dll lower, name), errors [str], extra_of {(dll, name): index into `extra`})"""
    rows, errors, extra_of, cache = [], [], {}, {}
    for line in names.split("\n"):
        if not line:
            continue
        dll, name = line.split("\t")[:2]
        if dll == "galaxywrp.dll":
            sig = {"SteamAPI_RegisterCallback": "ii", "SteamAPI_UnregisterCallback": "i"}.get(name, "")
            ret = "v" if name in ("SteamAPI_Shutdown", "SteamAPI_RunCallbacks", "SteamAPI_RegisterCallback",
                                  "SteamAPI_UnregisterCallback") else "i"
            rows.append((dll, name, "", "cdecl", ret, sig, name, "sym" if name in syms else "none"))
            continue
        try:
            real, ordn, conv, args = resolve(spec_text, dll, name, cache)
        except (KeyError, ValueError, OSError) as e:
            errors.append("%s!%s: %s" % (dll, name, e))
            continue
        ret, sym = RET.get((dll, real), "i"), native(dll, real)
        if sym.startswith("fnvl_"):
            bind = "l3"
        elif conv == "data":
            dsym = DATA_SYMS.get(real, real)
            sym, bind = (dsym, "data") if dsym in syms else ("-", "none")
        elif dll == "msvcrt.dll" and real in VA_MSVCRT:
            sym = VA_MSVCRT[real]
            bind = "sym" if sym in syms else "none"
        elif dll == "msvcrt.dll":
            sym, bind = "-", ("lookup" if name in name_table else "none")
        else:
            bind = "sym" if sym in syms else "none"
        rows.append((dll, real, ordn, conv, ret, args, sym, bind))
    for i, text in enumerate(extra):
        for line in text.split("\n"):
            if not line:
                continue
            r = tuple(line.split("\t"))
            if len(r) != 8:
                errors.append("extra[%d]: not 8 columns: %r" % (i, line))
            elif (r[0], r[1]) in extra_of:
                errors.append("%s!%s: given twice (extra[%d], extra[%d])" % (r[0], r[1], extra_of[(r[0], r[1])], i))
            else:
                extra_of[(r[0], r[1])] = i
                rows = [x for x in rows if (x[0], x[1]) != (r[0], r[1])] + [r]
    rows.sort(key=lambda r: (r[0].lower(), r[1]))
    return rows, errors, extra_of
