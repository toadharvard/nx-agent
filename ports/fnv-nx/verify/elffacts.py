#!/usr/bin/env python3
"""elffacts: the facts of a LINKED ELF that the link record carries ("fnvnx-elffacts/1"), so that no consumer runs readelf, nm or objdump on a linked
output. Pure functions over texts the PINNED binutils of env.json printed (the link stage's own readelf and ld stages): no process is started here.

  sections    readelf -W -S ELF   -> [{index, name, type, address, size, flags}]            (binutils readelf.c, process_section_headers:
              the columns `[Nr] Name Type Address Off Size ES Flg Lk Inf Al`, the numbers in hex without 0x, Flg a string of the letters of the
              readelf legend (W write, A alloc, X exec, M merge, S strings, I info, L link order, O extra OS processing, G group, T TLS,
              C compressed, E exclude, R retain, p processor specific, o OS specific, x unknown, l large)).
  symbols     readelf -W -s ELF   -> the DEFINED symbols (Ndx not UND) of `.symtab`: [{name, value, size, type, bind, section}]  (readelf.c,
              process_symbol_table / print_dynamic_symbol: `Num: Value Size Type Bind Vis [other] Ndx Name`; Size is decimal, or 0xHEX above 100000).
  definers    ld --cref --no-demangle -Map=MAP   -> {symbol: [file, ...]}  (ld.texi, Options, `--cref`: "Output a cross reference table. ... The
              format of the table is intentionally simple, so that it may be easily processed by a script if necessary. The symbols are printed
              out, sorted by name. For each symbol, a list of file names is given. If the symbol is defined, the first file listed is the
              location of the definition. If the symbol is defined as a common value then any files where this happens appear next. Finally any
              files that reference the symbol are listed."). A name is followed by its first file on the same line (a name too long for the
              column puts the file on the next line); further files are on lines of their own, indented. A file inside an archive is `ARCHIVE(MEMBER)`.
  Where the first listed file is not a definition, the symbol is one the linker defines itself (`__ehdr_start`, `_end`, a script assignment) or
  a weak undefined: ld's source (ldmain.c notice -> ldcref.c add_cref) records a definition only for a symbol that an input defines. The rule here
  is therefore exact and not a guess: the first file is the definer iff ITS OWN symbol table defines the name (the callers pass `defs_of`).

Every parser is total: a line that does not fit the layout, or a count that disagrees with the header line the tool printed, raises ElfFactsError.
The layouts are pinned by tests/test_elffacts.py on a real link with the same binutils version as env.json (2.42)."""
import re

FORMAT = "fnvnx-elffacts/1"


class ElfFactsError(Exception):
    pass


SEC = re.compile(r"\s*\[\s*(\d+)\]\s+(\S+)\s+(\S+)\s+([0-9a-f]+)\s+([0-9a-f]+)\s+([0-9a-f]+)\s+([0-9a-f]+)\s*([A-Za-z]*)\s+(\d+)\s+(\d+)\s+(\d+)\s*$")
SEC0 = re.compile(r"\s*\[\s*0\]\s+NULL\s+0+\s+0+\s+0+\s+0+\s+0\s+0\s+0\s*$")
SYM = re.compile(r"\s*(\d+):\s+([0-9a-f]+)\s+(0x[0-9a-f]+|\d+)\s+(\S+)\s+(\S+)\s+(\S+)(?:\s+\[[^\]]*\])?\s+(UND|ABS|COM|\d+|\S+\[\S+\])(?:\s+(\S*))?\s*$")


def parse_sections(text):
    """The section header table of `readelf -W -S`. The null section 0 has no name and is not a section of the output (it is checked and omitted).
    -> [{index, name, type, address, size, flags}], in table order."""
    head = re.search(r"^There are (\d+) section headers, starting at offset", text, re.M)
    if not head:
        raise ElfFactsError("readelf -S: no `There are N section headers` line")
    out, in_table = [], False
    for ln in text.splitlines():
        if ln.startswith("Section Headers:"):
            in_table = True
            continue
        if not in_table:
            continue
        if ln.startswith("Key to Flags:"):
            break
        if not ln.strip() or ln.lstrip().startswith("[Nr]"):
            continue
        if SEC0.match(ln):
            continue
        m = SEC.match(ln)
        if not m:
            if re.match(r"\s*[A-Za-z]", ln) and not ln.lstrip().startswith("["):   # the legend lines carry no table row
                break
            raise ElfFactsError("readelf -S: a section row does not fit the layout: %r" % ln)
        out.append({"index": int(m[1]), "name": m[2], "type": m[3], "address": int(m[4], 16), "size": int(m[6], 16), "flags": m[8]})
    if len(out) + 1 != int(head[1]):
        raise ElfFactsError("readelf -S: %d rows for %s section headers" % (len(out) + 1, head[1]))
    return out


