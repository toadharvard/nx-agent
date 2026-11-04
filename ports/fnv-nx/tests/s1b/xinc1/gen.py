"""gen.py AN_TSV OUTDIR [CALLERS]: candidate XINC1 patches for the mechanical patterns (scratch; the patches hold only our
shell declarations plus the decomp text the hunks match, the same as the committed patch files).

  A  free function whose whole code is `jmp callee` with ecx untouched -> thiscall shell member with the callee's
     parameters (portable types) and result, body `return FUN_<callee>(a1..)`.
  B  free function calling the callee with its own incoming ecx -> shell member (same parameters), calls on this.
  C  member / ctor / dtor calling the callee with ecx = this -> `((S*)this)->FUN_<callee>(..)`.
Rows that fit none are written to OUTDIR/manual.tsv.
"""
import collections, json, os, re, sys
D = os.environ['DECOMP'] + '/'
an, outdir = sys.argv[1], sys.argv[2]
only = set(sys.argv[3].split(',')) if len(sys.argv) > 3 else None
os.makedirs(outdir, exist_ok=True)
FSIZE = {}
for _u in json.load(open(D + 'build/units.json'))['units']:
    for _f in _u['functions']:
        FSIZE[(_u['source'], _f['address'])] = _f['size']
TAG = re.compile(r'//[ \t]*@fnv[ \t]+0x([0-9a-fA-F]+)')
FREE = {'cdecl', 'stdcall', 'fastcall', 'cdecl-c', 'stdcall-c', 'static-cdecl', 'static-stdcall'}
HAS_THIS = {'member', 'member-virt', 'ctor', 'dtor', 'deldtor'}


def split_params(s):
    out, depth, cur = [], 0, ''
    for ch in s:
        if ch in '<(': depth += 1
        if ch in '>)': depth -= 1
        if ch == ',' and depth == 0:
            out.append(cur.strip()); cur = ''
        else:
            cur += ch
    if cur.strip(): out.append(cur.strip())
    return [] if out == ['void'] else out


def parse_sig(dem):
    """demangled MSVC signature -> (ret, cc, qualified name, [params], kind)"""
    s = re.sub(r'^(public|private|protected): ', '', dem)
    s = re.sub(r'^(virtual|static) ', '', s)
    m = re.match(r'^(.*?)\b(__cdecl|__stdcall|__fastcall|__thiscall) (.+?)\((.*)\)( const)?$', s)
    if not m:
        return None
    ret, cc, name, params = m.group(1).strip(), m.group(2), m.group(3), m.group(4)
    return ret, cc, name, split_params(params)


SCALAR = {'int', 'unsigned int', 'long', 'unsigned long', 'bool', 'char', 'signed char', 'unsigned char', 'short',
          'unsigned short', 'wchar_t', 'float', 'double', '__int64', 'unsigned __int64', 'void'}


def portable(t):
    """type of a def parameter / result -> a type any unit can spell with the same ABI, or None."""
    t0 = re.sub(r'\bconst\b|\bvolatile\b', '', t).strip()
    t0 = re.sub(r'\s+', ' ', t0)
    if t0.endswith('*') or t0.endswith('&') or '(' in t0:
        return 'void*'
    if t0 in SCALAR:
        return t0
    if t0.startswith('enum '):
        return 'int'
    return None


def ctype(t):
    """demangled type -> C++ spelling MSVC 2008 accepts"""
    t = t.replace('>>', '> >').replace('>>', '> >')
    t = re.sub(r'\bunion (__m(?:64|128[di]?))\b', r'\1', t)      # SSE vector types are typedefs under GCC (sse2neon)
    if '`' in t or "'" in t or '<unnamed' in t or '<lambda' in t:
        return None
    return t


def find_tag(text, addr):
    tags = [(m.start(), int(m.group(1), 16)) for m in TAG.finditer(text)]
    for k, (pos, a) in enumerate(tags):
        if a == addr:
            lo = tags[k - 1][0] if k else 0
            hi = tags[k + 1][0] if k + 1 < len(tags) else len(text)
            return pos, lo, hi
    return None


