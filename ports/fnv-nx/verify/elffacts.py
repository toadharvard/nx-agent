#!/usr/bin/env python3
"""elffacts: the facts of a LINKED ELF that the link record carries ("fnvnx-elffacts/2"), so that no consumer runs readelf, nm or objdump on a linked
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

  header      readelf -W -h ELF   -> {machine, type, entry}                                (readelf.c, process_file_header: `Type:`, `Machine:`,
              `Entry point address:`).
  relocations readelf -W -r ELF   -> rows [section, offset, type, symbol, addend]; `section` is the relocation section as printed (`.rela.text`),
              `symbol` is "" for a relocation with no symbol (R_*_RELATIVE) and the section name for a section symbol; `target` of a row is the value of
              its symbol plus the addend (readelf.c, dump_relocations: `Offset Info Type Symbol's Value Symbol's Name + Addend`). A link keeps the static
              relocations of its inputs only with `--emit-relocs` (ld.texi, Options); without it these are the dynamic relocations.
  members     the map's `Archive member included to satisfy reference by file (symbol)` section (ld.texi, `-M`/`-Map`): the archive members the link pulled
              in, each with the file and symbol that required it.
  call_edges, address_taken  derived from the relocations and the symbols by the architecture's relocation types (CALL_TYPES: the call and jump
              relocations are call sites, the conditional-branch ones stay inside their function, every other relocation takes the address of its target).

Every parser is total: a line that does not fit the layout, or a count that disagrees with the header line the tool printed, raises ElfFactsError.
The layouts are pinned by tests/test_elffacts.py on a real link with the same binutils version as env.json (2.42)."""
import re

FORMAT = "fnvnx-elffacts/2"


class ElfFactsError(Exception):
    pass


SEC = re.compile(r"\s*\[\s*(\d+)\]\s+(\S+)\s+(\S+)\s+([0-9a-f]+)\s+([0-9a-f]+)\s+([0-9a-f]+)\s+([0-9a-f]+)\s*([A-Za-z]*)\s+(\d+)\s+(\d+)\s+(\d+)\s*$")
SEC0 = re.compile(r"\s*\[\s*0\]\s+NULL\s+0+\s+0+\s+0+\s+0+\s+0\s+0\s+0\s*$")
SYM = re.compile(r"\s*(\d+):\s+([0-9a-f]+)\s+(0x[0-9a-f]+|\d+)\s+(\S+)\s+(\S+)\s+(\S+)(?:\s+\[[^\]]*\])?\s+(UND|ABS|COM|\d+|\S+\[\S+\])(?:\s+(\S*))?\s*$")


def parse_sections(text):
    """The section header table of `readelf -W -S`. The null section 0 has no name and is not a section of the output (it is checked and omitted).
    -> [{index, name, type, address, size, flags}], in table order."""
    head = re.search(r"^There are (\d+) section headers, starting at offset", text, re.M) or re.search(r"^\s*Number of section headers:\s+(\d+)", text, re.M)
    if not head:   # `-S` alone prints the first form, `-h -S` the file header's own count (readelf.c, process_file_header / process_section_headers)
        raise ElfFactsError("readelf -S: no `There are N section headers` or `Number of section headers:` line")
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


def parse_header(text):
    """The ELF header of `readelf -W -h`: {machine, type, entry}: `Machine:` and `Type:` as printed (the first word of the type), `entry` an integer."""
    def one(key):
        m = re.search(r"^\s*%s:\s+(.*?)\s*$" % re.escape(key), text, re.M)
        if not m:
            raise ElfFactsError("readelf -h: no `%s:` line" % key)
        return m[1]
    ent = re.fullmatch(r"0x([0-9a-f]+)", one("Entry point address"))
    if not ent:
        raise ElfFactsError("readelf -h: `Entry point address:` is not 0xHEX")
    return {"machine": one("Machine"), "type": one("Type").split()[0], "entry": int(ent[1], 16)}


REL_HEAD = re.compile(r"^Relocation section '([^']+)' at offset 0x[0-9a-f]+ contains (\d+) entr(?:y|ies):\s*$")
REL_ROW = re.compile(r"^([0-9a-f]+)\s+([0-9a-f]+)\s+(\S+)(?:\s+([0-9a-f]+|\S+\(\))\s+(\S+)(?:\s+([+-])\s+([0-9a-f]+))?|\s+([0-9a-f]+))?\s*$")


