"""s1b_patches.py - per-site source patches of stream S1b-patches (docs/PATCHES.md).

Sites that no rule can fix: declarations that only worked on x86 by coincidence (a caller relying on ecx = this,
S2's `x86-inconsistent` verdicts), arg-less calls of the x87 register helpers `_ftol2_sse` / `_CIsqrt` (operand in
ST0), and decomp bugs that MSVC tolerated. Each fix was read from the x86 code of the function (the pristine decomp
built with MSVC 2008, byte-identical to the exe, D12) and is a small structured replacement in `patches/*.s1b`,
keyed by file and `// @fnv` address, with the evidence next to it.

Patch file format (UTF-8 text):

    @@ <RULE> <source path> <0x address | file>      one site; RULE is XINC1, FTOL1, DBUG1 or RET1
    # evidence ...                                    at least one line: what the x86 code shows
    <<<                                               a hunk: old text (verbatim, from the decomp) ...
    ...
    ===                                               ... replaced by ...
    ...
    >>>

The old text of a hunk must occur exactly once in the window of its site: from the `// @fnv` tag before the one of
the site (or the start of the file) to the tag after it (or the end of the file); `file` sites search the whole
file. Matching is exact first, then whitespace-insensitive (other rules may have re-spaced the text). A hunk that does
not apply is counted as `<RULE>-MISS` and reported on stderr; the file is left as it was for that hunk.

Definitions whose signature a patch changes get another MSVC decoration: `patches/units_renames.tsv` lists them (and
the new decorated reference names) as measured from MSVC objects of the patched units; `fix_units_json()` applies it
to a prepared tree's build/units.json (POST below: needs a post-copy hook in prepare.py, see docs/PATCHES.md).
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PATCH_DIR = os.path.normpath(os.path.join(HERE, '..', '..', 'patches'))
TAG = re.compile(r'//[ \t]*@fnv[ \t]+0x([0-9a-fA-F]+)')

# Byte-parity classes: MSVC compiles the patched function to the bytes of the exe (tests/s1b/msvc_check.py).
MSVC_RULES = ('XINC1', 'FTOL1', 'DBUG1', 'RET1')
# Gated classes: deliberate Switch-only behaviour changes, exempt from the MSVC byte check, applied by prepare only
# (the Switch build), every hunk behind the runtime gate (docs/PATCHES.md, "Patch class MT1").
GATED = ('MT1',)
MT1_GUARD = 'FNVNX_MT1_ON'          # fnvnx_compat.h: fnvnx_mt1_on() != 0; the engine's copy of bMTRendering, off by default
MT1_MODES = ('guarded', 'inert')    # `=== guarded`: the new text tests the gate itself; `=== inert`: adds text only


def mt1_emit(old, new, mode):
    """The text an MT1 hunk puts in place of the matched old text: a statement hunk (no mode) becomes
    `if (FNVNX_MT1_ON) { new } else { old }`, so with the gate off the code is the decomp's."""
    if mode:
        return new
    return 'if (%s) { /* fnv-nx MT1 */\n%s\n} else {\n%s\n}' % (MT1_GUARD, new, old)


def msvc_sites(sites):
    """The sites the MSVC byte check covers (every class but the gated ones)."""
    return [s for s in sites if s.rule in MSVC_RULES]


class Site:
    __slots__ = ('rule', 'path', 'addr', 'why', 'hunks', 'modes', 'origin')

    def __init__(self, rule, path, addr, origin):
        self.rule, self.path, self.addr, self.origin = rule, path, addr, origin
        self.why, self.hunks, self.modes = [], [], []


