#!/usr/bin/env python3
"""popgen.py POP_TSV ABI_TSV OUTDIR [--only SRC,..] [--family D:R,..] - candidate XINC1 patches for the
pop-mismatch chain family (docs/PATCHES.md, "Pop-mismatch families").

Input: popscan.py's table. A `fewer-donor` call whose donor is one `more` call of the same caller, with the words
the receiver lacks equal to the words the donor's declaration pushes too many, is a chain when the source spells

    D(a1, .., an, x1, .., xk) . R(r1, .., rj)          (or `->`; or R an implicit `operator bool` of D's result)

The decomp gave the receiver's trailing stack arguments to the donor: on x86 the pushes are the same, natively R
reads registers nobody set. The patch moves them:

    D(a1, .., an) . R(r1, .., rj, x1, .., xk)

The nested spelling, D's call as a whole argument of R (x86: the words pushed for D stay on the stack right after
D's result, which R pops as its next arguments):

    R(r1, .., D(a1, .., an, x1, .., xk), .., rj)   ->   R(r1, .., D(a1, .., an), x1, .., xk, .., rj)

A moved parameter whose name the receiver already uses keeps its type only. --skip-patched DIR leaves out callers
that another patch file owns.

and declares the two overloads next to the decomp's declarations (same class, same result and qualifiers; the
moved parameters keep the donor's spelling, so MSVC pushes the same words in the same order). An implicit
`operator bool` becomes an explicit `FUN_<R>(x..) const` returning bool, as in the pilot (0x00428110 / 0x004280f0).
Every chain occurrence of the pair in the caller must be a flagged site, every argument count must match its
declaration, no declaration may be virtual, static, defined inline or have default arguments, and no other
overload of the same arity may exist: otherwise the caller is listed in OUTDIR/manual.tsv.

Output (scratch): OUTDIR/xinc1_pop_gen.s1b (sites keyed by the caller's @fnv address; declarations that are
outside the caller's window go to `file` sites), OUTDIR/manual.tsv. The patches hold our declarations plus the
decomp text the hunks match, the same as the committed patch files; check them with tests/s1b/msvc_check.py
--patches OUTDIR before copying anything into patches/.
"""
import argparse
import collections
import os
import re
import sys

TAG = re.compile(r'//[ \t]*@fnv[ \t]+0x([0-9a-fA-F]+)')
ID = r'[A-Za-z_]\w*'
IMPURE = re.compile(r'[\w\]]\s*\(|\+\+|--|(?<![=!<>])=(?!=)|\bnew\b|\bdelete\b')


def match_paren(t, i):
    """t[i] == '(' -> index after the matching ')', or None. Skips string and char literals and comments."""
    depth, k, n = 0, i, len(t)
    while k < n:
        c = t[k]
        if c in '"\'':
            q = c
            k += 1
            while k < n and t[k] != q:
                k += 2 if t[k] == '\\' else 1
        elif t.startswith('//', k):
            e = t.find('\n', k)
            k = n if e < 0 else e
            continue
        elif t.startswith('/*', k):
            e = t.find('*/', k + 2)
            k = n if e < 0 else e + 2
            continue
        elif c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return k + 1
        elif c in ';{}' and depth:
            return None
        k += 1
    return None


def split_args(s):
    """Top-level comma split of an argument / parameter list text (without the parens). Parens, brackets and braces
    nest; `<` opens a template argument list only after a name and when a `>` closes it at the same depth before the
    list ends (`std::pair<int, int>`), so `->`, `<<`, `>>`, `<=`, `>=` and a lone comparison (`a < b, c`) are not
    brackets. (`f(a < b, c > d)` stays ambiguous, as it is to a C++ parser without knowing whether `a` is a template.)"""
    out, depth, angle, cur, k = [], 0, [], '', 0
    n = len(s)
    while k < n:
        c = s[k]
        if c in '"\'':
            q, j = c, k + 1
            while j < n and s[j] != q:
                j += 2 if s[j] == '\\' else 1
            cur += s[k:j + 1]
            k = j + 1
            continue
        two = s[k:k + 2]
        if two in ('->', '<<', '<=', '>=') or (two == '>>' and not angle):
            cur += two
            k += 2
            continue
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
            while angle and angle[-1] > depth:
                angle.pop()
        elif c == '<' and re.search(r'\w\s*$', cur) and _closes_angle(s, k):
            angle.append(depth)
        elif c == '>' and angle and angle[-1] == depth:
            angle.pop()
        if c == ',' and depth == 0 and not angle:
            out.append(cur)
            cur = ''
        else:
            cur += c
        k += 1
    if cur.strip() or out:
        out.append(cur)
    return out


