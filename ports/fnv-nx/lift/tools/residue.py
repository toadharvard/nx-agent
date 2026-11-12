"""The ONE residual rule of the byte accounting (modlift.py for modules, codemap.py for the exe).

Residue = valid executable bytes that no entry body reaches and no PE directory / relocation-named table names. In a non-PIC image every code address a program can hold has one of these sources:
  (s1) a relocated in-span value (R3: every one is an entry),
  (s2) a call-pushed return address or an FPU FIP/FDP (typed by pcsrc),
  (s3) exports, TLS callbacks, the PE entry and EH handlers from the tables (entries by construction),
  (s4) arithmetic on a value of s1..s3.
Every indirect transfer lands in s1..s3 unless s4 happens, so the closure is complete iff every relocated in-span value is an entry (unent == 0) AND no s4 value reaches a control transfer. The s4
check is pcsrc's (the typed-source rule extended from return addresses to relocated code values); its result is read from <facts dir>/pcsrc_s4.json {"s4_violations": n}. No file: s4 is UNCHECKED and
the residue is red, never assumed green.

RULE: closure complete -> every residue byte is data (class DATA = 9, "data-unreached"); otherwise every residue byte is UNEXPLAINED (class 8 in modlift, 0 in codemap) with the closure reason.
Nothing else decides a residue byte: the sweep below (decodes / undecodable / reloc-not-operand / no-end) is a breakdown printed for information and decides nothing."""
import os, json, bisect
from types import SimpleNamespace
DATA = 9
TERMS = {'ret', 'retf', 'jmp', 'int3', 'ud2', 'iret', 'iretd'}


def disp_only(md, text, T0, cov, r, bad=()):
    """R3 REFINED (owner order of the lead, 2026-11-12): a relocated in-span value V is a code entry iff V can become a run-time value, i.e. V occurs in a relocated data word or as an immediate
    operand or as the effective address of a lea. A relocation that sits ONLY in the 4-byte displacement of a non-lea memory operand of an instruction of reached flow (jmp [r*4+V], movzx r,[r+V],
    mov r,[V], call [V], ...) never yields V as a value: the effective-address computation consumes the displacement inside address generation (a base + disp form gives V + base as an address, never
    V). So V is a data address, and its bytes are code only if flow reaches them.
    True iff the relocation at site r is such a displacement. md: capstone disassembler; text/T0: executable bytes and their base; cov[o] == 1 starts a reached instruction, 2 is its interior;
    bad: instruction starts that are not trusted (a relocated dword straddling its operand fields = decoding ran into data): their sites stay value sites.
    THE ONE FUNCTION: modlift.py (modules) and universe.py (exe) both filter the relocated values through it, and the closure / unent check reads the filtered list."""
    o = r - T0
    if not (0 <= o < len(cov)) or not cov[o]:
        return False
    s0 = o
    while s0 > 0 and cov[s0] != 1:
        s0 -= 1
    if T0 + s0 in bad:
        return False
    ins = next(md.disasm(text[s0:s0 + 15], T0 + s0, 1), None)
    if ins is None or ins.mnemonic == 'lea' or ins.disp_size != 4 or T0 + s0 + ins.disp_offset != r:
        return False
    if ins.imm_size == 4 and T0 + s0 + ins.imm_offset == r:
        return False
    return any(op.type == 3 and op.mem.disp & 0xffffffff == int.from_bytes(text[o:o + 4], 'little') for op in ins.operands)       # 3 = X86_OP_MEM


