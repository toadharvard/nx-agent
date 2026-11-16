"""Bridge table of the import guest entries, pure derivation (rows: dll, name, ordinal, conv, ret, args, native_symbol, bind).

A pure function of its arguments (no file, no process): the driver verify/gate_imports.py reads the declared inputs and calls derive().
  names       text of sys_names.tsv (dll TAB name per line) from the private manifest
  spec_text   function dll -> text of the export specification of the reference environment (Wine 9.0 dlls/<dll>/<dll>.spec)
  syms        the set of symbol names that the product objects define or reference (objects.json defined + undefined); None = the rows table
              (stage A): every row binds by rule alone (sym, data), no symbol table is read and none is judged; the table is then a
              function of the names, the specs, the exports and the extra rows only, so the import keys (contract/iat.py) are too.  The
              symbols are judged against the rows by the gate `imports` (stage B: contract/imports.judge over derive(syms=the objects' symbols))
  extra       texts of rows in this 8-column format (other owners' rows: COM interfaces); a row of the same (dll, name) replaces the derived one
  exports     {(module, name): (symbol, kind, ret, args)} of exports.tsv (contract/exportgen.py: the `.fnvnx.export` records of the product objects, `ret` and `args` from the definer's own
              `.fnvnx.decltype`): THE source of a function row's native symbol and return letter, and of the argument letters of a module that has no reference spec. No constant of this file
              names a symbol, a return type or a signature.
bind  link   the row has an export record: native_symbol is defined by exactly one member of the link (func or, for a data row, the object)
      entry  the same, the record's kind is `entry`: the body works on the guest state, the bridge jumps to it and marshals nothing (ret `-`; conv and args stay the reference spec's: the callee-pop of the x86 call)
      sym    native_symbol is a symbol of the root's own objects (rows of another owner's file)      data  the same for a data row without a record
      none   no implementation: the gate `imports` is red for the row
conv  stdcall | cdecl | vararg | thiscall | fastcall | data;  ret v|i|q|f|d;  args: x86 stack slots i|q|f|d.
A name missing from its spec, a stub in the spec, a forward that cannot be followed or an unknown argument type is an error: total or failing.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): the siblings are imported by their qualified name (contract.X)
from fnvnx.contract import dllkind  # noqa: E402

NOSPEC_CONV = "cdecl"   # a module without a reference spec is a vendor SDK's: its exports are the C ABI of the SDK (steam_api.h S_API), whose x86 convention is cdecl


def has_spec(spec_text, dll):
    """True iff the reference environment has a spec for DLL; a missing spec file (FileNotFoundError of the reader) is the only way to None, every other error is the caller's."""
    try:
        spec_text(dll[:-4])
    except FileNotFoundError:
        return False
    return True


ARG = {"long": "i", "ptr": "i", "str": "i", "wstr": "i", "word": "i", "segptr": "i", "segstr": "i",
       "int64": "q", "double": "d", "float": "f", "": ""}
SPEC_LINE = re.compile(r"(@|\d+)\s+(stdcall|cdecl|varargs|thiscall|extern|stub|fastcall)\s+((?:-\w+(?:=\S+)?\s+)*)"
                       r"(\S+?)\s*(?:\(([^)]*)\))?(?:\s+(\S+))?$")


def read_va(text):
    """{msvcrt name: native symbol} from `name TAB symbol` lines of the va record (the exports whose native body reads the x86 stack words of the
    call); a duplicate name, a bad line or a symbol that is not a C identifier is an error."""
    out = {}
    for ln, line in enumerate(text.split("\n"), 1):
        if not line.strip():
            continue
        f = line.split("\t")
        if len(f) != 2 or not all(f) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", f[1]):
            raise ValueError("va record line %d: need name TAB symbol" % ln)
        if f[0] in out:
            raise ValueError("va record: %s given twice" % f[0])
        out[f[0]] = f[1]
    return out


def load(spec_text, dll, cache):
    if dll in cache:
        return cache[dll]
    by_name, by_ord = {}, {}
    for line in spec_text(dll[:-4]).split("\n"):
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
    """-> (name, ordinal, conv, args, chain) of the import `name` of DLL by the reference spec. CHAIN is the forward chain of the declared machine's own export: [(dll, name)] from the
    importing module through every forward to the module whose entry is not a forward (Wine 9.0 spec `module.name`), the definers a record may be keyed by (see record_of)."""
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
        return name, ordn, r[2], r[3], [(dll, name)] + r[4]
    if kind == "extern":
        return name, ordn, "data", "", [(dll, name)]
    if kind == "stub":
        raise KeyError("%s!%s is a stub in the reference spec" % (dll, name))
    conv = {"stdcall": "stdcall", "cdecl": "cdecl", "varargs": "vararg", "thiscall": "thiscall", "fastcall": "fastcall"}[kind]
    return name, ordn, conv, "".join(ARG[a] for a in args), [(dll, name)]


