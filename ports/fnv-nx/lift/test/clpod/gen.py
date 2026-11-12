#!/usr/bin/env python3
"""gen.py - the cl reference table of the MSVC C++03 result-return rule (hidden pointer or registers) over a feature matrix.

    gen.py --cl CL_EXE --prefix WINEPREFIX --out DIR --toolchain COMMIT --wine-debs FILE

MEASUREMENT: cl.exe is run under Wine; the table is a measurement until it is a recipe stage keyed on a pinned `wine` deb profile and on cl's sha256.
Deterministic: the output is a function of (this script, cl.exe's bytes, wine-debs file, toolchain id); no clock, no host path in the output.

Matrix. Feature (one at a time) x depth x size, plus plain aggregates as controls.
  features  C user default ctor | CC user copy ctor | CA user copy assign | D user dtor | V virtual function | BE empty base | BN non-empty base
            PR private data member | PT protected data member | REF reference member | SM static data member (control) | MF non-virtual member function (control)
  depth     d0  the feature is on the returned class S      d1  S has a member of type M, M has the feature
            d1a S has a member M[1]                          d2  S has a member N, N has a member M, M has the feature
  size      the data bytes of the feature class: 1 (char), 2 (short), 4 (int), 8 (2 int), 12 (3 int); REF adds a reference member, V a vptr, BN a base int:
            the size column is that nominal data size, `sizeof` is cl's own sizeof(S).
Every case is a namespace Nk holding the types, `S f();`, `char sz[sizeof(S)];`, a caller `void c1(){ f(); }` (the primary signal: no local, no argument of its own) and a caller `void c(){ S x = f(); use(&x); }` (the cross-check).

The primary rule, applied to every case by the structure of c1 (`void c1(){ f(); }`, f only declared, no declared argument, no local): count the `push` instructions of the whole PROC.
  none, or a tail `jmp f` with none                                                       =>  REGS    (no argument is passed: the result comes back in eax/edx)
  exactly one, `push R`, where R's last write before it is `lea R, DWORD PTR <name>[esp|ebp..]`, and it comes before the single `call f`  =>  HIDDEN  (the pushed address is the hidden first argument)
  anything else (another push count, a push of something that is no frame address, not exactly one call or jmp to f)  =>  UNREAD, the case is named and the generator exits 1; no guess.
Cross-check (column `post`): in c (which keeps the result alive in x) the nearest `push R` before the call whose R was written by `lea` of a local => hidden, else regs; and after the call
the result is loaded through eax (`[eax`, hidden) or not stored from eax/edx at all, else stored from eax/edx (regs). The two readings of c must agree with each other and with the primary result;
a disagreement is listed as INCONSISTENT in the table and the generator exits 1.
A case cl refuses to compile is SKIPPED with cl's first error line.
"""
import argparse, hashlib, os, re, subprocess, sys

FEATS = ['PLAIN', 'C', 'CC', 'CA', 'D', 'V', 'BE', 'BN', 'PR', 'PT', 'REF', 'SM', 'MF']
DEPTHS = ['d0', 'd1', 'd1a', 'd2']
SIZES = [1, 2, 4, 8, 12]
MEMBERS = {1: [('char', 'm0')], 2: [('short', 'm0')], 4: [('int', 'm0')], 8: [('int', 'm0'), ('int', 'm1')], 12: [('int', 'm0'), ('int', 'm1'), ('int', 'm2')]}


def klass(tag, feat, size):
    mem = MEMBERS[size]
    pre, head, body = '', 'struct %s' % tag, []
    if feat == 'BE': pre = 'struct %sBase {};\n' % tag; head += ' : %sBase' % tag
    if feat == 'BN': pre = 'struct %sBase { int x; };\n' % tag; head += ' : %sBase' % tag
    if feat == 'PR': body.append('private:')
    if feat == 'PT': body.append('protected:')
    body += ['%s %s;' % m for m in mem]
    if feat == 'REF': body.append('int &r;')
    if feat == 'C': body.append('%s() {}' % tag)
    if feat == 'CC': body.append('%s(const %s &o) : %s {}' % (tag, tag, ', '.join('%s(o.%s)' % (n, n) for _, n in mem)))
    if feat == 'CA': body.append('%s &operator=(const %s &o) { %s return *this; }' % (tag, tag, ' '.join('%s = o.%s;' % (n, n) for _, n in mem)))
    if feat == 'D': body.append('~%s() {}' % tag)
    if feat == 'V': body.append('virtual void v();')
    if feat == 'SM': body.append('static int s;')
    if feat == 'MF': body.append('void mf();')
    return pre + head + ' { ' + ' '.join(body) + ' };\n'