def entries_on_data(facts_dir):
    """INVARIANT (R3 refined): no universe entry starts on a byte that codemap.tsv classes as data named by a relocation. The universe and the codemap come from one rule, so an entry over table bytes
    would be a lifted body over data. -> sorted entry addresses violating it. Reads <facts>/universe.tsv and <facts>/codemap.tsv; writes the count into gate_codemap.json (key entries_on_data) and
    turns its status red when nonzero."""
    cm = []
    for l in open(os.path.join(facts_dir, 'codemap.tsv')):
        f = l.rstrip('\n').split('\t')
        if len(f) >= 3 and not l.startswith('#') and f[2] == 'data-reloc':
            cm.append((int(f[0], 16), int(f[0], 16) + int(f[1])))
    cm.sort(); st = [a for a, b in cm]; bad = []
    for l in open(os.path.join(facts_dir, 'universe.tsv')):
        f = l.rstrip('\n').split('\t')
        if len(f) < 3 or l.startswith('#'):
            continue
        a = int(f[0], 16); i = bisect.bisect_right(st, a) - 1
        if i >= 0 and cm[i][0] <= a < cm[i][1]:
            bad.append(a)
    g = os.path.join(facts_dir, 'gate_codemap.json')
    if os.path.exists(g):
        j = json.load(open(g)); j['entries_on_data'] = len(bad)
        if bad:
            j['status'] = 'red'
        open(g, 'w').write(json.dumps(j) + '\n')
    return sorted(bad)


def span(entry, ins):
    """universe.tsv `extent` for every image, one meaning: the SPAN [entry, entry + extent) that holds every instruction the entry reaches at or above it (a reached instruction below the entry is outside:
    a jump to it leaves the range) = the largest end of an instruction starting at or above the entry, minus the entry. ins: iterable of (address, size) of the instructions the entry reaches."""
    return max([a + n - entry for a, n in ins if a >= entry] or [0])


def body_facts(ins, tabs, entries, intext, imports):
    """what the body of one entry (the lifter's explore over [entry, entry + span), `ins`: address -> capstone instruction, `tabs`: jump tables by jmp address) says about how it returns:
    -> dict(rets = {N} the `ret N` operands of its ret sites (0 for a plain ret), far = it has a retf / iret, open = None or a reason (an indirect jmp that is not a jump table, an IAT thunk is
    named import!dll!name, a transfer target outside the executable sections: the callee is not known statically)). Tails (jumps and fall-throughs leaving the range) come from the caller."""
    rets, far, op = set(), False, []
    for a, g in ins.items():
        m = g.mnemonic.split(' ')[-1]; o = g.operands
        if m == 'ret':
            rets.add(o[0].imm & 0xffff if o else 0)
        elif m in ('retf', 'iret', 'iretd'):
            far = True
        elif m == 'jmp' and o[0].type != 2 and a not in tabs:       # 2 = X86_OP_IMM
            sl = o[0].mem.disp & 0xffffffff if o[0].type == 3 and not o[0].mem.base and not o[0].mem.index else None
            op.append('import:' + imports[sl] if sl in imports else 'indirect-jmp')
    return dict(rets=rets, far=far, open=sorted(set(op)))


def retn_rows(info):
    """facts/<img>/retn.tsv: `entry pop N`, one row per entry. info[e] = dict(rets, far, open (list of reasons), tails (entries the body leaves to by a jump or a fall-through),
    outside (a leaving target that is no entry: dispatcher), noreturn (e in the NORETURN set)). Rule: the set of `ret N` sites reachable from the entry in its body, with every tail entry's set added
    (least fixed point over the tail graph); {0} -> caller, {N>0} -> callee N, several values -> mixed, no ret site -> noreturn, and `open` when some path leaves to an unknown callee (an indirect jmp,
    an import thunk, a target outside the executable sections) or returns with retf / iret: never a guess. -> [(entry, pop, N, note)], pop in caller | callee | mixed | noreturn | open."""
    V = {e: set(i['rets']) for e, i in info.items()}; OP = {e: set(i['open']) | ({'outside'} if i['outside'] else set()) | ({'far-return'} if i['far'] else set()) for e, i in info.items()}
    ch = True
    while ch:
        ch = False
        for e, i in info.items():
            for t in i['tails']:
                if t in info:
                    if not V[t] <= V[e]: V[e] |= V[t]; ch = True
                    if not OP[t] <= OP[e]: OP[e] |= OP[t]; ch = True
    rows = []
    for e in sorted(info):
        v = V[e]
        if info[e]['noreturn']: rows.append((e, 'noreturn', '-', 'NORETURN set'))
        elif OP[e]: rows.append((e, 'open', '-', ','.join(sorted(OP[e])) + (' rets=%s' % ','.join(map(str, sorted(v))) if v else '')))
        elif not v: rows.append((e, 'noreturn', '-', 'no ret site'))
        elif v == {0}: rows.append((e, 'caller', '0', '-'))
        elif len(v) == 1: rows.append((e, 'callee', str(next(iter(v))), '-'))
        else: rows.append((e, 'mixed', '-', 'rets=' + ','.join(map(str, sorted(v)))))
    return rows


