"""fnkeys.py - a key per function that changes when what difffuzz would compare for it changes (incremental farms).

    fnkeys.py <targets.jsonl> <catalog.jsonl> <out keys.json>     {address: key} for the targets' functions

key = sha1(HARNESS, oracle hash, native hash):
  oracle   the function's relocated MSVC code (catalog `ohash`; fixed per decomp commit)
  native   the rewritten AArch64 function (link `fixed/`): its bytes and relocations (type, offset, addend, target),
           plus the same for every function of its object it reaches through relocations (difffuzz runs the whole
           unit for real, so a changed callee or inlined helper changes the caller's verdict) and for the S2 bridges
           it calls (zz_bridges.o). A target is named by what it resolves to: an absolute address from aliases.ld,
           `__fnv_<addr>`, a symbol name, or for a local data reference the referenced bytes (a literal's content).
  renames  S1b's units_renames rows of the function's unit that apply to the link's tree (config.units_renames:
           they set the definition's and the callers' conventions, engine.UnitCache); left out when there are
           none, so the keys of every other unit stay what they were (J-0141 renames: no HARNESS bump, only those units refresh).
  changes  the tags of the scoped harness changes (CHANGES) that select the function; left out when none does
HARNESS is bumped when the harness changes every verdict (a deliberate full refresh); a function without a native
definition gets no key (not fuzzed).

Scoped harness changes (difffuzz-14 on, item "a harness version bump says what it invalidates"): a change that
alters the verdicts of an identifiable set of functions is a CHANGES row instead of a HARNESS bump. It says in
words what it invalidates, and decides it with
  selects(fn)   static facts of the function (Facts: its MSVC name; the original addresses its oracle code calls,
                through the same-unit functions it runs for real; the names and absolute addresses its native code
                and the functions of its object it reaches refer to): its key gets the change's tag;
  recheck(div)  a saved divergence (div/<addr>-<n>.json) that the change explains differently: the cached verdict
                is fuzzed again once, whatever its key (fuzzcache.py todo; rows record the CHANGES they saw).
`fnkeys.py explain <targets> <catalog> [<cache>...]` counts what each change invalidates.
SCOPED (the newest tag) is what the farms compare with the integration head besides HARNESS.
"""
import hashlib
import json
import os
import re
import struct
import sys

import config

