# L1 CONTRACT 006: state at the second stop (2026-11-10, about 08:55Z)

Goal that was started: the first guest binary of the new scheme, fully lifted, started from the exe entry point.

## Done and pushed (branch claude/fnv-l1-contract; not yet merged into the clean branch)
- `link/ld.py`: links every input object and archive whole, position independent; red with `undefined.tsv` and
  no ELF when any referenced name is undefined. Checked with the local GNU ld on toy objects both ways. Not run on
  real lifted objects (L3 has no packer yet).
- `contract/iat.py`: value of every import slot. On the exe: 240 platform slots have keys
  (0x00100000 + 16 * index in L5's bridge.tsv), 40 module slots (d3dx9_38 25, binkw32 9, libvorbisfile 6) have none.
  Tables in fnv-nx-build `l1-contract/mp4`: iat.tsv, start_imports.tsv (56 kernel32 imports reachable from the entry
  point through CRT code).
- `loader/src/tls.c`: per-thread TLS block from the exe's TLS directory.

## Not started
- Loader start sequence (`loader/src/start.c`: identity, map, import slots, TLS, TEB, stack, entry through
  `fnvnx_lift_enter`). A worker was stopped before it wrote any file; nothing of it is in the tree.

## Open questions sent, no answer yet
- L3: module load bases. Module universes are at preferred base 0x00400000, the same as the exe, so an image
  address is not a dispatcher key until modules get distinct bases. Proposed: static bases after the exe image,
  modules in file-name order, 64 KiB aligned; the loader applies the module's own PE relocations; I publish
  modules.tsv.
- L3 still lacks: the packer (objects + dispatcher table + manifest), 420 entries without a body, the R3
  universe, import entries, thread/TEB entry `fnvnx_lift_enter`.

## Exact next steps on resume
1. Write the start sequence (above), test first.
2. On L3's answer: modules.tsv and the 40 module slots in iat.tsv.
3. Gate "dispatcher entries = universe" from L3's entries.tsv.
4. Send `merge claude/fnv-l1-contract` to MERGER.
5. Earlier queue (note 005): BODYSEL on real N3 verdicts when L2 objects arrive (final L2 sha 7200c194d825).