def write_retn(facts_dir, hd, rows):
    open(os.path.join(facts_dir, 'retn.tsv'), 'w').write(hd.rstrip() + '; columns: entry, pop (caller: every reachable ret pops 0 | callee: every ret pops N | mixed | noreturn | open), N (callee only), note; rule residue.retn_rows (ret sites reachable in the body by flow, tail entries take their own value)\n'
                                                      + ''.join('%08x\t%s\t%s\t%s\n' % r for r in rows))
    from collections import Counter
    return Counter(r[1] for r in rows)


def closure(rvals, entries, facts_dir):
    """-> SimpleNamespace(complete, reason, unent, s4_status, s4_viol). rvals: [(site, value)] of every relocated dword whose value lies in an executable section; entries: the entry set."""
    unent = sorted({v for r, v in rvals if v not in entries})
    f = os.path.join(facts_dir, 'pcsrc_s4.json')
    if os.path.exists(f):
        viol = int(json.load(open(f))['s4_violations']); st = 'green' if viol == 0 else 's4-violation'
    else:
        viol, st = None, 's4-unchecked'
    reason = 'unent' if unent else (None if st == 'green' else st)
    return SimpleNamespace(complete=reason is None, reason=reason, unent=unent, s4_status=st, s4_viol=viol)


def line(cl, n_data, n_runs):
    return 'residual rule: closure %s (relocated in-span values not entries %d, s4 %s%s); data-unreached runs %d, bytes %d' % (
        'complete' if cl.complete else 'OPEN: ' + cl.reason, len(cl.unent), cl.s4_status, '' if cl.s4_viol is None else ' (%d violations)' % cl.s4_viol, n_runs, n_data)


def sweep(md, text, T0, TN, relocs, rsl, cov, p, q):
    """INFORMATION ONLY: how a sweep of the residue run [p, q) (offsets in text) from its first byte would read it -> 'decodes' | 'undecodable' | 'reloc-not-operand' | 'no-end' (+ '/first-byte-undecodable').
    rsl[k] != 0: byte k is a byte of a relocation site; cov[k] == 1: byte k starts a reached instruction."""
    k = p; segs = []; why = None
    while k < q:
        if rsl[k]:
            j = k
            while j < q and rsl[j]: j += 1
            segs.append((k, j, 5)); k = j; continue
        g = next(md.disasm(text[k:min(k + 15, q)], T0 + k, 1), None)
        if g is None: why = 'undecodable'; break
        f = {g.address + g.disp_offset if g.disp_size == 4 else -1, g.address + g.imm_offset if g.imm_size == 4 else -1}
        if any(x not in f for x in relocs[bisect.bisect_left(relocs, T0 + k):bisect.bisect_left(relocs, T0 + k + g.size)]): why = 'reloc-not-operand'; break
        if segs and segs[-1][2] == 6: segs[-1] = (segs[-1][0], k + g.size, 6, g.mnemonic)
        else: segs.append((k, k + g.size, 6, g.mnemonic))
        k += g.size
    for i, sg in enumerate(segs):
        if sg[2] == 6 and not (sg[3] in TERMS or (i + 1 == len(segs) and not why and q < TN and cov[q] == 1)):
            last = i + 1 == len(segs); segs = segs[:i]; why = why if (last and why) else 'no-end'; break
    return ('decodes' if not why else why) + ('/first-byte-undecodable' if not segs and why == 'undecodable' else '')


