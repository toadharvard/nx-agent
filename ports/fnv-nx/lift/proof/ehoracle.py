#!/usr/bin/env python3
"""EH / SEH conformance oracle (path plan 4.5, status item 4g): the dispatch decision of Wine 9.0's MSVC exception handling, written as a pure
function of (the image bytes, Wine's rules), compared with the EH tables that the lifter and rt consume (tools/ehtables.py facts, the inputs of
lift/gates/gate_eh.py). It reads NOTHING of rt/seh.cc, rt/msvcrt_eh.cc or the lifter: the decision functions below are transcriptions of
Wine 9.0 (tag wine-9.0, dlls/msvcrt/except_i386.c and cppexcept.h; line numbers cite that file) and the tables are parsed from the PE bytes.

    python3 -I ehoracle.py --facts FACTS --game GAMEDIR --redist REDISTDIR --modules MODULES.tsv [--out report.json]

MODULES.tsv  the loader's module table (contract/modules.py): the image set, read by imageset.py (no image is named here).
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
import hashlib
import json
import os
import struct
import sys

import pefile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import imageset

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


# ---------------------------------------------------------------- the universe by rule: decoded code (capstone), the function spans of the facts as the decoding frame
import re
import capstone

FH_NAMES = ('__CxxFrameHandler', '__CxxFrameHandler3', '__CxxFrameHandler4')     # the frame handlers a FuncInfo thunk may reach (cppexcept.h; except_i386.c:651-674)
THROW_NAME = '_CxxThrowException'                                                  # cpp.c:1017-1050 (stdcall (object, throwinfo))
CXX_EXCEPTION = 0xe06d7363                                                         # cppexcept.h:25
_MD = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
_MD.skipdata = False


def load_entries(path):
    """facts universe.tsv: [(entry, extent)] (the function spans the lifter takes; the oracle only uses them as the frame it decodes in)."""
    out = []
    for ln in open(path):
        if ln.startswith('#') or not ln.strip():
            continue
        c = ln.split('\t')
        out.append((int(c[0], 16), int(c[2])))
    return out


def imports_of(img):
    """{slot VA: name} of the image's import address table (ordinal imports: '#n')."""
    out = {}
    for d in getattr(img.pe, 'DIRECTORY_ENTRY_IMPORT', []):
        for i in d.imports:
            out[img.base + (i.address - img.pref)] = i.name.decode('latin1') if i.name else '#%d' % i.ordinal
    return out


def decode(img, a, n):
    """the instructions of [a, a + n) decoded linearly: [(address, size, mnemonic, op_str)]."""
    o = a - img.base
    if o < 0 or o + n > img.size:
        n = max(0, min(n, img.size - o))
    return [(i.address, i.size, i.mnemonic, i.op_str) for i in _MD.disasm(bytes(img.mem[o:o + n]), a)]


def decode_all(img, entries):
    """{entry: instruction list} for the spans (each decoded once, in its own frame)."""
    return {a: decode(img, a, n) for a, n in entries}


_RE_IMM = re.compile(r'^0x[0-9a-f]+$')
_RE_SLOT = re.compile(r'^dword ptr \[(0x[0-9a-f]+)\]$')


def _imm(tok):
    tok = tok.strip()
    if re.match(r'^[0-9]+$', tok):
        return int(tok)
    return int(tok, 16) if _RE_IMM.match(tok) else None


def thunk_sites(streams):
    """every decoded adjacent pair `mov eax, imm32` ; `jmp T` (T direct, or `jmp [slot]`) -> [(site, imm, kind, T or slot)]."""
    out = []
    for ins in streams.values():
        for k in range(len(ins) - 1):
            a, b = ins[k], ins[k + 1]
            if a[2] == 'mov' and a[1] == 5 and a[3].startswith('eax, ') and b[2] == 'jmp' and b[0] == a[0] + 5:
                v = _imm(a[3][5:])
                if v is None:
                    continue
                m = _RE_SLOT.match(b[3])
                t = _imm(b[3])
                if m:
                    out.append((a[0], v, 'slot', int(m.group(1), 16)))
                elif t is not None:
                    out.append((a[0], v, 'direct', t))
    return out


