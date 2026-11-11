"""s8_asm.py - prepare rules for MSVC x86 inline assembly (docs/ASM.md).

Rule ASM-Pi: every `__asm` construct of a source or header is kept verbatim for cl and replaced for GCC by one empty
GNU asm statement, so that the N3 plugin sees a GIMPLE_ASM and gives the function the verdict `asm` (its body is
lifted from the exe's bytes). No pattern table, no translation to C. Output form, one per span:

    #ifdef _MSC_VER
    <the pristine logical lines of the span>
    #else
    <the same lines, each construct replaced by  __asm__("");   (+ the marker comment)>
    #endif
    #line <original number of the next line>

Grammar implemented (MSVC "Inline Assembler" syntax, __asm keyword; `_asm` and `asm` are not used by the tree and
are not recognised; the token is looked for only in code, not in comments, string or character literals):
  construct    := block | run
  block        := "__asm" ws* "{" asm-body "}"      the "{" is on the line of "__asm"; the body ends at the matching
                  "}" (nesting counted); inside it nested "__asm" are ordinary body text
  run          := stmt+ ; stmt := "__asm" instruction ; consecutive stmts separated only by white space (newlines and
                  line splices included) form one run, rewritten as one asm statement
  stmt extent  := from "__asm" to the first of: end of the logical line (newline not preceded by a backslash), the
                  next "__asm" token, a "}" (not consumed); a ";" starts an asm comment up to the end of the
                  physical line (a "}" or "__asm" inside it does not count); "//" and "/* */" comments are part of
                  the extent as well
  span         := the logical lines (backslash-newline joins physical lines) that contain constructs; constructs
                  sharing a logical line are one span. A span whose first logical line is a #define is wrapped as a
                  whole: the pristine #define under _MSC_VER, the #define with asm parts replaced under #else (a
                  conditional cannot be placed inside a replacement list). Any other directive line that holds or
                  lies inside a construct cannot be wrapped: ASMError (file:line).
Total by construction: gcc_view() (the text as GCC sees it, _MSC_VER undefined) is checked for `__asm` after the
rewrite; a leftover or an unparsable construct raises ASMError, there is no silent path.

Lexical note: the keyword "__asm" is 5 characters; the white space (and splices) before a "{" or an instruction are
skipped from the character right after it, so "__asm{" is a block like "__asm {" and "__asm\n" ends a statement form
at once (an empty statement), never one character later.
Totality properties (totality_violations(), checked by tests/s8 on every real file with __asm), by the tokens() lexer:
  T1 every construct is brace neutral (its tokens, `;` asm comments removed, have net depth 0 and never go below 0),
     so the pristine text outside its constructs has the brace profile of the pristine text; T2 the cl view of the output has the token stream of the pristine text; T3 the GCC
     view has the token stream of the pristine text with each construct replaced by `__asm__ ( "" ) ;`, so it holds no
     token that exists only inside a construct, and it is brace balanced like the pristine text outside them.

__rdtsc() (ASM4) is not inline asm: calls and the decomp's prototypes stay; GCC gets a compatible declaration
from shim/include/fnvnx_asm.h.
"""
import re


class ASMError(Exception):
    pass


MARK = '__asm__("");   /* fnv-nx ASM: x86 inline assembly; this function\'s body is lifted (N3 verdict asm) */'
TOKEN = re.compile(r'\b__asm\b')
WS = ' \t\r\f\v'


def _lineno(text, pos):
    return text.count('\n', 0, pos) + 1


def _skip_ws_splice(text, i):
    """past white space and backslash-newline"""
    n = len(text)
    while i < n:
        if text[i] in WS: i += 1
        elif text.startswith('\\\n', i): i += 2
        elif text.startswith('\\\r\n', i): i += 3
        else: break
    return i


def _skip_line_comment(text, i):
    """i at '//' or ';': to the physical end of line (position of the newline)"""
    j = text.find('\n', i)
    return len(text) if j < 0 else j


def _skip_block_comment(text, i):
    j = text.find('*/', i + 2)
    return len(text) if j < 0 else j + 2