# difffuzz-19: a full refresh (orchestrator-approved, S1-fuzzB-002): S1-fuzzB's untyped float results
# (x86emu.x87_result_use), CRT_MATH builtins and engine's decorated-spelling preference change verdicts no key
# reflects, and so do the untyped canned results, the secondary-base entry and the vtpad-before-unplanted order of
# this bump. Every key changes; the scoped CHANGES rows below stay for later partial refreshes.
# difffuzz-20: unit images link in the C++ names other link objects define (a64image.add_providers). A full refresh (orchestrator decision, EQ eq-003): the d21 / d24 harness changes (F1 pop learning on
# a derail and IAT / thunk pops, F2 destructor vptr words / stub holes / planted slots, F3 builtin faults /
# callee-saved pop learning / bound-stub rewrite / canned results out of the private ranges) change how every
# function is fuzzed, so no cache from before them is trusted. The d21 / d24 rows below stay for later partial
# refreshes, as difffuzz-19's did. F3b (native-stub-data): the AArch64 stub range is background memory, as on the
# oracle, except the slots of the named / vtable stubs the image binds, where a data access still faults. F1b: MSVC
# padding and value-free writes are no custom-register reads, custom registers carry the parameter their calibrated
# pairing agrees on, the adaptive follow skips a callee the native calls out of line itself and reruns after its
# last load, the bool-repr probe covers background bytes and NaN-only remainders, a store into the oracle's return
# slot marks oracle-frame-smash, and an NRVO copy + destructor pair is nrvo-copy. Float class: x87_result_use follows
# a jmp to the shared epilogue and knows fidiv / fist / fcomi / ftst / fsqrt consumers, overlapping pinned literals let
# the exe bytes (when available) replace the union, and a float difference runs the garbage / bool / x87 control-word (24-bit) / x87-range / callee
# float width probes before it stays `float`. Flow rows (EQ): float libm wrappers (ceilf / floorf ..) are untraced
# builtins with a float argument, the secondary-base entry `__fnvnx_msvc_<addr>` comes from aliases.ld, and a temporary's
# destructor before a throw the exe makes after its _CxxThrowException is throw-temp-dtor. Oracle-callee-unloaded also reads the base-object entries `__fnv_<Y>_C2` / `_D2` that a unit-local constructor X reaches as the traced call Y (the 71 global-object initializers calling the constructor 0x0045cfe0, whose first exe call is 0x00404920 at 0x0045d013; tests/s13/test_basector.py).
# The signedness probe clears an argument byte only when the oracle can
# widen it (not a float sign or a whole-read int), and an uninit-arm that vanishes once the unwritten stack words of
# buffers passed to stubbed callees are zeroed is stub-outparam. Stores class: a register first
# read on a later path is a custom register, the oracle gets ST0 for a call the caller's unit declares float, and
# native-zero-init / dtor-dead-store (by design) / thunk-binding relabel store-only divergences.
# Uncls2: the leftovers get reruns that change one mechanism (all-zero garbage fill, equal results for adjacent calls of one callee,
# 64-bit shift counts modulo 64, fsin/fcos/fsincos/fptan of an infinity giving the indefinite NaN, the native word in a planted vptr,
# 4-aligned arguments for a pointer difference), a unit's pool copy of an unnamed vftable is compared with the exe's table, and a data
# symbol the object keeps writable but the exe keeps read-only gets the exe's bytes pinned.
# F-uncls: the garbage probe compares
# the argument a call-args divergence names and probes random-background cases with the background pinned (an
# uninitialised out-parameter of an intercepted callee is uninit-x86), pop learning tries the pushes right before a
# call first (three call sites over branches), an integer result that the 24-bit x87 control word rerun reproduces
# is x87-precision, a consistent read-offset shift is layout-offset, a run that entered a zero
# placeholder body is native-zero-stub, stack words read past the declaration are param-count, and a result that differs only above an al / ax write is partial-return; a placeholder of a trivial exe leaf is not
# native-zero-stub (S2 W6).
# flow2 (call-flow fresh sample): a `jmp rel32` incremental-link thunk pops what the function it jumps to pops (the
# decomp's `void F(void)` placeholder said 0), a call into a function the tree does not define pops the `ret N` the
# exe's function has, a clean run that ends in a tail call (the stub return) is no derail, the pushes of the entry
# prologue are no arguments, a merged cdecl cleanup marks the earlier sites cdecl, and the pop search covers more than three sites.
# Native-null-read class: a native fault inside the unit's own FUN_<X> the oracle only called out follows X in the oracle, native vtable groups read background past their last slot, and a planted slot answered by a named native stub is not followed.
# Virtual-call class (F2b): a call-target where the native called planted slot k and the oracle's call went to slot k+1, a unit function it ran as code (no call event), is vslot-shift (Pair.vslots_run on call-target too), and a native callee that sits in the native copy of a planted vftable at a slot whose exe function the oracle ran is native-vslot-differs.
# Destructor-shape class (vtable): the destructor-vptr rule also takes a frameless scalar deleting destructor that tests [esp+4] and stores its vftable before the je (exeempty.deleting_wrapper), a thin wrapper whose only call is a destructor chain, and a body that stores its vftable and jumps to the base destructor (exeempty.dtor_via_callee).
# Oracle-fault class: calls to proven effect-free exe functions (exeempty.py) are no divergence (oracle-empty-call), an oracle-only fault of a bare-ret native is native-empty-body, and calibrate_custom tries up to 120 pairings.
# Small address classes: a stored pinned-literal address is compared by its pointee (literal-store), the canned result of a callee whose exe code returns its `this` or first pointer argument is that input (retsrc.py, canned-this), the address of a by-value aggregate parameter is its stack slot on the oracle and the hidden pointer natively, a hidden result buffer against a frame local is nrvo-elision (by design), and a jump into the native vtable region is a wild call.
# Small address classes: a stored pinned-literal address is compared by its pointee (literal-store), the canned result of a callee whose exe code returns its `this` or first pointer argument is that input (retsrc.py, canned-this), the address of a by-value aggregate parameter is its stack slot on the oracle and the hidden pointer natively, a hidden result buffer against a frame local is nrvo-elision (by design), and a jump into the native vtable region is a wild call.# Pops class: the pop search fixes the unknown-pop call sites one at a time in run order (the count that takes the run farthest before a fault or a return outside the code, a clean balanced run to end), and `_pushes_before` counts a `sub esp, N` argument block.
# R5 pairing / stack bytes (EQ, test_r5pair.py): calibrate_custom compares fault kinds and the argument-buffer accesses of each register pairing with the native's (calib_touch_pick) and treats a vptr-word byte copy as agreeing, a call-flow divergence that goes away with STUB_OUT is stub-outparam (STUB_OUT zeroes still-planted words only and restores the stack below sp at function entries: world.scrub_dead_frames), Pair.run re-runs a call divergence on the unadapted oracle code, and a stack word the callee reads as one byte is compared by its low byte (retsrc.byte_only_args).
# Memory residue class (EQ r4mem): calibrate_custom counts a case that differs only by float noise (NaN payload, ulp, cancellation residue) as agreeing and breaks a tie of top pairings with fresh cases, a function-local static leaves the diff on its own, a -0.0 / +0.0 store is probed as x87-range, the float-word check reads the raw stores, and a canned result inside the argument buffers is canned-alias when moving it to the heap makes the machines agree. R5 misc class (EQ r5misc): x87_result_use steps over `push ecx` / `sub esp, 4` before the `fstp [esp]` of a float result, the native code-pointer scan reads copied data (memcpy / memmove / strcpy builtins) at 4-aligned addresses only, the stub behind an sret-explicit bridge is decoded without the consumed result-pointer parameter (conv.Conv.bridged_view), and a call-target also gets the all-zero-fill uninit probe.
# Args class: an input the callee's exe code cannot read (ecx `this`, a stack parameter) is not compared (and registers that follow the exe's layout after a link adapter dropped an unread `this` are equal, 0x00d315d0), retsrc follows register copies (unnamed constructors, 0x00dba6a0), trailing calls on a by-value parameter's slot are byvalue-arg-dtor, a virtual-call thunk has no pop of its own, a shim of a noreturn exe function ends the run, an unnamed deleting destructor gets the dtor vptr rule, and stores that change with the oracle's cpuid answers are cpuid-env (by design).
# R5 order (tests/s13/test_r5order.py): calls the exe shows to be the operands / arguments of one expression (exeempty.expr_deps: results followed to the end of the expression, only dead [ebp-N] temporaries stored) are paired in another order with the exe's dependences kept, the native is run again with its canned results numbered by the oracle's counterpart call (Pair._order_probe), and a run where both machines fault inside such an expression is compared up to it.
# R4 flow (flow residue, tests/s13/test_r4flow.py): the scalar-deleting wrapper shape takes an int flag and an oracle-only 0x80000000 flag-word store is as dead as a zero one (dtor-dead-store), the nrvo-copy copy constructor may be a struct's (`ABU0`), `push ecx` is a call argument when the call's `add esp, N` consumes it, a one-byte argument above 1 is probed as a bool, an uninitialised out-parameter slot is probed on both machines (stub-outparam), an import's canned eax has the native's untyped low byte, an `extern "C" bool` declaration of the calling unit types the canned result, and a native `delete` after the D1 binding of a deleting wrapper is dtor-split-delete.
# R5 design (EQ, r5design.py): dead-result (no exe caller reads eax), dead-init-call (a constructor call whose stores the caller all overwrites), local-index-range (a variable frame index outside the frame), ptrdiff-unaligned for any non-power-of-two element size, x87-range for a float argument, canned-null-deref (an indirect call's canned 0 dereferenced at once) and the oracle's unstored destructor vptr word only where the exe shows a base destructor sets it; each needs exe evidence and an agreeing rerun.
# R4 uncls (EQ): `__movsd` / `__movsb` run as builtins (rep movs) and are no call, a delete of null and the delete after the destructor half of the exe's scalar deleting destructor are left out of both traces (libcalls.delete_fold: null-delete, split-delete), the same calls in another order are paired by callee (call-order, only a reversed argument block the exe shows; an adjacent swap likewise, else call-order-unproven), a destructor under another name (exeempty.deleting_wrapper / dtor_chain) gets the vptr-reset rule, and bool-repr may need several bytes (classify._bool_chain).
# EQ rest class (tests/s13/test_eqrest.py): a pointer argument that is the address of a function-local static (the oracle's image address against the native's .bss copy), and a call-count where the oracle's static guard is set while the native's own guard runs the initializer, are local-static; entry_register_reads follows the cases of a switch jump table inside the function, so a register a case body reads (edx at 0x00e03170) is a custom register.
# Leaf registers: an intercepted call to an exe function that is a straight-line leaf writing neither ecx nor edx leaves them as the caller held them (retsrc.preserved), as MSVC's optimiser relies on (0x00d3ab60 writes only eax and cl; 0x00d3abc0 keeps its argument in edx over the call).
# EQ memory group (tests/s13/test_eqmem.py): a function-local static of a class type / spelled with other back-references in its name pairs with its native `_ZZ` object, the address of a paired static is the same object, and the native's own guard of a paired static is no private read.
# EQ thunk-forward (tests/s13/test_thunkfwd.py): a native garbage word forwarded to the jump target of an exe function that is one `jmp rel32` (declared without parameters) is an artifact.
# EQ bh_C (tests/s13/test_bhc.py): fclose_nolock / Deallocate identities, deleting_d0 of synthesized vtables, pop ecx; ret closes a frame, vslot_entered (native-vslot-differs for a call-count), canned-null-deref through a register copy, the float-store x87 probes with the f32den denormal mode, native-vcall-unplanted.
HARNESS = 'difffuzz-20'


