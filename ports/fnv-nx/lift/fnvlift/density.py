"""The density rules of the lifter as one configuration (a lifter configuration input of the liftsrc stage; `liftgen --density`): which of the four emission rules are in force.

    labels    density rule 1: a label line L_<addr>: ; only where an emitted goto names it (core.prune_labels)
    forms     density rule 2: the control transfers are the one-line forms FNVL_CALLX / CALLD / TAILX / JMPN / RET of rt/fnvl.h; off: the lifter writes each form's expansion in place
    flaglive  density rule 3: flag writes that are dead on every path are not emitted (flaglive.dropped)
    polls     the suspension polls (flaglive.poll_set, FNVL_POLL at every back-edge target and entry)

The default is every rule (the product pack). A configuration with a rule off is a MEASUREMENT configuration: the runtime and the tree are the same, only the lifter's text differs, so the bytes of the
four configurations are comparable. `forms` off expands the form by the text of its definition in rt/fnvl.h (read from that file, never copied): the semantics of the text is the same, it is only longer.
`polls` need `forms` (FNVL_POLL is a form)."""
import os
import re

RULES = ('labels', 'forms', 'flaglive', 'polls')
ON = frozenset(RULES)
_S = {'on': ON}


def configure(spec):
    """spec: None / '' = every rule, else the comma-separated rules in force ('-' = none)"""
    on = ON if not spec else frozenset() if spec == '-' else frozenset(spec.split(','))
    bad = on - ON
    if bad:
        raise ValueError('--density: unknown rule %s (rules: %s)' % (sorted(bad), ','.join(RULES)))
    if 'polls' in on and 'forms' not in on:
        raise ValueError('--density: polls need forms (FNVL_POLL is a form)')
    _S['on'] = on


def on(rule):
    return rule in _S['on']


_DEF = {}
_FORMS = ('CALLX', 'CALLD', 'TAILX', 'JMPN', 'RET')


def _defs():
    """{form: (parameter names, body)} of the FNVL_<form> macros of rt/fnvl.h"""
    if not _DEF:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'rt', 'fnvl.h')
        text = open(path).read()
        for f in _FORMS:
            m = re.search(r'^#define FNVL_%s\(([^)]*)\) (.*)$' % f, text, re.M)
            if not m:
                raise ValueError('rt/fnvl.h defines no FNVL_%s' % f)
            _DEF[f] = ([p.strip() for p in m[1].split(',')], m[2].rstrip())
    return _DEF


def _args(s, i):
    """the top-level comma-separated arguments of the call whose '(' is at s[i-1], and the index after its ')'"""
    depth, out, cur = 1, [], i
    while depth:
        c = s[i]
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
            if not depth:
                break
        elif c == ',' and depth == 1:
            out.append(s[cur:i]); cur = i + 1
        i += 1
    out.append(s[cur:i])
    return out, i + 1


def expand_forms(text):
    """TEXT with every FNVL_<form>(...) call replaced by its definition's body, the parameters substituted by the call's arguments (word-wise)"""
    d = _defs()
    pat = re.compile(r'\bFNVL_(%s)\(' % '|'.join(_FORMS))
    out, pos = [], 0
    while True:
        m = pat.search(text, pos)
        if not m:
            out.append(text[pos:])
            return ''.join(out)
        args, end = _args(text, m.end())
        params, body = d[m[1]]
        if len(args) != len(params):
            raise ValueError('FNVL_%s: %d arguments, the definition takes %d' % (m[1], len(args), len(params)))
        sub = dict(zip(params, args))
        out.append(text[pos:m.start()])
        out.append(re.sub(r'\b(%s)\b' % '|'.join(params), lambda k: sub[k[1]], body))
        pos = end
