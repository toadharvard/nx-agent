#!/usr/bin/env python3
"""Gate "assumptions" (fnvnx-gate/1): every assumption the rules rest on is in one registry, with an id and a status
(verify/VERDICT.md class 18); the accepted ones are ASM-001 to ASM-009.
    python3 -I gate_assumptions.py   run in the checkout root (a git worktree), env GATE_OUT; writes GATE_OUT/assumptions.json

Registry: verify/ASSUMPTIONS.json ("fnvnx-assumptions/1"; the accepted entries are ASM-001 to ASM-009):
  {"format": "fnvnx-assumptions/1", "entries": [ENTRY...]}
  ENTRY  {"id": "ASM-NNN", "text": STR, "used_by": [PATH...], "aliases": [STR...], "status": "OWNER" | "REMOVE", ...}
    OWNER   + "decision": {"file": "CLAUDE.md", "quote": STR}   the owner's acceptance, verbatim
    REMOVE  + "removed_by": STR, "owner": LEAD | LEAD+LEAD      the discharge and the stream lead(s) who owe it
    optional: "note": STR. The registry holds exactly the entries that files of THIS tree rely on: the entry of an unmerged
              branch enters with that branch's merge, and every rule below applies to every entry without exception.
Universe: the MENTIONS of the tree. A text file is a tracked file below ports/fnv-nx that decodes as UTF-8 without NUL
bytes and is not data (suffix .json); a file of a root of verify/admit.json marked "record": true is an append-only log
(its dated entries state what was said then; the owner forbids editing it) and is not scanned. CLAUDE.md (the owner's
words) is read for the decisions, not scanned. A BLOCK is the unit of a text file: in a .md file a paragraph (lines up to a
blank line) cut further at every list item and every table row; in any other file the maximal run of non-blank lines. A
mention is a block whose PROSE matches `assum` or `допущени` at a word start (case-insensitive). PROSE is defined by one lexer per
file kind, chosen by the extension from the one table LEXER: python = COMMENT tokens and docstring STRING tokens (tokenize + ast); c, c++,
headers, .ld, .s = // and /* */ comments (string and char literals skipped); markdown = text outside fenced code blocks; shell and toml =
# comments (quoted strings and heredoc bodies skipped); asm = ; comments; sail = as c; data (.json .tsv .txt .expect .patch) = never prose. In every prose span the
backticked spans are dropped. Totality: a text file whose extension is not in the table, or that its lexer cannot read, is red (codes
ext-unknown, lex-error); nothing is skipped silently. An item of the
universe is (path, first line of the block). A mention is certified iff ALL hold:
  1. its raw block contains at least one id of the form ASM-NNN, for example ASM-001 (the compiler is correct);
  2. every id in it is the id of an entry of the registry.
The registry is certified iff ALL hold:
  3. ids are unique and well formed, aliases are unique across entries, text is not empty;
  4. used_by is not empty and every path in it is a tracked file that contains the entry's id (the reliance is written at the
     place of reliance, so an entry nobody cites is dead and is red);
  5. an OWNER entry has decision.file CLAUDE.md and decision.quote occurring verbatim inside exactly one numbered item under
     the line "OWNER, the only accepted ones:", no item serves two entries, and every item has an entry: the OWNER set is
     exactly the owner's list, so a tenth OWNER entry without the owner's item is red;
  6. a REMOVE entry has an owner of LEADS (or two joined by +) and `removed_by` of one of the forms `gate:NAME` (NAME is a
     gate file of verify/gates.d), `fact:PATH` (PATH is a tracked file: the fact table that decides it), `planned: TEXT`
     (the gate or fact is named and does not exist yet), `no gate yet: TEXT` (the fact that is needed) or `OPEN: TEXT`
     (nobody has a discharge yet). planned, no gate yet and OPEN are reported by count; they are the debt of the owner named in the entry.
  7. id blocks: an id below 200 is free (the records that predate the blocks; L1 takes the free numbers below 200); an id from
     200 lies in the block of the entry's owner lead (the first lead of a joint owner): L3a 2xx, L3b 3xx, L4 4xx, L5 5xx,
     L6 6xx, L6A 7xx, L2 8xx. A record of a layer branch enters with the branch's merge under its block, so ids never collide.
And no id outside the registry occurs in any tracked text file of the tree (a .json file included): an id that is
not in the registry is red wherever it is written.
Argument: an assumption is introduced by writing it down; every writing with the word in the universe is a mention, and a
mention passes only if it names a registry entry, so a new assumption that is written with the word has an id, and an id has
an entry (2, and the last rule); the entry has an owner decision or a named discharge (5, 6), and the files that rely on it
cite it (4). The verdict is green only if certified == universe and the registry is certified. The accepted
assumptions of the rule itself are ASM-001 (the compiler) and ASM-004 (the console), as for every gate.
Limits of this rule (stated as ASM-001..009 do not cover them; they are what a gate over words can close): an assumption that
is written without the words of the pattern is not seen (the rule closes the class "written with the word"; review closes the
rest); a data file (.json) states values, the rule that reads it is the code or document that cites ids; the append-only
logs are not scanned."""
import ast
import io
import json
import os
import re
import subprocess
import sys
import tokenize

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gatesdir  # noqa: E402

