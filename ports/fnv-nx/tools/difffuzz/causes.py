#!/usr/bin/env python3
"""causes.py - record-level cause refinement and the per-cause verdict policy (stream S13b-causes).

Everything here reads a saved divergence record (<campaign>/div/<addr>-<n>.json: `divergence`, `hint`, `conv`,
`symbol`, `case`) and nothing else: no replay, no emulator, no decomp. It runs downstream of the harness (fuzz.py,
classify.py, runner.py are not touched) and keeps their interface: a record's cause is still one string from the
same vocabulary, `report.reclassify` calls `refine` only where it would otherwise answer `unclassified`, `call-flow`
or `signedness?`, or the oracle-model tag `oracle-vcall` (HINT_REFINABLE).

    causes.py summary <campaign dir>... [--recheck-tag t] [--out causes.json]
        one row per cause: divergences, functions, functions whose worst cause it is, verdict class, owner, why
        (fnvnx-causes/1, read by tools/gates for R0); with --recheck-tag, replays that no longer reproduce are dropped
        exactly as report.py does
    causes.py merge <report.md | causes.json>... [--out causes.json]
        the shards' summaries added up: the ```json fnvnx-causes``` block of each fuzz report (reports/fz<k>, written
        by tools/farm/fuzz_job.sh) or a causes_cum.json; one fnvnx-causes/1 for all shards (examples dropped)
    causes.py explain <div.json>...
        the refined cause of each record and the rule that gave it
    causes.py handoffs <campaign dir>... --out-dir d [--recheck-tag t] [--effects e.jsonl] [--ref-prefix p]
                       [--ref-by-head]
        the per-owner lists: handoffs_<owner>.tsv (real-bug), triage_<owner>.tsv, harness_<owner>.tsv (artifacts
        with an owner), one row per counted divergence: owner, cause, address, unit, reproducer
        (<ref-prefix><campaign dir name>:div/<file>, default prefix fuzz/cache/), first effect; and index.tsv

Input layout of a campaign or fuzz cache directory (fuzz.py / fuzzcache.py; branch fuzz/cache/<k>of<N>):
    results.jsonl          one row per function: address, source (unit), status ('ok' is fuzzed; setup:*, run:*,
                           crash:*, oracle-gap:* are not counted), divergences [{kind, cause, file, soft}], ...
    div/<addr>-<n>.json    the minimised reproducer: source, address, symbol, conv, divergence {kind, x86, arm, ...},
                           hint {cause, ...}, case
    recheck[_<tag>].jsonl  optional (difffuzz.py recheck): replays gone are dropped, still ones use the new divergence
A function counts when its results row says ok; its causes come from its div files (report.reclassify, then refine).

Verdict classes (POLICY):
  by-design  MSVC and the port differ only where C++ leaves it open or the input is invalid for the original game
             (operand order, NaN payloads, bool bytes other than 0/1, x87 extended intermediates, #DE on x86)
  artifact   the harness or its oracle model, not the port (fixed or being fixed in difffuzz itself)
  real-bug   the port behaves differently from the exe on a valid input; the owner is the stream that fixes it
  triage     not decided from the record alone; needs a replay (`difffuzz.py effects` / `triage --reads`)
"""
import argparse
import collections
import glob
import json
import math
import os
import struct
import sys

if __name__ == '__main__':
    # report.reclassify imports `causes`: run as a script, that would be a second copy of this module whose EFFECTS
    # and OBJ_STATS the command line never fills (`summary --effects` silently ignored). One module, one state.
    sys.modules.setdefault('causes', sys.modules[__name__])

# address ranges of the shared world (world.py; duplicated so this module needs no emulator imports)
NULL_END = 0x10000
TEXT = (0x00400000, 0x00fdf000)
WINDOW = (0x00fdf000, 0x02100000)          # image data window (.rdata/.data/.bss of the exe)
BUFS = (0x03000000, 0x03040000)
HEAP = (0x03800000, 0x04000000)
STACK = (0x06e00000, 0x06f10000)
A64_STUBS = (0x08000000, 0x0a000000)       # __fnv_<X> code stubs and named stubs (brk #0)
A64_IMAGE = (0x0a000000, 0x0b800000)       # the native unit image (.text/.rodata/.data/.bss)
A64_PRIV = (0x08000000, 0x0c000000)
LIT_POOL = (0x02f00000, 0x03000000)        # literals units.json does not name (engine's pinned pool)
X86_PRIV = (0x0e000000, 0x0e100000)

