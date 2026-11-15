# L4 VERIFY 007: first ELF ran in the boot stage (2026-11-11)

- ELF `509809b3b809` (L1, 216,702,236 bytes, sha256 checked against its manifest) ran in the boot stage.
- Result: exit 0xC000001D after `init d3dx9_38.dll`: the module has no lifted body yet (L3a module packs). Expected.
- Numbers: 1,075,895,420 instructions, all on the main thread (1 thread); 342 host calls; 0 refused by the stage;
  28 s wall (about 38 M instructions/s); a second run gave a byte-identical report.
- The count equals L1's own run in the old emulator exactly (difference 0), same stopping point.
- Milestones: 17 of 17 loader lines in order (`identity ok` at 786.6 M, `image mapped` at 787.7 M, 4 modules mapped,
  `imports bound 280`, `init d3dx9_38.dll` at 1,075.9 M). `exe-entry` (0x00ecc4db), WinMain, menu and frame: not reached.
- 73 % of the instructions run before `identity ok` (measurement, reported to L1).
- Results: fnv-nx-build `l4-verify/boot/509809b3b809`.
- Stage changes (f79bbe3): `verify/bootinputs.py` joins the ELF parts and converts L1's tables, failing on any unknown
  row; the entry manifest takes `code|import` rows and a table id; `boot_job.sh` fetches exe and redist only by default.
- Gates (3643c65, in the trunk at 10c01c7f, verdict green, 7 gates, 126 s): ILP32 objects in every gate, COMDAT and
  link-script rules in `linkstrict`, C++ unwinding is not a terminator, the Wine-reference rule with `wine_fixme.tsv`.
- Next milestone is blocked by: module packs (L3a) and an ELF with them (L1); milestone addresses for WinMain, the
  DllMain entries, menu and first frame (asked of L1).