TREE = "ports/fnv-nx"
REGISTRY = TREE + "/verify/ASSUMPTIONS.json"
ADMIT = TREE + "/verify/admit.json"
FORMAT = "fnvnx-assumptions/1"
OWNER_FILE = "CLAUDE.md"
OWNER_HEAD = "OWNER, the only accepted ones:"
NUMBERED = re.compile(r"(\s*)\d+\.\s")
FIRST_RANGED = 100      # ids below it are the legacy ids ASM-001..054 and the free numbers of L1: exempt from the ranges
GATE = "assumptions"


class F(str):
    """One finding: a str (its text, so a finding reads as a message) with a stable CODE and the SUBJECT (an ASM id, or path:line) it is about."""
    def __new__(cls, code, subject, text):
        o = str.__new__(cls, text)
        o.code, o.subject = code, subject
        return o


def finding(code, subject, text):
    return F(code, subject, text)
LEADS = ("L1", "L2", "L3a", "L3b", "L4", "L5", "L6", "L6A")
STATUSES = ("OWNER", "REMOVE")
WORD = re.compile(r"(?<!\w)assum|(?<!\w)допущени", re.I)
ID = re.compile(r"\bASM-(\d{3})\b")
ID_FORM = re.compile(r"ASM-\d{3}\Z")
ITEM = re.compile(r"\s*(?:[-*+]|\d+[.)])\s")
ROW = re.compile(r"\s*\|")


LEXER = {".py": "python", ".c": "c", ".cc": "c", ".cpp": "c", ".cxx": "c", ".h": "c", ".hh": "c", ".hpp": "c", ".ld": "c", ".s": "c", ".sail": "c", ".asm": "semi",
         ".md": "markdown", ".sh": "shell", ".gitignore": "shell", ".toml": "toml",
         ".json": "data", ".tsv": "data", ".txt": "data", ".expect": "data", ".patch": "data"}
CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.S)


class LexError(Exception):
    pass


def suffix_of(path):
    """the key of the one lexer table: the extension, or the whole name of a file with no extension (.gitignore)"""
    base = os.path.basename(path)
    return os.path.splitext(base)[1] or base


def lexer_of(path):
    return LEXER.get(suffix_of(path))


def py_spans(body, lines, off):
    """comments and docstrings of a Python file as (start, end) offsets, found with tokenize and ast (code and string values are not prose)"""
    def at(row, col):
        return off[row - 1] + col
    try:
        tree = ast.parse(body)
        docs = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body \
                    and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) \
                    and isinstance(node.body[0].value.value, str):
                v = node.body[0].value
                # ast columns are UTF-8 byte offsets, tokenize columns are characters
                c0 = len(lines[v.lineno - 1].encode()[:v.col_offset].decode())
                c1 = len(lines[v.end_lineno - 1].encode()[:v.end_col_offset].decode())
                docs.append((at(v.lineno, c0), at(v.end_lineno, c1)))
        out = []
        for tok in tokenize.generate_tokens(io.StringIO(body).readline):
            st, en = at(*tok.start), at(*tok.end)
            if tok.type == tokenize.COMMENT:
                out.append((st, en))
            elif tok.type == tokenize.STRING and any(d0 <= st and en <= d1 for d0, d1 in docs):
                out.append((st, en))
        return out
    except (SyntaxError, ValueError, tokenize.TokenError) as e:
        raise LexError("python: %s" % e)


