#!/usr/bin/env python3
"""declaredmods - the services the declared machine does not have, as served dll names (L1 CONTRACT).

Input `platform/declared_modules.tsv` (owner L5): rows `dll<TAB>kind<TAB>cite`.  kind is `served-absent-service`: the dll's behaviour is a service the declared
machine lacks (the network, the GOG Galaxy service: CLAUDE.md "The declared machine", "GOG Galaxy is not lifted"), so the platform serves its entries even when a file of that
name is in the declared file set; such a dll is never an image.  A row declares a name the declared machine has that Wine 9.0 does not (a vendor service), so it need not be in declared_dlls.tsv.  `cite` is the heading text of the CLAUDE.md section that states the absent service.
Red (all listed, StageError): a row whose dll is in the declared dll set with no file (modules.read_served: the row would be redundant), a row with no cite or a cite that is not a heading of CLAUDE.md, a duplicate row,
an unknown kind, a malformed row."""
import re


class DeclaredModsError(Exception):
    pass


KINDS = ("served-absent-service",)


def headings(claude_md):
    return {m.group(1).strip() for m in re.finditer(r"^#+\s+(.*\S)\s*$", claude_md, re.M)}


def read(text, claude_md):
    """-> frozenset of the served-absent-service dll names. TEXT: declared_modules.tsv; CLAUDE_MD: its text."""
    heads, seen, errors, out = headings(claude_md), set(), [], set()
    for n, ln in enumerate(text.split("\n"), 1):
        if not ln.strip() or ln.startswith("#"):
            continue
        r = ln.split("\t")
        if len(r) != 3 or not all(c.strip() for c in r[:2]):
            errors.append("line %d: want dll<TAB>kind<TAB>cite" % n)
            continue
        dll, kind, cite = r[0].strip().lower(), r[1].strip(), r[2].strip()
        if kind not in KINDS:
            errors.append("line %d %s: kind %r is not one of %s" % (n, dll, kind, "/".join(KINDS)))
        if not cite:
            errors.append("line %d %s: no cite" % (n, dll))
        elif not any(cite in h for h in heads):
            errors.append("line %d %s: cite %r is not a heading of CLAUDE.md" % (n, dll, cite))
        if dll in seen:
            errors.append("line %d: duplicate row %s" % (n, dll))
        seen.add(dll)
        out.add(dll)
    if errors:
        raise DeclaredModsError("declared_modules.tsv: " + "; ".join(errors))
    return frozenset(out)
