#!/usr/bin/env python3
"""sdd1.py - prepare rule SDD1: a virtual destructor takes one vtable slot (shim/include/fnvnx_sdd.h).

    rules/sdd1.py <prepared tree> [--dry] [--report out.tsv]

A whole-tree pass, run after every per-file rule: it needs every class head to know which classes have a virtual
destructor through a base. It rewrites, in src/ and include/ of the tree:

    virtual ~C();                 ->  FNVNX_VDTOR(C, bases...) FNVNX_SDD_VIRTUAL ~C();
    virtual ~C() = 0;             ->  ... ~C() FNVNX_SDD_PURE(= 0);
    ~D();   (D in the set)        ->  FNVNX_VDTOR_O(D, bases...) ~D();
    class D : B { ... }  (no destructor declared, D in the set)
                                  ->  class D : B { ... FNVNX_SDD_IMPLICIT(D, B) }
    p->~C()  /  x.~C()            ->  FNVNX_DESTROY(p, C)  /  FNVNX_DESTROY_REF(x, C)   (unqualified calls only)

The set: a class that declares `virtual ~C`, and every class with a base in the set. A base is resolved to the
definition of that name earlier in the same file, else to the definitions in the files
it includes, else to the definitions of the whole tree. A reference whose definitions disagree is reported
(SDD1-MIXED) and taken as in the set: `override` in FNVNX_VDTOR_O makes a wrong guess a compile error.

Every edit keeps the line count. Under MSVC the macros give the original text back, without FNVNX_SDD they give
today's GCC text, so one tree serves both. Counts go to stdout as `id<TAB>files<TAB>edits`.
"""
import os
import re
import sys
from collections import Counter, defaultdict

EXT = ('.h', '.hpp', '.inl', '.cpp', '.cxx', '.cc', '.c')
MASK = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|//[^\n]*|/\*.*?(?:\*/|\Z)', re.S)
HEAD = re.compile(r'\b(class|struct)\s+((?:__declspec\s*\((?:[^()]|\([^()]*\))*\)\s*|__fnvnx_ds_\w+\s*|FNVNX_\w+\s+)*)'
                  r'([A-Za-z_]\w*)\s*(<[^;{}()]*>)?\s*(?:final\s*)?(:(?!:)[^;{}]*?)?\{')
ACCESS = re.compile(r'\b(?:public|private|protected|virtual)\b')
SPEC = re.compile(r'(?:\b(?:virtual|inline|__forceinline|__inline|explicit|FNVNX_KEEP|__thiscall|__cdecl|__stdcall)\b'
                  r'|__declspec\s*\((?:[^()]|\([^()]*\))*\)|__attribute__\s*\(\((?:[^()]|\([^()]*\))*\)\))\s*$')
DCALL = re.compile(r'(->|\.)\s*~\s*([A-Za-z_]\w*(?:\s*<[^;{}()]*?>)?)\s*\(\s*\)')
IDENT = re.compile(r'[A-Za-z_0-9]')


def mask(text):
    return MASK.sub(lambda m: re.sub(r'[^\n]', ' ', m.group()), text)


def close_of(m, i, o='{', c='}'):
    """index of the bracket closing m[i] (m is masked), or -1"""
    d = 0
    for j in range(i, len(m)):
        ch = m[j]
        if ch == o: d += 1
        elif ch == c:
            d -= 1
            if d == 0: return j
    return -1


def split_bases(clause):
    """[(spelling, simple name)] of a base clause `: public A<int, B>, virtual ns::C`"""
    out, depth, cur = [], 0, ''
    for ch in clause.lstrip(':') + ',':
        if ch in '<(': depth += 1
        elif ch in '>)': depth -= 1
        if ch == ',' and depth == 0:
            s = ' '.join(ACCESS.sub(' ', cur).split())
            if s:
                simple = re.sub(r'<.*', '', s, flags=re.S).split('::')[-1].strip()
                simple = simple.split()[-1] if simple.split() else simple     # `typename X`, `struct X`
                out.append((s, simple))
            cur = ''
        else:
            cur += ch
    return out


