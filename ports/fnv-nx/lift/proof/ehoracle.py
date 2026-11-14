#!/usr/bin/env python3
"""EH / SEH conformance oracle (path plan 4.5, status item 4g): the dispatch decision of Wine 9.0's MSVC exception handling, written as a pure
function of (the image bytes, Wine's rules), compared with the EH tables that the lifter and rt consume (tools/ehtables.py facts, the inputs of
lift/gates/gate_eh.py). It reads NOTHING of rt/seh.cc, rt/msvcrt_eh.cc or the lifter: the decision functions below are transcriptions of
Wine 9.0 (tag wine-9.0, dlls/msvcrt/except_i386.c and cppexcept.h; line numbers cite that file) and the tables are parsed from the PE bytes.

    python3 -I ehoracle.py --facts FACTS --game GAMEDIR --redist REDISTDIR [--out report.json]

FACTS   the facts tree (exe: FACTS/ehtables.json; module M: FACTS/M/ehtables.json), GAMEDIR holds FalloutNV.exe and the module dlls, REDISTDIR
        holds d3dx9_31.dll and d3dx9_38.dll. An image is mapped, relocated to its assigned base (the exe: preferred base; a module: the base
        whose .text lies where its facts say) and parsed with Wine's structure layouts.

The oracle (pure functions, no I/O):
  C++  cxx_frame_handler (except_i386.c:555-649), cxx_local_unwind (305-326), call_catch_block (374-447), find_caught_type (248-268):
       DECISION TABLE of a FuncInfo: for every trylevel `state` the frame may have and every kind of exception `k` (a C exception, i.e. no
       ExceptionInformation type, or one ThrowInfo of the image set): the catch block Wine calls (tryblock i, catchblock j, handler address),
       the unwind sequence it runs first (cxx_local_unwind(frame, descr, tryblock->start_level)), the trylevel after it (end_level + 1), plus
       the unwind sequence of an unwinding pass (state -> -1). An invalid level of the unwind table is Wine's terminate().
  SEH  _except_handler3 (883-928) and _except_handler4_common (951-990), msvcrt_local_unwind2/4 (768-817): for every scope level the chain
       of (level, filter, handler) that the hunt visits until TRYLEVEL_END (-1; -2 for the V4 table), and the cleanup sequence of the unwinding
       pass (a level with no filter runs its handler).
Findings: every difference between the oracle's table (parsed from the bytes) and the table the lifter encodes, as one record each; the report has
global counts: tables, states, type entries, decision rows, and the number that agree.
"""
import argparse
import json
import os
import struct
import sys

import pefile

CXX_FRAME_MAGIC_VC6, CXX_FRAME_MAGIC_VC7, CXX_FRAME_MAGIC_VC8 = 0x19930520, 0x19930521, 0x19930522   # cppexcept.h:22-24
FUNC_DESCR_SYNCHRONOUS = 1                                                                      # cppexcept.h:27
TYPE_FLAG_CONST, TYPE_FLAG_VOLATILE = 1, 2                                                      # except_i386.c:57-58
C_ANY = 'C'                                                                                     # the kind "no C++ exception type" (call_catch_block, info == NULL)


