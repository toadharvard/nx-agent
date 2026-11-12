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
mention is a block whose PROSE matches `assum` or `допущени` at a word start (case-insensitive); the prose of a .py file is
its comments and docstrings (code and string values are not statements), of any other file the whole text. An item of the
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
BLOCKS = {"L3a": 200, "L3b": 300, "L4": 400, "L5": 500, "L6": 600, "L6A": 700, "L2": 800}   # first id of the lead's block of 100
LEADS = ("L1", "L2", "L3a", "L3b", "L4", "L5", "L6", "L6A")
STATUSES = ("OWNER", "REMOVE")
WORD = re.compile(r"(?<!\w)assum|(?<!\w)допущени", re.I)
ID = re.compile(r"\bASM-(\d{3})\b")
ID_FORM = re.compile(r"ASM-\d{3}\Z")
ITEM = re.compile(r"\s*(?:[-*+]|\d+[.)])\s")
ROW = re.compile(r"\s*\|")


def prose_lines(path, body):
    """the lines of a file with everything that is not prose replaced by spaces (a mention is a statement in prose):
    .py: only comments and docstrings remain (code, identifiers and string values are not statements); any other file: whole"""
    lines = body.split("\n")
    if path.endswith(".py"):
        keep = [[False] * len(ln) for ln in lines]
        try:
            for node in ast.walk(ast.parse(body)):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body \
                        and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) \
                        and isinstance(node.body[0].value.value, str):
                    v = node.body[0].value
                    for k in range(v.lineno - 1, v.end_lineno):
                        keep[k] = [True] * len(lines[k])
            for tok in tokenize.generate_tokens(io.StringIO(body).readline):
                if tok.type == tokenize.COMMENT:
                    r, c = tok.start[0] - 1, tok.start[1]
                    keep[r][c:] = [True] * (len(lines[r]) - c)
        except (SyntaxError, ValueError, tokenize.TokenError):
            return lines
        return ["".join(ch if k else " " for ch, k in zip(ln, kp)) for ln, kp in zip(lines, keep)]
    return lines


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
        for line, text, prose in blocks(f, read(f)):
            if not WORD.search(prose):
                continue
            mentions.append((f, line))
            found = ["ASM-" + n for n in ID.findall(text)]
            if not found:
                bad[(f, line)] = "the block mentions an assumption and cites no ASM-NNN id"
            else:
                unknown = sorted(set(x for x in found if x not in ids))
                if unknown:
                    bad[(f, line)] = "id not in the registry: " + ", ".join(unknown)
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


