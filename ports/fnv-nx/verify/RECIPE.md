# Build recipe of a product root (`build.json`, "fnvnx-recipe/1")

A product root of `admit.json` names its recipe (`"recipe": "<root>/build.json"`). Gates `build` and `determinism` read it; nothing else
decides how the root is compiled. Every step is a sandbox stage (sandbox.py): it sees only its declared inputs, its result is cached by
the hash of the stage document + tool code. All paths are root-relative unless marked repo-relative.

```json
{"format": "fnvnx-recipe/1",
 "compiler": {"cc": "aarch64-linux-gnu-gcc-13", "cxx": "aarch64-linux-gnu-g++-13"},
 "flags": {"c": ["-O2"], "cxx": ["-O2"], "asm": []},
 "include": ["ports/fnv-nx/X/inc", "ports/fnv-nx/X/gen", "/ext/ft/include"],
 "units": "all",
 "generated": [{"tool": "ports/fnv-nx/contract/gen.py", "args": ["/out/gen/t.h"], "inputs": ["ports/fnv-nx/X/table.tsv"], "outputs": ["gen/t.h"]}],
 "os_layer": ["os.S"], "tests": [{"name": "t1", "units": ["tests/t1.cc"]}], "constants": "constants.tsv", "contract_names": "contract.tsv",
 "link": {"script": "link.ld", "output": "x.elf", "flags": ["-m", "aarch64linux32"], "roots": ["ports/fnv-nx/other"],
          "libs": {"external": "tc", "dirs": ["lib"], "names": ["c"], "startfiles": ["lib/crt1.o"], "endfiles": ["lib/crtn.o"]}},
 "plugins": [{"name": "fnvnx_order", "source": "ports/fnv-nx/toolchain/order/fnvnx_order.cc", "args": {"args": "rtl", "object": "msvc", "operator": "rtl", "assign": "msvc"}},
             {"name": "fnvnx_ftoi", "source": "ports/fnv-nx/toolchain/ftoi/fnvnx_ftoi.cc", "args": {"mode": "native"}}],
 "external": [{"name": "ft", "repo": "fnv-nx-build", "ref": "mirror/freetype", "commit": "<40 hex>", "tree_sha256": "<64 hex>", "at": "/ext/ft"}]}
```

## Keys (any other key is an error; all problems are listed at once)
- `compiler`: driver names under `/usr/bin` that env.json provides, or `{"external": NAME, "cc": PATH, "cxx": PATH}` (paths inside the external).
- `flags`: per language. Refused: `-I*` (use `include`), `-o -c -E -S -x -M*`, and the refusal set R below.
- `include`: repo-relative directory inside an admitted root (tracked files, or generated outputs of this root), or an absolute path inside an
  external's `at`. Included files are inputs by content; a file the unit reads that is not in them does not exist for it.
- `units`: must be `"all"`. A `.S` unit is preprocessed as its own stage like a C unit (its key covers exactly the headers it includes);
  a `.s` unit sees nothing but its own file (`.include` fails: use `.S` and `#include`). RULE: every tracked `*.c *.cc *.cpp *.cxx *.s *.S` under the root, and every generated output with such a name, is
  compiled. A file that must not be compiled does not belong in the root; there is no `exclude`.
- `run`: see "Run stages" below.
- `tests`: the declared test binaries of the root, `[{"name": N, "units": [root-relative sources]}]` (no double in tests, CLAUDE.md of 2026-11-11 night).
  Every source of the root is a PRODUCT unit or a unit of exactly one test (a unit in two tests, or not a source of the root, is an error).
  A test is linked by the recipe as `test-N.elf` from ALL product units of the root plus its own units, with the root's `link` script and flags
  (so `tests` needs `link`); it never replaces a product file. A test unit is not in `objects`/`defined`/`units` of the result: the result has
  `tests` [{name, units, objects, linked, defined, elf}]. The stub gate (`objstub`, doubles.py) is red when a test defines a symbol that any
  product root defines (global, weak or unique; no allow-list), when `linked` lacks a product unit, or when two product objects of one root define
  the same GLOBAL symbol.
- `generated`: python generators run as sandbox stages before compilation, in list order. `tool` is a tracked `.py` of an admitted tool or product
  root (the whole root is mounted at `/tool`, the script at `/tool/<path in its root>`), `inputs` are tracked files or directories (repo-relative;
  mounted at `/in/<repo path>`; they may not nest), the generator writes ONLY `/out/<output>` (the set of produced files must equal `outputs`).
  Outputs are root-relative, never tracked files, and appear in the root's view for `include` and as units when named like a source.
  The script must itself pass `puregate`; wall-clock reads and other open channels of the sandbox make `determinism` red.
  An `inputs` entry may also be `{"stage": "<this root>:gen:I", "path": OUTPUT}`: the declared output of an EARLIER generator I of the same recipe (red: a later
  one, a path it does not output, another root's stage; another root's output goes through `uses`). It is mounted at `/stage/<OUTPUT>` and its content is in the
  stage key. A chain of tools (`contract/facts`: exefacts, n1, objects, registry, bind, bodysel, nativerows) is one recipe whose stages cache one by one.
  The stage's `/dev/shm` is a private empty tmpfs that is writable (POSIX semaphores of `multiprocessing`); nothing in it survives the run and `/tmp`, the tool tree and the
  inputs stay read-only.
- `os_layer`: units that implement the OS interface (may contain SVC). They are flagged `os_layer: true` in objects.json for `objstub --os-layer`.
- `game`: root-relative directory whose units are the game's (decomp) objects; the contract gates (gate_contract.py) require it of a root that links, no default.
- `constants`: the constants table, a tracked file or a generated output, copied to `constants.tsv`. Rows
  `unit<TAB>symbol<TAB>kind<TAB>value<TAB>contract_ref`, key (unit, symbol), `unit` = the root-relative unit path as in `objects.json`
  (so two local symbols of equal name in two objects do not collide). Kinds: `void`, `u32`, `u64`, `addr` (the value of a function that
  objstub proves constant; `value` empty for void) and `exit`: the function is the implementation of a process-exit contract entry,
  `contract_ref` names the import. A row of kind `exit` is certified only if the function really ends in a call through a pointer
  (BLR as its last word) on every path; it is a contract row and not a per-site exception because it is the table of the exe's own
  exit imports, generated by the owner's manifest, one row per import (the one function `fnvnx_reach_violation` needs no row).
  Every row must match a function that objstub judges; a row that matches none is a finding.
- `contract_names`: optional, a tracked file or a generated output, copied to `contract_names.tsv`: rows `symbol<TAB>body_id` generated by
  the owner (an import name and the body that implements it). Gate `linkstrict` rule (c): every name is defined in the linked ELF with a
  nonzero size, and two names with different body ids never share an address.
- `link`: the guest ELF, null = no ELF. A link is the stage `ld:<root>` and the post stage `ldcheck:<root>` of the section "Link: the guest ELF"
  below; `flags` carries the emulation only, `roots` and `libs` are optional.
  `kind` is `pie` (the ILP32 guest image, the default) or `exe`; a root of target guest cannot link an `exe`. `roots` lists distinct admitted `product`
  roots of the same target, never the root itself; their stored objects are declared inputs of the link stage (so their content is in its key) and a
  listed root is built without its own link. A root of class `product` lists base roots and the per-target variant of its own target; a root of class `test` lists any root, variants included
  (`root@NAME`). A variant is one concept: `variants: {NAME: {"target"?}}` in the root's admit.json entry, its flags in the recipe's `variants`,
  addressed as `root@NAME` (a sanitizer build is a flags variant of the same root). Whatever the flags, `linkstrict` judges the linked output: no
  undefined symbol of any binding.