def _skip_quoted(text, i):
    """i at a quote; closed on the same line, else the quote is a plain character"""
    q = text[i]
    j = i + 1
    n = len(text)
    while j < n and text[j] != '\n':
        if text[j] == '\\': j += 2; continue
        if text[j] == q: return j + 1
        j += 1
    return i + 1


def _scan_code(text, i):
    """one lexical step in C code at i: returns the next index after a comment / literal / char"""
    c = text[i]
    if c == '/' and text.startswith('//', i):
        j = i
        while True:  # a // comment continues over backslash-newline
            j = text.find('\n', j)
            if j < 0: return len(text)
            if text[j - 1] == '\\' or text[j - 2:j] == '\\\r': j += 1; continue
            return j
    if c == '/' and text.startswith('/*', i): return _skip_block_comment(text, i)
    if c in '"\'': return _skip_quoted(text, i)
    return i + 1


def _asm_body_end(text, i, path):
    """i just after '{' of a block: index just after the matching '}'"""
    depth, n, start = 1, len(text), i
    while i < n:
        c = text[i]
        if c == ';' or text.startswith('//', i): i = _skip_line_comment(text, i); continue
        if text.startswith('/*', i): i = _skip_block_comment(text, i); continue
        if c in '"\'': i = _skip_quoted(text, i); continue
        if c == '{': depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0: return i + 1
        i += 1
    raise ASMError('%s:%d: __asm block without closing }' % (path, _lineno(text, start)))


def _stmt_end(text, i):
    """i just after '__asm' of a statement form: end of its extent (white space before the next construct excluded)"""
    n = len(text)
    last = i
    while i < n:
        c = text[i]
        if c == '\n': break
        if text.startswith('\\\n', i): i += 2; continue
        if text.startswith('\\\r\n', i): i += 3; continue
        if c == ';': i = _skip_line_comment(text, i); last = i; continue
        if text.startswith('//', i): i = _skip_line_comment(text, i); last = i; continue
        if text.startswith('/*', i): i = _skip_block_comment(text, i); last = i; continue
        if c in '"\'': i = _skip_quoted(text, i); last = i; continue
        if c == '}': break
        if c == '_' and TOKEN.match(text, i) and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == '_')): break
        i += 1
        if c not in WS: last = i
    return last


def find_constructs(text, path='?'):
    """[(start, end, kind)] kind 'block' | 'run', in order, non-overlapping"""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '_' and TOKEN.match(text, i) and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == '_')):
            j = _skip_ws_splice(text, i + 5)
            if j < n and text[j] == '{':
                end = _asm_body_end(text, j + 1, path)
                out.append((i, end, 'block')); i = end; continue
            end = _stmt_end(text, i + 5)
            while True:  # merge following statements separated by white space only
                k = _skip_ws_splice(text, end)
                while k < n and text[k] == '\n': k = _skip_ws_splice(text, k + 1)
                if TOKEN.match(text, k) and not (k < n and text.startswith('{', _skip_ws_splice(text, k + 5))):
                    end = _stmt_end(text, k + 5)
                else:
                    break
            out.append((i, end, 'run')); i = end; continue
        if c in '/"\'': i = _scan_code(text, i); continue
        i += 1
    return out


def _logical_start(text, pos):
    s = text.rfind('\n', 0, pos) + 1
    while s > 0 and (text[s - 2:s] == '\\\n' or text[s - 3:s] == '\\\r\n'):
        s = text.rfind('\n', 0, s - 1) + 1
    return s


def _logical_end(text, pos):
    """position of the newline ending the logical line that holds pos (or len)"""
    n = len(text)
    while True:
        e = text.find('\n', pos)
        if e < 0: return n
        if text[e - 1] == '\\' or text[e - 2:e] == '\\\r': pos = e + 1; continue
        return e


def _is_directive(line):
    return line.lstrip(' \t').startswith('#')