def depth1(m, o, c):
    """m[o+1:c] with everything nested in braces blanked (member bodies, nested classes)"""
    out, d = [], 0
    for ch in m[o + 1:c]:
        if ch == '{':
            d += 1; out.append(' ')
        elif ch == '}':
            d -= 1; out.append(' ')
        else:
            out.append(ch if d == 0 or ch == '\n' else ' ')
    return ''.join(out)


def classes(text, m=None):
    """every class definition of the file, in order: dicts name, bases, open, close, vdtor, dtor (span of the
    destructor's declarator start, or None), spec (start of its specifier run), pure (span of `= 0` or None)"""
    m = m if m is not None else mask(text)
    out = []
    for h in HEAD.finditer(m):
        pre = m[max(0, h.start() - 12):h.start()]
        if re.search(r'\benum\s*$', pre) or re.search(r'[<,]\s*$', pre):
            continue                                    # `enum class`, a template parameter
        o = h.end() - 1
        c = close_of(m, o)
        if c < 0: continue
        name = h.group(3)
        d1 = depth1(m, o, c)
        c1 = dict(name=name, bases=split_bases(h.group(5) or ''), open=o, close=c, vdtor=False, dtor=None, spec=None,
                  pure=None, union=False)
        dm = re.search(r'~\s*%s\s*\(' % re.escape(name), d1)
        if dm:
            at = o + 1 + dm.start()
            s = at
            while True:
                sm = SPEC.search(m[max(o + 1, s - 200):s])
                if not sm: break
                s = max(o + 1, s - 200) + sm.start()
            c1['dtor'], c1['spec'] = at, s
            c1['vdtor'] = bool(re.search(r'\bvirtual\b', m[s:at]))
            p = close_of(m, o + 1 + dm.end() - 1, '(', ')')
            pm = re.compile(r'\s*(?:const\s*)?(=\s*0)\s*;').match(m, p + 1) if p > 0 else None
            if pm: c1['pure'] = (pm.start(1), pm.end(1))
        out.append(c1)
    return out


def receiver_start(m, i):
    """start of the postfix expression that ends just before m[i] (the `->` or `.`), or -1"""
    j = i
    first = True
    while True:
        while j > 0 and m[j - 1] in ' \t\n': j -= 1
        if j == 0: return -1 if first else j
        ch = m[j - 1]
        if ch in ')]':
            o, c = ('(', ')') if ch == ')' else ('[', ']')
            d, k = 0, j - 1
            while k >= 0:
                if m[k] == c: d += 1
                elif m[k] == o:
                    d -= 1
                    if d == 0: break
                k -= 1
            if k < 0: return -1
            j = k
            first = False
            k2 = j
            while k2 > 0 and m[k2 - 1] in ' \t': k2 -= 1
            if k2 > 0 and (IDENT.match(m[k2 - 1]) or m[k2 - 1] in ')]'):
                j = k2
                if IDENT.match(m[j - 1]):
                    while j > 0 and IDENT.match(m[j - 1]): j -= 1
                    if re.match(r'(?:return|delete|new|sizeof|case|else|throw|if|while|for|switch)\b', m[j:]):
                        return k                       # `return (x)->~T()`: the parenthesis is the receiver
                else:
                    continue
            else:
                return j
        elif IDENT.match(ch):
            while j > 0 and IDENT.match(m[j - 1]): j -= 1
            first = False
        else:
            return -1 if first else j
        k = j
        while k > 0 and m[k - 1] in ' \t\n': k -= 1
        if m[k - 2:k] in ('->', '::') and k >= 2:
            j = k - 2
        elif m[k - 1:k] == '.' and k >= 1 and not m[k - 2:k - 1].isdigit():
            j = k - 1
        else:
            return j


