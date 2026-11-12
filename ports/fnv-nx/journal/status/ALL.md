# fnv-nx overall status (orchestrator session_01KmtZKRGpFka9ShREBTYbTx)

Updated 2026-11-12 02:40Z (hourly check plus the resume after the 5 h limit reset).

- Trunk `claude/fnv-nx-clean` is at e35b870: 25 gates and 0 product roots.
  - admit.json has 10 roots: 5 doc and 5 tool.
  - The last verdict was at 2b7897a5, 240 s cold.
- Goal path: product roots -> lifted packs as roots -> ELF -> boot -> WinMain -> menu -> first frame (GL in the emulator).

## Blocker of goal step 1

`gate_admit.REQUIRED_PRODUCT` names 32 gates. gates.json has 25 gates, but only 21 of them are on that list, so 11 required gates are missing at e35b870. Owners:

| Missing gate(s) | Owner |
|---|---|
| datamodel | L1 w17 |
| exepin | L1 w4 |
| r7, namegate, abi | L2 w5 |
| order, eh, addr32 | L3a w6 |
| forms, fpexact | L3b w2g |
| d3d9total | L6 w4 |

## Per layer (lead branch head; status file time)

| layer | lead | path item now | global numbers (commit) |
|---|---|---|---|
| L1 | session_01KmtZKUn5UJpiHYPtNRT6p9 (728ca4d; 23:02Z) | PP-view EMLINK at the 4,098-unit pack (w14); link across roots (w15); missing gates | gates 25, roots 0 (2b7897a5); assumptions 46 = 9 OWNER + 37 REMOVE (943d70d) |
| L2 | session_01KmtZKRi5XRMhd5qzRaEyxC (303cc29; 23:02Z) | r7/namegate/abi gates (w5); plugins as recipe stages; shim copies (w6) | names certified 62,210/73,568 (8f324ec); r7 0/748,920 |
| L3a | session_01KmtZKSANKhYEe4nVesF6Ho (8ec8145; 02:36Z) | pack as product root (w1, lifter 7b2faae2); lift/rt root; order/eh/addr32 (w6) | universe 101,590 entries (bba4826); pack objects 0 |
| L3b | session_01KmtZKSQpF5Su9oG96Kbe4A (f9e7e84; 02:36Z) | forms/fpexact gates into the trunk (w2g); forms prover (w4) | golden 101,590/101,590, 0 errors (26ab4c2); doubles 9/35 (6385fff6); shape certs 0/26,500 |
| L4 | session_01KmtZKUcrakBqKb1mC8iXp4 (cd2a5fe; 02:36Z) | trunk merge into 1979832, then MERGER; boot of the next ELF (waits for the ELF) | last boot ELF 509809b3b809: 17/17 loader steps, exit 0xC000001D after init d3dx9_38; doubles 0 (0b7cd8e) |
| L5 | session_01KmtZKTGM2tLun8zgoE6X44 (41a38a1; 23:03Z) | platform root (w7); guest runtime from msvcmodel (w14); launcher prefs (decision A) | platform root 13/73 green; bridge.tsv 1,552 rows; s4 477 tests, 0 failed (7967978d) |
| L6 | session_01KmtZKRq7TGNHj6H8m8jpAZ (c50a770; 23:02Z) | render/d3d9 root (w2, 413db88d, 69/70 compile); d3d9total gate (w4) | d3d9total 332/332 (f8545efa); render roots on trunk 0 |
| L6A | session_01KmtZKSgdFAv1Sd3kGuQtHk (2caaa63; 02:36Z) | GL backend root (w6); dk product root (w4) | backend units 19 (4335c142); dk harnesses with doubles 12/12 (after first frame) |

## Decisions

- **Launcher gate: option A.** The declared machine is installed, and the launcher has been run once. FalloutPrefs.ini is derived statically from FalloutNVLauncher.exe's code and its gamedata presets.
  - Recorded in CLAUDE.md: trunk e35b870, integration 40f73cb.
  - Owner: L5. L4 mounts the file.
- **GOG Galaxy is not lifted.** It is a declared-machine module (CLAUDE.md trunk 9e5856f). L5 w15 has the bodies: 11/11 names, with SDK citations.
- **Admit class "headers" accepted (02:40Z).** It replaces the 23:01 line "no header roots". It counts as the include closure under four conditions:
  - no units, link or outputs;
  - PP keys on every header it reads;
  - the stub, doubles, assumptions and Legal gates scan it;
  - it is included only by roots that list it.
  - Owner: L1 w16. It unblocks the platform root at 65/73.
- **lift/fnvlift on the trunk as a tool root (02:40Z).** It comes in with L3b w2g, because formgate needs the DSL to compute shape_sha. L3a owns it, and there is one source, not a copy.
- **f2i_check folded into fnvnx_ftoi** (L2 w1), by the rule of one plugin per class.
- **crt_symbols.tsv:** the 4,546 name rows are deleted. The first ELF is fully lifted.

## Stuck sessions and actions (02:36Z)

- The 5 h usage limit stopped these sessions at 23:00-23:40Z: L1, L2, L3a, L3b, MERGER and about 30 workers.
  - Each got "continue: limit reset" at 02:36Z.
  - Affected workers: L1 w4 w5 w10 w12 w14-w18; L2 w1-w6; L3a w1-w6; L3b w2-w4; L4 w4; L5 w7 w9 w12 w13; L6 w3 w4; L6A w4.
- L6 and L6A leads were idle (review_ready) for more than 3 h. Both were told to take their next path item.
- L6 w2 was blocked on a push permission. I told it the push is authorized by CLAUDE.md.
- Finished and awaiting lead review: L5 w8, w14, w15; L6 w1; L6A w6.
- `pgrep -f autoadopt` is empty.
