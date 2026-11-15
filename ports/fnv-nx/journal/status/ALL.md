# fnv-nx overall status (orchestrator session_01KmtZKRGpFka9ShREBTYbTx)

Updated 2026-11-14 23:30Z (path plan). Older sections below are from 2026-11-12 03:41Z. The hourly census of all sessions was taken at 03:36Z; the trigger fired at 03:39Z, and only the lead reports since then were added.

- Trunk `claude/fnv-nx-clean` is at 8cfbcdbf: 28 gates and 0 product roots.
  - Merged since 02:40Z:
    - L1 w14 (EMLINK, d6c72958);
    - L1 w18 (image bases, 7675fda5);
    - L1 w17 (datamodel, aaa709c8);
    - L6 w4 (d3d9total, 8cfbcdbf).
  - The last verdict took 302.9 s cold. Budget rule (L1, accepted): 300 s gates the warm incremental verdict on the pinned farm host, and cold time is reported only. The open defect is that warm ensure() takes 63 s with 0 stages run, and a docs-only change took 303 s. w19 must show per-gate times, and a gate that re-runs without a changed declared input is red.
- Goal path: product roots -> lifted packs as roots -> ELF -> boot -> WinMain -> menu -> first frame (GL in the emulator).


## Path plan 2026-11-14 (binding; rules in CLAUDE.md "Owner order of 2026-11-14" and its decisions)

**Decision 2026-11-15 (owner): OWNER 10, the x86 ISA as the Intel SDM documents it, for the parts no pinned formal model
covers.** This unblocks 2.6 x87, 2,063 shapes of pack 7ef692b5. L3b transcribes the x87 machinery as a z3 spec with a
citation per line; values stay IEEE under OWNER 8. L1 adds the registry record, quoting CLAUDE.md item 10.

**Open run-time class with no gate yet (2026-11-15): native stack overflow per thread** (reported by L1, w24 at 8dc852415:
5554 functions, 600 bounded). Owner L1 with facts from L3a (argflow, frame facts, indirect targets) and L5 (stack placement).
Rule: the lifted depth is bounded by the guest stack (dwStackSize or the PE reserve) times the static A64/x86 frame ratio; the
native code gets a sound call-graph bound, with recursion certified or rejected. Scheduled after the first frame, before Switch.

**Legal (owner, 2026-11-15: "Защита закрытых данных. Похуй."):** no Legal gates are built or scheduled; the `legal`
admit requirement is deleted. The practice stays: private artifacts live in fnv-nx-build only.

**Speed, after the first frame (owner, 2026-11-15):** first nativization and the PERFDEBT restores. Only after them, a
deterministic A57 cache model (L1/L2 by documented sizes) in the L4 emulator, giving misses per frame per thread beside the
instruction counts. Owner L4.
Also after the first frame (owner, 2026-11-15; CLAUDE.md "parallel loops and deviations"): the parallel-loop rule (owner
L3a for the proof over the points-to model, L5 for the fork-join runtime) and verify/DEVIATIONS.json with its gate (owner L1);
a deviation is admitted only for a stated goal.

All work resumed at 2026-11-14 23:00Z on the candidate `claude/fnv-next3b` 141b07580. That is 224af83e plus L1
w17m 4ee6cc10 plus CLAUDE.md. Its verdict v9 is running. The trunk is a5c8f427 (38 gates). The platform root is not
on it yet. The last full verdict, v8 at 224af83e, gave 27/39 green. Numbers come from the lead status files of
2026-11-12, unless a line says otherwise.

Every item is a step on the path, closed by a known rule. A lead runs at most 3 workers at a time, all on its items
below. Nothing else runs.

**Step 1: the first root on the trunk** (platform, msvcmodel13, all plugins). Merged through `l1.py merge` on a green
verdict.

| # | Owner | Item | Rule / done when |
|---|---|---|---|
| 1.1 | L1 | w23 comdat_identity (linkstrict 4888/5783, abi odr, onedef) | identity modulo normalised relocations; undecidable is red |
| 1.2 | L1 | w20u: the namegate/r7/objstub universe from admit covers. w20: objstub exit 2 | universe 0 with an admitted root is red; a gate crash is a finding |
| 1.3 | L1 | w22f2: the fpexact reader | expected 113 clean + 1 (mp3, see 1.6) |
| 1.4 | L5 | w10: `assumptions`, hostapi, abi layout, imports/machine inputs, boot selftests' HOSTABI from the recipe | gates green on 141b07580 |
| 1.5 | L5 | w14: publish tools/fnvnx-a64-msvcmodel13 (farm). L1 w4 checks the MANIFEST. Rebuild the platform root on it; revert source changes made only for gcc 15 (ini.cpp std::map, unless a rule names it) | root built on msvcmodel13 |
| 1.6 | L2 | w1: MINIMP3_NO_SIMD unconditional | native code holds no explicit target float→int intrinsic; global builtin findings over the platform root = 0 |
| 1.7 | L1 | w19: warm verdict ≤ 300 s on the trunk | the build is always fast |