- ABIs: the recipe names the ABI in its flags, and the build reads ELF32 and ELF64 AArch64 objects alike. ILP32 (the product): `-mabi=ilp32`
  in `flags.c`, `flags.cxx` and `flags.asm`, and `-m aarch64linux32` in `link.flags` (the emulation `gcc -mabi=ilp32 -###` passes to
  `ld`; it is required: a link is the ILP32 guest ELF). The objects are ELF32 (`SHT_RELA` with `Elf32_Rela`, relocations `R_AARCH64_P32_*`),
  the linked ELF is ELF32. LP64 is the default, with no flag.
- `external`: content-pinned inputs (see below).
- `plugins`: GCC plugins of the C++ front end (see "Plugins"). `flags` may not hold any `-fplugin*` token.

## Plugins (`fnvnx_order`, `fnvnx_ftoi`, ...)
`fnvnx_ftoi` is one plugin, class C (float -> integer), two modes chosen by the root's `cxx_origin` and never declared freely: `recipe.plugin_args(cxx_origin)` gives `mode=native`
(the MSVC `_ftol` rewrite, skipped inside functions with `__attribute__((fnvnx_f2i))`, which the plugin registers), `mode=lifted` for a lifted origin (no rewrite; a
conversion, conversion builtin or float-reading asm outside an `fnvnx_f2i` function is an error). The recipe adds `-fplugin-arg-fnvnx_ftoi-out=/out/unit.f2i.json` to every
CC stage (`PLUGIN_OUT`); the report (`fnvnx-f2i/2`: sites, rewritten, ranged, helper, helpers, findings; gate fpexact) is kept next to the object and listed in `objects.json` as `f2i` of the unit.
Rule: a root that uses an attribute declares the plugin that registers it (guest roots compile with `-Werror=attributes`).
Our compiler is the pinned g++-13 with our two plugins. Their sources are tracked under the tool root `ports/fnv-nx/toolchain` (no build script:
the recipe is the only way they are built). `recipe.py` is the ONLY declaration of what it fixes for a plugin: its source (`PLUGIN_SOURCES`, `<dir>/<name>.cc`, a tracked file of the tool root), its inputs
(`PLUGIN_INPUTS`: the tracked files the source includes) and its arguments (`plugin_args(cxx_origin)`, which become `-fplugin-arg-<name>-<key>[=<value>]`). An entry of a
root's `plugins` is `{"name": PLUGIN}` and nothing else, in the order of `PLUGIN_SOURCES`; the stage keys and mounts take source, inputs and arguments from recipe.py
(`recipe.plugin_decls`). An entry that carries `source`, `inputs` or `args` is an error ("plugin X: inputs are fixed by recipe.py (PLUGIN_INPUTS)"), never compared; a name outside
`PLUGIN_SOURCES` is an error. So a change of an input of a plugin (for example `f2i_builtins.inc` of `fnvnx_ftoi`) changes the key of every root that loads it and no build.json needs editing. The host driver under `/usr/bin` that builds
them is `recipe.PLUGIN_CXX`, DERIVED from env.json (the one pinned package `g++-N-x86-64-linux-gnu` provides `x86_64-linux-gnu-g++-N`; the bare `g++-N` prints no path for `as`/`ld`), the GCC version of `compiler.cxx`. A `plugin_cxx` in a build.json is red: no hand-typed compiler name.
- Stage `plugin:<name>` (sandbox, class build): `recipe.PLUGIN_CXX -shared -nostdlib -fPIC -fno-rtti -fno-exceptions -O2 -I<compiler.cxx -print-file-name=plugin>/include
  crti.o crtbeginS.o SOURCE crtendS.o crtn.o`. Declared inputs: the driver's tool runtime, the plugin headers of the target compiler
  (gcc-13-plugin-dev-aarch64-linux-gnu of env.json), the source, crti.o/crtn.o of libc6-dev. Key = content of these + driver + flags, like every stage.
  The `.so` is stored in `GATE_CACHE/store`, copied to `OUT/plugins/<name>.so`, listed in `objects.json` (`plugins`: name, sha256, path).
  It is linked against no library (symbols come from the compiler that loads it; `-fno-exceptions` because `_Unwind_Resume` is not exported by cc1plus).
- CC stage of every C++ unit (only C++: C and assembler units load nothing): the `.so` of each plugin is a declared input at `/plug/<name>`,
  and the stage argv holds `-fplugin=/plug/<name>/<name>.so` and the `-fplugin-arg-*` flags, so they are part of its key. The preprocessor stage loads
  no plugin. `toolflags` accepts `-fplugin=P` only under a declared input; it is not relaxed.
- Memory ordering has no option: `fnvnx_order` makes every access to memory that is not a proven local `ldar`/`stlr` (x86 TSO), volatile or not.
  `ORDER_MODE` of the old compile.sh no longer exists in the plugin (it rejects an unknown argument).
### Guest roots: the game's compiler (MSVCMODEL1)
**Flags.** The recipe, not the build.json, supplies every semantic flag: data model and ABI (`-mabi`, `-march`, `-mtune`, `-fPIC`, `-ftls-model`, char
signedness, `-fshort-wchar`, `-ffixed-x19..x27`), outline atomics, overflow, aliasing, null checks, lifetime-dse, FP (`-ffp-contract=off`,
`-frounding-math`, `-fno-math-errno`), language model (`-fms-extensions`, `-fcheck-new`, `-fno-sized-deallocation`, `-fno-devirtualize`), EH tables, stack
protection, jump tables, builtins, `-U_FORTIFY_SOURCE`. They are `flags.semantics` of `guest_compiler.json`, appended to every guest compile stage (C,
C++, asm) in that order BEFORE the root's own flags, minus the flags the pinned driver itself says do not apply to that language (`recipe.semantics_for`:
one run of the driver on an empty input; its diagnostic "command-line option 'X' is valid for C++/ObjC++ but not for C" is the rule; an unknown flag is an
error). A guest build.json may carry only per-root classes (`recipe.PER_ROOT`): `--param` with `ggc-min-expand=N` / `ggc-min-heapsize=N` (the compiler's own collector knobs, no effect on the emitted code), `-W*` (not `-Wa,` `-Wl,` `-Wp,`), `-D*`, `-std=*`, `-O0..3/s/g`,
`-g*`, `-ffunction-sections`, `-fdata-sections`; any other token is refused by `guest_errors` (an allow-list of classes, no list of semantic flags to
keep in sync). `-DFNVNX_SDD=1` is per root (a native C++ root must hold it).
Every product root's admit.json entry declares `"target": "guest"` (the game's code) or `"host"`; missing = invalid recipe = red `build`. A guest root's
compiler, for C and C++ alike, is the external compiler pinned in `verify/guest_compiler.json`
(`{"format": "fnvnx-guest-compiler/1", "external": {EXT_KEYS}, "cc", "cxx", "flags": {"semantics": {"common": [flag], "lifted_extra": [flag]}}}`; `flags.semantics` is the one source of `recipe.SEMANTICS_FLAGS` (`common`) and `recipe.SEMANTICS_LIFTED` (`common` + `lifted_extra`: `-ffreestanding -fno-exceptions -fno-asynchronous-unwind-tables -fno-unwind-tables`, what lifted code is built under). `-fno-exceptions` removes host C++ EH from lifted units only. The exe's MSVC C++ EH and SEH stay implemented by the lifter through the exe's own EH tables (owner order of 2026-11-10). Nothing in the lifted flag set changes that.; `recipe.semantics_set(cxx_origin)` picks the set of a root by its declared origin, `recipe.semantics_for(driver, language, origin=)` derives the per-language set from it; `verify/guest_flags.py --lang L --origin native|lifted` prints it): GCC 13.3.0 + `toolchain/msvcmodel/msvcmodel.patch`, a fnv-nx-build mirror
(`tools/gcc-ilp32-msvcmodel-noneelf`: the compiler and the guest runtime are this one tree, `runtime` = `external` in the pin). The root's `compiler` must be `{"external", "cc", "cxx"}` naming an `external` entry equal to the pinned one (name, commit,
tree_sha256, at) with the pinned driver paths. The distribution g++-13 is the base the compiler is built from, never the compiler of guest code. A tree
without the pin file has no valid guest root. The plugins are built against this compiler's own plugin headers (the plugin stage reads
`<cxx> -print-file-name=plugin`, inside the external).
What an external compiler names outside its tree is declared as inputs at their own paths, from the compiler's own answers: the assembler and linker it is
configured with (`-print-prog-name=as|ld`) and the header directories of its search list, symlink targets included (the distribution's cross binutils and
headers pinned by env.json). The CC stage mounts the compiler external, the plugins and unit.ii only; other externals are PP inputs.

