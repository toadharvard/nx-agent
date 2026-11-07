### J-XXXX | 2026-11-07 | stream: R3 (recon fleet, shard 2) | status: active | parent: J-0156

* **shard** - D23 shard 2 (sha1(home unit) mod 10 == 2), from S2-bodies' list s2d/list/9f865b0a4037 on fnv-nx-build
  (`$8=="recon" && $9==2`), per the orchestrator's message. Names and units stay on fnv-nx-build (agent-only).

  | cause | detail | names | trap stubs |
  |---|---|---:|---:|
  | no-body | msvc-direct | 50 | 49 |
  | no-body | other | 40 | 38 |
  | no-body | exe-shape | 6 | 6 |
  | no-body | template-special | 3 | 0 |
  | extern-text | name-addr | 27 | 27 |
  | extern-text | unit/exact | 5 | 5 |
  | **total** | | **131** | **125** |

  22 home units; the largest four hold 92 of the 131 names.

* **metric** - shard functions that are trap stubs and still have no accepted body (D23 a or b): **125** at start.
  The 6 non-stub names are carried along but not counted.

* **setup** - fnv-nx-build cloned; toolchain/msvc9 and gamedata/exe unpacked (FalloutNV.exe sha256 matches HANDOFF
  section 5); Wine + MSVC 9 setup running.

* **order** - boot path first, then most-referenced; one fnv-nx-build branch `recon/<unit>` per home unit.