class Facts:
    __slots__ = ('symbol', 'ocalls', 'nrefs', 'oindirect', 'ostatic', 'osg')

    def __init__(self, symbol='', ocalls=(), nrefs=(), oindirect=False, ostatic=False, osg=False):
        self.symbol, self.ocalls, self.nrefs = symbol or '', set(ocalls), set(nrefs)
        self.oindirect = oindirect      # the oracle code (or a same-unit function it runs) makes indirect calls
        self.ostatic = ostatic          # ... or refers to a function-local static or its guard (units.json names)
        self.osg = osg                  # ... or refers to a string literal units.json names ($SG)


class Change:
    def __init__(self, tag, invalidates, selects=None, recheck=None):
        self.tag, self.invalidates, self.selects, self.recheck = tag, invalidates, selects, recheck


def _noreturn(f):
    import libcalls
    return bool(f.ocalls & set(libcalls.NORETURN_X86)) or any(
        isinstance(n, str) and (libcalls.noreturn_a64(n) or n == '__cxa_allocate_exception') for n in f.nrefs)


def _math(f):
    import libcalls
    crt = {a for a, n in libcalls.X86.items() if n in libcalls.CRT_MATH or n in libcalls.CI_FORMS}
    return bool(f.ocalls & crt) or bool(f.nrefs & crt) or bool(
        {n for n in f.nrefs if isinstance(n, str)} & libcalls.FLOAT_NAMES)