# ---------------------------------------------------------------- the image
class Image:
    """A PE mapped at BASE with its HIGHLOW base relocations applied (the loader's work, ASM-003 documented Win32)."""

    def __init__(self, path, base=None, text_lo=None):
        self.pe = pefile.PE(path, fast_load=False)
        self.pref = self.pe.OPTIONAL_HEADER.ImageBase
        text = [s for s in self.pe.sections if s.Characteristics & 0x20000000]
        if base is None:
            base = (text_lo - text[0].VirtualAddress) if text_lo is not None else self.pref
        self.base = base
        self.size = self.pe.OPTIONAL_HEADER.SizeOfImage
        self.mem = bytearray(self.size)
        self.mem[0:self.pe.OPTIONAL_HEADER.SizeOfHeaders] = self.pe.header[:self.pe.OPTIONAL_HEADER.SizeOfHeaders]
        self.secs = []
        for s in self.pe.sections:
            d = s.get_data()
            n = min(len(d), max(s.Misc_VirtualSize, 0) or len(d))
            self.mem[s.VirtualAddress:s.VirtualAddress + n] = d[:n]
            self.secs.append((self.base + s.VirtualAddress, self.base + s.VirtualAddress + max(s.Misc_VirtualSize, s.SizeOfRawData), s.Characteristics, s.Name.rstrip(b'\0').decode('latin1')))
        self.relocs = 0
        delta = (self.base - self.pref) & 0xffffffff
        if hasattr(self.pe, 'DIRECTORY_ENTRY_BASERELOC'):
            for blk in self.pe.DIRECTORY_ENTRY_BASERELOC:
                for e in blk.entries:
                    if e.type == 3:
                        o = e.rva
                        v = (struct.unpack_from('<I', self.mem, o)[0] + delta) & 0xffffffff
                        struct.pack_into('<I', self.mem, o, v)
                        self.relocs += 1
        elif delta:
            raise ValueError('%s: needs relocating but has no base relocations' % path)
        self.text = [(lo, hi) for lo, hi, ch, nm in self.secs if ch & 0x20000000]

    def inside(self, va, n=1):
        o = va - self.base
        return 0 <= o and o + n <= self.size

    def u32(self, va):
        return struct.unpack_from('<I', self.mem, va - self.base)[0]

    def s32(self, va):
        return struct.unpack_from('<i', self.mem, va - self.base)[0]

    def cstr(self, va, n=256):
        o = va - self.base
        e = self.mem.find(b'\0', o, o + n)
        return bytes(self.mem[o:e if e >= 0 else o + n])

    def in_text(self, va):
        return any(lo <= va < hi for lo, hi in self.text)


# ---------------------------------------------------------------- parsing with Wine's structure layouts (except_i386.c:46-96, cppexcept.h:60-117)
class ParseError(Exception):
    pass


def parse_descr(img, a):
    """cxx_function_descr at A (except_i386.c:85-96): magic, unwind_count, unwind_table, tryblock_count, tryblock, ipmap_count, ipmap, expect_list, flags."""
    if not img.inside(a, 36):
        raise ParseError('descr outside the image')
    magic, uc, ut, tc, tb, ic, ip, el, fl = struct.unpack_from('<9I', img.mem, a - img.base)
    if not CXX_FRAME_MAGIC_VC6 <= magic <= CXX_FRAME_MAGIC_VC8:          # cxx_frame_handler:562
        raise ParseError('magic %#x' % magic)
    if uc > 0x10000 or tc > 0x10000 or ic > 0x10000:
        raise ParseError('counts')
    unwind = []
    for i in range(uc):
        if not img.inside(ut + 8 * i, 8):
            raise ParseError('unwind table outside')
        unwind.append((img.s32(ut + 8 * i), img.u32(ut + 8 * i + 4)))   # unwind_info {prev, handler} (:77-81)
    tries = []
    for i in range(tc):
        t = tb + 20 * i                                                  # tryblock_info {start, end, catch_level, catchblock_count, catchblock} (:68-75)
        if not img.inside(t, 20):
            raise ParseError('tryblock outside')
        s, e, cl, cn, cb = struct.unpack_from('<iiiiI', img.mem, t - img.base)
        if cn < 0 or cn > 0x1000:
            raise ParseError('catchblock count')
        cbs = []
        for j in range(cn):
            c = cb + 16 * j                                              # catchblock_info {flags, type_info, offset, handler} (:48-55)
            if not img.inside(c, 16):
                raise ParseError('catchblock outside')
            cbs.append(dict(flags=img.u32(c), type=img.u32(c + 4), offset=img.s32(c + 8), handler=img.u32(c + 12)))
        tries.append(dict(start=s, end=e, catch=cl, blocks=cbs))
    return dict(addr=a, magic=magic, unwind=unwind, tries=tries, ipmap_count=ic, flags=fl if magic >= CXX_FRAME_MAGIC_VC8 else 0)


