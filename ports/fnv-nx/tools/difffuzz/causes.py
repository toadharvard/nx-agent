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
import re
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
    'x87-range':      ('by-design', '-', False, 'an x87 intermediate beyond the float range (|v| >= 2**128: the '
                                                'x87 stack keeps a 15-bit exponent at every precision) that the '
                                                'AArch64 float arithmetic overflows to inf/NaN; the record keeps the '
                                                'instruction and exponent (classify.x87_probes)'),
    'division':       ('by-design', '-', False, 'x86 raises #DE (x/0, INT_MIN/-1) where sdiv/udiv give 0 / wrap: '
                                                'the exe would crash on that input'),
    'deleting-dtor-ret': ('triage', 'S13', True, 'MSVC\'s scalar/vector deleting destructor returns this, the '
                                                 'Itanium D0 is void (the difference is by design), but '
                                                 'runner.compare stops there and never compares the destructor '
                                                 'body: a real bug in it gets this label too. Triage until the '
                                                 'harness skips the return of ??_G/??_E (S13b audit)'),
    'call-order':     ('by-design', '-', False, 'two adjacent calls swapped: C++ leaves operand evaluation order open'),
    'vptr-store':     ('by-design', '-', False, 'a dead vptr store GCC drops (D7: objects hold Itanium vptrs); in a '
                                                'destructor MSVC resets the vptr to the base vftable, GCC does not'),
    'cpuid-env':      ('by-design', '-', False, 'the oracle stores / returns what `cpuid` answered (it is shown: the '
                                                'bytes change when the oracle\'s cpuid answers change); AArch64 has '
                                                'no CPUID and the port answers with the fixed values of '
                                                'shim/include/fnvnx_asm.h (docs/ASM.md), a CPU-defined result on '
                                                'the exe too'),
    'array-new-check': ('by-design', '-', False, 'GCC checks new[]\'s count for overflow, MSVC 2008 does not'),
    'ilp32-pointer':  ('by-design', '-', False, 'an address computed past 4 GiB (wraparound on x86): needs a pointer '
                                                'the exe never holds'),
    'float->int':     ('by-design', 'S12', False, 'out-of-range float->int (UB in C++); the FTOI2 plugin gives '
                                                  'MSVC semantics where the build loads it'),
    'vptr-bytes':     ('artifact', 'S13', False, 'the code reads a byte or part of a planted vptr word as data: the exe '
                                                 'vftable address on the oracle, the Itanium address point natively '
                                                 '(D7); equal once the oracle holds the native word too'),
    'vptr-pool':      ('artifact', 'S13', False, 'the oracle stores the synthetic pool address of its object\'s unnamed '
                                                 'vftable section where the native stores the image vftable that begins '
                                                 'with the same function address (the pool copy of the table)'),
    'x87-trig-inf':   ('artifact', 'S13', False, 'fsin / fcos / fsincos / fptan of an infinity: the oracle emulator '
                                                 'leaves the infinity in st0 where the x87 gives the indefinite NaN '
                                                 '(Intel SDM, #IA masked); equal once the probe applies it'),
    'canned-alias':   ('artifact', 'S13', False, 'a canned callee result points into the argument buffers, so the '
                                                 'function writes the object through two names; the machines order '
                                                 'those stores differently (no allocation returns the caller\'s own '
                                                 'object); equal with the results moved to the heap'),
    'ptrdiff-unaligned': ('by-design', '-', False, 'a pointer difference that is not a multiple of the element size: '
                                                   'GCC divides exactly (or folds (end - begin) / size * size), MSVC '
                                                   'keeps the idiv (UB in C++); equal with 4-aligned arguments or, '
                                                   'for `sub; cdq; idiv K` in the exe, with the subtrahend word '
                                                   'rounded to a multiple of K (r5design.ptrdiff_probe)'),
    'shift-count':    ('by-design', '-', False, 'a shift count >= the operand width (UB in C++)'),
    'dead-result':    ('by-design', '-', False, 'the target\'s integer / pointer result is read by no caller in the exe '
                                                '(every call is followed by a write of eax, or by a bare return of '
                                                'a wrapper whose callers do the same; the address is never taken): '
                                                'eax at the return is scratch (r5design.result_dead)'),
    'dead-init-call': ('by-design', '-', False, 'MSVC /Od calls a constructor that only stores through this into a '
                                                'frame object whose every byte the caller then overwrites before any '
                                                'read; GCC drops the dead initialisation (r5design.dead_init_site)'),
    'local-index-range': ('by-design', '-', False, 'a variable index into a local array leaves the frame (it writes '
                                                   'the caller\'s arguments, or wraps): the result moves with each '
                                                   'compiler\'s frame layout; equal for every index inside the frame '
                                                   '(r5design.frame_index_probe)'),
    'canned-null-deref': ('artifact', 'S13', False, 'the oracle\'s wild indirect call got a canned 0 and the next '
                                                    'instruction loads through eax; the native\'s declaration returns a '
                                                    'reference, which the harness answers with a valid object; equal '
                                                    'with that object on both machines (r5design.deref_after_call)'),
    'uninit-x86':     ('by-design', '-', False, 'the oracle\'s own result depends on garbage: undefined behaviour '
                                                'the exe gets away with; the port need not reproduce garbage'),
    'partial-return': ('by-design', '-', False, 'the oracle returns a byte / short in al / ax and the rest of eax is '
                                                'what it held; the declared int result differs only there'),
    'split-delete':   ('by-design', '-', False, 'MSVC calls the exe\'s scalar deleting destructor F(p, 1), which frees p '
                                                'itself (exeempty.deleting_wrapper: it tests bit 0 of its argument '
                                                'and then calls the deallocator); GCC calls the destructor, F(p, 0), '
                                                'and a separate operator delete of the same p. The pair is the one '
                                                'call; the arguments and the stores are compared'),
    'call-order-unproven': ('triage', 'S13b', True, 'two adjacent calls are swapped and the exe does not show them to be '
                                                    'the arguments of one call (exeempty.arg_blocks): a reordering of '
                                                    'statements is a divergence, not an evaluation order'),
    'call-order-args': ('triage', 'S13b', True, 'the calls are in another order on the two machines (eval-order) and the '
                                                'comparison after pairing them by callee still differs; the canned '
                                                'results are numbered by call index, so a reordered callee got '
                                                'another value and the arguments it feeds differ for that reason: '
                                                'not decided'),
    'null-delete':    ('by-design', '-', False, 'a call of operator delete (0x00401030, the sized 0x00aa1460) with a '
                                                'null pointer, which returns at once in the exe (0x00aa4060 `cmp '
                                                '[ebp+8], 0; jne`, 0x00aa1460 `test eax, eax; je`); GCC drops a '
                                                'delete of a pointer it knows is null'),
    'oracle-empty-call': ('by-design', '-', False, 'the oracle calls an exe function whose body is proven to do '
                                                   'nothing (push ebp; mov ebp, esp; pop ebp; ret, exeempty.py: no '
                                                   'store, no call, no register but ebp/esp written) and GCC drops '
                                                   'the call; the traces are equal without those calls'),
    'nrvo-copy':      ('by-design', '-', False, 'MSVC without NRVO copies the named result into the hidden result '
                                              'buffer and destroys the local, GCC constructs it there (copy '
                                              'elision, allowed by C++), F1b'),
    'dtor-split-delete': ('by-design', '-', False, 'the oracle calls a scalar deleting destructor wrapper (exe: `and '
                                                   'eax, 1; je`, then a call of operator delete / Deallocate) and '
                                                   'goes on; the native calls the same exe function through its D1 '
                                                   'binding with flags 0 and frees with its own operator delete '
                                                   'call. Same destructor body, same heap (libcalls: delete 0x401030 '
                                                   'and sized delete 0x00aa1460 both reach MemoryManager::Deallocate)'),
    'nrvo-elision':   ('by-design', '-', False, 'one machine passes the hidden result buffer of the function as the object a '
                                                'callee works on, the other a local of its own frame (named return '
                                                'value optimisation on one side only; allowed by C++). The result '
                                                'buffer is compared at the end, so a wrong copy still shows'),
    'oracle-frame-smash': ('by-design', '-', False, 'the oracle faults after a store into its own return-address '
                                                    'slot: a local array overrun by an out-of-range fuzzed count (UB '
                                                    'in C++; the exe corrupts its frame the same way), F1b'),
    # --- harness / oracle-model artefacts ----------------------------------------------------------------------------
    'local-static':  ('artifact', 'S13', False, 'MSVC keeps a function-local static and its guard in the image '
                                                 '(fuzzed bytes), GCC in .bss (zero): not compared state'),
    'thunk-forward':  ('artifact', 'S13', False, 'the exe function is a single `jmp rel32` (incremental-link thunk) and the '
                                                 'decomp declares it without parameters: the arguments it forwards '
                                                 'to its callee are not defined by the case (zero stack words on '
                                                 'the oracle, unset registers natively)'),
    'uninit-both':    ('artifact', 'S13', False, 'both machines read garbage: the fuzzed case is invalid for both'),
    'stub-outparam':  ('artifact', 'S13', False, 'the native reads back a stack buffer it passed to a stubbed callee '
                                                 'that never filled it (GCC temporary + copy where MSVC passed its '
                                                 'destination)'),
    'oracle-vcall':   ('artifact', 'S13', False, 'the oracle faults on an indirect call the harness does not model '
                                                 '(S13 vftable modelling)'),
    'native-vcall-unplanted': ('artifact', 'S13', False, 'the native calls a null word of a table in case memory that '
                                                         'no vptr planting made a vftable, where the oracle calls a '
                                                         'wild word of the same garbage (exe slot k against the '
                                                         'native k + 1): each machine reads its own slot layout of '
                                                         'memory with none (classify.native_unplanted_vcall)'),
    'x86-custom-cc':  ('artifact', 'S13', False, 'MSVC whole-program custom register convention not in units.json'),
    'msvc-inlined':   ('artifact', 'S13', False, 'MSVC inlined a callee the native calls out of line'),
    'runtime-equiv':  ('artifact', 'S13', False, 'the same runtime function under two addresses (before difffuzz-8)'),
    'stack-arg':      ('artifact', 'S13', False, 'each machine\'s own stack address through an int (fixed since)'),
    'literal-arg':    ('triage', 'S13b', True, 'a literal at its image address vs the native unit\'s copy; the runner '
                                              'drops pointers whose strings are equal, so a remaining record has '
                                              'different strings (a wrong literal is a real bug) (S13b audit)'),
    'literal-ret':    ('artifact', 'S13', False, 'a pointer result into the native unit\'s .rodata whose oracle '
                                                 'pointee the harness could not read (literal not pinned)'),
    'oracle-trap':    ('artifact', 'S13', False, 'the oracle executed int3/0xCC .text filler: code the harness did '
                                                 'not load (unit membership / preload gap)'),
    'new-null':       ('artifact', 'S13', False, 'the native constructs into the null an intercepted allocator '
                                                 'returned (GCC drops the null test after a throwing operator new, '
                                                 'MSVC 2008 keeps it); the game\'s allocators never return null: '
                                                 'canned allocator results should be heap addresses'),
    'oracle-native-address': ('triage', 'S13b', True, 'only the oracle faults on a native private address: the '
                                                      'native may never have made the access (a field at another '
                                                      'offset), the same ambiguity as oracle-fault (S13b audit)'),
    'literal-store':  ('triage', 'S13b', True, 'a stored literal address, image vs native copy: stores never compare '
                                               'the pointees, so a wrong literal looks the same; triage until the '
                                               'runner compares stored pointees (S13b audit)'),
    'oracle-callee-store': ('artifact', 'S13', False, 'the first differing store is made by a callee the oracle '
                                                      'runs (preloaded or followed) while the native calls it out of '
                                                      'line and the harness intercepts it: no side effects natively'),
    'no-native-entry': ('artifact', 'S13', False, 'the native image has no entry for the function (link subset)'),
    'vcall-garbage':  ('triage', 'S13b', True, 'both machines call a word of memory the case did not plant: an '
                                               'unplanted object (native slot = x86 slot + 1, Itanium\'s extra '
                                               'destructor) or a wrong native slot / vptr word (real bug); only a '
                                               'slot rebuilt from the case background separates them (S13b audit)'),
    'vcall-stub-alias': ('triage', 'S13', True, 'the same garbage call target on both machines (a stub-range word '
                                                'the native trace names as its original address): the harness\'s '
                                                'doing, but runner.compare stops at this call and compares nothing '
                                                'after it; triage until the runner treats both as one target (S13b audit)'),
    'oracle-private-address': ('triage', 'S13b', True, 'only the oracle faults in its own private range '
                                                       '(0x0e000000): the native may never have made the access; '
                                                       'for fault:x, a native that ran background bytes would have '
                                                       'trapped, so it never made that call (S13b audit)'),
    'ctor-this':      ('triage', 'S13b', True, 'one side uses the block a game allocator returned, the other the '
                                               'canned result of a later intercepted call. A harness artefact '
                                               'when that call is the constructor on the block (it returns this), '
                                               'a real bug when the native drops a factory/Init result; the record '
                                               'names neither the later call nor its this (V1 A2): needs a replay '
                                               'or a canned-this harness fix'),
    'canned-this':    ('triage', 'S13b', True, 'the oracle passes the canned eax of an earlier intercepted call as a '
                                               'pointer where the native passes its own frame temporary: a callee '
                                               'that returns its result pointer (harness gives a random eax), or a '
                                               'native declaration returning by value (hidden-pointer ABI mismatch, '
                                               'S2c); the record does not show the earlier call\'s arguments (V1 '
                                               'A3)'),
    'native-wild-private': ('triage', 'S13b', True, 'random background: the native executes its AArch64 private '
                                                    'range (fault:x), the oracle makes no error. A random code '
                                                    'pointer x86 intercepts as a wild call, or a native call '
                                                    'through a wrong vtable slot; the record cannot tell them '
                                                    'apart (V1 A4 = the rule\'s own shape)'),
    'stub-normalized': ('artifact', 'S13', False, 'every differing byte lies in a 4-byte window where the oracle '
                                                  'stored a word in the code-stub range [0x08000000, 0x09000000) '
                                                  'and the native has exactly that word - 0x08000000: what '
                                                  'arm64emu._codeptr does to any such stored word (data '
                                                  'included); a flag bit 0x08 elsewhere is not this (V1 A1)'),
    'thunk-binding':  ('real-bug', 'S2c', True, 'the original address is an override\'s MSVC entry (this = the '
                                                'secondary-base subobject); the link binds __fnv_<addr> to the GCC '
                                                'primary entry (this = the full object), not its _ZThn<d>_ thunk'),
    'ilp32-wrap':     ('real-bug', 'S1', True, 'the native faults at 2^32 + w where w is memory the oracle maps: the '
                                             'exe adds a base and a 32-bit index modulo 2^32 (a negative int as '
                                             'unsigned / as a pointer), GCC ILP32 adds it in 64 bits (uxtw); the '
                                             'decomp type or -fwrapv-pointer must make it wrap'),
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
    'native-vslot-differs': ('real-bug', 'S2c', True, 'the native vtable of a planted class has, at the slot where the '
                                                      'exe\'s vftable holds function Y, another exe function X, and '
                                                      'the native called it (D26: the decomp class\'s virtual at '
                                                      'that slot is not the exe\'s; the oracle ran Y)'),
    'vslot-shift':    ('real-bug', 'S1-rules', True, 'a virtual call through the planted vftable reaches the exe\'s '
                                                     'slot k-1 where the oracle calls slot k: the caller\'s class '
                                                     'declaration numbers its virtuals from a slot-0 placeholder, '
                                                     'not D22\'s minus-one order (D26 b: `virtual ~X()` for the '
                                                     'placeholder; tests/s5/callonly_vt.py)'),
    'vslot-differs':  ('real-bug', 'S1-rules', True, 'a virtual call through the planted vftable reaches another '
                                                      'slot than the oracle\'s (not one lower): the caller\'s class '
                                                      'declaration orders its virtuals otherwise than the exe'),
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
    'vslot-trap':     ('real-bug', 'S2c', True, 'the native calls a vtable slot the link filled with '
                                              'fnvnx_vslot_trap / fnvnx_special_trap (brk #0xf5 / #0xf6: a virtual '
                                              'defined nowhere) where the exe vftable has a function'),
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
    'param-count':    ('real-bug', 'S1b', True, 'the exe function reads stack parameters its decomp declaration does '
                                                'not have (every caller passes too few; the native reads what is '
                                                'in its registers)'),
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
    'native-zero-init': ('by-design', '-', False, 'the native only writes zero bytes the oracle leaves, into the object '
                                                  'being constructed (this of a constructor, a fresh allocation): '
                                                  'GCC value-initialises `T()` / `new T()` / `: Base()` of a class '
                                                  'with an implicit constructor (zero, then the constructor), MSVC '
                                                  '2008 runs the constructor only'),
    'dtor-dead-store': ('by-design', '-', False, 'the oracle only zeroes bytes of the object a destructor (or a scalar '
                                                 'deleting destructor wrapper) destroys (or stores the empty-array '
                                                 'marker 0x80000000 there): MSVC /Od keeps `m_p = 0` '
                                                 'after a release, GCC drops stores to an object whose lifetime ends'),
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
    'deleting-dtor-flags': ('triage', 'S13', True, '??_G/??_E fuzzed with flags bit 0 clear: MSVC does not free, '
                                                   'the Itanium D0 always frees (by design), and the call-count '
                                                   'difference hides the destructor body. Triage until the harness '
                                                   'runs the native D1 for flags 0 (S13b audit)'),
    'byvalue-arg-dtor': ('triage', 'S13', True, 'the function takes a class by value: MSVC destroys it in the '
                                                'callee, Itanium in the caller (by design only for a non-trivial '
                                                'native destructor, unchecked), and the call-count difference at '
                                                'the free hides the return value and writes. Triage until the '
                                                'harness drops the oracle\'s trailing argument frees (S13b audit)'),
    'callee-float-width': ('artifact', '-', False, 'the width (float or double) in which the harness hands an untyped '
                                                     'callee\'s canned float result to the oracle\'s ST0 is a guess '
                                                     'from the consuming x87 store; the other guess makes the '
                                                     'machines agree (classify.x87_probes)'),
    'libc-variant':   ('artifact', 'S13', False, 'the same C library function under another entry: an MSVC x87 '
                                                 'intrinsic (_CIacos ...) or the double function vs libc or its '
                                                 'float variant, sprintf vs GCC\'s fortified __sprintf_chk, atof vs '
                                                 'strtod: libcalls.X86/FAMILY do not pair them'),
    'oracle-callee-unloaded': ('artifact', 'S13', False, 'the oracle intercepts X (canned result) while the native '
                                                         'object defines X and runs its body: X is not in the '
                                                         'oracle\'s code set (unit membership / follow gap)'),
    'throw-temp-dtor': ('by-design', '-', False, 'the native calls the destructor of a temporary right before the throw '
                                                'the oracle ends the run at: GCC destroys the temporaries of a throw '
                                                'expression before __cxa_throw, MSVC emits the same call after '
                                                '_CxxThrowException (never run, the unwind map does it); the exe has '
                                                'that call within a few instructions after the throw '
                                                '(exeempty.calls_after_noreturn)'),
    'dyninit-no-atexit': ('real-bug', 'S10', False, 'the dynamic initializer registers the object\'s destructor '
                                                    'with atexit in the exe; the native initializer does not '
                                                    '(no fnvnx_xc_atexit): the destructor never runs at exit'),
    'native-empty-body': ('real-bug', 'S1', True, 'the native function, or a decomp-local stand-in it calls for an '
                                                  'original function defined elsewhere, is an empty body (ret / '
                                                  'return constant) where the exe runs code that makes calls'),
    'native-calls-data': ('real-bug', 'S2c', True, 'the native calls an image data address where the exe calls a '
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

# D21 (coverage first): every real-bug cause belongs to one fix class, a single general mechanism its owner closes
# for all instances at once. class -> (owner, mechanism, causes).
FIX_CLASSES = {
    'callee-bodies': ('S1', 'every call from a prepared unit reaches the original function\'s real body: emit all '
                            'inline/template/declared-only bodies, no decomp-local stand-in (empty or return-0) '
                            'shadows an original function',
                      ('native-undefined', 'after-native-undefined', 'native-empty-body', 'native-empty-callee',
                       'native-zero-stub')),
    'object-layout': ('S1', 'every field is read at the exe\'s offset, width and signedness (struct layouts and '
                            'member types from the exe, base subobject placement)',
                      ('native-null-read', 'layout-offset', 'field-width', 'signedness')),
    'control-flow':  ('S1', 'no path the exe takes ends in a trap, a fall-off or a stack-protector abort (no UB '
                            'GCC exploits: missing returns, unreachable, out-of-bounds locals)',
                      ('native-trap', 'falloff?', 'stack-protector')),
    'arithmetic':    ('S1', 'integer and float arithmetic as MSVC x86 computes it: 32-bit address wraparound '
                            '(ilp32-wrap), float results beyond x87 tolerance',
                      ('float', 'ilp32-wrap')),
    'ownership':     ('S1', 'the native frees exactly what the exe frees',
                      ('native-missing-free',)),
    'static-init':   ('S10', 'every dynamic initializer registers its destructor as the exe does (atexit)',
                      ('dyninit-no-atexit',)),
    'call-abi':      ('S1b', 'every call passes and preserves registers by the callee\'s MSVC convention (argument '
                             'slots written, results not reused, callee-saved kept, sret as MSVC)',
                      ('uninit-arm', 'native-garbage-arg', 'native-stale-result', 'callee-saved', 'sret-mismatch',
                       'param-count')),
    'data-binding':  ('S2c', 'every data symbol and import binds to the exe object (no stub as data, no unit copy, '
                             'no native address where the exe has an image address, bridges deliver the bytes)',
                      ('native-stub-data', 'native-address', 'split-global', 'import-slot-called', 'call-args')),
    'vtables':       ('S2c', 'every image vftable pairs with a full native vtable of the static class (no short '
                             'vtables, overrides bound to their this-adjusting thunks)',
                      ('vptr-unpaired', 'native-calls-data', 'thunk-binding', 'vslot-trap', 'native-vslot-differs')),
    'vtable-slots':  ('S1-rules', 'every virtual call reaches the exe\'s slot: each declaration a caller dispatches '
                                  'through numbers its virtuals in D26\'s one minus-one order (a `virtual ~X()` '
                                  'where the exe has its deleting destructor, then the exe\'s slots in order)',
                      ('vslot-shift', 'vslot-differs')),
}
FIX_CLASS_OF = {c: k for k, v in FIX_CLASSES.items() for c in v[2]}


def _table(name):
    """a decomp-derived table of fnv-nx-build (tools/tables.py: FNVNX_TABLES, sha256 against tools/tables.pin.json)"""
    import importlib.util
    s = importlib.util.spec_from_file_location('fnvnx_tables', os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tables.py'))
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m.path(name)


def fix_classes(causes_rows):
    """{class: owner, mechanism, causes, divergences, functions (sum over causes; a function in two causes counts
    twice), worst_of} from a summary's per-cause rows, largest first."""
    out = {}
    for k, (owner, mech, cs) in FIX_CLASSES.items():
        rows = [causes_rows.get(c) or {} for c in cs]
        out[k] = dict(owner=owner, mechanism=mech,
                      causes={c: (causes_rows.get(c) or {}).get('divergences', 0) for c in cs},
                      divergences=sum(r.get('divergences', 0) for r in rows),
                      functions=sum(r.get('functions', 0) for r in rows),
                      worst_of=sum(r.get('worst_of', 0) for r in rows))
    return dict(sorted(out.items(), key=lambda kv: (-kv[1]['worst_of'], kv[0])))


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
    tiny = 1.1754943508222875e-38 if len(bx) == 4 else 2.2250738585072014e-308
    if abs(fx) < tiny or abs(fa) < tiny or math.copysign(1, fx) != math.copysign(1, fa):
        return False, False         # zero, denormal or a sign flip is not x87 precision (S13b audit)
    ulp = abs(struct.unpack(fmt[1], bx)[0] - struct.unpack(fmt[1], ba)[0])
    rel = abs(fx - fa) / max(abs(fx), abs(fa), 1e-300)
    return ulp <= 4 or rel <= fmt[2], False


GEN_NAN = {4: (0xffc00000, 0x7fc00000), 8: (0xfff8000000000000, 0x7ff8000000000000)}   # x87 indefinite, A64 default


def _nan_generated(bx, ba):
    """Both NaN and one side is that machine's generated NaN (x87 real indefinite / AArch64 default NaN): the
    payloads differ because a machine made the NaN, not because the native computed something else."""
    tol, nan = _float_close(bx, ba)
    if not nan:
        return False
    n = len(bx)
    gx, ga = GEN_NAN[n]
    return int.from_bytes(bx, 'little') == gx or int.from_bytes(ba, 'little') == ga


def _window_words(d):
    """[(x86 word bytes, native word bytes)] of every differing aligned word of a mem-write record when all its
    differences lie in the record's 8-byte window and both machines wrote them; None otherwise (differences the
    record does not show, or a store only one machine made)."""
    try:
        first = int(d.get('addr') or '', 16)
        addrs = [int(a, 16) for a in d.get('addrs') or ()]
    except ValueError:
        return None
    bx, ba = bytes.fromhex(d.get('x86') or ''), bytes.fromhex(d.get('arm') or '')
    w = first & ~3
    if (not addrs or len(addrs) != d.get('ndiff') or d.get('only_x86') or d.get('only_arm') or len(bx) != 8
            or len(ba) != 8 or not all(w <= a < w + 8 for a in addrs)):
        return None
    return [(bx[i:i + 4], ba[i:i + 4]) for i in (0, 4) if bx[i:i + 4] != ba[i:i + 4]]


def _fault_addr(err):
    """('r'|'w'|'x', address) of a 'fault:<k>:<hex>' error, None otherwise (ilp32-high and uc faults too)."""
    p = str(err or '').split(':')
    if len(p) == 3 and p[0] == 'fault' and p[1] in ('r', 'w', 'x'):
        try:
            return p[1], int(p[2], 16)
        except ValueError:
            return None
    return None


def _rule_error(d, rec=None):
    x, a = d.get('x86'), d.get('arm')
    if x is None and isinstance(a, str):
        if a.startswith('exc:'):
            return 'native-trap'
        if a == 'stack-protector':
            return 'stack-protector'
        if a == 'no-native-entry':
            return 'no-native-entry' if _defined_elsewhere(rec) else 'native-undefined'
        fa = _fault_addr(a)
        if fa:
            if _in(fa[1], A64_PRIV) and fa[0] in ('r', 'w'):
                return 'native-stub-data'
            if fa[1] < NULL_END:
                return 'native-null-read'
    if a is None and isinstance(x, str):
        if d.get('x86_frame_smash'):
            return 'oracle-frame-smash'
        if x == 'exc:3':
            return 'oracle-trap'
        fx = _fault_addr(x)
        if fx and fx[0] in ('r', 'w') and _in(fx[1], A64_PRIV):
            return 'oracle-native-address'
        if fx and fx[0] in ('r', 'w'):
            return 'oracle-fault'
    return None


# The link's trap stubs (tools/link/fnvlink.py): fnvnx_vslot_trap `brk #0xf5`, fnvnx_special_trap `brk #0xf6`. Every unit
# image holds one copy of each, so all functions of a unit that reach it fault at the same pc (S1-fuzzA-001).
LINK_TRAPS = {0xd4201ea0: 'vslot-trap', 0xd4201ec0: 'vslot-trap'}
_IMAGE_WORDS = {}


def _image_word(source, pc):
    """The 32-bit word at `pc` of the unit image difffuzz linked for `source` (FNVNX_CAUSES_A64 or config.A64WORK,
    `<object>.elf`), None when the image is not there. An image linked from other objects than the record's may
    have moved; callers only act on words that cannot be anything else."""
    d = os.environ.get('FNVNX_CAUSES_A64')
    if not d:
        try:
            import config
            d = str(config.A64WORK)
        except Exception:  # noqa: BLE001
            return None
    path = os.path.join(d, (source or '').replace('/', '_') + '.o.elf')
    if path not in _IMAGE_WORDS:
        segs = []
        try:
            from elftools.elf.elffile import ELFFile
            with open(path, 'rb') as f:
                for seg in ELFFile(f).iter_segments():
                    if seg['p_type'] == 'PT_LOAD' and seg['p_filesz']:
                        segs.append((seg['p_vaddr'], seg.data()))
        except Exception:  # noqa: BLE001 - no image, or not an ELF: no answer
            segs = None
        _IMAGE_WORDS[path] = segs
    for base, data in _IMAGE_WORDS[path] or ():
        if base <= pc and pc + 4 <= base + len(data):
            return struct.unpack_from('<I', data, pc - base)[0]
    return None


def _rule_trap(d, rec):
    """native `exc:N@pc` whose instruction is one of the link's trap stubs -> vslot-trap (S2c), else None."""
    a = d.get('arm') or ''
    if '@' not in a:
        return None
    try:
        pc = int(a.split('@', 1)[1], 16)
    except ValueError:
        return None
    return LINK_TRAPS.get(_image_word((rec or {}).get('source'), pc))


def _rule_call_args(d, rec=None):
    if d.get('arm_garbage'):
        return 'native-garbage-arg'
    if d.get('arg') == 'sret':
        return 'sret-mismatch'
    bx, ba = bytes.fromhex(d.get('x86') or ''), bytes.fromhex(d.get('arm') or '')
    if d.get('cls') in ('F32', 'F64'):
        tol, nan = _float_close(bx, ba)
        if nan and _nan_generated(bx, ba):
            return 'nan-bits'
        if tol:
            return 'x87-precision'
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
        if rec is not None and _thunk_delta(rec, vx, va):
            return 'thunk-binding'          # MSVC's this of a secondary-base override, the native's full object
        for reg in (BUFS, HEAP, WINDOW):
            if _in(vx, reg) and _in(va, reg) and 0 < abs(vx - va) <= 0x100 and (vx - va) % 4 == 0:
                return 'layout-offset'      # the same object, another member offset (base or field placement)
        if _in(vx, BUFS) and _in(va, BUFS):
            return 'arg-other-buffer'
    return None


def _rule_return(d, conv, sym=None):
    if str(sym or '').startswith(('??_G', '??_E')):
        return 'deleting-dtor-ret'
    rx, ra = _word(d.get('x86')), _word(d.get('arm'))
    if rx is not None and ra is not None and _in(ra, A64_STUBS) and not _in(rx, A64_STUBS) and not _in(rx, TEXT):
        return 'native-stub-data'           # the native returns a stub address (a data symbol the link left unbound)
    if ('pointee_x86' in d and (not d.get('pointee_x86') or not d.get('pointee_arm')) and rx is not None
            and ra is not None and _in(rx, LIT_POOL) and _in(ra, A64_IMAGE)):
        return 'literal-ret'
    fl = d.get('float') or {}
    if fl and not fl.get('tolerant'):
        try:
            if (fl.get('x87') is not None and float(fl['x87']) == float(fl.get('arm'))
                    and float(fl['x87']) != float(fl.get('x86'))):
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
    ww = _window_words(d)
    if fw.get('tolerant') and ww and all(_float_close(x, a) == (True, False) for x, a in ww):
        return 'x87-precision'
    n, ox, oa = d.get('ndiff', 0), d.get('only_x86', 0), d.get('only_arm', 0)
    bx, ba = bytes.fromhex(d.get('x86') or ''), bytes.fromhex(d.get('arm') or '')
    if rec is not None and n:
        th = (_object_facts(rec.get('source')) or ({}, set()))[0]
        if th.get('__fnv_' + str(rec.get('address') or '')):
            return 'thunk-binding'      # the function's own `_ZThn<d>_` thunk: the oracle enters with the secondary base
        if d.get('arm_zero_only') and oa == n and _zero_init_region(d, rec):
            return 'native-zero-init'
        if (d.get('x86_zero_only') or d.get('x86_const_only')) and ox == n and _dtor_this_region(d, rec):
            return 'dtor-dead-store'
    if len(bx) >= 4 and len(ba) >= 4:
        if ww and all(_nan_generated(x, a) for x, a in ww):
            return 'nan-bits'
        vx, va = _word(d['x86']), _word(d['arm'])
        if _in(vx, LIT_POOL) and _in(va, A64_IMAGE):
            return 'literal-store'
        vft = image_vftables()
        if vx in vft and va in vft and vx != va:
            return 'vptr-differs'       # in a destructor too: a class-chain check needs the exe's RTTI (audit)
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


def _zero_init_region(d, rec):
    """Every listed differing byte lies in the object the function constructs: a fresh allocation block of the
    harness (ALLOC_BASE..) or, for an MSVC constructor (??0), the `this` buffer."""
    addrs = [int(a, 16) for a in d.get('addrs') or ()]
    if not addrs:
        return False
    if all(ALLOC_BASE <= a < ALLOC_BASE + 0x400 * 0x1000 for a in addrs):
        return True
    if str(rec.get('symbol') or '').startswith('??0'):
        this = _this_buffer(rec)
        return this is not None and all(this <= a < this + 0x1000 for a in addrs)
    return False


def _this_buffer(rec):
    """The address of the `this` buffer of a record: the case's `this` bytes when it lists them (a minimised case
    drops arguments equal to the default), else the first argument buffer (world.BUF, Target.placement)."""
    try:
        return int.from_bytes(bytes.fromhex(rec['case']['args']['this']), 'little')
    except (KeyError, ValueError, TypeError):
        return BUFS[0] if isinstance(rec.get('case'), dict) else None


_DTOR_SYM = re.compile(r'^\?\?(1|_G|_E)|^\?Destructor@|[QU]AEPAX(I|H|_N)@Z$|D[012]Ev$')


def _dtor_this_region(d, rec):
    """A destructor (??1, ??_G, ??_E, an Itanium D0/D1/D2) or an MSVC scalar-deleting-destructor wrapper shape (a
    member returning void* with one int / bool flag) whose differing bytes all lie in its `this` buffer."""
    if not _DTOR_SYM.search(str(rec.get('symbol') or '')):
        return False
    addrs = [int(a, 16) for a in d.get('addrs') or ()]
    this = _this_buffer(rec)
    return bool(addrs) and this is not None and all(this <= a < this + 0x1000 for a in addrs)


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
        p = _table('imports.tsv')
        if os.path.exists(p):
            for line in open(p, encoding='utf-8', errors='replace'):
                f = line.rstrip('\n').split('\t')
                if len(f) >= 3 and f[0].startswith('0x'):
                    _IAT[int(f[0], 16)] = f[2]
    return _IAT


def _rule_empty_call(d):
    """oracle-empty-call: the call traces are equal once the calls to proven effect-free exe functions are removed
    from both (at least one removed from the oracle's)."""
    tx, ta = d.get('x86_trace'), d.get('arm_trace')
    if d.get('kind') not in ('call-count', 'call-target') or not isinstance(tx, list) or not isinstance(ta, list):
        return None
    import exeempty
    def strip(t):
        out = []
        for c in t:
            try:
                if exeempty.effect_free(int(str(c), 16)):
                    continue
            except ValueError:
                pass
            out.append(c)
        return out
    sx, sa = strip(tx), strip(ta)
    return 'oracle-empty-call' if sx == sa and len(sx) < len(tx) else None


def _is_deleting_wrapper(addr):
    """The exe function at `addr` is a scalar deleting destructor wrapper: a flags test (`and r, 1`, then a branch) and a
    call of a deallocator (operator delete 0x00401030 / sized delete 0x00aa1460 / Deallocate 0x00aa4060) within its
    first 0x30 bytes. None without the exe."""
    import retsrc
    pe = retsrc._pe()
    if pe is None or addr is None or addr < 0:
        return None
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    try:
        code = pe.read(addr, 0x30)
    except Exception:  # noqa: BLE001
        return None
    flag = False
    for i in md.disasm(code, addr):
        if i.mnemonic == 'and' and i.op_str.endswith(', 1'):
            flag = True
        elif i.mnemonic == 'call' and flag:
            try:
                return int(i.op_str, 16) in DEALLOCATORS
            except ValueError:
                return False
        elif i.mnemonic == 'ret':
            break
    return False


def _rule_dtor_split(d):
    """dtor-split-delete: the traces agree up to a call of a scalar deleting destructor wrapper, the native then
    frees with a deallocator of its own and goes on with the oracle's next call."""
    tx, ta = d.get('x86_trace'), d.get('arm_trace')
    i = d.get('index')
    if d.get('kind') != 'call-target' or not isinstance(tx, list) or not isinstance(ta, list) or not isinstance(i, int):
        return None
    if i < 1 or i >= len(ta) or tx[:i] != ta[:i] or d.get('arm_next') != d.get('x86'):
        return None
    if _hex(d.get('arm')) not in DEALLOCATORS or not _is_deleting_wrapper(_hex(ta[i - 1])):
        return None
    return 'dtor-split-delete'


def _rule_calls(d, rec=None):
    tx, ta = d.get('x86_trace'), d.get('arm_trace')
    if _rule_dtor_split(d):
        return 'dtor-split-delete'
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

HINT_REFINABLE = {'oracle-vcall', 'ilp32-pointer'}        # probe causes refine() still splits (report.reclassify hands them over)


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


X86_MAPPED = (TEXT, WINDOW, LIT_POOL, BUFS, HEAP, STACK)    # what the oracle's world maps for every case


def _rule_ilp32(rec):
    """The probe's ilp32-pointer (the native touched an address >= 4 GiB) split by where the address lands mod 2^32.
    In memory the oracle maps, the oracle made the same 32-bit access validly: a valid base plus an index that relies
    on 32-bit wraparound (a negative int carried as unsigned or as a pointer), which GCC's ILP32 code adds in 64 bits
    (`add x, base, w, uxtw`). The exe computes that address on such input, so it is not a pointer the exe never
    holds: ilp32-wrap, a real bug. Near null or unmapped, the by-design reading stands."""
    d = rec.get('_now') or rec.get('divergence') or {}
    a = str(d.get('arm') or '')
    if d.get('kind') != 'error' or 'ilp32-high' not in a or d.get('x86') is not None:
        return None
    try:
        v = int(a.rsplit(':', 1)[1], 16)
    except ValueError:
        return None
    if v >> 32 == 1 and any(_in(v & 0xffffffff, r) for r in X86_MAPPED):
        return 'ilp32-wrap'
    return None


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
    try:
        em = empty_callees(rec.get('source'), int(rec.get('address') or '0', 16))
    except (TypeError, ValueError):
        em = None
    if em and (fx or str(d.get('x86') or '').startswith('fault:')):
        return 'native-empty-callee'
    try:
        me = native_body(rec.get('source'), '__fnv_%08x' % int(rec.get('address') or '0', 16))
    except (TypeError, ValueError):
        me = None
    if me is not None and _empty_body(me[0]) and (fx or str(d.get('x86') or '').startswith('fault:')):
        return 'native-empty-body'      # the native function itself is a bare ret where the exe reads memory
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
_OBJOF = {}              # original address -> the link object that defines it (address map `object` column)
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
        _OBJOF.clear()
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
                    rest = f[3].rstrip('\n').split('\t')
                    _BOUND.add(rest[0])
                    if len(rest) > 1 and rest[1]:
                        _OBJOF[a] = rest[1]
            _STARTS.sort()


def _is_dtor(sym):
    return str(sym or '').startswith(('??1', '??_G', '??_E'))


def _defined_elsewhere(rec):
    """True when the address map names an object that defines the function: the link has it in another unit and
    the harness's image subset left it out (no-native-entry is then the harness's doing). Otherwise the port
    defines it nowhere (native-undefined)."""
    try:
        a = int((rec or {}).get('address') or '', 16)
    except ValueError:
        return False
    _load_addrmap()
    o = _OBJOF.get(a)
    return bool(o) and o != ((rec or {}).get('source') or '').replace('/', '_') + '.o'


def _canned_null(rec):
    """True when the case holds a canned call result below 0x10000. Not evidence of new-null on difffuzz-12 and
    later (allocators return heap blocks; a canned 0 belongs to another call); kept for reading old caches."""
    for r in ((rec.get('case') or {}).get('rets') or ()):
        try:
            if int.from_bytes(bytes.fromhex(str(r))[:4], 'little') < NULL_END:
                return True
        except ValueError:
            continue
    return False


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
        m = re.match(r'__fnv_([0-9a-f]{8})(?:_[CD][0-2])?$', n)   # __fnv_<addr>, or its secondary-base entry _C2 / _D2
        toks.add(m.group(1) if m else 'name:' + n)
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


CI_ENTRIES = {0x00eca1d0: 'acos', 0x00ec9650: 'asin', 0x00ec9050: 'atan', 0x00ec913a: 'fmod', 0x00ecae6c: 'exp',
              0x00ec9b00: 'pow'}     # the exe's unnamed x87 _CI* entries and their C functions


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
        # an unnamed CRT x87 intrinsic (_CIacos/_CIasin/...): GCC calls the same libm function or its float form;
        # any other math name is a different function (audit: _CIacos vs cos)
        m = CI_ENTRIES.get(xa)
        return m is not None and n in (m, m + 'f')
    if base is not None and base == m:
        return True                                     # sprintf vs __sprintf_chk (_FORTIFY_SOURCE)
    if n.startswith('__isoc23_') and n[9:] == m:
        return True                                     # strtoul vs glibc's C23 redirect __isoc23_strtoul
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
    if k == 'call-target' and xa == 0x00ec7894 and a is not None and d.get('arm_next') == x and \
            _hex(a) is not None:
        # (difffuzz-20, flow) the native's one extra call before the throw is the destructor of a temporary the exe
        # calls right after its _CxxThrowException 0x00ec7894 (dead code, run by the unwind map)
        import exeempty
        if _hex(a) in exeempty.calls_after_noreturn(addr, xa):
            return 'throw-temp-dtor'
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
        others = set()
        for n in cs - {'__fnv_%08x' % xa}:
            if native_defines(src, n):
                others |= native_reaches(src, n) or set()   # what another unit-local callee could have called
        if (k == 'call-target' and '__fnv_%08x' % xa in cs
                and native_ran(src, '__fnv_%08x' % xa, seen, exclude=cs | others)):
            return 'oracle-callee-unloaded'
        # (EQ, farm baseline 588863: 0x007d1bb0 StartMenu) runner's dynamic evidence: the native executed X's own
        # __fnv_<X> entry block in this run (arm_entered) and its call here is the oracle's next one, so the one
        # difference is the oracle's traced call of X, which the native ran untraced in its unit. A leaf X makes no
        # traced call, so native_ran has nothing to go on; an early exit before X never enters it (V1 U1)
        if (k == 'call-target' and '__fnv_%08x' % xa in cs and '%08x' % xa in (d.get('arm_entered') or ())
                and a is not None and d.get('x86_next') == a):
            return 'oracle-callee-unloaded'
    # (EQ, farm baseline 588863: 0x00905330 -> BSSimpleList::IsEmpty 0x008256d0) the oracle's last call is X, a
    # callee the native's object only references (a provider of the unit image defines it): the native entered its
    # __fnv_<X> block in this run (arm_entered) and ran it untraced, so the traces are equal but for that one call
    if (k == 'call-count' and a is None and xa is not None and xa != addr and len(tx) == len(ta) + 1
            and '%08x' % xa in (d.get('arm_entered') or ()) and '__fnv_%08x' % xa in (native_callees(src, addr) or ())):
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


def _native_calls_more(rec, callee):
    """The record shows the native made calls to `callee` the oracle did not trace: a call-count / call-target
    record whose native trace holds it more often. The oracle runs that callee's code untraced and calls are
    compared before writes, so any other record kind means the native never made the call (S13b audit C5)."""
    if callee is None:
        return False
    d = (rec or {}).get('_now') or (rec or {}).get('divergence') or {}
    if d.get('kind') not in ('call-count', 'call-target'):
        return False
    t = '%08x' % callee
    return list(d.get('arm_trace') or ()).count(t) > list(d.get('x86_trace') or ()).count(t)


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
                and _native_calls_more(rec, intercepted_callee(rec, pc))):
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


def _zero_stub_of_leaf(d):
    """(S2 W6) every placeholder the native ran stands for an exe function that is itself a trivial leaf
    (retsrc.trivial_leaf: straight-line, no call, no store outside its frame): not the native-zero-stub class; the
    record goes on to the other rules (0x0065f820: 0x0065e750 returns an uninitialised al, 0x0065fd70 its argument)"""
    exe = d.get('arm_zero_stub_exe') or ()
    if not exe or '?' in exe:
        return False
    try:
        import retsrc
    except ImportError:
        return False
    return all(retsrc.trivial_leaf(int(a, 16)) for a in exe)


def _ret_bytes(case, k):
    import world             # plain module (no emulator): the same move out of the private ranges as Case.ret_bytes
    rets = case.get('rets') or []
    if k < len(rets) and rets[k] is not None:
        return world.canned_out_of_private(bytes.fromhex(rets[k]).ljust(16, b'\0'))
    import random
    r = random.Random(((case.get('seed') or 0) * 1000003 + k) & 0xffffffff)
    return world.canned_out_of_private(bytes(r.getrandbits(8) for _ in range(16)))


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
_ZERO_BODIES = {struct.pack('<II', 0x52800000, 0xd65f03c0), struct.pack('<II', 0x2a1f03e0, 0xd65f03c0),
                # (F-uncls, difffuzz-20) a float / double placeholder: `movi v0.2s, #0; ret`, `movi d0, #0; ret`
                struct.pack('<II', 0x0f000400, 0xd65f03c0), struct.pack('<II', 0x2f00e400, 0xd65f03c0)}


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


def _thunk_delta(rec, vx, va):
    """The native pointer is the oracle's plus d, both into one object region (literal pool / case buffers / heap /
    image), and the function or a direct callee in its unit has a _ZThn<d>_ this-adjusting thunk by exactly d: an
    override entered with MSVC's secondary-base this while __fnv_<addr> is bound to the GCC primary entry (S1-layout:
    these were labelled layout-offset)."""
    off = (va - vx) & 0xffffffff
    if not (0 < off <= 0x400 and off % 4 == 0):
        return False
    regs = (LIT_POOL, BUFS, HEAP, WINDOW)
    if not (any(_in(vx, r) for r in regs) and any(_in(va, r) for r in regs)):
        return False
    th = (_object_facts(rec.get('source')) or ({}, set()))[0]
    return any(off in th.get(n, ()) for n in _own_and_callees(rec) or ())


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


CODE_STUB, NAMED_STUB = 0x08000000, 0x09000000   # a64image (duplicated: this module needs no emulator imports)


def _stub_rewrite(bx, ba, diff):
    """True when every differing byte lies in a 4-byte window whose oracle word w is in [CODE_STUB, NAMED_STUB) and
    whose native word is exactly w - CODE_STUB: the rewrite arm64emu._codeptr applies to any stored word in that
    range (on a 1-byte sliding window), and nothing else."""
    covered = set()
    for i in range(0, len(bx) - 3):
        w, n = int.from_bytes(bx[i:i + 4], 'little'), int.from_bytes(ba[i:i + 4], 'little')
        if CODE_STUB <= w < NAMED_STUB and n == w - CODE_STUB:
            covered.update(range(i, i + 4))
    return bool(diff) and all(i in covered for i in diff)


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
    if d.get('arm_zero_stub') and not _zero_stub_of_leaf(d):
        # (F-uncls, difffuzz-20) the native entered a function of its unit whose whole body is a placeholder that
        # returns 0 (runner.zero_stubs): the exe's work there is not done, and what the function returns or stores
        # from its result differs (0x00d57b20 stores 1.0 - 0.0 where the exe's FUN_00d571d0 returned 0.51)
        return 'native-zero-stub'
    if k == 'mem-write':
        bx, ba = bytes.fromhex(d['x86']), bytes.fromhex(d['arm'])
        diff = [i for i in range(min(len(bx), len(ba))) if bx[i] != ba[i]]
        if diff and len(bx) == len(ba) and _stub_rewrite(bx, ba, diff):
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
    if cause == 'ilp32-pointer':
        return _rule_ilp32(rec) or cause
    if cause in HINT_REFINABLE:
        return _rule_vcall(rec) or cause
    if cause not in REFINABLE:
        return _with_effects(rec, cause)
    d = (rec.get('_now') or rec.get('divergence') or {})
    k = d.get('kind')
    got = None
    if k == 'error':
        got = _rule_error(d, rec)
        if got == 'native-trap':
            got = _rule_trap(d, rec) or got
        if got == 'native-null-read' and _calls_allocator(rec):
            pass        # new-null retired (S13b audit): since difffuzz-12 the allocators return heap blocks, so a
                        # canned 0 comes from another call, a valid input: the native null access stays a real bug
        elif got == 'oracle-fault':
            got = _rule_oracle_fault(d, rec) or got
    elif k == 'call-args':
        got = _rule_call_args(d, rec)
    elif k == 'return':
        got = _rule_return(d, rec.get('conv'), rec.get('symbol'))
    elif k == 'mem-write':
        got = _rule_mem_write(d, rec)
    elif k in ('call-count', 'call-target'):
        got = _rule_empty_call(d) or _rule_calls(d, rec)
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
        'fix_classes': fix_classes(causes),
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
    tot['fix_classes'] = fix_classes(tot['causes'])
    return tot


HANDOFF_FILES = (('real-bug', 'handoffs'), ('triage', 'triage'), ('artifact', 'harness'))
HANDOFF_COLUMNS = ('owner', 'cause', 'address', 'unit', 'reproducer', 'first effect', 'fix class')


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
            shard = (results.get(rec['address']) or {}).get('shard') or name   # a seeded row keeps its source
            ref = ('fuzz/%s/%s:div/%s' % (head, shard, rec['_file']) if by_head and head else
                   '%s%s:div/%s' % (ref_prefix, name, rec['_file']))
            out[(prefix[cls], owner)].append((owner, rec['_cause'], rec['address'], rec.get('source') or '', ref,
                                              e.get('first') or '', FIX_CLASS_OF.get(rec['_cause'], '')))
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
    fc = sub.add_parser('classes', help='the D21 fix classes of a causes.json (summary or merge output)')
    fc.add_argument('file')
    fc.add_argument('--tsv', default='')
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
    if a.cmd == 'classes':
        fcs = fix_classes(json.load(open(a.file)).get('causes') or {})
        lines = ['class\towner\tworst_of\tfunctions\tdivergences\tcauses\tmechanism']
        for k, v in fcs.items():
            lines.append('\t'.join([k, v['owner'], str(v['worst_of']), str(v['functions']), str(v['divergences']),
                                    ', '.join('%s %d' % kv for kv in v['causes'].items() if kv[1]), v['mechanism']]))
        if a.tsv:
            open(a.tsv, 'w').write('\n'.join(lines) + '\n')
        for l in lines:
            print('  '.join(l.split('\t')[:6]))
        return 0
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