def _deleting(f):
    return f.symbol.startswith(('??_G', '??_E'))


def _old_exc7(rec):
    return str((rec.get('divergence') or {}).get('arm') or '').startswith('exc:7@')


def _old_uninit_both(rec):
    return (rec.get('hint') or {}).get('cause') == 'uninit-both'


def _old_bool_repr(rec):
    return (rec.get('hint') or {}).get('cause') == 'bool-repr'


def _old_signedness(rec):
    return (rec.get('hint') or {}).get('cause') == 'signedness'


def _interlocked_slow(f):
    return 'fnvnx_interlocked_slow' in f.nrefs


def _crt_malloc(f):
    return 0x00ecd1c7 in f.ocalls


def _sg_literal(f):
    return f.osg


def _old_oracle_fault(rec):
    d = rec.get('divergence') or {}
    return d.get('kind') == 'error' and str(d.get('x86') or '').startswith('fault:') and d.get('arm') is None


def _hard_cached(rec):
    """d27-eqbase: a cached divergence the farm counted (not soft): EQ's farm-baseline round changed how many of them
    are explained (orchestrator's decision (b): re-fuzz the hard verdicts only, HARNESS stays)."""
    d = rec.get('divergence') or {}
    if not d or d.get('soft'):
        return False
    import report
    import causes
    try:
        cause = report.reclassify(dict(rec, _now=d))
    except Exception:  # noqa: BLE001
        return True                          # cannot tell: re-fuzz it
    pol = causes.policy(cause)
    return bool(pol[2]) and pol[0] in ('real-bug', 'triage')   # the hard verdicts the farm counts


def _indirect(f):
    return f.oindirect


def _f3_mem(rec):
    """F3 (d21-f3mem): a cached divergence one of its four harness fixes can explain differently: one machine
    faulted (builtins fault where the machine would), callee-saved registers (pop learning), a differing word in the
    code-stub range (stub rewrite), or a canned result word the case puts in a private range. A trap (exc:N) is
    not a data fault and stays d14's."""
    d = rec.get('divergence') or {}
    k = d.get('kind')
    if k == 'callee-saved':
        return True
    if k == 'error' and any(str(d.get(s) or '').startswith(('fault:r:', 'fault:w:')) for s in ('x86', 'arm')):
        return True
    for side in ('x86', 'arm'):
        v = d.get(side)
        if isinstance(v, str) and len(v) >= 8:
            try:
                b = bytes.fromhex(v)
            except ValueError:
                continue
            if any(0x08000000 <= int.from_bytes(b[i:i + 4], 'little') < 0x09000000 for i in range(len(b) - 3)):
                return True
    import random
    import world
    case = rec.get('case')
    if not case:
        return False
    rets = case.get('rets') or []
    for i in range(16):
        if i < len(rets) and rets[i]:
            raw = bytes.fromhex(rets[i]).ljust(16, b'\0')
        else:
            r = random.Random(((case.get('seed') or 0) * 1000003 + i) & 0xffffffff)
            raw = bytes(r.getrandbits(8) for _ in range(16))
        if world.canned_out_of_private(raw) != raw:
            return True
    return False


def _old_callflow(rec):
    return (rec.get('divergence') or {}).get('kind') in ('call-count', 'call-target')


def _hole_trace(rec):
    """A saved native call trace with a call into a hole of the stub range traced twice (d21-holes): `name:<hex>`
    or the code stub's original address, then the raw word."""
    d = rec.get('divergence') or {}
    ta = [str(t) for t in (d.get('arm_trace') or [])] + [str(d.get('arm')), str(d.get('arm_next'))]
    for a, b in zip(ta, ta[1:]):
        try:
            vb = int(b, 16)
            va = int(a[5:], 16) if a.startswith('name:') else int(a, 16) + 0x08000000
        except ValueError:
            continue
        if va == vb:
            return True
    return False


def _dtor_store(rec):
    import re as _re
    s = str(rec.get('symbol') or '')
    return (rec.get('divergence') or {}).get('kind') == 'mem-write' and (
        s.startswith(('??1', '??_G', '??_E')) or bool(_re.search(r'D[012]Ev$', s)))


def _planted_call(rec):
    return bool((rec.get('case') or {}).get('vptrs')) and \
        (rec.get('divergence') or {}).get('kind') in ('call-target', 'call-count')


def _statics(f):
    return f.ostatic or any(isinstance(n, str) and n.startswith(('_ZZ', '_ZGVZ')) for n in f.nrefs)