def _closes_angle(s, k):
    """True when the `<` at s[k] has a matching `>` at its own paren depth before that depth ends"""
    depth, ang, j, n = 0, 1, k + 1, len(s)
    while j < n:
        c, two = s[j], s[j:j + 2]
        if c in '"\'':
            q, j = c, j + 1
            while j < n and s[j] != q:
                j += 2 if s[j] == '\\' else 1
            j += 1
            continue
        if two in ('->', '<=', '>=', '<<'):
            j += 2
            continue
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
            if depth < 0:
                return False
        elif depth == 0 and c == '<' and re.search(r'\w\s*$', s[:j]):
            ang += 1
        elif depth == 0 and c == '>':
            ang -= 1
            if ang == 0:
                return True
        elif depth == 0 and c in ';{':
            return False
        j += 1
    return False


def sig_parts(sig):
    """'public: struct X __thiscall A::B::f(char const *, unsigned int) const' -> (['A', 'B'], 'f', [params], const)"""
    m = re.search(r'__(?:thiscall|cdecl|stdcall|fastcall)\s+(.*?)\((.*)\)\s*(const)?\s*$', sig)
    if not m:
        return None
    q = m.group(1).strip()
    parts = re.split(r'::', q)
    name = parts[-1]
    ps = [p.strip() for p in split_args(m.group(2))]
    if ps == ['void']:
        ps = []
    return parts[:-1], name, ps, bool(m.group(3))


def words(ptype):
    t = ptype.replace(' ', '')
    if t.endswith('*') or t.endswith('&'):
        return 1
    if t in ('double', '__int64', 'unsigned__int64', 'longlong', 'unsignedlonglong', 'longdouble'):
        return 2
    if t in ('char', 'signedchar', 'unsignedchar', 'short', 'unsignedshort', 'int', 'unsignedint', 'long',
             'unsignedlong', 'float', 'bool', 'wchar_t'):
        return 1
    if t.startswith('enum'):
        return 1
    return None                                  # a struct by value or an unknown typedef: not mechanical


def fn_window(text, addr):
    tags = [(m.start(), int(m.group(1), 16)) for m in TAG.finditer(text)]
    for i, (p, a) in enumerate(tags):
        if a == addr:
            return (tags[i - 1][0] if i else 0), p, (tags[i + 1][0] if i + 1 < len(tags) else len(text))
    return None


def enclosing_arg(seg, ds, de, rn):
    """D's call seg[ds:de] as a whole argument of a call `rn(..)`: (receiver '(' index, end after its ')', argument
    index), else None. The argument may have an object expression or cast before D's name, nothing after D's ')'."""
    depth, k = 0, ds - 1
    while k >= 0:
        c = seg[k]
        if c in ')]':
            depth += 1
        elif c in '([':
            if depth == 0:
                break
            depth -= 1
        elif c in ';{}':
            return None
        k -= 1
    if k < 0 or seg[k] != '(':
        return None
    if not re.search(r'(?<![\w~])%s\s*$' % re.escape(rn), seg[:k]):
        return None
    re_ = match_paren(seg, k)
    if re_ is None:
        return None
    args = split_args(seg[k + 1:re_ - 1])
    pos, idx = k + 1, None
    for i, x in enumerate(args):
        if pos <= ds < pos + len(x):
            idx = i
            if seg[de:pos + len(x)].strip():
                return None
            break
        pos += len(x) + 1
    if idx is None:
        return None
    return (k, re_, idx)