def func_span(text, pos, hi):
    """(header start, header end (at '{'), body end (after '}')) of the function after the tag at pos."""
    i = text.index('\n', pos) + 1
    while True:
        j = text.index('\n', i)
        line = text[i:j].strip()
        if line.startswith('//') or not line or line.startswith('/*') or line.startswith('#'):
            i = j + 1; continue
        break
    h0 = i
    depth, k = 0, h0
    paren = 0
    while k < hi:
        c = text[k]
        if c == '(': paren += 1
        elif c == ')': paren -= 1
        elif c == ';' and paren == 0:
            return None
        elif c == '{' and paren == 0:
            break
        k += 1
    if k >= hi:
        return None
    h1 = k
    depth = 0
    while k < len(text):
        c = text[k]
        if c == '{': depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return h0, h1, k + 1
        elif c == '/' and text[k:k + 2] == '//':
            k = text.index('\n', k); continue
        elif c == '"':
            k += 1
            while text[k] != '"':
                k += 2 if text[k] == '\\' else 1
        elif c == "'":
            k += 1
            while text[k] != "'":
                k += 2 if text[k] == '\\' else 1
        k += 1
    return None


def unique_ctx(text, lo, hi, a, b):
    """smallest run of whole lines around text[a:b] that occurs once in text[lo:hi] -> (start, end)"""
    s = text.rfind('\n', 0, a) + 1
    e = text.find('\n', b)
    e = len(text) if e < 0 else e
    for _ in range(12):
        seg = text[s:e]
        if text[lo:hi].count(seg) == 1:
            return s, e
        s2 = text.rfind('\n', 0, max(s - 1, 0)) + 1
        if s2 >= lo:
            s = s2
        else:
            e2 = text.find('\n', e + 1)
            e = e2 if e2 > 0 else len(text)
    return None


rows = []
hdr = None
for line in open(an):
    if line.startswith('#'):
        hdr = line[1:].rstrip('\n').split('\t'); continue
    rows.append(dict(zip(hdr, line.rstrip('\n').split('\t'))))
by_caller = collections.OrderedDict()
for r in rows:
    by_caller.setdefault((r['src'], r['caller']), []).append(r)

out_files = collections.defaultdict(list)
manual = []
stats = collections.Counter()


def bail(rs, why):
    for r in rs:
        manual.append((r['src'], r['caller'], r['callee'], why))
    stats['manual'] += 1