def parse_patch_file(fname):
    """-> [Site] of one patch file (format in the module docstring)."""
    sites, cur, mode, old, new, hmode = [], None, None, [], [], ''
    for n, line in enumerate(open(fname, encoding='utf-8'), 1):
        body = line.rstrip('\n')
        where = '%s:%d' % (os.path.basename(fname), n)
        if mode == 'old':
            m = re.match(r'^===(?: (\w+))?$', body)
            if m:
                hmode = m.group(1) or ''
                if hmode and (cur.rule not in GATED or hmode not in MT1_MODES):
                    raise ValueError('%s: hunk mode %r is only for the gated classes %s (%s)'
                                     % (where, hmode, ','.join(GATED), ','.join(MT1_MODES)))
                mode = 'new'
            else:
                old.append(line)
            continue
        if mode == 'new':
            if body == '>>>':
                o, nw = ''.join(old).rstrip('\n'), ''.join(new).rstrip('\n')
                if hmode == 'guarded' and MT1_GUARD not in nw:
                    raise ValueError('%s: a guarded MT1 hunk must test %s itself' % (where, MT1_GUARD))
                if hmode == 'inert' and o not in nw:
                    raise ValueError('%s: an inert MT1 hunk only adds text: its new text must contain the old' % where)
                cur.hunks.append((o, nw))
                cur.modes.append(hmode)
                mode, old, new = None, [], []
            else:
                new.append(line)
            continue
        if body.startswith('@@ '):
            f = body[3:].split()
            if len(f) != 3:
                raise ValueError('%s: site header needs RULE PATH ADDR' % where)
            addr = None if f[2] == 'file' else int(f[2], 16)
            cur = Site(f[0], f[1], addr, where)
            sites.append(cur)
        elif body == '<<<':
            if cur is None:
                raise ValueError('%s: hunk outside a site' % where)
            mode = 'old'
        elif body.startswith('#'):
            if cur is not None and not cur.hunks:
                cur.why.append(body[1:].strip())
        elif body.strip():
            raise ValueError('%s: unexpected line %r' % (where, body))
    if mode:
        raise ValueError('%s: unterminated hunk' % fname)
    for s in sites:
        if not s.why or not s.hunks:
            raise ValueError('%s: site without evidence or hunks' % s.origin)
    return sites


def load_sites(patch_dir=PATCH_DIR):
    sites = []
    if os.path.isdir(patch_dir):
        for name in sorted(os.listdir(patch_dir)):
            if name.endswith('.s1b'):
                sites += parse_patch_file(os.path.join(patch_dir, name))
    return sites


SITES = load_sites()
BY_FILE = {}
for _s in SITES:
    BY_FILE.setdefault(_s.path, []).append(_s)


def window(text, addr):
    """(start, end) of the text a site at `addr` may edit, or None when the tag is not in the text."""
    if addr is None:
        return 0, len(text)
    tags = [(m.start(), int(m.group(1), 16)) for m in TAG.finditer(text)]
    idx = [i for i, (_, a) in enumerate(tags) if a == addr]
    if not idx:
        return None
    i = idx[0]
    return (tags[i - 1][0] if i > 0 else 0), (tags[i + 1][0] if i + 1 < len(tags) else len(text))


def flexible(old):
    """Regex for `old` that tolerates other whitespace (at least one blank where old has one between words)."""
    out, prev_word = [], False
    for tok in re.findall(r'\s+|\w+|[^\w\s]', old):
        if tok.isspace():
            out.append(r'\s+' if prev_word else r'\s*')
            continue
        if out and out[-1] == r'\s+' and not re.match(r'\w', tok):
            out[-1] = r'\s*'
        elif out and not out[-1].startswith(r'\s') and not (prev_word and re.match(r'\w', tok)):
            out.append(r'\s*')
        out.append(re.escape(tok))
        prev_word = bool(re.match(r'\w', tok))
    return re.compile(''.join(out))


def find_once(text, old, lo, hi):
    """(start, end) of the single occurrence of `old` in text[lo:hi] (exact, then flexible), else (None, why)."""
    seg = text[lo:hi]
    n = seg.count(old)
    if n == 1:
        i = seg.index(old)
        return lo + i, lo + i + len(old)
    if n > 1:
        return None, 'ambiguous (%d exact matches)' % n
    ms = list(flexible(old).finditer(seg))
    if len(ms) == 1:
        return lo + ms[0].start(), lo + ms[0].end()
    return None, 'not found' if not ms else 'ambiguous (%d matches)' % len(ms)


def apply_sites(path, text, stats, rule, report=None):
    p = path.replace(os.sep, '/')
    for key, sites in BY_FILE.items():
        if not p.endswith('/' + key) and p != key:
            continue
        for s in sites:
            if s.rule != rule:
                continue
            for k, (old, new) in enumerate(s.hunks):
                hmode = s.modes[k] if k < len(s.modes) else ''
                w = window(text, s.addr)
                res = (None, 'tag 0x%08x not in the file' % s.addr) if w is None else find_once(text, old, *w)
                if res[0] is None:
                    stats[rule + '-MISS'] += 1
                    msg = 's1b_patches: %s hunk %d of %s (%s) does not apply: %s' % (rule, k + 1, s.origin, key, res[1])
                    if report is not None:
                        report.append(msg)
                    else:
                        print(msg, file=sys.stderr)
                    continue
                a, b = res
                if rule in GATED:
                    new = mt1_emit(text[a:b], new, hmode)
                text = text[:a] + new + text[b:]
                stats[rule] += 1
    return text


