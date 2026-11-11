#!/usr/bin/env python3
"""nostub: token-level static lint. Everything unfinished must fail at build time, never at run time. stdlib only.
    python3 -I nostub.py check [--terminator NAME]... [--json OUT] DIR...      exit 0 iff no findings, 2 otherwise, 1 usage
Output: `file:line: RULE token` sorted, then a count per rule. Universe: every regular file below the DIRs named *.c *.h *.cc
*.cpp *.cxx *.hpp *.inc *.s *.S *.py *.sh *.ld *.mk Makefile *.cmake CMakeLists.txt (no exclusions). Lexing is exact: C/C++,
assembly and linker scripts share one regex lexer; Python uses tokenize+ast; shell/make/cmake a quote- and comment-aware
segmenter. A file that cannot be lexed has the finding `unlexable`.
Per rule, the class it closes / its blind spot:
 R1 trap and kill primitives as code identifiers (abort family, trap builtins, raise(SIGABRT|SIGTRAP), terminate, svcBreak,
    fatalThrow, brk/udf/hlt in asm strings and .s/.S files, assert with a constant-false first argument). Closes: a missing
    path that dies at run time. Blind: a trap reached by pointer or by a macro-pasted name. Process exit: --terminator NAME
    declares function names (file scope; namespace and extern "C" are transparent) inside whose bodies _Exit/exit/svcExitProcess
    are the exit contract and not counted; only _Exit is otherwise a finding. JSON "terminators" = tokens each one covered.
 R2 not-supported result codes as identifiers (E_NOTIMPL family, ENOSYS; Python: raise NotImplementedError/NotImplemented).
    Closes: a body that answers with an error. Blind: the same value written as a number.
 R3 weak/alias linkage (attribute, pragma weak, .weak, PROVIDE) and linker options accepting undefined symbols, in code and
    strings. Closes: a missing body that still links. Blind: [[gnu::weak]], options assembled at run time.
 R4 swallowed failures: catch(...) without throw; fault-signal handler plus longjmp in one file; Python bare or broad except
    without raise, and bodies of only pass / ... / a docstring; shell `|| true`, `|| :`, `set +e`. Blind: a handler that logs
    and goes on is seen only in catch(...) form; a shell heredoc body is lexed as code.
 R5 `#if 0` / `#if false`, and unfinished-work words (table MARKERS) in comments and strings (R5) and in identifier parts
    split at `_` and camelCase (R5i). Blind: misspelt words; assembly `@` comments are code to the lexer.
 R6 empty C/C++ function bodies `) {}` (R6, lambdas included), empty constructor bodies with member-init `) : a(b) {}` (R6c),
    bodies of only `return CONST;` with CONST a literal, ALL_CAPS or kName (R6k). Control statements are not functions.
    Blind: `(void)x; return 0;`, trailing return types, brace-init members: an object-level rule must cover those."""
import ast, io, json
import os
import re
import sys
import tokenize
from collections import namedtuple

def _u(s):
    return s.replace("|", "")    # policy words carry a `|` inside so that this file does not trip its own rules

# Reason: trap and process-kill primitives; a path that reaches one is unfinished.
R1_NAMES = frozenset("abort _abort __builtin_trap __builtin_unreachable __builtin_abort _Exit quick_exit __assert_fail "
                     "__assert_func svcBreak fatalThrow diagAbortWithResult".split())