CHANGES = [
    Change('d14-dtor', 'targets that are MSVC deleting destructors (??_G / ??_E): fuzzed with flags = 1 only (the '
           'Itanium D0 they stand for always deletes), their return value (this vs void) no longer compared',
           selects=_deleting),
    Change('d14-noreturn', 'functions whose oracle code calls _CxxThrowException / std::_Xlen / exit or whose native '
           'code calls __cxa_throw*, __cxa_rethrow, fnvnx_cxx_throw_exception, std::__throw_*, abort ... (directly '
           'or through a same-unit function): the run ends at that call on both machines; __cxa_allocate_exception '
           'is a builtin', selects=_noreturn),
    Change('d14-math', 'functions that reach the CRT math (pow, log, log10, exp, atan[2], fmod, floor, ceil, tan, '
           'asin, acos, sin, cos, the _CI* forms) or the libm names: run in Python with IEEE results on both '
           'machines (log10 no longer through Unicorn\'s fyl2x)', selects=_math),
    Change('d14-vslot', 'cached divergences that ended in a native brk (exc:7): a call of the link\'s vslot / special '
           'trap is now named (trap:<stub>:<text>) and classified native-undefined', recheck=_old_exc7),
    Change('d15-vcall', 'functions whose oracle code (or a same-unit function it runs) makes indirect calls: an '
           'oracle call through memory the case did not plant no longer ends the comparison (oracle-vcall), and an '
           'intercepted callee of unknown convention pops what the caller pushed when the oracle otherwise '
           'returns to garbage (learned per call site)', selects=_indirect),
    Change('d17-statics', 'functions whose oracle code (or a same-unit function it runs) refers to a function-local '
           'static or its guard (units.json ?x@?1??f / ?$S1@), or whose native code names one: the static pairs with the oracle\'s image copy by function and name, '
           'both start zero (the MSVC value and guard word pinned), the native stores are compared at the oracle '
           'address, the guards are not compared (local-static)', selects=_statics),
    Change('d18-uninit', 'cached divergences classified uninit-both (both machines read garbage): the case runs once '
           'more with the same (zero) garbage on both machines, and when they then agree there is no record',
           recheck=_old_uninit_both),
    Change('d19-bool', 'cached divergences classified bool-repr: the probe now counts an input byte only when the '
           'oracle reads exactly that byte with a 1-byte access (a byte of a planted pointer or integer that removes '
           'the divergence when set to 1 is not a bool); the record gets the cause of the next probe',
           recheck=_old_bool_repr),
    Change('d20-sign', 'cached divergences classified signedness: the probe now clears the top bit of an input byte '
           'in memory only when the oracle read it as the top byte of a 1- or 2-byte access (char/short widening), '
           'not a byte of a word it read whole', recheck=_old_signedness),
    Change('d20-ilk', 'functions whose native code calls fnvnx_interlocked_slow (the misaligned path of the shim\'s '
           'inline Interlocked*, sync.cpp): it runs as the Interlocked function it performs, not as a traced call '
           '(the oracle\'s lock-prefixed instruction is none)', selects=_interlocked_slow),
    Change('d20-malloc', 'functions whose oracle code calls the CRT malloc (0x00ecd1c7): it returns a fresh heap block '
           'like the game allocators (world.ALLOCATORS) and pairs with operator new / MemoryManager::Allocate by '
           'family (libcalls.FAMILY)', selects=_crt_malloc),
    Change('d21-f3mem', 'cached divergences F3 explains differently (one machine faulted, callee-saved registers, a '
           'word in the code-stub range, a canned result in a private range): CRT/libc builtins fault where the '
           'machine\'s own access would (null region, own private range); a clean oracle run that only lost '
           'callee-saved registers learns the pops of its unknown callee-pops calls; only stubs the image binds are '
           'rewritten to their original address; canned results are moved out of both private ranges',
           recheck=_f3_mem),
    Change('d21-holes', 'cached divergences whose native trace has a call into a hole of the stub range traced '
           'twice (the stub hook and the wild hook both fired: `name:<hex>` + `<hex>`, or a code stub outside .text '
           'as its original address + the raw word): arm64emu traces it once, as the oracle does',
           recheck=_hole_trace),
    Change('d21-dtorvptr', 'cached mem-write divergences of destructors (??1, ??_G, ??_E, D0/D1/D2): the vptr words '
           'of the destroyed object (an image vftable on the oracle, a native vtable address point natively, or '
           'left as they were) are not compared, the rest of the memory is (runner.dtor_vptr_bytes)',
           recheck=_dtor_store),
    Change('d21-vslot', 'cached call divergences of cases with planted vptrs: a call-target where both machines '
           'call slots of the planted vftable is no longer followed (it became call-count x86 0, falloff?) and is '
           'vslot-shift / vslot-differs; a call-count whose native extra call is slot k of a vftable whose slot k+1 '
           'the oracle ran for real is vslot-shift (runner.Pair.vslots, vslots_run)', recheck=_planted_call),
    Change('d24-pop', 'functions whose oracle code (or a same-unit function it runs) makes indirect calls, IAT calls '
           'included: an intercepted callee of unknown convention whose wrong pop sent the oracle on into argument '
           'words without a fault (wild calls) is learned too; every IAT slot is named by its import library '
           'spelling and pops its stdcall bytes (the decomp\'s C++ spellings of an import popped 0); and cached '
           'call-count / call-target records, for callers of the exe\'s `jmp [__imp_X]` thunks (the import\'s bytes, '
           'or unknown and learned, instead of the placeholder definition\'s 0)', selects=_indirect,
           recheck=_old_callflow),
    Change('d25-sg', 'functions whose oracle code (or a same-unit function it runs) refers to a string literal units.json '
           'names ($SG): the oracle object numbers its $SG literals 43 higher than units.json (S11\'s force-included '
           'header), so each was pinned and relocated at a neighbour\'s address; coffobj now maps object $SG<n> to '
           'units.json $SG<n-d>, d found per unit by the exe\'s bytes (coffobj.sg_shift); and functions that refer '
           'to read-only data units.json names: pinned tables now carry their relocations (code / data pointers, '
           'the exe\'s word where the unit cannot place the target)', selects=_sg_literal),
    Change('d26-imp', 'cached divergences where only the oracle faulted: a native call of an import gets the raw canned word as '
           'on x86 (it was normalised as an untyped bool, 0xffffff01 against 0xffffffff for TlsGetValue, so [ret + 4] '
           'faulted on x86 only)', recheck=_old_oracle_fault),
    Change('d27-eqbase', 'cached hard divergences (real-bug / triage by causes.policy of the record cause): EQ\'s farm-baseline round (588863) explains many of them '
           'differently: oracle-callee-unloaded from arm_entered and base-object entries, __fnvnx_msvc_ secondary-base '
           'entries in the image, provider objects for C++ names another object defines, push-ecx reservations, '
           'local-static pairing of class-typed statics, thunk-forward, edx placeholders, float libm wrappers, '
           'throw-temp-dtor, x87 f32den, `pop ecx; ret` pop learning, the wild call at the top of the address space, '
           'deleting D0 pairing, unplanted vcalls, __readfsdword, returned vtables paired by the link (re-fuzzed once)',
           recheck=_hard_cached),
]
SCOPED = 'd27'


