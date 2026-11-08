"""slotfill2.py - S2c-vt2's additions to the exe slot fill (fnvlink.exe_slot_fill; docs/LINK.md "Vftables from the
player's exe"). fnvlink.py keeps a few hook lines; the rules live here so S2c-vt's and S2c-vt2's edits stay apart.

Rules:
* **Secondary-base entries** (`secondary_entry`): the exe slot of a secondary vftable (COL offset d > 0) holds an
  override that MSVC compiled with `this` at that base subobject (thisadj.py; the address map names its entry
  `__fnvnx_msvc_<a>`, an alias of GCC's `_ZThn<d>_` thunk). The native slot of a secondary vtable passes the
  subobject too, so binding it to `__fnv_<a>` (the full-object entry) hands the definition a `this` that is d bytes
  too high. When every vtable naming the slot has offset-to-top -d, the slot binds to `__fnvnx_msvc_<a>` instead.
* **Fastcall deleting destructors** (`fastcall_dtor`): GCC's D0 / D1 slot where the exe's ??_G is the decomp's
  `__fastcall f(this, int, flags)` goes through `__fnvnx_vflag3_<a>_<f>` (w2 = flags).
* **Enum parameters** (`enum_params`): the slot's Itanium parameter types that the MSVC side spells `enum X` are
  ints, not unknown types, for slot_abi and the x86 model.
* **Decomp constructors at an exe slot** (`slot_ctor`): an MSVC vftable never holds a constructor; a C1 there is a
  plain member the decomp named after its shell class (`FUN_00c66450::FUN_00c66450(char)`, an exe ??_G).
* **Secondary-group shift** (`shift_plan`, `place_extra`, `shift_fixed`; agreed with S2c-vt): a short primary in a
  group with secondary vtables is extended too; the secondary groups move down and every reference to them is
  patched (see the section below).
"""
import collections
import copy
import os
import re
import struct


def slot_sig(itanium, un, var=None):
    """The slot's Itanium signature; var (exe_slot_fill's destructor variant from the RTTI base) overrides it."""
    s = itanium(un)
    if var:
        s = copy.copy(s)
        s.variant = var
    return s


def secondary_entry(target_for, native, addr_sym, entry_sym, stats=None):
    """Wrap exe_slot_fill's target_for(un, a, otts, depth): a direct binding `__fnv_<a>` of an address MSVC enters
    at a secondary base d becomes `__fnvnx_msvc_<a>` when the native vtables naming the slot sit at that base
    (every offset-to-top is -d). Other results are returned unchanged."""

    def wrapped(un, a, otts=None, depth=0, var=None):
        tgt, why = target_for(un, a, otts, depth, var)
        nd = native.get(a) if tgt == addr_sym % a else None
        me = nd.get('msvc_entry') if nd else None
        if me and otts and all(-o == me[1] for o in otts):
            if stats is not None:
                stats['secondary_entry'] += 1
            return entry_sym % a, '%s; msvc this = base subobject +%d: the _ZThn thunk' % (why, me[1])
        return tgt, why
    return wrapped


FLAG3_SYM = '__fnvnx_vflag3_%08x_%d'   # GCC destructor slot -> a decomp `__fastcall f(this, int edx, flags)` ??_G


def fastcall_flags_param(ds, classify):
    """The MSVC declaration is the decomp's fastcall spelling of a thiscall deleting destructor: a free `__fastcall`
    function (pointer, int, integer): ecx = this, edx unused, the flags on the stack (the third argument)."""
    if ds is None or ds.kind != 'func' or ds.raw_params is None or ds.member or ds.cc != 'fastcall':
        return False
    ps = [classify(p) for p in ds.raw_params if p != 'void']
    return len(ps) == 3 and ps[0] == 'P' and ps[1] in ('I8', 'I16', 'I32') and ps[2] in ('I8', 'I16', 'I32')


