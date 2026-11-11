"""mutsrc.py: the one rule for every source mutation of the l6 tests (stale mutations must fail loudly, never silently).

A mutation is (where, old, new, count): `old` must occur in the text EXACTLY `count` times (default 1, the number the mutation was written for) and
the mutated text replaces every one of them. A pattern that matches zero times (the source moved or was refactored: the mutation no longer
mutates the point it was written for) or a different number of times (the mutation would hit another site than the one it names) is a
test failure that names the mutation, never a skipped mutation and never a mutation applied to "the first match".

    sys.path.append(<tests/l6>); import mutsrc
    mutsrc.apply(txt, old, new, "dev_chain.cpp: rotate")          -> mutated text or SystemExit("STALE MUTATION ...")
    mutsrc.apply_re(txt, pattern, repl, label)                      same for a regular expression (re.subn)
    mutsrc.check_all([(label, text, old[, count]), ...])            validate a whole table before any build: every stale one is listed at once
"""
import re, sys


def _fail(label, old, n, count):
    sys.exit("STALE MUTATION %s: the pattern occurs %d times, declared %d: %r" % (label, n, count, old[:200]))


def apply(txt, old, new, label, count=1):
    n = txt.count(old) if old else 0
    if n != count: _fail(label, old, n, count)
    return txt.replace(old, new)


def apply_re(txt, pattern, repl, label, count=1, flags=0):
    out, n = re.subn(pattern, repl, txt, flags=flags)
    if n != count: _fail(label, pattern, n, count)
    return out


def check_all(entries):
    """entries: (label, text, old[, count]); lists every stale one, then exits 1"""
    bad = []
    for e in entries:
        label, txt, old = e[0], e[1], e[2]; count = e[3] if len(e) > 3 else 1; n = txt.count(old) if old else 0
        if n != count: bad.append("STALE MUTATION %s: the pattern occurs %d times, declared %d: %r" % (label, n, count, old[:200]))
    if bad: sys.exit("\n".join(bad))