def harness_tag():
    """HARNESS plus the vtable model's input: verdicts with the player's exe (vptrs planted, vtmodel.py) and without
    it differ, so a farm that gains or loses the exe fuzzes again."""
    import vtmodel
    return HARNESS + ('+exe' if vtmodel.exe_path() else '')


ADDR = re.compile(r'^__fnv_([0-9a-f]{8})$')
CALLS = {282, 283}               # R_AARCH64_JUMP26 / CALL26 (ELF32 ILP32 uses the same numbers)
SHF_EXEC = 4


class Obj:
    """ELF32 relocatable: sections, symbols, RELA entries per target section."""

    def __init__(self, path):
        d = self.data = open(path, 'rb').read()
        if d[:4] != b'\x7fELF' or d[4] != 1:
            raise ValueError('%s: not ELF32' % path)
        shoff, = struct.unpack_from('<I', d, 0x20)
        shentsize, shnum, shstrndx = struct.unpack_from('<HHH', d, 0x2e)
        self.secs = [struct.unpack_from('<IIIIIIIIII', d, shoff + i * shentsize) for i in range(shnum)]
        shstr = self.secs[shstrndx][4]
        self.names = [self._cstr(shstr + s[0]) for s in self.secs]
        self.syms, self.rela = [], {}
        for s in self.secs:
            if s[1] == 2:                                    # SHT_SYMTAB
                stroff = self.secs[s[6]][4]
                for k in range(s[5] // 16):
                    nm, value, size, info, _, shndx = struct.unpack_from('<IIIBBH', d, s[4] + k * 16)
                    self.syms.append((self._cstr(stroff + nm) if nm else '', value, size, info & 15, info >> 4,
                                      shndx))
        for s in self.secs:
            if s[1] == 4:                                    # SHT_RELA
                ents = []
                for o in range(s[4], s[4] + s[5] - 11, 12):
                    off, info, addend = struct.unpack_from('<IIi', d, o)
                    ents.append((off, info & 0xff, info >> 8, addend))
                self.rela.setdefault(s[7], []).extend(ents)

    def _cstr(self, off):
        return self.data[off:self.data.index(b'\0', off)].decode('utf-8', 'surrogateescape')

    def sec_bytes(self, i, lo, hi):
        s = self.secs[i]
        if s[1] == 8:                                        # NOBITS
            return b'\0' * (hi - lo)
        return self.data[s[4] + lo:s[4] + hi]

    def functions(self):
        """[(section, start, end, name)] of sized function symbols in executable sections."""
        out = []
        for name, value, size, typ, bind, shndx in self.syms:
            if typ == 2 and size and 0 < shndx < len(self.secs) and self.secs[shndx][2] & SHF_EXEC:
                out.append((shndx, value, value + size, name))
        return out


def load_aliases(path):
    absolute = {}
    if os.path.exists(path):
        for line in open(path, encoding='utf-8', errors='replace'):
            m = re.match(r'\s*([^\s=]+)\s*=\s*(0x[0-9a-fA-F]+)\s*;', line)
            if m:
                absolute[m.group(1)] = int(m.group(2), 16)
    return absolute


class Native:
    def __init__(self, linkdir):
        self.fixed = os.path.join(linkdir, 'fixed')
        self.absolute = load_aliases(os.path.join(linkdir, 'out', 'aliases.ld'))
        self.bridges = {}
        for bn in ('zz_bridges.o', 'zz_bd_vcall.o'):   # S2 bridges, S2d's vcall stubs: both linked into every image
            bp = os.path.join(self.fixed, bn)
            if os.path.exists(bp):
                o = Obj(bp)
                for sec, lo, hi, name in o.functions():
                    self.bridges[name] = self._own(o, sec, lo, hi, {})[0]

    def _target(self, o, k, addend):
        name, value, size, typ, bind, shndx = o.syms[k]
        if name in self.absolute:
            return 'abs:%x+%d' % (self.absolute[name], addend)
        if name and bind != 0:
            return 'sym:%s+%d' % (name, addend)
        if typ == 3 or not name:                            # section symbol: the bytes it points at
            if 0 < shndx < len(o.secs) and not o.secs[shndx][2] & SHF_EXEC:
                at = value + addend
                return 'data:%s:%s' % (o.names[shndx], hashlib.sha1(o.sec_bytes(shndx, at, at + 64)).hexdigest()[:12])
            return 'sec:%s+%d' % (o.names[shndx] if 0 < shndx < len(o.names) else '?', value + addend)
        return 'local:%s+%d' % (name, addend)

    def _own(self, o, sec, lo, hi, by_start, refs=None):
        h = hashlib.sha1(o.sec_bytes(sec, lo, hi))
        reach = set()
        for off, typ, k, addend in sorted(o.rela.get(sec, ())):
            if not lo <= off < hi:
                continue
            t = self._target(o, k, addend)
            h.update(('%d:%d:%s;' % (off - lo, typ, t)).encode())
            name, value, size, styp, bind, shndx = o.syms[k]
            if refs is not None and name:
                m = ADDR.match(name)
                refs.add(int(m.group(1), 16) if m else (self.absolute[name] if name in self.absolute else name))
            if shndx == sec or (0 < shndx < len(o.secs) and o.secs[shndx][2] & SHF_EXEC):
                at = value + (addend if styp == 3 else 0)
                f = by_start.get((shndx, at))
                if f is not None:
                    reach.add(f)
            elif name in self.bridges:
                reach.add(('bridge', name))
        return h.hexdigest(), reach

    def keys(self, source, refs_out=None):
        """{original address: native hash} for the functions the unit's rewritten object defines; refs_out gets
        {original address: names / absolute addresses referred to by the function and what it reaches}."""
        p = os.path.join(self.fixed, config.obj_name(source))
        if not os.path.exists(p):
            return {}
        o = Obj(p)
        funcs = o.functions()
        by_start = {}
        for f in funcs:
            by_start.setdefault((f[0], f[1]), f)
        own, edges, refs = {}, {}, {}
        for f in funcs:
            refs[f] = set()
            own[f], edges[f] = self._own(o, f[0], f[1], f[2], by_start, refs[f])
        out = {}
        for name, value, size, typ, bind, shndx in o.syms:
            m = ADDR.match(name)
            if not m or not shndx or shndx >= len(o.secs):
                continue
            f = by_start.get((shndx, value))
            if f is None:
                continue
            seen, todo = {f}, [f]
            while todo:
                g = todo.pop()
                for e in edges.get(g, ()):
                    if e in seen:
                        continue
                    seen.add(e)
                    if e[0] != 'bridge':
                        todo.append(e)
            parts = sorted(own[g] if g[0] != 'bridge' else 'bridge:' + self.bridges[g[1]] for g in seen if g != f)
            h = hashlib.sha1((own[f] + '|' + ','.join(parts)).encode()).hexdigest()
            out[m.group(1)] = h
            if refs_out is not None:
                rs = set()
                for g in seen:
                    if g[0] == 'bridge':
                        rs.add(g[1])
                    else:
                        rs |= refs[g]
                refs_out[m.group(1)] = rs
        return out


def oracle_closure(catalog_rows, indirect_out=None, static_addrs=None, static_out=None, sg_addrs=None, sg_out=None):
    """{address: original addresses called (or tail-jumped to) by the function and the same-unit catalogued
    functions it reaches} - difffuzz runs the oracle's unit for real, so a callee's call is the caller's.
    indirect_out gets the addresses whose closure makes an indirect call."""
    by = {r['address']: r for r in catalog_rows}
    out = {}
    for a, r in by.items():
        seen, todo, calls = {a}, [a], set()
        while todo:
            x = by.get(todo.pop())
            if indirect_out is not None and x.get('indirect'):
                indirect_out.add(a)
            if static_out is not None and static_addrs and any(int(t, 16) in static_addrs
                                                               for t in x.get('data_refs') or ()):
                static_out.add(a)
            if sg_out is not None and sg_addrs and any(int(t, 16) in sg_addrs for t in x.get('data_refs') or ()):
                sg_out.add(a)
            for c in list(x.get('calls') or []) + list(x.get('tails') or []):
                calls.add(int(c, 16))
                y = by.get(c)
                if y is not None and y.get('source') == r.get('source') and c not in seen:
                    seen.add(c)
                    todo.append(c)
        out[a] = calls
    return out


def change_tags(facts):
    return [c.tag for c in CHANGES if c.selects is not None and c.selects(facts)]


def facts_of(t, ocl, nrefs, oind=(), ostat=(), osg=()):
    return Facts(t.get('symbol'), ocl.get(t['address'], ()), nrefs.get(t['address'], ()), t['address'] in oind,
                 t['address'] in ostat, t['address'] in osg)


def sg_literal_addrs():
    """d25-sg: every address units.json gives a $SG string literal, and, when the exe loads, every units.json symbol
    in a read-only section of the exe (the pinned read-only data whose relocations coffobj now applies)."""
    out = set()
    p = config.DECOMP / 'build' / 'units.json'
    if not p.exists():
        return out
    ro = []
    try:
        import coffobj
        exe = coffobj.exe_image()
        if exe is not None:
            ro = [(va, va + size) for va, size, flags, *_ in exe.sections
                  if not flags & 0x80000000 and not flags & 0x20000020]   # not writable, not code
    except Exception:  # noqa: BLE001
        ro = []
    for u in json.load(open(p))['units']:
        for n, a in u['symbols'].items():
            v = int(a, 16)
            if n.startswith('$SG') or any(lo <= v < hi for lo, hi in ro):
                out.add(v)
    return out


def local_static_bytes():
    """Byte addresses of every function-local static and guard units.json names (16 bytes each, as engine)."""
    import engine
    out = set()
    p = config.DECOMP / 'build' / 'units.json'
    if not p.exists():
        return out
    for u in json.load(open(p))['units']:
        for n, a in u['symbols'].items():
            if engine.LOCAL_STATIC.match(n):
                out.update(range(int(a, 16), int(a, 16) + 16))
    return out


def compute(targets, catalog):
    """-> ({address: key}, {address: [change tags]}, number of units)"""
    cat = [json.loads(line) for line in open(catalog)]
    ohash = {r['address']: r['ohash'] for r in cat if 'ohash' in r}
    oind, ostat, osg = set(), set(), set()
    ocl = oracle_closure(cat, oind, local_static_bytes(), ostat, sg_literal_addrs(), osg)
    nat = Native(str(config.LINK))
    rn, defs = {}, {}
    for row in config.units_renames():
        rn.setdefault(row[0], []).append('\t'.join(row))
        if row[1] == 'def':
            try:
                defs.setdefault(int(str(row[2]), 16), []).append('\t'.join(row))
            except ValueError:
                pass
    per_unit, nrefs, keys, tags = {}, {}, {}, {}
    for line in open(targets):
        t = json.loads(line)
        if t['source'] not in per_unit:
            try:
                per_unit[t['source']] = nat.keys(t['source'], nrefs)
            except TypeError:                   # a Native without native references (tests' stubs)
                per_unit[t['source']] = nat.keys(t['source'])
        n = per_unit[t['source']].get(t['address'])
        o = ohash.get(t['address'])
        if n and o:
            k = '%s|%s|%s' % (harness_tag(), o, n)
            if t['source'] in rn:
                k += '|renames:' + hashlib.sha1('\n'.join(sorted(rn[t['source']])).encode()).hexdigest()
            # a def row of another unit gives a callee this function reaches its convention (call-args decoding,
            # canned kind): the caller's verdict depends on it too (V1 R1)
            cd = sorted(r for a in ocl.get(t['address'], ()) for r in defs.get(a, ()) if not r.startswith(
                t['source'] + '\t'))
            if cd:
                k += '|callee-renames:' + hashlib.sha1('\n'.join(cd).encode()).hexdigest()
            tg = change_tags(facts_of(t, ocl, nrefs, oind, ostat, osg))
            if tg:
                k += '|changes:' + ','.join(tg)
                tags[t['address']] = tg
            keys[t['address']] = hashlib.sha1(k.encode()).hexdigest()[:20]
    return keys, tags, len(per_unit)


def explain(targets, catalog, caches=()):
    """What each scoped change invalidates on this link: functions whose key it changes, and cached verdicts it
    sends back for a recheck (fuzzcache.recheck_due)."""
    import fuzzcache
    keys, tags, nunits = compute(targets, catalog)
    out = {'harness': HARNESS, 'scoped': SCOPED, 'functions_with_key': len(keys), 'changes': []}
    for c in CHANGES:
        e = dict(tag=c.tag, invalidates=c.invalidates,
                 keys_changed=sum(1 for v in tags.values() if c.tag in v))
        if c.recheck is not None:
            e['rechecked'] = sum(len(fuzzcache.recheck_due(d, [c])) for d in caches)
        out['changes'].append(e)
    out['keys_changed_any'] = len(tags)
    return out


def main():
    if sys.argv[1] == 'explain':
        print(json.dumps(explain(sys.argv[2], sys.argv[3], sys.argv[4:]), indent=1))
        return
    targets, catalog, out = sys.argv[1:4]
    keys, tags, nunits = compute(targets, catalog)
    json.dump(keys, open(out, 'w'))
    print('%d keys (%d units); scoped changes %s: %s' % (
        len(keys), nunits, SCOPED, ', '.join('%s %d' % (c.tag, sum(1 for v in tags.values() if c.tag in v))
                                             for c in CHANGES if c.selects is not None)))


if __name__ == '__main__':
    main()