def parse_relocs(text):
    """The relocation sections of `readelf -W -r`: rows [section, offset, type, symbol, addend, target]; target is None for an IFUNC symbol (derive takes its value from the symbol table). The counts of the section headers are checked. A
    file with none prints `There are no relocations in this file.`. A row with no symbol column (a RELATIVE relocation) carries only its addend."""
    out, left, sec, any_ = [], 0, None, False
    for ln in text.splitlines():
        if not ln.strip() or ln.lstrip().startswith("Offset"):
            continue
        if ln.startswith("There are no relocations"):
            any_ = True
            continue
        h = REL_HEAD.match(ln)
        if h:
            if left:
                raise ElfFactsError("readelf -r: %d rows missing in %s" % (left, sec))
            sec, left, any_ = h[1], int(h[2]), True
            continue
        m = REL_ROW.match(ln)
        if not m or sec is None:
            raise ElfFactsError("readelf -r: a row does not fit the layout: %r" % ln)
        if m[8] is not None:
            sym, val, add = "", 0, int(m[8], 16)
        else:
            sym, val = (m[5], None if m[4].endswith("()") else int(m[4], 16)) if m[5] else ("", 0)   # `name()`: an IFUNC symbol, its value is not printed
            add = (-1 if m[6] == "-" else 1) * int(m[7], 16) if m[7] else 0
        out.append([sec, int(m[1], 16), m[3], sym, add, None if val is None else val + add])
        left -= 1
    if left or not any_:
        raise ElfFactsError("readelf -r: %s" % ("%d rows missing in %s" % (left, sec) if left else "no relocation section and no `no relocations` line"))
    return out


def parse_members(map_text):
    """The archive members the link pulled in: [{member, requested_by, symbol}] from the map's `Archive member included to satisfy reference by file
    (symbol)` section, in the order ld printed them. An entry is a line with the member `ARCHIVE(MEMBER)`, then the requirer `FILE (SYMBOL)` on the same line
    or, when the member name fills the column, on the next."""
    lines = map_text.splitlines()
    try:
        i = next(k for k, ln in enumerate(lines) if ln.startswith("Archive member included to satisfy reference by file (symbol)"))
    except StopIteration:   # ld prints the heading lazily, with the first archive member it pulls (ldmain.c add_archive_element): no section = no member, in a map that is a map
        if not any(ln.startswith("Memory Configuration") for ln in lines):
            raise ElfFactsError("map: no `Archive member included` section and no `Memory Configuration` (not a GNU ld map)")
        return []
    out, i, pend = [], i + 1, None
    while i < len(lines):
        ln = lines[i]
        i += 1
        if not ln.strip():
            if out or pend:
                if pend is None:
                    break
            continue
        m = re.fullmatch(r"(\S+\))\s+(\S.*?) \((.*)\)", ln) if ln[0] != " " else None
        if m:
            out.append({"member": m[1], "requested_by": m[2], "symbol": m[3]})
            pend = None
        elif ln[0] != " ":
            if pend is not None:
                raise ElfFactsError("map: archive member %r has no requirer" % pend)
            pend = ln.strip()
        elif pend is not None:
            m = re.fullmatch(r"\s+(\S.*?) \((.*)\)", ln)
            if not m:
                raise ElfFactsError("map: requirer line does not fit: %r" % ln)
            out.append({"member": pend, "requested_by": m[1], "symbol": m[2]})
            pend = None
        else:
            raise ElfFactsError("map: a line of the archive member section does not fit: %r" % ln)
    if pend is not None:
        raise ElfFactsError("map: archive member %r has no requirer" % pend)
    return out


# The relocations that are call sites (a branch to another function) and those that stay inside a function (a conditional branch), per machine as
# readelf prints it. Every other relocation takes the address of its target. AArch64 is the product; X86-64 is the machine of the toy links of the tests.
CALL_TYPES = {"AArch64": {"call": {"R_AARCH64_CALL26", "R_AARCH64_JUMP26"}, "local": {"R_AARCH64_CONDBR19", "R_AARCH64_TSTBR14"}},
              "Advanced Micro Devices X86-64": {"call": {"R_X86_64_PLT32"}, "local": set()}}


UNWIND = {".eh_frame", ".gcc_except_table"}   # the unwind tables name every function they describe; they are metadata, not a use of its address