GAP = re.compile(r'^(?:[\s)\]*]|\.|->|\+\s*(?:0x[0-9a-fA-F]+|\d+)|%s|\(\s*\))*$' % ID)


class _Reach:
    """re.match-like result of reach_receiver: end() is the index after the receiver's '(' in `rest`."""
    def __init__(self, end):
        self._e = end

    def end(self):
        return self._e


NOREACH = set()       # files generated without reach_receiver (an overload clash with it; see main)
CURRENT = [None]


def reach_receiver(rest, rn):
    """The receiver R further along D's result: casts, dereferences, a constant offset, member access or calls
    without arguments between D's ')' and `.R(` / `->R(` (`((T*)x->D(a))->R()`, `static_cast<T*>(X.D(a))->R()`,
    `D(a)->B()->R()`). x86: none of them pushes or pops a word, so the words pushed for D stay for R (popscan's stack
    simulation names D the donor of R). Same statement, no comma or other argument between."""
    if CURRENT[0] in NOREACH:
        return None
    for m in re.finditer(r'(?:\.|->)\s*%s\s*\(' % re.escape(rn), rest):
        gap = rest[:m.start()]
        if re.search(r'[;{},]', gap):
            return None
        if GAP.match(gap):
            return _Reach(m.end())
        return None
    return None


def find_chains(seg, dn, rn):
    """[(start, end of `dn(...)`, args text, receiver start or None, receiver args span)] in seg."""
    out = []
    for m in re.finditer(r'(?<![\w~])%s\s*\(' % re.escape(dn), seg):
        p = m.end() - 1
        e = match_paren(seg, p)
        if e is None:
            continue
        rest = seg[e:]
        if rn.startswith('operator '):
            nx = re.match(r'\s*(\.|->|\[)', rest)
            out.append((m.start(), e, seg[p + 1:e - 1], None if not nx else 'member', None))
            continue
        mm = re.match(r'\s*(?:\.|->)\s*%s\s*\(' % re.escape(rn), rest)
        if not mm:
            mm = reach_receiver(rest, rn)
        if not mm:
            nest = enclosing_arg(seg, m.start(), e, rn)
            if nest is not None:
                out.append((m.start(), e, seg[p + 1:e - 1], 'nested', nest))
            else:
                out.append((m.start(), e, seg[p + 1:e - 1], 'other', None))
            continue
        rp = e + mm.end() - 1
        re_ = match_paren(seg, rp)
        if re_ is None:
            continue
        out.append((m.start(), e, seg[p + 1:e - 1], 'chain', (rp, re_)))
    return out


def class_bodies(text, cname):
    """[(start of '{', end after '}')] of every `struct|class|union cname ... {` definition in text."""
    out = []
    for m in re.finditer(r'\b(?:struct|class|union)\s+(?:__declspec\([^)]*\)\s*)?%s\b\s*(?::[^;{()]*)?\{' %
                         re.escape(cname), text):
        b = m.end() - 1
        depth, k = 0, b
        while k < len(text):
            if text[k] == '{':
                depth += 1
            elif text[k] == '}':
                depth -= 1
                if depth == 0:
                    break
            k += 1
        out.append((b, k + 1))
    return out


def member_decls(text, body, name):
    """Top-level declarations of `name` in a class body: [(stmt start, stmt end incl ';', ret text, params text,
    const, virtual/static/inline-body flags)]."""
    b, e = body
    out = []
    depth = 0
    k = b + 1
    starts = [b + 1]
    # statement boundaries at depth 0
    while k < e - 1:
        c = text[k]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                starts.append(k + 1)
        elif c == ';' and depth == 0:
            starts.append(k + 1)
        elif c == ':' and depth == 0 and re.search(r'\b(?:public|private|protected)\s*$', text[starts[-1]:k]):
            starts.append(k + 1)
        k += 1
    starts.append(e - 1)
    for s0, s1 in zip(starts, starts[1:]):
        st = text[s0:s1]
        m = re.search(r'(?<![\w~])%s\s*\(' % re.escape(name), st)
        if not m:
            continue
        p = s0 + m.end() - 1
        q = match_paren(text, p)
        if q is None:
            continue
        tail = text[q:s1]
        head = st[:m.start()]
        bad = []
        if re.search(r'\bvirtual\b', head):
            bad.append('virtual')
        if re.search(r'\bstatic\b', head):
            bad.append('static')
        if '{' in tail:
            bad.append('inline body')
        if '=' in text[p:q]:
            bad.append('default argument')
        if not tail.strip().endswith(';'):
            bad.append('no ;')
        lead = len(head) - len(head.lstrip())
        out.append(dict(start=s0 + lead, end=s1, ret=head.strip(), params=text[p + 1:q - 1],
                        const=bool(re.match(r'\s*const\b', tail)), bad=bad))
    return out


