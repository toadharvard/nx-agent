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
 "os_layer": ["os.S"], "constants": "constants.tsv",
 "link": {"script": "link.ld", "output": "x.elf", "flags": ["-e", "entry"]},
 "external": [{"name": "ft", "repo": "fnv-nx-build", "ref": "mirror/freetype", "commit": "<40 hex>", "tree_sha256": "<64 hex>", "at": "/ext/ft"}]}
```

## Keys (any other key is an error; all problems are listed at once)
- `compiler`: driver names under `/usr/bin` that env.json provides, or `{"external": NAME, "cc": PATH, "cxx": PATH}` (paths inside the external).
- `flags`: per language. Refused: `-I*` (use `include`), `-o -c -E -S -x -M*`, and the refusal set R below.
- `include`: repo-relative directory inside an admitted root (tracked files, or generated outputs of this root), or an absolute path inside an
  external's `at`. Included files are inputs by content; a file the unit reads that is not in them does not exist for it.
- `units`: must be `"all"`. RULE: every tracked `*.c *.cc *.cpp *.cxx *.s *.S` under the root, and every generated output with such a name, is
  compiled. A file that must not be compiled does not belong in the root; there is no `exclude`.
- `generated`: python generators run as sandbox stages before compilation, in list order. `tool` is a tracked `.py` of an admitted tool or product
  root (the whole root is mounted at `/tool`, the script at `/tool/<path in its root>`), `inputs` are tracked files or directories (repo-relative;
  mounted at `/in/<repo path>`; they may not nest), the generator writes ONLY `/out/<output>` (the set of produced files must equal `outputs`).
  Outputs are root-relative, never tracked files, and appear in the root's view for `include` and as units when named like a source.
  The script must itself pass `puregate`; wall-clock reads and other open channels of the sandbox make `determinism` red.
- `os_layer`: units that implement the OS interface (may contain SVC). They are flagged `os_layer: true` in objects.json for `objstub --os-layer`.
- `constants`: the constant-answer TSV: a tracked file or a generated output; copied to `constants.tsv` for `objstub --constants`.
- `link`: `ld` of the compiler, `--build-id=none -T script flags -o output objects...` (objects in unit order). null = no ELF.
- `external`: content-pinned inputs (see below).

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
Limit of the external compiler form: the loader closure of its x86-64 ELF files is resolved on the checked-out copy by `runtime.closure()`
(libraries inside the tree are its own; others come from the host like `runtime.py tool`); an absolute RPATH into the tree's own prefix is not
mapped (`runtime.py tool` cannot say "host copy here, sandbox path there"), the build is red naming the missing library.

## Result (`objects.json`, "fnvnx-objects/1")
`objects` [{unit, object, sha256, os_layer}], `elf` {path, sha256}|null, `constants`, `defined` [{symbol, bind GLOBAL|WEAK|UNIQUE, type, object}] and
`undefined` [symbol] from the ELF `.symtab` of every object, `stage_keys`, `stages` {run, hit}, `status`; red adds `failure` {stage, exit, stderr
(last 20 lines)} and `failed_units`. Later gates read it at `GATE_CACHE/objects/<tree>/<root with / as __>/objects.json` (every gate has its own `GATE_OUT`).

## What the lead does
1. Put the recipe in the root, name it in `admit.json`. 2. `python3 -I verify/recipe.py check --root R` (validation). 3.
`python3 -I verify/recipe.py build --root R --store S --out O` builds; run it twice with fresh stores to see the determinism gate's view.
