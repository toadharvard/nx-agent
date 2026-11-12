"""mscgate.py - the dual-compile check of prepare.py (a total check, decided on the preprocessor token stream).

RULE. A conditional group whose condition mentions `_MSC_VER` and that a prepare rule introduced is permitted only in
the form
    #ifdef _MSC_VER   (or `#if defined(_MSC_VER)`, `#if defined _MSC_VER`; or, with the branches swapped,
    ...cl text...      `#ifndef _MSC_VER`, `#if !defined(_MSC_VER)`, `#if !defined _MSC_VER`)
    #else
    ...GCC text...     which contains the empty GNU asm statement `__asm__ ( "" )` (the ASM rule: the N3 verdict of the
    #endif             function is `asm`) or a call `fnvnx_lifted (` of a declared-only lifted function (verdict lifted)
Every other introduced group that mentions `_MSC_VER` (a branch missing, an `#elif`, a condition that is not one of the six
spellings above, a GCC branch without the asm or lifted token form) is a violation: a function whose cl text and GCC text
differ is not the function the N1/N2 gates certify, so the class is closed by the gates (the function is lifted), not by
text for GCC. Conditionals present in the decomp's input are passed through unchanged and are not judged.

IDENTIFICATION of the input's groups: by token stream, not by spelling in a line. A file is lexed to its preprocessor
tokens (comments, line splices, string and character literals handled); a group is identified by its header: the
directive name and condition tokens. For every header h, among the output's groups with header h at most as many may be
of a non-permitted form as the input has groups with header h (the others were introduced by a rule). Assumption: a rule
does not delete a decomp conditional and create a non-permitted one with the same header (the per-rule check in
check_rule_edit sees every edit of every rule at the moment it is made and does not rest on this assumption).
"""
import re
from collections import Counter

TOKEN = re.compile(r'''/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|[A-Za-z_]\w*|\d[\w.]*|\\\n|\n|[ \t\r\f\v]+|.''',
                   re.S)
COND_OPEN = ('if', 'ifdef', 'ifndef')
GATED = 'mscgate'


def lex(text):
    """[('dir', name, (tokens...))] for preprocessor directives, [('tok', token)] for every other token. Comments and
    white space are dropped; a directive runs to the first newline that no backslash splices."""
    out, i, n, bol = [], 0, len(text), True
    while i < n:
        m = TOKEN.match(text, i)
        t, i = m[0], m.end()
        if t == '\n':
            bol = True
        elif t[0] in ' \t\r\f\v' or t == '\\\n' or t.startswith('/*') or t.startswith('//'):
            pass
        elif t == '#' and bol:
            toks = []
            while i < n:
                m = TOKEN.match(text, i)
                u = m[0]
                if u == '\n':
                    break
                i = m.end()
                if u[0] in ' \t\r\f\v' or u == '\\\n' or u.startswith('//'):
                    continue
                if u.startswith('/*'):
                    continue
                toks.append(u)
            if toks and toks[0] == '#':      # `# #` never happens; keep the stream total
                toks = toks[1:]
            out.append(('dir', toks[0] if toks else '', tuple(toks[1:])))
            bol = False
            continue
        else:
            out.append(('tok', t))
            bol = False
    return out


def groups(text):
    """[group] for every conditional group of text, in order of its opening directive. A group is a dict: head (name,
    condition tokens), mentions (bool: `_MSC_VER` in the head or in an #elif), branches (the token lists between its
    directives, flattened: directive tokens inside count), own (names of its #else/#elif/#endif directives in order),
    complete (closed by its #endif)."""
    items, stack, res = lex(text), [], []
    for it in items:
        if it[0] == 'dir' and it[1] in COND_OPEN:
            g = {'head': (it[1], it[2]), 'mentions': '_MSC_VER' in it[2], 'branches': [[]], 'own': [], 'complete': False,
                 'conds': [it[2]]}
            res.append(g)
            stack.append(g)
            continue
        if it[0] == 'dir' and it[1] in ('elif', 'else', 'endif') and stack:
            g = stack[-1]
            g['own'].append(it[1])
            if it[1] == 'elif':
                g['conds'].append(it[2])
                g['mentions'] = g['mentions'] or '_MSC_VER' in it[2]
            if it[1] == 'endif':
                g['complete'] = True
                stack.pop()
            else:
                g['branches'].append([])
            continue
        flat = [it[1]] if it[0] == 'tok' else [it[1]] + list(it[2])
        for g in stack:
            g['branches'][-1].extend(flat)
    return res


