# fnv-nx overall status (orchestrator session_01KmtZKRGpFka9ShREBTYbTx)

Updated 2026-11-11 22:55Z (first hourly check after handoff 3). Trunk `claude/fnv-nx-clean` a2453fe9: L1 green
(3.7 s warm / 236 s cold), 23 gates, 0 product roots. Goal path: product roots -> lifted packs as roots -> ELF ->
boot -> WinMain -> menu -> first frame (GL in the emulator).

## Blocker of goal step 1
gate_admit.REQUIRED_PRODUCT names 32 gates, gates.json has 23: **13 missing**, so no product root can be admitted.
One owner per gate: datamodel (L1 w17), exepin (L1 w8 via w4), imports + machine (L5 f15935a9, rebase), r7 + namegate +
abi (L2), order + eh + addr32 (L3a), forms + fpexact (L3b), d3d9total (L6 w4 session_01KmtZKUN17roaVoBLBe4QKK).
Table with branch/commit/MERGER state: status/l1-contract.md (L1 05d3d184).

## Per layer (lead status file commit)
| layer | lead | path item now | workers | global numbers (commit) |
|---|---|---|---|---|
| L1 | session_01KmtZKUn5UJpiHYPtNRT6p9 | 13 missing gates; recipe link stage (w15); sources from externals + env pins (w16) | w4 w5 w10 w12 w14 w15 w16 w17 | gates 23, roots 0 (a2453fe9); missing required gates 13 |
| L2 | session_01KmtZKRi5XRMhd5qzRaEyxC | ORDER-CONT in fnvnx_order (w4, blocks lift/rt); r7/namegate/abi gates | w1 w2 w3 w4 | /Z7 2,949/2,988 built, 39 failed (8f324ec, off path) |
| L3a | session_01KmtZKSANKhYEe4nVesF6Ho | lift/rt root (owner; take L3b w2 be36b59a); pack root (w1); order/eh/addr32 gates | w1 w2 w3 w4 w5 | pack objects 0; lift/rt undefined symbols 16 (L3b 6385fff6) |
| L3b | session_01KmtZKSQpF5Su9oG96Kbe4A | forms + fpexact gates; lift/rt root built 11/11 (w2 be36b59a) -> handed to L3a | w2 w3 w4 | test programs with doubles 9/35; shape certs 0/26,500 (eb6f0b2b) |
| L4 | session_01KmtZKUcrakBqKb1mC8iXp4 | boot of the next ELF (waits ELF); GPU tests onto real libnxgl4 (w1b) | w1b w2, farm tc-repro | last boot ELF 509809b3b809: 17/17 loader steps, exit 0xC000001D after init d3dx9_38 |
| L5 | session_01KmtZKTGM2tLun8zgoE6X44 | platform/build.json root (w10); f15935a9 rebase | w9 w10 w11 w12 w13 w14 w15 | dynamic Reg* sites open 4/20; platform red units: not measured yet |
| L6 | session_01KmtZKRq7TGNHj6H8m8jpAZ | render/d3d9 root (w2); d3d9total gate (w4); MojoShader host root for GL (w1b) | w1b w2 w3 w4 | d3d9total 332/332 (f8545efa); render roots 0 |
| L6A | session_01KmtZKSgdFAv1Sd3kGuQtHk | GL root render/gl4 (w6 9a713d9, red 0/17 on L1 gaps); dk root red on -isystem (L1) | w4 w6 | dk harnesses with doubles 12/12 (4c542643, after first frame) |

## Decisions this hour
- One owner per missing gate (above).
- lift/rt product root: one owner L3a; L3b w2's built root (be36b59a) handed over; fnvnx_order extensions of L3b w2 go
  through L2's ORDER-CONT review.
- rt `fnvnx_f2i` attribute names no plugin (L3b): L3a, by rule (unknown fnvnx_* attribute = build error).

## Sessions (sweep 22:49Z)
45 live (8 leads, MERGER, 35 workers, 1 farm), none failed, none on a permission prompt. Finished, idle: L3b w1, L6A w5.
About 43 sessions of the Nov 7-9 waves (farms sw*, full1, qk1, streams R2/R4/S1/S5/S16/T1) are still not archived:
archiving them waits for the owner's word. pgrep autoadopt: empty.