def norm(s):
    return re.sub(r'\s+', ' ', s).strip()


def param_type(p):
    """'unsigned int flags' -> 'unsigned int' (drops a trailing identifier that is not a type keyword)."""
    p = norm(p)
    m = re.match(r'^(.*?[\w*&>\s])\s*\b(%s)$' % ID, p)
    kw = {'int', 'char', 'short', 'long', 'float', 'double', 'bool', 'unsigned', 'signed', 'const', 'void', 'wchar_t',
          '__int64'}
    if m and m.group(2) not in kw and m.group(1).strip() and not m.group(1).strip().endswith('::'):
        if re.search(r'[\w*&>]\s*$', m.group(1)):
            return norm(m.group(1))
    return p


def pname(p):
    """'unsigned int flags' -> 'flags'; None for an unnamed parameter."""
    t = param_type(p)
    n = norm(p)[len(t):].strip()
    return n or None


def same_params(a, b):
    ta = [param_type(x) for x in split_args(a)]
    tb = [param_type(x) for x in split_args(b)]
    ta = [] if ta == ['void'] else ta
    tb = [] if tb == ['void'] else tb
    return ta == tb


def arity(params):
    ps = [x for x in split_args(params) if x.strip()]
    return 0 if ps in ([], ['void']) or [norm(x) for x in ps] == ['void'] else len(ps)


def line_span(text, a, b):
    s = text.rfind('\n', 0, a) + 1
    e = text.find('\n', b)
    return s, (len(text) if e < 0 else e)


def unique_old(text, s, e, lo, hi):
    """Grow [s, e) by whole lines upward until text[s:e] occurs once in text[lo:hi]; None if not within 6 lines."""
    for _ in range(7):
        if text[lo:hi].count(text[s:e]) == 1:
            return s, e
        if s <= lo:
            return None
        s = text.rfind('\n', 0, s - 1) + 1
        s = max(s, lo)
    return None