def _cl_true(head):
    """True when the true branch of `head` is the one cl takes, False when it is GCC's, None for any other condition."""
    name, toks = head
    t = list(toks)
    if t.count('_MSC_VER') != 1:
        return None
    if name == 'ifdef' and t == ['_MSC_VER']:
        return True
    if name == 'ifndef' and t == ['_MSC_VER']:
        return False
    if name == 'if':
        for neg, forms in ((False, (['defined', '(', '_MSC_VER', ')'], ['defined', '_MSC_VER'])),
                           (True, (['!', 'defined', '(', '_MSC_VER', ')'], ['!', 'defined', '_MSC_VER']))):
            if t in forms:
                return not neg
    return None


def _has_seq(toks, seq):
    n = len(seq)
    return any(toks[i:i + n] == seq for i in range(len(toks) - n + 1))


def form(g):
    """'asm' / 'lifted' when g (a group that mentions _MSC_VER) is of the permitted form, else None."""
    if not g['complete'] or g['own'] != ['else', 'endif'] or len(g['branches']) != 2:
        return None
    pol = _cl_true(g['head'])
    if pol is None:
        return None
    gcc = g['branches'][1] if pol else g['branches'][0]
    if _has_seq(gcc, ['__asm__', '(', '""', ')']):
        return 'asm'
    if any(gcc[i] == 'fnvnx_lifted' and gcc[i + 1] == '(' for i in range(len(gcc) - 1)):
        return 'lifted'
    return None


def judge(before, after):
    """Lines (strings) naming each group of `after` that mentions _MSC_VER, is not of the permitted form and exceeds the
    groups of `before` with the same header; [] when there is none."""
    ga = [g for g in groups(after) if g['mentions']]
    if not ga:
        return []
    have = Counter(g['head'] for g in groups(before) if g['mentions'] and form(g) is None)
    bad = Counter()
    for g in ga:
        if form(g) is None:
            bad[g['head']] += 1
    out = []
    for h, k in sorted(bad.items()):
        if k > have[h]:
            out.append('%d conditional group(s) `#%s %s` not of the permitted form (%d in the input)' % (
                k - have[h], h[0], ' '.join(h[1]), have[h]))
    return out


def check_rule_edit(rule_name, before, after):
    """Called by prepare.py for an edit of one rule: [violation line naming the rule] ([] when the edit is clean)."""
    if before == after or '_MSC_VER' not in after:
        return []
    return ['rule %s: %s' % (rule_name, v) for v in judge(before, after)]


def check_tree(texts):
    """texts: {path: (input text, output text)}; [violation line] over the whole tree (total check)."""
    out = []
    for p in sorted(texts):
        before, after = texts[p]
        if '_MSC_VER' not in after:
            continue
        out += ['%s: %s' % (p, v) for v in judge(before, after)]
    return out


def census(texts):
    """The output's conditional groups that mention _MSC_VER, by origin: {'input': groups in the inputs, 'decomp': groups
    of the output that are not of a permitted rule form and are covered by the input's groups of that header, 'asm',
    'lifted': groups of the permitted form, 'other': the rest (a violation)}."""
    c = Counter()
    for p in sorted(texts):
        before, after = texts[p]
        gin = [g for g in groups(before) if g['mentions']]
        c['input'] += len(gin)
        gb = Counter(g['head'] for g in gin if form(g) is None)
        if '_MSC_VER' not in after:
            continue
        used = Counter()
        for g in groups(after):
            if not g['mentions']:
                continue
            f = form(g)
            if f is not None:
                c[f] += 1
            elif used[g['head']] < gb[g['head']]:
                used[g['head']] += 1
                c['decomp'] += 1
            else:
                c['other'] += 1
    return c