**Step 2: the lifted packs as roots.**

| # | Owner | Item | Rule / done when |
|---|---|---|---|
| 2.1 | L3a | w8 t16k: build lift/pack, lift/rt, bridges and the 7 test roots; run the 6 guest tests against references | recipe store only |
| 2.2 | L3a | w6: break the fnvlift↔proof cycle and gate that the root graph is acyclic; independent references for the 21 moved tests (rows with none go to L1); the 8 build-path defects onto recipe.py with declared externals | 0 stock-g++ runs, 0 undeclared inputs |
| 2.3 | L3a | w1: the product relift of all 7 images (shapes.tsv, the SEH batch, DEF0/DEFPRIOR, origin flags). Publish its shape-key list to L3b at once | one pack, every image |
| 2.4 | L3a | w7: the facts commit (jtab, ranges, reloc consistency, data_in_code) | removes L3a's code-range REMOVE record |
| 2.5 | L3a | synchronous x86 exceptions emitted at their instruction; the masked-exceptions fact over every fldcw/ldmxcsr/fldenv/frstor/fxrstor site | decision "Hardware exceptions" |
| 2.6 | L3b | certify the pack's shape set: the remaining int shapes on farms; certified builders for x87, SIMD and sys; w4 cpuid and DEF0 in the prover | pack forms ⊆ certified (2,370 / 19,494 int at 2026-11-12) |
| 2.7 | L2 | plugin census over the relift pack (global finding count); review of the dispatch/defdisp plugin for the bridges; top-level asm bracketed as recorded ranges | 0 findings |
| 2.8 | L3b | lift test doubles 9/35 → 0, with L3a w6; delete the Unicorn harnesses once replaced | test ∩ product symbols = ∅ |

**Step 3: the ELF.**

| # | Owner | Item | Rule / done when |
|---|---|---|---|
| 3.1 | L1 | w12 link/guest.json; w15 link root and onedef; w15c per-target recipes; w16c generator inputs and the header-root rule | one recipe per target |
| 3.2 | L1 | w16: the IAT root (`fnvnx_import_key`) | closes the render undefined names |
| 3.3 | L2 | w2 .fnvnx.tls (L1 tlsmodel); w3 extents. Open sites: GetProcAddress 0 of 135 and LoadLibrary 2 of 36 (exe 0xda284a, d3dx9_38 0x56b717), at facts a0f3d8ea / manifest e15d385f, measured 2026-11-14. Rule: a dominating index compare bounds the indexed store | reach certificates count once every dynamic site is resolved |
| 3.4 | L3a | the product ELF root, with a worker; bridges → `fnvnx_dispatch`; exitgen | undefined closure = ∅ |
| 3.5 | L5 | rt/guest: `main` process entry; pthread mutexes over real threads (libgcc unwind) | 3 native test roots link |
| 3.6 | L6 | w1b translator root; w6 render/d3d9 root (after 3.1 w16c); doubles 26/88 and the 22 ignore-all link commands → 0; `render/ASSUMPTIONS.md` into the registry | stub/doubles gates 0 |
| 3.7 | L6A | w6 gl4 root (after w15c, with lib/sha256 57051038, eb482552, b83b7309) | recipe-built libnxgl4 |
| 3.8 | L5 | tests onto the recipe with 0 doubles (5 files/87 symbols); delete s4/s7 Makefiles and build_guest.sh | one build |

**Step 4: boot → exe entry → WinMain → menu → first frame (GL in the emulator).**

