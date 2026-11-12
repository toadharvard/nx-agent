"""THE definition of the lifted set: the instructions the lifter emits a body for. liftgen.py lifts through lift_with_instructions() (below), and every
census of "what was lifted" (L3b's shape census, formgate) imports lifted() / is_lifted() from here, so the lifter and a census cannot disagree.

Rule: a universe entry is lifted iff fnvlift.lift_entry(img, entry) returns a text (it raises Unsupported otherwise: the entry is a missing row of
liftgen). The instructions lifted for an entry are the instruction table of the LAST fnvlift.core.explore call of that lift (the table the text is
emitted from; the dyn-x87 retry explores again and yields the same table). The lifted set is the union over lifted entries; each entry lifts its own
body, so an instruction inside the range of a parent with several entries is lifted once per entry that reaches it. lifted() yields one (va, length)
per lifted body instruction, entries in address order, instructions in address order inside an entry: a census that counts per instruction counts
exactly what liftgen counts (shapes.tsv). Nothing is decided here: no class list. The codemap (tools/codemap.py) is a DERIVED view of the facts and is
never consulted; the universe is the one fnvlift.universe loads (FNVNX_UNIVERSE), and `facts` must be that file.

The lifting mode and the jump-table facts are inputs of the result (`emit`: '' hand handlers, 'dsl' proven DSL forms first; `jtab`: the ehtables.json path or None), as in liftgen --emit / --jtab; there is no default."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(HERE)
for p in ('tools', ''):
    sys.path.insert(0, os.path.join(LIFT, p))
S = {}          # the capture of the last explore() result, and the memo of lifted sets by (image bytes path, facts, emit)


def install(img, emit, jtab):
    """the state of a lifting process: the emit mode, the jump-table facts (ehtables.json of the module: the only source of jump-table extents) and the capture wrapper around core.explore (once per process)"""
    import fnvlift as L, fnvlift.core as core
    core.set_emit(emit)
    core.set_jtab(json.load(open(jtab)) if jtab else None)
    S['cap'] = []
    if 'orig_explore' not in S:
        S['orig_explore'] = orig = core.explore

        def cap(*x, **k):
            r = orig(*x, **k); S['cap'].append(r); return r
        core.explore = cap
    return L


def lift_with_instructions(a):
    """-> (text, instructions {va: capstone instruction}) of universe entry a, or (None, reason). The one lift step of liftgen and of lifted()."""
    import fnvlift as L
    S['cap'].clear()
    try:
        t, m = L.lift_entry(S['img'], a)
    except L.Unsupported as e:
        return None, str(e)
    return t, S['cap'][-1][0]


def begin(img, emit, jtab):
    install(img, emit, jtab)
    S['img'] = img


def lifted(img, facts, emit, jtab):
    """-> list of (va, length) of every instruction a lifted body is emitted for (see the module text); `img` an fnvlift.Image, `facts` the universe.tsv the lifter reads"""
    import fnvlift as L
    ent = L.universe.load()[0]
    if os.path.abspath(facts) != os.path.abspath(os.environ.get('FNVNX_UNIVERSE', '/home/user/l3facts/universe.tsv')):
        raise ValueError('lifted: facts %s is not the universe the lifter reads (FNVNX_UNIVERSE)' % facts)
    key = (id(img), facts, emit, jtab)
    if S.get('key') != key:
        begin(img, emit, jtab)
        out = []
        for a in sorted(ent):
            t, ins = lift_with_instructions(a)
            if t is not None: out += [(va, ins[va].size) for va in sorted(ins)]
        S['key'], S['set'] = key, (out, frozenset(out))
    return S['set'][0]


def is_lifted(img, facts, lo, hi, emit, jtab):
    """True iff [lo, hi) is exactly one lifted instruction (lo, hi - lo)"""
    lifted(img, facts, emit, jtab)
    return (lo, hi - lo) in S['set'][1]
