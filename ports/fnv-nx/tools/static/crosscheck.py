#!/usr/bin/env python3
"""crosscheck.py - the static gate's sites against S13's differential-fuzzing ground truth (docs/STATIC_CHECKS.md,
"Cross-check with S13").

    crosscheck.py causes <campaign dir>... > divs.tsv
        one row per saved divergence of the campaigns with S13's own final cause (tools/difffuzz/report.reclassify;
        FNVNX_DFPROFILE selects the object set, as for report.py). Read-only.
    crosscheck.py matrix <prepared tree> <gate out> <divs.tsv> <campaign dir>... [--units LIST] [--out FILE.md]
        attributes every site of <gate out>/sites/*.tsv to the function around it (the `// @fnv 0x` tag before the
        site whose body - brace-matched from the tag - contains it), joins with the fuzzed functions
        (<campaign>/results.jsonl, status ok) and their divergence causes, and writes two tables:
          by S13 cause: divergent functions with that cause, and how many contain a site of the classes that predict
                        the cause (PREDICTS) - hits and misses; plus the classes most often present
          by class:     fuzzed functions containing a site of the class, how many diverged (any cause / a cause the
                        class predicts), against the base rate of all fuzzed functions
        --units restricts the fuzzed functions to the gate's sample units.
    crosscheck.py targets <prepared tree> <gate out> CLASS... > targets.jsonl
        S13 campaign targets: every function holding a site of those classes (unit index, source, address, symbol,
        tag S12:<classes>), for classes the fuzzer has not reached (F2U, ALIGN-IGNORED readers, ...).
Inputs and output are derived from the decomp: local only, never committed.
"""
import argparse
import bisect
import collections
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TAG = re.compile(r'//[ \t]*@fnv[ \t]+0x([0-9a-fA-F]+)')

# S13 cause -> gate classes whose sites predict it
PREDICTS = {
    'bool-repr': ['BOOL-PUN', 'PTRDIFF-EXACT'],
    'nan-bits': ['FLOAT-BITS'],
    'uninit-arm': ['W-UNINIT', 'OUTARG-UNINIT'],
    'uninit-both': ['W-UNINIT', 'OUTARG-UNINIT'],
    'uninit-x86': ['W-UNINIT', 'OUTARG-UNINIT'],
    'division': ['DIV-VAR'],
    'falloff?': ['W-RETURN'],
    'signedness': ['F2U', 'F2S', 'ENUM-UNSIGNED', 'ENUM-BITFIELD', 'SHIFT64-VAR', 'P2I-SIGNED', 'W-TYPE-LIMITS'],
    'signedness?': ['F2U', 'F2S', 'ENUM-UNSIGNED', 'ENUM-BITFIELD', 'SHIFT64-VAR', 'P2I-SIGNED', 'W-TYPE-LIMITS'],
    'call-flow': ['ORDER-ARGS', 'ORDER-OBJECT', 'ORDER-OPERATOR', 'ORDER-ASSIGN', 'ORDER-BINOP'],
    'float': ['F2U', 'F2S', 'FLOAT-BITS'],
    'ilp32-pointer': ['P2I-SIGNED', 'P2I64'],
}


def cmd_causes(dirs):
    sys.path.insert(0, os.path.join(HERE, '..', 'difffuzz'))
    import report  # noqa: E402  (S13's report.py: reclassify)
    out = sys.stdout
    out.write('campaign\tfile\tsource\taddress\tsymbol\tkind\thint\tcause\tsoft\n')
    for d in dirs:
        for p in sorted(glob.glob(os.path.join(d, 'div', '*.json'))):
            rec = json.load(open(p))
            rec['_now'] = None
            cause = report.reclassify(rec)
            dv = rec['divergence']
            out.write('\t'.join([os.path.basename(os.path.normpath(d)), os.path.basename(p), rec['source'],
                                 rec['address'], rec['symbol'], dv.get('kind', ''),
                                 (rec.get('hint') or {}).get('cause') or '', cause, str(bool(dv.get('soft')))]) + '\n')