def parse_throwinfo(img, a):
    """cxx_exception_type at A (cppexcept.h:103-110) with its cxx_type_info_table (:80-86) and cxx_type_info (:60-67); type names through type_info.mangled (+8)."""
    if not img.inside(a, 16):
        raise ParseError('throwinfo outside')
    fl, dt, ch, tt = struct.unpack_from('<4I', img.mem, a - img.base)
    if not img.inside(tt, 4):
        raise ParseError('type table outside')
    n = img.u32(tt)
    if n > 0x100:
        raise ParseError('type count')
    types = []
    for i in range(n):
        ti = img.u32(tt + 4 + 4 * i)
        if not img.inside(ti, 28):
            raise ParseError('cxx_type_info outside')
        tflags, tinfo, size, copy = img.u32(ti), img.u32(ti + 4), img.u32(ti + 20), img.u32(ti + 24)
        types.append(dict(flags=tflags, type=tinfo, mangled=img.cstr(tinfo + 8).decode('latin1') if img.inside(tinfo + 8) else '', size=size, copy=copy))
    return dict(addr=a, flags=fl, destructor=dt, custom_handler=ch, types=types)


def parse_scope(img, a, n, ver):
    """SCOPETABLE entries (except_i386.c:116-121); V4 has a 16-byte header SCOPETABLE_V4 (:134-141) before the entries. N entries (the length is a fact of the code, R4)."""
    base = a + (16 if ver == 4 else 0)
    ents = []
    for i in range(n):
        o = base + 12 * i
        if not img.inside(o, 12):
            raise ParseError('scope table outside')
        ents.append((img.s32(o), img.u32(o + 4), img.u32(o + 8)))
    hdr = [img.s32(a), img.u32(a + 4), img.s32(a + 8), img.u32(a + 12)] if ver == 4 else None
    return dict(addr=a, ver=ver, entries=ents, header=hdr)


# ---------------------------------------------------------------- Wine's rules as pure functions
TERMINATE = 'terminate'


def local_unwind(descr, trylevel, last_level):
    """cxx_local_unwind (:305-326): the handlers called from TRYLEVEL down to LAST_LEVEL, or TERMINATE (:313-317: level < 0 or >= unwind_count)."""
    seq = []
    while trylevel != last_level:
        if trylevel < 0 or trylevel >= len(descr['unwind']):
            return TERMINATE
        prev, handler = descr['unwind'][trylevel]
        if handler:
            seq.append(handler)
        trylevel = prev
    return seq


def find_caught_type(exc, catch_ti, catch_flags, mangled_of):
    """find_caught_type (:248-268) over an exception type EXC (parse_throwinfo); MANGLED_OF(ptr) -> the type_info.mangled string of a catch type."""
    for i, t in enumerate(exc['types']):
        if not catch_ti:
            return i                                                       # catch(...) matches any type (:255)
        if catch_ti != t['type'] and mangled_of(catch_ti) != t['mangled']:
            continue                                                       # :256-259 (pointer, else strcmp of the mangled names)
        if exc['flags'] & TYPE_FLAG_CONST and not catch_flags & TYPE_FLAG_CONST:
            continue                                                       # :261-262
        if exc['flags'] & TYPE_FLAG_VOLATILE and not catch_flags & TYPE_FLAG_VOLATILE:
            continue                                                       # :263-264
        return i
    return None


def catch_decision(descr, trylevel, exc, mangled_of):
    """cxx_frame_handler + call_catch_block with no enclosing catch frame (nested_frame == NULL): -> None (ExceptionContinueSearch) or
    (tryblock i, catchblock j, handler, unwind sequence to start_level, trylevel after).  EXC None = a C exception (rec->ExceptionCode != CXX_EXCEPTION)."""
    if exc is None and descr['magic'] >= CXX_FRAME_MAGIC_VC8 and descr['flags'] & FUNC_DESCR_SYNCHRONOUS:
        return None                                                        # :566-569
    if not descr['tries']:
        return None                                                        # :576-580
    for i, tb in enumerate(descr['tries']):                                # call_catch_block :388-447
        if trylevel < tb['start'] or trylevel > tb['end']:
            continue                                                       # :395-396
        for j, cb in enumerate(tb['blocks']):
            if exc is not None:
                if find_caught_type(exc, cb['type'], cb['flags'], mangled_of) is None:
                    continue                                               # :408-410
            elif cb['type']:
                continue                                                   # :418-420: no C++ exception: only catch(...)
            return (i, j, cb['handler'], local_unwind(descr, trylevel, tb['start']), tb['end'] + 1)    # :429-431, 445
    return None