def judge_registry(doc, tracked, read, gate_names):
    """-> ([error str], counts); pure function of its arguments"""
    err = []
    if not isinstance(doc, dict) or set(doc) != {"format", "entries"} or doc["format"] != FORMAT \
            or not isinstance(doc["entries"], list):
        return ["registry is missing or is not %s with keys format, entries" % FORMAT], {}
    ids, aliases = set(), {}
    count = {"OWNER": 0, "REMOVE": {}, "no gate yet": 0, "planned": 0, "OPEN": 0,
             "entries": len(doc["entries"])}
    items = owner_items(read(OWNER_FILE)) if OWNER_FILE in tracked else []
    taken = {}
    for e in doc["entries"]:
        if not isinstance(e, dict):
            err.append("an entry is not an object")
            continue
        i = e.get("id")
        if not isinstance(i, str) or not ID_FORM.match(i):
            err.append("entry id %r is not ASM-NNN" % (i,))
            continue
        w = "%s: " % i
        if i in ids:
            err.append(w + "id is repeated")
        ids.add(i)
        st = e.get("status")
        if st not in STATUSES:
            err.append(w + "status is not one of %s" % ", ".join(STATUSES))
            continue
        want = {"id", "text", "used_by", "aliases", "status"} | ({"decision"} if st == "OWNER" else {"removed_by", "owner"})
        extra = set(e) - want
        extra.discard("note")
        if set(want) - set(e) or extra:
            err.append(w + "keys are not %s (optional: note)" % ", ".join(sorted(want)))
            continue
        if "note" in e and (not isinstance(e["note"], str) or not e["note"].strip()):
            err.append(w + "note is empty")
        if not isinstance(e["text"], str) or not e["text"].strip():
            err.append(w + "text is empty")
        al = e["aliases"]
        if not isinstance(al, list) or not all(isinstance(a, str) and a.strip() for a in al):
            err.append(w + "aliases is not a list of strings")
        else:
            for a in al:
                if a in aliases:
                    err.append(w + "alias %r is also an alias of %s" % (a, aliases[a]))
                aliases[a] = i
        ub = e["used_by"]
        if not isinstance(ub, list) or not ub or not all(isinstance(p, str) for p in ub):
            err.append(w + "used_by is empty (a dead entry)")
        else:
            for p in ub:
                if p in tracked:
                    if i not in set("ASM-" + n for n in ID.findall(read(p))):
                        err.append(w + "used_by %s does not cite %s" % (p, i))
                else:
                    err.append(w + "used_by %r is not a tracked file" % (p,))
        if st == "OWNER":
            count["OWNER"] += 1
            d = e["decision"]
            if not isinstance(d, dict) or set(d) != {"file", "quote"} or d["file"] != OWNER_FILE \
                    or not isinstance(d["quote"], str) or not d["quote"].strip():
                err.append(w + "decision is not {file %s, quote}" % OWNER_FILE)
            elif d["file"] not in tracked or d["quote"] not in read(d["file"]):
                err.append(w + "decision.quote does not occur verbatim in %s" % d["file"])
            else:
                hit = [k for k, it in enumerate(items) if d["quote"] in it]
                if len(hit) != 1:
                    err.append(w + "decision.quote is not inside exactly one numbered item under %r" % OWNER_HEAD)
                elif hit[0] in taken:
                    err.append(w + "item %d of the owner's list is already the decision of %s" % (hit[0] + 1, taken[hit[0]]))
                else:
                    taken[hit[0]] = i
        else:
            ow = e["owner"]
            parts = ow.split("+") if isinstance(ow, str) else []
            if not parts or len(set(parts)) != len(parts) or any(x not in LEADS for x in parts):
                err.append(w + "owner %r is not LEAD or LEAD+LEAD of %s" % (ow, ", ".join(LEADS)))
            else:
                count["REMOVE"][ow] = count["REMOVE"].get(ow, 0) + 1
                n = int(i[4:])
                lead = parts[0]
                if n >= 200 and not (lead in BLOCKS and BLOCKS[lead] <= n < BLOCKS[lead] + 100):
                    err.append(w + "id is not in the block of its owner %s (%s)" % (
                        lead, "%d..%d" % (BLOCKS[lead], BLOCKS[lead] + 99) if lead in BLOCKS else "L1 has only ids below 200"))
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
                err.append(w + "removed_by %r is not gate:NAME of a verify/gates.d file, fact:PATH, planned: TEXT, no gate yet: TEXT or OPEN: TEXT" % (rb,))
    for k in range(len(items)):
        if k not in taken:
            err.append("item %d of the owner's list has no OWNER entry" % (k + 1))
    if count["OWNER"] != len(items):
        err.append("OWNER entries are %d, the owner's list has %d items" % (count["OWNER"], len(items)))
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
    scanned = [p for p in text_files if not p.endswith(".json")
               and not any(p == r or p.startswith(r + "/") for r in records)]
    doc = load(REGISTRY)
    gates = gatesdir.load_dir_or_empty()
    gate_names = set(g.get("name") for g in gates.get("gates", []) if isinstance(g, dict))
    errors, count = judge_registry(doc, tracked_all, read, gate_names)
    ids = set(e["id"] for e in doc["entries"] if isinstance(e, dict) and isinstance(e.get("id"), str)) \
        if isinstance(doc, dict) and isinstance(doc.get("entries"), list) else set()
    mentions, bad = judge_mentions(scanned, ids, read)
    stray = judge_stray(text_files, ids, read)
    for f, x in sorted(stray.items()):
        errors.append("%s: ids not in the registry: %s" % (f, ", ".join(x)))
    n = len(mentions) - len(bad)
    status = "green" if not bad and not errors else "red"
    rem = count.get("REMOVE", {})
    nogate = count.get("planned", 0) + count.get("no gate yet", 0) + count.get("OPEN", 0)
    detail = "%d of %d mentions cite a registry id; %d entries (%d OWNER, %d REMOVE: %s); " \
        "without an existing gate: %d (planned %d, no gate yet %d, OPEN %d)" % (
            n, len(mentions), count.get("entries", 0), count.get("OWNER", 0), sum(rem.values()),
            ", ".join("%s %d" % kv for kv in sorted(rem.items())), nogate,
            count.get("planned", 0), count.get("no gate yet", 0), count.get("OPEN", 0))
    res = {"format": "fnvnx-gate/1", "name": "assumptions", "status": status, "universe": len(mentions), "certified": n,
           "uncertified": ["%s:%d" % k for k in sorted(bad)][:50], "detail": "; ".join(errors[:20] + [detail])[:2000],
           "reasons": {"%s:%d" % k: bad[k] for k in sorted(bad)[:50]}, "errors": errors[:50],
           "entries": count}
    with open(os.path.join(out, "assumptions.json"), "w") as fh:
        json.dump(res, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print("assumptions: %s %d/%d %s" % (status, n, len(mentions), detail))
    return 0 if status == "green" else 2


if __name__ == "__main__":
    sys.exit(main())