def _skip_literal(text, i):
    """index after the string / char literal or comment starting at i, or None"""
    c = text[i]
    if c in '"\'':
        j = i + 1
        while j < len(text) and text[j] != c:
            j += 2 if text[j] == '\\' else 1
        return j + 1
    if text.startswith('//', i):
        j = text.find('\n', i)
        return len(text) if j < 0 else j
    if text.startswith('/*', i):
        j = text.find('*/', i + 2)
        return len(text) if j < 0 else j + 2
    return None


def function_spans(text):
    """[(first line, last line, address)] of the function after each `// @fnv` tag: from the tag to the brace that
    closes the first body opened after it (before the next tag)"""
    tags = [(m.start(), int(m.group(1), 16)) for m in TAG.finditer(text)]
    nl = [i for i, c in enumerate(text) if c == '\n'] if tags else []
    line_of = lambda pos: bisect.bisect_left(nl, pos) + 1     # noqa: E731
    spans = []
    for k, (pos, addr) in enumerate(tags):
        limit = tags[k + 1][0] if k + 1 < len(tags) else len(text)
        i, depth, end = text.find('\n', pos) + 1, 0, None
        while 0 < i < limit:
            j = _skip_literal(text, i)
            if j is not None:
                i = j
                continue
            c = text[i]
            if c == ';' and depth == 0:
                break                       # a declaration, not a definition
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    end = i
                    break
            i += 1
        if end is None:
            continue
        spans.append((line_of(pos), line_of(end), addr))
    return spans


def load_sites(out):
    """{class: [(file, line)]}"""
    sites = collections.defaultdict(list)
    for p in glob.glob(os.path.join(out, 'sites', '*.tsv')):
        cls = os.path.basename(p)[:-4]
        for line in open(p, encoding='utf-8', errors='replace').read().splitlines()[1:]:
            f = line.split('\t')
            if len(f) >= 2 and f[1].isdigit():
                sites[cls].append((f[0], int(f[1])))
    return sites


