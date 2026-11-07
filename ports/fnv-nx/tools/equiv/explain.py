#!/usr/bin/env python3
"""explain.py - S11's metric: differences of the MSVC equivalence gate that no explanation class covers.

    explain.py REPORT.md... [--table tools/equiv/explained.tsv] [--patches docs/PATCHES.md] [--list] [--json OUT]
    explain.py --git <clone of the artifacts repository> [--farms eq1,...,eq6] [--commit C12] [...]

REPORT.md: shard reports (`<commit12>-equiv.md` of reports/eq<k>) or merge_reports.py's summary; with --git the shard
reports are read as merge_reports.py --git reads them. Every differing function (unit, address, kind, rule) and every
unit the prepared side does not compile (unit, rule, first error) is matched against:

  * explained.tsv - one row per explanation: `class  what  kind  rule  unit  addr  error  reason`, tab-separated.
    `what` is `fn` (differing function), `unit` (no-build unit) or `wrapper` (one retarget pair: `addr` the
    pristine binding, `error` a regular expression on the prepared one); `kind` eh / retarget / code (`*` any);
    `rule` the attributed rule or group (`*` any; `a|b` alternatives); `unit` a regular expression on the unit path
    (`*` any; anchored at both ends); `addr` 0x... or `*`; `error` a regular expression searched in the first error
    (unit rows; `*` any). The first matching row wins.
  * a retarget whose report lists its relocation pairs (`## Retargets`) is explained by the class of the first
    `wrapper` row when every pair matches a `wrapper` row (whatever rule the gate attributed it to); a retarget
    without pairs falls back to the `fn` rows.
  * PATCHES.md, section "By-design MSVC differences (S11 gate)": S1b's by-design sites (source, function address),
    class `S1B-BYDESIGN`, whatever rule the gate attributed them to.

A FAILED or missing shard report (or a shard number below the reports' N that no report has) marks the result
INCOMPLETE, on the metric line and in --json (`complete`, `shard_problems`). Rows: an `fn` row needs a unit and an
address unless its kind is eh (identical with /EHs-c-: mechanical), so no row explains a future difference unread.

Prints the unexplained functions and units (with --list, also the explained ones by class) and the metric line
`unexplained: F functions, U no-build units (of N functions, M units)`. Exit 0 always; the number is the metric.
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import merge_reports  # noqa: E402

TABLE = HERE / 'explained.tsv'
CLASSES = HERE / 'classes.tsv'
PATCHES = PORT / 'docs/PATCHES.md'
FIELDS = ('cls', 'what', 'kind', 'rule', 'unit', 'addr', 'error', 'reason')


def load_table(path=TABLE):
    rows = []
    for n, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip() or line.startswith('#'):
            continue
        parts = line.split('\t')
        if len(parts) != len(FIELDS):
            raise SystemExit('explain: %s:%d: %d fields, want %d' % (path, n, len(parts), len(FIELDS)))
        r = dict(zip(FIELDS, (p.strip() for p in parts)))
        if r['what'] not in ('fn', 'unit', 'wrapper'):
            raise SystemExit('explain: %s:%d: what must be fn, unit or wrapper' % (path, n))
        r['unit_re'] = None if r['unit'] == '*' else re.compile(r['unit'] + r'\Z')
        r['error_re'] = None if r['error'] == '*' else re.compile(r['error'])
        r['rules'] = None if r['rule'] == '*' else set(r['rule'].split('|'))
        r['line'] = n
        if r['what'] == 'fn' and r['kind'] != 'eh' and r['unit'] == '*' and r['addr'] == '*':
            # a rule-wide row would explain every future difference of its rule unread (V1 E2); only the eh kind is
            # mechanical (identical when both sides build with /EHs-c-)
            raise SystemExit('explain: %s:%d: an fn row of kind %s needs a unit and an address' % (path, n, r['kind']))
        if r['what'] == 'unit' and r['error'] == '*' and r['unit'] == '*':
            raise SystemExit('explain: %s:%d: a unit row needs a unit or a first-error pattern' % (path, n))
        rows.append(r)
    return rows


def load_classes(path=CLASSES):
    """{class: (declared kind, reason)} of classes.tsv: the declared classes (D24)"""
    out = {}
    for n, line in enumerate(Path(path).read_text().splitlines(), 1) if Path(path).exists() else ():
        if not line.strip() or line.startswith('#'):
            continue
        p = [x.strip() for x in line.split('\t')]
        if len(p) != 3 or p[1] not in ('platform', 'compiler', 'by-design') or not p[2]:
            raise SystemExit('explain: %s:%d: want class, platform|compiler|by-design, reason' % (path, n))
        out[p[0]] = (p[1], p[2])
    return out


def load_bydesign(path=PATCHES):
    """{(source, addr)} of PATCHES.md's by-design table"""
    out, on = set(), False
    if not Path(path).exists():
        return out
    for line in Path(path).read_text().splitlines():
        if line.startswith('## '):
            on = line.startswith('## By-design MSVC differences')
            continue
        m = on and re.match(r'^\| *\w+ *\| *`([^`]+)` *\| *(0x[0-9a-fA-F]+) *\|', line)
        if m:
            out.add((m.group(1), m.group(2).lower()))
    return out