def c_spans(body):
    """// and /* */ comments of C and C++ (with string, char and raw string literals and digit separators skipped)"""
    n, i, out = len(body), 0, []
    while i < n:
        ch = body[i]
        nx = body[i + 1] if i + 1 < n else ""
        if ch == "/" and nx == "/":
            j = i
            while j < n and body[j] != "\n":
                j += 2 if body[j] == "\\" and j + 1 < n else 1   # a backslash continues a line comment
            out.append((i, min(j, n)))
            i = j
        elif ch == "/" and nx == "*":
            j = body.find("*/", i + 2)
            if j < 0:
                raise LexError("c: unterminated /* comment")
            out.append((i, j + 2))
            i = j + 2
        elif ch == '"' or ch == "'":
            raw = ch == '"' and i > 0 and body[i - 1] == "R" and (i < 2 or body[i - 2] in "uUL8" or not (body[i - 2].isalnum() or body[i - 2] == "_"))
            if raw:
                m = re.compile(r'"([^()\\ \t\n]{0,16})\(').match(body, i)
                if not m:
                    raise LexError("c: bad raw string")
                j = body.find(")" + m.group(1) + '"', m.end())
                if j < 0:
                    raise LexError("c: unterminated raw string")
                i = j + len(m.group(1)) + 2
                continue
            j = i + 1
            while j < n and body[j] != ch and body[j] != "\n":
                j += 2 if body[j] == "\\" else 1
            if j >= n or body[j] != ch:
                raise LexError("c: unterminated %s literal" % ("string" if ch == '"' else "char"))
            i = j + 1
        elif ch.isdigit() and (i == 0 or not (body[i - 1].isalnum() or body[i - 1] == "_")):
            j = i + 1                                             # a pp-number: digit separators are not char literals
            while j < n and (body[j].isalnum() or body[j] in "_." or (body[j] == "'" and j + 1 < n and body[j + 1].isalnum())
                             or (body[j] in "+-" and body[j - 1] in "eEpP")):
                j += 1
            i = j
        else:
            i += 1
    return out


def hash_spans(body, toml, mark="#"):
    """# comments of shell and TOML (MARK ";" with TOML rules: assembler), with quoted strings (and heredoc bodies of shell) skipped"""
    n, i, out = len(body), 0, []
    heredocs = []
    bol = True
    while i < n:
        ch = body[i]
        if ch == "\n":
            i += 1
            bol = True
            for tag, dash in heredocs:                              # the bodies of the heredocs opened on the line just ended
                while i < n:
                    j = body.find("\n", i)
                    j = n if j < 0 else j
                    line = body[i:j]
                    i = min(j + 1, n)
                    if (line.strip() if dash else line) == tag:
                        break
                else:
                    raise LexError("shell: unterminated heredoc %s" % tag)
            heredocs = []
            continue
        if ch == mark and (toml or bol or body[i - 1] in " \t;|&()"):
            j = body.find("\n", i)
            j = n if j < 0 else j
            out.append((i, j))
            i = j
            continue
        bol = bol and ch in " \t"
        if ch == "\\" and not toml:
            i += 2
        elif ch in "'\"":
            if toml and body.startswith(ch * 3, i):
                j = body.find(ch * 3, i + 3)
                if j < 0:
                    raise LexError("toml: unterminated multi-line string")
                i = j + 3
                continue
            j = i + 1
            while j < n and body[j] != ch and not (toml and body[j] == "\n"):
                j += 2 if body[j] == "\\" and ch == '"' else 1
            if j >= n or body[j] != ch:
                raise LexError("%s: unterminated quote" % ("toml" if toml else "shell"))
            i = j + 1
        elif not toml and body.startswith("<<<", i):
            i += 3                                                  # a here-string, not a heredoc
        elif not toml and body.startswith("<<", i):
            m = re.compile(r"<<(-?)[ \t]*(?:'([^'\n]+)'|\"([^\"\n]+)\"|\\?([A-Za-z_][A-Za-z_0-9]*))").match(body, i)
            if m:
                heredocs.append((m.group(2) or m.group(3) or m.group(4), m.group(1) == "-"))
                i = m.end()
            else:
                i += 2
        else:
            i += 1
    return out


