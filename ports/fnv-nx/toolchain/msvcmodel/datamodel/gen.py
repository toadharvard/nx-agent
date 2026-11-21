#!/usr/bin/env python3
"""DATAMODEL generator. The one source of DATAMODEL.json / DATAMODEL.md.
Inputs (nothing else is read):
  --docs DIR      the MicrosoftDocs/cpp-docs checkout at the commit pinned in sources.json (its `docs` directory is DIR/docs)
  --tmtexi FILE   gcc/doc/tm.texi of the pinned GCC 13.3.0 tarball (sources.json: sha256 of the tarball)
  facts.json      facts: id, property, the MS value, the doc page + a quotation that must occur in that page, what msvcmodel gives, the mechanism
                  and the check (a DM("<id>") static_assert case, or a flag of guest_compiler.json); tm: every entry of tm.texi's
                  "Storage Layout", "Type Layout" and "Type Layout"-following C++ ABI hooks -> {fact: id} or {not_layout: reason}.
Rules checked (any failure exits 1, nothing is written):
  U1 the universe is the tm.texi entries of the pinned file: each has exactly one mapping, no mapping lacks an entry;
  D1 every fact quotes its page: the quotation occurs in the page (whitespace-normalised); the citation records page path and line;
  M1 every `fact` a tm entry names exists; every fact is named by a tm entry or by `own` (properties the exe has that tm.texi has no entry for);
  C1 every fact has a check.
Output is deterministic (sorted, no timestamps)."""
import argparse, hashlib, json, os, re, sys