def scan(tree):
    """{path: (text, masked, classes)} for src/ and include/"""
    files = {}
    for top in ('src', 'include'):
        for dp, _, fs in os.walk(os.path.join(tree, top)):
            for f in sorted(fs):
                if not f.lower().endswith(EXT): continue
                p = os.path.join(dp, f)
                with open(p, encoding='utf-8', errors='surrogateescape', newline='') as fh:
                    text = fh.read()
                if 'class' not in text and 'struct' not in text and '~' not in text: continue
                m = mask(text)
                files[p] = (text, m, classes(text, m))
    return files


INCLUDE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*[<"]([^>"]+)[>"]', re.M)


def include_closure(tree, files):
    """{path: [paths it includes, transitively, nearest first]} over the scanned files"""
    by_rel = {}
    for p in files:
        by_rel[os.path.relpath(p, tree).replace(os.sep, '/').lower()] = p
    by_base = defaultdict(list)
    for p in files: by_base[os.path.basename(p).lower()].append(p)
    direct = {}
    for p, (text, m, _) in files.items():
        d = os.path.relpath(os.path.dirname(p), tree).replace(os.sep, '/').lower()
        out = []
        for inc in INCLUDE.findall(text):
            inc = inc.replace('\\', '/').lower()
            hit = None
            for root in (d, 'include', 'src'):
                q = os.path.normpath(root + '/' + inc).replace(os.sep, '/')
                if q in by_rel: hit = by_rel[q]; break
            if hit is None and len(by_base[os.path.basename(inc)]) == 1: hit = by_base[os.path.basename(inc)][0]
            if hit and hit != p: out.append(hit)
        direct[p] = out
    memo = {}
    def walk(p):
        if p in memo: return memo[p]
        seen, order, stack = {p}, [], list(reversed(direct.get(p, [])))
        while stack:
            q = stack.pop()
            if q in seen: continue
            seen.add(q); order.append(q)
            stack.extend(reversed(direct.get(q, [])))
        memo[p] = order
        return order
    return {p: walk(p) for p in files}


def closure(files, tree=None):
    """mark c['sdd'] for every class. A base name resolves to its definition earlier in the same file, else to the
    definitions in the files this one includes, else to every definition of the tree. Returns the (file, class, base)
    references whose definitions still disagree; those count as in the set (FNVNX_VDTOR_O's `override` then fails
    to compile when the guess is wrong)."""
    for _, _, cs in files.values():
        for c in cs: c['sdd'] = c['vdtor']
    inc = include_closure(tree, files) if tree else {p: [] for p in files}
    mixed = set()
    while True:
        glob = defaultdict(set)
        byfile = {}
        for p, (_, _, cs) in files.items():
            d = byfile[p] = defaultdict(set)
            for c in cs:
                glob[c['name']].add(c['sdd']); d[c['name']].add(c['sdd'])
        changed = False
        for p, (_, _, cs) in files.items():
            local = {}
            for c in cs:
                if not c['sdd']:
                    for _, b in c['bases']:
                        if b in local: st = {local[b]}
                        else:
                            st = set()
                            for q in inc[p]:
                                if b in byfile[q]: st |= byfile[q][b]
                            if not st: st = glob.get(b, set())
                        if st == {True, False}: mixed.add((p, c['name'], b))
                        if True in st:
                            c['sdd'] = True; changed = True; break
                local[c['name']] = c['sdd'] or local.get(c['name'], False)
        if not changed: break
    return mixed


def rewrite(text, m, cs, stats):
    edits = []                                           # (start, end, replacement)
    for c in cs:
        bases = ''.join(', ' + s for s, _ in c['bases'])
        if c['dtor'] is not None:
            if c['vdtor']:
                vm = re.search(r'\bvirtual\b', m[c['spec']:c['dtor']])
                edits.append((c['spec'], c['spec'], 'FNVNX_VDTOR(%s%s) ' % (c['name'], bases)))
                edits.append((c['spec'] + vm.start(), c['spec'] + vm.end(), 'FNVNX_SDD_VIRTUAL'))
                stats['SDD1-VDTOR'] += 1
                if c['pure']:
                    edits.append((c['pure'][0], c['pure'][1], 'FNVNX_SDD_PURE(= 0)'))
                    stats['SDD1-PURE'] += 1
            elif c['sdd']:
                edits.append((c['spec'], c['spec'], 'FNVNX_VDTOR_O(%s%s) ' % (c['name'], bases)))
                stats['SDD1-VDTOR-O'] += 1
        elif c['sdd']:
            edits.append((c['close'], c['close'], ' FNVNX_SDD_IMPLICIT(%s%s) ' % (c['name'], bases)))
            stats['SDD1-IMPLICIT'] += 1
    for d in DCALL.finditer(m):
        k = d.start()
        while k > 0 and m[k - 1] in ' \t\n': k -= 1
        if m[k - 2:k] == '::': continue
        s = receiver_start(m, d.start())
        if s < 0 or any(a < d.end() and s < b for a, b, _ in edits if a != b):
            stats['SDD1-DESTROY-MISSED'] += 1
            continue
        recv = text[s:d.start()].strip()
        if ',' in re.sub(r'\((?:[^()]|\([^()]*\))*\)|\[[^\]]*\]', '', recv): recv = '(' + recv + ')'
        macro = 'FNVNX_DESTROY' if d.group(1) == '->' else 'FNVNX_DESTROY_REF'
        nl = '\n' * text[s:d.end()].count('\n')          # keep the line count
        edits.append((s, d.end(), '%s(%s, %s)%s' % (macro, recv.replace('\n', ' '), ' '.join(d.group(2).split()), nl)))
        stats['SDD1-DESTROY'] += 1
    edits.sort(key=lambda e: (e[0], e[1]))
    out, at = [], 0
    for a, b, r in edits:
        if a < at: continue                              # nested destroy calls: the outer one wins
        out.append(text[at:a]); out.append(r); at = b
    out.append(text[at:])
    return ''.join(out)


def run(tree, dry=False, report=None):
    files = scan(tree)
    mixed = closure(files, tree)
    stats, nfiles = Counter(), Counter()
    rows = []
    for p in sorted(files):
        text, m, cs = files[p]
        if 'FNVNX_VDTOR' in text or 'FNVNX_DESTROY' in text or 'FNVNX_SDD_IMPLICIT' in text:
            stats['SDD1-ALREADY'] += 1
            continue
        st = Counter()
        new = rewrite(text, m, cs, st)
        assert new.count('\n') == text.count('\n'), p
        for k, v in st.items():
            stats[k] += v; nfiles[k] += 1
        for c in cs:
            if c['sdd']:
                rows.append((os.path.relpath(p, tree), c['name'], 'virtual' if c['vdtor'] else
                             'override' if c['dtor'] is not None else 'implicit', ' '.join(b for _, b in c['bases'])))
        if new != text and not dry:
            with open(p, 'w', encoding='utf-8', errors='surrogateescape', newline='') as fh:
                fh.write(new)
    stats['SDD1-MIXED'] = len(mixed)
    if report:
        with open(report, 'w') as fh:
            fh.write('# fnvnx-sdd1/1\nfile\tclass\tkind\tbases\n')
            for r in rows: fh.write('\t'.join(r) + '\n')
            for f, c, b in sorted(mixed): fh.write('#mixed\t%s\t%s\t%s\n' % (os.path.relpath(f, tree), c, b))
    return stats, nfiles


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    rep = sys.argv[sys.argv.index('--report') + 1] if '--report' in sys.argv else None
    if rep: args.remove(rep)
    st, nf = run(args[0], dry='--dry' in sys.argv, report=rep)
    for k in sorted(st): print('%s\t%d\t%d' % (k, nf[k], st[k]))