# cause -> (verdict class, owner, hard?, why). `hard` mirrors report.py's split (False = soft or oracle gap).
POLICY = {
    # --- by design -------------------------------------------------------------------------------------------------
    'bool-repr':      ('by-design', '-', False, 'a bool byte other than 0/1: MSVC tests the byte, GCC bit 0; the '
                                                'exe never stores such a byte (D10 keeps bool 1 byte)'),
    'nan-bits':       ('by-design', '-', False, 'only the NaN payload/sign differ (x87 real indefinite 0xffc00000 '
                                                'vs AArch64 default NaN 0x7fc00000); no game code inspects payloads'),
    'x87-precision':  ('by-design', '-', False, 'x87 80-bit intermediates vs IEEE single/double, within 4 ulp or '
                                                'relative 1e-6/1e-12, or the native value equals the oracle\'s '
                                                'unrounded x87 value'),
    'division':       ('by-design', '-', False, 'x86 raises #DE (x/0, INT_MIN/-1) where sdiv/udiv give 0 / wrap: '
                                                'the exe would crash on that input'),
    'deleting-dtor-ret': ('by-design', '-', False, 'MSVC\'s scalar/vector deleting destructor returns this, the '
                                                   'Itanium deleting destructor (D0) is void: no native caller reads '
                                                   'the result'),
    'call-order':     ('by-design', '-', False, 'two adjacent calls swapped: C++ leaves operand evaluation order open'),
    'vptr-store':     ('by-design', '-', False, 'a dead vptr store GCC drops (D7: objects hold Itanium vptrs); in a '
                                                'destructor MSVC resets the vptr to the base vftable, GCC does not'),
    'array-new-check': ('by-design', '-', False, 'GCC checks new[]\'s count for overflow, MSVC 2008 does not'),
    'ilp32-pointer':  ('by-design', '-', False, 'an address computed past 4 GiB (wraparound on x86): needs a pointer '
                                                'the exe never holds'),
    'float->int':     ('by-design', 'S12', False, 'out-of-range float->int (UB in C++); the FTOI2 plugin gives '
                                                  'MSVC semantics where the build loads it'),
    'shift-count':    ('by-design', '-', False, 'a shift count >= the operand width (UB in C++)'),
    'uninit-x86':     ('by-design', '-', False, 'the oracle\'s own result depends on garbage: undefined behaviour '
                                                'the exe gets away with; the port need not reproduce garbage'),
    # --- harness / oracle-model artefacts ----------------------------------------------------------------------------
    'local-static':   ('artifact', 'S13', False, 'MSVC keeps a function-local static and its guard in the image '
                                                 '(fuzzed bytes), GCC in .bss (zero): not compared state'),
    'uninit-both':    ('artifact', 'S13', False, 'both machines read garbage: the fuzzed case is invalid for both'),
    'oracle-vcall':   ('artifact', 'S13', False, 'the oracle faults on an indirect call the harness does not model '
                                                 '(S13 vftable modelling)'),
    'x86-custom-cc':  ('artifact', 'S13', False, 'MSVC whole-program custom register convention not in units.json'),
    'msvc-inlined':   ('artifact', 'S13', False, 'MSVC inlined a callee the native calls out of line'),
    'runtime-equiv':  ('artifact', 'S13', False, 'the same runtime function under two addresses (before difffuzz-8)'),
    'stack-arg':      ('artifact', 'S13', False, 'each machine\'s own stack address through an int (fixed since)'),
    'literal-arg':    ('artifact', 'S13', False, 'a literal at its image address vs the unit copy (fixed since)'),
    'literal-ret':    ('artifact', 'S13', False, 'a pointer result into the native unit\'s .rodata whose oracle '
                                                 'pointee the harness could not read (literal not pinned)'),
    'oracle-trap':    ('artifact', 'S13', False, 'the oracle executed int3/0xCC .text filler: code the harness did '
                                                 'not load (unit membership / preload gap)'),
    'new-null':       ('artifact', 'S13', False, 'the native constructs into the null an intercepted allocator '
                                                 'returned (GCC drops the null test after a throwing operator new, '
                                                 'MSVC 2008 keeps it); the game\'s allocators never return null: '
                                                 'canned allocator results should be heap addresses'),
    'oracle-native-address': ('artifact', 'S13', False, 'the oracle dereferences an address in the AArch64 '
                                                          'private range (stubs/native image): a native address '
                                                          'leaked to the x86 machine through the harness'),
    'literal-store':  ('artifact', 'S13', False, 'a pointer to a literal stored: the oracle\'s pinned-pool copy vs '
                                                 'the native unit\'s .rodata copy (stores do not compare pointees)'),
    'oracle-callee-store': ('artifact', 'S13', False, 'the first differing store is made by a callee the oracle '
                                                      'runs (preloaded or followed) while the native calls it out of '
                                                      'line and the harness intercepts it: no side effects natively'),
    'no-native-entry': ('artifact', 'S13', False, 'the native image has no entry for the function (link subset)'),
    'vcall-garbage':  ('artifact', 'S13', False, 'both machines call a word of memory the case did not plant (no '
                                                 'code there on either side): an object without a vftable, each '
                                                 'machine reading its own slot of garbage (Itanium has one destructor '
                                                 'slot more); invalid input for the exe'),
    'vcall-stub-alias': ('artifact', 'S13', False, 'the same garbage call target on both machines: a word in the '
                                                   'AArch64 stub range that the native trace names as the code stub\'s '
                                                   'original address (word - 0x08000000) or name:<hex>, the oracle as '
                                                   'the word itself (runner.compare should not count it)'),
    'oracle-private-address': ('artifact', 'S13', False, 'the oracle faults on a garbage address in its own private '
                                                          'range (0x0e000000: GDT/TEB/stubs) that the native machine '
                                                          'maps as background'),
    'ctor-this':      ('artifact', 'S13', False, 'one side uses the block a game allocator returned, the other the '
                                                 'canned result of the next intercepted call (the constructor run on '
                                                 'that block): the real constructor returns this, the canned result '
                                                 'is random'),
    'canned-this':    ('artifact', 'S13', False, 'the oracle passes the canned eax of an earlier intercepted call as a '
                                                 'pointer where the native passes its own frame temporary: the callee '
                                                 'returns its result/this pointer (MSVC aggregate return, operator=), '
                                                 'the harness gives it a random result'),
    'native-wild-private': ('artifact', 'S13', False, 'random background: a code pointer read from background bytes '
                                                      'lands in the AArch64 private range; x86 intercepts it as a '
                                                      'wild call, AArch64 executes its private memory (fault:x)'),
    'stub-normalized': ('artifact', 'S13', False, 'a stored data word with top byte 0x08 (a canned or background '
                                                  'value) that arm64emu rewrites as a code-stub pointer to its '
                                                  'original address (top byte 0x00): only that byte differs'),
    'thunk-binding':  ('real-bug', 'S2c', True, 'the original address is an override\'s MSVC entry (this = the '
                                                'secondary-base subobject); the link binds __fnv_<addr> to the GCC '
                                                'primary entry (this = the full object), not its _ZThn<d>_ thunk'),
    'native-zero-stub': ('real-bug', 'S1', True, 'the native has 0 where the oracle has a value, and the native '
                                                 'function or a direct callee in its unit is a placeholder body '
                                                 '`mov w0, #0; ret` (a stand-in the port never filled in)'),
    'native-stale-result': ('real-bug', 'S1b', True, 'the native passes the previous intercepted call\'s result '
                                                     'register (x0/v0 unchanged) where the oracle passes another '
                                                     'value: an argument the native caller never set (its '
                                                     'declaration of the callee differs from the definition)'),
    # --- real bugs -----------------------------------------------------------------------------------------------
    'uninit-arm':     ('real-bug', 'S1b', True, 'the native result depends on garbage: an argument slot the caller '
                                                'never wrote (ABI pop-mismatch) or an uninitialised local'),
    'native-garbage-arg': ('real-bug', 'S1b', True, 'the native passes one of its own garbage words as an argument '
                                                    '(runner arm_garbage): an argument nobody set'),
    'split-global':   ('real-bug', 'S2c', True, 'the native reaches its unit\'s copy of an image variable'),
    'native-undefined': ('real-bug', 'S1', True, 'the native calls a name the link binds to nothing'),
    'native-stub-data': ('real-bug', 'S2c', True, 'the native reads or writes a code/named stub as data: a data '
                                                  'symbol the link left as a stub (undefined variable)'),
    'native-address': ('real-bug', 'S2c', True, 'the native stores/passes/returns an address in its own image '
                                                'where the oracle has an image-window address: an unrebound '
                                                'variable or table (or a literal: check the pointee)'),
    'vptr-unpaired':  ('real-bug', 'S2c', False, 'no kind-V row pairs the native vtable with the image vftable'),
    'falloff?':       ('real-bug', 'S1', True, 'the native runs off a function end (FALL1)'),
    'after-native-undefined': ('real-bug', 'S1', True, 'the function calls a C++ name the link binds to nothing '
                                                       '(native-undefined): the native side gets a canned result '
                                                       'where the oracle runs the body; fix the name, then re-fuzz'),
    'native-empty-callee': ('real-bug', 'S1', True, 'only the oracle faults, and the native calls a function its '
                                                    'own object defines as a bare ret (a decomp stub of an exe '
                                                    'function or an empty destructor): the exe\'s work there, which '
                                                    'reads memory, is skipped natively'),
    'native-trap':    ('real-bug', 'S1', True, 'the native executes a trap (FALL1 __builtin_trap or GCC '
                                               'unreachable) on a path where the exe returns'),
    'layout-offset':  ('real-bug', 'S1', True, 'a pointer into the same object at another offset (a few words): a '
                                               'base subobject or member placed differently (layout)'),
    'field-width':    ('real-bug', 'S1', True, 'the machines read one field with another width or signedness '
                                             '(char vs int, short vs int, bool byte vs word): a member type differs'),
    'stack-protector': ('real-bug', 'S1', True, 'the native overruns a local buffer that MSVC\'s frame tolerated'),
    'native-null-read': ('real-bug', 'S1', True, 'the native dereferences a near-null address the oracle never '
                                                 'touches (a field read at another offset, or a hoisted load)'),
    'sret-mismatch':  ('real-bug', 'S1b', True, 'the hidden result pointer differs at a call (MSVC vs Itanium '
                                                'return-in-memory rules)'),
    'callee-saved':   ('real-bug', 'S1b', True, 'callee-saved registers clobbered: an ABI break'),
    'call-args':      ('real-bug', 'S2c', True, 'a bridge or definition delivers other argument bytes'),
    'signedness':     ('real-bug', 'S1', True, 'the divergence goes when an input byte >= 0x80 loses its top bit'),
    'float':          ('real-bug', 'S1', True, 'a float difference beyond tolerance (inf/NaN on one side)'),
    # --- not decided from the record -------------------------------------------------------------------------------
    'call-flow':      ('triage', 'S13b', True, 'the call sequence differs; inlining, rules and DECL1 rebinds explain '
                                               'most (replay with effects)'),
    'signedness?':    ('triage', 'S13b', True, 'a top-bit probe hit without a widening instruction in the oracle'),
    'native-early-exit': ('triage', 'S13b', True, 'the native makes a prefix of the oracle\'s calls and stops: an '
                                                  'early return or a branch on a field read differently'),
    'native-extra-calls': ('triage', 'S13b', True, 'the native makes the oracle\'s calls and more: a check the '
                                                   'oracle skips, or a call MSVC inlined into a loop'),
    'store-width':    ('triage', 'S13b', True, 'the same bytes stored with another width: one side writes bytes '
                                               'the other leaves (a bool/char vs dword member, a padded struct copy)'),
    'native-only-store': ('triage', 'S13b', True, 'the native writes memory the oracle never writes'),
    'x86-only-store': ('triage', 'S13b', True, 'the oracle writes memory the native never writes'),
    'oracle-fault':   ('triage', 'S13b', True, 'only the oracle faults on a data access: a dead load GCC dropped '
                                               '(by design) or a field the native reads at another offset (layout)'),
    'arg-stack-vs-object': ('triage', 'S13b', True, 'one side passes a temporary in its own frame, the other the '
                                                    'object itself: copy elision (by design) or a by-value '
                                                    'aggregate passed differently (ABI)'),
    'arg-other-buffer': ('triage', 'S13b', True, 'the two sides pass different argument objects: another member '
                                                 'or parameter (layout / argument order)'),
    'import-slot-called': ('real-bug', 'S2c', True, 'the oracle calls an import, the native jumps to the IAT slot '
                                                    'address itself (the slot bound as code, not loaded through)'),
    'native-inlined': ('triage', 'S13b', True, 'the oracle calls X, the native function never names X: GCC inlined '
                                               'it (an oracle body the adaptive follow could not load) or the '
                                               'native source does not make that call'),
    'vptr-differs':   ('triage', 'S2c', True, 'both sides store an image vftable, different ones, outside a '
                                              'destructor: a vtable paired with the wrong vftable (kind-V row) or a '
                                              'constructor order difference'),
    'unclassified':   ('triage', 'S13b', True, 'no rule matched'),
    # --- call-flow sub-shapes (_rule_callflow) -----------------------------------------------------------------------
    'deleting-dtor-flags': ('by-design', '-', False, 'MSVC\'s deleting destructor (??_G/??_E) frees only when flags '
                                                     'bit 0 is set (an explicit destructor call passes 0); the '
                                                     'Itanium D0 at that address always frees: such a call reaches '
                                                     'D1 natively'),
    'byvalue-arg-dtor': ('by-design', '-', False, 'the function takes a class by value: MSVC destroys such an '
                                                   'argument in the callee (the oracle\'s last calls free its '
                                                   'buffer), the Itanium ABI in the caller'),
    'libc-variant':   ('artifact', 'S13', False, 'the same C library function under another entry: an MSVC x87 '
                                                 'intrinsic (_CIacos ...) or the double function vs libc or its '
                                                 'float variant, sprintf vs GCC\'s fortified __sprintf_chk, atof vs '
                                                 'strtod: libcalls.X86/FAMILY do not pair them'),
    'oracle-callee-unloaded': ('artifact', 'S13', False, 'the oracle intercepts X (canned result) while the native '
                                                         'object defines X and runs its body: X is not in the '
                                                         'oracle\'s code set (unit membership / follow gap)'),
    'dyninit-no-atexit': ('real-bug', 'S10', False, 'the dynamic initializer registers the object\'s destructor '
                                                    'with atexit in the exe; the native initializer does not '
                                                    '(no fnvnx_xc_atexit): the destructor never runs at exit'),
    'native-empty-body': ('real-bug', 'S1', True, 'the native function, or a decomp-local stand-in it calls for an '
                                                  'original function defined elsewhere, is an empty body (ret / '
                                                  'return constant) where the exe runs code that makes calls'),
    'native-calls-data': ('real-bug', 'S1', True, 'the native calls an image data address where the exe calls a '
                                                  'function: a virtual slot read past the end of a decomp class\'s '
                                                  'shorter vtable (or a data word called as code)'),
    'native-missing-free': ('real-bug', 'S1', True, 'the exe frees a block here (CRT free / operator delete / '
                                                    'MemoryManager::Deallocate) and the native function reaches no '
                                                    'deallocator at all, and makes no other call in its place: the '
                                                    'native source omits the free (a leak)'),
    'native-vcall-oracle-direct': ('triage', 'S13b', True, 'the native makes an indirect call through fuzzed '
                                                           'memory where the exe calls a function directly: '
                                                           'a member the decomp made virtual, or a call MSVC '
                                                           'devirtualised (equivalent on a real object)'),
}

CLASS_ORDER = ('real-bug', 'triage', 'artifact', 'by-design')