# Reason: exit primitives the declared process-exit terminators may use (counted there).
EXIT_NAMES = frozenset(("_Exit", "exit", "svcExitProcess"))
# Reason: the trap instructions of the target architectures.
TRAPS = frozenset(("brk", "udf", "hlt"))
# Reason: result codes whose only meaning is that the callee is not written.
R2_NAMES = frozenset(_u(x) for x in "E_NOTIMPL ERROR_CALL_NOT_IMPL|EMENTED ERROR_NOT_SUPPORTED STATUS_NOT_IMPL|EMENTED ENOSYS".split())
R2_PY = frozenset(("NotImplementedError", "NotImplemented"))
# Reason: linkage that lets a missing body link.
R3_ATTR = frozenset(("weak", "__weak__", "alias", "__alias__"))
R3_DIRECT = frozenset((".weak", ".weak_definition", "PROVIDE", "PROVIDE_HIDDEN"))
# Reason: linker options that turn unresolved symbols into success.
R3_OPTS = re.compile("|".join(re.escape(_u(s)) for s in ("--unresolved|-symbols", "--allow-shlib|-undefined",
                                                       "--noinhibit|-exec", "--defs|ym")) + r"|-z[\s,=]?undefs\b")
# Reason: signals whose handler plus longjmp resumes after a fault.
FAULT = frozenset(("SIGSEGV", "SIGBUS", "SIGILL", "SIGFPE"))
# Reason: words that announce unfinished work.
MARKERS = tuple(tuple(re.findall(r"[a-z0-9]+", _u(m).lower())) for m in (
    "TO|DO", "FIX|ME", "XX|X", "HA|CK", "ST|UB", "ST|UBBED", "PLACE|HOLDER", "NO|T IMPLEMENTED", "NO|T YET IMPLEMENTED",
    "UN|IMPLEMENTED", "STAN|D-IN", "STAN|DIN", "WORK|AROUND", "FO|R NOW", "TEMPO|RARY", "FALL|BACK"))
