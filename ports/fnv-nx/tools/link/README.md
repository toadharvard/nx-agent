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
| `test_bridges.py` | Runs generated bridges under qemu-aarch64 (hand-written declarations). |
| `elfsyms.py` | ELF symbol tables, vtable-slot relocations, renaming undefined symbols (many old names -> one new). |
| `fnvnx_addrmap.h` | Runtime form of the address map (`addrmap.c` is generated). |
| `test_link.py` | Self-test on hand-written names (no decomp content). |

```sh
T=build/tree; O=build/obj; L=build/link             # all local, derived from the decomp: never commit
tools/prepare.py ext/fnv_decomp $T
SSE2NEON=ext/sse2neon tools/link/compile.sh $T $O   # [units.list] [jobs]; writes $O/objects.tsv
tools/link/fnvlink.py resolve $T/build/units.json $O/objects.tsv $L
tools/link/fnvlink.py probe $L $T                   # layouts of the aggregates; then resolve again
tools/link/fnvlink.py resolve $T/build/units.json $O/objects.tsv $L
tools/link/fnvlink.py apply $L $L/fixed             # renamed objects + zz_bridges.o + zz_addrmap.o
tools/link/fnvlink.py check $L $L/fixed             # trial link with aliases.ld; what is still undefined
python3 tools/link/test_link.py; python3 tools/link/test_bridges.py   # need SSE2NEON; qemu-user for the second
```

Needs `llvm-undname` (or `llvm-undname-18`; `apt install llvm-18`), `c++filt`, and binutils/gcc for
`aarch64-linux-gnu`. Override with `UNDNAME`, `CXXFILT`, `OBJCOPY`, `AS`, `LD`, `CC`.