# ---- sources.tsv: the closure of the code-address SOURCES of an image, one `source<TAB>closed|open` row each (the fact file of L1's closure_key certificate)
SOURCES = ('r3_reloc', 'pcsrc', 'export', 'tls', 'entry', 'eh', 'no_s4_arith')
EH_KINDS = {'eh-unwind', 'eh-catch', 'eh-cont', 'seh-filter', 'seh-handler', 'seh-frame', 'eh-throw'}


def pe_sources(M, u32, intext):
    """the PE's own lists of code addresses, read independently of the entry set: -> (exports in the executable span, [entry point], TLS callbacks)"""
    import pemod
    XC = [a for a in pemod.exports(M)[0] if intext(a)]
    entry = [M.base + M.entry] if M.entry else []
    tls = []
    d = M.dirent(9)
    if d[0]:
        p = u32(M.base + d[0] + 12)
        while intext(u32(p)): tls.append(u32(p)); p += 4
    return XC, entry, tls


def source_rows(cl, facts_dir, universe, exports, entry, tls, eh_addrs):
    """-> [(source, closed)] in SOURCES order. r3_reloc: every relocated in-span value is an entry; pcsrc: the pcsrc result of the image (gate_pcsrc.json) has no red row other than s4 (return-address / FIP /
    FDP sources typed); export / tls / entry / eh: every such address is an entry of the universe (checked here, not assumed); no_s4_arith: pcsrc_s4.json exists and says 0 violations. A missing file is open."""
    f = os.path.join(facts_dir, 'gate_pcsrc.json'); pc = False
    if os.path.exists(f):
        r = json.load(open(f)); pc = r['red'] - r['s4_red'] == 0
    inu = lambda xs: all(a in universe for a in xs)
    closed = {'r3_reloc': not cl.unent, 'pcsrc': pc, 'export': inu(exports), 'tls': inu(tls), 'entry': inu(entry), 'eh': inu(eh_addrs), 'no_s4_arith': cl.s4_status == 'green'}
    return [(k, closed[k]) for k in SOURCES]


def write_sources(image_dir, rows):
    os.makedirs(image_dir, exist_ok=True)
    open(os.path.join(image_dir, 'sources.tsv'), 'w').write(''.join('%s\t%s\n' % (k, 'closed' if c else 'open') for k, c in rows))


def combine(root):
    """<root>/sources.tsv over every image directory <root>/<image>/sources.tsv: a source is closed iff it is closed in every image (the lifted dispatcher is one table for all images)"""
    tot = {k: True for k in SOURCES}; n = 0
    for d in sorted(os.listdir(root)):
        f = os.path.join(root, d, 'sources.tsv')
        if not os.path.isfile(f): continue
        n += 1
        got = dict(l.rstrip('\n').split('\t') for l in open(f) if l.strip())
        for k in SOURCES: tot[k] = tot[k] and got.get(k) == 'closed'
    open(os.path.join(root, 'sources.tsv'), 'w').write(''.join('%s\t%s\n' % (k, 'closed' if tot[k] else 'open') for k in SOURCES))
    return n, tot


def lifter_outside(ia, ext, NR):
    """ia: {addr: (size, call target or None, falls through)} of the lifter's own flow of one entry; ext: the instructions the universe flow reaches (NORETURN-aware, so its ranges stop after a call to a
    never-returning entry NR). -> (lifter-only instructions that are NOT such a dead tail (invariant: 0), their sorted addresses, the count of dead-tail instructions): a lifter-only instruction is a dead tail
    iff it is reached from the fall-through of a call to an NR entry through fall-through instructions that are all lifter-only."""
    es = set(ext); lo = {a for a in ia if a not in es}
    if not lo: return 0, [], 0
    dead = set(); work = [a + ia[a][0] for a in es if a in ia and ia[a][1] in NR and a + ia[a][0] in lo]
    while work:
        x = work.pop()
        if x in dead or x not in lo: continue
        dead.add(x)
        if ia[x][2] and x + ia[x][0] in lo: work.append(x + ia[x][0])
    rest = sorted(lo - dead)
    return len(rest), rest, len(dead)