def descr_states(descr):
    """the trylevels a frame can be in: -1 .. the largest level any table names (+1 for end_level + 1)."""
    hi = len(descr['unwind'])
    for tb in descr['tries']:
        hi = max(hi, tb['end'] + 2, tb['catch'] + 1, tb['start'] + 1)
    return range(-1, hi)


def cxx_table(descr, throws, mangled_of):
    """the decision table of a FuncInfo: {(state, kind): row}; kind = C_ANY or the index of the ThrowInfo in THROWS; row 'unw' = the unwinding-pass sequence."""
    t = {}
    for s in descr_states(descr):
        t[(s, 'unw')] = local_unwind(descr, s, -1)                         # :570-574
        for kind in [C_ANY] + list(range(len(throws))):
            t[(s, kind)] = catch_decision(descr, s, None if kind == C_ANY else throws[kind], mangled_of)
    return t


def seh_chain(entries, level, end):
    """_except_handler3/4 hunt (:906-927 / :969-988): the (level, filter, handler) visited from LEVEL until END, or TERMINATE for a level outside the table
    (Wine would read out of bounds), or 'cycle'."""
    seq, seen = [], set()
    while level != end:
        if level < 0 or level >= len(entries) or level in seen:
            return TERMINATE if level not in seen else 'cycle'
        seen.add(level)
        prev, filt, handler = entries[level]
        seq.append((level, filt, handler))
        level = prev
    return seq


def seh_cleanup(entries, level, end):
    """msvcrt_local_unwind2/4 (:768-792 / :794-817): the handlers of the levels with no filter, from LEVEL until END."""
    seq, seen = [], set()
    while level != end:
        if level < 0 or level >= len(entries) or level in seen:
            return TERMINATE
        seen.add(level)
        prev, filt, handler = entries[level]
        if not filt:
            seq.append(handler)
        level = prev
    return seq


def seh_table(scope):
    end = -2 if scope['ver'] == 4 else -1
    n = len(scope['entries'])
    return {lv: (seh_chain(scope['entries'], lv, end), seh_cleanup(scope['entries'], lv, end)) for lv in range(n)}


# ---------------------------------------------------------------- the lifter's side: facts -> the same structures
def facts_descr(f):
    hx = lambda v: int(v, 16) if isinstance(v, str) else v
    return dict(addr=hx(f['addr']), magic=hx(f['magic']), unwind=[(u[0], hx(u[1])) for u in f['unwind']],
                tries=[dict(start=t['low'], end=t['high'], catch=t['catchHigh'],
                            blocks=[dict(flags=h['adj'], type=hx(h['type']), offset=h['disp'], handler=hx(h['addr'])) for h in t['handlers']]) for t in f['tries']],
                ipmap_count=len(f['ipmap']), flags=f['flags'] if hx(f['magic']) >= CXX_FRAME_MAGIC_VC8 else 0)


def facts_throw(t):
    hx = lambda v: int(v, 16) if isinstance(v, str) else v
    return dict(addr=hx(t['addr']), flags=t['attrs'], destructor=hx(t['unwind']),
                types=[dict(type=hx(x['type']), size=x['size'], copy=hx(x['copy'])) for x in t['types']])


def descr_diff(a, b):
    """field differences of two descr structures (bytes vs facts)."""
    d = []
    if a['magic'] != b['magic']:
        d.append('magic')
    if a['flags'] != b['flags']:
        d.append('flags')
    if a['unwind'] != b['unwind']:
        d.append('unwind')
    if len(a['tries']) != len(b['tries']):
        d.append('tryblock_count')
    else:
        for i, (x, y) in enumerate(zip(a['tries'], b['tries'])):
            for k in ('start', 'end', 'catch'):
                if x[k] != y[k]:
                    d.append('try%d.%s' % (i, k))
            if len(x['blocks']) != len(y['blocks']):
                d.append('try%d.catchblock_count' % i)
            else:
                for j, (p, q) in enumerate(zip(x['blocks'], y['blocks'])):
                    for k in ('flags', 'type', 'offset', 'handler'):
                        if p[k] & 0xffffffff != q[k] & 0xffffffff:        # the facts keep dispCatchObj as an unsigned word
                            d.append('try%d.cb%d.%s' % (i, j, k))
    return d