def rewrite(path, text):
    """-> (new text, {'block': n, 'run': n, 'define': n(spans that are #defines), 'spans': n})"""
    cons = find_constructs(text, path)
    cnt = {'block': 0, 'run': 0, 'define': 0, 'spans': 0}
    if not cons: return text, cnt
    out, pos, k = [], 0, 0
    while k < len(cons):
        s0 = _logical_start(text, cons[k][0])
        e0 = _logical_end(text, cons[k][1] - 1)
        grp = [cons[k]]; k += 1
        while k < len(cons) and cons[k][0] <= e0:  # constructs sharing a logical line
            e0 = _logical_end(text, cons[k][1] - 1)
            grp.append(cons[k]); k += 1
        first = text[s0:text.find('\n', s0) if text.find('\n', s0) >= 0 else len(text)]
        define = bool(re.match(r'[ \t]*#[ \t]*define\b', first))
        if _is_directive(first) and not define:
            raise ASMError('%s:%d: __asm in a preprocessor directive that is not #define' % (path, _lineno(text, s0)))
        span = text[s0:e0]
        if not define:
            for ln in span.split('\n'):
                if _is_directive(ln):
                    raise ASMError('%s:%d: preprocessor directive inside an __asm construct'
                                   % (path, _lineno(text, s0 + span.find(ln))))
        gcc, p = [], s0
        for (a, b, kind) in grp:
            gcc.append(text[p:a]); gcc.append(MARK); p = b
            cnt[kind] += 1
        gcc.append(text[p:e0])
        gtxt = ''.join(gcc)
        if define:
            cnt['define'] += 1
            # the replacement list continues on the same logical line: the marker comment cannot swallow the rest
            gtxt = gtxt.replace(MARK, '__asm__("");')
        nxt = '' if e0 >= len(text) else '\n#line %d' % (_lineno(text, e0) + 1)
        out.append(text[pos:s0])
        out.append('#ifdef _MSC_VER\n%s\n#else\n%s\n#endif%s' % (span, gtxt, nxt))
        pos = e0; cnt['spans'] += 1
    out.append(text[pos:])
    new = ''.join(out)
    left = gcc_leftovers(new)
    if left:
        raise ASMError('%s:%d: __asm still visible to GCC' % (path, left[0]))
    return new, cnt


# ----------------------------------------------------------------------------------------------- GCC view

def gcc_view(text, msc=False):
    """msc=True: the text as cl sees it (the `_MSC_VER` branches kept, `#else` of them and `#ifndef _MSC_VER` dropped).
    The text as GCC sees it for conditionals on _MSC_VER only: `#ifdef _MSC_VER` / `#if defined(_MSC_VER)` branches
    dropped, `#ifndef _MSC_VER` kept; every other conditional keeps all its branches (over-approximation). Dropped
    and comment-only lines are blanked (line numbers kept)."""
    out, stack = [], []  # stack of [kind, active_parent, branch_active]; kind 'msc' | 'nmsc' | 'other'
    def active(): return all(s[2] for s in stack)
    lines = text.split('\n')
    i = 0
    while i < len(lines):
        ln = lines[i]
        m = re.match(r'[ \t]*#[ \t]*(\w+)(.*)', ln)
        if m:
            d, rest = m[1], m[2]
            if d in ('if', 'ifdef', 'ifndef'):
                r = rest.strip()
                if (d == 'ifdef' and r == '_MSC_VER') or (d == 'if' and re.fullmatch(r'defined\s*\(?\s*_MSC_VER\s*\)?', r)):
                    stack.append(['msc', 0, msc])
                elif d == 'ifndef' and r == '_MSC_VER':
                    stack.append(['nmsc', 0, not msc])
                else:
                    stack.append(['other', 0, True])
                out.append(''); i += 1; continue
            if d in ('else', 'elif') and stack:
                st = stack[-1]
                if st[0] == 'msc': st[2] = not msc
                elif st[0] == 'nmsc': st[2] = msc
                out.append(''); i += 1; continue
            if d == 'endif' and stack:
                stack.pop(); out.append(''); i += 1; continue
        out.append(ln if active() else '')
        i += 1
    return '\n'.join(out)