def parse_fn(line):
    p = line.split()
    if len(p) == 4 and p[1].startswith('0x'):
        return dict(unit=p[0], addr=p[1].lower(), kind=p[2], rule=p[3])
    return None


def parse_unit(line):
    m = re.match(r'^(\S+)\s+(\S+)\s+(.*)$', line.strip())
    return dict(unit=m.group(1), rule=m.group(2), error=m.group(3)) if m else None


def classify_pairs(pairs, table):
    """class of the first wrapper row when every 'A>B' pair matches a wrapper row, else None"""
    first = None
    for pair in pairs:
        a, _, b = pair.partition('>')
        hit = next((r for r in table if r['what'] == 'wrapper' and r['addr'].lower() == a.lower()
                    and (r['error_re'] is None or r['error_re'].search(b))), None)
        if hit is None:
            return None
        first = first or hit['cls']
    return first


def classify_fn(f, table, bydesign):
    if (f['unit'], f['addr']) in bydesign:
        return 'S1B-BYDESIGN'
    if f['kind'] == 'retarget' and f.get('pairs'):
        return classify_pairs(f['pairs'], table)
    for r in table:
        if r['what'] != 'fn':
            continue
        if r['kind'] != '*' and r['kind'] != f['kind']:
            continue
        if r['rules'] is not None and f['rule'] not in r['rules']:
            continue
        if r['unit_re'] is not None and not r['unit_re'].match(f['unit']):
            continue
        if r['addr'] != '*' and r['addr'].lower() != f['addr']:
            continue
        return r['cls']
    return None


def classify_unit(u, table):
    for r in table:
        if r['what'] != 'unit':
            continue
        if r['rules'] is not None and u['rule'] not in r['rules']:
            continue
        if r['unit_re'] is not None and not r['unit_re'].match(u['unit']):
            continue
        if r['error_re'] is not None and not r['error_re'].search(u['error']):
            continue
        return r['cls']
    return None


class Found(list):
    """explain()'s lists; they carry the shard problems of the reports they come from"""
    shard_problems = ()


def explain(reports, table, bydesign):
    fns, units, seen_f, seen_u = Found(), Found(), set(), set()
    fns.shard_problems = units.shard_problems = tuple(shard_problems(reports))
    pairs = {}
    for r in reports:
        for ln in r.get('retargets', []):
            p = ln.split()
            if len(p) == 3 and p[2] != '-':
                pairs[(p[0], p[1].lower())] = p[2].split(',')
    for r in reports:
        for ln in r['differing']:
            f = parse_fn(ln)
            if f and (f['unit'], f['addr']) not in seen_f:
                seen_f.add((f['unit'], f['addr']))
                f['pairs'] = pairs.get((f['unit'], f['addr']), [])
                f['cls'] = classify_fn(f, table, bydesign)
                fns.append(f)
        for ln in r['nobuild']:
            u = parse_unit(ln)
            if u and u['unit'] not in seen_u:
                seen_u.add(u['unit'])
                u['cls'] = classify_unit(u, table)
                units.append(u)
    return fns, units


def shard_problems(reports):
    """['shard K/N FAILED (name)', 'shard K/N missing', ...]: a metric over these reports covers only part of the
    gate (V1 E1: a FAILED or missing shard must not quietly lower the number)"""
    out, seen, of = [], set(), 0
    for r in reports:
        if r.get('missing') or r.get('failed') or not r.get('commit'):
            out.append('%s: %s' % (r.get('name', '?'), 'no report' if r.get('missing') else 'FAILED'))
            if r.get('shard'):
                seen.add(r['shard'])
            continue
        if r.get('shard'):
            seen.add(r['shard'])
            of = max(of, r.get('of') or 0)
    for k in range(1, of + 1):
        if k not in seen:
            out.append('shard %d/%d missing' % (k, of))
    return out