# ---------------------------------------------------------------- the run
def load_facts(p):
    return json.load(open(p))


def hx(v):
    return int(v, 16) if isinstance(v, str) else v


def candidates(img):
    """every 4-aligned dword in ANY section (d3dx9 keeps its tables in .text) that equals a FuncInfo magic and parses as a cxx_function_descr (independent of the stubs the lifter's
    enumeration starts from, ASM-222): the oracle's own universe of FuncInfos."""
    out = []
    for lo, hi, ch, nm in img.secs:
        for a in range(lo & ~3, hi, 4):
            if not img.inside(a, 4):
                break
            if img.u32(a) in (CXX_FRAME_MAGIC_VC6, CXX_FRAME_MAGIC_VC7, CXX_FRAME_MAGIC_VC8):
                try:
                    d = parse_descr(img, a)
                except ParseError:
                    continue
                if all(img.in_text(h) for _, h in d['unwind'] if h) and all(img.in_text(c['handler']) for t in d['tries'] for c in t['blocks']):
                    out.append(a)
    return out


def check_image(name, img, facts, throws_all, mangled_of):
    """-> report dict for one image: counts and findings."""
    rep = dict(image=name, base=img.base, relocs=img.relocs, tables=dict(funcinfo=0, scope=0, throwinfo=0), states=0, type_entries=0, rows=0, agree_rows=0,
               fields_checked=0, findings=[])
    F = rep['findings']
    # FuncInfo: the union of the oracle's own candidates and the facts' addresses
    cand = set(candidates(img))
    fact_addrs = {hx(f['addr']): f for f in facts['funcinfo']}
    rep['empty_descriptors_not_in_facts'] = []
    for a in sorted(cand - set(fact_addrs)):
        d0 = parse_descr(img, a)
        if not d0['unwind'] and not d0['tries']:
            # no unwind entry and no tryblock: cxx_frame_handler returns ExceptionContinueSearch (:570-580) for every state and kind, the decision table is empty
            rep['empty_descriptors_not_in_facts'].append('0x%08x' % a)
        else:
            F.append(dict(kind='funcinfo_not_in_facts', addr='0x%08x' % a))
    for a in sorted(set(fact_addrs) - cand):
        F.append(dict(kind='funcinfo_not_found_by_oracle', addr='0x%08x' % a))
    for a in sorted(cand & set(fact_addrs)):
        rep['tables']['funcinfo'] += 1
        try:
            d = parse_descr(img, a)
        except ParseError as e:
            F.append(dict(kind='funcinfo_parse', addr='0x%08x' % a, why=str(e)))
            continue
        fd = facts_descr(fact_addrs[a])
        diff = descr_diff(d, fd)
        rep['fields_checked'] += 3 + len(d['unwind']) * 2 + sum(3 + 4 * len(t['blocks']) for t in d['tries'])
        for x in diff:
            F.append(dict(kind='funcinfo_field', addr='0x%08x' % a, field=x))
        to, tf = cxx_table(d, throws_all, mangled_of), cxx_table(fd, throws_all, mangled_of)
        keys = set(to) | set(tf)
        rep['states'] += len(descr_states(d))
        rep['type_entries'] += sum(len(t['blocks']) for t in d['tries'])
        for k in sorted(keys, key=lambda k: (k[0], str(k[1]))):
            rep['rows'] += 1
            if to.get(k, 'missing') == tf.get(k, 'missing'):
                rep['agree_rows'] += 1
            else:
                F.append(dict(kind='funcinfo_decision', addr='0x%08x' % a, state=k[0], exc=str(k[1]), oracle=repr(to.get(k)), lifter=repr(tf.get(k))))
    # ThrowInfo
    for t in facts['throwinfo']:
        a = hx(t['addr'])
        rep['tables']['throwinfo'] += 1
        try:
            ti = parse_throwinfo(img, a)
        except ParseError as e:
            F.append(dict(kind='throwinfo_parse', addr='0x%08x' % a, why=str(e)))
            continue
        ft = facts_throw(t)
        rep['type_entries'] += len(ti['types'])
        rep['fields_checked'] += 3 + 3 * len(ti['types'])
        if ti['custom_handler']:
            F.append(dict(kind='throwinfo_custom_handler', addr='0x%08x' % a, value='0x%08x' % ti['custom_handler']))    # :612-617 would call it; the lifter has no field for it
        if ti['flags'] != ft['flags']:
            F.append(dict(kind='throwinfo_field', addr='0x%08x' % a, field='flags'))
        if ti['destructor'] != ft['destructor']:
            F.append(dict(kind='throwinfo_field', addr='0x%08x' % a, field='destructor'))
        if len(ti['types']) != len(ft['types']):
            F.append(dict(kind='throwinfo_field', addr='0x%08x' % a, field='type_count'))
        else:
            for i, (x, y) in enumerate(zip(ti['types'], ft['types'])):
                for k in ('type', 'size', 'copy'):
                    if x[k] != y[k]:
                        F.append(dict(kind='throwinfo_field', addr='0x%08x' % a, field='type%d.%s' % (i, k)))
    # SEH scope tables
    for s in facts['scopes']:
        a, ver, n = hx(s['addr']), s['ver'], len(s['records'])
        rep['tables']['scope'] += 1
        try:
            sc = parse_scope(img, a, n, ver)
        except ParseError as e:
            F.append(dict(kind='scope_parse', addr='0x%08x' % a, why=str(e)))
            continue
        fr = [(r[0], hx(r[1]), hx(r[2])) for r in s['records']]
        rep['states'] += n
        rep['fields_checked'] += 3 * n
        for i, (x, y) in enumerate(zip(sc['entries'], fr)):
            for k, nm in enumerate(('prev', 'filter', 'handler')):
                if x[k] != y[k]:
                    F.append(dict(kind='scope_field', addr='0x%08x' % a, level=i, field=nm, oracle=x[k], lifter=y[k]))
        end = -2 if ver == 4 else -1
        for i, (prev, filt, hnd) in enumerate(sc['entries']):                       # table sanity Wine relies on (no bounds check in Wine: out of range = memory read)
            if not (prev == end or 0 <= prev < i):
                F.append(dict(kind='scope_prev_invalid', addr='0x%08x' % a, level=i, prev=prev))
            if filt and not img.in_text(filt):
                F.append(dict(kind='scope_filter_outside_text', addr='0x%08x' % a, level=i))
            if not img.in_text(hnd):
                F.append(dict(kind='scope_handler_outside_text', addr='0x%08x' % a, level=i))
        to, tf = seh_table(sc), seh_table(dict(sc, entries=fr))
        for lv in sorted(set(to) | set(tf)):
            rep['rows'] += 1
            if to.get(lv) == tf.get(lv):
                rep['agree_rows'] += 1
            else:
                F.append(dict(kind='scope_decision', addr='0x%08x' % a, level=lv))
        # the frame handler that registers the table must be a V3 / V4 handler of the right kind: recorded, not decided here (a code fact)
    return rep