def policy(cause):
    return POLICY.get(cause, ('triage', 'S13b', True, 'cause not in the policy table'))


# --------------------------------------------------------------------------------------------- record-level rules

def _in(v, rng):
    return rng[0] <= v < rng[1]


def _word(hexs, off=0):
    b = bytes.fromhex(hexs or '')
    if len(b) < off + 4:
        return None
    return struct.unpack_from('<I', b, off)[0]


def _float_close(bx, ba):
    """(tolerant, both_nan) of two IEEE values of the same width (runner.float_verdict's tolerance)."""
    fmt = {4: ('<f', '<i', 1e-6), 8: ('<d', '<q', 1e-12)}.get(len(bx))
    if fmt is None or len(ba) != len(bx):
        return False, False
    fx, fa = struct.unpack(fmt[0], bx)[0], struct.unpack(fmt[0], ba)[0]
    if math.isnan(fx) and math.isnan(fa):
        return True, True
    if math.isnan(fx) or math.isnan(fa) or math.isinf(fx) or math.isinf(fa):
        return False, False
    ulp = abs(struct.unpack(fmt[1], bx)[0] - struct.unpack(fmt[1], ba)[0])
    rel = abs(fx - fa) / max(abs(fx), abs(fa), 1e-300)
    return ulp <= 4 or rel <= fmt[2], False


def _fault_addr(err):
    """('r'|'w'|'x', address) of a 'fault:<k>:<hex>' error, None otherwise (ilp32-high and uc faults too)."""
    p = str(err or '').split(':')
    if len(p) == 3 and p[0] == 'fault' and p[1] in ('r', 'w', 'x'):
        try:
            return p[1], int(p[2], 16)
        except ValueError:
            return None
    return None


def _rule_error(d):
    x, a = d.get('x86'), d.get('arm')
    if x is None and isinstance(a, str):
        if a.startswith('exc:'):
            return 'native-trap'
        if a == 'stack-protector':
            return 'stack-protector'
        if a == 'no-native-entry':
            return 'no-native-entry'
        fa = _fault_addr(a)
        if fa:
            if _in(fa[1], A64_PRIV) and fa[0] in ('r', 'w'):
                return 'native-stub-data'
            if fa[1] < NULL_END:
                return 'native-null-read'
    if a is None and isinstance(x, str):
        if x.startswith('exc:3') or x == 'exc:3':
            return 'oracle-trap'
        fx = _fault_addr(x)
        if fx and fx[0] in ('r', 'w') and _in(fx[1], A64_PRIV):
            return 'oracle-native-address'
        if fx and fx[0] in ('r', 'w'):
            return 'oracle-fault'
    return None


def _rule_call_args(d):
    if d.get('arm_garbage'):
        return 'native-garbage-arg'
    if d.get('arg') == 'sret':
        return 'sret-mismatch'
    bx, ba = bytes.fromhex(d.get('x86') or ''), bytes.fromhex(d.get('arm') or '')
    if d.get('cls') in ('F32', 'F64'):
        tol, nan = _float_close(bx, ba)
        if nan:
            return 'nan-bits'
        if tol:
            return 'x87-precision'
    if d.get('cls') == 'I32' and len(bx) == 4 and len(ba) == 4:
        vx, va = _word(d['x86']), _word(d['arm'])
        ex, ea = (vx >> 23) & 0xff, (va >> 23) & 0xff
        if ex == ea and 0 < ex < 0xff and (vx >> 31) == (va >> 31) and abs((vx & 0x7fffff) - (va & 0x7fffff)) <= 256:
            return 'x87-precision'          # float bits through an int parameter, a few ulp apart
    if d.get('cls') in ('I32', 'P') and len(bx) >= 4 and len(ba) >= 4:
        vx, va = _word(d['x86']), _word(d['arm'])
        if _in(vx, LIT_POOL) and _in(va, A64_IMAGE):
            return 'literal-arg'
        if _in(vx, WINDOW) and _in(va, A64_IMAGE):
            return 'native-address'
        if _in(va, A64_STUBS) and not _in(vx, A64_STUBS):
            return 'native-stub-data'
        objs = (BUFS, HEAP)
        if (_in(vx, STACK) and any(_in(va, o) for o in objs)) or (_in(va, STACK) and any(_in(vx, o) for o in objs)):
            return 'arg-stack-vs-object'
        for reg in (BUFS, HEAP, WINDOW):
            if _in(vx, reg) and _in(va, reg) and 0 < abs(vx - va) <= 0x100 and (vx - va) % 4 == 0:
                return 'layout-offset'      # the same object, another member offset (base or field placement)
        if _in(vx, BUFS) and _in(va, BUFS):
            return 'arg-other-buffer'
    return None


def _rule_return(d, conv, sym=None):
    if str(sym or '').startswith(('??_G', '??_E')):
        return 'deleting-dtor-ret'
    if 'pointee_x86' in d and (not d.get('pointee_x86') or not d.get('pointee_arm')):
        return 'literal-ret'
    fl = d.get('float') or {}
    if fl and not fl.get('tolerant'):
        try:
            if fl.get('x87') is not None and float(fl['x87']) == float(fl.get('arm')):
                return 'x87-precision'      # the native value is the oracle's unrounded x87 value
        except (TypeError, ValueError):
            pass
    vx, va = _word(d.get('x86')), _word(d.get('arm'))
    if vx is not None and va is not None and _in(vx, LIT_POOL) and _in(va, A64_IMAGE):
        return 'literal-ret'
    if vx is not None and va is not None and _in(vx, WINDOW) and _in(va, A64_IMAGE):
        return 'native-address'
    return None


def _rule_mem_write(d, rec=None):
    fw = d.get('float_word') or {}
    if fw.get('tolerant'):
        return 'x87-precision'
    n, ox, oa = d.get('ndiff', 0), d.get('only_x86', 0), d.get('only_arm', 0)
    bx, ba = bytes.fromhex(d.get('x86') or ''), bytes.fromhex(d.get('arm') or '')
    if len(bx) >= 4 and len(ba) >= 4:
        tol, nan = _float_close(bx[:4], ba[:4])
        if nan:
            return 'nan-bits'
        vx, va = _word(d['x86']), _word(d['arm'])
        if _in(vx, LIT_POOL) and _in(va, A64_IMAGE):
            return 'literal-store'
        vft = image_vftables()
        if vx in vft and va in vft and vx != va:
            return 'vptr-store' if _is_dtor((rec or {}).get('symbol')) else 'vptr-differs'
        if _in(vx, WINDOW) and _in(va, A64_IMAGE):
            return 'native-address'
        if _in(va, A64_STUBS) and not _in(vx, A64_STUBS):
            return 'native-stub-data'
    if n and ox == n:
        # the oracle writes bytes the native leaves; when the native wrote the same value in the lower bytes
        # (only the upper bytes are missing) it is a narrower store, otherwise a store the native does not make
        return 'store-width' if _narrower(bx, ba) else 'x86-only-store'
    if n and oa == n:
        return 'store-width' if _narrower(ba, bx) else 'native-only-store'
    if n and ox + oa == n and ox and oa:
        return 'store-width'
    return None


def _narrower(wide, narrow):
    """`narrow` equals `wide` on a non-empty prefix and is zero after it (a byte/short store vs a dword)."""
    if len(wide) != len(narrow):
        return False
    k = len(narrow.rstrip(b'\0'))
    return 0 < k < len(wide) and narrow[:k] == wide[:k]


_IAT = None


def iat_names():
    """{IAT slot address: api name} from platform/win32/imports.tsv (S4)."""
    global _IAT
    if _IAT is None:
        _IAT = {}
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'platform', 'win32', 'imports.tsv')
        if os.path.exists(p):
            for line in open(p, encoding='utf-8', errors='replace'):
                f = line.rstrip('\n').split('\t')
                if len(f) >= 3 and f[0].startswith('0x'):
                    _IAT[int(f[0], 16)] = f[2]
    return _IAT


def _rule_calls(d, rec=None):
    tx, ta = d.get('x86_trace'), d.get('arm_trace')
    if d.get('kind') == 'call-target':
        x, a = str(d.get('x86') or ''), str(d.get('arm') or '')
        if a.startswith('name:_Z'):
            # the native calls a named stub: a C++ name the link binds to nothing (a provider would trace as its
            # address); report.reclassify finds the same with the link's providers, this is the fallback without
            return 'native-undefined'
        if x.startswith('imp:'):
            try:
                if _in(int(a, 16), WINDOW):
                    # the link binds a call of an import to the api (fnvlink `import`), and the harness gives the
                    # native api symbol the address of its IAT slot (engine.iat_names): the same import is the
                    # same call (S2c-abi: the 11 rows of reports/s13b-causes: VirtualAlloc, VirtualFree, EnterCriticalSection)
                    if iat_names().get(int(a, 16)) == x[4:]:
                        return 'runtime-equiv'
                    return 'import-slot-called'
            except ValueError:
                pass
        if rec is not None and not x.startswith(('imp:', 'name:')):
            cs = native_callees(rec.get('source'), int(rec.get('address') or '0', 16))
            try:
                ls = link_symbol(int(x, 16))
            except ValueError:
                ls = None
            if cs is not None and '__fnv_' + x not in cs and not any(n.startswith('__fnv_' + x) for n in cs) \
                    and ls not in cs:
                return 'native-inlined'
    return _rule_count(d, tx, ta)


def _rule_count(d, tx, ta):
    if d.get('kind') == 'call-count' and isinstance(tx, list) and isinstance(ta, list):
        if len(ta) < len(tx) and tx[:len(ta)] == ta:
            return 'native-early-exit'
        if len(ta) > len(tx) and ta[:len(tx)] == tx:
            return 'native-extra-calls'
    return None


# ------------------------------------------------- oracle-vcall / oracle-fault split (difffuzz-13 vftable planting)
#
# classify.py gives oracle-vcall to every call-target the runner marked vcall='unplanted' (the oracle's target is
# neither a direct callee nor a planted slot) and to every oracle-only fault it finds next to an indirect call or
# at an unmapped pc; causes.py's _rule_error leaves the other oracle-only data faults at oracle-fault. These rules
# split what the record shows; what they cannot split keeps its cause.