# Reason: `(...) {}` after these keywords is control flow, not a function body.
CONTROL = frozenset("if while for switch catch sizeof decltype alignof static_assert".split())
SUFFIX = (".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".inc", ".s", ".S", ".py", ".sh", ".ld", ".mk", ".cmake")

T = namedtuple("T", "k s ln pp i")   # kind (id num str chr op com), text, line, 0 / 1 = `#` of a directive / 2 = rest, index
S0 = T("op", "", 0, 0, -1)    # sentinel for a missing neighbour

class Unlexable(Exception): pass    # a lexer's answer for text it cannot lex exactly

_COMMON = r"""(?P<ws>[ \t\r\f\v]+|\\\r?\n)|(?P<nl>\n)|(?P<lc>//(?:\\\r?\n|[^\n])*)|(?P<bc>/\*.*?\*/)
|(?P<str>"(?:\\.|[^"\\\n])*")%s|(?P<num>\.?\d(?:[eEpP][+-]|'\w|[\w.])*)|(?P<id>%s)|(?P<bad>/\*|"%s)|(?P<op>\.\.\.|::|->|.)"""
_C = re.compile(_COMMON % (r"""|(?P<raw>(?:u8|u|U|L)?R"(?P<d>[^ ()\\\t\n]{0,16})\(.*?\)(?P=d)")|(?P<chr>'(?:\\.|[^'\\\n])*')""",
                            r"[A-Za-z_$\x80-\U0010ffff][\w$\x80-\U0010ffff]*", "|'"), re.S | re.X)
_A = re.compile(_COMMON % ("", r"[.A-Za-z_$\x80-\U0010ffff][\w$.\x80-\U0010ffff]*", ""), re.S | re.X)
_DIR = re.compile(r"[ \t]*(if|ifdef|ifndef|else|elif|endif|define|undef|include|pragma|error|warning|line)\b")

def lex_c(src, asm=False):
    rx, out, i, line, bol, pp, nm = _A if asm else _C, [], 0, 1, True, 0, ""
    while i < len(src):
        m = rx.match(src, i)
        k, s = m.lastgroup, m.group()
        k = "str" if k in ("raw", "d") else k
        if k == "bad":
            raise Unlexable(s)
        i = m.end()
        if k == "nl":
            line, bol, pp, nm = line + 1, True, 0, ""
        elif k == "ws":
            line += s.count("\n")
        elif k in ("lc", "bc"):
            out.append(T("com", s, line, pp, len(out)))
            line += s.count("\n")
        else:
            if s == "#" and bol:
                d = _DIR.match(src, i)
                if asm and not d:
                    j = src.find("\n", i)
                    j = len(src) if j < 0 else j
                    out.append(T("com", src[i - 1:j], line, 0, len(out)))
                    i = j
                    continue
                out.append(T("op", s, line, 1, len(out)))
                pp, nm = 2, d.group(1) if d else ""
                if d and nm in ("error", "warning"):    # free text to the end of the logical line
                    j = re.compile(r"(?:\\\n|[^\n])*").match(src, d.end()).end()
                    out.append(T("str", src[d.end():j], line, 2, len(out)))
                    line, i = line + src.count("\n", d.end(), j), j
                elif d:
                    out.append(T("id", nm, line, 2, len(out)))
                    i = d.end()
                bol = False
                continue
            if not (pp == 2 and nm in ("include", "include_next", "import")):
                out.append(T(k, s, line, pp, len(out)))
            line, bol = line + s.count("\n"), False
    return out

def _match(c):
    """Bracket pairs of a token list: {open index: close index}, and the brace depth per token (namespace/extern "C" free)."""
    st, mt, depth, d = [], {}, [], 0
    for j, t in enumerate(c):
        if t.k == "op" and t.s in ("(", "[", "{"):
            tr = t.s == "{" and ((j and c[j - 1].s == "namespace") or (j > 1 and c[j - 2].s == "namespace") or
                                 (j > 1 and c[j - 1].k == "str" and c[j - 2].s == "extern"))
            st.append((t.s, j, tr))
            d += t.s == "{" and not tr
        elif t.k == "op" and t.s in (")", "]", "}") and st and st[-1][0] + t.s in ("()", "[]", "{}"):
            o, oj, tr = st.pop()
            mt[oj] = j
            d -= o == "{" and not tr
        depth.append(d)
    return mt, depth

def _marks(text):
    ws = [(m.group().lower(), m.start()) for m in re.finditer(r"[A-Za-z0-9]+", text)]
    return [(p, " ".join(mk).upper()) for k, (w, p) in enumerate(ws) for mk in MARKERS
            if mk[0] == w and len(ws) - k >= len(mk) and all(ws[k + d][0] == mk[d] for d in range(len(mk)))]

def _text_rules(kind, text, ln, F):
    """R5 on a comment/string text, R3 option strings on a string or code text."""
    txt = re.sub(r"\\[a-zA-Z]", " ", text) if kind == "str" else text
    if kind != "code":
        F += [(ln + txt.count("\n", 0, p), "R5", m) for p, m in _marks(txt)]
    if kind != "com":
        F += [(ln + text.count("\n", 0, m.start()), "R3", m.group()) for m in R3_OPTS.finditer(text)]

def _ident_rules(s, ln, F):
    F += [(ln, "R5i", m) for m in {m for _, m in _marks(" ".join(re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+", s)))}]
    if s in R2_NAMES:
        F.append((ln, "R2", s))

def generic(ts, cover=None):
    """Rules on identifier/string/comment tokens, shared by every token language. cover(i) -> terminator name or None."""
    F, used = [], {}
    for q, t in enumerate(ts):
        p, n, n2 = (ts[q - 1] if q else S0), (ts[q + 1] if q + 1 < len(ts) else S0), (ts[q + 2] if q + 2 < len(ts) else S0)
        if t.k in ("com", "str"):
            _text_rules(t.k, t.s, t.ln, F)
        elif t.k == "id":
            _ident_rules(t.s, t.ln, F)
            s, tn = t.s, cover(t.i) if cover else None
            if s in EXIT_NAMES and tn:
                used[tn] = used.get(tn, 0) + 1
            elif (s in R1_NAMES or (s == "raise" and n.s == "(" and n2.s in ("SIGABRT", "SIGTRAP")) or
                  (s == "terminate" and ((n.s == "(" and p.s not in (".", "->")) or (p.s == "::" and ts[q - 2].s == "std"))) or
                  (s == "assert" and n.s == "(" and (n2.s in ("0", "false") or n2.k == "str"))):
                F.append((t.ln, "R1", s))
    return F, used

def _group(nc, q, cap=300):
    """Tokens after nc[q] up to the paren group that opens right after it closes (or the statement ends)."""
    d, out = 0, []
    for t in nc[q + 1:q + cap]:
        d += (t.s == "(") - (t.s == ")")
        if t.s == ";" or (d == 0 and t.s == "{"):
            break
        out.append(t)
        if d == 0 and out and t.s == ")":
            break
    return out

def c_rules(toks, terms, ext):
    nc = [t for t in toks if t.k != "com"]
    c = [t for t in nc if not t.pp]
    mt, depth = _match(c)
    rev = {v: k for k, v in mt.items()}
    ranges = []
    for j, t in enumerate(c[:-1]):
        if t.s in terms and t.k == "id" and c[j + 1].s == "(" and depth[j] == 0 and j + 1 in mt:
            k = mt[j + 1] + 1
            while k < len(c) and c[k].s not in ("{", ";"):
                k += 1
            if k in mt:
                ranges.append((t.s, c[k].i, c[mt[k]].i))
    F, used = generic(toks, lambda i: next((n for n, lo, hi in ranges if lo <= i <= hi), None))
    for q, t in enumerate(nc):
        p, n = (nc[q - 1] if q else S0), (nc[q + 1] if q + 1 < len(nc) else S0)
        if t.k == "id" and t.s in R3_DIRECT and (t.s[0] == "." or n.s == "("):
            F.append((t.ln, "R3", t.s))
        elif t.s == "__attribute__":
            F += [(x.ln, "R3", x.s) for x in _group(nc, q) if x.k == "id" and x.s in R3_ATTR]
        elif t.s in ("asm", "__asm__", "__asm"):
            F += [(x.ln, "R1", "asm-trap") for x in _group(nc, q) if x.k == "str" and
                  re.search(r'(?:^"|;|\\[nt])\s*(?:brk|udf|hlt)\b', x.s, re.I)]
        elif ext in (".s", ".S") and t.k == "id" and t.s.lower() in TRAPS and (p.ln != t.ln or p.s in (":", ";")):
            F.append((t.ln, "R1", t.s.lower()))
        if t.pp == 1 and [x.s for x in nc[q + 1:q + 3]] == ["pragma", "weak"]:
            F.append((t.ln, "R3", "#pragma weak"))
        if t.pp == 1 and n.s == "if":
            r = [x.s for x in nc[q + 2:q + 5] if x.ln == t.ln]
            if r[:1] in (["0"], ["false"]) and len(r) == 1 or r[:3] == ["(", "0", ")"] and len(r) == 3:
                F.append((t.ln, "R5", "#if " + r[-1 if len(r) == 1 else 1]))
    for j, t in enumerate(c):
        if t.s == "catch" and [x.s for x in c[j + 1:j + 5]] == ["(", "...", ")", "{"] and j + 4 in mt:
            if not any(x.s == "throw" for x in c[j + 4:mt[j + 4]]):
                F.append((t.ln, "R4", "catch(...)"))
        kind = "R6" if t.s == "{" and mt.get(j) == j + 1 else \
            "R6k" if t.s == "{" and mt.get(j) == j + 4 and c[j + 1].s == "return" and c[j + 3].s == ";" and \
            (c[j + 2].k in ("num", "str", "chr") or re.fullmatch(r"nullptr|NULL|true|false|[A-Z][A-Z0-9_]*|k[A-Z]\w*", c[j + 2].s)) else None
        if kind:
            p = j - 1
            while p >= 0:
                if c[p].s in ("const", "noexcept", "override", "final", "volatile", "&"):
                    p -= 1
                elif c[p].s == ")" and rev.get(p, 0) > 0 and c[rev[p] - 1].s in ("noexcept", "__attribute__", "alignas", "throw"):
                    p = rev[p] - 2
                elif c[p].s == "]" and p > 0 and c[p - 1].s == "]" and rev.get(p, 0) > 0 and c[rev[p] + 1].s == "[":
                    p = rev[p] - 1
                else:
                    break
            o = rev.get(p) if p >= 0 and c[p].s == ")" else None
            nm = c[o - 1] if o else S0
            if o is None or nm.s in CONTROL:
                continue
            if kind == "R6" and nm.k == "id" and o >= 2 and c[o - 2].s in (":", ",") and not (
                    c[o - 2].s == ":" and o >= 3 and c[o - 3].s in ("public", "private", "protected")):
                kind = "R6c"
            F.append((t.ln, kind, "return " + c[j + 2].s if kind == "R6k" else nm.s if nm.k == "id" else "lambda"))
    sigs = [t.ln for k, t in enumerate(c[:-1]) if t.s in ("signal", "sigaction") and c[k + 1].s == "(" and k + 1 in mt and
            any(x.s in FAULT for x in c[k + 1:mt[k + 1]])]
    if sigs and any(t.s in ("longjmp", "siglongjmp") for t in c):
        F += [(ln, "R4", "signal+longjmp") for ln in sigs]
    return F, used

_SH = r"""(?P<com>(?:^|(?<=[ \t\n;|&(]))\#[^\n]*)|(?P<str>"(?:\\.|[^"\\%s])*"|'[^'%s]*')|(?P<code>(?:\\.|[^"'\#\\]|(?<![ \t\n;|&(])\#)+)|(?P<q>.)"""
_SHS, _SHL = re.compile(_SH % ("", ""), re.S | re.M | re.X), re.compile(_SH % ("\\n", "\\n"), re.S | re.M | re.X)

def sh_rules(src, ext):
    """Shell/make/cmake: segments code/str/com; only .sh quotes may span lines."""
    segs, i, line = [], 0, 1
    while i < len(src):
        m = (_SHS if ext == ".sh" else _SHL).match(src, i)
        if m.lastgroup == "q" and ext == ".sh" and m.group() in "'\"":
            raise Unlexable("unterminated quote")
        segs.append(("code" if m.lastgroup == "q" else m.lastgroup, m.group(), line))
        line, i = line + m.group().count("\n"), m.end()
    F = []
    for kind, text, ln in segs:
        _text_rules(kind, text, ln, F)
        if kind == "code":
            for m in re.finditer(r"[A-Za-z_]\w*", text):
                _ident_rules(m.group(), ln + text.count("\n", 0, m.start()), F)
            if ext != ".cmake":
                F += [(ln + text.count("\n", 0, m.start()), "R4", m.group().strip())
                      for m in re.finditer(r"\|\|\s*(?:true|:)(?![\w-])|\bset\s+\+e\b", text)]
    return F

def py_rules(src):
    try:
        tree = ast.parse(src)
        raw = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (SyntaxError, ValueError, tokenize.TokenError) as e:
        raise Unlexable(str(e)[:60])
    kinds = {tokenize.COMMENT: "com", tokenize.STRING: "str", tokenize.NAME: "id", tokenize.NUMBER: "num",
             tokenize.FSTRING_MIDDLE: "str", tokenize.OP: "op"}
    F, _ = generic([T(kinds[x.type], x.string, x.start[0], 0, k) for k, x in enumerate(y for y in raw if y.type in kinds)])
    for nd in ast.walk(tree):
        if isinstance(nd, ast.Raise) and nd.exc is not None:
            e = nd.exc.func if isinstance(nd.exc, ast.Call) else nd.exc
            if isinstance(e, ast.Name) and e.id in R2_PY:
                F.append((nd.lineno, "R2", e.id))
        elif isinstance(nd, ast.ExceptHandler):
            names = [x.id for x in (nd.type.elts if isinstance(nd.type, ast.Tuple) else [nd.type]) if isinstance(x, ast.Name)]
            if (nd.type is None or {"Exception", "BaseException"} & set(names)) and \
                    not any(isinstance(x, ast.Raise) for b in nd.body for x in ast.walk(b)):
                F.append((nd.lineno, "R4", "except " + "/".join(names)))
        elif isinstance(nd, (ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = isinstance(nd.body[0], ast.Expr) and isinstance(nd.body[0].value, ast.Constant) and isinstance(nd.body[0].value.value, str)
            if all(isinstance(x, ast.Pass) or (isinstance(x, ast.Expr) and isinstance(x.value, ast.Constant) and
                                               x.value.value is Ellipsis) for x in nd.body[doc:]):
                F.append((nd.lineno, "R4", "def " + nd.name))
    return F

def scan_text(path, text, terms=()):
    """-> (sorted unique findings [(line, rule, token)], {terminator: covered tokens})."""
    base, ext = os.path.basename(path), os.path.splitext(path)[1]
    ext = {"Makefile": ".mk", "CMakeLists.txt": ".cmake"}.get(base, ext)
    try:
        if ext == ".py":
            F, used = py_rules(text), {}
        elif ext in (".sh", ".mk", ".cmake"):
            F, used = sh_rules(text, ext), {}
        else:
            F, used = c_rules(lex_c(text, ext in (".s", ".S", ".ld")), frozenset(terms), ext)
    except Unlexable:
        return [(1, "unlexable", "-")], {}
    return sorted(set(F)), used

def wanted(name):
    return name.endswith(SUFFIX) or os.path.basename(name) in ("Makefile", "CMakeLists.txt")

def universe(dirs):
    return sorted({os.path.normpath(os.path.join(r, f)) for d in dirs for r, ds, fs in os.walk(d) for f in fs
                   if wanted(f) and os.path.isfile(os.path.join(r, f)) and not os.path.islink(os.path.join(r, f))})

def check(dirs, terms=()):
    files, finds, used = universe(dirs), [], {n: 0 for n in terms}
    for f in files:
        try:
            fs, u = scan_text(f, open(f, "rb").read().decode("utf-8").lstrip("\ufeff"), terms)
        except (UnicodeDecodeError, OSError):
            fs, u = [(1, "unlexable", "-")], {}
        finds += [{"file": f, "line": ln, "rule": r, "token": tk} for ln, r, tk in fs]
        for k, v in u.items():
            used[k] += v
    counts = {r: sum(x["rule"] == r for x in finds) for r in sorted({x["rule"] for x in finds})}
    return {"files": files, "findings": finds, "counts": counts, "terminators": used}

def main(argv):
    terms, js, dirs, it = [], None, [], iter(argv[1:])
    for a in it:
        if a == "--terminator":
            terms.append(next(it, ""))
        elif a == "--json":
            js = next(it, None)
        else:
            dirs.append(a)
    if argv[:1] != ["check"] or not dirs or not all(os.path.isdir(d) for d in dirs):
        print("usage: nostub.py check [--terminator NAME]... [--json OUT] DIR... (existing directories)", file=sys.stderr)
        return 1
    res = check(dirs, sorted(set(terms)))
    for x in res["findings"]:
        print("%s:%d: %s %s" % (x["file"], x["line"], x["rule"], x["token"]))
    print(*("nostub: %s %d" % kv for kv in res["counts"].items()), sep="\n", end="\n" if res["counts"] else "")
    print("nostub: %d files, %d findings" % (len(res["files"]), len(res["findings"])))
    if js:
        with open(js, "w") as fh:
            json.dump(res, fh, sort_keys=True, indent=1)
    return 2 if res["findings"] else 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