def selftest():
    """the rule functions on a hand-made descriptor (levels 0..2; try block levels 0..1 catching type T and catch(...))"""
    d = dict(magic=CXX_FRAME_MAGIC_VC8, flags=0, unwind=[(-1, 0x10), (0, 0), (1, 0x30)],
             tries=[dict(start=0, end=1, catch=3, blocks=[dict(flags=0, type=0x100, offset=0, handler=0xA0), dict(flags=0x40, type=0, offset=0, handler=0xB0)])])
    assert local_unwind(d, 2, -1) == [0x30, 0x10] and local_unwind(d, 3, -1) == TERMINATE and local_unwind(d, 1, 1) == []
    mg = lambda p: {0x100: 'T', 0x200: 'T'}.get(p, '')
    exc = dict(flags=0, types=[dict(type=0x200, mangled='T')])
    assert catch_decision(d, 1, exc, mg) == (0, 0, 0xA0, [], 2)            # same name through strcmp
    assert catch_decision(d, 1, None, mg) == (0, 1, 0xB0, [], 2)           # a C exception: only catch(...)
    assert catch_decision(d, 2, exc, mg) is None and catch_decision(dict(d, flags=1), 1, None, mg) is None
    assert catch_decision(d, 1, dict(flags=TYPE_FLAG_CONST, types=exc['types']), mg)[1] == 1    # const exception, non-const catch: skipped, falls to catch(...)
    e = [(-1, 0, 0x5), (0, 0x6, 0x7), (1, 0, 0x8)]
    assert seh_chain(e, 2, -1) == [(2, 0, 8), (1, 6, 7), (0, 0, 5)] and seh_cleanup(e, 2, -1) == [0x8, 0x5] and seh_chain(e, 5, -1) == TERMINATE


