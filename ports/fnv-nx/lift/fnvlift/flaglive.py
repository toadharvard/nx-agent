"""Flags liveness (proof/FLAGLIVE.md, lemma of L3b): which flag writes of a lifted function nobody can read.

FL = the flags of the Form DSL. For each instruction i of a function that is emitted from its Form:
  reads(i)  = the flags the Form's DAG reads          kills(i) = the flags it assigns (a write with `when` assigns 0 when the condition fails: DEF0;
                                                                an undefined flag without `uwhen` is assigned 0)
  escapes(i) = ALL when the Form has a fault row, a guard raise (gs_guard) precedes it, or i is a suspension point (poll_set: a back-edge target or an entry)
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


def backedge_targets(graph):
    """the targets of the back edges of a depth-first search over graph (addr -> iterable of successor addrs; only addresses that are keys are nodes): roots and successors in address order, so the
    set is a function of the graph. A back edge u -> v is an edge to a vertex that is on the DFS stack; every cycle of the graph contains one, and its target is the loop header found first."""
    color, out = {}, set()
    for root in sorted(graph):
        if root in color: continue
        color[root] = 1; stack = [(root, iter(sorted(graph[root])))]
        while stack:
            u, it = stack[-1]
            for v in it:
                if v not in graph: continue
                c = color.get(v)
                if c == 1: out.add(v)
                elif c is None:
                    color[v] = 1; stack.append((v, iter(sorted(graph[v])))); break
            else:
                color[u] = 2; stack.pop()
    return frozenset(out)


def poll_set(graph, entries):
    """P, the suspension points of one lifted function (FNVL_POLL): the back-edge targets of its CFG and its entry labels. The cycles inside the function pass through a back-edge target; the cycles
    through transfers (tail-jmp rings, indirect rings, unbounded recursion) pass through an entry. The one derivation: the solver's escape set and the emitted polls both read this set."""
    return backedge_targets(graph) | frozenset(a for a in entries if a in graph)


def solve(finfo, poll=frozenset()):
    """finfo: addr -> info() of the Form instructions -> (live_in, live_out) dicts over those addresses.  An address not in finfo has live_in = ALL; neither has a poll point (a suspension is delivered
    there: P2' of FLAGLIVE.md), so its escapes are all of FL."""
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
        new = ALL if (r['esc'] or a in poll) else r['reads'] | (out - r['kills'])
        lout[a] = out
        if new != lin[a]:
            lin[a] = new
            work.extend(sorted(pred.get(a, ())))
    return lin, lout


def dropped(finfo, poll=frozenset()):
    """addr -> frozenset of the flags whose write is dead after the instruction (only non-empty entries)"""
    _, lout = solve(finfo, poll)
    d = {a: r['writes'] - lout[a] for a, r in finfo.items()}
    return {a: m for a, m in d.items() if m}


def mask(s):
    return sum(1 << FL.index(f) for f in s)