HINT_REFINABLE = {'oracle-vcall'}        # probe causes refine() still splits (report.reclassify hands them over)


def _code_target(t):
    """Is call target `t` (a trace string) code on some machine: an exe .text address, an import, a named stub,
    or the AArch64 private range (stubs, image, synthesized vtables)?"""
    if t.startswith(('imp:', 'name:')):
        return True
    try:
        v = int(t, 16)
    except ValueError:
        return True
    return _in(v, TEXT) or _in(v, A64_PRIV)


def _stub_alias(x, a):
    """The native trace names the garbage word the oracle called: a word in the code-stub range traced as its
    original address (word - 0x08000000), one in the named-stub range as name:<hex>."""
    try:
        vx = int(x, 16)
    except ValueError:
        return False
    if not _in(vx, A64_STUBS):
        return False
    return a == 'name:%x' % vx or a == '%08x' % (vx - A64_STUBS[0])


def _rule_vcall(rec):
    d = rec.get('_now') or rec.get('divergence') or {}
    if d.get('kind') != 'call-target':
        return _rule_oracle_fault(d, rec) if d.get('kind') == 'error' else None
    x, a = str(d.get('x86') or ''), str(d.get('arm') or '')
    if any(str(t).startswith('name:__s13_vtpad_') for t in [a] + list(d.get('arm_trace') or [])):
        # the native call went through a padding slot past the native vtable the link pairs with the planted vftable
        # (vtmodel.py): classify.py's own vptr-unpaired, shadowed there by its earlier unplanted test
        return 'vptr-unpaired'
    if _stub_alias(x, a):
        return 'vcall-stub-alias'
    if a.startswith('name:_Z') and not (rec.get('case') or {}).get('vptrs') and \
            _unbound_name(rec.get('source'), rec.get('address'), a[5:]) is not False:
        # no vptr was planted (so not a slot of a synthesized vtable): the native calls a C++ name that neither its
        # object defines nor the link binds (GCC devirtualised it, or the decomp calls it directly); it traps there
        return 'native-undefined'
    if not _code_target(x) and not _code_target(a):
        return 'vcall-garbage'
    return None


def _unbound_name(source, addr, name):
    """True when neither the native object of `source` defines `name` nor the address map binds it, False when one
    does, None without the object or the map (callers fall back to the record alone)."""
    try:
        if native_callees(source, int(addr or '0', 16)) is None:
            return None
    except (TypeError, ValueError):
        return None
    _load_addrmap()
    if not _LINKSYM:
        return None
    path = os.path.join(_obj_dir(), source.replace('/', '_') + '.o')
    return name not in (_OBJ_CACHE.get(path) or {}).get('_defined', set()) and name not in _BOUND


def _rule_oracle_fault(d, rec):
    """An oracle-only fault (oracle-vcall error or oracle-fault): a garbage pointer into the oracle's own private
    range, a word read across 4 GiB, or a native function that calls an empty local stub where the oracle works."""
    if d.get('arm') is not None:
        return None
    fx = _fault_addr(d.get('x86'))
    if fx and _in(fx[1], X86_PRIV):
        return 'oracle-private-address'
    if fx and fx[0] in ('r', 'w') and fx[1] >= 0xfffffffd:
        return 'ilp32-pointer'         # a dword access at a near-null pointer minus a few bytes, wrapped past 4 GiB
    try:
        em = empty_callees(rec.get('source'), int(rec.get('address') or '0', 16))
    except (TypeError, ValueError):
        em = None
    if em and (fx or str(d.get('x86') or '').startswith('fault:')):
        return 'native-empty-callee'
    return None


# --------------------------------------------------------------------- native object facts (optional, static)
#
# Where the link's rewritten objects are at hand (config.LINK/fixed/<unit>.o on a farm, or FNVNX_CAUSES_OBJ), a rule
# may look at the native function's own direct callees. Without the objects these rules simply do not fire.

ALLOCATORS = {'_Znwj', '_Znaj', '_Znwm', '_Znam', '_ZnwjRKSt9nothrow_t', '__fnv_00401000', '__fnv_00aa3e40',
              '__fnv_00aa13e0'}       # operator new, MemoryManager::Allocate, the heap's Allocate (game allocators)
_OBJ_CACHE = {}
# native object reader state, reported in every summary: without pyelftools every object rule (native-inlined,
# new-null, native-undefined callees, after-native-undefined) is skipped, which must not pass silently
OBJ_STATS = dict(reader=None, scanned=0, missing=0)


def _obj_dir():
    d = os.environ.get('FNVNX_CAUSES_OBJ')
    if d:
        return d
    try:
        import config
        return str(config.LINK / 'fixed')
    except Exception:  # noqa: BLE001
        return None


def native_callees(source, addr):
    """Names the native definition of original function `addr` calls directly (bl/b relocations inside its
    extent), or None when the object or the function is not available."""
    d = _obj_dir()
    if not d or not source:
        return None
    path = os.path.join(d, source.replace('/', '_') + '.o')
    if path not in _OBJ_CACHE:
        if os.path.exists(path):
            _OBJ_CACHE[path] = _scan_object(path)
            OBJ_STATS['scanned'] += _OBJ_CACHE[path] is not None
        else:
            _OBJ_CACHE[path] = None
            OBJ_STATS['missing'] += 1
    t = _OBJ_CACHE[path]
    if t is None:
        return None
    return t.get(addr)


def _scan_object(path):
    """{original address: set of called names} for every __fnv_<addr> defined in the object."""
    try:
        from elftools.elf.elffile import ELFFile
        from elftools.elf.relocation import RelocationSection
    except ImportError:
        if OBJ_STATS['reader'] is None:
            sys.stderr.write('causes.py: pyelftools is not installed: native object rules are OFF '
                             '(pip install pyelftools)\n')
        OBJ_STATS['reader'] = 'unavailable'
        return None
    OBJ_STATS['reader'] = 'pyelftools'
    out = {}
    with open(path, 'rb') as f:
        e = ELFFile(f)
        st = e.get_section_by_name('.symtab')
        if st is None:
            return None
        syms = list(st.iter_symbols())
        by_sec = collections.defaultdict(list)          # section index -> [(start, end, orig addr)]
        sized = collections.defaultdict(int)             # (section, value) -> the size of a function symbol there
        for sy in syms:
            if sy['st_info']['type'] == 'STT_FUNC' and isinstance(sy['st_shndx'], int):
                k = (sy['st_shndx'], sy['st_value'])
                sized[k] = max(sized[k], sy['st_size'])
        for sy in syms:
            n = sy.name
            if n.startswith('__fnv_') and isinstance(sy['st_shndx'], int) and len(n) >= 14:
                try:
                    a = int(n[6:14], 16)
                except ValueError:
                    continue
                k = (sy['st_shndx'], sy['st_value'])
                if sized[k]:
                    by_sec[sy['st_shndx']].append((sy['st_value'], sy['st_value'] + sized[k], a))
        for sec in e.iter_sections():
            if not isinstance(sec, RelocationSection) or sec['sh_info'] not in by_sec:
                continue
            spans = by_sec[sec['sh_info']]
            for rel in sec.iter_relocations():
                if rel['r_info_type'] not in (20, 21, 282, 283):     # P32_JUMP26/CALL26 (ILP32), JUMP26/CALL26 (LP64)
                    continue
                off = rel['r_offset']
                name = syms[rel['r_info_sym']].name
                for lo, hi, a in spans:
                    if lo <= off < hi:
                        out.setdefault(a, set()).add(name)
        for spans in by_sec.values():
            for lo, hi, a in spans:
                out.setdefault(a, set())
        out['_defined'] = {sy.name for sy in syms if isinstance(sy['st_shndx'], int) and sy.name}
        out['_empty'] = _empty_functions(e, syms)
    return out


A64_RET = 0xd65f03c0


def _empty_functions(e, syms):
    """Names of the functions the object defines whose first instruction is `ret` (an empty body)."""
    out, data = set(), {}
    for sy in syms:
        if sy['st_info']['type'] != 'STT_FUNC' or not isinstance(sy['st_shndx'], int) or not sy.name:
            continue
        i = sy['st_shndx']
        if i not in data:
            sec = e.get_section(i)
            data[i] = sec.data() if sec['sh_type'] == 'SHT_PROGBITS' else b''
        v = sy['st_value']
        if v + 4 <= len(data[i]) and struct.unpack_from('<I', data[i], v)[0] == A64_RET:
            out.add(sy.name)
    return out


def empty_callees(source, addr):
    """Direct callees of the native function `addr` that its own object defines with an empty body (a bare `ret`):
    decomp stubs standing in for exe code. None without the object."""
    cs = native_callees(source, addr)
    if cs is None:
        return None
    path = os.path.join(_obj_dir(), source.replace('/', '_') + '.o')
    return sorted(cs & (_OBJ_CACHE.get(path) or {}).get('_empty', set()))


def unbound_callees(source, addr):
    """C++ names the native function calls that neither its object defines nor the link's address map binds
    (declared-only members, inline functions never emitted): the native side calls a trap stub there. None
    without the object or the map."""
    cs = native_callees(source, addr)
    if cs is None:
        return None
    _load_addrmap()
    if not _LINKSYM:
        return None
    path = os.path.join(_obj_dir(), source.replace('/', '_') + '.o')
    defined = (_OBJ_CACHE.get(path) or {}).get('_defined', set())
    return sorted(n for n in cs if n.startswith('_Z') and n not in defined and n not in _BOUND)


_VFT = None
_LINKSYM = None
_STARTS = []             # sorted original start addresses of every code entry in the address map (extents by bisect)
_BOUND = set()           # every link_symbol / native_symbol the address map names


def image_vftables():
    """Original addresses of image vftables (address map kind V of the link), empty without the map."""
    _load_addrmap()
    return _VFT


def link_symbol(addr):
    """The name the link binds original address `addr` to (address map link_symbol: `free`, `memmove_s`,
    `__fnv_<addr>`), or None."""
    _load_addrmap()
    return _LINKSYM.get(addr)