| # | Owner | Item | Rule / done when |
|---|---|---|---|
| 4.1 | L4 | w6: bootguest on rt/guest with link/guest.json; its images stop defining the product's fnvnx_host | no doubles |
| 4.2 | L4 | boot the product ELF. Milestones: exe entry, WinMain, first frame (the GL backend's first present); instructions per frame per thread; every frame dumped as an image. The menu milestone gets an input-scenario rule after the first frame, because N1 certifies identity, not role (decision 2026-11-14) | observable in the trace |
| 4.3 | L4+L6A | the boot machine's GL from the gl4 root (not the prebuilt @2f0427f5); confirm the Mesa 25.2.8 pin for L6 | one GL |
| 4.4 | L5 | registry and FalloutPrefs generated stages, declared fonts, Galaxy bodies, Winsock network down, LoadLibrary 126 | declared machine |
| 4.5 | L3b+L3a | SEH continue restores the whole CONTEXT (A-CTX); EH/SEH conformance against the exe's tables and Wine 9.0 | decision "SEH continue" |
| 4.6 | L6+L6A | sm_link over every shipped shader = 0 failures (179 fail + 10 refused at 2026-11-12); the link-failure branch → reach_violation | decision "Shaders" |

**Before the Switch step:** 5.1, L5: the Switch host (toolchain/host: host_vm.inc, host_platform.inc and the rest, now
only on the integration branch) becomes a recipe-built product root. hostapi_total judges every service against the
admitted host roots: L4's boothost now, the Switch host from then on.

**After the first frame:** deko3d harnesses and uam, vtnames/decomp, native CRT, toolchain mirror reproducibility,
IKOS. Then speed toward 45 fps (floor) / 60 fps (target), then the Switch.

## Blocker of goal step 1 (the first product root)

**Critical chain (03:52Z):**
1. L1 w5's toolchain-build env.json profile, which had never landed and is now top priority;
2. L5 w14 builds tools/fnvnx-a64-msvcmodel13, the guest runtime with gcc 13.3's libstdc++ and a MANIFEST (2 farm runs, about 1 h);
3. the platform root (w10 3bfa841, red at ini.cpp: gcc 15 libstdc++ headers do not parse under gcc 13) turns green;
4. MERGER.

Rule: CLAUDE.md trunk 6481968. A second red, the L2 `pi` N3 check missing constexpr template instances, is with L2.

Required gates still missing on the trunk, with the MERGER queue in order:

| Gate(s) | Head at MERGER | State |
|---|---|---|
| headers class (L1 w16) | de8ed638 | green |
| gates.d + class `test` (L1 w10) | 1638c742, 29 gates | green; merged as one unit with L3b w2g + the ASM registry |
| exepin (L1 w4) | 03c699d0 | green |
| order, eh, addr32 + lock/fsw sites (L3a w6) | 0be860f47 | green, 262 s |
| r7, namegate, abi (L2 w5) | rebasing | conflict in gate_contract.py |
| forms, fpexact (L3b w2g) | merge unit w3a e7efb1fc (w10 + registry + fpexact f2i + L3a citations) | gate_assumptions 95/100 mentions cited (5 red: L3b 1, L3a 3, L1 1); waits on L1 w5 env fix, merges after w14 c0f23a3f |
| L2 heads | w4-clean b227059, w2 d19ba5a7, w3-rules 1d6d803f, w6-dyninit c9bdb4e8 | green on 8cfbcdbf |

The first product root is L5 w10's platform root (6516d0d is red: 58 of 74 units are green up to the first red, a missing loader/toolchain header that is not on the trunk; fix: L1 w16 nested headers, toolchain/include and loader/include admitted as headers, w15 importkey/reach onto contract/include; its verdict took 471.7 s) on `claude/fnv-l5-platform-trunk`, cut from 8cfbcdbf. It merges together with L2 w6's shim/include `headers` entry in one verdict.

## Per layer

| layer | lead | path item now | global numbers (commit) |
|---|---|---|---|
| L1 | session_01KmtZKUn5UJpiHYPtNRT6p9 | MERGER queue; w15 link + onedef; w17 flags.semantics; w19 warm speed | trunk 28 gates, 0 roots (8cfbcdbf); LoadLibrary 20/23 closed, dynamic GPA 12 open |
| L2 | session_01KmtZKRi5XRMhd5qzRaEyxC | 4 green heads at MERGER; w5 rebase; all 8 plugins load for C and C++ | CRT name bindings 0 (84fb9bc7); shim/include 517 names, 2 duplicate declarations being removed |
| L3a | session_01KmtZKSANKhYEe4nVesF6Ho | lift-gates at MERGER; pack as product root; dispatch (w8, 26/26) | flags gate 16,787 red reads (5b308371, being recounted on the 6-image set and split by cause); unent 0 on all 6 images |
| L3b | session_01KmtZKSQpF5Su9oG96Kbe4A | w2g + registry merge unit; forms queue 19,494 int shapes (~60 CPU-h) after the Sail hunks | `ASSUMPTIONS.json` 77 entries: L3b 11, L3a 17, L2 1 live (326f2c8e); mirror shard 39/40 proved |
| L4 | session_01KmtZKUcrakBqKb1mC8iXp4 | item 11 guest test image (w6, on L1 w16); item 14 lib/sha256 intake | lib/sha256: 233 NIST vectors, 0 mismatches (782e8872) |
| L5 | session_01KmtZKTGM2tLun8zgoE6X44 | platform root to the trunk (w10); crt0 split via L1 w20 c7f3b9d2; doubles -> 0 | s4 485 tests / 404,818 checks / 0 failed, s7 89 / 3,412 / 0; doubles 6 files / 88 symbols (3950cd01); registry sites 20/20 static; file sites 396 (75 static) |
| L6 | session_01KmtZKRq7TGNHj6H8m8jpAZ | sha256 shader keys (8 sites); b# census + link-failed reference farm | env dumps reproducible, 4/4 byte-equal runs per profile (tools/envprobe-bin d2b6916c); fmadd in C 7 -> 0 |
| L6A | session_01KmtZKSgdFAv1Sd3kGuQtHk | A2 dk side done (16893be), gl4 waits for lib/sha256; A3 link-failed forms | dk 19/19 units green, deterministic, 86 externals |

## Decisions since 02:40Z

- **FalloutPrefs.ini, revised:** the minimal-keys rule replaces option A, because the launcher is not in gamedata. CLAUDE.md trunk 29b4193, integration 33f97d7.
- **Sail model patches:** a hunk stays under OWNER 9 only if it is derived from the pinned ACL2 x86isa (function plus commit cited), with the new pin recorded. A form ACL2 does not model is never hand-written: it is counted by census, and the closure rule decides whether it is reachable.
- **ASM-043/045:** removed by the replacing fact (Form-writes), not given records. Id ranges are fixed per layer:

  | Layer | Range |
  |---|---|
  | L1 | 1xx |
  | L3a | 2xx |
  | L3b | 3xx |
  | L4 | 4xx |
  | L5 | 5xx |
  | L6 | 6xx |
  | L6A | 7xx |
  | L2 | 8xx |

  The gate is red on an id defined twice or outside its owner's range.
- **difffuzz deleted** from the integration branch with all its consumers (b84a2c0), because selection from test results is forbidden.
- **Plugins:** every guest CC stage loads the full plugin set, C and C++ alike, with a per-unit key clause.
- **flags.semantics:** one set for all languages, including -ffp-contract=off and -fsigned-char (the MSVC char model).
  - -fno-exceptions is decided by a static call-graph fact. Until that fact exists, exceptions and unwind tables stay on, in lift/pack and lift/rt as well.
- **onedef gate:**
  - strong or weak symbols are defined in exactly one root;
  - a COMDAT comes from one source definition with equal bytes (the ODR);
  - source provenance: a unit belongs to one root, and a header's content appears under one path.
  - Rejected: equal-bytes-as-copy, which is a heuristic.
- **lib/sha256:** a shared product root (guest + host) owned by L4. All content keys are sha256 (OWNER 2). L6 shader keys move from 64-bit FNV-1a to 32 bytes, an ABI pair change with L6A.
- **flags gate (L3a):** on the trunk, scoped to admitted roots (0/0 today), and REQUIRED for class `lifted`. Its universe is the 6-image set.
- **b# as an ALU source:**
  - reachability first, by a census of every shader bytecode in gamedata and at run time;
  - otherwise the Wine 9.0 / GL 4.6 §7.3 behaviour: a link-failed program keeps the previous one bound;
  - the link-failed set is generated by a farm over all 7,553 sm_table rows.
- **x87/SSE FP exceptions (L3a item 29):** first, a static fact that every control-word write keeps the masks set and that every sticky-flag read is listed. Full trap semantics are written only for what that fact leaves open.
- **Perf:** the metric is verify/icount.py. The A57 cycle pricing and the hand estimates are not ported. IPC is a REMOVE record (measured on the console), after the first frame.
- **Test inputs:** every reference a test reads is a declared external (commit + tree_sha256). Old failures are never accepted as a baseline.
- **Integration vs trunk:** they do not merge wholesale. Trunk-bound work is cut from the trunk, and the old line retires once the roots are on the trunk.
- **env dumps:** re-recorded now on a farm, because a generator input that cannot be reproduced is a determinism defect.

- **Plugins per front end (L1 94b4c823, measured by w17):** cc1 cannot load layout, sdd, pi or names (a static ELF fact), so guest C units lack those checks. This is an open class owned by L2, and L1 reports the global count. -fPIC stays (D16, D9). EH stays on (22 native throw sites).

- **s4 laundering (L3a item 22):** a code address laundered through memory after arithmetic.
  - Rule: a whole-program points-to over abstract objects. Static words are exact per relocated word (R3); the stack is per frame; the heap is per allocation site.
  - The work is scheduled after the first boot. Until then no_s4_arith stays OPEN and the certificates stay measurement-only.
  - Escapes at 63cabce1: binkw32 1,318, libvorbis 970, libvorbisfile 782, atimgpud 512. Values reaching a jump target: 0. exe and d3dx9_38 are still being counted.
  - There is one heap-object producer: L1 w18's allocation-site fact file, which also serves d3dx9_38 site (iii). L3a consumes it.
- **FalloutPrefs inputs:** render/d3d9 becomes a product root (L6). Its `generated` stage declares ident_handheld.bin and ident_docked.bin as `outputs`. L5 w13 consumes them through `uses`, and declared_files.cpp stays off the platform root until then.
- **MERGER order (L1):** w16 -> w10 6bcd7d2 (29/29) -> w14 c0f23a3f -> w15 afa6b301 (27/27, 291 s).

- **d3dx9_31.dll: decided (A).** It is in gamedata/redist c53729e (sha256 e2065619…78a5, from the OCT2006 cab); d3dcompiler_* is absent from all 16 gamedata heads.
  - The declared machine has the DirectX redist installed: every gamedata/redist DLL is in the declared system32 (CLAUDE.md trunk b6a8e5d / integration 408680f).
  - The image set goes 6 -> 7 (may-load closure, the sound superset). L1 pins it, L3a lifts it, and L5's modules.py takes redist as a class.
  - "Join only if reachable" was rejected, because reachability is unproven. Site 0x56b708 stays open.
- **PIE (L6A):** a target family that a root states (pie/pic) is exempt from forced distro-default neutralisation, and the hardening flags stay forced. deko3d stays PIE.
- **Forms certificates:** certs.tsv has 0 rows, so every lifted root is red on forms. L3b w4's queue (19,494 shapes, about 60 CPU-h) takes the first farm priority, on the path to goal step 2.
- **Forms farms started (03:45Z):** 16 Sonnet farms l3bcert-01..16, owned by L3b, one shard of the 19,494 shapes each (about 3.75 CPU-h). Their START lines follow w4's 373-shape IR validation.
  - Ids:
    - 01 session_01KmtZKSnvm9jrrTYaoptCJW
    - 02 ..SefBeDTfGWugKwboj
    - 03 ..SegBBQ8CvZMubkzSw
    - 04 ..V2SuYrJqpG7qJyPdw
    - 05 ..UBY5ZEEEGao5bC2UJ
    - 06 ..VHAgiqQieeiENKXvQ
    - 07 ..R7AKACexBq9odtH7c
    - 08 ..SrJuinKTE4BAjcDcK
    - 09 ..U9ey85VJzq7pnYnAd
    - 10 ..SeBec48FmqpwVciZQ
    - 11 ..S5baoGoYcGwQpmLWy
    - 12 ..VG4DH1gd66hzmPPfk
    - 13 ..V8hCV5PMeMNRYmZ3r
    - 14 ..S5ugow1y3T2otBX2w
    - 15 ..Qfis7dLbMYPoj7apH
    - 16 ..TZF2T3GmwY4rxDuy2
- **REQUIRED_PRODUCT on the trunk:** 9 of 33 gates are missing at 8cfbcdbf, all queued at MERGER:
  - order, eh, addr32 (L3a lift-gates);
  - forms, fpexact (L3b w3a);
  - r7, namegate, abi (L2 w5);
  - exepin (L1 w4).
- **L3a lifter tree on the trunk:** lift/tools, lift/proof, fnvlift and rt go in as one unit after lift-gates and before the pack root. The pack cold build runs on L1 w14 2d8176ae with no EMLINK, about 8,217 stage keys.
- **lib/sha256:** it goes in as L4's own root (w7 20724246) in the same verdict as L6 w6's render/d3d9 root (session_01KmtZKQbpDKvc9qzXT9bLHX).

## Sessions (03:36Z)

- All 8 leads and MERGER are active (each messaged within the last 10 min). Nothing was stopped by limits this hour.
- L1 w21 (legal gate) has been PENDING since 03:01Z without starting. L1 was told.
- L1 w13 setup-from-scratch: a fresh container fails at install_debs (25 dpkg errors). L1 was asked for the commit of the first fresh-container green.
- `pgrep -f autoadopt` is empty.