def fh_target(img, t, kind, imps, stream_at):
    """does the jump of a thunk reach a frame handler? -> (True, proof) | (False, why).  Rules, decided on decoded code:
    (R-a) the target is `jmp [slot]` (or its body calls / jumps through a slot) and the slot's import name is a frame handler (the IAT carries the name: msvcrt / vcruntime);
    (R-b) a static copy (the CRT linked in): the function stores the entry eax (the FuncInfo operand, __CxxFrameHandler's calling convention, except_i386.c:656-672 passes eax as
          `descr`) in a local before it changes eax, and pushes that local as an argument of its first call (the descr parameter of the internal handler)."""
    if kind == 'slot':
        nm = imps.get(t)
        return (nm in FH_NAMES, 'import ' + str(nm))
    body = stream_at(t)
    if not body:
        return (False, 'no decoded body at target')
    if body[0][2] == 'jmp':
        m = _RE_SLOT.match(body[0][3])
        if m:
            nm = imps.get(int(m.group(1), 16))
            return (nm in FH_NAMES, 'import ' + str(nm))
    for ins in body:
        if ins[2] in ('call', 'jmp'):
            m = _RE_SLOT.match(ins[3])
            if m and imps.get(int(m.group(1), 16)) in FH_NAMES:
                return (True, 'calls import ' + imps[int(m.group(1), 16)])
        if ins[2] == 'ret':
            break
    saved = None
    for ins in body:
        if ins[2] == 'call':
            return (False, 'first call reached without passing the entry eax')
        m = re.match(r'^dword ptr \[ebp - (0x[0-9a-f]+|[0-9]+)\], eax$', ins[3]) if ins[2] == 'mov' else None
        if saved is None and m:
            saved = m.group(1)
            continue
        if saved is None and ins[3].split(',')[0].strip() == 'eax' and ins[2] in ('mov', 'xor', 'lea', 'add', 'sub', 'and', 'or', 'pop'):
            return (False, 'eax changed before it was saved')
        if saved is not None and ins[2] == 'push' and ins[3] == 'dword ptr [ebp - %s]' % saved:
            return (True, 'static copy: entry eax passed to the internal handler')
    return (False, 'no pass-through of the entry eax')


def throwers_of(img, streams, imps):
    """-> (entries of static throwers, IAT slots of the thrower).  A static thrower builds a C++ exception record (ExceptionCode 0xe06d7363, cpp.c:1020-1046): it either
    stores the immediate into the record (`mov [m], 0xe06d7363`: a store, not a compare) or copies a record template whose first dword is the code (`mov esi, T` ; rep movsd,
    dword at T == 0xe06d7363).  Frame handlers only COMPARE the code (cmp / jne), so they are not throwers."""
    slots = {s for s, n in imps.items() if n == THROW_NAME}
    ents = set()
    for a, ins in streams.items():
        for k, i in enumerate(ins):
            if i[2] == 'mov' and i[3].startswith('dword ptr [') and i[3].endswith(', 0xe06d7363'):
                ents.add(a)
            elif i[2] == 'mov' and i[3].startswith('esi, '):
                v = _imm(i[3][5:])
                if v is not None and img.inside(v + 3) and img.u32(v) == 0xe06d7363 and any(j[2] == 'rep movsd' for j in ins[k:k + 6]):
                    ents.add(a)
    for a, ins in streams.items():                                                 # a stub is a function whose whole body is `jmp [thunk slot]` / `jmp thrower`: calls to it are throw sites
        if len(ins) == 1 and ins[0][2] == 'jmp':
            m = _RE_SLOT.match(ins[0][3])
            t = _imm(ins[0][3])
            if (m and int(m.group(1), 16) in slots) or (t is not None and t in ents):
                ents.add(a)
    return ents, slots