def metric(fns, units, reports=None, declared=None):
    """declared: classes.tsv. `open_code` (D24, stream EQ's metric with `units`): code-kind functions whose class is
    not declared (or that have none); every no-build unit counts, whatever its class."""
    probs = shard_problems(reports) if reports is not None else list(getattr(fns, 'shard_problems', ()))
    declared = load_classes() if declared is None else declared
    open_code = sum(f['kind'] == 'code' and f['cls'] not in declared for f in fns)
    return dict(functions=len(fns), units=len(units), unexplained_functions=sum(f['cls'] is None for f in fns),
                unexplained_units=sum(u['cls'] is None for u in units), open_code=open_code,
                open=open_code + len(units), complete=not probs, shard_problems=probs)


def render(fns, units, listing=False, reports=None, declared=None):
    declared = load_classes() if declared is None else declared
    m = metric(fns, units, reports, declared)
    out = ['# Unexplained MSVC gate differences (tools/equiv/explain.py)', '']
    if m['shard_problems']:
        out += ['**INCOMPLETE** - the metric covers only part of the gate: ' + '; '.join(m['shard_problems']), '']
    cf = Counter(f['cls'] or '-' for f in fns)
    cu = Counter(u['cls'] or '-' for u in units)
    cc = Counter(f['cls'] or '-' for f in fns if f['kind'] == 'code')
    out += ['| class | declared | functions | code | no-build units |', '|---|---|---:|---:|---:|']
    for c in sorted(set(cf) | set(cu), key=lambda c: (c == '-', -(cf[c] + cu[c]), c)):
        out.append('| %s | %s | %d | %d | %d |' % ('(unexplained)' if c == '-' else c,
                                                  declared[c][0] if c in declared else 'OPEN', cf[c], cc[c], cu[c]))
    out += ['', '## Unexplained functions (unit, address, kind, rule)', '', '```']
    out += ['%s  %s  %s  %s' % (f['unit'], f['addr'], f['kind'], f['rule']) for f in fns if f['cls'] is None]
    out += ['```', '', '## Unexplained no-build units (unit, rule, first error)', '', '```']
    out += ['%s  %s  %s' % (u['unit'], u['rule'], u['error']) for u in units if u['cls'] is None]
    out += ['```']
    if listing:
        out += ['', '## Explained', '', '```']
        out += ['%s  %s  %s  %s  %s' % (f['cls'], f['unit'], f['addr'], f['kind'], f['rule']) for f in fns if f['cls']]
        out += ['%s  %s  %s  %s' % (u['cls'], u['unit'], u['rule'], u['error']) for u in units if u['cls']]
        out += ['```']
    out += ['', '## Open code differences (not a declared class; unit, address, class, rule)', '', '```']
    out += ['%s  %s  %s  %s' % (f['unit'], f['addr'], f['cls'] or '-', f['rule']) for f in fns
            if f['kind'] == 'code' and f['cls'] not in declared]
    out += ['```']
    out += ['', 'open (D24): %d = %d code functions not in a declared class + %d no-build units' % (
        m['open'], m['open_code'], m['units'])]
    out += ['unexplained: %d functions, %d no-build units (of %d functions, %d units)%s' % (
        m['unexplained_functions'], m['unexplained_units'], m['functions'], m['units'],
        '' if m['complete'] else ' INCOMPLETE: ' + '; '.join(m['shard_problems']))]
    return '\n'.join(out) + '\n'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('reports', nargs='*')
    ap.add_argument('--git')
    ap.add_argument('--farms', default=','.join('eq%d' % k for k in range(1, 7)))
    ap.add_argument('--commit')
    ap.add_argument('--table', default=str(TABLE))
    ap.add_argument('--patches', default=str(PATCHES))
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--json')
    ap.add_argument('--out')
    a = ap.parse_args()
    if a.git:
        got, _ = merge_reports.from_git(a.git, a.farms.split(','), a.commit)
        reports = [merge_reports.parse(t, f) if t is not None else dict(merge_reports.parse('', f), missing=True)
                   for f, t in got]
    else:
        reports = [merge_reports.parse(Path(p).read_text(errors='replace'), Path(p).name) for p in a.reports]
    if not reports:
        raise SystemExit('explain: no reports')
    fns, units = explain(reports, load_table(a.table), load_bydesign(a.patches))
    text = render(fns, units, a.list, reports)
    if a.out:
        Path(a.out).write_text(text)
    if a.json:
        Path(a.json).write_text(json.dumps(dict(metric=metric(fns, units, reports), functions=fns, units=units),
                                           indent=1) + '\n')
    print(text, end='')


if __name__ == '__main__':
    main()