def md_spans(body):
    """the text of a Markdown file outside fenced code blocks (the inline code spans are dropped for every lexer by strip_code)"""
    out, pos, fence, start = [], 0, None, 0
    for line in body.split("\n"):
        end = pos + len(line)
        m = re.match(r" {0,3}(`{3,}|~{3,})", line)
        if fence is None:
            if m:
                if start < pos:
                    out.append((start, pos))
                fence = (m.group(1)[0], len(m.group(1)))
        elif m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] and line.strip() == m.group(1):
            fence = None
            start = end + 1
        pos = end + 1
    if fence is None and start < len(body):
        out.append((start, len(body)))
    return out


def strip_code(body, spans):
    """the prose spans of one file: the comment spans of one run (only whitespace and at most one newline between) are one text; in every
    text the backticked `...` spans are dropped, paragraph by paragraph. -> list of (start, end) offsets that remain prose"""
    merged = []
    for s, e in sorted(spans):
        gap = body[merged[-1][1]:s] if merged else None
        if merged and not gap.strip() and gap.count("\n") <= 1:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    keep = []
    for s, e in merged:
        p = s
        for para in re.split(r"(\n[ \t]*\n)", body[s:e]):
            q = p + len(para)
            a = p
            for m in CODE_SPAN.finditer(para):
                keep.append((a, p + m.start()))
                a = p + m.end()
            keep.append((a, q))
            p = q
    return keep


def prose_lines(path, body):
    """the lines of a file with everything that is not prose replaced by spaces (a mention is a statement in prose). Prose is defined by one
    lexer per file kind (LEXER, by extension): python = COMMENT tokens and docstring STRING tokens; c = // and /* */ comments; markdown = the
    text outside fenced code; shell and toml = # comments; data = none. In every prose span the backticked spans are dropped.
    A suffix not in the table or a file the lexer cannot read raises LexError."""
    kind = lexer_of(path)
    if kind is None:
        raise LexError("the extension %r has no lexer: add it to LEXER" % suffix_of(path))
    lines = body.split("\n")
    off, o = [], 0
    for ln in lines:
        off.append(o)
        o += len(ln) + 1
    if kind == "data":
        return [" " * len(ln) for ln in lines]
    spans = {"python": lambda: py_spans(body, lines, off), "c": lambda: c_spans(body), "markdown": lambda: md_spans(body),
             "shell": lambda: hash_spans(body, False), "toml": lambda: hash_spans(body, True), "semi": lambda: hash_spans(body, True, ";")}[kind]()
    keep = bytearray(len(body))
    for s, e in strip_code(body, spans):
        keep[s:e] = b"\x01" * (e - s)
    return "".join(ch if keep[k] or ch == "\n" else " " for k, ch in enumerate(body)).split("\n")


def blocks(path, body):
    """-> [(first line number, raw text, prose text)] of the blocks of one file (see the docstring)"""
    lines = body.split("\n")
    pl = prose_lines(path, body)
    out, cur, start = [], [], 0

    def flush():
        if cur:
            out.append((start, "\n".join(lines[i] for i in cur), "\n".join(pl[i] for i in cur)))
            del cur[:]
    md = path.endswith(".md")
    for i, ln in enumerate(lines):
        if ln.strip() == "":
            flush()
            continue
        if md and cur and (ITEM.match(ln) or ROW.match(ln)):
            flush()
        if not cur:
            start = i + 1
        cur.append(i)
    flush()
    return out