def throw_args(streams, ents, slots):
    """every call (or tail jump) of the thrower -> [(site, ('const', v) | ('unresolved', why))]; the 2nd stack argument (arg 2 = ThrowInfo; arg 1 = the object).
    A stub's own `jmp` is the stub's body, not a site.  A span that starts at a block boundary continues into every path that reaches the boundary: the fall-through
    (the span that ends right before it, when it does not end in jmp / ret) and every decoded direct jmp / jcc whose target is the boundary (the instructions before the
    branch in its span).  The ThrowInfo is the value when every path gives the same constant; any other case is unresolved (a call that targets the boundary is a function entry:
    unresolved).  Paths are followed one boundary deep; anything deeper stays unresolved."""
    srcs, calls = {}, set()
    for a, ins in streams.items():
        for m, i in enumerate(ins):
            t = _imm(i[3]) if (i[2] == 'call' or i[2] == 'jmp' or i[2].startswith('j')) else None
            if t is None:
                continue
            if i[2] == 'call':
                calls.add(t)
            else:
                srcs.setdefault(t, []).append(ins[:m])
    ends = {}
    for a, ins in streams.items():
        if ins:
            ends.setdefault(ins[-1][0] + ins[-1][1], []).append(a)
    stubs = {a for a, ins in streams.items() if len(ins) == 1 and ins[0][2] == 'jmp' and a in ents}
    out = []
    for a, ins in streams.items():
        if a in stubs:
            continue
        paths = []
        for pa in ends.get(a, []):
            last = streams[pa][-1][2]
            if last not in ('jmp', 'ret', 'retn') and not (last.startswith('j') and False):
                paths.append(streams[pa])
        paths += srcs.get(a, [])
        for k, i in enumerate(ins):
            if i[2] not in ('call', 'jmp'):
                continue
            t, m = _imm(i[3]), _RE_SLOT.match(i[3])
            if not ((t is not None and t in ents) or (m and int(m.group(1), 16) in slots)):
                continue
            r = _resolve_arg2(ins, k)
            if r[0] != 'const' and r[1] in ('start of span before the second push', 'register not set in the span'):
                if a in calls or not paths:
                    r = ('unresolved', 'span is a function entry or has no predecessor path')
                else:
                    rs = [_resolve_arg2(p + ins, k + len(p)) for p in paths]
                    vals = {x[1] for x in rs if x[0] == 'const'}
                    r = ('set', tuple(sorted(vals))) if all(x[0] == 'const' for x in rs) else ('unresolved', 'a path into the block does not resolve: %s' % sorted({str(x) for x in rs})[:2])
                    if r[0] == 'set' and len(r[1]) == 1:
                        r = ('const', r[1][0])
            out.append((i[0], r))
    return out


def _resolve_arg2(ins, k):
    pushes, j = 0, k - 1
    while j >= 0:
        m, op = ins[j][2], ins[j][3]
        if m == 'push':
            pushes += 1
            if pushes == 2:
                v = _imm(op)
                if v is not None:
                    return ('const', v)
                if re.match(r'^e[a-d]x$|^e[sd]i$|^ebp$', op):
                    return _resolve_reg(ins, j, op)
                return ('unresolved', 'push %s' % op)
        elif m in ('call', 'jmp', 'ret', 'add', 'sub', 'pop', 'leave') or m.startswith('j'):
            if m in ('add', 'sub') and not op.startswith('esp'):
                pass
            elif m == 'pop' or m in ('add', 'sub'):
                return ('unresolved', 'stack adjusted before the second push')
            else:
                return ('unresolved', 'control flow before the second push')
        j -= 1
    return ('unresolved', 'start of span before the second push')


def _resolve_reg(ins, j, reg):
    while j > 0:
        j -= 1
        m, op = ins[j][2], ins[j][3]
        a = op.split(',')[0].strip()
        if m in ('call', 'ret') or m.startswith('j') or m == 'jmp':
            return ('unresolved', 'control flow before the register is set')
        if a == reg or (m in ('mul', 'imul', 'div', 'idiv', 'cdq', 'cwde', 'xchg', 'rep movsd', 'lodsd') and reg in ('eax', 'edx')):
            if m == 'mov':
                v = _imm(op.split(',', 1)[1])
                return ('const', v) if v is not None else ('unresolved', 'mov %s' % op)
            if m == 'xor' and op.split(',')[1].strip() == reg:
                return ('const', 0)
            return ('unresolved', '%s %s' % (m, op))
    return ('unresolved', 'register not set in the span')


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