def main(argv):
    selftest()
    ap = argparse.ArgumentParser()
    ap.add_argument('--facts', required=True)
    ap.add_argument('--game', required=True)
    ap.add_argument('--redist', required=True)
    ap.add_argument('--out')
    a = ap.parse_args(argv)
    images = [('FalloutNV', os.path.join(a.game, 'FalloutNV.exe'), os.path.join(a.facts, 'ehtables.json'))]
    for m, f in (('atimgpud', 'atimgpud.dll'), ('binkw32', 'binkw32.dll'), ('d3dx9_31', 'd3dx9_31.dll'), ('d3dx9_38', 'd3dx9_38.dll'),
                 ('gdffalloutnv', 'GDFFalloutNV.dll'), ('libvorbis', 'libvorbis.dll'), ('libvorbisfile', 'libvorbisfile.dll')):
        d = a.redist if m.startswith('d3dx9') else a.game
        images.append((m, os.path.join(d, f), os.path.join(a.facts, m, 'ehtables.json')))
    loaded = []
    for name, path, fp in images:
        facts = load_facts(fp)
        loaded.append((name, Image(path, text_lo=hx(facts['text'][0]) if name != 'FalloutNV' else None), facts))
    # the exception kinds: every ThrowInfo of the image set, parsed from the bytes
    throws, owner = [], []
    for name, img, facts in loaded:
        for t in facts['throwinfo']:
            throws.append(parse_throwinfo(img, hx(t['addr'])))
            owner.append(name)

    def mangled_of(ptr):
        for _, img, _f in loaded:
            if img.inside(ptr + 8):
                return img.cstr(ptr + 8).decode('latin1')
        return ''
    reps = [check_image(name, img, facts, throws, mangled_of) for name, img, facts in loaded]
    tot = dict(images=len(reps), funcinfo=sum(r['tables']['funcinfo'] for r in reps), scope=sum(r['tables']['scope'] for r in reps),
               throwinfo=sum(r['tables']['throwinfo'] for r in reps), states=sum(r['states'] for r in reps), type_entries=sum(r['type_entries'] for r in reps),
               rows=sum(r['rows'] for r in reps), agree_rows=sum(r['agree_rows'] for r in reps), fields_checked=sum(r['fields_checked'] for r in reps),
               findings=sum(len(r['findings']) for r in reps), exception_kinds=1 + len(throws))
    tot['empty_descriptors_not_in_facts'] = sum(len(r['empty_descriptors_not_in_facts']) for r in reps)
    out = dict(format='fnvnx-ehoracle/1', totals=tot, images=reps)
    if a.out:
        json.dump(out, open(a.out, 'w'), indent=1)
    for r in reps:
        print('%-14s funcinfo %5d scope %3d throwinfo %2d states %6d type_entries %5d rows %7d agree %7d findings %d' % (
            r['image'], r['tables']['funcinfo'], r['tables']['scope'], r['tables']['throwinfo'], r['states'], r['type_entries'], r['rows'], r['agree_rows'], len(r['findings'])))
    print('TOTAL', json.dumps(tot))
    kinds = {}
    for r in reps:
        for f in r['findings']:
            kinds[f['kind']] = kinds.get(f['kind'], 0) + 1
    print('finding kinds', json.dumps(kinds))
    return 0 if not tot['findings'] else 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