def universe(path):
    t = open(path, encoding='utf-8').read().split('\n')
    out, on = [], False
    for l in t:
        m = re.match(r'@node (.*?)(,|$)', l)
        if m:
            on = m.group(1).strip() in ('Storage Layout', 'Type Layout', 'C++ ABI')
        if on:
            m = re.match(r'@def(mac|macx)\s+(\w+)', l)
            if m:
                out.append(m.group(2))
                continue
            m = re.match(r'@def(?:typefn|typefnx|typevr|typevrx)\b.*?\b(TARGET_[A-Z0-9_]+)\b', l)
            if m:
                out.append(m.group(1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mirror', help='the fnv-nx-build tools/cpp-docs tree: docs/<pages named by facts.json>, tm.texi, MANIFEST.json')
    ap.add_argument('--docs')
    ap.add_argument('--tmtexi')
    ap.add_argument('--subset', help='write the mirror tree (the pages facts.json names, tm.texi, MANIFEST.json) from --docs/--tmtexi into this directory, then stop')
    ap.add_argument('--check', action='store_true', help='compare with the committed --out (and its .md), never write')
    ap.add_argument('--facts', required=True, help='facts.json (a declared input of the caller; no default beside this file)')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    F = json.load(open(a.facts, encoding='utf-8'))
    if a.mirror:
        a.docs, a.tmtexi = a.mirror, os.path.join(a.mirror, 'tm.texi')
        man = json.load(open(os.path.join(a.mirror, 'MANIFEST.json')))
        src = json.load(open(os.path.join(os.path.dirname(a.facts), 'sources.json')))
        if man['cpp_docs_commit'] != src['cpp_docs']['commit'] or man['gcc_tarball_sha256'] != src['gcc']['tarball_sha256']:
            print('M0 the mirror MANIFEST is not the pin of sources.json')
            return 1
        for f, h in man['files'].items():
            if hashlib.sha256(open(os.path.join(a.mirror, f), 'rb').read()).hexdigest() != h:
                print('M0 %s differs from its MANIFEST sha256' % f)
                return 1
    if not a.docs or not a.tmtexi:
        print('need --mirror, or --docs and --tmtexi')
        return 1
    if a.subset:
        import shutil
        files = {}
        for page in sorted({d['page'] for f in F['facts'] for d in f['doc']}):
            dst = os.path.join(a.subset, 'docs', page)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(os.path.join(a.docs, 'docs', page), dst)
            files['docs/' + page] = hashlib.sha256(open(dst, 'rb').read()).hexdigest()
        shutil.copyfile(a.tmtexi, os.path.join(a.subset, 'tm.texi'))
        files['tm.texi'] = hashlib.sha256(open(a.tmtexi, 'rb').read()).hexdigest()
        src = json.load(open(os.path.join(os.path.dirname(a.facts), 'sources.json')))
        json.dump({'cpp_docs_commit': src['cpp_docs']['commit'], 'gcc_tarball_sha256': src['gcc']['tarball_sha256'], 'files': files},
                  open(os.path.join(a.subset, 'MANIFEST.json'), 'w'), indent=1, sort_keys=True)
        return 0
    errs = []
    uni = universe(a.tmtexi)
    tm = F['tm']
    for u in uni:
        if u not in tm:
            errs.append('U1 tm.texi entry %s has no mapping' % u)
    for k in tm:
        if k not in uni:
            errs.append('U1 mapping %s is not a tm.texi entry' % k)
    facts = {f['id']: f for f in F['facts']}
    if len(facts) != len(F['facts']):
        errs.append('duplicate fact id')
    named = set(F.get('own', []))
    for k, v in tm.items():
        if ('fact' in v) == ('not_layout' in v):
            errs.append('M1 %s: exactly one of fact / not_layout' % k)
        if 'fact' in v:
            if v['fact'] not in facts:
                errs.append('M1 %s names unknown fact %s' % (k, v['fact']))
            named.add(v['fact'])
        elif not v.get('not_layout', '').strip():
            errs.append('M1 %s: empty reason' % k)
    for i in facts:
        if i not in named:
            errs.append('M1 fact %s is named by no tm entry and is not `own`' % i)
    rows = []
    cache = {}
    for i in sorted(facts):
        f = facts[i]
        cit = []
        for d in f['doc']:
            p = os.path.join(a.docs, 'docs', d['page'])
            if p not in cache:
                cache[p] = open(p, encoding='utf-8').read().split('\n') if os.path.exists(p) else None
            lines = cache[p]
            if lines is None:
                errs.append('D1 %s: page %s missing' % (i, d['page']))
                continue
            q = re.sub(r'\s+', ' ', d['quote'])
            hit = [n + 1 for n, l in enumerate(lines) if q in re.sub(r'\s+', ' ', l)]
            if not hit:
                errs.append('D1 %s: quotation not in %s: %s' % (i, d['page'], d['quote'][:70]))
                continue
            cit.append({'page': 'docs/' + d['page'], 'line': hit[0], 'quote': d['quote']})
        if not f['doc'] and not f.get('exe_source'):
            errs.append('D1 %s has neither a doc quotation nor an exe_source' % i)
        c = f.get('check')
        if not c:
            errs.append('C1 %s has no check' % i)
        elif isinstance(c, str):
            if not re.fullmatch(r'static_assert DM\("%s"\) in toolchain/tests/ldbl/datamodel\.cpp' % re.escape(i), c):
                errs.append('C1 %s: a string check is `static_assert DM("<id>") in toolchain/tests/ldbl/datamodel.cpp`, anything else is {kind, file, id}' % i)
        elif not (isinstance(c, dict) and sorted(c) == ['file', 'id', 'kind'] and c['kind'] == 'test' and all(isinstance(v, str) and v for v in c.values())
                  and os.path.isfile(os.path.join(os.path.dirname(os.path.abspath(a.facts)), '..', '..', '..', c['file']))):
            errs.append('C1 %s: check must be a DM string or {"kind": "test", "file": <existing path from ports/fnv-nx>, "id": <function>}' % i)
        r = {k: f[k] for k in ('id', 'property', 'msvc', 'gcc', 'mechanism', 'kind', 'status', 'check') if k in f}
        r['flag'] = f.get('flag')
        if f.get('exe_source'):
            r['exe_source'] = f['exe_source']
        r['citations'] = cit
        rows.append(r)
    byid = {r['id']: r for r in rows}
    types, seen, pend = [], set(), []
    tables = {}   # type name -> {page: size from the documented table line that names it}
    for r in rows:
        for c in r['citations']:
            for l in cache[os.path.join(a.docs, c['page'])]:
                m = re.match(r'\|(.*)\|\s*(\d+) bytes?\s*\|\s*$', l)
                if m:
                    for n in re.findall(r'\*\*`([a-z_ ]+)`\*\*', m.group(1)):
                        tables.setdefault(n, {})[c['page']] = int(m.group(2))
    for t in F['types']:
        n = t['type']
        if n in seen:
            errs.append('T1 type %s listed twice' % n)
        seen.add(n)
        if not (set(t) in ({'type', 'row'}, {'type', 'row', 'size_as'})):
            errs.append('T1 %s: entry is {type, row} or {type, row, size_as}' % n)
            continue
        if t['row'] not in byid:
            errs.append('T1 %s names unknown row %s' % (n, t['row']))
            continue
        row = byid[t['row']]
        if not row['citations'] or row['status'] != 'closed':
            errs.append('T1 %s: row %s is not a closed row with a documentation citation' % (n, t['row']))
        pend.append((t, row))
    for t, row in pend:
        n = t['type']
        if 'size_as' in t:
            ref = t['size_as']
            if ref not in tables:
                errs.append('T3 %s: size_as %s has no documented size' % (n, ref))
                continue
            sizes = set(tables[ref].values())
        else:
            sizes = set(tables.get(n, {}).values())
        if len(sizes) != 1:
            errs.append('T3 %s: the documented size tables give %s (one value required)' % (n, sorted(sizes)))
            continue
        size = sizes.pop()
        quotes = ' '.join(c['quote'] for c in row['citations'])
        # signedness: only integral types have it. C++ docs: integers are signed unless `unsigned`; char by the row's own quote
        # (S01: promoted as from signed char), wchar_t by its row's quote (S02: unsigned), bool is unsigned.
        if n in ('float', 'double', 'long double', 'pointer'):
            sg = None
        elif n.startswith('unsigned') or n == 'bool':
            sg = False
        elif n == 'wchar_t':
            sg = False if 'unsigned' in quotes else None
        elif n == 'char':
            sg = True if 'signed char' in quotes else None
        else:
            sg = True
        if n in ('wchar_t', 'char') and sg is None:
            errs.append('T4 %s: row %s does not document its signedness' % (n, row['id']))
            continue
        e = {'type': n, 'size': size, 'align': min(size, 8)}
        if sg is not None:
            e['signed'] = sg
        e['row'] = row['id']
        e['citations'] = row['citations']
        types.append(e)
    if errs:
        print('\n'.join(errs))
        return 1
    tmrows = [{'entry': k, **tm[k]} for k in sorted(tm)]
    out = {'format': 'fnvnx-datamodel/2', 'target': F['target'], 'pins': json.load(open(os.path.join(os.path.dirname(a.facts), 'sources.json'))),
           'rows': rows, 'types': types, 'tm': tmrows, 'crossing_forbidden': F['crossing_forbidden']}
    target = a.out + '.new' if a.check else a.out
    json.dump(out, open(target, 'w', encoding='utf-8'), indent=1, ensure_ascii=False, sort_keys=False)
    open(target, 'a').write('\n')
    md = ['# DATAMODEL (generated by datamodel/gen.py; do not edit)', '',
          'Target: %s. Pins: cpp-docs %s, GCC 13.3.0 tm.texi.' % (F['target'], out['pins']['cpp_docs']['commit']), '',
          '## Facts', '', '| id | MSVC | msvcmodel | mechanism | status | source |', '|---|---|---|---|---|---|']
    for r in rows:
        src = '; '.join('%s:%d' % (c['page'], c['line']) for c in r['citations']) or 'exe: ' + r.get('exe_source', '')
        md.append('| %s | %s | %s | %s | %s | %s |' % (r['id'], r['msvc'], r['gcc'], r['mechanism'], r['status'], src))
    md += ['', '## Types (%d)' % len(types), '', '| type | size | align | signed | row |', '|---|---|---|---|---|']
    for t in types:
        md.append('| %s | %d | %d | %s | %s |' % (t['type'], t['size'], t['align'], {True: 'yes', False: 'no'}.get(t.get('signed'), '-'), t['row']))
    md += ['', '## tm.texi entries (%d)' % len(tmrows), '', '| entry | fact or reason |', '|---|---|']
    for t in tmrows:
        md.append('| %s | %s |' % (t['entry'], t['fact'] if 'fact' in t else 'not a layout fact: ' + t['not_layout']))
    md += ['', '## crossing_forbidden', '', out['crossing_forbidden']['predicate'], '']
    for t in out['crossing_forbidden']['terms']:
        md.append('- %s (rows %s): `%s`. %s' % (t['id'], ', '.join(t['rows']), t['gcc'], t['reason']))
    mdt = os.path.splitext(target)[0] + '.md'
    open(mdt, 'w', encoding='utf-8').write('\n'.join(md) + '\n')
    if a.check:
        bad = [n for n, t in ((a.out, target), (os.path.splitext(a.out)[0] + '.md', mdt)) if open(n, encoding='utf-8').read() != open(t, encoding='utf-8').read()]
        for t in (target, mdt):
            os.remove(t)
        if bad:
            print('G1 %s is not what the generator writes from the pinned inputs' % ', '.join(bad))
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