def check_image(name, img, facts, throws_all, mangled_of, entries):
    """-> report dict for one image: counts and findings."""
    rep = dict(image=name, base=img.base, relocs=img.relocs, tables=dict(funcinfo=0, scope=0, throwinfo=0), states=0, type_entries=0, rows=0, agree_rows=0,
               fields_checked=0, findings=[])
    F = rep['findings']
    # FuncInfo universe BY RULE: the eax operands of the frame-handler thunks (`mov eax, imm32 ; jmp T`, T reaching a frame handler, see fh_target)
    streams = decode_all(img, entries)
    imps = imports_of(img)
    by_entry = streams

    def stream_at(t):
        return by_entry.get(t) or decode(img, t, 0x100)
    sites, fh_cache, universe, proofs = thunk_sites(streams), {}, {}, {}
    for site, imm, kind, t in sites:
        key = (kind, t)
        if key not in fh_cache:
            fh_cache[key] = fh_target(img, t, kind, imps, stream_at)
        ok, why = fh_cache[key]
        if ok:
            universe.setdefault(imm, []).append(site)
            proofs[why] = proofs.get(why, 0) + 1
        elif img.inside(imm, 4) and img.u32(imm) in (CXX_FRAME_MAGIC_VC6, CXX_FRAME_MAGIC_VC7, CXX_FRAME_MAGIC_VC8):
            F.append(dict(kind='thunk_operand_funcinfo_target_not_frame_handler', site='0x%08x' % site, why=why))
    rep['thunk_sites'] = len(sites)
    rep['frame_handler_thunks'] = sum(len(v) for v in universe.values())
    rep['frame_handler_proofs'] = proofs
    cand = set(universe)
    fact_addrs = {hx(f['addr']): f for f in facts['funcinfo']}
    for a in sorted(cand - set(fact_addrs)):
        F.append(dict(kind='funcinfo_not_in_facts', addr='0x%08x' % a))
    for a in sorted(set(fact_addrs) - cand):
        F.append(dict(kind='funcinfo_not_found_by_oracle', addr='0x%08x' % a))
    # cross-check: the magic scan (a pattern, not a rule). A hit outside the universe needs a PROOF that no frame-handler thunk reaches it.
    rep['magic_not_funcinfo'] = []
    for a in candidates(img):
        if a in cand:
            continue
        d0 = parse_descr(img, a)
        if not proofs and not any(n in FH_NAMES for n in imps.values()):
            proof = 'the image has no frame-handler thunk (no import of %s and no static copy)' % '/'.join(FH_NAMES)
        elif not any(i[1] == a for i in sites):
            proof = 'no `mov eax, imm32 ; jmp` thunk in the decoded spans names it'
        else:
            F.append(dict(kind='magic_hit_with_thunk_to_non_frame_handler', addr='0x%08x' % a))
            continue
        rep['magic_not_funcinfo'].append(dict(addr='0x%08x' % a, unwind=len(d0['unwind']), tries=len(d0['tries']), proof=proof))
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
        if ver == 4:                                                              # _except_handler4_common (:932-990): Wine 9.0 has NO cookie validation
            rep['v4_tables'] = rep.get('v4_tables', 0) + 1                        # (:946 "/* FIXME: no cookie validation yet */", pinned in wine_pin.json); the header offsets are read, the decision is "not checked"
            gs, eh = sc['header'][0], sc['header'][2]
            rep['v4_gs_cookie_tables'] = rep.get('v4_gs_cookie_tables', 0) + (1 if gs != -2 else 0)
            rep['v4_eh_cookie_tables'] = rep.get('v4_eh_cookie_tables', 0) + (1 if eh != -2 else 0)
    # throw sites: every call (or tail jump) of the thrower, its ThrowInfo argument resolved on decoded code
    ents, slots = throwers_of(img, streams, imps)
    ta = throw_args(streams, ents, slots)
    rep['throwers'] = dict(static=len(ents), import_slots=len(slots))
    rep['throw_sites'] = len({x for x, _ in ta})
    rep['throw_resolved'] = {}
    rep['throw_unresolved'] = []
    for site, r in ta:
        if r[0] in ('const', 'set'):
            rep['throw_resolved'][site] = tuple(r[1]) if r[0] == 'set' else (r[1],)
        else:
            rep['throw_unresolved'].append((site, r[1]))
            F.append(dict(kind='throw_site_unresolved', site='0x%08x' % site, why=r[1]))
    fsites = {hx(x['site']): (hx(x['ti']) if x['ti'] is not None else None) for x in facts['throw_sites']}
    for site in sorted((set(rep['throw_resolved']) ^ set(fsites)) - set(x for x, _ in rep['throw_unresolved'])):
        F.append(dict(kind='throw_site_set', site='0x%08x' % site, oracle=site in rep['throw_resolved'], lifter=site in fsites))
    for site, tis in rep['throw_resolved'].items():
        if site not in fsites:
            continue
        if fsites[site] is None:
            F.append(dict(kind='lifter_throw_site_ti_unresolved', site='0x%08x' % site, oracle=['0x%08x' % x for x in tis]))   # the oracle resolves it (paths into a join), the lifter's facts do not
        elif tis != (fsites[site],):
            F.append(dict(kind='throw_site_ti', site='0x%08x' % site, oracle=['0x%08x' % x for x in tis], lifter='0x%08x' % fsites[site]))
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