def is_text(raw):
    if b"\0" in raw:
        return False
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def judge_mentions(files, ids, read):
    """files: scanned paths; ids: registry ids; read(path) -> text. -> (mentions [(path, line)], bad {(path, line): reason})"""
    mentions, bad = [], {}
    for f in files:
        try:
            bl = blocks(f, read(f))
        except LexError as e:
            mentions.append((f, 0))
            bad[(f, 0)] = finding("ext-unknown" if lexer_of(f) is None else "lex-error", "%s:0" % f, "%s: %s" % (f, e))
            continue
        for line, text, prose in bl:
            if not WORD.search(prose):
                continue
            mentions.append((f, line))
            found = ["ASM-" + n for n in ID.findall(text)]
            if not found:
                bad[(f, line)] = finding("mention-no-id", "%s:%d" % (f, line), "the block mentions an assumption and cites no ASM-NNN id")
            else:
                unknown = sorted(set(x for x in found if x not in ids))
                if unknown:
                    bad[(f, line)] = finding("mention-unknown-id", "%s:%d" % (f, line), "id not in the registry: " + ", ".join(unknown))
    return mentions, bad


def owner_items(text):
    """the numbered items under the line that holds OWNER_HEAD in the owner's file -> [item text]"""
    lines = text.split("\n")
    at = [i for i, ln in enumerate(lines) if OWNER_HEAD in ln]
    if len(at) != 1:
        return []
    items, indent = [], None
    for ln in lines[at[0] + 1:]:
        m = NUMBERED.match(ln)
        if m and (indent is None or len(m.group(1)) == indent):
            indent = len(m.group(1))
            items.append(ln)
        elif ln.strip() == "":
            continue
        elif indent is not None and len(ln) - len(ln.lstrip()) > indent and items:
            items[-1] += "\n" + ln
        elif items:
            break
    return items


def ranges_errors(rg):
    """`ranges` is {LEAD: [first, last]} for exactly the LEADS, integers with FIRST_RANGED <= first <= last, pairwise disjoint -> [message]"""
    if not isinstance(rg, dict) or set(rg) != set(LEADS):
        return ["ranges is not an object with exactly the keys %s" % ", ".join(LEADS)]
    out = []
    for k in LEADS:
        v = rg[k]
        if not (isinstance(v, list) and len(v) == 2 and all(type(x) is int for x in v) and FIRST_RANGED <= v[0] <= v[1]):
            out.append("ranges[%s] is not [first, last] with %d <= first <= last" % (k, FIRST_RANGED))
    if not out:
        for a in LEADS:
            for b in LEADS:
                if a < b and rg[a][0] <= rg[b][1] and rg[b][0] <= rg[a][1]:
                    out.append("ranges of %s and %s overlap" % (a, b))
    return out


def retired_of(rl):
    """`retired` is a list of {"id": ASM-NNN, "aliases": [str]}: records that are no longer entries, with every alias they had. -> ({id: [aliases]}, [finding])"""
    out, err = {}, []
    if not isinstance(rl, list):
        return {}, [finding("retired-form", "retired", "retired is not a list")]
    for r in rl:
        if not (isinstance(r, dict) and set(r) == {"id", "aliases"} and isinstance(r["id"], str) and ID_FORM.match(r["id"])
                and isinstance(r["aliases"], list) and all(isinstance(a, str) and a.strip() for a in r["aliases"])):
            err.append(finding("retired-form", "retired", "a retired item is not {id ASM-NNN, aliases [str]}: %r" % (r,)))
        elif r["id"] in out:
            err.append(finding("retired-form", r["id"], "id is retired twice"))
        else:
            out[r["id"]] = list(r["aliases"])
    return out, err


def judge_retired(files, retired, read):
    """A retired id or alias occurring in any scanned file is red: the citation moves to the live record, and a record cannot be removed
    while anything cites it. -> [finding]"""
    out = []
    for f in files:
        t = read(f)
        for i, al in sorted(retired.items()):
            for tok in [i] + al:
                if tok in t:
                    out.append(finding("retired-cited", f, "%s cites %r of the retired record %s" % (f, tok, i)))
    return out