def emit_flag3_stub(name, target, flags):
    return '\n'.join(['    .section .text.%s,"ax",%%progbits' % name, '    .p2align 2', '    .globl %s' % name,
                      '    .hidden %s' % name, '    .type %s, %%function' % name, '%s:' % name,
                      '    mov w2, #%d' % flags, '    b %s' % target, '    .size %s, .-%s' % (name, name),
                      '    .text']) + '\n'


def fastcall_dtor(target_for, native, itanium, msvc, classify, stubs, addr_sym, stats=None):
    """Wrap target_for: GCC's D0 / D1 / D2 slot where the exe slot's function is the decomp's fastcall deleting
    destructor `f(this, int, flags)` (DestroyNiTriShape, NiMaterialProperty_Destructor) goes through a stub that
    passes the flags in the third argument register: 1 for D0 (delete), 0 for D1 / D2."""

    def wrapped(un, a, otts=None, depth=0, var=None):
        tgt, why = target_for(un, a, otts, depth, var)
        if tgt is not None or not why.startswith('kind differs (function at the exe slot, D'):
            return tgt, why
        nd = native.get(a)
        isig = slot_sig(itanium, un, var)
        ds = msvc(nd['msvc']) if nd and nd.get('msvc') else None
        if not (isig.variant or '').startswith('D') or not fastcall_flags_param(ds, classify):
            return tgt, why
        fl = 1 if isig.variant == 'D0' else 0
        name = FLAG3_SYM % (a, fl)
        stubs.setdefault(name, emit_flag3_stub(name, addr_sym % a, fl))
        if stats is not None:
            stats['fastcall_dtor'] += 1
        return name, 'fastcall flags stub (%d)' % fl
    return wrapped


ENUM_RE = re.compile(r'\benum ([A-Za-z_][\w:]*)')


def msvc_enums(out):
    """Names the MSVC side spells `enum X` (the demangle cache of resolve: cache/msvc.tsv)."""
    names = set()
    try:
        for line in open(os.path.join(out, 'cache', 'msvc.tsv'), encoding='utf-8', errors='surrogateescape'):
            names.update(ENUM_RE.findall(line))
    except OSError:
        pass
    return names


def enum_params(itanium, enums, stats=None):
    """Wrap the Itanium signature reader for the slot fill: a parameter whose type the MSVC side declares as an enum
    (`ActorValue::Index`, `ANIM_GROUP_ENUM`) is spelled `enum X`, so abi.classify gives it MSVC's and GCC's int
    (I32) instead of an unknown type. The Itanium name carries no class/enum keyword; without this the slot's
    enum arguments never match the definitions' `int` (slot_abi `aggregate or varargs argument n differs`)."""
    cache = {}

    def wrapped(n):
        if n in cache:
            return cache[n]
        s = itanium(n)
        ps = s.raw_params
        if ps and any(p in enums for p in ps):
            s = copy.copy(s)
            s.raw_params = ['enum ' + p if p in enums else p for p in ps]
            if s.params is not None:
                s.params = ['enum ' + p if p in enums else p for p in s.params]
            if stats is not None:
                stats['enum_param_sigs'] += 1
        cache[n] = s
        return s
    return wrapped


FLAGC_SYM = '__fnvnx_vflagc_%08x_%d'   # GCC destructor slot -> a decomp "constructor" (this, flags) at the exe slot


def emit_flagc_stub(name, target, flags):
    return '\n'.join(['    .section .text.%s,"ax",%%progbits' % name, '    .p2align 2', '    .globl %s' % name,
                      '    .hidden %s' % name, '    .type %s, %%function' % name, '%s:' % name,
                      '    mov w1, #%d' % flags, '    b %s' % target, '    .size %s, .-%s' % (name, name),
                      '    .text']) + '\n'


