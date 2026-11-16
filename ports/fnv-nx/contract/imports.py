"""Gate `imports` (class 4 of verify/VERDICT.md), pure judgement: every row of the bridge table has a body proven by the build products.

Inputs (all values, no file): rows from bridgetable.derive; `syms` {symbol: [(bind, type)]} of the product objects (objects.json `defined`);
`table` the msvcrt name table {name: (symbol, kind)}; `others` {symbol: {member: [(bind, type)]}} over the MEMBERS of the link that the rows' root enters
(the root itself and every link candidate, recipe.link_candidates: the roots the pinned ld may take objects from; not "every root", and not the root's `uses`); `owner_of` {(dll, name): index} of rows taken from files that belong to another owner's build (the `external` rows).
A row is certified iff its body is proven by the symbol table of a build product:
  sym/data  a GLOBAL (or UNIQUE) symbol of type FUNC (sym) or OBJECT (data) is defined in the row's root
  link/entry (a row with an export record; entry: a guest-state body, FUNC like link) and rows of another owner's file: such a symbol is defined (GLOBAL or UNIQUE, the right type) by EXACTLY ONE member of the link; no member:
            `external`; several: `multiple-definers` (a name two members define is not a body, it is a link error)
Classes of an uncertified row: none (no symbol of that name is defined or referenced), unresolved-symbol (referenced only), weak (defined weak
only), not-a-function (defined global of the wrong type), external (a link or another owner's row that no member of the link defines), multiple-definers (more than one member defines it).
The decision reads symbol tables and tables, never a source text, a run or a name pattern. Assumption (ASM-052): objects.json lists every global, weak
and unique symbol of every object of the root (recipe.symbols); locals are not symbols a row can bind to."""


def elf_class(ents, want):
    """None if some entry is a defined GLOBAL/UNIQUE symbol of type `want`, else the class of the failure."""
    if not ents:
        return "none"
    glob = [t for b, t in ents if b in ("GLOBAL", "UNIQUE")]
    if want in glob:
        return None
    if glob:
        return "not-a-function"
    if any(b == "WEAK" for b, _ in ents):
        return "weak"
    return "unresolved-symbol"


def member_class(by_member, want):
    """None iff exactly one member of BY_MEMBER {member: [(bind, type)]} defines a GLOBAL/UNIQUE symbol of type WANT, `multiple-definers` iff several do, else `external`."""
    n = sum(1 for ents in by_member.values() if elf_class(ents, want) is None)
    return None if n == 1 else "multiple-definers" if n > 1 else "external"


def judge(rows, syms, others, owner_of):
    """-> [(id `dll!name`, class or None)] in row order"""
    out = []
    for dll, name, _, conv, _, _, sym, bind in rows:
        want = "OBJECT" if conv == "data" else "FUNC"
        if bind in ("link", "entry") or (dll, name) in owner_of:
            cls = member_class(others.get(sym, {}), want)
        elif bind == "none":
            cls = "none"
        elif bind in ("sym", "data"):
            cls = elf_class(syms.get(sym, []), want)
        else:
            cls = "none"
        out.append(("%s!%s" % (dll, name), cls))
    return out
