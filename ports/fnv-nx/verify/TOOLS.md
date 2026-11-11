# Stated rules of the tools

The rule each stage, gate and farm tool implements, one section per file. A test of a tool names its section here as its
reference (`tests/oracle.json`, gate `oracle`): the expected values of the test follow from this text, not from the tool's
code. This file is the reference; the doc comment at the top of each tool repeats its section. When a rule changes, change
it here first, then the tool and its test.

## contract/bind.py

```text
bind - the exe addresses of what L2's sections name (L1 CONTRACT; uses l2sections.py rows and the N1 certificate).

    bind.py --obj obj.json --n1 n1.json --units units.json --out bind.json      (prints counts only)

Pure functions; `unit_index` is the index of the object's unit in units.json (n1.py's unit number).
  symbol_addresses(unit_index, sympair_rows, n1) -> ({symbol: address}, [(symbol, kind, reason)])
      address = n1['sym'][(unit_index, msvc name)], as pinned (an absolute-value pin keeps its ABSOLUTE flag bit).
      kinds thunk, vtable, gcc, internal have no exe twin by definition: unbound, reason as in NO_TWIN, not an error.
      Other rows without an address: unbound with the kind's own `unnamed:*` text, 'no MSVC name', or 'not in N1'.
      One symbol with two different addresses or names is a ValueError.
  vtable_addresses(unit_index, vtpair_rows, n1) -> ({(_ZTV symbol, address point): vftable address}, [(ztv, point, reason)])
      through n1['vt'][(unit_index, ??_7 name)]; an empty or unnamed:* name, or a name N1 never pinned, is unbound.
  n3_verdicts(unit_index, n3_rows, symbol_addresses) -> {exe address: (verdict, [inlined exe addresses])}
      the shape bodysel.decide takes as `n3`.  A row whose symbol has no exe address is not an exe function: ignored.
      An ok / ok+E row with an inlined symbol that has no exe address keeps its verdict only when every n3 row of that
      symbol has kind `inline` and verdict ok (that body is part of this one: nothing to list); otherwise the verdict
      is 'inlined body without a verdict'.  Rows of one address (clones, aliases) combine: any non-ok verdict wins
      (the first in sorted order of the reasons), `ok+E` only when all rows say ok+E; inlined lists are unioned, sorted.
The CLI binds every object of obj.json that has section rows (unit = the unit whose source.replace('/', '_') + '.o' is
the object's basename; such an object with no unit is an error; objects without rows, e.g. the link's zz_* helpers, need none) and writes store kind `bind`.
```

## contract/bodysel.py

```text
bodysel - the static body selector (MASTER_PLAN4 R1): NATIVE(f) iff N1 and N2 and N3 and N4, else LIFTED.

    bodysel.py --universe universe.tsv --n1 n1.json --gates <dir with bodysel.tsv of registry.py> [--n3 n3.tsv] --out bodysel.tsv

Inputs are static facts only: the universe of code entries (L3: every entry of the exe, including funclets,
catch blocks, tail targets and the PE entry point), the N1 certificate, the object gates of N2 and L2's compiler
plugin verdict for N3 (address <tab> ok|ok+E|reason <tab> inlined callee addresses...; `ok+E`: the body is a
`__sdd` that implements the vector branch, flags & 2, of `??_E`).  No fuzz, boot or scenario
output is read: the argument list is the whole key of this stage (R13).
  N1  byte identity with the exe under one symbol map            (n1.py)
  N2  object gates on every site: data, call, vcall               (registry.py rows; a row = not N2)
  N3  GIMPLE inside the portable subset                           (L2 plugin; no verdict = not N3)
  N4  closure: everything inlined into f is NATIVE                (greatest fixed point over N3's inline lists)
A native body that references data or a symbol with no exe address cannot pass N1/N2 (its relocations are not
the exe's), so it is LIFTED here; a lifted body refers to exe addresses only by construction.  The link
therefore has no "unresolved" decision of its own: an unresolved reference there is an assert,
"N1/certificate violated: tool defect", expected 0 times.
The selector is total: every code entry of the universe gets NATIVE with its certificate, or LIFTED with
the first reason.  Incompleteness costs speed, never correctness.
Gate `bodysel` ($GATE_OUT/bodysel.json, gatefile.py): universe = the code addresses of the fact base, certified = those
with a verdict (totality).  The result is a function of the argument files only; rows are written sorted.
```

## contract/gate_call.py

```text
gate_call - direct call/tail targets of the exe function versus each native copy (L1 CONTRACT).

Native targets are `calls` plus any 8-hex-digit address inside an `other` symbol name that the exe function also
calls (bridges).  Several native copies of one address: each is judged, the worst verdict stands.
```

## contract/gate_data.py

```text
gate_data - .data references of the exe function versus each native copy (L1 CONTRACT).

Addresses are canonicalised to the start of the containing variable (sorted __fnv_XXXXXXXX of out/aliases.ld).
The /GS cookie (`___security_cookie`, its address from the N1 symbol map: facts['cookie']) is the compiler's own
check, not a reference of the source; it is dropped from both sides.
Several native copies of one address: each is judged, the worst verdict stands.
```

## contract/gate_vcall.py

```text
gate_vcall - virtual-call displacements of the exe function versus each native copy (L1 CONTRACT).

Exe side: icalls of kind 'mem' (disp) and kind 'reg' with a known displacement; functions without any are skipped.
Both sides are sorted; equal lengths are compared pairwise.  Several native copies: worst verdict stands.
```

## contract/gate_xc.py

```text
gate_xc - does every C++ static initializer (facts['xc']) have an address-bound native function? (L1 CONTRACT)
```

## contract/gatefile.py

```text
gatefile - the result file of a gate, in the shape the L4 merge verdict reads (verify/verdict.py, `fnvnx-gate/1`).

    write(name, universe, certified, uncertified_ids, detail)  ->  $GATE_OUT/<name>.json, or nothing if GATE_OUT is unset

    {"certified":C,"detail":"...","format":"fnvnx-gate/1","name":N,"status":"green"|"red","uncertified":[<=50 ids],"universe":U}

status is green exactly when certified == universe (completeness of certificates, not a run counter); `uncertified`
is the first 50 of the sorted ids (strings), so the file is a function of its arguments.  Ids of game data are written
to $GATE_OUT only, never committed.  The only environment variable read is the declared GATE_OUT.
```

## contract/l2sections.py

```text
l2sections - the three non-loaded ELF sections that L2's compiler plugins put into every game object (L1 CONTRACT).

    parse_sympair(data, where) -> [(symbol, msvc_name, kind, 'def'|'ref')]            .fnvnx.sympair (plugin NAMES1)
    parse_vtpair(data, where)  -> [(ztv symbol, address point, ??_7 name, vfptr offset)]   .fnvnx.vtpair (NAMES1)
    parse_n3(data, where)      -> [(symbol, verdict, (inlined symbols), kind, (types))]   .fnvnx.n3 (plugin pi, sdd)
    read(path)                 -> {'sympair': [...], 'vtpair': [...], 'n3': [...]}; an absent section gives []

Format: plain text, LF-terminated lines, TAB-separated.  sympair: exactly 4 fields, kind one of KINDS or
`unnamed:<reason>` (reason not empty); the name may be empty only for the kinds in NAMELESS.  vtpair: exactly 4
fields, offsets decimal.  n3: 2..5 fields (the producer omits trailing empty ones), verdict not empty, inlined a
`,`-separated list of symbols, kind one of N3_KINDS or a clone suffix (not empty, no blank), types `,`-separated
Itanium type strings (the producer's own separator; the task brief said `;`).  Any other line is an error
`<where>: line N: <what>`; no line is skipped, a final line without LF is an error, an empty section is [].
Pure: bytes in, tuples out (rows keep the section's order).
```

## contract/objfacts_merge.py

```text
objfacts_merge - union of the per-object files of objfacts_one.py into obj.json (merge stage of objfacts).

    objfacts_merge.py --inputs <dir of *.obj.json> --out obj.json

Reads the files in sorted file-name order and unions their entries; the result is byte-identical to objfacts.py's
output for the same objects (store kind `obj` is canonical: no order of the inputs reaches the bytes).  A key present
in two inputs is an error: exit status 1, the key named.  Prints the same summary line as objfacts.py.
```

## contract/proto.py

```text
proto - the prototype of the native side of every boundary (MASTER_PLAN4 R5).

    proto.py --n1 n1.json --out proto.tsv

The prototype of a function is read from the MSVC decorated name of the symbol whose bytes equal the exe (N1):
convention, stack words, varargs, struct return, return class and argument classes.  Nothing is inferred from the exe body (no arity arithmetic,
no register-use analysis, no `ret N` guess).  A function that is not N1, or whose declaration this decoder does
not fully decode, has no native prototype: it is LIFTED and reached through its guest entry (R5).

proto.tsv (decomp-derived: container and fnv-nx-build only):  address, kind, cc, stack words, varargs, sret, ret, args
(ret: n v i q f d m?; args: comma list of i q f d, suffixed @ecx / @edx for fastcall register args, '-' when empty)
```

## contract/store.py

```text
store - the one on-disk encoding of every contract stage output: canonical JSON (no pickle).

    save(path, kind, obj)     write  {"data":<encoded obj>,"format":"fnvnx-store/1","kind":kind}  + LF
    load(path, kind=None)     read it back; the in-memory object is exactly what the stage built (same Python types),
                              so every reader sees the shape its docstring documents.  `kind` is checked when given.

Bytes: json.dump(sort_keys=True, separators=(',', ':')), ensure_ascii, LF.  Equal objects give equal bytes (no set or
dict order, no process state reaches the file).  Encoding rules, closed over the whole file set:
  set                  -> sorted list (elements are ints or strs, never mixed in one set)
  tuple                -> list; the decoder of each kind restores the tuples at the places the kind documents
  dict with int keys   -> JSON object with decimal-string keys (json does this); the decoder applies int()
  dict with tuple keys -> key = the fields joined by TAB; ints in decimal (see KINDS); a TAB inside a text field is an
                          error (save refuses), so the split is unambiguous
Kinds (the explicit per-file encoding):
  facts  exefacts.py   fn/vft/imports keys: decimal address; F['ret'] tuple; the lists calls tails icalls itails stores
                       vrefs data codeptrs imports jt of F hold tuples; secs/ptrs/vft values tuples
  n1     n1.py         fn keys decimal address, F['relocs'] list of tuples;
                       sym keys "unit\tsymbol" (2-tuple) or "unit\tindex\tsymbol" (3-tuple), unit and index decimal;
                       vt keys "unit\tsymbol"
  obj    objfacts.py   keys "object basename\t%08x" (function) or "object basename\t<section kind>", the kind one of
                       SECTION_KINDS; function value (vt [tuples], data set, calls set, other set, vcall list), ztv value
                       {symbol: [tuples]}, sympair / vtpair / n3 values the row lists of l2sections.py (tuples; in n3 rows
                       the inlined and types fields are tuples too)
  bind   bind.py       sym keys "object\tsymbol" -> address; vt keys "object\t_ZTV symbol\toffset" -> address; n3 keys
                       "object\t%08x" -> [verdict, [addresses]]; unbound sym [(object, symbol, kind, reason)] and
                       vt [(object, _ZTV symbol, offset, reason)]
  objone objfacts_one.py  the same encoding as `obj`, for the entries of one object (one file per object)
  vtid   vtid.py       bind keys "unit\t_ZTV symbol\toffset" (offset decimal), errors [tuples]
  eh     ehfacts.py    funcinfo/stubs/owner/kind keys decimal address; unwind_at, catch_at sorted lists (sets in memory)
```

## farm/boot_job.sh

