### J-XXXX | 2026-11-07 | stream: R3 (recon fleet, shard 2) | status: active | parent: J-0156

* **metric** - shard-2 trap stubs without an accepted body (D23 a, or an alias of an exe function other units place):
  **130 -> 69** (baseline 130 on s2d/list/9f865b0a4037 @ bf0b0ebf: the 125 of recon3-001 plus 5 code-defmissing).
  Offline: the CRT rows by load_providers, the bodies by build_and_match + the GCC compile below; the next link confirms.

  | | names |
  |---|---:|
  | CRT provider rows (766ecfb: memcpy_s past its prologue, _time64, std::uncaught_exception) | 31 |
  | bodies, all bytes equal under MSVC 9 (accept a) | 25 |
  | aliases (recon.json symbols: the name is an exe function another unit places) | 5 |
  | appended without a byte match, accept pending fuzz (counted as open) | 6 |
  | code-defmissing, left to S2-bodies' addr-host at the next link (bodies exist in the decomp) | 5 |
  | in progress (2 workers: the largest unit and 10 small units) | the rest |

* **published** (fnv-nx-build, agent-only): recon/staging_blk_00c40000_od_ob1_variant (142/142 functions),
  recon/staging_blk_00440000_od (245/245), recon/gamebryo2.2_corelibs_nimain_nistream (alias only),
  recon/staging_blk_00b40000_od_ob1_variant (24/24), recon/speedtree_leafgeometry_ob1_variant (11/11). Each:
  build_and_match all bytes equal on the unit with the bodies (one EH tail of blk_00440000, 0x00eed640, fails the
  same without them), REC1 + the game's GCC flags compile the unit, and every shard name of the unit is then
  defined or no longer referenced (aliases stay undefined and bind by address).

* **finding: every matched body is an exe function another unit already places** (0 of 25 at a new address). The
  missing names are mostly the decomp's renamed copies of VC9 STL and Gamebryo helpers (vector/_Tidy/_Ufill/
  _Insert_n, NiTPointerList, NiPoint3::SqrLength, basic_string::_Copy) whose single exe copy, folded across
  instantiations, a unit places under another class name. Method: compile the body with the unit's flags, then a
  masked byte search of the exe .text (code match), when no placed caller reaches the name.

* **REC1 request (owner of tools/rules)**: REC1 skips a whole recon as REC1-DUP when any listed function's address is
  already defined, so these bodies are recorded as `equal_placed` in recon.json (not `functions`). REC1 appends the
  body and adds `symbols` either way, so nothing is lost, but build_and_match on the prepared tree does not see them.
  Proposal: REC1 skips only the duplicate entries (REC1-DUP per function), not the recon.

* **unmatched kinds** (6): a declaration the exe contradicts (void vs. a hidden-pointer return, a constructor declared
  as a void member, a member inline in the unit that the exe calls out of line) - fixing those needs a declaration
  change, which the recon does not make; and 2 with no exe copy (only virtual or inlined call sites). Their bodies are
  faithful but unproven: D23 (b) fuzz is the next step.
