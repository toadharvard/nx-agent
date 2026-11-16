"""Flags liveness (proof/FLAGLIVE.md, lemma of L3b): which flag writes of a lifted function nobody can read.

FL = the flags of the Form DSL. For each instruction i of a function that is emitted from its Form:
  reads(i)  = the flags the Form's DAG reads          kills(i) = the flags it assigns (a write with `when` assigns 0 when the condition fails: DEF0;
                                                                an undefined flag without `uwhen` is assigned 0)
  escapes(i) = ALL when the Form has a fault row, or a guard raise (gs_guard) precedes it
  live_in(i)  = ALL if i is not a Form instruction or escapes, else reads(i) | (live_out(i) - kills(i))
  live_out(i) = union of live_in(j) over the successors j that the lifter emits as gotos; ALL at every exit (call, ret, indirect or out-of-function jmp,
                a successor that is not a Form instruction of this function)
  dropped(i)  = (flags i writes) - live_out(i)
The solution is the least fixpoint of a monotone system over a finite lattice (backward worklist). Pure: it reads facts, never C text."""
FL = ('cf', 'pf', 'af', 'zf', 'sf', 'of', 'df')
ALL = frozenset(FL)
EXIT = None


def info(F, used, succs, guarded):
    """the liveness record of one Form instruction: used = names the Form reads (InlineEmit.used); succs = addresses, EXIT for a successor that is an exit"""
    writes = frozenset(f for f, _, _ in F.flags if f in ALL) | frozenset(f for f in F.undef if f in ALL)
    kills = writes
    return dict(reads=frozenset(used) & ALL, kills=kills, esc=bool(F.faults) or guarded, succs=tuple(succs), writes=writes)


def solve(finfo):
    """finfo: addr -> info() of the Form instructions -> (live_in, live_out) dicts over those addresses.  An address not in finfo has live_in = ALL."""
    pred = {}
    for a, r in finfo.items():
        for s in r['succs']:
            if s is not EXIT and s in finfo: pred.setdefault(s, set()).add(a)
    lin = {a: frozenset() for a in finfo}
    lout = {a: frozenset() for a in finfo}
    work = sorted(finfo)
    while work:
        a = work.pop()
        r = finfo[a]
        out = frozenset().union(*[ALL if (s is EXIT or s not in finfo) else lin[s] for s in r['succs']]) if r['succs'] else ALL
        new = ALL if r['esc'] else r['reads'] | (out - r['kills'])
        lout[a] = out
        if new != lin[a]:
            lin[a] = new
            work.extend(sorted(pred.get(a, ())))
    return lin, lout


def dropped(finfo):
    """addr -> frozenset of the flags whose write is dead after the instruction (only non-empty entries)"""
    _, lout = solve(finfo)
    d = {a: r['writes'] - lout[a] for a, r in finfo.items()}
    return {a: m for a, m in d.items() if m}


def mask(s):
    return sum(1 << FL.index(f) for f in s)
