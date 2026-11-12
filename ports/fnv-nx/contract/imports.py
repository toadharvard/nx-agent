"""Gate `imports` (class 4 of verify/VERDICT.md), pure judgement: every row of the bridge table has a body proven by the build products.

Inputs (all values, no file): rows from bridgetable.derive; `syms` {symbol: [(bind, type)]} of the product objects (objects.json `defined`);
`table` the msvcrt name table {name: (symbol, kind)}; `others` the same kind of map over every product root that is not the one the rows belong
to; `owner_of` {(dll, name): index} of rows taken from files that belong to another owner's build (the `external` rows).
A row is certified iff its body is proven by the symbol table of a build product:
  sym/data  a GLOBAL (or UNIQUE) symbol of type FUNC (sym) or OBJECT (data) is defined in the row's root
  lookup    the name table entry of the name names a symbol defined GLOBAL of type FUNC (kind func) or OBJECT (kind data) in the root
  l3        and rows of another owner's file: such a symbol is defined in another product root (the lifter runtime's build); else `external`
Classes of an uncertified row: none (no symbol of that name is defined or referenced), unresolved-symbol (referenced only), weak (defined weak
only), not-a-function (defined global of the wrong type), lookup-miss (an msvcrt name that the name table does not carry, or whose entry names
no proven symbol), external (l3 or another owner's row that no other root defines).
The decision reads symbol tables and tables, never a source text, a run or a name pattern. Assumption (ASM-052): objects.json lists every global, weak
and unique symbol of every object of the root (recipe.symbols); locals are not symbols a row can bind to."""


def elf_class(ents, want):
    """None if some entry is a defined GLOBAL/UNIQUE symbol of type `want`, else the class of the failure."""
    glob = [t for b, t in ents if b in ("GLOBAL", "UNIQUE")]
    if want in glob:
        return None
    if glob:
        return "not-a-function"
    if any(b == "WEAK" for b, _ in ents):
        return "weak"
    return "unresolved-symbol"


def judge(rows, syms, table, others, owner_of):
    """-> [(id `dll!name`, class or None)] in row order"""
    out = []
    for dll, name, _, _, _, _, sym, bind in rows:
        want = "OBJECT" if bind == "data" else "FUNC"
        if bind == "l3" or (dll, name) in owner_of:
            cls = None if elf_class(others.get(sym, []), want) is None else "external"
        elif bind == "lookup":
            tgt, kind = table[name]
            cls = None if elf_class(syms.get(tgt, []), "OBJECT" if kind == "data" else "FUNC") is None else "lookup-miss"
        elif bind == "none":
            cls = "lookup-miss" if dll == "msvcrt.dll" and sym == "-" else "none"
        elif bind in ("sym", "data"):
            cls = elf_class(syms.get(sym, []), want)
        else:
            cls = "none"
        out.append(("%s!%s" % (dll, name), cls))
    return out