def slot_ctor(target_for, native, itanium, msvc, classify, slot_abi, stubs, addr_sym, stats=None):
    """Wrap target_for: an MSVC vftable slot never holds a constructor, so a C1 at the exe slot address is the
    decomp's naming of a plain member: a shell class named after the function (`FUN_00c66450::FUN_00c66450(char)`,
    the exe's ??_G taking the flags; `FUN_00d04300::FUN_00d04300(bool)`). GCC compiled it as a constructor, which
    takes this and the same parameters. A destructor slot (D0 / D1 / D2) binds through a stub passing the flags in
    w1 (1 for D0) when the "constructor" takes this and one integer; any other slot binds directly when slot_abi
    finds the arguments where the definition reads them."""
    ints = ('I8', 'I16', 'I32')

    def wrapped(un, a, otts=None, depth=0, var=None):
        tgt, why = target_for(un, a, otts, depth, var)
        if tgt is not None or not why.startswith('kind differs (C1 at the exe slot'):
            return tgt, why
        nd = native.get(a)
        ds = msvc(nd['msvc']) if nd and nd.get('msvc') else None
        if ds is None or ds.kind != 'func' or not ds.member or ds.static or ds.raw_params is None:
            return tgt, why
        isig = slot_sig(itanium, un, var)
        ps = [classify(p) for p in ds.raw_params if p != 'void']
        if (isig.variant or '').startswith('D'):
            if len(ps) != 1 or ps[0] not in ints:
                return tgt, why
            fl = 1 if isig.variant == 'D0' else 0
            name = FLAGC_SYM % (a, fl)
            stubs.setdefault(name, emit_flagc_stub(name, addr_sym % a, fl))
            if stats is not None:
                stats['slot_ctor_dtor'] += 1
            return name, 'decomp constructor at the exe slot: flags stub (%d)' % fl
        if isig.variant or isig.raw_params is None:
            return tgt, why
        how = slot_abi(isig, ds)
        if how in ('same', 'the slot passes more') or how.startswith('the definition reads more'):
            if stats is not None:
                stats['slot_ctor_plain'] += 1
            return addr_sym % a, 'decomp constructor at the exe slot: %s' % how
        return tgt, why
    return wrapped


# ---- secondary-group shift (S2c-vt2, agreed with S2c-vt): a primary vtable shorter than the exe's, in a group
# with secondary vtables. exe_vtable_copies writes the group's strong copy with the exe's extra primary slots
# inserted before the first secondary group, so every secondary address point moves down by delta bytes. Every
# reference to the group at or past that offset is moved with it:
#   * data words (ABS32 / PREL32 relocations against _ZTV<C>): the addend;
#   * non-PIC code (ADR_PREL_PG_HI21 + ADD_ABS_LO12_NC): both addends;
#   * PIC code (ADR_GOT_PAGE + LD32_GOT_LO12_NC, then `add wD, wN, #off` on the loaded address, as compile.sh's
#     -fPIC emits every vptr store): the immediate of each add that reads the loaded register.
# A group is shifted only when every reference in every object is one of these and the register's every use
# up to its redefinition is such an add (shift_check); anything else (a load or store through it, a branch while
# it is live, a code reference through a section symbol) keeps the group as it is. apply re-runs the same scan
# on its rewritten objects and patches them (shift_apply); out/vtshift.tsv lists the shifted groups.
R_ABS32, R_PREL32, R_ADR_PG, R_ADD_LO12, R_GOT_PAGE, R_GOT_LO12 = 1, 3, 11, 12, 26, 27
SHF_ALLOC, SHF_EXEC = 2, 4
CALLEE_SAVED = set(range(19, 29))


def _elf(data):
    shoff, = struct.unpack_from('<I', data, 0x20)
    shentsize, shnum, _ = struct.unpack_from('<HHH', data, 0x2e)
    return [struct.unpack_from('<IIIIIIIIII', data, shoff + i * shentsize) for i in range(shnum)]


def _strz(data, o):
    return data[o:data.index(b'\0', o)].decode('utf-8', 'surrogateescape')


_CS = None


def _cs():
    global _CS
    if _CS is None:
        import capstone
        _CS = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
        _CS.detail = True
    return _CS


def _regno(cs, r):
    n = cs.reg_name(r) or ''
    return int(n[1:]) if n[:1] in ('w', 'x') and n[1:].isdigit() else None