def cmd_matrix(a):
    only = None
    if a.units:
        only = {l.strip() for l in open(a.units) if l.strip()}
    # fuzzed functions (status ok) and divergence causes
    fuzzed = set()
    for d in a.campaigns:
        for line in open(os.path.join(d, 'results.jsonl')):
            r = json.loads(line)
            if r.get('status') == 'ok' and (only is None or r['source'] in only):
                fuzzed.add((r['source'], int(r['address'], 16)))
    causes = collections.defaultdict(set)
    for line in open(a.divs).read().splitlines()[1:]:
        f = line.split('\t')
        key = (f[2], int(f[3], 16))
        if only is None or f[2] in only:
            causes[key].add(f[7])
    # sites -> functions
    sites = load_sites(a.out_dir)
    spans_of = {}
    classes_of = collections.defaultdict(set)      # (source, addr) -> {class}
    attributed = collections.Counter()
    total = collections.Counter()
    for cls, rows in sites.items():
        for path, line in rows:
            total[cls] += 1
            if path not in spans_of:
                fp = os.path.join(a.tree, path)
                try:
                    spans_of[path] = function_spans(open(fp, encoding='utf-8', errors='replace').read())
                except OSError:
                    spans_of[path] = []
            for lo, hi, addr in spans_of[path]:
                if lo <= line <= hi:
                    classes_of[(path, addr)].add(cls)
                    attributed[cls] += 1
                    break
    universe = fuzzed | set(causes)
    diverged = {k for k in universe if causes.get(k)}
    md = ['# Static gate vs S13 difffuzz', '',
          '%d fuzzed functions (status ok)%s, %d with a divergence; %d gate classes, %d sites (%d inside a tagged '
          'function body).' % (len(fuzzed), ' in the sample units' if only is not None else '', len(diverged),
                               len(total), sum(total.values()), sum(attributed.values())), '',
          '## By S13 cause', '',
          '| S13 cause | divergent functions | predicting classes | hit | miss | classes present (functions) |',
          '|---|---:|---|---:|---:|---|']
    by_cause = collections.defaultdict(set)
    for k, cs in causes.items():
        for c in cs:
            by_cause[c].add(k)
    for c in sorted(by_cause, key=lambda c: -len(by_cause[c])):
        fns = by_cause[c]
        pred = PREDICTS.get(c, [])
        hit = sum(1 for k in fns if classes_of.get(k, set()) & set(pred))
        present = collections.Counter(cls for k in fns for cls in classes_of.get(k, set()))
        md.append('| %s | %d | %s | %d | %d | %s |' % (
            c, len(fns), ', '.join(pred) or '(none)', hit, len(fns) - hit,
            ', '.join('%s %d' % kv for kv in sorted(present.items(), key=lambda kv: (-kv[1], kv[0]))[:8]) or '-'))
    base = len(diverged & universe) / max(1, len(universe))
    md += ['', '## By gate class', '',
           'Base rate: %d of %d fuzzed functions diverged (%.0f%%).' % (len(diverged), len(universe), 100 * base), '',
           '| class | sites | in a function | fuzzed functions with it | diverged | rate | diverged with a predicted cause |',
           '|---|---:|---:|---:|---:|---:|---|']
    pred_of = collections.defaultdict(set)
    for c, ks in PREDICTS.items():
        for k in ks:
            pred_of[k].add(c)
    for cls in sorted(total, key=lambda c: -total[c]):
        fns = [k for k in universe if cls in classes_of.get(k, set())]
        dv = [k for k in fns if causes.get(k)]
        dp = collections.Counter(c for k in dv for c in causes[k] if c in pred_of[cls])
        md.append('| %s | %d | %d | %d | %d | %s | %s |' % (
            cls, total[cls], attributed[cls], len(fns), len(dv),
            '%.0f%%' % (100.0 * len(dv) / len(fns)) if fns else '-',
            ', '.join('%s %d' % kv for kv in dp.most_common()) or '-'))
    text = '\n'.join(md) + '\n'
    if a.out:
        open(a.out, 'w').write(text)
    sys.stdout.write(text)
    # machine-readable: function, classes, causes
    if a.out:
        with open(os.path.splitext(a.out)[0] + '.tsv', 'w') as f:
            f.write('source\taddress\tfuzzed\tcauses\tclasses\n')
            for k in sorted(universe):
                f.write('%s\t%08x\t%d\t%s\t%s\n' % (k[0], k[1], k in fuzzed, ','.join(sorted(causes.get(k, ()))),
                                                    ','.join(sorted(classes_of.get(k, ())))))


def cmd_targets(tree, out_dir, classes):
    """S13 campaign targets (difffuzz.py fuzz <file>): one JSON line per function holding a site of the classes"""
    units = json.load(open(os.path.join(tree, 'build', 'units.json')))['units']
    where = {}
    for i, u in enumerate(units):
        for f in u.get('functions', []):
            where.setdefault((u['source'], int(f['address'], 16)), (i, f['symbol']))
    sites = load_sites(out_dir)
    seen, spans_of = {}, {}
    for cls in classes:
        for path, line in sites.get(cls, []):
            if path not in spans_of:
                try:
                    spans_of[path] = function_spans(open(os.path.join(tree, path), encoding='utf-8',
                                                         errors='replace').read())
                except OSError:
                    spans_of[path] = []
            for lo, hi, addr in spans_of[path]:
                if lo <= line <= hi and (path, addr) in where:
                    seen.setdefault((path, addr), set()).add(cls)
                    break
    for (path, addr), cls in sorted(seen.items()):
        i, sym = where[(path, addr)]
        print(json.dumps(dict(unit=i, source=path, address='%08x' % addr, symbol=sym, tag='S12:' + '+'.join(sorted(cls)))))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'causes':
        cmd_causes(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == 'targets':
        cmd_targets(sys.argv[2], sys.argv[3], sys.argv[4:])
        return
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['matrix'])
    ap.add_argument('tree')
    ap.add_argument('out_dir')
    ap.add_argument('divs')
    ap.add_argument('campaigns', nargs='+')
    ap.add_argument('--units')
    ap.add_argument('--out')
    a = ap.parse_args()
    cmd_matrix(a)


if __name__ == '__main__':
    main()