```text
boot_job.sh - L4 farm job: run the boot stage (verify/boot.py) on one real guest ELF and publish the result.

  [ARTIFACTS=...] [GAME_DIR=...] bash <checkout>/ports/fnv-nx/farm/boot_job.sh <elf_id> [--limit-instructions N] [--profile handheld|docked]
                               [--gpu-branch <artifacts branch>]

Steps:
 1 setup: what the stage needs (python3, git, the aarch64 binutils readelf/nm that icount.py spec runs, unicorn==2.1.4)
   is checked first; only what is missing is installed (apt-get, pip), and an install that fails fails the job
 2 branch l1-contract/elf/<elf_id> of the artifacts repository (the contract's own file formats; verify/bootinputs.py converts
   them, and fails on any row or line it does not understand): guest.elf, or manifest.json + part.NNN (joined in manifest
   order, every part's and the whole sha256 and size checked; a mismatch fails the job); entries.tsv (addr, symbol, kind
   code|import, `# table_id` trailer); modules.tsv (name sha256 size preferred_base assigned_base size_of_image ...; reserved
   ranges = assigned_base .. + size_of_image, plus the exe's own PE image range); keys.tsv (import keys; the key range is
   derived from the import rows of entries.tsv, and both table ids must agree); milestones.tsv (the contract's rows: exe_entry, module_entry:*, WinMain, import:*!Present) + the loader's
   console lines of run.txt (when present) -> the bootmiles table; optional softfloat.syms or softfloat.tsv (one symbol
   per line). Every missing required file is listed and fails the job
 3 icount.py spec: range lifted = section .text.fnvl, class softfloat from the softfloat file (left out when absent)
 4 SD root: a fresh empty card directory (--root); the installed game is mounted in place at the guest's sdmc:/switch/fnv
   (--game, verify/bootpath.py). The game is GAME_DIR, else fetched by farm/gamedata_fetch.sh into $FARM_HOME/game
   (the exe, INIs, static modules and d3dx9 DLLs; plus every Data item (gamedata/data/* of the artifacts repository: *.bsa, *.esm, Music,
   Shaders; every part's and every file's sha256 checked by the fetcher), or only the Data items named in BOOT_DATA_ITEMS; `--no-data`
   there means no Data item, a run without the game's archives). FalloutNV.exe must be in it; its sha256 is printed. The fetched
   Data items are recorded in meta.json (data_items; null with GAME_DIR, whose content the job does not know)
 5 verify/boot.py run, heap size 0x40000000. Exit 0 or 2 (the guest stopped with a fault, a deadlock, a missing
   module or a nonzero exit) is a RESULT; 1 is an input error and fails the job
 6 publish the orphan commit of OUT.json, OUT.console.txt, spec.json, meta.json to branch l4-verify/boot/<elf_id>
 7 final line: BOOT elf=<id> rc=<boot rc> reason=<r> last=<milestone|none> instr=<n> main=<n> threads=<n> seconds=<s> published=<branch>@<commit12>
--gpu-branch B: the renderer. Branch B of the artifacts repository must hold libnxgl4.so (the renderer's host library, GL 4.5 on
   Mesa llvmpipe). The job fetches it, installs what is missing of GPU_PKGS (default "libegl1 libgl1-mesa-dri libgles2 mesa-utils") and
   gcc (the callback shim of verify/bootgpu.py), checks that the library loads, and runs boot.py with --gpu LIB --frames DIR. The first 3
   frames (frame_*.rgba + .txt) are published under frames/ with the result; later ones are not. meta.json gains gpu_branch and
   gpu_branch_commit, the final line gains presents=<n>.
   The renderer pair rule (bootinputs.py pair), checked before the run; false = the job fails: the ELF branch's manifest.json names
   the bridge it was linked with (render: {branch, archive_sha256}); that branch's build.json archive_sha256 and its libfnvnx-d3d9.a
   bytes equal it; B's meta.json sha256 equals the libnxgl4.so bytes; bridge build.json nx_agent_commit == B's meta.json
   nx_agent_commit (bridge head == nxgl4 head). meta.json gains bridge_branch, bridge_branch_commit and render_head.
Stage table: env BOOT_STAGE_TABLE_BRANCH (default l4-verify/milestones), file stage.milestones (bootmiles format), appended after the
   contract's rows (bootinputs.py milestones --stage-table: names unique, every exe address a lifted code entry). A missing branch: the
   job runs with the contract rows only and the final line says stage_table=absent; a present branch without the file fails the job.
Env: GPU_PKGS (see above), ARTIFACTS (clone of the artifacts repository, default $FARM_HOME/artifacts; cloned --filter=blob:none --no-checkout
when absent), ARTIFACTS_URL (default https://github.com/toadharvard/fnv-nx-build), FARM_HOME (default $HOME/fnvnx-farm),
WORK (default $FARM_HOME/boot/<elf_id>), GAME_DIR (an installed game; never inside a git checkout). The profile is
passed to boot.py, recorded in meta.json and exported as FNVNX_PROFILE (a render host library reads it).
Exit 0 iff the result was published and boot.py exited 0 or 2; 1 otherwise (one-line reason on stderr).
```

## farm/setup_verdict_env.sh

```text
setup_verdict_env.sh - bring a fresh container of the base image (Ubuntu 24.04, root) to the environment DECLARED in
verify/env.json (fnvnx-env/1), the only environment in which an L1 verdict is computed.

  bash ports/fnv-nx/farm/setup_verdict_env.sh [--print]      (--print: only print the command lines, run nothing)

Installs exactly the Debian packages of env.json at exactly those versions (pkg=version) and exactly the python module
versions (pip --break-system-packages, as the other farm scripts), then runs `envcheck.py check` and exits with its status.
The lists are generated from env.json (no second list). A package version that the apt sources no longer carry makes the
script FAIL naming the package; another version is never installed: the fix is a deliberate new snapshot commit
(`envcheck.py snapshot -o verify/env.json --from-code` on a machine set up by this script) .
Env: ENV_JSON (default verify/env.json next to this script's tree).
```

## verify/a64access.cc

```text
a64access: per-word register read/write sets of AArch64 code, derived from LLVM MC (MCInstrDesc).
usage: a64access <file or - for stdin> [<start> <end>]...   (byte offsets, little-endian 32-bit words; no ranges = whole input)
output line: "<offset hex> <opcode> R:<ids> W:<ids> MEM:<base,index,disp,size,wb | - | pc,...> KIND:<kind> MAY:<L|S>"  or "<offset> UNDECODABLE"
Rule: operands [0,NumDefs) are defs, remaining register operands are uses, plus ImplicitDefs/ImplicitUses;
a read-modify-write def is listed again as a tied use operand by the opcode (TIED_TO), so it is read by the same rule.
Register ids are normalised through MCRegisterInfo super-register chains to the architectural X/SP/V/NZCV/FPCR/FPSR.
```

## verify/boot.py

```text
The boot stage CLI: runs the guest ELF under bootmachine.Machine, watches the milestone table, counts instructions.

  boot.py run --elf GUEST.elf --reserved modules.tsv --keys LO:HI --heap-size N --root SDROOT [--game DIR]
              --milestones TABLE --manifest entries.tsv [--icount-spec SPEC.json] [--limit-instructions N]
              [--hc-slice N] [--profile handheld|docked] --out OUT.json

plan = bootlayout (same rules as its CLI), watch points = bootmiles.resolve(table, manifest, ELF symbols, bias = image
base). OUT.json ("fnvnx-boot/1", sorted keys, indent 1, no wall time) holds the plan, the run record, the milestone state
and log, and the Icount report; the console text is OUT.console.txt. The tool only reports: which milestone must be
reached is the caller's verdict. Exit 0: run reason "exit" with code 0, or "limit"; 2: fault / deadlock /
ext-module-missing / scenario-error (a --text-entry text longer than the call's max_len) / nonzero exit / reach-violation (guest exit code 0x52000000|fact_id, reported with its fact_id); 1: usage or input error (message on stderr).

Host parts: the table's `host` target is an ID word, so the exact console line a part waits for is spelled
`console.<hex>` (hex of the line's UTF-8 bytes, no newline; helper console_part(line)). The part fires when the guest
writes exactly that line. Two more host events exist: `present` (every present of the renderer) and `gpu-first-call` (the first
guest call of ext module 10); they only fire with --gpu. Any other host name is an input error listing the known forms.

Renderer: --gpu LIB registers ext module 10 and forwards it to the host library LIB (bootgpu.py); --frames DIR is its
$NXGL4_FRAMES (the library writes frame_%06u.rgba/.txt there). The three GL variables for software rendering are set for it.
Without --gpu module 10 is not registered (a guest call answers NO_MODULE). The first 3 presents are icount marks `frame-<n>`;
OUT.json "gpu" = {library_sha256, presents, first_module10_call, last_error}.
```

## verify/bootgpu.py

```text
The renderer of the boot stage: ext module 10 (the guest's GPU calls) forwarded to the renderer's host library libnxgl4.so
(render/d3d9/backend/gl4.h: desktop GL 4.5 on Mesa llvmpipe, EGL surfaceless). Library: Gpu.

The library exports nxgl4_create(width, height, err, errlen), nxgl4_destroy, nxgl4_ext(fn, args, nargs) and nxgl4_last_error, and
leaves two symbols for the embedder: nxgl4_guest_ptr(addr, size) (guest range -> host pointer; NULL when the range is not wholly inside
one mapping readable and writable for the guest: Machine.host_ptr) and nxgl4_on_present(frame). The library does not ship them, so
this file builds a tiny C shim once per process (the two symbols forwarding to function pointers set through nxgl4_shim_set), loads
it RTLD_GLOBAL, then loads the library. The shim needs the host gcc. The environment of the library (LIBGL_ALWAYS_SOFTWARE,
GALLIUM_DRIVER, EGL_PLATFORM, NXGL4_FRAMES) is the caller's: boot.py sets it before Gpu() loads the library.
```

## verify/boothost.py

```text
The host services of the boot stage: the 50 h_<slot> methods of the FnvnxHostApi v3 table. Policy (address space, scheduling, ext modules) lives in bootmachine.Machine; these
methods do the argument decoding and the host-side effect. A blocking call returns (wait spec, result when resumed).

Files: guest paths go through bootpath.BootPath (sdmc: prefix, case-folded components, the game mounts; its rule is
stated in bootpath.py). BootPath(root) creates <root>/switch/fnv, as the device always has it.

Host slots whose effect is not modelled (the observable contract is kept; see HOST SLOTS in the stage report):
 output_debug_string (kept in .debug, no sink), console_flush, main_loop (always 1), buttons_down (always 0),
 thread_free, thread_set_priority / thread_set_cpu (recorded in .placement only, no placement effect),
 wall_time_us (fixed epoch + virtual ns), tz_offset_minutes (VC1's zone: UTC, 0), random64 (deterministic splitmix64, seed 0),
 memory_info (4 GiB / 3 GiB), cpu_count (3), audio_open / audio_submit / audio_close (frames counted, samples
 dropped, submit sleeps the period in virtual time), pad_read (never connected, zeros), pad_vibrate, input_poll
 (no events), module 11 CPU_BOOST (recorded in .boosts, returns 0: no platform config), MESSAGE (caption and text recorded in
 .messages, returns 0: the dialog is closed at once). The rest of module 11 (thread_activity, vm_*) is the machine's.
```

## verify/bootinputs.py

```text
Converters from the L1 contract branch files (l1-contract/elf/<id>) to what boot.py takes. Pure, stdlib only. Every
row or line a converter does not understand is an InputError carrying the file line number: nothing is skipped.

  join       --dir D -o guest.elf     manifest.json (fnvnx-elf/1: name, size, sha256, parts [[file, size, sha256]]) -> the parts
                                      concatenated in manifest order; each part's size and sha256 and the whole size and sha256 checked
                                      an optional `render` object is allowed (see pair)
  render-branch --manifest manifest.json
                                      prints `BRANCH SHA256`: manifest.json's `render` object {branch, archive_sha256} = the artifacts
                                      branch of the D3D9 bridge the ELF was linked with and the sha256 of its libfnvnx-d3d9.a
  pair       --manifest manifest.json --bridge DIR --gpu DIR
                                      the renderer pair rule (render_pair): the bridge linked into the ELF and libnxgl4.so are of one
                                      nx-agent commit, and every recorded sha256 equals the bytes; prints the common commit
  reserved   --modules modules.tsv --exe FalloutNV.exe -o reserved.tsv
                                      modules.tsv (header `# name sha256 size preferred_base assigned_base size_of_image has_tls
                                      entry_rva static load_index`; one row per module) -> `name lo hi` rows (lo = assigned_base,
                                      hi = lo + size_of_image) plus the row `exe` = the exe's own PE ImageBase .. + SizeOfImage,
                                      sorted by lo
  keys       --entries entries.tsv --keys keys.tsv
                                      prints LO:HI = the import-key range: lowest import key of entries.tsv .. highest + 0x10 (every
                                      key 16-byte aligned); every key of keys.tsv (`key module name ordinal conv ret args symbol`)
                                      must be an import key of entries.tsv and the two table ids must be equal
  milestones --table milestones.tsv --run run.txt [--entries entries.tsv] [--stage-table FILE] -o table.txt
                                      milestones.tsv + the loader's console lines of the contract's run.txt (every line that is exactly
                                      three spaces + `fnvnx-start: ...`, in file order, each distinct) -> a bootmiles table.
                                      Accepted grammar of milestones.tsv, the contract's own (link/milestones.py): TAB separated rows
                                      `NAME <TAB> KIND <TAB> VALUE <TAB> SOURCE [<TAB> EXTRA]`, VALUE = exactly 8 lowercase hex digits (no 0x),
                                      then the LAST line `# table_id H` (H = 64 hex digits, must equal entries.tsv's); every other comment, a
                                      blank line, a missing / repeated / not-last trailer and every other row is an error naming its line:
                                        exe_entry <TAB> exe_entry <TAB> ADDR <TAB> exe PE ImageBase+AddressOfEntryPoint
                                                                                       exactly once -> milestone exe-entry (call)
                                        module_entry:NAME <TAB> module_entry <TAB> ADDR <TAB> modules.tsv assigned_base+entry_rva <TAB> S
                                                                                       S = the `static` column of modules.tsv (column 9, the
                                                                                       10th counted from 0: 1 = in the exe's import closure,
                                                                                       0 = not); NAME unique, [A-Za-z0-9_.-]. S=1 ->
                                                                                       milestone module-entry-NAME (call); S=0 -> none.
                                        WinMain <TAB> winmain <TAB> ADDR <TAB> n1.json N1 function SYMBOL     at most once -> milestone
                                                                                       winmain (call). A stage-table milestone `winmain` then
                                                                                       is an error (the contract does not win silently).
                                        import:DLL!Present <TAB> import <TAB> KEY <TAB> keys.tsv import key   one or more (DLL unique) -> ONE
                                                                                       milestone first-present, group any, one `call` part
                                                                                       per KEY; every KEY must be an import row of entries.tsv
                                                                                       when --entries is given
                                      The old single-row file of the first ELF (`exe_entry_va <TAB> 0xADDR`) is NOT accepted: it is another grammar.
                                      Milestones come out in this order: start-01.. (console lines), then the rows above in FILE order
                                      (file order = boot order, as the ELF owner states it), then the stage table.
                                      --stage-table FILE: a table in the bootmiles format (verify/bootmiles.py), its milestones appended after
                                      all of those. Errors: a name used by both sources; an address-kind part (call/ret/retzero/rettrue) whose
                                      exe address is not a `code` row of --entries (the stage cannot observe a call of an address without a
                                      lifted entry; --entries is then required); a table that bootmiles rejects.
  stage-table --old milestones.txt -o stage.milestones
                                      the old S5 table (`<name> [all|seq|all:ID|seq:ID] <kind> <target> [main-thread]`, `#` comments, trailing
                                      `# ...` allowed) -> bootmiles format by RULE. kinds: call->call, ret->ret, callok->retzero,
                                      calltrue->rettrue (same semantics: the callee returns to its caller on the same thread and sp; w0==0 /
                                      low byte of w0 != 0). Plain `all`/`seq` get the group id `all`/`seq`. Dropped by class, listed in
                                      `# dropped:` lines: kind data and kind store (the old store targets and data steps are not the
                                      bootmiles host/store events: class `kind-data`, `kind-store`), target re:REGEX (class `regex-target`:
                                      names no exe address). A group (name, group id) that loses any part is dropped WHOLE (class
                                      `group-incomplete`): a group with fewer parts would be a weaker condition. A milestone left without
                                      rows is listed as `# dropped-milestone:`.
```

## verify/bootlayout.py

```text
Address-space plan of the emulator boot stage for the guest ELF (elf32-littleaarch64, ILP32, PIE).

Pure and stdlib-only: no unicorn, clock, environment or randomness; every iteration runs over sorted/ordered data.
The guest loader later maps the original Windows exe image (0x00400000..) and the module images through a host call, so
the stage must keep those ranges free. Library: parse_elf, normalize_reserved, plan, reloc_census. CLI:
`python3 -I verify/bootlayout.py plan -h`. A condition that cannot be met raises LayoutError (CLI: message, exit 1);
there is no alternative placement and no default.

Rules (each with its argument):
1. parse_elf: EI_CLASS=ELFCLASS32, EI_DATA=little, e_machine=EM_AARCH64 (183), e_type=ET_DYN and DT_FLAGS_1 with
   DF_1_PIE (0x08000000) in PT_DYNAMIC. Loading uses only program headers (PT_LOAD, PT_DYNAMIC, PT_TLS), never section
   headers, because the kernel/loader sees only those; the span [lo, hi) is the page-aligned (4 KiB) hull of all
   PT_LOADs, so everything the image occupies lies inside it. Any violation names the field and value.
2. reserved: (name, lo, hi) triples, from TSV `name<TAB>lo_hex<TAB>hi_hex` plus the import-key range (name "keys").
   Rows must be sorted by lo and pairwise disjoint (also after aligning outward to 4 KiB, so two ranges never share a
   page); names are unique and differ from the region names. Aligning outward only widens a range, hence the free
   claim stays sound.
3. plan: image base = the lowest 64 KiB-aligned address >= 0x05000000 whose [base+lo, base+hi) meets no reserved range
   and ends <= 0x3E000000 - n_stacks*stack_size. Argument: the scan only moves up, and moves exactly to the smallest
   64 KiB multiple that clears the overlapping range (any lower multiple still overlaps it), so no lower candidate
   is skipped; it terminates because the base grows each step and is bounded. Stacks end at 0x3E000000, host page
   (4 KiB) at 0x3F000000, heap at 0x40000000, end <= 2^32. Fixed regions are not moved: a clash is an error. Totality:
   `checks` lists every unordered pair of {regions + reserved} exactly once, each verified disjoint here, so the
   plan's disjointness is auditable without re-deriving it. `free` repeats the (aligned) reserved ranges.
4. reloc_census: the guest relocates itself at run time, so the stage applies no relocation; it only proves that the
   image is self-contained. Counts of dynamic relocations per type from DT_RELA/DT_RELASZ (Elf32_Rela), DT_REL and
   DT_JMPREL (decoded per DT_PLTREL) and the DT_RELR entry count (DT_RELRSZ / DT_RELRENT, default 4). Allowed: type
   R_AARCH64_P32_RELATIVE = 183 in DT_RELA (AAELF64 ILP32 relocations, binutils include/elf/aarch64.h, glibc elf.h)
   and any DT_RELR; anything else (other types, non-empty DT_REL / DT_JMPREL) is an error listing the counts.
   The census is part of the plan.
   crt0 header (parse_elf): at +8 of the first executable PT_LOAD: magic 0x47584E46, u32 abi, u32 link_base (+16) ==
   lowest PT_LOAD vaddr aligned down, u32 size (+20) == page-aligned span, u32 flags (+24) with bit0 (relocatable).
   Regions and reserved ranges all lie at or above 0x10000 (the guest's low pages stay unmapped).
5. CLI plan (--keys mandatory): deterministic JSON (sorted keys, indent 1, format "fnvnx-bootlayout/1").
```

## verify/bootmachine.py

```text
The machine of the emulator boot stage: runs the guest ELF (elf32-littleaarch64 ILP32 PIE, crt0 "FNXG", entry(api)
with the FnvnxHostApi v3 table) under Unicorn, deterministically. No fault-tolerating options exist.
Library: Machine, VirtualClock, MachineError, ExtMissing.

Rules:
1. Layout only from bootlayout.plan(): PT_LOADs at plan base + vaddr with ELF protection (bss = fresh zero pages), stacks /
   host page / heap = the plan's regions. No relocation: the guest relocates itself. A segment outside the image region
   or sharing a page with another raises MachineError.
2. Totality: all 50 slots resolve to services.h_<slot> at construction, else MachineError (the list is the header's struct, checked by the tests).
3. One CPU. A thread switches only at a blocking host call and at a host call's return after `hc_slice` calls of its
   slice while another thread is ready: no wall clock, no instruction counts, so switch points repeat. Nothing ready:
   the virtual tick jumps to the earliest deadline; none: reason "deadlock" with a per-thread wait report.
4. Any invalid access (unmapped or protection violation), Unicorn error, or host call on an unmapped guest pointer
   ends the run, reason "fault", record {kind, addr, size, pc, lr, tid}. There is no continue path, except the thread's own
   FNVNX_PLAT_ACCESS_FAULT_HANDLER (5c).
5. map_fixed(addr, size, src) follows svcMapMemory (hostapi.h): src is a page-aligned block of the heap region, wholly
   inside it and not given away before; addr gets RW memory holding the CONTENT of src, and [src, src+size) becomes
   unmapped for good (a later access ends the run with "fault"). addr: exactly there, wholly inside a plan["free"]
   range other than "keys", no overlap with src; a range holding any mapping (image, heap, commit, section view, ...) is refused with
   the libnx result LibnxError_AlreadyInitialized 0xF59 first (hostapi.h: "any occupied range"); other refusals return libnx kernel results (InvalidSize 0xCA01, InvalidAddress 0xCC01, InvalidCurrentMemory
   0xD401) and are recorded in run()["events"]. ext_call(11, 8 VM_PROTECT, {addr,size,prot}) re-protects any pages the
   guest owns (heap region minus given-away blocks, map_fixed mappings; bit0 R, bit1 W, 0 none), else a refusal likewise
   (image, host page, stacks, given-away blocks, unmapped: 0xD401). Other modules go to services.ext_<module>
   (fn, args) found at construction; an unregistered module, or a handler raising ExtMissing, ends the run with reason
   "ext-module-missing": a configuration error, never answered with a value.
5a. TOTAL against the header (hostapi.h TOTALITY TABLE, checked mechanically by tests/test_bootmachine.py against the parsed table): ext_call returns
   NO_MODULE (module >= 64 or not registered), NO_SERVICE (fn > UINT32_MAX, or a module-11 fn without a row: 0, 37 and up), BAD_NARGS
   (fewer arguments than the row, or nargs > 0 with a null array); nargs above UINT32_MAX counts as UINT32_MAX; only a handler raising
   ExtMissing ends the run. Module 11 serves fn 1..36 here. The stage has no network interface and a fixed UTC zone (VC1). Sockets 16..33 are bootsock.py's model (a host with no network interface; its docstring lists every
   served and every NOT answered (service, case); the latter end the run as "ext-module-missing" naming the service, with an event
   "sock-undetermined" saying why): the nargs rule (sockets dispatch before the general check: nargs != the exact count, null array or not, is -EINVAL;
   the exact count with a null array is BAD_NARGS). TZ_AT 34: UTC, offset 0, no DST, abbr "UTC",
   for every instant of the years 1970..2100 inclusive (0 <= utc_seconds < 4133980800; stated assumption of the stage), else -EOVERFLOW (-75), nothing written;
   tz_offset_minutes is the same zone's off_s / 60. THREAD_SELF 35: a record of the calling thread under a new id
   (never a second thread): started, so thread_start and thread_join answer BadInput; thread_free ends the record only.
   ACCESS_FAULT_HANDLER (10) is the machine's own (rule 5c); the console host serves it too (host_fault.inc).
   DISPLAY_DOCKED (9) answers 1 for the constructor argument profile="docked", 0 for "handheld" (default). Thread and directory ids are table
   handles (IdTable: (generation << 16) | slot, at most 1024 live together); the stack count (n_stacks) is a limit of this machine, not of the header.
5b. Platform service (module 11; always on). Totality at construction: every fn of PLATFORM_FNS
   resolves to services.plat_<name>(args) or the machine's own _plat_<name>(args), else MachineError (the machine serves
   thread_activity and vm_*; the services serve cpu_boost and message). A fn the header does not define is
   "ext-module-missing". VM service: reserve(hint, size) -> address or 0; size rounds up to 64 KiB, size 0 and every failure
   give 0. Without a hint the arena is the lowest 64 KiB multiple >= 0x10000 clear of the plan's regions, every plan["free"]
   range, map_fixed mappings and earlier reservations (one pass over the intervals sorted by lo: c only grows, and an
   interval that overlapped c is cleared by the move, an earlier one cannot overlap the new c because it ends below
   the old c + size or was cleared), end <= 4 GiB. A hint (rounded down to 64 KiB) is taken exactly or refused: it must not be 0, end
   above 4 GiB, or overlap anything listed above (the plan's free ranges are the module and key ranges: data, no fixed no-go). commit(addr, size): the page-rounded
   range must lie inside one reservation and size > 0 (else the libnx result LibnxError_BadInput 0x1759, checked FIRST; then a view page -> 0xD401); uncommitted pages become RW and zero, already committed pages keep their
   content and become RW. Each commit call maps its uncommitted gaps as extents (host_vm.inc). decommit follows host_vm.inc: an extent
   wholly inside the range is unmapped (an access ends the run with "fault"), a partly covered extent has the covered part zeroed and
   stays accessible RW. A reserved page never committed is unmapped (a reservation is address space only): an access faults. release(base): unmaps the reservation's committed pages and frees it (not a base: BadInput 0x1759). decommit has no size or reservation check (host_vm.inc): view page -> 0xD401 first, else the extents inside the range are handled, a range holding none is 0. Pages committed here are
   owned: VM_PROTECT works on them. map_fixed over a reservation is refused (0xD401). thread_activity(tid, paused): tid 0 (the calling thread) BadInput 0x1759; a paused thread is
   never picked; unknown tid NotFound 0x1359, a thread not started or ended BadInput 0x1759 (no kernel call, also for a THREAD_SELF record of such a thread); a thread (or the
   record of a thread) that is the CALLING thread: ResultBusy 0xF201, pause or resume; another live thread: pause of a paused one or resume of one that is not paused
   ResultInvalidState 0xFA01, else 0 (the two kernel codes are the header owner's statement from memory; assumption of the stage).
5e. UNMAP_FIXED (fn 15, addr, size, src): the undo of exactly one earlier map_fixed. addr or src unaligned 0xCC01; size 0 / unaligned 0xCA01; (addr, size,
   src) not exactly a live map_fixed mapping 0xD401 (so a second unmap refuses). Success: the range is unmapped (an access faults), the src heap block
   is accessible RW again with the content the mapping had, and addr may be mapped again. A page of the range that is not RW is the header's
   violated precondition: MachineError, which ends the run as a host-call fault (the contract defines no result).
5d. Sections (module 11 fn 11..14, host_section.inc; errors are -(code) as u64, recorded as events section_*-refused with the
   positive code). A section is one anonymous mmap (zero, page aligned) kept alive for the machine's life; each view is
   uc.mem_map_ptr of that buffer at the view's address, so views share bytes. MAP checks in the host's order: size, alignment, id,
   range, view table, then placement: addr != 0 must lie inside one VM reservation (else 0xDA01) with no committed extent and no
   view (else 0xD401); addr == 0 takes the lowest free 64 KiB multiple clear of plan regions, free ranges, mappings,
   reservations and views (not a VM reservation). Views are RW guest memory but not guest-owned pages: VM_PROTECT / COMMIT /
   DECOMMIT of a range holding a view page and VM_RELEASE of a reservation holding one are refused 0xD401 (nothing done);
   map_fixed cannot land on one (refused by its own range rules). FREE kills the id at once, the pages live until the last view is
   unmapped. The fault-handler frame may live in a view: a view is writable guest memory (rule 5c asks only that).
5d. TEXT_ENTRY (module 11 fn 36, args {max_len, init, init_n, out, n}; _plat_text_entry): the run's scenario `text_entries` (a list of
   strings, one consumed per successful call, in order). Checks as on the console: max_len 0 or > 500, out or n null -> -FNVNX_HE_BADINPUT, no
   text consumed. A text left -> 1, its UTF-16 units + NUL at out, *n = units; no text left (or no scenario) -> 0 (cancel, out and *n
   untouched). DIFFERENCE from the console, as the header says for the emulator: no human, so the answer comes from the scenario instead of
   always 0. A text of more than max_len units is a scenario error: the run ends, reason "scenario-error", extra["scenario"] = why (never
   truncated). A non-writable out / n on an answer of 1 is a host-call fault. Every call is an event {kind "text-entry", max_len, result}.
5c. ACCESS_FAULT_HANDLER (module 11 fn 10, args {handler, stack_top}, result 0; handler 0 unregisters; stack_top must be 16-byte
   aligned, >= 816 and < 4 GiB, else 0xCC01 and an event). Per calling thread. A data or instruction access fault (Unicorn's
   UC_HOOK_MEM_INVALID: unmapped or protection, read/write/fetch) of a thread with a handler, not already inside it, is
   delivered at once: the frame (FnvnxFaultFrame, 816 bytes) is written at stack_top - 816 from the exact register state at the
   faulting instruction (pc = that instruction; pstate = NZCV only, bits 31..28), then x0 = frame, sp = frame, lr = a return stub
   on the host page, pc = handler. reason is 0 (unmapped) or 1 (protection); 2 (alignment) is never produced: Unicorn raises no
   alignment fault through the hook (an alignment trap ends the run as "uc-error"), so the machine cannot tell it. When the
   handler returns to the stub: x0 == 0 loads x0..x30, sp, pc, NZCV, fpcr, fpsr, v0..v31 from the frame in guest memory and
   resumes; x0 != 0 ends the run, reason "exit", exit_code x0. A fault of a thread inside its handler ends the run: reason
   "fault", record with in_handler. A frame range that is not wholly guest memory the guest can write (not host page) ends the
   run, reason "fault", kind "frame-unwritable". Each delivery is an event {kind "fault-handler", tid, access, reason, addr, pc}.
   Faults of host calls on bad guest pointers ("host-access") are not thread faults and are not delivered.
6. Bound and count: one UC_HOOK_BLOCK over the image region (installed only with limit_instructions or attach_icount)
   adds size/4 per block, feeds the Icount if attached, and stops at the first block boundary where the total >=
   limit_instructions: reason "limit", the same stop point on every run (the stopping block is counted).
7. Observation only: on_thread, watch_pc, watch_ret (same thread, sp and return address as at entry), watch_store,
   on_console. An exit code of the form 0x52000000 | fact_id (exit_code >> 24 == 0x52, nothing above) is a violated build
   invariant, not an ordinary exit: reason "reach-violation" with "fact_id" (exit_code stays the raw value).
   run() -> {reason, exit_code, host_calls, switches, threads, virtual_ns, events, console, fault|waits|ext};
   "console" is the whole text the guest wrote (decoded with errors="replace").
```

## verify/bootmiles.py

```text
Boot milestones on exe addresses and exact symbols: table -> watch points, and progress as events arrive.

Pure: no emulator, clock or environment; stdlib only. The emulator driver installs the watch points of resolve() and
reports each hit to Progress.event(part_id, tid).

Table (text, one part per line; '#' lines and blank lines allowed; anything else that does not parse is a TableError
carrying the file line number):  <name> <group> <kind> <target> [main-thread]
  group  any        this line alone reaches the milestone
         all:<id>   every part of the id (within the name), in any order
         seq:<id>   the parts of the id in file order; a part counts only after the parts above it have counted
  kind   call 0xEXE     guest pc reaches the entry of the exe address (through the entry manifest)
         ret/retzero/rettrue 0xEXE   the function entered at the exe address returns (any / w0 == 0 / low byte of w0 != 0);
                                     the driver reports the return and decides the value kind from the part's kind
         sym NAME       pc reaches the exact ELF symbol NAME (native platform/render entries)
         store 0xADDR   a non-zero 32-bit word is stored at that guest data address (not biased: a data address)
         host NAME      a named host-side event string reported by the driver
Milestone order is the order of first appearance of the names. Part ids are "<name>#<k>", k counting the name's lines.
A duplicate identical part, a group id used with both all and seq under one name, or an empty table is a TableError.

Entry manifest: `addr_hex<TAB>symbol[<TAB>kind]` per row, kind `code` (default; an exe code address) or `import` (an
import key, never exe code); a duplicated address or symbol over both kinds, an unknown kind, or any other column count is
a ManifestError. The result is a Manifest: the dict of the code rows, plus .imports (the import rows, a separate map) and
.table_id (from the one optional `# table_id <64 hex>` comment line; None when absent). call/ret/sym targets resolve in the
code map first, then in the import map, so a milestone on an import key is possible.

resolve() turns the table into {"pc", "ret", "store", "host"}: name -> part ids in table order (guest addresses are the
ELF symbol value + bias; call and sym parts go to "pc", the three return kinds to "ret"). Every exe address missing from
the manifest and every symbol missing from the ELF is collected and reported together in one ResolveError.

Progress rule: a milestone is REACHED only if every earlier milestone (table order) is already reached. A hit earlier is
reported with out_of_order=True, is not reached and is not credited later; only a new complete hit after the prerequisites
counts. A part event is a hit when, after it is applied, its milestone part is complete: `any` always; `all` when every
part of the group has been seen (parts stay seen); `seq` when every part has counted in order (an event of a part whose
predecessors have not counted is ignored). Once a group is complete, every later event of one of its parts is a hit again.
`main-thread` parts ignore events with tid != main_tid. Events are counted from 0 over every call of event(); an unknown
part id is a KeyError. first_event_index is the index of the event that reached the milestone (None until then).
```

## verify/bootpath.py

```text
bootpath.py - the guest-path rule of the boot stage: where a guest SD path lands on the host.

A guest path is a string, "sdmc:/a/b" (prefix case-insensitive) or "/a/b"; "\" counts as "/". Components "" and "." are
dropped; ".." removes the previous component and stops at the card's root, so nothing outside the mounts is reachable
(the path is refused by clamping, not by an error: "/../x" is "/x").
Mounts. The guest sees one SD card, mapped to `root`. The game install (`game`) is mounted at sdmc:/switch/fnv and at
sdmc:/fnvnx/game: a path under either prefix (compared case-folded) continues inside `game`, except that the port's own
entries of sdmc:/switch/fnv (PORT_NAMES, PORT_PREFIXES: logs, profile, launcher files) stay on `root`. Without `game`
it is <root>/switch/fnv when that directory holds anything besides the port's own entries, else <root>/fnvnx/game.
Case. The SD card folds case. Each component is matched against the host entries: if the exact spelling exists it is
used (os.path.lexists); else the host entries equal under str.casefold() are collected and the first in sorted order
(code-point order, deterministic) is taken; with no match the spelling given is kept (a path to be created).
The old driver took `sorted(hit)[0]` too, so two entries differing only by case resolve the same way here.
Directory listings are cached per directory until changed() (call after create/remove/rename).
```

## verify/bootsock.py

```text
The socket services (module 11, fn 16..33, hostapi.h + sockwire.h) of the boot stage machine, for a host that has NO network interface.

Model (owner decision "The declared machine", CLAUDE.md: the machine has no network connection and every network call returns the
documented error of that state, never a fake success): the machine's host has only the loopback addresses; no packet ever arrives and nothing can connect. A socket is an entry of a
table of open handles; every result is derived from the header text and host_sock.inc (the reference host: its argument checks, in its
order) plus POSIX where the header states "an OS result" for a situation that exists without traffic. A (service, case) whose result
needs real traffic, or an OS's own choice, is NOT answered: the model raises NotDetermined and the machine ends the run as
"ext-module-missing" naming the service. A guest address that is not readable / writable memory raises BadGuestMemory (a violated
precondition of the header; the machine ends the run as a host-call fault). Never an invented value.

Stated assumptions (each is a fact of the stage's model, not of the header):
  A1 handle numbers: the header says only that a handle is the descriptor number the bsd service returned and that the host keeps just the
     set of open ones (no numbering of its own, no "lowest free"). The stage's model chooses: the lowest number >= 3 that is not open (0..2
     are stdio); a number is reused after its close. Guest programs must not depend on it. SOCKET never fails for lack of room.
  A2 the host's loopback has the addresses 127.0.0.0/8 and ::1; "any" is 0.0.0.0 / ::; every other unicast address is not local.
  A3 getaddrinfo with no network: a numeric node is parsed (strict dotted quad, or an RFC 4291 text without a scope) and a null hints
     argument is a hints record of zeros (POSIX); the list of a numeric node is one record per socket type STREAM/TCP 6, DGRAM/UDP 17,
     RAW/0 (the type given in the hints keeps one; the protocol given keeps those with that protocol), in this order, ai_flags = hints flags.
  A4 EPROTONOSUPPORT: STREAM takes protocol 0 or 6, DGRAM 0 or 17; any other protocol is EPROTONOSUPPORT except those a real OS may
     implement for the type (1, 58, 132, 136, 262, or outside 0..255), which are not answered.

The header (hostapi.h at a1923f65) and host_sock.inc agree on every case below; where a result is "the platform's answer" and depends on
traffic or on the OS's own choice, the case is NOT answered.

Served per service (everything else listed here as NOT answered):
  SOCKET     the domain is checked before the type: unknown domain (not 0/2/10, or wider than int32) EAFNOSUPPORT; unknown type EINVAL; STREAM/DGRAM (domain 2 or 10) -> a handle
             (A1, A4). NOT answered: domain AF_UNSPEC, type RAW, the protocols of A4's exception.
  CLOSE      an open handle -> 0 and gone; not open -> EBADF.
  every handle-first service: a handle that is not open (or wider than int32) -> EBADF before anything else.
  BIND       null address EFAULT; family not 0/2/10 EAFNOSUPPORT; a local address (A2) or any of the socket's family -> 0 (recorded);
             a unicast address that is not local EADDRNOTAVAIL; a socket already bound or listening EINVAL (POSIX).
             NOT answered: family AF_UNSPEC or another than the socket's, a v4-mapped / multicast / broadcast / link-local address,
             flowinfo or scope_id != 0, a port 1..1023, a port also in a bound socket's overlapping address, a bound socket's port 0
             later asked by GETSOCKNAME.
  LISTEN     DGRAM EOPNOTSUPP; a bound STREAM -> 0. NOT answered: an unbound STREAM.
  ACCEPT     DGRAM EOPNOTSUPP; not listening EINVAL; listening: non-blocking EAGAIN, blocking waits for ever (nothing can arrive).
  CONNECT    (the host checks only null and family, then the system service) null address EFAULT; family not 0/2/10 EAFNOSUPPORT; an address that is not local (A2) ENETUNREACH; a local address on a
             DGRAM socket (port != 0) -> 0, the peer recorded. NOT answered: a local address on a STREAM socket (loopback traffic), AF_UNSPEC
             or another family than the socket's, multicast / broadcast / link-local / v4-mapped addresses, flowinfo or scope_id != 0,
             a listening socket, address any, port 0.
  SENDTO     len > 0x7FFFFFFF EINVAL; an unknown flag bit EOPNOTSUPP; a bad address family EAFNOSUPPORT; DGRAM to a non-local address
             ENETUNREACH; DGRAM without address and without peer EDESTADDRREQ. NOT answered: every STREAM socket, MSG_OOB, a socket
             shut down, a local destination or a peer (loopback traffic), other families / exotic addresses as for CONNECT.
  RECVFROM   len / flags as SENDTO; STREAM ENOTCONN; DGRAM with nothing to receive: non-blocking or MSG_DONTWAIT EAGAIN, blocking waits for ever,
             or SO_RCVTIMEO (set earlier, > 0) in virtual time and then EAGAIN. NOT answered: MSG_OOB, a socket shut down for reading.
  SETSOCKOPT unknown (level, option) / read-only (SO_TYPE, SO_ERROR) ENOPROTOOPT, null value EFAULT, vallen != the shape's size EINVAL,
             TCP level on a non-STREAM, IP level on a non-INET, IPV6 level on a non-INET6 socket ENOPROTOOPT; then, for the boolean options
             (SO_DEBUG REUSEADDR DONTROUTE BROADCAST KEEPALIVE OOBINLINE REUSEPORT, TCP_NODELAY), SO_LINGER (onoff, linger_s >= 0) and
             SO_RCVTIMEO / SO_SNDTIMEO (sec >= 0, 0 <= usec < 1000000): 0 and recorded. NOT answered: every other option (its accepted
             range is the OS's), a linger / timeval outside those ranges.
  GETSOCKOPT order: handle EBADF, unknown (level, option) ENOPROTOOPT, null value EFAULT (also with capacity 0), null vallen EFAULT; then,
             for an option never set on this socket: SO_TYPE, SO_ERROR 0, the boolean options 0, SO_LINGER {0, 0}, the timeouts {0, 0}:
             min(capacity, size) bytes are written and *vallen is ALWAYS the full size (INT/TYPE/ERRNO 4, LINGER 8, TIMEVAL 16), result 0.
             NOT answered: an option that was set (the OS stores its own form), every other option, SO_ACCEPTCONN, an option of another
             protocol level than the socket's.
  SHUTDOWN   how > 2 EINVAL; a socket with no peer ENOTCONN; a DGRAM socket with a peer -> 0 (recorded).
  GETSOCKNAME null EFAULT; a bound socket (port != 0) -> its address. NOT answered: an unbound socket, a bound port 0.
  GETPEERNAME null EFAULT; no peer ENOTCONN; a recorded peer -> it.
  GETFL / SETFL  RDWR | NONBLOCK as the header says; SETFL sets NONBLOCK from that bit, other bits are ignored, result 0 (always answered).
  POLL       nfds > 0x1000000 EINVAL, nfds > 0 with a null array EFAULT; a negative fd is ignored (revents 0); an fd that is not an open
             handle gets POLLNVAL, counts, and makes the call not wait (timeout 0); event bits outside the wire list are dropped; an open
             socket with no local peer has no traffic ever (no network): revents 0, and after the timeout (virtual time; for ever for -1)
             the result is 0. NOT answered: an open socket that has a local peer or could get one: a recorded peer, a bound or a listening
             socket (a local sender could produce traffic; the model never produces it, so the case is left to the OS).
  GAI_OPEN   hints checked first (BADFLAGS, FAMILY, SOCKTYPE), both nodes null NONAME; node a name: NONAME (the name "localhost" without AI_NUMERICHOST is not answered)
             (*sys_errno not written); node null with a family INET / INET6 in the hints, or numeric of that family or of AF_UNSPEC hints
             (A3), service null or decimal 0..65535 (no leading zero): a ticket; a non-numeric service with AI_NUMERICSERV NONAME.
             NOT answered: node null with family AF_UNSPEC, a numeric node of the other family, flags CANONNAME V4MAPPED ALL ADDRCONFIG,
             a service name, a node that is numeric by inet_aton's rules but not strict (1.2.3, 0x7f.1, 01.2.3.4), an unparsable text
             with ':' or '%', an empty node, a hints type/protocol combination without a record.
  GAI_FETCH  the ticket's records as host_sock.inc writes them; a ticket that is not open EINVAL.
```

## verify/cas.py

```text
Content-addressed store and tree manifests (base of the hermetic build sandbox, rule R12).

Layout: STORE/obj/<aa>/<sha256>      file contents, mode 0444, mtime 0
        STORE/obj/<aa>/<sha256>.x    same content with exec bit, mode 0555 (separate object)
        STORE/tree/<aa>/<hash>.json  canonical manifests (<aa> = first two hex digits)

Tree hash definition:
  canon(obj) = json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=True) as ASCII bytes.
  entry of a regular file: {"t":"f","h":sha256(content),"x":0|1}   (x = any exec bit set)
  entry of a symlink:      {"t":"l","to":link text}                (never followed)
  entry of a directory:    {"t":"d","h":treehash}
  directory manifest:      canon({"format":"fnvnx-tree/1","entries":{name: entry}}); treehash = sha256(that)
  Names and link texts must be valid UTF-8 (else error). Other file types (fifo, socket, device) are errors.
  Hash of a top-level file/symlink input = sha256(canon(its entry)); of a directory = its treehash.
  Empty directories are kept. Nothing else (mtime, owner, mode other than exec) is part of the hash.
```

## verify/coffnorm.py

```text
coffnorm: zero the time-dependent fields of a COFF object or COFF/ar archive. stdlib only; run as `python3 -I`.

RULE: every time-dependent field of a COFF object / archive is defined by the PE-COFF format specification; the
normalizer sets exactly those fields to zero and changes nothing else. The fields (all little-endian):
  COFF object   IMAGE_FILE_HEADER.TimeDateStamp                         offset 4, 4 bytes
  bigobj        ANON_OBJECT_HEADER_BIGOBJ.TimeDateStamp (Sig1=0, Sig2=0xFFFF, Version>=2, ClassID
                {D1BAA1C7-BAEE-4ba9-AF20-FAF66AA4DCB8})                 offset 8, 4 bytes
  import object IMPORT_OBJECT_HEADER.TimeDateStamp (Sig1=0, Sig2=0xFFFF, Version 0)   offset 8, 4 bytes
  archive       each member header (60 bytes): date (offset 16, 12 ASCII bytes) -> "0" + 11 spaces; and the
                ownership fields as GNU `ar D` writes them (checked with `ar rcD`, binutils 2.42):
                uid (offset 28, 6 bytes) "0     ", gid (offset 34, 6 bytes) "0     ", mode (offset 40, 8 bytes)
                "644     ". Linker members ("/") and the longnames member ("//") are kept; their data holds no time.
  CodeView      .debug$T records LF_TYPESERVER (0x1301), LF_TYPESERVER_ST (0x1501), LF_TYPESERVER2 (0x1515) carry a PDB
                signature/GUID: positively identified, counted, NEVER rewritten; error unless --allow-typeserver.
Nothing else in .debug$S/.debug$T is identified as time-dependent (S_OBJNAME's signature is the PCH signature, not a
time; whether a given compiler fills it with a time is not decided by the format, so it is left alone and unlisted).
PE images (MZ) and anything unrecognised: exit 1. Exit codes: 0 ok, 1 error, 2 `check` found a non-normalized field.

usage: coffnorm.py normalize IN -o OUT [--allow-typeserver] | check FILE... [--allow-typeserver] | fields FILE
```

## verify/envcheck.py

```text
envcheck: the declared, reproducible environment of the L1 verdict (verify/env.json, "fnvnx-env/1"). Run as `python3 -I`.
    envcheck.py check [--env F] [--json OUT]   every item of F against this machine; one line per mismatch `env: <item>: want X,
                                               have Y`, then `envcheck: OK` | `envcheck: MISMATCH (n)`; exit 0/2
    envcheck.py id [--env F]                   sha256 of the canonical env.json (the verdict's env_id)
    envcheck.py snapshot -o F --from-code      regenerate F from THIS machine by the rule below; only on a machine set up by
                                               farm/setup_verdict_env.sh, then commit F
Rule (packages are DERIVED, never listed by hand): a package is in the environment iff dpkg says it owns a file that a verify tool
or test executes or reads as data: (1) each of PROGRAMS (resolved path + loader closure); (2) python's loader closure (interpreter,
lib-dynload, PY_MODULES); (3) UNIT_CC through unitstage.gcc_set(): driver, `as`, every ELF file of its trees + closure, and EVERY
FILE under its trees (the unit stage ingests them whole: /usr/include, GCC install dir with plugin headers, libexec); the host
gcc's HOST_GCC_PROGS/HOST_GCC_FILES. The trees also get a listing digest (an extra package adding files there is a mismatch).
A file without a dpkg owner outside python's prefix is an error. Programs and who executes them: strace opentrace.py; busybox
tests/test_graph,test_sandbox; gcc,as,ld test_sandbox,test_sdcheck; aarch64-linux-gnu-g++ test_unitstage (unitstage.py);
aarch64-linux-gnu-nm,readelf icount.py,test_unitstage; aarch64-linux-gnu-as,ld,objcopy test_icount; ar,clang test_coffnorm;
git verdict.py,l1.py,test_gates; ldd,true test_runtime; g++,llvm-config-18 build a64access.cc (objstub.py, test_a64access).
The a64access build set is derived the same way: every header `g++ -M` reports for a64access.cc under `llvm-config-18 --cxxflags`,
and every library file `llvm-config-18 --libfiles` names for A64ACCESS's components (plus cc1plus, libstdc++.so above). Python modules: capstone, unicorn (icount.py, test_icount, test_runtime); pyelftools (stage tools of contract/: objfacts).
```

## verify/fpbound.py

```text
fpbound: rule V-FP, derived floating-point comparison bounds (stdlib only, exact Fractions).

Two executions of one source-level computation (x87 original / IEEE ours) each carry, along their own executed
chain, a rigorous absolute bound e on |computed - exact real result of the same chain on the common inputs|.
ACCEPT iff |ours - exe| <= E_ours + E_exe. No tolerance is assigned: every number is derived.
x~, y~: computed operands (exact Fractions), e_x, e_y their bounds (|x - x~| <= e_x), z the computed result,
w the exact result of the op on x~, y~.  Formulas (r = rounding term of the op itself):
  add/sub  e_z = e_x + e_y + r
  mul      e_z = |x~| e_y + |y~| e_x + e_x e_y + r
  div      e_z = (e_x + |x~/y~| e_y) / (|y~| - e_y) + r     requires |y~| > e_y, else UNBOUNDED
  sqrt     e_z = e_x / (2 sqrt(x~ - e_x)) + r   requires x~ > e_x (x~ = e_x = 0 gives 0); sqrt(x~ - e_x) is
           replaced by a rational LOWER bound, so e_z only grows (sound)
  neg/abs  e unchanged;  scale2 (times 2^k): e 2^k + r;  store(fmt): e + r(fmt)
  transc   e_z = lip e_x + max(rel |z| / (1 - rel), floor); lip: caller's Lipschitz bound of f near x~;
           rel: caller's relative bound of the implementation (ours: 2^-p, correctly rounded)
  r = 0 if z == w exactly, else u |z| (u = 2^-p); r = 2^(emin-p) (half the smallest subnormal spacing) when
  |z| < 2^emin.  A bound reaching the overflow threshold (2 - 2^-p) 2^emax makes the value UNBOUNDED.
Proofs (triangle inequality):
  add: |z - (x+y)| <= |z - (x~+y~)| + |x~-x| + |y~-y| <= r + e_x + e_y.
  mul: x~y~ - xy = x~(y~-y) + y(x~-x) and |y| <= |y~| + e_y.
  div: x~/y~ - x/y = (x~-x)/y + (x~/y~)(y-y~)/y with |y| >= |y~| - e_y > 0.
  sqrt: |sqrt(x~)-sqrt(x)| = |x~-x| / (sqrt(x~)+sqrt(x)) and sqrt(x), sqrt(x~) >= sqrt(x~-e_x).
  rounding: nearest p-bit z of w in binade [2^k,2^(k+1)): spacing 2^(k-p+1), |z-w| <= 2^(k-p) <= u|z|; below
  2^emin the spacing is the fixed 2^(emin-p+1), so |z-w| <= 2^(emin-p).
  transc: z = f(x~)(1+d), |d| <= rel => |f(x~)| <= |z|/(1-rel); and |f(x~) - f(x)| <= lip e_x.
Kinds: 'f' finite, '+'/'-' infinity, 'n' NaN (payload/sign not compared), 'u' UNBOUNDED (unstable).
Infinities/NaN from memory or from exact operands are exact. Division by an interval containing 0
(also exactly 0, whose sign is unknown) is UNBOUNDED.
```

## verify/gate_admit.py

```text
Gate "admit" (fnvnx-gate/1): nothing is in the trunk without the gates that close its classes of failure.
    python3 -I gate_admit.py        run in the checkout root (a git worktree), env GATE_OUT; writes GATE_OUT/admit.json

Rule. verify/admit.json ("fnvnx-admit/1") lists the roots of the port tree: {"path", "class", "gates", ["recipe"]}.
Universe: every file git tracks below ports/fnv-nx (`git ls-files -z`), an item is its repo-relative path.
A file is certified iff ALL hold:
  1. exactly one root contains it (a root is a directory prefix or one exact file path; roots do not nest);
  2. the root's class is one of CLASSES and the root names every gate REQUIRED for that class;
  3. every gate the root names is a gate of verify/gates.json of this same tree;
  4. a product root names a "recipe": a file inside the root that the build gates read (how its code is compiled in the
     declared environment); the file exists.
Classes:
  doc      text for people: no gate required. Nothing in a doc root is compiled or run by a build.
  tool     stage and verification tools: "selftests" required (their tests pass in the declared environment).
  product  code that is compiled into the shipped binary: REQUIRED_PRODUCT gates, the static closure of the run-time
           failure classes of verify/VERDICT.md. A product root cannot be admitted before those gates exist in
           gates.json, so code enters the trunk only together with the gates that judge it.
Argument: the verdict is green only if certified == universe, so a tracked file outside every root, a root with a
missing gate, or a product root without its gates makes every merge red. The table maps directories to gates; it
holds no exception for a file, a function or a finding.
Assumption: `git ls-files` lists the tree under test (the verdict runs gates in a clean worktree of the commit).
```

## verify/gate_build.py

```text
Gate "build" (fnvnx-gate/1): every product root's recipe (build.json, see RECIPE.md) builds, all in the sandbox.
    python3 -I gate_build.py        cwd = checkout root (a git worktree), env GATE_OUT, optional GATE_CACHE, optional GATE_COMMIT
Universe: every unit of every product root of verify/admit.json (tracked sources + generated sources, recipe.units_of) and the ELF
of a root with a link. Certified: units whose object was built (+ the ELF). uncertified: repo-relative unit paths (<= 50, sorted).
No product root: universe 0, green. A recipe that fails validation certifies nothing (units = tracked source files).
Stages are cached by key in GATE_CACHE/store (a temp dir without GATE_CACHE); the result must not depend on the cache (determinism.py
compares a warm and a cold build). Hand-over to later gates: verdict.py gives EVERY gate its own GATE_OUT, so a later gate cannot
read this gate's GATE_OUT. objects.json of a root is therefore also kept at GATE_CACHE/objects/<tree>/<root name>/objects.json (with
obj/, the ELF and constants.tsv beside it; <tree> = git tree of the commit under test, <root name> = path with / as __), and later
gates call ensure() (build or cache hits, same result). Without GATE_CACHE nothing outlives the gate except GATE_OUT/objects/<root name>.json.
```

## verify/gate_linkstrict.py

```text
Gate "linkstrict" (fnvnx-gate/1, class 3 of VERDICT.md): no symbol is unresolved or substitutable. Decides on symbol tables only.
    python3 -I gate_linkstrict.py    cwd = checkout root, env GATE_OUT, optional GATE_CACHE and GATE_COMMIT; writes GATE_OUT/linkstrict.json
Roots come from verify/admit.json: for every product root the gate calls gate_build.ensure(root, GATE_CACHE) and reads the ELF `.symtab` of
its objects (relocatable) and of its linked ELF, never a name's text. Rules:
  (a) a root that links an ELF: the ELF has no undefined symbol and no weak undefined symbol, no IFUNC, and a `.symtab` (item `<elf>`).
  (b) across ALL product roots: every undefined symbol (global or weak) of every object is defined by exactly ONE object with a strong
      (GLOBAL, not common) definition, or by one COMDAT group family (b1), or by the link script (b2). Findings: `undefined` (no
      definition anywhere), `duplicate-strong` (two strong definitions), `weak-only` (a weak or UNIQUE definition outside any COMDAT
      group and no strong one: the linker may substitute it), `common` (a common symbol), `ifunc` (an IFUNC definition: the body is
      chosen at run time), `comdat-differs` (b1). Items: every undefined reference (object, symbol), every defined global symbol name
      and every COMDAT signature; a reference is certified iff its symbol has exactly one definition and no finding.
  (b1) C++ vague linkage. A weak or UNIQUE definition in a section that is a member of an SHT_GROUP with GRP_COMDAT (inline functions,
      templates, vtables, typeinfo, guard variables) is a DEFINITION: the linker keeps exactly one copy per group signature (the group's
      signature symbol). The kept copy is arbitrary, so the rule needs all copies to be the same: for each signature, across all
      objects of all product roots, every copy (the member sections in group order: name, type, flags, alignment, bytes, and the
      relocations of each member by offset, type, addend and TARGET) has one digest, else every copy is `comdat-differs` (an ODR
      violation). TARGET of a relocation: a global/weak symbol by name; a member of the same group by its ordinal in the group plus offset;
      a SHF_MERGE section by the entity it points at (the NUL-terminated string, or entsize bytes), so string literals of one inline
      function certify wherever the object put them; any other local target by (section name, symbol name, offset), which is
      layout dependent and therefore conservative (two identical copies that reference differently laid out local data are reported).
      A weak definition outside every COMDAT group stays `weak-only`.
  (b2) Linker-script symbols. A reference that no object defines is certified iff its object belongs to a root that links an ELF
      and that ELF's `.symtab` defines the name, GLOBAL (not weak, not local): the link script defined it (`__image_end`). A root that
      links no ELF has no script, so such a reference stays `undefined`; a symbol the ELF defines only weakly stays `undefined`.
  (c) contract names: the optional recipe key "contract_names" is a TSV `symbol<TAB>body_id` generated by the owner (an import name ->
      its body). Every listed symbol must be defined in the linked ELF with a nonzero size (`contract-undefined`, `contract-no-elf` when
      the root links no ELF), and two listed symbols with DIFFERENT body ids may not share one address (`contract-alias`: an alias stub
      that serves two contracts with one body). Two names with the same body id on one address are fine. A symbol listed twice with
      two body ids is `contract-duplicate`. Without the key rule (c) has universe 0 for that root.
Universe = undefined references + defined global names + contract names + one `<elf>` per linked root (+ a root whose recipe is invalid or
whose build is red, one uncertified item). Certified = the items with no finding. No product root: universe 0, green.
NOT EXACT (stated): rule (b) is by name over the objects. A script symbol with hidden visibility (PROVIDE_HIDDEN) is LOCAL in the
ELF, so it stays `undefined` (a finding, never a false certificate). b1 compares what the linker sees (bytes and relocations), not
what the source says; it does not follow a relocation into a non-group, non-merge local section beyond (section name, symbol, offset).
Rule (c) groups by address: a body reached through two names of one address with the same body id is the owner's statement of one body.
```

## verify/gate_objstub.py

```text
Gate "objstub" (fnvnx-gate/1, classes 1 and 2 of VERDICT.md): no built function holds a trap/abort path or is vacuous (rules and proofs: objstub.py).
    python3 -I gate_objstub.py [--jobs N]     cwd = checkout root, env GATE_OUT, optional GATE_CACHE and GATE_COMMIT; writes GATE_OUT/objstub.json
Roots come from verify/admit.json, never from arguments: for every product root the gate calls gate_build.ensure(root, GATE_CACHE) (build or
cache hits) and judges what the recipe says (RECIPE.md): the objects (os_layer ones may hold SVC), the constants table (key (unit, symbol))
and the terminator set DERIVED from the recipe's compiler: objstub.unit_terminators runs the compiler's own plugin over the preprocessed text
of every C/C++ unit (the stage result unit.ii of the build, found by the pp_key in objects.json), so the set is a function of the compiler,
its flags and every header the unit really includes (a function of the compiler and the header set; the finer key is the preprocessor stage
key). The set is cached per unit under GATE_CACHE/objstub. No product root: universe 0, green.
Wine rows: verify/wine_fixme.tsv of the tree under test (objstub.read_wine_table; missing or malformed is a gate error, red); the result
holds "wine_fixme": {id: call sites} so growth of the simplified bodies is visible.
Reach facts: GATE_CACHE/reach/<git tree>/reach.facts.tsv (rows fact_id<TAB>sha256, written by the gate `reach`, which does not exist yet).
While the file is absent, every call of fnvnx_reach_violation is the finding `reach-uncertified`.
Universe: every function of every object of every product root, every uncovered gap, and a root whose recipe is invalid or whose build is
red (one item `root`, uncertified). Certified: items with no finding. uncertified: the first 50 `unit path:symbol`, sorted. Per object results
are cached by (object sha256, os_layer flag, analysis version, the terminators the object mentions) under GATE_CACHE/objstub/<key>.json;
uncached objects run in --jobs processes (default os.cpu_count()). Exit 0 iff green; unreadable input is red with the reason in detail.
```

## verify/gate_oracle.py

```text
Gate "oracle" (fnvnx-gate/1): every test names where its expected values come from, and that source is not the
code under test (verify/VERDICT.md class 10).
    python3 -I gate_oracle.py        run in the checkout root (a git worktree), env GATE_OUT; writes GATE_OUT/oracle.json

Universe: the test files of gate "selftests" (the DIRs of its argv in verify/gates.json, DIR/tests/test_*.py, found by
gate_tests.universe: one list for both gates). An item is a repo-relative path.
Declaration: DIR/tests/oracle.json ("fnvnx-oracle/1"):
  {"format": "fnvnx-oracle/1", "tests": {"test_x.py": {"subject": [PATH...], "references": [REF...]}}}
  subject     the files under test (repo-relative).
  REF         one of (exactly these keys):
    {"kind": "wine-exe", "wine": "9.0", "exe_sha256": H64, "artifact": {"repo": "fnv-nx-build", "commit": H40,
     "tree_sha256": H64, "path": STR}}   a dump the game's exe produced under Wine 9.0: file `path` of that commit of
                                         the artifacts repository (an external, as in RECIPE.md)
    {"kind": "spec", "path": PATH, "anchor": STR}   cited text: a tracked file and a place in it. `anchor` is a line
                                         range "L<m>-L<n>" (1 <= m <= n <= number of lines) or a whole line of the file
                                         that starts with "#" (a heading) and occurs exactly once
    {"kind": "contract", "path": PATH}     a contract table of the tree
    {"kind": "model", "path": PATH}        an independent model in the tree
Rule. A test file is certified iff ALL hold:
  1. its DIR's oracle.json is well formed and holds exactly one entry for it (an entry without a test file, an unknown
     key or a wrong format is an error of the DIR: none of its tests is certified);
  2. `subject` is not empty and every subject path is a tracked file inside a root of verify/admit.json;
  3. `references` is not empty and every REF has one of the forms above; `wine` is "9.0"; `exe_sha256` equals
     verify/oracle_pins.json "exe_sha256"; the artifact is fetched by its exact commit (`git fetch --depth 1
     $ARTIFACTS_URL <commit>`, cached in GATE_CACHE/external/<commit> like a recipe's external), its tree hash
     (cas.py) equals `tree_sha256` and `path` is a file of it; every `path` of a spec, contract or model is a tracked
     file inside a root; a spec's anchor resolves in its file. A spec without a file and an anchor is not a reference;
  4. independence: no spec, contract or model `path` is in the import closure of the subject. The closure is computed
     from the syntax trees: a subject file, plus, for every `import a.b` / `from a.b import c` in a closure file, every
     tracked file of the tree named a/b.py, b.py, a/b/__init__.py, a/b/c.py or c.py at any depth (a superset of what
     python can load, whatever sys.path is; a closure file that does not parse makes the test uncertified);
  5. product: when a subject file lies in a product root, some REF is wine-exe or spec, and no REF `path` lies in a
     product root.
Argument: an expected value "comes from the code under test" when its declared source is the subject or is computed
through it. Rules 2-5 leave four sources: a dump of the exe under the pinned Wine, text at a place that exists in a
file, a contract table and a model, the last three outside everything the subject can load, and for product code never
the product tree. The verdict is green only if certified == universe, so a test without such a source blocks the merge.
Assumption (checked by verification, not by this gate): the declaration is true - the subject list is complete and
the test compares against the declared references. The after-merge verifier runs every test under verify/opentrace.py
and compares the tracked files it opened with subject + references (verify/oraclecheck.py).
```

## verify/gate_pure.py

```text
Gate "pure" (fnvnx-gate/1): every Python file of the stage-tool directories is accepted by puregate.
    python3 -I gate_pure.py DIR...        run in the checkout root, env GATE_OUT (result dir); writes GATE_OUT/pure.json
Universe: the items puregate checks below the existing DIRs given (a missing DIR contributes nothing): every .py file, and
every shell file (a shell file is an item and is never certified: `shell-unanalysable`). An item is a repo-relative path.
Certified: items with zero puregate violations. All DIRs are checked in ONE puregate run, so project imports resolve across them.
uncertified: the first 50 uncertified paths, sorted. Zero items: green, universe 0 (nothing to certify yet), said in "detail".
The universe is the STAGE-TOOL directories only (contract link rules lift). verify/ and farm/ are NOT stage tools: the
sandbox, the tracer and the farm scripts legitimately read the host (clock, processes, directory listings), so they are
outside this gate; their correctness is the business of the "selftests" gate. Files below a directory named `tests` are test
code, not stage tools: they are outside the universe too (the "selftests" gate runs them; they may use temp directories).
Exit 0 iff the result is green.
```

## verify/gate_tests.py

```text
Gate "selftests" (fnvnx-gate/1): every test file of the given directories passes.
    python3 -I gate_tests.py [--timeout-s N] [--jobs N] [--cache DIR] [--key-dirs D...] [--] DIR...
Run in the checkout root with env GATE_OUT; writes GATE_OUT/selftests.json (+ GATE_OUT/selftests.times.json).
Universe: DIR/tests/test_*.py for each DIR given (a missing DIR contributes nothing), in argument order, each DIR's files
sorted by name; items are repo-relative paths. Each file runs as `python3 -I FILE` in the checkout with a per-file timeout
(default 300 s; a timeout kills the process group and is a failure). Certified: exit status 0. No skip logic: a test that
cannot run in this environment (root, namespaces, strace) is uncertified. Zero files: green, universe 0. Exit 0 iff green.
--jobs N (default 1): N test files run at once, each in its own process; results are reported in sorted universe order.
  A test must therefore keep its scratch under its own tempfile.mkdtemp directory (no fixed path). No test needs a serial
  group: sandbox namespaces are per process and every store/work directory is per run.
--cache DIR (default $GATE_CACHE, see verdict.py; none = no caching): a test file's result is a function of the content of the
  directories the gate declares and of the declared environment. KEY = sha256 over: cas.tree_hash of every key dir
  (--key-dirs, default: the DIRs scanned; whole directory content, no mtimes, __pycache__ skipped), the envcheck id of the
  env.json next to this script, this gate's argv without --cache/--jobs, and the python version string. If DIR/<KEY>.json
  records every file of the universe as passed, they are certified WITHOUT running (detail: `N of M pass, cached key <12 hex>`).
  Otherwise all files run and the per-file results are stored under KEY only when the run completed. All-or-nothing per key:
  no per-file dependency guessing; a failed file is never recorded as passed, a key with a failure re-runs everything.
Result extra key "files": [{"name","seconds","status"}] (status passed|failed|cached); same list in selftests.times.json.
```

## verify/graph.py

```text
graph: the incremental build. A build is a DAG of hermetic sandbox stages (sandbox.py, "fnvnx-stage/1"); a stage's key
hashes its declared inputs, so an unchanged stage is a HIT and never executes; independent stages run in parallel.

    graph.py run  --store S GRAPH.json [--jobs N] [--cold] [--report R.json] [--toolflags]   exit 0, 1 bad graph, 2 failed/violation, 3 cold diff
    graph.py keys --store S GRAPH.json                      name -> key (`pending` while a producer has no stored result); runs nothing
    graph.py why  --store S GRAPH.json --against OLD.json   for each stage whose key differs from OLD_REPORT: the changed input/field

GRAPH "fnvnx-graph/1": {"sources": {NAME: host path}, "runtimes": {NAME: runtime.json}, "targets": [names] (default all), "stages":
 [{"name","class","tool","runtime"?,"argv","cwd","env"?,"outputs":[dirs],"inputs":[{"at", ONE OF "source":NAME | "tree":HASH |
 "stage":NAME,"output":DIR,"path"?:file or subtree of that output}]}]}. Sources are ingested at start; the host path is in no key.
A stage with "runtime" gets runtime.py's inputs, env (TZ/LC_ALL/SOURCE_DATE_EPOCH/COLUMNS/PATH, then the stage's) and argv prefix
[python,-s,-B]. Concrete documents are stored as STORE/keys/<key>/stage.json next to result.json (the data of `why`). Each stage runs as
`python3 -I sandbox.py run` in a child process. --jobs only schedules: it is in no key, document or output. Stage report order =
graph order. A producer with rc != 0 makes every transitive consumer `not-run`; a "path" absent from the producer's output fails
the consumer (a graph error, not skipped). --toolflags checks every document BEFORE anything runs; a document's violations depend
on argv/env/tool/input `at` only (not on tree hashes), so documents of not-yet-run consumers are checked with placeholder trees.
```

## verify/icount.py

```text
Deterministic executed-instruction counter for a Unicorn AArch64 guest (the owner's perf metric).

Library: attach(uc, lo, hi, spec=None, bias=0) installs one UC_HOOK_BLOCK over [lo, hi) and returns an Icount;
the emulator tells it thread(tid), mark(name, out_of_order=False) and stop(reason, **events); write(path) or report()
gives the OUT.json document ("fnvnx-icount/1", see report()). CLI: `python3 -I verify/icount.py spec ...` builds the
SPEC.json that classes (ranges, entries) are read from; run `spec -h`.

Counting rule: a block of `size` bytes adds size/4 instructions. The static facts of a block (instructions per opcode
class, per range class, entry) are computed once per (address, size) from the block's bytes and cached, so the hook
is a dict lookup plus integer adds. Counters are kept per (interval, thread id); an interval is the span between two
consecutive marks ("start" -> first mark ... last mark -> "stop"); a mark that was out of order flags the interval it
closes and the interval it opens. Thread 1 is named "main", thread N is "tN" (thread(tid, name) overrides).

Classes (A64 encodings, ARM ARM "Loads and Stores" / "System"; mask/value in OPCODES):
  ldar_stlr    every acquire/release memory op: LDAR/LDARB/LDARH, STLR/STLRB/STLRH, plus the two sub-counters
  ldapr        LDAPR/LDAPRB/LDAPRH and the LDAPUR*/STLUR* family (FEAT_LRCPC / LRCPC2)
  ldaxr_stlxr  LDAXR/LDAXRB/LDAXRH/LDAXP and STLXR/STLXRB/STLXRH/STLXP
               (plain LDXR/STXR, LDLAR/STLLR and the CAS/SWP/LD<op> atomics are NOT counted)
  dmb, dsb, isb  DMB, DSB (SSBB/PSSBB are DSB encodings) and ISB, any option; dmb/dsb/isb are not in ldar_stlr.
Range classes count instructions by the address of each instruction (a block that straddles a range edge is split
exactly). Entry classes count the blocks that START exactly at a listed address (calls of those functions, and also
a fall-through into the address if the translator split the block there). Code outside [lo, hi) is not counted.

Exactness limits (also in OUT.json "exactness"): a block hook fires at block entry, so a block that does not run to its
end is counted in full: a fault inside a block, an emu_stop inside a block (and the re-entry that then starts a new
block at the resume pc counts that part twice), a count=/timer-limited slice that ends inside a block. stop() takes
the emulator's event counts (faults, budget slices, ...) as `events` and puts them in OUT.json; nothing else is
approximate. Threads that switch at host calls (blocks ended by a branch to a host page) are exact.
```

## verify/l1.py

```text
L1: the one command a merger runs. Run as `python3 -I l1.py CMD ...`. Exit codes: 0 green/merged, 2 red/blocked, 3 conflict, 4 blocked: env, 1 error.
  verdict  --repo R --commit C --verdicts VDIR [--budget-s 300]
           verdict.py run with the gates file OF THAT COMMIT (ports/fnv-nx/verify/gates.json read from C's tree), then store.
  merge    --repo R --into BRANCH --from REF --verdicts VDIR [--budget-s 300]
           Computes the merge tree as verdict.py merge does. If VDIR has no verdict for it, makes the merge commit object with
           commit-tree (no ref moves) to have something to check out, runs `verdict` on it and stores it. Then verdict.py merge
           re-derives the tree and moves BRANCH only on green. The gates file is the one in the MERGE RESULT tree (a branch
           cannot drop gates): the ratchet's previous verdict is the stored green verdict of BRANCH's current tree, if any;
           otherwise this is the bootstrap case and `bootstrap: no previous verdict` is printed.
Both run `envcheck.py check` FIRST against the verify/env.json OF THAT COMMIT/TREE; on a mismatch they print `blocked: env` and the
mismatch lines, exit 4, run no gate and store nothing. The verdict carries "env_id" (sha256 of that env.json); merge only accepts a
stored verdict with the env_id of the merge tree's env.json.
Gates get GATE_CACHE = VDIR/cache (content-keyed results of gates such as selftests; see verdict.py).
  merge --remote NAME: the remote branch moves only from the exact commit the verdict was computed against, to the exact merge commit
           that was judged, in one atomic step. `git fetch NAME BRANCH` refreshes refs/remotes/NAME/BRANCH; its tip is the BASE of the
           merge (a local BRANCH that exists and differs from that tip is `blocked: local branch differs from NAME/BRANCH`; the caller
           brings it to the tip). REF is resolved as given: the caller fetches it. After green the merge commit (parents BASE, REF) is
           pushed with --force-with-lease=refs/heads/BRANCH:BASE; only after the push succeeded are refs/heads/BRANCH and
           refs/remotes/NAME/BRANCH set to it. A lease rejection is `blocked: moved (NAME/BRANCH is no longer <BASE12>)`, exit 5; any
           other push (or fetch) failure is `blocked: push failed: <first line>`, exit 6; in both nothing local changes and the stored green
           verdict stays (keyed by tree). Without --remote only the local ref moves and the last line ends `local only`.
  Exit codes of merge: 0 merged, 2 red/blocked by verdict, 3 conflict, 4 env, 5 moved, 6 push failed (1 error).
Both print `L1 <green|red> <tree> <seconds>s gates:<n> over_budget:<bool>`; merge adds `merged <commit>` or `blocked: <reason>`.
```

## verify/l1graph.py

```text
l1graph: the L1 CONTRACT stages and gates as one "fnvnx-graph/1" graph (graph.py), and the merge verdict read from its report.

    l1graph.py make --contract-src DIR --inputs INPUTS.json --runtime runtime.json --jobs-in-tools N -o graph.json
    l1graph.py verdict --report GRAPH_REPORT.json --store S -o verdict.json        exit 0 green, 2 red, 1 usage/format error

make: DIR is a checkout root holding ports/fnv-nx/contract. INPUTS.json (private, host paths live only there and in the
sources of the generated graph, never in a key) names: exe, units, msvc, link_out (dir with addrmap.tsv, aliases.ld), link_fixed
(dir of objects), universe. Stages, each ONE hermetic tool invocation (python -s -B via the runtime), wired by output paths:
    facts    exefacts.py                              obj      objfacts.py   (facts)
    n1       n1.py                  gate n1           gates    registry.py   (facts obj n1) gates n2_data n2_call n2_vcall xc
    bodysel  bodysel.py (n1 gates)  gate bodysel      proto    proto.py      (n1)
Every tool is declared as the FILES it imports (its own file plus the sibling modules in TOOLS), not as its directory: an edit
of one tool file changes the key of exactly the stages that load that file, and those stages' consumers. Gate files go to
GATE_OUT=/gate (output dir), data to /out. Class: all stages are "build". n1, the registry gates and bodysel are the build's
own static certificates (bodysel's input is the gate rows), so "verification" would make provenance reject them; no stage
of this graph is verification-only, hence none has that class. --jobs-in-tools is in argv and so in the keys.
NOT in the graph: gate_xunit, gate_rtti (subprocess `aarch64-linux-gnu-nm` / `-readelf` by bare name), gate_pcrel (same plus
contract/pcrel_abs.py): they need those ELF programs and their loader closure as declared inputs; link/tables.py needs
vtid.json, whose producer vtid.py needs L2's pair file (--pairs).

verdict: every file of every stage output tree (cas listing) of at most GATE_MAX bytes (a gate file holds <= 50 ids) that parses
as JSON with "format" == "fnvnx-gate/1" is a gate record, judged by verdict.py's _gate_result (rc = producing stage's rc).
Verdict green iff >= 1 gate, all green with certified == universe, and no stage failed or was not run.
```

## verify/objstub.py

```text
objstub: decide on MACHINE CODE whether built AArch64 objects contain (T) a trap/abort path or (V) a vacuous function.
    objstub.py check --terminators FILE [--constants FILE] [--facts FILE] [--os-layer OBJ]... [--wine-fixme FILE] [--cache DIR] [--jobs N] [--json OUT] OBJ...
        (the object id and the `unit` of a constants row are the path as given)
    objstub.py terminators --cc CC [--cflag F]... [--lang c|c++] --header H... -o FILE
Inputs: ELF relocatable objects, ELF32 (ILP32; SHT_RELA with Elf32_Rela, R_AARCH64_P32_* kinds) or ELF64, AArch64, little endian. Decoder: ONE source, verify/a64access.cc (built with
the host g++ against LLVM 18's MC layer; env var A64ACCESS names a prebuilt binary): per word the registers read and written
(MCInstrDesc defs/uses/implicit, tied operands), the memory operand and the kind. Everything the T and V classes need beyond that
is read from architectural encoding fields (Arm ARM A64 top-level table), never from mnemonics. Assumption: LLVM's AArch64
instruction descriptions and disassembler are correct; the test cross-checks the decoded word set against objdump. Output: one line `obj:symbol: KIND detail`, sorted; exit 0 iff no finding.

FUNCTION UNIVERSE. Every STT_FUNC symbol defined in an SHF_EXECINSTR section, range [value, value+size); equal ranges are aliases of
one body. `uncovered`: size 0, a partial overlap or a range leaving the section, a code run of odd length, and executable bytes
outside every function unless alignment padding (all zero under $d, all NOP words under $x; a NOP only runs into the next
function's first instruction). Mapping symbols $x/$d delimit data islands: bytes under $d are data. Code that cannot be delimited
cannot be certified.

(T) TRAP CLASS. T1: a function holds BRK, HLT, UDF (word >> 16 == 0), ERET, DCPS, or a word LLVM cannot decode in a code run
(`undecodable`: the LLVM disassembler rejects it); SVC/HVC/SMC are T1 unless the object is `--os-layer` (a fact of the build recipe). The encodings that raise
unconditionally are a finite set fixed by the architecture: T1 is exact given the decoder. T2: a relocation inside the function
whose symbol is global/weak and in the TERMINATOR SET (calls, tail calls, GOT/ADRP references: ANY relocation kind, so no kind list
can be incomplete; a local symbol cannot be the library routine). `T2-noreturn-tail`: the last instruction is a direct BL (callee a symbol; control
runs off the end after a call the compiler knew not to return), except a BL of `fnvnx_reach_violation` (reach rule below); another last instruction that can fall through (not RET, B, BR*, T1) is
`fallthrough`. Reachability of a trap is NOT computed: presence anywhere in a function is a finding; every function is judged, so
a caller of a flagged function is not flagged for it. Assumption: every terminating routine of the toolchain is declared noreturn
in the headers given. TERMINATOR RULE (what is a terminator): a routine that ENDS THE PROCESS. Resuming or starting the propagation of
an exception is not process end: it transfers control to a handler (or on to the caller), and whether a handler exists is the EH
class, not class 1. The plugin applies GCC's own flags (TREE_THIS_VOLATILE = noreturn, TREE_NOTHROW) and its own EH lowering: (1) the
EH-resume routines are the explicit builtins GCC's RESX lowering emits (`builtin_decl_explicit` of BUILT_IN_UNWIND_RESUME and
BUILT_IN_CXA_END_CLEANUP: `_Unwind_Resume` on AArch64, whatever the target uses); (2) in a C++ TU a noreturn function that is not
nothrow may raise an exception by the language (`__cxa_throw`, `__cxa_rethrow`, the `std::__throw_*` helpers), so it is not process
end; in a C TU every noreturn function is a terminator (C has no exceptions). Terminators = the remaining noreturn functions
(`abort`, `exit`, `_Exit`, `quick_exit`, `std::terminate`, `longjmp`, a noreturn nothrow user function; a C user function declared
noreturn). The others are UNWINDERS, written to the set as `~name`: a call of one is no T2, and a direct BL of one at the end of a
function is no `T2-noreturn-tail` (a landing pad ends in `bl _Unwind_Resume`; a `throw` ends in `bl __cxa_throw`). NOT EXACT (stated):
the headers declare `__cxa_pure_virtual`, `__cxa_deleted_virtual` and `pthread_exit` noreturn but not nothrow, and the compiler's flags
cannot tell them from `__cxa_throw`, so they are unwinders; they are reached through vtable slots (data), not by a call a user wrote.
A C++ user function declared noreturn without noexcept is an unwinder by the same flag: its calls are not class 1 findings. The SET comes from `terminators`: a GCC plugin (objstub_plugin.cc, built by the host g++ against
`CC -print-file-name=plugin`) walks all FUNCTION_DECLs of a TU including the headers and prints those with TREE_THIS_VOLATILE
(GCC's noreturn flag) by assembler name. No header text is read.

INDIRECT TAIL. A last instruction BLR (a call through a pointer, nothing after it) cannot be judged by a symbol: it is the finding
`indirect-noreturn-tail`, unless the function is `fnvnx_reach_violation` or has a constants row of kind `exit`, AND every path of it ends
that way (exit_tail: the last word is a reachable BLR; no reachable RET, BR, ERET, SVC or trap word; no branch leaves the function).
A row of kind `exit` on any other function is `exit-mismatch`. Why a table row and not a per-site exception: the rows are the exe's own
process-exit imports, generated by the owner's manifest (one per import); a hand-written site list would be the forbidden allow-list.
NOT PROVEN: that the pointer called is the host exit slot (the contract row says so); an infinite loop is class `nodeadlock`.
`fnvnx_reach_violation` is also `reach-not-exit` if its definition does not end that way.

(W) WINE FIXME. `fnvnx_wine_fixme(uint32_t id)` (C linkage, defined by the runtime's owner; writes one line and RETURNS) marks a body that
does exactly what Wine 9.0's simplified implementation of an API does (nothing, or a fixed value). For V the BL of it is transparent: it
is no evidence of a call (rule (i)), and after it x0-x18, x30, v0-v7, v16-v31 are unknown (written with unknown values), so a result
that depends on them is unknown. A function that calls it and is otherwise V is certified iff (1) w0 at every call is a constant
(`wine-fixme-nonconstant`), (2) every path passes exactly one id (else `wine-fixme-mismatch`; wine_path_id), (3) the table
verify/wine_fixme.tsv (`--wine-fixme`; the gate reads the tree under test) has the row of that id (`wine-fixme-unknown-id`) and (4) the
result equals the row's kind/value (void: any; u32/u64: x0 is that constant; addr: x0 is the address of SYMBOL (+ OFFSET): a name at that address, or a symbol whose extent holds it; a LOCAL symbol is matched through its own symbol-table entry (the assembler often relocates against the section symbol + addend, which is resolved back to `symbol+offset` that way); u32nz<N>: below; else `wine-fixme-mismatch`). For such a body
the input rule (v) of V is replaced by the result rule: reading an input register is no evidence (a debug build spills its arguments),
because the result must be constant by wine_sim and the effects are frame stores (V (a), (b)). u32nz<N> (a result that depends only on
whether the pointer argument x<N> is zero, `this` = 0; value `a,b`): wine_sim executes the body once with x<N> zero and once with it
non-zero on abstract values {int, ARG, own-frame address, unknown}. ARG may only be copied, saved to the frame and restored (also across
the call, which clobbers x0-x18), or compared with zero (cmp/cbz/cbnz/tst, followed by csel/cset/b.cond that need only Z); the flags are
four bits with unknown N for a non-zero ARG. Certified iff the results are a for zero and b otherwise. Any other use of ARG, an
unsupported instruction, a branch on an unknown bit or more than 4000 steps: no result, `wine-fixme-mismatch` (and the V rules still
apply: a body that dereferences ARG is `wine-fixme-in-real-body`). Limit (stated): non-zero means non-zero in the tested register view
(low 32 bits or full register alike); the simulator covers MOVZ/N/K, ADD/SUB immediate, mov, cmp/tst, CBZ/CBNZ, B, B.cond, TBZ/TBNZ,
CSEL family, LDR/STR/LDP/STP of W/X registers on the own frame, NOP and RET. A function that calls it and
is not otherwise V is `wine-fixme-in-real-body`. A reference that is not a BL (address taken, in code or data; a tail B) is
`wine-fixme-address-taken` / a real body. Table rows `id<TAB>contract_name<TAB>wine_ref<TAB>kind<TAB>value`, `wine_ref` =
`wine-9.0:<path in the Wine source tree>:<function name>`: only the FORM is validated here; checking the reference against a pinned
Wine 9.0 tree is the oracle class, not done. A row without a call site is no finding; the gate result counts call sites per id.

(R) REACH. A call (BL, or B as a tail call, with a CALL26/JUMP26 relocation) of the symbol `fnvnx_reach_violation` is not a T2 and not a
T2-noreturn-tail; it is judged by the reach rule: w0 at the call must be a constant of objstub's own forward constant propagation (the
V dataflow over a CFG where a call returns and clobbers the AAPCS64 caller-saved registers), else `reach-nonconstant`; the constant must be a
fact of GATE_CACHE/reach/<git tree>/reach.facts.tsv (`fact_id<TAB>sha256`, written by the gate `reach`), else `reach-uncertified` (also
when the file does not exist); a fact with no call site in the universe is `reach-stale`; any other reference to the symbol (address
taken, in code or in data) is `reach-address-taken`. Assumption: the decoder's write sets (LLVM defs) name every register an instruction
writes (a missed write of x0 would make a constant wrong).

CACHE. Per object, CACHE/objstub/<key>.json, key = sha256 of (analysis version = objstub.py + a64access.cc, object sha256, os_layer
flag, the terminators the object's own symbols mention): the analysis reads the terminator set only through the object's symbol names, so
the key is exact and a changed header does not invalidate objects that do not mention the new terminator. Constants rows and reach facts
are applied after the analysis (judge), so they are not in the key. Objects without an entry run in --jobs processes.

(V) VACUOUS CLASS. Forward dataflow to a fixed point over the CFG of direct branches inside the function. Values: E(r) entry value
of r, K(c) constant, A(addr) link-time address (from the relocation), I input-independent unknown, D maybe-entry, S(off) own-frame
address. Only a whitelist of encodings is modelled (NOP, MOVZ/N/K, logical/add/sub immediate and shifted register, ADR/ADRP, B,
B.cond, CBZ/CBNZ, TBZ/TBNZ, RET, LDR/STR/LDP/STP of W/X on sp/x29 in the frame, scalar FMOV immediate); ANY other word makes the
function non-vacuous, so V is a subset. V iff: (a) stores only to the own frame ([sp, entry sp)); (b) no BL/BLR/BR, no branch out of
the function or into a data island; (c) no SVC/atomic/system word (not in the whitelist); (d) no E or D value is read, except stores
of x19..x30 into the frame and RET of x30 = E(30): no argument, x8 or vector argument is read, loads read only frame slots stored
earlier; (e) at every reachable RET each of x0, x1, v0 is never written (void) or is K or A. So effects stay in the frame and the
result is a function of immediates and link-time addresses: V => result and effects are input-independent (merges give I or D,
never K). Limits: (1) never-written x0 means void OR identity (`ret` is both), so such a function is V-flagged and needs a `void`
row; (2) not complete: a body that logs and returns a constant is not V (class `oracle`). `--constants` rows (unit, symbol, kind
void/u32/u64/addr/exit, value, ref): a V function is certified iff the row of (its unit, its symbol) equals the result (u32: low 32 bits
of x0; addr: canonical address; x1 and v0 unwritten); a row that matches nothing, or differs, is `constants-mismatch` (`exit`: above).
```

## verify/objstub_plugin.cc

```text
GCC plugin for objstub.py `terminators`: prints the assembler name of every FUNCTION_DECL the compiler itself
marks noreturn (TREE_THIS_VOLATILE on a FUNCTION_DECL is GCC's noreturn flag; it is set by the attribute, by
[[noreturn]] and by _Noreturn). Declarations are collected as they finish; flags are read at end of unit, so
a later redeclaration that adds noreturn to the same decl is seen. Output: one name per line, sorted, to the file
given as -fplugin-arg-objstub_plugin-out=FILE.
```

## verify/opentrace.py

```text
opentrace: prove a build stage is hermetic by checking every file read under strace.

  opentrace.py run   --manifest M --out R [--keep-trace F] -- cmd args...
  opentrace.py check --manifest M --trace T --out R
  opentrace.py key   --manifest M
Exit: 0 green, 2 red, 1 usage/tool error.
```

## verify/oraclecheck.py

```text
oraclecheck: verification of the assumption of gate "oracle" - the declaration of a test is true.
    python3 -I oraclecheck.py [--jobs N] [--timeout-s N] --out REPORT.json [TEST...]      cwd = checkout root

Runs each test file of the oracle universe (or the TESTs named, repo-relative) as `python3 -I FILE` under
`strace -f` (the call set of opentrace.py) and compares the tracked files of the tree the run opened with its entry in
DIR/tests/oracle.json. A compiled module DIR/__pycache__/NAME.<tag>[.opt-N].pyc counts as DIR/NAME.py (PEP 3147, PEP 488).
For a test, with CLOSURE = the import closure of its subject (gate_oracle.closure) and
ALLOWED = CLOSURE + the reference paths + the test file + the tracked files under its own DIR/tests:
  undeclared   tracked files opened outside ALLOWED: the subject list is not complete, or the test reads something that
               is neither under test nor a reference;
  unopened     subject files the run never opened: the test does not exercise what it declares;
  refs_in_run  reference paths the run opened (information: a reference read by the test is compared by the machine,
               one that is not is a text for the reader).
A test passes iff it exits 0, undeclared and unopened are empty. The report states these facts per test; it ranks
nothing and excuses nothing. Exit 0 iff every test passes, 2 otherwise, 1 on a usage or tool error.
This is a verifier (after-merge, farm/runner.sh job `oraclecheck`), not a gate: it needs strace and runs every test.
```

## verify/puregate.py

```text
puregate: DEFAULT-DENY static gate for rule R12-T (a build stage's output is a function of its declared inputs).

    python3 -I puregate.py check PATH... [--json OUT.json]      exit 0 clean, 2 violations, 1 usage error
    python3 -I puregate.py audit-stdlib                          check the PURE table (A-stdlib): a report, not a gate

PATH: .py file or directory (sorted walk); shell files (suffix .sh/.bash or shell shebang): `shell-unanalysable`. Output
`file:line: RULE symbol`, at most 200 lines (JSON: all). Policy tables: puregate_policy.py (data only; rule classes there).
"Accepted" means: no Python code in the checked files can read an undeclared channel (clock, entropy, host identity,
directory/set order, file times/inodes), UNDER THE ASSUMPTIONS below. Anything not analysable is a violation, never a pass.
Names resolve through import bindings with Python scoping; a name bound by an import and by anything else in one scope is
`dynamic`; an escape of a module/class that carries rules (other than as an attribute base) is `dynamic`.
Rules: the class rules of POLICY/ATTR (clock inode entropy unsorted-listing host completion-order dynamic) plus
  import-unlisted  every import must be (a) PURE stdlib, (b) RESTRICTED stdlib (only allow-listed members, else the rule
                   of the member or `member-unlisted`), (c) PROJECT, (d) EXTERNAL (A-ext), or a banned module whose uses
                   are ruled. Project rule: `import a.b` / `from a.b import x` resolves a/b.py or a/b/__init__.py below
                   the importing file's directory or a directory PATH; `from .m import x` below the importing file's
                   directory (one level up per extra dot); `x` must be a submodule or (module a real file) a name in it.
                   The file must be among the checked files (a namespace directory counts only for submodules); a
                   checked file with the name of a listed module is ambiguous. Project modules are checked in the same
                   run, reported under their own path.
  stat-result      os.stat/lstat/fstat or a .stat()/.lstat() method call: only as the value of `.st_size`/`.st_mode`.
  unsorted-set     set literal/comprehension/set()/frozenset() (also |,&,-,^ and union..copy of those) directly iterated,
                   unpacked, starred, or passed to list tuple enumerate iter map zip filter reversed, .join .extend
                   .fromkeys, or .pop().
  clock (archives) gzip/zipfile/tarfile only for reading; writing needs gzip mtime=<not None>, else `clock`; a mode that is
                   not a constant string, or a call with *args/**kw, is `dynamic`.
  dynamic          also vars() dir() with any argument and attributes __dict__ __getattribute__ __subclasses__ __globals__
                   __code__ __self__ __closure__ f_globals f_back ...; sorted/min/max key=id|hash is `entropy` (id, hash).
Counter set_iterations_unproven (not a violation): for/comprehension iterations over a bare Name or Attribute, i.e. sets
held in variables whose origin the gate cannot see (an upper bound: every such iteration is counted). Floats: out of
scope (IEEE operations are deterministic).
ASSUMPTIONS (named; what "accepted" does NOT prove):
  A-stdlib   modules in the PURE table and the allowed members of RESTRICTED ones do not read clock/entropy/host identity
             (checked by `audit-stdlib`, which runs these rules over their sources; C modules are listed unaudited).
  A-ext      the EXTERNAL modules (summary line: `external modules assumed pure`) are pure.
  A-hashseed PYTHONHASHSEED=0 and str/bytes/int/tuple-of-those set elements: set iteration order is a function of the
             insertion sequence (sets held in variables; see the counter).
  A-path     at run time the project imports resolve to the files this gate resolved (no other sys.path content).
  A-child    subprocess children, the files read and the environment are declared inputs (governed by the stage document).
  A-text     strings built at run time are not file names under the host-identity trees.
```

## verify/puregate_policy.py

```text
puregate policy tables: DATA ONLY (the rules that read them are in puregate.py, which documents the assumptions).

Rule classes: clock (wall/monotonic time, file times), inode (inode/device/link identity), entropy (random, id, hash),
unsorted-listing (directory order), unsorted-set (set iteration order), host (machine identity/load), completion-order
(results in finish order), dynamic (code or names the gate cannot see), import-unlisted (module outside the tables
below), member-unlisted (member of a restricted module outside its allow-list), stat-result (stat result used for
anything but .st_size/.st_mode), syntax / shell-unanalysable (not analysable).
```

## verify/recipe.py

```text
recipe: build a product root from its recipe `build.json` ("fnvnx-recipe/1", contract in RECIPE.md) on the stage machinery:
every step is a sandbox stage (sandbox.py) keyed by its declared inputs, cached in the store (cas.py).
    python3 -I recipe.py build --root ports/fnv-nx/X --store S --out DIR [--jobs N]     (cwd = checkout root; exit 0 green, 2 red)
    python3 -I recipe.py check --root ports/fnv-nx/X                                    validate only
Steps: (1) generated stages (python runtime, runtime.py), (2) one unit stage per source: .c/.cc/.cpp/.cxx = unitstage.Build (PP on
name-complete views, then CC on the preprocessed text), .s/.S = unitstage.Build too (.S: PP then CC; .s: one stage on its own file), (3) the link stage
(`ld` of the compiler, objects + script as inputs). Tools (compilers, ld) are runtime.py `tool` runtimes: declared inputs, no host /usr.
RULE units: every tracked *.c *.cc *.cpp *.cxx *.s *.S under the root, and every generated output with such a name, is a unit.
RULE refusal set R: an option of GNU ld's grammar that lets a link accept missing or substituted symbols. Matched by OPTION SYNTAX:
  strip one or two leading dashes, cut at `=`; a name that is a prefix (getopt_long abbreviation) of a refused long name is refused (a
  single-dash name needs >= 2 chars); `-z undefs|muldefs` as `-z X` or `-zX`; `-Wl,a,b` and `-Xlinker a` are split into the options
  they pass; `@file` is refused (cannot be analysed). An ambiguous abbreviation is refused too (ld would reject it anyway).
EXTERNAL inputs (key "external", RECIPE.md): fetched only by exact commit from ARTIFACTS_URL into a bare cache (--ext DIR, shared by
  builds that must not share stores), checked out, ingested; the CAS tree hash must equal tree_sha256 (else the build is red naming
  the external). The tree is a declared input at "at" of EVERY stage (the tool runtime lists it), so stage keys cover its hash. A tree
  is the content-pinned INPUT, not a build output: sharing the fetch between two cold builds shares nothing that a build computes.
  compiler {"external": N, cc, cxx}: the tree is declared to runtime.py tool as HOST@AT (loader closure resolved as inside the sandbox, RECIPE.md).
Outputs in --out: objects.json ("fnvnx-objects/1": root, objects[{unit,object,sha256,os_layer}], elf, constants, defined [{symbol,bind,type,object}] and undefined [symbol] (ELF .symtab of the objects, struct), stage_keys, stages
  {run,hit}, status green|red, failure {stage,exit,stderr tail 20} / failed_units when red), obj/<unit>.o, <name>.elf, constants.tsv.
Assumptions: the compiler and ld are deterministic given the flags of unitstage/toolflags (A-env); the sandbox leaves the channels of
  sandbox.OPEN_CHANNELS open (see gate_determinism.py for which of them two cold builds expose).
```

## verify/runtime.py

```text
runtime: a python interpreter and everything it loads, as declared sandbox inputs (rule R12), plus stage documents running a
python script on it. The sandbox has no host /usr, so the interpreter is a content-hashed input (runtime_id is in the key).

    runtime.py python --store S [--python P] [--site PKG]... -o runtime.json
    runtime.py tool --store S --exe AT... [--tree DIR|HOST@AT]... -o toolrt.json
    runtime.py stage --store S --runtime runtime.json --name N --class build|verification [--input AT=HOST]... [--output D]...
                     [--env K=V]... [--cwd D] -o stage.json -- SCRIPT args...

`python` assembles, by rule (no guessing), into the CAS store:
 (a) the interpreter file (resolved real path) at its real path, alone in its directory input (so PATH=<its dir> is under an input).
 (b) its dynamic-loader closure, parsed from the ELF headers (struct): PT_INTERP, then transitively DT_NEEDED. Search order of
     glibc ld.so(8): DT_RPATH of the object (only without DT_RUNPATH; then the RPATHs of the loading chain), DT_RUNPATH of the
     object, the directories of /etc/ld.so.conf (+ include lines), the defaults /lib /usr/lib /lib64 /usr/lib64; $ORIGIN is the
     object's directory; $LIB/$PLATFORM and unresolved names are errors. A symlink cannot be an input, so each file is ingested
     with the real CONTENT and placed at the name the loader looks up (the path where the search found it, e.g. the soname).
     Libraries found inside an included tree need no input. Roots of the closure: the interpreter, every ELF file
     of the extension-module directory (sysconfig DESTSHARED) and of every --site package.
 (c) the standard library: the target interpreter runs once with `-s -B -c` under the stage environment (this is the builder,
     not a stage) and reports sys.prefix, sys.exec_prefix, sys.path, sys.version. Every sys.path entry that exists is included
     whole (directory = tree, zip = file), except directories named exactly `__pycache__` (stages run with -B, inputs read-only). A sys.path entry that is a symlink is kept as the symlink if it
     lies inside another entry (here site-packages -> dist-packages), else an error. Inputs nested in another input are dropped.
 (d) each --site PKG (top-level name): importlib.util.find_spec in the target with `-s` (user-site is off: not found = error).
     Its package directory (or module file) is covered by (c), or else added at its real path.
Output: {"format":"fnvnx-runtime/1","python","version","inputs":[{"at","tree"}] sorted,"env","runtime_id":cas.stage_key(inputs)}.
`tool` is the same for any native tool (a compiler driver): each --exe AT is placed at the path AT as the tool looks it up (a
symlink name such as /usr/aarch64-linux-gnu/bin/as keeps its name; the content is the resolved file; an exe inside a --tree is already there), alone with the other exes of
its directory in a directory input (so PATH can name it); its loader closure is rule (b). Each --tree DIR is ingested whole at
DIR (or the directory HOST of this machine at the sandbox path AT: HOST@AT; the loader closure is then resolved as the loader
resolves it inside the sandbox: paths under AT are the files under HOST, a path under HOST outside AT does not exist, every other
path is a declared host input; an unresolvable library is an error naming object and name); every x86-64 ET_EXEC/ET_DYN ELF file in it is a further root of the closure (other ELF files, e.g. foreign-arch objects, are
data). The caller names the exes and trees (unitstage.gcc_set derives them from the compiler's own -print-* output).
Output: {"format","kind":"tool","exes","inputs","env":{},"runtime_id"}.
`stage` writes the "fnvnx-stage/1" document (+ "tool":"python"): argv = [python,-s,-B]+given, env = runtime env + TZ=UTC LC_ALL=C
SOURCE_DATE_EPOCH=0 COLUMNS=80 PATH=<interpreter dir> + given. PYTHONNOUSERSITE is left out of the stage env (toolflags allows only
PYTHONHASHSEED/PYTHONDONTWRITEBYTECODE; -s does the same job). sandbox.check_doc ACCEPTS the optional key "tool" (and it is part of
the stage key), so the one document serves toolflags.py and `sandbox.py run` alike; there is no second NAME.run.json.
```

## verify/sandbox.py

```text
Hermetic build sandbox (rule R12 BUILD-PURE): a stage sees ONLY its declared inputs, so its outputs are a
function of the stage key. Needs root, Linux x86-64. Stage document "fnvnx-stage/1":
  {"format","stage","argv":[abs tool, args], "cwd","env":{COMPLETE env},"inputs":[{"at","tree"}],"outputs":[dirs]}
  optional "class": "build" | "verification" (part of the key; verdict.py provenance requires it); optional "tool"
  (the declared tool kind toolflags.py checks; part of the key).
key = cas.stage_key(document + {"sandbox": {sandbox.py sha256, cas.py sha256}}). Results: STORE/keys/<key>/result.json.
Sandbox: new mount/net/uts/pid/ipc namespaces; tmpfs root; inputs hardlinked from the CAS and bound read-only;
outputs fresh rw dirs; fresh /proc (time/stat/sys masked), /dev = null+zero, tmpfs /tmp; hostname fnvnx; no interface up;
stdin /dev/null; umask 022; ASLR off; no core files; all capabilities dropped; seccomp: getrandom -> ENOSYS (x32 ABI and foreign arch killed).
Measured (tests/test_sandbox.py case 9), NOT closed, listed in result.json "open_channels":
  wall_clock           clock_gettime/time/statx show real time (vDSO). PR_SET_TSC=SIGSEGV + seccomp on the clock syscalls
                       was tried: dynamic date/python/gcc/make crash at startup (SIGSEGV), coarse clocks stay readable. Dropped.
  entropy              getrandom is closed (ENOSYS) and /dev/urandom, /dev/random are a constant 1 MiB file (so os.urandom
                       and Python's multiprocessing import work and return the same bytes in every run), but AT_RANDOM and
                       RDRAND still give fresh bytes.
  cpu_count_scheduling sched_getaffinity/nproc show the host CPUs; order of parallel children is not controlled.
  kernel_version       uname shows the host kernel release.
  memory_size          sysinfo shows the host memory size.
  inode_readdir_order  inode numbers differ per run; readdir order is filesystem-hash order (not sorted).
  procfs               /proc/self/stat start time counts from host boot; other /proc files not masked remain.
Closed: network, hostname, pid numbering (command is pid 1), host files, environment, uid/gid (0/0), ASLR, leftovers.
```

## verify/sdcheck.py

```text
sdcheck.py - rule: the SD tree equals the reference manifest.

  manifest GAME_DIR -o game.manifest.json     file list (path, size, sha256) of the reference game install
  package  PKG_SD_DIR -o port.manifest.json   the same for the built package's sd/ tree (the port's own files)
  check --game G --port P --layout L SD_ROOT --fs fat32|exfat [--no-hash] [--json F]
  emit-c --game G --port P --layout L -o sd_manifest.inc

Exit: 0 green, 2 error, 3 incomplete (size-only pass). The manifest of the game is private (kept in the private
repository); nothing here carries game data. FAT32 limit: a file may hold at most 2^32 - 1 bytes (the spec text
"size >= 4 GiB - 1" is read as "size > 2^32 - 1": a file of exactly 2^32 - 1 bytes is representable).
```

## verify/toolflags.py

```text
toolflags: gate for rule R12-T class 2 (external tools): a stage whose command is a compiler/assembler/linker/archiver/
interpreter is deterministic BY CONSTRUCTION OF ITS INVOCATION when it carries the tool's documented reproducibility
switches and the environment that removes the documented ambient inputs.

    python3 -I toolflags.py check STAGE.json... [--root DIR] [--json OUT.json]   exit 0 clean, 2 violations, 1 usage/unreadable
    python3 -I toolflags.py policy                                                 print the policy table (toolflags_policy.py)

Input: stage documents "fnvnx-stage/1" (sandbox.py) plus "tool", the DECLARED kind (never guessed from a file name): gcc as
ld ar objcopy python msvc-cl msvc-link msvc-lib wine; sh/make: `tool-unanalysable` (child commands are not visible in argv);
others `tool-unlisted`; none `tool-undeclared`. wine needs "wine_tool" (an msvc-* kind) applied to the argv after wine.
GNU response files `@F`: F under a declared input, read from ROOT + F (--root), expanded before the rules run (no --root,
unreadable or a cycle: `response-file-unreadable`). Output `STAGE: RULE detail [citation]`, summary, JSON "fnvnx-toolflags/1".
Declared dependencies, NOT checked here: "no_date_time_macros": {"checked_by": NAME} names the stage/gate proving the sources
use no __DATE__/__TIME__ (VC9 has no switch); "coff_normalize"/"pe_normalize": the consumer normalizes time stamps
(verify/coffnorm.py); a python script must itself pass puregate.
ASSUMPTION A-env: external tools invoked this way are deterministic. It rests on each tool's reproducible-builds
documentation; the gate enforces the invocation, it does not prove the tool.
```

## verify/toolflags_policy.py

```text
toolflags policy table (data only): key -> (kind, requirement, citation). The reviewable statement of assumption A-env.
Rows citing VC9 are UNVERIFIED (no VC9 here); GCC 13.3 / binutils 2.42 rows were checked against the installed --help.
```

## verify/unitstage.py

```text
unitstage (level L0 of the fast loop): one C++ translation unit as two hermetic stages (sandbox.py), so an object is never
recompiled when nothing it reads changed.

    unitstage.py build --store S --toolrt toolrt.json --cxx /abs/g++ --src-root HOST@/src --include HOST@/inc/0 ...
                       --unit REL.cpp ... --flags-file FLAGS.json --report R.json [--jobs N] [--cold]
    unitstage.py gcc-set --cxx /abs/g++       print the --exe/--tree arguments of `runtime.py tool` for that compiler
RULE: what a preprocessor run reads is exactly (1) the CONTENT of the files it opens and (2) the EXISTENCE answers for the paths it
  probes (#include search, __has_include, __has_include_next). A stage is given both, exactly.
  PP stage  `g++ FLAGS -E -MD -MT x -MF /out/deps.d -o /out/unit.ii /src/UNIT`, inputs: the tool runtime (runtime.py tool) and VIEWS
            of the source root (/src) and the include roots (/inc/0..), see unitview.py: every name of the real tree, real content
            only for the files of a set C (the closure), every other file a placeholder `#error fnvnx: file outside the declared
            closure`. The key is f(name trees, content of C, compiler, flags).
  CC stage  `g++ FLAGS -fpreprocessed -c -o /out/unit.o /in/unit.ii`, inputs: the tool runtime and unit.ii. The key is f(preprocessed
            text, compiler, flags): a comment-only header edit re-runs PP for the units that read it, CC is a hit.
Closure discovery (this runner, outside the sandbox, a pure function of stage results): C0 = a hint (STORE/hints/<sha256 unit>.json,
  the last accepted closure of that unit path) or, without one, every file (the view is the real tree). Run PP. Failure: if the
  compiler printed the placeholder's own #error for files not in C (parsed from the `FILE:L:C: error:` prefix of that message), add
  them to C and run again (each pass adds >= 1 file: terminates); any other failure is the unit's real error, reported, nothing
  stored as success (no hint). Success: D = files of deps.d (resolved like the kernel does, symlinks followed, `..` on the real
  directory). If D != C run again with C = D (the minimal view) and require deps == D and the SAME unit.ii hash (else exit 1 with both
  hashes); the accepted result is the run on the view of D. ARGUMENT that the hint never changes the result: the accepted run opened
  only files of D, all real, and saw the true name tree, so every include search and probe got the answer it gets on the real tree and
  every opened file had its real content; its output equals the output on the real trees. A different hint only changes how many runs
  lead to D. ASSUMED about GCC: it reads the content of a file only by lexing it (then the file is in deps.d) or it fails; (a
  #pragma-once identity check on a file that is not included would not be caught by the placeholder, only by the equality assert).
Languages by suffix (LANG): .c gets `-x c` (PP) and `-x c -fpreprocessed` (CC); .S gets `-x assembler-with-cpp` and `-x assembler` (CC on the
  preprocessed text, the same two stages); .s has no PP stage (its CC stage reads the unit alone, a `.include` fails). `--include` may also be
  an absolute sandbox path inside an input of the tool runtime (an external tree): it is passed as `-I<path>` in the given order; its content is
  covered by the runtime's input hash (not by the closure).
Flags appended by this tool: -I/inc/N (PP only), -frandom-seed=<sha256 of the unit path>, -ffile-prefix-map for /src and each /inc/N,
  -Werror=date-time (the macros expand at PP). Both stage documents must pass toolflags.check_doc. Caller flags may not contain -I*,
  -o, -c, -E, -S, -M*. Sandbox paths are fixed, so keys are host-independent; line markers of unit.ii hold only /src, /inc/N paths.
  Documents are kept at STORE/stage-docs/<aa>/<key>.json. --cold runs a stage again and fails if the result differs.
Report "fnvnx-unitstage/2": per unit status (ok|pp-failed|cc-failed), closure (sandbox paths of real files), files, pp_key, pp_status
  (hit if every iteration hit), pp_seconds, pp_iterations, ii_bytes, cc_key, cc_status (hit|run|skipped), cc_seconds, object (tree hash
  of /out), name_tree_id (hash of the placeholder-only views = the pure name trees), error (the last 20 lines of the failing stage's stderr); totals. Exit 0 ok, 2 a unit failed, 1 usage.
```

## verify/verdict.py

```text
L4 verdict: gate certificates, merge gate, build/verification separation, rule-defect queue. stdlib only.
Run as `python3 -I verdict.py CMD ...`. Exit codes: 0 ok, 1 usage/format error, 2 red/blocked, 3 merge conflict.
  run         --repo R --commit C --gates GATES.json --out V.json [--budget-s 300] [--env-id H] [--cache DIR]
              Runs each gate (argv, cwd = clean git worktree of C, env + GATE_OUT + GATE_COMMIT [+ GATE_CACHE]), in file order.
              Gate contract for the environment: GATE_OUT (result directory), GATE_COMMIT (commit under test) and, when --cache is
              given, GATE_CACHE (absolute path of a caller-owned directory that outlives the run). A gate may store results in
              GATE_CACHE only under a key that covers everything it reads (its inputs' content, the declared environment, its own
              arguments); it must work, and give the same verdict, without GATE_CACHE. A gate writes
              {"format":"fnvnx-gate/1","name","status","universe","certified","uncertified":[<=50 ids],"detail"} at GATE_OUT/<result>.
              Gate green iff exit 0, valid result, name equal, status green and certified == universe (completeness of
              certificates, not a run counter; certified > universe is invalid). Verdict green iff >=1 gate and all gates green.
              over_budget is reported only; it never changes the verdict.
  store       --verdict V.json --verdicts VDIR   copy to VDIR/<tree>.json if self-consistent (red verdicts are stored too).
  merge       --repo R --into B --from REF --verdicts VDIR --gates GATES.json [--prev-verdict V.json] [--message-prefix T]
              Merge result computed with `git merge-tree --write-tree` (no worktree touched). Needs VDIR/<tree>.json: green, same
              tree, same gates_sha256 as GATES.json. Ratchet (exact and nothing more): the set of gates never shrinks, i.e. every
              gate name of the previous green verdict must be present in the new one (uncertified == 0 is already required by the
              gate rule). Then a two-parent commit via commit-tree and refs/heads/B moved by update-ref compare-and-swap.
              A fast-forward still needs the verdict for REF's tree (a merge commit is made).
  provenance  --store S --docs DIR   Verification never feeds the build: every stage document needs "class"; a build stage whose
              input equals or CONTAINS (cas.listing) an output root of a verification stage, or of a tainted build stage
              (transitively), is tainted. Exit 2 lists chains. Stage results: S/keys/<key>/result.json (sandbox.py).
  defect / defects   rule-defect queue: content-addressed JSON entries, verification-class data only.
```