def ratchet_errors(base, new):
    """The merge ratchet: every id and alias that the BASE registry held (entry or retired) is in the new registry, live or retired. A record
    or an alias cannot vanish; to remove one it is listed in `retired`, and then the tree may not cite it. -> [finding]"""
    out = []
    def held(d):
        ids, als = set(), set()
        if isinstance(d, dict):
            for e in d.get("entries", []):
                if isinstance(e, dict):
                    ids.add(e.get("id"))
                    als.update(a for a in e.get("aliases", []) if isinstance(a, str))
            for r in d.get("retired", []):
                if isinstance(r, dict):
                    ids.add(r.get("id"))
                    als.update(a for a in r.get("aliases", []) if isinstance(a, str))
        return ids, als
    bi, ba = held(base)
    ni, na = held(new)
    for i in sorted(x for x in bi - ni if isinstance(x, str)):
        out.append(finding("ratchet-id", i, "%s was held by the base registry and is neither an entry nor retired now" % i))
    for a in sorted(ba - na):
        out.append(finding("ratchet-alias", a, "alias %r was held by the base registry and is neither an alias nor retired now" % a))
    return out


def judge_registry(doc, tracked, read, gate_names):
    """-> ([error str], counts); pure function of its arguments"""
    err = []
    if not isinstance(doc, dict) or set(doc) != {"format", "ranges", "retired", "entries"} or doc["format"] != FORMAT \
            or not isinstance(doc["entries"], list):
        return [finding("registry-form", "registry", "registry is missing or is not %s with keys format, ranges, retired, entries" % FORMAT)], {}
    rg = doc["ranges"]
    rerr = ranges_errors(rg)
    if rerr:
        return [finding("ranges-form", "ranges", m) for m in rerr], {}
    ids, aliases = set(), {}
    retired, rerr2 = retired_of(doc["retired"])
    err += rerr2
    count = {"OWNER": 0, "REMOVE": {}, "no gate yet": 0, "planned": 0, "OPEN": 0,
             "entries": len(doc["entries"])}
    items = owner_items(read(OWNER_FILE)) if OWNER_FILE in tracked else []
    taken = {}
    for e in doc["entries"]:
        if not isinstance(e, dict):
            err.append(finding("entry-form", "registry", "an entry is not an object"))
            continue
        i = e.get("id")
        if not isinstance(i, str) or not ID_FORM.match(i):
            err.append(finding("id-form", str(i), "entry id %r is not ASM-NNN" % (i,)))
            continue
        w = "%s: " % i
        if i in retired:
            err.append(finding("id-retired-reused", i, w + "id is retired: an id is never reused"))
        if i in ids:
            err.append(finding("id-repeated", i, w + "id is repeated"))
        ids.add(i)
        st = e.get("status")
        if st not in STATUSES:
            err.append(finding("status", i, w + "status is not one of %s" % ", ".join(STATUSES)))
            continue
        want = {"id", "text", "used_by", "aliases", "status"} | ({"decision"} if st == "OWNER" else {"removed_by", "owner"})
        extra = set(e) - want
        extra.discard("note")
        if set(want) - set(e) or extra:
            err.append(finding("keys", i, w + "keys are not %s (optional: note)" % ", ".join(sorted(want))))
            continue
        if "note" in e and (not isinstance(e["note"], str) or not e["note"].strip()):
            err.append(finding("note-empty", i, w + "note is empty"))
        if not isinstance(e["text"], str) or not e["text"].strip():
            err.append(finding("text-empty", i, w + "text is empty"))
        al = e["aliases"]
        if not isinstance(al, list) or not all(isinstance(a, str) and a.strip() for a in al):
            err.append(finding("aliases-form", i, w + "aliases is not a list of strings"))
        else:
            for a in al:
                if a in {x for r in retired.values() for x in r}:
                    err.append(finding("alias-retired-reused", i, w + "alias %r is retired" % a))
                if a in aliases:
                    err.append(finding("alias-claimed", i, w + "alias %r is also an alias of %s" % (a, aliases[a])))
                aliases[a] = i
        ub = e["used_by"]
        if not isinstance(ub, list) or not ub or not all(isinstance(p, str) for p in ub):
            err.append(finding("used-by-empty", i, w + "used_by is empty (a dead entry)"))
        else:
            for p in ub:
                if p in tracked:
                    if i not in set("ASM-" + n for n in ID.findall(read(p))):
                        err.append(finding("used-by-cites", i, w + "used_by %s does not cite %s" % (p, i)))
                else:
                    err.append(finding("used-by-untracked", i, w + "used_by %r is not a tracked file" % (p,)))
        if st == "OWNER":
            count["OWNER"] += 1
            d = e["decision"]
            if not isinstance(d, dict) or set(d) != {"file", "quote"} or d["file"] != OWNER_FILE \
                    or not isinstance(d["quote"], str) or not d["quote"].strip():
                err.append(finding("decision-form", i, w + "decision is not {file %s, quote}" % OWNER_FILE))
            elif d["file"] not in tracked or d["quote"] not in read(d["file"]):
                err.append(finding("decision-quote", i, w + "decision.quote does not occur verbatim in %s" % d["file"]))
            else:
                hit = [k for k, it in enumerate(items) if d["quote"] in it]
                if len(hit) != 1:
                    err.append(finding("decision-item", i, w + "decision.quote is not inside exactly one numbered item under %r" % OWNER_HEAD))
                elif hit[0] in taken:
                    err.append(finding("decision-taken", i, w + "item %d of the owner's list is already the decision of %s" % (hit[0] + 1, taken[hit[0]])))
                else:
                    taken[hit[0]] = i
        else:
            ow = e["owner"]
            parts = ow.split("+") if isinstance(ow, str) else []
            if not parts or len(set(parts)) != len(parts) or any(x not in LEADS for x in parts):
                err.append(finding("owner-form", i, w + "owner %r is not LEAD or LEAD+LEAD of %s" % (ow, ", ".join(LEADS))))
            else:
                count["REMOVE"][ow] = count["REMOVE"].get(ow, 0) + 1
                n = int(i[4:])
                lead = parts[0]          # a jointly owned record is in the range of its first-named owner
                lo, hi = rg[lead]
                if n >= FIRST_RANGED and not lo <= n <= hi:
                    err.append(finding("id-range", i, w + "id is not in the range of its owner %s (%d..%d)" % (lead, lo, hi)))
            rb = e["removed_by"]
            ok = False
            if isinstance(rb, str):
                if rb.startswith("gate:") or rb.startswith("fact:"):
                    exists = rb[5:] in (gate_names if rb[0] == "g" else tracked)
                    ok = exists
                else:
                    for form in ("planned:", "no gate yet:", "OPEN:"):
                        if rb.startswith(form) and rb[len(form):].strip():
                            ok = True
                            count[form[:-1]] += 1
            if not ok:
                err.append(finding("removed-by-form", i, w + "removed_by %r is not gate:NAME of a verify/gates.d file, fact:PATH, planned: TEXT, no gate yet: TEXT or OPEN: TEXT" % (rb,)))
    for k in range(len(items)):
        if k not in taken:
            err.append(finding("owner-item-missing", "CLAUDE.md:%d" % (k + 1), "item %d of the owner's list has no OWNER entry" % (k + 1)))
    if count["OWNER"] != len(items):
        err.append(finding("owner-count", "registry", "OWNER entries are %d, the owner's list has %d items" % (count["OWNER"], len(items))))
    return err, count