def r_xinc1(path, text, stats):
    """XINC1: per-site fixes of declarations that only worked on x86 (patches/xinc1_*.s1b): a caller that relied on
    ecx = this (a member function declared as a free function, a callback table filled with member functions, a
    hidden result pointer); MSVC code of the patched function is unchanged (docs/PATCHES.md)."""
    return apply_sites(path, text, stats, 'XINC1')


def r_ftol1(path, text, stats):
    """FTOL1: arg-less calls of the x87 register helpers `_ftol2_sse` (FUN_00ec62c0) / `_CIsqrt` (helper_value) ->
    fnvnx_ftol2(expr) / sqrt(expr), the expression that the x86 code leaves in ST0 (patches/ftol1_*.s1b)."""
    return apply_sites(path, text, stats, 'FTOL1')


def r_dbug1(path, text, stats):
    """DBUG1: decomp bugs MSVC tolerated (S4b, J-0030): an 8-byte local type whose constructor writes 12 bytes,
    ThrowInfo objects passed by value instead of by address, one SpeedTree exception class across units, a Win32
    import declared with fewer parameters than the API whose missing argument the x86 code left on the stack
    (patches/dbug1_*.s1b)."""
    return apply_sites(path, text, stats, 'DBUG1')


def r_ret1(path, text, stats):
    """RET1: the evident return of non-void functions that end without one (patches/ret1_*.s1b): the trailing
    expression statement on each fall-through path becomes `return <it>;` where MSVC compiles that to the same code,
    i.e. the x86 function already returned that value (eax / ST0 left by it); prepare's FALL1 trap stays behind it,
    unreachable."""
    return apply_sites(path, text, stats, 'RET1')


def r_mt1(path, text, stats):
    """MT1: gated Switch behaviour changes (patches/mt1_*.s1b, S15-threads: multithreaded culling). Not MSVC byte
    parity: exempt from msvc_check.py, applied in the Switch build only (class `platform`), every hunk behind
    FNVNX_MT1_ON (the engine's copy of bMTRendering, off by default), so with the gate off the code is the decomp's."""
    return apply_sites(path, text, stats, 'MT1')


RULES = [r_xinc1, r_ftol1, r_dbug1, r_ret1, r_mt1]

# ---------------------------------------------------------------------------------------------------------- units.json

RENAMES = os.path.join(PATCH_DIR, 'units_renames.tsv')


def load_renames(path=RENAMES):
    """[(source, kind, address, old name, new name)]; kind 'def' (a recipe function's symbol) or 'ref' (a decorated
    reference name the patched unit uses for that address, added to the unit's symbols)."""
    out = []
    if os.path.exists(path):
        for line in open(path, encoding='utf-8'):
            if line.startswith('#') or not line.strip():
                continue
            f = line.rstrip('\n').split('\t')
            out.append((f[0], f[1], f[2], f[3], f[4]))
    return out


def fix_units_data(data, stats, renames=None):
    """Apply the renames to a units.json object (in place)."""
    renames = load_renames() if renames is None else renames
    by_src = {}
    for r in renames:
        by_src.setdefault(r[0], []).append(r)
    for u in data['units']:
        for src, kind, addr, old, new in by_src.get(u['source'], []):
            if kind == 'def':
                for fn in u['functions']:
                    if fn['address'] == addr and fn['symbol'] == old:
                        fn['symbol'] = new
                        stats['UNITS-DEF'] += 1
            elif kind == 'ref' and new not in u['symbols']:
                u['symbols'][new] = addr
                stats['UNITS-REF'] += 1
    return data


def fix_units_json(out_root, stats, files=None):
    """POST hook: rewrite <out_root>/build/units.json with patches/units_renames.tsv."""
    p = os.path.join(out_root, 'build', 'units.json')
    if not os.path.exists(p):
        return
    data = fix_units_data(json.load(open(p)), stats)
    json.dump(data, open(p, 'w'))


POST = [fix_units_json]