def parse_symbols(text, table=".symtab"):
    """The DEFINED symbols of the symbol table TABLE in `readelf -W -s`: [{name, value, size, type, bind, section}] in table order. `section` is the Ndx
    column as printed (a section index, ABS or COM), `value` and `size` are integers. A symbol of Ndx UND is not defined and is left out; the header
    count is checked against every row of the table, defined or not."""
    head = re.search(r"^Symbol table '%s' contains (\d+) entr(?:y|ies):" % re.escape(table), text, re.M)
    if not head:
        raise ElfFactsError("readelf -s: no symbol table %s" % table)
    rows, out = 0, []
    lines = text.splitlines()
    i = next(k for k, ln in enumerate(lines) if ln.startswith("Symbol table '%s'" % table)) + 1
    while i < len(lines) and not lines[i].startswith("Symbol table "):
        ln = lines[i]
        i += 1
        if not ln.strip() or ln.lstrip().startswith("Num:"):
            continue
        m = SYM.match(ln)
        if not m:
            raise ElfFactsError("readelf -s: a symbol row does not fit the layout: %r" % ln)
        rows += 1
        if m[7] != "UND":
            out.append({"name": m[8] or "", "value": int(m[2], 16), "size": int(m[3], 0), "type": m[4], "bind": m[5], "section": m[7]})
    if rows != int(head[1]):
        raise ElfFactsError("readelf -s: %d rows for %s entries of %s" % (rows, head[1], table))
    return out


def parse_cref(text):
    """The cross reference table of a linker map written with --cref --no-demangle: {symbol: [file, ...]} in ld's order (definition first, if any).
    A symbol can occur once only (the table is sorted by name and has one entry per symbol)."""
    lines = text.splitlines()
    try:
        i = lines.index("Cross Reference Table")
    except ValueError:
        raise ElfFactsError("map: no `Cross Reference Table` (ld --cref)")
    i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines) or lines[i].split() != ["Symbol", "File"]:
        raise ElfFactsError("map: the cross reference table has no `Symbol File` header")
    out, cur, i = {}, None, i + 1
    pending = None       # a symbol whose first file is on the next line (a name that fills the symbol column)
    for ln in lines[i:]:
        if not ln.strip():
            continue
        if ln[0] != " ":
            parts = ln.split(None, 1)
            cur = parts[0]
            if cur in out:
                raise ElfFactsError("map: symbol %r twice in the cross reference table" % cur)
            out[cur] = []
            pending = None
            if len(parts) == 2:
                out[cur].append(parts[1].strip())
            else:
                pending = cur
        else:
            if cur is None:
                raise ElfFactsError("map: a file line before any symbol: %r" % ln)
            out[cur].append(ln.strip())
            pending = None
    if pending is not None:
        raise ElfFactsError("map: symbol %r has no file" % pending)
    return out


def definers(cref, product_files, defs_of, defined_in_output):
    """For every product object the definer of each undefined reference it makes that the link resolved.
    CREF: parse_cref(); PRODUCT_FILES: {file name as the link line gave it: product object key}; DEFS_OF(file) -> the set of names that input file
    defines (global and weak), or None when the file is no input of the link (an error: ld printed a file the caller did not give it);
    DEFINED_IN_OUTPUT: the names the linked ELF defines. -> {product key: [{symbol, definer}]} with definer = ld's file name, or "<linker>" for a name
    the linker defines itself (no input defines it). A name that no one defines (weak undefined) is not a resolved reference and is left out."""
    out = {k: [] for k in product_files.values()}
    for sym, files in sorted(cref.items()):
        if not files:
            raise ElfFactsError("map: symbol %r has no file" % sym)
        first = files[0]
        d = defs_of(first)
        if d is None:
            raise ElfFactsError("map: file %r of symbol %r is no input of the link" % (first, sym))
        if sym in d:
            definer, refs = first, files[1:]
        elif sym in defined_in_output:
            definer, refs = "<linker>", files
        else:
            continue
        for f in refs:
            if f in product_files and f != definer:
                out[product_files[f]].append({"symbol": sym, "definer": definer})
    for v in out.values():
        v.sort(key=lambda x: x["symbol"])
    return out


def record(elf, sections_text, symbols_text, cref_text, product_files, defs_of):
    """The elffacts record of the linked ELF named ELF: its sections, its defined symbols and the definer of every resolved undefined reference of
    every product object. Deterministic: a function of the three texts and of the input symbol tables."""
    secs, syms, cref = parse_sections(sections_text), parse_symbols(symbols_text), parse_cref(cref_text)
    return {"format": FORMAT, "elf": elf, "sections": secs, "symbols": syms,
            "references": definers(cref, product_files, defs_of, {s["name"] for s in syms})}
