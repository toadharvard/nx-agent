# tools/link - link by address (stream S2-link)

Design, formats and measurements: [`../../docs/LINK.md`](../../docs/LINK.md).

| File | What |
|---|---|
| `compile.sh` | Compiles prepared units to AArch64 ILP32 objects (census flag set, `-c -O2`); incremental. |
| `fnvlink.py` | `resolve` / `apply` / `check`: the link step itself. |
| `sigs.py` | Parses llvm-undname and c++filt output into comparable signatures (canonical names, loose levels). |
| `abi.py` | x86 and AArch64 argument-slot models, the reference/definition verdict, bridge thunk generator. |
| `layout.py` | Per-unit probe: size/align/trivial/empty and AAPCS64 class of by-value aggregates. |
| `abi2.py` | Byte-level verdict with layouts and the general (frame-based) bridge generator. |
| `vtables.py` | Image vftables (primary and `{for B}`) -> Itanium address points; synthesized vtables/typeinfo for classes no object emits. |
| `vslots.py` | GCC vtable slots bound through the MSVC 2008 vftables of the pristine decomp (`--msvc-objs`): static targets or stubs through the image slot; `validate` against the x86 code. |
| `bodies.py` | Declared-only C++ functions (S2d): bound by address under another name, `ret` stubs / inline bodies from the pristine MSVC objects, `= default` special members, the rest listed (`fnvlink.py bodies`). |
| `test_override.py` | End-to-end `--override` test (hand-written units; a variadic replacement with its own signature). |
| `overrides/` | The port's native replacements (`overrides.tsv`, sources, `build.sh <out>` -> the `--override` table). |
| `test_bridges.py` | Runs generated bridges under qemu-aarch64 (hand-written declarations). |
| `test_bindings.py` | CRT/library providers, IAT slots by api, thread-local variables, hand-written vptr stores. |
| `test_startup.py` | `__fnvnx_dyninit_`/`_dynexit_` address entries and the game-only `__cxa_atexit` rename. |
| `test_vtables.py` | Secondary/synthesized vtables and the `??_G` D1/D0 table against GCC's own output and the linked image. |
| `test_vslots.py` | Slot order and binding on hand-written classes compiled by MSVC 2008 (Wine) and GCC; image-slot stubs under qemu. |
| `elfsyms.py` | ELF symbol tables, vtable-slot relocations, renaming undefined symbols (many old names -> one new). |
| `fnvnx_addrmap.h` | Runtime form of the address map (`addrmap.c` is generated). |
| `test_link.py` | Self-test on hand-written names (no decomp content). |

```sh
T=build/tree; O=build/obj; L=build/link             # all local, derived from the decomp: never commit
tools/prepare.py ext/fnv_decomp $T
SSE2NEON=ext/sse2neon tools/link/compile.sh $T $O   # [units.list] [jobs]; writes $O/objects.tsv
tools/link/fnvlink.py resolve $T/build/units.json $O/objects.tsv $L
tools/link/fnvlink.py probe $L $T                   # layouts of the aggregates + synthesized vtables; resolve again
tools/link/fnvlink.py resolve $T/build/units.json $O/objects.tsv $L [--override o.tsv] [--extern-text s4.txt] \
    [--override-lib libnxrender.a]                 # override symbols from an archive (render/native/overrides.tsv)
    [--msvc-objs $S11/obj/pristine]                # vtable slots through the MSVC vftables (u<index>.obj)
                                                    # [--msvc-code $S11/obj/pristine] only the definitions' ecx
                                                    # reads (ecx-not-passed verdicts; also with --msvc-objs)
                                                    # [--crt-symbols t.tsv|none] [--imports t.tsv|none]
                                                    # [--units-renames t.tsv|none] (S1b, patched units only)
tools/link/fnvlink.py bodies $L $T [--msvc-objs $S11/obj/pristine]   # declared-only functions; resolve again
tools/link/fnvlink.py apply $L $L/fixed             # renamed objects (own references of rebound variables
                                                    # retargeted) + zz_bridges.o + zz_addrmap.o; typeinfo
                                                    # kept with its vtable (typeinfo_pairs.tsv)
tools/link/fnvlink.py check $L $L/fixed             # trial link with aliases.ld; what is still undefined
python3 tools/link/test_link.py; python3 tools/link/test_bridges.py   # need SSE2NEON; qemu-user for the second
python3 tools/link/test_override.py; python3 tools/link/test_vtables.py; python3 tools/link/test_startup.py
FNV_DECOMP=... FNV_MSVC9_ROOT=... FNV_MSVC9_PREFIX=... python3 tools/link/test_vslots.py   # MSVC 2008 under Wine
python3 tools/link/test_bindings.py
```

Needs `llvm-undname` (or `llvm-undname-18`; `apt install llvm-18`), `c++filt`, and binutils/gcc for
`aarch64-linux-gnu`. Override with `UNDNAME`, `CXXFILT`, `OBJCOPY`, `AS`, `LD`, `CC`.