_TOK = re.compile(r'[A-Za-z_$][A-Za-z_0-9$]*|[0-9](?:[eEpP][+-]|[A-Za-z_0-9.\'])*|.', re.S)


def tokens(text):
    """C token stream by the rule's own lexical steps (_scan_code): comments dropped, string / char literals one token,
    backslash-newline spliced away, `#line N` lines dropped; every other character of code is its own token (words and
    numbers whole). Used by the totality properties of the tests."""
    text = re.sub(r'\\\r?\n', '', text)
    text = re.sub(r'(?m)^[ \t]*#[ \t]*line\b.*$', '', text)
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in WS or c == '\n': i += 1; continue
        if c == '/' and text.startswith(('//', '/*'), i): i = _scan_code(text, i); continue
        if c in '"\'': j = _scan_code(text, i); out.append(text[i:j]); i = j; continue
        m = _TOK.match(text, i); out.append(m[0]); i = m.end()
    return out


def brace_profile(toks):
    """(net depth, minimum depth) of the { } tokens"""
    d = lo = 0
    for t in toks:
        if t == '{': d += 1
        elif t == '}': d -= 1; lo = min(lo, d)
    return d, lo


def _strip_asm_comments(c):
    """a construct without its `;` comments (to the end of the physical line, outside quotes)"""
    out, i, n = [], 0, len(c)
    while i < n:
        if c[i] == ';': i = _skip_line_comment(c, i); continue
        if c[i] in '"\'': j = _skip_quoted(c, i); out.append(c[i:j]); i = j; continue
        out.append(c[i]); i += 1
    return ''.join(out)


def totality_violations(path, text, new=None):
    """the list of violated totality properties of the rewrite of `text` (empty = total):
      T1 every construct is brace neutral (its extent neither cut a `}` of the code nor took one);
      T2 the cl view of the rewrite has the token stream of the pristine text;
      T3 the GCC view of the rewrite has the token stream of the pristine text with every construct replaced by
         `__asm__ ( "" ) ;` (so no token that exists only inside a construct is visible to GCC), and is brace balanced
         like the pristine text outside the constructs (T1)."""
    if new is None: new = rewrite(path, text)[0]
    cons = find_constructs(text, path)
    outside, exp, p = [], [], 0
    for (a, b, _k) in cons:
        outside.append(text[p:a]); exp.append(text[p:a]); exp.append('__asm__("");'); p = b
    outside.append(text[p:]); exp.append(text[p:])
    bad = []
    full = tokens(text)
    for (a, b, _k) in cons:
        if brace_profile(tokens(_strip_asm_comments(text[a:b]))) != (0, 0):
            bad.append('T1 construct at line %d not brace neutral' % _lineno(text, a)); break
    if tokens(gcc_view(new, msc=True)) != full: bad.append('T2 cl view differs from pristine')
    g = tokens(gcc_view(new))
    if g != tokens(''.join(exp)): bad.append('T3 GCC view differs from pristine with constructs replaced')
    if brace_profile(g) != brace_profile(tokens(' '.join(outside))): bad.append('T3 GCC view brace profile')
    return bad


def gcc_leftovers(text):
    """line numbers (of the gcc view) of `__asm` code tokens left"""
    view = gcc_view(text)
    return [_lineno(view, a) for (a, _b, _k) in find_constructs(view)]


# ----------------------------------------------------------------------------------------------- prepare rule

def r_inline_asm(path, text, stats):
    """ASM-Pi: every __asm construct -> #ifdef _MSC_VER pristine #else __asm__("") #endif (module docstring)"""
    if '__asm' not in text: return text
    new, cnt = rewrite(path, text)
    if not cnt['spans']: return text
    stats['ASM-block'] += cnt['block']
    stats['ASM-stmt'] += cnt['run']
    stats['ASM-define'] += cnt['define']
    stats['ASM-spans'] += cnt['spans']
    return new


RULES = [r_inline_asm]