for (src, caller), rs in by_caller.items():
    if only and caller not in only:
        continue
    text = open(D + src, encoding='utf-8', errors='surrogateescape').read()
    ft = find_tag(text, int(caller, 16))
    if not ft:
        bail(rs, 'no tag'); continue
    pos, lo, hi = ft
    sp = func_span(text, pos, hi)
    if not sp:
        bail(rs, 'no function after tag'); continue
    h0, h1, b1 = sp
    header = text[h0:h1].rstrip()
    body = text[h1:b1]
    ck = rs[0]['ckind']
    if any(e != 'this' for r in rs for e in r['ecx'].split(',')):
        bail(rs, 'ecx not this'); continue
    insns = {x for r in rs for x in r['insn'].split(',')}
    if not insns <= {'call', 'jmp'}:
        bail(rs, 'insn ' + ','.join(sorted(insns))); continue
    if ck in HAS_THIS:
        pat = 'C'
    elif ck in FREE:
        pat = 'A' if insns == {'jmp'} and len(rs) == 1 and FSIZE.get((src, caller)) == 5 else 'B'
    else:
        bail(rs, 'caller kind ' + ck); continue
    shell = 'FUN_%08x_s1b' % int(caller, 16)
    if shell in text:
        bail(rs, 'shell name in use'); continue
    methods, notes, hunks, ok = [], [], [], True
    callmap = []
    for r in rs:
        ref = parse_sig(r['ref'])
        dsig = parse_sig(r['def'])
        if r['dkind'] in ('ctor', 'dtor') or dsig is None:
            m = re.match(r'^(?:public|private|protected): (?:virtual )?__thiscall (.+?)\((.*)\)$', r['def'])
            if not m:
                ok = False; notes.append('def unparsed ' + r['def']); break
            dsig = ('void*' if r['dkind'] == 'ctor' else 'void', '__thiscall', m.group(1), split_params(m.group(2)))
        if ref is None:
            ok = False; notes.append('ref unparsed ' + r['ref']); break
        dparams = list(dsig[3])
        if dsig[1] == '__fastcall':
            drop = 2 if 'edx-dummy' in r['classes'] else 1
            dparams = dparams[drop:]
        elif dsig[1] != '__thiscall':
            ok = False; notes.append('def cc ' + dsig[1]); break
        fname = 'FUN_%s' % r['callee']
        dname = dsig[2]
        if pat == 'A':
            ptypes = [portable(t) for t in dparams]
            rtype = portable(dsig[0]) if dsig[0] else 'void'
            if None in ptypes or rtype is None:
                ok = False; notes.append('non-portable def types ' + r['def']); break
            methods.append('%s %s(%s);' % (rtype, fname, ', '.join(ptypes)))
            callmap.append((r, ref, fname, rtype, ptypes))
        else:
            ptypes = [ctype(t) for t in ref[3]]
            rtype = ctype(ref[0])
            if None in ptypes or rtype is None:
                ok = False; notes.append('unspellable ref types ' + r['ref']); break
            if len(ref[3]) != len(dparams):
                notes.append('arity ref %d def %d' % (len(ref[3]), len(dparams)))
                ok = False; break
            # narrow reference type where the def takes a pointer / wider value: use the def's portable type
            for k2, (rt, dt) in enumerate(zip(ref[3], dparams)):
                pr, pd = portable(rt), portable(dt)
                if pr in ('bool', 'char', 'signed char', 'unsigned char', 'short', 'unsigned short') and \
                        pd not in ('bool', 'char', 'signed char', 'unsigned char', 'short', 'unsigned short'):
                    notes.append('narrow p%d %s vs %s' % (k2, rt, dt)); ok = False
            if not ok: break
            methods.append('%s %s(%s);' % (rtype, fname, ', '.join(ptypes)))
            callmap.append((r, ref, fname, rtype, ptypes))
    if not ok:
        bail(rs, '; '.join(notes)); continue
    # dedupe methods (one callee referenced under two names)
    seen = []
    for m in methods:
        if m not in seen: seen.append(m)
    methods = seen
    cmt = ', '.join('0x%s %s' % (r['callee'], re.sub(r'^.*__thiscall |^.*__fastcall |\(.*$', '', r['def'])) for r, *_ in callmap)
    # the caller's own signature for A/B
    if pat in ('A', 'B'):
        m = re.match(r'^(?P<pre>.*?)(?P<name>[\w:~]+)\s*\((?P<params>.*)\)\s*(?P<post>.*)$', header, re.S)
        if not m:
            bail(rs, 'header unparsed'); continue
        pre = m.group('pre')
        selfname = m.group('name').split('::')[-1]
        if any(selfname == x.split('(')[0].split()[-1] for x in methods):
            bail(rs, 'caller name equals a callee shell name'); continue
        pre2 = re.sub(r'\b(__cdecl|__stdcall|__fastcall|static|inline|__inline|__forceinline|extern)\b', ' ', pre)
        pre2 = re.sub(r'\s+', ' ', pre2).strip()
        if 'extern "C"' in pre or 'template' in pre or m.group('post').strip():
            bail(rs, 'header form ' + header[:80]); continue
        cparams = m.group('params')
        if pat == 'A':
            r, ref, fname, rtype, ptypes = callmap[0]
            names = ['a%d' % (k + 1) for k in range(len(ptypes))]
            cparams_new = ', '.join('%s %s' % (t, n) for t, n in zip(ptypes, names))
            cret = rtype
            body_call = ('' if rtype == 'void' else 'return ') + '%s(%s);' % (fname, ', '.join(names))
            # body must be one call statement of the reference name
            nm = ref[2].split('::')[-1]
            bm = re.match(r'^\{\s*(?:extern [^;{}]*;\s*)?(?:return\s+)?(?:[\w:]*::)?%s\s*\([^;{}]*\)\s*;\s*\}$' % re.escape(nm), body.strip())
            if not bm:
                bail(rs, 'A body not a single call: ' + re.sub(r'\s+', ' ', body)[:80]); continue
            decl_self = '%s %s(%s);' % (cret, selfname, ', '.join(ptypes))
        else:
            cret = pre2
            cparams_new = cparams
            if re.search(r'\.\.\.', cparams):
                bail(rs, 'varargs caller'); continue
            ptxt = re.sub(r'\s+', ' ', cparams.strip())
            decl_self = '%s %s(%s);' % (cret, selfname, ptxt)
        st = 'struct %s { %s %s };' % (shell, ' '.join(methods), decl_self)
        nl = header.count('\n')
        new_header = '%s /* fnv-nx XINC1: %s */ %s %s::%s(%s)' % (st, cmt, cret, shell, selfname, cparams_new.replace('\n', ' ') if pat == 'A' else re.sub(r'\s*\n\s*', ' ', cparams_new))
        new_header += '\n' * nl
        hunks.append((header, new_header))
        if pat == 'A':
            nm = callmap[0][1][2].split('::')[-1]
            mm = list(re.finditer(r'(?:return\s+)?(?:[\w:]*::)?%s\s*\([^;{}]*\)\s*;' % re.escape(nm), body))[-1]
            hunks.append(('__BODY__', (mm.group(0), body_call)))
    else:
        st = 'struct %s { %s };' % (shell, ' '.join(methods))
        hunks.append((header, '%s /* fnv-nx XINC1: %s */ %s' % (st, cmt, header)))
    # calls in the body (B, C)
    if pat in ('B', 'C'):
        nrel = sum(len(r['insn'].split(',')) for r in rs)
        ncalls = 0
        newbody = body
        for r, ref, fname, rtype, ptypes in callmap:
            nm = ref[2].split('::')[-1]
            rx = re.compile(r'(?<![\w.>])(?:(?:::)?[\w]+::)*%s\s*\(' % re.escape(nm))
            found = list(rx.finditer(newbody))
            # a member access like p->Name( is excluded by the lookbehind; ns::Name( is included
            ncalls += len(found)
            rep = (fname + '(') if pat == 'B' else '((%s*)this)->%s(' % (shell, fname)
            newbody = rx.sub(lambda m_: rep, newbody)
        if ncalls != nrel:
            bail(rs, 'calls in source %d vs relocations %d' % (ncalls, nrel)); continue
        hunks.append(('__BODYALL__', (body, newbody)))
    stats[pat] += 1
    out_files[src].append((caller, pat, rs, hunks, text, (h0, h1, b1)))