def elaborate(text, pos, p):
    """A moved parameter `T*` / `T&` whose class T is first declared after pos (the receiver's class body) gets the
    class key of that declaration (`struct T*`): an elaborated type in a member's parameter list declares T in the
    enclosing namespace, as the later definition does. Other parameters are returned unchanged."""
    m = re.match(r'^(const\s+)?(%s)(\s*[*&].*)$' % ID, p)
    if not m or m.group(2) in ('void', 'char', 'int', 'short', 'long', 'float', 'double', 'bool', 'unsigned',
                                'signed', 'wchar_t', '__int64', 'struct', 'class', 'union', 'enum'):
        return p
    d = re.search(r'\b(struct|class|union|enum)\s+(?:__declspec\([^)]*\)\s*)?%s\b|\btypedef\b[^;]*\b%s\s*;' %
                  (re.escape(m.group(2)), re.escape(m.group(2))), text)
    if d is None or d.start() < pos or not d.group(1):
        return p
    return '%s%s %s%s' % (m.group(1) or '', d.group(1), m.group(2), m.group(3))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pop')
    ap.add_argument('abi')
    ap.add_argument('outdir')
    ap.add_argument('--only')
    ap.add_argument('--exclude', help='source paths left out (e.g. hunks that conflict with other patches)')
    ap.add_argument('--family', help='donor:receiver address pairs (hex, 8 digits) to generate')
    ap.add_argument('--name', default='gen')
    ap.add_argument('--skip-patched', metavar='DIR',
                    help='leave out callers that are sites of a patch file in DIR other than xinc1_pop_<name>.s1b')
    ap.add_argument('--noreach', default='', help='files generated without the reach rule (set automatically)')
    a = ap.parse_args()
    NOREACH.update(filter(None, a.noreach.split('|')))
    owned = set()
    if a.skip_patched:
        for fn in sorted(os.listdir(a.skip_patched)):
            if fn.endswith('.s1b') and fn != 'xinc1_pop_%s.s1b' % a.name:
                for m in re.finditer(r'^@@ \w+ (\S+) 0x([0-9a-fA-F]+)',
                                     open(os.path.join(a.skip_patched, fn), encoding='utf-8').read(), re.M):
                    owned.add((m.group(1), m.group(2).lower().zfill(8)))
    D = os.environ['DECOMP']
    os.makedirs(a.outdir, exist_ok=True)
    abi = {}
    for line in open(a.abi):
        f = line.rstrip('\n').split('\t')
        if len(f) > 10 and not line.startswith('#'):
            abi.setdefault((f[0].replace('#vtables', ''), f[2], f[3]), f)
    rows = [l.rstrip('\n').split('\t') for l in open(a.pop) if not l.startswith('#')]
    calls = collections.defaultdict(dict)        # (src, caller) -> {off: row}
    for r in rows:
        if r[7] != '-':
            calls[(r[0], r[5])][r[7]] = r
    fams = set(a.family.split(',')) if a.family else None
    only = set(a.only.split(',')) if a.only else None
    exclude = set(a.exclude.split(',')) if a.exclude else None
    groups = collections.defaultdict(list)
    manual = []
    for r in rows:
        if r[10] != 'fewer-donor' or (only and r[0] not in only):
            continue
        if exclude and r[0] in exclude:
            manual.append((r[0], r[5], '-', 'excluded (hunks conflict with other patches)'))
            continue
        if (r[0], r[5].lower().zfill(8)) in owned:
            manual.append((r[0], r[5], '-', 'site of another patch file'))
            continue
        if re.search(r'\s', r[0]):
            manual.append((r[0], r[5], '-', 'path with blanks (patch format)'))
            continue
        groups[(r[0], r[5])].append(r)
    by_file = collections.defaultdict(lambda: dict(calls=[], decls={}))
    texts = {}
    stats = collections.Counter()
    for (src, caller), rs in sorted(groups.items()):
        if src not in texts:
            texts[src] = open(os.path.join(D, src), encoding='utf-8', errors='surrogateescape').read()
        text = texts[src]
        CURRENT[0] = src
        w = fn_window(text, int(caller, 16))
        if w is None:
            manual.append((src, caller, '-', 'no @fnv tag'))
            continue
        wlo, wtag, whi = w
        seg = text[wtag:whi]
        pairs = collections.defaultdict(list)
        why = None
        for r in rs:
            donors = r[12].split(',')
            if len(donors) != 1:
                why = 'several donors'
                break
            doff, daddr = donors[0].split(':')
            d = calls[(src, caller)].get(doff)
            if d is None:
                why = 'donor call not a mismatch row'
                break
            m = int(r[9]) - int(r[8])
            if int(d[8]) - int(d[9]) != m:
                why = 'donor surplus %d != receiver deficit %d' % (int(d[8]) - int(d[9]), m)
                break
            pairs[(d[2], r[2], daddr.zfill(8).lower(), r[1], m)].append(r)
        if why:
            manual.append((src, caller, '-', why))
            continue
        edits, ok, pending = [], True, []
        for (dref, rref, daddr, raddr, m), prs in pairs.items():
            if fams and '%s:%s' % (daddr, raddr) not in fams:
                ok = False
                why = 'family not selected'
                break
            fd, fr = abi.get((src, daddr, dref)) or abi.get((src, daddr.upper(), dref)), abi.get((src, raddr, rref))
            if fd is None or fr is None:
                ok, why = False, 'abi row missing'
                break
            sd, sr = sig_parts(fd[9]), sig_parts(fr[9])
            if not sd or not sr:
                ok, why = False, 'signature unparsed'
                break
            dcls, dn, dps, dconst = sd
            rcls, rn, rps, rconst = sr
            if not dcls or not rcls:
                ok, why = False, 'free function'
                break
            # trailing donor params whose words are m
            k, acc = 0, 0
            while acc < m and k < len(dps):
                wd = words(dps[-1 - k])
                if wd is None:
                    break
                acc += wd
                k += 1
            if acc != m:
                ok, why = False, 'donor params do not split at %d words' % m
                break
            chains = find_chains(seg, dn, rn)
            want = [c for c in chains if (c[3] in ('chain', 'nested') if not rn.startswith('operator ')
                                          else c[3] is None) and arity(c[2]) == len(dps)]
            if len({c[3] for c in want}) > 1:
                ok, why = False, 'chain and nested spellings mixed (%s . %s)' % (dn, rn)
                break
            ins = None
            if want and want[0][3] == 'nested':
                ins = {c[4][2] + 1 for c in want}
                if len(ins) != 1:
                    ok, why = False, 'nested at different argument positions'
                    break
                ins = ins.pop()
            if len(want) != len(prs):
                ok, why = False, '%d chains for %d sites (%s . %s)' % (len(want), len(prs), dn, rn)
                break
            for c in want:
                args = split_args(c[2])
                moved = [x.strip() for x in args[len(args) - k:]]
                # x86 evaluates the moved words before the donor call; GCC (C++17) evaluates a call's object
                # expression first: only side-effect-free arguments move (no call, assignment, ++/--, new)
                if any(IMPURE.search(x) for x in moved):
                    ok, why = False, 'moved argument with side effects'
                    break
                if c[4] is not None:
                    rargs = seg[c[4][0] + 1:c[4][1] - 1]
                    if arity(rargs) != len(rps):
                        ok, why = False, 'receiver argument count'
                        break
                edits.append(dict(c=c, k=k, moved=moved, dn=dn, rn=rn, raddr=raddr))
            if not ok:
                break
            # declarations
            for role, cls, name, ps, cst, keep in (('D', dcls, dn, dps, dconst, len(dps) - k), ('R', rcls, rn, rps, rconst, None)):
                bodies = [b for b in class_bodies(text, cls[-1])]
                cands = []
                for b in bodies:
                    for md in member_decls(text, b, name):
                        if arity(md['params']) == len(ps):
                            cands.append((b, md))
                if len(cands) != 1:
                    ok, why = False, '%s declaration of %s::%s: %d candidates' % (role, cls[-1], name, len(cands))
                    break
                b, md = cands[0]
                if md['bad']:
                    ok, why = False, '%s declaration: %s' % (role, ','.join(md['bad']))
                    break
                others = [x for x in member_decls(text, b, name)]
                if role == 'D':
                    plist = split_args(md['params'])
                    newp = ','.join(plist[:keep]).strip() if keep else ''
                    newname, ret = name, md['ret']
                    newcst = md['const']
                else:
                    plist = [x for x in split_args(md['params']) if norm(x) not in ('', 'void')]
                    newname, ret, newcst = name, md['ret'], md['const']
                    if name.startswith('operator '):
                        if name != 'operator bool':
                            ok, why = False, 'receiver %s' % name
                            break
                        newname, ret, newcst = 'FUN_%s' % raddr, 'bool', True
                        others = [x for x in member_decls(text, b, newname)]
                    newp = plist
                n_new = (keep if role == 'D' else len(plist) + k)
                clash = [x for x in others if arity(x['params']) == n_new]
                pending.append(((b[0], md['start']), md, dict(role=role, name=newname, ret=ret, params=newp,
                                                              const=newcst, k=k, donor=(src, daddr, dref), clash=clash,
                                                              ins=ins)))
            if not ok:
                break
        if not ok:
            manual.append((src, caller, '-', why))
            continue
        for key, md, ad in pending:
            by_file[src]['decls'].setdefault(key, dict(md=md, add=[]))['add'].append(ad)
        by_file[src]['calls'].append(dict(caller=caller, w=(wlo, wtag, whi), edits=edits))
        stats['callers'] += 1
        stats['sites'] += len(edits)
    # resolve the receiver's moved parameter spellings: the donor declaration's trailing params
    out_sites = []
    for src, fd in by_file.items():
        text = texts[src]
        decl_new = {}
        dparams = {}
        for key, ent in fd['decls'].items():
            for ad in ent['add']:
                if ad['role'] == 'D':
                    dparams[ad['donor']] = split_args(ent['md']['params'])
        bad = False
        seen_cls = collections.defaultdict(set)  # class body -> declarations added (one class may get it twice)
        for key, ent in fd['decls'].items():
            texts_add = []
            for ad in ent['add']:
                if ad['role'] == 'D':
                    ps = ad['params']
                else:
                    dp = dparams.get(ad['donor'])
                    if dp is None:
                        bad = True
                        break
                    rp_ = [norm(x) for x in ad['params']]
                    late = False
                    taken = {pname(x) for x in rp_} - {None}
                    mv = []
                    for x in dp[len(dp) - ad['k']:]:
                        x = norm(x)
                        if pname(x) in taken:            # the receiver already has a parameter of that name
                            x = param_type(x)
                        taken.add(pname(x))
                        tm = re.match(r'^(?:const\s+)?(%s)\s*<' % ID, x)
                        if tm and re.search(r'\btemplate\s*<[^;{]*>\s*(?:class|struct)\s+%s\b' % tm.group(1),
                                            text[:key[0]]) is None:
                            late = True              # a class template first declared after the receiver's class
                        mv.append(elaborate(text, key[0], x))
                    ins_ = len(rp_) if ad.get('ins') is None else ad['ins']
                    ps = ', '.join(rp_[:ins_] + mv + rp_[ins_:])
                    if late:
                        bad = 'moved parameter of a class template declared later'
                        break
                # an existing declaration with the same parameters already is the overload
                if any(same_params(x['params'], ps) and (x.get('ret') or '') == ad['ret'] for x in ad['clash']):
                    continue
                if ad['clash']:
                    bad = True
                    break
                d = '%s %s(%s)%s;' % (ad['ret'], ad['name'], ps, ' const' if ad['const'] else '')
                if d not in seen_cls[key[0]]:
                    seen_cls[key[0]].add(d)
                    texts_add.append(d)
            if bad:
                break
            if texts_add:
                decl_new[key] = texts_add
        if bad:
            for c in fd['calls']:
                manual.append((src, c['caller'], '-', bad if isinstance(bad, str) else 'overload clash'))
            continue
        # every edit (declarations and calls) as (start, end, replacement, (window lo, hi, caller) or None)
        edits_all = []
        for (b0, st), adds in sorted(decl_new.items()):
            md = fd['decls'][(b0, st)]['md']
            ls, le = line_span(text, md['start'], md['end'] - 1)
            if text[md['end']:le].strip():       # more on the line (a one-line class body): right after the ;
                le = md['end']
            edits_all.append((le, le, ' ' + ' '.join(adds) + ' /* fnv-nx XINC1 pop: overloads with the words the '
                              'callees pop */', None))
        for c in fd['calls']:
            wlo, wtag, whi = c['w']
            wnd = (wlo, whi, c['caller'])
            for e in c['edits']:
                s0, e0, args_t, kind, rspan = e['c']
                args = split_args(args_t)
                keep = ','.join(args[:len(args) - e['k']]).rstrip()
                edits_all.append((wtag + e0 - 1 - len(args_t), wtag + e0 - 1, keep, wnd))
                mv = ', '.join(e['moved'])
                if kind == 'nested':
                    edits_all.append((wtag + e0, wtag + e0, ', ' + mv, wnd))
                elif kind == 'chain':
                    rp, re_ = rspan
                    rargs = text[wtag + rp + 1:wtag + re_ - 1]
                    if norm(rargs) in ('', 'void'):
                        edits_all.append((wtag + rp + 1, wtag + re_ - 1, mv, wnd))
                    else:
                        edits_all.append((wtag + re_ - 1, wtag + re_ - 1, ', ' + mv, wnd))
                else:
                    edits_all.append((wtag + e0, wtag + e0, '.FUN_%s(%s)' % (e['raddr'], mv), wnd))
        # group the edits by overlapping line spans: one hunk per group
        spans = sorted(line_span(text, s, max(s, e - 1) if e > s else s) + (s, e, r, w) for s, e, r, w in edits_all)
        groups = []
        for ls, le, s, e, r, w in spans:
            if groups and ls <= groups[-1]['le']:
                groups[-1]['le'] = max(groups[-1]['le'], le)
                groups[-1]['ed'].append((s, e, r, w))
            else:
                groups.append(dict(ls=ls, le=le, ed=[(s, e, r, w)]))
        for g in groups:
            ws = {x[3] for x in g['ed']}
            owner = ws.pop() if len(ws) == 1 else None
            if owner is None:            # a declaration, or edits of two callers: whole-file uniqueness
                for c in fd['calls']:
                    if c['w'][0] <= g['ls'] and g['le'] <= c['w'][2] and all(x[3] in (None, (c['w'][0], c['w'][2], c['caller']))
                                                                             for x in g['ed']):
                        owner = (c['w'][0], c['w'][2], c['caller'])
            lo, hi = (owner[0], owner[1]) if owner else (0, len(text))
            u = unique_old(text, g['ls'], g['le'], lo, hi)
            if u is None and owner:
                owner, lo, hi = None, 0, len(text)
                u = unique_old(text, g['ls'], g['le'], lo, hi)
            if u is None:
                manual.append((src, owner[2] if owner else 'file', '-', 'hunk context not unique'))
                stats['nounique'] += 1
                continue
            s, e = u
            newt = text[s:e]
            for es, ee, r, _ in sorted(g['ed'], key=lambda x: (x[0], x[1]), reverse=True):
                newt = newt[:es - s] + r + newt[ee - s:]
            out_sites.append((src, owner[2] if owner else 'file', (text[s:e], newt),
                              sum(1 for x in g['ed'] if x[3] is not None and x[2] and not x[2].startswith(' /*'))))
    # a file whose new (reach) chains clash with its overloads loses every caller: generate it again without them
    clash = {r[0] for r in manual if r[3] == 'overload clash'} - NOREACH
    if clash:
        argv = [x for x in sys.argv if not x.startswith('--noreach')]
        os.execv(sys.executable, [sys.executable] + argv + ['--noreach=' + '|'.join(sorted(NOREACH | clash))])
    # write: one site per (file, owner), hunks in text order
    texts_out = []
    merged = collections.OrderedDict()
    for src, owner, hunk, n in out_sites:
        ent = merged.setdefault((src, owner), [[], 0])
        ent[0].append(hunk)
    for (src, owner), (hunks, _) in merged.items():
        body = ['@@ XINC1 %s %s' % (src, '0x' + owner if owner != 'file' else 'file')]
        if owner == 'file':
            body.append('# declarations: overloads with the stack words the callees pop (x86: the pushes are the same;')
            body.append("# the decomp gave the receiver's trailing arguments to the donor call before it)")
        else:
            body.append('# x86 0x%s: a call pops fewer words than its declaration pushes and the call on its result' % owner)
            body.append('# pops them (ret N of both definitions): the trailing arguments move to that call')
        for old, newt in hunks:
            body += ['<<<', old, '===', newt, '>>>']
        texts_out.append('\n'.join(body))
    with open(os.path.join(a.outdir, 'xinc1_pop_%s.s1b' % a.name), 'w') as f:
        f.write('# XINC1 pop-mismatch chains (generated by tests/s1b/pop/popgen.py, checked with msvc_check.py)\n\n')
        f.write('\n\n'.join(texts_out) + '\n')
    with open(os.path.join(a.outdir, 'manual.tsv'), 'w') as f:
        for r in manual:
            f.write('\t'.join(r) + '\n')
    why = collections.Counter(re.sub(r'\d+', 'N', r[3]) for r in manual)
    print(dict(stats), 'manual', len(manual), why.most_common(12))


if __name__ == '__main__':
    main()