def derive(header, relocs, symbols, sections):
    """-> (call_edges, address_taken) of the relocations RELOCS (parse_relocs rows) of an ELF with HEADER and defined SYMBOLS (parse_symbols).
    call_edges: [{site, caller, caller_offset, callee, target, type}] sorted by site: SITE the address of the branch, CALLER the names of the FUNC symbols
    whose [value, value+size) holds it ([] if none), CALLER_OFFSET the offset in the first, CALLEE the names of the symbols the target address belongs to (the
    relocation's own symbol when it names one; for a section symbol or no symbol, every symbol whose range holds the target, [] if none).
    address_taken: [{symbol, target}] sorted, one per distinct (symbols, target): the same resolution of every relocation that is no call and no local branch,
    in an allocated section (SECTIONS: parse_sections rows; the target of `.rela.X` / `.rel.X` is X; a dynamic relocation section has no such target and is
    loader work on allocated data) other than the unwind tables UNWIND."""
    alloc = {x["name"] for x in sections if "A" in x["flags"]}
    kinds = CALL_TYPES.get(header["machine"])
    if kinds is None:
        raise ElfFactsError("no call relocation table for machine %r" % header["machine"])
    funcs = sorted((s["value"], s["size"], s["name"]) for s in symbols if s["type"] == "FUNC" and s["size"])
    ranged = sorted((s["value"], max(s["size"], 1), s["name"]) for s in symbols if s["type"] in ("FUNC", "OBJECT", "TLS", "NOTYPE") and s["name"] and s["section"] != "ABS")

    def holding(table, addr):
        return sorted(n for v, z, n in table if v <= addr < v + z)

    def names(row):
        sym = row[3]
        if sym and not sym.startswith(".") and not any(c in sym for c in "@"):
            return [sym]
        return sorted(set(holding(ranged, row[5])))
    calls, taken = [], set()
    byname = {}
    for s in symbols:
        if s["name"] and s["bind"] != "LOCAL":
            byname.setdefault(s["name"], s["value"])
    for sec, off, typ, sym, add, target in relocs:
        if target is None:      # an IFUNC symbol: the value of the symbol of that name in the table
            if sym not in byname:
                raise ElfFactsError("relocation at %#x names the IFUNC symbol %r, which the symbol table does not define" % (off, sym))
            target = byname[sym] + add
        row = [sec, off, typ, sym, add, target]
        if typ in kinds["call"]:
            held = [(v, n) for v, z, n in funcs if v <= off < v + z]
            calls.append({"site": off, "caller": sorted(n for _, n in held), "caller_offset": (off - min(v for v, _ in held)) if held else None,
                          "callee": names(row), "target": target, "type": typ})
        elif typ not in kinds["local"]:
            tgt = re.sub(r"^\.rela?(?=\.)", "", sec)
            if tgt in UNWIND or (tgt != sec and tgt in {x["name"] for x in sections} and tgt not in alloc):
                continue
            taken.add((tuple(names(row)), target))
    return sorted(calls, key=lambda c: (c["site"], c["type"])), [{"symbol": list(n), "target": t} for n, t in sorted(taken)]


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


def reloc_part(text):
    """The relocation part of the stdout of one `readelf -W -h -S -r -s` run: from the first `Relocation section` (or `There are no relocations`) line up to
    the first symbol table. readelf prints the parts in this fixed order (readelf.c, process_object)."""
    lines = text.splitlines()
    try:
        a = next(k for k, ln in enumerate(lines) if ln.startswith("Relocation section '") or ln.startswith("There are no relocations"))
    except StopIteration:
        raise ElfFactsError("readelf -r: no relocation part")
    b = next((k for k in range(a, len(lines)) if lines[k].startswith("Symbol table '")), len(lines))
    return "\n".join(lines[a:b])


def record(elf, readelf_text, cref_text, map_text, product_files, defs_of):
    """The elffacts record of the linked ELF named ELF: its header (entry), sections, defined symbols, relocations, the call edges and address-taken
    symbols derived from them, the archive members the link pulled in, and the definer of every resolved undefined reference of every product object.
    READELF_TEXT is the stdout of one `readelf -W -h -S -r -s`. Deterministic: a function of the texts and of the input symbol tables."""
    head, secs, syms = parse_header(readelf_text), parse_sections(readelf_text), parse_symbols(readelf_text)
    rel = parse_relocs(reloc_part(readelf_text))
    calls, taken = derive(head, rel, syms, secs)
    return {"format": FORMAT, "elf": elf, "header": head, "sections": secs, "symbols": syms, "relocations": rel, "call_edges": calls, "address_taken": taken,
            "members": parse_members(map_text), "references": definers(parse_cref(cref_text), product_files, defs_of, {s["name"] for s in syms})}
