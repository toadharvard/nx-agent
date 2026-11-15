"""bootfacts.py - the L4 views of the ELF facts of a linked output: lookups in the link record (L1's "fnvnx-elffacts/2", `elffacts.json` beside the
linked ELF, objects.json key `elf_facts`), the one source of sections and defined symbols (rule 2: L4 reads no section or symbol table of an ELF).

  load(path)               the record, its format checked
  sections_by_name(rec)      {section name: [(address, size), ...]}                 (every section of the final ELF, a name may repeat)
  symbol_values(rec)     {symbol name: [value, ...]}   the defined symbols of .symtab other than STT_SECTION and STT_FILE, all bindings
  entry(rec)               header.entry: the entry address of the image (FactError when the record has none)
  symbol(rec, name)        the one value of the symbol NAME; FactError when it is not defined exactly once with one value
  symbol_bytes(rec, data, name)   the st_size bytes of the one defined symbol NAME, read from the image DATA through its PT_LOAD program headers
                           (bootlayout.file_bytes); FactError when the symbol is not defined exactly once, or its bytes are not file-backed (.bss)
A field the record lacks is a FactError naming it. The record's layout is elffacts.py's (RECIPE.md "ELF facts of a linked output").
"""
import json

import bootlayout

FORMAT = "fnvnx-elffacts/2"
NOT_SYMBOLS = ("SECTION", "FILE")


class FactError(ValueError):
    pass


def load(path):
    try:
        rec = json.load(open(path))
    except (OSError, ValueError) as e:
        raise FactError("%s: %s" % (path, e))
    if not isinstance(rec, dict) or rec.get("format") != FORMAT:
        raise FactError("%s: not an %s record" % (path, FORMAT))
    for k in ("sections", "symbols"):
        if not isinstance(rec.get(k), list):
            raise FactError("%s: no list `%s`" % (path, k))
    return rec


def sections_by_name(rec):
    out = {}
    for s in rec["sections"]:
        out.setdefault(s["name"], []).append((s["address"], s["size"]))
    return out


def _syms(rec):
    return [s for s in rec["symbols"] if s["type"] not in NOT_SYMBOLS and s["name"]]


def symbol_values(rec):
    out = {}
    for s in _syms(rec):
        out.setdefault(s["name"], []).append(s["value"])
    return out


def _one(rec, name):
    found = [s for s in _syms(rec) if s["name"] == name]
    if len(found) != 1:
        raise FactError("symbol %s: defined %d times, need exactly once" % (name, len(found)))
    return found[0]


def entry(rec):
    """the entry address of the linked image (header.entry of the record: e_entry, a link-time address)"""
    h = rec.get("header")
    if not isinstance(h, dict) or not isinstance(h.get("entry"), int):
        raise FactError("the record has no header.entry")
    return h["entry"]


def symbol(rec, name):
    return _one(rec, name)["value"]


def symbol_bytes(rec, data, name):
    s = _one(rec, name)
    try:
        return bootlayout.file_bytes(data, bootlayout.parse_elf(data), s["value"], s["size"], "symbol " + name)
    except bootlayout.LayoutError as e:
        raise FactError(str(e))
