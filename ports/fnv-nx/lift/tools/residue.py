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