def _load_addrmap():
    global _VFT, _LINKSYM
    if _VFT is None or _LINKSYM is None:
        _VFT, _LINKSYM = set(), {}
        del _STARTS[:]
        p = os.environ.get('FNVNX_CAUSES_ADDRMAP')
        if not p:
            try:
                import config
                p = str(config.LINK / 'out' / 'addrmap.tsv')
            except Exception:  # noqa: BLE001
                p = None
        if p and os.path.exists(p):
            for line in open(p):
                f = line.split('\t', 3)
                if len(f) < 3 or line.startswith('#'):
                    continue
                try:
                    a = int(f[0], 16)
                except ValueError:
                    continue
                if f[1] in ('F', 'E', 'C', 'X') and TEXT[0] <= a < TEXT[1]:
                    _STARTS.append(a)
                if f[1] == 'V':
                    _VFT.add(a)
                elif f[2]:
                    _LINKSYM[a] = f[2]
                _BOUND.add(f[2])
                if len(f) > 3:
                    _BOUND.add(f[3].split('\t', 1)[0])
            _STARTS.sort()


def _is_dtor(sym):
    return str(sym or '').startswith(('??1', '??_G', '??_E'))


def _calls_allocator(rec):
    try:
        cs = native_callees(rec.get('source'), int(rec.get('address') or '0', 16))
    except ValueError:
        return False
    return bool(cs and cs & ALLOCATORS)


# ------------------------------------------------------------------ call-flow sub-shapes (call-count / call-target)
#
# What the oracle calls and the native does not (or the other way round), decided from the two traces, the record's
# case and the native object (function bodies, which names it defines). Each rule needs its static facts; without
# the object or the address map it does not fire.

ATEXIT = 0x00ec658f                         # CRT _atexit (libcalls.EQUIV: fnvnx_xc_atexit / atexit)
ATEXIT_NAMES = {'fnvnx_xc_atexit', 'fnvnx_xc_cxa_atexit', 'atexit', '__cxa_atexit'}
DEALLOCATORS = {0x00401030, 0x00aa1460, 0x00aa4060, 0x00ecd291}    # delete, sized delete, Deallocate, CRT free
CRT_TEXT = (0x00ec3000, 0x00ee0000)         # libcmt inside the exe's .text
MATH = {'sin', 'cos', 'tan', 'asin', 'acos', 'atan', 'atan2', 'exp', 'log', 'log10', 'pow', 'fmod', 'sqrt', 'floor',
        'ceil', 'sinh', 'cosh', 'tanh', 'ldexp', 'frexp', 'modf'}
LIBC_PAIRS = {('atof', 'strtod'), ('atoi', 'strtol'), ('atol', 'strtol')}
_FUNCS = {}               # object path -> _obj_funcs tuple (None: no object)
_CRT_E = None


def _obj_path(source):
    d = _obj_dir()
    return os.path.join(d, source.replace('/', '_') + '.o') if d and source else None


def _obj_funcs(source):
    """(names -> (section, value, size), (section, value) -> names, section -> bytes, (section, value) -> called
    names) of the native object, or None."""
    p = _obj_path(source)
    if p is None:
        return None
    if p not in _FUNCS:
        _FUNCS[p] = None
        try:
            from elftools.elf.elffile import ELFFile
            from elftools.elf.relocation import RelocationSection
        except ImportError:
            return None
        if os.path.exists(p):
            syms, at, code, calls = {}, collections.defaultdict(set), {}, collections.defaultdict(set)
            with open(p, 'rb') as f:
                e = ELFFile(f)
                st = e.get_section_by_name('.symtab')
                allsyms = list(st.iter_symbols()) if st is not None else []
                for sy in allsyms:
                    if sy.name and not sy.name.startswith('$') and isinstance(sy['st_shndx'], int) and \
                            sy['st_info']['type'] in ('STT_FUNC', 'STT_NOTYPE'):
                        k = (sy['st_shndx'], sy['st_value'])
                        at[k].add(sy.name)
                        old = syms.get(sy.name)
                        syms[sy.name] = (k[0], k[1], max(sy['st_size'], old[2] if old else 0))
                sized = {}
                for n, (sh, v, sz) in syms.items():
                    sized[(sh, v)] = max(sized.get((sh, v), 0), sz)
                syms = {n: (sh, v, sized[(sh, v)]) for n, (sh, v, sz) in syms.items()}
                spans = collections.defaultdict(list)
                for (sh, v), sz in sized.items():
                    if sz:
                        spans[sh].append((v, v + sz))
                for sh in spans:
                    sec = e.get_section(sh)
                    code[sh] = sec.data() if sec['sh_type'] == 'SHT_PROGBITS' else b''
                for sec in e.iter_sections():
                    if not isinstance(sec, RelocationSection) or sec['sh_info'] not in spans:
                        continue
                    for rel in sec.iter_relocations():
                        if rel['r_info_type'] not in (20, 21, 282, 283):     # (P32_)JUMP26 / CALL26
                            continue
                        off, n = rel['r_offset'], allsyms[rel['r_info_sym']].name
                        for lo, hi in spans[sec['sh_info']]:
                            if lo <= off < hi and n:
                                calls[(sec['sh_info'], lo)].add(n)
            _FUNCS[p] = (syms, at, code, calls)
    return _FUNCS[p]


def native_reaches(source, name, depth=4):
    """Every name the native function `name` calls directly or through functions its own object defines (a few
    levels deep), or None without the object."""
    t = _obj_funcs(source)
    if t is None or name not in t[0]:
        return None
    out, todo, seen = set(), [name], set()
    for _ in range(depth):
        nxt = []
        for n in todo:
            k = t[0].get(n)
            if k is None or k[:2] in seen:
                continue
            seen.add(k[:2])
            for c in t[3].get(k[:2], ()):
                if c not in out:
                    out.add(c)
                    nxt.append(c)
        todo = nxt
    return out


def native_defines(source, name):
    """True when the native object defines `name` (False: it does not; None: no object)."""
    t = _obj_funcs(source)
    return None if t is None else name in t[0]


def native_ran(source, name, trace, exclude=()):
    """True when the native trace (from the divergence on) shows that the native ran the unit-local function `name`:
    it holds a call `name` reaches that leaves the unit (a traced call: `__fnv_<addr>` as <addr>, others as
    name:<n>). The unit's own functions run untraced, so without such a call there is no evidence (V1
    tests/v1/test_s13b_callee_unloaded.py U1/U2): False, and the record keeps its call-flow cause. Names in
    `exclude` (what the caller calls itself) prove nothing about `name`."""
    reach = native_reaches(source, name)
    if not reach or not trace:
        return False
    toks = set()
    for n in reach:
        if native_defines(source, n) is not False or n in exclude:
            continue                                     # unit-local (untraced), unknown, or called by the caller too
        toks.add(n[6:] if n.startswith('__fnv_') and len(n) == 14 else 'name:' + n)
    return any(str(t) in toks for t in trace)


def native_body(source, name):
    """(code bytes, names at the same address) of a function the native object defines, or None."""
    t = _obj_funcs(source)
    if t is None or name not in t[0]:
        return None
    sh, v, sz = t[0][name]
    return t[2].get(sh, b'')[v:v + sz], t[1].get((sh, v), set())


