### J-XXXX | 2026-11-07 | stream: R4 (claude/fnv-recon4) -> D25 #11 NRO checklist as checks | status: wip | parent: recon4-009

* **did**
  - **tools/report/nro_check.py**: docs/NRO_CHECKLIST.md as checks. It runs on the nro/<commit12> tree that
    publish_nro pushes, or on a full build directory. It writes fnvnx-nrocheck/1.
  - Every item a tool can verify is automated. Each row has a status (pass, fail or open) and an owner (NX1 for all
    automated rows). The rows:
    - **nro.format, nro.nacp, nro.icon**: tests/nx1/check_nro.py, imported and its rows split per item. Two more
      NACP checks: the title is "fnv-nx", as step 5 of the checklist says, and the display version is the commit12.
    - **nro.romfs**: the asset RomFS parses (romfs.py) and holds shaders.nxsp. Nothing else is allowed in it: no
      GLSL, no .esm, .bsa, .exe or .sdp, no translated.nxsp.
    - **exefs**: tests/s5/check_package.py checks the PFS0, the NPDM and both NSOs.
    - **sd.layout**: tests/nx1/check_fs.py --sd (add --s4 for its runtime cases). Also, every file on the card must be
      in sdlayout.h's FNVNX_SD_PORT_NAMES under switch/fnv/, and nothing may sit outside switch/fnv/.
    - **paths.policy** (data and save path): every `sdmc:/` literal in the three shipped images (fnv.nro, the exefs
      main, guest.nso, decompressed) is inside FNVNX_SD_DIR or Atmosphère's contents folder. Format templates like
      `sdmc:/%s` are listed. No `save:/` mount, so saves and INIs stay under user/.
    - **memory.budget**, from the packaged main.npdm (not the repository's JSON):
      - 32BitNoReserved, a 2 GiB heap region, inside the ~3.2 GiB pool;
      - FNVNX_MIN_APP_MEMORY lies between the heap region and the pool;
      - host heap (largest backend) + host NSO span + guest.nso's FNXG size + S10's window <= half the region.
    - **memory.peak**: check_mem.py. It needs the guest ELF and the boot summary, which the build directory has and
      the nro branch lacks. Without them the row is open.
    - **threads.plan**, from the packaged NPDM: cores 0..3, default 0, priorities inside 0x1C..0x3B, the main thread's
      inside them, a page-multiple stack. Plus check_threads.py --host-elf.
    - **imports**:
      - no svc word in guest.nso's text;
      - check_svc.py on the host and launcher ELFs, plus the guest ELF when given;
      - DT_NEEDED is checked in nro.format.
    - **size.limits**:
      - each file below FAT32's 4 GiB - 1;
      - fnv.nro below 64 MiB, my choice: hbmenu reads it whole;
      - the guest image inside the 1 GiB small map.
    - **strip.release**:
      - no ELF on the card;
      - no .debug_*/.symtab/.strtab section names in the loaded images;
      - no allocated .debug or .symtab section in the ELF sidecars, which keep their symbols for crash.log.
      - There is no separate release profile: the farm's package is the release.
    - **manifest**: MANIFEST.txt matches sd/ in sizes and sha256. Checked on the nro tree only; a build directory has
      no manifest before publish.
    - **provenance**: the NACP version is the package commit, from build.json, stage.json or --commit.
    - **checklist.page**: check_checklist.py.
  - **Manual items**, 11 rows: console and firmware, SD card, game files, the owner's translated.nxsp, title
    takeover, launcher lines, restart via A, display, exit to HOME, crash and Y recovery, send-back. Owner: the owner
    (hardware); restart is shared with NX1, display with render.
  - **Metric**: automated rows failing or open. Manual rows are not counted. With no package the metric is null and
    the exit code is 3.
  - **zero_targets.py #11**: reads `<h>-nrocheck.json`, or the same object under `nrocheck` in `<h>-full.json`.
  - **The full_job hook is a snippet, sent to T1.** full_job is not my file.
    - It goes in as step 6b, after publish_nro.
    - It runs nro_check.py on `$F/build` with `--commit "$COMMIT"`, so a package left by an older commit fails
      provenance.
    - It appends the table to the report and writes `nrocheck` into `${REPORT%.md}.json`. full_job writes no
      sidecar today, so this creates the one the runner publishes.
* **measured** - Metric: not measured on a real package. There is no build or nro/<c12> checkout here. This commit
  is a prerequisite for D25 #11. On the synthetic package: 2 of 16 automated rows not passing:
  - threads.plan fails, correctly: the fixture's host ELF has no symbols;
  - memory.peak is open.
* **tests**
  - tests/s15/test_nro_check.py (new): 35 ok, 0 failed.
    - The good synthetic package: tests/nx1/fixture packed as fnv.nro with a RomFS, the exefs from fnvnx-app.json,
      an FNXG guest.nso, MANIFEST.txt and build.json. Twelve items pass, memory.peak is open, the manual rows have
      owners, and metric = fail + open.
    - One mutation per item, each failing its row:
      - NACP name, NACP version, version vs commit;
      - a game file in the RomFS, no shader pack;
      - address space, cores, priorities;
      - an svc in the guest;
      - a path outside the layout, a save:/ mount;
      - DWARF in an image, an ELF on the card;
      - a stray file under switch/fnv, a file outside it.
    - Manifest: a changed file fails, a missing manifest is open.
    - No package: exit 3, metric null.
  - test_zero_targets: 0 failed, with a new #11 case (embedded in full.json at an older head, and the delta).
  - The snippet, run against a synthetic build directory: it appends the table and writes the sidecar's `nrocheck`.
* **next** - T1 or the full_job owner adds step 6b. The first full run gives the real #11 number.