def case_source(k, feat, depth, size):
    t = klass('M' if depth != 'd0' else 'S', feat, size)
    if depth == 'd1': t += 'struct S { M m; };\n'
    if depth == 'd1a': t += 'struct S { M m[1]; };\n'
    if depth == 'd2': t += 'struct N { M m; };\nstruct S { N n; };\n'
    return 'namespace N%d {\n%sS f();\nchar sz[sizeof(S)];\nvoid c1() { f(); }\nvoid c() { S x = f(); use(&x); }\n}\n' % (k, t)


def cases():
    out = [('PLAIN', 'd0', s) for s in SIZES]
    out += [(f, d, s) for f in FEATS[1:] for d in DEPTHS for s in SIZES]
    return out


def instrs(lines):
    for l in lines:
        l = l.split(';')[0].rstrip()
        if l.strip() and not l.rstrip().endswith(('PROC', 'ENDP')) and l[:1] in ' \t':
            yield l.strip()


def proc(asm, name, k):
    m = re.search(r'^\?%s@N%d@@YAXXZ PROC.*?^\?%s@N%d@@YAXXZ ENDP' % (name, k, name, k), asm, re.S | re.M)
    return list(instrs(m.group(0).split('\n'))) if m else None


def primary(ins, k):
    """REGS / HIDDEN by the whole-PROC push count of c1, or None (anything else)"""
    if ins is None: return None
    ci = [i for i, l in enumerate(ins) if re.match(r'(call|jmp)\s+\?f@N%d@@' % k, l)]
    if len(ci) != 1: return None
    pushes = [i for i, l in enumerate(ins) if re.match(r'push\b', l)]
    if not pushes: return 'REGS'
    if len(pushes) != 1 or not ins[ci[0]].startswith('call') or pushes[0] > ci[0]: return None
    m = re.fullmatch(r'push\s+(\w+)', ins[pushes[0]])
    if not m: return None
    w = [l for l in ins[:pushes[0]] if re.match(r'(mov|lea|xor|add|sub)\s+%s\s*,' % m.group(1), l)]
    return 'HIDDEN' if w and re.match(r'lea\s+%s\s*,\s*DWORD PTR \S*\[(esp|ebp)' % m.group(1), w[-1]) else None