def _empty_body(code):
    """`ret`, or constant moves into w0/x0/s0 then `ret`: a function that does nothing but return a constant."""
    if not code or len(code) % 4 or len(code) > 12:
        return False
    ws = struct.unpack('<%dI' % (len(code) // 4), code)
    if ws[-1] != 0xd65f03c0:
        return False
    for i in ws[:-1]:
        if (i & 0x7f80001f) not in (0x52800000, 0x12800000) and i not in (0x2a1f03e0, 0xaa1f03e0, 0x1e2703e0):
            return False                    # movz/movn w0|x0, #imm; mov w0|x0, wzr; fmov s0, wzr
    return True


_DEFS = None


def link_definition(addr):
    """(native symbol, object file) the address map binds original function `addr` to, or None."""
    global _DEFS
    if _DEFS is None:
        _DEFS = {}
        p = _addrmap_file()
        if p and os.path.exists(p):
            for line in open(p):
                f = line.split('\t')
                if len(f) > 4 and f[1] in ('F', 'X', 'C') and f[4].endswith('.o'):
                    try:
                        _DEFS[int(f[0], 16)] = (f[3] or f[2], f[4])
                    except ValueError:
                        pass
    return _DEFS.get(addr)


def _addrmap_file():
    p = os.environ.get('FNVNX_CAUSES_ADDRMAP')
    if not p:
        try:
            import config
            p = str(config.LINK / 'out' / 'addrmap.tsv')
        except Exception:  # noqa: BLE001
            p = None
    return p


def crt_entries():
    """Original addresses the link leaves to the CRT (address map kind E, 'CRT/thunk') inside the libcmt range."""
    global _CRT_E
    if _CRT_E is None:
        _CRT_E = set()
        p = _addrmap_file()
        if p and os.path.exists(p):
            for line in open(p):
                f = line.split('\t')
                if len(f) > 5 and f[1] == 'E' and 'CRT' in f[5]:
                    try:
                        a = int(f[0], 16)
                    except ValueError:
                        continue
                    if _in(a, CRT_TEXT):
                        _CRT_E.add(a)
    return _CRT_E


def _hex(t):
    try:
        return int(t, 16) if t and str(t)[:1].isdigit() else None
    except ValueError:
        return None


def _target_name(t):
    """C name a trace target stands for: 'name:<n>' or the link symbol of its address."""
    t = str(t or '')
    if t.startswith('name:'):
        return t[5:]
    a = _hex(t)
    return link_symbol(a) if a is not None else None


def _libc_variant(x, a):
    xa = _hex(x)
    if xa is None:
        return False
    n = _target_name(a)
    if not n:
        return False
    base = n[2:-4] if n.startswith('__') and n.endswith('_chk') else None
    m = link_symbol(xa)
    if m is None:
        # an unnamed CRT entry next to the C library (the x87 intrinsics _CIacos/_CIasin/...): GCC calls libm
        return xa in crt_entries() and (n in MATH or (n.endswith('f') and n[:-1] in MATH))
    if base is not None and base == m:
        return True                                     # sprintf vs __sprintf_chk (_FORTIFY_SOURCE)
    if m in MATH and n == m + 'f':
        return True                                     # atan vs atanf
    return (m, n) in LIBC_PAIRS


def _missing_free(src, addr, xa):
    """The oracle frees a block (CRT free, operator delete, MemoryManager::Deallocate) and the native function
    reaches no deallocator at all, directly or through its own object's functions."""
    if xa not in DEALLOCATORS:
        return False
    reach = native_reaches(src, '__fnv_%08x' % addr)
    if reach is None:
        return False
    import libcalls
    names = {'free', 'fnvnx_free'} | {n for n, b in libcalls.EQUIV.items() if b in DEALLOCATORS}
    for b in DEALLOCATORS:
        names.add('__fnv_%08x' % b)
        for n in (link_symbol(b), (link_definition(b) or (None,))[0]):
            if n:
                names.add(n)
    return not reach & names


def _callflow(d, rec):
    """_rule_callflow, except that an artifact or by-design shape never hides a function that calls an unbound C++
    name (after-native-undefined stays the record's cause there: the canned result may be what differs)."""
    got = _rule_callflow(d, rec)
    if got and policy(got)[0] in ('artifact', 'by-design'):
        try:
            if unbound_callees(rec.get('source'), int(rec.get('address') or '0', 16)):
                return None
        except (ValueError, TypeError):
            pass
    return got


def _rule_callflow(d, rec):
    if rec is None:
        return None
    tx, ta = d.get('x86_trace') or [], d.get('arm_trace') or []
    src = rec.get('source')
    try:
        addr = int(rec.get('address') or '0', 16)
    except ValueError:
        return None
    k = d.get('kind')
    if k == 'call-target':
        x, a = d.get('x86'), d.get('arm')
    elif k == 'call-count' and len(tx) > len(ta) and tx[:len(ta)] == ta:
        x, a = tx[len(ta)], None                         # the first call the native does not make
    elif k == 'call-count' and len(ta) > len(tx) and ta[:len(tx)] == tx:
        x, a = None, ta[len(tx)]
    else:
        return None
    me = native_body(src, '__fnv_%08x' % addr)
    # the function itself is an empty body: whatever the oracle calls, the native makes no call
    if x is not None and not ta and me is not None and _empty_body(me[0]):
        return 'native-empty-body'
    xa = _hex(x)
    if xa == ATEXIT:
        cs = native_callees(src, addr)
        if cs is not None and not cs & ATEXIT_NAMES:
            return 'dyninit-no-atexit'
    if x is None and str(rec.get('symbol') or '').startswith(('??_G', '??_E')):
        extra = [_hex(t) for t in ta[len(tx):]]
        p0 = ((rec.get('case') or {}).get('args') or {}).get('p0') or ''
        if extra and all(t in DEALLOCATORS for t in extra) and p0[:2] and not int(p0[:2], 16) & 1:
            return 'deleting-dtor-flags'
    aa = _hex(a)
    if k == 'call-target' and aa is not None and _in(aa, WINDOW) and xa is not None and _in(xa, TEXT):
        return 'native-calls-data'
    if k == 'call-target' and _libc_variant(x, a):
        return 'libc-variant'
    if xa is not None and xa != addr and native_defines(src, '__fnv_%08x' % xa):
        cs = native_callees(src, addr) or set()
        start = d.get('index') if isinstance(d.get('index'), int) else \
            next((i for i, (p, q) in enumerate(zip(tx, ta)) if p != q), min(len(tx), len(ta)))
        seen = list(ta[start:]) + ([a] if a else [])      # the native's calls from the divergence on
        if '__fnv_%08x' % xa in cs and native_ran(src, '__fnv_%08x' % xa, seen, exclude=cs):
            return 'oracle-callee-unloaded'
    # a decomp-local stand-in for an original function defined elsewhere, with an empty body: tied to the divergence
    # when the oracle calls that original where the native diverges, or when the native stops early
    if x is not None:
        for n in sorted(native_callees(src, addr) or ()):
            if n.startswith('__fnv_') or 'FUN_' not in n:
                continue
            b = native_body(src, n)
            if b is None or not _empty_body(b[0]) or any(m.startswith('__fnv_') for m in b[1]):
                continue
            for h in n.split('FUN_')[1:]:
                o = _hex(h[:8]) if len(h) >= 8 else None
                if o is not None and o != addr and (k == 'call-count' or o == xa) and link_symbol(o) and \
                        native_defines(src, link_symbol(o)) is False:
                    return 'native-empty-body'
    # MSVC destroys an aggregate passed by value in the callee (its buffer freed on the way out), Itanium in the caller
    if k == 'call-count' and a is None and 'AGG:' in str(rec.get('conv') or '').split('->')[0] and \
            all(_hex(t) in DEALLOCATORS for t in tx[len(ta):]):
        return 'byvalue-arg-dtor'
    # the native skips a free the exe makes: it goes straight on to the oracle's next call, or returns right there
    skip = (k == 'call-target' and a == d.get('x86_next')) or (k == 'call-count' and a is None and
                                                               len(tx) == len(ta) + 1)
    if skip and xa is not None and _missing_free(src, addr, xa):
        return 'native-missing-free'
    if k == 'call-target' and aa is not None and xa is not None and _in(xa, TEXT) and not _in(aa, TEXT) and \
            not _in(aa, WINDOW) and not _in(aa, A64_PRIV):
        return 'native-vcall-oracle-direct'
    return None


# ------------------------------------------------------------------ rules over `difffuzz.py effects` output
#
# `difffuzz.py effects --list <files> --out <effects.jsonl>` replays each record with every data access logged and
# names the first effect the machines disagree on. Where such a line exists for a record (summary --effects), these
# rules refine what the record rules left in triage.

EFFECTS = {}            # reproducer basename -> effects line


def load_effects(paths):
    for p in paths:
        for line in open(p):
            line = line.strip()
            if line:
                e = json.loads(line)
                if e.get('file'):
                    EFFECTS[os.path.basename(e['file'])] = e


def function_at(pc):
    """Original start address of the code entry of the address map that contains `pc`, or None."""
    import bisect
    _load_addrmap()
    i = bisect.bisect_right(_STARTS, pc) - 1
    return _STARTS[i] if i >= 0 else None


# causes whose shape says the native never made the call, or made it with its own code: a callee's store there is
# the native's own defect, never the harness's interception (V1, tests/v1/test_s13b_callee_store.py C1-C3)
NOT_INTERCEPTED = ('native-inlined', 'native-early-exit', 'x86-only-store')


def intercepted_callee(rec, pc):
    """The original callee containing x86 `pc` when the native calls it out of line through a name its own object
    does not define (the harness runs the unit's own functions natively and intercepts only those calls), else
    None. Without the native object or the address map there is no evidence: None."""
    try:
        addr = int((rec or {}).get('address') or '0', 16)
    except ValueError:
        return None
    cs = native_callees((rec or {}).get('source'), addr)
    callee = function_at(pc)
    if not cs or callee is None or callee == addr:
        return None
    path = os.path.join(_obj_dir(), (rec.get('source') or '').replace('/', '_') + '.o')
    defined = (_OBJ_CACHE.get(path) or {}).get('_defined', set())
    names = {'__fnv_%08x' % callee, link_symbol(callee)} - {None, ''}
    return callee if any(n in cs and n not in defined for n in names) else None


def _rule_effects(e, rec=None, cause=None):
    f = e.get('first')
    if f == 'width':
        return 'field-width'
    if f == 'store':
        st = e.get('store') or {}
        try:
            a, n = int((rec or {}).get('address') or e.get('address') or '0', 16), int((rec or {}).get('_size') or 0)
            pc = int(((st.get('x86') or {}).get('pc')) or '0', 16)
        except (TypeError, ValueError):
            a = n = pc = 0
        if (n and pc and not st.get('arm') and not a <= pc < a + n and cause not in NOT_INTERCEPTED
                and intercepted_callee(rec, pc) is not None):
            return 'oracle-callee-store'      # the store is made by a callee the oracle runs, the native intercepts
        w = st.get('word') or {}
        x, a = str(w.get('x86') or ''), str(w.get('arm') or '')
        if x.startswith('image address') and 'native image .data.rel.ro _ZTV' in a:
            return 'vptr-unpaired'
        if 'native named stub' in a or 'native stub' in a:
            return 'native-stub-data'
    if f == 'value':
        v = e.get('value') or {}
        a = str(v.get('arm') or '')
        if 'native named stub' in a or 'native stub' in a:
            return 'native-stub-data'
    return None


# ------------------------------------------------------- unclassified shapes: canned results, thunks, wild calls
#
# The harness's canned values are a function of the record's case (world.py, duplicated here): the k-th intercepted
# call returns case.ret_bytes(k) (eax / x0 / v0), a game allocator alloc_result(k) instead.

ALLOC_BASE = HEAP[0] + 0x200000


def _alloc_result(k):
    return ALLOC_BASE + 0x1000 * (k % 0x400) + 0x10


def _ret_bytes(case, k):
    rets = case.get('rets') or []
    if k < len(rets) and rets[k] is not None:
        return bytes.fromhex(rets[k]).ljust(16, b'\0')
    import random
    r = random.Random(((case.get('seed') or 0) * 1000003 + k) & 0xffffffff)
    return bytes(r.getrandbits(8) for _ in range(16))


def _canned_word(case, v, upto=64):
    """Indices k < upto whose canned result word (eax / x0) is v."""
    return [k for k in range(upto) if struct.unpack_from('<I', _ret_bytes(case, k))[0] == v]


def _alloc_index(v, upto=64):
    for k in range(upto):
        if _alloc_result(k) == v:
            return k
    return None


def _ctor_pair(case, vx, va, limit):
    """One side is the allocator block of call j, the other the canned result of a later call m < limit."""
    for a, b in ((vx, va), (va, vx)):
        j = _alloc_index(a)
        if j is not None and any(j < m < limit for m in _canned_word(case, b, limit)):
            return True
    return False


_FACTS_CACHE = {}
_ZERO_BODIES = {struct.pack('<II', 0x52800000, 0xd65f03c0), struct.pack('<II', 0x2a1f03e0, 0xd65f03c0)}


def _object_facts(source):
    """(thunks, zero) of one native object: thunks {__fnv_<addr>: set of this-adjustments d of the _ZThn<d>_ thunks
    of the function at that address}, zero {names of functions whose whole body is `mov w0, #0; ret`}; None without
    the object or pyelftools."""
    d = _obj_dir()
    if not d or not source:
        return None
    path = os.path.join(d, source.replace('/', '_') + '.o')
    if path in _FACTS_CACHE:
        return _FACTS_CACHE[path]
    out = None
    if os.path.exists(path):
        try:
            from elftools.elf.elffile import ELFFile
        except ImportError:
            ELFFile = None
        if ELFFile is not None:
            import re
            thunks_of, zero = {}, set()
            with open(path, 'rb') as f:
                e = ELFFile(f)
                st = e.get_section_by_name('.symtab')
                syms = [s for s in st.iter_symbols() if isinstance(s['st_shndx'], int) and s.name] if st else []
                at = collections.defaultdict(set)
                for s in syms:
                    at[(s['st_shndx'], s['st_value'])].add(s.name)
                for s in syms:
                    if s['st_info']['type'] == 'STT_FUNC' and s['st_size'] == 8:
                        body = e.get_section(s['st_shndx']).data()[s['st_value']:s['st_value'] + 8]
                        if body in _ZERO_BODIES:
                            zero |= at[(s['st_shndx'], s['st_value'])]
            thunks = collections.defaultdict(set)
            for s in syms:
                m = re.match(r'_ZThn(\d+)_N(.+)$', s.name)
                if m:
                    thunks['_ZN' + m.group(2)].add(int(m.group(1)))
            for names in at.values():
                for n in names:
                    if n.startswith('__fnv_'):
                        ds = set().union(*(thunks.get(o, set()) for o in names))
                        if ds:
                            thunks_of[n] = ds
            out = (thunks_of, zero)
    _FACTS_CACHE[path] = out
    return out


def _own_and_callees(rec):
    """{__fnv_<addr>} | the native function's direct callees, or None without the object."""
    try:
        cs = native_callees(rec.get('source'), int(rec.get('address') or '0', 16))
    except ValueError:
        return None
    if cs is None:
        return None
    return {'__fnv_' + str(rec.get('address') or '')} | set(cs)


_GARBAGE_CACHE = {}


def _garbage_words(seed, side):
    """world.garbage_words (duplicated): the words the harness plants as garbage on one machine for a case."""
    key = (seed, side)
    if key not in _GARBAGE_CACHE:
        import random

        def g32(k):
            return random.Random((seed * 7919 + k * 104729) & 0xffffffff).getrandbits(32)
        if side == 'x86':
            g = {g32(i) for i in range(7)} | {g32(100 + k) for k in range(64)}
            fill = random.Random(seed ^ 0x5a5a5a5a).randbytes(0x1000)
        else:
            g = {g32(200 + i) for i in range(31)} | {g32(300 + i) for i in range(31)} | \
                {g32(400 + i) for i in range(32)} | {g32(600 + i + 32 * k) for k in range(64) for i in range(1, 18)}
            fill = random.Random((seed ^ 0xa5) ^ 0x5a5a5a5a).randbytes(0x1000)
        g.update(struct.unpack_from('<I', fill, o)[0] for o in range(0, len(fill) - 3))
        g.discard(0)
        if len(_GARBAGE_CACHE) > 16:
            _GARBAGE_CACHE.clear()
        _GARBAGE_CACHE[key] = g
    return _GARBAGE_CACHE[key]


def _rule_unclassified(d, rec, k):
    """Shapes of records no other rule took (stream S13b, bucket `unclassified`)."""
    case = rec.get('case') or {}
    if k == 'error':
        a = d.get('arm')
        fa = _fault_addr(a) if d.get('x86') is None else None
        if fa and fa[0] == 'x' and _in(fa[1], A64_PRIV) and case.get('bg') == 'rand':
            return 'native-wild-private'
        return None
    if k not in ('return', 'mem-write', 'call-args') or not isinstance(d.get('x86'), str) or \
            not isinstance(d.get('arm'), str):
        return None
    try:
        vx, va = _word(d['x86']), _word(d['arm'])
    except ValueError:
        return None
    if k == 'mem-write':
        bx, ba = bytes.fromhex(d['x86']), bytes.fromhex(d['arm'])
        diff = [i for i in range(min(len(bx), len(ba))) if bx[i] != ba[i]]
        if diff and len(bx) == len(ba) and all(bx[i] == 0x08 and ba[i] == 0 for i in diff):
            # the top byte of a stored word in the code-stub range 0x08xxxxxx: arm64emu rewrites it to the
            # "original address" (0x00xxxxxx) although the bytes are data
            return 'stub-normalized'
    if k == 'return' or k == 'mem-write':
        if vx is not None and va is not None and vx != va and _ctor_pair(case, vx, va, 64):
            return 'ctor-this'
        if vx is not None and va is not None and vx != va and case.get('seed') is not None:
            # a stored / returned word that is one of the garbage words the harness planted on that machine
            gx, ga = vx in _garbage_words(case['seed'], 'x86'), va in _garbage_words(case['seed'], 'a64')
            if gx or ga:
                return 'uninit-both' if gx and ga else ('uninit-x86' if gx else 'uninit-arm')
    if k in ('return', 'call-args') and not bytes.fromhex(d['arm']).strip(b'\0') and \
            bytes.fromhex(d['x86']).strip(b'\0'):
        # the native has 0 where the oracle has a value: the native function, or a direct callee in its unit, is a
        # placeholder body `mov w0, #0; ret`
        facts = _object_facts(rec.get('source'))
        if facts and facts[1] & (_own_and_callees(rec) or set()):
            return 'native-zero-stub'
    if k == 'return' or k == 'mem-write':
        return None
    if k != 'call-args':
        return None
    idx = d.get('index') or 0
    cls = d.get('cls')
    if cls in ('P', 'I32') and vx is not None and va is not None:
        if _ctor_pair(case, vx, va, idx):
            return 'ctor-this'
        if cls == 'P' and _in(va, STACK) and any(m < idx for m in _canned_word(case, vx, idx)):
            return 'canned-this'
        off = (va - vx) & 0xffffffff
        if _in(va, BUFS) and _in(vx, LIT_POOL) and 0 < off <= 0x400 and off % 4 == 0:
            # the same object at another offset across the buffer start (this - d): an override's secondary-base
            # entry when the native has a this-adjusting thunk by d for the function or a direct callee
            th = (_object_facts(rec.get('source')) or ({}, set()))[0]
            if any(off in th.get(n, ()) for n in _own_and_callees(rec) or ()):
                return 'thunk-binding'
            return 'layout-offset'
    if cls in ('I32', 'F32', 'F64') and idx > 0:
        n = 8 if cls == 'F64' else 4
        bx, ba = bytes.fromhex(d.get('x86') or ''), bytes.fromhex(d.get('arm') or '')
        prev = _ret_bytes(case, idx - 1)[:n]
        if len(ba) == n and ba == prev and bx != prev:
            return 'native-stale-result'
    return None


TRIAGE_REFINABLE = {c for c, v in POLICY.items() if v[0] == 'triage'}
REFINABLE = (None, 'unclassified', 'call-flow', 'signedness?')


def refine(rec, cause=None):
    """The record-level cause of a divergence the probes left at `cause` (unclassified / call-flow / signedness?),
    or `cause` itself when no rule applies. Never overrides a probe's definite cause; oracle-vcall (HINT_REFINABLE,
    the runner's vcall tag or the fault probe, not a decision) is split by _rule_vcall."""
    if cause in HINT_REFINABLE:
        return _rule_vcall(rec) or cause
    if cause not in REFINABLE:
        return _with_effects(rec, cause)
    d = (rec.get('_now') or rec.get('divergence') or {})
    k = d.get('kind')
    got = None
    if k == 'error':
        got = _rule_error(d)
        if got == 'native-null-read' and _calls_allocator(rec):
            got = 'new-null'
        elif got == 'oracle-fault':
            got = _rule_oracle_fault(d, rec) or got
    elif k == 'call-args':
        got = _rule_call_args(d)
    elif k == 'return':
        got = _rule_return(d, rec.get('conv'), rec.get('symbol'))
    elif k == 'mem-write':
        got = _rule_mem_write(d, rec)
    elif k in ('call-count', 'call-target'):
        got = _rule_calls(d, rec)
        if got in (None, 'native-inlined', 'native-early-exit', 'native-extra-calls'):
            got = _callflow(d, rec) or got
    if got is None and cause in (None, 'unclassified') and _with_effects(rec, 'unclassified') == 'unclassified':
        got = _rule_unclassified(d, rec, k)      # only what would stay unclassified (S13b bucket rules)
    if got is None:
        got = cause or 'unclassified'
    return _with_effects(rec, got)


def _with_effects(rec, cause):
    if cause not in TRIAGE_REFINABLE:
        return cause
    e = EFFECTS.get(os.path.basename(rec.get('_file') or rec.get('_p') or '')) if EFFECTS else None
    got = _rule_effects(e, rec, cause) if e else None
    if got:
        return got
    try:
        u = unbound_callees(rec.get('source'), int(rec.get('address') or '0', 16))
    except (ValueError, TypeError):
        u = None
    return 'after-native-undefined' if u else cause


# ------------------------------------------------------------------------------------------------------- summary

def _load_campaign(d, tag=''):
    import report                 # reclassify: same causes as the verdict tables
    results = {}
    rp = os.path.join(d, 'results.jsonl')
    if os.path.exists(rp):
        for line in open(rp):
            line = line.strip()
            if line:
                r = json.loads(line)
                results[r['address']] = r
    gone, now = set(), {}
    cp = os.path.join(d, 'recheck%s.jsonl' % ('_' + tag if tag else ''))
    if os.path.exists(cp):
        for line in open(cp):
            x = json.loads(line)
            if x['status'] == 'gone':
                gone.add(x['file'])
            elif x['status'] == 'still':
                now[x['file']] = x['divergence']
    divs = []
    for p in sorted(glob.glob(os.path.join(d, 'div', '*.json'))):
        if os.path.basename(p) in gone:
            continue
        rec = json.load(open(p))
        rec['_now'] = now.get(os.path.basename(p))
        rec['_file'] = os.path.basename(p)
        rec['_size'] = (results.get(rec.get('address')) or {}).get('size')
        try:
            rec['_cause'] = report.reclassify(rec)
        except Exception:  # noqa: BLE001 - a record the static reclassifier cannot read keeps its hint
            rec['_cause'] = refine(rec, (rec.get('hint') or {}).get('cause'))
        rec['_file'] = os.path.basename(p)
        divs.append(rec)
    return results, divs


def summarize(dirs, tag=''):
    rows = collections.defaultdict(lambda: dict(divergences=0, functions=set(), worst=0, kinds=collections.Counter(),
                                                examples=[]))
    by_fn = collections.defaultdict(list)
    results = {}
    for d in dirs:
        r, divs = _load_campaign(d, tag)
        results.update(r)
        for rec in divs:
            if results.get(rec['address'], {}).get('status', 'ok') != 'ok':
                continue
            by_fn[rec['address']].append(rec)
    for addr, recs in by_fn.items():
        worst = min(recs, key=lambda x: (CLASS_ORDER.index(policy(x['_cause'])[0]), not policy(x['_cause'])[2]))
        rows[worst['_cause']]['worst'] += 1
        for rec in recs:
            row = rows[rec['_cause']]
            row['divergences'] += 1
            row['functions'].add(addr)
            row['kinds'][(rec.get('_now') or rec['divergence']).get('kind')] += 1
            if len(row['examples']) < 5:
                row['examples'].append(dict(address=addr, unit=rec.get('source'), file=rec['_file']))
    causes = {}
    for c, row in sorted(rows.items(), key=lambda kv: (-kv[1]['divergences'], kv[0])):
        cls, owner, hard, why = policy(c)
        causes[c] = dict(verdict=cls, owner=owner, hard=hard, why=why, divergences=row['divergences'],
                         functions=len(row['functions']), worst_of=row['worst'], kinds=dict(row['kinds']),
                         examples=row['examples'])
    by_class = collections.Counter()
    fn_class = {}
    for addr, recs in by_fn.items():
        fn_class[addr] = min((policy(x['_cause'])[0] for x in recs), key=CLASS_ORDER.index)
        by_class[fn_class[addr]] += 1
    ok = sum(1 for r in results.values() if r.get('status') == 'ok')
    return {
        'format': 'fnvnx-causes/1',
        'campaigns': [os.path.abspath(d) for d in dirs],
        'functions_ok': ok,
        'functions_diverging': len(by_fn),
        'functions_by_class': {c: by_class.get(c, 0) for c in CLASS_ORDER},
        'native_objects': dict(OBJ_STATS, dir=_obj_dir()),
        'causes': causes,
        'policy': {c: dict(verdict=v[0], owner=v[1], hard=v[2], why=v[3]) for c, v in sorted(POLICY.items())},
    }


def merge(paths):
    """Sum the fnvnx-causes summaries of several shards (report .md blocks or JSON files)."""
    tot = dict(format='fnvnx-causes/1', shards=0, functions_ok=0, functions_diverging=0,
               functions_by_class={c: 0 for c in CLASS_ORDER}, causes={})
    for p in paths:
        txt = open(p).read()
        if p.endswith('.md'):
            i = txt.find('```json fnvnx-causes')
            if i < 0:
                continue
            i = txt.index('\n', i) + 1
            txt = txt[i:txt.index('```', i)]
        s = json.loads(txt)
        tot['shards'] += 1
        tot['functions_ok'] += s.get('functions_ok', 0)
        tot['functions_diverging'] += s.get('functions_diverging', 0)
        for c, n in s.get('functions_by_class', {}).items():
            tot['functions_by_class'][c] = tot['functions_by_class'].get(c, 0) + n
        for c, r in s.get('causes', {}).items():
            cls, owner, hard, why = policy(c)       # this checkout's policy decides the class
            t = tot['causes'].setdefault(c, dict(verdict=cls, owner=owner, hard=hard, why=why, divergences=0,
                                                 functions=0, worst_of=0, kinds={}))
            for k_ in ('divergences', 'functions', 'worst_of'):
                t[k_] += r.get(k_, 0)
            for k_, n in (r.get('kinds') or {}).items():
                t['kinds'][k_] = t['kinds'].get(k_, 0) + n
    tot['causes'] = dict(sorted(tot['causes'].items(), key=lambda kv: (-kv[1]['divergences'], kv[0])))
    return tot


HANDOFF_FILES = (('real-bug', 'handoffs'), ('triage', 'triage'), ('artifact', 'harness'))
HANDOFF_COLUMNS = ('owner', 'cause', 'address', 'unit', 'reproducer', 'first effect')


def handoff_rows(dirs, tag='', ref_prefix='fuzz/cache/', by_head=False):
    """{(file stem, owner): [row]} for every counted divergence whose cause has an owner and is not by design;
    rows are HANDOFF_COLUMNS tuples, sorted by cause, address and reproducer. With by_head, the reproducer names
    the per-head branch of the head that fuzzed the function (results `commit`: fuzz/<head12>/<dir name>), which
    the farms never rewrite, instead of the rolling cache branch."""
    prefix = dict(HANDOFF_FILES)
    out = collections.defaultdict(list)
    for d in dirs:
        results, divs = _load_campaign(d, tag)
        name = os.path.basename(os.path.normpath(d))
        for rec in divs:
            if results.get(rec['address'], {}).get('status', 'ok') != 'ok':
                continue
            cls, owner = policy(rec['_cause'])[:2]
            if cls not in prefix or owner in ('-', ''):
                continue
            e = EFFECTS.get(rec['_file']) or {}
            head = str((results.get(rec['address']) or {}).get('commit') or '')[:12]
            ref = ('fuzz/%s/%s:div/%s' % (head, name, rec['_file']) if by_head and head else
                   '%s%s:div/%s' % (ref_prefix, name, rec['_file']))
            out[(prefix[cls], owner)].append((owner, rec['_cause'], rec['address'], rec.get('source') or '', ref,
                                              e.get('first') or ''))
    for rows in out.values():
        rows.sort(key=lambda r: (r[1], r[2], r[4]))
    return dict(out)


def write_handoffs(dirs, out_dir, tag='', ref_prefix='fuzz/cache/', by_head=False):
    """Write the per-owner TSVs and index.tsv (file, class, owner, rows, functions, causes); returns the index."""
    os.makedirs(out_dir, exist_ok=True)
    groups = handoff_rows(dirs, tag, ref_prefix, by_head)
    cls_of = {stem: cls for cls, stem in HANDOFF_FILES}
    index = []
    for (stem, owner), rows in sorted(groups.items()):
        fn = '%s_%s.tsv' % (stem, owner)
        with open(os.path.join(out_dir, fn), 'w') as f:
            f.write('\t'.join(HANDOFF_COLUMNS) + '\n')
            for r in rows:
                f.write('\t'.join(r) + '\n')
        cs = collections.Counter(r[1] for r in rows)
        index.append((fn, cls_of[stem], owner, len(rows), len({r[2] for r in rows}),
                      ', '.join('%s %d' % kv for kv in sorted(cs.items(), key=lambda kv: (-kv[1], kv[0])))))
    with open(os.path.join(out_dir, 'index.tsv'), 'w') as f:
        f.write('file\tclass\towner\trows\tfunctions\tcauses\n')
        for row in index:
            f.write('\t'.join(str(x) for x in row) + '\n')
    return index


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('summary')
    s.add_argument('dirs', nargs='+')
    s.add_argument('--recheck-tag', default='')
    s.add_argument('--effects', action='append', default=[], help='difffuzz.py effects output (JSON lines)')
    s.add_argument('--out', default='')
    m = sub.add_parser('merge')
    m.add_argument('files', nargs='+')
    m.add_argument('--out', default='')
    e = sub.add_parser('explain')
    e.add_argument('files', nargs='+')
    h = sub.add_parser('handoffs')
    h.add_argument('dirs', nargs='+')
    h.add_argument('--out-dir', required=True)
    h.add_argument('--recheck-tag', default='')
    h.add_argument('--effects', action='append', default=[], help='difffuzz.py effects output (JSON lines)')
    h.add_argument('--ref-prefix', default='fuzz/cache/', help='reproducer = <prefix><dir name>:div/<file>')
    h.add_argument('--ref-by-head', action='store_true',
                   help='reproducer = fuzz/<head12 of the results row>/<dir name>:div/<file> (stable branch)')
    a = ap.parse_args(argv)
    if a.cmd in ('summary', 'handoffs') and a.effects:
        load_effects(a.effects)
    if a.cmd == 'handoffs':
        for row in write_handoffs(a.dirs, a.out_dir, a.recheck_tag, a.ref_prefix, a.ref_by_head):
            print('%-22s %-9s %-5s rows %5d fn %5d  %s' % row)
        return 0
    if a.cmd in ('summary', 'merge'):
        out = summarize(a.dirs, a.recheck_tag) if a.cmd == 'summary' else merge(a.files)
        txt = json.dumps(out, indent=1, sort_keys=True)
        if a.out:
            open(a.out, 'w').write(txt + '\n')
        print('functions ok %d, diverging %d: %s' % (out['functions_ok'], out['functions_diverging'],
                                                    ', '.join('%s %d' % kv for kv in out['functions_by_class'].items())))
        no = out.get('native_objects')
        if no:
            print('native objects: reader %s, scanned %d, missing %d (%s)' % (no['reader'], no['scanned'],
                                                                              no['missing'], no['dir']))
        for c, row in out['causes'].items():
            print('  %-20s %-9s %-5s div %5d fn %5d worst %5d' % (c, row['verdict'], row['owner'], row['divergences'],
                                                                 row['functions'], row['worst_of']))
    else:
        for p in a.files:
            rec = json.load(open(p))
            h = (rec.get('hint') or {}).get('cause')
            print('%s\thint=%s\trefined=%s\t%s' % (os.path.basename(p), h, refine(rec, h), policy(refine(rec, h))[0]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
