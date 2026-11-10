#!/usr/bin/env python3
"""sdd1.py - prepare rule SDD1: a virtual destructor takes one vtable slot (shim/include/fnvnx_sdd.h).

    rules/sdd1.py <prepared tree> [--dry] [--report out.tsv]

Run after every per-file rule, over src/ and include/ of the tree. It places macros by syntax alone; whether a class
is polymorphic is decided by the compiler inside the macros, never here:

    virtual ~C();                 ->  FNVNX_VDTOR(C, bases...) FNVNX_SDD_VIRTUAL ~C();
    virtual ~C() = 0;             ->  ... ~C() FNVNX_SDD_PURE(= 0);
    class D : B { ... }           ->  class D : B { ... FNVNX_SDD_DERIVED(D, B) }      (every class with a base
                                                                                        and no `virtual ~D`)
    p->~C()  /  x.~C()            ->  FNVNX_DESTROY(p, C)  /  FNVNX_DESTROY_REF(x, C)   (unqualified calls only)
    C::operator delete(p[, n])    ->  FNVNX_OPDELETE1(p, C)  /  FNVNX_OPDELETE2(p, n, C)

Every edit keeps the line count. Under MSVC the macros give the original text back, without FNVNX_SDD they give
today's GCC text, so one tree serves both. Counts go to stdout as `id<TAB>files<TAB>edits`. A construct the rule
sees but cannot place (SDD1-*-MISSED, SDD1-ANON) is counted; the tree is accepted only with those at 0.
"""
import os
import re
import sys
from collections import Counter

EXT = ('.h', '.hpp', '.inl', '.cpp', '.cxx', '.cc', '.c')
MASK = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|//[^\n]*|/\*.*?(?:\*/|\Z)', re.S)
HEAD = re.compile(r'\b(class|struct)\s+((?:__declspec\s*\((?:[^()]|\([^()]*\))*\)\s*|__fnvnx_ds_\w+\s*|FNVNX_\w+\s+)*)'
                  r'([A-Za-z_]\w*)\s*(<[^;{}()]*>)?\s*(?:final\s*)?(:(?!:)[^;{}]*?)?\{')
ACCESS = re.compile(r'\b(?:public|private|protected|virtual)\b')
SPEC = re.compile(r'(?:\b(?:virtual|inline|__forceinline|__inline|explicit|FNVNX_KEEP|__thiscall|__cdecl|__stdcall)\b'
                  r'|__declspec\s*\((?:[^()]|\([^()]*\))*\)|__attribute__\s*\(\((?:[^()]|\([^()]*\))*\)\))\s*$')
DCALL = re.compile(r'(->|\.)\s*~\s*([A-Za-z_]\w*(?:\s*<[^;{}()]*?>)?)\s*\(\s*\)')
IDENT = re.compile(r'[A-Za-z_0-9]')
ANON = re.compile(r'\b(?:class|struct)\s*:(?!:)[^;{}()]*\{')
OPDEL = re.compile(r'(?<![\w~:>])((?:[A-Za-z_]\w*\s*(?:<[^;{}()]*?>\s*)?::\s*)+)operator\s+delete\s*\(')


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
            if k2 > 0 and m[k2 - 1] == '>' and m[k2 - 2:k2] != '->':     # xxx_cast<T>(p), f<T>(p)
                d, k3 = 0, k2 - 1
                while k3 >= 0:
                    if m[k3] == '>': d += 1
                    elif m[k3] == '<':
                        d -= 1
                        if d == 0: break
                    elif m[k3] in ';{}': k3 = -1; break
                    k3 -= 1
                if k3 > 0 and IDENT.match(m[k3 - 1]): k2 = k3
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


def rewrite(text, m, cs, stats):
    edits = []                                           # (start, end, replacement)
    for c in cs:
        bases = ''.join(', ' + s for s, _ in c['bases'])
        if c['vdtor']:
            vm = re.search(r'\bvirtual\b', m[c['spec']:c['dtor']])
            edits.append((c['spec'], c['spec'], 'FNVNX_VDTOR(%s%s) ' % (c['name'], bases)))
            edits.append((c['spec'] + vm.start(), c['spec'] + vm.end(), 'FNVNX_SDD_VIRTUAL'))
            stats['SDD1-VDTOR'] += 1
            if c['pure']:
                edits.append((c['pure'][0], c['pure'][1], 'FNVNX_SDD_PURE(= 0)'))
                stats['SDD1-PURE'] += 1
        elif c['bases']:
            edits.append((c['close'], c['close'], ' FNVNX_SDD_DERIVED(%s%s) ' % (c['name'], bases)))
            stats['SDD1-DERIVED'] += 1
    stats['SDD1-ANON'] += len(ANON.findall(m))
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
    for d in OPDEL.finditer(m):
        pre = m[max(0, d.start() - 40):d.start()].rstrip()
        if re.search(r'(?:\bvoid|\b__cdecl|\bstatic|\binline|\bfriend|\*|&)$', pre): continue      # a definition
        p = close_of(m, d.end() - 1, '(', ')')
        if p < 0: continue
        args, depth, cur = [], 0, ''
        for ch in text[d.end():p] + ',':
            if ch in '(<[': depth += 1
            elif ch in ')>]': depth -= 1
            if ch == ',' and depth == 0: args.append(cur.strip()); cur = ''
            else: cur += ch
        cls = ' '.join(d.group(1).split()).rstrip(':').rstrip()
        if len(args) not in (1, 2) or not args[0] or '\n' in text[d.start():p]:
            stats['SDD1-OPDELETE-MISSED'] += 1
            continue
        edits.append((d.start(), p + 1, 'FNVNX_OPDELETE%d(%s, %s)' % (len(args), ', '.join(args), cls)))
        stats['SDD1-OPDELETE'] += 1
    edits.sort(key=lambda e: (e[0], e[1]))
    out, at = [], 0
    for a, b, r in edits:
        if a < at: continue                              # nested destroy calls: the outer one wins
        out.append(text[at:a]); out.append(r); at = b
    out.append(text[at:])
    return ''.join(out)


def run(tree, dry=False, report=None):
    files = scan(tree)
    stats, nfiles = Counter(), Counter()
    rows = []
    for p in sorted(files):
        text, m, cs = files[p]
        if 'FNVNX_VDTOR' in text or 'FNVNX_DESTROY' in text or 'FNVNX_SDD_DERIVED' in text or 'FNVNX_OPDELETE' in text:
            stats['SDD1-ALREADY'] += 1
            continue
        st = Counter()
        new = rewrite(text, m, cs, st)
        assert new.count('\n') == text.count('\n'), p
        for k, v in st.items():
            if v: stats[k] += v; nfiles[k] += 1
        for c in cs:
            if c['vdtor'] or c['bases']:
                rows.append((os.path.relpath(p, tree), c['name'], 'virtual' if c['vdtor'] else 'derived',
                             ' '.join(b for _, b in c['bases'])))
        if new != text and not dry:
            with open(p, 'w', encoding='utf-8', errors='surrogateescape', newline='') as fh:
                fh.write(new)
    if report:
        with open(report, 'w') as fh:
            fh.write('# fnvnx-sdd1/2\nfile\tclass\tkind\tbases\n')
            for r in rows: fh.write('\t'.join(r) + '\n')
    return stats, nfiles


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    rep = sys.argv[sys.argv.index('--report') + 1] if '--report' in sys.argv else None
    if rep: args.remove(rep)
    st, nf = run(args[0], dry='--dry' in sys.argv, report=rep)
    for k in sorted(st): print('%s\t%d\t%d' % (k, nf[k], st[k]))