def judge_stray(files, ids, read):
    """every id written in any tracked text file is an id of the registry -> {path: [ids]}"""
    bad = {}
    for f in files:
        extra = sorted(set("ASM-" + n for n in ID.findall(read(f))) - ids)
        if extra:
            bad[f] = extra
    return bad


def pairs_unique(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise ValueError("key %r repeated" % k)
        d[k] = v
    return d


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh, object_pairs_hook=pairs_unique)
    except (OSError, ValueError):
        return None


PRINT_FINDINGS = 20


def write_findings(allf):
    """Every finding, uncapped and sorted by (code, subject, text), with the total per code, as JSON in the gate's detail directory: GATE_DETAIL
    when the verdict gives one (it outlives the gate's temporary GATE_OUT and is kept beside the stored verdict), else GATE_OUT. Returns the path."""
    d = os.environ.get("GATE_DETAIL") or os.environ["GATE_OUT"]
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, GATE + ".findings.json")
    counts = {}
    for x in allf:
        counts[x.code] = counts.get(x.code, 0) + 1
    with open(path, "w") as fh:
        json.dump({"format": "fnvnx-findings/1", "gate": GATE, "total": len(allf), "counts": counts,
                   "findings": [{"code": x.code, "id": x.subject, "text": str(x)} for x in allf]}, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return path


def main():
    out = os.environ["GATE_OUT"]
    ls = subprocess.run(["git", "ls-files", "-z", "--", TREE], capture_output=True, check=True).stdout
    tracked = sorted(p for p in ls.decode("utf-8", "surrogateescape").split("\0") if p)
    admit = load(ADMIT) or {}
    records = [r["path"] for r in admit.get("roots", []) if isinstance(r, dict) and r.get("record") is True
               and isinstance(r.get("path"), str)]
    cache = {}

    def read(p):
        if p not in cache:
            with open(p, "rb") as fh:
                cache[p] = fh.read().decode("utf-8", "replace")
        return cache[p]
    text_files = []
    for p in tracked:
        with open(p, "rb") as fh:
            if is_text(fh.read()):
                text_files.append(p)
    # the owner's file is outside the tree: it is read, not scanned (it holds the owner's words)
    tracked_all = set(tracked) | {"CLAUDE.md"}
    scanned_all = [p for p in text_files if not any(p == r or p.startswith(r + "/") for r in records)]
    scanned = scanned_all
    doc = load(REGISTRY)
    gates = gatesdir.load_dir_or_empty()
    gate_names = set(g.get("name") for g in gates.get("gates", []) if isinstance(g, dict))
    errors, count = judge_registry(doc, tracked_all, read, gate_names)
    ids = set(e["id"] for e in doc["entries"] if isinstance(e, dict) and isinstance(e.get("id"), str)) \
        if isinstance(doc, dict) and isinstance(doc.get("entries"), list) else set()
    retired = retired_of(doc.get("retired"))[0] if isinstance(doc, dict) else {}
    errors += judge_retired([p for p in scanned_all if p != REGISTRY], retired, read)
    mentions, bad = judge_mentions(scanned, ids, read)
    stray = judge_stray(text_files, ids | set(retired), read)
    for f, x in sorted(stray.items()):
        errors.append(finding("stray-id", f, "%s: ids not in the registry: %s" % (f, ", ".join(x))))
    n = len(mentions) - len(bad)
    status = "green" if not bad and not errors else "red"
    rem = count.get("REMOVE", {})
    nogate = count.get("planned", 0) + count.get("no gate yet", 0) + count.get("OPEN", 0)
    detail = "%d of %d mentions cite a registry id; %d entries (%d OWNER, %d REMOVE: %s); " \
        "without an existing gate: %d (planned %d, no gate yet %d, OPEN %d)" % (
            n, len(mentions), count.get("entries", 0), count.get("OWNER", 0), sum(rem.values()),
            ", ".join("%s %d" % kv for kv in sorted(rem.items())), nogate,
            count.get("planned", 0), count.get("no gate yet", 0), count.get("OPEN", 0))
    allf = sorted([e for e in errors] + [bad[k] for k in sorted(bad)], key=lambda x: (x.code, x.subject, str(x)))
    where = write_findings(allf)
    detail += "; %d findings, full list: %s" % (len(allf), where)
    res = {"format": "fnvnx-gate/1", "name": "assumptions", "status": status, "universe": len(mentions), "certified": n,
           "uncertified": ["%s:%d" % k for k in sorted(bad)][:50], "detail": "; ".join(errors[:20] + [detail])[:2000],
           "reasons": {"%s:%d" % k: bad[k] for k in sorted(bad)[:50]}, "errors": errors[:50],
           "entries": count}
    with open(os.path.join(out, "assumptions.json"), "w") as fh:
        json.dump(res, fh, indent=1, sort_keys=True)
        fh.write("\n")
    for x in allf[:PRINT_FINDINGS]:
        print("  %s %s: %s" % (x.code, x.subject, str(x)[:200]))
    if len(allf) > PRINT_FINDINGS:
        print("  ... %d more in %s" % (len(allf) - PRINT_FINDINGS, where))
    print("assumptions: %s %d/%d %s" % (status, n, len(mentions), detail))
    return 0 if status == "green" else 2


if __name__ == "__main__":
    sys.exit(main())