def record_of(exports, chain):
    """The export record of an import: the one record keyed by a member of its forward chain (the module that serves the name in the declared machine, wsock32 -> ws2_32).
    -> (record, error): no member keyed is (None, None), two members keyed is an error (one definer per import)."""
    got = [(k, exports[k]) for k in chain if k in exports]
    if len(got) > 1:
        return None, "records for %s: one import has one definer" % " and ".join("%s!%s" % k for k, _ in got)
    return (got[0][1] if got else None), None


def derive(names, spec_text, syms, extra, va, exports):
    """-> (rows sorted by (dll lower, name), errors [str], extra_of {(dll, name): index into `extra`})
    A function row's native symbol and return letter come from its export record (EXPORTS, record_of: the record keyed by the import or by a module of its forward chain) and
    from nothing else: an import with no record, or whose record says data, is an error; a data row without a record is served by the name table (msvcrt) or keeps its own name.
    VA ({msvcrt name: forwarder symbol}, read_va) is checked against the records: the export of a name in it must be the forwarder, and a varargs msvcrt export must be in it."""
    rows, errors, extra_of, cache = [], [], {}, {}
    has = (lambda sym: True) if syms is None else (lambda sym: sym in syms)
    for line in names.split("\n"):
        if not line:
            continue
        dll, name = line.split("\t")[:2]
        if dllkind.is_runtime(dll):
            errors.append("%s!%s: an import of the runtime's own module: it is no image and has no import table entry" % (dll, name))
            continue
        if not has_spec(spec_text, dll):   # a module without a reference spec (a vendor SDK's, e.g. galaxywrp.dll): its definer's own declared type is the signature
            rec = exports.get((dll, name))
            if rec is None:
                errors.append("%s!%s: the module has no reference spec and the import no export record: nothing gives its signature" % (dll, name))
                continue
            real, ordn, chain = name, "", [(dll, name)]
            conv, args = ("data", "") if rec[1] == "data" else (NOSPEC_CONV, "" if rec[3] == "-" else rec[3])
        else:
            try:
                real, ordn, conv, args, chain = resolve(spec_text, dll, name, cache)
            except (KeyError, ValueError, OSError) as e:
                errors.append("%s!%s: %s" % (dll, name, e))
                continue
        rec, err = record_of(exports, chain)
        if err:
            errors.append("%s!%s: %s" % (dll, name, err))
            continue
        if conv == "data":
            if rec is not None:
                if rec[1] != "data":
                    errors.append("%s!%s: the reference spec says data, exports.tsv says %s" % (dll, name, rec[1]))
                    continue
                dsym = rec[0]
            else:
                dsym = real
            sym, bind = ((dsym, "link") if rec is not None else (dsym, "data") if has(dsym) else ("-", "none"))
            rows.append((dll, real, ordn, conv, "-", args, sym, bind))
            continue
        if rec is None:
            errors.append("%s!%s: no export record: the definer is not declared (FNVNX_EXPORT), so the native symbol and the return type have no source" % (dll, name))
            continue
        sym, kind, ret, _ = rec
        if kind == "data":
            errors.append("%s!%s: exports.tsv says %s, the reference spec says %s" % (dll, name, kind, conv))
            continue
        if dll == "msvcrt.dll" and real in va and va[real] != sym:
            errors.append("%s!%s: the va record names the forwarder %s, the export record %s" % (dll, name, va[real], sym))
            continue
        if dll == "msvcrt.dll" and conv == "vararg" and real not in va:
            errors.append("%s!%s: a varargs export has no forwarder in the va record" % (dll, name))
            continue
        rows.append((dll, real, ordn, conv, ret, args, sym, "entry" if kind == "entry" else "link"))
    for (dll, name), (sym, kind, ret, args) in sorted(exports.items()):   # the runtime's own module: one row per record, conv and args from the definer's declared type like any spec-less module
        if not dllkind.is_runtime(dll):
            continue
        if kind == "data":
            rows.append((dll, name, "", "data", "-", "", sym, "link"))
        else:
            rows.append((dll, name, "", NOSPEC_CONV, ret, "" if args == "-" else args, sym, "entry" if kind == "entry" else "link"))
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