# write patch files: hunks as whole changed lines, unique within the window
def changed_lines(old, new):
    """line-level hunks between old and new (same number of lines)"""
    ol, nl_ = old.split('\n'), new.split('\n')
    if len(ol) != len(nl_):
        return None
    return [(k, o, n) for k, (o, n) in enumerate(zip(ol, nl_)) if o != n]


groups = collections.defaultdict(list)


def short(d):
    return re.sub(r'^(public|private|protected): ', '', d)


for src, items in out_files.items():
    g = src.split('/')[1] if not src.startswith('src/gamebryo2.2') else src.split('/')[3]
    if src.startswith('src/staging/'): g = 'staging'
    if src.startswith('src/havok'): g = 'havok'
    if src.startswith('src/fallout'): g = 'fallout'
    if src.count('/') == 1: g = 'misc'
    for caller, pat, rs, hunks, text, (h0, h1, b1) in items:
        pos, lo, hi = find_tag(text, int(caller, 16))
        ev = []
        if pat == 'A':
            ev.append('x86 %s: the whole function is `jmp 0x%s` with ecx untouched; 0x%s is %s: a thiscall forwarder,' % (
                caller, rs[0]['callee'], rs[0]['callee'], short(rs[0]['def'])))
            ev.append('so a member with the callee\'s parameters and result.')
        elif pat == 'B':
            ev.append('x86 %s (%s): ecx is never written before the %s%s of %s; the callee%s %s: the function' % (
                caller, rs[0]['ckind'], 'tail jump' if rs[0]['insn'] == 'jmp' else 'call', 's' if len(rs) > 1 or ',' in rs[0]['insn'] else '',
                ', '.join('0x' + r['callee'] for r in rs), 's are' if len(rs) > 1 else ' is', '; '.join(short(r['def']) for r in rs)))
            ev.append('passes its own ecx on, i.e. it is a thiscall member with the same parameters.')
        else:
            ev.append('x86 %s (%s): ecx = this at the call%s of %s; %s: a call on this.' % (
                caller, rs[0]['ckind'], 's' if len(rs) > 1 or ',' in rs[0]['insn'] else '',
                ', '.join('0x' + r['callee'] for r in rs), '; '.join(short(r['def']) for r in rs)))
        out = ['@@ XINC1 %s %s' % (src, caller)]
        for l in ev:
            out.append('# ' + l)
        # hunks: header, then body lines
        hh = [(text[h0:h1].rstrip(), hunks[0][1])]
        body_old = text[h1:b1]
        body_new = body_old
        for kind, val in hunks[1:]:
            if kind == '__BODY__':
                k_ = body_new.rfind(val[0])
                body_new = body_new[:k_] + val[1] + body_new[k_ + len(val[0]):]
                body_new = re.sub(r'extern [^;{}]*\b%s\s*\([^;{}]*\)\s*;' % re.escape(rs[0]['ref'].split('(')[0].split('::')[-1].split()[-1]), '', body_new)
            elif kind == '__BODYALL__':
                body_new = val[1]
        hl = []
        cl = changed_lines(body_old, body_new)
        if cl is None:
            raise SystemExit('line count changed ' + caller)
        bl_old, bl_new = body_old.split('\n'), body_new.split('\n')
        if text[lo:hi].count(hh[0][0]) != 1 and '\n' in text[h0:h1]:
            # a prototype repeats the header: take the header with its opening brace
            ho = text[h0:h1 + 1]
            if text[lo:hi].count(ho) == 1:
                hh = [(ho, hh[0][1] + text[h0 + len(hh[0][0]):h1 + 1])]
                bl_old[0], bl_new[0] = bl_old[0][1:], bl_new[0][1:]
                h1 += 1
        if text[lo:hi].count(hh[0][0]) != 1:
            manual.append((src, caller, rs[0]['callee'], 'header not unique')); continue
        # one-line function: header and the `{` line form one hunk (stopping before a closing brace: FALL1)
        if '\n' not in text[h0:h1]:
            old0 = text[h0:h1] + bl_old[0]
            new0 = hh[0][1] + text[h0 + len(hh[0][0]):h1] + bl_new[0]
            if old0.rstrip().endswith('}') and new0.rstrip().endswith('}'):
                old0, new0 = old0.rstrip()[:-1].rstrip(), new0.rstrip()[:-1].rstrip()
            hh = [(old0, new0)]
            cl = [(k, o, n) for k, o, n in cl if k != 0]
        # apply the header hunk, then each body line hunk in turn on the running text (contexts unique there)
        run = text.replace(hh[0][0], hh[0][1], 1) if text[lo:hi].count(hh[0][0]) == 1 else None
        bad = run is None
        b0 = h1 + len(hh[0][1]) - len(hh[0][0])
        blen = b1 - h1
        for k, o, n in cl:
            if bad: break
            _, lo1, hi1 = find_tag(run, int(caller, 16))
            rl = run[b0:b0 + blen].split('\n')
            if k >= len(rl) or rl[k] != o:
                bad = True; break
            a = b0 + sum(len(x) + 1 for x in rl[:k])
            uc = unique_ctx(run, lo1, hi1, a, a + len(o))
            if uc is None:
                bad = True; break
            s, e = uc
            seg = run[s:e]
            nseg = seg[:a - s] + n + seg[a - s + len(o):]
            hl.append((seg, nseg))
            run = run[:s] + nseg + run[e:]
            blen += len(nseg) - len(seg)
        if bad:
            manual.append((src, caller, rs[0]['callee'], 'body line not unique')); continue
        for o, n in hh + hl:
            out += ['<<<', o, '===', n, '>>>']
        groups[g].append('\n'.join(out))
for g, sites in groups.items():
    with open(os.path.join(outdir, 'xinc1_r2_%s.s1b' % g), 'w') as f:
        f.write('# XINC1 round 2 (S2c run f12) - %s: callers that relied on ecx = this (docs/PATCHES.md).\n\n' % g)
        f.write('\n\n'.join(sites) + '\n')
with open(os.path.join(outdir, 'manual.tsv'), 'w') as f:
    for m in manual:
        f.write('\t'.join(m) + '\n')
print(dict(stats), 'manual rows', len(manual))