def check_wine(wdir, pin_path=None):
    """the pinned Wine 9.0 reference: every file's sha256 equals wine_pin.json, and every cited rule is at its line of that file (the text the oracle's rules are transcribed from).
    -> (findings, {file: sha256})"""
    pin = json.load(open(pin_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wine_pin.json')))
    F, sh = [], {}
    for f, want in sorted(pin['files'].items()):
        try:
            b = open(os.path.join(wdir, f), 'rb').read()
        except OSError as e:
            F.append(dict(kind='wine_file_missing', file=f, why=str(e)))
            continue
        sh[f] = hashlib.sha256(b).hexdigest()
        if sh[f] != want:
            F.append(dict(kind='wine_file_sha', file=f, have=sh[f], pinned=want))
    for f, line, needle in pin['rules']:
        if f not in sh:
            continue
        ls = open(os.path.join(wdir, f), 'rb').read().decode('latin1').split('\n')
        if not (0 < line <= len(ls)) or needle not in ls[line - 1]:
            F.append(dict(kind='wine_rule_citation', file=f, line=line, needle=needle))
    return F, sh


def main(argv):
    selftest()
    ap = argparse.ArgumentParser()
    ap.add_argument('--facts', required=True)
    ap.add_argument('--game', required=True)
    ap.add_argument('--redist', required=True)
    ap.add_argument('--modules', required=True, help="the loader's module table (contract/modules.py modules.tsv): the image set")
    ap.add_argument('--wine', required=True, help='the pinned Wine 9.0 tree (external of wine_pin.json)')
    ap.add_argument('--out')
    a = ap.parse_args(argv)
    images = [(n, path, os.path.join(a.facts, 'ehtables.json') if sub is None else os.path.join(a.facts, sub, 'ehtables.json'), sub)
              for n, path, sub in imageset.images(os.path.join(a.game, 'FalloutNV.exe'), a.modules, [a.game, a.redist])]       # the image set is the loader's module table (imageset.py)
    loaded = []
    subdir = {}
    for name, path, fp, sub in images:
        subdir[name] = sub
        facts = load_facts(fp)
        loaded.append((name, Image(path, text_lo=hx(facts['text'][0]) if sub else None), facts))
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
    wf, wsha = check_wine(a.wine)
    reps = []
    for name, img, facts in loaded:
        up = os.path.join(a.facts, 'universe.tsv') if subdir[name] is None else os.path.join(a.facts, subdir[name], 'universe.tsv')
        reps.append(check_image(name, img, facts, throws, mangled_of, load_entries(up)))
    resolved = {x for r in reps for v in r['throw_resolved'].values() for x in v if x}
    known = {t['addr'] for t in throws}
    if resolved != known:
        reps[0]['findings'].append(dict(kind='throwinfo_set', only_resolved=sorted('0x%08x' % x for x in resolved - known), only_tables=sorted('0x%08x' % x for x in known - resolved)))
    tot = dict(images=len(reps), funcinfo=sum(r['tables']['funcinfo'] for r in reps), scope=sum(r['tables']['scope'] for r in reps),
               throwinfo=sum(r['tables']['throwinfo'] for r in reps), states=sum(r['states'] for r in reps), type_entries=sum(r['type_entries'] for r in reps),
               rows=sum(r['rows'] for r in reps), agree_rows=sum(r['agree_rows'] for r in reps), fields_checked=sum(r['fields_checked'] for r in reps),
               findings=sum(len(r['findings']) for r in reps), exception_kinds=1 + len(throws))
    tot['magic_not_funcinfo'] = sum(len(r['magic_not_funcinfo']) for r in reps)
    tot['thunk_sites'] = sum(r['thunk_sites'] for r in reps)
    tot['frame_handler_thunks'] = sum(r['frame_handler_thunks'] for r in reps)
    tot['throw_sites'] = sum(r['throw_sites'] for r in reps)
    tot['throw_unresolved'] = sum(len(r['throw_unresolved']) for r in reps)
    tot['throwinfo_resolved_distinct'] = len(resolved)
    tot['v4_tables'] = sum(r.get('v4_tables', 0) for r in reps)
    tot['v4_gs_cookie_tables'] = sum(r.get('v4_gs_cookie_tables', 0) for r in reps)
    tot['v4_eh_cookie_tables'] = sum(r.get('v4_eh_cookie_tables', 0) for r in reps)
    reps[0]['findings'].extend(wf)
    for r, (name, path, fp, _sub) in zip(reps, images):                                  # the report is bound to the exact inputs it judged
        r['image_sha256'] = hashlib.sha256(open(path, 'rb').read()).hexdigest()
        r['facts_sha256'] = hashlib.sha256(open(fp, 'rb').read()).hexdigest()
    tot['wine_findings'] = len(wf)
    tot['findings'] = sum(len(r['findings']) for r in reps)
    out = dict(oracle_sha256=hashlib.sha256(open(os.path.abspath(__file__), 'rb').read()).hexdigest(), wine_sha256=wsha, format='fnvnx-ehoracle/1', totals=tot, images=reps,
               v4_cookie_rule='Wine 9.0 except_i386.c _except_handler4_common (:932-990, FIXME at :946) performs NO GS / EH cookie validation ("FIXME: no cookie validation yet"); the header offsets are recorded per table, the decision rows do not depend on the cookie')
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
