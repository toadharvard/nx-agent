"""Emit-category provenance of the lifter's text (a measurement option; OFF unless install() is called, and then the C text is unchanged except for tag lines).

RULE. The category of a piece of emitted text is the category of the emitter that wrote it. Two kinds of emitter, both named by the table below (written once, here):

  1. SCOPE functions (HELPERS): a function that returns or emits text of one category. While one runs, every cx.em() call inside it is attributed to it, and its return value
     (a str, or a list of str) is a FRAGMENT of that category: when the handler passes it on to cx.em() inside a longer line, the fragment's bytes are counted to the
     function's category and the rest of the line to the handler.
  2. every other cx.em() caller: the function that called it (module.qualname of the outermost enclosing function). Its category is its row in EMITTER, else its module's row in MODULE.
     A module or function without a row is an error (a new emitter must be classified before a build can be measured).

Output. Each cx.em(t) element becomes `//@e <emitter> <category> <bytes of t> [<category>:<bytes> ...]` + '\\n' + t: the tag is a line of its own, so ONE rule removes it:
delete every line that starts with `//@e ` (strip()). The stripped text is byte-for-byte the text of the untagged lifter, because install() changes no emitter's result.
Nothing is decided here from the output: the tags are written by the emitters' call path, and tally() only adds numbers that tags carry plus the bytes between tags."""
import functools
import sys

PREFIX = '//@e '
CATS = ('eflags', 'memory', 'dispatch', 'x87sse', 'other')

# Fragment emitters (scope functions): '<module>.<qualname>' -> category.
HELPERS = {
    'core.Ctx.zsp': 'eflags',          # ZF/SF/PF of the result
    'core.Ctx.fl': 'eflags',           # identity on a literal flag-computation fragment of a handler (CF/OF/AF/... expressions); the handler names its flag text through it
    'core.Ctx.macc_': 'memory',        # the accessor text of a memory operand (M8/M16/M32 / MK*)
    'core.Ctx.fmem': 'memory',         # float memory accessor
    'f_flow.jump_stmt': 'dispatch',    # a direct transfer to another entry (CALLX + tail rule) or the dispatcher
    'f_flow.jump_out': 'dispatch',
    'f_flow.jump_to': 'dispatch',
    'f_flow.call_xfer': 'dispatch',
    'f_flow.jmp_indirect': 'dispatch',
    'f_flow.ret_xfer': 'dispatch',
}
# Emitters that are not scopes but whose category differs from their module's row.
EMITTER = {
    'f_flow.h_call': 'dispatch', 'f_flow.h_jmp': 'dispatch', 'f_flow.h_ret': 'dispatch',
    'f_flow.h_jcc': 'other', 'f_flow.h_jecxz': 'other',
    'f_sys.far_call': 'dispatch', 'f_sys.far_target': 'dispatch', 'f_sys.far_call_mem': 'dispatch', 'f_sys.h_call_or_far': 'dispatch', 'f_sys.h_jmp_or_far': 'dispatch',
    'f_sys.h_lcall': 'dispatch', 'f_sys.h_ljmp': 'dispatch', 'f_sys.h_ljmp_any': 'dispatch', 'f_sys.h_retf': 'dispatch', 'f_sys.h_iret': 'dispatch', 'f_sys.h_repz_ret': 'dispatch',
}
# Every other emitter: by module.
MODULE = {
    'core': 'other',          # lift2: labels, TRACE, goto, the nonlocal loop's text, prologue
    'f_int': 'other', 'f_string': 'other', 'f_sys': 'other', 'f_flow': 'other', 'f_cpufeat': 'other',
    'f_x87': 'x87sse', 'f_x87_ctl': 'x87sse', 'f_sse': 'x87sse', 'f_simd_int': 'x87sse', 'f_dsl': 'other', 'lift_dsl': 'other',
}

STATE = {'cx': None, 'stack': [], 'pending': [], 'on': False}


def _mod(f):
    return f.f_globals.get('__name__', '?').rsplit('.', 1)[-1]


def _key(code, f):
    return '%s.%s' % (_mod(f), getattr(code, 'co_qualname', code.co_name).split('.<locals>')[0])


def category(key):
    if key in HELPERS:
        return HELPERS[key]
    if key in EMITTER:
        return EMITTER[key]
    m = key.split('.', 1)[0]
    if m not in MODULE:
        raise KeyError('emitcat: emitter %s has no category (module %s has no row)' % (key, m))
    return MODULE[m]


def new_function(cx):
    """called by Ctx.__init__: the state of one lifted function"""
    STATE['cx'] = cx
    STATE['stack'] = []
    STATE['pending'] = []


def _frags(r):
    if isinstance(r, str):
        yield r
    elif isinstance(r, (list, tuple)):
        for x in r:
            yield from _frags(x)


def _wrap(fn, key, cat):
    @functools.wraps(fn)
    def w(*a, **k):
        STATE['stack'].append((key, cat))
        try:
            r = fn(*a, **k)
        finally:
            STATE['stack'].pop()
        if not STATE['stack']:      # outermost scope only: its fragment is its whole text (nested ones are inside it)
            for t in _frags(r):
                if t:
                    STATE['pending'].append([cat, t, False, getattr(STATE['cx'], 'a', None)])
        return r
    w.__emitcat__ = key
    return w


def tag(cx, t):
    """-> the element for cx.L: the tag line, then t. Called by Ctx.em (core.py) only when installed."""
    if STATE['stack']:
        key, cat = STATE['stack'][-1]
    else:
        f = sys._getframe(2)         # caller of Ctx.em
        key = _key(f.f_code, f)
        cat = category(key)
    nb = len(t.encode())
    frag = {}
    s = t
    here = getattr(cx, 'a', None)
    STATE['pending'] = [p for p in STATE['pending'] if not p[2] and p[3] == here]      # a fragment lives for the instruction that made it; a consumed one is gone
    for p in STATE['pending']:
        i = s.find(p[1])
        if i >= 0 and len(p[1]) <= nb:
            p[2] = True
            frag[p[0]] = frag.get(p[0], 0) + len(p[1].encode())
            s = s[:i] + '\0' * len(p[1]) + s[i + len(p[1]):]
    tail = ''.join(' %s:%d' % (c, n) for c, n in sorted(frag.items()))
    return '%s%s %s %d%s\n%s' % (PREFIX, key, cat, nb, tail, t)


def install(core):
    """Switch the tags on in this process: wrap the scope functions and let Ctx.em tag. Idempotent."""
    if STATE['on']:
        return
    import importlib
    for key, cat in HELPERS.items():
        mod, qn = key.split('.', 1)
        m = importlib.import_module('fnvlift.' + mod) if mod != 'core' else core
        if '.' in qn:
            cls, name = qn.split('.')
            owner = getattr(m, cls)
        else:
            owner, name = m, qn
        setattr(owner, name, _wrap(getattr(owner, name), key, cat))
    core.TAG = sys.modules[__name__]
    STATE['on'] = True


def strip(text):
    """THE strip rule: delete every line that starts with `//@e `."""
    return ''.join(l for l in text.splitlines(True) if not l.startswith(PREFIX))