def _add_uses(code, at, base):
    """The uses of the register an `ldr wN, [xM, #got_lo12]` at code offset `at` loads, until it is redefined:
    [(offset, imm)] when every one is `add wD|xD, wN|xN, #imm` (shift 0), else None. A register copy `mov wD, wN`
    (W46: TESWorldSpace's ~FUN_00582e30 keeps the group in w1 and w0) makes wD a holder too, followed the same way.
    A TLS descriptor call (`ldr wK, [x0, ...]; add w0, w0, #i; blr xK`, bhkCharacterController's ~FUN_00c705c0)
    keeps every register but x0 and x30, as the TLSDESC ABI says."""
    cs = _cs()
    w, = struct.unpack_from('<I', code, at)
    if (w & 0xffc00000) != 0xb9400000:   # LDR (immediate, unsigned offset), 32-bit
        return None
    live = {w & 31}
    out = []
    prev = []
    for ins in cs.disasm(bytes(code[at + 4:]), base + at + 4):
        rd, wr = ins.regs_access()
        reads = {_regno(cs, r) for r in rd}
        writes = {_regno(cs, r) for r in wr}
        word, = struct.unpack_from('<I', code, ins.address - base)
        prev, p2 = prev[-1:] + [word], prev
        if (word & 0xfffffc1f) == 0xd63f0000 and len(p2) == 2 and (p2[1] & 0xffc003ff) == 0x11000000 and \
                (p2[0] & 0xffc003e0) == 0xb9400000 and (p2[0] & 31) == (word >> 5) & 31:   # TLSDESC call
            live -= {0, 30}
            if not live:
                return out
            continue
        copied = None
        if reads & live:
            if (word & 0x7fc00000) == 0x11000000 and (word >> 5) & 31 in live:   # ADD (immediate), shift 0
                out.append((ins.address - base, (word >> 10) & 0xfff))
            elif (word & 0x7fe0ffe0) == 0x2a0003e0 and (word >> 16) & 31 in live:   # MOV wD, wN (ORR wD, wzr, wN)
                copied = word & 31
            else:
                return None
        live -= writes
        if copied is not None:
            live.add(copied)
        if not live:
            return out
        g = set(ins.groups)
        if 2 in g:   # CS_GRP_CALL (bl, blr): the call clobbers the caller-saved registers
            live &= set(CALLEE_SAVED)
            if not live:
                return out
        elif 3 in g:   # CS_GRP_RET
            return out
        elif 1 in g or 7 in g:   # any other jump while a holder is live: not followed
            return None
    return out


def steps(v):
    """A plan value as [(from byte offset, delta)]: one (from, delta) pair (S2c-vt2's primary extension) or, W46's
    VTFILL2, several (every vtable of the group that grows inserts its words before the next one)."""
    if not v:
        return []
    return [tuple(v)] if isinstance(v[0], int) else [tuple(x) for x in v]


def moved(x, v):
    """A byte offset into the group after the plan's insertions (offsets at or past each `from` move down)."""
    return x + sum(d for f, d in steps(v) if x >= f)


