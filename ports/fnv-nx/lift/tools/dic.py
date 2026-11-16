"""dic.py - data in code (DIC), one function for every image (tools/universe.py exe, tools/modlift.py modules).

RULE. A byte range lies in data_in_code.tsv iff a proven consumer reads it; the kind and the extent come from the consumer, never from the look of the bytes:
  jtab    the table of an indirect jump `jmp [T + r*4]` that tools/jtab.py resolved: its slots are the words of the jtab record (ehtables.json jtab_tables: base, lo..hi, words). The range is
          [first slot, last slot + 4); derivation `site=<jmp> jtab idxrange lo..hi`.
Kinds not produced here yet (ptrtable, string, struct, const, eh) need the consumer facts of the points-to / import contracts; they are counted as open by `nonoperand_words` (a relocated word in a run that is
neither an operand nor inside a data range is RED), never classified from bytes.

INVARIANT (facts gate). Over the instructions of every entry's ranges (the flow decode), each relocated dword that overlaps the bytes of an instruction lies exactly on its imm32 or disp32 field. The count of words
that do not is the `reloc-misfit` count (modlift: refused candidates; a proven entry with one is RED). `nonoperand_words` recounts it over the final ranges, independent of the entry flags: 0 for every image.
"""
import os, json


def jtab_ranges(outd, proven_sites=None):
    """-> [(start, end, kind, derivation)] from ehtables.json of `outd`."""
    p = outd + '/ehtables.json'
    if not os.path.exists(p): return []
    J = json.load(open(p)); out = []
    for t in J.get('jtab_tables', []):
        ws = sorted(int(w, 16) for w in t.get('words', []))
        if not ws: continue
        out.append((ws[0], ws[-1] + 4, 'jtab', 'site=%s jtab idxrange %d..%d slots=%d' % (t['site'], t['lo'], t['hi'], len(ws))))
    return sorted(out)


def write(outd, hdr, rows):
    """data_in_code.tsv: `# base=` header like ranges.tsv; columns start, end, kind, derivation (hex absolute VAs, half-open)."""
    open(outd + '/data_in_code.tsv', 'w').write(hdr.rstrip() + '; columns: start, end, kind, derivation (consumer site + rule); a range exists iff a proven consumer reads it (tools/dic.py)\n'
                                                 + ''.join('%08x\t%08x\t%s\t%s\n' % r for r in rows))


def nonoperand_words(md, text, T0, relocs, insns, rows=()):
    """insns: the distinct reached instructions [(addr, size)] of all entries (the flow decode, each instruction once); relocs: the HIGHLOW sites.
    -> (words, in_data): the relocated dwords that overlap the bytes of a reached instruction without being its imm32 or disp32 field, and of those the words inside a data range (rows)."""
    import bisect
    ins = sorted(set(insns)); st = [a for a, z in ins]; F = {}
    def fields(a):
        if a not in F:
            g = next(md.disasm(text[a - T0:a - T0 + 15], a, 1), None); F[a] = {a + g.disp_offset for _ in [0] if g.disp_size == 4} | {a + g.imm_offset for _ in [0] if g.imm_size == 4} if g else set()
        return F[a]
    words = []
    for r in relocs:
        i = bisect.bisect_right(st, r + 3) - 1
        while i >= 0 and ins[i][0] > r - 16:          # an instruction is at most 15 bytes: only these can overlap [r, r+4)
            a, z = ins[i]
            if a < r + 4 and r < a + z and r not in fields(a): words.append(r); break
            i -= 1
    ind = [w for w in words if any(a <= w < b for a, b, k, d in rows)]
    return words, ind


PROTECT = ('virtualprotect', 'writeprocessmemory', 'flushinstructioncache')


def protect_sites(md, text, T0, insns, imports):
    """The call sites of VirtualProtect(Ex), WriteProcessMemory and FlushInstructionCache in the reached code (`call [IAT slot]`, or `call` / `jmp` to a thunk `jmp [IAT slot]`): the sites whose target
    range may be an image range stay OPEN until the points-to (ASM-234, REMOVE; its removing fact is the points-to of L3a w4). -> [(site, api, via)] sorted; the count is the open count of the image."""
    ins = sorted(set(a for a, z in insns)); thunk = {}; calls = []
    for a in ins:
        g = next(md.disasm(text[a - T0:a - T0 + 15], a, 1), None)
        if g is None or not g.operands: continue
        m = g.mnemonic.split()[-1]; o = g.operands[0]
        if m in ('call', 'jmp'):
            if o.type == 3 and not o.mem.base and not o.mem.index and imports.get(o.mem.disp & 0xffffffff): 
                nm = imports[o.mem.disp & 0xffffffff]
                if m == 'jmp': thunk[a] = nm
                else: calls.append((a, nm, 'iat'))
            elif o.type == 2 and m == 'call': calls.append((a, o.imm & 0xffffffff, 'thunk'))
    out = []
    for a, nm, via in calls:
        if via == 'thunk': nm = thunk.get(nm)
        if nm and any(p in nm.lower().split('!')[-1] for p in PROTECT): out.append((a, nm, via))
    return sorted(out)