def classify(asm, k):
    """(primary, cross) or None: primary = the whole-PROC rule on c1; cross = HIDDEN/REGS when both readings of c agree, else DISAGREE"""
    p = primary(proc(asm, 'c1', k), k)
    ins = proc(asm, 'c', k)
    if p is None or ins is None: return None
    ci = [i for i, l in enumerate(ins) if re.match(r'call\s+\?f@N%d@@' % k, l)]
    if len(ci) != 1: return None
    i = ci[0]
    pre = 'REGS'
    j = i - 1
    while j >= 0 and not re.match(r'push\s+\w+$', ins[j]): j -= 1       # the nearest push before the call
    if j >= 0:
        r = ins[j].split()[1]
        w = [l for l in ins[:j] if re.match(r'(mov|lea|xor|add|sub)\s+%s\s*,' % r, l)]   # its register's last write
        if w and re.match(r'lea\s+%s\s*,\s*DWORD PTR (?:_x\$|\$T\d+)\[esp' % r, w[-1]): pre = 'HIDDEN'
    after = []
    for l in ins[i + 1:]:
        if l.startswith('call') or l.startswith('ret'): break
        after.append(l)
    thru = any('[eax' in l for l in after)                                 # a load through the returned pointer
    stores = any(re.match(r'mov\s+[^,]*\[[^\]]*\]\s*,\s*(eax|edx|ax|dx|al|dl)\b', l) for l in after)
    post = 'HIDDEN' if thru or not stores else 'REGS'
    return p, (pre if pre == post else 'DISAGREE')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cl', required=True); ap.add_argument('--prefix', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--toolchain', required=True); ap.add_argument('--wine-debs', required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    env = dict(os.environ, WINEPREFIX=a.prefix, WINEARCH='win32', WINEDEBUG='-all')
    allc = cases()
    skipped = {}
    while True:
        keep = [i for i in range(len(allc)) if i not in skipped]
        src = 'void use(void *);\n' + ''.join(case_source(i, *allc[i]) for i in keep)
        open(os.path.join(a.out, 'cases.cpp'), 'w').write(src)
        r = subprocess.run(['wine', a.cl, '/nologo', '/c', '/O2', '/FAs', '/Facases.asm', '/Focases.obj', 'cases.cpp'], cwd=a.out, env=env, capture_output=True, text=True)
        errs = re.findall(r'^cases\.cpp\((\d+)\) : (?:fatal )?error ([^\n]*)', r.stdout, re.M)
        if r.returncode == 0: break
        if not errs: print('cl failed:\n' + r.stdout + r.stderr, file=sys.stderr); sys.exit(2)
        lines = src.split('\n'); starts = {}
        for i in keep: starts[i] = src[:src.index('namespace N%d {' % i)].count('\n') + 1
        bad = {}
        for ln, msg in errs:
            ln = int(ln); cand = [i for i in keep if starts[i] <= ln]
            if cand: bad.setdefault(max(cand), msg)
        if not bad: print('cl failed:\n' + r.stdout, file=sys.stderr); sys.exit(2)
        skipped.update(bad)
    asm = open(os.path.join(a.out, 'cases.asm')).read()
    asm = re.sub(r'^; Listing generated by.*\n', '', asm, flags=re.M)
    asm = re.sub(r'[Zz]:\\[^\s]*?cases\.cpp', 'cases.cpp', asm)   # no host path in the output
    open(os.path.join(a.out, 'cases.asm'), 'w').write(asm)
    cl_sha = hashlib.sha256(open(a.cl, 'rb').read()).hexdigest()
    ban = subprocess.run(['wine', a.cl], env=env, capture_output=True, text=True)
    banner = [l for l in (ban.stdout + ban.stderr).split('\n') if l.startswith('Microsoft')][0].strip()
    wv = subprocess.run(['wine', '--version'], env=env, capture_output=True, text=True).stdout.strip()
    debs = open(a.wine_debs).read().rstrip('\n')
    rows, bad = [], 0
    for i, (f, d, s) in enumerate(allc):
        if i in skipped: rows.append('%s\t%s\t%d\t-\tSKIPPED\t-\t%s' % (f, d, s, skipped[i])); continue
        sz = re.search(r'\?sz@N%d@@3PADA\s+DB\s+([0-9A-Fa-f]+)H DUP' % i, asm)
        c = classify(asm, i)
        if sz is None or c is None: rows.append('%s\t%s\t%d\t-\tUNREAD\t-\t-' % (f, d, s)); bad += 1; continue
        ok = c[0] == c[1]; bad += not ok
        rows.append('%s\t%s\t%d\t%d\t%s\t%s\t%s' % (f, d, s, int(sz.group(1), 16), c[0], c[1], '' if ok else 'INCONSISTENT'))
    hdr = ['# MEASUREMENT: cl under Wine; not yet a test reference (it needs the recipe stage keyed on a pinned wine deb profile and on cl by sha256)',
           '# toolchain: fnv-nx-build %s' % a.toolchain, '# cl: %s' % banner, '# cl.exe sha256: %s' % cl_sha, '# wine: %s' % wv,
           '# wine debs (dpkg-query -W):'] + ['#   ' + l for l in debs.split('\n')] + [
           '# rule (result): whole-PROC push count of c1 `void c1(){ f(); }`: no push or tail jmp => REGS; exactly one `push R` with R = `lea` of a frame address before the single call => HIDDEN; else UNREAD',
           '# post: cross-check on c (the result kept in a local): nearest push + where the result is stored; must equal result',
           '# columns: feature depth size sizeof(S) result post flag']
    open(os.path.join(a.out, 'table.tsv'), 'w').write('\n'.join(hdr + rows) + '\n')
    n = lambda w: sum(1 for r in rows if r.split('\t')[4] == w)
    for r in rows:
        if r.split('\t')[4] == 'UNREAD': print('UNREAD case: ' + r, file=sys.stderr)
    print('cases %d: HIDDEN %d REGS %d SKIPPED %d UNREAD/INCONSISTENT %d' % (len(allc), n('HIDDEN'), n('REGS'), len(skipped), bad))
    sys.exit(1 if bad else 0)


if __name__ == '__main__': main()