The pin file also names the guest's **runtime** (`"runtime": {EXT_KEYS}`). It is the SAME external as the compiler: fnv-nx-build `tools/gcc-ilp32-msvcmodel-noneelf` is one
tree with the compiler (GCC 13.3.0 + msvcmodel.patch, target `aarch64-none-elf`, multilibs `.`, `pic`) and the runtime built by it (newlib, libm, libgcc,
GCC 13.3.0's own libstdc++, their headers). A guest recipe declares that external once; the entry must equal the pinned one exactly (red otherwise), and the
profile does not repeat it. `verify/samples/platform.build.json` is the sample recipe of the platform root (the one external, the eight plugins, the
semantics flags, `include` = the mirror's `c++/13.3.0`, its target directory and `include`); `test_guest.py` checks it with `recipe.py check` and builds a unit
that includes `<vector>`, `<string>`, `<cstdio>`.

### Gate rule: every guest C and C++ unit is compiled with the full plugin set
`admit.json` entry of a product root with at least one C++ unit declares `"cxx_origin"`: `"native"` (hand-written or decomp C++), or
`{"lifted_by": "<tracked .py of a tool root>", "pack": "<external name of the recipe>"}`. Alternative to `pack`: `"stage": "gen:<root>:<i>"`, a generated step of this recipe whose tool is `lifted_by` (its outputs are the root's units). Exactly one of `pack` and `stage`; the plugin set and flags are the same. Missing declaration = invalid recipe = red `build`.
- `native`: `plugins` must hold `fnvnx_order` and `fnvnx_ftoi`, with the sources `PLUGIN_SOURCES` and the arguments `PLUGIN_ARGS` of recipe.py (the MSVC
  semantics; `ltr`, `first`, `cpp17` exist for A/B measurements and are no product). Anything missing or different is a finding naming it.
- lifted (the lifter emits `ldar`/`stlr` itself): consistent only if the root has NO tracked C++ unit, every C++ unit is an output of a `generated`
  step whose tool is `lifted_by`, and `pack` is an external of the recipe (an input of every generator). The one plugin it must load is `fnvnx_ftoi` with `mode=lifted` (`plugin_args`): no rewrite, and every float -> integer conversion outside an `fnvnx_f2i` function is a build error.
- every guest root with a C or C++ unit (native or lifted, any language mix) holds ALL of `PLUGIN_SOURCES` with the arguments of `recipe.plugin_args(cxx_origin)` (the only source: `fnvnx_ftoi` in mode lifted for a lifted root), in that order: the
  C and the C++ CC stage of the root load every plugin their front end can load (`-fplugin=/plug/<n>/<n>.so`, `/plug/<n>` a declared input), so a
  plugin is in every unit key. Loadable = has no STRONG undefined reference to a symbol that only the other front end defines (`recipe.plugin_languages`: `nm -D` of cc1,
  cc1plus and the .so; a weak undefined reference resolves to null and is no load requirement). Every plugin declares the C++ front end's symbols weak
  and uses them only under the C++ front end (CC1PLUS-SYMS, `toolchain/tests/test_cc1.py`), so both front ends load all 8. Gate `datamodel`
  re-derives the set and reads it back from the CC stage documents; a unit lacking a loadable plugin .so is red, no name list, no exception list.
Assumption: the declaration `lifted_by` names the lifter is the owner's (admit.json is reviewed); the gate proves that no hand-written C++ sits in a lifted root. (ASM-101)
Check: `lifted_by` must be a tracked `.py` of an admitted root of class `tool` (recipe.py check, `cxx_origin.lifted_by ... is not a tracked .py of an admitted tool root` otherwise), and the gate proves that no hand-written C++ sits in a lifted root: every C++ unit is an output of a step of that tool. When the single lifter `liftall.py` is on the trunk, `lifted_by` must equal its path (one constant `LIFTER` of recipe.py); until then the check is the one stated here.

### The analysis plugin (`objstub_plugin`)
`ANALYSIS_PLUGIN` (verify/objstub_plugin.cc) is the plugin of the gate `objstub`: the recipe builds it, as the same hermetic stage `plugin:<name>` as every other plugin, for the target compiler of every root that has C/C++ units
(the plugin headers of that compiler, the host driver `PLUGIN_CXX`). No `build.json` declares it and no CC stage loads it; the gate loads it over the preprocessed text of the units. The result holds
`analysis_plugin: {name, sha256, path}` (the file `plugins/objstub_plugin.so` beside the objects). There is no other build of it: the gate has no compiler call of its own for it.

## Link: the guest ELF
There is one way to link: the recipe key `link` (no other path runs a linker; link/ has no subprocess call). Every key below is validated by
`check()` with all errors listed at once.
- `link.roots`: `[repo paths of other admitted product roots, or `root@NAME` pairs (Variants)]`. Each root's own recipe is built by the same machinery (content-keyed, cache hits in
  GATE_CACHE/store) and its objects are link inputs AFTER this root's own units, in the order of the list, each root's objects in unit order. An input
  root must be admitted, class product, with `link` null (so a cycle is impossible; `check()` names a cycle `A -> B -> A` anyway), listed once, not the
  root itself. A red input root makes this link red: stage `ld:<root>`, stderr names the input root and its failing stage.
- `link.script`, `link.libs` (guest roots): one fact for every guest link. `ports/fnv-nx/link/guest.json` = `{"script": "link/guest.ld"}` and nothing else: the libraries are not stored anywhere. The link derives them (keyed stages `drv:<root>` and `mld:<root>`: the pinned runtime's driver with `flags.semantics`, `-pie -###`, and `-print-multi-directory`): dirs, names, start files, end files of that link command, minus the start/end files the one-definition rule (they define a symbol a product root defines) or the entry rule (they define `_start` while the script's ENTRY is another symbol) excludes. A derived file outside the pinned runtime external or outside the selected multilib is a red `drv:<root>`.
  Every root whose admit.json `target` is guest and that links (the product ELF and every guest test image) gets exactly this script and these libraries; the link
  stage is `startfiles -L<external at>/<dir>.. [objects, whole] --start-group -l<name>.. --end-group endfiles`, so two guest links differ only by output and objects.
  `libs.external` is the guest runtime external pinned in `verify/guest_compiler.json` (`runtime`: the guest's libc, libm, libstdc++), which the root declares like
  any external; all paths are relative, inside it, there is no host path. A guest root that declares `link.script` or `link.libs` itself is red (no per-root choice, so
  a double of the script cannot exist), a missing or invalid profile is red. The profile's content is in every guest link stage key (script mounted at /ld, libs in argv).
  A host root declares `script` (a tracked file of the root or a generated output of it, required for kind pie) and `libs` as before: `{"external": ..}` of an external of the recipe or `{"system": [..]}`.
- `link.version_script`: a tracked file of the root, a generated output of the root (an output of its `generated` or `run` stages, the way `constants` and `contract_names` are), or a tracked file of an admitted tool root's tree (by repo path).
  `link.script` takes the same three forms. A generated script or version script is mounted at `/ld` by the ld stage from the root's build view, so its content is in the stage key: changing the generator's input changes the script and the key; an input change that leaves the script equal leaves the link key.
- Linked whole: every input OBJECT (own and of the input roots) and every archive that is an input is under `--whole-archive`; there is no `--gc-sections`:
  the set of symbols is the set of the inputs. (The toolchain's libraries of `link.libs` are the linker's to search, as the driver does: whole libc
  would be the wrong set; what they pull in is judged by the symbol tables after the link.)
- Options are fixed by the rule, not chosen per recipe: `--build-id=none -pie --no-dynamic-linker -z text -z now -z pack-relative-relocs
  -z max-page-size=0x1000 -z common-page-size=0x1000`, then the emulation from `link.flags` (`-m aarch64linux32`, `-EL`, `-X` only), `-T script`.
  `--build-id=none` (not the driver's sha1) is the recipe's own rule for determinism. The refusal set R stays.
- Post stage `ldcheck:<root>` (link/ldcheck.py, a pure python tool of the python runtime like `generated`; inputs: the linked ELF and the input objects): from
  ELF symbol tables only it checks the rules of the former ld.py: no undefined symbol in the output; every name the inputs reference or define is defined in the
  output; a WEAK undefined reference (STB_WEAK, SHN_UNDEF: in the output's symbol table, or dropped by the linker, which resolves it to 0) that the output does
  not define is red, id `weak-undefined:<name> <- <objects that reference it>`, with no exception list (a weak reference that the output defines is fine); no dynamic relocation but
  R_AARCH64_P32_RELATIVE or RELR (no REL, JMPREL, TEXTREL table); the PIE mark (DF_1_PIE); the FNXG magic at the first PT_LOAD + 8 and the entry at the
  first PT_LOAD. Then it writes the crt0 header words +16 (link address), +20 (page aligned span), +24 (bit 0: relocatable) and outputs the final ELF and the
  gate file `ld` (`ld.json`, `ld.ids`). A failed rule is a red build, stage `ldcheck:<root>`, stderr: one `ldcheck: <id>` line per finding.
- `objects.json` of the linking root: `elf` {path, sha256} is the final ELF; `ld_gate` {"ld.json": {path, sha256}, "ld.ids": ..}; `link_roots` [{root, sha256, objects [{unit, object, sha256}]}]:
  the input roots in link order, their objects copied beside the result (out/link_roots/<root>/), the sha256 being the digest of the CONTENT of that root's objects.json (the file itself holds machine paths and cache
  counters; the digest is over the objects' sha256, symbol tables, stage keys, ELF and constants hashes), so a later gate sees exactly what was linked.

- `link.kind`: `pie` (default, the guest: the rules above), `exe` (`-pie -z text -z now`, host executable, emulation `aarch64linux` or `elf_x86_64`, no
  script required) or `shared` (`-shared -z text -z now`, `link.version_script` required and passed as `--version-script`; the output is `NAME.so`). ldcheck
  of a non-pie kind checks from ELF symbol tables: every name the inputs define or reference is defined; no undefined symbol except those a
  `link.libs.system` library defines (its `.dynsym` is read); no TEXTREL; a shared object's exported names (those the version script names) are defined and
  global. `link.libs.system`: `[absolute paths of shared libraries of env.json packages]`, mounted as declared inputs at /syslib (their content is in the key). Any file of a package that env.json pins is admitted, the loader `ld-linux-x86-64.so.2` included (libc6): the link stage asks `envcheck.system_lib_errors` (dpkg's file lists under every usrmerge spelling of the path, against the pinned `packages`); a path no pinned package owns is a red `ld:<root>` stage naming it.
  **Static undefined set.** A red link record (`failed_units[].undefined`, `link_undefined`) carries every unique undefined symbol with all its referencing objects (`<root>:<unit>`), computed by `undefset.closure` from symbol tables, never from ld's text: strong references of the product objects, the start/end files and the archive members the pull-in fixpoint takes, minus names defined by a linked object, by a declared system library's .dynsym or the declared loader's set, or assigned by the link script. A weak reference counts only from a product object (a startfile's or runtime file's weak undefined is the compiler's runtime). ld's status decides the exit; if the set is empty and ld failed, the record carries the finding `static closure disagrees with ld`. The names the pinned ld defines itself (`_GLOBAL_OFFSET_TABLE_`, `_DYNAMIC`, `__bss_start`...) are facts read from a keyed reference link (stage `ldref:<root>`: the same ld, flags, script, inputs and libraries with `--unresolved-symbols=ignore-all`): the names its output defines that no start file or library member defines; with the script's own assignments they are the linker-defined set. A failed reference link leaves the set to the script and the record says so. Gate guestlink also reds a derived start/end file outside the pinned runtime external, or outside the multilib the semantic flags select.
  **One entry (ldcheck).** The entry symbol of a link is `ENTRY(sym)` of its script, else `_start`; exactly one object of the product's roots defines it (`entry-none:<sym>` when none does, a definition only in a startfile or library included; `entry-duplicate:<sym> <- objects` when several do). For the guest profile, when ENTRY is not `_start` the startfile that defines `_start` (the runtime's crt0.o, whose ABS32 relocations in .text fail `-z text`) is the runtime's own process start: the derivation excludes it (`recipe.entry_object`), so link/guest.json lists no crt0.
  **Host exe: start files from the driver.** For `kind exe` the link takes its start files (crt1/Scrt1, crti, crtbegin), end files (crtend, crtn) and the dynamic linker from the recipe's own pinned driver, not from the recipe: stage `drv:<root>` runs `<driver> -pie -###` on a one-line file, with the `.o` files of the driver's own library search directories as declared inputs; the link line it prints gives the `.o` files before the first `-l` (start), after the last (end) and `-dynamic-linker`. Each file must belong to a package env.json pins (`system_lib_errors`). The link stage mounts those files and uses them; a recipe that names `libs.startfiles/endfiles/dirs/names/external` for an exe is red, and `link.flags` carries emulation options only, so no `-e`, `-dynamic-linker` or `-nostartfiles`. The output has the exec bit. Weak undefined, host and guest alike (ruling A): a weak undefined reference is accepted iff no product-root object makes it: one that only a startfile or runtime file of the pinned toolchain carries (`__gmon_start__`, `__cxa_finalize`, `_ITM_*`: the compiler's runtime, value 0 by the gABI) is accepted; one a product object references and nothing defines is red (`weak-undefined:<name> <- <objects>`).
  `link.loader` (kind shared only): the admitted root (product or test, with a recipe, the same target) of the image that loads this one. ldcheck then accepts
  undefined names ⊆ (the dynamic exports of the `link.libs.system` libraries) ∪ (the global and weak names defined by the objects of the loader root and of its
  own `link.roots`, built objects-only through the stage cache and passed to `ldcheck:<root>` as a declared input), and nothing else. The loader is an edge
  of the root graph (cycles are red). Without `link.loader` only the libraries count.
  `link.roots` works for every kind; a root needed by several images has `link` null and is built once per (root, variant).
- Target of the link: every linked object (own and input roots') has the ELF class and e_machine of the link's emulation (`aarch64linux32` ILP32 = class 1,
  AArch64; `aarch64linux` = class 2, AArch64; `elf_x86_64` = class 2, x86-64), read from its ELF header. A mismatch is red, stage `ld:<root>`, naming the object.
  `exe` is host only: a root of target guest links a `pie` (emulation `aarch64linux32`, the crt0 header), a guest test root included; a guest root with
  kind `exe` is refused. A product image's `link.roots` name base roots, or the per-target variant of the image's own target (a variant that admit.json declares with its own `recipe`, the dict spelling `{guest, host}`: it is the same units compiled for the image's target, a product of the root). A flags variant (a sanitizer build or any variant declared in the recipe's `variants`) is test-only: an explicit `root@NAME` of one is linked only by a root of class `test`, and a product image naming one is red.
  A `link.roots` entry names a root and the image links that root's recipe for the IMAGE'S OWN TARGET (`recipe.resolve_link_root`): an explicit `root@NAME` must itself
  have the image's target, else red; a base name is the base recipe when the base's target is the image's, else the one variant of the root that admit.json declares with
  the image's target (a host image names the guest root `lib/sha256` and links `lib/sha256@host`); no such variant, or several, is red. An object of another target is
  red at the link in any case (class and e_machine of every input).
  The admit.json entry of a root may carry `variants`: {NAME: {"target": "guest"|"host"}} for variants the recipe declares; a variant inherits the base's
  target otherwise, the guest rules (compiler external, data model) are judged on the effective target, and a variant with its own target has it in
  every stage key (`FNVNX_TARGET`). Sources stay the base root's. Every entry point resolves a `root@NAME` spec with `split_spec` once.

  **One recipe per target.** A root whose units serve both targets (the console backend on the guest, the host's gl4 calling the same translator)
  names a recipe per target in admit.json: `"recipe": {"guest": PATH, "host": PATH}` with `"target"` the base's (a plain string is the root's single
  target, as before). The units are the same for every target; compiler, flags and plugins come from each target's recipe. It is one mechanism with
  `variants`: `recipe.norm_entry` parses the dict to the string spelling plus `variants: {OTHER: {"target": OTHER, "recipe": PATH}}`, and that
  spelling may be written in admit.json directly (it parses to the same structure). `entry_of` resolves the effective target and recipe path; the
  pair `root@OTHER` is built, keyed (the variant name, `FNVNX_TARGET`, its own compiler and flags) and gated separately in `GATE_CACHE`. A per-target
  recipe is a whole recipe with no variants of its own; its name may not also be a variant of the base recipe. Header home: an include into a
  product root is accepted for the including root's target only if that root has a recipe for it (its own, or a per-target one).

### ELF facts of a linked output (the link record)
A green link record carries the facts of the linked ELF, written by the link itself with the pinned binutils of `env.json`; every consumer reads the record and none runs
`readelf`, `nm` or `objdump` on a linked output. The record is `elffacts.json` ("fnvnx-elffacts/2", `objects.json` key `elf_facts`):
- `sections`: `{index, name, type, address, size, flags}` of every section of the final ELF (`flags` as readelf's letters), from the stage `ldfacts:<root>`: the
  pinned `readelf -W -h -S -r -s` (the one beside the linker the compiler names; it must be a file of a package env.json pins, else the stage is red) run on the final ELF
  (after ldcheck). The stage's stdout is cached with it, so a link that hits the cache runs nothing.
- `symbols`: the DEFINED symbols of `.symtab`: `{name, value, size, type, bind, section}` (`section` = the Ndx column: an index, ABS or COM).
- `header`: `{machine, type, entry}` (`-h`; `entry` the e_entry address, an integer).
- `relocations`: rows `[section, offset, type, symbol, addend, target]` of every relocation section (`-r`; `symbol` is "" for a relocation with no symbol, the section
  name for a section symbol; `target` = symbol value + addend, an IFUNC symbol's value taken from the symbol table). A link keeps the STATIC relocations of its inputs
  only with `--emit-relocs` (a link flag of the root; ld manual, Options): without it these are the dynamic relocations of the image.
- `call_edges`: `{site, caller, caller_offset, callee, target, type}` sorted by site, one per call relocation (the machine's CALL_TYPES: AArch64 CALL26, JUMP26):
  `caller` the FUNC symbols that hold the site (`[]` if none), `callee` the symbol of the relocation or, for a section symbol, every symbol whose range holds the
  target. `address_taken`: `{symbol, target}` for every other relocation (not a conditional branch) in an allocated section other than `.eh_frame` and
  `.gcc_except_table` (unwind tables name every function: metadata). Static call edges exist only for a link with `--emit-relocs`.
- `members`: `{member, requested_by, symbol}` for every archive member the link pulled in (the map's `Archive member included to satisfy reference by file (symbol)`
  section), in ld's order. The entry point symbol is the symbol of `symbols` at `header.entry` (the ENTRY of the link script).
- `references`: for every product object of the link (key `<group>:<unit>`, the objects of the root and of its `link.roots`) the list `{symbol, definer}` of every
  undefined reference it makes that the link resolved: `definer` is the input that defines it, named as ld names it (a product object `/objs/<group>/<unit>.o`, a
  start file, an archive member `ARCHIVE(MEMBER)` with the archive's path, a system library), or `<linker>` for a name that the linker defines itself. A weak
  undefined reference nothing defines is not resolved and has no entry.
  Source: ld's own cross reference table (`ld --cref --no-demangle`, in the map the link stage writes; ld manual, Options, `--cref`: for each symbol, "If the symbol
  is defined, the first file listed is the location of the definition"; the files that follow reference it). ld records a definition only for a symbol an input
  defines, so the first file is the definer iff ITS OWN symbol table defines the name (the object's or the archive member's, read by `undefset.object_symbols`);
  otherwise the name is the linker's own (or weak undefined: not defined in the output). No text of ld's diagnostics is read.
Totality: a row of readelf's tables that does not fit the layout of `readelf.c`, a count that disagrees with the table's header line, or a file in ld's table that
is no input of the link makes `ldfacts:<root>` red; nothing is skipped. The layouts are those of binutils 2.42 and are pinned by `tests/test_elffacts.py`.
Readers of the record: gate_tlsmodel (the relocations of the linked ELF), contract/pcrel_abs.py via gate_pcrel (the ABS symbols), verify/icount.py (L4 converts it).

## Run stages (`run`)

`"run": [{"name", "artifact", "args", "inputs", "debs", "outputs"}]` executes a declared artifact in the sandbox, after the generators.
- `artifact` is `ROOT:OUTPUT`: the linked `link.output` of another admitted **host** product root (it is built first, by this same recipe machinery).
- The stage sees only: the artifact at `/art/OUTPUT`; the declared `inputs` (a repo-relative tracked file or directory of an admitted root, git ls-files only, like a generator input; or `{external, path}` /
  `{stage, path}`, read by resolve_input) at `/in/1`, `/in/2`, ... in list order; the installed files of each declared deb at `/deb/<name>/<absolute path>`; its `/out`.
  The root is read-only, `/tmp` and `/work` are not writable. `debs` are names of `env.json` `debs.packages`; an undeclared name is red (check).
- An artifact with a PT_INTERP is started through the file at that path inside a declared deb (`/deb/<name>/<PT_INTERP>`, the deb's own symlinks
  followed across the union of the declared debs by `deb_resolve`: the usr-merge links are a fact of the declared system, shipped by `base-files`, so a run stage lists `base-files` in `debs`; without it a PT_INTERP such as `/lib64/ld-linux-x86-64.so.2` is red), with `--library-path` over the declared debs'
  `lib/x86_64-linux-gnu` and `usr/lib/x86_64-linux-gnu`. A PT_INTERP that the declared debs do not provide is red; no loader path is hard-coded.
- `"host_cpu": "cpuref"` (optional): the pin `verify/cpuref.json` {vendor_id, family, model, stepping, microcode, model_name} (the /proc/cpuinfo fields,
  ints as numbers; a null value means the field is ABSENT from /proc/cpuinfo (a VM has no microcode line), never a wildcard) is in the stage key. Before a stage that is not a cache hit runs, every processor of /proc/cpuinfo must equal the pin, else the stage
  is red (no fallback); a cache hit is served on any host.
- The key covers the artifact content, the input contents, the deb sha256s (`env.json`, in the stage environment) and the argv.
- `outputs` are root-relative declared paths, stored in the store and copied into the root like generated outputs (they may be units, headers,
  constants ...). A nonzero exit, an undeclared output and a missing declared output are red, naming `run:<root>:<name>`.
- A `run` stage is not a compile: toolflags does not judge it (its argv[0] is the artifact, never a compiler).

### Stage refs

`resolve_input` takes a third ref form `{"stage": "ROOT:gen:I" | "ROOT:run:NAME", "path": P}`: the declared output P of generator I (index in
`generated`) or of run stage NAME of an admitted root. The stage runs (cached by its key), the output is written to `CACHE/stageout/<sha256>/P`
and the ident is `stage:ROOT:NAME:P:sha256`. An undeclared output, an unknown root or stage and a red stage are `ValueError`.

## Profiles
`"profile": "guest" | "host"` names the profile of the root's target (it is the root's admit.json `target`; any other is red). recipe.py generates the
profile's fields from the pins (`recipe.profile_fields`, `recipe.expand_profile`) and expands them into the recipe before anything reads it, so every stage key is a
function of the expanded values alone (a root that names its profile has the keys of the root that spelled every field):
- guest: `compiler` (the external and drivers of verify/guest_compiler.json), `plugins` (the full PLUGIN_SOURCES set in load order, fixed arguments from
  recipe.py via `plugin_decls`), `external` = the compiler's external and the runtime's (pin `runtime`). The semantic flags are already generated (targets.json, DATAMODEL.json, guest_compiler.json).
- host: `compiler` = the distribution compiler of env.json, no plugin, no external.
A root names `profile` plus its own `flags`, `include`, `units`, `defines`, `generated`, `link`, and in `external` only what the profile does not carry (those follow
the profile's in the expanded list). Writing `compiler`, `plugins` or a profile external in the recipe is red ("restates a profile field").

## Variants
`variants: {NAME: {"compiler": .., "flags": {..}, "link": {..}, "flags_add": {..}, "defines_add": [..]}}`: one root built for several targets or with sanitizers. A variant is a set of **deltas**:
`flags_add: {"c": [..], "cxx": [..], "asm": [..]}` appends to the root's flags of that language, `defines_add: ["NAME[=VALUE]"]` appends `-DNAME[=VALUE]` to c, cxx and asm; both are
appended in the order written, after the base flags. A whole replacement (`compiler`, `flags`, `link`) is kept only where replacement is the point: a sanitizer variant's own `link`
(whole, e.g. its `link.libs.system` [libubsan.so.1 of the pinned libubsan1]) and a variant that changes the compiler. The rest is
the root's; its effective recipe is checked by every rule of `check()`. A (root, variant) pair is spelled `root@NAME` (`recipe.split_spec`; the implicit
variant is `root`). It is its own set of content-keyed stages: the variant name is in every stage key (env `FNVNX_VARIANT`, absent for the implicit variant, so
a root without variants keeps its keys), its objects are stored at `GATE_CACHE/objects/<tree>/<root>@<NAME>/objects.json` and its `objects.json` has
`root: "root@NAME"`. `link.roots` entries may be `root@NAME` (the variant must exist). The gates that quantify over product roots (`gate_build.product_roots`)
quantify over the pairs: build, determinism, objstub, linkstrict, argorder, contract's per-root gates.

## Outputs and uses
`outputs: {NAME: a path in generated[].outputs of this root}` declares what other roots may take. `uses: [{"root": R, "output": NAME}]` (R any admitted
root with a recipe that declares NAME) makes that output an input of this root's stages: the `generated` stages and the preprocessing stages mount it
read-only at `/uses/<R>/<NAME>` (the compile stage mounts nothing of it). The producer's generate stages run (content-keyed, cache hits) and the output file
is ingested; the consumer's stage key holds the file's content hash, not a path and not the producer's keys: a producer change that leaves the output
byte-identical changes no consumer key. Red (check errors): an output the root does not declare; a root that is not admitted; a cycle in `uses` plus
`link.roots` between distinct roots; a `generated` input that is a file of another root (class other than tool) by path, which must go through `uses`.
Objects of another root enter only through `link.roots`.

### Generated `sources`
`sources: PATH` (a path in generated[].outputs of the root) names a file a generate stage writes, one `<external>:<path>` line per unit; `.` names the root
itself (a tracked file of it). Each listed file of a declared external is copied into the unit view as the unit `<external>/<path>`, so its content is in
the unit's keys; the units are built with the root's own. Red (stage `sources:<root>`): a line that is not `<external>:<path>`, an external the recipe does
not declare, a path that is no file inside the external (`..` included), a `.` path the root does not track. A changed list adds only the new units:
the other units keep their objects and compile-stage keys (their preprocessing keys follow the view's file names, the compile keys do not).

### Objects selected by declaration (`objects_of` with `target`, `origin`)
`objects_of: {"except": [R...], "target": "guest"|"host", "origin": "native"|"lifted"}`: `target` and `origin` (both optional) select the admitted product roots by their
admit.json declaration: `target` equals the entry's `target`, `origin` is `origin_class(cxx_origin)` (native = `"native"` or undeclared, lifted = `{lifted_by, pack}`). A selecting stage
also leaves out every root that depends on the producing root through `link.roots`, the declared loader or `uses`, transitively (`depends_on`), so the consumer of the
stage's output is never in its input set: no `except` list and no cycle. `contract/facts` selects `{"target": "guest", "origin": "native"}` (the native roots' objects, whose
`.fnvnx.sympair` sections `bind` and `nativerows` read) and publishes `outputs: {"native": "native.tsv"}`: the address -> defined symbol rows of the native table of `lift/rt`.

`depends_on` follows every input edge, `objects_of` included, so a root that reads the objects of a consumer of the producer is itself a dependent. `objects_of` may carry
`dependents_of: ROOT`: the same selection, but only the roots that depend on ROOT, the complement of ROOT's own selection. `contract/factscheck` selects
`{target: guest, origin: native, dependents_of: contract/facts}` and proves (`nobinding.py`: bind over those objects with facts' `n1` output, red on any bound symbol)
that the roots facts leaves out contribute no row to `native.tsv`; it cannot be a stage of facts itself (those roots' builds need `native`: a cycle).

### Objects of every admitted product root (`objects_of`) and `forbid_sections`
A generate stage may carry `objects_of: {"except": [R...]}` (every R an admitted product root). Its inputs then hold, at `/objs/<root with / as __>/<unit>.o`,
the objects of every admitted product root with a recipe except the root itself and the R listed: the set is read from admit.json at the commit, so a
newly admitted root enters the next build and no list names it. Each such root is built as a product root and must be green (else red, stage
`objects_of:<root>`); the stage key holds the objects' content. A root that takes such a fact through `uses` is in the producer's reading set, so it must be
in `except` (else the cycle check is red), and it must carry `forbid_sections: [".name", ...]`: an object of the root with a named section is red (stage
`forbid:<root>`). The exception then loses nothing: the excepted root cannot hold what the fact collects.
`lift/descs` is this rule's first use: its tool collects the `.fnvnx.disp` sections (one `conv:ret:args` descriptor per line, sorted, unique per object,
LF-terminated, ASCII; an absent section is no indirect call) into the sorted union `descs.tsv`, output `descs`. A malformed section is red at the
generate stage, naming the object. `lift/bridges` takes it as `{"root": "ports/fnv-nx/lift/descs", "output": "descs"}` (at `/uses/ports/fnv-nx/lift/descs/descs`),
is listed in the `except` of lift/descs' recipe in the commit that admits it, and has `forbid_sections: [".fnvnx.disp"]`.

### errno (objstub, derived, no recipe key)
How the root's libc reaches `errno` is derived per root: objstub compiles `#include <errno.h>` / `int f(void){return errno;}` with the root's own C compiler and unit flags and reads the object (`errno_probe`): a call relocation to a symbol X is the accessor, a TLS relocation to Y is the thread-local form. The final ELF is then judged (rule I, `image_check`): a function body of the image whose whole effect is `errno = constant; return constant` is the finding `fixed-failure`, whether it is ours or comes from an archive member (a weak fallback a strong definition replaced is not in the image). A body that cannot be judged (a data island, a word LLVM rejects, an odd size) is the finding `unjudged`; the TLS form is `unjudged` for the root. Nothing is skipped.

### `external_errors` (objstub)
`external_errors: {EXTERNAL: {"type": TYPEDEF, "success": INT}}` declares once per library the error convention of an external whose units enter through `sources` (FreeType: `{"freetype": {"type": "FT_Error", "success": 0}}`). A unit `<external>/<path>` is a copy of that external's pinned file, so its V bodies (no effect, constant result) are the library's own code and are real, with one exception: a body whose return type (the compiler's own typedef name of the definition, from the objstub plugin) is the declared error type and that returns a constant other than `success` is the finding `external-error-constant`: an error answer where the library does work.

### `error_abi` and `empty_handlers` (objstub)
`error_abi` declares the root's one failure function and how its definition signals failure: `{"fail": SYMBOL, "kind": "noreturn"}` (the definition does
not return) or `{"fail": SYMBOL, "kind": "flag", "flag": FIELD}` (the definition stores a nonzero constant to the member FIELD of the structure its first
parameter points to, on every path, directly or through direct calls in the same object). objstub (gate `objstub`, rule T-B) checks the declaration from
the definition in the object (the DWARF gives the member offset, so a root with `flag` is compiled with -g) and makes a function whose every path ends in
a call of SYMBOL, with no output effect, the finding `trap-body`. `empty_handlers: {"cover": PATH, "syntax": PATH, "tables": {SLOT: {"symbol": TABLE,
"field": FIELD}}}` names the generated records (tracked repository files, op_cover.tsv and op_syntax.tsv) and, per slot, the instruction table (an array
variable) and the entry field that holds the handler; the handler of entry k is read from the data relocation at that table, by the layout from DWARF, and an
empty or constant handler is certified only by the record rows of the entries that select it (rule H). `cover` and `syntax` must be files of an admitted root other than this one (the generated outputs of the root that produces them, e.g. render/shaders/total). A `handler` column in op_cover.tsv (a symbol, a `+` list of clones, `MISSING:S`) is a claim checked against the object: it must equal the table's function, and a missing or undefined handler is the finding `handler-missing`. A root with `empty_handlers` must declare `error_abi`
(a recipe error otherwise). Both are checked as keys here and judged in objstub.py; the probe (error_abi and tables) is part of the objstub cache key.

## Python of the generated stages

A `generated` step runs in the python runtime of the sandbox (the interpreter and its loader closure, content-addressed: `runtime_id` is in the
key). Every python module pinned in `verify/env.json` (`python.modules`) is mounted in that runtime, at the content of its installed files, and
the key of every generated stage also names each of them as `name==version:installed_files_sha256` (envcheck.wheel_digest: the files read from the
disk, not the RECORD's claim). Changing one module's digest changes the key of every generated stage, and nothing else's.

## Keep going

A root build does not stop at the first red stage. Every unit whose declared inputs are all produced is built (and its objects land in the
store as usual); a red stage is recorded with its stage name, exit and the last 20 diagnostic lines. Blocked, never run and never hidden:
(i) a unit that is the output of a red generator, (ii) every unit of a root whose `uses` producer or whose external is red, (iii) the C++ units
when a plugin stage is red, (iv) the ELF whenever anything is red or blocked (also when an input root of `link.roots` is red). A unit that
includes a file a red generator did not produce fails in its own preprocessing stage and is listed with those diagnostics. The root is `red`
if any unit or stage is red or blocked. `objects.json` of a red root: `objects` (the built units), `failed_units` ({unit, stage, exit, stderr}, in unit order),
`blocked` ({unit, blocked_by: stage}, sorted by unit), `red_stages` (the stages that were red outside a unit), `failure` (the first red stage).
`units` is always the whole universe.

## Refusal set R (GNU ld option syntax, not a text guess)
Options that let a link accept missing or substituted symbols: `--unresolved-symbols`, `--allow-shlib-undefined`, `--noinhibit-exec`,
`--defsym`, `--wrap`, `--allow-multiple-definition`, `--warn-unresolved-symbols`, `-z undefs`, `-z muldefs`. A token is refused when, with one or two
leading dashes and the part after `=` cut, its name is a prefix of such a long name (getopt_long abbreviation; one-dash names need 2 chars),
as `-z X` or `-zX`, inside `-Wl,a,b` or after `-Xlinker`, or when it is an `@file`. Checked in every flag list, `link.flags` included.

## External inputs
Real sources and toolchains that are not Debian packages of env.json live in the private `fnv-nx-build` repository (a mirror branch). An external
is fetched ONLY by exact `commit` (`git fetch --depth 1 $ARTIFACTS_URL <commit>`, default https://github.com/toadharvard/fnv-nx-build) into
`<GATE_CACHE>/external/<commit>`, checked out, ingested into the CAS, and its tree hash must equal `tree_sha256` (get it with
`cas.py ingest`). A mismatch or an unreachable commit makes the build red with stage `external:<name>`. The tree is a declared input at `at`
of every stage, so a changed mirror changes every stage key. `ref` is documentation only; the commit decides. Validation: `repo` is `fnv-nx-build`,
names unique, `at` absolute and normalised, not under `/src /inc /in /out /tool /objs /ld /work /proc /dev /tmp`, not nested in another `at`.
An external compiler is a tree at `at` (cc and cxx are paths inside it, e.g. `usr/bin/<driver>` of a copy of a Debian prefix: the driver finds
cc1, as, ld relative to itself). It is declared `HOST@AT` to `runtime.py tool`: the loader closure of its x86-64 ELF files is resolved as the
loader resolves it INSIDE the sandbox (paths under `at` are files of the checked-out tree, other paths are host libraries that become declared
inputs); a library that cannot be resolved is a red build naming the object and the name. `include` may name a directory inside an external:
it is passed as `-I<that path>` (the external is a declared input of every stage, so keys stay content-addressed).

## Result (`objects.json`, "fnvnx-objects/1")
`objects` [{unit, object, sha256, os_layer}] (product units), `tests`, `elf` {path, sha256}|null, `constants`, `contract_names` (paths of the copies or null),
`pp_keys` {unit: stage key of its preprocessor stage; its result `unit.ii` is in the store GATE_CACHE/store, read by objstub to derive the
terminator set}, `units`, `defined` [{symbol, bind GLOBAL|WEAK|UNIQUE, type, object}] and
`undefined` [symbol] from the ELF `.symtab` of every object, `stage_keys`, `stages` {run, hit}, `status`; red adds `failure` {stage, exit, stderr
(last 20 lines), `done` (units that built)} (the first failed unit in unit order) and `failed_units` [{unit, stage, exit, stderr (last 20 lines)}] (EVERY unit whose PP or CC stage failed, in unit order; every unit is judged, so one failure never hides another; the link stage and the constants copy do not run after a failed unit; generators run before the units, their outputs being unit inputs). Later gates read it at `GATE_CACHE/objects/<tree>/<root with / as __>/objects.json` (every gate has its own `GATE_OUT`).

## What the lead does
1. Put the recipe in the root, name it in `admit.json`. 2. `python3 -I verify/recipe.py check --root R` (validation). 3.
`python3 -I verify/recipe.py build --root R --store S --out O` builds; run it twice with fresh stores to see the determinism gate's view.

## Stack-usage report (measurement)
`recipe.py build --root R --store S --out O --stack-usage 1` adds `-fstack-usage` to the CC stage of every unit (AUX_FLAGS in recipe.py; it is part of the stage key, so
the stages are their own cache entries and the code generated is the same). Each `objects.json` row then carries `su`: the path of `obj/<unit>.su`, the compiler's own
report, one line per function `file:line:col:name TAB bytes TAB static|dynamic|dynamic,bounded`. Any other option is refused. It is a measurement of a build
(`stackuse.py`), never an input of a gate that certifies a product.

## Externals in a shared store

Several builds may run at once on one `GATE_CACHE` and extract the same external (the bare fetch, the checkout, `exttree`). One rule, `recipe.publish_dir(tmp, final)`, makes every such directory visible: it is extracted into a temporary sibling in the same store (`<final>.tmp<pid>-<random>`), its `tree_sha256` is verified there, and `rename(2)` publishes it atomically. A writer that loses the race (final exists: ENOTEMPTY or EEXIST) deletes its own copy and uses the published one; it is already verified, because only a verified tree is ever renamed in. Any other error deletes the temporary directory and is raised. No locks, no waits. An interrupted extract leaves only a temporary directory; the target is never partial and no reader looks at a temporary name. Test: `tests/test_extcache_race.py` (8 concurrent processes, one tree, an interrupted extract).

The same rule covers every other write into `GATE_CACHE`, the directory form and the file form:
- A **directory** (an external, a green build's out dir `objects/<tree>/<root>`) is built in a private sibling and published by `recipe.publish_dir`. `gate_build.ensure` builds in `<final>.tmp-<pid>-<random>`, gives the machine paths of `objects.json` the final name, publishes, and later calls read the published dir without a build (the key is the tree of the commit). A red build is not published: its out dir is private to the process and removed at exit.
- A **file** (stage outputs of the contract gate's work dir, `inputs.rsp`, the reach facts, `stageout`) is written to a private sibling and renamed by `cas.publish_file(final, data | src)`. A contract stage runs in a private dir beside the work dir (the published files as hard links) and publishes each file it made.
- The store's own writers (`cas._put`, `_ingest_file`, `sandbox._store_result`, the stage-docs, `write_hint`, `cached_rt`, the gate item caches) were already a temporary name plus `link`/`rename`.
- No path is shared scratch: a gate that needs a work dir makes a private one (`tempfile.mkdtemp`) and removes it.
Test: `tests/test_gate_publish.py` (two processes build one root on one cache at once: both green, one published dir, no temporary name left, the same store content as one sequential build).

**Gate items are results of the same store.** `gate_build.memo(cache, gate, key_doc, make, keep)` keeps one item of a gate as `GATE_CACHE/memo/<gate>/<key>.json`, published by `cas.publish_file`; the key is the sha256 of `key_doc`, which holds everything the item reads and nothing else: the sha256 of each input file, `gate_build.tool_digest` of the tool's sources, the declared environment (env.json). A hit reads the file and runs nothing; a miss runs `make()`; a value `keep` refuses (a result that may be a transient failure: a red determinism check) is not stored. The items: `abi` (one root's C++ objects, a set: the ODR and layout joins read all of them), `fpexact` (one object: its sha256, its f2i report's sha256, unit, tool and library versions), `determinism` (one root: its stage keys, objects and ELF, the digest of the code that executes and stores stages), `exepin` (the whole-program flow over the universe: the sha256 of every source). A gate without a cache (`GATE_CACHE` unset) makes every item. Test: `tests/test_gate_memo.py`.
