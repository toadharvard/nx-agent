"""Emit provenance (density measurement): a SIDE TABLE of the bytes the emitter produced, by the emitter's own call structure. Off by default; the lifted text never depends on it
(the tag only reads the call stack and the lengths of what was emitted; `enable()` is the only switch, set by a measurement tool, never by the lifter).

A category is a pair (root, site):
  root = the handler that dispatch() ran for the instruction (the function directly under core.dispatch: the DSL emitter proof.lift_dsl.emit_form, a mnemonic handler, a shape handler),
         or `lift2` for a line the emit loop writes itself (labels, TRACE, the fall-through goto, the lock wrapper, the nonlocal loop) and `gs_guard` for the segment guard;
  site = the emitting source line, `function:line`, of the em() call (the emitter's own call structure; no category is named here), except the lines of the DSL Form block, whose site is the
         section of Form.to_c_inline that produced it (snapshots, temporaries, faults, register writes, flag writes, other-EFLAGS write, x87/SSE effects, stores) or `transfer`.
The text of one function is: the lines (each + one newline, none after the last), the rewrites core.lift2 / f_x87_ctl.fix_lines apply to those lines afterwards (their added bytes are
accounted to the rewriting site), the callee declarations, the x87 static-stack declaration and the rest of the frame. finish() checks that the parts add up to exactly len(text)."""
import linecache
import os
import sys

ON = False
LAST = None          # the record of the last function lift2 finished (None when off); a measurement tool takes it with take()
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(HERE)
_EMIT_FILES = None


def enable():
    global ON
    ON = True


def take():
    global LAST
    r, LAST = LAST, None
    return r


def _emitter_file(fn):
    return fn.startswith(HERE + os.sep) or fn == os.path.join(LIFT, 'proof', 'lift_dsl.py')


def classify(f, tag):
    """the category of an em() call made from frame f (the caller of Ctx.em); tag overrides the site (a region the emitter labelled itself)"""
    chain = []
    while f is not None:
        if _emitter_file(f.f_code.co_filename):
            chain.append(f)
            if f.f_code.co_name == 'lift2':
                break
        f = f.f_back
    if not chain:
        return ('?', '?')
    leaf = chain[0]
    skip = ('dispatch', 'lift2', 'lift1')
    hs = [x for x in chain if x.f_code.co_name not in skip]
    root = hs[-1].f_code.co_qualname if hs else 'lift2'
    site = tag if tag else '%s:%d %s' % (leaf.f_code.co_qualname, leaf.f_lineno, linecache.getline(leaf.f_code.co_filename, leaf.f_lineno).strip()[:70].replace('\t', ' '))      # the call site and its source text
    return (root, site)


def finish(cx, body0, body1, txt, decl, xdecl):
    """the record of one function; raises unless the parts add up to len(txt)"""
    global LAST
    n = len(cx.L)
    cats = {}

    def add(k, nbytes, lines=0):
        c = cats.setdefault(k, [0, 0]); c[0] += nbytes; c[1] += lines
    rx = {}
    for i, k, d in cx.PX:
        rx[i] = rx.get(i, 0) + d
        add(k, d)
    for i in range(n):
        add(cx.P[i], len(cx.L[i]) + (1 if i + 1 < n else 0) - rx.get(i, 0), 1)
    d = len(body1) - len(body0)
    if d:
        add(('lift2', 'SYNC_X_OUT / SYNC_X_IN insertion (sse)'), d)
    add(('lift2', 'extern declarations of the callees'), len(decl))
    add(('lift2', 'x87 static stack locals (f_x87_ctl.decl)'), len(xdecl))
    add(('lift2', 'function frame (signature, DECL, SYNC_IN, E, braces)'), len(txt) - len(body1) - len(decl) - len(xdecl))
    tot = sum(v[0] for v in cats.values())
    if tot != len(txt):
        raise AssertionError('prov: the parts of the function at %x add up to %d bytes, the text has %d' % (cx.start, tot, len(txt)))
    LAST = dict(cats={'%s | %s' % k: v for k, v in cats.items()}, insb=dict(cx.insb), n=len(cx.insb), text=len(txt))