def shift_scan(path, plan):
    """plan: {vtable name: (from byte offset, delta) or [(from, delta), ...] (steps)}. -> (patches, problems): patches [(file offset, kind, value)]
    (kind 'rela' = new addend at the RELA entry, 'add' = new 32-bit instruction word), problems {name: why}."""
    data = open(path, 'rb').read()
    if data[:4] != b'\x7fELF' or data[4] != 1:
        return [], {}
    secs = _elf(data)
    patches, problems = [], {}
    for si, s in enumerate(secs):
        if s[1] != 2:   # SHT_SYMTAB
            continue
        stroff = secs[s[6]][4]
        syms = []
        defsec = {}
        for k in range(s[5] // 16):
            nm, value, size, info, _, shndx = struct.unpack_from('<IIIBBH', data, s[4] + k * 16)
            name = _strz(data, stroff + nm) if nm else ''
            syms.append((name, info & 15, shndx))
            if name in plan and 0 < shndx < 0xff00:
                defsec[shndx] = name
        for r in secs:
            if r[1] != 4 or r[6] != si or not secs[r[7]][2] & SHF_ALLOC:
                continue
            tgt = secs[r[7]]
            exe = tgt[2] & SHF_EXEC
            for o in range(r[4], r[4] + r[5] - 11, 12):
                off, info, addend = struct.unpack_from('<IIi', data, o)
                k, typ = info >> 8, info & 0xff
                if k >= len(syms):
                    continue
                name, styp, shndx = syms[k]
                if styp == 3 and shndx in defsec and r[7] != shndx:
                    problems[defsec[shndx]] = 'referenced through its section symbol'
                    continue
                if name not in plan:
                    continue
                pv = plan[name]
                if typ in (R_ABS32, R_PREL32, R_ADR_PG, R_ADD_LO12):
                    if moved(addend, pv) != addend:
                        patches.append((o + 8, 'rela', moved(addend, pv)))
                elif typ == R_GOT_PAGE:
                    pass
                elif typ == R_GOT_LO12 and exe:
                    uses = _add_uses(data[tgt[4]:tgt[4] + tgt[5]], off, 0)
                    if uses is None:
                        problems[name] = 'GOT load at %s+%#x used other than by add' % (path.rsplit('/', 1)[-1], off)
                        continue
                    for at, imm in uses:
                        if moved(imm, pv) != imm:
                            if moved(imm, pv) > 0xfff:
                                problems[name] = 'add immediate past 4095'
                                continue
                            w, = struct.unpack_from('<I', data, tgt[4] + at)
                            patches.append((tgt[4] + at, 'add', (w & ~(0xfff << 10)) | (moved(imm, pv) << 10)))
                else:
                    problems[name] = 'relocation type %d' % typ
    return patches, problems


def shift_apply(path, plan):
    """Patch the object in place (apply, after the renames). -> patches made; raises on a problem, which
    shift_check ruled out for these objects."""
    patches, problems = shift_scan(path, plan)
    if problems:
        raise ValueError('%s: %s' % (path, problems))
    if patches:
        data = bytearray(open(path, 'rb').read())
        for o, kind, v in patches:
            struct.pack_into('<i' if kind == 'rela' else '<I', data, o, v)
        open(path, 'wb').write(data)
    return len(patches)


def referencing(paths, names):
    """{name: [object paths whose symbol strings name it]} by a byte search (cheap pre-filter for shift_scan)."""
    keys = {n: b'\0' + n.encode() + b'\0' for n in names}
    out = collections.defaultdict(list)
    for p in paths:
        try:
            data = open(p, 'rb').read()
        except OSError:
            continue
        for n, k in keys.items():
            if k in data:
                out[n].append(p)
    return out


def shift_check(cands, paths, stats=None):
    """cands {vtable name: (from, delta)} -> the subset every object can be patched for, {name: why} for the rest."""
    refs = referencing(paths, cands)
    bad = {}
    for name in cands:
        for p in refs.get(name, ()):
            _, problems = shift_scan(p, {name: cands[name]})
            if name in problems:
                bad[name] = problems[name]
                break
    ok = {n: v for n, v in cands.items() if n not in bad}
    if stats is not None:
        stats['shift-ok'] += len(ok)
        stats['shift-refused'] += len(bad)
    return ok, bad


VTSHIFT = 'vtshift.tsv'
_INDEX = {}


def shift_reset(out):
    """exe_vtable_copies starts: no group is shifted yet (a seeded out dir may hold an older vtshift.tsv)."""
    with open(os.path.join(out, VTSHIFT), 'w') as f:
        f.write('#vtable\tfrom (byte offset in the group)\tdelta (bytes)\n')
    _INDEX.pop(out, None)


def _objects(out, have):
    if out not in _INDEX:
        import glob
        _INDEX[out] = sorted({u.obj for u in have if u.obj} | set(glob.glob(os.path.join(out, 'vtables', '*.o'))) |
                             set(glob.glob(os.path.join(out, 'bodies', '**', '*.o'), recursive=True)))
    return _INDEX[out]


def shift_plan(z, pts, n, nm, slots, out, have, stats=None):
    """At exe_vtable_copies' `extend and len(pts) != 1`: (from, delta) when the group's first secondary vtable
    starts right after the primary's n slots and every object referencing z can be patched (shift_check), else
    None (the group keeps its layout, as before)."""
    frm = 8 + 4 * n
    if len(pts) < 2 or pts[1][0] != frm + 8 or len(slots) <= nm:
        if stats is not None:
            stats['shift-layout'] += 1
        return None
    delta = 4 * (len(slots) - nm)
    ok, bad = shift_check({z: (frm, delta)}, _objects(out, have), stats)
    return ok.get(z)


def place_extra(words, extra, shift, z=None, out=None):
    """The group's words with the exe's extra primary slots: at the end (a single-vtable group), or before the
    first secondary group when it is shifted (recorded in vtshift.tsv for apply)."""
    if not shift:
        return words + extra
    i = shift[0] // 4
    with open(os.path.join(out, VTSHIFT), 'a') as f:
        f.write('%s\t%d\t%d\n' % (z, shift[0], shift[1]))
    return words[:i] + extra + words[i:]


def shifted(ap, shift):
    """An address point of the group after the shift (one (from, delta) pair or steps)."""
    return moved(ap, shift)


def shift_steps(z, plan, out, have, stats=None):
    """W46 (VTFILL2): plan [(from, delta)] when every object referencing z can be patched for it, else None."""
    ok, bad = shift_check({z: list(plan)}, _objects(out, have), stats)
    return plan if z in ok else None


def record_shift(z, plan, out):
    """vtshift.tsv rows of a group shifted in several steps (one row per step; shift_fixed collects them)."""
    with open(os.path.join(out, VTSHIFT), 'a') as f:
        for frm, delta in plan:
            f.write('%s\t%d\t%d\n' % (z, frm, delta))


def shift_fixed(out, fixed):
    """apply: patch every rewritten object for the shifted groups. -> (objects, patches, failures)."""
    path = os.path.join(out, VTSHIFT)
    plan = {}
    if os.path.exists(path):
        for line in open(path, encoding='utf-8', errors='surrogateescape'):
            f = line.rstrip('\n').split('\t')
            if not line.startswith('#') and len(f) >= 3:
                plan.setdefault(f[0], []).append((int(f[1]), int(f[2])))
    if not plan:
        return 0, 0, 0
    objs = sorted(os.path.join(fixed, x) for x in os.listdir(fixed) if x.endswith('.o') and x != 'zz_bridges.o')
    refs = referencing(objs, plan)
    k = n = bad = 0
    for p in sorted({p for ps in refs.values() for p in ps}):
        try:
            m = shift_apply(p, {z: plan[z] for z in plan if p in refs[z]})
        except ValueError as e:
            print('vtshift failed: %s' % e)
            bad += 1
            continue
        k += bool(m)
        n += m
    return k, n, bad


def other_class_entry(isig, dsig):
    """A code reference to an address MSVC enters at a secondary base, named through another class than the
    definition's but with the same function name (`IslandRemovalCallback::FUN_00d64a20` for
    `IslandWorldRemoval::FUN_00d64a20`; the base class's vtable slot `CVB::FUN_008805f0` for `CVShell::...`): the
    caller holds the subobject, as the x86 caller did, so it enters through the thunk. The Itanium form of resolve's
    rule on MSVC declarations, for references that carry none (tiers name-addr, unit/exact of a #vtables object)."""
    q, dq = isig.qual or [], dsig.qual or []
    return (not isig.variant and not dsig.variant and len(q) > 1 and len(dq) > 1 and q[-1] == dq[-1]
            and q[:-1] != dq[:-1])
